"""Opening the images of a duplicate group in a viewer, and a file in its folder."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .front import warn  # on the page when it runs there

# Comparison apps: viewer name -> (display name, Flatpak app id, native command or None).
COMPARE_APPS = {
    "identity": ("Identity", "org.gnome.gitlab.YaLTeR.Identity", None),
    "imagecompare": ("Image Compare", "io.github.gimletlove.imagecompare", "imagecompare"),
}

_flatpak_state: dict[str, bool | None] = {}


def _flatpak_installed(app_id: str) -> bool | None:
    """True/False whether the Flatpak app is installed; None if Flatpak itself is missing."""
    if app_id not in _flatpak_state:
        if shutil.which("flatpak") is None:
            _flatpak_state[app_id] = None
        else:
            result = subprocess.run(["flatpak", "info", app_id],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _flatpak_state[app_id] = result.returncode == 0
    return _flatpak_state[app_id]


def _start(argv: list[str]) -> bool:
    """Start a viewer in the background; False if it can't be started (not installed)."""
    try:
        # Own session: Ctrl+C in keepbest must not close a comparison window you still look at.
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True


def _open_compare_app(viewer: str, paths: list[str]) -> str | None:
    """Open *paths* in a comparison app. Returns its name, or None if it is unavailable."""
    name, app_id, native = COMPARE_APPS[viewer]
    if native and shutil.which(native) and _start([native, *paths]):  # e.g. Image Compare from its RPM/DEB
        return name
    installed = _flatpak_installed(app_id)
    if installed is None:
        warn(f"{name} needs Flatpak, which is not installed. Falling back to the default viewer.")
        return None
    if not installed:
        warn(f"{name} is not installed. Install it with:  flatpak install flathub {app_id}")
        warn("Falling back to the default viewer.")
        return None
    # The sandbox cannot see your folders; --file-forwarding with @@ ... @@ hands
    # exactly these files to the app through the document portal.
    if not _start(["flatpak", "run", "--file-forwarding", app_id, "@@", *paths, "@@"]):
        warn(f"{name} could not be started. Falling back to the default viewer.")
        return None
    return name


def open_image_viewer(filepaths, viewer_mode: str = "auto") -> str | None:
    """Open the images with the configured viewer. Returns the name of the viewer
    used, or None (after a warning) when none could be started."""
    # Absolute paths, so Flatpaks and external tools find the files.
    paths = [str(p.resolve()) for p in filepaths]

    if viewer_mode in COMPARE_APPS:
        used = _open_compare_app(viewer_mode, paths)
        if used:
            return used
    # 'auto', and the fallback: the system's default viewer, one window per image.
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    for path in paths:
        if sys.platform == "win32":
            try:
                os.startfile(path)
                continue
            except OSError:
                pass
        elif _start([opener, path]):
            continue
        warn(f"No viewer could be started ({'no default app' if sys.platform == 'win32' else opener + ' not found'}). "
             f"Set \"viewer\" in the config to one that is installed.")
        return None
    return "the default viewer"


def show_in_folder(path) -> str | None:
    """Open the file manager at *path*'s folder, with the file selected where
    the file manager can do that. Returns how, or None when nothing started."""
    path = Path(path).resolve()
    if sys.platform == "darwin":
        return "Finder" if _start(["open", "-R", str(path)]) else None
    if sys.platform == "win32":
        return "Explorer" if _start(["explorer", f"/select,{path}"]) else None
    # The desktop's file manager selects the file (Nautilus, Dolphin, Nemo, ...).
    if shutil.which("gdbus") and subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems", f"['{path.as_uri()}']", ""],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0:
        return "the file manager"
    return "the file manager" if _start(["xdg-open", str(path.parent)]) else None
