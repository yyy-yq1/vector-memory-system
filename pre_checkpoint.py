#!/usr/bin/env python3
"""
Pre-checkpoint — 复杂任务前的强制快照系统
==========================================

整合自 Brain-v1.1.8 pre-checkpoint.js

在置信度 < 0.6 或任务涉及多个 subagent 时，
将「打算怎么做」写入缓冲区，确保压缩后无缝衔接。

工作流程：
  任务进入 → 评估置信度 → 需要 checkpoint → 写入预检点
  → 执行任务 → 标记检点完成

使用方式：
  python3 pre_checkpoint.py create "调研推特AI博主" "1.派researcher查趋势 2.整理报告" --confidence 7 --steps 3 --subagents researcher
  python3 pre_checkpoint.py complete <checkpoint_id> "任务完成"
  python3 pre_checkpoint.py status
"""
import os
import re
import hashlib
import datetime
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
BUFFER_FILE = MEMORY_DIR / '工作缓冲区.md'
SNAPSHOT_FILE = MEMORY_DIR / 'SNAPSHOT.md'
CHECKPOINT_DIR = MEMORY_DIR / 'checkpoints'

# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _cst_now() -> str:
    """返回 CST 时区当前时间"""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')

def _generate_id() -> str:
    """生成 8 位随机 ID"""
    return hashlib.sha256(str(datetime.datetime.now()).encode()).hexdigest()[:8]

def _atomic_write(file_path: Path, content):
    """原子写入：临时文件 → shutil.move（自动处理 dict/str）"""
    import shutil
    if isinstance(content, dict):
        import json
        content = json.dumps(content, ensure_ascii=False, indent=2)
    tmp = str(file_path) + f'.tmp.{os.getpid()}'
    Path(tmp).write_text(content, encoding='utf-8')
    shutil.move(tmp, str(file_path))

def _read_file(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding='utf-8')
    except Exception:
        pass
    return ""

# ─────────────────────────────────────────────────────────────
# Checkpoint 管理
# ─────────────────────────────────────────────────────────────

def create_checkpoint(
    task: str,
    plan: str,
    confidence: int = 5,
    steps: int = 0,
    subagents: list = None,
    parallel: bool = False,
) -> str:
    """
    创建预检点

    返回 checkpoint_id（8位哈希）
    """
    subagents = subagents or []
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp_id = _generate_id()
    ts = _cst_now()

    # Markdown block 格式
    block = f"""

<!-- PRE-CHECKPOINT {cp_id} | {ts} -->
## 🔸 预检点 [{cp_id[:6]}]（{ts}）

### 任务
{task}

### 计划
{plan}

### 元信息
| 字段 | 值 |
|:---|:---|
| 置信度 | {confidence}/10 |
| 预计步数 | {steps} |
| 子Agent | {', '.join(subagents) or '无'} |
| 并行 | {'是' if parallel else '否'} |
| 状态 | ⏳ 进行中 |

### 进度记录
- [{ts}] 任务开始

"""

    # 追加到缓冲区
    existing = _read_file(BUFFER_FILE)
    _atomic_write(BUFFER_FILE, existing + block)

    # 更新 SNAPSHOT（标记进行中任务）
    _update_snapshot(task, ts)

    # 保存独立 checkpoint 文件（用于恢复）
    cp_file = CHECKPOINT_DIR / f"checkpoint_{cp_id}.json"
    cp_data = {
        "id": cp_id,
        "task": task,
        "plan": plan,
        "confidence": confidence,
        "steps": steps,
        "subagents": subagents,
        "parallel": parallel,
        "status": "in_progress",
        "created_at": ts,
        "progress": [f"[{ts}] 任务开始"],
    }
    _atomic_write(cp_file, cp_data)

    return cp_id


