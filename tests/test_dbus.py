"""The D-Bus interface (dbus_service.py), for picktag and other programs, also from a Flatpak."""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import REPO, Page, asked, drive, summary
from keepbest import cli, config, dbus_service, running, web

PAGE_URL = "http://127.0.0.1:43210/?token=abc_DEF-1"
OTHER_URL = "http://127.0.0.1:43211/?token=xyz"


class FakeRun:
    """A run's process: prints *lines*, then runs until released (or stopped,
    as with Ctrl+C: 130)."""

    def __init__(self, *urls, code=0, hold=True):
        self.lines = [web.RUNNING_LINE.format(url=u).encode() + b"\n" for u in urls]
        self.code, self.stopped = code, 0
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.stdout = self._out()

    def _out(self):
        yield b"started\n"
        yield from self.lines
        self.release.wait(10)

    def wait(self):
        return self.code

    def stop(self):
        self.stopped += 1
        self.code = 130
        self.release.set()


def bus_service(*processes, clock=None, opens=True):
    """A service with stand-in processes and browser; .started holds the
    commands, .opened the addresses given to the browser."""
    started, opened = [], []

    def spawn(command):
        started.append(command)
        return processes[len(started) - 1]

    def open_url(url):
        opened.append(url)
        return opens
    service = dbus_service.Service(spawn=spawn, open_url=open_url, interrupt=lambda p: p.stop(),
                                   **({"clock": clock} if clock else {}))
    service.started, service.opened = started, opened
    return service


def drain(service, count=1, timeout=5.0):
    """What the service sends, once *count* items came (or the time is up)."""
    out, deadline = [], time.monotonic() + timeout
    while len(out) < count and time.monotonic() < deadline:
        out += service.outgoing()
        time.sleep(0.01)
    return out


def refused(service, member, signature, *args):
    with pytest.raises(dbus_service.ServiceError) as error:
        service.call(member, signature, args, "call")
    return error.value


def fs(path) -> bytes:
    return os.fsencode(str(path))


@pytest.fixture
def checkout(monkeypatch):
    """The service runs the keepbest of this checkout."""
    monkeypatch.setattr(sys, "argv", [str(REPO / "run.py")])
    return [sys.executable, str(REPO / "run.py")]


@pytest.fixture
def folders(tmp_path):
    for name in ("pics/a", "pics/b", "other"):
        (tmp_path / name).mkdir(parents=True)
    return tmp_path


def test_signatures_come_from_the_introspection_data():
    assert dbus_service._SIGNATURES == {"GetInfo": "", "OpenPage": "ay", "RemoveCopies": "a(ayaay)a{sv}",
                                        "CompareGroups": "aaaya{sv}", "ShowPage": "s", "Runs": "", "Stop": "s"}


def test_info_says_what_picktag_cannot_read_from_the_sandbox(isolated):
    info = dbus_service.Service().call("GetInfo", "", (), "call")
    assert info[0] == "a{sv}"
    assert info[1][0] == {"Version": ("s", config.__version__), "InterfaceVersion": ("u", 1),
                          "GroupsFormat": ("u", 1), "JournalFile": ("s", str(isolated / "state" / "journal.jsonl")),
                          "DeleteMode": ("s", "trash")}
    settings = isolated / "home" / ".config" / "keepbest"
    settings.mkdir(parents=True)
    (settings / "config.json").write_text(json.dumps({"delete_mode": "permanent", "journal_file": False}))
    info = dbus_service.Service.info()
    assert info["DeleteMode"] == ("s", "permanent") and info["JournalFile"] == ("s", "")


