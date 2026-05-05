---
name: vector-memory-self-evolution
description: "十层主动记忆系统 v4.3.0 - 基于 Qdrant + Qwen3-Embedding-4B，融合艾宾浩斯遗忘曲线 + MiniMax LLM + Brain-v1.1.8 完整能力。统一搜索入口 brain_agent.py 调用 hybrid_search(BM25+Vector+RRF)。"
version: 4.3.0
author: 凌凌柒
changelog: |
  4.3.0 - 搜索链融会贯通：memory_api.search() → hybrid_search(BM25+Vector+RRF) → Ebbinghaus重排；brain_agent.py 升级为统一调度中枢；L10路由器接入混合搜索
  4.2.0 - 深度整合 Brain-v1.1.8：SessionStore/Fragment/Router/Checkpoint/ContextBuilder
  4.1.0 - 10层架构 + 周期协调器
  4.0.0 - 初始版本
---

# 十层主动记忆系统 v4.2.0

## 核心架构

```
L00  Sensory Buffer     → transcript JSONL（原始数据）
L01  Working Memory    → SESSION-STATE.md（WAL 协议）
L02  Session Archive   → 每日日志 + Session Handoff
L03  Typed Store      → error/correction/practice/event/gap
L04  Semantic Vector  → Qdrant 2560维 + Ebbinghaus 强化
L05  Pattern Extract  → LLM MiniMax M2.7 跨记忆规律
L06  Procedural Mem   → 工具模式注册（成功率追踪）
L07  Core Memory      → MEMORY.md（蒸馏核心知识）
L08  Identity Model   → SOUL.md + USER.md
L09  Deep Archive     → memory_archive/（低分记忆归档）
L10  Cross-Layer Query → 智能路由 + QMD 混合搜索
```

## 新增核心模块（Brain-v1.1.8 深度整合）

### session_store.py — 会话持久化存储

```
三层存储：sessions / decisions / tasks
原子写入：临时文件 → shutil.move
文件损坏：自动恢复，不崩溃

CLI:
  python3 session_store.py stats          # 统计
  python3 session_store.py sessions        # 会话列表
  python3 session_store.py decisions       # 搜索决策
  python3 session_store.py save-decision -t "标题" -c "内容"
  python3 session_store.py save-task <id> <status>
  python3 session_store.py cleanup 180    # 清理过期数据
```

### memory_fragment.py — 记忆片段标准接口

```
5 种片段类型：
  SkillFragment      — 技能/工具（priority 0.7）
  TaskFragment       — 当前任务（priority 0.9）
  ConfigFragment     — 配置变更（priority 0.8）
  ConclusionFragment — 结论决定（priority 0.6）
  LessonFragment     — 教训反思（priority 0.5）

FragmentPool — 按优先级注入，超上限自动截断

CLI:
  python3 memory_fragment.py  # 单元测试
```

### pre_checkpoint.py — 复杂任务预检点

```
创建 → 执行 → 标记完成（带进度追踪）

CLI:
  python3 pre_checkpoint.py create "任务" "计划" --confidence 7 --steps 3
  python3 pre_checkpoint.py complete <id> "结果"
  python3 pre_checkpoint.py list
  python3 pre_checkpoint.py check -- "rm -rf /tmp"
```

### task_router.py — 任务路由决策引擎

```
输入任务 → 输出完整路由决策 JSON

决策内容：
  - 置信度评估（信号表 + 级别）
  - 是否拆分（保守策略）
  - 模型选择（按任务类型）
  - subagent 数量（最多 3 个）
  - 是否需要对抗验证
  - 警告列表

CLI:
  python3 task_router.py "调研推特AI博主"         # 详细输出
  python3 task_router.py "修复bug" --json       # JSON 输出
  python3 task_router.py "对比A和B" --simple    # 简化输出
```

### context_builder.py — 智能上下文组装

