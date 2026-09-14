# Logiclink v2 完整设计方案 —— 补全"块间 / 端到端 / 数据流"缺陷

> 状态：设计稿（待实现）
> 版本：v2.0
> 关联：`/Users/abc/PycharmProjects/ai_backend/link.toml`（仓库宪法）

---

## 一、背景与目标

### 1.1 现状（v1 已实现）

logiclink v1 做到了"块内验证"：给每个模块定义行为序列（eq），并检查代码块内是否
有对应的原子证据（调用 / 分支 / 返回）。

```
eq ThinkLoop = Loop(Retrieve · Think · Guard · (Write | Emit))
eq GateCheck = Guard · Think · Emit
eq PetChat   = Guard · Retrieve · Think · Write · Emit
eq App       = Guard · Retrieve · Think · Write · Emit
```

这相当于拿到了**每层楼的设计图纸**——每个房间的用途一目了然。

### 1.2 缺陷（v1 看不到的）

| 缺口 | 现状 | 后果 |
|------|------|------|
| **跨模块调用关系** | `use` 声明 0 个 | 模块之间怎么接线完全没记录 |
| **端到端路径** | 无主链路图 | 输入→感知→思考→记忆→输出 只能靠人脑拼 |
| **真实代码覆盖** | 100+ py 文件仅 ~25 个有块级标注 | 大多只有顶层空标注或没有 |
| **数据流** | 无变量绑定 | write 的数据从哪来、retrieve 去哪，没记录 |
| **状态转换** | 无状态机 | session / blackboard 状态流转不可验证 |
| **真实调用核对** | 无 call graph | 声明了但真实没接线，或真实接了线但没声明，都发现不了 |

### 1.3 目标（v2 要做成）

从"每层楼的设计图纸"升级为"整栋楼的完整工程图"：

```
v1: 块内验证               v2: 全局验证
eq A = think·emit    →     输入 → 感知 → 思考 → 记忆 → 输出 的完整链路可验证
```

---

## 二、缺陷架构图（要补的 4 层）

```
┌─────────────────────────────────────────────┐
│ L4 状态流转   state 声明：session/blackboard │
├─────────────────────────────────────────────┤
│ L3 调用图核对 --graph：声明 vs 真实 call     │
├─────────────────────────────────────────────┤
│ L2 数据流     变量绑定：Think(p) → Write(p)  │
├─────────────────────────────────────────────┤
│ L1 组装       use 声明：模块间接线           │
├─────────────────────────────────────────────┤
│ L0 骨架(已有)  eq 块内序列                   │
└─────────────────────────────────────────────┘
```

---

## 三、各层设计

### L1 — 接口 + 组装层（`use`）

#### 模块接口声明（新增 `.port`，写在各模块 `.link` 顶部）

```toml
# modules/thinking/core/thinking.link
#[port]
export ThinkLoop
input:  [Guard, Retrieve]      # 要别人喂给它的
output: [Write, Emit]          # 它生产出去的
```

#### 组装声明（根 `app.link`）

```toml
use perceive := perception.PerceiveLoop
use think    := thinking.ThinkLoop
use memory   := memory.MemRetrieve
use output   := output.Distrib

# 端到端主链路 — 声明即文档即验证
eq MainFlow = perceive · think · memory · output
eq AltFlow  = perceive · think | memory | think
```

#### 验证规则（新 E100 系列）

| 码 | 含义 | 说明 |
|----|------|------|
| **E101** | 输入未满足 | `think` 声明 `input=[Guard, Retrieve]`，但前序 `perceive` 的 `output` 不含这些 |
| **E102** | 输出悬空 | 模块 `output=[Emit]` 但没有任何后继消费它 |
| **E103** | 端口不匹配 | 拼接的原子集合对不上（类型即接口，原子即类型） |

---

### L2 — 数据流层（变量绑定）

#### 语法扩展

