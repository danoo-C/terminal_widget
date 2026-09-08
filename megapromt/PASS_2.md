# Terminal Widget — Pass 2 Brief

> **What this is.** A complete, self-contained specification for the second
> pass of this project. Pass 1 was a sketch: it proved the hard parts work.
> Pass 2 turns it into something a stranger can install and use.
>
> **Read section 3 before writing any code.** It is a catalogue of traps that
> already cost real debugging time in pass 1. Most of them look like nothing
> and behave like a haunting.

---

## 0. How to use this document

Pass 1 lives in this repository and **works**. It is not a throwaway: it is a
tested reference implementation of every hard part (PTY handling, terminal
emulation, rendering, the two-mode window, the IPC link).

**Recommendation: evolve pass 1, do not rewrite it.** The terminal core is the
risky part and it is already done and covered by 70 tests. Pass 2's new work is
almost entirely *around* that core — packaging, installation, OS integration,
and two additions to the settings app.

If you do choose to rewrite, treat sections 2 and 3 as the requirements you
must re-satisfy, and mine the existing modules for the solutions rather than
rediscovering them.

Everything in sections 1–3 describes what already exists. Everything from
section 4 onward is new work.

---

## 1. The product

A desktop terminal **widget**: one borderless, headerless window that sits on
the desktop and runs a shell. No title bar, no borders, no tabs, no menu bar.
It should feel like part of the desktop — a panel that happens to be a terminal
— rather than an application you switch to.

Configuration lives in a **separate settings app**, so the widget itself has no
UI chrome at all.

Targets **Linux** and **Windows**.

### The two modes — the core of the design

The widget is always in exactly one of two states, and **the mode is determined
by a single fact: whether the settings app is running.** There is no mode
toggle, no hotkey, no right-click menu.

| | Config mode | Locked mode |
| --- | --- | --- |
| Settings app | open | closed |
| Drag to move | **yes**, from anywhere in the window | no |
| Resize handles | **yes**, on all edges and corners | no |
| Keyboard input to shell | yes | yes |
| Mouse text selection | no (drag moves the window) | yes |
| Shell session | continues | continues |

Locked mode ignores **window-management gestures and nothing else**. It is a
terminal without a title bar, not a picture of a terminal. This distinction has
been decided and must not be re-litigated.

### Non-goals

Do not add: tabs, split panes, a scrollback UI, tmux-style sessions, a command
palette, themes beyond the existing colour settings, or a plugin system. The
entire appeal is that it does one thing.

---

## 2. What pass 1 delivered

### Modules

| File | Lines | What it does | Status |
| --- | ---: | --- | --- |
| `config.py` | 192 | Defaults, platform paths, atomic save, shell presets, child environment | Working |
| `pty/base.py` | 40 | The backend interface | Working |
| `pty/posix.py` | 58 | ptyprocess backend | Verified on Linux |
| `pty/windows.py` | 68 | pywinpty / ConPTY backend | **Never run** |
| `session.py` | 123 | Shell lifecycle, reader thread, pyte screen | Working |
| `renderer.py` | 270 | Paints the screen buffer, ANSI colours, cursor | Working |
| `keys.py` | 108 | Qt key events → escape sequences | Working |
| `window.py` | 268 | Frameless window, the two modes, drag/resize | Working |
| `ipc.py` | 226 | Local socket link to the settings app | Working |
| `settings/__main__.py` | 368 | The settings app | Working |
| `tests/` | 545 | 70 tests, Qt offscreen platform | Passing |

### Verified

Against real bash on X11 (WSLg): ANSI colours, bold, `ls --color`, UTF-8
box-drawing, tab expansion, cursor placement, keyboard input including Ctrl+C
and DECCKM-aware arrow keys, both modes, resize edge detection, the full IPC
round trip, and geometry persistence across restarts.

### Not verified

- **Windows, entirely.** The ConPTY backend has never executed. Assume it is
  wrong until proven otherwise. This is the single largest risk in the project.
- **Wayland.** Absolute window positioning is not permitted to applications
  under Wayland, which is a direct problem for a widget whose premise is living
  at fixed coordinates.
