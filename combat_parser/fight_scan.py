"""
One pass over a finished fight's combat log lines.

WoW flushes most of a fight to disk only after it ends, so deaths, combatants
and cooldown casts are read back from the log once the END arrives. A Mythic+
window can be hundreds of MB, so lines are streamed once and only what the
metadata needs is kept.

Names are resolved at the end: COMBATANT_INFO lines come first in a fight, but
player names only appear in the combat events after them.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from combat_parser.events import (
    local_player_in,
    log_timestamp_ms,
    parse_combatant_spec_id,
    parse_player_name_realm,
)
from tracked_spells import TRACKED_SPELLS

# Splitting further than the spell ID (field 9) is wasted work for every line.
_EVENT_MAX_SPLIT = 10
# COMBATANT_INFO needs fields up to the spec ID candidates (see events.py).
_COMBATANT_MAX_SPLIT = 27
_COMBATANT_MIN_FIELDS = 26


class LogClock:
    """Line timestamps to epoch ms. Lines arrive in bursts sharing a second,
    so strptime runs once per second of log rather than once per line."""

    def __init__(self):
        self._second = None
        self._second_ms = 0

    def line_ms(self, timestamp: str) -> Optional[int]:
        head, dot, frac = timestamp.strip().rpartition('.')
        if not dot:
            return None
        if head != self._second:
            whole = log_timestamp_ms(head + '.0')
            if whole is None:
                return None
            self._second, self._second_ms = head, whole
        try:
            return self._second_ms + int(frac[:3])
        except ValueError:
            return None


@dataclass
class FightScan:
    """What a fight's lines say about it. Combatant and cast names are
    already resolved against the whole fight's GUID->name map."""
    line_count: int = 0
    local_player: Optional[Tuple[str, str, str]] = None   # (guid, name, realm)
    names: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # guid -> (name, realm)
    combatants: List[dict] = field(default_factory=list)  # guid, name, realm, spec_id, team_id
    deaths: List[dict] = field(default_factory=list)      # name, timestamp_ms, spec_id
    casts: List[dict] = field(default_factory=list)       # metadata cooldownCasts entries


class _FightScanner:
    def __init__(self, start_ms: Optional[int], track_deaths: bool):
        self.start_ms = start_ms
        self.track_deaths = track_deaths
        self.clock = LogClock()
        self.scan = FightScan()
        self.guid_to_spec: Dict[str, int] = {}
        self.raw_combatants: List[Tuple[str, int, int]] = []   # guid, faction, spec
        self.raw_casts: List[Tuple[str, str, int, int]] = []   # guid, raw name, spell, offset

    def feed(self, line: str):
        self.scan.line_count += 1
        sep = line.find('  ')
        if sep < 0:
            return
        data = line[sep + 2:]
        # Its field layout differs ([2] is faction, not a name).
        if data.startswith('COMBATANT_INFO'):
            self._note_combatant(data)
            return
        parts = data.split(',', _EVENT_MAX_SPLIT)
        if len(parts) < 7:
            return
        self._note_names(parts)
        event = parts[0]
        if event == 'UNIT_DIED' and self.track_deaths:
            self._note_death(parts, line[:sep])
        elif event == 'SPELL_CAST_SUCCESS' and self.start_ms is not None:
            self._note_cast(parts, line[:sep])

    def _note_names(self, parts: List[str]):
        # Like WCR, the fight's own lines say who recorded it: the live
        # identity goes stale when the user swaps characters in one log.
        if self.scan.local_player is None:
            self.scan.local_player = local_player_in(parts)
        for guid_idx, name_idx in ((1, 2), (5, 6)):
            guid = parts[guid_idx].strip()
            if not guid.startswith('Player-') or guid in self.scan.names:
                continue
            raw_name = parts[name_idx].strip().strip('"')
            if raw_name and raw_name not in ('nil', 'Unknown'):
                self.scan.names[guid] = parse_player_name_realm(raw_name)

    def _note_combatant(self, data: str):
        parts = data.split(',', _COMBATANT_MAX_SPLIT)
        if len(parts) < _COMBATANT_MIN_FIELDS:
            return
        guid = parts[1].strip()
        if not guid.startswith('Player-'):
            return
        try:
            faction = int(parts[2].strip())
        except ValueError:
            return
        spec_id = parse_combatant_spec_id(parts)
        # Deaths later in the fight look the spec up here.
        if spec_id > 0:
            self.guid_to_spec[guid] = spec_id
        self.raw_combatants.append((guid, faction, spec_id))

    def _note_death(self, parts: List[str], timestamp: str):
        if len(parts) < 9:
            return
        guid = parts[5].strip()
        if not guid.startswith('Player-'):
            return
        line_ms = self.clock.line_ms(timestamp)
        if line_ms is None:
            return
        self.scan.deaths.append({
            'name': parts[6].strip().strip('"'),
            'timestamp_ms': line_ms,
            'spec_id': self.guid_to_spec.get(guid, 0),
        })

    def _note_cast(self, parts: List[str], timestamp: str):
        if len(parts) <= 9:
            return
        guid = parts[1].strip()
        if not guid.startswith('Player-'):
            return
        try:
            spell_id = int(parts[9].strip())
        except ValueError:
            return
        if spell_id not in TRACKED_SPELLS:
            return
        line_ms = self.clock.line_ms(timestamp)
        if line_ms is None or line_ms < self.start_ms:
            return  # before the pull: not on the video's timeline
        self.raw_casts.append((guid, parts[2].strip().strip('"'), spell_id, line_ms - self.start_ms))

    def finish(self) -> FightScan:
        names = self.scan.names
        for guid, faction, spec_id in self.raw_combatants:
            if guid in names:
                name, realm = names[guid]
                self.scan.combatants.append({
                    'guid': guid, 'name': name, 'realm': realm,
                    'spec_id': spec_id, 'team_id': faction,
                })
        for guid, raw_name, spell_id, offset_ms in self.raw_casts:
            caster = names.get(guid, (parse_player_name_realm(raw_name)[0],))[0]
            spell = TRACKED_SPELLS[spell_id]
            self.scan.casts.append({
                "spellId": spell_id,
                "name": spell["name"],
                "casterName": caster,
                "casterGuid": guid,
                "offsetMs": offset_ms,
                "class": spell["class"],
                "spec": spell.get("spec"),
                "category": spell["category"],
                "icon": spell["icon"],
            })
        return self.scan


def scan_fight(lines: Iterable[str], start_ms: Optional[int], track_deaths: bool) -> FightScan:
    """Scan a fight's lines in one pass.

    *start_ms* is the fight's START in epoch ms; cast offsets are relative to
    it and casts before it are dropped. None skips cast collection.
    """
    scanner = _FightScanner(start_ms, track_deaths)
    for line in lines:
        scanner.feed(line)
    return scanner.finish()
