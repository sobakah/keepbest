"""Hand a folder over to media-optimizer after a run, and come back afterwards.

media-optimizer (github.com/sobakah/media-optimizer) converts pictures and
videos to more efficient formats; running it after the duplicates are gone
saves converting copies. It runs as a child process, so keepbest's end menu
returns when it exits.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

COMMAND = "media-optimizer"


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


def run(target: Path, recursive: bool = True, excluded=()) -> int:
    """Run media-optimizer on *target* in this terminal and return its exit code.

    MO_CALLER tells media-optimizer who started it; MO_HOLD=0 stops it from
    keeping the window open at the end, since keepbest continues in it. The
    folder choice of this run is passed on (folder_env).
    """
    executable = available()
    if executable is None:
        return 127
    env = {**os.environ, "MO_CALLER": "keepbest", "MO_HOLD": "0", **folder_env(recursive, excluded)}
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
            child = subprocess.Popen([executable, "run", str(target)], env=env)
        except OSError:
            return 127
        for signum in pending:
            child.send_signal(signum)
        return child.wait()
    finally:
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)
