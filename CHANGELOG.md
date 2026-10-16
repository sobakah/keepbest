# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`i0`: only the recommended image is different.** On a group of three or more images, `i0` marks #0 as not a duplicate of the others, keeps it, and compares the others with each other afresh, so copies among them can still be removed.

### Changed

- **media-optimizer gets keepbest's folder choice.** Started with `m` from the end screen, media-optimizer now covers the same folders as the run: without `r` only the main folder, otherwise all subfolders except the ones left out with `f` or `--exclude`. Before, it always included every subfolder and keepbest only warned about it. The choice is passed as `MO_SUBFOLDERS` and `MO_EXCLUDE_FOLDERS` (a JSON list, since folder names may contain `:`); media-optimizer then skips its own subfolder question.
- **More folders are refused:** besides the home and root folder, also every folder above home (`/home`, `/Users`) and system folders (`/usr`, `/etc`, `/var`, ...).
- **Every track of a video counts in Stage 2:** further video streams (a second camera angle) and data tracks (timecode, camera telemetry) are compared like audio and subtitle tracks, and the copy with the most of them is recommended.
- **Track order counts in Stage 2.** Copies with the same tracks in another order start with another language or sound; they are now asked about (`TRACK ORDER`), and `--auto` leaves them alone. Before, `--auto` removed one of them.
- **"Not duplicates" marks separate two images, not a group.** Images marked against a group's recommended image are still compared with each other, so two copies among them are found as a group of their own.
- **Camera JPEGs with a second picture (MPO) and multi-page TIFFs take part in Stage 3.** They were skipped as "animated". They are compared by their first picture, rank like JPEG/TIFF, and are only ever kept, never removed for another copy.
- **Reviewing a dry run again (`e`/`r`)** shows the visual groups as they were found, with the images a chosen keeper had taken out; remuxes skipped in the dry run are asked again, and the ones decided there are logged as decided by you instead of "automatic".
- **`format_ranks` names are not case-sensitive**, `JPG` means JPEG and `TIF` TIFF; unknown names are reported. The unused `JPG` entry is gone from the defaults.

### Fixed

- **A dark 16-bit greyscale picture was deleted as a duplicate of a brighter version of itself** (4× as bright scored 0), and dark 16-bit pictures didn't match their 8-bit copies. Greyscale with more than 8 bits is now compared pairwise on one brightness scale.
- **A video with a second video stream could be removed** as an identical remux of a copy with only the first one.
- **Resumed sessions and saved dry runs use their own folder choice** for the file count on the start screen and for media-optimizer, instead of the command line's.
- **`--exclude` with `..`** (`Urlaub/../Urlaub`) was silently not applied, so the folder was scanned and cleaned anyway; paths outside the scanned folder are now reported.
- **Carrying out a dry run** no longer fails on a remux whose file Stage 1 renamed ("No such file or directory"), and a dry run now picks the same keeper as a real run would.
- When nothing of a group could be removed (e.g. no permission), the kept file keeps its "(1)" and the group stays open for a resumed session.
- Remux groups skipped with `s` are counted as skipped.
- The start screen promised renames whose new name was taken, and showed another rename setting than the one a saved dry run is carried out with; a finished Stage 1/2-only dry run was shown as "0 group(s)".
- Ctrl+C or SIGTERM during Stage 2 waited until every video being read was read to the end, and then started reading it again; ffmpeg is now stopped at once.
- Tab completion of folder names with spaces completed the wrong folder.
- `v` crashed the review when no viewer (`xdg-open`) was installed.
- Durations of possible remuxes are compared alike for both files (1 % of the longer one).
- Float pictures with infinite values are reported as such instead of with an internal error message.
- With videos switched off, "Nothing to scan here" now says that videos can be included (`7`).

## [1.1.0] — 2026-10-10

**Upgrading:** `pipx upgrade keepbest`.

### Added

