"""Launch the widget when the user logs in.

One interface, one backend per platform, mirroring how ``pty/`` is arranged.
Stdlib only, so the installer can use this before anything is installed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..platform_info import is_windows
from .base import AutostartBackend

__all__ = [
    "AutostartBackend",
    "backend",
    "describe",
    "disable",
    "enable",
    "is_enabled",
    "is_supported",
]


@lru_cache(maxsize=1)
def backend() -> AutostartBackend:
    if is_windows():
        from .windows import WindowsAutostart

        return WindowsAutostart()
    from .xdg import XdgAutostart

    return XdgAutostart()


def is_supported() -> tuple[bool, str]:
    return backend().is_supported()


def is_enabled() -> bool:
    return backend().is_enabled()


def enable(executable: Path, args: list[str] | None = None) -> None:
    backend().enable(Path(executable), args)


def disable() -> None:
    backend().disable()


def describe() -> str:
    return backend().describe()
