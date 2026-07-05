"""Where InstaStash keeps its own data (session, download memory).

Running from source: next to the code, as always.
Running as a packaged app (PyInstaller): a proper per-user data directory,
because writing inside the .app/.exe bundle is fragile and breaks signing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    if getattr(sys, "frozen", False):  # packaged by PyInstaller
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "InstaStash"
        if os.name == "nt":
            return Path(os.environ.get("APPDATA", str(Path.home()))) / "InstaStash"
        return Path.home() / ".instastash"
    return Path(__file__).resolve().parent.parent
