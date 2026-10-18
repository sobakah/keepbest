"""The file journal: a machine-readable record of the files a run removed or renamed.

JSON Lines, one event per line, for other programs that keep data about pictures
by path, such as the tagger picktag: when a copy is removed, its data can move to
the kept file, and a renamed file keeps its data. Format version 1 is shared with
media-optimizer and picktag; a field or event that changes meaning needs "v": 2.
Nothing is written in dry runs, and a journal problem never changes a run.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from .config import NAME, __version__
from .session import state_dir
from .ui import StyleUI

try:
    import fcntl
except ImportError:  # Windows: no flock; each event is still a single append
    fcntl = None

JOURNAL_NAME = "journal.jsonl"
FORMAT_VERSION = 1


def resolve_journal_path(setting) -> Path | None:
    """config 'journal_file': null = journal.jsonl in the state directory (also when
    running from a checkout, so other programs find it), false = off, string = that
    file. Relative paths count from the state directory."""
    if setting is False:
        return None
    if setting in (None, "", True):
        return state_dir() / JOURNAL_NAME
    path = Path(str(setting)).expanduser()
    return path if path.is_absolute() else state_dir() / path


def canonical(path: Path) -> str:
    """Absolute, with symlinks in the folder part resolved and the name left as is."""
    path = Path(os.path.abspath(path))
    try:
        return str(path.parent.resolve() / path.name)
    except (OSError, RuntimeError):
        return str(path)


def _run_id() -> str:
    return f"{datetime.now():%y%m%d-%H%M%S}-{os.getpid()}-{secrets.token_hex(2)}"


class Journal:
    """Appends events to the journal file; does nothing when *path* is None."""

    def __init__(self, path: Path | None):
        self.path = path
        self.run = _run_id()
        self._started = False
        self._warned = False

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def file_state(self, path: Path) -> tuple[int, int] | None:
        """(size, mtime_ns) of *path*, taken before it is removed; None when off."""
        if self.path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def start(self, source: Path) -> None:
        self._write("run_start", source=canonical(source), target=None, pid=os.getpid())
        self._started = True

    def end(self, code: int, interrupted: bool) -> None:
        if self._started:
            self._write("run_end", code=code, interrupted=interrupted)
            self._started = False

    def removed(self, path: Path, before: tuple[int, int] | None, how: str, kept: Path,
                kind: str, hard_link: bool) -> None:
        if before is None:  # couldn't be read before the removal: nothing to match it by
            return
        fields = {"path": canonical(path), "size": before[0], "mtime_ns": before[1], "how": how,
                  "reason": "duplicate", "kept": canonical(kept),
                  "match": {"exact": "exact", "remux": "remux"}.get(kind, "visual")}
        if hard_link:
            fields["hard_link"] = True
        self._write("removed", **fields)

    def renamed(self, src: Path, dest: Path, reason: str) -> None:
        if self.path is None:
            return
        try:
            stat = dest.stat()
        except OSError:
            return
        self._write("renamed", src=canonical(src), dest=canonical(dest), size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns, reason=reason)

    def _write(self, event: str, **fields) -> None:
        if self.path is None:
            return
        try:
            record = {"v": FORMAT_VERSION, "tool": NAME, "tool_version": __version__, "run": self.run,
                      "ts": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event,
                      **fields}
            # ensure_ascii: names that aren't valid UTF-8 become \udcXX escapes,
            # which os.fsencode turns back into the exact bytes.
            line = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
            try:
                if fcntl is not None:  # another keepbest or media-optimizer may append at the same time
                    fcntl.flock(fd, fcntl.LOCK_EX)
                view = memoryview(line)
                while view:  # one write; a short write only on a nearly full disk
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)  # also releases the lock
        except Exception as exc:  # the journal must never stop a run
            if not self._warned:
                self._warned = True
                print(f"{StyleUI.YELLOW}Could not write the journal file {self.path}: {exc}{StyleUI.RESET}",
                      file=sys.stderr)
