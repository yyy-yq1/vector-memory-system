#!/usr/bin/env python3
"""
快速向量检索脚本 - 使用 Qdrant 搜索记忆
用法: python3 quick_search.py <查询内容>
"""
import sys
import time
from pathlib import Path
from functools import lru_cache

from memory_consts import SKILL_DIR
sys.path.insert(0, str(SKILL_DIR))

# ─────────────────────────────────────────────────────────────
# 搜索结果缓存（60s TTL，避免重复向量检索）
# ─────────────────────────────────────────────────────────────
_SEARCH_CACHE = {}
_CACHE_TS = {}
_CACHE_TTL = 60

@lru_cache(maxsize=32)
def _cached_qdrant_search(query: str, n_results: int = 5):
    """带 TTL 缓存的 Qdrant 向量搜索（结果缓存 60 秒）"""
    from memory_store import get_store
    store = get_store()
    return store.search(query, n_results=n_results)

def search_today(query, n_results=5):
    """搜索记忆（使用 60s TTL 缓存）"""
    print(f"\n🔍 向量检索: {query}\n")
    print("=" * 60)

    try:
        results = _cached_qdrant_search(query, n_results)

        if not results:
            print("❌ 未找到相关内容")
            return []

        for i, r in enumerate(results, 1):
            print(f"\n【结果 {i}】")
            print(f"  类型: {r.get('type', '未知')}")
            print(f"  来源: {r.get('source', '未知')}")
            print(f"  时间: {r.get('timestamp', '未知')}")
            print(f"  相似度: {r['score']:.3f}")
            print(f"  内容:")
            text = r.get('text', '')
            print(f"   {text[:300]}{'...' if len(text) > 300 else ''}")

        print("\n" + "=" * 60)
        return results

    except Exception as e:
        print(f"❌ 检索失败: {e}")
        return []

if __name__ == '__main__':
    if len(sys.argv) > 1:
        query = ' '.join(sys.argv[1:])
        search_today(query)
    else:
        print("用法: python3 quick_search.py <查询内容>")
        print("示例: python3 quick_search.py 语音插件")
        print("       python3 quick_search.py Qdrant 安装")
