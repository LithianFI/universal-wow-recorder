"""
Recording retention policy.

Deletes old recordings according to two rules applied in sequence:
  1. Age-based: files older than RETENTION_MAX_AGE_DAYS are deleted.
  2. Count-based: for each (category, difficulty, boss) group, keep only
     the RETENTION_MAX_PER_GROUP newest recordings; delete the rest.

Only videos with a JSON sidecar are considered: the sidecar marks a file as
made by this app, so other footage in the OBS folder (streams, other games,
a recording still in progress) is never deleted. Grouping uses the sidecar.

Clips are skipped entirely: a user exporting a 20-second highlight shouldn't
have it evicted by retention.
"""

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from constants import VIDEO_EXTENSIONS, LOG_PREFIXES
from sidecar_cache import sidecars
from wcr_format import normalize_category


# Clip files produced by the export endpoint are suffixed with "_clip_MMSS-MMSS"
# right before the extension. We exempt these from retention sweeps.
_CLIP_SUFFIX_RE = re.compile(r'_clip_\d+-\d+$')


def _is_clip(path: Path) -> bool:
    """Return True if *path* looks like an exported clip file."""
    return bool(_CLIP_SUFFIX_RE.search(path.stem))


def _load_sidecar(video_path: Path) -> Optional[dict]:
    """Return the recording's JSON sidecar, or None if it has no readable one."""
    return sidecars.read(video_path.with_suffix('.json'))


def _group_key(video_path: Path, meta: dict) -> Tuple[str, str, str, bool]:
    """Grouping key (category, difficulty, encounter, is_kill) from the sidecar."""
    return (
        str(normalize_category(meta.get('category', 'Unknown'))),
        str(meta.get('difficulty', meta.get('difficultyID', 'Unknown'))),
        str(meta.get('encounterName', video_path.stem)),
        bool(meta.get('result', False)),
    )


def _list_video_files(record_dir: Path) -> List[Path]:
    """Return all video files in *record_dir* and its immediate subfolders.

    We look one level deep to cover the organize_by_date layout where files
    live in YYYY-MM-DD subfolders. We don't recurse further.
    """
    files: List[Path] = []
    if not record_dir.exists():
        return files

    for entry in record_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(entry)
        elif entry.is_dir():
            for nested in entry.iterdir():
                if nested.is_file() and nested.suffix.lower() in VIDEO_EXTENSIONS:
                    files.append(nested)
    return files


@dataclass
class RetentionPlan:
    """Files a sweep would delete, per rule."""
    by_age: List[Path] = field(default_factory=list)
    by_count: List[Path] = field(default_factory=list)
    scanned: int = 0


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None  # can't stat: never delete it


def plan_retention(record_dir: Path, max_age_days: int = 0, max_per_group: int = 0) -> RetentionPlan:
    """Decide what a sweep would delete, without touching any file.

    Rule 1 deletes files older than *max_age_days*. Rule 2 then keeps the
    newest *max_per_group* of each (category, difficulty, boss, result) group
    among the remaining own recordings; downloaded POVs don't count towards it,
    so other players' videos can't push out your own pulls. Protected
    recordings (WCR's `protected` flag) are neither deleted nor counted.
    """
    plan = RetentionPlan()
    if (max_age_days <= 0 and max_per_group <= 0) or not record_dir or not record_dir.exists():
        return plan

    sidecars: Dict[Path, dict] = {}
    for f in _list_video_files(record_dir):
        meta = None if _is_clip(f) else _load_sidecar(f)
        if meta is not None and not meta.get('protected'):
            sidecars[f] = meta
    plan.scanned = len(sidecars)

    survivors = []
    cutoff = time.time() - max_age_days * 86400
    for f in sidecars:
        mtime = _mtime(f)
        if mtime is None:
            continue
        if max_age_days > 0 and mtime < cutoff:
            plan.by_age.append(f)
        else:
            survivors.append((f, mtime))

    if max_per_group > 0:
        groups: Dict[tuple, List[tuple]] = defaultdict(list)
        for f, mtime in survivors:
            if not sidecars[f].get('pov'):
                groups[_group_key(f, sidecars[f])].append((f, mtime))
        for members in groups.values():
            members.sort(key=lambda m: m[1], reverse=True)
            plan.by_count.extend(f for f, _ in members[max_per_group:])
    return plan


def apply_retention(
    record_dir: Path,
    file_manager,
    max_age_days: int = 0,
    max_per_group: int = 0,
) -> Dict[str, int]:
    """Apply retention rules to *record_dir* (see plan_retention).

    Args:
        record_dir: Directory to sweep.
        file_manager: RecordingFileManager (for delete_recording, which also
            handles companion JSON cleanup).
        max_age_days: Delete files older than this; 0 disables the rule.
        max_per_group: Keep at most N per group; 0 disables the rule.

    Returns:
        Dict with counts: {'deleted_by_age': int, 'deleted_by_count': int,
                           'scanned': int}.
    """
    plan = plan_retention(record_dir, max_age_days, max_per_group)
    result = {'deleted_by_age': 0, 'deleted_by_count': 0, 'scanned': plan.scanned}
    if not plan.by_age and not plan.by_count:
        if plan.scanned:
            print(f"{LOG_PREFIXES['FILE']} [Retention] Nothing to delete")
        return result

    print(f"{LOG_PREFIXES['FILE']} [Retention] Sweeping {plan.scanned} recordings in {record_dir}")
    for f in plan.by_age:
        if file_manager.delete_recording(f, reason=f"retention: older than {max_age_days}d"):
            result['deleted_by_age'] += 1
    for f in plan.by_count:
        if file_manager.delete_recording(f, reason=f"retention: more than {max_per_group} per boss"):
            result['deleted_by_count'] += 1
    print(f"{LOG_PREFIXES['FILE']} [Retention] Deleted {result['deleted_by_age']} by age, "
          f"{result['deleted_by_count']} by group limit")
    return result
