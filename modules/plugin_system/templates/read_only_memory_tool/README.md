# read_only_memory_tool

Read-only memory template for `modules/plugin_system`.

## Tool

- `read_memory_record` accepts `key` and returns `{"found": boolean, "value": string}`.
- It uses the injected Gateway `api.read_memory()` and declares `memory.read`.
- `memory.read` is medium risk, so visibility depends on install, approval, actor role, and production policy.

## Local Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --json
python -m modules.plugin_system.production_policy_check . --dev-mode --json
```
