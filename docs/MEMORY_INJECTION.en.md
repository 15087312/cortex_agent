# Memory System & Injection Pipeline — Deep Dive

**Language**: [English](./MEMORY_INJECTION.en.md) | [简体中文](./MEMORY_INJECTION.md)

> This document traces the full pipeline of "event memory / session memory from creation
> to injection into LLM prompts" end to end, for quick troubleshooting of memory issues.

---

## 1. Data Model & Storage

**`MemoryEvent`** (`modules/memory/event_store.py`) — the minimal unit of memory:

| Field | Meaning |
|------|------|
| `id` | Unique ID |
| `fact` | What happened (main content) |
| `thought` | Thinking/reflection |
| `lesson` | Reusable lesson learned |
| `keywords` | Keywords |
| `importance` | Importance 0~1 |
| `type` | `emotion`/`thought`/`fact`/`strategy` (determines forgetting-curve rate) |
| `time` | Creation time (ISO) |
| **`session_id`** | **Associated session** (key field for session memory) |
| `owner_id` | Owning model (large/supervisor/expert) |
| `embedding` | Vector (stored in FAISS, not in SQLite) |

**Storage**: `EventStore` (`modules/memory/event_store.py`) — SQLite `events` table + FAISS vector index.

---

## 2. Event Creation (Write Path)

Events are extracted by `EventReducer` **after a session task finishes**, via two paths:

### Agent path
```
api_stream._post_task_extraction
  └─ asyncio.sleep(30s)  # delayed extraction after task completion (fire-and-forget)
     └─ EventReducer.reduce(session_id, conversation_text, owner_id)
        └─ every MemoryEvent gets ev.session_id = session_id   ← event_reducer.py:139
        └─ dedup by hash of first 60 chars of fact
        └─ store.save_event()  # auto vectorization + write SQLite + FAISS
```

### chatonly path
```
modules/thinking/chat_light/continuous_thinker._extract_memory (line 157)
  └─ condition: MEMORY_REDUCE_ENABLED
  └─ EventReducer.reduce(session_id, conversation_text)  # also tags session_id
```

---

## 3. Retrieval (Read Path)

**`EventRetrieval.retrieve(query, ...)`** (`modules/memory/event_retrieval.py`):

```
1. Vector semantic search    FAISS, top_k = max_results*3 (the only recall channel)
2. Causal-graph diffusion    candidate events likewise filtered by real cosine similarity
                             (no hardcoded pass score)
3. Merge & score:
   score = (0.60*semantic + 0.15*importance + 0.10*recency
            + 0.08*utility + 0.07*frequency) * content_bonus
   └─ semantic < MIN_SEMANTIC_SIMILARITY(0.30) → dropped
4. Absolute normalization → threshold (default 0.06) → sort → truncate to max_results
```

Other retrieval entry points:
- `retrieve_mixed(mix, ...)` — multi-topic weighted retrieval (memory focus `_memory_focus`)
- Explicit tools: `event_query` (`infra/tool_manager/tools/event_query.py`), `memory_match`/`memory_score`/`memory_batch_filter` (`memory_matcher.py`) — used when the model calls them proactively
- `depth_recall` — deep causal recall (`modules/memory/depth_recall.py`, triggered by `should_trigger_deep_recall`)

> ⚠ The keyword-search channel has been removed (`_keyword_search` / `_extract_keywords` deleted): every candidate
> (including those diffused out of the causal graph) must pass the real semantic cosine similarity ≥ 0.30 gate,
> preventing events that "hit on keywords but are semantically irrelevant" from being recalled.

---

## 4. Prompt Injection — Agent Path

### 4.1 Building the memory fragment
`continuous_thinker._build_prompt(initial_question, round_num)` (see core/continuous_thinker.py):

```
Creates TurnContext(pool)
├─ guidance       【系统指令】                priority 10
├─ notebook       【当前任务进度记事本】       priority 15
├─ history        【历史输出（不得重复）】      priority 20
├─ MEMORY         【历史记忆】                priority 30   ← this topic
├─ perception     【环境感知】                priority -
├─ blackboard     【协作上下文】              priority 50
├─ delegation     【当前委托状态】            priority 60
└─ skill_suggestion【技能建议】               priority -
```

