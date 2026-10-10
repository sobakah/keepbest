"""Finding files, excluding folders and the folder list's selection logic."""

import os

import pytest

from keepbest.app import (ROOT, FolderIndex, find_images, folder_entries, media_text, normalize_excluded,
                         parse_numbers, subfolders_along, switch_folders)


@pytest.fixture
def tree(library):
    library.photo("main.jpg", 1)
    library.photo("Familie/a.jpg", 2)
    library.photo("Urlaub/Strand/b.jpg", 3)
    library.photo("Urlaub/Berge/c.jpg", 4)
    library.photo(".thumbnails/hidden.jpg", 5)
    library.photo(".hidden.jpg", 6)
    return library


def names(paths, base):
    return sorted(p.relative_to(base).as_posix() for p in paths)


def test_hidden_files_and_folders_are_skipped(tree):
    assert names(find_images(tree.path, recursive=True), tree.path) == [
        "Familie/a.jpg", "Urlaub/Berge/c.jpg", "Urlaub/Strand/b.jpg", "main.jpg"]


def test_without_subfolders_only_the_main_folder_is_scanned(tree):
    assert names(find_images(tree.path, recursive=False), tree.path) == ["main.jpg"]


def test_symlinks_are_not_followed(tree):
    os.symlink(tree / "main.jpg", tree / "link.jpg")
    assert "link.jpg" not in names(find_images(tree.path, recursive=True), tree.path)


def test_exclude_takes_the_subfolders_along(tree):
    excluded = normalize_excluded(tree.path, ["Urlaub"])
    assert excluded == {"Urlaub", "Urlaub/Strand", "Urlaub/Berge"}
    assert names(find_images(tree.path, True, excluded), tree.path) == ["Familie/a.jpg", "main.jpg"]


def test_exclude_dot_leaves_out_only_the_main_folder(tree):
    excluded = normalize_excluded(tree.path, ["."])
    assert names(find_images(tree.path, True, excluded), tree.path) == [
        "Familie/a.jpg", "Urlaub/Berge/c.jpg", "Urlaub/Strand/b.jpg"]


def test_exclusions_only_apply_to_recursive_scans(tree):
    assert names(find_images(tree.path, False, {ROOT}), tree.path) == ["main.jpg"]


def test_files_in_scope(tree):
    index = FolderIndex()
    settings = {"recursive": True, "excluded": set(), "videos": True}
    assert index.in_scope(tree.path, settings) == 4
    assert index.in_scope(tree.path, {**settings, "excluded": {"Urlaub/Strand"}}) == 3
    assert index.in_scope(tree.path, {**settings, "recursive": False}) == 1


# --- the folder list ----------------------------------------------------------
def test_a_folder_can_be_switched_without_its_subfolders(entries):
    excluded = set()
    switch_folders(excluded, entries, [2], with_subfolders=False)    # Urlaub only
    assert excluded == {"Urlaub"}


def test_subfolders_along_tells_what_would_change(entries):
    assert subfolders_along(set(), entries, [2]) == (["Urlaub"], 2)          # Berge and Strand
    assert subfolders_along(set(), entries, [1]) == ([], 0)                  # Familie has none
    assert subfolders_along(set(), entries, [2, 3, 4]) == ([], 0)            # all listed themselves
    assert subfolders_along(set(), entries, [2, 4]) == (["Urlaub"], 1)       # only Berge goes along
    assert subfolders_along({"Urlaub"}, entries, [2]) == ([], 0)             # they are on already


def test_the_folder_list_asks_before_switching_subfolders(tree, run_cli):
    # f opens the list, 2 is Urlaub with Berge and Strand below it
    declined = run_cli(tree.path, "-r", keys=["f", "2", "n", "", "q"]).stdout
    assert "Also switch the 2 subfolders below Urlaub/? [Y/n]" in declined
    assert "Selected: main folder + 3 of 4 subfolders" in declined
    accepted = run_cli(tree.path, "-r", keys=["f", "2", "", "", "q"]).stdout   # Enter = yes
    assert "Selected: main folder + 1 of 4 subfolders" in accepted
    no_question = run_cli(tree.path, "-r", keys=["f", "1", "", "q"]).stdout   # Familie has none
    assert "Also switch" not in no_question


