"""Hand-drawn widgets that give the Tkinter UI its Instagram look:

- GradientBanner   : header bar painted with the Instagram logo gradient
- RoundedButton    : flat, rounded, hoverable button (primary / secondary / danger)
- PlaceholderEntry : Instagram-style input with in-field placeholder text
- IGCheckbutton    : checkbox drawn as a rounded Instagram-blue check
- Card             : white panel with a hairline border and optional title
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from . import theme


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return "#{:02x}{:02x}{:02x}".format(
        round(r1 + (r2 - r1) * t),
        round(g1 + (g2 - g1) * t),
        round(b1 + (b2 - b1) * t),
    )


def gradient_color(t: float, stops: list[str] | None = None) -> str:
    """Color at position t (0..1) along the Instagram gradient."""
    stops = stops or theme.GRADIENT
    t = min(max(t, 0.0), 1.0)
    segments = len(stops) - 1
    position = t * segments
    index = min(int(position), segments - 1)
    return _lerp_color(stops[index], stops[index + 1], position - index)


class GradientBanner(tk.Canvas):
    """Top banner painted with the Instagram gradient and the app wordmark."""

    def __init__(self, parent, height: int = 62, title: str = "InstaStash"):
        super().__init__(parent, height=height, highlightthickness=0, bd=0)
        self._title = title
        self._right_text = ""
        self._height = height
        self._drawn_width = -1
        self.bind("<Configure>", self._on_configure)

    def set_right(self, text: str) -> None:
        self._right_text = text
        self._drawn_width = -1
        self._redraw()

    def _on_configure(self, _event=None) -> None:
        self._redraw()

    def _redraw(self) -> None:
        width = self.winfo_width()
        if width <= 1 or width == self._drawn_width:
            return
        self._drawn_width = width
        self.delete("all")
        step = 2
        for x in range(0, width + step, step):
            self.create_rectangle(
                x, 0, x + step, self._height,
                fill=gradient_color(x / max(width, 1)), width=0,
            )
        self.create_text(
            20, self._height // 2, anchor="w", text=self._title,
            fill="#FFFFFF", font=theme.logo_font(26),
        )
        if self._right_text:
            self.create_text(
                width - 20, self._height // 2, anchor="e",
                text=self._right_text, fill="#FFFFFF",
                font=(theme.FAMILY, 12, "bold"),
            )


_PALETTES = {
    "primary": dict(
        fill=theme.BLUE, hover=theme.BLUE_HOVER, disabled=theme.BLUE_DISABLED,
        fg="#FFFFFF", fg_disabled="#FFFFFF", outline="",
    ),
    "secondary": dict(
        fill=theme.CARD, hover="#F5F5F5", disabled=theme.CARD,
        fg=theme.TEXT, fg_disabled="#C7C7C7", outline=theme.BORDER,
    ),
    "danger": dict(
        fill=theme.CARD, hover="#FFF0F1", disabled=theme.CARD,
        fg=theme.RED, fg_disabled="#C7C7C7", outline=theme.BORDER,
    ),
}


class RoundedButton(tk.Canvas):
    """Flat rounded button with hover + disabled states."""

    def __init__(
        self, parent, text: str, command=None, kind: str = "primary",
        width: int = 150, height: int = 36, radius: int = 9, font_size: int = 12,
    ):
        bg = parent.cget("bg") if "bg" in parent.keys() else theme.BG
        super().__init__(
            parent, width=width, height=height,
            highlightthickness=0, bd=0, bg=bg,
        )
        self._text = text
        self._command = command
        self._palette = _PALETTES[kind]
        self._radius = radius
        self._font = (theme.FAMILY, font_size, "bold")
        self._state = "normal"
        self._hover = False

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    # Accept .configure(state=..., text=...) like a normal Tk button.
    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs.update(cnf)
        state = kwargs.pop("state", None)
        text = kwargs.pop("text", None)
        if kwargs:
            super().configure(**kwargs)
        if state is not None:
            self._state = str(state)
        if text is not None:
            self._text = text
        if state is not None or text is not None:
            self._draw()

    config = configure

    def _rounded_points(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _draw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), int(self["width"]))
        h = max(self.winfo_height(), int(self["height"]))
        p = self._palette
        if self._state == "disabled":
            fill, fg = p["disabled"], p["fg_disabled"]
        elif self._hover:
            fill, fg = p["hover"], p["fg"]
        else:
            fill, fg = p["fill"], p["fg"]
        self.create_polygon(
            self._rounded_points(1, 1, w - 2, h - 2, self._radius),
            smooth=True, fill=fill,
            outline=p["outline"] or fill, width=1,
        )
        self.create_text(w // 2, h // 2, text=self._text, fill=fg,
                         font=self._font)
        cursor = "hand2" if self._state == "normal" else "arrow"
        tk.Canvas.configure(self, cursor=cursor)

    def _on_click(self, _event) -> None:
        if self._state == "normal" and self._command:
            self._command()

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._draw()


class PlaceholderEntry(tk.Entry):
    """Instagram-style input: light field, hairline border, gray placeholder."""

    def __init__(self, parent, placeholder: str, show: str = "", width: int = 28):
        super().__init__(
            parent, width=width, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER_FOCUS,
            bg=theme.FIELD_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
            font=(theme.FAMILY, 12),
        )
        self._placeholder = placeholder
        self._show = show
        self._showing_placeholder = False
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._put_placeholder()

    def _put_placeholder(self) -> None:
        if not self.get():
            self._showing_placeholder = True
            tk.Entry.configure(self, show="", fg=theme.SUBTEXT)
            self.insert(0, self._placeholder)

    def _on_focus_in(self, _event) -> None:
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.delete(0, "end")
            tk.Entry.configure(self, show=self._show, fg=theme.TEXT)

    def _on_focus_out(self, _event) -> None:
        self._put_placeholder()

    def value(self) -> str:
        return "" if self._showing_placeholder else self.get()


# Keep PhotoImage references alive for the lifetime of the app (Tk drops
# images that get garbage-collected).
_checkbox_cache: dict[str, ImageTk.PhotoImage] = {}


def _checkbox_images() -> tuple[ImageTk.PhotoImage, ImageTk.PhotoImage]:
    """(unchecked, checked) images: rounded box / Instagram-blue check."""
    if "off" not in _checkbox_cache:
        scale = 3  # draw large, downscale for crisp edges on retina displays
        size = 18 * scale

        off = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(off)
        draw.rounded_rectangle(
            (scale, scale, size - scale, size - scale),
            radius=5 * scale, fill="#FFFFFF",
            outline="#B8B8B8", width=scale + 1,
        )

        on = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(on)
        draw.rounded_rectangle(
            (scale, scale, size - scale, size - scale),
            radius=5 * scale, fill=theme.BLUE,
        )
        draw.line(
            [(size * 0.28, size * 0.53), (size * 0.44, size * 0.69),
             (size * 0.73, size * 0.33)],
            fill="#FFFFFF", width=2 * scale, joint="curve",
        )

        _checkbox_cache["off"] = ImageTk.PhotoImage(
            off.resize((18, 18), Image.LANCZOS)
        )
        _checkbox_cache["on"] = ImageTk.PhotoImage(
            on.resize((18, 18), Image.LANCZOS)
        )
    return _checkbox_cache["off"], _checkbox_cache["on"]


class IGCheckbutton(tk.Frame):
    """Checkbox with an Instagram-blue rounded check indicator.

    Fully custom (image label + text label) because native macOS Tk ignores
    background/relief styling on real Checkbuttons.
    """

    def __init__(self, parent, text: str, variable: tk.BooleanVar,
                 bg: str = theme.CARD):
        super().__init__(parent, bg=bg, cursor="hand2")
        self._var = variable
        self._off_img, self._on_img = _checkbox_images()

        self._icon = tk.Label(self, image=self._off_img, bg=bg, bd=0)
        self._icon.pack(side="left", pady=3)
        self._label = tk.Label(
            self, text=text, bg=bg, fg=theme.TEXT, font=(theme.FAMILY, 12)
        )
        self._label.pack(side="left", padx=(7, 0))

        for widget in (self, self._icon, self._label):
            widget.bind("<Button-1>", self._toggle)
        self._trace = variable.trace_add("write", lambda *_a: self._sync())
        self.bind("<Destroy>", self._remove_trace)
        self._sync()

    def _toggle(self, _event) -> None:
        self._var.set(not self._var.get())

    def _sync(self) -> None:
        if self._icon.winfo_exists():
            self._icon.configure(
                image=self._on_img if self._var.get() else self._off_img
            )

    def _remove_trace(self, _event) -> None:
        try:
            self._var.trace_remove("write", self._trace)
        except Exception:  # noqa: BLE001 - var may already be gone at teardown
            pass


class Card(tk.Frame):
    """White panel with a hairline border; optional bold title row."""

    def __init__(self, parent, title: str | None = None, padding: int = 14):
        super().__init__(
            parent, bg=theme.CARD, bd=0,
            highlightthickness=1, highlightbackground=theme.BORDER,
        )
        self.inner = tk.Frame(self, bg=theme.CARD)
        self.inner.pack(fill="both", expand=True, padx=padding, pady=padding)
        self.header = None
        if title:
            self.header = tk.Frame(self.inner, bg=theme.CARD)
            self.header.pack(fill="x", pady=(0, 8))
            tk.Label(
                self.header, text=title, bg=theme.CARD, fg=theme.TEXT,
                font=(theme.FAMILY, 13, "bold"),
            ).pack(side="left")
