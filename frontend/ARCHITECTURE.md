# Cortex Agent Frontend Architecture

> Vue 3 + Pinia + Vite, 无 TypeScript, 无 UI 框架；测试用 Vitest + @vue/test-utils
> 48 个源文件: 17 组件, 15 页面, 6 Store, 4 Composable, 3 Utils；测试见 src/**/*.spec.js

---

## 目录结构

```
frontend/
├── css/                          # 全局 CSS 三层架构
│   ├── theme.css                 # 设计令牌 (颜色/阴影/间距/动画/z-index)
│   ├── layout.css                # App Shell (sidebar/main/statusbar/响应式)
│   └── components.css            # 可复用组件样式 (~600行)
├── src/
│   ├── main.js                   # 启动: Pinia + Router + 自动检测 API Key
│   ├── App.vue                   # 根: Sidebar + KeepAlive router-view + StatusBar + Toast + DialogHost
│   ├── router.js                 # Hash 路由 + 健康检查守卫
│   ├── api.js                    # 统一 API 客户端 + 全部端点定义
│   ├── assets/hljs-dark.css      # highlight.js 暗色主题
│   ├── components/               # 17 可复用组件
│   ├── composables/              # 4 个组合函数
│   ├── pages/                    # 15 个页面组件
│   ├── stores/                   # 6 个 Pinia Store
│   ├── utils/                    # 3 个工具函数
│   └── ws/                       # WebSocket 层
│       ├── client.js             # WsClient 类 (连接/重连/发送/事件)
│       └── store.js              # Pinia Store 封装 WsClient 状态
```

---

## 技术栈

| 分类 | 技术 | 版本 |
|------|------|------|
| 框架 | Vue 3 (Composition API `<script setup>`) | ^3.5.13 |
| 路由 | Vue Router (Hash 模式) | ^4.5.0 |
| 状态管理 | Pinia | ^2.3.1 |
| Markdown | markdown-it + highlight.js | ^14.1.0 / ^11.11.1 |
| 构建 | Vite | ^6.3.5 |

**关键约束:** Qt WebEngine 环境, 不支持 `window.confirm/prompt` (阻塞 JS 引擎)

---

## 路由

**模式:** `createWebHashHistory`

| 路径 | 组件 | KeepAlive | 健康检查 |
|------|------|-----------|----------|
| `/` | 重定向到 `/chat` | - | - |
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

**路由守卫:** 健康检查页面会轮询 `/api/health` (每 2s, 最长 60s), 超时跳转 `/chat`
**错误恢复:** `router.onError()` 在动态 import 失败时自动 reload (解决构建后旧 chunk 问题)

---

## 状态管理 (Pinia Stores)

### chat.js — 核心对话状态
```
State:  messages[], processing, currentModel, streamingIdx, hint, elapsed, runners[], pendingThinking
Actions: init(), switchToSession(sid), addMessage(), sendMessage(), stop(), deleteMessageAt(), editMessageAt()
         addApproval()/approve() — 安全审批流
         addIntent()/answerIntent() — 模型提问用户流
```

### session.js — 会话 CRUD
```
State:  sessionId, sessions[], currentTitle
Actions: loadSessions(), createSession(), switchSession(sid), deleteSession(sid)
```

### config.js — 后端配置
```
State:  config{}, modelStatus{}
Actions: loadConfig(), loadModelStatus(), updateConfig(k, v)
```

### health.js — 健康监控
```
State:  status ('checking'|'healthy'|'degraded'|'offline'), moduleCount, backendText
Actions: check(), startPolling(15s), stop()
```

### theme.js — 主题切换
```
State:  theme, isDark (computed)
Actions: init() (读 localStorage), toggle(), apply() (设置 data-theme)
```

### toast.js — 通知队列
```
State:  toasts[]
Actions: show(msg, type) (3.5s 自动消失), dismiss(id)
```

---

## API 层 (`api.js`)

**Base URL:** `/api` (Vite proxy → `http://localhost:8080`)
**特性:** API Key 自动检测, Trace ID 注入 (`X-Trace-Id` + `X-Request-Seq`), 401/403 自动 toast

### 端点分组

| 模块 | 端点 | 说明 |
|------|------|------|
| **Health** | `GET /health` | 健康检查 |
| **Dashboard** | `GET /management/dashboard` | 仪表盘数据 |
| **Modules** | `GET /management/modules` | 模块列表 |
| | `POST /management/modules/{name}/refresh` | 刷新模块 |
| **Memory** | `GET /management/memory/events` | 记忆查询 |
| | `POST /management/memory/events` | 创建记忆 |
| | `DELETE /management/memory/events/{id}` | 删除记忆 |
| **Causal** | `GET /management/causal-graph` | 因果图数据 |
| | `GET /management/causal-graph/{id}` | 节点详情 |
| **Perception** | `GET /management/perception` | 感知状态 |
| | `POST /management/perception/start\|stop` | 启停控制 |
| **Tools** | `GET /tools/` | 工具列表 |
| | `POST /tools/call` | 调用工具 |
| **Security** | `GET /security/status` | 安全状态 |
| | `POST /security/switch` | 开关防护 |
| **Config** | `GET /config` | 读配置 |
| | `PUT /config/{key}` | 写配置 |
| | `GET /config/personas` | 角色人设 |
| | `PUT /config/persona/{role}` | 更新人设 |
| **Sessions** | `GET /stream/sessions` | 会话列表 |
| | `POST /stream/session` | 创建会话 |
| | `DELETE /stream/session/{id}` | 删除会话 |
| | `PUT /stream/session/{id}/title` | 重命名 |
| **Outreach** | `GET /stream/session/{id}/outreach-config` | 搭话配置 |
| | `PUT /stream/session/{id}/outreach-config` | 保存配置 |
| **MemoryLibs** | `GET /config/memory-libs` | 记忆库列表 |
| | `POST /config/memory-libs` | 创建记忆库 |

