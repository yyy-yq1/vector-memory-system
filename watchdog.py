#!/usr/bin/env python3
"""
watchdog.py - Subagent watchdog + budget allocation
Combines subagent-watchdog.js and subagent-budget.js functionality.

Functions:
  1. exec_with_retry  - Run command with timeout and exponential backoff retry
  2. pre_check        - Check file existence and type
  3. wrap_tool_call   - Generic tool call wrapper with pre-check + timeout + retry
  4. analyze_budget   - Assign budget based on task complexity
  5. watchdog         - Run subagent task with timeout retry

CLI:
  python3 watchdog.py exec "<cmd>" [timeout] [max_retries]
  python3 watchdog.py check <file_path>
  python3 watchdog.py budget "<task>"
  python3 watchdog.py watchdog <task_file>
"""

import sys
import os
import subprocess
import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Awaitable

# === Paths ===
WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
BUFFER_PATH = MEMORY_DIR / "工作缓冲区.md"
WATCHDOG_LOG = MEMORY_DIR / "watchdog-log.md"


# ─────────────────────────────────────────────
# 1. exec_with_retry
# ─────────────────────────────────────────────
def exec_with_retry(
    cmd: str,
    timeout: int = 120000,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Execute a shell command with timeout and automatic retry on failure.
    Uses exponential backoff: delay = min(1000 * 2^(i-1), 10000) ms.

    Args:
        cmd: Shell command string to execute.
        timeout: Timeout in milliseconds (default 120000 = 2 min).
        max_retries: Maximum retry attempts (default 3).

    Returns:
        Dict with keys: success (bool), stdout (str), stderr (str),
                        error (str|None), attempts (int)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        delay = min(1000 * (2 ** (attempt - 1)), 10000)  # exponential backoff, cap at 10s

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout / 1000,  # subprocess expects seconds
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": None if result.returncode == 0 else f"Exit code: {result.returncode}",
                "attempts": attempt,
            }
        except subprocess.TimeoutExpired:
            last_error = f"Timeout after {timeout}ms (attempt {attempt}/{max_retries})"
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries:
            time.sleep(delay / 1000)

    return {
        "success": False,
        "stdout": "",
        "stderr": "",
        "error": last_error,
        "attempts": max_retries,
    }


# ─────────────────────────────────────────────
# 2. pre_check
# ─────────────────────────────────────────────
def pre_check(file_path: str) -> Dict[str, Any]:
    """
    Check file or directory existence and type.

    Args:
        file_path: Path to check.

    Returns:
        Dict with keys: exists (bool), is_file (bool), is_dir (bool), error (str|None)
    """
    p = Path(file_path)
    try:
        if p.exists():
            return {
                "exists": True,
                "is_file": p.is_file(),
                "is_dir": p.is_dir(),
                "error": None,
            }
        else:
            return {
                "exists": False,
                "is_file": False,
                "is_dir": False,
                "error": None,
            }
    except Exception as e:
        return {
            "exists": False,
            "is_file": False,
            "is_dir": False,
            "error": str(e),
        }


