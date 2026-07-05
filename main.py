#!/usr/bin/env python3
"""Entry point for InstaStash."""

import sys

MIN_PYTHON = (3, 10)

if sys.version_info < MIN_PYTHON:
    sys.exit(
        f"This app needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
        f"(you are running {sys.version.split()[0]})."
    )

try:
    import instagrapi  # noqa: F401
except ImportError:
    sys.exit(
        "Dependencies are not installed. Run:\n\n"
        "    pip install -r requirements.txt\n"
    )

from instastash.gui import run

if __name__ == "__main__":
    run()
