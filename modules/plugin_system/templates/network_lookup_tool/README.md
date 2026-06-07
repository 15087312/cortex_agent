# network_lookup_tool

Restricted outbound network template for `modules/plugin_system`.

## Tool

- `lookup_status` accepts `path` and calls `api.network_request()`.
- It declares `network.outbound` with an explicit HTTPS URL rule.
- Network tools are high risk and should normally be visible only to `expert` or `admin`, with confirmation and idempotency in model loops.

## Local Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --include-hidden --json
python -m modules.plugin_system.production_policy_check . --dev-mode --json
```
