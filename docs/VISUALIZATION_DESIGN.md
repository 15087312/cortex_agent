# 思考与工具调用可视化设计（事件模型）

> 状态：Phase 0–3 已实施（工具结构化事件 + 持久事件日志 + 前端回放 + 工具卡片 + 正文流式）；Phase 4 已实施（`session_projection` 投影 + `session_request` 可应答帧）
> 目标：把“模型在想什么、调了什么工具、中途说了什么”做成可回放、可交错、可扩展的过程视图；对齐 dsh（`deepseek-harness`）的可视化架构，但不照搬其插件系统。
> 参考实现：`deepseek-harness/packages/core/agent-loop`、`packages/core/tools`、`packages/host/apiproxy`、`packages/client/runtime`
> 现有落点：`modules/thinking/api_stream.py`、`modules/thinking/core/model_runner.py`、`frontend/src/stores/chat.js`、`frontend/src/pages/Chat.vue`
> Phase 0 落点：`modules/thinking/tool_events.py`、`ModelRunner._push_tool_event`、`frontend/src/stores/chat.js`（`addToolCall/addToolResult`）
> Phase 1 落点：`modules/database/session_events.py`、`api_stream._record_event`、`api_stream.get_session_events`、`chat_gateway.get_session_events`、`chat.js::replayToolEvents`
> Phase 2 落点：`modules/thinking/tool_view.py`、`tool_events.build_call_payload/build_result_payload`、`dev_tools.write_text_file`（diff）、`frontend/src/components/tools/*.vue`、`Chat.vue`（`<ToolCard>`）
> Phase 3 落点：`ModelRunner._push_stream_chunk` + `_on_token`、`chat_gateway._consume_turn` / `_chatonly_sse`（`chunk` 帧 + `_record_user_message`）、`chat.js`（`streamTail/appendStreamChunk/clearStream`、轨迹 `afterKey`、`replayToolEvents` 锚点/时间回退/终稿锚点）、`Chat.vue`（`_onChunk` + 流式尾巴 + 轨迹入流）、`ChatMessage.vue`（`isStreaming` 光标）、`api_stream`（`user_message` durable 事件）、`cli_tui/services/ws_client.py`（`tool` / `chunk` 新帧）、`cli_tui/screens/repl.py`（`session_projection`）
> Phase 4.b（可应答帧）落点：`modules/thinking/pending_requests.py`、`api_stream`（调度事件登记、WS 连上补发、响应后 `resolve_and_broadcast`、断线 `clear`）、`chat_gateway`（会话清理）、`chat.js`（`pendingRequests/applyPendingRequests/resetPending`）、`Chat.vue`（`session_request` 帧 + 输入区接管）、`ChatInput.vue`（`blocked`）、`ChatMessage.vue`（失效文案）
> Phase 4（投影）落点：`modules/database/session_projections.py`、`modules/thinking/projections.py`、`api_stream`（ `_collect_process_snapshot` 复用 `build_runners`、回合末 `emit_runner_state`、连上即 `push_current`）、`model_runner`（`_push_todo_update` → `todos` 投影、`_record_usage` → `tokenUsage`）、`chat_gateway`（`GET /sessions/{id}/projections`、删除会话同步清理）、`chat.js`（`applyProjection/applyRunnerSnapshot/loadProjections`、`higher-seq-wins`）、`Chat.vue`（`session_projection` 帧、todos/上下文占用改读 store）

---

## 1. 结论（TL;DR）

dsh 的可视化不是“额外推送 UI 指令”，而是**把会话日志当唯一真源，UI 只是对日志的投影**：

1. **typed durable event log**：每个 chunk、每次工具调用都是带 `seq` 的持久事件，调用与结果显式配对；
2. **工具自述展示**：工具返回类型化渲染意图（terminal / diff / read / search / web / generic），宿主实时算 view，`meta` 随结果持久化；
3. **帧 + 投影**：WS 只推 `session/event`、`session/projection`、`session/queue`、可应答帧；重连靠 `lastSeq` 补发；
4. **客户端 reducer**：把事件 fold 成有序节点，按 node kind 分派组件；运行时与历史回放共用同一个 reducer。

我们现在的对应物是**一句句中文文本**：工具执行完生成 `read_file: done (1234 chars)` 这样的摘要，前端用正则 `_isToolTrace` 猜它是工具还是思考。参数、结果、耗时、错误结构、文件位置、diff 全部丢失，工具卡片因此做不出来，回放与实时也是两套代码。

本设计的最小可行路径是 **Phase 0（补结构化工具事件）→ Phase 1（事件日志骨架 + reducer）→ Phase 2（工具卡片）→ Phase 3（增量流式）→ Phase 4（投影/可应答帧）**。Phase 0–1 完成后，“刷新后与运行中长得一样”即自动成立。

---

## 2. dsh 的四层机制（代码位置）

### 2.1 事件日志

`packages/core/agent-loop/src/agent.ts`：

- 每个流式 chunk 单独 append `assistant/chunk`，收集返回的 `seq`；
- 落定消息 `assistant/message` 携带 `surfaceOp: 'append'` 与 `sourceEventSeqs: chunkSeqs`（chunk 与消息的归属关系）；
- `turn/start` / `step/start` / `turn/end` 划出层次，`step` 内才允许工具与 chunk；
- `packages/core/agent-loop/src/tool-calls.ts`：先 `session.append('tool/call', {turn, step, callId, name, arguments})` 拿 seq，结果 `tool/result` 用 `sourceEventSeqs: [callSeq]` 回指，并携带 `error.info` 与 `meta`（工具私有的展示载荷，如 diff，**持久化**用于回放重建卡片）。

