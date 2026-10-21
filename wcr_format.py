"""
Conversion from our recording sidecars to Warcraft Recorder's cloud format.

Our JSON sidecars are a superset of WCR's: they keep WCR's fields with WCR's
names and shapes, and add our own (cooldownCasts, videoStartDelayMs, ...).
WCR uploads the local metadata as-is and its API maps the keys to database
columns, so only WCR's own keys may be sent — anything we add stays local.

Mirrors `Metadata` / `CloudMetadata` in wow-recorder src/main/types.ts and
the payload built in src/main/VideoProcessQueue.ts.
"""

import json
from pathlib import Path
from typing import Any, Dict

# Every key of WCR's `Metadata` type except `level`, which the API rejects
# (renamed to keystoneLevel below, as WCR does for old videos).
WCR_METADATA_KEYS = frozenset({
    'category', 'parentCategory', 'parentVideoName', 'parentVideoOffset',
    'duration', 'start', 'clippedAt', 'result', 'flavour',
    'zoneID', 'zoneName', 'encounterID', 'difficultyID', 'difficulty',
    'player', 'teamMMR', 'deaths', 'upgradeLevel', 'mapID',
    'challengeModeTimeline', 'soloShuffleTimeline', 'keystoneLevel',
    'encounterName', 'protected', 'soloShuffleRoundsWon',
    'soloShuffleRoundsPlayed', 'combatants', 'overrun', 'affixes', 'tag',
    'delete', 'uniqueHash', 'bossPercent', 'appVersion', 'encoder', 'size',
})

CATEGORY_MYTHIC_PLUS = 'Mythic+'
CATEGORY_MANUAL = 'Manual'

# Category values we wrote before matching WCR's VideoCategory enum.
CATEGORY_ALIASES = {'MythicPlus': CATEGORY_MYTHIC_PLUS}

# WCR's M+ metadata names the run by mapID + keystoneLevel and carries no
# encounter fields; ours stored the dungeon as a pseudo-encounter.
MYTHIC_PLUS_EXCLUDED_KEYS = ('encounterID', 'encounterName', 'difficultyID', 'difficulty')

# Required `Metadata` fields and the value used when a sidecar lacks them.
REQUIRED_DEFAULTS = {
    'category': CATEGORY_MANUAL,
    'flavour': 'Retail',
    'duration': 0,
    'result': False,
    'combatants': [],
    'overrun': 0,
}


def normalize_category(category: Any) -> Any:
    """WCR's spelling of a category we may have stored under an old name."""
    return CATEGORY_ALIASES.get(category, category)


def to_wcr_metadata(sidecar: Dict[str, Any]) -> Dict[str, Any]:
    """WCR `Metadata` from one of our sidecars: WCR's keys only, WCR's values."""
    metadata = {k: v for k, v in sidecar.items() if k in WCR_METADATA_KEYS and v is not None}

    if 'keystoneLevel' not in metadata and sidecar.get('level') is not None:
        metadata['keystoneLevel'] = sidecar['level']

    for key, default in REQUIRED_DEFAULTS.items():
        metadata.setdefault(key, default)

    metadata['category'] = normalize_category(metadata['category'])
    if metadata['category'] == CATEGORY_MYTHIC_PLUS:
        for key in MYTHIC_PLUS_EXCLUDED_KEYS:
            metadata.pop(key, None)

    return metadata


def to_cloud_metadata(sidecar: Dict[str, Any], video_key: str,
                      file_size: int, fallback_start_ms: int) -> Dict[str, Any]:
    """WCR `CloudMetadata` (POST /guild/{g}/video body) for one video."""
    metadata = to_wcr_metadata(sidecar)
    video_name = Path(video_key).stem

    # Without a name WCR's list shows a blank row for manual recordings.
    if metadata['category'] == CATEGORY_MANUAL:
        metadata.setdefault('encounterName', video_name)

    metadata['start'] = metadata.get('start') or fallback_start_ms
    metadata['uniqueHash'] = metadata.get('uniqueHash') or ''
    metadata['size'] = file_size
    metadata['videoName'] = video_name
    metadata['videoKey'] = video_key
    return metadata


def read_sidecar(video_path: Path) -> Dict[str, Any]:
    """The video's full JSON sidecar, or {} when missing or unreadable."""
    try:
        data = json.loads(video_path.with_suffix('.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cloud_metadata_for_video(video_path: Path) -> Dict[str, Any]:
    """CloudMetadata for a video on disk, read from its sidecar right now."""
    stat = video_path.stat()
    return to_cloud_metadata(
        read_sidecar(video_path),
        video_key=video_path.name,
        file_size=stat.st_size,
        fallback_start_ms=int(stat.st_mtime * 1000),
    )
