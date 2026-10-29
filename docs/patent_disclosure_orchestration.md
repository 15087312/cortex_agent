# 发明专利技术交底书

**发明名称（建议）**：一种基于探针间接激活与认知黑板的多模型分层协作方法及系统

**对应项目**：Cortex Agent（多模型协作 Agent 系统）
**交底日期**：2026-10-23
**申请类型**：发明专利（拟）

---

## 一、技术领域

本发明涉及人工智能领域，尤其涉及一种由多个大语言模型（LLM）实例构成的分层协作任务编排方法与系统，具体涉及多模型之间的任务分解、间接激活、状态共享与结果整合机制。

---

## 二、背景技术

现有基于 LLM 的自动化任务执行技术存在以下不足：

1. **单模型直调式协作**：常见框架中，上层"主"模型通过硬编码调用关系或函数调用直接实例化并唤醒下层模型。该方式造成：调用链与业务代码强耦合；每次调用需在内存中显式持有所层上下文，N 个模型间为 O(N²) 的上下文交叉污染源；且被调用方无需经过任何权限验证即可被唤起，存在安全风险。

2. **重复提示与上下文膨胀**：为实现"看到全局状态"，现有做法多在每次模型调用（含每次工具调用后）重建包含全局状态的完整提示词。当系统含全局黑板、长期记忆、感知上下文时，逐次工具调用都重建提示词会导致 token 消耗与延迟以调用次数线性放大。

3. **多 Agent 状态分散**：多个协作模型各自维护私有状态（待办、中间结果、发现），缺乏单一真理来源，出现重复回复、状态不一致、结果整合困难。

4. **委托深度不可控**：现有编排缺少对委托链（模型委托下层、下层再委托）的结构化记录与追溯，无法回答"这条结果来自哪条委托链、父子关系如何"。

本发明针对上述四个问题提出整体解决方案。

---

## 三、发明内容

### 3.1 要解决的技术问题

提供一种不需要模型直接调用模型的分层协作方法：使上层模型通过标准工具接口操纵"探针"对象间接激活下层模型；使所有协作模型共享唯一认知状态源（认知黑板）并按层级获取切片视图；采用双层 ReAct 循环在"全局感知"与"局部连续性"之间取得 token 收益平衡；并对任意深度的委托链建立可追溯的树形结构。

### 3.2 总体技术方案（概述）

系统采用三层模型拓扑：

```
用户输入
   ↓
[总指挥（Large）] ← 战略决策、委托分解、最终整合（唯一对用户输出）
   ↓ 委托（delegate_task 工具 → probe_start）
[主管（Supervisor，N 个并行）] ← 领域任务分解（代码/创作/查询…）
   ↓ 委托（probe_start）
[专家（Expert，N×M 并行）] ← 具体执行（工具调用为主）
   ↓ 观察结果写入黑板
[认知黑板（唯一状态源）] → 总指挥读取切片视图 → 整合最终答案
```

三个关键机制（详见第四~六节）：

- **探针间接激活**：模型→工具→探针→激活下级模型的工作流；
- **认知黑板**：分层权限写入 + 按层级切片读取的单一状态源；
- **双层 ReAct**：思考轮次（Round，低频重建 prompt 含黑板切片）与工具循环（Turn，高频增量消息）分离。

### 3.3 有益效果

| 对应背景问题 | 本发明效果 |
|---|---|
| 模型直调耦合 | 模型只面向工具接口编程，任何模型无论其提供方/协议，都可被统一探针协议激活；激活行为可插入统一权限审计 |
| O(N²) 交叉污染与重复回复 | 每轮（turn）完全隔离：模型仅从黑板切片视图读全局，不直接持有他者上下文 |
| token/延迟膨胀 | prompt 重建仅发生在"轮次"边界（新任务/被唤醒），工具循环内消息增量追加 |
| 委托不可追溯 | 委托以结构化对象（Delegation）落黑板，parent/children 构成树，origin_task_id 沿链传播，支持链查询与进度查询 |

