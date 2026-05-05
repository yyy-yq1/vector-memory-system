#!/usr/bin/env python3
"""
SiliconFlow Qwen3-Embedding-4B 嵌入客户端

性能优化：
  - 模块级 httpx.Client 复用（连接池），避免每次调用建连开销
  - 配置从 memory_consts 读取（进程内只解析一次）
"""
import httpx
from memory_consts import (
    EMBEDDING_MODEL, EMBEDDING_TIMEOUT,
)
from memory_config import config as _runtime_config

# 模块级 Client 复用（连接池）
_cached_client: httpx.Client | None = None

def _get_client() -> httpx.Client:
    """返回复用 Client（含连接池）"""
    global _cached_client
    if _cached_client is None:
        _cached_client = httpx.Client(
            timeout=float(EMBEDDING_TIMEOUT),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _cached_client


def get_embedding(text: str) -> list[float]:
    """调用 SiliconFlow API 获取文本嵌入（复用连接）"""
    api_key = _runtime_config.embedding.api_key
    model = EMBEDDING_MODEL

    resp = _get_client().post(
        "https://api.siliconflow.cn/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={"input": text, "model": model},
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """批量获取嵌入（复用连接）"""
    api_key = _runtime_config.embedding.api_key
    model = EMBEDDING_MODEL

    resp = _get_client().post(
        "https://api.siliconflow.cn/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={"input": texts, "model": model},
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]

if __name__ == "__main__":
    # 测试
    import sys
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = "今天安装了一个很棒的AI助手"
    
    emb = get_embedding(text)
    print(f"✅ 嵌入维度: {len(emb)}")
    print(f"✅ 前5维: {emb[:5]}")
