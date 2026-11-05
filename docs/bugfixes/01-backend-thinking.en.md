# Error Log & Fixes · Backend · Thinking / Model / Tools / Delegation

[← Back to index](./README.en.md) | [简体中文](./01-backend-thinking.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 27 sections: §1、§10、§19、§21、§25、§39、§46、§47、§48、§57、§59、§60、§61、§62、§63、§64、§67、§70、§76、§79、§80、§83、§84、§92、§93、§95、§97.

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

---

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

---

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

---

## 39. mypy fix introduces a regression: input_controller idempotency broken (backend)

**Symptom:** Full-suite run: `test_input_controller.py::test_init_idempotent_and_force` failed (`PyAutoGUIController` created 2 times, expected 1). Full suite: 5833 passed / 1 failed.

**Root cause:** During mypy auto-fixing, to eliminate the var-annotated error on `_initialized`, `self._initialized: bool = False` was added **before** the `hasattr(self, '_initialized')` check — the assignment resets the flag to False on every `__init__`, making the idempotency short-circuit `hasattr(...) and self._initialized and not force` always false → the second init recreated the controller.

**Fix:** Changed to **declaration-only, no assignment**: `self._initialized: bool` (satisfies mypy, no runtime reset), restoring the idempotency logic.

**Verification:** `test_input_controller.py` 24 passed; `mypy modules/output_system/input_controller.py` 0 errors.

**Lesson:** Automated type fixes (adding declarations/annotations) can introduce **behavior changes** — "adding an initialization line" ≠ "adding an annotation". After a mypy fix you must run **all tests for that file** (not just mypy 0 errors) to verify behavior is unchanged.

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

**Symptom:** On the orchestration page the user disabled the custom agent `123` and activated `orchestrator`, but pure-chat mode still force-applied `123`'s custom persona ("RoleA"), and editing other prompts "had no effect" either.

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

## 57. Thinking-mode tool loop reports 400: assistant message doesn't return reasoning_content (backend)

**Symptom:** The code supervisor (code_supervisor) errored during multi-turn tool calls:
`400 - The reasoning_content in the thinking mode must be passed back to the API.` (provider: Console Go / DeepSeek thinking mode)

**Root cause:** In `modules/thinking/core/model_runner.py`'s tool loop, when constructing the **assistant message declaring tool_calls** (`messages.append(ChatMessage(role="assistant", content=None, tool_calls=all_result_calls))`), **this round's `reasoning_content` was not attached**. In thinking mode, once an assistant message generated reasoning, subsequent requests must return it verbatim (only `if m.reasoning_content: msg["reasoning_content"]=...` inside `_messages_to_api` sends it back). In history, that assistant message had reasoning_content None → not returned → next request 400s.

**Fix (around model_runner.py:1740):** When constructing the tool_calls assistant message, attach `reasoning_content=getattr(response.message, "reasoning_content", None)` — returned in thinking mode; None and harmless otherwise.

**Verification:** 314 model_runner / large_model_client related tests pass.

**Lesson:** For thinking-mode (Reasoner) multi-turn tool calls, **assistant messages must fully preserve and return `reasoning_content`** (just as important as `tool_call_id`/`tool_calls`) — no site constructing an assistant message may drop it, or cross-turn requests get rejected by the provider. Triage mantra for such 400s: does the history's assistant carry reasoning_content + does `_messages_to_api` send it back.

---

## 59. delegate_task's wait_seconds parsed but never forwarded — supervisor-set subordinate timeout always falls back to 300s (backend)

**Symptom:** After the request to "let the upper-level large model set the subordinate's thinking timeout itself", the `delegate_task` tool marked `wait_seconds` as required, yet the runtime value set by the supervisor **never took effect** — the subordinate runner's think timeout stayed pinned at `DEFAULT_DELEGATE_THINK_TIMEOUT=300`.

**Root cause:** At `model_runner._generate_with_tools`'s delegate_calls dispatch, only `role`/`task` were parsed; **`args["wait_seconds"]` was never fed into `DelegationRequest`** (delegation_port.py:24 field stayed None); receiving None, `ProbeDelegationAdapter.delegate` took the fallback branch (warning only on absence). The tool schema declared "required + effective", but the chain snapped between parsing and consumption.

**Fix (commit `16bd27e`):** delegate_calls dispatch parses `wait_seconds` (clamped 1-600) → `DelegationRequest.wait_seconds` → probe_started `think_timeout` → `_handle_probe_started` → `start_runner(think_timeout)` → `runner.THINK_TIMEOUT` (overridden in delegation scenarios, default otherwise).

**Verification:** `test_delegate_think_timeout_passed` / `test_delegate_think_timeout_fallback`; 288+ related pass.

**Lesson:** "A tool parameter declared required" ≠ "the parameter truly takes effect" — **parse → forward → consume** must be tested as one connected flow. This pattern (schema declarations disconnected from actual forwarding) is a hotspot for same-class bugs; when triaging, search whether the parameter is genuinely used downstream of the parse site.

---

## 60. think_once's outer 120s nested timeout overrides the runner's THINK_TIMEOUT — the supervisor-set timeout gets swallowed (backend)

**Symptom:** Even after §59 fixed `wait_seconds` forwarding, the supervisor-assigned subordinate timeout (e.g., 300s) still had no effect — a single think always ended at 120s.

**Root cause:** Doubly nested `pausable_wait_for`: `think_once`'s outer `timeout=SINGLE_THINK_TIMEOUT=120` (continuous_thinker.py:32) wraps the entire `think_fn` (i.e., `runner._generate`), while inside the runner `timeout=self.THINK_TIMEOUT` (300). **The outer 120s elapses first → cancels the inner → the supervisor-set 300s is never reached**. Nested timeouts resolve to "the smaller one", not the outer's "ceiling".

**Fix:** `think_once` switched to `getattr(runner_ref, "THINK_TIMEOUT", None) or SINGLE_THINK_TIMEOUT` — the upper layer's timeout aligns with the runner's (delegation-supplied values truly take effect); the timeout-retry branch's log/error lines likewise use the dynamic `_timeout`.

**Verification:** 609 related pass.

**Lesson:** With layered `wait_for`/timeout wrappers, **an outer timeout smaller than the inner means the inner never fires** (effective timeout = smallest nested value). Whenever making timeouts configurable, audit every wrapping layer so none is smaller than the configured value.

---

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

---

## 62. thinking_result's two production paths disagree on delegation_id semantics — RuntimeExpert path uses task_id, blackboard delegation chain finds nothing (backend)

**Symptom:** After on-demand experts (RuntimeExpert, e.g., SecurityMonitor) completed, the supervisor's `query_delegation` couldn't find that delegation's result/status update — the delegation chain stayed stuck at "delegated".

**Root cause:** After §61's fix, the thinking_result sent by `continuous_thinker._notify_return_target` uses `delegation_id = runner._delegation_id` (**probe_id**, the blackboard delegation-chain key); but `model_runner._run_runtime_expert`'s wakeup path (around model_runner.py:367) still wrote `"delegation_id": self._task_id` (**task_id**). Two production paths emitting the same message field with different semantics → the supervisor's `_wait_for_wakeup_event` wrote back keyed by delegation_id and blackboard `get_delegation()` could never match (task_id isn't the blackboard key) → RuntimeExpert delegation status/results never landed on the blackboard.

