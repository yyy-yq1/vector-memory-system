#!/usr/bin/env python3
"""
Session Transcript Extractor v1.1
自动从 OpenClaw session transcript JSONL 文件中提取会话内容，写入 L2 文件 + L4 Qdrant

支持直接提取（越过 extract_memories 规则，直接写有价值的内容）
追踪已处理会话：避免重复处理
状态文件：memory/.processed_sessions.json
"""
import sys
import json
import re
import datetime
import hashlib
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from memory_consts import MEMORY_DIR, MAX_CONTENT_LEN
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state


# ─────────────────────────────────────────────────────────────
# 状态文件
# ─────────────────────────────────────────────────────────────

PROCESSED_SESSIONS_FILE = MEMORY_DIR / '.processed_sessions.json'

def _load_processed() -> dict:
    if PROCESSED_SESSIONS_FILE.exists():
        with open(PROCESSED_SESSIONS_FILE) as f:
            return json.load(f)
    return {"processed": {}, "last_run": None}

def _save_processed(data: dict):
    with open(PROCESSED_SESSIONS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _mark_processed(session_id: str, timestamp: str, count: int):
    data = _load_processed()
    data["processed"][session_id] = {
        "timestamp": timestamp,
        "extracted_count": count,
        "processed_at": datetime.datetime.now().isoformat()
    }
    data["last_run"] = datetime.datetime.now().isoformat()
    _save_processed(data)


# ─────────────────────────────────────────────────────────────
# Transcript 解析
# ─────────────────────────────────────────────────────────────

THINKING_RE = re.compile(r'<thinking[^>]*>.*?</thinking>', re.DOTALL | re.IGNORECASE)
TOOL_CALL_RE = re.compile(r'\[(?:tool_call|function_call)\].*?\[/(?:tool_call|function_call)\]', re.DOTALL)


def _clean_text(text: str) -> str:
    """清理文本：移除 thinking blocks、tool calls 等"""
    text = THINKING_RE.sub('[思考块]', text)
    text = TOOL_CALL_RE.sub('[工具调用]', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_messages_from_transcript(filepath: Path) -> list[dict]:
    """从 transcript JSONL 文件中提取所有对话消息"""
    messages = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get('type') != 'message':
                    continue
                msg = obj.get('message', {})
                role = msg.get('role', 'unknown')
                if role not in ('user', 'assistant'):
                    continue

                content = msg.get('content', '')
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get('type') == 'text':
                                text_parts.append(part.get('text', ''))
                            elif part.get('type') == 'output':
                                text_parts.append(str(part.get('content', '')))
                    text = '\n'.join(text_parts)
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content) if content else ''

                text = _clean_text(text)
                if len(text) < 15:
                    continue

                messages.append({
                    'role': role,
                    'text': text,
                    'timestamp': obj.get('timestamp', ''),
                })
            except Exception:
                continue
    return messages


# ─────────────────────────────────────────────────────────────
# 从会话消息中直接提取有价值内容
# ─────────────────────────────────────────────────────────────

# 有价值内容的模式
VALUE_PATTERNS = [
    # 助手给出的结果（命令输出、数值、状态）
    (re.compile(r'^(?:✅|❌|📊|📄|🔍|🚀|💡|⚠️|🔥|🎯).{5,}', re.MULTILINE), 'practice'),
    # 代码块中的内容
    (re.compile(r'```[\w]*\n.{20,}?```', re.DOTALL), 'practice'),
    # 表格数据
    (re.compile(r'\|.+\|.+\|\n\|[-:|]+\|', re.MULTILINE), 'event'),
    # 数字统计类
    (re.compile(r'\b\d+[\d.,]+%\b|\b\d+[\d.,]+条\b|\b\d+[\d.,]+个\b',), 'practice'),
    # 路径配置
    (re.compile(r'(?:路径|path|目录|dir)[:\s]+\S+', re.IGNORECASE), 'practice'),
    # 错误信息
    (re.compile(r'(?:error|exception|failed|失败|异常)[:\s]+.{5,}', re.IGNORECASE), 'error'),
    # 关键决策/结论
    (re.compile(r'(?:决定|结论|方案|选择)[:\s]+.{10,}', re.IGNORECASE), 'event'),
    # 版本/状态报告
    (re.compile(r'(?:版本|version|状态|status)[:\s]+.{5,}', re.IGNORECASE), 'event'),
]


def _extract_valuable_from_text(text: str, role: str) -> list[dict]:
    """从文本中提取有价值片段"""
    results = []
    for pattern, mem_type in VALUE_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(0).strip()
            if len(snippet) < 15:
                continue
            if len(snippet) > 500:
                snippet = snippet[:500] + '...'
            results.append({
                'type': mem_type,
                'text': snippet,
                'role': role,
            })
    return results


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


def _write_entry(typ: str, title: str, content: str, source: str = '') -> bool:
    """写入 L2 文件 + L4 Qdrant"""
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
---

{content}

---
"""
    if filepath.exists():
        existing = filepath.read_text(encoding='utf-8')
        if entry_text.strip() in existing:
            return False  # 已存在

    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(entry_text)

    # 写 Qdrant
    try:
        store = get_store()
        store.add(content[:MAX_CONTENT_LEN], typ, source=source, metadata={"title": title})
    except Exception as e:
        print(f"    ⚠️  Qdrant: {e}")

    return True


def _extract_and_save(text: str, typ: str, title_prefix: str, source: str = '') -> bool:
    """提取并保存有价值片段"""
    synced = _load_synced()
    h = _content_hash(text[:200])
    if h in synced:
        return False

    ok = _write_entry(typ, f"{title_prefix}: {text[:60]}", text, source=source)
    if ok:
        _save_hash(text[:200])
    return ok


# ─────────────────────────────────────────────────────────────
# 核心处理函数
# ─────────────────────────────────────────────────────────────

def process_session_transcript(filepath: Path, force: bool = False) -> dict:
    """
    处理单个 session transcript 文件
    返回 {"extracted": N, "skipped": N, "session_id": str, "messages": N}
    """
    session_id = filepath.stem.split('.')[0]

    if not force:
        processed = _load_processed()
        if session_id in processed.get('processed', {}):
            return {"extracted": 0, "skipped": 0, "session_id": session_id, "already_processed": True}

    messages = _extract_messages_from_transcript(filepath)
    if not messages:
        return {"extracted": 0, "skipped": 0, "session_id": session_id, "already_processed": False}

    ts = messages[0].get('timestamp', datetime.datetime.now().isoformat())
    date_str = ts[:10] if ts else datetime.datetime.now().strftime('%Y-%m-%d')
    source_label = f"session:{session_id[:20]}"

    extracted = 0
    skipped = 0
    valuable_total = 0

    # 对助手消息进行有价值内容提取
    for msg in messages:
        if msg['role'] != 'assistant':
            continue

        text = msg['text']

        # 方法1：直接模式匹配有价值片段
        valuable = _extract_valuable_from_text(text, 'assistant')
        valuable_total += len(valuable)

        for v in valuable:
            if _extract_and_save(v['text'], v['type'], f"会话:{msg['role']}", source=source_label):
                extracted += 1
            else:
                skipped += 1

        # 方法2：如果整段文字超过100字且有实质内容，直接保存
        if len(text) > 100 and len(text) < 2000:
            # 检查是否有实质内容（非纯命令）
            if not re.match(r'^[`\-\s]{0,20}$', text.strip()) and 'thinking' not in text.lower():
                # 作为 practice 保存
                if _extract_and_save(text, 'practice', '会话记录', source=source_label):
                    extracted += 1
                else:
                    skipped += 1

    # 同时把会话摘要追加到每日日志（供 extract_memories.py 处理）
    daily_log = MEMORY_DIR / f"{date_str}.md"
    session_meta = f"\n\n---\n\n## 会话记录: {session_id[:20]}\n\n时间: {ts}\n消息数: {len(messages)}\n"
    # 追加用户问题和助手回复摘要
    user_msgs = [m for m in messages if m['role'] == 'user']
    assistant_msgs = [m for m in messages if m['role'] == 'assistant']
    if user_msgs:
        session_meta += f"\n用户提问 ({len(user_msgs)} 条)：\n"
        for m in user_msgs[:5]:
            q = m['text'][:150]
            session_meta += f"- {q}\n"
    if assistant_msgs:
        session_meta += f"\n助手回复摘要：\n"
        for m in assistant_msgs[:3]:
            a = m['text'][:200]
            session_meta += f"- {a}\n"

    if daily_log.exists():
        existing = daily_log.read_text(encoding='utf-8')
        if session_id not in existing:
            with open(daily_log, 'a', encoding='utf-8') as f:
                f.write(session_meta)
    else:
        with open(daily_log, 'w', encoding='utf-8') as f:
            f.write(f"# {date_str} 日志\n" + session_meta)

    _mark_processed(session_id, ts, extracted)

    return {
        "extracted": extracted,
        "skipped": skipped,
        "session_id": session_id,
        "messages": len(messages),
        "valuable_found": valuable_total,
        "already_processed": False,
    }


def process_recent_sessions(sessions_dir: Path, hours: int = 24, force: bool = False) -> dict:
    """处理最近 N 小时内的新会话"""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)

    transcript_files = []
    for f in sessions_dir.glob('*.jsonl'):
        if any(x in f.name for x in ('checkpoint', 'trajectory', 'trajectory-path', '.bak')):
            continue
        transcript_files.append(f)

    transcript_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    results = {"processed": [], "total_extracted": 0, "total_skipped": 0, "errors": []}
    for tf in transcript_files:
        try:
            mtime = datetime.datetime.fromtimestamp(tf.stat().st_mtime)
            if mtime < cutoff and not force:
                break
        except Exception:
            pass

        try:
            r = process_session_transcript(tf, force=force)
            results["processed"].append(r)
            results["total_extracted"] += r['extracted']
            results["total_skipped"] += r['skipped']
            if r['extracted'] > 0 or r.get('messages', 0) > 0:
                print(f"  📄 {tf.name}: +{r['extracted']} 条 | {r.get('messages',0)} 条消息 | {'已处理' if r.get('already_processed') else '新'}")
        except Exception as e:
            results["errors"].append({"file": str(tf), "error": str(e)})
            print(f"  ❌ {tf.name}: {e}")

    return results


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Session Transcript Extractor v1.1')
    parser.add_argument('--sessions-dir', type=str,
        default='/root/.openclaw/agents/main/sessions')
    parser.add_argument('--hours', type=int, default=24)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--file', type=str)
    parser.add_argument('--stats', action='store_true')
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir)

    if args.stats:
        data = _load_processed()
        processed = data.get('processed', {})
        print(f"📊 已处理会话: {len(processed)} 个")
        print(f"📊 上次运行: {data.get('last_run', '从未')}")
        for sid, info in list(processed.items())[-5:]:
            print(f"  {sid[:30]} | {info.get('timestamp','')[:10]} | +{info.get('extracted_count',0)} 条")
        sys.exit(0)

    if args.file:
        r = process_session_transcript(Path(args.file), force=args.force)
        print(f"✅: +{r['extracted']} | 跳过 {r['skipped']} | {r.get('messages',0)} 条消息")
    else:
        print(f"🚀 处理最近 {args.hours} 小时内的会话 transcripts\n")
        results = process_recent_sessions(sessions_dir, hours=args.hours, force=args.force)
        print(f"\n✅ 会话数: {len(results['processed'])} | 新增: {results['total_extracted']} 条 | 跳过: {results['total_skipped']} 条")
        if results['errors']:
            print(f"❌ 错误: {len(results['errors'])}")
