"""What the program shows and asks, apart from how it looks.

The flow (app.py, core_logic.py) never prints or reads input itself: it calls
the functions here, and the current front end draws them. Without one set, that
is the terminal (terminal.py). Every question that has a whole screen is a
Screen with its data and the keys it understands; the answer is the key, the
same one a user types in the terminal, so another front end (a page) can offer
the keys as buttons and the flow handles both alike.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


class Front:
    """A front end. show() gets every message by kind (see the functions
    below and terminal.py for the kinds) with its data; the questions block
    until they are answered."""

    def show(self, kind: str, data: dict) -> None:
        raise NotImplementedError

    def ask(self, screen: "Screen", again: bool = False) -> str:
        """The key chosen on *screen*: "" for its primary action. *again*: the
        same screen after an invalid key, which the terminal doesn't redraw."""
        raise NotImplementedError

    def confirm(self, question: str, default: bool = False, tone: str = "") -> bool:
        """A yes/no question; *tone* "warn" or "danger" colours it."""
        raise NotImplementedError

    def ask_path(self, prompt: str) -> str:
        """A folder path, as typed (unstripped)."""
        raise NotImplementedError

    def progress(self, label: str, total: int):
        """Context manager with advance(): a counter for long work."""
        raise NotImplementedError

    def transient(self, text: str):
        """Context manager: a status line while short work runs."""
        raise NotImplementedError


_front: Front | None = None


def use(front: Front | None) -> None:
    """Send output and questions to *front*; None is the terminal again."""
    global _front
    _front = front


def in_terminal() -> bool:
    """True when no other front end is set: output and questions use the terminal."""
    return _front is None


def current() -> Front:
    if _front is None:
        from .terminal import TERMINAL
        return TERMINAL
    return _front


# --- output --------------------------------------------------------------------
def show(kind: str, **data) -> None:
    current().show(kind, data)


def banner(text: str) -> None:
    show("banner", text=text)


def info(text: str) -> None:
    show("info", text=text)


def success(text: str) -> None:
    show("success", text=text)


def warn(text: str) -> None:
    show("warn", text=text)


def error(text: str) -> None:
    show("error", text=text)


def dim(text: str) -> None:
    show("dim", text=text)


def blank() -> None:
    show("blank")


def outcome(symbol: str, label: str, tone: str, name: str) -> None:
    """One line of what happened to a file; *tone* is ok, bad, note or warn."""
    show("outcome", symbol=symbol, label=label, tone=tone, name=name)


def exit_script(code: int = 0) -> None:
    show("goodbye")
    sys.exit(code)


# --- questions -----------------------------------------------------------------
def ask(screen: "Screen", again: bool = False) -> str:
    return current().ask(screen, again)


def confirm(question: str, default: bool = False, tone: str = "") -> bool:
    return current().confirm(question, default, tone)


def ask_path(prompt: str) -> str:
    return current().ask_path(prompt)


def progress(label: str, total: int):
    return current().progress(label, total)


def transient(text: str):
    return current().transient(text)


# --- screens -------------------------------------------------------------------
Menu = list  # [(group title, [(key, label), ...]), ...]


@dataclass
class Screen:
    """A question with a whole screen. *prompt* is what the terminal asks with,
    *primary* what Enter does (None: Enter does nothing special), *menu* the
    other keys in groups."""
    prompt: str
    primary: str | None
    menu: Menu


@dataclass
class SettingRow:
    """A start-screen setting with its choices; *locked* gives the reason it can't change."""
    key: str
    label: str
    options: dict
    current: object
    danger: object = None
    unavailable: object = ()
    locked: str | None = None


@dataclass
class FolderRow:
    """The start screen's folder choice, changed on its own screen."""
    key: str
    label: str
    main_on: bool
    chosen: int
    total: int


@dataclass
class Note:
    """A line between the settings; *style* "dim" or "hint" (two shades of grey in the terminal)."""
    text: str
    style: str = "dim"


@dataclass
class SessionPanel:
    """A saved session on the start screen."""
    dry: bool
    finished: bool
    when: str
    groups: int
    done: int
    skipped: int
    ignored: int
    open: int
    to_remove: int = 0
    to_rename: int = 0
    description: str | None = None


@dataclass
class StartScreen(Screen):
    target: Path
    in_scope: int
    scope_videos: int
    more_in_subfolders: int        # files the subfolder setting leaves out (0: don't mention)
    panel: SessionPanel | None
    mode: str
    rows: list                     # SettingRow, FolderRow and Note, in order
    blocked: str | None            # why Enter can't start anything
    nothing_to_scan: bool


@dataclass
class FolderEntry:
    rel: str
    name: str                      # indented like a tree, with a trailing "/"
    on: bool
    amount: str


@dataclass
class FolderHeader:
    count: int                     # subfolders listed
    main_on: bool
    files_text: str                # what the main folder holds directly


@dataclass
class FolderScreen(Screen):
    header: FolderHeader
    entries: list[FolderEntry]
    chosen: int
    scope_text: str


@dataclass
class DecisionScreen(Screen):
    """A visual duplicate group waiting for a decision."""
    group: object
    position: str
    rel: object


@dataclass
class HandledScreen(Screen):
    """A group already decided or marked "not duplicates"."""
    group: object
    position: str
    rel: object
    note: str


@dataclass
class OverviewScreen(Screen):
    groups: list
    current: int | None
    rel: object


@dataclass
class RemuxScreen(Screen):
    group: object
    number: int
    total: int
    rel: object


@dataclass
class EndScreen(Screen):
    """The menu after a run."""


@dataclass
class AfterDryRunScreen(Screen):
    real_mode: str