**Fix:** `_run_runtime_expert`'s wakeup message changed to `"delegation_id": self._delegation_id or self._task_id` (preferring probe_id), plus `"task_id": self._task_id` (so the supervisor's `_pending_delegations` can match by task_id). Aligned with the `_notify_return_target` path.

**Verification:** `test_runtime_expert_thinking_result_uses_probe_id` (asserts delegation_id=probe_id and task_id present); 145+ related pass.

**Lesson:** When one message field has **multiple production paths**, its semantics must be uniform — fixing one site mandates a repo-wide sweep of every sender (grep `action: thinking_result` / `"delegation_id"`), otherwise some paths (RuntimeExpert here) fail silently, with no error raised — surfacing only as "can't find it" at query_delegation time.

---

## 63. start_runner's delegation-node record overwrites delegate dispatch's record — delegation role name replaced by identity.role (backend)

**Symptom:** End-to-end testing revealed that at `delegate_task` dispatch, `_record_delegation_chain` recorded the delegation under the **role display name** (e.g., "代码实现专家"); subsequently the manager consuming probe_started → `start_runner` **overwrote that same probe_id's delegation node** with `identity.role` (e.g., `code_writer`) → the role in the delegation chain became the English identifier and `query_delegation` lost the display name.

**Root cause:** A single delegation node had two recording sites (delegate dispatch + probe activation); the latter's `write_delegation` unconditionally clobbered the former (both keyed by the same probe_id).

