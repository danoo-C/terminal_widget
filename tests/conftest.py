"""Test fixtures. Everything runs on Qt's offscreen platform, so the suite
needs no display and is safe in CI."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


class FakeShell:
    """A stand-in for the Win32 side of the desktop layer.

    ``kinds`` says what ``classify`` reports for each hwnd (anything unlisted
    is APP, and the widget's own hwnd is SELF). ``above`` is what
    ``desktop_above`` answers, and ``asked`` records each question.
    ``calls`` records every placement in order, as ``("raise", widget)`` or
    ``("bottom", widget)``.
    """

    def __init__(self, fg: int = 0, install_ok: bool = True) -> None:
        from terminal_widget.desktop_layer import Kind

        self.fg = fg
        self.install_ok = install_ok
        self.kinds: dict[int, Kind] = {0: Kind.NONE}
        self.above = False
        self.asked: list[tuple[int, int]] = []
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
        return self.kinds.get(hwnd, Kind.APP)

    def desktop_above(self, widget: int, desktop: int) -> bool:
        self.asked.append((widget, desktop))
        return self.above

    def raise_topmost(self, widget: int) -> None:
        self.calls.append(("raise", widget))

    def place_at_bottom(self, widget: int) -> None:
        self.calls.append(("bottom", widget))

    def bring(self, hwnd: int) -> None:
        """Simulate ``hwnd`` coming to the foreground."""
        self.fg = hwnd
        if self.callback is not None:
            self.callback(hwnd)


class FakeTicker:
    """A stand-in for the watch QTimer: started and stopped by the layer,
    fired by hand with ``fire()``."""

    def __init__(self, callback) -> None:
        self.callback = callback
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.running = True
        self.starts += 1

    def stop(self) -> None:
        self.running = False
        self.stops += 1

    def fire(self, times: int = 1) -> None:
        """Deliver ``times`` ticks, as the timer would while it runs."""
        for _ in range(times):
            if not self.running:
                return
            self.callback()


class TickerFactory:
    """``make_ticker`` for DesktopLayer, remembering the ticker it made."""

    def __init__(self) -> None:
        self.ticker: FakeTicker | None = None

    def __call__(self, callback) -> FakeTicker:
        self.ticker = FakeTicker(callback)
        return self.ticker


@pytest.fixture
def fake_shell():
    return FakeShell()


@pytest.fixture
def ticker_factory():
    return TickerFactory()


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
