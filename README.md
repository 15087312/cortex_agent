# Cortex Agent

> **类人智能后端系统** — 多模型协作 · 连续思考 · 三层记忆 · 安全审计

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent
```

### 2. 安装依赖

```bash
pip install -e .
```

### 3. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的模型 API Key（DeepSeek / OpenAI 等）
```

### 4. 启动

```bash
cortex
```

就这么简单。`cortex` 会自动启动后端服务并打开交互式终端。

---

## 使用方式

```bash
# 一键启动（后端 + 交互式终端）
cortex

# 指定端口
cortex --port 9000

# 只启动后端（API 服务模式，无终端界面）
cortex --no-tui

# 连接已有的远程后端
cortex --api-url http://192.168.1.100:8080

# 指定 API 密钥
cortex --api-key your-secret-key
```

启动后你会看到：

```
🚀 启动 Cortex Agent (:8080)...
⏳ 等待后端就绪...
✓ 后端就绪: http://127.0.0.1:8080
```

然后进入交互式终端，直接输入问题即可对话。按 `Ctrl+C` 优雅退出。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **多模型协作** | 大/中/小三层模型分工合作，像团队一样推理 |
| **连续深度思考** | 不急于回答，多轮迭代推理直到满意 |
| **三层记忆** | 短期对话 + 工具关联 + 长期沉淀，越用越懂你 |
| **安全多层防护** | 工具分级审批、输入输出双向校验、完整审计链 |
| **联网搜索** | crawl4ai 无头浏览器抓取网页全文 |
| **技能系统** | YAML 定义角色，一句话让 AI 扮演任意领域专家 |
| **价值观进化** | AI 可根据经验动态调整自身行为准则 |
| **认知黑板** | 所有 AI 角色共享同一份"思维画布"，协同而非孤立 |
| **时间感知** | AI 知道当前时间、距上次对话多久、谁在说话 |
| **实时流式** | WebSocket 推送思考过程、专家调度、工具调用 |
| **三格式兼容** | 同时支持 OpenAI / DashScope / Anthropic API |
| **CLI 终端** | 配套 TUI 界面，实时可视化所有内部过程 |

---

## 项目结构

```
cortex_agent/
├── cortex/           # CLI 入口（cortex 命令）
├── api/              # FastAPI 应用 + WebSocket
├── cli_tui/          # Textual TUI 终端界面
├── config/           # 配置系统
├── modules/          # 核心业务模块
│   ├── thinking/     # 多模型思考引擎
│   ├── memory/       # 三层记忆系统
│   ├── security/     # 安全与审计
│   ├── attention/    # 注意力系统
│   └── ...
├── infra/            # 基础设施（模型客户端、工具管理、数据处理）
├── skills/           # YAML 技能定义
├── tests/            # 测试（248 个用例）
├── docs/             # 文档
├── frontend/         # 前端（预留）
├── pyproject.toml    # 项目配置
├── requirements.txt  # Python 依赖
└── Dockerfile        # Docker 部署
```

---

## 配置

核心配置通过 `.env` 文件：

```env
# 必填：模型 API Key
LARGE_MODEL_API_KEY=sk-your-key-here

# 可选：HTTP API 认证
SIMPLE_API_KEY=your-secret

# 可选：指定模型
LARGE_MODEL_NAME=deepseek-v4-flash
LARGE_MODEL_API_URL=https://api.deepseek.com/v1/chat/completions
```

完整配置项见 [.env.example](.env.example)。

---

## Docker 部署

```bash
docker-compose up -d
```

---

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /metrics` | Prometheus 指标 |
| `WS /stream/ws/{session_id}` | WebSocket 实时对话 |
| `GET /config` | 获取配置 |
| `PUT /config/{key}` | 更新配置（需 API Key） |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -q

# 启动后端（开发模式）
python -m scripts.start_all
```

---

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLite / FAISS / aiohttp
- **终端**: Textual (TUI) / Rich
- **模型**: DeepSeek / OpenAI / Anthropic（三格式兼容）
- **搜索**: crawl4ai（无头浏览器）
- **部署**: Docker / Docker Compose

---

## 协议

[Apache License 2.0](LICENSE)
