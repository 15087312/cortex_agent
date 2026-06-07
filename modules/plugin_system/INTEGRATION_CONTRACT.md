# 集成契约

本文档说明外部模型循环、旧版 tool-manager 代码和 API handler 应该如何使用插件工具。
它刻意放在 `modules/plugin_system` 内部，这样不用修改外部目录也能看到集成要求。

## 本地契约检查

```bash
python -m modules.plugin_system.integration_contract --json
```

该检查会输出要求、集成点、禁止模式和调用示例。
它不会扫描或修改外部目录。

## 必须使用的路径

面向模型的代码应该使用下面这些入口之一：

- `PluginToolService.list_tools()`
- `PluginToolService.invoke_tool_call()`
- `PluginToolManagerAdapter.list_tools()`
- `PluginToolManagerAdapter.execute_tool()`
- `ModelLoopToolAdapter.build_provider_tools()`
- `ModelLoopToolAdapter.execute_tool_calls()`
- `ModelLoopToolAdapter.append_tool_results_to_messages()`

标准调用链路是：

```text
provider payload
  -> provider parser
  -> PluginToolService
  -> ModelToolBridge
  -> LLMToolRuntime
  -> PluginEngine
  -> PluginGateway
  -> sanitized provider tool result
```

## Provider 结果规则

插件输出属于不可信工具数据。
宿主必须把它作为 provider tool result message 或等价的 provider-safe response 放回 provider 对话中。
不能把插件输出转换成 `system` message 或 `developer` message。

## 必须保留的安全控制

外部集成必须保留：

- 针对 OpenAI、Anthropic 和 generic 的 provider-specific tool schema 导出
- provider tool-call 解析
- request id 传递
- 参数 schema 校验
- 单工具权限范围
- 通过 Gateway 访问 memory、config、network、filesystem 和 output
- 确认、预算、限流、循环保护和幂等
- 返回结果 schema 校验
- 返回结果清洗和截断
- 安全错误码
- 不包含原始 args/results/secrets 的审计摘要

## 禁止模式

不要：

- 在模型循环中直接调用插件函数
- 在 provider 集成中直接调用 `PluginEngine.call_tool`
- 把 `include_hidden=True` 的工具作为默认工具暴露给普通 `model` actor
- 把原始 `data/plugins/<name>` 或模板目录当成已安装插件
- 记录原始工具参数、原始结果、密钥、stderr、traceback 或内部路径
- 把工具结果合并进高权限 prompt 指令

## OpenAI, Anthropic, Generic

当前 provider 适配层支持：

- OpenAI Chat Completions function calls
- OpenAI Responses function calls
- Anthropic `tool_use`
- generic 内部工具调用形态

Provider tool name 会通过服务响应 metadata 映射回内部 plugin/tool 标识。
外部调用方应该使用该映射，不要手动拼接或还原插件名。
