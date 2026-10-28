"""Test fixtures. Everything runs on Qt's offscreen platform, so the suite
needs no display and is safe in CI."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


class FakeShell:
    """A stand-in for the Win32 side of the desktop layer.

    ``kinds`` says what ``classify`` reports for each hwnd (anything unlisted
    is OTHER, and the widget's own hwnd is SELF); ``calls`` records every
    placement in order, as ``("above", widget, desktop)`` or
    ``("bottom", widget)``.
    """

    def __init__(self, fg: int = 0, install_ok: bool = True) -> None:
        from terminal_widget.desktop_layer import Kind

        self.fg = fg
        self.install_ok = install_ok
        self.kinds: dict[int, Kind] = {0: Kind.NONE}
        self.calls: list[tuple] = []
        self.callback = None
        self.installs = 0
        self.uninstalls = 0

    def install(self, callback) -> bool:
        self.installs += 1
        if self.install_ok:
            self.callback = callback
        return self.install_ok

    def uninstall(self) -> None:
        self.uninstalls += 1
        self.callback = None

    @property
    def installed(self) -> bool:
        return self.callback is not None

    def foreground(self) -> int:
        return self.fg

    def classify(self, hwnd: int, widget: int):
        from terminal_widget.desktop_layer import Kind

        if hwnd and hwnd == widget:
            return Kind.SELF
        return self.kinds.get(hwnd, Kind.OTHER)

    def place_above(self, widget: int, desktop: int) -> None:
        self.calls.append(("above", widget, desktop))

    def place_at_bottom(self, widget: int) -> None:
        self.calls.append(("bottom", widget))

    def bring(self, hwnd: int) -> None:
        """Simulate ``hwnd`` coming to the foreground."""
        self.fg = hwnd
        if self.callback is not None:
            self.callback(hwnd)


@pytest.fixture
def fake_shell():
    return FakeShell()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    """A widget window with no shell attached (no process spawned)."""
    from terminal_widget.config import Config
    from terminal_widget.window import WidgetWindow

    w = WidgetWindow(Config(x=100, y=100, width=640, height=320))
    w.show()
    yield w
    w.close()


@pytest.fixture
def live(window):
    """A widget window whose view has a screen, with no process behind it.

    TerminalSession builds its screen and stream in __init__, so feeding the
    stream directly exercises rendering, scrolling and selection without
    spawning anything. ``_alive`` is set by hand because writes are dropped
    otherwise, and several behaviours here are about what does and does not
    reach the shell.
    """
    from terminal_widget.session import TerminalSession

    cols, rows = window.view.grid_size()
    session = TerminalSession(cols, rows, 200, window)
    session._alive = True
    window.view.session = session
    window.session = session
    return window