def test_a_page_opens_in_the_browser_and_its_address_stays_here(folders, checkout):
    process = FakeRun(PAGE_URL)
    service = bus_service(process)
    assert service.call("OpenPage", "ay", (fs(folders / "pics") + b"\0",), "call") is None   # GLib's trailing NUL
    assert service.started == [[*checkout, str(folders / "pics"), "--no-browser", "--color", "never",
                                "--unvisited-timeout", "600"]]
    assert drain(service) == [("return", "call", "sb", ("1", True))]
    assert service.opened == [PAGE_URL]
    assert service.call("Runs", "", (), "c") == ("a(ssay)", ([("1", "page", fs(folders / "pics"))],))
    # the same folder again: the same page, shown again
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "again")
    assert drain(service) == [("return", "again", "sb", ("1", True))] and len(service.started) == 1
    service.call("ShowPage", "s", ("1",), "show")
    assert drain(service) == [("return", "show", "b", (True,))]
    process.release.set()
    out = drain(service)
    assert [o[:3] for o in out] == [("signal", "Finished", "sis")] and out[0][3][:2] == ("1", 0)
    assert "token" not in repr(out) and service.runs == {}
    log = Path(out[0][3][2])
    assert log.parent == dbus_service.service_folder()           # keepbest's state folder
    # other programs read the log: the address is there, its token isn't
    assert "is running at http://127.0.0.1:43210/?token=...\n" in log.read_text() and "abc_DEF" not in log.read_text()
    assert refused(service, "ShowPage", "s", "1").name.endswith("UnknownRun")


def test_a_page_without_a_folder_asks_for_one(checkout):
    service = bus_service(FakeRun(PAGE_URL, hold=False))
    service.call("OpenPage", "ay", (b"",), "call")
    assert service.started[0][2:] == ["--no-browser", "--color", "never", "--unvisited-timeout", "600"]
    out = drain(service, 2)
    assert out[0] == ("return", "call", "sb", ("1", True)) and out[1][3][:2] == ("1", 0)   # the reply first


def test_says_when_no_browser_opened(folders):
    service = bus_service(FakeRun(PAGE_URL), opens=False)
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    assert drain(service) == [("return", "call", "sb", ("1", False))]


def test_copies_are_removed_one_folder_after_the_other(folders, checkout):
    first, second = FakeRun(PAGE_URL, code=1), FakeRun(OTHER_URL)
    service = bus_service(first, second)
    scans = [(fs(folders / "pics"), [b".", b"b"]), (fs(folders / "other"), [])]
    service.call("RemoveCopies", "a(ayaay)a{sv}", (scans, {"DryRun": ("b", True)}), "call")
    assert service.started == [[*checkout, str(folders / "pics"), "-r", "--exclude=.", "--exclude=b", "--dry-run",
                                "--no-browser", "--color", "never", "--unvisited-timeout", "600"]]
    assert drain(service) == [("return", "call", "sb", ("1", True))]
    assert service.call("Runs", "", (), "c")[1][0] == [("1", "copies", fs(folders / "pics"))]
    first.release.set()                          # some files could not be removed: the next one still runs
    deadline = time.monotonic() + 5
    while len(service.opened) < 2 and time.monotonic() < deadline:
        assert service.outgoing() == []
        time.sleep(0.01)
    assert service.started[1][2:4] == [str(folders / "other"), "-r"]
    assert service.opened == [PAGE_URL, OTHER_URL]          # its page opens by itself
    assert service.call("Runs", "", (), "c")[1][0] == [("1", "copies", fs(folders / "other"))]
    second.release.set()
    finished = drain(service)
    assert [f[3][:2] for f in finished] == [("1", 1)]       # the last error's code
    log = Path(finished[0][3][2]).read_text()
    assert f"keepbest 1 of 2: {folders / 'pics'}" in log and f"keepbest 2 of 2: {folders / 'other'}" in log


def test_a_page_closed_at_a_question_ends_the_rest(folders):
    first = FakeRun(PAGE_URL, code=130, hold=False)
    service = bus_service(first, FakeRun(OTHER_URL))
    service.call("RemoveCopies", "a(ayaay)a{sv}", ([(fs(folders / "pics"), []), (fs(folders / "other"), [])], {}),
                 "call")
    out = drain(service, 2)
    assert out[0] == ("return", "call", "sb", ("1", True)) and out[1][3][:2] == ("1", 130)
    assert len(service.started) == 1 and service.runs == {}


