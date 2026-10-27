"""Shared fixtures and helpers for the keepbest tests.

Every test runs isolated: its own home folder, config and state folder, so
your real config, log, sessions and trash are never touched.

The helpers are meant for writing new tests quickly:

    def test_my_case(library, run_cli):
        library.photo("a.png", seed=1)
        library.copy_of("a.png", "backup/a copy.png")
        result = run_cli(library.path, "-r", "--auto", "--dry-run")
        assert summary(result)["exact"] == 1

Without --auto, run_cli answers keepbest's page with the keys given.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import pictures

REPO = Path(__file__).resolve().parent.parent
FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point home, config, state and trash into the test's temporary folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))  # the trash lives here
    monkeypatch.setenv("KEEPBEST_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    return tmp_path


# --- building picture folders ------------------------------------------------
class Library:
    """A folder of test pictures; paths are relative to it."""

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def __truediv__(self, rel: str) -> Path:
        return self.path / rel

    def photo(self, rel: str, seed: int, size=(320, 240), **options) -> Path:
        """A new photo-like picture; the format follows the extension."""
        return pictures.save(pictures.photo(seed, size), self / rel, **options)

    def variant(self, source: str, rel: str, change=None, **options) -> Path:
        """Save *source* again (e.g. another format or quality), optionally changed
        by *change*, a function from the pictures module such as with_text."""
        from PIL import Image
        with Image.open(self / source) as img:
            img = img.convert("RGB")
        if change is not None:
            img = change(img)
        return pictures.save(img, self / rel, **options)

    def copy_of(self, source: str, rel: str) -> Path:
        """A byte-identical copy (same content, other name or folder)."""
        (self / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self / source, self / rel)
        return self / rel

    def files(self) -> list[str]:
        """All files below the folder, relative, sorted, hidden ones included."""
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file())


@pytest.fixture
def library(tmp_path) -> Library:
    return Library(tmp_path / "pictures")


# --- running the program -----------------------------------------------------
@pytest.fixture
def config_file(tmp_path):
    """Write a config for run_cli: config_file({"strictness": "strict"})."""
    path = tmp_path / "config.json"
    path.write_text("{}")

    def write(data: dict) -> Path:
        path.write_text(json.dumps(data))
        return path

    write.path = path
    return write


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


ADDRESS = re.compile(r"keepbest is running at (http://127\.0\.0\.1:\d+/\?token=[\w-]+)")


def _typed(question: dict, key: str):
    """A key as typed in the terminal before 1.0.0, as the page's answer to *question*;
    None when the page has no such answer (a typo)."""
    if question["kind"] == "confirm":
        key = key.strip().lower()
        if key == "":
            return question["default"]
        return {"y": True, "yes": True, "n": False, "no": False}.get(key)
    if question["kind"] == "path":
        return key
    key = key.strip().lower()
    return key if key in question["keys"] else None


def drive(page: Page, keys, proc) -> tuple[list, list, list]:
    """Answer keepbest's questions on its page with *keys*, one per question, as
    a user clicks through it. A key the question doesn't offer (a typo) is
    skipped, as the page has no button for it. When the keys run out with a
    question open, the page is closed, which stops the flow there (like the
    end of input in the terminal before). Returns (questions asked, typos, events)."""
    keys, asked, typos, events = list(keys), [], [], {}
    answered = set()
    rev = 0
    while proc.poll() is None:
        try:
            state = page.get(f"/api/state?since={rev}")
        except (urllib.error.URLError, ConnectionError):
            break                                   # it has just ended
        rev = state["rev"]
        for event in state["events"]:
            events[event["id"]] = event
        q = state["question"]
        if q is None:
            if state["finished"] is not None:
                page.post("/api/close")
                break
            continue
        if q["id"] in answered:
            continue
        value = None
        while value is None and keys:
            key = keys.pop(0)
            value = _typed(q, key)
            if value is None:
                typos.append((q, key))
        if value is None:                           # no keys left: the page goes away
            page.post("/api/close")
            break
        asked.append(q)
        answered.add(q["id"])
        page.answer(q, value)
    return asked, typos, [events[i] for i in sorted(events)]


@pytest.fixture
def run_cli(config_file, request):
    """Run keepbest as a user would: run_cli(folder, "-r", keys=["", "q"]).

    Without --auto keepbest shows its page; *keys* answer its questions in turn
    (see drive(): the key of a button, "" for Enter, y/n for yes/no questions,
    a path for the folder question). The program always gets the test's own
    config file, so no real config is read. The result has the terminal's
    output (the run's log) and .questions, .typos and .events from the page.

    KEEPBEST_RECORD=DIR saves every run's terminal output there
    (KEEPBEST_RECORD_COLOR=always with colours), so two versions of the code can
    be compared byte for byte: run the suite with each and diff the folders.
    """
    record = os.environ.get("KEEPBEST_RECORD")
    color = os.environ.get("KEEPBEST_RECORD_COLOR", "never") if record else "never"
    calls = []

    def run(*args, keys=(), env=None, timeout=180):
        args = [str(a) for a in args]
        argv = [sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--color", color, "--no-browser", *args]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=REPO, env={**os.environ, **(env or {})})
        out, err = [], []
        readers = [threading.Thread(target=lambda s=s, b=b: b.extend(iter(s.readline, "")), daemon=True)
                   for s, b in ((proc.stdout, out), (proc.stderr, err))]
        for reader in readers:
            reader.start()
        asked, typos, events = [], [], []
        try:
            deadline = time.monotonic() + timeout
            url = None
            while url is None and proc.poll() is None and time.monotonic() < deadline:
                found = ADDRESS.search("".join(out[:3]))
                url = found.group(1) if found else None
                time.sleep(0.02)
            if url is not None:
                asked, typos, events = drive(Page(url), keys, proc)
            proc.wait(timeout=max(1, deadline - time.monotonic()))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for reader in readers:
                reader.join(timeout=5)
        result = subprocess.CompletedProcess(argv, proc.returncode, "".join(out), "".join(err))
        result.questions, result.typos, result.events = asked, typos, events
        if record:
            calls.append(result)
            name = re.sub(r"[^\w.-]+", "_", request.node.nodeid) + f"-{len(calls)}.txt"
            text = f"exit {result.returncode}\n--- stdout\n{result.stdout}--- stderr\n{result.stderr}"
            text = text.replace(str(config_file.path.parent), "<tmp>")
            text = re.sub(r"http://127\.0\.0\.1:\d+/\?token=[\w-]+", "<address>", text)
            text = re.sub(r"\d\d [A-Z][a-z]{2} \d{4} \d\d:\d\d", "<date>", text)  # saved-session times
            Path(record).mkdir(parents=True, exist_ok=True)
            (Path(record) / name).write_text(text, encoding="utf-8", errors="surrogateescape")
        return result
    return run


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def asked(result, screen: str) -> list[dict]:
    """The questions of one kind of screen the page asked, as it got them (web.screen_data)."""
    return [q for q in result.questions if q.get("screen") == screen]


def shown(result) -> str:
    """Everything a run showed as text: the terminal's log, then every text of
    the questions the page asked (prompts, choices, settings, file names, notes)."""
    return result.stdout + "\n".join(_strings(result.questions))


_SUMMARY_FIELDS = {
    "scanned": r"Scanned: (\d+)",
    "exact": r"Exact copies (?:removed|to remove): (\d+)",
    "remux": r"Remuxed videos (?:removed|to remove): (\d+)",
    "visual": r"Identical images (?:removed|to remove): (\d+)",
    "renamed": r"(?:Renamed|To rename): (\d+)",
    "skipped": r"Groups skipped: (\d+)",
    "not_duplicates": r"Not duplicates: (\d+)",
    "failed": r"Failed: (\d+)",
}


def summary(result) -> dict:
    """The numbers of the last summary printed by run_cli (missing ones are 0)."""
    out = result.stdout if hasattr(result, "stdout") else str(result)
    last = out.rsplit("Summary", 1)[-1]
    found = {}
    for key, pattern in _SUMMARY_FIELDS.items():
        match = re.search(pattern, last)
        found[key] = int(match.group(1)) if match else 0
    return found


# --- stand-in programs -------------------------------------------------------
@pytest.fixture
def fake_command(tmp_path, monkeypatch):
    """Put a stand-in program on PATH that records how it was called.

    cmd = fake_command("xdg-open"); ...; cmd.calls() -> list of argument lists.
    With exit_code, `flatpak info` style checks can be answered.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def make(name: str, exit_code: int = 0, script: str = ""):
        log = tmp_path / f"{name}.calls"
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{script}\nprintf '%s\\n' \"$*\" >> '{log}'\nexit {exit_code}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

        class Command:
            def calls(self, count: int = 1, wait: float = 5.0) -> list[list[str]]:
                """The recorded calls, waiting up to *wait* seconds for *count* of
                them: viewers are started in the background and may take a moment."""
                def read():
                    return [line.split(" ") for line in log.read_text().splitlines()] if log.exists() else []
                end = time.time() + wait
                while len(read()) < count and time.time() < end:
                    time.sleep(0.05)
                return read()
        return Command()
    return make


# --- videos ------------------------------------------------------------------
def make_video(path: Path, seconds: int = 3, codec: str = "libx264", audio: bool = True, size="320x240") -> Path:
    """A short test video (moving test pattern, optional sine tone) made with ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if audio:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    argv += ["-c:v", codec, "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(argv, check=True)
    return path


def remux(source: Path, target: Path, *extra: str) -> Path:
    """The same streams in another container (the extension decides which)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-map", "0", "-c", "copy", *extra, str(target)], check=True)
    return target
