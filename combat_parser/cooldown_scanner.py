"""
Cooldown cast scanner for WoW combat logs.

collect_cooldown_casts() — processes an already-collected list of log lines;
    used by the live parser after an encounter ends.

scan_log_for_cooldowns() — standalone retroactive scanner that accepts absolute
    ms timestamps from a recording's metadata JSON; used for past recordings
    where the log file is still available.
"""

import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow importing tracked_spells from the project root when this module is used
# as a standalone script (e.g. python -m combat_parser.cooldown_scanner).
sys.path.insert(0, str(Path(__file__).parent.parent))

from tracked_spells import TRACKED_SPELLS


def _parse_line_timestamp_ms(line: str) -> Optional[int]:
    """Parse the leading timestamp of a combat log line to ms since epoch.

    Expected format: "M/D/YYYY H:MM:SS.XXXX  EVENT,..."
    Returns None if the line has no parseable timestamp.
    """
    try:
        ts_str = line.split('  ', 1)[0].strip()
        if '/' not in ts_str:
            return None
        dt_str, frac = ts_str.rsplit('.', 1)
        dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
        return int(dt.timestamp() * 1000) + int(frac[:3])
    except Exception:
        return None


def collect_cooldown_casts(
    window_lines: list,
    encounter_start_ms: int,
    guid_to_name: dict,
) -> list:
    """Extract tracked SPELL_CAST_SUCCESS events from a pre-collected list of log lines.

    encounter_start_ms: absolute ms timestamp of the encounter start — cast
        offsets are computed relative to this so they map directly onto the
        video timeline (video t=0 == encounter start).
    guid_to_name: dict mapping Player GUID → (name, realm), built from the
        same window by the caller (already available in the parser scan).

    Returns a list of cast dicts ready to be stored in the metadata JSON.
    """
    casts = []
    for line in window_lines:
        if 'SPELL_CAST_SUCCESS' not in line:
            continue
        try:
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                continue
            parts = ts_split[1].split(',', 11)
            if len(parts) < 11:
                continue
            if not parts[0].strip() == 'SPELL_CAST_SUCCESS':
                continue

            src_guid = parts[1].strip()
            if not src_guid.startswith('Player-'):
                continue

            try:
                spell_id = int(parts[9].strip())
            except ValueError:
                continue

            spell_info = TRACKED_SPELLS.get(spell_id)
            if not spell_info:
                continue

            line_ms = _parse_line_timestamp_ms(line)
            if line_ms is None:
                continue
            offset_ms = max(0, line_ms - encounter_start_ms)

            name_realm = guid_to_name.get(src_guid)
            if name_realm:
                caster_name = name_realm[0]
            else:
                # Fallback: strip realm/region from raw name field
                caster_name = parts[2].strip().strip('"').split('-')[0]

            casts.append({
                "spellId": spell_id,
                "name": spell_info["name"],
                "casterName": caster_name,
                "casterGuid": src_guid,
                "offsetMs": offset_ms,
                "class": spell_info["class"],
                "spec": spell_info.get("spec"),
                "category": spell_info["category"],
                "icon": spell_info["icon"],
            })
        except Exception:
            continue
    return casts


def scan_log_for_cooldowns(
    log_path: Path,
    start_ms: int,
    duration_s: int,
    tolerance_s: int = 10,
) -> list:
    """Scan a combat log file for tracked cooldown casts in the given time window.

    Designed for retroactive use on recordings that were made before cooldown
    tracking was added, as long as the original log file is still available.

    start_ms:    encounter start time in ms since epoch  (metadata JSON "start")
    duration_s:  encounter duration in seconds           (metadata JSON "duration")
    tolerance_s: extra seconds to collect before/after the window so casts right
                 on the boundary are not missed

    Returns a list of cast dicts with offsetMs relative to start_ms, identical
    in shape to what the live scanner produces.
    """
    end_ms = start_ms + duration_s * 1000
    window_start_ms = start_ms - tolerance_s * 1000
    window_end_ms = end_ms + tolerance_s * 1000

    window_lines = []
    in_window = False

    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line_ms = _parse_line_timestamp_ms(line)
                if line_ms is None:
                    continue
                if not in_window:
                    if line_ms >= window_start_ms:
                        in_window = True
                    else:
                        continue
                window_lines.append(line)
                if line_ms > window_end_ms:
                    break
    except OSError as e:
        print(f"[CooldownScanner] Cannot read log file: {e}")
        return []

    if not window_lines:
        print(f"[CooldownScanner] No lines found in window — log may not cover this encounter")
        return []

    # Build guid→name from src/dest fields (same approach as the live parser scan)
    guid_to_name = {}
    for line in window_lines:
        try:
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                continue
            event_data = ts_split[1]
            if event_data.startswith('COMBATANT_INFO'):
                continue
            parts = event_data.split(',', 9)
            if len(parts) < 7:
                continue
            for guid_idx, name_idx in ((1, 2), (5, 6)):
                guid = parts[guid_idx].strip()
                if not guid.startswith('Player-'):
                    continue
                raw = parts[name_idx].strip().strip('"')
                if raw and raw not in ('nil', 'Unknown') and guid not in guid_to_name:
                    name = raw.split('-')[0]
                    guid_to_name[guid] = (name, '')
        except Exception:
            continue

    casts = collect_cooldown_casts(window_lines, start_ms, guid_to_name)
    print(f"[CooldownScanner] Found {len(casts)} tracked casts in {len(window_lines)} lines")
    return casts
