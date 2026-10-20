"""
Cache of parsed recording JSON sidecars.

The recordings list, stats, local-POV lookup, retention and POV sync each walk
the whole recordings tree; without a cache every call re-parses every sidecar.
Entries are keyed by path and invalidated by (mtime, size), so edits on disk
(cooldown rescans, new recordings) are picked up without explicit hooks.
"""

import json
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

# Large lists none of the bulk readers use; endpoints that need them (the
# per-recording metadata view, cloud upload) read the file directly.
_HEAVY_KEYS = ('cooldownCasts', 'combatants')


class SidecarCache:
    def __init__(self):
        self._entries: Dict[Path, Tuple[Tuple[int, int], Optional[dict]]] = {}
        self._lock = threading.Lock()

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