**Fix:** Before recording the delegation node, `start_runner` checks whether `bb.delegations` already contains that probe_id — if present, it only fills in `target_model_id` (via `update_delegation_progress`) without touching role/task/caller; `write_delegation` applies only to genuinely missing cases like orchestrator direct starts.

**Verification:** `tests/integration/test_thinking_e2e.py::test_delegation_chain_full_flow` asserts the delegation chain `role=代码实现专家, caller=large_primary_001, parent=probe_user_input, status=replied`; 79+ related pass.

**Lesson:** When one business entity (a delegation node) has two writers, "who wins / who supplements" must be spelled out, or the later writer silently clobbers the earlier one.

---

## 64. The resume-thinking "continue from interruption" instruction accumulates across repeated timeout retries (backend)

**Symptom:** End-to-end review found that on `think_once` timeout retries (MAX_THINK_RETRIES), every resume appended a "continue from interruption" system instruction to messages; once the checkpoint refreshed, another resume inserted another copy → context bloat + duplicated instructions.

**Root cause:** The resume branch performed an unconditional `messages.append(...)`, never checking whether the checkpoint already contained the instruction.

**Fix:** The resume branch first scans the checkpoint messages for the existing marker (`has_resume_marker`) and skips insertion when present.

**Verification:** 503+ related pass.

**Lesson:** Idempotency — before injecting a marker instruction, any "checkpoint restore/resume" logic must check whether it's already been injected; repeatedly resuming the same checkpoint must not append repeatedly.

---

## 67. New control tool read_context wrongly added to all tiers — expert privilege overreach (backend/testing)

**Symptom:** `test_get_control_tools_expert` failed: expert's control tools gained `read_context`.

**Root cause:** Inside `tool_permission_controller.get_control_tools`, `READ_CONTEXT_TOOL` had been put into the base tools list `tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL, READ_CONTEXT_TOOL]` — **shared across all tiers**. But `read_context` reads blackboard memories/delegation context; expert has no memory-reading needs and shouldn't see it.

**Fix:** Moved `READ_CONTEXT_TOOL` into the `if delegation_available and tier in ("large", "supervisor")` delegation group (peer to query/resume_delegation).

**Verification:** `test_get_control_tools_expert` restored; 144 tool-related passed.

**Lesson:** New control tools must be **explicitly classified** by tier permissions (base/delegation/large-model-only), never mindlessly stuffed into the shared base list; expert exposes only the minimal necessary tools.

---

## 70. Model call failure: 'NoneType' object is not iterable —— SSE stream delta.tool_calls is null (backend)

**Symptom:** During the tool loop the model call reported `[模型调用失败: 'NoneType' object is not iterable]`, interrupting thinking.

**Root cause:** `large_model_client._parse_openai_stream` iterated tool-call deltas via `for tc_delta in delta.get("tool_calls", [])`. When DeepSeek reasoning mode streams, **during the thinking phase the `delta.tool_calls` field exists but holds `null`** — `dict.get(key, [])` returns **None** when the key exists but its value is None (not the default `[]`), so `for ... in None` → `'NoneType' object is not iterable`.

**Fix:** `for tc_delta in (delta.get("tool_calls") or [])` — using `or []` guards both "key missing" and "value is null".

**Verification:** Added `test_openai_stream_tool_calls_null_safe` (tool_calls:null interleaved with text + subsequent tool calls) + `test_openai_stream_tool_calls_key_missing`; large_model_stream 9 passed + 274 related passed.

