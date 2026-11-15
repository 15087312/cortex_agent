---
name: 工作流编排（CortexFlow）
description: "通过 cortexflow 工具创建、修改、删除、回滚、运行外部工作流引擎中的 DAG 工作流，并把节点级执行结果与失败定位带回对话。"
keywords: [工作流, workflow, cortexflow, 编排, 流程, DAG, 运行工作流, 执行流程, 流水线, pipeline, 自动化流程, 创建工作流, 修改工作流, 调整流程]
trigger:
  include: [工作流, workflow, cortexflow, 编排, 流水线, pipeline]
  exclude: [工作流引擎源码, cortexflow 项目开发]
  min_score: 1
metadata:
  version: 2
  type: builtin
id: cortexflow
---
你可以通过一组 `cortexflow_*` 工具**创建、修改、运行**本机 CortexFlow 引擎里的 DAG 工作流。你既能把它已有的流程当作能力来调用，也能直接给用户编排新流程。

## 工具一览

**读**
| 工具 | 用途 |
|---|---|
| `cortexflow_list_workflows` | 列出全部工作流（id/名称/状态/节点数） |
| `cortexflow_get_workflow` | 看某个工作流的定义：入口、变量、节点清单 |
| `cortexflow_list_examples` | 列出仓库自带示例模板 |
| `cortexflow_get_example` | 读示例 YAML 原文（当模板改） |
| `cortexflow_list_versions` | 看版本历史（回滚前取版本号） |
| `cortexflow_status` / `cortexflow_trace` | 查执行状态 / 看每节点耗时与报错 |

**写**（HIGH 风险，会改变系统状态）
| 工具 | 用途 |
|---|---|
| `cortexflow_validate_workflow` | 只校验定义，不保存 |
| `cortexflow_create_workflow` | 新建工作流 |
| `cortexflow_update_workflow` | 改定义/改名（upsert，自动记新版本） |
| `cortexflow_delete_workflow` | 删除（含全部版本历史，不可逆） |
| `cortexflow_rollback_workflow` | 回退到某个历史版本 |

**运行**
`cortexflow_run`（阻塞，默认最长 300s）/ `cortexflow_resume`（唤醒挂起）/ `cortexflow_cancel`

## 创建工作流的标准路径

1. **不确定 DSL** → `cortexflow_list_examples` 挑一个相近的，`cortexflow_get_example` 取原文当模板。
2. **写定义**（YAML 最好写，JSON 也行，工具都接受）：
   ```yaml
   id: my_flow            # 必须与 workflow_id 参数一致
   name: 我的流程
   entry: first           # 必须是真正的第一个节点，否则报 Unreachable
   config:
     default_tier: analyst
     role: assistant
   variables:
     question: {type: string, required: true}
   tiers:
     analyst: {model: deepseek-chat, provider: openai}
   nodes:
     first:
       type: model        # 节点类型必须在 schema 枚举里
       tier: analyst
       input: {text: "${question}"}
       output: answer
       on_success: done
     done:
       type: end
       end_mode: variable
       return_value: answer
   ```
3. **先校验**：`cortexflow_validate_workflow(definition)`。失败会返回具体字段错误。
4. **再保存**：`cortexflow_create_workflow(workflow_id, name, definition)`。
   它**默认也会先校验**，校验不过不会写入，并返回错误让你改——看到错误就按提示修正后重试，不要用 `skip_validate` 绕过。
5. **试跑**：`cortexflow_run` 验证行为是否符合预期。
6. **改坏了**：`cortexflow_list_versions` 拿版本号 → `cortexflow_rollback_workflow` 回退。

## 写定义时最容易踩的坑

- **`code` 节点禁止 `import`**：`re` / `json` / `math` / `datetime` 已注入，直接用。
- **`code` 节点没有 `Exception`**：不能写 `try/except`，用「先判空再处理」保证降级。
- **`entry` 必须指向真正的首节点**，否则校验报 `Unreachable nodes`。
- **分支的两条路都要汇合到同一变量**：例如 `A --条件1--> B`、`A --否则--> C`，若后续节点读 `${x.y}`，则 B 和 C 都要设置 `x`，否则一路会是空值。
- **`text` 节点拼接用 `text_action: concat`** + `items` / `separator`；`join` 是对单个列表分隔拼接，语义不同。
- **tier 名 ≠ 模型名**：actual model 由 `tiers.<名字>.model` 决定，`identity` 只是角色名。
- 节点类型只能取 schema 枚举值（model/tool/branch/loop/wait/subflow/state/parallel/map/http_request/transform/memory/retrieval/knowledge/agent/collaborate/skill/end/break/continue/aggregate/intent/ask/code/database/text/message/perception）。

## 运行时的关键约束

- **`cortexflow_run` 是阻塞的**：一直等到 completed / suspended / failed。长流程先告诉用户。
- **按 status 分流**：
  - `completed` → 结果在 `result`，直接汇报。
  - `suspended` → **不是失败**。看 `question`（在提问）或 `waiting_event`（等事件），用 `cortexflow_resume` 发回去。**问题必须转达给用户，不要自己编答案。**
  - `failed` → 用 `cortexflow_trace` 定位失败节点，报给用户。
- **不要用轮询代替 resume**：挂起后反复查 status 不会有进展。
- **变量必须合乎定义**：先 `cortexflow_get_workflow` 看 `variables`，不要猜。

## 破坏性操作的分寸

- `create` / `update` / `delete` / `rollback` 都会改变引擎状态。**改或删之前先 `cortexflow_get_workflow` 把原定义取出来**，必要时告诉用户「已备份，可回滚」。
- 用户只是问「能不能改」或「怎么改」时，先给方案；**明确说了要改再动手**。
- 删除是完全不可逆的（版本历史一起删），做之前请用户确认。

## 典型场景
- 「跑一下订单处理流程」→ list → get 确认输入 → run → 汇报
- 「给我做个把手动录入变成自动汇总的流程」→ list_examples → get_example → 写定义 → validate → create → run 验证
- 「这个流程第三步不对，把阈值从 30 提到 50」→ get → 改定义 → validate → update → run 验证
- 「改坏了，退回去」→ list_versions → rollback
- 工作流跑到一半问「确认退款吗」→ 原样转达用户 → 拿答复后 resume

## 不要用于
- 修改 cortexflow 引擎源码本身（那是项目开发，不是编排）
- 把工作流当万能 Shell；它只执行已注册的流程
