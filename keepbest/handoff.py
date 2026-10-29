"""Hand a folder over to media-optimizer after a run, and come back afterwards.

media-optimizer (github.com/sobakah/media-optimizer) converts pictures and
videos to more efficient formats; running it after the duplicates are gone
saves converting copies. It runs as a child process, so keepbest's end menu
returns when it exits. A media-optimizer with a page of its own (0.8 and
newer) takes over keepbest's tab and leads back to it; an older one runs
without questions and its output is shown on keepbest's page. One that
writes its results (--results, 1.1.0 and newer) has its numbers shown next to
keepbest's own when it is done.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
from pathlib import Path

from .core_logic import format_size

COMMAND = "media-optimizer"
_child: subprocess.Popen | None = None   # the media-optimizer running now, for interrupt()
_run_help: dict[str, str] = {}


def available() -> str | None:
    """The path of media-optimizer if it is installed, else None."""
    return shutil.which(COMMAND)


def _help(executable: str) -> str:
    """What this media-optimizer's `run --help` says: which options it has."""
    if executable not in _run_help:
        try:
            result = subprocess.run([executable, "run", "--help"], capture_output=True, text=True, timeout=30,
                                    env={**os.environ, "MO_HOLD": "0"})
            _run_help[executable] = result.stdout
        except (OSError, subprocess.SubprocessError):
            _run_help[executable] = ""
    return _run_help[executable]


def has_page(executable: str) -> bool:
    """Whether this media-optimizer can show its page and lead back to keepbest's
    (--return-url, media-optimizer 0.8 and newer), as its help says."""
    return "--return-url" in _help(executable)


def has_results(executable: str) -> bool:
    """Whether this media-optimizer writes its results for keepbest (--results)."""
    return "--results" in _help(executable)


def folder_env(recursive: bool, excluded) -> dict[str, str]:
    """keepbest's folder choice as media-optimizer settings, so it covers the
    same folders and does not ask about subfolders again. Both use the same
    model: an excluded folder ('/' form, '.' = the main folder) loses only its
    own files. JSON, because a folder name may contain ':'."""
    if not recursive:
        return {"MO_SUBFOLDERS": "exclude", "MO_EXCLUDE_FOLDERS": "[]"}
    return {"MO_SUBFOLDERS": "include", "MO_EXCLUDE_FOLDERS": json.dumps(sorted(excluded))}


def run(target: Path, output, recursive: bool = True, excluded=(), return_url: str = "",
        results_file: Path | None = None) -> int:
    """Run media-optimizer on *target* and return its exit code; *output* gets
    each line it prints. With *results_file*, one that can writes its results
    there when its run ends (read_results).

    With *return_url* (keepbest's page) it shows its own page, whose address it
    prints, and its last button leads back there. Without, it asks nothing
    (-y: its config's settings) and its output is all there is to show.
    interrupt() stops it as Ctrl+C would.

    MO_CALLER tells media-optimizer who started it; MO_HOLD=0 stops it from
    keeping a window open at the end. The folder choice of this run is passed
    on (folder_env).
    """
    global _child
    executable = available()
    if executable is None:
        return 127
    env = {**os.environ, "MO_CALLER": "keepbest", "MO_HOLD": "0", **folder_env(recursive, excluded),
           "PYTHONUNBUFFERED": "1"}  # lines as they come, not when a pipe's buffer is full
    argv = [executable, "run", str(target)]
    if return_url:
        argv += ["--web", "--no-browser", "--return-url", return_url, "--return-name", "keepbest"]
    else:
        argv.append("-y")
    if results_file is not None and has_results(executable):
        argv += ["--results", str(results_file)]
    captured = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    # Ctrl+C and a closed terminal (where keepbest was started) reach
    # media-optimizer directly (same process group); it stops its encoders cleanly. keepbest must neither stop nor kill
    # it then. A SIGTERM sent only to keepbest is passed on. The handlers are in
    # place before the start, so no signal slips through in between; they are
    # Python handlers, not SIG_IGN, because an ignored signal would stay ignored
    # in media-optimizer, while a handler is reset to the default there.
    child = None
    pending: list[int] = []

    def ignore(signum, frame):
        pass

    def forward(signum, frame):
        if child is None:
            pending.append(signum)  # arrived while starting: sent right after
        else:
            child.send_signal(signum)

    previous = {name: signal.getsignal(getattr(signal, name))
                for name in ("SIGINT", "SIGHUP", "SIGTERM") if hasattr(signal, name)}
    for name in previous:
        signal.signal(getattr(signal, name), forward if name == "SIGTERM" else ignore)
    try:
        try:
            child = _child = subprocess.Popen(argv, env=env, **captured)
        except OSError:
            return 127
        for signum in pending:
            child.send_signal(signum)
        _pass_on(child, output)
        return child.wait()
    finally:
        _child = None
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)


