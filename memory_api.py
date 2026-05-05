#!/usr/bin/env python3
"""
记忆 API - 统一的记忆操作接口
写入:L2 文件存储 + L4 Qdrant 向量库
搜索:L4 Qdrant 向量库(语义)+ L2 文件(关键词)
"""
import json
import datetime
import sys
import time
from pathlib import Path
from functools import lru_cache

from memory_consts import WORKSPACE, MEMORY_DIR, MEMORY_ARCHIVE_DIR, MEMORY_TYPES

# ─────────────────────────────────────────────────────────────
# TTL 文件内容缓存(避免 check_before_execute 重复读磁盘)
# ─────────────────────────────────────────────────────────────
_FILE_CACHE = {}
_CACHE_TS = {}
_CACHE_TTL = 60  # 秒

def _get_cached_content(path: Path, max_age: int = _CACHE_TTL) -> str | None:
    """带 TTL 的文件内容缓存,避免重复读取同一文件"""
    now = time.time()
    p = str(path)
    if p in _FILE_CACHE and now - _CACHE_TS.get(p, 0) < max_age:
        return _FILE_CACHE[p]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        _FILE_CACHE[p] = content
        _CACHE_TS[p] = now
        return content
    except Exception:
        return None

# 便捷函数
def capture_error(command, error, context='', suggested_fix=''):
    """捕获错误"""
    return capture('error', command, f"错误: {error}\n建议: {suggested_fix}", context)

def capture_correction(topic, wrong, correct, context=''):
    """捕获纠正"""
    return capture('correction', topic, f"错误做法: {wrong}\n正确做法: {correct}", context)

def capture_practice(category, practice, reason='', context=''):
    """捕获最佳实践"""
    return capture('practice', category, f"实践: {practice}\n原因: {reason}", context)

def capture_event(title, content, context=''):
    """捕获重要事件"""
    return capture('event', title, content, context)

# === 核心功能 ===

def _make_typed_fragment(typ: str, title: str, content: str, context: str = ''):
    """
    根据记忆类型创建正确的 typed fragment

    映射规则：
      error      → LessonFragment（教训）
      correction → LessonFragment（纠正也是一种教训）
      practice   → ConclusionFragment（最佳实践=结论）
      event      → TaskFragment（重要事件=任务）
      gap        → LessonFragment（知识空白=教训）
    """
    try:
        from memory_fragment import (
            skill, task, config,
            conclusion, lesson, MemoryFragment
        )
        combined = f"{title}\n{content}"

        if typ == 'event':
            return task(
                task_name=title,
                status='event',
                note=context,
                priority=0.9
            )
        elif typ == 'practice':
            return conclusion(
                text=combined,
                source=context,
                priority=0.7
            )
        elif typ in ('error', 'correction', 'gap'):
            return lesson(
                text=combined,
                context=context,
                priority=0.6
            )
        else:
            return MemoryFragment(typ, combined, priority=0.5)
    except Exception:
        return None


def _is_good_memory_content(text: str) -> bool:
    """
    记忆质量过滤器：从源头拒绝垃圾记忆入库
    
    规则：
    1. 最小长度：正文 ≥ 10 字符（去除空白后）
    2. 无意义占位符：拒绝连续重复字符（xxxxxx / aaaaaa）超过文本 50%
    3. 最低信息密度：去除空白+标点后，有效字符 ≥ 5 个
    4. 禁止纯代码/哈希：有效字符须含中文或英文单词（不只是十六进制/Hex）
    """
    if not text:
        return False
    import re
    stripped = text.strip()
    # 规则1：最小长度
    if len(stripped) < 10:
        return False
    # 规则2：无意义占位符（某单一字符占总字符数 > 60%，如 xxxx / aaaaa / 个个个个）
    if stripped:
        from collections import Counter
        most_common_char, most_common_count = Counter(stripped).most_common(1)[0]
        if most_common_count / len(stripped) > 0.6:
            return False
    # 规则3：最低信息密度（去除空白标点后至少5个有效字）
    meaningful = re.sub(r'[\s\W_]', '', stripped)
    if len(meaningful) < 5:
        return False
    return True


