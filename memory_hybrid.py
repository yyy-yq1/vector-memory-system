#!/usr/bin/env python3
"""
QMD 混合搜索 v1.0
===================

整合自 Brain-v1.1.8 的 QMD 语义记忆系统：

架构：BM25（关键词） + Embedding（语义） + RRF（Reciprocal Rank Fusion 融合）

RRF 公式：
  score(d) = Σ  1 / (k + rank_i(d))
  k = 60（标准 RRF 常数）

优势：
  - BM25：精确关键词匹配（"Docker" 搜 "Docker" → 命中）
  - Embedding：语义理解（"容器工具" → 也能找到 Docker）
  - RRF 融合：两者取长补短，排名更稳健

使用方式：
  python3 memory_hybrid.py search "飞书图片发送"
  python3 memory_hybrid.py benchmark
"""
import re
import sys
import datetime
from pathlib import Path
from typing import Optional

try:
    import rank_bm25
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False

WORKSPACE = Path.home() / '.openclaw/workspace'
SKILL_DIR = Path(__file__).parent
MEMORY_DIR = WORKSPACE / 'memory'

RRF_K = 60  # RRF 常数

# ─────────────────────────────────────────────────────────────
# BM25 搜索
# ─────────────────────────────────────────────────────────────

def _tokenize_chinese(text: str) -> list[str]:
    """
    简单中文分词（基于字符 n-gram + 英文分词）
    英文按空格/驼峰分割，中文按字符组合
    """
    if not text:
        return []
    # 英文：按空格 + 驼峰分割
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    en_tokens = re.findall(r'[A-Za-z0-9]+', text)
    # 中文：字符组合（2-gram + 3-gram）
    cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
    cn_tokens = []
    for n in [2, 3]:
        for i in range(len(cn_chars) - n + 1):
            cn_tokens.append(''.join(cn_chars[i:i+n]))
    return en_tokens + cn_tokens


def bm25_search(query: str, documents: list[dict], top_k: int = 10) -> list[dict]:
    """
    BM25 关键词搜索

    documents: [{"text": str, "id": str, "type": str}, ...]
    返回: [{"id", "text", "score", "rank"}, ...]
    """
    if not BM25_AVAILABLE or not documents:
        return []

    corpus = [doc['text'] for doc in documents]
    tokenized_corpus = [_tokenize_chinese(doc) for doc in corpus]
    query_tokens = _tokenize_chinese(query)

    if not query_tokens:
        return []

    bm25 = rank_bm25.BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(query_tokens)

    # 按分数排序
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])
    results = []
    for rank, (idx, score) in enumerate(ranked[:top_k]):
        if score > 0:
            results.append({
                'id': documents[idx].get('id', str(idx)),
                'text': documents[idx].get('text', ''),
                'type': documents[idx].get('type', ''),
                'score': float(score),
                'rank': len(results) + 1,
                'method': 'bm25'
            })

    return results


# ─────────────────────────────────────────────────────────────
# RRF 融合
# ─────────────────────────────────────────────────────────────

def rrf_merge(ranked_lists: list[list[dict]], top_k: int = 10) -> list[dict]:
    """
    Reciprocal Rank Fusion

    输入：多个排好序的结果列表（每个列表是 [{"id", "score", "rank"}, ...]）
    输出：RRF 融合后的排序
    """
    scores: dict[str, float] = {}

    for ranked_list in ranked_lists:
        for pos, item in enumerate(ranked_list, start=1):
            item_id = item.get('id', str(pos))
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (RRF_K + pos)

    # 排序
    sorted_items = sorted(scores.items(), key=lambda x: -x[1])
    results = []
    seen_ids = set()  # 防止同一 ID 在多路结果中重复添加
    for rank, (item_id, rrf_score) in enumerate(sorted_items[:top_k], start=1):
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        # 找到原始 item（多路中取排在最前面那个）
        for rl in ranked_lists:
            for item in rl:
                if item.get('id') == item_id:
                    results.append({
                        **item,
                        'rrf_score': rrf_score,
                        'rank': rank,
                        'method': 'rrf'
                    })
                    break
    return results


# ─────────────────────────────────────────────────────────────
# 混合搜索入口
# ─────────────────────────────────────────────────────────────

