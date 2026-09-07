# terminal_widget

A desktop terminal **widget**: one borderless, headerless window that sits on your
desktop and does exactly one thing — run a shell. No tabs, no menu bar, no title
bar, no window chrome. Just the terminal.

Configuration lives in a **separate settings app**, so the widget itself stays
free of UI clutter.

Runs on **Linux** and **Windows**.

> **Status: installable and working on Linux.** One command installs
> everything -- a private environment, launcher entries, optional autostart,
> and an uninstaller. The widget and settings app both work, and 98 tests
> cover them. **Not yet verified on Windows:** the code is there and the paths
> are right, but it has never been run.

---

## The idea

Most terminal emulators are applications you *switch to*. This one is meant to be
*part of the desktop* — always in the same spot, always showing the same shell,
the way a clock or system-monitor widget is. It should feel less like a program
you launch and more like a panel that happens to be a terminal.

That means:

- **No decorations.** No title bar, no borders, no close/minimize buttons.
- **Stays put.** Fixed position and size, remembered between sessions.
- **One shell, no tabs.** If you want tabs, you want a different program.
- **Configured elsewhere.** Everything tunable lives in the settings app.

## Two components

| Component | What it is |
| --- | --- |
| **Widget** (`terminal_widget`) | The borderless window. Spawns a shell in a pseudo-terminal, renders its output, forwards your keystrokes. Reads config; never asks questions. |
| **Settings app** (`terminal_widget.settings`) | A normal, decorated window. Edits the config — geometry, shell, opacity, appearance — and puts the widget into config mode for as long as it is open. |

Both are launched from the same package, so a single install gives you both.

---

## The two modes

This is the core of the design. The widget is always in exactly one of two modes,
and **the mode is determined by a single fact: whether the settings app is
running.** There is no mode toggle, no hotkey, no right-click menu. Open settings
to arrange the widget; close settings to use it.

### Config mode — settings app is open

The widget becomes a window you can arrange:

- **Drag from anywhere.** Click and drag anywhere inside the terminal to move the
  window. There is no title bar, so the entire surface is the drag handle.
- **Resize like a normal window.** Edge and corner handles, the usual cursors.
- **Live preview.** Opacity, font, and color changes from the settings app apply
  immediately — no restart, no apply button needed to *see* the result.
- **The shell keeps running.** Entering or leaving config mode never restarts your
  session. (Changing the shell program is the one exception — see below.)
- **Visibly distinct.** The widget shows an outline or similar affordance while in
  config mode, so it is never ambiguous which mode you are in.

**Trade-off, stated plainly:** because click-and-drag anywhere moves the window,
you cannot select text with the mouse while config mode is active. Config mode is
meant to be brief — you open settings, arrange the widget, close settings.

### Locked mode — settings app is closed

The widget becomes a pure terminal:

- **It does not move.** Dragging does nothing. Position is frozen.
- **It does not resize.** No handles, no resize cursors. Size is frozen.
- **It is still a real terminal.** Click it to take keyboard focus, type commands,
  read output, select text with the mouse. Everything you expect of a terminal.

Put differently: locked mode ignores *window-management* gestures and nothing
else. It is a terminal without a title bar, not a picture of a terminal.

### Mode transitions at a glance

| | Config mode | Locked mode |
| --- | --- | --- |
| Settings app | open | closed |
| Drag to move | **yes**, from anywhere | no |
| Resize handles | **yes** | no |
| Keyboard input to shell | yes | yes |
| Mouse text selection | no (drag moves window) | yes |
| Shell session | continues | continues |

---

## Settings reference

Everything the settings app exposes.

### Geometry

| Setting | Type | Notes |
| --- | --- | --- |
| X position | pixels | Top-left corner of the widget. |
| Y position | pixels | |
| Width | pixels | |
| Height | pixels | |

**Geometry is two-way.** Dragging or resizing the widget in config mode updates
these numbers live in the settings app; typing a number into the settings app
moves or resizes the widget live. They are two views of one value, not a form you
submit.

Width and height are entered in pixels, but a terminal is a character grid, so the
settings app also displays the resulting grid size (e.g. `98 × 27 cells`) as you
adjust them.

### Shell

| Setting | Type | Notes |
| --- | --- | --- |
| Terminal program | dropdown | The shell the widget spawns. |
| Custom command | text | Free-form command line, when the dropdown is set to *Custom*. |

The dropdown offers what actually exists on the current platform:

- **Windows:** Command Prompt (`cmd.exe`), PowerShell, WSL, Custom
- **Linux:** Bash, Zsh, Fish, the login shell from `$SHELL`, Custom

