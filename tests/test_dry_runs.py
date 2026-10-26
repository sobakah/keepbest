"""Carrying out a saved dry run, or reviewing it again: the same result as a real run."""

import pytest

import pictures
from conftest import asked, make_video, remux, requires_ffmpeg, summary


# --- identical copies and remuxes -------------------------------------------------
@pytest.fixture
def renamed_remux(library):
    """clip (1).mp4 + clip (2).mp4 (identical) and clip.mkv (the same streams, remuxed):
    Stage 1 renames clip (1).mp4 to clip.mp4, which Stage 2 then compares."""
    clip = make_video(library / "clip (1).mp4", codec="mpeg4")
    library.copy_of("clip (1).mp4", "clip (2).mp4")
    remux(clip, library / "clip.mkv")
    return library


@requires_ffmpeg
@pytest.mark.parametrize("carry_out", [[""], ["e"]], ids=["apply", "review-again"])
def test_a_stage_1_rename_reaches_the_remux_group(renamed_remux, run_cli, tmp_path, carry_out):
    real = tmp_path / "real"
    import shutil
    shutil.copytree(renamed_remux.path, real, copy_function=shutil.copy2)
    run_cli(real, "--auto")
    expected = sorted(p.name for p in real.iterdir())

    run_cli(renamed_remux.path, "--dry-run", keys=["", ""])         # dry run, kept to carry out
    result = run_cli(renamed_remux.path, keys=[*carry_out, "", ""])
    assert summary(result)["failed"] == 0
    assert renamed_remux.files() == expected == ["clip.mp4"]        # the same keeper as a real run


@pytest.fixture
def silent_remux(library):
    """A remux without sound (asked about), plus an identical picture pair, so the
    dry run has something to carry out whatever is decided about the remux."""
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "silent.mkv", "-an")
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")                              # same mtime: a.png is kept by name
    return library


@requires_ffmpeg
def test_a_skipped_remux_is_counted_and_asked_again(silent_remux, run_cli):
    dry = run_cli(silent_remux.path, "--dry-run", keys=["", "s", ""])     # start, skip the remux, quit
    assert summary(dry)["skipped"] == 1
    again = run_cli(silent_remux.path, keys=["e", "", ""])          # review again, keep clip.mp4, quit
    assert [q["group"]["tracks"] for q in asked(again, "RemuxScreen")] == ["covered"]   # asked again
    assert silent_remux.files() == ["a.png", "clip.mp4"]


@requires_ffmpeg
def test_a_skipped_remux_is_left_alone_when_applied(silent_remux, run_cli):
    run_cli(silent_remux.path, "--dry-run", keys=["", "s", ""])
    applied = run_cli(silent_remux.path, keys=["", ""])             # carry out as it showed
    assert "1 remux group(s) skipped in the dry run were left as they are" in applied.stdout
    assert summary(applied)["skipped"] == 1
    assert silent_remux.files() == ["a.png", "clip.mp4", "silent.mkv"]


@requires_ffmpeg
def test_a_remux_chosen_in_the_dry_run_is_logged_as_chosen(silent_remux, run_cli, tmp_path):
    run_cli(silent_remux.path, "--dry-run", keys=["", "1", ""])     # keep silent.mkv instead
    run_cli(silent_remux.path, keys=["e", ""])                      # review again: carried out as chosen
    assert silent_remux.files() == ["a.png", "silent.mkv"]
    log = (tmp_path / "state" / "keepbest.log").read_text()
    remux_line = next(line for line in log.splitlines() if "clip.mp4" in line)
    assert "chosen by user in the dry run" in remux_line and "automatic" not in remux_line


