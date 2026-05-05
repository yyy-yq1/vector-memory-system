#!/usr/bin/env python3
"""
记忆置信度评估 + 胶囊管理系统
=====================================

整合自 Brain-v1.1.8 的核心机制：

1. 置信度评估（Confidence Scoring）
   初始 0.8，按信号表动态调整：
   >0.8 → 直接执行
   0.6~0.8 → 执行+记录
   ≤0.6 → 双保险（快照+对抗验证）+ 强制记录

2. 胶囊成熟度系统（Capsule Maturity）
   raw → tested(连续2次成功) → stable(连续5次成功)
   任务完成后自动建议是否创建胶囊

3. Pre-checkpoint 快照
   危险操作（删除/发布/系统核心修改）前强制快照

使用方式：
  python3 memory_brain.py confidence "重构量化交易模块"
  python3 memory_brain.py capsule suggest "完成Docker部署"
  python3 memory_brain.py checkpoint "删除旧日志文件"
"""
import sys
import json
import re
import datetime
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
SKILL_DIR = Path(__file__).parent


# ─────────────────────────────────────────────────────────────
# 1. 置信度评估
# ─────────────────────────────────────────────────────────────

# 信号表（来自 Brain-v1.1.8 subagent-think-chain.js）
CONFIDENCE_SIGNALS = {
    # 负向信号（降低置信度）
    'dangerous': {
        'patterns': [r'删除', r'rm\s', r'delete', r'销毁', r'DROP\s', r'TRUNCATE'],
        'delta': -0.3,
        'reason': '不可逆操作'
    },
    'external': {
        'patterns': [r'发布', r'上线', r'deploy', r'推送', r'send.*email', r'发.*邮件', r'发.*消息'],
        'delta': -0.2,
        'reason': '对外操作，后果严重'
    },
    'core': {
        'patterns': [r'核心', r'gateway', r'config.*set', r'system', r'内核'],
        'delta': -0.2,
        'reason': '系统核心修改'
    },
    'security': {
        'patterns': [r'密码', r'secret', r'密钥', r'api.?key', r'token', r'私钥'],
        'delta': -0.2,
        'reason': '安全相关'
    },
    'complex': {
        'patterns': [r'重构', r'迁移', r'并行', r'分布式', r'refactor', r'migrate'],
        'delta': -0.1,
        'reason': '任务复杂，链路长易漂移'
    },
    'first_time': {
        'patterns': [r'首次', r'第一次', r'从来没', r'new.*task'],
        'delta': -0.2,
        'reason': '无先例可循'
    },
    # 正向信号（提升置信度）
    'high_success': {
        'patterns': [r'之前成功', r'做过', r'验证过', r'known.*good', r'proven'],
        'delta': +0.1,
        'reason': '历史成功率高'
    },
    'simple': {
        'patterns': [r'简单', r'直接', r'就行', r'easy', r'quick', r'simple'],
        'delta': +0.1,
        'reason': '任务明确简单'
    },
    'uncertain': {
        'patterns': [r'试试', r'可能', r'看看', r'maybe', r'perhaps', r'try.*this'],
        'delta': -0.1,
        'reason': '包含不确定性关键词'
    },
}

CAPABILITIES = {
    'bash': 0.8,
    'python': 0.9,
    'lark': 0.85,
    'feishu': 0.85,
    'git': 0.9,
    'docker': 0.85,
    'qdrant': 0.9,
    'openclaw': 0.85,
    'search': 0.8,
    'web': 0.75,
}


