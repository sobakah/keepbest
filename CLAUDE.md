# keepbest – notes for Claude Code

Finds duplicate images and videos and removes redundant copies, keeping the best one.
Terminal app (no GUI), Python 3.9+, dependencies Pillow and send2trash; ffmpeg/ffprobe optional (videos).
Owner: sobakah (GitHub `sobakah/keepbest`, public; called img_dedupe before 1.0.0). Developed on
Fedora, installed with pipx (Python 3.15). The local checkout is in `~/Projekte/Git-Repos/keepbest`.

## Layout – important

- Same layout as the owner's project lyrembed: the package is the folder **`keepbest/`**,
  `run.py` is the launcher for a checkout (explains a missing package folder), tests in `tests/`,
  `CHANGELOG.md` in the root. All imports inside `keepbest/` are relative. Command, package and
  pipx name are all `keepbest`. The name lives in `NAME` (and the old one in `OLD_NAME`) in
  `config.py`; use them in new code.
- From the checkout: `./run.py <folder>` or `python3 -m keepbest <folder>`. `build/` and
  `*.egg-info/` appear after `pip install .`/`pipx install .` from the checkout; ignored, deletable.
- `migrate.py` moves the files kept under the old name (config and state folders, log file) on
  every start, before the config is read; `IgnoreList` takes over `.img_dedupe_ignore.json`.
  Existing new-name folders are never overwritten. Keep this in place for a long time.
- `keepbest/cli.py` arguments → `app.py` (start screen, folder list, run_scan, sessions flow)
  → `core_logic.py` (the three stages, review loop, deletion, resume) → `image_utils.py`
  (normalising, hashing, pixel comparison), `video.py` (ffprobe, stream hash), `session.py`
  (log, saved sessions, "not duplicates" marks), `config.py` (defaults, validation, version),
  `journal.py` (the file journal, see below), `front.py` / `terminal.py` / `ui.py` (see below),
  `viewer.py`, `handoff.py` (runs media-optimizer
  from the end menu as a child process with `MO_CALLER=keepbest`, `MO_HOLD=0` and the run's
  folder choice as `MO_SUBFOLDERS`/`MO_EXCLUDE_FOLDERS` (JSON; same model: an excluded folder loses
  only its own files); keepbest ignores
  SIGINT/SIGHUP meanwhile, they reach the child directly, and passes SIGTERM on; the handlers are
  set before the start, as no-op handlers rather than SIG_IGN so the child gets default handling).
- **Output and questions go through `front.py`**, never `print`/`input`. `app.py` and `core_logic.py`
  call `front.info/warn/…`, `front.show(kind, …)` for structured output (group tables, outcomes,
  summary) and `front.ask(screen)` with a `Screen` dataclass. The screen carries its data, `primary`
  (what Enter does) and `menu` (the other keys). The answer is the key a user would type, so a page
  can send the same keys from buttons. `terminal.py` is the default front end and draws everything;
  `ui.py` holds its primitives. A new screen needs a dataclass in `front.py` and a renderer in
  `terminal.py`; a new message kind needs an entry in `terminal._MESSAGES`. `tests/test_front.py`
  runs the flow with a scripted front end and fails if anything reaches stdout directly. `cli.py`
  still prints its startup errors with `ui` directly.
