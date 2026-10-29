# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] — 2026-10-29

Upgrade with `pipx upgrade keepbest`, or with the *Update* button on keepbest's page.

**Saved dry runs work differently now.** Coming back to a folder with a finished dry run, Enter *opens* it, as a dry run, instead of carrying it out. Carrying it out is the button *Carry it out* (`a`), and it always asks first, No by default. *Review its groups again for real* is gone.

### Added

- **The space keepbest and media-optimizer freed, added up.** Back from media-optimizer (1.1.0 and newer), its box has a tile *Freed together* next to *Space saved*, and the line below it says how much each freed.
- **A dry run's decisions can change.** Open a decided group from the overview and keep another file with its *Keep* button, or skip the group instead. The *Undo* button is gone; `u` still works.

### Changed

- **Coming back to a saved dry run opens it, as a dry run.** Enter on the start screen used to carry it out at once, and *Review its groups again* (*Review the groups again* right after a dry run) was a real run in which every decision moved files to the trash. Now Enter opens the dry run's groups overview, where every group can be looked at and its decision changed without touching anything. Carrying it out is a button of its own (`a`; Enter right after a dry run) that always asks first, No by default, in trash mode too. Remuxes skipped in a dry run are left alone when it is carried out.
- **Each file has its own *Keep* button**, next to its picture and in its row of the details (also without pictures, and for remuxed videos), instead of a *Keep this one* button with its number on each card and a big *Keep* button below the group. The recommended file is framed in both places; Enter keeps it, as before.
- **Groups belong to the overview.** Each group starts with *‹ Groups overview › Group 3 of 12*, and a group opened from the overview leads back to it once decided, skipped or looked at, with the next group not asked yet as *Continue*.
- **Comparing pixels is 5 to 6 times faster** on a computer with many cores, with the same results: pictures are compared in parallel, and each one is blurred once instead of once per comparison.
- **Every step of stage 3 counts.** With tens of thousands of pictures, keepbest could look frozen for many minutes after "Comparing pixels" had reached 100 %: that counter only loaded the pictures, and finding the candidate pairs and comparing their pixels showed nothing. Now each step has its own counter: *Finding candidates*, *Loading pictures* and *Comparing pixels*, which counts the candidate pairs and ends at the number shown afterwards.

### Fixed

- Switching a folder that has subfolders no longer makes the folder tree disappear: the question whether the folders below it change too appears over the tree, which stays visible, dimmed, until its new state is there. The same goes for the start screen's *Discard the saved session?*.
- An answer given just before the page was closed counts. Choosing *don't save progress* and closing the tab at once could stop keepbest at that question instead, and keep the progress.
- The summary's tiles all have the same height. A label on two lines ("Identical images removed" on a wide page) made its row taller than the others and moved its number up.

## [1.0.1] — 2026-10-29

Upgrade with `pipx upgrade keepbest`, or with the *Update* button on keepbest's page. To see media-optimizer's numbers, update it to 1.1.0 too (`pipx upgrade media-optimizer`).

### Added

- **media-optimizer's numbers next to keepbest's.** Back from media-optimizer (1.1.0 and newer), its box shows what it did: converted, space saved, skipped and failed as tiles like keepbest's summary, a line per stage, and the space keepbest and media-optimizer freed together. Until now keepbest only said that it had run. Closed without a run, keepbest says so.

### Changed

- A brighter, flatter icon in the style of picktag's: framed pictures in GNOME colours instead of the dark tile.
- media-optimizer is a step of its own: a section *Next · media-optimizer* after the summary and a step in the top bar. Once a run without its own page has ended, its output is folded away below its numbers.

## [1.0.0] — 2026-10-27

The first stable release. keepbest finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one. It asks its questions on a local page in the browser; `--auto` runs in the terminal without questions.

**Installing:** `pipx install git+https://github.com/sobakah/keepbest.git`; with JPEG XL support `pipx install "keepbest[jxl] @ git+https://github.com/sobakah/keepbest.git"`. Needs Python 3.9 or newer; ffmpeg for remuxed videos.

### What it does

