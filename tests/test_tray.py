"""Tests for the tray icon -- the way back to a widget with no taskbar button."""

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from terminal_widget import tray as tray_module
from terminal_widget.tray import TRAY_RETRIES, WidgetTray


@pytest.fixture
def with_a_tray(monkeypatch):
    """Pretend the desktop has a tray. The offscreen platform has none."""
    monkeypatch.setattr(
        QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
    )


@pytest.fixture
def spawns(monkeypatch):
    """Capture what the tray would have launched."""
    calls = []

    class FakeProcess:
        @staticmethod
        def startDetached(program, args):  # noqa: N802
            calls.append((program, args))
            return True

    monkeypatch.setattr(tray_module, "QProcess", FakeProcess)
    return calls


def menu_of(tray: WidgetTray) -> QMenu:
    assert tray._menu is not None
    return tray._menu


def action_texts(tray: WidgetTray) -> list[str]:
    return [a.text() for a in menu_of(tray).actions() if not a.isSeparator()]


def action_named(tray: WidgetTray, name: str) -> QAction:
    return [a for a in menu_of(tray).actions() if a.text() == name][0]


def test_no_tray_is_not_a_crash(window):
    """Some desktops have no tray at all, and neither does the offscreen
    platform. The widget still has to run."""
    tray = WidgetTray(window)
    assert not tray.available
    assert tray._menu is None


def test_a_missing_tray_is_retried_but_not_forever(window):
    tray = WidgetTray(window)
    for _ in range(TRAY_RETRIES + 3):
        tray._try_install()
    assert not tray.available
    assert tray._attempts <= TRAY_RETRIES + 4  # never raises, never installs


def test_the_menu_is_built_when_a_tray_exists(window, with_a_tray):
    tray = WidgetTray(window)
    assert tray.available
    assert action_texts(tray) == ["Show", "Settings", "Quit"]


def test_show_brings_a_hidden_widget_back(window, with_a_tray):
    tray = WidgetTray(window)
    window.hide()
    action_named(tray, "Show").trigger()
    assert window.isVisible()


def test_quit_closes_the_window_rather_than_the_app(window, with_a_tray):
    """app.quit() would skip closeEvent, which is where the PTY child is
    terminated and the reader thread joined -- an orphaned shell, and a
    conhost.exe on Windows."""
    tray = WidgetTray(window)
    seen = []
    window.closed.connect(lambda: seen.append(True))
    action_named(tray, "Quit").trigger()
    assert seen == [True]


def test_settings_is_launched_with_the_widgets_own_config(window, with_a_tray, spawns, tmp_path):
    path = tmp_path / "config.json"
    tray = WidgetTray(window, path)
    tray._open_settings()
    assert spawns
    _program, args = spawns[-1]
    assert "--config" in args and str(path) in args


def test_settings_is_not_launched_twice(window, with_a_tray, spawns):
    """A second settings app is dropped by WidgetServer and then retries
    forever, which looks to the user like nothing happened."""
    tray = WidgetTray(window)
    window.set_config_mode(True)
    tray._open_settings()
    assert spawns == []