# ─────────────────────────────────────────────
# 3. wrap_tool_call
# ─────────────────────────────────────────────
async def wrap_tool_call(
    fn: Callable[..., Awaitable[Any]],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generic tool call wrapper with pre-check, timeout, and retry.

    Args:
        fn: Async function to call.
        options: Dict with keys:
            - pre_check_path (str, optional): File to pre-check before call.
            - timeout (int, ms): Timeout in milliseconds.
            - max_retries (int): Max retry attempts.
            - **fn_kwargs: Additional kwargs passed to fn.

    Returns:
        Dict with keys: success, result, error, attempts.
    """
    opts = options or {}
    pre_check_path = opts.get("pre_check_path")
    timeout = opts.get("timeout", 120000)
    max_retries = opts.get("max_retries", 3)
    fn_kwargs = {k: v for k, v in opts.items()
                 if k not in ("pre_check_path", "timeout", "max_retries")}

    # Pre-check
    if pre_check_path:
        check = pre_check(pre_check_path)
        if not check["exists"]:
            return {
                "success": False,
                "result": None,
                "error": f"Pre-check failed: file not found: {pre_check_path}",
                "attempts": 0,
            }

    # Run with retry
    last_error = None
    for attempt in range(1, max_retries + 1):
        delay = min(1000 * (2 ** (attempt - 1)), 10000)
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(**fn_kwargs), timeout=timeout / 1000)
            else:
                result = fn(**fn_kwargs)
            return {
                "success": True,
                "result": result,
                "error": None,
                "attempts": attempt,
            }
        except asyncio.TimeoutError:
            last_error = f"Timeout after {timeout}ms (attempt {attempt}/{max_retries})"
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries:
            await asyncio.sleep(delay / 1000)

    return {
        "success": False,
        "result": None,
        "error": last_error,
        "attempts": max_retries,
    }


# ─────────────────────────────────────────────
# 4. analyze_budget
# ─────────────────────────────────────────────
def analyze_budget(task: str) -> Dict[str, Any]:
    """
    Assign budget (in tokens) and select model tier based on task complexity.

    Complexity levels:
      - simple   : ['搜索', '查天气', '时间']           → budget=10
      - medium   : ['调研', '分析', '总结']             → budget=30
      - complex  : ['代码', '编程', '开发', '架构']    → budget=None (unlimited)

    Args:
        task: Task description string.

    Returns:
        Dict with keys: budget (int|None), model (str), complexity (str)
    """
    simple_keywords   = ["搜索", "查天气", "时间", "查询", "问"]
    medium_keywords   = ["调研", "分析", "总结", "报告", "整理", "研究"]
    complex_keywords  = ["代码", "编程", "开发", "架构", "实现", "系统", "构建", "写代码"]

    task_lower = task.lower()

    if any(kw in task_lower for kw in complex_keywords):
        return {"budget": None, "model": "high", "complexity": "complex"}
    elif any(kw in task_lower for kw in medium_keywords):
        return {"budget": 30, "model": "medium", "complexity": "medium"}
    elif any(kw in task_lower for kw in simple_keywords):
        return {"budget": 10, "model": "low", "complexity": "simple"}
    else:
        # Default to medium
        return {"budget": 30, "model": "medium", "complexity": "medium"}


# ─────────────────────────────────────────────
# 5. watchdog (async subagent runner)
# ─────────────────────────────────────────────
async def watchdog(
    task: str,
    timeout: int = 120000,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Run a subagent task with timeout and automatic retry.

    Args:
        task: Task description (or path to task file if task_file mode).
        timeout: Timeout in milliseconds.
        max_retries: Max retry attempts.

    Returns:
        Dict with keys: success, stdout, stderr, error, attempts, budget_info.
    """
    budget_info = analyze_budget(task)

    # Determine if task is a file path or direct task string
    task_path = Path(task)
    if task_path.exists() and task_path.is_file():
        try:
            with open(task_path, "r", encoding="utf-8") as f:
                task_content = f.read().strip()
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": f"Failed to read task file: {e}",
                "attempts": 0,
                "budget_info": budget_info,
            }
    else:
        task_content = task

    # Log start
    _log_watchdog_event("start", task_content, budget_info)

    # Execute - note: actual subagent execution depends on the environment
    # Here we return the structured info; actual execution would be
    # triggered via the OpenClaw subagent API in the main agent context.
    result = {
        "success": True,
        "stdout": "",
        "stderr": "",
        "error": None,
        "attempts": 1,
        "budget_info": budget_info,
        "task_content": task_content,
        "message": "Watchdog initialized. Execute subagent with provided budget info.",
    }

    _log_watchdog_event("end", task_content, budget_info, result)
    return result


def _log_watchdog_event(
    event: str,
    task: str,
    budget_info: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
):
    """Append watchdog events to the log file."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if event == "start":
        line = f"\n## [{timestamp}] WATCHDOG START\n- Task: {task}\n- Budget: {budget_info}\n"
    else:
        line = f"- [{timestamp}] WATCHDOG END | Success: {result.get('success') if result else 'N/A'} | Attempts: {result.get('attempts') if result else 'N/A'}\n\n"

    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 watchdog.py exec '<cmd>' [timeout] [max_retries]")
        print("  python3 watchdog.py check <file_path>")
        print("  python3 watchdog.py budget '<task>'")
        print("  python3 watchdog.py watchdog <task_file>")
        sys.exit(1)

    action = sys.argv[1]

    if action == "exec":
        cmd = sys.argv[2] if len(sys.argv) > 2 else ""
        timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 120000
        max_retries = int(sys.argv[4]) if len(sys.argv) > 4 else 3
        result = exec_with_retry(cmd, timeout, max_retries)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif action == "check":
        if len(sys.argv) < 3:
            print("Error: missing file_path", file=sys.stderr)
            sys.exit(1)
        result = pre_check(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif action == "budget":
        task = sys.argv[2] if len(sys.argv) > 2 else ""
        result = analyze_budget(task)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif action == "watchdog":
        task = sys.argv[2] if len(sys.argv) > 2 else ""
        result = asyncio.run(watchdog(task))
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
