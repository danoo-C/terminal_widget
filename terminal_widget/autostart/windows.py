"""Windows autostart via the per-user Run key.

HKEY_CURRENT_USER needs no administrator rights, and the Run key is the
plainest mechanism Windows offers. Task Scheduler is more capable but is a
great deal more surface area, and it is what actual malware reaches for,
which makes antivirus software suspicious of anything that writes there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..platform_info import DISPLAY_NAME
from .base import AutostartBackend

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TerminalWidget"


def _command(executable: Path, args: list[str] | None) -> str:
    # Paths contain spaces on essentially every Windows machine.
    return subprocess.list2cmdline([str(executable)] + list(args or []))


class WindowsAutostart(AutostartBackend):
    def is_supported(self) -> tuple[bool, str]:
        return True, ""

    def is_enabled(self) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return bool(value)

    def enable(self, executable: Path, args: list[str] | None = None) -> None:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, _command(executable, args)
            )

    def disable(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass  # already gone is success
        except OSError:
            pass

    def describe(self) -> str:
        return f"HKCU\\{RUN_KEY}\\{VALUE_NAME}  ({DISPLAY_NAME})"
