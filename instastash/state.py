"""Resume state: remembers which items were fully downloaded.

The state lives in a small JSON file inside the chosen output folder, so the
resume information travels with the downloads themselves. An item is keyed by
"<collection name>:<media pk>" because the same post may legitimately appear
in more than one collection (and should then exist in both folders).

Writes are atomic (temp file + rename) so a crash mid-save can never corrupt
the state file.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

STATE_FILENAME = ".instastash_state.json"


class DownloadState:
    def __init__(self, output_dir: Path):
        self.path = Path(output_dir) / STATE_FILENAME
        self._lock = threading.Lock()
        self._completed: set[str] = set()
        self._load()

    @staticmethod
    def key(collection_name: str, media_pk) -> str:
        return f"{collection_name}:{media_pk}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._completed = set(data.get("completed", []))
        except (json.JSONDecodeError, OSError):
            # A corrupt state file only means we re-verify; never fatal.
            self._completed = set()

    def is_done(self, key: str) -> bool:
        with self._lock:
            return key in self._completed

    def folders_done_for(self, media_pk) -> list[str]:
        """Folder names (from earlier runs) that already hold this post."""
        pk = str(media_pk)
        with self._lock:
            return [
                k.rsplit(":", 1)[0]
                for k in self._completed
                if k.rsplit(":", 1)[1] == pk
            ]

    def mark_done(self, key: str) -> None:
        with self._lock:
            self._completed.add(key)
            self._save_locked()

    def _save_locked(self) -> None:
        payload = json.dumps(
            {"version": 1, "completed": sorted(self._completed)}, indent=2
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, prefix=".instastash_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
