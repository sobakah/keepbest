"""Terminal helpers: text width, truncation, and tables."""

from keepbest import ui


def test_wide_characters_count_double():
    assert ui.display_width("abc") == 3
    assert ui.display_width("고양이") == 6
    assert ui.display_width("\x1b[31mred\x1b[0m") == 3   # colour codes take no space


def test_truncating_paths_keeps_the_file_name():
    shortened = ui.truncate("Urlaub/Strand/beach_q80.jpg", 12, keep_end=True)
    assert shortened.startswith("…") and shortened.endswith("q80.jpg")
    assert ui.display_width(shortened) <= 12


def test_tables_have_no_trailing_spaces(capsys, monkeypatch):
    monkeypatch.setattr(ui, "terminal_width", lambda default=80: 80)
    ui.print_table([ui.Column(""), ui.Column("File", flex=True)],
                   [[("▶", ""), ("kept.png", ui.StyleUI.BOLD)], [" ", "other.png"]])
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line == line.rstrip() for line in lines)
