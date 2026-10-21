"""
Main combat log parser coordinating all components.
"""

import time
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Callable
from pathlib import Path

from obs_client import OBSClient, StopResult
from state_manager import RecordingState
from config_manager import ConfigManager

from combat_parser.file_manager import RecordingFileManager
from combat_parser.recording_processor import RecordingProcessor
from combat_parser.dungeon_monitor import DungeonMonitor
from combat_parser.events import (
    CombatEvent, BossInfo, DungeonInfo, parse_player_name_realm, parse_combatant_spec_id,
)
from combat_parser.cooldown_scanner import collect_cooldown_casts
from metadata_generator import RecordingMetadata, RecordingCategory, DeathParser

from constants import LOG_PREFIXES

# Events that need a full CSV parse (CombatEvent) and a handler. Everything
# else, including the very frequent SPELL_DAMAGE, is handled on the raw line.
_DISPATCHED_EVENTS = frozenset((
    "ENCOUNTER_START", "ENCOUNTER_END",
    "CHALLENGE_MODE_START", "CHALLENGE_MODE_END",
    "ZONE_CHANGE",
))


def _event_name(line: str) -> str:
    """Event type of a log line ("TIMESTAMP  EVENT,..."), without parsing the rest."""
    start = line.find('  ')
    if start < 0:
        return ''
    start += 2
    end = line.find(',', start)
    return line[start:end] if end >= 0 else line[start:].strip()

# WCR ignores any unit with maxHP below this on Retail to avoid tracking
# adds/players before the boss takes its first SPELL_DAMAGE hit.
# (LogHandler.ts: private static minBossHp = 100 * 10 ** 6)
_MIN_BOSS_HP = 100 * 10 ** 6


_LOCAL_PLAYER_FLAGS = frozenset({'0x511', '0x10511'})

# Video kept after ENCOUNTER_END (matches metadata overrun). Also gives WoW
# time to flush the end of the fight to the log before we scan it. A new
# pull cuts it short so its start isn't lost.
_RECORDING_OVERRUN_SECONDS = 3

# Upper bound for a believable gap between the log's START and the video start.
_MAX_VIDEO_START_DELAY_MS = 60_000

# Long enough for an end thread to finish (log flush wait, rename delay,
# file stability check) so a save in progress isn't cut off at exit.
_SHUTDOWN_THREAD_JOIN_SECONDS = 10.0


@dataclass(frozen=True)
class _RecordingContext:
    """What an end thread needs, captured at END so a re-pull can't overwrite it."""
    metadata: RecordingMetadata
    log_path: Optional[Path]
    start_offset: Optional[int]     # byte offset of the START line, if known
    start_ts: Optional[str]         # log timestamp of START (fallback locator)
    end_ts: Optional[str]           # log timestamp of END; None = read to EOF
    start_time: Optional[datetime]
    fight_seq: int                  # _fight_seq when this fight ended


