"""The pixel comparison: copies must match, edits and other pictures must not."""

import struct

import pytest
from PIL import Image

import pictures
from keepbest.image_utils import (analyze_image, aspect_ratio_close, comparison_thumbnail,
                                 is_lossless, pixel_difference, score_image)

LIMIT = 20  # the "normal" strictness


def diff(a, b):
    return pixel_difference(comparison_thumbnail(a, 512), comparison_thumbnail(b, 512))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_reencoded_and_resized_copies_match(library, seed):
    original = library.photo("original.png", seed)
    copies = [
        library.variant("original.png", "q85.jpg", quality=85),
        library.variant("original.png", "q50.jpg", quality=50),
        library.variant("original.png", "copy.webp", quality=70),
        library.variant("original.png", "half.jpg", pictures.downscaled, quality=85),
    ]
    for copy in copies:
        assert diff(original, copy) <= LIMIT, copy.name


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_small_edits_do_not_match(library, seed):
    original = library.photo("original.png", seed)
    for change in (pictures.with_text, pictures.with_patch, pictures.brighter):
        edited = library.variant("original.png", f"{change.__name__}.jpg", change, quality=90)
        assert diff(original, edited) > LIMIT, change.__name__


def test_unrelated_pictures_do_not_match(library):
    assert diff(library.photo("a.png", 1), library.photo("b.png", 2)) > 100


def test_exif_rotation_is_applied(library):
    original = library.photo("upright.png", 3)
    with Image.open(original) as img:
        rotated = img.transpose(Image.ROTATE_90)       # pixels turned; the tag turns them back
    exif = rotated.getexif()
    exif[0x0112] = 6
    rotated.save(library / "rotated.jpg", quality=92, exif=exif)
    meta = analyze_image(library / "rotated.jpg", 8, {})
    assert (meta["width"], meta["height"]) == (320, 240)
    assert diff(original, library / "rotated.jpg") <= LIMIT


def test_hidden_colours_under_transparency_are_ignored(library):
    def square(hidden, visible, name):
        img = Image.new("RGBA", (200, 200), hidden + (0,))      # fully transparent background
        img.paste(visible + (255,), (50, 50, 150, 150))
        img.save(library / name)
        return library / name
    a = square((0, 0, 0), (200, 30, 30), "a.png")
    b = square((0, 255, 0), (200, 30, 30), "b.png")              # other hidden colour, same picture
    c = square((0, 0, 0), (30, 30, 200), "c.png")                # visibly different
    assert diff(a, b) == 0
    assert diff(a, c) > LIMIT


def test_different_16_bit_pictures_do_not_look_alike(library):
    for name, seed in (("a.png", 1), ("b.png", 2)):
        gray = pictures.photo(seed).convert("L")
        Image.frombytes("I;16", gray.size, bytes(b for px in gray.tobytes() for b in (0, px))).save(library / name)
    assert diff(library / "a.png", library / "b.png") > LIMIT


def save_high_bit_depth(img, path, bits: int):
    """Save an 8-bit picture as 16-bit greyscale PNG using only the lowest *bits* bits,
    like 12-bit camera or scanner data stored in a 16-bit file."""
    gray = img.convert("L")
    values = [px << (bits - 8) for px in gray.tobytes()]
    Image.frombytes("I;16", gray.size, b"".join(v.to_bytes(2, "little") for v in values)).save(path)
    return path


@pytest.mark.parametrize("bits", [10, 12, 14])
def test_edits_in_12_bit_pictures_do_not_match(library, bits):
    original = pictures.photo(1)
    a = save_high_bit_depth(original, library / "a.png", bits)
    b = save_high_bit_depth(pictures.with_patch(original), library / "b.png", bits)
    copy = save_high_bit_depth(original, library / "copy.tif", bits)
    assert diff(a, b) > LIMIT
    assert diff(a, copy) == 0


def save_16_bit(path, values, size):
    img = Image.new("I;16", size)
    img.putdata([min(65535, round(v)) for v in values])
    img.save(path)
    return path


def dark_values(seed: int, brightest: int):
    """A greyscale photo whose brightest pixel is *brightest* (of 65535), and its size."""
    gray = pictures.photo(seed).convert("L")
    top = gray.getextrema()[1]
    return [v * brightest / top for v in gray.tobytes()], gray.size


