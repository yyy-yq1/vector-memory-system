#!/usr/bin/env python3
"""
智能记忆提取引擎 v1.1
自动从每日日志中提取有价值记忆，写入 L2 文件 + L4 Qdrant

去重策略：
  - 按内容 hash 去重（synced_hashes）
  - 同一来源文件内去重（避免重复追加）
  - 内容质量过滤（最短长度、最少字符密度）

类型优先级（同一片段命中多类型时）：
  error > correction > practice > event > gap
"""
import sys
import re
import json
import datetime
import hashlib
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from memory_consts import (
    MEMORY_DIR, VECTORIZE_STATE_FILE, TURNS_STATE_FILE,
    TURN_THRESHOLD, MAX_CONTENT_LEN
)
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state


# ─────────────────────────────────────────────────────────────
# 提取规则（优先级：数字越小优先级越高）
# ─────────────────────────────────────────────────────────────

EXTRACTION_RULES = [
    {
        "type": "error",
        "priority": 1,
        "patterns": [
            # 错误标记
            (re.compile(r'\b(error|exception|failed|failure|❌|失败|异常)\b', re.IGNORECASE), 0.3),
            # 堆栈跟踪
            (re.compile(r'Traceback \(most recent call last\)'), 0.5),
            # 命令失败
            (re.compile(r'Command.*exited with code [1-9]|returned non-zero|non-zero exit', re.IGNORECASE), 0.4),
            # 待修复标记
            (re.compile(r'待修复|待解决|pending.*fix|bug[:\s]', re.IGNORECASE), 0.2),
            # 警告/严重
            (re.compile(r'⚠️|warning|warn', re.IGNORECASE), 0.1),
        ],
        "min_content_len": 30,
        "require_keywords": None,
    },
    {
        "type": "correction",
        "priority": 2,
        "patterns": [
            # 错误→正确映射
            (re.compile(r'错误做法.*正确做法|wrong.*correct|❌.*✅|\[x\].*→|\[ \].*→', re.DOTALL | re.IGNORECASE), 0.5),
            # 修复记录
            (re.compile(r'修复|fix|bug|问题.*解决|解决.*问题', re.IGNORECASE), 0.2),
            # 表格格式的修复（模块|问题|修复）
            (re.compile(r'\|\s*\w+\s*\|\s*(?:问题|错误|待修复|bug)', re.IGNORECASE), 0.3),
        ],
        "min_content_len": 40,
        "require_keywords": None,
    },
    {
        "type": "practice",
        "priority": 3,
        "patterns": [
            # 成功验证
            (re.compile(r'✅.*(?:完成|成功|通过|验证)', re.IGNORECASE), 0.3),
            # 代码块（可执行的命令/配置）
            (re.compile(r'```(?:bash|python|sql|json|yaml|sh|npm|git|curl)', re.IGNORECASE), 0.4),
            # 配置/方法描述
            (re.compile(r'(?:配置|安装|部署|搭建|运行|启动|方法|命令)[:\s]+', re.IGNORECASE), 0.2),
            # 技术栈描述
            (re.compile(r'(?:技术栈|tech stack|框架|framework|路径)[:\s]+\`', re.IGNORECASE), 0.3),
        ],
        "min_content_len": 40,
        "require_keywords": ["完成", "成功", "验证", "配置", "安装", "部署", "方法", "命令", "路径", "技术栈", "版本"],
    },
    {
        "type": "event",
        "priority": 4,
        "patterns": [
            # 里程碑
            (re.compile(r'^#{1,3}\s+.*(?:完成|上线|发布|达成|里程碑)', re.MULTILINE | re.IGNORECASE), 0.4),
            # 关键决策
            (re.compile(r'(?:决定|选择|采用|方案|架构)[:\s]+', re.IGNORECASE), 0.3),
            # 系统状态
            (re.compile(r'(?:状态|status|版本).*(?:正常|✅|ok|running)', re.IGNORECASE), 0.2),
        ],
        "min_content_len": 30,
        "require_keywords": None,
    },
    {
        "type": "gap",
        "priority": 5,
        "patterns": [
            # 未知/待学
            (re.compile(r'未知|待学习|不清楚|未完成|pending.*learn|not yet', re.IGNORECASE), 0.3),
            # 局限
            (re.compile(r'局限|限制|constraint|limitation|待验证', re.IGNORECASE), 0.2),
        ],
        "min_content_len": 20,
        "require_keywords": None,
    },
]

