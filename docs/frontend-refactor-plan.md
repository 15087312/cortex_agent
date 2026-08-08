# Cortex Agent 前端重构方案

> 创建日期：2026-07-21
> 当前分支：main (37a5ea2)
> 状态：草案

---

## 1. 修改需求

### 1.1 当前架构

```
┌─────────────────────────────────────────────────────┐
│  main.py (PyQt6 QMainWindow + QWebEngineView)       │
│  ├── macOS 菜单栏 / Dock 图标 / 窗口管理             │
│  └── QWebEngineView 加载 http://localhost:8765        │
│                                                       │
│  server.py (Python HTTP 代理)                         │
│  ├── 提供 /frontend 下的静态文件                      │
│  └── 将 /api/* 代理到 backend:8080                    │
│                                                       │
│  index.html + js/ + css/ (SPA)                        │
│  ├── app.js         ← 路由 + 主题 + 健康检查          │
│  ├── api.js          ← fetch 封装                     │
│  ├── ws.js           ← WebSocket 客户端                │
│  ├── components.js   ← 字符串模板                      │
│  ├── pages/chat.js   ← 聊天页面 (643 行)              │
│  ├── pages/*.js      ← 其余 11 个页面                  │
│  └── css/*.css       ← ~415 行 CSS 自定义属性          │
└─────────────────────────────────────────────────────┘
```

### 1.2 核心问题

| # | 问题 | 严重性 | 位置 |
|---|------|--------|------|
| 1 | **字符串模板 XSS** — `UI.e()` 只转义 HTML 实体，onclick 参数可注入任意 JS | 🔴 高危 | `components.js:25,58,62` |
| 2 | **API Key 明文存 localStorage** — 任何 XSS 或本地访问者都能窃取 | 🔴 高危 | `api.js:2` |
| 3 | **函数重复定义** — 第二个 `_scheduleRetry` 覆盖第一个，max retries 静默丢失；`_renderMsgShell` 空气泡覆盖流式光标 | 🔴 高危 | `ws.js:21-30`, `chat.js:250-256` |
| 4 | **WebSocket URL 硬编码** — `ws://localhost:8080`，无法服务非本机部署 | 🟡 中危 | `ws.js:13` |
| 5 | **CSS 变量缺失** — `--font`, `--font-mono`, `--sidebar-width`, `--statusbar-height` 引用未定义 | 🟡 中危 | `theme.css`, `layout.css`, `components.css` |
| 6 | **静默 catch** — 大量 catch 块为空，API 错误在 UI 上无反馈 | 🟡 中危 | `chat.js:28`, `dashboard.js:14,29,35` |
| 7 | **localStorage 无 try/catch** — Safari/Chrome 隐私模式直接崩溃 | 🟡 中危 | `app.js:7,22` |
| 8 | **版本号硬编码** — `v=2`, `v2.0.0` 在 index.html 中，服务器端字节替换脆弱 | 🔵 低危 | `index.html`, `server.py:97` |
| 9 | **重复代码** — `UI.e()` = `App.escapeHtml()`, `UI.time()` = `App.formatTime()` | 🔵 低危 | 多处 |
| 10 | **状态管理散落** — 20+ 个 `this.xxx` 散布在 chat.js 中，无统一状态层 | 🟡 中危 | `chat.js:1-648` |
| 11 | **无错误边界** — 单个页面崩溃拖垮整个应用 | 🟡 中危 | `app.js:53` |
| 12 | **PyQt6 体积** — 仅为一个浏览器外壳占用 523MB | 🔵 低危 | 依赖 |

### 1.3 CLI TUI 对比差距

CLI TUI (`cli_tui/`) 基于 Textual 框架，在以下方面显著优于 Web UI：

- **统一状态管理** — `AppState` 数据类，30+ 字段集中管理
- **错误链追踪** — 错误携带 source/tier，UI 显示根因追溯
- **重试机制** — 超时自动重连 + 2 次全量重试
- **安全审批** — 交互式 Yes/No 审批，键盘快捷键
- **实时调试面板** — 事件时间线、瓶颈分析
- **多模型可视化** — 实时显示总指挥/主管/专家身份
- **命令系统** — 14 个注册命令 + 模糊补全
- **WebSocket 后台监听** — 空闲时持续接收服务端推送

---

## 2. 修改理由

### 2.1 为什么选择 Vue（非 Qt，非纯 JS）

**放弃纯 Qt Widgets 的理由：**

当前应用的核心交互是 **流式 markdown 聊天**——需要实时渲染、代码高亮、打字机效果、图片拖放粘贴。Qt Widgets 对此的支持极差：

