#!/usr/bin/env python3
"""
ContextBuilder — 智能上下文组装器
===================================

整合自 Brain-v1.1.8 片段池 + 会话追踪系统

功能：
  1. 从多源（L1/L2/L4/L6/L7/L8）收集记忆片段
  2. 按任务类型决定注入优先级和内容
  3. 组装成 AI 可直接理解的上下文字符串
  4. 支持 10K token 硬上限自动压缩

注入分段策略（Brain 10K 硬上限）：
  1. 用户信息（USER.md）— 始终注入
  2. 当前任务（session state）— 始终注入
  3. 相关记忆（向量搜索 top 5）— 按相关性
  4. 最近决策（decisions top 3）— 始终注入
  5. 工作缓冲区 — 按剩余空间

使用方式：
  from context_builder import ContextBuilder
  builder = ContextBuilder()
  ctx = await builder.build(task="用户问如何优化量化策略")
  print(ctx["injection"])  # 注入字符串
  print(ctx["stats"])      # token 统计
"""
import re
import datetime
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
USER_FILE = WORKSPACE / 'USER.md'
SESSION_STATE = WORKSPACE / 'SESSION-STATE.md'
MEMORY_MD = WORKSPACE / 'MEMORY.md'

MAX_INJECTION_TOKENS = 10000  # 10K 硬上限

# ─────────────────────────────────────────────────────────────
# 分段注入器
# ─────────────────────────────────────────────────────────────

class ContextBuilder:
    """
    智能上下文组装器

    组装顺序（按优先级）：
      1. 用户信息（L8 USER.md）
      2. 当前任务状态（L1 SESSION-STATE）
      3. 相关记忆片段（L4 向量搜索）
      4. 最近决策（L02 历史）
      5. 工作缓冲区（L01）
    """

    def __init__(self):
        self.sections = []

    def add_section(self, title: str, content: str, priority: float = 0.5):
        """添加一个分段"""
        self.sections.append({
            "title":    title,
            "content": content,
            "priority": priority,
            "tokens":   self._estimate_tokens(content),
        })
        # 按优先级降序
        self.sections.sort(key=lambda x: -x["priority"])

    def add_user_info(self):
        """添加用户信息（始终第一位）"""
        if USER_FILE.exists():
            content = USER_FILE.read_text(encoding='utf-8')
            # 提取核心用户信息（去掉详细描述）
            lines = content.split('\n')
            clean_lines = [l for l in lines if l.strip() and not l.startswith('##')]
            clean = '\n'.join(clean_lines[:10])
            self.add_section("👤 用户信息", clean, priority=1.0)

    def add_session_state(self):
        """添加当前会话状态"""
        if SESSION_STATE.exists():
            content = SESSION_STATE.read_text(encoding='utf-8')
            # 提取关键字段
            state = self._parse_key_value(content)
            if state:
                state_text = f"当前任务: {state.get('current_task', '无')}\n"
                state_text += f"关键上下文: {state.get('key_context', '')}\n"
                decisions = state.get('recent_decisions', '')
                if decisions and decisions not in ['- [None]', 'None']:
                    state_text += f"近期决策:\n{decisions}\n"
                if state_text.strip():
                    self.add_section("📋 当前会话状态", state_text, priority=0.95)

    def add_memory_search(self, query: str, n_results: int = 5):
        """添加相关记忆（向量搜索）"""
        try:
            import sys
            sys.path.insert(0, str(WORKSPACE / 'skills' / 'vector-memory-self-evolution'))
            from memory_api import search
            results = search(query, n_results=n_results)
            if results:
                texts = []
                for r in results:
                    text = r.get('text', '')[:200]
                    frag_type = r.get('type', '')
                    texts.append(f"[{frag_type}] {text}")
                memory_text = '\n'.join(texts)
                self.add_section("🧠 相关记忆", memory_text, priority=0.7)
        except Exception as e:
            pass

    def add_recent_decisions(self, limit: int = 3):
        """添加最近决策"""
        try:
            sys_path = str(WORKSPACE / 'skills' / 'vector-memory-self-evolution')
            import sys
            sys.path.insert(0, sys_path)
            from session_store import get_store
            import asyncio

            async def _fetch():
                store = get_store()
                return await store.search_decisions(limit=limit)

            decisions = asyncio.get_event_loop().run_until_complete(_fetch())
            if decisions:
                texts = []
                for d in decisions:
                    topic = d.get('topic', '')
                    content = d.get('content', '')[:100]
                    ts = d.get('created_at', '')[:10]
                    texts.append(f"[{ts}] {topic}: {content}")
                self.add_section("�的决定", '\n'.join(texts), priority=0.6)
        except:
            pass

    def add_core_memory(self, query: str = ""):
        """添加核心记忆（MEMORY.md）"""
        if MEMORY_MD.exists():
            content = MEMORY_MD.read_text(encoding='utf-8')
            if query:
                # 关键词过滤
                lines = [l for l in content.split('\n')
                         if any(kw in l.lower() for kw in query.lower().split()[:3])]
                if lines:
                    content = '\n'.join(lines[:10])
            if content.strip():
                self.add_section("💎 核心记忆", content[:500], priority=0.5)

    def add_buffer(self):
        """添加工作缓冲区"""
        buffer_file = MEMORY_DIR / '工作缓冲区.md'
        if buffer_file.exists():
            content = buffer_file.read_text(encoding='utf-8')
            if content.strip():
                self.add_section("📝 工作缓冲区", content[:300], priority=0.4)

    def build(self) -> dict:
        """
        组装上下文

        返回：
          {
            "injection": str,    # 注入字符串
            "sections": list,     # 各分段信息
            "total_tokens": int,
            "truncated": bool,   # 是否截断
            "overflow": str,     # 被截断的内容
          }
        """
        total_tokens = 0
        kept_sections = []
        overflow_parts = []

        for sec in self.sections:
            sec_tokens = sec["tokens"]
            if total_tokens + sec_tokens <= MAX_INJECTION_TOKENS:
                kept_sections.append(sec)
                total_tokens += sec_tokens
            else:
                overflow_parts.append(sec)
                continue

        # 构建注入字符串
        lines = [
            "# 上下文注入\n",
            f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} CST\n",
            f"**上下文上限**: {MAX_INJECTION_TOKENS} tokens（当前 {total_tokens}）\n",
        ]

        for sec in kept_sections:
            lines.append(f"\n## {sec['title']}\n")
            lines.append(sec['content'])
            lines.append("\n")

        injection = '\n'.join(lines)

        # 如果整体超限，从末尾开始截断
        final_tokens = self._estimate_tokens(injection)
        truncated = False
        overflow_text = ""

        if final_tokens > MAX_INJECTION_TOKENS:
            truncated = True
            # 二分查找截断点
            injection = self._truncate_to_tokens(injection, MAX_INJECTION_TOKENS - 100)
            overflow_text = "(上下文已达上限，部分内容已截断，完整版见对应层级)"

        return {
            "injection":       injection + overflow_text,
            "sections":        kept_sections,
            "total_tokens":    self._estimate_tokens(injection),
            "truncated":       truncated,
            "dropped_count":   len(overflow_parts),
        }

    # ── 内部工具 ──────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
        english = len(text) - chinese
        return chinese * 2 + int(english * 1.3)

    @staticmethod
    def _parse_key_value(content: str) -> dict:
        """从 Markdown 文本解析 key: value 对"""
        result = {}
        for line in content.split('\n'):
            line = line.strip()
            if ':' in line and not line.startswith('#'):
                parts = line.split(':', 1)
                key = parts[0].strip().lower().replace(' ', '_')
                value = parts[1].strip()
                result[key] = value
        return result

    @staticmethod
    def _truncate_to_tokens(text: str, max_tokens: int) -> str:
        """二分查找截断文本"""
        low, high = 0, len(text)
        while low + 100 < high:
            mid = (low + high) // 2
            if ContextBuilder._estimate_tokens(text[:mid]) <= max_tokens:
                low = mid
            else:
                high = mid
        return text[:low]


