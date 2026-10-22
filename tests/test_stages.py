"""End to end: the three stages, deleting, the log, renaming and the command line."""

import os
from pathlib import Path

from PIL import Image

import pytest

import pictures
from conftest import asked, summary


@pytest.fixture
def mixed(library):
    """Two exact copies, two visual copies, one edited version, one unrelated picture."""
    library.photo("a.png", 1)
    library.copy_of("a.png", "backup/a copy.png")                 # stage 1: identical
    library.variant("a.png", "a.jpg", quality=85)                 # stage 3: visually identical
    library.variant("a.png", "a small.jpg", pictures.downscaled)  # stage 3
    library.variant("a.png", "a text.jpg", pictures.with_text, quality=90)  # edited: keep
    library.photo("b.png", 2)                                     # unrelated
    return library


def test_dry_run_finds_all_stages_and_changes_nothing(mixed, run_cli):
    before = mixed.files()
    result = run_cli(mixed.path, "-r", "--auto", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary(result) == {**summary(""), "scanned": 6, "exact": 1, "visual": 2}
    assert mixed.files() == before
    for stage in ("Stage 1 · Identical files", "Stage 3 · Visually identical images"):
        assert stage in result.stdout


def test_real_run_moves_copies_to_the_trash_and_logs_them(mixed, run_cli, isolated):
    result = run_cli(mixed.path, "-r", "--auto")
    assert result.returncode == 0
    assert mixed.files() == ["a text.jpg", "a.png", "b.png"]
    trash = isolated / "home" / ".local" / "share" / "Trash" / "files"
    assert sorted(p.name for p in trash.iterdir()) == ["a copy.png", "a small.jpg", "a.jpg"]
    log = (isolated / "state" / "keepbest.log").read_text()
    assert log.count("| TRASHED  |") == 3
    assert "exact copy (identical bytes), automatic" in log


def test_the_copy_number_is_removed_from_a_better_copy(library, run_cli):
    library.photo("photo (1).jpg", 3, size=(640, 480), quality=95)       # the better copy
    library.variant("photo (1).jpg", "photo.jpg", pictures.downscaled)   # smaller copy
    run_cli(library.path, "--auto")
    assert library.files() == ["photo.jpg"]
    assert summary(run_cli(library.path, "--auto"))["visual"] == 0


def test_series_numbers_are_not_removed(library, run_cli):
    library.photo("Holiday (12).png", 4)
    library.variant("Holiday (12).png", "IMG_5.jpg", quality=90)
    run_cli(library.path, "--auto")
    assert library.files() == ["Holiday (12).png"]


def test_hard_links_free_no_space(library, run_cli):
    library.photo("a.png", 5)
    os.link(library / "a.png", library / "b.png")
    result = run_cli(library.path, "--auto")
    assert "only the extra name is removed" in result.stdout
    assert "Space freed: 0 B" in result.stdout


# Without stage 1, the byte-identical copy is still in place for stage 3, which
# then finds it as a visually identical picture too: 3 instead of 2.
@pytest.mark.parametrize("stages, exact, visual", [("1", 1, 0), ("3", 0, 3), ("1,3", 1, 2), ("all", 1, 2), ("both", 1, 2)])
def test_choosing_stages(mixed, run_cli, stages, exact, visual):
    counts = summary(run_cli(mixed.path, "-r", "--auto", "--dry-run", "--stages", stages))
    assert (counts["exact"], counts["visual"]) == (exact, visual)


def test_invalid_stages_are_rejected(mixed, run_cli):
    result = run_cli(mixed.path, "--stages", "4")
    assert result.returncode == 2
    assert "stage numbers 1-3" in result.stderr


def test_exclude_leaves_out_subfolders_too(library, run_cli):
    library.photo("keep.png", 1)
    library.copy_of("keep.png", "Urlaub/Strand/copy.png")
    counts = summary(run_cli(library.path, "-r", "--exclude", "Urlaub", "--auto", "--dry-run"))
    assert (counts["scanned"], counts["exact"]) == (1, 0)


@pytest.mark.parametrize("mode, keys, code", [
    (["--auto", "--dry-run"], [], 2),     # --auto: refused, exit code 2
    ([], ["q"], 0),                       # interactive: refused, asked for another folder (q quits)
])
def test_the_home_folder_is_refused_with_the_same_message(run_cli, isolated, mode, keys, code):
    from keepbest.app import protected_message
    home = isolated / "home"
    pictures.save(pictures.photo(1), home / "a.png")
    result = run_cli(home, *mode, keys=keys)
    assert result.returncode == code
    assert protected_message(home) in " ".join(result.stdout.split())   # the same text in both modes


@pytest.mark.parametrize("args, code", [
    (["/does/not/exist", "--auto"], 2),
    (["--version"], 0),
])
def test_exit_codes(run_cli, args, code):
    assert run_cli(*args).returncode == code


def test_a_missing_config_file_is_an_error(mixed, run_cli, tmp_path):
    result = run_cli(mixed.path, "--auto", "-c", tmp_path / "nope.json")
    assert result.returncode == 2


def test_invalid_config_numbers_are_reported(mixed, run_cli, config_file):
    config_file({"compare_size": "big"})
    result = run_cli(mixed.path, "-r", "--auto", "--dry-run")
    assert "'compare_size' must be a whole number" in result.stdout
    assert summary(result)["visual"] == 2          # still compares correctly, with the default


def test_marked_pairs_are_left_out(library, run_cli):
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=85)
    library.variant("a.png", "a small.jpg", pictures.downscaled)
    # interactively: Enter starts, i marks the only group, Enter finishes
    result = run_cli(library.path, "--dry-run", "-i", keys=["", "i", ""])
    assert "Marked as not duplicates" in result.stdout
    assert (library / ".keepbest_ignore.json").exists()
    # a.jpg and a small.jpg were only marked against a.png: they are still asked about
    assert summary(run_cli(library.path, "--auto", "--dry-run"))["visual"] == 1
    run_cli(library.path, "--dry-run", "-i", keys=["", "i", ""])
    assert summary(run_cli(library.path, "--auto", "--dry-run"))["visual"] == 0
    assert summary(run_cli(library.path, "--auto", "--dry-run", "--no-ignore"))["visual"] == 2


