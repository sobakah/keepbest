"""A newer keepbest: the check (git tags, once a day), the command for each
kind of install, installing it from the page and starting again in the same
process and tab. The releases are a local repository, never GitHub."""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error

import pytest

import pictures
from conftest import REPO, Page, releases
from keepbest import front, selfupdate, web
from keepbest.config import __version__, load_config

MAJOR = int(__version__.split(".")[0])
NEWER = f"{MAJOR + 1}.0.0"


@pytest.fixture
def newer_release(tmp_path, monkeypatch):
    """GitHub has a release newer than this keepbest."""
    url = releases(tmp_path / "releases", "0.5.1", __version__, NEWER, "not-a-version", "9.9")
    monkeypatch.setenv(selfupdate.REPO_ENV, url)
    return url


def write_metadata(prefix, package_or_url, injected=(), venv_args=(), pinned=False):
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "pipx_metadata.json").write_text(json.dumps({
        "main_package": {"package": "keepbest", "package_or_url": package_or_url, "pip_args": [],
                         "pinned": pinned},
        "injected_packages": {name: {"package": name, "package_or_url": name} for name in injected},
        "venv_args": list(venv_args)}))
    return prefix


# --- the check ----------------------------------------------------------------------
def test_the_newest_release_tag_counts(newer_release):
    assert selfupdate.latest() == NEWER
    assert selfupdate.newer(NEWER) and not selfupdate.newer(__version__) and not selfupdate.newer("0.5.1")


def test_a_repository_that_cant_be_read_is_an_error(tmp_path):
    with pytest.raises(selfupdate.UpdateError):
        selfupdate.latest(str(tmp_path / "missing"))
    with pytest.raises(selfupdate.UpdateError, match="no releases"):
        selfupdate.latest(releases(tmp_path / "none"))


def test_github_is_asked_once_a_day_and_the_answer_kept(newer_release, monkeypatch):
    asked = []
    real = selfupdate.latest
    monkeypatch.setattr(selfupdate, "latest", lambda: asked.append(1) or real())
    now = time.time()
    assert selfupdate.available(now) == NEWER
    assert selfupdate.available(now + 3600) == NEWER        # remembered, not asked again
    assert len(asked) == 1
    assert selfupdate.available(now + 25 * 3600) == NEWER
    assert len(asked) == 2


def test_a_failed_check_is_quiet_and_tried_again(tmp_path, monkeypatch):
    monkeypatch.setenv(selfupdate.REPO_ENV, str(tmp_path / "missing"))
    assert selfupdate.available() is None
    assert not (tmp_path / "state" / selfupdate.STATE_FILE).exists()


def test_no_newer_release_offers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(selfupdate.REPO_ENV, releases(tmp_path / "releases", "0.5.1", __version__))
    assert selfupdate.available() is None


# --- how to update ------------------------------------------------------------------
def test_a_pipx_install_from_the_repository_is_upgraded(tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate.shutil, "which", lambda name: f"/bin/{name}")
    venv = write_metadata(tmp_path / "venv", "git+https://github.com/sobakah/keepbest.git", injected=["pillow-jxl-plugin"])
    how = selfupdate.how_installed(NEWER, venv)
    assert how.kind == "pipx" and how.command == "pipx upgrade keepbest"
    assert how.commands == [["/bin/pipx", "upgrade", "keepbest"]]  # pipx keeps what was injected


@pytest.mark.parametrize("spec", ["git+https://github.com/sobakah/keepbest.git@1.0.0",
                                  "keepbest[jxl] @ git+https://github.com/sobakah/keepbest.git@1.0.0"])
def test_a_pinned_install_moves_to_the_new_release_and_injects_again(tmp_path, monkeypatch, spec):
    monkeypatch.setattr(selfupdate.shutil, "which", lambda name: f"/bin/{name}")
    venv = write_metadata(tmp_path / "venv", spec, injected=["pillow-jxl-plugin"], venv_args=["--system-site-packages"])
    how = selfupdate.how_installed(NEWER, venv)
    assert how.commands == [["/bin/pipx", "install", "--force", "--system-site-packages", spec.replace("1.0.0", NEWER)],
                            ["/bin/pipx", "inject", "keepbest", "pillow-jxl-plugin"]]
    assert how.command.splitlines()[1] == "pipx inject keepbest pillow-jxl-plugin"


def test_an_install_held_back_with_pipx_pin_only_gets_the_command(tmp_path):
    how = selfupdate.how_installed(NEWER, write_metadata(tmp_path / "venv", "keepbest", pinned=True))
    assert how.commands is None and "pipx unpin keepbest" in how.command