def test_stop_is_ctrl_c_for_the_run_and_nothing_more_starts(folders):
    first = FakeRun(PAGE_URL)
    service = bus_service(first, FakeRun(OTHER_URL))
    service.call("RemoveCopies", "a(ayaay)a{sv}", ([(fs(folders / "pics"), []), (fs(folders / "other"), [])], {}),
                 "call")
    drain(service)
    service.call("Stop", "s", ("1",), "stop")
    service.call("Stop", "s", ("1",), "stop")    # a second Ctrl+C would cut off the stopping of the first
    assert first.stopped == 1
    assert [f[3][:2] for f in drain(service)] == [("1", 130)]
    assert len(service.started) == 1
    assert refused(service, "Stop", "s", "1").name.endswith("UnknownRun")


def test_groups_go_to_keepbest_as_its_groups_file(tmp_path, checkout):
    odd = b"/pics/caf\xe9.jpg"                     # not UTF-8
    process = FakeRun(PAGE_URL)
    service = bus_service(process)
    groups = [[b"/pics/a.jpg", b"/pics/sub/../b.png"], [odd, b"/pics/c.png"]]
    service.call("CompareGroups", "aaaya{sv}", (groups, {"From": ("s", "picktag")}), "call")
    command = service.started[0]
    assert command[:3] == [*checkout, "--groups"] and command[4:] == ["--no-browser", "--color", "never",
                                                                     "--unvisited-timeout", "600"]
    groups_file = Path(command[3])
    assert groups_file.parent == dbus_service.service_folder()
    data = json.loads(groups_file.read_text(encoding="ascii"))
    assert data == {"v": 1, "from": "picktag", "groups": [["/pics/a.jpg", "/pics/b.png"],
                                                          [os.fsdecode(odd), "/pics/c.png"]]}
    assert service.call("Runs", "", (), "c")[1][0] == [("1", "groups", b"/pics")]
    drain(service)
    process.release.set()
    assert drain(service)[0][3][:2] == ("1", 0)
    assert not groups_file.exists()


def test_calls_that_are_refused(folders, isolated, monkeypatch):
    service = bus_service(FakeRun(PAGE_URL))
    home = isolated / "home"
    assert refused(service, "OpenPage", "ay", fs(home)).name.endswith(".Protected")
    assert refused(service, "RemoveCopies", "a(ayaay)a{sv}", [(b"/", [])], {}).name.endswith(".Protected")
    assert refused(service, "OpenPage", "ay", fs(folders / "missing")).name.endswith(".NoFolder")
    assert refused(service, "OpenPage", "ay", b"pics").name.endswith("InvalidArgs")
    assert refused(service, "OpenPage", "ay", b"/pi\0cs").name.endswith("InvalidArgs")
    for excluded in (b"../other", b"/abs", b""):
        assert refused(service, "RemoveCopies", "a(ayaay)a{sv}", [(fs(folders / "pics"), [excluded])], {}
                       ).name.endswith("InvalidArgs")
    assert refused(service, "RemoveCopies", "a(ayaay)a{sv}", [], {}).name.endswith("InvalidArgs")
    for options in ({"Permanent": ("b", True)}, {"DryRun": ("s", "yes")}, {"Auto": ("b", True)}):
        error = refused(service, "RemoveCopies", "a(ayaay)a{sv}", [(fs(folders / "pics"), [])], options)
        assert error.name.endswith("InvalidArgs")
    assert refused(service, "CompareGroups", "aaaya{sv}", [[b"rel.jpg"]], {}).name.endswith("InvalidArgs")
    assert refused(service, "CompareGroups", "aaaya{sv}", [[]], {}).name.endswith("InvalidArgs")
    assert refused(service, "CompareGroups", "aaaya{sv}", [[b"/a.jpg"]], {"From": ("s", "x" * 101)}
                   ).name.endswith("InvalidArgs")
    monkeypatch.setattr(dbus_service, "MAX_FILES", 2)
    assert refused(service, "CompareGroups", "aaaya{sv}", [[b"/a", b"/b", b"/c"]], {}).name.endswith("InvalidArgs")
    assert refused(service, "OpenPage", "s", "/pics").name.endswith("InvalidArgs")      # the wrong signature
    assert refused(service, "Delete", "", ).name.endswith("UnknownMethod")
    assert service.started == [] and service.runs == {}
    assert not list(dbus_service.service_folder().glob("groups-*"))


