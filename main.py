#!/usr/bin/env python3
"""Repo-root entry point — delegates to wow_raid_recorder.cli.main().

This is the headless CLI mode (no web dashboard). For the normal experience
with the web UI, use run.py instead.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wow_raid_recorder.cli import main

if __name__ == "__main__":
    main()
