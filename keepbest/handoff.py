"""Hand a folder over to media-optimizer after a run, and come back afterwards.

media-optimizer (github.com/sobakah/media-optimizer) converts pictures and
videos to more efficient formats; running it after the duplicates are gone
saves converting copies. It runs as a child process, so keepbest's end menu
returns when it exits.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

COMMAND = "media-optimizer"


def available() -> str | None:
    """The path of media-optimizer if it is installed, else None."""
    return shutil.which(COMMAND)


def run(target: Path) -> int:
    """Run media-optimizer on *target* in this terminal and return its exit code.

    MO_CALLER tells media-optimizer who started it; MO_HOLD=0 stops it from
    keeping the window open at the end, since keepbest continues in it.
    """
    executable = available()
    if executable is None:
        return 127
    env = {**os.environ, "MO_CALLER": "keepbest", "MO_HOLD": "0"}
    try:
        child = subprocess.Popen([executable, "run", str(target)], env=env)
    except OSError:
        return 127
    # Ctrl+C and a closed terminal reach media-optimizer directly (same process
    # group); it stops its encoders cleanly. keepbest must neither stop nor kill
    # it then. A SIGTERM sent only to keepbest is passed on.
    previous = {name: signal.getsignal(getattr(signal, name))
                for name in ("SIGINT", "SIGHUP", "SIGTERM") if hasattr(signal, name)}
    for name in ("SIGINT", "SIGHUP"):
        if name in previous:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    if "SIGTERM" in previous:
        signal.signal(signal.SIGTERM, lambda signum, frame: child.send_signal(signum))
    try:
        return child.wait()
    finally:
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)
