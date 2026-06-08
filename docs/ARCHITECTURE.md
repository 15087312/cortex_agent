# 架构设计文档

> Cortex Agent 系统架构详解 — 四层分层、事件驱动黑板、多模型编排、协议解耦

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
│  16 个业务模块：思考/记忆/安全/感知/注意力/输出/插件/...   │
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
  │     ├─ SessionLifecycle.start_turn()       → TurnContext + CognitiveBlackboard
  │     ├─ ModelRunnerManager.start_listening()
  │     ├─ 注入上下文到 Blackboard（委托指导、专家指导、记忆）
  │     ├─ MessageBus: probe_start("large")    → ModelRunner 激活
  │     ├─ ModelRunner._think_loop()
  │     │   → ContinuousThinker.continuous_think()
  │     │     → build_prompt → model.chat() with tools → parse tools
  │     │     → delegate_task → ProbeDelegationAdapter → probe_start
  │     │     → write to Blackboard → loop or finalize
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
| `ContextPort` | `ContextManagerAdapter` | 记忆/上下文加载/保存/注入 |
| `GuidancePort` | `PreGenExpertGuidanceAdapter` | 预生成专家管线（情感+价值观+安全） |
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
| `config/model_config.py` | `LargeModelConfig` 等 | 模型参数（max_tokens、temperature） |
| `config/memory_config.py` | `MemoryConfig` | 记忆 TTL、向量维度、批量操作 |
| `config/attention_config.py` | `AttentionWeightConfig` 等 | 注意力权重、中断规则、调度配置 |
| `config/output_config.py` | `OutputPriorityConfig` 等 | 输出优先级、TTS |
| `config/plugin_config.py` | `PluginConfig` | 插件目录、沙箱设置 |

### 5.3 运行时配置修改

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

12 个内置身份模板：large、code_supervisor、query_supervisor、creative_supervisor、code_reviewer、code_implementer、test_writer、analyzer、security_monitor、customer_expert、creative_writer、emotion、memory_manager。

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

### 7.2 压缩引擎（CompressionEngine）

5 级压缩：

| 级别 | 策略 | 触发条件 |
|------|------|---------|
| NONE | 原样返回 | token 预算充足 |
| LIGHT | 去空行和注释 | 轻微超限 |
| MODERATE | LLM 摘要旧事件 | 中度超限 |
| HEAVY | 结构化压缩，仅保留关键句 | 严重超限 |
| AGGRESSIVE | 仅保留关键词和结论 | 极端超限 |

自动选择级别：根据 token 预算和当前占用量计算。

### 7.3 审计器（Auditor）

- **冗余检测**：Jaccard 相似度
- **内存使用监控**
- **一致性检查**：时间戳排序、事件-文件交叉引用
- 结果缓存 60 秒

---

## 8. 记忆系统架构

### 8.1 T1-T6 管线

详见 README.md 记忆系统章节。关键实现细节：

| 阶段 | 实现位置 | 异步策略 |
|------|---------|---------|
| T1 | `api_stream.py` `_preload_session_memories` | `asyncio.create_task` (fire-and-forget) |
| T2 | `ContextManager.load_context` | 同步（在 orchestrator 流程内） |
| T3 | 工具执行后钩子 | `asyncio.create_task` |
| T4 | `CompressionEngine` | 按需触发 |
| T5 | `_post_task_extraction` | `asyncio.create_task` |
| T6 | `MemoryManagerExpert` | 定时任务（12小时周期） |

### 8.2 向量检索

- **模型**：`all-MiniLM-L6-v2`（sentence-transformers）
- **索引**：FAISS（IndexFlatIP，内积相似度）
- **维度**：768
- **搜索**：`MemoryMatchEngine` 四维评分（语义 40% + 关键词 20% + 时间衰减 20% + 重要性 20%）

---

## 9. 安全架构

### 9.1 三层防护

```
输入 → [输入审查] → [执行审查] → [输出审查] → 响应
         │              │              │
         ▼              ▼              ▼
    SecurityAPI    SecurityGate    SecurityMonitor
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
- **完整性**：SHA-256 哈希链（每条记录包含 previous_hash + current_hash）
- **内容**：所有工具调用、权限决策、安全事件
- **检查点**：Ed25519 签名锚定

---

## 10. 插件系统架构

详见 [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md)。

关键架构层次：

```
Model/Expert 调用层
  ↓
治理层 (Governance) — 预算/确认/幂等/限流/循环检测
  ↓
引擎层 (Engine) — 生命周期/沙箱/调用/断路器
  ↓
网关层 (Gateway) — 权限校验/资源访问
  ↓
插件沙箱 (Sandbox) — sub_process 隔离
```

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