# 从日志行提取标题
TITLE_RE = re.compile(r'^#{1,3}\s+(.+)$', re.MULTILINE)
CODE_BLOCK_RE = re.compile(r'```[\w]*\n.*?```', re.DOTALL)


def _clean_content(text: str) -> str:
    """清理内容：去掉 markdown 标题行和过多空行"""
    lines = text.split('\n')
    cleaned = []
    prev_empty = False
    for line in lines:
        stripped = line.strip()
        # 跳过纯标题行（将被 title 捕获）
        if re.match(r'^#{1,6}\s*$', stripped):
            continue
        # 跳过纯分隔符
        if re.match(r'^[|=\-*_]{3,}$', stripped):
            continue
        # 跳过单独的命令提示符行
        if re.match(r'^\$ $', stripped):
            continue
        # 压缩连续空行
        if not stripped:
            if not prev_empty:
                cleaned.append('')
                prev_empty = True
            continue
        prev_empty = False
        cleaned.append(line)

    result = '\n'.join(cleaned).strip()
    # 截断过长内容
    if len(result) > MAX_CONTENT_LEN:
        result = result[:MAX_CONTENT_LEN] + f"\n...[截断，原始 {len(result)} 字符]"
    return result


def _extract_title(lines: list[str], start: int, end: int) -> str:
    """从片段上下文中提取标题"""
    snippet = '\n'.join(lines[start:end])
    # 先找最近的 markdown 标题
    m = TITLE_RE.search(snippet)
    if m:
        return m.group(1).strip()
    # 回退：找第一个非空非装饰行
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and len(stripped) > 4:
            if not re.match(r'^[|=\-*_]{2,}$', stripped):
                return stripped[:50].strip()
    return "未命名"


def _section_boundaries(content: str) -> list[tuple[int, int, str]]:
    """
    将内容按 markdown 标题分段，返回 [(start_line, end_line, title), ...]
    """
    lines = content.split('\n')
    boundaries = []
    i = 0
    current_start = 0
    current_title = "(开头)"

    while i < len(lines):
        m = re.match(r'^(#{1,3})\s+(.+)$', lines[i])
        if m:
            if i > current_start:
                boundaries.append((current_start, i, current_title))
            current_start = i
            current_title = m.group(2).strip()
        i += 1

    boundaries.append((current_start, len(lines), current_title))
    return boundaries


def _char_density(text: str) -> float:
    """计算字符密度（有意义字符占比）"""
    if not text:
        return 0.0
    meaningful = sum(1 for c in text if c.isalnum() or c in ' .,;:!?-_*`\n')
    return meaningful / max(len(text), 1)


def _score_and_rank_snippets(snippets: list[dict]) -> list[dict]:
    """
    对片段打分并去重，保留每个唯一内容的最佳匹配
    返回按分数排序的列表
    """
    # 按归一化文本 hash 分组
    seen = {}
    for sn in snippets:
        # 用归一化文本（去除多余空白）计算 hash
        norm = re.sub(r'\s+', ' ', sn['raw_text']).strip()
        h = hashlib.sha256(norm.encode('utf-8')).hexdigest()[:32]

        if h not in seen:
            seen[h] = sn
        else:
            # 保留分数更高的
            if sn['score'] > seen[h]['score']:
                seen[h] = sn

    ranked = sorted(seen.values(), key=lambda x: (-x['score'], -len(x['raw_text'])))
    return ranked


