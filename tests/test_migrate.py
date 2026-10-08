"""Moving the files kept under the old name img_dedupe to keepbest."""

import json
from pathlib import Path

import pytest

from conftest import summary
from keepbest import config as config_mod
from keepbest import migrate as migrate_mod
from keepbest.config import load_config
from keepbest.migrate import migrate_old_locations
from keepbest.session import IgnoreList, state_dir


@pytest.fixture
def home(isolated, monkeypatch):
    """The test's home folder, without a project folder and without the state override."""
    monkeypatch.setattr(config_mod, "project_dir", lambda: None)
    monkeypatch.setattr(migrate_mod, "project_dir", lambda: None)
    monkeypatch.delenv("KEEPBEST_STATE_DIR")
    return isolated / "home"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_the_old_config_folder_is_moved_and_used(home):
    write(home / ".config" / "img_dedupe" / "config.json", '{"strictness": "strict"}')
    moved, warnings = migrate_old_locations()
    assert warnings == []
    assert moved == [f"img_dedupe is now called keepbest: moved your config from "
                     f"{home / '.config' / 'img_dedupe'} to {home / '.config' / 'keepbest'}."]
    assert not (home / ".config" / "img_dedupe").exists()
    config, _ = load_config()
    assert config["strictness"] == "strict"
    assert config["_source"] == str(home / ".config" / "keepbest" / "config.json")


def test_the_old_log_and_sessions_are_moved(home):
    old = home / ".local" / "state" / "img_dedupe"
    write(old / "img_dedupe.log", "earlier entries\n")
    write(old / "sessions" / "abc.json", "{}")
    moved, _ = migrate_old_locations()
    new = home / ".local" / "state" / "keepbest"
    assert state_dir() == new
    assert (new / "keepbest.log").read_text() == "earlier entries\n"
    assert (new / "sessions" / "abc.json").exists()
    assert not old.exists() and not (new / "img_dedupe.log").exists()
    assert moved == [f"img_dedupe is now called keepbest: moved your log and saved sessions from {old} to {new}."]


def test_nothing_is_moved_when_the_new_folders_exist(home):
    write(home / ".config" / "img_dedupe" / "config.json", '{"strictness": "strict"}')
    write(home / ".config" / "keepbest" / "config.json", '{"strictness": "loose"}')
    assert migrate_old_locations() == ([], [])
    assert (home / ".config" / "img_dedupe" / "config.json").exists()
    assert load_config()[0]["strictness"] == "loose"


def test_a_failed_move_is_reported_and_the_old_config_still_read(home):
    write(home / ".config" / "img_dedupe" / "config.json", '{"strictness": "strict"}')
    parent = home / ".config"
    parent.chmod(0o500)                                      # the folder can't be renamed
    try:
        moved, warnings = migrate_old_locations()
        config, _ = load_config()
    finally:
        parent.chmod(0o700)
    assert moved == [] and "Could not move your config" in warnings[0]
    assert config["strictness"] == "strict"


def test_old_marks_are_read_and_replaced_by_the_new_file(library):
    a, b, c = (library.photo(f"{n}.png", i) for i, n in enumerate("abc", 1))
    meta = {p: {"path": p, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in (a, b, c)}
    IgnoreList(library.path).add(meta[a], meta[b])
    (library / ".keepbest_ignore.json").rename(library / ".img_dedupe_ignore.json")   # as before 1.0.0
    marks = IgnoreList(library.path)
    assert marks.contains(meta[a], meta[b])                  # read from the old file
    marks.add(meta[a], meta[c])
    assert not (library / ".img_dedupe_ignore.json").exists()
    data = json.loads((library / ".keepbest_ignore.json").read_text())
    assert len(data["pairs"]) == 2                           # the old mark is kept


def test_the_old_state_variable_still_works(home, monkeypatch, tmp_path):
    monkeypatch.setenv("IMG_DEDUPE_STATE_DIR", str(tmp_path / "old-variable"))
    assert state_dir() == tmp_path / "old-variable"


def test_the_move_is_announced_when_keepbest_starts(isolated, library, run_cli):
    write(isolated / "home" / ".config" / "img_dedupe" / "config.json", "{}")
    library.photo("a.png", 1)
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "img_dedupe is now called keepbest: moved your config" in result.stdout
    assert (isolated / "home" / ".config" / "keepbest" / "config.json").exists()
    assert summary(result)["scanned"] == 1