### 2.2 工具自述展示

`packages/core/tools/README.md#tool-owned-ui-presentation`：

```
call:   {card:'generic'|'terminal'|'diff', title, ...}
result: {card:'generic'|'terminal'|'diff'|'search'|'read'|'web', ...}
```

关键约束：presenter 只依赖「参数 + 持久化结果」，因此 **live 与 replay 走同一个函数**；`output.presentationMeta(args, value)` 的产物随 `tool/result` 持久化，再次回放时喂给 `presentResult`；返回 `undefined` 落回 generic。

### 2.3 帧与投影

`packages/host/apiproxy/src/api/events.ts`：

- `session/event {sessionId, event, view?}`：`view` 是宿主实时计算、**不落库**的 `ToolEventView`；
- `session/projection {key, value, seq}`：派生状态（todo、tokenUsage、sessionStats、contextPressure），客户端按 higher-seq-wins；
- `session/queue`、`approval/requested`、`question/requested`：瞬态但**整快照**，重连即收敛；
- `session/subscribed {lastSeq}`：订阅基线；`since` 参数在 v1 未实现，重连 = 重开流 + 重取 history。

### 2.4 客户端组装

`packages/client/runtime/src/client/sessions/conversation-assembler.ts`：按 `seq` 二分插入，生成有序 `ConversationNode`（`conversation.ts` 中 `user / assistant / steering / context / model-retry / tool-result / turn-error / compaction / command / unknown`）；`tool-call-tree.ts` 负责子调用（Code Dispatch）父子配对与递归投影。

渲染侧（`packages/client/ui-conversation/README.md`、`packages/client/ui-tool/README.md`）：

- 流式尾内容单独隔离（streaming tail isolation），落定后并入正文；
- Think 行默认折叠，流式中摘要跟随最新一行，落定后回到首行；
- 工具行由 `conversation.chat.node` + `tool.call.toolview` 按工具名 keyed 分派，未注册工具落 generic；
- 运行/成功/失败/中断状态只从冻结的 call/result slice 推导。

---

## 3. 我们的现状

| 层 | 现状 | 位置 |
|----|------|------|
| 事件壳 | `_build_event(type, event, content, data)`，`content` 是给人看的中文句子；`_seq` + 200 条 deque 仅用于断线补发 | `modules/thinking/api_stream.py:237`、`:209-221`、`:1568` |
| 工具 | **无工具事件**。执行完拼一行摘要写黑板 → message_bus 广播 → `_format_scheduler_event` 拼成 `🧠 [总指挥] [R3] 工具 read_file run 成功` | `model_runner.py:3107`、`api_stream.py:497` |
| 中途的话 | 主管/专家每轮整段 content（`_push_expert_output`）；reasoning 每轮整段（`_push_reasoning`）；流式 delta 累计 100 字符/换行才以纯文本广播 | `model_runner.py:1422`、`:1378`、`:2422` |
| 客户端 | 单个 pinia store，用正则分类：`_isToolTrace`、`classifyThinking`、`_cleanThinking`；工具进 `traces[]` 纯文本 | `frontend/src/stores/chat.js:12`、`:615` |
| 回放 | 运行时走 `dispatchThinking`，恢复时在 `switchToSession` 里另写一遍聚合（`expertAgg` 增量 slice、`pendingLargeThinking`、`hasProcessAgg`） | `chat.js:187-360` |
| 持久化 | `chat_messages` 混存 user/assistant 与 `thought / process / mental` 文本行 | `modules/database/chat_models.py:39`、`NON_DIALOG_ROLES` |

**根因**：事件里没有数据，只有描述。`read_file` 成功与 `web_search` 成功在协议层完全同形，所以无法做卡片、配对、状态、耗时、diff、跳转；前端只能靠正则和文本前缀猜语义。

次要缺陷：

- 无 chunk 级顺序 → “说一句、调工具、再接着说”无法还原，工具只能进另一个面板；
- 无投影 → todo/token/context 靠心跳与轮询；
- UI 单体 → 加一个工具要同时改正则、`ChatMessage.vue` 和 `ProcessPanel`。

---

## 4. 目标架构

```
LLM stream ──► Agent Loop ──► session_events (SQLite, append-only, seq)
                    │                     │
                    ├─ tool/call ─────────┤
                    └─ tool/result ───────┤──► WS: session/event (+view)
                                          └──► WS: session/projection (seq 水位)
                                                       │
                                             前端 reducer (events → nodes)
                                                       │
                                    ChatFlow（文本/Think/工具/委托按 seq 交错）
```

### 4.1 事件表

新增表（沿用项目 SQLAlchemy + `get_session_repo()` 的建模方式，放在 `modules/database/`）：

```sql
CREATE TABLE session_events (
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,          -- 每会话单调递增，从 1 开始
    type        TEXT NOT NULL,             -- 见 4.2
    turn        INTEGER DEFAULT 0,
    step        INTEGER DEFAULT 0,
    ts          REAL NOT NULL,
    data        TEXT NOT NULL,             -- JSON
    source_seqs TEXT DEFAULT '[]',         -- 归属的先行事件 seq（chunk→message、call→result）
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX ix_session_events_type ON session_events(session_id, type, seq);
```

