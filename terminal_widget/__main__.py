"""Widget entry point: ``python -m terminal_widget``."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .config import Config, config_path
from .ipc import WidgetServer
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
    config = Config.load(args.config)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("terminal_widget")

    window = WidgetWindow(config)
    window.show()
    window.start_session()

    # The settings app connecting is what enables config mode; the socket
    # dropping is what ends it. A crashed settings app therefore looks the
    # same as one that quit, so the widget can never get stuck.
    server = WidgetServer(app)

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
    window.geometryEdited.connect(server.send_geometry)

    if not server.start():
        print(
            "warning: could not open the settings socket; the settings app "
            "will not be able to reach this widget.",
            file=sys.stderr,
        )

    # Ctrl+C in the launching terminal should close the widget. Qt's event
    # loop blocks Python's signal handling, so poke the interpreter awake.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.start(200)
    wake.timeout.connect(lambda: None)

    try:
        return app.exec()
    finally:
        server.stop()
        _save(window.config, args.config)


def _save(config: Config, path: Path | None) -> None:
    try:
        config.clamped().save(path)
    except OSError:
        pass  # a read-only config dir must not take the widget down


if __name__ == "__main__":
    raise SystemExit(main())
