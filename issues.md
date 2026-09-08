# Known issues

Defects found in real use, with what is known about each. Design limitations
that are working as intended live in the README instead; open questions that
were never decided live in section 14 of `megapromt/PASS_2.md`.

---

## 1. The widget has a taskbar button and appears in Alt+Tab

**Status:** fixed in code, unverified on Windows. Found on Windows,
2026-09-08; fixed 2026-09-08.

### What happens

The widget shows up on the Windows taskbar with its icon, and Alt+Tab lists it
as though it were an application you switch between. Alt+Tab can also raise it
over the windows you were actually working in.

### Why it matters

It contradicts the premise the whole project is built on. `README.md:22-23`
opens with it:

> Most terminal emulators are applications you *switch to*. This one is meant to
> be *part of the desktop*.

and the brief says the same at `megapromt/PASS_2.md:37-38` — "a panel that
happens to be a terminal — rather than an application you switch to."

Alt+Tab is the literal act of switching to an application. A desktop widget that
takes a slot in the switcher is competing with real windows for attention it
should never ask for — and next to Rainmeter widgets, none of which do this, it
is the one thing that gives it away as a program rather than part of the
wallpaper.

### Cause

`terminal_widget/window.py:85`:

```python
self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
```

`Qt::Window` means exactly "a normal top-level application window", and Qt maps
it to a plain Windows top-level window. Taskbar presence and Alt+Tab membership
follow from that, automatically and by design. `FramelessWindowHint` removes the
*decorations* only; it says nothing about how the shell should classify the
window. Nothing else in the code contributes — this is a one-line cause.

### Candidate fix

Swap the base type for `Qt::Tool`:

```python
self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
```

Qt documents `Qt::Tool` as producing a window that "will not appear in the
taskbar and does not appear in the list of windows when the user presses
Alt+Tab", by giving it the `WS_EX_TOOLWINDOW` extended style on Windows.

Checked here, so far as this machine allows:

- `Qt::Tool` is `0x0b` — `Window | Dialog | 0x08`. It does **not** imply
  `WindowStaysOnTopHint`, so it will not start floating over other windows.
- The stay-above-parent behaviour tool windows have applies to *parented* ones.
  `WidgetWindow` is constructed with `super().__init__(None)`, so it does not
  apply.
- Run under WSLg with the flag swapped, the widget still shows, still takes
  keyboard focus and delivers typing to the shell, still drags in config mode
  via the manual fallback, and still keeps `WA_TranslucentBackground`. Nothing
  observable here regresses.

The settings app is a separate process and a normal window. It *should* keep its
taskbar button; leave it alone.

### The catch, and why this was not a one-line change

Removing the widget from the taskbar and from Alt+Tab also removes both ways of
getting it back. Today it sits in the normal window stack, so anything you open
can cover it — and with no taskbar button, no switcher entry, no tray icon and
no global hotkey, a covered widget would be genuinely unreachable until
something moved.

So the fix was really two decisions, and only the first one was easy:

1. Stop being an application window — `Qt::Tool`, above.
2. Decide where it lives in the stack. This was open question 4 in the brief,
   and it is what makes (1) safe.

It turned out to be three. See below.

---

### What landed

**1. `Qt::Tool`.** As proposed. `window.py` now builds its flags from
`BASE_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint`.

**2. Stacking is a setting.** A `stacking` field in `Config` with three values —
`desktop` (`Qt::WindowStaysOnBottomHint`, the default), `normal`, `top` — and a
**Window layer** dropdown in the settings app's Behaviour group. Not `WorkerW`
reparenting: no supported API, and it has to be redone every time Explorer
restarts. The widget therefore sits *below other windows*, not *behind the
desktop icons*.

Config mode overrides it. `_stacking_hint()` returns no hint at all while the
settings app is connected, so the widget joins the normal window order for as
long as you are arranging it and drops back the moment settings closes. A
stacking change typed while settings is open is stored and applied on the way
out — the same bargain the shell and working-directory fields already make.

