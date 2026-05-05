#!/usr/bin/env python3
"""
技能健康检查器
扫描 skills 目录，检测低频/废弃/异常技能
读取 skill-usage.json 判断使用频率
阈值: days_low_frequency=30, days_abandoned=180

CLI:
    python3 skill_health_checker.py          # 写入报告
    python3 skill_health_checker.py --report   # 仅输出报告路径
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
USAGE_PATH = MEMORY_DIR / "skill-usage.json"
REPORT_PATH = MEMORY_DIR / "skill-health-report.md"

# Thresholds (days)
DAYS_LOW_FREQUENCY = 30
DAYS_ABANDONED = 180

# Skills directories to scan
SKILL_SEARCH_PATHS = [
    WORKSPACE / "skills",
    Path.home() / ".openclaw" / "npm" / "node_modules" / "@openclaw" / "feishu",
    Path.home() / ".local" / "share" / "pnpm" / "global" / "5" / ".pnpm",
]

REVIEW_THRESHOLD = 20


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def days_ago(ts: float) -> int:
    return int((now_ts() - ts) / 86400)


def _load_usage() -> dict:
    if USAGE_PATH.exists():
        try:
            with open(USAGE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _scan_skills_dirs() -> dict:
    """Scan skill directories, return {skill_name: {path, mtime}}."""
    skills = {}
    for base in SKILL_SEARCH_PATHS:
        if not base.exists():
            continue
        if base.is_file():
            continue
        for item in base.iterdir():
            # Handle scoped packages like @openclaw/feishu
            if item.is_dir():
                skill_dir = item
                name = item.name
            elif item.is_file() and item.suffix == ".md" and item.stem.startswith("SKILL"):
                # SKILL.md or SKILL_v2.md inside a skill dir
                parent = item.parent
                if parent.name.startswith("@"):
                    name = f"{parent.name}/{parent.name}"
                    skill_dir = parent
                else:
                    name = parent.name
                    skill_dir = parent
            else:
                continue

            # Try to read skill name from SKILL.md
            skill_md = None
            for candidate in [item, item.parent]:
                if candidate.is_dir():
                    for f in candidate.iterdir():
                        if f.name.upper().startswith("SKILL") and f.suffix == ".md":
                            skill_md = f
                            break
                if skill_md:
                    break

            if skill_md:
                mtime = skill_md.stat().st_mtime
            else:
                mtime = item.stat().st_mtime if item.exists() else 0

            skills[name] = {
                "path": str(item if item.is_dir() else item.parent),
                "mtime": mtime,
                "skill_md": str(skill_md) if skill_md else None,
            }
    return skills


def _check_single_skill(name: str, info: dict, usage: dict) -> dict:
    usage_entry = usage.get(name, {})
    last_used_ts = usage_entry.get("last_used") if isinstance(usage_entry, dict) else None
    use_count = usage_entry.get("count", 0) if isinstance(usage_entry, dict) else 0

    result = {
        "name": name,
        "path": info["path"],
        "last_used_ts": last_used_ts,
        "days_since_used": None,
        "use_count": use_count,
        "status": "unknown",
        "notes": [],
    }

    if last_used_ts:
        days = days_ago(last_used_ts)
        result["days_since_used"] = days
        if days >= DAYS_ABANDONED:
            result["status"] = "abandoned"
            result["notes"].append(f"超过 {DAYS_ABANDONED} 天未使用 ({days} 天)")
        elif days >= DAYS_LOW_FREQUENCY:
            result["status"] = "low_frequency"
            result["notes"].append(f"低频使用 ({days} 天未使用)")
        else:
            result["status"] = "active"
    else:
        # No usage record at all
        mtime_days = days_ago(info["mtime"])
        if mtime_days >= DAYS_ABANDONED:
            result["status"] = "abandoned"
            result["notes"].append(f"无使用记录，文件修改于 {mtime_days} 天前")
        elif mtime_days >= DAYS_LOW_FREQUENCY:
            result["status"] = "low_frequency"
            result["notes"].append(f"无使用记录，文件修改于 {mtime_days} 天前")
        else:
            result["status"] = "unknown"
            result["notes"].append("无使用记录")

    return result


def check_all() -> list:
    skills = _scan_skills_dirs()
    usage = _load_usage()
    results = []
    for name, info in sorted(skills.items()):
        results.append(_check_single_skill(name, info, usage))
    return results


def generate_report() -> str:
    results = check_all()
    lines = [
        "# 技能健康检查报告",
        "",
        f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"低频阈值: {DAYS_LOW_FREQUENCY} 天",
        f"废弃阈值: {DAYS_ABANDONED} 天",
        "",
    ]

    # Summary
    active = [r for r in results if r["status"] == "active"]
    low = [r for r in results if r["status"] == "low_frequency"]
    abandoned = [r for r in results if r["status"] == "abandoned"]
    unknown = [r for r in results if r["status"] == "unknown"]

    lines += [
        "## 概览",
        f"- 总技能数: {len(results)}",
        f"- 活跃: {len(active)}",
        f"- 低频: {len(low)}",
        f"- 废弃: {len(abandoned)}",
        f"- 未知: {len(unknown)}",
        "",
    ]

    # Detailed sections
    if abandoned:
        lines += ["## ⚠️ 废弃技能", ""]
        for r in abandoned:
            days = r["days_since_used"]
            lines.append(f"- **{r['name']}** (`{r['path']}`)")
            lines.append(f"  - 状态: 废弃")
            if days:
                lines.append(f"  - {days} 天未使用")
            for note in r["notes"]:
                lines.append(f"  - {note}")
            lines.append("")
        lines.append("")

    if low:
        lines += ["## 📉 低频技能", ""]
        for r in low:
            lines.append(f"- **{r['name']}** (`{r['path']}`)")
            if r["days_since_used"]:
                lines.append(f"  - {r['days_since_used']} 天未使用")
            lines.append(f"  - 使用次数: {r['use_count']}")
            lines.append("")

    if active:
        lines += ["## ✅ 活跃技能", ""]
        for r in active:
            lines.append(f"- **{r['name']}**")
            if r["days_since_used"] is not None:
                lines.append(f"  - {r['days_since_used']} 天前使用")
            lines.append(f"  - 使用次数: {r['use_count']}")
        lines.append("")

    if unknown:
        lines += ["## ❓ 未知技能（无使用记录）", ""]
        for r in unknown:
            lines.append(f"- **{r['name']}** (`{r['path']}`)")
            for note in r["notes"]:
                lines.append(f"  - {note}")
        lines.append("")

    return "\n".join(lines)


def write_report() -> str:
    report = generate_report()
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(MEMORY_DIR), suffix=".md")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(report)
        shutil.move(tmp_path, str(REPORT_PATH))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return str(REPORT_PATH)


def main():
    if "--report" in sys.argv:
        path = write_report()
        print(path)
    else:
        path = write_report()
        print(f"报告已写入: {path}")


if __name__ == "__main__":
    main()
