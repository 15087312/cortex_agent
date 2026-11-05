# Error Log & Fixes · Backend · Config Persistence & Security

[← Back to index](./README.en.md) | [简体中文](./03-backend-config-security.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 14 sections: §13、§18、§28、§29、§56、§77、§78、§86、§87、§88、§89、§90、§96、§102.

---
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

---

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

---

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

## 77. Redundant manual API-Key input on the authorization settings page (frontend, security-exposure anti-pattern)

**Symptom:** The "Authorization Settings" tab had a manual "API Key" input box (`X-API-Key`, governed by backend `SIMPLE_API_KEY`) able to save/clear the key.

**Root cause:** Redundant design. On startup the frontend's `autoDetectApiKey()` already auto-fetches the key from `/config/api-key` (dev/test return plaintext directly; production returns plaintext only to loopback clients, everyone else just gets a `configured` status). The manual input mattered only for the edge case "production + non-loopback client" and was useless everywhere else; worse, it exposed a security credential as a frontend entry point — a security anti-pattern.

**Fix:** Removed the authorization tab's API-key input section (`keyInput`/`saveKey`/`clearKey`), keeping the `autoDetectApiKey`/header-carrying logic. The authorization settings tab retains only the runtime config table.

**Verification:** Settings.spec.js dropped 3 tests dependent on that UI and rewrote 1 tab test; 50 frontend tests pass; after rerunning `npm run build`, dist contains no `key-input`/`输入 X-API-Key` residue.

**Lesson:** Any configuration already covered by "auto-detect/auto-recovery" should not keep a manual frontend entry — it is both redundant and an enlarged attack surface. Same-class checks: see §78, §79.

---

## 78. Authorization settings tab's runtime config table displays model API Keys in plaintext (frontend, §77 class)

**Symptom:** The authorization tab's "Runtime Configuration" table rendered every modifiable entry of `configStore.config` line by line via `String(v)`, including plaintext secrets such as `LARGE_MODEL_API_KEY`/`MEDIUM_MODEL_API_KEY`/`SMALL_MODEL_API_KEY`/`PERCEPTION_VOICE_API_KEY`/`OUTPUT_TTS_API_KEY`/`VISION_API_KEY`.

**Root cause:** `get_config` (api/main.py:764) returns all keys within `_MODIFIABLE_CONFIG_KEYS` (model/voice/vision secrets included), and the frontend runtime table prints every non-object value as plaintext via `String(v)`. These secrets each have dedicated password-style config sections (main model config etc.), so displaying them in a running table is redundant exposure (shoulder-surfing/log-leak risk).

**Status:** Confirmed same-class issue; fix pending (frontend will mask secret fields as `••••`, editing via password inputs).

**Lesson:** The allowed-to-display config list (`_MODIFIABLE_FIELDS`) itself contains secret fields; any "full-config render" surface must mask secret values by field name (KEY/TOKEN/SECRET/PASSWORD patterns).

---

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

---

## 87. personas.yaml concurrent writes lose updates — no process-level lock (backend)

**Symptom:** Two concurrent API requests (e.g., updating persona and tools simultaneously) modify personas.yaml; the second write clobbers the first's changes. In the worst case this reproduces §86's "data loss after restart".

**Root cause:** All `set_*` methods (`set_persona`/`set_system_override`/`set_role_tools`/`set_model_params`/`set_agent_active`/`set_custom_agent` etc.) follow read-modify-write (`_load_personas_yaml` → modify → `_save_personas_yaml`) but **lack a process-level lock**. Multiple concurrent requests read the same file version, each modifies different keys, then write back — the later write clobbers the earlier. Atomic `os.replace` prevents half-writes, not lost updates.

**Scope:** 14+ `set_*` methods share the same `_save_personas_yaml`; any two concurrent calls lose data.

**Recommended fix:** Add a file-level lock (e.g., `fcntl.flock` or `threading.Lock`), or coalesce into single writes (multiple `set_*` calls within one request merged into one read-modify-write).

**Similar-issue sweep:** `save_user_config` (settings.json) suffers the exact same problem.

---

## 88. settings.json concurrent writes lose updates (backend)

**Symptom:** The Settings page's "Save model form" iterates multiple keys PUTting one by one; each PUT triggers `save_user_config`'s read-modify-write of settings.json. With two concurrently operating browser tabs or rapid operations, the second write clobbers the first.

**Root cause:** `save_user_config` (`config/settings.py:902`) reads settings.json → adds the key → writes back, with no lock. Atomic `os.replace` prevents half-writes but not lost updates. `_load_user_config` (line 884) has no retry logic (unlike `_load_personas_yaml`'s retry), silently falling back to .env defaults when JSON corrupts.

**Scope:** Every config item modified through the Settings page (model params, API keys, switches etc.).

**Recommended fix:** Add a file-level lock or switch to optimistic locking (verify the file hasn't been modified before writing).

---

## 89. delete_custom_agent cascades four independent read-modify-writes, losing data (backend)

**Symptom:** When deleting a custom agent, `delete_custom_agent` removes the agent and then makes **four independent** `set_persona`/`set_system_override`/`set_role_tools`/`set_model_params` calls to clean up that agent's configs. If any intermediate write gets clobbered by a concurrent request, orphaned data remains; a process crash leaves partial cleanup incomplete.

**Root cause:** `api/main.py:950-962`: five independent read-modify-writes, with other requests free to interleave modifications between each.

**Scope:** Config inconsistency after deleting a custom agent (residual persona/override/tools/params).

**Recommended fix:** Coalesce into a single read-modify-write (one read, delete all keys at once, one write-back).

---

## 90. _save_personas_yaml swallows exceptions — callers assume success (backend)

**Symptom:** `_save_personas_yaml` only print-warns on write failure, raising nothing. All 14+ `set_*` callers assume success; APIs return success toasts. Users see "Saved" although nothing landed on disk; a restart restores old values.

**Root cause:** `config/settings.py:589-604`: `except Exception` catches everything (disk full, permission errors, file-lock conflicts), printing only to stderr. API endpoints return `{"success": True}`.

**Scope:** All personas.yaml write operations, silently lost.

**Recommended fix:** `_save_personas_yaml` should return success/failure (mirroring `save_user_config`'s bool pattern); callers decide whether to inform the user based on the return value.

---

## 96. Cross-process config write race — a thread lock cannot protect multiple processes (backend)

**Symptom:** `~/.cortex/personas.yaml` occasionally loses content (custom personas revert to old values or become empty a while after saving); some `~/.cortex/settings.json` fields return to defaults after a restart. Repeated operations inside a single process never reproduce it.

**Root cause:**
1. `config/settings.py` only had **in-process** locks: `_PERSONAS_YAML_LOCK = threading.Lock()` and `_SETTINGS_JSON_LOCK = threading.Lock()`. `threading.Lock` only excludes threads in the same process — it has **no cross-process semantics**.
2. `_modify_personas_yaml()` (~line 770) and `save_user_config()` (~line 1132) are both read-modify-write: read file → mutate → atomic replace. When two **processes** enter at the same time, their in-process locks cannot see each other; the later writer overwrites the earlier one with its own stale snapshot → lost update (the §86/§87/§88 symptoms).
3. `frontend/main.py:535` starts the backend via `subprocess.Popen` + uvicorn, so the frontend and backend — or multiple backend instances — may **write the same `~/.cortex` config concurrently**, a real cross-process race.

**Fix:** reuse the project's existing cross-process lock `utils/faiss_lock.py::faiss_file_lock` (in-process reentrant RLock + cross-process `fcntl.flock`; platforms without fcntl automatically degrade to a thread lock; the lock file is the data path + `.lock`, and never opens/truncates the data file itself).
1. `_modify_personas_yaml`: **nest** `with faiss_file_lock(self._personas_yaml_path):` inside `with _PERSONAS_YAML_LOCK:`. The tool appends `.lock` automatically. Keep the thread lock: outer thread lock, inner file lock.
2. `save_user_config`: nest `faiss_file_lock(str(self._USER_CONFIG_PATH))` inside `_SETTINGS_JSON_LOCK` the same way.
3. Import lazily **inside the function** (`from utils.faiss_lock import faiss_file_lock`) to avoid module-level circular imports; on import failure, `try/except` degrades to `contextlib.nullcontext()` (thread lock only) and logs at `logger.debug` — **a missing lock must never abort the write or crash**.
4. Existing behavior and signatures of `_load_personas_yaml` / `_save_personas_yaml` are unchanged.

**Tests:** new `tests/unit/test_settings_file_lock.py` (5 cases, all passing):
- the lock file exists during `_modify_personas_yaml` and is released afterwards (`_depth == 0`, `_fd is None`);
- re-entering the same path lock inside the mutator does not deadlock;
- when importing `utils.faiss_lock` fails it degrades to the thread lock, the write still succeeds and no lock file is created;
- the same hold/degrade checks for `save_user_config`.
All tests use `tmp_path` + monkeypatch to redirect `_personas_yaml_path` / `_USER_CONFIG_PATH` and never touch the real `~/.cortex`.

**Regression:** `tests/unit/test_settings_ext.py` + `test_faiss_lock.py` + the new cases, 109 cases in total, all passing.

---

## 102. `settings.json` parse failure silently became `{}` — one save wiped all config (backend, §88 leftover)

**Symptom:** After the config file was hand-edited badly (or a rare mid-write power loss left incomplete JSON), saving any single item from the Settings page made **every other persisted key (model params, API keys, toggles) disappear** from `settings.json`, leaving only the ones from that save.

**Root cause:** `save_user_config` read the file with `except Exception: data = {}`, then wrote those keys back and `os.replace`d under the same lock — the lock fixed "concurrent overwrite" (§96) but not "treat a read failure as an empty file", a **single-request** whole-file wipe path. `_load_personas_yaml` already had the "unreadable → raise RuntimeError, refuse to write from an empty dict" guard (added while investigating §86); `save_user_config` was missing it.

**Fix:** raise `RuntimeError` (message includes the file path) when parsing fails or the content is not a JSON object, **leaving the original file byte-for-byte untouched**; the outer `except` prints and re-raises, and the API layer already returns a failure (`tests/unit/test_api_main.py` covers it). A missing file (first run) still writes normally from an empty config.

**Tests:** `tests/unit/test_settings_ext.py::test_save_user_config_corrupt_existing` now asserts the error and that the file content is preserved (it previously asserted the old "silently write back" behavior); new `test_save_user_config_non_object_json` (valid but non-object JSON such as `[1, 2]` is rejected too).

**Regression:** `tests/unit/test_settings_ext.py` + `test_settings_file_lock.py` 104 cases.


### 102.1 Failed persist still left memory/env updated (follow-up)

**Symptom:** When saving a config while the disk is full, permissions are wrong, or `~/.cortex` is busy, the API returned 500 but the Settings page showed the value as applied; after a restart it was old again — the user could not tell whether it had been saved.

**Root cause:** `PUT /config/{key}` called `object.__setattr__(settings, ...)` first (and wrote `os.environ` for CORTEX_MODE), then `save_user_config()`. After §96 changed failures to raise, a failed write only turned the response into 500 — **memory already held the new value**. `save_user_config`'s `-> bool` return had also been constant `True` since §96/§102 (it never returned False anymore), making the `if not ...` branch dead code.

**Fix:**
1. `save_user_config(keys, values=None)` gains a `values` parameter: persist the pending new value instead of reading the current value from `self`; the return type becomes `None` (failures always raise, never return a boolean).
2. `PUT /config/{key}` now **persists first, applies second**: `save_user_config([actual], values={actual: validated})` must succeed before `object.__setattr__` / writing `CORTEX_MODE`; a failed write returns 500 and memory/env keep their old values.
3. Delete the always-false `if not save_user_config(...)` warning branch.
4. Model rebuild and system-level toggles still run only after a successful persist.

**Tests:**
- `test_update_config_persists_before_apply`: `save_user_config` receives the new value in `values`, and memory is still old at call time.
- `test_update_config_internal_error_500` / `test_update_config_cortex_mode_persist_failure_keeps_env`: persist raises → 500, memory value and `CORTEX_MODE` unchanged.
- `test_save_user_config_values_override_without_mutating_memory`: `values=` writes the file without touching memory.
- The old "persist failure only warns, still 200" case is removed (that behavior no longer exists).
