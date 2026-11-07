# 错误原因与修复记录 · 前端 · 页面 / 样式 / 构建 / 按钮

[← 返回总索引](./README.md) | [English](./05-frontend-ui.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 7 节：§6、§7、§14、§22、§23、§75、§108。

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

---

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

---

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

---

## 75. AI 输出序号（有序列表）超出气泡边界（前端样式）

**现象：** AI 输出带序号的内容（markdown 有序列表 `1. 2. 3.`）时，序号/长内容渲染后超出气泡边界，撑破 `message-bubble`。

**根因：** `.message-bubble`（`frontend/css/components.css`）有 `max-width: 100%` 但**缺 `min-width: 0` 和 `overflow-wrap: break-word`/`word-break: break-word`**。作为 flex 容器 `.message-body` 的子项，flex 子项默认 `min-width: auto` 阻止收缩，遇到无法换行的长 token（序号列表）就溢出气泡。

**修复：**
- `.message-bubble` 加 `min-width: 0; overflow-wrap: break-word; word-break: break-word;`
- 新增 `.message-bubble ol/ul/li` 换行保护（`overflow-wrap`/`word-break`）+ 列表缩进样式（`list-style: decimal/disc`）

**验证：** ChatMessage 21 测试 + 前端构建通过。

**教训：** flex 布局里的气泡子项必须显式 `min-width: 0` 才允许收缩换行；长内容（序号列表/URL/代码标识符）要靠 `overflow-wrap: break-word` 兜底，否则会撑破固定 max-width 容器。CSS 溢出类问题常被"只测逻辑不测样式"的测试忽略，需在真实宽度的渲染下人工核对。

---

## 108. 会话列表 / 输入框被长文本撑宽 —— Vue 内联尺寸缺单位 + flex 子项未设 min-width:0（前端样式）

**现象：**
- 对话页左侧会话列表本应固定 260px，长标题出现后被撑到 429px；收起状态也不能稳定在 40px。
- AI 输入框在长提示文字、长输入或操作按钮出现时被挤压/横向溢出，严重时 textarea 宽度变成 0。

**根因：**
1. **Vue 内联样式长度缺单位**：`Chat.vue` 里 `:style="{ width: sessionListCollapsed ? 40 : 260 }"` 把数字直接传给 DOM。Vue 不会为动态 style 的长度属性自动补 `px`，浏览器把 `width: 260` 判为非法并丢弃；侧栏没有固定宽度，只能按内容宽度（max-content）撑开。
2. **flex 子项没有 `min-width:0`**：`.session-list`、`.session-item`、标题中间层、`.chat-input-actions` 等 flex 子项默认 `min-width:auto`，长标题/提示的 min-content 宽度会参与布局；`textarea` 只有 `flex:1`，没有 `width:0; min-width:0`，被右侧操作区挤到 0 宽并溢出。

**修复：**
- `frontend/src/pages/Chat.vue`：侧栏 `width`/`maxWidth` 改为 `(sessionListCollapsed ? 40 : 260) + 'px'`，并补 `minWidth: 0`。
- `frontend/src/components/SessionList.vue`：标题行改用 `.session-item-main` / `.session-item-text`，去掉不可控的内联 flex 样式。
- `frontend/css/components.css`：
  - 会话栏/列表体/会话项/搜索框/批量栏补 `min-width:0`、`overflow:hidden`；标题/时间加省略号。
  - 输入区/输入框外层补 `width:100%; min-width:0; overflow:hidden`；`textarea` 用 `flex:1 1 0; width:0; min-width:0`；操作区 `max-width:40%`，提示文字省略。
- `frontend/css/layout.css`：`app-body`、`main-content`、`page-view` 及页面根节点补 `min-width:0`，防止内部长内容反向撑开整页。
- **同类排查已修**：宠物输入框/记忆输入、记忆时间线、设置页 `flex:1` 输入框、工具卡片标题/代码行、运行轨迹参数、RunnerNode 模型名、会话设置弹窗标题，均补 `min-width:0`/省略规则。

**验证：** 用 Playwright 在真实 Vue 页面注入超长会话标题和长输入，测试 1440/1024/800 三种宽度：侧栏稳定 260px（收起 40px），输入框宽度不随文字变化，`document.body.scrollWidth === innerWidth`；前端 585 项测试通过，`npm run build` 通过。

**经验 / 检查清单：**
1. **Vue 动态 style 的长度属性必须带单位**：`'260px'`、`'50%'`、`calc(...)`；只有 `opacity`、`zIndex`、`flexShrink`、`lineHeight` 等无单位属性可以传数字，`0` 例外。
2. **flex 里所有可收缩的文本/输入子项都要 `min-width:0`**；省略号三件套要加在真正被约束的那一层，并确保中间 flex 父级也能收缩。
3. **输入框在 flex 行中推荐 `flex:1 1 0; width:0; min-width:0`**；右侧提示/按钮加 `min-width:0; max-width`，不要让提示的 max-content 参与抢空间。
4. **固定侧栏四件套**：`width` + `max-width` + `flex-shrink:0` + `min-width:0`，并用长标题回归。
5. **CSS 溢出问题必须做真实宽度渲染验证**：看 `getBoundingClientRect()`、`scrollWidth/clientWidth`，不要只跑 jsdom 单测；至少覆盖宽/中/窄三档。
6. 静态扫描（人工确认命中项）：
   ```bash
   # 动态 style 里的数字尺寸（可能缺单位）
   grep -RInE --include='*.vue' ':style="\{[^}]*\b(width|height|maxWidth|minWidth|maxHeight|minHeight)\s*:\s*[0-9]+' frontend/src
   # flex:1 但可能缺 min-width:0 的候选
   grep -RInE --include='*.vue' --include='*.css' 'flex:\s*1(;|\s)' frontend/src frontend/css
   ```
