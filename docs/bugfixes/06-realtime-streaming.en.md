# Error Log & Fixes · Realtime · WS / SSE / Push / Proxy / TUI

[← Back to index](./README.en.md) | [简体中文](./06-realtime-streaming.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 8 sections: §12、§24、§68、§82、§98、§99、§100、§104.

---
## 12. Bare frontend fetch paths all 404 under the 8765 static proxy (frontend)

**Symptom:** Graph page shows "no agents at this tier"; Orchestration/Skills/Session settings data won't load; browser Network panel shows 404 for those requests.

**Error (browser console):**
```
TypeError: Failed to fetch dynamically imported module: http://localhost:8765/assets/xxx.js
```
(This one is actually a different class: stale chunks after build, see 12.2)

**Root cause:** The frontend SPA is served statically by `frontend/server.py` (port 8765), which **only proxies `/api/*` to the backend :8080 stripping the `/api` prefix**. New page code used bare paths directly:
```js
fetch('/management/orchestration')   // ❌ 8765 doesn't proxy /management → 404
fetch('/config/persona/...')          // ❌ same 404
fetch('/stream/session/...')          // ❌ same 404
```
Only requests through the `endpoints.*` wrappers (api.js `BASE='/api'`) or explicit `fetch('/api/...')` go through the proxy correctly.

Measured:
- `8765/management/orchestration` → **404**
- `8765/api/management/orchestration` → **200** (proxy strips prefix to 8080)
- `8080/api/management/orchestration` → 401 (8080 has no `/api` routes; **never use 8080's /api directly**)

**Fix:** All bare `fetch('/management|/config|/stream|/tools/...')` unified with the `/api/` prefix (8765 proxy strips it to 8080). 8 files affected: Graph.vue, Orchestration.vue, Skills.vue, SessionSettings.vue, ScheduledTasks.vue, Dashboard.vue, Settings.vue, ChatMessage.vue.

**Key conventions (must follow when writing frontend code):**
1. **All backend API requests must use the `/api/` prefix** (or go through `endpoints.*` wrappers) — both 8765/vite proxies strip `/api` and forward to 8080; bare paths 404 directly on 8765
2. **WebSocket exception**: `ws/client.js` connects directly to `:8080/stream/ws/{sid}` (8765 has no WS forwarding); don't change it to 8765
3. **`/audio` and `/pet` assets stay on bare paths** (used respectively by backend static serving / desktop pet, unaffected by the proxy)
4. **Never hit 8080 directly with `/api/` prefixed** — backend routes have no `/api` prefix, would 401

**Verification:** After the fix all pages load normally; `grep -rnE "fetch\\('/(?!api/)"` finds no leftover bare fetches.

### 12.1 Same class: lazy-loaded chunk 404 (old tabs white-screen after rebuild)

**Symptom:** After `npm run build`, an already-open old page navigating to lazy-loaded routes reports:
```
Error: Unable to preload CSS for /assets/Graph-xxx.css
TypeError: Failed to fetch dynamically imported module: /assets/Graph-xxx.js
```

**Root cause:** Vite build artifacts are content-hashed; after rebuild, chunks with old hashes are deleted. The browser's old page still references old hashes in its in-memory module graph; navigation triggers dynamic import → 404.

**Fix:**
- `frontend/src/router.js` adds `router.onError`: on `Failed to fetch dynamically imported module` / `Unable to preload CSS`, auto `location.reload()` to pull fresh index.html + chunks
- `frontend/server.py` serves `/assets/*` with `Cache-Control: public, max-age=31536000, immutable` (hashed files never change content); `index.html` stays `no-cache` (always fresh, referencing the latest chunk list)

**Lesson:** After changing backend/frontend remember to `npm run build`; users with old tabs need one refresh; lazy-load failures after builds now auto-recover via reload.

---

## 24. send_json_from_thread self-deadlock inside the event-loop thread → conversation errors (backend)

**Error (ERROR, empty message):**
```
2026-08-10 17:34:32 - stream_api - ERROR - [ConnectionManager] send_json_from_thread failed:
```
The **empty message after the colon** is the telltale sign — this is `concurrent.futures.TimeoutError` (thrown by `future.result(timeout)`, whose `str()` is empty).

**Symptom:** During conversations (especially when deepseek reasoning models return `reasoning_content`), backend logs flood with this error and thinking-event pushes stall for 5 seconds each.

**Root cause:** `ConnectionManager.send_json_from_thread()` in `modules/thinking/api_stream.py` sends synchronously via
`asyncio.run_coroutine_threadsafe(_send(), self._loop)` + `future.result(timeout=5.0)`; it's designed for **non-event-loop threads** (daemon threads) to call safely. But `_push_reasoning`
(`modules/thinking/core/model_runner.py:1204`, pushes thinking events during model reasoning) calls it **from within the event-loop thread itself**:

- `run_coroutine_threadsafe` schedules `_send` onto the event loop;
- `future.result(timeout=5)` **blocks the event-loop thread itself** → `_send` never gets to run;
- After 5 seconds: `TimeoutError` with empty message; every push stalls 5s.

I.e., "schedule yourself + block yourself" self-deadlock (`run_coroutine_threadsafe` docs explicitly say it must only be called from non-loop threads).

**Why tests missed it:**
- `tests/unit/test_api_stream_core.py` tested only three paths: no event loop, no connections, and calling from a **dedicated worker thread**
  (`test_send_json_from_thread_success` happened to exercise the cross-thread path that works);
- The **called-from-loop-thread** path had no test — it requires a real conversation with a model emitting `reasoning_content`,
  and it's a runtime timing issue (the loop must actually be blocked 5s before the error); neither unit nor integration tests can construct that.

**Fix:** `send_json_from_thread` entry checks whether it's already running on the event-loop thread (`asyncio.get_running_loop() is self._loop`):
- Yes → schedule via `asyncio.create_task(_fire_and_forget())`, **non-blocking**, returns True immediately;
- No (background thread) → keep original `run_coroutine_threadsafe + future.result(timeout)` semantics.

**Verification:** New regression test `test_send_json_from_thread_on_loop_thread_not_blocked`
(`tests/unit/test_api_stream_core.py`) — called within an async context (loop thread), asserts immediate True return
and message delivery; all 13 related cases pass; full suite 1460 passing.

**Lessons:**
1. **Every "thread-safe" synchronous entry must also handle being called from the event-loop thread** — calling `run_coroutine_threadsafe`/`loop.call_soon_threadsafe` from the loop thread itself is deadlock/timeout;
2. **Empty error messages usually come from `TimeoutError`/`CancelledError`** (empty `str()`) — when logs show "failed: " followed by nothing, suspect synchronous wait timeouts first;
3. **Tests must cover the real production call context** — testing only another thread's success path misses the loop-thread self-deadlock scenario.

---

## 104. Frontend cannot reach backend: IPv6 localhost trap + test blind spot in the frontend proxy layer (frontend/backend)

> **Numbering fix:** the original document reused §40 here (duplicate of an earlier §40); this section is now §104.

**Symptom:** Clicking features on the frontend page showed "waiting for backend to start"; the desktop pet/frontend process then exited. Backend `curl :8080/health` was fine, but the frontend's polling of `/api/health` kept failing.

**Root cause (two):**
1. **IPv6 `localhost` trap**: the `frontend/server.py` proxy used `http://localhost:{port}` — on macOS `localhost` resolves to `::1` (IPv6), while the backend binds only `127.0.0.1` (IPv4, `--host 127.0.0.1`) → urllib went IPv6 → **502**. `curl` prefers IPv4, so direct tests of 8080 looked fine, misdirecting the investigation.
2. **Frontend port fixed and out of sync**: server.py pinned `BACKEND_URL` at startup; after a backend port fallback/restart it drifted; cortex launching the backend also never wrote the port-discovery file.

**Fix:**
1. Proxy switched to explicit `127.0.0.1` (IPv4), mirrored in `pet_widget.py`
2. Proxy **dynamically reads** `read_backend_port()` on every request (frontend follows port changes)
3. cortex writes the port-discovery file via `save_backend_port` when starting the backend

**Verification:** `8765/api/health` went from 502 → 200.

**Test blind spot (why it wasn't caught):** `frontend/server.py` (the Python proxy layer) previously had **zero tests**, and:
- pytest tested only `tests/` (backend); vitest tested only JS — the Python proxy layer was "unclaimed"
- The module coverage checklist `_PRODUCTION_DIRS` excluded `frontend` → the "full coverage" proof only covered the backend
- IPv6 localhost is runtime resolution behavior; unit tests connecting directly to 127.0.0.1 never trigger it

**Blind-spot fix:** Added `tests/unit/test_frontend_server.py` (10 cases: IPv4 regression, dynamic port, /api stripping, error passthrough); the coverage checklist now includes `frontend/` (server.py/pet_widget.py, Qt GUI launcher exempt).

**Lesson:** Every piece of Python code must have **explicit test ownership** and be listed in the coverage checklist — it cannot leave the test regime just because "the directory belongs to the frontend". Environment-class bugs (DNS/IPv6 resolution) require explicitly mocked behavior in tests.

---

## 68. Todo panel's 3-second polling → WS event push + pull-on-demand (frontend/backend)

**Symptom:** The frontend todo panel polled via `setInterval(loadTodos, 3000)`, so after the model updated todos, display lagged by up to 3 seconds; and the polling fired a request every 3 seconds, continuously consuming resources even for idle sessions.

**Root cause:** The todo tool (invoked by the model) executes via MCP in `model_runner._generate_with_tools`, leaving the frontend oblivious — it could only pull via polling; the architecture was "pull" rather than "push".

**Fix (architecture change: polling → push + on-demand fetch):**
- Backend: `model_runner` gains `_push_todo_update()`; after successful todo-tool execution it pushes a `type='todo', event='todo_changed'` WS event (with session_id) via `connection_manager.send_json_from_thread`
- Frontend: `Chat.vue` registers `wsClient.on('todo', _onTodo)` (onMounted) → upon receiving the event, calls `loadTodos()` **on demand**; removed the 3-second `setInterval` polling; `_onTodo` filters by session_id (pushes from other sessions don't refresh the current view)

**Persistence (unchanged, already sound):** Todo tool invocations write `~/.cortex/todos/{session_id}.json` (`_save_todos`) immediately, isolated per session; nothing lost when switching sessions.

**Verification:** Backend `test_push_todo_update` + `test_todo_tool_execution_triggers_push`; frontend `_onTodo` two cases (triggers refresh / other sessions ignored); frontend 497 + all related backend pass.

**Lesson:** ① Frontend "real-time state" should prefer **event push** over timed polling — where a WS channel exists, push + pull-on-demand avoids wasteful polling; ② pushed events must carry `session_id` and be filtered per session on the frontend, otherwise multi-session cross-talk; ③ for persisted state like todos, writing to disk upon model tool invocation is the correct paradigm — no extra synchronization needed.

---

## 82. Multi-session parallelism: single WS connection + global processing state caused cross-session interference (frontend ws client / chat store / Chat.vue)

**Symptom:** After asking a question in session A and switching to B, a banner appeared: 「会话「b1da48c1…」正在思考中，切回该会话可查看进度」("Session 'b1da48c1…' is thinking; switch back to view progress"); A's reply was lost (invisible after returning to A); B's processing state was unexpectedly cleared by A's done/error events. User requirement: sessions independent and parallel, with no cross-session banners.

**Root cause:** The frontend WS client `frontend/src/ws/client.js` was **single-connection** (`this._conn` holds one WebSocket; `connect()` closes the old one); the chat store's `processing`/`_processingSid` was **globally singular**. Switching to B while A processed and sending a message: `_ensureConnected` force-closed A's connection (`this._conn.close()` inside `_doConnect`), A's backend task kept running but results could never arrive; `_onDone`/`_onError` called `finalizeStream('')` even for non-current-session events, unconditionally clearing the global `processing` and wrongly wiping B's state; `_onMessage`/`_onThinking` did filter by `session_id` (nothing mixed into B's stream), but state clearing and connection switching were not session-isolated.

**Fix:** True session parallelism requires every processing session to hold its own connection + its own state:
- `frontend/src/ws/client.js` converted to multi-connection: `_conns = { sid: {conn, ...} }`, adding `isConnected(sid)`, `send(sid, data)`, `disconnect(sid)`, `disconnectAll()`; `connect(sid)` no longer closes other sessions' connections
- `frontend/src/stores/chat.js`: `_processingSid` → `_processingSids` (a `Set` supporting multi-session parallelism); `processing` became a computed (is the current session processing?); added `finalizeSession(sid)` for precise per-session cleanup plus a `markProcessing(sid, on)` helper; `switchToSession` always connects the target session while preserving other sessions' processing states; `sendMessage`/`stop`/`clearMessages` operate on the current session
- `frontend/src/pages/Chat.vue`: removed the cross-session banner 「会话xxx正在思考中」("Session xxx is thinking"); `_onDone`/`_onError` call only `finalizeSession(sid)` for non-current-session events, leaving the current session untouched; the watchdog checks the current session's connection (`isConnected(session.sessionId)`)

**Verification:** ws client gained multi-session independent-connection tests; chat store gained parallel-session scenario tests (switch from A-processing to B while B also processes; A's done doesn't clear B's state); Chat.spec gained a real `switchToSession` flow verifying A's reply/thinking/done never mix into B; all 505 frontend tests pass, `npm run build` passes.

**Lesson:** "Session parallelism" demands that both connection setup/teardown and state lifecycles be isolated along the session dimension. A singleton connection + global boolean supports only single-session serialization; multi-session concurrency must model both "connections" and "processing state" as sets/maps, routing events precisely by `session_id`.

---

## 98. New events only worked in the browser — TUI saw no tools, SSE buffered until the end, replay mis-ordered a round (cross-client)

When the visualization events (Phase 0–4) landed, only the Web frontend consumed all of them. Three gaps shared one consequence: **the same conversation looked different depending on the entry point**.

### 98.1 `cli_tui` did not understand the new events at all

**Symptom:** Running a tool-using task in the terminal left the tool panel empty; the answer appeared only after the whole generation finished; the context-usage bar never moved. The same session was fine in the Web UI.

**Root cause:** `cli_tui/services/ws_client.py::_parse_event_inner` only knew the old envelopes (`stage_event` rich events + simple `thinking`/`status` events). The backend now pushes `type=tool` / `type=chunk` / `type=session_projection`, and the TUI returned `None` for all three, dropping them. The `entry_type=streaming_delta` branch in `cli_tui/screens/repl.py` was therefore dead code (nothing ever produced such an entry).

**Fix:**
1. `parse_event` gains three parsers: `type=tool` → `_parse_tool_frame` (`call_id` / `name` / one-line action summary / status / duration / result preview), `type=chunk` → a dialog entry with `entry_type=streaming_delta`, `type=session_projection` handled directly by `repl`.
2. The one-line summary uses the card fields computed by the server (`view.title/command/path/query`), falling back to the command/path/query in the arguments — the TUI does not re-guess tool semantics.
3. Structured events send two frames per call (`tool_call` creates the row, `tool_result` completes it). The new `cli_tui.state.merge_tool_record` merges by `call_id`: no duplicate rows, no double counting, and a result frame never overwrites the call frame's arguments. `AppState.add_tool_call` and `WSClient._process_input_impl` share it.
4. `chunk` frames only accumulate the current round's streaming character count and update `thinking_hint` (`生成中… N 字`); they are **not** written into the dialog frame by frame — the final `assistant_message` renders the whole text, so writing deltas would print the body twice in the terminal.
5. `session_projection` only handles keys the TUI actually displays: `contextPressure` (context bar) and `runners` (top-bar model identity). There are no panels for `todos`/`tokenUsage`, so we do not invent any.
6. The model-status parsing in `thinking_progress` was extracted into `_apply_runner_snapshot`, reusing the same tier mapping as the `runners` projection.

**Tests:** `tests/unit/test_ws_client.py` adds `TestParseEventToolFrame` (5 cases: call frame, result frame, failure status, argument fallback without `view`, `data=None` does not raise) and `TestParseEventChunkFrame` (2 cases); `tests/unit/test_tui_state.py` adds `TestToolRecordMerge` (3 cases: merge into one row while keeping arguments, failed result overrides success, missing `call_id` is not merged).

**Follow-up:** `session_request` (the answerable frame) was not wired up at the time, and TUI approvals still used the old `security` event; it was connected in §99.2 (`_parse_session_request` reuses the existing approval component, while the old `security` frame is still dual-sent).

### 98.2 The SSE path had no streaming

**Symptom:** With SSE (`GET /stream/sse/{sid}`) instead of WebSocket, the answer appeared all at once; the WS endpoint streamed the same question token by token.

**Root cause:** `chat_gateway._chatonly_sse` appended every `message` token to `full_text` but yielded no body frame inside the loop, then sent one `assistant_message` at the end. The WS branch (`_consume_turn._flush_reply_chunk`) had long been pushing `chunk` frames throttled to 40 chars / sentence end, so the two paths diverged.

**Fix:** `_chatonly_sse` gains the same `flush_buf` + `_take_chunk()`: flush at 40 chars or on `\n。！？.!?`, and flush the tail before `done` and `error`. `chunk` remains ephemeral (not persisted, no seq); the final `assistant_message` is authoritative.

**Tests:** `tests/unit/test_chat_gateway_ext2.py` adds 3 cases: flush at sentence end (exact content of two frames + final message still complete), accumulate below 40 chars (`done` flushes one `abcdef` frame), flush before an error (`chunk` precedes `error` and contains the generated part).

### 98.3 Replay piled every tool of a round under the user message

**Symptom:** After refreshing, the tool cards of a round that went "say something → call a tool → say more" all piled under the user message, as if a batch of tools ran before speaking; the live order was correct.

**Root cause:** `chat.js::replayToolEvents` only advanced its anchor on `user_message` events and explicitly skipped assistant `message` events (the original concern was that old sessions without user-message events would attach tools to the previous reply). As a result **all** tools could only attach to a user message, losing the relative order inside a round.

**Fix:** assistant final messages also advance the anchor, with two guards:
1. only in **new-format sessions** (the event stream contained a `user_message`) — old sessions keep "user messages only + time fallback", unchanged;
2. only when the message was **actually persisted** (`data.message_id` must be in the set of message ids rebuilt in this pass) — interrupted drafts have no `message_id`, and a final message that was not persisted has a mismatched id; in both cases keep the previous anchor, preferring "no anchor" (folded panel) over a wrong one.

**Tests:** `frontend/src/stores/chat.spec.js`: the old "everything in a round hangs under the user message" case now asserts `['u1','a1']` (the interleaving of "say → tool → say again"), a new case covers "keep the previous anchor when the final message was not persisted", and "no anchor for old sessions without `user_message` events" keeps passing.

**Regression:** `tests/unit` 6417 cases (1 skipped, including 13 new), `tests/integration` 432 cases (1 skipped) all passing; `tests/unit/test_ws_client.py` + `test_tui_state.py` 64 cases, `test_chat_gateway*.py` 83 cases; frontend vitest 51 files / 582 cases all passing.

---

## 99. SSE saw no tool/streaming frames; TUI did not accept the answerable frame (backend + TUI)

### 99.1 SSE received only scheduling events

**Symptom:** An agent-mode SSE session (`GET /stream/sse/{sid}?question=`) delivered the body all at once, showed no tool traces, and never updated todos or context usage; the same session over WebSocket was fine.

**Root cause:** Frames have two sources:
1. `_emit(...)` → `callback` (the SSE queue) + `connection_manager.send_json`;
2. `ModelRunner._push_tool_event` / `_push_stream_chunk`, `projections._push_frame`,
   `pending_requests.broadcast` → **only `connection_manager`** (tools/streaming/projections/todos).

`ConnectionManager` only tracked `active_connections` (WebSocket), and `send_json_from_thread` returned `False` immediately when there was "no WS connection"; SSE had no subscription entry point at all. So source 2 did not exist for SSE clients — not a failed push, but no recipient whatsoever.

**Fix:** add a second outlet to `ConnectionManager`, parallel to WebSocket:
1. `subscribe_stream(session_id) / unsubscribe_stream(...) / has_stream(...)`: one `asyncio.Queue` per subscriber per session (each subscriber gets its own copy); subscribing also records `_loop` — a pure SSE session never `connect`s over WS, and an empty `_loop` would make `send_json_from_thread` return early;
2. `send_json` fans out to streams first, then sends WS; `send_json_from_thread` counts "has stream subscribers" as an outlet and fans out inside the coroutine scheduled on the event loop (`Queue.put_nowait` cannot be called from another thread);
3. `_stream_sse` subscribes its own queue and drains both queues in the loop: scheduling events first (ordering), tools/projections from the stream queue; the same envelope object arrives through both `callback` and fan-out, so dedupe by object identity with a bounded `deque(maxlen=256)`; `finally` unsubscribes (client disconnect must clean up too);
4. align with `websocket_chat`: on connect, `projections.push_current` + `pending_requests.broadcast`, so todos/context usage/approvals are visible without waiting for the next state change.

**Tests:** new `tests/unit/test_api_stream_sse_fanout.py` (5 cases): `send_json` delivers with only an SSE subscriber; `send_json_from_thread` returns True and the frame arrives with no WS and only a stream subscriber; returns False with no outlet; multiple subscribers each get a copy; `_stream_sse` receives a `tool_call` frame delivered only via fan-out, the same envelope is not sent twice, and the subscription is removed after the generator ends.

### 99.2 TUI did not recognize `session_request`

**Symptom:** After a refresh the Web UI still showed the "pending approval" banner and the confirm button; after a refresh/reconnect the terminal showed nothing, and the approval Future could only time out.

**Root cause:** Phase 4.b made todos a full `session_request` snapshot (the only thing that can rebuild banners after refresh/reconnect). The Web frontend consumed it, but the TUI `parse_event` did not know the name at all. The terminal could only rely on the old one-shot `security` event, which disappears after a refresh.

**Fix:** `WSClient._parse_session_request` maps the snapshot back to the two existing TUI entries: `approval` → `security_review` (`request_id`/`tool`/`detail`), `question` → `user_intent_request` (`question`/`options`/`context`), so the approval component and the `security_response` / `interactive_response` reporting are all reused instead of writing a second set. An empty snapshot → `pending_clear`; `repl` collapses the approval panel and clears `pending_security_review`, so no dead buttons remain after answering. With multiple requests in one frame, take the earliest registered one; after answering, the next snapshot naturally continues.

**Tests:** `tests/unit/test_ws_client.py::TestParseEventSessionRequest` (5 cases): approval mapping, question mapping (with options/context), first of multiple requests, empty snapshot → `pending_clear`, dirty snapshot with missing fields ignored.

**Regression:** `tests/unit/test_ws_client.py` + `test_tui_state.py` + `test_api_stream_sse_fanout.py` 74 cases; `test_api_stream_ext.py` + `test_api_stream_think_ext.py` + `test_pending_requests.py` 71 cases.

---

## 100. SSE sessions treated as "frontend disconnected": handshake skipped the LLM turn, pushes only targeted WS, disconnect left state behind (backend)

After §99 opened the SSE frame outlet, three places still equated "is there a WebSocket connection" with "is the frontend online"; any one of them was enough to make SSE look "unreachable" or "stuck".

### 100.1 Handshake failure → the LLM turn was skipped entirely

**Symptom:** Connecting to `GET /stream/sse/{sid}?question=` produced only a `connection_lost` frame ("前端连接已断开，本轮请求已跳过，可在重连后重试") and no answer; the same session over WebSocket was fine.

**Root cause:** Before calling the model, `think()` performs the handshake `frontend_channel.confirm_frontend_connection(session_id)`, whose first line was `if not cm.active_connections: return False` — `active_connections` only holds WebSockets. A pure SSE client never `connect`ed over WS, so the handshake always failed and the turn returned before the LLM.

**Fix:** the handshake now looks at "all push outlets": the union of `ConnectionManager.stream_sessions()` (new) and `active_connections` (`frontend_channel._channels`). `confirm_frontend_connection` and `_confirm_async` both use it to decide online status and broadcast targets; an SSE session is reachable thanks to the new `send_json_from_thread` semantics (stream subscribers count as delivered).

### 100.2 `push_content` only iterated WebSocket sessions

**Symptom:** Proactive messages / scheduled jobs / background pushes never reached SSE clients (they were persisted and appeared in history after a refresh, but did not pop up at the time).

**Root cause:** `push_content`'s send loop was hardcoded to `for sid in cm.active_connections`.

**Fix:** iterate the union; when a session has both WS and SSE, `send_json` fans out internally exactly once, so no duplicates.

### 100.3 SSE disconnect did not clear pending approvals or cancel the task

**Symptom:** When an SSE client closed the page / lost the network during a tool approval (or `ask_user_intent`): the session stayed "processing" forever and every later request was busy; logs showed the approval neither approved nor rejected; the model kept generating (burning tokens).

**Root cause:** `websocket_chat`'s `finally` called `reject_session_reviews` + `reject_session_user_responses` + `pending_requests.clear`, while `_stream_sse`'s `finally` only unsubscribed its own queue. `sse_starlette` closes the generator when the client disconnects, leaving that `_safe_think` task as an orphan: the approval future hung forever (freezing the global `Suspension` timer) and nobody consumed the task's exception.

**Fix:** extract the three WS cleanup steps into `_cleanup_session_on_disconnect(session_id, tag)` and share it between the WS and SSE `finally` blocks; SSE additionally calls `task.cancel()` (SSE has no "resume after reconnect" semantics — if the client left, stop generating) and attaches `add_done_callback` to consume the exception, avoiding "Task exception was never retrieved" noise. After cancellation, `sessions[sid]["processing"]` must be set back to `False` manually: the `await _set_processing(False)` in `think()`'s `finally` may not finish when the task is cancelled, and a miss leaves the session busy forever.

### 100.4 Aside: the dedupe `deque` stored `id()` (incidental fix)

§99 deduped the same envelope (arriving through both `callback` and fan-out) by object `id()`. With only ints in the `deque`, the object was long gone and CPython could reuse the `id` — a new frame could collide with a just-freed id and be dropped. Store the objects themselves in the `deque` (`any(e is event for e in seen)`): identity comparison is preserved and the objects stay alive so ids cannot be reused.

**Tests:** `tests/unit/test_frontend_channel_ext.py` adds 6 cases: a pure SSE session handshakes successfully and only receives its own frames; broadcast includes SSE sessions; False only with no outlet; the same for `_confirm_async`; `push_content` reaches a pure SSE session; no duplicate send when WS+SSE are both online.
`tests/unit/test_api_stream_sse_fanout.py` adds 1 case: after the generator is `aclose()`d (client disconnect), the subscription is removed, all three cleanup paths (pending approvals / pending interactions / pending todos) are called, the thinking task is cancelled, and the session no longer stays "processing".

**Regression:** `tests/unit` full 6434 cases (1 skipped, 12 deselected) passing; related subset `-k "frontend_channel or pending_requests or api_stream or chat_gateway or projection or proactive"` 379 cases; `tests/integration` 432 cases (1 skipped).


### 100.5 Other push paths still iterated only WebSocket (follow-up)

§99 / §100.1–100.2 covered `_emit` and `push_content`, but `ModelRunner._push_reasoning`, `_push_expert_output` and `_push_todo_update` still did `for sid in connection_manager.active_connections: send_json_from_thread(sid, ...)`: pure SSE sessions (no WS connection) received no model reasoning, no supervisor/expert loop output and no `todo_changed`, and the session's frames were delivered one by one to **every** session's WebSocket — effectively a broadcast.

**Fix:** all three now use `send_json_from_thread(self.session_id, event)` — same as `_push_tool_event` / `_push_stream_chunk`: single-target delivery for this session, with `ConnectionManager` handling WS/SSE fan-out itself.

**Tests:** `tests/unit/test_model_runner_ext2.py::test_push_reasoning_targets_own_session` (delivers to this session's id even when it has no WS and only another session does); `test_push_expert_output_expert` now also asserts the delivery target.


### 100.6 Some push/cleanup decisions still looked only at WebSocket (follow-up)

**Symptom:**
1. `system_error` frames from the global error bus were only sent to WebSocket sessions; pure SSE clients never received them;
2. the `/sessions` empty-session cleanup only protected WebSocket sessions, so an active pure-SSE session could be deleted as "empty" (especially right after connecting, before its first message was persisted), and the session then vanished from the frontend list.

**Root cause:** three places still read `connection_manager.active_connections` directly:
- the `_on_error` broadcast loop in `api/main.py`;
- `delete_empty_sessions(exclude_ids=...)` in `chat_gateway.get_sessions()` (chatonly) and `api_stream.get_sessions()` (agent);
- `ConnectionManager.broadcast()`.

**Fix:** `ConnectionManager` gains `push_sessions()` (WebSocket ∪ SSE, deduplicated, order preserved), and all four places use it. `frontend_channel._channels()` keeps its explicit union (tests stubs may only provide `active_connections` / `stream_sessions`, and a `MagicMock` callable attribute iterates as empty, so it must not delegate blindly).

**Tests:**
- `test_api_stream_sse_fanout.py::test_push_sessions_unions_ws_and_sse_without_duplicates` (dedup/order for WS + SSE; only WS after unsubscribing);
- `test_api_stream_ext.py::test_route_get_sessions_excludes_sse_sessions`;
- `test_chat_gateway_ext2.py::test_route_get_sessions_chatonly_keeps_sse_session`.
