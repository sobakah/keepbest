"""Persistent state: the action log and resumable review sessions."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import NAME, OLD_NAME, project_dir
from .ui import warn

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

SESSION_VERSION = 2          # 2: dry-run sessions and exact-copy groups
_READABLE_VERSIONS = (1, 2)


LOG_NAME = f"{NAME}.log"
OLD_LOG_NAME = f"{OLD_NAME}.log"


def state_override() -> Path | None:
    """$KEEPBEST_STATE_DIR (or the old $IMG_DEDUPE_STATE_DIR) replaces the state
    directory and the project folder for the log and sessions (used by the tests)."""
    value = os.environ.get("KEEPBEST_STATE_DIR") or os.environ.get("IMG_DEDUPE_STATE_DIR")
    return Path(value) if value else None


def state_base() -> Path:
    """$XDG_STATE_HOME or ~/.local/state, %LOCALAPPDATA% on Windows."""
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def state_dir() -> Path:
    """~/.local/state/keepbest, unless overridden (state_override)."""
    return state_override() or state_base() / NAME


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write_atomically(fd: int, tmp: str, target: Path, write) -> None:
    """Write to the temporary file, flush it to disk, then replace *target* in one
    step: a crash never leaves half a file. On failure the temporary file is removed."""
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve_log_path(setting) -> Path | None:
    """config 'log_file': null = default location, false = off, string = that file.

    Default: ``keepbest.log`` in the project folder when running from a
    source checkout, otherwise in the state directory
    (~/.local/state/keepbest). Relative paths count from that same folder.
    """
    if setting is False:
        return None
    base = None if state_override() else project_dir()
    if base is not None and not os.access(base, os.W_OK):
        warn(f"The project folder {base} is not writable - logging to {state_dir()} instead.")
        base = None
    if base is None:
        base = state_dir()
    if setting in (None, "", True):
        return base / LOG_NAME
    path = Path(str(setting)).expanduser()
    return path if path.is_absolute() else base / path


class ActionLog:
    """Append-only, human-readable record of every change made to files.

    One line per action, fields separated by ' | ' so it is easy to grep:
        2026-09-23 20:15:04 | TRASHED  | /pics/a (1).jpg | kept /pics/a.jpg | exact copy, automatic
    The session header is written lazily, so runs that change nothing leave no trace.
    """

    def __init__(self, path: Path | None, header: str):
        self.path, self.header = path, header
        self.entries = 0
        self._started = False
        self._failed = False

    def _write(self, line: str) -> None:
        if self.path is None or self._failed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Reopened per line: survives crashes. A file name that isn't valid UTF-8
            # is written with its original bytes.
            with open(self.path, "a", encoding="utf-8", errors="surrogateescape") as handle:
                if not self._started:
                    handle.write(f"\n{_stamp()} | START    | {self.header}\n")
                    self._started = True
                handle.write(line + "\n")
        except OSError as exc:
            self._failed = True
            warn(f"Could not write the log file {self.path}: {exc}")

    def action(self, kind: str, path: Path, kept: Path | None = None, detail: str = "") -> None:
        parts = [_stamp(), f"{kind:<8}", str(path)]
        if kept is not None:
            parts.append(f"kept {kept}")
        if detail:
            parts.append(detail)
        self._write(" | ".join(parts))
        self.entries += 1

    def end(self, text: str) -> None:
        if self._started:
            self._write(f"{_stamp()} | END      | {text}")


def sessions_dir() -> Path:
    """Where saved sessions live: ``sessions/`` in the project folder when running
    from a source checkout, otherwise the state directory."""
    project = None if state_override() else project_dir()
    if project is not None and os.access(project, os.W_OK):
        return project / "sessions"
    return state_dir() / "sessions"


class SessionStore:
    """One saved review session per folder, written atomically after every change.

    A session is either a real run (its deletions already happened) or a dry
    run (``dry_run``); a finished dry run (``finished``) waits to be carried out.
    """

    def __init__(self, target: Path):
        key = hashlib.sha1(os.fsencode(target)).hexdigest()[:16]  # any bytes the name holds
        self.path = sessions_dir() / f"{key}.json"
        # Older checkouts kept their sessions in the state directory.
        self._legacy = state_dir() / "sessions" / f"{key}.json"
        self.target = target
        self.saved = False
        self._warned = False

    def _paths(self) -> list[Path]:
        return [self.path] if self._legacy == self.path else [self.path, self._legacy]

    def _read(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") not in _READABLE_VERSIONS \
                or data.get("target") != str(self.target):
            return None
        data.setdefault("dry_run", False)
        data.setdefault("finished", False)
        data.setdefault("exact_groups", [])
        data.setdefault("groups", [])
        if not data["groups"] and not data["exact_groups"]:
            return None
        return data

    def load(self) -> dict | None:
        for path in self._paths():
            data = self._read(path)
            if data is not None:
                return data
        return None

    def save(self, data: dict) -> None:
        payload = {"version": SESSION_VERSION, "target": str(self.target), **data,
                   "updated": datetime.now().isoformat(timespec="seconds")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".session-", suffix=".tmp")
            _write_atomically(fd, tmp, self.path, lambda handle: json.dump(payload, handle))
            self.saved = True
        except OSError as exc:
            if not self._warned:
                self._warned = True
                warn(f"Could not save progress to {self.path}: {exc}")
            return
        if self._legacy != self.path:  # moved to the new location: drop the old copy
            try:
                self._legacy.unlink()
            except OSError:
                pass

    def delete(self) -> None:
        for path in self._paths():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                warn(f"Could not delete saved progress {path}: {exc}")
        self.saved = False


IGNORE_FILE = f".{NAME}_ignore.json"
OLD_IGNORE_FILE = f".{OLD_NAME}_ignore.json"   # read until the next mark replaces it


class IgnoreList:
    """Image pairs marked "not duplicates", kept in a hidden file in the scanned folder.

    A file is identified by its path relative to that folder, its size and its
    modification time; if either image changes, the mark no longer applies and
    the pair is shown again. With *enabled* False (``--no-ignore``) nothing is
    left out, but new marks are still saved.
    """

    def __init__(self, base: Path, enabled: bool = True, file: Path | None = None):
        self.base = base
        self.path = file or base / IGNORE_FILE   # *file*: ``--groups`` runs keep theirs in the state folder
        self._old_path = base / OLD_IGNORE_FILE if file is None else self.path
        self.enabled = enabled
        self._pairs: dict[tuple, dict] = {}
        self._warned = False
        self._load()

    def _identity(self, meta: dict) -> tuple | None:
        try:
            rel = Path(meta["path"]).relative_to(self.base).as_posix()
        except ValueError:
            return None
        return (rel, int(meta["size"]), int(meta["mtime_ns"]))

    @staticmethod
    def _key(a: tuple, b: tuple) -> tuple:
        return tuple(sorted((a, b)))

    def _read(self, source: Path) -> dict[tuple, dict]:
        """The marks in *source*; raises OSError/ValueError (not FileNotFoundError: none then)."""
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        pairs = {}
        for entry in data.get("pairs", []) if isinstance(data, dict) else []:
            try:
                a, b = (tuple((f["path"], int(f["size"]), int(f["mtime_ns"]))) for f in entry["files"])
            except (KeyError, TypeError, ValueError):
                continue
            pairs[self._key(a, b)] = entry
        return pairs

    def _load(self) -> None:
        source = self.path if self.path.exists() or not self._old_path.exists() else self._old_path
        try:
            self._pairs.update(self._read(source))
        except (OSError, ValueError) as exc:
            warn(f"Could not read {source} ({exc}); marked pairs are ignored this time.")

    @contextlib.contextmanager
    def _locked(self):
        """The state folder's file is shared by every --groups run (they may run at once on
        different folders): its saves take turns. A folder's own file has one keepbest at a time."""
        if fcntl is None or self._old_path != self.path:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def __len__(self) -> int:
        return len(self._pairs)

    def contains(self, a: dict, b: dict) -> bool:
        if not self.enabled:
            return False
        ia, ib = self._identity(a), self._identity(b)
        return ia is not None and ib is not None and self._key(ia, ib) in self._pairs

    def add(self, a: dict, b: dict) -> None:
        ia, ib = self._identity(a), self._identity(b)
        if ia is None or ib is None:
            return
        self._pairs[self._key(ia, ib)] = {
            "files": [{"path": p, "size": s, "mtime_ns": m} for p, s, m in self._key(ia, ib)],
            "marked": _stamp(),
        }
        self._save()

    def _save(self) -> None:
        try:
            with self._locked():
                # Marks are only ever added: those another keepbest saved meanwhile stay.
                try:
                    for key, entry in self._read(self.path).items():
                        self._pairs.setdefault(key, entry)
                except ValueError:
                    pass   # a broken file is replaced, as before
                payload = {
                    "about": f"Image pairs marked 'not duplicates' in {NAME}. Delete this file to see them again.",
                    "version": 1,
                    "pairs": list(self._pairs.values()),
                }
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{NAME}_ignore-", suffix=".tmp")
                _write_atomically(fd, tmp, self.path, lambda handle: json.dump(payload, handle, indent=1))
        except OSError as exc:
            if not self._warned:
                self._warned = True
                warn(f"Could not save the 'not duplicates' marks to {self.path}: {exc}")
            return
        if self._old_path == self.path:
            return
        try:  # the new file has every mark of the old one: it replaces it
            self._old_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            warn(f"Could not remove the old marks file {self._old_path}: {exc}")
