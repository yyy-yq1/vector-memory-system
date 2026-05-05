#!/usr/bin/env python3
"""
向量数据库测试 - 验证 Qdrant 连接和基本操作
"""
import sys
from pathlib import Path

SKILL_DIR = Path.home() / '.openclaw/workspace' / 'skills' / 'vector-memory-self-evolution'
sys.path.insert(0, str(SKILL_DIR))

def test_qdrant():
    """测试 Qdrant 向量库"""
    print("🧪 Qdrant 向量库测试\n")
    print("=" * 60)

    # 1. 测试 API 连接
    print("\n1️⃣  测试 Qdrant REST API...")
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:6333/collections", timeout=5)
        data = resp.json()
        collections = data.get('result', {}).get('collections', [])
        print(f"   ✅ API 正常，运行中")
        print(f"   集合列表: {[c['name'] for c in collections]}")
    except Exception as e:
        print(f"   ❌ API 失败: {e}")
        return

    # 2. 测试集合
    print("\n2️⃣  测试 memories 集合...")
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:6333/collections/memories", timeout=5)
        info = resp.json().get('result', {})
        print(f"   ✅ 集合状态: {info.get('status')}")
        print(f"   向量维度: {info.get('config', {}).get('params', {}).get('vectors', {}).get('size')}")
        print(f"   点数量: {info.get('points_count', 0)}")
        print(f"   距离度量: {info.get('config', {}).get('params', {}).get('vectors', {}).get('distance')}")
    except Exception as e:
        print(f"   ❌ 集合查询失败: {e}")

    # 3. 测试嵌入
    print("\n3️⃣  测试 SiliconFlow Qwen3-Embedding-4B...")
    try:
        from embedding_client import get_embedding
        emb = get_embedding("这是一个测试文本")
        print(f"   ✅ 嵌入生成成功")
        print(f"   向量维度: {len(emb)}")
        print(f"   前3维: {emb[:3]}")
    except Exception as e:
        print(f"   ❌ 嵌入失败: {e}")

    # 4. 测试添加（使用临时ID，验证后删除）
    print("\n4️⃣  测试添加记忆...")
    try:
        from memory_store import get_store
        store = get_store()
        test_pid = store.add("测试记忆：系统健康检查", "test", source="test_vector_db.py")
        print(f"   ✅ 添加成功，ID: {test_pid}")
        store.delete(test_pid)
        print(f"   🧹 已清理测试记忆")
    except Exception as e:
        print(f"   ❌ 添加失败: {e}")

    # 5. 测试搜索
    print("\n5️⃣  测试语义搜索...")
    try:
        from memory_store import get_store
        store = get_store()
        results = store.search("系统健康检查", n_results=3)
        print(f"   ✅ 搜索成功，找到 {len(results)} 条")
        for r in results:
            print(f"   [{r['score']:.3f}] {r['text'][:60]}")
    except Exception as e:
        print(f"   ❌ 搜索失败: {e}")

    # 6. 测试数量统计
    print("\n6️⃣  测试计数...")
    try:
        from memory_store import get_store
        store = get_store()
        cnt = store.count()
        print(f"   ✅ 记忆总数: {cnt}")
    except Exception as e:
        print(f"   ❌ 计数失败: {e}")

    print("\n" + "=" * 60)
    print("✅ 测试完成")

if __name__ == '__main__':
    test_qdrant()
