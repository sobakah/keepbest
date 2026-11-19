"""--groups: files another program (picktag) found together, compared only within their group."""

import json
import os
from pathlib import Path

import pytest

import pictures
from conftest import asked, shown, summary
from keepbest import app


def groups_file(tmp_path, *groups, version=1, name="groups.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"v": version, "from": "picktag",
                                "groups": [[str(p) for p in group] for group in groups]}))
    return path


@pytest.fixture
def pairs(library):
    """a.png with a re-encoded copy, b.png with a smaller copy, in different folders."""
    library.photo("one/a.png", 1)
    library.variant("one/a.png", "two/a.jpg", quality=85)
    library.photo("one/b.png", 2)
    library.variant("one/b.png", "two/b small.jpg", pictures.downscaled)
    return library


def test_only_files_in_the_same_group_are_compared(pairs, run_cli, tmp_path):
    lib = pairs
    # b's copy is in a group with a.png's: they differ, so it stays
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"], [lib / "one/b.png", lib / "one/a.png"],
                         [lib / "two/b small.jpg", lib / "two/a.jpg"])
    result = run_cli("--groups", groups, "--auto")
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary(result)["visual"] == 1
    assert lib.files() == ["one/a.png", "one/b.png", "two/b small.jpg"]
    assert "3 groups from picktag" in result.stdout


def test_every_pair_of_a_group_is_compared_without_the_hash_filter(pairs, run_cli, config_file, tmp_path):
    lib = pairs
    config_file({"hash_max_distance": 0})       # the hash lets only hash-identical pairs through
    assert summary(run_cli(lib.path, "-r", "--auto", "--dry-run"))["visual"] < 2
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"], [lib / "one/b.png", lib / "two/b small.jpg"])
    assert summary(run_cli("--groups", groups, "--auto", "--dry-run"))["visual"] == 2


def test_the_pixels_still_decide(library, run_cli, tmp_path):
    library.photo("a.png", 1)
    library.variant("a.png", "a text.jpg", pictures.with_text, quality=90)
    library.photo("b.png", 2)
    groups = groups_file(tmp_path, [library / "a.png", library / "a text.jpg", library / "b.png"])
    result = run_cli("--groups", groups, "--auto")
    assert summary(result)["visual"] == 0
    assert library.files() == ["a text.jpg", "a.png", "b.png"]


def test_identical_files_are_found_in_a_group(library, run_cli, tmp_path):
    library.photo("a.png", 1)
    library.copy_of("a.png", "elsewhere/a.png")
    groups = groups_file(tmp_path, [library / "a.png", library / "elsewhere/a.png"])
    assert summary(run_cli("--groups", groups, "--auto"))["exact"] == 1
    assert library.files() == ["a.png"]


def test_files_left_out_of_the_groups(library, tmp_path):
    a = library.photo("a.png", 1)
    b = library.variant("a.png", "b.jpg", quality=85)
    library.photo(".hidden.png", 3)
    (library / "notes.txt").write_text("x")
    os.symlink(a, library / "link.png")
    groups, sender = app.load_groups(groups_file(
        tmp_path, [a, b, library / ".hidden.png", library / "notes.txt", library / "link.png", library / "gone.png",
                   "relative.png", a],
        [a, library / "gone.png"],      # one file left: no group
        [b]))
    assert sender == "picktag"
    assert groups == [[a, b]]
    assert app.groups_folder(groups) == library.path


@pytest.mark.parametrize("content, message", [
    ("not json", "can't read"),
    ('{"v": 2, "groups": []}', "format version 2"),
    ('{"v": 1}', "no list of groups"),
])
def test_an_unusable_groups_file_is_an_error(run_cli, tmp_path, content, message):
    path = tmp_path / "groups.json"
    path.write_text(content)
    result = run_cli("--groups", path, "--auto")
    assert result.returncode == 2
    assert message in result.stdout + result.stderr


def test_groups_take_no_folder(library, run_cli, tmp_path):
    result = run_cli(library.path, "--groups", groups_file(tmp_path), "--auto")
    assert result.returncode == 2
    assert "--groups names the files itself" in result.stderr


