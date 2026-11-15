# 架构设计文档

**Language / 语言**: [简体中文](./ARCHITECTURE.md) | [English](./ARCHITECTURE.en.md)

> Cortex Agent 系统架构详解 — 四层分层、事件驱动黑板、多模型编排、协议解耦

> **更新说明**：本文件已按当前代码同步（9 业务模块、`config/providers` 模型格式层、
> 双对话引擎、多端交互、安全 JSONL 审计）。不存在的模块（attention）与类已删除或修正。

---

## 0. 交互端与双引擎

Cortex 提供四种交互方式与两条对话引擎：

### 0.1 交互端

| 端 | 入口 | 说明 |
|----|------|------|
| Web UI | `frontend/`（Vue 3 + Vite） | 15 个页面：Chat/编排/设置/仪表盘等，经 `/api` 前缀代理到后端 |
| Qt 桌面客户端 | `frontend/main.py`（PyQt6 + QtWebEngine） | 后台起 server.py，Qt 窗口内嵌 Web UI；自动拉起桌宠 |
| 桌宠 | `frontend/pet_launch.py` + `pet_widget.py` + `modules/desktop_pet/` | Live2D 透明置顶窗，绑定固定主会话 pet_main，TTS 语音回复 |
| 终端 TUI | `cli_tui/`（Textual） | `cortex` 启动 |

### 0.2 双对话引擎

| 模式 | 引擎 | 流程 |
|------|------|------|
| Agent（默认） | `modules/thinking/core/` | 多角色编排：Large 决策 → Supervisor 拆解 → Expert 并行 → 黑板整合 |
| 纯对话（chatonly） | `modules/thinking/chat_light/` | 单人格轻量：`_recall_memories → ContextSlicer.slice → composer.build_system → runner.run`，无连续思考循环/委托 |

入口 `chat_gateway`（`/stream/ws/{sid}`）按 `_resolve_mode()` 分流：agent 走 `api_stream`、chatonly 走 `_chatonly_ws`。

### 0.3 测试

`tests/` 387 个测试文件（unit/integration/external），全量 6700+ 项。隔离原则：
临时 SQLite + monkeypatch 单例（不碰生产库）、重库加载放宽 timeout、后台线程类提供 `stop()`、
禁 `except: pass` 吞错、测试假对象与真实模型字段一致。

**验证分层（新增测试按此选层，详见 §110）**：

| 层 | 位置 | 适用 |
|----|------|------|
| 产物级单测 | `tests/unit/` | 外部服务的替身要替在**边界**：假后端也要产出真实产物（音频看时长+波形、图片看尺寸+像素、文件看 schema），不能只断言「文件存在/函数返回非空」 |
| 跨端契约 | `tests/unit/test_frontend_api_contract.py` | 前端源码里的请求路径必须能在 `api.main.app.routes` 找到（方法+路径，含 `/api` 代理前缀），防止 `fetch` mock 把 404 变成 `{}`；抽不出路径的调用必须**失败**，不能静默跳过 |
| 真机集成 | `tests/integration/` + `@pytest.mark.external` | 依赖真麦克风/系统语音/显示/本机模型的能力，用 `external` 标记（默认不跑，手动 `-m external`），需要真 torch 时放子进程（conftest 会话级 `torch=None`）；一旦选中，缺后端/缺模型必须**失败**，不用 skip |
| UI 状态不变量 | `frontend/src/**/*.spec.js` | 断言「当前编辑对象/脏标记/删除目标」等状态，而不只是「点了按钮调了哪个 API」 |

---

## 1. 总体架构

### 1.1 四层分层

```
┌─────────────────────────────────────────────────────────┐
│  L1 入口层 (cortex/)                                     │
│  CLI 入口 · 子进程编排 · TUI 启动 · 版本管理              │
└──────────────────────┬──────────────────────────────────┘
                       │ uvicorn subprocess / os.execvp
┌──────────────────────▼──────────────────────────────────┐
│  L2 API 层 (api/)                                        │
│  FastAPI 应用 · WebSocket/SSE 流式 · 中间件链             │
│  CORS · API Key 认证 · 限流 · 请求ID · 日志              │
└──────────────────────┬──────────────────────────────────┘
                       │ 路由分发
┌──────────────────────▼──────────────────────────────────┐
│  L3 业务层 (modules/)                                    │
│  9 个业务模块：思考/记忆/安全/感知/输出/管理/数据库/桌宠/cortex（无 attention 模块）   │
└──────────────────────┬──────────────────────────────────┘
                       │ Protocol 接口 + 直接导入
┌──────────────────────▼──────────────────────────────────┐
│  L4 基础设施层 (infra/)                                   │
│  模型客户端 · 工具注册/管理 · Prompt 引擎                 │
│  NLP · 数据处理 · 安全策略 · MCP · 数据库                │
└─────────────────────────────────────────────────────────┘
```

### 1.2 依赖规则

| 规则 | 说明 |
|------|------|
| L3 → L4 | ✅ 允许（业务模块使用基础设施） |
| L4 → L3 | ❌ 禁止（基础设施不得反向依赖业务） |
| L3 ↔ L3 | 仅通过 MessageBus、CognitiveBlackboard 或 Protocol 接口 |
| L4 ↔ L4 | 允许（同层模块可互相引用） |

### 1.3 共享工具层 (utils/)

```
utils/
├── logger.py         # 日志：console + 按日轮转文件（14天保留）
├── async_utils.py    # 异步工具：async_wrap、并发控制、超时、任务组
├── json_utils.py     # JSON：DateTimeEncoder、序列化/反序列化
└── time_utils.py     # 时间：now、格式化、时间范围、日期边界
```

---

## 2. 核心数据流

### 2.1 请求处理主流程

