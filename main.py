#!/usr/bin/env python3
# main.py (updated with config validation)
import time
import argparse
from watchdog.observers import Observer
from pathlib import Path

from config_manager import ConfigManager
from obs_client import OBSClient
from state_manager import RecordingState
from combat_parser import CombatParser
from log_watcher import LogDirHandler

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='WoW Raid Recorder')
    parser.add_argument('--config', '-c', type=str, 
                       help='Path to configuration file')
    parser.add_argument('--show-config', action='store_true',
                       help='Show current configuration and exit')
    parser.add_argument('--no-rename', action='store_true',
                       help='Disable auto-renaming of recordings')
    parser.add_argument('--create-config', action='store_true',
                       help='Create a default config file and exit')
    return parser.parse_args()

def main():
    # Parse command line arguments
    args = parse_arguments()
    
    # Handle --create-config flag
    if args.create_config:
        config_path = Path(args.config) if args.config else None
        config = ConfigManager(config_path)
        print(f"[CONFIG] Configuration file created at: {config.config_path}")
        print("[CONFIG] Please edit the file and run the program again.")
        return
    
    # Initialize configuration manager
    config_path = Path(args.config) if args.config else None
    config = ConfigManager(config_path)
    
    # Apply command line overrides
    if args.no_rename:
        config.set('Recording', 'auto_rename', 'false')
        print("[CONFIG] Auto-rename disabled via command line")
    
    # Show configuration if requested
    if args.show_config:
        config.print_config()
        return
    
    # Validate configuration
    errors = config.validate_config()
    if errors:
        print("[ERROR] Configuration errors found:")
        for section, section_errors in errors.items():
            print(f"  [{section}]")
            for error in section_errors:
                print(f"    - {error}")
        print("\nPlease fix these errors in your config file and try again.")
        return

    # Initialize components
    obs_client = OBSClient(
        host=config.OBS_HOST,
        port=config.OBS_PORT,
        password=config.OBS_PASSWORD
    )
    
    state_manager = RecordingState()
    parser = CombatParser(obs_client, state_manager, config)
    log_handler = LogDirHandler(parser)
    
    # Connect to OBS
    try:
        obs_client.connect()
        
        # Get recording settings
        settings = obs_client.get_recording_settings()
        if settings and 'record_directory' in settings:
            print(f"[OBS] Recording directory: {settings['record_directory']}")
        elif config.RECORDING_PATH_FALLBACK:
            print(f"[OBS] Using fallback recording path: {config.RECORDING_PATH_FALLBACK}")
        else:
            print("[OBS] Warning: No recording directory configured or detected")
        
    except Exception as e:
        print(f"Failed to connect to OBS: {e}")
        print("Please ensure:")
        print("1. OBS is running")
        print("2. OBS WebSocket server is enabled (Tools -> WebSocket Server Settings)")
        print("3. Host, port, and password in config match OBS settings")
        return

    # Start directory observer
    observer = Observer()
    observer.schedule(log_handler, str(config.LOG_DIR), recursive=False)
    observer.start()
    print(f"[INIT] Watching directory: {config.LOG_DIR}")
    print(f"[INIT] Recordings will be named as: YYYY-MM-DD_HH-MM-SS_BossName_Difficulty{config.RECORDING_EXTENSION}")
    
    # Show difficulty settings
    print(f"[INIT] Enabled difficulties:")
    print(f"  - LFR: {'✓' if config.RECORD_LFR else '✗'}")
    print(f"  - Normal: {'✓' if config.RECORD_NORMAL else '✗'}")
    print(f"  - Heroic: {'✓' if config.RECORD_HEROIC else '✗'}")
    print(f"  - Mythic: {'✓' if config.RECORD_MYTHIC else '✗'}")
    print(f"  - Other: {'✓' if config.RECORD_OTHER else '✗'}")
    
    if config.AUTO_RENAME:
        print(f"[INIT] Auto-rename enabled (delay: {config.RENAME_DELAY}s)")
    else:
        print("[INIT] Auto-rename disabled")
    
    # Show recording cleanup settings
    print(f"[INIT] Short recording cleanup: {'✓' if config.DELETE_SHORT_RECORDINGS else '✗'} (min: {config.MIN_RECORDING_DURATION}s)")
    
    # Show recording path info
    if config.RECORDING_PATH_FALLBACK:
        print(f"[INIT] Fallback recording path: {config.RECORDING_PATH_FALLBACK}")
        if not config.RECORDING_PATH_FALLBACK.exists():
            print(f"[INIT] Warning: Fallback recording path does not exist")

    # Attach to the newest existing log file (if any)
    try:
        existing = sorted(
            [p for p in config.LOG_DIR.iterdir() if config.LOG_PATTERN.match(p.name)],
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