const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const fs = require('fs')

const PORT = 5001
const APP_DIR = path.join(__dirname, '..')

let mainWindow = null
let pythonProcess = null
let appIsQuitting = false

// ---------------------------------------------------------------------------
// Backend process
// ---------------------------------------------------------------------------

function spawnBackend() {
  if (app.isPackaged) {
    // Packaged: use the PyInstaller binary bundled into resources/backend/
    const exeName = process.platform === 'win32' ? 'WoWRaidRecorder.exe' : 'WoWRaidRecorder'
    const exePath = path.join(process.resourcesPath, 'backend', exeName)

    // Config lives in the user's writable app-data directory
    const userData = app.getPath('userData')
    const configPath = path.join(userData, 'config.ini')
    const exampleConfig = path.join(process.resourcesPath, 'backend', 'config.ini.example')

    if (!fs.existsSync(configPath) && fs.existsSync(exampleConfig)) {
      fs.mkdirSync(userData, { recursive: true })
      fs.copyFileSync(exampleConfig, configPath)
    }

    console.log(`[Electron] Starting packaged backend: ${exePath}`)
    return spawn(exePath, ['--config', configPath], {
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  }

  // Development: spawn via venv Python
  const python = getDevPython()
  console.log(`[Electron] Starting dev backend: ${python} run.py`)
  return spawn(python, ['run.py'], {
    cwd: APP_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

function getDevPython() {
  const candidates = process.platform === 'win32'
    ? [path.join(APP_DIR, 'venv', 'Scripts', 'python.exe'), 'python']
    : [path.join(APP_DIR, 'venv', 'bin', 'python'), 'python3', 'python']
  for (const c of candidates) {
    if (!path.isAbsolute(c) || fs.existsSync(c)) return c
  }
  return 'python3'
}

function startBackend() {
  pythonProcess = spawnBackend()
  pythonProcess.stdout.on('data', d => process.stdout.write(d))
  pythonProcess.stderr.on('data', d => process.stderr.write(d))
  pythonProcess.on('exit', (code) => {
    if (!appIsQuitting) {
      dialog.showErrorBox(
        'Recorder Stopped',
        `The recorder process exited unexpectedly (code ${code}).`
      )
      app.quit()
    }
  })
}

// ---------------------------------------------------------------------------
// Server readiness poll
// ---------------------------------------------------------------------------

function waitForServer(maxAttempts = 60) {
  return new Promise((resolve, reject) => {
    let attempts = 0
    const check = () => {
      const req = http.get(`http://127.0.0.1:${PORT}`, (res) => {
        res.resume()
        resolve()
      })
      req.setTimeout(1000, () => req.destroy())
      req.on('error', () => {
        if (++attempts >= maxAttempts) {
          reject(new Error(`Backend did not start after ${maxAttempts / 2}s`))
        } else {
          setTimeout(check, 500)
        }
      })
    }
    check()
  })
}

// ---------------------------------------------------------------------------
// Loading screen
// ---------------------------------------------------------------------------

const LOADING_HTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0d1117;
    color: #c9d1d9;
    font-family: 'Segoe UI', system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    gap: 20px;
    user-select: none;
    -webkit-app-region: drag;
  }
  .spinner {
    width: 36px;
    height: 36px;
    border: 3px solid #1c2128;
    border-top-color: #c7a047;
    border-radius: 50%;
    animation: spin 0.75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .title {
    font-size: 20px;
    font-weight: 600;
    color: #c7a047;
    letter-spacing: 3px;
    text-transform: uppercase;
  }
  .status {
    font-size: 12px;
    color: #484f58;
    letter-spacing: 1px;
  }
</style>
</head>
<body>
  <div class="spinner"></div>
  <div class="title">WoW Raid Recorder</div>
  <div class="status">Starting backend&hellip;</div>
</body>
</html>`

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: 'WoW Raid Recorder',
    backgroundColor: '#0d1117',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  })

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(LOADING_HTML)}`)
  mainWindow.once('ready-to-show', () => mainWindow.show())
  mainWindow.on('closed', () => { mainWindow = null })

  try {
    await waitForServer()
    if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${PORT}`)
  } catch (err) {
    if (!appIsQuitting) {
      dialog.showErrorBox('Startup Failed', err.message)
      app.quit()
    }
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.on('ready', () => {
  startBackend()
  createWindow()
})

app.on('before-quit', () => {
  appIsQuitting = true
  if (pythonProcess) pythonProcess.kill('SIGTERM')
})

app.on('window-all-closed', () => app.quit())

app.on('activate', () => {
  if (mainWindow === null) createWindow()
})
