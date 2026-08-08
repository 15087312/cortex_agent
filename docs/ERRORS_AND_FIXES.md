# 错误原因与修复记录

> 本文件记录开发过程中遇到的报错及其根因、修复方式，便于排查同类问题。

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

## 3. WebSocket 发送无重试 → 消息静默丢失（前端）

**现象：** 发送消息后界面一直"正在思考"（加载态），但后台其实已回复；按"停止"无效。

**根因：** Vue `stores/chat.js` 的 `sendMessage` 在 WS 未就绪/首次连接失败时只发送一次，`send()` 返回 `false` 后**静默丢弃**消息（纯 JS 版有最多 8s 重试，Vue 迁移时漏掉）：
- input 没到后端 → 永远加载
- stop 也发不出去 → "按停止还在继续"

**修复：**
- `_ensureConnected()` + `_sendWithRetry()`（最多等 8s 重发）
- 连接看门狗：处理中断开 → 复位加载态 + toast 提示
- `ack busy`（后端正忙会丢弃 input）→ 2.5s 后自动重发 `retryLastInput()`
- stop 后设 `stopped` 标志，忽略后端补发的 `message` 事件

**同类修复：** `approve`/`answerIntent`（审批/提问响应）同样改用 `_sendWithRetry`；`stores/cortex.js` 的 `_sendWithRetry` 增加重连尝试。

---

## 4. 虚拟滚动 → 新消息不渲染（前端）

**现象：** AI 回复已到达但页面不显示，仍停留在加载态。

**根因：** `vue-virtual-scroller` 的 `DynamicScroller` 只渲染标记为 `active` 的项，新追加消息若未被判定 active 则**不渲染**；`scrollToItem` 对动态高度内容不可靠。

**修复：** Chat / CortexChat 两页改为普通滚动容器（`scrollTop = scrollHeight`），每条消息必定渲染。
**补充：** 为避免超长会话一次性渲染全部 DOM，加入分批渲染——最多同时渲染 50 条，超出时顶部「加载更早消息」按钮每次再加载 50 条（滚动锚定不跳位）；自动滚动仅在用户接近底部时触发。

---

## 5. 停止按钮只改 UI 不通知后端（前端）

**现象：** 按停止后界面停止，但后端继续生成，随后补发的回复又出现在界面。

**根因：**
- `stores/cortex.js` 的 `stopGeneration()` 只设本地标志，**未发送 `{type:'stop'}`** 给后端
- chat store 的 stop 已发 stop，但需抑制后端补发的尾部 `message`

**修复：**
- cortex `stopGeneration()` 补发 `{type:'stop'}`
- chat store 用 `stopped` 标志抑制停止后的尾部回复

---

## 6. 原生 confirm/prompt 阻塞 Qt WebEngine（前端）

**现象：** 桌面客户端（Qt WebEngine）中删除/确认弹窗"点了没反应"。

**根因：** 原生 `confirm()`/`prompt()` 会阻塞整个 JS 引擎，对话框未关闭时所有点击无响应。

**修复：** 新建 `useDialog` composable + `DialogHost.vue`（页内弹层 + Promise，永不阻塞），替换全站原生调用（Chat/CortexChat/Memory/Settings）。

---

## 7. 界面使用 emoji 违背 CSS 图标约定

**现象/根因：** 项目约定使用纯 CSS/Lucide SVG 图标（clip-path 头像、Lucide stroke 图标），迁移 Vue 时误用了约 60 处 emoji。

**修复：** 新建 `Icon.vue`（Lucide SVG 路径，`currentColor` 可着色），全站替换 emoji；深色代码块主题用 `[data-theme="dark"]` 作用域 CSS。

---

## 8. 新建会话无效（前端）

**现象：** 点击「新建会话」后界面清空，但继续对话仍写入**旧会话**。

**根因：** Vue `stores/chat.js` 的 `init()` 只清空消息列表，**未清空 `session.sessionId`、未断开旧 WebSocket**：
- 旧 WS 仍连接着旧会话，`sendMessage` 检测到 `connected` 为真就不重连
- 新消息发到旧会话 → 等于没新建

纯 JS 版 `newSession()` 正确做了 `_sessionId=null` + `WS.disconnect()`。