---

## WebSocket 架构

### 连接
- **地址:** `ws://{host}:8080/stream/ws/{sessionId}` (直连后端, 绕过 Qt proxy)
- **重试:** 最多 3 次, 指数退避 (1s → 2s → 4s)
- **超时:** 8s 连接超时
- **看门狗:** 2s 间隔检查, processing 但断开 → 复位 + toast

### 事件流

```
前端发送:
  send('user_input', { content, model, attachments })
  send('stop')

后端推送:
  thinking    → 累积思考文本, 检测安全审批/用户意图请求
  message     → 最终 AI 回复 或 心理活动
  mental      → 心理活动 (复用 message 处理器)
  done        → 处理完成
  error       → 错误
  ack         → 消息确认 (回填 message_id, 处理 busy 状态)
  status      → 思考进度 (elapsed + runner 状态树)
  proactive   → 主动搭话消息
```

### Runner 状态树
```
thinking_progress → 解析为:
  large (根模型)
    ├── supervisor-1
    │   ├── expert-1
    │   └── expert-2
    └── supervisor-2
        └── expert-3

状态枚举: thinking | tool_loop | waiting_delegation | completed | error | idle
```

---

## CSS 三层架构

### Layer 1: `theme.css` — 设计令牌
```css
:root {
  /* 颜色 */
  --bg-primary: #f9f9f9;        --bg-secondary: #ffffff;
  --text-primary: #111111;      --text-secondary: #5f6368;
  --accent: #10a37f;            --accent-hover: #0d9273;
  --success: #22c55e;           --warning: #d97706;           --danger: #ef4444;
  --purple: #8b5cf6;

  /* 阴影 */  --shadow-xs ~ --shadow-xl
  /* 间距 */  --space-2xs(2px) ~ --space-4xl(96px)
  /* 圆角 */  --radius-sm(4px) ~ --radius-full(9999px)
  /* 动画 */  --duration-fast(100ms) ~ --duration-slow(300ms)
  /* z-index */ --z-sidebar(100) ~ --z-toast(1100)
}
[data-theme="dark"] { /* 暗色覆盖 */ }
```

### Layer 2: `layout.css` — App Shell
```
.app-shell (flex column, 100vh)
├── .sidebar (220px fixed, flex column)
│   ├── .sidebar-header (logo + 标题)
│   └── .sidebar-nav (分组 + 导航项)
├── .app-body (flex row, flex:1)
│   ├── .main-content (flex column)
│   │   ├── .page-header (sticky, 底部渐变线)
│   │   └── .page-body (scrollable, fadeIn)
│   └── .statusbar (32px fixed bottom)
```

**响应式:** `@media (max-width: 768px)` sidebar 变浮动层; `@media (max-width: 480px)` 紧凑布局

### Layer 3: `components.css` — 组件样式 (~600行)
```
通用: .card, .badge, .btn, .input, .alert, .data-table, .modal, .toggle-switch
聊天: .message, .message-bubble, .avatar-*, .chat-input-area, .code-block
设置: .settings-*, .setting-row, .seg (分段控件)
思考: .think-*, .st-* (状态色), .chain-* (因果链)
其他: .stat-card, .health-ring, .pipeline-card, .tool-item, .todo-*, .outreach-*
```

---

## 组件依赖关系

```
App.vue
├── Sidebar.vue (导航)
├── <router-view> (KeepAlive: Chat)
│   ├── Chat.vue
│   │   ├── SessionList.vue (会话列表)
│   │   ├── ModelSelector.vue (模型选择)
│   │   ├── ChatMessage.vue (消息渲染)
│   │   │   └── CodeBlock.vue (代码块)
│   │   ├── ChatInput.vue (输入框)
│   │   ├── ThinkingIndicator.vue (思考中动画)
│   │   ├── ThinkingStatusPanel.vue (思考状态树)
│   │   │   └── RunnerNode.vue (递归树节点)
│   │   └── SessionSettings.vue (会话设置)
│   ├── Dashboard.vue (仪表盘)
│   ├── Memory.vue (记忆管理)
│   ├── Outreach.vue (主动搭话)
│   ├── ScheduledTasks.vue (定时任务)
│   ├── Skills.vue (技能管理)
│   ├── Causal.vue (因果图)
│   ├── Graph.vue (图谱)
│   ├── Orchestration.vue (编排)
│   ├── Tools.vue (工具管理)
│   ├── Security.vue (安全审计)
│   ├── Perception.vue (感知系统)
│   ├── System.vue (系统信息)
│   ├── Modules.vue (模块管理)
│   └── Settings.vue (全局设置)
├── StatusBar.vue (底部状态栏)
├── Toast.vue (通知)
├── DialogHost.vue (非阻塞弹窗)
├── ErrorBoundary.vue (错误边界)
└── LoadingState.vue (加载状态)
```

