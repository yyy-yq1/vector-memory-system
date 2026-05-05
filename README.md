# Vector Memory System — 自我进化向量记忆系统 v4.3.0

融合 **Brain-v1.1.8** 完整能力的十层主动记忆架构，基于 Qdrant 向量数据库 + Qwen3-Embedding-4B + 艾宾浩斯遗忘曲线。

---

## 架构概览

```
L00  感官层          → OpenClaw transcript JSONL（原始数据）
L01  工作记忆        → SESSION-STATE.md（WAL 协议，写前日志）
L02  会话归档        → 每日 memory/YYYY-MM-DD.md + Session Handoff
L03  类型化存储      → error / correction / practice / event / gap 五类片段
L04  语义向量库      → Qdrant + Qwen3-Embedding-4B（2560维）+ 艾宾浩斯强化
L05  规律提取        → MiniMax LLM M2.7 归纳跨记忆模式
L06  程序性记忆      → 工具使用成功率 + 任务模式注册
L07  核心记忆        → MEMORY.md（蒸馏精华）
L08  身份层          → SOUL.md + USER.md + IDENTITY.md
L09  深度归档        → 低分记忆归档（Ebbinghaus 触发）
L10  跨层路由        → BM25 + Vector + RRF 融合搜索 + 智能路由
```

---

## 搜索链路

```
用户查询
  └→ memory_api.search()
       └→ memory_hybrid.hybrid_search()
            ├→ BM25 关键词搜索（markdown 文件）
            ├→ Qdrant 向量语义搜索（2560维）
            └→ RRF 融合（k=60）
        └→ Ebbinghaus fluid_score 二次重排
        └→ reinforce() 命中自动强化
```

---

## 部署状态

| 项目 | 状态 |
|------|------|
| Qdrant 向量数据库 | ✅ 运行中（`127.0.0.1:6333`） |
| SiliconFlow Embedding | ✅ `Qwen3-Embedding-4B` 2560维 |
| OpenClaw Skill 加载 | ✅ `ready` |
| Gateway 自启 | ✅ systemd `enabled` |
| Qdrant 自启 | ✅ systemd `enabled` |
| 周期任务 | ✅ crontab 已配置 |

---

## 核心模块（42 个 .py 文件）

| 模块 | 文件 | 职责 |
|------|------|------|
| **统一 API** | `memory_api.py` | 搜索/捕获/强化统一入口 |
| **混合搜索** | `memory_hybrid.py` | BM25 + Vector + RRF 融合 |
| **强化引擎** | `memory_reinforcement.py` | Ebbinghaus 遗忘曲线强化 |
| **十层架构** | `memory_layers.py` | L00-L10 定义 + L10 路由器 |
| **Brain 调度** | `brain_agent.py` | 置信度/预检点/胶囊/上下文组装 |
| **胶囊管理** | `capsule_manager.py` | 经验胶囊 CRUD + 自动建议 |
| **预检点** | `pre_checkpoint.py` | 任务快照 + 进度记录 |
| **上下文构建** | `context_builder.py` | 6段式上下文组装 |
| **会话存储** | `session_store.py` | sessions/decisions/tasks 持久化 |
| **记忆片段** | `memory_fragment.py` | 5类片段 + FragmentPool 优先级注入 |
| **LLM 提取** | `llm_extract.py` | MiniMax M2.7 提取记忆 + 精确退避重试 |
| **日志提取** | `extract_memories.py` | 从 transcript JSONL 提取结构化记忆 |
| **会话状态** | `session_state.py` | WAL 协议状态机 |
| **会话归档** | `session_transcript_extractor.py` | transcript → 结构化日志 |
| **Qdrant 存储** | `qdrant_store.py` | 向量数据库 CRUD |
| **向量编码** | `embedding_client.py` | SiliconFlow Qwen3-Embedding-4B |
| **任务路由** | `task_router.py` | 子 agent 决策引擎 |
| **看门狗** | `watchdog.py` | 超时重试 + 预算分配 |
| **记忆存储** | `memory_store.py` | JSON 文件持久化 |
| **上下文监控** | `context_monitor.py` | Token 使用率监控 |
| **配置快照** | `config_snapshot.py` | 配置变更追踪 |
| **周期协调** | `memory_cycle.py` | 4个统一 cron 调度任务 |
| **进化触发** | `evolution_trigger.py` | 记忆自我进化触发器 |
| **置信度评估** | `auto_confidence_trigger.py` | 任务危险度自动评估 |
| **自动预检** | `auto_pre_checkpoint_trigger.py` | 危险任务自动打预检点 |
| **会话注入** | `build_session_injection.py` | Brain-v1.1.8 会话注入 |
| **对话计数** | `conversation_counter.py` | 对话轮次计数 |
| **主动检查** | `proactive_check.py` | 主动健康检查 |
| **技能检查** | `skill_health_checker.py` | 技能健康检查报告 |
| **经验匹配** | `capsule_matcher.py` | 胶囊相似度匹配 |
| **快速搜索** | `quick_search.py` | 轻量快速搜索 |

