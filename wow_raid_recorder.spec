# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for WoW Raid Recorder
# Build with: pyinstaller wow_raid_recorder.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        # Bundle all HTML templates from the new package templates folder.
        # Flask (initialised in src/wow_raid_recorder/app.py with Flask(__name__))
        # looks for a sibling `templates/` directory, so we ship them next to
        # the package module.
        ('src/wow_raid_recorder/templates/index.html',      'wow_raid_recorder/templates'),
        ('src/wow_raid_recorder/templates/config.html',     'wow_raid_recorder/templates'),
        ('src/wow_raid_recorder/templates/recordings.html', 'wow_raid_recorder/templates'),
        ('src/wow_raid_recorder/templates/stats.html',      'wow_raid_recorder/templates'),
        ('src/wow_raid_recorder/templates/clips.html',      'wow_raid_recorder/templates'),
        # Bundle example config
        ('config_ini.example', '.'),
        # Linux terminal launcher (sits next to the binary)
        ('WoWRaidRecorder.sh', '.'),
    ],
    hiddenimports=[
        # Flask + SocketIO internals that PyInstaller misses
        'flask',
        'flask_socketio',
        'engineio',
        'socketio',
        'engineio.async_drivers.threading',
        'socketio.async_drivers.threading',
        # Watchdog platform backends
        'watchdog.observers',
        'watchdog.observers.polling',
        'watchdog.events',
        # OBS websocket
        'obsws_python',
        # Google API (optional cloud upload)
        'google.auth',
        'google.auth.transport.requests',
        'google_auth_oauthlib.flow',
        'googleapiclient.discovery',
        'googleapiclient.http',
        # Standard lib helpers
        'configparser',
        'asyncio',
        'threading',
        'signal',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WoWRaidRecorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # Keep console window so users can see log output
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows only: show a nice icon if you have one
    # icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WoWRaidRecorder',
)
