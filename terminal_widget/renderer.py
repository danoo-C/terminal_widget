"""Draws the pyte screen buffer and turns key presses into shell input."""

from __future__ import annotations

import pyte
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QWidget

from .config import OPACITY_BACKGROUND, Config
from .keys import DECCKM, sequence_for
from .session import TerminalSession

#: pyte names the yellow slots "brown"; everything else is conventional.
ANSI_COLORS = {
    "black": "#1e1e1e",
    "red": "#cd3131",
    "green": "#0dbc79",
    "brown": "#e5e510",
    "blue": "#2472c8",
    "magenta": "#bc3fbc",
    "cyan": "#11a8cd",
    "white": "#e5e5e5",
    "brightblack": "#666666",
    "brightred": "#f14c4c",
    "brightgreen": "#23d18b",
    "brightbrown": "#f5f543",
    "brightblue": "#3b8eea",
    "brightmagenta": "#d670d6",
    "brightcyan": "#29b8db",
    "brightwhite": "#ffffff",
}


class TerminalView(QWidget):
    """The terminal grid. Owns no window behaviour -- that lives in window.py."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._config_mode = False
        self._color_cache: dict[str, QColor] = {}
        self.session: TerminalSession | None = None

        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setCursor(Qt.IBeamCursor)
        self._apply_font()

    # -- Appearance --------------------------------------------------

    def _apply_font(self) -> None:
        font = QFont(self._config.font_family, self._config.font_size)
        font.setStyleHint(QFont.Monospace)
        font.setFixedPitch(True)
        self.setFont(font)
        fm = QFontMetricsF(font)
        # Monospace, so any glyph gives the cell width.
        self.cell_w = fm.horizontalAdvance("M")
        self.cell_h = fm.height()
        self._ascent = fm.ascent()
        if self.cell_w <= 0 or self.cell_h <= 0:  # pathological font
            self.cell_w, self.cell_h, self._ascent = 8.0, 16.0, 12.0

    def apply_config(self, config: Config) -> None:
        """Adopt new settings and repaint. Called live from config mode."""
        self._config = config
        self._apply_font()
        self._color_cache.clear()
        self.update()

    def set_config_mode(self, enabled: bool) -> None:
        self._config_mode = enabled
        # In config mode the whole surface is a drag handle, so an I-beam
        # would be a lie about what a click does.
        self.setCursor(Qt.ArrowCursor if enabled else Qt.IBeamCursor)

    # -- Grid sizing -------------------------------------------------

    def grid_size(self) -> tuple[int, int]:
        """(cols, rows) that fit in the current widget size."""
        cols = max(1, int(self.width() // self.cell_w))
        rows = max(1, int(self.height() // self.cell_h))
        return cols, rows

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if self.session is not None:
            cols, rows = self.grid_size()
            self.session.resize(cols, rows)

    # -- Colors ------------------------------------------------------

    def _color(self, name: str, default: QColor) -> QColor:
        if name == "default":
            return default
        cached = self._color_cache.get(name)
        if cached is not None:
            return cached
        hex_value = ANSI_COLORS.get(name)
        if hex_value is None and len(name) == 6:
            try:
                int(name, 16)
                hex_value = "#" + name
            except ValueError:
                hex_value = None
        color = QColor(hex_value) if hex_value else default
        self._color_cache[name] = color
        return color

    # -- Painting ----------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setFont(self.font())

        default_fg = QColor(self._config.foreground)
        default_bg = QColor(self._config.background)

        # In background-only mode the backdrop carries the alpha and glyphs
        # stay fully opaque. In window mode the window itself is faded, so
        # we paint the backdrop solid and let Qt do the blending.
        if self._config.opacity_mode == OPACITY_BACKGROUND:
            default_bg.setAlpha(int(self._config.opacity * 255 / 100))

        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), default_bg)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if self.session is None:
            painter.end()
            return

        screen = self.session.screen
        cols, rows = screen.columns, screen.lines
        opaque_bg = QColor(self._config.background)

        for y in range(rows):
            line = screen.buffer[y]
            for start, end, char in _runs(line, cols):
                fg_name, bg_name = char.fg, char.bg
                bold = char.bold
                if char.reverse:
                    fg_name, bg_name = bg_name, fg_name
                    # A reversed cell needs a real backdrop to invert against.
                    if bg_name == "default":
                        bg_name = "__fg__"

                fg = default_fg if fg_name == "default" else self._color(fg_name, default_fg)
                if fg_name == "__fg__":
                    fg = default_fg
                if bold and fg_name in ANSI_COLORS and not fg_name.startswith("bright"):
                    fg = self._color("bright" + fg_name, fg)

                rect = QRectF(
                    start * self.cell_w,
                    y * self.cell_h,
                    (end - start) * self.cell_w,
                    self.cell_h,
                )

                if bg_name == "__fg__":
                    painter.fillRect(rect, default_fg)
                    fg = opaque_bg
                elif bg_name != "default":
                    painter.fillRect(rect, self._color(bg_name, default_bg))

                text = "".join(line[x].data for x in range(start, end))
                if text.strip():
                    font = painter.font()
                    if font.bold() != bold or font.italic() != char.italics:
                        font.setBold(bold)
                        font.setItalic(char.italics)
                        painter.setFont(font)
                    painter.setPen(fg)
                    painter.drawText(QPointF(rect.x(), rect.y() + self._ascent), text)
                if char.underscore:
                    painter.setPen(fg)
                    baseline = rect.y() + self._ascent + 1
                    painter.drawLine(
                        int(rect.x()), int(baseline), int(rect.right()), int(baseline)
                    )

        self._paint_cursor(painter, screen, default_fg, opaque_bg)
        painter.end()

    def _paint_cursor(
        self, painter: QPainter, screen: pyte.Screen, fg: QColor, bg: QColor
    ) -> None:
        cursor = screen.cursor
        if cursor.hidden or self._config_mode:
            return
        rect = QRectF(
            cursor.x * self.cell_w, cursor.y * self.cell_h, self.cell_w, self.cell_h
        )
        if self.hasFocus():
            painter.fillRect(rect, fg)
            try:
                char = screen.buffer[cursor.y][cursor.x].data
            except (KeyError, IndexError):
                char = " "
            if char.strip():
                painter.setPen(bg)
                painter.drawText(QPointF(rect.x(), rect.y() + self._ascent), char)
        else:
            # Unfocused: hollow box, the usual terminal convention.
            painter.setPen(fg)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    # -- Input -------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.session is None or not self.session.alive:
            return
        app_cursor = DECCKM in self.session.screen.mode
        data = sequence_for(event, app_cursor)
        if data is None:
            super().keyPressEvent(event)
            return
        self.session.write(data)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # In config mode every click belongs to the window (drag/resize), so
        # refuse it here and let it propagate to the parent.
        if self._config_mode:
            event.ignore()
            return
        self.setFocus(Qt.MouseFocusReason)
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()  # repaint the cursor in its focused form

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()


def _runs(line, cols: int):
    """Group a row into (start, end, char) spans sharing one set of attributes.

    Painting per span instead of per cell cuts draw calls by roughly the
    average run length, which matters because we repaint the whole grid.
    """
    if cols <= 0:
        return
    start = 0
    prev = line[0]
    for x in range(1, cols):
        char = line[x]
        if (
            char.fg != prev.fg
            or char.bg != prev.bg
            or char.bold != prev.bold
            or char.italics != prev.italics
            or char.underscore != prev.underscore
            or char.reverse != prev.reverse
        ):
            yield start, x, prev
            start, prev = x, char
    yield start, cols, prev
