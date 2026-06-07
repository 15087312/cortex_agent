# 插件作者指南

本文档说明 `modules/plugin_system` 中可被模型调用的插件契约。

## 插件结构

```text
plugin.yaml
config_schema.json
README.md
src/
  __init__.py
  main.py
tests/
  test_placeholder.py
```

可以用下面的命令生成这种结构：

```bash
python -m modules.plugin_system.scaffold data/plugins my_plugin --tool-name echo
python -m modules.plugin_system.scaffold data/plugins my_hello --template hello_world
python -m modules.plugin_system.scaffold data/plugins my_lookup --template network_lookup_tool
python -m modules.plugin_system.scaffold --template production-package --output /tmp/plugin_package_example
python -m modules.plugin_system.scaffold --template readonly-retrieval --output /tmp/readonly_retrieval_example
python -m modules.plugin_system.scaffold --template controlled-network --output /tmp/controlled_network_example
python -m modules.plugin_system.scaffold --template file-summary --output /tmp/file_summary_example
```

也可以从 `modules/plugin_system/templates/` 复制模板：`hello_world`、`read_only_memory_tool`、`network_lookup_tool`、`admin_only_output_tool`、`production_plugin_package`、`readonly_retrieval_plugin`、`controlled_network_plugin` 和 `file_summary_plugin`。

注意：`data/plugins` 或 `modules/plugin_system/templates` 下的原始目录只是源码/示例内容。
只有经过安装、权限审批和启用流程后，它才是正式安装、已批准、已启用并且模型可见的插件。
状态快照会把这些原始示例单独报告为 `raw_example_plugins` 和 `not_installed_examples`。

## Manifest

每个插件都必须在插件根目录提供 `plugin.yaml`：

```yaml
name: example_tool
version: 1.0.0
description: Example model-callable plugin.
author: plugin-author
license: MIT

extensions:
  - type: tool
    name: echo
    entry: src.main:echo
    description: Echo text supplied by the model.
    params:
      text:
        type: string
        description: Text to echo.
        required: true
        maxLength: 256
    returns:
      type: object
      required: [text]
      properties:
        text:
          type: string
      additionalProperties: false
    permissions:
      - compute: true

permissions:
  - compute: true

runtime:
  mode: sub_process
  trust: third_party
  timeout_seconds: 5
  max_concurrency: 1
```

工具入口函数接收 `(args, api=None)`，并返回可 JSON 序列化的数据。

## 参数 Schema

工具的 `params` 描述模型传入的参数。
支持的参数类型包括 `string`、`number`、`integer`、`boolean`、`array` 和 `object`。
需要时可以使用 `enum`、`items`、`properties`、`additionalProperties`、`maxLength`、`minLength`、`maximum` 和 `minimum`。
必填参数用 `required: true` 标记。

## 返回值 Schema

每个可被模型调用的工具都应该声明 `returns`。
运行时校验支持与 params 相同的 JSON Schema 子集。
如果返回数据违反 `returns`，宿主会拒绝该结果，并记录 `plugin.tool_return_schema_violation`。

## 单工具权限

插件级 `permissions` 是权限上限。
每个工具应该声明自己实际需要的精确权限。
没有声明单工具权限的工具，默认不会获得任何敏感 Gateway 资源权限。

敏感权限包括：

- `memory.read`
- `memory.write`
- `config.read`
- `network.outbound`
- `fs.read`
- `fs.write`
- `output.send`

使用注入的 Gateway `api`；不要直接访问网络、文件系统、memory、config 或 output 系统：

```python
def lookup(args, api=None):
    response = api.network_request("https://api.example.com/items", method="GET")
    return {"status_code": response["status_code"]}
```

## Provider 暴露规则

`PluginToolService.list_tools()` 会为 `generic`、`openai` 和 `anthropic` 导出 provider-specific tool schema。
低风险工具可以对 `model` 可见。
高风险工具默认会从普通 `model` actor 隐藏，并根据权限要求 `expert` 或 `admin` 可见性。

后续外部集成应该调用：

- `PluginToolManagerAdapter.list_tools()`：用于旧版 tool-manager 风格代码。
- `ModelLoopToolAdapter.build_provider_tools()`：用于模型循环。
- `PluginToolService.invoke_tool_call()` 或 adapter wrapper：用于执行。

不要绕过 `PluginToolService`。
未来在 `infra/tool_manager`、模型循环或 API handler 中接线时，应该导入这些 adapter，而不是直接调用插件函数。

如需查看模型循环集成要求，运行：

```bash
python -m modules.plugin_system.integration_contract --json
```

## 确认和幂等

有副作用或高风险的工具，对非 admin actor 需要确认。
模型循环应该传入：

- `conversation_id`：用于会话预算。
- `execution_mode="dry_run"`：用于预览。
- `confirmation_token`：在用户/操作员确认后传入。
- `idempotency_key`：用于有副作用操作的重试。

工具结果会被包装成 provider-safe envelope，并标记为 untrusted。
不要把插件输出当成 system 或 developer 指令。

## 敏感数据

不要返回密钥。
宿主会脱敏 `token`、`password`、`secret`、`authorization` 和 `api_key` 等敏感 key，截断过长字符串，并拒绝不安全或过大的结果。
审计记录保存摘要、大小、决策和错误码，不保存完整 args/results/secrets。

## Prompt Injection 防护

看起来像是在要求覆盖 system/developer policy 的工具描述，会从普通 `model` actor 隐藏。
工具结果可能包含用户或插件可控文本，必须留在 tool-result message 内。

## 本地检查

```bash
python -m modules.plugin_system.manifest_lint path/to/plugin --json
python -m modules.plugin_system.llm_tools path/to/plugin --provider openai --approved --json
python -m modules.plugin_system.tool_service --selftest --json
python -m modules.plugin_system.tool_manager_adapter --selftest --json
python -m modules.plugin_system.model_loop_adapter --selftest --json
python -m modules.plugin_system.selftest --json
python -m modules.plugin_system.selftest --quiet-json
python -m modules.plugin_system.evidence_adapters --help
python -m modules.plugin_system.production_evidence --schema --json
python -m modules.plugin_system.production_policy_check path/to/plugin --json
python -m modules.plugin_system.production_policy_check path/to/plugin --governance-store-evidence governance.json --confirmation-provider-evidence confirmation.json --json
python -m modules.plugin_system.integration_contract --json
python -m modules.plugin_system.test_plan --json
```

## 构建、签名、安装、发布

生产级第三方插件包应该包含：

- `manifest.lock`
- `sbom.cdx.json`
- Ed25519 包签名
- 扫描报告或明确的风险接受记录
- 已签名的 registry entry
- 用于多实例生产的外部治理存储
- 用于高风险确认的外部审批 provider
- 外部不可变审计锚点，例如 SIEM、WORM storage、append-only log 或 transparency log

HMAC 签名只适用于 legacy/development，不属于生产信任证据。
只发布已签名的插件包和已签名的 registry index。

`templates/production_plugin_package` 中的示例文件不是生产证据。
`SIGNATURE.example` 不是有效签名，`scanner_report.example.json` 只用于参考，本地 audit checkpoint 不是不可变审计锚点。
Windows Job Object 只提供资源限制，不应该被描述为第三方插件的强生产沙箱。

另见：

- `PRODUCTION_EVIDENCE.md`
- `EXTERNAL_GOVERNANCE.md`
- `WINDOWS_SANDBOX_LIMITS.md`
- `INTEGRATION_CONTRACT.md`