def test_keeping_another_image_removes_the_others(library, run_cli, isolated):
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=90)
    run_cli(library.path, "-i", keys=["", "1", ""])                 # start, keep #1, finish
    assert library.files() == ["a.jpg"]
    assert "to the chosen image), user kept #1" in (isolated / "state" / "keepbest.log").read_text()


def test_permanent_mode_asks_once_before_starting(mixed, run_cli, config_file):
    config_file({"delete_mode": "permanent"})
    before = mixed.files()
    declined = run_cli(mixed.path, "-r", keys=["", "n", "q"])       # start, decline, quit
    assert "Permanent mode: files are deleted for good" in declined.stdout
    assert "Nothing started." in declined.stdout
    assert mixed.files() == before
    accepted = run_cli(mixed.path, "-r", keys=["", "y", ""])        # start, confirm, quit at the end
    assert summary(accepted)["exact"] == 1
    assert "a copy.png" not in " ".join(mixed.files())


@pytest.mark.skipif(not __import__("keepbest.image_utils").image_utils.JXL_SUPPORTED,
                    reason="pillow-jxl-plugin not installed")
@pytest.mark.parametrize("prefer_jxl, kept", [(False, "a.png"), (True, "a.jxl")])
def test_prefer_jxl_chooses_between_png_and_jpeg_xl(library, run_cli, config_file, prefer_jxl, kept):
    config_file({"prefer_jxl": prefer_jxl})
    library.photo("a.png", 1)
    library.variant("a.png", "a.jxl", quality=90)                    # lossy JPEG XL copy
    result = run_cli(library.path, "--auto")
    assert summary(result)["visual"] == 1
    assert library.files() == [kept]


@pytest.fixture
def locked(library):
    """A subfolder whose files can't be removed (moving them needs write access)."""
    folder = library / "locked"
    folder.mkdir(parents=True)
    yield folder
    folder.chmod(0o755)


def test_a_failed_removal_keeps_the_copy_number(library, run_cli, locked):
    library.photo("a (1).png", 1)
    library.copy_of("a (1).png", "locked/a (2).png")
    locked.chmod(0o555)
    counts = summary(run_cli(library.path, "-r", "--auto"))
    assert (counts["failed"], counts["renamed"]) == (1, 0)
    assert library.files() == ["a (1).png", "locked/a (2).png"]


def test_a_group_nothing_could_be_removed_from_stays_open(library, run_cli, locked):
    library.photo("photo (1).png", 1, size=(640, 480))
    library.variant("photo (1).png", "locked/photo.jpg", pictures.downscaled, quality=90)
    locked.chmod(0o555)
    # start, keep #0 (fails), keep the session for the open group
    result = run_cli(library.path, "-r", "-i", keys=["", "", "", "y"])
    assert "Nothing was removed - the group stays open." in result.stdout
    assert summary(result)["renamed"] == 0
    assert "photo (1).png" in library.files()
    assert asked(run_cli(library.path, keys=["q"]), "StartScreen")[0]["primary"] == "Resume saved session (1 open group(s))"


def test_the_folders_above_home_and_system_folders_are_refused(isolated, monkeypatch):
    from keepbest.app import is_protected
    home = isolated / "home"
    real_home = isolated / "var-home" / "max"
    real_home.mkdir(parents=True)
    (isolated / "linked-home").symlink_to(real_home)
    assert is_protected(home) and is_protected(isolated) and is_protected(Path("/"))
    assert is_protected(Path("/usr")) and is_protected(Path("/etc"))
    assert not is_protected(home / "Pictures") and not is_protected(Path("/mnt"))
    monkeypatch.setenv("HOME", str(isolated / "linked-home"))       # $HOME through a symlink
    assert is_protected(real_home) and is_protected(real_home.parent)
    assert not is_protected(real_home / "Pictures")