def test_overlapping_runs_are_refused(folders):
    service = bus_service(FakeRun(PAGE_URL), FakeRun(OTHER_URL))
    service.call("RemoveCopies", "a(ayaay)a{sv}", ([(fs(folders / "pics" / "a"), [])], {}), "call")
    busy = refused(service, "OpenPage", "ay", fs(folders / "pics"))         # above it
    assert busy.name.endswith(".Busy") and "Run 1" in str(busy)
    assert refused(service, "CompareGroups", "aaaya{sv}", [[fs(folders / "pics/a/x.jpg"), fs(folders / "y.jpg")]],
                   {}).name.endswith(".Busy")
    service.call("OpenPage", "ay", (fs(folders / "pics" / "b"),), "beside")   # beside it is fine
    assert len(service.started) == 2


def test_a_folder_another_keepbest_works_on_is_refused_without_its_address(folders):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        registry = running._folder()
        registry.mkdir(parents=True)
        (registry / f"{other.pid}.json").write_text(json.dumps(
            {"pid": other.pid, "folder": str(folders / "pics"), "address": PAGE_URL}))
        service = bus_service(FakeRun(PAGE_URL))
        busy = refused(service, "OpenPage", "ay", fs(folders / "pics" / "a"))
        assert busy.name.endswith(".Busy") and f"process {other.pid}" in str(busy) and "token" not in str(busy)
        assert running.other(folders / "other") is None
        assert (registry / f"{other.pid}.json").exists()       # only looked at, nothing registered
        assert not (registry / f"{os.getpid()}.json").exists()
    finally:
        other.kill()
        other.wait()


def test_a_page_that_ends_before_its_address_is_an_error_and_finished(folders):
    service = bus_service(FakeRun(code=2, hold=False))
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    out = drain(service, 2)
    assert out[0][:3] == ("error", "call", dbus_service.ERROR + ".Failed") and "exit code 2" in out[0][3]
    assert out[1][:2] == ("signal", "Finished") and out[1][3][:2] == ("1", 2)


def test_gives_up_on_a_page_that_does_not_come(folders):
    now = [0.0]
    process = FakeRun()
    service = bus_service(process, clock=lambda: now[0])
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    assert service.outgoing() == []
    now[0] = dbus_service.URL_TIMEOUT + 1
    out = service.outgoing()
    assert out[0][:3] == ("error", "call", dbus_service.ERROR + ".Failed") and process.stopped == 1
    assert drain(service)[0][3][:2] == ("1", 130)


def test_keepbest_that_cannot_start_is_an_error_and_no_run(folders):
    def spawn(command):
        raise FileNotFoundError(2, "No such file", command[0])
    service = dbus_service.Service(spawn=spawn)
    assert refused(service, "OpenPage", "ay", fs(folders / "pics")).name.endswith(".Failed")
    assert service.runs == {}


def test_old_logs_and_stale_groups_files_go(folders):
    folder = dbus_service.service_folder()
    folder.mkdir(parents=True)
    for i in range(dbus_service.KEEP_LOGS + 3):
        (folder / f"old-{i}.log").write_text("")
        os.utime(folder / f"old-{i}.log", (1000 + i, 1000 + i))
    (folder / "groups-stale.json").write_text("{}")
    os.utime(folder / "groups-stale.json", (1000, 1000))
    (folder / "groups-fresh.json").write_text("{}")
    service = bus_service(FakeRun(PAGE_URL))
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    logs = {p.name for p in folder.glob("*.log")}
    assert {f"old-{i}.log" for i in range(3)}.isdisjoint(logs) and f"old-{dbus_service.KEEP_LOGS + 2}.log" in logs
    assert not (folder / "groups-stale.json").exists() and (folder / "groups-fresh.json").exists()


def test_the_page_line_is_the_one_keepbest_prints():
    pattern = dbus_service._running_pattern()
    assert pattern.match(web.RUNNING_LINE.format(url=PAGE_URL)).group(1) == PAGE_URL
    assert not pattern.match(f"lyrembed is running at {PAGE_URL}")
    assert not pattern.match("keepbest is running at https://evil.example/?token=x")
    assert dbus_service.URL_TIMEOUT < 25   # GDBus' and libdbus' default reply timeout


