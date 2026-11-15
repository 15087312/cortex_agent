# Architecture Design Document

**Language**: [English](./ARCHITECTURE.en.md) | [简体中文](./ARCHITECTURE.md)

> A detailed tour of the Cortex Agent system architecture — four-layer structure, event-driven blackboard, multi-model orchestration, protocol decoupling

> **Update note**: This document has been synchronized with the current codebase (9 business modules,
> the `config/providers` model-format layer, dual conversation engines, multi-surface interaction,
> and secure JSONL auditing). Non-existent modules (attention) and classes have been removed or corrected.

---

## 0. Interaction Surfaces and Dual Engines

Cortex provides four interaction surfaces and two conversation engines:

### 0.1 Interaction Surfaces

| Surface | Entry Point | Description |
|---------|-------------|-------------|
| Web UI | `frontend/` (Vue 3 + Vite) | 15 pages: Chat/Orchestration/Settings/Dashboard, etc.; proxied to the backend via the `/api` prefix |
| Qt Desktop Client | `frontend/main.py` (PyQt6 + QtWebEngine) | Starts server.py in the background; the Qt window embeds the Web UI; auto-launches the desktop pet |
| Desktop Pet | `frontend/pet_launch.py` + `pet_widget.py` + `modules/desktop_pet/` | Live2D transparent always-on-top window, bound to the fixed main session pet_main, with TTS voice replies |
| Terminal TUI | `cli_tui/` (Textual) | Launched via `cortex` |

### 0.2 Dual Conversation Engines

| Mode | Engine | Flow |
|------|--------|------|
| Agent (default) | `modules/thinking/core/` | Multi-role orchestration: Large decides → Supervisor decomposes → Experts run in parallel → Blackboard integrates |
| Chat-only (chatonly) | `modules/thinking/chat_light/` | Lightweight single persona: `_recall_memories → ContextSlicer.slice → composer.build_system → runner.run`; no continuous thinking loop / delegation |

The entry point `chat_gateway` (`/stream/ws/{sid}`) routes by `_resolve_mode()`: agent goes through `api_stream`, chatonly goes through `_chatonly_ws`.

### 0.3 Testing

`tests/` holds 387 test files (unit/integration/external), 6700+ tests in total. Isolation principles:
temporary SQLite + monkeypatched singletons (production databases untouched), relaxed timeouts for heavy
library loading, background thread classes provide `stop()`, `except: pass` error swallowing is forbidden,
and test fakes must match real model fields exactly.

**Verification layers (pick one per new test; see §110):**

| Layer | Location | Applies to |
|-------|----------|------------|
| Artifact-level unit tests | `tests/unit/` | Doubles for external services must sit at the **boundary**: a fake backend still produces a real artifact (audio duration + waveform, image size + pixels, file schema), never just "file exists / returns non-empty" |
| Cross-boundary contract | `tests/unit/test_frontend_api_contract.py` | Every request path in frontend sources must exist in `api.main.app.routes` (method + path), including the `/api` proxy prefix, so `fetch` mocks cannot turn a 404 into `{}`; a call whose path cannot be extracted **fails** rather than being skipped |
| Real-device integration | `tests/integration/` + `@pytest.mark.external` | Features needing a real microphone/system speech/display/local model use the `external` marker (skipped by default, run with `-m external`); when real torch is required, run it in a subprocess (conftest sets `torch=None` session-wide). Once selected, a missing backend/model **fails**, never skips |
| UI state invariants | `frontend/src/**/*.spec.js` | Assert the currently edited entity, dirty flag, delete target, etc. — not just which API a click called |

---

## 1. Overall Architecture

### 1.1 Four-Layer Structure

```
┌──────────────────────────────────────────────────────────────┐
│  L1 Entry Layer (cortex/)                                    │
│  CLI entry · subprocess orchestration · TUI launch · version management   │
└──────────────────────┬───────────────────────────────────────┘
                       │ uvicorn subprocess / os.execvp
┌──────────────────────▼───────────────────────────────────────┐
│  L2 API Layer (api/)                                         │
│  FastAPI application · WebSocket/SSE streaming · middleware chain   │
│  CORS · API Key auth · rate limiting · request ID · logging  │
└──────────────────────┬───────────────────────────────────────┘
                       │ Route dispatch
┌──────────────────────▼───────────────────────────────────────┐
│  L3 Business Layer (modules/)                                │
│  9 business modules: thinking/memory/security/perception/output/management/database/desktop pet/cortex (no attention module)   │
└──────────────────────┬───────────────────────────────────────┘
                       │ Protocol interfaces + direct imports
┌──────────────────────▼───────────────────────────────────────┐
│  L4 Infrastructure Layer (infra/)                            │
│  Model clients · tool registry/management · Prompt engine    │
│  NLP · data processing · security policies · MCP · database  │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 Dependency Rules

| Rule | Description |
|------|-------------|
| L3 → L4 | ✅ Allowed (business modules use infrastructure) |
| L4 → L3 | ❌ Forbidden (infrastructure must never depend back on business) |
| L3 ↔ L3 | Only via MessageBus, CognitiveBlackboard, or Protocol interfaces |
| L4 ↔ L4 | Allowed (same-layer modules may reference each other) |

### 1.3 Shared Utility Layer (utils/)

```
utils/
├── logger.py         # Logging: console + daily rotating files (14-day retention)
├── async_utils.py    # Async utilities: async_wrap, concurrency control, timeouts, task groups
├── json_utils.py     # JSON: DateTimeEncoder, serialization/deserialization
└── time_utils.py     # Time: now, formatting, time ranges, date boundaries
```

---

## 2. Core Data Flow

### 2.1 Main Request Processing Flow

```
User input (WebSocket/SSE)
  │
  ▼
api_stream.py :: StreamThinkingSystem.think()
  │
  ▼
