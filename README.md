# Vector Memory System — 自我进化向量记忆系统

融合 **Brain-v1.1.8** 完整能力的 10 层向量记忆架构。

## 架构概览

```
L00 感官层（原始输入）
L01 工作记忆（SESSION-STATE + 会话快照）
L02 会话归档（transcript → 结构化日志）
L03 类型化存储（error / correction / practice / event / gap）
L04 语义向量库（Qdrant + Qwen3-Embedding-4B, 2560维）
L05 规律提取（MiniMax LLM 归纳模式）
L06 程序性记忆（工具使用成功率）
L07 核心记忆（MEMORY.md 蒸馏精华）
L08 身份层（USER.md / IDENTITY.md / SOUL.md）
L09 深度归档（Ebbinghaus 低分记忆）
L10 跨层路由（智能查询 + BM25+Vector+RRF 融合）
```

## 搜索链路

```
用户查询
  └→ memory_api.search()
       └→ memory_hybrid.hybrid_search()
            ├→ BM25 关键词搜索（markdown 文件）
            ├→ Qdrant 向量语义搜索
            └→ RRF 融合（k=60）
        └→ Ebbinghaus fluid_score 二次重排
        └→ reinforce() 命中自动强化
```

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **记忆API** | `memory_api.py` | 统一读写入口 |
| **混合搜索** | `memory_hybrid.py` | BM25 + Vector + RRF |
| **强化引擎** | `memory_reinforcement.py` | Ebbinghaus 遗忘曲线 |
| **十层架构** | `memory_layers.py` | L00-L10 定义 + L10路由器 |
| **Brain调度** | `brain_agent.py` | 置信度/预检点/路由/胶囊 |
| **胶囊管理** | `capsule_manager.py` | 经验胶囊 CRUD |
| **置信度** | `auto_confidence_trigger.py` | 任务危险度评估 |
| **预检点** | `pre_checkpoint.py` | 任务快照 + 进度记录 |
| **上下文构建** | `context_builder.py` | 6段式上下文组装 |
| **会话存储** | `session_store.py` | sessions/decisions/tasks 持久化 |
| **记忆片段** | `memory_fragment.py` | 5类片段 + FragmentPool |
| **任务路由** | `task_router.py` | 子agent决策引擎 |
| **看门狗** | `watchdog.py` | 超时重试 + 预算分配 |
| **周期调度** | `memory_cycle.py` | 4个统一 cron 任务 |

## 快速开始

```bash
# 搜索记忆
python3 memory_api.py search "飞书"

# 捕获错误
python3 memory_api.py capture error "OAuth失败" "token过期"

# BrainAgent 执行流程
python3 brain_agent.py run "帮我调研一下天气API"

# 置信度评估
python3 brain_agent.py confidence "删除所有文件"

# 任务路由
python3 brain_agent.py route "多平台内容发布"

# 上下文组装
python3 brain_agent.py context -q "用户偏好"

# 周期调度
python3 brain_agent.py cycle 30min
python3 brain_agent.py cycle daily

# 胶囊管理
python3 capsule_manager.py list
python3 capsule_manager.py suggest "完成了" "调研任务"

# 技能健康检查
python3 skill_health_checker.py --report

# 配置快照
python3 config_snapshot.py check

# 上下文监控
python3 context_monitor.py status
```

## Cron 配置

```crontab
*/30 * * * * cd /path/to/vector-memory-self-evolution && python3 memory_cycle.py 30min --quiet
0 */6 * * * cd /path/to/vector-memory-self-evolution && python3 memory_cycle.py 6h --quiet
0 23 * * * cd /path/to/vector-memory-self-evolution && python3 memory_cycle.py daily --quiet
0 3 * * * cd /path/to/vector-memory-self-evolution && python3 memory_cycle.py 3am --quiet
```

## 依赖

- Python 3.10+
- Qdrant（向量数据库）
- MiniMax API（LLM 提取）
- Qwen3-Embedding-4B（向量编码）

## 目录结构

```
vector-memory-self-evolution/
├── memory_api.py          # 统一记忆 API
├── memory_layers.py       # L00-L10 架构定义
├── memory_cycle.py        # Cron 调度协调器
├── memory_hybrid.py       # BM25 + Vector + RRF
├── memory_brain.py        # Brain 置信度/胶囊系统
├── brain_agent.py         # 统一大脑 Agent 入口
├── context_builder.py     # 上下文组装器
├── task_router.py         # 子agent 决策引擎
├── pre_checkpoint.py      # 预检点管理
├── session_store.py       # 会话持久化
├── memory_fragment.py     # 记忆片段标准接口
├── memory_reinforcement.py # Ebbinghaus 强化引擎
├── qdrant_store.py        # Qdrant 存储层
├── embedding_client.py    # 向量编码客户端
├── conversation_counter.py # 对话轮次计数
├── capsule_manager.py     # 经验胶囊管理
├── watchdog.py            # 执行看门狗
├── config_snapshot.py     # 配置快照
├── context_monitor.py     # Token 使用率监控
├── memory_consts.py       # 共享常量
└── memory_config.py       # 配置管理
```

## License

MIT
