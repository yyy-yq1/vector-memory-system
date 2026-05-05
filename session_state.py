#!/usr/bin/env python3
"""
SESSION-STATE.md 管理器 v1.0
WAL (Write-Ahead Log) 协议实现 - AI 的"热内存"

每次响应前写入，保存当前工作状态，崩溃/compact后恢复现场

WAL 触发条件（优先级从高到低）：
  1. 用户给出具体事实/偏好/决定 → 立即写入 SESSION-STATE.md → 再响应
  2. 用户纠正 AI → 立即写入 → 再响应
  3. 任务完成/关键里程碑 → 写入 → 再响应
  4. 每轮对话结束（自动追加）

文件位置：~/.openclaw/workspace/SESSION-STATE.md
"""
import sys
import datetime
import re
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
STATE_FILE = WORKSPACE / 'SESSION-STATE.md'

TEMPLATE = """# SESSION-STATE.md — Active Working Memory

This file is the agent's "RAM" — survives compaction and context loss.
Write BEFORE responding, not after.

## Current Task
{current_task}

## Key Context
{key_context}

## Pending Actions
{pending_actions}

## Recent Decisions
{recent_decisions}

## User Preferences (Live)
{user_preferences}

## Important Facts
{important_facts}

---
*Last updated: {timestamp}*
"""


def _default_state() -> dict:
    return {
        "current_task": "[None]",
        "key_context": "[None]",
        "pending_actions": "- [ ] None",
        "recent_decisions": "- [None]",
        "user_preferences": "- [None]",
        "important_facts": "- [None]",
        "timestamp": datetime.datetime.now().isoformat(),
    }


def _parse_state(content: str) -> dict:
    """从现有文件内容解析状态"""
    state = _default_state()
    lines = content.split('\n')
    current_section = None

    for line in lines:
        stripped = line.strip()
        # 检测章节
        if stripped.startswith('## Current Task'):
            current_section = 'current_task'
        elif stripped.startswith('## Key Context'):
            current_section = 'key_context'
        elif stripped.startswith('## Pending Actions'):
            current_section = 'pending_actions'
        elif stripped.startswith('## Recent Decisions'):
            current_section = 'recent_decisions'
        elif stripped.startswith('## User Preferences'):
            current_section = 'user_preferences'
        elif stripped.startswith('## Important Facts'):
            current_section = 'important_facts'
        elif stripped.startswith('---') or stripped.startswith('*Last updated'):
            current_section = None
        elif current_section and stripped and not stripped.startswith('#'):
            # 追加内容行
            existing = state[current_section]
            if existing == f"[{current_section.replace('_', ' ').title()}]" or existing == "[None]":
                state[current_section] = stripped
            else:
                state[current_section] += "\n" + stripped

    return state


def read() -> dict:
    """读取当前状态"""
    if not STATE_FILE.exists():
        return _default_state()
    content = STATE_FILE.read_text(encoding='utf-8')
    return _parse_state(content)


def update(
    current_task: Optional[str] = None,
    key_context: Optional[str] = None,
    pending_actions: Optional[str] = None,
    recent_decisions: Optional[str] = None,
    user_preferences: Optional[str] = None,
    important_facts: Optional[str] = None,
    append_decision: Optional[str] = None,
    append_preference: Optional[str] = None,
    append_fact: Optional[str] = None,
    append_action: Optional[str] = None,
) -> bool:
    """
    更新 SESSION-STATE.md（原子写）

    推荐用法（WAL协议）：
        # 用户给出具体信息 → 先写入，再响应
        session_state.update(append_fact="用户偏好: 使用简体中文")
        # 用户做决定 → 先写入，再响应
        session_state.update(append_decision="决定用 Qwen3-Embedding-4B")
        # 任务完成 → 先写入，再响应
        session_state.update(current_task="[完成]")
    """
    state = read()

    def replace(state_key, new_val):
        existing = state.get(state_key, '')
        if new_val is not None:
            state[state_key] = new_val
        return state

    if current_task is not None:
        state['current_task'] = current_task
    if key_context is not None:
        state['key_context'] = key_context
    if pending_actions is not None:
        state['pending_actions'] = pending_actions
    if recent_decisions is not None:
        state['recent_decisions'] = recent_decisions
    if user_preferences is not None:
        state['user_preferences'] = user_preferences
    if important_facts is not None:
        state['important_facts'] = important_facts

    # 追加模式（不清空，只追加）
    if append_decision:
        existing = state['recent_decisions']
        if existing == '[None]' or existing == '- [None]':
            state['recent_decisions'] = f"- {append_decision}"
        else:
            state['recent_decisions'] += f"\n- {append_decision}"

    if append_preference:
        existing = state['user_preferences']
        if existing == '[None]' or existing == '- [None]':
            state['user_preferences'] = f"- {append_preference}"
        else:
            state['user_preferences'] += f"\n- {append_preference}"

    if append_fact:
        existing = state['important_facts']
        if existing == '[None]' or existing == '- [None]':
            state['important_facts'] = f"- {append_fact}"
        else:
            state['important_facts'] += f"\n- {append_fact}"

    if append_action:
        existing = state['pending_actions']
        if existing == '- [ ] None' or existing == '[None]':
            state['pending_actions'] = f"- [ ] {append_action}"
        else:
            state['pending_actions'] += f"\n- [ ] {append_action}"

    state['timestamp'] = datetime.datetime.now().isoformat()

    # 原子写（先写临时文件，再 rename）
    content = TEMPLATE.format(**state)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.rename(STATE_FILE)

    # 同时同步到 SessionStore（L1 ↔ SessionStore 双写）
    try:
        import asyncio
        _sync_to_session_store(
            current_task=state.get('current_task'),
            append_decision=append_decision,
            append_action=append_action,
        )
    except Exception:
        pass  # SessionStore 不可用不影响主流程

    return True


