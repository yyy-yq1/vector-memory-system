#!/usr/bin/env python3
"""
BrainAgent — 统一的 Brain 类大脑 Agent 接口
==========================================

整合 Brain-v1.1.8 全部模块的统一入口：

架构：
  BrainAgent
  ├── memory_backend   (记忆存储)
  ├── session_store   (会话持久化)
  ├── fragment        (片段标准接口)
  ├── confidence      (置信度评估)
  ├── capsule         (胶囊管理)
  ├── checkpoint      (预检点)
  ├── router          (任务路由)
  ├── context_builder (上下文组装)
  ├── watchdog         (执行看门狗)
  └── skill_health    (技能健康检查)

使用方式：
  from brain_agent import BrainAgent
  agent = BrainAgent()
  result = agent.run("用户任务描述")
"""
import os
import re
import json
import datetime
import asyncio
from pathlib import Path
from typing import Optional

WORKSPACE = Path.home() / '.openclaw/workspace'
MEMORY_DIR = WORKSPACE / 'memory'
SKILL_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────
# 导入所有子模块
# ─────────────────────────────────────────────────────────────

try:
    from session_store import SessionStore, get_store as get_session_store
except ImportError:
    SessionStore = None
    get_session_store = None

try:
    from memory_fragment import (
        MemoryFragment, SkillFragment, TaskFragment,
        FragmentPool, skill, task, config as cfg_frag,
        conclusion, lesson, estimate_tokens
    )
except ImportError:
    MemoryFragment = None

try:
    from memory_brain import confidence_check as brain_confidence
except ImportError:
    brain_confidence = None

try:
    from task_router import route_task
except ImportError:
    route_task = None

try:
    from context_builder import ContextBuilder, quick_context
except ImportError:
    ContextBuilder = None
    quick_context = None

try:
    from pre_checkpoint import create_checkpoint, complete_checkpoint, needs_checkpoint
except ImportError:
    create_checkpoint = None
    complete_checkpoint = None
    needs_checkpoint = None

# ─────────────────────────────────────────────────────────────
# BrainAgent
# ─────────────────────────────────────────────────────────────