# --- visually identical images ----------------------------------------------------
def test_review_again_brings_back_images_another_keeper_took_out(library, run_cli):
    """a.png is close to b.png and c.png, which are far from each other. Keeping b.png
    in the dry run takes c.png out of the group; reviewing again starts from the
    group as it was found, so a.png (the larger picture) is kept and both go."""
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "b.png", change=lambda im: pictures.brighter(pictures.downscaled(im), 1.06))
    library.variant("a.png", "c.png", change=lambda im: pictures.brighter(pictures.downscaled(im), 0.94))
    dry = run_cli(library.path, "--dry-run", "-i", keys=["", "1", "", ""])   # start, keep b.png, finish
    assert "Kept, not a duplicate of #1: c.png" in dry.stdout
    run_cli(library.path, keys=["e", "", ""])                       # review again: asks as configured
    assert library.files() == ["a.png"]


# --- what the start screen promises ------------------------------------------------
def numbered_copies(library, name_taken=False):
    """photo (1).jpg + photo (2).jpg (identical): the kept one would become photo.jpg."""
    library.photo("photo (1).jpg", 1)
    library.copy_of("photo (1).jpg", "photo (2).jpg")
    if name_taken:
        library.photo("photo.jpg", 2)                               # another picture has that name


def test_a_taken_name_is_not_counted_as_a_rename(library, run_cli):
    numbered_copies(library, name_taken=True)
    result = run_cli(library.path, "--stages", "1", keys=["4", "", "q"])   # dry run from the start screen
    assert "Name taken" in result.stdout and summary(result)["renamed"] == 0
    assert "rename 1" not in asked(result, "AfterDryRunScreen")[0]["primary"]


def test_a_name_an_earlier_stage_frees_is_counted_as_a_rename(library, run_cli):
    """Stage 1 removes photo.png (a copy of a.png); Stage 3 then renames photo (1).png to it."""
    library.photo("a.png", 2)
    library.copy_of("a.png", "photo.png")
    library.photo("photo (1).png", 1)
    library.variant("photo (1).png", "photo (2).jpg", quality=90)
    dry = run_cli(library.path, keys=["4", "", "q"])
    assert "Name taken" not in dry.stdout and summary(dry)["renamed"] == 1
    assert asked(run_cli(library.path, keys=["q"]), "StartScreen")[0]["panel"]["to_rename"] == 1
    real = run_cli(library.path, "--auto")
    assert summary(real)["renamed"] == 1 and library.files() == ["a.png", "photo.png"]


def test_two_renames_to_one_name_are_counted_once(library, run_cli):
    """Both groups would become photo.png; a real run renames the first one only."""
    library.photo("photo (1).png", 1)
    library.copy_of("photo (1).png", "photo (2).png")
    library.photo("photo (3).png", 2)
    library.copy_of("photo (3).png", "photo (4).png")
    dry = run_cli(library.path, "--stages", "1", keys=["4", "", "q"])
    assert summary(dry)["renamed"] == 1
    assert asked(run_cli(library.path, "--stages", "1", keys=["q"]), "StartScreen")[0]["panel"]["to_rename"] == 1
    real = run_cli(library.path, "--stages", "1", "--auto")
    assert summary(real)["renamed"] == 1 and library.files() == ["photo (3).png", "photo.png"]


def test_the_saved_session_counts_identical_copies_as_groups(library, run_cli):
    numbered_copies(library)
    run_cli(library.path, "--stages", "1", keys=["4", "", "q"])
    panel = asked(run_cli(library.path, keys=["q"]), "StartScreen")[0]["panel"]
    assert panel["finished"] and panel["groups"] == 1


@pytest.mark.parametrize("dry_args, now_args, renamed", [((), ("--no-rename",), True),
                                                         (("--no-rename",), (), False)])
def test_carrying_out_renames_as_the_dry_run_showed(library, run_cli, dry_args, now_args, renamed):
    numbered_copies(library)
    run_cli(library.path, "--stages", "1", *dry_args, keys=["4", "", "q"])
    result = run_cli(library.path, *now_args, keys=["", "q"])       # carry out, quit
    start = asked(result, "StartScreen")[0]
    assert "Carry out the saved dry run" in start["primary"]
    assert ("rename 1" in start["primary"]) == (start["panel"]["to_rename"] == 1) == renamed
    assert ("photo.jpg" in library.files()) == renamed
