# Error Log & Fixes · Frontend · Pages / Styling / Build / Buttons

[← Back to index](./README.en.md) | [简体中文](./05-frontend-ui.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 6 sections: §6、§7、§14、§22、§23、§75.

---
## 6. Native confirm/prompt blocks Qt WebEngine (frontend)

**Symptom:** In the desktop client (Qt WebEngine), delete/confirm dialogs "don't respond to clicks".

**Root cause:** Native `confirm()`/`prompt()` block the whole JS engine; while the dialog is open all clicks go unanswered.

**Fix:** New `useDialog` composable + `DialogHost.vue` (in-page overlay + Promise, never blocking), replacing all native calls site-wide (Chat/CortexChat/Memory/Settings).

---

## 7. UI used emoji, violating the CSS icon convention

**Symptom / Root cause:** Project convention is pure CSS/Lucide SVG icons (clip-path avatars, Lucide stroke icons); ~60 emoji usages slipped in during the Vue migration.

**Fix:** New `Icon.vue` (Lucide SVG paths, colorable via `currentColor`), emoji replaced site-wide; dark code-block theme via `[data-theme="dark"]` scoped CSS.

---

## 14. Session graph return_to node has empty tier → nodes/edges dropped by layout (frontend/backend)

**Symptom:** Some Agent nodes missing from the session graph; related call/reply edges lost; occasionally unknown-tier nodes mislabeled as "Implementation Expert" (orange).

**Root cause (two layers):**
1. **Backend**: in `session_graph.py`, when `record` creates the callee (`return_to`) node, `tier` defaults to an empty string — if that node never appeared as a speaker, its tier stays empty forever;
2. **Frontend**: `Graph.vue` layout columns only for the 4 known tiers (user/large/supervisor/expert); empty-tier nodes **fall into no column → no coordinates** → node not rendered, edges touching it skipped by `if (!f || !t) continue`; meanwhile `tierOf('')` falls back to `expert`, mislabeling orange.

**Fix:**
- Backend: infer the superior tier from the speaker's tier chain (`expert→supervisor→large→user`) to fill return_to node tier;
- Frontend: `layout` gains an "unknown" column fallback (gray `bot` icon); `tierOf` returns gray "unknown" for unrecognized values instead of falling back to expert.

**Verification:** Node-side simulation of 5 scenarios (full chain / orchestrator-only / 5 experts / unknown tier / empty graph) — no NaN coordinates, no same-column overlaps, no out-of-bounds, no lost unknown nodes/edges.

**Similar-issue sweep:** For all frontend logic that maps enum values to columns/colors, **unknown enum values must have an explicit fallback**, and the fallback must not be a real business enum value (to avoid mislabeling).

**Lesson:** The classic data-driven-rendering pitfall — **missing/empty fields vs frontend enum mismatch**. Frontend enums must tolerate unknowns with neutral styling; backends should fill display fields (tier/label) when creating nodes.

---

## 22. Full-button audit methodology + startup shortcut `shortcut_keys` was decorative (frontend/backend)

**Task background:** User demanded "fully verify every frontend button actually works" — focusing on dead buttons ("click does nothing / saved value never read").

**Audit method (4 layers, reusable):**

### 22.1 Layer 1: authoritative backend route inventory
Import the FastAPI app and enumerate all routes (`for r in app.routes: (methods, r.path)`), yielding 177 real endpoints. **Don't rely on grep** (static/conditional mounts get missed).

### 22.2 Layer 2: frontend call paths → backend route matching (catch dead routes)
Script extracts all string paths in `.vue/.js` (`'/api/xxx'`, `'/xxx'`, template literals), replaces concrete segments with `{p}`, matches backend patterns. **Result: all 25 frontend paths hit, 0 dead routes** — no 404 buttons.

### 22.3 Layer 3: consumer audit (catch "saved but never read" — the core of decoration)
Extract all frontend `XxxCfg('KEY')` / `updateConfig('KEY')` / CK mapping keys; count backend references per key **excluding tests/docs/frontend/cli_tui**:
- Not in the `_MODIFIABLE_FIELDS` whitelist → PUT /config 403s (click does nothing);
- Reference count ≤1 (only the settings.py definition) → suspected decoration.
- **Trap warning:** the frontend CK mapping uses **lowercase** keys (`allow_geolocation`/`shortcut_keys`/`launch_at_startup`) easily missed by regex — scan both upper and lower case.

### 22.4 Layer 4: runtime verification (real backend + real frontend)
1. Start backend with `subprocess.Popen(start_new_session=True)` (plain nohup gets killed by basher session cleanup);
2. Frontend vite on another port (5173 may be taken — **this time 5173 was occupied by a React marketing page, though our frontend is Vue**);
3. Batch-curl read-only endpoints (28/28 returned 200);
4. Write-operation chains verified with **temp sessions** then cleaned up (outreach-config/tasks/title/persona/tool permissions/model params/memory events all ✓);
5. Browser evaluate_script end-to-end (change config → dispatch KeyboardEvent → check `document.activeElement`).

### 22.5 Audit conclusions
- **52 frontend config keys: 51 genuinely consumed, 1 decorative** — `shortcut_keys` (startup shortcut);
- Why decorative: Settings.vue edits and saves it to the backend, but **App.vue's shortcut logic hardcodes Cmd/Ctrl+K**, and the backend references it only once at the settings.py definition — user-set values had zero consumers;
- The user had actually configured `⌥ + X`, which never took effect — confirmed decoration.

### 22.6 Fix (make shortcuts real)
`App.vue`:
1. Import `useConfigStore()`; keydown reads `config.shortcut_keys || config.SHORTCUT_KEYS`;
2. New `parseShortcut()` parsing `⌥ + X` / `Cmd+K` / `Ctrl+Shift+P` (supports ⌘/⌥/⇧/⌃/Cmd/Alt/Option/Ctrl spellings);
3. New `shortcutMatches()` for exact modifier-combination matching;
4. Configured shortcut hit → `_focusChat()` (jump to chat page + focus input); built-in Cmd/Ctrl+K retained as fallback;
5. Settings.vue description updated ("presses focus the chat input (takes effect live)").

**Verification:** Parsing logic passes 8 node unit tests; browser end-to-end: set `Ctrl+Alt+X` → backend reads back ✓ → dispatch keyboard event → focus lands on input TEXTAREA ✓ → restore `⌥ + X` ✓.

**Lessons:**
1. **"Editable and saveable" ≠ "effective"** — judging decoration requires finding who consumes the value, on both ends (this time frontend hardcoded keys and backend had no references; neither side consumed);
2. Config-button audits must **scan both uppercase/lowercase keys**; frontend CK mappings mix them;
3. Runtime verification prefers **real backend responses + temp-data cleanup**; full browser clicking is environment-sensitive; evaluate_script is a reliable end-to-end tool;
4. Test backends must detach from the session (`start_new_session=True`) or get reaped; dev ports may be occupied — check with `lsof -i` first.

---

## 23. Two root causes of "details button does nothing": DOM position + broken build (frontend)

**Symptom:** Clicking "Details" on dashboard API request logs does nothing visible; users assume the button is broken.

**Root cause 1 (main): details panel rendered off-viewport**
- `API_PAGE = 50` — log table paginates 50 rows; the `dash-detail` panel renders **after** the table;
- Clicking only sets `apiDetail.value`, **with no scroll logic** — panel appears below the viewport at page bottom; users see zero change;
- Browser measurement (evaluate_script): after click `dash-detail` exists but `rect.top=3736 > viewport 2029`, `scrollY=0` — functional but invisible.

**Root cause 2 (hidden bomb): duplicate class attributes broke vite build**
- `<div class="setting-ctl" class="ctl-flex">` — two class attributes on one element; Vue compiler errors `Duplicate attribute`;
- This was committed to HEAD (4 occurrences total), so **any `vite build` failed** → dist never updated; users on 8765 forever loaded the stale build;
- Debugging chain: build failure → located Settings.vue:428 → project-wide scan of `class="..." class=` found all 4.

**Fix:**
1. `Dashboard.vue`: `openApiDetail` adds `nextTick` + `scrollIntoView({behavior:'smooth', block:'start'})` to scroll to the panel (note the scroll container is `.page-body`, not window);
2. `Settings.vue`: 4× `class="setting-ctl" class="ctl-flex"` → `class="setting-ctl ctl-flex"`;
3. `api/main.py`: GET/DELETE requests carry no body; record `?query` into `request_body` instead (otherwise GET details always show "no record").

**Verification:** Browser test: after click `rect.top=1712 < viewport 2029` → panel visible ✓; GET log details show `?limit=3` ✓; 33 related tests pass.

**Lessons:**
1. **For "button does nothing", check DOM position/visibility first**, event binding second — panels rendered off-viewport are the most common illusion;
2. **A failing `vite build` means users are forever on the old frontend** — always build before deploying; CI/commit flows should include build validation;
3. For duplicate-attribute template errors, project-wide grep (`class="[^"]*" class=`) catches them all at once;
4. SPA artifacts: index.html must be no-cache (server.py does this); hashed assets can be immutable; but **open pages won't auto-refresh** — tell users to refresh/restart windows after upgrades.

---

## 75. AI ordered-list output overflows the bubble boundary (frontend styling)

**Symptom:** When AI output contained numbered content (markdown ordered lists `1. 2. 3.`), the numbers/long content overflowed past the bubble boundary, bursting `message-bubble`.

**Root cause:** `.message-bubble` (`frontend/css/components.css`) had `max-width: 100%` but **lacked `min-width: 0` and `overflow-wrap: break-word`/`word-break: break-word`**. As a child of the flex container `.message-body`, flex items default to `min-width: auto`, blocking shrinkage; encountering unwrappable long tokens (numbered lists), content overflowed the bubble.

**Fix:**
- `.message-bubble` gains `min-width: 0; overflow-wrap: break-word; word-break: break-word;`
- Added `.message-bubble ol/ul/li` wrapping protections (`overflow-wrap`/`word-break`) + list indentation styles (`list-style: decimal/disc`)

**Verification:** ChatMessage 21 tests + frontend build pass.

**Lesson:** Bubble children inside flex layouts need explicit `min-width: 0` to permit shrink-and-wrap; long content (numbered lists/URLs/code identifiers) relies on `overflow-wrap: break-word` as the safety net, otherwise it bursts fixed max-width containers. CSS overflow problems are routinely missed by "logic-only, no-styling" tests — verify manually under realistic-width rendering.
