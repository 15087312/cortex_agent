# Thinking Module Internal Architecture

**Language**: [English](./THINKING_ARCHITECTURE.en.md) | [简体中文](./THINKING_ARCHITECTURE.md)

## Core Execution Model: Two-Layer ReAct Loop

### Concept

The thinking module adopts a two-layer design separating **cognitive context** from **operational context**:

| Dimension | Thinking Round | Tool Loop (Turn) |
|------|-----------------|-----------------|
| **Corresponding code** | `ContinuousThinker.continuous_think()` | `ModelRunner._generate_with_tools()` |
| **Context management** | Full prompt rebuild each round | Incremental append of assistant/tool messages per turn |
| **Injected content** | Blackboard state, memory retrieval, perception context, delegation progress | Tool call results from the previous turn |
| **Frequency** | Low (one prompt construction per round) | High (appended after every tool call) |
| **Purpose** | Let the model see the latest global cognitive state | Let the model remember the current operational history |

### Execution Flow

```
ModelRunner._think_loop()                          ← session loop (across delegations)
  │
  ├─ ContinuousThinker.continuous_think()          ← thinking round
  │   │
  │   ├─ round 0: _build_prompt()                  ← rebuild context
  │   │     → blackboard slices (goals/delegations/expert findings)
  │   │     → memory retrieval (vector + keyword)
  │   │     → perception context (screen/window state)
  │   │     → conscience guidance (internalized values)
  │   │
  │   └─ _generate_with_tools()                    ← tool loop (ReAct)
  │       │
  │       ├─ turn 0: chat → tool_calls[web_search]
  │       │          → MCP execution → 5 results
  │       │          → inject results into messages → continue
  │       │
  │       ├─ turn 1: chat (with search results)
  │       │          → tool_calls[web_fetch]
  │       │          → MCP execution → page content
  │       │          → inject results into messages → continue
  │       │
  │       ├─ turn 2..N: chat → tool execution → ...
  │       │
  │       └─ turn N: chat → no tool_calls
  │                  → output final text → return
  │
  ├─ delegation detected → break → _wait_for_wakeup(300s)
  │   received thinking_result → rebuild prompt → new round
  │
  └─ no delegation + continue=false → _notify_thinking_complete
```

### Why Two Layers Instead of One

With a single layer — rebuilding the prompt (blackboard + memory + perception) on every tool call — the token consumption and latency per tool call would be unacceptable.

If only the tool loop were used — never rebuilding the prompt — the model would not see global information such as findings written to the blackboard by other models or delegation completion status.

The two-layer separation ensures rebuilding happens only when needed (new task, being woken up), while the continuous context of tool execution stays incrementally appended in the messages array.

### Cross-Model Collaboration

```
Commander ModelRunner
  while _running:
    continuous_think → delegate_task(Supervisor) → exit → _wait_for_wakeup
                                                      ↓
                                                Supervisor ModelRunner
                                                  while: continuous_think → delegate_task(Expert) → wait
                                                                                           ↓
                                                                                      Expert execution
                                                                                tool loop → done
                                                      ↑ thinking_result
    woken up → reset → new round of continuous_think → integrate → done
```

All three tiers (Commander/Supervisor/Expert) share the same `continuous_think` + `_generate_with_tools` execution model, differentiated only by `max_rounds` and tier-specific exit conditions.

### About RuntimeExpert

`RuntimeExpert` is a reserved abstract base class for expert types that may need different lifecycle management in the future (e.g., a persistently running security monitoring expert). Currently all experts go through the `_think_loop` → `_generate_with_tools` path; `RuntimeExpert.run_cli_mode` is not used in production.

---

## Pure Conversation Engine (chat_light / chatonly)

`CORTEX_MODE=chatonly` takes an independent lightweight engine `modules/thinking/chat_light/`, **not** the two-layer ReAct loop above:

```
User message
   ↓
_recall_memories (memory recall, optional)
   ↓
ContextSlicer.slice (conversation history slicing)
   ↓
PromptComposer.build_system (persona/system overrides/perception notes)
   ↓
ModelRunner.run (single-pass streaming generation, no continuous thinking/no delegation)
   ↓
Streamed tokens → frontend
```

Differences from the Agent engine:

| | Agent (core/) | Pure conversation (chat_light/) |
|---|---|---|
| Engine | `core/continuous_thinker` + `_generate_with_tools` | `chat_light/continuous_thinker` single-pass |
| Thinking loop | Multi-round ReAct + control tools + delegation | None (single generation) |
| Models | Three tiers (Large/Supervisor/Expert) | Single "chief commander" persona |
| Persona | Role-based roles.yaml + personas | `get_persona("orchestrator")`, falling back to large-tier custom agents |

The entry point `chat_gateway._chatonly_ws` routes via `_resolve_mode()`: agent goes through api_stream, chatonly goes through this engine.