| 需求 | Qt Widgets 实现 | Vue 实现 |
|------|----------------|---------|
| Markdown 渲染 | `QTextBrowser.setHtml()` 需自行解析 | `markdown-it` 一行代码 |
| 代码高亮 | 无原生支持，需嵌入 QWebView | `highlight.js` CDN 引入 |
| 流式文本更新 | `QTextCursor.insertText()` 手动维护 | 响应式 `ref('')` 自动渲染 |
| WebSocket | `QWebSocket` + 信号槽，异步管理复杂 | 原生 `WebSocket` + async/await |
| 图片拖放上传 | `QDragEnterEvent` + 文件路径处理 | `<input type="file">` + FileReader |
| 组件化 | 无，QWidget 继承树耦合 | SFC 天然隔离 |
| 状态管理 | 自行实现信号/数据类 | Pinia 内置响应式状态 |
| 开发效率 | 改代码→重启→看结果 | Vite HMR 秒级热更新 |

**放弃当前手写 JS 的理由：**

- 643 行 `chat.js` 包含 20 个散落状态变量 + 27 个手动挂载事件 + 3 种并发模式——全部手动协调
- 字符串模板拼接 = XSS 风险
- 无类型检查，函数重名编译期不报错
- 全局命名空间 `App._pages.chat.*` 脆弱

**放弃 Electron 的理由：**

不需要文件系统访问、不需要原生菜单（可通过 `cortex --qt` 在终端启动）、不需要后台进程。Electron 的 200MB+ 体积和复杂构建链是过度设计。

### 2.2 Vue 带来的收益

- **组件化** — 12 个页面 + 20 个可复用组件，每个 < 200 行
- **响应式状态** — Pinia store 替代 20 个散落 `this.xxx`，状态变化自动触发渲染
- **模板安全** — `@click`、`v-html` 不会产生 XSS
- **HMR 开发** — 修改代码即时反映，无需刷新
- **渐进迁移** — 可以逐个页面从原生 JS 迁到 Vue
- **可选 Qt 外壳** — 保留 `main.py` 作为可选启动方式，Vue 在 QWebEngineView 中运行

### 2.3 维持现状的代价

- 每个新功能都需要手动管理 DOM 和状态
- 流式聊天的并发协调逻辑极易引入竞态
- 现有 12 个 bug（6 个已修复，仍有 XSS、静默 catch、无状态管理等问题）
- 任何新开发者接手都需要通读 1300+ 行全局 JS 代码

---

## 3. 具体要求

### 3.1 功能要求

| # | 功能 | 优先级 | 当前状态 |
|---|------|--------|---------|
| 1 | 流式聊天（WebSocket + markdown + 代码高亮） | P0 | 已有（有 bug） |
| 2 | 会话管理（创建/切换/删除/加载历史） | P0 | 已有（删除无 API 调用） |
| 3 | 仪表盘（系统健康度、模块状态） | P1 | 已有（空 catch，静默失败） |
| 4 | 记忆管理（事件 CRUD + 搜索） | P1 | 已有 |
| 5 | 设置页面（API Key、模型选择、执行模式） | P1 | 已有 |
| 6 | 工具管理（工具列表 + 调用记录） | P2 | 已有 |
| 7 | 安全审计（安全状态 + 日志） | P2 | 已有 |
| 8 | 感知系统（传感器状态） | P3 | 已有 |
| 9 | 会话监控（活跃会话列表） | P3 | 已有 |
| 10 | 多模型可视化（总指挥/主管/专家身份） | P3 | CLI TUI 有，Web 无 |
| 11 | 安全审批工作流 | P3 | CLI TUI 有，Web 无 |
| 12 | 错误链展示 | P3 | CLI TUI 有，Web 无 |

### 3.2 非功能要求

| # | 要求 | 说明 |
|---|------|------|
| 1 | **无 XSS 漏洞** | 所有用户输入必须转义，禁止字符串拼接 onclick |
| 2 | **API Key 安全存储** | 改用 sessionStorage 或 httpOnly cookie |
| 3 | **WebSocket 可配置 URL** | 从 `window.location` 或环境变量读取 |
| 4 | **错误边界** | 单个页面崩溃不影响其他页面 |
| 5 | **状态可追踪** | 使用 DevTools 可查看应用状态 |
| 6 | **渐进可用** | 后端离线时 UI 不崩溃，显示友好提示 |
| 7 | **响应式布局** | 适配 480px-1920px 宽度 |
| 8 | **暗色/亮色模式** | 保留现有主题系统 |
| 9 | **缓存清除** | 构建时自动 hash，移除服务器端字节替换 |
| 10 | **无 PyQt6 硬依赖** | Vue 应可独立在浏览器中运行 |

