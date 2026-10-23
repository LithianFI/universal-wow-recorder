"""
Metadata Generator for WoW Raid Recorder.
Generates WCR-compatible JSON metadata files for recordings.
"""

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from sidecar_cache import write_sidecar

class RecordingCategory:
    """Valid category strings matching WCR VideoCategory enum."""
    RAIDS   = "Raids"
    MYTHIC_PLUS = "Mythic+"
    MANUAL  = "Manual"


class RecordingMetadata:
    def __init__(self):
        self.reset()
        
    def reset(self):
        """Reset metadata for a new recording."""
        self.category = RecordingCategory.MANUAL
        self.zone_id = 0
        self.zone_name = "Unknown"
        self.flavour = "Retail"
        self.encounter_id = None
        self.encounter_name = None
        self.difficulty_id = None
        self.difficulty = None
        self.duration = 0
        self.result = False
        self.player_info = {}
        self.deaths = []
        self.overrun = 0
        self.combatants = []
        self.cooldown_casts = []
        self.start_timestamp = None
        # How long after the log's START the video began (log flush lag,
        # OBS start). Our own field: markers are shifted by it in the player.
        self.video_start_delay_ms = 0
        self.unique_hash = None
        self.boss_percent = 0
        self.app_version = "7.5.1"
        # M+ specific fields
        self.keystone_level: Optional[int] = None
        self.map_id: Optional[int] = None
        self.upgrade_level: int = 0
        self.affixes: Optional[List[int]] = None
    
    def set_encounter_info(self, encounter_id: int, encounter_name: str, 
                          difficulty_id: int, zone_id: int = 0, zone_name: str = "Unknown Raid"):
        """Set basic encounter information."""
        self.encounter_id = encounter_id
        self.encounter_name = encounter_name
        self.difficulty_id = difficulty_id
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.difficulty = self._get_difficulty_shorthand(difficulty_id)
    
    def set_keystone_info(self, zone_id: int, zone_name: str, map_id: int,
                          keystone_level: int, affixes: List[int]):
        """Set the Mythic+ fields WCR's ChallengeModeDungeon metadata carries."""
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.map_id = map_id
        self.keystone_level = keystone_level
        self.affixes = list(affixes)

    def set_player_info(self, guid: str, name: str, realm: str, spec_id: int, team_id: int = 1):
        """Set information about the recording player."""
        self.player_info = {
            "_GUID": guid,
            "_teamID": team_id,
            "_specID": spec_id,
            "_name": name,
            "_realm": realm
        }
    
    def add_combatant(self, guid: str, name: str, realm: str, spec_id: int, team_id: int):
        """Add a combatant to the encounter."""
        combatant = {
            "_GUID": guid,
            "_teamID": team_id,
            "_specID": spec_id,
            "_name": name,
            "_realm": realm
        }
        
        # Avoid duplicates
        if combatant not in self.combatants:
            self.combatants.append(combatant)
    
    def add_death(self, name: str, timestamp_ms: int, spec_id: int = 0, friendly: bool = True):
        """Add a player death event.

        Shape matches WCR's PlayerDeathType:
          { name, specId, date (ISO), timestamp (seconds from video start), friendly }

        WCR (LogHandler.ts line 193):
          deathDate = (line.date().getTime() - 2) / 1000   ← 2ms subtracted, whole-second wall clock
          relativeTime = deathDate - activityStartDate (in seconds)

        line.date() truncates to whole seconds (LogLine.ts sets milliseconds to 0),
        so we do the same. The -2ms nudge matches WCR's intentional 2ms lookback.
        """
        from datetime import datetime, timezone

        # Truncate to whole seconds (WCR's line.date() sets ms to 0)
        timestamp_s_truncated = (timestamp_ms // 1000) * 1000

        # WCR date format: "2026-05-13T20:55:45.000Z"
        date_iso = datetime.fromtimestamp(timestamp_s_truncated / 1000, tz=timezone.utc) \
                           .strftime('%Y-%m-%dT%H:%M:%S.000Z')

        # WCR: deathDate = (wallClockMs - 2) / 1000
        #      relativeTime = deathDate - (startMs / 1000)
        if self.start_timestamp:
            offset_secs = ((timestamp_s_truncated - 2) / 1000) - (self.start_timestamp / 1000)
        else:
            offset_secs = (timestamp_s_truncated - 2) / 1000

        self.deaths.append({
            "name": name,
            "specId": spec_id,
            "date": date_iso,
            "timestamp": offset_secs,
            "friendly": friendly,
        })
    
    def add_cooldown_cast(self, cast: dict):
        """Append a tracked cooldown cast event.

        cast dict shape (produced by cooldown_scanner.collect_cooldown_casts):
          spellId, name, casterName, casterGuid, offsetMs, class, spec, category, icon
        """
        self.cooldown_casts.append(cast)

    def set_result(self, is_kill: bool, duration: float, boss_percent: float = 0):
        """Set encounter result and recompute uniqueHash (result is part of the hash)."""
        self.result = is_kill
        self.duration = int(duration)
        self.boss_percent = int(boss_percent)
        # Result changed — hash must be recomputed
        if self.start_timestamp is not None:
            self._recompute_hash()
    
    def set_start_time(self, timestamp_ms: int):
        """Set encounter start timestamp and compute uniqueHash using WCR's algorithm.

        WCR Activity.getUniqueHash():
          md5( category + ' ' + flavour + ' ' + str(result)
               + sorted_combatant_names_joined )

        This must match exactly so the backend can group multi-PoV videos.
        """
        self.start_timestamp = timestamp_ms
        self._recompute_hash()
    
    def _recompute_hash(self):
        """Recompute the uniqueHash to exactly match Activity.getUniqueHash() in WCR.

        WCR (Activity.ts):
          deterministicFields = [category, flavour, result].map(f => f.toString())
          sortedNames = combatantMap.values().map(c => c.name).sort().filter(Boolean)
          uniqueString = deterministicFields.join(' ') + sortedNames.join(' ')
          md5(uniqueString)

        Key points:
        - result.toString() in JS: true → 'true', false → 'false'
        - Names are joined with a SPACE (not empty string)
        - The two halves are concatenated directly with NO space between them
          e.g. "Raids Retail trueName1 Name2"
        - The combatantMap includes the player — WCR adds the player via addCombatant
          the same way as any other combatant, so we include player_info here too.
        - combatantMap is keyed by GUID: two players sharing a name (different
          realms) contribute the name twice, so names are not deduplicated.
        - JS sort() orders by UTF-16 code units, not code points.
        """
        import hashlib

        result_str = 'true' if self.result else 'false'
        deterministic = f"{self.category} {self.flavour} {result_str}"

        # One name per GUID, like WCR's combatantMap (player included).
        names_by_guid = {}
        for c in [*self.combatants, self.player_info]:
            name = c.get('_name')
            if name:
                names_by_guid[c.get('_GUID') or name] = name

        sorted_names = sorted(names_by_guid.values(), key=lambda n: n.encode('utf-16-be'))

        # Mirror JS: deterministicFields.join(' ') + sortedNames.join(' ')
        # No space between the two halves — that's what WCR produces.
        unique_string = deterministic + ' '.join(sorted_names)
        self.unique_hash = hashlib.md5(unique_string.encode('utf-8')).hexdigest()
    
    def generate_filename(self, start_datetime: datetime, extension: str = ".mp4") -> str:
        """
        Generate WCR-style filename.
        
        Format: YYYY-MM-DD HH-MM-SS - PlayerName - BossName [Difficulty] (Result).ext
        Example: 2026-01-21 21-11-57 - Isalith - Nexus-King Salhadaar [M] (Kill).mp4
        
        Args:
            start_datetime: DateTime when recording started
            extension: File extension (default .mp4)
            
        Returns:
            Formatted filename string
        """
        # Format: YYYY-MM-DD HH-MM-SS (space between date and time, matching WCR)
        date_str = start_datetime.strftime("%Y-%m-%d %H-%M-%S")

        if not self.encounter_name or not self.player_info:
            # Fallback: use timestamp only, still using WCR-style space separator
            return f"{date_str}{extension}"

        # Player name
        player_name = self.player_info.get("_name", "Unknown")

        # Boss name
        boss_name = self.encounter_name

        # Difficulty shorthand
        difficulty_str = self.difficulty or "N"

        # Result
        result_str = "Kill" if self.result else "Wipe"

        # Combine: YYYY-MM-DD HH-MM-SS - PlayerName - BossName [Difficulty] (Result)
        filename = f"{date_str} - {player_name} - {boss_name} [{difficulty_str}] ({result_str}){extension}"

        # Sanitize filename (remove invalid characters)
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        return filename

    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary matching WCR format."""
        data = {
            "category": self.category,
            "zoneID": self.zone_id,
            "zoneName": self.zone_name,
            "flavour": self.flavour,
            "encounterID": self.encounter_id,
            "encounterName": self.encounter_name,
            "difficultyID": self.difficulty_id,
            "difficulty": self.difficulty,
            "duration": self.duration,
            "result": self.result,
            "player": self.player_info,
            "deaths": self.deaths,
            "overrun": self.overrun,
            "combatants": self.combatants,
            "cooldownCasts": self.cooldown_casts,
            "start": self.start_timestamp,
            "videoStartDelayMs": self.video_start_delay_ms,
            "uniqueHash": self.unique_hash,
            "bossPercent": self.boss_percent,
            "appVersion": self.app_version
        }
        if self.keystone_level is not None:
            data.update({
                "mapID": self.map_id,
                "keystoneLevel": self.keystone_level,
                "upgradeLevel": self.upgrade_level,
                "affixes": self.affixes or [],
            })
        return data
    
    def save_json(self, filepath: Path) -> bool:
        """
        Save metadata to JSON file.
        
        Args:
            filepath: Path where to save the JSON file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            write_sidecar(filepath, self.to_dict())

            print(f"[Metadata] ✅ Saved metadata to {filepath.name}")
            return True
            
        except Exception as e:
            print(f"[Metadata] ❌ Failed to save metadata: {e}")
            return False
    
    def _get_difficulty_shorthand(self, difficulty_id: int) -> str:
        """Get difficulty shorthand letter."""
        difficulty_map = {
            1: "N",   # Normal (5-player)
            2: "H",   # Heroic (5-player)
            3: "M",   # Mythic
            4: "M+",  # Mythic+
            8: "M+",  # Mythic Keystone (set for dungeon runs by the parser)
            7: "LFR", # LFR
            14: "N",  # Normal (raid)
            15: "H",  # Heroic (raid)
            16: "M",  # Mythic (raid)
            17: "LFR",# LFR (raid)
            22: "M",  # Mythic Flex
            23: "M",  # Mythic
        }
        return difficulty_map.get(difficulty_id, "N")


class DeathParser:
    """Parses UNIT_DIED events from combat logs."""
    
    @staticmethod
    def parse_death_line(line: str) -> Optional[Dict[str, Any]]:
        """
        Parse a UNIT_DIED line from combat log.
        
        Format: TIMESTAMP  UNIT_DIED,sourceGUID,sourceName,sourceFlags,...,destGUID,destName,destFlags,...
        Example: 2/11/2026 21:03:23.2292  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-1403-0A330CE3,"Amitrees-Draenor-EU",0x514,0x80000000,0
        
        Args:
            line: Combat log line
            
        Returns:
            Dictionary with death information or None if not a death event
        """
        if "UNIT_DIED" not in line:
            return None
        
        try:
            # Split timestamp and event
            parts = line.split("  ", 1)
            if len(parts) < 2:
                return None
            
            timestamp_str = parts[0].strip()
            event_data = parts[1].strip()
            
            # Parse timestamp (format: M/D/YYYY HH:MM:SS.ssss)
            timestamp = DeathParser._parse_timestamp(timestamp_str)
            
            # Split event data by comma
            fields = event_data.split(",")
            
            if len(fields) < 9:
                return None
            
            # Extract player GUID (field 5) and name (field 6)
            # Base parameters: [0]=eventType, [1]=srcGUID, [2]=srcName, [3]=srcFlags,
            # [4]=srcRaidFlags, [5]=destGUID, [6]=destName, [7]=destFlags, [8]=destRaidFlags
            player_guid = fields[5].strip()
            player_name = fields[6].strip().strip('"')
            
            # Only track player deaths
            if not player_guid.startswith("Player-"):
                return None
            
            return {
                "timestamp": timestamp,
                "guid": player_guid,
                "name": player_name,
            }
            
        except Exception as e:
            print(f"[DeathParser] Error parsing death line: {e}")
            return None
    
    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> int:
        """
        Parse combat log timestamp to milliseconds.
        
        Args:
            timestamp_str: Timestamp string (M/D/YYYY HH:MM:SS.ssss)
            
        Returns:
            Timestamp in milliseconds since epoch
        """
        try:
            # Example: 2/11/2026 21:03:23.2292
            dt_str, ms_str = timestamp_str.rsplit(".", 1)
            dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
            
            # Convert to timestamp and add milliseconds
            timestamp_ms = int(dt.timestamp() * 1000)
            timestamp_ms += int(ms_str[:3])  # First 3 digits of fractional seconds
            
            return timestamp_ms
            
        except Exception as e:
            print(f"[DeathParser] Error parsing timestamp: {e}")
            return 0
