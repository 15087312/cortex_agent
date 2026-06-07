# hello_world

Low-risk compute-only plugin template for `modules/plugin_system`.

## Tool

- `say_hello` accepts `name` and returns `{"greeting": string}`.
- It declares `compute` only, so the default exposure policy may show it to the normal `model` actor after install/enable/approval.
- Results still return through `PluginToolService` as untrusted tool data.

## Local Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --json
python -m modules.plugin_system.production_policy_check . --dev-mode --json
```
