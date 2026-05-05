#!/usr/bin/env python3
"""
会话注入构建器
从 SNAPSHOT.md / 工作缓冲区.md / conversation-turns.json 构建注入字符串
支持 10K token 硬上限自动压缩

CLI:
    python3 build_session_injection.py        # 输出 JSON {injection, meta}
    python3 build_session_injection.py --build  # 仅输出注入文本
"""

import json
import os
import re
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
COUNTER_PATH = MEMORY_DIR / "conversation-turns.json"
SNAPSHOT_PATH = MEMORY_DIR / "SNAPSHOT.md"
BUFFER_PATH = MEMORY_DIR / "工作缓冲区.md"

# Token hard cap: 10K tokens
TOKEN_LIMIT = 10000

# Token estimation: Chinese chars * 2, English words * 1.3
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    other = len(text) - chinese_chars - english_words
    return int(chinese_chars * 2 + english_words * 1.3 + other * 1.0)


def read_file(path: Path, default: str = "") -> str:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except (UnicodeDecodeError, IOError):
            # 使用 errors='replace' 保留可见字符，不静默丢内容
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                pass
    return default


def compress_to_limit(text: str, limit: int = TOKEN_LIMIT) -> str:
    """Truncate text with a simple suffix indicator if over limit."""
    tokens = estimate_tokens(text)
    if tokens <= limit:
        return text
    # Binary search for truncation point
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    truncated = text[:low]
    return truncated + f"\n\n[... 内容已截断，原文约 {tokens} tokens，超出 {TOKEN_LIMIT} token 上限 ...]"


def build_injection_text() -> str:
    """Build the full injection text from available sources."""
    parts = []

    counter_data = _load_counter()
    counter_text = _format_counter(counter_data)
    if counter_text:
        parts.append(counter_text)

    snapshot_text = read_file(SNAPSHOT_PATH)
    if snapshot_text.strip():
        parts.append(f"## SNAPSHOT.md\n\n{snapshot_text.strip()}")

    buffer_text = read_file(BUFFER_PATH)
    if buffer_text.strip():
        parts.append(f"## 工作缓冲区.md\n\n{buffer_text.strip()}")

    injection = "\n\n---\n\n".join(parts)
    # Apply token limit
    injection = compress_to_limit(injection, TOKEN_LIMIT)
    return injection


def _load_counter() -> dict:
    if COUNTER_PATH.exists():
        try:
            with open(COUNTER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _format_counter(data: dict) -> str:
    turns = data.get("turns", 0)
    if turns == 0:
        return ""
    threshold = 20
    lines = [
        f"## 对话轮次状态",
        f"- 当前轮次: {turns}",
        f"- 复盘阈值: {threshold}",
        f"- 建议复盘: {'是' if turns >= threshold else '否'}",
    ]
    last = data.get("last_updated")
    if last:
        lines.append(f"- 上次更新: {last}")
    return "\n".join(lines)


def build() -> dict:
    """Return full JSON with injection text and metadata."""
    injection = build_injection_text()
    tokens = estimate_tokens(injection)
    return {
        "injection": injection,
        "meta": {
            "token_estimate": tokens,
            "token_limit": TOKEN_LIMIT,
            "within_limit": tokens <= TOKEN_LIMIT,
            "sources": {
                "conversation_turns": str(COUNTER_PATH),
                "snapshot": str(SNAPSHOT_PATH),
                "buffer": str(BUFFER_PATH),
            },
        },
    }


def main():
    if "--build" in sys.argv:
        print(build_injection_text())
    else:
        result = build()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
