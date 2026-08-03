# 记忆系统与注入链路 — 深度梳理

> 本文完整追踪"事件记忆 / 会话记忆从创建到注入 LLM 提示词"的全链路，
> 供排查记忆相关问题时快速定位。

---

## 一、数据模型与存储

**`MemoryEvent`**（`modules/memory/event_store.py`）— 记忆的最小单位：

| 字段 | 含义 |
|------|------|
| `id` | 唯一 ID |
| `fact` | 发生了什么（内容主体） |
| `thought` | 思考/反思 |
| `lesson` | 学到的可复用经验 |
| `keywords` | 关键词 |
| `importance` | 重要性 0~1 |
| `type` | `emotion`/`thought`/`fact`/`strategy`（决定遗忘曲线速率） |
| `time` | 创建时间（ISO） |
| **`session_id`** | **关联会话**（会话记忆的关键字段） |
| `owner_id` | 所属模型（large/supervisor/expert） |
| `embedding` | 向量（存 FAISS，不落 SQLite） |

**存储**：`EventStore`（`modules/memory/event_store.py`）— SQLite `events` 表 + FAISS 向量索引。

---

## 二、事件创建（写入路径）

事件在**会话任务结束后**由 `EventReducer` 提取，两条路径：

### Agent 路径
```
api_stream._post_task_extraction (line 873)
  └─ asyncio.sleep(30s)  # 任务结束后延迟提取（fire-and-forget）
     └─ EventReducer.reduce(session_id, conversation_text, owner_id)
        └─ 每条 MemoryEvent 打上 ev.session_id = session_id   ← event_reducer.py:139
        └─ 按 fact 前 60 字符 hash 去重
        └─ store.save_event()  # 自动向量化 + 写 SQLite + FAISS
```

### chatonly 路径
```
backend/chat/continuous_thinker._extract_memory (line 157)
  └─ 条件：MEMORY_REDUCE_ENABLED
  └─ EventReducer.reduce(session_id, conversation_text)  # 同样打 session_id
```

---

## 三、检索（读取路径）

**`EventRetrieval.retrieve(query, ...)`**（`modules/memory/event_retrieval.py`）：

```
1. 向量语义搜索   FAISS, top_k = max_results*3
2. 关键词搜索     补充候选
3. 因果图扩散     通过边关系找关联事件
4. 合并打分：
   score = (0.35*semantic + 0.20*importance + 0.20*recency
            + 0.15*utility + 0.10*frequency) * content_bonus
   └─ semantic < MIN_SEMANTIC_SIMILARITY(0.20) → 剔除
5. 归一化 → 阈值(默认0.06) → 排序 → 截断 max_results
```

其他检索入口：
- `retrieve_mixed(mix, ...)` — 多主题加权检索（记忆聚焦 `_memory_focus`）
- 显式工具：`event_query`（`infra/tool_manager/tools/event_query.py`）、`memory_match`/`memory_score`/`memory_batch_filter`（`memory_matcher.py`）— 模型主动调用时用
- `depth_recall` — 深度因果回忆（`modules/memory/depth_recall.py`，`should_trigger_deep_recall` 触发）

> ⚠ 注意：关键词命中的事件若未出现在向量结果里，会被赋 `semantic=0.1`，
> 低于 0.20 阈值被过滤 → **纯关键词命中实际永远被淘汰**（潜在问题）。

---

## 四、提示词注入 — Agent 路径

### 4.1 记忆片段的构建
`continuous_thinker._build_prompt(initial_question, round_num)`（line 577）：

```
创建 TurnContext(pool)
├─ guidance       【系统指令】                priority 10
├─ notebook       【当前任务进度记事本】       priority 15
├─ history        【历史输出（不得重复）】      priority 20
├─ MEMORY         【历史记忆】                priority 30   ← 本主题
├─ perception     【环境感知】                priority -
├─ blackboard     【协作上下文】              priority 50
├─ delegation     【当前委托状态】            priority 60
└─ skill_suggestion【技能建议】               priority -
```

