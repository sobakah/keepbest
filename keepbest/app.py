"""Interactive workflow: folder selection, start screen, running and summary."""

from __future__ import annotations

import os
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import handoff, running
from .config import NAME, __version__
from .core_logic import (ExactGroup, Group, RunContext, Stats, UserQuit, stats_from_session,
                        apply_dry_run, find_exact_duplicates, find_remuxes, find_similar_images,
                        dry_run_from_session, planned_changes,
                        resolve_limit, resume_review, review_dry_run_again)
from .front import (AfterDryRunScreen, EndScreen, FolderEntry, FolderHeader, FolderRow, FolderScreen, Note,
                    SessionPanel, SettingRow, StartScreen, ask, ask_path, blank, confirm, error, exit_script,
                    info, page_address, show, success, transient, warn)
from .image_utils import IMAGE_EXTENSIONS, STRICTNESS_PRESETS
from .video import VIDEO_EXTENSIONS, is_video
from .video import tools_available as video_tools_available
from .journal import Journal, resolve_journal_path
from .session import ActionLog, IgnoreList, SessionStore, resolve_log_path

_NATURAL_RE = re.compile(r"(\d+)")
ROOT = "."

# Choices per setting, in the order the setting key cycles through them.
STAGE_OPTIONS = {"1,2,3": "all", "1,2": "identical copies (1+2)", "3": "visual only (3)"}
_LEGACY_STAGES = {"both": "1,2,3", "1": "1,2", "2": "3"}  # values stored by older sessions


def stage_options(current: str) -> dict:
    """The start screen's stage choices, plus *current* if it is another combination."""
    if current in STAGE_OPTIONS:
        return STAGE_OPTIONS
    return {**STAGE_OPTIONS, current: f"stages {current}"}


def session_stages(matching: dict, fallback: str) -> str:
    stages = matching.get("stages", fallback)
    return _LEGACY_STAGES.get(stages, stages) if matching.get("stages_version") != 2 else stages
CONFIRM_OPTIONS = {"uncertain": "borderline only", "always": "every group", "never": "never"}
MODE_OPTIONS = {"trash": "trash", "dry_run": "dry run", "permanent": "permanent"}
VIEWER_OPTIONS = {"auto": "system viewer", "identity": "Identity", "imagecompare": "Image Compare"}
SUBFOLDER_OPTIONS = {False: "excluded", True: "included"}
RENAME_OPTIONS = {True: "on", False: "off"}
VIDEO_OPTIONS = {True: "included", False: "excluded"}


# --- file discovery ----------------------------------------------------------
def natural_key(path: Path):
    """Sort 'IMG_2' before 'IMG_10' instead of lexicographically."""
    parts = _NATURAL_RE.split(path.name.lower())
    return (str(path.parent).lower(), [int(p) if p.isdigit() else p for p in parts])


def _natural_parts(rel: str):
    return [[int(p) if p.isdigit() else p for p in _NATURAL_RE.split(part.lower())]
            for part in PurePosixPath(rel).parts]


def folder_of(path: Path, base: Path) -> str:
    """Folder of *path* relative to *base* in '/' form; '.' for base itself."""
    return path.parent.relative_to(base).as_posix()


