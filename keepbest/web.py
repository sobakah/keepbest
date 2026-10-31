"""The browser page: keepbest's flow, unchanged, with a page as its front end.

The flow keeps the main thread: handoff.py and the interrupt handling set
signal handlers, which Python allows only there. The HTTP server and a watcher
run in threads. WebFront collects what the flow shows and holds the question
it waits on; the page asks for what is new and sends the key of the button
pressed (its keyboard shortcuts are the same keys). The terminal keepbest was
started in keeps a log of the messages. Only this machine can connect, and
only with the token from the printed address, which the first visit turns
into a cookie (as in lyrembed and cdimport).

A page that is closed stops the flow at its next question, as Ctrl+C would
there; work in progress (a scan, a deletion) is never cut off.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import front as front_mod
from . import selfupdate
from .config import NAME, PROJECT_URL, __version__
from .core_logic import format_size
from .front import (AfterDryRunScreen, DecisionScreen, EndScreen, FolderRow, FolderScreen, Front, HandledScreen,
                    Note, OverviewScreen, RemuxScreen, SettingRow, StartScreen)
from .image_utils import IMAGE_EXTENSIONS, deciding_criterion, preview_jpeg
from .video import VIDEO_EXTENSIONS

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",  # keepbest's own icon and logo only
}
_CSP = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'")
# A picture opened in a tab of its own: the browser's viewer styles it inline, nothing runs.
_FILE_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
# Cookies belong to a host, not a port: two pages on 127.0.0.1 (a second
# keepbest, or another program's page) would overwrite each other's token
# under one name. The port goes into the name.
COOKIE = f"{NAME}_token"
# The page pings every 15 s, and Chrome runs a hidden tab's timers once a
# minute, so only longer silence means it is gone. A reload sends "closing"
# too, then pings again within the grace.
HEARTBEAT_TIMEOUT = 90.0
CLOSE_GRACE = 10.0
# Stopped from the terminal (Ctrl+C, SIGTERM): how long the page gets to see that.
INTERRUPT_GRACE = 2.0
MAX_BODY = 64 * 1024
MAX_FOLDER_ENTRIES = 500
# How often a progress counter is sent on to the page at most.
PROGRESS_INTERVAL = 0.2
# Longest side of a preview: for the group view, and for zooming in.
PREVIEW_SIZES = {"thumb": 480, "screen": 1200, "zoom": 3000}
# Bytes of a video sent per range at most; the player asks for the next ones.
MEDIA_CHUNK = 4 * 1024 * 1024
PREVIEW_CACHE = 40
# Lines of a tool's output the page keeps.
TOOL_LINES = 500
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# media-optimizer prints its page's address like this (its web.ADDRESS_LINE), as lyrembed does for cdimport.
_HANDOFF = re.compile(r"is running at (http://(?:127\.0\.0\.1|localhost):\d+/\?token=[A-Za-z0-9_-]+)")
# The tab is on media-optimizer's page while it runs; coming back takes a moment.
HANDOFF_GRACE = 30.0
# Pictures the browser shows as they are; other formats (JXL, TIFF, ...) are rendered.
_BROWSER_IMAGES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
                   ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp"}
# Longest side of a rendered picture opened on its own: in practice the full size.
FULL_SIZE = 16384
# Where an update can be installed: before a scan or after one, never in the middle
# (a new version under a running one would mix the two). Also the folder question.
UPDATE_SCREENS = ("StartScreen", "EndScreen")
# After an update: the address the new keepbest takes over ("port token"), so the tab stays.
RESTART_ENV = "KEEPBEST_RESTART_PAGE"
# How long the page gets to see that keepbest starts again.
RESTART_GRACE = 1.0


def cookie_name(port: int) -> str:
    return f"{COOKIE}_{port}"


class Cancelled(KeyboardInterrupt):
    """The page was closed, or quit, while the flow waited for an answer: the
    flow stops as on Ctrl+C at that question (progress saved, summary)."""


class Restart(Cancelled):
    """A newer keepbest was installed: the flow stops at its open question and
    serve() starts the new one in this process's place."""


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --- what the page gets ----------------------------------------------------------
def _menu(screen) -> list:
    return [[title, [[key, label] for key, label in items]] for title, items in screen.menu]


_RANGE = re.compile(r"([a-z]*)(\d+)-\1(\d+)")


def _expand(key: str) -> list[str]:
    """A menu key as the keys it stands for: "1-3" is 1, 2 and 3, "i1-i2" is i1 and i2."""
    match = _RANGE.fullmatch(key)
    if not match:
        return [key]
    prefix, low, high = match[1], int(match[2]), int(match[3])
    return [f"{prefix}{n}" for n in range(low, high + 1)]


def screen_keys(screen) -> list[str]:
    """Every key *screen* offers, ranges written out; "" for Enter."""
    keys = [""] if screen.primary else []
    for _, items in screen.menu:
        for key, _ in items:
            keys += _expand(key)
    return keys


def _lossless(meta: dict) -> str:
    return {True: "lossless", None: "unknown"}.get(meta.get("lossless"), "lossy")


