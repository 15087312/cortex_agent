# Cortex Agent — Web UI

**Language / 语言**: [简体中文](./README.md) | [English](./README.en.md)

Cortex Agent 的 Web 控制台，包含 15 个功能页面（聊天/编排/设置/仪表盘/记忆/技能/工具/定时任务/主动搭话/感知/安全/系统/图谱/因果图/模块），通过 WebSocket 连接后端。

## 启动方式

### macOS（推荐）

```bash
# 一键启动（双击或在终端运行）
./frontend/start.command
```

或手动启动：
```bash
# 确保后端已在 localhost:8080 运行
# 启动 Qt 桌面客户端
python frontend/main.py
```

### 浏览器访问（无 Qt）

```bash
python frontend/server.py
# 打开 http://localhost:8765
```

### Windows

双击 `frontend/start.bat`

## 功能页面

| 页面 | 功能 |
|------|------|
| 对话 | WebSocket 实时聊天，多模型选择 |
| 仪表盘 | 系统概览 |
| 模块管理 | 模块状态查看 |
| 记忆管理 | 事件记忆 CRUD |
| 因果图 | 因果关系可视化 |
| 工具管理 | 工具注册与调用 |
| 安全审计 | 安全策略与审计日志 |
| 感知系统 | 传感器状态监控 |
| 会话监控 | 活跃会话列表 |
| 系统信息 | 系统状态与配置 |
| 设置 | API Key 与运行时配置 |

## 布局防溢出约定

- Vue 动态 `:style` 里的长度值必须带单位（如 `'260px'`、`'50%'`），不要写裸数字；只有 `opacity`、`zIndex`、`flexShrink`、`lineHeight` 等无单位属性可以传数字。
- flex 行/列里的文本、输入框、可收缩面板补 `min-width: 0`；省略号要加在真正受约束的那一层，并确保中间 flex 父级也能收缩。
- 输入框在 flex 行中用 `flex: 1 1 0; width: 0; min-width: 0`，右侧提示/按钮限制 `max-width`，避免提示文字按 max-content 抢空间。
- 固定宽度侧栏同时设置 `width`、`max-width`、`flex-shrink: 0`、`min-width: 0`。
- 修改布局后用真实浏览器在 1440 / 1024 / 800 宽度下检查 `scrollWidth` 与 `getBoundingClientRect()`，不要只跑 jsdom 单测。

完整案例见 [`docs/bugfixes/05-frontend-ui.md` §108](../docs/bugfixes/05-frontend-ui.md)。

## WebSocket 协议

```
连接: ws://localhost:8080/stream/ws/{session_id}
创建会话: POST /api/stream/session → {session_id}

发送: {"type": "input", "content": "消息内容"}
停止: {"type": "stop"}
