# Cortex Agent — Frontend

Web 前端界面，连接 Humanoid AGI 后端 API。

## 目录结构

```
frontend/
├── src/           # 源代码
├── public/        # 静态资源
├── package.json   # 依赖配置
└── README.md      # 本文件
```

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

## API 接口

前端连接后端 `http://localhost:8080`，主要接口：

- `POST /stream/ws/{session_id}` — WebSocket 实时对话
- `GET /health` — 健康检查
- `GET /metrics` — Prometheus 指标
- `GET /config` — 获取配置
- `PUT /config/{key}` — 更新配置（需 API Key）

## 认证

所有 API 请求需在 Header 中携带 `X-API-Key`。
