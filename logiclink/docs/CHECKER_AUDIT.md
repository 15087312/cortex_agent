# Checker 自身缺陷审计与修复计划（2026-11）

> 状态：缺陷模式沉淀 + 分阶段修复计划（部分已落地）
> 适用范围：`logiclink` 本体的 checker / 渲染 / 配置，不是 `*.link` 的写法
> 关联：[`AUDIT_LESSONS.md`](./AUDIT_LESSONS.md)（写 `.link` 的误写模式）、[`V2_DESIGN.md`](./V2_DESIGN.md)（四层设计）

---

## 一、为什么需要这份文档

`AUDIT_LESSONS.md` 记录的是**标注写错**。本文记录的是更危险的一类：**checker 自己
失效**。它的危害在于输出看起来正常——绿就是绿，没人会怀疑“规则根本没执行”。

本轮暴露的四个真实例子：

| # | 症状 | 真相 |
|---|------|------|
| 1 | A、B 互相 import，`logiclink .` 绿 | E302 的跳过条件在遍历 `real` 的循环里恒假，双向边被整体跳过；E303 又只在“反向真实、正向不真实”时触发 → 环完全隐形 |
| 2 | `forbid_eq_change = true` 时永远 exit 1 | 只读布尔值，从未跟任何基线比对；改名成“禁止运行”才准确 |
| 3 | `logiclink . --graph` 说“全绿”，checker 报 E302 | 渲染与判定各写了一份跳过规则，两份规则语义不一致 |
| 4 | 同一处端口断开报 2 条错 | 判定分支重叠：E101（全部输入缺失）与 E103（集合不相交）同时命中 |

共同点：**没有任何一条测试表达“这条规则在应当触发时必须触发”**。所有测试都只覆盖
“正常路径不误报”，所以规则空转不会被发现。

---

## 二、缺陷模式（再次写 checker 时对照）

### C1. 空真守卫（vacuous guard）—最危险

```python
for (ma, mb) in sorted(real):
    if (mb, ma) in declared and (ma, mb) not in declared:   # `(ma,mb) not in declared` 可能为真
        continue
    ...
```
原版的实际写法是 `(ma, mb) not in real`，而它正处在 `for ... in sorted(real)` 里 ——
条件恒假，于是“反向已声明就跳过”对**所有**边生效。

**识别**：任何在 `for x in S` 内出现 `x not in S`（或等价恒假谓词）的守卫。
**纪律**：把守卫改写成“E303 真正拥有的那一格”，并在注释里写出互补关系：

```python
if (mb, ma) in declared and (mb, ma) not in real:   # E303 owns it
    continue
```

**必须配的测试**：同时构造“只有正向真实”“只有反向真实”“双向都真实”三种图，
第三种必须报错。缺第三种，这类 bug 一定复发。

### C2. 判定与渲染各写一份规则

`check_graph` 与 `render_graph` 各自维护“哪些边该跳过”。修好判定侧后渲染侧仍保留旧
假设，于是命令行报环、图说无事。

**纪律**：规则只有一个来源。现在由 `_cycle_edges()` 统一计算“边 → 诊断码”，
`check_graph` 与 `render_graph` 都消费它。新增规则时**先抽函数，再接线**。

### C3. 配置旋钮不接线

`ArchConstraints` 里目前仍有 6 个字段被解析、零引用（见 F6）。写进配置的人会以为
有护栏，比“没有这个功能”更糟。

**纪律**：新增字段必须同 PR 内“接线 + 行为测试”，否则不要加字段。
**防复发**：`test_config_wiring.py`——反射遍历 dataclass 字段，断言每个字段名在
`src/` 里至少出现一次（免检名单必须带 TODO 与截止条件）。

### C4. 声明即消毒

把环写进两条 `eq`（`A·B` 与 `B·A`）曾被视为“合法”，等于让文档消除诊断。
**纪律**：checker 只信代码事实（真实 import / 真实调用），声明只能**增加**可读性，
不能**豁免**事实。豁免必须走显式白名单（`[arch] allow_cycles`）并降级为 warning。

### C5. 一个断裂多条诊断

E101 与 E103 分支重叠。**纪律**：一个断裂只有一个归属诊断；用 `if/elif` 表达互斥，
并在 docstring 里写清“谁吸收谁”。

### C6. 生成物入库

`--html*` 产物曾被提交，导致每次重生成都是巨型 diff，review 里完全淹没有效改动。
**纪律**：生成物只写不进库（`.gitignore` + README 声明输出位置）。

---

## 三、写/审 checker 的检查清单

- [ ] 每条 `continue` / `break` 的守卫，我能否举出一个“它必须为真”的具体输入？
- [ ] 每条诊断规则是否有**正例测试**（能触发）和**反例测试**（不该触发）？
- [ ] 同一条语义是否在两个文件里各写了一次？
- [ ] 图算法是否有暴力实现交叉验证（随机对拍）？
- [ ] 新增配置字段是否已经接线，且有不接就红的测试？
- [ ] 新增渲染/输出是否会把同一结论表达成与判定不同的样子？
- [ ] 诊断是否可能对同一处问题报两条？
- [ ] 仓库主干 `--strict` 是否为 0 诊断？（否则严格模式形同废弃）

---

## 四、现有缺陷清单与修复计划

优先级：P0 = 正确性/可用性受损；P1 = 结论可能错；P2 = 覆盖与卫生；P3 = 待决策。

