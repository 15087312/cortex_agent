# Error Log & Fixes · Index

**Language**: [English](./README.en.md) | [简体中文](./README.md)

> This directory collects the project's bug-fix records, split by module from the original
> `docs/ERRORS_AND_FIXES.en.md` (107 sections). Section numbers match the original, so
> historical references (`ERRORS_AND_FIXES.md §N`) still work—the old path is now a redirect.
> High-risk behavior changes awaiting confirmation live in [`pending.en.md`](./pending.en.md).

## Structure

| Module | Sections |
|--------|----------|
| [Backend · Thinking / Model / Tools / Delegation](./01-backend-thinking.en.md) | 27 |
| [Backend · Memory / Causality / Mental Activity / Perception](./02-backend-memory.en.md) | 15 |
| [Backend · Config Persistence & Security](./03-backend-config-security.en.md) | 14 |
| [Frontend · Chat / Bubbles / Replay / Multi-session](./04-frontend-chat.en.md) | 15 |
| [Frontend · Pages / Styling / Build / Buttons](./05-frontend-ui.en.md) | 6 |
| [Realtime · WS / SSE / Push / Proxy / TUI](./06-realtime-streaming.en.md) | 8 |
| [Desktop Pet](./07-desktop-pet.en.md) | 1 |
| [Testing / CI / Environment / Dependencies](./08-testing-ci.en.md) | 19 |
| [Process · Audit Methodology / Doc Governance](./09-process-docs.en.md) | 2 |

> The memory-safety testing framework lives in [`../MEMORY_LEAK_TESTING.en.md`](../MEMORY_LEAK_TESTING.en.md).

## Numbering fixes

The original reused four numbers (§40/§41/§42/§43 each appeared twice; the later one was a
follow-up entry). To keep the log searchable, the four later sections became §104–§107 and
each notes its original number; all other numbers are unchanged. References to `§1–§103`
in code comments and other docs therefore remain valid.

| New | Original | Topic |
|-----|----------|-------|
| §104 | §40 (2nd) | Frontend cannot reach backend: IPv6 localhost trap + proxy test blind spot |
| §105 | §41 (2nd) | Mental-activity box disappears after switching sessions |
| §106 | §42 (2nd) | Environment perception not injected into the LLM context |
| §107 | §43 (2nd) | File perception effectively dead — pipeline severed |

## Adding an entry

1. Append `## <next number>. <title> (backend/frontend/…)` to the matching module file; keep the
   shape **Symptom → Root cause → Fix → Tests** (optionally **Regression** / **Similar-issue sweep**).
2. Add a row at the end of the "Full index" table below: No. / Module / Title / File.
3. Update the English mirror (`<stem>.en.md`) and `README.en.md` together.
4. If the change alters a production behavior contract and needs human sign-off, also record it in
   [`pending.en.md`](./pending.en.md).

## Full index

