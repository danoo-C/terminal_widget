import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from terminal_widget import keys
from terminal_widget.keys import sequence_for


def ev(key=0, mods=Qt.KeyboardModifier.NoModifier, text=""):
    return QKeyEvent(QEvent.Type.KeyPress, key, mods, text)


@pytest.mark.parametrize(
    "key,normal,app",
    [
        (Qt.Key.Key_Up, "\x1b[A", "\x1bOA"),
        (Qt.Key.Key_Down, "\x1b[B", "\x1bOB"),
        (Qt.Key.Key_Right, "\x1b[C", "\x1bOC"),
        (Qt.Key.Key_Left, "\x1b[D", "\x1bOD"),
        (Qt.Key.Key_Home, "\x1b[H", "\x1bOH"),
        (Qt.Key.Key_End, "\x1b[F", "\x1bOF"),
    ],
)
def test_cursor_keys_follow_decckm(key, normal, app):
    """Cursor keys switch between CSI and SS3 form under DECCKM."""
    assert sequence_for(ev(key), app_cursor=False) == normal
    assert sequence_for(ev(key), app_cursor=True) == app


@pytest.mark.parametrize(
    "key,expected",
    [
        (Qt.Key.Key_Return, "\r"),
        (Qt.Key.Key_Escape, "\x1b"),
        (Qt.Key.Key_Backspace, "\x7f"),
        (Qt.Key.Key_Delete, "\x1b[3~"),
        (Qt.Key.Key_PageUp, "\x1b[5~"),
        (Qt.Key.Key_F1, "\x1bOP"),
        (Qt.Key.Key_F5, "\x1b[15~"),
        (Qt.Key.Key_F12, "\x1b[24~"),
    ],
)
def test_fixed_sequences(key, expected):
    assert sequence_for(ev(key)) == expected


@pytest.mark.parametrize(
    "key,expected",
    [(Qt.Key.Key_C, "\x03"), (Qt.Key.Key_D, "\x04"), (Qt.Key.Key_A, "\x01"), (Qt.Key.Key_Z, "\x1a")],
)
def test_control_letters(key, expected):
    """Ctrl+C must interrupt, not type a 'c'."""
    assert sequence_for(ev(key, Qt.KeyboardModifier.ControlModifier)) == expected


def test_alt_prefixes_escape():
    assert sequence_for(ev(Qt.Key.Key_B, Qt.KeyboardModifier.AltModifier, "b")) == "\x1bb"


def test_plain_text_passes_through():
    assert sequence_for(ev(Qt.Key.Key_X, text="x")) == "x"


def test_unicode_passes_through():
    assert sequence_for(ev(0, text="é")) == "é"


def test_modifier_only_press_is_ignored():
    """Holding Shift alone must not send anything to the shell."""
    assert sequence_for(ev(Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier, "")) is None


# -- Widget actions ---------------------------------------------------

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.mark.parametrize(
    "key,mods,expected",
    [
        (Qt.Key.Key_PageUp, SHIFT, keys.SCROLL_PAGE_UP),
        (Qt.Key.Key_PageDown, SHIFT, keys.SCROLL_PAGE_DOWN),
        (Qt.Key.Key_Home, SHIFT, keys.SCROLL_TOP),
        (Qt.Key.Key_End, SHIFT, keys.SCROLL_BOTTOM),
        (Qt.Key.Key_C, CTRL | SHIFT, keys.COPY),
        (Qt.Key.Key_V, CTRL, keys.PASTE),
        (Qt.Key.Key_V, CTRL | SHIFT, keys.PASTE),
        (Qt.Key.Key_Insert, SHIFT, keys.PASTE),
    ],
)
def test_widget_chords_are_recognised(key, mods, expected):
    assert keys.action_for(ev(key, mods)) == expected


@pytest.mark.parametrize(
    "key,mods",
    [
        (Qt.Key.Key_PageUp, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_C, CTRL),
        (Qt.Key.Key_A, CTRL),
        (Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier),
    ],
)
def test_everything_else_belongs_to_the_shell(key, mods):
    assert keys.action_for(ev(key, mods)) is None


def test_ctrl_shift_c_is_not_an_interrupt():
    """Shift does not change event.key() for letters, so Ctrl+Shift+C looks
    exactly like Ctrl+C to sequence_for -- which is why action_for has to be
    consulted first."""
    assert sequence_for(ev(Qt.Key.Key_C, CTRL | SHIFT, "\x03")) == "\x03"
    assert keys.action_for(ev(Qt.Key.Key_C, CTRL | SHIFT)) == keys.COPY
