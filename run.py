#!/usr/bin/env python3
"""Repo-root entry point — delegates to wow_raid_recorder.app.main()."""

import sys
from pathlib import Path

# Make `src/` importable when running directly from the repo root or from a
# PyInstaller bundle.
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wow_raid_recorder.app import main

if __name__ == "__main__":
    main()
