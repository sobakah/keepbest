"""The browser page: keepbest's flow, unchanged, with a page as its front end.

The flow keeps the main thread: handoff.py and the interrupt handling set
signal handlers, which Python allows only there. The HTTP server and a watcher
run in threads. WebFront collects what the flow shows and holds the question
it waits on; the page asks for what is new and sends the key of the button
pressed, the same key a user types in the terminal. Only this machine can
connect, and only with the token from the printed address, which the first
visit turns into a cookie (as in lyrembed and cdimport).

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
from .config import NAME
from .core_logic import format_size
from .front import (AfterDryRunScreen, DecisionScreen, EndScreen, FolderRow, FolderScreen, Front, HandledScreen,
                    Note, OverviewScreen, RemuxScreen, SettingRow, StartScreen)
from .image_utils import IMAGE_EXTENSIONS, preview_jpeg
from .video import VIDEO_EXTENSIONS

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",  # keepbest's own icon only
}
_CSP = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'")
# Cookies belong to a host, not a port: two pages on 127.0.0.1 (a second
# keepbest, or another program's page) would overwrite each other's token
# under one name. The port goes into the name.
COOKIE = f"{NAME}_token"
# The page pings every 15 s, and Chrome runs a hidden tab's timers once a
# minute, so only longer silence means it is gone. A reload sends "closing"
# too, then pings again within the grace.
HEARTBEAT_TIMEOUT = 90.0
CLOSE_GRACE = 10.0
MAX_BODY = 64 * 1024
MAX_FOLDER_ENTRIES = 500
# How often a progress counter is sent on to the page at most.
PROGRESS_INTERVAL = 0.2
# Longest side of a preview: for the group view, and for zooming in.
PREVIEW_SIZES = {"screen": 1200, "zoom": 3000}
PREVIEW_CACHE = 40


def cookie_name(port: int) -> str:
    return f"{COOKIE}_{port}"


class Cancelled(KeyboardInterrupt):
    """The page was closed, or quit, while the flow waited for an answer: the
    flow stops as on Ctrl+C at that question (progress saved, summary)."""


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


def visual_group(group, rel, preview=None) -> dict:
    """A visual group for the page; *preview* gives a picture's preview id."""
    worst = max(range(len(group.diffs)), key=lambda i: -1 if group.diffs[i] is None else group.diffs[i])
    return {
        "images": [{"name": rel(meta["path"]), "format": meta.get("format"), "type": _lossless(meta),
                    "width": meta.get("width"), "height": meta.get("height"), "size": format_size(meta["size"]),
                    "bpp": round(meta["bpp"], 2) if meta.get("bpp") is not None else None, "diff": diff,
                    "spot": meta.get("spot"), "preview": preview(meta["path"]) if preview else None}
                   for meta, diff in zip(group.images, group.diffs)],
        # where the group differs most: the spot of its least similar image
        "spot": group.images[worst].get("spot"),
        "worst": group.worst, "limit": group.limit, "borderline": group.borderline,
        "borderlineAbove": group.borderline_above, "status": group.status, "kept": group.kept,
        "removed": group.removed, "rename": group.rename,
    }


def exact_group(group, rel) -> dict:
    return {"kind": group.kind, "tracks": group.tracks, "status": group.status,
            "files": [{"name": rel(f["path"]), "size": format_size(f["size"]), "streams": f.get("streams", "")}
                      for f in group.files]}


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


def screen_data(screen, preview=None) -> dict:
    """A Screen as the page gets it: plain data, no paths outside its texts.
    *preview* gives a picture's preview id (WebFront.preview_id)."""
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
        data.update(position=screen.position, group=visual_group(screen.group, screen.rel, preview))
        if isinstance(screen, HandledScreen):
            data["note"] = screen.note.strip()
    elif isinstance(screen, OverviewScreen):
        data.update(current=screen.current, groups=[
            {"key": str(n), "keeper": screen.rel(g.images[g.kept if g.kept is not None else 0]["path"]),
             "status": g.status, "borderline": g.borderline, "worst": g.worst, "limit": g.limit,
             "count": len(g.images)}
            for n, g in enumerate(screen.groups, 1)])
    elif isinstance(screen, RemuxScreen):
        data.update(number=screen.number, total=screen.total, group=exact_group(screen.group, screen.rel))
    elif isinstance(screen, AfterDryRunScreen):
        data.update(realMode=screen.real_mode)
    else:
        assert isinstance(screen, EndScreen)
    return data


