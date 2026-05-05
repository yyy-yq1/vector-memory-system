#!/usr/bin/env python3
"""
LLM 智能记忆提取器 v1.0
使用 MiniMax API 从原始文本中提取结构化记忆

调用 MiniMax Chat API，传入提取 prompt，返回 JSON 格式的记忆列表
然后写入 L2 文件 + L4 Qdrant

融合来源：
  - elite-longterm-memory: Mem0 自动提取思路（用 LLM 代替规则）
  - fluid-memory: 增量总结 + 分类提取
"""
import sys
import json
import datetime
import hashlib
import httpx
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from memory_consts import MEMORY_DIR, MAX_CONTENT_LEN
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state

# ─────────────────────────────────────────────────────────────
# MiniMax API 调用
# ─────────────────────────────────────────────────────────────

def _load_api_key() -> str:
    """获取 MiniMax API key（mmx CLI 配置优先）"""
    import os, json
    # 优先从 mmx CLI 配置读取
    mmx_cfg = Path.home() / '.mmx' / 'config.json'
    if mmx_cfg.exists():
        try:
            data = json.loads(mmx_cfg.read_text())
            key = data.get('api_key', '')
            if key:
                return key
        except:
            pass
    # 环境变量
    return os.environ.get('MINIMAX_API_KEY', '')


EXTRACTION_PROMPT = (
    "你是一个记忆提取专家。从给定的文本中提取所有有价值的信息，输出JSON数组。\n\n"
    "输出格式（严格JSON数组）：\n"
    "[\n"
    "  {{\n"
    "    \"type\": \"error\" | \"practice\" | \"correction\" | \"event\" | \"gap\",\n"
    "    \"title\": \"简短标题（少于30字）\",\n"
    "    \"content\": \"详细描述（50-300字）\",\n"
    "    \"importance\": 0.0-1.0（重要性评分）\n"
    "  }}, ...\n"
    "]\n\n"
    "类型说明：\n"
    "- error: 错误、失败、异常、bug\n"
    "- practice: 有效的命令、配置、方法、最佳实践、验证成功的方案\n"
    "- correction: 错误做法→正确做法、修复记录\n"
    "- event: 里程碑、关键决策、系统变更、状态更新\n"
    "- gap: 知识盲区、待学习、未知领域\n\n"
    "要求：\n"
    "1. importance: error/correction 通常 0.7-1.0，practice 通常 0.5-0.9\n"
    "2. content 要具体，包含命令/配置/数值等细节\n"
    "3. 不要提取纯情感、无意义闲聊\n"
    "4. 最多提取 10 条最重要的记忆\n"
    "5. 只输出 JSON 数组，不要其他文字\n\n"
    "需要提取的文本：\n"
    "{text}\n"
)



