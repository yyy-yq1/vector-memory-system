#!/usr/bin/env python3
"""
记忆片段标准接口 v1.0
==========================

整合自 Brain-v1.1.8 memory-fragment.js

设计原则：
  1. 每个 fragment 有明确类型（type）
  2. 每个 fragment 知道自己多大（get_size）
  3. 每个 fragment 可转注入格式（to_context）
  4. 每个 fragment 可转 QMD 搜索格式（to_searchable）

5 种片段类型：
  skill      — 技能/工具用法（优先级 0.7）
  task       — 当前任务（优先级 0.9）
  config     — 配置/系统变更（优先级 0.8）
  conclusion — 结论/决定（优先级 0.6）
  lesson     — 教训/反思（优先级 0.5）

FragmentPool — 按优先级注入，超上限自动截断

使用方式：
  from memory_fragment import skill, task, conclusion, lesson, FragmentPool
  pool = FragmentPool(max_tokens=30000)
  pool.add(skill("飞书图片", "用 message 工具发送", "message(action=send, ...)"))
  pool.add(task("完成记忆系统", "进行中", "还差 L5"))
  ctx = pool.to_context()  # 用于注入
"""
import re
import time
import json
from typing import Optional

MAX_FRAGMENT_TOKENS = 10000  # 单片段硬上限