### F1 · `Retrieve` 原子白名单过宽（P0，纯配置）— 已落地（见 §七·1）

**现象**：主干 63 条诊断**全是 W010**，其中 57 条是“有 `Retrieve` 证据但 term 未提及”。
**根因**：`link.toml` 的 `[atoms.Retrieve].calls` 含 `get`、`result`、`exists`、`slice`、
`collect`、`post`、`initial`、`__aexit__` 等泛名，任何函数随手调用就算“有 Retrieve 证据”。
**后果**：`--strict` 必失败 → 严格模式废弃 → 真问题被噪声掩盖（本轮 4 个缺陷都因此没被发现）。

**方案**
1. 二分 `calls`：保留有区分度的（`retrieve`、`recall`、`_build_prompt`、`_vector_search`、
   `_causal_search`、`_match_skill`、`_get_session_conversation`、`get_recent_messages`、
   `get_session_metadata`、`_build_context`、`urlopen`、`call_tool`、`list_tools`）；
   泛名（`get`、`result`、`exists`、`slice`、`collect`、`post`、`initialize`、`isdir`、
   `__aexit__`）移入新增的 `[atoms.RetrieveWeak]`（不参与 W010、不参与 E002）或删除。
2. 逐类替换，每类跑一次 `--strict`，把新增的 E002 逐条核到模块的 `.link` 上。

**风险**：删泛名会让真实 Retrieve 失去证据 → E002。缓解：一次只删一类、可整体 revert（纯配置）。
**验收**：`logiclink . --strict` 0 诊断；`git diff` 只动 `link.toml`；测试不变绿→红→绿均无。

**执行结果（2026-11-04）**

直接删掉 16 个泛名会立刻产生 **21 条 E002**（这些 block 的 Retrieve 证据本来就只来自
`ctx.get()` 之类的泛名），说明“删”和“改模型”是两件事，必须拆开：

1. `AtomRule` 新增 `weak_calls`：**弱调用可作 E002 证据，但永不被计为 W010 的“多余证据”**
   （`evidence_hit` 用 `calls + weak_calls`，`attribute_call` 仍只看 `calls`）。
2. `link.toml` 把 16 个泛名（`get`/`result`/`post`/`__aexit__`/`popleft`/`gather` …）
   从 `Retrieve.calls` 移到 `weak_calls`。
3. 余下 6 条 W010 是真信号，按语义修正 5 个 block（见 §七）；其中 embedding 两个块
   依法审计判定表 M2 从 `Retrieve` 改为 `Retrieve · Think`（`_load_model` 取权重算 Retrieve，
   `self._model(**inputs)` 是推理算 Think）。

**结果**：`logiclink . --strict` → `logiclink: ok`（63 条 W010 → 0，0 error）；
测试 85 → 88（新增 3 条弱调用语义用例）。

### F1b · 21 个 block 的 `Retrieve` 证据仍是弱调用（P1，语义审计）— 已落地（2026-11-05）

F1 只把噪声藏起来了（改成弱证据），没解决“声明了 Retrieve 但唯一证据是 `x.get()`”。
逐个核过后应有的形态是：**要么补上真实的检索调用名**（精确加入 `calls`），
**要么把 term 里的 `Retrieve` 删掉**。待核清单：

| block | 位置 | 弱证据 |
|---|---|---|
| `WsDispatch` | `cli_tui/screens/repl.py:229` | `data.get`（应用快照，疑似应删 Retrieve） |
| `InputProcess` | `cli_tui/screens/repl.py:782` | `e.get`（真实检索是 `read_edit_history`，可精确补名） |
| `StdioSubmit` | `infra/mcp/transport.py:88` | `run_coroutine_threadsafe` / `fut.result` |
| `StdioClose` / `SseClose` | `infra/mcp/transport.py:201/348` | `__aexit__`（资源收尾，不是检索） |
| `SMALL_Chat_Emit` / `SMALL_Generate_Emit` | `infra/model/small_model_client.py:76/162` | `session.post` / `data.get` |
| `DbInit` / `DbConn` | `modules/database/connection.py:50/126` | `config.get_url` / `self.initialize` |
| `PetPersist` | `modules/desktop_pet/pet_engine.py:244` | `PetStore.get_instance` |
| `PetActions_Emit` | `modules/desktop_pet/actions.py:1` | 模块级标注（见 M6） |
| `ModuleScan` | `modules/management/core/collector.py:41` | `get` |
| `ChatOnly_Turn` | `modules/thinking/chat_gateway.py:232` | `queue.get` / `asyncio.wait_for` |
| `LightThink_Think` | `modules/thinking/chat_light/continuous_thinker.py:54` | `collect` / `_slicer.slice` |
| `BB_ReadDialog` | `modules/thinking/cognition/blackboard.py:615` | `to_dict` |
| `BusRequest` / `BusReceive` | `modules/thinking/communication/message_bus.py:240/299` | `wait_for` / `popleft` |
| `CtxBuild` | `modules/thinking/context/manager.py:31` | `d.get` |
| `MockHttp` | `tools/mock_backend.py:54` | `hdrs.get` |
| `Async_Gather` | `utils/async_utils.py:21` | `asyncio.gather`（纯并发，应删 Retrieve） |
| `App_Emit` | `api/main.py:165` | `ctx.get`（真实读取是 `push_sessions`，可精确补名） |

**执行结果（2026-11-05，21 条逐条核完）**：结论是上表里“该不该有 Retrieve”其实分三等，
只有第二等能靠改名解决：

