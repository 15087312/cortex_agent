# Cortex Agent

> **类人智能 Agent 系统** —— 多模型协作 · 连续思考 · 认知黑板 · 记忆与因果推理 · 安全审计 · 多端交互

Cortex Agent 是一个面向"类人智能"的完整 Agent 运行时：不仅是一个聊天机器人，而是一套**可编排的多模型协作引擎**，配备事件驱动认知黑板、结构化记忆、因果推理、分级安全门控、85+ 工具与 MCP 扩展，并提供 **Web UI / Qt 桌面客户端 / 桌宠 / 终端** 四种交互方式。

---

## 核心亮点

| | 亮点 | 说明 |
|---|---|---|
| **认知黑板** | 事件驱动，消除多 Agent 的 N² 复杂度 | 单一真理来源 + 分层上下文切片，每个 turn 完全隔离，杜绝重复回复与上下文污染 |
| **多模型三层编排** | Large → Supervisor → Expert 并行协作 | 战略决策 → 任务分解 → 专家并行执行，结果汇聚黑板由总指挥整合 |
| **连续思考引擎** | 不止"输入→输出" | 多轮 ReAct 迭代，复杂度自适应思考预算，模型自主决定继续思考 / 委托 / 输出 |
| **双对话模式** | Agent 编排 与 纯对话（chatonly） | 纯对话走轻量引擎单一人格；Agent 模式多角色协作，前端可编排/自定义角色 |
| **记忆 + 因果** | 事件记忆 + 因果图谱推理 | 会话提炼为结构化事件（SQLite+FAISS），因果树深度回忆按因果链召回 |
| **多端交互** | Web / Qt 桌面 / 桌宠 / TUI | Vue 3 Web UI、PyQt6 桌面客户端、Live2D 桌宠、Textual 终端 |
| **安全 fail-closed** | 分级审批 + 全链路审计 | 工具调用分级门控（LOW/HIGH/CRITICAL），权限/拦截异常一律拒绝（不静默放行） |
| **工具系统** | 85 内置 + MCP + AI 自创 | 文件/搜索/感知/代码执行/UI 检测；MCP 服务器扩展；模型运行时自创工具 |
| **测试保障** | 1700+ 项测试 | unit/integration 分层，临时库隔离、零触碰生产、禁吞错掩盖，多轮补测覆盖 0% 模块 |

---

## 架构概览

```
                          ┌─────────────────────────────────────────┐
   用户输入 ─────────────► │  Cortex 入口（cortex/ CLI / 各端前端）  │
                          └──────────────────┬──────────────────────┘
                                             ▼
                         ┌──────────────────────────────────────────┐
                         │   FastAPI + WebSocket/SSE（api/）         │
                         │   ├── Agent 模式 → 多模型三层编排          │
                         │   ├── chatonly 模式 → 轻量引擎             │
                         │   └── 管理 API（编排/人设/感知/记忆）        │
                         └──────────────────┬──────────────────────┘
                                             ▼
        ┌──────────────┬───────────────────┼────────────────────┬──────────────┐
        ▼              ▼                   ▼                    ▼              ▼
   CognitiveBlackboard  记忆系统          安全门控             感知系统        工具系统
   （事件驱动黑板）     EventStore+FAISS  fail-closed 分级审批   屏幕/OCR/语音    85+ / MCP
        │              CausalGraph       完整审计链           差异检测        / create_tool
        └──────────────┴───────────────────┴────────────────────┴──────────────┘
```

### 多模型三层编排

```
用户输入
   ↓
[Large 模型] ← 战略决策、关键判断、最终整合
   ↓ 分解为子任务（delegate_task）
[Supervisor] ← N 个主管并行（code / creative / query …）
   ↓ 分配专家（probe_start）
[Expert] ← N×M 个专家并行执行
   ↓ 结果汇聚
[CognitiveBlackboard] → [Large 模型整合，生成最终答案]
```

### 事件驱动黑板（为什么没有 N² 复杂度）

传统多 Agent 让所有 Agent 读全部历史 → 重复回复、超时、上下文污染。Cortex 用 `CognitiveBlackboard` 作为单一真理来源，`ContextSlicer` 按层级切片上下文：

