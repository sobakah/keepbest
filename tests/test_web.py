"""The browser page (--web): the flow answered over HTTP, the server's guards,
the folder picker, the lock against two keepbests on one folder and the
app-menu entry. No browser: the tests talk to the API as the page does."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import pictures
from conftest import REPO, Page, make_video, remux, requires_ffmpeg
from keepbest import front, running, web
from keepbest.config import load_config


def status(opener_or_none, url, method="GET", headers=None, data=None):
    """HTTP status of a request (urllib raises for errors)."""
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with (opener_or_none or urllib.request.build_opener()).open(request) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def serve(config_file):
    """Run keepbest with the page in this process: serve(folder, settings...) -> (Page, result).
    result["code"] is the exit code once the run has ended."""
    from keepbest import app
    threads = []

    def start(folder, recursive=False, confirm="uncertain", **config_values):
        config_file(config_values)
        config, _ = load_config(config_file.path)
        config["_configured_delete_mode"], config["_cli_dry_run"] = config["delete_mode"], False
        settings = {"stages": "1,2,3", "recursive": recursive, "no_ignore": False, "videos": True, "confirm": confirm}
        urls, result = [], {}

        def run():
            result["code"] = web.serve(lambda: app.run(folder, config, settings, auto=False),
                                       open_browser=False, announce=urls.append)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
        while not urls:
            time.sleep(0.01)
        result["thread"] = thread
        return Page(re.search(r"http://\S+", urls[0]).group(0)), result

    yield start
    front.use(None)
    for thread in threads:
        thread.join(timeout=10)


def visual_pair(library):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)


def finish(page, result):
    page.post("/api/close")
    result["thread"].join(timeout=10)
    return result.get("code")


# --- the flow on the page ---------------------------------------------------------------
def test_a_run_on_the_page(library, serve):
    visual_pair(library)
    library.copy_of("a.png", "b.png")
    page, result = serve(library.path, confirm="always")
    start, decision, overview, end = page.walk("", "", "", "q")
    assert start["screen"] == "StartScreen" and start["primary"].startswith("Start scan")
    assert decision["screen"] == "DecisionScreen"
    assert [i["name"] for i in decision["group"]["images"]] == ["a.png", "a small.jpg"]
    assert overview["screen"] == "OverviewScreen" and end["screen"] == "EndScreen"
    assert finish(page, result) == 0
    assert library.files() == ["a.png"]
    kinds = {e["kind"] for e in page_events_after(page)}
    assert {"banner", "exact_heading", "exact_table", "outcome", "summary", "answer"} <= kinds


def page_events_after(page):
    try:
        return page.events()
    except urllib.error.URLError:  # the server is gone after close
        return page.last_events


@pytest.fixture(autouse=True)
def _keep_last_events(monkeypatch):
    """Page.events after close: the server is gone, so remember the last answer."""
    original = Page.get

    def get(self, path):
        data = original(self, path)
        if path.startswith("/api/state"):
            self.last_events = data["events"]
        return data
    monkeypatch.setattr(Page, "get", get)


def test_a_setting_is_chosen_directly(library, serve):
    visual_pair(library)
    page, result = serve(library.path)
    first = page.question()
    strictness = next(r for r in first["rows"] if r.get("key") == "2")
    assert [o["label"] for o in strictness["options"]] == ["strict (12)", "normal (20)", "loose (28)"]
    page.answer(first, {"set": ["2", 2]})
    again = page.question(first["id"])
    strictness = next(r for r in again["rows"] if r.get("key") == "2")
    assert [o["current"] for o in strictness["options"]] == [False, False, True]
    page.answer(again, "q")
    assert finish(page, result) == 0


def test_answers_are_checked(library, serve):
    visual_pair(library)
    page, result = serve(library.path)
    q = page.question()
    for wrong in ("x", 3, {"set": ["2", 7]}, {"set": ["c", 0]}):
        with pytest.raises(urllib.error.HTTPError) as caught:
            page.answer(q, wrong)
        assert caught.value.code == 400
    page.answer(q, "q")
    with pytest.raises(urllib.error.HTTPError) as caught:   # a second click on the same question
        page.answer(q, "")
    assert caught.value.code == 409
    assert finish(page, result) == 0


def test_closing_the_page_stops_at_the_question_and_keeps_the_session(library, serve, monkeypatch):
    monkeypatch.setattr(web, "CLOSE_GRACE", 0.2)
    visual_pair(library)
    library.photo("c.png", 3, size=(640, 480))
    library.variant("c.png", "c small.jpg", change=pictures.downscaled)
    page, result = serve(library.path, confirm="always")
    page.walk("", "")                                   # start; keep in the first group
    assert page.question(2)["screen"] == "DecisionScreen"
    page.post("/api/closing", headers={"Content-Type": "text/plain"})
    result["thread"].join(timeout=15)
    assert result["code"] == 130
    assert library.files() == ["a.png", "c small.jpg", "c.png"]
    # The next start offers the rest of the session.
    page, result = serve(library.path, confirm="always")
    start = page.question()
    assert start["panel"]["open"] == 1 and start["primary"].startswith("Resume")
    page.answer(start, "q")
    assert finish(page, result) == 0


def test_permanent_needs_a_yes_on_the_page(library, serve):
    visual_pair(library)
    page, result = serve(library.path, confirm="always", delete_mode="permanent")
    start, permanent = page.walk("", False)              # start, then "Delete permanently?" - no
    assert permanent["kind"] == "confirm" and permanent["tone"] == "danger" and permanent["default"] is False
    page.answer(page.question(permanent["id"]), "q")
    assert finish(page, result) == 0
    assert library.files() == ["a small.jpg", "a.png"]


def test_folder_list_on_the_page(library, serve):
    library.photo("a.png", 1)
    library.copy_of("a.png", "sub/a.png")
    library.copy_of("a.png", "sub/deeper/a.png")
    page, result = serve(library.path, recursive=True)
    start, folders, along = page.walk("f", "1", False)
    assert folders["screen"] == "FolderScreen"
    assert [(e["key"], e["name"], e["depth"]) for e in folders["entries"]] == [("1", "sub", 0), ("2", "deeper", 1)]
    assert along["kind"] == "confirm" and along["prompt"].startswith("Also switch")
    folders = page.question(along["id"])
    assert [e["on"] for e in folders["entries"]] == [False, True]
    page.answer(folders, "")
    page.answer(page.question(folders["id"]), "q")
    assert finish(page, result) == 0


# --- pictures in the group view ---------------------------------------------------------
def test_the_group_view_has_previews_and_the_square(library, serve):
    from PIL import Image
    library.photo("a.png", 1, size=(800, 600))
    library.variant("a.png", "a stamped.png", change=lambda img: pictures.with_patch(pictures.downscaled(img, 0.9)))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)
    page, result = serve(library.path, confirm="always", strictness="loose", max_pixel_diff=255)
    start, decision = page.walk("", "s")
    group = decision["group"]
    assert [i["name"] for i in group["images"]][0] == "a.png"
    assert group["images"][0]["spot"] is None and all(len(i["spot"]) == 3 for i in group["images"][1:])
    worst = max(group["images"][1:], key=lambda i: i["diff"])
    assert worst["name"] == "a stamped.png" and group["spot"] == worst["spot"]
    left, top, size = group["spot"]
    assert 0.1 <= left + size / 2 <= 0.22 and 0.1 <= top + size / 2 <= 0.2
    for image in group["images"]:
        data = page.opener.open(f"{page.base}/api/preview/{image['preview']}").read()
        with Image.open(io.BytesIO(data)) as shown:
            assert shown.format == "JPEG" and max(shown.size) <= 1200
    zoom = page.opener.open(f"{page.base}/api/preview/{group['images'][0]['preview']}?size=zoom").read()
    assert Image.open(io.BytesIO(zoom)).size == (800, 600)    # never larger than the picture
    assert status(page.opener, f"{page.base}/api/preview/nosuchid") == 404
    assert status(page.opener, f"{page.base}/api/preview/{group['images'][0]['preview']}?size=huge") == 400
    assert status(None, f"{page.base}/api/preview/{group['images'][0]['preview']}") == 403
    overview = page.question(decision["id"])
    page.answer(overview, "q")
    save = page.question(overview["id"])
    assert save["kind"] == "confirm" and save["prompt"].startswith("Save progress")
    page.answer(save, False)
    assert finish(page, result) == 0


def test_a_preview_of_a_removed_file_says_so(library, serve):
    visual_pair(library)
    page, result = serve(library.path, confirm="always")
    start, decision = page.walk("", "")                       # keep a.png: "a small.jpg" goes to the trash
    gone = decision["group"]["images"][1]["preview"]
    page.question(decision["id"])                              # the deletion is done once the next question comes
    assert status(page.opener, f"{page.base}/api/preview/{gone}") == 404
    assert status(page.opener, f"{page.base}/api/file/{gone}") == 404
    page.walk("", "q")
    assert finish(page, result) == 0


def test_the_page_says_why_a_file_is_kept(library, serve):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)     # fewer pixels
    library.variant("a.png", "a.jpg", quality=95)                            # as many, but lossy
    page, result = serve(library.path, confirm="always")
    start, decision = page.walk("", "s")
    beaten = {i["name"]: i["beaten"] for i in decision["group"]["images"]}
    assert beaten == {"a.png": None, "a small.jpg": "resolution", "a.jpg": "lossless"}
    finish(page, result)


@pytest.mark.parametrize("dry", [False, True])
def test_only_files_still_on_disk_can_be_opened(library, serve, dry):
    """After a real run only the kept file of a group can be opened or shown in its
    folder; after a dry run every file can."""
    visual_pair(library)
    library.copy_of("a.png", "b.png")                                         # Stage 1: an identical copy
    page, result = serve(library.path, confirm="always", delete_mode="dry_run" if dry else "trash")
    start, decision = page.walk("", "")                                       # keep a.png
    assert all(image["file"] for image in decision["group"]["images"])       # all there before deciding
    overview = page.question(decision["id"])
    page.answer(overview, "1")                                               # the group again
    handled = page.question(overview["id"])
    assert handled["screen"] == "HandledScreen"
    assert [bool(i["file"]) for i in handled["group"]["images"]] == [True, dry]
    assert [bool(i["preview"]) for i in handled["group"]["images"]] == [True, dry]   # no request for a gone file
    (table,) = page.events("exact_table")
    assert [bool(f["file"]) for f in table["group"]["files"]] == [True, dry]
    finish(page, result)


def test_a_file_opens_on_its_own_and_in_its_folder(library, serve, monkeypatch):
    from PIL import Image
    from keepbest import viewer
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.tif", change=pictures.downscaled)    # no browser shows TIFF
    shown_in_folder = []
    monkeypatch.setattr(viewer, "show_in_folder", lambda path: shown_in_folder.append(path) or "the file manager")
    page, result = serve(library.path, confirm="always")
    start, decision = page.walk("", "s")
    png, tif = (image["file"] for image in decision["group"]["images"])
    with page.opener.open(f"{page.base}/api/file/{png}") as response:       # as it is
        assert response.headers["Content-Type"] == "image/png"
        assert response.read() == (library / "a.png").read_bytes()
    with page.opener.open(f"{page.base}/api/file/{tif}") as response:       # rendered, full size
        assert response.headers["Content-Type"] == "image/jpeg"
        assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
        with Image.open(io.BytesIO(response.read())) as rendered, Image.open(library / "a small.tif") as original:
            assert rendered.size == original.size
    assert status(page.opener, f"{page.base}/api/file/nosuchid") == 404
    assert status(None, f"{page.base}/api/file/{png}") == 403
    assert page.post("/api/reveal", {"id": tif}) == {"how": "the file manager"}
    assert shown_in_folder == [library / "a small.tif"]
    reveal = f"{page.base}/api/reveal"
    json_post = {"Content-Type": "application/json", "Origin": page.base}
    assert status(page.opener, reveal, "POST", json_post, b"{}") == 400
    assert status(page.opener, reveal, "POST", json_post, b'{"id": "nosuchid"}') == 404
    assert status(page.opener, reveal, "POST", {"Content-Type": "application/json"}, json.dumps({"id": png}).encode()) == 403
    assert shown_in_folder == [library / "a small.tif"]
    finish(page, result)


@pytest.mark.parametrize("make", ["16-bit", "cmyk", "rotated", "mpo"])
def test_previews_of_pictures_a_browser_cant_show(tmp_path, make):
    """Previews are JPEGs rendered like the comparison sees the picture."""
    from PIL import Image
    from keepbest.image_utils import preview_jpeg
    path = tmp_path / "picture"
    if make == "16-bit":
        Image.linear_gradient("L").resize((300, 200)).convert("I").point(lambda v: v * 257).convert("I;16").save(path.with_suffix(".png"))
        expected = (300, 200)
    elif make == "cmyk":
        pictures.photo(1, (300, 200)).convert("CMYK").save(path.with_suffix(".jpg"))
        expected = (300, 200)
    elif make == "rotated":
        exif = Image.Exif()
        exif[0x0112] = 6
        pictures.photo(1, (300, 200)).save(path.with_suffix(".jpg"), exif=exif)
        expected = (200, 300)                                 # shown upright
    else:
        first, second = pictures.photo(1, (300, 200)), pictures.photo(2, (300, 200))
        first.save(path.with_suffix(".mpo"), format="MPO", save_all=True, append_images=[second])
        expected = (300, 200)
    source = next(tmp_path.glob("picture.*"))
    with Image.open(io.BytesIO(preview_jpeg(source, 1200))) as shown:
        assert shown.format == "JPEG" and shown.mode == "RGB" and shown.size == expected


def test_closing_while_a_question_is_open_stops_keepbest(library, serve):
    visual_pair(library)
    page, result = serve(library.path)
    page.question()
    page.post("/api/close")
    result["thread"].join(timeout=10)
    assert result["code"] == 130


# --- the same decisions on the page and in the terminal ------------------------------------
def _session(folder):
    from keepbest.session import SessionStore
    text = json.dumps(SessionStore(folder).load(), sort_keys=True).replace(str(folder), "<folder>")
    data = json.loads(text)
    for key in ("created", "updated"):
        data.pop(key, None)
    return data


def test_page_and_terminal_save_the_same_session(library, serve, run_cli, tmp_path):
    """The page sends the keys the terminal reads, so the same answers must leave the
    same saved session behind (here a dry run, kept to be carried out later)."""
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)
    library.variant("a.png", "a q50.jpg", quality=50)
    library.photo("b.png", 2, size=(640, 480))
    library.variant("b.png", "b small.jpg", change=pictures.downscaled)
    library.photo("c.png", 3)
    library.copy_of("c.png", "c (1).png")
    terminal = tmp_path / "terminal"
    shutil.copytree(library.path, terminal, copy_function=shutil.copy2)
    keys = ["4", "", "2", "i", "", "q"]          # dry run; start; keep #2 in group 1; group 2 not duplicates; finish; quit
    result = run_cli(terminal, "-i", keys=keys)
    assert result.returncode == 0, result.stdout
    page, done = serve(library.path, confirm="always")
    page.walk(*keys)
    assert finish(page, done) == 0
    assert _session(library.path) == _session(terminal)
    assert _session(library.path)["finished"] is True


def test_a_saved_dry_run_is_carried_out_from_the_page(library, serve, run_cli):
    visual_pair(library)
    library.photo("c.png", 3)
    library.copy_of("c.png", "c (1).png")
    run_cli(library.path, "--dry-run", keys=["", ""])            # dry run, saved
    assert len(library.files()) == 4
    page, done = serve(library.path)
    start = page.question()
    assert start["panel"]["finished"] is True and start["primary"] == "Open the saved dry run (changes nothing)"
    sure = page.walk("a")[0]
    sure = page.question(sure["id"])
    assert sure["kind"] == "confirm" and sure["default"] is False and "move 2 file(s) to the trash" in sure["prompt"]
    page.answer(sure, True)
    page.answer(page.question(sure["id"]), "q")
    assert finish(page, done) == 0
    assert library.files() == ["a.png", "c.png"]


@requires_ffmpeg
def test_remuxed_videos_on_the_page(library, serve):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.mkv")
    page, result = serve(library.path, confirm="always")
    start, remuxes = page.walk("", "s")
    assert remuxes["screen"] == "RemuxScreen"
    files = remuxes["group"]["files"]
    assert [f["name"] for f in files] == ["clip.mp4", "clip.mkv"] or [f["name"] for f in files] == ["clip.mkv", "clip.mp4"]
    mp4 = next(f for f in files if f["name"] == "clip.mp4")
    url = f"{page.base}/api/media/{mp4['media']}"
    size = (library / "clip.mp4").stat().st_size
    with page.opener.open(url) as answer:
        assert answer.status == 200 and answer.headers["Content-Type"] == "video/mp4"
        assert answer.headers["Accept-Ranges"] == "bytes" and len(answer.read()) == size
    request = urllib.request.Request(url, headers={"Range": "bytes=10-109"})
    with page.opener.open(request) as answer:
        assert answer.status == 206 and answer.headers["Content-Range"] == f"bytes 10-109/{size}"
        assert answer.read() == (library / "clip.mp4").read_bytes()[10:110]
    request = urllib.request.Request(url, headers={"Range": "bytes=-50"})
    with page.opener.open(request) as answer:
        assert answer.read() == (library / "clip.mp4").read_bytes()[-50:]
    assert status(page.opener, url, headers={"Range": f"bytes={size}-"}) == 416
    assert status(None, url) == 403
    assert status(page.opener, f"{page.base}/api/media/unknown") == 404
    page.answer(page.question(remuxes["id"]), "q")
    assert finish(page, result) == 0


def test_an_answer_given_just_before_the_page_closes_counts():
    """Closing the page right after answering ("don't save progress", then the
    tab closed) stops the flow at its next question, not at the one answered."""
    front_end, got = web.WebFront(echo=False), {}

    def flow():
        try:
            got["answer"] = front_end.confirm("Save progress?", default=True)
        except web.Cancelled:
            got["answer"] = "stopped"
    thread = threading.Thread(target=flow)
    thread.start()
    while front_end.question is None:
        time.sleep(0.01)
    with front_end._cond:   # the flow can only pick the answer up once the page is gone
        front_end.answer(front_end.question["id"], False)
        front_end.page_gone()
    thread.join(timeout=10)
    assert got["answer"] is False
    with pytest.raises(web.Cancelled):
        front_end.confirm("The next question?")


def test_every_step_of_stage_3_counts_on_the_page(library, serve):
    """Comparing the pixels of many candidate pairs takes long: it has a counter
    of its own, which ends at the number of candidate pairs."""
    visual_pair(library)
    library.variant("a.png", "a again.jpg", quality=85)
    library.variant("a.png", "a text.jpg", pictures.with_text, quality=90)  # stays out of the group
    page, result = serve(library.path, confirm="always")
    start, decision = page.walk("", "q")
    assert decision["screen"] == "DecisionScreen"
    bars = {e["label"]: e for e in page.events("progress")}
    assert list(bars)[-4:] == ["Analyzing images", "Finding candidates", "Loading pictures", "Comparing pixels"]
    assert all(not e["running"] and e["done"] == e["total"] for e in bars.values()), bars
    analyzed = next(e["text"] for e in page.events() if e.get("text", "").startswith("Analyzed"))
    assert f"· {bars['Comparing pixels']['total']} candidate pair(s)" in analyzed
    assert bars["Comparing pixels"]["total"] > 2   # more pairs than those in the group
    page.answer(page.question(decision["id"]), False)          # don't keep the session
    assert finish(page, result) == 0


def test_the_overview_shows_each_groups_kept_picture(library, serve):
    from PIL import Image
    visual_pair(library)
    page, result = serve(library.path, confirm="always")
    start, decision = page.walk("", "t")
    overview = page.question(decision["id"])
    assert overview["screen"] == "OverviewScreen"
    [group] = overview["groups"]
    data = page.opener.open(f"{page.base}/api/preview/{group['preview']}?size=thumb").read()
    assert max(Image.open(io.BytesIO(data)).size) <= 480
    page.answer(overview, "q")
    page.answer(page.question(overview["id"]), False)          # don't keep the session
    assert finish(page, result) == 0


# --- starting without a folder (the app menu) and the folder picker ---------------------
def test_without_a_folder_the_page_asks_for_one(library, serve, tmp_path):
    visual_pair(library)
    page, result = serve(None)
    q = page.question()
    assert q["kind"] == "path" and q["start"] == ""               # the picker opens the pictures folder
    listing = page.get("/api/folders?path=" + urllib.request.quote(str(tmp_path)))
    names = [d["name"] for d in listing["dirs"]]
    assert "pictures" in names and "home" in names
    assert next(d for d in listing["dirs"] if d["name"] == "home")["protected"] is True
    page.answer(q, str(tmp_path / "home"))               # refused: the home folder
    again = page.question(q["id"])
    assert again["kind"] == "path"
    assert any("home folder" in e.get("text", "") for e in page.events("error"))
    page.answer(again, str(library.path))
    start = page.question(again["id"])
    assert start["screen"] == "StartScreen" and start["target"] == str(library.path)
    page.answer(start, "q")
    assert finish(page, result) == 0


def test_changing_the_folder_starts_in_the_current_one(library, serve, tmp_path):
    """The picker opens the folder being worked on, also on a page that was
    reloaded (after media-optimizer's page had the tab) and forgot the last one."""
    visual_pair(library)
    other = tmp_path / "other"
    other.mkdir()
    page, result = serve(library.path)
    start, change = page.walk("c", str(other))                       # no pictures there: asked again
    assert change["kind"] == "path" and change["start"] == str(library.path)
    again = page.question(change["id"])
    assert again["kind"] == "path" and again["start"] == str(library.path)
    page.answer(again, "")                                           # keep the current folder
    start = page.question(again["id"])
    assert start["screen"] == "StartScreen"
    page.answer(start, "q")
    assert finish(page, result) == 0


def test_identical_files_say_whether_they_are_videos(tmp_path):
    """The page plays a video below its row and shows a picture there."""
    from keepbest.core_logic import ExactGroup
    files = []
    for name in ("a.png", "b.png", "c.MP4"):
        (tmp_path / name).write_bytes(b"x")
        files.append({"path": tmp_path / name, "size": 1})
    group = web.exact_group(ExactGroup(files=files), lambda path: path.name, lambda path: "id-" + path.name)
    assert [(f["video"], f["file"]) for f in group["files"]] == [(False, "id-a.png"), (False, "id-b.png"), (True, "id-c.MP4")]


def test_a_removed_video_gets_no_player(tmp_path):
    from keepbest.core_logic import ExactGroup
    files = []
    for name in ("a.mp4", "b.mkv"):
        (tmp_path / name).write_bytes(b"x")
        files.append({"path": tmp_path / name, "size": 1})
    (tmp_path / "b.mkv").unlink()
    group = web.exact_group(ExactGroup(kind="remux", files=files), lambda path: path.name, lambda path: "id-" + path.name)
    assert [f["media"] for f in group["files"]] == ["id-a.mp4", None]


def test_the_folder_picker_lists_folders_only(tmp_path):
    folder = tmp_path / "pictures"
    for name in ("b", "A", ".hidden"):
        (folder / name).mkdir(parents=True)
    (folder / "x.jpg").write_bytes(b"")
    (folder / "notes.txt").write_text("")
    listing = web.list_folders(str(folder))
    assert [d["name"] for d in listing["dirs"]] == ["A", "b"]
    assert listing["mediaFiles"] == 1 and listing["parent"] == str(tmp_path)
    with pytest.raises(web.ApiError):
        web.list_folders(str(tmp_path / "missing"))


def test_the_picker_starts_in_the_pictures_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert web.pictures_folder() == home
    (home / "Bilder").mkdir()
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
    assert web.pictures_folder() == home / "Bilder"


# --- the server's guards ------------------------------------------------------------------
def test_only_the_page_with_the_token_gets_in(library, serve):
    visual_pair(library)
    page, result = serve(library.path)
    page.question()
    base, port = page.base, page.port
    cookie = next(c for c in page.jar)
    assert cookie.name == f"keepbest_token_{port}"     # the port in the name: pages on other ports keep theirs
    assert cookie.has_nonstandard_attr("HttpOnly")
    anonymous = None
    assert status(anonymous, base + "/") == 403
    assert status(anonymous, base + "/api/state") == 403
    assert status(anonymous, base + "/static/app.js") == 403
    assert status(anonymous, base + "/?token=wrong") == 403
    assert status(page.opener, base + "/api/state", headers={"Host": "evil.example:80"}) == 400   # DNS rebinding
    body = json.dumps({"id": 1, "value": "q"}).encode()
    assert status(page.opener, base + "/api/answer", "POST", {"Content-Type": "application/json"}, body) == 403
    assert status(page.opener, base + "/api/answer", "POST", {"Origin": base, "Content-Type": "text/plain"}, body) == 415
    assert status(page.opener, base + "/static/../web.py") == 404
    assert status(page.opener, base + "/static/%2e%2e/web.py") == 404
    assert status(page.opener, base + "/static/index.html") == 200
    page.answer(page.question(), "q")
    assert finish(page, result) == 0


def test_screen_keys_write_out_ranges():
    screen = front.OverviewScreen(prompt="Action: ", primary="Finish",
                                  menu=[("Navigate", [("1-3", "Jump to group"), ("q", "Quit")])],
                                  groups=[], current=None, rel=str)
    assert web.screen_keys(screen) == ["", "1", "2", "3", "q"]
    decision = front.DecisionScreen(prompt="", primary=None, menu=[("Resolve", [("i1-i2", "x"), ("1", "y")])],
                                    group=None, position="1/1", rel=str)
    assert web.screen_keys(decision) == ["i1", "i2", "1"]


# --- the command line -----------------------------------------------------------------------
def test_the_command_line_starts_the_page(library, config_file, tmp_path):
    visual_pair(library)
    argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--web", "--no-browser", str(library.path)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO)
    try:
        line = proc.stdout.readline()                       # printed and flushed at once, also into a pipe
        url = re.search(r"keepbest is running at (http://127\.0\.0\.1:\d+/\?token=[\w-]+)", line).group(1)
        page = Page(url)
        start, end = page.walk("", "q")                     # the pair isn't borderline: no question about it
        assert end["screen"] == "EndScreen"
        page.post("/api/close")
        assert proc.wait(timeout=30) == 0
    finally:
        proc.kill()
    assert library.files() == ["a.png"]


@pytest.fixture
def cli_page(config_file):
    """keepbest --web as a separate process (needed where it sets signal handlers,
    which only the main thread may): cli_page(folder, *args) -> (Page, process)."""
    procs = []

    def start(*args):
        argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--web", "--no-browser", *map(str, args)]
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO)
        procs.append(proc)
        url = re.search(r"http://\S+", proc.stdout.readline()).group(0)
        return Page(url), proc
    yield start
    for proc in procs:
        proc.kill()