def test_a_dark_16_bit_picture_matches_its_8_bit_copy(library):
    values, size = dark_values(1, 8000)                          # a low-key photo: 16-bit, but dark
    a = save_16_bit(library / "a.png", values, size)
    copy = Image.new("L", size)
    copy.putdata([round(v / 257) for v in values])               # what any converter makes of it
    copy.save(library / "copy.png")
    assert diff(a, library / "copy.png") <= LIMIT


def test_a_16_bit_picture_and_a_brighter_copy_do_not_match(library):
    values, size = dark_values(1, 4000)
    a = save_16_bit(library / "a.png", values, size)
    for factor in (1.3, 2, 4):                                    # x4 used to score 0
        brighter = save_16_bit(library / f"x{factor}.png", [v * factor for v in values], size)
        assert diff(a, brighter) > LIMIT, factor


def test_a_downscaled_16_bit_copy_matches(library):
    values, size = dark_values(3, 4110)                          # just above 12 bits
    a = save_16_bit(library / "a.png", values, size)
    with Image.open(a) as img:
        img.convert("I").resize((size[0] // 2, size[1] // 2), Image.BOX).convert("I;16").save(library / "b.png")
    assert diff(a, library / "b.png") <= LIMIT


@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_float_pictures_with_infinite_values_are_reported(library, value):
    Image.frombytes("F", (4, 4), struct.pack("<16f", *([0.5] * 15 + [value]))).save(library / "inf.tif")
    result = analyze_image(library / "inf.tif", 8, {})
    assert "not finite" in result["error"]


def test_float_pictures_are_not_all_black(library):
    def save_float(img, name):
        gray = img.convert("L")
        Image.frombytes("F", gray.size, b"".join(struct.pack("<f", px / 255) for px in gray.tobytes())) \
            .save(library / name)
        return library / name
    a = save_float(pictures.photo(1), "a.tif")
    b = save_float(pictures.photo(2), "b.tif")
    assert diff(a, b) > LIMIT


def test_lossless_detection(library):
    library.photo("a.png", 1)
    assert is_lossless(library / "a.png", "PNG")
    assert not is_lossless(library.variant("a.png", "a.jpg"), "JPEG")
    assert is_lossless(library.variant("a.png", "l.webp", lossless=True), "WEBP")
    assert not is_lossless(library.variant("a.png", "q.webp", quality=80), "WEBP")


def test_keeper_ranking(library):
    library.photo("big.png", 1, size=(640, 480))
    meta = {name: analyze_image(library / name, 8, {"PNG": 3, "JPEG": 2}) for name in (
        "big.png",
        library.variant("big.png", "small.png", pictures.downscaled).name,
        library.variant("big.png", "big.jpg", quality=95).name,
        library.copy_of("big.png", "big (1).png").name,
    )}
    ranked = sorted(meta, key=lambda n: score_image(meta[n]), reverse=True)
    assert ranked[0] == "big.png"                             # full size, lossless, no copy number
    assert ranked.index("big (1).png") < ranked.index("big.jpg")   # lossless before lossy
    assert ranked[-1] == "small.png"                          # resolution comes first


def test_zero_sized_images_never_match():
    empty = {"width": 0, "height": 0}
    assert aspect_ratio_close(empty, {"width": 10, "height": 10}, 0.02) is False


def test_tiff_is_judged_by_its_compression(library):
    library.photo("a.png", 1)
    for compression, lossless in (("tiff_lzw", True), ("tiff_adobe_deflate", True), ("jpeg", False)):
        path = library.variant("a.png", f"{compression}.tif", compression=compression)
        assert analyze_image(path, 8, {})["lossless"] is lossless, compression


def test_jpeg_xl_is_unknown(tmp_path):
    assert is_lossless(tmp_path / "a.jxl", "JXL") is None


def test_prefer_jxl_decides_between_jpeg_xl_and_a_lossless_original():
    common = {"resolution": 100, "numbered": False, "bpp": 2.0, "age": 1.0}
    png = {**common, "lossless": True, "lossless_rank": True, "format_rank": 3}
    jxl = {**common, "lossless": None, "lossless_rank": False, "format_rank": 6}   # default
    assert score_image(png) > score_image(jxl)
    assert score_image({**jxl, "lossless_rank": True}) > score_image(png)      # "prefer_jxl": true


# --- keeping another image than the recommended one ---------------------------------
def found_group(library, names, diffs):
    """A group as Stage 3 would build it; *diffs* are the scores against names[0]."""
    from keepbest.core_logic import Group
    metas = [analyze_image(library / name, 8, {}) for name in names]
    return Group(images=metas, diffs=[None, *diffs], limit=LIMIT, borderline_above=LIMIT * 0.8)


COMPARE = {"compare_size": 512, "max_aspect_diff": 0.02}


def test_another_keeper_is_compared_with_every_image_it_replaces(library):
    from keepbest.core_logic import regroup_around
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=90)
    library.variant("a.png", "a edited.jpg", pictures.with_patch, quality=90)
    # As if "a edited.jpg" had passed against a.png: it must still not be removed
    # when the user keeps a.jpg instead.
    group = found_group(library, ["a.png", "a edited.jpg", "a.jpg"], [15, 2])
    far = regroup_around(group, 2, COMPARE)
    assert [m["path"].name for m in group.images] == ["a.jpg", "a.png"]
    assert group.diffs[0] is None and group.diffs[1] <= LIMIT
    assert [(m["path"].name, score > LIMIT) for m, score in far] == [("a edited.jpg", True)]


def test_another_keeper_without_any_close_image_changes_nothing(library):
    from keepbest.core_logic import regroup_around
    library.photo("a.png", 1)
    library.variant("a.png", "a edited.jpg", pictures.with_patch, quality=90)
    group = found_group(library, ["a.png", "a edited.jpg"], [15])
    assert regroup_around(group, 1, COMPARE) is None
    assert [m["path"].name for m in group.images] == ["a.png", "a edited.jpg"]


def test_undo_brings_back_the_images_a_chosen_keeper_took_out(library):
    from keepbest.core_logic import (Group, RunContext, Stats, _resolve, _snapshot, regroup_around,
                                    undo_decision)
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=90)
    library.variant("a.png", "a edited.jpg", pictures.with_patch, quality=90)
    group = found_group(library, ["a.png", "a edited.jpg", "a.jpg"], [15, 2])
    stats = Stats(scanned=3)
    before = _snapshot(group)
    regroup_around(group, 2, COMPARE)                          # keep a.jpg: "a edited.jpg" leaves
    _resolve(group, 0, {"delete_mode": "dry_run"}, stats, str, RunContext(), automatic=False, before=before)
    assert (group.status, stats.similar_deleted) == ("done", 1)
    group = Group.from_dict(group.to_dict())                   # as saved in the session
    undo_decision(group, stats)
    assert [m["path"].name for m in group.images] == ["a.png", "a edited.jpg", "a.jpg"]
    assert (group.status, group.undo, group.diffs) == ("pending", None, [None, 15, 2])
    assert (stats.similar_deleted, stats.space_saved, stats.scanned) == (0, 0, 3)