**修复：** `init()` 增加：
1. `ws.wsClient.disconnect()`（断开旧连接，防止自动重连旧会话）
2. `session.sessionId = null`（下次发送时懒创建新会话）
3. `session.currentTitle = '新会话'`、清空 `_lastInput`

**验证：** 新建会话后发送 → 创建新 `session_id` 并加入会话列表，与旧会话隔离。

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


## 12. 前端裸 fetch 路径在 8765 静态代理下全部 404（前端）

**现象：** 编排图页显示"暂无该层 Agent"、编排页/技能页/会话设置数据不加载；浏览器 Network 面板对应请求 404。

**报错（浏览器 Console）：**
```
TypeError: Failed to fetch dynamically imported module: http://localhost:8765/assets/xxx.js
```
（此为另一类：构建后旧 chunk 失效，见 12.2）

**根因：** 前端 SPA 由 `frontend/server.py`（端口 8765）静态服务，它**只代理 `/api/*` 到后端 8080 并去掉 `/api` 前缀**。新增页面代码直接写裸路径：
```js
fetch('/management/orchestration')   // ❌ 8765 不代理 /management → 404
fetch('/config/persona/...')          // ❌ 同样 404
fetch('/stream/session/...')          // ❌ 同样 404
```
而走 `endpoints.*` 封装（api.js `BASE='/api'`）或 `fetch('/api/...')` 才经代理正常。

实测：
- `8765/management/orchestration` → **404**
- `8765/api/management/orchestration` → **200**（代理去前缀到 8080）
- `8080/api/management/orchestration` → 401（8080 无 `/api` 路由，**不能直接用 8080 的 /api**）

**修复：** 前端所有裸 `fetch('/management|/config|/stream|/tools/...')` 统一加 `/api/` 前缀（8765 代理去前缀到 8080）。涉及 8 个文件：Graph.vue、Orchestration.vue、Skills.vue、SessionSettings.vue、ScheduledTasks.vue、Dashboard.vue、Settings.vue、ChatMessage.vue。

**关键约定（写前端代码务必遵守）：**
1. **所有后端 API 请求必须用 `/api/` 前缀**（或走 `endpoints.*` 封装）——8765/vite 代理都会去掉 `/api` 转到 8080；裸路径在 8765 直接 404
2. **WebSocket 例外**：`ws/client.js` 直连 `:8080/stream/ws/{sid}`（8765 无 WS 转发），不要改成 8765
3. **`/audio`、`/pet` 资源保留裸路径**（分别由后端静态/桌宠使用，不受代理影响）
4. **不可用 8080 直接拼 `/api/`**——后端路由无 `/api` 前缀，会 401

**验证：** 修复后各页面数据加载正常；`grep -rnE "fetch\\('/(?!api/)"` 无残留裸 fetch。

### 12.1 同类：懒加载 chunk 404（构建更新后旧页面白屏）

**现象：** `npm run build` 后，已打开的旧页面导航到懒加载路由报：
```
Error: Unable to preload CSS for /assets/Graph-xxx.css
TypeError: Failed to fetch dynamically imported module: /assets/Graph-xxx.js
```

**根因：** Vite 构建产物带内容 hash，重建后旧 hash 的 chunk 被删除；浏览器旧页面内存中的模块图仍引用旧 hash，导航触发动态 import 即 404。

**修复：**
- `frontend/src/router.js` 加 `router.onError`：识别 `Failed to fetch dynamically imported module` / `Unable to preload CSS` 时自动 `location.reload()` 拉最新 index.html + chunk
- `frontend/server.py` 对 `/assets/*` 设 `Cache-Control: public, max-age=31536000, immutable`（hash 文件不变内容不变）；`index.html` 保持 `no-cache`（每次拿最新，引用最新 chunk 列表）

**经验：** 改完后端/前端记得重新 `npm run build`；已打开旧页面的用户需刷新一次；构建更新后懒加载失败会自动刷新恢复。


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


## 14. 会话图谱 return_to 节点 tier 为空 → 节点/边被布局丢弃（前端/后端）

**现象：** 会话图谱里某些 Agent 节点不显示、相关呼唤/回复边丢失；偶发把未知层级节点误标为"实现专家"（橙色）。