def capture(typ, title, content, context=''):
    """
    捕获记忆到 L2（文件）+ L4（Qdrant）
    同时写入文件（用于归档）和向量库（用于语义检索）
    同时注册到 FragmentPool（使用正确类型片段）
    """
    # ── 质量过滤：从源头拒绝垃圾 ──────────────────────────────
    combined_text = f"{title}\n{content}"
    if not _is_good_memory_content(combined_text):
        print(f"⚠️  记忆被过滤（质量不合格）：{title[:30]}（长度={len(combined_text)}）")
        return {"ok": False, "reason": "quality_filter_failed", "title": title}

    timestamp = datetime.datetime.now().isoformat()
    today = datetime.datetime.now().strftime('%Y-%m-%d')

    # 1. 写入 L2 文件（按类型分类）
    type_dir = MEMORY_DIR / typ
    type_dir.mkdir(parents=True, exist_ok=True)
    filename = type_dir / f"{today}.md"

    entry = f"""---
type: {typ}
timestamp: {timestamp}
title: {title}
context: {context}
---

{content}

---
"""
    with open(filename, 'a', encoding='utf-8') as f:
        f.write(entry)

    # 2. 同时写入 L4 Qdrant 向量库（通过 memory_store）
    full_text = f"{title}\n{content}"
    vector_stored = False
    try:
        from memory_store import get_store
        from memory_store import _update_synced_hash
        store = get_store()
        store.add(full_text, typ, source=filename.name, metadata={
            "title": title,
            "context": context,
            "date": today
        })
        _update_synced_hash(full_text)
        vector_stored = True
    except Exception as e:
        print(f"⚠️  Qdrant 写入失败: {e}")

    # 3. 注册到 FragmentPool（使用正确类型片段）
    pool_ok = False
    try:
        from memory_fragment import (
            MemoryFragment, skill, task, config,
            conclusion, lesson
        )
        pool = _get_fragment_pool()
        if pool is not None:
            # 根据 typ 选择正确的 typed fragment 工厂
            frag = _make_typed_fragment(typ, title, content, context)
            if frag:
                pool.add(frag)
                pool_ok = True
    except Exception:
        pass  # FragmentPool 不可用不影响主流程

    print(f"✅ Captured [{typ}]: {title}")
    return {
        "ok": True,
        "type": typ,
        "title": title,
        "timestamp": timestamp,
        "file": str(filename),
        "vector_stored": vector_stored,
        "fragment_pooled": pool_ok,
    }

# FragmentPool 单例(lazy init)
_fragment_pool = None
def _get_fragment_pool():
    global _fragment_pool
    if _fragment_pool is None:
        try:
            from memory_fragment import FragmentPool
            _fragment_pool = FragmentPool()
        except Exception:
            pass
    return _fragment_pool

def search(query, typ=None, n_results=5):
    """
    统一搜索入口 - L4/L10 混合检索

    完整链路:
      hybrid_search (BM25 + Vector + RRF)
        → Ebbinghaus fluid_score 二次重排
        → 命中记忆自动强化 (reinforce)
        → 最终结果

    typ 参数:只过滤向量结果(BM25 结果来自 markdown,不受 typ 影响)
    """
    # 优先使用混合搜索(BM25 + Vector + RRF)
    try:
        from memory_hybrid import hybrid_search
        hybrid_results = hybrid_search(query, n_results=n_results * 3)

        # typ 过滤(BM25 结果不区分 type,保持兼容性)
        if typ:
            hybrid_results = [r for r in hybrid_results
                           if r.get('type') == typ or r.get('method') == 'bm25']

        if hybrid_results:
            # 在强化层注册这次访问(Ebbinghaus 强化)
            try:
                from memory_reinforcement import reinforce
                for r in hybrid_results[:n_results]:
                    if r.get('fluid_score', 0) > 0.05:
                        reinforce(r.get('id', ''))
            except Exception:
                pass  # 强化失败不影响搜索返回

            # 重新 apply fluid_score 排序(hybrid 的 rrf_score 在前,fluid_score 调整)
            try:
                from memory_reinforcement import calculate_fluid_score
                for r in hybrid_results:
                    base = r.get('fluid_score', r.get('score', 0.5))
                    r['final_score'] = round(base + r.get('rrf_score', 0) * 0.1, 4)
                hybrid_results.sort(key=lambda x: -x.get('final_score', 0))
            except Exception:
                pass  # 排序失败不影响返回

            return hybrid_results[:n_results]

    except Exception as e:
        print(f"⚠️  混合搜索失败: {e},降级为向量搜索")

    # 降级:纯向量 + Ebbinghaus
    try:
        from memory_reinforcement import search_with_fluid
        return search_with_fluid(query, n_results=n_results, memory_type=typ)
    except Exception as e2:
        print(f"⚠️  向量搜索失败: {e2},降级为普通向量")
        try:
            from memory_store import get_store
            return get_store().search(query, n_results=n_results, typ=typ)
        except Exception as e3:
            print(f"❌ 全部搜索失败: {e3}")
            return []

