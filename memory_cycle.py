#!/usr/bin/env python3
"""
记忆系统周期协调器 v1.0
=========================

将 10 层记忆系统整合为统一的自动运行周期：

  每 30 分钟（后台）
    ├─ L00 → L02: session_transcript_extractor  (原始 → 归档)
    ├─ L02 → L03+L04: extract_memories --llm    (提取 → 结构化 + 向量)
    └─ L06: 工具模式注册（L1 WAL 中的命令历史）

  每 6 小时
    └─ L04 → L09: memory_reinforcement scan      (低分归档)

  每日 23:00（睡前整合）
    ├─ L01 → L07: memory_consolidation           (蒸馏核心记忆)
    ├─ L01 → L02: L1 快照写入日志
    └─ L05: LLM 跨记忆规律提取

  每日 03:00（数据整理）
    └─ L03 压缩 + 去重

统一入口：
  python3 memory_cycle.py run --mode <30min|6h|daily|3am|full>

相比分散的 7 个 cron 任务，整合后只有 4 个周期事件。
"""
import sys
import os
import datetime
import json
from pathlib import Path

SKILL_DIR = Path(__file__).parent
WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
sys.path.insert(0, str(SKILL_DIR))

# ─────────────────────────────────────────────────────────────
# 状态记录（防止重复执行）
# ─────────────────────────────────────────────────────────────

STATE_FILE = MEMORY_DIR / '.cycle_state.json'

def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_30min": None, "last_6h": None, "last_daily": None, "last_3am": None}

def _save_state(state: dict):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _should_run(state: dict, key: str, interval_hours: float) -> bool:
    last = state.get(key)
    if not last:
        return True
    last_dt = datetime.datetime.fromisoformat(last)
    elapsed = (datetime.datetime.now() - last_dt).total_seconds() / 3600
    return elapsed >= interval_hours

# ─────────────────────────────────────────────────────────────
# 各周期执行函数
# ─────────────────────────────────────────────────────────────

def run_30min(quiet: bool = False) -> bool:
    """每 30 分钟：L00→L02 + L02→L03/L04 + L06 工具注册"""
    state = _load_state()
    if not _should_run(state, 'last_30min', 0.5):
        return False

    results = []

    # L00 → L02: session transcript extractor
    sys.path.insert(0, str(SKILL_DIR))
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'session_transcript_extractor.py'), '--hours', '24'],
            capture_output=True, text=True, timeout=120
        )
        results.append(f"L02: {r.stdout.strip()[:100]}" if r.returncode == 0 else f"L02 ERR: {r.stderr[:80]}")
    except Exception as e:
        results.append(f"L02 ERR: {e}")

    # L02 → L03+L04: MiniMax LLM extract
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'extract_memories.py'), '--llm', '--days', '2'],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode == 0:
            # Parse the count from output
            lines = r.stdout.strip().split('\n')
            results.append(f"L03+L04: {lines[-1][:100]}" if lines else 'done')
        else:
            results.append(f"L03+L04 ERR: {r.stderr[:80]}")
    except Exception as e:
        results.append(f"L03+L04 ERR: {e}")

    state['last_30min'] = datetime.datetime.now().isoformat()
    _save_state(state)

    if not quiet:
        print(f"[30min @ {datetime.datetime.now().strftime('%H:%M')}] " + " | ".join(results))

    # Brain 主动预判检查
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'proactive_check.py')],
            capture_output=True, text=True, timeout=30, cwd=str(SKILL_DIR)
        )
        if r.returncode == 0 and r.stdout.strip():
            if not quiet:
                for line in r.stdout.strip().split('\n')[:5]:
                    print(f"  [proactive] {line}")
    except Exception:
        pass

    return True


def run_6h(quiet: bool = False) -> bool:
    """每 6 小时：L04 低分归档到 L09"""
    state = _load_state()
    if not _should_run(state, 'last_6h', 6.0):
        return False

    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'memory_reinforcement.py'), 'scan'],
            capture_output=True, text=True, timeout=300
        )
        output = r.stdout.strip()[:150] if r.stdout else r.stderr[:80]
    except Exception as e:
        output = str(e)

    state['last_6h'] = datetime.datetime.now().isoformat()
    _save_state(state)

    if not quiet:
        print(f"[6h @ {datetime.datetime.now().strftime('%H:%M')}] L04→L09: {output}")
    return True