- **Opacity visually.** The code paths are tested; the actual translucency has
  not been eyeballed against a real desktop with a compositor.
- **HiDPI / fractional scaling.** Never tried.

---

## 3. Hard-won lessons — read this before writing code

Each of these was a real bug in pass 1. They are recorded because every one of
them is easy to reintroduce and hard to diagnose.

### 3.1 Frame coordinates vs client coordinates

`QWidget.setGeometry()` takes **client** coordinates. `QWidget.x()` and `.y()`
return the **frame** position. Some window managers put a frame around even a
frameless window — WSLg offsets it by exactly 38px left and 59px up.

Saving `x()/y()` and restoring with `setGeometry()` shifted the window by the
frame offset on **every single launch**, compounding. It presented as "the
widget appears at random positions".

**Rule: use client coordinates everywhere.** Place with `setGeometry()`, read
back with `geometry()`. Never mix in `x()`, `y()`, `pos()`, or `move()` for
anything that gets persisted.

### 3.2 Window managers move windows; that is not user intent

Geometry writeback must only happen in **config mode**. In locked mode the user
has no way to move the window, so any geometry change originated with the WM
and must not be persisted as if it were a choice.

### 3.3 Who owns the live Config object

`apply_config()` swaps in a *new* `Config` instance. Anything that saves must
ask the window for the config currently in effect (`WidgetWindow.config`), not
hold on to the object it loaded at startup — otherwise settings changes are
silently discarded at exit.

Related: within `apply_config`, snapshot the target geometry into locals
**before** touching the window. Moving or resizing fires the geometry writeback,
which mutates the very `Config` you are reading targets from.

### 3.4 `QPainter.drawText` — rect vs baseline

`drawText(QRectF, str)` top-aligns text *inside* the rectangle.
`drawText(QPointF, str)` places the **baseline** at the point.

Passing a rect already offset by the font ascent pushed every glyph down by
almost a full line, which presented as "the cursor is in the wrong place" — the
cursor was correct and the text was wrong.

**Use the QPointF overload with `rect.y() + ascent`.**

### 3.5 PySide6 enums and type checkers

PySide6 keeps unscoped enum aliases (`Qt.LeftButton`) working at runtime, but
its **type stubs only declare the scoped form** (`Qt.MouseButton.LeftButton`).
Pylance/pyright flags every shorthand access as an error while the code runs
fine.

Pass 1 accumulated 98 such errors before they were cleaned up. **Write scoped
enum names from the start.** Note `Qt.Edges` has no stub at all — use the flag
type `Qt.Edge`, and `Qt.Edge(0)` for the empty value.

`QKeyEvent.key()` is typed `int`; convert with `Qt.Key(...)` if your lookup
tables are keyed by the enum. `Qt.Key` is an `IntEnum`, so comparisons and
arithmetic still work.

Some `QWidget` subclasses reject property keyword arguments in the stubs
(`QSlider(orientation, minimum=..., maximum=...)`). Use the setters.

### 3.6 pyte encodes DEC private modes shifted left by five bits

Application cursor key mode is DECSET 1, but pyte records it in `screen.mode`
as `1 << 5 == 32`. There is no named constant for it in `pyte.modes`. Get this
wrong and arrow keys break inside `less`, `vim`, and anything else that sets
DECCKM.

### 3.7 Qt needs system libraries on Linux

A bare `pip install PySide6` is not enough. Qt fails with
`libEGL.so.1: cannot open shared object file` or
`Could not load the Qt platform plugin "xcb"`.

On Debian/Ubuntu/Kali the set is:

```
libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1
libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0
```

**The installer must detect this and tell the user exactly what to run.**
See section 6.6. A silent broken install is the worst possible outcome.

### 3.8 Threading discipline

The PTY is read on a background thread; the pyte screen is only ever touched on
the GUI thread. Text crosses the boundary as a **queued Qt signal**, so the
renderer never locks anything while painting. Keep this arrangement.

### 3.9 The socket connection *is* the mode signal

The widget enters config mode when the settings app connects and leaves it when
the socket drops. This is deliberate: a settings app that **crashes** looks
identical to one that quit cleanly, so the widget can never be stranded in
config mode with a dead partner. Do not replace this with an explicit
"goodbye" message.