**A. 声明就是错的（本次删除 `Retrieve` 或整个幻影 block，12 条：10 条降级 + 2 条删 block）**

| block | 新声明 | 依据 |
|---|---|---|
| `StdioSubmit` | `Guard` | 跨线程调度 + `fut.result` 等值，没有取数 |
| `StdioClose` / `SseClose` | `Guard` | 分支 + `__aexit__` 资源释放 |
| `WsDispatch` | `Guard` | 读的是后端**推来**的帧（`data.get`），不是本层去取 |
| `MockHttp` | `Guard` | 观测中间件，读别人发来的 header/body |
| `DbInit` / `DbConn` | `Guard · Write` | `config.get_url` 是读配置；取数发生在调用方 |
| `ModuleScan` | `Loop(Guard)` | 只做 `isdir/exists` 目录探测，不读文件内容 |
| `SMALL_Chat_Emit` / `SMALL_Generate_Emit` | `Loop(Guard · Throttle) · Emit` | HTTP 发包 + 流式输出，唯一的“读取”是 `kwargs.get` |
| `Async_Gather` / `PetActions_Emit` | 注释删除 | 纯并发工具 / 纯静态数据表，模块级幻影声明 |

**B. 声明是对的，只是缺精确名（本次补进 `calls`，3 条 block / 4 个名）**：`InputProcess` 补
`read_edit_history`（读 `.opencode/edits/` 的已修正历史）、`LightThink_Think` 补
`_recall_memories` / `_recall_knowledge`（事件记忆 + 知识库）、`App_Emit` 补
`push_sessions`（枚举可推送出口后再发，正是 `Retrieve · Emit` 的顺序）。补名后它们从
“弱证据”升为强证据，W010 也重新对它们生效。

**C. 声明是对的，且本层原理上看不见证据（保留，6 条）**：`BB_ReadDialog`（`self._dialog_entries`
属性读 + `to_dict`）、`CtxBuild`（黑板 dict 读）、`BusReceive`（`deque.popleft`）、
`BusRequest`（`Future` 等待）、`ChatOnly_Turn`（`asyncio.Queue.get`）、`PetPersist`
（读库发生在 `PetEngine._last_assistant_same_as` 里，v1 不跨函数）。这 6 条既不删声明
（它们确实是取数），也不把泛名塞进 `calls`（会炸出全仓 W010）。

**所以 `weak_calls` 不清空，但也不再是黑箱**——新增 `W011`（info）逐条报出“该原子只有
弱证据”，于是这份债从文档表格变成了每次运行都能统计的输出：`--strict` 仍是 0
error / 0 warning，W011 固定 6 条，只能降不能升（回归用例：
`test_integration.py::test_weak_only_evidence_is_reported_as_W011` /
`test_strong_evidence_does_not_emit_W011` / `test_W011_does_not_change_cli_exit_codes`）。
`eq/alias` 基线已按新声明 `--arch-update`（77 条指纹）。

### F2 · 跨分支造假边（P0，代码）— 已落地（见 §七·2）

**现象**

```
A · (B | C) · D            -> A→B, B→C, C→D      # B→C 是假的：C 与 B 互斥
A · (B · C | D)            -> A→B, B→C, C→D      # C→D 跨分支
Loop(... · (Write | Emit)) -> ... Write→Emit     # 同上
```

**根因**：`v2_flow.py:35 _neighbour_pairs` 与 `v2_graph.py:98 _declared_edges` 都对
`T.linear_atoms(term)` 展平后的全序取相邻对，分支边界被抹平。
**后果**：L1 端口衔接（E101/E103）误报或漏报；L3 造出幽灵声明边 → E301 误报。

**方案**：在 `ast_terms` 新增 `sequence_pairs(term)`，语义为“同一分支内的相邻”：

```
pairs(Seq(a,b)) = pairs(a) ∪ pairs(b) ∪ (last(a) × first(b))
pairs(Alt(x,y)) = pairs(x) ∪ pairs(y)          # 分支之间不连边
pairs(Atom)     = ∅
first(Alt)      = first(x) ∪ first(y)，last 同理
```

`A·(B|C)·D` 得到 `A→B, A→C, B→D, C→D`，而不是 `B→C`。`_neighbour_pairs` 与
`_declared_edges` 改为消费它。

**风险**：结论会变（有些边消失、有些新增）。消失的是假边（好事）；新增的若是真耦合，
说明之前被假边掩盖，需逐条确认真实 import 事实。
**验收**：新增 `test_neighbour_pairs_respects_branches`（三种形状 + 嵌套 `Loop`）；
全仓跑一遍，对每条新增/消失的边在 PR 里写明依据。

**执行结果**：`T.sequence_pairs()` 已落地（`_edge_atoms` 取每支的首/尾集合，`Seq` 只连
last(left)×first(right)，`Alt`/`Par` 只递归），`v2_flow._neighbour_pairs` 与
`v2_graph._declared_edges` 均改为消费它。新增 4 条单测（线性 / 两支 / 嵌套支 / Loop 体）。
全仓边集合无变化（`app.link` 的 eq 均为线性 use 链），`--strict` 保持 0。

### F3 · 模块扫描根由“符号声明位置”决定（P1，代码）— 已落地（见 §七·2）

