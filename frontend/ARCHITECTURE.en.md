# Cortex Agent Frontend Architecture

**Language**: [English](./ARCHITECTURE.en.md) | [简体中文](./ARCHITECTURE.md)

> Vue 3 + Pinia + Vite, no TypeScript, no UI framework; tests use Vitest + @vue/test-utils
> 48 source files: 17 components, 15 pages, 6 Stores, 4 Composables, 3 Utils; tests in src/**/*.spec.js

---

## Directory Structure

```
frontend/
├── css/                          # Global CSS three-layer architecture
│   ├── theme.css                 # Design tokens (colors/shadows/spacing/animations/z-index)
│   ├── layout.css                # App Shell (sidebar/main/statusbar/responsive)
│   └── components.css            # Reusable component styles (~600 lines)
├── src/
│   ├── main.js                   # Startup: Pinia + Router + auto-detect API Key
│   ├── App.vue                   # Root: Sidebar + KeepAlive router-view + StatusBar + Toast + DialogHost
│   ├── router.js                 # Hash routing + health-check guard
│   ├── api.js                    # Unified API client + all endpoint definitions
│   ├── assets/hljs-dark.css      # highlight.js dark theme
│   ├── components/               # 17 reusable components
│   ├── composables/              # 4 composables
│   ├── pages/                    # 15 page components
│   ├── stores/                   # 6 Pinia Stores
│   ├── utils/                    # 3 utility functions
│   └── ws/                       # WebSocket layer
│       ├── client.js             # WsClient class (connect/reconnect/send/events)
│       └── store.js              # Pinia Store wrapping WsClient state
```

---

## Tech Stack

| Category | Technology | Version |
|------|------|------|
| Framework | Vue 3 (Composition API `<script setup>`) | ^3.5.13 |
| Routing | Vue Router (Hash mode) | ^4.5.0 |
| State management | Pinia | ^2.3.1 |
| Markdown | markdown-it + highlight.js | ^14.1.0 / ^11.11.1 |
| Build | Vite | ^6.3.5 |

**Key constraint:** Qt WebEngine environment; `window.confirm/prompt` are not supported (they block the JS engine)

---

## Routing

**Mode:** `createWebHashHistory`

| Path | Component | KeepAlive | Health Check |
|------|------|-----------|----------|
| `/` | Redirect to `/chat` | - | - |
| `/chat` | Chat.vue | ✅ | - |
| `/dashboard` | Dashboard.vue | - | ✅ |
| `/modules` | Modules.vue | - | ✅ |
| `/memory` | Memory.vue | - | ✅ |
| `/outreach` | Outreach.vue | - | - |
| `/tasks` | ScheduledTasks.vue | - | - |
| `/skills` | Skills.vue | - | - |
| `/causal` | Causal.vue | - | ✅ |
| `/graph` | Graph.vue | - | - |
| `/orchestration` | Orchestration.vue | - | - |
| `/tools` | Tools.vue | - | ✅ |
| `/security` | Security.vue | - | ✅ |
| `/perception` | Perception.vue | - | ✅ |
| `/system` | System.vue | - | ✅ |
| `/settings` | Settings.vue | - | ✅ |

**Route guard:** health-check pages poll `/api/health` (every 2s, up to 60s); on timeout they jump to `/chat`
**Error recovery:** `router.onError()` automatically reloads when a dynamic import fails (fixes the stale-chunk problem after rebuilds)

---

## State Management (Pinia Stores)

### chat.js — Core chat state
```
State:  messages[], processing, currentModel, streamingIdx, hint, elapsed, runners[], pendingThinking
Actions: init(), switchToSession(sid), addMessage(), sendMessage(), stop(), deleteMessageAt(), editMessageAt()
         addApproval()/approve() — security approval flow
         addIntent()/answerIntent() — model-asks-user flow
```

