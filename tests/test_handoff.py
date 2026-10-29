"""Offering media-optimizer after a run and coming back from it: one with a
page of its own takes over keepbest's tab, an older one runs without
questions with its output on keepbest's page."""

import re

import pytest

from conftest import asked, shown

OFFER = "Optimize this folder with media-optimizer"
BACK = "Back in keepbest"
OLD_HELP = "usage: media-optimizer run [-h] [-y] [source]"
PAGE_HELP = "usage: media-optimizer run [-h] [-y] [--web] [--no-browser] [--return-url URL] [--return-name NAME]"
RESULTS_HELP = PAGE_HELP + " [--results FILE]"
# What a media-optimizer that converted 3 pictures and saved 2048 bytes writes (its results.py).
RESULTS = {"v": 1, "program": "media-optimizer", "version": "1.1.0", "code": 3, "dry_run": False,
           "source": "/x", "target": None, "converted": 3, "failed": 1, "before": 5120, "after": 3072,
           "stages": [{"stage": "images", "title": "Images", "state": "done", "code": 3, "total": 4, "done": 4,
                       "outcomes": {"converted": 3, "failed": 1}, "before": 5120, "after": 3072, "elapsed": 1.0},
                      {"stage": "gifs", "title": "GIFs", "state": "earlier", "code": None, "total": 1, "done": 0,
                       "outcomes": {}, "before": 0, "after": 0, "elapsed": None}]}


@pytest.fixture
def one_copy(library):
    library.photo("a.png", 1)
    library.copy_of("a.png", "a copy.png")
    return library


@pytest.fixture
def optimizer(fake_command, tmp_path):
    """A stand-in media-optimizer that also records the variables it was given."""
    env_log = tmp_path / "optimizer.env"

    def make(script: str = "", exit_code: int = 0, page: bool = False, help_text: str = ""):
        folders_log = tmp_path / "optimizer.folders"
        # `run --help` tells keepbest whether it has a page (not recorded as a run)
        help_text = help_text or (PAGE_HELP if page else OLD_HELP)
        command = fake_command("media-optimizer", exit_code=exit_code,
                               script=f"[ \"$2\" = --help ] && {{ echo '{help_text}'; exit 0; }}\n"
                                      f"echo \"$MO_CALLER $MO_HOLD\" >> '{env_log}'\n"
                                      f"echo \"$MO_SUBFOLDERS $MO_EXCLUDE_FOLDERS\" >> '{folders_log}'\n{script}")
        command.env = lambda: env_log.read_text().split() if env_log.exists() else []
        command.folders = lambda: folders_log.read_text().strip() if folders_log.exists() else None
        return command
    return make


def test_real_run_offers_media_optimizer_for_the_same_folder(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["", "m", "q"])            # start, optimizer, quit
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert command.calls() == [["run", str(one_copy.path), "-y"]]   # an old one: no questions
    assert command.env() == ["keepbest", "0"]                       # caller known, no hold-open
    assert BACK in result.stdout
    first, again = asked(result, "EndScreen")
    assert "no questions" in dict(first["menu"][0][1])["m"]
    assert "m" in first["keys"] and "m" not in again["keys"]        # not offered twice
    assert result.returncode == 0


def test_carrying_out_a_dry_run_offers_it_too(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["4", "", "", "m", "q"])   # dry run, start, apply, optimizer
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert OFFER in shown(result)
    assert command.calls() == [["run", str(one_copy.path), "-y"]]


def test_dry_run_does_not_offer_it(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, "--dry-run", keys=["", "m"])
    assert OFFER not in shown(result)
    assert command.calls(count=0) == []