```
用户输入 (WebSocket/SSE)
  │
  ▼
api_stream.py :: StreamThinkingSystem.think()
  │
  ▼
multi_model_orchestrator.py :: MultiModelOrchestrator.process()
  │
  │  1. SecurityPort.validate_input()          → SecurityAPI 输入审查
  │  2. ContextPort.load_context()             → ContextManager + 记忆检索
  │  3. GuidancePort.run()                     → PreGenExpertPipeline
  │     └─ ValuesExpert + SecurityExpert + EmotionExpert (并行)
  │  3.5 SkillManager.match_skill()            → YAML 技能匹配
   │  4. _execute_multi_model_thinking()        ← 核心编排
   │     │
   │     ├─ TurnContext + CognitiveBlackboard 初始化
   │     ├─ ModelRunnerManager.start_listening()
   │     ├─ 注入上下文到 Blackboard（委托指导、良知引导、记忆）
   │     ├─ MessageBus: probe_start("large")    → ModelRunner 激活
   │     │
   │     ├─ ModelRunner._think_loop()            ← 会话循环（跨委托）
   │     │   │
   │     │   ├─ ContinuousThinker.continuous_think()  ← 思考轮次
   │     │   │   │  每轮重建 prompt（黑板状态 + 记忆 + 感知）
   │     │   │   │
   │     │   │   └─ _generate_with_tools()        ← 工具循环（ReAct）
   │     │   │       │  turn 0..N: chat → tool_calls → execute → 结果注入
   │     │   │       └─ 无工具调用 → 输出文本 → return
   │     │   │
   │     │   ├─ 检测到委托 → 退出思考轮次 → _wait_for_wakeup
   │     │   ├─ 收到 thinking_result → 重建 prompt → 新一轮思考
   │     │   └─ 无委托且 continue=false → 退出会话循环
   │     │
   │     ├─ 等待 thinking_complete 事件 (MessageBus, 300s 超时)
   │     └─ 读取 CognitiveBlackboard.final_response
  │
  │  5. OutputReviewPort.review()              → 输出校验 + 专家审查 + 情感样式
  │  6. ContextPort.save_memory()              → 对话记忆保存
  │  7. Memory promotion (fire-and-forget)     → 后台记忆提升
  │
  ▼
响应通过 WebSocket/SSE 流式返回
```

### 2.2 委托流（Delegation Flow）

```
Large 模型调用 delegate_task(role="code_writer", task="实现支付模块")
  │
  ▼
ProbeDelegationAdapter.delegate()
  ├─ resolve_role("code_writer") → ("expert", "expert_implementer")
  ├─ ProbePermissionManager.validate(caller_tier, target_tier)
  └─ probe_start(probe_id, task, identity)
       │
       ▼
  MessageBus.SYSTEM(probe_started)
       │
       ▼
  ModelRunnerManager._listen_loop() 接收
       │
       ▼
  start_runner() → ModelFactory.create_instance() → ModelRunner
       │
       ▼
  ModelRunner._think_loop() → ContinuousThinker
       │
       ▼
  专家完成 → _write_final_result()
       ├─ Blackboard: add_observation / write_expert_finding
       └─ MessageBus: thinking_result → return_to_model_id
            │
            ▼
  Large ModelRunner._wait_for_wakeup_message() 被唤醒
       │
       ▼
  Large 模型继续推理（读取 Blackboard 中的专家结果）
```

---

## 3. 关键设计模式

### 3.1 认知黑板（CognitiveBlackboard）

**单例源**：每个 Turn 一个 Blackboard 实例，由 `SessionLifecycle` 创建。

**数据结构**：
```python
class CognitiveBlackboard:
    goal: str                                    # 当前用户目标
    delegations: Dict[str, Delegation]           # 委托任务（角色、状态、元数据）
    observations: List[Observation]              # 观察（tier、content、metadata）
    expert_findings: Dict[str, ExpertFinding]    # 专家发现
    dialog_entries: Deque[DialogEntry]           # 对话条目（maxlen=500）
    final_response: str                          # 最终输出
```

**线程安全**：`threading.RLock` 保护所有写操作。

**分层视图**（`ContextSlicer`）：
- `slice_for_large()`：目标 + 计划 + 风险 + 委托 + 专家发现 + 记忆
- `slice_for_supervisor()`：任务目标 + 可用工具
- `slice_for_expert()`：当前步骤 + 工具状态 + 最近 5 步历史

**广播机制**：每次写入通过 MessageBus 广播变更事件。

### 3.2 事件驱动通信（MessageBus）

**单例**：`ModelMessageBus` 全局单例。

**通信模式**：
| 模式 | 方法 | 用途 |
|------|------|------|
| 点对点 | `send(Message)` | 模型间直接通信 |
| 广播 | `broadcast(Message)` | 全局事件通知 |
| RPC | `request()` + Future | 请求-响应式调用 |
| 订阅 | `subscribe(channel, callback)` | 事件驱动回调 |

**消息类型**：
- `SYSTEM`：probe_started、probe_stopped、thinking_complete、thinking_result
- `EXPERT`：专家间通信
- `USER`：用户输入

**TTL 清理**：默认 300 秒自动过期。

### 3.3 端口/适配器模式（Ports & Adapters）

定义在 `modules/thinking/ports.py` 和 `adapters.py`：

| 端口 | 适配器 | 职责 |
|------|--------|------|
| `SecurityPort` | `SecurityApiAdapter` | 输入验证 |
| `GuidancePort` | `PreGenExpertGuidanceAdapter` | 良知系统（内心独白/过往经验）注入 |
| `OutputReviewPort` | `OutputSystemReviewAdapter` | 输出校验 + 专家审查 + 情感样式 |
| `ActivityNotifierPort` | `DifferenceDetectorActivityNotifier` | 通知差异检测器 |

每个适配器内部使用懒导入 + try/except 降级，确保单个模块故障不影响整体。

### 3.4 探针驱动激活（Probe-Driven Activation）

模型不直接调用模型。激活链路：

```
delegate_task (工具) → ProbePermissionManager (权限) → probe_start (注册)
  → MessageBus.SYSTEM(probe_started) → ModelRunnerManager (创建 runner)
  → ModelRunner → ContinuousThinker (执行) → Blackboard (写入结果)
  → MessageBus(thinking_result) → 委托方唤醒
```

**权限层级**：`Large > Supervisor > Expert`，通过 `ProbePermissionManager` 三级控制。

### 3.5 单例模式现状

项目中存在多种单例实现方式：

| 模式 | 使用位置 | 说明 |
|------|---------|------|
| 模块级全局变量 | `tool_manager`, `prompt_manager` | 最简单，import 时初始化 |
| `__new__` + `_initialized` | `PromptManager`, `PromptRegistry` | 类内控制 |
| 类变量 + `threading.Lock` | `LiteModelClient`, `MCPToolService` | 线程安全 |
| `@classmethod` 类方法 | `ToolRegistry` | 无实例，纯类级状态 |
| 双重检查锁定 | `ValueSystem` | 高并发安全 |

**已知限制**：`asyncio.Lock` 绑定到创建时的事件循环。如果单例跨循环使用（如 uvicorn 热重载），需要重建。

---

## 4. 线程与协程安全模型

### 4.1 线程模型

```
主线程 (asyncio event loop)
  ├─ FastAPI 请求处理 (async)
  ├─ WebSocket 连接管理 (async)
  └─ MessageBus 通信 (async)

Daemon 线程池
  ├─ ModelRunner (每模型一个线程)
  │   └─ ContinuousThinker._think_loop() (sync → 内部调用 async via asyncio.run())
  ├─ ModelRunnerManager._listen_loop() (MessageBus 消费者)
  ├─ Synchronizer 文件监控 (轮询)
  ├─ ProbeCache 清理 (30 分钟 TTL)
  └─ ProactiveOutreach 空闲检测
```

