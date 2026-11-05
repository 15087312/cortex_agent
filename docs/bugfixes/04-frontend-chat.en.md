# Error Log & Fixes · Frontend · Chat / Bubbles / Replay / Multi-session

[← Back to index](./README.en.md) | [简体中文](./04-frontend-chat.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 15 sections: §3、§4、§5、§8、§17、§26、§69、§71、§72、§73、§74、§85、§91、§94、§101.

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

---

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

---

## 69. Frontend context-usage display reads low — context_tokens excludes tool-call history (backend)

**Symptom:** ThinkingStatusPanel showed a context-usage percentage lower than reality, mismatching actual consumption.

**Root cause:** `model_runner._generate_with_tools` estimated only once **before entering the tool loop**: `_thinker._context_tokens = engine.estimate_tokens(system + tools + user_prompt)` (model_runner.py:2023); messages accumulated each round inside the tool loop (tool_calls + tool results) were never counted. Meanwhile `_maybe_summarize_context` internally computed messages tokens (for the 90% summarize threshold) but never synced back into `_context_tokens` → the frontend displayed initial-prompt usage, not live accumulation.

**Fix:** Each round, `_maybe_summarize_context` syncs the messages-derived token estimate back into `self._thinker._context_tokens` (regardless of whether summarization triggers), so the frontend displays real usage including tool history.

**Verification:** `test_maybe_summarize_syncs_context_tokens`; frontend ThinkingStatusPanel context warn(70%)/danger(90%)/100%-cap/hidden-when-no-data tests.

**Lesson:** ① A "one-time estimate" used to display cumulative state must cover the full accumulation range (tool history included), else the displayed value distorts; ② token-estimation update points must align with "real consumption points" (where messages grow), not be computed only once at initialization; ③ frontend-displayed fields (context_tokens) need a backend-maintained **authoritative live value**; the frontend merely renders.

---

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

---

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

---

## 73. Restore-path tool traces double-counted + approval text polluting the thinking area (frontend/backend, §71 class)

**Symptom:** Two MED issues surfaced while sweeping §71-class bugs (runtime vs restore inconsistency):

1. **Tool traces double-counted**: supervisor/expert tool traces at restore entered both the expert bubble `_tools` (via expertAgg pre-scan) and the global `traces`; at runtime they entered only expert `_tools` (`addExpertTool`). → duplicated trajectory entries after restore.
2. **Approval/question text polluting the thinking area**: security events ("等待用户审批"/"user_intent_request") persisted as `role='thought', tier='thinking'`, folded at restore into the large-model reply's thinking area — transient interaction text became historical "thinking" content, misleading.

**Fix:**
- MED-1: At restore, supervisor/expert tool traces go only into expert `_tools` (global `traces` receives only `tier!=='supervisor'&&tier!=='expert'`)
- MED-2: Backend `_persist_thought` persists `event_type=='security'` with `tier='security'`; frontend skips `tier==='security'` thoughts at restore (neither folded into the thinking area nor turned into historical bubbles)

**Verification:** Added "expert tool traces not duplicated into global traces" + "security tier skipped" cases; frontend 501 + backend api_stream 180 all pass.

**Lesson:** ① Multi-path accounting of the same data at restore (expert aggregation + global traces) needs dedup protection; whichever path runtime uses, restore must mirror; ② transient interaction events (approvals/questions) must not be persisted/displayed as historical "thinking" — either don't persist them, or tag them with a dedicated tier and skip at restore; ③ cross-stack fixes must ship together (backend persistence tier tagging + frontend recognition).

---

## 74. Architectural root cure: runtime and restore share one classification rule (frontend refactor)

**Background:** §71-73 successively exposed runtime-vs-loadHistory inconsistencies (thinking folding / expert bubble count / attribute names / doubled tool traces / approval pollution), each fixed patch-style ("fix runtime in one place, restore in another"). Root cause: two independent implementations whose attribute names/aggregation rules were never aligned.

**Architectural refactor (root cure):**
- **New pure function `classifyThinking(d)`**: unified classification rules (security skip / approval / intent / tool_trace / expert / thinking), **shared by** runtime WS events and loadHistory restore — a single source of classification truth, eliminating inconsistency at the root
- **New `dispatchThinking(d)`**: runtime WS-event dispatcher (calls classifyThinking, then executes addApproval/addIntent/addExpertThinking/addExpertMessage/addThinkingStep/traces per category)
- **Chat.vue `_onThinking`** changed to call `chat.dispatchThinking(d)` (runtime now flows through the unified classification too)
- **loadHistory** now uses `classifyThinking` to classify persisted messages and accumulate by category (expert aggregation / large-model thinking folding / tool trajectories / security skip) — no separately maintained mapping logic anymore

**Verification:** Added architecture-consistency tests (classifyThinking classification rules + dispatchThinking output/routing split + tool-trace categorization); frontend 504 all pass.

**Lesson:** ① Display rules ("which data → which UI") should be extracted into a **single pure function** invoked by both runtime and restore, not implemented twice — classification then always agrees, and attribute names/aggregation strategies unify naturally; ② restore is not "write another renderer" but "replay/accumulate persisted data under the same rules"; ③ architectural cures beat repeated patches — patches fix today's inconsistency point, while architectural unification eliminates entire bug classes at the source.

---

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

---

## 91. switchToSession replay path merges multiple outputs of the same identity (frontend)

**Symptom:** When switching sessions, multiple outputs of the same identity (tier:identity) — e.g., "前端方案v1", "前端方案v2", "前端方案v3" — merge into one bubble showing only the last. Same origin as §85.

**Root cause:** `chat.js:208-286`'s `switchToSession` replay path aggregates all thinking/contents by `tier:identity` into `expertAgg`, ultimately taking `agg.contents[agg.contents.length - 1]`. Intermediate outputs are lost.

**Scope:** Conversation history appears incomplete after switching sessions.

**Recommended fix:** The replay path should create a bubble per output rather than aggregating by identity.

---

## 94. thinking-content substring dedup over-kills — short text swallowed by longer containing text (frontend)

**Symptom:** AI thinking step A "分析需求" ("analyze requirements") and step B "分析需求并制定方案" ("analyze requirements and devise a plan"): B contains A's substring, so B got skipped by the `includes()` dedup.

**Root cause:** `chat.js:341`: `if (pendingThinking.value.includes(line)) return` matches substrings rather than exact equality.

**Scope:** Thinking steps occasionally lost (low probability but real).

**Recommended fix:** Store already-output lines in a `Set` (exact matching), or compare via `line.trim() === existing.trim()`.

---

## 101. Replay merged multiple supervisor/expert outputs into one bubble — `entry_type` was not persisted (frontend + backend, §85/§91)

**Symptom:** In orchestration mode the same AI (same tier + identity) spoke twice in one round ("方案v1" → "方案v2"); **after a page refresh or switching back to the session only "方案v2" remained**. Worse, supervisor/expert final replies were sometimes stuffed into the large model's "thinking" fold, looking like its reasoning.

**Root cause (two places):**
1. The backend `api_stream._persist_thought()` wrote only `tier` and `identity_name` into `chat_messages.metadata`, **not `entry_type`**. In live frames `data.payload.content.entry_type == "response"` is the only signal the frontend uses to distinguish a "final reply bubble" from "process reasoning" (`classifyThinking`); dropping it on persist made every supervisor/expert utterance classify as `kind='thinking'` on replay.
2. The frontend `chat.js::switchToSession` replay aggregated by `tier:identity` into `agg.contents[]` and pushed a single `content: agg.contents[overwritten]`; the missing `entry_type` meant the `c.kind === 'expert'` branch was never reached, so expert final replies fell through to `kind === 'thinking'` → `pendingLargeThinking` and were attached to the next assistant message's `thinking` (misattribution + lost body).

**Fix:**
1. `_persist_thought(..., entry_type="")`: write `entry_type` into metadata too (only when non-empty, so no redundant key); `scheduler_event_callback` passes its already-computed `_inject_entry_type`. `model_runner._push_expert_output` already wrote `entry_type="thinking"`, so both sides agree.
2. `switchToSession` replay switched to "buffer + consume":
   - restore `data.entry_type` / `identity_name` per row (also accepting top-level `d.entry_type`), so `classifyThinking` receives the same input as at runtime (§74's "one classification rule" still holds).
   - replace `expertAgg{contents[]}` with `expertBuf{tier:identity -> {thinking, tools}}`: on every `response`, **create** a new bubble, attach the current thinking/tools and clear the buffer — exactly like runtime `addExpertMessage`; multiple outputs by the same identity are no longer merged.
   - non-`response` supervisor/expert reasoning goes to the process flow (`addProcessStep`) and no longer pollutes `pendingLargeThinking`; when a `role=process` aggregate exists it is still skipped to avoid duplication.
   - leftover buffers at the end are discarded (matching runtime's "unconsumed thinking").

**Compatibility:** old data has no `entry_type` (that is how the DB was written); replay treats it as process content — no bubble, but also no more incorrect attachment to the large model's thinking area.

**Tests:** `tests/unit/test_api_stream_ext.py::test_persist_thought_keeps_entry_type` (metadata contains entry_type; an empty value does not add a redundant key); `frontend/src/stores/chat.spec.js` adds "历史加载 主管/专家多次输出各自独立气泡" (two responses by the same identity → two bubbles, the tool buffer only attached to the first, expert replies not folded into the large model's thinking).

**Regression:** `frontend` 583 cases; `tests/unit` related subset (three api_stream files + two settings files) 223 cases.


### 101.1 Expert-bubble thinking/tool buffers have no producer left (cleanup)

**Symptom:** None (invisible), but `switchToSession` replay put supervisor/expert tool text into the expert bubble's `_tools`, while live runtime sent the same text to the process flow — the display differed before/after a refresh.

**Root cause:** Since 414375c (expert reasoning/tool traces moved to the process flow) `addExpertThinking` / `addExpertTool` had **no production callers**; only the `_pendingByTier` / `_pendingTools` buffers — always empty — were read by `addExpertMessage`. Yet the replay path still filled `_tools` (it was aligned to the old model during the §101 rework). "Bubble-local thinking/tools" was thus visible only on replay.

**Fix:** delete the dead code and unify on the process flow:
1. Remove `addExpertThinking` / `addExpertTool` / `_pendingByTier` / `_pendingTools` / `_expertBubbles` and `_hasThinkingLine`.
2. `addExpertMessage` only creates the final-reply bubble and no longer reads buffers.
3. `switchToSession`: supervisor/expert tool text now goes through `addProcessStep` (same as runtime `dispatchThinking`); expert bubbles no longer write `_thinking` / `_tools`.
4. `ChatMessage.vue` removes the now-unreachable thinking fold and tool list inside expert bubbles.

**Tests:** the `chat.spec.js` replay case now asserts bubbles have no `_tools` and that tool text lands in `processFlow`; `ChatMessage.spec.js` now asserts expert bubbles do not render thinking/tools (regression guard). All 583 frontend cases pass.