- **Run media-optimizer afterwards.** If [media-optimizer](https://github.com/sobakah/media-optimizer) is installed, the end screen of a run that removed files offers `m` to run it on the same folder. When it ends, keepbest's end screen comes back for a new scan. Ctrl+C stops only media-optimizer.

### Changed

- **The folder list counts images and videos apart** (`3 images · 1 video`), for the main folder, every subfolder and the selection total. Before, every file was counted as an image. With videos switched off, their count is marked *not scanned*.
- **Switching a folder asks about its subfolders.** In the folder list, a folder used to take every folder below it along. Now you are asked first (`Also switch the 2 subfolders below Urlaub/? [Y/n]`, Enter: yes), so a folder can be left out while its subfolders are still scanned. No question when nothing below would change, or when the subfolders are listed in the same input.

## [1.0.0] — 2026-10-08

The first stable release, under a new name: **keepbest** (formerly img_dedupe).
The name says what it does; "img_dedupe" was one of many similar names. After
five pre-releases and two independent code reviews, the program is the state
of 0.5.1 under the new name.

The history before this release was consolidated: 0.1.0 to 0.5.1 are the
pre-releases that were published as 1.0 to 1.4.1. A keepbest 1.1.0 that was
published for a few minutes on the same day is now part of this release.

**What it does:**

- **Three stages.** Identical files (SHA-256, whatever their names and folders); remuxed videos (the same video stream in another container, with the audio and subtitle tracks compared by content); visually identical images (the same picture in another format, quality or size, found by comparing pixels).
- **Keeps the best copy:** the highest resolution, lossless over lossy, your preferred format, a name without a copy number like "(1)", the less compressed file, the oldest.
- **Cautious.** Edited versions, watermarks, crops and the next frame of a burst are left alone; every deleted image is compared with the one that is kept.
- **Safe by default.** Files go to the system trash; `--dry-run` never deletes anything; permanent mode asks before it starts; every change is logged with the file kept instead and the reason.
- **You decide what is borderline:** keep another image (the others are compared with it afresh), skip a group, mark images as not duplicates, open them in a viewer, or undo a decision in a dry run.
- **Interruptible.** Progress is saved as you go, so a review can be resumed, and a finished dry run can be carried out later without scanning again.

### Changed

- **Renamed to keepbest.** The command and the package are `keepbest`, the repository is `github.com/sobakah/keepbest` (the old address forwards there), the config folder is `~/.config/keepbest/`, the log `~/.local/state/keepbest/keepbest.log`, and "not duplicates" marks are stored in `.keepbest_ignore.json`.
- **Your files move along by themselves.** The first start moves `~/.config/img_dedupe/` and `~/.local/state/img_dedupe/` (log and saved sessions) to the new folders and renames the log file; folders that already exist under the new name are never overwritten. If a move fails, keepbest says why, and the old config is still read. Marks in `.img_dedupe_ignore.json` keep working and are moved to `.keepbest_ignore.json` the next time you mark a pair in that folder. `IMG_DEDUPE_STATE_DIR` is still accepted next to the new `KEEPBEST_STATE_DIR`.
- **Repository layout.** The code moved from `scripts/` to `keepbest/`. From a checkout, start it with `./run.py` or `python3 -m keepbest` instead of `python3 -m scripts`. The development extra is now `.[dev]` (pytest and pyflakes) instead of `.[test]`.
- **Release tags have no `v`** (`1.0.0`, `0.5.1`). Add the version without `v` to install a specific release: `...keepbest.git@1.0.0`.
- The release notes are now this changelog.

### Upgrading from img_dedupe

`pipx upgrade` can't follow a rename. It stops with *inconsistent name* and leaves the old version in place, so nothing breaks. Switch once:

```bash
pipx uninstall img-dedupe
pipx install git+https://github.com/sobakah/keepbest.git
```

With JPEG XL support, install `"keepbest[jxl] @ git+https://github.com/sobakah/keepbest.git"` instead. On its first start, keepbest moves your config, log and saved sessions over and says so.

If you installed the short-lived keepbest 1.1.0, `pipx upgrade` won't go back to 1.0.0; reinstall with `pipx install --force git+https://github.com/sobakah/keepbest.git`.

## Pre-releases

The releases before 1.0.0 were renumbered; their notes below use the new numbers.

| Pre-release | Formerly | Released |
|---|---|---|
| 0.5.1 | 1.4.1 | 2026-10-05 |
| 0.5.0 | 1.4 | 2026-10-02 |
| 0.4.0 | 1.3 | 2026-09-27 |
| 0.3.0 | 1.2 | 2026-09-24 |
| 0.2.1 | 1.1.1 | 2026-09-24 |
| 0.2.0 | 1.1 | 2026-09-24 |
| 0.1.0 | 1.0 | 2026-09-23 |

## [0.5.1] — 2026-10-05

*Pre-release; published as img_dedupe 1.4.1.*

This release lets a dry run take back a decision.

**Upgrading:** `pipx upgrade img-dedupe`.

### Added

- **Undo a decision in a dry run.** A group you already decided can be opened again from the overview (`t`, then its number) and `u` takes the decision back, so you can keep another image. The group gets back all its images and the totals no longer count it. Real runs can't undo, since the files are already in the trash.

## [0.5.0] — 2026-10-02

*Pre-release; published as img_dedupe 1.4.*

This release fixes the data-safety problems found in an independent code review.

### Fixed

- **`--dry-run` could delete files.** With a saved real session, `--dry-run` and Enter resumed it for real; with a saved dry run, Enter carried it out. Started with `--dry-run`, nothing is deleted or renamed in that run any more, and the delete mode is locked.
- **Videos with the same picture but other sound were treated as remuxes** and removed without asking, e.g. a dub whose language tag is missing. The audio and subtitle tracks are now compared by content too (MPEG-TS remuxes of AAC still match). Without questions, a group is only resolved when one copy contains every track of the others.
- **10- and 12-bit pictures stored in 16-bit files** were compared almost black, so a clearly edited version could count as a confident duplicate. They are now scaled from the bit depth they actually use; float pictures no longer all look identical.
- **Keeping another image than the recommended one** removed the others without comparing them with the image you chose. They are now compared afresh; images too different from it stay on disk.
- **A lossy JPEG XL copy could replace a lossless PNG original**, because JXL always counted as lossless. By default the lossless original is now kept; the new setting `prefer_jxl` keeps the JXL copy instead. JPEG-compressed TIFFs count as lossy.

### Changed

- **After `--dry-run`, carrying it out is no longer offered right away.** Start again without `--dry-run`: Enter on the start screen carries out the saved dry run without a rescan. A dry run chosen on the start screen (setting 4) is still offered for carrying out when it is done.
- **If your library has lossless JPEG XL copies next to PNG originals**, the PNGs are now kept. Set `"prefer_jxl": true` in your config to keep the JXL copies as before.
- **Permanent mode asks once before a run starts**, since automatic decisions delete without further questions. The README claimed every deletion asked first.

## [0.4.0] — 2026-09-27

*Pre-release; published as img_dedupe 1.3.*

This release fixes the problems found in a code review, numbers the stages consistently, and adds a test suite.

**Upgrading:** `pipx upgrade img-dedupe`.

**`--stages` has changed.** The stages are now numbered 1 identical files, 2 remuxed videos, 3 visually identical images, and `--stages` takes these numbers (e.g. `--stages 1,2`). Before, `1` meant identical files *and* remuxes, and `2` meant the visual comparison. If you use `--stages` in scripts: the old `1` is now `1,2`, the old `2` is now `3`; `both` still means all.

### Fixed

- **`--auto` scanned the home or root folder.** It is now refused (exit code 2), as it always was without `--auto`.
- **`--exclude` didn't cover subfolders:** `--exclude Urlaub` still scanned `Urlaub/Strand`. A folder now takes its subfolders along, like in the folder list.
- **Invalid numbers in `config.json`** (e.g. `"compare_size": "big"`) silently made the visual comparison find nothing. They are now reported and the default is used.
- **ffmpeg could hang forever**, e.g. on a network drive; it now gets a time limit that grows with the file size.
- A failed save (e.g. a full disk) left a temporary file behind.
- Tab completion failed for folder names with brackets, like `Photos [2024]`.
- Renaming a kept copy could replace a dangling symbolic link.
- Removing a hard link was counted as freed space; it is now shown as such, without freeing anything.

### Changed

- **Stages are numbered 1 to 3** everywhere: in the output, in setting 1 on the start screen (*all*, *identical copies (1+2)*, *visual only (3)*) and in `--stages`.
- README corrections: the keys for keeping another file, and when exit code 2 is used.

### Added

- **A test suite** of 116 tests covering the comparison, the three stages, deleting, logging, renaming, folders, sessions, videos, viewers and the command line. Run it with `python3 -m pytest`; `tests/conftest.py` has helpers for writing new tests (see *Running the tests* in the README).
- `IMG_DEDUPE_STATE_DIR` redirects the log and saved sessions to another folder (used by the tests).

## [0.3.0] — 2026-09-24

*Pre-release; published as img_dedupe 1.2.*

This release makes saved sessions clearer and more robust, and asks fewer questions.

**Upgrading:** `pipx upgrade img-dedupe`. If your own `config.json` contains `"uncertain_ratio": 0.6` (for example because it was copied from the old example file), change it to `0.8` or remove the line, otherwise it keeps the old behaviour.

### Added

- **The start screen shows what a saved session allows.** Settings that belong to the session (stages, videos, subfolders, folder choice) are greyed out and show the session's values; unavailable choices are struck through. `n` discards the session and unlocks all settings, without starting the new scan straight away.
- **Stricter without a new scan.** When resuming, strictness can be made stricter: images whose saved score is above the new limit are taken out of their groups. A looser limit still needs a new scan.
- **Session totals.** A resumed session continues its statistics; the summary shows the totals of the whole session, including earlier runs.

### Changed

- **Fewer questions.** `uncertain_ratio` now defaults to 0.8 instead of 0.6: with the normal limit of 20, you are asked about matches above 16 instead of above 12.
- **More robust saving.** Progress is saved before every question and after every decision, and at most once a second while groups are handled automatically. Every save is written fully to disk before it replaces the previous one.
- **Stopping from outside.** Closing the terminal or stopping the program (`kill`, shutdown) now works like Ctrl+C: progress is saved and the log is closed.
- **Carrying out a saved dry run** uses the delete mode chosen on the start screen (trash or permanent).

### Fixed

- Large reviews were slow: the session was saved after every step, costing about a minute per 1,000 groups.
- When resuming, the rename setting (`6`) and `uncertain_ratio` were not applied to the groups still open.
- When carrying out a saved dry run from the start screen, the delete mode chosen there was ignored.

## [0.2.1] — 2026-09-24

*Pre-release; published as img_dedupe 1.1.1.*

### Fixed

- In the folder list, several folders typed at once (like `4,1` or `2-5`) all switched together, following the first one. So a folder couldn't be unselected in the same input that selected another: `4,1` selected both. Now each listed folder is switched on its own: `4,1` selects 4 and unselects 1. A folder still takes the folders below it along, and a subfolder listed together with its parent keeps its own choice.

**Upgrading:** `pipx upgrade img-dedupe`.

## [0.2.0] — 2026-09-24

*Pre-release; published as img_dedupe 1.1.*

This release adds duplicate detection for videos, remembers matches you mark as "not duplicates", and saves dry runs so they can be carried out later.

**Upgrading:** `pipx upgrade img-dedupe`, or install this release directly with `pipx install --force "git+https://github.com/sobakah/img_dedupe.git@0.2.0"`. Your config and saved sessions from 0.1.0 keep working.

### Added

- **Videos.** Identical copies of videos are found whatever their names and folders, and so are **remuxes**: the same video stream in another container (MP4, MKV, MPEG-TS, …). Nothing is decoded; if the copies have different audio or subtitle tracks, you are asked, and the copy with the most tracks is recommended. Needs ffmpeg for remuxes; without it, identical copies are still found. Switch videos off with setting `7`, `--no-videos` or `"include_videos": false`. Re-encoded videos are not compared.
- **"Not duplicates" marks.** In a group, `i` keeps all files and remembers the match, so it isn't shown again; `i2` marks only image #2. Marks are saved in a hidden `.img_dedupe_ignore.json` in the scanned folder. `--no-ignore` shows them again for one run.
- **Dry runs are saved.** Stopping a dry run with Ctrl+C or `q` no longer loses it, and a finished dry run stays saved until you carry it out: later, from the start screen, Enter applies it and `e` goes through its groups again, without a new scan.
- **Image Compare** as a viewer (`"viewer": "imagecompare"`): all images of a group side by side or in a grid, with synchronised zoom.
- **Running without installing** is documented, and the message for missing packages now explains how to set them up.

### Changed

- **The main folder** is switched on or off only with its own key `m` in the folder list. Numbers, ranges, "all" and "none" only change subfolders.
- **Saved sessions** of a project-folder checkout are stored in its `sessions/` folder (ignored by git), next to the log and config. Sessions from the old place are found and moved automatically.
- **Viewers:** if Identity or Image Compare isn't installed, img_dedupe says so and shows the install command before using the default viewer. Viewer windows stay open when img_dedupe is stopped.
- **The README** is reorganised and about half as long.

### Fixed

- Choosing subfolders could silently leave out the files directly in the main folder: "select none" also deselected the main folder, and picking subfolders afterwards didn't bring it back.

### Known limitations

Unchanged from 0.1.0, except that dry runs are now saved as sessions. Videos are only compared by content, so a re-encoded copy isn't recognised. The full list is in the README.

## [0.1.0] — 2026-09-23

*Pre-release, first public release; published as img_dedupe 1.0.*

img_dedupe finds duplicate images in a folder and removes the redundant copies, keeping the best one. It recognises byte-identical files as well as the same picture saved in another format, quality or size, and it is deliberately conservative: pictures that merely look similar, like an edited version or the next frame of a burst, are left alone.

### Installation

```bash
pipx install git+https://github.com/sobakah/img_dedupe.git
```

With JPEG XL support:

```bash
pipx install "img-dedupe[jxl] @ git+https://github.com/sobakah/img_dedupe.git"
```

To install exactly this release, add `@0.1.0` to the end of the URL. Requires Python 3.9 or newer; Pillow and send2trash are installed automatically.

### Highlights

- **Two stages.** Exact copies are found by their content (SHA-256). Visually identical images are found by comparing the actual pixels, after a quick fingerprint check has picked the candidates.
- **Keeps the best copy.** Higher resolution first, then lossless over lossy, then your preferred format, then a name without a copy number like "(1)", then the less compressed file, then the oldest.
- **Safe by default.** Files go to the system trash, never straight to deletion. A dry run shows everything in advance and can then be applied without scanning again.
- **Traceable.** Every change is written to a log, with the file that was kept instead and the reason.
- **Interruptible.** Review progress is saved after every step, so you can stop at any time and continue later.

### Finding duplicates

- **What counts as the same picture:** re-saved and converted copies (JPEG, PNG, WebP, AVIF, TIFF, BMP, GIF, JPEG XL), downscaled copies, copies rotated by an EXIF tag, and copies saved with a different colour profile.
- **What does not:** crops, added text or watermarks, brightness and colour edits. Images with a different aspect ratio are never compared.
- **Strictness presets** `strict`, `normal` (default) and `loose` set how different two pictures may be; each match shows its score, so you can see how close it was.
- **Copy numbers:** when the better copy is called `photo (1).jpg`, it is renamed to `photo.jpg` after the other copy is removed. This only happens when another file in the same group shows that the number is a copy number, so names like `Holiday (12).jpg` in a series keep their number.

### Reviewing

- **Start screen** with every setting and all its choices visible, plus a folder list to choose which subfolders are scanned.
- **Numbered groups** (`[3/12]`) with the image to keep marked `▶`. Enter keeps the recommendation, a number keeps another image, `s` skips the group, and `v` opens the images in a viewer (system default, Identity, kitty or timg).
- **Overview** of all groups with their status, to jump back to skipped ones.
- **You decide how much to confirm:** only borderline matches (default), every group, or nothing (`--auto` for scripts).

### Safety and traceability

- **Delete modes:** trash (default), dry run, or permanent. Permanent deletion always asks first, with No as the default answer.
- **After a dry run,** you can apply exactly what it showed, or review the groups again, using the delete mode from your config.
- **The log** records every trashed, deleted or renamed file with its full path.
- **Saved sessions** survive quitting, Ctrl+C and crashes. Before a saved session or a dry run is carried out, every file is checked against the disk, and anything that changed in the meantime is left alone.

### Command line

`-r` include subfolders · `--exclude DIR` skip a subfolder · `--stages 1|2|both` · `--strictness strict|normal|loose` · `-i` confirm every group · `-y`/`--auto` no questions · `--dry-run` · `--no-rename` · `-c FILE` config file · `--color auto|always|never` · `-v` more logging

All settings can also be set in `config.json`; `config.example.json` lists every option with its default.

### Known limitations

- A copy that was *upscaled* counts as the better one, because it has the higher resolution.
- Very small watermarks on very large photos can disappear at the 512 px comparison size; raise `compare_size` or use `strict` if that matters for your pictures.
- During the pixel comparison, a thumbnail of every candidate image is held in memory at once (about 768 KB each). Folders with thousands of candidates can need several GB of RAM for this step.
- Progress is saved from the moment the duplicate groups are found; interrupting the analysis before that means the scan starts over.
- Dry runs and `--auto` runs are not saved as sessions.
- Animated images are only checked for exact copies.
- Developed and tested on Linux. macOS and Windows are supported by the code but have not been tested yet.

[1.1.0]: https://github.com/sobakah/keepbest/releases/tag/1.1.0
[1.0.0]: https://github.com/sobakah/keepbest/releases/tag/1.0.0
[0.5.1]: https://github.com/sobakah/keepbest/releases/tag/0.5.1
[0.5.0]: https://github.com/sobakah/keepbest/releases/tag/0.5.0
[0.4.0]: https://github.com/sobakah/keepbest/releases/tag/0.4.0
[0.3.0]: https://github.com/sobakah/keepbest/releases/tag/0.3.0
[0.2.1]: https://github.com/sobakah/keepbest/releases/tag/0.2.1
[0.2.0]: https://github.com/sobakah/keepbest/releases/tag/0.2.0
[0.1.0]: https://github.com/sobakah/keepbest/releases/tag/0.1.0