### 4.2 同步原语

| 原语 | 位置 | 保护对象 |
|------|------|---------|
| `threading.RLock` | CognitiveBlackboard | 所有状态读写 |
| `threading.RLock` | SessionLifecycle | 状态转换 |
| `threading.RLock` | GlobalContextPool | 全局上下文 |
| `threading.Lock` | ToolRegistry._tools | 工具注册表 |
| `threading.Lock` | ToolManager._tool_events | 事件记录 |
| `asyncio.Lock` | ModelMessageBus | 消息队列 |
| `threading.Event` | ModelRunner | 唤醒信号 |

### 4.3 已知限制

- `asyncio.Lock` 单例绑定到特定事件循环，跨循环失效
- 部分工具使用阻塞 I/O（`subprocess.run`、`requests.get`、`time.sleep`），会阻塞事件循环
- `model_factory.get_model_factory()` 非线程安全（无锁保护）

---

## 5. 配置系统

### 5.1 配置层级

```
环境变量 (.env)
  ↓ 覆盖
Pydantic Settings (config/settings.py)
  ↓ 注入
各模块通过 settings.xxx 访问
```

### 5.2 核心配置类

| 文件 | 类 | 职责 |
|------|-----|------|
| `config/settings.py` | `Settings` | 全局配置（模型API、功能开关、TTL、阈值） |
| `config/providers/` | ProviderRegistry | 模型 API 格式统一适配（openai/anthropic/dashscope/gemini/azure/bedrock/cohere/ollama） |
| `config/providers/catalog.py` | ProviderSpec | 35+ 供应商目录：名称 → 默认端点/格式/模型/密钥（opencode 风格最简配置） |
| `config/prompts/` | PromptComposer | 提示词组装（roles.yaml / base.yaml） |
| `config/values_store.py` | ValueSystem | 价值观规则存储（add/remove/cleanup） |

### 5.3 模型 API 适配层（Provider Adapter Layer）

`config/providers/` 是独立的「供应商 → 协议」适配层，位于模型客户端（`infra/model/*`）与
外部 LLM API 之间，职责边界清晰：

```
infra/model/*_model_client.py   ← 只管 HTTP 时序/重试/序列化 ChatMessage
        │  委托
        ▼
config/providers/registry.py    ← 解析：供应商名 > 显式格式 > URL 推断 > 默认 OpenAI
        │  实例化
        ▼
config/providers/{openai,anthropic,dashscope,gemini,azure,bedrock,cohere,ollama}.py
        │  每个适配器负责：
        │    构建请求头（认证方式）
        │    组装请求体（协议格式转换）
        │    解析响应（还原为标准 {content, tool_calls, finish_reason, usage}）
        │    解析 SSE 流
config/providers/catalog.py      ← 35+ 供应商声明式目录（ProviderSpec）
```

**最简配置**：用户只需填写一个供应商名，其余自动补齐（`config/settings.py` 的
`resolve_model_tier()`）：

```dotenv
LARGE_MODEL_PROVIDER=deepseek    # 自动用 api.deepseek.com/v1 + deepseek-chat + DEEPSEEK_API_KEY
# 或
LARGE_MODEL_PROVIDER=gemini       # 自动用 generativelanguage.../v1beta + gemini-2.0-flash
# 或
LARGE_MODEL_PROVIDER=anthropic     # 自动用 api.anthropic.com/v1 + claude-3-5-sonnet-...
```

解析优先级：`*_MODEL_PROVIDER`（查目录）> `*_MODEL_API_FORMAT`（显式格式）>
URL 推断（`base_url` 含 `dashscope`/`anthropic`/`generativelanguage` 等）>
默认 OpenAI 兼容。显式设置的 `*_MODEL_API_URL` / `*_MODEL_NAME` 始终覆盖目录默认值。

协议矩阵：

| 协议 | 适配器 | 认证方式 | 适用供应商 |
|------|--------|----------|-----------|
| openai | OpenAIProvider | `Authorization: Bearer` | OpenAI/DeepSeek/Groq/OpenRouter/Mistral/Kimi/GLM/MiniMax/SiliconFlow/… 30+ |
| anthropic | AnthropicProvider | `x-api-key` | Anthropic Claude |
| gemini | GeminiProvider | `x-goog-api-key` | Google Gemini/Vertex |
| azure | AzureProvider | `api-key` + api-version | Azure OpenAI |
| bedrock | BedrockProvider | AWS SigV4 | AWS Bedrock |
| cohere | CohereProvider | `Authorization: Bearer` | Cohere |
| ollama | OllamaProvider | 无 | 本地 Ollama |
| dashscope | DashScopeProvider | `Authorization: Bearer` | 阿里百炼/ModelScope |

### 5.4 运行时配置修改

`PUT /config/{key}` 端点支持运行时修改，但有以下限制：
- 仅白名单内的 key 可修改（`_MODIFIABLE_CONFIG_KEYS`）
- 通过 `setattr(settings, key, value)` 实现（⚠️ 跳过 Pydantic 校验）
- 部分配置（如 `DIFFERENCE_DETECTOR_ENABLED`）修改后不会动态生效

---

## 6. 身份与权限系统

### 6.1 身份模板

定义在 `modules/thinking/identity.py`：

```python
ModelIdentity:
  model_id: str          # 唯一标识
  name: str              # 显示名
  tier: str              # large / supervisor / expert
  role: str              # 角色描述
  personality: str       # 人格特征
  speaking_style: str    # 说话风格
  tool_whitelist: list   # 工具白名单
  permissions: ModelPermissions  # 权限配置
```

12 个内置身份模板：large、code_supervisor、query_supervisor、creative_supervisor、code_reviewer、code_implementer、test_writer、analyzer、customer_expert、creative_writer、emotion、memory_manager。

### 6.2 权限模型（ModelPermissions）

```python
ModelPermissions:
  can_start_probes: bool         # 是否可启动探针
  can_stop_probes: bool          # 是否可停止探针
  controllable_tiers: list       # 可控制的层级
  can_write_memory: bool         # 是否可写入记忆
  allowed_tool_categories: list  # 允许的工具类别
  can_delegate: bool             # 是否可委托任务
  delegatable_tiers: list        # 可委托的目标层级
  max_instances: int             # 最大实例数
```

### 6.3 工具白名单

| 层级 | 白名单 | 说明 |
|------|--------|------|
| Large | `"*"` | 所有工具 |
| Supervisor | 管理工具 | delegate_task、continue_thinking 等 |
| Expert | 角色限定 | 由身份模板定义，HIGH/CRITICAL 风险工具自动屏蔽 |

