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

> 归档自旧版前端重构计划（文档已移除）第五节

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
4. 设置入口与功能页合并（如主动搭话页并入设置）后，侧栏入口要同步移除，避免两处入口、一处失效。## 21. 主动搭话旁路绕过全局/会话开关（后端）+ 测试污染生产库

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


## 20. 测试假对象与真实模型字段脱节（后端）

**现象：** 感知触发思考（trigger_think）每次触发传给 LLM 的 prompt 完全相同——`"检测到环境高强度变化（perception:）。请自然简短地关心/提醒用户…"`，`perception:` 后面始终是空的。

**根因：** `trigger_think._trigger` 用 `getattr(d, 'description', '')` 构造差异描述，但真实模型 `Difference`（`modules/perception/difference/models.py` 的 dataclass）**根本没有 `description` 字段**——只有 `id/source_type/category/intensity/payload/…`。于是 desc 恒为 `source_type:`（`perception:`），每次触发内容相同。

**为什么测试没抓到：**
1. **测试用自定义假类 `_Diff`**，自己加了 `description="变化"` 属性——假对象"看起来有 description"，掩盖了真实模型缺字段的事实；
2. **测试只断言"是否触发"**（`_run` 被调用、触发次数），**从不检查传给 LLM 的 desc/prompt 内容**——内容空洞这类质量问题，断言根本覆盖不到。

**修复：** `_trigger` 改用真实模型存在的字段——`category` + `payload.target/change_type` 构造可读描述（如 `screen_changed:主窗口`），每次随实际差异变化；新增测试用真实字段结构断言 desc 内容。

**经验（最值得记住）：**
1. **测试输入必须用真实生产模型构造**（或用与其完全一致的字段集），不要自定义"看起来合理"的假类——假对象字段与真实模型不一致时，测试通过但真实运行暴露的恰恰是字段差异。
2. **消费生产数据对象的代码，测试要断言"输出内容质量"**，而不只是"被调用了"——`getattr(x, 'field', default)` 取到默认空值导致内容空洞，只有断言内容才能抓住。
3. 给测试写 fixture 前，先 `grep` 生产模型定义确认字段名，别凭感觉。


## 22. 全按钮审计方法论 + 启动快捷键 shortcut_keys 是摆设（前端/后端）

**任务背景：** 用户要求"完整确认全部前端按钮都是有效果的"——重点抓"点了没反应 / 存了没人读"的摆设按钮。

**审计方法（4 层，可直接复用）：**

### 22.1 层 1：后端路由权威清单
导入 FastAPI app 枚举全部路由（`for r in app.routes: (methods, r.path)`），得到 177 条真实端点，**不要靠 grep**（静态挂载/条件挂载会漏）。

### 22.2 层 2：前端调用路径 → 后端路由匹配（抓死路由）
脚本提取全部 `.vue/.js` 中的字符串路径（`'/api/xxx'`、`'/xxx'`、模板字符串），将具体段替换为 `{p}` 与后端模式匹配。**结果：25 个前端路径全部命中，0 死路由**——没有 404 按钮。

### 22.3 层 3：消费方审计（抓"存了没人读"——摆设的核心）
提取前端所有 `XxxCfg('KEY')` / `updateConfig('KEY')` / CK 映射表 key，对每个 key 统计**排除 tests/docs/frontend/cli_tui 后**的后端引用数：
- 不在 `_MODIFIABLE_FIELDS` 白名单 → PUT /config 会 403（点了没反应）；
- 引用数 ≤1（仅 settings.py 定义）→ 疑似摆设。
- **注意陷阱：** 前端 CK 映射表用**小写** key（`allow_geolocation`/`shortcut_keys`/`launch_at_startup`），正则提取时容易漏掉——必须同时扫大写与小写。

### 22.4 层 4：运行时实测（真后端 + 真前端）
1. 后端用 `subprocess.Popen(start_new_session=True)` 启动（普通 nohup 会被 basher 会话清理杀掉）；
2. 前端 vite 另起端口（5173 可能被别的应用占用——**本次 5173 被一个 React 营销页占用，前端真身是 Vue**）；
3. 只读端点批量 curl 验证（28 个全 200）；
4. 写操作链路用**临时会话**实测并清理（outreach-config/tasks/title/人设/工具权限/模型参数/记忆事件 全 ✓）；
5. 浏览器 evaluate_script 端到端（改配置 → 派发 KeyboardEvent → 检查 `document.activeElement`）。

