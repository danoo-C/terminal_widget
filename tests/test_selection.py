"""Tests for mouse text selection and the clipboard."""

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def feed(window, *rows):
    window.view.session.stream.feed("\r\n".join(rows))


def _event(kind, view, x, y, button, buttons):
    return QMouseEvent(
        kind,
        QPointF(x, y),
        QPointF(view.mapToGlobal(QPoint(int(x), int(y)))),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def press(view, x, y, button=Qt.MouseButton.LeftButton):
    event = _event(QEvent.Type.MouseButtonPress, view, x, y, button, button)
    view.mousePressEvent(event)
    return event


def move(view, x, y):
    event = _event(
        QEvent.Type.MouseMove, view, x, y,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
    )
    view.mouseMoveEvent(event)
    return event


def release(view, x, y):
    event = _event(
        QEvent.Type.MouseButtonRelease, view, x, y,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
    )
    view.mouseReleaseEvent(event)
    return event


def double(view, x, y):
    event = _event(
        QEvent.Type.MouseButtonDblClick, view, x, y,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
    )
    view.mouseDoubleClickEvent(event)
    return event


def drag(view, from_x, from_y, to_x, to_y):
    press(view, from_x, from_y)
    move(view, to_x, to_y)
    release(view, to_x, to_y)


def key(view, code, mods=Qt.KeyboardModifier.NoModifier, text=""):
    event = QKeyEvent(QEvent.Type.KeyPress, code, mods, text)
    view.keyPressEvent(event)
    return event


# -- Selecting --------------------------------------------------------


def test_dragging_selects_text(live):
    view = live.view
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    assert view.selected_text() == "hello"


def test_dragging_across_rows_selects_whole_lines(live):
    view = live.view
    feed(live, "alpha", "beta", "gamma")
    drag(view, 0, 0, 3 * view.cell_w, 2 * view.cell_h)
    assert view.selected_text() == "alpha\nbeta\ngam"


def test_a_selection_survives_new_output(live):
    """Anchors are absolute rows, so text scrolling into the history keeps
    the same identity it had on the live screen."""
    view = live.view
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    feed(live, "", *[f"line{i}" for i in range(40)])
    assert view.selected_text() == "hello"


def test_a_click_clears_the_previous_selection(live):
    view = live.view
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    press(view, 0, 0)
    assert not view.has_selection()


def test_double_click_selects_a_word(live):
    view = live.view
    feed(live, "hello world")
    double(view, int(1.5 * view.cell_w), 0)
    assert view.selected_text() == "hello"


def test_double_click_takes_a_whole_path(live):
    """Paths and URLs should come out in one gesture, not six."""
    view = live.view
    feed(live, "see /usr/local/bin here")
    double(view, int(6 * view.cell_w), 0)
    assert view.selected_text() == "/usr/local/bin"


def test_triple_click_selects_the_line_with_its_newline(live):
    view = live.view
    feed(live, "hello world")
    press(view, 0, 0)
    double(view, 0, 0)
    press(view, 0, 0)
    assert view.selected_text() == "hello world\n"


# -- Clipboard --------------------------------------------------------


def test_ctrl_c_copies_when_there_is_a_selection(live, monkeypatch):
    """Copying must not double as an interrupt, or it would kill whatever
    is running every time someone copied."""
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    key(view, Qt.Key.Key_C, CTRL, "\x03")
    assert QGuiApplication.clipboard().text() == "hello"
    assert sent == []
    assert not view.has_selection()


def test_ctrl_c_interrupts_when_there_is_none(live, monkeypatch):
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    feed(live, "hello world")
    key(view, Qt.Key.Key_C, CTRL, "\x03")
    assert sent == ["\x03"]


def test_ctrl_shift_c_never_interrupts(live, monkeypatch):
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    key(view, Qt.Key.Key_C, CTRL | SHIFT)
    assert QGuiApplication.clipboard().text() == "hello"
    assert sent == []


def test_paste_turns_newlines_into_returns(live, monkeypatch):
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    QGuiApplication.clipboard().setText("one\ntwo")
    key(view, Qt.Key.Key_V, CTRL, "\x16")
    assert sent == ["one\rtwo"]


def test_paste_is_bracketed_when_the_shell_asks(live, monkeypatch):
    """Otherwise every newline in a pasted block runs the instant it lands."""
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    view.session.stream.feed("\x1b[?2004h")
    QGuiApplication.clipboard().setText("one\ntwo")
    key(view, Qt.Key.Key_V, CTRL, "\x16")
    assert sent == ["\x1b[200~one\rtwo\x1b[201~"]


def test_right_click_pastes(live, monkeypatch):
    view = live.view
    sent = []
    monkeypatch.setattr(view.session, "write", sent.append)
    QGuiApplication.clipboard().setText("hi")
    press(view, 0, 0, Qt.MouseButton.RightButton)
    assert sent == ["hi"]


def test_typing_clears_the_selection(live):
    view = live.view
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    key(view, Qt.Key.Key_A, text="a")
    assert not view.has_selection()


# -- Config mode ------------------------------------------------------


def test_config_mode_refuses_every_mouse_event(live):
    """window.py's manual drag fallback only ever sees the events the view
    declines, so refusing the press alone is not enough."""
    view = live.view
    feed(live, "hello world")
    live.set_config_mode(True)
    assert not press(view, 0, 0).isAccepted()
    assert not move(view, 5 * view.cell_w, 0).isAccepted()
    assert not release(view, 5 * view.cell_w, 0).isAccepted()
    assert not view.has_selection()


def test_entering_config_mode_drops_a_selection(live):
    view = live.view
    feed(live, "hello world")
    drag(view, 0, 0, 5 * view.cell_w, 0)
    live.set_config_mode(True)
    assert not view.has_selection()