**现象**：`_module_dirs` 用 `use n := module.Symbol` 中符号声明文件的父目录当模块根。
把符号从包根移到 `core/x.link`，扫描范围会**静默**从整个模块缩到子目录，E301/E302
结论随搬家变化。`app.link:67`、`modules/thinking/orchestrator.link:14` 的注释就是
为绕开这个坑而故意把 `use` 指向包根符号。
**根因**：`v2_graph.py:39 _symbol_decl` + `v2_graph.py:48 _module_dirs`。

**方案**：模块根由 `use` 的 **module 段**决定 —— 在 `cfg.scan_roots` 下定位同名目录
（缺目录则退化为扫描根内所有 `*.link` 的公共父目录）；符号只用于存在性校验（缺失走 E005）。
**风险**：扫描根变宽 → 可能新报 E302（真实耦合）。这些通常应写进 `app.link`，或在
`allow_*` 里显式豁免。
**验收**：新增“符号声明在子目录、真实 import 在同级子目录”用例可检出；把 `use think :=
thinking.ThinkModule` 改回 `ThinkLoop` 后结论不变；更新 `app.link:67` 的绕坑注释。

**执行结果**：`_module_dirs(decls, use_target, roots)` 改为“按 `use` 的 module 段匹配
`scan_roots` 目录名”，符号仅作存在性校验，无匹配时才回退旧行为。`check_graph` /
`render_graph` 新增 `roots` 参数，`check.run` 与 `cli` 传入 `root / scan_root`。
**代价立刻显现**：盲区封堵后多出 2 条真实未声明边 —— `management → thinking`
（`modules/management/api.py:236` 的 `skill_manager`）与 `management → memory`
（`:547` 的 `EventStore`）。已在 `app.link` 如实声明为 `eq ManageThink` / `eq ManageMem`，
并注明若要消除应走与 perception/security 相同的端口倒置。基线随之 75 → 77 条。

### F4 · 相对 import 不计入真实边（P1，代码）— 已落地（见 §七·2）

**现象**：`evidence.py:108` 把 `from .beta import x` 记为 `.beta`，而 `_real_edges` 只匹配
`modules.b` / `b.` 前缀 → 包内相对导入形成的耦合与环**全部隐身**。
**方案**：在 `_real_edges` 里按“文件相对模块根的层级”解析：`level` 个点回退父包后拼接
模块名，归一到扫描根可见的绝对名；无法解析时保守忽略并计数（避免幽灵边）。
**风险**：层数算错会造出幽灵边。**验收**：`from . import x` / `from ..beta import y` /
`from .sub import z` 三条单测 + 全仓无新增诊断。

**执行结果**：改为**按路径解析**而不是按名字猜：`_relative_target(imp, source)` 从
`source.parent` 上溯 `level-1` 层再拼模块路径，然后只判定该路径是否落在
`module_dirs[b]` 内。这样 `from .beta import B`（包内 `alpha/beta.py`）绝不会被误认为
顶层模块 `beta`。同时 `evidence.visit_ImportFrom` 补上 `from . import beta`
（`node.module is None` 时之前整条丢弃）。新增 2 条集成用例（跨包相对导入算边、包内相对
导入不算边）。

### F5 · L4 状态机零使用（P2，先决策）

**现状**：仓库无任何 `state` 声明，整层从未跑过；`v2_state.py:106` 的模块目录同样是
“`.link` 所在目录”，有与 F3 相同的盲区；`v2_state.py:98-103` 的 `events` 收集后从未使用（死代码）。
**方案 A（推荐）**：在 `modules/thinking`（blackboard 的 `empty→partial→complete→empty`）
补一条真实状态机，跑通后再决定保留；目录盲区随 F3 一起修。
**方案 B**：删掉 `v2_state.py` 与 README 的 L4 行，不做未验证的承诺。
**验收**：方案 A 需一条端到端测试（代码设了未声明的状态 → E401）；方案 B 需 README/文档同步。

**实测（2026-11-04）——找到真实可绑定的状态机，但它本身是死的**：

- `modules/thinking/context/pool.py:20` `class TurnState(str, Enum)`：
  `IDLE / PLANNING / EXECUTING / INTEGRATING / COMPLETE / ERROR`，docstring 明写
  `IDLE → PLANNING → EXECUTING → INTEGRATING → COMPLETE/ERROR`；
  转换方法唯一：`transition_to(new_state)`（`:65`）。
- **`transition_to` 在生产代码中零调用**（仅 `tests/unit/test_context_pool_ext.py` 调用），
  `.state = TurnState.X` 零处，`self.state` 永远停在 `IDLE`。
- `TurnContext` 确在生产代码里被创建（`core/continuous_thinker.py:603`、
  `multi_model_orchestrator.py:470`），因此“状态机存在但从不推进”是**真实现状**。
- 并行还存在一个**字符串**状态：`multi_model_orchestrator.py:480` 写
  `_session_registry[..]["state"] = "planning"`，之后从不更新，而
  `management/api.py:1304` 把该字段直接外露给管理 API → 会话状态永远是 `planning`。
- `is_active` / `is_complete` 两个属性无任何生产消费者（潜在而非当下故障）。
- **现有 L4 实现根本看不见 `Enum`**：`v2_state._STATE_LIT_RE` 只匹配
  `state = "lit"` 字面量，`self.state: TurnState = TurnState.IDLE` 与
  `transition_to(TurnState.PLANNING)` 都匹配不到。

**方向**：