def test_waits_for_its_runs_before_it_ends(folders):
    process = FakeRun(PAGE_URL)
    service = bus_service(process)
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    drain(service)
    threading.Timer(0.3, process.release.set).start()
    started = time.monotonic()
    dbus_service._outlive_runs(service)
    assert service.runs == {} and time.monotonic() - started >= 0.25


def test_ctrl_c_reaches_the_runs_too(folders, monkeypatch):
    pytest.importorskip("jeepney")
    import jeepney.io.blocking

    process = FakeRun(PAGE_URL)
    service = bus_service(process)
    service.call("OpenPage", "ay", (fs(folders / "pics"),), "call")
    drain(service)
    monkeypatch.setattr(jeepney.io.blocking, "open_dbus_connection", lambda bus: contextlib.nullcontext())

    def interrupted(conn, service, idle_exit):
        raise KeyboardInterrupt
    monkeypatch.setattr(dbus_service, "_answer", interrupted)
    assert dbus_service.serve(service) == 130
    assert process.stopped == 1 and service.runs == {}


# --- the bus loop (a stand-in connection) ----------------------------------------
class FakeBus:
    """Just enough of a jeepney connection for dbus_service._answer: the bus
    answers RequestName and ReleaseName; *before_reply* comes before
    RequestName's answer, *after* before ReleaseName's; then *end*."""

    def __init__(self, before_reply=(), after=(), end=OSError("the bus went away")):
        self.outgoing_serial = itertools.count(1)
        self.sent, self.before_reply, self.after, self.end = [], list(before_reply), list(after), end
        self.answered = 0

    def send(self, message, serial=None):
        message.header.serial = serial or next(self.outgoing_serial)
        message.serialise()   # as the real one does: fails on values out of range
        self.sent.append(message)

    def _bus_call(self):
        from jeepney import HeaderFields

        calls = [m for m in self.sent if m.header.fields.get(HeaderFields.destination) == "org.freedesktop.DBus"]
        return calls[self.answered] if self.answered < len(calls) else None

    def receive(self, timeout=None):
        from jeepney import new_method_return

        if self.before_reply:
            return self.before_reply.pop(0)
        if self.answered == 0 and self._bus_call() is not None:   # RequestName: "now the owner"
            self.answered += 1
            return new_method_return(self._bus_call() or self.sent[0], "u", (1,))
        if self.after:
            return self.after.pop(0)
        if (release := self._bus_call()) is not None:              # ReleaseName
            self.answered += 1
            return new_method_return(release, "u", (1,))
        raise self.end


def _calls(*methods):
    from jeepney import DBusAddress, new_method_call

    target = DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, dbus_service.INTERFACE)
    messages = []
    for serial, method in enumerate(methods, start=7):
        messages.append(new_method_call(target, method))
        messages[-1].header.serial = serial
    return messages


def test_survives_a_failing_call_and_keeps_the_one_that_started_it(capsys):
    pytest.importorskip("jeepney")
    from jeepney import MessageType

    messages = _calls("Runs", "GetInfo", "GetInfo", "Runs")
    bus = FakeBus(before_reply=messages[:1], after=messages[1:])   # the first before RequestName's reply
    service = dbus_service.Service()
    infos = iter([PermissionError(13, "Permission denied"), {"Version": ("u", -1)}])   # then one jeepney can't send

    def info():
        answer = next(infos)
        if isinstance(answer, Exception):
            raise answer
        return answer
    service.info = info
    assert dbus_service._answer(bus, service, idle_exit=60) == 0   # ended by the bus going away
    replies = bus.sent[1:]
    assert [m.header.message_type for m in replies] == [MessageType.method_return, MessageType.error,
                                                        MessageType.error, MessageType.method_return]
    assert "PermissionError" in replies[1].body[0] and "Traceback" in capsys.readouterr().err


