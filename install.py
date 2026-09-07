#!/usr/bin/env python3
"""Installer for Terminal Widget.

Run it and it does everything: creates a private virtual environment,
installs the application into it, registers a Start Menu / application
launcher entry, optionally enables autostart, and writes an uninstaller.

    python3 install.py              graphical installer
    python3 install.py --cli        text installer
    python3 install.py --uninstall  remove an existing install

Nothing here needs administrator rights, and nothing is written outside the
user's own home directory.

Stdlib only, on purpose: this runs before any dependency exists.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)
MANIFEST_SCHEMA = 1

# The installer imports these from the project source it sits next to. They
# are stdlib-only for exactly this reason.
sys.path.insert(0, str(HERE))
try:
    from terminal_widget.platform_info import (  # noqa: E402
        DISPLAY_NAME,
        DIST_NAME,
        app_root,
        distro_family,
        is_windows,
        is_wsl,
        venv_python,
        venv_script,
    )
except ImportError:  # running from the app root, where only the manifest matters
    DISPLAY_NAME, DIST_NAME = "Terminal Widget", "terminal-widget"

    def is_windows() -> bool:
        return sys.platform == "win32"

    def is_wsl() -> bool:
        return "microsoft" in Path("/proc/version").read_text().lower() if Path(
            "/proc/version").exists() else False

    def distro_family() -> str:
        return "unknown"

    def app_root() -> Path:
        if is_windows():
            base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
            return Path(base) / "TerminalWidget"
        return Path(os.environ.get("XDG_DATA_HOME")
                    or Path.home() / ".local" / "share") / DIST_NAME

    def venv_python(root: Path) -> Path:
        return root / "venv" / ("Scripts/python.exe" if is_windows() else "bin/python")

    def venv_script(root: Path, name: str) -> Path:
        return root / "venv" / ("Scripts" if is_windows() else "bin") / (
            f"{name}.exe" if is_windows() else name)


# System packages Qt needs on Linux. Getting this wrong produces a widget
# that installs cleanly and then crashes, which is the worst outcome.
QT_SYSTEM_PACKAGES = {
    "debian": (
        "sudo apt install libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 "
        "libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 "
        "libxkbcommon-x11-0"
    ),
    "fedora": (
        "sudo dnf install libglvnd-egl xcb-util-cursor xcb-util-wm "
        "xcb-util-image xcb-util-keysyms xcb-util-renderutil libxkbcommon-x11"
    ),
    "arch": (
        "sudo pacman -S libglvnd xcb-util-cursor xcb-util-wm xcb-util-image "
        "xcb-util-keysyms xcb-util-renderutil libxkbcommon-x11"
    ),
    "suse": (
        "sudo zypper install libEGL1 libxcb-cursor0 libxcb-icccm4 "
        "libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 "
        "libxcb-xkb1 libxkbcommon-x11-0"
    ),
}


class InstallError(Exception):
    """Something went wrong that the user needs to read about."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


Progress = Callable[[str, float], None]
"""Called with (message, fraction 0..1). Fraction < 0 means 'log line only'."""


def _log_only(progress: Progress, line: str) -> None:
    progress(line, -1.0)


@dataclass
class Options:
    prefix: Path = field(default_factory=app_root)
    shortcuts: bool = True
    autostart: bool = False
    dry_run: bool = False