def tool_event(page, running=False, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tools = [e for e in page.get("/api/state")["events"] if e["kind"] == "tool"]
        if tools and tools[-1]["running"] == running and (running is False or tools[-1]["lines"]):
            return tools[-1]
        time.sleep(0.05)
    raise AssertionError("no such tool event")


def test_media_optimizer_runs_on_the_page_without_questions(library, cli_page, fake_command, tmp_path):
    library.photo("a.png", 1)
    library.copy_of("a.png", "a copy.png")
    env = tmp_path / "optimizer.env"
    command = fake_command("media-optimizer", script="[ \"$2\" = --help ] && exit 0\n"   # no page of its own
                                                     f"echo \"$MO_CALLER $MO_SUBFOLDERS\" > '{env}'\n"
                                                     "printf 'converting\\033[1;32m a.png\\033[0m\\n'\necho done")
    page, proc = cli_page(library.path)
    start, end = page.walk("", "m")
    offer = dict(end["menu"][0][1])["m"]
    assert "no questions" in offer
    tool = tool_event(page)
    assert tool["name"] == "media-optimizer" and tool["code"] == 0
    assert tool["lines"] == ["converting a.png", "done"]          # colours taken out
    assert command.calls() == [["run", str(library.path), "-y"]]
    assert env.read_text().split() == ["keepbest", "exclude"]
    again = page.question(end["id"])
    assert again["screen"] == "EndScreen" and "m" not in again["keys"]   # once per run, as in the terminal
    page.answer(again, "q")
    page.post("/api/close")
    assert proc.wait(timeout=20) == 0
    assert len(library.files()) == 1


def test_media_optimizer_can_be_stopped_from_the_page(library, cli_page, fake_command, tmp_path):
    library.photo("a.png", 1)
    library.copy_of("a.png", "a copy.png")
    got = tmp_path / "got-int"
    fake_command("media-optimizer", script="[ \"$2\" = --help ] && exit 0\n"
                                           f"trap 'echo INT > \"{got}\"; exit 130' INT\necho started\nsleep 20 & wait")
    page, proc = cli_page(library.path)
    start, end = page.walk("", "m")
    tool_event(page, running=True)
    assert page.post("/api/stop-tool") == {"stopped": True}
    tool = tool_event(page, timeout=5)        # not waiting for the "sleep" that still holds the pipe
    assert tool["code"] == 130 and got.read_text().strip() == "INT"
    page.answer(page.question(end["id"]), "q")                   # keepbest itself goes on
    page.post("/api/close")
    assert proc.wait(timeout=20) == 0


def test_the_terminal_screens_are_gone(library, run_cli):
    result = run_cli(library.path, "--terminal")
    assert result.returncode == 2 and "there are no terminal screens" in result.stderr


def test_without_a_display_the_page_only_prints_its_address(library, config_file, fake_command):
    """Over SSH or on a console no browser is opened; the address is there to forward."""
    visual_pair(library)
    browser = fake_command("xdg-open")
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["SSH_CONNECTION"] = "10.0.0.2 50000 10.0.0.1 22"
    argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), str(library.path)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO, env=env)
    try:
        lines = [proc.stdout.readline() for _ in range(3)]
    finally:
        proc.kill()
    port = re.search(r"127\.0\.0\.1:(\d+)", lines[0]).group(1)
    assert lines[1] == "Open that address in your browser.\n"
    assert f"ssh -L {port}:127.0.0.1:{port}" in lines[2]
    assert browser.calls(count=0) == []


