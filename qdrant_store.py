#!/usr/bin/env python3
"""
Qdrant 向量库存储层 - 适配 vector-memory-self-evolution
使用 Qdrant HTTP API (REST)

性能优化：
  - 模块级 httpx.Client 复用（连接池），避免每次 API 调用建连开销
"""
import json
import hashlib
import datetime
import httpx

from memory_consts import QDRANT_REST_URL, QDRANT_COLLECTION_NAME
from memory_config import config as _runtime_config
from embedding_client import get_embedding, get_embeddings

# 模块级 Client 复用（连接池）
_cached_client: httpx.Client | None = None

def _get_client() -> httpx.Client:
    """返回复用 Client（含连接池）"""
    global _cached_client
    if _cached_client is None:
        _cached_client = httpx.Client(
            from memory_consts import QDRANT_TIMEOUT; timeout=QDRANT_TIMEOUT,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _cached_client


def _api(path, method="GET", data=None):
    """调用 Qdrant REST API（复用连接）"""
    url = f"{QDRANT_REST_URL}{path}"
    kwargs = {}
    if data is not None:
        kwargs["json"] = data
    resp = _get_client().request(method, url, **kwargs)
    resp.raise_for_status()
    return resp.json()

def init_collection():
    """初始化集合"""
    from memory_consts import QDRANT_COLLECTION_NAME as _coll
    dim = _runtime_config.qdrant.vector_dimension
    
    try:
        _api(f"/collections/{QDRANT_COLLECTION_NAME}")
        print(f"集合 {QDRANT_COLLECTION_NAME} 已存在")
    except Exception:
        _api("/collections", method="PUT", data={
            "vectors": {"size": dim, "distance": "Cosine"},
            "hnsw_config": {
                "m": 16,
                "ef_construct": 200,
                "full_scan_threshold": 500  # 已在consts.QDRANT_FULL_SCAN_THRESHOLD,
            },
            "optimizer_config": {
                "indexing_threshold": 100,
            },
        })
        print(f"✅ 创建集合 {QDRANT_COLLECTION_NAME}，维度={dim}，HNSW m=16/ef_construct=200")

def add_memory(text: str, memory_type: str, source: str = "", metadata: dict = None):
    """添加单条记忆（确定性 ID，同一文本不会重复添加）"""
    emb = get_embedding(text)
    # 确定性 ID：同一文本 → 相同 ID，Qdrant PUT 时自然覆盖，实现去重
    point_id = hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]
    
    payload = {
        "text": text,
        "type": memory_type,
        "source": source,
        "timestamp": datetime.datetime.now().isoformat(),
        **(metadata or {})
    }
    
    _api(f"/collections/{QDRANT_COLLECTION_NAME}/points", method="PUT", data={
        "points": [{"id": point_id, "vector": emb, "payload": payload}]
    })
    
    print(f"✅ 添加记忆 [{memory_type}]: {text[:50]}...")
    return point_id

def add_memories_batch(texts: list[str], memory_type: str, source: str = "", metadata: dict = None):
    """批量添加记忆"""
    embeddings = get_embeddings(texts)
    
    points = []
    for i, (text, emb) in enumerate(zip(texts, embeddings)):
        point_id = hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]
        payload = {
            "text": text,
            "type": memory_type,
            "source": source,
            "timestamp": datetime.datetime.now().isoformat(),
            **(metadata or {})
        }
        points.append({"id": point_id, "vector": emb, "payload": payload})
    
    _api(f"/collections/{QDRANT_COLLECTION_NAME}/points", method="PUT", data={"points": points})
    print(f"✅ 批量添加 {len(texts)} 条记忆")

def search_memories(query: str, n_results: int = 5, memory_type: str = None) -> list[dict]:
    """语义搜索记忆"""
    emb = get_embedding(query)
    
    filter_cond = None
    if memory_type:
        filter_cond = {
            "must": [{
                "key": "type",
                "match": {"value": memory_type}
            }]
        }
    
    result = _api(f"/collections/{QDRANT_COLLECTION_NAME}/points/search", method="POST", data={
        "vector": emb,
        "limit": n_results,
        "with_payload": True,
        "filter": filter_cond
    })
    
    return [
        {
            "id": r["id"],
            "text": r["payload"].get("text"),
            "type": r["payload"].get("type"),
            "source": r["payload"].get("source"),
            "timestamp": r["payload"].get("timestamp"),
            "score": r["score"]
        }
        for r in result.get("result", [])
    ]

def delete_memory(point_id: str) -> bool:
    """删除记忆"""
    try:
        _api(f"/collections/{QDRANT_COLLECTION_NAME}/points/delete", method="POST", data={"points": [point_id]})
        print(f"✅ 删除记忆: {point_id}")
        return True
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        return False

def count_memories() -> int:
    """统计记忆数量"""
    result = _api(f"/collections/{QDRANT_COLLECTION_NAME}/points/count", method="POST", data={"exact": True})
    return result.get("result", {}).get("count", 0)

if __name__ == "__main__":
    import sys
    
    init_collection()
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        
        if cmd == "add":
            text = " ".join(sys.argv[2:])
            add_memory(text, "general")
        
        elif cmd == "search":
            query = " ".join(sys.argv[2:])
            results = search_memories(query)
            print(f"\n找到 {len(results)} 条相关记忆:")
            for r in results:
                print(f"  [{r['score']:.3f}] {r['text'][:80]}")
        
        elif cmd == "count":
            print(f"记忆总数: {count_memories()}")
        
        elif cmd == "init":
            init_collection()
    else:
        print("用法: python3 qdrant_store.py [add|search|count|init]")
