# Cortex Agent API 需求文档

> 生成时间：2026-06-08
> 现有端点：170 个 | 缺失：4 个 | 死代码：2 个

---

## 一、现有 API 全景

### 1.1 端点统计

| 路由前缀 | 端点数 | 认证方式 | 用途 |
|----------|--------|---------|------|
| `/` (根) | 4 | X-API-Key | 健康检查、配置 |
| `/stream` | 7 | X-API-Key | 思考流、WebSocket |
| `/memory` | 25 | X-API-Key | 短期/长期记忆 |
| `/tools` | 11 | Bearer TOOL_API_TOKEN | 工具管理 |
| `/management` | 54 | Bearer INTERNAL_API_TOKEN | 系统管理 |
| `/security` | 6 | X-API-Key | 安全审查 |
| `/attention` | 6 | X-API-Key | 注意力机制 |
| `/output` | 23 | X-API-Key | 输出控制（鼠标/键盘/UI） |
| `/data-process` | 8 | X-API-Key | 语音/图像处理 |
| `/plugins` | 17 | Bearer PLUGIN_API_TOKEN | 插件管理 |
| `/differences` | 9 | X-API-Key | 差异检测 |
| **WebSocket** | **1** | X-API-Key | 实时对话 |
| **合计** | **170** | | |

### 1.2 认证体系

| 认证方式 | 适用范围 | 用途 |
|----------|---------|------|
| `X-API-Key` header | 大部分端点 | 前端/TUI 调用 |
| `Bearer INTERNAL_API_TOKEN` | `/management/*` | 内部管理 |
| `Bearer TOOL_API_TOKEN` | `/tools/*` | 工具系统 |
| `Bearer PLUGIN_API_TOKEN` | `/plugins/*` | 插件系统 |

---

## 二、缺失端点（TUI 需要但不存在）

| # | 缺失端点 | TUI 调用方法 | 修复方案 |
|---|---------|-------------|---------|
| 1 | `GET /config` | `api_client.get_config()` | 新增，返回所有可读配置项 |
| 2 | `POST /config/toggle-companion-mode` | `api_client.toggle_companion_mode()` | 新增，或改 TUI 用 `PUT /config/COMPANION_MODE` |
| 3 | `GET /stream/sessions` | `api_client.get_sessions()` | 新增，或改 TUI 调 `/management/sessions` |
| 4 | `POST /stream/stop` | `api_client.stop_thinking()` | 新增，内部转发到 WebSocket stop 消息 |

---

## 三、前端需要的 API 分类

### 3.1 前端必须有的（外部暴露）

#### A. 对话核心

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| POST | `/stream/session` | 创建会话 | ✅ |
| WS | `/stream/ws/{session_id}` | 实时对话 | ✅ |
| GET | `/stream/sse/{session_id}` | SSE 流式输出 | ✅ |
| DELETE | `/stream/session/{session_id}` | 关闭会话 | ✅ |
| POST | `/stream/stop` | 停止当前思考 | ❌ **缺失** |
| GET | `/stream/status` | 系统状态 | ✅ |
| GET | `/stream/context/{session_id}` | 会话上下文 | ✅ |

#### B. 配置管理

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/config` | 读取当前配置 | ❌ **缺失** |
| PUT | `/config/{key}` | 更新配置项 | ✅ |
| POST | `/config/toggle-companion-mode` | 切换陪伴模式 | ❌ **缺失** |

#### C. 安全审查

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/security/status` | 安全状态 | ✅ |
| GET | `/security/audit` | 审计日志 | ✅ |
| WS | (复用 `/stream/ws`) | 安全审批交互 | ✅ |

#### D. 系统健康

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/health` | 健康检查 | ✅ |
| GET | `/` | 系统信息 | ✅ |
| GET | `/metrics` | Prometheus 指标 | ✅ |

---

### 3.2 前端管理面板需要的（外部暴露）

#### E. 会话管理

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/stream/sessions` | 列出所有会话 | ❌ **缺失** |
| GET | `/management/sessions/{id}/dialog` | 会话对话记录 | ✅ (需 INTERNAL_API_TOKEN) |
| GET | `/management/runners` | 活跃模型状态 | ✅ (需 INTERNAL_API_TOKEN) |