- **Three stages.** Identical files (SHA-256, whatever their names and folders); remuxed videos (the same video stream in another container, with every other track, audio, subtitles, further video streams and data, compared by content and in order); visually identical images (the same picture in another format, quality or size, found by comparing pixels).
- **Keeps the best copy:** the highest resolution, lossless over lossy, your preferred format, a name without a copy number like "(1)", the less compressed file, the oldest. Each group says which of these made its file the recommended one.
- **Cautious.** Edited versions, watermarks, crops and the next frame of a burst are left alone; every removed image is compared with the one that is kept. Files with several pictures (MPO, multi-page TIFF) are only ever kept.
- **Safe by default.** Files go to the system trash; `--dry-run` never deletes or renames anything; permanent mode asks before it starts; the home folder, the folders above it and system folders are refused; every change is logged with the file kept instead and the reason.
- **A page to decide on.** A start screen with every setting and a folder tree to choose subfolders; each group with its pictures side by side or flipping between them, one zoom for all, and the spot where they differ most outlined; remuxed videos playable; an overview of all groups; *Open* and *Show in folder* for every file. Without a graphical session keepbest prints the page's address.
- **You decide what is borderline:** keep another file (the others are compared with it afresh), skip a group, mark images as not duplicates, or undo a decision in a dry run.
- **Interruptible.** Progress is saved as you go, so a review can be resumed, and a finished dry run can be carried out later without scanning again. Closing the page or Ctrl+C stops at the next question.
- **Works with other tools.** A file journal (`journal.jsonl`) tells programs like a tagger which file was removed for which kept one and which was renamed. [media-optimizer](https://github.com/sobakah/media-optimizer) can run from the end screen on the same folders; version 0.8 and newer takes over the tab and leads back.
- **One keepbest per folder**, and an app-menu entry (`--install-desktop-entry`, Linux) with "Open with keepbest" for folders.
- **Keeps itself up to date.** Once a day keepbest looks for a newer release (setting `check_for_updates`; it asks through git, with the same access as `pipx install`). The page shows the command and a link to what's new; installed with pipx, *Update* installs it on the start or end screen and keepbest starts again in the same tab. `--auto` prints the command.

## [0.6.0] — 2026-10-18

*Pre-release, kept for the record: keepbest asking its questions on screens in the terminal.*

### Added

- **Run media-optimizer afterwards** from the end screen, on the same folders as the run (needs media-optimizer 0.6).
- **A file journal for other programs** (`journal.jsonl` in the state folder, setting `journal_file`).
- **`i0`:** only the recommended image is different; the others are compared with each other afresh.

### Changed

- Renamed from img_dedupe to keepbest; the first start moves the old config and state folders over.
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

*Pre-release, kept for the record, under the project's first name.*

img_dedupe finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one, on screens in the terminal.

- **Images:** identical copies (SHA-256) and visually identical ones (pixel comparison, presets `strict`, `normal`, `loose`), with the keeper ranked by resolution, lossless format, preferred format, copy number, compression and age; copy numbers are removed from the kept file when a copy shows they are copy numbers.
- **Videos:** identical copies and remuxes (the same video stream in another container, audio and subtitle tracks compared by content).
- **Reviewing:** a start screen with every setting and a folder list, numbered groups, keep another file, skip, "not duplicates" marks, an overview, a viewer, and undo in dry runs.
- **Safety:** trash by default, dry runs saved and carried out later, permanent mode asks first, files checked against the disk before a saved session goes on, sessions survive Ctrl+C and crashes.
- **Stages** numbered 1 identical files, 2 remuxed videos, 3 visually identical images (`--stages`).

[1.1.0]: https://github.com/sobakah/keepbest/releases/tag/1.1.0
[1.0.1]: https://github.com/sobakah/keepbest/releases/tag/1.0.1
[1.0.0]: https://github.com/sobakah/keepbest/releases/tag/1.0.0
[0.6.0]: https://github.com/sobakah/keepbest/releases/tag/0.6.0
[0.5.1]: https://github.com/sobakah/keepbest/releases/tag/0.5.1
