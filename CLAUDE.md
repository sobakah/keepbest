# keepbest – notes for Claude Code

keepbest finds duplicate pictures and videos in a folder and removes the redundant copies, keeping
the best one. Python 3.9+, dependencies Pillow and send2trash (optional `pillow-jxl-plugin`;
ffmpeg/ffprobe for videos). It asks only on its page in the browser (`web.py` and `static/`,
Preact+htm); `--auto` runs in the terminal without questions. User docs: `README.md`. The file
journal's contract is picktag's `docs/JOURNAL_SPEC.md`.

## The program family

<!-- This section is the same in all six repositories. Change it in all of them or none. -->

Six programs by the same owner, each in its own private repository `sobakah/<name>` with its own
CLAUDE.md. Other sessions work in the other repositories: change one only when the owner asks.

| Program | What it does | Works with |
|---|---|---|
| picktag | Tags and searches pictures, videos and documents (GTK 4, libadwaita) | reads keepbest's and media-optimizer's file journals; starts keepbest for *Find Duplicates* |
| keepbest | Finds duplicate pictures and videos and keeps the best copy (page in the browser) | hands its tab to media-optimizer; writes a file journal |
| media-optimizer | Converts pictures, GIFs and videos to smaller formats (page in the browser) | started from keepbest; writes a file journal |
| cdport | Rips CDs, tags them and adds covers and lyrics (page in the browser) | starts lyrembed; D-Bus service for Jewelcase |
| lyrembed | Finds lyrics, embeds them in music files, romanizes them (page in the browser) | started from cdport; D-Bus service for Jewelcase |
| Jewelcase | A GNOME music player for an album library, with Spotify (GTK 4, libadwaita) | uses cdport and lyrembed; never writes music files |

