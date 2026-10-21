"""The terminal front end: draws what front.py is told to show and reads the
answers from the keyboard. The default whenever no other front end is set."""

from __future__ import annotations

from .core_logic import format_size
from .front import (AfterDryRunScreen, DecisionScreen, EndScreen, FolderRow, FolderScreen, Front,
                    HandledScreen, Note, OverviewScreen, RemuxScreen, SettingRow, StartScreen)
from .ui import (Column, StyleUI, badge, confirm, dim, disable_path_completion, display_width,
                 enable_path_completion, error, info, pad, print_banner, print_menu, print_primary_action,
                 print_table, progress, safe_input, success, terminal_width, transient, truncate, warn)

_TONES = {"ok": "GREEN", "bad": "RED", "note": "CYAN", "warn": "YELLOW"}
_CONFIRM_TONES = {"warn": "YELLOW", "danger": "RED"}


# --- badges ----------------------------------------------------------------------
def mode_badge(delete_mode: str) -> str:
    return {
        "dry_run": badge("DRY RUN", StyleUI.CYAN),
        "trash": badge("TRASH", StyleUI.YELLOW),
        "permanent": badge("PERMANENT", StyleUI.RED),
    }.get(delete_mode, badge(delete_mode.upper(), StyleUI.WHITE))


def verdict(group) -> str:
    if group.borderline:
        return badge("BORDERLINE", StyleUI.YELLOW)
    return badge("CONFIDENT ", StyleUI.GREEN)


def status_badge(group) -> str:
    if group.status == "done":
        return badge("DONE   ", StyleUI.GREEN)
    if group.status == "skipped":
        return badge("SKIPPED", StyleUI.YELLOW)
    if group.status == "ignored":
        return badge("NOT DUP", StyleUI.MAGENTA)
    return badge("OPEN   ", StyleUI.GRAY)


def _tracks_badge(group) -> str:
    if group.tracks == "reordered":
        return badge("TRACK ORDER", StyleUI.YELLOW) + " "
    if group.tracks == "same":
        return ""
    return badge("TRACKS DIFFER", StyleUI.YELLOW) + " "


# --- groups ----------------------------------------------------------------------
def _exact_heading(group, number: int, total: int, tracks: bool = False) -> None:
    extra = _tracks_badge(group) if tracks else ""
    if group.kind == "remux":
        label = badge("REMUX", StyleUI.CYAN)
        detail = f"{len(group.files)} files · same video stream in different containers"
    else:
        label = badge("IDENTICAL", StyleUI.MAGENTA)
        detail = f"{len(group.files)} files · {format_size(group.files[0]['size'])} each"
    print(f"\n{StyleUI.BOLD}[{number}/{total}]{StyleUI.RESET} {label} {extra}{detail}")


def _exact_table(group, rel, keep_index: int | None = 0) -> None:
    """*keep_index* None: every file stays."""
    remux = group.kind == "remux"
    rows = []
    for i, f in enumerate(group.files):
        kept = i == keep_index
        if keep_index is None:
            action = ("KEEP", StyleUI.GREEN)
        else:
            action = ("KEEP", StyleUI.GREEN) if kept else ("DEL", StyleUI.RED)
        row = [("▶", StyleUI.GREEN + StyleUI.BOLD) if kept else " ",
               (f"[{i}]", "key"),
               action,
               (rel(f["path"]), StyleUI.BOLD) if kept else rel(f["path"])]
        if remux:
            row += [format_size(f["size"]), (f.get("streams", ""), StyleUI.GRAY)]
        rows.append(row)
    columns = [Column(""), Column("#"), Column("Action"), Column("File", flex=True)]
    if remux:
        columns += [Column("Size", align="right"), Column("Streams", optional=True)]
    print_table(columns, rows)