- **The page (`--web`, preview)**:
  - `web.py` is the second front end, `WebFront`, with the HTTP server; `static/` holds the page
    (`app.js` with Preact+htm from `static/vendor/`, unchanged, licences in `LICENSES.txt`).
  - `serve(flow)` runs the flow in the **main thread**; the server and a watcher run in threads.
  - A closed page stops the flow at its next question with `web.Cancelled` (a
    `KeyboardInterrupt`), so the Ctrl+C handling saves progress. Work in progress is never cut off.
  - Answers are screen keys, checked against `screen_keys()` (ranges written out). Choosing a
    start-screen option directly (`{"set": [key, index]}`) presses the row's key until it shows.
  - Screens without their own view on the page get a generic one (table plus key buttons).
  - Group view: previews come from `image_utils.preview_jpeg` (the comparison's `_normalize`,
    so upright and 16-bit/CMYK/JXL safe), only by the random ids `WebFront.preview_id` hands out.
    Each grouped image carries `spot` (`difference_spot`: the worst 8×8 cell relative to #0, as
    fractions of the upright picture; saved in sessions, None in older ones). `regroup_around`
    and `_check_decided` move it along with the scores. View buttons blur after a click, so
    Enter always reaches the flow.
  - `desktop.py` is the app-menu entry (from lyrembed; `Exec=… --web %f`). `running.py` is the
    lock: one JSON file per process in `state_dir()/running/`; overlapping folders are refused
    in `resolve_directory` and with `--auto`. It does nothing on Windows.

## Commands

```bash
python3 -m pytest                 # 287 tests, ~45 s (see below for which Python)
python3 -m pytest -k video        # a subset
./run.py ~/Pictures --dry-run                # run from the checkout
```

