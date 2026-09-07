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
