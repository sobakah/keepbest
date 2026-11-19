"""keepbest on the D-Bus session bus, for picktag and other programs.

Why: a sandboxed program (picktag as a Flatpak) can't start host programs,
but it may talk to a bus name it is allowed
(``--talk-name=io.github.sobakah.keepbest``). The bus starts this service
when it is called (a service file in ``~/.local/share/dbus-1/services``,
written by ``--install-dbus-service`` or ``--install``), so keepbest stays a
pipx program on the host. The loop and the service file are lyrembed's
(``dbus_service.py`` there, itself from cdport), copied, not imported: the
programs stay independent.

Every call only opens keepbest's page: a run started here never deletes
anything without the user deciding it on the page. So nothing a call sends
becomes an option of its own (no --auto, --permanent, --config): the calls
take folders, files and a dry run. The page's address never leaves this
side: its token opens the page's whole API, and a Flatpak shares the host's
127.0.0.1, so the service opens the browser itself.

Paths are byte strings (``ay``): D-Bus strings must be UTF-8 and file names
need not be. One trailing NUL is allowed (GLib's bytestrings have it).

Interface ``io.github.sobakah.keepbest1`` on ``/io/github/sobakah/keepbest``:

- ``GetInfo() -> a{sv}``: Version, InterfaceVersion, GroupsFormat (the
  ``--groups`` format CompareGroups writes), JournalFile ("" when off) and
  DeleteMode (the config's: trash, permanent or dry_run).
- ``OpenPage(ay folder) -> (s run, b opened)``: the page on folder (empty:
  the page asks for one), opened in the browser once it is up; opened is
  false when no browser could be started. A page already open on that
  folder is shown again instead of a second one.
- ``RemoveCopies(a(ayaay) scans, a{sv} options) -> (s run, b opened)``:
  one page after the other, each ``keepbest FOLDER -r --exclude …`` (the
  excluded folders relative to it, "." its own files); a page closed at a
  question (130) ends the rest. Option DryRun (b).
- ``CompareGroups(aaay groups, a{sv} options) -> (s run, b opened)``:
  ``keepbest --groups`` with these groups of absolute paths. Options DryRun
  (b) and From (s, the sender's name on the page).
- ``ShowPage(s run) -> b``: open a run's page in the browser again.
- ``Runs() -> a(ssay)``: the runs still going: run, kind ("page", "copies"
  or "groups"), the folder (of the current scan; the groups' common folder).
- ``Stop(s run)``: as Ctrl+C: progress is saved; only the first Stop counts.
- Signal ``Finished(s run, i exit_code, s log)``: a run ended, with
  keepbest's exit code (0 done, 1 some files could not be removed, 2
  refused, 130 the page was closed at a question or the run stopped; for
  RemoveCopies the last error's), and the file with its output.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import re
import secrets
import shlex
import signal
import subprocess
import threading
import time
import traceback
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import container, running, ui
from .config import NAME, __version__, load_config
from .session import state_dir

BUS_NAME = "io.github.sobakah.keepbest"
OBJECT_PATH = "/io/github/sobakah/keepbest"
INTERFACE = "io.github.sobakah.keepbest1"
INTERFACE_VERSION = 1
ERROR = INTERFACE + ".Error"
# Without runs or calls for this long, the service ends; the bus starts it
# again at the next call. Short, so an old version doesn't stay after pipx
# updated keepbest.
IDLE_EXIT = 60.0
# How long a call waits for the page before giving up on the run: below the
# 25 s D-Bus clients (GDBus, libdbus) wait for a reply by default, else the
# caller gives up first and a page starts nobody sees.
URL_TIMEOUT = 20.0
# A page nobody opened ends after this long (web.serve's unvisited): the
# browser may not have come up.
UNVISITED_TIMEOUT = 600.0
# Limits for one call, so it can't tie up the service.
MAX_SCANS = 1000
MAX_FILES = 200_000
MAX_FROM = 100
KEEP_LOGS = 20                          # the newest run logs kept in the service folder
STALE_GROUPS = 24 * 3600.0              # a groups file left by a crash is removed after this

INTROSPECTION = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTERFACE}">
    <method name="GetInfo"><arg name="info" type="a{{sv}}" direction="out"/></method>
    <method name="OpenPage">
      <arg name="folder" type="ay" direction="in"/>
      <arg name="run" type="s" direction="out"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="RemoveCopies">
      <arg name="scans" type="a(ayaay)" direction="in"/>
      <arg name="options" type="a{{sv}}" direction="in"/>
      <arg name="run" type="s" direction="out"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="CompareGroups">
      <arg name="groups" type="aaay" direction="in"/>
      <arg name="options" type="a{{sv}}" direction="in"/>
      <arg name="run" type="s" direction="out"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="ShowPage">
      <arg name="run" type="s" direction="in"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="Runs"><arg name="runs" type="a(ssay)" direction="out"/></method>
    <method name="Stop"><arg name="run" type="s" direction="in"/></method>
    <signal name="Finished">
      <arg name="run" type="s"/><arg name="exit_code" type="i"/><arg name="log" type="s"/>
    </signal>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg name="xml" type="s" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Peer">
    <method name="Ping"/>
  </interface>
</node>
"""