---

## 四、具体实施方式（一）：探针间接激活机制

### 4.1 核心原则

**模型不直接调用模型。** 任何层级的模型要激活下级模型，只能：调用内置工具 `delegate_task` → 工具实现创建/操纵**探针（Probe）**对象 → 探针携带配置激活目标模型实例。

`delegation_port.py` 原文设定：

> "ContinuousThinker 负责决定模型是否要委托，但具体的系统动作（probe_start / runner 激活）由这个 port 封装，这样 thinker 就不需要直接依赖 probe 工具。"

### 4.2 方法步骤（单次委托）

以"总指挥委托某主管"为例：

1. **决策**：总指挥在思考轮次中，模型输出 `delegate_task(role=supervisor_code, task=…, wait_seconds=…)` 工具调用；
2. **载荷构建**：委托正文不硬截断（超限时头 7000 + 尾 1000 字符的结构化保留截断），下级可通过黑板接口拉取完整上下文——保证委托语义完整；
3. **探针创建**：工具层创建探针对象（probe_id），探针绑定：目标角色、目标层级（target_tier）、发起方标识（caller_model_id / caller_tier）、结果回传地址（return_to_model_id / return_to_session_id）、沿链传播的根任务 id（origin_task_id）、可选超时；
4. **身份装载**：探针根据 roles.yaml 装载目标角色的 system prompt（人格/专长/工具白名单）与用户自定义人设覆盖（优先级：自定义 personas.yaml → 自定义 agent → roles.yaml 内置 → 空）；
5. **实例化激活**：探针激活目标模型的 ModelRunner，进入该层级的思考循环（见第六节）；多个探针可并行激活多个下级（主管间并行、专家×主管并行，实测为 N×M 矩阵并行）；
6. **结果回传**：下级完成时将结果以"专家发现"（ExpertFinding）写入黑板并经消息总线唤醒上级（thinking_result 事件）；
7. **上级响应**：上级从 `_wait_for_wakeup` 状态被唤醒，重置思考缓冲，重建 prompt（含黑板新内容）进入整合轮。

### 4.3 技术效果

- 模型供应商无关：OpenAI / Anthropic / Gemini / Azure / Ollama 等不同协议的模型实例，均通过同一探针协议协作；
- 激活动作天然成为**权限与安全审计的单一入口点**（可统一插桩：审批、风控、限流）；
- 委托树可构建：每次委托生成唯一 delegation_id（= probe_id），parent_delegation_id / child_delegation_ids 与 origin_task_id 共同构成可追溯委托树。

---

## 五、具体实施方式（二）：认知黑板（唯一状态源）

### 5.1 数据结构（实施例）

黑板为每个会话（session）× 每个回合（turn）实例化的对象，含以下结构化记录（均为带元数据的物理实体）：

| 数据结构 | 字段要点 | 写入者 |
|---|---|---|
| `Delegation` 委托任务 | delegation_id、role、task、status（pending/running/replied/stale）、caller_model_id、caller_tier、parent_delegation_id、return_to_model_id、origin_task_id、probe_id、target_tier、progress、context_summary、child_delegation_ids | 上级（写委托）；下级/系统（更新状态/进度） |
| `Observation` 观察 | observation_id、tier、content | 任意层级（典型为专家） |
| `ExpertFinding` 专家发现 | finding_id、source_tier、role、content、status（pending/completed/rejected） | 主管/专家 |
| `DialogEntry` 对话条目 | entry_id、type（thought/response/user_input）、model_id、tier、content、round_num | 参与对话的模型 |

黑板进一步承载：goal（目标）、current_plan（计划）、runtime_state（运行态）、final_response、security_block（安全拦截标记）。

### 5.2 分层权限写 + 层级切片读（snapshot_for）

**写侧约束**：部分字段仅特定层级可写（如安全拦截标记仅安全模块写入）。