**Lesson:** ① `dict.get(key, default)` returns the default only when the **key is missing**; when the **key exists but the value is None**, it returns None — for nullable fields use `get(key) or default`; ② SSE stream parsing must tolerate provider field emptiness (null/absent); DeepSeek reasoning's tool_calls being null during the thinking phase is normal; ③ when triaging "X is not iterable", search for `for x in dict.get(...)` patterns first.

---

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

---

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

---

## 80. "Thinking Ns" timer counts from session connection, not the current task (backend api_stream)

**Symptom:** The frontend "Thinking Xs" counter (`ThinkingStatusPanel`/`ThinkingIndicator`) ran persistently high; asking consecutive questions within the same session, the next round's seconds accumulated from the previous round instead of reflecting the current round's true elapsed time.

**Root cause:** In `modules/thinking/api_stream.py`, `started_at` is set only once at `start(session_id)` (WS connection established / session created); every user-message round runs `think()`, which never resets `started_at`. The WS `status` message's `elapsed_s = int(now - started_at)` therefore counts from "session connection moment" rather than "this round's think start".

**Fix:** Inside `think()`, after `_set_processing(session_id, True)` and before scheduling begins, reset `self.sessions[session_id]["started_at"] = time.time()` under lock — each task round restarts the timer. The frontend `chat.elapsed` purely consumes the backend-pushed `elapsed_s` with no local timer, so no change needed.

**Verification:** Added `test_api_stream_think_ext.py::test_think_resets_started_at_per_round` (old started_at gets reset; two consecutive rounds refresh their timing origins independently); all 183 api_stream tests pass.

**Lesson:** "Session-lifetime" timing and "task/round-lifetime" timing are two different semantics sharing no timestamp. Elapsed-time fields in WS status pushes must anchor at "this round's processing start", otherwise the UI's elapsed-time display distorts.

---

## 83. After disabling a supervisor/expert on the orchestration page, the System Prompt still stitched in their context (capability tables didn't filter enabled state)

**Symptom:** After disabling some supervisor/expert (enable toggle) on the orchestration page, the system prompt preview and runtime prompts still contained that role's entry within the "delegable supervisors/experts" tables; and the disabled role could still be scheduled/delegated into starting.

**Root cause:** `config/prompts/composer.py::_merged_roles()` (the capability-table data source) merged roles.yaml + orchestration-page custom agents **without querying `settings.get_agent_active()`**, making the off-state entirely ineffective for system prompt assembly. Enable/disable state lives in personas.yaml's `agent_active` (default True), but the capability-table path never read it. Runtime scheduling's `start_runner` already checks `get_agent_active() is False → refuse start` (model_runner.py:3044), but the prompt-side gap let "disabled roles still appear in the delegable list", misleading the model into treating them as delegable.