@dataclass
class Manifest:
    version: str = "0.2.0"
    paths: list[str] = field(default_factory=list)
    registry: list[dict] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    app_root: str = ""
    platform: str = sys.platform
    installed_at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "app": DIST_NAME,
                "version": self.version,
                "installed_at": self.installed_at,
                "platform": self.platform,
                "python": sys.executable,
                "app_root": self.app_root,
                "paths": self.paths,
                "registry": self.registry,
                "preserved": self.preserved,
            },
            indent=2,
        ) + "\n"

    @staticmethod
    def load(path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != MANIFEST_SCHEMA:
            raise InstallError(
                f"Unrecognised manifest schema in {path}.",
                "This install was made by a different version of the installer.",
            )
        return data


def manifest_path(root: Path) -> Path:
    return root / "install-manifest.json"


def existing_install(root: Path) -> dict | None:
    path = manifest_path(root)
    if not path.exists():
        return None
    try:
        return Manifest.load(path)
    except (OSError, ValueError, InstallError):
        return None


def run_streaming(cmd: list[str], progress: Progress, label: str) -> None:
    """Run a command, forwarding its output into the log as it arrives."""
    _log_only(progress, f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise InstallError(f"{label} could not start: {exc}") from exc

    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            del tail[:-40]
            _log_only(progress, f"  {line}")
    code = proc.wait()
    if code != 0:
        raise InstallError(
            f"{label} failed (exit {code}).", "\n".join(tail[-12:])
        )


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


class Installer:
    """Performs the install. Knows nothing about how it is being displayed."""

    def __init__(self, options: Options, source: Path = HERE) -> None:
        self.options = options
        self.source = source
        self.root = options.prefix
        self.manifest = Manifest(app_root=str(self.root))

    # -- helpers ---------------------------------------------------------

    def _record(self, path: Path) -> None:
        text = str(path)
        if text not in self.manifest.paths:
            self.manifest.paths.append(text)

    def _mkdir(self, path: Path) -> None:
        if not self.options.dry_run:
            path.mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, content: str, executable: bool = False) -> None:
        if self.options.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(0o755)

    # -- steps -----------------------------------------------------------

    def preflight(self, progress: Progress) -> None:
        progress("Checking prerequisites", 0.02)
        if sys.version_info < MIN_PYTHON:
            raise InstallError(
                f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required; "
                f"this is {sys.version.split()[0]}."
            )
        if not (is_windows() or sys.platform.startswith("linux")):
            raise InstallError(f"{sys.platform} is not a supported platform.")
        if not (self.source / "pyproject.toml").exists():
            raise InstallError(
                "Cannot find the project to install.",
                f"Expected pyproject.toml next to this script, in {self.source}.",
            )
        _log_only(progress, f"Python {sys.version.split()[0]} at {sys.executable}")
        _log_only(progress, f"Installing into {self.root}")

        previous = existing_install(self.root)
        if previous:
            _log_only(
                progress,
                f"Found an existing install (version {previous.get('version')}); "
                "it will be replaced. Your configuration is kept.",
            )

    def create_venv(self, progress: Progress) -> None:
        progress("Creating the virtual environment", 0.08)
        venv_dir = self.root / "venv"
        if venv_dir.exists() and not self.options.dry_run:
            # Reinstalling over a half-built venv is a common way to get a
            # confusing failure; start clean instead.
            _log_only(progress, "Removing the previous environment")
            shutil.rmtree(venv_dir, ignore_errors=True)
        self._mkdir(self.root)
        self._record(self.root)
        if self.options.dry_run:
            _log_only(progress, f"would create a venv in {venv_dir}")
            return
        try:
            run_streaming(
                [sys.executable, "-m", "venv", str(venv_dir)],
                progress,
                "Creating the virtual environment",
            )
        except InstallError as exc:
            raise InstallError(
                "Could not create a virtual environment.",
                "On Debian and Ubuntu this usually means the python3-venv "
                "package is missing:\n\n    sudo apt install python3-venv\n\n"
                + (exc.hint or ""),
            ) from exc

    def install_package(self, progress: Progress) -> None:
        progress("Installing the application (this downloads Qt, give it a minute)", 0.18)
        if self.options.dry_run:
            _log_only(progress, f"would pip install {self.source}")
            return
        python = venv_python(self.root)
        run_streaming(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            progress,
            "Upgrading pip",
        )
        progress("Installing the application and its dependencies", 0.25)
        run_streaming(
            [str(python), "-m", "pip", "install", str(self.source)],
            progress,
            "Installing the application",
        )

    def verify_qt(self, progress: Progress) -> None:
        """Prove Qt actually loads before claiming the install worked.

        A widget that installs cleanly and then crashes on launch is far
        worse than an install that stops here and says what is missing.
        """
        progress("Checking that Qt can start", 0.70)
        if self.options.dry_run:
            return
        check = (
            "import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen';"
            "from PySide6.QtWidgets import QApplication; QApplication([]);"
            "print('qt-ok')"
        )
        result = subprocess.run(
            [str(venv_python(self.root)), "-c", check],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and "qt-ok" in result.stdout:
            _log_only(progress, "Qt starts correctly.")
            return

        output = (result.stdout + result.stderr).strip()
        _log_only(progress, output)
        hint = ""
        if not is_windows():
            command = QT_SYSTEM_PACKAGES.get(distro_family())
            if command:
                hint = (
                    "Qt needs some system libraries that are not installed.\n"
                    "Run this, then start the installer again:\n\n"
                    f"    {command}"
                )
            else:
                hint = (
                    "Qt needs system libraries that are not installed. Install "
                    "the Qt/xcb runtime for your distribution, then run the "
                    "installer again. On Debian-like systems:\n\n"
                    f"    {QT_SYSTEM_PACKAGES['debian']}"
                )
        raise InstallError("Qt could not start.", hint or output)

    def install_icons(self, progress: Progress) -> None:
        progress("Installing the icon", 0.76)
        assets = self.source / "terminal_widget" / "assets"
        if is_windows() or not assets.is_dir():
            return
        base = Path(
            os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        ) / "icons" / "hicolor"
        for png in sorted(assets.glob(f"{DIST_NAME}-*.png")):
            try:
                size = int(png.stem.rsplit("-", 1)[1])
            except ValueError:
                continue
            target = base / f"{size}x{size}" / "apps" / f"{DIST_NAME}.png"
            self._mkdir(target.parent)
            if not self.options.dry_run:
                shutil.copyfile(png, target)
            self._record(target)
            _log_only(progress, f"icon {size}x{size}")

    def create_shortcuts(self, progress: Progress) -> None:
        if not self.options.shortcuts:
            _log_only(progress, "Skipping shortcuts at your request.")
            return
        progress("Creating shortcuts", 0.82)
        if is_windows():
            self._windows_shortcuts(progress)
        else:
            self._linux_shortcuts(progress)

    def _linux_shortcuts(self, progress: Progress) -> None:
        apps = Path(
            os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        ) / "applications"
        entries = [
            (
                f"{DIST_NAME}-settings.desktop",
                f"{DISPLAY_NAME} Settings",
                "Configure the terminal widget",
                venv_script(self.root, f"{DIST_NAME}-settings"),
                "Utility;Settings;",
            ),
            (
                f"{DIST_NAME}.desktop",
                DISPLAY_NAME,
                "Borderless desktop terminal widget",
                venv_script(self.root, DIST_NAME),
                "Utility;TerminalEmulator;",
            ),
        ]
        for filename, name, comment, target, categories in entries:
            body = "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    f"Name={name}",
                    f"Comment={comment}",
                    f"Exec={target}",
                    f"Icon={DIST_NAME}",
                    "Terminal=false",
                    f"Categories={categories}",
                    "",
                ]
            )
            path = apps / filename
            self._write(path, body)
            self._record(path)
            _log_only(progress, f"launcher entry {filename}")

        updater = shutil.which("update-desktop-database")
        if updater and not self.options.dry_run:
            subprocess.run([updater, str(apps)], capture_output=True)

    def _windows_shortcuts(self, progress: Progress) -> None:
        start_menu = (
            Path(os.environ["APPDATA"])
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / DISPLAY_NAME
        )
        self._mkdir(start_menu)
        self._record(start_menu)
        icon = self.root / "venv" / "Lib" / "site-packages" / "terminal_widget" \
            / "assets" / f"{DIST_NAME}.ico"
        shortcuts = [
            (f"{DISPLAY_NAME} Settings.lnk",
             venv_script(self.root, f"{DIST_NAME}-settings")),
            (f"{DISPLAY_NAME}.lnk", venv_script(self.root, DIST_NAME)),
        ]
        for filename, target in shortcuts:
            link = start_menu / filename
            if not self.options.dry_run:
                create_windows_shortcut(link, target, icon)
            self._record(link)
            _log_only(progress, f"shortcut {filename}")

    def configure_autostart(self, progress: Progress) -> None:
        progress("Configuring startup", 0.90)
        try:
            from terminal_widget import autostart
        except ImportError:
            _log_only(progress, "Autostart module unavailable; skipping.")
            return

        supported, reason = autostart.is_supported()
        if self.options.autostart and not supported:
            _log_only(progress, f"Autostart not available here: {reason}")
            return
        if self.options.dry_run:
            _log_only(progress, f"would set autostart to {self.options.autostart}")
            return
        try:
            if self.options.autostart:
                autostart.enable(venv_script(self.root, DIST_NAME))
                _log_only(progress, f"autostart enabled: {autostart.describe()}")
                if is_windows():
                    from terminal_widget.autostart.windows import RUN_KEY, VALUE_NAME

                    self.manifest.registry.append(
                        {"root": "HKCU", "key": RUN_KEY, "value": VALUE_NAME}
                    )
                else:
                    self._record(Path(autostart.describe()))
            else:
                autostart.disable()
                _log_only(progress, "autostart disabled")
        except OSError as exc:
            _log_only(progress, f"Could not configure autostart: {exc}")

    def write_uninstaller(self, progress: Progress) -> None:
        progress("Writing the uninstaller", 0.94)
        # Copy this script in, so uninstalling never depends on the source
        # checkout still being around.
        target = self.root / "install.py"
        if not self.options.dry_run:
            shutil.copyfile(Path(__file__).resolve(), target)
        self._record(target)

        shim = "\n".join(
            [
                "#!/usr/bin/env python3",
                f'"""Uninstall {DISPLAY_NAME}."""',
                "import runpy, sys",
                "from pathlib import Path",
                "",
                "here = Path(__file__).resolve().parent",
                'sys.argv = [str(here / "install.py"), "--uninstall",',
                '            "--prefix", str(here)] + sys.argv[1:]',
                'runpy.run_path(str(here / "install.py"), run_name="__main__")',
                "",
            ]
        )
        path = self.root / "uninstall.py"
        self._write(path, shim, executable=True)
        self._record(path)

    def write_manifest(self, progress: Progress) -> None:
        # Written last, deliberately: the manifest must never describe things
        # that do not exist yet.
        progress("Recording what was installed", 0.97)
        self.manifest.installed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            from terminal_widget.config import config_dir

            self.manifest.preserved = [str(config_dir())]
        except ImportError:
            pass
        self._write(manifest_path(self.root), self.manifest.to_json())

    # -- driver ----------------------------------------------------------

    def run(self, progress: Progress) -> None:
        steps = [
            self.preflight,
            self.create_venv,
            self.install_package,
            self.verify_qt,
            self.install_icons,
            self.create_shortcuts,
            self.configure_autostart,
            self.write_uninstaller,
            self.write_manifest,
        ]
        for step in steps:
            step(progress)
        progress("Done", 1.0)