def extract_with_llm(text: str, model: str = "MiniMax-M2.7") -> list[dict]:
    """
    使用 MiniMax LLM 从文本中提取结构化记忆
    返回 [{type, title, content, importance}, ...]
    """
    api_key = _load_api_key()
    if not api_key:
        return []

    prompt = EXTRACTION_PROMPT.format(text=text[:4000])  # 限制输入长度

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                "https://api.minimaxi.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一个精确的记忆提取助手。只输出 JSON，不输出其他内容。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,  # 低温度，确保稳定输出
                    "max_tokens": 2048,
                }
            )
            resp.raise_for_status()
            result = resp.json()

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return []

        # 提取 JSON（可能在 markdown 代码块或解释性文字中）
        content = content.strip()

        # 去掉 markdown 代码块
        if '```json' in content:
            import re
            m = re.search(r'```json\s*(.+?)```', content, re.DOTALL)
            if m:
                content = m.group(1).strip()
        elif '```' in content:
            import re
            m = re.search(r'```\s*(.+?)```', content, re.DOTALL)
            if m:
                content = m.group(1).strip()
        else:
            # 去掉解释性文字，只保留 JSON 数组部分
            import re
            m = re.search(r'(\[[\s\S]+\])', content)
            if m:
                content = m.group(1).strip()

        memories = json.loads(content)
        if isinstance(memories, dict):
            memories = [memories]
        return memories

    except Exception as e:
        print(f"⚠️  LLM 提取失败: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# 存储
# ─────────────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]

def _save_hash(text: str):
    h = _content_hash(text)
    state = _load_vectorize_state()
    synced = set(state.get("synced_hashes", []))
    synced.add(h)
    state["synced_hashes"] = list(synced)
    state["last_sync"] = datetime.datetime.now().isoformat()
    _save_vectorize_state(state)

def _load_synced() -> set:
    state = _load_vectorize_state()
    return set(state.get("synced_hashes", []))

def _write_entry(typ: str, title: str, content: str, source: str = '', importance: float = 0.5) -> bool:
    """写入 L2 文件 + L4 Qdrant，返回是否新写入"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.datetime.now().isoformat()
    type_dir = MEMORY_DIR / typ
    type_dir.mkdir(parents=True, exist_ok=True)
    filepath = type_dir / f"{today}.md"

    entry_text = f"""---
type: {typ}
timestamp: {timestamp}
title: {title}
context: {source}
importance: {importance}
---

{content}

---
"""
    if filepath.exists():
        existing = filepath.read_text(encoding='utf-8')
        if entry_text.strip() in existing:
            return False

    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(entry_text)

    try:
        store = get_store()
        store.add(content[:MAX_CONTENT_LEN], typ, source=source, metadata={
            "title": title,
            "importance": importance,
        })
    except Exception as e:
        print(f"    ⚠️  Qdrant: {e}")

    return True


def capture_extracted(memories: list[dict], source: str = '') -> dict:
    """
    将 LLM 提取的记忆写入 L2 + L4
    返回 {"written": N, "skipped": N, "types": {type: count}}
    """
    synced = _load_synced()
    written = 0
    skipped = 0
    type_counts = {}

    for mem in memories:
        typ = mem.get('type', 'practice')
        title = mem.get('title', '未命名')[:60]
        content = mem.get('content', '')
        importance = float(mem.get('importance', 0.5))

        if len(content) < 20:
            continue

        text_to_save = f"{title}\n{content}"
        h = _content_hash(text_to_save[:200])

        if h in synced:
            skipped += 1
            continue

        ok = _write_entry(typ, title, content, source=source, importance=importance)
        if ok:
            _save_hash(text_to_save[:200])
            synced.add(h)
            written += 1
        else:
            skipped += 1

        type_counts[typ] = type_counts.get(typ, 0) + 1

    return {"written": written, "skipped": skipped, "types": type_counts}


# ─────────────────────────────────────────────────────────────
# 主提取流程
# ─────────────────────────────────────────────────────────────

def llm_extract_text(text: str, source: str = '') -> dict:
    """
    从任意文本用 LLM 提取记忆并写入存储
    """
    print(f"  🔮 调用 MiniMax LLM 提取...")
    memories = extract_with_llm(text)
    if not memories:
        print(f"  ⚠️  LLM 未提取到内容")
        return {"written": 0, "skipped": 0, "types": {}}

    print(f"  📦 LLM 提取了 {len(memories)} 条记忆:")
    for m in memories:
        print(f"     [{m.get('type')}] (imp={m.get('importance',0):.1f}) {m.get('title','')[:50]}")

    result = capture_extracted(memories, source=source)
    print(f"  ✅ 写入 {result['written']} 条，跳过 {result['skipped']} 条（已存在）")
    return result


def llm_extract_file(filepath: Path, force: bool = False) -> dict:
    """
    从文件提取记忆（LLM 模式）
    """
    if not filepath.exists():
        return {"written": 0, "skipped": 0}

    content = filepath.read_text(encoding='utf-8')
    return llm_extract_text(content, source=f"file:{filepath.name}")


def llm_extract_session(session_id: str, messages: list[dict], force: bool = False) -> dict:
    """
    从会话消息列表提取记忆
    messages: [{role, text, timestamp}, ...]
    """
    # 构建会话摘要
    user_msgs = [m['text'][:300] for m in messages if m['role'] == 'user'][:10]
    assistant_msgs = [m['text'][:300] for m in messages if m['role'] == 'assistant'][:10]

    text = "## 用户提问\n" + "\n".join(f"- {m}" for m in user_msgs)
    text += "\n## 助手回复\n" + "\n".join(f"- {m}" for m in assistant_msgs)

    return llm_extract_text(text, source=f"session:{session_id[:20]}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='LLM 智能记忆提取')
    parser.add_argument('--file', type=str, help='从文件提取')
    parser.add_argument('--text', type=str, help='直接传入文本')
    parser.add_argument('--session-id', type=str, help='会话 ID')
    args = parser.parse_args()

    if args.file:
        result = llm_extract_file(Path(args.file))
        print(f"✅ 完成: 写入 {result['written']} 条")

    elif args.text:
        result = llm_extract_text(args.text)
        print(f"✅ 完成: 写入 {result['written']} 条")

    elif args.session_id:
        # 从 session_transcript_extractor 读取
        from session_transcript_extractor import _extract_messages_from_transcript
        sessions_dir = Path('/root/.openclaw/agents/main/sessions')
        for tf in sessions_dir.glob('*.jsonl'):
            if args.session_id in tf.stem:
                msgs = _extract_messages_from_transcript(tf)
                result = llm_extract_session(tf.stem.split('.')[0], msgs)
                print(f"✅ 完成: 写入 {result['written']} 条")
                break
        else:
            print(f"❌ 找不到会话 {args.session_id}")
    else:
        print("用法: llm_extract.py --text '要提取的文本'")
