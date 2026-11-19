# Changelog

All notable changes to keepbest are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **A D-Bus interface** (`io.github.sobakah.keepbest`), so a program inside a Flatpak, such as
  picktag, can start keepbest: its page on a folder, on the folders with copies one after the
  other, or on groups of files to compare. Every call only opens the page; you decide there what
  is removed. `--install-dbus-service` lets the session bus start keepbest when a program calls
  it, `--remove-dbus-service` undoes that.
- **`--install`** writes the app menu entry and the D-Bus service in one go, **`--uninstall`**
  removes both again (before `pipx uninstall keepbest`).

### Fixed

- **Find Duplicates (`--groups`) left out every file on a removable drive**, and on Fedora
  Silverblue and other image-based systems every file in the home folder: they were taken for
  system files.
- **A `--groups` run removed less than its dry run showed** when the kept file lost its copy
  number in the first stage: the renamed file was no longer compared with the group's other
  pictures.
- After carrying out a dry run, keepbest ended with exit code 0 even when files could not be
  removed; it is 1 now.
- `--exclude` with `--groups` was remembered for later runs; it applies to that run only.
- With `--groups`, an absolute `--exclude` in a folder reached through a symlink was ignored as
  "not inside".
- Two `--groups` runs at the same time could lose each other's "not duplicates" marks.
- In a toolbox or distrobox on a host without Flatpak, a Flatpak comparison app was reported as
  not installed instead of Flatpak as missing.

## [1.0.2] — 2026-11-14

### Added

- **Fedora Silverblue, Bluefin and other image-based systems:** keepbest runs on the host with
  pipx, or in a toolbox or distrobox, whose app menu entry starts it in the container; from
  there the page, the viewers and the file manager are the host's. The README has a section for
  these systems, also on building the JPEG XL plugin there.
- In a container, a media-optimizer installed only on the host is reported as such.

### Fixed

- The JPEG XL hint no longer offers `dnf` on an image-based system.

## [1.0.1] — 2026-11-11

### Added

- **`--permanent`** deletes permanently instead of moving files to the trash, for that run.

### Changed

- **`--auto` deletes permanently only with `--permanent`.** With `delete_mode` `permanent` in the
  config file alone, an `--auto` run moves files to the trash and says so: it asks nothing, not even
  for borderline groups, so a config meant for the page no longer deletes for good in a script.

## [1.0.0] — 2026-11-11

The first release. keepbest finds duplicate pictures and videos in a folder and removes the
redundant copies, keeping the best one. You choose the settings and decide on borderline groups on
a page in your browser; `--auto` runs in the terminal without questions.

### Features

- **Three stages.** Identical files (SHA-256, whatever their names and folders); remuxed videos (the
  same video stream in another container, with every other track – audio, subtitles, further video
  streams, data – compared by content and in order); visually identical pictures (the same picture
  in another format, quality or size, found by comparing pixels).
- **Keeps the best copy:** the highest resolution, lossless over lossy, your preferred format, a
  name without a copy number like "(1)", the less compressed file, the oldest. Each group says which
  of these made its file the recommended one, and a copy number is removed from the kept file when
  a copy shows it is one.
- **Cautious.** Edited versions, watermarks, crops and the next frame of a burst are left alone;
  every removed picture is compared with the one that is kept. Files with several pictures (MPO,
  multi-page TIFF) are only ever kept. 16-bit greyscale pictures are compared on one brightness
  scale.
- **Safe by default.** Files go to the trash; `--dry-run` never deletes or renames anything;
  permanent mode asks before it starts and at every decision; the home folder, the folders above it
  and system folders are refused; every change is logged with the file kept instead and the reason.
- **A page to decide on.** A start screen with every setting and a folder tree to choose
  subfolders; each group with its pictures side by side or flipping between them, one zoom for all
  and the most different spot outlined; remuxed videos playable; an overview of all groups; a
  *Keep* button, *Open* and *Show in folder* for every file. Without a graphical session keepbest
  prints the page's address.
- **You decide what is borderline:** keep another file (the others are compared with it afresh),
  skip a group, or mark pictures as not duplicates; the marks stay with the folder.
- **Dry runs you can come back to.** A finished dry run is saved: open it again to look at its
  groups and change decisions, or carry out exactly what it showed, without scanning again. It
  always asks first.
- **Interruptible.** Progress is saved as you go, so a review can be resumed; closing the page or
  Ctrl+C stops at the next question, and files are checked against the disk before anything goes on.
- **picktag's *Find Duplicates*** (`--groups FILE`): only the files of each group are compared,
  every pair by its pixels, with folders to leave out.
- **media-optimizer afterwards.** After a run that removed files, media-optimizer can run on the
  same folders: its page takes over the tab and leads back, and its numbers are shown next to
  keepbest's, with the space both freed together.
- **A file journal** (`journal.jsonl`) tells programs like picktag which file was removed for which
  kept one and which was renamed.
- **Keeps itself up to date.** At every start keepbest looks for a newer release; installed with
  pipx, *Update* on the page installs it and keepbest starts again in the same tab.
- **One keepbest per folder**, and an app-menu entry (`--install-desktop-entry`, Linux) with *Open
  with keepbest* for folders.
- **Installing:** `pipx install git+https://github.com/sobakah/keepbest.git`; with JPEG XL support
  `pipx install "keepbest[jxl] @ git+https://github.com/sobakah/keepbest.git"`. Needs Python 3.9 or
  newer, and ffmpeg for remuxed videos.

[Unreleased]: https://github.com/sobakah/keepbest/compare/1.0.2...HEAD
[1.0.2]: https://github.com/sobakah/keepbest/compare/1.0.1...1.0.2
[1.0.1]: https://github.com/sobakah/keepbest/compare/1.0.0...1.0.1
[1.0.0]: https://github.com/sobakah/keepbest/releases/tag/1.0.0