def test_a_folder_above_home_is_refused_with_auto(run_cli, isolated):
    pictures.save(pictures.photo(1), isolated / "home" / "a.png")
    pictures.save(pictures.photo(1), isolated / "home" / "b.png")
    result = run_cli(isolated, "-r", "--auto", "--dry-run")
    assert result.returncode == 2 and "are not scanned" in result.stdout


@pytest.mark.parametrize("written", ["Urlaub/../Urlaub", "./Urlaub/", "Urlaub/Strand/.."])
def test_exclude_understands_dots(library, run_cli, written):
    library.photo("keep.png", 1)
    library.copy_of("keep.png", "Urlaub/copy.png")
    counts = summary(run_cli(library.path, "-r", "--exclude", written, "--auto", "--dry-run"))
    assert (counts["scanned"], counts["exact"]) == (1, 0)


def test_exclude_outside_the_folder_is_reported(library, run_cli):
    library.photo("keep.png", 1)
    (library.path.parent / "other").mkdir()
    result = run_cli(library.path, "-r", "--exclude", "../other", "--auto", "--dry-run")
    assert "--exclude ../other is not inside" in result.stdout


@pytest.fixture
def edited_on_top(library):
    """photo.png and photo.jpg are copies; edited.png is close to both but larger,
    so it would be the recommended keeper of all three."""
    def bigger_brighter(img):
        return pictures.brighter(img.resize((640, 480)), 1.05)
    library.photo("photo.png", 1)
    library.variant("photo.png", "photo.jpg", quality=85)
    library.variant("photo.png", "edited.png", bigger_brighter)
    return library


def test_copies_are_still_found_next_to_an_image_marked_against_them(edited_on_top, run_cli):
    first = run_cli(edited_on_top.path, "--dry-run", "-i", keys=["", "i", ""])   # mark the whole group
    assert "Marked as not duplicates" in first.stdout
    again = run_cli(edited_on_top.path, "--auto", "--dry-run")
    assert summary(again)["visual"] == 1                             # photo.png and photo.jpg


def test_i0_takes_only_the_recommended_image_out(edited_on_top, run_cli):
    # start, "only #0 is different", keep the new #0, finish
    result = run_cli(edited_on_top.path, "-i", keys=["", "i0", "", ""])
    assert "Marked #0 edited.png as not a duplicate of the others." in result.stdout
    assert edited_on_top.files() == [".keepbest_ignore.json", "edited.png", "photo.png"]
    assert summary(run_cli(edited_on_top.path, "--auto", "--dry-run"))["visual"] == 0   # nothing left


def save_with_second_picture(img, path, **options):
    """An MPO (as cameras write .jpg files with a second picture) or a two-page TIFF."""
    second = pictures.photo(9, img.size).convert("RGB")
    fmt = "MPO" if path.suffix == ".jpg" else "TIFF"
    img.convert("RGB").save(path, format=fmt, save_all=True, append_images=[second], **options)
    return path


def test_an_mpo_photo_is_compared_and_kept(library, run_cli):
    save_with_second_picture(pictures.photo(1, (320, 240)), library / "camera.jpg", quality=95)
    library.variant("camera.jpg", "export.jpg", quality=80)
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "animated" not in result.stdout
    assert "Kept         camera.jpg" in result.stdout and "Would delete export.jpg" in result.stdout


def test_a_file_with_several_pictures_is_never_removed(library, run_cli):
    library.photo("big.png", 1, size=(640, 480))                    # a better copy of page 1
    with Image.open(library / "big.png") as img:
        save_with_second_picture(img.resize((320, 240)), library / "pages.tif")
    library.variant("big.png", "small.jpg", pictures.downscaled, quality=85)
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "Would delete small.jpg" in result.stdout
    assert "pages.tif" not in result.stdout


def test_keeping_another_image_never_removes_a_file_with_several_pictures(library, run_cli):
    save_with_second_picture(pictures.photo(1, (640, 480)), library / "pages.tif")
    library.variant("pages.tif", "small.jpg", pictures.downscaled, quality=85)
    result = run_cli(library.path, "-i", keys=["", "1", "s", ""])  # keep small.jpg instead, then skip
    assert "nothing would be removed" in result.stdout
    assert library.files() == ["pages.tif", "small.jpg"]


@pytest.mark.skipif(__import__("keepbest.image_utils").image_utils.JXL_SUPPORTED,
                    reason="the hint only appears without pillow-jxl-plugin")
def test_without_the_jxl_plugin_the_hint_names_this_python(library, run_cli):
    import sys
    library.photo("a.png", 1)
    library.photo("b.png", 2)
    (library / "c.jxl").write_bytes(b"\xff\x0a not really")
    result = run_cli(library.path, "--auto", "--dry-run")
    assert f"{sys.executable} -m pip install pillow-jxl-plugin" in " ".join(result.stdout.split())


def test_a_pipx_install_is_told_to_inject_the_plugin(monkeypatch):
    import sys
    from keepbest.core_logic import jxl_install_hint
    monkeypatch.setattr(sys, "prefix", "/home/u/.local/share/pipx/venvs/keepbest")
    assert "pipx inject keepbest pillow-jxl-plugin" in jxl_install_hint()[0]