def _in_signatures(introspection: str) -> dict[str, str]:
    """Each method's input signature, from the introspection data, so the
    calls are declared once."""
    root = ElementTree.fromstring(introspection.split("\n", 2)[2])   # without the DOCTYPE
    interface = next(i for i in root.iter("interface") if i.get("name") == INTERFACE)
    return {m.get("name"): "".join(a.get("type") for a in m.iter("arg") if a.get("direction") == "in")
            for m in interface.iter("method")}


_SIGNATURES = _in_signatures(INTROSPECTION)


def _running_pattern() -> re.Pattern:
    """The page's first line (web.RUNNING_LINE): its address, again after an
    update on the page restarted it. Runs get --color never, so it is never
    wrapped in colour codes."""
    from .web import RUNNING_LINE

    return re.compile("^" + re.escape(RUNNING_LINE).replace(
        re.escape("{url}"), r"(http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)") + "$")


# Page addresses in a run's log lose their token: the log is read by other
# programs (Finished names it), and the token opens the page's whole API.
_TOKEN = re.compile(rb"(token=)[A-Za-z0-9_-]+")


def redacted(line: bytes) -> bytes:
    return _TOKEN.sub(rb"\1...", line)


NEEDS_JEEPNEY = (f"The D-Bus service needs the Python package jeepney, which pipx installs with {NAME} "
                 "(in a checkout: pip install jeepney).")


class ServiceError(Exception):
    """A call that cannot be answered: becomes a D-Bus error reply."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


def _invalid(message: str) -> ServiceError:
    return ServiceError("org.freedesktop.DBus.Error.InvalidArgs", message)


@dataclass
class Run:
    id: str
    kind: str                     # "page", "copies" or "groups"
    log: Path
    folders: list[str]            # every folder it will work on (busy checks); "" for none
    commands: list[list[str]]     # the keepbest runs still to start, in turn
    process: Any = None           # subprocess.Popen, or a test's stand-in
    started: float = 0.0          # when the current process started
    step: int = 0                 # which of the commands runs now (1-based)
    url: str = ""                 # never sent over the bus (see the module)
    shown: set = field(default_factory=set)       # addresses opened in the browser
    waiting: list = field(default_factory=list)   # calls waiting for the first page
    stopping: bool = False        # Ctrl+C sent: never a second one, nothing more started
    replying: int = 0             # browser replies still to send: Finished waits for them
    code: int = 0                 # the last error's exit code so far
    groups_file: Path | None = None   # CompareGroups' file, removed at the end

    @property
    def folder(self) -> str:
        return self.folders[min(max(self.step, 1), len(self.folders)) - 1] if self.folders else ""


def run_command(*arguments: str) -> list[str]:
    """A page of the keepbest this service runs from (pipx launcher or checkout)."""
    from .desktop import launch_command

    return [*launch_command(), *arguments, "--no-browser", "--color", "never",
            "--unvisited-timeout", f"{UNVISITED_TIMEOUT:.0f}"]


def spawn_run(command: list[str]) -> subprocess.Popen:
    # Its own session, so the service's terminal or process group signals
    # don't reach it. That alone does not protect it under dbus-broker, which
    # runs the service as a systemd unit and stops the unit's cgroup with
    # it: serve therefore never ends while runs are going (_outlive_runs).
    # Its output as bytes: names that aren't UTF-8 reach the log unchanged
    # (PYTHONIOENCODING: before 3.15 printing them to a pipe would fail).
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True, cwd=Path.home(),
                            env={**os.environ, "PYTHONIOENCODING": "utf-8:surrogateescape"})


def ctrl_c(process: Any) -> None:
    """SIGINT to the run's process group, as Ctrl+C in a terminal: it reaches a
    media-optimizer started from the page too (keepbest ignores SIGINT meanwhile)."""
    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, OSError):
        pass   # ended meanwhile; its exit is reported as usual


def service_folder() -> Path:
    """The runs' logs and CompareGroups' files."""
    return state_dir() / "service"


def _overlap(a: str, b: str) -> bool:
    return running._overlaps(Path(a), Path(b))


def _path(raw: Any, what: str, absolute: bool = True) -> str:
    """A path from the bus (bytes) as Python names files (surrogateescape)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise _invalid(f"{what} is a byte string (ay).")
    raw = bytes(raw)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if b"\0" in raw:
        raise _invalid(f"{what} has a NUL byte inside.")
    if absolute and not raw.startswith(b"/"):
        raise _invalid(f"{what} must be an absolute path: {raw!r}")
    return os.fsdecode(raw)