**3. A tray icon** (`terminal_widget/tray.py`), with Show / Settings / Quit.
Deliberately not optional: it is the only way back to a covered widget and the
only way to quit one with no close button. Where there is no tray it constructs
nothing, warns on stderr and the widget runs anyway.

### Two Qt traps this uncovered

**`Qt::Tool` clears `WA_QuitOnClose`.** `QWidgetPrivate::adjustQuitOnCloseAttribute()`
clears it for any top-level whose type is not Widget/Window/Dialog. Measured
here: `Window` → True, `Tool` → False, and `close()` on a tool window does *not*
end `app.exec()`. Setting the attribute back does not stick either — Qt
re-clears it on every flags change. So `_on_session_ended`'s `self.close()` was
quitting the process only as a side effect of the window *type*, and the flag
swap alone would have left a headless process running every time the shell
exited. Fixed with an explicit `WidgetWindow.closed` signal wired to `app.quit`,
plus `setQuitOnLastWindowClosed(False)` so the intent is stated rather than
inherited. Verified end to end: a widget whose shell exits now exits with 0
instead of hanging.

**`WindowStaysOnBottomHint` is one-shot on Windows, and it disables `raise_()`.**
In `qtbase/src/plugins/platforms/windows/qwindowswindow.cpp`, the hint becomes a
single `SetWindowPos(hwnd, HWND_BOTTOM, …)` when the native window is created —
Windows has no persistent stay-below state — and `QWindowsBaseWindow::raise_sys()`
deliberately skips the raise while the hint is set. So clicking the widget will
bring it forward until you click elsewhere, and `bring_to_front()` leans on
`activateWindow()` rather than `raise_()`. X11 uses `_NET_WM_STATE_BELOW`, which
the WM does hold, so the two platforms genuinely differ.

### The ctypes fast path, and why it was rejected

A Windows-only `SetWindowPos(HWND_BOTTOM/HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)`
would avoid the hide-and-recreate that a flags change costs. It was not taken:
it is the same call Qt already makes, so it buys nothing for the below-layer;
leaving topmost via `HWND_NOTOPMOST` would desynchronise the real extended style
from Qt's `windowFlags()`, giving one fact two sources of truth; a restack
happens exactly twice per settings session; and nothing in the offscreen suite
could exercise it, on the one platform that has never been run.

The one thing native code *would* buy is different and larger: re-issuing
`SetWindowPos(HWND_BOTTOM)` on `WM_ACTIVATE`, to make the desktop layer
persistent rather than one-shot. Worth considering only if the checklist below
says the one-shot behaviour is not good enough.

### What was verified here

Under WSLg, against a real window manager:

- The widget starts, shows, and takes keyboard input.
- Entering config mode restacks it and reports geometry `(250, 260, 520, 300)`
  for a widget configured at exactly that — no frame offset, no drift.
- Geometry is byte-identical after a restack and a full config-mode cycle.
- A stacking change sent during config mode is stored, not applied, and saved.
- A widget whose shell exits exits the process.
- No tray under WSLg → the warning fires and everything else still works.

230 tests pass, pyright is clean.

### To verify on Windows

Nothing below can be checked from WSL; the taskbar and Alt+Tab are Windows shell
behaviours, and Qt's `render()`/`grab()` cannot even capture the compositing.

- [ ] No taskbar button and no Alt+Tab entry
- [ ] Clicking the widget still focuses it and typing still reaches the shell
- [ ] Config mode still drags and resizes under the Windows WM
- [ ] Background-only opacity still composites (tool windows are still layered)
- [ ] **Win+D / Show desktop** — does it hide the widget? Rainmeter survives
      this by living on the desktop layer; a tool window may not
- [ ] On `desktop`, the widget starts behind other windows
- [ ] Whether clicking it raises it, and whether clicking elsewhere puts it
      back. Qt's source says it will raise — decide whether one-shot is good
      enough or whether the `WM_ACTIVATE` re-sink above is wanted
- [ ] On `top`, it stays above normal windows
- [ ] Opening settings lifts it into view; closing puts it back; the restack
      neither moves nor resizes it, and does not flicker badly enough to
      reopen the ctypes question
