"""The file journal (journal.jsonl): what other programs, such as picktag, read."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

import pictures
from conftest import REPO, Library, make_video, remux, requires_ffmpeg, summary


@pytest.fixture
def journal(tmp_path):
    """The journal's default place (KEEPBEST_STATE_DIR in the tests); read() parses it."""
    path = tmp_path / "state" / "journal.jsonl"

    def read(at=path):
        if not at.exists():
            return []
        text = at.read_text(encoding="ascii")
        assert text.endswith("\n")
        return [json.loads(line) for line in text.splitlines()]
    read.path = path
    return read


def changes(records):
    return [r for r in records if r["event"] not in ("run_start", "run_end")]


def test_a_run_is_framed_by_run_start_and_run_end(library, run_cli, journal):
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    run_cli(library.path, "--auto")
    records = journal()
    assert [r["event"] for r in records] == ["run_start", "removed", "run_end"]
    start, end = records[0], records[-1]
    assert start["source"] == str(library.path.resolve()) and start["target"] is None
    assert isinstance(start["pid"], int)
    assert end["code"] == 0 and end["interrupted"] is False
    for record in records:
        assert record["v"] == 1 and record["tool"] == "keepbest" and record["run"] == start["run"]
        assert record["tool_version"] and "T" in record["ts"]


def test_an_identical_copy_is_journalled_with_its_keeper(library, run_cli, journal):
    library.photo("a.png", 1)
    copy = library.copy_of("a.png", "b.png")
    before = copy.stat()
    run_cli(library.path, "--auto")
    [removed] = changes(journal())
    assert removed == {**removed, "event": "removed", "path": str(copy), "kept": str(library / "a.png"),
                       "size": before.st_size, "mtime_ns": before.st_mtime_ns, "how": "trash",
                       "reason": "duplicate", "match": "exact"}
    assert "hard_link" not in removed


def test_a_hard_link_has_its_real_size(library, run_cli, journal):
    library.photo("a.png", 1)
    os.link(library / "a.png", library / "b.png")
    run_cli(library.path, "--auto")
    [removed] = changes(journal())
    assert removed["hard_link"] is True and removed["size"] == (library / "a.png").stat().st_size > 0


def test_permanent_removals_say_so(library, run_cli, config_file, journal):
    config_file({"delete_mode": "permanent"})
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    run_cli(library.path, "--auto")
    assert [r["how"] for r in changes(journal())] == ["permanent"]


@requires_ffmpeg
def test_a_remux_is_journalled(library, run_cli, journal):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.mkv")
    run_cli(library.path, "--auto")
    [removed] = changes(journal())
    assert removed["match"] == "remux" and {removed["path"], removed["kept"]} == \
        {str(library / "clip.mp4"), str(library / "clip.mkv")}


def test_a_visual_duplicate_is_journalled(library, run_cli, journal):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "small.jpg", change=pictures.downscaled)
    run_cli(library.path, "--auto")
    [removed] = changes(journal())
    assert removed["match"] == "visual"
    assert (removed["path"], removed["kept"]) == (str(library / "small.jpg"), str(library / "a.png"))


def test_a_copy_number_rename_is_journalled(library, run_cli, journal):
    library.photo("photo (1).png", 1)
    library.copy_of("photo (1).png", "photo (2).png")
    run_cli(library.path, "--auto")
    removed, renamed = changes(journal())
    assert (removed["path"], removed["kept"]) == (str(library / "photo (2).png"), str(library / "photo (1).png"))
    after = (library / "photo.png").stat()
    assert renamed == {**renamed, "event": "renamed", "src": str(library / "photo (1).png"),
                       "dest": str(library / "photo.png"), "size": after.st_size,
                       "mtime_ns": after.st_mtime_ns, "reason": "copy_number"}


def final_name(records, index, path):
    """Where *path* ends up: later events of the run may rename or remove it again
    (a Stage 1 keeper can lose to a larger picture in Stage 3)."""
    for record in records[index + 1:]:
        if record["event"] == "renamed" and record["src"] == path:
            path = record["dest"]
        elif record["event"] == "removed" and record["path"] == path:
            path = record["kept"]
    return path


def test_kept_always_leads_to_a_file_that_exists(library, run_cli, journal):
    library.photo("big.png", 1, size=(640, 480))
    library.variant("big.png", "small.png", change=pictures.downscaled)
    library.copy_of("small.png", "small copy.png")              # Stage 1 keeps small.png, Stage 3 removes it
    library.photo("x (1).png", 2)
    library.variant("x (1).png", "x.jpg")                       # x.jpg is removed, then x (1).png renamed to x.png
    run_cli(library.path, "--auto")
    records = journal()
    assert summary_of(records) == {"removed": 3, "renamed": 1}
    for i, record in enumerate(records):
        if record["event"] == "removed":
            assert os.path.exists(final_name(records, i, record["kept"]))
            assert not os.path.exists(record["path"])


def summary_of(records):
    events = [r["event"] for r in changes(records)]
    return {name: events.count(name) for name in set(events)}


def test_every_line_is_json(library, run_cli, journal):
    library.photo("a (1).png", 1)
    library.copy_of("a (1).png", "a (2).png")
    library.photo("b.png", 2, size=(640, 480))
    library.variant("b.png", "b.jpg", change=pictures.downscaled)
    run_cli(library.path, "--auto")
    run_cli(library.path, "--auto")                             # nothing left: start and end only
    lines = journal.path.read_bytes().split(b"\n")
    assert lines[-1] == b"" and all(json.loads(line) for line in lines[:-1])
    assert len({json.loads(line)["run"] for line in lines[:-1]}) == 2


