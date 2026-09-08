"""Tests for the wheel-driven viewport over the scrollback."""

import time

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent


def feed(window, *rows):
    window.view.session.stream.feed("\r\n".join(rows))


def numbers(count, prefix=""):
    return [f"{prefix}{i}" for i in range(count)]


def wheel(view, notches):
    """One notch is 120 units, the same as a physical wheel click."""
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(view.mapToGlobal(QPoint(10, 10))),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(event)
    return event


def key(view, code, mods=Qt.KeyboardModifier.NoModifier, text=""):
    event = QKeyEvent(QEvent.Type.KeyPress, code, mods, text)
    view.keyPressEvent(event)
    return event


def top_row_text(view):
    screen = view.session.screen
    line = screen.line_at(screen.history_total - view.scroll_offset())
    return "".join(line[x].data for x in range(screen.columns)).rstrip()


def test_starts_at_the_live_bottom(live):
    feed(live, *numbers(60))
    assert live.view.scroll_offset() == 0


def test_wheel_scrolls_back_and_forward(live):
    feed(live, *numbers(60))
    wheel(live.view, 1)
    assert live.view.scroll_offset() > 0
    wheel(live.view, -1)
    assert live.view.scroll_offset() == 0


def test_wheel_does_nothing_in_config_mode(live):
    """In config mode the mouse belongs to the window."""
    feed(live, *numbers(60))
    live.set_config_mode(True)
    event = wheel(live.view, 1)
    assert not event.isAccepted()
    assert live.view.scroll_offset() == 0


def test_a_slow_trackpad_still_scrolls(live):
    """Sub-line deltas accumulate; rounding each event away would make slow
    scrolling silently do nothing."""
    view = live.view
    feed(live, *numbers(60))
    small = int(view.cell_h / 3) + 1
    for _ in range(4):
        view.wheelEvent(
            QWheelEvent(
                QPointF(10, 10),
                QPointF(view.mapToGlobal(QPoint(10, 10))),
                QPoint(0, small),
                QPoint(0, 0),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )
    assert view.scroll_offset() > 0


def test_new_output_does_not_move_what_you_are_reading(live):
    """The anchor is an absolute row, so the bottom moving does not drag the
    view down with it."""
    view = live.view
    feed(live, *numbers(60))
    view.scroll_by(10)
    before = top_row_text(view)
    feed(live, "", *numbers(5, prefix="new"))
    assert top_row_text(view) == before


def test_the_anchor_survives_a_resize(live):
    view = live.view
    feed(live, *numbers(60))
    view.scroll_by(10)
    before = top_row_text(view)
    live.resize(live.width(), live.height() - int(3 * view.cell_h))
    assert top_row_text(view) == before


def test_scrolling_clamps_at_the_oldest_line(live):
    view = live.view
    feed(live, *numbers(60))
    view.scroll_by(10_000)
    assert view.scroll_offset() == len(view.session.screen.history)


def test_the_view_follows_the_bottom_once_lines_are_evicted(live):
    """Nothing can hold a position that no longer exists."""
    view = live.view
    view.session.screen.set_scrollback(4)
    feed(live, *numbers(60))
    view.scroll_to_top()
    feed(live, "", *numbers(20, prefix="more"))
    assert view.scroll_offset() <= 4


def test_typing_snaps_to_the_bottom(live):
    view = live.view
    feed(live, *numbers(60))
    view.scroll_by(10)
    key(view, Qt.Key.Key_A, text="a")
    assert view.scroll_offset() == 0


def test_shift_page_up_scrolls_instead_of_reaching_the_shell(live):
    view = live.view
    feed(live, *numbers(60))
    key(view, Qt.Key.Key_PageUp, Qt.KeyboardModifier.ShiftModifier)
    assert view.scroll_offset() > 0
    key(view, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ShiftModifier)
    assert view.scroll_offset() == 0


def test_shift_home_and_end_reach_both_ends(live):
    view = live.view
    feed(live, *numbers(60))
    key(view, Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier)
    assert view.scroll_offset() == len(view.session.screen.history)
    key(view, Qt.Key.Key_End, Qt.KeyboardModifier.ShiftModifier)
    assert view.scroll_offset() == 0


def test_plain_page_up_still_goes_to_the_shell(live, monkeypatch):
    sent = []
    monkeypatch.setattr(live.view.session, "write", sent.append)
    feed(live, *numbers(60))
    key(live.view, Qt.Key.Key_PageUp)
    assert sent == ["\x1b[5~"]


# -- Scroll to the top when the shell starts ---------------------------


def test_the_startup_scroll_shows_the_first_line_the_shell_printed(live):
    """A banner taller than the widget should read from its top, not its
    bottom. At session start history_total is 0, so the top of the scrollback
    *is* the first line of the startup output."""
    live._arm_startup_scroll()
    feed(live, *numbers(60, prefix="banner"))
    live._startup_scroll_fired()
    screen = live.view.session.screen
    assert live.view.scroll_offset() == len(screen.history)
    assert top_row_text(live.view) == "banner0"


def test_the_startup_scroll_gives_way_to_anyone_who_has_started_typing(live):
    """Firing after the user has begun working would yank the view off the
    prompt they are typing at."""
    live._arm_startup_scroll()
    feed(live, *numbers(60, prefix="banner"))
    live.view.session.write("l")
    live._startup_scroll_fired()
    assert live.view.scroll_offset() == 0


def test_the_startup_scroll_disarms_itself_once_it_has_fired(live):
    live._arm_startup_scroll()
    feed(live, *numbers(60))
    live._startup_scroll_fired()
    assert live._startup_scroll is None


def test_the_quiet_period_is_measured_from_the_output_not_the_launch(live):
    """The regression test for a shell that takes a moment to say anything --
    which is every cold WSL start. Arming must not start the clock, or the
    whole quiet period is spent in silence, the timer fires against an empty
    screen, and the banner that arrives a second later is never scrolled to."""
    live._arm_startup_scroll()
    assert not live._startup_scroll.isActive()
    feed(live, "the first thing the shell said")
    live.view.session.screenUpdated.emit()
    assert live._startup_scroll.isActive()


def test_the_startup_scroll_gives_up_on_a_shell_that_never_stops_printing(live):
    """Nothing should scroll to the top minutes into a session because some
    long-running startup command finally drew breath."""
    live._arm_startup_scroll()
    live.view.session.screenUpdated.emit()  # starts the budget
    live._startup_deadline = time.monotonic() - 1  # as if it had run out
    live.view.session.screenUpdated.emit()
    assert live._startup_scroll is None
    feed(live, *numbers(60))
    live._startup_scroll_fired()
    assert live.view.scroll_offset() == 0


def test_scrolling_around_during_startup_wins_over_the_banner(live):
    """The wheel says you are already reading something of your own choosing."""
    live._arm_startup_scroll()
    feed(live, *numbers(60, prefix="banner"))
    live.view.scroll_by(5)
    before = live.view.scroll_offset()
    live._startup_scroll_fired()
    assert live.view.scroll_offset() == before


def test_a_banner_that_fits_leaves_the_view_where_it_was(live):
    """Nothing has scrolled off, so there is nothing to scroll back to."""
    live._arm_startup_scroll()
    feed(live, "just one line")
    live._startup_scroll_fired()
    assert live.view.scroll_offset() == 0