- [ ] The shell survives the restack
- [ ] The tray icon appears, with the 16px `.ico` face rather than a scaled 256
- [ ] Tray → Show works from the desktop layer and from minimised
- [ ] Tray → Settings starts one settings app, and does nothing when one is
      already open
- [ ] Tray → Quit exits leaving no orphaned shell and no stray `conhost.exe`
- [ ] Exiting the shell still ends the *process*, not just the window

---

## 2. A custom command containing quotes is mangled before it reaches the shell

**Status:** fixed in code, unverified on Windows. Found on Windows,
2026-09-08; diagnosed and fixed 2026-09-08.

### What happens

With the shell dropdown on *Custom* and the command set to

```
wsl -d kali-linux -- zsh -c "fastfetch; exec zsh"
```

the widget fails outright: it flashes and disappears, or never appears at all.
Nothing is written anywhere, because the shipped launcher is a `gui-scripts`
entry point (`pyproject.toml:35-37`) and therefore `pythonw`-backed, so there is
no console and stderr goes nowhere. The command works when typed into Windows
Terminal, which is what makes it look like the widget simply cannot run WSL.

It can. What actually runs inside the distro is this:

```
zsh:1: command not found: fastfetch; exec zsh
```

zsh exits 127 immediately, the PTY reader hits EOF, `TerminalSession` emits
`ended`, and `WidgetWindow._on_session_ended` closes the window
(`window.py:147-150`), which quits the app. The whole life of the widget is a
few milliseconds — long enough to print an error onto a screen that is never
painted.

### Why it matters

Quoting is not an edge case in this field, it is the *point* of it. Every
interesting custom command has one multi-word argument in it: `zsh -c "..."`,
`cmd.exe /c "..."`, `wsl -- bash -lc "..."`, or an interpreter living under
`C:\Program Files\...`. The one shape that works today is the shape in the
placeholder text — `e.g. wsl.exe -d Ubuntu` (`settings/__main__.py:244`) — and
nothing tells the user that the field stops there.

It is also Windows-only. The POSIX branch of `resolve_shell` uses
`shlex.split(..., posix=True)` and is correct, so no amount of testing under WSL
would ever have shown this — the same trap as issue 1, from the same direction.

### Cause

Four hops, each defensible on its own, that together take one layer of quoting
and turn it from syntax into data.

**Hop 1 — `terminal_widget/config.py:249-254`.** The custom command is handed on
as a single unparsed string:

```python
if sys.platform == "win32":
    # Windows command lines don't follow POSIX quoting; pass through.
    return [cfg.custom_command.strip()]
```

The reasoning in that comment is right: POSIX splitting would eat the
backslashes out of `C:\Users\dano`. But "pass through" only holds if whatever
receives the string parses it with Windows rules. Nothing downstream does.

**Hop 2 — `terminal_widget/pty/windows.py:34`.** A one-element argv is
unwrapped straight back into a command string, and pywinpty is given a `str`:

```python
command = argv[0] if len(argv) == 1 else " ".join(argv)
self._proc = PtyProcess.spawn(command, cwd=cwd, dimensions=(rows, cols), env=env)
```

**Hop 3 — pywinpty's own `PtyProcess.spawn` (`winpty/ptyprocess.py`, 2.x).**
Given a string, it does this:

```python
if isinstance(argv, str):
    argv = shlex.split(argv, posix=False)
...
command = which(_argv[0], path=env.get('PATH', os.defpath))
cmdline = ' ' + subprocess.list2cmdline(_argv[1:])
```

`shlex.split(s, posix=False)` is a **tokenizer**, not a command-line parser. It
finds the argument boundaries correctly but deliberately *keeps the quote
characters inside the token*, on the assumption that the caller will hand the
token to something that parses quotes a second time. `subprocess.list2cmdline`
assumes the exact opposite — that its input is already-parsed argv, and that any
`"` in it is a literal character the child must receive. So it escapes them.

The transformation, reproduced verbatim:

| stage | value |
| --- | --- |
| user types | `wsl -d kali-linux -- zsh -c "fastfetch; exec zsh"` |
| `shlex.split(posix=False)` | `['wsl', '-d', 'kali-linux', '--', 'zsh', '-c', '"fastfetch; exec zsh"']` |
| `list2cmdline(argv[1:])` | `-d kali-linux -- zsh -c "\"fastfetch; exec zsh\""` |
| what `zsh -c` receives | `"fastfetch; exec zsh"` — quotes included, as data |

**Hop 4 — winpty-rs, `src/pty/conpty/pty_impl.rs:484-508`.** The resolved
executable path and that command line are concatenated and passed to
`CreateProcessW` with `lpApplicationName` set to `NULL`:

```rust
let mut cmdline_oss_buf: Vec<u16> = cmdline_oss.encode_wide().collect();  // appname
...
cmdline_oss_buf.push(0x0020);                                             // a space
cmdline_oss_buf.extend(cmd_buf);                                          // the cmdline
```

`CommandLineToArgvW` in the child then unescapes `\"` back to a literal `"`, so
zsh is asked to run a command whose *name* is `fastfetch; exec zsh`. There is no
such command, and there was never a shell in the chain that would have split it
on the semicolon.

Note that the preset shells escape this only by luck: none of `cmd.exe`,
`powershell.exe -NoLogo`, `pwsh.exe -NoLogo` or `wsl.exe` contains a quote or a
space, so the round trip through hops 2-4 happens to be an identity.

### Candidate fix

Parse the command line once, with the rules the child is going to parse it by,
and never re-serialise it ourselves. Two edits.

**1. `terminal_widget/config.py` — split with Windows rules.**

```python
def split_windows_command_line(line: str) -> list[str]:
    """Split a Windows command line the way ``CommandLineToArgvW`` does.

    Not the same thing as ``shlex``: backslashes are ordinary path
    separators unless they precede a quote, and the quotes themselves are
    syntax, not part of the argument.
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
                # 2n+1: n backslashes, quote is literal.
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
```

and in `resolve_shell`:

```python
if sys.platform == "win32":
    # Parsed with Windows rules, not POSIX ones, and parsed *here* so that
    # nothing downstream has to guess whether it is looking at a command
    # line or at argv.
    return split_windows_command_line(cfg.custom_command.strip())
```

**2. `terminal_widget/pty/windows.py` — hand pywinpty the list.**

```python
# A list, never a joined string: given a str, pywinpty re-splits it with
# shlex, which keeps the quote characters inside the tokens and then
# escapes them into the child's argv as literal quotes.
self._proc = PtyProcess.spawn(
    list(argv), cwd=cwd, dimensions=(rows, cols), env=env
)
```

That is enough. pywinpty skips `shlex` entirely for a list, and
`list2cmdline` is the exact inverse of the parser above — the argv the child
reconstructs is the argv we passed in.

Two alternatives, both rejected:

- **`shlex.split(cmd, posix=True)`** — strips the quotes correctly but treats
  backslash as an escape character, so `C:\Users\dano` arrives as `C:Usersdano`.
  This is precisely what the existing comment was right to avoid.
- **`ctypes.windll.shell32.CommandLineToArgvW`** — exact by construction, since
  it *is* the function the child calls. Rejected because it only exists on
  Windows, which puts it in the same place issue 1 put the ctypes restack: no
  test can touch it from this machine. The parser above is seventy-odd lines,
  comments included, and pytest can cover every documented case of it under
  Linux.

### A second defect this uncovered, in pywinpty rather than here

`cmdline = ' ' + subprocess.list2cmdline(_argv[1:])` starts at `_argv[1:]`, and
winpty-rs then prepends the resolved executable path **unquoted**. So a custom
command naming an interpreter under a path with a space in it:

```
"C:\Program Files\Git\bin\bash.exe" -l
```

becomes the command line `C:\Program Files\Git\bin\bash.exe -l`, and with
`lpApplicationName` NULL, `CreateProcessW` falls back to its ambiguous-path
search: `C:\Program.exe`, then `C:\Program Files\Git\bin\bash.exe`. It usually
still starts, with a mangled `argv[0]` — and it would start `C:\Program.exe`
first if such a file existed, which is the classic unquoted-path hijack.