@pytest.mark.parametrize("args, keys", [(("--dry-run", "--auto"), ()),
                                        ((), ["4", "", "q"])],          # a dry run chosen on the start screen
                         ids=["--dry-run", "setting"])
def test_dry_runs_write_nothing(library, run_cli, journal, args, keys):
    library.photo("a (1).png", 1)
    library.copy_of("a (1).png", "a (2).png")
    result = run_cli(library.path, *args, keys=keys)
    assert summary(result)["exact"] == 1 and len(library.files()) == 2
    assert not journal.path.exists()


def test_journal_file_false_writes_nothing(library, run_cli, config_file, journal, tmp_path):
    config_file({"journal_file": False})
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    run_cli(library.path, "--auto")
    assert library.files() == ["a.png"] and not journal.path.exists()
    assert "TRASHED" in (tmp_path / "state" / "keepbest.log").read_text()   # the log is still written


def test_journal_file_can_name_another_file(library, run_cli, config_file, journal, tmp_path):
    config_file({"journal_file": str(tmp_path / "elsewhere" / "j.jsonl")})
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    run_cli(library.path, "--auto")
    assert not journal.path.exists()
    assert len(changes(journal(tmp_path / "elsewhere" / "j.jsonl"))) == 1


def test_a_journal_that_cant_be_written_changes_nothing(run_cli, config_file, tmp_path):
    def run(folder, journal_file):
        config_file({"journal_file": journal_file})
        library = Library(tmp_path / folder)
        library.photo("a (1).png", 1)
        library.copy_of("a (1).png", "a (2).png")
        library.photo("b.png", 2)
        library.copy_of("b.png", "c.png")
        return library, run_cli(library.path, "--auto")
    (tmp_path / "a folder").mkdir()
    good, ok = run("good", "j.jsonl")
    bad, broken = run("bad", str(tmp_path / "a folder"))        # a folder can't be appended to
    assert broken.returncode == ok.returncode == 0
    assert broken.stdout.replace(str(bad.path), "") == ok.stdout.replace(str(good.path), "")
    assert bad.files() == good.files() == ["a.png", "b.png"]
    assert broken.stderr.count("Could not write the journal file") == 1


def without_run_details(records, folder):
    """The events as they would compare between two runs on copies of a folder."""
    def strip(value):
        return value.replace(str(folder), "<folder>") if isinstance(value, str) else value
    return [{k: strip(v) for k, v in r.items() if k not in ("run", "ts", "pid")} for r in records]


@pytest.mark.parametrize("carry_out", [[""], ["e"]], ids=["apply", "review-again"])
def test_carrying_out_a_dry_run_journals_like_a_real_run(library, run_cli, journal, tmp_path, carry_out):
    library.photo("photo (1).png", 1)
    library.copy_of("photo (1).png", "photo (2).png")
    library.photo("big.png", 2, size=(640, 480))
    library.variant("big.png", "small.png", change=pictures.downscaled)
    real = tmp_path / "real"
    shutil.copytree(library.path, real, copy_function=shutil.copy2)
    run_cli(real, "--auto")
    direct = without_run_details(journal(), real)
    journal.path.unlink()

    run_cli(library.path, "--dry-run", keys=["", ""])           # dry run, kept to carry out
    assert not journal.path.exists()
    result = run_cli(library.path, keys=[*carry_out, "", ""])
    assert summary(result)["failed"] == 0
    carried_out = without_run_details(journal(), library.path)
    assert carried_out == direct and len(changes(carried_out)) == 3


def test_a_name_that_is_not_utf_8_round_trips(library, run_cli, journal):
    library.photo("a.png", 1)
    name = os.fsencode(library.path) + b"/b\xff.png"
    try:
        shutil.copy2(library / "a.png", name)
    except OSError:
        pytest.skip("the file system doesn't take names that aren't UTF-8")
    # Output with escapes: without UTF-8 mode (the default before Python 3.15), printing
    # such a name fails - a separate, known limitation.
    result = run_cli(library.path, "--auto", env={"PYTHONIOENCODING": "utf-8:backslashreplace"})
    assert result.returncode == 0, result.stdout + result.stderr
    [removed] = changes(journal())
    assert os.fsencode(removed["path"]) == name and not os.path.exists(name)
    assert b"b\xff.png" in (journal.path.parent / "keepbest.log").read_bytes()  # the log keeps the bytes


def test_an_interrupted_run_ends_with_interrupted(library, journal, config_file):
    """SIGTERM while a group waits for a decision: the journal still says the run ended."""
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "b.jpg", change=pictures.downscaled)
    argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--color", "never", "--terminal",
            str(library.path), "-i"]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=REPO)
    try:
        proc.stdin.write(b"\n")                                  # start; then the decision screen waits
        proc.stdin.flush()
        deadline = time.monotonic() + 60
        while not journal.path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
    finally:
        proc.kill()
    records = journal()
    assert [r["event"] for r in records] == ["run_start", "run_end"]
    assert records[-1]["interrupted"] is True and records[-1]["code"] == 130
    assert sorted(p.name for p in library.path.iterdir()) == ["a.png", "b.jpg"]