控制工具（continue_thinking、delegate_task、create_supervisor、respond_to_user）不在 ToolRegistry 中注册，由 ModelRunner 在 `_generate_with_tools()` 中动态注入。

---

## 7. 上下文管理系统

### 7.1 GlobalContextPool（GCP）

全局上下文池，单例，`threading.RLock` 保护：

- **文件存储**：项目文件内容缓存
- **项目元数据**：项目名称、结构、依赖
- **全局状态**：当前任务、阶段、参与者
- **事件日志**：最大 10000 条，TTL 自动清理
- **会话上下文**：每会话独立的上下文视图

### 7.2 Token 估算与 LLM 总结（原压缩引擎）

> 历史说明：原 CompressionEngine 的 5 级规则压缩（NONE/LIGHT/MODERATE/HEAVY/AGGRESSIVE）
> 已移除。token 控制统一交由 LLM 总结机制，规则截断不再使用。

现状：

| 组件 | 职责 |
|------|------|
| `CompressionEngine.estimate_tokens` | 中英文混合 token 粗估（供占用统计与阈值判断） |
| `ModelRunner._maybe_summarize_context` | 工具循环中上下文超模型窗口 90% 时，调当前模型总结，messages 替换为【system + 摘要 + 原任务】 |
| `chat_light/context_slicer.ContextSlicer` | 近 15 条保留全文，更旧部分调 LLM 总结为一条摘要；失败才降级首尾截断 |
| `TurnContext._compact` | 超限仅告警不裁剪，交由上述总结机制与来源侧裁剪控制 |

### 7.3 审计器（Auditor）

- **冗余检测**：Jaccard 相似度
- **内存使用监控**
- **一致性检查**：时间戳排序、事件-文件交叉引用
- 结果缓存 60 秒

---

## 8. 记忆系统架构

### 8.1 两层回忆体系

```
浅层回忆（默认）              深度回忆（触发式）
   │                            │
   ▼                            ▼
EventRetrieval              CausalGraph (因果图)
(RAG 语义+关键词+重要性)       │ 定位锚点 + 邻域扩散
   │                            ▼
   ▼                        CausalTree (树下钻)
注入 prompt 作为            上溯/下钻/横向对比
【历史记忆】                    │
                               ▼
                           EventStore (事件池)
                           复合排序召回（因果+语义+重要性+时间）
```

### 8.2 浅层回忆（EventRetrieval）

评分公式: `0.60×语义 + 0.15×重要性 + 0.10×时效 + 0.08×效用 + 0.07×频率`

| 因子 | 来源 | 说明 |
|------|------|------|
| semantic | FAISS 向量内积 | 归一化 0~1 |
| importance | LLM 离散标注 | critical=1.0 → trivial=0.03 |
| recency | exp(-λ·days) | 按 type 不同衰减速率 |
| utility | log(access+3)/log(13) | 检索次数越多越高 |
| frequency | log(mention+3)/log(13) | 话题被提及越多越高 |

### 8.3 深度回忆（CausalGraph + CausalTree）

三步闭环:
1. **图定位**: `find_anchor_nodes()` 按关键词定位锚点，按意图定向邻域扩散
2. **树下钻**: `trace_up()` 溯源 → `trace_down()` 预测 → `compare_lateral()` 归纳
3. **事件召回**: 复合排序 `0.3×语义 + 0.4×因果关联 + 0.2×重要性 + 0.1×时间`

触发条件（自动）:
- 查询含"为什么/原因/后果/规律/如果当时"等逻辑词
- 浅层召回置信度 < 0.3
- 当前为决策/分析类任务

| 模块 | 文件 | 职责 |
|------|------|------|
| CausalGraph | `modules/memory/causal_graph.py` | 因果节点与边持久化 (SQLite) |
| CausalTree | `modules/memory/causal_tree.py` | 上溯/下钻/横向对比遍历 |
| DepthRecallScheduler | `modules/memory/depth_recall.py` | 触发判断+三步闭环调度 |
| ResultFusion | `modules/memory/result_fusion.py` | 结果格式组装 |

### 8.4 记忆流水线

| 阶段 | 实现位置 | 说明 |
|------|---------|------|
| 写入 | `api_stream.py` `_post_task_extraction` → `EventReducer.reduce()` | 会话结束后 30s |
| 浅层读取 | `ContinuousThinker._build_prompt()` → `EventRetrieval.retrieve()` | 每轮思考 |
| 深度读取 | `ContinuousThinker._build_prompt()` → `DepthRecallScheduler.deep_recall()` | 触发式 |
| 工具调用 | `deep_recall` probe tool | 模型主动调用 |

---

## 9. 安全架构

### 9.1 三层防护

```
输入 → [输入审查] → [执行审查] → [输出审查] → 响应
         │              │              │
         ▼              ▼              ▼
    SecurityAPI    SecurityGate
    (意图识别)     (工具分级审批)   (双层:规则+LLM)
```

### 9.2 安全门控（Security Gate）

工具执行前的分级审批：

| 风险等级 | 处理方式 |
|---------|---------|
| LOW | 快速检查（路径、参数格式） |
| MEDIUM | 路径/命令验证 + 白名单 |
| HIGH | LLM 审批 |
| CRITICAL | 用户确认 或 LLM 审批 |

### 9.3 审计系统

- **格式**：JSONL
- **完整性**：JSONL 纯追加写入（timestamp/event_type/security_level/content_preview/result/metadata）
- **内容**：所有工具调用、权限决策、安全事件
- **可追溯**：所有工具调用、权限决策、安全事件全量落盘

---

## 10. MCP 与工具系统架构

插件系统已整体移除，其生态位由 **MCP（Model Context Protocol）** 和 **AI 自创工具** 替代。

### 10.1 三层工具模型

```
┌──────────────────────────────────────────────────┐
│                统一路由层                          │
│  MCPToolService (MCPToolExecutor.merge_tools)    │
│  CombinedToolProvider + CombinedToolExecutor     │
│  ToolManagerPermissionAdapter (安全权限检查)      │
└──────────────────────┬───────────────────────────┘
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
┌─────────┐    ┌──────────────┐    ┌────────────┐
│ 内置工具 │    │  MCP 远程工具 │    │ AI 自创工具 │
│ToolReg. │    │  stdio/SSE   │    │ create_tool │
│ 85 个  │    │  7+ 个服务器  │    │ +自制插件   │
└─────────┘    └──────────────┘    │ (§10.3.1)  │
                                   └────────────┘
```

### 10.2 MCP 集成架构