**读侧切片**：`snapshot_for(tier)` 输出与该层级匹配的视图——总指挥视图含目标/全部委托/全部专家发现；主管视图含本领域委托及其子委托；专家视图仅含自身任务的上下文摘要。切片同时可用游标增量读取（`new_entries()` / `get_observations_since(cursor)`），避免重复注入旧内容。

**每轮隔离**：每个用户回合（turn）创建独立黑板实例（`clear_turn_state`），回合结束即隔离——从根本上阻止上一回合的中间推理污染当前回复，这是"重复回复"问题的对策。

### 5.3 委托链查询（可追溯性）

- `get_delegation_chain(delegation_id)`：沿 parent 指针逐级回溯，返回完整委托链；
- `build_delegation_context(delegation_id)`：为下级生成含"父委托是什么、根任务是什么、我是第几层"的上下文块，作为下级 prompt 的组成部分；
- `query_delegation` 工具：任意模型可在思考中查询某委托的进度/状态，实现长任务监控。

### 5.4 变更通知

黑板维护变更回调（`on_change`），写入事件经 MessageBus 广播，实现跨进程/跨层级的事件驱动唤醒（感知差异 → 触发思考 → 委托 → 结果唤醒上级的完整闭环）。

---

## 六、具体实施方式（三）：双层 ReAct 执行模型

三个层级共用同一套执行模型，仅按层级差异化退出条件（max_rounds）：

### 6.1 外层：思考轮次（Round）——低频重构

每轮以**完整重建**的 prompt 开跑，注入：

1. 黑板切片（目标/委托/专家发现——全局认知状态）；
2. 记忆检索（向量 + 关键词混合召回，FAISS 索引 + 结构化事件库）；
3. 感知上下文（屏幕差异检测/窗口状态/语音等外部事件）；
4. 良知引导（价值观内化模块的注入块）；
5. 委托进度（跨层唤醒后的新状态）。

### 6.2 内层：工具循环（Turn）——高频增量

轮内，模型进入增量 ReAct：模型输出工具调用 → 执行 → 结果追加到 messages 数组 → 继续……直至模型输出无工具调用的最终文本，该轮结束。

### 6.3 双层分离的技术效果（与背景 2 对应）

- prompt 重建成本 O(轮次数) 而非 O(工具调用次数)——实测面板/委托/记忆重建一次的 token 开销不再随工具调用次数线性放大；
- 工具循环上下文保留增量连续性——模型不失去当前操作性上下文；
- 只有"新任务/被唤醒/委托完成"才触发重建，重建时机即状态发生实质变化的时机。

### 6.4 跨层执行流（实施例全流程）

```
指挥 ModelRunner
  while _running:
    continuous_think（轮次重建 prompt）
      → 工具循环（turns: chat → tool → 结果追加 → …）
      → 输出含 delegate_task → 委托成立 → 退出循环 → _wait_for_wakeup(300s)
                                            ↓
                                    主管 ModelRunner（同构流程）
                                            ↓ probe → 专家 ModelRunner
                                            ↓ 执行完成 → ExpertFinding 写黑板
                                    thinking_result 经总线回传
  被唤醒 → 重置缓冲 → 新一轮 continuous_think → 整合 → set_final_response
```

补充机制：
- **复杂度自适应思考预算**：按用户输入复杂度分配各轮思考预算，简单输入缩短轮次、复杂任务扩展轮数；
- **闭环安全**：每回合执行链为"安全验证 → 记忆上下文 → 专家引导（情绪+价值观+安全）→ 多模型思考 → 输出审查 → 记忆存储"，工具调用经分级安全门控（LOW/HIGH/CRITICAL，fail-closed：权限/拦截异常一律拒绝并写入 security_block）。

---

## 七、系统组成（装置权利要求基础）

系统模块与数据通路：

