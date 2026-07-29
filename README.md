# Cortex Agent

AI Agent 管理控制台前端。基于 **Vue 3 + Pinia + Vue Router**，12 个功能页面，WebSocket 流式聊天。

## 启动

```bash
# 开发模式
cd frontend-dev
npm install
npm run dev          # http://localhost:5173

# 构建
npm run build        # 输出 frontend-dev/dist/

# Qt 桌面版
python frontend/main.py
```

## 架构

```
frontend-dev/
└── src/
    ├── api/          REST 封装 (fetch + sessionStorage)
    ├── ws/           WebSocket 状态机
    ├── stores/       Pinia (chat, session, health, theme, toast, config)
    ├── components/   14 个通用组件
    ├── pages/        12 个路由页面 (懒加载)
    ├── utils/        markdown + format + escape
    └── css/          设计令牌 (128 变量, 暗/亮)
```

## 功能

| 页面 | 说明 |
|------|------|
| 对话 | WebSocket 流式聊天，markdown + 代码高亮，多模态 |
| 仪表盘 | 系统健康度，模块状态 |
| 记忆 | 事件 CRUD + 搜索 |
| 工具 | 工具注册 + 在线调用 |
| 安全 | L0-L4 开关 + 审计日志 |
| 感知 | 传感器启停 + 10s 轮询 |
| 设置 | API Key + 运行时配置 + 模型状态 |

## 依赖

- Node.js >= 18
- Vue 3, Pinia, Vue Router
- markdown-it, highlight.js
- 后端: localhost:8080
