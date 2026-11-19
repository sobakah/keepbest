"""Command-line entry point: find and remove duplicate images."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from . import selfupdate, ui
from .config import NAME, __version__, load_config
from .migrate import migrate_old_locations

REQUIRED_PACKAGES = {"PIL": "Pillow", "send2trash": "send2trash"}


def parse_stages(text: str) -> str:
    """'all' (or the old 'both'), or stage numbers like '1,2' or '3'; returns e.g. '1,2'."""
    text = text.strip().lower()
    if text in ("all", "both"):
        return "1,2,3"
    numbers = {part.strip() for part in text.replace(" ", ",").split(",") if part.strip()}
    if not numbers or not numbers <= {"1", "2", "3"}:
        raise argparse.ArgumentTypeError("use 'all' or stage numbers 1-3, e.g. 1,2 or 3")
    return ",".join(sorted(numbers))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=NAME,
        description="Find exact and visually identical duplicate images and remove the "
                    "redundant copies, keeping the best one.",
    )
    parser.add_argument("path", nargs="?", help="picture directory (default: the page asks; with --auto, "
                                                "the current directory)")
    parser.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")

    scan = parser.add_argument_group("scanning")
    scan.add_argument("-r", "--recursive", action="store_true", help="include subdirectories")
    scan.add_argument("--exclude", action="append", metavar="DIR", default=[],
                      help="with -r, skip this subfolder (relative to the scanned folder; repeatable); "
                           "with --groups, leave out the groups' files in it (absolute or relative to their "
                           "common folder)")
    scan.add_argument("--groups", type=Path, metavar="FILE",
                      help="compare only files in the same group of this JSON file (from picktag), "
                           "every pair by its pixels; instead of a folder")
    scan.add_argument("--stages", type=parse_stages, default="1,2,3", metavar="STAGES",
                      help="stages to run: 1 identical files, 2 remuxed videos, 3 visually identical "
                           "images; e.g. 1,2 or 3 (default: all)")
    scan.add_argument("--no-videos", action="store_true", help="leave video files out of the scan")
    scan.add_argument("--strictness", choices=("strict", "normal", "loose"), help="override the configured strictness")

    review = parser.add_argument_group("review")
    mode = review.add_mutually_exclusive_group()
    mode.add_argument("-i", "--interactive", action="store_true", help="ask before resolving every group")
    mode.add_argument("-y", "--auto", action="store_true",
                      help="no start screen and no questions; resolve every group automatically")
    review.add_argument("--dry-run", action="store_true", help="show what would be deleted, delete nothing")
    review.add_argument("--permanent", action="store_true",
                        help="delete permanently instead of moving to the trash; --auto needs it for "
                             "permanent deletion, a delete_mode in the config file is not enough")
    review.add_argument("--no-rename", action="store_true",
                        help='keep "(1)" in the names of kept copies')
    review.add_argument("--no-ignore", action="store_true",
                        help='show pairs marked "not duplicates" again, for this run')

    page = parser.add_argument_group("browser page")
    # The page is the only way keepbest asks: --web stays for app-menu
    # entries and scripts that pass it, --terminal explains where the questions are.
    page.add_argument("--web", action="store_true", help=argparse.SUPPRESS)
    page.add_argument("--terminal", action="store_true", help=argparse.SUPPRESS)
    page.add_argument("--no-browser", action="store_true",
                      help="only print the page's address, don't open it (without a display: always)")
    # For dbus_service's pages: end when nobody opens the page.
    page.add_argument("--unvisited-timeout", type=float, default=0, metavar="SECONDS", help=argparse.SUPPRESS)

    setup = parser.add_argument_group("app menu and D-Bus (Linux)")
    install = setup.add_mutually_exclusive_group()
    install.add_argument("--install", action="store_true",
                         help="both of the following: --install-desktop-entry and --install-dbus-service")
    install.add_argument("--install-desktop-entry", action="store_true",
                         help="add keepbest to the app menu (it opens the page) and exit")
    install.add_argument("--remove-desktop-entry", action="store_true", help="remove that entry and exit")
    install.add_argument("--install-dbus-service", action="store_true",
                         help="let the session bus start this keepbest when a program calls it (picktag, "
                              "also from a Flatpak), then exit")
    install.add_argument("--remove-dbus-service", action="store_true", help="undo --install-dbus-service, then exit")
    install.add_argument("--dbus", action="store_true",
                         help="serve the D-Bus interface until idle (what the bus starts)")

    output = parser.add_argument_group("output")
    output.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    output.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity (repeatable)")
    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def stop_on_signals() -> None:
    """Closing the terminal (SIGHUP) or `kill` / a shutdown (SIGTERM) stop the
    program like Ctrl+C: progress is saved once more and the log is closed."""
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupt)


def missing_packages() -> list[str]:
    return [pip for module, pip in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]


def graphical_session() -> bool:
    """Whether a browser can be shown here: on Linux and BSD only with a display
    (not over SSH or on a console)."""
    if sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def _desktop_entry(install: bool) -> int:
    from . import desktop

    try:
        if install:
            entry, icon, command = desktop.install()
            ui.success(f"Added {NAME} to the app menu: {entry}")
            ui.info(f"It starts: {' '.join(command + desktop.PAGE_ARGUMENTS)}")
            ui.info(f"Run this again after installing {NAME} elsewhere (pipx, another checkout).")
        else:
            removed = desktop.remove()
            ui.success("Removed: " + ", ".join(str(p) for p in removed) if removed else "There was no app-menu entry.")
    except desktop.DesktopError as exc:
        ui.error(str(exc))
        return 2
    return 0


def _dbus_service(install: bool) -> int:
    from . import dbus_service

    try:
        if install:
            path, command, unreloaded = dbus_service.install_service_file()
            ui.success(f"The session bus starts {NAME} for {dbus_service.BUS_NAME}: {path}")
            ui.info(f"It starts: {' '.join(command)} --dbus")
            if unreloaded:
                ui.warn(f"Could not tell the bus to read it ({unreloaded}); it does at the next login.")
            ui.info(f"Run this again after installing {NAME} elsewhere (pipx, another checkout).")
        else:
            removed = dbus_service.remove_service_file()
            ui.success(f"Removed: {removed}" if removed else "There was no D-Bus service file.")
    except OSError as exc:
        ui.error(f"Could not {'write' if install else 'remove'} the D-Bus service file: {exc}")
        return 2
    return 0


def _install() -> int:
    """--install: the app-menu entry and the D-Bus service; both are tried."""
    codes = [_desktop_entry(install=True), _dbus_service(install=True)]
    return max(codes)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dbus:
        # The service starts each run with keepbest's own config; a path or
        # a mode given with it would be silently ignored.
        defaults = vars(parser.parse_args([]))
        given = [key for key, value in vars(args).items()
                 if key not in ("dbus", "verbose") and value != defaults[key]]
        if given:
            parser.error("--dbus takes no other options: it serves the D-Bus interface until idle")
    if args.unvisited_timeout < 0:
        parser.error("--unvisited-timeout must be 0 (wait for ever) or a positive number of seconds")
    if args.unvisited_timeout and args.auto:
        parser.error("--unvisited-timeout and --auto don't go together: --auto shows no page")
    if args.web and args.auto:
        parser.error("--web and --auto don't go together: --auto asks nothing, so there is no page to show")
    if args.terminal:
        parser.error(f"there are no terminal screens: {NAME} asks on its page in the "
                     "browser (--no-browser only prints its address), or asks nothing with --auto")
    if args.permanent and args.dry_run:
        parser.error("--permanent and --dry-run don't go together")
    if args.groups and (args.path or args.recursive):
        parser.error("--groups names the files itself: no folder or -r with it")
    configure_logging(args.verbose)

    moved, migration_warnings = migrate_old_locations()  # before the config is looked for
    config, warnings = load_config(args.config)
    warnings = migration_warnings + warnings
    if args.color:
        config["color"] = args.color
    if args.permanent:
        config["delete_mode"] = "permanent"
    elif args.auto and config["delete_mode"] == "permanent":
        # --auto asks nothing, borderline groups included: a config set up for the
        # page must not delete for good in a script.
        config["delete_mode"] = "trash"
        warnings.append("delete_mode is \"permanent\" in the config file, but --auto deletes permanently "
                        "only with --permanent: files go to the trash.")
    config["_configured_delete_mode"] = config["delete_mode"]  # what "for real" means after a dry run
    if args.dry_run:
        config["delete_mode"] = "dry_run"
    config["_cli_dry_run"] = args.dry_run  # the start screen then never deletes anything
    if args.no_rename:
        config["rename_numbered"] = False
    if args.strictness:
        config["strictness"], config["max_pixel_diff"] = args.strictness, None
    ui.apply_color_settings(config["color"])

    if args.install:
        return _install()
    if args.install_desktop_entry or args.remove_desktop_entry:
        return _desktop_entry(install=args.install_desktop_entry)
    if args.install_dbus_service or args.remove_dbus_service:
        return _dbus_service(install=args.install_dbus_service)

    for message in moved:
        ui.info(message)
    for message in warnings:
        ui.warn(f"Warning: {message}")
    if args.config and not args.config.is_file():
        ui.error(f"Config file not found: {args.config}")
        return 2

    missing = missing_packages()
    if missing:
        ui.error(f"Missing required packages: {', '.join(missing)}")
        print(f"To run {NAME} without installing it, set up the packages once in the project folder:")
        print("  python3 -m venv .venv")
        print(f"  .venv/bin/pip install {' '.join(missing)}")
        print("  .venv/bin/python run.py /path/to/pictures")
        print(f"Or install {NAME} with pipx, which takes care of this (see the README).")
        return 2
    if args.dbus:
        from . import dbus_service
        return dbus_service.serve()
    settings = {
        "stages": args.stages,
        "recursive": args.recursive,
        "no_ignore": args.no_ignore,
        "videos": config["include_videos"] and not args.no_videos,
        "confirm": "always" if args.interactive else "never" if args.auto else config["confirm"],
    }

    from . import app  # imports Pillow/send2trash, so only after the dependency check
    stop_on_signals()
    groups = None
    if args.groups:
        try:
            groups = app.load_groups(args.groups)
        except app.GroupsError as exc:
            ui.error(str(exc))
            return 2

    if not args.auto:  # questions: the page asks them
        from . import web
        initial = Path(args.path) if args.path else None  # from the app menu: the page asks
        return web.serve(lambda: app.run(initial, config, settings, auto=False, exclude=args.exclude, groups=groups),
                         open_browser=not args.no_browser and graphical_session(),
                         check_updates=config["check_for_updates"], unvisited=args.unvisited_timeout)
    update = _UpdateCheck() if config["check_for_updates"] else None
    try:
        code = app.run(Path(args.path or "."), config, settings, auto=args.auto, exclude=args.exclude, groups=groups)
    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130
    if update is not None:
        update.report()
    return code


class _UpdateCheck:
    """--auto asks nothing: a newer keepbest gets a line with the command at the end."""

    WAIT = 5.0  # at most this long after the run, for a check that hasn't answered yet

    def __init__(self) -> None:
        self.found: str | None = None
        self.thread = threading.Thread(target=self._check, name=f"{NAME}-update-check", daemon=True)
        self.thread.start()

    def _check(self) -> None:
        self.found = selfupdate.available()

    def report(self) -> None:
        self.thread.join(self.WAIT)
        if self.found is not None:
            how = selfupdate.how_installed(self.found)
            print()
            ui.info(f"{NAME} {self.found} is available (this is {__version__}). To update:")
            for line in how.command.splitlines():
                print(f"  {line}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