# ─────────────────────────────────────────────────────────────
# Token 估算
# ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    估算 token 数（中英混排）
    中文：每个字符 = 2 tokens
    英文：每个字符 ≈ 1.3 tokens
    """
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(text) - chinese
    return chinese * 2 + int(english * 1.3)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """截断文本到指定 token 数（二分查找）"""
    current = estimate_tokens(text)
    if current <= max_tokens:
        return text

    low, high = 0, len(text)
    while low + 100 < high:
        mid = (low + high) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid
    return text[:low]


# ─────────────────────────────────────────────────────────────
# 片段基类
# ─────────────────────────────────────────────────────────────

class MemoryFragment:
    """
    记忆片段基类

    属性：
      type     — 片段类型：skill/task/config/conclusion/lesson
      content  — 记忆内容
      priority — 优先级 0-1（越高越不会被压缩）
      id       — 唯一标识
      created_at — 创建时间
    """

    TYPE = 'fragment'

    def __init__(self, type_: str, content: str, priority: float = 0.5):
        self.type = type_
        self.content = content
        self.priority = max(0.0, min(1.0, priority))
        self.id = f"{type_}-{int(time.time()*1000)}-{random_id(4)}"
        self.created_at = time.strftime('%Y-%m-%dT%H:%M:%S')

    def get_size(self) -> int:
        """片段 token 数"""
        return estimate_tokens(self.content)

    def is_oversized(self) -> bool:
        """是否超过硬上限"""
        return self.get_size() > MAX_FRAGMENT_TOKENS

    def to_context(self) -> dict:
        """
        转注入格式
        超大片段自动压缩
        """
        if self.is_oversized():
            truncated = truncate_to_tokens(self.content, MAX_FRAGMENT_TOKENS - 80)
            return {
                "type":        self.type,
                "id":         self.id,
                "priority":   self.priority,
                "content":    truncated,
                "compressed": True,
                "original_size": self.get_size(),
                "note":       f"[已压缩：原始{self.get_size()}tokens，完整版见 L4 向量库]"
            }
        return {
            "type":      self.type,
            "id":       self.id,
            "priority":  self.priority,
            "content":  self.content,
            "compressed": False
        }

    def to_searchable(self) -> str:
        """转 QMD 搜索格式（扁平文本）"""
        return f"{self.type} | {self.content}"

    def to_snapshot(self) -> str:
        """转人类可读快照格式"""
        type_emoji = {"skill": "🛠", "task": "📋", "config": "⚙️",
                       "conclusion": "✅", "lesson": "📌", "fragment": "📄"}
        emoji = type_emoji.get(self.type, "📄")
        return f"{emoji} [{self.type.upper()}] {self.content}"

    def to_json(self) -> dict:
        """序列化为 JSON"""
        return {
            "id":        self.id,
            "type":      self.type,
            "content":   self.content,
            "priority":  self.priority,
            "size":      self.get_size(),
            "created_at": self.created_at
        }

    @classmethod
    def from_json(cls, data: dict) -> "MemoryFragment":
        """从 JSON 重建"""
        frag = cls(data["type"], data["content"], data.get("priority", 0.5))
        frag.id = data.get("id", frag.id)
        frag.created_at = data.get("created_at", frag.created_at)
        return frag


def random_id(n: int = 4) -> str:
    """生成随机字母数字 ID"""
    import random, string
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=n))


# ─────────────────────────────────────────────────────────────
# 5 种片段类型
# ─────────────────────────────────────────────────────────────

class SkillFragment(MemoryFragment):
    """技能/工具片段 — 优先级 0.7"""

    def __init__(self, skill_name: str, description: str, example: str, priority: float = 0.7):
        content = f"【技能】{skill_name}\n描述：{description}\n示例：{example}"
        super().__init__("skill", content, priority)
        self.skill_name = skill_name


class TaskFragment(MemoryFragment):
    """当前任务片段 — 优先级 0.9"""

    def __init__(self, task: str, status: str, note: str = "", priority: float = 0.9):
        content = f"【任务】{task}\n状态：{status}"
        if note:
            content += f"\n备注：{note}"
        super().__init__("task", content, priority)
        self.task = task
        self.status = status


class ConfigFragment(MemoryFragment):
    """配置/系统片段 — 优先级 0.8"""

    def __init__(self, key: str, value: str, reason: str = "", priority: float = 0.8):
        content = f"【配置】{key} = {value}"
        if reason:
            content += f"\n原因：{reason}"
        super().__init__("config", content, priority)
        self.key = key
        self.value = value


class ConclusionFragment(MemoryFragment):
    """结论/决定片段 — 优先级 0.6"""

    def __init__(self, conclusion: str, source: str = "", priority: float = 0.6):
        content = f"【结论】{conclusion}"
        if source:
            content += f"\n来源：{source}"
        super().__init__("conclusion", content, priority)
        self.conclusion = conclusion


class LessonFragment(MemoryFragment):
    """教训/反思片段 — 优先级 0.5"""

    def __init__(self, lesson: str, context: str = "", priority: float = 0.5):
        content = f"【教训】{lesson}"
        if context:
            content += f"\n场景：{context}"
        super().__init__("lesson", content, priority)
        self.lesson = lesson


# ─────────────────────────────────────────────────────────────
# FragmentPool — 按优先级注入，超上限自动截断
# ─────────────────────────────────────────────────────────────

class FragmentPool:
    """
    片段池 — 管理多个片段的注入

    特点：
      - 按 priority 降序排列
      - 总 token 超限时，末尾片段被截断
      - 支持 to_context / to_searchable / to_snapshot
    """

    def __init__(self, max_tokens: int = 30000):
        self.fragments: list[MemoryFragment] = []
        self.max_tokens = max_tokens

    def add(self, fragment: MemoryFragment):
        """添加片段（按优先级降序插入）"""
        self.fragments.append(fragment)
        self.fragments.sort(key=lambda f: -f.priority)

    def add_all(self, *fragments: MemoryFragment):
        for f in fragments:
            self.add(f)

    def get_total_size(self) -> int:
        return sum(f.get_size() for f in self.fragments)

    def to_context(self) -> dict:
        """
        全部转注入格式
        按优先级注入，超总上限时末尾截断
        """
        results = []
        total_size = 0

        for frag in self.fragments:
            ctx = frag.to_context()
            frag_size = (
                estimate_tokens(ctx["content"])
                + (estimate_tokens(ctx.get("note", "")) if ctx.get("compressed") else 0)
            )

            if total_size + frag_size > self.max_tokens:
                remaining = self.max_tokens - total_size
                if remaining > 200:
                    ctx["content"] = truncate_to_tokens(ctx["content"], remaining - 50)
                    ctx["truncated"] = True
                    ctx["note"] = f"[已达上下文上限{self.max_tokens}tokens，部分内容已截断]"
                    results.append(ctx)
                break

            results.append(ctx)
            total_size += frag_size

        return {
            "fragments":    results,
            "total_tokens": total_size,
            "max_tokens":   self.max_tokens,
            "frag_count":   len(results),
            "total_frags":  len(self.fragments)
        }

    def to_searchable(self) -> str:
        return "\n".join(f.to_searchable() for f in self.fragments)

    def to_snapshot(self) -> str:
        return "\n---\n".join(f.to_snapshot() for f in self.fragments)

    def to_json(self) -> dict:
        return {
            "fragments": [f.to_json() for f in self.fragments],
            "stats": {
                "total":      len(self.fragments),
                "total_tokens": self.get_total_size(),
                "max_tokens":  self.max_tokens
            }
        }

    def __len__(self):
        return len(self.fragments)


# ─────────────────────────────────────────────────────────────
# 快速工厂函数
# ─────────────────────────────────────────────────────────────

def skill(name: str, desc: str, example: str, priority: float = 0.7) -> SkillFragment:
    return SkillFragment(name, desc, example, priority)

def task(task_name: str, status: str, note: str = "", priority: float = 0.9) -> TaskFragment:
    return TaskFragment(task_name, status, note, priority)

def config(key: str, value: str, reason: str = "", priority: float = 0.8) -> ConfigFragment:
    return ConfigFragment(key, value, reason, priority)

def conclusion(text: str, source: str = "", priority: float = 0.6) -> ConclusionFragment:
    return ConclusionFragment(text, source, priority)

def lesson(text: str, context: str = "", priority: float = 0.5) -> LessonFragment:
    return LessonFragment(text, context, priority)

def fragment(type_: str, content: str, priority: float = 0.5) -> MemoryFragment:
    return MemoryFragment(type_, content, priority)


# ─────────────────────────────────────────────────────────────
# CLI 测试
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("🧪 MemoryFragment 单元测试\n")

    # Test 1: 基本片段
    f1 = MemoryFragment("skill", "这个skill是发飞书图片的", 0.8)
    print(f"✅ Test1 - 基本片段: {f1.get_size()} tokens, id: {f1.id[:20]}...")

    # Test 2: 片段池
    pool = FragmentPool(max_tokens=500)
    pool.add(f1)
    pool.add(task("完成记忆系统", "进行中", "还差 L5 模式"))
    pool.add(config("model", "MiniMax-M2.7", "调研任务专用"))
    pool.add(conclusion("Codex 比 Claude Code 更 Rust 化", "AGENTS.md 分析"))

    ctx = pool.to_context()
    print(f"✅ Test2 - 片段池: {ctx['frag_count']}/{ctx['total_frags']} fragments, "
          f"{ctx['total_tokens']} tokens")

    # Test 3: 超大片段自动压缩
    long_content = "这是一段很长的记忆内容 " * 500
    f2 = MemoryFragment("note", long_content, 0.5)
    print(f"✅ Test3 - 超大片段: {f2.get_size()} tokens, isOversized: {f2.is_oversized()}")
    compressed = f2.to_context()
    print(f"    压缩后: {len(compressed['content'])} chars, compressed: {compressed['compressed']}")

    # Test 4: 快速工厂
    f4 = skill("飞书图片", "用message工具发送图片", "message(action=send, filePath=...)")
    print(f"✅ Test4 - 快速工厂: {f4.type}, skill_name: {f4.skill_name}")

    # Test 5: 序列化
    print(f"✅ Test5 - JSON: {json.dumps(f1.to_json(), ensure_ascii=False)[:80]}...")

    # Test 6: to_snapshot
    snap = f4.to_snapshot()
    print(f"✅ Test6 - Snapshot: {snap[:60]}")

    print("\n✅ 所有测试通过")
