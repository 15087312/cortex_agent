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
| L3 业务 | `modules/` | 15 个业务模块（思考、记忆、安全、感知、工具管理等） |
| L4 基础设施 | `infra/` | 模型客户端、工具注册/管理、Prompt 引擎、NLP、数据库、MCP |

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

内置专家角色：security_monitor（安全审计）、customer_expert（用户视角验收）、memory_manager（记忆管理）、memory_search（记忆搜索）、pre_gen_pipeline（价值观+安全+情感预生成分析）。

### 探针驱动激活（Probe-Driven Activation）

模型不直接调用模型，而是通过工具→探针→模型运行器的间接链路：

1. 模型调用 `delegate_task` 工具 → `ProbePermissionManager` 验证权限
2. `probe_start` 注册探针到 `ProbeCache`，发送 SYSTEM 消息到 `ModelRunnerManager`
3. Manager 创建 `ModelRunner` → 启动 `ContinuousThinker` 执行任务
4. 专家完成后写入 Blackboard → 通过 MessageBus 发送 `thinking_result` 唤醒委托方
5. 委托方模型从 Blackboard 读取结果继续推理

---

## 记忆系统

### T1-T6 多阶段记忆管线

| 阶段 | 触发时机 | 作用 |
|------|---------|------|
| **T1** | 会话预加载 | 4 路并行读取过去对话、笔记、技能、行为准则 |
| **T2** | 输入后检索 | Per-turn 缓存，FAISS 向量搜索相关历史对话 |
| **T3** | 工具后关联 | 执行工具后立即检索相关知识 |
| **T4** | 水位线压缩 | 对话长度达 70% 时触发，5 级压缩引擎（LIGHT→AGGRESSIVE） |
| **T5** | 任务后沉淀 | 任务完成后 30 秒，沉淀关键发现到长期记忆 |
| **T6** | 深度整合 | 每 12 小时运行，跨会话知识融合与演化 |

所有 T1-T6 都用 `fire-and-forget` 异步，**不阻塞**主请求流程。失败自动降级，永不抛异常中断上游。

### 7 层存储

| 层级 | 存储方式 | 用途 |
|------|---------|------|
| 短期记忆 | 内存 deque | 当前会话上下文 |
| 长期记忆 | JSONL 文件 | 跨会话持久化 |
| 分类记忆 | JSONL + 索引 | 按主题分类存储 |
| 人格记忆 | YAML 配置 | 用户偏好、交互风格 |
| 黑匣子 | JSONL 审计 | 所有决策可追溯 |
| 笔记本 | JSON 文件 | 专家笔记和发现 |
| 向量 RAG | FAISS 索引 | 语义相似度检索 |

---

## 安全系统

### 多层防护

- **输入检查** → 内容审核、意图识别
- **执行审查** → 工具调用前预检，分级审批（LOW/MEDIUM/HIGH/CRITICAL）
- **输出审查** → 回复内容合规性校验（SecurityMonitor 双层：规则引擎 + LLM 语义分析）
- **完整审计链** → JSONL 格式，SHA-256 哈希链，所有决策可追溯

### 安全门控（Security Gate）

工具执行前经过三级安全检查：
- **LOW** → 快速检查
- **MEDIUM** → 路径/命令验证
- **HIGH/CRITICAL** → LLM 审批或用户确认

### SecurityMonitor（常驻专家）

- 6 项规则检查：禁用命令、敏感数据、注入攻击、权限提升、输出操纵、写操作
- 4 种响应动作：允许 / 警告 / 阻断 / 终止
- 关键发现写入 Blackboard "最高指令"，强制影响后续所有推理

---

## MCP 工具系统

插件系统已整体移除，其生态位由 **MCP（Model Context Protocol）** 和 **AI 自创工具（create_tool）** 替代。

### 三层工具架构

| 层级 | 来源 | 说明 |
|------|------|------|
| **ToolRegistry（内置）** | `infra/tool_manager/` | 50+ 内置工具：文件操作、搜索、感知、代码执行等 |
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

### COMPANION_MODE（陪伴模式开关）

```env
# False（工作模式）— 推荐生产环境
# 完整工具委托，仅安全检测，无情感/价值观开销
COMPANION_MODE=False

# True（陪伴模式）— AI 助手模式
# 情绪+价值观全开，委托受限
COMPANION_MODE=True
```

