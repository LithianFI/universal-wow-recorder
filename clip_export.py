"""
Clip export using ffmpeg stream copy.

Cuts a segment out of an existing recording without re-encoding. Stream copy
cuts at the nearest keyframe, so the start may shift by a few seconds —
acceptable for raid highlights where frame-exact timing isn't needed.

Output filename convention:
    <stem>_clip_<start_sec>-<end_sec>.<ext>
e.g. 2026-04-15_21-30-10_Broodtwister_Heroic_clip_180-240.mp4

The retention module recognises this suffix and exempts clips from sweeps.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from constants import LOG_PREFIXES


class ClipError(Exception):
    """Raised when clip export fails for a reason the user should see."""


@dataclass
class ClipResult:
    output_path: Path
    duration_seconds: float


def resolve_ffmpeg(configured_path: str = '') -> Optional[str]:
    """The ffmpeg to run: *configured_path* if set (a file or a command name),
    else ffmpeg from PATH. None if not found."""
    if configured_path:
        if Path(configured_path).is_file():
            return configured_path
        return shutil.which(configured_path)
    return shutil.which('ffmpeg')


def ffmpeg_available(configured_path: str = '') -> bool:
    """Return True if an ffmpeg binary can be found."""
    return resolve_ffmpeg(configured_path) is not None


def _format_seconds_for_name(seconds: float) -> str:
    """Format a float-seconds value as an integer-second string for filenames."""
    return str(max(0, int(round(seconds))))


def _unique_output_path(candidate: Path) -> Path:
    """Return *candidate* or, if it exists, "<base> (2)_clip_X-Y.ext" etc.

    The counter goes before the _clip_ suffix: clips are recognised by a name
    ending in _clip_X-Y, and "..._clip_10-20_2" would vanish from the Clips page.
    """
    if not candidate.exists():
        return candidate
    base, separator, clip_range = candidate.stem.rpartition('_clip_')
    if not separator:
        base, clip_range = candidate.stem, ''
    clip_suffix = f"_clip_{clip_range}" if separator else ''
    n = 2
    while True:
        alt = candidate.parent / f"{base} ({n}){clip_suffix}{candidate.suffix}"
        if not alt.exists():
            return alt
        n += 1


def export_clip(
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_dir: Optional[Path] = None,
    ffmpeg_path: str = '',
) -> ClipResult:
    """Cut [start_seconds, end_seconds] out of *source_path* using ffmpeg.

    Args:
        source_path: Existing video file.
        start_seconds: Inclusive start offset in seconds (>= 0).
        end_seconds: Exclusive end offset in seconds (> start_seconds).
        output_dir: Directory for the clip (defaults to source_path.parent).
        ffmpeg_path: Configured ffmpeg binary ('' = from PATH).

    Raises:
        ClipError: For any user-visible failure — missing ffmpeg, bad range,
            missing source, or ffmpeg nonzero exit.

    Returns:
        ClipResult with the output path and requested duration.
    """
    ffmpeg_bin = resolve_ffmpeg(ffmpeg_path)
    if not ffmpeg_bin:
        raise ClipError("ffmpeg not found — install it or set its path in Config")

    if not source_path.exists() or not source_path.is_file():
        raise ClipError(f"Source file not found: {source_path.name}")

    if start_seconds < 0:
        raise ClipError("Clip start must be >= 0")
    if end_seconds <= start_seconds:
        raise ClipError("Clip end must be greater than start")

    duration = end_seconds - start_seconds
    if duration < 0.5:
        raise ClipError("Clip is too short (< 0.5 seconds)")

    out_dir = output_dir or source_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize the base stem: strip any prior _clip_X-Y suffix so clips-of-clips
    # don't pile up redundant markers in the filename.
    base_stem = re.sub(r'_clip_\d+-\d+$', '', source_path.stem)

    out_name = (
        f"{base_stem}"
        f"_clip_{_format_seconds_for_name(start_seconds)}-{_format_seconds_for_name(end_seconds)}"
        f"{source_path.suffix}"
    )
    out_path = _unique_output_path(out_dir / out_name)

    # Stream copy: fast, no re-encode. -ss before -i is fast-seek at container
    # level; for accuracy we put it after -i which does decode-seek — slower
    # but matches what the user actually picked on the scrubber. For stream
    # copy the cut still snaps to a keyframe regardless, but post-input seek
    # at least gets us a correct end.
    cmd = [
        ffmpeg_bin,
        '-loglevel', 'error',
        '-y',                       # overwrite temp targets
        '-i', str(source_path),
        '-ss', f'{start_seconds:.3f}',
        '-to', f'{end_seconds:.3f}',
        '-c', 'copy',
        '-avoid_negative_ts', 'make_zero',
        str(out_path),
    ]

    print(f"{LOG_PREFIXES['FILE']} [Clip] Exporting {start_seconds:.1f}-{end_seconds:.1f}s → {out_path.name}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # A half-written clip would otherwise show up on the Clips page.
        out_path.unlink(missing_ok=True)
        raise ClipError("ffmpeg timed out after 120 seconds")
    except OSError as e:
        raise ClipError(f"Failed to invoke ffmpeg: {e}")

    if proc.returncode != 0:
        # Clean up any partial output
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        stderr_tail = (proc.stderr or '').strip().splitlines()[-3:]
        detail = '; '.join(stderr_tail) if stderr_tail else f'exit {proc.returncode}'
        raise ClipError(f"ffmpeg failed: {detail}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ClipError("ffmpeg produced no output")

    return ClipResult(output_path=out_path, duration_seconds=duration)