def test_folders_count_images_and_videos_apart(tree):
    (tree.path / "Urlaub" / "clip.mp4").write_bytes(b"not decoded")   # the list goes by extension
    (tree.path / "Urlaub" / "Strand" / "wave.mkv").write_bytes(b"not decoded")
    kinds = FolderIndex().folder_kinds(tree.path)
    assert kinds == {ROOT: (1, 0), "Familie": (1, 0), "Urlaub": (0, 1), "Urlaub/Strand": (1, 1),
                     "Urlaub/Berge": (1, 0)}


@pytest.mark.parametrize("images, videos, scanned, text", [
    (1, 0, True, "1 image"),
    (3, 1, True, "3 images · 1 video"),
    (0, 2, True, "2 videos"),
    (2, 1, False, "2 images · 1 video (not scanned)"),
    (0, 0, True, ""),
])
def test_media_text(images, videos, scanned, text):
    assert media_text(images, videos, scanned) == text


def test_the_folder_list_shows_images_and_videos(tree, run_cli):
    (tree.path / "Urlaub" / "clip.mp4").write_bytes(b"not decoded")
    (tree.path / "Urlaub" / "Strand" / "wave.mkv").write_bytes(b"not decoded")
    screen = run_cli(tree.path, "-r", keys=["f", "", "q"]).stdout   # open the list, done, quit
    assert "Main folder  1 image directly in it" in screen
    assert "Familie/   1 image\n" in screen
    assert "Urlaub/    1 video\n" in screen
    assert "Strand/  1 image · 1 video\n" in screen
    assert "Selected: main folder + 4 of 4 subfolders · 4 images · 2 videos\n" in screen


def test_the_folder_list_marks_videos_that_are_not_scanned(tree, run_cli):
    (tree.path / "Urlaub" / "clip.mp4").write_bytes(b"not decoded")
    screen = run_cli(tree.path, "-r", "--no-videos", keys=["f", "", "q"]).stdout
    assert "1 video (not scanned)" in screen
    assert "Selected: main folder + 4 of 4 subfolders · 4 images\n" in screen


@pytest.fixture
def entries(tree):
    found = folder_entries(tree.path, FolderIndex())
    assert [rel for rel, _ in found] == ["Familie", "Urlaub", "Urlaub/Berge", "Urlaub/Strand"]
    return found


def test_each_listed_folder_switches_on_its_own(entries):
    excluded = {"Urlaub", "Urlaub/Berge", "Urlaub/Strand"}           # only Familie selected
    switch_folders(excluded, entries, [4, 1])                        # "4,1": select Strand, unselect Familie
    assert excluded == {"Familie", "Urlaub", "Urlaub/Berge"}


def test_a_parent_takes_its_subfolders_along(entries):
    excluded = set()
    switch_folders(excluded, entries, [2])
    assert excluded == {"Urlaub", "Urlaub/Berge", "Urlaub/Strand"}


def test_a_listed_subfolder_keeps_its_own_choice(entries):
    excluded = {"Urlaub/Berge"}
    switch_folders(excluded, entries, [2, 3])                        # Urlaub off, Berge on
    assert excluded == {"Urlaub", "Urlaub/Strand"}


def test_the_main_folder_is_never_switched_by_numbers(entries):
    excluded = {ROOT}
    switch_folders(excluded, entries, [1, 2, 3, 4])
    assert ROOT in excluded


@pytest.mark.parametrize("text, upper, expected", [
    ("3", 5, [3]), ("2-4", 5, [2, 3, 4]), ("4-2", 5, [2, 3, 4]), ("1,3 5", 5, [1, 3, 5]),
    ("0", 5, None), ("6", 5, None), ("a", 5, None), ("", 5, None),
])
def test_parse_numbers(text, upper, expected):
    assert parse_numbers(text, upper) == expected
