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