### 4.2 记忆块逻辑（会话优先）
```python
# 1. 会话记忆：本会话产生的事件（session_id == self._session_id）
session_events = [e for e in store.list_events(500) if e.session_id == self._session_id]

# 2. 事件记忆补充
events = session_events
if self._memory_focus:            # 显式聚焦：始终召回
    events += retrieve_mixed(mix=self._memory_focus)
elif session_events:              # 有会话记忆 → 补充全局相关
    events += retrieve(query=initial_question)
# 无会话记忆且无聚焦 → events 为空 → 不注入

# 3. 格式化标注
【当前会话记忆】     ← 会话事件（不带日期）
【曾经发生的事】     ← 全局事件（每条带日期 YYYY-MM-DD）
```

### 4.3 进入 LLM
```
composer.build(pool, role, tier, question)
  └─ pool.view(role)  →  "【历史记忆】\n<记忆文本>\n\n【当前任务】\n<问题>"
     （TurnContext.view 按 priority 排序、按 target_roles 过滤、
       "历史记忆" 的 target_roles = (orchestrator, supervisor, expert)）

model_runner._generate(prompt)
  └─ messages = [
        ChatMessage("system", composer.build_system(...) + 时间上下文),   # 身份/规则/工具
        ChatMessage("user",  <含【历史记忆】的轮次上下文 + 【当前任务】>),  # 记忆在这
     ]
  └─ 进入工具循环 → LLM
```

---

## 五、提示词注入 — chatonly 路径

`backend/chat/continuous_thinker.think`（line 40）：

```
1. memory_context = _recall_memories(user_message, session_id)
      └─ 先检查会话已有历史对话（黑板上当前消息之前的对话）
      └─ 无历史对话 → 返回 ""（不注入事件记忆）
      └─ 有历史对话 → 深度回忆 + 浅层【曾经发生的事】(带日期 + "优先当前会话"注释)
2. context_messages = _slicer.slice(history, memory_context)
      └─ 记忆作为 system 消息："以下是从历史记忆中检索到的相关信息：..."
      └─ 滑动窗口 + token 预算裁剪（记忆优先计入预算）
3. system_prompt = _composer.build_system(memory_context)   # 记忆并入 system
4. _runner.run(messages=context_messages, system_prompt=system_prompt) → LLM
5. 后台 _extract_memory(session_id)  → 提取新事件
```

---

## 六、会话记忆到底是怎么注入的（结论）

**定义**：会话记忆 = **历史对话**（当前会话的对话消息，system prompt 顶部的 `【对话历史】`），
**不是**按 `session_id` 过滤的事件。

**事件记忆 = 全局记忆**：全局 `EventStore` 提取的事件，仅在会话已有历史对话时注入
（`【曾经发生的事】` + 日期 + "非当前会话、仅供参考、优先当前会话对话"注释）。

**注入规则（当前实现）**：
1. 会话记忆（历史对话）已由 system prompt 注入，不重复
2. 事件记忆（全局）仅在本会话已有历史对话时注入
3. **新会话（无历史对话）→ 不注入事件记忆**（避免无关历史污染）

**关键现状**：当前库 281 条事件中 **266 条 `session_id` 为空**（旧路径创建），
仅 15 条带标记——但按简化模型，`session_id` 标记已不再用于注入判断，
注入只依赖"会话是否有历史对话"。
- 新会话首轮：无历史对话 → 不注入事件记忆
- 多轮会话后：注入全局相关事件记忆（【曾经发生的事】）
- 旧的无标记事件：只在"有会话记忆的会话"里作为全局补充出现

---

## 七、完整调用链速查

```
[agent]  chat_gateway /stream/ws → api_stream.websocket_chat
         → system.think(session, input)
           → orchestrator.process
             → model_runner（large）
               → _build_runner_prompt → continuous_thinker._build_prompt
                 → EventStore.list_events(500) 过滤 session_id    ← 会话记忆
                 → EventRetrieval.retrieve(query)                 ← 全局补充
                 → ContextFragment("memory") → TurnContext
                 → composer.build → user_prompt（含【历史记忆】）
               → _generate → LLM
         → _post_task_extraction → EventReducer.reduce(session_id) ← 写事件

[chatonly] chat_gateway /stream/ws → _chatonly_ws
         → backend/chat/continuous_thinker.think
           → _recall_memories(session_id)                          ← 会话优先
           → ContextSlicer.slice + composer.build_system → LLM
         → _extract_memory → EventReducer.reduce(session_id)       ← 写事件
```

