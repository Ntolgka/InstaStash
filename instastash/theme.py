"""InstaStash visual theme — Instagram-inspired palette, fonts and ttk styles.

Palette follows Instagram's design language: near-white page background,
white cards with hairline borders, the classic blue action color, and the
logo gradient for accents.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ------------------------------------------------------------------ palette

BG = "#FAFAFA"          # page background
CARD = "#FFFFFF"        # card surface
BORDER = "#DBDBDB"      # hairline borders
BORDER_FOCUS = "#A8A8A8"
TEXT = "#262626"        # primary text
SUBTEXT = "#8E8E8E"     # secondary text
FIELD_BG = "#FAFAFA"    # input background

BLUE = "#0095F6"        # primary action
BLUE_HOVER = "#1877F2"
BLUE_DISABLED = "#B2DFFC"
RED = "#ED4956"         # errors / destructive
GREEN = "#1FA855"       # success

# Instagram logo gradient, left to right.
GRADIENT = ["#FEDA75", "#FA7E1E", "#D62976", "#962FBF", "#4F5BD5"]

# Filled in by init_style().
FAMILY = "Helvetica"
MONO = "Courier"
SCRIPT = None  # script-style family for the logo, if one exists


def _first_available(candidates: tuple[str, ...], available: set[str]) -> str | None:
    for family in candidates:
        if family in available:
            return family
    return None


def init_style(root: tk.Tk) -> None:
    """Pick platform fonts and configure every ttk style the app uses."""
    global FAMILY, MONO, SCRIPT

    available = set(tkfont.families(root))
    FAMILY = _first_available(
        ("SF Pro Text", "Helvetica Neue", "Segoe UI Variable", "Segoe UI",
         "Helvetica", "Arial"),
        available,
    ) or "TkDefaultFont"
    MONO = _first_available(
        ("SF Mono", "Menlo", "Consolas", "Courier New"), available
    ) or "TkFixedFont"
    SCRIPT = _first_available(
        ("Snell Roundhand", "Segoe Script", "Brush Script MT"), available
    )

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", font=(FAMILY, 11), background=BG, foreground=TEXT)

    # Progress bar in Instagram magenta.
    style.configure(
        "IG.Horizontal.TProgressbar",
        troughcolor="#EFEFEF", bordercolor="#EFEFEF",
        background="#D62976", lightcolor="#D62976", darkcolor="#D62976",
        thickness=8,
    )

    # Slim, quiet scrollbar.
    style.configure(
        "Card.Vertical.TScrollbar",
        background="#C7C7C7", troughcolor=CARD, bordercolor=CARD,
        arrowcolor=SUBTEXT, relief="flat",
    )


def logo_font(size: int) -> tuple:
    """Instagram-style script font for the wordmark, with a graceful fallback."""
    if SCRIPT:
        return (SCRIPT, size, "bold")
    return (FAMILY, size, "bold", "italic")
