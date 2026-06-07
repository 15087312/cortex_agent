# Humanoid AGI — 类人智能架构系统

> 多模型协作 · 连续思考 · 三层记忆 · 原生 API Tool Calls · CognitiveBlackboard · 实时流式输出

---

## 项目概述

Humanoid AGI 是一个工业级的类人智能后端系统，采用分层解耦架构，实现：

- **多模型协作**：大/中/小三层模型分层调度，总指挥+主管+专家，模型间通过 CognitiveBlackboard 共享状态
- **连续思考**：多轮推理链，`continue_thinking` 控制终止，支持多轮迭代+工具调用
- **原生 API Tool Calls**：基于模型的原生 function calling
- **三层记忆**：会话预加载、工具后关联检索、任务后沉淀
- **CognitiveBlackboard**：统一认知黑板，所有 Agent 读写同一状态源（SharedDialog 已完全移除）
- **注意力系统**：TF-IDF + 注意力评分，负责候选上下文排序与资源分配
- **实时流式**：WebSocket + SSE 推送，事件驱动管道
- **委托循环检测**：同一角色连续委托失败超过 3 次时自动拦截并提示策略调整
- **安全系统**：多层验证、审计日志、权限控制、输入输出双向校验
- **技能系统**：YAML 定义角色+规章+流程，大模型按技能扮演领域专家
- **时间感知**：大模型知道当前时间、距上次对话时长、谁在跟它说话
- **联网搜索**：web_search crawl4ai 无头浏览器抓取全文
- **三格式 API**：支持 OpenAI / DashScope / Anthropic 三种 API 格式，URL 自动检测
- **价值观自动进化系统**：AI 可动态修改价值观规则实现自适应，项目操作规范由系统硬编码确保安全约束，用户可手动修改但需重启

**技术栈**：Python 3.13 + FastAPI + SQLite + diskcache + FAISS + crawl4ai


---

## 系统架构

### 四层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     L1: 入口层 (main.py / api/main.py)            │
│                   FastAPI 应用启动、生命周期管理、路由注册          │
└────────────────────────────┬────────────────────────────────────┘
│
┌──────────────┼──────────────┐
▼              ▼              ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│   L2: API 层      │ │ L3: 模块层    │ │  L4: 基础设施层   │
│   api/main.py     │ │ modules/     │ │  infra/          │
│   路由 + 中间件    │ │ 业务逻辑      │ │  模型/工具/数据处理 │
└──────────────────┘ └──────────────┘ └──────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据存储层                                 │
│   SQLite (关系数据) + diskcache (缓存) + JSONL (长期记忆)          │
│   + FAISS (向量检索) + JSON (配置/人格)                           │
└─────────────────────────────────────────────────────────────────┘
```

### 核心设计理念

#### 1. 分层解耦
- **L1 入口层**：应用启动、全局初始化、生命周期管理
- **L2 API 层**：HTTP 路由、中间件、请求处理、WebSocket/SSE
- **L3 模块层**：业务逻辑、模块间事件通信
- **L4 基础层**：模型调用、工具管理、数据处理、硬件控制

#### 2. 抽象层交互（模块间要用抽象，模块内部自由实现）
- **模块间通信**：所有跨模块调用必须通过抽象接口（Protocol/ABC）或工厂函数
- **模块内部实现**：模块内部可自由使用具体实现，无需受抽象限制
- **工厂模式**：通过 `get_*_port()` 工厂函数获取服务实例
- **类型注解**：使用 `*Port` Protocol 作为参数/返回类型注解
- **依赖方向**：L3 模块 → L4 基础设施（通过接口），L4 不得依赖 L3

#### 3. 全局错误处理
为确保系统稳定性和可调试性，所有模块的错误必须统一收集并输出到同一个终端，避免错误被吞掉或分散到多个日志流。

**核心原则：**
- **错误不吞**：所有 `except` 块必须要么处理错误（能够恢复），要么重新抛出或上报到全局错误总线
- **错误上下文**：每个错误必须包含模块名、函数名、关键参数等上下文信息，便于定位问题
- **统一格式**：所有错误输出采用相同的格式：时间 | 级别 | 模块:函数:行号 | 错误详情 + 堆栈
- **全局单例**：整个系统只有一个错误总线实例，所有模块通过它报错

**实现方式：**
1. 使用 `modules/management/core/error_bus.py` 中的 `GlobalErrorBus` 单例和 `ErrorContext` 数据类
2. 在项目入口（如 `main.py` 或 `api/main.py`）初始化 asyncio 异常处理器
3. 在业务代码中捕获不可恢复的错误时，调用 `error_bus.report_error(e, ErrorContext(...))`
4. 所有未被捕获的异常（主线程、子线程、asyncio 任务）会自动被全局错误总线接管并输出

**示例：**
```python
from modules.management.core.error_bus import error_bus, ErrorContext

def process_data(data):
try:
# 业务逻辑
result = risky_operation(data)
return result
except ValueError as e:
# 可以处理的错误：记录警告并使用默认值
logger.warning(f"数据格式错误，使用默认值: {e}")
return get_default_value()
except Exception as e:
# 无法处理的错误：上报到全局总线
error_bus.report_error(
e,
ErrorContext(
module="data_processor",
function="process_data",
extra={"data_length": len(data) if data else 0}
)
)
# 根据业务决定是否重新抛出
raise
```

#### 4. 模块自治
每个模块独立运行，通过统一接口和事件总线通信：

```python
class BaseModule(ABC):
module_name: str
module_level: int

