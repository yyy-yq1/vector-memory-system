#!/usr/bin/env python3
"""
SessionStore — 会话持久化存储
================================

整合自 Brain-v1.1.8 session-store.js

三层存储：sessions / decisions / tasks
原子写入：临时文件 → rename
文件损坏：自动恢复，不崩溃

使用方式：
  from session_store import SessionStore
  store = SessionStore()
  await store.save_snapshot("session_key", {"task": "...", "decisions": [...]})
  snapshot = await store.get_latest_snapshot()
  decisions = await store.search_decisions("量化交易")
"""
import os
import json
import datetime
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'

# ─────────────────────────────────────────────────────────────
# 路径解析
# ─────────────────────────────────────────────────────────────

def _expand_path(p: str) -> Path:
    if isinstance(p, str) and p.startswith('~'):
        return Path.home() / p[1:]
    return Path(p)

# ─────────────────────────────────────────────────────────────
# SessionStore
# ─────────────────────────────────────────────────────────────

class SessionStore:
    """
    会话持久化存储（JSON 文件模拟 SQLite 风格）

    存储结构：
      memory/sessions.json     — 会话快照
      memory/decisions.json    — 决策历史
      memory/tasks.json        — 任务状态
    """

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = str(MEMORY_DIR / 'sessions.json')
        self._base = Path(base_path)
        self._dir = self._base.parent
        self._base_name = self._base.stem  # 无扩展名的文件名

        self._files = {
            'sessions':  self._dir / f'{self._base_name}_sessions.json',
            'decisions': self._dir / f'{self._base_name}_decisions.json',
            'tasks':     self._dir / f'{self._base_name}_tasks.json',
        }
        self._ensure_dir()
        self._load_all()

    def _ensure_dir(self):
        self._dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> str:
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── 文件读写（原子写入） ────────────────────────────────

    def _load_json(self, file_path: Path) -> dict:
        try:
            if file_path.exists():
                return json.loads(file_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_json(self, file_path: Path, data: dict):
        """原子写入：先写临时文件，再 replace（shutil.move）"""
        import shutil
        tmp = str(file_path) + f'.tmp.{os.getpid()}'
        Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.move(tmp, str(file_path))

    def _load_all(self):
        self._sessions  = self._load_json(self._files['sessions'])
        self._decisions = self._load_json(self._files['decisions'])
        self._tasks     = self._load_json(self._files['tasks'])

    def _save_sessions(self):
        self._save_json(self._files['sessions'], self._sessions)
    def _save_decisions(self):
        self._save_json(self._files['decisions'], self._decisions)
    def _save_tasks(self):
        self._save_json(self._files['tasks'], self._tasks)

    # ── Sessions ────────────────────────────────────────────

    async def save_snapshot(self, session_key: str, data: dict) -> dict:
        """
        保存会话快照

        data = {
          "task": str,        # 当前任务
          "progress": str,    # 进度描述
          "decisions": [],     # 决策列表 [{"topic", "content", "source"}]
          "subagents": [],     # subagent 记录
          "notes": str,        # 备注
        }
        """
        now = self._now()
        existing = self._sessions.get(session_key)

        self._sessions[session_key] = {
            "session_key": session_key,
            "timestamp":   now,
            "task":        data.get("task"),
            "progress":    data.get("progress"),
            "decisions":   data.get("decisions", []),
            "subagents":   data.get("subagents", []),
            "notes":       data.get("notes"),
            "created_at":  existing.get("created_at", now) if existing else now,
        }
        self._save_sessions()

        # 同时把 decisions 写入独立表（双写保证）
        for d in data.get("decisions", []):
            if d.get("topic") and d.get("content"):
                self._decisions[self._next_decision_id()] = {
                    "id":          None,
                    "topic":       d["topic"],
                    "content":     d["content"],
                    "source":      d.get("source", session_key),
                    "session_key": session_key,
                    "created_at":  now,
                }
        if data.get("decisions"):
            self._save_decisions()

        return {"ok": True, "session_key": session_key}

    async def get_latest_snapshot(self) -> Optional[dict]:
        """获取最新快照"""
        if not self._sessions:
            return None
        keys = sorted(self._sessions.keys(), reverse=True)
        return self._sessions[keys[0]] if keys else None

    async def get_session(self, session_key: str) -> Optional[dict]:
        """获取特定会话"""
        return self._sessions.get(session_key)

    async def list_sessions(self, date: str = None) -> list[dict]:
        """
        列出会话（可选按日期过滤）
        date: YYYY-MM-DD 格式
        """
        results = []
        for key, session in self._sessions.items():
            ts = session.get("timestamp", "")
            if date is None or ts.startswith(date):
                results.append({
                    "session_key": session.get("session_key", key),
                    "timestamp":    ts,
                    "task":         session.get("task"),
                    "progress":     session.get("progress"),
                })
        return sorted(results, key=lambda x: x["timestamp"], reverse=True)

    # ── Decisions ──────────────────────────────────────────

    def _next_decision_id(self) -> str:
        ids = []
        for k in self._decisions.keys():
            try:
                ids.append(int(k))
            except (ValueError, TypeError):
                pass
        return str(max(ids) + 1 if ids else 1)

    async def save_decision(self, topic: str, content: str, source: str = None) -> dict:
        """保存关键决策"""
        did = self._next_decision_id()
        self._decisions[did] = {
            "id":          int(did),
            "topic":       topic,
            "content":     content,
            "source":      source,
            "session_key": None,
            "created_at":  self._now(),
        }
        self._save_decisions()
        return {"ok": True, "id": int(did)}

    async def search_decisions(self, query: str = None, limit: int = 10) -> list[dict]:
        """
        搜索决策历史
        支持 topic 和 content 模糊匹配
        """
        results = list(self._decisions.values())

        if query:
            q = query.lower()
            results = [
                d for d in results
                if q in d.get("topic", "").lower() or q in d.get("content", "").lower()
            ]

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results[:limit]

    async def get_decision_stats(self) -> dict:
        """决策统计"""
        decisions = list(self._decisions.values())
        return {
            "total":       len(decisions),
            "by_source":   self._count_by(decisions, "source"),
            "recent_week": sum(1 for d in decisions if self._is_recent(d.get("created_at", ""), days=7)),
        }

    def _count_by(self, items: list, key: str) -> dict:
        counts = {}
        for item in items:
            k = item.get(key) or "unknown"
            counts[k] = counts.get(k, 0) + 1
        return counts

    def _is_recent(self, ts: str, days: int = 7) -> bool:
        if not ts:
            return False
        try:
            dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            return (datetime.datetime.now() - dt).days <= days
        except Exception:
            return False

    # ── Tasks ─────────────────────────────────────────────

    async def save_task(self, task_id: str, status: str, progress: str = None, details: str = None) -> dict:
        """
        保存任务状态

        status: "pending" | "in_progress" | "completed" | "failed"
        """
        now = self._now()
        existing = self._tasks.get(task_id)

        self._tasks[task_id] = {
            "task_id":    task_id,
            "status":     status,
            "progress":   progress,
            "details":    details,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        self._save_tasks()
        return {"ok": True, "task_id": task_id}

    async def get_pending_tasks(self) -> list[dict]:
        """获取所有未完成任务"""
        return [
            t for t in self._tasks.values()
            if t.get("status") in ("pending", "in_progress")
        ]

    async def update_task(self, task_id: str, status: str, progress: str = None) -> dict:
        """更新任务状态"""
        if task_id not in self._tasks:
            return {"ok": False, "error": "task not found"}
        now = self._now()
        self._tasks[task_id]["status"] = status
        if progress is not None:
            self._tasks[task_id]["progress"] = progress
        self._tasks[task_id]["updated_at"] = now
        self._save_tasks()
        return {"ok": True, "task_id": task_id}

    # ── Maintenance ────────────────────────────────────────

    async def cleanup(self, days_to_keep: int = 180) -> dict:
        """
        清理过期数据（直接删除，不归档）
        days_to_keep: 保留最近 N 天
        """
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)
        cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

        sessions_removed = 0
        decisions_removed = 0

        for key in list(self._sessions.keys()):
            if self._sessions[key].get("timestamp", "") < cutoff_str:
                del self._sessions[key]
                sessions_removed += 1
        self._save_sessions()

        for did in list(self._decisions.keys()):
            created = self._decisions[did].get("created_at", "")
            if created and created < cutoff_str:
                del self._decisions[did]
                decisions_removed += 1
        self._save_decisions()

        return {"removed": sessions_removed + decisions_removed,
                "sessions": sessions_removed,
                "decisions": decisions_removed}

    async def archive(self, days_to_keep: int = 30) -> dict:
        """
        归档旧数据（30天前的会话移到独立文件）
        """
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)
        cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

        archived_sessions = {}
        kept_sessions = {}
        archived_count = 0
        kept_count = 0

        for key, session in self._sessions.items():
            if session.get("timestamp", "") < cutoff_str:
                archived_sessions[key] = session
                archived_count += 1
            else:
                kept_sessions[key] = session
                kept_count += 1

        if archived_count > 0:
            archive_path = self._dir / f"{self._base_name}_archive_{datetime.date.today()}.json"
            self._save_json(archive_path, archived_sessions)
            self._sessions = kept_sessions
            self._save_sessions()

        return {"archived": archived_count, "kept": kept_count, "archive_path": str(archive_path)}

    async def stats(self) -> dict:
        """系统统计"""
        pending = [t for t in self._tasks.values() if t.get("status") in ("pending", "in_progress")]
        return {
            "sessions":      len(self._sessions),
            "decisions":     len(self._decisions),
            "tasks":         len(self._tasks),
            "pending_tasks": len(pending),
        }

    async def export_all(self) -> dict:
        """导出全部数据"""
        return {
            "sessions":  dict(self._sessions),
            "decisions": dict(self._decisions),
            "tasks":     dict(self._tasks),
        }


