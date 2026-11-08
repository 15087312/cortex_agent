# Cortex Agent — Web UI

**Language**: [English](./README.en.md) | [简体中文](./README.md)

Web console of Cortex Agent, containing 15 feature pages (Chat/Orchestration/Settings/Dashboard/Memory/Skills/Tools/Scheduled Tasks/Outreach/Perception/Security/System/Graph/Causal Graph/Modules), connecting to the backend via WebSocket.

## How to Start

### macOS (recommended)

```bash
# One-click start (double-click or run in terminal)
./frontend/start.command
```

Or start manually:
```bash
# Make sure the backend is already running on localhost:8080
# Start the Qt desktop client
python frontend/main.py
```

### Browser access (no Qt)

```bash
python frontend/server.py
# Open http://localhost:8765
```

### Windows

Double-click `frontend/start.bat`

## Feature Pages

| Page | Function |
|------|------|
| Chat | Real-time WebSocket chat, multi-model selection |
| Dashboard | System overview |
| Modules | Module status viewer |
| Memory | Event memory CRUD |
| Causal Graph | Causal relationship visualization |
| Tools | Tool registration and invocation |
| Security | Security policies and audit logs |
| Perception | Sensor status monitoring |
| Session Monitor | Active session list |
| System | System status and configuration |
| Settings | API Key and runtime configuration |

## UI interaction / visual smoke testing

Vitest (jsdom) covers logic and DOM state. **Real CSS, visible button feedback, computed styles, and browser media APIs** are covered by Playwright smoke tests.

- Run: `cd frontend && npm run test:e2e` (first run `npx playwright install chromium`)
- Tests live in `frontend/e2e/*.spec.js`; the minimal harness loads the real `theme.css` / `layout.css` / `components.css`
- When adding a class toggled by JS (e.g. `chat-btn-active`):
  1. it must have a matching CSS rule;
  2. add a Playwright assertion for the class plus a `getComputedStyle` change;
  3. asserting `classList` alone is not enough — jsdom stays green even when the style is missing.
- Real microphone/camera permissions cannot be reproduced in CI: inject a fake device with `addInitScript` to verify UI state; verify real QtWebEngine/macOS permissions manually on the desktop.
- E2E must assert that a click produces a user-perceivable change, not just the final request payload.

## Layout overflow guardrails

- Length values in dynamic Vue `:style` bindings must include units (for example `'260px'` or `'50%'`); never pass a bare number. Only unitless properties such as `opacity`, `zIndex`, `flexShrink`, and `lineHeight` may take numbers.
- Add `min-width: 0` to text, input, and shrinkable-panel flex children. Put the ellipsis trio on the element that is actually constrained, and make sure every intermediate flex parent can shrink too.
- Inputs in flex rows should use `flex: 1 1 0; width: 0; min-width: 0`; cap right-side hints/buttons with `max-width` so hint text cannot claim space at max-content width.
- Fixed-width sidebars need `width`, `max-width`, `flex-shrink: 0`, and `min-width: 0` together.
- After layout changes, verify `scrollWidth` and `getBoundingClientRect()` in a real browser at 1440 / 1024 / 800 widths, not only in jsdom unit tests.

Full case study: [`docs/bugfixes/05-frontend-ui.en.md` §108](../docs/bugfixes/05-frontend-ui.en.md).

## WebSocket Protocol

```
Connect: ws://localhost:8080/stream/ws/{session_id}
Create session: POST /api/stream/session → {session_id}

Send: {"type": "input", "content": "消息内容"}
Stop: {"type": "stop"}