@abstractmethod
def start(self) -> bool
@abstractmethod
def stop(self) -> bool
@abstractmethod
def handle_task(self, task_type: str, params: Dict) -> Any
```

#### 4. 事件驱动管道
`UnifiedScheduler.process()` 在 15 个阶段依次发出类型化事件，流式客户端 (WebSocket/SSE) 实时消费，时序数据库和黑匣子也订阅事件。

#### 5. 零依赖存储
- SQLite：关系型数据（短期记忆、元数据）
- diskcache：高性能缓存（工作记忆、会话状态）
- JSONL：长期记忆（分类存储、增量写入）
- FAISS：向量语义检索
- 无需 Docker/Redis/MongoDB

---

## 目录结构

```
ai_backend/
│
├── main.py                    # FastAPI 入口（端口 8000）
├── cli_tui/                   # ★ Textual TUI 客户端 (多模型协作界面)
│   ├── main.py                #   入口 + argparse CLI 参数解析
│   ├── app.py                 #   Textual App
│   ├── state.py               #   全局响应式状态 (reactive)
│   ├── commands.py            #   命令注册表 (/help, /status, /tools, /export 等)
│   ├── screens/               #   界面
│   │   ├── repl.py            #     主 REPL 界面
│   │   └── help_screen.py     #     帮助弹窗
│   ├── widgets/               #   组件
│   │   ├── header.py          #     顶栏: 连接状态 + session + 统计
│   │   ├── message_list.py    #     消息列表: 共享对话框 + AI 回复
│   │   ├── prompt_input.py    #     输入框: 历史 + 命令检测
│   │   ├── status_line.py     #     底栏: 模型/token/耗时
│   │   └── tool_panel.py      #     工具调用追踪面板
│   └── services/              #   服务
│       ├── ws_client.py       #     WebSocket 客户端 (自动重连)
│       └── api_client.py      #     HTTP API 客户端
├── cli_tool_trace.py          # 工具调用追踪 CLI (XML 解析可视化)
├── cli_model_comm.py          # 多模型通信可视化 CLI (WebSocket + Rich Live)
├── monitor_cli.py             # 实时监控仪表盘 CLI
├── .env / .env.example        # 环境变量配置
├── requirements.txt           # 生产依赖
├── requirements-dev.txt       # 开发依赖
├── pyinstaller.spec           # EXE 打包配置
├── pytest.ini                 # 测试配置
├── docker-compose.yml         # Docker 部署
│
├── api/                       # L2: API 路由层
│   ├── main.py                # FastAPI 应用、中间件、全部路由
│   ├── dependencies.py        # 依赖注入
│   └── middleware/             # 自定义中间件
│
├── modules/                   # L3: 业务模块层 (16 个模块)
│   ├── module_base.py         # 模块基类
│   │
│   ├── unified_scheduler/     # ★ 统一调度中枢
│   │   ├── coordinator.py     #   UnifiedScheduler — 主协调器 (1610行)
│   │   ├── resource_core.py   #   资源核心 — 硬件感知资源分配
│   │   └── hardware_detector.py # 硬件检测器
│   │
│   ├── context/               # ★ 全局上下文管理器 (GCM)
│   │   ├── global_context_pool.py  # GlobalContextPool 单例 — 中央数据池
│   │   ├── types.py                # 数据类型: FileInfo, EventRecord, ContextView 等
│   │   ├── compression.py          # CompressionEngine — 5 级上下文压缩
│   │   ├── context_view.py         # ContextViewGenerator — 角色视图生成
│   │   ├── synchronizer.py         # Synchronizer — 文件监听 + 外部同步
│   │   ├── auditor.py              # Auditor — 健康审计 + 冗余检测
│   │   └── wire.py                 # 集成适配器 (coordinator/thinker/probe)
│   │
│   ├── thinking/              # 思维系统
│   │   ├── core/
│   │   │   ├── continuous_thinker.py  # 连续思考器 (多轮 ReAct + 委托 + 唤醒)
│   │   │   ├── model_runner.py        # 模型运行器 (工具循环 + 委托分发 + 结果回传)
│   │   │   ├── process_collector.py   # 思考过程收集器 (快照 + control_decision)
│   │   │   └── delegation_port.py     # 委托端口 (ProbeDelegationAdapter)
│   │   ├── intent/
│   │   │   └── delegation_compiler.py # 委托角色解析 (角色名 → tier + identity_key)
│   │   ├── identity.py        #   身份模板 + 工具白名单 (DEFAULT_TOOL_WHITELISTS)
│   │   ├── session/           #   会话生命周期 + CognitiveBlackboard
│   │   ├── experts/           #   专家系统 (情感/价值观/安全，并行执行)
│   │   ├── integration/       #   探针集成 + 感知集成
│   │   ├── systems/           #   专家系统管理器
│   │   ├── evolution/         #   自进化 (自我反思 + 价值观进化)
│   │   └── utils/             #   思考分割/价值观约束/规则匹配
│   │
│   ├── cognition/             # 认知黑板 (CognitiveBlackboard，已替代 SharedDialog)
│   │   ├── blackboard.py      #   统一认知黑板 + MessageBus 广播
│   │   └── context_slicer.py  #   上下文切片器 (按角色裁剪)
│   │
│   ├── memory/                # 六层记忆系统 + 主动时机优化
│   │   ├── core/
│   │   │   ├── memory_manager.py              # 聚合管理器 (835行)
│   │   │   ├── session_memory_preloader.py    # T1: 会话启动预加载 (~150行)
│   │   │   ├── post_tool_memory_retriever.py  # T3: 工具后关联检索 (~120行)
│   │   │   ├── post_task_memory_extractor.py  # T5: 任务后记忆提取 (~200行)
│   │   │   ├── context_compressor.py          # T4: 水位线上下文压缩 (~100行)
│   │   │   ├── memory_scheduler.py            # 后台调度器 + T6 深度整合 (357行)
│   │   │   ├── importance_scorer.py           # 重要性评分 (规则 + 模型)
│   │   │   ├── long_term.py                   # 长期记忆 (JSONL + FAISS)
│   │   │   ├── personality.py                 # 人格记忆
│   │   │   ├── blackbox.py                    # 黑匣子 (不可变审计日志)
│   │   │   └── notebook.py                    # AI 记事本 (版本化笔记)
│   │   ├── faiss_index.py              # FAISS 向量索引
│   │   └── embeddings.py               # 嵌入生成
│   │
│   ├── communication/          # 模型间通信
│   │   └── message_bus.py          # ModelMessageBus (350+行)
│   │
│   ├── attention/             # 注意力系统
│   │   ├── core/
│   │   │   ├── attention_core.py           # 注意力决策 (TF-IDF + 关键词)
│   │   │   ├── memory_retriever.py         # 三级记忆候选检索 (Retrieval Layer)
│   │   │   └── memory_attention_scorer.py  # 多维注意力评分
│   │   ├── weight_calculator.py
│   │   └── task_scheduler.py
│   │
│   ├── perception/            # 感知系统
│   │   ├── manager.py         #   感知管理器
│   │   └── integration.py     #   感知-思考集成器
│   │
│   ├── output_system/         # 输出系统
│   │   ├── core.py            #   输出流水线 + 验证
│   │   ├── distributor.py     #   多通道分发
│   │   ├── styler.py          #   输出样式
│   │   └── input_controller.py #  输入控制 (委托层)
│   │
│   ├── resource/              # 资源管理系统
│   │   ├── resource_manager.py  # 资源管理器 (单例)
│   │   ├── api.py               # 资源监控 API
│   │   ├── init_probes.py       # 探针初始化
│   │   ├── scheduler.py         # 资源调度器
│   │   ├── detector.py          # 硬件检测
│   │   ├── strategy.py          # 资源策略
│   │   └── pool.py              # 资源池
│   │
│   ├── security_system/       # 安全系统
│   │   ├── core.py            #   安全核心
│   │   ├── validators/        #   多层验证器
│   │   └── audit_logger.py    #   审计日志
│   │
│   ├── management/            # 管理控制台
│   │   ├── api.py             #   管理 API
│   │   └── core/
│   │       ├── global_monitor/ #  全局监控 (10 个文件)
│   │       ├── timeseries/     #  时序数据库
│   │       ├── alert/          #  告警引擎
│   │       └── health/         #  健康检查
│   │
│   ├── plugin_system/         # 插件系统
│   ├── database/              # 数据库封装 (SQLAlchemy + Repository)
│   └── metrics/               # 指标收集
│
├── infra/                     # L4: 基础设施层
│   ├── model/                 #   多模型客户端
│   │   ├── large_model_client.py   # 大模型 (Qwen-Max, MoE 235B)
│   │   ├── medium_model_client.py  # 中模型 (QwQ-32B, 推理增强)
│   │   └── small_model_client.py   # 云端 7B 模型 (qwen2.5-7b-instruct)
│   │
│   ├── model_router.py        #   三级模型路由 (large → medium → small)
│   ├── prompts/               #   提示词管理 (动态构建 + 防复读约束)
│   ├── tool_manager/          #   原生 API Tool Calls
│   │   ├── tool_registry.py   #     工具注册表 + ToolInfo.to_xml_schema()
│   │   ├── tool_manager.py    #     工具管理器 (核心协调层)
│   │   ├── tool_executor.py   #     统一执行器 + 参数自动纠错
│   │   ├── context_budget.py  #     上下文预算分配器 (per_tool → 按策略动态分配)
│   │   ├── tool_discovery.py  #     运行时工具发现 + Top-K 智能选择
│   │   ├── api.py             #     /tools 管理 API (统计/审计/热重载/运行时安全)
│   │   └── tools/             #     内置工具 (自动扫描, 20 个模块, 78 个工具)
│   │       ├── __init__.py    #       pkgutil 自动扫描，新增工具无需改此处
│   │       ├── calculator.py  #       计算器 (calc/advanced/sum/avg)
│   │       ├── file_manager.py#       文件 CRUD (list/read/write/delete/info/search)
│   │       ├── file_edit.py   #       精确文件编辑 (查找替换)
│   │       ├── file_extra.py  #       文件元信息 (append/exists)
│   │       ├── exec_command.py#       Shell/Python 执行 (exec/run_command/run_python/kill)
│   │       ├── web_search.py  #       DuckDuckGo 搜索
│   │       ├── web_fetch.py   #       HTTP 页面获取
│   │       ├── git_tools.py   #       Git 操作 (status/add/commit/push/pull/diff)
│   │       ├── dev_tools.py   #       开发辅助 (AST/引用/文档/测试/格式化)
│   │       ├── memory_matcher.py #    记忆匹配检索 (match/score/batch_filter)
│   │       ├── mouse_keyboard.py #   桌面自动化 (鼠标/键盘/快捷键)
│   │       ├── security_tools.py #   安全扫描 (SAST/密钥/依赖审计)
│   │       ├── runtime_security.py # 运行时安全策略
│   │       ├── mcp_tools.py   #       MCP 协议工具
│   │       ├── plugin_tools.py#       插件生命周期
│   │       ├── rag_tools.py   #       RAG 索引/查询/更新
│   │       ├── audit_tools.py #       审计日志/变更追踪
│   │       ├── attention.py   #       注意力水平工具
│   │       ├── external_api.py#       HTTP GET/POST 外部 API
│   │       └── todo.py        #       待办事项管理
│   ├── data_process/          #   数据处理 (图像/语音/文本)
│   ├── hardware_input/        #   硬件输入控制 (鼠标/键盘/串口)
│   ├── database/              #   数据库会话 + 向量 DB + 时序 DB
│   ├── nlp/                   #   NLP 服务
│   ├── mq/                    #   消息队列 (Kafka 可选)
│   └── utils/                 #   通用工具
│
├── config/                    # 配置管理
│   ├── settings.py            #   全局配置 (Pydantic Settings)
│   ├── model_config.py        #   模型配置
│   ├── memory_config.py       #   记忆配置
│   ├── attention_config.py    #   注意力配置
│   └── output_config.py       #   输出系统配置
│
├── utils/                     # 共享工具
│   ├── logger.py              #   Loguru 日志
│   ├── async_utils.py         #   异步工具
│   └── json_utils.py          #   JSON 工具
│
├── data/                      # 运行时数据 (gitignored)
│   ├── memory.db              #   SQLite 数据库
│   ├── cache/                 #   diskcache 缓存
│   ├── memory/                #   文件存储
│   │   ├── long_term/         #   长期记忆 (JSONL)
│   │   ├── blackbox/          #   黑匣子日志
│   │   └── personality.json   #   人格配置
│   └── logs/                  #   日志文件
│
├── tests/                     # 测试 (10 个文件, 79K+ 行)
├── docs/                      # 文档 (12 个文件)
├── scripts/                   # 运维脚本
└── plugins/                   # 本地插件
```

---

## 🚀 价值观自动进化系统 (Phase 4)

### 概述

Humanoid AGI 实现了完整的**价值观自动进化系统**，允许 AI 在遵守项目规范约束的前提下，通过检测行为偏差来动态修正自身的行为规则。系统包括 4 个阶段的实现：

**核心特性**：
- ✅ **实时规范检测**（感知系统）：检测输出中的价值观和操作规范违反
- ✅ **项目操作规范**（硬编码）：8 项规范约束，AI 无法修改
- ✅ **大模型修改工具**：`modify_value_system` 工具支持动态修改行为规则
- ✅ **后台趋势分析**（差异检测器）：统计长期对齐度，供大模型定期查询
- ✅ **模式隔离**：陪伴模式启用全套指导，工作模式仅保留安全检测

### 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│              实时主流程（同轮响应）                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SecurityExpert                                             │
│  ├─ 安全风险检测 (security_monitor.py)                      │
│  └─ 项目操作规范 (8 项硬编码规则)                           │
│                                                              │
│  RuleCompliancePerception                                   │
│  ├─ 读取输出内容                                           │
│  ├─ 与 core_values.txt 规则对比                             │
│  └─ 生成违反事件到感知系统                                 │
│                                                              │
│  → 大模型立即看到并调整（无延迟）                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│              后台被动监测（定期统计）                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  DifferenceDetector + ValueAlignmentDifferenceSource        │
│  ├─ 每 30 秒计算一次对齐度评分 (0-1)                       │
│  ├─ 统计严重程度分布                                        │
│  └─ 记录进化历史                                            │
│                                                              │
│  → 大模型定期查询，自主决定修改规则                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 4 阶段实现

| 阶段 | 组件 | 职责 | 文件 |
|------|------|------|------|
| 1 | SecurityMonitor | 运行时检测价值观规范 | `modules/thinking/experts/security_monitor.py` |
| 2 | ValueAlignmentDifferenceSource | 计算对齐度评分，生成差异 | `modules/difference_detector/sources/value_alignment_source.py` |
| 3 | ValueAlignmentHandler + DifferenceDetector | 差异回调注册和处理 | `modules/thinking/value_alignment_handler.py` |
| 4 | modify_value_system 工具 | 大模型调用修改规则 | `infra/tool_manager/tools/value_tools.py` |

**新增**：
| 组件 | 职责 | 文件 |
|------|------|------|
| RuleCompliancePerception | 实时规范违反检测 | `modules/perception/rule_compliance_perception.py` |

### 关键概念

#### 1. 价值观 vs. 项目规范

**价值观规则**（AI 可动态修改）：
- 存储在 `modules/thinking/evolution/prompts/core_values.txt`
- 示例："不要机械地说我是AI"、"回复要简洁有力"
- 大模型可通过 `modify_value_system` 工具动态修改
- 质量门控确保规则有效性

**项目操作规范**（AI 无法修改，用户可配置化修改）：
- 定义在 `config/project_guidelines.yaml` 中
- 8 项规范：代码变更、数据库修改、API 变更、配置修改、文件操作、外部调用、日志记录、依赖更新
- **AI 无法通过工具修改**，确保系统安全约束不被绕过
- **用户/管理员可直接编辑 YAML 文件**，下一轮处理时自动生效（无需重启）
- 权限隔离：protect system guardrails from AI tampering

#### 2. 运行模式隔离

**陪伴模式** (`COMPANION_MODE=True`)：
- ✅ SecurityExpert（安全检测 + 项目规范）
- ✅ RuleCompliancePerception（规范违反检测）
- ✅ ValuesExpert（价值观指导）
- ✅ EmotionExpert（情绪反馈）
- ❌ 工具委托关闭

**工作模式** (`COMPANION_MODE=False`)：
- ✅ SecurityExpert（安全检测 + 项目规范）
- ✅ RuleCompliancePerception（规范违反检测）
- ❌ ValuesExpert（关闭）
- ❌ EmotionExpert（关闭）
- ✅ 工具委托完整开启

#### 3. 检测机制

**实时检测** (RuleCompliancePerception)：
- 输出生成后立即检查
- 与 core_values.txt 规则对比
- 生成感知事件注入系统提示词
- 大模型同轮内看到并调整

**被动监测** (ValueAlignmentHandler)：
- 计算近期对齐度评分（0-1）
- 统计持续性违反模式
- 供大模型定期查询
- 大模型自主判断是否修改规则

### 配置与使用

详见 [CONFIG_VALUE_EVOLUTION.md](CONFIG_VALUE_EVOLUTION.md)

**快速开始**：
```env
# .env
COMPANION_MODE=False              # 工作模式
PERCEPTION_ENABLED=True           # 启用感知系统规范检测
DIFFERENCE_DETECTOR_ENABLED=True  # 启用后台差异检测
VALUE_ALIGNMENT_HANDLER_ENABLED=True  # 启用被动监测
```

**大模型查看当前规则**：
```python
from infra.tool_manager.tools.value_tools import get_current_values
rules = get_current_values(format="compact")
```

**大模型修改规则**：
```python
await modify_value_system(
    action="add_rule",
    section="行为准则",
    rule="输出要简洁有力",
    reason="检测到过长回复"
)
```

---

## 架构决策标准（绝对规则）

以下规则定义了每个模块的职责边界，所有开发必须遵守。

### 思维系统 (`modules/thinking/`)

| 规则 | 说明 |
|------|------|
| **只协调思考循环，不构建业务提示词** | `ContinuousThinker` 负责多轮思考循环、终止检测、委托追踪、最终整合输出和结果回传。业务上下文 prompt 必须委托给 `ContextManager`；循环控制契约和最终整合防泄漏 prompt 属于思考循环内部控制。 |
| **控制工具归属思考循环** | `continue_thinking` 是循环生命周期控制工具，由 `ContinuousThinker` 消费；`ModelRunner` 只负责模型实例运行和结果发布。 |
| **最终输出只能来自最终整合** | 对外发布或回传给其他模型的结果必须来自最终整合函数的 `final_output`，不得回退到原始思考过程、委托指令、工具日志或 `history_thoughts[-1]`。 |
| **思考过程通过抽象收集** | 其他模块需要读取思考过程时，只能依赖 `ThinkingProcessCollector` / `ThinkingProcessCollectorFactory` 抽象，不得读取 `ContinuousThinker` 私有状态。 |
| **不直接调用工具** | 工具调用走 `tool_manager`，不得直接 import 或调用工具函数。 |
| **不直接访问存储** | 记忆读写通过 `MemoryManager`，不得直接操作 SQLite/JSONL/FAISS。 |

### 上下文模块 (`modules/context/`)

| 规则 | 说明 |
|------|------|
| **唯一负责 prompt 构建** | `ContextManager` 是唯一允许拼装完整 prompt 的地方。所有模块（包括 `ContinuousThinker`）必须调用 `context_manager.build_prompt()` 获得最终 prompt。 |
| **不拥有思考逻辑** | `ContextManager` 只组装上下文，不决定思考流程、不配置探针、不管理循环。 |
| **GCM 是全局状态唯一来源** | `GlobalContextPool` 持有所有共享状态。其他模块不得在本地缓存或维护全局数据的副本。 |

### 阶段化思考 (`modules/thinking/core/stage_thinker.py`)

| 规则 | 说明 |
|------|------|
| **只管理认知阶段转换** | `CognitiveController` 负责 Planning→Execution→Critic→Reflection 的阶段流转。不得包含记忆检索、工具调用执行或 prompt 构建。 |
| **不替代 ContinuousThinker** | 阶段化思考是 ContinuousThinker 的替代/进化方案，两者不同时运行相同任务。 |

### 记忆系统 (`modules/memory/`)

| 规则 | 说明 |
|------|------|
| **唯一数据持久化入口** | 所有记忆写入和读取必须经过 `MemoryManager`。其他模块不得直接操作 SQLite、JSONL、FAISS 或 diskcache。 |
| **不包含业务逻辑** | 记忆系统负责存储和检索，不包含推理、验证或决策逻辑。 |
| **所有时机优化必须 fire-and-forget** | T1~T6 时机触发必须使用 `asyncio.create_task`，绝不阻塞主流程。失败时优雅降级（`logger.debug` + 返回原有行为），绝不抛异常中断调用方。 |

### 注意力系统 (`modules/attention/`)

| 规则 | 说明 |
|------|------|
| **只做评分和排序** | AttentionCore 和 MemoryAttentionScorer 对候选上下文评分排序，不决定最终 prompt 中放什么，不执行检索，不构建提示词。 |
| **不决定模块调度** | 注意力输出是调度器的参考输入，不自行决定激活或休眠哪些模块。 |

### 统一调度器 (`modules/unified_scheduler/`)

| 规则 | 说明 |
|------|------|
| **唯一请求处理管道** | `UnifiedScheduler.process()` 是请求进入系统的唯一入口。其他模块不得定义自己的请求处理主流程。 |
| **不持有业务状态** | 调度器编排阶段但不存储业务数据。所有阶段结果同步到 GCM。 |

### 工具系统 (`infra/tool_manager/`)

| 规则 | 说明 |
|------|------|
| **唯一工具执行入口** | 所有工具调用必须经过 `tool_manager.call_tool_sync()`。任何模块不得直接执行工具函数。 |
| **不包含业务逻辑** | 工具系统是基础设施：注册、解析、执行、权限检查。不理解业务语义。 |
| **内置工具按职责拆分** | 每个内置工具一个独立 `.py` 文件，通过 `@ToolRegistry.register` 装饰器注册。`tools/__init__.py` 自动扫描目录，新增工具无需手动 import。 |
| **动态上下文预算** | `ContextBudgetAllocator` 按策略为每个工具动态分配上下文预算，替代旧的 per_tool 固定截断。 |
| **运行时工具发现** | `ToolDiscovery` 基于语义相关性 + 使用频率做 Top-K 选择，减少上下文噪声。 |

### 模型调用 (`infra/model/`)

| 规则 | 说明 |
|------|------|
| **只做 API 调用和流式处理** | 模型客户端封装 HTTP/MLX 调用，不包含提示词构建、工具解析或思考逻辑。 |
| **不管理生命周期** | 模型实例的创建/销毁由 `ModelRunnerManager` 统一管理，模型客户端不自行启动或停止。 |

### 核心原则（跨模块）

1. **单向依赖**：`modules/` 可以依赖 `infra/` 和 `utils/`，但 `infra/` 和 `utils/` 绝不反向依赖 `modules/`。
2. **不跨层调用**：L3 模块之间通过 `MessageBus` 或 GCM 通信，不直接 import 对方的类。
3. **延迟创建**：所有模块间依赖使用延迟初始化（`from X import Y` 放在方法内），避免启动时循环导入。
4. **失败不影响调用方**：任何非核心模块的异常必须被捕获并降级，不得让外层链路崩溃。
5. **不读另一个模块的私有状态**：一个模块不得访问另一个模块的 `_` 前缀属性或方法。需要跨模块数据时，通过 GCM 或接口方法。

---

## 核心模块详解

### 1. 统一调度中枢 (modules/unified_scheduler) ★

系统主协调器，负责整个请求处理管道。

**15 阶段处理流程**：
```
用户输入
→ 1. 安全输入验证
→ 2. 注意力分析 (激活哪些模块)
→ 3. 资源状态检查 + 可能降级
→ 4. 分层记忆候选检索 (近期/中期/长期)
→ 5. 用户情感分析
→ 6. PreGenExpertPipeline (3 专家并行: 情感/价值观/安全)
→ 7. 感知-思考集成 (环境变化注入)
→ 8. 模块执行 (thinking/memory/perception/security/output)
→ 9. 探针扫描 (思考输出)
→ 10. 输出集成
→ 11. 专家系统审查
→ 12. 输出系统路由 + 安全校验
→ 13. 资源决策
→ 14. 对话存储到短期记忆
→ 15. 全流程事件发出 (供流式客户端消费)
```

**GCM 自动注入**：初始化时自动检测并注入 `GlobalContextPool`，所有阶段结果同步到 GCM。

**代码位置**：`modules/unified_scheduler/coordinator.py` (1610行)

---

### 2. 全局上下文管理器 (modules/context) ★

系统的"中央大脑记忆中枢"——所有模型共享同一全局状态，消除数据冗余。

```
GlobalContextPool (单例)
├── 文件缓存 (path → FileInfo + hash + embedding + summary)
├── 全局状态 (tasks/progress/sessions)
├── 事件日志 (线程安全 RLock, TTL 自动裁剪)
├── 记忆索引引用
└── 会话上下文

