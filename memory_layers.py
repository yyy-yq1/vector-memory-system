#!/usr/bin/env python3
"""
十层记忆架构 v1.0
==================

设计原则：层数多不一定聪明，关键在于功能区分度 + 层间路由效率。
每层有明确的职责、访问频率、数据格式。层间有明确的上升/下降路径。

┌──────────────────────────────────────────────────────────────────┐
│                        10 层记忆架构                                │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  L0  Sensory Buffer    ← 原始数据（session transcript JSONL）      │
│  L1  Working Memory    ← SESSION-STATE.md（WAL 协议）              │
│  L2  Session Archive  ← 每日日志 + Session Handoff                │
│  L3  Typed Store     ← error/correction/practice/event/gap      │
│  L4  Semantic Vector ← Qdrant + Ebbinghaus 强化搜索               │
│  L5  Pattern Extract  ← LLM MiniMax 模式发现                     │
│  L6  Procedural Mem  ← 工具/命令模式注册表                       │
│  L7  Core Memory     ← MEMORY.md（蒸馏后的核心知识）              │
│  L8  Identity Model  ← SOUL.md + USER.md（人格+用户模型）         │
│  L9  Deep Archive    ← memory_archive/（低分记忆归宿）            │
│                                                                  │
│  ── Special ──                                                  │
│  L10 Cross-Layer Query ← 智能路由 + 层间推理                     │
└──────────────────────────────────────────────────────────────────┘

职责说明：
  L0: 原始 transcript，仅追加，写入后不再读
  L1: 当前会话状态，WAL 协议，响应前写入
  L2: 每日会话记录，session 切换时的 handoff 摘要
  L3: 结构化记忆，按类型分类，L4 向量双写
  L4: 语义向量搜索，强化机制，动态调整权重
  L5: LLM 模式提取，发现跨记忆的规律
  L6: 工具使用模式，成功/失败案例模式注册
  L7: 长期记忆核心，极少更新，极高权重
  L8: 人格定义 + 用户模型，主动更新
  L9: 超低分记忆归档，可恢复
  L10: 查询路由，根据问题类型选择最优层级组合
"""
import sys
import json
import datetime
import re
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────
# 层级定义
# ─────────────────────────────────────────────────────────────

LAYER_META = {
    0: {"name": "Sensory Buffer",   "storage": "transcript JSONL",  "ttl": "permanent"},
    1: {"name": "Working Memory",  "storage": "SESSION-STATE.md", "ttl": "session"},
    2: {"name": "Session Archive",  "storage": "memory/*.md",       "ttl": "30 days"},
    3: {"name": "Typed Store",     "storage": "memory/{type}/",   "ttl": "permanent"},
    4: {"name": "Semantic Vector",  "storage": "Qdrant",            "ttl": "Ebbinghaus"},
    5: {"name": "Pattern Extract", "storage": "memory/patterns/",  "ttl": "permanent"},
    6: {"name": "Procedural Mem",  "storage": "memory/skills/",   "ttl": "permanent"},
    7: {"name": "Core Memory",     "storage": "MEMORY.md",         "ttl": "permanent"},
    8: {"name": "Identity Model",  "storage": "SOUL.md + USER.md","ttl": "permanent"},
    9: {"name": "Deep Archive",     "storage": "memory_archive/",  "ttl": "forever"},
   10: {"name": "Cross-Layer Query", "storage": "query router",    "ttl": "realtime"},
}

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
SKILL_DIR = WORKSPACE / 'skills' / 'vector-memory-self-evolution'
SESSION_STATE_FILE = WORKSPACE / 'SESSION-STATE.md'


# ─────────────────────────────────────────────────────────────
# L1: Working Memory（WAL 协议）
# ─────────────────────────────────────────────────────────────

def l1_read() -> dict:
    """读取 L1 工作内存"""
    from session_state import read as ss_read
    return ss_read()


