# readonly_retrieval_plugin

Read-only business plugin template for bounded retrieval.

## Safety Shape

- Requests `memory.read` only.
- Intended to be model visible after normal install, approval, and enablement.
- Query and result sizes are bounded by manifest schemas.
- No network, filesystem write, or output permissions.

## Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --json
```
