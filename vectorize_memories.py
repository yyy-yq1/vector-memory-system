#!/usr/bin/env python3
"""
记忆向量化脚本 - 增量同步 L2 文件 → L4 Qdrant
使用 SiliconFlow Qwen3-Embedding-4B 生成嵌入向量

触发方式（两路）：
  1. capture() 每 5 轮对话自动调用（主路径）
  2. crontab 每小时兜底运行（次路径）

增量策略：
  - 状态文件记录已同步 entry 的文本 hash 集合
  - 只添加新 hash 的 entry，已存在的自动去重（确定性 ID）
  - 同步完成后更新状态文件
"""
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

from memory_consts import WORKSPACE, MEMORY_DIR, VECTORIZE_STATE_FILE, SKILL_DIR
sys.path.insert(0, str(SKILL_DIR))

def _entry_hash(text: str) -> str:
    """计算 entry 的唯一 hash（用于去重跟踪）"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]

def _load_state():
    if VECTORIZE_STATE_FILE.exists():
        with open(VECTORIZE_STATE_FILE) as f:
            return json.load(f)
    return {"synced_hashes": [], "last_sync": None}

def _save_state(state):
    with open(VECTORIZE_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def parse_memory_file(filepath):
    """
    解析 memory/*.md 文件，提取所有 entry。
    返回 [(hash, type, text, meta), ...]
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    results = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == '---':
            # 收集 front matter（直到下一个 ---）
            j = i + 1
            front_lines = []
            while j < len(lines) and lines[j].strip() != '---':
                front_lines.append(lines[j])
                j += 1

            # 收集 body（直到再下一个 ---）
            k = j + 1
            body_lines = []
            while k < len(lines) and lines[k].strip() != '---':
                body_lines.append(lines[k])
                k += 1

            # 解析 front matter
            meta = {}
            for line in front_lines:
                if ':' in line:
                    k2, v2 = line.split(':', 1)
                    meta[k2.strip()] = v2.strip()

            body = '\n'.join(body_lines).strip()
            entry_type = meta.get('type', filepath.parent.name)

            if body:
                h = _entry_hash(body)
                results.append({
                    'hash': h,
                    'type': entry_type,
                    'text': body,
                    'meta': meta,
                    'source': filepath.name
                })

            i = k + 1
        else:
            i += 1

    return results

def vectorize_memories(force_full=False):
    """
    增量向量化：只同步尚未记录的 entry。

    force_full=True  时：忽略已同步记录，重新处理所有 entry（用于修复）
    force_full=False 时：只处理 synced_hashes 中不存在的新 entry
    """
    from embedding_client import get_embeddings
    from memory_store import get_store

    store = get_store()
    state = _load_state()
    synced_hashes = set(state.get("synced_hashes", []))

    print(f"🚀 向量化记忆（{'全量' if force_full else '增量'}模式）")

    # 收集所有 entry
    all_entries = []
    for memory_type in ['error', 'correction', 'practice', 'event', 'gap']:
        type_dir = MEMORY_DIR / memory_type
        if not type_dir.exists():
            continue
        for md_file in sorted(type_dir.glob("*.md")):
            try:
                entries = parse_memory_file(md_file)
                for entry in entries:
                    if entry['text']:
                        all_entries.append(entry)
            except Exception as e:
                print(f"⚠️  读取失败 {md_file}: {e}")

    if not all_entries:
        print("✅ 没有需要向量化的记忆")
        return

    # 过滤：只处理新增 entry
    if force_full:
        new_entries = all_entries
    else:
        new_entries = [e for e in all_entries if e['hash'] not in synced_hashes]

    print(f"📝 文件共 {len(all_entries)} 条 entry，{len(new_entries)} 条新 entry")

    if not new_entries:
        print("✅ 没有新 entry 需要向量化")
        state["last_sync"] = datetime.now().isoformat()
        _save_state(state)
        return

    # 批量向量化
    batch_size = 50
    success = 0
    now = datetime.now().isoformat()

    for i in range(0, len(new_entries), batch_size):
        batch = new_entries[i:i + batch_size]
        texts = [m["text"] for m in batch]

        try:
            embeddings = get_embeddings(texts)
            for j, (mem, emb) in enumerate(zip(batch, embeddings)):
                store.add(
                    mem["text"], mem["type"],
                    source=mem.get("source", ""),
                    metadata={
                        **mem.get("meta", {}),
                        "source": mem.get("source", ""),
                        "vectorized_at": now,
                    }
                )
                # 记录已同步的 hash
                synced_hashes.add(mem["hash"])
                success += 1
            print(f"  批次 {i // batch_size + 1}: {min(i + batch_size, len(new_entries))}/{len(new_entries)} 条")
        except Exception as e:
            print(f"⚠️  批次失败: {e}")

    # 更新状态
    state["synced_hashes"] = list(synced_hashes)
    state["last_sync"] = datetime.now().isoformat()
    _save_state(state)

    total = store.count()
    print(f"\n✅ 向量化完成！本次处理 {success} 条，Qdrant 共 {total} 条")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="全量重新同步（忽略增量状态）")
    args = parser.parse_args()

    vectorize_memories(force_full=args.full)