def test_a_checkout_and_other_installs_get_the_command_only(tmp_path):
    how = selfupdate.how_installed(NEWER, tmp_path / "no-venv")
    assert how.kind == "checkout" and how.commands is None
    assert how.command == f"git -C {REPO} pull"
    package = tmp_path / "site-packages" / "keepbest"
    package.mkdir(parents=True)
    how = selfupdate.how_installed(NEWER, tmp_path / "no-venv", package)
    assert how.kind == "other" and how.commands is None and "pipx install" in how.command


def test_installing_stops_at_the_first_failure_and_keeps_a_log(tmp_path):
    log = tmp_path / "update.log"
    code, tail = selfupdate.install([["sh", "-c", "echo one"], ["sh", "-c", "echo broke >&2; exit 3"],
                                     ["sh", "-c", f"touch {tmp_path / 'never'}"]], log)
    assert code == 3 and tail.splitlines()[-1] == "broke"
    assert "echo one" in log.read_text() and not (tmp_path / "never").exists()
    assert selfupdate.install([["sh", "-c", "true"]], log)[0] == 0


def test_starting_again_uses_the_same_command(monkeypatch, tmp_path):
    script = tmp_path / "bin" / "keepbest"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(script), "--dry-run", "pics"])
    assert selfupdate.restart_argv() == [str(script), "--dry-run", "pics"]
    monkeypatch.setattr(sys, "argv", [str(REPO / "keepbest" / "__main__.py"), "pics"])
    assert selfupdate.restart_argv() == [sys.executable, "-m", "keepbest", "pics"]
    monkeypatch.setattr(sys, "argv", ["./run.py", "pics"])
    assert selfupdate.restart_argv() == [sys.executable, "./run.py", "pics"]


# --- on the page --------------------------------------------------------------------
@pytest.fixture
def serve(config_file, monkeypatch, newer_release):
    """keepbest's page in this process with a newer release and a pipx install
    (stand-ins for pipx and for starting again): -> (Page, result, calls)."""
    from keepbest import app
    calls = {"installed": [], "restarted": []}
    how = selfupdate.Install("pipx", "pipx upgrade keepbest", [["pipx", "upgrade", "keepbest"]])
    monkeypatch.setattr(selfupdate, "how_installed", lambda version: how)
    outcome = {"code": 0}

    def install(commands, log):
        calls["installed"].append(commands)
        return outcome["code"], outcome.get("tail", "")
    monkeypatch.setattr(selfupdate, "install", install)
    monkeypatch.setattr(selfupdate, "restart", lambda argv: calls["restarted"].append(
        (argv, os.environ.pop(web.RESTART_ENV, None))))
    threads = []

    def start(folder, fail=False):
        outcome.update(code=1 if fail else 0, tail="pip: no such tag" if fail else "")
        config, _ = load_config(config_file({}))
        config["_configured_delete_mode"], config["_cli_dry_run"] = config["delete_mode"], False
        settings = {"stages": "1,2,3", "recursive": False, "no_ignore": False, "videos": True, "confirm": "always"}
        urls, result = [], {}

        def run():
            result["code"] = web.serve(lambda: app.run(folder, config, settings, auto=False),
                                       open_browser=False, announce=urls.append, check_updates=True)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
        while not urls:
            time.sleep(0.01)
        result["thread"], result["said"] = thread, urls
        return Page(re.search(r"http://\S+", urls[0]).group(0)), result, calls

    yield start
    front.use(None)
    for thread in threads:
        thread.join(timeout=10)


def offered(page, state="available", ready=True, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        update = page.get("/api/state")["update"]
        if update and update["state"] == state and update["ready"] == ready:
            return update
        time.sleep(0.05)
    raise AssertionError(f"no update in state {state}: {page.get('/api/state')['update']}")


def test_the_page_offers_the_update_and_starts_again(library, serve):
    library.photo("a.png", 1)
    page, result, calls = serve(library.path)
    start = page.question()
    assert start["screen"] == "StartScreen"
    update = offered(page)
    assert update["version"] == NEWER and update["current"] == __version__ and update["installable"]
    assert update["command"] == "pipx upgrade keepbest" and update["notes"].endswith(f"/releases/tag/{NEWER}")
    page.post("/api/update")
    result["thread"].join(timeout=20)
    assert result["code"] == 0
    assert calls["installed"] == [[["pipx", "upgrade", "keepbest"]]]
    [(argv, address)] = calls["restarted"]
    assert argv == selfupdate.restart_argv()
    port, token = address.split(" ")
    assert int(port) == page.port and token in result["said"][0]  # the new one takes over the tab's address
    assert f"Starting keepbest {NEWER}." in result["said"]
    assert library.files() == ["a.png"]


def test_the_update_waits_for_a_screen_between_runs(library, serve):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)
    page, result, calls = serve(library.path)
    start = page.question()
    page.answer(start, "")
    decision = page.question(start["id"])
    assert decision["screen"] == "DecisionScreen"
    offered(page, ready=False)
    with pytest.raises(urllib.error.HTTPError) as refused:
        page.post("/api/update")
    assert refused.value.code == 409 and calls["installed"] == []
    page.answer(decision, "")
    overview = page.question(decision["id"])
    assert overview["screen"] == "OverviewScreen" and not offered(page, ready=False)["ready"]
    page.answer(overview, "")
    end = page.question(overview["id"])
    assert end["screen"] == "EndScreen" and offered(page)["ready"]
    page.post("/api/update")
    result["thread"].join(timeout=20)
    assert calls["restarted"] and result["code"] == 0


