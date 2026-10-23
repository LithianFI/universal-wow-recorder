"""
Retroactive cooldown scan for recordings made before cooldown tracking, or
whose scan missed lines, as long as the combat log is still on disk.

The live parser collects casts itself (fight_scan); this finds a past fight in
a possibly multi-GB log by binary search on the line timestamps and runs the
same scan over just that window.
"""

from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from combat_parser.fight_scan import LogClock, scan_fight

# Extra seconds read past the fight's end, so casts right on the boundary
# aren't missed when the recorded duration was rounded.
_END_TOLERANCE_SECONDS = 10
# Binary search stops once the window is this small; the rest is streamed.
_SEEK_PRECISION_BYTES = 64 * 1024
# Lines tried after a seek before giving up on finding a timestamp there.
_SEEK_PROBE_LINES = 50


def _first_line_ms(f: BinaryIO, clock: LogClock) -> Optional[int]:
    """Timestamp of the first complete line after the current position."""
    f.readline()  # most likely the tail of a line cut by the seek
    for _ in range(_SEEK_PROBE_LINES):
        raw = f.readline()
        if not raw:
            return None
        line = raw.decode('utf-8', errors='replace')
        sep = line.find('  ')
        line_ms = clock.line_ms(line[:sep]) if sep > 0 else None
        if line_ms is not None:
            return line_ms
    return None


def _seek_to_time(f: BinaryIO, target_ms: int, clock: LogClock):
    """Position *f* at a line boundary at or shortly before the first line
    logged at *target_ms* (logs are written in time order)."""
    lo, hi = 0, f.seek(0, 2)
    while hi - lo > _SEEK_PRECISION_BYTES:
        mid = (lo + hi) // 2
        f.seek(mid)
        line_ms = _first_line_ms(f, clock)
        if line_ms is None or line_ms >= target_ms:
            hi = mid
        else:
            lo = mid
    f.seek(lo)
    if lo:
        f.readline()


def _window_lines(f: BinaryIO, start_ms: int, end_ms: int) -> Iterator[str]:
    """Lines logged in [start_ms, end_ms], streamed from the current position."""
    clock = LogClock()
    for raw in f:
        line = raw.decode('utf-8', errors='replace')
        sep = line.find('  ')
        line_ms = clock.line_ms(line[:sep]) if sep > 0 else None
        if line_ms is None or line_ms < start_ms:
            continue
        if line_ms > end_ms:
            return
        yield line


def scan_log_for_cooldowns(log_path: Path, start_ms: int, duration_s: int) -> list:
    """Tracked cooldown casts in a past fight, offsets relative to *start_ms*.

    start_ms:   fight start in epoch ms   (sidecar "start")
    duration_s: fight length in seconds   (sidecar "duration")

    Returns casts shaped like the live scan's (metadata cooldownCasts).
    Raises OSError if the log can't be read.
    """
    end_ms = start_ms + (duration_s + _END_TOLERANCE_SECONDS) * 1000
    with open(log_path, 'rb') as f:
        _seek_to_time(f, start_ms, LogClock())
        scan = scan_fight(_window_lines(f, start_ms, end_ms), start_ms, track_deaths=False)

    if not scan.line_count:
        print("[CooldownScanner] No lines found in window — log may not cover this encounter")
    print(f"[CooldownScanner] Found {len(scan.casts)} tracked casts in {scan.line_count} lines")
    return scan.casts
