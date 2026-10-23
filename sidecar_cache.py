"""
Cache of parsed recording JSON sidecars.

The recordings list, stats, local-POV lookup, retention and POV sync each walk
the whole recordings tree; without a cache every call re-parses every sidecar.
Entries are keyed by path and invalidated by (mtime, size), so edits on disk
(cooldown rescans, new recordings) are picked up without explicit hooks.

The list of sidecar files is cached briefly too: selecting a recording looks
up its local POVs, which would otherwise walk the tree on every click.
write_sidecar drops it, so files this app writes show up at once.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# How long a directory listing is reused. Bounds how late files written by
# something else (a manual copy) appear.
_LISTING_TTL_SECONDS = 10.0

# Large lists none of the bulk readers use; endpoints that need them (the
# per-recording metadata view, cloud upload) read the file directly.
_HEAVY_KEYS = ('cooldownCasts', 'combatants')


class SidecarCache:
    def __init__(self):
        self._entries: Dict[Path, Tuple[Tuple[int, int], Optional[dict]]] = {}
        self._listings: Dict[Path, Tuple[float, List[Path]]] = {}
        self._lock = threading.Lock()

    def json_files(self, root: Path) -> List[Path]:
        """Every *.json under *root* (cached; may include just-deleted files,
        for which read() returns None)."""
        now = time.monotonic()
        with self._lock:
            cached = self._listings.get(root)
        if cached and now - cached[0] < _LISTING_TTL_SECONDS:
            return cached[1]
        files = list(root.rglob('*.json'))
        with self._lock:
            self._listings[root] = (now, files)
        return files

    def invalidate_listings(self):
        with self._lock:
            self._listings.clear()

    def read(self, json_path: Path) -> Optional[dict]:
        """Parsed sidecar without the heavy keys, or None if missing/invalid.

        The returned dict is shared between callers and must not be mutated.
        """
        try:
            stat = json_path.stat()
        except OSError:
            with self._lock:
                self._entries.pop(json_path, None)
            return None

        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._entries.get(json_path)
        if cached and cached[0] == stamp:
            return cached[1]

        data = self._parse(json_path)
        with self._lock:
            self._entries[json_path] = (stamp, data)
        return data

    @staticmethod
    def _parse(json_path: Path) -> Optional[dict]:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        for key in _HEAVY_KEYS:
            data.pop(key, None)
        return data


sidecars = SidecarCache()


def write_sidecar(json_path: Path, data: dict):
    """Replace a sidecar atomically so a crash can't leave half a file.

    A truncated sidecar hides its recording from the list and reads as
    unprotected, so every sidecar write goes through here.
    """
    tmp = json_path.with_name(json_path.name + '.tmp')
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, json_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    sidecars.invalidate_listings()