`seq` 由服务端在 append 时分配（`SELECT MAX(seq)+1` 在单写锁内，或内存计数器启动时对齐 DB）。**客户端永远不生成 seq**。

保留策略（避免 chunk 无限膨胀）：

- `assistant/chunk` 保留 7 天或每会话最多 2 万条，超出按 seq 删除；
- `assistant/message`、`tool/call`、`tool/result`、`turn/*` 永久保留；
- 删除后重连补发若跨过缺口，返回 `history_required`，前端改为重取窗口。

### 4.2 事件类型

| type | data 字段 | 说明 |
|------|-----------|------|
| `turn/start` | `{turn, model_id, tier, identity_name}` | 一轮开始 |
| `step/start` | `{turn, step, purpose?}` | 一次模型调用 |
| `assistant/chunk` | `{turn, step, delta}` | 流式增量，只给“尾巴” |
| `assistant/message` | `{turn, step, content, reasoning?, usage?, stop_reason}` | 落定正文；`reasoning` 为该步思考 |
| `tool/call` | `{turn, step, call_id, name, args, identity}` | 参数为脱敏后的预览（见 7.3） |
| `tool/result` | `{turn, step, call_id, call_seq, status, duration_ms, content, error?, meta?}` | `status ∈ ok/error/timeout/denied/interrupted` |
| `delegation/start` | `{turn, step, call_id, role, task, target_model_id}` | 委托/创建主管，`call_id` 挂在父 tool 上 |
| `delegation/end` | `{call_id, status, duration_ms, result_preview}` | 委托结束 |
| `todo/write` | `{items:[{id, text, status}]}` | 计划变更（供 projection） |
| `approval/requested` | `{request_id, call_id, target, detail}` | 可应答 |
| `approval/resolved` | `{request_id, approved, reason}` | |
| `turn/end` | `{turn, reason}` | `completed / stopped / error / max_turns` |
| `error` | `{turn, step, code, message}` | 无工具归属的失败 |

**委托复用工具树**：`delegation/start` 的 `call_id` 与父 `tool/call` 相同，前端直接按 `parent_call_id` 建树。这是我们相对 dsh 的增量——`large → supervisor → expert` 的层级天然是子调用树，现有 `ProcessPanel` 的模型树快照已经证明信息结构成立，只是它是快照而非事件。

### 4.3 WS 帧

过渡期**复用现有 envelope**，不新开通道（CLI TUI 只认 `type`，未知 type 自动忽略）：

```json
{"type":"session_event","event":"tool/result","session_id":"...","seq":42,
 "data":{"payload":{"type":"tool/result","turn":2,"step":3,"call_id":"c1","call_seq":40,
                    "status":"ok","duration_ms":812,"content":"...","meta":{...}},
         "view":{"card":"terminal","exitCode":0,"output":"..."}}}
```

补充帧：

| frame | 用途 |
|-------|------|
| `session_event` | 单条事件 + 实时 view |
| `chunk` | 正文流式增量（瞬时，不落库；`content` 即增量文本，`data.turn` 标轮次） |
| `session_subscribed` | `{last_seq}` 基线 |
| `session_projection` | `{key, value, seq}`（todos / tokenUsage / contextPressure / runners） |
| `session_queue` | 待处理输入整快照（多会话并行下防错乱） |
| `session_request` | 待审批/待提问整快照 `{requests: [{request_id, kind, ...}]}`（无 seq、幂等覆盖；替代混在消息流里的 `kind=approval/intent`） |
| `history_required` | 补发跨越裁剪缺口 |

重连：客户端带 `last_seq` 连上 → 服务端补发 `seq > last_seq` → `sync_complete`。现有 `_event_buffers` 环形缓冲在 Phase 1 删除，改为查事件表。

### 4.4 工具展示契约

新增 `modules/thinking/tool_view.py`（宿主侧纯函数，无 IO）：

```python
def present_call(name: str, args: dict) -> dict | None: ...
def present_result(name: str, args: dict, result: str, status: str,
                   meta: dict | None) -> dict | None: ...
```

渲染意图（与 dsh 同构，字段按我们工具实际返回裁剪）：

```python
{"card": "terminal", "title": "pytest -q", "cwd": "...", "exit_code": 0, "output": "..."}
{"card": "diff", "title": "edit config/settings.py", "diffs": [{"path": ..., "hunks": [...]}]}
{"card": "read", "title": "settings.py", "path": "...", "offset": 1, "lines": [{"number": 1, "text": "..."}], "total_lines": 1375}
{"card": "search", "shape": "matches", "title": "grep KNOWLEDGE_", "matches": [{"path": ..., "line": 12, "text": ...}], "truncated": false, "total": 3}
{"card": "web", "kind": "search", "title": "...", "sources": [{"title": ..., "url": ...}]}
{"card": "generic", "title": "knowledge_search", "content": "..."}
```

- `view` 由宿主实时计算；本项目沿用现有 envelope 结构，`view` 随事件一起落库（回放直接使用），
  但重建所需的一切都在 `args + meta` 里，展示层升级后可以随时改为重算；
