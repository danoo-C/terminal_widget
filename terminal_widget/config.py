"""Configuration: defaults, platform paths, load/save, shell resolution.

The widget reads this file; the settings app writes it. Both agree on the
shape defined by :class:`Config`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "terminal_widget"

# Opacity modes -- see README, "Appearance".
OPACITY_BACKGROUND = "background"  # only the backdrop fades; glyphs stay solid
OPACITY_WINDOW = "window"  # the whole window fades, glyphs included


def config_dir() -> Path:
    """Platform-conventional configuration directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    """History file used when ``separate_history`` is on."""
    return config_dir() / "shell_history"


def _default_font() -> str:
    if sys.platform == "win32":
        return "Cascadia Mono"
    if sys.platform == "darwin":
        return "Menlo"
    return "Monospace"


@dataclass
class Config:
    """Every setting the widget understands."""

    # -- Geometry (pixels). Two-way: the settings app edits these, and
    #    dragging/resizing in config mode writes back to them.
    x: int = 120
    y: int = 120
    width: int = 720
    height: int = 420

    # -- Shell. `shell` is a preset key; "custom" defers to `custom_command`.
    shell: str = "default"
    custom_command: str = ""
    #: Keep the widget's shell history out of the login shell's history file.
    #: A desktop widget you type the odd command into should not rewrite the
    #: history of the terminal you actually work in.
    separate_history: bool = True

    # -- Appearance
    opacity: int = 100  # 0-100
    opacity_mode: str = OPACITY_BACKGROUND
    font_family: str = field(default_factory=_default_font)
    font_size: int = 11
    foreground: str = "#e0e0e0"
    background: str = "#101014"

    def clamped(self) -> "Config":
        """Return a copy with out-of-range values pulled back into range."""
        c = Config(**asdict(self))
        c.width = max(120, int(c.width))
        c.height = max(80, int(c.height))
        c.x = int(c.x)
        c.y = int(c.y)
        c.opacity = min(100, max(10, int(c.opacity)))
        c.font_size = min(72, max(5, int(c.font_size)))
        if c.opacity_mode not in (OPACITY_BACKGROUND, OPACITY_WINDOW):
            c.opacity_mode = OPACITY_BACKGROUND
        return c

    # -- Persistence -------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config, falling back to defaults for anything missing.

        A corrupt or unreadable file is not fatal: the widget must always
        start, so we fall back to defaults rather than refusing to run.
        """
        path = path or config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known}).clamped()

    def save(self, path: Path | None = None) -> None:
        """Write config atomically, so a crash mid-write can't corrupt it."""
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)


# -- Shell presets ---------------------------------------------------

#: Preset key -> (label, argv). Only entries whose program exists are offered.
_WINDOWS_SHELLS: list[tuple[str, str, list[str]]] = [
    ("cmd", "Command Prompt", ["cmd.exe"]),
    ("powershell", "PowerShell", ["powershell.exe", "-NoLogo"]),
    ("pwsh", "PowerShell 7", ["pwsh.exe", "-NoLogo"]),
    ("wsl", "WSL", ["wsl.exe"]),
]

_POSIX_SHELLS: list[tuple[str, str, list[str]]] = [
    ("bash", "Bash", ["bash"]),
    ("zsh", "Zsh", ["zsh"]),
    ("fish", "Fish", ["fish"]),
    ("sh", "sh", ["sh"]),
]


def available_shells() -> list[tuple[str, str]]:
    """(key, label) pairs for shells that actually exist on this machine.

    The settings app builds its dropdown from this, so it never offers a
    shell that would fail to spawn.
    """
    presets = _WINDOWS_SHELLS if sys.platform == "win32" else _POSIX_SHELLS
    out = [("default", "System default")]
    out += [(key, label) for key, label, argv in presets if shutil.which(argv[0])]
    out.append(("custom", "Custom command..."))
    return out


def environment_for(cfg: Config) -> dict[str, str]:
    """Environment for the widget's shell.

    Beyond announcing a terminal we can emulate, this optionally redirects
    shell history so commands typed into the widget do not end up in the
    history of the user's real terminal.
    """
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    # Let the shell take its size from the PTY, not from stale inherited values.
    env.pop("LINES", None)
    env.pop("COLUMNS", None)

    if cfg.separate_history:
        path = history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        # HISTFILE covers bash and zsh; fish reads fish_history as a session
        # name rather than a path, so it gets its own namespace instead.
        env["HISTFILE"] = str(path)
        env["fish_history"] = APP_NAME
    return env


def resolve_shell(cfg: Config) -> list[str]:
    """Turn the configured shell into an argv list to spawn."""
    if cfg.shell == "custom" and cfg.custom_command.strip():
        import shlex

        if sys.platform == "win32":
            # Windows command lines don't follow POSIX quoting; pass through.
            return [cfg.custom_command.strip()]
        return shlex.split(cfg.custom_command)

    presets = _WINDOWS_SHELLS if sys.platform == "win32" else _POSIX_SHELLS
    for key, _label, argv in presets:
        if key == cfg.shell and shutil.which(argv[0]):
            return list(argv)

    # "default", or a configured shell that has since gone missing.
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"]