| 方向 | 能做/不能做 | 代价 |
|---|---|---|
| D1 缩到“状态名一致性”（只 E401），证据源改为 `Enum.Member` + `transition_to(...)` | 能抓枚举成员名漂移；不宣称验证流转路径 | 改 `_STATE_LIT_RE` 为枚举成员匹配（~10 行）+ 模块目录按 module 段驱动 + 一条 `state turn` 声明 |
| D2 完整声明 `TurnState` 的真实转换并接线 | 立刻报 5 条 E402（真实发现：状态机从未被驱动）；要么修代码接 `transition_to`，要么承认它不需要 | 需在 orchestrator/thinker 里真接状态，或调低声明 |
| D3 删层（`v2_state.py` + README L4 + `state/init/trans` 解析） | 不再有未验证承诺 | 最少代码，失去枚举名检查 |
| D4 反向生成：工具从代码提取候选状态集，产出 `.link` 片段供人确认后入库 | 免“人手写幻影机器” | 中等（需生成器 + 人工确认流程） |

**推荐**：D1（真实、低风险、不动生产代码）+ 顺手报告 `TurnState` 从未被驱动这件事
（属代码缺陷，不属 logiclink 的范围）；D2 只在打算真接生命周期时做。

**执行结果（选了 D2，已落地）**：

1. **L4 支持 Enum 机器**：parser 新增 `enum = TurnState`（`state_enum`）；`v2_state` 用 AST
   采集 `TurnState.X` 并区分“读”与“写”：赋值/传参算**到达**（E403 目标），只读不算
   （`is_active` 里 `state in (TurnState.A, ...)` 不会误报）；`_snake` 修正为
   `IDLE → idle`（原实现会给 `i_d_l_e`）；模块根改为“向上找扫描根 / `<dir>/<dir>.link`”，
   机器写在 `core/` 不再只扫 `core/`（与 F3 同类盲区）。
2. **声明入库**：`modules/thinking/orchestrator.link` 的 `state turn`，6 个状态 +
   4 条推进边 + 4 条 error 边。
3. **代码真的接线**（`multi_model_orchestrator.py`）：`_advance_turn_state()` 在
   ①大模型激活后 → `PLANNING`、②转入等待 → `EXECUTING`、③拿到回复 → `INTEGRATING`、
   ④`process()` 返回前 → `COMPLETE`、⑤异常分支 → `ERROR`；同时 `_sync_session_state()`
   把 `state`/`is_active` 写回 `_session_registry`，管理 API `/sessions` 不再永远显示
   “planning”（此前是写死的字面量）。

**验收**：`logiclink/tests/test_state.py` 新增 6 条枚举用例（含“只读不算到达”与“声明在
子目录仍覆盖全模块”）；`tests/unit/test_turn_lifecycle.py` 用**真实** `TurnContext` 跑
`_execute_multi_model_thinking`，断言状态停在 `integrating` 且会话表同步，并验证
`complete` 后 `is_active=False`、`elapsed_seconds` 已记录。全仓 `--strict` 保持 0 诊断。

**遗留**：`TurnContext` 在 `core/continuous_thinker.py:603` 每次构建 prompt 都新建一个
一次性实例（只用作 Fragment 池，状态不参与），这本身是冗余但无害；真正需要接入的是
orchestrator 那一个，已接。

### F6 · 六个配置字段空转（P1，代码）— 已落地（见 §七·3）

`config.py` 里曾有 7 个字段只解析不执行：`forbid_new_atoms`、`forbid_new_blocks`、
`require_evidence`、`require_doc`、`no_direct_db_access`、`must_use_event_bus`、
`require_security_check`（其中 `require_evidence` 默认 true，但证据检查从不读它）。
**方案**：逐个“接线或删除”，不留悬空字段；删除的要在文档里写明（避免有人照旧配置）。
**防复发**：`test_config_wiring.py`（见 C3）。
**验收**：该测试对当前代码应报未接线字段（红），修完后绿。

**执行结果**：7 个字段全部删除（沿用旧 `link.toml` 的仓库不会报错，未知键本来就被忽略），
`ArchConstraints` 只剩三个已接线的：`forbid_eq_change`、`max_complexity`、`allow_cycles`。
新增 `tests/test_config_wiring.py`：反射遍历 `ArchConstraints`，断言每个字段名在
`src/`（除 `config.py`）至少出现一次；另加两条具体接线断言（`allow_cycles` → `v2_graph`，
`forbid_eq_change` → `arch.check_arch` → `check.run`）。免检名单 `ALLOWED_UNWIRED` 目前为空，
只允许临时加入并带截止条件。

### F7 · 生成物入库（P2，卫生）— 已落地（见 §七·3）

`git rm --cached logiclink/logiclink-{detail,panorama,viz}.html`，`.gitignore` 加
`logiclink/logiclink-*.html`，README 注明输出目录。

**执行结果**：三个 HTML 已移出版本库（本地文件保留，可用 `--html*` 重生成），
`.gitignore` 已加 `logiclink/logiclink-*.html`。

### F8 · `use` 的 module 段从不校验（P1，代码）— 已落地

`_symbol_decl` 按“最后一个名字段”匹配，两模块导出同名符号时取第一个 → 模块目录张冠李戴。
F3 落地后模块根不再由符号位置决定，但**更严重的问题暴露出来**：`use n := module.Symbol`
的 module 段是装饰性的。实测：`use a := alpha.ThinkLoop`，而 `ThinkLoop` 只存在于 beta →
**exit 0，零诊断**。`use` 是接线合同，两段都必须成立，这属于假阴性 bug 而不是“歧义提示
要不要加”的产品决策，因此直接修复（原计划的 E006 歧义提示随之失去意义：按模块定位后
不可能再“取第一个”）。

