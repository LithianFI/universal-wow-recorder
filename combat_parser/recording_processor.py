"""
Recording processing logic for encounters and dungeons.
"""

import time
from typing import Optional
from pathlib import Path
from datetime import datetime

from constants import (
    LOG_PREFIXES,
)

from combat_parser.events import BossInfo, DungeonInfo
from obs_client import StopResult
from metadata_generator import RecordingMetadata


class RecordingProcessor:
    """Processes recordings based on encounter events."""
    
    def __init__(self, obs_client, file_manager, config):
        self.obs = obs_client
        self.file_manager = file_manager
        self.config = config
    
    def should_record_encounter(self, boss_info: BossInfo) -> bool:
        """Whether this encounter's difficulty is enabled for recording."""
        if self.config.is_difficulty_enabled(boss_info.difficulty_id):
            return True
        diff_name = self.file_manager._get_difficulty_name(boss_info.difficulty_id)
        print(f"{LOG_PREFIXES['PROC']} Skipping {diff_name} encounter - not enabled in config")
        return False

    def should_record_dungeon(self) -> bool:
        """Whether Mythic+ recording is enabled."""
        if self.config.RECORD_MPLUS:
            return True
        print(f"{LOG_PREFIXES['PROC']} Skipping M+ dungeon - not enabled in config")
        return False

    def process_encounter_start(self, boss_info: BossInfo) -> bool:
        """Start recording for an encounter already approved by should_record_encounter."""
        print(f"{LOG_PREFIXES['PROC']} Starting recording for: {boss_info.name}")
        return self._start_obs()

    def process_dungeon_start(self, dungeon_info: DungeonInfo) -> bool:
        """Start recording for a dungeon already approved by should_record_dungeon."""
        print(f"{LOG_PREFIXES['PROC']} Starting recording for: {dungeon_info.name} (+{dungeon_info.dungeon_level})")
        return self._start_obs()

    def _start_obs(self) -> bool:
        if not self.obs.start_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to start OBS recording")
            return False
        return True

    def stop_recording(self) -> StopResult:
        """Stop OBS. Callers pass the result to process_*_end."""
        return self.obs.stop_recording()

    def process_encounter_end(self, stop_result: StopResult, boss_info: BossInfo,
                            recording_duration: float,
                            metadata: Optional[RecordingMetadata] = None,
                            start_time: Optional[datetime] = None) -> Optional[Path]:
        """Rename/delete the stopped encounter recording. Returns the saved path."""
        print(f"{LOG_PREFIXES['PROC']} Processing recording for: {boss_info.name}")
        return self._process_stopped(
            stop_result,
            boss_info=boss_info,
            recording_duration=recording_duration,
            metadata=metadata,
            start_time=start_time
        )

    def process_dungeon_end(self, stop_result: StopResult, dungeon_info: DungeonInfo = None,
                           recording_duration: float = 0, reason: str = "",
                           metadata: Optional[RecordingMetadata] = None,
                           start_time: Optional[datetime] = None) -> Optional[Path]:
        """Rename/delete the stopped dungeon recording. Returns the saved path."""
        print(f"{LOG_PREFIXES['PROC']} Processing dungeon recording{f' ({reason})' if reason else ''}")
        return self._process_stopped(
            stop_result,
            dungeon_info=dungeon_info,
            recording_duration=recording_duration,
            metadata=metadata,
            start_time=start_time
        )

    def _process_stopped(self, stop_result: StopResult, **file_args) -> Optional[Path]:
        if not stop_result.stopped:
            # Nothing of ours was recording: never touch files on disk.
            print(f"{LOG_PREFIXES['PROC']} No recording was stopped, leaving files untouched")
            return None

        time.sleep(self.config.RENAME_DELAY)

        recording_path = self._resolve_recording_path(stop_result.output_path)
        if not recording_path:
            print(f"{LOG_PREFIXES['PROC']} Could not find recording file")
            return None
        return self._process_recording_file(recording_path, **file_args)

    def _resolve_recording_path(self, output_path: Optional[Path]) -> Optional[Path]:
        if output_path and output_path.is_file():
            return output_path
        # OBS older than websocket 5 doesn't report the path; we did stop a
        # recording, so the newest file is the best remaining guess.
        return self.file_manager.find_latest_recording()

    def _process_recording_file(self, recording_path: Path, boss_info: BossInfo = None,
                               dungeon_info: DungeonInfo = None, recording_duration: float = 0,
                               metadata: RecordingMetadata = None,
                               start_time: Optional[datetime] = None) -> Optional[Path]:
        """Rename the recording WCR-style and write its JSON sidecar (or delete
        it if too short). Returns the saved path."""
        min_duration = self.config.MIN_RECORDING_DURATION
        if recording_duration < min_duration:
            print(f"{LOG_PREFIXES['PROC']} Recording too short ({recording_duration:.1f}s)")
            return self._handle_short_recording(recording_path, recording_duration)

        if not self.file_manager.wait_until_stable(recording_path):
            print(f"{LOG_PREFIXES['PROC']} Recording file not stable, skipping")
            return None

        if start_time is None:
            start_time = datetime.fromtimestamp(recording_path.stat().st_mtime)

        new_path = self._rename_wcr_style(recording_path, metadata, start_time)
        if new_path:
            self._save_metadata_json(new_path, metadata)

        # Move into date subfolder.
        if new_path and self.config.ORGANIZE_BY_DATE:
            organised = self.file_manager.organize_into_date_subfolder(new_path)
            if organised:
                new_path = organised

        if new_path:
            self.file_manager.last_renamed_path = new_path

        return new_path

    def _rename_wcr_style(self, recording_path: Path, metadata: RecordingMetadata, 
                         start_time: datetime) -> Optional[Path]:
        """Rename recording using WCR-style filename."""
        try:
            extension = recording_path.suffix
            new_filename = metadata.generate_filename(start_time, extension)
            new_path = self._unused_path(recording_path.parent / new_filename)

            recording_path.rename(new_path)
            print(f"{LOG_PREFIXES['PROC']} ✅ Renamed (WCR): {recording_path.name} -> {new_path.name}")
            
            return new_path
        except Exception as e:
            print(f"{LOG_PREFIXES['PROC']} ❌ Failed to rename with WCR style: {e}")
            return None
    
    @staticmethod
    def _unused_path(path: Path) -> Path:
        """*path*, or "name (2).ext" etc. if taken: rename() overwrites on POSIX."""
        candidate, counter = path, 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            counter += 1
        return candidate

    def _save_metadata_json(self, video_path: Path, metadata: RecordingMetadata) -> bool:
        """Save metadata JSON file alongside the video."""
        try:
            json_path = video_path.with_suffix('.json')
            success = metadata.save_json(json_path)
            
            if success:
                print(f"{LOG_PREFIXES['PROC']} ✅ Saved metadata JSON: {json_path.name}")
            
            return success
        except Exception as e:
            print(f"{LOG_PREFIXES['PROC']} ❌ Failed to save metadata JSON: {e}")
            return False
    
    def _handle_short_recording(self, recording_path: Path, duration: float) -> Optional[Path]:
        """Delete a too-short recording, or keep it as-is. Returns the kept path."""
        if not self.config.DELETE_SHORT_RECORDINGS:
            print(f"{LOG_PREFIXES['PROC']} Short recording kept (delete_short_recordings = false)")
            return recording_path

        self.file_manager.delete_recording(recording_path, f"too short ({duration:.1f}s)")
        return None