### 22.5 审计结论
- **52 个前端配置 key：51 个真实消费，1 个摆设**——`shortcut_keys`（启动快捷键）；
- 摆设原因：Settings.vue 可编辑并保存到后端，但**前端 App.vue 的快捷键逻辑是硬编码 Cmd/Ctrl+K**，后端也仅有 settings.py 定义处 1 处引用，用户改的值零消费；
- 用户实际已配置 `⌥ + X` 却从未生效——实锤摆设。

### 22.6 修复（让快捷键真实生效）
`App.vue`：
1. 引入 `useConfigStore()`，keydown 时读取 `config.shortcut_keys || config.SHORTCUT_KEYS`；
2. 新增 `parseShortcut()` 解析 `⌥ + X` / `Cmd+K` / `Ctrl+Shift+P`（支持 ⌘/⌥/⇧/⌃/Cmd/Alt/Option/Ctrl 等写法）；
3. 新增 `shortcutMatches()` 精确匹配修饰键组合；
4. 配置快捷键命中 → `_focusChat()`（跳转对话页 + 聚焦输入框）；内置 Cmd/Ctrl+K 作为兜底保留；
5. Settings.vue 描述文案同步更新（"按下后聚焦对话输入框（实时生效）"）。

**验证：** 解析逻辑 node 单测 8 项全过；浏览器端到端：设 `Ctrl+Alt+X` → 后端读回 ✓ → 派发键盘事件 → 焦点落在输入框 TEXTAREA ✓ → 恢复 `⌥ + X` ✓。

**经验：**
1. **"能编辑、能保存"≠"有效果"**——判断摆设要查"谁消费这个值"，前后端都要查（本次前端硬编码键盘、后端无引用，两头都不消费）；
2. 审计配置类按钮必须**同时扫大/小写 key**，前端 CK 表常混用；
3. 运行时实测优先用**后端真实响应 + 临时数据清理**，浏览器全量点击易受环境干扰，evaluate_script 是可靠的端到端手段；
4. 启动测试后端要脱离会话（`start_new_session=True`），否则进程被清理；开发端口可能被其他应用占用，先 `lsof -i` 确认。


## 23. "点详情没反应"的两种根因：DOM 位置 + 构建失败（前端）

**现象：** 仪表盘 API 请求日志点「详情」按钮，页面纹丝不动，用户以为按钮失效。

**根因 1（主因）：详情面板渲染在视口外**
- `API_PAGE = 50`——日志表格每页 50 行，`dash-detail` 详情面板在表格**之后**渲染；
- 点击按钮只设置 `apiDetail.value`，**没有任何滚动逻辑**——面板出现在页面底部视口之外，用户看不到任何变化；
- 浏览器实测（evaluate_script）：点击后 `dash-detail` 确实出现，但 `rect.top=3736 > viewport 2029`，且 `scrollY=0`——功能在但不可见。

**根因 2（隐蔽炸弹）：Settings.vue 重复 class 属性导致 vite build 失败**
- `<div class="setting-ctl" class="ctl-flex">`——同一元素两个 class 属性，Vue 编译器直接报 `Duplicate attribute`；
- 该错误已提交进 HEAD（共 4 处），**任何 `vite build` 都会失败**→ dist 无法更新，用户 8765 永远加载旧版；
- 排查链：build 失败 → 定位 Settings.vue:428 → 全项目扫描 `class="..." class=` 找出全部 4 处。

**修复：**
1. `Dashboard.vue`：`openApiDetail` 里 `nextTick` + `scrollIntoView({behavior:'smooth', block:'start'})` 滚动到面板（注意滚动容器是 `.page-body`，不是 window）；
2. `Settings.vue`：4 处 `class="setting-ctl" class="ctl-flex"` → `class="setting-ctl ctl-flex"`；
3. `api/main.py`：GET/DELETE 请求无 body，改为记录 `?query` 到 `request_body`（否则 GET 详情恒为「无记录」）。

**验证：** 浏览器实测点击后 `rect.top=1712 < viewport 2029` → 面板可见 ✓；GET 日志详情显示 `?limit=3` ✓；相关测试 33 项全过。

**经验：**
1. **"点按钮没反应"先查 DOM 位置/可见性**，再怀疑事件绑定——面板渲染到视口外是最常见的假象；
2. **`vite build` 失败 = 用户永远在用旧版前端**——任何部署前先跑一次 build，CI/提交流程应包含构建校验；
3. 排查重复属性等模板错误，用 grep 全项目扫描（`class="[^"]*" class=`）一次抓全；
4. SPA 产物：index.html 必须 no-cache（server.py 已做），带 hash 资源可 immutable；但**已打开的页面不会自动刷新**，升级后需告知用户刷新/重启窗口。