multi_model_orchestrator.py :: MultiModelOrchestrator.process()
  │
  │  1. SecurityPort.validate_input()          → SecurityAPI input review
  │  2. ContextPort.load_context()             → ContextManager + memory retrieval
  │  3. GuidancePort.run()                     → PreGenExpertPipeline
  │     └─ ValuesExpert + SecurityExpert + EmotionExpert (in parallel)
  │  3.5 SkillManager.match_skill()            → YAML skill matching
   │  4. _execute_multi_model_thinking()        ← core orchestration
   │     │
   │     ├─ TurnContext + CognitiveBlackboard initialization
   │     ├─ ModelRunnerManager.start_listening()
   │     ├─ Inject context into the Blackboard (delegation guidance, conscience guidance, memories)
   │     ├─ MessageBus: probe_start("large")    → ModelRunner activation
   │     │
   │     ├─ ModelRunner._think_loop()            ← session loop (spans delegations)
   │     │   │
   │     │   ├─ ContinuousThinker.continuous_think()  ← thinking rounds
   │     │   │   │  Rebuild the prompt each round (blackboard state + memories + perception)
   │     │   │   │
   │     │   │   └─ _generate_with_tools()        ← tool loop (ReAct)
   │     │   │       │  turn 0..N: chat → tool_calls → execute → results injected
   │     │   │       └─ no tool calls → output text → return
   │     │   │
   │     │   ├─ Delegation detected → exit thinking rounds → _wait_for_wakeup
   │     │   ├─ thinking_result received → rebuild prompt → another thinking round
   │     │   └─ no delegation and continue=false → exit session loop
   │     │
   │     ├─ Wait for the thinking_complete event (MessageBus, 300s timeout)
   │     └─ Read CognitiveBlackboard.final_response
  │
  │  5. OutputReviewPort.review()              → Output validation + expert review + emotion styling
  │  6. ContextPort.save_memory()              → Conversation memory saved
  │  7. Memory promotion (fire-and-forget)     → background memory promotion
  │
  ▼
Response streamed back to the user via WebSocket/SSE
```

### 2.2 Delegation Flow

```
Large model calls delegate_task(role="code_writer", task="implement the payment module")
  │
  ▼
ProbeDelegationAdapter.delegate()
  ├─ resolve_role("code_writer") → ("expert", "expert_implementer")
  ├─ ProbePermissionManager.validate(caller_tier, target_tier)
  └─ probe_start(probe_id, task, identity)
       │
       ▼
  MessageBus.SYSTEM(probe_started)
       │
       ▼
  ModelRunnerManager._listen_loop() receives it
       │
       ▼
  start_runner() → ModelFactory.create_instance() → ModelRunner
       │
       ▼
  ModelRunner._think_loop() → ContinuousThinker
       │
       ▼
  Expert finishes → _write_final_result()
       ├─ Blackboard: add_observation / write_expert_finding
       └─ MessageBus: thinking_result → return_to_model_id
            │
            ▼
  Large ModelRunner._wait_for_wakeup_message() is woken up
       │
       ▼
  Large model continues reasoning (reads expert results from the Blackboard)
```

---

## 3. Key Design Patterns

### 3.1 Cognitive Blackboard (CognitiveBlackboard)

**Singleton source**: one Blackboard instance per Turn, created by `SessionLifecycle`.

**Data structure**:
```python
class CognitiveBlackboard:
    goal: str                                    # current user goal
    delegations: Dict[str, Delegation]           # delegation tasks (role, status, metadata)
    observations: List[Observation]              # observations (tier, content, metadata)
    expert_findings: Dict[str, ExpertFinding]    # expert findings
    dialog_entries: Deque[DialogEntry]           # dialog entries (maxlen=500)
    final_response: str                          # final output
```

**Thread safety**: `threading.RLock` guards all write operations.

**Layered views** (`ContextSlicer`):
- `slice_for_large()`: goal + plan + risks + delegations + expert findings + memories
- `slice_for_supervisor()`: task goal + available tools
- `slice_for_expert()`: current step + tool status + last 5 steps of history

**Broadcast mechanism**: every write broadcasts a change event via the MessageBus.

### 3.2 Event-Driven Communication (MessageBus)

**Singleton**: `ModelMessageBus` is a global singleton.

**Communication patterns**:
| Pattern | Method | Purpose |
|---------|--------|---------|
| Point-to-point | `send(Message)` | Direct communication between models |
| Broadcast | `broadcast(Message)` | Global event notification |
| RPC | `request()` + Future | Request-response style calls |
| Subscription | `subscribe(channel, callback)` | Event-driven callbacks |

**Message types**:
- `SYSTEM`: probe_started, probe_stopped, thinking_complete, thinking_result
- `EXPERT`: inter-expert communication
- `USER`: user input

**TTL cleanup**: messages auto-expire after 300 seconds by default.

### 3.3 Ports & Adapters Pattern

Defined in `modules/thinking/ports.py` and `adapters.py`:

| Port | Adapter | Responsibility |
|------|---------|----------------|
| `SecurityPort` | `SecurityApiAdapter` | Input validation |
| `GuidancePort` | `PreGenExpertGuidanceAdapter` | Conscience system (inner monologue / past experience) injection |
| `OutputReviewPort` | `OutputSystemReviewAdapter` | Output validation + expert review + emotion styling |
| `ActivityNotifierPort` | `DifferenceDetectorActivityNotifier` | Notifies the difference detector |

Each adapter internally uses lazy imports + try/except fallback, ensuring a single module failure does not affect the whole.

### 3.4 Probe-Driven Activation

Models never call models directly. Activation chain:

```
delegate_task (tool) → ProbePermissionManager (permissions) → probe_start (registration)
  → MessageBus.SYSTEM(probe_started) → ModelRunnerManager (creates runner)
  → ModelRunner → ContinuousThinker (execution) → Blackboard (writes results)
  → MessageBus(thinking_result) → delegator wakes up
```

**Permission hierarchy**: `Large > Supervisor > Expert`, enforced at three levels by `ProbePermissionManager`.

### 3.5 Current State of Singleton Patterns

The codebase uses several singleton implementation styles:

| Style | Used By | Notes |
|-------|---------|-------|
| Module-level global variable | `tool_manager`, `prompt_manager` | Simplest; initialized at import time |
| `__new__` + `_initialized` | `PromptManager`, `PromptRegistry` | Controlled inside the class |
| Class variable + `threading.Lock` | `LiteModelClient`, `MCPToolService` | Thread-safe |
| `@classmethod` methods | `ToolRegistry` | No instances; pure class-level state |
| Double-checked locking | `ValueSystem` | Safe under high concurrency |

**Known limitation**: `asyncio.Lock` binds to the event loop present at creation time. If a singleton is used across loops (e.g., uvicorn hot reload), it must be rebuilt.

---

## 4. Thread and Coroutine Safety Model

### 4.1 Thread Model

```
Main thread (asyncio event loop)
  ├─ FastAPI request handling (async)
  ├─ WebSocket connection management (async)
  └─ MessageBus communication (async)

Daemon thread pool
  ├─ ModelRunner (one thread per model)
  │   └─ ContinuousThinker._think_loop() (sync → internally invokes async via asyncio.run())
  ├─ ModelRunnerManager._listen_loop() (MessageBus consumer)
  ├─ Synchronizer file watching (polling)
  ├─ ProbeCache cleanup (30-minute TTL)
  └─ ProactiveOutreach idle detection
