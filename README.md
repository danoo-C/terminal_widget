# Terminal Widget

A terminal that lives on your desktop instead of on your taskbar.

One borderless window, fixed where you put it, running one shell. No title bar,
no tabs, no menus, no close button — the way a clock or a system monitor sits on
the desktop rather than being an application you switch to.

Runs on **Linux** and **Windows**. Python 3.11+.

> **Status:** working and installable on Linux, with 279 tests covering it. The
> Windows code paths are written but have never been run — see `issues.md` for
> what still needs checking there.

---

## What it is

Two programs, installed together:

| | |
| --- | --- |
| **Widget** | The borderless window. Runs a shell in a pseudo-terminal, draws its output, takes your keystrokes. Never asks you anything. |
| **Settings app** | An ordinary window that edits the configuration. Opening it is also what unlocks the widget for moving and resizing. |

There is no settings menu on the widget itself, because there is nowhere to put
one. **Open the settings app and the widget becomes draggable and resizable;
close it and the widget locks back into place.** That is the whole interaction
model — no mode toggle, no hotkey, no right-click menu.

Because the widget has no taskbar button and no Alt+Tab entry, a **tray icon** is
how you reach it: show it, open settings, or quit.

## What it does

**The shell.** Pick from the shells found on your system, or give it any command
line you like — `wsl -d kali-linux -- zsh -c "fastfetch; exec zsh"` works, quoting
and all. Optionally start it in a chosen directory, and keep its history in its
own file so a desktop scratch terminal does not rewrite the history of the
terminal you actually work in.

**Appearance.** Font family and size, foreground and background colours, and
opacity from 0–100% — either fading the whole window or just the backdrop, which
leaves the text crisp on your wallpaper.

**Position and layering.** Exact pixel geometry, remembered between sessions.
Sits behind your other windows by default, or in the normal window order, or
always on top.

**Scrollback.** Up to 50,000 lines, scrolled with the wheel or
Shift+PageUp/PageDown/Home/End. Full-screen programs like `vim` and `htop` do not
pour their redraws into it. Optionally, jump to the top of the shell's startup
output once it finishes printing, so a banner taller than the window reads from
its first line.

**Selection and clipboard.** Drag to select; double-click for a word, triple for a
line. `Ctrl+C` copies when there is a selection and sends an interrupt when there
is not, so it can never kill a running program by surprise. `Ctrl+Shift+C` always
copies. `Ctrl+V`, `Ctrl+Shift+V`, `Shift+Insert` and right-click paste, bracketed
when the shell asks for it.

**Starting up.** Optional autostart on login. One widget runs per config file, so
launching a second one brings the first to the front rather than starting a rival
to it — two widgets sharing a config would overwrite each other's settings.

---

## Installing

Nothing needs administrator rights, and nothing is installed outside your home
directory.

```bash
git clone https://github.com/danoo-C/terminal_widget.git
cd terminal_widget
python3 install.py          # py install.py on Windows
```

That opens a graphical installer, which creates a private Python environment,
installs both programs into it, adds a Start Menu or application-launcher entry,
optionally enables autostart, and writes an uninstaller.

The installer uses tkinter. It ships with the official Windows Python; on Linux
it is usually one package away (`sudo apt install python3-tk`), and without it
the installer falls back to a text version and tells you the command.

### Options

```
python3 install.py --cli          text installer instead of the GUI
python3 install.py --dry-run      print every action, change nothing
python3 install.py --yes          unattended, no prompts
python3 install.py --prefix PATH  install somewhere other than the default
python3 install.py --uninstall    remove it again
```

### Where things go

| | Linux | Windows |
| --- | --- | --- |
| Program | `~/.local/share/terminal-widget/` | `%LOCALAPPDATA%\TerminalWidget\` |
| Shortcuts | `~/.local/share/applications/` | Start Menu |
| Autostart | `~/.config/autostart/` | `HKCU\…\CurrentVersion\Run` |
| Settings | `~/.config/terminal_widget/` | `%APPDATA%\terminal_widget\` |

Your settings live outside the program directory on purpose, so reinstalling
keeps the widget exactly where you put it.

### Linux system packages

Qt needs a few X11 libraries that are not always present:

```bash
sudo apt install libegl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 \
    libxkbcommon-x11-0
```

The installer checks for these and stops with the right command for your
distribution rather than leaving you an install that crashes on launch. Windows
needs no equivalent step.

### Removing it

```bash
python3 ~/.local/share/terminal-widget/uninstall.py
```

Removes only what the install recorded. Your settings are kept unless you add
`--purge`.

---

## Using it

After installing, launch **Terminal Widget Settings** from your Start Menu or
application launcher — or run either program directly:

```bash
~/.local/share/terminal-widget/venv/bin/terminal-widget
~/.local/share/terminal-widget/venv/bin/terminal-widget-settings
```

The settings app can start the widget itself. Its button reads **Launch widget**
when none is running and **Restart widget** when one is; restarting is how the
settings that are only read at startup — the shell, its working directory, the
startup scroll — take effect.

Run `terminal-widget-debug` instead if something goes wrong and you want to see
why: it is the same program with a console attached.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"

python -m pytest                 # offscreen; no display needed
pyright
```

[`issues.md`](issues.md) records the bugs found in real use, with what caused
each one and what the fix was; [`megapromt/PASS_2.md`](megapromt/PASS_2.md) is
the design brief behind the unusual choices.

## License

MIT. See [LICENSE](LICENSE).