| 功能 | 工作模式 (False) | 陪伴模式 (True) |
|------|:---:|:---:|
| SecurityExpert | ✅ | ✅ |
| ValuesExpert | ❌ | ✅ |
| EmotionExpert | ❌ | ✅ |
| 完整工具委托 | ✅ | ❌ |
| 感知系统 | 可选 | 可选 |
| 差异检测器 | 可选 | 可选 |

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
├── modules/                # 业务逻辑模块
│   ├── thinking/           # 核心编排引擎（50+ 文件）
│   │   ├── cognition/      # 认知黑板、会话生命周期、领域事件
│   │   ├── communication/  # MessageBus（点对点、广播、RPC、订阅）
│   │   ├── context/        # 上下文管理（GCP、压缩、同步、审计）
│   │   ├── core/           # ContinuousThinker、ModelRunner、ModelManager
│   │   ├── evolution/      # 价值观系统、反思状态机
│   │   ├── experts/        # RuntimeExpert 基类 + 5 个专家实现
│   │   ├── integration/    # 感知/探针集成
│   │   ├── intent/         # 委托编译器（角色名→身份映射）
│   │   ├── probes/         # 探针系统（注册、缓存、权限、工具）
│   │   └── skills/         # 技能管理器（YAML 技能加载）
│   ├── toolbuilder/        # 学习模式管线（动作规划、recipe 引擎、插件生成、Skill 生成）
│   ├── memory/             # 7 层记忆系统（短期/长期/分类/人格/黑匣/笔记本/向量RAG）
│   ├── security_system/    # 5 层安全（5 个 AST 验证器 + 审计）
│   ├── perception/         # 感知系统（文件/对话/屏幕 + 规范违反检测）
│   ├── attention/          # TF-IDF + 注意力评分
│   ├── output_system/      # 输出管线（多通道分发、情感样式）
│   ├── difference_detector/# 4 种差异源，SQLite 持久化
│   ├── management/         # GlobalMonitor、AlertEngine、HealthChecker
│   ├── database/           # SQLAlchemy + SQLite WAL、DiskCache
│   └── metrics/            # Prometheus 指标
├── infra/                  # 基础设施层
│   ├── model/              # 模型客户端（Large/Medium/Small/Lite，三格式自动检测）
│   ├── tool_manager/       # 工具注册/管理 + 50+ 内置工具 + AI 自创工具
│   │   └── tools/          # 工具实现（搜索、感知、文件、MCP、create_tool 等）
│   ├── mcp/                # MCP 协议集成
│   │   ├── transport.py    # stdio/SSE 传输层
│   │   ├── server_manager.py  # MCP 服务器生命周期管理
│   │   ├── combined_provider.py  # 本地+远程工具合并
│   │   ├── perception_client.py  # MCP 感知客户端
│   │   └── factory.py      # MCP 连接工厂
│   ├── prompts/            # Prompt 引擎（模板 + 构建器 + 约束）
│   ├── security/           # 集中安全策略
│   ├── data_process/       # 语音识别 + 图像分析
│   ├── nlp/                # NLP 服务（情感、NER、摘要）
│   ├── hardware_input/     # 硬件输入（PyAutoGUI + Serial）
│   └── utils/              # 健康检查
├── config/                 # 配置系统（Pydantic Settings）
├── cli_tui/                # Textual TUI 终端界面
├── utils/                  # 共享工具（日志、异步、JSON、时间）
├── skills/                 # YAML 技能定义文件
├── tests/                  # 测试（21 个测试文件）
├── docs/                   # 文档
├── scripts/                # 部署和运维脚本
├── data/                   # 运行时数据（记忆、缓存、索引）
├── pyproject.toml          # 项目配置
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 构建（多阶段）
├── docker-compose.yml      # Docker Compose 编排
└── TODO.md                 # 设计问题与改进计划
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| **后端框架** | Python 3.11+ / FastAPI / Uvicorn |
| **数据存储** | SQLite (SQLAlchemy WAL) / DiskCache / JSONL / FAISS |
| **模型客户端** | aiohttp / httpx（DashScope / OpenAI / Anthropic 三格式自动检测） |
| **终端界面** | Textual (TUI) / Rich |
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
| `GET /metrics` | Prometheus 指标 |
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

# 运行测试
pytest tests/ -q

# 代码检查
ruff check .

# 启动后端（开发模式）
python -m scripts.start_all
```

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
| [docs/CODE_QUALITY.md](docs/CODE_QUALITY.md) | 代码质量分析报告 |
| [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | 已知问题清单 |
| [docs/CONFIG_VALUE_EVOLUTION.md](docs/CONFIG_VALUE_EVOLUTION.md) | 价值观进化系统配置 |
| [docs/expert_cli_mode.md](docs/expert_cli_mode.md) | 专家 CLI 模式使用指南 |
| [TODO.md](TODO.md) | 设计问题与改进计划 |

---

## 协议

[Apache License 2.0](LICENSE)
