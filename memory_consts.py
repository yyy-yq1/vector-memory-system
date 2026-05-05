#!/usr/bin/env python3
"""
共享常量模块 - 所有路径和配置常量的唯一真实来源
支持三层优先级：内置默认值 < memory_config.yaml < 环境变量

变更历史：
  v2.6.0: load_config() 增加 @lru_cache 避免重复磁盘读取
  v2.5.0: 引入 memory_config.py 实现配置外部化
"""
from pathlib import Path
from functools import lru_cache

WORKSPACE = Path.home() / '.openclaw/workspace'
SKILL_DIR = WORKSPACE / 'skills' / 'skills' / 'vector-memory-self-evolution'

try:
    from memory_config import config as _runtime_config

    QDRANT_REST_URL: str = _runtime_config.qdrant.rest_url
    QDRANT_GRPC_URL: str = _runtime_config.qdrant.grpc_url
    QDRANT_COLLECTION_NAME: str = _runtime_config.qdrant.collection_name
    EMBEDDING_MODEL: str = _runtime_config.embedding.model
    EMBEDDING_TIMEOUT: int = _runtime_config.embedding.timeout
    TURN_THRESHOLD: int = _runtime_config.behavior.turn_threshold
    MAX_CONTENT_LEN: int = _runtime_config.behavior.max_content_len
    ARCHIVE_AFTER_DAYS: int = _runtime_config.behavior.archive_after_days
    DEDUP_WINDOW_DAYS: int = _runtime_config.behavior.dedup_window_days
    MEMORY_DIR = _runtime_config.memory.memory_dir
    MEMORY_ARCHIVE_DIR = _runtime_config.memory.archive_dir
    VECTOR_DB_DIR = _runtime_config.memory.vector_db_dir
    CONFIG_PATH: Path = _runtime_config.memory.config_file or (WORKSPACE / 'memory_config.yaml')
    _CONFIG_LOADED = True

except Exception as _e:
    import sys, warnings
    warnings.warn(f"memory_config 加载失败，使用内置默认值: {_e}", stacklevel=2)
    print(f"⚠️  配置加载失败，使用内置默认值: {_e}", file=sys.stderr)

    QDRANT_REST_URL = "http://127.0.0.1:6333"
    QDRANT_GRPC_URL = "127.0.0.1:6334"
    QDRANT_COLLECTION_NAME = "memories"
    EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
    EMBEDDING_TIMEOUT = 60
    TURN_THRESHOLD = 5
    MAX_CONTENT_LEN = 10_000
    ARCHIVE_AFTER_DAYS = 30
    DEDUP_WINDOW_DAYS = 7
    MEMORY_DIR = WORKSPACE / 'memory'
    MEMORY_ARCHIVE_DIR = WORKSPACE / 'memory_archive'
    VECTOR_DB_DIR = WORKSPACE / 'vector_db'
    CONFIG_PATH = WORKSPACE / 'memory_config.json'
    _CONFIG_LOADED = False

TURNS_STATE_FILE = MEMORY_DIR / '.turns_state.json'
VECTORIZE_STATE_FILE = MEMORY_DIR / '.vectorize_state.json'
QDRANT_DATA_DIR = VECTOR_DB_DIR / 'qdrant_data'
QDRANT_CONFIG_FILE = VECTOR_DB_DIR / 'qdrant_config.yaml'
MEMORY_TYPES = ['error', 'correction', 'practice', 'event', 'gap']

# ── Magic Number Constants ──────────────────────────────────────────────────────
# 统一消魔法化：所有硬编码数字集中在这一节定义
REVIEW_THRESHOLD = 20
MAX_CPS_LEN = 500
DEFAULT_TOP_K = 5
RRF_K = 60
MAX_BM25_CHARS = 500
SNAPSHOT_MAX_TOKENS = 30000
FRAGMENT_POOL_MAX_TOKENS = 500
CONTEXT_BUILD_LIMIT = 10
DAYS_LOW_FREQUENCY = 30
DAYS_ABANDONED = 180
QDRANT_TIMEOUT = 60.0
EMBEDDING_BATCH_SIZE = 50
SKILL_HEALTH_DAYS = 30
WATCHDOG_LOG = MEMORY_DIR / 'watchdog.log'


@lru_cache(maxsize=1)
def load_config():
    """加载配置文件（结果缓存，进程内只读一次磁盘）"""
    import json
    with open(CONFIG_PATH) as f:
        return json.load(f)


def reload_config():
    """清除 load_config() 缓存，强制重新读取配置文件"""
    load_config.cache_clear()


def get_config_info() -> dict:
    """返回当前配置来源信息（调试用）"""
    from memory_config import _find_config_file
    return {
        "config_file": str(CONFIG_PATH) if CONFIG_PATH.exists() else None,
        "config_loaded": _CONFIG_LOADED,
        "env_overrides": {
            "QDRANT_REST_URL": QDRANT_REST_URL,
            "TURN_THRESHOLD": TURN_THRESHOLD,
            "MAX_CONTENT_LEN": MAX_CONTENT_LEN,
        }
    }