### 4.2 Memory block logic (session-first)
```python
# 1. Session memory = 【对话历史】 (injected via system prompt); event memory gated by owner_id + gating:
#    _memory_focus always recalls / retrieve only when the session already has prior conversation turns
#    (core/continuous_thinker._build_prompt)
events = await retrieval.retrieve(query, owner_id=f"{tier}::{model_id}", threshold=0.10)

# 2. Supplement with event memory
events = session_events
if self._memory_focus:            # explicit focus: always recall
    events += retrieve_mixed(mix=self._memory_focus)
elif session_events:              # has session memory → supplement with globally relevant events
    events += retrieve(query=initial_question)
# no session memory and no focus → events is empty → nothing injected

# 3. Formatting annotations
【当前会话记忆】     ← session events (no dates)
【曾经发生的事】     ← global events (each tagged with date YYYY-MM-DD)
```

### 4.3 Into the LLM
```
composer.build(pool, role, tier, question)
  └─ pool.view(role)  →  "【历史记忆】\n<memory text>\n\n【当前任务】\n<question>"
     (TurnContext.view sorts by priority, filters by target_roles;
       target_roles of "历史记忆" = (orchestrator, supervisor, expert))

model_runner._generate(prompt)
  └─ messages = [
        ChatMessage("system", composer.build_system(...) + time context),   # identity/rules/tools
        ChatMessage("user",  <turn context containing 【历史记忆】 + 【当前任务】>),  # memory goes here
     ]
  └─ into tool loop → LLM
```

---

## 5. Prompt Injection — chatonly Path

`modules/thinking/chat_light/continuous_thinker.think` (see chat_light/continuous_thinker.py):

```
1. memory_context = _recall_memories(user_message, session_id)
      └─ first checks whether the session already has prior conversation turns
         (conversation before the current message on the blackboard)
      └─ no prior conversation → return "" (no event memory injected)
      └─ has prior conversation → deep recall + shallow 【曾经发生的事】
         (with dates + "prioritize the current session" note)
2. context_messages = _slicer.slice(history, memory_context)
      └─ memory as system message: "以下是从历史记忆中检索到的相关信息：..."
      └─ sliding window + token budget trimming (memory counts toward budget first)
3. system_prompt = _composer.build_system(memory_context)   # memory merged into system
4. _runner.run(messages=context_messages, system_prompt=system_prompt) → LLM
5. background _extract_memory(session_id)  → extract new events
```

---

## 6. How Session Memory Is Actually Injected (Conclusion)

**Definition**: Session memory = **conversation history** (the dialogue messages of the current session, shown as `【对话历史】` at the top of the system prompt),
**not** events filtered by `session_id`.

**Event memory = global memory**: events extracted by the global `EventStore`, injected only when the session already has prior conversation turns
(`【曾经发生的事】` + dates + note "not the current session, for reference only, prioritize the current session's conversation").

**Injection rules (current implementation)**:
1. Session memory (conversation history) is already injected via the system prompt — not duplicated
2. Event memory (global) is injected only when this session already has prior conversation turns
3. **New session (no prior conversation) → no event memory injected** (avoids pollution from irrelevant history)

**Key status quo**: Of the current library's 281 events, **266 have an empty `session_id`** (created via the old path);
only 15 carry a tag — but under the simplified model, the `session_id` tag is no longer used for injection decisions.
Injection depends only on "whether the session has prior conversation".
- New session, first turn: no prior conversation → no event memory injected
- After multiple turns: inject globally relevant event memory (【曾经发生的事】)
- Old untagged events: appear only as global supplements in "sessions that have session memory"

---

## 7. Full Call Chain Quick Reference

```
[agent]  chat_gateway /stream/ws → api_stream.websocket_chat
         → system.think(session, input)
           → orchestrator.process
             → model_runner (large)
               → _build_runner_prompt → continuous_thinker._build_prompt
                 → event memory retrieved by owner_id + gating (session memory = conversation history)    ← session memory
                 → EventRetrieval.retrieve(query)                 ← global supplement
                 → ContextFragment("memory") → TurnContext
                 → composer.build → user_prompt (contains 【历史记忆】)
               → _generate → LLM
         → _post_task_extraction → EventReducer.reduce(session_id) ← write events

[chatonly] chat_gateway /stream/ws → _chatonly_ws
         → modules/thinking/chat_light/continuous_thinker.think
           → _recall_memories(session_id)                          ← session-first
           → ContextSlicer.slice + composer.build_system → LLM
         → _extract_memory → EventReducer.reduce(session_id)       ← write events
```