- `meta` 随 `tool/result` 落库（如 diff、lines、sources、文件路径），是回放重建卡片的唯一额外输入；
- 前端按 `card` 分派组件，未注册工具落 generic（不崩、不特殊化）。

### 4.5 客户端 reducer

新增 `frontend/src/stores/trace.js`，输入 `session_event`，输出节点数组：

```js
// node: {id, kind, seq, turn, step, ...}
// kind: 'user' | 'assistant' | 'think' | 'tool' | 'delegation' | 'approval' | 'intent' | 'error' | 'turn-end'
function apply(ev) {
  switch (ev.type) {
    case 'assistant/chunk':      // 只写 tail，不落节点
    case 'assistant/message':    // 落定：tail → assistant 节点；reasoning → think 节点
    case 'tool/call':            // 建 running 节点，占位在 seq 位置
    case 'tool/result':          // 按 call_seq 原地替换为 settled 节点
    case 'delegation/start':     // 建子节点，parent_call_id = call_id
    case 'turn/end':             // 关闭本轮流式状态
  }
}
```

规则：

- 节点按 `seq` 排序，**文本与工具在同一条流里交错**（这是“看得见思考过程”的观感来源）；
- `assistant/chunk` 只更新尾部节点，不产生新节点，避免碎片；
- `tool/call` 先占位、`tool/result` 原地替换，保证工具卡片在调用瞬间就出现“运行中”；
- 历史回放复用同一 `apply`（从事件表分页读取），`chat.js` 中现有的 `classifyThinking` / `expertAgg` / `pendingLargeThinking` 全部删除。

节点到组件的映射：

| kind | 组件 |
|------|------|
| assistant | `ChatMessage.vue`（正文 + 打字机 + meta 展开） |
| think | `ThinkRow.vue`（默认折叠，流式显示最新一行） |
| tool | `ToolCard.vue` → `TerminalCard / DiffCard / ReadCard / SearchCard / WebCard / GenericCard` |
| delegation | `DelegationRow.vue`（负责人 + 任务 + 状态，可展开子节点） |
| approval / intent | `ApprovalCard.vue` / `IntentCard.vue`（接管输入区） |
| error | `ErrorRow.vue` |

---

## 5. 分阶段实施

### Phase 0 — 工具结构化事件（已实施）

- `modules/thinking/tool_events.py`：参数脱敏（`api_key|token|secret|password…` → `***`）、逐叶截断、载荷构造；
- `model_runner.py`：正常工具调用前后各发一条 `type=tool` 事件（`tool_call` / `tool_result`），`data` 为 `{phase, call_id, name, args, status, duration_ms, result_chars, result_preview, error, tier, model_id, identity_name}`；状态覆盖 `ok/error/timeout/denied/invalid`；只发本会话连接；
- `chat.js`：`traces[]` 改存对象，`addToolCall` 建“运行中”行，`addToolResult` 按 `call_id` 原地补齐；旧文本工具摘要只进过程流（不再冒充轨迹）；
- `Chat.vue` / `components.css` / i18n：轨迹行渲染工具名、参数、状态徽标、耗时与结果预览（悬停显示完整参数）；
- 测试：`tests/unit/test_tool_events.py`（12 例：脱敏/截断/载荷/推送兜底）、`tests/unit/test_model_runner_ext2.py::test_generate_with_tools_emits_structured_tool_events`（调用→结果配对）、`frontend/src/stores/chat.spec.js` 与 `frontend/src/pages/Chat.spec.js` 中的轨迹用例。

已知边界：事件只推给在线连接，不落库（刷新后不会重建）；历史会话仍靠 `chat_messages` 的文本摘要重建，因此旧会话的轨迹面板为空、摘要留在过程流。这是 Phase 1 要解决的核心问题。

验收：跑一个含 `read_file` + `web_search` 的任务，轨迹面板显示工具名、参数、耗时、成功/失败，且与正则无关。

### Phase 1 — 事件日志骨架（已实施，缩减版）

已实施：**持久事件日志 + 工具轨迹回放**，解决“刷新后轨迹消失”这一核心缺口。

- `modules/database/session_events.py`：`SessionEvent` 表（主键 `(session_id, seq)`）+ `SessionEventRepository`；
  - `append_envelope` 为每个 WS envelope 分配每会话递增 `seq` 并写回 `envelope['_seq']`；
  - 只有 `DURABLE_TYPES`（thinking/message/tool/mental/error/done/todo）落库，progress/thinking_progress/ack/ping 等瞬态事件只占 seq 不落库 → 心跳不会把表撑大；
  - 重启后 seq 从库内 `MAX(seq)` 对齐；主键冲突自动重试一次；
  - `list_events(after_seq, limit)` 返回与 WS 帧同构的 envelope；`clear()` 供会话删除/清空调用。
- `api_stream._record_event`：分配 seq + 落库 + 进环形缓冲；任何 DB 异常都退回进程内计数，绝不阻断实时推送；
- `ModelRunner._push_tool_event`：工具事件先 `_record_event` 再发送 → 工具轨迹持久化；
- `GET /stream/sessions/{id}/events?after_seq&limit`（`chat_gateway` 挂载的 `/stream` 路由，agent/chatonly 共用）；
- WS `sync` 改为**事件表为主、环形缓冲补瞬态**，按 seq 合并去重后补发，并回 `sync_complete.last_seq`；
- 会话删除 / 批量删除 / 清空消息时同步 `clear()` 事件，避免旧轨迹被回放；
- 前端 `endpoints.sessionEvents` + `chat.js::replayToolEvents`：切换会话时按 seq 回放 `tool_call`/`tool_result`，复用与实时相同的 `addToolCall/addToolResult`；
  `addToolCall` 对同 `call_id` 去重（重连补发与回放重叠只建一行）。

