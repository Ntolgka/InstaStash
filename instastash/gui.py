"""Tkinter GUI for InstaStash — styled after Instagram's design language.

Two screens under a persistent gradient banner:
  1. Login  - centered card with username / password / optional 2FA code
  2. Main   - collection picker, output folder, live progress panel

All Instagram/network work happens on background threads; the GUI thread only
polls an event queue, so the window never freezes.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme
from .cache import AccountCache
from .client import InstagramClient, LoginError
from .paths import data_dir
from .downloader import DownloadWorker, Event
from .widgets import (
    Card, GradientBanner, IGCheckbutton, PlaceholderEntry, RoundedButton,
    Segmented,
)

APP_TITLE = "InstaStash"
SESSION_FILE = data_dir() / "session.json"


def _format_bytes_per_second(bps: float) -> str:
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def _format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "estimating..."
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("660x820")
        self.minsize(620, 740)
        self.configure(bg=theme.BG)
        theme.init_style(self)

        self.ig: InstagramClient | None = None
        self.collections: list = []
        self.worker: DownloadWorker | None = None
        self.stop_event = threading.Event()
        self.events: "queue.Queue[Event]" = queue.Queue()

        self.banner = GradientBanner(self, title=APP_TITLE)
        self.banner.pack(fill="x")

        self._container = tk.Frame(self, bg=theme.BG)
        self._container.pack(fill="both", expand=True, padx=16, pady=14)

        self._build_login_screen()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ login view

    def _clear_container(self) -> None:
        for child in self._container.winfo_children():
            child.destroy()

    def _build_login_screen(self) -> None:
        self._clear_container()

        card = Card(self._container, padding=32)
        card.place(relx=0.5, rely=0.42, anchor="center")

        inner = card.inner
        tk.Label(
            inner, text="InstaStash", bg=theme.CARD, fg=theme.TEXT,
            font=theme.logo_font(30),
        ).pack(pady=(0, 2))
        tk.Label(
            inner,
            text="Back up your saved posts, organized by collection.",
            bg=theme.CARD, fg=theme.SUBTEXT, font=(theme.FAMILY, 11),
        ).pack(pady=(0, 18))

        self.username_entry = PlaceholderEntry(inner, "Username")
        self.password_entry = PlaceholderEntry(inner, "Password", show="•")
        self.twofa_entry = PlaceholderEntry(inner, "2FA code (optional)")
        for entry in (self.username_entry, self.password_entry, self.twofa_entry):
            entry.pack(fill="x", ipady=7, pady=4)

        self.login_button = RoundedButton(
            inner, "Log in", command=self._on_login, width=280, height=38,
        )
        self.login_button.pack(pady=(14, 8))

        self.login_status = tk.Label(
            inner, text="", bg=theme.CARD, fg=theme.RED,
            font=(theme.FAMILY, 10), wraplength=280, justify="center",
        )
        self.login_status.pack()

        footer = tk.Label(
            self._container,
            text="Your credentials go directly to Instagram.\n"
                 "The login session is stored only on this device.",
            bg=theme.BG, fg=theme.SUBTEXT, font=(theme.FAMILY, 9),
            justify="center",
        )
        footer.place(relx=0.5, rely=0.93, anchor="center")

        self.bind("<Return>", lambda _e: self._on_login())

    def _on_login(self) -> None:
        username = self.username_entry.value().strip()
        password = self.password_entry.value()
        twofa = self.twofa_entry.value().strip()
        if not username or not password:
            self.login_status.configure(
                text="Please enter username and password.", fg=theme.RED
            )
            return

        self.login_button.configure(state="disabled")
        self.login_status.configure(text="Logging in...", fg=theme.SUBTEXT)

        def work() -> None:
            try:
                ig = InstagramClient(SESSION_FILE)
                ig.login(username, password, twofa)
                collections = ig.named_collections()
                self.events.put(
                    Event("login_ok", {"ig": ig, "collections": collections})
                )
            except LoginError as exc:
                self.events.put(Event("login_fail", {"message": str(exc)}))
            except Exception as exc:  # noqa: BLE001
                self.events.put(
                    Event("login_fail", {"message": f"Unexpected error: {exc}"})
                )

        threading.Thread(target=work, daemon=True, name="instastash-login").start()

    # ------------------------------------------------------------- main view

    def _build_main_screen(self) -> None:
        self._clear_container()
        self.unbind("<Return>")
        self.banner.set_right(f"@{self.ig.client.username}")
        self.account_cache = AccountCache(self.ig.client.username)
        frame = self._container

        # --- collections ---------------------------------------------------
        col_card = Card(frame, title="Saved collections")
        col_card.pack(fill="both", expand=True)

        for label_text, select_value in (("Select all", True), ("Select none", False)):
            link = tk.Label(
                col_card.header, text=label_text, bg=theme.CARD, fg=theme.BLUE,
                font=(theme.FAMILY, 10, "bold"), cursor="hand2",
            )
            link.pack(side="right", padx=(12, 0))
            link.bind(
                "<Button-1>",
                lambda _e, v=select_value: [var.set(v) for var in self.collection_vars],
            )

        list_holder = tk.Frame(col_card.inner, bg=theme.CARD)
        list_holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            list_holder, highlightthickness=0, height=150, bg=theme.CARD, bd=0
        )
        scrollbar = ttk.Scrollbar(
            list_holder, orient="vertical", command=canvas.yview,
            style="Card.Vertical.TScrollbar",
        )
        inner = tk.Frame(canvas, bg=theme.CARD)
        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event: tk.Event) -> None:
            if canvas.winfo_exists():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        # bind_all so scrolling works with the cursor over the checkboxes too.
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self.collection_vars: list[tk.BooleanVar] = []
        if not self.collections:
            tk.Label(
                inner, text="No named collections found.", bg=theme.CARD,
                fg=theme.SUBTEXT, font=(theme.FAMILY, 11),
            ).pack(anchor="w")
        for collection in self.collections:
            var = tk.BooleanVar(value=True)
            self.collection_vars.append(var)
            row = tk.Frame(inner, bg=theme.CARD)
            row.pack(fill="x", anchor="w")
            IGCheckbutton(row, collection.name, var).pack(side="left")
            tk.Label(
                row, text=f"{collection.media_count} items", bg=theme.CARD,
                fg=theme.SUBTEXT, font=(theme.FAMILY, 10),
            ).pack(side="left", padx=(6, 0))

        tk.Frame(col_card.inner, bg=theme.BORDER, height=1).pack(
            fill="x", pady=(8, 8)
        )
        self.uncategorized_var = tk.BooleanVar(value=True)
        IGCheckbutton(
            col_card.inner,
            'Also download saves that are in no collection ("Uncategorized")',
            self.uncategorized_var,
        ).pack(anchor="w")

        # --- output folder ---------------------------------------------------
        out_card = Card(frame, title="Save to")
        out_card.pack(fill="x", pady=(12, 0))
        out_row = tk.Frame(out_card.inner, bg=theme.CARD)
        out_row.pack(fill="x")
        default_out = Path.home() / "Downloads" / "InstaStash"
        self.output_var = tk.StringVar(value=str(default_out))
        out_entry = tk.Entry(
            out_row, textvariable=self.output_var, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER_FOCUS, bg=theme.FIELD_BG,
            fg=theme.TEXT, insertbackground=theme.TEXT, font=(theme.FAMILY, 11),
        )
        out_entry.pack(side="left", fill="x", expand=True, ipady=6)
        RoundedButton(
            out_row, "Browse...", command=self._pick_folder, kind="secondary",
            width=96, height=32, font_size=11,
        ).pack(side="left", padx=(10, 0))

        options_row = tk.Frame(out_card.inner, bg=theme.CARD)
        options_row.pack(fill="x", pady=(8, 0))
        self.only_new_var = tk.BooleanVar(value=True)
        IGCheckbutton(
            options_row,
            "Only download new items (skips previous runs)",
            self.only_new_var,
        ).pack(side="left")
        reset_link = tk.Label(
            options_row, text="Reset download memory", bg=theme.CARD,
            fg=theme.BLUE, font=(theme.FAMILY, 10, "bold"), cursor="hand2",
        )
        reset_link.pack(side="right")
        reset_link.bind("<Button-1>", lambda _e: self._reset_cache())

        options_row2 = tk.Frame(out_card.inner, bg=theme.CARD)
        options_row2.pack(fill="x", pady=(6, 0))
        self.sidecars_var = tk.BooleanVar(value=False)
        IGCheckbutton(
            options_row2,
            "Save captions & post links as .txt files",
            self.sidecars_var,
        ).pack(side="left")
        self.concurrency_var = tk.IntVar(value=1)
        Segmented(options_row2, [1, 2, 3], self.concurrency_var).pack(
            side="right"
        )
        tk.Label(
            options_row2, text="Parallel downloads:", bg=theme.CARD,
            fg=theme.SUBTEXT, font=(theme.FAMILY, 11),
        ).pack(side="right", padx=(0, 6))

        # --- controls ----------------------------------------------------------
        controls = tk.Frame(frame, bg=theme.BG)
        controls.pack(fill="x", pady=(12, 0))
        self.start_button = RoundedButton(
            controls, "Start download", command=self._on_start,
            width=170, height=38,
        )
        self.start_button.pack(side="left")
        self.stop_button = RoundedButton(
            controls, "Stop", command=self._on_stop, kind="danger",
            width=90, height=38,
        )
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left", padx=(10, 0))

        # --- progress ----------------------------------------------------------
        progress_card = Card(frame, title="Progress")
        progress_card.pack(fill="both", expand=True, pady=(12, 0))
        inner_frame = progress_card.inner

        self.percent_label = tk.Label(
            progress_card.header, text="", bg=theme.CARD, fg=theme.TEXT,
            font=(theme.FAMILY, 13, "bold"),
        )
        self.percent_label.pack(side="right")

        self.progress_bar = ttk.Progressbar(
            inner_frame, maximum=100, style="IG.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill="x")

        self.overall_label = tk.Label(
            inner_frame, text="Ready when you are.", bg=theme.CARD,
            fg=theme.TEXT, font=(theme.FAMILY, 11), anchor="w",
        )
        self.overall_label.pack(fill="x", pady=(8, 0))
        self.collection_label = tk.Label(
            inner_frame, text="", bg=theme.CARD, fg=theme.TEXT,
            font=(theme.FAMILY, 11), anchor="w",
        )
        self.collection_label.pack(fill="x")
        self.item_label = tk.Label(
            inner_frame, text="", bg=theme.CARD, fg=theme.SUBTEXT,
            font=(theme.FAMILY, 10), anchor="w",
        )
        self.item_label.pack(fill="x")
        self.stats_label = tk.Label(
            inner_frame, text="", bg=theme.CARD, fg=theme.SUBTEXT,
            font=(theme.FAMILY, 11), anchor="w",
        )
        self.stats_label.pack(fill="x", pady=(2, 0))

        self.log_text = tk.Text(
            inner_frame, height=6, state="disabled", wrap="word",
            bg="#1E1E1E", fg="#D6D6D6", insertbackground="#D6D6D6",
            relief="flat", bd=0, padx=8, pady=6, font=(theme.MONO, 10),
        )
        self.log_text.pack(fill="both", expand=True, pady=(10, 0))

    # ---------------------------------------------------------- re-login

    def _show_relogin_dialog(self, username: str) -> None:
        if getattr(self, "_relogin_dialog", None) is not None:
            return

        dialog = tk.Toplevel(self)
        dialog.title("Session expired")
        dialog.configure(bg=theme.CARD)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        frame = tk.Frame(dialog, bg=theme.CARD, padx=28, pady=22)
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text=f"Instagram signed @{username} out.\n"
                 "Log in again to continue the download where it left off.",
            bg=theme.CARD, fg=theme.TEXT, font=(theme.FAMILY, 11),
            justify="center",
        ).pack(pady=(0, 12))

        password_entry = PlaceholderEntry(frame, "Password", show="•")
        password_entry.pack(fill="x", ipady=6, pady=3)
        twofa_entry = PlaceholderEntry(frame, "2FA code (optional)")
        twofa_entry.pack(fill="x", ipady=6, pady=3)

        status = tk.Label(frame, text="", bg=theme.CARD, fg=theme.RED,
                          font=(theme.FAMILY, 10), wraplength=260)
        status.pack(pady=(6, 0))

        def attempt() -> None:
            password = password_entry.value()
            if not password:
                status.configure(text="Please enter your password.",
                                 fg=theme.RED)
                return
            login_button.configure(state="disabled")
            status.configure(text="Logging in...", fg=theme.SUBTEXT)

            def work() -> None:
                try:
                    self.ig.login(username, password,
                                  twofa_entry.value().strip())
                    self.events.put(Event("relogin_ok", {}))
                except Exception as exc:  # noqa: BLE001
                    self.events.put(Event("relogin_fail", {"message": str(exc)}))

            threading.Thread(target=work, daemon=True,
                             name="instastash-relogin").start()

        def cancel() -> None:
            # Giving up on re-login means the download cannot continue.
            self.stop_event.set()
            self._close_relogin_dialog()

        buttons = tk.Frame(frame, bg=theme.CARD)
        buttons.pack(pady=(10, 0))
        login_button = RoundedButton(buttons, "Log in", command=attempt,
                                     width=130, height=34)
        login_button.pack(side="left", padx=(0, 8))
        RoundedButton(buttons, "Stop download", command=cancel,
                      kind="danger", width=130, height=34).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        self._relogin_dialog = dialog
        self._relogin_status = status
        self._relogin_button = login_button

    def _close_relogin_dialog(self) -> None:
        dialog = getattr(self, "_relogin_dialog", None)
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
            self._relogin_dialog = None

    def _reset_cache(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(
                APP_TITLE, "Please stop the running download first."
            )
            return
        count = self.account_cache.total_downloaded()
        if not messagebox.askyesno(
            APP_TITLE,
            f"Forget the {count} item(s) recorded as downloaded for "
            f"@{self.ig.client.username}?\n\n"
            "The next run will list everything again. Files already on disk "
            "are still skipped inside their own output folder.",
        ):
            return
        self.account_cache.clear()
        self._log_line("Download memory reset for this account.")

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="Choose where to save the downloads",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if chosen:
            self.output_var.set(chosen)

    # -------------------------------------------------------------- download

    def _on_start(self) -> None:
        selected = [
            c for c, var in zip(self.collections, self.collection_vars)
            if var.get()
        ]
        include_uncategorized = self.uncategorized_var.get()
        if not selected and not include_uncategorized:
            messagebox.showinfo(
                APP_TITLE, "Select at least one collection (or the "
                "Uncategorized option) to download."
            )
            return

        output = self.output_var.get().strip()
        if not output:
            messagebox.showinfo(APP_TITLE, "Please choose an output folder.")
            return
        try:
            Path(output).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                APP_TITLE, f"Cannot create the output folder:\n{exc}"
            )
            return

        # Fresh event per run: a previous worker that is still winding down
        # keeps its own (already set) event and can never be "un-stopped".
        self.stop_event = threading.Event()
        self.worker = DownloadWorker(
            ig=self.ig,
            output_dir=Path(output),
            selected_collections=selected,
            include_uncategorized=include_uncategorized,
            events=self.events,
            stop_event=self.stop_event,
            account_cache=self.account_cache,
            only_new=self.only_new_var.get(),
            concurrency=self.concurrency_var.get(),
            write_sidecars=self.sidecars_var.get(),
        )
        self.worker.start()

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress_bar.configure(value=0)
        self.percent_label.configure(text="0%")
        self.overall_label.configure(text="Preparing download...")
        self.collection_label.configure(text="")
        self.item_label.configure(text="")
        self.stats_label.configure(text="")
        self._log_line("Download started.")

    def _on_stop(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self._log_line("Stopping after the current file...")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                APP_TITLE,
                "A download is running. Stop it and quit?\n"
                "(Progress is saved — you can resume next time.)",
            ):
                return
            self.stop_event.set()
        self.destroy()

    # ---------------------------------------------------------------- events

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _handle_event(self, event: Event) -> None:
        data = event.data
        if event.kind == "login_ok":
            self.ig = data["ig"]
            self.collections = data["collections"]
            self._build_main_screen()
        elif event.kind == "login_fail":
            self.login_button.configure(state="normal")
            self.login_status.configure(text=data["message"], fg=theme.RED)
        elif event.kind == "log":
            self._log_line(data["message"])
        elif event.kind == "collection":
            self.collection_label.configure(
                text=f'Folder {data["index"]}/{data["count"]}: {data["name"]}'
            )
            self._log_line(f'--- Downloading "{data["name"]}" ---')
        elif event.kind == "item":
            self.item_label.configure(text=f'Current: {data["description"]}')
        elif event.kind == "progress":
            self._update_progress(data)
        elif event.kind == "relogin":
            self._show_relogin_dialog(data["username"])
        elif event.kind == "relogin_ok":
            self._close_relogin_dialog()
            if self.worker:
                self.worker.relogin_event.set()
        elif event.kind == "relogin_fail":
            if getattr(self, "_relogin_dialog", None) is not None:
                self._relogin_status.configure(text=data["message"],
                                               fg=theme.RED)
                self._relogin_button.configure(state="normal")
        elif event.kind == "finished":
            self._on_finished(data)
        elif event.kind == "error":
            self._log_line(f"ERROR: {data['message']}")
            self._reset_buttons()
            messagebox.showerror(APP_TITLE, data["message"])

    def _update_progress(self, data: dict) -> None:
        done, total = data["done"], data["total"]
        skipped, failed = data["skipped"], data["failed"]
        processed = done + skipped + failed
        percent = (processed / total * 100) if total else 0.0
        self.progress_bar.configure(value=percent)
        self.percent_label.configure(text=f"{percent:.0f}%")
        copied = data.get("copied", 0)
        copied_note = f" ({copied} copied locally)" if copied else ""
        self.overall_label.configure(
            text=f"{processed}/{total} items — {done} downloaded{copied_note}, "
                 f"{skipped} already done, {failed} failed"
        )
        self.stats_label.configure(
            text=f"⚡ {_format_bytes_per_second(data['speed_bps'])}      "
                 f"⏱ {_format_eta(data['eta_seconds'])} left"
        )

    def _on_finished(self, data: dict) -> None:
        self._reset_buttons()
        self.item_label.configure(text="")
        if data.get("aborted"):
            self.overall_label.configure(text="Stopped. Progress saved.")
            return
        done, skipped, failed = data["done"], data["skipped"], data["failed"]
        copied = data.get("copied", 0)
        copied_note = f" ({copied} copied locally)" if copied else ""
        summary = (
            f"Finished: {done} downloaded{copied_note}, {skipped} already "
            f"done, {failed} failed."
        )
        self.overall_label.configure(text=summary)
        self._log_line(summary)
        if failed:
            self._log_line(
                "Some items failed (often removed/private posts). "
                "Run again to retry just the failed ones."
            )
        messagebox.showinfo(APP_TITLE, summary)

    def _reset_buttons(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._close_relogin_dialog()

    def _log_line(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def run() -> None:
    App().mainloop()
