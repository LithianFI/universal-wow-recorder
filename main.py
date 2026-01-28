#!/usr/bin/env python3
# main.py
import time
from watchdog.observers import Observer
from pathlib import Path

from config import Config
from obs_client import OBSClient
from state_manager import RecordingState
from combat_parser import CombatParser
from log_watcher import LogDirHandler

def main():
    # Validate log directory exists
    if not Config.LOG_DIR.is_dir():
        raise RuntimeError(f"Log directory not found: {Config.LOG_DIR}")

    # Initialize components
    obs_client = OBSClient(
        host=Config.OBS_HOST,
        port=Config.OBS_PORT,
        password=Config.OBS_PASSWORD
    )
    
    state_manager = RecordingState()
    parser = CombatParser(obs_client, state_manager)
    log_handler = LogDirHandler(parser)
    
    # Connect to OBS
    try:
        obs_client.connect()
        
        # Get recording settings
        settings = obs_client.get_recording_settings()
        if settings and 'record_directory' in settings:
            print(f"[OBS] Recording directory: {settings['record_directory']}")
        
    except Exception as e:
        print(f"Failed to connect to OBS: {e}")
        return

    # Start directory observer
    observer = Observer()
    observer.schedule(log_handler, str(Config.LOG_DIR), recursive=False)
    observer.start()
    print(f"[INIT] Watching directory: {Config.LOG_DIR}")
    print(f"[INIT] Recordings will be named as: YYYY-MM-DD_HH-MM-SS_BossName_Difficulty.mp4")

    # Attach to the newest existing log file (if any)
    try:
        existing = sorted(
            [p for p in Config.LOG_DIR.iterdir() if Config.LOG_PATTERN.match(p.name)],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if existing:
            print(f"[INIT] Using latest existing log: {existing[0].name}")
            log_handler._start_new_tail(existing[0])
    except Exception as e:
        print(f"[INIT] Error finding existing logs: {e}")

    # Main loop
    try:
        print("Recorder running")
        while True:
            time.sleep(1)
            
    except Exception:
        print("\n[SHUTDOWN] Shutting down gracefully...")
    finally:
        # Cleanup
        observer.stop()
        observer.join()
        log_handler.stop()
        obs_client.disconnect()
        print("[SHUTDOWN] Cleanup complete")

if __name__ == "__main__":
    main()