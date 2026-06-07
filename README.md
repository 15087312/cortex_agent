# Cortex Agent

> **类人智能后端系统** — 多模型协作 · 连续思考 · 三层记忆 · 安全审计

---

## 快速开始

### 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

安装完成后直接运行：

```bash
cortex
```

### 手动安装

```bash
# 1. 克隆
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent

# 2. 安装
pip install -e .

# 3. 配置
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 4. 启动
cortex
```

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

## 核心架构 — 与众不同之处

### 🎯 事件驱动黑板架构（Event-Driven Blackboard）
传统多agent系统存在 **N²复杂度**（所有agent都读全部history），导致重复回复、超时、上下文污染。Cortex_agent从根本上重构为**事件驱动黑板**：

- **单一真理来源**：`CognitiveBlackboard` 维护完整思维状态
- **分层上下文切片**：
  - 📘 **Large模型** → 看全局目标、计划、风险、委托、发现
  - 📗 **Supervisor** → 看任务目标、可用工具
  - 📙 **Expert** → 只看当前步骤、工具状态、最近5步执行历史
- **消除N²污染**：每个turn完全隔离，agent间无噪音干扰

### 🧠 6时机记忆系统（T1-T6 Memory Pipeline）
对标Claude内部设计的多阶段记忆，支持真正的"长期学习"：

| 阶段 | 触发时机 | 作用 |
|------|---------|------|
| **T1** | 会话预加载 | 4路并行读取过去对话、笔记、技能、行为准则 |
| **T2** | 输入后检索 | Per-turn缓存，找相关历史对话（毫秒级FAISS搜索） |
| **T3** | 工具后关联 | 执行工具后立即检索相关知识 |
| **T4** | 水位线压缩 | 当对话长度达70%时触发，自动总结与删除 |
| **T5** | 任务后沉淀 | 任务完成后30秒，沉淀关键发现到长期记忆 |
| **T6** | 深度整合 | 每12小时运行，跨会话知识融合与演化 |

**关键约束**：所有T1-T6都用 `fire-and-forget` 异步，**绝不阻塞**主请求流程。

### 🔄 显式生命周期管理（SessionLifecycle + TurnState）
从隐式状态（易竞态、难debug）升级为显式状态机：

```
IDLE → PLANNING → EXECUTING → INTEGRATING → COMPLETE
```

- 每个turn有独立的 `TurnContext` 和 `TurnState`
- `SessionLifecycle` 管理会话全生命周期
- 杜绝竞态条件，支持真正的多会话并发

### 🎪 13个专家角色系统（Intelligent Delegation）
不是简单的prompt，而是完整的角色定义和任务路由：

| 角色 | 专长 |
|------|------|
| `code_supervisor` | 代码架构设计、技术风险评估 |
| `code_reviewer` | 代码质量审查 |
| `code_writer` | 代码实现 |
| `security_monitor` | 安全审计、漏洞检测 |
| `test_writer` | 测试策略规划 |
| `emotion` | 情感分析、用户体验 |
| `memory_manager` | 知识整合与演化 |
| `orchestrator` | 任务协调与流程编排 |
| 其他8个... | — |

智能委托引擎自动根据任务复杂度选择最优expert，支持**并行执行**和**串行依赖**。

### 🛡️ 多层安全防护（Defense in Depth）
- **输入检查** → 内容审核、意图识别
- **执行审查** → 工具调用前预检，分级审批
- **输出审查** → 回复内容合规性校验
- **完整审计链** → 所有决策可追溯

已修复11个P0级安全漏洞，通过认知黑板实现**完全可审计的AI决策**。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **多模型协作** | 大/中/小三层模型分工合作，像团队一样推理 |
| **连续深度思考** | 不急于回答，多轮迭代推理直到满意 |
| **6时机记忆** | T1-T6多阶段记忆，fire-and-forget异步，越用越懂你 |
| **安全多层防护** | 工具分级审批、输入输出双向校验、完整审计链 |
| **认知黑板** | 所有角色共享同一份"思维画布"，消除N²上下文污染 |
| **13个专家角色** | 智能委托系统，自动路由最优expert执行任务 |
| **显式生命周期** | SessionLifecycle + TurnState，杜绝竞态、支持并发 |
| **联网搜索** | crawl4ai 无头浏览器抓取网页全文 |
| **价值观进化** | AI 可根据经验动态调整自身行为准则 |
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