```
分段注入（按优先级）：
  1. 用户信息（USER.md）— 始终注入
  2. 当前会话状态（L1 SESSION-STATE）
  3. 相关记忆（L4 向量搜索 top 5）
  4. 最近决策（decisions top 3）
  5. 核心记忆（MEMORY.md）
  6. 工作缓冲区

10K token 硬上限自动压缩

CLI:
  python3 context_builder.py build --query "量化交易"
  python3 context_builder.py quick --query "Docker"
```

## 原有核心模块

| 模块 | 层级 | 功能 |
|------|------|------|
| `memory_layers.py` | L00-L10 | 统一 10 层管理 + 跨层路由 |
| `memory_cycle.py` | 协调器 | 4 周期自动运行（30min/6h/daily/3am） |
| `memory_brain.py` | Brain 整合 | 置信度评估 + 胶囊成熟度 + Pre-checkpoint |
| `memory_hybrid.py` | L10 | QMD 混合搜索（BM25 + Embedding + RRF） |
| `memory_api.py` | L03-L04 | 结构化记忆 + Qdrant 统一接口 |
| `memory_reinforcement.py` | L04→L09 | 艾宾浩斯遗忘曲线强化 + 归档 |
| `qdrant_store.py` | L04 | Qdrant REST API |

## 🤖 BrainAgent 统一入口（v4.3.0 新增）

`brain_agent.py` 是整个系统的统一调度中枢，串联所有模块：

```
brain_agent.execute(task)
  1. confidence_check()     → 置信度评估（危险度打分）
  2. pre_checkpoint()       → 必要时创建任务快照
  3. route_task()           → 任务路由决策（拆分/模型/验证）
  4. ContextBuilder()       → 组装智能上下文
  5. increment_counter()    → 递增会话轮次
  6. session_store.save()   → 持久化会话快照
  → 返回完整执行链路 JSON
```

**CLI 用法：**
```bash
python3 brain_agent.py run "调研天气API"         # 完整流程
python3 brain_agent.py confidence "删除文件"      # 置信度评估
python3 brain_agent.py route "多平台内容发布"    # 任务路由
python3 brain_agent.py context -q "用户偏好"     # 组装上下文
python3 brain_agent.py cycle 30min              # Cron 周期调度
python3 brain_agent.py capture error "OAuth失败" "token过期"  # 捕获记忆
```

## 周期协调器

```bash
*/30 * * * * python3 memory_cycle.py 30min --quiet   # L00→L02 + L02→L03/L04
0 */6 * * * python3 memory_cycle.py 6h --quiet       # L04→L09 归档
0 23 * * * python3 memory_cycle.py daily --quiet     # L01→L07 蒸馏 + L05
0 3 * * * python3 memory_cycle.py 3am --quiet       # L03 压缩去重
```

## Brain v1.1.8 精华提炼

### 置信度评估（Brain 信号表 → memory_brain.py）

| 信号 | Delta | 说明 |
|------|-------|------|
| 不可逆操作（删除/rm） | -0.3 | 风险高 |
| 对外操作（发布/上线） | -0.2 | 后果严重 |
| 系统核心修改 | -0.2 | 牵一发动全身 |
| 安全相关（密码/密钥） | -0.2 | 安全第一 |
| 首次遇到 | -0.2 | 无先例可循 |
| 历史成功率高 | +0.1 | 有参考 |
| 任务明确简单 | +0.1 | 把握大 |
| 不确定性关键词 | -0.1 | 试试/可能 |

### 胶囊成熟度（Brain capsule → memory_brain.py）

- 🟡 **raw**：首次成功，等待验证
- 🟢 **tested**：连续成功 2 次，建议使用
- 🏆 **stable**：连续成功 5 次，默认复用

### QMD 混合搜索（Brain hybrid → memory_hybrid.py）

BM25 + Embedding + RRF（Reciprocal Rank Fusion）融合：
- BM25：精确关键词匹配
- Embedding：语义向量相似度
- RRF：两者取长补短，k=60 常数

## 依赖

| 包 | 用途 |
|----|------|
| httpx | Qdrant REST API + MiniMax API |
| rank-bm25 | BM25 关键词搜索 |

_Last Updated: 2026-05-05_
