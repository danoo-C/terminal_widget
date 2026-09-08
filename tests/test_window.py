"""Tests for the two modes -- the core of the widget's design."""

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from terminal_widget.config import (
    OPACITY_BACKGROUND,
    OPACITY_WINDOW,
    STACKING_DESKTOP,
    STACKING_NORMAL,
    STACKING_TOP,
    Config,
)


def press_at(window, x, y):
    e = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        window.mapToGlobal(QPoint(x, y)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.mousePressEvent(e)
    return e


# -- Locked mode ------------------------------------------------------


def test_locked_is_the_default(window):
    assert not window.config_mode


def test_locked_press_does_not_arm_a_drag(window):
    window.set_config_mode(False)
    before = (window.x(), window.y())
    press_at(window, 300, 150)
    assert window._drag_origin is None
    assert (window.x(), window.y()) == before


def test_locked_click_focuses_the_terminal(window):
    """Locked mode ignores window management, not input."""
    window.set_config_mode(False)
    e = press_at(window, 300, 150)
    assert e.isAccepted()
    # focusWidget() rather than hasFocus(): the offscreen platform never
    # activates a window, so hasFocus() is always False under test.
    assert window.focusWidget() is window.view


def test_locked_hides_the_overlay(window):
    window.set_config_mode(True)
    window.set_config_mode(False)
    assert not window._overlay.isVisible()


def test_locked_uses_an_ibeam(window):
    window.set_config_mode(False)
    assert window.view.cursor().shape() == Qt.CursorShape.IBeamCursor


# -- Config mode ------------------------------------------------------


def test_config_press_bubbles_to_the_window(window):
    """The view must refuse the press so the window can start a drag."""
    window.set_config_mode(True)
    e = press_at(window, 300, 150)
    assert not e.isAccepted()


def test_config_shows_the_overlay(window):
    window.set_config_mode(True)
    assert window._overlay.isVisible()


def test_config_uses_an_arrow(window):
    """An I-beam would misrepresent what a click does in config mode."""
    window.set_config_mode(True)
    assert window.view.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_overlay_ignores_the_mouse(window):
    """The affordance must never intercept a drag."""
    assert window._overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_mode_toggle_is_idempotent(window):
    for _ in range(3):
        window.set_config_mode(True)
    assert window.config_mode
    for _ in range(3):
        window.set_config_mode(False)
    assert not window.config_mode


# -- Resize edges -----------------------------------------------------


def test_edges_detected_at_borders(window):
    assert window._edges_at(QPoint(1, 150)) & Qt.Edge.LeftEdge
    assert window._edges_at(QPoint(window.width() - 1, 150)) & Qt.Edge.RightEdge
    assert window._edges_at(QPoint(300, 1)) & Qt.Edge.TopEdge
    assert window._edges_at(QPoint(300, window.height() - 1)) & Qt.Edge.BottomEdge


def test_corner_reports_two_edges(window):
    edges = window._edges_at(QPoint(1, 1))
    assert edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge


def test_middle_is_not_an_edge(window):
    """The interior must drag, not resize."""
    assert not window._edges_at(QPoint(300, 150))


# -- Geometry writeback -----------------------------------------------


def test_config_mode_move_updates_config(window):
    window.set_config_mode(True)
    window.setGeometry(321, 222, 640, 320)
    assert (window._config.x, window._config.y) == (321, 222)


def test_config_mode_resize_updates_config(window):
    window.set_config_mode(True)
    window.setGeometry(100, 100, 500, 400)
    assert (window._config.width, window._config.height) == (500, 400)


def test_geometry_signal_fires_in_config_mode(window):
    window.set_config_mode(True)
    seen = []
    window.geometryEdited.connect(lambda *a: seen.append(a))
    window.setGeometry(400, 300, 640, 320)
    assert seen


def test_locked_mode_ignores_geometry_changes(window):
    """A window manager moving the window is not user intent.

    Locked mode offers no way to move the window, so anything that does is
    the WM. Writing that back and saving it made the widget drift across
    the screen a little further on every launch.
    """
    window.set_config_mode(False)
    before = (window._config.x, window._config.y)
    seen = []
    window.geometryEdited.connect(lambda *a: seen.append(a))
    window.setGeometry(777, 666, 500, 400)
    assert (window._config.x, window._config.y) == before
    assert not seen


def test_geometry_round_trips_exactly(window):
    """What we save must be what we can restore.

    setGeometry takes client coordinates but x()/y() report the frame, and
    a WM that frames a frameless window makes those differ. Saving one and
    restoring as the other shifted the window by the frame offset each run.
    """
    window.set_config_mode(True)
    window.setGeometry(432, 321, 555, 444)
    # Snapshot into a detached Config, the way a save/reload would.
    saved = Config(
        x=window._config.x,
        y=window._config.y,
        width=window._config.width,
        height=window._config.height,
    )
    window.setGeometry(0, 0, 200, 200)
    window.apply_config(saved)
    g = window.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (432, 321, 555, 444)


# -- Opacity ----------------------------------------------------------


def test_background_mode_keeps_the_window_opaque(window):
    """Background-only opacity is baked into the backdrop, so the window
    itself must stay fully opaque or glyphs would fade too."""
    window.apply_config(Config(opacity=40, opacity_mode=OPACITY_BACKGROUND))
    assert window.windowOpacity() == 1.0
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_window_mode_fades_the_whole_window(window):
    window.apply_config(Config(opacity=40, opacity_mode=OPACITY_WINDOW))
    assert abs(window.windowOpacity() - 0.4) < 0.01
    assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_apply_config_moves_and_resizes(window):
    window.apply_config(Config(x=250, y=260, width=800, height=500))
    assert (window.x(), window.y()) == (250, 260)
    assert (window.width(), window.height()) == (800, 500)


def test_config_property_tracks_applied_settings(window):
    """apply_config swaps in a new Config object; anything that saves must
    read it back from the window, not hold the one it loaded."""
    replacement = Config(x=11, y=22, width=333, height=244, opacity=70)
    window.apply_config(replacement)
    assert window.config is replacement
    assert (window.config.x, window.config.y) == (11, 22)


def test_zero_opacity_keeps_the_window_itself_opaque(window):
    """At 0% in background mode the backdrop goes but the glyphs stay, which
    only works if the window opacity is left alone."""
    window.apply_config(Config(opacity=0, opacity_mode=OPACITY_BACKGROUND))
    assert window.windowOpacity() == 1.0
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_the_backdrop_never_becomes_a_true_zero(window):
    """A fully transparent pixel of a layered window is click-through on
    Windows, which would leave the widget unclickable off the glyphs."""
    window.apply_config(Config(opacity=0, opacity_mode=OPACITY_BACKGROUND))
    assert window.view.backdrop_alpha() == 1


def test_the_backdrop_alpha_follows_the_opacity(window):
    window.apply_config(Config(opacity=50, opacity_mode=OPACITY_BACKGROUND))
    assert window.view.backdrop_alpha() == 128


def test_window_mode_paints_a_solid_backdrop(window):
    """The window is what fades there; fading the backdrop as well would
    apply the setting twice."""
    window.apply_config(Config(opacity=40, opacity_mode=OPACITY_WINDOW))
    assert window.view.backdrop_alpha() == 255


def test_window_mode_never_fades_to_nothing(window):
    """clamped() holds window mode at the floor, and the window applies the
    same number, so the two cannot drift apart."""
    window.apply_config(Config(opacity=0, opacity_mode=OPACITY_WINDOW).clamped())
    assert abs(window.windowOpacity() - 0.1) < 0.01


# -- Scrollback and the working directory ------------------------------


def test_scrollback_changes_without_a_restart(live):
    live.apply_config(Config(scrollback=17))
    assert live.session.screen.history.maxlen == 17


def test_the_session_is_given_the_configured_working_directory(monkeypatch):
    """The value has to reach the backend, since nothing downstream of the
    spawn can tell you it did not."""
    from terminal_widget import session as session_module

    seen = {}

    class FakeBackend:
        def spawn(self, argv, cols, rows, env=None, cwd=None):
            seen["argv"], seen["cwd"] = argv, cwd

        def read(self, size=4096):
            raise EOFError

        def write(self, data):
            pass

        def resize(self, cols, rows):
            pass

        def is_alive(self):
            return False

        def terminate(self):
            pass

    monkeypatch.setattr(session_module, "open_pty", lambda: FakeBackend())
    session = session_module.TerminalSession(20, 5)
    session.start(["sh"], None, "/tmp")
    session.stop()
    assert seen == {"argv": ["sh"], "cwd": "/tmp"}


def test_config_mode_drag_still_moves_the_window(window):
    """The view gained move and release handlers for selection. If they do
    not decline in config mode, this drag stops reaching the window and
    dragging breaks wherever startSystemMove is unavailable.

    Sent through QApplication rather than called directly, because it is
    Qt's propagation of the declined events that is under test.
    """
    window.set_config_mode(True)

    def send(kind, x, y, button, buttons):
        QApplication.sendEvent(
            window.view,
            QMouseEvent(
                kind,
                QPointF(x, y),
                QPointF(window.view.mapToGlobal(QPoint(x, y))),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    send(QEvent.Type.MouseButtonPress, 300, 150,
         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert window._drag_origin is not None
    before = (window.x(), window.y())
    send(QEvent.Type.MouseMove, 340, 190,
         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert (window.x(), window.y()) == (before[0] + 40, before[1] + 40)


def replace_stacking(window, stacking, **extra):
    """The window's current config with a different layer."""
    from dataclasses import replace

    return replace(window.config, stacking=stacking, **extra)


# -- Window type and stacking -----------------------------------------

BOTTOM = Qt.WindowType.WindowStaysOnBottomHint
TOP = Qt.WindowType.WindowStaysOnTopHint


def base_type(window):
    return window.windowFlags() & Qt.WindowType.WindowType_Mask


def test_the_widget_is_not_an_application_window(window):
    """Qt::Window is what gives a window a taskbar button and an Alt+Tab
    slot, and a widget you can switch to is not part of the desktop."""
    assert base_type(window) == Qt.WindowType.Tool


def test_the_widget_is_still_undecorated(window):
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint


def test_a_tool_window_does_not_quit_the_app_on_its_own(window):
    """Qt clears WA_QuitOnClose for anything outside Widget/Window/Dialog, so
    closing no longer ends the event loop by itself. The `closed` signal is
    what replaces it; this pins the reason it has to exist."""
    assert not window.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)


def test_closing_announces_itself(window):
    seen = []
    window.closed.connect(lambda: seen.append(True))
    window.close()
    assert seen == [True]


def test_the_default_layer_is_behind_other_windows(window):
    assert window.windowFlags() & BOTTOM
    assert not window.windowFlags() & TOP


def test_the_top_layer_asks_to_stay_on_top(qapp):
    from terminal_widget.window import WidgetWindow

    w = WidgetWindow(Config(stacking=STACKING_TOP))
    try:
        assert w.windowFlags() & TOP
        assert not w.windowFlags() & BOTTOM
    finally:
        w.close()


def test_the_normal_layer_asks_for_nothing(qapp):
    """And the base type survives: a stacking hint must never eat it."""
    from terminal_widget.window import WidgetWindow

    w = WidgetWindow(Config(stacking=STACKING_NORMAL))
    try:
        assert not w.windowFlags() & (BOTTOM | TOP)
        assert base_type(w) == Qt.WindowType.Tool
    finally:
        w.close()


def test_a_layer_change_leaves_the_widget_visible(window):
    """Qt hides a window whose flags change. Forgetting the second show()
    would lose the widget outright."""
    window.apply_config(replace_stacking(window, STACKING_TOP))
    assert window.isVisible()


def test_a_layer_change_keeps_the_geometry(window):
    """Same contract as test_geometry_round_trips_exactly, for the path that
    hides and re-shows the window underneath the user."""
    window.setGeometry(321, 222, 640, 320)
    before = window.geometry()
    window.apply_config(
        Config(x=321, y=222, width=640, height=320, stacking=STACKING_TOP)
    )
    assert window.geometry() == before
    assert window.windowFlags() & TOP


def test_a_layer_change_keeps_the_base_type(window):
    window.apply_config(replace_stacking(window, STACKING_NORMAL))
    assert base_type(window) == Qt.WindowType.Tool


def test_geometry_is_not_written_back_while_restacking(window):
    """A window manager gets to place the window again when it comes back
    from a flags change, and that placement is not the user's intent. The
    offscreen platform cannot produce a WM move, so the guard itself is what
    is tested here."""
    window.set_config_mode(True)
    seen = []
    window.geometryEdited.connect(lambda *a: seen.append(a))
    window._restacking = True
    window.setGeometry(777, 666, 400, 200)
    window._restacking = False
    assert (window.config.x, window.config.y) != (777, 666)
    assert seen == []


def test_config_mode_lifts_the_widget_out_of_the_desktop_layer(window):
    """You cannot arrange a widget that sits underneath the settings app."""
    window.set_config_mode(True)
    assert not window.windowFlags() & BOTTOM
    assert base_type(window) == Qt.WindowType.Tool


def test_leaving_config_mode_restores_the_configured_layer(window):
    window.set_config_mode(True)
    window.set_config_mode(False)
    assert window.windowFlags() & BOTTOM


def test_a_layer_change_in_config_mode_waits_for_it_to_end(window):
    """Previewing it live would either bury the widget under the settings app
    or float it over the fields being typed into."""
    window.set_config_mode(True)
    window.apply_config(replace_stacking(window, STACKING_TOP))
    assert not window.windowFlags() & TOP
    window.set_config_mode(False)
    assert window.windowFlags() & TOP


def test_config_mode_keeps_the_terminal_focused_across_the_restack(window):
    # focusWidget() rather than hasFocus(): the offscreen platform never
    # activates a window.
    window.set_config_mode(True)
    assert window.focusWidget() is window.view


def test_the_overlay_survives_the_restack_into_config_mode(window):
    window.set_config_mode(True)
    assert window._overlay.isVisible()
    assert window._overlay.geometry() == window.rect()


def test_translucency_survives_a_layer_change(window):
    window.apply_config(
        replace_stacking(window, STACKING_TOP, opacity_mode=OPACITY_BACKGROUND)
    )
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_window_opacity_survives_a_layer_change(window):
    window.apply_config(
        replace_stacking(window, STACKING_TOP, opacity_mode=OPACITY_WINDOW, opacity=40)
    )
    assert round(window.windowOpacity(), 2) == 0.4


def test_the_session_survives_a_layer_change(live):
    """The PTY lives on a QThread, not on the native window, so recreating
    the window must not disturb it."""
    session = live.session
    live.apply_config(replace_stacking(live, STACKING_TOP))
    assert live.session is session
    assert live.view.session is session
