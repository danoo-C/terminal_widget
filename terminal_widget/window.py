"""The borderless widget window, and the two modes it can be in.

Config mode  -- settings app open: drag from anywhere, resize from the edges.
Locked mode  -- settings app closed: geometry frozen, still a live terminal.

Nothing here decides *which* mode is active; that is the settings app's
connection state. This module only implements the two behaviours.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .config import (
    MIN_OPACITY_WINDOW,
    OPACITY_BACKGROUND,
    STACKING_DESKTOP,
    STACKING_NORMAL,
    STACKING_TOP,
    Config,
    environment_for,
    resolve_shell,
    working_dir_for,
)
from .platform_info import DISPLAY_NAME, icon_path
from .renderer import TerminalView
from .session import TerminalSession

#: How close to an edge a press counts as "resize" rather than "move".
RESIZE_MARGIN = 7

#: What the widget *is*, before any stacking hint. ``Qt::Tool`` rather than
#: ``Qt::Window`` is what keeps the shell from giving it a taskbar button and
#: an Alt+Tab slot: a widget you can switch to is not part of the desktop.
BASE_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint

_NO_HINT = Qt.WindowType(0)

#: Config value -> the flag that puts the window in that layer.
_STACKING_HINTS: dict[str, Qt.WindowType] = {
    STACKING_DESKTOP: Qt.WindowType.WindowStaysOnBottomHint,
    STACKING_NORMAL: _NO_HINT,
    STACKING_TOP: Qt.WindowType.WindowStaysOnTopHint,
}

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

    #: Emitted once the window has closed and its session is torn down.
    #:
    #: Qt clears ``WA_QuitOnClose`` for every window type outside
    #: Widget/Window/Dialog, so a ``Qt::Tool`` window closing does *not* end
    #: ``app.exec()`` -- and setting the attribute back does not stick, because
    #: Qt re-clears it on every flags change. Without this signal the widget
    #: would vanish when its shell exited and leave a headless process behind.
    closed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__(None)
        self._config = config
        self._config_mode = False
        self._drag_origin = None
        self._translucent = False
        self._restacking = False

        self.setWindowTitle(DISPLAY_NAME)
        icon = icon_path()
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        # Set before the window is ever shown, so launching in any layer
        # creates exactly one native window and never needs a restack.
        self.setWindowFlags(self._window_flags())
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
        self.session = TerminalSession(cols, rows, self._config.scrollback, self)
        self.session.screenUpdated.connect(self.view.update)
        self.session.ended.connect(self._on_session_ended)
        self.view.session = self.session
        self.session.start(
            resolve_shell(self._config),
            environment_for(self._config),
            working_dir_for(self._config),
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
        # Assigned before the restack: _stacking_hint() reads it.
        self._config_mode = enabled
        self._restack()
        self.view.set_config_mode(enabled)
        self._sync_overlay()
        self._overlay.setVisible(enabled)
        if enabled:
            # Arranging the widget starts with being able to see it, and from
            # the desktop layer -- or from under whatever else is open -- you
            # cannot. The restack alone does not do this: it is a no-op when
            # the configured layer is already the normal one.
            self.bring_to_front()
        else:
            self.unsetCursor()
            self._drag_origin = None
        self.update()

    # -- Stacking ----------------------------------------------------

    def _stacking_hint(self) -> Qt.WindowType:
        """The stacking flag that should be in effect right now.

        Config mode overrides the setting. You cannot arrange a widget that is
        underneath the settings app, so it joins the normal window order for
        as long as settings is open and drops back the moment it closes.
        """
        if self._config_mode:
            return _NO_HINT
        return _STACKING_HINTS.get(
            self._config.stacking, Qt.WindowType.WindowStaysOnBottomHint
        )

    def _window_flags(self) -> Qt.WindowType:
        return BASE_FLAGS | self._stacking_hint()

    def _restack(self) -> None:
        """Re-apply the window flags, putting back what a flags change drops.

        Qt routes a flags change through ``setParent()``, which hides the
        window and, on Windows, destroys and recreates the native one. Anything
        the *platform* window owns -- placement, translucency, opacity -- has
        to be re-asserted afterwards. Anything Qt owns survives on its own: the
        child widgets, the overlay, and the session, whose PTY lives on a
        QThread rather than on the native window and never notices.

        One ``setWindowFlags`` rather than two ``setWindowFlag`` calls, because
        going from one layer to another clears one hint and sets another, and
        each call would cost its own hide-and-recreate cycle.
        """
        wanted = self._window_flags()
        if wanted == self.windowFlags():
            return
        was_visible = self.isVisible()
        # Client coordinates, for the reason _emit_geometry documents at
        # length: pos() would be the frame position and would drift.
        geom = self.geometry()
        self._restacking = True
        try:
            self.setWindowFlags(wanted)
            if was_visible:
                self.setGeometry(geom)
                # Re-showing must not steal focus from whatever the user is
                # actually working in; the widget is furniture.
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                self.show()
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
                # Again: a window manager has its say about placement once the
                # window is mapped, not before.
                self.setGeometry(geom)
                self._apply_window_opacity()
                self._sync_overlay()
                self.view.setFocus()
        finally:
            self._restacking = False

    def bring_to_front(self) -> None:
        """Make the widget visible and focused, whatever layer it lives in.

        The only way back to a window with no taskbar button and no Alt+Tab
        slot, so it has to work from the desktop layer and from minimised.
        ``raise_()`` is a deliberate no-op on Windows while the bottom hint is
        set -- Qt's platform plugin refuses it -- so ``activateWindow()`` is
        the half that does the work there.
        """
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        self.view.setFocus()

    def _sync_overlay(self) -> None:
        """Keep the config affordance the size of the window, and on top."""
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

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
        if config.scrollback != previous.scrollback and self.session is not None:
            self.session.set_scrollback(config.scrollback)
        if config.stacking != previous.stacking:
            # Before the geometry block, so the restack's own geometry restore
            # cannot land on top of the size we were actually asked for.
            #
            # Deliberately deferred while the settings app is open: the setting
            # says where the widget sits when you are *not* arranging it, and
            # _stacking_hint() holds the normal order for as long as config
            # mode lasts, so this is a no-op until settings goes away. Same
            # bargain the shell and working-directory fields already make.
            self._restack()

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
            # Config.clamped() already keeps window mode above the floor;
            # this is the same number, so the two can never drift apart.
            self.setWindowOpacity(
                max(MIN_OPACITY_WINDOW / 100, self._config.opacity / 100)
            )

        if want_translucent != self._translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, want_translucent)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, want_translucent)
            self._translucent = want_translucent
        self.update()

    # -- Geometry writeback ------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_overlay()
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
        3. A stacking change hides and re-shows the window, and a window
           manager gets to place it again on the way back. That is the same
           trap as (2) but in config mode, where the guard above does not
           apply -- hence ``_restacking``.

        The guard belongs here and not in ``resizeEvent``/``moveEvent``: those
        also re-sync the overlay, which has to keep happening throughout.
        """
        if self._restacking or not self._config_mode:
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
        # Only after the session is down: whoever is listening quits the
        # application, and the shell must already be gone by then.
        self.closed.emit()