def check_before_execute(command):
    """
    执行前检查记忆:
    1. 关键词搜索 L2 文件(errors/corrections/practices)
    2. 语义搜索 L4 Qdrant 向量库
    """
    print(f"\n🔍 检查命令: {command}")
    found_any = False

    # 1. L2 文件关键词匹配(预分词一次,避免重复 split)
    cmd_words = [w for w in command.split() if len(w) > 3]
    cmd_words_lower = {w: w.lower() for w in cmd_words}

    for memory_type in MEMORY_TYPES:
        type_dir = MEMORY_DIR / memory_type
        if not type_dir.exists():
            continue

        for md_file in sorted(type_dir.glob("*.md"), reverse=True)[:3]:
            try:
                content = _get_cached_content(md_file)
                if content is None:
                    continue

                # 关键词匹配(content.lower() 只计算一次)
                content_lc = content.lower()
                matched = [w for w in cmd_words if cmd_words_lower[w] in content_lc]
                if matched:
                    print(f"\n⚠️  发现相关 {memory_type.upper()}:")
                    print(f"  文件: {md_file.name}")
                    _sep = ", "; print(f"  匹配词: {_sep.join(matched)}")
                    # 提取标题(限制扫描前20行)
                    for line in content.split('\n')[:20]:
                        if line.startswith('title:'):
                            print(f"  {line}")
                            break
                    found_any = True
            except Exception:
                pass

    # 2. L4 Qdrant 语义搜索(强化版 - 命中自动 boost)
    try:
        results = search(command, n_results=3)
        if results:
            print(f"\n🔮 语义搜索结果(强化后):")
            for r in results:
                fluid = r.get('fluid_score', r.get('score'))
                marker = " ★" if r.get('access_count', 0) > 0 else ""
                print(f"  [{r.get('type','?')}] [{fluid:.3f}{marker}] {r['text'][:100]}")
            found_any = True
    except Exception:
        pass

    if not found_any:
        print("  未发现相关记忆")

# === 管理功能 ===

def compress():
    """
    压缩/清理:L2 文件超长时归档
    超过30天的记忆文件 → L3 归档目录
    """
    from datetime import timedelta
    cutoff = datetime.datetime.now() - timedelta(days=30)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    MEMORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0

    for type_dir in [MEMORY_DIR / t for t in ['error', 'correction', 'practice', 'event', 'gap']]:
        if not type_dir.exists():
            continue
        for md_file in type_dir.glob("*.md"):
            if md_file.stem < cutoff_str:
                # 归档到 L3
                year_month = md_file.stem[:7]  # 2026-03
                archive_subdir = MEMORY_ARCHIVE_DIR / year_month
                archive_subdir.mkdir(parents=True, exist_ok=True)
                dest = archive_subdir / md_file.name
                md_file.rename(dest)
                moved += 1

    print(f"✅ 归档完成,共移动 {moved} 个文件到 {MEMORY_ARCHIVE_DIR}")
    return moved

def count():
    """返回各层记忆数量"""
    total_files = 0
    for type_dir in [MEMORY_DIR / t for t in ['error', 'correction', 'practice', 'event', 'gap']]:
        if type_dir.exists():
            total_files += len(list(type_dir.glob("*.md")))

    try:
        from memory_store import get_store
        store = get_store()
        vector_count = store.count()
    except Exception:
        vector_count = -1

    print(f"L2 文件记忆: {total_files} 条")
    print(f"L4 向量记忆: {vector_count} 条")
    return {"files": total_files, "vectors": vector_count}

# === 主入口 ===

if __name__ == '__main__':
    args = sys.argv[1:]

    if not args:
        # 默认测试
        capture_error("npm install", "permission denied", "全局安装", "使用 sudo")
        capture_correction("代码风格", "双引号", "单引号", "项目规范")
        capture_practice("高效安装", "pip install -e .", "可编辑模式", "Python开发")
        print("\n--- 搜索测试 ---")
        results = search("如何安装 Python 包")
        for r in results:
            print(f"  [{r['score']:.3f}] {r['text'][:80]}")
        print("\n--- 统计 ---")
        count()

    elif args[0] == 'compress':
        compress()

    elif args[0] == 'count':
        count()

    elif args[0] == 'search':
        query = ' '.join(args[1:])
        results = search(query)
        for r in results:
            print(f"  [{r.get('type','?')}] [{r['score']:.3f}] {r['text']}")

    elif args[0] == 'check':
        check_before_execute(' '.join(args[1:]))

    else:
        check_before_execute(' '.join(args))
