# 插件工具服务契约

契约版本：`2026-05-rc1`

`PluginToolService` 是插件工具暴露给模型侧的统一入口。宿主系统、旧 ToolManager 适配层、模型循环适配层和 API 层都应该通过它或它的适配器调用插件工具，不应该直接调用插件函数、`PluginEngine`、`PluginGateway` 或治理内部实现。

## 最小用法

```python
from modules.plugin_system.tool_service import PluginToolService

service = PluginToolService(engine=engine, production_mode=True)

tools = service.list_tools(
    provider="openai",
    actor_role="model",
    request_id=request_id,
)

result = service.invoke_tool_call(
    provider="openai",
    payload=model_tool_call,
    actor_role="model",
    conversation_id=conversation_id,
    request_id=request_id,
)
```

每个公开响应都会包含 `contract_version`。

## Provider 适配

当前支持 3 类 provider：

- `openai`：导出 Chat Completions/Responses 可用的 function tool 定义。
- `anthropic`：导出 Anthropic `tool_use` 可用的 tool 定义。
- `generic`：导出内部通用结构，并包含插件、权限、风险、暴露策略等元数据。

OpenAI 工具定义示例：

```json
{"type":"function","function":{"name":"plugin_tool","description":"...","parameters":{"type":"object"}}}
```

Anthropic 工具定义示例：

```json
{"name":"plugin_tool","description":"...","input_schema":{"type":"object"}}
```

通用工具定义示例：

```json
{"name":"plugin_tool","input_schema":{"type":"object"},"metadata":{"plugin_id":"...","contract_version":"2026-05-rc1"}}
```

Provider 工具名会通过 `name_mapping` 映射回内部 `model_tool_name`、`plugin_id`、`plugin_version` 和 `tool_name`。`PluginToolManagerAdapter.list_tools()` 还会补充 `visibility_metadata`，以及增强后的 `name_mapping` 字段：`exposure`、`risk_level`、`hidden`、`status`。

## 支持的工具调用格式

OpenAI Chat Completions:

```json
{"id":"call_x","type":"function","function":{"name":"plugin_tool","arguments":"{\"x\":1}"}}
```

OpenAI legacy function call:

```json
{"function_call":{"name":"plugin_tool","arguments":"{\"x\":1}"}}
```

OpenAI Responses API:

```json
{"type":"function_call","call_id":"call_x","name":"plugin_tool","arguments":"{\"x\":1}"}
```

OpenAI Responses wrapper:

```json
{"output":[{"type":"function_call","call_id":"call_x","name":"plugin_tool","arguments":"{\"x\":1}"}]}
```

Anthropic tool use:

```json
{"type":"tool_use","id":"toolu_x","name":"plugin_tool","input":{"x":1}}
```

Anthropic content wrapper:

```json
{"content":[{"type":"tool_use","id":"toolu_x","name":"plugin_tool","input":{"x":1}}]}
```

通用调用：

```json
{"name":"plugin_tool","arguments":{"x":1}}
```

`parse_model_tool_calls(provider, payload)` 可解析多工具 wrapper；`invoke_tool_call()` 和 `parse_model_tool_call()` 只接受一次工具调用。如果传入多个调用，会返回稳定错误码 `MULTIPLE_TOOL_CALLS_UNSUPPORTED`。

`thinking`、`reasoning`、`chain_of_thought`、`internal_thoughts` 这些字段会被视为 provider 元数据并忽略，不会作为工具参数，也不会出现在 provider 响应中。

## 可见性和风险

工具可见性由 manifest、权限、生产模式、审批状态、actor role 和 schema 完整性共同决定。

- `model`：普通模型调用方，默认只能看到低风险工具。
- `expert`：可看到部分高风险工具，例如受限网络工具。
- `admin`：可看到管理员工具，例如文件写入、记忆写入、输出发送。

风险大致按权限推导：

- `compute`：低风险。
- `memory.read`、`config.read`、`fs.read`：中等风险。
- `network.outbound`、`fs.write`：高风险。
- `memory.write`、`output.send`：关键风险。

高风险/关键风险工具不会默认暴露给普通 `model` actor。需要通过 `include_hidden=True` 才能诊断隐藏工具，但执行时仍会重新走可见性、治理和权限检查。

## 执行模式

- `execute`：所有检查通过后执行工具。
- `dry_run` / `preview_only`：只返回治理预览，不执行插件。
- `confirmation_only`：需要确认时只创建确认 token，不执行插件。

