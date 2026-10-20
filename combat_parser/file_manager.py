"""
Recording file management operations.
"""

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from constants import (
    VIDEO_EXTENSIONS,
    DEFAULT_FILE_STABILITY_CHECK_INTERVAL,
    LOG_PREFIXES,
)


class RecordingFileManager:
    """Manages recording file operations: finding, renaming, deleting."""
    
    def __init__(self, config, obs_client):
        self.config = config
        self.obs = obs_client
        self.last_renamed_path: Optional[Path] = None
    
    def get_recording_directory(self) -> Optional[Path]:
        """Get the current recording directory from OBS or config fallback."""
        try:
            settings = self.obs.get_recording_settings()
            if settings and 'record_directory' in settings:
                path = Path(settings['record_directory'])
                if path.exists():
                    print(f"{LOG_PREFIXES['FILE']} Using OBS recording directory: {path}")
                    return path

            fallback = self.config.RECORDING_PATH_FALLBACK
            if fallback:
                print(f"{LOG_PREFIXES['FILE']} Using fallback directory: {fallback}")
                if not fallback.exists():
                    fallback.mkdir(parents=True, exist_ok=True)
                return fallback
            
            print(f"{LOG_PREFIXES['FILE']} No recording directory available")
            return None
            
        except Exception as e:
            print(f"{LOG_PREFIXES['FILE']} Error getting recording directory: {e}")
            return None
    
    def find_latest_recording(self) -> Optional[Path]:
        """Find the most recent recording file in recording directory."""
        record_dir = self.get_recording_directory()
        if not record_dir:
            return None
        
        try:
            video_files = []
            for file in record_dir.iterdir():
                if file.suffix.lower() in VIDEO_EXTENSIONS and file.is_file():
                    video_files.append(file)

            if not video_files:
                print(f"{LOG_PREFIXES['FILE']} No video files found in {record_dir}")
                return None

            latest = max(video_files, key=lambda f: f.stat().st_mtime)
            print(f"{LOG_PREFIXES['FILE']} Found latest recording: {latest.name}")
            return latest
            
        except Exception as e:
            print(f"{LOG_PREFIXES['FILE']} Error finding recordings: {e}")
            return None
    
    def validate_file_stable(self, file_path: Path, 
                           check_interval: float = DEFAULT_FILE_STABILITY_CHECK_INTERVAL) -> bool:
        """Check if a file has stopped changing (OBS finished writing)."""
        try:
            if not file_path.exists():
                return False
            
            initial_size = file_path.stat().st_size
            time.sleep(check_interval)
            final_size = file_path.stat().st_size
            
            if initial_size != final_size:
                print(f"{LOG_PREFIXES['FILE']} File still changing: {initial_size} → {final_size} bytes")
                return False
            
            return True
            
        except Exception as e:
            print(f"{LOG_PREFIXES['FILE']} Error validating file stability: {e}")
            return False
    
    def delete_recording(self, recording_path: Path, reason: str = "") -> bool:
        """Delete a recording file."""
        try:
            if not recording_path.exists():
                print(f"[FILE] File already doesn't exist: {recording_path}")
                return False
        
            file_size = recording_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            recording_path.unlink()

            reason_text = f" ({reason})" if reason else ""
            print(f"[FILE] Deleted recording{reason_text}: {recording_path.name} ({file_size_mb:.2f}MB)")

            # Also remove the companion JSON sidecar if it exists.
            json_path = recording_path.with_suffix('.json')
            if json_path.exists():
                json_path.unlink()
                print(f"[FILE] Deleted companion JSON: {json_path.name}")

            return True
        
        except Exception as e:
            print(f"[FILE] Error deleting recording: {e}")
            return False
    
    def _get_difficulty_name(self, difficulty_id: int) -> str:
        """Convert difficulty ID to readable name."""
        difficulties = {
            1: "Normal", 2: "Heroic", 3: "Mythic", 4: "Mythic+",
            5: "Timewalking", 7: "LFR", 9: "40Player",
            14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR",
            22: "Mythic", 23: "Mythic", 24: "Timewalking", 33: "Timewalking",
        }
        return difficulties.get(difficulty_id, f"Difficulty_{difficulty_id}")
    
    def organize_into_date_subfolder(self, file_path: Path) -> Optional[Path]:
        """Move *file_path* (and its companion .json) into a YYYY-MM-DD subfolder.

        The date is extracted from the filename when it starts with the
        ``YYYY-MM-DD`` prefix of WCR-style names.
        If the filename doesn't match, the file's modification time is used as
        a fallback so no recording is ever left behind.

        Returns the new :class:`~pathlib.Path` on success, or ``None`` on
        failure (the original file is never deleted in that case).
        """
        if not file_path or not file_path.exists():
            print(f"{LOG_PREFIXES['FILE']} organize_into_date_subfolder: file not found: {file_path}")
            return None

        # Determine the date.
        date_str: Optional[str] = None

        # WCR names start "YYYY-MM-DD HH-MM-SS - ..."; older simple names used "_".
        m = re.match(r'^(\d{4}-\d{2}-\d{2})[ _]', file_path.name)
        if m:
            date_str = m.group(1)
        else:
            # Fallback: use the file's last-modified timestamp
            date_str = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d")

        # Create the target directory.
        target_dir = file_path.parent / date_str
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"{LOG_PREFIXES['FILE']} Could not create date subfolder {target_dir}: {e}")
            return None

        # Move the video file.
        new_path = target_dir / file_path.name

        # Avoid silent overwrites if a file with the same name already exists
        if new_path.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while new_path.exists():
                new_path = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            file_path.rename(new_path)
            print(f"{LOG_PREFIXES['FILE']} 📁 Organised into {date_str}/: {new_path.name}")
        except Exception as e:
            print(f"{LOG_PREFIXES['FILE']} Failed to move {file_path.name} → {target_dir}: {e}")
            return None

        # Move companion .json metadata (if present).
        json_src = file_path.with_suffix('.json')
        if json_src.exists():
            json_dst = new_path.with_suffix('.json')
            try:
                json_src.rename(json_dst)
                print(f"{LOG_PREFIXES['FILE']} 📁 Organised companion JSON: {json_dst.name}")
            except Exception as e:
                # Non-fatal — the video already moved successfully
                print(f"{LOG_PREFIXES['FILE']} Warning: could not move companion JSON: {e}")

        self.last_renamed_path = new_path
        return new_path