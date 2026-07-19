# Cortex Agent — Web UI

Cortex Agent 的 Web 控制台，包含 12 个功能页面，通过 WebSocket 连接后端。

## 启动方式

```bash
# 确保后端已在 localhost:8080 运行
python frontend/server.py

# 浏览器打开
# http://localhost:8765
```

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

## WebSocket 协议

```
连接: ws://localhost:8080/stream/ws/{session_id}
创建会话: POST /api/stream/session → {session_id}

发送: {"type": "input", "content": "消息内容"}
停止: {"type": "stop"}