class CombatParser:
    """Main parser that coordinates combat log parsing and recording actions."""

    def __init__(self, obs_client: OBSClient, state_manager: RecordingState,
                 config_manager: ConfigManager):
        self.obs = obs_client
        self.state = state_manager
        self.config = config_manager

        # Initialize components
        self.file_manager = RecordingFileManager(config_manager, obs_client)
        self.processor = RecordingProcessor(obs_client, self.file_manager, config_manager)
        self.dungeon_monitor = DungeonMonitor(state_manager, config_manager, self._handle_recording_timeout)

        # Thread management
        self._active_threads: List[threading.Thread] = []
        self._cleanup_completed_threads()

        # Event callbacks for frontend
        self.on_event: Optional[Callable] = None
        self.on_recording_saved: Optional[Callable] = None

        # Callback to retrieve the current log file path from LogMonitor
        # Set by run.py after both parser and log_monitor are created
        self.get_log_path: Optional[Callable] = None

        # Metadata tracking
        self.current_metadata = RecordingMetadata()
        self.player_guid = None
        self.player_name = None
        self.player_realm = None
        self.player_spec_id = None
        self.encounter_start_time = None

        # Whether the current encounter/dungeon passed the recording filters.
        # The end handlers only touch OBS and files when this is set.
        self._recording_requested = False

        # Log handlers run on the tail thread and the watchdog thread.
        self._event_lock = threading.RLock()
        # OBS start/stop run on one worker in submission order, so a re-pull's
        # start can never overtake the previous pull's stop.
        self._obs_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='obs')
        # Bumped whenever a fight starts; an end job whose fight is no longer
        # the latest cuts its overrun short so the new fight isn't delayed.
        self._fight_seq = 0
        self._fight_started = threading.Condition()
        self._is_shutting_down = False

        # Where the current fight starts in the log, for the post-fight scan.
        self._line_offset: Optional[int] = None
        self._window_log_path: Optional[Path] = None
        self._window_start_offset: Optional[int] = None

        # Log timestamp of the current fight's START (scan fallback locator)
        self.encounter_start_log_timestamp: Optional[str] = None

        # Boss HP tracking — updated live from SPELL_DAMAGE events.
        # WCR strategy: track the unit with the highest maxHP seen (RaidEncounter.updateHp).
        # Initialized to 1/1 so bossPercent = 100% until we see real data.
        self._boss_current_hp: int = 1
        self._boss_max_hp: int = 1

        # Throttle update_activity() calls — no need to call time.time() on every line
        self._last_activity_update: float = 0.0

        # Start dungeon monitor
        self.dungeon_monitor.start()

    def process_line(self, line: str, offset: Optional[int] = None):
        """Process a single combat log line. *offset* is its byte position in the log."""
        with self._event_lock:
            self._line_offset = offset
            self._process_line(line)

    def _process_line(self, line: str):
        # Always try to identify the player first, before any other processing.
        # This runs on every line but short-circuits immediately once resolved.
        if not (self.player_guid and self.player_name):
            self._try_identify_player(line)

        # Throttled activity update — dungeon monitor only checks every 5 s.
        if self.state.dungeon_active:
            now = time.time()
            if now - self._last_activity_update >= 1.0:
                self.state.update_activity()
                self._last_activity_update = now

        event_name = _event_name(line)

        # COMBATANT_INFO is logged at every pull, so this also picks up spec changes.
        if event_name == 'COMBATANT_INFO':
            if self.player_guid:
                self._parse_combatant_info(line)
            return

        # Track boss HP% live from SPELL_DAMAGE events (mirrors WCR handleSpellDamage).
        if event_name == 'SPELL_DAMAGE':
            if self.state.encounter_active:
                self._track_boss_health(line)
            return

        if event_name not in _DISPATCHED_EVENTS:
            return

        # Parse the line
        event = CombatEvent(line)
        if not event.is_valid():
            return

        # Handle the event - prioritize dungeons over encounters
        if event.is_dungeon_start:
            self._handle_dungeon_start(event)
        elif event.is_dungeon_end:
            self._handle_dungeon_end(event, "dungeon_complete")
        elif event.is_zone_change:
            self._handle_zone_change(event)
        elif event.is_encounter_start:
            self._handle_encounter_start(event)
        elif event.is_encounter_end:
            self._handle_encounter_end(event)

        # Deaths are collected by _scan_log_for_encounter_data() after the
        # encounter ends — see section 8 of the reference doc. Doing it live
        # on every UNIT_DIED line is pure overhead on the hot path.
        
    def start_manual_recording(self) -> bool:
        """Start a manual recording triggered by the user."""
        with self._event_lock:
            return self._start_manual_recording()

    def _start_manual_recording(self) -> bool:
        if self.state.is_recording or self.state.manual_recording:
            print(f"{LOG_PREFIXES['PARSER']} Manual recording requested but already recording")
            return False

        print(f"{LOG_PREFIXES['PARSER']} Starting manual recording...")

        # The queued start waits behind any pending stop; cut its overrun.
        self._signal_new_fight()
        if not self._submit_obs_job(self.obs.start_recording).result():
            print(f"{LOG_PREFIXES['PARSER']} Failed to start OBS recording for manual session")
            return False

        self.state.start_manual_recording()
        self.state.start_recording()
        self.encounter_start_time = datetime.now()
        # No START line: the window begins at the last line read, ends at EOF.
        self._mark_window_start(timestamp=None)

        # Reset metadata to a clean manual baseline
        self.current_metadata.reset()
        self.current_metadata.category = RecordingCategory.MANUAL
        self.current_metadata.set_start_time(int(time.time() * 1000))
        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
            guid=self.player_guid,
            name=self.player_name,
            realm=self.player_realm or "Unknown",
            spec_id=self.player_spec_id or 0,
        )

        self._emit_event('MANUAL_RECORDING_START', '', {'boss_name': 'Manual'})
        print(f"{LOG_PREFIXES['PARSER']} Manual recording started")
        return True


    def stop_manual_recording(self) -> bool:
        """Stop an active manual recording."""
        with self._event_lock:
            return self._stop_manual_recording()

    def _stop_manual_recording(self) -> bool:
        if not self.state.manual_recording:
            print(f"{LOG_PREFIXES['PARSER']} Stop manual recording requested but no manual recording active")
            return False

        print(f"{LOG_PREFIXES['PARSER']} Stopping manual recording...")
        recording_duration = self.state.get_recording_duration()

        boss_info = BossInfo(
            boss_id=0,
            name="Manual",
            difficulty_id=0,
            instance_id=0,
            timestamp=datetime.now().strftime('%H:%M:%S'),
        )

        self._finalize_metadata(is_kill=True, duration=recording_duration)
        self._emit_event('MANUAL_RECORDING_STOP', '', {'duration': round(recording_duration, 1)})

        context = self._capture_context(end_ts=None)

        # Reset state before the background thread so is_recording becomes False immediately
        self.state.reset()

        # Reuse the existing encounter-end pipeline: stops OBS + renames file
        self._submit_obs_job(self._encounter_end_job, boss_info, recording_duration, context)

        print(f"{LOG_PREFIXES['PARSER']} Manual recording stopped ({recording_duration:.1f}s)")
        return True    


    def _try_identify_player(self, line: str):
        """Attempt to identify the player GUID and name from any combat log line.

        Regular combat events carry source and dest unit info in a predictable
        position. We look for any Player- GUID paired with a quoted name and
        use the unit flags to confirm it is the local player (0x511 = 
        AFFILIATION_MINE | REACTION_FRIENDLY | CONTROL_PLAYER | TYPE_PLAYER).

        Format (after the double-space timestamp):
          EVENT,srcGUID,"srcName",srcFlags,srcRaidFlags,
                dstGUID,"dstName",dstFlags,dstRaidFlags,...

        We operate on the raw line here rather than going through CombatEvent
        so that this stays as cheap as possible — it runs on every single line.
        """
        # Quick pre-check: must contain a Player- GUID
        if 'Player-' not in line:
            return

        try:
            # Split off the timestamp
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                return
            data = ts_split[1].strip()

            # Split into the first 9 fields (event + 4 src fields + 4 dst fields)
            # Use a limit so we don't split the whole (potentially huge) line
            parts = data.split(',', 9)
            if len(parts) < 9:
                return

            # parts[0] = eventType
            # parts[1] = srcGUID   parts[2] = srcName   parts[3] = srcFlags
            # parts[5] = dstGUID   parts[6] = dstName   parts[7] = dstFlags

            for guid_idx, name_idx, flags_idx in ((1, 2, 3), (5, 6, 7)):
                guid = parts[guid_idx].strip()
                flags = parts[flags_idx].strip()

                if not guid.startswith('Player-'):
                    continue
                if flags not in _LOCAL_PLAYER_FLAGS:
                    continue

                raw_name = parts[name_idx].strip().strip('"')
                if not raw_name or raw_name in ('nil', 'Unknown'):
                    continue

                # Name format: "CharName-RealmName-Region"
                # e.g. "Isalith-Ravencrest-EU"
                # We want CharName and RealmName, dropping the trailing region code.
                # Region codes are always 2-3 uppercase letters (EU, US, TW, KR, CN).
                name, realm = parse_player_name_realm(raw_name)
                if not name:
                    continue

                self.player_guid = guid
                self.player_name = name
                self.player_realm = realm
                print(f"{LOG_PREFIXES['PARSER']} Player identified: "
                      f"{name}-{realm} (GUID: {guid})")
                return

        except Exception as e:
            print(f"{LOG_PREFIXES['PARSER']} Error in player identification: {e}")
            
    
    def _parse_combatant_info(self, line: str):
        """Extract specID from the player's COMBATANT_INFO line.

        Field layout: see parse_combatant_spec_id.
        """
        try:
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                return

            # Split only up to field 25 to avoid parsing the huge talent/gear arrays
            parts = ts_split[1].split(',', 25)
            if len(parts) < 24:
                return

            guid = parts[1].strip()
            if guid != self.player_guid:
                return  # Not the local player's COMBATANT_INFO

            spec_id = parse_combatant_spec_id(parts)
            if spec_id > 0 and spec_id != self.player_spec_id:
                self.player_spec_id = spec_id
                print(f"{LOG_PREFIXES['PARSER']} Player specID: {spec_id}")

                # Patch metadata if an encounter/dungeon is already underway.
                # _init_metadata_for_* runs at ENCOUNTER_START / CHALLENGE_MODE_START,
                # before COMBATANT_INFO fires, so player_info may have spec_id=0.
                # Update it here now that we have the real value.
                if self.current_metadata.player_info:
                    self.current_metadata.player_info["_specID"] = spec_id
                    print(f"{LOG_PREFIXES['PARSER']} Metadata specID updated: {spec_id}")

        except (ValueError, IndexError):
            pass

    def _track_boss_health(self, line: str):
        """Update boss HP from a SPELL_DAMAGE line, mirroring WCR's handleSpellDamage.

        WCR reads SPELL_DAMAGE and calls raid.updateHp(current, max), which only
        updates if max >= current stored maxHp. This means we always track the
        highest-HP unit seen — a safe proxy for the boss without needing a hardcoded
        list of boss names (RaidEncounter.ts updateHp comment).

        SPELL_DAMAGE fields (0-indexed from event type, matching WCR's LogLine.arg()):
          arg(0)  = SPELL_DAMAGE
          arg(1)  = srcGUID      arg(2)  = srcName    arg(3)  = srcFlags
          arg(4)  = srcRaidFlags arg(5)  = destGUID   arg(6)  = destName
          arg(7)  = destFlags    arg(8)  = destRaidFlags
          arg(9)  = spellId      arg(10) = spellName  arg(11) = spellSchool
          arg(12) = amount       arg(13) = baseAmount arg(14) = currentHP (dest)
          arg(15) = maxHP (dest) ...

        WCR: arg(14) = currentHP, arg(15) = maxHP  (LogHandler.ts lines 508/524)
        """
        try:
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                return
            # Split only as far as we need (arg 15 = index 15, so 17 parts)
            parts = ts_split[1].split(',', 17)
            if len(parts) < 16:
                return

            max_hp = int(parts[15].strip())

            # WCR: skip units with maxHP < 100M on Retail (avoids tracking adds)
            if max_hp < _MIN_BOSS_HP:
                return

            current_hp = int(parts[14].strip())

            # WCR strategy: only update if this unit has more HP than any seen
            # so far — the boss will always have the highest HP in an encounter.
            if max_hp >= self._boss_max_hp:
                self._boss_max_hp = max_hp
                self._boss_current_hp = current_hp

        except (ValueError, IndexError):
            pass

    def _handle_dungeon_start(self, event: CombatEvent):
        """Handle CHALLENGE_MODE_START event."""
        # Don't start if already recording a dungeon
        if self.state.dungeon_active:
            return
    
        if self.state.manual_recording: 
            return

        # Extract dungeon information
        dungeon_info = event.get_dungeon_info()
        if not dungeon_info:
            print(f"{LOG_PREFIXES['PARSER']} Could not parse dungeon info from: {event}")
            return

        # Start the dungeon in state
        self.state.start_dungeon(
            dungeon_info.dungeon_id,
            dungeon_info.name,
            dungeon_info.dungeon_level,
            dungeon_info.timestamp
        )

        self._mark_window_start(dungeon_info.timestamp)

        # Initialize metadata for M+ dungeon
        self._init_metadata_for_dungeon(dungeon_info)

        self._recording_requested = self.processor.should_record_dungeon()
        if self._recording_requested:
            self._submit_obs_job(self._dungeon_start_job, dungeon_info, self.current_metadata)

        # Emit event for frontend
        self._emit_event('DUNGEON_START', dungeon_info.timestamp, {
            'dungeon_name': dungeon_info.name,
            'dungeon_level': dungeon_info.dungeon_level,
            'dungeon_id': dungeon_info.dungeon_id,
        })

        print(f"{LOG_PREFIXES['PARSER']} Started M+ dungeon: {dungeon_info.name} (+{dungeon_info.dungeon_level})")

    def _handle_dungeon_end(self, event, reason: str = "dungeon_complete"):
        """Handle CHALLENGE_MODE_END event."""
        if not self.state.dungeon_active:
            return
 
        dungeon_name = self.state.dungeon_name
        dungeon_level = self.state.dungeon_level
        dungeon_duration = self.state.get_encounter_duration()
 
        # event is None when called from a timeout — guard every access
        is_success = (
            reason == 'dungeon_complete'
            and event is not None
            and event.get_dungeon_end_info()[0]
        )
        timestamp = event.timestamp if event else datetime.now().strftime("%m/%d/%Y %H:%M:%S.0000")
 
        dungeon_info = DungeonInfo(
            dungeon_id=self.state.dungeon_id or 0,
            name=dungeon_name or "Unknown Dungeon",
            dungeon_level=dungeon_level or 0,
            timestamp=timestamp
        )
 
        self._finalize_metadata(is_kill=is_success, duration=dungeon_duration)
 
        self._emit_event('DUNGEON_END', timestamp, {
            'dungeon_name': dungeon_name,
            'dungeon_level': dungeon_level,
            'duration': round(dungeon_duration, 1),
            'is_success': is_success,
            'reason': reason,
        })
 
        if self._recording_requested:
            # Without an END line (timeout, log switch) scan to the end of the log.
            context = self._capture_context(end_ts=event.timestamp if event else None)
            self._submit_obs_job(self._dungeon_end_job, dungeon_info, dungeon_duration, reason, context)
        self._recording_requested = False
        self.state.reset()
 
        print(f"{LOG_PREFIXES['PARSER']} Ended M+ dungeon: {dungeon_info.name} ({reason})")

    def _handle_zone_change(self, event: CombatEvent):
        """Handle ZONE_CHANGE event during dungeon runs."""
        # Only process if we're in an active dungeon
        if not self.state.dungeon_active:
            return

        print(f"{LOG_PREFIXES['PARSER']} Zone change detected during dungeon run")

        # ZONE_CHANGE,instanceID,zoneName,difficultyID. Compare instance IDs:
        # zone names don't always match the CHALLENGE_MODE_START name.
        try:
            new_instance_id = int(event.fields[1])
        except (IndexError, ValueError):
            return
        if new_instance_id != self.state.dungeon_id:
            new_zone = event.fields[2] if len(event.fields) > 2 else '?'
            print(f"{LOG_PREFIXES['PARSER']} Zone changed from dungeon to: {new_zone}")
            self._handle_dungeon_end(event, "zone_change")

    def _handle_recording_timeout(self):
        """Watchdog callback: a fight ran past its maximum length without an END."""
        with self._event_lock:
            self._force_end_active("timeout")

    def on_log_switched(self):
        """A new combat log replaced the old one: its END will never arrive."""
        with self._event_lock:
            self._force_end_active("log_changed")
            # A new log usually means a relog, possibly onto another character.
            self.player_guid = self.player_name = self.player_realm = None
            self.player_spec_id = None

    def _force_end_active(self, reason: str):
        if self.state.dungeon_active:
            self._handle_dungeon_end(None, reason)
        elif self.state.encounter_active and not self.state.manual_recording:
            print(f"{LOG_PREFIXES['PARSER']} Ending encounter without END ({reason})")
            self._handle_encounter_end(None)

    def _handle_encounter_start(self, event: CombatEvent):
        """Handle ENCOUNTER_START event."""
        # Don't start if already recording (either encounter or dungeon)
        if self.state.is_recording:
            return
        
        if self.state.manual_recording:  
            return

        # Extract boss information
        boss_info = event.get_boss_info()
        if not boss_info:
            print(f"{LOG_PREFIXES['PARSER']} Could not parse boss info from: {event}")
            return

        # Apply boss name overrides
        overrides = self.config.BOSS_NAME_OVERRIDES
        if boss_info.boss_id in overrides:
            boss_info.name = overrides[boss_info.boss_id]
    
        # Start the encounter in state
        self.state.start_encounter(
            boss_info.boss_id, boss_info.name,
            boss_info.difficulty_id, boss_info.instance_id
        )

        self._mark_window_start(boss_info.timestamp)

        # Reset boss HP tracking for this new pull
        self._boss_current_hp = 1
        self._boss_max_hp = 1

        # Initialize metadata for encounter
        self._init_metadata_for_encounter(boss_info)

        self._recording_requested = self.processor.should_record_encounter(boss_info)
        if self._recording_requested:
            self._submit_obs_job(self._encounter_start_job, boss_info, self.current_metadata)

        # Emit event for frontend
        self._emit_event('ENCOUNTER_START', boss_info.timestamp, {
            'boss_name': boss_info.name,
            'difficulty_id': boss_info.difficulty_id,
        })

        print(f"{LOG_PREFIXES['PARSER']} Started encounter: {boss_info.name}")

    def _handle_encounter_end(self, event: CombatEvent):
        """Handle ENCOUNTER_END event."""
        # Manual recordings set encounter_active too, but only the user stops them.
        if not self.state.encounter_active or self.state.manual_recording:
            return
 
        boss_name = self.state.boss_name
        difficulty_id = self.state.difficulty_id
        encounter_duration = self.state.get_encounter_duration()
        # event is None when forced (timeout / log switch): record it as a wipe.
        is_kill = event.get_encounter_end_info()[0] if event else False
        end_ts = event.timestamp if event else None

        boss_info = BossInfo(
            boss_id=self.state.boss_id or 0,
            name=boss_name or "Unknown",
            difficulty_id=difficulty_id or 0,
            instance_id=self.state.instance_id or 0,
            timestamp=end_ts or datetime.now().strftime("%m/%d/%Y %H:%M:%S.0000")
        )
 
        boss_hp_percent = round((100 * self._boss_current_hp) / self._boss_max_hp)
        self._finalize_metadata(is_kill=is_kill, duration=encounter_duration,
                                fight_percentage=boss_hp_percent)
 
        self._emit_event('ENCOUNTER_END', boss_info.timestamp, {
            'boss_name': boss_name,
            'difficulty_id': difficulty_id,
            'duration': round(encounter_duration, 1),
            'is_kill': is_kill,
        })
 
        if self._recording_requested:
            context = self._capture_context(end_ts)
            self._submit_obs_job(self._encounter_end_job, boss_info, encounter_duration, context)
        self._recording_requested = False
        self.state.reset()
 
        print(f"{LOG_PREFIXES['PARSER']} Ended encounter: {boss_info.name}")

    def _init_metadata_for_encounter(self, boss_info: BossInfo):
        """Initialize metadata for a new raid/boss encounter."""
        self.current_metadata.reset()

        # Explicit category for boss encounters
        self.current_metadata.category = RecordingCategory.RAIDS
        self.current_metadata.overrun = 3  # WCR RaidEncounter: this.overrun = 3

        self.current_metadata.set_encounter_info(
            encounter_id=boss_info.boss_id,
            encounter_name=boss_info.name,
            difficulty_id=boss_info.difficulty_id,
        )

        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
                guid=self.player_guid,
                name=self.player_name,
                realm=self.player_realm or "Unknown",
                spec_id=self.player_spec_id or 0,
            )

        start_ms = self._parse_timestamp_to_ms(boss_info.timestamp)
        self.current_metadata.set_start_time(start_ms)
        self.encounter_start_time = datetime.now()

    def _init_metadata_for_dungeon(self, dungeon_info: DungeonInfo):
        """Initialize metadata for a Mythic+ dungeon run."""
        self.current_metadata.reset()

        # Explicit category for M+ dungeons
        self.current_metadata.category = RecordingCategory.MYTHIC_PLUS

        self.current_metadata.set_encounter_info(
            encounter_id=dungeon_info.dungeon_id,
            encounter_name=f"{dungeon_info.name} +{dungeon_info.dungeon_level}",
            difficulty_id=8,  # Mythic+ difficulty ID
        )
        self.current_metadata.set_keystone_info(
            zone_id=dungeon_info.dungeon_id,
            zone_name=dungeon_info.name,
            map_id=dungeon_info.challenge_mode_id,
            keystone_level=dungeon_info.dungeon_level,
            affixes=dungeon_info.affixes,
        )

        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
                guid=self.player_guid,
                name=self.player_name,
                realm=self.player_realm or "Unknown",
                spec_id=self.player_spec_id or 0,
            )

        start_ms = self._parse_timestamp_to_ms(dungeon_info.timestamp)
        self.current_metadata.set_start_time(start_ms)
        self.encounter_start_time = datetime.now()

    def _finalize_metadata(self, is_kill: bool, duration: float, fight_percentage: float = 0.0):
        """Finalize metadata with encounter result."""
        # On a kill the log reports 0% remaining; store as 0 (dead).
        # On a wipe it reports the actual HP% remaining — use that directly.
        boss_percent = 0 if is_kill else fight_percentage

        self.current_metadata.set_result(
            is_kill=is_kill,
            duration=duration,
            boss_percent=boss_percent,
        )

        # Deaths already added to current_metadata by _scan_log_for_deaths()
        # which is called before _finalize_metadata on encounter/dungeon end.

    def _mark_window_start(self, timestamp: Optional[str]):
        """Remember where this fight starts in the log for the post-fight scan."""
        self.encounter_start_log_timestamp = timestamp
        self._window_start_offset = self._line_offset
        self._window_log_path = self.get_log_path() if self.get_log_path else None
        self._signal_new_fight()

    def _signal_new_fight(self):
        with self._fight_started:
            self._fight_seq += 1
            self._fight_started.notify_all()

    def _capture_context(self, end_ts: Optional[str]) -> _RecordingContext:
        """Hand the finished fight's state to its end thread and start fresh.

        A re-pull can begin while the end thread is still scanning, so the end
        thread must not read shared parser state.
        """
        context = _RecordingContext(
            metadata=self.current_metadata,
            log_path=self._window_log_path,
            start_offset=self._window_start_offset,
            start_ts=self.encounter_start_log_timestamp,
            end_ts=end_ts,
            start_time=self.encounter_start_time,
            fight_seq=self._fight_seq,
        )
        self.current_metadata = RecordingMetadata()
        return context

    def _read_window_lines(self, context: _RecordingContext) -> Optional[List[str]]:
        """Log lines from the fight's START up to its END (or EOF)."""
        log_path = context.log_path
        if not log_path or not log_path.exists():
            print(f"{LOG_PREFIXES['PARSER']} Log file not available for encounter data scan")
            return None
        if context.start_offset is None and not context.start_ts:
            print(f"{LOG_PREFIXES['PARSER']} Missing encounter start for data scan")
            return None

        window_lines = []
        try:
            with open(log_path, 'rb') as f:
                # Seeking skips re-reading a multi-GB raid-night log from byte 0.
                in_window = context.start_offset is not None
                if in_window:
                    f.seek(context.start_offset)
                for raw in f:
                    # One bad byte must not abort the scan.
                    line = raw.decode('utf-8', errors='replace')
                    if not in_window:
                        if context.start_ts not in line:
                            continue
                        in_window = True
                    window_lines.append(line)
                    if context.end_ts and context.end_ts in line:
                        break
        except OSError as e:
            print(f"{LOG_PREFIXES['PARSER']} Error reading log for encounter data: {e}")
            return None
        return window_lines

    def _scan_log_for_encounter_data(self, context: _RecordingContext):
        """Collect deaths, combatants and cooldown casts for a finished fight.

        WoW only flushes combat log data to disk after the encounter ends, so
        neither deaths nor combatant info can be collected in real-time -- we must
        read them retroactively once ENCOUNTER_END / CHALLENGE_MODE_END fires.

        Player names are not in COMBATANT_INFO directly -- we build a GUID->name
        map from srcGUID/srcName pairs seen throughout the encounter window.
        """
        window_lines = self._read_window_lines(context)
        if window_lines is None:
            return
        metadata = context.metadata
        print(f"{LOG_PREFIXES['PARSER']} Scanning {len(window_lines)} log lines for encounter data")

        deaths_found = 0
        combatants_found = 0

        # GUID -> (name, realm) map built from any combat event in the window
        guid_to_name: dict = {}

        # Build GUID->name map from src/dest fields in combat event lines.
        # Standard combat event format (after timestamp double-space):
        #   eventType, srcGUID, srcName, srcFlags, srcRaidFlags,
        #   destGUID, destName, destFlags, destRaidFlags, ...
        # We skip COMBATANT_INFO lines because their field layout is different
        # (field [2] is faction, not a name) and would corrupt the map.
        for line in window_lines:
            try:
                ts_split = line.split('  ', 1)
                if len(ts_split) < 2:
                    continue
                event_data = ts_split[1]
                # Skip COMBATANT_INFO - field layout is incompatible with src/dest parsing
                if event_data.startswith('COMBATANT_INFO'):
                    continue
                parts = event_data.split(',', 9)
                if len(parts) < 7:
                    continue
                for guid_idx, name_idx in ((1, 2), (5, 6)):
                    guid = parts[guid_idx].strip()
                    if not guid.startswith('Player-'):
                        continue
                    raw_name = parts[name_idx].strip().strip('"')
                    if not raw_name or raw_name in ('nil', 'Unknown'):
                        continue
                    if guid not in guid_to_name:
                        # Format: "CharName-Realm-EU" or "CharName-Realm-US" etc.
                        # Strip the trailing region code (2-3 uppercase letters)
                        # then split on the first '-' to get name vs realm.
                        name, realm = parse_player_name_realm(raw_name)
                        guid_to_name[guid] = (name, realm)
            except Exception:
                continue

        guid_to_spec: dict = {}
        for line in window_lines:
            is_combatant = 'COMBATANT_INFO' in line
            is_death = 'UNIT_DIED' in line
 
            if not is_combatant and not is_death:
                continue
 
            if is_death and self.config.TRACK_PLAYER_DEATHS:
                death_info = DeathParser.parse_death_line(line)
                if death_info:
                    spec_id = guid_to_spec.get(death_info['guid'], 0)
                    metadata.add_death(
                        name=death_info['name'],
                        timestamp_ms=death_info['timestamp'],
                        spec_id=spec_id,
                    )
                    deaths_found += 1
                    print(f"{LOG_PREFIXES['PARSER']} 💀 Death: {death_info['name']}")
 
            if is_combatant:
                try:
                    ts_split = line.split('  ', 1)
                    if len(ts_split) < 2:
                        continue
                    parts = ts_split[1].split(',', 27)
                    if len(parts) < 26:
                        continue
 
                    guid = parts[1].strip()
                    if not guid.startswith('Player-'):
                        continue
 
                    faction = int(parts[2].strip())
                    spec_id = parse_combatant_spec_id(parts)
 
                    # Always update guid_to_spec so any death lines later in this
                    # same pass can look up the spec_id for this player.
                    if spec_id > 0:
                        guid_to_spec[guid] = spec_id
 
                    if guid not in guid_to_name:
                        continue
 
                    name, realm = guid_to_name[guid]
 
                    if guid == self.player_guid and spec_id > 0:
                        self.player_spec_id = spec_id
                        if metadata.player_info:
                            metadata.player_info["_specID"] = spec_id
 
                    metadata.add_combatant(
                        guid=guid,
                        name=name,
                        realm=realm,
                        spec_id=spec_id,
                        team_id=faction,
                    )
                    combatants_found += 1
 
                except (ValueError, IndexError):
                    continue
 
        # Collect tracked cooldown casts from the same window lines.
        # guid_to_name is already built above so caster names resolve cleanly.
        if metadata.start_timestamp:
            casts = collect_cooldown_casts(
                window_lines,
                metadata.start_timestamp,
                guid_to_name,
            )
            for cast in casts:
                metadata.add_cooldown_cast(cast)
        else:
            casts = []

        print(f"{LOG_PREFIXES['PARSER']} Scan complete: {deaths_found} deaths, "
              f"{combatants_found} combatants, {len(casts)} cooldown casts found")

        # Recompute uniqueHash now that combatants are fully populated.
        # _finalize_metadata() runs before this scan, so the hash it computed
        # had an empty combatants list — WCR only writes metadata after all
        # processing is done, so we match that by recomputing here.
        metadata._recompute_hash()

    def _parse_timestamp_to_ms(self, timestamp: str) -> int:
        """Parse combat log timestamp to milliseconds."""
        try:
            # Format: "2/11/2026 21:02:44.3002" or "21:02:44.3002"
            if '/' in timestamp:
                dt_str, ms_str = timestamp.rsplit(".", 1)
                dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
            else:
                # Time only, use today's date
                time_str, ms_str = timestamp.rsplit(".", 1)
                today = datetime.now().date()
                dt = datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M:%S")
            
            timestamp_ms = int(dt.timestamp() * 1000)
            timestamp_ms += int(ms_str[:3])  # First 3 digits of fractional seconds
            return timestamp_ms
        except Exception as e:
            print(f"{LOG_PREFIXES['PARSER']} Error parsing timestamp: {e}")
            return int(datetime.now().timestamp() * 1000)

    def _submit_obs_job(self, job: Callable, *args) -> Future:
        """Queue an OBS step. Exceptions are logged (a Future would hide them)."""
        def run():
            try:
                return job(*args)
            except Exception:
                print(f"{LOG_PREFIXES['PARSER']} OBS job {job.__name__} failed:")
                traceback.print_exc()
                return None
        return self._obs_executor.submit(run)

    def _dungeon_start_job(self, dungeon_info: DungeonInfo, metadata: RecordingMetadata):
        if self.processor.process_dungeon_start(dungeon_info):
            self._note_video_start(metadata)
            self.state.start_recording()

    def _encounter_start_job(self, boss_info: BossInfo, metadata: RecordingMetadata):
        if self.processor.process_encounter_start(boss_info):
            self._note_video_start(metadata)
            self.state.start_recording()

    @staticmethod
    def _note_video_start(metadata: RecordingMetadata):
        """Record how far the video start lags the log's START.

        WoW writes the log in bursts, so START is often read seconds late;
        death/cooldown offsets are relative to START, the video isn't.
        Implausible values (clock changes, replayed logs) are ignored.
        """
        if not metadata.start_timestamp:
            return
        delay_ms = int(time.time() * 1000) - metadata.start_timestamp
        metadata.video_start_delay_ms = delay_ms if 0 <= delay_ms <= _MAX_VIDEO_START_DELAY_MS else 0

    def _stop_after_overrun(self, context: _RecordingContext) -> StopResult:
        """Keep recording briefly past the END, then stop OBS."""
        with self._fight_started:
            self._fight_started.wait_for(
                lambda: self._fight_seq != context.fight_seq or self._is_shutting_down,
                timeout=_RECORDING_OVERRUN_SECONDS)
        return self.processor.stop_recording()

    def _dungeon_end_job(self, dungeon_info: DungeonInfo, duration: float,
                         reason: str, context: _RecordingContext):
        stop_result = self._stop_after_overrun(context)
        # Scanning and renaming take seconds; don't hold up the next start.
        self._start_thread(self._process_dungeon_end_thread,
                           stop_result, dungeon_info, duration, reason, context)

    def _encounter_end_job(self, boss_info: BossInfo, duration: float,
                           context: _RecordingContext):
        stop_result = self._stop_after_overrun(context)
        self._start_thread(self._process_encounter_end_thread,
                           stop_result, boss_info, duration, context)

    def _process_dungeon_end_thread(self, stop_result: StopResult, dungeon_info: DungeonInfo,
                                    duration: float, reason: str, context: _RecordingContext):
        """Thread function: scan the log and save the stopped dungeon recording."""
        self._scan_log_for_encounter_data(context)

        result = self.processor.process_dungeon_end(
            stop_result,
            dungeon_info,
            duration,
            reason,
            metadata=context.metadata,
            start_time=context.start_time
        )

        if result and self.on_recording_saved:
            self.on_recording_saved({
                'path': str(result),
                'metadata': context.metadata,
                'duration': duration,
                'boss_name': f"{dungeon_info.name} +{dungeon_info.dungeon_level}",
                'difficulty_id': 4,
                'is_kill': reason == 'dungeon_complete',
                'category': 'dungeon',
            })

    def _process_encounter_end_thread(self, stop_result: StopResult, boss_info: BossInfo,
                                      duration: float, context: _RecordingContext):
        """Thread function: scan the log and save the stopped encounter recording."""
        self._scan_log_for_encounter_data(context)

        result = self.processor.process_encounter_end(
            stop_result,
            boss_info,
            duration,
            metadata=context.metadata,
            start_time=context.start_time
        )

        if result and self.on_recording_saved:
            self.on_recording_saved({
                'path': str(result),
                'metadata': context.metadata,
                'duration': duration,
                'boss_name': boss_info.name,
                'difficulty_id': boss_info.difficulty_id,
                'is_kill': context.metadata.result,
                'category': 'raid',
            })

    def _start_thread(self, target: Callable, *args):
        """Helper to start a background thread."""
        self._cleanup_completed_threads()  # prune dead entries before adding new one
        thread = threading.Thread(
            target=target,
            args=args,
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

    def _emit_event(self, event_type: str, timestamp: str, data: dict):
        """Helper to emit events to frontend."""
        if self.on_event:
            self.on_event({
                'type': event_type,
                'timestamp': timestamp,
                **data
            })

    def _cleanup_completed_threads(self):
        """Remove completed threads from active list."""
        self._active_threads = [t for t in self._active_threads if t.is_alive()]

    def get_status(self) -> dict:
        """Get parser status."""
        return {
            'active_threads': len(self._active_threads),
            'dungeon_monitor_running': self.dungeon_monitor.is_running(),
            'last_renamed_path': str(self.file_manager.last_renamed_path) if self.file_manager.last_renamed_path else None,
        }

    def shutdown(self):
        """Clean shutdown of the parser."""
        print(f"{LOG_PREFIXES['PARSER']} Shutting down...")

        # Stop dungeon monitor
        self.dungeon_monitor.stop()

        # Let queued OBS steps finish (each is bounded by the overrun wait);
        # end jobs spawn the threads joined below.
        with self._fight_started:
            self._is_shutting_down = True
            self._fight_started.notify_all()
        self._obs_executor.shutdown(wait=True)

        # Clean up completed threads
        self._cleanup_completed_threads()

        # Wait for active threads to complete (with timeout)
        for thread in self._active_threads:
            if thread.is_alive():
                thread.join(timeout=_SHUTDOWN_THREAD_JOIN_SECONDS)

        self._active_threads.clear()
        print(f"{LOG_PREFIXES['PARSER']} Shutdown complete")
