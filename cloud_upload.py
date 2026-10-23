"""
Cloud Upload Module for WoW Raid Recorder.
Provides automated upload to cloud storage services after recording completion.
"""

import os
import re
import json
import time
import base64
import asyncio
import threading
import dataclasses
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from datetime import datetime

import requests

import wcr_format


# Retries for one HTTP step (a part PUT, the metadata POST, a Drive chunk).
# Backoff doubles per attempt: 1, 2, 4, 8 s ... capped.
HTTP_STEP_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 30


class _FilePart:
    """Read-only view of *length* bytes of an open file from *offset*.

    Lets requests stream a multipart chunk from disk instead of holding it in
    memory (100 MB per part, kept alive across its retries). Each attempt
    re-reads from the start because requests calls read() until empty.
    """

    def __init__(self, f, offset: int, length: int):
        self._f = f
        self._offset = offset
        self._length = length
        self._read = 0
        f.seek(offset)

    def __len__(self) -> int:
        return self._length

    def read(self, size: int = -1) -> bytes:
        remaining = self._length - self._read
        if remaining <= 0:
            return b''
        if size is None or size < 0 or size > remaining:
            size = remaining
        data = self._f.read(size)
        self._read += len(data)
        return data


def _backoff(attempt: int):
    time.sleep(min(2 ** (attempt - 1), MAX_BACKOFF_SECONDS))


# Full URLs, and the bare paths urllib3 reports as "url: /path?query".
_URL_QUERY_RE = re.compile(r'((?:https?://|url: )[^\s?\'"]+)\?[^\s\'"]*')


