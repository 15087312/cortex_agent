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
| E005 | 符号重名 / `use` 悬空 |
| E006 | `@ file:` / `fn:` 挂接失败 |
| E305 | eq/alias 与 `.logiclink/arch.json` 基线不一致（新增/删除/改动），或门禁已开但基线缺失 |
| W010 | 区间内有已登记原子调用但项未包含（`--strict` 变 error） |

`[arch] forbid_eq_change = true` 是**基线门禁**：它不再拒绝运行，而是把当前 eq/alias
的规范化 AST 指纹与 `.logiclink/arch.json` 对比，只有真正的新增/删除/改动才报 `E305`
（`·` 与 `>>`、空白、注释差异不算改动）。首次启用需人工执行一次 `--arch-update`
归档基线，该文件应提交进版本库；`--allow-arch` 只放行本次运行，不放宽后续。

v1 说明：相等一律走 normalize 后的 AST（`ast_terms.equal`），禁止字符串比较；
E004 先试严格结构相等，不等则退化为原子集合差比较（顺序/分支结构差异在 v1
容忍、有集合缺失或多余才报错）；证据只做存在性检查，不证明顺序与业务正确性。
同一原子配置多个证据条件（calls/returns/requires_branch）时，任一满足即算有证据。

## v2：全局验证（组装 / 数据流 / 调用图 / 状态机）

v1 验证"块内"，v2 在 `logiclink/src/logiclink/v2_flow.py`、`v2_datav.py`、
`v2_graph.py`、`v2_state.py` 实现四层全局检查（设计见 `docs/V2_DESIGN.md`）：

| 层 | 语法 | 错误码 |
|---|---|---|
| L1 组装 | `use name := module.Symbol` + `input Sym: [...]` / `output Sym: [...]` | E101 输入未满足 / E102 输出悬空 / E103 端口不匹配 |
| L2 数据流 | `Atom(var)`、`Atom(v@"channel")`、`Atom(v:Type)` | E201 未定义先使用 / E202 产出未消费 / E203 类型不匹配 / E204 通道未接线 |
| L3 调用图 | `--graph`（真实 import 边 vs `use`/`eq` 声明边） | E301 声明无真实 / E302 真实未声明 / E303 方向相反 / E304 模块间循环依赖（W304 = 已在 `allow_cycles` 白名单） |
| L4 状态机 | `state NAME` + `init = ...` + `trans = "s1 --event--> s2"` | E401 非法状态 / E402 声明转换未实现 / E403 转换未声明 |

```bash
logiclink . --graph              # mermaid 全链路图（声明 vs 真实）
logiclink . --graph --drift-only # 只看声明/真实差异
logiclink . --allow-arch         # 根 app.link 已接入主链路
```

端口声明写在模块 `.link` 的 `export` 之后；根 `app.link` 声明 `use` 与主链路
`eq`。L3 会如实报告未声明的真实耦合（E302）——这是工具的设计产出，不是噪声。

E304 只看**真实 import**：模块 A、B 互相 import 构成强连通分量即判环，声明
`eq Flow = a · b` 与 `eq Back = b · a` 只是把环写进文档，不会让它消失。一个环报
一条，环内边不再重复报 E302。确属设计需要的双向耦合可白名单放行，降级为 W304：

```toml
[arch]
allow_cycles = [["desktop_pet", "thinking"]]
```

### 代码块召回与全景图

```bash
logiclink . --recall              # 列出全部 250 个命名代码块
logiclink . --recall App          # 按名召回代码块（函数级返回整函数，行内返回注释行）
logiclink . --html out.html       # 全仓库模块依赖全景（15 模块 × 真实 import 双向边，可交互）
logiclink . --html-flow out.html  # wired 主链路精炼视图
logiclink . --html-detail out.html # 三层钻取：模块间（边标注发起代码块）/ 模块内（eq 展开）/ 逻辑节点（250 block 按 eq 分组）
```

全景图按真实 import 计算模块间依赖（长包名优先，避免同名目录误配），
wired 模块之间的边按 L3 着色（已声明/未声明/方向），未治理边灰色显示。
