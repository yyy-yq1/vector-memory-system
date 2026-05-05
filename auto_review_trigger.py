#!/usr/bin/env python3
"""
复盘触发器
检查对话轮次是否达到复盘阈值（20轮）
达到时调用 skill_health_checker.py

轮次计数委托给 conversation_counter.py（唯一真实来源）
触发检查委托给 brain_agent.check_review_needed()

CLI:
  python3 auto_review_trigger.py
"""

import json
import subprocess
import sys
from pathlib import Path

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
SKILL_DIR = WORKSPACE / "skills" / "skills" / "vector-memory-self-evolution"
COUNTER_SCRIPT = SKILL_DIR / "conversation_counter.py"
SKILL_CHECKER = SKILL_DIR / "skill_health_checker.py"
REVIEW_THRESHOLD = 20


def get_turns() -> int:
    """通过 conversation_counter.py 获取当前轮次（唯一真实来源）"""
    try:
        result = subprocess.run(
            [sys.executable, str(COUNTER_SCRIPT)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("turns", 0)
    except Exception:
        pass
    return 0


def trigger_review():
    """触发复盘（调用 skill_health_checker.py）"""
    print(f"🎯 达到复盘阈值 ({REVIEW_THRESHOLD} 轮)，触发复盘检查...")

    if not SKILL_CHECKER.exists():
        print(f"⚠️ skill_health_checker.py 不存在，跳过")
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_CHECKER)],
            capture_output=True, text=True, timeout=120
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
    turns = get_turns()
    print(f"当前对话轮次: {turns} / {REVIEW_THRESHOLD}")

    if turns >= REVIEW_THRESHOLD:
        trigger_review()
    else:
        remaining = REVIEW_THRESHOLD - turns
        print(f"还需 {remaining} 轮触发复盘")


if __name__ == "__main__":
    main()
