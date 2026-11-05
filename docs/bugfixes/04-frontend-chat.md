# 错误原因与修复记录 · 前端 · 聊天 / 气泡 / 重放 / 多会话

[← 返回总索引](./README.md) | [English](./04-frontend-chat.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 15 节：§3、§4、§5、§8、§17、§26、§69、§71、§72、§73、§74、§85、§91、§94、§101。

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

---

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

---

## 69. 前端上下文占用显示偏低 —— context_tokens 未计入工具调用历史（后端）

**现象：** ThinkingStatusPanel 显示的上下文占用百分比偏低，与真实消耗不符。

**根因：** `model_runner._generate_with_tools` 只在**进入工具循环前**估算一次 `_thinker._context_tokens = engine.estimate_tokens(system + tools + user_prompt)`（model_runner.py:2023），工具循环内**每轮累积的 messages（tool_calls + tool 结果）不计入**。而 `_maybe_summarize_context` 内部算了 messages token（用于判断 90% 总结阈值），但没同步回 `_context_tokens` → 前端显示的是初始 prompt 占用，不是实时累积。

**修复：** `_maybe_summarize_context` 每轮把 messages 估算的 token 同步写回 `self._thinker._context_tokens`（无论是否触发总结），前端展示含工具历史的真实占用。

**验证：** `test_maybe_summarize_syncs_context_tokens`；前端 ThinkingStatusPanel 上下文 warn(70%)/danger(90%)/100%封顶/无数据隐藏测试。

**教训：** ① "估算一次"若用于展示累计状态，必须覆盖完整累积范围（含工具历史），否则展示值失真；② token 估算的更新点要与"真实消费点"（messages 增长处）对齐，不能只在初始化处算一次；③ 前端展示字段（context_tokens）要由后端维护一个**权威实时值**，前端只管渲染。

---

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

---

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

---

## 73. 恢复路径工具 trace 双份 + 审批文本污染思考区（前端/后端，§71 同类）

**现象：** 排查 §71 同类 bug（运行时 vs 恢复不一致）时的两个 MED 问题：

1. **工具 trace 双份入账**：supervisor/expert 的工具 trace 在恢复时既进 expert 气泡 `_tools`（预扫描 expertAgg），又进全局 `traces`；运行时只进 expert `_tools`（`addExpertTool`）。→ 恢复后运行轨迹多出重复项。
2. **审批/提问文本污染思考区**：security 事件（"等待用户审批"/"user_intent_request"）持久化为 `role='thought', tier='thinking'`，恢复时折叠进大模型回复的思考区——瞬态交互文本变成历史"思考"内容，误导。

**修复：**
- MED-1：恢复时 supervisor/expert 的工具 trace 只进 expert `_tools`（`tier!=='supervisor'&&tier!=='expert'` 才进全局 traces）
- MED-2：后端 `_persist_thought` 对 `event_type=='security'` 用 `tier='security'` 持久化；前端恢复时 `tier==='security'` 的 thought 跳过（不折叠进思考区、不作历史气泡）

**验证：** 新增"expert 工具 trace 不重复进全局 traces"+"security tier 跳过"用例；前端 501 + 后端 api_stream 180 全过。

**教训：** ① 同一数据在恢复时的多条路径（expert 聚合 + 全局 traces）要防重复入账，运行时进哪条恢复就进哪条；② 瞬态交互事件（审批/提问）不应作为历史"思考"持久化或展示——要么不持久化，要么用独立 tier 标记并在恢复时跳过；③ 跨端修复要同步（后端持久化 tier 标记 + 前端识别）。

---

## 74. 架构根治：运行时与恢复路径共用同一分类规则（前端重构）

**背景：** §71-73 连续发现运行时展示路径与 loadHistory 恢复路径不一致（思考折叠/专家气泡数量/属性名/工具 trace 双份/审批污染），每次都是"运行时改一处、恢复改一处"的补丁式修复。根因：两套独立实现，属性名/聚合规则未对齐。

**架构重构（根治）：**
- **新增 `classifyThinking(d)` 纯函数**：统一分类规则（security 跳过 / approval / intent / tool_trace / expert / thinking），运行时 WS 事件与 loadHistory 恢复**共用它**——分类规则唯一，从根源杜绝不一致
- **新增 `dispatchThinking(d)`**：运行时 WS 事件分派器（调 classifyThinking + 按类别执行 addApproval/addIntent/addExpertThinking/addExpertMessage/addThinkingStep/traces）
- **Chat.vue `_onThinking`** 改为调 `chat.dispatchThinking(d)`（运行时也走统一分类）
- **loadHistory** 改为用 `classifyThinking` 分类持久化消息并按类别累积（专家聚合/大模型思考折叠/工具轨迹/security 跳过）——不再维护独立映射逻辑

**验证：** 新增架构一致性测试（classifyThinking 分类规则 + dispatchThinking 输出/推理分流 + 工具 trace 归类）；前端 504 全过。

**教训：** ① 展示规则（"某数据 → 哪种 UI"）应抽成**单一纯函数**，运行时与恢复都调用，而非各自实现——这样分类永远一致，属性名/聚合策略天然统一；② 恢复不是"另写一套渲染"，而是"把持久化数据按同一规则重放/累积"；③ 架构层根治优于反复补丁——补丁只修当前不一致点，架构统一从源头消除整类问题。

---

## 85. 同一 AI 多次发言被合并到一个气泡（前端专家气泡机制）

**现象：** 编排模式下同一个 AI（同 tier + 同身份）第一次和第二次发言被合并成同一个对话气泡，内容互相覆盖。用户期望：每次发言独立气泡，方便追溯对话历史。

**根因：** 前端 `chat.js` 的 `addExpertMessage` 按 `tier` 做 key 去重复用已有气泡（`existing.content = content` 覆盖）。§86 修复了不同身份互相替换的问题（改用 `tier:identity` 复合 key），但**同身份的多次输出仍然复用同一个气泡**——第二次发言时命中已有同身份气泡，直接覆盖内容而非新建。

**现象细节：**
- `addExpertThinking`（思考缓冲）按身份累积到 `_pendingByTier`，`addExpertMessage` 时一次性输出到气泡，然后清空该身份的思考缓冲。
- `addExpertTool`（工具调用）同理累积到 `_pendingTools`，输出到气泡。
- 问题在于：同一个 AI 在同一轮对话中多次输出（如先思考、调工具、再输出），最终只保留最后一次的完整内容，之前的思考过程和工具调用丢失。

**修复方向：** `addExpertMessage` 应为每次输出新建气泡，而非复用已有同身份气泡。思考/工具缓冲仍按身份累积，但在每次输出时**消耗并挂到该次输出气泡上**，不清空以便后续输出仍可引用。`switchToSession` 重放路径需对齐。

**影响范围：** `frontend/src/stores/chat.js` 的 `addExpertMessage`、`addExpertThinking`、`addExpertTool`、`_clearExpertState`、`switchToSession` 重放路径。

**当前状态：** 已识别，待实现修复。

---

## 91. switchToSession 重放路径合并同一身份的多次输出（前端）

**现象：** 切换会话时，同一身份（tier:identity）的多次输出（如"前端方案v1"、"前端方案v2"、"前端方案v3"）被合并为一个气泡，只显示最后一次。与 §85 同源。

**根因：** `chat.js:208-286` 的 `switchToSession` 重放路径用 `expertAgg` 按 `tier:identity` 聚合所有 thinking/contents，最终取 `agg.contents[agg.contents.length - 1]`。中间输出丢失。

**影响范围：** 切换会话后看到的对话历史不完整。

**修复建议：** 重放路径每次输出新建气泡，而非按身份聚合。

---

## 94. thinking 内容子串去重误杀 —— 短文本被长文本包含导致丢失（前端）

**现象：** AI 思考步骤 A "分析需求" 和步骤 B "分析需求并制定方案"，B 包含 A 的子串，B 被 `includes()` 去重跳过。

**根因：** `chat.js:341`：`if (pendingThinking.value.includes(line)) return` 用子串匹配而非精确匹配。

**影响范围：** 思考步骤丢失（概率低但确实存在）。

**修复建议：** 改为 `Set` 存储已输出的行（精确匹配），或用 `line.trim() === existing.trim()` 比较。

---

## 101. 重放把主管/专家多次输出合并成一个气泡 —— entry_type 没有落库（前端 + 后端，§85/§91）

**现象：** 编排模式下同一个 AI（同 tier + 同身份）一轮里发言两次（"方案v1"→"方案v2"），
**刷新页面或切回会话后只剩"方案v2"**；更糟的是，主管/专家的最终回复有时会被塞进大模型
回复的"思考"折叠区里，看起来像大模型的推理。

**根因（两处）**：
1. 后端 `api_stream._persist_thought()` 只把 `tier` 与 `identity_name` 写进
   `chat_messages.metadata`，**没有写 `entry_type`**。实时帧里
   `data.payload.content.entry_type == "response"` 是前端区分"最终回复气泡"与
   "过程推理"的唯一依据（`classifyThinking`）；落库时丢掉它，恢复时所有主管/专家发言
   都被判成 `kind='thinking'`。
2. 前端 `chat.js::switchToSession` 的重放按 `tier:identity` 聚合成
   `agg.contents[]`，最后只 `push` 一条 `content: agg.contents[被覆盖]`；而落库缺
   `entry_type` 又让 `c.kind === 'expert'` 分支根本进不去，于是专家最终回复顺着
   `kind === 'thinking'` 落到 `pendingLargeThinking`，被挂到下一条 assistant 消息的
   `thinking` 上（归因错误 + 正文丢失）。

**修复：**
1. `_persist_thought(..., entry_type="")`：把 `entry_type` 一起写进 metadata（有值才写，
   空则不产生多余键）；`scheduler_event_callback` 用它已经算好的 `_inject_entry_type` 传入。
   `model_runner._push_expert_output` 早已写 `entry_type="thinking"`，两边口径一致。
2. `switchToSession` 重放改为"缓冲 + 消耗"：
   - 逐条还原 `data.entry_type` / `identity_name`（同时兼容顶层 `d.entry_type`），
     让 `classifyThinking` 与运行时拿到同样输入（§74 的"同一套分类规则"继续成立）。
   - 缓冲从 `expertAgg{contents[]}` 换成 `expertBuf{tier:identity -> {thinking, tools}}`：
     每遇到一条 `response` 就**新建**一个气泡，把此刻的思考/工具挂上去并清空缓冲 ——
     与运行时 `addExpertMessage` 完全同款，同身份多次输出不再合并。
   - 主管/专家的非 `response` 推理进过程流（`addProcessStep`），不再污染
     `pendingLargeThinking`；有 `role=process` 聚合时仍然跳过，避免重复。
   - 循环结束后剩余的缓冲直接丢弃（对应运行时"未消费的思考"）。

**兼容性：** 老数据没有 `entry_type`（当时的库就是这样），重放时按过程内容处理 ——
不产生气泡，但也不会再错误地粘到大模型回复的思考区。

**测试：** `tests/unit/test_api_stream_ext.py::test_persist_thought_keeps_entry_type`
（metadata 含 entry_type；空值不产生多余键）；
`frontend/src/stores/chat.spec.js` 新增"历史加载 主管/专家多次输出各自独立气泡"
（同身份两条 response → 两个气泡、工具缓冲只挂首个气泡、专家回复不进大模型思考区）。

**回归：** `frontend` 全量 583 例通过；`tests/unit` 相关子集（api_stream 三件套 +
settings 两件套）223 例通过。


### 101.1 专家气泡的思考/工具缓冲已无生产者（清理）

**现象：** 不可见。但 `switchToSession` 重放会把主管/专家的工具文本塞进专家气泡的
`_tools`，而实时运行时同样的文本进过程流——刷新前后展示不一致。

**根因：** 自 414375c（专家推理/工具轨迹改走过程流）后，`addExpertThinking` /
`addExpertTool` **已无生产调用**，只剩永远为空的 `_pendingByTier` / `_pendingTools`
被 `addExpertMessage` 读取；重放路径却仍在填充 `_tools`（§101 改造时按旧模型对齐）。
于是「气泡自带思考/工具」只剩重放能看到。

**修复：** 删除死代码，统一到过程流：
1. 移除 `addExpertThinking` / `addExpertTool` / `_pendingByTier` / `_pendingTools` /
   `_expertBubbles` 及 `_hasThinkingLine`。
2. `addExpertMessage` 只创建最终回复气泡，不再读取缓冲。
3. `switchToSession`：主管/专家的工具文本改为 `addProcessStep`（与运行时
   `dispatchThinking` 同款）；专家气泡不再写 `_thinking` / `_tools`。
4. `ChatMessage.vue` 删除专家气泡内已不可达的思考折叠与工具列表块。

**测试：** `chat.spec.js` 重放用例改为断言气泡无 `_tools`、工具文本进 `processFlow`；
`ChatMessage.spec.js` 改为断言专家气泡不渲染思考/工具（防回归）。前端 583 例全过。
