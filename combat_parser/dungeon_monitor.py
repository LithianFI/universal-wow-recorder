"""
Watchdog for fights whose END line never arrives (game crash, missed line).
"""

import time
import threading
from typing import Optional, Callable

from constants import (
    LOG_PREFIXES,
    MAX_DUNGEON_SECONDS,
    MAX_ENCOUNTER_SECONDS,
)


class DungeonMonitor:
    """Calls *on_timeout* when a dungeon or encounter outlives its maximum length."""
    
    def __init__(self, state_manager, config, on_timeout: Optional[Callable] = None):
        self.state = state_manager
        self.config = config
        self.on_timeout = on_timeout
        
        self._monitor_thread = None
        self._running = False
        self._check_interval = 5  # seconds
    
    def start(self):
        """Start the dungeon monitor thread."""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self._monitor_thread.start()
        print(f"{LOG_PREFIXES['DUNGEON']} Started dungeon timeout monitor")
    
    def stop(self):
        """Stop the dungeon monitor."""
        self._running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        print(f"{LOG_PREFIXES['DUNGEON']} Stopped dungeon timeout monitor")
    
    def _monitor_loop(self):
        # No idle timeout: logs are buffered during boss fights, so activity
        # goes quiet mid-pull even when the fight is ongoing. Only a total
        # duration far beyond any real fight counts as a missed END.
        while self._running:
            time.sleep(self._check_interval)
            if self._is_overdue() and self.on_timeout:
                print(f"{LOG_PREFIXES['DUNGEON']} No END after the maximum fight length, ending it")
                self.on_timeout()

    def _is_overdue(self) -> bool:
        now = time.time()
        state = self.state
        if state.dungeon_active and state.dungeon_start_time:
            return now - state.dungeon_start_time > MAX_DUNGEON_SECONDS
        if state.encounter_active and not state.manual_recording and state.encounter_start_time:
            return now - state.encounter_start_time > MAX_ENCOUNTER_SECONDS
        return False
    
    def is_running(self) -> bool:
        """Check if monitor is running."""
        return self._running