def test_not_offered_when_not_installed(one_copy, run_cli, tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    result = run_cli(one_copy.path, keys=["", "m"], env={"PATH": str(empty)})
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert OFFER not in shown(result)
    assert [key for _, key in result.typos] == ["m"]                # no such button


def test_without_subfolders_media_optimizer_gets_none(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["", "m", "q"])            # without -r
    assert "only the main folder, no subfolders" in result.stdout
    assert command.folders() == "exclude []"


def test_the_folder_choice_is_passed_on(one_copy, run_cli, optimizer):
    one_copy.photo("Urlaub/Strand/b.png", 2)
    one_copy.photo("Familie: 2024/c.png", 3)                         # ':' needs JSON
    one_copy.photo("Arbeit/d.png", 4)
    command = optimizer()
    result = run_cli(one_copy.path, "-r", "--exclude", "Urlaub", "--exclude", "Familie: 2024",
                     keys=["", "m", "q"])
    assert "3 folder(s) left out" in result.stdout, result.stdout
    assert command.folders() == 'include ["Familie: 2024", "Urlaub", "Urlaub/Strand"]'


def test_folder_env_matches_media_optimizer():
    """The settings media-optimizer reads; checked against its parser when installed."""
    from keepbest.handoff import folder_env
    env = folder_env(True, {"a:b", ".", "x/y"})
    assert env == {"MO_SUBFOLDERS": "include", "MO_EXCLUDE_FOLDERS": '[".", "a:b", "x/y"]'}
    config = pytest.importorskip("media_optimizer.config")
    assert config.parse_value(config.RULES["exclude_folders"], env["MO_EXCLUDE_FOLDERS"]) == [".", "a:b", "x/y"]


def test_failing_optimizer_still_returns_to_keepbest(one_copy, run_cli, optimizer):
    optimizer(exit_code=3)
    result = run_cli(one_copy.path, keys=["", "m", "b", "q"])       # back to the settings afterwards
    assert BACK in result.stdout
    assert len(asked(result, "StartScreen")) == 2                   # the start screen again
    assert result.returncode == 0


def test_ctrl_c_in_media_optimizer_does_not_stop_keepbest(one_copy, run_cli, optimizer):
    # Ctrl+C goes to the whole foreground process group; the stand-in sends it to keepbest.
    optimizer(script="kill -INT $PPID; sleep 0.5")
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert "aborted by user" not in result.stdout
    assert BACK in result.stdout
    assert result.returncode == 0


def test_sigterm_to_keepbest_is_passed_on(one_copy, run_cli, optimizer, tmp_path):
    got = tmp_path / "got-term"
    optimizer(script=f"trap 'echo TERM > \"{got}\"; exit 143' TERM\nkill -TERM $PPID\nsleep 5 & wait")
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert got.read_text().strip() == "TERM"
    assert BACK in result.stdout


def test_sigterm_while_starting_is_passed_on(fake_command, monkeypatch, tmp_path):
    """A SIGTERM that arrives while media-optimizer is being started must not stop
    keepbest (it did about one time in five): it is passed on once the child runs."""
    import os
    import signal
    import subprocess

    from keepbest import handoff

    fake_command("media-optimizer", script="sleep 5")
    real_popen = subprocess.Popen

    def popen_with_sigterm(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)     # right in the window before the child exists
        return real_popen(*args, **kwargs)

    def stop(signum, frame):                     # keepbest's own handler (cli.stop_on_signals)
        raise KeyboardInterrupt

    monkeypatch.setattr(handoff.subprocess, "Popen", popen_with_sigterm)
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        try:
            code = handoff.run(tmp_path, lambda line: None)
        except KeyboardInterrupt:
            pytest.fail("keepbest stopped on a SIGTERM meant for media-optimizer")
        assert signal.getsignal(signal.SIGTERM) is stop                # restored afterwards
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code == -signal.SIGTERM                                     # the child got it


def test_a_resumed_session_passes_on_its_own_folder_choice(library, run_cli, optimizer):
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=85)
    library.photo("other/c.png", 3)
    run_cli(library.path, "-r", "--exclude", "other", "-i", keys=["", "q", ""])   # quit at the group, save
    command = optimizer()
    # without -r: resume, keep #0, finish, media-optimizer, quit
    result = run_cli(library.path, "-i", keys=["", "", "", "m", "q"])
    assert "Resuming" in result.stdout
    assert asked(result, "StartScreen")[0]["moreInSubfolders"] == 0   # the session's folders are counted
    assert command.folders() == 'include ["other"]'


def test_m_after_media_optimizer_ran_is_not_offered_again(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["", "m", "m", "q"])     # the second m has no button
    assert [key for _, key in result.typos] == ["m"]
    assert len(command.calls()) == 1


# --- media-optimizer with a page of its own ---------------------------------------------------
def test_media_optimizer_with_a_page_gets_the_tab(one_copy, run_cli, optimizer, tmp_path):
    """It is started with its page and keepbest's address to lead back to; keepbest
    reads its address from its output and the page moves the tab there."""
    command = optimizer(page=True, script="echo 'media-optimizer is running at "
                                          "http://127.0.0.1:9/?token=abc_DEF-1'\nsleep 0.5")
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert result.returncode == 0 and BACK in result.stdout
    first = asked(result, "EndScreen")[0]
    assert dict(first["menu"][0][1])["m"] == "Optimize this folder with media-optimizer (on its page)"
    (call,) = command.calls()
    assert call[:7] == ["run", str(one_copy.path), "--web", "--no-browser", "--return-url", call[5], "--return-name"]
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/", call[5]) and call[7] == "keepbest" and "-y" not in call
    assert command.env() == ["keepbest", "0"]
    (tool,) = [e for e in result.events if e["kind"] == "tool"]
    assert tool["handoff"] and tool["url"] == "http://127.0.0.1:9/?token=abc_DEF-1" and tool["code"] == 0


def test_only_a_page_on_this_computer_is_followed(one_copy, run_cli, optimizer):
    optimizer(page=True, script="echo 'media-optimizer is running at http://example.com:80/?token=abc'")
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    (tool,) = [e for e in result.events if e["kind"] == "tool"]
    assert tool["url"] is None


def test_the_page_is_not_gone_while_the_tab_is_on_media_optimizers(monkeypatch):
    from keepbest import web
    page = web.WebFront(echo=False)
    page.show("tool_start", {"name": "media-optimizer", "command": "media-optimizer run x --web", "handoff": True})
    assert page.away
    page.show("tool_end", {"code": 0})
    assert page.away                                            # coming back takes a moment
    monkeypatch.setattr(web, "HANDOFF_GRACE", 0.0)
    assert not page.away
    page.show("tool_start", {"name": "media-optimizer", "command": "media-optimizer run -y x"})
    assert not page.away                                        # its output box: the tab stays here


