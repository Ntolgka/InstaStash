"""Per-account download memory.

Unlike the per-folder resume state (which records what already exists in one
output folder), this cache remembers what was ever downloaded for an
Instagram *account*, across all runs and output folders. It enables:

- "Only download new items": posts downloaded in any earlier run are skipped.
- Incremental fetching: per collection we store the pk of the newest item
  that was fully downloaded; the next run asks Instagram only for items
  saved after it, instead of re-listing the whole collection.

The newest-pk marker is advanced only when a collection completes with zero
failures, so interrupted or failed items are always seen (and retried) by
the next run. If a marker item is ever unsaved, Instagram simply never
reports it, instagrapi falls back to a full listing, and the downloaded-set
filter still prevents duplicates — the cache can cause extra work, never
missed items.

Lives in cache/<username>.json next to the app (git-ignored). Contains only
media ids — no credentials, no media.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .naming import sanitize_name

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


class AccountCache:
    def __init__(self, username: str):
        self.path = CACHE_DIR / f"{sanitize_name(username, 'account')}.json"
        self._lock = threading.Lock()
        # collection id -> {"name": str, "newest_pk": int, "downloaded": set}
        self._collections: dict[str, dict] = {}
        self._load()

    # ----------------------------------------------------------------- disk

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for cid, entry in data.get("collections", {}).items():
                self._collections[cid] = {
                    "name": entry.get("name", ""),
                    "newest_pk": int(entry.get("newest_pk", 0)),
                    "downloaded": set(entry.get("downloaded", [])),
                }
        except (json.JSONDecodeError, OSError, ValueError):
            # Corrupt cache only means more re-checking, never data loss.
            self._collections = {}

    def _save_locked(self) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "collections": {
                    cid: {
                        "name": entry["name"],
                        "newest_pk": entry["newest_pk"],
                        "downloaded": sorted(entry["downloaded"]),
                    }
                    for cid, entry in self._collections.items()
                },
            },
            indent=2,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, prefix=".cache_", suffix=".tmp"
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

    # ------------------------------------------------------------------ api

    def _entry(self, collection_id: str) -> dict:
        return self._collections.setdefault(
            str(collection_id),
            {"name": "", "newest_pk": 0, "downloaded": set()},
        )

    def newest_pk(self, collection_id: str) -> int:
        with self._lock:
            return self._entry(collection_id)["newest_pk"]

    def is_downloaded(self, collection_id: str, media_pk) -> bool:
        with self._lock:
            return str(media_pk) in self._entry(collection_id)["downloaded"]

    def downloaded_pks(self, collection_id: str) -> set[str]:
        with self._lock:
            return set(self._entry(collection_id)["downloaded"])

    def mark_downloaded(self, collection_id: str, name: str, media_pk) -> None:
        with self._lock:
            entry = self._entry(collection_id)
            entry["name"] = name or entry["name"]
            entry["downloaded"].add(str(media_pk))
            self._save_locked()

    def advance_newest(self, collection_id: str, name: str, newest_pk) -> None:
        """Record that everything up to newest_pk is fully downloaded."""
        with self._lock:
            entry = self._entry(collection_id)
            entry["name"] = name or entry["name"]
            entry["newest_pk"] = int(newest_pk)
            self._save_locked()

    def total_downloaded(self) -> int:
        with self._lock:
            return sum(len(e["downloaded"]) for e in self._collections.values())

    def clear(self) -> None:
        with self._lock:
            self._collections = {}
            self.path.unlink(missing_ok=True)
