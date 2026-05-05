#!/usr/bin/env python3
"""
LLM 智能记忆提取器 v1.2
使用 MiniMax API 从原始文本中提取结构化记忆

变更日志 v1.2（参考 MiniMax 官方文档）:
  - 精确错误码处理（errorcode.md 9类错误，区分重试/不重试）
  - 指数退避 + jitter（避免惊群）
   - httpx 超时：connect=10s / read=120s（适配 max_tokens=1024 输出）
  - 主动缓存 system prompt（同一进程内重复调用受益）
  - 降低 max_tokens: 2048 → 1024（提取输出通常 <1KB，减少服务端处理时间）
  - 增加 HTTP 429 处理（速率限制）
  - streaming 支持（预留）

参考:
  https://platform.minimaxi.com/docs/api-reference/errorcode.md
  https://platform.minimaxi.com/docs/guides/rate-limits.md
  https://platform.minimaxi.com/docs/api-reference/text-prompt-caching.md
"""
import sys
import json
import datetime
import hashlib
import time
import random
import httpx
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from memory_consts import MEMORY_DIR, MAX_CONTENT_LEN
from memory_store import get_store, _load_vectorize_state, _save_vectorize_state

# ─────────────────────────────────────────────────────────────
# MiniMax 官方错误码（来自 errorcode.md）
# ─────────────────────────────────────────────────────────────
#
# 可重试（临时性服务端问题或速率限制）：
#   0     - 成功
#   1000  - 系统默认错误（"请稍后再试"）
#   1001  - 请求超时（"请稍后再试"）
#   1002  - 请求频率超限（"请稍后再试"）
#   1033  - 下游服务错误（"请稍后再试"）
#
# 不可重试（客户端错误或资源问题）：
#   1004  - 未授权 / API Key 错误
#   1008  - 余额不足
#   1026  - 输入内容涉敏
#   1027  - 输出内容涉敏
#   1039  - Token 限制（max_tokens 超出上限）
#   1041  - 连接数限制
#   1042  - 非法字符超过 10%
#   1043  - ASR 相似度检查失败
#   1044  - 克隆提示词相似度检查失败
#   2013  - 参数错误
#   20132 - 语音克隆参数错误
#   2037  - 语音时长不符合要求
#   2038  - 语音克隆功能被禁用
#   2039  - voice_id 重复
#   2042  - 无权访问该 voice_id
#   2045  - 请求频率增长超限（骤增骤减）
#   2049  - 无效的 API Key
#   2056  - Token Plan 资源限制
# ─────────────────────────────────────────────────────────────

# 速率限制（来自 rate-limits.md）：
#   免费用户: 20 RPM / 1M TPM
#   付费用户: 500 RPM / 20M TPM
#   错误码 1002 = "请求频率超限"，2045 = "请求频率增长超限"
#
# 策略：检测到速率限制后，等待 60s 再重试（超过 1 分钟窗口）

RETRYABLE_CODES = {0, 1000, 1001, 1002, 1033}
NO_RETRY_CODES = {
    1004,  # 未授权
    1008,  # 余额不足
    1026,  # 输入涉敏
    1027,  # 输出涉敏
    1039,  # Token 限制
    1041,  # 连接数限制
    1042,  # 非法字符
    1043,  # ASR 检查失败
    1044,  # 克隆检查失败
    2013,  # 参数错误
    20132, # 语音参数错误
    2037, 2038, 2039, 2042,  # 语音克隆相关
    2045,  # 频率增长超限（需较长冷却）
    2049,  # 无效 Key
    2056,  # Token Plan 限制
}
MAX_RETRIES = 3
# 速率限制冷却时间（秒），超过 1 分钟 RPM 窗口
RATE_LIMIT_COOL = 60


def _load_api_key() -> str:
    """获取 MiniMax API key（mmx CLI 配置优先）"""
    import os, json

    mmx_cfg = Path.home() / '.mmx' / 'config.json'
    if mmx_cfg.exists():
        try:
            data = json.loads(mmx_cfg.read_text())
            key = data.get('api_key', '')
            if key:
                return key
        except Exception:
            pass
    return os.environ.get('MINIMAX_API_KEY', '')