Nothing in the pywinpty API lets us avoid this: the path comes from its own
`which()` call and is always emitted bare. It is worth knowing about, worth
avoiding by pointing custom commands at paths without spaces, and worth an
upstream issue. It does not block the fix above.

### A third one: the failure had no way to be seen

`window.start_session()` is called bare at `__main__.py:49`, and
`TerminalSession.start` does not guard the spawn. So the two failure modes are:

- the command cannot be found at all → `FileNotFoundError` out of pywinpty →
  traceback to a stderr that `pythonw` discards → nothing happens, at all;
- the command starts and dies at once → `ended` → `close()` → `app.quit()`,
  which is correct behaviour for a shell you exited, and useless behaviour for
  a shell that never started.

The second is why the only available bug report was "it completely failed".
Two small changes would have made this self-diagnosing:

- catch the spawn exception in `start_session`, feed the message into the
  screen as text, and leave the window up;
- in `_on_session_ended`, if the shell lived for less than a second, hold the
  window open with the exit status instead of closing — the widget's premise
  is that the window follows the shell, and a shell that never ran has no
  claim on it.

Worth doing regardless of whether the quoting fix lands, since it covers every
future bad custom command as well as this one.

### Workarounds that need no code change

All three are verified to survive hops 1-4 unchanged, because `list2cmdline`
only adds quotes to an argument that contains a space or a tab.

1. **Drop the quotes and the space.** `list2cmdline` leaves a space-free token
   alone, so this reaches zsh intact:

   ```
   wsl -d kali-linux -- zsh -c fastfetch;zsh
   ```

   The inner `zsh` has no arguments and inherits the PTY, so it starts fully
   interactive and reads `~/.zshrc` as usual. The cost is one extra process:
   `exec` cannot be used, because `exec zsh` needs the space that started all
   of this.

2. **Move the payload into the distro**, which is the cleanest of the three.
   Put a `#!/bin/zsh` script at `/usr/local/bin/widget-shell` containing
   `fastfetch` and `exec zsh`, `chmod +x` it, and set the custom command to:

   ```
   wsl -d kali-linux -- /usr/local/bin/widget-shell
   ```

3. **Let the shell's own rc file do it.** `fastfetch` in `~/.zshrc` and a custom
   command of `wsl -d kali-linux -- zsh` needs no quoting anywhere. Guard it
   with `[[ -o interactive ]]` if it should not run in scripts.

### What was verified here

From WSL, against the real libraries and the real pywinpty sources, on
2026-09-08:

- `shlex.split(posix=False)` → `list2cmdline` reproduces the mangled command
  line exactly as tabulated above.
- `zsh -c '"fastfetch; exec zsh"'` gives `command not found: fastfetch; exec
  zsh` and exit status 127 — the widget's window closing follows from that
  alone.
- pywinpty's `ptyprocess.py` and winpty-rs's `conpty/pty_impl.rs` were read at
  the versions the pin `pywinpty>=2.0.0` resolves to; the `shlex`,
  `list2cmdline`, and `appname + " " + cmdline` behaviours are quoted from them
  above rather than inferred.
- The proposed parser passes all four documented `CommandLineToArgvW` examples,
  plus quoted-program-path and the failing command, and round-trips cleanly
  through `list2cmdline` in every case except the unquoted-`argv[0]` one, which
  is the pywinpty defect described above and is present today too.
- Workaround 1's shape was confirmed on a real PTY: `zsh -c 'echo hi;zsh'`
  yields an interactive zsh with `i` in `$-` and the distro prompt.

Nothing was run on Windows. Every claim about `CreateProcessW` is from the
winpty-rs source and Microsoft's documented parsing rules, not from observation.

### What landed

All three, in the shape the candidate fix describes.

- `split_windows_command_line` in `config.py`, used by `resolve_shell` on Windows
  only; the POSIX `shlex.split` branch is untouched. `pty/windows.py` now hands
  pywinpty a list, so `shlex` never sees the command line at all -- which also
  retires the join-then-re-split the preset shells were going through.
