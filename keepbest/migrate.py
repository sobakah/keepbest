"""One-time move of the files kept under the old name (img_dedupe, up to 0.5.1).

The config folder and the state folder (log and saved sessions) are renamed to
the new name, and the log file inside them too, the first time keepbest starts.
"Not duplicates" marks in picture folders are taken over by IgnoreList when
that folder is used again (session.py).
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import NAME, OLD_NAME, config_home, project_dir
from .session import LOG_NAME, OLD_LOG_NAME, state_base, state_override


def _move(old: Path, new: Path, what: str, moved: list[str], warnings: list[str], hint: str,
          quiet: bool = False) -> bool:
    """Rename *old* to *new* unless *new* already exists. True if *new* is in place.
    *quiet*: don't announce it (a failure is still reported)."""
    if new.exists() or new.is_symlink():
        return True
    if not old.exists():
        return False
    try:
        os.rename(old, new)
    except OSError as exc:
        warnings.append(f"Could not move your {what} from {old} to {new} ({exc}). {hint}")
        return False
    if not quiet:
        moved.append(f"{OLD_NAME} is now called {NAME}: moved your {what} from {old} to {new}.")
    return True


def migrate_old_locations() -> tuple[list[str], list[str]]:
    """Move the old folders and log files to the new name. Returns (what was
    moved, warnings). Nothing happens when the new ones exist already."""
    moved: list[str] = []
    warnings: list[str] = []
    _move(config_home() / OLD_NAME, config_home() / NAME, "config", moved, warnings,
          "It is still read from there; move the folder by hand to finish.")
    if state_override() is None:
        new_state = state_base() / NAME
        if _move(state_base() / OLD_NAME, new_state, "log and saved sessions", moved, warnings,
                 "Move the folder by hand, otherwise they are not found."):
            _move(new_state / OLD_LOG_NAME, new_state / LOG_NAME, "log", moved, warnings,
                  "New entries go to a new log file.", quiet=True)  # part of the folder's move
        project = project_dir()
        if project is not None:  # running from a checkout keeps its log in the project folder
            _move(project / OLD_LOG_NAME, project / LOG_NAME, "log", moved, warnings,
                  "New entries go to a new log file.")
    return moved, warnings
