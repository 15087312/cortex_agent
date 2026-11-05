# logiclink（逻辑连接）— Link 语言与确定性检查器（v1）

Link 是一门极薄的**抽象逻辑注释语言**：代码块用项（term）描述抽象积木
（`Think`、`Tool`、`Loop(Think · Tool)`），用等式命名组合
（`eq ReAct = Loop(Think · Act)`）。`logiclink` 确定性地检查两件事：

1. 子块按注释树拼起来 == 父等式（否则 **E004**）；
2. 项里的每个原子在代码里都有证据（Python `ast` 存在性检查，否则 **E002**）。

`logiclink` 绿 ≠ 业务正确，只表示**声明的抽象拼图与标注/证据一致**。
模型可以提议修改等式，**不能**当最终裁判。

## 安装

```bash
pip install -e .
logiclink examples/agent_react   # 期望：logiclink: ok
```

无 `link.toml` 时使用内置默认原子表，照样能跑。

## 用法约定

1. 仓库根目录只放一份 `link.toml`（宪法），不要每个模块复制。
2. 模块旁的 `*.link` 写 `export` 与 `eq`；上层 `eq` 组合导出名，不展开底层 `Think`。
3. 函数上只写 `# @link:block <Name>`，函数体内细粒度步骤用行内 `# @link:block`。
4. 改 `eq`/`alias` 视为架构变更，应人工审；模型默认只改代码去贴合等式。
5. `·`（或 `>>`）顺序有意义，不可交换；`|` / `||` 按书写顺序比较。

```bash
logiclink                 # 从 cwd 读 link.toml，扫 roots
logiclink path/           # 指定根
logiclink --strict        # W010（多余证据）变 error
logiclink --arch-update   # 归档当前 eq/alias 指纹到 .logiclink/arch.json
logiclink --allow-arch    # 放行本次 eq/alias 漂移（仍打印变更清单）
logiclink --json          # 机器可读诊断
logiclink --explain ReAct # 打印 ReAct 展开后的 term
```

退出码：有 error → 1；仅 warning → 0（`--strict-warnings` 下 warning 也 → 1）。

## 最小示例

```python
# @link:eq ReAct = Loop(Think · Guard · (Tool · Observe | Emit))
# @link:block ReAct
def react(state):
    while True:
        # @link:block Think
        msg = llm.invoke(state)
        # @link:block Guard
        if not getattr(msg, "tool_calls", None):
            # @link:block Emit
            return msg
        # @link:block Tool
        out = tools.invoke(msg.tool_calls[0])
        # @link:block Observe
        state = observe(state, out)
```

删掉 `llm.invoke` 一行 → `error[E002]: missing evidence for Think`，退出 1。

## 错误码

| 码 | 含义 |
|---|---|
| E001 | 解析失败 / 未登记标识符 |
| E002 | 项中原子在 block 区间无证据（含无证据规则） |
| E003 | 同名 `eq`/`alias` 重定义不一致，或 block 项与同名 `eq` 不一致 |
| E004 | 父 block 声明项 ≠ 子 block 组合（报告 `missing:`/`extra:` 原子集合差） |
| E005 | 符号重名 / `use` 悬空 / `use n := module.Symbol` 的符号不属于该 module（两段都校验） |
| E102 | 输出既没被后继 input 接住，也没被调用方 `accept` 接住 |
| E104 | `accept` 声明了没有任何被调用方产出的原子 |
| E006 | `@ file:` / `fn:` 挂接失败 |
| E305 | eq/alias 与 `.logiclink/arch.json` 基线不一致（新增/删除/改动），或门禁已开但基线缺失 |
| E011 | 弱证据债超过 `[arch] max_weak_evidence` 上限（超过一条即失败，除非把上限调高） |
| W010 | 区间内有已登记原子调用但项未包含（`--strict` 变 error） |
| W011 | 该原子只有弱证据（泛名）撑着：E002 通过，但不能算“已验证”（info，不卡门禁） |
| W012 | `use` 同名歧义：同一个短名由多个模块声明，未限定模块时可能接错实现（`--strict` 变 error） |

