"""Hand a folder over to media-optimizer after a run, and come back afterwards.

media-optimizer (github.com/sobakah/media-optimizer) converts pictures and
videos to more efficient formats; running it after the duplicates are gone
saves converting copies. It runs as a child process, so keepbest's end menu
returns when it exits.
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

COMMAND = "media-optimizer"
_child: subprocess.Popen | None = None   # the media-optimizer running now, for interrupt()


def available() -> str | None:
    """The path of media-optimizer if it is installed, else None."""
    return shutil.which(COMMAND)


def folder_env(recursive: bool, excluded) -> dict[str, str]:
    """keepbest's folder choice as media-optimizer settings, so it covers the
    same folders and does not ask about subfolders again. Both use the same
    model: an excluded folder ('/' form, '.' = the main folder) loses only its
    own files. JSON, because a folder name may contain ':'."""
    if not recursive:
        return {"MO_SUBFOLDERS": "exclude", "MO_EXCLUDE_FOLDERS": "[]"}
    return {"MO_SUBFOLDERS": "include", "MO_EXCLUDE_FOLDERS": json.dumps(sorted(excluded))}


def run(target: Path, recursive: bool = True, excluded=(), output=None) -> int:
    """Run media-optimizer on *target* in this terminal and return its exit code.

    MO_CALLER tells media-optimizer who started it; MO_HOLD=0 stops it from
    keeping the window open at the end, since keepbest continues in it. The
    folder choice of this run is passed on (folder_env).

    With *output* (called with each line), for keepbest's page: media-optimizer
    asks nothing (-y: its config's settings) and its output goes there instead
    of to the terminal; interrupt() stops it as Ctrl+C would.
    """
    global _child
    executable = available()
    if executable is None:
        return 127
    env = {**os.environ, "MO_CALLER": "keepbest", "MO_HOLD": "0", **folder_env(recursive, excluded)}
    argv = [executable, "run", str(target)]
    captured = {}
    if output is not None:
        argv.append("-y")
        env["PYTHONUNBUFFERED"] = "1"  # lines as they come, not when a pipe's buffer is full
        captured = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    # Ctrl+C and a closed terminal reach media-optimizer directly (same process
    # group); it stops its encoders cleanly. keepbest must neither stop nor kill
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
        if output is not None:
            _pass_on(child, output)
        return child.wait()
    finally:
        _child = None
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)


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
            if selector.select(timeout=0.2):
                data = os.read(fd, 65536)
                if not data:
                    break
                *lines, pending = re.split(r"\r?\n", pending + decoder.decode(data))
                for line in lines:
                    output(line)
            elif child.poll() is not None:
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
