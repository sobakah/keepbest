# keepbest – notes for Claude Code

Finds duplicate images and videos and removes redundant copies, keeping the best one.
A local browser page (the only way it asks;
`--auto` runs in the terminal without questions), Python 3.9+, dependencies Pillow and send2trash; ffmpeg/ffprobe optional (videos).
Owner: sobakah (GitHub `sobakah/keepbest`, private for now; first called img_dedupe). Developed on
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
  `selfupdate.py` (a newer keepbest, see the page),
  `viewer.py` (comparison apps for `v`, `show_in_folder`), `handoff.py` (runs media-optimizer
  from the end menu as a child process with `MO_CALLER=keepbest`, `MO_HOLD=0` and the run's
  folder choice as `MO_SUBFOLDERS`/`MO_EXCLUDE_FOLDERS` (JSON; same model: an excluded folder loses
  only its own files). One whose `run --help` lists `--return-url` (media-optimizer 0.8+) gets
  `--web --no-browser --return-url <keepbest's page> --return-name keepbest` and takes over the tab;
  an older one gets `-y` and its output goes into a box on the page. keepbest ignores
  SIGINT/SIGHUP meanwhile, they reach the child directly, and passes SIGTERM on; the handlers are
  set before the start, as no-op handlers rather than SIG_IGN so the child gets default handling).
  One whose help lists `--results` also gets `--results <temp file>`: media-optimizer's results file
  (its `results.py`, format `v` 1), read by `read_results` and turned by `summary` into tiles, a line
  per stage and the space both freed together (`freed` comes from the run's `Stats.space_saved`
  through `end_menu`). The tool event carries it as `results`; the page shows it in the tool box, the
  terminal after the exit code. `run_optimizer` starts with the banner `OPTIMIZER_BANNER`, which the
  page makes a step and a section of its own (`stepOf`).
- **Output and questions go through `front.py`**, never `print`/`input`. `app.py` and `core_logic.py`
  call `front.info/warn/…`, `front.show(kind, …)` for structured output (group tables, outcomes,
  summary) and `front.ask(screen)` with a `Screen` dataclass. The screen carries its data, `primary`
  (what Enter does) and `menu` (the other keys). The answer is a key: the page's buttons send them,
  and they are its keyboard shortcuts. Only the page asks (`web.WebFront`); `terminal.py` shows
  messages only (`--auto`, and the log of a page run, `WebFront.echo`) and raises `NeedsPage` if
  asked anything. `ui.py` holds its primitives. A new screen needs a dataclass in `front.py`, data in
  `web.screen_data` and a view in `app.js` (or the generic one); a new message kind needs an entry in
  `terminal._MESSAGES` and `web.message_data`. Wording is for the page: no "press x". `tests/test_front.py`
  runs the flow with a scripted front end and fails if anything reaches stdout directly. `cli.py`
  still prints its startup errors with `ui` directly.