# ─────────────────────────────────────────────────────────────
# 快速上下文（轻量版，用于简单查询）
# ─────────────────────────────────────────────────────────────

def quick_context(query: str = "") -> str:
    """
    快速组装上下文（不调用向量库，用于简单任务）
    返回注入字符串
    """
    builder = ContextBuilder()
    builder.add_user_info()
    builder.add_session_state()
    builder.add_core_memory(query=query)
    builder.add_buffer()
    result = builder.build()
    return result["injection"]


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='ContextBuilder CLI')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('build', help='组装上下文')
    p.add_argument('--query', '-q', default='', help='搜索查询（用于记忆匹配）')
    p.add_argument('--max-tokens', '-m', type=int, default=10000, help='最大 tokens')

    p = sub.add_parser('quick', help='快速上下文（轻量版）')
    p.add_argument('--query', '-q', default='', help='搜索查询')

    p = sub.add_parser('sections', help='查看分段优先级')

    args = parser.parse_args()

    if args.cmd == 'build':
        builder = ContextBuilder()
        builder.add_user_info()
        builder.add_session_state()
        if args.query:
            builder.add_memory_search(args.query)
        builder.add_recent_decisions()
        builder.add_core_memory(args.query)
        result = builder.build()
        print(result["injection"])
        print(f"\n📊 总计: {result['total_tokens']} tokens, "
              f"截断: {result['truncated']}, "
              f"丢弃分段: {result['dropped_count']}")

    elif args.cmd == 'quick':
        print(quick_context(args.query))

    elif args.cmd == 'sections':
        builder = ContextBuilder()
        builder.add_user_info()
        builder.add_session_state()
        builder.add_buffer()
        print("📋 分段优先级：")
        for sec in builder.sections:
            print(f"  [{sec['priority']:.2f}] {sec['title']}: {sec['tokens']} tokens")

    else:
        parser.print_help()