# ─────────────────────────────────────────────────────────────
# System prompt 缓存（减少重复 token 开销，MiniMax 自动缓存相同上下文）
# ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = "你是一个精确的记忆提取助手。只输出 JSON，不输出其他内容。"


EXTRACTION_PROMPT = (
    "你是一个记忆提取专家。从给定的文本中提取所有有价值的信息，输出JSON数组。\n\n"
    "输出格式（严格JSON数组）：\n"
    "[\n"
    "  {{\n"
    "    \"type\": \"error\" | \"practice\" | \"correction\" | \"event\" | \"gap\",\n"
    "    \"title\": \"简短标题（少于30字）\",\n"
    "    \"content\": \"详细描述（50-300字）\",\n"
    "    \"importance\": 0.0-1.0（重要性评分）\n"
    "  }}, ...\n"
    "]\n\n"
    "类型说明：\n"
    "- error: 错误、失败、异常、bug\n"
    "- correction: 错误做法→正确做法、修复记录\n"
    "- practice: 有效的命令、配置、方法、最佳实践、验证成功的方案\n"
    "- event: 里程碑、关键决策、系统变更、状态更新\n"
    "- gap: 知识盲区、待学习、未知领域\n\n"
    "要求：\n"
    "1. importance: error/correction 通常 0.7-1.0，practice 通常 0.5-0.9\n"
    "2. content 要具体，包含命令/配置/数值等细节\n"
    "3. 不要提取纯情感、无意义闲聊\n"
    "4. 最多提取 10 条最重要的记忆\n"
    "5. 只输出 JSON 数组，不要其他文字\n\n"
    "需要提取的文本：\n"
    "{text}\n"
)