def create_windows_shortcut(link: Path, target: Path, icon: Path | None) -> None:
    """Create a .lnk by driving PowerShell's WScript.Shell COM object.

    Avoids a pywin32 dependency for one call. Single quotes are doubled
    because the paths are embedded in PowerShell single-quoted strings.
    """

    def q(value: str) -> str:
        return value.replace("'", "''")

    lines = [
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(str(link))}')",
        f"$s.TargetPath = '{q(str(target))}'",
        f"$s.WorkingDirectory = '{q(str(target.parent))}'",
        f"$s.Description = '{q(DISPLAY_NAME)}'",
    ]
    if icon and icon.exists():
        lines.append(f"$s.IconLocation = '{q(str(icon))}'")
    lines.append("$s.Save()")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(lines)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise InstallError(
            f"Could not create the shortcut {link.name}.",
            (result.stderr or result.stdout).strip(),
        )


# ---------------------------------------------------------------------------
# Uninstalling
# ---------------------------------------------------------------------------


class Uninstaller:
    """Removes exactly what the manifest says was installed, and nothing else."""

    def __init__(self, root: Path, purge: bool = False, dry_run: bool = False) -> None:
        self.root = root
        self.purge = purge
        self.dry_run = dry_run
        self.data = self._load()

    def _load(self) -> dict:
        path = manifest_path(self.root)
        if not path.exists():
            raise InstallError(
                f"No install found in {self.root}.",
                "Without a manifest the uninstaller will not guess which files "
                "belong to it. If you know the install location, pass "
                "--prefix PATH.",
            )
        try:
            return Manifest.load(path)
        except (OSError, ValueError) as exc:
            raise InstallError(f"Could not read {path}: {exc}") from exc

    def summary(self) -> tuple[list[str], list[str]]:
        """(will remove, will keep)."""
        remove = list(self.data.get("paths", []))
        for entry in self.data.get("registry", []):
            remove.append(
                f"{entry.get('root')}\\{entry.get('key')}\\{entry.get('value')}"
            )
        keep = list(self.data.get("preserved", []))
        if self.purge:
            remove.extend(keep)
            keep = []
        return remove, keep

    def run(self, progress: Progress) -> None:
        # Autostart first: a failure later must not leave a login entry
        # pointing at an interpreter that is about to be deleted.
        progress("Removing startup entry", 0.05)
        self._remove_autostart(progress)

        paths = [Path(p) for p in self.data.get("paths", [])]
        # Deepest first, so directories are empty by the time we reach them.
        paths.sort(key=lambda p: len(p.parts), reverse=True)
        total = max(1, len(paths))
        for index, path in enumerate(paths, start=1):
            progress(f"Removing {path.name}", 0.1 + 0.75 * index / total)
            self._remove(path, progress)

        if self.purge:
            progress("Removing configuration", 0.9)
            for kept in self.data.get("preserved", []):
                self._remove(Path(kept), progress)
        else:
            for kept in self.data.get("preserved", []):
                _log_only(progress, f"keeping {kept}")

        progress("Done", 1.0)

    def _remove_autostart(self, progress: Progress) -> None:
        for entry in self.data.get("registry", []):
            if entry.get("root") != "HKCU":
                continue
            if self.dry_run:
                _log_only(progress, f"would delete registry value {entry.get('value')}")
                continue
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, entry["key"], 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, entry["value"])
                _log_only(progress, f"removed registry value {entry['value']}")
            except (ImportError, OSError):
                pass  # already gone, or not Windows

    def _remove(self, path: Path, progress: Progress) -> None:
        if self.dry_run:
            _log_only(progress, f"would remove {path}")
            return
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            _log_only(progress, f"removed {path}")
        except OSError as exc:
            # Removing something already gone is success, not failure.
            _log_only(progress, f"could not remove {path}: {exc}")