def extract_from_content(content: str, source_name: str = '') -> list[dict]:
    """
    从文本内容中提取有价值的记忆片段
    返回 [{type, title, context, score}, ...]
    """
    lines = content.split('\n')
    boundaries = _section_boundaries(content)
    all_snippets = []

    for sec_start, sec_end, sec_title in boundaries:
        sec_text = '\n'.join(lines[sec_start:sec_end])

        # 跳过极短章节
        if len(sec_text) < 15:
            continue

        # 计算每个类型的匹配分
        best_match = None
        best_score = 0.0

        for rule in EXTRACTION_RULES:
            rule_type = rule['type']
            score = 0.0

            for pattern, weight in rule['patterns']:
                if pattern.search(sec_text):
                    score += weight

            # 内容长度奖励
            if len(sec_text) > 100:
                score += 0.1
            if len(sec_text) > 300:
                score += 0.1

            # 关键字过滤
            if rule.get('require_keywords'):
                has_keyword = any(kw in sec_text for kw in rule['require_keywords'])
                if not has_keyword:
                    score *= 0.5  # 降低分数但不直接排除

            # 最短长度过滤
            if len(sec_text) < rule['min_content_len']:
                continue

            # 字符密度过滤
            density = _char_density(sec_text)
            if density < 0.3:
                continue

            if score > best_score:
                best_score = score
                best_match = rule_type

        if best_match and best_score >= 0.2:
            title = _extract_title(lines, sec_start, sec_end)
            context = _clean_content(sec_text)

            all_snippets.append({
                "type": best_match,
                "title": title,
                "context": context,
                "raw_text": sec_text,
                "score": best_score,
                "source": source_name,
            })

    # 去重 + 排序
    return _score_and_rank_snippets(all_snippets)


# ─────────────────────────────────────────────────────────────
# 存储操作
# ─────────────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]


def _load_synced() -> set:
    state = _load_vectorize_state()
    return set(state.get("synced_hashes", []))


def _save_hash(text: str):
    h = _content_hash(text)
    state = _load_vectorize_state()
    synced = set(state.get("synced_hashes", []))
    synced.add(h)
    state["synced_hashes"] = list(synced)
    state["last_sync"] = datetime.datetime.now().isoformat()
    _save_vectorize_state(state)


def _write_l2(typ: str, title: str, context: str, source: str = '') -> tuple[Path, bool]:
    """
    写入 L2 文件
    返回 (filepath, is_new)
    is_new=False 表示内容已存在（防重复追加）
    """
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.datetime.now().isoformat()
    type_dir = MEMORY_DIR / typ
    type_dir.mkdir(parents=True, exist_ok=True)
    filepath = type_dir / f"{today}.md"

    entry_text = f"""---
type: {typ}
timestamp: {timestamp}
title: {title}
context: {source}
---

{context}

---
"""
    # 检查是否已存在
    is_new = True
    if filepath.exists():
        existing = filepath.read_text(encoding='utf-8')
        if entry_text.strip() in existing:
            is_new = False

    if is_new:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(entry_text)

    return filepath, is_new