def redact_urls(error) -> str:
    """Error text with URL query strings removed.

    requests puts the full URL in its messages; for R2 signed URLs and Drive
    upload sessions the query string is a bearer credential, and these
    messages are logged and shown in the UI.
    """
    return _URL_QUERY_RE.sub(r'\1?<redacted>', str(error))


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class UploadProgress:
    """Tracks upload progress."""
    video_name: str
    total_bytes: int
    uploaded_bytes: int
    status: str  # 'queued', 'uploading', 'completed', 'failed'
    start_time: float
    error: Optional[str] = None

    @property
    def progress_percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.uploaded_bytes / self.total_bytes) * 100

    @property
    def upload_speed(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.uploaded_bytes / elapsed


@dataclass
class VideoMetadata:
    """Identifies a queued video. The WCR metadata itself is read from the
    sidecar at upload time (see wcr_format), as WCR does, so edits made while
    the video waits in the queue are sent and our own fields never are."""
    video_name: str       # stem without extension
    video_key: str        # filename with extension, exactly as stored in R2
    file_path: str
    file_size: int

    @classmethod
    def for_file(cls, file_path: Path) -> 'VideoMetadata':
        return cls(
            video_name=file_path.stem,
            video_key=file_path.name,
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
        )

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> 'VideoMetadata':
        """Load a persisted queue entry; older entries carried every metadata field."""
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

# =============================================================================
# Abstract Base
# =============================================================================

class CloudUploadProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> bool:
        pass

    @abstractmethod
    async def upload_video(self, file_path: Path, metadata: VideoMetadata,
                           progress_callback=None) -> bool:
        pass

    @abstractmethod
    def is_authenticated(self) -> bool:
        pass

    @abstractmethod
    def get_storage_info(self) -> Dict[str, Any]:
        pass


# =============================================================================
# Warcraft Recorder Cloud Provider
# =============================================================================

class WarcraftRecorderCloud(CloudUploadProvider):
    """
    Upload provider matching the WCR CloudClient.ts implementation exactly.

    Upload flow (single part, file < 100 MB):
      1. POST /guild/{g}/upload          {key, bytes}  → {signed: <put_url>}
      2. PUT  <signed_url>               raw file bytes
      3. POST /guild/{g}/video           CloudMetadata payload

    Upload flow (multipart, file >= 100 MB):
      1. POST /guild/{g}/create-multipart-upload   {key, total, part}  → {urls: [...]}
      2. PUT  urls[0..n]                            file chunks
      3. POST /guild/{g}/complete-multipart-upload  {key, etags: [...]}
      4. POST /guild/{g}/video                      CloudMetadata payload

    Authentication:
      GET /user/affiliations  → list of {guildName, read, write, del}
    """

    API_BASE = 'https://api.warcraftrecorder.com/api'
    MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100 MB

    def __init__(self, username: str, password: str, guild_name: str):
        self.username = username
        self.password = password
        self.guild_name = guild_name

        self.authenticated = False
        self.can_write = False
        self.can_read = False
        self.can_delete = False
        self.usage_bytes = 0
        self.limit_bytes = 0
        # video_key -> size of files already in storage whose metadata POST
        # failed: a retry only re-posts the metadata, not gigabytes of video.
        self._stored_files: Dict[str, int] = {}

        raw = f"{username}:{password}".encode('utf-8')
        self.auth_header = f"Basic {base64.b64encode(raw).decode('utf-8')}"
        self._headers = {'Authorization': self.auth_header}

        print(f"[WCR Cloud] Initialized for guild: {guild_name}")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """
        Authenticate by fetching /user/affiliations and checking guild membership.
        Mirrors CloudClient.configure() / fetchAffiliations().
        """
        self.authenticated = False
        try:
            url = f"{self.API_BASE}/user/affiliations"
            resp = requests.get(url, headers=self._headers, timeout=10)

            if resp.status_code == 401:
                print("[WCR Cloud] ❌ Invalid credentials (401)")
                return False

            resp.raise_for_status()
            affiliations = resp.json()  # [{guildName, read, write, del}, ...]

            affiliation = next(
                (a for a in affiliations if a.get('guildName') == self.guild_name),
                None
            )

            if not affiliation:
                print(f"[WCR Cloud] ❌ Not affiliated with guild: {self.guild_name}")
                return False

            self.can_read = affiliation.get('read', False)
            self.can_write = affiliation.get('write', False)
            self.can_delete = affiliation.get('del', False)

            if not self.can_write:
                print(f"[WCR Cloud] ❌ No write permission for guild: {self.guild_name}")
                return False

            self.authenticated = True
            print(f"[WCR Cloud] ✅ Authenticated. write={self.can_write}")

            # Fetch storage info (non-fatal if it fails)
            try:
                self._fetch_storage_info()
            except Exception as e:
                print(f"[WCR Cloud] Warning: could not fetch storage info: {redact_urls(e)}")

            return True

        except Exception as e:
            print(f"[WCR Cloud] ❌ Authentication error: {redact_urls(e)}")
            return False

    def _fetch_storage_info(self):
        g = requests.utils.quote(self.guild_name)
        usage = requests.get(f"{self.API_BASE}/guild/{g}/usage",
                             headers=self._headers, timeout=10)
        if usage.status_code == 200:
            self.usage_bytes = usage.json().get('bytes', 0)

        limit = requests.get(f"{self.API_BASE}/guild/{g}/limit",
                             headers=self._headers, timeout=10)
        if limit.status_code == 200:
            self.limit_bytes = limit.json().get('bytes', 0)

        print(f"[WCR Cloud] Storage: "
              f"{self.usage_bytes / (1024**3):.2f} GB / "
              f"{self.limit_bytes / (1024**3):.2f} GB")

    def is_authenticated(self) -> bool:
        return self.authenticated and self.can_write

    def get_storage_info(self) -> Dict[str, Any]:
        return {
            'usage_bytes': self.usage_bytes,
            'limit_bytes': self.limit_bytes,
            'usage_gb': round(self.usage_bytes / (1024**3), 2),
            'limit_gb': round(self.limit_bytes / (1024**3), 2),
            'usage_percent': round(self.usage_bytes / self.limit_bytes * 100, 2)
                             if self.limit_bytes else 0,
            'can_write': self.can_write,
            'can_delete': self.can_delete,
        }

    # ------------------------------------------------------------------
    # Upload entry point
    # ------------------------------------------------------------------

    async def upload_video(self, file_path: Path, metadata: VideoMetadata,
                           progress_callback=None) -> bool:
        if not self.is_authenticated():
            print("[WCR Cloud] Not authenticated")
            return False

        if not file_path.exists():
            print(f"[WCR Cloud] File not found: {file_path}")
            return False

        file_size = file_path.stat().st_size
        progress = UploadProgress(
            video_name=metadata.video_key,
            total_bytes=file_size,
            uploaded_bytes=0,
            status='uploading',
            start_time=time.time(),
        )

        try:
            print(f"[WCR Cloud] Uploading {metadata.video_key} "
                  f"({file_size / (1024**2):.1f} MB)")

            if self._stored_files.get(metadata.video_key) == file_size:
                print(f"[WCR Cloud] {metadata.video_key} already stored, retrying metadata only")
                success = True
            elif file_size >= self.MULTIPART_THRESHOLD:
                success = await self._multipart_upload(
                    file_path, metadata, progress, progress_callback)
            else:
                success = await self._single_upload(
                    file_path, metadata, progress, progress_callback)

            if success:
                self._stored_files[metadata.video_key] = file_size
                # Step 3 (both paths): register metadata in WCR database.
                # Without it the file uses guild storage but nobody can see it.
                success = await self._post_video_metadata(metadata)
                if success:
                    self._stored_files.pop(metadata.video_key, None)

            if success:
                progress.status = 'completed'
                progress.uploaded_bytes = file_size
                if progress_callback:
                    progress_callback(progress)
                print(f"[WCR Cloud] ✅ Upload complete: {metadata.video_key}")
            else:
                progress.status = 'failed'
                if progress_callback:
                    progress_callback(progress)

            return success

        except Exception as e:
            print(f"[WCR Cloud] ❌ Upload error: {redact_urls(e)}")
            progress.status = 'failed'
            progress.error = redact_urls(e)
            if progress_callback:
                progress_callback(progress)
            return False

    # ------------------------------------------------------------------
    # Single-part upload
    # ------------------------------------------------------------------

    async def _single_upload(self, file_path: Path, metadata: VideoMetadata,
                              progress: UploadProgress, progress_callback) -> bool:
        """
        Step 1: POST /guild/{g}/upload  {key, bytes}  → {signed: url}
        Step 2: PUT signed_url  <file bytes>
        """
        try:
            key = metadata.video_key
            file_size = file_path.stat().st_size
            g = requests.utils.quote(self.guild_name)

            # Step 1 – get signed PUT URL
            sign_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'bytes': file_size},
                timeout=10,
            )

            if sign_resp.status_code != 200:
                print(f"[WCR Cloud] Failed to get signed URL: {sign_resp.status_code} {sign_resp.text}")
                return False

            signed_url = sign_resp.json().get('signed')
            if not signed_url:
                print("[WCR Cloud] No signed URL in response")
                return False

            # Step 2 – PUT file to signed URL, retry up to 5 times on failure
            content_type = 'video/mp4' if key.endswith('.mp4') else 'application/octet-stream'
            attempts = 0
            success = False

            while not success and attempts < HTTP_STEP_ATTEMPTS:
                attempts += 1
                if attempts > 1:
                    _backoff(attempts - 1)
                try:
                    with open(file_path, 'rb') as f:
                        put_resp = requests.put(
                            signed_url,
                            data=f,
                            headers={
                                'Content-Type': content_type,
                                'Content-Length': str(file_size),
                            },
                            timeout=600,
                        )
                    if put_resp.status_code in (200, 204):
                        success = True
                    else:
                        print(f"[WCR Cloud] PUT attempt {attempts}/5 failed: "
                              f"{put_resp.status_code}")
                except Exception as e:
                    print(f"[WCR Cloud] PUT attempt {attempts}/5 error: {redact_urls(e)}")

            if not success:
                print(f"[WCR Cloud] Single part upload failed after 5 attempts: {key}")
                return False

            progress.uploaded_bytes = file_size
            if progress_callback:
                progress_callback(progress)

            return True

        except Exception as e:
            print(f"[WCR Cloud] Single upload error: {redact_urls(e)}")
            return False

    # ------------------------------------------------------------------
    # Multipart upload
    # ------------------------------------------------------------------

    async def _multipart_upload(self, file_path: Path, metadata: VideoMetadata,
                                 progress: UploadProgress, progress_callback) -> bool:
        """
        Step 1: POST /guild/{g}/create-multipart-upload  {key, total, part}  → {urls:[...]}
        Step 2: PUT each url with its chunk, collect etags
        Step 3: POST /guild/{g}/complete-multipart-upload  {key, etags:[...]}
        """
        try:
            key = metadata.video_key
            file_size = file_path.stat().st_size
            g = requests.utils.quote(self.guild_name)
            part_size = self.MULTIPART_THRESHOLD

            # Step 1 – create multipart upload, get signed part URLs
            create_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/create-multipart-upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'total': file_size, 'part': part_size},
                timeout=10,
            )

            if create_resp.status_code != 200:
                print(f"[WCR Cloud] Failed to create multipart upload: "
                      f"{create_resp.status_code} {create_resp.text}")
                return False

            urls: List[str] = create_resp.json().get('urls', [])
            expected_parts = -(-file_size // part_size)  # ceil
            if len(urls) != expected_parts:
                # Completing with parts missing would store a truncated video.
                print(f"[WCR Cloud] Expected {expected_parts} part URLs, got {len(urls)}")
                return False

            print(f"[WCR Cloud] Multipart upload: {len(urls)} parts")

            # Step 2 – upload each part
            etags: List[str] = []
            content_type = 'video/mp4' if key.endswith('.mp4') else 'application/octet-stream'
            offset = 0

            with open(file_path, 'rb') as f:
                for part_num, url in enumerate(urls, start=1):
                    chunk_size = min(part_size, file_size - offset)
                    if chunk_size <= 0:
                        break

                    # Retry each part up to 5 times — on failure we only need to
                    # re-send this part, not restart the whole upload.
                    attempts = 0
                    part_success = False
                    put_resp = None

                    while not part_success and attempts < HTTP_STEP_ATTEMPTS:
                        attempts += 1
                        if attempts > 1:
                            _backoff(attempts - 1)
                        try:
                            put_resp = requests.put(
                                url,
                                data=_FilePart(f, offset, chunk_size),
                                headers={
                                    'Content-Type': content_type,
                                    'Content-Length': str(chunk_size),
                                },
                                timeout=600,
                            )
                            if put_resp.status_code in (200, 204):
                                part_success = True
                            else:
                                print(f"[WCR Cloud] Part {part_num} attempt {attempts}/5 "
                                      f"failed: {put_resp.status_code}")
                        except Exception as e:
                            print(f"[WCR Cloud] Part {part_num} attempt {attempts}/5 "
                                  f"error: {redact_urls(e)}")

                    if not part_success or put_resp is None:
                        print(f"[WCR Cloud] Part {part_num} failed after 5 attempts: {key}")
                        return False

                    # Strip quotes from etag (axios does this too per WCR source)
                    etag = put_resp.headers.get('ETag', '').replace('"', '')
                    etags.append(etag)

                    offset += chunk_size
                    progress.uploaded_bytes = offset
                    if progress_callback:
                        progress_callback(progress)

            if offset != file_size:
                print(f"[WCR Cloud] File changed during upload ({offset} of {file_size} bytes sent)")
                return False

            # Step 3 – complete multipart upload
            complete_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/complete-multipart-upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'etags': etags},  # flat string list, no part numbers
                timeout=30,
            )

            if complete_resp.status_code not in (200, 204):
                print(f"[WCR Cloud] Failed to complete multipart: "
                      f"{complete_resp.status_code} {complete_resp.text}")
                return False

            print("[WCR Cloud] Multipart upload complete")
            return True

        except Exception as e:
            print(f"[WCR Cloud] Multipart upload error: {redact_urls(e)}")
            return False

    # ------------------------------------------------------------------
    # Post metadata to WCR database (always last step after file upload)
    # ------------------------------------------------------------------

    async def _post_video_metadata(self, metadata: VideoMetadata) -> bool:
        """POST /guild/{g}/video with CloudMetadata payload. True if registered."""
        g = requests.utils.quote(self.guild_name)
        payload = wcr_format.cloud_metadata_for_video(Path(metadata.file_path))

        for attempt in range(1, HTTP_STEP_ATTEMPTS + 1):
            if attempt > 1:
                _backoff(attempt - 1)
            try:
                resp = requests.post(
                    f"{self.API_BASE}/guild/{g}/video",
                    headers={**self._headers, 'Content-Type': 'application/json'},
                    json=payload,
                    timeout=15,
                )
            except requests.RequestException as e:
                print(f"[WCR Cloud] Error posting metadata (attempt {attempt}): {redact_urls(e)}")
                continue

            if resp.status_code == 401:
                self._note_rejected_credentials()
                return False

            if resp.status_code in (200, 201, 204):
                print(f"[WCR Cloud] ✅ Metadata registered: {metadata.video_key}")
                self._run_housekeeping(g)
                return True

            print(f"[WCR Cloud] Failed to post metadata (attempt {attempt}): "
                  f"{resp.status_code} {resp.text[:200]}")
            if resp.status_code < 500 and resp.status_code != 429:
                return False  # the payload itself was rejected; retrying won't help

        return False

    def get_signed_download_url(self, video_key: str) -> Optional[str]:
        """
        Fetch a presigned download URL for a single cloud video key.
        Tries two endpoint patterns: GET /video/{key} then POST /download {key}.
        """
        g = requests.utils.quote(self.guild_name)
        url_fields = ('signedVideoKey', 'videoUrl', 'signedUrl', 'url', 'downloadUrl', 'signed')

        # Pattern 1: GET /guild/{g}/video/{key}
        try:
            enc_key = requests.utils.quote(video_key, safe='')
            resp = requests.get(
                f"{self.API_BASE}/guild/{g}/video/{enc_key}",
                headers=self._headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for field in url_fields:
                    v = data.get(field, '')
                    if v and str(v).startswith('http'):
                        return v
                print(f"[WCR Cloud] GET /video/{{key}} 200 but no URL field — "
                      f"keys present: {list(data.keys())}")
            else:
                print(f"[WCR Cloud] GET /video/{{key}} → HTTP {resp.status_code}: "
                      f"{resp.text[:120]}")
        except Exception as exc:
            print(f"[WCR Cloud] GET /video/{{key}} error: {redact_urls(exc)}")

        # Pattern 2: POST /guild/{g}/download {key: video_key}  (mirror of /upload)
        try:
            resp = requests.post(
                f"{self.API_BASE}/guild/{g}/download",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': video_key},
                timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for field in url_fields:
                    v = data.get(field, '')
                    if v and str(v).startswith('http'):
                        return v
                print(f"[WCR Cloud] POST /download 200 but no URL field — "
                      f"keys present: {list(data.keys())}")
            else:
                print(f"[WCR Cloud] POST /download → HTTP {resp.status_code}: "
                      f"{resp.text[:120]}")
        except Exception as exc:
            print(f"[WCR Cloud] POST /download error: {redact_urls(exc)}")

        return None

    async def protect_videos(self, video_names: List[str], protect: bool) -> bool:
        """POST /guild/{g}/bulk/protect. Protected videos survive the housekeeper."""
        g = requests.utils.quote(self.guild_name)
        try:
            resp = requests.post(
                f"{self.API_BASE}/guild/{g}/bulk/protect",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'videos': video_names, 'protect': protect},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"[WCR Cloud] Error setting protection: {redact_urls(e)}")
            return False
        if resp.status_code == 401:
            self._note_rejected_credentials()
            return False
        if resp.status_code in (200, 201, 204):
            print(f"[WCR Cloud] {'Protected' if protect else 'Unprotected'}: {', '.join(video_names)}")
            return True
        print(f"[WCR Cloud] Setting protection failed: {resp.status_code} {resp.text[:200]}")
        return False

    def _note_rejected_credentials(self):
        """A 401 means the username/password no longer work.

        Auth is static HTTP Basic, so logging in again can't help: stop
        uploading until the user saves new credentials (which rebuilds the
        manager).
        """
        print("[WCR Cloud] ❌ Credentials rejected (401); update them in Config")
        self.authenticated = False

    def _run_housekeeping(self, encoded_guild: str):
        """POST /guild/{g}/housekeeper — fire-and-forget after each upload.

        Asks the server to evict old unprotected recordings to free space for
        the next upload, matching the behaviour in WCR CloudClient.postVideo().
        """
        try:
            resp = requests.post(
                f"{self.API_BASE}/guild/{encoded_guild}/housekeeper",
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code in (200, 201, 204):
                print(f"[WCR Cloud] Housekeeping OK: {resp.text[:120]}")
            else:
                print(f"[WCR Cloud] Housekeeping returned {resp.status_code}")
        except Exception as e:
            print(f"[WCR Cloud] Housekeeping error (non-fatal): {redact_urls(e)}")


# =============================================================================
# Upload Queue — persistent worker, never exits until stop() is called
# =============================================================================

class CloudUploadQueue:
    """
    Upload queue with a single worker thread. Pending (including the one in
    progress) and failed items are saved to *state_file*, so uploads survive
    a restart and failed ones can be retried.
    """

    MAX_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 5
    MAX_REMEMBERED_FAILURES = 50

    def __init__(self, provider: CloudUploadProvider, provider_name: str = '',
                 state_file: Optional[Path] = None,
                 on_uploaded: Optional[Callable[[Path], None]] = None):
        self.provider = provider
        self.provider_name = provider_name
        self._state_file = state_file
        self._on_uploaded = on_uploaded
        self._queue: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._work_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active: Optional[Dict] = None
        self.completed: List[str] = []
        self.failed: List[Dict] = []
        self._progress_callbacks: List[Callable] = []
        self._load_state()
        print(f"[Upload Queue] Initialized ({type(provider).__name__})")

    # ── persistence ──────────────────────────────────────────────────
    def _item_to_json(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'file_path': str(item['file_path']),
            'metadata': dataclasses.asdict(item['metadata']),
            'added_at': item['added_at'],
            'attempts': item['attempts'],
            'provider': self.provider_name,
        }

    def _item_from_json(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if data.get('provider') != self.provider_name:
            return None  # queued for a provider that is no longer configured
        path = Path(data['file_path'])
        if not path.exists():
            return None
        return {
            'file_path': path,
            'metadata': VideoMetadata.from_json(data['metadata']),
            'added_at': data.get('added_at', time.time()),
            'attempts': data.get('attempts', 0),
        }

    def _load_state(self):
        if not self._state_file or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding='utf-8'))
            for key, target in (('pending', self._queue), ('failed', self.failed)):
                for raw in data.get(key, []):
                    item = self._item_from_json(raw)
                    if item:
                        target.append(item)
        except (OSError, ValueError, TypeError, KeyError) as e:
            print(f"[Upload Queue] ⚠️ Could not load saved queue ({e}); starting empty")
            return
        if self._queue:
            print(f"[Upload Queue] Resuming {len(self._queue)} pending upload(s) from last session")

    def _save_state(self):
        """Write pending/failed items. Call with self._lock held."""
        if not self._state_file:
            return
        pending = ([self._active] if self._active else []) + self._queue
        data = {
            'pending': [self._item_to_json(i) for i in pending],
            'failed': [self._item_to_json(i) for i in self.failed[-self.MAX_REMEMBERED_FAILURES:]],
        }
        tmp = self._state_file.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(data, indent=1), encoding='utf-8')
            tmp.replace(self._state_file)
        except OSError as e:
            print(f"[Upload Queue] ⚠️ Could not save queue: {redact_urls(e)}")

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self):
        """Start the persistent worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        if self._queue:
            self._work_event.set()
        print("[Upload Queue] Worker started")

    def stop(self):
        """Signal the worker to stop after the current upload finishes."""
        self._stop_event.set()
        self._work_event.set()  # wake it up so it can exit

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for a stopped worker to exit; True once it has."""
        if self._thread:
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())

    def is_busy(self) -> bool:
        """Whether an upload is in progress."""
        return self._active is not None

    # ── queue operations ─────────────────────────────────────────────
    def _is_queued(self, file_path: Path) -> bool:
        active = [self._active] if self._active else []
        return any(i['file_path'] == file_path for i in active + self._queue)

    def is_pending(self, file_path: Path) -> bool:
        """Whether *file_path* is uploading or waiting to upload."""
        target = file_path.resolve()
        with self._lock:
            active = [self._active] if self._active else []
            return any(Path(i['file_path']).resolve() == target for i in active + self._queue)

    def add_to_queue(self, file_path: Path, metadata: VideoMetadata) -> bool:
        if not file_path.exists():
            print(f"[Upload Queue] ❌ File not found: {file_path}")
            return False

        item = {
            'file_path': file_path,
            'metadata': metadata,
            'added_at': time.time(),
            'attempts': 0,
        }

        with self._lock:
            if self._is_queued(file_path):
                print(f"[Upload Queue] Already queued: {metadata.video_key}")
                return True
            self._queue.append(item)
            self._save_state()
            size = len(self._queue)

        print(f"[Upload Queue] Queued: {metadata.video_key} (queue size: {size})")

        # Wake the worker thread
        self._work_event.set()
        return True

    def retry_failed(self) -> int:
        """Move failed uploads whose files still exist back into the queue."""
        with self._lock:
            retry = [i for i in self.failed if i['file_path'].exists()
                     and not self._is_queued(i['file_path'])]
            for item in retry:
                item['attempts'] = 0
            self._queue.extend(retry)
            self.failed = []
            self._save_state()
        if retry:
            self._work_event.set()
        return len(retry)

    def add_progress_callback(self, cb: Callable):
        self._progress_callbacks.append(cb)

    def _on_progress(self, progress: UploadProgress):
        for cb in self._progress_callbacks:
            try:
                cb(progress)
            except Exception as e:
                print(f"[Upload Queue] Progress callback error: {redact_urls(e)}")

    # ── worker ───────────────────────────────────────────────────────
    def _worker(self):
        """Persistent worker loop — blocks when idle, wakes on new items."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop_event.is_set():
            # Block until there's something to do
            self._work_event.wait()
            self._work_event.clear()

            while not self._stop_event.is_set():
                with self._lock:
                    if not self._queue:
                        break
                    item = self._queue.pop(0)
                    item['attempts'] += 1
                    self._active = item
                    self._save_state()
                self._upload_item(loop, item)

        loop.close()
        print("[Upload Queue] Worker stopped")

    def _upload_item(self, loop, item: Dict[str, Any]):
        meta = item['metadata']
        print(f"[Upload Queue] Processing: {meta.video_key} (attempt {item['attempts']})")
        try:
            success = loop.run_until_complete(
                self.provider.upload_video(
                    item['file_path'], meta,
                    progress_callback=self._on_progress,
                )
            )
        except Exception as e:
            print(f"[Upload Queue] Exception during upload: {redact_urls(e)}")
            success = False

        should_retry = not success and item['attempts'] < self.MAX_ATTEMPTS
        with self._lock:
            self._active = None
            if success:
                self.completed.append(meta.video_key)
            elif should_retry:
                self._queue.insert(0, item)
            else:
                self.failed.append(item)
            self._save_state()

        if success:
            print(f"[Upload Queue] ✅ Completed: {meta.video_key}")
            if self._on_uploaded:
                try:
                    self._on_uploaded(item['file_path'])
                except Exception as e:
                    print(f"[Upload Queue] Post-upload hook failed: {redact_urls(e)}")
        elif should_retry:
            print(f"[Upload Queue] ⚠️ Retry {item['attempts']}/{self.MAX_ATTEMPTS}: {meta.video_key}")
            time.sleep(self.RETRY_DELAY_SECONDS)
        else:
            print(f"[Upload Queue] ❌ Failed after {self.MAX_ATTEMPTS} attempts: {meta.video_key}")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            q_size = len(self._queue)
            active = self._active['metadata'].video_key if self._active else None
        return {
            'is_running': self._thread.is_alive() if self._thread else False,
            'queue_size': q_size,
            'active_upload': active,
            'completed_count': len(self.completed),
            'failed_count': len(self.failed),
        }


# =============================================================================
# Google Drive Upload Provider
# =============================================================================

class GoogleDriveUpload(CloudUploadProvider):
    """
    Upload provider for Google Drive using OAuth2 (installed-app flow).

    Authentication:
      On first run, opens the user's browser for one-time OAuth2 consent.
      The token is cached at ~/.wow_raid_recorder_gdrive_token.json so all
      subsequent launches authenticate silently.

    Upload flow (Drive resumable upload — handles large video files):
      1. POST https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable
         → response Location header contains the session URI.
      2. PUT session URI in CHUNK_SIZE chunks, reporting progress after each.

    Dependencies (install once):
      pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
    """

    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    TOKEN_FILE = Path.home() / '.wow_raid_recorder_gdrive_token.json'
    CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB per chunk

    def __init__(self, credentials_file: str, folder_id: str = ''):
        """
        Args:
            credentials_file: Path to the OAuth2 client secrets JSON file
                               downloaded from Google Cloud Console
                               (Application type: Desktop app).
            folder_id:         Google Drive folder ID to upload into.
                               Leave empty to upload to the root of My Drive.
        """
        self.credentials_file = Path(credentials_file)
        self.folder_id = folder_id.strip()
        self._creds = None
        self._authenticated = False
        self._quota_used: int = 0
        self._quota_limit: int = 0

    # ------------------------------------------------------------------
    # CloudUploadProvider interface
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        try:
            self._check_dependencies()
        except ImportError as e:
            print(f"[GDrive] ❌ Missing dependency: {redact_urls(e)}")
            print("[GDrive]  → Install with: pip install google-auth google-auth-oauthlib "
                  "google-auth-httplib2 google-api-python-client")
            return False

        if not self.credentials_file.exists():
            print(f"[GDrive] ❌ Credentials file not found: {self.credentials_file}")
            print("[GDrive]  → Download OAuth2 client secrets (Desktop app) from "
                  "https://console.cloud.google.com/apis/credentials")
            return False

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request

            creds = None
            if self.TOKEN_FILE.exists():
                creds = Credentials.from_authorized_user_file(
                    str(self.TOKEN_FILE), self.SCOPES)

            if not creds or not creds.valid:
                creds = self._refresh(creds, Request) if creds and creds.expired and creds.refresh_token else None
                if creds is None:
                    print("[GDrive] Opening browser for OAuth2 authorisation...")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_file), self.SCOPES)
                    creds = flow.run_local_server(port=0)

                self._write_token(creds.to_json())
                print(f"[GDrive] Token cached at {self.TOKEN_FILE}")

            self._creds = creds
            self._authenticated = True

            try:
                self._fetch_quota()
            except Exception as e:
                print(f"[GDrive] Warning: could not fetch quota: {redact_urls(e)}")

            print("[GDrive] ✅ Authenticated successfully")
            if self._quota_limit:
                used_gb = self._quota_used / (1024 ** 3)
                limit_gb = self._quota_limit / (1024 ** 3)
                print(f"[GDrive] Storage: {used_gb:.2f} GB / {limit_gb:.2f} GB")
            return True

        except Exception as e:
            print(f"[GDrive] ❌ Authentication error: {redact_urls(e)}")
            return False

    def is_authenticated(self) -> bool:
        return self._authenticated and self._creds is not None

    def get_storage_info(self) -> Dict[str, Any]:
        limit = self._quota_limit or 0
        used = self._quota_used or 0
        return {
            'usage_bytes': used,
            'limit_bytes': limit,
            'usage_gb': round(used / (1024 ** 3), 2),
            'limit_gb': round(limit / (1024 ** 3), 2),
            'usage_percent': round(used / limit * 100, 2) if limit else 0,
            'can_write': self._authenticated,
            'can_delete': False,
        }

    async def upload_video(self, file_path: Path, metadata: VideoMetadata,
                           progress_callback=None) -> bool:
        if not self.is_authenticated():
            print("[GDrive] Not authenticated")
            return False

        if not file_path.exists():
            print(f"[GDrive] File not found: {file_path}")
            return False

        file_size = file_path.stat().st_size
        progress = UploadProgress(
            video_name=metadata.video_key,
            total_bytes=file_size,
            uploaded_bytes=0,
            status='uploading',
            start_time=time.time(),
        )

        try:
            print(f"[GDrive] Uploading {metadata.video_key} ({file_size / (1024**2):.1f} MB)")
            session_uri = self._initiate_resumable_upload(file_path, metadata)
            if not session_uri:
                raise RuntimeError("Failed to obtain resumable upload session URI")

            success = self._execute_resumable_upload(
                file_path, file_size, session_uri, progress, progress_callback)

            if success:
                progress.status = 'completed'
                progress.uploaded_bytes = file_size
                if progress_callback:
                    progress_callback(progress)
                print(f"[GDrive] ✅ Upload complete: {metadata.video_key}")
            else:
                progress.status = 'failed'
                if progress_callback:
                    progress_callback(progress)

            return success

        except Exception as e:
            print(f"[GDrive] ❌ Upload error: {redact_urls(e)}")
            progress.status = 'failed'
            progress.error = redact_urls(e)
            if progress_callback:
                progress_callback(progress)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _refresh(creds, request_cls):
        """Refreshed credentials, or None if the refresh token is no longer valid.

        Refresh tokens of Google projects in "Testing" mode expire after 7 days;
        returning None sends the caller back through the browser consent flow.
        """
        from google.auth.exceptions import RefreshError
        print("[GDrive] Refreshing expired token...")
        try:
            creds.refresh(request_cls())
            return creds
        except RefreshError as e:
            print(f"[GDrive] Token refresh failed ({redact_urls(e)}); re-authorising")
            return None

    def _write_token(self, token_json: str):
        # The token grants Drive access: owner-only permissions.
        fd = os.open(self.TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(token_json)
        os.chmod(self.TOKEN_FILE, 0o600)  # also tighten a token written by older versions

    @staticmethod
    def _check_dependencies():
        import google.oauth2.credentials          # noqa: F401
        import google_auth_oauthlib.flow          # noqa: F401
        import google.auth.transport.requests     # noqa: F401

    def _auth_header(self) -> Dict[str, str]:
        """Return Bearer auth header, auto-refreshing the token if expired."""
        from google.auth.transport.requests import Request
        if self._creds.expired and self._creds.refresh_token:
            self._creds.refresh(Request())
        return {'Authorization': f'Bearer {self._creds.token}'}

    def _fetch_quota(self):
        resp = requests.get(
            'https://www.googleapis.com/drive/v3/about',
            params={'fields': 'storageQuota'},
            headers=self._auth_header(),
            timeout=10,
        )
        if resp.status_code == 200:
            q = resp.json().get('storageQuota', {})
            self._quota_used = int(q.get('usageInDrive', 0))
            self._quota_limit = int(q.get('limit', 0))

    def _initiate_resumable_upload(self, file_path: Path,
                                   metadata: VideoMetadata) -> Optional[str]:
        """POST to Drive resumable endpoint; returns session URI or None."""
        ext = file_path.suffix.lower()
        mime = 'video/mp4' if ext == '.mp4' else 'application/octet-stream'
        file_size = file_path.stat().st_size

        file_meta: Dict[str, Any] = {'name': file_path.name}
        if self.folder_id:
            file_meta['parents'] = [self.folder_id]

        headers = {
            **self._auth_header(),
            'Content-Type': 'application/json; charset=UTF-8',
            'X-Upload-Content-Type': mime,
            'X-Upload-Content-Length': str(file_size),
        }

        resp = requests.post(
            'https://www.googleapis.com/upload/drive/v3/files',
            params={'uploadType': 'resumable'},
            headers=headers,
            json=file_meta,
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[GDrive] Failed to initiate resumable upload: "
                  f"{resp.status_code} {resp.text[:300]}")
            return None

        session_uri = resp.headers.get('Location')
        if not session_uri:
            print("[GDrive] Missing Location header in resumable upload response")
        return session_uri

    @staticmethod
    def _committed_bytes(resp) -> int:
        """Bytes Drive has stored, from a 308 response's Range header.

        No Range header means nothing has been stored yet.
        """
        range_header = resp.headers.get('Range')
        if not range_header:
            return 0
        return int(range_header.rsplit('-', 1)[1]) + 1

    def _query_upload_offset(self, session_uri: str, file_size: int) -> Optional[int]:
        """Ask Drive how much of an interrupted upload it has; None if unknown."""
        try:
            resp = requests.put(
                session_uri,
                headers={**self._auth_header(), 'Content-Range': f'bytes */{file_size}'},
                data=b'',
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"[GDrive] Upload status query failed: {redact_urls(e)}")
            return None
        if resp.status_code in (200, 201):
            return file_size
        if resp.status_code == 308:
            return self._committed_bytes(resp)
        return None

    def _put_chunk(self, session_uri: str, chunk: bytes, offset: int,
                   file_size: int, mime: str):
        """PUT one chunk. Returns the response, or None on a network error."""
        chunk_end = offset + len(chunk) - 1
        try:
            return requests.put(
                session_uri,
                headers={
                    **self._auth_header(),
                    'Content-Type': mime,
                    'Content-Range': f'bytes {offset}-{chunk_end}/{file_size}',
                },
                data=chunk,
                timeout=300,
            )
        except requests.RequestException as e:
            print(f"[GDrive] Chunk upload error at byte {offset}: {redact_urls(e)}")
            return None

    def _execute_resumable_upload(self, file_path: Path, file_size: int,
                                  session_uri: str, progress: UploadProgress,
                                  progress_callback) -> bool:
        """Stream file to Drive in chunks, resuming from Drive's offset after errors."""
        ext = file_path.suffix.lower()
        mime = 'video/mp4' if ext == '.mp4' else 'application/octet-stream'
        uploaded = 0
        consecutive_failures = 0

        with open(file_path, 'rb') as f:
            while uploaded < file_size:
                # Drive may store less than we sent; always continue from its offset.
                f.seek(uploaded)
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break

                resp = self._put_chunk(session_uri, chunk, uploaded, file_size, mime)

                if resp is not None and resp.status_code in (200, 201):
                    uploaded = file_size
                    consecutive_failures = 0
                elif resp is not None and resp.status_code == 308:
                    uploaded = self._committed_bytes(resp)
                    consecutive_failures = 0
                elif resp is not None and resp.status_code < 500 and resp.status_code != 429:
                    # 4xx: the session is gone or the request is invalid.
                    print(f"[GDrive] Upload chunk rejected: "
                          f"{resp.status_code} {resp.text[:200]}")
                    return False
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= HTTP_STEP_ATTEMPTS:
                        print("[GDrive] Giving up after repeated chunk failures")
                        return False
                    _backoff(consecutive_failures)
                    offset = self._query_upload_offset(session_uri, file_size)
                    if offset is not None:
                        uploaded = offset

                progress.uploaded_bytes = uploaded
                if progress_callback:
                    progress_callback(progress)

        return uploaded >= file_size