---

## 4. 架构设计建议

### 4.1 目标架构

```
frontend/
├── index.html                    # 入口 HTML
├── vite.config.js                # 构建配置
├── package.json                  # 依赖声明
├── .env.example                  # 环境变量模板
│
├── src/
│   ├── main.js                   # Vue 应用入口
│   ├── App.vue                   # 根组件（侧边栏 + 路由 + 状态栏）
│   ├── router.js                 # 路由配置
│   │
│   ├── api/
│   │   ├── client.js             # Axios/fetch 封装（自动注入 API Key）
│   │   └── endpoints.js          # 全部 API 端点定义
│   │
│   ├── ws/
│   │   ├── client.js             # WebSocket 连接管理 + 重试
│   │   └── store.js              # Pinia store：消息列表 + 流式状态
│   │
│   ├── stores/
│   │   ├── chat.js               # 聊天状态（会话列表 + 消息 + 流式）
│   │   ├── session.js            # 会话 CRUD
│   │   ├── health.js             # 健康检查轮询
│   │   ├── theme.js              # 主题切换
│   │   └── config.js             # 用户配置
│   │
│   ├── pages/
│   │   ├── Chat.vue              # 聊天页面
│   │   ├── Dashboard.vue         # 仪表盘
│   │   ├── Memory.vue            # 记忆管理
│   │   ├── Modules.vue           # 模块管理
│   │   ├── Sessions.vue          # 会话监控
│   │   ├── Tools.vue             # 工具管理
│   │   ├── Security.vue          # 安全审计
│   │   ├── Perception.vue        # 感知系统
│   │   ├── Causal.vue            # 因果图
│   │   ├── System.vue            # 系统信息
│   │   └── Settings.vue          # 设置
│   │
│   ├── components/
│   │   ├── ChatMessage.vue       # 单条消息（markdown + 操作按钮）
│   │   ├── ChatInput.vue         # 输入框 + 附件拖放
│   │   ├── SessionList.vue       # 会话列表
│   │   ├── ModelSelector.vue     # 模型选择
│   │   ├── ThinkingIndicator.vue # 思考指示器
│   │   ├── Toast.vue             # 通知
│   │   ├── EmptyState.vue        # 空状态
│   │   ├── LoadingState.vue      # 加载态
│   │   ├── ErrorBoundary.vue     # 错误边界
│   │   ├── StatCard.vue          # 统计卡片
│   │   ├── DataTable.vue         # 数据表格
│   │   ├── Modal.vue             # 弹窗
│   │   ├── SearchBar.vue         # 搜索栏
│   │   └── CodeBlock.vue         # 代码块（复制 + 高亮）
│   │
│   └── utils/
│       ├── markdown.js           # markdown-it + highlight.js 配置
│       ├── format.js             # 时间格式化等工具函数
│       └── escape.js             # 安全转义
│
├── public/
│   └── favicon.ico               # 替换 404 的 favicon
│
└── css/
    └── theme.css                 # 保留现有 CSS 变量（可逐步迁移到组件内）
```

### 4.2 页面复杂度预估

| 页面 | 当前行数 | 预估 Vue 行数 | 主要逻辑 |
|------|---------|--------------|---------|
| Chat | 643 | ~350 | WebSocket + markdown + 会话管理 |
| Dashboard | 37 | ~80 | 统计卡片 + 表格 |
| Memory | 73 | ~100 | CRUD 表格 + 搜索 |
| Modules | 36 | ~60 | 列表 + 状态标签 |
| Sessions | 44 | ~60 | 列表 + 操作按钮 |
| Tools | 57 | ~80 | 表格 + 调用记录 |
| Security | 30 | ~50 | 状态 + 日志列表 |
| Perception | 45 | ~60 | 传感器状态卡片 |
| Causal | 49 | ~60 | 图或列表 |
| System | 42 | ~50 | 信息表格 |
| Settings | 48 | ~100 | 表单 + API Key 配置 |
| **合计** | **~1104** | **~1050** | 组件复用降低维护量 |

### 4.3 流式聊天的 Vue 架构

```vue
<!-- 思路：响应式消息列表 + 单条消息组件 -->
<script setup>
import { ref, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from '@/components/ChatMessage.vue'

const store = useChatStore()

// 当前正在流式更新的消息
// store.currentStreaming = { content: ref(''), done: false }
// ChatMessage 内部监听 content 变化，v-html 实时更新
</script>

<template>
  <div class="chat-messages" ref="messagesContainer">
    <ChatMessage
      v-for="(msg, idx) in store.messages"
      :key="msg.id || idx"
      :message="msg"
      :is-streaming="idx === store.streamingIndex"
    />
  </div>
</template>
```

