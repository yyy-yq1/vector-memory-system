#!/usr/bin/env python3
"""
auto_confidence_trigger.py - Automatic confidence scoring trigger

Translates auto-confidence-trigger.sh logic to Python.

Logic:
  - Read task description (CLI arg or from 工作缓冲区.md)
  - Detect risk signals and reduce confidence score:
      irreversible (-0.3), external (-0.2), core-system (-0.2),
      security (-0.2), complex (-0.1), first-time (-0.2)
  - Simple task bonus: +0.1
  - Clamp final score to [0.0, 1.0]

CLI:
  python3 auto_confidence_trigger.py [task...]
  python3 auto_confidence_trigger.py --task "具体任务"
"""

import sys
import re
from pathlib import Path

# === Paths ===
WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
BUFFER_PATH = MEMORY_DIR / "工作缓冲区.md"


# ─────────────────────────────────────────────
# Confidence scoring
# ─────────────────────────────────────────────
def score_confidence(task: str) -> float:
    """
    Calculate automatic confidence score for a task.

    Risk signals (deductions):
      - irreversible (-0.3): actions that cannot be undone
      - external (-0.2):     operations affecting outside systems
      - core-system (-0.2): core system files or configurations
      - security (-0.2):    security-related operations
      - complex (-0.1):      architecturally complex tasks
      - first-time (-0.2):   first-time / unknown operations

    Bonus:
      - simple task (+0.1): routine, low-risk operations

    Returns:
        Float score clamped to [0.0, 1.0]
    """
    task_lower = task.lower()

    score = 1.0

    # Risk signals
    irreversible_signals = [
        "删除", "永久", "rm ", "drop ", "truncate", "delete ",
        "destroy", "wipe", "格式化", "format ",
    ]
    external_signals = [
        "发送", "邮件", "发邮件", "email", "post ", "发布",
        "公开", "public ", "推送", "通知",
    ]
    core_system_signals = [
        "系统", "内核", "kernel", "root", "系统配置",
        "system ", "sudo", "boot", "init",
    ]
    security_signals = [
        "安全", "密码", "passwd", "credential", "auth",
        "加密", "解密", "证书", "certificate", "ssl", "token",
    ]
    complex_signals = [
        "架构", "系统设计", "架构设计", "复杂任务", "多层",
        "architecture", "pipeline", "复杂",
    ]
    first_time_signals = [
        "首次", "第一次", "新任务", "未知", "不熟悉",
        "第一次做", "first time", "new task",
    ]

    # Simple task bonus signals
    simple_signals = [
        "搜索", "查询", "查看", "读取", "简单", "搜索",
        "search", "query", "read", "view", "查天气", "时间",
    ]

    # Deduct for risk signals
    for sig in irreversible_signals:
        if sig in task_lower:
            score -= 0.3
            break

    for sig in external_signals:
        if sig in task_lower:
            score -= 0.2
            break

    for sig in core_system_signals:
        if sig in task_lower:
            score -= 0.2
            break

    for sig in security_signals:
        if sig in task_lower:
            score -= 0.2
            break

    for sig in complex_signals:
        if sig in task_lower:
            score -= 0.1
            break

    for sig in first_time_signals:
        if sig in task_lower:
            score -= 0.2
            break

    # Bonus for simple tasks
    for sig in simple_signals:
        if sig in task_lower:
            score += 0.1
            break

    # Clamp to [0, 1]
    return max(0.0, min(1.0, score))


def get_buffer_task() -> str:
    """Extract task description from the work buffer file."""
    if not BUFFER_PATH.exists():
        return ""

    try:
        with open(BUFFER_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # Try to extract task from common frontmatter patterns
        # Look for task/任务 description after headers
        lines = content.splitlines()
        task_lines = []
        capture = False
        for line in lines:
            if re.match(r"^#+\s*(当前任务|task|进行中)", line, re.IGNORECASE):
                capture = True
                continue
            if capture:
                if line.strip().startswith("#"):
                    break
                task_lines.append(line.strip())
        if task_lines:
            return " ".join(task_lines).strip()
        # Fallback: return first non-empty non-header line
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return content[:200].strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def main():
    task = ""

    # Parse arguments
    if "--task" in sys.argv:
        idx = sys.argv.index("--task")
        if idx + 1 < len(sys.argv):
            task = sys.argv[idx + 1]
    else:
        # Collect remaining positional args as task
        args = sys.argv[1:]
        if args:
            task = " ".join(args)

    # If no task from CLI, try buffer
    if not task.strip():
        task = get_buffer_task()

    if not task.strip():
        print("Error: No task provided. Usage: auto_confidence_trigger.py [task...] or --task 'task'", file=sys.stderr)
        sys.exit(1)

    score = score_confidence(task)

    print(f"Task: {task}")
    print(f"Confidence score: {score:.2f}")

    # Interpretation
    if score >= 0.8:
        print("Assessment: High confidence - safe to proceed")
    elif score >= 0.5:
        print("Assessment: Medium confidence - verify before proceeding")
    else:
        print("Assessment: Low confidence - exercise caution, consider pre-checkpoint")


if __name__ == "__main__":
    main()