Skips: video tests without ffmpeg (H.264 ones also with Fedora's `ffmpeg-free`), two JXL tests
without `pillow-jxl-plugin`, and the check of `MO_EXCLUDE_FOLDERS` against media-optimizer's
parser unless `media_optimizer` is importable. On the owner's machine 3 skip (JXL + parser).

Run the tests with a Python that has Pillow, send2trash and pytest. If those are missing, the CLI
tests fail with misleading assertions like `(0, 0) == (1, 0)` (the program exits "Missing required
packages"). On the owner's machine (as of 2026-10-16) no single Python has all three: the system
`python3` (3.15) has pytest and Pillow, the pipx venv (3.15) has send2trash but no pytest, and the
user site-packages still hold send2trash only for 3.14. What works:

```bash
PYTHONPATH=~/.local/share/pipx/venvs/keepbest/lib/python3.15/site-packages python3 -m pytest
```

(The tests use their own HOME, so the user site isn't found on its own anyway.) Alternatively
`pipx inject keepbest pytest`, then `"$(pipx environment --value PIPX_LOCAL_VENVS)/keepbest/bin/python" -m pytest`.
Both paths change when the Python version changes.

## Tests

- `tests/conftest.py`: autouse isolation (own HOME, XDG dirs, trash and `KEEPBEST_STATE_DIR`),
  `library` (build picture folders: `photo`, `variant`, `copy_of`), `run_cli(folder, *args,
  keys=[...])` (runs the real CLI, typed answers via stdin), `summary(result)` (parses the final
  numbers), `fake_command` (stand-in programs on PATH; `.calls(count=…)` waits for background calls),
  `make_video` / `remux` (ffmpeg).
- `tests/pictures.py`: deterministic photo-like pictures. Calibrated: re-encodes/resizes score ≤ 16,
  edits (`with_text`, `with_patch`, `brighter`) ≥ 32, limit 20.
- `KEEPBEST_STATE_DIR` (old: `IMG_DEDUPE_STATE_DIR`) redirects log and sessions (otherwise a
  checkout writes them into the project folder). Never let tests touch the real project folder, config or trash.
- Every fix gets a test that fails without the fix. Prefer reproducing a reported bug first.
- **Checking that the terminal output didn't change** (refactoring):
  - `KEEPBEST_RECORD=DIR` makes `run_cli` save every run's output there (paths and saved-session
    times normalised); add `KEEPBEST_RECORD_COLOR=always` for a pass with colours.
  - Record the suite on the old code (a `git worktree` of the last commit, with the current
    `conftest.py` copied in) and on the new one, then `diff -r` the folders.
  - The colour pass fails about 10 tests that read plain text; that's expected, and both sides
    fail the same ones.
  - Runs are deterministic. Paths the suite doesn't reach need extra throwaway scenarios.
- **The page** is covered by API tests (`tests/test_web.py`, server in the test process, no
  browser). Clicking through it is a manual check: Playwright in a venv outside the repo,
  `chromium.launch(channel="chrome")` (Chrome is installed), keepbest started with the system
  `python3` and `--web --no-browser`, generated pictures only. The page's CSP blocks
  `wait_for_function`; wait with locators.

## Behaviour that must not regress (safety rules)

- Files go to the trash by default; nothing is deleted without being compared against the file
  that is **kept** (stage 3 compares each member with the keeper, never via chains).
- Enter keeps the recommended file; in `permanent` mode deletions in interactive screens ask `[y/N]`
  (default No). Refused in every mode (`is_protected`, one shared message): the home folder, every
  folder above it, the root folder and `SYSTEM_FOLDERS`; compared with symlinks resolved (home
  reached through a symlink, e.g. Fedora Atomic's `/home -> /var/home`).
- Choosing another keeper than the recommended one (`regroup_around`) compares every other image
  with the chosen one afresh; images above the limit stay on disk and leave the group.
- Files with several pictures (MPO, multi-page TIFF; `frames` > 1; animations are GIF/PNG/WebP/
  AVIF/JXL only) are compared by their first picture but never removed: `find_groups` never takes
  them into another anchor's group, `regroup_around` leaves them out.
- "Not duplicates" marks separate pairs, not groups: `find_groups` keeps a member marked against
  the anchor out of its group (it is compared with the rest again); `i0` marks #0 against the
  others and regroups them around the next one.
- Dry runs can undo a decided group (`u` on the handled screen, `undo_decision`): `_resolve` stores
  the group before the decision (`Group.undo`, saved in the session) and what it added to the
  totals; undo restores both and the group is always asked again. Real runs store no undo data.
- Before resuming a session or carrying out a dry run, every file is checked against the disk
  (size + mtime); changed or missing files are left alone.
- `--dry-run` on the command line always wins (`config["_cli_dry_run"]`): nothing is deleted or
  renamed in that run. The start screen locks the delete mode, doesn't resume a saved real session
  or carry out a saved dry run, and no carry-out is offered after the dry run; `run_scan` raises if
  a real mode slips through. A dry run chosen with setting 4 may still be carried out right after.
- `permanent` mode asks once before an interactive run starts (`confirm_permanent`), since
  automatic decisions then delete without further questions. `--auto` asks nothing (open point).
- Stage 2 hashes every other track by content too (`stream_hashes`): further real video streams
  (`extra_videos`), audio, subtitles and data (timecode, telemetry); AAC normalised with
  `aac_adtstoasc`, so MPEG-TS remuxes match. Automatic only when all tracks are identical and in
  the same order; a copy containing every track of the others in their order (`covering_copy`) is
  recommended; the same tracks in another order (`tracks_reordered`) are asked about. Without
  questions a group is only resolved when a covering copy exists, otherwise left alone and counted
  as skipped. Skipped remux groups stay in `exact_groups` (`ExactGroup.status`), so reviewing a
  dry run again asks about them; decided ones carry `decision` for the log.
- Carrying out a dry run maps Stage 1 renames into the later remux and visual groups; in the dry
  run itself, `ctx.planned` (renames a real run would make) ranks Stage 2/3 like a real run and
  counts each rename once. Reviewing a dry run again restores each visual group from `Group.undo`.
- When nothing of a group could be removed, there is no rename and (real runs) the group stays open.
- Interrupts during Stage 2 stop ffprobe/ffmpeg at once (`video.stop_all` via `_threaded`); a
  stopped ffmpeg is never retried.
- A dry run resumes as a dry run; settings that decided a session's groups (stages, strictness
  looser, videos, subfolders) are locked on the start screen; stricter strictness works without a
  rescan (`tighten_groups`).
- The main folder is only excluded with its own key `m` (or `--exclude .`); numbers/ranges/a/n in
  the folder list change subfolders only; each listed folder switches on its own (`switch_folders`);
  subfolders that would change along are only switched after a `[Y/n]` question (`subfolders_along`).
- Copy numbers "(n)" are only removed from a kept file when another file of the group shares the
  base name (series names like `Holiday (12).jpg` stay); never overwrite an existing name.
- Keeper ranking: resolution → lossless over lossy/unknown → `format_ranks` → no copy number → bpp
  → oldest. JPEG XL is "unknown" (Pillow can't tell lossy from lossless), so a lossless original
  wins; config `prefer_jxl: true` ranks JXL like lossless (`lossless_rank`). TIFF is lossy only
  when JPEG/WebP/SGILog-compressed.
- Greyscale with more than 8 bits (I;16, I, F) is never scaled per file for the pixel comparison:
  `comparison_thumbnail` keeps the stored values and `pixel_difference` brings both images of a
  pair to one scale (8-bit x 257) and stretches them together by the brighter one's maximum. So a
  dark 16-bit picture matches its 8-bit copy, a 2x/4x brighter version doesn't, and edits in
  10/12-bit data in 16-bit files aren't squeezed into a few levels. `_to_8_bit` (scaling by the
  bit depth the values use) is only used for the dhash, which ignores brightness. Non-finite float
  values are reported as an error for that file.

## The file journal – a contract with picktag

`journal.py` appends one JSON line per change to `journal.jsonl` in the state folder (always
`state_dir()`, also from a checkout; config `journal_file`). The owner's tagger **picktag** reads it
(and media-optimizer's) to move tags from removed copies to the kept file and to follow renames, so
**picktag depends on this format**. The contract is `~/Software/Claude/Projects/picktag/JOURNAL_SPEC.md`
(format version 1; outside this repo, the README has the user-facing summary): changing the meaning
of a field or event needs `"v": 2` and a matching picktag change; adding fields is fine. Rules: an
event only after the action succeeded, none for failures or dry runs (not even `run_start`), stat
before removing (`Journal.file_state`), `ensure_ascii=True` (non-UTF-8 names as `\udcXX`), append
with `flock`, never truncate, a journal error only warns once. Events: `run_start`/`run_end` around
each `run_scan` of a real run (one `run` id per `run_scan`, since one process can do several runs;
`code` is what the program would exit with: 0/1, 130 when interrupted), `removed` in
`execute_deletion` (`match` exact/remux/visual, `hard_link`), `renamed` in `rename_kept`
(`copy_number`). Those two functions are the only places that change media files; a new one needs
an event too. Events are in order: a `kept` file may be renamed or removed by a later event of the
same run (Stage 1 keeper loses to a larger picture in Stage 3), so consumers replay them in order.

## Stages and terms

1 identical files (SHA-256, same-size files only) · 2 remuxed videos (ffprobe preselects, then a
container-independent hash of the video stream; H.264/HEVC "picture data only" via bitstream
filters; audio/subtitle tracks hashed as well) · 3 visually identical images (128-bit dhash candidates, then worst 8×8-cell pixel
difference at ≤ 512 px; presets strict 12 / normal 20 / loose 28; borderline above
`uncertain_ratio` × limit, default 0.8). `--stages` takes numbers (`1,2`, `3`, `all`; `both` = all).

## Conventions

- UI style follows the owner's other project lrckit: plain ANSI via `StyleUI`, banners, `[BADGE]`s,
  grouped key menus, highlighted Enter action, `▶` marker, greyed/struck-through unavailable choices,
  `‹ ›` markers when colours are off. Keep new screens consistent.
- Config: new keys go into `DEFAULT_CONFIG`, get validated in `config.py` (types/ranges → warning +
  default), and appear in `config.example.json` (regenerate from `DEFAULT_CONFIG`) and the README.
- README is in English, concise, reader-first order; every option/key documented once. Keep it in
  sync with behaviour (reviews found mismatches before).
- Commit messages: English, subject ≤ 72 chars, body wrapped at 72, explain what and why.
- Versions: three parts (`1.0.0`); `__version__` in `keepbest/config.py` is the only source.
  Features or behaviour changes → minor (1.0.0 → 1.1.0), pure fixes → patch (1.0.1). Tags have
  **no `v`** (`1.0.0`), like lyrembed. Release: update `CHANGELOG.md` (Keep a Changelog: newest
  first, `## [X.Y.Z] — date`, sections Added/Changed/Fixed, upgrade notes, link line at the
  bottom), run tests, commit, `git tag -a X.Y.Z -m "keepbest X.Y.Z"`, push with tags, then create
  the GitHub release: `gh release create X.Y.Z --title "keepbest X.Y.Z" --notes-file <that
  version's section of CHANGELOG.md, without heading>`. Without a version bump
  `pipx upgrade` doesn't pick up changes.
- Since 1.0.0 the history is never rewritten (no rebase, no force-push of `main`); a mistake in
  a release is fixed with a new version. If the owner wants a release folded into the previous one,
  do it like media-optimizer: a new commit sets the version back and merges the CHANGELOG
  sections, the older tag is moved to it and the newer tag and release are deleted. (Before 1.0.0, releases 1.0–1.4.1 were renumbered as
  pre-releases 0.1.0–0.5.1 and the history was rebuilt with one commit per release; a backup of
  the old history and releases is in `~/Software/Claude/Projects/img_dedupe-backup-2026-10-08/`.
  The same day, a keepbest 1.1.0 (the rename) was folded into a new 1.0.0 together with the move
  to the lyrembed layout, and all tags lost their `v`; backup in
  `~/Software/Claude/Projects/keepbest-backup-2026-10-08/`. On 2026-10-10, 1.2.0 was folded into
  1.1.0 that way, an hour after both were published; backup in
  `~/Software/Claude/Projects/keepbest-backup-2026-10-10/`. On 2026-10-17 the typo fixes were added
  to 1.2.1 hours after its release: the tag `1.2.1` moved from `ae6907a` to the commit with them,
  the release notes were updated; no version bump, so existing installs need `pipx reinstall`.)
- Shell: the owner uses **fish** on Fedora. Commands meant for the owner to paste (loops,
  `&&` chains, `$(...)`) must be written for fish (`for x in …; …; end`, `; and`, `(…)`).
  Examples in the README are for all users and stay in bash. Don't rely on tools only some
  distributions ship.

## Environment notes

- Fedora's `ffmpeg-free` lacks some encoders (H.264); H.264 video tests skip there. The owner's
  machine now has a full ffmpeg (libx264, libx265), so they run. Remux detection itself needs no
  decoders.
- Known limitations (README): upscaled copies win on resolution; tiny watermarks can vanish at
  512 px; all candidate thumbnails are held in memory during stage 3 (~768 KB each); MPEG-4 Part 2
  inside MPEG-TS can't be read by ffmpeg (left alone); macOS/Windows untested.

## Working style the owner expects

- Verify claims by running code before acting on them (several outside reviews contained wrong
  claims); say clearly what was tested and what wasn't.
- Small, focused changes with tests; summarise what changed, which files, and a commit message.
- Don't change the package folder name, the default delete mode (trash) or Enter's meaning without
  asking.

## Web page (agreed with the owner 2026-10-22, steps 1–3 done)

Plan: `~/Software/Claude/Projects/keepbest/WEB_UI_PLAN.md` (it wins over `WEB_UI_PROPOSAL.md`
next to it). Mockup: https://claude.ai/artifact/U7avKy8pezpVzCakGEevQc.
- The page becomes the default in **2.0.0**, once it can do everything the terminal can; until
  then it sits behind `--web`. `--terminal` keeps today's screens. `--auto` and sessions without a
  display stay in the terminal.
- Order: separate logic from the terminal (**done 2026-10-22**, `front.py`; the terminal output
  stayed byte-for-byte the same) → server and start screen with a folder picker, the folder tree, the
  desktop entry (copied from lyrembed) and the lock (E5) (**done 2026-10-22**) → group review (**done**) → overview, resume, remux
  groups → media-optimizer hand-off.
- **The flow stays in the main thread** and the HTTP server runs in a thread: `handoff.py`'s
  `signal.signal()` and interrupt handling need the main thread.
- media-optimizer keeps the terminal as its default. It gets a thin page behind `--web` (brief:
  `~/Software/Claude/Projects/media-optimizer/WEB_PAGE_BRIEF.md`). keepbest hands the tab over
  like cdimport → lyrembed (`--no-browser --return-url --return-name`), and runs an older
  media-optimizer with `-y` and an output box. Closing its tab stops a run unless the user chose
  the background; then a desktop notification reports the end (`notify-send`, else `gdbus`).

## Open tasks (target: release 1.4.0)

1.0.0 is the first stable release and the first under the name keepbest; 1.1.0 added the
media-optimizer hand-off on the end screen, the image/video counts and the subfolder question
in the folder list; 1.2.0 (2026-10-16) passes the folder choice to media-optimizer (needs its 2.2)
and has the fixes from an outside review (16-bit greyscale, extra video streams, track order,
protected folders, marks, MPO/TIFF, dry-run carry-out, `i0`); 1.2.1 (2026-10-17) names the right
command for the JPEG XL plugin (`pipx inject`) and asks again after typos (end screen, yes/no);
1.3.0 (2026-10-18) adds the file journal for picktag and fixes the log crash on non-UTF-8 names. New changes go under
an `## [Unreleased]` heading at the top of `CHANGELOG.md`.

- **Ask the owner:** `--auto` with `permanent` – only a warning, or an extra explicit option?
  Dry-run "not duplicates" marks write `.keepbest_ignore.json` into the picture folder, which
  contradicts "changes nothing" – change the README wording or store marks elsewhere (see E3).
- **Quick fixes:** printing a file name that isn't valid UTF-8 crashes on Python before 3.15
  (no UTF-8 mode: stdout is `strict`; checked with `PYTHONUTF8=0 LC_ALL=en_US.UTF-8`), so
  `test_a_name_that_is_not_utf_8_round_trips` sets `PYTHONIOENCODING`; unhandled
  `p.stat()` in `find_exact_duplicates` when a file vanishes; README describes `n`/`x` differently
  though both discard the session; don't group 0-byte files; warn on `--exclude` without `-r`;
  friendlier goodbye than "Program terminated."; summary
  "Visually identical images" (adjust `summary()` regex in `tests/conftest.py`); `kitty` viewer
  fallback outside kitty (check return code); pytest should stop early with a clear message naming
  `sys.executable` when Pillow/send2trash are missing; "Missing required packages" should name the
  Python used; `--auto` should print excluded folders.
- **Performance:** with defaults the candidate search is always all-pairs (128 bits / 29 chunks =
  4 bits; 0.9 s for 5,000 images, ~90 s for 50,000, no progress shown) – numpy XOR+popcount or a
  BK-tree, at least a progress line. Pixel comparisons run serially and re-blur the anchor per
  pair – cache blurred thumbnails. All candidate thumbnails are held in memory – process clusters
  in batches of ~200.
- **Possible features (owner decides):** E1 folder priority (`--prefer DIR` / reference folders
  never deleted from – rated most valuable); E2 HEIC/HEIF and RAW (extensions for stage 1,
  optional `pillow-heif` for stage 3); E3 marks tied to content instead of relative paths; E4 list
  files with damaged EXIF (Pillow's "Truncated File Read" names no file); E5 log rotation, cleanup of
  old sessions (the lock against two instances is done, `running.py`); E6 README `curl` command to fetch
  `config.example.json` for pipx users.
- **Next release:** raise `__version__` (1.4.0 for behaviour changes, else 1.3.1) before
  tagging, turn "Unreleased" into that version in `CHANGELOG.md`, tests, commit, `git tag -a`,
  push with tags, GitHub release.
