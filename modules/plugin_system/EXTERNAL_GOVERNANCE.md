# 外部治理

插件运行时已经包含本地治理能力，包括预算、限流、确认 token、幂等、循环保护和安全审计摘要。
这些能力适合本地开发和单进程测试；如果要进入多实例生产环境，还需要由宿主系统提供外部治理组件。

## 本地实现

`ToolCallSessionStore`

- kind: `memory`
- persistent: false
- multi-process safe: false
- multi-instance safe: false
- production recommended: false

`FileToolCallSessionStore`

- kind: `local_file`
- persistent: true
- multi-process safe: false
- multi-instance safe: false
- production recommended: false

`LocalConfirmationProvider`

- kind: `local`
- stores raw args: false
- token scope: actor role, conversation id, model tool name, args hash, permissions
- external UI: false
- production recommended: false

这些本地组件不能被描述成外部审批系统，也不能被描述成生产级多实例治理。

## 外部存储契约

`ExternalGovernanceStore` 描述了未来接入事务型外部存储时，宿主系统需要实现的接口形态。
生产实现应该为下面这些能力提供持久化、可用于多实例的行为：

- `get_session(conversation_id)`
- `save_session(session)`
- `reserve_idempotency_key(key, request_summary)`
- `complete_idempotency_key(key, safe_envelope)`
- `get_idempotency_record(key)`
- `increment_rate_counter(scope, window)`
- `get_rate_counter(scope, window)`
- `health()`

`modules/plugin_system` 只提供契约和未配置的 stub，不实现 Redis、SQL、队列、锁服务或任何外部后端。

## 外部审批契约

`UnconfiguredApprovalProvider` 描述了宿主系统提供审批 provider 或操作员 UI 时应满足的形态：

- 创建确认请求
- 查询确认状态
- 批准
- 拒绝
- 过期处理
- 健康检查

生产环境里的高风险确认应接入外部审批 provider，并把审批与 actor、conversation、tool、参数 hash、权限、过期时间、审批人、拒绝/过期状态绑定。

## 状态和诊断

`status` 和 `doctor` 会刻意区分本地就绪状态与外部治理就绪状态：

```bash
python -m modules.plugin_system.status --json
python -m modules.plugin_system.doctor --json
```

关键字段：

- `governance.governance_store_kind`
- `governance.governance_store_ready_for_production`
- `governance.approval_provider_kind`
- `governance.approval_provider_ready_for_production`
- `llm_tool_service.capabilities.governance_store`
- `llm_tool_service.capabilities.confirmation_provider`

缺少外部治理时，开发模式会报告为 warning；如果生产策略要求外部治理，则会报告为生产阻断项。

## 审计安全

治理和审批记录应该保存 hash 与摘要，不保存原始参数、原始工具结果、密钥、stderr、traceback 或内部路径。
