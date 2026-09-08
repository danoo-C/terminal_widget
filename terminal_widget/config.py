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

#: The lowest opacity each mode can survive. Background-only fades nothing but
#: the backdrop, so 0 is the whole point of it -- glyphs floating on the
#: desktop. Whole-window fades the glyphs too, and a fully faded window is one
#: you cannot see, find, or fix, so it stops short of disappearing.
MIN_OPACITY_BACKGROUND = 0
MIN_OPACITY_WINDOW = 10


def min_opacity(mode: str) -> int:
    """The lowest opacity that still leaves something on screen in ``mode``."""
    return MIN_OPACITY_WINDOW if mode == OPACITY_WINDOW else MIN_OPACITY_BACKGROUND


# Stacking layers -- see README, "Behaviour".
STACKING_DESKTOP = "desktop"  # below normal windows: part of the desktop
STACKING_NORMAL = "normal"  # in the ordinary window order
STACKING_TOP = "top"  # above normal windows

#: Ordered (key, label) pairs, the way available_shells() hands the settings
#: app its dropdown. One source of truth, so the menu and the validator below
#: cannot drift apart and offer a layer that gets thrown away on save.
STACKING_CHOICES: tuple[tuple[str, str], ...] = (
    (STACKING_DESKTOP, "Behind other windows"),
    (STACKING_NORMAL, "In the normal window order"),
    (STACKING_TOP, "Always on top"),
)

#: Built once at import: clamped() runs on every IPC message, which is every
#: keystroke in the settings app, so it must not build a set per call.
_STACKING_KEYS = frozenset(key for key, _label in STACKING_CHOICES)


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
    #: Directory to start the shell in. Empty means "let the shell decide",
    #: which is what every terminal does by default.
    working_dir: str = ""
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

    # -- Scrollback, in lines. 0 turns it off.
    scrollback: int = 5000

    #: Where the viewport sits once the shell has finished starting up. Off by
    #: default: it only earns its keep when the startup output is taller than
    #: the widget, which is a thing you opt into by printing a banner.
    scroll_top_on_start: bool = False

    # -- Where the widget sits in the window stack. "desktop" keeps it below
    #    normal windows, which is what makes it furniture rather than an app.
    stacking: str = STACKING_DESKTOP

    def clamped(self) -> "Config":
        """Return a copy with out-of-range values pulled back into range."""
        c = Config(**asdict(self))
        c.width = max(120, int(c.width))
        c.height = max(80, int(c.height))
        c.x = int(c.x)
        c.y = int(c.y)
        # Order matters: the opacity floor depends on the mode, so an
        # unrecognised mode has to fall back *before* the floor is applied.
        if c.opacity_mode not in (OPACITY_BACKGROUND, OPACITY_WINDOW):
            c.opacity_mode = OPACITY_BACKGROUND
        c.opacity = min(100, max(min_opacity(c.opacity_mode), int(c.opacity)))
        c.font_size = min(72, max(5, int(c.font_size)))
        c.scrollback = min(50000, max(0, int(c.scrollback)))
        # Unlike the opacity floor, nothing derives from this, so it can be
        # checked anywhere in here.
        if c.stacking not in _STACKING_KEYS:
            c.stacking = STACKING_DESKTOP
        # Deliberately not checked against the filesystem: clamped() runs on
        # every IPC message, which is every keystroke in the settings app.
        # Whether the directory exists is decided once, at spawn time.
        c.working_dir = str(c.working_dir or "")
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


def working_dir_for(cfg: Config) -> str | None:
    """The directory to start the shell in, or None to let it choose.

    A configured directory that has since been renamed or deleted must not
    stop the widget from starting. Both PTY backends chdir in the child, so
    a bad path there kills the shell before it prints anything, the reader
    hits EOF, and the window closes on launch with nothing to explain it.
    Checking here, in the parent, turns that into a silent fallback.
    """
    raw = (cfg.working_dir or "").strip()
    if not raw:
        return None
    try:
        path = Path(os.path.expandvars(raw)).expanduser()
        if path.is_dir():
            return str(path)
    except OSError:
        pass
    return None


def split_windows_command_line(line: str) -> list[str]:
    """Split a Windows command line the way ``CommandLineToArgvW`` does.

    Not the same thing as :mod:`shlex`. Backslashes are ordinary path
    separators unless they precede a quote, and the quotes themselves are
    syntax rather than part of the argument -- which is exactly what
    ``shlex.split(posix=False)`` gets wrong, since it leaves them in the
    token for something further down to parse a second time. See issues.md,
    issue 2: nothing further down does.
    """
    argv: list[str] = []
    i, n = 0, len(line)

    # argv[0] plays by different rules: quotes delimit it, and backslashes
    # inside it are always literal, because it is a path.
    while i < n and line[i] in " \t":
        i += 1
    if i < n:
        first: list[str] = []
        if line[i] == '"':
            i += 1
            while i < n and line[i] != '"':
                first.append(line[i])
                i += 1
            i += 1
        else:
            while i < n and line[i] not in " \t":
                first.append(line[i])
                i += 1
        argv.append("".join(first))

    cur: list[str] = []
    in_quotes = started = False
    while i < n:
        ch = line[i]
        if ch == "\\":
            slashes = 0
            while i < n and line[i] == "\\":
                slashes += 1
                i += 1
            if i < n and line[i] == '"':
                # 2n backslashes then a quote: n backslashes, quote is syntax.
                # 2n+1: n backslashes, and the quote is a literal character.
                cur.append("\\" * (slashes // 2))
                if slashes % 2:
                    cur.append('"')
                    i += 1
                elif in_quotes and i + 1 < n and line[i + 1] == '"':
                    cur.append('"')
                    i += 2
                else:
                    in_quotes = not in_quotes
                    i += 1
                started = True
            else:
                cur.append("\\" * slashes)
                started = started or bool(slashes)
            continue
        if ch == '"':
            if in_quotes and i + 1 < n and line[i + 1] == '"':
                cur.append('"')  # "" inside quotes is one literal quote
                i += 2
            else:
                in_quotes = not in_quotes
                i += 1
            started = True
            continue
        if ch in " \t" and not in_quotes:
            if started:
                argv.append("".join(cur))
                cur, started = [], False
            i += 1
            continue
        cur.append(ch)
        started = True
        i += 1
    if started:
        argv.append("".join(cur))
    return argv


def resolve_shell(cfg: Config) -> list[str]:
    """Turn the configured shell into an argv list to spawn."""
    if cfg.shell == "custom" and cfg.custom_command.strip():
        if sys.platform == "win32":
            # Parsed with Windows rules rather than POSIX ones, and parsed
            # *here*, so that nothing downstream has to guess whether it is
            # looking at a command line or at an argv.
            argv = split_windows_command_line(cfg.custom_command.strip())
            if argv and argv[0]:
                return argv
            # A lone quote parses to a program with no name. Fall through to
            # the default shell rather than spawn that -- deliberately the
            # only such fallback, because a command that is merely wrong
            # should reach the shell and be reported, not be replaced.
        else:
            import shlex

            return shlex.split(cfg.custom_command)

    presets = _WINDOWS_SHELLS if sys.platform == "win32" else _POSIX_SHELLS
    for key, _label, argv in presets:
        if key == cfg.shell and shutil.which(argv[0]):
            return list(argv)

    # "default", or a configured shell that has since gone missing.
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"]
