"""Background download worker.

Runs in its own thread, emits progress events into a queue that the GUI
polls. Files are downloaded straight from Instagram's CDN at source quality
(instagrapi always exposes the highest-resolution candidate URL), streamed in
chunks so we can report live speed, and written to a ".part" temp file that
is renamed only when complete — so an interrupted download never leaves a
half-written file that looks finished.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue

import requests
from instagrapi.exceptions import (
    ClientThrottledError,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from .cache import AccountCache
from .client import InstagramClient
from .naming import extension_from_url, media_basename, sanitize_name
from .state import DownloadState

CHUNK_SIZE = 64 * 1024
MAX_RETRIES = 3
SPEED_WINDOW_SECONDS = 8.0
UNCATEGORIZED_FOLDER = "Uncategorized"
UNCATEGORIZED_ID = "UNCATEGORIZED"


class DownloadAborted(Exception):
    """User pressed Stop."""


@dataclass
class Event:
    """A progress event for the GUI. `kind` is one of:

    log        - message: str
    collection - name: str, index: int, count: int   (starting a collection)
    item       - description: str                     (starting an item)
    progress   - done, total, skipped, failed, copied, speed_bps, eta_seconds
    finished   - done, total, skipped, failed, copied [, aborted]
    relogin    - username: str   (session expired; GUI must re-login, then
                                  set worker.relogin_event)
    error      - message: str
    """

    kind: str
    data: dict = field(default_factory=dict)


class _SpeedMeter:
    """Rolling-window byte counter for live speed reporting."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def add(self, nbytes: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, nbytes))
            self._trim(now)

    def bytes_per_second(self) -> float:
        now = time.monotonic()
        with self._lock:
            self._trim(now)
            if not self._samples:
                return 0.0
            total = sum(n for _, n in self._samples)
            span = max(now - self._samples[0][0], 0.5)
            return total / span

    def _trim(self, now: float) -> None:
        while self._samples and now - self._samples[0][0] > SPEED_WINDOW_SECONDS:
            self._samples.popleft()


