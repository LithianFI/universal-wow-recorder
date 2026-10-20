"""
Application-wide constants for WoW Raid Recorder.
"""

import os
import secrets

# ============================================================================
# FILE SYSTEM CONSTANTS
# ============================================================================

# Video file extensions (used by RecordingFileManager)
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8', '.avi', '.wmv'}

# ============================================================================
# WOW GAME CONSTANTS
# ============================================================================

# Difficulty ID mappings (used by ConfigManager)
DIFFICULTY_IDS = {
    'lfr': [7, 17],           # Looking For Raid
    'normal': [1, 14],        # Normal
    'heroic': [2, 15],        # Heroic
    'mythic': [3, 16, 22, 23],    # Mythic
    'other': [4, 5, 8, 9, 24, 33],  # Timewalking, Mythic+, etc.
}

# Every player specialization ID (COMBATANT_INFO currentSpecID), used to
# sanity-check the field we read from the log.
VALID_SPEC_IDS = frozenset({
    250, 251, 252,          # Death Knight
    577, 581, 1480,         # Demon Hunter
    102, 103, 104, 105,     # Druid
    1467, 1468, 1473,       # Evoker
    253, 254, 255,          # Hunter
    62, 63, 64,             # Mage
    268, 269, 270,          # Monk
    65, 66, 70,             # Paladin
    256, 257, 258,          # Priest
    259, 260, 261,          # Rogue
    262, 263, 264,          # Shaman
    265, 266, 267,          # Warlock
    71, 72, 73,             # Warrior
})

# Watchdog limits for fights whose END never arrives (game crash, missed
# line). Generous so that slow progression pulls and long keys aren't cut.
MAX_ENCOUNTER_SECONDS = 25 * 60
MAX_DUNGEON_SECONDS = 2 * 60 * 60

# Difficulty names for display (used by file_manager.py and frontend)
DIFFICULTY_NAMES = {
    1: "Normal", 
    2: "Heroic", 
    3: "Mythic", 
    4: "Mythic+",
    5: "Timewalking", 
    7: "LFR", 
    9: "40Player",
    14: "Normal", 
    15: "Heroic", 
    16: "Mythic", 
    17: "LFR",
    22: "Mythic",
    23: "Mythic",
    24: "Timewalking",
    33: "Timewalking",
}

# ============================================================================
# APPLICATION CONSTANTS
# ============================================================================

# Default timeouts and delays (in seconds)
DEFAULT_FILE_STABILITY_CHECK_INTERVAL = 1.0 # Used by RecordingFileManager

# OBS WebSocket defaults
DEFAULT_OBS_HOST = 'localhost'              # Used by OBSClient
DEFAULT_OBS_PORT = 4455                     # Used by OBSClient
DEFAULT_OBS_TIMEOUT = 3                     # Used by OBSClient

# Web server defaults
DEFAULT_WEB_HOST = '127.0.0.1'              # Used by run.py
DEFAULT_WEB_PORT = 5001                     # Used by run.py
FLASK_SECRET_KEY = os.environ.get('WOW_RECORDER_SECRET') or secrets.token_hex(32)   # Used by run.py

# Event log size
MAX_EVENT_LOG_SIZE = 50                     # Used by run.py and AppState

# Broadcast interval (seconds)
STATUS_BROADCAST_INTERVAL = 0.5             # Used by status_broadcast_loop in run.py

# ============================================================================
# LOGGING PREFIXES (for consistent console output)
# ============================================================================

LOG_PREFIXES = {
    'APP': '[APP]',
    'PARSER': '[PARSER]',
    'STATE': '[STATE]',
    'FILE': '[FILE]',
    'OBS': '[OBS]',
    'MONITOR': '[MONITOR]',
    'CONFIG': '[CONFIG]',
    'RECORDER': '[RECORDER]',
    'WEBSOCKET': '[WEBSOCKET]',
    'DUNGEON': '[DUNGEON]',
    'PROC': '[PROC]',  # RecordingProcessor
    'WATCHER': '[WATCHER]',  # LogWatcher
}

# ============================================================================
# ERROR MESSAGES (for consistent error handling)
# ============================================================================

ERROR_MESSAGES = {
    'OBS_CONNECTION_FAILED': 'Failed to connect to OBS WebSocket',
    'LOG_DIR_NOT_FOUND': 'Log directory not found',
    'RECORDING_DIR_NOT_FOUND': 'Recording directory not found',
    'CONFIG_LOAD_FAILED': 'Failed to load configuration',
    'FILE_NOT_FOUND': 'File not found',
    'PERMISSION_DENIED': 'Permission denied',
}