```

### 4.2 Synchronization Primitives

| Primitive | Location | Protects |
|-----------|----------|----------|
| `threading.RLock` | CognitiveBlackboard | All state reads/writes |
| `threading.RLock` | SessionLifecycle | State transitions |
| `threading.RLock` | GlobalContextPool | Global context |
| `threading.Lock` | ToolRegistry._tools | Tool registry |
| `threading.Lock` | ToolManager._tool_events | Event records |
| `asyncio.Lock` | ModelMessageBus | Message queues |
| `threading.Event` | ModelRunner | Wakeup signal |

### 4.3 Known Limitations

- A singleton `asyncio.Lock` binds to a specific event loop and breaks across loops
- Some tools use blocking I/O (`subprocess.run`, `requests.get`, `time.sleep`), which blocks the event loop
- `model_factory.get_model_factory()` is not thread-safe (no lock protection)

---

## 5. Configuration System

### 5.1 Configuration Hierarchy

```
Environment variables (.env)
  ↓ override
Pydantic Settings (config/settings.py)
  ↓ injected
Each module accesses values via settings.xxx
```

### 5.2 Core Configuration Classes

| File | Class | Responsibility |
|------|-------|----------------|
| `config/settings.py` | `Settings` | Global configuration (model APIs, feature flags, TTLs, thresholds) |
| `config/providers/` | ProviderRegistry | Unified adaptation of model API formats (openai/anthropic/dashscope/gemini/azure/bedrock/cohere/ollama) |
| `config/providers/catalog.py` | ProviderSpec | Catalog of 35+ providers: name → default endpoint/format/models/key (opencode-style minimal configuration) |
| `config/prompts/` | PromptComposer | Prompt assembly (roles.yaml / base.yaml) |
| `config/values_store.py` | ValueSystem | Value-rule storage (add/remove/cleanup) |

### 5.3 Model API Adapter Layer (Provider Adapter Layer)

`config/providers/` is a standalone "provider → protocol" adapter layer sitting between the model
clients (`infra/model/*`) and the external LLM APIs, with clearly bounded responsibilities:

```
infra/model/*_model_client.py   ← only handles HTTP timing/retries/ChatMessage serialization
        │  delegates
        ▼
config/providers/registry.py    ← resolution: provider name > explicit format > URL inference > default OpenAI
        │  instantiates
        ▼
config/providers/{openai,anthropic,dashscope,gemini,azure,bedrock,cohere,ollama}.py
        │  each adapter is responsible for:
        │    building request headers (authentication scheme)
        │    assembling request bodies (protocol format conversion)
        │    parsing responses (restored to the standard {content, tool_calls, finish_reason, usage})
        │    parsing SSE streams
config/providers/catalog.py      ← declarative catalog of 35+ providers (ProviderSpec)
```

**Minimal configuration**: users only fill in a provider name; everything else is auto-completed
(via `resolve_model_tier()` in `config/settings.py`):

```dotenv
LARGE_MODEL_PROVIDER=deepseek    # automatically uses api.deepseek.com/v1 + deepseek-chat + DEEPSEEK_API_KEY
# or
LARGE_MODEL_PROVIDER=gemini       # automatically uses generativelanguage.../v1beta + gemini-2.0-flash
# or
LARGE_MODEL_PROVIDER=anthropic     # automatically uses api.anthropic.com/v1 + claude-3-5-sonnet-...
```

Resolution priority: `*_MODEL_PROVIDER` (catalog lookup) > `*_MODEL_API_FORMAT` (explicit format) >
URL inference (`base_url` containing `dashscope`/`anthropic`/`generativelanguage`, etc.) >
default OpenAI-compatible. Explicitly set `*_MODEL_API_URL` / `*_MODEL_NAME` always override catalog defaults.

Protocol matrix:

| Protocol | Adapter | Auth Scheme | Applicable Providers |
|----------|---------|-------------|----------------------|
| openai | OpenAIProvider | `Authorization: Bearer` | OpenAI/DeepSeek/Groq/OpenRouter/Mistral/Kimi/GLM/MiniMax/SiliconFlow/… 30+ |
| anthropic | AnthropicProvider | `x-api-key` | Anthropic Claude |
| gemini | GeminiProvider | `x-goog-api-key` | Google Gemini/Vertex |
| azure | AzureProvider | `api-key` + api-version | Azure OpenAI |
| bedrock | BedrockProvider | AWS SigV4 | AWS Bedrock |
| cohere | CohereProvider | `Authorization: Bearer` | Cohere |
| ollama | OllamaProvider | None | Local Ollama |
| dashscope | DashScopeProvider | `Authorization: Bearer` | Alibaba Cloud Bailian/ModelScope |

### 5.4 Runtime Configuration Changes

The `PUT /config/{key}` endpoint supports runtime modification, with the following limitations:
- Only whitelisted keys can be modified (`_MODIFIABLE_CONFIG_KEYS`)
- Implemented via `setattr(settings, key, value)` (⚠️ skips Pydantic validation)
- Some settings (e.g., `DIFFERENCE_DETECTOR_ENABLED`) do not take effect dynamically after modification

---

## 6. Identity and Permission System

### 6.1 Identity Templates

Defined in `modules/thinking/identity.py`:

```python
ModelIdentity:
  model_id: str          # unique identifier
  name: str              # display name
  tier: str              # large / supervisor / expert
  role: str              # role description
  personality: str       # personality traits
  speaking_style: str    # speaking style
  tool_whitelist: list   # tool whitelist
  permissions: ModelPermissions  # permission configuration
```

12 built-in identity templates: large, code_supervisor, query_supervisor, creative_supervisor, code_reviewer, code_implementer, test_writer, analyzer, customer_expert, creative_writer, emotion, memory_manager.

### 6.2 Permission Model (ModelPermissions)

```python
ModelPermissions:
  can_start_probes: bool         # whether probes can be started
  can_stop_probes: bool          # whether probes can be stopped
  controllable_tiers: list       # tiers under control
  can_write_memory: bool         # whether memory can be written
  allowed_tool_categories: list  # allowed tool categories
  can_delegate: bool             # whether tasks can be delegated
  delegatable_tiers: list        # tiers that tasks can be delegated to
  max_instances: int             # maximum number of instances
```

### 6.3 Tool Whitelists

| Tier | Whitelist | Description |
|------|-----------|-------------|
| Large | `"*"` | All tools |
| Supervisor | Management tools | delegate_task, continue_thinking, etc. |
| Expert | Role-restricted | Defined by identity templates; HIGH/CRITICAL risk tools are automatically blocked |

Control tools (continue_thinking, delegate_task, create_supervisor, respond_to_user) are not registered in the ToolRegistry; ModelRunner injects them dynamically in `_generate_with_tools()`.

---

## 7. Context Management System

### 7.1 GlobalContextPool (GCP)

Global context pool; singleton; guarded by `threading.RLock`:

- **File storage**: project file content cache
- **Project metadata**: project name, structure, dependencies
- **Global state**: current task, phase, participants
- **Event log**: up to 10,000 entries, automatic TTL cleanup
- **Session context**: an independent context view per session

### 7.2 Token Estimation and LLM Summarization (Formerly the Compression Engine)

> Historical note: the original CompressionEngine's 5-level rule-based compression
> (NONE/LIGHT/MODERATE/HEAVY/AGGRESSIVE) has been removed. Token control is now handled
> uniformly by the LLM summarization mechanism; rule-based truncation is no longer used.

Current state:

| Component | Responsibility |
|-----------|----------------|
| `CompressionEngine.estimate_tokens` | Rough token estimation for mixed Chinese/English text (feeds usage statistics and threshold checks) |
| `ModelRunner._maybe_summarize_context` | When the context exceeds 90% of the model window during the tool loop, calls the current model to summarize; messages replaced with [system + summary + original task] |
| `chat_light/context_slicer.ContextSlicer` | Keeps the full text of the most recent 15 entries; older parts are summarized by the LLM into a single summary; falls back to head/tail truncation only if summarization fails |
| `TurnContext._compact` | Warns without truncating when over the limit; control is left to the summarization mechanism above and source-side trimming |

### 7.3 Auditor

- **Redundancy detection**: Jaccard similarity
- **Memory usage monitoring**
- **Consistency checks**: timestamp ordering, event-file cross-references
- Results cached for 60 seconds

---

## 8. Memory System Architecture

### 8.1 Two-Tier Recall System

```
Shallow recall (default)      Deep recall (triggered)
   │                            │
   ▼                            ▼
EventRetrieval              CausalGraph (causal graph)
(RAG semantic+keyword+importance)   │ anchor location + neighborhood diffusion
   │                                ▼
   ▼                            CausalTree (tree drilling)
Injected into prompt as       trace up / drill down / lateral comparison
[historical memory]               │
                                  ▼
                              EventStore (event pool)
                              composite-ranked recall (causal+semantic+importance+time)
```

### 8.2 Shallow Recall (EventRetrieval)

Scoring formula: `0.60×semantic + 0.15×importance + 0.10×recency + 0.08×utility + 0.07×frequency`

| Factor | Source | Description |
|--------|--------|-------------|
| semantic | FAISS vector inner product | Normalized to 0–1 |
| importance | Discrete LLM annotation | critical=1.0 → trivial=0.03 |
| recency | exp(-λ·days) | Decay rate varies by type |
| utility | log(access+3)/log(13) | Higher with more retrievals |
| frequency | log(mention+3)/log(13) | Higher when the topic is mentioned more often |

### 8.3 Deep Recall (CausalGraph + CausalTree)

Three-step closed loop:
1. **Graph localization**: `find_anchor_nodes()` locates anchor nodes by keywords, then diffuses directionally through the neighborhood based on intent
2. **Tree drilling**: `trace_up()` traces origins → `trace_down()` predicts → `compare_lateral()` induces patterns
3. **Event recall**: composite ranking `0.3×semantic + 0.4×causal relevance + 0.2×importance + 0.1×time`

Trigger conditions (automatic):
- The query contains logical terms such as "why / cause / consequence / pattern / what if back then"
- Shallow recall confidence < 0.3
- The current task is decision-making/analysis oriented

| Module | File | Responsibility |
|--------|------|----------------|
| CausalGraph | `modules/memory/causal_graph.py` | Persistence of causal nodes and edges (SQLite) |
| CausalTree | `modules/memory/causal_tree.py` | Trace-up / drill-down / lateral-comparison traversal |
| DepthRecallScheduler | `modules/memory/depth_recall.py` | Trigger evaluation + three-step closed-loop scheduling |
| ResultFusion | `modules/memory/result_fusion.py` | Result format assembly |

### 8.4 Memory Pipeline

| Phase | Implementation | Description |
|-------|----------------|-------------|
| Write | `api_stream.py` `_post_task_extraction` → `EventReducer.reduce()` | 30s after the session ends |
| Shallow read | `ContinuousThinker._build_prompt()` → `EventRetrieval.retrieve()` | Every thinking round |
| Deep read | `ContinuousThinker._build_prompt()` → `DepthRecallScheduler.deep_recall()` | Triggered |
| Tool invocation | `deep_recall` probe tool | Invoked proactively by the model |

---

## 9. Security Architecture

### 9.1 Three Layers of Defense

```
Input → [Input Review] → [Execution Review] → [Output Review] → Response
         │                 │                    │
         ▼                 ▼                    ▼
    SecurityAPI       SecurityGate
    (intent           (tiered tool approval)   (dual-layer: rules + LLM)
     recognition)
```

### 9.2 Security Gate

Tiered approval before tool execution:

| Risk Level | Handling |
|------------|----------|
| LOW | Quick checks (paths, parameter formats) |
| MEDIUM | Path/command validation + whitelist |
| HIGH | LLM approval |
| CRITICAL | User confirmation or LLM approval |

### 9.3 Audit System

- **Format**: JSONL
- **Integrity**: JSONL append-only writing (timestamp/event_type/security_level/content_preview/result/metadata)
- **Content**: all tool invocations, permission decisions, security events
- **Traceability**: all tool invocations, permission decisions, and security events fully persisted to disk

---

## 10. MCP and Tool System Architecture

The plugin system has been removed entirely; its ecological niche is now filled by **MCP (Model Context Protocol)** and **AI-created tools**.

### 10.1 Three-Tier Tool Model

```
┌──────────────────────────────────────────────────┐
│              Unified Routing Layer               │
│  MCPToolService (MCPToolExecutor.merge_tools)    │
│  CombinedToolProvider + CombinedToolExecutor     │
│  ToolManagerPermissionAdapter (permission checks)│
└──────────────────────┬───────────────────────────┘
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
┌─────────┐    ┌──────────────┐    ┌────────────┐
│ Built-in│    │  MCP remote  │    │ AI-created │
│ToolReg. │    │  stdio/SSE   │    │ create_tool│
│ 85 tools│    │  7+ servers  │    │ +authored  │
└─────────┘    └──────────────┘    │  plugins   │
                                   │ (§10.3.1)  │
                                   └────────────┘
```

### 10.2 MCP Integration Architecture

```
infra/mcp/
├── transport.py             # MCPStdioTransport / MCPSseTransport
│                            # uses the mcp SDK's stdio_client / SSE client
├── server_manager.py        # MCPServerManager — server lifecycle
│                            # add_server() adds at runtime; connect_all connects automatically
├── combined_provider.py     # CombinedToolProvider — merges local + remote tool lists
│                            # CombinedToolExecutor — unified execution routing
├── perception_client.py     # MCPPerceptionClient — obtains external perception data
│                            # via resources/subscribe
└── factory.py               # MCP connection factory: stdio / sse / auto-detect
```

**Key technical details**:
- MCP transports manage their lifecycle manually via `__aenter__`/`__aexit__` (so `async with` does not close the connection)
- On tool name conflicts, built-in ToolRegistry tools win and same-named MCP tools are skipped
- `ToolManagerPermissionAdapter` wraps the MCP executor to ensure security approval goes through `_check_tool_permission()`

#### 10.2.1 MCP Lifecycle Management (Hot-Plugging + Auto-Reconnect)

```
MCPServerManager
├── start_all() / add_server()      # connect + index tools + start reconnect monitoring
├── remove_server(name)             # independent hot-unload: detach tools first → close connection → clear index
├── replace_server(name, ...)       # hot replacement (equivalent to dsh HMR: dispose old + build new)
├── _watch_connection(name)         # background monitor task: periodically checks is_connected
├── _reconnect_with_backoff()       # exponential backoff on disconnect: base_delay × 2^(attempt-1)
└── _refresh_tools(name)            # refresh the tool index after successful reconnection (model-visible list updates immediately)
```

- **Independent hot-plugging**: `remove_server` cleans up in reverse order (detaches tools first so models stop seeing them immediately → disconnects → clears the index) without affecting other servers; `replace_server` hot-swaps with updated configuration
- **Auto-reconnect**: enabled via the `MCPServerConfig.reconnect` setting; after disconnection it self-heals with exponential backoff and re-runs list_tools once reconnected
- **No leftover tasks**: `remove_server`/`shutdown` stops all monitoring tasks (asyncio task cancel + stop events), eliminating background task leaks

#### 10.2.2 Dependency Injection Ports (Capability Registry)

Decoupling between the tool layer and business modules is achieved via **Service Locator-style dependency injection**:

```
infra/tool_manager/service_registry.py
├── register_capability(name, provider)   # registered on the modules side (bootstrap assembly layer)
├── provide_capability(name, provider)    # take over a port; returns the undo (see §12)
└── get_capability(name)                  # fetched by the tool layer; returns None for graceful degradation when missing

bootstrap.py  register_business_capabilities()
   └── 13 capabilities: blackboard_query / skill_manager / event_retrieval /
       file_history / touchpoint_detector / detector_router /
       value_formatter / tool_security_gate / turn_images / context_controller /
       event_store / causal_graph / knowledge_base
   └── Startup-time validation: _report_capability_status reports missing/failed capabilities (fail-fast)
   └── Triggers tool-door registration: probe_tools, plugin_authoring_tools (see §10.3.1) —
       decorator self-registration is an import side effect, so only the assembly layer may
       trigger it, otherwise infra would import modules back
   └── Then restores AI-created tools (ai_tools.restore_ai_tools; formerly an import side effect — see §12.2)
```

- Dependency direction: `modules → infra` (the reverse dependency `infra→modules` has been reduced to zero)
- Tools fetch services through ports; missing services return explicit error messages (rather than ImportError)
- Tests inject mocks via `register_capability(name, fake)`

### 10.3 AI-Created Tools

```
infra/tool_manager/tools/ai_tools.py
├── create_tool(name, code, description, params)   # create a new tool
├── list_my_tools()                                 # list all self-created tools
├── delete_tool(tool_name)                          # delete a self-created tool
├── edit_tool(name, code, description, params)      # edit an existing tool
└── restore_ai_tools()                              # rebuild from persisted records (assembly layer calls it)
```

- Self-created tools persist as `.py` files under `data/user_tools/`
- Dynamically registered at runtime via `ToolRegistry.register`
- Automatic syntax checking on creation/editing (`compile()` + `ast.parse()`)
- Supports offline creation and execution without network access

#### 10.3.1 Runtime-authored plugins (L5): three doors

`create_tool` builds one tool at a time, and what it builds is already the final form. One
level more freedom — **submit a whole plugin source file that both provides tools and takes
over capability ports, with failure undone on the spot** — is carried by two door modules in
`modules/shared/`; the machine semantics live in §12.4:

```
modules/shared/plugin_authoring.py          # machine semantics (mount/unmount/observation/conflict/storage)
modules/shared/plugin_authoring_tools.py    # door 1: model-facing tools
├── mount_plugin(name, source, trial, replace)  # mount; persists only after the trial passes
├── unmount_plugin(name)                        # detach, keep the record (restorable)
├── list_plugins()                              # on disk + which names each one answers today
└── delete_plugin(name)                         # detach and drop the record

modules/shared/plugin_authoring_api.py      # door 2: HTTP routes /plugins/authored
├── POST   ""                # submit source (same trial semantics as mount_plugin)
├── GET    ""                # full status
├── GET    "/{name}"         # one entry: disk row + live ownership
└── DELETE "/{name}?forget="  # forget=false detaches without deleting

api/main.py  lifespan                      # door 3: restore at boot
└── load_authored(store) / unload_authored()   # see §12.3
```

- All three doors share one set of semantics; they differ only in how an error is expressed:
  the tool door returns `✅`/`❌` strings, the HTTP door returns 422 (the source itself is
  broken) or 400 (conflict, or plugin code blew up) with the full traceback, and a failing
  restore at boot only warns instead of aborting startup
- Tool functions are synchronous while execution lands in an `asyncio.to_thread` worker (no
  running loop there), hence the `_run()` bridge in the door: no loop → `asyncio.run`,
  loop present → run it on a fresh thread. Never call `asyncio.run` on somebody else's loop
- Persistence file: `data/authored_plugins.json` (env `CORTEX_AUTHORED_PLUGINS_FILE`)

#### 10.3.2 Seam catalogue: takeover points are computed from code, not handwritten

The ports in §10.3 are *declared* takeover points, but a declaration is not a working
switch: as long as one more file hard-imports the original implementation, a plugin taking
over the port only replaces half the path and the same capability runs in two versions at
once. This project makes no plugin-safety claims (§12.5), so there is no permission list to
consult — "what can actually be taken over today" has to be computed from the import/call
graph. `scripts/seam_catalogue.py` does exactly that and writes `docs/SEAMS.md` (generated;
do not hand-edit).

```
bootstrap._safe_register("name", factory)   ──▶ business port: which provider, which symbols
                                                it wraps, which files call get_capability("name")
infra/ports.py register("a.b", provider)    ──▶ facade port (same rules, one extra "system" column)
module-level def get_x() returning a global  ──▶ singleton accessor
@classmethod get_instance / instance         ──▶ singleton accessor
any name/attribute-chain read (mgr.run() too)──▶ reference edge ──▶ attributed to accessors ∪ port-wrapped symbols
```

Tiers are by "can it really be taken over today"; current counts live in `docs/SEAMS.md`:

| Tier | Test | Meaning |
|---|---|---|
| ✅ switchable | port exists + port has consumers + no hard-import readers | one `provide_capability` / `provide_port` replaces the whole path |
| ❌ no port yet | hard-import readers, no port | downgrade the accessor to a port and route readers through it |
| ⚠️ half wired | port exists, yet files still hard-import the same symbol | two behaviours coexist after takeover — harder to spot than having no port |

- Three passes: module ids → each package `__init__.py`'s re-export table → per-file
  references. Relative imports are expanded against the file's own directory and
  `from pkg import name` is followed to its definition site; both of those bugs under-count
  readers, and under-counting reads turns a ⚠️ into a ✅
- One dotted chain counts as its longest segment only: `Cls.get_instance()` makes the caller
  a reader of the *accessor*, not of the class. Counting every segment inflates the class
  numbers and destroys the meaning of the "who touches the class directly" tier
- Locally shadowed names do not make an accessor (`setup_logger` returns a freshly built
  logger, not a module-level singleton); only names handed out via `global` count. Port-wrapped
  module-level instances also enter the table — counting accessors alone misses a whole class
  of takeover points
- Read-only: never imports the scanned code (stdlib + ast), and a parse failure raises —
  under-counting is worse than an error
- Regenerate: `python scripts/seam_catalogue.py --out docs/SEAMS.md`; drift guard: `--check`
  (exit 1 when stale)

`tests/unit/test_seam_catalogue.py` (45 tests) pins every rule above. On 2026-11-14
`/tmp/mutate_b6.py` inverted 24 claims in the generator one at a time: **0 survivors**, and
every file byte-identical to how the run started.

##### Two port registries: the catalogue used to see only half

When the catalogue first reported ❌=0 that claim was **half true**: this repo has two port
registries and the generator only knew the first.

| System | Filled by | Name shape | Plugin takeover entry |
|---|---|---|---|
| business | `bootstrap.register_business_capabilities()` at startup via `_safe_register` | snake_case `tts_output_dir` | `ctx.provide_capability(name, provider)` |
| facade | each provider module itself, at its own import time, via `infra/ports.py::register` | dotted `security.tool_gate` | `ctx.provide_port(name, provider)` |

The facade ports carry the **cross-cutting contracts** — tool gate, permission controller,
identity table, perception system, model factory. Before this they were neither in the
catalogue nor takeover-able, so "every candidate seam is switchable" covered only the
business half, and it was missing in the worst shape: *absent from the table*. The most
consequential swap of all (replace the tool security gate) was the one nobody could do.

Four things had to be added:

- `infra/ports.py::provide(name, provider)` — differs from `register` in exactly one way: it
  **returns the undo**, and the undo checks identity (if someone took the port over later,
  it does nothing). Import-time registration is process history and needs no undo; a plugin
  unmount must return to its predecessor, otherwise unmounting leaves the contract as a hole.
  `ctx.provide_port` is the time-scoped wrapper and it **rejects snake_case names**: picking
  the wrong registry does not raise, it silently swaps nothing.
- The generator learned the second wiring: `ports.register(...)` / `resolve(...)`, where the
  port name may be a literal **or a constant of the file** (`infra/security_gate.py` keeps all
  nine names as `PORT_GATE = "security.tool_gate"`), and the attribute form has to be judged by
  *module*, not by the resolved symbol (`_resolve` deliberately returns `None` for a pure
  module reference). Either rule missing and the whole security facade vanishes from the table
  without an error. Same-named methods are not ports: `ToolRegistry.register("x", f)` stays out.
- A port that is only ever *resolved* still gets a row, with "(no registration site found)" in
  the provider column, because a forwarder like `register_provider(name, provider)` is
  invisible to static analysis. The catalogue must not turn "I cannot see it" into "it does
  not exist".
- L5 authored plugins **see** facade ports too: mount takes a `ports.snapshot()` before and
  after apply and records `port:<name>` entries in that mount's diff, so a takeover gets the
  same conflict check, `list_plugins` row and unmount log line as a tool or capability. Without
  this, a plugin that swapped `security.tool_gate` was **entirely absent** from the conflict
  check, `list_plugins` and the logs — a third invisible table, the same old "only watching the
  first table" bug. Ownership is attributed by the **provider identity** in the snapshot, never
  by calling `resolve()`: a factory port builds a fresh implementation per resolve, so
  instantiating one just to list it would itself be wrong.

Current counts: **73 ports (58 business / 15 facade, 0 dead) · 180 candidate seams ·
✅ 137 / ❌ 0 / ⚠️ 2** — the two ⚠️ are the judged ones below. One half-wiring surfaced the
moment facade ports became visible: `modules.perception:get_perception_system`, the package-root
forwarder, was just `from .setup import ...`, so taking over `perception.system` did not reach
it — and `integration.py` imports exactly that one. It is now port-first with
`_builtin_perception_system` as the provider (otherwise port → accessor → port recurses on the
first call), and `perception.manager` registers a *factory* instead of a ready-made proxy, so
swapping the perception system swaps the compat proxy too.

##### Completing the seams: what is left once ❌ hits zero

On 2026-11-15 every ❌ was downgraded to a port-first accessor (a `_x()` provider per port in
`bootstrap`, call sites moved onto the accessor). The catalogue now reads
**✅ 137 / ❌ 0 / ⚠️ 2**. The earlier reading of 121/0/2 covered only the business half; with
facade ports counted it is the number above, and neither ❌ nor ⚠️ moved — the 16 extra ✅ rows
all sit under facade ports (8 security-facade accessors that were already port-first but
invisible, plus 8 provider objects those ports wrap). Those last two ⚠️ are **judged,
deliberately kept** hard imports:

| Symbol | Still hard-imported by | Why routing it through the port would be wrong |
|---|---|---|
| `EventStore` | `config/settings.py` | switching memory libs must clear the **builtin** singleton; through the port the reset target becomes whatever a plugin installed, and the switch leaves state behind |
| `EventStore` | `modules/desktop_pet/memory_bank.py` | the pet builds one `EventStore(...)` per persona — it wants the class, not "the current implementation" |
| `CausalGraph` | the same two files | the causal graph follows the memory lib, same reason |

The rule in one line: **hard import where the code wants *that class*, port where it wants
*the current implementation***. Both this table and the ✅ count are locked by
`tests/unit/test_seam_catalogue.py` — `MIGRATED` (51 rows, accessor → port plus the builtin it
wraps) may not drift back to ❌, and any reader outside `JUDGED_BYPASS` turns the suite red. A
call site a takeover cannot reach has to be a recorded decision, never an omission.

One trap while writing this code: `_safe_register(name, loader)` registers **what `loader()`
returns**, so for the class-singleton idiom (`Cls.get_instance()` port-first, builtin body in
`Cls._builtin_instance`) the provider points at `_builtin_instance`. Which means
`monkeypatch.setattr(Cls, "get_instance", ...)` in a test **does not bite** — consumers get the
builtin straight off the port, and the symptom is "the mock ignored me, it really loaded the
model". Stub the port instead: `take_over(name, provider)` / `drop_capability(name)` from
`tests/conftest.py` (the latter checks the fallback branch for when no port is wired).

Two registries need two sets of fixtures: dotted facade ports get
`take_over_port(name, provider)` / `drop_port(name)`, and they ride on `ports.provide`'s undo
operation — the very same path a plugin unmount takes. Using `take_over` on `security.tool_gate`
**silently swaps nothing at all** (that key does not exist in that table), which is exactly the
shape of "the test is green but the takeover never actually held". The reverse case is just as
quiet: a call site that resolves the port directly stopped seeing a
`monkeypatch.setattr(perc_mod, "get_perception_system", ...)` the moment that accessor became
port-first — five management-surface tests turned red for that reason, and one of them had been
passing vacuously long before it did.

### 10.4 Learn Mode

Learn mode is a **transient state**, not a fixed execution mode. Flow:

```
Model calls request_mode_change("learn")
  ↓
model_runner injects the learning prompt
  ↓
run_learn_pipeline() executes automatically
  ├─ 1. Open the app (open_app)
  ├─ 2. Take a screenshot (capture_screen)
  ├─ 3. OmniParser element detection (local or remote)
  ├─ 4. ActionPlanner plans the action sequence with AI
  ├─ 5. Execution recording (semantic actions: click_element/type_into etc.)
  └─ 6. Generate the plugin package (PluginBuilder) + Skill YAML update
  ↓
Automatically restores the original execution mode (plan/edit/yolo/control)
```

- Concurrency protection: an `asyncio.Lock` ensures only one learn pipeline runs at a time
- Timeout protection: 120-second overall timeout
- Precision degradation detection: recognizes OCR-only low-precision mode and errors out early
- Semantic actions: `click_element("Save button")` re-detects coordinates via OmniParser at runtime, without relying on fixed pixel positions

---

## 11. Deployment Architecture

### 11.1 Single-Process Mode (Default)

```
cortex command
  ├─ uvicorn subprocess (api.main:app, 1 worker)
  └─ TUI process (os.execvp replaces the cortex process)
```

### 11.2 Docker Mode

```
docker-compose
  └─ app container
      ├─ python scripts/start_all.py
      ├─ 4GB memory limit, 2 CPUs
      ├─ health check: GET /health (30s interval)
      └─ data volume: ./data → /app/data
```

### 11.3 Multi-Worker Mode

```bash
uvicorn api.main:app --workers 4
```

**Note**: with multiple workers, the following features are affected:
- The rate limiter is per-process (not global)
- Module-level singletons are independent in each worker
- MessageBus messages do not propagate across workers

---

## 12. Assembly & Plugin Contract (Reversible)

One sentence: **every assembly step must be able to say how it is undone.** The registration
surface, the scoping of bindings, and the lifecycle each hold one rule, and all three come
from the same fact — anything registered by an import side effect cannot be unmounted without
restarting the process.

### 12.1 Two registration surfaces, two duplicate-name behaviours

```
infra/tool_manager/tool_registry.py          # process-wide registry; knows nothing about scopes
├── @ToolRegistry.register / register(replace=False)  # "the core owns this slot" → duplicate raises DuplicateToolError
├── ToolRegistry.provide(name, func, plugin_name=…)   # "a plugin deliberately takes over" → shadowing allowed, returns undo
├── ToolRegistry.register_tool(…)                     # legacy overwrite path (management relies on source="dynamic"); skips the duplicate check
└── get_plugins() / list_by_source() / unregister_by_plugin()   # fed by the source/plugin_name that provide writes

modules/shared/plugin_api.py                 # contract layer: adds timeliness only, never touches dependency direction
├── Ctx                    named service slots (resolved up the parent chain) + LIFO effect stack + child scopes
│   ├── effect(disposer)   the only way a plugin says "undo this when I leave"; accepts sync and async disposers
│   ├── provide_tool / provide_capability    bindings pushed onto the stack, living and dying with the scope
│   │                                        (capability = business port, snake_case name)
│   ├── provide_port(name, provider)         take over a cross-cutting port (infra/ports.py, dotted name);
│   │                                        rejects snake_case: writing to the wrong table only ever
│   │                                        means "nothing was swapped"
│   └── aclose()           unwinds LIFO; a throwing disposer does not stop the rest
├── Plugin.apply(ctx)      the only hook allowed to bind (neither import time nor __init__ time)
└── install / mounted / unmount              apply raising → the half-mounted scope is rolled back on the spot
```

- **Takeover vs. collision**: a plugin shadowing a builtin is the mechanism working as
  intended (`provide` allows it); two plugins that don't know about each other claiming one
  tool name is a bug (`provide` refuses, unless the same plugin rewrites its own binding).
- **undo is identity-guarded** (it returns immediately when `_tools.get(name) is not info`),
  so it only ever puts back the layer it shadowed. The price: **calling two undos out of
  order resurrects a stale mount** — correctness depends on LIFO unwinding, which is exactly
  why `Ctx` offers no `deactivate` hook.
- **Ports live in two tables** (business = `service_registry`, snake_case; facade =
  `infra/ports.py`, dotted names), so there are two takeover entry points as well. Offer only
  one and "every candidate seam is switchable" describes half the system — see §10.3.2.

### 12.2 Assembly-time registration (bootstrap)

The pkgutil scan in `infra/tool_manager/tools/__init__.py` is the **only** place in the repo
where import-time registration is allowed, and it registers builtin tool modules only.
Restoring AI-created tools (`ai_tools.restore_ai_tools()` — reads `data/ai_tools.json` and
execs model-authored source) used to be a side effect of that import. It now runs at the end
of `bootstrap.register_business_capabilities()`, strictly after the builtin tools are
imported — otherwise registration order alone would decide whether a builtin gets rejected
by the duplicate check because a self-authored tool claimed its name first.

The rule: anything persisted, model-authored, or expected to be removable must not ride on an import.

### 12.3 Lifecycle: register the teardown where you start it

`api/main.py`'s lifespan was ~250 lines of linear startup plus a hand-written teardown list,
and the two lists disagreed: teardown order was not the reverse of startup (the model factory
closed first while perception and the heartbeat were still using it), and 5 components had a
start with no matching stop. Each startup step now registers its disposer right next to itself
via `_push(stack, dispose)`:

```
async with AsyncExitStack() as stack:
    start a component → _push(stack, how to stop it)
```

- **Registration is immediate**: if startup fails halfway, whatever already came up is still
  unwound (previously the whole teardown block was skipped by the exception)
- **Unwinding is always reverse order** — guaranteed by the structure, not by remembering a list
- A component that failed to start never registers its disposer, so nothing gets "stopped" that never ran
- The DB pool is opened lazily and has no startup step, so it is registered first → unwound last
- Fatal-vs-degraded semantics are preserved per step: missing API key and Embedding load failure
  abort startup; vision preload, perception and MCP failures degrade and continue
- The 5 pairings added: perception integrator, desktop-pet engine, per-session scheduled tasks,
  screen content analysis, and the `error_bus` handler installed on the event loop
- The authored-plugin step is placed with intent: the unload is registered **before** the mount
  (so a mount that dies halfway still gets swept), and the whole block sits **after the fatal
  checks but before the first component that calls tools** — unwinding is reverse order, so the
  model factory, perception, heartbeat, pet and scheduled tasks all stop before tools are
  withdrawn from under them
- Tests: `tests/unit/test_api_main.py`, 139 items (133 pre-existing + 3 pinning the new
  semantics above + 3 pinning authored-plugin restore / half-mount rollback / unwind order)

### 12.4 Runtime-authored plugins (L5: the model writes source, it mounts now)

`modules/shared/plugin_authoring.py` is the machinery; three doors sit in front of it. The
machinery knows exactly four rules:

```
mount_source(name, source, replace=False)      # compile → apply(ctx) → observed diff = what it provides
author(name, source, trial, replace, store)    # the above + a trial run + write to disk only if it passed
load_authored(store) / unload_authored()       # startup restore (creation order) / teardown unmount
drop(name) / status()                          # unmount without forgetting / is each stored row live
```

- **Claims are observed, not declared.** Read the model-visible set once before and once after
  the mount (`get_tools_for_api(["*"])` ∪ `registered_names()`); the diff *is* what the plugin
  provided. There is no manifest field, so there is nothing to under-report — this is how the
  §12.5 invariant is implemented at this layer.
- **Two conflict gates.** `provide` refuses at bind time (it alone knows who currently answers);
  observation catches **binds that bypass `provide`** — with no sandbox, plugin source can call
  `register_tool` directly. Only another *authored* mount claiming the name counts as a conflict;
  shadowing a builtin is the takeover mechanism itself. `replace=True` opens both gates, because
  when rows are replayed in persisted order "last author wins" has to hold. Two error types:
  `PluginConflictError` (says who answers) and `DuplicateToolError` (kept verbatim when the owner
  is not an authored mount — there is no plugin name worth printing).
- **Re-authoring undoes first, then installs.** The reverse leaks something invisible: the new
  version's undo records "put back the slot I shadowed", and that slot then held the previous
  version of itself — so unmounting restores an implementation no plugin owns, visible to the
  model and absent from the logs. If the reinstall fails, the old version is put back on the spot
  (`_restore`).
- **The store row is written only after the trial passes.** Every row has succeeded at least
  once, so nothing restored after a restart needs re-verification live. A row that will not mount
  is recorded on itself (`status="failed"` + reason) and skipped — one plugin is not a reason for
  the process to fail to start.

The three doors **may not add policy**, only translate shape:

| Door | Where | Shape |
|---|---|---|
| Assembly (startup restore) | `api/main.py` lifespan | `load_authored` + `_push_async(stack, unload_authored)` |
| Model | `modules/shared/plugin_authoring_tools.py` | `mount_plugin / unmount_plugin / list_plugins / delete_plugin`, registered by a `bootstrap` import |
| HTTP | `modules/shared/plugin_authoring_api.py` | `POST/GET/DELETE /plugins/authored`, failures carry the traceback in `error.message` |

Tool functions are synchronous (tool execution runs via `asyncio.to_thread`) while mounting is a
coroutine, so the tools module has a `_run` bridge: no loop in this thread → open one; a loop is
already running → hand the coroutine to a thread of its own. It never calls `asyncio.run` on
somebody else's loop.

Tests: `tests/unit/test_plugin_authoring.py` (33, machinery) +
`tests/unit/test_plugin_authoring_entries.py` (18, the two doors) + the 3 lifespan items
(restore, half-mount rollback, undo order). On 2026-11-14 each promise was inverted one by one
via `/tmp/mutate_b4.py` — 19 mutations for the machinery and 10 for the doors, 29 total, with
**0 survivors**, and the script verifies afterwards that every file on disk is byte-identical
to where it started. The 3 promises added on 2026-11-15 for the facade-port observability layer
got the same treatment (`/tmp/mutate_b6b4_ports.py`, 4 mutations, **0 survivors**). This table
is not "the tests happened to pass"; it is "each claim has a guard".

### 12.5 What is deliberately absent

No permission model, no signature checks, no sandbox, no isolation: plugin code runs inside the
host interpreter with the interpreter's full privileges. That is the settled policy for this
local single-user app (the governance half was built once and deleted). In place of a trust
boundary: reversibility, no half-mounts left behind, and one invariant — **what the model can
see == what the logs can show** (`provide` writes `source="plugin"` + `plugin_name`, which is
what `get_plugins()` and `GET /tools/plugins/loaded` read).

Known boundaries (do not read the agent side with flow-side semantics):
- `Ctx.provide_tool` writes the process-wide registry directly; there is **no per-run frozen
  frame** as in the flow side. More than twenty call sites across 6 modules read `_tools`.
  Consequence: unmounting a plugin mid-run makes the remaining tool calls resolve back to the
  builtin implementation.
- The trial is "the author names one thing this plugin should do", not a coverage check: a trial
  that does not point at a tool this very mount provided is refused (that would be testing the
  builtin implementation, i.e. testing nothing).
- Bindings a plugin leaves by calling `register_tool` directly are not reachable by unmount
  (undo only restores the slot `provide` registered). The second gate **rejects** such a mount,
  but the stray binding stays behind — the test asserts this boundary as it is, not as we'd like
  it.

