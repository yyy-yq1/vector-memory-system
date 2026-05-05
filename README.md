# Vector Memory System — 自我进化向量记忆系统 v4.3.0

融合 **Brain-v1.1.8** 完整能力的十层主动记忆架构，基于 Qdrant 向量数据库 + Qwen3-Embedding-4B + 艾宾浩斯遗忘曲线。已部署至生产环境。

---

## 能力概览

| 能力 | 说明 |
|------|------|
| **混合搜索** | BM25（关键词）+ 向量（语义）+ RRF 融合，关键词精确匹配与语义理解兼得 |
| **艾宾浩斯遗忘曲线** | 记忆强化：被检索越多越耐久；低分记忆自动归档至 L9 |
| **LLM 智能提取** | 从 transcript 自动识别 error/correction/practice/event/gap 五类记忆 |
| **十层分级存储** | L0-L10 分层，从瞬时感官到身份模型，层间路由智能 |
| **WAL 热内存** | 每次响应前写入 SESSION-STATE.md，崩溃 / compaction 后恢复现场 |
| **会话快照与交接** | session 切换时生成 handoff 摘要，防止记忆断层 |
| **经验胶囊** | 最佳实践封装为可复用胶囊，任务完成自动建议 |
| **置信度评估** | 执行危险命令前评估风险，自动打预检点 |
| **看门狗重试** | 超时自动指数退避重试，预算智能分配 |
| **上下文压缩** | OpenClaw compaction 配置优化，防止 session token 爆满 |

---

## 架构详解

```
L00  感官缓冲   → transcript JSONL（只写不读，永久原始数据）
L01  工作记忆   → SESSION-STATE.md（WAL 协议，响应前写入，崩溃恢复）
L02  会话归档   → memory/YYYY-MM-DD.md（每日会话日志 + session handoff）
L03  类型存储   → error / correction / practice / event / gap 五类片段
L04  语义向量   → Qdrant 2560维（Qwen3-Embedding-4B）+ 艾宾浩斯强化
L05  规律提取   → MiniMax LLM M2.7 跨记忆归纳模式
L06  程序记忆   → 工具使用成功率 + 命令模式注册表
L07  核心记忆   → MEMORY.md（蒸馏精华，长期知识）
L08  身份层     → SOUL.md + USER.md + IDENTITY.md（人格 + 用户模型）
L09  深度归档   → 低分记忆归宿（Ebbinghaus 触发，可恢复）
L10  跨层路由   → 智能选择最优层级组合进行查询
```

---

## 核心能力详解

### 1. 混合搜索（BM25 + Vector + RRF）

**三层融合搜索**，解决单一搜索的局限：

- **BM25**：精确关键词匹配（搜 "Docker" → 精确命中 "Docker"）
- **向量搜索**：语义理解（搜 "容器工具" → 也能找到 Docker）
- **RRF 融合**： Reciprocal Rank Fusion，k=60，对两种排名取长补短

```python
from memory_hybrid import hybrid_search
results = hybrid_search("飞书文档创建", n_results=5)
# 返回: [{"score", "content", "source", "method", "reinforced_at"}, ...]
```

**中文分词**：基于字符 n-gram（2-gram + 3-gram）+ 英文驼峰分割，无需额外中文分词库。

---

### 2. 艾宾浩斯遗忘曲线强化

记忆不是静态存储，而是动态衰减与强化：

```
final_score = base_similarity × exp(-λ × days) + α × log(1 + access_count)

λ = 0.03  （遗忘速度）
α = 0.25  （强化力度）
```

- **强化**：每次被 `hybrid_search` 命中，`access_count++`，记忆更耐久
- **遗忘**：超过设定天数无访问，分数持续衰减
- **归档**：得分 < 0.10 标记为待归档，进入 L9 深度归档层

---

### 3. LLM 智能记忆提取

从 OpenClaw transcript JSONL 自动提取五类记忆：

