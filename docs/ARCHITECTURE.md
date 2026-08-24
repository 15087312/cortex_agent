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

`tests/` 137 个测试文件（unit/integration/external），全量 1700+ 项。隔离原则：
临时 SQLite + monkeypatch 单例（不碰生产库）、重库加载放宽 timeout、后台线程类提供 `stop()`、
禁 `except: pass` 吞错、测试假对象与真实模型字段一致。

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
│ 85 个  │    │  7+ 个服务器  │    │ 运行时创建  │
└─────────┘    └──────────────┘    └────────────┘
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
└── get_capability(name)                  # 工具层获取；缺失返回 None 优雅降级

bootstrap.py  register_business_capabilities()
   └── 9 个能力：blackboard_query / skill_manager / event_retrieval /
       file_history / touchpoint_detector / detector_router /
       value_formatter / tool_security_gate / turn_images
   └── 启动期校验：_report_capability_status 报告缺失/失败能力（fail-fast）
```

- 依赖方向：`modules → infra`（逆向依赖 `infra→modules` 已归零）
- 工具通过端口取服务，缺失时返回显式错误信息（而非 ImportError）
- 测试经 `register_capability(name, fake)` 注入 mock

### 10.3 AI 自创工具

```
infra/tool_manager/tools/create_tool.py
├── create_tool(name, code, description, params)   # 创建新工具
├── list_my_tools()                                 # 列出所有自创工具
├── delete_tool(name)                               # 删除自创工具
└── edit_tool(name, code, description, params)      # 编辑已有工具
```

- 自创工具以 `.py` 文件持久化到 `data/user_tools/`
- 运行时通过 `ToolRegistry.register` 动态注册
- 创建/编辑时自动语法检查（`compile()` + `ast.parse()`）
- 支持断网离线创建和执行

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
