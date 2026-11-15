"""Thin entrypoint for the Excel desktop agent.

All behaviour lives in :mod:`excel_agent`. This file only makes the package
importable when run as ``python main.py`` from the project directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from excel_agent.cli import main  # noqa: E402
from excel_agent.logging_setup import setup_logging  # noqa: E402

if __name__ == "__main__":
    # Every command writes data/logs/agent.log, so a failed run can be read
    # back afterwards instead of guessed at from screenshots.
    setup_logging(
        level=os.getenv("LOG_LEVEL", "INFO"),
        console=os.getenv("LOG_CONSOLE", "0") == "1",
    )
    raise SystemExit(main())