def _group_table(group, rel, keep_index: int = 0, done: bool = False) -> None:
    rows = []
    for i, (meta, diff) in enumerate(zip(group.images, group.diffs)):
        kept = i == keep_index
        if done:
            action = ("KEPT", StyleUI.GREEN) if kept else ("GONE", StyleUI.GRAY)
        else:
            action = ("KEEP", StyleUI.GREEN) if kept else ("DEL", StyleUI.RED)
        if diff is None:
            diff_cell = ("—", StyleUI.GRAY)
        else:
            diff_cell = (str(diff), StyleUI.YELLOW if diff > group.borderline_above else StyleUI.GREEN)
        rows.append([
            ("▶", StyleUI.GREEN + StyleUI.BOLD) if kept else " ",
            (f"[{i}]", "key"),
            action,
            (rel(meta["path"]), StyleUI.BOLD) if kept else rel(meta["path"]),
            (meta["format"], StyleUI.MAGENTA),
            ({True: "lossless", None: "unknown"}.get(meta["lossless"], "lossy"), StyleUI.GRAY),
            f"{meta['width']}x{meta['height']}",
            format_size(meta["size"]),
            (f"{meta['bpp']:.2f}", StyleUI.GRAY),
            diff_cell,
        ])
    print_table([
        Column(""), Column("#"), Column("Action"), Column("File", flex=True, min_width=16),
        Column("Fmt"), Column("Type", optional=True), Column("Resolution", align="right"),
        Column("Size", align="right"), Column("BPP", align="right", optional=True),
        Column("Diff", align="right"),
    ], rows)


def _group_heading(group) -> str:
    return (f"{verdict(group)} worst difference {group.worst} of {group.limit} allowed "
            f"{StyleUI.GRAY}(0 = pixel-identical){StyleUI.RESET}")


# --- messages --------------------------------------------------------------------
def _outcome(symbol: str, label: str, tone: str, name: str) -> None:
    color = getattr(StyleUI, _TONES[tone])
    print(f"  {color}{symbol} {label:<13}{StyleUI.RESET}{name}")


def _summary(stats, delete_mode: str, log_entries: int = 0, log_path=None,
             note: str | None = None, totals_note: str | None = None) -> None:
    dry = delete_mode == "dry_run"
    verb = "to remove" if dry else "removed"
    print_banner("Summary" + (" - dry run" if dry else ""))
    print(f"  Scanned: {stats.scanned}   "
          f"{StyleUI.GREEN}Exact copies {verb}: {stats.exact_deleted}{StyleUI.RESET}   "
          + (f"{StyleUI.GREEN}Remuxed videos {verb}: {stats.remux_deleted}{StyleUI.RESET}   " if stats.remux_deleted else "")
          + f"{StyleUI.GREEN}Identical images {verb}: {stats.similar_deleted}{StyleUI.RESET}")
    print(f"  {StyleUI.GREEN}{'To rename' if dry else 'Renamed'}: {stats.renamed}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Groups skipped: {stats.groups_skipped}{StyleUI.RESET}   "
          + (f"{StyleUI.MAGENTA}Not duplicates: {stats.groups_ignored}{StyleUI.RESET}   " if stats.groups_ignored else "")
          + f"{StyleUI.RED}Failed: {stats.failed}{StyleUI.RESET}   "
          f"{StyleUI.BOLD}Space {'that would be freed' if dry else 'freed'}: {format_size(stats.space_saved)}{StyleUI.RESET}")
    if totals_note:
        print(f"  {StyleUI.GRAY}{totals_note}{StyleUI.RESET}")
    if dry:
        print(f"  {StyleUI.GRAY}Nothing was deleted or renamed.{StyleUI.RESET}")
    if log_entries:
        print(f"  {StyleUI.GRAY}Log ({log_entries} entries): {log_path}{StyleUI.RESET}")
    if note:
        print(f"  {StyleUI.CYAN}{note}{StyleUI.RESET}")


def _stage3_header(limit: int, preset: str, confirm_mode: str, delete_mode: str) -> None:
    print(f"{StyleUI.BOLD}Limit:{StyleUI.RESET} {limit} ({preset})   "
          f"{StyleUI.BOLD}Confirm:{StyleUI.RESET} {confirm_mode}   {mode_badge(delete_mode)}")


def _folder_header(header) -> None:
    print_banner(f"Choose folders to scan ({header.count} subfolders)")
    if header.main_on:
        print(f"  [{StyleUI.GREEN}  m{StyleUI.RESET}] {StyleUI.GREEN}[✓]{StyleUI.RESET} {StyleUI.BOLD}Main folder{StyleUI.RESET}"
              f"  {StyleUI.GRAY}{header.files_text}{StyleUI.RESET}\n")
    else:
        print(f"  [{StyleUI.GREEN}  m{StyleUI.RESET}] {StyleUI.GRAY}[ ]{StyleUI.RESET} {StyleUI.YELLOW}Main folder - excluded"
              f"{StyleUI.RESET}  {StyleUI.GRAY}{header.files_text}{StyleUI.RESET}\n")


