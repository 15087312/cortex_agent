# 错误原因与修复记录 · 后端 · 记忆 / 因果 / 心理活动 / 感知

[← 返回总索引](./README.md) | [English](./02-backend-memory.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 15 节：§2、§9、§11、§16、§40、§41、§42、§43、§49、§50、§51、§65、§105、§106、§107。

---
## 2. 分类记忆工具加载失败 — 悬空引用（后端）

**报错（WARNING，非致命）：**
```
分类记忆工具加载失败: No module named 'modules.memory.tools'
```

**根因：** `infra/tool_manager/tools/__init__.py` 残留旧架构的死引用：
```python
importlib.import_module("modules.memory.tools.classified_memory_tool")
```
该文件曾在旧结构（`modules.infra.*`、`modules.memory.classification_memory`）下存在，仓库重构后已删除，引用未清理。功能已被新记忆工具取代：`memory_match`、`memory_score`、`memory_batch_filter`、`event_query`。

**修复：** 删除死引用代码块，保留注释说明。

**验证：** 工具包导入正常，87 个工具注册，记忆工具齐全，无告警。
**同类排查：** 自动化扫描 `modules.*`/`infra.*`/`backend.*`/`api.*` 全部 import 引用，无其他悬空模块。

---

## 9. 无关历史记忆注入污染上下文（后端）

**现象：** 新建会话后只发送一个"1"，系统提示词里仍注入 5 条**无关**的历史记忆（如之前"在 /Users/abc/123 建网站"的任务）。

**根因：**
1. 记忆检索 `EventRetrieval.retrieve()` 用 FAISS 向量搜索，对**无语义内容的短查询**（如"1"）仍返回 top-k 记忆
2. `MIN_SEMANTIC_SIMILARITY=0.20` 阈值过宽松：短查询的 embedding 几乎不与任何记忆正交，都能通过
3. `_rank_and_filter` 归一化后 top-1 恒为 1.0，必过阈值
4. 且 `importance` 权重 0.20 单凭高分记忆（如 importance=1.0 → 0.20 分）就能越过阈值
→ 无关但重要的历史记忆总是被注入，污染上下文、误导模型

**修复（会话记忆优先 + 标注区分）：**
- 记忆检索改为**会话优先**：先取本会话（`session_id`）产生的事件作为"会话记忆"，**优先展示、重要性更高**
- **无会话记忆时不注入事件记忆**：新建会话（无本会话历史事件）时跳过全局事件记忆检索，不再拖入无关历史
- **标注区分**：注入时明确分为两段——
  - `【当前会话记忆】`：本会话产生的事件（优先展示）
  - `【过去发生的事】`：全局事件记忆，每条**标注日期**（`YYYY-MM-DD`）
- 有会话记忆时才补充全局相关事件记忆（agent 路径补语义检索，chatonly 路径补深度回忆 + 语义检索）
- 显式 `_memory_focus`（用户主动要求召回）始终执行
- 改动：`modules/thinking/core/continuous_thinker.py` + `backend/chat/continuous_thinker.py`

**会话记忆实现方式（调查结论）：**
1. 事件存于 `EventStore`（SQLite + FAISS），`MemoryEvent` 带 `session_id` 字段
2. 任务后提取事件时（`api_stream._post_task_extraction` → `EventReducer.reduce(session_id, ...)`），每条事件被标记 `ev.session_id = session_id`（`event_reducer.py:139`）
3. 提示词注入时（`continuous_thinker.py`），会话记忆 = `store.list_events(500)` 按 `session_id == self._session_id` 过滤
4. 注意：库里 281 条事件中 266 条 `session_id` 为空（旧路径创建），仅 15 条带会话标记——新会话需经过几轮对话产生事件后才会有"当前会话记忆"

**验证：** 两处语法 OK；模拟输出：会话事件标`【当前会话记忆】`，全局事件标`【过去发生的事】`并带日期。

**同类排查：** 两条记忆注入路径（agent / chatonly）均已改为会话优先；`event_query`/`memory_score` 等**工具**检索保留原行为（模型显式调用）。

---

## 11. 审批结果未回传模型 + 黑板生命周期问题

### 11.1 审批通过结果未回传大模型（后端）

**现象：** 用户批准/拒绝后，大模型不知道是否经过了审批流程，只知道工具执行了。

**根因：** `gate.check` 返回 `(True, "用户批准: ...")` 后，model_runner 只把**工具执行结果**追加为 tool 消息，`reason`（审批通过信息）被丢弃。发起审批的模型只看到"文件创建成功"，不知道"经过了用户审批"。

**修复**（`model_runner.py` 工具执行后）：
```python
if reason and reason.startswith("用户批准"):
    result = f"[用户审批已通过] {reason}\n{result}"
```
- 通过：tool 消息前置 `[用户审批已通过] 用户批准: ...`
- 拒绝：`[安全门控拦截] 用户拒绝: ...`（原有）

**验证**：tool 消息配对校验通过（不会 400 orphan）；审批信息保留在 tool 消息中，下一轮 API 调用传给模型。

### 11.2 黑板生命周期（结论：per-turn 一次性）

- **每次用户对话** → 新建 `CognitiveBlackboard`（新 turn_id），对话结束 finally 清理 manager
- 一次思考内的多轮工具循环共享黑板，observations 累积其中
- **跨对话不累积**——对话历史靠 `context`（会话消息历史）每轮重新注入，不靠黑板

### 11.3 黑板复用竞态修复（后端）

**问题：** `remove_runner_manager` 原为 `asyncio.create_task`（异步），短间隔两次对话时旧 manager 未清理，`get_runner_manager` 复用旧 manager + **旧黑板**（对话历史/expert_findings 全过期）。

**修复：**
- `get_runner_manager`（model_runner.py:2784）复用时**更新** manager 的 `blackboard`/`turn_context`，并同步到所有 runner
- `multi_model_orchestrator.py` finally 改 `await remove_runner_manager`（同步清理）

**验证：** 复用返回同一 manager，但 `blackboard` 已更新为新黑板 ✓

### 11.4 旧 observation 清理（后端）

**问题：** blackboard `observations` 无上限，长轮次思考会无限累积。

**修复**（`blackboard.py`）：
- 加 `MAX_OBSERVATIONS = 200` 类常量
- `add_observation` 超限时删除最旧的（读取方只取最近 5 条，删旧的不影响推理）

**验证：** 250 次观察 → 保留 200 条，最旧 50 条被清 ✓

---

## 16. 黑板共享记忆：角色别名不匹配（后端）

> 归档自 `docs/MEMORY_INJECTION.md` 第十节

**现象：** 多模型协作中总指挥看不到 `【协作上下文】`、`【当前委托状态】`、`【当前任务进度记事本】`、`【历史输出】` 等黑板共享片段——专家产出没完整回到总指挥，潜在协作断裂。

**根因：** `TurnContext.view(role)` 按 `role in target_roles` 过滤，而 `continuous_thinker._build_prompt` 里 `role = getattr(self, '_role', 'orchestrator')` 恒为 `'orchestrator'`；但这些片段的 `target_roles=("large",)` → 大模型（总指挥）匹配不上。

**修复**（`modules/thinking/context/pool.py` `view()`）：把 `"large"` 与 `"orchestrator"` 视为**同一角色（总指挥）的两种写法**——查看角色是二者之一时可见含任一别名的片段；supervisor/expert 仍精确匹配。

**验证：** `view('orchestrator')` 返回全部片段；`view('supervisor')` 只返回系统指令+历史记忆；`view('expert')` 只返回历史记忆。

**同类排查：** 全局检索角色字符串常量（`"large"`/`"orchestrator"`/`"supervisor"` 等）是否被硬编码成两种写法且未归一——配置项里同一角色多种叫法极易导致匹配失败。

---

## 40. 深度回忆无噪声守卫 → 无关查询臆造因果链（后端）

**现象：** 对 `deep_recall("今天天气怎么样")`、`deep_recall("今天午饭吃什么")` 等完全无关的查询，深度回忆仍**成功**返回锚点 + 因果链 + 佐证事件（如把"午饭吃什么"锚到「人手不足」并输出 6 条因果链）。评测矩阵暴露：`find_anchor_nodes` 对噪声查询返回锚点「库存告急」0.356。

**根因（两层）：**
1. `causal_graph.find_anchor_nodes` 语义分用**除以最大值归一化**（`sim / max_sem_score`）——无关查询也会把"语义最近"的节点抬到固定 `0.4×1.0=0.40`，属于"矮子里拔将军"；
2. `deep_recall` 拿到锚点后**无任何置信度下限校验**（depth_recall.py 原 `anchors[0][1]` 直接使用），0.40 分照样继续跑。

**修复：**
- `find_anchor_nodes`：语义分改用**绝对余弦相似度**；无关键词命中的节点必须 `abs_sem ≥ 0.30`（`_MIN_ANCHOR_SEMANTIC_SIMILARITY`）才构成锚点，得分直接用 `abs_sem`
- `deep_recall`：新增锚点置信度守卫 `CAUSAL_MIN_ANCHOR_CONFIDENCE`，低于阈值回退浅层（`low_anchor_confidence`）
- 阈值校准（实测）：真实语义匹配 ≥0.53、闲聊噪声 ≤0.37 → 锚点下限定为 **0.50**（`config/settings.py`）

**验证：** 噪声查询全部正确回退（`no_anchor_nodes` / `low_anchor_confidence`），深度佐证 0 误召回；正常查询锚点置信度从虚高的 1.0/0.40 变为真实的 0.90-0.94。

**经验：** 归一化分数在"候选集无相关项"时必然产出高分，任何打分环节都要考虑**绝对阈值**而非相对归一化。

---

## 41. 跨场景事件污染佐证 + 增量挂链自我强化（后端）

**现象：** 「服务器宕机」事件 `ev_server_down` 反复混入「项目延期」场景的深度回忆佐证事件；且一旦混入，`_incremental_update` 会把它**永久挂到延期因果节点**上（写 `causal_node_ids`），下次直接命中 1.0，误检被自我强化。

**根因：** `depth_recall._causal_relevance` 的向量匹配兜底太宽松——`ev_server_down`（"故障持续三小时…"）与目标节点「发布事故」余弦 **0.402** 过 0.35 准入线；同时 `_recall_events` 的语义分只有 `0/0.5` 两档，`_incremental_update` 对佐证事件**无因果门槛**一律挂链。

**修复（depth_recall.py）：**
1. `_recall_events` 增加**佐证准入门槛** `CAUSAL_MIN_EVENT_RELEVANCE=0.35`：因果关联不足的事件不得冒充因果佐证
2. `_incremental_update` 增加**挂链守卫**：`_causal_relevance < 0.35` 的事件不进因果图
3. `_causal_relevance` 改用注入的 `self._graph`（替代 `CausalGraph.get_instance()` 单例，生产等价、测试更稳）

**验证：** 延期场景佐证不再出现服务器事件；单测 `test_incremental_update_no_causal_relevance_skipped` 覆盖。

**经验：** "语义相关" ≠ "因果相关"。佐证事件必须有**显式关联或词项证据**支撑，纯语义相似度不能冒充因果佐证；且**写入型副作用**（挂链）必须有独立门槛，不能依赖"进了结果列表"。

---

## 42. 显式归属守卫过度杀伤同链事件（后端）

**现象：** 新增矩阵场景后，`deep_recall("新功能上线后为什么出现回归")` 漏掉佐证事件 `ev_regression`（挂「功能回归」节点，与锚点「新功能上线」同因果链但不在目标集合内）。

**根因：** §41 引入的"显式归属守卫"（事件所有显式关联都在目标集合外 → 返回 0）**一刀切**——把同链下游事件也误杀了：锚点集合只有 `{n_feature}`，`ev_regression` 归属 `n_regression` 不在集合内 → 0。

**修复：** 守卫改为**图上连通性判断**：事件归属节点若与目标集合 1 跳邻接（同一因果链，`_node_connected_to_set`）视为同链相关，不再零分；同链关联给**基础分 `0.4 + 0.6×信号`**（与直接命中同档，向量/文本信号调节）。孤立节点（如「补丁发布」）仍返回 0，跨场景防护不受影响。

**验证：** `ev_regression` 因果关联从 0.000 → 1.000 正常召回；`ev_patch_day` 仍被排除。新增单测 `test_causal_relevance_connected_assignment`。

**经验：** 用"不在集合内"判定"无关"是**欠考虑**的——因果图里集合外的邻居可能是同链相关。负向判定要基于**图结构**（连通性）而非**集合成员**。

---

## 43. 因果链单节点噪点与重复刷屏（后端/展示）

**现象：** 深度回忆输出大量**单节点链**（如 `人手不足 (85%)`，没有箭头、看不到与锚点的关系），且同一链路重复出现多次（如「人手不足」出现 2 次）；溯源链方向用 `←` 拼接，语义混乱。

**根因：** `causal_tree.trace_up/trace_down` 的 `path_nodes` **不含起点节点**——单跳因果只回传一个前驱节点；`deep_recall` 对锚点 + 每个邻居都调 `trace_up`，不同入口产出相同链路不去重。

**修复（depth_recall.py）：**
- 收集链后**补全锚点**：溯源链尾部补锚点、预测链头部补锚点 → 单跳因果显示完整路径「人手不足 → 项目延期」
- 按节点序列**去重**
- 展示统一为因→果顺序 ` → ` 拼接（`DeepRecallResult.format` / `result_fusion.format_deep_recall_result` / `_build_conclusion`）

**验证：** 因果链从"6 条单节点链（重复）"变为"3 条完整路径（去重）"；结论「核心链路: 人手不足 → 项目延期」。相关展示断言同步更新。

---

## 49. 心理活动（conscience）对话缓存跨会话累计（后端）

**现象：** 用户反馈"切换会话后实际注入的上下文没变化反而一直累计"。排查对话历史按 session 隔离无误，但**心理活动**（内心独白）仍引用其它会话的对话。

**根因：** `Conscience` 是**全局单例**（`get_conscience()`），其对话缓存 `_last_dialog_buffer` 是**实例级、不分 session**（conscience.py:70-78）：
```python
def add_to_dialog(self, role, text):       # 无 session 参数
    self._last_dialog_buffer.append(...)    # 所有会话共用一个 buffer
```
`think()` 取 `recent_dialog = self._last_dialog_buffer[-6:]` → 切换到新会话，心理活动的"最近对话"仍是旧会话的，跨会话累计。这是与 §47 同类的"单例状态未按 session 隔离"问题。

（对照：`_get_causal_knowledge`/`_get_node_ids_from_events` 已按 `owner_id` 隔离事件检索，唯独 dialog buffer 漏了。）

**修复（conscience.py）：** 对话缓存改为**按 session 隔离**：
- `_last_dialog_buffer` → `_dialog_buffers: Dict[str, list]`（owner_id/session_id → buffer）
- `add_to_dialog(role, text, session_id="large_primary")`：默认值向后兼容旧调用
- `think()` 的 `recent_dialog` 与内部 `add_to_dialog("assistant", ...)` 都按 `owner_id` 取对应 buffer
- `continuous_thinker.py` 调用处传 `session_id`

**验证：** 新增 `test_add_to_dialog_session_isolated`（不同 session 互不累计），更新 3 处旧断言到新属性；相关 155 项测试通过。

**排查信号：** "某功能是全局单例 + 内部有实例级可变状态（list/dict）缓存" 而该状态本应按会话隔离——`add_to_dialog` 无 session 参数即是可疑点。同类：`_recall_memories` 注入的全局事件记忆【曾经发生的事】也是跨会话累计源（设计为跨会话经验复用，如需严格隔离可后续加开关）。

---

## 50. 因果图不跟记忆库走 → 多记忆库因果知识跨库污染（后端）

**现象：** 切换记忆库后事件库（EventStore/FAISS）随库切换，但因果图仍全局共享（`data/causal.db`）——不同人格/体系的记忆库提炼的因果知识互相污染，深度回忆用 A 库的因果链佐证 B 库的事件。

**根因：** `switch_memory_lib`（settings.py）只切换 `MEMORY_DB_PATH / MEMORY_FAISS_INDEX / MEMORY_ID_MAP` 三个事件库路径，**不切换 `CAUSAL_DB_PATH`**；`CausalGraph` 全局单例固定用 `data/causal.db`，`_reset_memory_singletons` 也不重置因果图单例。

**修复（因果图跟记忆库走）：**
- 每个记忆库配独立因果图路径：`lib["causal"] = data/causal_{safe}.db`（与事件库同目录，同名派生）
- `get_memory_libs` 默认库、`create_memory_lib`、`delete_memory_lib` 默认重建均带 `causal` 字段
- `switch_memory_lib` / `_apply_current_memory_lib`（启动时）同步设置 `CAUSAL_DB_PATH`，且**兼容旧库**（无 causal 字段时按库名派生并补写 memory_libs.json）
- `_reset_memory_singletons` 重置 `CausalGraph._instance`，切库后按新路径重新加载

**验证：** 新增 5 项测试（切换因果路径 / 旧库派生兼容 / 创建带 causal / 启动应用 / CausalGraph 单例重置），相关 403 项测试通过。

**教训：** "多套隔离存储"（记忆库）必须**完整复制隔离维度**——只隔离事件库而遗漏因果图/向量索引/单例，会让看似隔离的体系在推理层串味。审计点：隔离库的**所有**路径型配置 + 相关单例是否一起切换。

---

## 51. 心理活动显示思维链而非内心独白（后端）

**现象：** 心理活动（内心独白）输出变成大段思维链——"用户让我回忆过去的经验…让我组织语言…构思：…让我检查…"，而不是括号包裹的简短独白。

**根因（两层）：**
1. **`max_tokens=500` 太小**：思考型（Reasoner）模型（deepseek-v4-flash）会先输出长思维链再产出正式独白。心理活动 prompt 含因果知识+历史对话，模型思考超过 500 token 被截断在思考阶段 → `content` 为空。
2. **`small_model_client.generate` 的 reasoning 兜底把思维链当输出**：`if not content and "reasoning_content" in message: content = reasoning`（small_model_client.py:186-189）——content 为空时把完整思维链当作正式输出返回。

实测：模型对心理活动请求，正常时 `content` 是干净的"（我记得…）"独白（500→128字、1500→149字）；只有思考被截断（content 空）时才触发 reasoning 兜底显示思维链。

**修复：**
- `conscience.think`：`max_tokens` 500 → **1500**（给思考留足空间，产出正式 content）
- 三个模型 client 的 `generate()` reasoning 兜底全部改为 `fallback_to_reasoning: bool = False` **默认关闭**：
  - `small_model_client.generate`、`large_model_client.generate`、`medium_model_client.generate`
  - 思考过程（思维链）**永远不冒充正式输出**——content 为空即返回空，由调用方降级；仅显式开 `True` 的极少数"以思维链为产物"场景才兜底
  - 全仓库确认**无非测试调用方传 True**；流式路径（large chat_stream）本就正确分离 `reasoning_content` 到思考区

**验证：** 更新三个 client 的默认行为测试（`test_reasoning_not_used_by_default` / `test_generate_does_not_use_reasoning_by_default`）+ 显式 True 测试；相关 299 项测试通过。

**教训：** 思考过程与正式输出是**两个独立通道**——`content` 是产物、`reasoning_content` 是过程，**永远不应混用**。模型截断/异常时宁可返回空让调用方降级，也不能把思维链塞进正式输出；"兜底"默认必须关闭，需显式选择才启用。

---

## 105. 切换会话后心理活动框消失 —— mental 事件不持久化（前端/后端）

> **编号修订：** 原文此处误用 §41（与前面的 §41 重复），现编为 §105。

**现象：** 前端切换会话窗口后，"心理活动"框消失（该会话的心理活动记录不可见）。

**根因：** 心理活动（`msg_type='mental'`，conscience 内心独白）此前**只做实时 WS 推送、不持久化**（`push_content(persist=False)`，设计为避免污染 AI 上下文）——切换会话后历史加载无 mental 消息 → 框为空。

**修复：**
1. 后端：`multi_model_orchestrator` 心理活动持久化为 `role="mental"`（`persist=True`）；`api_stream` 恢复上下文时过滤 `mental`（同 thought，不污染模型输入）
2. 前端：`chat.js` 切换会话历史加载时渲染 `role="mental"` → `kind:'mental'` 心理活动框（与实时事件一致）

**验证：** 后端 212 passed + 前端 chat store 27 passed；mental 以 `role="mental"` 持久化、恢复上下文被过滤。

**注意：** `chat_gateway` 中 conscience 逐段 mental（流式 token 中间态）仍不持久化——主要心理活动（orchestrator 整合的内心独白）已持久化可见。

---

## 106. 环境感知未注入大模型上下文 —— dd1ee8b 重构回归（后端）

> **编号修订：** 原文此处误用 §42（与前面的 §42 重复），现编为 §106。

**现象：** agent 模式（model_runner）与纯对话（chat_light）下，模型的 system prompt 里没有【环境感知】——感知系统在运行（窗口/屏幕/OCR），但模型看不到环境。

**根因：** `dd1ee8b`（2026-06-27）重构"上下文和提示词系统"时**移除了编排器的 `get_context_summary()` 感知注入调用**；重构后的新机制（`PerceptionSource`→`PerceptionPool`）只接入了桌宠/连续思考（core）/主动搭话，**未接入 `model_runner`（agent）与 `chat_light`（纯对话）**——感知注入从此断了约 2 个月。

**修复：**
1. `model_runner._build_system_prompt_for_mode` 调用处：`PerceptionSource().collect()` → 追加【环境感知】块
2. `chat_light/continuous_thinker`：心理活动注入后同样追加【环境感知】
3. 均 try/except 容错（感知未初始化/异常不影响正常对话）

**验证：** 两处注入均生效（mock PerceptionSource → system_prompt 含环境感知）；新增 `test_perception_injection.py`（4 用例）；相关 266 passed。

**教训：** 大规模重构（上下文/提示词系统）后必须回归"数据→prompt"完整链路；感知这类"采集端正常但注入端断"的问题，代码审查难发现，需注入链路测试。

---

## 107. 文件感知功能实际无效 —— 采集端与消费端管道断（后端）

> **编号修订：** 原文此处误用 §43（与前面的 §43 重复），现编为 §107。

**现象：** 修改项目根文件后，感知池仍返回"当前无感知数据"——文件变化从未进入模型上下文。

**根因：** `FileDifferenceSource.detect()` 只产生 `Difference`（供 detector 存储/高强度回调），**从不发布 `FILE_CHANGE` 感知事件**；感知池订阅了 `FILE_CHANGE` 但**没有任何发布者**。且 `file_modified` intensity 25 < 阈值 50，也不触发 high_intensity 回调。采集端（检测到差异）与消费端（感知池→模型）之间的管道是断的。

**为什么没被测试发现（系统性盲区）：**
- 单测验证组件各自正确：`detect()` 返回 Difference ✓ / detector 注册 ✓ / integration 格式化 ✓——但**组件间的"数据管道"（谁把 Difference 发布成感知事件）无人测**
- 模块覆盖清单只证明"被 import 执行"，不证明"数据到达消费者"
- 这是 §42（感知注入消费端断）的同类模式——生产端也断

**处理：** 删除文件感知功能（采集端本无真实发布者，链路不完整）：
- 删除 `FileDifferenceSource` 模块 + `test_file_source_ext.py`
- detector 移除注册；integration 移除 FILE_CHANGE 订阅/格式化
- settings 移除 `PERCEPTION_FILE_ENABLED`；前端设置页移除文件监控开关
- 相关 mock 测试更新（test_difference_detector / test_perception_integration_ext）

**防再犯：** 补感知数据链路端到端测试（真实发布事件 → 感知池 → collect → 断言内容），任何"采集正常但管道断"都会被捕获。

---

## 65. 黑板 final_response 设置后未落库 —— 重启后最终回复丢失（后端）

**现象：** 端到端验证黑板快照时发现，`blackboard.persist()` 只在断点保存/委托链记录时调用；`final_response` 作为终态在 `set_final_response` 设置后从不落库 → DB 快照的 `final_response` 恒为 None。

**根因：** 持久化时机覆盖不全——只覆盖了"过程态"（断点/委托），遗漏了"终态"（最终回复）。

**修复：** `CognitiveBlackboard.set_final_response` 末尾调用 `self.persist()`（失败不阻塞），保证最终回复落库可恢复。

**验证：** `test_delegation_chain_full_flow` 断言 `state["final_response"] == blackboard.final_response`；相关 503+ 通过。

**教训：** 黑板持久化要覆盖"终态"字段（final_response 等），不能只持久化过程态；写终态的方法本身应触发落库。
