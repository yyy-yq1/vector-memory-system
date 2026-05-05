#!/usr/bin/env python3
"""
复盘触发器
检查 conversation-turns.json 是否达到复盘阈值（20轮）
触发时调用 skill_health_checker.py

CLI:
  python3 auto_review_trigger.py
"""

import json
import sys
import subprocess
from pathlib import Path

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
CONVERSATION_FILE = MEMORY_DIR / "conversation-turns.json"
SKILL_CHECKER = WORKSPACE / "skills/vector-memory-self-evolution/skill_health_checker.py"
REVIEW_THRESHOLD = 20


def load_conversation_turns() -> int:
    """加载对话轮次"""
    if not CONVERSATION_FILE.exists():
        return 0
    try:
        with open(CONVERSATION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("turns", 0)
    except (json.JSONDecodeError, KeyError):
        return 0


def save_conversation_turns(turns: int):
    """保存对话轮次"""
    with open(CONVERSATION_FILE, "w", encoding="utf-8") as f:
        json.dump({"turns": turns, "updated_at": str(Path(__file__).stat().st_mtime)}, f, ensure_ascii=False, indent=2)


def increment_turns():
    """增加一轮对话"""
    turns = load_conversation_turns()
    turns += 1
    save_conversation_turns(turns)
    return turns


def trigger_review():
    """触发复盘"""
    print(f"🎯 达到复盘阈值 ({REVIEW_THRESHOLD} 轮)，触发复盘检查...")

    if not SKILL_CHECKER.exists():
        print(f"⚠️ skill_health_checker.py 不存在，跳过")
        print(f"   期望路径: {SKILL_CHECKER}")
        return False

    try:
        result = subprocess.run(
            ["python3", str(SKILL_CHECKER)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        print("✅ 复盘完成")
        return True
    except subprocess.TimeoutExpired:
        print("⏰ 复盘超时")
        return False
    except Exception as e:
        print(f"❌ 复盘失败: {e}")
        return False


def main():
    turns = load_conversation_turns()
    print(f"当前对话轮次: {turns} / {REVIEW_THRESHOLD}")

    if turns >= REVIEW_THRESHOLD:
        trigger_review()
    else:
        remaining = REVIEW_THRESHOLD - turns
        print(f"还需 {remaining} 轮触发复盘")


if __name__ == "__main__":
    main()
