#!/usr/bin/env python3
"""
对话轮次计数器
追踪对话轮次，判断是否达到复盘阈值
读取/写入 memory/conversation-turns.json

CLI:
    python3 conversation_counter.py         # 返回当前状态
    python3 conversation_counter.py --inc    # 递增并返回
    python3 conversation_counter.py --reset  # 重置
"""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

REVIEW_THRESHOLD = 20

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
COUNTER_PATH = MEMORY_DIR / "conversation-turns.json"


def _load() -> dict:
    """Load counter data, return defaults if file doesn't exist."""
    if COUNTER_PATH.exists():
        try:
            with open(COUNTER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"turns": 0, "last_updated": None, "review_count": 0}


def _save(data: dict) -> None:
    """Atomically write counter data via temp file + move."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(MEMORY_DIR), suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, str(COUNTER_PATH))
    except Exception:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise




def get_status() -> dict:
    """Return current counter status."""
    data = _load()
    turns = data.get("turns", 0)
    review_count = data.get("review_count", 0)
    return {
        "turns": turns,
        "review_threshold": REVIEW_THRESHOLD,
        "needs_review": turns >= REVIEW_THRESHOLD,
        "review_count": review_count,
        "last_updated": data.get("last_updated"),
    }


def increment() -> dict:
    """Increment counter by 1 and return updated status."""
    data = _load()
    data["turns"] = data.get("turns", 0) + 1
    from datetime import datetime, timezone
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    if data["turns"] == REVIEW_THRESHOLD:
        data["review_count"] = data.get("review_count", 0) + 1
    _save(data)
    return get_status()


def reset() -> dict:
    """Reset counter to zero."""
    data = _load()
    data["turns"] = 0
    from datetime import datetime, timezone
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return get_status()


def main():
    if "--reset" in sys.argv:
        result = reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--inc" in sys.argv:
        result = increment()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = get_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
