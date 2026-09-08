"""Starting a widget.

Three places need to do this -- the settings app's Launch button, a widget
restarting itself, and the installer -- and they have to agree on both which
program to run and which config file to hand it. They agree here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess

from .platform_info import widget_launcher


def spawn_widget(config: Path | None = None) -> bool:
    """Start a widget, detached, on ``config`` (None means the default file).

    Prefers the installed launcher, because that is the one the user's shortcut
    and autostart entry point at; a source checkout has none, and falls back to
    the interpreter running us.
    """
    launcher = widget_launcher()
    if launcher is not None:
        program, args = str(launcher), []
    else:
        program, args = sys.executable, ["-m", "terminal_widget"]
    if config is not None:
        # A widget started with --config must be handed the same file, or it
        # would take the lock on one config and read another.
        args = args + ["--config", str(config)]
    # The static overload answers with (started, pid); only the first half is
    # ours to act on -- a detached child's pid is not something we follow.
    started, _pid = QProcess.startDetached(program, args)
    return started
