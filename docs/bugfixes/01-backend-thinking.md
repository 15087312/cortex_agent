# 错误原因与修复记录 · 后端 · 思考 / 模型 / 工具 / 委托

[← 返回总索引](./README.md) | [English](./01-backend-thinking.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 27 节：§1、§10、§19、§21、§25、§39、§46、§47、§48、§57、§59、§60、§61、§62、§63、§64、§67、§70、§76、§79、§80、§83、§84、§92、§93、§95、§97。

---
## 1. OpenAI tool 消息孤儿引用 → 工具调用死循环（后端）

**报错（API 400）：**
```
Messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

**现象：** AI 反复调用同一个工具（如 `query_tool_details`、审批类工具），`model_runner` 进入 `TOOL-LOOP max_turns=25` 死循环。

**根因：** `modules/thinking/core/model_runner.py` 工具循环中消息顺序错误：
- `query_tool_details` 与部分控制工具（`request_skill`/`stop_skill`/`list_skills`/`stop_task`）的**结果**先被追加为 `role="tool"` 消息（带各自的 `tool_call_id`）
- 但声明这些调用的 **assistant 消息**只包含 `normal_calls`，且是在结果**之后**才追加

OpenAI API 严格校验：**每条 `tool` 消息的 `tool_call_id` 必须在前序 assistant 消息的 `tool_calls` 中声明过**。校验失败 → API 400 → 工具结果从未真正回到模型 → 模型只能重复调用 → 死循环。

**修复：**
1. 在工具处理之前构建 assistant 消息，`tool_calls` 声明本轮所有会产生结果的调用：
   `normal_calls + query_calls + result_control_calls`
2. 删除 normal 块中重复的 assistant 追加
3. 控制工具异常路径补错误 tool 响应（已声明的调用必须应答）
4. 不产生结果的调用（`continue_thinking`/`respond_to_user`/`delegate_task`/`request_mode_change`/`ask_user_intent`/`create_supervisor`）**不写入** `tool_calls`，避免反向错误

**验证：** 4 个场景断言测试通过——每个 `tool` 消息的 id 都有前序 `assistant.tool_calls` 声明。

**同类排查：** 全仓库检索确认 `role="tool"` 消息仅此一处构造（`tests/test_model_clients.py` 为测试文件）。

---

## 10. 交互等待超时（ask_user_intent / 审批）

**现象：** AI 询问用户选择选项（ask_user_intent）或等待审批时，用户未及时操作会超时，AI 擅自继续。

**根因：** 交互等待使用了 `USER_REVIEW_TIMEOUT=120`（120 秒）超时：
- 安全门控审批（`tool_security_gate.py`）120s 后自动拒绝
- 模式切换审批（`model_runner._handle_mode_change_request`）120s 后按拒绝处理

**修复（改为无限等待，由用户显式决定）：**
- `tool_security_gate.py`：审批 `asyncio.wait_for(future, timeout=None)`，不再自动超时拒绝
- `model_runner._handle_mode_change_request`：同样 `timeout=None`
- `model_runner._wait_for_user_response`（ask_user_intent）：挂起全局计时器（`Suspension.suspend/resume`）+ 无限等待，等待期间不消耗思考/轮次计时

**验证：** 四处语法 OK；等待期间计时器暂停，用户可无限期操作选项/审批，直到显式点击。

---

## 19. 自动化替换留下的运行时 NameError + 同名索引重复（后端）

### 19.1 自动化替换未定义 helper → 运行时 NameError

**现象：** 后端启动后 WS 建会话报 `NameError: name '_utcnow' is not defined`（`session_repo.py:44`），前端无法开始对话。

**根因：** 修 `datetime.utcnow()` 弃用警告时用 python 脚本批量替换：
- `s.replace("from datetime import datetime, timedelta", "from datetime import datetime, timedelta, timezone\ndef _utcnow():...")` —— 但文件实际是 `from datetime import datetime`（无 `timedelta`），**第一处 replace 没匹配**，helper 没插入；
- 第二处 `s.replace("datetime.utcnow()", "_utcnow()")` **成功了**，把 7 处调用全部替换成 `_utcnow()`。

结果：调用被替换、定义没加，`py_compile` 通过（语法合法），运行时才炸。

**为什么测试没抓到：** ① NameError 是运行时错误，py_compile 不执行函数体；② 测试套件从未测真实 `SessionRepository`（`test_database` 用 MagicMock、`test_chat_gateway` 用内存版 mock），真实 `create_session/save_message` 路径零覆盖。

**修复：** 补 `_utcnow()` 定义；新增 `tests/test_session_repo.py`（5 用例覆盖 create/save/get/clear/delete 真实实现）。

### 19.2 同名索引重复 → 新建 DB 建表报 already exists

**现象：** 补测试用临时 SQLite 建表时报 `OperationalError: index ix_chat_sessions_last_active already exists`。

**根因：** `chat_models.py` 的 `ChatSession.last_active` 同时声明了 `index=True`（SQLAlchemy 自动生成 `ix_chat_sessions_last_active`）和 `__table_args__` 里显式 `Index("ix_chat_sessions_last_active", "last_active")`，**两个同名索引**。create_all 尝试建两个同名索引，第二次报 already exists。

**为什么真实 DB 没暴露：** 真实 `data/memory.db` 建过一次（表+索引已存在），后续 create_all 幂等跳过；只有**新建数据库**（新部署/测试临时库）才触发。

**修复：** 去掉 `last_active` 的 `index=True`，保留 `__table_args__` 显式 Index。

**同类排查：** AST 扫描所有 Base 模型，检查 `index=True` 列与 `__table_args__` Index 是否生成同名（自动索引名 `ix_<table>_<col>`）。

### 19.3 自动化脚本 replace 的教训（最重要）
- **任何批量文本替换（sed/python replace）改代码，替换后必须运行 + 在真实入口跑一遍**，不能只 py_compile。
- 替换目标的字符串要**精确匹配实际文件内容**（如 `from datetime import datetime` 与 `...timedelta` 不同），替换前先 grep 确认目标存在。
- **辅助函数（helper）替换调用点后，必须确认定义也加入**（成对检查）。
- **被 mock 掩盖的真实实现**：测 mock 路径前先确认被 mock 的核心逻辑有真实测试覆盖。

---

## 21. 主动搭话旁路绕过全局/会话开关（后端）+ 测试污染生产库

**现象：** 用户在设置页关闭「主动搭话」全局开关后，感知到屏幕高强度变化/定时任务到点时**仍然收到主动消息**；同时生产库 `data/memory.db` 积累大量 `test_xxx` 前缀的「你好」测试会话。

**根因（两层）：**
1. **旁路绕过三层闸门**：§18 只修了主路径 `ProactiveTrigger._get_enabled_outreach_sessions()`（全局开关 → 会话 enabled → 规则），但另两条触发源完全绕过：
   - `trigger_think.py`（感知触发思考）：只有自己的冷却+强度阈值，**不检查** `PROACTIVE_OUTREACH_ENABLED`，也不检查任何会话是否开启主动搭话——全局关闭后仍广播 proactive 消息；
   - `scheduled_tasks.py::_handle_chat`（定时任务）：只查任务 enabled，**不检查**全局总开关。
2. **测试写生产库**：`tests/unit/test_conversation_memory.py::test_session_context_accumulation` 直接用全局单例 `get_thinking_system()` + 随机 `test_{hex}` 会话 id → `system.start()` 落库到真实 `data/memory.db`，每次跑测试新增一个「你好」会话。

**修复：**
1. 新增模块级三层闸门函数 `modules/perception/trigger.py::outreach_trigger_allowed()`——全局开关关闭或无任何会话开启主动搭话时返回 False；`trigger_think._trigger` 入口接入（第 1、2 层），`scheduled_tasks._handle_chat` 接入全局开关检查。第 3 层（规则标准）仍由各触发源判定。
2. 测试改用临时 SQLite（monkeypatch `sqlite_path` + 独立 `StreamThinkingSystem` + 固定会话 id），绝不触碰生产库；已清理生产库残留 `test_*` 会话（备份 `data/memory.db.bak_pre_test_cleanup`）。

**验证：** 新增单测覆盖「全局关不触发」「无会话开启不触发」；端到端 7 项闸门断言全过；相关测试 71 项通过。

**经验：**
1. **修开关类 bug 要枚举所有触发源**——主路径修好但旁路（感知触发/定时任务）仍绕过 = 用户依旧觉得"开关是摆设"。§18 的三层闸门语义应作为所有主动消息的统一前置。
2. **测试必须隔离生产库**——凡调用 `get_thinking_system()`/`get_session_repo()` 全局单例的测试，都必须先 monkeypatch `sqlite_path` 指向临时库；用完随机 `test_` 前缀会话 id 的测试，本身就是污染源。

---

## 25. 工具型单次 LLM 调用被统一套上 agent 人设（后端）

**现象：** 记忆收纳/对话摘要/安全审查/良知反思等**纯工具型**单次 LLM 调用，模型实际收到的 system prompt 是无关的 agent 人设（总指挥/代码专家），与任务要求的身份（记忆分析专家/摘要助手/安全专家）冲突，且白白消耗 token（人格+工具表+安全规则+能力表+价值观全带上）。

**根因：** 三个模型客户端的 `generate()` 快捷方法**内部硬编码** system prompt：
- `LargeModelClient.generate` → `PromptRequest(tier="large", role="orchestrator")`
- `SmallModelClient.generate` → `tier="expert", role="code_writer"`
- `MediumModelClient.generate` → `tier="supervisor", role="code_supervisor"`

而调用方（`EventReducer._call_llm`、`context_slicer._summarize_chunk`、`tool_security_gate._check_llm_review`、`conscience.think/analyze_feedback`）只传了自足的任务 prompt，把 system prompt 留给客户端默认 → 每个工具任务都套上无关人设。

**主对话流是正确的（非统一）：** `ModelRunner._build_system_prompt_for_mode()` → `PromptComposer.build_system(PromptRequest(tier=self.tier, role=self.identity.role))`，走 `chat()`/`chat_stream()` 时 system 消息按角色区分（总指挥/代码主管/各专家），`roles.yaml` 人格各不相同，支持设置页自定义人设——主模型提示词构造正常，问题只在通用 `generate()` 快捷路径。

**修复：**
1. 三个客户端 `generate()` 新增 `system_prompt: str = None` 参数——**非空时覆盖**自动人设，默认行为不变（向后兼容）；
   > **后续变更（§27.8 前）：** `generate()` 的 `system_prompt` 已改为 **keyword-only 必填**——缺失直接
   > `TypeError`，不再注入任何默认 agent 人设（详见 docs 后续 §27.8 config/providers 接线）。
2. 各工具调用方传入专用精简 system prompt（各自"只做 X、不执行工具、只输出指定格式"）：
   - `event_reducer` → `MEMORY_REDUCE_SYSTEM_PROMPT`（记忆收纳）
   - `context_slicer` → 摘要助手专用（对话摘要）
   - `tool_security_gate` → 安全审查专用
   - `conscience` → `CONSCIENCE_SYSTEM_PROMPT`（良知/因果反馈）
3. `model_runner._generate` 传统回退路径：原来把 `system_prompt + prompt` 拼成一个 user 字符串传给 `generate()`，会再被自动注入一份 system（**双份 system prompt**）——改为 `system_prompt=system_prompt` 分开传，消除重复。

**为什么测试没抓到：**
- 主对话流本就走 `chat()`（自带 role prompt），`generate()` 的硬编码人设只在工具型调用路径生效，测试大多断言"被调用/返回内容"，**不检查传入的 system prompt 内容**；
- 新增参数后部分测试 mock 签名不兼容（`generate()` 不接收 `system_prompt`），被 `assert` 抓到并同步更新了 mock 签名（`test_toolgate.py`、`test_conscience.py`、`test_model_runner_core.py`）。

**验证：** 新增 `test_reduce_uses_dedicated_system_prompt`（`tests/unit/test_event_reducer.py`）断言记忆收纳调用带专用 system prompt；全量 unit 1095 通过。

**经验：**
1. **"模型客户端通用方法"的人设默认值，只应服务主对话流**——凡把通用 `generate()` 当"单次 LLM 调用"用的工具任务，都必须显式传自己的 system prompt，否则会继承无关 agent 人设（身份冲突 + 浪费 token）；
2. **给通用方法加新参数时，先 grep 所有 mock/调用方**——测试里的替身类 mock 常因签名不兼容直接抛 `unexpected keyword argument`；
3. **断言"输出内容质量"比断言"被调用"更能抓 prompt 类问题**——§20 同款经验。

---

## 39. mypy 修复引入回归：input_controller 幂等被破坏（后端）

**现象：** 全量测试 `test_input_controller.py::test_init_idempotent_and_force` 失败（`PyAutoGUIController` 被创建 2 次，期望 1 次）。全量 5833 passed / 1 failed。

**根因：** mypy 自动修复时，为消除 `_initialized` 的 var-annotated 错误，在 `hasattr(self, '_initialized')` **判断之前**加了 `self._initialized: bool = False`——赋值语句每次 `__init__` 都重置标志为 False，使幂等短路 `hasattr(...) and self._initialized and not force` 永远为假 → 二次 init 重新创建 controller。

**修复：** 改为**仅声明不赋值** `self._initialized: bool`（mypy 满足声明，运行时不再重置标志），幂等逻辑恢复。

**验证：** `test_input_controller.py` 24 passed；`mypy modules/output_system/input_controller.py` 0 errors。

**教训：** 自动类型修复（加声明/注解）可能引入**行为变化**——"加一行初始化"≠"加注解"。mypy 修复后必须跑**该文件的全部测试**（不只 mypy 0 errors）验证行为不变。

---

## 46. 模型 client 缓存旧配置 → 改 API URL/Key 不生效（后端/实时生效缺失）

**现象：** 用户在设置页修改模型 API URL/Key 后，**对话仍请求旧 URL**（404），但**心理活动却能用新配置**；只有重启后端才生效。

**根因：**
- `chat_light/ModelRunner.client`（model_runner.py:22-24）与 `ContextSlicer._get_client` 懒建一次并**缓存** `LargeModelClient` 实例
- 配置变更前已构建的 client 持有旧 `api_url/api_key`，之后一直复用 → 请求旧地址
- 心理活动每次 think **新建** `SmallModelClient`（continuous_thinker.py:77-80）→ 读最新 settings → 故能立即生效。两者行为不一致，暴露"缓存旧配置"问题
- 设置页保存（`update_config`）只更新**运行中内存 settings + 重建 model_factory 实例**，但 chat_light 的独立 client 缓存不在 factory 管辖内

**修复（配置指纹热重载，无需重启）：**
- 新增 `infra/model/config_fingerprint.py`：`model_config_fingerprint(tier)` 返回 URL/Key/名称/格式指纹；`close_client_session()` 关闭旧 aiohttp session
- `ModelRunner.client` / `ContextSlicer._get_client`：每次获取比对指纹，变化即重建 client
- `PromptComposer`：按 `base.yaml` mtime 热重载 identity（改提示词实时生效）

**同类排查（A 类：懒建缓存 client 不感知配置变更）：** 全仓库搜 `XxxModelClient()` 构造处，共 3 处——
1. `chat_light/ModelRunner.client` → 已修
2. `chat_light/ContextSlicer._get_client` → 已修
3. `desktop_pet/pet_engine._build_messages`（桌宠懒建缓存 LargeModelClient）→ **本轮同样加配置指纹重建**（pet_engine.py）

其余 client 均每次新建（心理活动/价值观演化 `SmallModelClient(...)` 按需 new）或由 `model_factory` 统一管理（`update_config` 会 `reload_from_config()` 重建），无此问题。

**验证：** 新增 `tests/unit/test_config_hot_reload.py`（指纹变化/复用、ModelRunner 重建、ContextSlicer 重建、base.yaml 热重载），相关 119 项测试通过。

**注意：** 设置页保存（`update_config` 更新内存）才实时生效；**直接编辑 `~/.cortex/settings.json` 文件**仍须重启（`_load_user_config` 只在进程启动时读一次）。

**教训：** 任何"懒建并缓存"的模型/外部 client，都必须感知配置变更（指纹/版本号），否则用户改配置会被缓存"吞掉"。判断标准：**同一类调用，有的每次新建、有的缓存复用**，就是这类 bug 的信号。

---

## 47. 纯对话人设无视编排 active 状态 → 强制套用已停用 agent 的人设（后端）

**现象：** 用户在编排页把自定义 agent `123` 停用、`orchestrator` 激活，但纯对话模式仍被强制套用 `123` 的自定义人设（"角色A"），改其它提示词也"不生效"。

**根因：** `chat_light/prompt_composer.build_system` 硬编码选择纯对话人设：
```python
custom_persona = get_persona("orchestrator")
if not custom_persona:
    for ca in get_custom_agents():
        if ca.get("tier") == "large" and ca.get("role"):
            custom_persona = get_persona(ca["role"])  # 取"第一个" large agent
            if custom_persona: break
```
**无视 `get_agent_active()`**——即使用户在编排里停用了某个 agent，只要它是"第一个 large 自定义 agent"就会被套用。用户"设置了哪个启动哪个"被硬编码优先级覆盖。

**修复（prompt_composer.py）：** 尊重编排的 active 状态，只从激活的 agent 选取：
1. `orchestrator` 激活且有自定义人设 → 优先
2. 否则 → **激活的** large-tier 自定义 agent 人设（`get_agent_active(role)` 过滤）
3. 否则 → 内置 `base.yaml` identity
（`persona_override` 高级修改仍最高，用户显式设置）

**验证：** 用户真实配置（`agent_active={'orchestrator': True, '123': False}`）下，`build_system` 从"角色A人设"变为内置 "你的名字是 Cortex。"。新增 3 项测试覆盖（停用不套用/激活优先/高级修改最高）。

**为什么之前没测到（测试盲区）：**
1. 现有测试 mock 了 `get_persona`/`get_custom_agents`/`get_system_override`，但**从不 mock `get_agent_active`** → 走真实默认值 `True`（settings.py `active_map.get(role, True)`）→ mock 的 agent 全部"默认激活"
2. 只有**正向测试**（"选谁优先"），**没有负向测试**（"谁应被排除"）——停用分支从未被覆盖
3. 用户真实场景（`active_map` 显式写 `False`）在测试里从未出现

**教训：** 测试不能只 mock 关心的方法而让其它逻辑吃真实默认值——默认值会掩盖分支 bug。用户真实操作产生的**状态分支**（停用/激活）必须有显式负向测试。

---

## 48. 同类问题排查：人设选择硬编码 / 配置缓存的系统性审计

**背景：** §47 修复"纯对话无视编排 active"后，按两类根因模式做全仓库排查。

**B 类（硬编码选人设 / 无视 active 状态）：**
| 位置 | 结论 |
|---|---|
| `core/model_runner.py:2451` ModelRunnerManager.start_runner | ✅ **已正确**——启动时检查 `get_agent_active()`，停用的 agent 拒绝启动 |
| `chat_light/prompt_composer.build_system` | ⚠️ 曾硬编码取第一个 large agent → **已修**（§47） |
| `identity.py` 合并自定义 agent 到 roles | ✅ 提供模板给 start_runner，调度层已拦停用；无强制套用 |
| `management/api.py` / `api/main.py` 读取展示 | ✅ 仅读状态，无选择逻辑 |

结论：B 类仅 chat_light 一处漏网，已修复；agent 模式调度从设计上就尊重 active。

**C 类（缓存导致配置改动不生效）：**
| 位置 | 结论 |
|---|---|
| `identity.get_identities()` 缓存 `_merged_identities` | ⚠️ 直接编辑 personas.yaml 不重载；但 `set_custom_agent`/`delete_custom_agent` 会 `_invalidate_identity_cache`——设置页操作实时，属"改文件需重启"同类（§46 已注明） |

**验证：** 相关 119 项测试通过。

---

## 57. thinking 模式 tool loop 报 400：assistant 消息未回传 reasoning_content（后端）

**现象：** 代码主管（code_supervisor）多轮工具调用时报错：
`400 - The reasoning_content in the thinking mode must be passed back to the API.`（provider: Console Go / DeepSeek thinking 模式）

**根因：** `modules/thinking/core/model_runner.py` 工具循环里，构造**声明 tool_calls 的 assistant 消息**（`messages.append(ChatMessage(role="assistant", content=None, tool_calls=all_result_calls))`）时**没有带上本轮的 `reasoning_content`**。thinking 模式下，assistant 消息一旦生成了 reasoning，后续请求必须原样回传（`_messages_to_api` 里 `if m.reasoning_content: msg["reasoning_content"]=...` 才能回传）。历史里该 assistant 消息 reasoning_content 为 None → 不回传 → 下一轮请求 400。

**修复（model_runner.py 1740 附近）：** 构造 tool_calls 的 assistant 消息时补 `reasoning_content=getattr(response.message, "reasoning_content", None)`——thinking 模式回传，非 thinking 模式为 None 不影响。

**验证：** model_runner / large_model_client 相关 314 项测试通过。

**教训：** thinking 模式（Reasoner）的多轮工具调用，**assistant 消息必须完整保留并回传 `reasoning_content`**（与 `tool_call_id`/`tool_calls` 同等重要）——任何"构造 assistant 消息"的地方都不能丢它，否则跨轮请求被 provider 拒绝。排查此类 400 的口诀：历史里 assistant 是否带 reasoning_content + `_messages_to_api` 是否回传。

---

## 59. delegate_task 的 wait_seconds 解析后未传递 —— 上级设置的下级超时恒走 300s 兜底（后端）

**现象：** 要求"让上一级大模型自主设置下级思考超时"后，`delegate_task` 工具已把 `wait_seconds` 标为 required，但运行时上级设置的值**从未生效**——下级 runner 的思考超时恒为 `DEFAULT_DELEGATE_THINK_TIMEOUT=300`。

**根因：** `model_runner._generate_with_tools` 的 delegate_calls 分发处只解析了 `role`/`task`，**没有把 `args["wait_seconds"]` 传入 `DelegationRequest`**（delegation_port.py:24 字段恒为 None）；`ProbeDelegationAdapter.delegate` 收到 None 后走兜底分支（且只在缺省时打 warning）。工具 schema 声明"必填+生效"，但解析到使用之间断链。

**修复（commit `16bd27e`）：** delegate_calls 分发处解析 `wait_seconds`（clamp 1-600）→ `DelegationRequest.wait_seconds` → probe_started `think_timeout` → `_handle_probe_started` → `start_runner(think_timeout)` → `runner.THINK_TIMEOUT`（委托场景覆盖，非委托用默认）。

**验证：** `test_delegate_think_timeout_passed` / `test_delegate_think_timeout_fallback`；相关 288+ 通过。

**教训：** "工具参数声明必填"不等于"参数真正生效"——**解析 → 传递 → 消费**三步必须连测。此模式（schema 声明与实际传递断链）是同类 bug 高发区，排查时搜工具名参数在解析处之后是否真的被使用。

---

## 60. think_once 外层 120s 嵌套覆盖 runner 的 THINK_TIMEOUT —— 上级设置的超时被内层吞掉（后端）

**现象：** 即使 §59 修好 `wait_seconds` 传递，上级给下级设的超时（如 300s）依然不生效——单次思考总是在 120s 就结束。

**根因：** 双层 `pausable_wait_for` 嵌套：`think_once` 外层 `timeout=SINGLE_THINK_TIMEOUT=120`（continuous_thinker.py:32）包裹整个 `think_fn`（即 `runner._generate`），而 runner 内部 `timeout=self.THINK_TIMEOUT`（300）。**外层 120s 先到 → 取消内层 → 上级设置的 300s 永远到不了**。嵌套超时取"较小者"，而非外层的"上限"。

**修复：** `think_once` 改用 `getattr(runner_ref, "THINK_TIMEOUT", None) or SINGLE_THINK_TIMEOUT`——上层超时与 runner 的超时对齐（上级委托设置的值真正生效）；超时重试分支的日志/error 同步用动态 `_timeout`。

**验证：** 相关 609 项通过。

**教训：** 多层 `wait_for`/超时包裹时，**外层超时若小于内层，内层永远不触发**（实际超时 = 最小嵌套值）。设置"可配置超时"时要检查所有包裹层，确保没有任何一层比被配置值更小。

---

## 61. 委托链不可重建 + 超时后思考上下文丢失（后端）

**现象（两个相关的结构性缺陷）：**
1. **委托链无法重建**：`delegation_id` ≡ `task_id`（沿整条链共享，无法区分层级）；`probe_id` 每次委托唯一但只存进程内 `_probe_map`，不随消息传播；黑板 `delegations` 生产代码从不写入（`write_delegation` 无生产调用者）；`_pending_delegations` 以 `task_id` 为 key 且每次 `continuous_think` 清空，多次委托互相覆盖。
2. **超时后思考丢失**：工具循环的 `messages` 只在内存，超时/中断终止后丢失；`_save_partial_result` 只保存部分输出**文本**（history_thoughts + streaming），不是可恢复的 messages 断点。

**根因：** 委托追踪用的唯一标识选择错误（复用 task_id）+ 委托链数据从未落库；断点快照从未设计（超时=从头重试）。

**修复（commit `16bd27e`）：**
- `Delegation` 扩展为链节点（`caller/return_to/parent_delegation_id/child_delegation_ids/origin_task_id/probe_id/target_model_id/progress/context_summary`），`delegation_id` 用每次委托唯一的 `probe_id`
- `delegate_task` 分发时 `_record_delegation_chain` 写入黑板并随黑板按 `(session_id, blackboard_id)` 落库；`probe_started` 传播 `parent_delegation_id`/`origin_task_id`；`thinking_result` 回写状态/结果
- 工具循环每轮 `_save_resume_context` 保存 messages 断点（runner + 黑板 + 落库）；`think_once` 超时重试 `_request_resume` → runner 从断点续思考（`_resume_context` 重建 messages + "从中断处继续"指令）
- `ChatMessage/ToolCall` 加 `to_dict/from_dict`；黑板加 `persist/load`

**验证：** 委托链黑板测试 7 项 + 断点续思考测试 3 项 + 相关 938+ 通过。

**教训：** ① 链路追踪必须用**每次实例唯一的 ID**（probe_id），不能复用沿链共享的标识（task_id）；② "超时后如何继续"必须在设计期决定（断点快照），否则超时=全部从头重来；③ 委托这类跨模型状态必须落库（随黑板按 session 持久化），进程内临时表无法支撑跨重启/跨层查询。

---

## 62. thinking_result 两处生产路径 delegation_id 语义不一致 —— RuntimeExpert 路径用 task_id，黑板委托链查不到（后端）

**现象：** on_demand 专家（RuntimeExpert，如 SecurityMonitor）完成后，上级 `query_delegation` 查不到该委托的结果/状态更新——委托链一直停在"已委托"。

**根因：** §61 修复后，`continuous_thinker._notify_return_target` 发送的 thinking_result 用 `delegation_id = runner._delegation_id`（**probe_id**，黑板委托链 key）；但 `model_runner._run_runtime_expert` 的唤醒路径（model_runner.py:367 附近）仍写 `"delegation_id": self._task_id`（**task_id**）。两处生产路径发送同一消息字段但语义不同 → 上级 `_wait_for_wakeup_event` 用 delegation_id 回写黑板 `get_delegation()` 永远找不到（task_id 不是黑板 key）→ RuntimeExpert 委托的状态/结果不落黑板。

**修复：** `_run_runtime_expert` 唤醒消息改为 `"delegation_id": self._delegation_id or self._task_id`（优先 probe_id），并补 `"task_id": self._task_id`（供上级 `_pending_delegations` 按 task_id 匹配）。与 `_notify_return_target` 路径对齐。

**验证：** `test_runtime_expert_thinking_result_uses_probe_id`（断言 delegation_id=probe_id 且 task_id 存在）；相关 145+ 通过。

**教训：** 同一消息字段**多生产路径**时，字段语义必须一致——修一处必须全仓排查所有发送点（grep `action: thinking_result` / `"delegation_id"`），否则部分链路（本例 RuntimeExpert）静默失效，且无报错、只在 query_delegation 时表现为"查不到"。

---

## 63. start_runner 委托节点记录覆盖 delegate 分发记录 —— 委托角色名被 identity.role 替换（后端）

**现象：** 端到端测试发现，`delegate_task` 分发时 `_record_delegation_chain` 已用**角色显示名**（如"代码实现专家"）记录委托；随后 manager 消费 probe_started → `start_runner` 又用 `identity.role`（如 `code_writer`）**覆盖了同一 probe_id 的委托节点** → 委托链里角色变成英文标识，`query_delegation` 展示的角色名丢失。

**根因：** 同一委托节点有两个记录点（delegate 分发 + probe 激活），后者用 `write_delegation` 无条件覆盖前者（同一 probe_id 为 key）。

**修复：** `start_runner` 记录委托节点前先检查 `bb.delegations` 是否已存在该 probe_id——存在则只补全 `target_model_id`（通过 `update_delegation_progress`），不覆盖 role/task/caller；仅 orchestrator 直启等缺失场景才 `write_delegation`。

**验证：** `tests/integration/test_thinking_e2e.py::test_delegation_chain_full_flow` 断言委托链 `role=代码实现专家, caller=large_primary_001, parent=probe_user_input, status=replied`；相关 79+ 通过。

**教训：** 同一业务实体（委托节点）有两个写入源时，必须明确"谁优先/谁补充"，否则后写者静默覆盖前者。

---

## 64. 断点续思考的"从中断处继续"指令在多次超时重试下累积（后端）

**现象：** 端到端审查发现，`think_once` 超时重试（MAX_THINK_RETRIES）时每次 resume 都往 messages 追加"从中断处继续" system 指令；断点快照更新后再次 resume 会再插一条 → 上下文膨胀 + 指令重复。

**根因：** resume 分支无条件 `messages.append(...)`，未检查断点里是否已含该指令。

**修复：** resume 分支先扫描断点 messages 是否已含"从中断处继续"（`has_resume_marker`），有则跳过插入。

**验证：** 相关 503+ 通过。

**教训：** 幂等性——任何"断点恢复/续跑"逻辑注入标记指令前，先检查是否已注入过；多次恢复同一断点不能重复追加。

---

## 67. 新增控制工具 read_context 误加入所有 tier —— expert 权限越权（后端/测试）

**现象：** `test_get_control_tools_expert` 失败：expert 的 control tools 多出 `read_context`。

**根因：** 在 `tool_permission_controller.get_control_tools` 里把 `READ_CONTEXT_TOOL` 放进了 `tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL, READ_CONTEXT_TOOL]` 基础工具列表——**所有 tier 共享**。但 `read_context` 是读取黑板记忆/委托上下文，expert 无记忆读取需求，不应暴露。

**修复：** 把 `READ_CONTEXT_TOOL` 移到 `if delegation_available and tier in ("large", "supervisor")` 委托组（与 query/resume_delegation 同级）。

**验证：** `test_get_control_tools_expert` 恢复；tool 相关 144 passed。

**教训：** 新增控制工具要按 tier 权限**显式归类**（基础/委托/大模型专属），不要无脑塞进共享基础列表；expert 只暴露最小必要工具。

---

## 70. 模型调用失败：'NoneType' object is not iterable —— SSE 流 delta.tool_calls 为 null（后端）

**现象：** 工具循环中模型调用报 `[模型调用失败: 'NoneType' object is not iterable]`，思考中断。

**根因：** `large_model_client._parse_openai_stream` 用 `for tc_delta in delta.get("tool_calls", [])` 迭代工具调用增量。DeepSeek reasoning 模式流式返回时，**思考阶段 `delta.tool_calls` 字段存在但值为 `null`**——`dict.get(key, [])` 在键存在但值为 None 时返回 **None**（不是默认 `[]`），`for ... in None` → `'NoneType' object is not iterable`。

**修复：** `for tc_delta in (delta.get("tool_calls") or [])` —— 用 `or []` 同时兜底"键缺失"与"值为 null"两种情况。

**验证：** 新增 `test_openai_stream_tool_calls_null_safe`（tool_calls:null 混入文本+后续工具调用）+ `test_openai_stream_tool_calls_key_missing`；large_model_stream 9 passed + 相关 274 passed。

**教训：** ① `dict.get(key, default)` 只在**键缺失**时返回默认值，**键存在但值为 None** 时返回 None——对可能为 null 的字段要用 `get(key) or default`；② SSE 流解析对 provider 的字段空值（null/缺省）都要容忍，DeepSeek reasoning 的 tool_calls 思考阶段为 null 是常态；③ 排查 "X is not iterable" 优先搜 `for x in dict.get(...)` 模式。

---

## 76. 长时间挂机后："Task was destroyed but it is pending!"（asyncio Event.wait 残留，后端）

**现象：** 挂机数小时后日志大量出现：
```
Asyncio error without exception: {'message': 'Task was destroyed but it is pending!',
  'task': <Task pending ... coro=<Event.wait() running at locks.py:213> wait_for=<Future pending cb=[Task.task_wakeup()]>>}