### 3.10 Both PTY backends deal in `str`, not bytes

Decoding is the backend's problem, because only it can hold a partial
multi-byte character across reads. `ptyprocess.PtyProcessUnicode` and pywinpty
both already do this. Feed `pyte.Stream` (not `ByteStream`).

### 3.11 Environments that forbid binding a local socket

Sandboxes and some CI images refuse `bind()` on a Unix socket even in a
writable directory. The IPC tests **skip** rather than fail in that case, and
`WidgetServer.start()` accepts an override socket name so tests can isolate
themselves. Preserve both behaviours.

---

## 4. Pass 2 scope

Ordered by priority.

1. **Proper Python packaging** — `pyproject.toml` with console entry points.
   Everything else depends on this. (§5)
2. **An installer** — one `.py` file that does everything. (§6)
3. **Autostart, both platforms** — plus a toggle in the settings app. (§7)
4. **"Open config folder" button** in the settings app. (§8.2)
5. **Desktop integration** — Start Menu / application launcher entries. (§9)
6. **An uninstaller** — generated by the installer, removes everything. (§10)
7. **Windows verification** — the ConPTY backend has never run. (§11)

Everything pass 1 does must keep working. The 70 existing tests must keep
passing.

---

## 5. Packaging

Replace the bare `requirements.txt` with a `pyproject.toml`.

```toml
[project]
name = "terminal-widget"
version = "0.2.0"
requires-python = ">=3.11"
dependencies = [
    "PySide6>=6.6",
    "pyte>=0.8.1",
    "ptyprocess>=0.7.0 ; sys_platform != 'win32'",
    "pywinpty>=2.0.0 ; sys_platform == 'win32'",
]

[project.scripts]
terminal-widget = "terminal_widget.__main__:main"

[project.gui-scripts]
terminal-widget-settings = "terminal_widget.settings.__main__:main"
```

**Why `gui-scripts` matters on Windows:** entry points under `[project.scripts]`
get a console-attached `.exe` launcher, which flashes a black console window.
`[project.gui-scripts]` produces a `pythonw`-backed launcher that does not.
The widget itself should also be a gui-script — a desktop widget must never
pop a console.

Consider making **both** entry points gui-scripts, and keeping a
`terminal-widget-debug` console script for troubleshooting.

Ship the package with an icon asset (see §9.3).

---

## 6. The installer

### 6.1 Goal

The user clones or downloads the project, runs **one command**, and ends up
with: a working install, a Start Menu / application-launcher entry for the
settings app, optionally autostart, and a working uninstaller. No manual venv
creation, no `pip install`, no PATH editing.

```bash
python3 install.py          # Linux
py install.py               # Windows
```

### 6.2 Non-goals — things the installer must NOT do

- **Never require administrator or root.** Everything is per-user. If a step
  would need elevation, the design is wrong.
- Never write outside the user's home directory (except the documented system
  package hint, which it only *prints*).
- Never modify `PATH`, shell profiles, `.bashrc`, or the registry beyond the
  single documented autostart key.
- Never install into the system Python or `--user` site-packages. Use a
  dedicated venv so uninstalling is a directory removal.
- Never touch the user's existing config or shell history.

### 6.3 Install layout