**Fix:** `config/prompts/composer.py::_merged_roles()` uniformly filters out active=false roles before merging (both roles.yaml built-ins and custom agents filtered via `settings.get_agent_active(key)`, falling back to all-True if the method is missing, preserving compatibility with tests/external callers). The identity table `get_identities()` deliberately does **not** filter (keeping scheduling resolvable; delegation gets refused by `start_runner`'s active check, avoiding "unknown identity template" false alarms). `settings.set_agent_active`/`deactivate_same_tier` additionally call `_invalidate_identity_cache()` so enable/disable changes reflect immediately. Additionally, `delegation_port.delegate` now checks `get_agent_active(identity_key)` before delegating, refusing disabled roles outright (consistent with capability-table filtering, avoiding the "prompt doesn't show it yet delegation accepts and start fails" disconnect).

**Verification:** Added `test_build_tables_filter_disabled_agents` (disabled code_supervisor/data_analyzer excluded from capability tables) and `test_delegate_disabled_agent_rejected` (delegating a disabled role is refused); `test_build_supervisor_table_merges_custom_agents` and delegation tests mock `get_agent_active` with fixed values, isolating real yaml state; 197 composer/identity/delegation_port/settings related tests + 360 model_runner/orchestrator tests pass.

**Lesson:** An "enable/disable switch" must thread through every path consuming role collections: the prompt capability table (which delegables the model sees) and the scheduling entry (whether start truly proceeds) each govern one segment; omitting either yields "prompt-visible yet start-refused" or "disabled yet still present in context" disconnects. After config changes (active flips), invalidate the identity cache, otherwise changes don't take effect.

---

## 84. After switching the commander on the orchestration page, startup still targeted orchestrator and was refused by the toggle (orchestrator hardcoded identity)

**Symptom:** After enabling a custom commander (tier=large, e.g., `123`) on the orchestration page, sending a message that triggered thinking reported `[ModelRunnerManager] orchestrator 已被禁用（编排页激活开关），拒绝启动`, and the commander couldn't run. The orchestration page's toggle logic enforces "only one active in the commander layer" (`deactivate_same_tier`: enabling `123` sets `orchestrator` to false), yet startup always used the `orchestrator` identity.

**Root cause:** `multi_model_orchestrator`, when activating the large model, **hardcoded** `identity_key: "orchestrator"` (probe_started message) and `match_skill(role="orchestrator")`, never reading the orchestration page's selected active large role. Once §83 gave `start_runner` the `get_agent_active() is False → refuse start` check, this hardcoding got "amplified": the orchestrator still requested orchestrator, but orchestrator had been deactivated by same-tier exclusivity → start refused, even though the custom commander `123` was active. Chat-side (chat_light) persona selection has long supported "orchestrator first, else the activated custom large" (prompt_composer); the orchestrator hadn't synced.

**Fix:** `multi_model_orchestrator` gains module-level `resolve_active_large_role()`: returns `orchestrator` when orchestrator is active, otherwise the first custom agent with `tier=large` and `get_agent_active=True`, falling back to orchestrator on exceptions. Both probe_started's `identity_key` and `match_skill`'s `role` now use it.

**Verification:** Added `tests/unit/test_resolve_active_large_role.py` with 6 cases (orchestrator priority / custom-large follows / multiple customs pick first active / supervisor excluded / exception fallback); 78 orchestrator tests pass. In the real environment `~/.cortex/personas.yaml` (orchestrator=false, 123=true), `resolve_active_large_role()` returns `123`.

**Lesson:** Hardcoded default role names (orchestrator) encode an "identity single-point" assumption; once the orchestration page allows same-tier exclusive multi-select (a unique active commander), any path bypassing that abstraction and hardwiring the role key decouples from toggle state. Start-type paths should funnel through one shared "resolve currently-active role" helper instead of scattered hardcodes; §79 (delegation ROLE_TO_IDENTITY hardcode), §83 (capability table not filtering active) belong to the same "role-collection source mismatch" family.

---

## 92. _session_dialog_history concurrent overwrite — two requests on one session clobber each other (backend)

**Symptom:** Sending two messages rapidly in the same session: the second's dialog history overwrites the first's, so the first message's frontend expansion panel displays wrong history.

**Root cause:** `multi_model_orchestrator.py:32-33`'s `_session_dialog_history` is a module-level dict with no locking. Two concurrent requests write different content for the same session_id.

**Scope:** Expansion-panel contents scramble during concurrent conversations.

**Recommended fix:** Switch to per-session locks or per-request independent storage.

---

## 93. TurnContext fragments overwritten by source — same-source different-content lost (backend)

**Symptom:** Context fragments produced by two different modules (e.g., memory retrieval and causal analysis) used the same `source` name (e.g., `"memory"`), the latter overwriting the former.

**Root cause:** `pool.py:97`: `self.fragments[fragment.source] = fragment`, with no dedup/merge logic.

**Scope:** Blackboard-shared memories may lose some sources' context.

**Recommended fix:** Switch to a list (same-source fragments coexist) or use composite `source:sub_key` keys.

---

## 95. Thinking timeout charged the whole round — tool execution ate the budget, UI silent for minutes (backend)

**Symptom:** An open-ended task (e.g. "upgrade your own codebase") produced no reply for 10+ minutes; the UI showed only thinking steps. Logs contained two `single thinking timeout (>300.0s)` entries, then the run was stopped and the session had **0 assistant messages**.

**Root cause:**
1. `ContinuousThinker.think_once` / `ModelRunner._generate` wrapped the **entire round** (model call + tool execution + multi-turn tool loop) in a fixed 300s `pausable_wait_for(..., timeout=THINK_TIMEOUT)`. A single `exec_command` running tests takes 2 minutes, so two tool-loop rounds always blew the budget.
2. No stall timeout for the model HTTP call — a request that never returns the first token just waited for 300/600s.
3. Tool execution had no timeout at all: `asyncio.to_thread(mcp.execute, ...)` was unbounded.
4. Race when reading partial output on cancel: `remove_runner_manager()` popped the manager first, then `api_stream` looked up `_runner_managers` for the blackboard → miss, so the partial reply was neither persisted nor shown.

**Fix:**
1. New `utils/activity.py::ActivityClock`: every AI output (stream token / model response / tool call) calls `touch()`, and `pausable_wait_for(..., activity=clock)` becomes an **idle timeout** (activity resets the countdown).
2. `clock.pause()/resume()` around tool execution so tool time is excluded from the AI idle budget; approval waits (`Suspension`) freeze the same timer.
3. Independent per-tool timeout: `TOOL_CALL_TIMEOUT` (default 600s); on timeout only that tool call ends and the error is returned to the model.
4. Overall wall-clock cap per round kept as `MODEL_THINK_MAX_WALL` (default 3600s, 0 = unlimited) to stop infinite tool loops.
5. Visible retry notice in the blackboard while retrying, and a visible `result_summary` once retries are exhausted — no more "process but no reply".
6. `api_stream` cancel path now falls back to the latest `blackboard_snapshots` row when the manager is already gone, recovering the `[thinking interrupted ...]` partial text.

---

## 97. plan mode blocked read-only delegation too — empty keyword list still returned a block (backend)

**Symptom:** In plan mode, the large model called `delegate_task(role=代码实现专家, task=计算 1+1)`. The log only showed `[直通委托] 失败: ... error=plan 模式拦截`, **not** `[ModelRunner] plan 模式拦截写操作委派`; the delegation was never sent and the chain was never recorded. The runner then finished with "large model produced no result_summary" and the user got nothing.

**Root cause:** In `_generate_with_tools` (`modules/thinking/core/model_runner.py`) the plan-mode check was written as:
```python
if exec_mode == "plan":
    matched = [kw for kw in DELEGATE_WRITE_KEYWORDS if kw in task_lower]
    if matched:
        logger.warning(...)              # only logs when a keyword matches
    if self._thinker:
        self._thinker.record_delegation(... error="plan 模式拦截")
    return "[安全门控拦截] ..."          # ← return sits outside `if matched`
```
The `return` and the "record block" were indented under `if exec_mode == "plan"` instead of `if matched` → **any delegation in plan mode (including pure read-only/compute tasks) was treated as a write and blocked**, and `', '.join(matched)` printed an empty keyword list.

**Fix:** move both the record call and the `return` into the `if matched:` branch. plan mode still blocks delegations containing write keywords (`写入`/`部署`/`git push` etc.); read-only tasks are dispatched normally.

**Tests:**
- New `tests/unit/test_model_runner_ext2.py::test_generate_with_tools_delegate_plan_allows_readonly_task`: plan mode + `task=计算 1+1` → no `安全门控拦截`, `adapter.delegate` awaited once, state becomes `waiting_delegation`.
- Existing `test_generate_with_tools_delegate_plan_blocked` (task contains a write keyword) still asserts a block.
- The 2 `tests/integration/test_thinking_e2e.py` cases (`test_delegation_chain_full_flow`, `test_query_delegation_e2e`) already failed at HEAD (a concurrent fixture pinned `EXECUTION_MODE` to plan and exposed this bug); all 5 cases pass after the fix.

**Regression:** `test_model_runner_ext2.py` 178 cases and `tests/integration` 432 cases all passing.