Synchronizer (单例)
├── 文件监听 (watchdog 后台线程, 二进制检测, 10MB 限制)
├── 模型输出同步 → 自动写入事件日志
├── 工具调用同步 → 事件日志
├── 探针信号同步 → 事件日志
└── 冲突解决 (latest/incoming/merge 三策略)

CompressionEngine (单例) — 5 级上下文压缩
├── NONE:       原样返回
├── LIGHT:      去空行/注释
├── MODERATE:   摘要旧事件
├── HEAVY:      结构化压缩 (仅保留关键句)
└── AGGRESSIVE: 仅保留关键词和结论

ContextViewGenerator
├── Manager 视图 (全貌, 8000 tokens)
├── Expert 视图  (相关文件/事件, 4000 tokens)
├── Probe 视图   (最小上下文, 2000 tokens)
└── Auditor 视图 (完整审计, 16000 tokens)

Auditor (单例)
├── 冗余检测 (Jaccard 相似度, 三级阈值)
├── 内存检查 (500MB 预警, 8000 事件/5000 文件上限)
├── 一致性检查 (事件-文件引用, 时间戳单调性)
└── 统计视图 (事件类型分布, 源角色分布)
```

**管理 API 端点**：
| 端点 | 方法 | 功能 |
|------|------|------|
| `/context` | GET | GCM 完整状态 + 健康检查 |
| `/context/stats` | GET | 池统计信息 |
| `/context/warnings` | GET | 审计警告列表 |
| `/context/clear-warnings` | POST | 清除警告 |

**代码位置**：`modules/context/` (7 个文件, ~1200 行)

---

### 3. 工具系统 (infra/tool_manager) ★

原生 API Tool Calls 基础设施，负责工具注册、解析、执行和运行时安全。

**注册方式** — 装饰器驱动，零配置：
```python
@ToolRegistry.register("my_tool", description="...", params={"arg1": "..."}, risk_level="LOW", category="query", tags=["my_domain"])
def my_tool(arg1: str) -> dict:
    return {"result": ...}