def l1_write(current_task=None, key_context=None, pending=None,
             append_decision=None, append_fact=None, append_pref=None) -> bool:
    """写入 L1 工作内存（WAL 协议：响应前写入）"""
    from session_state import update, append_decision as _app_dec, \
        append_fact as _app_fact, append_preference as _app_pref
    if append_decision:
        _app_dec(append_decision)
    if append_fact:
        _app_fact(append_fact)
    if append_pref:
        _app_pref(append_pref)
    if current_task or key_context or pending:
        update(current_task=current_task, key_context=key_context,
               pending_actions=pending)
    return True


def l1_snapshot() -> str:
    """生成 L1 当前状态的文本快照（用于 handoff）"""
    state = l1_read()
    lines = [
        f"# SESSION-STATE 快照 @ {state.get('timestamp','')}",
        f"当前任务: {state.get('current_task','')}",
        f"关键上下文: {state.get('key_context','')}",
        f"待办: {state.get('pending_actions','')}",
        "---",
        "近期决策:",
    ]
    for d in state.get('recent_decisions', '').split('\n'):
        if d.strip() and not d.startswith('- [None]'):
            lines.append(d)
    lines.append("---")
    lines.append("用户偏好:")
    for p in state.get('user_preferences', '').split('\n'):
        if p.strip() and not p.startswith('- [None]'):
            lines.append(p)
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# L2: Session Archive（每日日志 + Handoff）
# ─────────────────────────────────────────────────────────────

def l2_append_session_log(session_key: str, user_query: str, assistant_summary: str) -> Path:
    """追加会话到当日日志"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    log_file = MEMORY_DIR / f"{today}.md"
    entry = f"""

---
## 会话: {session_key[:30]}
**时间**: {datetime.datetime.now().isoformat()}
**用户**: {user_query[:200]}
**助手摘要**: {assistant_summary[:300]}
"""
    if log_file.exists():
        content = log_file.read_text(encoding='utf-8')
        if session_key not in content:
            log_file.write_text(content + entry, encoding='utf-8')
    else:
        log_file.write_text(f"# {today} 日志\n{entry}", encoding='utf-8')
    return log_file


def l2_write_handoff(from_session: str, to_session: str, snapshot: str) -> Path:
    """
    写入 session handoff 文件（防止会话切换时的记忆断层）
    来源：Triple-Layer Memory 的核心机制
    """
    handoff_dir = MEMORY_DIR / 'handoff'
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = handoff_dir / f"{from_session[:30]}_to_{to_session[:30]}.md"
    content = f"""# Session Handoff

**From**: {from_session}
**To**: {to_session}
**时间**: {datetime.datetime.now().isoformat()}

## 状态快照

{snapshot}

## 迁移原因

[会话切换 - 手动/自动/session满]

## 后续行动

