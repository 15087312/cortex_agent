# file_summary_plugin

Expert-only file summary business plugin template.

## Safety Shape

- Requests `fs.read` only for `data/input/*`.
- Filename is a simple basename; path traversal is rejected in runtime code.
- The model actor should not receive this as a default visible tool.
- Runtime code must use the injected filesystem Gateway.

## Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --actor-role expert --include-hidden --json
```
