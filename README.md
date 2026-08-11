# Cortex Agent

> **类人智能后端系统** — 多模型协作 · 连续思考 · 认知黑板 · 安全审计

---

## 一键安装

### macOS / Linux
```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

### Windows（PowerShell）
在 PowerShell 中直接执行：
```powershell
iex (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/15087312/cortex_agent/main/install.ps1')
```

如果遇到执行策略限制，先运行：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
```

安装完成后运行：
```bash
cortex
```

## 手动安装

### macOS
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

### Windows
```powershell
# 1. 克隆
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent

# 2. 创建虚拟环境（推荐）
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. 安装
pip install -e .

# 4. 配置
Copy-Item .env.example .env
# 用文本编辑器编辑 .env 填入 API Key

# 5. 启动
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

启动后进入交互式终端，直接输入问题即可对话。按 `Ctrl+C` 优雅退出。

---

## 前端界面（Web UI）

Vue 3 前端提供完整的 Web 交互界面（聊天、编排、设置、仪表盘等），与后端通过 HTTP + WebSocket 通信。

### 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3（SFC `<script setup>`）+ Vite 6 |
| 状态管理 | Pinia（chat/session/config 等 store） |
| 路由 | Vue Router（懒加载页面） |
| UI | 手写 CSS（无 UI 框架），Lucide SVG 图标 |
| 测试 | Vitest + @vue/test-utils（jsdom） |
| 桌宠 | Live2D 模型（frontend/pet/） |

### 页面

`frontend/src/pages/` 共 15 个页面：

| 页面 | 说明 |
|------|------|
| Chat | 主对话（多模态附件上传、流式、思考区、待办、审批横幅） |
| Orchestration | 编排管理（角色/人设/工具权限/模型参数/激活开关/人设预设） |
| Dashboard | 仪表盘（API 请求日志、模块状态） |
| Memory | 事件记忆管理 |
| Settings | 系统设置（模型/感知/主动搭话/快捷键等） |
| Skills / Tools / Modules | 技能 / 工具 / 模块管理 |
| ScheduledTasks / Outreach | 定时任务 / 主动搭话配置 |
| Perception / Security / System / Graph / Causal | 感知 / 安全 / 系统 / 图谱 / 因果图 |

### 启动

```bash
# 开发模式（Vite HMR，默认 5173）
cd frontend && npm run dev

# 生产静态服务（默认 8765，代理 /api → 后端 8080）
python frontend/server.py

# 前端测试（Vitest）
cd frontend && npm test
```

**API 约定**：前端所有后端请求统一用 `/api/` 前缀（Vite/8765 代理会去掉前缀转到 8080）；
WebSocket 直连 `:8080/stream/ws/{session_id}`；`/audio`、`/pet` 资源保留裸路径。

### 纯对话模式（chatonly）

`CORTEX_MODE=chatonly` 时走 `modules/thinking/chat_light/` 轻量引擎（单一"总指挥"人格）：
- system prompt 由 `chat_light/prompt_composer.py` 组装，支持设置页人设/系统覆盖
- 自定义 large-tier 总指挥 agent 的人设会自动用于纯对话

---

## 桌宠

内置桌面宠物（前端 Live2D + 后端引擎）：

- **前端** `frontend/pet/`：Live2D 模型加载与交互
- **后端** `modules/desktop_pet/`：
  - `pet_engine.py` — 桌宠引擎（绑定固定主会话 `pet_main`，对话记忆延续；TTS 语音回复 + `pet_reply` 广播）
  - `pet_state.py` — 桌宠运行状态
  - `actions.py` — 动作
- 通过 `DESKTOP_PET_ENABLED` 开关启用

---

## 核心架构

### 事件驱动黑板架构（Event-Driven Blackboard）

传统多 agent 系统存在 **N² 复杂度**（所有 agent 都读全部 history），导致重复回复、超时、上下文污染。Cortex Agent 从根本上重构为**事件驱动黑板**：

- **单一真理来源**：`CognitiveBlackboard` 维护完整思维状态
- **分层上下文切片**（`ContextSlicer`）：
  - **Large 模型** → 看全局目标、计划、风险、委托、发现
  - **Supervisor** → 看任务目标、可用工具
  - **Expert** → 只看当前步骤、工具状态、最近 5 步执行历史
- **消除 N² 污染**：每个 turn 完全隔离，agent 间无噪音干扰

### 四层架构

| 层级 | 路径 | 职责 |
|------|------|------|
| L1 入口 | `cortex/` | CLI 入口，子进程编排，版本管理 |
| L2 API | `api/` | FastAPI 应用、WebSocket/SSE 流式、中间件（CORS/认证/限流/请求ID） |
| L3 业务 | `modules/` | 9 个业务模块（思考、记忆、安全、感知、输出、管理、数据库、桌宠、cortex） |
| L4 基础设施 | `infra/` | 模型客户端、工具注册/管理、MCP 协议、数据处理、硬件输入 |

依赖规则：L3→L4 允许；L4→L3 禁止。跨模块通信仅通过 MessageBus、CognitiveBlackboard 或 Protocol 接口。

### 多模型三层编排

```
用户输入
   ↓
[Large 模型] ← 战略决策、关键判断、最终整合
   ↓ 分解为子任务（delegate_task 工具调用）
[Supervisor] ← N 个主管并行接收任务
   ├─ code_supervisor → 代码架构设计
   ├─ creative_supervisor → 创意方案规划
   ├─ query_supervisor → 信息检索指导
   └─ ...
   ↓ 每个主管分配给专家（probe_start）
[Expert] ← N×M 个专家并行执行
   ├─ code_writer, code_reviewer, test_writer
   ├─ creative_writer, emotion, memory_manager
   └─ ...
   ↓ 所有结果汇聚到 CognitiveBlackboard
[CognitiveBlackboard] ← 统一的思维状态
   ↓
[Large 模型整合] ← 综合所有专家发现，生成最终答案
```

### 连续思考引擎（ContinuousThinker）

不是简单的"输入→输出"，而是多轮 ReAct 风格迭代：

- **复杂度分析**：4 维评分（推理深度、上下文范围、歧义度、任务复杂度）→ 自动分配思考预算
- **控制工具**：模型通过 `continue_thinking`（继续思考）、`respond_to_user`（输出结果）、`delegate_task`（委托任务）自主决定何时停止
- **终止规则**：7 条自动终止条件（空回复、停用词、3 次重复、Jaccard 相似度等）
- **委托跟踪**：等待子任务完成，结果通过 MessageBus 事件驱动回流

### 专家系统（RuntimeExpert）

专家有两种执行模式：

| 模式 | 适用场景 | 触发方式 |
|------|---------|---------|
| `run_loop()` | 被动等待消息驱动的长期监听 | MessageBus 事件 |
| `run_cli_mode()` | 主动执行任务直到完成 | Supervisor/ModelRunner 调用 |

内置专家角色：customer_expert（用户视角验收）、memory_manager（记忆管理）、memory_search（记忆搜索）、pre_gen_pipeline（价值观+情感预生成分析）。

### 探针驱动激活（Probe-Driven Activation）

模型不直接调用模型，而是通过工具→探针→模型运行器的间接链路：

1. 模型调用 `delegate_task` 工具 → `ProbePermissionManager` 验证权限
2. `probe_start` 注册探针到 `ProbeCache`，发送 SYSTEM 消息到 `ModelRunnerManager`
3. Manager 创建 `ModelRunner` → 启动 `ContinuousThinker` 执行任务
4. 专家完成后写入 Blackboard → 通过 MessageBus 发送 `thinking_result` 唤醒委托方
5. 委托方模型从 Blackboard 读取结果继续推理

---

## 记忆系统

### 事件记忆架构

会话结束时，LLM 将对话提炼为结构化事件，存入向量数据库供后续检索。

| 组件 | 作用 |
|------|------|
| **EventReducer** | 会话结束时调用 LLM，将对话提炼为 MemoryEvent（fact/thought/lesson/keywords） |
| **EventStore** | SQLite + FAISS 存储事件，支持向量相似度检索 |
| **EventRetrieval** | 混合检索：语义×0.35 + 重要性×0.20 + 时效×0.20 + 使用频率×0.15 + 提及频率×0.10 |
| **CausalGraph** | 事件因果关系图，支持多跳推理 |
| **CausalTree** | 因果树深度回忆，按因果链召回相关事件 |

### 记忆事件结构

```python
MemoryEvent = {
    fact: "发生了什么",
    thought: "思考/反思",
    lesson: "学到了什么（可复用经验）",
    keywords: ["关键词1", "关键词2"],
    importance: 0.7,  # 0.0-1.0
    type: "fact",     # emotion | thought | fact | strategy
    owner_id: "large_primary",  # 记忆归属
}
```

### 存储层

| 存储 | 用途 |
|------|------|
| SQLite | 事件元数据、会话历史 |
| FAISS | 向量索引，语义相似度检索 |
| JSONL | 审计日志、黑匣子 |

---

## 安全系统

### 多层防护

- **输入检查** → 内容审核、意图识别
- **执行审查** → 工具调用前预检，分级审批（LOW/MEDIUM/HIGH/CRITICAL）
- **输出审查** → 回复内容合规性校验
- **完整审计链** → JSONL 格式，SHA-256 哈希链，所有决策可追溯

### 安全门控（Security Gate）

工具执行前经过三级安全检查：
- **LOW** → 快速检查
- **MEDIUM** → 路径/命令验证
- **HIGH/CRITICAL** → LLM 审批或用户确认

---

## MCP 工具系统

插件系统已整体移除，其生态位由 **MCP（Model Context Protocol）** 和 **AI 自创工具（create_tool）** 替代。

### 三层工具架构

| 层级 | 来源 | 说明 |
|------|------|------|
| **ToolRegistry（内置）** | `infra/tool_manager/` | 85 个内置工具：文件操作、搜索、感知、代码执行、UI 检测等 |
| **MCP（远程）** | `infra/mcp/` | 通过 stdio/SSE 连接的 MCP 服务器（文件系统、数据库、浏览器等） |
| **create_tool（AI 自创）** | `infra/tool_manager/tools/create_tool.py` | 模型在运行时动态创建/编辑/删除工具，持久化到磁盘 |

### 统一路由

所有工具（内置 + MCP + AI 自创）通过 `MCPToolService` 统一路由：
- `CombinedToolProvider` 合并本地 ToolRegistry + 远程 MCP Server 的工具列表
- 工具名冲突时优先保留内置工具，跳过同名 MCP 工具
- `ToolManagerPermissionAdapter` 确保所有调用经过安全权限检查

### 学习模式（Learn Mode）

学习模式是一个**瞬态**状态，不固定存在于 EXECUTION_MODE 中：

1. 模型调用 `request_mode_change("learn")` 进入学习模式
2. `model_runner` 注入学习提示词并自动执行 `run_learn_pipeline`
3. 管线步骤：打开应用 → 截图 → OmniParser 元素检测 → AI 规划动作序列 → 执行录制 → 生成插件
4. 完成后自动恢复原始执行模式（plan/edit/yolo/control）

---

## 运行模式

### 执行模式（EXECUTION_MODE）

| 模式 | 行为 |
|------|------|
| `plan` | 只读 — 禁止所有写操作 |
| `edit` | 确认 — 写操作前需用户确认 |
| `yolo` | 宽松 — 仅安全专家检测，跳过用户确认 |
| `control` | 用户完全控制 — MEDIUM+工具需用户单独确认 |

### 其他关键配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `PERCEPTION_ENABLED` | True | 感知系统（文件/对话/屏幕监控 + 规范违反检测） |
| `DIFFERENCE_DETECTOR_ENABLED` | True | 差异检测器（4种差异源，1Hz 心跳） |
| `VALUE_ALIGNMENT_HANDLER_ENABLED` | True | 价值观对齐被动监测 |
| `PROACTIVE_OUTREACH_ENABLED` | True | 主动搭话（闲置触发） |
| `SECURITY_REVIEW_MODE` | auto | 安全审查模式（llm / user / auto） |

完整配置见 [.env.example](.env.example) 和 [docs/CONFIG_VALUE_EVOLUTION.md](docs/CONFIG_VALUE_EVOLUTION.md)。

---

## 项目结构

```
ai_backend/
├── cortex/                 # CLI 入口（cortex 命令）
├── api/                    # FastAPI 应用 + WebSocket/SSE
├── frontend/               # Vue 3 前端（聊天/编排/设置/仪表盘等 15 个页面）
│   ├── src/                # 前端源码（Vue SFC + Pinia + Vue Router）
│   ├── pet/                # 桌宠前端（Live2D 模型 + 交互逻辑）
│   ├── server.py           # 静态服务（默认 8765，代理 /api 到后端 8080）
│   └── package.json        # npm 依赖与脚本（dev/build/test）
├── modules/                # 业务逻辑模块
│   ├── thinking/           # 核心编排引擎
│   │   ├── chat_light/     # 纯对话模式（chatonly）轻量引擎
│   │   ├── cognition/      # 认知黑板、会话生命周期、领域事件
│   │   ├── communication/  # MessageBus（点对点、广播、RPC、订阅）
│   │   ├── context/        # 上下文管理（GCP、压缩、同步、审计）
│   │   ├── core/           # ContinuousThinker、ModelRunner、ModelRunnerManager
│   │   ├── probes/         # 探针系统（注册、缓存、权限、工具）
│   │   └── skills/         # 技能管理器（YAML 技能加载）
│   ├── memory/             # 事件记忆系统（EventStore + FAISS + 因果树）
│   ├── security_system/    # 安全系统（分级审批 + 审计 + fail-closed）
│   ├── perception/         # 感知系统
│   │   ├── detectors/      # 检测器（窗口、帧差、OCR、语音）
│   │   ├── screen/         # 屏幕理解（touchpoint + CDP + 视觉模型）
│   │   ├── events/         # 事件总线
│   │   └── state/          # 世界状态管理
│   ├── desktop_pet/        # 桌宠引擎（pet_engine/pet_state/actions）
│   ├── output_system/      # 输出管线（多通道分发）
│   ├── management/         # 监控、告警、健康检查、管理 API
│   └── database/           # SQLite + DiskCache
├── infra/                  # 基础设施层
│   ├── model/              # 模型客户端（Large/Medium/Small，格式由 config/providers 统一）
│   ├── tool_manager/       # 工具注册/管理 + 85 个内置工具
│   │   └── tools/          # 工具实现（搜索、感知、文件、MCP、create_tool 等）
│   ├── mcp/                # MCP 协议集成
│   ├── data_process/       # 语音识别 + 图像分析 + CDP 扫描
│   └── hardware_input/     # 硬件输入（PyAutoGUI）
├── config/                 # 配置系统（Pydantic Settings）
│   ├── providers/          # 模型 API 格式适配层（openai/anthropic/dashscope）
│   └── prompts/            # 提示词组装（roles.yaml / base.yaml / composer）
├── cli_tui/                # Textual TUI 终端界面
├── utils/                  # 共享工具（日志、异步、JSON、时间）
├── skills/                 # YAML 技能定义文件
├── tests/                  # 测试（unit 105 个文件 + integration + external）
├── docs/                   # 文档（含 ERROR_AND_FIXES 修复记录）
├── scripts/                # 部署和运维脚本（含 fix_macos_libomp.py）
├── data/                   # 运行时数据（记忆、缓存、索引）
├── pyproject.toml          # 项目配置
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 构建（多阶段）
└── docker-compose.yml      # Docker Compose 编排
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| **后端框架** | Python 3.11+ / FastAPI / Uvicorn |
| **前端** | Vue 3 / Vite 6 / Pinia / Vue Router / Vitest |
| **数据存储** | SQLite (SQLAlchemy WAL) / DiskCache / JSONL / FAISS |
| **模型客户端** | aiohttp / httpx，格式由 `config/providers` 统一（DashScope / OpenAI / Anthropic） |
| **终端界面** | Textual (TUI) / Rich |
| **桌宠** | Live2D + TTS（frontend/pet + modules/desktop_pet） |
| **搜索引擎** | DuckDuckGo / 搜狗 / 必应 / 百度 / crawl4ai（无头浏览器） |
| **NLP** | jieba / sentence-transformers / tiktoken |
| **ML（可选）** | PyTorch / transformers / faiss-cpu / mlx-lm（Apple Silicon） |
| **监控** | Prometheus / psutil |
| **部署** | Docker / Docker Compose / PyInstaller |

---

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查（healthy / degraded / critical） |
| `GET /` | 系统信息和版本 |
| `WS /stream/ws/{session_id}` | WebSocket 实时对话 |
| `GET /stream/sse/{session_id}` | SSE 流式对话 |
| `GET /config` | 获取配置 |
| `PUT /config/{key}` | 更新配置（需 API Key，白名单限制） |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行后端测试（unit + integration，跳过 external/slow）
pytest tests/ -q -m "not external and not slow"

# 运行前端测试（Vitest）
cd frontend && npm test

# 代码检查
ruff check .

# 启动后端（开发模式）
python -m scripts.start_all
```

---

## 测试

共 **137 个测试文件**（`tests/`），按环境分层：

| 层级 | 目录 | 说明 |
|------|------|------|
| **unit** | `tests/unit/` | 纯单元测试（mock 外部依赖），秒级完成 |
| **integration** | `tests/integration/` | 真实临时库/组件协作，需 `-m "not external"` 排除真环境用例 |
| **external** | `tests/external/` | 需真实模型/屏幕/硬件的用例，默认跳过 |

### 运行

```bash
# 全量（推荐）
pytest tests/ -m "not external and not slow"

# 单个文件
pytest tests/unit/test_providers.py -q

# 前端测试
cd frontend && npm test
```

### 测试隔离原则（重要）

- **绝不触碰生产库**：memory/conscience/causal 类测试用临时 SQLite + monkeypatch 单例
  （`EventStore._instance`/`CausalGraph._instance` 等），并重置 `EventRetrieval._instance`
- **重库加载放宽 timeout**：触发真实 embedding/BERT 加载的测试加模块级
  `pytestmark = pytest.mark.timeout(60)`（如 conscience/causal_graph/image_analyzer）
- **线程清理**：起后台线程的类（如 ApiLogStore）必须提供 `stop()`，fixture teardown 调用
- **禁止吞错掩盖**：`except: pass` 会掩盖真实 bug（如 `test_context_slicer` 曾因签名过时被吞）
- **测试假对象须与真实模型字段一致**（§20 教训）；**只断言调用不查结果**是假测试（§25）

### 覆盖情况

多轮补测后全量 **1700+ 项通过**，重点覆盖：
- 0% → 覆盖：utils、config/providers、identity_loader、values_store、tool_discovery、
  context_budget、ModelRunnerManager
- 全端点：management 管理 API、编排/人设/激活开关消费链
- 核心：ModelRunner 方法、模型流式解析、记忆链路、因果树、agent 工具（含安全检测）、web_search
- 安全回归：fail-closed（权限/拦截异常拒绝）、pipe-to-shell 漏检修复、附件契约

---

## Docker 部署

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止
docker-compose down
```

资源限制：4GB 内存，2 CPU。健康检查：每 30 秒轮询 `/health`。

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 详细架构设计文档 |
| [docs/THINKING_ARCHITECTURE.md](docs/THINKING_ARCHITECTURE.md) | 思考模块内部架构（双层 ReAct 循环） |
| [docs/MEMORY_INJECTION.md](docs/MEMORY_INJECTION.md) | 记忆系统与注入链路 |
| [docs/CONFIG_VALUE_EVOLUTION.md](docs/CONFIG_VALUE_EVOLUTION.md) | 价值观进化系统配置 |
| [docs/ERRORS_AND_FIXES.md](docs/ERRORS_AND_FIXES.md) | 错误原因与修复记录（§1-§27，含假测试/安全/覆盖修复经验） |
| [frontend/ARCHITECTURE.md](frontend/ARCHITECTURE.md) | 前端架构说明 |
| [frontend/README.md](frontend/README.md) | 前端使用/开发说明 |

---

## 协议

[Apache License 2.0](LICENSE)
