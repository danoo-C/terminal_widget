"""Test fixtures. Everything runs on Qt's offscreen platform, so the suite
needs no display and is safe in CI."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


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