def _present(file_id, path) -> str | None:
    """*path*'s id for opening it or showing it in its folder, while it is on disk
    (after a real run, only the kept file of a group is)."""
    return file_id(path) if file_id and Path(path).is_file() else None


def visual_group(group, rel, preview=None) -> dict:
    """A visual group for the page; *preview* gives a picture's id (previews,
    opening it). Each image that ranks below the kept one says in which
    criterion of score_image ("beaten"), so the page can show why."""
    worst = max(range(len(group.diffs)), key=lambda i: -1 if group.diffs[i] is None else group.diffs[i])
    kept = group.kept if group.status == "done" and group.kept is not None else 0
    keeper = group.images[kept] if kept < len(group.images) else None
    return {
        "images": [{"name": rel(meta["path"]), "format": meta.get("format"), "type": _lossless(meta),
                    "width": meta.get("width"), "height": meta.get("height"), "size": format_size(meta["size"]),
                    "bpp": round(meta["bpp"], 2) if meta.get("bpp") is not None else None, "diff": diff,
                    "spot": meta.get("spot"), "preview": _present(preview, meta["path"]),
                    "file": _present(preview, meta["path"]),
                    "beaten": deciding_criterion(keeper, meta) if keeper is not None and i != kept else None,
                    "jxlPreferred": meta.get("format") == "JXL" and bool(meta.get("lossless_rank"))
                                    and meta.get("lossless") is not True}
                   for i, (meta, diff) in enumerate(zip(group.images, group.diffs))],
        # where the group differs most: the spot of its least similar image
        "spot": group.images[worst].get("spot"),
        "worst": group.worst, "limit": group.limit, "borderline": group.borderline,
        "borderlineAbove": group.borderline_above, "status": group.status, "kept": group.kept,
        "removed": group.removed, "rename": group.rename,
    }


def exact_group(group, rel, file_id=None) -> dict:
    """An exact or remux group for the page; *file_id* gives the id a file still on disk
    is played (`media`) and opened by (`file`; a picture's preview too)."""
    return {"kind": group.kind, "tracks": group.tracks, "status": group.status,
            "files": [{"name": rel(f["path"]), "size": format_size(f["size"]), "streams": f.get("streams", ""),
                       "media": _present(file_id, f["path"]), "type": _video_type(f["path"]),
                       "video": Path(f["path"]).suffix.lower() in VIDEO_EXTENSIONS,
                       "file": _present(file_id, f["path"])}
                      for f in group.files]}


# What a browser is told a video is; it decides itself whether it can play it.
_VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
                ".mkv": "video/x-matroska", ".ogv": "video/ogg", ".avi": "video/x-msvideo", ".ts": "video/mp2t",
                ".mts": "video/mp2t", ".m2ts": "video/mp2t", ".3gp": "video/3gpp", ".wmv": "video/x-ms-wmv",
                ".flv": "video/x-flv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg"}


def _video_type(path) -> str:
    return _VIDEO_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def _option_list(row: SettingRow) -> list[dict]:
    unavailable = row.unavailable or ()
    return [{"label": text, "current": value == row.current, "unavailable": value in unavailable,
             "danger": row.danger is not None and value == row.danger}
            for value, text in row.options.items()]


def _row(row) -> dict:
    if isinstance(row, SettingRow):
        return {"type": "setting", "key": row.key, "label": row.label, "options": _option_list(row),
                "locked": row.locked}
    if isinstance(row, FolderRow):
        return {"type": "folders", "key": row.key, "label": row.label, "mainOn": row.main_on,
                "chosen": row.chosen, "total": row.total}
    assert isinstance(row, Note)
    return {"type": "note", "text": row.text, "style": row.style}


