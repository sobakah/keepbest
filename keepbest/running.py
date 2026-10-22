"""Which folders running keepbest processes work on, so two never scan the same
files: each process has a small file in the state folder naming its folder (and
its page's address), and a folder that is the same as, inside or above another
process's folder is refused. Files of processes that no longer run are ignored.
"""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path

from .session import state_dir

try:
    import fcntl
except ImportError:  # Windows: no flock, and os.kill(pid, 0) would end the process there
    fcntl = None

_address: str | None = None
_registered = False


def _folder() -> Path:
    return state_dir() / "running"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process with that number
        return True
    except OSError:
        return False
    return True


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def set_address(url: str | None) -> None:
    """The page's address, shown to a second keepbest that wants the same folder."""
    global _address
    _address = url


def claim(folder: Path) -> dict | None:
    """Register *folder* for this process (replacing its earlier folder).
    Returns the other process's entry ({pid, folder, address}) when one works on
    an overlapping folder; nothing is registered then."""
    global _registered
    if fcntl is None:
        return None
    folder = folder.resolve()
    registry = _folder()
    try:
        registry.mkdir(parents=True, exist_ok=True)
        with open(registry / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for path in registry.glob("*.json"):
                try:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                    pid = int(entry["pid"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if pid == os.getpid():
                    continue
                if not _alive(pid):
                    path.unlink(missing_ok=True)
                    continue
                if _overlaps(folder, Path(entry.get("folder", ""))):
                    return entry
            own = registry / f"{os.getpid()}.json"
            own.write_text(json.dumps({"pid": os.getpid(), "folder": str(folder), "address": _address}),
                           encoding="utf-8")
    except OSError:
        return None  # a broken state folder must not stop keepbest
    if not _registered:
        atexit.register(release)
        _registered = True
    return None


def release() -> None:
    try:
        (_folder() / f"{os.getpid()}.json").unlink(missing_ok=True)
    except OSError:
        pass


def busy_message(entry: dict) -> str:
    text = (f"Another keepbest (process {entry.get('pid')}) is working on {entry.get('folder')}. "
            f"Let it finish first, or choose a folder that doesn't overlap with it.")
    if entry.get("address"):
        text += f" Its page: {entry['address']}"
    return text