### session.js — Session CRUD
```
State:  sessionId, sessions[], currentTitle
Actions: loadSessions(), createSession(), switchSession(sid), deleteSession(sid)
```

### config.js — Backend configuration
```
State:  config{}, modelStatus{}
Actions: loadConfig(), loadModelStatus(), updateConfig(k, v)
```

### health.js — Health monitoring
```
State:  status ('checking'|'healthy'|'degraded'|'offline'), moduleCount, backendText
Actions: check(), startPolling(15s), stop()
```

### theme.js — Theme switching
```
State:  theme, isDark (computed)
Actions: init() (reads localStorage), toggle(), apply() (sets data-theme)
```

### toast.js — Notification queue
```
State:  toasts[]
Actions: show(msg, type) (auto-dismisses after 3.5s), dismiss(id)
```

---

## API Layer (`api.js`)

**Base URL:** `/api` (Vite proxy → `http://localhost:8080`)
**Features:** automatic API Key detection, Trace ID injection (`X-Trace-Id` + `X-Request-Seq`), auto toast on 401/403

### Endpoint groups

| Module | Endpoint | Description |
|------|------|------|
| **Health** | `GET /health` | Health check |
| **Dashboard** | `GET /management/dashboard` | Dashboard data |
| **Modules** | `GET /management/modules` | Module list |
| | `POST /management/modules/{name}/refresh` | Refresh module |
| **Memory** | `GET /management/memory/events` | Memory query |
| | `POST /management/memory/events` | Create memory |
| | `DELETE /management/memory/events/{id}` | Delete memory |
| **Causal** | `GET /management/causal-graph` | Causal graph data |
| | `GET /management/causal-graph/{id}` | Node details |
| **Perception** | `GET /management/perception` | Perception status |
| | `POST /management/perception/start\|stop` | Start/stop control |
| **Tools** | `GET /tools/` | Tool list |
| | `POST /tools/call` | Invoke tool |
| **Security** | `GET /security/status` | Security status |
| | `POST /security/switch` | Toggle protection |
| **Config** | `GET /config` | Read config |
| | `PUT /config/{key}` | Write config |
| | `GET /config/personas` | Role personas |
| | `PUT /config/persona/{role}` | Update persona |
| **Sessions** | `GET /stream/sessions` | Session list |
| | `POST /stream/session` | Create session |
| | `DELETE /stream/session/{id}` | Delete session |
| | `PUT /stream/session/{id}/title` | Rename |
| **Outreach** | `GET /stream/session/{id}/outreach-config` | Outreach config |
| | `PUT /stream/session/{id}/outreach-config` | Save config |
| **MemoryLibs** | `GET /config/memory-libs` | Memory library list |
| | `POST /config/memory-libs` | Create memory library |

---

## WebSocket Architecture

### Connection
- **Address:** `ws://{host}:8080/stream/ws/{sessionId}` (direct backend connection, bypassing the Qt proxy)
- **Retry:** up to 3 attempts, exponential backoff (1s → 2s → 4s)
- **Timeout:** 8s connect timeout
- **Watchdog:** checks every 2s; processing while disconnected → reset + toast

### Event flow

```
Frontend sends:
  send('user_input', { content, model, attachments })
  send('stop')

Backend pushes:
  thinking    → accumulates thinking text, detects security approvals / user-intent requests
  message     → final AI reply or mental activity
  mental      → mental activity (reuses the message handler)
  done        → processing complete
  error       → error
  ack         → message acknowledgement (backfills message_id, handles busy state)
  status      → thinking progress (elapsed + runner status tree)
  proactive   → proactive outreach message
```

### Runner status tree
```
thinking_progress → parsed into:
  large (root model)
    ├── supervisor-1
    │   ├── expert-1
    │   └── expert-2
    └── supervisor-2
        └── expert-3

Status enum: thinking | tool_loop | waiting_delegation | completed | error | idle
```

---

## Three-Layer CSS Architecture