```
infra/mcp/
├── transport.py             # MCPStdioTransport / MCPSseTransport
│                            # 使用 mcp SDK 的 stdio_client / SSE 客户端
├── server_manager.py        # MCPServerManager — 服务器生命周期
│                            # add_server() 运行时添加、connect_all 自动连接
├── combined_provider.py     # CombinedToolProvider — 合并本地+远程工具列表
│                            # CombinedToolExecutor — 统一执行路由
├── perception_client.py     # MCPPerceptionClient — 通过 resources/subscribe
│                            # 获取外部感知数据
└── factory.py               # MCP 连接工厂：stdio / sse / 自动检测
```

**关键技术细节**：
- MCP 传输使用 `__aenter__`/`__aexit__` 手动管理生命周期（避免 `async with` 关闭连接）
- 工具名冲突时优先保留内置 ToolRegistry 工具，跳过同名 MCP 工具
- `ToolManagerPermissionAdapter` 包装 MCP 执行器，确保通过 `_check_tool_permission()` 进行安全审批

#### 10.2.1 MCP 生命周期管理（热插拔 + 自动重连）

```
MCPServerManager
├── start_all() / add_server()      # 连接 + 索引工具 + 启动重连监控
├── remove_server(name)             # 独立热卸载：先摘工具 → 关连接 → 清索引
├── replace_server(name, ...)       # 热替换（等价 dsh HMR：dispose 旧 + 建新）
├── _watch_connection(name)         # 后台监控任务：周期检测 is_connected
├── _reconnect_with_backoff()       # 断线指数退避：base_delay × 2^(attempt-1)
└── _refresh_tools(name)            # 重连成功后刷新工具索引（模型可见列表即时更新）
```

- **独立热插拔**：`remove_server` 逆序清理（先摘工具让模型立刻看不到 → 断开 → 清索引），不影响其他 server；`replace_server` 改配置热替换
- **自动重连**：`MCPServerConfig.reconnect` 配置开启，断线后指数退避自愈，重连成功后重新 list_tools
- **防任务遗留**：`remove_server`/`shutdown` 停止全部监控任务（asyncio task cancel + 停止事件），杜绝后台任务泄漏

#### 10.2.2 依赖注入端口（能力注册表）

工具层与业务模块的解耦通过 **Service Locator 式依赖注入**实现：

```
infra/tool_manager/service_registry.py
├── register_capability(name, provider)   # modules 侧注册（bootstrap 装配层）
├── provide_capability(name, provider)    # 接管端口，返回撤销它的操作（见 §12）
└── get_capability(name)                  # 工具层获取；缺失返回 None 优雅降级

bootstrap.py  register_business_capabilities()
   └── 13 个能力：blackboard_query / skill_manager / event_retrieval /
       file_history / touchpoint_detector / detector_router /
       value_formatter / tool_security_gate / turn_images / context_controller /
       event_store / causal_graph / knowledge_base
   └── 启动期校验：_report_capability_status 报告缺失/失败能力（fail-fast）
   └── 触发工具门面注册：probe_tools、plugin_authoring_tools（见 §10.3.1）——
       装饰器自注册是 import 副作用，只能由装配层触发，否则 infra 要反过来 import modules
   └── 末尾恢复 AI 自创工具（ai_tools.restore_ai_tools，原挂在 import 副作用上，见 §12.2）
```

- 依赖方向：`modules → infra`（逆向依赖 `infra→modules` 已归零）
- 工具通过端口取服务，缺失时返回显式错误信息（而非 ImportError）
- 测试经 `register_capability(name, fake)` 注入 mock

### 10.3 AI 自创工具

```
infra/tool_manager/tools/ai_tools.py
├── create_tool(name, code, description, params)   # 创建新工具
├── list_my_tools()                                 # 列出所有自创工具
├── delete_tool(tool_name)                          # 删除自创工具
├── edit_tool(name, code, description, params)      # 编辑已有工具
└── restore_ai_tools()                              # 启动时按持久化记录重建（装配层调用）
```

- 自创工具以 `.py` 文件持久化到 `data/user_tools/`
- 运行时通过 `ToolRegistry.register` 动态注册
- 创建/编辑时自动语法检查（`compile()` + `ast.parse()`）
- 支持断网离线创建和执行

#### 10.3.1 运行期自制插件（L5）：三个门面

`create_tool` 一次只能造一个工具，且造出来的就是最终形态。往上一层的自由度——**提交
一整份插件源码，同时提供工具和接管能力端口，失败当场撤销**——由 `modules/shared/`
的两个门面模块承载，机器语义在 §12.4：

```
modules/shared/plugin_authoring.py          # 机器语义（挂载/撤除/观察/冲突/持久化）
modules/shared/plugin_authoring_tools.py    # 门面 1：模型侧工具
├── mount_plugin(name, source, trial, replace)  # 挂载 + 试运行通过才写盘
├── unmount_plugin(name)                        # 卸掉但不删记录（还能恢复）
├── list_plugins()                              # 盘上的 + 现在应答哪些名字
└── delete_plugin(name)                         # 卸掉 + 删记录

modules/shared/plugin_authoring_api.py      # 门面 2：HTTP 路由 /plugins/authored
├── POST   ""            # 提交源码（试运行语义与 mount_plugin 一致）
├── GET    ""            # 全量状态
├── GET    "/{name}"     # 单条：盘上行 + 运行期归属
└── DELETE "/{name}?forget="  # forget=false 只卸不删

api/main.py  lifespan                      # 门面 3：开机恢复
└── load_authored(store) / unload_authored()   # 见 §12.3
```

- 三个门面共用一套语义，差别只在错误怎么表达：工具门面回 `✅`/`❌` 字符串，HTTP 门面
  回 422（源码本身错）/400（冲突或插件炸了）+ 完整 traceback，lifespan 恢复失败只告警
  不阻断开机
- 工具函数是同步的，而执行落在 `asyncio.to_thread` 的 worker 线程（没有运行中的循环），
  所以门面里有 `_run()` 桥：无循环就 `asyncio.run`，有循环就换一条线程跑，绝不在别人的
  循环上调 `asyncio.run`
- 持久化文件：`data/authored_plugins.json`（env `CORTEX_AUTHORED_PLUGINS_FILE`）

#### 10.3.2 缝隙目录：接管点由代码算出来，不是人写的

§10.3 的端口是**声明出来的**可接管点，但声明不等于接得上：只要还有一处代码硬 import
原实现，插件接管端口就只换掉路径的一半，同一个能力跑出两套行为。本项目不做插件安全
（§12.5），也就没有权限清单可查——「今天到底能接管什么」只能从源码的 import/调用关系
算出来。`scripts/seam_catalogue.py` 干这件事，产出 `docs/SEAMS.md`（生成物，别手改）。