Changing the shell **does** restart the session — it is the one setting that
cannot be applied live, since it means killing one process and starting another.
The settings app will say so before it happens.

### Appearance

| Setting | Type | Notes |
| --- | --- | --- |
| Opacity | 0–100% | How transparent the widget is. |
| Opacity applies to | choice | *Background only* or *Entire window* — see below. |

Two opacity behaviours, selectable:

- **Background only** *(recommended default)* — the desktop shows through behind
  the terminal, but characters render fully opaque. Text stays sharp and readable
  even at low opacity. This is what most desktop terminal widgets do.
- **Entire window** — text and background fade together, like a normal window's
  opacity. Simpler, more uniform, but text washes out quickly.

Font family, font size, and color scheme also live here.

### Behaviour

| Setting | Type | Notes |
| --- | --- | --- |
| Keep shell history separate | on/off | On by default. |

With this on, the widget's shell writes to its own history file
(`~/.config/terminal_widget/shell_history`) instead of the one your login
shell uses. A widget you type the occasional command into should not be
rewriting the history of the terminal you actually work in. Turn it off to
share one history with everything else.

| Setting | Type | Notes |
| --- | --- | --- |
| Start automatically on login | on/off | Applied immediately, not on Save. |

Autostart is an operating-system setting, not a config field, so the checkbox
reads the real state rather than a remembered one -- an XDG autostart entry on
Linux, a per-user `Run` registry value on Windows. Neither needs administrator
rights. It is disabled, with the reason shown, when the widget is running from
a source checkout (no stable path to register) or under WSL (no persistent
desktop session for it to run in).

### Configuration

Shows where the config file lives, with an **Open config folder** button that
opens it in the system file manager.

---

## Architecture

The hard part of a cross-platform terminal widget is that "spawn a terminal" means
two very different things on Linux and Windows. The plan is to **emulate the
terminal ourselves** rather than embed someone else's:

```
  ┌─────────────────────────────┐
  │  Widget window (frameless)  │   PySide6 / Qt6
  │  ┌───────────────────────┐  │
  │  │  Terminal renderer    │  │   draws the screen buffer
  │  └───────────┬───────────┘  │
  └──────────────┼──────────────┘
                 │ screen state
        ┌────────┴────────┐
        │  VT emulator    │         pyte — parses escape sequences
        └────────┬────────┘
                 │ raw bytes
        ┌────────┴────────┐
        │   PTY backend   │         platform split
        └────────┬────────┘
         ┌───────┴────────┐
   ptyprocess           pywinpty
   (Linux PTY)      (Windows ConPTY)
```

**Why this shape:** the alternative — reparenting an existing terminal emulator's
window into ours (XEmbed on Linux, `SetParent` on Windows) — is far less code but
breaks on Wayland, behaves differently on every desktop environment, and gives no
control over appearance. Emulating the terminal keeps behaviour and looks
identical on both platforms, at the cost of writing a renderer.

### How the two processes talk

The widget must know, continuously, whether the settings app is open, and the two
must share geometry live in both directions. That needs a real channel — a local
socket (Unix domain socket on Linux, named pipe on Windows) carrying:

- settings app → widget: *entering config mode*, *setting changed*, *leaving config mode*
- widget → settings app: *user dragged me here*, *user resized me to this*

The connection dropping **is** the "settings app closed" signal, which means an
ordinary quit and a crash both correctly return the widget to locked mode. The
widget must never get stuck in config mode because the settings app died.

### Dependencies

| Package | Role |
| --- | --- |
| `PySide6` | GUI toolkit (Qt 6). Frameless windows, per-pixel opacity, cross-platform. LGPL. |
| `pyte` | Pure-Python VT100/xterm screen emulator. Turns raw shell bytes into a character grid. |
| `ptyprocess` | PTY handling on Linux. |
| `pywinpty` | ConPTY handling on Windows. |

---

## Installing

Requires **Python 3.11+** (developed against 3.13). Nothing needs administrator
rights, and nothing is installed outside your home directory.

```bash
git clone https://github.com/danoo-C/terminal_widget.git
cd terminal_widget
python3 install.py
```

On Windows, `py install.py`.

That opens a graphical installer which creates a private environment, installs
the application into it, adds a Start Menu or application-launcher entry, and
optionally enables autostart. It also writes an uninstaller.

`install.py` uses tkinter, which ships with the official Windows Python. On
Linux it is usually one package away (`sudo apt install python3-tk`); if it is
missing, the installer falls back to a text version and tells you the command.

### Installer options