class DownloadWorker(threading.Thread):
    """Downloads the selected collections into the output folder."""

    # Successive pauses when Instagram rate-limits API calls (seconds).
    RATE_LIMIT_WAITS = (120, 300, 600, 900)
    FAILED_REPORT_NAME = "failed_items.txt"

    def __init__(
        self,
        ig: InstagramClient,
        output_dir: Path,
        selected_collections: list,
        include_uncategorized: bool,
        events: "Queue[Event]",
        stop_event: threading.Event,
        account_cache: AccountCache | None = None,
        only_new: bool = True,
        concurrency: int = 1,
        write_sidecars: bool = False,
    ) -> None:
        super().__init__(daemon=True, name="instastash-download")
        self.ig = ig
        self.output_dir = Path(output_dir)
        self.selected = selected_collections
        self.include_uncategorized = include_uncategorized
        self.events = events
        self.stop_event = stop_event
        self.cache = account_cache
        self.only_new = only_new and account_cache is not None
        self.concurrency = max(1, min(int(concurrency), 3))
        self.write_sidecars = write_sidecars

        # Set by the GUI after a successful mid-run re-login.
        self.relogin_event = threading.Event()

        self.state = DownloadState(self.output_dir)
        self.speed = _SpeedMeter()
        self.http = requests.Session()
        self.item_durations: deque[float] = deque(maxlen=25)

        self.done = 0
        self.skipped = 0
        self.failed = 0
        self.copied = 0
        self.total = 0
        self.failures: list[dict] = []
        self._count_lock = threading.Lock()
        self._last_progress_emit = 0.0
        # media pk -> folder it was downloaded to earlier in this run, so a
        # post saved in several collections is fetched once and copied.
        self._run_locations: dict[str, Path] = {}

    # ----------------------------------------------------------------- events

    def _emit(self, kind: str, **data) -> None:
        self.events.put(Event(kind, data))

    def _log(self, message: str) -> None:
        self._emit("log", message=message)

    def _emit_progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress_emit < 0.25:
            return
        self._last_progress_emit = now

        remaining = max(self.total - self.done - self.skipped - self.failed, 0)
        if self.item_durations:
            avg = sum(self.item_durations) / len(self.item_durations)
            eta = remaining * avg
        else:
            eta = None
        self._emit(
            "progress",
            done=self.done,
            total=self.total,
            skipped=self.skipped,
            failed=self.failed,
            copied=self.copied,
            speed_bps=self.speed.bytes_per_second(),
            eta_seconds=eta,
        )

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise DownloadAborted()

    # ------------------------------------------------------- API resilience

    def _api_call(self, func, *args, **kwargs):
        """Call an Instagram API function, absorbing the two recoverable
        failure modes: rate limits (pause with a countdown, then retry) and
        expired sessions (ask the GUI for a re-login, then retry)."""
        rate_limit_hits = 0
        relogins = 0
        while True:
            self._check_stop()
            try:
                return func(*args, **kwargs)
            except (PleaseWaitFewMinutes, ClientThrottledError):
                if rate_limit_hits >= len(self.RATE_LIMIT_WAITS):
                    raise
                wait = self.RATE_LIMIT_WAITS[rate_limit_hits]
                rate_limit_hits += 1
                self._log(
                    "Instagram rate limit hit — pausing for "
                    f"{max(int(wait) // 60, 1)} minute(s), then continuing "
                    "automatically."
                )
                self._wait_countdown(wait, "Rate limited")
                self._log("Rate-limit pause over — resuming.")
            except LoginRequired:
                relogins += 1
                if relogins > 2:
                    raise
                self._request_relogin()

    def _wait_countdown(self, seconds: float, reason: str) -> None:
        """Sleep with a live countdown in the UI; Stop interrupts instantly."""
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            minutes, secs = divmod(int(remaining) + 1, 60)
            self._emit(
                "item", description=f"{reason} — resuming in {minutes}:{secs:02d}"
            )
            if self.stop_event.wait(min(1.0, remaining)):
                raise DownloadAborted()

    def _request_relogin(self) -> None:
        """Block until the GUI re-authenticates the session (or Stop)."""
        self._log("Instagram session expired — log in again to continue.")
        self.relogin_event.clear()
        self._emit("relogin", username=self.ig.client.username)
        self._emit("item", description="Waiting for re-login...")
        while not self.relogin_event.wait(0.5):
            self._check_stop()
        self._log("Re-login successful — continuing where we left off.")

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        try:
            plan = self._build_plan()
            self.total = sum(len(entry["medias"]) for entry in plan)
            if self.only_new:
                self._log(
                    f"Ready: {self.total} new item(s) across {len(plan)} "
                    "folder(s) (previously downloaded items are skipped)."
                )
            else:
                self._log(
                    f"Ready: {self.total} items across {len(plan)} folder(s)."
                )
            self._emit_progress(force=True)

            for index, entry in enumerate(plan, start=1):
                self._check_stop()
                self._emit(
                    "collection", name=entry["folder"], index=index,
                    count=len(plan),
                )
                self._download_collection(entry)

            self._emit_progress(force=True)
            self._write_failure_report(completed=True)
            self._emit(
                "finished",
                done=self.done,
                total=self.total,
                skipped=self.skipped,
                failed=self.failed,
                copied=self.copied,
            )
        except DownloadAborted:
            self._log("Stopped by user. Progress was saved — restart to resume.")
            self._write_failure_report(completed=False)
            self._emit(
                "finished",
                done=self.done,
                total=self.total,
                skipped=self.skipped,
                failed=self.failed,
                copied=self.copied,
                aborted=True,
            )
        except Exception as exc:  # noqa: BLE001 - report anything to the UI
            self._write_failure_report(completed=False)
            self._emit(
                "error",
                message=f"{type(exc).__name__}: {exc}. "
                "Progress was saved — restart the download to resume.",
            )

    def _write_failure_report(self, completed: bool) -> None:
        """Persist failed items to failed_items.txt in the output folder, or
        remove a stale report after a fully clean run."""
        report = self.output_dir / self.FAILED_REPORT_NAME
        try:
            if self.failures:
                lines = [
                    f"InstaStash — items that failed on "
                    f"{time.strftime('%Y-%m-%d %H:%M')}",
                    "Run the download again to retry them automatically.",
                    "",
                ]
                for failure in self.failures:
                    url = (
                        f"https://www.instagram.com/p/{failure['code']}/"
                        if failure["code"] else "(no link)"
                    )
                    lines.append(
                        f"[{failure['folder']}] {failure['basename']} — "
                        f"{url} — {failure['error']}"
                    )
                report.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self._log(
                    f"Wrote {self.FAILED_REPORT_NAME} with "
                    f"{len(self.failures)} failed item(s)."
                )
            elif completed:
                report.unlink(missing_ok=True)
        except OSError as exc:
            self._log(f"Could not write {self.FAILED_REPORT_NAME}: {exc}")

    # ------------------------------------------------------------------ plan

    def _fetch_new(self, collection_id: str, name: str) -> tuple[list, int]:
        """Fetch a collection's items, incrementally when the cache allows.

        Returns (medias newest-first, newest_pk_candidate). The candidate is
        the pk of the newest item Instagram reported; committing it to the
        cache (only after a clean download run) lets the next fetch stop at
        this point instead of re-listing everything.
        """
        last_pk = self.cache.newest_pk(collection_id) if self.only_new else 0
        if collection_id == UNCATEGORIZED_ID:
            medias = self._api_call(self.ig.all_saved_medias, last_media_pk=last_pk)
        else:
            medias = self._api_call(
                self.ig.collection_medias, collection_id, last_media_pk=last_pk
            )
        newest_candidate = int(medias[0].pk) if medias else last_pk
        if self.only_new:
            medias = [
                m for m in medias
                if not self.cache.is_downloaded(collection_id, m.pk)
            ]
        return medias, newest_candidate

    def _build_plan(self) -> list[dict]:
        """Resolve which medias go into which folder.

        Returns a list of {"id", "name", "folder", "medias", "newest"} dicts.
        """
        plan: list[dict] = []
        used_names: set[str] = set()
        assigned_pks: set = set()

        for collection in self.selected:
            self._check_stop()
            self._log(f'Fetching item list for "{collection.name}"...')
            medias, newest = self._fetch_new(collection.id, collection.name)
            folder = self._unique_folder_name(collection.name, used_names)
            plan.append({
                "id": collection.id, "name": collection.name,
                "folder": folder, "medias": medias, "newest": newest,
            })
            assigned_pks.update(m.pk for m in medias)
            # Items downloaded in earlier runs still belong to this
            # collection — they must not leak into "Uncategorized".
            if self.cache:
                assigned_pks.update(self.cache.downloaded_pks(collection.id))

        if self.include_uncategorized:
            self._check_stop()
            # Items in *unselected* named collections are categorized too.
            # Those need a full listing: we have no download memory that
            # could stand in for their membership.
            selected_ids = {c.id for c in self.selected}
            for collection in self._api_call(self.ig.named_collections):
                if collection.id in selected_ids:
                    continue
                self._check_stop()
                self._log(
                    f'Indexing "{collection.name}" (to keep "Uncategorized" clean)...'
                )
                assigned_pks.update(
                    m.pk
                    for m in self._api_call(
                        self.ig.collection_medias, collection.id
                    )
                )

            self._log("Fetching the saved list...")
            all_saved, newest = self._fetch_new(UNCATEGORIZED_ID, UNCATEGORIZED_FOLDER)
            leftovers = [m for m in all_saved if m.pk not in assigned_pks]
            folder = self._unique_folder_name(UNCATEGORIZED_FOLDER, used_names)
            plan.append({
                "id": UNCATEGORIZED_ID, "name": UNCATEGORIZED_FOLDER,
                "folder": folder, "medias": leftovers, "newest": newest,
            })
            if not leftovers:
                self._log("No new uncategorized saved items found.")

        return plan

    @staticmethod
    def _unique_folder_name(raw_name: str, used: set[str]) -> str:
        base = sanitize_name(raw_name, fallback="Collection")
        name, n = base, 2
        while name.lower() in used:
            name = f"{base} ({n})"
            n += 1
        used.add(name.lower())
        return name

    # ------------------------------------------------------------- downloads

    def _download_collection(self, entry: dict) -> None:
        folder_name, medias = entry["folder"], entry["medias"]
        folder = self.output_dir / folder_name
        if medias:
            folder.mkdir(parents=True, exist_ok=True)

            # Leftover temp files from a killed process are never valid.
            for stale in folder.glob("*.part"):
                stale.unlink(missing_ok=True)

        failures_before = self.failed
        if self.concurrency <= 1 or len(medias) <= 1:
            for media in medias:
                self._check_stop()
                self._process_item(entry, folder, media)
        else:
            with ThreadPoolExecutor(
                max_workers=self.concurrency, thread_name_prefix="instastash-dl"
            ) as pool:
                futures = [
                    pool.submit(self._process_item, entry, folder, media)
                    for media in medias
                ]
                try:
                    for future in as_completed(futures):
                        future.result()
                except DownloadAborted:
                    for future in futures:
                        future.cancel()
                    raise

        # Advance the incremental-fetch marker only after a clean pass:
        # a failure or an abort must leave the marker behind, so the next
        # run sees (and retries) everything from this point again.
        if self.cache and self.failed == failures_before:
            self.cache.advance_newest(entry["id"], entry["name"], entry["newest"])

    def _process_item(self, entry: dict, folder: Path, media) -> None:
        """Fetch one post (thread-safe; may run in a worker pool)."""
        self._check_stop()
        folder_name = entry["folder"]
        key = DownloadState.key(folder_name, media.pk)
        if self.state.is_done(key):
            with self._count_lock:
                self.skipped += 1
            if self.cache:
                self.cache.mark_downloaded(entry["id"], entry["name"], media.pk)
            self._emit_progress()
            return

        basename = media_basename(media)
        self._emit("item", description=f"{folder_name} / {basename}")
        started = time.monotonic()
        try:
            fully_copied = self._download_media(media, folder, basename)
            self.state.mark_done(key)
            if self.cache:
                self.cache.mark_downloaded(entry["id"], entry["name"], media.pk)
            with self._count_lock:
                self.done += 1
                if fully_copied:
                    self.copied += 1
            if fully_copied:
                self._log(
                    f'Copied "{basename}" from another collection folder '
                    "(no re-download needed)."
                )
            else:
                # Local copies are near-instant; only real downloads
                # should inform the ETA.
                self.item_durations.append(time.monotonic() - started)
        except DownloadAborted:
            raise
        except Exception as exc:  # noqa: BLE001 - keep going on bad items
            with self._count_lock:
                self.failed += 1
                self.failures.append({
                    "folder": folder_name,
                    "basename": basename,
                    "code": getattr(media, "code", "") or str(media.pk),
                    "error": f"{type(exc).__name__}: {exc}",
                })
            self._log(f"FAILED {basename}: {type(exc).__name__}: {exc}")
        self._emit_progress()

    def _download_media(self, media, folder: Path, basename: str) -> bool:
        """Get every file belonging to one post into `folder` (1 file for a
        photo/video, N for a carousel, named basename_1..N).

        A post saved in several collections is fetched from Instagram only
        once: if another collection folder already holds the same files
        (from this run or an earlier one), they are copied locally instead
        of re-downloaded. Returns True when every needed file came from a
        local copy.
        """
        parts = self._media_parts(media)
        if not parts:
            raise ValueError("No downloadable URL found (post may be unavailable)")

        timestamp = media.taken_at.timestamp() if media.taken_at else None
        if len(parts) == 1:
            url, media_type = parts[0]
            filenames = [f"{basename}{extension_from_url(url, media_type)}"]
        else:
            filenames = [
                f"{basename}_{i}{extension_from_url(url, media_type)}"
                for i, (url, media_type) in enumerate(parts, start=1)
            ]

        source_folder = self._find_local_copy(media.pk, exclude=folder)
        copied_any = False
        downloaded_any = False
        for (url, _media_type), filename in zip(parts, filenames):
            self._check_stop()
            dest = folder / filename
            if dest.exists() and dest.stat().st_size > 0:
                continue
            if source_folder is not None:
                source = source_folder / filename
                if source.is_file() and source.stat().st_size > 0:
                    shutil.copy2(source, dest)  # copy2 keeps the post date
                    copied_any = True
                    continue
            self._download_file(url, dest, timestamp)
            downloaded_any = True

        if self.write_sidecars:
            self._write_sidecar(media, folder, basename)
        self._run_locations[str(media.pk)] = folder
        return copied_any and not downloaded_any

    def _write_sidecar(self, media, folder: Path, basename: str) -> None:
        """Save the post's link, author, date and caption as <basename>.txt."""
        path = folder / f"{basename}.txt"
        if path.exists():
            return
        code = getattr(media, "code", "") or ""
        url = f"https://www.instagram.com/p/{code}/" if code else "(no link)"
        user = getattr(media, "user", None)
        author = user.username if user and getattr(user, "username", "") else "unknown"
        taken_at = getattr(media, "taken_at", None)
        posted = taken_at.strftime("%Y-%m-%d %H:%M") if taken_at else "unknown"
        caption = (getattr(media, "caption_text", "") or "").strip()

        text = f"{url}\nAuthor: @{author}\nPosted: {posted}\n"
        if caption:
            text += f"\n{caption}\n"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            # A missing caption file must never fail the item itself.
            self._log(f"Could not write caption file {path.name}: {exc}")

    def _find_local_copy(self, media_pk, exclude: Path) -> Path | None:
        """Another folder in this output dir that already holds this post."""
        folder = self._run_locations.get(str(media_pk))
        if folder is not None and folder != exclude and folder.is_dir():
            return folder
        for folder_name in self.state.folders_done_for(media_pk):
            candidate = self.output_dir / folder_name
            if candidate != exclude and candidate.is_dir():
                return candidate
        return None

    @staticmethod
    def _media_parts(media) -> list[tuple[str, int]]:
        """(url, media_type) for every file in a post, at source quality."""
        parts: list[tuple[str, int]] = []
        if media.media_type == 8:  # carousel / album
            for resource in media.resources:
                if resource.media_type == 2 and resource.video_url:
                    parts.append((str(resource.video_url), 2))
                elif resource.thumbnail_url:
                    parts.append((str(resource.thumbnail_url), 1))
        elif media.media_type == 2 and media.video_url:  # video / reel / IGTV
            parts.append((str(media.video_url), 2))
        elif media.thumbnail_url:  # photo
            parts.append((str(media.thumbnail_url), 1))
        return parts

    def _download_file(self, url: str, dest: Path, timestamp: float | None) -> None:
        if dest.exists() and dest.stat().st_size > 0:
            return  # already on disk from a previous run

        tmp = dest.with_name(dest.name + ".part")
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._check_stop()
            try:
                with self.http.get(url, stream=True, timeout=(10, 60)) as response:
                    response.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in response.iter_content(CHUNK_SIZE):
                            self._check_stop()
                            f.write(chunk)
                            self.speed.add(len(chunk))
                            self._emit_progress()
                os.replace(tmp, dest)
                if timestamp:
                    os.utime(dest, (timestamp, timestamp))
                return
            except DownloadAborted:
                tmp.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001 - retry network hiccups
                tmp.unlink(missing_ok=True)
                last_error = exc
                if attempt < MAX_RETRIES:
                    # Backoff that still reacts to the Stop button instantly.
                    if self.stop_event.wait(1.5 * attempt):
                        raise DownloadAborted()
        raise last_error  # type: ignore[misc]
