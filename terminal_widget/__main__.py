"""Widget entry point: ``python -m terminal_widget``."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication

from .config import Config, config_path
from .launch import spawn_widget
from .ipc import WidgetServer, instance_lock, request_show, socket_name
from .tray import WidgetTray
from .window import WidgetWindow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="terminal_widget",
        description="A borderless desktop terminal widget.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"config file to use (default: {config_path()})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Before the config is even read, and well before a Qt platform plugin or
    # a shell: one widget per config file. Two widgets sharing a config each
    # hold a snapshot of it and each write the whole thing back on the way
    # out, so the second to quit silently reverts everything the user changed.
    lock = instance_lock(args.config)
    if lock.error() == QLockFile.LockError.LockFailedError:
        # Launching is meant to hand you your widget. You have one, so bring
        # it forward rather than starting a rival to it.
        request_show(socket_name(args.config))
        return 0
    if lock.error() != QLockFile.LockError.NoError:
        # Nowhere to put the lock is nearly always nowhere to put the config
        # either, and refusing to start over that would be worse than the
        # thing it guards against.
        print(
            "warning: could not take the single-widget lock; a second widget "
            "on this config would not be stopped.",
            file=sys.stderr,
        )

    config = Config.load(args.config)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("terminal_widget")
    # Said outright rather than inherited. Qt clears WA_QuitOnClose for a
    # Qt::Tool window, so the widget closing already does not end the event
    # loop on its own; window.closed below is what does, and it fires only
    # once the shell is torn down.
    app.setQuitOnLastWindowClosed(False)

    server = WidgetServer(app)
    if not server.start(socket_name(args.config)):
        print(
            "warning: could not open the settings socket; the settings app "
            f"will not be able to reach this widget ({server.error}).",
            file=sys.stderr,
        )

    window = WidgetWindow(config)
    window.closed.connect(app.quit)
    window.show()
    window.start_session()

    # The only way back to a widget with no taskbar button and no Alt+Tab
    # slot, so it outlives main(): a collected tray icon is a lost widget.
    tray = WidgetTray(window, args.config, app)
    if not tray.available:
        print(
            "warning: no system tray on this desktop; open the settings app "
            "to reach the widget, and quit it by exiting its shell.",
            file=sys.stderr,
        )

    # The settings app introducing itself is what enables config mode; the
    # socket dropping is what ends it. A crashed settings app therefore looks
    # the same as one that quit, so the widget can never get stuck.
    def on_mode(enabled: bool) -> None:
        window.set_config_mode(enabled)
        if enabled:
            # Tell the settings app where we actually are, so its fields
            # start out truthful rather than showing the last saved values.
            geom = window.geometry()
            server.send_geometry(geom.x(), geom.y(), geom.width(), geom.height())
        else:
            # Leaving config mode is the moment the arrangement is final.
            _save(window.config, args.config)

    server.configModeChanged.connect(on_mode)
    server.configReceived.connect(window.apply_config)
    server.showRequested.connect(window.bring_to_front)

    relaunch = False

    def on_quit(restart: bool) -> None:
        nonlocal relaunch
        relaunch = restart
        # close(), not app.quit(): quit() skips closeEvent, which is where the
        # PTY child is terminated and the reader thread joined. Same reasoning
        # as the tray's Quit entry.
        window.close()

    server.quitRequested.connect(on_quit)
    window.geometryEdited.connect(server.send_geometry)

    # Ctrl+C in the launching terminal should close the widget. Qt's event
    # loop blocks Python's signal handling, so poke the interpreter awake.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.start(200)
    wake.timeout.connect(lambda: None)

    try:
        return app.exec()
    finally:
        # The order is the whole design of Restart. We are the only party that
        # knows when we have finished letting go, so we are the one that
        # starts the replacement: socket first, then the config it will read,
        # then the lock that would otherwise turn it away at the door.
        server.stop()
        _save(window.config, args.config)
        lock.unlock()
        if relaunch:
            spawn_widget(args.config)


def _save(config: Config, path: Path | None) -> None:
    try:
        config.clamped().save(path)
    except OSError:
        pass  # a read-only config dir must not take the widget down


if __name__ == "__main__":
    raise SystemExit(main())