**方案**：`check.py` 新增 `_decls_named` / `_in_module`——符号解析仍按名收集候选，但对
`:=` 形式额外要求候选属于该 module（点号前缀或**任一祖先目录**名为 module，后者保证 F3
的 `modules/<module>/core/*.link` 布局不误报）；不匹配即 E005，消息给出符号真实所在文件。
`v2_graph._symbol_decl(decls, module, symbol)` 同步改为模块优先匹配，仅在完全找不到时回退
旧行为——已报错的模块不应再从图里凭空消失。

**验收**：`test_graph.py::test_use_declares_symbol_owned_by_another_module`（红→绿，
断言 E005 且消息含 `alpha.BSym` 与 `beta.link`）、
`test_use_symbol_moved_into_module_subdir_still_resolves`（子目录布局不误报）。
全仓 `--strict` 保持 `logiclink: ok`，`tests/` 100 passed。

### F9 · E102 对链尾豁免（P3，待决策）

末端模块无后继时不检查悬空输出，链尾输出永远不被验证。
**方案**：默认检查，用 `[arch] allow_dangling_output = ["output"]` 显式豁免；或维持现状并在
README 把这条盲区写清。需要产品决策（可能会对现有 12 条真实边之上的链路产生大量 E102）。

**实测（2026-11-04，去掩码重算）**：取消尾豁免在全仓只新增 2 条，且都是合法交付点 ——
`memory(=MemRetrieve)` 的 `Retrieve`（主链路止于 memory）、`output(=Distrib)` 的 `Emit`
（分发由 API 编排层驱动）。`think(=ThinkModule)` 的 `Write` **不会**报，因为 `consumers_of`
是**全局**的：memory 的 input 含 `Write`，于是 `eq ApiThink = app · think` 这条链上
think 的 `Write` 也被“别处的消费”掩蔽了。

因此这里有两个独立的可修点，不是一个：

1. **位置决定豁免**（当前）：链尾自动放行。
2. **全局消费**（未被记录）：任一链上的消费会给该模块在所有链上盖章，`ApiThink` 的链尾
   输出事实上无人接收。

候选设计：

| 设计 | 兼顾点 | 代价 |
|---|---|---|
| B1 端口级 `deliver` 声明（`deliver Distrib: [Emit]`），默认不再豁免 | 意图写在端口旁；逐原子精确；中途链也可查 | parser 新增 decl（~4 行）+ flow（~10 行）+ 文档/测试 |
| B2 `[arch] allow_dangling_output = ["memory", "output"]` | 2 行配置即可 | 模块级消音器：同一模块在别的链上真悬空也会被放过 |
| B3 每链判定 + 顶层链推导（eq 作复合体展开，其出口升为复合端口） | 零语法，语义最正 | 需实现复合端口推导（~60–80 行）；本仓 12 条链全为顶层，今日不改变任何结论 |
| B4 补返回端口（caller 侧 `input` 声明它从 callee 收到的原子） | 唯一能查链尾“该收没收”的方案（`ApiThink` 的 `Write` 变可检） | 语义变更最大，需重写 E102 定义 + 全仓声明 |

**推荐**：先 B1（把“位置豁免”换成“意图声明”，2 条 `deliver` 即可清零），把 B4 作为唯一
能真正验证链尾的方向另行评估。不建议 B2：它把“我不知道”变成“我允许”，和 F6 空字段同类。

**执行结果（选了 B4，已落地）**：端口声明新增第三种 `accept Sym: [Atom]`（调用方接住的
返回原子）。E102 重写为逐链判定：

- 链中模块：产出必须被**同一链**后继的 `input` 接住（原来的 `consumers_of` 是全局并集，
  于是 `eq ApiThink = app · think` 里 think 的 `Write` 被 `MainFlow` 里 memory 的 input
  掩蔽）；
- 链尾模块：产出必须被调用方的 `accept` 接住（位置不再自动豁免）；
- 链头模块不参与：链头的 `output` 是它给自己的调用方的契约，不是要全部塞给下游——
  否则 `app` 的 `[Think, Write, Emit]` 会在每条 `app · X` 上误报三条。

防滥用两道门：**E104** 报“`accept` 了声明的原子没有任何被调用方产出”（否则
`accept X: [一切]` 就是新的全局消音器）；链头完全没有端口契约时该链不做数据流判定
（只记录 import 的 `eq` 不逼人写假声明）。

落地后全仓从 0 → 6 条 E102（3 条链尾 + 3 条链头误报），按上面的新规则收敛为 3 条，
再补两条真实返回声明清零：`api/api.link` 的 `accept App: [Write, Retrieve, Emit]`、
`modules/thinking/orchestrator.link` 的 `accept ThinkModule: [Retrieve]`
（后者依据 `_build_prompt` 把检索片段注入 prompt）。回归用例：`test_flow.py` 的
`test_e102_mid_chain_output_not_consumed` / `test_e102_chain_tail_needs_accepted_return` /
`test_e104_accept_names_atom_no_callee_produces`。

---

## 五、里程碑

