# 生产证据

本文档描述 `modules/plugin_system` 负责维护的本地生产证据契约。
它定义证据的结构、归一化方式和检查方式。
它不声明本地示例、离线报告或诊断 fixture 是真实生产证据。

## 本地命令

```bash
python -m modules.plugin_system.evidence_adapters --help
python -m modules.plugin_system.evidence_adapters --status --json
python -m modules.plugin_system.evidence_adapters --normalize evidence.json --json
python -m modules.plugin_system.evidence_adapters --validate evidence.json --json
python -m modules.plugin_system.production_evidence --schema --json
python -m modules.plugin_system.production_evidence --validate evidence.json --json
python -m modules.plugin_system.production_policy_check path/to/plugin --json
```

`evidence_adapters.py` 只读取本地 JSON。
它不会调用外部 scanner、registry、signing、sandbox、audit、CI 或 LLM 服务。

## 证据类型

`production_evidence.py` 定义这些证据类型：

- `scanner`
- `signature`
- `registry`
- `sandbox`
- `governance`
- `confirmation`
- `audit_anchor`
- `ci`

当前 adapter 层会对下面这些本地 JSON 进行归一化：

- scanner 报告：`pip-audit`、OSV、Grype、Safety 和 enterprise SCA generic
- signature verification result JSON
- registry verification 或 drill JSON
- sandbox validation JSON
- audit anchor verification JSON

## Scanner 证据

scanner 报告只有在满足下面条件时，才能解除 scanner policy 阻断：
它必须由真实 scanner 运行生成，未过期，包含 `production_evidence=true`，包含 `policy_decision=pass`，并且不包含阻断级 high 或 critical finding。

下面这些输入不是生产 scanner 证据：

- 以 `.example` 结尾的文件
- `source=offline`
- `source=reference_only`
- GitHub-hosted diagnostic fixture
- 没有 scanner identity/version 的本地手改 JSON

缺少 scanner version 会报告为 warning。

## Signature 证据

生产 signature 证据要求：

- `signature_verified=true`
- `package_digest_verified=true`
- `key_revoked=false`
- `production_evidence=true`
- `policy_decision=pass`
- Ed25519 算法，目前为 `Ed25519-SHA256`

HMAC 签名只适用于 legacy/development，不属于生产信任证据。

## Registry 证据

registry 证据只有在 registry 和 package verification path 已证明下面能力时才通过：

- 已签名 registry index
- package SHA-256 校验
- 拒绝未签名 registry
- 拒绝被篡改 registry
- 拒绝已撤销版本
- 拒绝 rollback 或 downgrade

这是本地证据契约。
真实 registry verification 仍然必须由生成证据的 build/release 或 registry 系统执行。

## Sandbox 证据

sandbox 证据只有来自目标生产 Linux 主机、self-hosted 生产环境，或明确的非 GitHub-hosted 目标环境，并且满足下面全部条件时，才能解除强沙箱阻断：

- `evidence_type=sandbox`
- `mode=production-required`
- `status=pass`
- `production_blocking=false`
- `sandbox_backend.enforced=true`
- all required checks pass:
  `plugin_executed`, `bwrap_backend_enforced`, `bwrap_wrapped_command`,
  `bwrap_unshared_network`, `bwrap_private_tmp`, `host_home_blocked`,
  `env_blocked`, `core_blocked`, `code_readonly`, `private_tmp_writable`,
  `host_tmp_not_leaked`, `direct_network_blocked`, `data_write_allowed`,
  `audit_records_present`

GitHub-hosted diagnostics 和不受支持环境的 probe 不能解除生产沙箱阻断。

## Audit Anchor 证据

本地 audit hash chain 和本地 checkpoint 只能证明本地完整性。
它们不是不可变生产审计证据。

不可变审计证据要求：

- `external_anchor_configured=true`
- `production_immutability=true`

外部锚点可以是 SIEM、WORM storage、append-only storage、transparency log，或其他由宿主提供的不可变审计系统。

## 证据 Bundle

可以把多个 evidence record 作为 bundle 传入：

```json
{
  "schema_version": "2026-06-rc1",
  "evidences": [],
  "summary": {},
  "production_blockers": [],
  "warnings": []
}
```

bundle 的可信度只取决于其中的记录。
adapter CLI 输出的示例 bundle 只是结构示例，不是发布证据。
