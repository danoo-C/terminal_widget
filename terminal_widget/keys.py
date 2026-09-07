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
    Qt.Key_Up: "A",
    Qt.Key_Down: "B",
    Qt.Key_Right: "C",
    Qt.Key_Left: "D",
    Qt.Key_Home: "H",
    Qt.Key_End: "F",
}

#: Keys with one fixed sequence.
_SIMPLE_KEYS = {
    Qt.Key_Return: "\r",
    Qt.Key_Enter: "\r",
    Qt.Key_Tab: "\t",
    Qt.Key_Escape: "\x1b",
    Qt.Key_Backspace: "\x7f",
    Qt.Key_Delete: "\x1b[3~",
    Qt.Key_Insert: "\x1b[2~",
    Qt.Key_PageUp: "\x1b[5~",
    Qt.Key_PageDown: "\x1b[6~",
    Qt.Key_Backtab: "\x1b[Z",
}

_FUNCTION_KEYS = {
    Qt.Key_F1: "\x1bOP",
    Qt.Key_F2: "\x1bOQ",
    Qt.Key_F3: "\x1bOR",
    Qt.Key_F4: "\x1bOS",
    Qt.Key_F5: "\x1b[15~",
    Qt.Key_F6: "\x1b[17~",
    Qt.Key_F7: "\x1b[18~",
    Qt.Key_F8: "\x1b[19~",
    Qt.Key_F9: "\x1b[20~",
    Qt.Key_F10: "\x1b[21~",
    Qt.Key_F11: "\x1b[23~",
    Qt.Key_F12: "\x1b[24~",
}


def sequence_for(event: QKeyEvent, app_cursor: bool = False) -> str | None:
    """Return what to send the shell for ``event``, or None to ignore it."""
    key = event.key()
    mods = event.modifiers()
    ctrl = bool(mods & Qt.ControlModifier)
    alt = bool(mods & Qt.AltModifier)

    if key in _CURSOR_KEYS:
        letter = _CURSOR_KEYS[key]
        return f"\x1bO{letter}" if app_cursor else f"\x1b[{letter}"

    if key in _FUNCTION_KEYS:
        return _FUNCTION_KEYS[key]

    if key in _SIMPLE_KEYS:
        seq = _SIMPLE_KEYS[key]
        if key == Qt.Key_Backspace and ctrl:
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


def _control_sequence(key: int, text: str) -> str | None:
    """Map Ctrl+<key> to its C0 control character."""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key - Qt.Key_A + 1)
    # The remaining C0 codes, in their traditional Ctrl+ spellings.
    extras = {
        Qt.Key_Space: "\x00",
        Qt.Key_BracketLeft: "\x1b",
        Qt.Key_Backslash: "\x1c",
        Qt.Key_BracketRight: "\x1d",
        Qt.Key_AsciiCircum: "\x1e",
        Qt.Key_Underscore: "\x1f",
        Qt.Key_Question: "\x7f",
    }
    if key in extras:
        return extras[key]
    # Fall back to whatever Qt already resolved, if it is a control char.
    if len(text) == 1 and ord(text) < 32:
        return text
    return None
