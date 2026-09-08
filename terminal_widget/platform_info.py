"""Environment facts the installer and the autostart backends both need.

Deliberately stdlib-only: the installer imports this before any dependency
has been installed, so it must not reach for PySide6 or anything else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Package/import name, used for config directories.
APP_NAME = "terminal_widget"
#: Distribution name, used for install directories and launcher entries.
DIST_NAME = "terminal-widget"
#: Human-readable name, used in UI and shortcut labels.
DISPLAY_NAME = "Terminal Widget"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_wsl() -> bool:
    """WSL runs Linux binaries but has no persistent desktop session.

    Autostart entries are written happily and then never fire, so it is
    kinder to say so than to pretend it worked.
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def distro_family() -> str:
    """'debian' | 'fedora' | 'arch' | 'suse' | 'unknown'.

    Used only to print the right system-package command when Qt fails to
    load; nothing depends on it being right.
    """
    try:
        fields = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
    except OSError:
        return "unknown"
    ids = f"{fields.get('ID', '')} {fields.get('ID_LIKE', '')}".replace('"', "").lower()
    for family in ("debian", "ubuntu", "fedora", "rhel", "centos", "arch", "suse"):
        if family in ids:
            if family in ("ubuntu",):
                return "debian"
            if family in ("rhel", "centos"):
                return "fedora"
            return family
    return "unknown"


def app_root() -> Path:
    """Where the installer puts the venv and its bookkeeping."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "TerminalWidget"
    return Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    ) / DIST_NAME


def venv_python(root: Path) -> Path:
    """The interpreter inside an installed venv."""
    if is_windows():
        return root / "venv" / "Scripts" / "python.exe"
    return root / "venv" / "bin" / "python"


def venv_script(root: Path, name: str) -> Path:
    """An entry-point launcher inside an installed venv."""
    if is_windows():
        return root / "venv" / "Scripts" / f"{name}.exe"
    return root / "venv" / "bin" / name


def _launcher(name: str) -> Path | None:
    """An installed entry-point launcher sitting beside this interpreter."""
    bindir = Path(sys.executable).parent
    candidate = bindir / (f"{name}.exe" if is_windows() else name)
    return candidate if candidate.exists() else None


def widget_launcher() -> Path | None:
    """The installed launcher for the widget, or None when running from source.

    Autostart needs a stable executable to point at. Running from a checkout
    with ``python -m terminal_widget`` gives no such thing -- the interpreter
    might be a throwaway venv -- so the settings app disables the toggle
    rather than registering a path that will rot.
    """
    return _launcher(DIST_NAME)


def settings_launcher() -> Path | None:
    """The installed launcher for the settings app, or None from a checkout.

    The tray's Settings entry prefers this over ``python -m``: on Windows it
    is the ``gui-scripts`` launcher, which is pythonw-backed and so does not
    flash a console window on its way up.
    """
    return _launcher(f"{DIST_NAME}-settings")


def icon_path(size: int | None = None) -> Path:
    """Bundled application icon."""
    assets = Path(__file__).resolve().parent / "assets"
    if is_windows():
        ico = assets / f"{DIST_NAME}.ico"
        if ico.exists():
            return ico
    if size is not None:
        sized = assets / f"{DIST_NAME}-{size}.png"
        if sized.exists():
            return sized
    return assets / f"{DIST_NAME}.png"