_MESSAGES = {
    "banner": lambda text: print_banner(text),
    "info": lambda text: info(text),
    "success": lambda text: success(text),
    "warn": lambda text: warn(text),
    "error": lambda text: error(text),
    "dim": lambda text: dim(text),
    "blank": lambda: print(),
    "goodbye": lambda: print(f"\n{StyleUI.YELLOW}Program terminated.{StyleUI.RESET}"),
    "outcome": _outcome,
    "exact_heading": _exact_heading,
    "exact_table": _exact_table,
    "group_line": lambda group, position: print(f"\n{StyleUI.BOLD}[{position}]{StyleUI.RESET} {_group_heading(group)}"),
    "group_table": _group_table,
    "stage3_header": _stage3_header,
    "summary": _summary,
    "folder_header": lambda header: _folder_header(header),
}


# --- screens ---------------------------------------------------------------------
_LABEL_WIDTH = 12


def _setting(row: SettingRow) -> None:
    """One settings row: every choice listed, the current one in normal bold
    text and the others greyed out. Choices in *unavailable* are also struck
    through. A *locked* row is greyed out entirely and shows only its current
    value, followed by the reason. Without colours, the current choice is
    wrapped in ‹ ›, unavailable ones are marked ✗ and locked rows are labelled.
    Wraps to the terminal width."""
    key, label, options, current = row.key, row.label, row.options, row.current
    colored = bool(StyleUI.GRAY)
    if row.locked is not None:
        value = options.get(current, str(current))
        shown = value if colored else f"‹{value}› (locked)"
        print(f"  {StyleUI.GRAY}[{key}] {label:<{_LABEL_WIDTH}} {shown}   - {row.locked}{StyleUI.RESET}")
        return

    prefix = f"  {StyleUI.GRAY}[{key}]{StyleUI.RESET} {StyleUI.BOLD}{label:<{_LABEL_WIDTH}}{StyleUI.RESET} "
    indent = display_width(prefix)
    gap = "   "

    cells = []
    for value, text in options.items():
        if value == current:
            color = StyleUI.RED if value == row.danger else ""
            shown = text if colored else f"‹{text}›"
            cells.append((display_width(shown), f"{color}{StyleUI.BOLD}{shown}{StyleUI.RESET}"))
        elif value in row.unavailable:
            shown = text if colored else f"✗{text}"
            cells.append((display_width(shown), f"{StyleUI.GRAY}{StyleUI.STRIKE}{shown}{StyleUI.RESET}"))
        else:
            cells.append((display_width(text), f"{StyleUI.GRAY}{text}{StyleUI.RESET}"))

    width = terminal_width()
    lines, line, used = [], [], indent
    for cell_width, cell in cells:
        extra = cell_width + (len(gap) if line else 0)
        if line and used + extra > width:
            lines.append(gap.join(line))
            line, used = [], indent
            extra = cell_width
        line.append(cell)
        used += extra
    lines.append(gap.join(line))
    print(prefix + ("\n" + " " * indent).join(lines))


def _folder_row(row: FolderRow) -> None:
    main_text = ("main folder" if row.main_on
                 else f"{StyleUI.YELLOW}main folder excluded{StyleUI.RESET}{StyleUI.BOLD}")
    subs = f"all {row.total} subfolders" if row.chosen == row.total else f"{row.chosen} of {row.total} subfolders"
    text = f"{StyleUI.BOLD}{main_text} + {subs}{StyleUI.RESET}"
    print(f"  {StyleUI.GRAY}[{row.key}]{StyleUI.RESET} {StyleUI.BOLD}{row.label:<{_LABEL_WIDTH}}{StyleUI.RESET} "
          f"{text} {StyleUI.GRAY}- press f to choose{StyleUI.RESET}")


def _note(note: Note) -> None:
    indent = " " * (_LABEL_WIDTH + 5)
    if note.style == "hint":
        print(f"  {StyleUI.GRAY}{indent}{note.text}{StyleUI.RESET}")
    else:
        dim(f"  {indent}{note.text}")