def message_data(kind: str, data: dict) -> dict:
    """A message as the page gets it."""
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
        return {"group": exact_group(data["group"], data["rel"]), "keepIndex": data.get("keep_index", 0)}
    if kind == "group_line":
        group = data["group"]
        return {"position": data["position"], "worst": group.worst, "limit": group.limit,
                "borderline": group.borderline}
    if kind == "group_table":
        return {"group": visual_group(data["group"], data["rel"]), "keepIndex": data.get("keep_index", 0),
                "done": data.get("done", False)}
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

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._rev = 0
        self.events: list[dict] = []
        self.question: dict | None = None
        self._screen = None
        self._answers: dict[int, object] = {}
        self._next_question = 0
        self.finished: int | None = None
        self.gone = False
        # Start screen: a choice the page picked directly, reached by pressing
        # the row's key until it shows (the flow only knows "next choice").
        self._target: tuple[str, int] | None = None
        self._presses = 0
        # Pictures the page may show, by random id: the only files /api/preview
        # reads, so the page can never make keepbest open anything else.
        self._previews: dict[str, Path] = {}
        self._preview_ids: dict[Path, str] = {}
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
        if kind in ("blank", "goodbye"):
            return
        self._add({"kind": kind, **message_data(kind, data)})

    def progress(self, label: str, total: int):
        return _Counter(self, label, total)

    def transient(self, text: str):
        return _Status(self, text)

    # questions
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
        data = screen_data(screen, self.preview_id)
        answer = self._ask("screen", data, screen)
        if not isinstance(screen, (StartScreen, FolderScreen)):  # forms: the screen itself shows the change
            self._add({"kind": "answer", "prompt": data["prompt"], "text": _key_text(screen, answer)})
        return answer

    def preview_id(self, path: Path) -> str:
        with self._cond:
            if path not in self._preview_ids:
                image_id = secrets.token_urlsafe(9)
                self._preview_ids[path], self._previews[image_id] = image_id, path
            return self._preview_ids[path]

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
        key, index = target
        row = next((r for r in screen.rows if isinstance(r, SettingRow) and r.key == key and r.locked is None), None)
        if row is None or list(row.options).index(row.current) == index or self._presses > len(row.options):
            self._target = None
            return None
        self._presses += 1
        return key

    def confirm(self, question: str, default: bool = False, tone: str = "") -> bool:
        answer = self._ask("confirm", {"prompt": question, "default": default, "tone": tone})
        self._add({"kind": "answer", "prompt": question, "text": "Yes" if answer else "No"})
        return answer

    def ask_path(self, prompt: str) -> str:
        return self._ask("path", {"prompt": prompt.strip().rstrip(":")})

    def answer(self, qid: int, value: object) -> None:
        """The page's answer to question *qid*, checked against what it asked."""
        with self._cond:
            question = self.question
            if question is None or question["id"] != qid:
                raise ApiError(409, "That question is no longer open.")
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
            self._target, self._presses = (key, index), 1
            return key
        if not isinstance(value, str) or value not in question["keys"]:
            raise ApiError(400, f"Not an option here: {value!r}")
        return value

    def cancel(self) -> bool:
        """Stop the flow at the open question; False when none is open."""
        with self._cond:
            if self.question is None:
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
            return {"rev": self._rev, "events": [e for e in self.events if e["rev"] > since],
                    "question": self.question, "finished": self.finished}

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
        self.send_response(status)
        for name, value in {"Content-Type": content_type, "Content-Length": str(len(body)),
                            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                            "Referrer-Policy": "no-referrer", **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

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


def serve(flow, open_browser: bool = True, port: int = 0, announce=_say) -> int:
    """Run *flow* (no arguments, returns an exit code) with the page as its
    front end, in this thread; returns its exit code."""
    from . import running

    token = secrets.token_urlsafe(24)
    web = WebFront()
    server = _Server(web, token, port)
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    running.set_address(url)
    threading.Thread(target=server.serve_forever, name=f"{NAME}-web", daemon=True).start()
    announce(f"{NAME} is running at {url}")
    opened = open_browser and webbrowser.open(url)
    if not opened:
        announce("Open that address in your browser.")
    announce("Close the page when done, or press Ctrl+C here.")
    sys.stdout.flush()

    def watch() -> None:
        while not server.stop.wait(1.0):
            if server.page_gone() and not web.gone:
                web.page_gone()
        if web.finished is None:  # closed while the flow still runs: nobody answers any more
            web.page_gone()

    threading.Thread(target=watch, name=f"{NAME}-watch", daemon=True).start()
    code = 130
    try:
        while not server.visited.wait(0.5):
            if server.stop.is_set():
                return 0
        front_mod.use(web)
        try:
            code = flow()
        except SystemExit as exc:
            code = _exit_code(exc)
        except Cancelled:
            code = 130
            announce("The page was closed: stopped at the open question. Saved progress is kept.")
        except KeyboardInterrupt:
            code = 130
            announce("\nProgram aborted by user.")
        except Exception as exc:  # shown on the page and here, not lost
            code = 1
            web.show("error", {"text": f"{NAME} stopped on an error: {exc}"})
            traceback.print_exc()
        finally:
            front_mod.use(None)
            web.finish(code)
        # Let the page show the end until it is closed or gone.
        while not server.stop.wait(1.0):
            if web.gone or server.page_gone():
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        running.set_address(None)
    return code