- **Large** 看全局目标、计划、风险、委托、发现
- **Supervisor** 看任务目标、可用工具
- **Expert** 只看当前步骤、工具状态、最近 5 步执行历史

跨模块通信仅通过 MessageBus / CognitiveBlackboard / Protocol 接口，依赖方向严格 L3→L4。

### 四层架构

| 层级 | 路径 | 职责 |
|------|------|------|
| L1 入口 | `cortex/` | CLI 入口、子进程编排、版本管理 |
| L2 API | `api/` | FastAPI、WebSocket/SSE 流式、中间件（CORS/认证/限流/请求ID） |
| L3 业务 | `modules/` | 9 个业务模块（思考、记忆、安全、感知、输出、管理、数据库、桌宠、cortex） |
| L4 基础设施 | `infra/` | 模型客户端、工具注册/管理、MCP、数据处理、硬件输入 |

### 双对话模式

| 模式 | 引擎 | 特点 |
|------|------|------|
| **Agent（默认）** | `modules/thinking/core/` | 多角色协作、探针驱动激活、专家并行 |
| **纯对话（chatonly）** | `modules/thinking/chat_light/` | 轻量单人格，system prompt 支持人设/系统覆盖，自定义总指挥 agent 人设自动生效 |

---

## 快速开始

### 一键安装

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

**Windows（PowerShell）**
```powershell
iex (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/15087312/cortex_agent/main/install.ps1')
```
执行策略受限时先：`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force`

安装后运行 `cortex`。

### 手动安装

```bash
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent
pip install -e .
cp .env.example .env        # 编辑填入 API Key
```

### 各端运行

| 端 | 命令 | 说明 |
|----|------|------|
| **后端 + TUI** | `cortex` | 一键启动（API + Textual 交互终端），`Ctrl+C` 退出 |
| **后端（无界面）** | `cortex --no-tui` | 仅 API 服务模式 |
| **Web UI（开发）** | `cd frontend && npm run dev` | Vite HMR，默认 5173，代理 `/api` → 8080 |
| **Web UI（生产）** | `python frontend/server.py` | 静态服务默认 8765 |
| **Qt 桌面客户端** | `python frontend/main.py` | macOS PyQt6+QtWebEngine，自动拉起桌宠 |
| **桌宠** | `DESKTOP_PET_ENABLED=true` 启用 | 独立进程 `pet_launch.py`，透明置顶窗 Live2D |
| **远程连接** | `cortex --api-url http://192.168.1.100:8080` | 连接已有后端 |

**前端 API 约定**：所有请求用 `/api/` 前缀（代理去前缀到 8080）；WebSocket 直连
`:8080/stream/ws/{session_id}`；`/audio`、`/pet` 资源保留裸路径。

---

## 交互端

### Web UI（Vue 3）

15 个页面：Chat（多模态附件/流式/思考区/待办/审批横幅）、Orchestration（角色编排/人设/工具权限/
激活开关）、Dashboard、Memory、Settings、Skills/Tools/Modules、ScheduledTasks/Outreach、Perception/
Security/System/Graph/Causal。

### Qt 桌面客户端（macOS）

`frontend/main.py`：PyQt6 + QtWebEngine，后台启动 server.py，Qt 窗口内嵌 Web UI；关闭隐藏到 Dock、
`Cmd+Q` 退出；原生 `confirm()/prompt()` 会阻塞 QtWebEngine，前端一律用页内弹层。

### 桌宠（Live2D）

Live2D 角色 + 独立进程 + Qt 透明置顶窗：拖动移动、单击角色互动菜单、F8/"科特"语音触发主会话对话；
绑定固定主会话 `pet_main`，对话记忆延续，TTS 语音回复。

### 终端（TUI）

Textual 交互终端，`cortex` 启动，支持多模态文件与流式回复。

---

## 功能模块

### 记忆系统

会话结束时 LLM 将对话提炼为结构化事件，存入 SQLite+FAISS 供向量检索，并构建因果图谱支持多跳推理。