**有意留到后续**（不阻塞当前收益）：

- 未引入 `session_event` 包装帧：现有 envelope 本身即事件（`_seq` 已带上），包装只会让前端多一层；
- 未新增 `frontend/src/stores/trace.js` 节点 reducer，正文/过程流仍由 `chat_messages` 重建（本轮只回放 tool 事件，避免与 `role=process` 聚合重复）；
- `assistant/chunk` 增量流式、`delegation/*` 事件、projection（todos/tokenUsage/contextPressure）仍属 Phase 3–4；
- `thought/process/mental` 行仍会写入 `chat_messages`（作为过程流的现有真源），旧会话因此无需 `legacy` 合成事件。

验收：跑一轮含工具调用的对话 → 刷新页面 / 重新打开该会话 → 轨迹面板仍显示工具名、参数、状态、耗时、结果预览（数据来自事件表，且与正则无关）。

### Phase 2 — 工具卡片（已实施）

已实施：**工具自述展示 + 前端卡片分派**，轨迹行从“名称+预览”升级为可读卡片。

- `modules/thinking/tool_view.py`：三个纯函数（无 IO）
  - `present_call(name, args)`：调用瞬间的卡片骨架（命令/路径/查询），前端立刻画出“运行中”的卡片；
  - `build_meta(name, args, result, status)`：把“卡片需要、但结果预览装不下”的数据压成有界 `meta`（终端输出、带行号的文件片段、来源列表、diff 行），随 `tool/result` 落库；
  - `present_result(name, args, ..., meta)`：实时与回放**同一个函数**，只依赖 `args + meta`，因此刷新后重建的卡片与当时一致。
- 覆盖的卡片：`terminal`（exec_command / run_command / run_script）、`read`（read_text_file / read_file）、`diff`（write_text_file / write_file / edit_file）、`search`（knowledge_search / grep / search_files）、`web`（web_search / web_fetch）；其余工具落 `generic`。
- 结果解析兼容三层：`dict` → `json.loads` → `ast.literal_eval`（本项目工具结果经 `str(dict)` 传给事件层，单引号不是合法 JSON），都失败时按纯文本处理。
- `tool_events`：`build_call_payload` 带 `view`；`build_result_payload` 新增 `args` 参数，输出 `meta` + `view`（未知工具两者都不写，避免冗余）。
- `dev_tools.write_text_file`：写入前读取旧内容，返回有界 `diff`（80 行 / 单行 200 字符 / 合计 4000 字符），既是 `DiffCard` 的数据源，也让模型自己确认改了什么；同时修正 `action` 语义（真的新建时报 `created`）。
- 前端：`components/tools/ToolCard.vue` 按 `view.card` 分派到 `TerminalCard / DiffCard / ReadCard / SearchCard / WebCard / GenericCard`，头部（名称/参数/状态/耗时）与错误行统一在 `ToolCard` 渲染；`Chat.vue` 的原内联轨迹模板收敛为 `<ToolCard :trace="tr" />`；`chat.js` 在 `addToolCall/addToolResult` 上携带 `view`（结果无 `view` 时清空骨架 → 回退 generic）。

已知边界：

- MCP 引入的 `write_file` 不返回 diff → 结果卡片回退 `generic`（调用骨架仍是 diff，不丢信息）；
- `read_file` 由 MCP 提供时，行号按返回文本从 1 计（不解析 `offset` 参数）；
- 卡片是**只读展示**，尚无“点击跳转到文件/重跑命令”等动作；
- 终端输出上限 4000 字符、文件片段上限 60 行，超出在卡片内标注截断。

验收：`exec_command` 显示终端卡片（命令、cwd、exit code、输出），`write_text_file` 显示 diff（+N/−M 与着色行），`web_search` 显示来源列表（可点链接）；未知工具（如 `calc`）落 generic 卡片显示结果预览，不报错。刷新后卡片与运行时一致（`meta` 来自事件表）。

### Phase 3 — 增量流式与交错（已实施，缩减版）

已实施：**正文流式（chunk 帧 + 流式尾巴气泡）**，解决“回答憋到最后一次性蹦出来”的观感问题。

- 新增瞬时帧 `{"type":"chunk", "event":"chunk_delta", "content": <增量>, "data":{turn, tier, model_id, identity_name}}`：
  - `ModelRunner._push_stream_chunk`：agent 模式下大模型正文每 40 字符或遇到换行/句末推一帧，只推本会话连接；
  - `chat_gateway._consume_turn`：简单路线不再把 token 全攒到 `done`，满 40 字符或句末即推一帧，结束时补齐尾巴；
  - 主管/专家的正文不进 chunk（他们按轮次整段走 `thinking` 事件），避免同一段话推两遍。
