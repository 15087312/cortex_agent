# Error Log & Fixes · Process · Audit Methodology / Doc Governance

[← Back to index](./README.en.md) | [简体中文](./09-process-docs.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 2 sections: §15、§38.

---
## 15. Supplementary historical lessons (previously unrecorded)

### 15.1 Rate-limit quota exhausted by high-frequency polling → 429 on normal operations (backend)

**Symptom:** After health-check/desktop-pet polling, even normal page opens report "rate limit exceeded"; logs flooded with `限流触发: 127.0.0.1 (GET /xxx)`.

**Root cause:** The rate limiter middleware in `api/main.py` allows 100 req/min per IP with only `/stream/pet/move` whitelisted; `/health` (multiple times/sec) + pet polling (`last-reply`/`state`) exhaust the quota, then everything 429s.

**Fix:** ① High-frequency read-only polling endpoints (`/health`, `/stream/pet/*`, `/stream/status`, `/stream/sessions`, `/config`, `/dashboard`, `/metrics`) added to the rate-limit whitelist; ② loopback (127.0.0.1/::1) raised to 1000/min, public networks stay at 100.

**Similar-issue sweep:** When adding endpoints, distinguish "high-frequency polling (read-only status)" from "business operations" — the former belongs on the rate-limit whitelist/log-ignore list, or they starve each other.

### 15.2 macOS screenshot "could not create image from display" (backend)

**Symptom:** Terminal repeatedly prints `could not create image from display` (once per screenshot).

**Root cause (two layers):**
1. On macOS, `PIL ImageGrab.grab()` goes through X11 and prints this error to the fd itself (`except` can't intercept it);
2. The `screencapture` subprocess fallback's stderr wasn't captured and inherited straight into the parent terminal.

**Fix:** `utils/screen_capture.py` skips ImageGrab on macOS and uses `screencapture` directly with `subprocess.run(..., capture_output=True)`; `_try_screencapture` deletes temp files via `try/finally` (once leaked 38k temp PNGs).

**Similar-issue sweep:** All `subprocess.run` calls without captured stdout/stderr — subprocess errors leak into the parent terminal.

### 15.3 Time-window checks via string comparison → never fires across midnight (backend)

**Symptom:** Proactive outreach `time_windows` configured as `22:00-02:00` (crossing midnight) never triggers.

**Root cause:** `_check_time_windows` used string comparison `start <= cur <= end` — `"23:38" <= "02:00"` is always False.

**Fix:** Compare minutes numerically; treat `end < start` as crossing midnight (`cur >= s or cur <= e`).

**Similar-issue sweep:** Any `"HH:MM"` time comparison should convert to minutes first; string comparisons necessarily break at day/hour boundaries.

### 15.4 Template referencing undefined variables (frontend)

**Symptom:** Opening causal graph/orchestration graph throws `Cannot read properties of undefined (reading 'type'/'color')` → ErrorBoundary shows "page failed to load".

**Root cause:** Template iterates `v-for="node in ..."` but expressions reference `{{ n.type }}` / `n.color` (typo `n` vs `node`) — Vue compilation doesn't flag undefined variables; it crashes at runtime render.

**Fix:** `n` → `node` (7 spots across Causal.vue / Graph.vue).

**Similar-issue sweep:** A script extracting template root identifiers cross-checked against script definitions + v-for locals; the only true bug pattern was "v-for variable typo"; `:style`/`:class` object keys, arrow params, `(v,k)` second vars were all false positives.

**Lesson:** Undefined variables in Vue templates don't fail at build time, only at runtime — after adding/refactoring pages **always open every page and verify**, or run static template-variable checks.

---

## 38. Systematic bug-document audit — 2 similar issues found and fixed

**Background:** Extracted root-cause patterns item by item from §1-37 and searched the codebase for similar issues.

**Audit scope and conclusions (most patterns handled correctly):**

| Pattern | Conclusion |
|---|---|
| §29 missing `import sys` | ✅ Nothing missed (every usage site of `sys.stderr/stdout` imports it) |
| §31 `sys.modules` set to None at module level | ⚠️ **Found 2 similar cases** → fixed |
| §34 hardcoded version assertions | ✅ All are fake data internal to tests, not project-version assertions |
| §24 event-loop self-lock inside a thread | ✅ frontend_channel/api_stream handled (comments explicitly avoid self-locking) |
| §30 background-thread leftovers | ✅ Fully addressed via weakref registry + conftest cleanup |
| §28 unauthenticated WS | ✅ chat_gateway has explicit `_ws_auth_ok` + 4401 rejection, covered by test_ws_auth |
| §21 proactive talk bypassing switches | ✅ trigger gate has 52 enabled/allowed tests, sufficient |
| §25 tool calls wrapped in persona | ✅ model_runner builds prompts uniformly via PromptComposer; tool-type calls use `_NEUTRAL_SYSTEM_PROMPT` |
| §37 `__del__` trap | ⚠️ **Found 1 similar case** → fixed |

**Similar bug 1 (§31 type):** `test_mcp_screen_diff.py` / `test_mcp_screen_monitor.py` set `sys.modules["rapidocr_onnxruntime"] = None` at module level, permanently polluting — conftest's `block_real_native_libs` already nulls it globally, making this redundant and preserving the "module-level pollution" anti-pattern.
**Fix:** Removed the module-level None assignments from both files; managed centrally by conftest. Verified 93 passed.

**Similar bug 2 (§37 type):** `causal_graph.CausalGraph.__del__` calls `self.close()` directly, with neither a `sys.is_finalizing()` guard nor try/except — during GC/interpreter shutdown, if builtins/modules are already torn down, an exception from `__del__` can trigger a GC infinite loop.
**Fix:** Aligned with the `event_store.__del__` guard pattern: `is_finalizing()` early return + try/except swallowing exceptions. Verified 171 passed.

**Takeaway:** Turning known bugs' root causes into a "searchable pattern" checklist (`sys.modules` pollution / unprotected `__del__` / missing imports / hardcoded versions) lets each audit batch-grep against the checklist and efficiently surface similar problems.
