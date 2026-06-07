# controlled_network_plugin

Controlled network business plugin template.

## Safety Shape

- Requests `network.outbound` only for `https://api.example.com/*`.
- The model actor should not receive this as a default visible tool; expert/admin review is expected.
- Runtime code must use the injected Gateway `api.network`, not direct `requests`, `httpx`, `socket`, or `urllib`.
- No real network call is made by this template unless a host Gateway is injected.

## Checks

```bash
python -m modules.plugin_system.manifest_lint . --json
python -m modules.plugin_system.llm_tools . --provider openai --approved --actor-role expert --include-hidden --json
```
