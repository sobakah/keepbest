# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-10-27

The first stable release. keepbest finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one. It asks its questions on a local page in the browser; `--auto` runs in the terminal without questions.

The versions published as 1.0.0 to 2.1.4 between 2026-10-08 and 2026-10-26 were development versions. Their tags and releases were removed; the last one with terminal screens (1.3.0) is kept as pre-release 0.6.0, and everything since is part of this release. The git history still names the old numbers.

**Upgrading:** from any earlier keepbest, reinstall once, since `pipx upgrade` won't go from 2.1.4 down to 1.0.0:

```bash
pipx install --force git+https://github.com/sobakah/keepbest.git
```

Config, log, saved sessions and "not duplicates" marks keep working. Coming from 0.6.0 or another version with terminal screens: `--terminal` is gone, remove `"browser_page"` from `config.json` (ignored with a warning), and set `"viewer"` to `auto`, `identity` or `imagecompare` if it was `kitty` or `timg`. Journal lines written before this release carry the old number in `tool_version`. Coming from img_dedupe: see *Coming from img_dedupe?* in the README.

### What it does

- **Three stages.** Identical files (SHA-256, whatever their names and folders); remuxed videos (the same video stream in another container, with every other track, audio, subtitles, further video streams and data, compared by content and in order); visually identical images (the same picture in another format, quality or size, found by comparing pixels).
- **Keeps the best copy:** the highest resolution, lossless over lossy, your preferred format, a name without a copy number like "(1)", the less compressed file, the oldest. Each group says which of these made its file the recommended one.
- **Cautious.** Edited versions, watermarks, crops and the next frame of a burst are left alone; every removed image is compared with the one that is kept. Files with several pictures (MPO, multi-page TIFF) are only ever kept.
- **Safe by default.** Files go to the system trash; `--dry-run` never deletes or renames anything; permanent mode asks before it starts; the home folder, the folders above it and system folders are refused; every change is logged with the file kept instead and the reason.
- **A page to decide on.** A start screen with every setting and a folder tree to choose subfolders; each group with its pictures side by side or flipping between them, one zoom for all, and the spot where they differ most outlined; remuxed videos playable; an overview of all groups; *Open* and *Show in folder* for every file. Without a graphical session keepbest prints the page's address.
- **You decide what is borderline:** keep another file (the others are compared with it afresh), skip a group, mark images as not duplicates, or undo a decision in a dry run.
- **Interruptible.** Progress is saved as you go, so a review can be resumed, and a finished dry run can be carried out later without scanning again. Closing the page or Ctrl+C stops at the next question.
- **Works with other tools.** A file journal (`journal.jsonl`) tells programs like a tagger which file was removed for which kept one and which was renamed. [media-optimizer](https://github.com/sobakah/media-optimizer) can run from the end screen on the same folders; version 2.4 and newer takes over the tab and leads back.
- **One keepbest per folder**, and an app-menu entry (`--install-desktop-entry`, Linux) with "Open with keepbest" for folders.

### Changes since 0.6.0

- **Added:** the page and everything on it listed above; the app-menu entry; the lock against two keepbest on one folder; options `--no-browser` and `--web` (accepted, changes nothing).
- **Removed:** the terminal screens (`--terminal`, `"browser_page"`) and the terminal viewers `kitty` and `timg`.
- **Fixed:** permanent mode's No after choosing another file to keep left the group rebuilt around that file; dry runs counted some renames wrong; a remux was missed when ffmpeg crashed on another track or the container had no duration in its header; a file that vanished while being hashed stopped the scan; a folder name that isn't valid UTF-8 crashed saving the session; float TIFFs with negative infinite values were silently left out; a resumed session forgot `prefer_jxl`; Ctrl+C in the terminal didn't end keepbest while the page waited.

## [0.6.0] — 2026-10-18

*Pre-release; published as keepbest 1.3.0, which this version reports when installed. It sums up the development versions published as keepbest 1.0.0 to 1.3.0. The last version with terminal screens.*

**Renamed from img_dedupe to keepbest.** The command, package, repository (`github.com/sobakah/keepbest`; the old address forwards there), config and state folders and the marks file (`.keepbest_ignore.json`) carry the new name. The first start moves `~/.config/img_dedupe/` and `~/.local/state/img_dedupe/` over; existing folders under the new name are never overwritten, and `.img_dedupe_ignore.json` and `IMG_DEDUPE_STATE_DIR` keep working. Release tags have no `v`.

**Upgrading from img_dedupe:** `pipx upgrade` can't follow a rename (it stops with *inconsistent name* and changes nothing). Switch once:

```bash
pipx uninstall img-dedupe
pipx install git+https://github.com/sobakah/keepbest.git
```

### Added

- **Run media-optimizer afterwards** from the end screen, on the same folders as the run (needs media-optimizer 2.2).
- **A file journal for other programs** (`journal.jsonl` in the state folder, setting `journal_file`).
- **`i0`:** only the recommended image is different; the others are compared with each other afresh.

### Changed

- The folder list counts images and videos apart, and switching a folder asks before it takes its subfolders along.
- Every track of a video counts in Stage 2, in order; copies with the same tracks in another order are asked about.
- "Not duplicates" marks separate two images, not a group. MPO and multi-page TIFFs take part in Stage 3 but are only kept.
- More folders are refused: every folder above home and system folders.

### Fixed

- A dark 16-bit greyscale picture could be deleted as a duplicate of a brighter version of itself.
- A video with a second video stream could be removed as a remux of a copy with only the first one.
- Carrying out a dry run failed on a remux Stage 1 had renamed; `--exclude` with `..` was silently not applied.
- A file name that isn't valid UTF-8 crashed the log; typos on the end screen or in a yes/no question counted as an answer; the JPEG XL hint named the wrong install command.

## [0.5.1] — 2026-10-05

*Pre-release; published as img_dedupe 1.4.1. It sums up the img_dedupe pre-releases 0.1.0 to 0.5.1 (first published as 1.0 to 1.4.1, 2026-09-23 to 2026-10-05).*

img_dedupe finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one, on screens in the terminal.

- **Images:** identical copies (SHA-256) and visually identical ones (pixel comparison, presets `strict`, `normal`, `loose`), with the keeper ranked by resolution, lossless format, preferred format, copy number, compression and age; copy numbers are removed from the kept file when a copy shows they are copy numbers.
- **Videos:** identical copies and remuxes (the same video stream in another container, audio and subtitle tracks compared by content).
- **Reviewing:** a start screen with every setting and a folder list, numbered groups, keep another file, skip, "not duplicates" marks, an overview, a viewer, and undo in dry runs.
- **Safety:** trash by default, dry runs saved and carried out later, permanent mode asks first, files checked against the disk before a saved session goes on, sessions survive Ctrl+C and crashes.
- **Stages** numbered 1 identical files, 2 remuxed videos, 3 visually identical images (`--stages`).

[1.0.0]: https://github.com/sobakah/keepbest/releases/tag/1.0.0
[0.6.0]: https://github.com/sobakah/keepbest/releases/tag/0.6.0
[0.5.1]: https://github.com/sobakah/keepbest/releases/tag/0.5.1