```toml
# 块内绑定：Think 生产 answer，Write 消费 answer
eq ThinkLoop = Retrieve(q_prompt) · Think(answer) · Write(answer)

# 跨块显式接线：q 走 thinker.question 通道
eq Pipeline = Think(q@"thinker.question") · Write(q)
```

#### 验证规则（新 E200 系列）

| 码 | 含义 | 说明 |
|----|------|------|
| **E201** | 未定义先使用 | `Write(answer)` 但前面没有 `Think(answer)` 产出 |
| **E202** | 未使用产出 | 产出的变量没有任何消费 |
| **E203** | 类型不匹配 | `Retrieve(q)` 产出 `query` 类型，`Write(q)` 期望 `result` 类型 |
| **E204** | 通道未接线 | `q@"thinker.question"` 声明了但没有任何模块 publish 该通道 |

#### 实现要点

- term 语法加 `Atom(vars...)` 绑定
- 变量在同一 `eq` 内做**定义-使用图**检查
- 跨块用 `@channel` 引用解析

---

### L3 — 调用图核对层（`--graph`）

#### 新增命令

```bash
logiclink --graph                 # 生成真实调用图（mermaid/dot）
logiclink --graph --drift-only    # 只输出声明 vs 真实 的差异
```

#### 原理

从 AST 提取真实 call（`A.f → B.g`），与 `use` / `eq` 声明比对。

#### 验证规则（新 E300 系列）

| 码 | 含义 | 说明 |
|----|------|------|
| **E301** | 声明未接线 | eq 声明了 `perceive · think`，但真实代码里 `PerceiveLoop` 没调用 `ThinkLoop` 任何入口 |
| **E302** | 真实调用未声明 | 真实存在 `A→B` 调用，但没有任何 `use`/eq 提到 |
| **E303** | 反向依赖 | 声明的依赖方向和真实调用方向相反 |

#### 输出示例

```bash
logiclink --graph
  perceive → think  ✓ (声明 ✓ 真实 ✓)
  think    → memory ✓ (声明 ✓ 真实 ✓)
  memory   → output ✗ (声明有，真实无调用)
  error_bus → ws    ✗ (真实有，声明无)
```

---

### L4 — 状态流转层（state）

#### 语法扩展

```toml
# 状态机声明
# context state  blackboard
# init: empty
# trans: empty --AcceptEvent--> partial
# trans: partial --merge--> complete
# trans: complete --flush--> empty

[state.blackboard]
init = "empty"
transitions = [
  "empty --AcceptEvent--> partial",
  "partial --merge--> complete",
  "complete --flush--> empty",
]
```

#### 验证规则（新 E400 系列）

| 码 | 含义 | 说明 |
|----|------|------|
| **E401** | 非法转换 | 代码在 `empty` 状态直接调用了 `flush`（必须先在 `partial/complete`） |
| **E402** | 未覆盖转换 | 声明了 `partial --merge--> complete`，但代码里找不到 `merge` 调用 |
| **E403** | 未声明转换 | 代码里有 `A → B` 状态变更，但 state 声明没有这条 |

---

## 四、语法总结（v1 → v2 新增）

```
# v1 已有
export NAME                       # 导出
eq NAME = ...                     # 行为序列
alias NAME = ...                  # 别名
block NAME [= ...]                # 代码块标注（# @link:block）
use NAME                          # v1 的 use（引用，暂未启用）

# v2 新增
use name := module.Symbol         # 组装 / 接线
input: [Atom...]                  # 端口契约（输入）
output: [Atom...]                 # 端口契约（输出）
Atom(var)                         # 数据流变量绑定
Atom(v@"channel")                 # 跨块通道
state / [state.name]              # 状态机声明
transitions = [                   # 状态转换表
  "s1 --event--> s2",
]
--graph                           # 调用图渲染
```

---

## 五、新错误码总表

