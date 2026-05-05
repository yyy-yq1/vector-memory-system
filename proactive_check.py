#!/usr/bin/env python3
"""
主动预判检查
从工作缓冲区和 SNAPSHOT 读取待办和进行中任务

CLI:
  python3 proactive_check.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
SNAPSHOT_DIR = MEMORY_DIR / "SNAPSHOT"
BUFFER_FILE = MEMORY_DIR / "buffer.json"
TASKS_FILE = MEMORY_DIR / "tasks.json"


def load_json(filepath: Path, default=None):
    """安全加载 JSON 文件"""
    if not filepath.exists():
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return default


def get_pending_tasks() -> list:
    """获取待办任务"""
    tasks = load_json(TASKS_FILE, {"tasks": []})
    task_list = tasks.get("tasks", []) if isinstance(tasks, dict) else tasks

    pending = []
    in_progress = []
    for t in task_list:
        status = t.get("status", "pending")
        if status == "in_progress":
            in_progress.append(t)
        elif status == "pending":
            pending.append(t)
    return pending, in_progress


def get_snapshot_status() -> dict:
    """获取快照状态"""
    if not SNAPSHOT_DIR.exists():
        return {"count": 0, "latest": None}

    snapshots = sorted(
        SNAPSHOT_DIR.glob("config_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    latest = None
    if snapshots:
        latest = snapshots[0].name

    return {
        "count": len(snapshots),
        "latest": latest,
    }


def get_buffer_items() -> list:
    """获取工作缓冲区内容"""
    buffer = load_json(BUFFER_FILE, {"items": []})
    items = buffer.get("items", []) if isinstance(buffer, dict) else buffer
    return items


def main():
    print("=" * 60)
    print("主动预判检查")
    print("=" * 60)

    # 待办任务
    pending, in_progress = get_pending_tasks()
    print(f"\n📋 待办任务 ({len(pending)} 项)")
    if pending:
        for t in pending[:10]:  # 最多显示10项
            title = t.get("title", t.get("name", "未知"))
            priority = t.get("priority", "normal")
            due = t.get("due", "无期限")
            print(f"  • {title} [{priority}] (截止: {due})")
        if len(pending) > 10:
            print(f"  ... 还有 {len(pending) - 10} 项")
    else:
        print("  (无)")

    print(f"\n🔄 进行中任务 ({len(in_progress)} 项)")
    if in_progress:
        for t in in_progress[:10]:
            title = t.get("title", t.get("name", "未知"))
            progress = t.get("progress", "未知")
            print(f"  • {title} [{progress}]")
        if len(in_progress) > 10:
            print(f"  ... 还有 {len(in_progress) - 10} 项")
    else:
        print("  (无)")

    # 快照状态
    snap = get_snapshot_status()
    print(f"\n💾 配置快照")
    print(f"  快照数量: {snap['count']}")
    if snap["latest"]:
        print(f"  最新快照: {snap['latest']}")
    else:
        print("  (无快照)")

    # 工作缓冲区
    buffer_items = get_buffer_items()
    print(f"\n📦 工作缓冲区 ({len(buffer_items)} 项)")
    if buffer_items:
        for item in buffer_items[:5]:
            content = str(item.get("content", item))[:50]
            print(f"  • {content}...")
        if len(buffer_items) > 5:
            print(f"  ... 还有 {len(buffer_items) - 5} 项")
    else:
        print("  (空)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