def test_answers_what_came_while_it_let_go_of_its_name():
    # Idle, the service releases its name before it ends: a call the bus
    # routed to it just before is still answered, the next one starts a new one.
    pytest.importorskip("jeepney")
    from jeepney import HeaderFields, MessageType

    bus = FakeBus(after=_calls("Runs"), end=TimeoutError())
    assert dbus_service._answer(bus, dbus_service.Service(), idle_exit=-1) == 0
    members = [m.header.fields.get(HeaderFields.member) for m in bus.sent]
    assert members[:2] == ["RequestName", "ReleaseName"]
    assert bus.sent[2].header.message_type == MessageType.method_return
    assert bus.sent[2].header.fields[HeaderFields.reply_serial] == 7


def test_a_run_started_while_letting_go_of_the_name_takes_it_back(folders):
    """A call routed here just before the bus confirmed the release starts a run:
    the name comes back, or Stop and ShowPage would reach a new keepbest."""
    pytest.importorskip("jeepney")
    from jeepney import DBusAddress, HeaderFields, MessageType, new_method_call

    call = new_method_call(DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, dbus_service.INTERFACE),
                           "OpenPage", "ay", (fs(folders / "pics"),))
    call.header.serial = 7
    process = FakeRun(PAGE_URL)
    service = bus_service(process)
    bus = FakeBus(after=[call], end=TimeoutError())
    threading.Timer(1.0, process.release.set).start()
    assert dbus_service._answer(bus, service, idle_exit=-1) == 0
    members = [m.header.fields.get(HeaderFields.member) for m in bus.sent
               if m.header.message_type == MessageType.method_call]
    assert members == ["RequestName", "ReleaseName", "RequestName", "ReleaseName"]
    replies = [m for m in bus.sent if m.header.fields.get(HeaderFields.reply_serial) == 7]
    assert replies[0].body == ("1", True)


def test_a_service_that_stops_answering_lets_go_of_its_name_and_starts_nothing_more(folders, monkeypatch):
    pytest.importorskip("jeepney")
    import jeepney.io.blocking
    from jeepney import HeaderFields

    class Conn(contextlib.nullcontext):
        sent = []

        def send(self, message, serial=None):
            self.sent.append(message)
    first = FakeRun(PAGE_URL)
    service = bus_service(first, FakeRun(OTHER_URL))
    service.call("RemoveCopies", "a(ayaay)a{sv}", ([(fs(folders / "pics"), []), (fs(folders / "other"), [])], {}),
                 "call")
    drain(service)
    monkeypatch.setattr(jeepney.io.blocking, "open_dbus_connection", lambda bus: Conn())
    monkeypatch.setattr(dbus_service, "_answer", lambda conn, service, idle_exit: 0)   # the bus went away
    threading.Timer(0.3, first.release.set).start()
    assert dbus_service.serve(service) == 0
    assert [m.header.fields.get(HeaderFields.member) for m in Conn.sent] == ["ReleaseName"]
    assert len(service.started) == 1 and service.runs == {}   # the second folder isn't started any more


def test_groups_through_a_symlinked_folder_are_busy_with_the_real_one(tmp_path):
    real = tmp_path / "data" / "Pictures"
    real.mkdir(parents=True)
    link = tmp_path / "Pictures"
    link.symlink_to(real)
    service = bus_service(FakeRun(PAGE_URL), FakeRun(OTHER_URL))
    service.call("RemoveCopies", "a(ayaay)a{sv}", ([(fs(real), [])], {}), "call")
    busy = refused(service, "CompareGroups", "aaaya{sv}", [[fs(link / "a.jpg"), fs(link / "b.jpg")]], {})
    assert busy.name.endswith(".Busy")


