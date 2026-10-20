# Project Memory

<!-- Maintained by Claude per the global guidelines. Keep entries short; remove outdated ones. -->

## Decisions
<!-- Architectural or design decisions, with a one-line reason. -->
- WCR naming only; JSON sidecar always written. UI reads encounter info from the sidecar summary, filename only as fallback.
- OBS start/stop go through one FIFO worker in CombatParser; END hands a _RecordingContext snapshot to the end thread (re-pulls can't clobber it).
- Upload queue persisted to <config dir>/upload_queue.json; pending uploads always resume (no 'upload on startup' toggle).
- Auto upload is filtered per content via CloudUpload upload_{lfr,normal,heroic,mythic,mplus} (default true; ConfigManager.should_upload_content). Content without a toggle (timewalking, manual) always uploads; the manual Upload button ignores the filter.
- Removed dead settings (2026-10-21): auto_rename (renaming is always on) and dungeon_timeout_seconds (M+ end is driven by the log + MAX_DUNGEON_SECONDS watchdog). rename_delay is live (wait before rename).

## Conventions
<!-- Patterns established in this codebase. -->
- Frontend assets vendored (static/vendor). Tailwind v4: config lives in styles/tailwind.css (@theme, @source "../templates"); no tailwind.config.js. After adding classes to templates run `npm run build:css` and commit static/css/tailwind.css.
- `npm run build` = CSS + PyInstaller + electron-builder (AppImage); `npm run build:electron` only repackages.

## Edge Cases & Gotchas
<!-- Non-obvious behavior: protocol quirks, vendor bugs, environment issues. -->
- COMBATANT_INFO currentSpecID is field 24 ([2] is faction); parse_combatant_spec_id validates against VALID_SPEC_IDS.
- `videoStartDelayMs` in sidecars is our field, not WCR's; the player shifts death/cooldown markers by it.
- Claude's sandbox has no Node/browser and Python 3.13 while venv/ is 3.14 — test via a scratch venv (`pip install -r requirements.txt`), JS syntax via tree-sitter.
- npm audit (2026-10-21): remaining braces (via @tailwindcss/cli watcher) and sprintf-js (via electron-builder's @electron/get) are build-time only with no fixed release; `npm audit fix --force` downgrades and reintroduces tar issues — don't run it.

## Technical Debt
<!-- Known shortcuts and what a proper fix would look like. -->
- Not done: OBS replay-buffer pre-roll, tray icon.
- main.py is an unused legacy CLI entry (run.py / Electron is the real app).
