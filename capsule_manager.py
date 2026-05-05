#!/usr/bin/env python3
"""
胶囊管理系统
capsules 存储: memory/capsules.json
capsule 文件目录: WORKSPACE / 'skills' / '私人胶囊'

CLI:
  python3 capsule_manager.py list              # 列出所有胶囊
  python3 capsule_manager.py create <name> <type> <pattern>  # 创建胶囊
  python3 capsule_manager.py suggest "<result>" "<task>"  # 自动建议
  python3 capsule_manager.py update <name> --maturity tested  # 更新成熟度
  python3 capsule_manager.py get <name>       # 获取详情
"""

import json
import sys
import os
import hashlib
import tempfile
import shutil
import fcntl
from pathlib import Path

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
CAPSULES_FILE = MEMORY_DIR / "capsules.json"
CAPSULES_DIR = WORKSPACE / "skills" / "私人胶囊"

# 确保目录存在
CAPSULES_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def load_capsules():
    """加载胶囊数据"""
    if not CAPSULES_FILE.exists():
        return {"capsules": []}
    with open(CAPSULES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_capsules_unlocked():
    """Load capsules without locking (caller must hold lock)."""
    if not CAPSULES_FILE.exists():
        return {"capsules": []}
    with open(CAPSULES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_capsules_atomic(data):
    """原子保存胶囊数据（写锁 + temp file + rename）"""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(MEMORY_DIR), suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, ensure_ascii=False, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        shutil.move(tmp_path, str(CAPSULES_FILE))
    except Exception:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise


def save_capsules(data):
    """保存胶囊数据（对外接口，写锁 + 原子rename）"""
    save_capsules_atomic(data)


def list_capsules():
    """列出所有胶囊"""
    data = load_capsules()
    capsules = data.get("capsules", [])
    if not capsules:
        print("暂无胶囊")
        return
    print(f"{'名称':<30} {'类型':<15} {'成熟度':<10} {'触发词数':<8} {'创建时间'}")
    print("-" * 80)
    for c in capsules:
        keywords = c.get("trigger_keywords", [])
        created = c.get("created_at", "unknown")
        print(f"{c['name']:<30} {c.get('type','unknown'):<15} {c.get('maturity','draft'):<10} {len(keywords):<8} {created}")


def create_capsule(name: str, cap_type: str, pattern: str):
    """创建新胶囊"""
    data = load_capsules()
    capsules = data.get("capsules", [])

    # 检查是否已存在
    for c in capsules:
        if c["name"] == name:
            print(f"胶囊 '{name}' 已存在")
            return

    from datetime import datetime
    capsule = {
        "name": name,
        "type": cap_type,
        "pattern": pattern,
        "trigger_keywords": [],
        "maturity": "draft",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usage_count": 0,
        "last_used": None,
    }
    capsules.append(capsule)
    data["capsules"] = capsules
    save_capsules(data)

    # 创建胶囊文件
    capsule_file = CAPSULES_DIR / f"{name}.md"
    capsule_file.write_text(
        f"# {name}\n\n**类型**: {cap_type}\n**模式**: {pattern}\n**成熟度**: draft\n\n",
        encoding="utf-8",
    )
    print(f"已创建胶囊 '{name}'，文件: {capsule_file}")


def suggest_capsule(result: str, task: str):
    """
    自动建议是否值得创建胶囊
    
    判断条件：
    - 涉及多个并行 subagent → 创建
    - 复杂关键词（重构/调研/系统/架构/多步骤） → 创建
    - 耗时关键词（30分钟/1小时） → 创建
    - 其他 → 跳过
    """
    # 复杂关键词
    complex_keywords = [
        "重构", "调研", "系统", "架构", "多步骤", "分析", "设计",
        "实现", "优化", "迁移", "部署", "集成", "测试", "审查",
        " benchmark ", " performance ", " refactor ", " research ",
        " architecture ", " system ", " multi-step ",
    ]
    # 耗时关键词
    time_keywords = ["30分钟", "1小时", "2小时", "半小时", "一小时"]
    # 并行 subagent 关键词
    parallel_keywords = ["并行", "parallel", "同时", "多个subagent", "多个 agent"]

    task_lower = task.lower()
    result_lower = result.lower()

    reasons = []

    # 检查并行 subagent
    for kw in parallel_keywords:
        if kw.lower() in task_lower or kw.lower() in result_lower:
            reasons.append(f"涉及并行操作({kw})")
            break

    # 检查复杂关键词
    for kw in complex_keywords:
        if kw.lower() in task_lower or kw.lower() in result_lower:
            reasons.append(f"复杂任务({kw})")
            break

    # 检查耗时关键词
    for kw in time_keywords:
        if kw in task or kw in result:
            reasons.append(f"耗时任务({kw})")
            break

    if reasons:
        print(f"✅ 建议创建胶囊")
        print(f"   原因: {'; '.join(reasons)}")
        return True
    else:
        print(f"➖ 暂不建议创建胶囊（任务较简单）")
        return False


def update_capsule(name: str, maturity: str = None):
    """更新胶囊成熟度"""
    data = load_capsules()
    capsules = data.get("capsules", [])
    found = False
    for c in capsules:
        if c["name"] == name:
            if maturity:
                c["maturity"] = maturity
            from datetime import datetime
            c["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            found = True
            print(f"已更新胶囊 '{name}' 成熟度为 '{maturity}'")
            break
    if not found:
        print(f"胶囊 '{name}' 不存在")
        return
    data["capsules"] = capsules
    save_capsules(data)


def get_capsule(name: str):
    """获取胶囊详情"""
    data = load_capsules()
    capsules = data.get("capsules", [])
    for c in capsules:
        if c["name"] == name:
            print(f"=== 胶囊: {name} ===")
            print(f"  类型: {c.get('type', 'unknown')}")
            print(f"  模式: {c.get('pattern', 'unknown')}")
            print(f"  成熟度: {c.get('maturity', 'draft')}")
            print(f"  触发词: {c.get('trigger_keywords', [])}")
            print(f"  使用次数: {c.get('usage_count', 0)}")
            print(f"  创建时间: {c.get('created_at', 'unknown')}")
            print(f"  最后使用: {c.get('last_used', '从未使用')}")
            return
    print(f"胶囊 '{name}' 不存在")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        list_capsules()

    elif cmd == "create":
        if len(sys.argv) < 5:
            print("用法: capsule_manager.py create <name> <type> <pattern>")
            sys.exit(1)
        create_capsule(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "suggest":
        if len(sys.argv) < 4:
            print("用法: capsule_manager.py suggest \"<result>\" \"<task>\"")
            sys.exit(1)
        suggest_capsule(sys.argv[2], sys.argv[3])

    elif cmd == "update":
        if len(sys.argv) < 3:
            print("用法: capsule_manager.py update <name> --maturity <level>")
            sys.exit(1)
        name = sys.argv[2]
        maturity = None
        if "--maturity" in sys.argv:
            idx = sys.argv.index("--maturity")
            maturity = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        update_capsule(name, maturity)

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("用法: capsule_manager.py get <name>")
            sys.exit(1)
        get_capsule(sys.argv[2])

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