- **The page** (everything but `--auto`; `--web` is accepted and does nothing, `--terminal` is an
  error; without `DISPLAY`/`WAYLAND_DISPLAY` no browser is opened, only the address printed):
  - `web.py` is the second front end, `WebFront`, with the HTTP server; `static/` holds the page
    (`app.js` with Preact+htm from `static/vendor/`, unchanged, licences in `LICENSES.txt`).
  - `serve(flow)` runs the flow in the **main thread**; the server and a watcher run in threads.
  - A closed page stops the flow at its next question with `web.Cancelled` (a
    `KeyboardInterrupt`), so the Ctrl+C handling saves progress. Work in progress is never cut off.
  - Answers are screen keys, checked against `screen_keys()` (ranges written out). Choosing a
    start-screen option directly (`{"set": [key, index]}`) presses the row's key until it shows.
    Key badges only for Enter and the numbers; other keys are a bold letter in the label (`Label`)
    and a hover title. A menu item with the primary's label is not shown twice.
  - Files are only reachable by the random ids `WebFront.file_id` hands out: previews, `/api/media`
    (videos, Range), `/api/file` (opening one in a tab: the original where the browser can show it,
    else rendered at full size; own CSP), `/api/reveal` (`viewer.show_in_folder`: FileManager1
    ShowItems over gdbus, else xdg-open of the folder). A file's `file` id is only given while it is
    on disk; the log's tables of a real run give it to the kept file only (`dry` in the show call).
    In a group's table Open shows the file below its row (`InlinePicture`, or `Player` for a video).
    The folder question carries `start` (the current folder), where the picker opens after a reload.
  - Each grouped image carries `beaten`: the `image_utils.deciding_criterion` (the first part of
    `score_image` in which the kept file ranks above it), for the "why" line and bold cells.
  - Pictures (and the remux player) are off until switched on; then on for the page's lifetime.
  - Forms (start screen, folder tree) stay shown, dimmed, after a change (and the yes/no question it
    leads to) until their next state arrives, so nothing flickers while files are counted.
  - media-optimizer's page: the tool event with `handoff` gets the `url` read from its output
    (`_HANDOFF`, only 127.0.0.1/localhost); the page goes there once (sessionStorage), and while it is
    away (`WebFront.away`, plus `HANDOFF_GRACE` after the child ended) keepbest's page doesn't count
    as gone.
  - Ctrl+C/SIGTERM in the terminal ends keepbest after `INTERRUPT_GRACE`, not when the tab closes.
  - Screens without their own view on the page get a generic one (table plus key buttons).
  - Group view: previews come from `image_utils.preview_jpeg` (the comparison's `_normalize`,
    so upright and 16-bit/CMYK/JXL safe), only by the random ids `WebFront.preview_id` hands out.
    Each grouped image carries `spot` (`difference_spot`: the worst 8×8 cell relative to #0, as
    fractions of the upright picture; saved in sessions, None in older ones). `regroup_around`
    and `_check_decided` move it along with the scores. View buttons blur after a click, so
    Enter always reaches the flow.
  - **Updates** (`selfupdate.py`, as in picktag): the newest `x.y.z` tag by `git ls-remote` (the repo
    is private; git has pipx's access, never prompts), at every start in a thread (the page's
    `serve(check_updates=…)`, `--auto`'s `cli._UpdateCheck`), the newest found kept in
    `state_dir()/update_check.json` for an offline start; `KEEPBEST_UPDATE_REPO` replaces the repo (tests: a missing
    one by default, `conftest.releases` makes one). `serve(check_updates=…)` checks in a thread and
    `WebFront.offer_update` puts it in the state; `/api/update` installs only at `UPDATE_SCREENS`
    or the folder question (a new version under a running one would mix lazy imports and
    `static/`), answers wait meanwhile. Success: `Restart` (a `Cancelled`) stops the flow, serve
    closes the server and `exec`s `restart_argv()` with `KEEPBEST_RESTART_PAGE` = "port token", so
    the new process keeps the pid (picktag waits on it) and the tab's address; the page reloads
    when `boot` changes. pipx: `upgrade` for an install from the repo, `install --force` with the new
    tag plus `inject` again for a pinned one, nothing for `pipx pin`. `--auto` prints the command.
  - `desktop.py` is the app-menu entry (from lyrembed; `Exec=… --web %f`). `running.py` is the
    lock: one JSON file per process in `state_dir()/running/`; overlapping folders are refused
    in `resolve_directory` and with `--auto`. It does nothing on Windows.

## Commands

```bash
python3 -m pytest                 # 360 tests, ~110 s (see below for which Python)
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
  keys=[...])` (runs the real CLI with `--no-browser` and answers its page over the API, one key per
  question: a screen key, "" for Enter, y/n for yes/no, a path; a key the question doesn't offer is
  recorded in `.typos` and skipped; keys running out close the page, which stops at the question
  with 130 as end of input did. The result has the terminal log as `stdout` and `.questions`/
  `.events` from the page), `asked(result, "StartScreen")` (that screen's question data),
  `shown(result)` (log plus every text the page asked with), `Page` (the API as the page's script
  uses it), `summary(result)` (parses the final numbers from the log), `fake_command` (stand-in programs on PATH; `.calls(count=…)` waits for background calls),
  `make_video` / `remux` (ffmpeg).
- `tests/pictures.py`: deterministic photo-like pictures. Calibrated: re-encodes/resizes score ≤ 16,
  edits (`with_text`, `with_patch`, `brighter`) ≥ 32, limit 20.
- `KEEPBEST_STATE_DIR` (old: `IMG_DEDUPE_STATE_DIR`) redirects log and sessions (otherwise a
  checkout writes them into the project folder). Never let tests touch the real project folder, config or trash.
- Every fix gets a test that fails without the fix. Prefer reproducing a reported bug first.
- **Checking that the terminal output (log, `--auto`) didn't change** (refactoring):
  - `KEEPBEST_RECORD=DIR` makes `run_cli` save every run's output there (paths, the page's address
    and saved-session times normalised); add `KEEPBEST_RECORD_COLOR=always` for a pass with colours.
  - Record the suite on the old code (a `git worktree` of the last commit, with the current
    `conftest.py` copied in) and on the new one, then `diff -r` the folders.
  - The colour pass fails about 10 tests that read plain text; that's expected, and both sides
    fail the same ones.
  - Runs are deterministic. Paths the suite doesn't reach need extra throwaway scenarios.
- **The page** is covered by API tests (`tests/test_web.py`, server in the test process, no
  browser). Clicking through it is a manual check: Playwright in a venv outside the repo,
  `chromium.launch(channel="chrome")` (Chrome is installed), keepbest started with the system
  `python3` and `--no-browser`, generated pictures only (the last script:
  `scratchpad/click21.py` of the 2026-10-22 session: tree, pictures switch, Open/Show in folder
  with a fake `gdbus`, one Quit, the hand-off to a media-optimizer 0.8 checkout via a wrapper). The page's CSP blocks
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
- Dry runs can undo a decided group (`u` on the handled screen, `undo_decision`), or change it at once:
  a file's number or `s` there returns `("change", answer)`, the review loop undoes and hands the answer
  (a path, or "s") to `_decision_screen` as `preset`. `_resolve` stores
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
  `aac_adtstoasc`, so MPEG-TS remuxes match. Only a crash (not an interrupt: 255 or
  SIGINT/SIGTERM/SIGHUP, see `video._INTERRUPTED`) lets the track-less second pass run. A file
  without a duration in its header gets one from its packets (`_measured_duration`). Automatic only when all tracks are identical and in
  the same order; a copy containing every track of the others in their order (`covering_copy`) is
  recommended; the same tracks in another order (`tracks_reordered`) are asked about. Without
  questions a group is only resolved when a covering copy exists, otherwise left alone and counted
  as skipped. Skipped remux groups stay in `exact_groups` (`ExactGroup.status`) and are left alone
  when the dry run is carried out; decided ones carry `decision` for the log.
- Carrying out a dry run maps Stage 1 renames into the later remux and visual groups; in the dry
  run itself, `ctx.planned` (renames a real run would make) ranks Stage 2/3 like a real run and
  counts each rename once; with `ctx.dropping` (files it would remove) a name an earlier group frees
  counts as free and one an earlier rename takes as taken (`_name_free`). `replay_dry_run` rebuilds
  both on resume and for `planned_changes`; `undo_decision` takes the group's share out again.
- A saved dry run is never carried out by Enter: on the start screen Enter opens it (`open_saved`:
  `run_scan(resume=…, open_overview=True)` as a dry run, its overview first), `a` carries it out;
  right after a dry run Enter carries it out and `o` opens it. Carrying out (`carry_out_dry_run`)
  always asks `[y/N]` first, in every mode. There is no "review again for real" any more (it trashed
  files with each decision and caught the owner out on 2026-10-29).
- Declining the permanent-mode `[y/N]` after choosing another keeper restores the group as found.
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
  sections, the older tag is moved to it and the newer tag and release are deleted.
  Old release numbers are not mentioned anywhere in the repo: the release history starts at
  1.0.0, pre-releases 0.5.1 and 0.6.0 are kept for the record only.
- Shell: the owner uses **fish** on Fedora. Commands meant for the owner to paste (loops,
  `&&` chains, `$(...)`) must be written for fish (`for x in …; …; end`, `; and`, `(…)`).
  Examples in the README are for all users and stay in bash. Don't rely on tools only some
  distributions ship.

## Environment notes

- Fedora's `ffmpeg-free` lacks some encoders (H.264); H.264 video tests skip there. The owner's
  machine now has a full ffmpeg (libx264, libx265), so they run. Remux detection itself needs no
  decoders.
- Known limitations (README): upscaled copies win on resolution; tiny watermarks can vanish at
  512 px; all candidate thumbnails are held in memory during stage 3 (~768 KB each, plus up to
  32 blurred ones per thread); MPEG-4 Part 2
  inside MPEG-TS can't be read by ffmpeg (left alone); macOS/Windows untested.

## Working style the owner expects

- Verify claims by running code before acting on them (several outside reviews contained wrong
  claims); say clearly what was tested and what wasn't.
- Small, focused changes with tests; summarise what changed, which files, and a commit message.
- Don't change the package folder name, the default delete mode (trash) or Enter's meaning without
  asking.

## Web page (agreed 2026-10-22)

Plan: `~/Software/Claude/Projects/keepbest/WEB_UI_PLAN.md` (it wins over `WEB_UI_PROPOSAL.md`
next to it). Mockup: https://claude.ai/artifact/U7avKy8pezpVzCakGEevQc.
- Done: the front-end layer, the server and page, start screen, folder tree and picker,
  desktop entry, lock, group review with pictures, overview grid, remux view with a player,
  media-optimizer on the page with `-y` and an output box (`handoff.run(output=…)`; the pipe is
  not read to its end once the child exited, so a stray grandchild can't block). The page is the
  default, and the terminal output never changed.
- Done (owner's request, 2026-10-22): terminal screens and kitty/timg removed, the tab
  hand-off to media-optimizer 0.8 (plan step 6), pictures off by default, Open/Show in folder,
  the "why" line, key badges only for Enter and numbers, the collapsed folder tree, one Quit.
- **The flow stays in the main thread** and the HTTP server runs in a thread: `handoff.py`'s
  `signal.signal()` and interrupt handling need the main thread. Tests that reach `handoff` on the
  page go through the real CLI (`cli_page` in `tests/test_web.py`).
- **Still open (plan step 7):** picktag may drop its terminal window (it only waits for the exit
  code; keepbest's terminal now only shows the address and a log).

## Open tasks (target: release 1.2.0)

1.0.0 (2026-10-27) is the first release, 1.0.1 (2026-10-29) shows media-optimizer's numbers, 1.1.0
(2026-10-29) opens saved dry runs instead of carrying them out, Keep buttons, faster stage 3; 0.5.1 (img_dedupe) and 0.6.0 (terminal screens) are
pre-releases kept for the record. New changes go under
an `## [Unreleased]` heading at the top of `CHANGELOG.md`.

- **Ask the owner:** `--auto` with `permanent` – only a warning, or an extra explicit option?
  Dry-run "not duplicates" marks write `.keepbest_ignore.json` into the picture folder, which
  contradicts "changes nothing" – change the README wording or store marks elsewhere (see E3).
- **Quick fixes:** printing a file name that isn't valid UTF-8 crashes on Python before 3.15
  (no UTF-8 mode: stdout is `strict`; checked with `PYTHONUTF8=0 LC_ALL=en_US.UTF-8`), so
  `test_a_name_that_is_not_utf_8_round_trips` sets `PYTHONIOENCODING`; README describes `n`/`x` differently
  though both discard the session; don't group 0-byte files; warn on `--exclude` without `-r`;
  friendlier goodbye than "Program terminated."; summary
  "Visually identical images" (adjust `summary()` regex in `tests/conftest.py`); `kitty` viewer
  fallback outside kitty (check return code); pytest should stop early with a clear message naming
  `sys.executable` when Pillow/send2trash are missing; "Missing required packages" should name the
  Python used; `--auto` should print excluded folders.
- **Performance:** with defaults the candidate search is always all-pairs (128 bits / 29 chunks =
  4 bits; 0.9 s for 5,000 images, ~90 s for 50,000) – numpy XOR+popcount or a BK-tree. Each step
  has a counter ("Finding candidates", "Loading pictures", "Comparing pixels" counting candidate
  pairs: compared, or settled when an image leaves with a group). All candidate thumbnails are
  held in memory – process clusters in batches of ~200.
- **Pixel comparison** (`find_groups.settle`): clusters (components) are settled in threads, the
  largest first; within one, anchors go in score order as before, and an anchor with 8+ candidates
  is compared with them in a second thread pool. Results don't depend on timing (groups sorted by
  keeper path); `stop` ends every thread at its next comparison after an error or Ctrl+C.
  `image_utils.Blurred` keeps up to 32 blurred thumbnails per cluster (the blur was 6 of the
  7.5 ms a pair took), only for pictures compared at their own size, so scores are unchanged.
  Measured: 5-6x faster on 16 cores (bigger clusters ~1.2 ms a pair).
- **Possible features (owner decides):** E1 folder priority (`--prefer DIR` / reference folders
  never deleted from – rated most valuable); E2 HEIC/HEIF and RAW (extensions for stage 1,
  optional `pillow-heif` for stage 3); E3 marks tied to content instead of relative paths; E4 list
  files with damaged EXIF (Pillow's "Truncated File Read" names no file); E5 log rotation, cleanup of
  old sessions (the lock against two instances is done, `running.py`); E6 README `curl` command to fetch
  `config.example.json` for pipx users.
- **Next release:** raise `__version__` (1.2.0 for behaviour changes, else 1.1.1) before
  tagging, turn "Unreleased" into that version in `CHANGELOG.md`, tests, commit, `git tag -a`,
  push with tags, GitHub release.