def extract_with_llm(
    text: str,
    model: str = "MiniMax-M2.7-highspeed",
    max_retries: int = MAX_RETRIES,
) -> list[dict]:
    """
    使用 MiniMax LLM 从文本中提取结构化记忆

    特性：
      - 精确错误码处理（对照 errorcode.md）
      - 指数退避 + jitter（burst protection）
      - httpx 超时：connect=10s / read=120s
      - 速率限制后冷却 60s（跨过 RPM 窗口）
      - 自动缓存 system prompt（MiniMax 端自动命中缓存）
    """
    api_key = _load_api_key()
    if not api_key:
        print("⚠️  未找到 MiniMax API Key")
        return []

    prompt = EXTRACTION_PROMPT.format(text=text[:4000])

    # httpx 超时：连接 10s（防僵死），读取 120s（M2.7 输出 100 TPS，1024 tokens 约 10s）
    HTTP_TIMEOUT = httpx.Timeout(10.0, read=120.0)

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(
                    "https://api.minimaxi.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1024,  # 降低到 1024（提取输出通常 << 1024 tokens，减少处理时间）
                    },
                )

            # ── HTTP 层错误 ──────────────────────────────────────
            if resp.status_code == 429:
                wait = RATE_LIMIT_COOL + random.uniform(0, 5)
                print(f"⚠️  HTTP 429 速率超限，等待 {wait:.0f}s（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            result = resp.json()

            # ── MiniMax 业务错误码 ───────────────────────────────
            base_resp = result.get("base_resp", {})
            status_code = base_resp.get("status_code", 0)

            # 成功
            if status_code == 0:
                pass  # 继续解析

            # 已知不可重试错误 → 直接返回空
            elif status_code in NO_RETRY_CODES:
                msg = base_resp.get("status_msg", "")
                print(f"⚠️  API 错误 {status_code}: {msg}（不重试）")
                return []

            # 速率限制 → 较长冷却
            elif status_code in (1002, 2045):
                wait = RATE_LIMIT_COOL + random.uniform(0, 10)
                print(f"⚠️  API 速率限制（{status_code}），等待 {wait:.0f}s（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(wait)
                continue

            # 可重试错误（1000/1001/1033 等）→ 指数退避
            elif status_code in RETRYABLE_CODES:
                delay = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 2)
                msg = base_resp.get("status_msg", "请稍后再试")
                print(f"⚠️  API 错误 {status_code}: {msg}，{delay:.1f}s 后重试（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(delay)
                continue

            # 未知错误 → 当作可重试处理（安全策略）
            else:
                delay = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 2)
                print(f"⚠️  未知 API 错误 {status_code}，{delay:.1f}s 后重试（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(delay)
                continue

            # ── 解析响应内容 ────────────────────────────────────
            choices = result.get("choices", [{}])
            if not choices:
                return []

            content = choices[0].get("message", {}).get("content", "")
            if not content:
                return []

            # 提取 JSON（兼容 markdown 代码块或裸 JSON）
            import re

            content = content.strip()

            if '```json' in content:
                m = re.search(r'```json\s*(.+?)```', content, re.DOTALL)
                if m:
                    content = m.group(1).strip()
            elif '```' in content:
                m = re.search(r'```\s*(.+?)```', content, re.DOTALL)
                if m:
                    content = m.group(1).strip()
            else:
                # 去掉解释性文字，只保留 JSON 数组
                m = re.search(r'(\[[\s\S]+\])', content)
                if m:
                    content = m.group(1).strip()

            memories = json.loads(content)
            if isinstance(memories, dict):
                memories = [memories]
            return memories

        # ── 网络层异常 ─────────────────────────────────────────
        except httpx.TimeoutException as e:
            delay = min(60.0, 4.0 * (2 ** attempt)) + random.uniform(0, 3)
            print(f"⚠️  网络超时（{e}），{delay:.1f}s 后重试（第 {attempt+1}/{max_retries} 次）...")
            time.sleep(delay)
            continue

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                wait = RATE_LIMIT_COOL + random.uniform(0, 5)
                print(f"⚠️  HTTP 429，等待 {wait:.0f}s（第 {attempt+1}/{max_retries} 次）...")
                time.sleep(wait)
                continue
            delay = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 2)
            print(f"⚠️  HTTP {status}，{delay:.1f}s 后重试（第 {attempt+1}/{max_retries} 次）...")
            time.sleep(delay)
            continue

        except json.JSONDecodeError as e:
            # JSON 解析失败 → 不重试（服务端返回了非 JSON 内容）
            print(f"⚠️  LLM 响应非 JSON（{e}），跳过")
            return []

        except Exception as e:
            delay = min(30.0, 2.0 * (2 ** attempt))
            print(f"⚠️  未知错误（{type(e).__name__}: {e}），{delay:.1f}s 后重试...")
            time.sleep(delay)
            continue

    # 所有重试耗尽
    print(f"⚠️  LLM 提取失败：已重试 {max_retries} 次")
    return []


# ─────────────────────────────────────────────────────────────
# 存储
# ─────────────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]


def _save_hash(text: str):
    h = _content_hash(text)
    state = _load_vectorize_state()
    synced = set(state.get("synced_hashes", []))
    synced.add(h)
    state["synced_hashes"] = list(synced)
    state["last_sync"] = datetime.datetime.now().isoformat()
    _save_vectorize_state(state)


def _load_synced() -> set:
    state = _load_vectorize_state()
    return set(state.get("synced_hashes", []))


def _write_entry(
    typ: str, title: str, content: str, source: str = '', importance: float = 0.5
) -> bool:
    """写入 L2 文件 + L4 Qdrant，返回是否新写入"""
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
importance: {importance}
---

{content}

---
"""
    if filepath.exists():
        existing = filepath.read_text(encoding='utf-8')
        if entry_text.strip() in existing:
            return False

    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(entry_text)

    try:
        store = get_store()
        store.add(
            content[:MAX_CONTENT_LEN],
            typ,
            source=source,
            metadata={"title": title, "importance": importance},
        )
    except Exception as e:
        print(f"    ⚠️  Qdrant: {e}")

    return True


def capture_extracted(memories: list[dict], source: str = '') -> dict:
    """将 LLM 提取的记忆写入 L2 + L4"""
    synced = _load_synced()
    written = 0
    skipped = 0
    type_counts = {}

    for mem in memories:
        typ = mem.get('type', 'practice')
        title = mem.get('title', '未命名')[:60]
        content = mem.get('content', '')
        importance = float(mem.get('importance', 0.5))

        if len(content) < 20:
            continue

        text_to_save = f"{title}\n{content}"
        h = _content_hash(text_to_save[:200])

        if h in synced:
            skipped += 1
            continue

        ok = _write_entry(typ, title, content, source=source, importance=importance)
        if ok:
            _save_hash(text_to_save[:200])
            synced.add(h)
            written += 1
        else:
            skipped += 1

        type_counts[typ] = type_counts.get(typ, 0) + 1

    return {"written": written, "skipped": skipped, "types": type_counts}


# ─────────────────────────────────────────────────────────────
# 主提取流程
# ─────────────────────────────────────────────────────────────


def llm_extract_text(text: str, source: str = '') -> dict:
    """从任意文本用 LLM 提取记忆并写入存储"""
    print(f"  🔮 调用 MiniMax LLM 提取...")
    memories = extract_with_llm(text)
    if not memories:
        print(f"  ⚠️  LLM 未提取到内容")
        return {"written": 0, "skipped": 0, "types": {}}

    print(f"  📦 LLM 提取了 {len(memories)} 条记忆:")
    for m in memories:
        print(f"     [{m.get('type')}] (imp={m.get('importance',0):.1f}) {m.get('title','')[:50]}")

    result = capture_extracted(memories, source=source)
    print(f"  ✅ 写入 {result['written']} 条，跳过 {result['skipped']} 条（已存在）")
    return result


def llm_extract_file(filepath: Path, force: bool = False) -> dict:
    """从文件提取记忆（LLM 模式）"""
    if not filepath.exists():
        return {"written": 0, "skipped": 0}
    content = filepath.read_text(encoding='utf-8')
    return llm_extract_text(content, source=f"file:{filepath.name}")


def llm_extract_session(session_id: str, messages: list[dict], force: bool = False) -> dict:
    """从会话消息列表提取记忆"""
    user_msgs = [m['text'][:300] for m in messages if m['role'] == 'user'][:10]
    assistant_msgs = [m['text'][:300] for m in messages if m['role'] == 'assistant'][:10]
    text = "## 用户提问\n" + "\n".join(f"- {m}" for m in user_msgs)
    text += "\n## 助手回复\n" + "\n".join(f"- {m}" for m in assistant_msgs)
    return llm_extract_text(text, source=f"session:{session_id[:20]}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='LLM 智能记忆提取')
    parser.add_argument('--file', type=str, help='从文件提取')
    parser.add_argument('--text', type=str, help='直接传入文本')
    parser.add_argument('--session-id', type=str, help='会话 ID')
    args = parser.parse_args()

    if args.file:
        result = llm_extract_file(Path(args.file))
        print(f"✅ 完成: 写入 {result['written']} 条")

    elif args.text:
        result = llm_extract_text(args.text)
        print(f"✅ 完成: 写入 {result['written']} 条")

    elif args.session_id:
        from session_transcript_extractor import _extract_messages_from_transcript

        sessions_dir = Path('/root/.openclaw/agents/main/sessions')
        for tf in sessions_dir.glob('*.jsonl'):
            if args.session_id in tf.stem:
                msgs = _extract_messages_from_transcript(tf)
                result = llm_extract_session(tf.stem.split('.')[0], msgs)
                print(f"✅ 完成: 写入 {result['written']} 条")
                break
        else:
            print(f"❌ 找不到会话 {args.session_id}")
    else:
        print("用法: llm_extract.py --text '要提取的文本'")
