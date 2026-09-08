"""The tray icon: the widget's only handle once it leaves the taskbar.

The widget is a ``Qt::Tool`` window, so the shell gives it no taskbar button
and no Alt+Tab slot. That is the point -- it is meant to be part of the
desktop rather than something you switch to -- but it removes both of the ways
you would normally get a covered window back, and the only way to quit one that
has no close button. This is the replacement.

Deliberately not a setting. Turning it off would strand the user, which is the
same reasoning that gives whole-window opacity its floor in ``config.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .platform_info import DISPLAY_NAME, icon_path, settings_launcher

#: A desktop panel that starts *after* our autostart entry has no tray yet, so
#: one look at startup is not enough. Bounded, because a desktop with no tray
#: at all is a legitimate outcome and must not be retried forever.
TRAY_RETRY_MS = 5000
TRAY_RETRIES = 3

#: Sizes to offer Qt. XEmbed trays scale the 256px icon badly, so hand them
#: every face that ships in assets/ and let Qt pick.
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, None)


def tray_icon() -> QIcon:
    """A multi-resolution icon for the tray."""
    icon = QIcon()
    seen: set[Path] = set()
    for size in _ICON_SIZES:
        path = icon_path(size)
        # On Windows icon_path() returns the same multi-size .ico whatever it
        # is asked for, so the same file would otherwise be added eight times.
        if path in seen or not path.exists():
            continue
        seen.add(path)
        icon.addFile(str(path))
    return icon


class WidgetTray(QObject):
    """A tray icon for the widget, where the desktop has a tray to put one in."""

    def __init__(
        self,
        window,
        config_path: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._config_path = config_path
        self._icon: QSystemTrayIcon | None = None
        # A QMenu that is only ever passed to setContextMenu() has no Python
        # owner and gets collected, after which the menu silently stops
        # appearing. Hold on to it.
        self._menu: QMenu | None = None
        self._attempts = 0
        self._try_install()

    @property
    def available(self) -> bool:
        """Whether a tray icon is actually installed."""
        return self._icon is not None

    # -- Installation --------------------------------------------------

    def _try_install(self) -> None:
        """Install the icon, or schedule another look if there is no tray yet.

        Nothing is constructed when there is no tray: Qt warns about an icon it
        cannot show, and there is no sense holding a menu nobody can open.
        """
        if self._icon is not None:
            return
        self._attempts += 1
        if not QSystemTrayIcon.isSystemTrayAvailable():
            if self._attempts < TRAY_RETRIES:
                QTimer.singleShot(TRAY_RETRY_MS, self._try_install)
            return

        icon = tray_icon()
        if icon.isNull():
            # An icon-less tray entry is invisible on some platforms, which is
            # indistinguishable from having no tray at all.
            icon = self._window.windowIcon()

        self._menu = QMenu(self._window)
        show = self._menu.addAction("Show")
        show.triggered.connect(self._window.bring_to_front)
        settings = self._menu.addAction("Settings")
        settings.triggered.connect(self._open_settings)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        # close(), not app.quit(): quit() skips closeEvent, which is where the
        # PTY child is terminated and the reader thread joined, and would leave
        # an orphaned shell behind (plus a conhost.exe on Windows).
        quit_action.triggered.connect(self._window.close)

        self._icon = QSystemTrayIcon(icon, self)
        self._icon.setToolTip(DISPLAY_NAME)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._icon.show()

    # -- Menu actions --------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Some platforms send Trigger and DoubleClick for the same gesture;
        # bring_to_front is idempotent, so that is harmless. Middle click is
        # deliberately not wired to Quit -- the widget has no other close
        # affordance, and a stray click would take the session with it.
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._window.bring_to_front()

    def _open_settings(self) -> None:
        """Start the settings app, unless one is already talking to us."""
        # Config mode means a settings app is connected. A second one would be
        # dropped by WidgetServer and then sit there retrying forever.
        if self._window.config_mode:
            self._window.bring_to_front()
            return

        launcher = settings_launcher()
        if launcher is not None:
            program, args = str(launcher), []
        else:
            program, args = sys.executable, ["-m", "terminal_widget.settings"]
        if self._config_path is not None:
            # A widget started with --config must hand the settings app the
            # same file, or it would edit one nobody is reading.
            args = args + ["--config", str(self._config_path)]
        QProcess.startDetached(program, args)
