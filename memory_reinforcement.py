#!/usr/bin/env python3
"""
记忆强化与遗忘机制 v1.0
融合 Fluid Memory 的艾宾浩斯遗忘曲线 + 我们的 Qdrant 高质量向量

核心公式：
  final_score = base_similarity * exp(-λ * days) + α * log(1 + access_count)

强化：每次被检索/使用时，access_count++，记忆更持久
遗忘：超过 THRESHOLD_SCORE 的记忆标记为"待归档"
归档：定期将低分记忆从向量库转移到 L3 归档文件

元数据存储：~/.openclaw/workspace/memory/.memory_metadata.json
"""
import sys
import json
import math
import datetime
import hashlib
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from memory_consts import MEMORY_DIR, MAX_CONTENT_LEN
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

METADATA_FILE = MEMORY_DIR / '.memory_metadata.json'
ARCHIVE_THRESHOLD = 0.10  # 最终得分低于此值 → 待归档
LAMBDA_DECAY = 0.03      # 遗忘速度系数（越小遗忘越慢）
ALPHA_BOOST = 0.25       # 强化力度系数


# ─────────────────────────────────────────────────────────────
# 元数据存储
# ─────────────────────────────────────────────────────────────

def _load_metadata() -> dict:
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            return json.load(f)
    return {"reinforcements": {}, "archive_candidates": []}