**根因（两层）：**
1. **后端** `session_graph.py` 的 `record` 创建被回复方（`return_to`）节点时 `tier` 默认空字符串——若该节点从未作为发言者出现过，tier 一直为空；
2. **前端** `Graph.vue` 布局只按 4 个已知 tier（user/large/supervisor/expert）分列，空 tier 节点**不落入任何列 → 没有坐标** → 节点不渲染、以它为端点的边被 `if (!f || !t) continue` 跳过；同时 `tierOf('')` 回退到 `expert` 误标橙色。

**修复：**
- 后端：按发言者层级**推断上级层级**（`expert→supervisor→large→user`）填充 return_to 节点 tier；
- 前端：`layout` 增加"未知"列兜底（灰色 `bot` 图标），`tierOf` 未知值返回灰色"未知"而非回退到 expert。

**验证：** node 模拟 5 场景（完整链 / 只有总指挥 / 5 专家 / 未知 tier / 空图）——无 NaN 坐标、无同列重叠、无越界、unknown 节点/边不丢失。

**同类排查：** 前端所有"按枚举分列/映射颜色"的逻辑，对**未知枚举值必须显式兜底**，且兜底值不应是某个真实业务枚举（避免误标）。

**经验：** 数据驱动渲染的经典坑——**字段缺失/空值 vs 前端枚举不匹配**，前端枚举必须允许未知值并给中性样式；后端造节点时尽量把展示字段（tier/label）补全。


## 15. 补充历史经验（此前未记录）

### 15.1 限流额度被高频轮询耗尽 → 正常操作 429（后端）

**现象：** 健康检查/桌宠轮询后，用户正常打开页面也报"请求频率超限"，日志刷 `限流触发: 127.0.0.1 (GET /xxx)`。

**根因：** `api/main.py` 限流中间件每 IP 每分钟 100 次，白名单只有 `/stream/pet/move`；`/health`（每秒多次）+ 桌宠轮询（`last-reply`/`state`）把额度耗尽，之后所有请求 429。

**修复：** ① 高频只读轮询端点（`/health`、`/stream/pet/*`、`/stream/status`、`/stream/sessions`、`/config`、`/dashboard`、`/metrics`）加入限流白名单；② 本地回环（127.0.0.1/::1）上限放宽到 1000/分钟，公网仍 100。

**同类排查：** 加新端点时区分"高频轮询（只读状态）"与"业务操作"——前者应进限流白名单/日志忽略名单，否则会互相挤占额度。

### 15.2 macOS 截图 could not create image from display（后端）

**现象：** 终端反复打印 `could not create image from display`（每次截图一次）。

**根因（两层）：**
1. macOS 上 `PIL ImageGrab.grab()` 走 X11，主动打印该错误到 fd（`except` 拦不住）；
2. 回退的 `screencapture` 子进程错误输出未捕获，直接继承到父终端。

**修复：** `utils/screen_capture.py` macOS 跳过 ImageGrab 直接用 `screencapture`，`subprocess.run(..., capture_output=True)` 捕获子进程输出；`_try_screencapture` 用 `try/finally` 删除临时文件（曾泄漏 3.8 万个临时 png）。

**同类排查：** 所有 `subprocess.run` 未捕获 stdout/stderr 的调用——子进程错误会泄漏到父进程终端。

### 15.3 时段判断用字符串比较 → 跨午夜永不触发（后端）

**现象：** 主动搭话 `time_windows` 配 `22:00-02:00`（跨午夜）永不触发。

**根因：** `_check_time_windows` 用 `start <= cur <= end` **字符串比较**——`"23:38" <= "02:00"` 恒 False。

**修复：** 改为分钟数数值比较，`end < start` 视为跨午夜（`cur >= s or cur <= e`）。

**同类排查：** 任何 `"HH:MM"` 时间比较都应转分钟数再比，字符串比较在跨天/整点边界必然出错。

### 15.4 模板误用未定义变量（前端）

**现象：** 打开因果图/编排图报 `Cannot read properties of undefined (reading 'type'/'color')` → ErrorBoundary 显示"页面加载失败"。

**根因：** 模板里 `v-for="node in ..."` 但表达式写 `{{ n.type }}` / `n.color`（变量名笔误 `n` vs `node`）——Vue 编译不报未定义变量，运行时渲染才崩。