| | Linux | Windows |
| --- | --- | --- |
| App root | `~/.local/share/terminal-widget/` | `%LOCALAPPDATA%\TerminalWidget\` |
| venv | `<app root>/venv/` | `<app root>\venv\` |
| Manifest | `<app root>/install-manifest.json` | same |
| Uninstaller | `<app root>/uninstall.py` | same |
| Launcher entries | `~/.local/share/applications/` | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Terminal Widget\` |
| Autostart | `~/.config/autostart/` | `HKCU\...\CurrentVersion\Run` |
| Icon | `~/.local/share/icons/hicolor/256x256/apps/` | inside app root |
| **Config (untouched)** | `~/.config/terminal_widget/` | `%APPDATA%\terminal_widget\` |

Config deliberately lives outside the app root so that uninstalling and
reinstalling preserves the user's arrangement.

### 6.4 Steps

1. **Preflight.** Check Python ≥ 3.11. Check the platform is supported.
   Refuse early and clearly if not.
2. **Create the venv** at `<app root>/venv`.
3. **Install the project into it** — `pip install .` from the project
   directory, so entry points are generated.
4. **Verify Qt actually loads** (§6.6). This is where Linux installs go wrong.
5. **Write the icon** into place.
6. **Create launcher entries** (§9).
7. **Offer autostart** — prompt, or honour `--autostart` / `--no-autostart`.
8. **Write the manifest** listing every path and registry value created.
9. **Write `uninstall.py`** into the app root.
10. **Print a summary**: what was created, how to launch it, how to remove it.

### 6.5 CLI

```
python install.py [options]

  --prefix PATH        override the app root
  --autostart          enable autostart without prompting
  --no-autostart       skip autostart without prompting
  --no-shortcuts       skip Start Menu / launcher entries
  --quiet              only print errors and the final summary
  --dry-run            print every action, change nothing
  --uninstall          run the uninstaller for an existing install
```

`--dry-run` is not decoration: it is how you test an installer without a
disposable machine.

### 6.6 The Qt smoke test — do not skip this

After installing dependencies, the installer must actually **prove Qt works**
before declaring success:

```python
subprocess.run([venv_python, "-c",
    "import os; os.environ['QT_QPA_PLATFORM']='offscreen';"
    "from PySide6.QtWidgets import QApplication; QApplication([]); print('ok')"],
    capture_output=True, check=True)
```

If this fails with a missing-library error, the installer must detect the
distro family and print the exact command:

| Family | Command |
| --- | --- |
| Debian / Ubuntu / Kali / Mint | `sudo apt install libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0` |
| Fedora / RHEL | `sudo dnf install libglvnd-egl xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxkbcommon-x11` |
| Arch | `sudo pacman -S libglvnd xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxkbcommon-x11` |

Read `/etc/os-release` (`ID` and `ID_LIKE`) to pick. Fall back to listing all
three. **The install must exit non-zero in this case** — a half-installed
widget that crashes on launch is worse than a clear failure.

### 6.7 Idempotency, upgrade, repair

Running the installer twice must be safe. If an install already exists:

- Same version → offer repair (recreate launchers and venv, keep config).
- Different version → upgrade in place; keep config; refresh launchers.
- Always rewrite the manifest rather than appending to a stale one.

Never leave the system in a state where the manifest and reality disagree —
write the manifest **last**, after everything it describes exists.

### 6.8 The manifest

`install-manifest.json` in the app root. It is the uninstaller's only source of
truth, so it must be complete.

```json
{
  "schema": 1,
  "app": "terminal-widget",
  "version": "0.2.0",
  "installed_at": "2026-09-07T21:14:00Z",
  "platform": "linux",
  "python": "/usr/bin/python3.13",
  "app_root": "/home/dano/.local/share/terminal-widget",
  "paths": [
    "/home/dano/.local/share/terminal-widget",
    "/home/dano/.local/share/applications/terminal-widget-settings.desktop",
    "/home/dano/.local/share/icons/hicolor/256x256/apps/terminal-widget.png",
    "/home/dano/.config/autostart/terminal-widget.desktop"
  ],
  "registry": [],
  "preserved": [
    "/home/dano/.config/terminal_widget"
  ]
}
```

On Windows, `registry` carries entries like:

```json
{"root": "HKCU",
 "key": "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
 "value": "TerminalWidget"}
