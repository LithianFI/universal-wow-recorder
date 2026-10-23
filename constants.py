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

# WoW DifficultyID (Difficulty.db2, checked on wago.tools) ->
# (display name, short label, recording toggle).
# The short label is the sidecar's "difficulty" field (WCR's letter); content
# without a letter of its own keeps "N" as before. Party difficulties (1, 2,
# 23) share the toggle of the same raid tier; legacy fixed-size raids, keys
# and timewalking fall under record_other (M+ runs have record_mplus).
DIFFICULTIES = {
    1:   ('Normal', 'N', 'normal'),               # party
    2:   ('Heroic', 'H', 'heroic'),               # party
    3:   ('10 Player', 'N', 'other'),             # legacy raid
    4:   ('25 Player', 'N', 'other'),             # legacy raid
    5:   ('10 Player (Heroic)', 'H', 'other'),    # legacy raid
    6:   ('25 Player (Heroic)', 'H', 'other'),    # legacy raid
    7:   ('Looking For Raid', 'LFR', 'lfr'),      # legacy LFR
    8:   ('Mythic Keystone', 'M+', 'other'),      # boss inside a key; runs use 8 too
    9:   ('40 Player', 'N', 'other'),             # legacy raid
    14:  ('Normal', 'N', 'normal'),
    15:  ('Heroic', 'H', 'heroic'),
    16:  ('Mythic', 'M', 'mythic'),
    17:  ('Looking For Raid', 'LFR', 'lfr'),
    23:  ('Mythic', 'M', 'mythic'),               # party (Mythic 0)
    24:  ('Timewalking', 'N', 'other'),           # party
    33:  ('Timewalking', 'N', 'other'),           # raid
    233: ('Mythic', 'M', 'mythic'),               # raid
}

# Set on M+ run metadata (WCR does the same).
MYTHIC_KEYSTONE_DIFFICULTY_ID = 8

# Recording toggle -> its difficulty IDs (Difficulties record_<kind>).
DIFFICULTY_IDS = {
    kind: frozenset(i for i, (_, _, k) in DIFFICULTIES.items() if k == kind)
    for kind in ('lfr', 'normal', 'heroic', 'mythic', 'other')
}


def difficulty_name(difficulty_id: int) -> str:
    entry = DIFFICULTIES.get(difficulty_id)
    return entry[0] if entry else f"Difficulty {difficulty_id}"


def difficulty_short(difficulty_id: int) -> str:
    """WCR's difficulty letter for the sidecar ("N" when there is none)."""
    entry = DIFFICULTIES.get(difficulty_id)
    return entry[1] if entry else "N"

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

# ============================================================================
# APPLICATION CONSTANTS
# ============================================================================

# Default timeouts and delays (in seconds)
DEFAULT_FILE_STABILITY_CHECK_INTERVAL = 1.0 # Used by RecordingFileManager
# How long OBS may keep finalizing a stopped recording (a long M+ run on a
# slow disk) before the file is given up on.
FILE_STABILITY_TIMEOUT_SECONDS = 60.0

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
CLOUD_STATUS_INTERVAL_SECONDS = 5           # Cloud queue/storage push to clients
OBS_RECONNECT_INTERVAL_SECONDS = 15         # Passive reconnect while OBS is down

# Downloaded POVs with the same uniqueHash but a start further apart than this
# are a different pull of the same boss.
POV_START_TOLERANCE_MS = 60 * 1000

# Dashboard warns when the recordings drive has less free space than this.
LOW_DISK_SPACE_BYTES = 10 * 1024 ** 3
DISK_CHECK_INTERVAL_SECONDS = 30

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
