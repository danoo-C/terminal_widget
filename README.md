# terminal_widget

A desktop terminal **widget**: one borderless, headerless window that sits on your
desktop and does exactly one thing — run a shell. No tabs, no menu bar, no title
bar, no window chrome. Just the terminal.

Configuration lives in a **separate settings app**, so the widget itself stays
free of UI clutter.

Runs on **Linux** and **Windows**.

> **Status: both apps work.** The widget spawns a shell, renders it, and takes
> input; the settings app drives it live over a local socket. Opening settings
> puts the widget into config mode and closing settings takes it out, exactly
> as described below. Not yet verified on Windows.

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

---

## Planned architecture

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

### Proposed dependencies

| Package | Role |
| --- | --- |
| `PySide6` | GUI toolkit (Qt 6). Frameless windows, per-pixel opacity, cross-platform. LGPL. |
| `pyte` | Pure-Python VT100/xterm screen emulator. Turns raw shell bytes into a character grid. |
| `ptyprocess` | PTY handling on Linux. |
| `pywinpty` | ConPTY handling on Windows. |

None of these are installed yet — see below.

---

## Getting started

Requires **Python 3.11+** (developed against 3.13).

### Linux

```bash
git clone https://github.com/<you>/terminal_widget.git
cd terminal_widget

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### Windows (PowerShell)

```powershell
git clone https://github.com/<you>/terminal_widget.git
cd terminal_widget

py -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
```

The `.venv/` directory is git-ignored — each machine creates its own.

### System dependencies (Linux only)

Qt needs a handful of X11 libraries that are not always installed:

```bash
sudo apt install libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 \
    libxkbcommon-x11-0
```

Without these, Qt fails with `libEGL.so.1: cannot open shared object file` or
`Could not load the Qt platform plugin "xcb"`. Windows needs no equivalent step.

### Running

```bash
python -m terminal_widget            # the widget
python -m terminal_widget.settings   # the settings app
```

Start the widget first, then open settings whenever you want to move, resize or
reconfigure it. The settings app can also launch the widget itself, and if you
open settings with no widget running it still edits the config file for next
time.

### Tests

```bash
pip install pytest
python -m pytest tests/
```

The suite runs on Qt's offscreen platform, so it needs no display.

## Planned layout

```
terminal_widget/
├── terminal_widget/
│   ├── __main__.py        # widget entry point                    [done]
│   ├── window.py          # frameless window; the two modes       [done]
│   ├── renderer.py        # draws the pyte screen buffer          [done]
│   ├── session.py         # shell lifecycle + reader thread       [done]
│   ├── keys.py            # Qt key events -> escape sequences     [done]
│   ├── config.py          # load / save / defaults / shells       [done]
│   ├── pty/
│   │   ├── base.py        # the backend interface                 [done]
│   │   ├── posix.py       # ptyprocess backend                    [done]
│   │   └── windows.py     # pywinpty backend            [done, untested]
│   ├── ipc.py             # local socket to the settings app      [done]
│   └── settings/
│       └── __main__.py    # settings app entry point              [done]
├── tests/                 # 70 tests, offscreen                   [done]
├── LICENSE
└── README.md
```

Config will live in the platform-conventional spot — `~/.config/terminal_widget/`
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
- **Windows.** The ConPTY backend is written but has never been run on Windows.

## License

[MIT](LICENSE) © 2026 Daniel Danko
