"""Filename and folder-name helpers.

Naming scheme for downloaded items:

    <YYYY-MM-DD>_<author-username>_<shortcode>.<ext>          (single photo/video)
    <YYYY-MM-DD>_<author-username>_<shortcode>_1.<ext>        (carousel item 1)
    <YYYY-MM-DD>_<author-username>_<shortcode>_2.<ext>        (carousel item 2)
    ...

The date makes folders sort chronologically, the author username tells you
whose post it is at a glance, and the shortcode is Instagram's own unique
post id (the one you see in the post URL), which guarantees uniqueness.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlparse

# Characters that are illegal on Windows (superset of macOS/Linux restrictions).
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved device names on Windows.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_name(name: str, fallback: str = "untitled") -> str:
    """Make a string safe to use as a file or folder name on macOS and Windows."""
    name = _ILLEGAL_CHARS.sub("_", name).strip().rstrip(". ")
    if not name:
        return fallback
    if name.upper() in _RESERVED_NAMES:
        name = f"_{name}"
    # Keep names comfortably below filesystem limits.
    return name[:120]


def media_basename(media) -> str:
    """Base filename (no extension, no carousel index) for an instagrapi Media."""
    date = media.taken_at.strftime("%Y-%m-%d") if media.taken_at else "unknown-date"
    username = media.user.username if media.user and media.user.username else "unknown"
    code = media.code or str(media.pk)
    return sanitize_name(f"{date}_{username}_{code}")


def extension_from_url(url: str, media_type: int) -> str:
    """File extension from a CDN URL, falling back to the media type."""
    suffix = PurePosixPath(urlparse(str(url)).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".heic"}:
        return suffix
    return ".mp4" if media_type == 2 else ".jpg"