def screen_data(screen, file_id=None) -> dict:
    """A Screen as the page gets it: plain data, no paths outside its texts.
    *file_id* gives the id under which the page may show a file (WebFront.file_id)."""
    data = {"screen": type(screen).__name__, "prompt": screen.prompt.strip().rstrip(":"),
            "primary": screen.primary, "menu": _menu(screen), "keys": screen_keys(screen)}
    if isinstance(screen, StartScreen):
        data.update(target=str(screen.target), targetShort=tilde(screen.target), inScope=screen.in_scope, scopeVideos=screen.scope_videos,
                    moreInSubfolders=screen.more_in_subfolders,
                    panel=asdict(screen.panel) if screen.panel is not None else None, mode=screen.mode,
                    rows=[_row(r) for r in screen.rows], blocked=screen.blocked,
                    nothingToScan=screen.nothing_to_scan)
    elif isinstance(screen, FolderScreen):
        data.update(header=asdict(screen.header), chosen=screen.chosen, scopeText=screen.scope_text,
                    entries=[{"key": str(n), "rel": e.rel, "name": e.name.strip().rstrip("/"),
                              "depth": (len(e.name) - len(e.name.lstrip(" "))) // 2, "on": e.on, "amount": e.amount}
                             for n, e in enumerate(screen.entries, 1)])
    elif isinstance(screen, (DecisionScreen, HandledScreen)):
        data.update(position=screen.position, group=visual_group(screen.group, screen.rel, file_id))
        if isinstance(screen, HandledScreen):
            data["note"] = screen.note.strip()
    elif isinstance(screen, OverviewScreen):
        groups = []
        for n, g in enumerate(screen.groups, 1):
            keeper = g.images[g.kept if g.kept is not None else 0]["path"]
            groups.append({"key": str(n), "keeper": screen.rel(keeper), "preview": file_id(keeper) if file_id else None,
                           "file": _present(file_id, keeper),
                           "status": g.status, "borderline": g.borderline, "worst": g.worst, "limit": g.limit,
                           "count": len(g.images)})
        data.update(current=screen.current, groups=groups)
    elif isinstance(screen, RemuxScreen):
        data.update(number=screen.number, total=screen.total, group=exact_group(screen.group, screen.rel, file_id))
    elif isinstance(screen, AfterDryRunScreen):
        data.update(realMode=screen.real_mode)
    else:
        assert isinstance(screen, EndScreen)
    return data


def _only_kept(rows: list[dict], keep_index, dry: bool) -> list[dict]:
    """A real run removes the other files right after its table is shown: only
    the kept one keeps its buttons (open it, show it in its folder)."""
    if not dry and keep_index is not None:
        for i, row in enumerate(rows):
            if i != keep_index:
                row["file"] = None
    return rows


def message_data(kind: str, data: dict, file_id=None) -> dict:
    """A message as the page gets it; *file_id* gives the ids of files it may open."""
    if "text" in data:
        return {"text": data["text"].strip("\n")}
    if kind == "outcome":
        return dict(data)
    if kind == "exact_heading":
        group = data["group"]
        return {"number": data["number"], "total": data["total"], "groupKind": group.kind,
                "tracks": group.tracks if data.get("tracks") else "", "count": len(group.files),
                "size": format_size(group.files[0]["size"])}
    if kind == "exact_table":
        group = exact_group(data["group"], data["rel"], file_id)
        _only_kept(group["files"], data.get("keep_index", 0), data.get("dry", False))
        return {"group": group, "keepIndex": data.get("keep_index", 0)}
    if kind == "group_line":
        group = data["group"]
        return {"position": data["position"], "worst": group.worst, "limit": group.limit,
                "borderline": group.borderline}
    if kind == "group_table":
        group = visual_group(data["group"], data["rel"], file_id)
        _only_kept(group["images"], data.get("keep_index", 0), data.get("dry", False))
        return {"group": group, "keepIndex": data.get("keep_index", 0), "done": data.get("done", False)}
    if kind == "stage3_header":
        return {"limit": data["limit"], "preset": data["preset"], "confirm": data["confirm_mode"],
                "mode": data["delete_mode"]}
    if kind == "summary":
        return {"stats": asdict(data["stats"]), "mode": data["delete_mode"], "sizeFreed": format_size(
                    data["stats"].space_saved), "logEntries": data.get("log_entries", 0),
                "logPath": str(data["log_path"]) if data.get("log_path") else None,
                "note": data.get("note"), "totalsNote": data.get("totals_note")}
    if kind == "folder_header":
        return asdict(data["header"])
    return {}


# --- the front end -----------------------------------------------------------------
class _Counter:
    """A progress counter on the page."""

    def __init__(self, web: "WebFront", label: str, total: int):
        self._web, self._last = web, 0.0
        self.event = web._add({"kind": "progress", "label": label, "total": total, "done": 0, "running": True})

    def __enter__(self):
        return self

    def advance(self, amount: int = 1) -> None:
        self.event["done"] += amount
        now = time.monotonic()
        if now - self._last >= PROGRESS_INTERVAL or self.event["done"] >= self.event["total"]:
            self._last = now
            self._web._update(self.event)

    def __exit__(self, *exc):
        self.event["running"] = False
        self._web._update(self.event)
        return False


class _Status:
    """A status line on the page while short work runs."""

    def __init__(self, web: "WebFront", text: str):
        self._web = web
        self.event = web._add({"kind": "status", "text": text, "running": True})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.event["running"] = False
        self._web._update(self.event)
        return False


class WebFront(Front):
    """Collects what the flow shows and holds the question it waits on.

    Every change gets a new revision number; the page asks for everything
    newer than the last one it saw.
    """

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo                # messages also go to the terminal, as a log
        self.page_url = ""              # set by serve(): where media-optimizer's page leads back to
        self._cond = threading.Condition()
        self._rev = 0
        self.events: list[dict] = []
        self.question: dict | None = None
        self._screen = None
        self._answers: dict[int, object] = {}
        self._next_question = 0
        self.finished: int | None = None
        self.gone = False
        self.boot = secrets.token_urlsafe(6)  # a new one after an update: the page loads itself again
        self.update: dict | None = None       # a newer keepbest (offer_update)
        self._update_commands: list | None = None
        # Start screen: a choice the page picked directly, reached by pressing
        # the row's key until it shows (the flow only knows "next choice").
        self._target: tuple[str, object] | None = None
        self._presses = 0
        # Pictures the page may show, by random id: the only files /api/preview
        # reads, so the page can never make keepbest open anything else.
        self._tool: dict | None = None  # the running tool's output event (media-optimizer)
        self._tool_sent = 0.0
        self._handoff_end: float | None = None
        self._previews: dict[str, Path] = {}
        self._file_ids: dict[Path, str] = {}
        self._preview_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    # recording
    def _bump(self, event: dict | None = None) -> None:
        self._rev += 1
        if event is not None:
            event["rev"] = self._rev
        self._cond.notify_all()

    def _add(self, event: dict) -> dict:
        with self._cond:
            event["id"] = len(self.events)
            self.events.append(event)
            self._bump(event)
        return event

    def _update(self, event: dict) -> None:
        with self._cond:
            self._bump(event)

    def show(self, kind: str, data: dict) -> None:
        if self.echo:
            self._log(kind, data)
        if kind in ("blank", "goodbye"):
            return
        if kind == "tool_start":
            self._tool = self._add({"kind": "tool", "name": data["name"], "command": data["command"],
                                    "lines": [], "running": True, "code": None,
                                    "handoff": data.get("handoff", False), "url": None, "results": None})
            self._tool_sent = 0.0
            return
        if kind in ("tool_line", "tool_end") and self._tool is not None:
            tool = self._tool
            with self._cond:
                if kind == "tool_line":
                    # A redrawn progress line keeps only its newest state.
                    line = _ANSI.sub("", data["text"]).split("\r")[-1].rstrip()
                    tool["lines"].append(line)
                    del tool["lines"][:-TOOL_LINES]
                    found = _HANDOFF.search(line) if tool["handoff"] and not tool["url"] else None
                    if found:  # the page moves its tab there; that page leads back
                        tool["url"] = found.group(1)
                        self._bump(tool)
                        return
                else:
                    tool.update(running=False, code=data["code"], results=data.get("results"))
                    if tool["handoff"]:
                        self._handoff_end = time.monotonic()
                    self._tool = None
                now = time.monotonic()
                if kind == "tool_end" or now - self._tool_sent >= PROGRESS_INTERVAL:
                    self._tool_sent = now
                    self._bump(tool)
            return
        self._add({"kind": kind, **message_data(kind, data, self.file_id)})

    @staticmethod
    def _log(kind: str, data: dict) -> None:
        from .terminal import TERMINAL
        try:
            TERMINAL.show(kind, data)
        except OSError:  # no terminal to write to (started from the app menu)
            pass

    @property
    def away(self) -> bool:
        """The tab is on media-optimizer's page, or only just came back from it."""
        with self._cond:
            if self._tool is not None and self._tool["handoff"]:
                return True
            return self._handoff_end is not None and time.monotonic() - self._handoff_end < HANDOFF_GRACE

    def progress(self, label: str, total: int):
        return _Counter(self, label, total)

    def transient(self, text: str):
        return _Status(self, text)

    # questions
    # a newer keepbest
    def offer_update(self, version: str, how: selfupdate.Install) -> None:
        with self._cond:
            self.update = {"version": version, "current": __version__, "command": how.command,
                           "installable": bool(how.commands), "kind": how.kind, "state": "available",
                           "notes": f"{PROJECT_URL}/releases/tag/{version}", "output": ""}
            self._update_commands = how.commands
            self._bump()

    def _update_ready(self) -> bool:
        """Whether the open question is one where an update may be installed."""
        q = self.question
        return q is not None and (q["kind"] == "path" or q.get("screen") in UPDATE_SCREENS)

    def start_update(self) -> None:
        """Install the newer keepbest (in a thread); questions wait meanwhile.
        Then the flow stops with Restart, or the page shows what went wrong."""
        with self._cond:
            update = self.update
            if update is None or not self._update_commands:
                raise ApiError(409, "There is no update to install here.")
            if update["state"] not in ("available", "failed"):
                raise ApiError(409, "The update is already being installed.")
            if not self._update_ready():
                raise ApiError(409, f"{NAME} can be updated on the start screen, the end screen "
                                    "or the folder question, not during a run.")
            update.update(state="installing", output="")
            self._bump()
        commands = self._update_commands
        if self.echo:
            _say(f"Updating {NAME} to {update['version']}: {' ; '.join(update['command'].splitlines())}")

        def run() -> None:
            from .session import state_dir
            code, tail = selfupdate.install(commands, state_dir() / "update.log")
            self._installed(code, tail)

        threading.Thread(target=run, name=f"{NAME}-update", daemon=True).start()

    def _installed(self, code: int, tail: str) -> None:
        with self._cond:
            update = self.update
            if code != 0:
                update.update(state="failed", output=tail, code=code)
            elif self.question is not None and not self.gone:
                update["state"] = "restarting"
                self._answers[self.question["id"]] = Restart()
            else:  # the page went away meanwhile: the next start is the new version
                update["state"] = "installed"
            self._bump()
            self._cond.notify_all()
        if self.echo:
            _say(f"{NAME} {update['version']} is installed." if code == 0 else
                 f"The update failed (exit code {code}):\n{tail}")

    def _ask(self, kind: str, payload: dict, screen=None) -> object:
        with self._cond:
            if self.gone:
                raise Cancelled()
            self._next_question += 1
            qid = self._next_question
            self.question = {"id": qid, "kind": kind, **payload}
            self._screen = screen
            self._bump()
            while qid not in self._answers:
                self._cond.wait()
            answer = self._answers.pop(qid)
            self.question = self._screen = None
            self._bump()
        if isinstance(answer, Cancelled):
            raise answer
        return answer

    def ask(self, screen, again: bool = False) -> str:
        key = self._press_toward_target(screen)
        if key is not None:
            return key
        data = screen_data(screen, self.file_id)
        answer = self._ask("screen", data, screen)
        if not isinstance(screen, (StartScreen, FolderScreen)):  # forms: the screen itself shows the change
            self._add({"kind": "answer", "prompt": data["prompt"], "text": _key_text(screen, answer)})
        return answer

    def file_id(self, path: Path) -> str:
        with self._cond:
            if path not in self._file_ids:
                image_id = secrets.token_urlsafe(9)
                self._file_ids[path], self._previews[image_id] = image_id, path
            return self._file_ids[path]

    def media(self, file_id: str) -> Path:
        """The file behind *file_id*: a video for the player, a file to open or show."""
        with self._cond:
            path = self._previews.get(file_id)
        if path is None:
            raise ApiError(404, "No such file.")
        if not path.is_file():
            raise ApiError(404, "The file is no longer there.")
        return path

    def original(self, file_id: str) -> tuple[bytes, str]:
        """A picture to open on its own: as it is where the browser can show it,
        else rendered at full size."""
        path = self.media(file_id)
        content_type = _BROWSER_IMAGES.get(path.suffix.lower())
        if content_type is not None:
            return path.read_bytes(), content_type
        try:
            return preview_jpeg(path, FULL_SIZE, quality=92), "image/jpeg"
        except Exception as exc:  # whatever Pillow raises for a file it can't read
            raise ApiError(415, f"This file can't be shown: {exc}") from exc

    def preview(self, image_id: str, size: str) -> bytes:
        """The preview behind *image_id* as JPEG, rendered when first asked for."""
        if size not in PREVIEW_SIZES:
            raise ApiError(400, "Unknown preview size.")
        with self._cond:
            path = self._previews.get(image_id)
            cached = self._preview_cache.get((image_id, size))
        if path is None:
            raise ApiError(404, "No such picture.")
        if cached is not None:
            return cached
        if not path.is_file():
            raise ApiError(404, "The file is no longer there.")
        try:
            data = preview_jpeg(path, PREVIEW_SIZES[size])
        except Exception as exc:  # whatever Pillow raises for a file it can't read
            raise ApiError(415, f"No preview: {exc}") from exc
        with self._cond:
            self._preview_cache[(image_id, size)] = data
            while len(self._preview_cache) > PREVIEW_CACHE:
                self._preview_cache.popitem(last=False)
        return data

    def _press_toward_target(self, screen) -> str | None:
        target = self._target
        if target is None or not isinstance(screen, StartScreen):
            self._target = None
            return None
        key, option = target
        row = next((r for r in screen.rows if isinstance(r, SettingRow) and r.key == key and r.locked is None), None)
        if row is None or row.current == option or self._presses > len(row.options):
            self._target = None
            return None
        self._presses += 1
        return key

    def confirm(self, question: str, default: bool = False, tone: str = "") -> bool:
        answer = self._ask("confirm", {"prompt": question, "default": default, "tone": tone})
        self._add({"kind": "answer", "prompt": question, "text": "Yes" if answer else "No"})
        return answer

    def ask_path(self, prompt: str, start: Path | None = None) -> str:
        # start: where the picker opens; the page forgets its last folder when it reloads
        return self._ask("path", {"prompt": prompt.strip().rstrip(":"), "start": str(start) if start else ""})

    def answer(self, qid: int, value: object) -> None:
        """The page's answer to question *qid*, checked against what it asked."""
        with self._cond:
            question = self.question
            if question is None or question["id"] != qid:
                raise ApiError(409, "That question is no longer open.")
            if self.update is not None and self.update["state"] in ("installing", "restarting"):
                raise ApiError(409, f"{NAME} is being updated; it starts again in a moment.")
            kind = question["kind"]
            if kind == "screen":
                value = self._screen_answer(question, value)
            elif kind == "confirm":
                if not isinstance(value, bool):
                    raise ApiError(400, "Answer yes or no.")
            elif kind == "path":
                if not isinstance(value, str) or len(value) > 4096:
                    raise ApiError(400, "Expected a folder path.")
            self._answers[qid] = value
            self._cond.notify_all()

    def _screen_answer(self, question: dict, value: object) -> str:
        if isinstance(value, dict) and question["screen"] == "StartScreen":
            # {"set": [row key, option index]}: that choice of a setting
            key, index = (value.get("set") or [None, None])[:2]
            row = next((r for r in question["rows"] if r["type"] == "setting" and r["key"] == key), None)
            if row is None or row["locked"] or not isinstance(index, int) or not 0 <= index < len(row["options"]) \
                    or row["options"][index]["unavailable"]:
                raise ApiError(400, "That choice can't be made here.")
            # The option, not its place: pressing can add or take away options (a custom limit).
            options = next(r.options for r in self._screen.rows if isinstance(r, SettingRow) and r.key == key)
            self._target, self._presses = (key, list(options)[index]), 1
            return key
        if not isinstance(value, str) or value not in question["keys"]:
            raise ApiError(400, f"Not an option here: {value!r}")
        return value

    def cancel(self) -> bool:
        """Stop the flow at the open question; False when none is open. A question
        already answered keeps its answer: the flow goes on with it."""
        with self._cond:
            if self.question is None or self.question["id"] in self._answers:
                return False
            self._answers[self.question["id"]] = Cancelled()
            self._cond.notify_all()
            return True

    def page_gone(self) -> None:
        """Nobody can answer any more: the open question and every later one stop the flow."""
        with self._cond:
            self.gone = True
        self.cancel()

    def finish(self, code: int) -> None:
        with self._cond:
            self.finished = code
            self._bump()

    # the page's view
    def snapshot(self, since: int) -> dict:
        with self._cond:
            update = dict(self.update, ready=self._update_ready()) if self.update is not None else None
            return {"rev": self._rev, "events": [e for e in self.events if e["rev"] > since],
                    "question": self.question, "finished": self.finished, "boot": self.boot, "update": update}

    def wait_for_change(self, since: int, timeout: float) -> None:
        """Return once something newer than *since* exists, or after *timeout*."""
        with self._cond:
            if self._rev <= since:
                self._cond.wait(timeout)


def _key_text(screen, key: str) -> str:
    """What choosing *key* on *screen* means, for the page's history."""
    if key == "":
        return screen.primary or "Enter"
    for _, items in screen.menu:
        for menu_key, label in items:
            if key in _expand(menu_key):
                return label if menu_key == key else f"{label}: {key}"
    return key


def tilde(path) -> str:
    """*path* with the home folder written as ~, for short display."""
    home = Path.home()
    try:
        return "~/" + Path(path).relative_to(home).as_posix()
    except ValueError:
        return str(path)


# --- the folder picker -------------------------------------------------------------
def pictures_folder() -> Path:
    """The desktop's pictures folder (XDG_PICTURES_DIR, ~/Bilder on a German
    desktop), else ~/Pictures if it exists, else the home folder: where the
    folder picker starts."""
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        for line in (config_home / "user-dirs.dirs").read_text(encoding="utf-8").splitlines():
            match = re.match(r'\s*XDG_PICTURES_DIR="(.*)"\s*$', line)
            if match:
                folder = Path(match.group(1).replace("$HOME", str(home)))
                if folder.is_absolute() and folder.is_dir() and folder != home:
                    return folder
    except OSError:
        pass
    fallback = home / "Pictures"
    return fallback if fallback.is_dir() else home


def list_folders(raw: str) -> dict:
    """Subfolders of *raw* for the folder picker: the browser can't hand out
    paths, so the server lists them. Only names of folders, never files."""
    from .app import is_protected

    path = Path(raw).expanduser() if raw else pictures_folder()
    try:
        path = path.resolve()
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ApiError(400, f"Cannot open '{path}': {exc.strerror or exc}") from exc
    extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
    dirs, media = [], 0
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                # Full paths, so the page never joins them (Windows uses "\").
                dirs.append({"name": entry.name, "path": str(entry), "protected": is_protected(entry)})
            elif entry.suffix.lower() in extensions:
                media += 1
        except OSError:
            continue
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else "",
            "dirs": dirs[:MAX_FOLDER_ENTRIES], "truncated": len(dirs) > MAX_FOLDER_ENTRIES,
            "mediaFiles": media, "protected": is_protected(path), "home": str(Path.home()),
            "pictures": str(pictures_folder())}