def complete_checkpoint(checkpoint_id: str, result: str = "完成") -> bool:
    """
    标记检点完成

    1. 替换状态：⏳ → ✅
    2. 追加完成记录
    3. 更新独立 checkpoint 文件
    """
    ts = _cst_now()

    # 更新缓冲区
    content = _read_file(BUFFER_FILE)
    safe_id = re.escape(checkpoint_id)

    # 替换状态标记
    pattern_state = re.compile(
        rf'(<!-- PRE-CHECKPOINT {safe_id}[\s\S]*?状态 \| )⏳ 进行中',
        re.MULTILINE
    )
    content = pattern_state.sub(r'\1✅ 完成', content)

    # 追加完成记录
    pattern_progress = re.compile(
        rf'(<!-- PRE-CHECKPOINT {safe_id}[\s\S]*?(\- \[.*?\].*?\n))',
        re.MULTILINE
    )
    def add_complete(m):
        return m.group(1) + f"- [{ts}] 任务完成: {result}\n"
    content = pattern_progress.sub(add_complete, content)

    _atomic_write(BUFFER_FILE, content)

    # 更新独立 checkpoint 文件
    cp_file = CHECKPOINT_DIR / f"checkpoint_{checkpoint_id}.json"
    if cp_file.exists():
        try:
            data = json.loads(cp_file.read_text())
            data["status"] = "completed"
            data["completed_at"] = ts
            data["result"] = result
            data["progress"].append(f"[{ts}] 任务完成: {result}")
            _atomic_write(cp_file, data)
        except Exception:
            pass

    return True


def add_progress(checkpoint_id: str, note: str):
    """追加进度记录"""
    ts = _cst_now()

    content = _read_file(BUFFER_FILE)
    safe_id = re.escape(checkpoint_id)
    pattern = re.compile(
        rf'(<!-- PRE-CHECKPOINT {safe_id}[\s\S]*?(\- \[.*?\].*?\n))',
        re.MULTILINE
    )
    def add_note(m):
        return m.group(1) + f"- [{ts}] {note}\n"
    content = pattern.sub(add_note, content)
    _atomic_write(BUFFER_FILE, content)

    # 同时更新独立文件
    cp_file = CHECKPOINT_DIR / f"checkpoint_{checkpoint_id}.json"
    if cp_file.exists():
        try:
            data = json.loads(cp_file.read_text())
            data.setdefault("progress", []).append(f"[{ts}] {note}")
            _atomic_write(cp_file, data)
        except Exception:
            pass


def list_checkpoints(include_completed: bool = False) -> list[dict]:
    """列出所有检点"""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoints = []
    for f in sorted(CHECKPOINT_DIR.glob('checkpoint_*.json'), key=lambda x: -x.stat().st_mtime):
        try:
            data = json.loads(f.read_text())
            if not include_completed and data.get("status") == "completed":
                continue
            checkpoints.append(data)
        except Exception:
            pass
    return checkpoints


def get_checkpoint(checkpoint_id: str) -> Optional[dict]:
    """获取特定检点"""
    cp_file = CHECKPOINT_DIR / f"checkpoint_{checkpoint_id}.json"
    if cp_file.exists():
        try:
            return json.loads(cp_file.read_text())
        except Exception:
            pass
    return None


def _update_snapshot(task: str, ts: str):
    """更新 SNAPSHOT 文件的进行中任务"""
    if not SNAPSHOT_FILE.exists():
        return
    try:
        content = _read_file(SNAPSHOT_FILE)

        # 更新进行中任务（简单替换）
        import re
        pattern = re.compile(r'(\*\*🔸 进行中\*\*: )[^\n]+')
        if pattern.search(content):
            content = pattern.sub(rf'\g<1>{task} [{ts}]', content)
        else:
            # 追加到进行中 section
            content += f"\n- **🔸 进行中**: {task} [{ts}]"

        # 更新时间戳
        content = re.sub(
            r'# 最后更新时间：[\d\-: ]+',
            f'# 最后更新时间：{ts}',
            content
        )
        _atomic_write(SNAPSHOT_FILE, content)
    except Exception:
        pass