| 组件 | 作用 |
|------|------|
| **EventReducer** | 会话提炼为 MemoryEvent（fact/thought/lesson/keywords） |
| **EventStore** | SQLite + FAISS 存储与向量检索 |
| **EventRetrieval** | 混合检索（语义×0.35 + 重要性 + 时效 + 使用/提及频率） |
| **CausalGraph / CausalTree** | 事件因果图 + 因果树深度回忆 |

### 安全系统

- **多层防护**：输入检查 → 执行审查（分级审批 LOW/MEDIUM/HIGH/CRITICAL）→ 输出审查 → 完整审计链
- **fail-closed**：权限/安全拦截检查异常一律拒绝，不静默放行（历史上修复过 3 处 fail-open）
- **危险命令检测**：极端危险命令硬阻断（rm -rf /、pipe-to-shell 下载执行等）

### 工具系统

| 层级 | 来源 | 说明 |
|------|------|------|
| **ToolRegistry（内置）** | `infra/tool_manager/` | 85 个内置工具 |
| **MCP（远程）** | `infra/mcp/` | stdio/SSE 连接的 MCP 服务器 |
| **create_tool（AI 自创）** | 运行时动态创建 | 持久化到磁盘 |

统一经 `MCPToolService` 路由，工具名冲突内置优先，全部经过安全权限检查。

### 感知系统

窗口变化 / 屏幕差异 / OCR / 语音 / 文件变化检测，1Hz 心跳差异源，驱动主动搭话与桌宠互动。

---

## 运行模式与配置

| 执行模式 | 行为 |
|----------|------|
| `plan` | 只读，禁止所有写操作 |
| `edit` | 写操作前需用户确认 |
| `yolo` | 仅安全专家检测，跳过用户确认 |
| `control` | MEDIUM+ 工具需用户单独确认 |

其他关键配置：`PERCEPTION_ENABLED`、`DIFFERENCE_DETECTOR_ENABLED`、`PROACTIVE_OUTREACH_ENABLED`、
`SECURITY_REVIEW_MODE`、`CORTEX_MODE`（agent/chatonly）、`DESKTOP_PET_ENABLED` 等。
完整配置见 [.env.example](.env.example) 与 [docs/CONFIG_VALUE_EVOLUTION.md](docs/CONFIG_VALUE_EVOLUTION.md)。

---

## 项目结构

```
ai_backend/
├── cortex/                 # CLI 入口（cortex 命令）
├── api/                    # FastAPI 应用 + WebSocket/SSE + 管理 API
├── frontend/               # Vue 3 前端 + Qt 桌面端 + 桌宠
│   ├── src/                # Web UI 源码（15 个页面）
│   ├── pet/                # 桌宠 Live2D 前端
│   ├── main.py             # macOS 桌面客户端（PyQt6 + QtWebEngine）
│   ├── pet_launch.py       # 桌宠独立进程
│   ├── pet_widget.py       # 桌宠透明置顶窗
│   └── server.py           # 静态服务（8765，代理 /api）
├── modules/                # 业务模块
│   ├── thinking/           # 编排引擎（core / chat_light / cognition / communication / probes / skills）
│   ├── memory/             # 事件记忆 + 因果图谱
│   ├── security_system/    # 安全门控（fail-closed）+ 审计
│   ├── perception/         # 感知系统
│   ├── desktop_pet/        # 桌宠引擎
│   ├── output_system/ management/ database/ cortex/
├── infra/                  # 模型客户端 / 工具 / MCP / 数据处理 / 硬件输入
├── config/                 # Pydantic Settings + providers（模型格式适配）+ prompts
├── cli_tui/                # Textual TUI
├── utils/                  # 共享工具
├── tests/                  # 137 个测试文件（unit/integration/external）
├── docs/                   # 文档（架构/记忆/修复记录）
├── scripts/                # 部署与运维（含 fix_macos_libomp.py）
└── data/                   # 运行时数据
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 后端 | Python 3.11+ / FastAPI / Uvicorn / aiohttp / httpx |
| 模型 | DashScope / OpenAI / Anthropic（`config/providers` 统一格式适配） |
| 前端 | Vue 3 / Vite 6 / Pinia / Vue Router / Vitest |
| 桌面 | PyQt6 + QtWebEngine（macOS） |
| 桌宠 | Live2D + TTS |
| 数据 | SQLite / DiskCache / JSONL / FAISS |
| NLP/ML | jieba / sentence-transformers / PyTorch / transformers / faiss / mlx-lm（可选） |
| 搜索 | DuckDuckGo / 搜狗 / 必应 / 百度 |
| 部署 | Docker / Docker Compose / PyInstaller |

---

## 测试

**137 个测试文件，全量 1700+ 项通过**（unit / integration / external 分层）。

```bash
# 后端全量（推荐）
pytest tests/ -m "not external and not slow"

