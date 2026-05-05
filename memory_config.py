#!/usr/bin/env python3
"""
配置加载模块 - 配置外部化核心
支持三层优先级：默认 < memory_config.yaml < 环境变量

用法：
    from memory_consts import get_config  # 向后兼容接口
    from memory_config import config       # 直接获取完整配置对象

环境变量列表：
    MEMORY_WORKSPACE        → memory.workspace
    QDRANT_REST_URL         → qdrant.rest_url
    QDRANT_GRPC_URL         → qdrant.grpc_url
    QDRANT_COLLECTION       → qdrant.collection_name
    SILICONFLOW_API_KEY     → embedding.api_key
    SILICONFLOW_MODEL       → embedding.model
    MEMORY_TURN_THRESHOLD   → behavior.turn_threshold
    MEMORY_MAX_CONTENT_LEN  → behavior.max_content_len
    MEMORY_ARCHIVE_DAYS     → memory.archive_after_days
"""
import os, json
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────
# Pydantic 配置模型
# ─────────────────────────────────────────────────────────────

class QdrantConfig(BaseModel):
    rest_url: str = "http://127.0.0.1:6333"
    grpc_url: str = "127.0.0.1:6334"
    collection_name: str = "memories"
    vector_dimension: int = 2560


class EmbeddingConfig(BaseModel):
    provider: str = "siliconflow"
    model: str = "Qwen/Qwen3-Embedding-4B"
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    timeout: int = 60


class MemoryPaths(BaseModel):
    workspace: Path = Path.home() / ".openclaw/workspace"
    memory_dir: Path = Path.home() / ".openclaw/workspace/memory"
    archive_dir: Path = Path.home() / ".openclaw/workspace/memory_archive"
    vector_db_dir: Path = Path.home() / ".openclaw/workspace/vector_db"
    config_file: Optional[Path] = None  # 运行时配置文件路径


class BehaviorConfig(BaseModel):
    turn_threshold: int = 5
    max_content_len: int = 10_000
    archive_after_days: int = 30
    dedup_window_days: int = 7


class MemoryConfigModel(BaseModel):
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryPaths = Field(default_factory=MemoryPaths)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)

    @field_validator("memory", mode="before")
    @classmethod
    def resolve_memory_paths(cls, v):
        if isinstance(v, dict):
            for k, val in v.items():
                if isinstance(val, str) and not val.startswith("/"):
                    # 相对路径 → 相对于 workspace
                    ws = v.get("workspace", str(Path.home() / ".openclaw/workspace"))
                    v[k] = str(Path(ws) / val)
        return v


# ─────────────────────────────────────────────────────────────
# 配置文件定位（支持多路径）
# ─────────────────────────────────────────────────────────────

CONFIG_SEARCH_PATHS = [
    Path.home() / ".openclaw/workspace/memory_config.yaml",
    Path.home() / ".openclaw/workspace/memory_config.json",  # 兼容旧版
    Path.home() / ".openclaw/memory_config.yaml",
    Path("memory_config.yaml"),  # 当前工作目录
]


def _find_config_file() -> Optional[Path]:
    for p in CONFIG_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def _load_file(path: Path) -> dict:
    if path.suffix in (".yaml", ".yml"):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    elif path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _env_overrides() -> dict:
    """从环境变量生成覆盖配置（第二高优先级）"""
    overrides = {}

    # Qdrant
    if v := os.getenv("QDRANT_REST_URL"):
        overrides.setdefault("qdrant", {})["rest_url"] = v
    if v := os.getenv("QDRANT_GRPC_URL"):
        overrides.setdefault("qdrant", {})["grpc_url"] = v
    if v := os.getenv("QDRANT_COLLECTION"):
        overrides.setdefault("qdrant", {})["collection_name"] = v

    # Embedding
    if v := os.getenv("SILICONFLOW_API_KEY"):
        overrides.setdefault("embedding", {})["api_key"] = v
    if v := os.getenv("SILICONFLOW_MODEL"):
        overrides.setdefault("embedding", {})["model"] = v
    if v := os.getenv("SILICONFLOW_BASE_URL"):
        overrides.setdefault("embedding", {})["base_url"] = v

    # Memory paths
    if v := os.getenv("MEMORY_WORKSPACE"):
        overrides.setdefault("memory", {})["workspace"] = v
    if v := os.getenv("MEMORY_DIR"):
        overrides.setdefault("memory", {})["memory_dir"] = v
    if v := os.getenv("MEMORY_ARCHIVE_DIR"):
        overrides.setdefault("memory", {})["archive_dir"] = v

    # Behavior
    if v := os.getenv("MEMORY_TURN_THRESHOLD"):
        overrides.setdefault("behavior", {})["turn_threshold"] = int(v)
    if v := os.getenv("MEMORY_MAX_CONTENT_LEN"):
        overrides.setdefault("behavior", {})["max_content_len"] = int(v)
    if v := os.getenv("MEMORY_ARCHIVE_DAYS"):
        overrides.setdefault("behavior", {})["archive_after_days"] = int(v)

    return overrides


# ─────────────────────────────────────────────────────────────
# 全局配置单例
# ─────────────────────────────────────────────────────────────

def _build_config() -> MemoryConfigModel:
    """加载配置（三层合并）"""
    file_cfg = {}

    # 1. 从文件加载（最低优先级）
    if config_path := _find_config_file():
        file_cfg = _load_file(config_path)
        # 记录配置文件路径，供后续使用
        file_cfg.setdefault("memory", {})["config_file"] = str(config_path)

    # 2. 环境变量覆盖（最高优先级）
    env_cfg = _env_overrides()

    # 3. 深度合并：file_cfg < env_cfg
    def deep_merge(base: dict, overlay: dict) -> dict:
        result = base.copy()
        for k, v in overlay.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    merged = deep_merge(file_cfg, env_cfg)
    return MemoryConfigModel.model_validate(merged)


# 全局单例（惰性加载，进程内只加载一次）
config: MemoryConfigModel = _build_config()


# ─────────────────────────────────────────────────────────────
# 向后兼容导出（供 memory_consts.py 使用）
# 注意：memory_consts.py 的常量应从 config 对象读取，
#       以保证环境变量覆盖生效。
# ─────────────────────────────────────────────────────────────

def get_config() -> MemoryConfigModel:
    """返回完整配置对象（推荐）"""
    return config
