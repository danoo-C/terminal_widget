import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from terminal_widget.keys import sequence_for


def ev(key=0, mods=Qt.NoModifier, text=""):
    return QKeyEvent(QEvent.KeyPress, key, mods, text)


@pytest.mark.parametrize(
    "key,normal,app",
    [
        (Qt.Key_Up, "\x1b[A", "\x1bOA"),
        (Qt.Key_Down, "\x1b[B", "\x1bOB"),
        (Qt.Key_Right, "\x1b[C", "\x1bOC"),
        (Qt.Key_Left, "\x1b[D", "\x1bOD"),
        (Qt.Key_Home, "\x1b[H", "\x1bOH"),
        (Qt.Key_End, "\x1b[F", "\x1bOF"),
    ],
)
def test_cursor_keys_follow_decckm(key, normal, app):
    """Cursor keys switch between CSI and SS3 form under DECCKM."""
    assert sequence_for(ev(key), app_cursor=False) == normal
    assert sequence_for(ev(key), app_cursor=True) == app


@pytest.mark.parametrize(
    "key,expected",
    [
        (Qt.Key_Return, "\r"),
        (Qt.Key_Escape, "\x1b"),
        (Qt.Key_Backspace, "\x7f"),
        (Qt.Key_Delete, "\x1b[3~"),
        (Qt.Key_PageUp, "\x1b[5~"),
        (Qt.Key_F1, "\x1bOP"),
        (Qt.Key_F5, "\x1b[15~"),
        (Qt.Key_F12, "\x1b[24~"),
    ],
)
def test_fixed_sequences(key, expected):
    assert sequence_for(ev(key)) == expected


@pytest.mark.parametrize(
    "key,expected",
    [(Qt.Key_C, "\x03"), (Qt.Key_D, "\x04"), (Qt.Key_A, "\x01"), (Qt.Key_Z, "\x1a")],
)
def test_control_letters(key, expected):
    """Ctrl+C must interrupt, not type a 'c'."""
    assert sequence_for(ev(key, Qt.ControlModifier)) == expected


def test_alt_prefixes_escape():
    assert sequence_for(ev(Qt.Key_B, Qt.AltModifier, "b")) == "\x1bb"


def test_plain_text_passes_through():
    assert sequence_for(ev(Qt.Key_X, text="x")) == "x"


def test_unicode_passes_through():
    assert sequence_for(ev(0, text="é")) == "é"


def test_modifier_only_press_is_ignored():
    """Holding Shift alone must not send anything to the shell."""
    assert sequence_for(ev(Qt.Key_Shift, Qt.ShiftModifier, "")) is None