def find_images(base_dir: Path, recursive: bool, excluded=frozenset(), videos: bool = True) -> list[Path]:
    """Image files, skipping hidden files and folders (.thumbnails, .Trash, ...)
    and never following symlinks. *excluded* folders (relative, '/' form) only
    apply to recursive scans; '.' is the main folder itself."""
    extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS if videos else IMAGE_EXTENSIONS
    found: list[Path] = []
    for root, dirs, files in os.walk(base_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if recursive else []
        root_path = Path(root)
        rel = root_path.relative_to(base_dir).as_posix()
        if recursive and rel in excluded:
            continue
        for name in files:
            if name.startswith("."):
                continue
            path = root_path / name
            if path.suffix.lower() in extensions and not path.is_symlink():
                found.append(path)
    return sorted(found, key=natural_key)


class FolderIndex:
    """All images below a folder, cached so screens can redraw instantly."""

    def __init__(self):
        self._cache: dict[Path, list[Path]] = {}

    def images(self, base_dir: Path) -> list[Path]:
        """All images and videos below *base_dir* (filtered by the callers)."""
        if base_dir not in self._cache:
            with transient("Counting files..."):
                self._cache[base_dir] = find_images(base_dir, recursive=True, videos=True)
        return self._cache[base_dir]

    def folder_counts(self, base_dir: Path, videos: bool = True) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in self.images(base_dir):
            if videos or not is_video(path):
                rel = folder_of(path, base_dir)
                counts[rel] = counts.get(rel, 0) + 1
        return counts

    def folder_kinds(self, base_dir: Path) -> dict[str, tuple[int, int]]:
        """(images, videos) directly in each folder, relative in '/' form."""
        kinds: dict[str, tuple[int, int]] = {}
        for path in self.images(base_dir):
            rel = folder_of(path, base_dir)
            images, videos = kinds.get(rel, (0, 0))
            kinds[rel] = (images, videos + 1) if is_video(path) else (images + 1, videos)
        return kinds

    def in_scope_files(self, base_dir: Path, settings: dict) -> list[Path]:
        files = []
        for path in self.images(base_dir):
            if is_video(path) and not settings.get("videos", True):
                continue
            rel = folder_of(path, base_dir)
            if not settings["recursive"]:
                if rel == ROOT:
                    files.append(path)
            elif rel not in settings["excluded"]:
                files.append(path)
        return files

    def in_scope(self, base_dir: Path, settings: dict) -> int:
        return len(self.in_scope_files(base_dir, settings))

    def clear(self) -> None:
        self._cache.clear()


# System folders: never picture folders, and files there must not be touched.
SYSTEM_FOLDERS = {"/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc", "/run",
                  "/sbin", "/srv", "/sys", "/usr", "/var",
                  "/Applications", "/Library", "/System", "/Volumes", "/private"}   # macOS


def is_protected(path: Path) -> bool:
    """The home folder, a folder above it (/home, /Users), the root folder or a
    system folder: never scanned, to prevent large-scale accidents. Compared with
    symlinks resolved, so a home reached through one (Fedora Atomic's /home ->
    /var/home) counts too."""
    path = path.resolve()
    home = Path.home()
    homes = {home, home.resolve()}
    if path in homes or any(path in h.parents for h in homes):
        return True
    return path == Path(path.anchor) or path.as_posix() in SYSTEM_FOLDERS


def protected_message(path: Path) -> str:
    """The one message for a refused folder, with and without --auto."""
    return (f"The home folder, the folders above it, the root folder and system folders ({path}) are not "
            f"scanned, to prevent large-scale deletions by accident. Please choose a specific picture folder.")


def resolve_directory(initial: Path | None, index: FolderIndex,
                      allow_cancel: bool = False, start: Path | None = None) -> Path | None:
    """Find a usable folder, prompting until one is given (lrckit behaviour).

    With *allow_cancel* an empty answer returns None instead of quitting.
    *start*: the folder the picker opens (the current one when changing).
    """
    current = initial
    while True:
        if current is not None:
            resolved = current.expanduser().resolve()
            if is_protected(resolved):
                error("\n" + protected_message(resolved))
            elif resolved.is_dir():
                if index.images(resolved):
                    other = running.claim(resolved)
                    if other is None:
                        return resolved
                    error("\n" + running.busy_message(other))
                else:
                    warn(f"\nNo supported images or videos found in '{resolved}' (including subfolders).")
            elif not resolved.exists():
                error(f"\nPath does not exist: {resolved}")
            else:
                error(f"\nPath is not a directory: {resolved}")

        raw = ask_path("Picture folder (empty to cancel): " if allow_cancel else "Picture folder: ", start)

        cleaned = raw.strip().strip("'\" ")
        if not cleaned or cleaned.lower() == "q":
            if allow_cancel:
                return None
            exit_script()
        current = Path(cleaned)


# --- folder picker -----------------------------------------------------------
def media_text(images: int, videos: int, videos_scanned: bool = True) -> str:
    """'3 images · 1 video', '2 videos', ... or '' when there are none."""
    parts = [f"{images} image{'s' if images != 1 else ''}"] if images else []
    if videos:
        parts.append(f"{videos} video{'s' if videos != 1 else ''}" + ("" if videos_scanned else " (not scanned)"))
    return " · ".join(parts)


def folder_entries(base_dir: Path, index: FolderIndex) -> list[tuple[str, int]]:
    """(relative folder, images directly in it) for every subfolder holding images,
    plus the folders above them so the list reads like a tree. The main folder is
    not listed: it is always scanned."""
    counts = index.folder_counts(base_dir, videos=True)
    folders = set(counts)
    for rel in counts:
        if rel != ROOT:
            parts = PurePosixPath(rel).parts
            folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
    ordered = sorted((f for f in folders if f != ROOT), key=_natural_parts)
    return [(rel, counts.get(rel, 0)) for rel in ordered]


def _below(rel: str, entries) -> list[str]:
    """*rel* and every listed folder beneath it."""
    return [r for r, _ in entries if r == rel or r.startswith(rel + "/")]


def parse_numbers(text: str, upper: int) -> list[int] | None:
    """'3', '2-5', '1,4 7' -> numbers in 1..upper, or None when invalid."""
    numbers = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            return None
        low, high = int(match[1]), int(match[2] or match[1])
        low, high = min(low, high), max(low, high)
        if low < 1 or high > upper:
            return None
        numbers.extend(range(low, high + 1))
    return numbers or None


def switch_folders(excluded: set, entries: list[tuple[str, int]], numbers: list[int],
                   with_subfolders: bool = True) -> None:
    """Switch the listed folders (1-based numbers into *entries*) in *excluded*.

    Every listed folder switches on its own, judged by its state before this
    input ("4,1" selects 4 and unselects 1). With *with_subfolders*, a folder
    takes the folders below it along; parents go first, so a listed subfolder's
    own switch wins over its parent's."""
    listed = {entries[n - 1][0] for n in numbers}
    targets = {rel: rel in excluded for rel in listed}  # True = switch on
    for rel in sorted(listed, key=lambda r: r.count("/")):
        for below in (_below(rel, entries) if with_subfolders else [rel]):
            if targets[rel]:
                excluded.discard(below)
            else:
                excluded.add(below)


def subfolders_along(excluded: set, entries: list[tuple[str, int]], numbers: list[int]) -> tuple[list[str], int]:
    """What switching the listed folders would change below them: the listed
    folders concerned and how many subfolders would change with them. ([], 0)
    when the subfolders already have that state or are listed themselves."""
    alone, along = set(excluded), set(excluded)
    switch_folders(alone, entries, numbers, with_subfolders=False)
    switch_folders(along, entries, numbers)
    changed = alone ^ along
    listed = [entries[n - 1][0] for n in sorted(set(numbers))]
    parents = [rel for rel in listed if any(c.startswith(rel + "/") for c in changed)]
    return parents, len(changed)


def choose_folders(base_dir: Path, index: FolderIndex, settings: dict) -> None:
    """Tree of subfolders with on/off boxes; edits settings['excluded'] in place.

    The main folder is switched only with its own key (m): numbers, ranges,
    "all" and "none" change subfolders only, so it can't be left out by accident."""
    excluded: set[str] = settings["excluded"]
    while True:
        entries = folder_entries(base_dir, index)
        kinds = index.folder_kinds(base_dir)
        videos_on = settings.get("videos", True)
        main_on = ROOT not in excluded
        files_text = f"{media_text(*kinds.get(ROOT, (0, 0)), videos_on) or 'no files'} directly in it"
        header = FolderHeader(count=len(entries), main_on=main_on, files_text=files_text)
        if not entries:
            show("folder_header", header=header)
            info("This folder has no subfolders with images or videos.")
            if ROOT in excluded:
                excluded.discard(ROOT)
                info("The main folder was switched back on, since it is the only folder.")
            return

        listed = []
        for rel, _ in entries:
            parts = PurePosixPath(rel).parts
            listed.append(FolderEntry(rel=rel, name="  " * (len(parts) - 1) + parts[-1] + "/",
                                      on=rel not in excluded,
                                      amount=media_text(*kinds.get(rel, (0, 0)), videos_on) or "—"))
        chosen = sum(1 for rel, _ in entries if rel not in excluded)
        scope = index.in_scope_files(base_dir, settings)
        scope_videos = sum(1 for path in scope if is_video(path))
        choice = ask(FolderScreen(
            prompt="Folders: ", primary="Done",
            menu=[("Select", [
                (f"1-{len(entries)}", "Switch a folder on/off (asks about its subfolders)"),
                ("a", "Select all subfolders"),
                ("n", "Select no subfolders"),
                ("m", "Switch the main folder on/off"),
            ])],
            header=header, entries=listed, chosen=chosen,
            scope_text=media_text(len(scope) - scope_videos, scope_videos) or "no files"))
        if choice == "":
            if chosen == 0 and not main_on:
                warn("Nothing selected - no files would be scanned.")
            elif chosen == 0:
                warn("No subfolder selected - only the main folder will be scanned.")
            return
        if choice == "m":
            if main_on:
                excluded.add(ROOT)
            else:
                excluded.discard(ROOT)
        elif choice == "a":  # subfolders only: the main folder keeps its own state
            excluded.difference_update(rel for rel, _ in entries)
        elif choice == "n":
            excluded.update(rel for rel, _ in entries)
        else:
            numbers = parse_numbers(choice, len(entries))
            if numbers is None:
                warn("Unknown option.")
                continue
            parents, count = subfolders_along(excluded, entries, numbers)
            along = True
            if parents:
                names = ", ".join(f"{rel}/" for rel in parents)
                along = confirm(f"Also switch the {count} subfolder{'s' if count != 1 else ''} below {names}?",
                                default=True)
            switch_folders(excluded, entries, numbers, with_subfolders=along)


# --- start screen ------------------------------------------------------------
def _cycle(value, options):
    options = list(options)
    return options[(options.index(value) + 1) % len(options)] if value in options else options[0]


def _cycle_available(value, options, unavailable=()):
    """Next choice after *value*, skipping unavailable ones."""
    keys = [k for k in options if k not in unavailable]
    if not keys:
        return value
    if value not in keys:
        return keys[0]
    return keys[(keys.index(value) + 1) % len(keys)]


def session_limit(saved: dict) -> int:
    """The pixel-difference limit a saved session was matched with."""
    limit = (saved.get("matching") or {}).get("limit")
    if isinstance(limit, int):
        return limit
    return max((g.get("limit", 20) for g in saved.get("groups", [])), default=20)


def strictness_choices(config: dict, cap: int | None) -> tuple[dict, str, set, int]:
    """Options for the strictness row: (options, current, unavailable, effective limit).

    With *cap* (a saved session's limit), only that limit or stricter ones can
    be used, since loosening would need a new scan."""
    limit, preset = resolve_limit(config)
    effective = limit if cap is None else min(limit, cap)
    options = {name: f"{name} ({value})" for name, value in STRICTNESS_PRESETS.items()}
    by_value = {value: name for name, value in STRICTNESS_PRESETS.items()}
    current = by_value.get(effective, "custom")
    if current == "custom":
        options = {"custom": f"custom ({effective})", **options}
    if cap is not None and cap not in by_value and cap != effective:
        options["saved"] = f"session ({cap})"
    unavailable = {name for name, value in STRICTNESS_PRESETS.items() if cap is not None and value > cap}
    return options, current, unavailable, effective


def carry_out_mode(config: dict) -> str:
    """Delete mode for carrying out a dry run: the start screen's choice, unless
    that is a dry run itself, then the configured real mode."""
    return config["delete_mode"] if config["delete_mode"] != "dry_run" else real_delete_mode(config)


def matching_settings(config: dict, settings: dict, target: Path, index: FolderIndex) -> dict:
    """What decided the groups of a session; stored with it and shown on resume."""
    limit, preset = resolve_limit(config)
    if not settings["recursive"]:
        folders = "this folder only"
    else:
        entries = folder_entries(target, index)
        chosen = sum(1 for rel, _ in entries if rel not in settings["excluded"])
        main = "main folder" if ROOT not in settings["excluded"] else "main folder excluded"
        folders = f"{main} + " + ("all subfolders" if chosen == len(entries) else f"{chosen} of {len(entries)} subfolders")
    return {"limit": limit, "preset": preset, "stages": settings["stages"], "stages_version": 2,
            "recursive": settings["recursive"], "excluded": sorted(settings["excluded"]),
            "videos": settings.get("videos", True),
            "description": f"{preset} ({limit}) · {folders}"}


def session_settings(settings: dict, saved: dict | None) -> dict:
    """*settings* with the folder choice a saved session was scanned with: which
    folders and file kinds its groups come from, what the start screen counts and
    what media-optimizer gets afterwards. Older sessions without it keep *settings*."""
    matching = (saved or {}).get("matching") or {}
    result = dict(settings)
    if "recursive" in matching:
        result["recursive"] = bool(matching["recursive"])
    if isinstance(matching.get("excluded"), list):
        result["excluded"] = set(matching["excluded"])
    if "videos" in matching:
        result["videos"] = bool(matching["videos"])
    return result


def saved_rename(saved: dict, config: dict) -> bool:
    """Whether carrying out a saved dry run strips "(1)": as the dry run did."""
    return bool(saved.get("rename", config["rename_numbered"]))


def session_state(saved: dict) -> tuple[bool, bool, int]:
    """(is a dry run, dry run finished, open groups) of a saved session."""
    open_ = sum(g["status"] in ("pending", "skipped") for g in saved["groups"])
    dry = bool(saved.get("dry_run"))
    return dry, dry and (bool(saved.get("finished")) or open_ == 0), open_


def saved_dry_run(saved: dict, rename: bool) -> "DryRun":
    exact, groups = dry_run_from_session(saved)
    result = DryRun(exact_groups=exact, groups=groups, scanned=int(saved.get("scanned", 0)))
    result.to_remove, result.to_rename = planned_changes(exact, groups, rename)
    return result


def session_panel(saved: dict, rename: bool) -> SessionPanel:
    groups = saved["groups"]
    dry, finished, open_ = session_state(saved)
    try:
        when = datetime.fromisoformat(saved.get("updated", "")).strftime("%d %b %Y %H:%M")
    except ValueError:
        when = "earlier"
    panel = SessionPanel(dry=dry, finished=finished, when=when, groups=len(groups),
                         done=sum(g["status"] == "done" for g in groups),
                         skipped=sum(g["status"] == "skipped" for g in groups),
                         ignored=sum(g["status"] == "ignored" for g in groups), open=open_,
                         description=saved.get("matching", {}).get("description"))
    if finished:
        plan = saved_dry_run(saved, rename)
        panel.groups = len(groups) + len(saved.get("exact_groups", []))
        panel.to_remove, panel.to_rename = plan.to_remove, plan.to_rename
    return panel


def _fate(mode: str, count: int) -> str:
    return {"trash": f"move {count} file(s) to the trash",
            "permanent": f"permanently delete {count} file(s)"}.get(mode, f"remove {count} file(s)")


def start_screen(target: Path, config: dict, settings: dict, index: FolderIndex,
                 store: SessionStore | None) -> tuple[str, dict | None]:
    """Show folder, saved session and settings.

    Returns (action, saved session) with action one of 'start', 'resume',
    'apply_saved', 'review_saved' or 'change'.
    """
    while True:
        saved = store.load() if store else None
        saved_dry, saved_finished, open_groups = session_state(saved) if saved else (False, False, 0)
        # With a saved session, the settings that decided its groups are fixed;
        # "n" discards the session and unlocks everything for a new scan.
        session = bool(saved and (open_groups or saved_finished))
        shown = session_settings(settings, saved) if session else settings
        counts = index.folder_counts(target, videos=shown["videos"])
        direct, total = counts.get(ROOT, 0), sum(counts.values())
        scope_files = index.in_scope_files(target, shown)
        in_scope = len(scope_files)
        scope_videos = sum(1 for p in scope_files if is_video(p))

        # --dry-run on the command line always wins: a saved real session or a
        # finished dry run would delete files, so they can't be continued here.
        forced_dry = bool(config.get("_cli_dry_run"))
        blocked = None
        if forced_dry and session and saved_finished:
            blocked = ("--dry-run was given, so the saved dry run is not carried out. Start keepbest "
                       "without --dry-run to carry it out, or start over to discard it and scan again.")
        elif forced_dry and session and not saved_dry:
            blocked = ("--dry-run was given, but the saved session is a real run that would delete files. "
                       "Start keepbest without --dry-run to resume it, or start over to discard it.")
        matching = (saved or {}).get("matching") or {}
        fixed = "fixed by the saved session"
        cap = session_limit(saved) if session and not saved_finished else None
        strictness, strict_current, strict_unavailable, _ = strictness_choices(config, cap)
        if forced_dry or (session and saved_dry and not saved_finished):
            mode = "dry_run"
        elif session:
            mode = carry_out_mode(config) if saved_finished else resume_mode(saved, config, quiet=True)
        else:
            mode = config["delete_mode"]

        rows = []
        if session:
            stages = session_stages(matching, settings["stages"])
            rows.append(SettingRow("1", "Stages", stage_options(stages), stages, locked=fixed))
        else:
            rows.append(SettingRow("1", "Stages", stage_options(settings["stages"]), settings["stages"]))
        if saved_finished:
            locked_limit = session_limit(saved)
            rows.append(SettingRow("2", "Strictness", {"s": f"limit {locked_limit}"}, "s",
                                   locked="carried out as the dry run showed"))
        else:
            rows.append(SettingRow("2", "Strictness", strictness, strict_current, unavailable=strict_unavailable))
            if cap is not None:
                rows.append(Note(f"Stricter than the session's limit ({cap}) works without a new scan; looser needs one."))
        rows.append(SettingRow("3", "Confirm", CONFIRM_OPTIONS, settings["confirm"]))
        if forced_dry:
            rows.append(SettingRow("4", "Delete mode", MODE_OPTIONS, "dry_run", locked="--dry-run on the command line"))
        elif session and saved_dry and not saved_finished:
            rows.append(SettingRow("4", "Delete mode", MODE_OPTIONS, "dry_run", locked="a dry run resumes as a dry run"))
        elif session:
            rows.append(SettingRow("4", "Delete mode", MODE_OPTIONS, mode, danger="permanent", unavailable={"dry_run"}))
        else:
            rows.append(SettingRow("4", "Delete mode", MODE_OPTIONS, mode, danger="permanent"))
        rows.append(SettingRow("5", "Viewer", VIEWER_OPTIONS, config["viewer"]))
        if saved_finished:
            rows.append(SettingRow("6", "Rename (1)", RENAME_OPTIONS, saved_rename(saved, config),
                                   locked="carried out as the dry run showed"))
        else:
            rows.append(SettingRow("6", "Rename (1)", RENAME_OPTIONS, config["rename_numbered"]))
        if session:
            rows.append(SettingRow("7", "Videos", VIDEO_OPTIONS, matching.get("videos", settings["videos"]), locked=fixed))
            rows.append(SettingRow("r", "Subfolders", SUBFOLDER_OPTIONS, matching.get("recursive", settings["recursive"]),
                                   locked=fixed))
        else:
            rows.append(SettingRow("7", "Videos", VIDEO_OPTIONS, settings["videos"]))
            if settings["videos"] and not video_tools_available():
                rows.append(Note("ffmpeg not found: identical video copies are found, remuxes are not."))
            rows.append(SettingRow("r", "Subfolders", SUBFOLDER_OPTIONS, settings["recursive"]))
        if session and matching.get("recursive"):
            description = matching.get("description", "").split(" · ", 1)[-1]
            rows.append(SettingRow("f", "Folders", {"d": description}, "d", locked=fixed))
        elif not session and settings["recursive"]:
            entries = folder_entries(target, index)
            rows.append(FolderRow("f", "Folders", main_on=ROOT not in settings["excluded"],
                                  chosen=sum(1 for rel, _ in entries if rel not in settings["excluded"]),
                                  total=len(entries)))
        if session:
            rows.append(Note("Locked settings belong to the saved session. "
                             "Start over to change them all.", style="hint"))
        if mode == "dry_run":
            rows.append(Note("Dry runs change nothing and are not logged; their progress is saved."))

        primary = None
        if blocked:
            pass
        elif saved and saved_finished:
            plan = saved_dry_run(saved, saved_rename(saved, config))
            what = _fate(carry_out_mode(config), plan.to_remove)
            if plan.to_rename:
                what += f", rename {plan.to_rename}"
            primary = f"Carry out the saved dry run: {what}"
        elif saved and open_groups:
            label = "dry run" if saved_dry else "session"
            primary = f"Resume saved {label} ({open_groups} open group(s))"
        elif in_scope >= 2:
            primary = f"Start scan ({in_scope} files)"
        else:
            hidden_videos = 0 if shown["videos"] else index.in_scope(target, {**shown, "videos": True}) - in_scope
            rows.append(Note(f"Nothing to scan here - {'include videos, ' if hidden_videos else ''}"
                             "include subfolders or change the directory."))

        if session:
            settings_menu = [("1-7", "Next choice (not greyed-out ones)")]
        else:
            settings_menu = [("1-7", "Next choice for a setting"), ("r", "Toggle subfolders")]
            if settings["recursive"]:
                settings_menu.append(("f", "Choose folders"))
        groups_menu = [("Settings", settings_menu)]
        if saved:
            session_menu = [("n", "Start over: discard the session, unlock all settings")]
            if saved_finished and not forced_dry:
                session_menu.insert(0, ("e", "Review its groups again for real"))
            groups_menu.append(("Session", session_menu))
        groups_menu.append(("Navigate", [("c", "Change directory"), ("q", "Quit")]))

        choice = ask(StartScreen(
            prompt="Action: ", primary=primary, menu=groups_menu, target=target, in_scope=in_scope,
            scope_videos=scope_videos,
            more_in_subfolders=total - direct if not shown["recursive"] and total > direct and not session else 0,
            panel=session_panel(saved, saved_rename(saved, config)) if saved else None,
            mode=mode, rows=rows, blocked=blocked, nothing_to_scan=in_scope < 2))
        if choice == "" and blocked:
            warn(blocked)
        elif choice == "":
            if saved and saved_finished:
                return "apply_saved", saved
            if saved and open_groups:
                return "resume", saved
            if in_scope >= 2:
                return "start", None
        elif choice == "q":
            exit_script()
        elif choice == "c":
            return "change", None
        elif choice == "e" and saved and saved_finished and not forced_dry:
            return "review_saved", saved
        elif choice in ("x", "n") and saved:
            if confirm("Discard the saved session for this folder?", tone="warn"):
                store.delete()
                info("Saved session discarded - all settings can be changed now; Enter starts a new scan.")
        elif session and choice in ("1", "7", "r", "f"):
            warn("That setting belongs to the saved session. Start over to change all settings.")
        elif choice == "2" and saved_finished:
            warn("A finished dry run is carried out as it showed. Start over to change all settings.")
        elif choice == "1":
            settings["stages"] = _cycle(settings["stages"], STAGE_OPTIONS)
        elif choice == "2":
            nxt = _cycle_available(strict_current, strictness, strict_unavailable)
            if nxt in STRICTNESS_PRESETS:
                config["strictness"], config["max_pixel_diff"] = nxt, None
            elif nxt == "saved":
                config["max_pixel_diff"] = cap
            if cap is not None and nxt == strict_current:
                warn(f"Only stricter than the session's limit ({cap}) is possible without a new scan.")
        elif choice == "3":
            settings["confirm"] = _cycle(settings["confirm"], CONFIRM_OPTIONS)
        elif choice == "4":
            if forced_dry:
                warn("--dry-run was given on the command line: this run deletes nothing.")
            elif session and saved_dry and not saved_finished:
                warn("A dry run resumes as a dry run. Start over to change all settings.")
            else:
                unavailable = {"dry_run"} if session else set()
                config["delete_mode"] = _cycle_available(mode, MODE_OPTIONS, unavailable)
                if config["delete_mode"] == "permanent":
                    warn("Files will be deleted permanently, not moved to the trash.")
        elif choice == "5":
            config["viewer"] = _cycle(config["viewer"], VIEWER_OPTIONS)
        elif choice == "6":
            if saved_finished:
                warn("A finished dry run is carried out as it showed. Start over to change all settings.")
            else:
                config["rename_numbered"] = not config["rename_numbered"]
        elif choice == "7":
            settings["videos"] = not settings["videos"]
        elif choice == "r":
            settings["recursive"] = not settings["recursive"]
        elif choice == "f" and settings["recursive"]:
            choose_folders(target, index, settings)
        else:
            warn("Unknown option.")


# --- running -----------------------------------------------------------------
def show_summary(stats: Stats, delete_mode: str, action_log: ActionLog | None = None,
                  note: str | None = None, totals_note: str | None = None) -> None:
    show("summary", stats=stats, delete_mode=delete_mode,
         log_entries=action_log.entries if action_log is not None else 0,
         log_path=action_log.path if action_log is not None else None, note=note, totals_note=totals_note)


@dataclass
class DryRun:
    """What a finished dry run found and decided, so it can be carried out without a rescan."""
    exact_groups: list[ExactGroup] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    scanned: int = 0
    to_remove: int = 0
    to_rename: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.to_remove or self.to_rename)