def _save_metadata(data: dict):
    with open(METADATA_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_meta_id(point_id: str) -> str:
    """从 point_id 获取元数据 key（point_id 的前16位）"""
    return point_id[:16]


def _get_reinforcement(point_id: str) -> dict:
    """获取某条记忆的强化状态"""
    data = _load_metadata()
    mid = _get_meta_id(point_id)
    return data.get('reinforcements', {}).get(mid, {
        "access_count": 0,
        "importance": 0.5,  # 默认中等重要性
        "last_accessed": None,
        "first_created": None,
        "is_archived": False,
    })


def _update_reinforcement(point_id: str, **kwargs):
    """更新记忆的强化状态（原子写）"""
    data = _load_metadata()
    mid = _get_meta_id(point_id)
    if 'reinforcements' not in data:
        data['reinforcements'] = {}

    current = data['reinforcements'].get(mid, {
        "access_count": 0,
        "importance": 0.5,
        "last_accessed": None,
        "first_created": None,
        "is_archived": False,
    })

    for key, val in kwargs.items():
        current[key] = val

    current['last_accessed'] = datetime.datetime.now().isoformat()
    data['reinforcements'][mid] = current
    _save_metadata(data)
    return current


def reinforce(point_id: str):
    """强化记忆（access_count++）"""
    current = _get_reinforcement(point_id)
    new_count = current.get('access_count', 0) + 1
    _update_reinforcement(point_id, access_count=new_count)


def reinforce_by_ids(point_ids: list[str]):
    """批量强化记忆"""
    for pid in point_ids:
        reinforce(pid)


def set_importance(point_id: str, importance: float):
    """设置记忆重要性（0.0-1.0）"""
    _update_reinforcement(point_id, importance=max(0.0, min(1.0, importance)))


# ─────────────────────────────────────────────────────────────
# Ebbinghaus 遗忘曲线评分
# ─────────────────────────────────────────────────────────────

def calculate_fluid_score(base_similarity: float, point_id: str) -> float:
    """
    计算最终得分 = 向量相似度 * 遗忘衰减 + 强化 boost
    """
    meta = _get_reinforcement(point_id)

    # 计算天数
    first_created = meta.get('first_created')
    if first_created:
        try:
            created = datetime.datetime.fromisoformat(first_created)
            days_passed = (datetime.datetime.now() - created).total_seconds() / 86400
        except:
            days_passed = 0
    else:
        days_passed = 0

    # 遗忘衰减
    decay = math.exp(-LAMBDA_DECAY * days_passed)

    # 强化 boost
    access_count = meta.get('access_count', 0)
    importance = meta.get('importance', 0.5)
    boost = ALPHA_BOOST * importance * math.log(1 + access_count)

    return (base_similarity * decay) + boost


# ─────────────────────────────────────────────────────────────
# 搜索时应用强化（re-rank）
# ─────────────────────────────────────────────────────────────

def search_with_fluid(query: str, n_results: int = 5, memory_type: str = None) -> list[dict]:
    """
    带艾宾浩斯强化机制的语义搜索
    1. Qdrant 语义召回 Top N×10（大幅超量，确保被强化的记忆能进入候选池）
    2. 对每条记忆计算 fluid_score
    3. 返回 re-rank 后的结果（强化过的记忆排名提前）
    """
    store = get_store()
    # 初始召回量 × 10，解决"强化记忆因初始相似度低而无法进入候选"的问题
    raw_results = store.search(query, n_results=n_results * 10, typ=memory_type)

    if not raw_results:
        return []

    # 计算每条记忆的最终得分
    scored = []
    for r in raw_results:
        point_id = r.get('id', '')
        fluid = calculate_fluid_score(r.get('score', 0), point_id)

        # 强化：这次搜索命中了它，access_count++
        if fluid > 0.05:  # 只强化有意义的记忆
            reinforce(point_id)

        r['fluid_score'] = round(fluid, 4)
        scored.append(r)

    # 按 fluid_score 降序排列
    scored.sort(key=lambda x: -x['fluid_score'])

    return scored[:n_results]


# ─────────────────────────────────────────────────────────────
# 遗忘扫描 + 归档
# ─────────────────────────────────────────────────────────────

def scan_for_forgetting(n_samples: int = 50) -> list[dict]:
    """
    扫描向量库，找出低分记忆（待归档候选）
    返回 [{point_id, text, fluid_score, days_passed}, ...]
    """
    try:
        store = get_store()
        # 召回一批记忆（按时间逆序，新记忆优先）
        all_results = store.search(".", n_results=n_samples, typ=None)
    except Exception as e:
        print(f"⚠️  扫描失败: {e}")
        return []

    candidates = []
    for r in all_results:
        point_id = r.get('id', '')
        fluid = calculate_fluid_score(r.get('score', 0), point_id)

        meta = _get_reinforcement(point_id)
        first_created = meta.get('first_created', '')
        days_passed = 0
        if first_created:
            try:
                created = datetime.datetime.fromisoformat(first_created)
                days_passed = (datetime.datetime.now() - created).total_seconds() / 86400
            except:
                pass

        candidates.append({
            'point_id': point_id,
            'text': r.get('text', '')[:60],
            'type': r.get('type', ''),
            'fluid_score': round(fluid, 4),
            'days_passed': round(days_passed, 1),
            'access_count': meta.get('access_count', 0),
            'importance': meta.get('importance', 0.5),
        })

    # 按分数升序（最低分的先出现）
    candidates.sort(key=lambda x: x['fluid_score'])
    return candidates


def archive_low_score_memories(threshold: float = ARCHIVE_THRESHOLD) -> dict:
    """
    将低于阈值的记忆归档到 L3 归档目录
    返回 {"archived": N, "skipped": N}
    """
    candidates = scan_for_forgetting(n_samples=100)

    archived = []
    skipped = []

    for cand in candidates:
        if cand['fluid_score'] >= threshold:
            skipped.append(cand)
            continue

        # 归档到 L3
        point_id = cand['point_id']
        text = cand['text']
        mem_type = cand['type']
        days = cand['days_passed']

        try:
            from memory_consts import MEMORY_ARCHIVE_DIR
            from memory_store import _load_vectorize_state, _save_vectorize_state
            import hashlib

            MEMORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            year_month = datetime.datetime.now().strftime('%Y-%m')

            archive_file = MEMORY_ARCHIVE_DIR / year_month / f"archived_{point_id[:12]}.md"
            archive_file.parent.mkdir(parents=True, exist_ok=True)

            content = f"""---
type: {mem_type}
original_id: {point_id}
archived_at: {datetime.datetime.now().isoformat()}
days_alive: {days}
fluid_score: {cand['fluid_score']}
access_count: {cand['access_count']}
importance: {cand['importance']}
---

# 已归档记忆

{text}

---
"""
            archive_file.write_text(content, encoding='utf-8')

            # 从向量库删除
            store = get_store()
            store.delete(point_id)

            # 标记为已归档
            _update_reinforcement(point_id, is_archived=True)

            archived.append(point_id)
        except Exception as e:
            print(f"  ⚠️  归档失败 {point_id[:8]}: {e}")
            skipped.append(cand)

    return {"archived": len(archived), "skipped": len(skipped), "archived_ids": archived}


# ─────────────────────────────────────────────────────────────
# 重要记忆强化（从会话内容中提取）
# ─────────────────────────────────────────────────────────────

def extract_and_boost_from_session(session_text: str) -> list[str]:
    """
    从会话文本中提取关键词，对相关记忆做强化 boost
    简单实现：提取命名实体、命令、技术词，对匹配的记忆 +1 boost
    """
    import re
    # 简单词提取
    words = re.findall(r'\b[A-Za-z][\w\-\.\+]{3,}\b', session_text)
    boosted = []

    for word in set(words):
        if len(word) < 4:
            continue
        results = search_with_fluid(word, n_results=2)
        for r in results:
            if r['fluid_score'] > 0.1:
                pid = r.get('id', '')
                reinforce(pid)
                boosted.append(pid[:12])

    return list(set(boosted))


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='记忆强化与遗忘机制')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('scan', help='扫描低分记忆')
    p.add_argument('--n', type=int, default=20)

    p = sub.add_parser('archive', help='归档低于阈值的记忆')

    p = sub.add_parser('reinforce', help='强化指定记忆')
    p.add_argument('point_id')

    p = sub.add_parser('set-importance', help='设置记忆重要性')
    p.add_argument('point_id')
    p.add_argument('importance', type=float)

    p = sub.add_parser('test', help='测试 Ebbinghaus 公式')

    args = parser.parse_args()

    if args.cmd == 'scan':
        print("🔍 扫描低分记忆（按 fluid_score 升序）\n")
        cands = scan_for_forgetting(n_samples=args.n)
        for c in cands:
            bar = "░" * int(c['fluid_score'] * 20)
            print(f"  [{c['fluid_score']:.3f}] {bar} | {c['type']} | 访问{c['access_count']}次 | {c['days_passed']}天前 | {c['text']}")
        print(f"\n  共 {len(cands)} 条候选")

    elif args.cmd == 'archive':
        print("🗑️  执行归档...")
        r = archive_low_score_memories()
        print(f"✅ 归档 {r['archived']} 条，跳过 {r['skipped']} 条")

    elif args.cmd == 'reinforce':
        reinforce(args.point_id)
        print(f"✅ 已强化 {args.point_id[:12]}")

    elif args.cmd == 'set-importance':
        set_importance(args.point_id, args.importance)
        print(f"✅ 重要性设为 {args.importance}")

    elif args.cmd == 'test':
        print("📐 Ebbinghaus 遗忘曲线测试\n")
        print("days  |  decay  |  boost(access=0)  |  boost(access=5)  |  boost(access=20)")
        print("-" * 80)
        for days in [0, 1, 7, 14, 30, 60, 90]:
            decay = math.exp(-LAMBDA_DECAY * days)
            boost0 = ALPHA_BOOST * 0.5 * math.log(1 + 0)
            boost5 = ALPHA_BOOST * 0.5 * math.log(1 + 5)
            boost20 = ALPHA_BOOST * 0.5 * math.log(1 + 20)
            print(f"  {days:3d}  | {decay:.4f}  |  {boost0:.4f}             |  {boost5:.4f}             |  {boost20:.4f}")
    else:
        print("用法: python3 memory_reinforcement.py [scan|archive|reinforce|set-importance|test]")