有副作用的调用应该传入稳定 `conversation_id` 和 `idempotency_key`。重复完成的幂等调用会返回安全缓存结果；正在执行的重复调用会返回 `DUPLICATE_IN_PROGRESS`；参数不匹配会返回 `IDEMPOTENCY_CONFLICT`。

## 确认和治理

高风险工具可能返回 `CONFIRMATION_REQUIRED` 和确认 token。token 绑定 actor role、conversation id、模型工具名、参数 hash 和权限集合。

默认 `LocalConfirmationProvider` 是进程内实现，不是外部审批系统。它只存 hash 和元数据，不存原始参数。生产环境如果要多实例或真实外部审批，应注入外部确认 provider 和持久治理 store。

治理指标可通过 `metrics_snapshot()` 和状态快照查看，包含调用总数、允许/拒绝次数、确认次数、预算超限、限流、重复调用、参数 schema 错误和返回 schema 错误等。

## Gateway 和安全响应

插件访问网络、文件、记忆、配置、输出必须走注入的 Gateway `api`。敏感权限会检查：

- 插件级权限是否声明并授权。
- 工具级权限是否声明。
- 当前 request scope 是否允许该工具使用该权限。
- Gateway 自身的目标白名单、路径限制、响应大小和安全规则。

插件结果会被包装成 provider-safe tool result。宿主应该把 `response` / `message` 作为工具结果放回 provider 对话，不要把插件输出当成 system 或 developer 指令。

错误响应不会包含 Python traceback、内部路径、原始 stderr、原始参数或原始结果。常见稳定错误码包括 `INVALID_ARGUMENT_JSON`、`TOOL_CALL_MISSING_NAME`、`PARAM_SCHEMA_ERROR`、`RETURN_SCHEMA_ERROR`、`PERMISSION_DENIED`、`TOOL_NOT_VISIBLE`、`CONFIRMATION_REQUIRED`、`BUDGET_EXCEEDED`。

## 状态快照

`python -m modules.plugin_system.status --json` 会输出模块级状态：

- `platform`：平台、Python、沙箱后端、Windows Job Object 警告、第三方生产插件是否具备强沙箱条件。
- `plugins`：已安装、已启用、运行中、隔离/撤销数量；原始示例目录会单独列为 `raw_example_plugins` / `not_installed_examples`。
- `tools`：总工具数、模型可见数、expert/admin 工具数、隐藏工具数、高风险工具数、各 provider 可导出数量。
- `security`：权限审批、签名要求、沙箱状态。
- `governance`：治理 store、确认 provider、调用/拒绝/确认/预算/限流/重复调用指标。
- `audit`：审计日志、hash chain、checkpoint、外部不可变锚点状态。
- `supply_chain`：签名、SBOM、lockfile、scanner、registry、撤销信息。
- `llm_tool_service`：契约版本、支持 provider、health、capabilities、metrics。

原始 `data/plugins/<name>` 或 `modules/plugin_system/templates/<name>` 目录只是源码/示例；没有经过安装、权限审批和启用流程前，不等于模型可见插件。

## 生产策略检查

`python -m modules.plugin_system.production_policy_check path/to/plugin --json` 会检查第三方生产插件是否满足准入要求。它会给出 `errors`、`warnings`、`recommendations`，并在生产模式下 fail-closed。

标准生产证据 schema 由 `modules.plugin_system.production_evidence` 定义：

```bash
python -m modules.plugin_system.production_evidence --schema --json
python -m modules.plugin_system.production_evidence --validate evidence.json --json
```

可选证据输入：

```bash
python -m modules.plugin_system.production_policy_check path/to/plugin \
  --evidence-bundle evidence_bundle.json \
  --signature-evidence signature.json \
  --registry-evidence registry.json \
  --sandbox-evidence sandbox.json \
  --governance-store-evidence governance_store.json \
  --confirmation-provider-evidence confirmation_provider.json \
  --external-anchor-evidence audit_anchor.json \
  --json
```

Windows 上的 Job Object 只算资源限制，不算强文件系统/网络/syscall 沙箱证据；生产模式会因此阻断第三方插件准入。

生产证据适配器只读取本地 JSON，不会联网调用 scanner、registry、签名服务、沙箱服务或审计服务：

```bash
python -m modules.plugin_system.evidence_adapters --help
python -m modules.plugin_system.evidence_adapters --status --json
python -m modules.plugin_system.evidence_adapters --normalize evidence.json --json
python -m modules.plugin_system.evidence_adapters --validate evidence.json --json
python -m modules.plugin_system.integration_contract --json
python -m modules.plugin_system.test_plan --json
```

