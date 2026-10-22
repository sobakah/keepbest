"""The browser page (--web): the flow answered over HTTP, the server's guards,
the folder picker, the lock against two keepbests on one folder and the
app-menu entry. No browser: the tests talk to the API as the page does."""

import http.cookiejar
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
from conftest import REPO
from keepbest import front, running, web
from keepbest.config import load_config


class Page:
    """What the page's script does: open the printed address, then use the API."""

    def __init__(self, url):
        self.base = url.split("/?")[0]
        self.port = int(self.base.rsplit(":", 1)[1])
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.open(url).read()

    def get(self, path):
        return json.loads(self.opener.open(self.base + path).read())

    def post(self, path, body=None, headers=None):
        data = json.dumps(body if body is not None else {}).encode()
        request = urllib.request.Request(self.base + path, data, {
            "Content-Type": "application/json", "Origin": self.base, **(headers or {})})
        return json.loads(self.opener.open(request).read())

    def question(self, after=0, timeout=30):
        """The next open question with an id above *after* (None once finished)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.get("/api/state")
            q = state["question"]
            if q and q["id"] > after:
                return q
            if state["finished"] is not None:
                return None
            time.sleep(0.05)
        raise AssertionError("no question came")

    def answer(self, q, value):
        self.post("/api/answer", {"id": q["id"], "value": value})

    def walk(self, *answers):
        """Answer the next questions in turn; returns them."""
        asked, last = [], 0
        for value in answers:
            q = self.question(last)
            assert q is not None, f"finished before {value!r}"
            asked.append(q)
            self.answer(q, value)
            last = q["id"]
        return asked

    def events(self, kind=None):
        return [e for e in self.get("/api/state")["events"] if kind is None or e["kind"] == kind]


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


# --- starting without a folder (the app menu) and the folder picker ---------------------
def test_without_a_folder_the_page_asks_for_one(library, serve, tmp_path):
    visual_pair(library)
    page, result = serve(None)
    q = page.question()
    assert q["kind"] == "path"
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
    assert f'Exec={sys.executable} "{launcher}" --web %f' in text and f"Icon={icon}" in text
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
    assert result.returncode == 0 and entry.is_file() and "--web %f" in entry.read_text()
    assert run_cli(library.path, "--remove-desktop-entry").returncode == 0 and not entry.exists()