| 阶段 | 内容 | 退出条件 |
|------|------|----------|
| M1 精度 | F1（✅ 2026-11-04） | `--strict` 在主干 0 诊断；W010 从 63 → 0（已达成：`logiclink: ok`） |
| M2 正确性 | F2 + F4（✅ 2026-11-04） | 分支/相对导入用例全绿；全仓边集合变化逐条有依据（均已声明/核实） |
| M3 覆盖 | F3 ✅ / F6 ✅ / F5 ✅ | 扫描根盲区用例通过；配置无悬空字段（已达成）；状态机层已绑定真实 Enum 并接线（见 §四·F5 执行结果） |
| M4 卫生 | F7 ✅ / F8 缓解 / F9 ✅ | 生成物不再入库（已达成）；同名符号歧义待降频；E102 已改为逐链判定 + `accept` 返回端口（见 §四·F9 执行结果） |

顺序理由：M1 只改配置、可秒级回滚，先把“严格模式可用”拿回来；M2 改的是判定语义，
必须在噪声清完之后做，否则分不清“新报的错”是修复带来的还是本来就有的。

---

## 六、回归策略（防复发）

1. **正反例成对**：每条规则至少两个用例，`should_fire` 与 `should_not_fire`，
   命名带规则码（`test_e304_three_module_cycle_reported_once`）。
2. **随机对拍**：图算法用暴力实现交叉验证（Tarjan 已用 4 节点 400 组 + 8 节点 500 组
   随机图对拍，0 处不一致）。新增图算法照此办理。
3. **精度门槛**：主干 `logiclink . --strict` 必须 0 诊断，作为 CI 判据；一出现 W010
   就说明白名单在扩散，先修配置再合并功能。
4. **单一来源**：判定与渲染共用同一函数；新增渲染时禁止复制判定条件。
5. **配置即契约**：字段没接线就是 bug，不是“未来功能”（`test_config_wiring.py`）。
6. **文档同提交**：改动诊断语义（新增/吸收错误码）必须同一提交内更新 README 错误码表
   与本文件对应条目。

---

## 七、本轮已落地

### 1. F1 精度（2026-11-04）

- `AtomRule.weak_calls`（`config.py`）+ `evidence_hit` 合并、`attribute_call` 排除
  （`check.py`）：弱调用只能“证明”，不能“指控”。
- `link.toml`：`Retrieve` 的 16 个泛名移入 `weak_calls`，`calls` 只留有区分度的检索名。
- 按语义修正 5 个 block（这些是真实模型错误，不是噪声）：
  | block | 旧 | 新 | 依据 |
  |---|---|---|---|
  | `Embedding_Embed` | `Retrieve` | `Retrieve · Think` | M2：`_load_model` 取权重=Retrieve，`self._model(**inputs)` 推理=Think |
  | `Embedding_EmbedBatch` | `Retrieve` | `Retrieve · Think` | 同上 |
  | `OutputAPI_Emit` | `Emit` | `Emit · Tool · Throttle` | 模块级块内含 click/typewrite 与 sleep |
  | `ChatOnly_Turn` | `Loop(Retrieve · Guard · Write · Emit)` | `Retrieve · Guard · Think · Loop(...)` | 循环外 `thinker.think()` 建任务 |
  | `Autostart_Apply` | `Guard · Tool` | `Guard · Write · Tool` | `makedirs` 与 plist 写入 |
- 结果：`--strict` 从 63 error → `logiclink: ok`；新增 3 条弱调用用例（共 88）。

### 2. F2 + F3 + F4 判定语义（2026-11-04）

- **F2 分支邻接**：`ast_terms.sequence_pairs()` 取代 `linear_atoms` 的相邻对；
  `a · (b | c) · d` 得 `a→b, a→c, b→d, c→d`，不再造假边 `b→c`。
- **F3 扫描根**：由 `use` 的 module 段匹配 `scan_roots` 目录名，符号只证明接线。
  封堵盲区后立刻多出 2 条真实未声明边（`management → thinking` / `management → memory`），
  已在 `app.link` 声明为 `eq ManageThink` / `eq ManageMem`，基线 75 → 77。
- **F4 相对 import**：按路径解析（`_relative_target`），只算真正跨模块目录的边；
  补上 `from . import x` 的记录。
- 结果：`--strict` 仍 0；测试 88 → 98（+4 邻接、+2 相对导入、+1 扫描根、+3 配置接线）。

### 3. F6 配置接线 + F7 卫生（2026-11-04）

- 删除 7 个悬空字段（`forbid_new_atoms`、`forbid_new_blocks`、`require_evidence`、
  `require_doc`、`no_direct_db_access`、`must_use_event_bus`、`require_security_check`）；
  `ArchConstraints` 只剩 `forbid_eq_change` / `max_complexity` / `allow_cycles`。
- 新增 `tests/test_config_wiring.py`：字段声明但无人引用即红。
- 3 个生成的 HTML 移出版本库 + `.gitignore` 收口。

### 4. E304 / E305 / 渲染对齐 / 诊断去重（commit `9c33fc1`）

- **E304/W304**：对真实 import 图求强连通分量，一个分量报一条；环内边不再重复报 E302；
  `[arch] allow_cycles` 命中降级为 W304。顺带修掉 C1 的空真守卫。
- **E305 + 基线**：`forbid_eq_change` 改为对 `.logiclink/arch.json` 的 eq/alias 规范化
  AST 指纹做 diff（`·` 与 `>>`、空白、注释差异不算改动），`--arch-update` 归档，
  `--allow-arch` 只放行本次。修掉 C3 的一个实例。