```
Task 编号（Task-3838/4022/4214/4386/4572/4755...）随对话逐次递增，即**每次对话**产生一个残留的 `Event.wait()` task，关闭事件循环时被销毁报错。

**根因：** `modules/thinking/multi_model_orchestrator.py` 等待大模型完成信号时用了
`asyncio.wait_for(asyncio.shield(done_event.wait()), timeout=POLL_INTERVAL)`。
`asyncio.shield` 会创建**独立的内部 task** 包裹 `done_event.wait()`，外层 `wait_for` 超时取消时，**shield 防止取消传播到内部 task**，于是内部 `Event.wait()` 继续挂起。每次对话都这样残留一个 pending task，挂机久了积累大量未完成 task，事件循环关闭时触发 "Task was destroyed but it is pending!"。

**修复：** 去掉 `asyncio.shield`，直接用 `asyncio.wait_for(done_event.wait(), timeout=POLL_INTERVAL)`。
`done_event.wait()` 只是等待信号，超时后下次循环重新 await 即可（`done_event` 已 set 则立即返回），无需 shield 保护。
（`model_runner.py:211/3270` 的 shield 用于关闭时等待长期后台任务正常结束，属正确用途，未改动。）

**验证：** `test_multi_model_orchestrator_ext.py::test_wait_large_no_shield_task_leak`（wait_for 超时后无残留 pending Event.wait task）+ `test_wait_large_done_event_set_returns`；orchestrator 相关 213 测试通过。

**教训：** `asyncio.shield` 是"延迟取消"而非"不取消"——被 shield 的内部 task 在事件循环关闭时仍可能 pending。对只需"等待信号、超时重试"的 `Event.wait()`，直接 `wait_for` 即可，不要 shield；shield 只应用于确实需要与调用方生命周期解耦的长期任务。

---

## 79. 委托角色名解析硬编码（delegation_port ROLE_TO_IDENTITY，后端，§77 同类隐患）

**现象：** `modules/thinking/core/delegation_port.py:135` 的 `ROLE_TO_IDENTITY` 是**硬编码** role 名 → (tier, identity_key) 映射表。新增 supervisor/expert 角色（含编排页自定义 agent）时若不同步更新此表，`delegate_task` 报 `未知委托角色`。

**根因：** 提示词侧的角色列表是**动态**的（`composer._build_supervisor_table()/_build_expert_table()` 从 roles.yaml 实时读取所有 tier=supervisor/expert 角色注入总指挥/主管；`identity.get_identities()` 还合并了编排页自定义 agent），但委托执行侧 `_resolve_role` 只能解析硬编码映射表。动态列 vs 硬编码解析不一致 → 新增角色可直接委托界面列出、却实际无法委托。已实测：`security_supervisor`、`data_expert` → `_resolve_role` 返回 `None`。

**附加隐患：** `_build_supervisor_table/_build_expert_table` 仅读 `roles.yaml`，不含编排页自定义 agent（合并进 identities 但不出现在委托引导列表）。

**修复：**
1. `_resolve_role` 加动态回退：`ROLE_TO_IDENTITY` 未命中时查 `get_identities()`（含编排页自定义 agent，直接/子串匹配）——新增角色无需改映射表即可委托。
2. `_build_supervisor_table/_build_expert_table` 改为合并自定义 agent（composer `_merged_roles`，以注入 loader 为基础 + `settings.get_custom_agents()`）。
3. **可委托角色表格按模型权限在 system prompt 注入**，不再走黑板：
   - 移除 `multi_model_orchestrator.py` 写入黑板的 `delegation_guidance`（含可用主管/可用专家表）
   - `_build_capability_table`：large → 主管表+专家表；supervisor → 专家表；expert → 无
   - 删除 `continuous_thinker._build_expert_context_section` **硬编码角色表**（含旧名 `test_writer`/`data_analyzer`/`memory_manager`/`emotion`，与 roles.yaml 不一致且漏 `ui_designer`/`customer`）
   - `delegate_task` 工具 role 描述同步改为「可委托的主管」「可委托的专家」

**验证：** delegation_port 新增 2 动态回退测试；composer 新增自定义 agent 合并测试；`test_build_expert_context_only_large` 改为 `test_expert_context_moved_to_system_prompt`（large 主管+专家表 / supervisor 专家表 / expert 无表）；相关 180 测试 + orchestrator 89 测试通过。

**教训：** "提示词动态列举的能力"必须与"执行侧可解析的集合"同源同生，否则出现 UI/提示词看得到、实际跑不通的脱节。可委托角色表格是"模型权限"信息，应随 system prompt 按 tier 注入，而非全局黑板广播（避免重复注入+越权可见）；硬编码角色名会随 roles.yaml 演化而失配。

---

## 80. "正在思考 Ns" 计时从会话连接起算，而非当前任务（后端 api_stream）

**现象：** 前端"正在思考 Xs"（`ThinkingStatusPanel`/`ThinkingIndicator`）的秒数持续偏大，同一会话内连续提问时，后一轮的秒数从上一轮累加，不是本轮真实耗时。

**根因：** `modules/thinking/api_stream.py` 的 `started_at` 只在 `start(session_id)`（WS 连接建立 / 会话创建）时设置一次；每轮用户消息走 `think()`，全程不复位 `started_at`。WS `status` 消息的 `elapsed_s = int(now - started_at)` 因此从"会话连接时刻"起算，而不是"本轮思考开始"。

**修复：** 在 `think()` 中 `_set_processing(session_id, True)` 之后、开始调度之前，于锁内重置 `self.sessions[session_id]["started_at"] = time.time()` —— 每轮任务开始重新计时。前端 `chat.elapsed` 完全消费后端推送的 `elapsed_s`，无本地计时器，无需改动。

**验证：** 新增 `test_api_stream_think_ext.py::test_think_resets_started_at_per_round`（旧 started_at 被重置、连续两轮各自刷新计时起点）；api_stream 全部 183 测试通过。

**教训：** "会话生命周期"计时与"任务/轮次生命周期"计时是两种语义，不能混用同一个时间戳。WS 状态推送的耗时类字段必须挂在"本轮处理"的起点上，否则 UI 展示的耗时失真。

---

## 83. 编排页关闭主管/专家后，System Prompt 仍拼接其上下文（能力表未过滤启用状态）

**现象：** 用户在编排页关闭某主管/专家（启用开关）后，system prompt 预览与运行时提示词仍包含"【可委托的主管/专家】"里该角色的条目；且被关闭的角色仍可被调度/委托启动。

**根因：** `config/prompts/composer.py::_merged_roles()`（能力表数据源）合并 roles.yaml + 编排页自定义 agent 时**不查询 `settings.get_agent_active()`**，导致关闭状态对 system prompt 组装完全无效。启停状态存于 `personas.yaml` 的 `agent_active`（默认 True），但能力表路径从未读取。运行时调度 `start_runner` 已有 `get_agent_active() is False → 拒绝启动` 检查（model_runner.py:3044），但提示词侧缺口使"已关闭的角色仍出现在可委托列表"，模型会误以为可委托。

**修复：** `config/prompts/composer.py::_merged_roles()` 合并前统一过滤 active=false 的角色（roles.yaml 内置 + 自定义 agent 均按 `settings.get_agent_active(key)` 过滤，缺失方法时兜底全 True 以兼容测试/外部调用）。身份表 `get_identities()` **不**过滤（保留调度可解析，委托时由 `start_runner` 的 active 检查拒绝启动，避免"未知身份模板"误报）。`settings.set_agent_active`/`deactivate_same_tier` 补调 `_invalidate_identity_cache()`，保证启停变更即时反映。另补 `delegation_port.delegate` 在委托前检查 `get_agent_active(identity_key)`，关闭的角色直接拒绝委托（与能力表过滤一致，避免"提示词看不到、委托却接受然后启动失败"的脱节）。

**验证：** 新增 `test_build_tables_filter_disabled_agents`（关闭的 code_supervisor/data_analyzer 不进入能力表）、`test_delegate_disabled_agent_rejected`（关闭角色委托被拒）；`test_build_supervisor_table_merges_custom_agents` 及 delegation 测试 mock `get_agent_active` 固定值隔离真实 yaml 状态；composer/identity/delegation_port/settings 相关 197 测试 + model_runner/orchestrator 360 测试通过。

**教训：** "启停开关"必须贯通所有消费角色集合的路径：提示词能力表（决定模型可见哪些可委托对象）与调度入口（决定是否真能启动）各管一段，遗漏任一路径就会产生"提示词看得到、实际启动被拒"或"关闭了仍出现在上下文"的脱节。配置变更（active 切换）后务必失效身份缓存，否则改动不生效。

---

## 84. 编排页切换总指挥后启动仍走 orchestrator，被启停开关拒绝（编排器硬编码身份）

**现象：** 用户在编排页启用自定义总指挥（tier=large，如 `123`）后，发消息触发思考时报 `[ModelRunnerManager] orchestrator 已被禁用（编排页激活开关），拒绝启动`，总指挥无法运行。编排页启停逻辑为"总指挥层只保留一个激活"（`deactivate_same_tier`：启用 `123` 时把 `orchestrator` 置 false），但启动侧却始终用 `orchestrator` 身份。

**根因：** `multi_model_orchestrator` 直接激活大模型时**硬编码** `identity_key: "orchestrator"`（probe_started 消息）与 `match_skill(role="orchestrator")`，未读取编排页选中的激活 large 角色。§83 给 `start_runner` 加了 `get_agent_active() is False → 拒绝启动` 后，这个硬编码被"放大"：编排器仍请求 orchestrator，但 orchestrator 已因同层互斥被停用 → 启动被拒，即使自定义总指挥 `123` 处于激活态。对话侧（chat_light）的人设选择早已支持"orchestrator 优先，否则激活的自定义 large"（prompt_composer），编排器未同步。

**修复：** `multi_model_orchestrator` 新增模块级 `resolve_active_large_role()`：orchestrator 激活时返回 `orchestrator`，否则返回第一个 `tier=large` 且 `get_agent_active=True` 的自定义 agent，异常时兜底 orchestrator。probe_started 的 `identity_key` 与 `match_skill` 的 `role` 均改用它。

**验证：** 新增 `tests/unit/test_resolve_active_large_role.py` 6 用例（orchestrator 优先 / 自定义 large 跟随 / 多个自定义取激活首个 / supervisor 不参与 / 异常兜底）；编排器 78 测试通过。真实环境 `~/.cortex/personas.yaml`（orchestrator=false, 123=true）下 `resolve_active_large_role()` 返回 `123`。

**教训：** 硬编码默认角色名（orchestrator）是"身份单点"假设，一旦编排页允许同层多选互斥（总指挥层唯一激活），任何绕过该抽象、直接写死角色 key 的路径都会与启停状态脱节。启动类路径应统一经过"解析当前激活角色"的同一辅助函数，而不是分散硬编码；§79（delegation ROLE_TO_IDENTITY 硬编码）、§83（能力表不过滤 active）属同一类"角色集合来源不一致"问题。

---

## 92. _session_dialog_history 并发覆盖 —— 同 session 两次请求互相覆盖（后端）

**现象：** 同一会话快速发送两条消息，第二条的 dialog history 覆盖第一条的，导致第一条的前端展开面板显示错误的历史。

**根因：** `multi_model_orchestrator.py:32-33` 的 `_session_dialog_history` 是模块级 dict，无锁。两条并发请求对同一 session_id 写入不同内容。

**影响范围：** 并发对话时前端展开面板内容错乱。

**修复建议：** 改用 per-session 锁或 per-request 独立存储。

---

## 93. TurnContext fragments 按 source 覆盖 —— 同源不同内容丢失（后端）

**现象：** 两个不同模块（如记忆检索和因果分析）产出的 context fragment 使用相同 `source` 名（如 `"memory"`），后者覆盖前者。

**根因：** `pool.py:97`：`self.fragments[fragment.source] = fragment`，无去重/合并逻辑。

**影响范围：** 黑板共享记忆可能丢失部分来源的上下文。

**修复建议：** 改为 list（同 source 多 fragment 共存）或用 `source:sub_key` 复合 key。

---

## 95. 思考超时按“整轮墙钟”计时 —— 工具执行吃掉预算、界面长时间无回复（后端）

**现象：** 一个开放式任务（例如让 AI 改自己的代码）连续 10 多分钟没有任何回复，界面只有思考过程；日志里出现两次 `单次思考超时（>300.0s）`，随后会话被停止，数据库里该会话 0 条 assistant 消息。

**根因：**
1. `ContinuousThinker.think_once` / `ModelRunner._generate` 用 `pausable_wait_for(..., timeout=THINK_TIMEOUT)` 给**整轮**（模型调用 + 工具执行 + 多轮工具循环）固定 300s。`exec_command` 跑一次测试就 2 分钟，两轮工具循环必然超时。
2. 模型 HTTP 卡住（首 token 迟迟不返回）时没有独立的静默超时，只能干等 300/600s。
3. 工具执行没有独立超时：`asyncio.to_thread(mcp.execute, ...)` 无上限。
4. 取消时读取部分输出存在竞态：`remove_runner_manager()` 先 pop manager，`api_stream` 再去 `_runner_managers` 取黑板 → 取不到，中断残稿既不落库也不显示。

**修复：**
1. 新增 `utils/activity.py::ActivityClock`：AI 每次产出（流式 token / 模型响应 / 工具调用）都 `touch()`，`pausable_wait_for(..., activity=clock)` 语义变为**静默超时**（有产出就重新计时）。
2. 工具执行期间 `clock.pause()/resume()`，工具耗时不计入 AI 静默时间；等待审批（`Suspension`）同样冻结计时。
3. 工具调用独立计时：`TOOL_CALL_TIMEOUT`（默认 600s），超时只结束本次工具调用、把错误回传给模型，不影响整轮思考预算。
4. 思考轮次保留整体墙钟上限 `MODEL_THINK_MAX_WALL`（默认 3600s，0=不限），防止无限工具循环。
5. 超时重试时向黑板写一条可见提示（“模型静默超过 Ns，正在从断点继续”）；重试耗尽时写入可见的 `result_summary`，不再出现“只有过程、没有回复”。
6. `api_stream` 取消分支新增快照兜底：manager 已被移除时，从 `blackboard_snapshots` 捞回 `[思考被中断 ...]` 残稿并落库/下发。

---

## 97. plan 模式把只读委派也拦掉 —— 关键词为空仍返回拦截（后端）

**现象：** plan 模式下 large 模型调用 `delegate_task(role=代码实现专家, task=计算 1+1)`，日志只有 `[直通委托] 失败: ... error=plan 模式拦截`，**没有** `[ModelRunner] plan 模式拦截写操作委派` 那一行；委派未发出、委托链未记录，runner 直接以「大模型无 result_summary」结束，用户什么也拿不到。

**根因：** `modules/thinking/core/model_runner.py` 的 `_generate_with_tools` 中，plan 模式检查写成：
```python
if exec_mode == "plan":
    matched = [kw for kw in DELEGATE_WRITE_KEYWORDS if kw in task_lower]
    if matched:
        logger.warning(...)              # 只有命中关键词才打日志
    if self._thinker:
        self._thinker.record_delegation(... error="plan 模式拦截")
    return "[安全门控拦截] ..."          # ← return 落在 if matched 之外
