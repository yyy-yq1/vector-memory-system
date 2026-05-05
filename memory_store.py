#!/usr/bin/env python3
"""
记忆存储层 - Qdrant 后端
L2 文件写入由 memory_api.capture() 单独处理

性能优化：
  - Qdrant Client 由 qdrant_store 模块复用（连接池）
  - 状态文件读写使用 fcntl 文件锁（进程安全）
"""
import json
from pathlib import Path
from typing import Optional

from memory_consts import (
    CONFIG_PATH, TURNS_STATE_FILE, VECTORIZE_STATE_FILE,
    SKILL_DIR, MAX_CONTENT_LEN, TURN_THRESHOLD,
    MEMORY_DIR, load_config
)

# === QdrantStore ===
class QdrantStore:
    def __init__(self):
        from qdrant_store import (
            init_collection, add_memory, add_memories_batch,
            search_memories, delete_memory, count_memories
        )
        self._init_fn = init_collection
        self._add_fn = add_memory
        self._add_batch_fn = add_memories_batch
        self._search_fn = search_memories
        self._delete_fn = delete_memory
        self._count_fn = count_memories

    def init(self):
        self._init_fn()

    def add(self, text, typ, source="", metadata=None):
        return self._add_fn(text, typ, source, metadata)

    def add_batch(self, texts, typ, source="", metadata=None):
        return self._add_batch_fn(texts, typ, source, metadata)

    def search(self, query, n_results=5, typ=None):
        return self._search_fn(query, n_results, typ)

    def delete(self, point_id):
        return self._delete_fn(point_id)

    def count(self):
        return self._count_fn()


def get_store():
    """返回 Qdrant 存储后端（目前唯一支持的实现）"""
    return QdrantStore()


# ─────────────────────────────────────────────────────────────
# 状态追踪（文件锁版本，进程安全）
# ─────────────────────────────────────────────────────────────

def _entry_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]

def _load_turns_state():
    if TURNS_STATE_FILE.exists():
        with open(TURNS_STATE_FILE) as f:
            return json.load(f)
    return {"turns_since_sync": 0}

def _save_turns_state(state):
    import fcntl
    with open(TURNS_STATE_FILE, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(state, f, ensure_ascii=False)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _load_vectorize_state():
    """加载 .vectorize_state.json"""
    if VECTORIZE_STATE_FILE.exists():
        with open(VECTORIZE_STATE_FILE) as f:
            return json.load(f)
    return {"synced_hashes": [], "last_sync": None}

def _save_vectorize_state(state):
    """保存 .vectorize_state.json（文件锁）"""
    import fcntl
    with open(VECTORIZE_STATE_FILE, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(state, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _update_synced_hash(text: str):
    """将 entry text 的 hash 记入状态文件（原子写 + 文件锁）"""
    import fcntl
    h = _entry_hash(text)
    state = {"synced_hashes": [], "last_sync": None}
    if VECTORIZE_STATE_FILE.exists():
        with open(VECTORIZE_STATE_FILE, 'r+') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                state = json.load(f)
                synced = set(state.get("synced_hashes", []))
                synced.add(h)
                state["synced_hashes"] = list(synced)
                f.seek(0)
                f.truncate()
                json.dump(state, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    else:
        state["synced_hashes"] = [h]
        with open(VECTORIZE_STATE_FILE, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _trigger_sync():
    """触发增量同步（每 TURN_THRESHOLD 轮对话执行一次）"""
    import subprocess, sys
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / 'vectorize_memories.py')],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"🔄 [向量同步] {result.stdout.strip().split(chr(10))[-1]}")
        else:
            print(f"⚠️  向量同步失败: {result.stderr[:100]}")
    except Exception as e:
        print(f"⚠️  向量同步异常: {e}")


# ─────────────────────────────────────────────────────────────
# capture() - 实时写入 Qdrant + 状态追踪
# L2 文件写入由 memory_api.capture() 处理
# ─────────────────────────────────────────────────────────────

def capture(typ, title, content, context=''):
    """捕获记忆（对外统一接口）
    - 实时写入 Qdrant（向量），同步更新状态文件
    - 每 TURN_THRESHOLD 轮对话触发一次文件层增量同步
    - 输入限制：单条 content 最多 MAX_CONTENT_LEN 字符
    - L2 文件写入由 memory_api.capture() 处理
    """
    if len(content) > MAX_CONTENT_LEN:
        content = content[:MAX_CONTENT_LEN] + f"\n...[截断，原始长度 {len(content)} 字符]"
        print(f"⚠️  记忆内容过长，已截断至 {MAX_CONTENT_LEN} 字符")
    if not content or not content.strip():
        print("⚠️  记忆内容为空，跳过")
        return False

    store = get_store()
    store.add(content, typ, context)
    _update_synced_hash(content)

    state = _load_turns_state()
    state['turns_since_sync'] = state.get('turns_since_sync', 0) + 1
    _save_turns_state(state)

    if state['turns_since_sync'] >= TURN_THRESHOLD:
        _trigger_sync()
        state['turns_since_sync'] = 0
        _save_turns_state(state)

    return True


def capture_batch(entries, typ='practice', context=''):
    """批量捕获记忆"""
    import hashlib
    if not entries:
        return 0

    store = get_store()
    texts, hashes = [], []

    for content in entries:
        if len(content) > MAX_CONTENT_LEN:
            content = content[:MAX_CONTENT_LEN]
        if not content or not content.strip():
            continue
        texts.append(content)
        h = hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]
        hashes.append(h)

    if not texts:
        return 0

    store.add_batch(texts, typ, context)

    state = _load_turns_state()
    _state = _load_vectorize_state()
    synced = set(_state.get('synced_hashes', []))
    for h in hashes:
        synced.add(h)
    _state['synced_hashes'] = list(synced)
    _save_vectorize_state(_state)

    state['turns_since_sync'] = state.get('turns_since_sync', 0) + len(texts)
    _save_turns_state(state)

    if state['turns_since_sync'] >= TURN_THRESHOLD:
        _trigger_sync()
        state['turns_since_sync'] = 0
        _save_turns_state(state)

    return len(texts)


def search(query, typ=None, n_results=5):
    """搜索记忆"""
    store = get_store()
    return store.search(query, n_results, typ)


def init():
    """初始化存储"""
    store = get_store()
    store.init()
