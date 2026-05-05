#!/usr/bin/env python3
"""
语义检索测试 - 测试 Qdrant 向量记忆的语义搜索功能
"""
import sys
from pathlib import Path

SKILL_DIR = Path.home() / '.openclaw/workspace' / 'skills' / 'vector-memory-self-evolution'
sys.path.insert(0, str(SKILL_DIR))

def semantic_search(query, n_results=3):
    """语义搜索记忆"""
    print(f"\n🔍 语义搜索: {query}\n")
    print("=" * 60)

    try:
        from memory_store import get_store
        store = get_store()
        results = store.search(query, n_results=n_results)

        if not results:
            print("❌ 未找到相关记忆")
            return

        for i, r in enumerate(results, 1):
            print(f"\n【结果 {i}】")
            print(f"  类型: {r.get('type', '未知')}")
            print(f"  来源: {r.get('source', '未知')}")
            print(f"  时间: {r.get('timestamp', '未知')}")
            print(f"  相似度: {r['score']:.3f}")
            print(f"  内容:")
            text = r.get('text', '')
            print(f"   {text[:200]}{'...' if len(text) > 200 else ''}")

        print("\n" + "=" * 60)

    except Exception as e:
        print(f"❌ 搜索失败: {e}")

if __name__ == '__main__':
    print("🧪 向量记忆语义检索测试\n")

    test_queries = [
        "向量数据库安装",
        "Qdrant 部署",
        "SiliconFlow 嵌入",
        "npm install 失败",
        "代码风格规范",
        "自我进化系统",
        "飞书配置"
    ]

    for query in test_queries:
        semantic_search(query, n_results=2)
        print("\n")
