# 插件 Manifest 指南

插件可以以目录或 zip 包形式安装，结构如下：

```text
plugin.yaml
config_schema.json
README.md
src/
  main.py
tests/
  test_placeholder.py
```

`modules/plugin_system/templates/` 包含多个示例模板：低风险 compute、只读 memory、受限网络查询、admin-only output 工具、生产包结构，以及三个业务向模板：只读检索、受控网络查询、文件摘要。
每个工具模板都包含 params schema、returns schema 和单工具权限。

原始 `data/plugins/<name>` 目录不等于已安装且已启用的插件。
在安装、权限审批和启用流程写入安装状态与审批记录之前，status provider 应该把它当成原始示例内容，并报告到 `not_installed_examples`，而不是模型可见工具。

`plugin.yaml` 描述可被模型调用的工具、运行时策略，以及通过宿主 Gateway 请求的权限。

```yaml
name: memory_search
version: 1.0.0
description: Search approved memory records for a user question.
author: example-team
license: MIT

extensions:
  - type: tool
    name: search_memory
    entry: src.main:search_memory
    description: Search memory records by query text.
    params:
      query:
        type: string
        description: Search query from the model.
        required: true
        maxLength: 512
      limit:
        type: integer
        description: Maximum number of records to return.
        minimum: 1
        maximum: 20
      mode:
        type: string
        description: Search mode.
        enum: [keyword, semantic]
    returns:
      type: object
      required: [records]
      properties:
        records:
          type: array
          items:
            type: object
      additionalProperties: false
    permissions:
      - memory.read: true

permissions:
  - memory.read: true

runtime:
  mode: sub_process
  trust: third_party
  memory_mb: 256
  timeout_seconds: 5
  cpu_seconds: 5
  max_concurrency: 1
```

工具函数以 dict 接收模型参数。
如果函数接受第二个参数，宿主会为已批准能力注入 Gateway client。

```python
def search_memory(args, api=None):
    query = args["query"]
    limit = int(args.get("limit", 5))
    records = api.memory_search(query=query, limit=limit)
    return {"records": records}
```

## Schema 规则

支持的工具参数类型包括 `string`、`number`、`integer`、`boolean`、`array` 和 `object`。
`enum`、`items`、`properties`、`additionalProperties`、长度限制和数值边界会透传到面向模型的 JSON Schema。

每个可被模型调用的工具都应该声明 `returns`。
结果 schema 校验失败时，宿主会在模型看到结果之前拒绝该结果。

## 权限规则

工具级 `permissions` 必须是插件级 `permissions` 的子集。
这些权限决定风险分级和 provider 可见性：

- 低风险 compute 工具可以对 `model` 可见。
- `network.outbound` 和 `fs.write` 等高风险工具默认会从普通 `model` actor 隐藏。
- expert/admin 可见性仍然必须经过治理、确认、预算、幂等、schema 校验、Gateway 权限范围、结果清洗和审计。

不要直接访问网络、文件、memory、config 或 output。
请使用注入的 Gateway `api`。

## Provider 和模型循环入口

Provider schema 导出：

```bash
python -m modules.plugin_system.llm_tools path/to/plugin --provider openai --approved --json
```

后续外部集成应使用这些稳定运行时入口：

- `PluginToolService.list_tools()`
- `PluginToolService.invoke_tool_call()`
- `PluginToolManagerAdapter`
- `ModelLoopToolAdapter`

执行路径不能绕过 `PluginToolService`。
未来在 `infra/tool_manager`、`modules/thinking/model_runner` 或 admin/API handler 中接线时，应该调用这些 adapter/facade。
这样 schema 校验、Gateway scope、治理、结果清洗和审计才能保持在同一条路径里。

## 确认和幂等

有副作用的工具应该带着 `conversation_id` 和 idempotency key 调用。
当模型提出高影响操作时，执行前应先使用 `dry_run` 或 `preview_only`。
高风险工具应使用 confirmation token。

## 本地检查

```bash
python -m modules.plugin_system.scaffold data/plugins my_plugin --tool-name echo
python -m modules.plugin_system.scaffold data/plugins my_hello --template hello_world
python -m modules.plugin_system.scaffold data/plugins my_lookup --template network_lookup_tool
python -m modules.plugin_system.scaffold --template production-package --output /tmp/plugin_package_example
python -m modules.plugin_system.scaffold --template readonly-retrieval --output /tmp/readonly_retrieval_example
python -m modules.plugin_system.scaffold --template controlled-network --output /tmp/controlled_network_example
python -m modules.plugin_system.scaffold --template file-summary --output /tmp/file_summary_example
python -m modules.plugin_system.manifest_lint data/plugins/my_plugin --json
python -m modules.plugin_system.tool_selftest --json
python -m modules.plugin_system.selftest --json
python -m modules.plugin_system.selftest --quiet-json
python -m modules.plugin_system.evidence_adapters --help
python -m modules.plugin_system.production_evidence --schema --json
python -m modules.plugin_system.tool_manager_adapter --selftest --json
python -m modules.plugin_system.model_loop_adapter --selftest --json
python -m modules.plugin_system.status --json
python -m modules.plugin_system.production_policy_check data/plugins/my_plugin --json
python -m modules.plugin_system.integration_contract --json
python -m modules.plugin_system.test_plan --json
```

## 生产包证据

生产级第三方插件要求：

- `runtime.mode: sub_process`
- 已强制执行的沙箱证据
- Ed25519 签名
- `manifest.lock`
- `sbom.cdx.json`
- 扫描报告或明确的风险接受记录
- 已签名 registry
- 有效的撤销检查
- 用于多实例生产的持久外部治理存储
- 用于高风险确认的外部审批 provider
- 外部不可变审计锚点

Legacy `metadata.json` 加 `plugin.py` 的本地结构不是生产包证据。
HMAC 签名只适用于 legacy/development。

以 `.example` 结尾的文件只是文档 fixture。
它们不能被当成真实 scanner、SBOM、signature 或 sandbox 证据。
Windows Job Object 不是第三方插件的强生产沙箱证据。

生产证据和集成细节见：

- `PRODUCTION_EVIDENCE.md`
- `EXTERNAL_GOVERNANCE.md`
- `WINDOWS_SANDBOX_LIMITS.md`
- `INTEGRATION_CONTRACT.md`