关键区别：
- 不再需要打字机 `setInterval`——Vue 响应式 `ref` 变化自动触发 DOM 更新
- 不再需要手动管理 `_streamingTimer`——`watch` 监听流式内容
- 不再需要手动高亮代码——`onMounted` + `nextTick` 单次调用

### 4.4 WebSocket 状态机（替换现有 ws.js）

```
                  ┌──────────┐
   connect() ──▶ │ DISCONNECTED │
                  └──────┬────┘
                         │ new WebSocket()
                  ┌──────▼──────┐
                  │  CONNECTING  │ ◀── 重试指数退避
                  └──────┬──────┘
                         │ onopen
                  ┌──────▼──────┐
           ──▶   │   CONNECTED   │
           │      └──────┬──────┘
           │             │ onclose (shouldReconnect)
           │      ┌──────▼──────┐
           └────── │  RECONNECTING│ ──▶ maxRetry → 通知用户
                  └─────────────┘

状态 + 方法 + 错误处理 → 全部封装在 Pinia store 中
```

### 4.5 可选：Qt WebEngine 外壳

保留 `main.py` 但不作为必需：

```python
# 需要 macOS 原生菜单栏时使用
python frontend/main.py  # 启动 Qt + 加载 Vue app 的构建产物
```

Vue 构建产物（`dist/`）可以直接被 `server.py` 或 Qt WebEngine 加载。零耦合。

---

## 5. 已修复 Bug 分析

> 已归档至 `docs/ERRORS_AND_FIXES.md` 第 17 节，本文档保留原始上下文。

### 5.1 `ws.js:21-30` — 重复函数定义

**症状**：WebSocket 重试达到 maxRetry 后静默失败，无错误反馈。

**根因**：`_scheduleRetry` 定义了两次。第二个定义（第 26-30 行）完全覆盖了第一个（第 21-25 行）。第一个版本在超过最大重试次数时调用 `this._connectReject?.('max retries')` 通知调用者；第二个版本只是 `return`，调用者永远不知道连接失败。这是复制粘贴产生的典型错误。

**修复**：删除第二个定义，保留带 `_connectReject` 的版本。

**影响范围**：所有 WebSocket 连接——聊天发送消息、代理审批、后台监听。如果后端短暂离线，用户将看到消息发送失败且无任何提示。

### 5.2 `components.js:25,58,62` — 字符串模板 XSS

**症状**：攻击者可以通过注入 onclick 参数执行任意 JS。例如 `UI.btn('click', "'); alert('xss') //")`。

**根因**：`UI.e()` 只转义 HTML 实体（`<` `>` `&` `"`），不适用于 JS 上下文的字符串。onclick 属性值是 JS 上下文，需要转义 `'` `"` `\` `\n` `\r`。`UI.jsStr()` 辅助函数已存在（第 90-92 行）但未被使用。

**修复**：在 `statCard()`、`btn()`、`btnSm()` 中使用 `this.jsStr(onClick)` 替代 `this.e(onClick)`。

**影响范围**：所有通过 `UI.statCard()`、`UI.btn()`、`UI.btnSm()` 生成的带 onclick 的元素。这些函数在 chat.js 和 dashboard.js 中被广泛使用。

**为什么不直接去掉 onclick 字符串**：因为当前代码将 HTML 作为字符串拼接，onclick 无法用 `addEventListener` 替代。Vue 重构后将彻底消除此问题。

### 5.3 `chat.js:250-256` — 重复 `_renderMsgShell` 丢失流式光标

**症状**：AI 开始生成消息时，消息气泡内没有闪烁的光标（`▊`），用户不知道 AI 是否已经开始回复。

**根因**：`_renderMsgShell` 定义了两次。第一个版本包含 `<div class="streaming-cursor">▊</div>`，第二个版本是空气泡。第二个完全覆盖第一个。这是复制粘贴错误。

**修复**：删除第二个定义，保留带光标的版本。

**影响范围**：所有流式聊天响应。用户看到的是：

```
# 修复前（光标被覆盖，UI 空白）
AI  │  (空气泡——用户困惑)

# 修复后（光标可见，用户知道 AI 在生成）
AI  │  ▊
```

### 5.4 `ws.js:13` — WebSocket URL 硬编码

**症状**：当应用部署到非 `localhost` 环境时，WebSocket 连接到错误的地址。

**根因**：`ws://localhost:8080` 直接硬编码在第 13 行。

**修复**：从 `window.location.hostname` 动态获取主机名，回退到 `localhost`。