```

**加载方式** — `tools/__init__.py` 使用 `pkgutil.iter_modules` 自动扫描目录下所有模块，新增工具只需在 `tools/` 下创建 `.py` 文件，无需手动 import。

**过滤方式** — 三级管线：`tag:` 前缀展开 → 风险等级过滤（专家不能用 HIGH/CRITICAL）→ 角色白名单。

**工具执行循环** — `_generate_with_tools()` 内部循环（最多 5 轮）：模型调用工具 → 执行 → `role="tool"` + `tool_call_id` 注回消息 → 模型继续推理，直到无工具调用或达到上限。

**工具生命周期**：
```
注册 → 发现(上下文选择) → Schema 生成 → LLM 推理
→ 解析 → 参数校验(TypeValidator) → 权限检查
→ 执行(超时+重试) → 结果提取 → 缓存更新
```

**管理 API 端点**：
| 端点 | 方法 | 功能 |
|------|------|------|
| `/tools` | GET | 工具列表 + 使用统计 |
| `/tools/{name}` | GET | 单个工具详情 + 审计记录 |
| `/tools/audit` | GET | 全局审计日志 |
| `/tools/reload` | POST | 热重载工具注册表 |
| `/tools/runtime-security` | GET | 运行时安全配置 |

**代码位置**：`infra/tool_manager/` (8 个文件 + 20 个工具模块, 78 个注册工具)

---

### 4. 思维系统 (modules/thinking)

#### 双层 ReAct 架构

系统同时存在两层 ReAct，职责不同，不能混淆：

```
系统级 ReAct（多模型协作）
用户输入
→ 总指挥 large
→ 主管 supervisor
→ 专家 expert / 专家
→ 主管整合
→ 总指挥继续判断
→ final_draft