@pytest.mark.skipif(__import__("os").name == "nt", reason="reads the pipe to its end there")
def test_the_last_lines_are_not_lost_when_the_child_ends_between_two_looks(monkeypatch):
    """The wait times out, then the child writes its last line and ends before
    keepbest checks whether it is still running."""
    import selectors
    import subprocess
    from keepbest import handoff
    child = subprocess.Popen(["sh", "-c", "echo last"], stdout=subprocess.PIPE)
    child.wait()

    class TimesOutOnce(selectors.DefaultSelector):
        waited = False

        def select(self, timeout=None):
            if timeout and not self.waited:  # the line arrives just after this wait
                self.waited = True
                return []
            return super().select(timeout)
    monkeypatch.setattr(handoff.selectors, "DefaultSelector", TimesOutOnce)
    lines = []
    handoff._pass_on(child, lines.append)
    assert lines == ["last"]


# --- its results ---------------------------------------------------------------------------
def _writes_results(results: dict) -> str:
    """A stand-in's script part: the results file after --results (the arguments stay as they are)."""
    import json
    return (f"prev=; for a in \"$@\"; do [ \"$prev\" = --results ] && out=\"$a\"; prev=\"$a\"; done\n"
            f"printf '%s' '{json.dumps(results)}' > \"$out\"")


def test_its_results_are_shown_next_to_keepbests(one_copy, run_cli, optimizer):
    """One that writes its results gets a file for them; its numbers come back
    on the page and in the log, with the space both freed together."""
    command = optimizer(help_text=RESULTS_HELP,
                        script="echo 'media-optimizer is running at http://127.0.0.1:9/?token=abc'\n"
                               + _writes_results(RESULTS))
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert result.returncode == 0 and BACK in result.stdout, result.stdout
    (call,) = command.calls()
    assert call[-2] == "--results" and call[-1].endswith("results.json")
    assert not __import__("os").path.exists(call[-1]), "the file goes with keepbest's temp folder"
    (tool,) = [e for e in result.events if e["kind"] == "tool"]
    assert tool["results"]["tiles"] == [["Converted", 3], ["Space saved", "2.0 KB"], ["Skipped", 0], ["Failed", 1]]
    assert tool["results"]["stages"] == ["Images: 3 converted, 1 failed, 5.0 KB → 3.0 KB",
                                         "GIFs: done in the run it resumed"]
    assert "Together with the" in tool["results"]["together"] and "less in this folder" in result.stdout
    assert "media-optimizer: Converted 3 · Space saved 2.0 KB · Skipped 0 · Failed 1" in result.stdout


def test_an_older_one_gets_no_results_file(one_copy, run_cli, optimizer):
    command = optimizer(page=True)
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert "--results" not in command.calls()[0]
    assert "without a run" not in result.stdout, "it may have run: it just writes no results"


def test_closed_without_a_run(one_copy, run_cli, optimizer):
    """Its page was closed before anything ran: no results file, and keepbest says so."""
    optimizer(help_text=RESULTS_HELP, script="echo 'media-optimizer is running at http://127.0.0.1:9/?token=abc'")
    result = run_cli(one_copy.path, keys=["", "m", "q"])
    assert "media-optimizer was closed without a run." in result.stdout
    (tool,) = [e for e in result.events if e["kind"] == "tool"]
    assert tool["results"] is None


def test_results_keepbest_cannot_read_are_left_out(tmp_path):
    from keepbest import handoff
    path = tmp_path / "results.json"
    assert handoff.read_results(path) is None                     # none written
    for text in ["{", "[]", '{"v": 2, "stages": []}', '{"v": 1}']:
        path.write_text(text)
        assert handoff.read_results(path) is None, text
    path.write_text(__import__("json").dumps(RESULTS))
    assert handoff.read_results(path) == RESULTS


def test_the_summary_of_a_preview_and_of_a_stopped_run():
    from keepbest import handoff
    dry = {**RESULTS, "dry_run": True, "converted": 0, "failed": 0, "before": 0, "after": 0,
           "stages": [{"title": "Images", "state": "done", "outcomes": {"dry": 4}, "before": 0}]}
    s = handoff.summary(dry, freed=10_000)
    assert s["tiles"][0] == ("Would be converted", 4) and s["dry"] and s["together"] is None
    stopped = {**RESULTS, "code": 130, "stages": [{**RESULTS["stages"][0], "state": "running"},
                                                  {"title": "Videos", "state": "waiting", "outcomes": {}}]}
    assert handoff.summary(stopped)["stages"] == ["Images: 3 converted, 1 failed, 5.0 KB → 3.0 KB (stopped here)",
                                                  "Videos: not reached"]
    assert handoff.summary(RESULTS)["together"] is None, "keepbest freed nothing"
    assert handoff.summary(None) is None
