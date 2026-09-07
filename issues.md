# Known issues

Defects found in real use, with what is known about each. Design limitations
that are working as intended live in the README instead; open questions that
were never decided live in section 14 of `megapromt/PASS_2.md`.

---

## 1. The widget has a taskbar button and appears in Alt+Tab

**Status:** open, not yet fixed. Found on Windows, 2026-09-08.

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

### The catch, and why this is not a one-line change

Removing the widget from the taskbar and from Alt+Tab also removes both ways of
getting it back. Today it sits in the normal window stack, so anything you open
can cover it — and with no taskbar button, no switcher entry, no tray icon and
no global hotkey, a covered widget would be genuinely unreachable until
something moved.

So the fix is really two decisions, and only the first one is easy:

1. Stop being an application window — `Qt::Tool`, above.
2. Decide where it lives in the stack. This is open question 4 in the brief, and
   it is what makes (1) safe. Rainmeter's answer is to pin to the desktop layer,
   which on Windows means parenting to the `WorkerW` window behind the desktop
   icons — no supported API, and it has to be redone when Explorer restarts. The
   cheaper approximation is `Qt::WindowStaysOnBottomHint`, which keeps the widget
   below normal windows without the `WorkerW` surgery, at the cost of it being
   covered rather than living behind the icons.

Doing (1) without (2) trades a visible annoyance for a way to lose the widget
entirely. They should land together.

### To verify on Windows

Nothing below can be checked from WSL; the taskbar and Alt+Tab are Windows shell
behaviours, and Qt's `render()`/`grab()` cannot even capture the compositing.

- [ ] With `Qt::Tool`, no taskbar button and no Alt+Tab entry
- [ ] Clicking the widget still focuses it and typing still reaches the shell
- [ ] Config mode still drags and resizes under the Windows WM
- [ ] Background-only opacity still composites (tool windows are still layered)
- [ ] **Win+D / Show desktop** — does it hide the widget? Rainmeter survives
      this by living on the desktop layer; a tool window may not. Worth knowing
      before choosing between `WorkerW` and `WindowStaysOnBottomHint`
- [ ] Whether the widget can still be raised at all once it is covered, which is
      the question decision (2) exists to answer
