#!/usr/bin/env python3
import os
import re
import time
import csv
import io
from pathlib import Path
from threading import Event, Thread

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
#from obswebsocket import obsws, requests
import obsws_python as obs
import time

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
LOG_DIR = Path.home() / "Games" / "World of Warcraft" / "_retail_" / "Logs"
LOG_PATTERN = re.compile(r"WoWCombatLog-\d{6}_\d{6}\.txt$")

# Test‑mode: any ENCOUNTER_START triggers recording.
BOSS_IDS = {}

OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASSWORD = ""          # set if you configured a password

# ----------------------------------------------------------------------
# OBS helpers
# ----------------------------------------------------------------------
def obs_connect():
    ws = obs.ReqClient(host='localhost', port=4455, password='', timeout=3)
    return ws

def start_recording(ws):
    """
    Tell OBS to start a recording.
    Prints the full response (as a dict) and any error details.
    """
    try:
        ws.start_record()
    except Exception as exc:
        # Catches unexpected network or protocol failures
        print("[OBS] *** FAILED to start recording:", exc)


def stop_recording(ws):
    """
    Tell OBS to stop the current recording.
    Mirrors the logic of start_recording().
    """
    try:
        ws.stop_record()
    except Exception as exc:
        print("[OBS] *** FAILED to stop recording:", exc)

# ----------------------------------------------------------------------
# Combat‑log line processor (CSV format, with ENCOUNTER_END stop)
# ----------------------------------------------------------------------
def process_line(line: str, ws, state):
    """
    Handles a single combat‑log line that is CSV‑formatted.
    Starts on ENCOUNTER_START, stops on ENCOUNTER_END (or UNIT_DIED).
    """
    # --------------------------------------------------------------
    # 1️⃣ Split off the timestamp (everything before the double‑space)
    # --------------------------------------------------------------
    try:
        ts_part, rest = line.split("  ", 1)   # two spaces separate timestamp
    except ValueError:
        return  # not the expected format

    # --------------------------------------------------------------
    # 2️⃣ Parse the remainder as CSV (handles quoted fields)
    # --------------------------------------------------------------
    csv_reader = csv.reader(io.StringIO(rest))
    try:
        fields = next(csv_reader)
    except StopIteration:
        return

    if not fields:
        return

    # --------------------------------------------------------------
    # 3️⃣ First field is the event name
    # --------------------------------------------------------------
    event = fields[0].strip().upper()

    # --------------------------------------------------------------
    # 4️⃣ React to events
    # --------------------------------------------------------------
    if event == "ENCOUNTER_START":
        if not state["recording"]:
            print(f"[INFO] ENCOUNTER_START detected at {ts_part}")
            start_recording(ws)
            state["recording"] = True
        return

    if event == "ENCOUNTER_END":
        if state["recording"]:
            print(f"[INFO] ENCOUNTER_END detected at {ts_part}")
            # Run this for 3s extra to properly get the end of the encounter
            time.sleep(3)
            stop_recording(ws)
            state["recording"] = False
        return

    # Optional safety‑net: stop on any creature death
    if event == "UNIT_DIED":
        if state["recording"]:
            print(f"[INFO] UNIT_DIED detected at {ts_part} – stopping")
            stop_recording(ws)
            state["recording"] = False
        return

    # (You can add more triggers here if you wish.)

# ----------------------------------------------------------------------
# Tail‑reader thread
# ----------------------------------------------------------------------
def tail_file(path: Path, ws, stop_event: Event):
    print(f"[TAIL] Watching {path.name}")
    with path.open("r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)          # ignore historic lines
        state = {"recording": False}
        while not stop_event.is_set():
            line = f.readline()
            if not line:
                time.sleep(0.05)
                continue
            process_line(line, ws, state)

# ----------------------------------------------------------------------
# Watchdog handler – reacts to new combat‑log files
# ----------------------------------------------------------------------
class LogDirHandler(FileSystemEventHandler):
    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.tailer_thread = None
        self.stop_evt = None

    def _start_new_tail(self, new_path: Path):
        # Stop any previous tailer first
        if self.stop_evt:
            self.stop_evt.set()
            self.tailer_thread.join()

        stop_evt = Event()
        t = Thread(target=tail_file, args=(new_path, self.ws, stop_evt), daemon=True)
        t.start()
        self.tailer_thread = t
        self.stop_evt = stop_evt

    def on_created(self, event):
        if event.is_directory:
            return
        fname = os.path.basename(event.src_path)
        if LOG_PATTERN.match(fname):
            print(f"[WATCHER] New combat‑log detected: {fname}")
            self._start_new_tail(Path(event.src_path))

    def on_moved(self, event):
        # Handles the “write‑to‑temp‑then‑rename” pattern some clients use.
        self.on_created(event)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    if not LOG_DIR.is_dir():
        raise RuntimeError(f"Log directory not found: {LOG_DIR}")

    ws = obs_connect()
    handler = LogDirHandler(ws)

    observer = Observer()
    observer.schedule(handler, str(LOG_DIR), recursive=False)
    observer.start()
    print(f"[INIT] Watching directory: {LOG_DIR}")

    # Attach to the newest existing log file (if any) when we start.
    existing = sorted(
        [p for p in LOG_DIR.iterdir() if LOG_PATTERN.match(p.name)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if existing:
        print(f"[INIT] Using latest existing log: {existing[0].name}")
        handler._start_new_tail(existing[0])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        observer.stop()
        observer.join()
        if handler.stop_evt:
            handler.stop_evt.set()
            handler.tailer_thread.join()
        ws.disconnect()

if __name__ == "__main__":
    main()