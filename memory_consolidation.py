#!/usr/bin/env python3
"""
记忆整合器 v1.0
会话结束时的自动整合流程：

1. 读取 SESSION-STATE.md（热内存）
2. 提取关键信息 → 写入 L2 文件 + L4 Qdrant
3. 标记会话中的重要记忆强化
4. 运行归档扫描（遗忘低分记忆）
5. 清空 SESSION-STATE.md（准备下一会话）

触发时机：
  - cron 每日 23:00 自动运行
  - 或每次 extract_memories.py 完成后顺便运行
"""
import sys
import datetime
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory_consts import MEMORY_DIR, MEMORY_ARCHIVE_DIR
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state
from memory_api import capture
from session_state import read as read_session_state, clear as clear_session_state
from memory_reinforcement import archive_low_score_memories, scan_for_forgetting


# ─────────────────────────────────────────────────────────────
# 从 SESSION-STATE 提取并固化记忆
# ─────────────────────────────────────────────────────────────

def _extract_decisions(state: dict) -> list[str]:
    """从 recent_decisions 提取决策"""
    content = state.get('recent_decisions', '')
    decisions = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('-') and line not in ('- [None]', '- None'):
            decision = line[1:].strip()
            if decision and decision != '[None]':
                decisions.append(decision)
    return decisions


def _extract_preferences(state: dict) -> list[str]:
    """从 user_preferences 提取偏好"""
    content = state.get('user_preferences', '')
    prefs = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('-') and line not in ('- [None]', '- None'):
            pref = line[1:].strip()
            if pref and pref != '[None]':
                prefs.append(pref)
    return prefs


def _extract_facts(state: dict) -> list[str]:
    """从 important_facts 提取事实"""
    content = state.get('important_facts', '')
    facts = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('-') and line not in ('- [None]', '- None'):
            fact = line[1:].strip()
            if fact and fact != '[None]':
                facts.append(fact)
    return facts


def _extract_task(state: dict) -> str:
    """获取当前任务描述"""
    task = state.get('current_task', '[None]')
    return task if task and task != '[None]' else ''


def consolidate_session_state() -> dict:
    """
    将会话状态固化为长期记忆
    返回 {"decisions": N, "preferences": N, "facts": N, "task": str}
    """
    state = read_session_state()
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    result = {"decisions": 0, "preferences": 0, "facts": 0, "task": ""}

    task = _extract_task(state)
    if task:
        result["task"] = task
        capture('event', f"会话任务: {task[:50]}", task, context=f"source:session_state date:{today}")

    # 决策
    decisions = _extract_decisions(state)
    for d in decisions:
        if len(d) > 5:
            capture('event', f"决策: {d[:50]}", d, context=f"source:session_state date:{today}")
            result["decisions"] += 1

    # 偏好
    preferences = _extract_preferences(state)
    for p in preferences:
        if len(p) > 5:
            capture('practice', f"偏好: {p[:50]}", p, context=f"source:session_state date:{today}")
            result["preferences"] += 1

    # 事实
    facts = _extract_facts(state)
    for f in facts:
        if len(f) > 5:
            capture('practice', f"事实: {f[:50]}", f, context=f"source:session_state date:{today}")
            result["facts"] += 1

    return result


# ─────────────────────────────────────────────────────────────
# 会话总结增量写入
# ─────────────────────────────────────────────────────────────

def write_daily_session_summary(session_key: str, user_query: str, assistant_summary: str):
    """
    将每个会话的摘要写入当日日志
    session_key: 会话标识符
    user_query: 用户提问摘要
    assistant_summary: 助手回复摘要
    """
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    daily_log = MEMORY_DIR / f"{today}.md"

    session_block = f"""
---
## 会话: {session_key[:20]}

**用户**: {user_query[:200]}

**助手摘要**: {assistant_summary[:300]}

---
"""

    if daily_log.exists():
        existing = daily_log.read_text(encoding='utf-8')
        if session_key not in existing:
            daily_log.write_text(existing + session_block, encoding='utf-8')
    else:
        daily_log.write_text(f"# {today} 日志\n{session_block}", encoding='utf-8')


# ─────────────────────────────────────────────────────────────
# 每周归档扫描
# ─────────────────────────────────────────────────────────────

def weekly_forgetting_scan() -> dict:
    """
    每周执行一次低分记忆归档
    """
    print("🧠 执行每周遗忘扫描...")
    return archive_low_score_memories(threshold=ARCHIVE_THRESHOLD)


# ─────────────────────────────────────────────────────────────
# 完整整合流程
# ─────────────────────────────────────────────────────────────

def run_full_consolidation(clear_state: bool = True) -> dict:
    """
    完整整合流程：
    1. 固化 SESSION-STATE.md
    2. 扫描遗忘
    3. （可选）清空 SESSION-STATE.md
    """
    print("🚀 开始记忆整合...")

    # 1. 固化会话状态
    consolidation = consolidate_session_state()
    print(f"  📋 固化: {consolidation['decisions']} 决策, {consolidation['preferences']} 偏好, {consolidation['facts']} 事实")

    # 2. 遗忘归档
    archive_result = archive_low_score_memories()
    print(f"  🗄️  归档: {archive_result['archived']} 条低分记忆")

    # 3. 清空热内存
    if clear_state:
        clear_session_state()
        print("  🧹 SESSION-STATE.md 已清空（准备下一会话）")

    return {
        "consolidation": consolidation,
        "archive": archive_result,
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='记忆整合器')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('run', help='运行完整整合流程')
    p.add_argument('--keep-state', action='store_true', help='不清空 SESSION-STATE.md')

    p = sub.add_parser('scan', help='只扫描遗忘情况（不归档）')
    p.add_argument('--n', type=int, default=30)

    p = sub.add_parser('archive', help='执行归档')

    p = sub.add_parser('status', help='显示状态摘要')

    args = parser.parse_args()

    if args.cmd == 'run':
        result = run_full_consolidation(clear_state=not args.keep_state)
        print(f"\n✅ 整合完成")
    elif args.cmd == 'scan':
        from memory_reinforcement import scan_for_forgetting, ARCHIVE_THRESHOLD
        cands = scan_for_forgetting(n_samples=args.n)
        print(f"🔍 扫描 {len(cands)} 条记忆，阈值={ARCHIVE_THRESHOLD}\n")
        for c in cands:
            marker = " ← 归档" if c['fluid_score'] < ARCHIVE_THRESHOLD else ""
            bar = "█" * int(c['fluid_score'] * 20)
            print(f"  [{c['fluid_score']:.3f}] {bar}{marker}")
            print(f"        [{c['type']}] 访问{c['access_count']}次 | {c['days_passed']}天 | {c['text']}")
    elif args.cmd == 'archive':
        r = archive_low_score_memories()
        print(f"✅ 归档 {r['archived']} 条，跳过 {r['skipped']} 条")
    elif args.cmd == 'status':
        state = read_session_state()
        print("📋 SESSION-STATE.md 摘要:")
        print(f"  当前任务: {state.get('current_task','')}")
        decisions = _extract_decisions(state)
        print(f"  决策数: {len(decisions)}")
        prefs = _extract_preferences(state)
        print(f"  偏好数: {len(prefs)}")
        facts = _extract_facts(state)
        print(f"  事实数: {len(facts)}")
        print(f"  更新时间: {state.get('timestamp','')}")
    else:
        print("用法: memory_consolidation.py [run|scan|archive|status]")
