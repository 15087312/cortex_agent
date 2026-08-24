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

`tests/` holds 137 test files (unit/integration/external), 1700+ tests in total. Isolation principles:
temporary SQLite + monkeypatched singletons (production databases untouched), relaxed timeouts for heavy
library loading, background thread classes provide `stop()`, `except: pass` error swallowing is forbidden,
and test fakes must match real model fields exactly.

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
│ 85 tools│    │  7+ servers  │    │ at runtime │
└─────────┘    └──────────────┘    └────────────┘
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
└── get_capability(name)                  # fetched by the tool layer; returns None for graceful degradation when missing

bootstrap.py  register_business_capabilities()
   └── 9 capabilities: blackboard_query / skill_manager / event_retrieval /
       file_history / touchpoint_detector / detector_router /
       value_formatter / tool_security_gate / turn_images
   └── Startup-time validation: _report_capability_status reports missing/failed capabilities (fail-fast)
```

- Dependency direction: `modules → infra` (the reverse dependency `infra→modules` has been reduced to zero)
- Tools fetch services through ports; missing services return explicit error messages (rather than ImportError)
- Tests inject mocks via `register_capability(name, fake)`

### 10.3 AI-Created Tools

```
infra/tool_manager/tools/create_tool.py
├── create_tool(name, code, description, params)   # create a new tool
├── list_my_tools()                                 # list all self-created tools
├── delete_tool(name)                               # delete a self-created tool
└── edit_tool(name, code, description, params)      # edit an existing tool
```

- Self-created tools persist as `.py` files under `data/user_tools/`
- Dynamically registered at runtime via `ToolRegistry.register`
- Automatic syntax checking on creation/editing (`compile()` + `ast.parse()`)
- Supports offline creation and execution without network access

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
