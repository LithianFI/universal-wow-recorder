# Project Memory

<!-- Maintained by Claude per the global guidelines. Keep entries short; remove outdated ones. -->

## Decisions
<!-- Architectural or design decisions, with a one-line reason. -->
- WCR naming only; JSON sidecar always written. UI reads encounter info from the sidecar summary, filename only as fallback.
- OBS start/stop go through one FIFO worker in CombatParser; END hands a _RecordingContext snapshot to the end thread (re-pulls can't clobber it).
- Upload queue persisted to <config dir>/upload_queue.json; pending uploads always resume (no 'upload on startup' toggle).
- Auto upload is filtered per content via CloudUpload upload_{lfr,normal,heroic,mythic,mplus} (default true; ConfigManager.should_upload_content). Content without a toggle (timewalking, manual) always uploads; the manual Upload button ignores the filter.
- Removed dead settings (2026-10-21): auto_rename (renaming is always on) and dungeon_timeout_seconds (M+ end is driven by the log + MAX_DUNGEON_SECONDS watchdog). rename_delay is live (wait before rename).
- No WCR-style buffer recording (2026-10-22): WCR's video t=0 is exactly START (it cuts back into an always-on buffer); ours starts ~videoStartDelayMs late, so multi-POV on WCR's site is off by that. Matching it needs constant background recording + ffmpeg cut; rejected as not worth it.
- No dashboard page (2026-10-21): the recordings page (/) carries live status, manual record and the cloud queue; the session progression chart is on Stats.
- "Keep" = WCR's `protected` sidecar field (POST /api/recordings/<f>/protect). Retention neither deletes nor counts kept files, delete-after-upload skips them, and WCR cloud gets /bulk/protect so its housekeeper won't evict them.
- Sidecars are a superset of WCR `Metadata`; our own fields go top-level. `wcr_format` allowlists WCR's keys and builds the CloudMetadata from the sidecar at upload time (WCR's API maps keys to SQL columns). VideoMetadata only identifies the queued file.
- Boss name overrides removed (2026-10-23, unused). Old [BossNames] sections in config.ini are ignored.
- Difficulty IDs live in one table, `constants.DIFFICULTIES` (checked against Difficulty.db2 on wago.tools); config toggles, sidecar letters and the page's DIFFICULTY_SHORT derive from it. Legacy fixed-size raids (3/4/5/6/9) are record_other; party 1/2/23 share the raid tier toggles.
- config.ini.example is the template for new configs (copied on first run) and the settings reference; keep it in sync when adding a setting. An unparsable config.ini is moved to config.ini.bad and the app runs on defaults.
- Post-fight scan = `combat_parser/fight_scan.py`, one streaming pass; names are resolved at the end. The cooldown rescan binary-searches the log by timestamp, so it stays on the request thread.

## Conventions
<!-- Patterns established in this codebase. -->
- Sidecar writes go through `sidecar_cache.write_sidecar` (atomic, refreshes the cached listing); config saves are atomic + 0600.
- Read `get_state().cloud_manager` once into a local per function: `restart_cloud_manager` (one lock, waits for the old workers) can replace it at any time.
- Clip names: `clip_export.is_clip` / `strip_clip_suffix`, never a local regex.
- Pages extend `templates/base.html` (head, fonts, Alpine) and use `_macros.html` (nav, toasts). Shared CSS is plain `static/css/common.css` (page `<style>` overrides it); shared JS mixins (formatMixin, toastMixin, videoPlayerMixin) are in `static/js/common.js`, spread into the page's Alpine data.
- Alpine x-text runs even when its element is hidden by x-show: guard optional data (`?.`, `|| 0`).
- Frontend assets vendored (static/vendor). Tailwind v4: config lives in styles/tailwind.css (@theme, @source "../templates"); no tailwind.config.js. After adding classes to templates run `npm run build:css` and commit static/css/tailwind.css.
- `npm run build` = CSS + PyInstaller + electron-builder (AppImage); `npm run build:electron` only repackages.

## Edge Cases & Gotchas
<!-- Non-obvious behavior: protocol quirks, vendor bugs, environment issues. -->
- COMBATANT_INFO currentSpecID is field 24 ([2] is faction); parse_combatant_spec_id validates against VALID_SPEC_IDS.
- `videoStartDelayMs` in sidecars is our field, not WCR's; the player shifts death/cooldown markers by it.
- Category strings must be WCR's VideoCategory values ("Mythic+", not the old "MythicPlus"); the category is part of uniqueHash, so a wrong spelling breaks multi-POV grouping. Old files are normalised by `wcr_format.normalize_category`.
- The recording player is taken per fight from the window's first local-player (0x511) line, like WCR: the live identity goes stale on a character swap within one log, and a player outside the combatant list breaks uniqueHash, so the POV doesn't group on WCR's site.
- CHALLENGE_MODE_START's affix list "[10,9,147]" is cut at its commas by the CSV split; `CombatEvent._parse_affixes` rejoins it.
- Claude's sandbox has no Node and Python 3.13 while venv/ is 3.14 — test via a scratch venv (`pip install -r requirements.txt`), JS syntax via tree-sitter, page logic via py_mini_racer, layout via Playwright headless Chromium.
- Alpine: a string `:style` replaces the whole style attribute — it wipes x-show's display:none (the REC banner) and any static `style=` on the element (the cooldown timeline's name column/playhead). Always use object form `:style="{ left: x + '%' }"`.
- Alpine calls a component's init() itself; adding x-init="init()" runs it twice (two sockets, doubled debug lines).
- Bar charts with percentage heights need a definite-height box per bar (column `h-full`, bar in its own `flex-1 min-h-0` box, fixed-height labels); otherwise the bars collapse to 0.
- npm audit (2026-10-21): remaining braces (via @tailwindcss/cli watcher) and sprintf-js (via electron-builder's @electron/get) are build-time only with no fixed release; `npm audit fix --force` downgrades and reintroduces tar issues — don't run it.

## Technical Debt
<!-- Known shortcuts and what a proper fix would look like. -->
- Not done: tray icon.
- Google Drive: an expired refresh token makes startup block in the OAuth browser flow, and refreshed tokens aren't written back. Fix: consent only from an explicit user action.
- Unverified: whether Hunter Feign Death shows up as UNIT_DIED (WCR checks unconsciousOnDeath).
- M+ upgradeLevel is always 0 (needs WCR's dungeonTimersByMapId table); no challengeModeTimeline.