def test_no_groups_left_is_not_an_error(library, run_cli, tmp_path):
    a = library.photo("a.png", 1)
    result = run_cli("--groups", groups_file(tmp_path, [a, library / "gone.png"]), "--auto")
    assert result.returncode == 0
    assert "nothing to compare" in result.stdout


def test_the_page_names_the_groups_and_has_no_folder_to_change(pairs, run_cli, tmp_path):
    lib = pairs
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"])
    result = run_cli("--groups", groups, "--dry-run", keys=["", "q"])
    start = asked(result, "StartScreen")[0]
    assert start["groups"] == 1 and start["groupsFrom"] == "picktag"
    assert start["target"] == str(lib.path)
    assert start["inScope"] == 2
    assert "c" not in start["keys"] and "r" not in start["keys"] and "f" in start["keys"]
    assert "Only files in the same group are compared" in shown(result)
    assert summary(result)["visual"] == 1
    assert all("c" not in q["keys"] for q in asked(result, "EndScreen"))


def test_not_duplicates_marks_go_to_the_state_folder(pairs, run_cli, isolated, tmp_path):
    lib = pairs
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"])
    run_cli("--groups", groups, "--dry-run", "-i", keys=["", "i", ""])
    marks = isolated / "state" / app.GROUPS_IGNORE_FILE
    assert marks.exists()
    assert not any(name.endswith("_ignore.json") for name in lib.files())
    assert str(lib / "one/a.png").lstrip("/") in marks.read_text()
    assert summary(run_cli("--groups", groups, "--auto", "--dry-run"))["visual"] == 0
    assert summary(run_cli("--groups", groups, "--auto", "--dry-run", "--no-ignore"))["visual"] == 1