`[arch] forbid_eq_change = true` 是**基线门禁**：它不再拒绝运行，而是把当前 eq/alias
的规范化 AST 指纹与 `.logiclink/arch.json` 对比，只有真正的新增/删除/改动才报 `E305`
（`·` 与 `>>`、空白、注释差异不算改动）。首次启用需人工执行一次 `--arch-update`
归档基线，该文件应提交进版本库；`--allow-arch` 只放行本次运行，不放宽后续。

v1 说明：相等一律走 normalize 后的 AST（`ast_terms.equal`），禁止字符串比较；
E004 先试严格结构相等，不等则退化为原子集合差比较（顺序/分支结构差异在 v1
容忍、有集合缺失或多余才报错）；证据只做存在性检查，不证明顺序与业务正确性。
同一原子配置多个证据条件（calls/returns/requires_branch）时，任一满足即算有证据。

### 强证据与弱证据（`calls` / `weak_calls`）

```toml
[atoms.Retrieve]
calls = ["retrieve", "recall", "_vector_search"]   # 强：既能证 E002，也能触发 W010
weak_calls = ["get", "to_dict", "wait_for"]        # 弱：只能证 E002，永不触发 W010（只留仍在用的）
```

弱名是低区分度的通用 API（`dict.get`、`Future.result`、`__aexit__`）。它们能证明
“这里确实取过东西”，但不能反推“作者漏写了 Retrieve”——否则每个碰字典的 block 都会
报 W010，`--strict` 直接不可用。把泛名放 `calls` 就是精度事故；放 `weak_calls`
才有可用信号。

**但弱证据不能是藏身处**：只靠弱名通过的声明会额外报 `info[W011]`，把“这条原子其实
没被验证”摆在每次运行的输出里。W011 是 info 级别，`--strict` / `--strict-warnings`
都不因此失败，因此它是一份**可被机器统计的债**，而不是把台账写在文档里靠人记得。
两条出路：给原子补一个真实的调用名进 `calls`，或把该原子从项里删掉（本层看不见的
取数，例如内存属性读、跨函数的 `store.get_history`、`asyncio.Queue.get`，就不该计入）。

**天花板由机器执行**：`[arch] max_weak_evidence = 6` 把“只能降不能升”变成门禁——
W011 超过上限时额外报 `error[E011]` 并退出 1。降掉一条债时把该值同步下调（ratchet），
想放宽必须显式改 `link.toml`，而不是靠每个人都记得“别加”。

主干当前不变量：`logiclink . --strict` **0 error / 0 warning**，固定 6 条 W011（已登记的
弱证据债，逐条见 `docs/CHECKER_AUDIT.md` §四·F1b）。输出末尾仍是 `logiclink: ok (6 info)`
——“ok” 是门禁结论，括号里的数字是债。丢掉这个不变量视为回退。

## v2：全局验证（组装 / 数据流 / 调用图 / 状态机）

v1 验证"块内"，v2 在 `logiclink/src/logiclink/v2_flow.py`、`v2_datav.py`、
`v2_graph.py`、`v2_state.py` 实现四层全局检查（设计见 `docs/V2_DESIGN.md`）：

| 层 | 语法 | 错误码 |
|---|---|---|
| L1 组装 | `use name := module.Symbol` + `input Sym: [...]` / `output Sym: [...]` / `accept Sym: [...]` | E101 输入未满足 / E102 输出悬空 / E103 端口不匹配 / E104 accept 凭空声明 |
| L2 数据流 | `Atom(var)`、`Atom(v@"channel")`、`Atom(v:Type)` | E201 未定义先使用 / E202 产出未消费 / E203 类型不匹配 / E204 通道未接线 |
| L3 调用图 | `--graph`（真实 import 边 vs `use`/`eq` 声明边） | E301 声明无真实 / E302 真实未声明 / E303 方向相反 / E304 模块间循环依赖（W304 = 已在 `allow_cycles` 白名单） |
| L4 状态机 | `state NAME` + `init = ...` + `trans = "s1 --event--> s2"`（可选 `enum = TurnState`） | E401 非法状态 / E402 声明转换未实现 / E403 转换未声明 |

```bash
logiclink . --graph              # mermaid 全链路图（声明 vs 真实）
logiclink . --graph --drift-only # 只看声明/真实差异
logiclink . --allow-arch         # 根 app.link 已接入主链路
```

