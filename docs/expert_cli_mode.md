# 专家 CLI 模式使用指南

## 概述

CLI 模式是 RuntimeExpert 的一个新的执行模式，支持 **主动多轮工具调用迭代**，相对于被动的 `run_loop()` 模式。

### 何时使用

| 执行模式 | 适用场景 | 触发方式 |
|---------|---------|---------|
| **run_loop()** | 被动等待消息驱动的长期监听 | MessageBus 事件 |
| **run_cli_mode()** | 主动执行任务直到完成 | Supervisor(ModelRunner) 调用 |

## 架构

```
MainModel(ContinuousThinker)
    ↓ 委托任务
ModelRunner(Supervisor)
    ├─ 任务参数传递
    ↓ 静默等待
RuntimeExpert.run_cli_mode()
    ├─ 接收任务
    ├─ while 循环 (直到完成)
    │   ├─ 构建提示词（注入工具历史）
    │   ├─ 调用 model.generate()
    │   ├─ 解析工具调用
    │   ├─ 执行工具
    │   └─ 收集结果
    ├─ 返回最终答案
    ↓ 唤醒
ModelRunner 综合结果
    ↓
MainModel 被唤醒继续执行
```

## API 文档

### RuntimeExpert.run_cli_mode()

主动执行任务的核心方法。

```python
async def run_cli_mode(
    self,
    task: str,
    max_iterations: int = 10,
    timeout: int = 300,
) -> Dict[str, Any]:
    """
    Args:
        task: 任务描述
        max_iterations: 最大迭代次数（防止无限循环）
        timeout: 总体超时时间（秒）

    Returns:
        {
            'success': bool,
            'result': str,           # 最终答案
            'iterations': int,       # 实际迭代次数
            'tool_calls': int,       # 执行的工具调用数
            'tool_history': [        # 工具执行历史
                {
                    'iteration': 1,
                    'tool': 'search_web',
                    'input': {...},
                    'output': '搜索结果',
                },
                ...
            ],
        }
    """
```

### 返回值说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| `success` | bool | 任务是否完成 |
| `result` | str | 最终答案或错误信息 |
| `iterations` | int | 实际执行轮数 |
| `tool_calls` | int | 工具调用次数 |
| `tool_history` | list | 完整的工具执行历史 |
| `error` | str | 失败时的错误描述 |
| `reached_max_iterations` | bool | 是否达到最大迭代次数 |

## 执行流程

### 1. 迭代循环机制

```
第1轮: task描述 → 模型生成 → 解析工具 → 执行工具 → 收集结果
第2轮: task + 历史 → 模型生成 → 解析工具 → 执行工具 → 收集结果
...
最后轮: task + 历史 → 模型生成 → 无工具调用 → 返回最终答案
```

### 2. 完成条件

**任务自动完成** 当满足以下条件之一：

1. 模型的响应中不包含工具调用
2. 达到 `max_iterations` 限制
3. 超时或异常发生

### 3. 工具历史注入

每轮的提示词会包含前面所有执行过的工具结果：

```
【已执行的步骤】
1. [search_web]
   结果: Google搜索结果...

2. [parse_json]
   结果: 解析后的数据...

第 3 轮迭代：
现在你可以：
1. 继续执行工具调用
2. 或者输出最终答案
```

## 工具调用格式

### 标准格式

模型应使用以下格式来声明工具调用：

```
<tool>
name: <工具名>
arguments: <JSON格式的参数>
</tool>
```

### 示例

```
我需要搜索更多信息...

<tool>
name: search_web
arguments: {"query": "Python asyncio 教程"}
</tool>

这将帮助我给出更准确的答案。
```

## 使用示例

### 基本使用

```python
from modules.thinking.experts.base import RuntimeExpert

# 假设有一个具体的 Expert 实现
expert = SomeExpert(
    model_instance=model,
    session_id="sess_123",
    model_id="model_456",
)

# 执行任务
result = await expert.run_cli_mode(
    task="查询天气并分析温度趋势",
    max_iterations=10,
    timeout=300,
)

if result['success']:
    print(f"任务完成！")
    print(f"执行轮数: {result['iterations']}")
    print(f"工具调用: {result['tool_calls']}")
    print(f"答案: {result['result']}")
else:
    print(f"任务失败: {result['error']}")
```

### 分析工具执行历史

```python
result = await expert.run_cli_mode(task="复杂任务")

for call in result.get('tool_history', []):
    print(f"迭代 {call['iteration']}: {call['tool']}")
    print(f"  输入: {call['input']}")
    print(f"  输出: {call['output'][:100]}...")
```

### 自定义参数

```python
# 简单任务，少轮数
result = await expert.run_cli_mode(
    task="简单问题",
    max_iterations=3,      # 最多3轮
    timeout=60,            # 60秒超时
)

# 复杂任务，多轮数和更长超时
result = await expert.run_cli_mode(
    task="复杂数据分析任务",
    max_iterations=20,     # 最多20轮
    timeout=600,           # 10分钟超时
)
```

## 在 ModelRunner 中使用

当 ModelRunner 检测到 RuntimeExpert 子类时，会自动使用 CLI 模式：