# 前端
cd frontend && npm test
```

**隔离原则**：绝不触碰生产库（临时 SQLite + monkeypatch 单例）；重库加载放宽 timeout；
后台线程类提供 `stop()`；禁 `except: pass` 吞错掩盖；测试假对象须与真实模型字段一致。

**覆盖亮点**：utils / config.providers / identity_loader / values_store / tool_discovery /
context_budget / ModelRunnerManager 从 0% 补到覆盖；management 全端点；安全 fail-closed 回归。

---

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查（healthy / degraded） |
| `GET /` | 系统信息和版本 |
| `WS /stream/ws/{session_id}` | WebSocket 实时对话（流式/附件/审批） |
| `GET /stream/sse/{session_id}` | SSE 流式对话 |
| `GET /config` / `PUT /config/{key}` | 配置读写（白名单 + API Key） |
| `PUT /config/persona/{role}` | 人设 / 系统提示词覆盖 |
| `POST /management/orchestration/agents` | 自定义 Agent（含层级/模型/人设） |

---

## Docker 部署

```bash
docker-compose up -d      # 构建并启动（4GB 内存，2 CPU）
docker-compose logs -f app
docker-compose down
```

---

## 发布（Release）

### 升级版本号并打 tag

```bash
python scripts/release.py patch --tag --push    # 2.0.0 -> 2.0.1，提交 + 打 tag v2.0.1 + 推送
```

脚本会自动同步 `VERSION` 与 `frontend/package.json`。推 tag 后 GitHub Actions
（`.github/workflows/release.yml`）自动在 Windows / macOS 上构建并上传便携包到 Release。

### 打包产物（`dist/CortexAgent/`）

PyInstaller 一次产出两个可执行文件（`pyinstaller pyinstaller.spec --clean --noconfirm`）：

| 可执行文件 | 作用 |
|-----------|------|
| `Cortex_Client(.exe)` | 桌面客户端（PyQt6 + QtWebEngine），双击启动 |
| `AI_Backend(.exe)`    | 后端 API（uvicorn），由客户端自动拉起（同目录） |

客户端内置 Vue 构建产物 `frontend/dist`（发布前需 `cd frontend && npm run build`）。

### 首次运行注意

- **Embedding 模型**（约 500MB）：缓存缺失时启动会自动下载（支持 `HF_MIRROR` 环境变量加速，如 `hf-mirror.com`）。
- **视觉模型**：`VISION_BACKEND` 默认 `local`（下载较大模型）；可用 `VISION_BACKEND=mock` 或 `api` 跳过本地加载。
- Windows 首次运行若被 SmartScreen 拦截，选"更多信息 → 仍要运行"。

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 详细架构设计 |
| [docs/THINKING_ARCHITECTURE.md](docs/THINKING_ARCHITECTURE.md) | 思考模块内部架构（双层 ReAct 循环） |
| [docs/MEMORY_INJECTION.md](docs/MEMORY_INJECTION.md) | 记忆系统与注入链路 |
| [docs/CONFIG_VALUE_EVOLUTION.md](docs/CONFIG_VALUE_EVOLUTION.md) | 价值观进化配置 |
| [docs/ERRORS_AND_FIXES.md](docs/ERRORS_AND_FIXES.md) | 错误原因与修复记录（§1-§27，含假测试/安全/覆盖修复经验） |
| [frontend/ARCHITECTURE.md](frontend/ARCHITECTURE.md) | 前端架构 |
| [frontend/README.md](frontend/README.md) | 前端使用/开发 |

---

## 协议

[Apache License 2.0](LICENSE)