# --- HTTP --------------------------------------------------------------------------
class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, web: WebFront, token: str, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.web = web
        self.token = token
        self.stop = threading.Event()
        self.visited = threading.Event()
        self.last_contact: float | None = None
        self.closing_at: float | None = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def contact(self) -> None:
        self.last_contact = time.monotonic()
        self.closing_at = None

    def page_gone(self) -> bool:
        now = time.monotonic()
        if self.closing_at is not None and now - self.closing_at > CLOSE_GRACE:
            return True
        return self.last_contact is not None and now - self.last_contact > HEARTBEAT_TIMEOUT


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = NAME

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name
        log.debug("web: " + format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _host_ok(self) -> bool:
        # A page elsewhere could reach 127.0.0.1 through a name it controls
        # (DNS rebinding); the Host header then carries that name.
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _cookie_token(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == cookie_name(self.server.port):
                return value
        return ""

    def _authorized(self) -> bool:
        return hmac.compare_digest(self._cookie_token().encode(), self.server.token.encode())

    def _body(self, json_required: bool) -> dict:
        if self.headers.get("Origin", "") != f"http://{self.headers.get('Host', '')}":
            raise ApiError(403, f"Requests must come from {NAME}'s own page.")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length) if length else b""
        if not json_required:
            return {}
        # A form on another site cannot send this type without asking first.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ApiError(415, "Expected JSON.")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ApiError(400, f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object.")
        return data

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self._send_head(status, len(body), content_type, headers)
        self.wfile.write(body)

    def _send_head(self, status: int, length: int, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        for name, value in {"Content-Type": content_type, "Content-Length": str(length),
                            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                            "Referrer-Policy": "no-referrer", **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()

    def _send_json(self, status: int, data) -> None:
        # File names that aren't valid UTF-8 keep their \udcXX escapes as JSON escapes.
        self._send(status, json.dumps(data, ensure_ascii=True).encode("ascii"), "application/json; charset=utf-8")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                raise ApiError(400, "Unexpected host.")
            url = urlsplit(self.path)
            if method == "GET" and url.path == "/":
                self._index(url.query)
                return
            if not self._authorized():
                raise ApiError(403, f"Open {NAME} through the address it printed in the terminal.")
            if method == "GET" and url.path == "/favicon.ico":  # asked for by a tab with a picture
                self._static("logo.svg")
                return
            if method == "GET" and url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
                return
            if not url.path.startswith("/api/"):
                raise ApiError(404, "Not found.")
            if method == "GET" and url.path.startswith("/api/preview/"):
                size = (parse_qs(url.query).get("size") or ["screen"])[0]
                data = self.server.web.preview(url.path[len("/api/preview/"):], size)
                self._send(200, data, "image/jpeg", {"Cache-Control": "private, max-age=600"})
                return
            if method == "GET" and url.path.startswith("/api/media/"):
                self._media(self.server.web.media(url.path[len("/api/media/"):]))
                return
            if method == "GET" and url.path.startswith("/api/file/"):
                file_id = url.path[len("/api/file/"):]
                path = self.server.web.media(file_id)
                if path.suffix.lower() in VIDEO_EXTENSIONS:
                    self._media(path)
                    return
                data, content_type = self.server.web.original(file_id)
                self._send(200, data, content_type, {"Cache-Control": "private, max-age=600",
                                                     "Content-Security-Policy": _FILE_CSP})
                return
            body = self._body(json_required=url.path != "/api/closing") if method == "POST" else {}
            self.server.contact()
            result = self._api(method, url.path, parse_qs(url.query), body)
            self._send_json(200, result)
            # Only once the answer is out: stopping first could end the
            # program while the answer is still being written.
            if url.path == "/api/closing":
                self.server.closing_at = time.monotonic()
            elif url.path == "/api/close":
                self.wfile.flush()
                self.server.stop.set()
        except (BrokenPipeError, ConnectionResetError):
            return  # the page went away mid-answer (a closed tab, a reload)
        except ApiError as exc:
            if self.path.startswith("/api/"):
                self._send_json(exc.status, {"error": exc.message})
            else:
                self._send(exc.status, exc.message.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as exc:  # a bug must not kill the server thread silently
            log.exception("web request failed: %s %s", method, self.path)
            self._send_json(500, {"error": f"Internal error: {exc}"})

    def _media(self, path: Path) -> None:
        """A video for the browser's player, in the byte ranges it asks for. A
        request for no range (a download) gets all of it."""
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:  # the last n bytes
                start = max(0, size - int(match[2]))
            if start >= size or start > end:
                self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                return
            status = 206
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=600"}
        with open(path, "rb") as handle:
            if status == 200:
                self._send_head(200, size, _video_type(path), headers)
                shutil.copyfileobj(handle, self.wfile, MEDIA_CHUNK)
                return
            handle.seek(start)
            data = handle.read(min(end - start + 1, MEDIA_CHUNK))
        headers["Content-Range"] = f"bytes {start}-{start + len(data) - 1}/{size}"
        self._send(206, data, _video_type(path), headers)

    def _index(self, query: str) -> None:
        given = (parse_qs(query).get("token") or [""])[0]
        if given and hmac.compare_digest(given.encode(), self.server.token.encode()):
            self._send(303, b"", "text/plain", {
                "Location": "/",
                "Set-Cookie": f"{cookie_name(self.server.port)}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
            })
            return
        if not self._authorized():
            raise ApiError(403, f"Open {NAME} through the address it printed in the terminal.")
        self._static("index.html")

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "Not found.")
        content_type = _CONTENT_TYPES.get(target.suffix)
        if content_type is None:
            raise ApiError(404, "Not found.")
        headers = {"Content-Security-Policy": _CSP} if target.suffix == ".html" else None
        self._send(200, target.read_bytes(), content_type, headers)

    def _api(self, method: str, path: str, query: dict, body: dict):
        web = self.server.web
        if method == "GET" and path == "/api/state":
            self.server.visited.set()  # the flow starts with the first visit
            since = (query.get("since") or ["0"])[0]
            since_rev = int(since) if since.isdigit() else 0
            # Long poll: wait briefly for something new, so progress shows
            # without the page asking ten times a second.
            web.wait_for_change(since_rev, timeout=5.0 if since_rev else 0)
            return web.snapshot(since_rev)
        if method == "POST" and path == "/api/answer":
            qid = body.get("id")
            if not isinstance(qid, int):
                raise ApiError(400, "Which question?")
            web.answer(qid, body.get("value"))
            return {"ok": True}
        if method == "GET" and path == "/api/folders":
            return list_folders((query.get("path") or [""])[0])
        if method == "POST" and path == "/api/reveal":
            from .viewer import show_in_folder
            file_id = body.get("id")
            if not isinstance(file_id, str):
                raise ApiError(400, "Which file?")
            how = show_in_folder(web.media(file_id))
            if how is None:
                raise ApiError(500, "No file manager could be started.")
            return {"how": how}
        if method == "POST" and path == "/api/stop-tool":
            from . import handoff
            return {"stopped": handoff.interrupt()}
        if method == "POST" and path == "/api/update":
            web.start_update()
            return {"ok": True}
        if method == "POST" and path == "/api/quit":
            if web.finished is None and not web.cancel():
                raise ApiError(409, f"{NAME} is busy; it stops at its next question.")
            return {"ok": True}
        if method == "POST" and path in ("/api/ping", "/api/closing", "/api/close"):
            return {"ok": True}
        raise ApiError(404, "Not found.")


def _say(message: str) -> None:
    """A line for the terminal keepbest was started in, which the page doesn't reach."""
    print(message, flush=True)


def _exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    return exc.code if isinstance(exc.code, int) else 1


def _check_for_update(web: WebFront) -> None:
    version = selfupdate.available()
    if version is not None:
        web.offer_update(version, selfupdate.how_installed(version))


def _server(web: WebFront, port: int) -> tuple[_Server, bool]:
    """The page's server; after an update on the address the old keepbest had
    (True), so its tab carries on. Taken meanwhile: a new address."""
    restarted = os.environ.pop(RESTART_ENV, "")
    old_port, _, old_token = restarted.partition(" ")
    if old_port.isdigit() and old_token:
        try:
            return _Server(web, old_token, int(old_port)), True
        except OSError:
            pass
    return _Server(web, secrets.token_urlsafe(24), port), False


def serve(flow, open_browser: bool = True, port: int = 0, announce=_say, echo: bool = True,
          check_updates: bool = False) -> int:
    """Run *flow* (no arguments, returns an exit code) with the page as its
    front end, in this thread; returns its exit code. With *echo* the
    terminal gets a log of the messages; with *check_updates* the page offers
    a newer keepbest, and after installing it this process becomes the new one."""
    from . import running

    web = WebFront(echo=echo)
    server, restarted = _server(web, port)
    token = server.token
    if restarted:  # the tab is still open
        open_browser = False
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    web.page_url = f"http://127.0.0.1:{server.port}/"  # the cookie is set by then
    running.set_address(url)
    threading.Thread(target=server.serve_forever, name=f"{NAME}-web", daemon=True).start()
    announce(f"{NAME} is running at {url}")
    opened = open_browser and webbrowser.open(url)
    if not opened and not restarted:  # after an update the tab is still open
        announce("Open that address in your browser.")
        if os.environ.get("SSH_CONNECTION"):
            announce(f"Over SSH, forward the port first: ssh -L {server.port}:127.0.0.1:{server.port} <this computer>")
    announce("Close the page when done, or press Ctrl+C here.")
    sys.stdout.flush()

    def watch() -> None:
        while not server.stop.wait(1.0):
            # While the tab shows media-optimizer's page, keepbest's isn't gone.
            if server.page_gone() and not web.gone and not web.away:
                web.page_gone()
        if web.finished is None:  # closed while the flow still runs: nobody answers any more
            web.page_gone()

    threading.Thread(target=watch, name=f"{NAME}-watch", daemon=True).start()
    if check_updates:
        threading.Thread(target=_check_for_update, args=(web,), name=f"{NAME}-update-check", daemon=True).start()
    code = 130
    interrupted = False
    restart = False
    try:
        while not server.visited.wait(0.5):
            if server.stop.is_set():
                return 0
        front_mod.use(web)
        try:
            code = flow()
        except SystemExit as exc:
            code = _exit_code(exc)
        except Restart:
            code, restart = 0, True
        except Cancelled:
            code = 130
            announce("The page was closed: stopped at the open question. Saved progress is kept.")
        except KeyboardInterrupt:  # Ctrl+C or a SIGTERM here, not on the page
            code, interrupted = 130, True
            announce("\nProgram aborted by user.")
        except Exception as exc:  # shown on the page and here, not lost
            code = 1
            web.show("error", {"text": f"{NAME} stopped on an error: {exc}"})
            traceback.print_exc()
        finally:
            front_mod.use(None)
            if not restart:
                web.finish(code)
        # Let the page show the end until it is closed or gone; stopped from
        # the terminal, only for a moment, so the page still learns of it.
        # Updated, the page shows that keepbest starts again, then waits for the new one.
        end = time.monotonic() + (RESTART_GRACE if restart else INTERRUPT_GRACE) if interrupted or restart else None
        while not server.stop.wait(0.2 if end is not None else 1.0):
            if web.gone or (server.page_gone() and not web.away) or (end is not None and time.monotonic() > end):
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        running.set_address(None)
    if restart:
        running.release()
        announce(f"Starting {NAME} {web.update['version']}.")
        os.environ[RESTART_ENV] = f"{server.port} {token}"
        selfupdate.restart(selfupdate.restart_argv())
    return code