- **渲染对齐**：`--graph` 的跳过条件与 checker 共用 `_cycle_edges`，环边标 `环E304/W304`。
  修掉 C2。
- **诊断去重**：端口完全不相交只报 E103。修掉 C5。
- 迁移：主干已归档 75 条 eq/alias 指纹，`logiclink .` exit 0。

### 5. F1b 弱证据清理 + W011 债账（2026-11-05）

- **删 12 条无取数的 `Retrieve`**：`StdioSubmit` / `StdioClose` / `SseClose` / `WsDispatch` /
  `MockHttp` → `Guard`；`DbInit` / `DbConn` → `Guard · Write`；`ModuleScan` → `Loop(Guard)`；
  `SMALL_Chat_Emit` / `SMALL_Generate_Emit` 去 `Retrieve`；`Async_Gather` / `PetActions_Emit`
  的模块级幻影 block 直接删除。
- **补 4 个精确名**（`link.toml`）：`read_edit_history` / `_recall_memories` /
  `_recall_knowledge` / `push_sessions`，`InputProcess`、`LightThink_Think`、`App_Emit`
  由弱证据升为强证据。
- **新增 `W011`（info）**：`check.py::_check_evidence` 在“E002 靠弱名通过”时额外报一条，
  使剩 6 条无法验证的声明（内存属性读、`Future`/队列等待、跨函数读库）每次运行都可见。
  info 级别不影响 `--strict` / `--strict-warnings`，因此不破坏 0 诊断门禁。
- **`weak_calls` 16 → 4**，只留 `get` / `to_dict` / `wait_for` / `get_instance`——每个都对应
  上面那 6 条中的实际无法验证读，其余 12 个泛名（`slice`/`collect`/`result`/`__aexit__`/
  `gather`/`popleft`/`post`/`get_url`/`initialize`/`exists`/`isdir`/`run_coroutine_threadsafe`）
  已无 block 依赖，直接删除。
- 验收：主干 `--strict` 0 error / 0 warning + 固定 6 条 W011；`--arch-update` 重新归档
  77 条指纹；`logiclink/tests` 108 → 111。

---

### 6. F8 诊断化 + W011 债账上限（2026-11-05）

- **`W012`（warning）— 同名符号歧义**：`use` 解析时，若同一短名的候选声明来自
  **多个模块**（按 `link.toml` 的扫描根判定模块边界），报一条 warning，列出模块与行号，
  提示改成 `module.Symbol`。
  - 只在 `use` 处判定，避免“声明了但没人接线”时的噪声；限定模块后不再报；
  - `.link` 的 `export`/`eq` 与同模块的 Python `block` 是同一个符号的两半，**不**算歧义
    （早期版本按“文件数 > 1”判定时在本仓误报 3 条，已改为按模块判定）；
  - 当前全仓 0 条 W012，`--strict` / `--strict-warnings` 不受影响。
- **`E011` + `[arch] max_weak_evidence` — 债账天花板**：`ArchConstraints` 新增
  `max_weak_evidence`（默认 `-1` = 不设限），`run()` 在收尾时统计 `W011` 条数，超过上限
  追加一条 `error[E011]` 并以 1 退出；W011 本身仍是 info。
  - `link.toml` 固定为 6（当前基线），降一条债就同步下调（ratchet）；
  - 想放宽必须显式改配置，杜绝“靠人记得别加”。
- **接线/反例用例**：
  - `test_ambiguous_unqualified_use_is_reported_as_W012` /
    `test_qualified_use_disambiguates_same_symbol` /
    `test_link_declaration_plus_python_block_is_not_ambiguous`；
  - `test_weak_evidence_ceiling_allows_baseline` /
    `test_weak_evidence_ceiling_fires_when_debt_grows` /
    `test_weak_evidence_ceiling_default_is_unlimited` /
    `test_max_weak_evidence_is_parsed`；
  - `test_config_wiring.py` 自动要求新字段被 `check.py` 引用。
- **验收**：`logiclink .`、`--strict`、`--strict-warnings` 三种调用均 exit 0 且输出
  `logiclink: ok (6 info)`；`logiclink/tests` 111 → 118。

## 八、待决策

> 已结：F5 → D2（Enum 状态机 + 真接线）、F9 → B4（逐链 E102 + `accept` 返回端口）、
> F1b → 21 条逐条核完（降级/删 12、补名 3、留 6 条 W011），弱原子 → **不新建 `RetrieveWeak`**，
> 保留 `weak_calls` 但用 W011 把这份债变成每次运行的可见输出（理由：另立原子会多一份
> 无证据规则的声明，而删泛名会让“内存读”这类真实取数无法表达）。
>
> 2026-11-05 两项也已决策并落地（见 §七·6）：
>
> 1. **F8 同名符号歧义 → `W012`（warning）**。判定放在 `use` 解析处而不是全量声明处：
>    只有“接线真的可能落错实现”时才报，零噪声（当前全仓 0 条）。限定模块
>    （`use a := alpha.ASym`）或“`.link` 声明 + 同模块 Python block”都不会误报。
> 2. **W011 天花板 → `[arch] max_weak_evidence = 6`（`E011` 硬门禁）**。W011 仍是 info
>    （不污染 `--strict` 的 0 诊断），但条数超过上限时额外报 `error[E011]` 并退出 1：
>    “只能降不能升”由机器执行；降一条就同步下调该值（ratchet）。

暂无其它待决策项。若后续要放宽上限，必须显式修改 `link.toml` 并在此记录理由。