---

## 八、两套记忆系统（明确区分）

| | **历史对话** | **事件记忆** |
|---|-------------|-------------|
| 内容 | 当前会话的对话消息 | 全局历史中提取的事实/经验/思考 |
| 存储 | Blackboard observations（`context_type=conversation_history`）+ SQLite session_repo | `EventStore`（SQLite + FAISS），`MemoryEvent` |
| 注入位置 | system prompt **最顶部**（`【对话历史】`，`_build_system_prompt_for_mode` line 1282） | user 消息（`【历史记忆】`，`continuous_thinker._build_prompt` 记忆块） |
| 作用域 | 仅当前会话 | 全局，按 `session_id` 区分会话记忆 |
| 写入 | 每轮对话直接追加 | 任务结束后 `EventReducer` 提取 |

两者互不依赖、独立存储。

---

## 九、简化模型：两套记忆系统各自独立注入

**最终模型**（用户确认）：
- **会话记忆 = 历史对话**：当前会话对话消息，已作为 `【对话历史】` 注入 system prompt 顶部（`_build_system_prompt_for_mode` line 1282）。**不再单独注入"会话事件"**。
- **事件记忆 = 全局记忆**：全局 `EventStore` 提取的事件，以 `【曾经发生的事】`（标注日期 + 注释"非当前会话、仅供参考、优先当前会话对话"）注入 user 消息。
- **AI 知道当前时间**：`_build_time_context()`（model_runner.py:1334）给 system prompt 注入 `【当前时间】` + `【对话对象】` + `【上次对话】`。

**事件记忆注入 gate**（`continuous_thinker._build_prompt` + `backend/chat/continuous_thinker._recall_memories`）：
```
if _memory_focus:               # 显式记忆聚焦 → 始终注入
    events = retrieve_mixed(...)
elif 会话已有历史对话:            # 本会话已产生过思考/对话
    events = retrieve(query, ...)   # 全局语义检索
else:                           # 新会话（如只发"1"）→ 不注入事件记忆
    events = []
```
- 新会话第一轮（无历史对话）→ 不注入事件记忆，避免无关历史污染
- 多轮会话（有历史对话）→ 注入与当前问题相关的全局事件记忆（`【曾经发生的事】`+日期）

**验证**：新会话"1"→ 不注入；多轮会话 → 注入全局相关事件；显式聚焦 → 始终注入。

---

## 十、黑板共享记忆修复（角色别名）

**问题**：`TurnContext.view(role)` 按 `role in target_roles` 过滤，而 `continuous_thinker._build_prompt` 里
`role = getattr(self, '_role', 'orchestrator')` 恒为 **'orchestrator'**。但大部分片段（协作上下文/委托状态/
记事本/历史输出）的 `target_roles=("large",)` → **大模型（总指挥）看不到这些片段**，包括：

- `【协作上下文】`（黑板共享记忆：外部 prompt builder 输出，含专家发现 / MessageBus 消息）
- `【当前委托状态】`
- `【当前任务进度记事本】`、`【历史输出】`

→ 多模型协作的产出没能完整回到总指挥（潜在协作断裂）。

**修复**（`modules/thinking/context/pool.py` `view()`）：
- 把 `"large"` 与 `"orchestrator"` 视为**同一角色（总指挥）的两种写法**
- 查看角色是二者之一时，能看到 `target_roles` 含任一别名的片段
- supervisor / expert 仍精确匹配，行为不变

**验证**：`view('orchestrator')` 现在返回全部片段（系统指令/记事本/历史输出/历史记忆/**协作上下文**/委托状态）；
`view('supervisor')` 只返回系统指令+历史记忆；`view('expert')` 只返回历史记忆。