- `TerminalSession.start` binds `_backend` only after a successful spawn and
  releases the PTY on failure, so a failed start leaves the session exactly as it
  was. `WidgetWindow.start_session` catches that and writes the reason onto the
  screen instead of dying to a stderr nothing reads.
- `_on_session_ended` keeps the window up when the shell lived under
  `INSTANT_EXIT_S` **and** was never typed into. Both halves matter: a shell you
  typed `exit` into still closes the widget, which is the premise the project
  rests on. It stops the session first -- the window is held open, not the dead
  PTY, which on Windows would be a `conhost.exe` held with it.
- Holding the window open takes away the way to close a widget where there is no
  tray, which the README documents as "exiting its shell". So `Esc` closes a
  window with no live session, and the notice on screen says so. The view has to
  `ignore()` the key for it to get there: Qt only propagates to the parent what
  the child leaves unaccepted.
- The spawn-failure notice prints the *parsed argv*, not the command line. For
  this whole class of bug, where the argument boundaries fell is the answer.
- `exit_status` was added to `PtyBackend` and both backends. Best effort by
  design -- the POSIX one has to call `isalive()` first, because ptyprocess only
  fills `exitstatus` in as it reaps the child, and the notice drops the status
  when it comes back `None`.

The unquoted-`argv[0]` defect above is upstream and was **not** fixed; nothing in
the pywinpty API reaches it. It is still worth an upstream issue.

28 new tests, 258 passing, pyright clean.

One bug was caught in review rather than by the tests, and is worth recording
because the tests all passed with it in place: the quiet-period timer was
started when the feature armed, not when output arrived. Any shell that took
longer than `STARTUP_QUIET_MS` to say its first word -- which is every cold WSL
start, i.e. exactly the case this was built for -- would spend the whole period
in silence, fire against an empty screen, and disarm before its banner arrived.
The suite missed it because every test fed the screen instantly. It is pinned
now by `test_the_quiet_period_is_measured_from_the_output_not_the_launch`.

### What was verified after the fix

From WSL, with a real PTY and a real shell:

- The failure reproduces on Linux when zsh is deliberately fed a literally-quoted
  string, and the window now stays open showing
  `zsh:1: command not found: fastfetch; exec zsh` and `status 127` -- exactly the
  information Windows never gave up.
- A custom command naming a program that does not exist leaves a window reading
  `could not start the shell: The command was not found or was not executable`.
- The parser passes the four documented `CommandLineToArgvW` cases, the quoted
  program path, the backslash path, `cmd.exe /c "..."`, and round-trips through
  `list2cmdline`.

Still nothing run on Windows.

### To verify on Windows

- [ ] The original quoted command — `wsl -d kali-linux -- zsh -c "fastfetch;
      exec zsh"` — starts a working widget, with fastfetch's output and an
      interactive zsh under it
- [ ] Workaround 1 (`... zsh -c fastfetch;zsh`) still works, so nobody who
      adopted it is broken by the fix
- [ ] `cmd.exe /c "echo hello & pause"` — the other quoting shape
- [ ] A custom command whose program is a quoted path with a space in it, e.g.
      `"C:\Program Files\Git\bin\bash.exe" -l`, and whether the unquoted-argv[0]
      defect above bites in practice
- [ ] Every preset shell still starts — cmd, PowerShell, pwsh, WSL — since the
      list-not-string change touches their path too
- [ ] A custom command with a backslash path and no quotes, e.g.
      `C:\Windows\System32\wsl.exe -d kali-linux`, is not eaten
- [ ] A nonsense custom command leaves a readable window rather than a widget
      that never appears, naming the argv it tried, and the tray icon can still
      quit it
- [ ] Esc closes a window held open after a failure, and reaches the shell as
      an ordinary key while one is running
- [ ] No stray `conhost.exe` is left behind by a widget held open after a
      failed shell
- [ ] A custom command that starts and dies at once keeps its window, with the
      shell's own error above the widget's note
- [ ] Ctrl+D, and typing `exit`, still close the widget and end the process
- [ ] With **Scroll to the top when the shell starts** on, the banner reads from
      line 1 and the first keystroke returns to the prompt; with it off, nothing
      changed