def test_the_journal_names_the_kept_file(pairs, run_cli, isolated, tmp_path):
    lib = pairs
    run_cli("--groups", groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"]), "--auto")
    events = [json.loads(line) for line in (isolated / "state" / "journal.jsonl").read_text().splitlines()]
    removed = [e for e in events if e["event"] == "removed"]
    assert len(removed) == 1
    assert removed[0]["path"] == str(lib / "two/a.jpg") and removed[0]["kept"] == str(lib / "one/a.png")
    assert removed[0]["match"] == "visual"


@pytest.mark.parametrize("how", ["absolute", "relative"])
def test_exclude_leaves_a_folder_of_the_groups_out(pairs, run_cli, tmp_path, how):
    lib = pairs
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"], [lib / "one/b.png", lib / "two/b small.jpg"])
    folder = str(lib / "two") if how == "absolute" else "two"
    result = run_cli("--groups", groups, "--exclude", folder, "--auto")
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary(result)["visual"] == 0                    # their copies are in two/: nothing to compare
    assert "two/a.jpg" in lib.files() and "two/b small.jpg" in lib.files()


def test_folders_left_out_on_the_page_are_remembered(pairs, run_cli, isolated, tmp_path):
    lib = pairs
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg"], [lib / "one/b.png", lib / "two/b small.jpg"])
    # f: the folder tree (one/, two/), 2 switches two/ off, Enter: done; Enter starts; q at the end
    result = run_cli("--groups", groups, "--dry-run", keys=["f", "2", "", "", "q"])
    tree = asked(result, "FolderScreen")[0]
    assert [e["rel"] for e in tree["entries"]] == ["one", "two"]   # only the groups' folders
    assert summary(result)["visual"] == 0
    saved = json.loads((isolated / "state" / app.GROUPS_FOLDERS_FILE).read_text())
    assert saved["excluded"] == [str(lib / "two")]
    again = run_cli("--groups", groups, "--auto", "--dry-run")
    assert "as last time" in again.stdout and summary(again)["visual"] == 0
    # back on: forgotten
    run_cli("--groups", groups, "--dry-run", keys=["f", "2", "", "", "q", "q"])
    assert json.loads((isolated / "state" / app.GROUPS_FOLDERS_FILE).read_text())["excluded"] == []
    assert summary(run_cli("--groups", groups, "--auto", "--dry-run"))["visual"] == 2


def test_remembered_folders_elsewhere_are_kept(tmp_path, isolated):
    a, b = tmp_path / "a", tmp_path / "b"
    (b / "x").mkdir(parents=True)
    (a / "y").mkdir(parents=True)
    app.save_groups_excluded(b, {"x"}, set())
    app.save_groups_excluded(a, {"y"}, set())
    assert app.load_groups_excluded(b) == {"x"} and app.load_groups_excluded(a) == {"y"}
    app.save_groups_excluded(a, set(), {"y"})                       # switched back on below a only
    assert app.load_groups_excluded(b) == {"x"} and app.load_groups_excluded(a) == set()
    assert app.load_groups_excluded(tmp_path) == {"b/x"}


@pytest.mark.parametrize("limit", [12, 20, 28])
def test_the_pre_check_never_skips_a_copy(library, limit):
    """Copies the full comparison finds (re-encoded, smaller, another format) pass the
    pre-check at every strictness; other photos don't."""
    from keepbest import core_logic, image_utils as iu
    library.photo("a.png", 1, size=(1200, 900))
    copies = [library.variant("a.png", "a.jpg", quality=70), library.variant("a.png", "small.jpg", pictures.downscaled),
              library.variant("a.png", "a.webp", quality=80), library.variant("a.png", "re.jpg", pictures.reencoded)]
    others = [library.photo(f"o{i}.jpg", 10 + i, size=(1200, 900)) for i in range(5)]
    small = lambda p: iu.coarse(iu.comparison_thumbnail(p, core_logic.COARSE_LOAD))   # noqa: E731
    full = lambda p: iu.comparison_thumbnail(p, 512)                                   # noqa: E731
    a = small(library / "a.png")
    for c in copies:
        assert iu.pixel_difference(full(library / "a.png"), full(c)) <= 16
        assert not iu.coarse_apart(a, small(c), limit), c.name
    assert all(iu.coarse_apart(a, small(o), limit) for o in others)


def test_groups_count_only_pairs_that_pass_the_pre_check(pairs, run_cli, tmp_path):
    lib = pairs
    groups = groups_file(tmp_path, [lib / "one/a.png", lib / "two/a.jpg", lib / "one/b.png", lib / "two/b small.jpg"])
    result = run_cli("--groups", groups, "--auto", "--dry-run")
    assert "2 candidate pair(s)" in result.stdout and summary(result)["visual"] == 2


# --- review fixes ------------------------------------------------------------------
def test_files_on_drives_and_in_atomic_homes_are_not_system_files(monkeypatch):
    for path in ("/run/media/me/Drive/a.jpg", "/var/home/me/Pictures/a.jpg", "/var/mnt/data/a.jpg",
                 "/Volumes/Drive/a.jpg"):
        assert not app._in_system_folder(Path(path)), path
    for path in ("/usr/share/backgrounds/a.jpg", "/var/lib/a.jpg", "/run/user/1000/a.jpg", "/etc/a.jpg"):
        assert app._in_system_folder(Path(path)), path


def test_the_home_folder_is_not_a_system_folder_wherever_it_is(monkeypatch, tmp_path):
    # Fedora Atomic: the home folder resolves to /var/home/…, below the system folder /var.
    monkeypatch.setattr(app, "SYSTEM_FOLDERS", app.SYSTEM_FOLDERS | {str(tmp_path)})
    assert not app._in_system_folder(Path.home() / "Pictures" / "a.jpg")
    assert app._in_system_folder(tmp_path / "elsewhere" / "a.jpg")


@pytest.fixture
def linked(tmp_path):
    """~/Pictures is a symlink to the real folder (as /home -> /var/home on Fedora Atomic)."""
    real = tmp_path / "data" / "Pictures"
    (real / "old").mkdir(parents=True)
    link = tmp_path / "home" / "Pictures"
    link.symlink_to(real)
    from conftest import Library
    return Library(real), link


def test_groups_through_a_symlinked_folder_work_like_a_folder_run(linked, run_cli, tmp_path):
    lib, link = linked
    lib.photo("a.png", 1)
    lib.copy_of("a.png", "a copy.png")
    lib.copy_of("a.png", "old/a old.png")
    groups = groups_file(tmp_path, [link / "a.png", link / "a copy.png", link / "old/a old.png"])
    result = run_cli("--groups", groups, "--exclude", link / "old", "--auto")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not inside" not in result.stdout + result.stderr
    files = lib.files()
    assert len(files) == 2 and "old/a old.png" in files          # one copy removed, the left-out folder's stays


def test_a_renamed_kept_file_is_still_compared_in_stage_3(library, run_cli, tmp_path):
    library.photo("IMG (1).png", 1)
    library.copy_of("IMG (1).png", "IMG (2).png")
    library.variant("IMG (1).png", "IMG small.jpg", pictures.downscaled)
    groups = groups_file(tmp_path, [library / "IMG (1).png", library / "IMG (2).png", library / "IMG small.jpg"])
    assert summary(run_cli("--groups", groups, "--auto", "--dry-run"))["visual"] == 1
    result = run_cli("--groups", groups, "--auto")
    assert summary(result)["visual"] == 1 and summary(result)["renamed"] == 1   # as the dry run showed
    assert library.files() == ["IMG.png"]


def test_back_to_settings_knows_the_renamed_file(library, run_cli, tmp_path):
    library.photo("IMG (1).png", 1)
    library.copy_of("IMG (1).png", "IMG (2).png")
    library.photo("other.png", 2)
    groups = groups_file(tmp_path, [library / "IMG (1).png", library / "IMG (2).png", library / "other.png"])
    result = run_cli("--groups", groups, keys=["", "b", "q"])     # start, back to settings, quit
    starts = asked(result, "StartScreen")
    assert library.files() == ["IMG.png", "other.png"]
    assert [s["inScope"] for s in starts] == [3, 2]


def test_exclude_on_the_command_line_is_not_remembered(library, run_cli, tmp_path, isolated):
    library.photo("one/a.png", 1)
    library.copy_of("one/a.png", "one/a copy.png")
    library.copy_of("one/a.png", "two/a.png")
    groups = groups_file(tmp_path, [library / "one/a.png", library / "one/a copy.png", library / "two/a.png"])
    result = run_cli("--groups", groups, "--exclude", "two", "--dry-run", keys=["", "q"])
    assert "Left out: 1 folder(s)." in result.stdout and summary(result)["exact"] == 1
    assert not (isolated / "state" / app.GROUPS_FOLDERS_FILE).exists()
    again = run_cli("--groups", groups, "--dry-run", keys=["", "q"])
    assert "Left out" not in again.stdout and asked(again, "StartScreen")[0]["inScope"] == 3


@pytest.fixture
def locked(library):
    """A subfolder whose files can't be removed (moving them needs write access)."""
    folder = library / "locked"
    folder.mkdir(parents=True)
    yield folder
    folder.chmod(0o755)


def test_a_carried_out_dry_run_that_failed_to_remove_ends_with_1(library, run_cli, locked, tmp_path):
    library.photo("a.png", 1)
    library.copy_of("a.png", "locked/a copy.png")
    locked.chmod(0o555)
    groups = groups_file(tmp_path, [library / "a.png", library / "locked/a copy.png"])
    grouped = run_cli("--groups", groups, keys=["4", "", "", "y", "q"])   # dry run, start, carry out, yes, quit
    assert summary(grouped)["failed"] == 1 and grouped.returncode == 1


def test_a_carried_out_dry_run_of_a_folder_that_failed_to_remove_ends_with_1(library, run_cli, locked):
    library.photo("a.png", 1)
    library.copy_of("a.png", "locked/a copy.png")
    locked.chmod(0o555)
    folder = run_cli(library.path, "-r", keys=["4", "", "", "y", "q"])
    assert summary(folder)["failed"] == 1 and folder.returncode == 1


def test_two_groups_runs_keep_each_others_marks(tmp_path):
    from keepbest.session import IgnoreList
    shared = tmp_path / "state" / app.GROUPS_IGNORE_FILE
    first, second = IgnoreList(Path("/"), file=shared), IgnoreList(Path("/"), file=shared)   # both loaded empty

    def meta(name):
        return {"path": f"/pics/{name}", "size": 1, "mtime_ns": 2}
    first.add(meta("a.jpg"), meta("b.jpg"))
    second.add(meta("c.jpg"), meta("d.jpg"))
    again = IgnoreList(Path("/"), file=shared)
    assert again.contains(meta("a.jpg"), meta("b.jpg")) and again.contains(meta("c.jpg"), meta("d.jpg"))
    assert len(json.loads(shared.read_text())["pairs"]) == 2