| 系列 | 码 | 含义 |
|------|-----|------|
| **组装** | E101 | 前模块输出不满足后模块输入 |
| | E102 | 输出悬空（无后继消费） |
| | E103 | 端口原子不匹配 |
| **数据流** | E201 | 未定义先使用 |
| | E202 | 产出未消费 |
| | E203 | 变量类型不匹配 |
| | E204 | 通道未接线 |
| **调用图** | E301 | 声明有接线、真实无调用 |
| | E302 | 真实有调用、声明无 |
| | E303 | 依赖方向相反 |
| **状态机** | E401 | 非法状态转换 |
| | E402 | 声明转换未实现 |
| | E403 | 代码转换未声明 |

### v1 原有错误码（保留）

| 码 | 含义 |
|----|------|
| E001 | 解析错误 |
| E002 | 缺少证据 |
| E003 | 定义冲突 |
| E004 | 跨文件遮蔽 |
| E005 | 重复导出 |
| E006 | Hook 失败 |
| E007 | 缺少导入 |
| E008 | 缺少上下文 |
| E009 | 禁止调用 |
| E010 | 复杂度超限 |
| E011 | 缺少文档 |
| E012 | 架构约束违反 |
| W010 | 冗余证据 |

---

## 六、实现步骤

| 步骤 | 内容 | 产出 |
|------|------|------|
| 1 | parser 扩展：`use`/`input`/`output`/变量绑定/`state` 语法 | 新 AST 节点 |
| 2 | collect 扩展：跨文件收集 `use` 与端口 | symbol 表 |
| 3 | 组装检查器（L1）：端口匹配 + 悬空检测 | E101-103 |
| 4 | 数据流检查器（L2）：块内定义-使用图 + 通道解析 | E201-204 |
| 5 | 调用图提取器（L3）：从 AST 建真实 call graph | E301-303 |
| 6 | 状态机检查器（L4）：转换合法性 + 覆盖度 | E401-403 |
| 7 | `--graph` 输出 mermaid/dot 渲染 | 可视化 |
| 8 | 工程接入：根 `app.link` 补全 3 条主链路 + 各模块端口声明 | 全仓绿 |

---

## 七、验证方案

1. **单元测试**：每层新增独立测试
   - `tests/test_flow.py`（L1 组装）
   - `tests/test_datav.py`（L2 数据流）
   - `tests/test_graph.py`（L3 调用图）
   - `tests/test_state.py`（L4 状态机）

2. **基准项目**：`examples/agent_react/` 扩成 4 模块示例，作为端到端验收

3. **全仓门禁**：补完端口声明后 `logiclink --strict` 全绿

4. **人工抽查**：对 `main.py` 的 App 链路、`tool_security_gate` 的 GateCheck 链路人工核对正确性

---

## 八、落地优先级

| 优先级 | 层 | 理由 |
|--------|-----|------|
| **P0** | L1 组装（use/port） | 最缺，直接回答"端到端路径" |
| **P0** | L3 调用图 | 自动对照真实代码，最能暴露问题 |
| **P1** | L2 数据流 | 验证"谁产出谁消费" |
| **P1** | `--graph` 可视化 | 让"看完整逻辑"变成看得見的东西 |
| **P2** | L4 状态机 | 项目状态最复杂但投入产出比次之 |

---

## 九、试点建议

先做 P0 的 L1 组装 + L3 调用图（这两层直接让你"看到端到端路径 + 发现真实接线问题"）。

如果想先看效果，可以先给项目写一个试点 `app.link`，把
`感知 → 思考 → 记忆 → 输出` 三条主链路显式接出来，让 `--graph` 先渲染出第一张全链路图。

**最终状态**：v2 完成后，`logiclink . --strict` 能保证：

- 每个导出的 eq 都有对应代码实现（声明即实现）
- 模块间接线方向与真实调用一致（声明即事实）
- 数据变量先产出后消费（声明即数据契约）
- 状态转换合法且实现完整（声明即状态机）