```
bootstrap._safe_register("name", factory)   ──▶ 业务端口：provider 是谁、包了哪些符号、
                                                 哪些文件真的 get_capability("name")
infra/ports.py 的 register("a.b", provider) ──▶ 门面端口（同一套判据，多一列「体系」）
模块级 def get_x() 且返回模块级名字          ──▶ 单例访问器
@classmethod get_instance / instance         ──▶ 单例访问器
任何名字/属性链的读取（含 mgr.run()）         ──▶ 引用边 ──▶ 归属到「访问器 ∪ 端口包装的符号」
```

分档按「今天能不能真的接管」，具体数字看 `docs/SEAMS.md`：

| 档 | 判据 | 含义 |
|---|---|---|
| ✅ 已可接管 | 有端口 + 端口有消费者 + 无硬 import 读者 | 插件 `provide_capability` / `provide_port` 一挂，整条路径换掉 |
| ❌ 还没有端口 | 有硬 import 读者、无端口 | 先把访问器降级成端口、把读者改成经端口取 |
| ⚠️ 只接了一半 | 有端口，但仍有文件硬 import 同一个符号 | 接管后两套行为并存，比没端口更难查 |

- 三遍扫描：模块 id → 包 `__init__.py` 的转口表 → 逐文件记引用。相对导入按文件所在
  目录展开、`from pkg import name` 要跟到定义处；这两条都栽在**少算读者**上，而少算
  读者会把 ⚠️ 说成 ✅
- 一条点号链只算最长那段：`Cls.get_instance()` 是访问器的读者，不是类的读者。逐段都记
  会让类的数字虚增，「谁绕开访问器直接碰类」这一档就没意义了
- 局部同名遮蔽不算访问器（`setup_logger` 返回现场新建的 logger，不是模块级单例），
  `global` 声明递出来的才算；端口包的是模块级实例时它也进表，只算访问器会漏一整类接管点
- 只读，不 import 被扫描的代码（stdlib + ast）；解析失败直接抛——漏算比报错更糟
- 重跑：`python scripts/seam_catalogue.py --out docs/SEAMS.md`；防漂：`--check`（漂了退出 1）

测试 `tests/unit/test_seam_catalogue.py`（45 项）钉住上面每条判据。2026-11-14 用
`/tmp/mutate_b6.py` 把生成器的 24 条主张逐条反向改一遍，**0 条存活**，跑完落盘与开始时
逐字节一致。

##### 两套端口注册表：目录以前只看见一半

`docs/SEAMS.md` 第一次算出 ❌=0 时，这句话其实是**半对**的：本仓有两套端口注册表，
生成器只认第一套。

| 体系 | 谁填 | 名字形状 | 插件接管入口 |
|---|---|---|---|
| 业务 | `bootstrap.register_business_capabilities()` 启动期 `_safe_register` | 蛇形 `tts_output_dir` | `ctx.provide_capability(name, provider)` |
| 门面 | 提供方模块在自己的 import 期调 `infra/ports.py::register` | 点号 `security.tool_gate` | `ctx.provide_port(name, provider)` |

门面端口装的是**横切契约**——安全门、权限控制器、身份表、感知系统、模型工厂。之前
它们既不在目录里、也没有插件接管入口，于是「候选缝全部可切换」实际只覆盖了业务那一半，
而且是以「表里没有」的形式漏的：最要紧的换实现点（换掉工具安全门）反而没人能换。

补齐的四件事：

- `infra/ports.py::provide(name, provider)` —— 和 `register` 的唯一区别是**返回撤销操作**，
  撤销按身份判断（后来者接管过时什么都不做）。装配期的登记是进程史，不需要撤销；插件卸载
  必须回到前任，否则卸一个插件就把横切契约留成空位。`ctx.provide_port` 是它的时效包装，
  并且拒绝蛇形名：写错注册表的那一侧不会报错，只会「接管了但什么都没换」。
- 生成器认识第二种接线：`ports.register(...)` / `resolve(...)`，端口名可以是字面量**或
  本文件常量**（`infra/security_gate.py` 的 9 个端口名全是 `PORT_GATE = "security.tool_gate"`
  这种），属性写法要按**模块**判而不是按解出的符号判（`_resolve` 对纯模块引用故意返回
  `None`）。两条判据少任何一条，整根安全门面都会从表里安静消失。同名方法不误伤：
  `ToolRegistry.register("x", f)` 不是端口。
- 只检测到取用、检测不到登记点的端口**照样列出来**（provider 一格写「没检测到登记点」），
  因为 `register_provider(name, provider)` 那种转口在静态层面确实看不见——目录不能把
  「我看不见」说成「不存在」。
- L5 自制插件也**看得见**门面端口：挂载前后各取一次 `ports.snapshot()`，按 `port:<名字>`
  记进这次挂载的差集，于是和工具/能力共用同一套冲突检查、`list_plugins` 与卸载日志。不做
  这一步，一个换掉了 `security.tool_gate` 的自制插件在冲突检查、`list_plugins` 和日志里
  **完全不存在**——第三套看不见的表，还是「只观察第一张表」的老毛病。归属按快照里的
  **提供方身份**算而不是调 `resolve()`：工厂端口每 resolve 一次就新建一个实现，为了列清单
  去实例化它本身就错了。

当前 **73 条端口（业务 58 / 门面 15，死端口 0）· 180 处候选缝 · ✅ 137 / ❌ 0 / ⚠️ 2**
（两处 ⚠️ 就是下面判定过的那两处）。

门面这一半刚被看见时多出过一处半接线：`modules.perception:get_perception_system`——包根
那个转发访问器以前只是 `from .setup import ...`，接管 `perception.system` 换不到它，而
`integration.py` import 的恰恰是它。现在它端口优先，内置实现另起
`_builtin_perception_system` 当 provider（否则端口→访问器→端口，第一次调用就递归），
`perception.manager` 也从「登记现成对象」改成「登记工厂」，换感知系统时兼容代理跟着换。

##### 补全与切换：❌ 清零之后余下什么

2026-11-15 把全部 ❌ 降级成端口优先访问器（`bootstrap` 里给每条端口配 `_x()` provider，
调用点改走访问器），目录现在是 **✅ 137 / ❌ 0 / ⚠️ 2**。先算出来的 121/0/2 只覆盖业务端口
那一半；上一小节把门面端口纳进来之后是现在的数字，❌ 和 ⚠️ 都没变——多出来的 16 处 ✅
全在门面端口名下（8 个安全门面访问器本来就端口优先，只是目录看不见；外加 8 个被这些端口
包住的 provider 对象）。留下来的两处 ⚠️ 不是没做完，
是**判定过、故意保留**的硬 import：