def test_the_page_keeps_a_log_in_the_terminal(library, run_cli):
    library.photo("a.png", 1)
    library.copy_of("a.png", "a copy.png")
    result = run_cli(library.path, keys=["", "q"])
    assert result.stdout.startswith("keepbest is running at http://127.0.0.1:")
    assert "Stage 1 · Identical files" in result.stdout and "Exact copies removed: 1" in result.stdout
    assert [q["screen"] for q in result.questions] == ["StartScreen", "EndScreen"]


def test_with_a_display_the_page_comes(library, config_file):
    visual_pair(library)
    env = {**os.environ, "WAYLAND_DISPLAY": "wayland-0"}
    argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--no-browser", str(library.path)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO, env=env)
    try:
        assert "keepbest is running at http://127.0.0.1:" in proc.stdout.readline()
    finally:
        proc.kill()


def test_web_and_auto_dont_go_together(library, run_cli):
    result = run_cli(library.path, "--web", "--auto")
    assert result.returncode == 2 and "--web and --auto" in result.stderr


# --- one keepbest per folder ------------------------------------------------------------------
def _other_keepbest(tmp_path, folder, pid, address=None):
    registry = tmp_path / "state" / "running"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / f"{pid}.json").write_text(json.dumps({"pid": pid, "folder": str(folder), "address": address}))