```

`preserved` documents what the uninstaller will **keep** by default.

---

## 7. Autostart

### 7.1 Rules that apply to both platforms

- Autostart launches **the widget**, never the settings app.
- It must use the venv's GUI interpreter so no console window appears.
- **The OS is the source of truth, not `config.json`.** The settings checkbox
  must reflect whether the entry actually exists on disk / in the registry.
  Storing an `autostart: true` flag in config invites the two to disagree.
- Enabling and disabling must both be possible from the settings app *and* the
  installer, and must agree with each other.
- Enabling twice must not create two entries.

### 7.2 Linux — XDG autostart (primary mechanism)

Write `~/.config/autostart/terminal-widget.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Terminal Widget
Comment=Borderless desktop terminal
Exec=/home/dano/.local/share/terminal-widget/venv/bin/terminal-widget
Icon=terminal-widget
Terminal=false
X-GNOME-Autostart-enabled=true
```

Honoured by GNOME, KDE, XFCE, LXQt, Cinnamon and MATE. Create
`~/.config/autostart/` if absent. Disabling = deleting the file. Detecting =
testing for the file.

`Exec` must be an absolute path — autostart runs with a minimal environment and
`PATH` may not include the venv.

**systemd user unit (documented alternative, not the default).** More robust
where available (restart on failure, proper logging) but adds a dependency on
systemd and needs `systemctl --user enable`. Mention it in the README as an
option for users who want it; do not make it the default path.

**WSL has no persistent desktop session.** Autostart is effectively meaningless
there. Detect WSL (`/proc/version` contains `microsoft`, or
`WSL_DISTRO_NAME` is set) and say so rather than silently writing a file that
will never fire.

### 7.3 Windows — the `Run` registry key (primary mechanism)

`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`, a string
value named `TerminalWidget`:

```
"C:\Users\dano\AppData\Local\TerminalWidget\venv\Scripts\terminal-widget.exe"
```

Quote the path — it contains spaces on most machines. Use the `winreg` module.
No administrator rights required; `HKCU` is per-user by definition.

- Enable: `winreg.SetValueEx(...)`
- Disable: `winreg.DeleteValue(...)`, tolerating `FileNotFoundError`
- Detect: `winreg.QueryValueEx(...)` in a try/except

**Alternative: a `.lnk` in the Startup folder**
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). More discoverable
for users — it shows up in a folder they can open — but requires shortcut
creation. The registry key is simpler and equally per-user. Pick the registry
key; mention the alternative in the docs.

**Do not use Task Scheduler.** It is more capable but far more surface area,
and it is what actual malware uses, which makes antivirus software nervous.

### 7.4 Module shape

Put this behind one platform-agnostic interface, mirroring how `pty/` is
organised:

```
terminal_widget/autostart/
├── __init__.py     # enable() / disable() / is_enabled() -> dispatch
├── base.py         # the interface
├── xdg.py          # Linux
└── windows.py      # Windows
```

Three functions is the whole API:

```python
def is_enabled() -> bool: ...
def enable(executable: Path) -> None: ...
def disable() -> None: ...
```

Everything must be safe to call when the entry is already in the desired state.

---

## 8. Settings app additions

### 8.1 Autostart toggle

A checkbox in the **Behaviour** group, below the existing history option:

```
Behaviour
  History      [x] Keep shell history separate
  Startup      [x] Start automatically on login
