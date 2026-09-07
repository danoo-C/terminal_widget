"""Tests for the two modes -- the core of the widget's design."""

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from terminal_widget.config import OPACITY_BACKGROUND, OPACITY_WINDOW, Config


def press_at(window, x, y):
    e = QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(x, y),
        window.mapToGlobal(QPoint(x, y)),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
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
    assert window.view.cursor().shape() == Qt.IBeamCursor


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
    assert window.view.cursor().shape() == Qt.ArrowCursor


def test_overlay_ignores_the_mouse(window):
    """The affordance must never intercept a drag."""
    assert window._overlay.testAttribute(Qt.WA_TransparentForMouseEvents)


def test_mode_toggle_is_idempotent(window):
    for _ in range(3):
        window.set_config_mode(True)
    assert window.config_mode
    for _ in range(3):
        window.set_config_mode(False)
    assert not window.config_mode


# -- Resize edges -----------------------------------------------------


def test_edges_detected_at_borders(window):
    assert window._edges_at(QPoint(1, 150)) & Qt.LeftEdge
    assert window._edges_at(QPoint(window.width() - 1, 150)) & Qt.RightEdge
    assert window._edges_at(QPoint(300, 1)) & Qt.TopEdge
    assert window._edges_at(QPoint(300, window.height() - 1)) & Qt.BottomEdge


def test_corner_reports_two_edges(window):
    edges = window._edges_at(QPoint(1, 1))
    assert edges & Qt.LeftEdge and edges & Qt.TopEdge


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
    assert window.testAttribute(Qt.WA_TranslucentBackground)


def test_window_mode_fades_the_whole_window(window):
    window.apply_config(Config(opacity=40, opacity_mode=OPACITY_WINDOW))
    assert abs(window.windowOpacity() - 0.4) < 0.01
    assert not window.testAttribute(Qt.WA_TranslucentBackground)


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