def confidence_check(task: str) -> dict:
    """
    置信度评估（来自 Brain-v1.1.8 信号表）

    返回：
      confidence: float (0-1)
      level: 'high' | 'medium' | 'low'
      signals: [{"signal": str, "delta": float, "reason": str}]
      recommendation: str
    """
    score = 0.8  # 初始置信度
    signals = []

    task_lower = task.lower()

    for sig_name, sig_info in CONFIDENCE_SIGNALS.items():
        for pattern in sig_info['patterns']:
            if re.search(pattern, task, re.IGNORECASE):
                score += sig_info['delta']
                signals.append({
                    'signal': sig_name,
                    'delta': sig_info['delta'],
                    'reason': sig_info['reason'],
                    'matched': re.findall(pattern, task, re.IGNORECASE)[0]
                })
                break  # 每个信号只计一次

    # 任务长度惩罚
    if len(task) > 200:
        score -= 0.1
        signals.append({
            'signal': 'long_task',
            'delta': -0.1,
            'reason': f'任务描述过长（{len(task)}字符），可能复杂'
        })

    # 钳制到 [0, 1]
    score = max(0.0, min(1.0, score))

    # 分级
    if score >= 0.7:
        level = 'high'
        recommendation = '✅ 直接执行'
    elif score >= 0.4:
        level = 'medium'
        recommendation = '⚠️ 执行 + 全程记录（写入决策日志）'
    else:
        level = 'low'
        recommendation = '🚨 双保险：先写快照（checkpoint）+ 执行 + 对抗验证'

    return {
        'task': task,
        'confidence': round(score, 2),
        'level': level,
        'signals': signals,
        'recommendation': recommendation,
        'timestamp': datetime.datetime.now().isoformat()
    }


# ─────────────────────────────────────────────────────────────
# 2. 胶囊成熟度系统
# ─────────────────────────────────────────────────────────────

CAPSULE_FILE = MEMORY_DIR / 'capsules.json'

def _load_capsules() -> dict:
    if CAPSULE_FILE.exists():
        try:
            with open(CAPSULE_FILE) as f:
                return json.load(f)
        except Exception:
            return {'capsules': [], 'successions': []}
    return {'capsules': [], 'successions': []}

def _save_capsules(data: dict):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    # 原子写入
    tmp = str(CAPSULE_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, CAPSULE_FILE)

CAPSULE_MATURITY = {
    'raw': {'threshold': 1, 'next': 'tested', 'desc': '首次成功，等待验证'},
    'tested': {'threshold': 2, 'next': 'stable', 'desc': '连续成功2次，建议使用'},
    'stable': {'threshold': 5, 'next': None, 'desc': '连续成功5次，默认复用'},
}

CAPSULE_PATTERNS = {
    r'飞书.*图片|图片.*飞书': {'type': 'skill', 'tool': 'lark', 'desc': '飞书图片发送'},
    r'搜索.*网页|网页.*搜索': {'type': 'skill', 'tool': 'search', 'desc': '网络搜索'},
    r'docker\s+ps|docker.*启动': {'type': 'skill', 'tool': 'docker', 'desc': 'Docker操作'},
    r'extract.*memory|memory.*extract': {'type': 'skill', 'tool': 'python', 'desc': '记忆提取'},
    r'qdrant.*search|向量.*搜索': {'type': 'skill', 'tool': 'qdrant', 'desc': '向量搜索'},
}


def capsule_suggest(task: str, result_summary: str, success: bool) -> dict:
    """
    任务完成后建议是否创建胶囊
    来自 Brain-v1.1.8 的 capsule-auto-suggest.sh 逻辑
    """
    data = _load_capsules()

    # 检测是否匹配已知模式
    matched = None
    for pattern, info in CAPSULE_PATTERNS.items():
        if re.search(pattern, task, re.IGNORECASE):
            matched = info
            break

    # 查找是否有匹配的胶囊
    existing = None
    for cap in data['capsules']:
        if matched and cap.get('tool') == matched.get('tool'):
            existing = cap
            break

    suggestion = {
        'task': task,
        'result_summary': result_summary[:100],
        'success': success,
        'matched_pattern': matched,
        'existing_capsule': None,
        'action': 'none',
        'maturity': None,
    }

    if matched:
        if existing:
            # 更新已有胶囊的成功计数
            if success:
                existing['success_count'] = existing.get('success_count', 0) + 1
                maturity_info = CAPSULE_MATURITY[existing.get('maturity', 'raw')]
                if existing['success_count'] >= maturity_info['threshold']:
                    if maturity_info['next']:
                        old_maturity = existing.get('maturity', 'raw')
                        existing['maturity'] = maturity_info['next']
                        suggestion['maturity_upgrade'] = f"{old_maturity} → {maturity_info['next']}"
                suggestion['action'] = 'updated'
            suggestion['existing_capsule'] = existing
        else:
            # 创建新胶囊
            if success:
                new_cap = {
                    'name': matched['desc'],
                    'tool': matched['tool'],
                    'type': matched['type'],
                    'task_pattern': task[:50],
                    'maturity': 'raw',
                    'success_count': 1,
                    'created': datetime.datetime.now().isoformat(),
                    'last_used': datetime.datetime.now().isoformat(),
                }
                data['capsules'].append(new_cap)
                suggestion['action'] = 'create'
                suggestion['new_capsule'] = new_cap

    # 记录 succession（用于追踪）
    if success:
        data['successions'].append({
            'task': task[:100],
            'timestamp': datetime.datetime.now().isoformat(),
            'matched_capsule': existing['name'] if existing else None
        })
        # 只保留最近50条
        data['successions'] = data['successions'][-50:]

    _save_capsules(data)
    return suggestion