def test_deciding_again_after_an_undo_counts_the_rename_again(library):
    from keepbest.core_logic import RunContext, Stats, _resolve, undo_decision
    library.photo("photo (1).png", 1)
    library.variant("photo (1).png", "photo (2).jpg", quality=90)
    group = found_group(library, ["photo (1).png", "photo (2).jpg"], [2])
    stats, ctx = Stats(scanned=2), RunContext(rename=True)
    dry = {"delete_mode": "dry_run"}
    _resolve(group, 0, dry, stats, str, ctx, automatic=False)
    assert stats.renamed == 1
    undo_decision(group, stats, ctx)
    _resolve(group, 0, dry, stats, str, ctx, automatic=False)
    assert (stats.renamed, stats.similar_deleted) == (1, 1)


# --- where a group differs most (for the page) ------------------------------------------
@pytest.mark.parametrize("edit, area", [
    (pictures.with_patch, (0.1, 0.1, 0.22, 0.2)),              # the dark rectangle
    (pictures.with_text, (0.0, 0.8, 1.0, 1.0)),                # text along the bottom
])
def test_the_most_different_square_is_where_the_edit_is(library, edit, area):
    from keepbest.image_utils import difference_spot
    library.photo("a.png", 1, size=(800, 600))
    library.variant("a.png", "b.png", edit)
    score, (left, top, size) = difference_spot(comparison_thumbnail(library / "a.png", 512),
                                               comparison_thumbnail(library / "b.png", 512))
    assert score == diff(library / "a.png", library / "b.png")
    x0, y0, x1, y1 = area
    assert x0 <= left + size / 2 <= x1 and y0 <= top + size / 2 <= y1, (left, top)