def test_a_failed_update_is_shown_and_keepbest_goes_on(library, serve):
    library.photo("a.png", 1)
    page, result, calls = serve(library.path, fail=True)
    start = page.question()
    offered(page)
    page.post("/api/update")
    update = offered(page, state="failed")
    assert update["code"] == 1 and "no such tag" in update["output"]
    page.answer(start, "q")                 # the questions go on
    while page.get("/api/state")["finished"] is None:
        time.sleep(0.05)
    page.post("/api/close")
    result["thread"].join(timeout=20)
    assert result["code"] == 0 and calls["restarted"] == []


def test_questions_wait_while_the_update_installs(library, serve, monkeypatch):
    library.photo("a.png", 1)
    page, result, calls = serve(library.path)
    release = threading.Event()
    monkeypatch.setattr(selfupdate, "install", lambda commands, log: (release.wait(10), (0, ""))[1])
    start = page.question()
    offered(page)
    page.post("/api/update")
    offered(page, state="installing")
    with pytest.raises(urllib.error.HTTPError) as refused:
        page.answer(start, "q")
    assert refused.value.code == 409
    release.set()
    result["thread"].join(timeout=20)
    assert result["code"] == 0


def test_the_check_can_be_turned_off(library, run_cli, config_file, newer_release, tmp_path):
    library.photo("a.png", 1)
    config_file({"check_for_updates": False})
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "is available" not in result.stdout
    assert not (tmp_path / "state" / selfupdate.STATE_FILE).exists()


# --- the command line ---------------------------------------------------------------
def test_auto_prints_the_command_at_the_end(library, run_cli, newer_release):
    library.photo("a.png", 1)
    result = run_cli(library.path, "--auto", "--dry-run")
    assert result.returncode == 0
    tail = result.stdout.rsplit("Summary", 1)[1]
    assert f"keepbest {NEWER} is available (this is {__version__}). To update:" in tail
    assert f"git -C {REPO} pull" in tail       # the tests run from the checkout


def test_auto_says_nothing_without_a_newer_release(library, run_cli, tmp_path, monkeypatch):
    monkeypatch.setenv(selfupdate.REPO_ENV, releases(tmp_path / "releases", __version__))
    library.photo("a.png", 1)
    assert "available" not in run_cli(library.path, "--auto", "--dry-run").stdout


DRIVER = """
import sys
sys.path.insert(0, {repo!r})
from keepbest import cli, selfupdate
# a pipx install whose update is a stand-in: keepbest really starts again (exec)
selfupdate.how_installed = lambda version: selfupdate.Install(
    "pipx", "pipx upgrade keepbest", [["sh", "-c", "echo installed >> {marker}"]])
sys.exit(cli.main())
"""


def test_after_the_update_the_same_process_and_tab_go_on(library, config_file, newer_release, tmp_path):
    library.photo("a.png", 1)
    marker = tmp_path / "installed"
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(repo=str(REPO), marker=marker))
    config_file({})
    proc = subprocess.Popen([sys.executable, str(driver), "-c", str(config_file.path), "--color", "never",
                             "--no-browser", str(library.path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=REPO)
    out = []
    reader = threading.Thread(target=lambda: out.extend(iter(proc.stdout.readline, "")), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 30
        while not any("is running at" in line for line in out) and time.monotonic() < deadline:
            time.sleep(0.02)
        url = re.search(r"http://\S+", next(line for line in out if "is running at" in line)).group(0)
        page = Page(url)
        first = page.question()
        boot = page.get("/api/state")["boot"]
        offered(page)
        page.post("/api/update")
        state = None
        while time.monotonic() < deadline:     # the same address answers again, from the new keepbest
            try:
                state = page.get("/api/state")
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
                continue
            if state["boot"] != boot and state["question"]:
                break
            time.sleep(0.1)
        assert state["boot"] != boot and state["question"]["screen"] == "StartScreen"
        assert state["question"]["id"] == 1 and first["id"] == 1
        assert proc.poll() is None and marker.read_text() == "installed\n"
        page.answer(state["question"], "q")
        while page.get("/api/state")["finished"] is None:
            time.sleep(0.05)
        page.post("/api/close")
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        reader.join(timeout=5)
    said = "".join(out)
    assert said.count(f"keepbest is running at {url}") == 2 and f"Starting keepbest {NEWER}." in said