def _session_panel(panel) -> None:
    kind = badge("DRY RUN", StyleUI.CYAN) + " " if panel.dry else ""
    if panel.finished:
        text = (f"{kind}{badge('FINISHED', StyleUI.GREEN)} from {panel.when} · {panel.groups} group(s) · "
                f"{panel.to_remove} file(s) to remove, {panel.to_rename} to rename")
    else:
        text = (f"{kind}{badge('RESUMABLE', StyleUI.CYAN)} from {panel.when} · {panel.groups} groups: "
                f"{StyleUI.GREEN}{panel.done} done{StyleUI.RESET} · {StyleUI.YELLOW}{panel.skipped} skipped{StyleUI.RESET} · "
                + (f"{StyleUI.MAGENTA}{panel.ignored} not duplicates{StyleUI.RESET} · " if panel.ignored else "")
                + f"{panel.open - panel.skipped} open")
    print(f"\n{StyleUI.BOLD}Saved session:{StyleUI.RESET} {text}")
    if panel.description:
        dim(f"               Matched with: {panel.description}.")


def _start(screen: StartScreen) -> None:
    from .config import NAME
    print_banner(f"{NAME} · Duplicate image finder")
    print(f"{StyleUI.BOLD}Folder:{StyleUI.RESET}  {StyleUI.GRAY}{screen.target}{StyleUI.RESET}")
    scope = (f"{StyleUI.BOLD}{screen.in_scope}{StyleUI.RESET} file(s) in scope "
             f"{StyleUI.GRAY}({screen.in_scope - screen.scope_videos} images, {screen.scope_videos} videos){StyleUI.RESET}")
    if screen.more_in_subfolders:
        scope += f" {StyleUI.GRAY}({screen.more_in_subfolders} more in subfolders - press r){StyleUI.RESET}"
    print(f"{StyleUI.BOLD}Files:{StyleUI.RESET}   {scope}")
    if screen.panel is not None:
        _session_panel(screen.panel)

    print(f"\n{StyleUI.BOLD}Settings:{StyleUI.RESET} {mode_badge(screen.mode)}")
    for row in screen.rows:
        if isinstance(row, SettingRow):
            _setting(row)
        elif isinstance(row, FolderRow):
            _folder_row(row)
        else:
            _note(row)

    if screen.blocked:
        print()
        warn(screen.blocked)
    elif screen.primary:
        print_primary_action("Enter", screen.primary)
    elif screen.nothing_to_scan:
        warn("\nFewer than two files in scope - nothing to compare.")
    print_menu(screen.menu)


def _folders(screen: FolderScreen) -> None:
    _folder_header(screen.header)
    entries = screen.entries
    name_width = min(max(display_width(e.name) for e in entries), max(20, terminal_width() - 30))
    for number, entry in enumerate(entries, 1):
        box = f"{StyleUI.GREEN}[✓]{StyleUI.RESET}" if entry.on else f"{StyleUI.GRAY}[ ]{StyleUI.RESET}"
        shown = pad(truncate(entry.name, name_width), name_width)
        shown = shown if entry.on else f"{StyleUI.GRAY}{shown}{StyleUI.RESET}"
        print(f"  [{StyleUI.GREEN}{number:3d}{StyleUI.RESET}] {box} {shown}  {StyleUI.GRAY}{entry.amount}{StyleUI.RESET}")

    main_text = "main folder" if screen.header.main_on else f"{StyleUI.YELLOW}main folder excluded{StyleUI.RESET}"
    print(f"\n{StyleUI.BOLD}Selected:{StyleUI.RESET} {main_text} + {screen.chosen} of {len(entries)} subfolders · "
          f"{screen.scope_text}")
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)
    dim("  Several at once: 2-5 or 1,3,7 - each listed folder is switched.")


def _decision(screen: DecisionScreen) -> None:
    group = screen.group
    print_banner(f"[{screen.position}] Duplicate group · {len(group.images)} images")
    print(_group_heading(group))
    if group.status == "skipped":
        dim("You skipped this group earlier.")
    print()
    _group_table(group, screen.rel)
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