- `chunk` 是**瞬时事件**：不落库、不占 seq（不在 `DURABLE_TYPES` 里），刷新后以最终 `message` 收尾，因此不会撑大事件表。
- 前端：`chat.js` 新增 `streamTail`（`{content, tier, identityName, turn}`）+ `appendStreamChunk/clearStream`；
  - 同一轮累加到同一个尾巴，换轮（工具循环下一轮）重置；
  - `Chat.vue` 在消息流末尾渲染尾巴气泡（`ChatMessage :is-streaming`，带光标、不显示复制/删除按钮）；
  - 最终 `message` 到达 → 尾巴清空、换成落定气泡；`tool_call` 到达 → 尾巴收起（避免把“我先看看文件”留着冒充最终回答）；
  - `stop` / `done` / `error` / 切换会话 / 清空都会丢弃未落定的尾巴；
  - `classifyThinking` 跳过 `entry_type=streaming_delta` 的**大模型**增量（已由 chunk 帧渲染），主管/专家的流式增量仍进过程流。

已实施：**实时轨迹入流**（工具卡片插进对话流，不再只在侧边面板）

- `chat.js::addToolCall` 给每条轨迹记 `afterKey` = 调用发生时“最后一条消息”的 key（服务端 id 优先，回退本地 `_id`）；
- `Chat.vue` 在每条消息之后渲染该消息的轨迹，形成“用户消息 → 工具卡片 → 回复气泡”的真实顺序；
  - 无锚点的轨迹（历史回放、尚无消息时的调用）仍进折叠面板 `运行轨迹（N）`，避免旧工具被错插到新位置；
  - 渲染时同时比对 `_id` 与 `id`，所以 ack 回填服务端 id 后锚点依旧命中。

刷新后回到原位：**用户消息入事件表**

- 新增 durable 事件 `user_message`（只存 `message_id`，不存正文），在 `api_stream` 的 ack 之后、正文之前记录；
- `replayToolEvents` 按 seq 回放时用它推进锚点（`user_message` → 后续工具挂在它下面），并把锚点写给 `addToolCall`；
- **助手终稿也推进锚点**（`assistant/message.source_seqs` 的轻量替代）：`message` 事件的 `data.message_id` 命中本次重建的消息时，把它设为新锚点，于是同一轮内“说一句 → 调工具 → 接着说”能按 seq 交错回放，而不是全部堆在用户消息下；
- 但只在**新格式会话**（出现过 `user_message` 事件）里这么做：老会话没有用户消息事件，一推进就会把工具错挂到上一轮回复下面。终稿未落库/被截断（没有 `message_id` 或 id 对不上任何消息）时保留上一个锚点——宁可不定位（落折叠面板）也不错位。
- `chat_gateway` 两条简单路线（WS / SSE）也在计时基线之后、本轮任何事件之前记录 `user_message`（直写仓储，不造 envelope、不取 `time()`，不影响心跳/超时判定）；

老会话（本次改动之前）没有 `user_message` 事件 → **按时间回退**：

- `switchToSession` 构建消息时收集用户消息锚点 `[{key, ts}]`（`chat_messages.created_at`），传给 `replayToolEvents`；
- 回放遇到没有事件锚点的工具时，取“时间上不晚于该事件”的最后一条用户消息作为 `afterKey`（容忍 1s 同秒抖动）；都早于它则留空 → 折叠面板；
- 两侧时间同为 naive UTC（`chat_messages` / `session_events` 同源），因此可直接比较；`_tsSec` 统一解析数字/ISO（截断导秒之后的小数、无时区按 UTC），解析失败返回 0 = 不定位；
- `user_message` 事件存在时始终优先（比消息时间更精确，也不受时钟回拨影响）。

已知边界（Phase 3 收尾工作）：

- 没有 §4.5 的 `trace.js` 节点 reducer：交错靠“消息锚点”而非 seq 排序，粒度是**消息**——同一轮里工具挂在“最后一条用户消息或助手终稿”下，不会细分到主管/专家气泡之间（他们的气泡是 `chat_messages.role=thought` 聚合出来的，没有对应的 durable `message` 事件可锚定）；
- 时间回退只解决老会话“挂在哪个用户消息下”，不解决同轮内工具与主管/专家气泡的相对顺序；消息时间与事件时间若跨时区/时钟漂移（非同一进程写入）可能偏差；
- 未引入 `assistant/message.source_seqs`（chunk 不落库，没有需要回指的 seq）；
- SSE 路线：简单路线（`_chatonly_sse`）与 WS 同一节流推 chunk；agent 模式 SSE（`_stream_sse`）经 `connection_manager.subscribe_stream` 接入同一套帧（工具/流式/投影/待办），连上时同样补发投影与待办快照；
- Think 行“跟随最新一行”沿用现有过程流行为，未改为节点模型。

验收：跑一个稍长的回答 → 正文边生成边渲染（尾泡 + 光标），结束后替换为正式气泡；中途刷新页面不会出现半截气泡（以 `message` 事件为准）。
跑一个含工具的任务 → 工具卡片出现在用户消息下方、回复气泡上方，与“说一句 → 调工具 → 接着说”一致；刷新后卡片回到同一条用户消息下方（锚点来自 `user_message` 事件）。
老会话（无 `user_message` 事件）刷新后仍能回到原用户消息下方（锚点来自消息时间回退）；早于首条用户消息的工具继续留在折叠面板。

### Phase 4 — 投影与可应答帧