```
MultiModelOrchestrator（编排器，含端口：SecurityPort / GuidancePort /
  OutputReviewPort / ActivityNotifierPort 全部依赖注入）
  ├─ ContinuousThinker（思考引擎，决定委托/继续/输出）
  ├─ DelegationPort（委托抽象，封装 probe_start / runner 激活）
  ├─ CognitiveBlackboard（黑板，会话×回合实例）
  ├─ ModelRunner / _generate_with_tools（双层 ReAct 执行器）
  ├─ MessageBus（事件总线，thinking_result 等跨进程事件）
  ├─ 安全门控（分级审批 + 审计链 + security_block）
  └─ 记忆系统（EventStore + FAISS + CausalGraph）
```

关键设计决策（内部文档摘录）：
- 端口与适配器分离：编排器仅依赖抽象端口，具体实现（安全 API、输出审查、引导服务）由适配器注入，便于多企业环境接入；
- 三层共用 `continuous_think` + `_generate_with_tools`，差异化仅在 max_rounds/tier 退出条件与 roles.yaml 人格配置；
- $N$ 主管 × $M$ 专家并行：黑板天然支持并发写入、按层级切片互不冲突。

---

## 八、权利要求书示例（草案，供代理人参考撰写）

**权利要求 1（方法）**：一种多模型分层协作方法，其特征在于，包括：
- S1，由第一层级模型在思考轮次内通过内置工具发出委托指令，所述委托指令携带目标角色、任务正文与回传地址；
- S2，根据所述委托指令创建探针对象，由所述探针装载目标角色的提示词配置并激活第二层级模型实例，其中所述第二层级模型不与第一层级模型建立任何直接函数调用关系；
- S3，第二层级模型以相同机制经所述探针进一步激活第三层级模型实例，并将执行结果作为专家发现写入认知黑板；
- S4，认知黑板向请求方模型提供按层级切片的视图切片，第一层级模型在收到唤醒事件后基于该切片整合最终输出；
- 其特征还在于，每次委托在黑板上形成含父委托标识、子委托标识列表与根任务标识的结构化委托记录，多次委托构成可追溯委托树。

**权利要求 2（依据 1）**：其中所述思考轮次与工具循环分离：轮次按状态实质变化事件（新任务/唤醒）重建提示词，轮内工具调用的结果以增量方式追加至同一消息序列。

**权利要求 3（依据 1）**：其中所述认知黑板在用户回合边界完全隔离重建，配合游标增量读取接口提供变更新内容。

**权利要求 4（依据 1）**：其中探针激活行为经过分级安全门控，异常时拒绝并阻断，形成轴向审计链。

**权利要求 5（装置）**：对应的系统装置权利要求（含编排器、探针、黑板、总线各单元）。

**权利要求 6（介质）**：计算机可读存储介质。

---

## 九、检索建议（供代理人查新）

- 关键词：cognitive blackboard；LLM orchestration；hierarchical delegation；indirect activation via tool/probe；turn isolation；incremental ReAct
- 区别于（预期结合点）：AutoGPT / LangChain Agent / OpenAI multi-agent（多为直调式或队列式）；multi-agent 黑板系统（经典 AI 文献）——本发明新点集中在"工具-探针-激活三层解耦 + 委托树落黑板 + 双层 ReAct 的重建时机策略"三者的**组合**。

## 十、术语对照

| 专利文档用语 | 代码实体 |
|---|---|
| 第一层级模型（总指挥） | large tier ModelRunner |
| 第二层级模型（主管） | supervisor tier ModelRunner |
| 第三层级模型（专家） | expert tier ModelRunner |
| 内置工具 | delegate_task（model_runner 工具表） |
| 探针对象 | probe（delegation_port.probe / probe_tools） |
| 认知黑板 | CognitiveBlackboard（cognition/blackboard.py） |
| 委托记录 | Delegation dataclass |
| 专家发现 | ExpertFinding dataclass |
| 事件总线 | MessageBus |
| 分级安全门控 | ToolSecurityGate（LOW/HIGH/CRITICAL） |