def capsule_list() -> list:
    """列出所有胶囊"""
    data = _load_capsules()
    return data.get('capsules', [])


def capsule_get(name_or_tool: str) -> Optional[dict]:
    """根据名称或工具查找胶囊"""
    data = _load_capsules()
    for cap in data.get('capsules', []):
        if name_or_tool.lower() in cap.get('name', '').lower() or \
           name_or_tool.lower() in cap.get('tool', '').lower():
            return cap
    return None


# ─────────────────────────────────────────────────────────────
# 3. Pre-checkpoint 快照
# ─────────────────────────────────────────────────────────────

CHECKPOINT_DIR = MEMORY_DIR / 'checkpoints'

DANGEROUS_PATTERNS = [
    r'rm\s+', r'delete\s+', r'drop\s+', r'destroy',
    r'chmod\s+0', r'chown\s+root',
    r'sudo\s+', r'systemctl\s+stop', r'systemctl\s+restart',
    r'docker\s+rm', r'docker\s+rmi',
    r'crontab\s+-r', r'kill\s+-9',
    r'curl.*\|\s*bash', r'wget.*\|\s*bash',
    r'发.*邮件|发送.*邮件|publish.*pypi',
]

NEED_CHECKPOINT_PATTERNS = [
    r'删除', r'重启', r'停止', r'kill', r'stop',
    r'deploy', r'发布', r'上线',
    r'修改.*config', r'gateway.*restart',
    r'sql.*delete', r'sql.*drop',
]


def needs_checkpoint(task: str) -> bool:
    """判断任务是否需要 pre-checkpoint"""
    task_lower = task.lower()
    return (
        any(re.search(p, task_lower) for p in DANGEROUS_PATTERNS) or
        any(re.search(p, task_lower) for p in NEED_CHECKPOINT_PATTERNS)
    )


def pre_checkpoint(reason: str, context: str = '') -> Path:
    """
    创建 pre-checkpoint 快照
    返回快照文件路径
    """
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    cp_file = CHECKPOINT_DIR / f"checkpoint_{ts}.md"

    content = f"""# Pre-Checkpoint

**时间**: {datetime.datetime.now().isoformat()}
**原因**: {reason}

## 当前状态

{context or '(无上下文)'}

## 快照文件

{_snapshot_files()}

---
> ⚠️ 此快照为危险操作前的安全网。如执行后出现问题，对比此快照进行恢复。
"""
    cp_file.write_text(content, encoding='utf-8')
    return cp_file


