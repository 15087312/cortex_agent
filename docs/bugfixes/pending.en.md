# High-Risk Behavior Changes Pending Confirmation

**Language**: [English](./pending.en.md) | [简体中文](./pending.md)

[← Back to index](./README.en.md)

> Issues discovered during supplementary testing that **may affect production behavior**. Manual confirmation is required before fixing.
> Ordinary non-high-risk/unexpected error bugs were fixed directly during test supplementation (see the [bug-fix index](./README.en.md)).

**Status (reviewed 2026-11)**: §85, §87, §88, §89, §90, §91, §92, §93 and §94 are all fixed with regression tests. §86 only has an open observation left ("where did the historical data get lost"); the backend read/write path and the cross-process locks are in place. The table is kept as a traceable record of behavior changes.

| # | Module | Issue | Impact | Suggested Fix | Status |
|---|------|------|------|----------|------|
| 85 | chat.js expert bubble | Multiple messages from the same AI merged into one bubble (same identity reuses the existing bubble and overwrites content) | Conversation history lost: thinking process, tool calls, and previous outputs are overwritten, keeping only the last one | Change `addExpertMessage` to create a new bubble per output; align the replay path | ✅ Fixed (`addExpertMessage` creates a bubble per output; expert reasoning/tools now go to the process flow and the bubble buffers were removed, see §101/§101.1) |
| 86 | personas.yaml persistence | personas.yaml content overwritten/lost wholesale (agent_active and custom agents all disappear) | All orchestration configuration (enable/disable, personas, overrides, tool permissions, model params) becomes invalid after restart | Backend write path verified correct; investigate trigger scenarios for early data loss (concurrent-write race? different packaged-app HOME?); recommend adding write logging or file change monitoring | 🔍 Write path hardened (§96 cross-process lock + §102 refuses writing from an empty snapshot); historical trigger still under observation |
| 87 | config/settings.py | personas.yaml concurrent writes lose updates (14+ set_* without locks) | Two concurrent requests modify the same file; the latter overwrites the former; in extreme cases causes the data loss in §86 | Add a file-level lock or merge into a single read-modify-write | ✅ Fixed (§96: thread lock + `faiss_file_lock` cross-process lock around the whole read-modify-write; every `set_*` goes through `_modify_personas_yaml`) |
| 88 | config/settings.py | settings.json concurrent writes lose updates (save_user_config without lock) | Rapid operations on the Settings page or concurrent edits from two tabs; the latter overwrites the former | Add a file-level lock or optimistic locking | ✅ Fixed (same double lock as §96; also fixed the whole-file wipe when parsing fails, see §102) |
| 89 | api/main.py | delete_custom_agent cascades five independent read-modify-writes | Configuration inconsistency after deleting an agent (residual persona/override/tools/params) | Merge into a single read-modify-write | ✅ Fixed (`Settings.delete_custom_agent` cleans every key in one `_modify_personas_yaml`) |
| 90 | config/settings.py | _save_personas_yaml swallows exceptions; callers assume success | On write failure the API returns a success toast; users believe the save succeeded but nothing was persisted | Return success/failure so callers can inform the user accordingly | ✅ Fixed (cleans the temp file then re-raises; API layer returns an error response) |
| 91 | chat.js switchToSession | Replay path merges multiple outputs of the same identity | After switching sessions only the last output is shown; intermediate versions are lost | Create a new bubble per output in the replay path | ✅ Fixed (§101: `entry_type` is persisted again, one bubble per output, buffers consumed per output) |
| 92 | multi_model_orchestrator | _session_dialog_history concurrent overwrite | Two rapid messages in the same session cause the expanded panel to show incorrect history | Per-session lock or separate storage | ✅ Fixed (`_session_dialog_history_lock` guards the write) |
| 93 | context/pool.py | TurnContext fragments overwritten by source | Context from different modules sharing the same source is overwritten and lost | Switch to a list or composite key | ✅ Fixed (`source` / `source#2`… composite keys plus content-hash dedup) |
| 94 | chat.js thinking | Substring dedup false positives (includes matching) | "Analyze requirements" is contained in "Analyze requirements and draft a plan", so the latter is skipped | Switch to exact matching (Set or trimmed comparison) | ✅ Fixed (`_thinkingSeen` Set with exact matching) |

See `docs/bugfixes/` §85-§94, §96, §101-§102 for details.