def needs_checkpoint(task: str, confidence: float = None) -> bool:
    """
    快速判断任务是否需要 checkpoint
    基于关键词检测
    """
    task_lower = task.lower()
    DANGEROUS = [r'删除', r'rm\s', r'drop\s', r'destroy', r'sudo\s',
                  r'systemctl\s+(stop|restart)', r'docker\s+rm', r'crontab\s+-r']
    COMPLEX   = [r'重构', r'迁移', r'并行', r'subagent', r'多步骤',
                  r'架构', r'系统设计']
    NEED_VERIFY = [r'安全', r'权限', r'发布', r'上线', r'deploy']

    if confidence is not None and confidence < 0.6:
        return True
    if any(re.search(p, task_lower) for p in DANGEROUS):
        return True
    if any(re.search(p, task_lower) for p in COMPLEX):
        return True
    return False


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse, json

    parser = argparse.ArgumentParser(description='Pre-checkpoint 管理系统')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('create', help='创建预检点')
    p.add_argument('task', help='任务描述')
    p.add_argument('plan', help='计划描述')
    p.add_argument('--confidence', '-c', type=int, default=5, help='置信度 1-10')
    p.add_argument('--steps', '-s', type=int, default=0, help='预计步数')
    p.add_argument('--subagents', help='子 agent（逗号分隔）')
    p.add_argument('--parallel', action='store_true', help='并行任务')

    p = sub.add_parser('complete', help='标记检点完成')
    p.add_argument('checkpoint_id', help='检点 ID')
    p.add_argument('result', nargs='?', default='完成', help='完成结果')

    p = sub.add_parser('progress', help='追加进度')
    p.add_argument('checkpoint_id', help='检点 ID')
    p.add_argument('note', help='进度描述')

    p = sub.add_parser('list', help='列出检点')
    p.add_argument('--all', action='store_true', help='包含已完成的')

    p = sub.add_parser('get', help='查看检点详情')
    p.add_argument('checkpoint_id', help='检点 ID')

    p = sub.add_parser('check', help='快速检查是否需要 checkpoint')
    p.add_argument('task', nargs='*', help='任务描述')
    p.add_argument('--confidence', '-c', type=float, help='置信度')

    args = parser.parse_args()

    if args.cmd == 'create':
        subs = [s.strip() for s in args.subagents.split(',')] if args.subagents else []
        cp_id = create_checkpoint(args.task, args.plan,
                                  confidence=args.confidence,
                                  steps=args.steps,
                                  subagents=subs,
                                  parallel=args.parallel)
        print(f"✅ 预检点已创建 [{cp_id[:6]}]")
        print(f"📋 任务: {args.task[:60]}")
        print(f"📊 置信度: {args.confidence}/10")
        print(f"🔗 缓冲区: {BUFFER_FILE}")
        print(f"\n完成后运行:")
        print(f"  python3 pre_checkpoint.py complete {cp_id} '<结果>'")

    elif args.cmd == 'complete':
        ok = complete_checkpoint(args.checkpoint_id, args.result)
        print(f"{'✅' if ok else '❌'} 检点 [{args.checkpoint_id[:6]}] 已标记完成")

    elif args.cmd == 'progress':
        add_progress(args.checkpoint_id, args.note)
        print(f"✅ 进度已追加到 [{args.checkpoint_id[:6]}]")

    elif args.cmd == 'list':
        checkpoints = list_checkpoints(include_completed=args.all)
        print(f"📋 预检点列表 ({len(checkpoints)} 个)\n")
        for cp in checkpoints:
            status_icon = "⏳" if cp.get("status") == "in_progress" else "✅"
            print(f"  {status_icon} [{cp['id'][:6]}] {cp.get('task','')[:50]}")
            print(f"     置信度: {cp.get('confidence',0)} | 状态: {cp.get('status')} | 创建: {cp.get('created_at','')[:10]}")
            if cp.get('progress'):
                print(f"     进度: {cp['progress'][-1][:60]}")
            print()

    elif args.cmd == 'get':
        cp = get_checkpoint(args.checkpoint_id)
        if cp:
            print(f"🔍 检点详情 [{cp['id'][:6]}]")
            print(f"  任务: {cp.get('task')}")
            print(f"  计划: {cp.get('plan')}")
            print(f"  置信度: {cp.get('confidence')}/10")
            print(f"  状态: {cp.get('status')}")
            print(f"  创建: {cp.get('created_at')}")
            if cp.get('progress'):
                print(f"  进度记录:")
                for p in cp['progress']:
                    print(f"    - {p}")
        else:
            print(f"❌ 未找到检点: {args.checkpoint_id}")

    elif args.cmd == 'check':
        task = ' '.join(args.task) if args.task else ""
        conf = args.confidence
        needed = needs_checkpoint(task, conf)
        print(f"{'🚨 需要 checkpoint' if needed else '✅ 普通任务'} | {task[:60]}")

    else:
        parser.print_help()
