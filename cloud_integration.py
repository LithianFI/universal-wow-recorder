"""
Cloud Upload Integration for WoW Raid Recorder.
Integrates cloud upload functionality with the existing recording system.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, Callable, Set
from threading import Thread

from cloud_upload import (
    CloudUploadProvider,
    WarcraftRecorderCloud,
    GoogleDriveUpload,
    CloudUploadQueue,
    VideoMetadata,
    UploadProgress,
)
from cloud_pov_sync import PovSyncManager, DownloadProgress
import wcr_format

# Pending/failed uploads, kept next to config.ini so they survive restarts.
UPLOAD_QUEUE_STATE_FILE = 'upload_queue.json'


def saved_pending_uploads(config) -> Set[Path]:
    """Resolved paths the saved upload queue still has to upload.

    For when no manager is running (still logging in, or login failed):
    those files must not be deleted before the queue gets to them.
    """
    state_file = Path(config.config_path).parent / UPLOAD_QUEUE_STATE_FILE
    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))
        return {Path(item['file_path']).resolve() for item in data.get('pending', [])}
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        print(f"[Cloud Manager] ⚠️ Could not read the saved upload queue: {e}")
        return set()

# How long shutdown waits for an in-flight upload. Together with the parser's
# thread join this must stay under Electron's kill timeout (30 s).
UPLOAD_SHUTDOWN_GRACE_SECONDS = 10


class CloudUploadManager:
    """
    Manages cloud upload integration with the recording system.
    Handles automatic uploads after recording completion.
    """
    
    def __init__(self, config):
        """
        Initialize cloud upload manager.
        
        Args:
            config: ConfigManager instance with cloud settings
        """
        self.config = config
        self.provider: Optional[CloudUploadProvider] = None
        self.upload_queue: Optional[CloudUploadQueue] = None
        self.progress_callback: Optional[Callable] = None
        self.pov_sync: Optional[PovSyncManager] = None
        self._get_recording_dir: Optional[Callable] = None
        self._is_recording: Optional[Callable] = None
        # Called after delete_after_upload removed a recording, to refresh the UI.
        self.on_local_copy_deleted: Optional[Callable[[], None]] = None

    async def initialize(self,
                         get_recording_dir_fn: Optional[Callable] = None,
                         is_recording_fn: Optional[Callable] = None) -> bool:
        self._get_recording_dir = get_recording_dir_fn
        self._is_recording = is_recording_fn

        if not self.config.CLOUD_UPLOAD_ENABLED:
            print("[Cloud Manager] Cloud upload is disabled in config")
            return False

        provider_name = self.config.CLOUD_UPLOAD_PROVIDER

        if provider_name == 'warcraft_recorder':
            success = await self._init_warcraft_recorder()
        elif provider_name == 'google_drive':
            success = await self._init_google_drive()
        else:
            print(f"[Cloud Manager] Unknown/unsupported provider: {provider_name}")
            return False

        if success:
            self.upload_queue = CloudUploadQueue(
                self.provider,
                provider_name=provider_name,
                state_file=Path(self.config.config_path).parent / UPLOAD_QUEUE_STATE_FILE,
                on_uploaded=self._after_upload,
            )

            if self.progress_callback:
                self.upload_queue.add_progress_callback(self.progress_callback)

            self.upload_queue.start()

            # Start POV sync if enabled and provider supports it (WCR only)
            if (provider_name == 'warcraft_recorder'
                    and self.config.POV_SYNC_ENABLED
                    and self._get_recording_dir is not None):
                self._start_pov_sync()

            print("[Cloud Manager] ✅ Cloud upload initialized and ready")

        return success
    
    def _after_upload(self, file_path: Path):
        """Delete the local recording and its sidecar if the user asked for it."""
        if not self.config.CLOUD_DELETE_AFTER_UPLOAD:
            return
        sidecar = wcr_format.read_sidecar(file_path)
        if sidecar.get('protected'):
            print(f"[Cloud Manager] Kept local copy of protected recording: {file_path.name}")
            return
        if not sidecar and file_path.with_suffix('.json').exists():
            # Unreadable right now (e.g. mid-write): it may be marked Keep.
            print(f"[Cloud Manager] Kept local copy, its metadata could not be read: {file_path.name}")
            return
        for path in (file_path, file_path.with_suffix('.json')):
            path.unlink(missing_ok=True)
        print(f"[Cloud Manager] Deleted local copy after upload: {file_path.name}")
        if self.on_local_copy_deleted:
            self.on_local_copy_deleted()

    async def authenticate_only(self) -> bool:
        """Log in to the configured provider without starting queue or POV sync."""
        provider_name = self.config.CLOUD_UPLOAD_PROVIDER
        if provider_name == 'warcraft_recorder':
            return await self._init_warcraft_recorder()
        if provider_name == 'google_drive':
            return await self._init_google_drive()
        print(f"[Cloud Manager] Unknown/unsupported provider: {provider_name}")
        return False

    def is_upload_pending(self, file_path: Path) -> bool:
        return bool(self.upload_queue) and self.upload_queue.is_pending(file_path)

    def retry_failed_uploads(self) -> int:
        return self.upload_queue.retry_failed() if self.upload_queue else 0

    async def _init_warcraft_recorder(self) -> bool:
        """Initialize Warcraft Recorder cloud provider."""
        username = self.config.WCR_USERNAME
        password = self.config.WCR_PASSWORD
        guild = self.config.WCR_GUILD
        
        if not username or not password or not guild:
            print("[Cloud Manager] ❌ Missing Warcraft Recorder credentials in config")
            return False
        
        print(f"[Cloud Manager] Initializing Warcraft Recorder Cloud (guild: {guild})")
        
        self.provider = WarcraftRecorderCloud(username, password, guild)
        
        # Authenticate
        authenticated = await self.provider.authenticate()
        
        if authenticated:
            storage_info = self.provider.get_storage_info()
            print(f"[Cloud Manager] Storage: {storage_info['usage_gb']}GB / {storage_info['limit_gb']}GB "
                  f"({storage_info['usage_percent']}%)")
        
        return authenticated
    
    async def _init_google_drive(self) -> bool:
        """Initialize Google Drive cloud provider."""
        credentials_file = self.config.GDRIVE_CREDENTIALS_FILE
        folder_id = self.config.GDRIVE_FOLDER_ID

        if not credentials_file:
            print("[Cloud Manager] ❌ gdrive_credentials_file not set in config")
            return False

        print(f"[Cloud Manager] Initializing Google Drive "
              f"(folder: {folder_id or 'My Drive root'})")

        self.provider = GoogleDriveUpload(credentials_file, folder_id)
        authenticated = await self.provider.authenticate()

        if authenticated:
            storage_info = self.provider.get_storage_info()
            if storage_info.get('limit_gb'):
                print(f"[Cloud Manager] Storage: {storage_info['usage_gb']}GB "
                      f"/ {storage_info['limit_gb']}GB "
                      f"({storage_info['usage_percent']}%)")

        return authenticated
    
    def queue_upload(self, file_path: Path, metadata: 'VideoMetadata') -> bool:
        """
        Queue a recording for upload to cloud storage.

        Args:
            file_path: Path to the recording file
            metadata:  VideoMetadata identifying the file (WCR metadata is read from its sidecar at upload)

        Returns:
            True if queued successfully, False otherwise
        """
        if not self.config.CLOUD_UPLOAD_ENABLED:
            return False

        if not self.upload_queue:
            print("[Cloud Manager] Upload queue not initialized")
            return False

        success = self.upload_queue.add_to_queue(file_path, metadata)

        if success:
            print(f"[Cloud Manager] ✅ Queued for upload: {file_path.name}")

        return success
    
    def set_progress_callback(self, callback: Callable[[UploadProgress], None]):
        """
        Set callback function for upload progress updates.
        
        Args:
            callback: Function that receives UploadProgress objects
        """
        self.progress_callback = callback
        if self.upload_queue:
            self.upload_queue.add_progress_callback(callback)
    
    def get_queue_status(self) -> dict:
        """Get current upload queue status."""
        if not self.upload_queue:
            return {
                'enabled': False,
                'queue_size': 0,
                'active_upload': None,
            }
        
        status = self.upload_queue.get_status()
        status['enabled'] = self.config.CLOUD_UPLOAD_ENABLED
        status['provider'] = self.config.CLOUD_UPLOAD_PROVIDER
        
        return status
    
    def get_storage_info(self) -> dict:
        """Get cloud storage information."""
        if not self.provider:
            return {}
        return self.provider.get_storage_info()

    # ------------------------------------------------------------------
    # POV sync
    # ------------------------------------------------------------------

    def _start_pov_sync(self):
        """Instantiate and start the PovSyncManager."""
        if not isinstance(self.provider, WarcraftRecorderCloud):
            return
        self.pov_sync = PovSyncManager(
            wcr_cloud=self.provider,
            config=self.config,
            get_recording_dir_fn=self._get_recording_dir,
            is_recording_fn=self._is_recording,
        )
        self.pov_sync.start()
        print("[Cloud Manager] POV sync started")

    def set_pov_progress_callback(self, cb: Callable[[DownloadProgress], None]):
        if self.pov_sync:
            self.pov_sync.add_progress_callback(cb)

    def get_available_povs(self) -> dict:
        if not self.pov_sync:
            return {}
        return self.pov_sync.get_available_povs()

    def get_pov_status(self) -> dict:
        if not self.pov_sync:
            return {'enabled': False}
        status = self.pov_sync.get_status()
        status['enabled'] = True
        return status

    def trigger_pov_sync(self):
        if self.pov_sync:
            self.pov_sync.trigger_sync()

    def queue_pov_download(self, video_key: str) -> bool:
        if not self.pov_sync:
            return False
        return self.pov_sync.queue_download(video_key)

    def queue_all_povs_for_hash(self, unique_hash: str) -> int:
        if not self.pov_sync:
            return 0
        return self.pov_sync.queue_all_for_hash(unique_hash)

    def set_cloud_protection(self, video_name: str, protect: bool) -> bool:
        """Mirror a recording's keep flag to its cloud copy, in the background.

        False when the provider has no such concept (Google Drive) or isn't
        ready. A recording not uploaded yet carries the flag in its sidecar.
        """
        if not self.is_ready() or not isinstance(self.provider, WarcraftRecorderCloud):
            return False
        provider = self.provider
        Thread(target=lambda: asyncio.run(provider.protect_videos([video_name], protect)),
               daemon=True).start()
        return True

    def is_ready(self) -> bool:
        """Check if cloud upload is ready."""
        return (
            self.config.CLOUD_UPLOAD_ENABLED and
            self.provider is not None and
            self.provider.is_authenticated() and
            self.upload_queue is not None
        )
    
    async def shutdown(self, wait_for_workers: bool = False):
        """Stop POV sync and the upload worker.

        With *wait_for_workers*, return only once both have exited. A
        replacement manager must wait: its queue reloads the saved state,
        which still lists the in-flight upload, and would upload it again
        alongside the old worker.
        """
        print("[Cloud Manager] Shutting down...")
        if self.pov_sync:
            self.pov_sync.stop()
        if self.upload_queue:
            wait = 0
            while self.upload_queue.is_busy() and wait < UPLOAD_SHUTDOWN_GRACE_SECONDS:
                await asyncio.sleep(1)
                wait += 1
            self.upload_queue.stop()
        if wait_for_workers:
            if self.pov_sync:
                self.pov_sync.join()
            if self.upload_queue and not self.upload_queue.join(timeout=0):
                print("[Cloud Manager] Waiting for the current upload to finish...")
                self.upload_queue.join()
        print("[Cloud Manager] Shutdown complete")

# =============================================================================
# Integration Helper Functions
# =============================================================================

async def initialize_cloud_upload(
    config,
    get_recording_dir_fn: Optional[Callable] = None,
    is_recording_fn: Optional[Callable] = None,
) -> Optional[CloudUploadManager]:
    """
    Initialize cloud upload if enabled in config.

    Args:
        config:               ConfigManager instance
        get_recording_dir_fn: callable that returns the recording Path (or None).
        is_recording_fn:      callable returning True while a recording is active;
                              used to pause POV downloads during fights.

    Returns:
        CloudUploadManager if successful, None otherwise
    """
    if not config.CLOUD_UPLOAD_ENABLED:
        print("[Cloud] Cloud upload disabled in config")
        return None

    manager = CloudUploadManager(config)
    success = await manager.initialize(
        get_recording_dir_fn=get_recording_dir_fn,
        is_recording_fn=is_recording_fn,
    )

    if success:
        return manager

    print("[Cloud] Failed to initialize cloud upload")
    return None


def should_auto_upload(config, recording_duration: float,
                       category: str, difficulty_id: Optional[int]) -> bool:
    """
    Determine if a recording should be automatically uploaded.
    
    Args:
        config: ConfigManager instance
        recording_duration: Duration of the recording in seconds
        category: RecordingCategory string of the recording
        difficulty_id: WoW difficulty ID, if known
        
    Returns:
        True if recording should be uploaded, False otherwise
    """
    if not config.CLOUD_UPLOAD_ENABLED:
        return False
    
    if not config.CLOUD_AUTO_UPLOAD:
        return False
    
    # Don't upload very short recordings
    min_duration = config.MIN_RECORDING_DURATION
    if recording_duration < min_duration:
        return False

    if not config.should_upload_content(category, difficulty_id):
        print(f"[Cloud] Auto upload not enabled for this content ({category}, difficulty {difficulty_id}); keeping local only")
        return False
    
    return True