def _snapshot_files() -> str:
    """快照关键文件"""
    snapshots = []
    key_files = [
        WORKSPACE / 'SESSION-STATE.md',
        WORKSPACE / 'MEMORY.md',
        WORKSPACE / 'USER.md',
        MEMORY_DIR / 'memory-store.json',
    ]
    for f in key_files:
        if f.exists():
            size = f.stat().st_size
            mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            snapshots.append(f"- {f.relative_to(WORKSPACE)}: {size} bytes, modified {mtime}")
    return '\n'.join(snapshots) if snapshots else '(无关键文件)'


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='记忆置信度评估 + 胶囊管理系统')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('confidence', help='置信度评估')
    p.add_argument('task', nargs='*', help='要评估的任务描述')
    p.add_argument('--json', action='store_true', help='JSON 输出')

    p = sub.add_parser('capsule', help='胶囊管理')
    p.add_argument('action', nargs='?', choices=['list', 'get', 'suggest', 'maturity'])
    p.add_argument('--name', help='胶囊名称或工具')
    p.add_argument('--task', help='任务描述（用于 suggest）')
    p.add_argument('--result', help='结果摘要（用于 suggest）')
    p.add_argument('--success', type=lambda x: x.lower() == 'true', default=True, help='是否成功')

    p = sub.add_parser('checkpoint', help='Pre-checkpoint 快照')
    p.add_argument('reason', nargs='*', help='快照原因')
    p.add_argument('--context', help='附加上下文')

    p = sub.add_parser('check', help='检查任务是否需要快照')
    p.add_argument('task', nargs='*', help='任务描述')

    args = parser.parse_args()

    if args.cmd == 'confidence':
        task = ' '.join(args.task) if args.task else input('输入任务描述: ')
        result = confidence_check(task)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n📊 置信度评估: {task[:60]}")
            print(f"   分数: {result['confidence']} ({result['level']})")
            if result['signals']:
                print(f"   信号:")
                for s in result['signals']:
                    print(f"     [{s['delta']:+.1f}] {s['reason']} (匹配: {s.get('matched','')})")
            print(f"   建议: {result['recommendation']}")

    elif args.cmd == 'capsule':
        if args.action == 'list' or args.action is None:
            capsules = capsule_list()
            print(f"🧪 胶囊列表: {len(capsules)} 个\n")
            for c in capsules:
                mat = c.get('maturity', 'raw')
                mat_emoji = {'raw': '🟡', 'tested': '🟢', 'stable': '🏆'}.get(mat, '⚪')
                print(f"  {mat_emoji} {c.get('name')} ({c.get('tool')}) - {mat} - 成功{c.get('success_count', 0)}次")
                print(f"      任务: {c.get('task_pattern', '')[:50]}")

        elif args.action == 'get':
            cap = capsule_get(args.name or '')
            if cap:
                print(f"🧪 胶囊: {cap.get('name')}")
                print(f"  工具: {cap.get('tool')}")
                print(f"  成熟度: {cap.get('maturity')}")
                print(f"  成功: {cap.get('success_count', 0)}次")
                print(f"  创建: {cap.get('created', '')[:10]}")
                print(f"  上次使用: {cap.get('last_used', '')[:10]}")
            else:
                print(f"❌ 未找到胶囊: {args.name}")

        elif args.action == 'suggest':
            if not args.task:
                print("❌ 需要 --task 参数")
            else:
                result = capsule_suggest(args.task, args.result or '', args.success)
                print(f"💡 胶囊建议:")
                print(f"  任务: {result['task'][:60]}")
                print(f"  操作: {result['action']}")
                if result.get('matched_pattern'):
                    print(f"  匹配模式: {result['matched_pattern']['desc']}")
                if result.get('maturity_upgrade'):
                    print(f"  升级: {result['maturity_upgrade']}")

        elif args.action == 'maturity':
            print("📊 胶囊成熟度定义:")
            for stage, info in CAPSULE_MATURITY.items():
                emoji = {'raw': '🟡', 'tested': '🟢', 'stable': '🏆'}.get(stage, '⚪')
                print(f"  {emoji} {stage}: 连续成功{info['threshold']}次 → {info['next'] or '无'} | {info['desc']}")

    elif args.cmd == 'checkpoint':
        reason = ' '.join(args.reason) if args.reason else input('快照原因: ')
        context = args.context or ''
        cp_file = pre_checkpoint(reason, context)
        print(f"✅ 快照已创建: {cp_file.name}")

    elif args.cmd == 'check':
        task = ' '.join(args.task) if args.task else input('输入任务: ')
        needed = needs_checkpoint(task)
        print(f"{'🚨 需要快照' if needed else '✅ 普通任务'} | {task[:60]}")

    else:
        parser.print_help()