---

## 搜索示例

```python
import sys
sys.path.insert(0, '/root/.openclaw/workspace/skills/skills/vector-memory-self-evolution')
from memory_hybrid import hybrid_search

results = hybrid_search("飞书 文档 创建", n_results=5)
for r in results:
    print(f"[{r['score']:.3f}] {r['content'][:80]}...")
```

---

## CLI 命令

```bash
# 搜索记忆
python3 memory_api.py search "飞书 文档"

# 捕获错误/教训
python3 memory_api.py capture error "OAuth失败" "token过期"
python3 memory_api.py capture lesson "不要用 rm *"

# BrainAgent 流程
python3 brain_agent.py run "帮我调研天气API"
python3 brain_agent.py confidence "删除所有文件"
python3 brain_agent.py context -q "用户偏好"
python3 brain_agent.py cycle 30min

# 胶囊管理
python3 capsule_manager.py list
python3 capsule_manager.py suggest "完成了" "调研任务"

# 周期调度
python3 memory_cycle.py status
python3 memory_cycle.py health
python3 memory_cycle.py 30min   # 每30分钟
python3 memory_cycle.py 6h       # 每6小时
python3 memory_cycle.py daily   # 每日23点
python3 memory_cycle.py 3am     # 凌晨3点归档

# 技能健康检查
python3 skill_health_checker.py --report

# 配置快照
python3 config_snapshot.py check

# 上下文监控
python3 context_monitor.py status

# LLM 提取记忆
python3 extract_memories.py --llm --days 3

# 会话存储
python3 session_store.py stats
python3 session_store.py sessions
python3 session_store.py decisions
python3 session_store.py cleanup 180
```

---

## Crontab 配置

```crontab
# 向量记忆系统周期任务
*/30 * * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 30min --quiet 2>/dev/null
0 */6 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 6h --quiet 2>/dev/null
0 23 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py daily --quiet 2>/dev/null
0 3 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 3am --quiet 2>/dev/null
```

---

## 依赖

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 运行时 |
| Qdrant | 向量数据库（`127.0.0.1:6333`） |
| MiniMax API | LLM 提取记忆 |
| SiliconFlow | `Qwen3-Embedding-4B` 向量编码 |
| httpx | 异步 HTTP 客户端 |
| qdrant-client | Qdrant Python SDK |

安装依赖：
```bash
pip install qdrant-client httpx pyyaml
```

---

## 配置

配置文件：`memory_config.py`（已通过 `EnvironmentFile` 自动注入以下变量）

| 变量 | 值 |
|------|-----|
| `SILICONFLOW_API_KEY` | SiliconFlow API Key |
| `MINIMAX_API_KEY` | MiniMax API Key |
| Qdrant | `http://127.0.0.1:6333` |
| Collection | `memories` |
| 向量维度 | 2560 |

---

## Git 提交历史

```
cc2effb fix(context_monitor): 修复消息content深度提取
b97bedf fix: 4个P2问题 - token估算偏差+UnicodeDecodeError+非原子写
acb3439 fix: memory_layers.py append_fact → append_key_fact
26571cb fix: 次要bug修复
2a9c68a fix: extract_memories._save_hash 防重 + evolution_trigger 防重
e3a62d0 fix: 多文件路径/并发bug修复
a9ae2a0 docs: SKILL.md 更新时间
075a365 fix(llm_extract): v1.2 - 精确错误码退避 + jitter + 120s超时
853fa39 fix(llm_extract): 超时/重试/API错误处理
```

---

## License

MIT
