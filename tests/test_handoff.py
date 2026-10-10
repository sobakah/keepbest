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
        command = fake_command("media-optimizer", exit_code=exit_code,
                               script=f"echo \"$MO_CALLER $MO_HOLD\" >> '{env_log}'\n{script}")
        command.env = lambda: env_log.read_text().split() if env_log.exists() else []
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


def test_a_partial_scan_warns_that_it_covers_all_subfolders(one_copy, run_cli, optimizer):
    optimizer()
    result = run_cli(one_copy.path, keys=["", "m", "q"])            # without -r
    assert "includes all subfolders" in result.stdout


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