### Layer 1: `theme.css` — Design tokens
```css
:root {
  /* Colors */
  --bg-primary: #f9f9f9;        --bg-secondary: #ffffff;
  --text-primary: #111111;      --text-secondary: #5f6368;
  --accent: #10a37f;            --accent-hover: #0d9273;
  --success: #22c55e;           --warning: #d97706;           --danger: #ef4444;
  --purple: #8b5cf6;

  /* Shadows */  --shadow-xs ~ --shadow-xl
  /* Spacing */  --space-2xs(2px) ~ --space-4xl(96px)
  /* Radius */  --radius-sm(4px) ~ --radius-full(9999px)
  /* Animations */  --duration-fast(100ms) ~ --duration-slow(300ms)
  /* z-index */ --z-sidebar(100) ~ --z-toast(1100)
}
[data-theme="dark"] { /* dark-mode overrides */ }
```

### Layer 2: `layout.css` — App Shell
```
.app-shell (flex column, 100vh)
├── .sidebar (220px fixed, flex column)
│   ├── .sidebar-header (logo + title)
│   └── .sidebar-nav (groups + nav items)
├── .app-body (flex row, flex:1)
│   ├── .main-content (flex column)
│   │   ├── .page-header (sticky, bottom gradient line)
│   │   └── .page-body (scrollable, fadeIn)
│   └── .statusbar (32px fixed bottom)
```

**Responsive:** at `@media (max-width: 768px)` the sidebar becomes a floating overlay; at `@media (max-width: 480px)` a compact layout applies

### Layer 3: `components.css` — Component styles (~600 lines)
```
Generic: .card, .badge, .btn, .input, .alert, .data-table, .modal, .toggle-switch
Chat: .message, .message-bubble, .avatar-*, .chat-input-area, .code-block
Settings: .settings-*, .setting-row, .seg (segmented control)
Thinking: .think-*, .st-* (status colors), .chain-* (causal chain)
Others: .stat-card, .health-ring, .pipeline-card, .tool-item, .todo-*, .outreach-*
```

---

## Component Dependency Tree

```
App.vue
├── Sidebar.vue (navigation)
├── <router-view> (KeepAlive: Chat)
│   ├── Chat.vue
│   │   ├── SessionList.vue (session list)
│   │   ├── ModelSelector.vue (model selection)
│   │   ├── ChatMessage.vue (message rendering)
│   │   │   └── CodeBlock.vue (code block)
│   │   ├── ChatInput.vue (input box)
│   │   ├── ThinkingIndicator.vue (thinking animation)
│   │   ├── ThinkingStatusPanel.vue (thinking status tree)
│   │   │   └── RunnerNode.vue (recursive tree node)
│   │   └── SessionSettings.vue (session settings)
│   ├── Dashboard.vue (dashboard)
│   ├── Memory.vue (memory management)
│   ├── Outreach.vue (proactive outreach)
│   ├── ScheduledTasks.vue (scheduled tasks)
│   ├── Skills.vue (skills management)
│   ├── Causal.vue (causal graph)
│   ├── Graph.vue (knowledge graph)
│   ├── Orchestration.vue (orchestration)
│   ├── Tools.vue (tool management)
│   ├── Security.vue (security audit)
│   ├── Perception.vue (perception system)
│   ├── System.vue (system info)
│   ├── Modules.vue (module management)
│   └── Settings.vue (global settings)
├── StatusBar.vue (bottom status bar)
├── Toast.vue (notifications)
├── DialogHost.vue (non-blocking dialogs)
├── ErrorBoundary.vue (error boundary)
└── LoadingState.vue (loading states)
```

---

## Composables

| Function | Purpose | Key implementation |
|------|------|----------|
| `useDialog()` | Non-blocking confirm/prompt | Promise + reactive state, replaces window.confirm (Qt blocking issue) |
| `useWakeLock()` | Keep screen awake | Wake Lock API, works with the `prevent_sleep` config |
| `useGeolocation()` | Geolocation | Geolocation API, works with the `allow_geolocation` config |

