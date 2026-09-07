"""Linux autostart via the XDG Desktop Entry specification.

Honoured by GNOME, KDE, XFCE, LXQt, Cinnamon and MATE. A systemd user unit
would be more capable, but it adds a dependency on systemd and needs a
`systemctl --user enable` step; a desktop entry is a single file.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..platform_info import DISPLAY_NAME, DIST_NAME, is_wsl
from .base import AutostartBackend


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / f"{DIST_NAME}.desktop"


def _quote(value: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec."""
    if not value or any(c in value for c in ' \t\n"\'\\><~|&;$*?#()`'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


class XdgAutostart(AutostartBackend):
    def is_supported(self) -> tuple[bool, str]:
        if is_wsl():
            return False, (
                "WSL has no persistent desktop session, so an autostart entry "
                "would never run. Launch the widget from Windows instead."
            )
        return True, ""

    def is_enabled(self) -> bool:
        path = entry_path()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        # Some desktop environments disable an entry by rewriting it rather
        # than deleting it, so the file existing is not enough.
        for line in text.splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().lower()
            if key == "Hidden" and value == "true":
                return False
            if key == "X-GNOME-Autostart-enabled" and value == "false":
                return False
        return True

    def enable(self, executable: Path, args: list[str] | None = None) -> None:
        command = " ".join([_quote(str(executable))] + [_quote(a) for a in args or []])
        entry = "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={DISPLAY_NAME}",
                "Comment=Borderless desktop terminal widget",
                # Absolute path: autostart runs with a minimal environment and
                # PATH will not contain the install venv.
                f"Exec={command}",
                f"Icon={DIST_NAME}",
                "Terminal=false",
                "X-GNOME-Autostart-enabled=true",
                "",
            ]
        )
        path = entry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".desktop.tmp")
        tmp.write_text(entry, encoding="utf-8")
        os.replace(tmp, path)

    def disable(self) -> None:
        entry_path().unlink(missing_ok=True)

    def describe(self) -> str:
        return str(entry_path())