def _write_qdrant(text: str, typ: str, title: str, source: str = '') -> bool:
    """写入 Qdrant，返回是否成功"""
    try:
        store = get_store()
        store.add(text, typ, source=source, metadata={"title": title})
        return True
    except Exception as e:
        print(f"    ⚠️  Qdrant: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 核心提取函数
# ─────────────────────────────────────────────────────────────

def extract_file(filepath: Path, force: bool = False) -> dict:
    """
    从单个日志文件提取记忆
    返回 {"extracted": [...], "skipped_duplicates": N, "skipped_short": N}
    """
    content = filepath.read_text(encoding='utf-8')
    snippets = extract_from_content(content, source_name=filepath.name)
    synced = set() if force else _load_synced()

    result = {"extracted": [], "skipped_duplicates": 0, "skipped_short": 0, "file": filepath.name}
    seen_hashes = set()  # 本次运行内的去重

    for sn in snippets:
        text_to_save = f"{sn['title']}\n{sn['context']}"

        # 质量再过滤
        if len(text_to_save.strip()) < 20:
            result["skipped_short"] += 1
            continue

        # hash 去重（全局）
        h = _content_hash(text_to_save)
        if h in synced or h in seen_hashes:
            result["skipped_duplicates"] += 1
            continue
        seen_hashes.add(h)

        # 写入 L2
        l2_path, is_new = _write_l2(sn['type'], sn['title'], sn['context'], source=sn['source'])

        if not is_new:
            result["skipped_duplicates"] += 1
            continue

        # 写入 Qdrant
        qdrant_ok = _write_qdrant(text_to_save[:MAX_CONTENT_LEN], sn['type'], sn['title'], source=sn['source'])

        # 更新状态
        _save_hash(text_to_save)
        synced.add(h)

        result["extracted"].append({
            "type": sn['type'],
            "title": sn['title'][:50],
            "qdrant": "✅" if qdrant_ok else "⚠️",
            "score": round(sn['score'], 2),
        })

    return result


def extract_daily_logs(days: int = 7, force: bool = False) -> dict:
    """从最近 N 天的每日日志中批量提取"""
    today = datetime.datetime.now()
    results = {"files": [], "total_extracted": 0, "total_skipped": 0}

    for i in range(days):
        date = today - datetime.timedelta(days=i)
        log_file = MEMORY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if not log_file.exists():
            continue

        fr = extract_file(log_file, force=force)
        results["files"].append(fr)
        results["total_extracted"] += len(fr["extracted"])
        results["total_skipped"] += fr["skipped_duplicates"] + fr["skipped_short"]

        if fr["extracted"]:
            print(f"📄 {log_file.name}: +{len(fr['extracted'])} 条 | 跳过 {fr['skipped_duplicates']} 重复")

    return results


def scan_report(days: int = 7) -> dict:
    """只扫描不写入，返回预览报告"""
    today = datetime.datetime.now()
    report = {
        "total_snippets": 0,
        "type_counts": {"error": 0, "practice": 0, "correction": 0, "event": 0, "gap": 0},
        "files": [],
    }

    for i in range(days):
        date = today - datetime.timedelta(days=i)
        log_file = MEMORY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if not log_file.exists():
            continue

        content = log_file.read_text(encoding='utf-8')
        snippets = extract_from_content(content, source_name=log_file.name)

        file_data = {"file": log_file.name, "snippets": []}
        for sn in snippets:
            report["type_counts"][sn["type"]] += 1
            report["total_snippets"] += 1
            file_data["snippets"].append({
                "type": sn["type"],
                "title": sn["title"][:50],
                "score": round(sn["score"], 2),
                "preview": sn["context"][:60].replace('\n', ' '),
            })

        if file_data["snippets"]:
            report["files"].append(file_data)

    return report


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='智能记忆提取引擎 v1.1')
    parser.add_argument('--days', type=int, default=7, help='扫描最近 N 天（默认7）')
    parser.add_argument('--force', action='store_true', help='强制提取，跳过全局去重')
    parser.add_argument('--scan', action='store_true', help='只扫描预览，不写入')
    parser.add_argument('--llm', action='store_true', help='使用 MiniMax LLM 智能提取（更高质量）')
    args = parser.parse_args()

    if args.scan:
        print("🔍 扫描模式 - 预览提取结果（不写入）\n")
        report = scan_report(days=args.days)
        print(f"📊 可提取片段: {report['total_snippets']} 个")
        for t, n in report["type_counts"].items():
            if n:
                print(f"  [{t}]: {n}")
        print()
        for fd in report["files"]:
            print(f"\n📄 {fd['file']}")
            for sn in fd["snippets"]:
                bar = "█" * int(sn["score"] * 10)
                print(f"  [{sn['type']:10s}] {bar} {sn['title']}")
                print(f"               → {sn['preview']}")

    elif args.llm:
        from llm_extract import llm_extract_file
        print(f"🚀 LLM 智能提取最近 {args.days} 天日志（MiniMax M2.7）")
        today = datetime.datetime.now()
        total_written = 0
        for i in range(args.days):
            date = today - datetime.timedelta(days=i)
            log_file = MEMORY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
            if not log_file.exists():
                continue
            print(f"\n📄 {log_file.name}: MiniMax LLM 提取中...")
            result = llm_extract_file(log_file, force=args.force)
            total_written += result.get('written', 0)
        print(f"\n✅ LLM 提取完成，共写入 {total_written} 条新记忆")

    else:
        print(f"🚀 提取最近 {args.days} 天日志中的有价值记忆")
        if args.force:
            print("⚠️  force 模式：跳过全局去重")
        print()

        results = extract_daily_logs(days=args.days, force=args.force)

        print(f"\n✅ 共提取 {results['total_extracted']} 条新记忆")
        print(f"   跳过 {results['total_skipped']} 条（已存在/内容过短）")

        # 统计向量库
        try:
            store = get_store()
            vc = store.count()
            print(f"\n📊 L4 向量总数: {vc}")
        except:
            pass

        synced = _load_synced()
        print(f"📊 synced_hashes: {len(synced)}")