def run_daily(quiet: bool = False) -> bool:
    """每日 23:00：L01→L07 蒸馏 + L02 日志 + L05 规律提取"""
    state = _load_state()
    if not _should_run(state, 'last_daily', 24.0):
        return False

    results = []

    # L01 WAL 快照写入 L02 日志
    try:
        from memory_layers import l1_snapshot
        snapshot = l1_snapshot()
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        log_file = MEMORY_DIR / f"daily_{today}.md"
        existing = log_file.read_text() if log_file.exists() else ""
        header = f"# 每日记忆汇总 {today}\n\n## L01 工作内存快照\n{snapshot}\n"
        log_file.write_text(header + "\n" + existing, encoding='utf-8')
        results.append(f"L01→L02: 快照已写入 {log_file.name}")
    except Exception as e:
        results.append(f"L01→L02 ERR: {e}")

    # L01 → L07: 蒸馏核心记忆
    try:
        from memory_layers import l7_distill
        n = l7_distill()
        results.append(f"L07: 蒸馏 {n} 条核心记忆")
    except Exception as e:
        results.append(f"L07 ERR: {e}")

    # L05: LLM 跨记忆规律提取
    try:
        from memory_api import search
        from qdrant_store import search_memories
        # 取最近的高分记忆作为 LLM 规律发现的输入
        recent = search_memories("重要 决策 教训 偏好 错误 实践", n_results=15)
        if recent:
            summary_text = "\n".join([r.get('text','')[:100] for r in recent[:10]])
            # 调用 MiniMax LLM 提取跨记忆规律
            prompt = f"""你是记忆规律分析专家。以下是最近的记忆片段：

{summary_text}

请找出其中的：
1. 重复出现的主题或模式
2. 用户的偏好或习惯
3. 值得注意的决策或教训

用简洁的列表格式输出（3-5条）。"""
            try:
                import httpx
                config = json.loads(open(str(Path.home() / '.mmx/config.json')).read())
                api_key = config.get('api_key', os.environ.get('MINIMAX_API_KEY',''))
                if api_key:
                    r = httpx.post(
                        'https://api.minimaxi.com/v1/chat/completions',
                        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                        json={'model': 'MiniMax-M2.7', 'messages': [
                            {'role': 'system', 'content': '你是一个记忆规律分析专家。简洁输出，直接列出3-5条规律。'},
                            {'role': 'user', 'content': prompt}
                        ], 'temperature': 0.1, 'max_tokens': 1024}, timeout=60
                    )
                    if r.status_code == 200:
                        result = r.json()
                        reply = result.get('choices', [{}])[0].get('message', {}).get('content', '')[:200]
                        results.append(f"L05: {reply}")
                        # 写入 patterns 文件
                        pat_dir = MEMORY_DIR / 'patterns'
                        pat_dir.mkdir(exist_ok=True)
                        pat_file = pat_dir / f"{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
                        pat_file.write_text(f"# 记忆规律 @ {datetime.datetime.now().isoformat()}\n\n## 分析输入\n{summary_text[:500]}\n\n## LLM 分析结果\n{reply}\n", encoding='utf-8')
            except Exception as e:
                results.append(f"L05 LLM ERR: {e}")
    except Exception as e:
        results.append(f"L05 ERR: {e}")

    state['last_daily'] = datetime.datetime.now().isoformat()
    _save_state(state)

    if not quiet:
        ts = datetime.datetime.now().strftime('%H:%M')
        print(f"[daily @ {ts}] " + " | ".join(results))

    # Brain daily 健康检查
    try:
        import subprocess

        # 1. 配置快照检查
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'config_snapshot.py'), 'check'],
            capture_output=True, text=True, timeout=30, cwd=str(SKILL_DIR)
        )
        if r.returncode == 0 and 'changed' in r.stdout:
            if not quiet:
                print(f"  [brain] config changed — snapshot taken")

        # 2. 复盘触发检查
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'auto_review_trigger.py')],
            capture_output=True, text=True, timeout=30, cwd=str(SKILL_DIR)
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().split('\n')[:3]:
                if line.strip():
                    if not quiet:
                        print(f"  [brain] {line}")
    except Exception:
        pass

    return True


def run_3am(quiet: bool = False) -> bool:
    """每日 03:00：L03 压缩去重"""
    state = _load_state()
    if not _should_run(state, 'last_3am', 24.0):
        return False

    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'memory_api.py'), 'compress'],
            capture_output=True, text=True, timeout=300
        )
        output = r.stdout.strip()[:150] if r.stdout else r.stderr[:80]
    except Exception as e:
        output = str(e)

    state['last_3am'] = datetime.datetime.now().isoformat()
    _save_state(state)

    if not quiet:
        print(f"[3am] L03 compress: {output}")
    return True


# ─────────────────────────────────────────────────────────────
# 全量健康检查
# ─────────────────────────────────────────────────────────────

