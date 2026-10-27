"""The terminal front end: draws the messages front.py is told to show. The
default whenever no other front end is set, which is --auto; on the page it
keeps a log of the run in the terminal (web.py). It asks nothing."""

from __future__ import annotations

from .core_logic import format_size
from .front import Front
from .ui import Column, StyleUI, badge, dim, error, info, print_banner, print_table, progress, success, transient, warn

_TONES = {"ok": "GREEN", "bad": "RED", "note": "CYAN", "warn": "YELLOW"}


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


def _exact_table(group, rel, keep_index: int | None = 0, dry: bool = False) -> None:
    """*keep_index* None: every file stays. (*dry* matters on the page only.)"""
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


def _group_table(group, rel, keep_index: int = 0, done: bool = False, dry: bool = False) -> None:
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
    # media-optimizer on the page: its command and exit code (its lines stay on the page)
    "tool_start": lambda name, command, handoff=False: info(f"$ {command}"),
    "tool_line": lambda text: None,
    "tool_end": lambda code: dim(f"exit code {code}"),
}


class NeedsPage(RuntimeError):
    """A question outside the page: since 1.0.0 only the page asks."""


class TerminalFront(Front):
    """Messages in the terminal: --auto's whole output, and the log of a run
    on the page. Questions are the page's (web.py); --auto asks none."""

    def show(self, kind: str, data: dict) -> None:
        _MESSAGES[kind](**data)

    def ask(self, screen, again: bool = False) -> str:
        raise NeedsPage(f"asked in the terminal: {screen.prompt!r}")

    def confirm(self, question: str, default: bool = False, tone: str = "") -> bool:
        raise NeedsPage(f"asked in the terminal: {question!r}")

    def ask_path(self, prompt: str, start=None) -> str:
        raise NeedsPage(f"asked in the terminal: {prompt!r}")

    def progress(self, label: str, total: int):
        return progress(label, total)

    def transient(self, text: str):
        return transient(text)


TERMINAL = TerminalFront()
