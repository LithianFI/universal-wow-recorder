"""
POV Sync Manager — fetches cloud video list and identifies recordings from other
players (POVs) that share a uniqueHash with a local recording.

Flow:
  1. Background thread periodically calls GET /guild/{g}/video to get the full
     guild video list.
  2. Local JSON sidecars are scanned to build a uniqueHash → local filenames map.
  3. Cloud videos whose uniqueHash matches a local recording but whose videoKey
     is not present locally are "available POVs".
  4. On demand (or automatically) those videos are streamed down from their
     pre-signed R2 URL to the recordings directory.
"""

import json
import threading
import time
import requests
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Callable, Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIFF_MAP: Dict[str, str] = {
    'mythic': 'mythic', 'm': 'mythic',
    'heroic': 'heroic', 'h': 'heroic',
    'normal': 'normal', 'n': 'normal',
    'lfr': 'lfr', 'l': 'lfr', 'looking for raid': 'lfr',
    'mythic+': 'mythic+', 'mythicplus': 'mythic+', '+': 'mythic+',
}


def _normalize_difficulty(d: str) -> str:
    """Normalise difficulty strings to a canonical lowercase form."""
    return _DIFF_MAP.get(str(d).lower().strip(), str(d).lower().strip())


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DownloadProgress:
    """Tracks download progress for a POV video."""
    video_key: str
    total_bytes: int
    downloaded_bytes: int
    status: str          # 'queued' | 'downloading' | 'completed' | 'failed'
    error: Optional[str] = None

    @property
    def progress_percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.downloaded_bytes / self.total_bytes) * 100


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PovSyncManager:
    """
    Manages discovery and download of other players' POV recordings from the
    WarcraftRecorder cloud.

    A "POV" is any cloud video that:
      - shares its uniqueHash with at least one locally-stored recording, AND
      - whose videoKey (filename) is not already present in the local recordings
        directory tree.

    Thread model:
      - _sync_thread  : wakes every 30 s, fires a full cloud sync when the
                        configured interval has elapsed.
      - _download_thread: persistent worker that drains _download_queue.
    """

    def __init__(self, wcr_cloud, config,
                 get_recording_dir_fn: Callable[[], Optional[Path]],
                 is_recording_fn: Optional[Callable[[], bool]] = None):
        """
        Args:
            wcr_cloud           : authenticated WarcraftRecorderCloud instance
            config              : ConfigManager instance
            get_recording_dir_fn: callable that returns the current Path to the
                                  recording directory (or None).
            is_recording_fn     : callable that returns True while a recording is
                                  active.  Downloads are paused during recording.
        """
        self._cloud = wcr_cloud
        self._config = config
        self._get_recording_dir = get_recording_dir_fn
        self._is_recording: Callable[[], bool] = is_recording_fn or (lambda: False)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._sync_thread: Optional[threading.Thread] = None
        self._download_thread: Optional[threading.Thread] = None

        # Sync state
        self._cloud_videos: List[dict] = []
        self._local_hashes: Dict[str, List[str]] = {}   # hash → [relative filenames]
        self._local_keys: Set[str] = set()              # bare filenames present locally
        self._local_meta: Dict[str, List[dict]] = {}    # hash → [{encounterName, difficulty, result, duration, start}, …]
        self._last_sync_time: float = 0.0
        self._sync_error: Optional[str] = None

        # Download queue
        self._download_queue: List[dict] = []
        self._active_download: Optional[str] = None
        self._download_work_event = threading.Event()
        self._progress_callbacks: List[Callable] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start background sync and download threads."""
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._stop_event.clear()
        self._sync_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name='pov-sync')
        self._download_thread = threading.Thread(
            target=self._download_loop, daemon=True, name='pov-download')
        self._sync_thread.start()
        self._download_thread.start()
        print("[PovSync] Started")

    def stop(self):
        """Signal threads to exit cleanly."""
        self._stop_event.set()
        self._download_work_event.set()
        print("[PovSync] Stopping…")

    def trigger_sync(self):
        """Force an immediate sync on the next loop tick."""
        self._last_sync_time = 0.0

    def add_progress_callback(self, cb: Callable[[DownloadProgress], None]):
        self._progress_callbacks.append(cb)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'last_sync': self._last_sync_time,
                'cloud_video_count': len(self._cloud_videos),
                'local_hash_count': len(self._local_hashes),
                'active_download': self._active_download,
                'queue_size': len(self._download_queue),
                'error': self._sync_error,
            }

    def get_available_povs(self) -> Dict[str, List[dict]]:
        """
        Return {uniqueHash: [cloud_video, …]} for each hash that:
          - matches at least one local recording, AND
          - the cloud video is from the same specific pull (duration + time window), AND
          - has not yet been downloaded locally.
        """
        with self._lock:
            result: Dict[str, List[dict]] = {}
            for video in self._cloud_videos:
                h = video.get('uniqueHash', '')
                key = video.get('videoKey', '')
                if not key or key in self._local_keys:
                    continue
                # Primary: exact uniqueHash match + pull-level filter
                if h and h in self._local_hashes:
                    if self._matches_any_local_pull(video, h):
                        result.setdefault(h, []).append(video)
                    continue
                # Fallback: metadata match for videos without a matching hash
                matched_hash = self._find_matching_local_hash(video)
                if matched_hash:
                    result.setdefault(matched_hash, []).append(video)
            return result

    def _matches_any_local_pull(self, cloud_video: dict, local_hash: str) -> bool:
        """
        Return True if this cloud video's start time is within 60 seconds of
        any local recording with the same hash.  Mirrors VideoCorrelator.ts in
        the official WCR client (same hash + areDatesWithinSeconds(d1, d2, 60)).
        """
        c_start = int(cloud_video.get('start') or 0)
        if not c_start:
            return True  # no start time — can't filter, include

        for meta in self._local_meta.get(local_hash, []):
            l_start = int(meta.get('start') or 0)
            if l_start and abs(c_start - l_start) <= 60 * 1000:
                return True
        return False

    def _find_matching_local_hash(self, cloud_video: dict) -> Optional[str]:
        """
        Fallback: return the local uniqueHash whose pulls best match this cloud
        video by encounter name + difficulty + result + duration + start time.
        Used when the cloud video carries no uniqueHash (e.g. other-client upload).
        """
        c_name = (cloud_video.get('encounterName') or '').lower().strip()
        c_diff = _normalize_difficulty(cloud_video.get('difficulty') or '')
        c_result = bool(cloud_video.get('result'))
        c_dur = int(cloud_video.get('duration') or 0)
        c_start = int(cloud_video.get('start') or 0)

        if not c_name:
            return None

        for h, pulls in self._local_meta.items():
            for meta in pulls:
                if (meta.get('encounterName') or '').lower().strip() != c_name:
                    continue
                if _normalize_difficulty(meta.get('difficulty') or '') != c_diff:
                    continue
                if bool(meta.get('result')) != c_result:
                    continue
                l_start = int(meta.get('start') or 0)
                if l_start and c_start and abs(l_start - c_start) > 60 * 1000:
                    continue
                return h
        return None

    def get_povs_for_hash(self, unique_hash: str) -> List[dict]:
        """Return available (not-yet-downloaded) cloud POVs for a specific hash."""
        return self.get_available_povs().get(unique_hash, [])

    def queue_download(self, video_key: str) -> bool:
        """
        Queue a cloud video for download by its videoKey.
        Returns True if queued (or already queued), False if the key is unknown.
        """
        with self._lock:
            target = next(
                (v for v in self._cloud_videos if v.get('videoKey') == video_key), None)
            if not target:
                print(f"[PovSync] Unknown video key: {video_key}")
                return False
            if video_key in self._local_keys:
                print(f"[PovSync] Already present locally: {video_key}")
                return False
            already_queued = any(
                v.get('videoKey') == video_key for v in self._download_queue)
            if already_queued:
                return True
            self._download_queue.append(target)

        self._download_work_event.set()
        print(f"[PovSync] Queued: {video_key}")
        return True

    def queue_all_for_hash(self, unique_hash: str) -> int:
        """Queue all available POVs for an encounter. Returns number queued."""
        count = 0
        for video in self.get_povs_for_hash(unique_hash):
            if self.queue_download(video.get('videoKey', '')):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Background sync loop
    # ------------------------------------------------------------------

    def _sync_loop(self):
        while not self._stop_event.is_set():
            interval_secs = self._config.POV_SYNC_INTERVAL * 60
            now = time.time()
            if now - self._last_sync_time >= interval_secs:
                try:
                    self._do_sync()
                except Exception as exc:
                    print(f"[PovSync] Sync error: {exc}")
                    with self._lock:
                        self._sync_error = str(exc)
                self._last_sync_time = time.time()
            self._stop_event.wait(30)

    def _do_sync(self):
        print("[PovSync] Running sync…")

        # 1. Build local index from JSON sidecars
        local_hashes: Dict[str, List[str]] = {}
        local_meta: Dict[str, List[dict]] = {}
        local_keys: Set[str] = set()
        recording_dir = self._get_recording_dir()
        ext = getattr(self._config, 'RECORDING_EXTENSION', '.mp4').lower()

        if recording_dir and recording_dir.exists():
            for json_path in recording_dir.rglob('*.json'):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    h = data.get('uniqueHash', '')
                    if h:
                        rel = str(json_path.with_suffix('').relative_to(recording_dir))
                        local_hashes.setdefault(h, []).append(rel)
                        local_meta.setdefault(h, []).append({
                            'encounterName': data.get('encounterName', ''),
                            'difficulty': data.get('difficulty', ''),
                            'result': data.get('result', False),
                            'duration': data.get('duration', 0),
                            'start': data.get('start', 0),
                        })
                except Exception:
                    continue
            for vf in recording_dir.rglob(f'*{ext}'):
                local_keys.add(vf.name)

        # 2. Fetch cloud video list
        g = requests.utils.quote(self._cloud.guild_name)
        url = f"{self._cloud.API_BASE}/guild/{g}/video"
        resp = requests.get(url, headers=self._cloud._headers, timeout=15)

        if resp.status_code == 401:
            print("[PovSync] 401 — credentials may have expired")
            return
        resp.raise_for_status()

        cloud_videos: List[dict] = resp.json()
        print(f"[PovSync] Fetched {len(cloud_videos)} cloud videos; "
              f"{len(local_hashes)} local hashes found")

        with self._lock:
            self._cloud_videos = cloud_videos
            self._local_hashes = local_hashes
            self._local_meta = local_meta
            self._local_keys = local_keys
            self._sync_error = None

        # 3. Auto-queue recent POVs if configured (only encounters < 24 h old)
        if self._config.POV_AUTO_DOWNLOAD:
            cutoff_ms = (time.time() - 86400) * 1000  # 24 h ago in epoch ms
            for videos in self.get_available_povs().values():
                for video in videos:
                    if video.get('start', 0) >= cutoff_ms:
                        self.queue_download(video.get('videoKey', ''))

    # ------------------------------------------------------------------
    # Download loop
    # ------------------------------------------------------------------

    def _wait_for_recording_idle(self) -> bool:
        """Block until no recording is active or stop is requested.

        Returns True if it's safe to proceed, False if stop was requested.
        """
        while self._is_recording():
            if self._stop_event.is_set():
                return False
            print("[PovSync] Recording in progress — download paused")
            self._stop_event.wait(5)
        return not self._stop_event.is_set()

    def _download_loop(self):
        while not self._stop_event.is_set():
            self._download_work_event.wait()
            self._download_work_event.clear()

            while True:
                with self._lock:
                    if not self._download_queue:
                        break
                    item = self._download_queue.pop(0)

                # Wait until any active recording finishes before downloading
                if not self._wait_for_recording_idle():
                    # Stop requested — put item back and exit
                    with self._lock:
                        self._download_queue.insert(0, item)
                    break

                key = item.get('videoKey', '')
                self._active_download = key
                try:
                    self._download_video(item)
                except Exception as exc:
                    print(f"[PovSync] Download failed for {key}: {exc}")
                    self._notify_progress(DownloadProgress(
                        video_key=key, total_bytes=0, downloaded_bytes=0,
                        status='failed', error=str(exc)))
                finally:
                    self._active_download = None

    def _download_video(self, cloud_video: dict):
        key = cloud_video.get('videoKey', '')
        if not key:
            return

        recording_dir = self._get_recording_dir()
        if not recording_dir:
            raise RuntimeError("No recording directory available for POV download")

        # Extract pre-signed download URL — first try fields in the video list object,
        # then fall back to fetching it from the individual video endpoint.
        signed_url = ''
        for field in ('signedVideoKey', 'videoUrl', 'signedUrl', 'url', 'downloadUrl'):
            v = cloud_video.get(field, '')
            if v and str(v).startswith('http'):
                signed_url = v
                break

        if not signed_url:
            fields = [k for k, v in cloud_video.items() if v]
            print(f"[PovSync] No URL in video object for {key} — fields present: {fields}")
            signed_url = self._cloud.get_signed_download_url(key) or ''

        if not signed_url:
            raise RuntimeError(f"Could not obtain a download URL for {key}")

        # Fetch file size for progress reporting (best-effort)
        total_bytes = 0
        try:
            g = requests.utils.quote(self._cloud.guild_name)
            enc_key = requests.utils.quote(key)
            size_resp = requests.get(
                f"{self._cloud.API_BASE}/guild/{g}/video/{enc_key}/size",
                headers=self._cloud._headers, timeout=10)
            if size_resp.status_code == 200:
                total_bytes = size_resp.json().get('bytes', 0)
        except Exception as exc:
            print(f"[PovSync] Could not get size for {key}: {exc}")

        progress = DownloadProgress(
            video_key=key, total_bytes=total_bytes,
            downloaded_bytes=0, status='downloading')
        self._notify_progress(progress)

        # Destination: configurable subdir (default: <recordings>/other_povs/)
        dl_dir = self._get_download_dir(recording_dir)
        dl_dir.mkdir(parents=True, exist_ok=True)
        dest = dl_dir / key

        if dest.exists():
            print(f"[PovSync] Already on disk: {dest}")
            with self._lock:
                self._local_keys.add(key)
            progress.status = 'completed'
            progress.downloaded_bytes = total_bytes
            self._notify_progress(progress)
            return

        print(f"[PovSync] Downloading {key} "
              f"({total_bytes / (1024**2):.1f} MB to {dl_dir})")

        tmp_path = dest.with_suffix(dest.suffix + '.tmp')
        try:
            with requests.get(signed_url, stream=True, timeout=600) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(tmp_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        # Pause mid-download if a recording starts
                        if self._is_recording():
                            if not self._wait_for_recording_idle():
                                break
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress.downloaded_bytes = downloaded
                        self._notify_progress(progress)
                        if self._stop_event.is_set():
                            break

            if self._stop_event.is_set():
                tmp_path.unlink(missing_ok=True)
                return

            tmp_path.rename(dest)
            with self._lock:
                self._local_keys.add(key)
            self._write_pov_sidecar(dest, cloud_video)
            progress.status = 'completed'
            progress.downloaded_bytes = total_bytes or downloaded
            self._notify_progress(progress)
            print(f"[PovSync] Saved: {dest}")

        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _get_download_dir(self, recording_dir: Path) -> Path:
        custom = getattr(self._config, 'POV_DOWNLOAD_DIR', '')
        if custom and str(custom).strip():
            return Path(custom)
        return recording_dir / 'other_povs'

    def _write_pov_sidecar(self, dest: Path, cloud_video: dict):
        """Write a JSON sidecar alongside a downloaded POV using cloud metadata."""
        sidecar = {
            'videoKey': cloud_video.get('videoKey', dest.name),
            'encounterName': cloud_video.get('encounterName', ''),
            'difficulty': cloud_video.get('difficulty', ''),
            'difficultyID': cloud_video.get('difficultyID'),
            'result': cloud_video.get('result'),
            'start': cloud_video.get('start', 0),
            'duration': cloud_video.get('duration', 0),
            'uniqueHash': cloud_video.get('uniqueHash', ''),
            'player': cloud_video.get('player'),
            'combatants': cloud_video.get('combatants', []),
            'zoneID': cloud_video.get('zoneID', 0),
            'zoneName': cloud_video.get('zoneName', ''),
            'category': cloud_video.get('category', ''),
            'flavour': cloud_video.get('flavour', 'Retail'),
            'pov': True,
        }
        try:
            sidecar_path = dest.with_suffix('.json')
            with open(sidecar_path, 'w', encoding='utf-8') as f:
                json.dump(sidecar, f, indent=2)
        except Exception as exc:
            print(f"[PovSync] Could not write sidecar for {dest.name}: {exc}")

    def _notify_progress(self, progress: DownloadProgress):
        for cb in self._progress_callbacks:
            try:
                cb(progress)
            except Exception as exc:
                print(f"[PovSync] Progress callback error: {exc}")