#### F. 记忆浏览

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/memory/short-term/context` | 短期记忆 | ✅ |
| GET | `/memory/long-term/{type}` | 长期记忆 | ✅ |
| GET | `/memory/long-term/{type}/search` | 搜索记忆 | ✅ |
| GET | `/memory/summary` | 记忆概览 | ✅ |
| GET | `/memory/personality` | 人格配置 | ✅ |
| GET | `/memory/personality/values` | 价值观 | ✅ |

#### G. 系统监控

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/management/dashboard` | 仪表盘 | ✅ (需 INTERNAL_API_TOKEN) |
| GET | `/management/modules/status` | 模块状态 | ✅ (需 INTERNAL_API_TOKEN) |
| GET | `/management/resources` | 资源使用 | ✅ (需 INTERNAL_API_TOKEN) |
| GET | `/management/health/detailed` | 详细健康 | ✅ (需 INTERNAL_API_TOKEN) |
| GET | `/management/alerts` | 告警列表 | ✅ (需 INTERNAL_API_TOKEN) |

#### H. 技能管理

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/skills` | 列出所有技能 | ❌ **缺失** |
| GET | `/skills/{id}` | 技能详情 | ❌ **缺失** |
| PUT | `/skills/{id}/activate` | 激活技能 | ❌ **缺失** |

#### I. 工具浏览

| 方法 | 路径 | 功能 | 状态 |
|------|------|------|------|
| GET | `/tools/` | 工具列表 | ✅ (需 TOOL_API_TOKEN) |
| GET | `/tools/info/{name}` | 工具详情 | ✅ (需 TOOL_API_TOKEN) |
| GET | `/tools/events` | 调用历史 | ✅ (需 TOOL_API_TOKEN) |

---

### 3.3 仅内部使用（不暴露给前端）

| 模块 | 端点数 | 原因 |
|------|--------|------|
| `/output/*` (鼠标/键盘/UI) | 23 | 系统操控，前端不应直接调用 |
| `/attention/*` | 6 | 内部注意力计算 |
| `/tools/call*` | 4 | 工具执行需安全审查 |
| `/management/memory/clear` | 1 | 危险操作 |
| `/management/health/{module}/repair` | 1 | 系统修复 |
| `/plugins/*` | 17 | 插件系统独立管理 |
| `/differences/*` | 9 | 内部差异检测 |
| `/data-process/*` | 8 | 内部数据处理 |

---

## 四、前端 vs TUI 差异

| 功能 | TUI | Web 前端需要 |
|------|-----|-------------|
| 对话 | WebSocket + 文本 | WebSocket + Markdown 渲染 |
| 安全审批 | ApprovalSelect 组件 | 弹窗/Modal |
| 模式切换 | Shift+Tab / /mode | 下拉菜单 |
| 流式输出 | RichLog 追加 | 增量 Markdown 渲染 |
| 思考过程 | 折叠显示 | 可展开的 Accordion |
| 工具调用 | 简化显示 | 代码块 + 参数高亮 |
| 记忆浏览 | /memory 命令 | 独立页面 |
| 系统监控 | /status 命令 | Dashboard 页面 |

---

## 五、认证方案建议

当前有 3 套 token（X-API-Key、INTERNAL_API_TOKEN、TOOL_API_TOKEN），前端需要统一：

| 方案 | 说明 |
|------|------|
| **A. 统一 X-API-Key** | 前端只用一个 key，管理端点降级为 X-API-Key 认证 |
| **B. JWT + 角色** | 前端登录获取 JWT，admin 角色可访问 management |
| **C. 保持现状** | 前端持有多个 token，按模块分别传 |

建议方案 A：前端统一用 X-API-Key，`/management/*` 和 `/tools/*` 也接受 X-API-Key（或新增一个 `ADMIN_API_KEY`）。

---

## 六、优先级排序

### P0 — 必须修复（TUI 功能缺失）

1. `GET /config` — TUI 启动时读取配置
2. `POST /stream/stop` — TUI ESC 暂停功能

### P1 — 前端开发必须

3. `GET /stream/sessions` — 会话列表
4. `POST /config/toggle-companion-mode` — 模式切换
5. `GET /skills` — 技能列表
6. `GET /skills/{id}` — 技能详情

### P2 — 前端增强

7. 认证方案统一
8. `/management/*` 端点开放给前端（需 ADMIN_API_KEY）
9. WebSocket 消息格式文档化

### P3 — 清理

10. 删除 `POST /tools/register`（NOT_IMPLEMENTED）
11. 删除 `POST /output/controller/mode`（BAD_REQUEST）
12. TUI `stop_thinking()` 改用 WebSocket stop 消息（已有修复）
