# 思考模块内部架构

## 核心执行模型：双层 ReAct 循环

### 概念

思考模块采用**认知上下文**与**操作上下文**分离的双层设计：

| 维度 | 思考轮次 (Round) | 工具循环 (Turn) |
|------|-----------------|-----------------|
| **对应代码** | `ContinuousThinker.continuous_think()` | `ModelRunner._generate_with_tools()` |
| **上下文管理** | 每轮完整重建 prompt | 每 turn 增量追加 assistant/tool 消息 |
| **注入内容** | 黑板状态、记忆检索、感知上下文、委托进度 | 上一轮工具调用结果 |
| **频率** | 低（每轮一次 prompt 构造） | 高（每次工具调用后追加） |
| **目的** | 让模型看到最新全局认知状态 | 让模型记住当前操作历史 |

### 运行流程

```
ModelRunner._think_loop()                          ← 会话循环（跨委托）
  │
  ├─ ContinuousThinker.continuous_think()          ← 思考轮次
  │   │
  │   ├─ round 0: _build_prompt()                  ← 重建上下文
  │   │     → 黑板切片（目标/委托/专家发现）
  │   │     → 记忆检索（向量 + 关键词）
  │   │     → 感知上下文（屏幕/窗口状态）
  │   │     → 良知引导（价值观内化）
  │   │
  │   └─ _generate_with_tools()                    ← 工具循环（ReAct）
  │       │
  │       ├─ turn 0: chat → tool_calls[web_search]
  │       │          → MCP 执行 → 5 results
  │       │          → 结果注入 messages → continue
  │       │
  │       ├─ turn 1: chat（带搜索结果）
  │       │          → tool_calls[web_fetch]
  │       │          → MCP 执行 → 页面内容
  │       │          → 结果注入 messages → continue
  │       │
  │       ├─ turn 2..N: chat → 工具执行 → ...
  │       │
  │       └─ turn N: chat → 无 tool_calls
  │                  → 输出最终文本 → return
  │
  ├─ 检测委托 → break → _wait_for_wakeup(300s)
  │   收到 thinking_result → 重建 prompt → 新一轮
  │
  └─ 无委托 + continue=false → _notify_thinking_complete
```

### 为什么是双层而不是单层

如果单层——每调一次工具就重建 prompt（黑板 + 记忆 + 感知）——每次工具调用的 token 消耗和延迟是不可接受的。

如果只用工具循环——永远不重建 prompt——模型看不到其他模型写入黑板的发现、委托完成状态等全局信息。

双层分离让重建只在需要时发生（新任务、被唤醒），而工具执行的连续上下文保持在 messages 数组中增量追加。

### 跨模型协作

```
指挥 ModelRunner
  while _running:
    continuous_think → delegate_task(主管) → 退出 → _wait_for_wakeup
                                                      ↓
                                                主管 ModelRunner
                                                  while: continuous_think → delegate_task(专家) → wait
                                                                                           ↓
                                                                                      专家执行
                                                                                工具循环 → 完成
                                                      ↑ thinking_result
    被唤醒 → reset → 新一轮 continuous_think → 整合 → 完成
```

三个层级（指挥/主管/专家）共用同一套 `continuous_think` + `_generate_with_tools` 执行模型，仅通过 `max_rounds` 和 tier 相关的退出条件差异化。

### 关于 RuntimeExpert

`RuntimeExpert` 是一个预留的抽象基类，用于将来可能需要不同生命周期管理的专家类型（如持久运行的安全监控专家）。当前所有专家都走 `_think_loop` → `_generate_with_tools` 路径，`RuntimeExpert.run_cli_mode` 未被生产环境使用。
