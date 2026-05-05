#!/usr/bin/env python3
"""
auto_pre_checkpoint_trigger.py - Automatic pre-checkpoint trigger

Translates auto-pre-checkpoint.sh logic to Python.

Logic:
  - Read current in-progress task from 工作缓冲区.md
  - Call pre_checkpoint.py create to create a checkpoint
  - Optionally force creation with --force flag

CLI:
  python3 auto_pre_checkpoint_trigger.py
  python3 auto_pre_checkpoint_trigger.py --force
"""

import sys
import subprocess
import json
import re
from pathlib import Path

# === Paths ===
WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
BUFFER_PATH = MEMORY_DIR / "工作缓冲区.md"
PRE_CHECKPOINT_SCRIPT = MEMORY_DIR.parent / "skills" / "vector-memory-self-evolution" / "pre_checkpoint.py"

# Fallback: look for pre_checkpoint.py in skills dir
SKILL_DIR = WORKSPACE / "skills" / "vector-memory-self-evolution"
PRE_CHECKPOINT_SCRIPT_ALT = SKILL_DIR / "pre_checkpoint.py"


# ─────────────────────────────────────────────
# Extract in-progress task from buffer
# ─────────────────────────────────────────────
def get_buffer_task() -> str:
    """Extract the currently in-progress task from the work buffer."""
    if not BUFFER_PATH.exists():
        return ""

    try:
        with open(BUFFER_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.splitlines()
        task_lines = []
        capture = False

        # Look for common task section markers
        markers = [
            r"^#+\s*(当前任务|进行中|当前进行|in-progress|active task)",
            r"^#+\s*task",
            r"^-+\s*(task|任务)",
        ]

        for line in lines:
            stripped = line.strip()
            if any(re.match(m, stripped, re.IGNORECASE) for m in markers):
                capture = True
                continue
            if capture:
                # Stop at next header or empty section separator
                if stripped.startswith("#") or stripped == "---":
                    break
                if stripped:
                    task_lines.append(stripped)

        if task_lines:
            return " ".join(task_lines).strip()

        # Fallback: first non-header paragraph
        paragraph = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("-" * 3):
                if paragraph:
                    break
                continue
            if stripped:
                paragraph.append(stripped)
        if paragraph:
            return " ".join(paragraph[:5]).strip()

        return content[:300].strip()
    except Exception as e:
        return ""


# ─────────────────────────────────────────────
# Find pre_checkpoint.py
# ─────────────────────────────────────────────
def find_pre_checkpoint_script() -> Path:
    """Locate pre_checkpoint.py script."""
    for p in [PRE_CHECKPOINT_SCRIPT, PRE_CHECKPOINT_SCRIPT_ALT]:
        if p.exists():
            return p
    # Search in workspace skills dir
    for p in SKILL_DIR.parent.glob("*/pre_checkpoint.py"):
        if "vector-memory" in str(p):
            return p
    return PRE_CHECKPOINT_SCRIPT_ALT  # Return default even if not exists


# ─────────────────────────────────────────────
# Create pre-checkpoint via pre_checkpoint.py
# ─────────────────────────────────────────────
def create_pre_checkpoint(force: bool = False) -> dict:
    """
    Call pre_checkpoint.py create to create a checkpoint.

    Args:
        force: If True, pass --force flag.

    Returns:
        Dict with keys: success (bool), stdout (str), stderr (str), error (str|None)
    """
    script = find_pre_checkpoint_script()

    if not script.exists():
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": f"pre_checkpoint.py not found at {script}",
        }

    cmd = [sys.executable, str(script), "create"]
    if force:
        cmd.append("--force")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": None if result.returncode == 0 else f"Exit code: {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": "Timeout while creating pre-checkpoint",
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": str(e),
        }


# ─────────────────────────────────────────────
# Main logic
# ─────────────────────────────────────────────
def main():
    force = "--force" in sys.argv

    # Get task from buffer
    task = get_buffer_task()

    if not task:
        print("No in-progress task found in 工作缓冲区.md")
        print("Nothing to checkpoint.")
        sys.exit(0)

    print(f"Detected in-progress task: {task}")
    print(f"Creating pre-checkpoint...")

    result = create_pre_checkpoint(force=force)

    if result["success"]:
        print("Pre-checkpoint created successfully.")
        if result["stdout"]:
            print(result["stdout"])
    else:
        print(f"Failed to create pre-checkpoint: {result.get('error')}")
        if result["stderr"]:
            print(result["stderr"], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