class BrainAgent:
    """
    统一的 Brain 类大脑 Agent 接口

    每次收到任务时的完整流程：
      1. 置信度评估（confidence_check）
      2. 预检点判断（needs_checkpoint → pre_checkpoint）
      3. 任务路由决策（route_task）
      4. 上下文组装（ContextBuilder）
      5. 执行任务
      6. 胶囊建议（capsule_suggest）
      7. 会话快照保存（session_store）
    """

    def __init__(self):
        self.session_store = get_session_store() if get_session_store else None
        self.fragment_pool = FragmentPool() if FragmentPool else None
        self.workspace = WORKSPACE
        self.memory_dir = MEMORY_DIR

    # ── 1. 置信度评估 ────────────────────────────────────

    def assess_confidence(self, task: str) -> dict:
        """
        评估任务置信度

        返回：{"confidence": float, "level": str, "signals": list, "recommendation": str}
        """
        if brain_confidence:
            return brain_confidence(task)
        # 降级：简单信号检测
        score = 0.9
        signals = []
        dangerous = re.findall(r'删除|rm\s|drop\s|销毁', task)
        if dangerous:
            score -= 0.3
            signals.append(f'不可逆操作: {dangerous}')
        return {
            'task': task,
            'confidence': max(0, min(1, score)),
            'level': 'high' if score >= 0.7 else ('medium' if score >= 0.4 else 'low'),
            'signals': signals,
            'recommendation': '直接执行' if score >= 0.7 else ('执行+记录' if score >= 0.4 else '先快照')
        }

    # ── 2. 预检点 ──────────────────────────────────────

    def ensure_checkpoint(self, task: str, confidence: float = None) -> Optional[str]:
        """
        确保任务有预检点（如果需要）
        返回 checkpoint_id 或 None
        """
        if create_checkpoint is None or needs_checkpoint is None:
            return None

        if needs_checkpoint(task, confidence):
            cp_id = create_checkpoint(
                task=task,
                plan="由 BrainAgent 自动创建预检点",
                confidence=int((confidence or 0.5) * 10),
                steps=0
            )
            return cp_id
        return None

    # ── 3. 任务路由 ────────────────────────────────────

    def route(self, task: str) -> dict:
        """
        任务路由决策

        返回完整的路由决策：
          {
            "task": str,
            "confidence": float,
            "should_split": bool,
            "agents": [{"direction", "model", "prompt"}],
            "need_verification": bool,
            "warnings": [str],
          }
        """
        if route_task:
            return route_task(task)
        # 降级：简单分类
        categories = []
        for cat, keywords in {
            'search': ['搜索', '调研', '查找'],
            'code': ['代码', '编程', 'bug'],
            'write': ['写', '文案', '文章'],
        }.items():
            if any(k in task for k in keywords):
                categories.append(cat)
        return {
            'task': task,
            'confidence': 0.8,
            'category': categories[0] if categories else 'general',
            'should_split': False,
            'agents': [{'direction': '单一方向', 'model': 'MiniMax-M2.7', 'prompt': task}],
            'need_verification': False,
            'warnings': []
        }

    # ── 4. 上下文组装 ─────────────────────────────────

    def build_context(self, query: str = "") -> dict:
        """
        组装智能上下文

        返回：{"injection": str, "sections": list, "total_tokens": int}
        """
        if ContextBuilder:
            builder = ContextBuilder()
            builder.add_user_info()
            builder.add_session_state()
            if query:
                builder.add_memory_search(query)
            builder.add_recent_decisions()
            builder.add_core_memory(query)
            builder.add_buffer()
            return builder.build()
        # 降级：只返回快速上下文
        return {"injection": quick_context(query) if quick_context else "", "sections": [], "total_tokens": 0}

    # ── 5. 执行包装 ────────────────────────────────────

    def wrap_exec(self, cmd: str, timeout: int = 120) -> dict:
        """
        带看门狗的命令执行

        返回：{"success": bool, "stdout": str, "stderr": str, "error": str}
        """
        try:
            import subprocess
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "Command timed out", "error": "TIMEOUT"}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e), "error": str(e)}

    # ── 6. 胶囊建议 ───────────────────────────────────

    def suggest_capsule(self, task: str, result: str, success: bool) -> dict:
        """
        任务完成后建议胶囊

        返回：{"action": str, "capsule": dict}
        """
        if not (MEMORY_DIR / 'capsules.json').exists():
            return {"action": "none", "reason": "capsules.json not found"}

        try:
            from memory_brain import capsule_suggest as brain_capsule_suggest
            return brain_capsule_suggest(task, result, success)
        except Exception:
            pass

        # 降级：简单判断
        should_create = (
            len(task) > 50 or
            any(k in task for k in ['重构', '调研', '系统', '多步骤']) or
            success
        )
        return {"action": "create" if should_create else "none", "reason": "auto判断"}

    # ── 7. 会话快照 ───────────────────────────────────

    async def save_snapshot(self, session_key: str, data: dict) -> dict:
        """保存会话快照到 SessionStore"""
        if self.session_store:
            return await self.session_store.save_snapshot(session_key, data)
        return {"ok": False, "error": "SessionStore not available"}

    # ── 8. 记忆片段 ───────────────────────────────────

    def create_fragment(self, type_: str, content: str, **kwargs) -> Optional[MemoryFragment]:
        """创建记忆片段（统一走基类，避免子类签名差异）"""
        if MemoryFragment is None:
            return None
        priority = float(kwargs.get('priority', 0.5))
        try:
            return MemoryFragment(type_, content, priority)
        except Exception:
            return None

    def inject_fragments(self, fragments: list) -> str:
        """
        将记忆片段注入上下文

        fragments: list of MemoryFragment
        返回注入字符串
        """
        if self.fragment_pool is None:
            try:
                self.fragment_pool = FragmentPool()
            except Exception:
                return ""

        for frag in fragments:
            self.fragment_pool.add(frag)

        result = self.fragment_pool.to_context()
        return result.get("injection", "")

    # ── 9. 会话轮次计数 ────────────────────────────

    def increment_counter(self) -> dict:
        """递增对话轮次计数（每次任务完成后调用）"""
        try:
            import subprocess
            r = subprocess.run(
                [__import__('sys').executable, str(SKILL_DIR / 'conversation_counter.py'), '--inc'],
                capture_output=True, text=True, timeout=10, cwd=str(SKILL_DIR)
            )
            if r.returncode == 0:
                import json
                return json.loads(r.stdout)
        except Exception:
            pass
        return {}

    def check_review_needed(self) -> dict:
        """检查是否需要复盘"""
        try:
            import subprocess
            r = subprocess.run(
                [__import__('sys').executable, str(SKILL_DIR / 'auto_review_trigger.py')],
                capture_output=True, text=True, timeout=30, cwd=str(SKILL_DIR)
            )
            if r.returncode == 0 and r.stdout.strip():
                return {"triggered": True, "output": r.stdout}
        except Exception:
            pass
        return {"triggered": False}

    # ── 10. 记忆捕获 ──────────────────────────────────

    def capture(self, typ: str, title: str, content: str, context: str = '') -> dict:
        """
        捕获记忆到 L2 文件 + L4 向量库
        同时写入文件、向量库，并返回结构化 fragment
        """
        try:
            from memory_api import capture as api_capture
            result = api_capture(typ, title, content, context)
            # 同时创建 fragment
            frag = self.create_fragment(typ, f"{title}\n{content}", priority=0.7)
            if frag and self.fragment_pool:
                self.fragment_pool.add(frag)
            return {"ok": True, "fragment": frag}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 11. Cron 周期调度 ────────────────────────────

    def run_cycle(self, mode: str = '30min', quiet: bool = False) -> dict:
        """
        统一 cron 调度入口（替代 memory_cycle.py 直接调用子脚本）

        mode:
          30min: L00→L02 + L02→L03/L04 + proactive_check
          6h:    L04 低分归档 L09
          daily: L01→L07 蒸馏 + L05 规律提取 + Brain 健康检查
          3am:   L03 压缩去重
        """
        results = {"mode": mode, "steps": []}

        if mode == '30min':
            from memory_cycle import run_30min as cycle_run
            cycle_run(quiet=quiet)
            results["steps"].append("L00→L02→L04 done")

            # proactive check
            try:
                import subprocess
                r = subprocess.run(
                    [__import__('sys').executable, str(SKILL_DIR / 'proactive_check.py')],
                    capture_output=True, text=True, timeout=20, cwd=str(SKILL_DIR)
                )
                if r.returncode == 0:
                    results["steps"].append("proactive_check ok")
            except Exception:
                pass

        elif mode == '6h':
            from memory_cycle import run_6h as cycle_run
            cycle_run(quiet=quiet)
            results["steps"].append("L04→L09 archival done")

        elif mode == 'daily':
            from memory_cycle import run_daily as cycle_run
            cycle_run(quiet=quiet)
            results["steps"].append("daily distillation done")

            # Brain daily health
            try:
                import subprocess
                for script in ['config_snapshot.py', 'auto_review_trigger.py']:
                    r = subprocess.run(
                        [__import__('sys').executable, str(SKILL_DIR / script)],
                        capture_output=True, text=True, timeout=30, cwd=str(SKILL_DIR)
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        results["steps"].append(f"{script}: ok")
            except Exception:
                pass

        elif mode == '3am':
            from memory_cycle import run_3am as cycle_run
            cycle_run(quiet=quiet)
            results["steps"].append("L03 compress/dedup done")

        return results

    # ── 12. 技能健康检查 ────────────────────────────────

    def check_skill_health(self) -> dict:
        """
        执行技能健康检查

        返回：{"healthy": int, "lowFreq": int, "abandoned": int, "needsRepair": int}
        """
        try:
            import subprocess
            result = subprocess.run(
                [__import__('sys').executable, str(SKILL_DIR / 'skill_health_checker.py')],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception:
            pass
        return {"error": "skill_health_checker not available"}

    # ── 10. 完整任务执行流程 ───────────────────────────

    async def execute(self, task: str, mode: str = "auto") -> dict:
        """
        完整任务执行流程

        mode: "auto" | "fast" | "safe"
          auto: 完整流程（置信度→预检点→路由→执行→胶囊→快照）
          fast:  仅置信度+路由+执行
          safe:  完整流程 + 强制预检点
        """
        result = {
            "task": task,
            "timestamp": datetime.datetime.now().isoformat(),
            "steps": [],
        }

        # Step 1: 置信度评估
        conf = self.assess_confidence(task)
        result["steps"].append({"step": "confidence", **conf})

        # Step 2: 预检点（auto/safe 模式）
        checkpoint_id = None
        if mode in ("auto", "safe") or conf["confidence"] < 0.6:
            checkpoint_id = self.ensure_checkpoint(task, conf["confidence"])
            if checkpoint_id:
                result["steps"].append({
                    "step": "checkpoint",
                    "id": checkpoint_id,
                    "action": "created"
                })

        # Step 3: 任务路由
        routing = self.route(task)
        result["steps"].append({"step": "routing", **routing})

        # Step 4: 执行（这里仅记录，实际执行由调用方）
        result["steps"].append({
            "step": "ready",
            "message": f"任务已就绪，置信度 {conf['confidence']}，路由 {routing.get('category', 'unknown')}",
            "checkpoint_id": checkpoint_id
        })

        # Step 5: 递增会话轮次
        counter = self.increment_counter()
        if counter.get('needs_review'):
            result["steps"].append({
                "step": "review_triggered",
                "message": f"对话已达 {counter.get('turns')} 轮，建议复盘",
                "turns": counter.get('turns')
            })
            self.check_review_needed()

        # Step 6: 持久化会话快照
        if self.session_store:
            try:
                await self.session_store.save_snapshot(
                    f"task_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    {"task": task, "confidence": conf, "routing": routing, "result": result}
                )
            except Exception:
                pass

        return result

    # ── CLI 入口 ──────────────────────────────────────

    def cli(self, args: list = None):
        """BrainAgent CLI 入口"""
        import argparse
        parser = argparse.ArgumentParser(description='BrainAgent — 统一大脑接口')
        sub = parser.add_subparsers(dest='cmd')

        p = sub.add_parser('run', help='执行完整流程')
        p.add_argument('task', nargs='*', help='任务描述')
        p.add_argument('--mode', default='auto', choices=['auto', 'fast', 'safe'])

        p = sub.add_parser('confidence', help='置信度评估')
        p.add_argument('task', nargs='*', help='任务描述')

        p = sub.add_parser('route', help='任务路由')
        p.add_argument('task', nargs='*', help='任务描述')

        p = sub.add_parser('context', help='组装上下文')
        p.add_argument('--query', '-q', default='', help='查询')

        p = sub.add_parser('capsule', help='胶囊建议')
        p.add_argument('--result', '-r', required=True)
        p.add_argument('--task', '-t', required=True)
        p.add_argument('--success', '-s', default='true')

        p = sub.add_parser('cycle', help='Cron 周期调度')
        p.add_argument('mode', nargs='?', default='30min',
                       choices=['30min', '6h', 'daily', '3am'])
        p.add_argument('--quiet', '-q', action='store_true')

        p = sub.add_parser('capture', help='捕获记忆')
        p.add_argument('type', help='记忆类型: error/correction/practice/event/gap')
        p.add_argument('title', help='标题')
        p.add_argument('content', nargs='*', help='内容')

        args = parser.parse_args(args)

        if args.cmd == 'run':
            task = ' '.join(args.task) if args.task else input('任务: ')
            result = asyncio.run(self.execute(task, mode=args.mode))
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.cmd == 'confidence':
            task = ' '.join(args.task) if args.task else input('任务: ')
            conf = self.assess_confidence(task)
            print(f"📊 置信度: {conf['confidence']} ({conf['level']})")
            if conf['signals']:
                for s in conf['signals']:
                    print(f"  - {s}")
            print(f"  → {conf['recommendation']}")

        elif args.cmd == 'route':
            task = ' '.join(args.task) if args.task else input('任务: ')
            routing = self.route(task)
            print(f"🔀 路由: {routing.get('category', 'unknown')}")
            print(f"   拆分: {routing.get('should_split', False)}")
            print(f"   Agent数: {len(routing.get('agents', []))}")
            for a in routing.get('agents', []):
                print(f"   → [{a.get('direction')}] {a.get('prompt','')[:60]}")

        elif args.cmd == 'context':
            ctx = self.build_context(args.query)
            print(f"📋 上下文 ({ctx['total_tokens']} tokens):")
            print(ctx['injection'][:500])

        elif args.cmd == 'capsule':
            success = args.success.lower() == 'true'
            suggestion = self.suggest_capsule(args.task, args.result, success)
            print(f"💡 胶囊建议: {suggestion.get('action', 'none')}")
            if suggestion.get('reason'):
                print(f"   原因: {suggestion['reason']}")

        elif args.cmd == 'cycle':
            result = self.run_cycle(mode=args.mode, quiet=args.quiet)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.cmd == 'capture':
            content = ' '.join(args.content) if args.content else ''
            r = self.capture(args.type, args.title, content)
            print(f"{'✅' if r.get('ok') else '❌'} 已捕获 [{args.type}]: {args.title}")


# ─────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    agent = BrainAgent()
    agent.cli(sys.argv[1:])