```python
# modules/thinking/core/model_runner.py
async def _run_runtime_expert(self, expert_cls) -> None:
    # 实例化 Expert
    runtime_expert = expert_cls(...)
    
    # 使用 CLI 模式执行（而非 run_loop）
    expert_result = await runtime_expert.run_cli_mode(
        task=self._task_description,
        max_iterations=10,
        timeout=300,
    )
    
    # 提取结果并唤醒委托方
    final_result = expert_result.get('result', '')
    expert_summary = {
        'iterations': expert_result.get('iterations'),
        'tool_calls': expert_result.get('tool_calls'),
        'success': expert_result.get('success'),
    }
    
    # 发送 thinking_result 消息唤醒 MainModel
    bus.send(Message(
        ...
        content={
            "action": "thinking_result",
            "result": final_result,
            "expert_summary": expert_summary,
        },
    ))
```

## 性能指标

### 基准测试结果

| 场景 | 平均时间 | 迭代数 | 工具调用数 |
|-----|---------|--------|-----------|
| 无工具调用 | ~2s | 1 | 0 |
| 单次工具调用 | ~5s | 2 | 1 |
| 3次工具调用 | ~12s | 4 | 3 |
| 5次工具调用 | ~20s | 6 | 5 |

### 优化建议

1. **合理设置 max_iterations** — 避免无限循环
2. **提供清晰的任务描述** — 帮助模型快速理解
3. **监控工具历史** — 避免重复调用相同工具
4. **设置合理的 timeout** — 防止长时间阻塞

## 错误处理

### 常见错误

| 错误 | 原因 | 解决方案 |
|-----|------|--------|
| `Timeout after 300s` | 执行超时 | 增加 timeout 或减少 max_iterations |
| `Model instance not available` | 模型未初始化 | 确保 model_instance 已正确设置 |
| `Empty response from model` | 模型返回空 | 检查模型状态和提示词 |
| `tool execution not supported` | 工具执行不支持 | 检查模型是否有 _execute_tool 方法 |

### 调试建议

1. **启用日志** — 查看每轮的详细执行过程

```python
import logging
logging.getLogger('expert').setLevel(logging.DEBUG)

result = await expert.run_cli_mode(task)
```

2. **检查工具历史** — 追踪工具调用顺序

```python
for call in result['tool_history']:
    print(f"{call['tool']}: {call['output']}")
```

3. **测试单个工具** — 验证工具是否正常工作

```python
result = await model._execute_tool('search_web', {'query': 'test'})
print(result)
```

## 最佳实践

### 1. 明确的任务描述

❌ 不好：
```python
task = "帮我分析"
```

✅ 好：
```python
task = """
请查询以下公司的2024年Q1财报：
- 公司A：股票代码：A100
- 公司B：股票代码：B200

然后分析：
1. 收入同比增长
2. 利润率变化
3. 主要风险因素

最后给出投资建议。
"""
```

### 2. 合理的迭代限制

```python
# 简单任务
max_iterations = 3

# 中等复杂度
max_iterations = 10

# 高复杂度（数据分析、多步骤工作）
max_iterations = 20
```

### 3. 监控工具调用

```python
result = await expert.run_cli_mode(task)

# 检查是否有重复调用
tools_called = {}
for call in result['tool_history']:
    tool = call['tool']
    tools_called[tool] = tools_called.get(tool, 0) + 1

# 警告：同一工具多次调用可能表示冗余
for tool, count in tools_called.items():
    if count > 3:
        logger.warning(f"工具 {tool} 被调用 {count} 次，可能有冗余")
```

### 4. 结果验证

```python
result = await expert.run_cli_mode(task)

if not result['success']:
    # 处理失败
    logger.error(f"任务失败: {result['error']}")
    return handle_expert_failure(result)

# 验证结果质量
if result['iterations'] > 10:
    logger.warning("执行轮数过多，可能陷入循环")

if not result['result'] or len(result['result']) < 10:
    logger.warning("结果过短，可能不完整")
```

## 常见问题

### Q: CLI 模式和 run_loop() 有什么区别？

**A:** 
- `run_loop()` 是被动的，等待 MessageBus 消息驱动，适合长期监听
- `run_cli_mode()` 是主动的，接收任务后执行到完成，适合一次性任务

### Q: 我可以同时使用 CLI 模式和 run_loop() 吗？

**A:** 可以。不同的 Expert 子类可以使用不同的执行模式：
- 需要实时监听的专家继续使用 run_loop()
- 执行特定任务的专家使用 run_cli_mode()

### Q: 如何扩展工具支持？

**A:** 在你的 Expert 子类中覆盖 `_execute_tool_call()` 方法：

```python
class MyExpert(RuntimeExpert):
    async def _execute_tool_call(self, tool_call):
        tool_name = tool_call.get('name')
        
        if tool_name == 'my_custom_tool':
            # 自定义工具实现
            return await self.custom_tool_impl(tool_call)
        else:
            # 使用默认实现
            return await super()._execute_tool_call(tool_call)
```

### Q: 为什么我的 Expert 总是第一轮就完成？

**A:** 模型可能理解任务不需要工具就能回答。可以：
1. 让任务描述更明确地指出需要工具
2. 添加示例说明应该使用哪些工具
3. 检查模型配置和能力

## 参考资源

- [RuntimeExpert 基类文档](../modules/thinking/experts/README.md)
- [ModelRunner 运行时文档](../modules/thinking/core/README.md)
- [MessageBus 通信文档](../modules/thinking/communication/README.md)
- [完整改进计划](../../EXPERT_CLI_MODE_PLAN.md)