def system_health() -> dict:
    """十层系统健康状态总览"""
    from memory_layers import LAYER_META, get_memory_architecture_summary
    from memory_api import count as vector_count

    health = {
        "timestamp": datetime.datetime.now().isoformat(),
        "layers": {},
        "issues": [],
        "recommendations": []
    }

    # L04 向量数量
    try:
        n = vector_count()
        if isinstance(n, dict):
            health["layers"]["L04_vectors"] = n
            vec_n = n.get('vectors', 0)
        else:
            health["layers"]["L04_vectors"] = n
            vec_n = n if isinstance(n, int) else 0
        if vec_n < 50:
            health["issues"].append(f"L04 向量数量过少（{vec_n} < 50），记忆积累不足")
    except Exception as e:
        health["issues"].append(f"L04 Qdrant 连接失败: {e}")

    # L01 SESSION-STATE
    try:
        from memory_layers import l1_read
        state = l1_read()
        if state.get('current_task') in [None, '[None]', '']:
            health["layers"]["L01"] = "empty (正常，新会话）"
        else:
            health["layers"]["L01"] = state.get('current_task', '')[:60]
    except Exception as e:
        health["issues"].append(f"L01 读取失败: {e}")

    # L02 日志
    try:
        logs = list(MEMORY_DIR.glob('*.md'))
        log_count = len([f for f in logs if 'memory' not in f.name and 'procedural' not in f.name])
        health["layers"]["L02_daily_logs"] = log_count
    except Exception as e:
        health["issues"].append(f"L02 日志检查失败: {e}")

    # L03 结构化
    try:
        from memory_consts import MEMORY_TYPES
        type_counts = {}
        for t in MEMORY_TYPES:
            d = MEMORY_DIR / t
            type_counts[t] = len(list(d.glob('*.md'))) if d.exists() else 0
        health["layers"]["L03_typed"] = type_counts
    except Exception as e:
        health["issues"].append(f"L03 类型检查失败: {e}")

    # L06 工具注册
    try:
        from memory_layers import _load_procedural
        data = _load_procedural()
        n = len(data.get('tool_patterns', {}))
        health["layers"]["L06_tools"] = n
        if n == 0:
            health["recommendations"].append("L06 工具模式注册表为空，建议正常使用工具以积累模式")
    except Exception as e:
        health["issues"].append(f"L06 检查失败: {e}")

    # L09 归档
    try:
        archive_dir = WORKSPACE / 'memory_archive'
        n = len(list(archive_dir.glob('**/*.md'))) if archive_dir.exists() else 0
        health["layers"]["L09_archived"] = n
    except Exception as e:
        health["issues"].append(f"L09 检查失败: {e}")

    # 周期状态
    state = _load_state()
    health["cycle_state"] = {
        "last_30min": state.get('last_30min', '从未'),
        "last_6h": state.get('last_6h', '从未'),
        "last_daily": state.get('last_daily', '从未'),
        "last_3am": state.get('last_3am', '从未'),
    }

    return health


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='记忆系统周期协调器')
    parser.add_argument('mode', nargs='?', default='30min',
                        choices=['30min', '6h', 'daily', '3am', 'full', 'health', 'status'])
    parser.add_argument('--quiet', action='store_true', help='静默运行（用于 cron）')
    parser.add_argument('--force', action='store_true', help='强制执行（忽略时间检查）')

    args = parser.parse_args()

    if args.force:
        state = _load_state()
        state['last_30min'] = None
        state['last_6h'] = None
        state['last_daily'] = None
        state['last_3am'] = None
        _save_state(state)

    if args.mode == '30min':
        run_30min(quiet=args.quiet)

    elif args.mode == '6h':
        run_6h(quiet=args.quiet)

    elif args.mode == 'daily':
        run_daily(quiet=args.quiet)

    elif args.mode == '3am':
        run_3am(quiet=args.quiet)

    elif args.mode == 'full':
        # 全量检查（用于手动触发）
        print(f"🧠 记忆系统全量检查 @ {datetime.datetime.now().isoformat()}\n")
        run_30min(quiet=args.quiet)
        run_6h(quiet=args.quiet)
        run_daily(quiet=args.quiet)
        run_3am(quiet=args.quiet)
        print()
        h = system_health()
        for layer, status in h['layers'].items():
            if isinstance(status, dict):
                print(f"  {layer}:")
                for k, v in status.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {layer}: {status}")
        if h['issues']:
            print("\n⚠️ 问题:")
            for i in h['issues']:
                print(f"  - {i}")
        if h['recommendations']:
            print("\n💡 建议:")
            for r in h['recommendations']:
                print(f"  - {r}")

    elif args.mode == 'health':
        h = system_health()
        print(f"🧠 系统健康状态 @ {h['timestamp'][:19]}\n")
        for layer, status in h['layers'].items():
            if isinstance(status, dict):
                print(f"  {layer}:")
                for k, v in status.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {layer}: {status}")
        if h['issues']:
            print("\n⚠️ 问题:")
            for i in h['issues']: print(f"  - {i}")
        if h['recommendations']:
            print("\n💡 建议:")
            for r in h['recommendations']: print(f"  - {r}")

    elif args.mode == 'status':
        state = _load_state()
        print("📊 周期状态：")
        for k, v in state.items():
            print(f"  {k}: {v}")

        print("\n📋 10 层架构：")
        from memory_layers import LAYER_META
        for ln, meta in LAYER_META.items():
            print(f"  L{ln:02d} {meta['name']}: {meta['storage']}")