def real_delete_mode(base_config: dict) -> str:
    """The configured delete mode, or 'trash' if the configured one is a dry run too."""
    mode = base_config.get("_configured_delete_mode", base_config.get("delete_mode", "trash"))
    return "trash" if mode == "dry_run" else mode


def _summary_text(stats: Stats) -> str:
    return (f"{stats.exact_deleted + stats.remux_deleted + stats.similar_deleted} removed, {stats.renamed} renamed, "
            f"{stats.groups_skipped} skipped, {stats.groups_ignored} marked not duplicates, {stats.failed} failed")


def run_scan(target: Path, config: dict, settings: dict, index: FolderIndex,
             store: SessionStore | None, resume: dict | None = None,
             from_dry: DryRun | None = None, review_again: bool = False) -> tuple[Stats, bool, DryRun | None]:
    """Run a new scan, resume a saved one, or carry out a finished dry run.

    Returns (stats, quit_requested, dry_run_result). The last one is only set
    after a dry run (new or resumed) that finished normally.
    """
    dry = config["delete_mode"] == "dry_run"
    if config.get("_cli_dry_run") and not dry:  # safety net: the screens never get here
        raise RuntimeError("--dry-run was given, but a real run was about to start")
    applying = from_dry is not None and not review_again
    # Applying asks nothing, so it saves nothing new. The saved dry run is only
    # deleted once applying has finished, so an interrupted apply can be repeated.
    ctx_store = None if applying else store

    fresh = resume is None and from_dry is None
    files = find_images(target, settings["recursive"], settings["excluded"], settings["videos"]) if fresh else []
    if fresh:
        scanned = len(files)
    else:
        scanned = from_dry.scanned if from_dry else int(resume.get("scanned", 0))
    # A resumed session continues its running totals.
    stats = stats_from_session(resume, scanned) if resume else Stats(scanned=scanned)
    limit, preset = resolve_limit(config)
    if resume and limit >= session_limit(resume):  # a looser limit doesn't apply to a saved session
        limit, preset = session_limit(resume), "session"
    kind = ("resumed dry run" if resume and dry else "resumed session" if resume
            else "dry run applied" if applying else "dry run reviewed again" if from_dry else "new scan")
    action_log = ActionLog(
        None if dry else resolve_log_path(config["log_file"]),
        f"{NAME} {__version__} · folder {target} · mode {config['delete_mode']} · "
        f"limit {limit} ({preset}) · {kind}",
    )
    journal = Journal(None if dry else resolve_journal_path(config.get("journal_file")))
    ctx = RunContext(
        action_log, ctx_store,
        matching=resume.get("matching") if resume else matching_settings(config, settings, target, index),
        created=resume.get("created") if resume else None,
        rename=config["rename_numbered"], dry=dry, scanned=scanned, journal=journal,
    )
    ctx.ignores = IgnoreList(target, enabled=not settings.get("no_ignore", False))
    ctx.stats = stats
    totals_note = None
    if resume and resume.get("stats"):
        try:
            since = datetime.fromisoformat(resume.get("created", "")).strftime("%d %b %Y %H:%M")
        except ValueError:
            since = "the first run"
        totals_note = f"Totals for the whole session since {since}, including earlier runs."

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(target))
        except ValueError:
            return str(path)

    journal.start(target)
    try:
        keep_session = False
        if resume:
            info(f"\nResuming the saved {'dry run' if dry else 'session'} for {target}")
            keep_session = resume_review(resume, config, settings["confirm"], stats, rel, ctx)
        elif from_dry and review_again:
            info(f"\nReviewing the dry run's groups again for real ({MODE_OPTIONS[config['delete_mode']]}) - no rescan")
            keep_session = review_dry_run_again(from_dry.exact_groups, from_dry.groups, config,
                                                settings["confirm"], stats, rel, ctx)
        elif from_dry:
            info(f"\nApplying the dry run for real ({MODE_OPTIONS[config['delete_mode']]}) - no rescan")
            apply_dry_run(from_dry.exact_groups, from_dry.groups, config, stats, rel, ctx)
        else:
            videos = sum(1 for f in files if is_video(f))
            info(f"\nScanning {len(files) - videos} images and {videos} videos in {target}")
            stages = set(settings["stages"].split(","))
            remaining, exact_groups, remux_groups = files, [], []
            if "1" in stages:
                remaining, exact_groups = find_exact_duplicates(files, config, stats, rel, ctx)
                ctx.exact_groups = exact_groups
            if "2" in stages:
                remaining, remux_groups = find_remuxes(remaining, config, settings["confirm"], stats, rel, ctx)
            ctx.exact_groups = exact_groups + remux_groups
            remaining = [f for f in remaining if not is_video(f)]  # visual matching is for images only
            if "3" in stages:
                if len(remaining) > 1:
                    keep_session, groups = find_similar_images(remaining, config, settings["confirm"],
                                                               stats, rel, ctx)
                    ctx.groups = groups
                else:
                    warn("\nNot enough images left for a visual comparison.")
    except UserQuit:
        ctx.flush()
        journal.end(1 if stats.failed else 0, interrupted=False)
        warn("\nStopped by user.")
        note = None
        if ctx.has_saved_session:
            if confirm("Save progress so you can continue this comparison later?", default=True):
                note = "Progress saved - choose this folder again to resume."
            else:
                store.delete()
                note = "Saved progress deleted."
        action_log.end(f"quit by user · {_summary_text(stats)}")
        show_summary(stats, config["delete_mode"], action_log, note, totals_note)
        return stats, True, None
    except KeyboardInterrupt:
        ctx.flush()
        journal.end(130, interrupted=True)
        action_log.end(f"interrupted · {_summary_text(stats)}")
        note = None
        if ctx.has_saved_session:
            note = "Progress saved - run keepbest on this folder again to resume."
        elif applying and store is not None and store.load() is not None:
            note = ("The saved dry run is still there - carry it out again to finish; "
                    "files already handled are skipped.")
        blank()
        show_summary(stats, config["delete_mode"], action_log, note, totals_note)
        raise

    journal.end(1 if stats.failed else 0, interrupted=False)
    note = None
    result = None
    if dry and from_dry is None:
        result = DryRun(exact_groups=ctx.exact_groups, groups=ctx.groups or [], scanned=scanned)
        result.to_remove, result.to_rename = planned_changes(result.exact_groups, result.groups, ctx.rename)
        if store is not None:
            if result.has_changes:
                ctx.save(finished=True)  # kept until it is carried out
                if config.get("_cli_dry_run"):
                    note = ("Dry run saved - to carry it out without a rescan, start keepbest on this "
                            "folder again without --dry-run and carry it out from the start screen.")
                else:
                    note = "Dry run saved - it can also be carried out later from the start screen."
            else:
                store.delete()
    elif store is not None:
        if keep_session:
            note = "Session kept - choose this folder again to review the skipped groups."
        else:
            store.delete()
    action_log.end(f"finished · {_summary_text(stats)}")
    show_summary(stats, config["delete_mode"], action_log, note, totals_note)
    return stats, False, result