The file journal format is specified in picktag's `docs/JOURNAL_SPEC.md`. The programs with a page
share its look (libadwaita's design language): `static/base.css` and `static/icons.js` are
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; change them in lyrembed and
copy them back. Setup details of the owner's machine are in `CLAUDE.local.md` (not in git).

On image-based systems (Fedora Silverblue, Bluefin) the programs run on the host where the image
has what they need (pipx from its zipapp, or layered with `rpm-ostree`); cdport runs in a toolbox,
where cyanrip can be built, and Jewelcase comes as a Flatpak bundle on its releases. In a toolbox
or distrobox, what the host starts (app-menu entry, D-Bus service) enters the container, and what
a program opens in other apps (browser, Flatpaks, file manager) leaves it: `container.py`,
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; picktag's
`core/desktop.py`. The suites of the four make no container unless a test asks (an autouse
fixture), so they run alike in a toolbox. Each README has a section *Fedora Silverblue, Bluefin
and other image-based systems* under its installation.

### Working rules

- **Commits**: commit finished work once its tests pass, locally, on the default branch;
  experiments stay uncommitted. **Push, tags and releases only when the owner says so**; a request
  to release includes commit, tag and push. Pushed history is never rewritten; a mistake in a
  release is fixed with a new version.
- **Commit messages**: English; a subject of at most 72 characters saying what changed; a body
  wrapped at 72 saying why and what was verified. Written directly, no draft files.
- **Before pushing**, check the diff for personal data: the owner's name, home paths, file and
  folder names or contents from the owner's libraries, details of the owner's machine. Those
  belong in `CLAUDE.local.md`.
- **Verify, don't assert.** Run the code before saying something works; say what was tested and
  what wasn't. Outside reviews have been wrong before: check each claim.
- **Every bug fix comes with a test that fails without it.**
- **Tests never touch the owner's data** (libraries, music, settings, journals, the trash), the
  network or real hardware; they use temporary folders and generated media.
- **Ask first** before installing system packages, deleting or overwriting the owner's files, or
  changing a default the owner chose.
- Stop processes by PID, never with `pkill -f`. Commands for the owner to paste are written for
  fish; examples in the docs use bash.

### Versions and releases

- Versions have three parts and tags no `v` (`1.2.0`). New features raise the minor version. A
  release of a few changes that fix bugs is a patch, also when a fix changes behaviour. The
  version lives in one place (see Releasing below).
- Every user-visible change goes under `## [Unreleased]` at the top of `CHANGELOG.md` when it is
  committed. Don't propose a release after every change: say what is waiting and suggest one when
  a feature or a few fixes have gathered.
- A release: the version raised, `## [Unreleased]` becomes `## [X.Y.Z] — YYYY-MM-DD`, the full test
  suite passes, one commit `<program> X.Y.Z: <what it brings>`, an annotated tag
  `git tag -a X.Y.Z -m "<program> X.Y.Z"`, push of the branch and the tag, then a GitHub release
  titled `<program> X.Y.Z` whose notes are that version's CHANGELOG section.
- Installed copies look for newer releases by their tags, so a tag above the current version must
  never exist. Pre-releases below 1.0.0 on GitHub are an archive; they are not mentioned in the
  repository.

### Documentation

- `README.md` is for people using the program: what it does, installing, using it, settings,
  working with the other programs, limits. Current state only.
- `CHANGELOG.md` follows Keep a Changelog, newest first: `### Added`, `### Changed`, `### Fixed`,
  bullets in the user's terms (a bold lead-in for the important ones), no internal reasons, no
  commit hashes.
- `CLAUDE.md` is for Claude sessions: the same section order in every repository. Keep a reason
  only where it stops a session from undoing a decision.
- Update README and CHANGELOG in the commit that changes the behaviour.

### Wording

British spelling (colour, behaviour, analyse); names of code, GTK and GNOME stay as they are.
Program names are written as above (lower case, except Jewelcase). Terms used in all six:

| Use | Not |
|---|---|
| folder | directory (except in code and option names) |
| the page (the program's page in the browser) | web UI, frontend, site |
| settings | options, preferences (except GNOME's *Preferences* window in the GTK apps) |
| dry run (`--dry-run`) | simulation, test run |
| the trash; *move to the trash* | recycle bin, wastebin |
| the owner (in CLAUDE.md) | the user, the maintainer, a name |
| you (in README) | the user |
| Enter, Esc, Ctrl+C | ENTER, Return, ^C |

## Layout

- The package is the folder **`keepbest/`** (all imports inside are relative); `run.py` is the
  launcher for a checkout (explains a missing package folder); tests in `tests/`. Command, package
  and pipx name are `keepbest`; the name lives in `NAME` (the old one in `OLD_NAME`) in
  `config.py`: use them in new code. `build/` and `*.egg-info/` from `pip install .` are ignored.
- `cli.py` arguments → `app.py` (start screen, folder list, `run_scan`, sessions flow, end menu,
  `--groups`) → `core_logic.py` (the three stages, review loop, deletion, resume) →
  `image_utils.py` (normalising, hashing, pixel comparison), `video.py` (ffprobe, stream hash).
  Further: `session.py` (log, saved sessions, "not duplicates" marks), `config.py` (defaults,
  validation, version), `journal.py`, `front.py` / `terminal.py` / `ui.py` / `web.py` (see
  Conventions), `selfupdate.py`, `viewer.py` (comparison apps for `v`, `show_in_folder`),
  `handoff.py` (media-optimizer), `desktop.py` (app-menu entry, `Exec=… --web %f`), `container.py`
  (toolbox and distrobox, lyrembed's, unchanged; see the family block), `running.py`
  (the lock: one JSON file per process in `state_dir()/running/`; overlapping folders are refused
  in `resolve_directory` and with `--auto`; does nothing on Windows).
- `migrate.py` moves the config and state folders kept under the old name img_dedupe on every
  start, before the config is read; `IgnoreList` takes over `.img_dedupe_ignore.json`,
  `IMG_DEDUPE_STATE_DIR` still works. Existing new-name folders are never overwritten. Keep it.
- **`handoff.py`** runs media-optimizer from the end menu as a child process: `run DIR --web
  --no-browser --return-url <keepbest's page> --return-name keepbest --results <temp file>`, with
  `MO_CALLER=keepbest` and the run's folder choice as `MO_SUBFOLDERS`/`MO_EXCLUDE_FOLDERS` (JSON;
  same model: an excluded folder loses only its own files). It is offered only on the page
  (`page_address()`), since its page leads back there. keepbest ignores SIGINT/SIGHUP meanwhile
  (they reach the child directly) and passes SIGTERM on; the handlers are set before the start, as
  Python handlers rather than SIG_IGN so the child gets default handling. Ctrl+C in the terminal
  ends keepbest once the child is gone (`stopped_from_terminal`, not the exit code: a run stopped
  on media-optimizer's page ends with 130 too). The results file (its `results.py`, format `v` 1)
  is read by `read_results` and turned by `summary` into tiles, a line per stage and the space both
  freed (`freed` from the run's `Stats.space_saved` through `end_menu`); the tool event carries it
  as `results`. `run_optimizer` starts with `OPTIMIZER_BANNER`, which the page makes a step of its
  own (`stepOf`). The pipe is not read to its end once the child exited (a stray grandchild can't
  block).
- **The page** (everything but `--auto`; `--web` is accepted and does nothing, `--terminal` is an
  error; without `DISPLAY`/`WAYLAND_DISPLAY` only the address is printed):
  - `serve(flow)` runs the flow in the **main thread** (`handoff.py`'s `signal.signal()` needs it);
    the server and a watcher run in threads. Tests that reach `handoff` on the page go through the
    real CLI (`cli_page` in `tests/test_web.py`).
  - A closed page stops the flow at its next question with `web.Cancelled` (a `KeyboardInterrupt`),
    so the Ctrl+C handling saves progress. Work in progress is never cut off. Ctrl+C/SIGTERM in the
    terminal ends keepbest after `INTERRUPT_GRACE`, not when the tab closes.
  - Answers are screen keys, checked against `screen_keys()` (ranges written out). Choosing a
    start-screen option directly (`{"set": [key, index]}`) presses the row's key until it shows.
  - Files are only reachable by the random ids `WebFront.file_id` / `preview_id` hand out:
    previews, `/api/media` (videos, Range), `/api/file` (the original where the browser can show
    it, else rendered at full size; own CSP), `/api/reveal` (FileManager1 ShowItems over gdbus, else
    xdg-open of the folder). A file's id is only given while it is on disk; the log's tables of a
    real run give it to the kept file only (`dry` in the show call).
  - Previews come from `image_utils.preview_jpeg` (the comparison's `_normalize`: upright,
    16-bit/CMYK/JXL safe). Each grouped image carries `beaten` (`deciding_criterion`, for the "why"
    line and bold cells) and `spot` (`difference_spot`: the worst 8×8 cell relative to #0, as
    fractions of the upright picture; saved in sessions). `regroup_around` and `_check_decided`
    move `spot` along with the scores.
  - media-optimizer's page: the tool event with `handoff` gets the `url` read from its output
    (`_HANDOFF`, only 127.0.0.1/localhost); the page goes there once (sessionStorage), and while it
    is away (`WebFront.away`, plus `HANDOFF_GRACE` after the child ended) keepbest's page doesn't
    count as gone.
  - **Updates** (`selfupdate.py`): the newest `x.y.z` tag by `git ls-remote` (the repo is private;
    git has pipx's access, never prompts), at every start in a thread (`serve(check_updates=…)`,
    `--auto`'s `cli._UpdateCheck`), the newest found kept in `state_dir()/update_check.json` for an
    offline start; `KEEPBEST_UPDATE_REPO` replaces the repo. `/api/update` installs only at
    `UPDATE_SCREENS` or the folder question (a new version under a running one would mix lazy
    imports and `static/`). Success: `Restart` (a `Cancelled`) stops the flow, serve `exec`s
    `restart_argv()` with `KEEPBEST_RESTART_PAGE` = "port token", so the new process keeps the pid
    (picktag waits on it) and the tab's address; the page reloads when `boot` changes. pipx:
    `upgrade` for an install from the repo, `install --force` with the new tag plus `inject` again
    for a pinned one (`...keepbest.git@X.Y.Z`), nothing for `pipx pin`. `--auto` prints the command.
  - **`--groups FILE`** (picktag's *Find Duplicates*): `app.load_groups` (format `v` 1, absolute
    paths, `from`), `run_groups` instead of the folder flow (target = `groups_folder`, the files'
    common folder, for names and the lock; never counted with `FolderIndex`, it can be `/`; no
    sessions; start screen without `c`/`r`, `StartScreen.groups`). `find_groups(hints=…)`: candidate
    pairs are the pairs within a group (`hint_pairs`), no hash filter, the aspect check stays.
    Pairs are first checked at `COARSE_LOAD` (32 px) averaged to 16² (`image_utils.coarse`,
    `coarse_apart`: averaging only lowers a cell's difference; margin ×1.25 + 3); only pictures
    with a pair left are loaded at 512. `FolderIndex.fix(target, files)` makes the folder tree list
    only the groups' files; `settings["excluded"]` filters `group_files`, remembered in
    `groups_excluded.json`. Marks: `IgnoreList(Path("/"), file=state_dir()/GROUPS_IGNORE_FILE)`.

## Commands

```bash
python3 -m pytest -q                  # 388 tests, about 2 minutes
python3 -m pytest -q -k video         # a subset
python3 -m pyflakes keepbest tests run.py
./run.py ~/Pictures --dry-run         # run from the checkout
```

The Python running the tests needs Pillow, send2trash and pytest; without them the CLI tests fail
with misleading assertions like `(0, 0) == (1, 0)` (the program exits "Missing required
packages"). The tests use their own HOME, so a user site isn't found. Ways that work: a venv with
`pip install -e ".[dev]"` (`.[dev,jxl]` for the JXL tests), or `pipx inject keepbest pytest` and
`"$(pipx environment --value PIPX_LOCAL_VENVS)/keepbest/bin/python" -m pytest`, or the system
Python with the pipx venv's site-packages on `PYTHONPATH`.

Skips: video tests without ffmpeg (H.264 ones also with an ffmpeg without libx264, like Fedora's
`ffmpeg-free`), two JXL tests without `pillow-jxl-plugin`, and the check of `MO_EXCLUDE_FOLDERS`
against media-optimizer's parser unless `media_optimizer` is importable.

## Rules that must not break

- Files go to the trash by default; nothing is deleted without being compared against the file
  that is **kept** (stage 3 compares each member with the keeper, never via chains).
- Enter keeps the recommended file; in `permanent` mode deletions in interactive screens ask
  `[y/N]` (default No), and `confirm_permanent` asks once before an interactive run starts.
  Refused in every mode (`is_protected`, one shared message): the home folder, every folder above
  it, the root folder and `SYSTEM_FOLDERS`; compared with symlinks resolved (`/home -> /var/home`
  on Fedora Atomic).
- `--auto` deletes permanently only with `--permanent` on the command line; `delete_mode`
  `permanent` from the config alone becomes `trash` there, with a warning (`cli.main`): `--auto`
  decides borderline groups without asking.
- "Not duplicates" marks are saved in dry runs too (the owner's choice: a mark is a judgement
  about the pictures, not a change to them; the README says so).
- Choosing another keeper (`regroup_around`) compares every other image with the chosen one
  afresh; images above the limit stay on disk and leave the group. Declining the permanent-mode
  `[y/N]` after that restores the group as found.
- Files with several pictures (MPO, multi-page TIFF; `frames` > 1; animations are GIF/PNG/WebP/
  AVIF/JXL only) are compared by their first picture but never removed: `find_groups` never takes
  them into another anchor's group, `regroup_around` leaves them out.
- "Not duplicates" marks separate pairs, not groups: `find_groups` keeps a member marked against
  the anchor out of its group (it is compared with the rest again); `i0` marks #0 against the
  others and regroups them around the next one.
- Dry runs can undo a decided group (`u`, `undo_decision`) or change it at once: a file's number or
  `s` on the handled screen returns `("change", answer)`, the review loop undoes and hands the
  answer to `_decision_screen` as `preset`. `_resolve` stores the group before the decision
  (`Group.undo`, saved in the session) and what it added to the totals; undo restores both. Real
  runs store no undo data.
- Before resuming a session or carrying out a dry run, every file is checked against the disk
  (size + mtime); changed or missing files are left alone.
- `--dry-run` on the command line always wins (`config["_cli_dry_run"]`): nothing is deleted or
  renamed in that run; the start screen locks the delete mode, doesn't resume a saved real session
  or carry out a saved dry run, and no carry-out is offered after the dry run; `run_scan` raises if
  a real mode slips through. A dry run chosen with setting 4 may be carried out right after.
- A saved dry run is never carried out by Enter: on the start screen Enter opens it (`open_saved`:
  `run_scan(resume=…, open_overview=True)` as a dry run), `a` carries it out; right after a dry run
  Enter carries it out and `o` opens it. `carry_out_dry_run` always asks `[y/N]` first, in every
  mode. There is no "review again for real" (it trashed files with each decision).
- Carrying out a dry run maps Stage 1 renames into the later remux and visual groups; in the dry
  run, `ctx.planned` (renames a real run would make) ranks Stage 2/3 like a real run and counts
  each rename once; with `ctx.dropping` a name an earlier group frees counts as free and one an
  earlier rename takes as taken (`_name_free`). `replay_dry_run` rebuilds both on resume and for
  `planned_changes`; `undo_decision` takes the group's share out again.
- Stage 2 hashes every other track by content too (`stream_hashes`): further video streams,
  audio, subtitles and data; AAC normalised with `aac_adtstoasc`, so MPEG-TS remuxes match. Only a
  crash (not an interrupt: 255 or SIGINT/SIGTERM/SIGHUP, `video._INTERRUPTED`) lets the track-less
  second pass run. A file without a duration in its header gets one from its packets
  (`_measured_duration`). Automatic only when all tracks are identical and in the same order; a
  copy containing every track of the others in their order (`covering_copy`) is recommended; the
  same tracks in another order (`tracks_reordered`) are asked about. Without questions a group is
  only resolved when a covering copy exists. Skipped remux groups stay in `exact_groups`
  (`ExactGroup.status`) and are left alone when the dry run is carried out.
- Interrupts during Stage 2 stop ffprobe/ffmpeg at once (`video.stop_all` via `_threaded`); a
  stopped ffmpeg is never retried.
- When nothing of a group could be removed, there is no rename and (real runs) the group stays
  open.
- A dry run resumes as a dry run; settings that decided a session's groups (stages, looser
  strictness, videos, subfolders) are locked on the start screen; stricter strictness works without
  a rescan (`tighten_groups`).
- The main folder is only excluded with its own key `m` (or `--exclude .`); numbers/ranges/a/n in
  the folder list change subfolders only; each listed folder switches on its own
  (`switch_folders`); subfolders that would change along are only switched after a `[Y/n]`
  question (`subfolders_along`).
- Copy numbers "(n)" are only removed from a kept file when another file of the group shares the
  base name (`Holiday (12).jpg` stays); never overwrite an existing name.
- Keeper ranking: resolution → lossless over lossy/unknown → `format_ranks` → no copy number → bpp
  → oldest. JPEG XL is "unknown" (Pillow can't tell lossy from lossless), so a lossless original
  wins; `prefer_jxl: true` ranks JXL like lossless (`lossless_rank`). TIFF is lossy only when
  JPEG/WebP/SGILog-compressed.
- Greyscale with more than 8 bits (I;16, I, F) is never scaled per file for the pixel comparison:
  `comparison_thumbnail` keeps the stored values and `pixel_difference` brings both images of a
  pair to one scale (8-bit × 257) and stretches them together by the brighter one's maximum, so a
  dark 16-bit picture matches its 8-bit copy and a 2×/4× brighter version doesn't. `_to_8_bit` is
  only for the dhash. Non-finite float values are an error for that file.
- Stage 3 settles clusters in threads (`find_groups.settle`; anchors with 8+ candidates in a
  second pool); results must not depend on timing (groups sorted by keeper path). `stop` ends every
  thread at its next comparison after an error or Ctrl+C. `image_utils.Blurred` keeps up to 32
  blurred thumbnails per cluster, only for pictures compared at their own size.
- **The file journal is a contract with picktag** (and media-optimizer writes the same format).
  `journal.py` appends one JSON line per change to `journal.jsonl` in `state_dir()` (also from a
  checkout; config `journal_file`). Changing the meaning of a field or event needs `"v": 2` and a
  matching picktag change; adding fields is fine. An event only after the action succeeded, none
  for failures or dry runs (not even `run_start`); stat before removing (`Journal.file_state`);
  `ensure_ascii=True` (non-UTF-8 names as `\udcXX`); append with `flock`, never truncate; a journal
  error only warns once. Events: `run_start`/`run_end` around each `run_scan` of a real run (one
  `run` id per `run_scan`; `code` 0/1, 130 when interrupted), `removed` in `execute_deletion`
  (`match` exact/remux/visual, `hard_link`), `renamed` in `rename_kept` (`copy_number`). Those two
  functions are the only places that change media files; a new one needs an event too. A `kept`
  file may be renamed or removed by a later event of the same run, so consumers replay in order.
- Don't change the package folder name, the default delete mode (trash) or Enter's meaning without
  asking.

## Conventions

- **Output and questions go through `front.py`**, never `print`/`input`. `app.py` and
  `core_logic.py` call `front.info/warn/…`, `front.show(kind, …)` for structured output and
  `front.ask(screen)` with a `Screen` dataclass carrying its data, `primary` (what Enter does) and
  `menu` (the other keys). The answer is a key: the page's buttons send them, and they are its
  keyboard shortcuts. Only the page asks (`web.WebFront`); `terminal.py` shows messages only
  (`--auto`, and the log of a page run, `WebFront.echo`) and raises `NeedsPage` if asked anything.
  `cli.py` prints its startup errors with `ui` directly.
- A new screen needs a dataclass in `front.py`, data in `web.screen_data` and a view in `app.js`
  (screens without one get a generic view: table plus key buttons); a new message kind needs an
  entry in `terminal._MESSAGES` and `web.message_data`. Wording is for the page: no "press x".
- The page: key badges only for Enter and the numbers; other keys are a bold letter in the label
  (`Label`) and a hover title; a menu item with the primary's label is not shown twice. Pictures
  (and the remux player) are off until switched on, then on for the page's lifetime. Forms (start
  screen, folder tree) stay shown, dimmed, after a change until their next state arrives, so
  nothing flickers. View buttons blur after a click, so Enter always reaches the flow.
- The look is libadwaita's, shared with lyrembed, cdport and media-optimizer: `static/base.css`
  and `static/icons.js` are lyrembed's, unchanged, like `static/vendor/` (licences in
  `LICENSES.txt`); compare them with `diff`. Only the green accent and what only this page has live
  in `style.css`. The header bar: program (with the folder under it), steps in the middle (icons,
  no numbers), actions. `icon.svg` is unreadable below about 64 px: header and browser tab use
  `logo.svg`, the full icon appears only at 112 px on status pages. What shows after the server
  stopped is in the page from the start (`hidden`). The page follows the system's light or dark
  style, but pictures stay on `--picture-bg`. The film-strip icon isn't in the shared set
  (`StepIcon` in `app.js`).
- The terminal output (`--auto`, the log of a page run) uses `ui.py`: plain ANSI via `StyleUI`,
  banners, `[BADGE]`s, aligned tables with a `▶` marker for the kept file, transient status lines;
  `--color`/`NO_COLOR` respected.
- Config: new keys go into `DEFAULT_CONFIG`, get validated in `config.py` (types/ranges → warning +
  default), and appear in `config.example.json` (regenerate from `DEFAULT_CONFIG`) and the README.
  `REMOVED_CHOICES` turns values that no longer exist (viewer `kitty`, `timg`) into a warning.
- README: every option and config key documented once; keep it in sync with behaviour (reviews
  found mismatches before).
- Stages and terms: 1 identical files (SHA-256, same-size files only) · 2 remuxed videos (ffprobe
  preselects, then a container-independent hash of the video stream; H.264/HEVC "picture data
  only" via bitstream filters) · 3 visually identical images (128-bit dhash candidates, then the
  worst 8×8-cell pixel difference at ≤ 512 px; presets strict 12 / normal 20 / loose 28;
  borderline above `uncertain_ratio` × limit, default 0.8). `--stages` takes numbers (`1,2`, `3`,
  `all`; `both` = all).

## Testing

- `tests/conftest.py`: autouse isolation (own HOME, XDG folders, trash and `KEEPBEST_STATE_DIR`,
  no container (`not_in_a_container`; the `toolbox` fixture makes one),
  `KEEPBEST_UPDATE_REPO` pointing at a missing repo; `releases` makes one). `library` builds
  picture folders (`photo`, `variant`, `copy_of`). `run_cli(folder, *args, keys=[...])` runs the
  real CLI with `--no-browser` and answers its page over the API, one key per question (a screen
  key, "" for Enter, y/n, a path); a key the question doesn't offer is recorded in `.typos` and
  skipped; keys running out close the page (exit 130). The result has the terminal log as
  `stdout` and `.questions`/`.events` from the page. Helpers: `asked(result, "StartScreen")`,
  `shown(result)`, `Page` (the API as the page's script uses it), `summary(result)` (final numbers
  from the log), `fake_command` (stand-in programs on PATH; `.calls(count=…)` waits for background
  calls), `make_video` / `remux` (ffmpeg).
- `tests/pictures.py`: deterministic photo-like pictures. Calibrated: re-encodes/resizes score
  ≤ 16, edits (`with_text`, `with_patch`, `brighter`) ≥ 32, limit 20.
- `KEEPBEST_STATE_DIR` redirects log and sessions (otherwise a checkout writes them into the
  project folder). Never let tests touch the real project folder, config or trash.
- **Checking that the terminal output didn't change** (refactoring): `KEEPBEST_RECORD=DIR` makes
  `run_cli` save every run's output there (paths, the page's address and saved-session times
  normalised); `KEEPBEST_RECORD_COLOR=always` for a pass with colours. Record the suite on the old
  code (a `git worktree` of the last commit with the current `conftest.py` copied in) and on the
  new one, then `diff -r`. The colour pass fails about 10 tests that read plain text, on both sides
  alike. Runs are deterministic.
- **The page** is covered by API tests (`tests/test_web.py`, server in the test process, no
  browser). Clicking through it is a manual check: Playwright in a venv outside the repo, keepbest
  started with `--no-browser`, generated pictures only: both `color_scheme`s, and 390 px wide with
  `scrollWidth` compared to `innerWidth`. The page's CSP blocks `wait_for_function`; wait with
  locators. A full-page screenshot shows the sticky header where the page was scrolled to.

## Releasing

The version lives only in `__version__` in `keepbest/config.py` (`pyproject.toml` reads it). The
steps are the family's (see Versions and releases above): `## [Unreleased]` in `CHANGELOG.md`
becomes the version, the full suite passes, one commit `keepbest X.Y.Z: …`, `git tag -a X.Y.Z -m
"keepbest X.Y.Z"`, push branch and tag, `gh release create X.Y.Z --title "keepbest X.Y.Z"` with
that version's CHANGELOG section as notes. Without a version raise, `pipx upgrade` and the update
check don't see the change.

## Gotchas

- Printing a file name that isn't valid UTF-8 crashes on Python before 3.15 (no UTF-8 mode:
  stdout is `strict`; check with `PYTHONUTF8=0 LC_ALL=en_US.UTF-8`), so
  `test_a_name_that_is_not_utf_8_round_trips` sets `PYTHONIOENCODING`.
- Fedora's `ffmpeg-free` lacks some encoders (H.264), so those video tests skip there. Remux
  detection itself needs no decoders.
- Pillow's "Truncated File Read" warning for damaged EXIF names no file.
- MPEG-4 Part 2 inside MPEG-TS can't be read by ffmpeg (such files are left alone).

## Open items

- **Small fixes:** don't group 0-byte files; warn on `--exclude` without `-r`; a friendlier
  goodbye than "Program terminated."; summary label "Visually identical images" (adjust
  `_SUMMARY_FIELDS` in `tests/conftest.py`); pytest should stop early with a clear message naming
  `sys.executable` when Pillow/send2trash are missing; "Missing required packages" should name the
  Python used; `--auto` should print excluded folders.
- **Performance:** with defaults the candidate search is all-pairs (128 bits / 29 chunks = 4 bits;
  about 90 s for 50,000 images) – numpy XOR+popcount or a BK-tree. All candidate thumbnails are
  held in memory – process clusters in batches of about 200.
- **Possible features (the owner decides):** E1 folder priority (`--prefer DIR` / reference
  folders never deleted from – rated most valuable); E2 HEIC/HEIF and RAW (extensions for stage 1,
  optional `pillow-heif` for stage 3); E3 marks tied to content instead of relative paths; E4 list
  files with damaged EXIF; E5 log rotation and cleanup of old sessions; E6 a README `curl` command
  to fetch `config.example.json` for pipx users.
