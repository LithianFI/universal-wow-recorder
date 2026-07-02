# WoW Raid Recorder

A World of Warcraft encounter recorder for Windows, Linux, and Mac. Monitors your combat log, automatically starts and stops OBS recordings on boss pulls, and provides a web dashboard for reviewing VODs, clips, and session statistics.

Built with AI assistance. While the code has been reviewed, mistakes happen — if AI-generated code is a concern for you, this project isn't for you.

> **Windows users:** this project does run on Windows, but [Warcraft Recorder](https://warcraftrecorder.com/) is a mature, purpose-built tool for Windows that does everything this does and more — use that instead if you can. This project exists primarily for players on systems Warcraft Recorder doesn't support.

---

## What it does

- Detects the latest WoW combat log and watches it in real time
- Automatically starts OBS recording when a boss encounter begins, stops a few seconds after it ends
- Records Mythic+ dungeon runs as a single continuous VOD
- Web dashboard at `http://localhost:5001` with:
  - Live recording status and in-progress pull timer
  - Previous pull summary with death timeline and markers
  - Per-boss progression chart across the session
  - Recent recordings list
  - **Recordings page** — full library with search, filters, and sort
  - **Clips page** — manage exported highlight clips (rename, delete, download)
  - **Statistics page** — per-boss breakdown across all recordings: pull count, kill rate, best/average time, boss HP% progression on wipes, death leaderboard
  - **Debug page** (Electron client) — live-streamed backend log output, for troubleshooting
- Video player with timeline scrubbing, death markers, volume control, playback speed (0.5×–2×), fullscreen, and keyboard shortcuts
- **Cooldown timeline** — per-player row of tracked cooldowns (healer/tank/raid/DPS/utility) plotted against the fight, with class-colored spell icons and category filters
- Clip export (requires ffmpeg — see below)
- Optional cloud upload — Google Drive, or Warcraft Recorder Cloud (which also enables **POV sync**: automatically fetch guildmates' recordings of the same pull)

---

## Requirements

### Required

- **Python 3.10+**
- **OBS Studio** with the WebSocket server enabled
  - In OBS: Tools → WebSocket Server Settings → Enable WebSocket Server
- **A WoW combat log** — enable it in-game: Main Menu → Options → Network → Advanced Combat Logging

### Optional but recommended

- **ffmpeg** — required for the clip export feature (cutting highlights from recordings)

#### Installing ffmpeg

**Ubuntu / Debian**
```bash
sudo apt update && sudo apt install ffmpeg
```

**Fedora**
```bash
sudo dnf install ffmpeg
```

**Arch / Manjaro**
```bash
sudo pacman -S ffmpeg
```

**macOS (Homebrew)**
```bash
brew install ffmpeg
```

**Windows**
Download a build from [ffmpeg.org](https://ffmpeg.org/download.html) and add the `bin/` folder to your system PATH. Or use a package manager:
```powershell
winget install ffmpeg
# or
choco install ffmpeg
```

Verify the install worked:
```bash
ffmpeg -version
```

---

## Downloads

Pre-built releases for Windows, macOS, and Linux are on the [Releases page](https://github.com/LithianFI/universal-wow-recorder/releases) — see below for platform-specific run instructions. Otherwise, run from source (next section).

---

## Running from source

### Browser mode (no extra dependencies)

```bash
# Make the launcher executable (first time only)
chmod +x launch.sh

# Start the app
./launch.sh
```

The launcher creates a virtual environment, installs Python dependencies, and opens your browser automatically. Use `--no-browser` to skip the browser open.

On Windows, double-click `launch.bat`.

#### Command line options

```
--config PATH    Path to config file (default: config.ini)
--host HOST      Web server host (default: 0.0.0.0)
--port PORT      Web server port (default: 5001)
--no-recorder    Start web GUI only, without recorder
--debug          Enable debug mode
```

### Desktop client mode (Electron)

Runs the app as a standalone window — no browser required. Requires [Node.js](https://nodejs.org/).

```bash
# First time only: install dependencies
npm install

# Launch
npm start
```

---

## Running the pre-built installer (Windows)

Download `WoW Raid Recorder Setup 1.0.0.exe` from the [Releases page](https://github.com/LithianFI/universal-wow-recorder/releases) and run it. It's unsigned, so Windows SmartScreen may warn about it — click **More info → Run anyway**.

On first launch, a `config.ini` is created at `%APPDATA%\wow-raid-recorder\config.ini`. Edit it to set your WoW log directory and OBS connection before starting.

---

## Running the pre-built AppImage (Linux)

A pre-built AppImage requires no Python or Node.js installation — double-click to run, or:

```bash
chmod +x "WoW Raid Recorder-1.0.0.AppImage"
./"WoW Raid Recorder-1.0.0.AppImage"
```

On first launch, a `config.ini` is created at `~/.config/wow-raid-recorder/config.ini`. Edit it to set your WoW log directory and OBS connection before starting.

> **Note:** The AppImage is built against a specific glibc version. If you get a glibc error on an older distribution, run from source instead.

---

## Running the pre-built app (macOS)

The `.dmg`/`.zip` releases are **not notarized or signed with an Apple Developer certificate** — that costs $99/year and this is a free hobby project, so it's not happening. macOS Gatekeeper will refuse to open the app normally and just says it's "damaged" or from an "unidentified developer."

To run it anyway:

1. Open the `.dmg` and drag the app to `Applications` (or unzip the `.zip`).
2. Right-click (or Control-click) the app in Finder and choose **Open**, then confirm in the dialog that appears.
3. If macOS still refuses, run the following once to remove the quarantine flag, then try opening it again:
   ```bash
   xattr -cr "/Applications/WoW Raid Recorder.app"
   ```

You only need to do this on first launch.

On first launch, a `config.ini` is created at `~/Library/Application Support/wow-raid-recorder/config.ini`. Edit it to set your WoW log directory and OBS connection before starting.

---

## Building the AppImage yourself

Requires Python 3.10+, Node.js, and the project's Python venv set up.

```bash
# Install build dependencies (first time only)
source venv/bin/activate && pip install pyinstaller
npm install

# Build everything (PyInstaller + Electron AppImage)
npm run build
```

This runs two steps in sequence:
1. **PyInstaller** bundles the Python backend into `dist/WoWRaidRecorder/`
2. **electron-builder** wraps it with Electron into `dist/WoW Raid Recorder-1.0.0.AppImage`

You can also run the steps individually:

```bash
npm run build:python    # PyInstaller only
npm run build:electron  # Electron packaging only (requires build:python first)
```

> ffmpeg is **not** bundled — it must be installed separately on the target machine if clip export is needed.

---

## Configuration

On first run the app starts without a recorder and opens the configuration page. The main things to set:

- **WoW log directory** — the folder containing `WoWCombatLog.txt`, typically inside your WoW installation under `_retail_/Logs/`
- **OBS connection** — host (usually `localhost`), port (default `4455`), and password if you set one
- **Recording output path** — where OBS saves recordings; must match the path set in OBS

### Cloud upload & POV sync

Recordings can optionally be uploaded to the cloud. Two providers are supported today:

- **Warcraft Recorder Cloud** — also enables **POV sync**, which automatically checks for and downloads guildmates' recordings of the same pull, so you can compare POVs without manually sharing files.
- **Google Drive**

Proton Drive is a planned provider (Proton has recently improved their Linux tooling) but isn't supported yet.

If using Warcraft Recorder Cloud, enable **Auto rename**, **Generate metadata JSON**, and **Track player deaths** in the Recording settings, and set **Naming scheme** to **Warcraft Recorder**. Also set OBS to record in **MPEG-4 (.mp4)** — other containers have been unreliable playing back on the Warcraft Recorder website in testing.

---

## OBS encoder settings

Frame time hitches while recording usually mean OBS is using a software encoder (x264). Pick a hardware encoder instead. In OBS: **Settings → Output → Output Mode: Advanced → Recording → Encoder**.

Codecs in order of efficiency: **AV1 > HEVC > H.264**. Pick the most efficient one your GPU has hardware support for:

| GPU | Encoder |
|---|---|
| AMD RX 7000 / NVIDIA RTX 40 / Intel Arc | AV1 |
| AMD RX 6000 / NVIDIA RTX 20–30 / Intel iGPU gen 11+ | HEVC |
| Older AMD, NVIDIA GTX 16 and older, Intel iGPU gen 9–10 | H.264 |

Encoder name prefixes: `VAAPI` on Linux, `AMF` (AMD) / `QSV` (Intel) / `NVENC` (NVIDIA) on Windows. Avoid `AOM AV1` and `SVT-AV1` — those are software encoders despite the AV1 name.

Then set **Rate Control: CBR**, **Keyframe Interval: 2s**, and a bitrate from this table:

| Resolution (60fps) | AV1 / HEVC | H.264 |
|---|---|---|
| 1080p | 12–20 Mbps | 25–40 Mbps |
| 1440p | 20–30 Mbps | 40–60 Mbps |
| 3440×1440 | 25–40 Mbps | 50–70 Mbps |
| 4K | 40–60 Mbps | 80–120 Mbps |

To confirm the GPU is actually doing the encoding: `radeontop` / `nvtop` / `intel_gpu_top` on Linux, or Task Manager → Performance → GPU → Video Encode on Windows. If the encoder block stays idle, OBS fell back to software — restart OBS and re-check the dropdown.

---

## Contributing

PRs and issues welcome. The core is a Python backend talking to OBS over WebSocket and serving a vanilla HTML/Alpine.js frontend (no build step required for the web UI), wrapped in an optional Electron desktop client.

---

## License

[MIT](LICENSE)