# ---------------------------------------------------------------------------
# Text interface
# ---------------------------------------------------------------------------


def cli_install(options: Options, assume_yes: bool) -> int:
    print(f"\n  {DISPLAY_NAME} installer\n  {'-' * (len(DISPLAY_NAME) + 10)}\n")
    print(f"  Install location : {options.prefix}")
    print(f"  Shortcuts        : {'yes' if options.shortcuts else 'no'}")
    print(f"  Start on login   : {'yes' if options.autostart else 'no'}")
    if options.dry_run:
        print("\n  DRY RUN - nothing will be written.\n")
    elif not assume_yes:
        try:
            if input("\n  Proceed? [Y/n] ").strip().lower() in {"n", "no"}:
                print("  Cancelled.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return 1
    print()

    def progress(message: str, fraction: float) -> None:
        if fraction < 0:
            print(f"    {message}")
        else:
            print(f"  [{int(fraction * 100):3d}%] {message}")

    try:
        Installer(options).run(progress)
    except InstallError as exc:
        print(f"\n  FAILED: {exc}\n")
        if exc.hint:
            print("  " + exc.hint.replace("\n", "\n  ") + "\n")
        return 1

    print(f"\n  Installed to {options.prefix}\n")
    print("  Launch the settings app:")
    print(f"    {venv_script(options.prefix, f'{DIST_NAME}-settings')}")
    print("  Launch the widget:")
    print(f"    {venv_script(options.prefix, DIST_NAME)}")
    print(f"\n  To remove it later:\n    python {options.prefix / 'uninstall.py'}\n")
    return 0


def cli_uninstall(root: Path, purge: bool, dry_run: bool, assume_yes: bool) -> int:
    try:
        uninstaller = Uninstaller(root, purge=purge, dry_run=dry_run)
    except InstallError as exc:
        print(f"\n  {exc}\n")
        if exc.hint:
            print("  " + exc.hint.replace("\n", "\n  ") + "\n")
        return 1

    remove, keep = uninstaller.summary()
    print(f"\n  Removing {DISPLAY_NAME} from {root}\n")
    for item in remove:
        print(f"    - {item}")
    for item in keep:
        print(f"    keep {item}")
    if not assume_yes and not dry_run:
        try:
            if input("\n  Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("  Cancelled.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return 1
    print()

    def progress(message: str, fraction: float) -> None:
        if fraction < 0:
            print(f"    {message}")
        else:
            print(f"  [{int(fraction * 100):3d}%] {message}")

    uninstaller.run(progress)
    print("\n  Removed.\n")
    return 0


# ---------------------------------------------------------------------------
# Graphical interface
# ---------------------------------------------------------------------------

# tkinter, not Qt: this runs before Qt exists. tkinter ships with the official
# Windows Python and is one apt/dnf package away on Linux.

HEADER_BG = "#15151b"
HEADER_FG = "#f0f0f4"
ACCENT = "#0dbc79"
ERROR_FG = "#c62828"


def gui_available() -> tuple[bool, str]:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        package = {
            "debian": "sudo apt install python3-tk",
            "fedora": "sudo dnf install python3-tkinter",
            "arch": "sudo pacman -S tk",
            "suse": "sudo zypper install python3-tk",
        }.get(distro_family(), "install the python3-tk package")
        return False, (
            "The graphical installer needs tkinter, which is not installed.\n"
            f"Either run:  {package}\n"
            "or use the text installer:  python3 install.py --cli"
        )
    if not is_windows() and not os.environ.get("DISPLAY") and not os.environ.get(
        "WAYLAND_DISPLAY"
    ):
        return False, "No display available; use --cli for the text installer."
    return True, ""


class InstallerGui:
    """A small wizard: options, then progress, then a result."""

    def __init__(self, options: Options, uninstall: bool = False,
                 purge: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.options = options
        self.uninstall_mode = uninstall
        self.purge = purge
        self.result = 1
        self.worker: Any = None
        self.queue: Any = None

        self.root = tk.Tk()
        self.root.title(
            f"Remove {DISPLAY_NAME}" if uninstall else f"{DISPLAY_NAME} Installer"
        )
        self.root.geometry("640x580")
        self.root.minsize(560, 480)
        self._apply_theme()
        self._set_icon()

        self.body = ttk.Frame(self.root, padding=0)
        self.body.pack(fill="both", expand=True)
        self._build_header()
        self.page = ttk.Frame(self.body, padding=20)
        self.page.pack(fill="both", expand=True)
        self.footer = ttk.Frame(self.body, padding=(20, 0, 20, 16))
        self.footer.pack(fill="x")

        self._show_confirm() if uninstall else self._show_welcome()

    # -- chrome ----------------------------------------------------------

    def _apply_theme(self) -> None:
        style = self.ttk.Style()
        for preferred in ("vista", "aqua", "clam"):
            if preferred in style.theme_names():
                style.theme_use(preferred)
                break
        style.configure("Header.TFrame", background=HEADER_BG)
        style.configure(
            "HeaderTitle.TLabel",
            background=HEADER_BG,
            foreground=HEADER_FG,
            font=("TkDefaultFont", 15, "bold"),
        )
        style.configure(
            "HeaderSub.TLabel",
            background=HEADER_BG,
            foreground="#9aa0a6",
            font=("TkDefaultFont", 9),
        )
        style.configure("Hint.TLabel", foreground="#6b7280")
        style.configure("Error.TLabel", foreground=ERROR_FG,
                        font=("TkDefaultFont", 10, "bold"))
        style.configure("Ok.TLabel", foreground=ACCENT,
                        font=("TkDefaultFont", 11, "bold"))

    def _set_icon(self) -> None:
        icon = HERE / "terminal_widget" / "assets" / f"{DIST_NAME}-64.png"
        if not icon.exists():
            return
        try:
            self._icon = self.tk.PhotoImage(file=str(icon))
            self.root.iconphoto(True, self._icon)
        except Exception:
            self._icon = None

    def _build_header(self) -> None:
        header = self.ttk.Frame(self.body, style="Header.TFrame", padding=(20, 16))
        header.pack(fill="x")

        icon = HERE / "terminal_widget" / "assets" / f"{DIST_NAME}-48.png"
        if icon.exists():
            try:
                self._header_icon = self.tk.PhotoImage(file=str(icon))
                # A plain tk.Label, not ttk: ttk widgets reject -background,
                # which silently cost us the icon the first time round.
                self.tk.Label(
                    header,
                    image=self._header_icon,
                    background=HEADER_BG,
                    borderwidth=0,
                ).pack(side="left", padx=(0, 14))
            except Exception:
                pass

        text = self.ttk.Frame(header, style="Header.TFrame")
        text.pack(side="left", fill="x", expand=True)
        title = f"Remove {DISPLAY_NAME}" if self.uninstall_mode else DISPLAY_NAME
        subtitle = (
            "Uninstall the widget and its shortcuts"
            if self.uninstall_mode
            else "A borderless desktop terminal with a separate settings app"
        )
        self.ttk.Label(text, text=title, style="HeaderTitle.TLabel").pack(anchor="w")
        self.ttk.Label(text, text=subtitle, style="HeaderSub.TLabel").pack(anchor="w")

    def _clear(self) -> None:
        for child in self.page.winfo_children():
            child.destroy()
        for child in self.footer.winfo_children():
            child.destroy()

    # -- pages -----------------------------------------------------------

    def _show_welcome(self) -> None:
        self._clear()
        ttk, tk = self.ttk, self.tk

        ttk.Label(
            self.page,
            text="This will set up the widget in its own private environment.\n"
                 "Nothing is installed system-wide and no administrator rights "
                 "are needed.",
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(0, 16))

        box = ttk.LabelFrame(self.page, text="Install location", padding=12)
        box.pack(fill="x", pady=(0, 14))
        self.var_prefix = tk.StringVar(value=str(self.options.prefix))
        row = ttk.Frame(box)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.var_prefix)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse).pack(
            side="left", padx=(8, 0)
        )

        box = ttk.LabelFrame(self.page, text="Options", padding=12)
        box.pack(fill="x", pady=(0, 14))
        self.var_shortcuts = tk.BooleanVar(value=self.options.shortcuts)
        self.var_autostart = tk.BooleanVar(value=self.options.autostart)
        ttk.Checkbutton(
            box,
            text="Add shortcuts to the "
                 + ("Start Menu" if is_windows() else "application launcher"),
            variable=self.var_shortcuts,
        ).pack(anchor="w")
        self.check_autostart = ttk.Checkbutton(
            box, text="Start the widget automatically on login",
            variable=self.var_autostart,
        )
        self.check_autostart.pack(anchor="w", pady=(4, 0))

        if is_wsl():
            self.var_autostart.set(False)
            self.check_autostart.state(["disabled"])
            ttk.Label(
                box,
                text="Autostart is unavailable under WSL: there is no "
                     "persistent desktop session for it to run in.",
                style="Hint.TLabel",
                wraplength=520,
                justify="left",
            ).pack(anchor="w", pady=(6, 0))

        ttk.Label(
            self.page,
            text="Your existing configuration is never touched, so "
                 "reinstalling keeps the widget exactly where you put it.",
            style="Hint.TLabel",
            wraplength=560,
            justify="left",
        ).pack(anchor="w")

        ttk.Button(self.footer, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(self.footer, text="Install", command=self._start_install).pack(
            side="right", padx=(0, 8)
        )

    def _show_confirm(self) -> None:
        self._clear()
        ttk = self.ttk
        try:
            self.uninstaller = Uninstaller(
                self.options.prefix, purge=self.purge, dry_run=self.options.dry_run
            )
        except InstallError as exc:
            self._show_result(False, str(exc), exc.hint)
            return

        remove, keep = self.uninstaller.summary()
        ttk.Label(
            self.page, text="These will be removed:", justify="left"
        ).pack(anchor="w", pady=(0, 8))
        listing = self._make_log(height=12)
        for item in remove:
            listing.insert("end", f"{item}\n")
        if keep:
            listing.insert("end", "\nKept:\n")
            for item in keep:
                listing.insert("end", f"{item}\n")
        listing.configure(state="disabled")

        self.var_purge = self.tk.BooleanVar(value=self.purge)
        ttk.Checkbutton(
            self.page,
            text="Also remove my settings and the widget's shell history",
            variable=self.var_purge,
            command=self._repopulate_confirm,
        ).pack(anchor="w", pady=(12, 0))

        ttk.Button(self.footer, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(self.footer, text="Remove", command=self._start_uninstall).pack(
            side="right", padx=(0, 8)
        )

    def _repopulate_confirm(self) -> None:
        self.purge = bool(self.var_purge.get())
        self._show_confirm()

    def _show_progress(self, title: str) -> None:
        self._clear()
        ttk = self.ttk
        self.status = ttk.Label(self.page, text=title, justify="left")
        self.status.pack(anchor="w", pady=(0, 10))
        self.bar = ttk.Progressbar(self.page, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(0, 14))
        self.log = self._make_log(height=16)
        ttk.Button(self.footer, text="Cancel", command=self._cancel).pack(side="right")

    def _show_result(self, ok: bool, message: str, detail: str = "") -> None:
        self._clear()
        ttk = self.ttk
        ttk.Label(
            self.page,
            text=message,
            style="Ok.TLabel" if ok else "Error.TLabel",
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        if detail:
            box = self._make_log(height=14)
            box.insert("end", detail)
            box.configure(state="disabled")

        if ok and not self.uninstall_mode and not self.options.dry_run:
            self.var_launch = self.tk.BooleanVar(value=True)
            ttk.Checkbutton(
                self.page, text="Open the settings app now",
                variable=self.var_launch,
            ).pack(anchor="w", pady=(12, 0))
        else:
            self.var_launch = None

        self.result = 0 if ok else 1
        ttk.Button(self.footer, text="Finish", command=self._finish).pack(side="right")

    def _make_log(self, height: int):
        frame = self.ttk.Frame(self.page)
        frame.pack(fill="both", expand=True)
        text = self.tk.Text(
            frame, height=height, wrap="none", font=("TkFixedFont", 9),
            relief="solid", borderwidth=1,
        )
        scroll = self.ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    # -- actions ---------------------------------------------------------

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(
            title="Install location", initialdir=str(Path.home())
        )
        if chosen:
            self.var_prefix.set(str(Path(chosen) / DIST_NAME))

    def _cancel(self) -> None:
        self.result = 1
        self.root.destroy()

    def _finish(self) -> None:
        if self.var_launch is not None and self.var_launch.get():
            launcher = venv_script(self.options.prefix, f"{DIST_NAME}-settings")
            try:
                subprocess.Popen([str(launcher)], start_new_session=not is_windows())
            except OSError:
                pass
        self.root.destroy()

    def _start_install(self) -> None:
        self.options.prefix = Path(self.var_prefix.get()).expanduser()
        self.options.shortcuts = bool(self.var_shortcuts.get())
        self.options.autostart = bool(self.var_autostart.get())
        self._show_progress("Starting...")
        self._run_worker(lambda progress: Installer(self.options).run(progress),
                         done=self._install_done)

    def _start_uninstall(self) -> None:
        self.purge = bool(self.var_purge.get())
        self.uninstaller.purge = self.purge
        self._show_progress("Removing...")
        self._run_worker(self.uninstaller.run, done=self._uninstall_done)

    def _run_worker(self, work, done) -> None:
        """Run `work` off the main thread; tkinter may only be touched here."""
        import queue
        import threading

        self.queue = queue.Queue()
        self._done_callback = done

        def progress(message: str, fraction: float) -> None:
            self.queue.put(("progress", message, fraction))

        def target() -> None:
            try:
                work(progress)
                self.queue.put(("finished", "", 0.0))
            except InstallError as exc:
                self.queue.put(("error", str(exc), exc.hint))
            except Exception as exc:  # unexpected: still must reach the UI
                self.queue.put(("error", f"Unexpected error: {exc}", ""))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        self.root.after(60, self._drain)

    def _drain(self) -> None:
        import queue as _queue

        try:
            while True:
                kind, message, extra = self.queue.get_nowait()
                if kind == "progress":
                    if extra < 0:
                        self.log.insert("end", f"{message}\n")
                        self.log.see("end")
                    else:
                        self.status.configure(text=message)
                        self.bar.configure(value=extra * 100)
                        self.log.insert("end", f"* {message}\n")
                        self.log.see("end")
                elif kind == "finished":
                    self._done_callback(True, "", "")
                    return
                elif kind == "error":
                    self._done_callback(False, message, extra)
                    return
        except _queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _install_done(self, ok: bool, message: str, detail: str) -> None:
        if not ok:
            self._show_result(False, message, detail)
            return
        root = self.options.prefix
        lines = [
            f"Installed to {root}",
            "",
            "Settings app:",
            f"  {venv_script(root, f'{DIST_NAME}-settings')}",
            "Widget:",
            f"  {venv_script(root, DIST_NAME)}",
            "",
            "To remove it later:",
            f"  python {root / 'uninstall.py'}",
        ]
        if self.options.shortcuts:
            lines.insert(
                1,
                "Shortcuts added to the "
                + ("Start Menu." if is_windows() else "application launcher."),
            )
        self._show_result(True, f"{DISPLAY_NAME} is installed.", "\n".join(lines))

    def _uninstall_done(self, ok: bool, message: str, detail: str) -> None:
        if not ok:
            self._show_result(False, message, detail)
            return
        self._show_result(True, f"{DISPLAY_NAME} has been removed.",
                          "" if self.purge else "Your settings were kept.")

    def run(self) -> int:
        self.root.mainloop()
        return self.result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description=f"Install or remove {DISPLAY_NAME}.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 install.py                 graphical installer\n"
            "  python3 install.py --cli --yes     unattended install\n"
            "  python3 install.py --dry-run       show what would happen\n"
            "  python3 install.py --uninstall     remove it again\n"
        ),
    )
    parser.add_argument("--prefix", type=Path, default=None, metavar="PATH",
                        help=f"install location (default: {app_root()})")
    parser.add_argument("--cli", action="store_true",
                        help="use the text installer even if a GUI is available")
    parser.add_argument("--gui", action="store_true",
                        help="require the graphical installer; fail if unavailable")
    parser.add_argument("--autostart", dest="autostart", action="store_true",
                        default=None, help="enable start-on-login")
    parser.add_argument("--no-autostart", dest="autostart", action="store_false",
                        help="do not enable start-on-login")
    parser.add_argument("--no-shortcuts", action="store_true",
                        help="skip Start Menu / launcher entries")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every action, change nothing")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove an existing install")
    parser.add_argument("--purge", action="store_true",
                        help="with --uninstall, also remove settings and history")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="do not ask for confirmation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    options = Options(
        prefix=(args.prefix or app_root()).expanduser(),
        shortcuts=not args.no_shortcuts,
        autostart=bool(args.autostart),
        dry_run=args.dry_run,
    )

    want_gui = not args.cli
    if want_gui:
        ok, reason = gui_available()
        if not ok:
            if args.gui:
                print(f"\n  {reason}\n")
                return 1
            print(f"\n  Note: {reason.splitlines()[0]}")
            print("  Falling back to the text installer.\n")
            want_gui = False

    if want_gui:
        try:
            gui = InstallerGui(options, uninstall=args.uninstall, purge=args.purge)
        except Exception as exc:  # a display that exists but will not cooperate
            if args.gui:
                print(f"\n  Could not start the graphical installer: {exc}\n")
                return 1
            print(f"\n  Note: could not start the graphical installer ({exc}).")
            print("  Falling back to the text installer.\n")
        else:
            return gui.run()

    if args.uninstall:
        return cli_uninstall(options.prefix, args.purge, args.dry_run, args.yes)
    return cli_install(options, args.yes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        raise SystemExit(1)