def _handled(screen: HandledScreen) -> None:
    group = screen.group
    print_banner(f"[{screen.position}] Duplicate group · {len(group.images)} images")
    print(_group_heading(group))
    print()
    if group.status == "ignored":
        _group_table(group, screen.rel)
    else:
        _group_table(group, screen.rel, keep_index=group.kept or 0, done=True)
    info(screen.note)
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


def _overview(screen: OverviewScreen) -> None:
    groups, rel = screen.groups, screen.rel
    print_banner(f"Duplicate groups overview ({len(groups)} groups)")
    for number, group in enumerate(groups, 1):
        selected = screen.current == number - 1
        marker = f"{StyleUI.GREEN}{StyleUI.BOLD}▶{StyleUI.RESET}" if selected else " "
        keeper = group.images[group.kept if group.kept is not None else 0]["path"]
        name = f"{StyleUI.BOLD}{rel(keeper)}{StyleUI.RESET}" if selected else rel(keeper)
        print(f"{marker} [{StyleUI.GREEN}{number:3d}{StyleUI.RESET}] {status_badge(group)} "
              f"{verdict(group)} {len(group.images)} images · diff {group.worst}/{group.limit} · {name}")

    done = sum(g.status == "done" for g in groups)
    skipped = sum(g.status == "skipped" for g in groups)
    ignored = sum(g.status == "ignored" for g in groups)
    pending = len(groups) - done - skipped - ignored
    progress_line = (f"\n{StyleUI.BOLD}Progress:{StyleUI.RESET} {StyleUI.GREEN}{done} done{StyleUI.RESET}   "
                     f"{StyleUI.YELLOW}{skipped} skipped{StyleUI.RESET}   ")
    if ignored:
        progress_line += f"{StyleUI.MAGENTA}{ignored} not duplicates{StyleUI.RESET}   "
    print(progress_line + f"{StyleUI.GRAY}{pending} open{StyleUI.RESET}")
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


def _remux(screen: RemuxScreen) -> None:
    group = screen.group
    _exact_heading(group, screen.number, screen.total, tracks=True)
    if group.tracks == "reordered":
        dim("The video is identical and so are the other tracks, but in another order: "
            "a player starts with another language or sound.")
    elif group.tracks == "same":
        dim("The video and every other track are identical.")
    else:
        dim("The video is identical; the files differ in their other tracks (sound, subtitles, ...).")
    if group.tracks == "differ":
        warn("No file contains all tracks of the others: removing them loses a track "
             "(other sound, language or subtitles).")
    _exact_table(group, screen.rel)
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


def _end(screen: EndScreen) -> None:
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


def _after_dry_run(screen: AfterDryRunScreen) -> None:
    print(f"\n{StyleUI.BOLD}Dry run finished.{StyleUI.RESET} The groups found are kept, so continuing needs no rescan. "
          f"Real runs use the delete mode {mode_badge(screen.real_mode)} from your config.")
    print_primary_action("Enter", screen.primary)
    print_menu(screen.menu)


_SCREENS = {
    StartScreen: _start, FolderScreen: _folders, DecisionScreen: _decision, HandledScreen: _handled,
    OverviewScreen: _overview, RemuxScreen: _remux, EndScreen: _end, AfterDryRunScreen: _after_dry_run,
}


class TerminalFront(Front):
    def show(self, kind: str, data: dict) -> None:
        _MESSAGES[kind](**data)

    def ask(self, screen, again: bool = False) -> str:
        if not again:
            _SCREENS[type(screen)](screen)
        return safe_input(f"{StyleUI.BOLD}{screen.prompt}{StyleUI.RESET}").strip().lower()

    def confirm(self, question: str, default: bool = False, tone: str = "") -> bool:
        if tone:
            color = getattr(StyleUI, _CONFIRM_TONES[tone])
            question = f"{color}{question}{StyleUI.RESET}"
        return confirm(question, default=default)

    def ask_path(self, prompt: str) -> str:
        enable_path_completion()
        try:
            return safe_input(f"{StyleUI.BOLD}{prompt}{StyleUI.RESET}")
        finally:
            disable_path_completion()

    def progress(self, label: str, total: int):
        return progress(label, total)

    def transient(self, text: str):
        return transient(text)


TERMINAL = TerminalFront()