| 符号 | 仍在硬 import 的文件 | 为什么不该改成经端口取 |
|---|---|---|
| `EventStore` | `config/settings.py` | 切记忆库时要把**内置那份**单例清掉；改成端口取，重置目标就成了插件的对象，切库清不干净 |
| `EventStore` | `modules/desktop_pet/memory_bank.py` | 桌宠按人设各自 `EventStore(...)` new 一份，要的是类本身，不是「当前实现」 |
| `CausalGraph` | 同上两处 | 因果图跟记忆库走，同上 |

判据一句话：**要「那个类」的地方留硬 import，要「当前实现」的地方走端口**。这张表和
✅ 的数字都由 `tests/unit/test_seam_catalogue.py` 锁住——`MIGRATED`（51 行，访问器 →
端口 + 它包住的内置入口）不许漂回 ❌，`JUDGED_BYPASS` 之外的任何新增读者直接让测试变红，
所以「接管不到的调用点」必须是显式记录在案的决定，不能是漏掉的。

顺带一条写代码时会踩的：`_safe_register(name, loader)` 注册的是 `loader()` **返回值**，
所以单例类那种端口（`Cls.get_instance()` 端口优先 + `Cls._builtin_instance` 装内置实现）
provider 指向的是 `_builtin_instance`。于是测试里
`monkeypatch.setattr(Cls, "get_instance", ...)` **打不中**——调用点经端口直接拿到内置那份，
表现为「mock 没生效，真去加载了模型」。桩要打在端口上：`tests/conftest.py` 的
`take_over(name, provider)` / `drop_capability(name)`（后者用来验端口没接线时的降级分支）。

两套注册表就要两套夹具：点号端口用 `take_over_port(name, provider)` / `drop_port(name)`，
它们走 `ports.provide` 的撤销操作——和插件卸载同一条路。拿 `take_over` 去打桩 `security.tool_gate`
会**悄悄什么都不换**（那张表根本没有这个键），这正是「测试通过但接管其实不成立」的形状。

### 10.4 学习模式（Learn Mode）

学习模式是**瞬态状态**，非固定执行模式。流程：

```
模型调用 request_mode_change("learn")
  ↓
model_runner 注入学习提示词
  ↓
run_learn_pipeline() 自动执行
  ├─ 1. 打开应用（open_app）
  ├─ 2. 截图（capture_screen）
  ├─ 3. OmniParser 元素检测（本地或远程）
  ├─ 4. ActionPlanner AI 规划动作序列
  ├─ 5. 执行录制（语义动作：click_element/type_into 等）
  └─ 6. 生成插件包（PluginBuilder）+ Skill YAML 更新
  ↓
自动恢复原始执行模式（plan/edit/yolo/control）
```

- 并发保护：`asyncio.Lock` 限制同时只能执行一个学习管线
- 超时保护：整体 120 秒超时
- 精度降级检测：识别 OCR-only 低精度模式，提前报错
- 语义动作：`click_element("保存按钮")` 在运行时重新通过 OmniParser 检测坐标，不依赖固定像素位置

---

## 11. 部署架构

### 11.1 单进程模式（默认）

```
cortex 命令
  ├─ uvicorn 子进程 (api.main:app, 1 worker)
  └─ TUI 进程 (os.execvp 替换 cortex 进程)
```

### 11.2 Docker 模式

```
docker-compose
  └─ app 容器
      ├─ python scripts/start_all.py
      ├─ 4GB 内存限制，2 CPU
      ├─ 健康检查: GET /health (30s 间隔)
      └─ 数据卷: ./data → /app/data
```

### 11.3 多 Worker 模式

```bash
uvicorn api.main:app --workers 4
```

**注意**：多 Worker 时以下功能受影响：
- Rate limiter 为 per-process（非全局）
- 模块级单例每个 worker 独立
- MessageBus 消息不跨 worker 传播

---

## 12. 装配与插件契约（可撤销）

一句话：**每一步装配都要说得出「怎么撤」**。注册面、时效、生命周期三处各自守一条规则，
规则的来源是同一个——被 import 副作用装起来的东西，不重启进程卸不掉。

### 12.1 两层注册面，两种重名行为

```
infra/tool_manager/tool_registry.py          # 进程级注册表，本身不管时效
├── @ToolRegistry.register / register(replace=False)  # 「核心声明这块槽归我」→ 重名即 DuplicateToolError
├── ToolRegistry.provide(name, func, plugin_name=…)   # 「插件有意接管」→ 允许遮蔽，返回 undo
├── ToolRegistry.register_tool(…)                     # 历史覆盖写（management 侧依赖 source="dynamic"），不走重名拒绝
└── get_plugins() / list_by_source() / unregister_by_plugin()   # 由 provide 填入的 source/plugin_name 喂活

modules/shared/plugin_api.py                 # 契约层：只加时效，不碰依赖方向
├── Ctx                    具名服务槽（沿父链解析）+ LIFO 效应栈 + 子作用域
│   ├── effect(disposer)   插件说「我走的时候撤销这个」的唯一方式；同步/异步都收
│   ├── provide_tool / provide_capability    绑定压栈，随本作用域生灭
│   │                                        （capability = 业务端口，蛇形名）
│   ├── provide_port(name, provider)         接管横切端口（infra/ports.py，点号名），
│   │                                        拒绝蛇形名：写错表那一侧只会「什么都没换」
│   └── aclose()           逆序弹栈；某个 disposer 抛错也要把剩下的走完
├── Plugin.apply(ctx)      唯一允许做绑定的钩子（import 期与 __init__ 期都不行）
└── install / mounted / unmount              apply 抛错 → 半挂载当场回滚
```

- **接管 vs 抢占**：内置实现被插件遮蔽是这套机制的本意（`provide` 允许）；两个互不知情的
  插件抢同一个工具名是 bug（`provide` 拒绝，除非同一插件自己重写）。
- **undo 带身份守卫**（`_tools.get(name) is not info` 就直接返回），所以它只会放回自己
  遮蔽掉的那一层。代价是：**乱序调用两个 undo 会复活一个陈旧挂载**——正确性依赖效应栈
  的后进先出，这正是 `Ctx` 不提供 `deactivate` 钩子的原因。
- **端口有两张表**（业务 = `service_registry` 蛇形名，门面 = `infra/ports.py` 点号名），
  所以接管入口也有两个；只给一个入口的话「候选缝全部可切换」实际只说了一半，
  详见 §10.3.2。

### 12.2 装配期注册（bootstrap）

`infra/tool_manager/tools/__init__.py` 的 pkgutil 扫描是**全仓唯一**允许 import 期注册的地方，
且只登记内置工具模块。AI 自创工具的恢复（`ai_tools.restore_ai_tools()`，读 `data/ai_tools.json`
并 exec 模型自己写的源码）原本是那个 import 的副作用，现已移到
`bootstrap.register_business_capabilities()` 末尾，并硬性排在内置工具 import 之后——
否则会出现「自创工具先占了名字、内置随后被重名拒绝」的顺序依赖。

