# High-Risk Behavior Changes Pending Confirmation

**Language**: [English](./BUGS_REQUIRING_CONFIRMATION.en.md) | [简体中文](./BUGS_REQUIRING_CONFIRMATION.md)

> Issues discovered during supplementary testing that **may affect production behavior**. Manual confirmation is required before fixing.
> Ordinary non-high-risk/unexpected error bugs were fixed directly during test supplementation (see docs/ERRORS_AND_FIXES.md).

| # | Module | Issue | Impact | Suggested Fix |
|---|------|------|------|----------|
| 85 | chat.js expert bubble | Multiple messages from the same AI merged into one bubble (same identity reuses the existing bubble and overwrites content) | Conversation history lost: thinking process, tool calls, and previous outputs are overwritten, keeping only the last one | Change `addExpertMessage` to create a new bubble per output; consume thinking/tool buffers on output instead of clearing them; align the replay path |
| 86 | personas.yaml persistence | personas.yaml content overwritten/lost wholesale (agent_active and custom agents all disappear) | All orchestration configuration (enable/disable, personas, overrides, tool permissions, model params) becomes invalid after restart | Backend write path verified correct; investigate trigger scenarios for early data loss (concurrent-write race? different packaged-app HOME?); recommend adding write logging or file change monitoring |
| 87 | config/settings.py | personas.yaml concurrent writes lose updates (14+ set_* without locks) | Two concurrent requests modify the same file; the latter overwrites the former; in extreme cases causes the data loss in §86 | Add a file-level lock or merge into a single read-modify-write |
| 88 | config/settings.py | settings.json concurrent writes lose updates (save_user_config without lock) | Rapid operations on the Settings page or concurrent edits from two tabs; the latter overwrites the former | Add a file-level lock or optimistic locking |
| 89 | api/main.py | delete_custom_agent cascades five independent read-modify-writes | Configuration inconsistency after deleting an agent (residual persona/override/tools/params) | Merge into a single read-modify-write |
| 90 | config/settings.py | _save_personas_yaml swallows exceptions; callers assume success | On write failure the API returns a success toast; users believe the save succeeded but nothing was persisted | Return success/failure so callers can inform the user accordingly |
| 91 | chat.js switchToSession | Replay path merges multiple outputs of the same identity | After switching sessions only the last output is shown; intermediate versions are lost | Create a new bubble per output in the replay path |
| 92 | multi_model_orchestrator | _session_dialog_history concurrent overwrite | Two rapid messages in the same session cause the expanded panel to show incorrect history | Per-session lock or separate storage |
| 93 | context/pool.py | TurnContext fragments overwritten by source | Context from different modules sharing the same source is overwritten and lost | Switch to a list or composite key |
| 94 | chat.js thinking | Substring dedup false positives (includes matching) | "Analyze requirements" is contained in "Analyze requirements and draft a plan", so the latter is skipped | Switch to exact matching (Set or trimmed comparison) |

See `docs/ERRORS_AND_FIXES.md` §85-§94 for details.