- [ ] 继续执行未完成任务
- [ ] 验证上下文是否正确传递
"""
    handoff_file.write_text(content, encoding='utf-8')
    return handoff_file


def l2_read_latest_handoffs(n: int = 3) -> list[dict]:
    """读取最近 N 个 handoff 文件"""
    handoff_dir = MEMORY_DIR / 'handoff'
    if not handoff_dir.exists():
        return []
    files = sorted(handoff_dir.glob('*.md'), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    results = []
    for f in files:
        content = f.read_text(encoding='utf-8')
        results.append({
            'file': f.name,
            'content': content,
            'modified': datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        })
    return results


# ─────────────────────────────────────────────────────────────
# L3: Typed Store（结构化记忆）
# ─────────────────────────────────────────────────────────────

def l3_capture(typ: str, title: str, content: str, context: str = '', importance: float = 0.5) -> bool:
    """写入 L3 结构化记忆（同时触发 L4 向量）"""
    from memory_api import capture_error, capture_correction, capture_practice, \
        capture_event, capture as generic_capture
    if typ == 'error':
        capture_error(title, content, context, '')
    elif typ == 'correction':
        capture_correction(title, context, content, '')  # 复用：wrong→correct
    elif typ == 'practice':
        capture_practice(title, content, context)
    elif typ == 'event':
        capture_event(title, content, context)
    elif typ == 'gap':
        generic_capture('gap', title, content, context)
    else:
        generic_capture(typ, title, content, context)
    return True


# ─────────────────────────────────────────────────────────────
# L6: Procedural Memory（工具/命令模式注册）
# ─────────────────────────────────────────────────────────────

PROCEDURAL_FILE = MEMORY_DIR / 'procedural_mem.json'

def _load_procedural() -> dict:
    if PROCEDURAL_FILE.exists():
        with open(PROCEDURAL_FILE) as f:
            return json.load(f)
    return {"tool_patterns": {}, "command_patterns": {}, "skill_patterns": {}}


def _save_procedural(data: dict):
    PROCEDURAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCEDURAL_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def l6_register_tool_pattern(tool_name: str, pattern: str, result: str, success: bool):
    """
    注册工具使用模式（L6 Procedural Memory）
    成功案例：command_patterns[pattern] += success_count
    失败案例：command_patterns[pattern] += failure_count
    """
    data = _load_procedural()
    patterns = data.get('tool_patterns', {})

    if tool_name not in patterns:
        patterns[tool_name] = {"successes": 0, "failures": 0, "last_used": None, "examples": []}

    entry = {
        "pattern": pattern[:100],
        "result_preview": result[:80],
        "success": success,
        "timestamp": datetime.datetime.now().isoformat()
    }

    if success:
        patterns[tool_name]["successes"] += 1
    else:
        patterns[tool_name]["failures"] += 1

    patterns[tool_name]["last_used"] = datetime.datetime.now().isoformat()

    # 保留最近5个示例
    examples = patterns[tool_name].get("examples", [])
    examples.insert(0, entry)
    patterns[tool_name]["examples"] = examples[:5]

    data["tool_patterns"] = patterns
    _save_procedural(data)


def l6_get_tool_patterns(tool_name: str = None) -> dict:
    """获取工具模式注册表"""
    data = _load_procedural()
    if tool_name:
        return data.get("tool_patterns", {}).get(tool_name, {})
    return data.get("tool_patterns", {})


def l6_get_best_patterns(command_keyword: str) -> list[str]:
    """根据命令关键词获取最佳模式（成功率最高）"""
    data = _load_procedural()
    patterns = data.get("tool_patterns", {})
    results = []
    for tool, info in patterns.items():
        if command_keyword.lower() in tool.lower():
            total = info.get("successes", 0) + info.get("failures", 0)
            success_rate = info.get("successes", 0) / max(total, 1)
            results.append((tool, success_rate, total, info.get("examples", [])[:1]))
    results.sort(key=lambda x: -x[1])
    return results


# ─────────────────────────────────────────────────────────────
# L8: Identity Model（USER.md 动态更新）
# ─────────────────────────────────────────────────────────────

USER_FILE = WORKSPACE / 'USER.md'

L8_PREF_PATTERNS = [
    (re.compile(r'喜欢|偏好|倾向于|prefer', re.I), '偏好'),
    (re.compile(r'讨厌|不喜欢|不要|avoid|hate', re.I), '厌恶'),
    (re.compile(r'用\s*中文|说中文|chinese', re.I), '语言'),
    (re.compile(r'直接给|不要废话|简洁|concise', re.I), '沟通风格'),
]

def l8_update_from_text(text: str) -> dict:
    """
    从对话文本中提取用户偏好，更新 USER.md（L8 Identity Model）
    来自 triple-layer-memory 的质量门控思路：每条输出都打分
    """
    if not USER_FILE.exists():
        return {}

    content = USER_FILE.read_text(encoding='utf-8')
    updates = {}

    for pattern, category in L8_PREF_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            key = f"auto_{category}_{datetime.datetime.now().strftime('%Y%m%d%H%M')}"
            updates[key] = {"category": category, "matched": matches, "source": "auto_extract"}

    # 如果发现新偏好，追加到 USER.md
    if updates:
        new_entries = ["\n## 自动提取偏好（请人工确认）\n"]
        for key, info in updates.items():
            new_entries.append(f"- [{info['category']}] {', '.join(info['matched'])} (来源: {info['source']})")

        content += '\n' + '\n'.join(new_entries)
        USER_FILE.write_text(content, encoding='utf-8')

    return updates


def l8_get_user_model() -> dict:
    """获取用户模型（偏好字典）"""
    if not USER_FILE.exists():
        return {}
    content = USER_FILE.read_text(encoding='utf-8')
    prefs = {}
    for pattern, category in L8_PREF_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            prefs[category] = matches
    return prefs


# ─────────────────────────────────────────────────────────────
# L7: Core Memory（MEMORY.md 管理）
# ─────────────────────────────────────────────────────────────

MEMORY_MD = WORKSPACE / 'MEMORY.md'

def l7_distill(from_layers: list[int] = None) -> int:
    """
    从其他层蒸馏核心记忆到 MEMORY.md（L7）
    from_layers: 指定从哪些层提取，默认 [L3, L4, L5]
    每次蒸馏都会将高质量记忆精华写入 MEMORY.md
    """
    from memory_api import search
    from memory_reinforcement import search_with_fluid

    if from_layers is None:
        from_layers = [3, 4, 5]

    # 从 L4 向量库中提取最高分记忆（importance 高 + fluid_score 高）
    candidates = []
    if 4 in from_layers:
        results = search_with_fluid("核心知识 重要决策 关键教训", n_results=20)
        for r in results:
            if r.get('fluid_score', 0) > 0.5:
                candidates.append({
                    'text': r.get('text', '')[:200],
                    'layer': 'L4',
                    'score': r.get('fluid_score', 0),
                    'type': r.get('type', ''),
                })

    if not candidates:
        return 0

    # 按分数排序，取前5
    candidates.sort(key=lambda x: -x['score'])
    top = candidates[:5]

    if not top:
        return 0

    # 追加到 MEMORY.md
    timestamp = datetime.datetime.now().isoformat()
    entries = [f"\n\n## 蒸馏记忆 @ {timestamp}\n"]
    for c in top:
        entries.append(f"- [{c['type']}] (score={c['score']:.2f}) {c['text']}")

    content = MEMORY_MD.read_text() if MEMORY_MD.exists() else "# Memory\n"
    MEMORY_MD.write_text(content + '\n'.join(entries), encoding='utf-8')
    return len(top)


# ─────────────────────────────────────────────────────────────
# L10: Cross-Layer Query（智能路由）
# ─────────────────────────────────────────────────────────────

def l10_query(question: str, layers: list[int] = None) -> dict:
    """
    跨层智能查询
    根据问题类型自动选择最优层级组合

    路由规则：
      "用户偏好" → L8 (USER.md) + L1 (SESSION-STATE)
      "之前做过什么" → L2 (日志) + L4 (向量搜索)
      "最佳实践" → L3 (practices) + L6 (procedural)
      "我叫什么" → L8 (USER.md)
      "错误修复" → L3 (corrections) + L4
      "技术知识" → L4 + L5 (LLM提取)
      默认 → L1 + L4 + L7 联合查询
    """
    from memory_api import search
    from memory_reinforcement import search_with_fluid

    q_lower = question.lower()

    # 自动路由
    if any(k in q_lower for k in ['偏好', '喜欢', '讨厌', 'prefer', 'hate']):
        target_layers = [8, 1]
    elif any(k in q_lower for k in ['之前', '上次', '上次做', '曾经', 'last time']):
        target_layers = [2, 4]
    elif any(k in q_lower for k in ['怎么', '如何', '方法', 'how to', 'method']):
        target_layers = [3, 6, 4]
    elif any(k in q_lower for k in ['错误', 'bug', '失败', 'error', '修复']):
        target_layers = [3, 4]
    elif any(k in q_lower for k in ['我叫', '名字', 'who am i', 'my name']):
        target_layers = [8]
    elif any(k in q_lower for k in ['决策', '决定', 'decision']):
        target_layers = [1, 7, 4]
    else:
        target_layers = [1, 4, 7]

    if layers:
        target_layers = layers

    results_by_layer = {}

    # L1: SESSION-STATE
    if 1 in target_layers:
        state = l1_read()
        if any(state.values()):
            results_by_layer['L1'] = [{
                'text': f"当前任务: {state.get('current_task','')} | 决策: {state.get('recent_decisions','')[:100]}",
                'score': 1.0
            }]

    # L2: Session logs
    if 2 in target_layers:
        from session_transcript_extractor import _extract_messages_from_transcript
        sessions_dir = Path('/root/.openclaw/agents/main/sessions')
        recent = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        l2_results = []
        for tf in recent:
            if 'checkpoint' in tf.name or 'trajectory' in tf.name:
                continue
            msgs = _extract_messages_from_transcript(tf)
            if msgs:
                user_msgs = [m['text'][:100] for m in msgs if m['role'] == 'user'][:3]
                l2_results.append({'text': f"最近会话: {' | '.join(user_msgs)}", 'score': 0.8})
        if l2_results:
            results_by_layer['L2'] = l2_results

    # L3+L4: Typed + Vector — 使用混合搜索（BM25 + Vector + RRF）
    if 4 in target_layers:
        from memory_hybrid import hybrid_search
        vec_results = hybrid_search(question, n_results=5)
        results_by_layer['L4'] = [{
            'text': r.get('text', '')[:150],
            'score': r.get('final_score', r.get('fluid_score', r.get('score', 0))),
            'type': r.get('type', ''),
            'method': r.get('method', 'vector')
        } for r in vec_results]

    # L6: Procedural
    if 6 in target_layers:
        data = _load_procedural()
        patterns = data.get('tool_patterns', {})
        tool_hints = []
        for tool, info in patterns.items():
            if any(k in question.lower() for k in tool.lower().split()):
                total = info.get('successes', 0) + info.get('failures', 0)
                sr = info.get('successes', 0) / max(total, 1)
                tool_hints.append({
                    'text': f"工具 {tool}: 成功率 {sr:.0%} ({total}次使用)",
                    'score': sr,
                    'examples': info.get('examples', [])[:1]
                })
        if tool_hints:
            results_by_layer['L6'] = tool_hints

    # L7: MEMORY.md
    if 7 in target_layers and MEMORY_MD.exists():
        content = MEMORY_MD.read_text()
        # 简单关键词匹配
        lines = [l for l in content.split('\n')
                 if any(k in l.lower() for k in question.lower().split()[:3])]
        if lines:
            results_by_layer['L7'] = [{'text': '\n'.join(lines[:5]), 'score': 0.9}]

    # L8: USER.md
    if 8 in target_layers:
        if USER_FILE.exists():
            content = USER_FILE.read_text()
            # 中文2-gram + 英文单词
            cn_words = re.findall(r'[\u4e00-\u9fff]{2,}', question)
            en_words = [w for w in re.findall(r'[a-zA-Z0-9]+', question) if len(w) >= 1]
            keywords = set(cn_words + en_words)
            lines = [l.strip() for l in content.split('\n')
                     if any(kw.lower() in l.lower() for kw in keywords)]
            if lines:
                results_by_layer['L8'] = [{'text': '\n'.join(lines[:8]), 'score': 1.0}]

    return {
        "question": question,
        "routed_layers": target_layers,
        "results": results_by_layer,
        "timestamp": datetime.datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# 全局架构报告
# ─────────────────────────────────────────────────────────────

def get_memory_architecture_summary() -> dict:
    """生成十层记忆架构的完整状态报告"""
    from memory_api import count

    l1_state = l1_read()
    l2_files = list(MEMORY_DIR.glob('*.md')) if MEMORY_DIR.exists() else []
    l3_counts = {}
    for t in ['error', 'correction', 'practice', 'event', 'gap']:
        d = MEMORY_DIR / t
        l3_counts[t] = len(list(d.glob('*.md'))) if d.exists() else 0

    l4_info = {}
    try:
        count()
    except:
        pass

    l6_procedural = _load_procedural()
    l8_prefs = l8_get_user_model()
    l9_archives = list((WORKSPACE / 'memory_archive').glob('**/*.md')) if (WORKSPACE / 'memory_archive').exists() else []

    return {
        "layers": LAYER_META,
        "L1_working_memory": l1_state.get('current_task', '[empty]'),
        "L2_daily_logs": len(l2_files),
        "L3_typed_entries": sum(l3_counts.values()),
        "L4_vector_count": "see memory_api count",
        "L6_tool_patterns": len(l6_procedural.get('tool_patterns', {})),
        "L8_user_prefs": l8_prefs,
        "L9_archived_count": len(l9_archives),
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='十层记忆架构管理器')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('summary', help='十层架构总览')
    p = sub.add_parser('layers', help='各层详细信息')
    p = sub.add_parser('l1', help='L1 工作内存状态')
    p = sub.add_parser('l6', help='L6 工具模式注册表')
    p = sub.add_parser('query', help='L10 跨层查询')
    p.add_argument('q', nargs='*', help='查询内容')
    p = sub.add_parser('handoff', help='L2 Handoff 记录')
    p = sub.add_parser('distill', help='L7 从其他层蒸馏核心记忆')

    args = parser.parse_args()

    if args.cmd == 'summary':
        print("🧠 十层记忆架构 v1.0\n")
        for layer_num, meta in LAYER_META.items():
            print(f"  L{layer_num:02d}  {meta['name']:<20} | {meta['storage']:<25} | {meta['ttl']}")

    elif args.cmd == 'layers':
        summary = get_memory_architecture_summary()
        print("🧠 十层记忆架构详情\n")
        for ln, meta in LAYER_META.items():
            print(f"L{ln:02d} {meta['name']}: {meta['storage']} ({meta['ttl']})")

    elif args.cmd == 'l1':
        state = l1_read()
        print("📋 L1 工作内存状态:")
        print(f"  当前任务: {state.get('current_task','')}")
        print(f"  上下文: {state.get('key_context','')}")
        print(f"  待办: {state.get('pending_actions','')}")
        print(f"  更新: {state.get('timestamp','')}")

    elif args.cmd == 'l6':
        data = _load_procedural()
        patterns = data.get('tool_patterns', {})
        print(f"🔧 L6 工具模式注册: {len(patterns)} 个工具\n")
        for tool, info in list(patterns.items())[:10]:
            total = info.get('successes', 0) + info.get('failures', 0)
            sr = info.get('successes', 0) / max(total, 1)
            print(f"  {tool}: 成功率 {sr:.0%} ({total}次) | 上次: {info.get('last_used','无')[:10]}")

    elif args.cmd == 'query':
        q = ' '.join(args.q) if args.q else "查询所有层级"
        result = l10_query(q)
        print(f"🔍 查询: {result['question']}")
        print(f"📡 路由: L{' + L'.join(map(str, result['routed_layers']))}\n")
        for layer, hits in result['results'].items():
            print(f"  {layer}:")
            for h in hits[:3]:
                print(f"    [{h.get('score',0):.2f}] {h.get('text','')[:100]}")

    elif args.cmd == 'handoff':
        handoffs = l2_read_latest_handoffs()
        print(f"🔗 L2 Handoff 记录: {len(handoffs)} 个\n")
        for h in handoffs:
            print(f"  {h['file']} @ {h['modified'][:10]}")

    elif args.cmd == 'distill':
        n = l7_distill()
        print(f"✅ L7 蒸馏完成: {n} 条记忆写入 MEMORY.md")

    else:
        summary = get_memory_architecture_summary()
        print("🧠 十层记忆架构 v1.0\n")
        print(f"  L1 工作内存: {summary['L1_working_memory'][:50]}")
        print(f"  L2 日志文件: {summary['L2_daily_logs']} 个")
        print(f"  L3 结构化条目: {summary['L3_typed_entries']} 个")
        print(f"  L6 工具模式: {summary['L6_tool_patterns']} 个")
        print(f"  L8 用户偏好: {summary['L8_user_prefs']}")
        print(f"  L9 归档记忆: {summary['L9_archived_count']} 条")