# --- the real keepbest -------------------------------------------------------------------
def test_groups_from_the_service_are_compared_on_the_page(library, checkout):
    """CompareGroups starts the real keepbest: its page shows the groups and the dry run finds the copy."""
    library.photo("one/a.png", 1)
    library.variant("one/a.png", "two/a.jpg", quality=85)
    opened = []
    service = dbus_service.Service(open_url=lambda url: opened.append(url) or True)
    groups = [[fs(library / "one/a.png"), fs(library / "two/a.jpg")]]
    service.call("CompareGroups", "aaaya{sv}", (groups, {"DryRun": ("b", True), "From": ("s", "picktag")}), "call")
    assert drain(service, timeout=30) == [("return", "call", "sb", ("1", True))]
    run = service.runs["1"]
    questions, typos, _ = drive(Page(opened[0]), ["", "q"], run.process)
    assert not typos
    out = drain(service, timeout=60)
    assert out[0][:2] == ("signal", "Finished") and out[0][3][1] == 0, Path(out[0][3][2]).read_text()
    start = asked(type("Result", (), {"questions": questions}), "StartScreen")[0]
    assert start["groups"] == 1 and start["groupsFrom"] == "picktag"
    assert summary(Path(out[0][3][2]).read_text())["visual"] == 1
    assert library.files() == ["one/a.png", "two/a.jpg"]          # a dry run
    assert not list(dbus_service.service_folder().glob("groups-*"))


def test_a_page_nobody_opens_ends(library, config_file):
    library.photo("a.png", 1)
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--no-browser",
                             "--color", "never", "--unvisited-timeout", "1", str(library.path)],
                            capture_output=True, text=True, cwd=REPO, timeout=60)
    assert result.returncode == 130 and "Nobody opened the page in 1 s" in result.stdout
    assert time.monotonic() - started < 30


def test_dbus_takes_no_other_options(capsys):
    for argv in (["--dbus", "--dry-run"], ["--dbus", "/pics"], ["--unvisited-timeout", "-1"],
                 ["--unvisited-timeout", "5", "--auto"], ["--install", "--remove-desktop-entry"],
                 ["--install", "--uninstall"]):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_install_adds_the_app_menu_entry_and_the_dbus_service(run_cli, tmp_path, library):
    result = run_cli(library.path, "--install")
    data = tmp_path / "home" / ".local" / "share"
    entry = data / "applications" / "keepbest.desktop"
    service = data / "dbus-1" / "services" / f"{dbus_service.BUS_NAME}.service"
    assert result.returncode == 0, result.stdout + result.stderr
    assert entry.is_file() and "run.py %f" in entry.read_text()
    assert service.read_text() == dbus_service.service_file_text([sys.executable, str(REPO / "run.py")])
    assert "it does at the next login" in result.stdout     # the tests have no session bus to tell
    assert run_cli(library.path, "--remove-dbus-service").returncode == 0 and not service.exists()
    assert entry.exists()                                   # each removal only its own
    assert run_cli(library.path, "--install-dbus-service").returncode == 0 and service.exists()
    assert run_cli(library.path, "--remove-desktop-entry").returncode == 0 and not entry.exists() and service.exists()