@pytest.mark.skipif(running.fcntl is None, reason="no lock on Windows")
@pytest.mark.parametrize("where", ["same", "inside", "above"])
def test_a_folder_another_keepbest_works_on_is_refused(library, run_cli, tmp_path, where):
    library.photo("a.png", 1)
    library.copy_of("a.png", "sub/b.png")
    other = {"same": library.path, "inside": library.path / "sub", "above": library.path.parent}[where]
    _other_keepbest(tmp_path, other, os.getpid(), "http://127.0.0.1:1/?token=x")   # the test itself: alive
    result = run_cli(library.path, "-r", "--auto")
    assert result.returncode == 2
    assert f"process {os.getpid()}" in result.stdout and "http://127.0.0.1:1/?token=x" in result.stdout
    assert library.files() == ["a.png", "sub/b.png"]


@pytest.mark.skipif(running.fcntl is None, reason="no lock on Windows")
def test_a_finished_keepbest_doesnt_block(library, run_cli, tmp_path):
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _other_keepbest(tmp_path, library.path, dead.pid)
    _other_keepbest(tmp_path, tmp_path / "elsewhere", os.getpid())
    assert run_cli(library.path, "--auto").returncode == 0
    assert library.files() == ["a.png"]
    assert sorted(p.name for p in (tmp_path / "state" / "running").glob("*.json")) == [f"{os.getpid()}.json"]