---

## Composables

| 函数 | 用途 | 关键实现 |
|------|------|----------|
| `useDialog()` | 非阻塞 confirm/prompt | Promise + reactive state, 替代 window.confirm (Qt 阻塞问题) |
| `useWakeLock()` | 屏幕常亮 | Wake Lock API, 配合 `prevent_sleep` 配置 |
| `useGeolocation()` | 定位 | Geolocation API, 配合 `allow_geolocation` 配置 |

---

## Utils

| 文件 | 导出 | 用途 |
|------|------|------|
| `escape.js` | `escapeHtml(s)` | HTML 实体转义 (DOM textContent) |
| `format.js` | `formatTime(ts)` | 中文时间格式化 `HH:MM:SS` |
| `markdown.js` | `renderMarkdown()`, `parseMarkdownSegments()`, `copyCodeBlock()` | markdown-it + highlight.js 管线 |

---

## 关键设计模式

### 1. KeepAlive + 路由守卫
```js
// router.js — 所有页面 lazy import
{ path: '/chat', component: () => import('./pages/Chat.vue') }

// App.vue — Chat 缓存
<KeepAlive :include="['Chat']"><router-view /></KeepAlive>

// Chat.vue — defineOptions + onActivated
defineOptions({ name: 'Chat' })
onActivated(() => session.loadSessions())  // 切回时刷新
```

### 2. 非阻塞对话框 (Qt 兼容)
```js
// composables/useDialog.js
const { confirm, prompt } = useDialog()
const ok = await confirm('确定删除？')  // 返回 Promise<boolean>
```

### 3. API Key 自动检测
```js
// main.js 启动时
autoDetectApiKey()  // GET /config/api-key → localStorage + sessionStorage
// api.js 请求时自动附加 X-API-Key header
```

### 4. 分批渲染 (性能)
```js
// Chat.vue — 首次只渲染 50 条
const RENDER_LIMIT = 50
const visibleMessages = computed(() =>
  chat.messages.slice(Math.max(0, chat.messages.length - renderLimit.value))
)
```

### 5. Trace ID 链路追踪
```js
// 每个 API 请求: X-Trace-Id (UUID) + X-Request-Seq (递增)
// 每个 WS 消息: trace_id + trace_seq
// 用于前后端日志关联
```

### 6. 思考流水线可视化
```
后端 thinking_progress 事件 → 解析 runner 树
→ ThinkingStatusPanel 渲染层级:
   大模型 (根) → 主管 → 专家
→ RunnerNode 递归组件, 各层级不同颜色
```

### 7. 错误处理策略
- API: try/catch + toast 通知
- WS: 指数退避重连 (3 次)
- 渲染: ErrorBoundary.vue 捕获 + 重试按钮
- 路由: 健康检查守卫 + 超时 fallback
- 动态 import: 失败自动 reload

---

## 新页面开发 Checklist

1. 在 `pages/` 创建 `<Name>.vue`, 使用 `<script setup>`
2. 在 `router.js` 添加路由 (lazy import)
3. 如需健康检查, 加入 `healthPaths` 数组
4. 用 CSS 类替代 inline style (优先使用 theme.css 变量)
5. API 调用通过 `api.js` 的 `endpoints` 对象
6. 弹窗用 `useDialog()` 而非 `window.confirm`
7. 轮询用 `setInterval` + `onBeforeUnmount` 清理
8. 页面标题在 `<div class="page-header"><h2>` 中

## 新组件开发 Checklist

1. 在 `components/` 创建, 使用 `<script setup>`
2. Props 用 `defineProps()`, 事件用 `defineEmits()`
3. 样式优先用全局 CSS class (components.css)
4. 需要 scoped style 时放在 `<style scoped>` 中
5. 颜色用 CSS 变量, 不要硬编码
6. 图标用 `<Icon name="xxx" :size="16" />`

## CSS 开发规范

1. **颜色:** 始终用 `var(--accent)`, `var(--text-primary)` 等, 不写 `#xxx`
2. **间距:** 用 `var(--space-*)` 或 `var(--radius-*)`
3. **动画:** 用 `var(--duration)` + `var(--ease-in-out)`
4. **z-index:** 用 `var(--z-*)` 层级变量
5. **组件样式:** 写在 `components.css` (全局) 或 `<style scoped>` (局部)
6. **新页面:** 用 `<style scoped>`, class 命名 `{page}-{element}` (如 `.dash-grid-2`)

---

*最后更新: 2026-08-10*
