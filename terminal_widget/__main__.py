"""Widget entry point: ``python -m terminal_widget``."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .config import Config, config_path
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
    parser.add_argument(
        "--config-mode",
        action="store_true",
        help="start in config mode (drag/resize enabled). Temporary: normally "
        "the settings app decides this by connecting.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config.load(args.config)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("terminal_widget")

    window = WidgetWindow(config)
    window.set_config_mode(args.config_mode)
    window.show()
    window.start_session()

    # Ctrl+C in the launching terminal should close the widget. Qt's event
    # loop blocks Python's signal handling, so poke the interpreter awake.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.start(200)
    wake.timeout.connect(lambda: None)

    try:
        return app.exec()
    finally:
        # The user may have dragged or resized in config mode; that is the
        # authoritative geometry, so keep it.
        try:
            config.clamped().save(args.config)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