@pytest.mark.skipif(running.fcntl is None, reason="no lock on Windows")
def test_the_page_asks_for_another_folder_when_one_is_taken(library, serve, tmp_path):
    visual_pair(library)
    _other_keepbest(tmp_path, library.path, os.getppid())
    page, result = serve(library.path)
    q = page.question()
    assert q["kind"] == "path"
    assert any("Another keepbest" in e.get("text", "") for e in page.events("error"))
    page.post("/api/quit")
    assert finish(page, result) == 130


# --- the app-menu entry -------------------------------------------------------------------------
from keepbest import desktop  # noqa: E402


def test_the_desktop_entry_opens_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    launcher = tmp_path / "check out" / "run.py"
    launcher.parent.mkdir()
    launcher.write_text("")
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(desktop, "_refresh", lambda folder: None)
    entry, icon, command = desktop.install()
    assert command == [sys.executable, str(launcher)]
    text = entry.read_text()
    assert f'Exec={sys.executable} "{launcher}" %f' in text and f"Icon={icon}" in text
    assert "Terminal=false" in text and "MimeType=inode/directory;" in text
    assert icon.read_text().startswith("<svg")
    if shutil.which("desktop-file-validate"):
        checked = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
        assert checked.returncode == 0 and not checked.stdout.strip(), checked.stdout
    assert desktop.remove() == [entry, icon] and not entry.exists() and desktop.remove() == []


def test_desktop_exec_arguments_are_quoted_and_escaped():
    assert desktop.exec_argument("/usr/bin/keepbest") == "/usr/bin/keepbest"
    assert desktop.exec_argument("/home/a b/run.py") == '"/home/a b/run.py"'
    assert desktop.exec_argument("/p/100%") == "/p/100%%"
    assert desktop.exec_argument("/x/$y") == '"/x/\\\\$y"'


def test_the_command_line_installs_the_entry(run_cli, tmp_path, library):
    result = run_cli(library.path, "--install-desktop-entry")
    entry = tmp_path / "home" / ".local" / "share" / "applications" / "keepbest.desktop"
    assert result.returncode == 0 and entry.is_file() and "run.py %f" in entry.read_text()
    assert run_cli(library.path, "--remove-desktop-entry").returncode == 0 and not entry.exists()