端口声明写在模块 `.link` 的 `export` 之后；根 `app.link` 声明 `use` 与主链路
`eq`。L3 会如实报告未声明的真实耦合（E302）——这是工具的设计产出，不是噪声。

### 返回端口：`accept`

链路 `a · b` 表示 `a` 调用 `b`。`b` 的产出有两个合法去处：**向后**交给 `b` 的后继的
`input`，或**返回**给调用方 `a` 的 `accept`：

```
output Distrib: [Emit]
accept App: [Emit]        # app 调用 Distrib 后真的接住 Emit
```

E102 因此不再看“谁恰好在链尾”，也不再把“别的链上有人消费”当作自证：

- 链中模块：产出必须被**同一链**里后继的 `input` 接住（`a · b · c` 里 `b` 的留给
  `c` 的 input）；
- 链尾模块：产出必须被**调用方**的 `accept` 声明接住，否则报 E102；
- 链头模块不受此约束：链头的 `output` 是它自己给“它的调用方”的契约，不是要
  全部塞给下游（`app` 声明 `[Think, Write, Emit]` 不等于 `think` 要接三样）。

两条防滥用规则：`accept` 只能写**真实会被接住**的原子——`E104` 会报“这个原子没有任何
被调用方产出”（否则 `accept X: [一切]` 就是个全局消音器）；链头**完全没有端口契约**时
该链不做数据流判定（例如只记录 import 依赖的 `eq`），不会逼人写假声明。

代价要写清：**这类链的 L1 数据流实际不覆盖**，E102 的覆盖面取决于链头是否写了端口契约。
这是刻意取舍（宁可承认“这条链没查”，也不靠假声明凑出一份全绿台账）；给链头补一条真实
`input`/`output` 才会让该链重新进入判定。

模块的**扫描根由 `use name := module.Symbol` 的 module 段决定**（在 `scan_roots` 中找同名
目录），符号只用来证明该模块确实被接线。因此把 export 符号挪进 `module/core/` 不会再把
扫描范围静默缩到子目录；声明边也只在**同一分支内**求相邻（`a · (b | c) · d` 不会产生 `b→c`）。

checker 自身的失效模式（空真守卫、渲染/判定分叉、配置不接线、声明即消毒）与分阶段
修复计划见 [`docs/CHECKER_AUDIT.md`](docs/CHECKER_AUDIT.md)；写 `.link` 的误写模式见
[`docs/AUDIT_LESSONS.md`](docs/AUDIT_LESSONS.md)。

L4 的真实写法是绑定 Enum（`python` 里状态几乎都是 Enum，不是字符串字面量）：

```
state turn
enum = TurnState
init = idle
trans = "idle --transition_to--> planning"
```

此时证据是 `TurnState.X` 的**赋值/传参**（如 `transition_to(TurnState.PLANNING)`），
只读（`state in (TurnState.A,)`）不算“到达”；E402 也据此判定“声明的转换没有任何一处
把该状态设进去”。声明文件的模块根按扫描根 / `<module>/<module>.link` 向上查找，所以
机器写在 `module/core/` 也能覆盖整个模块。

E304 只看**真实 import**：模块 A、B 互相 import 构成强连通分量即判环，声明
`eq Flow = a · b` 与 `eq Back = b · a` 只是把环写进文档，不会让它消失。一个环报
一条，环内边不再重复报 E302。确属设计需要的双向耦合可白名单放行，降级为 W304：

```toml
[arch]
allow_cycles = [["desktop_pet", "thinking"]]
```

### 代码块召回与全景图

```bash
logiclink . --recall              # 列出全部 244 个命名代码块
logiclink . --recall App          # 按名召回代码块（函数级返回整函数，行内返回注释行）
logiclink . --html out.html       # 全仓库模块依赖全景（15 模块 × 真实 import 双向边，可交互）
logiclink . --html-flow out.html  # wired 主链路精炼视图
logiclink . --html-detail out.html # 三层钻取：模块间（边标注发起代码块）/ 模块内（eq 展开）/ 逻辑节点（244 block 按 eq 分组）
```

全景图按真实 import 计算模块间依赖（长包名优先，避免同名目录误配），
wired 模块之间的边按 L3 着色（已声明/未声明/方向），未治理边灰色显示。
