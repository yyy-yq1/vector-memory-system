#!/usr/bin/env python3
"""
上下文监控
监控当前 session 文件 token 使用率
阈值: 80% 时触发压缩
token 估算: chinese*2 + english*1.3
会话文件: agents/main/sessions/ 下的 .jsonl 文件
context window: 200000 tokens

CLI:
  python3 context_monitor.py status   # 查看状态
  python3 context_monitor.py once     # 单次检查
  python3 context_monitor.py monitor  # 持续监控
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

# === 路径配置 ===
WORKSPACE = Path.home() / ".openclaw/workspace"
SESSION_DIR = Path.home() / ".openclaw" / "agents/main/sessions"
CONTEXT_WINDOW = 200000  # tokens
WARNING_THRESHOLD = 0.80  # 80%


def estimate_tokens(text: str) -> int:
    """
    估算 token 数量
    中文 *2, 拉丁字母 *1.3, 其他字符(标点/数字/空格) *1.0
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    english_chars = sum(1 for c in text if ('a' <= c <= 'z') or ('A' <= c <= 'Z'))
    other_chars = len(text) - chinese_chars - english_chars
    return int(chinese_chars * 2 + english_chars * 1.3 + other_chars * 1.0)


def get_latest_session() -> Path:
    """获取最新的 session 文件"""
    if not SESSION_DIR.exists():
        return None
    sessions = list(SESSION_DIR.glob("*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def get_current_tokens() -> int:
    """获取当前 session 的 token 估算"""
    session_file = get_latest_session()
    if not session_file:
        return 0

    total = 0
    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # OpenClaw session: {"type":"message","message":{"role":"...","content":[{"type":"text","text":"..."}]}}
                # 正确提取：obj.message.content[].text
                msg_obj = obj.get("message", {})  # 顶层 message 字段（可能不存在）
                if isinstance(msg_obj, dict):
                    content_list = msg_obj.get("content", [])
                else:
                    content_list = obj.get("content", [])

                if isinstance(content_list, list):
                    parts = []
                    for block in content_list:
                        if isinstance(block, dict) and "text" in block:
                            parts.append(str(block["text"]))
                        elif isinstance(block, str):
                            parts.append(block)
                    content = " ".join(parts)
                elif isinstance(content_list, str):
                    content = content_list
                else:
                    content = str(content_list)
                total += estimate_tokens(content)
            except (json.JSONDecodeError, KeyError):
                pass
    return total


def get_session_info() -> dict:
    """获取 session 详细信息"""
    session_file = get_latest_session()
    if not session_file:
        return {"exists": False}

    stat = session_file.stat()
    lines = 0
    with open(session_file, "r", encoding="utf-8") as f:
        lines = sum(1 for line in f if line.strip())

    tokens = get_current_tokens()
    usage_ratio = tokens / CONTEXT_WINDOW

    return {
        "exists": True,
        "file": session_file.name,
        "size": stat.st_size,
        "lines": lines,
        "tokens": tokens,
        "usage_ratio": usage_ratio,
        "warning": usage_ratio >= WARNING_THRESHOLD,
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def status():
    """查看状态"""
    info = get_session_info()
    if not info["exists"]:
        print("无活跃 session")
        return

    bar_len = 40
    ratio = info["usage_ratio"]
    filled = int(ratio * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(f"Session: {info['file']}")
    print(f"行数: {info['lines']}, 大小: {info['size']} bytes")
    print(f"Token 估算: {info['tokens']} / {CONTEXT_WINDOW}")
    print(f"使用率: {ratio*100:.1f}%")
    print(f"[{bar}]")

    if info["warning"]:
        print(f"⚠️  警告: 使用率超过 {WARNING_THRESHOLD*100:.0f}% 阈值，建议压缩上下文")
    else:
        print(f"✅ 状态正常")


def once():
    """单次检查"""
    info = get_session_info()
    if not info["exists"]:
        print("no-session")
        return

    ratio = info["usage_ratio"]
    print(f"{info['tokens']}\t{ratio*100:.2f}\t{'WARNING' if info['warning'] else 'OK'}")


def monitor(interval: int = 60):
    """
    持续监控
    每 interval 秒检查一次
    """
    print(f"开始监控上下文使用率 (间隔 {interval}s, 阈值 {WARNING_THRESHOLD*100:.0f}%)")
    print("按 Ctrl+C 停止")
    try:
        while True:
            info = get_session_info()
            if info["exists"]:
                ratio = info["usage_ratio"]
                ts = datetime.now().strftime("%H:%M:%S")
                status_str = "⚠️ WARNING" if info["warning"] else "OK"
                print(f"[{ts}] tokens={info['tokens']} usage={ratio*100:.1f}% [{status_str}]")
                if info["warning"]:
                    print(f"   → 建议触发上下文压缩")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 无活跃 session")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n监控已停止")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status()

    elif cmd == "once":
        once()

    elif cmd == "monitor":
        interval = 60
        if len(sys.argv) >= 3:
            interval = int(sys.argv[2])
        monitor(interval)

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
