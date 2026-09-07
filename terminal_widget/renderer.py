"""Draws the screen buffer and turns key presses into shell input.

Two things beyond drawing live here, because both are really questions about
what the user is looking at rather than about the shell: the viewport into
the scrollback, and the selection. Both are expressed in the absolute row
space defined by :mod:`terminal_widget.screen`, which is what lets them
survive new output, scrolling and resizing without any bookkeeping.
"""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QClipboard,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from .config import OPACITY_BACKGROUND, Config
from .keys import (
    BRACKETED_PASTE,
    COPY,
    DECCKM,
    PASTE,
    SCROLL_BOTTOM,
    SCROLL_PAGE_DOWN,
    SCROLL_PAGE_UP,
    SCROLL_TOP,
    action_for,
    sequence_for,
)
from .screen import Pos, ScrollbackScreen
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

#: Punctuation that reads as part of a word when double-clicking, so paths
#: and URLs come out in one gesture instead of six.
WORD_CHARS = "_-./~:@"

#: How often the view scrolls while a selection is dragged off its edge.
AUTOSCROLL_MS = 50


class TerminalView(QWidget):
    """The terminal grid. Owns no window behaviour -- that lives in window.py."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._config_mode = False
        self._color_cache: dict[str, QColor] = {}
        self.session: TerminalSession | None = None

        #: Absolute row shown at the top of the widget, or None to follow the
        #: live bottom. Stored as an absolute row rather than an offset so
        #: that output arriving, or the window changing size, moves the
        #: bottom without moving what the user is reading.
        self._view_top: int | None = None
        self._wheel_accum = 0.0

        self._anchor: Pos | None = None
        self._caret: Pos | None = None
        self._selecting = False
        self._drag_point: QPointF | None = None
        self._clicks = 0
        self._click_timer = QElapsedTimer()
        self._click_point = QPointF()
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_tick)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._apply_font()

    # -- Appearance --------------------------------------------------

    def _apply_font(self) -> None:
        font = QFont(self._config.font_family, self._config.font_size)
        font.setStyleHint(QFont.StyleHint.Monospace)
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
        if enabled:
            # Dragging the window and dragging a selection are the same
            # gesture, and in config mode the window wins.
            self._selecting = False
            self._autoscroll.stop()
            self.clear_selection()
        # In config mode the whole surface is a drag handle, so an I-beam
        # would be a lie about what a click does.
        self.setCursor(Qt.CursorShape.ArrowCursor if enabled else Qt.CursorShape.IBeamCursor)

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

    # -- Viewport ----------------------------------------------------

    @property
    def _screen(self) -> ScrollbackScreen | None:
        return self.session.screen if self.session is not None else None

    def scroll_offset(self) -> int:
        """How many rows the view sits above the live bottom.

        Derived from the anchor rather than stored, and re-anchored here, so
        that eviction from the scrollback and a return to the bottom are both
        handled in one place instead of at every caller.
        """
        screen = self._screen
        if screen is None or self._view_top is None:
            return 0
        offset = max(0, min(screen.history_total - self._view_top, len(screen.history)))
        self._view_top = None if offset == 0 else screen.history_total - offset
        return offset

    def _set_offset(self, offset: int) -> None:
        screen = self._screen
        if screen is None:
            return
        view_top = None if offset <= 0 else screen.history_total - offset
        if view_top != self._view_top:
            self._view_top = view_top
            self.update()

    def scroll_by(self, lines: int) -> None:
        """Move the view; positive is towards older output."""
        screen = self._screen
        if screen is None:
            return
        self._set_offset(
            max(0, min(self.scroll_offset() + lines, len(screen.history)))
        )

    def scroll_to_bottom(self) -> None:
        self._set_offset(0)

    def scroll_to_top(self) -> None:
        screen = self._screen
        if screen is not None:
            self._set_offset(len(screen.history))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._config_mode or self._screen is None:
            event.ignore()
            return
        pixels = event.pixelDelta().y()
        if pixels:
            self._wheel_accum += pixels / self.cell_h
        else:
            self._wheel_accum += (
                event.angleDelta().y() / 120 * QApplication.wheelScrollLines()
            )
        # Keep the fraction. A trackpad sends a few pixels at a time, and
        # rounding each event away means slow scrolling does nothing at all.
        step = int(self._wheel_accum)
        self._wheel_accum -= step
        if step:
            self.scroll_by(step)
        event.accept()

    # -- Selection ---------------------------------------------------

    def selection(self) -> tuple[Pos, Pos] | None:
        """The selection as an ordered half-open range, or None."""
        if self._anchor is None or self._caret is None or self._anchor == self._caret:
            return None
        first, second = self._anchor, self._caret
        return (first, second) if first <= second else (second, first)

    def has_selection(self) -> bool:
        return self.selection() is not None

    def clear_selection(self) -> None:
        if self._anchor is not None or self._caret is not None:
            self._anchor = self._caret = None
            self.update()

    def selected_text(self) -> str:
        screen, span = self._screen, self.selection()
        if screen is None or span is None:
            return ""
        return screen.text_between(*span)

    def _pos_at(self, point: QPointF) -> Pos:
        """The character boundary a pixel lands on."""
        screen = self._screen
        assert screen is not None
        row = max(0, min(int(point.y() // self.cell_h), screen.lines - 1))
        # Round, not floor: a character joins the selection once the pointer
        # is past its middle, which is what makes dragging feel right.
        col = max(0, min(round(point.x() / self.cell_w), screen.columns))
        return Pos(screen.history_total - self.scroll_offset() + row, col)

    def _select_word(self, pos: Pos) -> None:
        screen = self._screen
        if screen is None:
            return
        line = screen.line_at(pos.row)
        if line is None:
            return

        def is_word(x: int) -> bool:
            char = line[x].data
            return bool(char) and (char.isalnum() or char in WORD_CHARS)

        x = max(0, min(pos.col, screen.columns - 1))
        start, end = x, x
        # A click on punctuation selects just that cell: neither loop runs.
        while start > 0 and is_word(x) and is_word(start - 1):
            start -= 1
        while end + 1 < screen.columns and is_word(x) and is_word(end + 1):
            end += 1
        self._anchor, self._caret = Pos(pos.row, start), Pos(pos.row, end + 1)
        self._selecting = False

    def _select_line(self, pos: Pos) -> None:
        # Ends at column 0 of the next row, so the newline is included.
        self._anchor, self._caret = Pos(pos.row, 0), Pos(pos.row + 1, 0)
        self._selecting = False

    def _click_count(self, point: QPointF) -> int:
        """1 or 3, for a press. Never 2.

        Qt delivers the second click of a double as a MouseButtonDblClick of
        its own rather than a press, so a press that lands right after one is
        the third click -- and a press that does not follow a double is a
        first click, however fast it arrived.
        """
        near = (point - self._click_point).manhattanLength() <= 3
        recent = (
            self._click_timer.isValid()
            and self._click_timer.elapsed() <= QApplication.doubleClickInterval()
        )
        self._clicks = 3 if self._clicks == 2 and recent and near else 1
        self._click_timer.restart()
        self._click_point = point
        return self._clicks

    # -- Clipboard ---------------------------------------------------

    def _copy(self, mode: QClipboard.Mode = QClipboard.Mode.Clipboard) -> bool:
        clipboard = QGuiApplication.clipboard()
        if mode == QClipboard.Mode.Selection and not clipboard.supportsSelection():
            return False
        text = self.selected_text()
        if not text:
            return False
        clipboard.setText(text, mode)
        return True

    def _paste(self, mode: QClipboard.Mode = QClipboard.Mode.Clipboard) -> None:
        if self.session is None or not self.session.alive:
            return
        clipboard = QGuiApplication.clipboard()
        if mode == QClipboard.Mode.Selection and not clipboard.supportsSelection():
            return
        text = clipboard.text(mode)
        if not text:
            return
        # A terminal is sent Return, not a line ending.
        data = text.replace("\r\n", "\r").replace("\n", "\r")
        if BRACKETED_PASTE in self.session.screen.mode:
            # Without the brackets, every newline in a pasted block runs as
            # a command the instant it arrives.
            data = f"\x1b[200~{data}\x1b[201~"
        self.scroll_to_bottom()
        self.session.write(data)

    # -- Colors ------------------------------------------------------

    def backdrop_alpha(self) -> int:
        """The alpha the backdrop is painted with, 0-255.

        Never a true zero. A fully transparent pixel of a layered window is
        click-through on Windows, which would leave the widget unclickable
        everywhere except exactly on a glyph. One part in 255 is invisible
        and still a pixel.
        """
        if self._config.opacity_mode != OPACITY_BACKGROUND:
            # Window mode fades the window itself, so the backdrop under it
            # is painted solid and Qt does the blending.
            return 255
        return max(1, round(self._config.opacity * 255 / 100))

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
        # stay fully opaque; in window mode the window itself is faded.
        default_bg.setAlpha(self.backdrop_alpha())

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), default_bg)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        screen = self._screen
        if screen is None:
            painter.end()
            return

        cols, rows = screen.columns, screen.lines
        opaque_bg = QColor(self._config.background)
        offset = self.scroll_offset()
        kept = len(screen.history)
        top_row = screen.history_total - offset

        for row in range(rows):
            index = kept - offset + row
            # Retired lines are StaticDefaultDicts too, so they read exactly
            # like live ones -- including ones saved at a different width.
            line = screen.history[index] if index < kept else screen.buffer[row - offset]
            self._paint_row(
                painter,
                line,
                row,
                cols,
                default_fg,
                default_bg,
                opaque_bg,
                self._sel_span(top_row + row, cols),
            )

        # The cursor is a live-screen fact; scrolled up, there is nothing for
        # it to mark.
        if not offset:
            self._paint_cursor(painter, screen, default_fg, opaque_bg)
        painter.end()

    def _paint_row(
        self,
        painter: QPainter,
        line,
        row: int,
        cols: int,
        default_fg: QColor,
        default_bg: QColor,
        opaque_bg: QColor,
        selection: tuple[int, int] | None,
    ) -> None:
        top = row * self.cell_h
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
                top,
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

        if selection is not None:
            self._paint_selection(painter, line, top, selection, default_fg, opaque_bg)

    def _paint_selection(
        self,
        painter: QPainter,
        line,
        top: float,
        span: tuple[int, int],
        fg: QColor,
        bg: QColor,
    ) -> None:
        """Invert the selected cells.

        Inversion rather than a tint, for the same reason the cursor uses it:
        it is legible against any configured palette, and it is opaque, so a
        selection can still be read at 0% background opacity. Colour and bold
        are flattened inside the highlight, which is what most terminals do.
        """
        start, end = span
        rect = QRectF(
            start * self.cell_w, top, (end - start) * self.cell_w, self.cell_h
        )
        painter.fillRect(rect, fg)
        text = "".join(line[x].data for x in range(start, end))
        if text.strip():
            font = painter.font()
            if font.bold() or font.italic():
                font.setBold(False)
                font.setItalic(False)
                painter.setFont(font)
            painter.setPen(bg)
            painter.drawText(QPointF(rect.x(), top + self._ascent), text)

    def _sel_span(self, row: int, cols: int) -> tuple[int, int] | None:
        """The selected column range on one absolute row, if any."""
        span = self.selection()
        if span is None:
            return None
        start, end = span
        if not start.row <= row <= end.row:
            return None
        first = start.col if row == start.row else 0
        last = end.col if row == end.row else cols
        # Clamping here is what lets a selection survive a narrowing resize.
        first = max(0, min(first, cols))
        last = max(0, min(last, cols))
        return (first, last) if last > first else None

    def _paint_cursor(
        self, painter: QPainter, screen: ScrollbackScreen, fg: QColor, bg: QColor
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
        action = action_for(event)
        if action is not None and self._handle_action(action):
            event.accept()
            return

        # Ctrl+C copies when there is something to copy and interrupts when
        # there is not, so it can never kill a running program by surprise.
        if (
            self.has_selection()
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and Qt.Key(event.key()) == Qt.Key.Key_C
        ):
            self._copy()
            self.clear_selection()
            event.accept()
            return

        if self.session is None or not self.session.alive:
            return
        app_cursor = DECCKM in self.session.screen.mode
        data = sequence_for(event, app_cursor)
        if data is None:
            super().keyPressEvent(event)
            return
        # Typing says you are done reading what is on screen.
        self.clear_selection()
        self.scroll_to_bottom()
        self.session.write(data)
        event.accept()

    def _handle_action(self, action: str) -> bool:
        screen = self._screen
        page = max(1, (screen.lines if screen is not None else self.grid_size()[1]) - 1)
        if action == COPY:
            self._copy()
            self.clear_selection()
        elif action == PASTE:
            self._paste()
        elif action == SCROLL_PAGE_UP:
            self.scroll_by(page)
        elif action == SCROLL_PAGE_DOWN:
            self.scroll_by(-page)
        elif action == SCROLL_TOP:
            self.scroll_to_top()
        elif action == SCROLL_BOTTOM:
            self.scroll_to_bottom()
        else:
            return False
        return True

    # -- Mouse -------------------------------------------------------
    #
    # Every handler here refuses in config mode. The press has always done
    # so, so the window can start a drag; the move and release must too, or
    # window.py's manual drag fallback never sees the events it needs.

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._config_mode:
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        button = event.button()
        if button == Qt.MouseButton.MiddleButton:
            self._paste(QClipboard.Mode.Selection)  # the X11 convention
        elif button == Qt.MouseButton.RightButton:
            self._paste()  # the cmd.exe one
        elif button == Qt.MouseButton.LeftButton and self._screen is not None:
            point = event.position()
            clicks = self._click_count(point)
            pos = self._pos_at(point)
            if clicks >= 3:
                self._select_line(pos)
            elif clicks == 2:
                self._select_word(pos)
            else:
                self._anchor = self._caret = pos
                self._selecting = True
            self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Qt sends this *instead of* the second press, so the counter has to
        # be told about it or a triple click can never reach three.
        if self._config_mode:
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._screen is not None:
            self._clicks = 2
            self._click_timer.restart()
            self._click_point = event.position()
            self._select_word(self._pos_at(event.position()))
            self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._config_mode:
            event.ignore()
            return
        if not self._selecting or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = event.position()
        self._drag_point = point
        self._caret = self._pos_at(point)
        if point.y() < 0 or point.y() > self.height():
            if not self._autoscroll.isActive():
                self._autoscroll.start()
        else:
            self._autoscroll.stop()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._config_mode:
            event.ignore()
            return
        self._autoscroll.stop()
        self._drag_point = None
        if self._selecting:
            self._selecting = False
            # X11 puts a finished selection on PRIMARY, where middle click
            # pastes it. The clipboard proper still needs Ctrl+C.
            self._copy(QClipboard.Mode.Selection)
        event.accept()

    def _autoscroll_tick(self) -> None:
        if not self._selecting or self._drag_point is None:
            self._autoscroll.stop()
            return
        self.scroll_by(1 if self._drag_point.y() < 0 else -1)
        self._caret = self._pos_at(self._drag_point)
        self.update()

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