| 类型 | 优先级 | 触发模式 |
|------|--------|----------|
| `error` | 最高 | "error", "failed", "❌", 堆栈跟踪 |
| `correction` | 高 | "错误做法→正确做法", "❌→✅" |
| `practice` | 中 | "最佳实践", "成功", "✅" |
| `event` | 中 | 重要事件、决策点 |
| `gap` | 低 | "待修复", "未知", "空白" |

**防重机制**：
- 内容 hash 去重（跨文件）
- 同文件内去重
- 最短长度过滤（< 30 字符丢弃）
- 字符密度过滤（去除大量空白/代码行）

```bash
# 提取最近3天的记忆
python3 extract_memories.py --llm --days 3

# 强制执行（忽略时间检查）
python3 extract_memories.py --llm --days 3 --force
```

---

### 4. WAL 热内存（工作记忆）

SESSION-STATE.md 是 AI 的"内存条"——每次响应前写入，崩溃后恢复现场。

**触发条件**：
- 用户给出具体事实/偏好/决定 → 立即写入
- 用户纠正 AI → 立即写入
- 任务完成/关键里程碑 → 写入
- 每轮对话结束 → 自动追加

**文件结构**：
```
## Current Task        ← 当前任务
## Key Context        ← 关键上下文
## Pending Actions     ← 待办事项
## Recent Decisions    ← 近期决策
## User Preferences   ← 用户偏好（实时）
## Important Facts     ← 重要事实
```

---

### 5. 会话快照与交接（Handoff）

session 切换时防止记忆断层：

```
旧 session 结束 → 生成 SESSION-STATE 快照 → 写入 handoff 文件
新 session 开始 → 读取 handoff → 恢复上下文
```

每日会话日志 `memory/YYYY-MM-DD.md` 记录所有会话摘要，可回溯历史对话。

---

### 6. 经验胶囊（Capsule）

将最佳实践封装为可复用的"经验胶囊"：

```bash
# 列出所有胶囊
python3 capsule_manager.py list

# 自动建议胶囊（任务完成后）
python3 capsule_manager.py suggest "完成了" "多平台内容发布"

# 创建胶囊
python3 capsule_manager.py create <name> <type> <pattern>

# 更新成熟度
python3 capsule_manager.py update <name> --maturity tested
```

**胶囊类型**：`pattern` / `procedure` / `lesson` / `reference`

---

### 7. 置信度评估与预检点

对危险操作自动评估风险等级：

```python
from auto_confidence_trigger import assess_danger_level
level = assess_danger_level("删除所有文件")
# 返回: {level, warning, auto_checkpoint}
```

| 风险等级 | 行为 |
|----------|------|
| 低危 | 正常执行 |
| 中危 | 输出警告，建议备份 |
| 高危 | 自动打预检点（快照），强制确认 |
| 极高危 | 拒绝执行，建议改用 dry-run |

预检点记录：任务进度快照 + 当前状态，可随时回滚。

---

### 8. 看门狗（Watchdog）

超时重试 + 预算智能分配：

```python
from watchdog import exec_with_retry, wrap_tool_call

# 执行命令，超时自动重试（指数退避）
result = exec_with_retry("python3 build.py", timeout=60000, max_retries=3)

# 工具调用包装（pre-check + timeout + retry）
result = wrap_tool_call("file_write", path, content)
```

**重试策略**：指数退避，`delay = min(1000 × 2^(attempt-1), 10000)` ms

---

### 9. 上下文构建器

6段式上下文组装（用于注入 session 背景）：

```
[会话续接]    → Handoff 摘要 + 当前任务
[记忆检索]    → hybrid_search 相关记忆
[活跃任务]    → SESSION-STATE 当前任务
[用户偏好]    → 已知的用户偏好
[关键事实]    → 本会话中的重要决定
[系统提示]    → 必要的工作规范
```

---

### 10. 周期协调器

4个 cron 任务自动调度：

| 周期 | 职责 |
|------|------|
| `30min` | token 检查 + 碎片整理 |
| `6h` | 归档检查 + 记忆统计 |
| `daily`（23:00） | 全量提取 + 记忆汇总 |
| `3am` | 低分记忆归档 + Qdrant 整理 |