---

## Utils

| File | Exports | Purpose |
|------|------|------|
| `escape.js` | `escapeHtml(s)` | HTML entity escaping (DOM textContent) |
| `format.js` | `formatTime(ts)` | Chinese-style time formatting `HH:MM:SS` |
| `markdown.js` | `renderMarkdown()`, `parseMarkdownSegments()`, `copyCodeBlock()` | markdown-it + highlight.js pipeline |

---

## Key Design Patterns

### 1. KeepAlive + route guards
```js
// router.js — all pages lazy-imported
{ path: '/chat', component: () => import('./pages/Chat.vue') }

// App.vue — Chat is cached
<KeepAlive :include="['Chat']"><router-view /></KeepAlive>

// Chat.vue — defineOptions + onActivated
defineOptions({ name: 'Chat' })
onActivated(() => session.loadSessions())  // refresh when switching back
```

### 2. Non-blocking dialogs (Qt compatibility)
```js
// composables/useDialog.js
const { confirm, prompt } = useDialog()
const ok = await confirm('Confirm delete?')  // returns Promise<boolean>
```

### 3. Automatic API Key detection
```js
// main.js at startup
autoDetectApiKey()  // GET /config/api-key → localStorage + sessionStorage
// api.js attaches the X-API-Key header automatically on every request
```

### 4. Batched rendering (performance)
```js
// Chat.vue — only the latest 50 messages are rendered initially
const RENDER_LIMIT = 50
const visibleMessages = computed(() =>
  chat.messages.slice(Math.max(0, chat.messages.length - renderLimit.value))
)
```

### 5. Trace ID tracing
```js
// Every API request: X-Trace-Id (UUID) + X-Request-Seq (incrementing)
// Every WS message: trace_id + trace_seq
// Used to correlate frontend/backend logs
```

### 6. Thinking pipeline visualization
```
Backend thinking_progress event → parse the runner tree
→ ThinkingStatusPanel renders the hierarchy:
   Large model (root) → Supervisors → Experts
→ RunnerNode recursive component, different color per level
```

### 7. Error handling strategy
- API: try/catch + toast notification
- WS: reconnect with exponential backoff (3 attempts)
- Rendering: ErrorBoundary.vue catch + retry button
- Routing: health-check guard + timeout fallback
- Dynamic import: automatic reload on failure

---

## New Page Checklist

1. Create `<Name>.vue` in `pages/`, using `<script setup>`
2. Add the route in `router.js` (lazy import)
3. If a health check is needed, add it to the `healthPaths` array
4. Use CSS classes instead of inline styles (prefer theme.css variables)
5. Make API calls through the `endpoints` object of `api.js`
6. Use `useDialog()` instead of `window.confirm` for dialogs
7. For polling use `setInterval` + clean up in `onBeforeUnmount`
8. Page titles go inside `<div class="page-header"><h2>`

## New Component Checklist

1. Create under `components/`, using `<script setup>`
2. Props via `defineProps()`, events via `defineEmits()`
3. Prefer global CSS classes for styling (components.css)
4. When scoped styles are needed, put them in `<style scoped>`
5. Use CSS variables for colors, never hardcode
6. Icons via `<Icon name="xxx" :size="16" />`

## CSS Guidelines

1. **Colors:** always use `var(--accent)`, `var(--text-primary)`, etc.; never write `#xxx`
2. **Spacing:** use `var(--space-*)` or `var(--radius-*)`
3. **Animations:** use `var(--duration)` + `var(--ease-in-out)`
4. **z-index:** use the `var(--z-*)` layer variables
5. **Component styles:** write them in `components.css` (global) or `<style scoped>` (local)
6. **New pages:** use `<style scoped>`, class naming `{page}-{element}` (e.g. `.dash-grid-2`)

---

*Last updated: 2026-08-10*
