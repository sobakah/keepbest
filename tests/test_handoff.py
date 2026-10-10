"""Offering media-optimizer after a run and coming back from it."""

import pytest

OFFER = "Optimize this folder with media-optimizer"
BACK = "Back in keepbest"


@pytest.fixture
def one_copy(library):
    library.photo("a.png", 1)
    library.copy_of("a.png", "a copy.png")
    return library


@pytest.fixture
def optimizer(fake_command, tmp_path):
    """A stand-in media-optimizer that also records the variables it was given."""
    env_log = tmp_path / "optimizer.env"

    def make(script: str = "", exit_code: int = 0):
        folders_log = tmp_path / "optimizer.folders"
        command = fake_command("media-optimizer", exit_code=exit_code,
                               script=f"echo \"$MO_CALLER $MO_HOLD\" >> '{env_log}'\n"
                                      f"echo \"$MO_SUBFOLDERS $MO_EXCLUDE_FOLDERS\" >> '{folders_log}'\n{script}")
        command.env = lambda: env_log.read_text().split() if env_log.exists() else []
        command.folders = lambda: folders_log.read_text().strip() if folders_log.exists() else None
        return command
    return make


def test_real_run_offers_media_optimizer_for_the_same_folder(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["", "m", "q"])            # start, optimizer, quit
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert command.calls() == [["run", str(one_copy.path)]]
    assert command.env() == ["keepbest", "0"]                       # caller known, no hold-open
    assert BACK in result.stdout
    assert result.stdout.count(OFFER) == 1                          # not offered twice
    assert result.returncode == 0


def test_carrying_out_a_dry_run_offers_it_too(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, keys=["4", "", "", "m", "q"])   # dry run, start, apply, optimizer
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert OFFER in result.stdout
    assert command.calls() == [["run", str(one_copy.path)]]


def test_dry_run_does_not_offer_it(one_copy, run_cli, optimizer):
    command = optimizer()
    result = run_cli(one_copy.path, "--dry-run", keys=["", "m"])
    assert OFFER not in result.stdout
    assert command.calls(count=0) == []


def test_not_offered_when_not_installed(one_copy, run_cli, tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    result = run_cli(one_copy.path, keys=["", "m"], env={"PATH": str(empty)})
    assert len(one_copy.files()) == 1                           # the copy is gone
    assert OFFER not in result.stdout


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
    assert result.stdout.count("keepbest · Duplicate image finder") == 2   # the start screen again
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
            code = handoff.run(tmp_path)
        except KeyboardInterrupt:
            pytest.fail("keepbest stopped on a SIGTERM meant for media-optimizer")
        assert signal.getsignal(signal.SIGTERM) is stop                # restored afterwards
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code == -signal.SIGTERM                                     # the child got it