```bash
python3 memory_cycle.py status   # 查看各周期上次执行时间
python3 memory_cycle.py health   # 健康检查
python3 memory_cycle.py full     # 手动执行全量周期
```

---

## 搜索链路完整图

```
用户查询
  │
  ▼
memory_api.search(query)
  │
  ▼
memory_hybrid.hybrid_search(query, n_results)
  │
  ├─→ BM25 搜索（markdown 文件，关键词精确）
  │     tokenize_chinese() → BM25Okapi → rank scores
  │
  ├─→ Qdrant 向量搜索（2560维 Qwen3-Embedding-4B）
  │     embedding_client.get_embeddings() → qdrant_store.query()
  │
  └─→ RRF 融合（k=60）
        score = Σ 1/(k + rank_i)
        融合两路排名，取长补短
  │
  ▼
Ebbinghaus fluid_score 二次重排
  score × exp(-λ×days) × (1 + α×log(1+access_count))
  │
  ▼
reinforce() 命中自动强化
  access_count++ → 记忆更耐久
  │
  ▼
返回 ranked results
```

---

## 快速开始

```bash
# 搜索记忆（最常用）
python3 memory_api.py search "飞书"

# 捕获错误/最佳实践
python3 memory_api.py capture error "OAuth失败" "token过期"
python3 memory_api.py capture practice "批量操作" "先 dry-run"

# 置信度评估（危险命令）
python3 brain_agent.py confidence "rm -rf /"

# 自动预检点
python3 brain_agent.py ensure_checkpoint "部署到生产"

# 经验胶囊
python3 capsule_manager.py list
python3 capsule_manager.py suggest "完成" "多平台发布"

# 上下文组装（查询相关背景）
python3 brain_agent.py context -q "用户偏好"

# 健康检查
python3 skill_health_checker.py --report
```

---

## 记忆质量过滤器

capture() 入口强制执行三层质量门控，从源头阻止垃圾记忆入库：

| 规则 | 条件 | 拒绝示例 |
|------|------|---------|
| 最小长度 | 正文 ≥ 10 字符 | `批量1`（3字）|
| 字符去重 | 单一字符 ≤ 60% | `xxxxxxxxxxxxxx...`（全x）、`啊啊啊啊啊啊啊` |
| 有效密度 | 有效字符 ≥ 5 | `hi`（英文太少）|

不合格记忆直接返回 `{ok: False}`，不写文件不进 Qdrant。

---

## Crontab 配置

```crontab
*/30 * * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 30min --quiet 2>/dev/null
0 */6 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 6h --quiet 2>/dev/null
0 23 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py daily --quiet 2>/dev/null
0 3 * * * cd /root/.openclaw/workspace/skills/skills/vector-memory-self-evolution && python3 memory_cycle.py 3am --quiet 2>/dev/null
```

---

## 依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行时 |
| Qdrant | 运行中 | 向量数据库 |
| rank_bm25 | latest | BM25 关键词搜索 |
| qdrant-client | latest | Qdrant Python SDK |
| httpx | latest | 异步 HTTP（Embedding API） |
| pyyaml | latest | 配置文件解析 |

```bash
pip install qdrant-client rank_bm25 httpx pyyaml
```

---

## 配置

| 配置项 | 当前值 |
|--------|--------|
| 向量维度 | 2560 |
| Embedding 模型 | `Qwen/Qwen3-Embedding-4B` |
| Embedding Provider | SiliconFlow |
| Qdrant 地址 | `127.0.0.1:6333` |
| Collection | `memories` |
| RRF 常数 k | 60 |
| 遗忘 λ | 0.03 |
| 强化 α | 0.25 |
| 归档阈值 | 0.10 |

API Key 通过 `EnvironmentFile=/root/.openclaw/gateway-env` 自动注入。

---

## 部署状态

```
✅  Qdrant            systemd enabled, running on 127.0.0.1:6333
✅  OpenClaw Gateway  systemd enabled, running on port 18621
✅  SiliconFlow       API Key 已注入，Embedding 正常
✅  Skill 加载        vector-memory-self-evolution ready
✅  Crontab           4个周期任务已配置
✅  GitHub            main branch, latest commit pushed
```