def read_results(path: Path) -> dict | None:
    """media-optimizer's results file (format 1), or None: none written (no run
    started, an older media-optimizer) or not one keepbest can read."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("stages"), list):
        return None
    return data


def _count(stage: dict, *outcomes: str) -> int:
    return sum(int((stage.get("outcomes") or {}).get(name, 0)) for name in outcomes)


def summary(results: dict | None, freed: int = 0) -> dict | None:
    """media-optimizer's numbers as keepbest shows them: tiles as in keepbest's
    own summary, a line per stage, and with *freed* (what keepbest's run freed)
    the space both freed together."""
    if not results:
        return None
    stages = [s for s in results["stages"] if isinstance(s, dict)]
    dry = bool(results.get("dry_run"))
    before, after = int(results.get("before") or 0), int(results.get("after") or 0)
    saved = before - after
    tiles = [("Would be converted" if dry else "Converted",
              sum(_count(s, "dry") for s in stages) if dry else int(results.get("converted") or 0))]
    if before:
        tiles.append(("Space saved" if saved >= 0 else "Space added", format_size(abs(saved))))
    tiles += [("Skipped", sum(_count(s, "skipped", "cached") for s in stages)),
              ("Failed", int(results.get("failed") or 0))]
    lines = []
    for s in stages:
        title = s.get("title") or s.get("stage")
        if s.get("state") == "earlier":
            lines.append(f"{title}: done in the run it resumed")
        elif s.get("state") == "waiting":
            lines.append(f"{title}: not reached")
        else:
            parts = [f"{n} {label}" for n, label in ((_count(s, "converted"), "converted"),
                                                       (_count(s, "dry"), "would be converted"),
                                                       (_count(s, "skipped", "cached"), "skipped"),
                                                       (_count(s, "discarded"), "kept (result larger)"),
                                                       (_count(s, "failed"), "failed")) if n]
            if s.get("before"):
                parts.append(f"{format_size(s['before'])} → {format_size(s.get('after') or 0)}")
            lines.append(f"{title}: {', '.join(parts) or 'nothing to do'}"
                         + (" (stopped here)" if s.get("state") == "running" else ""))
    together = None
    if freed > 0 and saved > 0 and not dry:
        together = (f"Together with the {format_size(freed)} keepbest freed: "
                    f"{format_size(freed + saved)} less in this folder.")
    return {"tiles": tiles, "stages": lines, "together": together, "dry": dry, "code": results.get("code")}


def _pass_on(child: subprocess.Popen, output) -> None:
    """Hand the child's output to *output* line by line until the child has
    ended. A program it started that still holds the pipe (a stray encoder)
    must not keep keepbest waiting, so the pipe is not read to its end."""
    if os.name == "nt":  # select() takes no pipes there: read to the end
        for raw in child.stdout:
            output(raw.decode("utf-8", "replace").rstrip("\r\n"))
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    fd = child.stdout.fileno()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            # Checked before looking: what it wrote before it ended is in the pipe by then.
            ended = child.poll() is not None
            if selector.select(timeout=0 if ended else 0.2):
                data = os.read(fd, 65536)
                if not data:
                    break
                *lines, pending = re.split(r"\r?\n", pending + decoder.decode(data))
                for line in lines:
                    output(line)
            elif ended:
                break
    pending += decoder.decode(b"", final=True)
    if pending:
        output(pending)
    child.stdout.close()


def interrupt() -> bool:
    """Stop the running media-optimizer as Ctrl+C would; False when none runs."""
    child = _child
    if child is None or child.poll() is not None:
        return False
    child.send_signal(signal.SIGINT)
    return True