def _options(options: dict, known: dict[str, str]) -> dict[str, Any]:
    out = {}
    for key, (sig, value) in options.items():
        if key not in known:
            raise _invalid(f"Unknown option {key} (known: {', '.join(known)}).")
        if sig != known[key]:
            raise _invalid(f"{key} is ({known[key]}), not ({sig}).")
        out[key] = value
    return out


class Service:
    """The calls and the runs, without the bus: call() answers a method call,
    outgoing() turns what the runs and the browser reported into replies and
    signals."""

    def __init__(self, spawn: Callable[[list[str]], Any] = spawn_run,
                 clock: Callable[[], float] = time.monotonic,
                 open_url: Callable[[str], bool] = container.open_url,
                 interrupt: Callable[[Any], None] = ctrl_c) -> None:
        self.runs: dict[str, Run] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self._spawn = spawn
        self._clock = clock
        self._open_url = open_url
        self._interrupt_process = interrupt
        self._ids = itertools.count(1)
        self._pending = 0           # calls answered later: a browser being opened
        self._page = _running_pattern()

    def busy(self) -> bool:
        return bool(self.runs) or self._pending > 0

    # --- method calls -----------------------------------------------------
    def call(self, member: str, signature: str, args: tuple, caller: Any) -> tuple[str, tuple] | None:
        """(signature, body) of the reply, or None when it comes later (the
        calls that open a page). Raises ServiceError."""
        if member not in _SIGNATURES:
            raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod", f"No method {member} in {INTERFACE}.")
        if signature != _SIGNATURES[member]:
            raise _invalid(f"{member} takes ({_SIGNATURES[member]}), not ({signature}).")
        if member == "GetInfo":
            return "a{sv}", (self.info(),)
        if member == "OpenPage":
            self.open_page(args[0], caller)
        elif member == "RemoveCopies":
            self.remove_copies(args[0], args[1], caller)
        elif member == "CompareGroups":
            self.compare_groups(args[0], args[1], caller)
        elif member == "ShowPage":
            self.show_page(args[0], caller)
        elif member == "Runs":
            return "a(ssay)", ([(r.id, r.kind, os.fsencode(r.folder)) for r in self.runs.values()],)
        else:
            self.stop(args[0])
            return "", ()
        return None

    @staticmethod
    def info() -> dict[str, tuple[str, Any]]:
        from .app import GROUPS_VERSION
        from .journal import resolve_journal_path

        # Read at each call: it may have changed. A broken file gives the
        # defaults, as at keepbest's start.
        config = load_config()[0]
        journal = resolve_journal_path(config.get("journal_file"))
        return {"Version": ("s", __version__),
                "InterfaceVersion": ("u", INTERFACE_VERSION),
                "GroupsFormat": ("u", GROUPS_VERSION),
                # A D-Bus string is UTF-8: a name that isn't comes with "?".
                "JournalFile": ("s", str(journal).encode("utf-8", "replace").decode() if journal else ""),
                "DeleteMode": ("s", str(config.get("delete_mode", "trash")))}

    @staticmethod
    def _folder(raw: Any, what: str = "The folder") -> str:
        from .app import is_protected, protected_message

        path = Path(_path(raw, what))
        if not path.is_dir():
            raise ServiceError(ERROR + ".NoFolder", f"There is no folder {path}.")
        path = path.resolve()
        if is_protected(path):
            raise ServiceError(ERROR + ".Protected", protected_message(path))
        return str(path)

    def _check_free(self, folders: list[str]) -> None:
        """Busy when a run of this service or another keepbest works on an
        overlapping folder. A guard, not the lock: keepbest claims the folder
        itself once the page is opened."""
        for folder in folders:
            for run in self.runs.values():
                if any(f and _overlap(f, folder) for f in run.folders):
                    raise ServiceError(ERROR + ".Busy", f"Run {run.id} is working on {run.folder or folder}.")
            other = running.other(Path(folder))
            if other is not None:   # without its page's address (see the module)
                raise ServiceError(ERROR + ".Busy", f"Another {NAME} (process {other.get('pid')}) is working "
                                                    f"on {other.get('folder')}.")

    def open_page(self, raw: Any, caller: Any) -> Run:
        folder = self._folder(raw) if raw not in (b"", b"\0") else ""
        if folder:
            same = next((r for r in self.runs.values() if r.kind == "page" and r.folders == [folder]), None)
            if same is not None:   # shown again, not a second keepbest on the folder
                if same.url:
                    self._show(same, [caller])
                else:
                    same.waiting.append(caller)
                return same
            self._check_free([folder])
        command = run_command(*([folder] if folder else []))
        return self._start("page", [command], [folder] if folder else [], caller)

    def remove_copies(self, scans: list, options: dict, caller: Any) -> Run:
        chosen = _options(options, {"DryRun": "b"})
        if not scans:
            raise _invalid("No folders to scan.")
        if len(scans) > MAX_SCANS:
            raise _invalid(f"{len(scans)} folders; at most {MAX_SCANS} in one call.")
        folders, commands = [], []
        for raw_folder, raw_excluded in scans:
            folder = self._folder(raw_folder)
            excluded = []
            for raw in raw_excluded:
                name = _path(raw, "An excluded folder", absolute=False)
                if not name or name.startswith("/") or ".." in Path(name).parts:
                    raise _invalid(f"Excluded folders are relative to their folder, without '..': {name!r}")
                excluded.append(name)
            folders.append(folder)
            commands.append(run_command(folder, "-r", *(f"--exclude={e}" for e in excluded),
                                        *(["--dry-run"] if chosen.get("DryRun") else [])))
        self._check_free(folders)
        return self._start("copies", commands, folders, caller)

    def compare_groups(self, groups: list, options: dict, caller: Any) -> Run:
        from .app import GROUPS_VERSION

        chosen = _options(options, {"DryRun": "b", "From": "s"})
        sender = str(chosen.get("From", "")).strip()
        if len(sender) > MAX_FROM:
            raise _invalid(f"From is at most {MAX_FROM} characters.")
        if not groups:
            raise _invalid("No groups.")
        if sum(len(g) for g in groups) > MAX_FILES:
            raise _invalid(f"More than {MAX_FILES} files; send fewer groups at a time.")
        paths = [[os.path.normpath(_path(raw, "A file")) for raw in group] for group in groups]
        files = [p for g in paths for p in g]
        if not files:
            raise _invalid("The groups have no files.")
        # The folder keepbest locks for the run (app.groups_folder, before it
        # leaves out what it can't use).
        folder = os.path.commonpath([os.path.dirname(p) for p in files])
        self._check_free([folder])
        service = service_folder()
        service.mkdir(parents=True, exist_ok=True)
        groups_file = service / f"groups-{secrets.token_hex(6)}.json"
        data = {"v": GROUPS_VERSION, **({"from": sender} if sender else {}), "groups": paths}
        # ensure_ascii: a name that isn't UTF-8 goes as \udcXX, as load_groups reads it
        groups_file.write_text(json.dumps(data), encoding="ascii")
        command = run_command("--groups", str(groups_file), *(["--dry-run"] if chosen.get("DryRun") else []))
        try:
            run = self._start("groups", [command], [folder], caller)
        except BaseException:
            groups_file.unlink(missing_ok=True)
            raise
        run.groups_file = groups_file
        return run

    def show_page(self, run_id: str, caller: Any) -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError(ERROR + ".UnknownRun", f"No run {run_id!r} (it may have ended).")
        if not run.url:
            raise ServiceError(ERROR + ".NotUp", f"The page of run {run_id} is not up yet.")
        self._show(run, [caller], with_run=False)

    def stop(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError(ERROR + ".UnknownRun", f"No run {run_id!r} (it may have ended).")
        self._interrupt(run)

    def _interrupt(self, run: Run) -> None:
        if run.stopping:
            return   # a second Ctrl+C would cut off the stopping of the first
        run.stopping = True
        self._interrupt_process(run.process)

    # --- runs -------------------------------------------------------------
    def _start(self, kind: str, commands: list[list[str]], folders: list[str], caller: Any) -> Run:
        run_id = str(next(self._ids))
        log = service_folder() / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{run_id}.log"
        run = Run(run_id, kind, log, folders, list(commands))
        self.runs[run.id] = run       # before its reader can report anything
        try:
            self._next(run)
        except ServiceError:          # keepbest can't be started: no run then
            del self.runs[run.id]
            raise
        run.waiting.append(caller)
        self._prune()
        return run

    def _next(self, run: Run) -> None:
        command = run.commands.pop(0)
        try:
            process = self._spawn(command)
        except OSError as exc:
            raise ServiceError(ERROR + ".Failed", f"Could not start {NAME}: {exc}") from exc
        run.process, run.started, run.url = process, self._clock(), ""
        run.step += 1
        total = run.step + len(run.commands)
        header = f"{NAME} {run.step} of {total}: {run.folder}" if total > 1 else ""
        threading.Thread(target=self._read, args=(run, process, header),
                         name=f"{NAME}-dbus-run-{run.id}", daemon=True).start()

    def _read(self, run: Run, process: Any, header: str) -> None:
        """In a thread per process: its output into the run's log, until it
        ends. Reads to the end so a run never blocks on a full pipe."""
        log = None
        try:
            run.log.parent.mkdir(parents=True, exist_ok=True)
            log = open(run.log, "ab")
            if header:
                log.write(os.fsencode(("\n" if run.step > 1 else "") + header + "\n\n"))
        except OSError:
            traceback.print_exc()   # the run goes on without a log
        try:
            for line in process.stdout:
                if log is not None:
                    try:
                        log.write(redacted(line))
                        log.flush()
                    except OSError:
                        log = None
                page = self._page.match(line.decode("utf-8", "replace").rstrip("\r\n"))
                if page:
                    self.events.put(("url", run.id, process, page.group(1)))
        finally:
            if log is not None:
                log.close()
            self.events.put(("exit", run.id, process, process.wait()))

    def _show(self, run: Run, callers: list, with_run: bool = True) -> None:
        """Open run's page in the browser, in a thread (a browser can take its
        time to start), and answer callers with whether it worked."""
        url = run.url
        run.shown.add(url)

        def open_it() -> None:
            try:
                opened = bool(self._open_url(url))
            except Exception:   # webbrowser has its own ideas; one failed open, not the service
                traceback.print_exc()
                opened = False
            for caller in callers:
                self.events.put(("reply", caller, "sb" if with_run else "b",
                                 (run.id, opened) if with_run else (opened,), run.id))
        self._pending += len(callers)
        run.replying += len(callers)
        threading.Thread(target=open_it, name=f"{NAME}-dbus-show-{run.id}", daemon=True).start()

    def _prune(self) -> None:
        """The newest run logs stay; groups files a crash left behind go."""
        try:
            logs = sorted(service_folder().glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in logs[KEEP_LOGS:]:
                old.unlink(missing_ok=True)
            mine = {r.groups_file for r in self.runs.values()}
            for stale in service_folder().glob("groups-*.json"):
                if stale not in mine and time.time() - stale.stat().st_mtime > STALE_GROUPS:
                    stale.unlink(missing_ok=True)
        except OSError:
            pass

    def _finish(self, run: Run, code: int, out: list[tuple]) -> None:
        del self.runs[run.id]
        if run.groups_file is not None:
            run.groups_file.unlink(missing_ok=True)
        out += [("error", caller, ERROR + ".Failed",
                 f"{NAME} ended (exit code {code}) before its page was up; see {run.log}.")
                for caller in run.waiting]
        out.append(("signal", "Finished", "sis", (run.id, int(code), str(run.log))))

    def outgoing(self) -> list[tuple]:
        """Replies and signals to send now: ("return", caller, signature,
        body), ("error", caller, name, message) or ("signal", member,
        signature, body)."""
        out: list[tuple] = []
        later = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "reply":
                self._pending -= 1
                if event[4] in self.runs:
                    self.runs[event[4]].replying -= 1
                out.append(("return", *event[1:4]))
                continue
            run = self.runs.get(event[1])
            if run is None or event[2] is not run.process:
                continue
            if kind == "exit" and run.replying:
                later.append(event)   # the caller learns of the run before it hears it ended
                continue
            if kind == "url":
                run.url = event[3]
                if run.waiting:
                    self._show(run, run.waiting)
                    run.waiting = []
                elif run.url not in run.shown and not run.stopping:
                    self._show(run, [])   # the next scan's page; after an update the tab is still open
                continue
            code = int(event[3])
            if code == 130 or run.stopping or not run.commands or run.waiting:
                self._finish(run, code if code == 130 or not run.code else run.code if code == 0 else code, out)
                continue
            if code != 0:
                run.code = code
            try:
                self._next(run)
            except ServiceError as exc:
                ui.error(str(exc))
                self._finish(run, 1, out)
        for event in later:
            self.events.put(event)
        for run in self.runs.values():
            if run.waiting and self._clock() - run.started > URL_TIMEOUT:
                out += [("error", caller, ERROR + ".Failed",
                         f"{NAME}'s page was not up after {URL_TIMEOUT:.0f} s; it is stopped.")
                        for caller in run.waiting]
                run.waiting = []
                self._interrupt(run)
        return out


# --- on the bus --------------------------------------------------------------
def serve(service: Service | None = None, idle_exit: float = IDLE_EXIT) -> int:
    """Own the bus name and answer calls until idle; the exit code."""
    try:
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        ui.error(NEEDS_JEEPNEY)
        return 2
    service = service or Service()
    # Started by the bus: answer on the bus that started us.
    bus = os.environ.get("DBUS_STARTER_ADDRESS") or "SESSION"
    try:
        conn = open_dbus_connection(bus=bus)
    except (OSError, KeyError, ValueError) as exc:
        ui.error(f"{NAME}: cannot connect to the D-Bus session bus: {exc}")
        return 1
    with conn:
        try:
            return _answer(conn, service, idle_exit)
        except KeyboardInterrupt:
            # Started by hand: Ctrl+C reaches the runs too, which have
            # sessions of their own and would otherwise be waited for.
            for run in list(service.runs.values()):
                service._interrupt(run)
            return 130
        finally:
            _outlive_runs(service)


def _outlive_runs(service: Service) -> None:
    """Wait for the runs still going, whatever ended the loop: under
    dbus-broker the service's end stops its systemd unit, and every process
    in it, a page half done included."""
    while service.runs:
        service.outgoing()   # drops the ended ones; nobody to tell any more
        time.sleep(0.5)


def _send(conn: Any, make: Callable[[], Any], call: Any = None) -> None:
    """Send one message. One that cannot be built or serialised (a value
    out of range) fails its call with an error, not the service; a bus that
    is gone (OSError) still ends the loop."""
    from jeepney import new_error

    try:
        conn.send(make())
    except OSError:
        raise
    except Exception as exc:
        traceback.print_exc()
        if call is not None:
            conn.send(new_error(call, "org.freedesktop.DBus.Error.Failed", "s",
                                (f"{type(exc).__name__}: {exc}",)))


def _answer(conn: Any, service: Service, idle_exit: float) -> int:
    """The loop: take the name, answer calls, pass on what the runs report.

    Idle, it releases the name first and ends only once the bus confirmed
    that and nothing came meanwhile: a call routed to this process just
    before would otherwise go unanswered, while after the release the bus
    starts a new keepbest for the next one."""
    from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_return, new_signal
    from jeepney.bus_messages import message_bus

    try:
        owner, inbox = _request_name(conn, message_bus.RequestName(BUS_NAME, 4))  # 4: don't queue
    except (OSError, EOFError, ValueError, TimeoutError) as exc:
        ui.error(f"{NAME}: could not take the name {BUS_NAME}: {exc}")
        return 1
    if owner not in (1, 4):   # 1: now the owner, 4: already was
        ui.error(f"{NAME}: {BUS_NAME} is already taken by another {NAME}.")
        return 0
    emitter = DBusAddress(OBJECT_PATH, interface=INTERFACE)
    last = time.monotonic()
    releasing: int | None = None   # the ReleaseName's serial, once sent
    released = False
    while True:
        for item in service.outgoing():
            if item[0] == "return":
                _send(conn, lambda i=item: new_method_return(i[1], i[2], i[3]), item[1])
            elif item[0] == "error":
                _send(conn, lambda i=item: new_error(i[1], i[2], "s", (i[3],)))
            else:
                _send(conn, lambda i=item: new_signal(emitter, i[1], i[2], i[3]))
        if service.busy():
            last = time.monotonic()
        elif time.monotonic() - last > idle_exit and releasing is None:
            releasing = next(conn.outgoing_serial)
            conn.send(message_bus.ReleaseName(BUS_NAME), serial=releasing)
        try:
            msg = inbox.pop(0) if inbox else conn.receive(timeout=0.25)
        except TimeoutError:
            if released and not service.busy():
                return 0
            continue
        except (OSError, EOFError, ValueError):   # the bus went away (logout)
            return 0
        if releasing is not None and msg.header.fields.get(HeaderFields.reply_serial) == releasing:
            released = True
            continue
        if msg.header.message_type != MessageType.method_call:
            continue   # signals such as NameAcquired
        last = time.monotonic()
        fields = msg.header.fields
        interface = fields.get(HeaderFields.interface)
        member = fields.get(HeaderFields.member)
        signature = fields.get(HeaderFields.signature, "")
        try:
            if interface == "org.freedesktop.DBus.Introspectable" and member == "Introspect":
                answer: tuple[str, tuple] | None = ("s", (INTROSPECTION,))
            elif interface == "org.freedesktop.DBus.Peer" and member == "Ping":
                answer = ("", ())
            elif fields.get(HeaderFields.path) != OBJECT_PATH or interface not in (INTERFACE, None):
                raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod",
                                   f"No {interface}.{member} on {fields.get(HeaderFields.path)}.")
            else:
                answer = service.call(member, signature, msg.body, msg)
        except ServiceError as exc:
            _send(conn, lambda m=msg, e=exc: new_error(m, e.name, "s", (str(e),)))
            continue
        except Exception as exc:   # a bug, or the file system: one failed call, not the service
            traceback.print_exc()
            _send(conn, lambda m=msg, e=exc: new_error(m, "org.freedesktop.DBus.Error.Failed", "s",
                                                       (f"{type(e).__name__}: {e}",)))
            continue
        if answer is not None:
            _send(conn, lambda m=msg, a=answer: new_method_return(m, a[0] or None, a[1]), msg)


def _request_name(conn: Any, request: Any, timeout: float = 10) -> tuple[int, list]:
    """RequestName's answer, and what came before it. Started by the bus,
    the call that started us arrives as soon as the name is ours, which can
    be before the reply: jeepney's send_and_get_reply would drop it."""
    from jeepney import HeaderFields

    serial = next(conn.outgoing_serial)
    conn.send(request, serial=serial)
    early, deadline = [], time.monotonic() + timeout
    while True:
        msg = conn.receive(timeout=max(0.0, deadline - time.monotonic()))
        if msg.header.fields.get(HeaderFields.reply_serial) == serial:
            if msg.header.message_type.name == "error":
                raise ValueError(f"the bus refused: {msg.body}")
            return msg.body[0], early
        early.append(msg)


# --- the service file ---------------------------------------------------------
def service_file_path() -> Path:
    from .desktop import _data_home

    return _data_home() / "dbus-1" / "services" / f"{BUS_NAME}.service"


def service_file_text(command: list[str]) -> str:
    # The bus splits Exec like a shell (quotes and backslashes).
    return "\n".join(["[D-BUS Service]", f"Name={BUS_NAME}",
                      "Exec=" + " ".join(shlex.quote(a) for a in [*command, "--dbus"]), ""])


def install_service_file() -> tuple[Path, list[str], str | None]:
    """Write the service file for the keepbest that runs this; returns its
    path, the command, and why the bus could not be told (None when it was)."""
    from .desktop import launch_command

    # In a toolbox or distrobox the bus on the host starts the service.
    command = container.on_host(launch_command())
    path = service_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(service_file_text(command), encoding="utf-8")
    return path, command, _reload_bus()


def remove_service_file() -> Path | None:
    path = service_file_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    _reload_bus()
    return path


def _reload_bus() -> str | None:
    """dbus-daemon notices a new service file by itself; dbus-broker reads
    them when told to reload."""
    try:
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return NEEDS_JEEPNEY
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            reply = conn.send_and_get_reply(message_bus.ReloadConfig(), timeout=5)
    except (OSError, KeyError, ValueError, TimeoutError) as exc:
        return str(exc)
    return None if reply.header.message_type.name == "method_return" else str(reply.body)