#### 4.a 投影（已实施）

`todos` / `tokenUsage` / `contextPressure` / `runners` 收敛为 `session_projection` 整值快照。

- 存储：`session_projections`（主键 `(session_id, key)`）**覆盖写**，每个 key 只留最新一版；
  投影**不进** `session_events`，避免每次状态变化追加一行把表撑大。
- seq：与事件日志**共用同一个每会话计数器**（`SessionEventRepository.next_seq`），所以客户端能把两类帧
  放在一个序号空间比较；仓储在重启用事件表 max(seq) 重建计数器，可能与投影水位撞号，
  `ProjectionRepository._allocate_seq` 取“事件计数器 vs 投影水位 + 1”的较大值（中间空号无害）。
- 帧：`{"type": "session_projection", "event": "projection", "data": {key, value, seq}}`，
  `_seq` 同步写在 envelope 上（与事件一致）。
- 发射点：
  - `runners` + `contextPressure`：每回合结束（`api_stream` 的 `_persist_process` 之后）`emit_runner_state`；
    快照构建复用 `projections.build_runners`（与 `_collect_process_snapshot` 同一份逻辑）；
  - `todos`：`ModelRunner._push_todo_update`（todo 工具执行后）；旧 `type='todo'` 事件保留兼容；
  - `tokenUsage`：`ModelRunner._record_usage` 在每次模型响应后累加（读改写投影，跨 runner 销毁仍准确）。
- 恢复：`GET /stream/sessions/{id}/projections` 一次读回全部帧（切会话时调用）；WS 连上时 `push_current` 补发，
  重连（页面未刷新）也能立刻拿到状态。
- 前端：`chat.js` 用 `applyProjection` 按 key 覆盖、`seq <= 已应用值` 直接丢弃（higher-seq-wins），
  并把值映射到 `todos` / `contextTokens` / `contextWindowSize` / `runners`（扁平化） / `tokenUsage`；
  `Chat.vue` 的 todo 面板与上下文占用改为读 store（不再自持 ref），`_onStatus` 与投影共用 `applyRunnerSnapshot`。
- 已知边界：`tokenUsage` 依赖模型响应带 `usage`（部分 OpenAI 兼容端点不返回 → 该轮不计）；
  投影是“最近一次快照”，不保留历史曲线；投影帧不参与 `sync` 的 `last_seq` 补发（靠连上即补发解决）。

#### 4.b 可应答帧（已实施）

审批/提问从“一次性的 security 思考行”改为**服务端 pending 表 + 整快照帧**。

- 待办表：`modules/thinking/pending_requests.py`，会话级 `request_id → {kind: approval|question, target/detail 或 question/options/context, created_at}`，
  线程安全（思考循环登记、WS 线程 resolve）；同 `request_id` 幂等（重复事件不重复建条目）。
- 帧：`{"type": "session_request", "event": "requests_snapshot", "data": {"requests": [...]}}`——
  **整快照、无 seq**（幂等覆盖），与投影区分：待办是内存态，不落库。
- 登记：`api_stream` 的调度事件循环里，`_format_scheduler_event` 产出 `event_type=security` + `request_id` 后，
  按动作识别（`等待用户审批` → approval，`user_intent_request` → question）并登记 + 推快照。
- 结束：WS 的 `security_response` / `interactive_response` 处理完原有 Future 后 `resolve_and_broadcast`；
  断线（`finally`）、停止、删除/清空会话都会 `clear`。
- 恢复：WS 连上即 `broadcast`，刷新/重连后横幅按快照重建。
  **这修掉一个真实挂死**：以前横幅只在 `role='security'` 的思考行里出现一次，而前端恢复时跳过 security 行 →
  刷新后按钮消失、回合永久等待。
- 前端：`chat.js::applyPendingRequests` 按快照建/复用横幅（`request_id` 去重），
  快照里没有的横幅置 `resolved+stale`（保留历史，但按钮不再可点、显示“该请求已失效”）；
  `hasPendingRequest` 为真时 `ChatInput` 进入 `blocked`（占位符提示 + 发送按钮禁用 + `handleSend` 直接返回），
  即“输入区接管”。
- 已知边界：待办不落库，进程重启后历史里只剩已失效横幅（请求本身也无法响应）；
  一次只在前端展示快照内请求，不排队；“输入区接管”只是禁用发送，不隐藏输入框；
  多客户端同时连同一会话时，任一点击即结束（与原有 Future 语义一致）。

---

## 6. 测试计划

### 6.1 服务端

- `tests/unit/test_session_events.py`：seq 单调、并发 append 不重号、trim 不影响 message/tool 事件、分页边界；
- `tests/unit/test_pending_requests.py`：登记幂等/会话隔离/快照副本、从调度事件识别审批与提问、
  非交互事件不登记、结束与清理、推送失败不影响等待中的请求；
- `tests/unit/test_session_projections.py`：覆盖写单行、seq 与事件同空间且重启后不撞号、帧形状、
  四个 key 的构建（contextPressure 比值/clamp、todos 计数、token 累加跨 tier）、`/projections` 路由；
- `tool_view` 每个 card 的纯函数用例（正常/空/超长/错误结果）；
- WS 集成：订阅 → 收到 `session_subscribed` → 产生事件 → 帧顺序与 seq 一致；断线 → 带 `last_seq` 重连 → 只补缺失。

