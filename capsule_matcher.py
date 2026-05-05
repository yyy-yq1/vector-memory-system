#!/usr/bin/env python3
"""
胶囊匹配器（关键词匹配）
读取 capsules.json，匹配 trigger_keywords

CLI:
  python3 capsule_matcher.py "<task>"
"""

import json
import sys
from pathlib import Path

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
MEMORY_DIR = WORKSPACE / "memory"
CAPSULES_FILE = MEMORY_DIR / "capsules.json"


def load_capsules():
    if not CAPSULES_FILE.exists():
        return []
    with open(CAPSULES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("capsules", [])


def match_capsules(task: str) -> list:
    """
    匹配任务与胶囊的触发词
    返回匹配到的胶囊列表，按匹配分数排序
    """
    capsules = load_capsules()
    task_lower = task.lower()
    matched = []

    for capsule in capsules:
        keywords = capsule.get("trigger_keywords", [])
        score = 0
        hit_keywords = []

        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in task_lower:
                score += 1
                hit_keywords.append(kw)

        if score > 0:
            matched.append({
                "name": capsule["name"],
                "score": score,
                "hit_keywords": hit_keywords,
                "type": capsule.get("type", "unknown"),
                "maturity": capsule.get("maturity", "draft"),
            })

    # 按分数降序
    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched


def main():
    if len(sys.argv) < 2:
        print("用法: capsule_matcher.py \"<task>\"")
        sys.exit(1)

    task = sys.argv[1]
    matched = match_capsules(task)

    if not matched:
        print(f"未匹配到任何胶囊")
        print(f"任务: {task}")
        return

    print(f"任务: {task}")
    print(f"\n匹配到 {len(matched)} 个胶囊:")
    print(f"{'胶囊名':<30} {'类型':<15} {'成熟度':<10} {'命中关键词'}")
    print("-" * 80)
    for m in matched:
        hits = ", ".join(m["hit_keywords"])
        print(f"{m['name']:<30} {m['type']:<15} {m['maturity']:<10} {hits}")


if __name__ == "__main__":
    main()
