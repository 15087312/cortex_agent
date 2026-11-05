# 错误原因与修复记录 · 后端 · 配置持久化与安全

[← 返回总索引](./README.md) | [English](./03-backend-config-security.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 14 节：§13、§18、§28、§29、§56、§77、§78、§86、§87、§88、§89、§90、§96、§102。

---
## 13. settings 双重 @property → 多 Agent 思考崩溃（后端）

**报错：**
```
TypeError: 'property' object is not callable
```

**现象：** agent 模式对话时 `continuous_thinker think_once` 反复异常，总指挥永远无法委托主管/专家（主管/专家实例不创建）。

**根因：** `config/settings.py` 的 `is_delegation_available` 前有**两个 `@property` 叠加**：
```python
@property          # ← 多余：装饰的是下一行的 @property 对象

@property
def is_delegation_available(self) -> bool:
    return True
```
外层 `@property` 把内层的 **property 对象**当作 getter → 访问 `settings.is_delegation_available` 时调用 getter（property 对象）→ `'property' object is not callable`。任何读取该配置的代码（`model_runner` 工具注入、委托判定）全部崩溃。

**修复：** 删除多余的 `@property`（只保留装饰方法的那一个）。

**验证：** `settings.is_delegation_available` 返回 `True`；agent 模式总指挥完整思考、专家实例正常创建。

**同类排查：** `grep -nB1 "def " config/settings.py | grep @property` 确认无其他重复装饰器。

**经验：** 连续两行相同装饰器叠加会产生极难定位的 `'property' object is not callable`——它既不是真正的属性访问也不是方法调用。凡是 `@xxx` 装饰器，检查其下一行是否紧贴一个函数（空行/另一装饰器都会出错）。

---

## 18. 设置全局开关是摆设（后端/前端）

**现象：** 设置页「主动搭话 → 启用主动搭话」全局开关开了/关了，触发行为完全不变——用户质疑"真能生效吗"。

**根因：** 三层配置各管一段，但接入不完整：
1. **全局总开关 `PROACTIVE_OUTREACH_ENABLED`**——只在 `setup.py:59` 被**读取用于前端状态显示**，`trigger._get_enabled_outreach_sessions()` 判定时**根本没检查** → 开关改不动任何行为；
2. **会话级配置**（`metadata.outreach`）——唯一被 trigger 判定的 → 只有会话 enabled 才触发；
3. **无全局默认规则**——会话没配置就不触发，无兜底。

**修复（配置体系重构，优先级：全局总开关 > 会话配置 > 全局默认）：**
- `trigger._get_enabled_outreach_sessions()` 入口先检查 `PROACTIVE_OUTREACH_ENABLED`（关闭全停，返回空）
- 新增 `PROACTIVE_OUTREACH_DEFAULT`（JSON 全局默认规则）——**会话未配置（`not cfg`）时回退用全局默认**（含 `enabled` 判断）
- 新增方法 `_get_global_default_rules()`（解析 JSON，容错返回 `{}`）
- `PROACTIVE_OUTREACH_DEFAULT` 加入 `_MODIFIABLE_FIELDS`（否则 PUT /config 报 FORBIDDEN）
- 前端：设置页「主动搭话」= 全局总开关 + 全局默认规则编辑（保存到 DEFAULT）+ 会话规则管理（Outreach 页合并为 compact 子组件）；侧栏移除主动搭话独立入口

**验证：** 全局默认 `{enabled:true, idle:...}` 保存读回正常；14 个未配置会话全部命中全局默认；全局开关关闭时 `_get_enabled_outreach_sessions` 返回空。

**经验（写前端/后端配置时务必遵守）：**
1. **任何"开关/设置"必须真正接入消费方**——只读显示不接判定 = 摆设。加设置时先查谁消费它、判定路径是否引用。
2. **前端可改配置项必须进 `_MODIFIABLE_FIELDS`**——否则 PUT /config/{key} 报 FORBIDDEN（403），用户点了没反应。
3. **三层配置（全局/默认/覆盖）明确优先级**，消费方用同一套解析（`会话配置 if cfg else 全局默认`），避免"配了不生效"困惑。
4. 设置入口与功能页合并（如主动搭话页并入设置）后，侧栏入口要同步移除，避免两处入口、一处失效。

---

## 28. 无鉴权 WebSocket + 0.0.0.0 绑定 + execution_mode 注入 + 审批 future 断开泄漏（安全审计高危）

**现象：** 全量安全审计发现三个高危/中危问题：

1. **WS 完全无鉴权（HIGH）**：HTTP 中间件不覆盖 WebSocket（`api/main.py` 注释明示），任何能连到 8080 端口的客户端可自由连接 `/stream/ws/{任意 session_id}`；用相同 session_id 连接会**顶掉原连接并接管消息流**（会话劫持）。
2. **默认绑定 0.0.0.0（HIGH）**：`main.py`/`start_all.py`/`autostart_launcher.py`/`Dockerfile` 全部监听所有网卡——局域网内任何设备可直连。
3. **WS 消息可注入全局执行模式（HIGH）**：`api_stream.websocket_chat` 在 input 消息里带 `"execution_mode":"yolo"` 就直接 `object.__setattr__(settings, "EXECUTION_MODE", ...)` **全局生效**，绕过 PUT /config 的认证——攻击者可远程以 yolo 模式（跳过确认）执行工具（含 exec_command）。
4. **审批 future 断开泄漏 + 全局计时冻结（MEDIUM）**：用户关闭前端后，`ToolSecurityGate._pending_reviews` / `ModelRunner._pending_user_responses` 的 future 永远无人 resolve，`asyncio.wait_for(future, timeout=None)` 永久挂起，且 `Suspension` 被 suspend 后永不 resume → **整个系统计时冻结**。
5. **API Key 落 localStorage（MEDIUM）**：`frontend/src/api.js` 把密钥持久化到 localStorage（§17.7 已记录过此模式，Vue 重构时又带回）。

**修复：**
1. **WS 握手鉴权**：新增 `api_stream._ws_auth_ok()`——`SIMPLE_API_KEY` 未配置（开发模式）放行；已配置则校验 `X-API-Key` header 或 `?api_key=` 查询参数（hmac 常量时间比较，header/query 任一匹配即放行）。`chat_gateway.websocket_chat`（实际挂载的入口）与 `api_stream.websocket_chat`（纵深防御）均在 accept 前校验，失败 `close(4401)`。CLI（aiohttp）带 header；浏览器 WebSocket 无法设 header，前端 `ws/client.js` 追加 `?api_key=`。
2. **默认绑定 127.0.0.1**：`main.py`/`start_all.py`/`autostart_launcher.py` 改为 `SERVER_HOST` env（默认 `127.0.0.1`）；docker-compose 显式 `SERVER_HOST=0.0.0.0`（容器内需监听 eth0 供 docker-proxy 转发，宿主机已用 `127.0.0.1:8080:8080` 限制外部访问）。
3. **移除 WS execution_mode 注入**：`api_stream.websocket_chat` 不再读消息里的 `execution_mode`；CLI `send_input` 不再附带该字段。模式切换统一走 `PUT /config/EXECUTION_MODE`（CLI `_set_execution_mode` 已按此实现，带认证）。
4. **审批/交互会话关联 + 断开清理**：`ToolSecurityGate` 新增 `_pending_review_sessions`（request_id→session_id）+ `reject_session_reviews(session_id)`；`check/_check_impl/_check_high_risk/_check_user_review` 全链透传 `session_id`，`ModelRunner` 的 gate.check 与 `_handle_mode_change_request` 关联会话；新增 `ModelRunner.reject_session_user_responses(session_id)` 清理 `ask_user_intent` 待交互。`api_stream.websocket_chat` 的 finally 在断开时调用两者批量拒绝——只影响断开会话，不误伤其他会话，`Suspension` 恢复、思考流程继续。
5. **API Key 仅内存**：`frontend/src/api.js` 移除 localStorage/sessionStorage 读写；刷新后由 `autoDetectApiKey()` 从 `/config/api-key` 自动拉取（生产环境仅回环返回明文）。
6. **会话接管防护**：`ConnectionManager.connect` 先关闭同 session 旧连接再注册新连接（code 4000）。

**验证：** 新增测试 15 项——`test_ws_auth.py`（6 项 _ws_auth_ok 单元）、`test_chat_gateway.py` 追加 3 项（无 key 拒绝/错 key 拒绝/带 key 通过）、`test_toolgate.py` 追加 `TestRejectSessionReviews`（3 项：只拒目标会话/check 透传 session 标签/未知会话 noop）、`test_reject_session_interactions.py`（3 项：清理/跳过已完成/cancelled 提示）；全量 unit 1378 + 前端 5 项 + `vite build` 全绿。

**经验：**
1. **HTTP 认证中间件不覆盖 WebSocket**——凡有 WS 端点必须单独做握手鉴权，且鉴权必须在 `accept()` 之前（否则已握手成功无法拒绝）；
2. **"开关/模式"禁止经无鉴权通道注入**——WS 消息里的配置写入都是旁路，统一收敛到带认证的 PUT /config；
3. **无限等待（timeout=None）必须配断开清理**——§10 把审批改为无限等待后，前端断开就成永久挂起 + Suspension 全局冻结；交互类 future 必须登记归属（session_id）并在断开时批量拒绝；
4. **本机服务默认绑定 127.0.0.1**，需要局域网暴露时显式开 SERVER_HOST=0.0.0.0（Docker 容器内除外，需配宿主端口映射）；
5. **敏感凭据只进内存**——localStorage 的 XSS 窃取面太大，自动拉取 + 内存持有即可满足免录入体验。

### 27.20 后端测试补全（覆盖率 57% → 60%）+ 修复 1 个真实 bug

**补测（新增 ~130 项，全量 1721 → 1859 全绿）：**
- **0% 模块**：`test_ocr_utils.py`（OCR 引擎三分支/单例）、`infra.model.interface` re-export
- **agent 工具**：value_tools（12）、web_fetch（SSRF/URL/方法/超时 9）、open_app（touchpoint/subprocess 7）、
  plan_tools/security_tools/file_history_tools（14）、dev_tools（AST/依赖 13）、ai_tools（代码校验/创建/删除 18）、
  exec_command 执行路径（7）、memory_matcher（语义/关键词/时间/重要性 15）
- **模型客户端**：`test_model_client_chat.py`（chat 成功/工具/非200/超时重试、generate reasoning 回退/anthropic 9）
- **本地视觉**：image_analyzer `_analyze_qwen_vl`/`_analyze_mlx_vlm`/UI 降级

**修复 1 个真实 bug：** `image_analyzer.detect_ui_elements` 的 else 分支调用**不存在的方法
`_detect_ui_mock`**（视觉后端不可用时 AttributeError）→ 补降级方法返回空结果。测试覆盖。

**稳定性：** pytest 全局 timeout 10s → 20s（全量负载下多个极快测试偶发 timeout flaky，单独跑 0.9s 通过）。
性能测试 `TestPerformance` 标记 `slow`（依赖机器负载，默认排除）。

**覆盖率：** 57% → 60%（miss -335 行）。剩余低覆盖集中在需要真实环境/重型 mock 的模块：
api_stream（35%）、model_runner（45%）、image_analyzer（23%→含本地视觉）、speech_recognizer/hardware_input/
mouse_keyboard/perception_tools/cdp_scanner（外部硬件依赖）。高危需确认项见
`docs/BUGS_REQUIRING_CONFIRMATION.md`（本轮无新增高危行为变更，发现的均为普通 bug 已直接修复）。

### 27.21 后端测试补全（续）：覆盖率 60% → 61%

**新增测试（全量 1859 → 1895 全绿）：**
- `test_speech_recognizer.py`（6 项）——whisper 识别/降级 mock/置信度/文件/Base64（whisper 缺失抛 ImportError 行为）
- `test_hardware_controller.py`（14 项）——PyAutoGUI 包装：移动/点击/滚动/拖拽/位置/按键/输入/热键/截图（含截图禁用分支）
- `test_mouse_external.py`（9 项）——external_api SSRF/GET/POST/超时 + mouse_keyboard 移动/点击/键盘
- `test_perception_cdp.py`（7 项）——cdp_scanner DOM 解析（按钮/占位符/深度/文本跳过）+ transcribe_audio + understand_screen 降级

**稳定性修正：** 全量 flaky 截图测试改为 mock `utils.screen_capture`（真实 pyautogui 全量负载下返回 None）。

**覆盖率：** 60% → 61%（累计 57% → 61%，miss 从 9601 → 9090，-511 行）。
**累计新增**：1721 → 1895 项（+174）。剩余低覆盖：`api_stream`（35%）、`model_runner`（45%）、
`ai_tools`/`tools` 部分、`output_system`、`management` 深路径——需重型 mock 或真实环境。

### 27.22 后端测试补全（续）：api_stream 鉴权/身份/记忆提取

**新增测试（全量 1895 → 1901 全绿，`test_api_stream_core.py` 追加 6 项）：**
- `_ws_auth_ok`（开发模式放行 / header 正确错误 / query 正确）
- `_resolve_identity_name`（缓存 / 去 _001 后缀 / 空）
- `_post_task_extraction`（mock EventReducer 提取成功 / 对话过短不提取）

**覆盖率：** 61%（api_stream 35% → 39%）。累计 57% → 61%，全量 1721 → 1901（+180 项）。
剩余低覆盖：`model_runner`（45%，1590 行）、`ai_tools`/`output_system`/`management` 深路径、tools 剩余
分支——需重型 mock（完整运行链路）或真实环境。

### 27.23 后端测试补全（续）：model_runner/output_system/ai_tools

**新增测试（全量 1901 → 1922 全绿）：**
- `test_model_runner_methods.py` 追加 5 项——`_supports_native_tool_chat`（静态判断）、
  `_build_time_context`（时间/对象）、`_push_reasoning`（思考推送/空不推）
- `test_output_system.py`（7 项）——text/speech（TTS 成功/禁用）/mouse/键盘端点（mock 底层）
- `test_ai_tools.py` 追加 4 项——edit_tool（成功/缺名/不存在）+ `_add/_remove_persisted`
- `test_api_stream_core.py` 追加 6 项（§27.22）

**稳定性修正：** pytest 下 `_visible_tool_whitelist` 依赖注入 mock 不稳定（模块属性遮蔽），
且该方法仅转发给 ToolPermissionController（已单独测）→ 移除该测试。

**覆盖率：** 61%（累计 57% → 61%，miss -619 行）。全量 1721 → 1922（+201 项）。
剩余低覆盖集中在**需真实运行链路/重型 mock**：`model_runner` 剩余方法（_generate_with_tools 完整循环、
_run_task/_think_loop）、`api_stream` 完整 WS 流、`output_system` 深路径、真实硬件/屏幕/语音分支。

### 27.24 后端测试补全（续）：工具循环分支 + 白名单命令

**新增测试（全量 1922 → 1932 全绿）：**
- `test_model_runner.py` 追加 3 项——`_generate_with_tools` 专家直出（无工具+文本）、
  主管纯文本拒绝重试（chat_calls>=3 验证注入）、缺参拦截（calc 未执行）
- `test_exec_command_safety.py` 追加 6 项——`run_command` 白名单（空/不在白名单/白名单内/
  shlex 解析失败）、`run_script`（空/极端拦截/成功）

**覆盖率：** 61%（累计 miss -645 行，57% → 61%）。全量 1721 → 1932（+211 项）。
剩余低覆盖需**完整运行链路/真实环境**：`model_runner` 完整循环（_run_task/_think_loop）、
`api_stream` WS 流式、真实硬件/屏幕/语音分支——纯单测提升有限。

### 27.25 后端测试补全（续）：_generate 重试/回退 + system prompt 构建

**新增测试（全量 1932 → 1936 全绿，`test_model_runner.py` 追加 4 项）：**
- `_generate` 前端不可达 → 跳过 LLM（返回"[系统] 前端连接已断开"）
- `_generate` 原生工具 client → 走 `_generate_with_tools`
- `_generate` 无原生工具 client → 传统 `generate()` 回退
- `_build_system_prompt_for_mode` 对话历史前置（mock PromptComposer）

**覆盖率：** 61%（累计 miss -700+ 行）。全量 1721 → 1936（+215 项）。
剩余低覆盖：`model_runner._run_task/_think_loop`（完整编排循环）、`api_stream` WS 流式、
真实硬件/屏幕/语音分支——纯单测边际收益低。

### 27.26 后端测试补全（续）：_run_task 生命周期 + RuntimeExpert

**新增测试（全量 1936 → 1940 全绿，`test_model_runner.py` 追加 4 项）：**
- `_run_task` 正常思考 → finally 清理 manager 注册（runners/count）
- `_run_task` 异常 → status=error + 详情
- `_run_task` 取消 → status=completed
- `_run_runtime_expert` on_demand → 实例化 + run_cli_mode

**覆盖率：** 61%（累计 miss -730+ 行）。全量 1721 → 1940（+219 项）。
剩余低覆盖：`_think_loop` 完整编排循环、`api_stream` WS 流式、真实硬件/屏幕/语音——需完整运行链路 mock。

---

## 29. config/settings.py 缺失 `import sys` → NameError 隐患（后端）

**现象：** `~/.cortex/settings.json` 不存在时，`_ensure_user_config`/`_apply_user_config` 等 10 处使用 `sys.stderr`，但模块未 `import sys` → 首次运行触发 `NameError`。

**根因：** `settings.py` 顶部只 `import os`，多处 `print(..., file=sys.stderr)` 依赖隐式 sys。单元测试因 autouse fixture 注入 sys 才未暴露。

**修复：** `import sys` 加入模块顶部（`config/settings.py`）。

**发现方式：** 覆盖率补测 `config/settings.py`（61%→100%）时，测试用注入 sys 绕过，暴露了真实缺口。

---

## 56. 人设存储两套来源 + 反馈闭环节点集合未按会话隔离（后端）

**现象：** 用户在编排/设置页改人设，对话 system prompt 生效但**心理活动（内心独白）不生效**——心理活动仍用 roles.yaml 内置模板。另：多会话并发时心理活动的反馈闭环（analyze_feedback 调整因果图置信度）可能用到**其它会话**的因果节点。

**根因（两个）：**
1. **人设两套来源**：对话用 `personas.yaml`（get_persona 用户自定义，core 模式 composer.py:139 也 get_persona 覆盖），心理活动用 `config/prompts/roles.yaml` 内置（conscience._resolve_role 直接读 roles.yaml）——改一处，另一处不变。
2. **`conscience._last_analyzed_node_ids` 是全局单例可变状态**（§49 同类）：多会话并发 think 时互相覆盖，analyze_feedback（fire-and-forget 异步）可能用错会话的节点调整因果图置信度。

**修复：**
- 新增统一人设入口 `settings.get_role_persona(role)`：用户自定义 `personas.yaml personas[role]` → 自定义 agent（personality+风格+擅长）→ `roles.yaml` 内置 → 空。`conscience._build_role_context` 改用它——**对话与心理活动同源，改一套人设同时生效**（心理活动仍只取人设文本，不含工具段）
- `_compose_persona` 兼容 expertise 为逗号字符串（拆列表）
- 反馈闭环节点集合按 session 隔离：think 结束时把本轮节点**快照**到 `_pending_feedback_by_session[session_id]`，`analyze_feedback(owner_id=session_id)` 用对应会话快照，用完清理；直接调用（测试）回退 `_last_analyzed_node_ids`（向后兼容）

**内存安全：**
- `Conscience` 新增 `clear_session()` / `clear_all_dialogs()`（会话删除时释放对话缓存，防无界增长）
- 新增 `tests/leak/test_leak_conscience_dialogs.py`（每会话有界 20 条 + 会话可清理 + 大量会话可整体清空）和 `tests/leak/test_leak_client_rebuild.py`（client 配置指纹重建时旧 aiohttp session 被 close，防泄漏）

**验证：** 统一人设防御测试 8 项 + 反馈池隔离测试 + leak 12 项 + 相关 329 项通过。

**教训：** ① "改一处人设，两处生效"必须统一读取入口（用户自定义优先 + 内置回退），而不是让各消费方各读各的源；② 全局单例的可变状态（哪怕是瞬态的"本轮分析节点"）在多会话并发下也会串——**快照按 session 存**是标准解法；③ 新增有界/可清理的状态（对话缓存）要配套清理方法 + 泄漏测试。

---

## 77. 授权设置页冗余的 API Key 手动输入（前端，安全暴露反模式）

**现象：** "授权设置" tab 有一个"API 密钥"手动输入框（`X-API-Key`，由后端 `SIMPLE_API_KEY` 控制），可保存/清除密钥。

**根因：** 设计冗余。前端启动时 `autoDetectApiKey()` 已从 `/config/api-key` 自动拉取密钥（开发/测试直接返回明文；生产仅回环客户端返回明文，其余只返回 `configured` 状态）。手动输入框只对"生产 + 非回环客户端"这一边缘场景有意义，其余场景完全无用；且它把安全密钥暴露成前端入口，属安全反模式。

**修复：** 移除授权设置 tab 的 API 密钥输入区（`keyInput`/`saveKey`/`clearKey`），保留 `autoDetectApiKey`/请求头携带逻辑。授权设置 tab 仅保留运行时配置表。

**验证：** Settings.spec.js 删除 3 个依赖该 UI 的测试、改写 1 个 tab 测试；50 个前端测试通过；重新 `npm run build` 后 dist 中无 `key-input`/`输入 X-API-Key` 残留。

**教训：** 凡"自动检测/自动恢复已覆盖"的配置，前端不要再留手动入口——既冗余又扩大攻击面。检查同类：见 §78、§79。

---

## 78. 授权设置 tab 运行时配置表明文展示模型 API Key（前端，§77 同类）

**现象：** 授权设置 tab 的"运行时配置"表把 `configStore.config` 全部可改配置项逐行渲染 `String(v)`，包括 `LARGE_MODEL_API_KEY`/`MEDIUM_MODEL_API_KEY`/`SMALL_MODEL_API_KEY`/`PERCEPTION_VOICE_API_KEY`/`OUTPUT_TTS_API_KEY`/`VISION_API_KEY` 等密钥明文。

**根因：** `get_config`（api/main.py:764）返回 `_MODIFIABLE_CONFIG_KEYS` 内全部配置项（含模型/语音/视觉密钥），前端运行时配置表对非对象值一律 `String(v)` 明文输出。这些密钥均有专属 password 式配置区（主模型配置等），运行表明文展示属冗余暴露（窥屏/日志泄露风险）。

**状态：** 已确认同类问题，待修复（前端对密钥字段 masked 显示 `••••`，编辑走 password 输入）。

**教训：** 允许展示的配置清单（`_MODIFIABLE_FIELDS`）本身含密钥类字段；任何"全量配置渲染"界面都必须按字段名屏蔽密钥值（KEY/TOKEN/SECRET/PASSWORD 模式）。

---

## 86. personas.yaml 内容丢失 / 系统提示词覆盖重启后消失（配置持久化风险）

**现象：** `~/.cortex/personas.yaml` 之前存储的 `agent_active`（orchestrator=false, 123=true）、自定义 agent 信息全部丢失，文件只剩 `model_params: {}`。用户报告"完整系统提示词覆盖每次重启都会消失"。

**根因调查：**

已验证的正确路径（不丢数据）：
- `set_system_override` / `set_persona` / `set_model_params` 等 setter 均采用 read-modify-write 模式（`_load_personas_yaml` 读全量 → 修改 → `_save_personas_yaml` 写回），**不会丢其他键**。
- 后端 live server 实测：`PUT /config/persona/orchestrator` 写入 → `~/.cortex/personas.yaml` 落盘 → `GET /management/orchestration` 读回 → `POST /management/orchestration/preview` 组装 prompt 均正确使用覆盖。
- `composer.build_system` 读 `get_system_override(req.role)` → 返回覆盖文本（优先于所有组装逻辑）。
- 前端 `saveOverride` 正确发送 `{value, system_override}`，`savePersona` 不包含 `system_override` 字段 → 后端 `if body.system_override is not None` 跳过 → 不会误清。
- 启动/关闭路径（`api/main.py` lifespan、`bootstrap.py`、`frontend/main.py` 子进程）均无写 personas.yaml 的代码。
- `config/prompts/composer.py`、`modules/` 下无直接读写 personas.yaml 的代码。

**未解答的疑问：**
1. `personas.yaml` 之前的内容（agent_active、custom agents）是如何丢失的？所有 setter 都不会删除非目标键。
2. 可能的触发场景：两个进程同时写（read-modify-write 竞态）、打包桌面 app 使用不同的 HOME/配置路径、用户手动删除/覆盖文件、某次 bug 导致 `_load_personas_yaml` 返回 `{}`（文件不存在或读取失败）后 set_* 写入空数据。
3. 当前 `personas.yaml` 内容为 `model_params: {}`，是本次调查期间 round-trip 测试的 cleanup 副产物（测试前已只剩此内容）。

**影响范围：** 所有通过 `set_*` 写入 personas.yaml 的功能（人设、系统覆盖、工具权限、模型参数、agent 启停、自定义 agent）。

**当前状态：** 后端写入/读取路径验证正确，覆盖持久化无问题。`personas.yaml` 早期数据丢失的根因待进一步排查（可能需要增加文件写入日志或原子写监控）。

---

## 87. personas.yaml 并发写丢失更新 —— 无进程级锁（后端）

**现象：** 两个并发 API 请求（如同时更新 persona 和 tools）修改 personas.yaml，第二个写入覆盖第一个的更改。极端情况下导致 §86 的"重启后数据丢失"。

**根因：** 所有 `set_*` 方法（`set_persona`/`set_system_override`/`set_role_tools`/`set_model_params`/`set_agent_active`/`set_custom_agent` 等）都采用 read-modify-write 模式（`_load_personas_yaml` → 修改 → `_save_personas_yaml`），但**无进程级锁**。多个并发请求同时读取同一版本文件，各自修改不同键后写回——后者覆盖前者。原子 `os.replace` 只防半写，不防 lost-update。

**影响范围：** 14+ 个 `set_*` 方法共用同一个 `_save_personas_yaml`，任何两个并发调用都会丢数据。

**修复建议：** 加文件级锁（如 `fcntl.flock` 或 `threading.Lock`），或改为单次写入合并（一个请求内多个 `set_*` 合并为一次 read-modify-write）。

**同类排查：** `save_user_config`（settings.json）存在完全相同的问题。

---

## 88. settings.json 并发写丢失更新（后端）

**现象：** Settings 页面"保存模型表单"遍历多个 key 逐个 PUT，每个 PUT 触发 `save_user_config` 读-改-写 settings.json。并发两个浏览器 tab 或快速操作时，第二个写入覆盖第一个。

**根因：** `save_user_config`（`config/settings.py:902`）读 settings.json → 添加 key → 写回，无锁。原子 `os.replace` 防半写不防 lost-update。`_load_user_config`（line 884）无重试逻辑（不像 `_load_personas_yaml` 有 retry），JSON 损坏时静默回退到 .env 默认值。

**影响范围：** 所有通过 Settings 页面修改的配置项（模型参数、API Key、开关等）。

**修复建议：** 加文件级锁或改用乐观锁（写入前校验文件未被修改）。

---

## 89. delete_custom_agent 四次独立 read-modify-write 级联丢数据（后端）

**现象：** 删除自定义 agent 时，`delete_custom_agent` 先删除 agent，然后**四次独立**调用 `set_persona`/`set_system_override`/`set_role_tools`/`set_model_params` 清理该 agent 的配置。中间任何一次写入被并发请求覆盖，都会留下孤立数据；进程崩溃则部分清理不完整。

**根因：** `api/main.py:950-962`：五次独立的 read-modify-write，每次之间其他请求可插入修改。

**影响范围：** 删除自定义 agent 后配置不一致（残留 persona/override/tools/params）。

**修复建议：** 合并为单次 read-modify-write（一次读取、一次性删除所有键、一次写回）。

---

## 90. _save_personas_yaml 吞掉异常 —— 调用方假设成功（后端）

**现象：** `_save_personas_yaml` 写入失败时只 print warning，不抛异常。所有 14+ 个 `set_*` 调用方都假设写入成功，API 返回 success toast。用户看到"已保存"但实际没落盘，重启后恢复旧值。

**根因：** `config/settings.py:589-604`：`except Exception` 捕获所有错误（磁盘满、权限错、文件锁冲突），只打印到 stderr。API endpoint 返回 `{"success": True}`。

**影响范围：** 所有 personas.yaml 写入操作，静默丢失。

**修复建议：** `_save_personas_yaml` 应返回 success/failure（同 `save_user_config` 的 bool 模式），调用方根据返回值决定是否告知用户。

---

## 96. 配置文件跨进程写竞态 —— 仅线程锁挡不住多进程（后端）

**现象：** `~/.cortex/personas.yaml` 偶发内容丢失（用户自定义人设写完后过一段时间变回旧值/被清空）；`~/.cortex/settings.json` 重启后部分字段回到默认。单进程内反复操作无法复现。

**根因：**
1. `config/settings.py` 第 26-27 行只有**进程内**锁：`_PERSONAS_YAML_LOCK = threading.Lock()`、`_SETTINGS_JSON_LOCK = threading.Lock()`。`threading.Lock` 只对同一进程内的线程互斥，**没有跨进程语义**。
2. `_modify_personas_yaml()`（约 770 行）与 `save_user_config()`（约 1132 行）都是 read-modify-write：读文件 → 改 → 原子替换。两个**进程**同时进入时，各自的进程内锁互不可见，后写者用自己读到的旧快照覆盖前写者的结果 → 丢失更新（§86/§87/§88 记载的现象）。
3. `frontend/main.py:535` 用 `subprocess.Popen` 以 uvicorn 启动后端，前端与后端、或多次启动的后端实例可能**并发写同一份 `~/.cortex` 配置**，构成真实的跨进程竞态场景。

**修复：** 复用项目已有的跨进程锁工具 `utils/faiss_lock.py::faiss_file_lock`（进程内可重入 RLock + 跨进程 `fcntl.flock`，无 fcntl 平台自动退化为线程锁；锁文件 = 数据路径 + `.lock`，不打开/截断数据文件本身）。
1. `_modify_personas_yaml`：在 `with _PERSONAS_YAML_LOCK:` 内**再嵌套** `with faiss_file_lock(self._personas_yaml_path):`，锁文件由工具自动追加 `.lock`。保留原线程锁，嵌套顺序为外层线程锁、内层文件锁。
2. `save_user_config`：同样在 `_SETTINGS_JSON_LOCK` 内嵌套 `faiss_file_lock(str(self._USER_CONFIG_PATH))`。
3. 导入用**函数内延迟导入**（`from utils.faiss_lock import faiss_file_lock`），避免模块级循环依赖；导入失败时 `try/except` 降级为 `contextlib.nullcontext()`（仅线程锁）并打 `logger.debug`，**绝不因加锁不可用而中断写入或崩溃**。
4. `_load_personas_yaml` / `_save_personas_yaml` 的既有行为与签名不变。

**测试：** 新增 `tests/unit/test_settings_file_lock.py`（5 例，全部通过）：
- `_modify_personas_yaml` 写入期间锁文件存在、写后释放（`_depth==0`、`_fd is None`）；
- mutator 内部重入同一路径锁不死锁；
- `utils.faiss_lock` 导入失败时降级为仅线程锁、写入成功且不产生锁文件；
- `save_user_config` 同路径验证锁持有/降级。
测试全部用 `tmp_path` + monkeypatch 重定向 `_personas_yaml_path` / `_USER_CONFIG_PATH`，不触碰真实 `~/.cortex`。

**回归：** `tests/unit/test_settings_ext.py` + `test_faiss_lock.py` + 新增用例 共 109 例全部通过。

---

## 102. settings.json 解析失败时静默当作 `{}` 写回 —— 一次保存抹掉全部配置（后端，§88 残留）

**现象：** 配置文件被手工改坏（或少见的中途断电留下不完整 JSON）后，用户在 Settings 页
随便保存一项，`settings.json` 里**其它所有已持久化的 key（模型参数、API Key、开关）全部消失**，
只留下这次保存的那几个。

**根因：** `save_user_config` 读文件时 `except Exception: data = {}`，随后在同一个锁内
把这几个 key 写回并 `os.replace` —— 锁修好了「并发覆盖」（§96），却没修「把读失败当成空文件」
这条**单个请求就能触发**的整文件清空路径。`_load_personas_yaml` 早就有
「读不懂就抛 RuntimeError，阻止基于空字典写入」的保护（§86 排查时加的），
`save_user_config` 漏了同样的保护。

**修复：** 解析失败或内容不是 JSON 对象时抛 `RuntimeError`（消息里带文件路径），
**保持原文件一个字节不动**；外层 `except` 打印并向上抛，API 层已按失败返回
（`tests/unit/test_api_main.py` 覆盖）。文件不存在（首次运行）仍然按空配置正常写入。

**测试：** `tests/unit/test_settings_ext.py::test_save_user_config_corrupt_existing`
改为断言抛错且文件内容原样保留（原断言的是"静默写回"的旧行为）；
新增 `test_save_user_config_non_object_json`（`[1, 2]` 这类合法但非对象的 JSON 同样拒绝）。

**回归：** `tests/unit/test_settings_ext.py` + `test_settings_file_lock.py` 104 例通过。


### 102.1 落盘失败仍先改了内存/环境变量（补）

**现象：** 磁盘满、权限不足或 `~/.cortex` 被占用时保存配置，接口返回 500，但设置页重新
读取却显示「已生效」；重启后又回到旧值——用户无法判断到底存没存上。

**根因：** `PUT /config/{key}` 先 `object.__setattr__(settings, ...)`（`CORTEX_MODE`
还会写 `os.environ`），再调 `save_user_config()`。§96 把失败改成向上抛之后，写失败只让
接口变成 500，**内存里已经是新值**；`save_user_config` 的 `-> bool` 返回值也早已恒为
`True`（§96/§102 之后不再返回 False），`if not ...` 分支成了死代码。

**修复：**
1. `save_user_config(keys, values=None)` 新增 `values` 参数：落盘时直接写待保存的新值，
   而不是读 `self` 上的当前值；返回类型改为 `None`（失败一律抛异常，不返回布尔）。
2. `PUT /config/{key}` 改为**先落盘、再应用**：`save_user_config([actual],
   values={actual: validated})` 成功后才 `object.__setattr__` / 写 `CORTEX_MODE`；
   写失败直接 500，内存与环境变量保持原值。
3. 删除恒假的 `if not save_user_config(...)` 告警分支。
4. 主模型重建、系统级开关等副作用仍只在落盘成功后执行。

**测试：**
- `test_update_config_persists_before_apply`：`save_user_config` 收到的 `values` 是新值，
  且调用时内存仍是旧值。
- `test_update_config_internal_error_500` /
  `test_update_config_cortex_mode_persist_failure_keeps_env`：落盘抛错 → 500，
  内存值与 `CORTEX_MODE` 均不变。
- `test_save_user_config_values_override_without_mutating_memory`：`values=` 写入文件但
  不改内存。
- 原「持久化失败仅告警、仍返回 200」用例删除（该行为已不存在）。