```
python3 install.py --cli          text installer instead of the GUI
python3 install.py --dry-run      print every action, change nothing
python3 install.py --yes          unattended, no prompts
python3 install.py --prefix PATH  install somewhere other than the default
python3 install.py --uninstall    remove it again
```

| | Linux | Windows |
| --- | --- | --- |
| Installed to | `~/.local/share/terminal-widget/` | `%LOCALAPPDATA%\TerminalWidget\` |
| Shortcuts | `~/.local/share/applications/` | Start Menu |
| Autostart | `~/.config/autostart/` | `HKCU\...\CurrentVersion\Run` |
| **Config (never touched)** | `~/.config/terminal_widget/` | `%APPDATA%\terminal_widget\` |

Configuration lives outside the install directory on purpose, so reinstalling
keeps the widget exactly where you put it.

### System dependencies (Linux only)

Qt needs a handful of X11 libraries that are not always installed:

```bash
sudo apt install libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 \
    libxkbcommon-x11-0
```

The installer checks for these and stops with the exact command for your
distribution if they are missing, rather than leaving you with an install that
crashes on launch. Windows needs no equivalent step.

### Removing it

```bash
python3 ~/.local/share/terminal-widget/uninstall.py
```

This removes only what the install manifest records. Your settings are kept
unless you pass `--purge`.

## Running

After installing, launch **Terminal Widget Settings** from the Start Menu or
application launcher, or run the commands directly:

```bash
~/.local/share/terminal-widget/venv/bin/terminal-widget
~/.local/share/terminal-widget/venv/bin/terminal-widget-settings
```

Start the widget first, then open settings whenever you want to move, resize or
reconfigure it. The settings app can also launch the widget itself, and if you
open settings with no widget running it still edits the config file for next
time.

## Developing

```bash
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"

python -m pytest tests/          # 98 tests, no display needed
python -m pyright                # type checking
python -m terminal_widget        # run from source
python -m terminal_widget.settings
```

The test suite runs on Qt's offscreen platform. Tests that need a local socket
skip themselves where the environment forbids binding one.

## Layout

```
terminal_widget/
├── install.py             # the installer, GUI and CLI
├── pyproject.toml         # packaging and entry points
├── terminal_widget/
│   ├── __main__.py        # widget entry point
│   ├── window.py          # frameless window; the two modes
│   ├── renderer.py        # draws the pyte screen buffer
│   ├── session.py         # shell lifecycle + reader thread
│   ├── keys.py            # Qt key events -> escape sequences
│   ├── config.py          # load / save / defaults / shells
│   ├── platform_info.py   # platform facts, stdlib only
│   ├── ipc.py             # local socket to the settings app
│   ├── pty/               # ptyprocess (Linux) / pywinpty (Windows)
│   ├── autostart/         # XDG entry (Linux) / Run key (Windows)
│   ├── assets/            # application icon
│   └── settings/          # the settings app
├── tools/make_icon.py     # regenerates the icon
├── tests/                 # 98 tests, offscreen
├── LICENSE
└── README.md
```

`platform_info.py` and `autostart/` are deliberately stdlib-only: the installer
imports them before any dependency exists.

Config lives in the platform-conventional spot — `~/.config/terminal_widget/`
on Linux, `%APPDATA%\terminal_widget\` on Windows.

## Known unknowns

Things that will need deciding or discovering as the code gets written:

- **Wayland.** Setting a window's absolute position is not permitted under
  Wayland, which is a direct problem for a widget whose whole point is living at
  fixed coordinates. May need an X11-only path plus a documented limitation.
- **Window stacking.** Whether the widget sits below normal windows (true desktop
  widget), above them, or in the normal stack. Pinning to the desktop layer has no
  supported API on Windows.
- **Resize granularity.** Free-pixel resizing leaves partial character cells at
  the edges; snapping to whole cells fights the OS resize handles. Needs a call.
- **Frame vs client coordinates.** Some window managers put a frame around a
  frameless window. The widget stores client coordinates throughout, because
  `setGeometry` uses them while `QWidget.x()` reports the frame -- mixing the
  two made the widget drift by the frame offset on every launch.
- **Font rendering performance.** Repainting a full character grid per frame can
  be slow if done naively; will likely need dirty-region tracking.
- **Scrollback.** Whether the widget keeps any at all, given the no-chrome goal —
  there is no scrollbar and no obvious place to put one.
- **Shell exit.** The widget currently closes when the shell exits. Respawning
  or showing a message may suit a permanent desktop widget better.
- **Windows.** Nothing has ever been run on Windows -- not the ConPTY backend,
  not the installer, not the Start Menu shortcuts or the Run-key autostart.

## License

[MIT](LICENSE) © 2026 Daniel Danko