def _sync_to_session_store(
    current_task: str = None,
    append_decision: str = None,
    append_action: str = None,
):
    """
    将 SESSION-STATE.md 的变更同步到 SessionStore
    双写保证：SESSION-STATE.md 是 AI 的热内存，
    SessionStore 是结构化持久化（可被 L10 查询）
    """
    try:
        from session_store import get_store
        store = get_store()
        loop = asyncio.get_event_loop()

        if append_decision:
            try:
                loop.run_until_complete(
                    store.save_decision(
                        append_decision[:80],
                        append_decision,
                        source="SESSION-STATE",
                    )
                )
            except Exception:
                pass

        if append_action and store:
            try:
                loop.run_until_complete(
                    store.save_task(
                        append_action[:100],
                        "pending",
                        progress=None,
                        details=f"来源: SESSION-STATE",
                    )
                )
            except Exception:
                pass

        if current_task and current_task not in ('[None]', '[Completed]'):
            try:
                loop.run_until_complete(
                    store.save_snapshot("L1_state", {
                        "task": current_task,
                        "timestamp": datetime.datetime.now().isoformat(),
                    })
                )
            except Exception:
                pass
    except Exception:
        pass  # 完全降级，不影响主流程


def mark_task_complete(task_desc: str = ""):
    """标记当前任务完成"""
    update(current_task=f"[Completed] {task_desc}" if task_desc else "[Completed]")


def append_key_fact(fact: str):
    """追加重要事实（WAL 协议：响应前调用）"""
    update(append_fact=fact)


def append_decision(decision: str):
    """追加决定（WAL 协议：响应前调用）"""
    update(append_decision=decision)


def append_preference(preference: str):
    """追加用户偏好（WAL 协议：响应前调用）"""
    update(append_preference=preference)


def init():
    """初始化 SESSION-STATE.md（如果不存在）"""
    if not STATE_FILE.exists():
        state = _default_state()
        STATE_FILE.write_text(TEMPLATE.format(**state), encoding='utf-8')


def clear():
    """清空并重置状态（会话结束时调用）"""
    state = _default_state()
    STATE_FILE.write_text(TEMPLATE.format(**state), encoding='utf-8')


def get_current_task() -> str:
    """快速获取当前任务描述"""
    return read().get('current_task', '[None]')


def get_recent_decisions() -> list[str]:
    """获取最近的决策列表"""
    content = read().get('recent_decisions', '')
    decisions = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('-') and line != '- [None]':
            decisions.append(line[1:].strip())
    return decisions


# CLI
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='SESSION-STATE.md 管理器')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('read', help='读取当前状态')
    p = sub.add_parser('init', help='初始化')
    p = sub.add_parser('clear', help='清空状态')

    p = sub.add_parser('update', help='更新状态')
    p.add_argument('--task')
    p.add_argument('--context')
    p.add_argument('--append-fact')
    p.add_argument('--append-decision')
    p.add_argument('--append-preference')
    p.add_argument('--append-action')

    args = parser.parse_args(sys.argv[2:] if len(sys.argv) > 2 else [])

    if args.cmd == 'read':
        state = read()
        for k, v in state.items():
            print(f"  {k}: {v[:80]}")
    elif args.cmd == 'init':
        init()
        print("✅ SESSION-STATE.md 已初始化")
    elif args.cmd == 'clear':
        clear()
        print("✅ 状态已清空")
    elif args.cmd == 'update':
        update(
            current_task=args.task,
            key_context=args.context,
            append_fact=args.append_fact,
            append_decision=args.append_decision,
            append_preference=args.append_preference,
            append_action=args.append_action,
        )
        print("✅ 已更新")
    else:
        # 默认：显示状态摘要
        state = read()
        print(f"📋 当前任务: {state['current_task']}")
        print(f"⏳ 待办: {state['pending_actions'][:100]}")
        print(f"🕐 更新于: {state['timestamp']}")