```

- Initial state comes from `autostart.is_enabled()`, **not** from config.
- Toggling calls `enable()` / `disable()` immediately — no Save required. It is
  an OS-level action, not a setting, so deferring it would be a lie.
- If the call fails (permissions, missing directory), revert the checkbox and
  show the reason in the status line. Never leave the checkbox showing a state
  that is not real.
- On WSL, disable the checkbox and explain why (§7.2).
- If the widget was launched from a source checkout rather than an install,
  there is no stable executable path to register. Detect this and disable the
  checkbox with an explanatory tooltip.

### 8.2 "Open config folder" button

A button that opens the configuration directory in the system file manager.

| Platform | Call |
| --- | --- |
| Windows | `os.startfile(path)` |
| Linux | `subprocess.Popen(["xdg-open", str(path)])` |
| macOS | `subprocess.Popen(["open", str(path)])` |

Prefer `QDesktopServices.openUrl(QUrl.fromLocalFile(path))` — it is already
available through Qt and handles all three. Fall back to the table above if it
returns `False`.

Create the directory first if it does not exist, or the call silently does
nothing on a fresh install.

Place it next to the existing Save/Close buttons, or in a small "Config file"
row showing the path with an "Open folder" button beside it. Showing the path
is worth doing — users ask where settings live.

### 8.3 While you are in there

Two small things pass 1 left rough:

- The **font dropdown** resolves `"Monospace"` to a concrete family
  (`"DejaVu Sans Mono"`) and then saves that. Harmless, but decide deliberately
  whether to store the generic or resolved name.
- The **colour buttons** open a dialog but there is no preview of the actual
  terminal. A small live sample row would help.

---

## 9. Desktop integration

### 9.1 Linux

`~/.local/share/applications/terminal-widget-settings.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Terminal Widget Settings
Comment=Configure the terminal widget
Exec=/home/dano/.local/share/terminal-widget/venv/bin/terminal-widget-settings
Icon=terminal-widget
Terminal=false
Categories=Utility;Settings;
```

Optionally a second entry for the widget itself. Run
`update-desktop-database ~/.local/share/applications` if present — it may not
be (it was absent on the pass 1 development machine), so treat it as optional.

### 9.2 Windows

Create `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Terminal Widget\`
containing `Terminal Widget Settings.lnk` and optionally `Terminal Widget.lnk`.

Creating a `.lnk` without adding a dependency — drive PowerShell:

```python
ps = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
    "$s.TargetPath = '{target}';"
    "$s.Arguments = '{args}';"
    "$s.WorkingDirectory = '{workdir}';"
    "$s.IconLocation = '{icon}';"
    "$s.Save()"
)
subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                "-Command", ps], check=True)
```

Escape single quotes in paths by doubling them. If `pywin32` happens to be
available, `win32com.client` is cleaner — but do not add the dependency just
for this.

### 9.3 Icon

The project needs one. A 256×256 PNG is the minimum; an SVG plus rendered PNGs
at 16/32/48/128/256 is better for Linux icon themes, and Windows wants an
`.ico` for shortcuts. Keep the source under `terminal_widget/assets/`.

---

## 10. The uninstaller

`uninstall.py`, written into the app root by the installer, runnable directly:

```bash
python ~/.local/share/terminal-widget/uninstall.py
```

Also reachable as `python install.py --uninstall`.

### Behaviour

1. Read the manifest. If it is missing, say so and refuse to guess — never
   delete paths inferred from defaults.
2. Show what will be removed and what will be kept, then confirm (unless
   `--yes`).
3. Remove autostart **first**, so a failure later cannot leave a login entry
   pointing at a deleted interpreter.
4. Remove launcher entries and the icon.
5. Remove registry values (Windows).
6. Remove the app root, including the venv.
7. Offer to remove config and shell history — **default no**, `--purge` to
   include them.
8. Delete itself last, or note that the app root removal takes it with it. On
   Windows a running script cannot delete its own directory; copy to a temp
   location and re-exec, or instruct the user to remove the last folder.

### Flags

```
  --yes       do not prompt
  --purge     also remove config and shell history
  --dry-run   print what would be removed, change nothing
