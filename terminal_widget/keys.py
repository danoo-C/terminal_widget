"""Translate Qt key events into the byte sequences a shell expects.

pyte stores DEC private modes shifted left by five bits, so application
cursor key mode (DECSET 1) lands at 32 in ``screen.mode``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

#: DEC private mode 1 -- application cursor keys, as pyte records it.
DECCKM = 1 << 5

#: Keys that switch between CSI and SS3 form depending on DECCKM.
_CURSOR_KEYS = {
    Qt.Key.Key_Up: "A",
    Qt.Key.Key_Down: "B",
    Qt.Key.Key_Right: "C",
    Qt.Key.Key_Left: "D",
    Qt.Key.Key_Home: "H",
    Qt.Key.Key_End: "F",
}

#: Keys with one fixed sequence.
_SIMPLE_KEYS = {
    Qt.Key.Key_Return: "\r",
    Qt.Key.Key_Enter: "\r",
    Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Escape: "\x1b",
    Qt.Key.Key_Backspace: "\x7f",
    Qt.Key.Key_Delete: "\x1b[3~",
    Qt.Key.Key_Insert: "\x1b[2~",
    Qt.Key.Key_PageUp: "\x1b[5~",
    Qt.Key.Key_PageDown: "\x1b[6~",
    Qt.Key.Key_Backtab: "\x1b[Z",
}

_FUNCTION_KEYS = {
    Qt.Key.Key_F1: "\x1bOP",
    Qt.Key.Key_F2: "\x1bOQ",
    Qt.Key.Key_F3: "\x1bOR",
    Qt.Key.Key_F4: "\x1bOS",
    Qt.Key.Key_F5: "\x1b[15~",
    Qt.Key.Key_F6: "\x1b[17~",
    Qt.Key.Key_F7: "\x1b[18~",
    Qt.Key.Key_F8: "\x1b[19~",
    Qt.Key.Key_F9: "\x1b[20~",
    Qt.Key.Key_F10: "\x1b[21~",
    Qt.Key.Key_F11: "\x1b[23~",
    Qt.Key.Key_F12: "\x1b[24~",
}


def sequence_for(event: QKeyEvent, app_cursor: bool = False) -> str | None:
    """Return what to send the shell for ``event``, or None to ignore it."""
    # QKeyEvent.key() is typed as a plain int; the lookup tables below are
    # keyed by Qt.Key. Qt.Key is an IntEnum, so this changes nothing at
    # runtime -- comparisons and arithmetic still work on it.
    key = Qt.Key(event.key())
    mods = event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)

    if key in _CURSOR_KEYS:
        letter = _CURSOR_KEYS[key]
        return f"\x1bO{letter}" if app_cursor else f"\x1b[{letter}"

    if key in _FUNCTION_KEYS:
        return _FUNCTION_KEYS[key]

    if key in _SIMPLE_KEYS:
        seq = _SIMPLE_KEYS[key]
        if key == Qt.Key.Key_Backspace and ctrl:
            return "\x08"
        return f"\x1b{seq}" if alt else seq

    if ctrl:
        seq = _control_sequence(key, event.text())
        if seq is not None:
            return f"\x1b{seq}" if alt else seq

    text = event.text()
    if not text:
        return None
    return f"\x1b{text}" if alt else text


def _control_sequence(key: Qt.Key, text: str) -> str | None:
    """Map Ctrl+<key> to its C0 control character."""
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(key - Qt.Key.Key_A + 1)
    # The remaining C0 codes, in their traditional Ctrl+ spellings.
    extras = {
        Qt.Key.Key_Space: "\x00",
        Qt.Key.Key_BracketLeft: "\x1b",
        Qt.Key.Key_Backslash: "\x1c",
        Qt.Key.Key_BracketRight: "\x1d",
        Qt.Key.Key_AsciiCircum: "\x1e",
        Qt.Key.Key_Underscore: "\x1f",
        Qt.Key.Key_Question: "\x7f",
    }
    if key in extras:
        return extras[key]
    # Fall back to whatever Qt already resolved, if it is a control char.
    if len(text) == 1 and ord(text) < 32:
        return text
    return None