单模型内部 ReAct（一次模型思考增强）
某个模型的一次思考
→ 第1轮观察/分析
→ 内部控制工具 continue_thinking / delegate_task
→ 等待专家/工具/上下文反馈
→ 第N轮继续思考
→ 安全最终整合 final_output
```

- **系统级 ReAct** 由 `MultiModelOrchestrator`、`ModelRunnerManager`、`CognitiveBlackboard`、`MessageBus` 协作完成。
- **单模型内部 ReAct** 由 `ContinuousThinker` 完成。它对外等价于某个模型的一次思考，但内部允许多轮观察、委托、等待和最终整合，用于降低单次模型输出跑偏率。
- `ModelRunner` 是两层之间的桥：持有模型实例，执行原生 tool calling，并把 `ContinuousThinker` 的最终结果写入 `final_draft` 或 `expert_findings`。

**委托返回链路**：
```
大模型 delegate_task(return_to=self.model_id)
→ 主管 delegate_task(return_to=self.model_id)
→ 专家完成 → _notify_return_target → 发给主管
→ 主管整合 → _notify_return_target → 发给大模型
→ 大模型输出 final_draft
```
- `DelegationRequest.return_to_model_id` 始终传 `self.model_id`（委托方自身），确保被委托方回报给正确的上级。
- `_notify_return_target()` 检查 `pending_delegations`：有待处理委托时跳过，全部完成后才发送唤醒。
- 唤醒消息包含 `source_tier`/`source_role`，`_build_awakening_prompt()` 动态显示来源（"你已被专家唤醒"而非硬编码"主管"）。

#### 连续思考器 (ContinuousThinker)

`ContinuousThinker` 是**单模型内部 ReAct 引擎**：它不负责系统级多模型编排，但负责让一个模型的一次思考变成可多轮修正、可等待反馈、可最终整合的增强思考过程。

---

### 5. 技能系统 (modules/thinking/skills)

YAML 驱动的技能框架，让大模型从通用助手变成领域专家。

**文件**：
| 文件 | 用途 |
|------|------|
| `skills/skill.py` | Skill/SkillRule/WorkflowStep 数据类，`to_context_block()` 生成提示词注入块 |
| `skills/manager.py` | SkillManager 单例，YAML 加载、关键词匹配、缓存、热重载 |
| `skills/*.yaml` | 技能定义（code_review, architecture_design, problem_diagnosis） |

**技能定义示例**：
```yaml
id: code_review
name: 代码审查专家
role: 资深代码审查员
personality: 严谨、细致、不留情面...
speaking_style: 直接、有理有据...
expertise: [代码审查, 安全审计, 性能分析]
weaknesses: [需求讨论, 产品设计]
rules:
  - text: 必须逐行审查，不能只看摘要
    severity: must
  - text: 安全漏洞必须标记为 BLOCKER
    severity: must
workflow:
  - step: 1
    action: 通读代码，理解整体架构
  - step: 2
    action: 逐行检查逻辑错误和安全隐患
  - step: 3
    action: 生成审查报告
```

**匹配机制**：关键词命中（权重3）+ 角色名匹配（权重2）+ 技能名匹配（权重2）+ 描述词匹配（权重1），总分 ≥ 3 激活。

**注入位置**：技能激活后，system prompt 使用技能身份替换默认身份，user prompt 追加技能上下文块（角色+规章+流程+示例），执行需求切换为"按技能流程执行"。

---

### 6. 时间感知与用户身份

大模型的 system prompt 自动注入时间上下文：

```
【当前时间】2026-06-04 21:20
【对话对象】小明
【上次对话】小明5分钟前说过话
```

**实现**：
- `Session.last_user_message_time` — 仅在用户发消息时更新（`orchestrator.process()` 入口）
- 感知系统等其他触发**不会更新**这个时间
- 首次对话显示"这是与用户的首次对话"
- 只对 `large` 层注入，专家/主管不加
- 配置：`.env` 中设置 `USER_NAME=小明`，默认值"用户"

**代码位置**：`modules/thinking/core/model_runner.py` → `_build_time_context()`

---

### 7. 专家能力列表（动态注入主管提示词）

主管提示词自动追加专家能力表，避免盲目委派：

```
【可委托的专家】
| 角色(role) | 名称 | 能力描述 |
|------------|------|----------|
| code_reviewer | 审查专家 | 代码审查、Bug发现、安全漏洞扫描 |
| code_writer | 实现专家 | 代码编写、算法实现、重构 |
| data_analyzer | 分析专家 | 联网搜索(web_search)、信息检索、数据分析 |
| ... | ... | ... |

选择专家时，根据任务类型匹配最合适的 role。
```

大模型提示词自动追加主管能力表：

```
【可委托的主管】
| 角色(role) | 名称 | 能力描述 |
|------------|------|----------|
| code_supervisor | 代码主管 | 代码相关任务：编写、审查、测试 |
| query_supervisor | 查询主管 | 信息查询任务：联网搜索、资料整理 |
| creative_supervisor | 创意主管 | 创意写作任务：文学创作、文案撰写 |
```

**实现**：从 `DEFAULT_IDENTITIES` 动态生成，每个专家模板的 `capability` 字段定义能力描述。新增专家只需加 `capability` 字段即可自动出现在列表中。

**代码位置**：`modules/thinking/identity.py` → `build_expert_capability_list()` / `build_supervisor_capability_list()`

---

### 8. 联网搜索 (web_search)

多引擎自动 fallback 的联网搜索工具，搜索后自动用 crawl4ai 无头浏览器抓取页面全文。

**搜索引擎链路**（按优先级）：
| 优先级 | 引擎 | 国内可用 | 全文抓取 |
|--------|------|---------|---------|
| 1 | DuckDuckGo | ❌ 需VPN | ✅ |
| 2 | 搜狗 | ✅ | ✅ crawl4ai |
| 3 | 必应中国 | ✅ | ✅ crawl4ai |
| 4 | 百度 | ✅ | ✅ crawl4ai |

**返回格式**：
```json
{
  "query": "大模型推理优化",
  "results_count": 3,
  "source": "sogou",
  "results": [
    {
      "title": "百川智能:深度学习大模型推理性能优化策略",
      "url": "https://...",
      "snippet": "...",
      "content": "# 完整页面正文 (markdown, 最多3000字符) ..."
    }
  ]
}
```

**参数**：
- `query`: 搜索关键词
- `limit`: 返回结果数量（默认5，最大20）
- `fetch_content`: 是否抓取页面正文（默认true），设为false只返回标题和摘要

**代码位置**：`infra/tool_manager/tools/web_search.py`

---

### 9. 模型 API 格式支持

所有模型客户端（大/中/小/轻量）支持三种 API 格式，URL 自动检测：

| 格式 | 自动检测条件 | 适用模型 |
|------|-------------|---------|
| **DashScope** | URL 含 `dashscope` | 通义千问 Qwen 系列 |
| **OpenAI** | URL 含 `openai`/`v1/chat` | DeepSeek、OpenAI、vLLM 等 |
| **Anthropic** | URL 含 `anthropic`/`claude` | Claude 系列 |

**配置方式**（`.env`）：
```bash
# OpenAI 兼容格式（DeepSeek）
LARGE_MODEL_API_URL=https://api.deepseek.com/v1/chat/completions
LARGE_MODEL_API_FORMAT=openai

# Anthropic Claude
LARGE_MODEL_API_URL=https://api.anthropic.com/v1/messages
LARGE_MODEL_NAME=claude-sonnet-4-20250514
LARGE_MODEL_API_FORMAT=anthropic

# DashScope 通义千问
LARGE_MODEL_API_URL=https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation
LARGE_MODEL_API_FORMAT=dashscope
```

**Anthropic 协议适配**：
- 请求：`x-api-key` 认证、`system` 顶层参数、`input_schema` 工具格式
- 消息：tool 结果用 `tool_result` content block、assistant 工具调用用 `tool_use` block
- 响应：解析 `content[].text` + `content[].tool_use`、`stop_reason` 映射

**代码位置**：`infra/model/base_model.py`（共享方法）+ 各客户端