def test_a_rotated_picture_has_its_square_where_it_is_seen(library):
    """The comparison works on the upright picture, and so does the square: a portrait
    stored sideways with an EXIF orientation still gets the square on the patch."""
    from keepbest.image_utils import difference_spot
    upright = pictures.photo(1, (600, 800))
    pictures.save(upright, library / "a.png")
    edited = pictures.with_patch(upright)
    exif = Image.Exif()
    exif[0x0112] = 6                                           # stored turned left, shown turned right
    edited.rotate(90, expand=True).save(library / "b.jpg", exif=exif, quality=95)
    _, (left, top, size) = difference_spot(comparison_thumbnail(library / "a.png", 512),
                                           comparison_thumbnail(library / "b.jpg", 512))
    assert 0.1 <= left + size / 2 <= 0.22 and 0.1 <= top + size / 2 <= 0.2


def test_a_chosen_keeper_moves_the_squares_too(library):
    from keepbest.core_logic import regroup_around
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=90)
    library.variant("a.png", "b.jpg", quality=60)
    group = found_group(library, ["a.png", "a.jpg", "b.jpg"], [2, 3])
    group.images[1]["spot"] = (0.5, 0.5, 0.1)                  # relative to a.png
    regroup_around(group, 1, COMPARE)
    assert group.images[0]["path"].name == "a.jpg" and group.images[0]["spot"] is None
    assert all(len(m["spot"]) == 3 for m in group.images[1:])


def test_the_deciding_criterion_follows_the_ranking_order():
    from keepbest.image_utils import deciding_criterion
    base = {"resolution": 100, "lossless": False, "format_rank": 2, "numbered": False, "bpp": 2.0, "age": 10.0}
    assert deciding_criterion(base, {**base, "resolution": 50, "lossless": True}) == "resolution"
    assert deciding_criterion({**base, "lossless": True}, base) == "lossless"
    assert deciding_criterion({**base, "format_rank": 5}, base) == "format"
    assert deciding_criterion(base, {**base, "numbered": True}) == "name"
    assert deciding_criterion(base, {**base, "bpp": 1.0}) == "bpp"
    assert deciding_criterion(base, {**base, "age": 20.0}) == "age"            # older wins
    assert deciding_criterion(base, {**base, "resolution": 200}) is None       # chosen by hand
    assert deciding_criterion(base, dict(base)) is None
    assert deciding_criterion({"resolution": 1}, base) is None                  # an old session's fields


def test_a_saved_session_keeps_the_jpeg_xl_preference():
    """With prefer_jxl a JPEG XL file ranks as lossless; a resumed session still knows."""
    from pathlib import Path
    from keepbest.core_logic import Group
    from keepbest.image_utils import deciding_criterion
    common = {"resolution": 100, "numbered": False, "age": 0, "width": 10, "height": 10, "size": 1, "mtime_ns": 0}
    jxl = {**common, "path": Path("a.jxl"), "format": "JXL", "lossless": None, "lossless_rank": True,
           "format_rank": 0, "bpp": 1.0}
    jpeg = {**common, "path": Path("a.jpg"), "format": "JPEG", "lossless": False, "lossless_rank": False,
            "format_rank": 0, "bpp": 2.0}
    group = Group(images=[jxl, jpeg], diffs=[None, 3], limit=20, borderline_above=16)
    assert deciding_criterion(*group.images) == "lossless"
    loaded = Group.from_dict(group.to_dict())
    assert deciding_criterion(*loaded.images) == "lossless"
    old = group.to_dict()                                      # a session saved before the field
    for image in old["images"]:
        image.pop("lossless_rank", None)
    assert deciding_criterion(*Group.from_dict(old).images) is None


@pytest.mark.parametrize("count", [0, 1, 2, 7, 8, 40])
@pytest.mark.parametrize("bits, distance", [(128, 28), (128, 9)])   # every pair / by chunks
def test_the_candidate_search_finds_every_close_pair(count, bits, distance):
    import random
    from keepbest.core_logic import _candidate_pairs
    rng = random.Random(count)
    base = rng.getrandbits(bits)
    # near copies of one hash, so some pairs are close and some aren't
    hashes = [base ^ sum(1 << rng.randrange(bits) for _ in range(rng.randrange(20))) for _ in range(count)]
    expected = {(i, j) for i in range(count) for j in range(i + 1, count)
                if bin(hashes[i] ^ hashes[j]).count("1") <= distance}
    assert _candidate_pairs(hashes, bits, distance) == expected
    assert count < 8 or expected
