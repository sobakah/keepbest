#!/usr/bin/env python3
"""Standalone launcher so a repository checkout stays runnable as ./run.py.

The actual code lives in the sibling package directory ``keepbest/``. The
launcher must not be called ``keepbest.py``: without the package directory,
``import keepbest`` would then import this file itself instead of failing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODULES = ["app.py", "cli.py", "config.py", "core_logic.py", "handoff.py", "image_utils.py",
           "migrate.py", "session.py", "ui.py", "video.py", "viewer.py"]

try:
    from keepbest.cli import main
except ModuleNotFoundError as exc:
    if exc.name != "keepbest":
        raise

    loose = sorted(p.name for p in HERE.glob("*.py") if p.name in MODULES)

    print("keepbest: the 'keepbest' package was not found.\n", file=sys.stderr)
    print(f"Looked in: {HERE}", file=sys.stderr)

    if loose:
        print(
            "\nThe module files are sitting next to this launcher instead of inside\n"
            "a 'keepbest/' directory. This happens when the files are downloaded\n"
            "individually. Fix it with:\n\n"
            f"    cd {HERE}\n"
            "    mkdir -p keepbest\n"
            f"    mv {' '.join(MODULES)} __main__.py keepbest/\n"
            "    touch keepbest/__init__.py\n",
            file=sys.stderr,
        )
    else:
        print(
            "\nExpected this layout:\n\n"
            "    run.py\n"
            "    keepbest/\n"
            "        __init__.py  __main__.py  app.py  cli.py  config.py  core_logic.py\n"
            "        handoff.py  image_utils.py  migrate.py  session.py  ui.py  video.py  viewer.py\n",
            file=sys.stderr,
        )
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
