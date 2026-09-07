"""Tests for the scrollback screen and the absolute row space it defines."""

import pyte

from terminal_widget.screen import Pos, ScrollbackScreen


def feed(screen: ScrollbackScreen, *rows: str) -> ScrollbackScreen:
    pyte.Stream(screen).feed("\r\n".join(rows))
    return screen


def numbers(count: int) -> list[str]:
    return [str(i) for i in range(count)]


def test_index_keeps_the_line_that_scrolls_off():
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), "a", "b", "c")
    assert screen.history_total == 1
    assert len(screen.history) == 1


def test_history_total_outgrows_a_full_deque():
    """The count is what every view and selection anchor is measured
    against, so it has to keep rising after the deque stops growing."""
    screen = feed(ScrollbackScreen(10, 2, scrollback=3), *numbers(12))
    assert len(screen.history) == 3
    assert screen.history_total == 10


def test_scrollback_off_still_counts_rows():
    screen = feed(ScrollbackScreen(10, 2, scrollback=0), "a", "b", "c", "d")
    assert len(screen.history) == 0
    assert screen.history_total == 2


def test_absolute_rows_round_trip():
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), "one", "two", "three")
    assert screen.first_row == 0
    assert screen.last_row == 2
    assert screen.text_between(Pos(0, 0), Pos(1, 0)) == "one\n"


def test_line_at_returns_none_once_evicted():
    screen = feed(ScrollbackScreen(10, 2, scrollback=2), *numbers(8))
    assert screen.line_at(screen.first_row) is not None
    assert screen.line_at(screen.first_row - 1) is None


def test_shrinking_keeps_the_rows_it_drops():
    """pyte drops rows from the top with delete_lines, which never reaches
    index(), so resize has to feed the history itself."""
    screen = feed(ScrollbackScreen(10, 4, scrollback=10), "a", "b", "c", "d")
    assert screen.history_total == 0
    screen.resize(2, 10)
    assert screen.history_total == 2
    assert screen.text_between(Pos(0, 0), Pos(2, 0)) == "a\nb\n"


def test_full_screen_apps_do_not_pollute_the_scrollback():
    """pyte has no alternate buffer, so vim's redraws land in the normal one
    and would otherwise bury the shell history that belongs there."""
    screen = ScrollbackScreen(10, 2, scrollback=10)
    pyte.Stream(screen).feed("\x1b[?1049h" + "\r\n".join(numbers(6)))
    assert len(screen.history) == 0
    pyte.Stream(screen).feed("\x1b[?1049l" + "\r\n".join(["x", "y", "z"]))
    assert len(screen.history) > 0


def test_erase_in_display_3_clears_history_but_not_the_count():
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), "a", "b", "c", "d")
    total = screen.history_total
    pyte.Stream(screen).feed("\x1b[3J")
    assert len(screen.history) == 0
    assert screen.history_total == total


def test_reset_clears_history_but_not_the_count():
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), "a", "b", "c", "d")
    total = screen.history_total
    screen.reset()
    assert len(screen.history) == 0
    assert screen.history_total == total


def test_set_scrollback_keeps_the_newest():
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), *numbers(8))
    screen.set_scrollback(2)
    assert len(screen.history) == 2
    first = screen.first_row
    assert screen.text_between(Pos(first, 0), Pos(first + 2, 0)) == "4\n5\n"


def test_identical_cells_are_interned():
    """Retired lines are frozen and Char is a value type, so cells can be
    shared -- measured 56.7 MB to 14.6 MB on 5000x70."""
    screen = feed(ScrollbackScreen(10, 2, scrollback=10), "aa", "aa", "aa", "aa")
    assert screen.history[0][0] is screen.history[0][1]
    assert screen.history[0][0] is screen.history[1][0]


def test_text_between_skips_evicted_rows():
    screen = feed(ScrollbackScreen(10, 2, scrollback=2), *numbers(8))
    text = screen.text_between(Pos(0, 0), Pos(screen.last_row + 1, 0))
    assert text.startswith("4")  # 0 through 3 are gone, not an error


def test_text_between_is_empty_for_an_inverted_range():
    screen = ScrollbackScreen(10, 2, scrollback=10)
    assert screen.text_between(Pos(5, 0), Pos(1, 0)) == ""


def test_text_between_strips_trailing_blanks():
    screen = feed(ScrollbackScreen(20, 2, scrollback=10), "hi", "there", "x")
    assert screen.text_between(Pos(0, 0), Pos(1, 0)) == "hi\n"


def test_a_wide_glyph_is_not_doubled():
    """pyte stores a double-width glyph plus an empty cell, so joining the
    cell data gives one character per glyph."""
    screen = ScrollbackScreen(10, 2, scrollback=10)
    pyte.Stream(screen).feed("你好")
    assert screen.text_between(Pos(0, 0), Pos(0, 4)) == "你好"