```
`return` 与「记录拦截」缩在 `if exec_mode == "plan"` 里而不是 `if matched` 里 → **只要处于 plan 模式，任何委派（含纯只读/计算任务）都被当成写操作拦下**，`', '.join(matched)` 还会输出空关键词。

**修复：** 把记录拦截 + `return` 一起移入 `if matched:` 分支。plan 模式仍拦截含写操作关键词（`写入`/`部署`/`git push` 等）的委派，只读任务照常下发。

**测试：**
- 新增 `tests/unit/test_model_runner_ext2.py::test_generate_with_tools_delegate_plan_allows_readonly_task`：plan 模式 + `task=计算 1+1` → 不出现 `安全门控拦截`、`adapter.delegate` 被 await 一次、状态进入 `waiting_delegation`。
- 既有 `test_generate_with_tools_delegate_plan_blocked`（task 含写关键词）继续断言被拦截。
- `tests/integration/test_thinking_e2e.py` 的 2 例（`test_delegation_chain_full_flow`、`test_query_delegation_e2e`）此前在 HEAD 上即失败（并发 fixture 把 `EXECUTION_MODE` 固定为 plan 后暴露此 bug），修复后 5 例全通。

**回归：** `test_model_runner_ext2.py` 178 例、`tests/integration` 432 例全部通过。
