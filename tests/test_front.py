"""The flow talks only to the front end: another one than the terminal sees every
message and question, and nothing reaches the terminal behind its back."""

from contextlib import contextmanager

import pytest

import pictures
from keepbest import front
from keepbest.config import load_config
from keepbest.front import (DecisionScreen, EndScreen, FolderScreen, Front, HandledScreen, OverviewScreen,
                            StartScreen)


class Scripted(Front):
    """Answers screens and yes/no questions from a list, as a page's buttons would."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked, self.shown = [], []

    def show(self, kind, data):
        self.shown.append(kind)

    def ask(self, screen, again=False):
        self.asked.append(screen)
        answer = self.answers.pop(0)
        offered = {key for _, items in screen.menu for key, _ in items}
        # a key the screen offers, Enter for its primary action, or a number from a range like "1-3"
        assert answer in offered or (answer == "" and screen.primary) or answer.lstrip("i").isdigit(), answer
        return answer

    def confirm(self, question, default=False, tone=""):
        self.asked.append(question)
        return self.answers.pop(0)

    def ask_path(self, prompt):
        self.asked.append(prompt)
        return self.answers.pop(0)

    @contextmanager
    def progress(self, label, total):
        class Bar:
            def advance(self, amount=1):
                pass
        yield Bar()

    @contextmanager
    def transient(self, text):
        yield


@pytest.fixture
def run_with(config_file, capfd):
    """Run keepbest in this process with a Scripted front end; returns it."""
    from keepbest import app

    def run(folder, answers, recursive=False, confirm="uncertain", auto=False):
        config, _ = load_config(config_file.path)
        config["_configured_delete_mode"], config["_cli_dry_run"] = config["delete_mode"], False
        settings = {"stages": "1,2,3", "recursive": recursive, "no_ignore": False, "videos": True,
                    "confirm": confirm}
        scripted = Scripted(answers)
        front.use(scripted)
        try:
            with pytest.raises(SystemExit) if not auto else _returns():
                app.run(folder, config, settings, auto=auto)
        finally:
            front.use(None)
        out, err = capfd.readouterr()
        assert out == "" and err == ""
        assert scripted.answers == []      # every answer was asked for
        return scripted
    return run


@contextmanager
def _returns():
    yield


def screens(scripted):
    return [type(q) for q in scripted.asked if not isinstance(q, str)]


def test_a_review_goes_through_the_front_end(library, run_with):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)
    scripted = run_with(library.path, ["", "", "", "q"], confirm="always")
    assert screens(scripted) == [StartScreen, DecisionScreen, OverviewScreen, EndScreen]
    decision = scripted.asked[1]
    assert [m["path"].name for m in decision.group.images] == ["a.png", "a small.jpg"]
    assert library.files() == ["a.png"]
    assert {"banner", "stage3_header", "outcome", "summary", "goodbye"} <= set(scripted.shown)


def test_marks_and_navigation_are_keys(library, run_with):
    library.photo("a.png", 1, size=(640, 480))
    library.variant("a.png", "a small.jpg", change=pictures.downscaled)
    # mark as not duplicates, open it again from the overview, continue, finish, quit
    scripted = run_with(library.path, ["", "i", "1", "", "", "q"], confirm="always")
    assert screens(scripted) == [StartScreen, DecisionScreen, OverviewScreen, HandledScreen,
                                 OverviewScreen, EndScreen]
    assert sorted(library.files()) == [".keepbest_ignore.json", "a small.jpg", "a.png"]


def test_folders_and_confirmations(library, run_with):
    library.photo("a.png", 1)
    library.copy_of("a.png", "sub/a.png")
    library.copy_of("a.png", "sub/deeper/a.png")
    # folder list: switch "sub" off and, asked about the folder below it, say no; done; start; quit
    scripted = run_with(library.path, ["f", "1", False, "", "", "q"], recursive=True)
    assert FolderScreen in screens(scripted)
    assert any(isinstance(q, str) and q.startswith("Also switch") for q in scripted.asked)
    assert library.files() == ["a.png", "sub/a.png"]


def test_the_terminal_asks_nothing():
    """--auto's front end shows messages only; a question there is a bug."""
    from keepbest.terminal import TERMINAL, NeedsPage
    with pytest.raises(NeedsPage):
        TERMINAL.ask(EndScreen(prompt="Action: ", primary="Quit", menu=[]))
    with pytest.raises(NeedsPage):
        TERMINAL.confirm("Delete?")


def test_auto_asks_nothing(library, run_with):
    library.photo("a.png", 1)
    library.copy_of("a.png", "b.png")
    scripted = run_with(library.path, [], auto=True)
    assert scripted.asked == [] and library.files() == ["a.png"]