规则：凡是**持久化的、模型自创的、需要能被卸掉的**注册，都不许挂在 import 上。

### 12.3 生命周期：启动即登记收尾

`api/main.py` 的 lifespan 原先是 ~250 行顺序启动 + 一份手写收尾清单，两份清单对不上：
收尾顺序不是启动的逆序（模型工厂先关，还在用它的感知/心跳后关），且有 5 个组件只有启动
没有收尾。现在每一步启动都在自己旁边用 `_push(stack, dispose)` 登记撤销：

```
async with AsyncExitStack() as stack:
    起一个组件 → _push(stack, 怎么停它)
```

- **登记即时生效**：启动走到一半失败，已经起来的部分照样撤干净（旧写法整段收尾被异常跳过）
- **撤销必然逆序**：由结构保证，不再靠人记得清单顺序
- **失败即不登记**：没起来的组件不会被「停」一次
- 数据库连接池是懒打开的、没有启动步骤，因此作为**最早**登记的时效 → **最后**撤销
- 原有的致命/降级语义逐步保留：API Key、Embedding 加载失败致命；视觉预加载、感知、
  MCP 启动失败降级继续
- 已补齐的 5 处配对：感知集成器、桌宠引擎、会话定时任务、屏幕内容分析、
  `error_bus` 装在事件循环上的 handler
- 自制插件那一段的位置有讲究：登记卸载**排在挂载之前**（挂载走到一半失败也有人
  收尾），整段排在**致命校验之后、第一个会调用工具的组件之前**（撤销走逆序，于是
  模型工厂/感知/心跳/桌宠/定时任务都先停下，工具才从它们脚下抽走）
- 测试：`tests/unit/test_api_main.py` 139 项（133 项原有 + 3 项 pin 上述新语义
  + 3 项 pin 自制插件的恢复/半挂载回滚/撤销顺序）

### 12.4 运行期自制插件（L5：模型自己写源码，当场挂上）

`modules/shared/plugin_authoring.py` 是机件，外面三个门面。机件只认四条规则：

```
mount_source(name, source, replace=False)      # 编译 → apply(ctx) → 观察差集 = 它提供了什么
author(name, source, trial, replace, store)    # 上面 + 试运行 + 通过才写盘
load_authored(store) / unload_authored()       # 启动恢复（按创建顺序）/ 收尾撤销
drop(name) / status()                          # 卸而不删 / 盘上的每一条现在在不在跑
```

- **声明换成观察。** 挂载前后各读一次「模型可见集」（`get_tools_for_api(["*"])` ∪
  `registered_names()`），差集就是这个插件提供了什么。没有清单字段，就没有可以少报
  的地方——这条是 §12.5 那条不变量在这一层的实现。
- **冲突两道闸。** `provide` 在绑定那一刻拒（它知道现在谁在位）；观察兜住**绕过
  `provide` 的绑定**（无沙箱意味着插件源码可以直接写 `register_tool`）。只有另一个
  *自制* 挂载占着名字才算冲突，遮蔽内置是接管；`replace=True` 同时放行两道，因为
  按持久化顺序重新恢复时「后来者赢」要成立。报错分两类：`PluginConflictError`（带
  「由谁应答」）与 `DuplicateToolError`（owner 不是自制挂载时不硬编名字）。
- **同名重提交先撤后装。** 反过来会漏一个看不见的东西：新版撤销时登记的是「放回它
  遮蔽的那一格」，那一格当时住的是旧版自己人——卸载后工具回到一个已经没有插件拥有
  的实现，模型看得见，日志查不到归属。装不回去就当场把旧版再装上（`_restore`）。
- **试运行通过才写盘。** 盘上每一条都曾经成功过一次，重启后回来的东西不必再现场验证；
  单条挂不上记在它自己那一行上（`status="failed"` + 原因）然后跳过，不拖垮启动。

三个门面**不许加策略**，只翻译形状：

| 门面 | 位置 | 形状 |
|---|---|---|
| 装配（启动恢复） | `api/main.py` lifespan | `load_authored` + `_push_async(stack, unload_authored)` |
| 模型 | `modules/shared/plugin_authoring_tools.py` | `mount_plugin / unmount_plugin / list_plugins / delete_plugin`，由 `bootstrap` import 触发注册 |
| HTTP | `modules/shared/plugin_authoring_api.py` | `POST/GET/DELETE /plugins/authored`，失败带 traceback 进 `error.message` |

工具面是同步的（工具执行走 `asyncio.to_thread`），挂载是协程，所以带一个 `_run` 桥：
本线程没有循环就自己开一个，有循环就把协程送到独立线程——绝不在别人的循环上调
`asyncio.run`。

测试：`tests/unit/test_plugin_authoring.py`（33 项，机件）+
`tests/unit/test_plugin_authoring_entries.py`（18 项，模型面与 HTTP 面）+ lifespan 那 3 项
（恢复、半挂载回滚、撤销顺序）。2026-11-14 用 `/tmp/mutate_b4.py` 把这些主张逐条反向改
一遍——机件 19 条 + 门面 10 条，共 29 条变异，**0 条存活**，且跑完落盘文件与开始时逐字节
一致。2026-11-15 门面端口观察层新增的 3 条主张同样过了一遍（`/tmp/mutate_b6b4_ports.py`，
4 条变异，**0 条存活**）。这份表不是「测试碰巧过了」，是「每条都有人守着」。

### 12.5 刻意没有的东西

无权限模型、无签名校验、无沙箱、无隔离：插件代码在宿主进程内以解释器的全部权限运行，
这是既定策略（本地单用户应用；治理那半边曾实现过并已删除）。代替信任边界的是**可撤销、
失败不留半挂载**，以及一条不变量：**模型可见的 == 日志里查得到的**
（`provide` 填入 `source="plugin"` + `plugin_name`，`get_plugins()` 与
`GET /tools/plugins/loaded` 读的就是它）。

已知边界（不要按 flow 侧的语义理解 agent 侧）：
- `Ctx.provide_tool` 直接写进程级注册表，**没有 flow 侧那种 per-run 冻结帧**——本仓读
  `_tools` 的地方有二十多处、跨 6 个模块。后果：运行中途卸载插件，剩下的工具调用会解析
  回内置实现。
- 试运行是「提交人自己点名一件事」，不是全覆盖：没有点名本次新挂的工具的 trial 会被
  拒（那试的是内置实现，等于没试）。
- 插件源码里 `register_tool` 直写注册表留下的绑定，卸载撤销不到（撤销只覆盖 `provide`
  登记过的那一格）。第二道闸会**拒绝**这种挂载，但拒完之后那条绑定还在——测试里断言的
  是这个边界本身，不是理想。