def normalize_excluded(target: Path, folders) -> set[str]:
    result = set()
    for folder in folders or ():
        path = Path(folder).expanduser()
        # "sub/../sub" or "../other" are tidied up first, so they match the folder
        # they name, or are reported when they lead outside the scanned folder.
        path = path.resolve() if path.is_absolute() else Path(os.path.normpath(target / path))
        try:
            path = path.relative_to(target)
        except ValueError:
            warn(f"--exclude {folder} is not inside {target}, ignored.")
            continue
        rel = path.as_posix().strip("/") or ROOT
        if rel != ROOT and not (target / rel).is_dir():
            warn(f"--exclude {folder}: no such folder in {target}, ignored.")
            continue
        result.add(rel)
        if rel != ROOT:  # like the folder list: a folder takes its subfolders along
            for root, dirs, _ in os.walk(target / rel, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                result.update((Path(root) / d).relative_to(target).as_posix() for d in dirs)
    return result


def run(initial: Path | None, base_config: dict, base_settings: dict, auto: bool, exclude=()) -> int:
    """The whole program after the command line. *initial* None (a page started
    from the app menu) asks for the folder first."""
    index = FolderIndex()
    use_sessions = bool(base_config.get("save_sessions", True)) and not auto

    if auto:
        target = initial.expanduser().resolve()
        if not target.is_dir():
            error(f"Not a directory: {target}")
            return 2
        if is_protected(target):
            error(protected_message(target))
            return 2
        other = running.claim(target)
        if other is not None:
            error(running.busy_message(other))
            return 2
        settings = {**base_settings, "excluded": normalize_excluded(target, exclude)}
        stats, _, _ = run_scan(target, dict(base_config), settings, index, store=None)
        return 1 if stats.failed else 0

    target = resolve_directory(initial, index)
    config = dict(base_config)
    settings = {**base_settings, "excluded": normalize_excluded(target, exclude)}
    while True:
        store = SessionStore(target) if use_sessions else None
        action, saved = start_screen(target, config, settings, index, store)
        if action == "change":
            changed = resolve_directory(None, index, allow_cancel=True, start=target)
            if changed is None:
                info("Keeping the current folder.")
            else:
                target = changed
                settings["excluded"] = set()  # folder choices belong to the old folder
                success(f"Switched to {target}.")
            continue

        choice = None
        # A saved session continues with the folders it was scanned with.
        run_settings = session_settings(settings, saved) if action != "start" else settings
        if action in ("apply_saved", "review_saved"):
            run_config = {**config, "rename_numbered": saved_rename(saved, config)}
            stats = carry_out_dry_run(target, run_config, base_config, run_settings, index, store,
                                      saved_dry_run(saved, run_config["rename_numbered"]),
                                      review=(action == "review_saved"), mode=carry_out_mode(config))
            if stats is None:  # declined the permanent-deletion question
                continue
            real_run = True
        else:
            run_config = config
            if action == "resume":
                run_config = {**config, "delete_mode": resume_mode(saved, config)}
            if run_config["delete_mode"] == "permanent" and not confirm_permanent():
                continue
            stats, quit_requested, dry_result = run_scan(target, run_config, run_settings, index, store,
                                                         resume=saved if action == "resume" else None)
            index.clear()  # files were removed or renamed, recount on the next screen
            if quit_requested:
                exit_script(1 if stats.failed else 0)
            real_run = run_config["delete_mode"] != "dry_run"
            # Started with --dry-run, nothing is carried out in this run; the saved
            # dry run can be carried out by starting again without it.
            if dry_result is not None and dry_result.has_changes and not config.get("_cli_dry_run"):
                choice = continue_after_dry_run(target, config, base_config, run_settings, index, store,
                                                dry_result)
                real_run = choice is None  # None: it was carried out

        if choice is None:
            choice = end_menu(target, run_settings, index, offer_optimizer=real_run)
        if choice == "change":
            changed = resolve_directory(None, index, allow_cancel=True, start=target)
            if changed is not None:
                target = changed
                settings["excluded"] = set()
                success(f"Switched to {target}.")
        elif choice != "back":
            exit_script(1 if stats.failed else 0)


def end_menu(target: Path, settings: dict, index: FolderIndex, offer_optimizer: bool) -> str:
    """The menu after a run. Returns 'back', 'change' or 'quit'.

    After a real run, media-optimizer is offered for the same folder if it is
    installed; when it exits, this menu comes back for a new run.
    """
    executable = handoff.available() if offer_optimizer else None
    optimizer = executable is not None
    # One with a page of its own asks its questions there; an older one runs with its config's settings.
    label = ("Optimize this folder with media-optimizer (on its page)"
             if optimizer and handoff.has_page(executable) and page_address()
             else "Optimize this folder with media-optimizer (its config's settings, no questions)")
    while True:
        groups = []
        if optimizer:
            groups.append(("Next", [("m", label)]))
        groups.append(("Navigate", [("b", "Back to settings (scan again)"),
                                    ("c", "Change directory"), ("q", "Quit")]))
        answer = ask(EndScreen(prompt="Action: ", primary="Quit", menu=groups))
        if answer == "m" and optimizer:
            run_optimizer(target, settings)
            index.clear()  # media-optimizer converts and renames files
            optimizer = False
            continue
        if answer in ("", "q", "b", "c"):
            return {"b": "back", "c": "change"}.get(answer, "quit")
        warn("Unknown option.")  # a typo must not end the program


def run_optimizer(target: Path, settings: dict) -> None:
    info(f"\nStarting media-optimizer on {target}.")
    if not settings["recursive"]:
        info("It gets the same choice: only the main folder, no subfolders.")
    elif settings["excluded"]:
        info(f"It gets the same folder choice: {len(settings['excluded'])} folder(s) left out.")
    executable = handoff.available()
    # Its own page takes over this tab and leads back here; else its output shows here.
    back = page_address() if executable and handoff.has_page(executable) else ""
    show("tool_start", name="media-optimizer", handoff=bool(back),
         command=f"media-optimizer run {target} --web" if back else f"media-optimizer run -y {target}")
    code = handoff.run(target, lambda line: show("tool_line", text=line), settings["recursive"],
                       settings["excluded"], return_url=back)
    show("tool_end", code=code)
    blank()
    if code == 127:
        error("media-optimizer could not be started.")
    success(f"Back in {NAME}. Scan again, change the directory or quit.")


def confirm_permanent() -> bool:
    """Asked once before an interactive run deletes permanently: only the groups
    you decide on are asked about one by one, everything else is not."""
    warn("\nPermanent mode: files are deleted for good, not moved to the trash. Identical copies, "
         "remuxes and clear matches are removed without asking; only the groups you decide on ask again.")
    if confirm("Delete permanently?", tone="danger"):
        return True
    info("Nothing started.")
    return False


def resume_mode(saved: dict, config: dict, quiet: bool = False) -> str:
    """A session continues the way it was started: a dry run as a dry run, a
    real run never as a dry run (its earlier deletions already happened)."""
    if saved.get("dry_run"):
        mode = "dry_run"
        if config["delete_mode"] != mode and not quiet:
            info("The saved session is a dry run, so it continues as a dry run.")
    else:
        mode = config["delete_mode"] if config["delete_mode"] != "dry_run" else real_delete_mode(config)
        if config["delete_mode"] == "dry_run" and not quiet:
            info(f"The saved session is a real run, so it continues with: {MODE_OPTIONS[mode]}.")
    return mode


def carry_out_dry_run(target: Path, config: dict, base_config: dict, settings: dict,
                      index: FolderIndex, store, dry: DryRun, review: bool,
                      mode: str | None = None) -> Stats | None:
    """Apply a dry run (or review its groups again) for real. None if declined."""
    real_mode = mode or real_delete_mode(base_config)
    if real_mode == "permanent":
        if review:
            if not confirm_permanent():
                return None
        elif not confirm(f"Permanently delete {dry.to_remove} file(s)? This cannot be undone.", tone="danger"):
            return None
    real_config = {**config, "delete_mode": real_mode}
    stats, quit_requested, _ = run_scan(target, real_config, settings, index, store,
                                        from_dry=dry, review_again=review)
    index.clear()
    if quit_requested:
        exit_script(1 if stats.failed else 0)
    return stats


def continue_after_dry_run(target: Path, config: dict, base_config: dict, settings: dict,
                           index: FolderIndex, store, dry: DryRun) -> str | None:
    """Offer to carry out a finished dry run without rescanning.

    Returns 'back' or 'change' when the user navigates away, None after a real
    run has been done (the normal end menu follows).
    """
    real_mode = real_delete_mode(base_config)
    while True:
        choice = after_dry_run_menu(dry, real_mode)
        if choice == "quit":
            if store is not None:
                info("The dry run stays saved - choose this folder again to carry it out.")
            exit_script(0)
        if choice in ("back", "change"):
            return choice
        if choice not in ("apply", "review"):
            warn("Unknown option.")
            continue
        if carry_out_dry_run(target, config, base_config, settings, index, store, dry,
                             review=(choice == "review")) is not None:
            return None


def after_dry_run_menu(dry: DryRun, real_mode: str) -> str:
    """Offer to carry out the dry run. Returns 'apply', 'review', 'back', 'change' or 'quit'."""
    what = _fate(real_mode, dry.to_remove)
    if dry.to_rename:
        what += f", rename {dry.to_rename}"
    answer = ask(AfterDryRunScreen(
        prompt="Action: ", primary=f"Apply exactly what the dry run showed: {what}",
        menu=[("Continue for real", [("r", "Review the groups again (asks as configured)")]),
              ("Navigate", [("b", "Back to settings"), ("c", "Change directory"), ("q", "Quit")])],
        real_mode=real_mode))
    return {"": "apply", "r": "review", "b": "back", "c": "change", "q": "quit"}.get(answer, "unknown")