`.example`、`offline`、`reference_only`、GitHub-hosted diagnostic、本地 audit checkpoint、Windows Job Object 都不能被当作完整生产证据。

外部治理状态要明确区分：

- `local_memory_governance_store` / 默认内存 store：仅适合本地和单进程验证，不是多实例生产治理。
- `local_file_governance_store`：可持久化，但不是多实例事务存储。
- `external_governance_store`：生产多实例应接入的外部事务型治理存储。
- `local_confirmation_provider`：本地 token provider，不是外部审批系统。
- `external_approval_provider`：生产高风险确认应接 UI/审批系统。
- `local_audit_checkpoint`：只表示本地完整性检查，不是不可篡改审计。
- `external_audit_anchor`：生产应接 SIEM/WORM/append-only/transparency log 等外部锚点。

## 适配器

`PluginToolManagerAdapter` 用于旧 ToolManager 风格调用：

- `list_tools()` 委托到 `PluginToolService.list_tools()`。
- `execute_tool()` 委托到 `PluginToolService.invoke_tool_call()`。
- `resolve_tool_name()` 可用 provider tool name、model tool name 或 `plugin.tool` 解析工具。
- `status()` 返回 `ok`、`provider_supported`、`service_health`、`warnings`、`ready_for_model_calls`、`ready_for_production`。

`ModelLoopToolAdapter` 用于模型循环构建 provider tools、预览、确认和执行。它同样不绕过 `PluginToolService`。

## 模板

模板放在 `modules/plugin_system/templates/`，用于作者参考和 scaffold：

- `hello_world`：低风险 compute-only 示例。
- `read_only_memory_tool`：只读记忆示例。
- `network_lookup_tool`：受限 HTTPS 网络查询示例。
- `admin_only_output_tool`：管理员输出发送示例。
- `production_plugin_package`：生产包结构示例，包含 `manifest.lock.example`、`sbom.cdx.json.example`、`scanner_report.example.json`、`SIGNATURE.example`。
- `readonly_retrieval_plugin`：只读检索业务模板，普通 `model` actor 可见，适合低风险演示。
- `controlled_network_plugin`：受控 HTTPS 查询模板，默认 expert-only，不直接调用 `requests`/`httpx`/`socket`。
- `file_summary_plugin`：受限文件摘要模板，只允许读取 `data/input/*`，默认 expert-only。

创建示例：

```bash
python -m modules.plugin_system.scaffold data/plugins my_hello --template hello_world
python -m modules.plugin_system.scaffold data/plugins my_lookup --template network_lookup_tool
python -m modules.plugin_system.scaffold --template production-package --output /tmp/plugin_package_example
python -m modules.plugin_system.scaffold --template readonly-retrieval --output /tmp/readonly_retrieval_example
python -m modules.plugin_system.scaffold --template controlled-network --output /tmp/controlled_network_example
python -m modules.plugin_system.scaffold --template file-summary --output /tmp/file_summary_example
```

`.example` 文件不是生产证据：示例 SBOM 不等于真实 SBOM，示例 scanner report 不等于真实扫描，`SIGNATURE.example` 不是有效签名。真实生产包必须由 build/sign/scan 流程生成。

## 本地验证

```bash
python -m compileall modules/plugin_system
python -m modules.plugin_system.manifest_lint modules/plugin_system/templates/hello_world --json
python -m modules.plugin_system.llm_tools modules/plugin_system/templates/hello_world --provider openai --approved --json
python -m modules.plugin_system.tool_service --selftest --json
python -m modules.plugin_system.tool_manager_adapter --selftest --json
python -m modules.plugin_system.model_loop_adapter --selftest --json
python -m modules.plugin_system.selftest --json
python -m modules.plugin_system.selftest --quiet-json
python -m modules.plugin_system.status --json
python -m modules.plugin_system.doctor --json
python -m modules.plugin_system.evidence_adapters --help
python -m modules.plugin_system.production_evidence --schema --json
python -m modules.plugin_system.production_policy_check --json
python -m modules.plugin_system.integration_contract --json
python -m modules.plugin_system.test_plan --json
```

## 禁止事项

- 不要把插件结果当作 system/developer 指令。
- 不要暴露原始参数、原始结果、密钥、traceback、stderr 或内部路径。
- 不要绕过 `PluginToolService -> ModelToolBridge -> LLMToolRuntime -> Engine/Gateway`。
- 不要默认向普通 `model` actor 暴露高风险工具。
- 不要在生产多实例部署中直接依赖默认内存治理 store。
- 不要把本地确认 provider 描述成外部审批系统。
