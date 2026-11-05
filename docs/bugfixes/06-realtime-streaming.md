# 错误原因与修复记录 · 实时链路 · WS / SSE / 推送 / 代理 / TUI

[← 返回总索引](./README.md) | [English](./06-realtime-streaming.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 8 节：§12、§24、§68、§82、§98、§99、§100、§104。

---
## 12. 前端裸 fetch 路径在 8765 静态代理下全部 404（前端）

**现象：** 编排图页显示"暂无该层 Agent"、编排页/技能页/会话设置数据不加载；浏览器 Network 面板对应请求 404。

**报错（浏览器 Console）：**
```
TypeError: Failed to fetch dynamically imported module: http://localhost:8765/assets/xxx.js
```
（此为另一类：构建后旧 chunk 失效，见 12.2）

**根因：** 前端 SPA 由 `frontend/server.py`（端口 8765）静态服务，它**只代理 `/api/*` 到后端 8080 并去掉 `/api` 前缀**。新增页面代码直接写裸路径：
```js
fetch('/management/orchestration')   // ❌ 8765 不代理 /management → 404
fetch('/config/persona/...')          // ❌ 同样 404
fetch('/stream/session/...')          // ❌ 同样 404
```
而走 `endpoints.*` 封装（api.js `BASE='/api'`）或 `fetch('/api/...')` 才经代理正常。

实测：
- `8765/management/orchestration` → **404**
- `8765/api/management/orchestration` → **200**（代理去前缀到 8080）
- `8080/api/management/orchestration` → 401（8080 无 `/api` 路由，**不能直接用 8080 的 /api**）

**修复：** 前端所有裸 `fetch('/management|/config|/stream|/tools/...')` 统一加 `/api/` 前缀（8765 代理去前缀到 8080）。涉及 8 个文件：Graph.vue、Orchestration.vue、Skills.vue、SessionSettings.vue、ScheduledTasks.vue、Dashboard.vue、Settings.vue、ChatMessage.vue。

**关键约定（写前端代码务必遵守）：**
1. **所有后端 API 请求必须用 `/api/` 前缀**（或走 `endpoints.*` 封装）——8765/vite 代理都会去掉 `/api` 转到 8080；裸路径在 8765 直接 404
2. **WebSocket 例外**：`ws/client.js` 直连 `:8080/stream/ws/{sid}`（8765 无 WS 转发），不要改成 8765
3. **`/audio`、`/pet` 资源保留裸路径**（分别由后端静态/桌宠使用，不受代理影响）
4. **不可用 8080 直接拼 `/api/`**——后端路由无 `/api` 前缀，会 401

**验证：** 修复后各页面数据加载正常；`grep -rnE "fetch\\('/(?!api/)"` 无残留裸 fetch。

### 12.1 同类：懒加载 chunk 404（构建更新后旧页面白屏）

**现象：** `npm run build` 后，已打开的旧页面导航到懒加载路由报：
```
Error: Unable to preload CSS for /assets/Graph-xxx.css
TypeError: Failed to fetch dynamically imported module: /assets/Graph-xxx.js
```

**根因：** Vite 构建产物带内容 hash，重建后旧 hash 的 chunk 被删除；浏览器旧页面内存中的模块图仍引用旧 hash，导航触发动态 import 即 404。

**修复：**
- `frontend/src/router.js` 加 `router.onError`：识别 `Failed to fetch dynamically imported module` / `Unable to preload CSS` 时自动 `location.reload()` 拉最新 index.html + chunk
- `frontend/server.py` 对 `/assets/*` 设 `Cache-Control: public, max-age=31536000, immutable`（hash 文件不变内容不变）；`index.html` 保持 `no-cache`（每次拿最新，引用最新 chunk 列表）

**经验：** 改完后端/前端记得重新 `npm run build`；已打开旧页面的用户需刷新一次；构建更新后懒加载失败会自动刷新恢复。

---

## 24. send_json_from_thread 事件循环线程内自锁 → 对话报错（后端）

**报错（ERROR，错误消息为空）：**
```
2026-08-10 17:34:32 - stream_api - ERROR - [ConnectionManager] send_json_from_thread 失败:
```
冒号后**空消息**是关键特征——这是 `concurrent.futures.TimeoutError`（`future.result(timeout)` 超时抛出，`str()` 为空）。

**现象：** 对话中（尤其 deepseek 推理模型返回 `reasoning_content` 时）后端日志刷该错误，思考事件推送被卡 5 秒。

**根因：** `modules/thinking/api_stream.py` `ConnectionManager.send_json_from_thread()` 用
`asyncio.run_coroutine_threadsafe(_send(), self._loop)` + `future.result(timeout=5.0)` 同步阻塞发送，
设计目标是供**非事件循环线程**（daemon 线程）安全调用。但 `_push_reasoning`
（`modules/thinking/core/model_runner.py:1204`，模型推理时推送 thinking 事件）在**事件循环线程内**调用它：

- `run_coroutine_threadsafe` 把 `_send` 调度到事件循环；
- `future.result(timeout=5)` **阻塞事件循环自身线程** → `_send` 永远没机会执行；
- 5 秒后 `TimeoutError`，空错误消息，每次推送卡 5 秒。

即"自己调度自己 + 自己阻塞自己"的自锁（`run_coroutine_threadsafe` 官方说明只能在非循环线程调用）。

**为什么测试没抓到：**
- `tests/unit/test_api_stream_core.py` 只测了三条路径：无事件循环、无连接、**独立工作线程**调用
  （`test_send_json_from_thread_success` 恰好走的是能正常工作的跨线程路径）；
- **事件循环线程内调用**这条路径没有测试——必须真实跑对话、模型带 `reasoning_content` 才会触发，
  且是运行期时序问题（要真把循环卡 5s 才报错），单测/集成测试都构造不出。

**修复：** `send_json_from_thread` 入口检测是否已运行在事件循环线程上（`asyncio.get_running_loop() is self._loop`）：
- 是 → `asyncio.create_task(_fire_and_forget())` 异步调度发送，**不阻塞**，立即返回 True；
- 否（后台线程）→ 保持原 `run_coroutine_threadsafe + future.result(timeout)` 语义。

**验证：** 新增回归测试 `test_send_json_from_thread_on_loop_thread_not_blocked`
（`tests/unit/test_api_stream_core.py`）——在 async 上下文（事件循环线程）内调用，断言立即返回 True
且消息送达；相关 13 个用例全过，全量 1460 通过。

**经验：**
1. **凡是"跨线程安全"的同步入口，都要同时考虑"在事件循环线程内被调用"的分支**——`run_coroutine_threadsafe`/`loop.call_soon_threadsafe` 从循环线程调用自身就是死锁/超时；
2. **空错误消息往往来自 `TimeoutError`/`CancelledError`**（`str()` 为空）——日志里"失败: "后面什么都没有时，优先怀疑同步等待超时；
3. **测试要覆盖"生产里真实调用上下文"**——只测另一条线程的成功路径，会漏掉事件循环线程内的自锁场景。

---

## 104. 前端连不上后端：IPv6 localhost 陷阱 + 前端代理层测试盲区（前端/后端）

> **编号修订：** 原文此处误用 §40（与前面的 §40 重复），现编为 §104。

**现象：** 前端页面点击功能显示"正在等待后端启动"；桌宠/前端进程随后退出。后端 `curl :8080/health` 正常，但前端轮询 `/api/health` 一直失败。

**根因（两个）：**
1. **IPv6 `localhost` 陷阱**：`frontend/server.py` 代理用 `http://localhost:{port}`——macOS 上 `localhost` 解析为 `::1`（IPv6），而后端只绑 `127.0.0.1`（IPv4，`--host 127.0.0.1`）→ urllib 走 IPv6 端口 → **502**。`curl` 优先 IPv4 所以直接测 8080 正常，误导排查。
2. **前端端口固定不同步**：server.py 启动时固定 `BACKEND_URL`，后端端口回退/重启后脱节；cortex 启动后端也不写端口发现文件。

**修复：**
1. 代理改用显式 `127.0.0.1`（IPv4），`pet_widget.py` 同步
2. 代理每次请求**动态读** `read_backend_port()`（端口变化后前端跟随）
3. cortex 启动后端时 `save_backend_port` 同步端口发现文件

**验证：** `8765/api/health` 从 502 → 200。

**测试盲区（为什么没测出来）：** `frontend/server.py`（Python 代理层）此前**零测试**，且：
- pytest 只测 `tests/`（后端），vitest 只测 JS——Python 代理层"无人认领"
- 模块覆盖清单 `_PRODUCTION_DIRS` 不含 `frontend` → "全覆盖"证明只覆盖后端
- IPv6 localhost 是运行时解析行为，单测用 127.0.0.1 直接连不会触发

**修复盲区：** 新增 `tests/unit/test_frontend_server.py`（10 用例：IPv4 回归、动态端口、/api 剥离、错误透传）；覆盖清单纳入 `frontend/`（server.py/pet_widget.py，Qt GUI 启动器豁免）。

**教训：** 任何 Python 代码都须有**明确测试归属**并纳入覆盖清单，不能因"目录属于前端"而脱离测试体系。环境类 bug（DNS/IPv6 解析）需在测试中显式 mock 固定行为。

---

## 68. todo 面板 3 秒轮询 —— 改 WS 事件推送 + 按需拉取（前端/后端）

**现象：** 前端 todo 面板用 `setInterval(loadTodos, 3000)` 轮询拉取，模型更新 todo 后最多延迟 3 秒才显示；且轮询每 3 秒发一次请求，空闲会话也持续消耗。

**根因：** todo 工具（模型调用）在 `model_runner._generate_with_tools` 通过 MCP 执行后，前端无感知——只能靠轮询主动拉取，架构是"拉"而非"推"。

**修复（架构调整：轮询 → 推送 + 按需调用）：**
- 后端：`model_runner` 新增 `_push_todo_update()`，todo 工具执行成功后通过 `connection_manager.send_json_from_thread` 推送 `type='todo', event='todo_changed'` WS 事件（含 session_id）
- 前端：`Chat.vue` 注册 `wsClient.on('todo', _onTodo)`（onMounted）→ 收到事件**按需**调用 `loadTodos()`；移除 3 秒轮询 `setInterval`；`_onTodo` 按 session_id 过滤（其他会话的推送不刷新当前）

**持久化（未改，已合理）：** todo 工具调用即写 `~/.cortex/todos/{session_id}.json`（`_save_todos`），按会话隔离，切换会话不丢。

**验证：** 后端 `test_push_todo_update` + `test_todo_tool_execution_triggers_push`；前端 `_onTodo` 两个用例（触发刷新/其他会话忽略）；前端 497 + 后端相关全过。

**教训：** ① 前端"实时状态"优先用**事件推送**而非定时轮询——有 WS 通道就该推送 + 按需拉取，避免无效轮询；② 推送事件要带 `session_id` 并在前端按会话过滤，否则多会话串扰；③ todo 这类持久化状态，模型工具调用即写盘是正确范式，无需额外同步。

---

## 82. 多会话并行处理：WS 单连接 + 全局处理状态导致会话互相干扰（前端 ws client / chat store / Chat.vue）

**现象：** 用户在 A 会话提问后切到 B 会话，出现「会话「b1da48c1…」正在思考中，切回该会话可查看进度」横幅；A 的回复丢失（切回 A 看不到）；B 的处理状态被 A 的 done/error 事件意外清除。用户要求：各会话独立且可并行，不出现跨会话横幅。

**根因：** 前端 WS 客户端 `frontend/src/ws/client.js` 是**单连接**（`this._conn` 单一 WebSocket，`connect()` 会关闭旧连接）；chat store 的 `processing`/`_processingSid` 是**全局单态**。当 A 处理中切到 B 并发消息：`_ensureConnected` 强制关闭 A 连接（`_doConnect` 里 `this._conn.close()`），A 的后端任务继续运行但结果无法到达；`_onDone`/`_onError` 对非当前会话事件直接调 `finalizeStream('')`，无条件清全局 `processing`，误清除 B 的处理状态；`_onMessage`/`_onThinking` 虽按 `session_id` 拦截（不混入 B 消息流），但状态清除与连接切换未按会话隔离。

**修复：** 会话真正并行需要每个处理中的会话保有独立连接 + 独立状态：
- `frontend/src/ws/client.js` 改造为多连接：`_conns = { sid: {conn, ...} }`，新增 `isConnected(sid)`、`send(sid, data)`、`disconnect(sid)`、`disconnectAll()`；`connect(sid)` 不再关闭其他会话连接
- `frontend/src/stores/chat.js`：`_processingSid` 改 `_processingSids`（`Set` 支持多会话并行）；`processing` 改为 computed（当前会话是否处理中）；新增 `finalizeSession(sid)` 按会话精确清理、`markProcessing(sid, on)` 辅助；`switchToSession` 始终连接目标会话、保留其他会话处理状态；`sendMessage`/`stop`/`clearMessages` 按当前会话操作
- `frontend/src/pages/Chat.vue`：移除跨会话横幅「会话xxx正在思考中」；`_onDone`/`_onError` 对非当前会话事件只调 `finalizeSession(sid)` 不影响当前会话；watchdog 检查当前会话连接（`isConnected(session.sessionId)`）

**验证：** ws client 新增多会话独立连接测试；chat store 新增并行会话场景测试（A 处理中切 B、B 也处理时 A 的 done 不清除 B 状态）；Chat.spec 新增真实 `switchToSession` 流程验证 A 的回复/思考/done 不混入 B；全部前端 505 测试通过，`npm run build` 通过。

**教训：** "会话并行"要求连接的建立/关闭与状态的生命周期都按会话维度隔离。单例连接 + 全局布尔状态只能支撑单会话串行；多会话并发必须把"连接"和"处理状态"都建模成集合/映射，事件处理按 `session_id` 精确路由。

---

## 98. 新事件只在浏览器里生效 —— TUI 看不到工具、SSE 整段憋到结束、回放同轮错位（跨端）

可视化事件（Phase 0–4）落地时，消费端只有 Web 前端接全了。三处缺口共用一个后果：**同一场对话在不同入口看到的东西不一样**。

### 98.1 `cli_tui` 完全不认新事件

**现象：** 终端里跑一个用工具的任务，工具面板一直空着；回答要等整段生成完才出现；上下文占用条不更新。而 Web 端同一会话正常。

**根因：** `cli_tui/services/ws_client.py::_parse_event_inner` 只认旧 envelope（`stage_event` 富事件 + `thinking`/`status` 简单事件）。后端已改成推 `type=tool` / `type=chunk` / `type=session_projection`，TUI 对这三种帧一律返回 `None`，直接丢弃。`cli_tui/screens/repl.py` 里的 `entry_type=streaming_delta` 分支因此成了死代码（从没有人产出这种条目）。

**修复：**
1. `parse_event` 新增三处解析：`type=tool` → `_parse_tool_frame`（`call_id` / `name` / 一行动作摘要 / 状态 / 耗时 / 结果预览），`type=chunk` → `entry_type=streaming_delta` 的 dialog 条目，`type=session_projection` 由 `repl` 直接处理。
2. 一行动作摘要取服务端算好的卡片字段（`view.title/command/path/query`），回退参数里的命令/路径/查询词——不在 TUI 里重新猜工具语义。
3. 结构化事件一个调用发两帧（`tool_call` 建行、`tool_result` 补齐），新增 `cli_tui.state.merge_tool_record` 按 `call_id` 合并：不新建行、不重复计数，且结果帧不覆盖掉调用帧的参数。`AppState.add_tool_call` 与 `WSClient._process_input_impl` 共用它。
4. `chunk` 帧只累计本轮流式字数并更新 `thinking_hint`（`生成中… N 字`），**不逐帧写进对话框**：终稿的 `assistant_message` 会整段渲染，逐帧写入会让正文在终端里出现两遍。
5. `session_projection` 只处理 TUI 真会展示的键：`contextPressure`（上下文条）、`runners`（顶栏模型身份）。`todos`/`tokenUsage` 没有面板，不凭空造。
6. 顺带把 `thinking_progress` 里的模型状态解析抽成 `_apply_runner_snapshot`，与 `runners` 投影复用同一段层级映射。

**测试：** `tests/unit/test_ws_client.py` 新增 `TestParseEventToolFrame`（5 例：调用帧、结果帧、失败状态、无 `view` 时回退参数、`data=None` 不抛）与 `TestParseEventChunkFrame`（2 例）；`tests/unit/test_tui_state.py` 新增 `TestToolRecordMerge`（3 例：合并成一行且保留参数、失败结果覆盖成功、无 `call_id` 不误合并）。

**后续：** `session_request`（可应答帧）当时未接，TUI 审批仍走旧的 `security` 事件；已在 §99.2 接上（`_parse_session_request` 复用现有审批组件，旧 `security` 帧保留双发）。

### 98.2 SSE 路线没有流式

**现象：** 用 SSE（`GET /stream/sse/{sid}`）而非 WebSocket 时，回答是一次性蹦出来的；WS 端同一问题边生成边显示。

**根因：** `chat_gateway._chatonly_sse` 把每个 `message` token 只 append 进 `full_text`，循环里不 yield 任何正文帧，最后一次性发 `assistant_message`。WS 分支（`_consume_turn._flush_reply_chunk`）早就按 40 字符/句末节流推 `chunk` 了，两条路线行为分叉。

**修复：** `_chatonly_sse` 引入同样的 `flush_buf` + `_take_chunk()`：满 40 字或遇到 `\n。！？.!?` 推一帧，`done` 与 `error` 前都把尾巴补出去。`chunk` 仍是瞬时帧（不落库、不占 seq），终稿以 `assistant_message` 为准。

**测试：** `tests/unit/test_chat_gateway_ext2.py` 新增 3 例：句末即推（两帧内容精确比对 + 终稿仍整段）、不到 40 字先攒着（`done` 补一帧 `abcdef`）、出错前先 flush（`chunk` 在 `error` 之前且内容为已生成部分）。

### 98.3 回放时同轮工具全挤在用户消息下

**现象：** 刷新页面后，一轮里“说一句 → 调工具 → 接着说”的工具卡片全部堆在用户消息下面，看着像先调了一堆工具再说话；实时看着的顺序是对的。

**根因：** `chat.js::replayToolEvents` 只用 `user_message` 事件推进锚点，且明确跳过助手 `message` 事件（当初的顾虑是老会话没有用户消息事件时会把工具错挂到上一轮回复下）。结果是**所有**工具只能挂到用户消息，同轮内的相对顺序丢失。

**修复：** 助手终稿也推进锚点，但加两道闸：
1. 只在**新格式会话**里生效（本会话事件流出现过 `user_message`）——老会话仍走“只认用户消息 + 时间回退”，行为不变；
2. 只认**确定落库的那条**（`data.message_id` 必须命中本次重建出的消息 id 集合）——中断残稿没有 `message_id`、终稿未落库时 id 对不上，此时保留上一锚点，宁可不定位（落折叠面板）也不错位。

**测试：** `frontend/src/stores/chat.spec.js`：原「同一轮都挂在用户消息下」一例改为断言 `['u1','a1']`（`说一句 → 调工具 → 接着说` 的交错），新增「终稿没落库时保留上一锚点」一例；「老会话无 `user_message` 事件时保持无锚点」一例继续通过。

**回归：** `tests/unit` 6417 例（1 skipped，含本次新增 13 例）、`tests/integration` 432 例（1 skipped）全部通过；`tests/unit/test_ws_client.py` + `test_tui_state.py` 64 例、`test_chat_gateway*.py` 83 例；前端 vitest 51 文件 582 例全通。

---

## 99. SSE 出口看不到工具/流式帧；TUI 不认可应答帧（后端 + TUI）

### 99.1 SSE 只收到调度事件

**现象：** 用 agent 模式的 SSE（`GET /stream/sse/{sid}?question=`）接会话，正文整段蹦出、工具轨迹完全没有、todo 与上下文占用也不更新；用 WebSocket 接同一会话则一切正常。

**根因：** 帧有两个来源：
1. `_emit(...)` → `callback`（SSE 的 queue）+ `connection_manager.send_json`；
2. `ModelRunner._push_tool_event` / `_push_stream_chunk`、`projections._push_frame`、
   `pending_requests.broadcast` → **只调 `connection_manager`**（工具/流式/投影/待办）。

`ConnectionManager` 只维护 `active_connections`（WebSocket），`send_json_from_thread` 在
“无 WS 连接”时直接 `return False`；SSE 没有任何订阅入口。于是第 2 类帧对 SSE 客户端
完全不存在——不是推送失败，而是根本没有收件人。

**修复：** 给 `ConnectionManager` 加一条与 WebSocket 并列的出口：
1. `subscribe_stream(session_id) / unsubscribe_stream(...) / has_stream(...)`：每会话一组
   `asyncio.Queue`（多订阅者各自一份），订阅时顺手记下 `_loop`——纯 SSE 会话从未
   `connect` 过 WS，`_loop` 为空会让 `send_json_from_thread` 提前返回；
2. `send_json` 先 `_fanout_streams` 再发 WS；`send_json_from_thread` 把“有流订阅者”也算
   一个出口，并在调度到事件循环的协程里扇出（`Queue.put_nowait` 不能跨线程调）；
3. `_stream_sse` 订阅自己的队列，循环里两个队列都抽：调度事件优先（保序），工具/投影
   走流队列；同一个 envelope 对象会同时经 `callback` 与扇出到达，因此按对象 `id` 做一个
   有界 `deque(maxlen=256)` 去重；`finally` 注销订阅（客户端断开也必须清）；
4. 与 `websocket_chat` 对齐：连上即 `projections.push_current` + `pending_requests.broadcast`，
   不用等下一次状态变化才看到 todos/上下文占用/审批项。

**测试：** 新增 `tests/unit/test_api_stream_sse_fanout.py`（5 例）：只有 SSE 订阅时
`send_json` 能送达；`send_json_from_thread` 在无 WS、只有流订阅时返回 True 且帧到达；
无任何出口时返回 False；多订阅者各得一份；`_stream_sse` 能收到只经扇出的 `tool_call`
帧，且同一 envelope 不会重复下发，生成器结束后订阅已注销。

### 99.2 TUI 不认 `session_request`

**现象：** 网页里刷新后仍能看到“待审批”横幅并点确定；终端里刷新/重连后什么也没有，
审批 Future 只能等超时。

**根因：** Phase 4.b 把待办做成了 `session_request` 整快照帧（唯一能在刷新/重连后重建
横幅的东西），Web 前端接上了，TUI 的 `parse_event` 对这四个字毫不认识。终端侧只能靠旧的
`security` 一次性事件，而那条路径在刷新后就没有了。

**修复：** `WSClient._parse_session_request` 把快照映射回 TUI 已有的两种条目：
`approval` → `security_review`（`request_id`/`tool`/`detail`），`question` →
`user_intent_request`（`question`/`options`/`context`），于是审批组件、`security_response` /
`interactive_response` 上报全部复用，不用重写一套；快照为空 → `pending_clear`，`repl`
收起审批面板并清 `pending_security_review`，避免应答后留下点不动的僵尸按钮。一帧多个
待办时取最早登记的那个，应答后新快照自然接上下一个。

**测试：** `tests/unit/test_ws_client.py::TestParseEventSessionRequest`（5 例）：审批映射、
提问映射（含 options/context）、多请求取首个、空快照→`pending_clear`、缺字段的脏快照忽略。

**回归：** `tests/unit/test_ws_client.py` + `test_tui_state.py` + `test_api_stream_sse_fanout.py`
74 例全通；`test_api_stream_ext.py` + `test_api_stream_think_ext.py` + `test_pending_requests.py`
71 例全通。

---

## 100. SSE 会话被当成“前端已断开”：握手拦下本轮 LLM、推送只认 WS、断开不清理（后端）

§99 打通了 SSE 的帧出口之后，SSE 路线还剩三处把“有没有 WebSocket 连接”当成
“前端在不在线”的判断，任何一处都足以让 SSE 端看起来“接不上”或“卡死”。

### 100.1 握手失败 → 本轮 LLM 直接被跳过

**现象：** `GET /stream/sse/{sid}?question=` 连上去，只收到一条
`connection_lost`（“前端连接已断开，本轮请求已跳过，可在重连后重试”），没有任何回答；
同一会话换 WebSocket 一切正常。

**根因：** `think()` 在调模型前会握手
`frontend_channel.confirm_frontend_connection(session_id)`，而它第一行就是
`if not cm.active_connections: return False`——`active_connections` 只装 WebSocket。
纯 SSE 客户端从未 `connect` 过 WS，于是握手恒失败，本轮在 LLM 之前就 return。

**修复：** 握手改看“所有可推送出口”：`ConnectionManager.stream_sessions()`（新增）与
`active_connections` 的并集（`frontend_channel._channels`）。
`confirm_frontend_connection` 与 `_confirm_async` 都用它判在线与挑选广播目标；
SSE 会话靠 `send_json_from_thread` 的新语义（有流订阅者即算送达）确认可达。

### 100.2 `push_content` 只遍历 WebSocket 会话

**现象：** 主动搭话 / 定时任务 / 后台补推的消息，SSE 客户端收不到（消息确实落库，
刷新后历史里有，但当时不弹）。

**根因：** `push_content` 的发送循环写死 `for sid in cm.active_connections`。

**修复：** 改为遍历并集；同一会话既有 WS 又有 SSE 时只发一次（`send_json` 内部自己
扇出两份），不会重复。

### 100.3 SSE 断开不清理待审批 / 不取消任务

**现象：** SSE 客户端在工具审批（或 `ask_user_intent` 提问）时关掉页面/断网：会话
一直“卡在处理中”，后续请求全是 busy；日志里审批既没批准也没拒绝；模型还在继续
生成（白烧 token）。

**根因：** `websocket_chat` 的 `finally` 会 `reject_session_reviews` +
`reject_session_user_responses` + `pending_requests.clear`，`_stream_sse` 的 `finally`
只注销了自己的订阅队列。`sse_starlette` 在客户端断开时关闭生成器，于是那一轮
`_safe_think` 任务成了没人管的孤儿：审批 future 永久挂起（Suspension 全局计时随之
冻结），任务的异常也没人取走。

**修复：** 把 WS 的三步清理抽成 `_cleanup_session_on_disconnect(session_id, tag)`，
WS 与 SSE 的 `finally` 共用；SSE 额外 `task.cancel()`（SSE 没有“重连后接着跑”的语义，
客户端走了就别再生成），并挂 `add_done_callback` 取走异常，避免
“Task exception was never retrieved” 噪声。取消后还要把 `sessions[sid]["processing"]`
手动置回 False：`think()` 的 `finally` 里那句 `await _set_processing(False)` 在任务被
取消时未必跑得完，漏了就会留下一个永远是 busy 的会话。

### 100.4 附：去重用的 `deque` 存的是 `id()`（顺手修）

§99 里按对象 `id()` 去重同一 envelope（既经 `callback` 又经扇出）。`deque` 只存 int
时对象早已释放，CPython 会复用 `id`——新帧可能撞上刚释放的 id 被误丢。改为在
`deque` 里存对象本身（`any(e is event for e in seen)`），既保住身份比较，也钉住对象
不让 id 被复用。

**测试：** `tests/unit/test_frontend_channel_ext.py` 新增 6 例：纯 SSE 会话握手成功且
只发给该会话、广播含 SSE 会话、无任何出口才返回 False、`_confirm_async` 同理、
`push_content` 能推到纯 SSE 会话、WS+SSE 同时在线时不重复发。
`tests/unit/test_api_stream_sse_fanout.py` 新增 1 例：生成器被 `aclose()`（客户端断开）
后订阅已注销、待审批/待交互/待办三处清理都被调用、思考任务已被取消且会话不再停在
“处理中”。

**回归：** `tests/unit` 全量 6434 例（1 skipped，12 deselected）通过；本次相关子集
`-k "frontend_channel or pending_requests or api_stream or chat_gateway or projection or proactive"`
379 例通过；`tests/integration` 432 例（1 skipped）通过。


### 100.5 其余推送路径仍只遍历 WebSocket（补）

§99 / §100.1–100.2 覆盖了 `_emit` 与 `push_content`，但 `ModelRunner._push_reasoning`、
`_push_expert_output`、`_push_todo_update` 仍然是
`for sid in connection_manager.active_connections: send_json_from_thread(sid, ...)`：
纯 SSE 会话（没有 WS 连接）收不到模型 reasoning、主管/专家的循环输出与 `todo_changed`，
而且会把本会话的帧逐个投给**所有**会话的 WebSocket，等于广播。

**修复：** 三处统一改为 `send_json_from_thread(self.session_id, event)` —— 与
`_push_tool_event` / `_push_stream_chunk` 同款：本会话单点投递，WebSocket 与 SSE
由 `ConnectionManager` 自己扇出。

**测试：** `tests/unit/test_model_runner_ext2.py::test_push_reasoning_targets_own_session`
（本会话没有 WS、只有别的会话时仍按本会话 id 投递）；
`test_push_expert_output_expert` 增加投递目标断言。


### 100.6 仍有推送/清理判定只看 WebSocket（补）

**现象：**
1. 全局错误总线的 `system_error` 帧只发给 WebSocket 会话，纯 SSE 客户端收不到；
2. `/sessions` 的“空会话清理”只把 WebSocket 会话列为保留项，纯 SSE 活跃会话可能被
   当成空会话删除（刚连上、第一条消息还没落库时尤其容易），前端会话列表随即消失。

**根因：** 三处仍直接读 `connection_manager.active_connections`：
- `api/main.py` 的 `_on_error` 广播循环；
- `chat_gateway.get_sessions()`（chatonly）与 `api_stream.get_sessions()`（agent）的
  `delete_empty_sessions(exclude_ids=...)`；
- `ConnectionManager.broadcast()`。

**修复：** `ConnectionManager` 新增 `push_sessions()`（WebSocket ∪ SSE，去重保序），
上述四处统一改用它。`frontend_channel._channels()` 保留原有并集写法（兼容只实现
`active_connections` / `stream_sessions` 的测试桩，且 MagicMock 的可调用属性会返回
空迭代，不能直接委托）。

**测试：**
- `test_api_stream_sse_fanout.py::test_push_sessions_unions_ws_and_sse_without_duplicates`
  （WS + SSE 去重保序、注销后只剩 WS）；
- `test_api_stream_ext.py::test_route_get_sessions_excludes_sse_sessions`；
- `test_chat_gateway_ext2.py::test_route_get_sessions_chatonly_keeps_sse_session`。
