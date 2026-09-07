"""The borderless widget window, and the two modes it can be in.

Config mode  -- settings app open: drag from anywhere, resize from the edges.
Locked mode  -- settings app closed: geometry frozen, still a live terminal.

Nothing here decides *which* mode is active; that is the settings app's
connection state. This module only implements the two behaviours.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .config import OPACITY_BACKGROUND, Config, environment_for, resolve_shell
from .renderer import TerminalView
from .session import TerminalSession

#: How close to an edge a press counts as "resize" rather than "move".
RESIZE_MARGIN = 7

_EDGE_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
}


class _ConfigOverlay(QWidget):
    """A dashed border shown only in config mode.

    A separate always-on-top child rather than something the terminal draws,
    so the affordance can never be painted over by shell output. It is
    transparent to the mouse, so drag and resize pass straight through.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor("#4aa3ff"), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        painter.end()


class WidgetWindow(QWidget):
    """The widget: one frameless window wrapping one terminal."""

    #: Emitted whenever the user moves or resizes the window, so the
    #: settings app's geometry fields can follow along live.
    geometryEdited = Signal(int, int, int, int)

    def __init__(self, config: Config) -> None:
        super().__init__(None)
        self._config = config
        self._config_mode = False
        self._drag_origin = None
        self._translucent = False

        self.setWindowTitle("terminal_widget")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)

        self.view = TerminalView(config, self)
        self.view.setMouseTracking(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)

        self._overlay = _ConfigOverlay(self)
        self._overlay.hide()

        self.session: TerminalSession | None = None
        self._apply_window_opacity()
        self.setGeometry(config.x, config.y, config.width, config.height)

    # -- Session -----------------------------------------------------

    def start_session(self) -> None:
        """Spawn the configured shell and wire it to the view."""
        cols, rows = self.view.grid_size()
        self.session = TerminalSession(cols, rows, self)
        self.session.screenUpdated.connect(self.view.update)
        self.session.ended.connect(self._on_session_ended)
        self.view.session = self.session
        self.session.start(
            resolve_shell(self._config), environment_for(self._config)
        )
        self.view.setFocus()

    def _on_session_ended(self) -> None:
        # The shell exited. Closing follows the widget's premise: it is a
        # window that shows one shell, and that shell is gone.
        self.close()

    # -- Modes -------------------------------------------------------

    def set_config_mode(self, enabled: bool) -> None:
        """Switch between config and locked mode."""
        if enabled == self._config_mode:
            return
        self._config_mode = enabled
        self.view.set_config_mode(enabled)
        self._overlay.setGeometry(self.rect())
        self._overlay.setVisible(enabled)
        self._overlay.raise_()
        if not enabled:
            self.unsetCursor()
            self._drag_origin = None
        self.update()

    @property
    def config_mode(self) -> bool:
        return self._config_mode

    @property
    def config(self) -> Config:
        """The settings currently in effect, live geometry included.

        Not the object the window was constructed with: applying settings
        from the settings app swaps in a new one, so anything that saves
        must ask the window rather than hold on to what it loaded.
        """
        return self._config

    # -- Live settings ------------------------------------------------

    def apply_config(self, config: Config) -> None:
        """Adopt new settings immediately (config mode's live preview)."""
        previous = self._config
        self._config = config

        # Snapshot the requested geometry before touching the window. Moving
        # or resizing fires our own geometry writeback, which mutates this
        # very Config -- reading it afterwards would see the window's current
        # size, not the size we were asked for, and silently drop the change.
        target = (config.x, config.y, config.width, config.height)

        self.view.apply_config(config)
        if config.opacity_mode != previous.opacity_mode or config.opacity != previous.opacity:
            self._apply_window_opacity()

        geom = self.geometry()
        if target != (geom.x(), geom.y(), geom.width(), geom.height()):
            self.setGeometry(*target)

    def _apply_window_opacity(self) -> None:
        """Route opacity to the right mechanism for the configured mode."""
        want_translucent = self._config.opacity_mode == OPACITY_BACKGROUND
        if want_translucent:
            # The renderer bakes alpha into the backdrop; the window itself
            # stays fully opaque so glyphs render solid.
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(max(0.1, self._config.opacity / 100))

        if want_translucent != self._translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, want_translucent)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, want_translucent)
            self._translucent = want_translucent
        self.update()

    # -- Geometry writeback ------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        self._emit_geometry()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._emit_geometry()

    def _emit_geometry(self) -> None:
        """Record geometry the *user* chose, in client coordinates.

        Two traps here, both of which caused the widget to drift across the
        screen on every launch:

        1. ``QWidget.x()``/``y()`` report the frame position, while
           ``setGeometry`` takes client coordinates. Some window managers
           (WSLg's among them) put a frame around a frameless window, so
           saving x()/y() and restoring it with setGeometry shifted the
           window by the frame offset each time. ``geometry()`` is in the
           same coordinates we place with, so it round-trips exactly.
        2. Window managers move windows for their own reasons. In locked
           mode the user cannot move anything, so any change is the WM's
           and must not be written back as if it were intent.
        """
        if not self._config_mode:
            return
        geom = self.geometry()
        self._config.x, self._config.y = geom.x(), geom.y()
        self._config.width, self._config.height = geom.width(), geom.height()
        self.geometryEdited.emit(geom.x(), geom.y(), geom.width(), geom.height())

    # -- Drag and resize (config mode only) ---------------------------

    def _edges_at(self, pos) -> Qt.Edge:
        """Which window edges, if any, a point is close enough to grab."""
        edges = Qt.Edge(0)
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._config_mode:
            return
        if self._drag_origin is not None:
            # Fallback path: the platform refused startSystemMove.
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            return
        edges = self._edges_at(event.position().toPoint())
        self.setCursor(_EDGE_CURSORS.get(edges, Qt.CursorShape.SizeAllCursor))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._config_mode or event.button() != Qt.MouseButton.LeftButton:
            return
        handle = self.windowHandle()
        edges = self._edges_at(event.position().toPoint())

        # Prefer the compositor's own move/resize: it behaves correctly on
        # Wayland, snaps like a normal window, and needs no math from us.
        if handle is not None:
            if edges and handle.startSystemResize(edges):
                return
            if not edges and handle.startSystemMove():
                return

        if not edges:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._config_mode:
            self.unsetCursor()

    # -- Shutdown -----------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.session is not None:
            self.session.stop()
            self.session = None
        super().closeEvent(event)
