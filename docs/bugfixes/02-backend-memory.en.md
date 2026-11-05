# Error Log & Fixes · Backend · Memory / Causality / Mental Activity / Perception

[← Back to index](./README.en.md) | [简体中文](./02-backend-memory.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 15 sections: §2、§9、§11、§16、§40、§41、§42、§43、§49、§50、§51、§65、§105、§106、§107.

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

---

## 16. Blackboard shared memory: role alias mismatch (backend)

> Archived from section 10 of `docs/MEMORY_INJECTION.md`

**Symptom:** In multi-model collaboration, the orchestrator can't see blackboard-shared sections like `【协作上下文】`, `【当前委托状态】`, `【当前任务进度记事本】`, `【历史输出】` — expert output doesn't fully reach the orchestrator; potential collaboration breakage.

**Root cause:** `TurnContext.view(role)` filters by `role in target_roles`, but `continuous_thinker._build_prompt` sets `role = getattr(self, '_role', 'orchestrator')`, always `'orchestrator'`; those fragments declare `target_roles=("large",)` → the large model (orchestrator) doesn't match.

**Fix** (`modules/thinking/context/pool.py` `view()`): treat `"large"` and `"orchestrator"` as **two spellings of the same role (orchestrator)** — when the viewing role is either, fragments containing either alias are visible; supervisor/expert still match exactly.

**Verification:** `view('orchestrator')` returns all fragments; `view('supervisor')` returns system instructions + historical memory only; `view('expert')` returns historical memory only.

**Similar-issue sweep:** Search globally for role-string constants (`"large"`/`"orchestrator"`/`"supervisor"` etc.) hardcoded under multiple spellings without normalization — multiple names for one role in config easily break matching.

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

---

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

---

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

## 105. Psychological-activity box disappears after switching sessions — mental events not persisted (frontend/backend)

> **Numbering fix:** the original document reused §41 here (duplicate of an earlier §41); this section is now §105.

**Symptom:** After switching session windows on the frontend, the "psychological activity" box disappears (that session's mental-activity records are invisible).

**Root cause:** Psychological activity (`msg_type='mental'`, conscience inner monologue) was previously **pushed live over WS only, never persisted** (`push_content(persist=False)`, designed to avoid polluting AI context) — after a session switch, history loading contained no mental messages → the box was empty.

**Fix:**
1. Backend: `multi_model_orchestrator` persists psychological activity as `role="mental"` (`persist=True`); `api_stream` filters out `mental` when restoring context (like thought, keeping model input unpolluted)
2. Frontend: `chat.js` renders `role="mental"` → `kind:'mental'` psychological-activity box when loading session history (consistent with live events)

**Verification:** Backend 212 passed + frontend chat store 27 passed; mental persisted as `role="mental"` and filtered on context restore.

**Note:** In `chat_gateway`, conscience's segment-by-segment mentals (streaming-token intermediate states) remain unpersisted — the main psychological activity (the orchestrator-integrated inner monologue) is now persisted and visible.

---

## 106. Environment perception not injected into the LLM context — dd1ee8b refactor regression (backend)

> **Numbering fix:** the original document reused §42 here (duplicate of an earlier §42); this section is now §106.

**Symptom:** Under agent mode (model_runner) and pure chat (chat_light), the model's system prompt lacked 【环境感知】 — the perception system ran (window/screen/OCR) but the model saw no environment.

**Root cause:** The `dd1ee8b` (2026-06-27) refactor of the "context and prompt systems" **removed the orchestrator's `get_context_summary()` perception-injection call**; the post-refactor mechanism (`PerceptionSource`→`PerceptionPool`) wired into only desktop pet / continuous thinking (core) / proactive talk, **not into `model_runner` (agent) nor `chat_light` (pure chat)** — perception injection thereby broken for about 2 months.

**Fix:**
1. At the `model_runner._build_system_prompt_for_mode` call site: `PerceptionSource().collect()` → append the 【环境感知】 block
2. `chat_light/continuous_thinker`: likewise append 【环境感知】 after the psychological-activity injection
3. Both wrapped in try/except tolerance (uninitialized perception/exceptions never break normal conversation)

**Verification:** Both injections effective (mock PerceptionSource → system_prompt contains environment perception); added `test_perception_injection.py` (4 cases); 266 related passed.

**Lesson:** After large refactors (context/prompt systems), regression must cover the complete "data→prompt" chain; problems like perception where "the collection side works but the injection side is severed" are hard to catch in code review — injection-chain tests are needed.

---

## 107. File-perception feature effectively dead — collection-to-consumer pipeline severed (backend)

> **Numbering fix:** the original document reused §43 here (duplicate of an earlier §43); this section is now §107.

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

---

## 65. Blackboard final_response set but never persisted — final reply lost after restart (backend)

**Symptom:** While verifying blackboard snapshots end to end, discovered that `blackboard.persist()` was invoked only during checkpoint saving/delegation-chain recording; `final_response` as terminal state, once set via `set_final_response`, was never persisted → the DB snapshot's `final_response` stayed None forever.

**Root cause:** Persistence-timing coverage gap — only "process states" (checkpoints/delegations) covered; "terminal states" (the final reply) omitted.

**Fix:** `CognitiveBlackboard.set_final_response` calls `self.persist()` at the end (failures non-blocking), guaranteeing the final reply lands in the DB and remains recoverable.

**Verification:** `test_delegation_chain_full_flow` asserts `state["final_response"] == blackboard.final_response`; 503+ related pass.

**Lesson:** Blackboard persistence must cover "terminal-state" fields (final_response etc.), not merely process states; methods writing terminal state should themselves trigger persistence.