**修复：** `n` → `node`（Causal.vue / Graph.vue 共 7 处）。

**同类排查：** 写脚本提取模板根标识符对照 script 定义 + v-for 局部变量，唯一真 bug 模式是"v-for 变量笔误"；`:style`/`:class` 对象键、箭头函数参数、`(v,k)` 第二变量均为误报。

**经验：** Vue 模板未定义变量在构建期不报错、运行期才崩——新增/重构页面后**务必打开每个页面实测**，或跑模板变量静态检查。


## 16. 黑板共享记忆：角色别名不匹配（后端）

> 归档自 `docs/MEMORY_INJECTION.md` 第十节

**现象：** 多模型协作中总指挥看不到 `【协作上下文】`、`【当前委托状态】`、`【当前任务进度记事本】`、`【历史输出】` 等黑板共享片段——专家产出没完整回到总指挥，潜在协作断裂。

**根因：** `TurnContext.view(role)` 按 `role in target_roles` 过滤，而 `continuous_thinker._build_prompt` 里 `role = getattr(self, '_role', 'orchestrator')` 恒为 `'orchestrator'`；但这些片段的 `target_roles=("large",)` → 大模型（总指挥）匹配不上。

**修复**（`modules/thinking/context/pool.py` `view()`）：把 `"large"` 与 `"orchestrator"` 视为**同一角色（总指挥）的两种写法**——查看角色是二者之一时可见含任一别名的片段；supervisor/expert 仍精确匹配。

**验证：** `view('orchestrator')` 返回全部片段；`view('supervisor')` 只返回系统指令+历史记忆；`view('expert')` 只返回历史记忆。

**同类排查：** 全局检索角色字符串常量（`"large"`/`"orchestrator"`/`"supervisor"` 等）是否被硬编码成两种写法且未归一——配置项里同一角色多种叫法极易导致匹配失败。


## 17. 前端重构期已修复 Bug 汇总（旧 JS 版 → Vue 迁移经验）

> 归档自 `docs/frontend-refactor-plan.md` 第五节

### 17.1 `ws.js` 重复函数定义 → 连接失败无人知晓（复制粘贴）
`_scheduleRetry` 定义两次，第二个覆盖第一个（丢失 `_connectReject` 通知）——连接失败调用者永远不知道。
**修复：** 删除第二个定义，保留带通知的版本。

### 17.2 `components.js` 字符串模板 XSS
`UI.e()` 只转义 HTML 实体，onclick 属性是 JS 上下文（需转义 `'` `"` `\` 换行）。修复用 `this.jsStr()` 替代 `this.e()`。

### 17.3 `chat.js` 重复 `_renderMsgShell` → 流式光标丢失（复制粘贴）
第二个定义覆盖第一个（丢 `<div class="streaming-cursor">▊</div>`）。
**修复：** 删除第二个定义。

### 17.4 `ws.js` WebSocket URL 硬编码
`ws://localhost:8080` 硬编码 → 非 localhost 环境连不上。修复从 `window.location.hostname` 动态取。

### 17.5 `app.js` localStorage 无 try/catch
隐私模式下 `localStorage.getItem/setItem` 抛 `SecurityError` 未捕获。修复全站 try/catch 包裹。

### 17.6 CSS 变量缺失
`theme.css`/`layout.css` 引用未声明的 CSS 变量。修复在 `:root` 补全定义。

### 17.7 已知未修复问题（旧 JS 版，Vue 重构后已解决）
API Key 明存 localStorage → 改内存；静默 catch → 各调用处加错误处理；无错误边界 → `<ErrorBoundary>`；删除会话无 API → 补 `DELETE`；版本号硬编码 → 构建注入；重复工具函数 → 统一 `utils/`。

### 17.8 Bug 三大共因（最值得记住的经验）
1. **复制粘贴错误**（3 个 bug）：重复函数/模板互相覆盖。手写 JS 无类型检查、无编译期检测——**Vue SFC + ESLint 可防**。
2. **全局可变状态**（2 个 bug）：散落 `this.xxx` 竞态覆盖——**Vue 响应式 ref + Pinia store 可追踪**。
3. **字符串作为代码**（2 个 bug）：onclick 字符串注入 XSS、字节替换脆弱——**Vue 模板编译 + Vite 构建根治**。


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