| No. | Module | Title | File |
|-----|--------|-------|------|
| §1 | Thinking/Model | OpenAI tool message orphan reference → tool-call infinite loop (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §2 | Memory/Causality | Classified memory tool load failure — dangling import (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §3 | Chat/Bubbles | WebSocket send without retry → silent message loss (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §4 | Chat/Bubbles | Virtual scrolling → new messages not rendered (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §5 | Chat/Bubbles | Stop button changes UI only, never notifies backend (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §6 | Pages/Styling | Native confirm/prompt blocks Qt WebEngine (frontend) | [05-frontend-ui](./05-frontend-ui.en.md) |
| §7 | Pages/Styling | UI used emoji, violating the CSS icon convention | [05-frontend-ui](./05-frontend-ui.en.md) |
| §8 | Chat/Bubbles | New session has no effect (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §9 | Memory/Causality | Unrelated historical memories injected, polluting context (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §10 | Thinking/Model | Interactive wait timeout (ask_user_intent / approvals) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §11 | Memory/Causality | Approval result not returned to model + blackboard lifecycle issues | [02-backend-memory](./02-backend-memory.en.md) |
| §12 | Realtime | Bare frontend fetch paths all 404 under the 8765 static proxy (frontend) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §13 | Config/Security | settings double @property → multi-agent thinking crash (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §14 | Pages/Styling | Session graph return_to node has empty tier → nodes/edges dropped by layout (frontend/backend) | [05-frontend-ui](./05-frontend-ui.en.md) |
| §15 | Process/Docs | Supplementary historical lessons (previously unrecorded) | [09-process-docs](./09-process-docs.en.md) |
| §16 | Memory/Causality | Blackboard shared memory: role alias mismatch (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §17 | Chat/Bubbles | Bugs already fixed during the old-JS → Vue migration | [04-frontend-chat](./04-frontend-chat.en.md) |
| §18 | Config/Security | Global settings toggle was decorative (backend/frontend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §19 | Thinking/Model | Automated-replacement NameError at runtime + duplicate index name (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §20 | Testing/CI | Test fakes diverged from real model fields (backend) | [08-testing-ci](./08-testing-ci.en.md) |
| §21 | Thinking/Model | Proactive-outreach bypasses global/session switches (backend) + tests polluting prod DB | [01-backend-thinking](./01-backend-thinking.en.md) |
| §22 | Pages/Styling | Full-button audit methodology + startup shortcut `shortcut_keys` was decorative (frontend/backend) | [05-frontend-ui](./05-frontend-ui.en.md) |
| §23 | Pages/Styling | Two root causes of "details button does nothing": DOM position + broken build (frontend) | [05-frontend-ui](./05-frontend-ui.en.md) |
| §24 | Realtime | send_json_from_thread self-deadlock inside the event-loop thread → conversation errors (backend) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §25 | Thinking/Model | Utility-style one-shot LLM calls got agent personas forced onto them (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §26 | Chat/Bubbles | Chat attachments "won't send": three contract breaks in a chain + missing test infrastructure (frontend/backend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §27 | Testing/CI | macOS dual libomp segfault/abort + test-vs-production environment audit (backend/environment) | [08-testing-ci](./08-testing-ci.en.md) |
| §28 | Config/Security | Unauthenticated WebSocket + 0.0.0.0 binding + execution_mode injection + approval-future disconnect leak (security audit critical) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §29 | Config/Security | config/settings.py missing `import sys` → latent NameError (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §30 | Testing/CI | ScreenMonitor background-thread leftovers → random hangs in full pytest runs (backend/testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §31 | Testing/CI | test_detect_text OCR ordering pollution — sys.modules set to None at module level (backend/testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §32 | Testing/CI | BytesIO internal buffer invisible to pympler — leak-detection blind spot (test infra) | [08-testing-ci](./08-testing-ci.en.md) |
| §33 | Testing/CI | Local proxy fake-ip resolves example.com to a private IP → SSRF protection false positive (test env) | [08-testing-ci](./08-testing-ci.en.md) |
| §34 | Testing/CI | test_api version assertion outdated (2.0.0 vs actual version) (testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §35 | Testing/CI | macOS forbids resource.setrlimit(RLIMIT_AS) below current usage (test infra) | [08-testing-ci](./08-testing-ci.en.md) |
| §36 | Testing/CI | sys.getallocatedobjects() removed in Python 3.13 (test infra) | [08-testing-ci](./08-testing-ci.en.md) |
| §37 | Testing/CI | EventStore.__del__ triggers a GC infinite loop on a fake faiss index (backend/testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §38 | Process/Docs | Systematic bug-document audit — 2 similar issues found and fixed | [09-process-docs](./09-process-docs.en.md) |
| §39 | Thinking/Model | mypy fix introduces a regression: input_controller idempotency broken (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §40 | Memory/Causality | Deep recall lacks a noise guard → irrelevant queries fabricate causal chains (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §41 | Memory/Causality | Cross-scenario event pollution of corroboration + self-reinforcing incremental linking (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §42 | Memory/Causality | Explicit-attribution guard over-kills same-chain events (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §43 | Memory/Causality | Single-node causal-chain noise and repetitive flooding (backend/display) | [02-backend-memory](./02-backend-memory.en.md) |
| §44 | Testing/CI | Random CI full-suite flake — `set_event_loop(None)` residual pollution (testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §45 | Testing/CI | Random CI full-suite flake — `/tools` route assertion (testing, root cause not reproduced) | [08-testing-ci](./08-testing-ci.en.md) |
| §104 *(renumbered)* | Realtime | Frontend cannot reach backend: IPv6 localhost trap + test blind spot in the frontend proxy layer (frontend/backend) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §46 | Thinking/Model | Model client caches stale config → changing API URL/Key has no effect (backend/missing hot-reload) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §47 | Thinking/Model | Pure-chat persona ignores orchestration active status → disabled agent's persona force-applied (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §48 | Thinking/Model | Similar-issue sweep: systematic audit of hardcoded persona selection / config caching | [01-backend-thinking](./01-backend-thinking.en.md) |
| §49 | Memory/Causality | Psychological-activity (conscience) dialog cache accumulates across sessions (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §50 | Memory/Causality | Causal graph doesn't follow memory libraries → causal knowledge pollutes across libraries (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §51 | Memory/Causality | Psychological activity displays chain-of-thought instead of inner monologue (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §105 *(renumbered)* | Memory/Causality | Psychological-activity box disappears after switching sessions — mental events not persisted (frontend/backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §52 | Testing/CI | CI test-failure triage: missing PyQt6 environment + tests depending on real user config (testing/environment) | [08-testing-ci](./08-testing-ci.en.md) |
| §53 | Testing/CI | Config-fingerprint rebuild constructs a real client in test environments → `API key 不能为空` (backend/testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §54 | Testing/CI | CI `/tools` route assertion failure — fastapi 0.141 lazy `_IncludedRouter` (testing) | [08-testing-ci](./08-testing-ci.en.md) |
| §55 | Testing/CI | Dependency version drift — CI installs latest, local stays old (`>=` pins nothing) | [08-testing-ci](./08-testing-ci.en.md) |
| §106 *(renumbered)* | Memory/Causality | Environment perception not injected into the LLM context — dd1ee8b refactor regression (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §56 | Config/Security | Persona has two storage sources + feedback-loop node set not session-isolated (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §107 *(renumbered)* | Memory/Causality | File-perception feature effectively dead — collection-to-consumer pipeline severed (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §57 | Thinking/Model | Thinking-mode tool loop reports 400: assistant message doesn't return reasoning_content (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §58 | Testing/CI | socket/subprocess tests falsely killed by the 20s timeout in CI full runs (testing/CI) | [08-testing-ci](./08-testing-ci.en.md) |
| §59 | Thinking/Model | delegate_task's wait_seconds parsed but never forwarded — supervisor-set subordinate timeout always falls back to 300s (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §60 | Thinking/Model | think_once's outer 120s nested timeout overrides the runner's THINK_TIMEOUT — the supervisor-set timeout gets swallowed (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §61 | Thinking/Model | Delegation chains unreconstructable + think context lost after timeout (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §62 | Thinking/Model | thinking_result's two production paths disagree on delegation_id semantics — RuntimeExpert path uses task_id, blackboard delegation chain finds nothing (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §63 | Thinking/Model | start_runner's delegation-node record overwrites delegate dispatch's record — delegation role name replaced by identity.role (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §64 | Thinking/Model | The resume-thinking "continue from interruption" instruction accumulates across repeated timeout retries (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §65 | Memory/Causality | Blackboard final_response set but never persisted — final reply lost after restart (backend) | [02-backend-memory](./02-backend-memory.en.md) |
| §66 | Testing/CI | Test directly tampers with the global singleton ToolRegistry._tools — 69 failures in full CI runs (test pollution) | [08-testing-ci](./08-testing-ci.en.md) |
| §67 | Thinking/Model | New control tool read_context wrongly added to all tiers — expert privilege overreach (backend/testing) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §68 | Realtime | Todo panel's 3-second polling → WS event push + pull-on-demand (frontend/backend) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §69 | Chat/Bubbles | Frontend context-usage display reads low — context_tokens excludes tool-call history (backend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §70 | Thinking/Model | Model call failure: 'NoneType' object is not iterable —— SSE stream delta.tool_calls is null (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §71 | Chat/Bubbles | Extra standalone "Thinking" bubbles appear after switching sessions (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §72 | Chat/Bubbles | Runtime vs restore path inconsistency (similar-issue sweep) — expert bubble count / thinking-area attribute name (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §73 | Chat/Bubbles | Restore-path tool traces double-counted + approval text polluting the thinking area (frontend/backend, §71 class) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §74 | Chat/Bubbles | Architectural root cure: runtime and restore share one classification rule (frontend refactor) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §75 | Pages/Styling | AI ordered-list output overflows the bubble boundary (frontend styling) | [05-frontend-ui](./05-frontend-ui.en.md) |
| §76 | Thinking/Model | After long idle periods: "Task was destroyed but it is pending!" (leftover asyncio Event.wait, backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §77 | Config/Security | Redundant manual API-Key input on the authorization settings page (frontend, security-exposure anti-pattern) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §78 | Config/Security | Authorization settings tab's runtime config table displays model API Keys in plaintext (frontend, §77 class) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §79 | Thinking/Model | Delegation role-name resolution hardcoded (delegation_port ROLE_TO_IDENTITY, backend, §77-class hazard) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §80 | Thinking/Model | "Thinking Ns" timer counts from session connection, not the current task (backend api_stream) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §81 | Testing/CI | Vercel deployment failure: pyaudio compilation requires portaudio.h (server/headless environment dependency governance) | [08-testing-ci](./08-testing-ci.en.md) |
| §82 | Realtime | Multi-session parallelism: single WS connection + global processing state caused cross-session interference (frontend ws client / chat store / Chat.vue) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §83 | Thinking/Model | After disabling a supervisor/expert on the orchestration page, the System Prompt still stitched in their context (capability tables didn't filter enabled state) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §84 | Thinking/Model | After switching the commander on the orchestration page, startup still targeted orchestrator and was refused by the toggle (orchestrator hardcoded identity) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §85 | Chat/Bubbles | Multiple utterances by the same AI merged into one bubble (frontend expert-bubble mechanism) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §86 | Config/Security | personas.yaml contents lost / system-prompt override vanishes after restart (config persistence risk) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §87 | Config/Security | personas.yaml concurrent writes lose updates — no process-level lock (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §88 | Config/Security | settings.json concurrent writes lose updates (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §89 | Config/Security | delete_custom_agent cascades four independent read-modify-writes, losing data (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §90 | Config/Security | _save_personas_yaml swallows exceptions — callers assume success (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §91 | Chat/Bubbles | switchToSession replay path merges multiple outputs of the same identity (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §92 | Thinking/Model | _session_dialog_history concurrent overwrite — two requests on one session clobber each other (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §93 | Thinking/Model | TurnContext fragments overwritten by source — same-source different-content lost (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §94 | Chat/Bubbles | thinking-content substring dedup over-kills — short text swallowed by longer containing text (frontend) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §95 | Thinking/Model | Thinking timeout charged the whole round — tool execution ate the budget, UI silent for minutes (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §96 | Config/Security | Cross-process config write race — a thread lock cannot protect multiple processes (backend) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §97 | Thinking/Model | plan mode blocked read-only delegation too — empty keyword list still returned a block (backend) | [01-backend-thinking](./01-backend-thinking.en.md) |
| §98 | Realtime | New events only worked in the browser — TUI saw no tools, SSE buffered until the end, replay mis-ordered a round (cross-client) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §99 | Realtime | SSE saw no tool/streaming frames; TUI did not accept the answerable frame (backend + TUI) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §100 | Realtime | SSE sessions treated as "frontend disconnected": handshake skipped the LLM turn, pushes only targeted WS, disconnect left state behind (backend) | [06-realtime-streaming](./06-realtime-streaming.en.md) |
| §101 | Chat/Bubbles | Replay merged multiple supervisor/expert outputs into one bubble — `entry_type` was not persisted (frontend + backend, §85/§91) | [04-frontend-chat](./04-frontend-chat.en.md) |
| §102 | Config/Security | `settings.json` parse failure silently became `{}` — one save wiped all config (backend, §88 leftover) | [03-backend-config-security](./03-backend-config-security.en.md) |
| §103 | Pet | Desktop pet "frontend receives no messages": proxy leaks the upstream SSE + native tool_calls missing the assistant echo (frontend proxy + pet loop) | [07-desktop-pet](./07-desktop-pet.en.md) |
