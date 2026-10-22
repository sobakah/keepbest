# keepbest

Finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one.

- **Exact copies:** identical images and videos, whatever their names and folders.
- **Remuxed videos:** the same video in another container, e.g. `clip.mp4`, `clip.mkv` and `clip.ts`.
- **Visually identical images:** the same picture saved in another format, quality or size (JPEG vs. WebP vs. PNG, a downscaled or EXIF-rotated copy).

It is deliberately cautious: pictures that merely *look similar* (an edited version, an added watermark, a crop, the next frame of a burst) are left alone. Files go to the system trash by default, every change is logged, and a dry run shows everything before anything happens.

## Installation

You need Python 3.9 or newer and [pipx](https://pipx.pypa.io/) (on Fedora: `sudo dnf install pipx`).

```bash
pipx install git+https://github.com/sobakah/keepbest.git
```

With JPEG XL support: `pipx install "keepbest[jxl] @ git+https://github.com/sobakah/keepbest.git"`, or add it to an existing install with `pipx inject keepbest pillow-jxl-plugin` (a plain `pip install` doesn't reach pipx's environment). For a new Python version the plugin may have no ready-made package yet; pip then builds it, which needs a C++ compiler and cmake (on Fedora: `sudo dnf install gcc-c++ cmake`). To install a specific release, add it to the URL: `...keepbest.git@1.0.0`. Releases before 1.0.0 are pre-releases (0.1.0 to 0.5.1).

**For videos**, also install ffmpeg (a system package): on Fedora `sudo dnf install ffmpeg-free`, or `ffmpeg` from RPM Fusion. Without it, identical video copies are still found, but not remuxes.

| Task | Command |
|---|---|
| Update | `pipx upgrade keepbest` (new version) or `pipx reinstall keepbest` (latest commit) |
| Uninstall | `pipx uninstall keepbest` |
| `keepbest` not found | run `pipx ensurepath` once and open a new terminal |

The package and the command are both called `keepbest`. Other ways to install or run it are described in [Other ways to run it](#other-ways-to-run-it).

**Coming from img_dedupe?** keepbest is its new name (since 1.0.0). `pipx upgrade` can't follow a rename, so switch once:

```bash
pipx uninstall img-dedupe
pipx install git+https://github.com/sobakah/keepbest.git
```

On its first start, keepbest moves your config, log and saved sessions from the old `img_dedupe` folders to the new ones and says so. "Not duplicates" marks in your picture folders are taken over the next time you use that folder.

## First run

```bash
keepbest ~/Pictures --dry-run
```

`--dry-run` shows what would be deleted and changes nothing. To carry out exactly what it showed, without scanning again, start keepbest on the same folder without `--dry-run` and press Enter on the start screen.

The program opens on a **start screen** with the folder, the number of files in scope and all settings. Each setting shows its choices: the current one in bold, the others greyed out. Press a setting's number to switch it, and **Enter** to start.

| Key | Setting |
|---|---|
| `1` | Stages to run: all, identical copies (1+2), visual only (3) |
| `2` | Strictness of the visual comparison: strict, normal, loose |
| `3` | When to ask: borderline groups only, every group, never |
| `4` | Delete mode: trash, dry run, permanent |
| `5` | Viewer for comparing images (see [Viewers](#viewers)) |
| `6` | Remove "(1)" from the names of kept copies: on/off |
| `7` | Include videos: on/off |
| `r` | Include subfolders |
| `f` | Choose subfolders (with `r` on) |
| `c` | Change folder (with Tab completion) |

**Choosing subfolders (`f`):** a tree of all subfolders with images or videos, all selected by default. A number switches that subfolder; if folders below it would change too, you are asked whether to switch them along (Enter: yes). `2-5` or `1,3,7` switch several at once, each on its own (`4,1` selects 4 and unselects 1); `a` selects all subfolders, `n` none. The **main folder** is switched only with its own key `m`, so it can't be left out by accident; the start screen shows the choice, e.g. `main folder + 2 of 4 subfolders`.

Hidden folders such as `.thumbnails` or `.Trash` are never scanned. The home folder, the folders above it (`/home`, `/Users`), the root folder and system folders (`/usr`, `/etc`, `/var`, ...) are refused, also when they are reached through a symlink.

## How it decides

**Stage 1 – identical files.** Files with identical content (SHA-256) are grouped, images and videos alike; only files of equal size are read, so this is fast.

**Stage 2 – remuxed videos.** ffprobe reads each video's details from its header, and only videos with the same codec, resolution and length are read in full and their video stream compared. Nothing is decoded, and a re-encoded video (other codec, size or quality) is *not* treated as a duplicate. The (first) video stream decides whether files belong together; all other tracks are compared by content as well: further video streams (another camera angle), audio, subtitles and data tracks such as timecode or camera telemetry. If all copies have byte-identical tracks in the same order, they are handled like identical files. If the tracks differ, you are asked, and a copy that contains every track of the others, in the same order, is recommended (else the one with the most tracks). The same tracks in another order count as a difference too (`TRACK ORDER`), since a player then starts with another language or sound. Without questions (`--auto`, or *when to ask: never*), such a group is only resolved when one copy contains all tracks of the others in their order, so no track is lost and no copy's default track changes; otherwise all files are left alone.

**Stage 3 – visually identical images** (images only). A quick fingerprint finds pairs that *might* match; then each is compared pixel by pixel with the image that would be kept. Both are shrunk to the same size (at most 512 px) and cut into 8×8-pixel squares; the score is the difference in the *most different* square, from 0 (identical) to 255. One small changed spot, like a date stamp, is therefore enough to keep two images apart. In tests, re-saves and resizes scored up to 15 and real edits 28 or more; the default limit is 20. Images whose shape differs by more than 2% are never compared, and animated images only take part in Stage 1. Files that hold several pictures, like camera JPEGs with a second picture (MPO: a stereo image or depth map) and multi-page TIFFs, are compared by their first picture and only ever kept, never removed for another copy. Greyscale pictures with more than 8 bits (16-bit PNG or TIFF) are compared with each other on one brightness scale, so a dark picture still matches its 8-bit copy, but not a brighter version of itself.

**Which copy is kept:**

1. the highest resolution;
2. lossless over lossy or unknown (a lossy copy never replaces a lossless original). JPEG XL can be either, and Pillow can't tell which, so by default a lossless original (PNG, TIFF, ...) of the same size is kept instead of a JXL copy. With `"prefer_jxl": true` the JXL copy is kept instead (it is smaller; choose this if your JXL files are lossless). TIFF counts as lossless unless it is JPEG-compressed;
3. the preferred format (`format_ranks`);
4. a name without a copy number like "(1)";
5. the less compressed file;
6. the oldest file.

For exact copies it is simply: a name without a copy number, then the oldest.

**Copy numbers:** if the kept file is called `photo (1).jpg` and another file of its group is `photo.jpg`, the number is removed after the others are gone: `photo (1).jpg` → `photo.jpg`. This only happens when another file of the group has the same base name, so series names like `Holiday (12).jpg` keep their number, and never when the new name is taken.

## Reviewing groups

The groups are numbered (`[3/12]`) and the file to keep is marked `▶`. By default, only **borderline** matches are shown for a decision; clear ones are handled automatically.

| Key | Action |
|---|---|
| **Enter** | Keep the recommended file ▶ and remove the others |
| `1`, `2`, … | Keep that file instead (the numbers shown in the group; `#0` is the recommended one) |
| `s` | Skip: keep all files; the group is shown again next time |
| `i` | Not duplicates of #0: keep all files and never show them together with #0 again |
| `i2` | Only image #2 is not a duplicate; decide on the rest as usual |
| `i0` | Only #0 is different: it is marked and kept, and the others are compared with each other afresh |
| `n` | Remove "(1)" from the kept name: on/off for this group |
| `v` | Open the images in the viewer |
| `p` / `t` | Previous open group / overview of all groups |
| `u` | Dry runs only: undo the decision of a group you already decided, and choose again. Open the group from the overview (`t`, then its number); its images, including any a chosen file had left out, come back. Real runs can't undo: trashed files are restored from the system trash (the log has their paths). |
| `q` | Quit (asks whether to keep your progress) |

In `permanent` mode, you are asked once before the run starts, since identical copies, remuxes and clear matches are then deleted without further questions. Every decision you make on a group's screen asks `[y/N]` again, with No as the default, so pressing Enter twice never deletes anything permanently. `--auto` asks nothing.

**"Not duplicates" marks** (`i`) are saved in a hidden file, `.keepbest_ignore.json`, in the scanned folder, so they stay with your pictures. A mark separates two images, not a whole group: images you marked against #0 are still compared with each other, and two of them that are copies are shown as a group of their own. A mark stops applying when one of its files changes. `--no-ignore` shows marked pairs again for one run; deleting the file forgets all marks.

## Dry runs and saved progress

**After a dry run** chosen on the start screen (setting `4`), you can carry it out for real right away, with the delete mode from your config (`trash` if the config says `dry_run`). After `--dry-run`, it is saved instead, to be carried out from the start screen in a later start without `--dry-run` (see below):

- **Enter:** apply exactly what the dry run showed, including your choices, without asking again.
- **`r`:** go through the groups again for real. Identical copies and remuxes are carried out as decided in the dry run (logged as decided there; remuxes you skipped are asked again), and the visual groups are shown as they were found, as if the dry run's decisions had not been made.

Before anything is deleted, every file is checked against the dry run; files that changed in the meantime are left alone.

**Progress is saved** once the duplicate groups are found, in dry runs too: before every question, after every decision, and at least once a second while groups are handled automatically. Each save is written to disk completely before it replaces the previous one. Ctrl+C, closing the terminal, stopping the program from outside or a crash therefore lose at most the last second of automatic decisions, and those are safe to repeat: before resuming, every file is checked against the disk, and anything already removed or changed is left out. Only an interruption *during* the analysis means starting that scan again.

When you open the same folder again, the start screen offers to **resume** (Enter), start a new scan (`n`) or discard the saved session (`x`). A finished dry run stays saved until you carry it out, so you can also do that later: on the start screen, Enter carries it out and `e` goes through its groups again.

**`--dry-run` always wins.** Started with `--dry-run`, nothing is deleted or renamed in that run: the delete mode is locked, a saved real session isn't resumed, a finished dry run isn't carried out, and a new dry run isn't offered for carrying out when it is done. Start without `--dry-run` for that, or press `n` to discard a saved session.

**When resuming**, the groups found by the original scan are used as they are, and the start screen shows which settings can still be changed. Greyed-out rows (stages, videos, subfolders, folder choice) belong to the saved session and are shown with its values. The file count on the start screen and media-optimizer afterwards (`m`) use the session's folders too, not those of the command line. Struck-through choices are unavailable: a *looser* strictness would need a new scan, and a real session can't continue as a dry run. **Stricter works without a new scan**: every group keeps the score of each image, so images above the new limit are simply taken out. Everything else applies: when to ask (`3`, and `uncertain_ratio`), the viewer (`5`) and the rename of "(1)" (`6`). A dry run always resumes as a dry run. A finished dry run is carried out exactly as it showed; only the delete mode (trash or permanent) can be chosen. Press **`n`** to discard the session: all settings are unlocked, and Enter starts a new scan. The summary shows the **totals of the whole session**, including earlier runs.

## After a run

The end screen offers to scan again with other settings (`b`), change the folder (`c`) or quit (Enter or `q`).

**media-optimizer:** if [media-optimizer](https://github.com/sobakah/media-optimizer) is installed, `m` runs it on the same folder after a run that removed files (not after a dry run), to convert what is left to more efficient formats. It runs in the same window and asks its usual questions; when it ends, you are back on keepbest's end screen. media-optimizer gets the same folder choice as the run (without `r` only the main folder, otherwise the subfolders chosen with `f` or `--exclude`), so it doesn't ask about subfolders again (needs media-optimizer 2.2 or newer; older versions include all subfolders). Ctrl+C stops media-optimizer, not keepbest.

## Viewers

`v` opens all images of a group at once, with the viewer chosen by setting `5` or `"viewer"` in the config:

| Viewer | What you get |
|---|---|
| `auto` | Your default image viewer, one window per image |
| `imagecompare` | [Image Compare](https://github.com/gimletlove/imagecompare): side by side or in a grid, synchronised zoom; recommended. `flatpak install flathub io.github.gimletlove.imagecompare` |
| `identity` | [Identity](https://apps.gnome.org/Identity/): tabs or side by side. `flatpak install flathub org.gnome.gitlab.YaLTeR.Identity` |
| `kitty`, `timg` | Previews inside the terminal (kitty terminal, or `timg`) |

If the chosen app is missing, keepbest says so and uses your default viewer; if no viewer can be started at all, it says so and the review goes on. Neither app can be told to open maximized: on KDE use a window rule (*System Settings → Window Management → Window Rules*), on GNOME press Super+↑.

## In the browser (preview)

`keepbest --web [FOLDER]` shows keepbest in your browser instead of the terminal: the same steps and questions as a page with buttons, and the keys still work there. keepbest prints a local address and opens it; only this computer can open the page, and only through that address. Without a folder, the page lets you choose one.

Close the page when you are done, and keepbest ends. Closing it while keepbest waits for an answer stops it there, as Ctrl+C would, and progress is saved; work in progress (a scan, a deletion) is finished first.

**Reviewing a group** shows its pictures side by side, or flips between the kept one and another one in one place (click the picture or press Space). Zoom (`+`, `-`) and dragging move all pictures together, so a difference stands out. *Go to the most different square* zooms in on the part where the pictures differ most, the one the score comes from, and a strip below shows that square from every picture. The pictures are shown as keepbest reads them (also JPEG XL, TIFF, 16-bit and CMYK), upright, as previews it renders itself.

This is a preview: remuxed videos are shown as tables only, and media-optimizer can't be started from the page yet; use the terminal for that.

**App menu:** `keepbest --install-desktop-entry` adds keepbest to your desktop's app menu (Linux); it opens the page, and file managers offer *Open with keepbest* for folders. `--remove-desktop-entry` removes it. The entry starts the keepbest that wrote it, so run the command again after installing keepbest elsewhere.

**One keepbest per folder:** keepbest refuses a folder that another running keepbest works on, or a folder inside or above it, and names that keepbest's process and page.

## Command-line options

| Option | Meaning |
|---|---|
| `-r`, `--recursive` | Include subfolders |
| `--exclude DIR` | With `-r`: skip this subfolder and everything in it (repeatable); `--exclude .` skips only the main folder |
| `--stages STAGES` | Stages to run, e.g. `1,2` or `3`: 1 identical files, 2 remuxed videos, 3 visually identical images (default `all`) |
| `--strictness {strict,normal,loose}` | Visual limit 12 / 20 / 28 |
| `--dry-run` | Show what would be deleted; change nothing |
| `-i`, `--interactive` | Ask for every group |
| `-y`, `--auto` | No start screen, no questions, no saved progress (for scripts) |
| `--no-rename` | Keep "(1)" in the names of kept copies |
| `--no-videos` | Leave videos out |
| `--no-ignore` | Show pairs marked "not duplicates" again |
| `-c FILE`, `--config FILE` | Use this config file |
| `--web` | Show keepbest in the browser (see [In the browser](#in-the-browser-preview)); not with `--auto` |
| `--no-browser` | With `--web`: only print the page's address |
| `--install-desktop-entry`, `--remove-desktop-entry` | Add keepbest to the app menu, or remove it, and exit |
| `--color {auto,always,never}` | Colours; `auto` respects `NO_COLOR` |
| `-v`, `-vv`, `--verbose` | More log output on stderr |

Exit codes: `0` success (also when you quit), `1` some files could not be removed, `2` a bad path, a refused folder (see [First run](#first-run)) or a folder another keepbest works on with `--auto`, a bad config file or missing packages, `130` stopped with Ctrl+C or by closing the page. Without `--auto`, a bad path is asked for again instead.

## Configuration

All settings can be stored in `config.json`; copy `config.example.json` to start. Missing keys use the defaults, and invalid values are reported at startup.

| Key | Default | Meaning |
|---|---|---|
| `delete_mode` | `trash` | `trash`, `permanent` or `dry_run` |
| `confirm` | `uncertain` | ask for `uncertain` (borderline) groups, `always` or `never` |
| `viewer` | `auto` | see [Viewers](#viewers) |
| `strictness` | `normal` | visual limit: `strict` 12, `normal` 20, `loose` 28 |
| `max_pixel_diff` | `null` | a number here replaces the strictness preset |
| `include_videos` | `true` | also check videos |
| `prefer_jxl` | `false` | `false`: keep a lossless original (PNG, TIFF, ...) instead of a JPEG XL copy of the same size; `true`: keep the JXL copy |
| `rename_numbered` | `true` | remove "(1)" from kept copies |
| `save_sessions` | `true` | save progress so it can be resumed |
| `log_file` | `null` | `null` = default place, `false` = no log, or a file path |
| `journal_file` | `null` | `null` = default place, `false` = no [journal](#the-file-journal), or a file path |
| `color` | `auto` | `auto`, `always`, `never` |
| `format_ranks` | JXL > WEBP > AVIF > PNG/TIFF > JPEG > GIF > BMP | preferred formats when choosing the copy to keep; names are not case-sensitive, `JPG` means JPEG and `TIF` TIFF, MPO ranks like JPEG unless given its own rank |
| `uncertain_ratio`, `compare_size`, `max_aspect_diff`, `hash_size`, `hash_max_distance` | `0.8`, `512`, `0.02`, `8`, `28` | fine-tuning, see below |

### Fine-tuning the visual comparison

| Key | What it does | Higher | Lower |
|---|---|---|---|
| `max_pixel_diff` / `strictness` | Highest score that still counts as the same picture | Also finds heavily compressed copies; tiny edits may slip through | Safer; heavily compressed copies stay on disk |
| `uncertain_ratio` | Share of the limit above which you are asked (0.8 × 20 = above 16) | Fewer questions (1.0 = never) | More questions (0 = always) |
| `compare_size` | Comparison size in pixels | Catches smaller watermarks; slower | Faster; small differences blur away |
| `max_aspect_diff` | Allowed difference in shape (0.02 = 2%) | Slightly cropped copies get compared | Below 0.01, small resized copies can be missed |
| `hash_max_distance` | How loose the quick pre-check is (of 128 bits) | Finds more candidates; slower, never less accurate | Faster; real duplicates can be missed |

Leave `hash_size` at 8. If different pictures get grouped, use `strict` or raise `compare_size` (e.g. 768); if obvious duplicates are missed, try `loose` or raise `hash_max_distance` (e.g. 36); if you're asked too often, raise `uncertain_ratio` (e.g. 0.9), or lower it to be asked more. Check any change with `--dry-run` first.

## Files and where they are kept

| File | Installed with pipx | Running from the project folder |
|---|---|---|
| Config `config.json` | `~/.config/keepbest/` | the project folder, else `~/.config/keepbest/` |
| Log `keepbest.log` | `~/.local/state/keepbest/` | the project folder |
| Saved sessions | `~/.local/state/keepbest/sessions/` | `sessions/` in the project folder |
| Journal `journal.jsonl` | `~/.local/state/keepbest/` | the same |
| "Not duplicates" marks | `.keepbest_ignore.json` in the scanned folder | the same |
| Running keepbest processes and their folders | `~/.local/state/keepbest/running/` | `running/` in the project folder |

`$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` are respected; on Windows the state folder is `%LOCALAPPDATA%\keepbest`. In the project folder, `config.json`, the log and `sessions/` are ignored by git.

**The log** records every change, one line per file: what happened, the full path, the file kept instead, and why. Trashed files can be restored from the system trash using these paths. Dry runs are not logged.

```
2026-09-23 20:39:37 | TRASHED  | /pics/a (1).png | kept /pics/a.png | exact copy (identical bytes), automatic
2026-09-23 20:39:37 | TRASHED  | /pics/photo.jpg | kept /pics/photo (1).jpg | visual match (worst diff 5/20), automatic
2026-09-23 20:39:37 | RENAMED  | /pics/photo (1).jpg | new name /pics/photo.jpg
```

### The file journal

Besides the log, keepbest keeps a journal for other programs: `journal.jsonl`, one JSON object per line, about every file it removed or renamed. A program that stores data about pictures by path, like a tagger, can move a removed copy's data to the kept file and follow renames. media-optimizer writes the same format. The journal is always in the state folder, also when running from the project folder; set `journal_file` to `false` to turn it off, or to a path (relative paths count from the state folder). Dry runs write nothing, and if the journal can't be written, keepbest warns once and carries on. The file is only ever appended to; delete it when no program needs it any more.

Every line has `v` (format version, 1), `tool` (`keepbest`), `tool_version`, `run` (one id per run), `ts` (local time) and `event`:

| Event | Fields |
|---|---|
| `run_start` | `source` (the folder), `target` (always `null`), `pid` |
| `removed` | `path`, `size`, `mtime_ns` (as before the removal), `how` (`trash` or `permanent`), `reason` (`duplicate`), `kept` (the file kept instead), `match` (`exact`, `remux` or `visual`), `hard_link` (`true` when the removed name was a hard link to the kept file) |
| `renamed` | `src`, `dest`, `size`, `mtime_ns`, `reason` (`copy_number`: "(1)" removed) |
| `run_end` | `code` (the exit code), `interrupted` |

Paths are absolute. File names that aren't valid UTF-8 appear as `\udcXX` escapes, and `os.fsencode` in Python gives back the exact bytes. Events are in the order things happened: a kept file may be renamed or, in a later stage, removed in favour of a larger copy by a later event of the same run. Failed actions are not journalled.

```
{"v":1,"tool":"keepbest","tool_version":"1.3.0","run":"261018-141502-4242-9f3c","ts":"2026-10-18T14:15:02+02:00","event":"removed","path":"/pics/photo (2).png","size":5320,"mtime_ns":1792327502000000000,"how":"trash","reason":"duplicate","kept":"/pics/photo (1).png","match":"exact"}
{"v":1,"tool":"keepbest","tool_version":"1.3.0","run":"261018-141502-4242-9f3c","ts":"2026-10-18T14:15:02+02:00","event":"renamed","src":"/pics/photo (1).png","dest":"/pics/photo.png","size":5320,"mtime_ns":1792327502000000000,"reason":"copy_number"}
```

## Other ways to run it

**From a local clone:** `git clone https://github.com/sobakah/keepbest.git`, then `pipx install .` in that folder. To update, `git pull` and `pipx install --force .`.

**For development:** an editable install, where code changes take effect immediately:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[jxl]"
```

**Without installing:** get the code (clone it, or *Code → Download ZIP* on GitHub and rename `keepbest-main` to `keepbest`), set up the two packages once, and start it with `run.py`:

```bash
cd ~/keepbest
python3 -m venv .venv
.venv/bin/pip install Pillow send2trash        # optional: pillow-jxl-plugin
.venv/bin/python run.py ~/Pictures
```

`run.py` works from any folder: `~/keepbest/.venv/bin/python ~/keepbest/run.py ~/Pictures`. As a shortcut, add this line to `~/.bashrc`:

```bash
alias keepbest='~/keepbest/.venv/bin/python ~/keepbest/run.py'
```

| Error | Cause |
|---|---|
| *the 'keepbest' package was not found* | the `keepbest/` folder is missing next to `run.py`; the message says how to fix it |
| *attempted relative import with no known parent package* | started as `python3 keepbest/…`; use `run.py` or `python3 -m keepbest` |
| *Missing required packages* | started without `.venv/bin/python` |

On Windows (not tested yet): `py -m venv .venv`, `.venv\Scripts\pip install Pillow send2trash pyreadline3`, `.venv\Scripts\python run.py C:\Pictures`.

## Running the tests

The tests belong to the repository, not to the installed program: pipx installs only keepbest itself, without the tests or pytest. So you need a clone of the repository, and the tests check the code in that clone.

**With your pipx installation.** It already has Pillow and send2trash; add pytest to it once, then run the tests of a clone with its Python:

```bash
pipx inject keepbest pytest
git clone https://github.com/sobakah/keepbest.git
cd keepbest
git checkout 1.2.1       # optional: the version you have installed (see keepbest --version)
"$(pipx environment --value PIPX_LOCAL_VENVS)/keepbest/bin/python" -m pytest
```

`pipx uninject keepbest pytest` removes pytest again; the program keeps working. (`pipx run pytest` does *not* work: it starts pytest in a separate environment without Pillow.)

**In a development setup**, independent of any pipx installation:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest              # everything, about 15 seconds
.venv/bin/python -m pytest -k video     # only tests with "video" in their name
```

Video tests are skipped if ffmpeg isn't installed, and the H.264 cases if your ffmpeg has no H.264 encoder (like Fedora's `ffmpeg-free`). Every test runs in its own temporary home, config and state folder, so your real settings, log, sessions and trash are never touched.

**Writing a test** is quick with the helpers in `tests/conftest.py`: `library` builds a picture folder (`photo`, `variant`, `copy_of`), `run_cli` runs keepbest like a user would, including typed answers (`keys=[...]`), and `summary()` reads the final numbers:

```python
from conftest import summary

def test_a_backup_copy_is_found(library, run_cli):
    library.photo("a.png", seed=1)
    library.copy_of("a.png", "backup/a copy.png")
    result = run_cli(library.path, "-r", "--auto", "--dry-run")
    assert summary(result)["exact"] == 1
```

`tests/pictures.py` makes deterministic photo-like pictures and edited versions (`with_text`, `with_patch`, `brighter`, `downscaled`); `fake_command` puts stand-in programs on the PATH, e.g. to test viewers.

## Releasing a new version

Versions have three parts: a new feature or changed behaviour raises the middle number (1.3.0), a pure fix the last one (1.2.2). Tags are the plain version, without `v`. Raise `__version__` in `keepbest/config.py` (the only place the version is set), add the changes to `CHANGELOG.md`, run the tests, commit, then tag and push:

```bash
git tag -a 1.3.0 -m "keepbest 1.3.0"
git push && git push --tags
gh release create 1.3.0 --title "keepbest 1.3.0" --notes-file notes.md   # that version's changelog section
```

Without the version bump, `pipx upgrade keepbest` doesn't see the new release. Never move or delete a published tag; release a fix as a new version instead.

## Known limitations

- An upscaled copy counts as the better one, because it has the higher resolution.
- Very small watermarks on very large photos can disappear at the 512 px comparison size; raise `compare_size` or use `strict` if that matters.
- During the visual comparison, a thumbnail of every candidate image is kept in memory (about 768 KB each), so thousands of candidates can need several GB of RAM.
- Videos are only compared by content; a re-encoded copy is not recognised. MPEG-4 (DivX/Xvid) video inside an MPEG-TS file can't be read by ffmpeg and is left alone.
- Developed and tested on Linux; macOS and Windows are supported by the code but untested.