---

## 8. Two Memory Systems (Clearly Distinguished)

| | **Conversation History** | **Event Memory** |
|---|-------------|-------------|
| Content | Dialogue messages of the current session | Facts/experiences/thoughts extracted from global history |
| Storage | Blackboard observations (`context_type=conversation_history`) + SQLite session_repo | `EventStore` (SQLite + FAISS), `MemoryEvent` |
| Injection point | **Very top** of the system prompt (`【对话历史】`, `_build_system_prompt_for_mode` (see core/model_runner.py)) | user message (`【历史记忆】`, memory block of `continuous_thinker._build_prompt`) |
| Scope | Current session only | Global; `session_id` distinguishes session memory |
| Written | Appended directly on each dialogue turn | Extracted by `EventReducer` after task completion |

The two are mutually independent and stored separately.

---

## 9. Simplified Model: Two Independent Memory Systems, Each Injected Independently

**Final model** (confirmed by user):
- **Session memory = conversation history**: dialogue messages of the current session, already injected as `【对话历史】` at the top of the system prompt (`_build_system_prompt_for_mode` (see core/model_runner.py)). **No separate injection of "session events" anymore**.
- **Event memory = global memory**: events extracted by the global `EventStore`, injected into the user message as `【曾经发生的事】` (with dates + note "not the current session, for reference only, prioritize the current session's conversation").
- **AI knows the current time**: `_build_time_context()` (model_runner.py:1334) injects `【当前时间】` + `【对话对象】` + `【上次对话】` into the system prompt.

**Event memory injection gate** (`continuous_thinker._build_prompt` + `modules/thinking/chat_light/continuous_thinker._recall_memories`):
```
if _memory_focus:               # explicit memory focus → always inject
    events = retrieve_mixed(...)
elif session has prior conversation:   # this session already produced thinking/dialogue
    events = retrieve(query, ...)   # global semantic retrieval
else:                           # new session (e.g. just sent "1") → no event memory injected
    events = []
```
- New session, first turn (no prior conversation) → no event memory injected, avoiding pollution from irrelevant history
- Multi-turn session (has prior conversation) → inject globally relevant event memory related to the current question (`【曾经发生的事】` + dates)

**Verified**: New session "1" → not injected; multi-turn session → globally relevant events injected; explicit focus → always injected.

---

## 10. Blackboard Shared-Memory Fix (Role Alias)

> Archived to section 16 of `docs/ERRORS_AND_FIXES.md`; this document keeps the original context.

**Problem**: `TurnContext.view(role)` filters by `role in target_roles`, while in `continuous_thinker._build_prompt`
`role = getattr(self, '_role', 'orchestrator')` is always **'orchestrator'**. But most fragments (collaboration context /
delegation status / notebook / historical outputs) have `target_roles=("large",)` → **the large model (commander-in-chief)
cannot see these fragments**, including:

- `【协作上下文】` (blackboard shared memory: external prompt builder output, incl. expert findings / MessageBus messages)
- `【当前委托状态】`
- `【当前任务进度记事本】`, `【历史输出】`

→ Outputs of multi-model collaboration failed to flow fully back to the commander-in-chief (potential collaboration breakage).

**Fix** (`modules/thinking/context/pool.py` `view()`):
- Treat `"large"` and `"orchestrator"` as **two spellings of the same role (commander-in-chief)**
- When the viewing role is either one, it can see fragments whose `target_roles` contain either alias
- supervisor / expert still match exactly; behavior unchanged

**Verified**: `view('orchestrator')` now returns all fragments (system instructions/notebook/historical outputs/historical memory/**collaboration context**/delegation status);
`view('supervisor')` returns only system instructions + historical memory; `view('expert')` returns only historical memory.