### 6.2 前端

- reducer golden test：固定事件数组 → 期望节点数组（含 chunk 合并、call/result 原地替换、delegation 树）；
- 回放等价 test：live 事件序列 与 历史读取序列 经同一 reducer 输出深相等；
- 组件 test：每个 card 的 props → DOM（沿用现有 `*.spec.js` 约定）；
- 投影 test：`chat.spec.js` 的 `applyProjection`（higher-seq-wins / 按 key 独立 / 缺 seq 照常应用 / `resetProjections`）
  与 `Chat.spec.js` 的 `session_projection` 帧 → todo 面板与上下文占用（不额外拉取）；
- 可应答 test：`chat.spec.js` 的 `applyPendingRequests`（建横幅/去重/失效标记/重置）与
  `Chat.spec.js` 的 `session_request` 帧 → 横幅重建 + 输入区接管（占位符/禁用发送）与失效恢复。

### 6.3 回归

- `tests/unit/test_api_stream*.py`、`test_chat_gateway*.py` 中依赖旧 `thinking` 文案的断言需同步调整；
- `cli_tui/services/ws_client.py` 的 `tool_call` / `thinking` 分支保留（旧帧兼容），Phase 3 后再统一。

---

## 7. 兼容与风险

### 7.1 旧会话

事件表为空 → `history` 接口回退读 `chat_messages`，一次性合成 `legacy` 事件（`thought` → `think`、`process` → `process` 节点、`assistant` → `assistant`），前端只读不可续。新会话从第一条事件开始即为新格式。

### 7.2 CLI TUI

`cli_tui` 消费旧 envelope（`thinking` / `stage_event` 富事件），但**已接新帧**，不再依赖旧的工具摘要文本：

- `type=tool`（`tool_call` / `tool_result`）→ 工具面板记录，按 `call_id` 合并成一行（`cli_tui.state.merge_tool_record`），一行摘要取服务端卡片的 `view.title`，回退参数里的命令/路径/查询词；
- `type=chunk` → 只累计本轮流式字数并更新 `thinking_hint`（`生成中… N 字`），**不逐帧写进对话框**——终稿的 `assistant_message` 会整段渲染，逐帧写入会让正文在终端里出现两遍；
- `type=session_projection` → `contextPressure`（上下文条）与 `runners`（顶栏模型身份）。`todos` / `tokenUsage` 在 TUI 里没有面板，不凭空造。

`session_request`（可应答帧，Phase 4.b）也做了映射：整快照里的待处理请求转成既有的 `security_review` / `user_intent_request` 条目，直接复用已有审批组件与 `security_response` / `interactive_response` 上报；快照变空 → `pending_clear`，面板收起（避免应答后留下点不动的僵尸按钮）。一次快照多个请求时取最早登记的那个，应答后后端推新快照，下一个自然接上。

### 7.3 敏感信息

`tool/call.args` 与 `tool/result.content` 可能含密钥（config、env）。事件落库前过一遍脱敏：键名匹配 `api_key|token|secret|password` 的值替换为 `***`，值长度截断（预览 500 字符，完整内容只进 `tool/result.content` 但同样脱敏）。现有 `modules/security_system/tool_permission_controller.py` 的规则可复用。

### 7.4 存储增长

按 4.1 的保留策略；`session_events` 单独库文件（`data/session_events.db`）避免与 `chat_messages` 争锁。写入走单写队列（类似 `utils/faiss_lock.py` 的跨进程互斥）。

### 7.5 多会话并行

seq 按 `session_id` 隔离；前端每会话一个 reducer 实例（现有 `_processingSids` 模型不变）；`session_queue` 整快照避免并行下的增量错乱。

---

## 8. 不做什么

| 不做 | 原因 |
|------|------|
| cordis 插件系统 / slot 注册表 / web 双阶段启动 | Vue + FastAPI 无此需要，借的是数据模型不是框架 |
| Code Mode（`run_code` 单一入口 + SDK） | 我们的工具集与委托模型不同，收益低 |
| 每个工具一个独立 npm 包 | 单体前端，卡片组件放 `components/tools/` 即可 |
| 事件级权限/审计重建 | 现有 `trace_id` 审计链路保留，不并入事件表 |

---

## 9. 工作量

| 阶段 | 后端 | 前端 | 合计 |
|------|------|------|------|
| P0 | 0.5d | 0.5d | 1d |
| P1 | 2d | 2d | 4d |
| P2 | 1.5d | 2d | 3.5d |
| P3 | 1d | 1d | 2d |
| P4 | 1d | 1d | 2d |

建议顺序：P0 先合入（低风险、立刻可感知）→ P1（骨架，收益最大）→ P2 → P3。P4 视需求。

---

## 10. 与 dsh 的差异（保留我们的优势）

- **委托树**：dsh 是单 agent + 工具树；我们 `large → supervisor → expert` 的委托链在事件模型下天然是 `subCalls` 树，信息量更大，Phase 1 一并落地；
- **过程流**：现有 `processFlow` 的“连续思考 + 调度语言”在节点模型里对应 `think` 与 `delegation` 节点，不再是一坨文本；
- **模型状态快照**：`runners`（状态、轮次、上下文占用）已经是结构化的，Phase 4 直接升级为 projection，不需要重做。