# ─────────────────────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────────────────────

_store: SessionStore = None

def get_store() -> SessionStore:
    """获取 SessionStore 单例"""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import asyncio
    import argparse

    async def run_cmd(args):
        store = get_store()

        if args.cmd == 'stats':
            s = await store.stats()
            print(f"📊 SessionStore 统计")
            print(f"   会话: {s['sessions']}")
            print(f"   决策: {s['decisions']}")
            print(f"   任务: {s['tasks']} (待处理 {s['pending_tasks']})")

        elif args.cmd == 'sessions':
            sessions = await store.list_sessions(args.date)
            print(f"📋 会话列表 ({len(sessions)} 个)")
            for s in sessions[:20]:
                print(f"  [{s['timestamp'][:10]}] {s.get('task', '无任务')[:60]}")

        elif args.cmd == 'decisions':
            results = await store.search_decisions(args.query, limit=args.limit)
            print(f"🔍 决策搜索: '{args.query}' ({len(results)} 条)")
            for d in results:
                print(f"  [{d.get('created_at','')[:10]}] {d.get('topic','')}")
                print(f"    {d.get('content','')[:80]}")

        elif args.cmd == 'tasks':
            pending = await store.get_pending_tasks()
            print(f"📌 待处理任务 ({len(pending)} 个)")
            for t in pending:
                print(f"  [{t.get('status')}] {t.get('task_id')}: {t.get('progress','')[:60]}")

        elif args.cmd == 'snapshot':
            snap = await store.get_latest_snapshot()
            if snap:
                print(f"📸 最新会话: {snap.get('session_key')}")
                print(f"   时间: {snap.get('timestamp')}")
                print(f"   任务: {snap.get('task', '无')}")
                print(f"   决策: {len(snap.get('decisions', []))} 条")
            else:
                print("❌ 无会话快照")

        elif args.cmd == 'save-decision':
            r = await store.save_decision(args.topic, args.content, args.source)
            print(f"✅ 决策已保存: #{r['id']}")

        elif args.cmd == 'save-task':
            r = await store.save_task(args.task_id, args.status, args.progress)
            print(f"✅ 任务已保存: {args.task_id} → {args.status}")

        elif args.cmd == 'cleanup':
            r = await store.cleanup(int(args.days))
            print(f"🧹 清理完成: 删除 {r['removed']} 条 (会话 {r['sessions']} + 决策 {r['decisions']})")

    parser = argparse.ArgumentParser(description='SessionStore CLI')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('stats', help='统计信息')
    p = sub.add_parser('sessions', help='会话列表')
    p.add_argument('--date', help='按日期过滤 YYYY-MM-DD')
    p = sub.add_parser('decisions', help='搜索决策')
    p.add_argument('query', nargs='?', default='', help='搜索关键词')
    p.add_argument('--limit', type=int, default=10)
    p = sub.add_parser('tasks', help='待处理任务')
    p = sub.add_parser('snapshot', help='最新快照')
    p = sub.add_parser('save-decision', help='保存决策')
    p.add_argument('--topic', '-t', required=True)
    p.add_argument('--content', '-c', required=True)
    p.add_argument('--source', '-s', default=None)
    p = sub.add_parser('save-task', help='保存任务')
    p.add_argument('task_id')
    p.add_argument('status')
    p.add_argument('--progress', '-p')
    p = sub.add_parser('cleanup', help='清理过期数据')
    p.add_argument('days', nargs='?', default='180')

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
    else:
        asyncio.run(run_cmd(args))
