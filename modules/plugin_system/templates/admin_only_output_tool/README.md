# admin_only_output_tool

Admin-only output template for `modules/plugin_system`.

## Tool

- `send_operator_note` accepts a bounded message and optional channel.
- It uses `api.send_output()` and declares `output.send`.
- `output.send` is critical risk, so it should be treated as admin-only and confirmation/idempotency protected in model loops.

## Local Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --actor-role admin --include-hidden --json
python -m modules.plugin_system.production_policy_check . --dev-mode --json
```
