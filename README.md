# InstaStash

A small desktop app that downloads **all of your Instagram saved posts** and
organizes them into folders that mirror your saved collections. If a post is
saved in a collection called `ASD`, it lands in an `ASD/` folder on disk.

## Features

- **Collection-aware** — every saved collection becomes a folder with the same
  name; saved posts that are in no collection go to `Uncategorized/`.
- **Source quality** — photos and videos are downloaded from Instagram's CDN
  at the highest resolution Instagram serves.
- **Clean, unique filenames** — `2024-03-17_natgeo_C4kXbQwJx1a.jpg`
  (date, author, Instagram's own post code). Carousel posts become
  `..._1.jpg`, `..._2.mp4`, and so on. Files also get their original post
  date as the file's modified time, so sorting by date "just works".
- **Live progress** — overall percentage, current collection, current item,
  download speed, and estimated time remaining.
- **Resume support** — if the app stops for any reason (error, Stop button,
  crash, power loss), just start the download again with the same output
  folder: already-downloaded items are detected and skipped automatically.
- **Only-new-items memory** — the app remembers per account what it has
  already downloaded (across all runs and output folders). When you save new
  posts and run it again, it asks Instagram only for the items saved since
  last time and downloads just those. Toggle with *"Only download new
  items"*; forget everything with *"Reset download memory"*.
- **Choose your output folder** — anywhere on your disk.
- **Session reuse** — after the first login a session is stored locally
  (`session.json`), so future runs usually will not ask Instagram again.

## Requirements

- Python **3.10 or newer**
- An Instagram account (you download *your own* saved posts)

## Setup

### macOS

```bash
# 1. Check your Python version (3.10+). If missing, install from python.org
#    or with: brew install python
python3 --version

# 2. From the project folder, create a virtual environment
cd InstaStash
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python main.py
```

> Note: the GUI uses Tkinter, which ships with the python.org and Homebrew
> Python installers. If you see `No module named _tkinter` on Homebrew Python,
> run `brew install python-tk`.

### Windows

```powershell
# 1. Install Python 3.10+ from https://www.python.org/downloads/
#    IMPORTANT: tick "Add python.exe to PATH" in the installer.
py --version

# 2. From the project folder, create a virtual environment
cd InstaStash
py -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
py main.py
```

## Usage

1. **Log in** with your Instagram username and password.
   - If your account has **two-factor authentication**, put the 6-digit code
     from your authenticator app into the *2FA code* field.
   - If Instagram raises a security check, approve the login from the
     Instagram app on your phone, then log in again here.
2. **Pick collections** — all of your saved collections are listed with their
   item counts; everything is selected by default. Optionally keep
   *"Also download saved posts that are in no collection"* checked to get an
   `Uncategorized/` folder with the rest of your saves.
3. **Choose the output folder** (defaults to `~/Downloads/InstaStash`).
4. Press **Start download** and watch the progress panel.

The result looks like this:

```
InstaStash/
├── ASD/
│   ├── 2024-03-17_natgeo_C4kXbQwJx1a.jpg
│   ├── 2024-05-02_nasa_C6hPqRtJm3b_1.jpg      ← carousel item 1
│   ├── 2024-05-02_nasa_C6hPqRtJm3b_2.mp4      ← carousel item 2
│   └── ...
├── Recipes/
│   └── ...
└── Uncategorized/
    └── ...
```

## Resuming an interrupted download

Nothing to configure. The app keeps a tiny state file
(`.instastash_state.json`) inside the output folder recording every fully
downloaded item. Start the download again with the same output folder and it
skips straight to where it left off. Partially downloaded files (`.part`) are
never counted as finished.

## Downloading only new saves

Also automatic. A per-account memory (`cache/<username>.json`, media ids
only) records everything ever downloaded. On the next run with *"Only
download new items"* checked (the default), the app fetches just the items
you saved since the last successful run — it does not even re-list old
items, so repeat runs are fast no matter how big your library is. This works
across output folders: you can download new items into a different folder
and nothing old is repeated.

- If a run is interrupted or some items fail, the memory deliberately does
  not advance past them — the next run picks them up again.
- *"Reset download memory"* (next to the checkbox) makes the app forget the
  account's history and treat everything as new again.
- Untick *"Only download new items"* for one run to re-list everything
  (files already inside the chosen output folder are still skipped).

## Troubleshooting

| Problem | Fix |
|---|---|
| "Two-factor authentication required" | Enter the 6-digit code from your authenticator app in the 2FA field and log in again. |
| "Instagram is asking for a security check" | Open the Instagram app/website, approve the login attempt, then retry. |
| "Instagram is rate-limiting login attempts" | Wait 5–10 minutes before retrying. Avoid repeated failed logins. |
| A few items show as *failed* | Usually the original post was deleted or made private. Run the download again to retry only the failed items. |
| `No module named _tkinter` (macOS/Homebrew) | `brew install python-tk` |
| Login works but collections list is empty | You have no named collections — use the *Uncategorized* option to download all saves. |

## Notes on safety & privacy

- Your credentials go **directly to Instagram** — nowhere else. The saved
  session (`session.json`) stays on your machine and is git-ignored.
- The app deliberately paces its API requests (1–3 s between calls) to stay
  well within Instagram's limits. Very large libraries simply take a while
  to index — the actual media downloads are fast.
- This tool is for **personal backup of your own saved posts**. Automated
  access is against Instagram's Terms of Service; use it reasonably and at
  your own risk.

## Project layout

```
main.py                 entry point
instastash/
├── gui.py              Tkinter interface (login + main screen)
├── theme.py            Instagram-inspired palette, fonts, ttk styles
├── widgets.py          gradient banner, rounded buttons, custom checkboxes
├── cache.py            per-account download memory (only-new-items runs)
├── client.py           login / session / collections (instagrapi)
├── downloader.py       background download worker, speed & ETA, retries
├── state.py            resume state (atomic JSON in the output folder)
└── naming.py           cross-platform safe file & folder names
requirements.txt
```
