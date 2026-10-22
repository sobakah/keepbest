"""Command-line entry point: find and remove duplicate images."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import signal
import sys
from pathlib import Path

from . import ui
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
    parser.add_argument("path", nargs="?", help="picture directory (default: the current directory; "
                                                "with --web, the page asks)")
    parser.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")

    scan = parser.add_argument_group("scanning")
    scan.add_argument("-r", "--recursive", action="store_true", help="include subdirectories")
    scan.add_argument("--exclude", action="append", metavar="DIR", default=[],
                      help="with -r, skip this subfolder (relative to the scanned folder; repeatable)")
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
    review.add_argument("--no-rename", action="store_true",
                        help='keep "(1)" in the names of kept copies')
    review.add_argument("--no-ignore", action="store_true",
                        help='show pairs marked "not duplicates" again, for this run')

    page = parser.add_argument_group("browser page (preview)")
    page.add_argument("--web", action="store_true", help="show keepbest in the browser instead of the terminal")
    page.add_argument("--no-browser", action="store_true", help="with --web: only print the page's address")
    entry = page.add_mutually_exclusive_group()
    entry.add_argument("--install-desktop-entry", action="store_true",
                       help="add keepbest to the app menu (it opens the page) and exit")
    entry.add_argument("--remove-desktop-entry", action="store_true", help="remove that entry and exit")

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.web and args.auto:
        parser.error("--web and --auto don't go together: --auto asks nothing, so there is no page to show")
    configure_logging(args.verbose)

    moved, migration_warnings = migrate_old_locations()  # before the config is looked for
    config, warnings = load_config(args.config)
    warnings = migration_warnings + warnings
    if args.color:
        config["color"] = args.color
    config["_configured_delete_mode"] = config["delete_mode"]  # what "for real" means after a dry run
    if args.dry_run:
        config["delete_mode"] = "dry_run"
    config["_cli_dry_run"] = args.dry_run  # the start screen then never deletes anything
    if args.no_rename:
        config["rename_numbered"] = False
    if args.strictness:
        config["strictness"], config["max_pixel_diff"] = args.strictness, None
    ui.apply_color_settings(config["color"])

    if args.install_desktop_entry or args.remove_desktop_entry:
        return _desktop_entry(install=args.install_desktop_entry)

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
    if not ui.HAS_READLINE and not args.auto and not args.web:
        ui.warn("readline is unavailable - Tab completion and prefilled prompts are disabled. "
                "On Windows: pip install pyreadline3")

    settings = {
        "stages": args.stages,
        "recursive": args.recursive,
        "no_ignore": args.no_ignore,
        "videos": config["include_videos"] and not args.no_videos,
        "confirm": "always" if args.interactive else "never" if args.auto else config["confirm"],
    }

    from . import app  # imports Pillow/send2trash, so only after the dependency check
    stop_on_signals()

    if args.web:
        from . import web
        initial = Path(args.path) if args.path else None  # from the app menu: the page asks
        return web.serve(lambda: app.run(initial, config, settings, auto=False, exclude=args.exclude),
                         open_browser=not args.no_browser)
    try:
        return app.run(Path(args.path or "."), config, settings, auto=args.auto, exclude=args.exclude)
    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