def test_uninstall_removes_what_install_wrote(run_cli, tmp_path, library):
    data = tmp_path / "home" / ".local" / "share"
    entry = data / "applications" / "keepbest.desktop"
    icon = data / "icons" / "hicolor" / "scalable" / "apps" / "keepbest.svg"
    service = data / "dbus-1" / "services" / f"{dbus_service.BUS_NAME}.service"
    assert run_cli(library.path, "--install").returncode == 0 and entry.exists() and service.exists()
    result = run_cli(library.path, "--uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not entry.exists() and not icon.exists() and not service.exists()
    assert str(entry) in result.stdout and str(service) in result.stdout
    again = run_cli(library.path, "--uninstall")
    assert again.returncode == 0
    assert "There was no app-menu entry." in again.stdout and "There was no D-Bus service file." in again.stdout


def test_uninstall_removes_the_service_also_when_the_entry_cannot_go(run_cli, tmp_path, library):
    data = tmp_path / "home" / ".local" / "share"
    service = data / "dbus-1" / "services" / f"{dbus_service.BUS_NAME}.service"
    assert run_cli(library.path, "--install-dbus-service").returncode == 0
    (data / "applications" / "keepbest.desktop").mkdir(parents=True)   # can't be unlinked
    result = run_cli(library.path, "--uninstall")
    assert result.returncode == 2 and "Could not remove" in result.stdout + result.stderr
    assert not service.exists()


def test_the_service_file_starts_this_keepbest(tmp_path, monkeypatch, checkout):
    monkeypatch.setattr(dbus_service, "_reload_bus", lambda: None)
    path, command, unreloaded = dbus_service.install_service_file()
    assert command == checkout and unreloaded is None
    assert path == Path(os.environ["XDG_DATA_HOME"]) / "dbus-1" / "services" / "io.github.sobakah.keepbest.service"
    assert path.read_text() == (f"[D-BUS Service]\nName=io.github.sobakah.keepbest\n"
                                f"Exec={sys.executable} {REPO / 'run.py'} --dbus\n")
    assert dbus_service.remove_service_file() == path and dbus_service.remove_service_file() is None


def test_in_a_toolbox_the_service_enters_it(toolbox, tmp_path, monkeypatch, checkout):
    monkeypatch.setattr(dbus_service, "_reload_bus", lambda: None)
    path, command, _ = dbus_service.install_service_file()
    assert command == ["/usr/bin/toolbox", "run", "--container", "my box", *checkout]
    assert "Exec=/usr/bin/toolbox run --container 'my box' " in path.read_text()


def test_on_a_real_bus_started_by_the_bus(tmp_path, library, monkeypatch):
    """A private dbus-daemon with keepbest's service file: the bus starts
    keepbest --dbus at the first call, as the session bus does for picktag."""
    pytest.importorskip("jeepney")
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        pytest.skip("no dbus-daemon")
    from jeepney import DBusAddress, MatchRule, MessageType, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection

    services = tmp_path / "services"
    services.mkdir()
    (services / f"{dbus_service.BUS_NAME}.service").write_text(
        dbus_service.service_file_text([sys.executable, str(REPO / "run.py")]), encoding="utf-8")
    (tmp_path / "bus.conf").write_text(f"""<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/>
  </policy>
</busconfig>
""", encoding="utf-8")
    # What the bus starts inherits this environment: the test's home and
    # state folder, no PATH to find a browser with, and no display.
    library.photo("a.png", 1)
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    for name in ("BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "TERM"):
        monkeypatch.delenv(name, raising=False)
    bus = subprocess.Popen([daemon, f"--config-file={tmp_path / 'bus.conf'}", "--nofork", "--print-address"],
                           stdout=subprocess.PIPE, text=True)
    try:
        address = bus.stdout.readline().strip()
        keepbest = DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, dbus_service.INTERFACE)
        with open_dbus_connection(bus=address) as conn:
            def call(method, signature=None, body=()):
                reply = conn.send_and_get_reply(new_method_call(keepbest, method, signature, body), timeout=30)
                assert reply.header.message_type == MessageType.method_return, reply.body
                return reply.body

            info = call("GetInfo")[0]
            assert info["Version"] == ("s", config.__version__) and info["GroupsFormat"] == ("u", 1)
            finished = MatchRule(type="signal", interface=dbus_service.INTERFACE, member="Finished")
            conn.send_and_get_reply(message_bus.AddMatch(finished), timeout=10)
            with conn.filter(finished) as signals:
                run, opened = call("OpenPage", "ay", (fs(library.path),))
                assert opened is False                            # no browser here, and no address either
                assert call("Runs") == ([(run, "page", fs(library.path))],)
                assert call("ShowPage", "s", (run,)) == (False,)
                call("Stop", "s", (run,))
                ended = conn.recv_until_filtered(signals, timeout=30)
            assert ended.body[:2] == (run, 130)   # stopped before the page's first visit
            log = Path(ended.body[2]).read_text()
            assert "keepbest is running at http://127.0.0.1:" in log and "?token=...\n" in log
            refusal = conn.send_and_get_reply(new_method_call(
                keepbest, "OpenPage", "ay", (b"/",)), timeout=10)
            assert refusal.header.message_type == MessageType.error and "Protected" in str(refusal.header.fields)
            intro = conn.send_and_get_reply(new_method_call(
                DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, "org.freedesktop.DBus.Introspectable"),
                "Introspect"), timeout=10)
            assert 'name="CompareGroups"' in intro.body[0]
    finally:
        bus.terminate()
        bus.wait(10)