def hybrid_search(query: str, n_results: int = 5) -> list[dict]:
    """
    BM25 + 向量语义搜索 + RRF 融合

    策略：
    1. 从 Qdrant 获取向量搜索结果（语义）
    2. 从 memory_store 获取 BM25 结果（关键词）
    3. RRF 融合排名
    """
    sys.path.insert(0, str(SKILL_DIR))

    bm25_results = []
    vec_results = []

    # 1. BM25：从 L2/L3 markdown 文件获取所有记忆文本
    try:
        from memory_consts import MEMORY_DIR, MEMORY_TYPES
        docs = []
        # 扫描 memory/ 目录下的所有 .md 文件
        for md_file in MEMORY_DIR.glob('**/*.md'):
            if md_file.name.startswith('.') or 'checkpoint' in md_file.name:
                continue
            try:
                text = md_file.read_text(encoding='utf-8')
                # 提取标题和内容（去掉 frontmatter）
                lines = text.split('\n')
                clean_lines = [l for l in lines if not l.strip().startswith('---') and l.strip()]
                clean_text = ' '.join(clean_lines[:20])  # 取前20行
                if clean_text:
                    docs.append({
                        'id': str(md_file.relative_to(MEMORY_DIR)),
                        'text': clean_text[:500],
                        'type': md_file.parent.name if md_file.parent != MEMORY_DIR else 'misc'
                    })
            except Exception:
                continue
        if docs:
            bm25_results = bm25_search(query, docs, top_k=n_results * 2)
    except Exception as e:
        print(f"BM25 搜索失败: {e}", file=sys.stderr)

    # 2. 向量搜索（语义）
    try:
        from qdrant_store import search_memories
        raw_vec = search_memories(query, n_results=n_results * 2)
        vec_results = [{
            'id': r.get('id', str(i)),
            'text': r.get('text', ''),
            'type': r.get('type', ''),
            'score': r.get('score', 0),
            'rank': i + 1,
            'method': 'vector'
        } for i, r in enumerate(raw_vec)]
    except Exception as e:
        print(f"向量搜索失败: {e}", file=sys.stderr)

    # 3. RRF 融合
    if bm25_results and vec_results:
        fused = rrf_merge([bm25_results, vec_results], top_k=n_results)
        return fused
    elif vec_results:
        return vec_results[:n_results]
    elif bm25_results:
        return bm25_results[:n_results]
    else:
        return []


def quick_benchmark():
    """快速测试 BM25 效果"""
    docs = [
        {'id': '1', 'text': '飞书消息发送使用 message(action=send, channel=feishu)', 'type': 'skill'},
        {'id': '2', 'text': 'Docker 容器管理 docker ps docker run', 'type': 'skill'},
        {'id': '3', 'text': 'Qdrant 向量数据库搜索 search_memories(query)', 'type': 'skill'},
        {'id': '4', 'text': 'Git 版本控制 git commit git push', 'type': 'skill'},
        {'id': '5', 'text': '飞书图片发送使用 message(action=send, filePath=...)', 'type': 'skill'},
    ]

    print("🧪 BM25 基准测试\n")
    for q in ['飞书', 'Docker', '发送消息', 'Git', '向量']:
        results = bm25_search(q, docs, top_k=3)
        print(f"查询: '{q}'")
        for r in results:
            print(f"  [{r['score']:.2f}] {r['text'][:50]}")
        print()


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='QMD 混合搜索')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('search', help='混合搜索')
    p.add_argument('query', nargs='*', help='搜索查询')
    p.add_argument('--top', '-n', type=int, default=5, help='返回数量')

    p = sub.add_parser('benchmark', help='BM25 基准测试')
    p.add_argument('--query', '-q', help='单条查询')

    args = parser.parse_args()

    if args.cmd == 'search':
        query = ' '.join(args.query) if args.query else input('查询: ')
        print(f"🔍 混合搜索: {query}\n")
        results = hybrid_search(query, n_results=args.top)
        if results:
            for r in results:
                print(f"  #{r['rank']} [{r.get('method','?')}] (RRF={r.get('rrf_score',0):.3f}) {r.get('text','')[:70]}")
        else:
            print("  无结果")

    elif args.cmd == 'benchmark':
        if args.query:
            docs = [{'id': '1', 'text': '飞书图片发送', 'type': 'skill'}]
            r = bm25_search(args.query, docs, top_k=1)
            print(f"BM25('{args.query}'): {r}")
        else:
            quick_benchmark()

    else:
        parser.print_help()