```

Removing a path that is already gone is success, not an error.

---

## 11. Windows verification

The ConPTY backend has never executed. Before pass 2 can be called done, on a
real Windows machine:

- [ ] `cmd.exe` spawns, renders, and accepts input
- [ ] PowerShell spawns and renders (it emits far more escape sequences)
- [ ] WSL spawns through `wsl.exe`
- [ ] Resizing propagates to the shell (`mode con` / `$Host.UI` reports the
      right size)
- [ ] Ctrl+C interrupts rather than typing a character
- [ ] The shell exiting is detected and handled
- [ ] UTF-8 and box-drawing render correctly
- [ ] No console window flashes on launch (this is why gui-scripts matter)
- [ ] Frameless drag and resize work under the Windows WM
- [ ] Background-only opacity works (needs a translucent-capable window)
- [ ] Geometry persists without drift (§3.1 — check whether Windows reports a
      frame offset for a frameless window)

Added in pass 3, and unverified for the same reason:

- [ ] The settings app is readable in Windows dark mode (this is what pass 3
      fixed; the fix is derived from the live palette, so confirm it against the
      real Windows palette rather than a synthetic one)
- [ ] 0% background opacity leaves the widget clickable off the glyphs. The
      backdrop is painted at alpha 1 rather than 0 precisely because a fully
      transparent pixel of a *layered* window is click-through on Windows —
      confirm whether Qt 6 actually takes that path, and drop the floor in
      `TerminalView.backdrop_alpha` if it does not
- [ ] `cwd` reaches the shell through ConPTY. The signature is confirmed
      against the pywinpty 3.0.5 wheel -- `spawn(cls, argv, cwd=None,
      env=None, dimensions=(24, 80))` -- so only the live behaviour is open
- [ ] Wheel scrolling and drag-selection behave under the Windows WM
- [ ] Ctrl+C copies with a selection and interrupts without one

Expect `pty/windows.py` to need real changes. Budget for it.

---

## 12. Testing requirements

Pass 1's 70 tests must keep passing. Add:

**Installer** — the highest-value new tests, because the installer is what a
stranger runs first:

- Install into a temp prefix; assert every manifest path exists.
- Uninstall; assert every manifest path is gone and `preserved` paths remain.
- Install twice; assert idempotency and no duplicate entries.
- `--dry-run` creates nothing at all.
- A corrupt or missing manifest makes the uninstaller refuse, not guess.

**Autostart** — against a temp `XDG_CONFIG_HOME` on Linux and a temp registry
key or a mocked `winreg` on Windows:

- `enable()` → `is_enabled()` is True; the entry has an absolute path.
- `enable()` twice → exactly one entry.
- `disable()` when not enabled → no error.
- `is_enabled()` reflects a file/value removed behind its back.

**Settings app** — the autostart checkbox reflects OS truth on open, and a
failed toggle reverts the checkbox.

Keep everything on Qt's `offscreen` platform so the suite needs no display, and
keep the graceful skip for environments that forbid binding a local socket
(§3.11).

---

## 13. Acceptance criteria

Pass 2 is done when, on a **clean machine of each platform**:

1. `python install.py` completes without errors and without elevation.
2. The settings app appears in the Start Menu / application launcher.
3. Launching the settings app puts a running widget into config mode.
4. Dragging the widget updates the settings app's numbers live, and typing
   numbers moves the widget live.
5. Closing the settings app leaves a fixed, borderless, still-typeable terminal.
6. Ticking "Start automatically on login", rebooting, and logging back in
   produces the widget at the saved geometry with **no console window**.
7. Unticking it and rebooting produces no widget.
8. "Open config folder" opens the right directory in the file manager.
9. Geometry is identical across five launches — no drift (§3.1).
10. Shell history does not touch the login shell's history file.
11. `python uninstall.py` removes everything and leaves config intact;
    `--purge` removes that too.
12. The full test suite passes on both platforms.

---

## 14. Open risks and unresolved questions

Ranked by how much they could hurt.

1. **Wayland cannot set absolute window positions.** This is load-bearing: the
   entire premise is a window that stays at fixed coordinates. Options are an
   X11-only path with a documented limitation, a compositor-specific protocol
   (`wlr-layer-shell` covers wlroots compositors but not GNOME), or accepting
   that the Linux target is X11. **Decide this early — it may change what the
   Linux product is.**
2. **Windows is entirely unverified.** (§11)
3. **Single instance.** Autostart plus a manual launch will produce two
   widgets. The IPC server already fails to listen when the socket is taken —
   use that as the guard: if `start()` fails because the socket is in use,
   exit rather than running a second, unreachable widget.
4. **Window stacking.** ~~Should the widget sit below normal windows (a true
   desktop widget), above them, or in the normal stack? Pinning to the desktop
   layer has no supported API on Windows. Currently unaddressed.~~
   *Answered in pass 4:* the user chooses — a `stacking` setting with three
   values, defaulting to `desktop` (`Qt::WindowStaysOnBottomHint`). What made
   this load-bearing was the window type: it is a `Qt::Tool` now, so it takes no
   taskbar button and no Alt+Tab slot, and a covered widget would otherwise have
   had no way back. A tray icon and the settings app are the two ways back, and
   config mode lifts the widget into the normal order for as long as settings is
   open. Not `WorkerW` reparenting — no supported API, and it has to be redone
   every time Explorer restarts — so the widget is below other windows rather
   than behind the desktop icons. Still open underneath it: on Windows the
   below-hint is applied once when the native window is created rather than held
   by the shell, so clicking the widget raises it until you click elsewhere; and
   Win+D still likely minimises a tool window.
5. **Shell exit.** The widget closes when the shell exits. For something meant
   to live on the desktop permanently, respawning may be better. Make it a
   setting or make a deliberate choice. *Note from pass 4:* `Qt::Tool` clears
   `WA_QuitOnClose`, so this is now explicit wiring (`WidgetWindow.closed` →
   `app.quit`) rather than a Qt default — which makes respawning instead a
   smaller change than it was.
6. **Scrollback.** ~~There is none, and no chrome to put a scrollbar in.~~
   *Answered in pass 3:* the wheel scrolls, with no scrollbar and no chrome, plus
   Shift+PageUp/Home for the keyboard. Not `pyte.HistoryScreen` -- it wraps every
   stream event through `__getattribute__`, which measured 2.6x slower than a
   plain `Screen` on the same input, and its paging API mutates the live buffer.
   `screen.py` subclasses `pyte.Screen` instead and keeps retired lines in one
   absolute row space, which the selection is anchored to as well. Still open
   underneath it: no reflow on a width change, and growing the window does not
   pull lines back out of history.
7. **HiDPI and fractional scaling.** Untested. Cell metrics are computed from
   `QFontMetricsF`, which should scale, but "should" is doing work there.
8. **Resize granularity.** Free-pixel resizing leaves partial cells at the
   edges; snapping to whole cells fights the OS resize handles.
9. **Multi-monitor.** Saved coordinates may land off-screen if a monitor is
   disconnected. Consider clamping to the available desktop on startup — but
   note that clamping is itself a geometry change and must not be persisted
   (§3.2).

---

## Appendix A — platform path reference

| Purpose | Linux | Windows |
| --- | --- | --- |
| Config | `$XDG_CONFIG_HOME/terminal_widget` or `~/.config/terminal_widget` | `%APPDATA%\terminal_widget` |
| Shell history | `<config>/shell_history` | `<config>\shell_history` |
| IPC socket | Unix domain socket, `terminal_widget-<user>` | named pipe, same name |
| App install | `~/.local/share/terminal-widget` | `%LOCALAPPDATA%\TerminalWidget` |
| Autostart | `~/.config/autostart/*.desktop` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Launcher entry | `~/.local/share/applications/*.desktop` | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` |
| Icons | `~/.local/share/icons/hicolor/<size>/apps/` | alongside the app |

## Appendix B — detecting the environment

```python
import os, sys, platform
from pathlib import Path

def is_windows() -> bool:
    return sys.platform == "win32"

def is_wsl() -> bool:
    """WSL has no persistent desktop session; autostart is meaningless there."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False

def distro_family() -> str:
    """'debian' | 'fedora' | 'arch' | 'unknown' -- for the system-package hint."""
    try:
        fields = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
    except OSError:
        return "unknown"
    ids = f"{fields.get('ID', '')} {fields.get('ID_LIKE', '')}".strip('"').lower()
    for family in ("debian", "fedora", "rhel", "arch", "suse"):
        if family in ids:
            return "fedora" if family == "rhel" else family
    return "unknown"
```

## Appendix C — things that are already decided

Do not spend time reopening these. They were settled in pass 1 with reasons.

| Decision | Reason |
| --- | --- |
| Emulate the terminal (pyte) rather than reparent a real emulator | XEmbed/`SetParent` breaks on Wayland, varies per desktop, and gives no control over appearance |
| Settings-app-open **is** config mode; no toggle | One fact, one state, nothing to get out of sync |
| Config mode loses mouse text selection | Drag-anywhere and drag-to-select are the same gesture with no title bar to disambiguate; config mode is brief |
| Changing the shell restarts the session | It means killing one process and starting another |
| Opacity has two modes (background-only / whole window) | Background-only keeps text readable; whole-window is what people expect from "opacity" |
| Separate shell history, on by default | A widget should not rewrite the history of the terminal you actually work in |
| Geometry in client coordinates | §3.1 |
| Both PTY backends return `str` | §3.10 |