**影响范围**：远程部署、Docker 环境、非本机访问。目前仅在 Qt WebEngine 中访问所以影响不大。

### 5.5 `app.js:7,22` — localStorage 无 try/catch

**症状**：在 Safari/Chrome 隐私模式下，页面加载直接崩溃。

**根因**：`localStorage.getItem/setItem` 在隐私模式下抛出 `SecurityError`。代码未捕获。

**修复**：用 try/catch 包裹所有 localStorage 读写。

**影响范围**：隐私模式用户。

### 5.6 `theme.css`, `layout.css` — CSS 变量缺失

**症状**：
- 字体退回到 serif（`--font` 未定义）
- 代码块退回到默认等宽字体（`--font-mono` 未定义）
- 侧边栏宽度塌缩到内容宽度（`--sidebar-width` 未定义）
- 状态栏高度塌缩到 auto（`--statusbar-height` 未定义）
- 导航项悬停无过渡动画（`--transition` 未定义）

**根因**：CSS 变量在 `theme.css` 和 `layout.css` 中被引用但从未声明。可能是从设计稿到代码的遗漏。

**修复**：在 `theme.css` 的 `:root` 中添加缺失的变量定义：
- `--font`: 系统字体栈（`-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', ...`）
- `--font-mono`: 等宽字体栈（`'SF Mono', 'JetBrains Mono', 'Fira Code', ...`）
- `--sidebar-width`: `220px`
- `--statusbar-height`: `32px`

### 5.7 未修复但已知的问题

| # | 问题 | 优先级 | 计划修复方式 |
|---|------|--------|-------------|
| 1 | API Key 明存 localStorage | P0 | Vue 重构时改用 sessionStorage + 内存 |
| 2 | 静默 catch 块 | P1 | Vue 重构时在每个 API 调用处加错误处理 |
| 3 | 无错误边界 | P1 | Vue 中 `<ErrorBoundary>` 组件包裹每个页面 |
| 4 | 删除会话无 API 调用 | P1 | Vue 中实现 `DELETE /api/stream/session/:id` |
| 5 | 版本号硬编码 | P2 | Vite 构建时自动注入 |
| 6 | 重复代码 (escapeHtml / formatTime) | P2 | Vue 中统一在 `utils/` 下 |

### 5.8 Bug 模式总结

已发现的 7 个 bug 呈现三个共因：

1. **复制粘贴错误**（3 个 bug）：`_scheduleRetry`、`_renderMsgShell`、`App.escapeHtml`/`UI.e` 重复。根源是手写 JS 无类型检查、无编译期检测。Vue SFC + ESLint 可以直接防止。

2. **全局可变状态**（2 个 bug）：20 个散落 `this.xxx` 导致竞态和覆盖。Vue 响应式 ref + Pinia store 提供可追踪的状态管理。

3. **字符串作为代码**（2 个 bug）：onclick 字符串注入 XSS、版本号字节替换脆弱。Vue 模板编译 + Vite 构建从根本上消除这类问题。

**结论**：这 7 个 bug 不是"程序员不够小心"的问题，而是**手写 JS 字符串模板这个架构模式本身的缺陷**。Vue 的编译时检查 + 运行时响应式系统可以直接消除这类 bug 的出现空间。

---

## 6. 迁移计划

### Phase 1：搭建基础设施（1 天）

```
前端目录重构：frontend-dev/（与现有 frontend/ 并行）
- npm create vite@latest frontend-dev -- --template vue
- 配置 Vite 代理到 localhost:8080（开发时无需 server.py）
- 引入 Pinia + Vue Router + markdown-it + highlight.js
- 迁移 theme.css，验证暗色/亮色模式
```

### Phase 2：Chat 页面（2 天）

```
- 实现 Pinia chatStore（替代 20 个散落变量）
- 实现 WebSocket 状态机 Pinia wsStore
- ChatMessage.vue（markdown 渲染 + 代码高亮 + 操作按钮）
- ChatInput.vue（输入框 + 图片拖放粘贴）
- SessionList.vue（会话切换 + CRUD）
- 验证流式渲染没有闪烁
```

### Phase 3：其余 11 个页面（2 天）

```
- 按复杂度从低到高迁移
- Dashboard → System → Settings → Modules →
  Sessions → Security → Perception → Tools →
  Memory → Causal
- 每个页面复用 Phase 2 创建的通用组件
```

### Phase 4：收尾（1 天）

```
- 删除 frontend/server.py（开发时直接 API）
- 删除 frontend/js/、frontend/css/（旧代码）
- 更新 main.py 加载 Vue 构建产物
- 验证 cortex --qt 启动流程
```
