#!/usr/bin/env python3
"""
配置快照
监控 openclaw.json MD5 变化，变化时自动备份
快照目录: memory/SNAPSHOT/
保留最近5份

CLI:
  python3 config_snapshot.py check    # 检查并快照
  python3 config_snapshot.py list     # 列出快照
"""

import json
import sys
import hashlib
import shutil
from pathlib import Path
from datetime import datetime

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
SNAPSHOT_DIR = MEMORY_DIR / "SNAPSHOT"
OPENCLAW_JSON = Path.home() / ".openclaw/openclaw.json"
MAX_SNAPSHOTS = 5

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def get_file_md5(filepath: Path) -> str:
    """计算文件 MD5"""
    if not filepath.exists():
        return None
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def get_current_md5() -> str:
    return get_file_md5(OPENCLAW_JSON)


def load_md5_history() -> dict:
    """加载 MD5 历史记录"""
    history_file = SNAPSHOT_DIR / ".md5_history.json"
    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_md5": None, "last_check": None}


def save_md5_history(history: dict):
    history_file = SNAPSHOT_DIR / ".md5_history.json"
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def create_snapshot() -> bool:
    """
    创建配置快照
    返回 True 表示创建了新快照，False 表示无变化
    """
    current_md5 = get_current_md5()
    history = load_md5_history()

    if current_md5 is None:
        print(f"配置文件不存在: {OPENCLAW_JSON}")
        return False

    if current_md5 == history.get("last_md5"):
        print(f"配置无变化 (MD5: {current_md5[:8]}...)")
        return False

    # 创建快照
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"config_{timestamp}_{current_md5[:8]}.json"
    snapshot_path = SNAPSHOT_DIR / snapshot_name

    shutil.copy2(OPENCLAW_JSON, snapshot_path)
    print(f"✅ 已创建快照: {snapshot_name}")

    # 更新历史
    history["last_md5"] = current_md5
    history["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_md5_history(history)

    # 清理旧快照，保留最近5份
    cleanup_old_snapshots()
    return True


def cleanup_old_snapshots():
    """清理旧快照，保留最近 MAX_SNAPSHOTS 份"""
    snapshots = sorted(
        SNAPSHOT_DIR.glob("config_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in snapshots[MAX_SNAPSHOTS:]:
        old.unlink()
        removed += 1
    if removed:
        print(f"🗑️  已清理 {removed} 份旧快照")


def list_snapshots():
    """列出所有快照"""
    snapshots = sorted(
        SNAPSHOT_DIR.glob("config_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not snapshots:
        print("暂无快照")
        return []
    print(f"{'文件名':<50} {'修改时间':<20} {'大小'}")
    print("-" * 90)
    for sp in snapshots:
        mtime = datetime.fromtimestamp(sp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size = sp.stat().st_size
        print(f"{sp.name:<50} {mtime:<20} {size} bytes")

    history = load_md5_history()
    print(f"\n当前 MD5: {history.get('last_md5', 'unknown')}")
    print(f"最后检查: {history.get('last_check', 'unknown')}")
    return snapshots


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        create_snapshot()

    elif cmd == "list":
        list_snapshots()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
