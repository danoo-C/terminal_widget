# Surviving "Show desktop" on Windows

**Status:** design only, no code yet. Fixes GitHub issue #6. Branch
`issue-6-show-desktop`, 2026-10-28.

---

## The bug

Reported in issue #6 (in Slovak): clicking **Show desktop** in the bottom-right
corner of the taskbar makes the widget disappear. It comes back as soon as any
window is opened, whether that's an unrelated app or the widget's own settings.

Win+D does the same thing. This is the checklist item `issues.md:180` left
open, *"Win+D / Show desktop — does it hide the widget? Rainmeter survives this
by living on the desktop layer; a tool window may not"*, and the answer is that
it does hide it.

## Cause

The widget is not closed and not minimized. It's covered.

- The default `desktop` layer is `Qt::WindowStaysOnBottomHint`
  (`terminal_widget/window.py:61`). On Windows, Qt turns that into one
  `SetWindowPos(hwnd, HWND_BOTTOM)` call when the native window is created
  (`issues.md:133-140`). It means "below every other window", and nothing keeps
  it there afterwards.
- Show desktop doesn't hide windows one by one. Explorer brings its desktop
  window (the one that holds the icons, `SHELLDLL_DefView`, inside a `WorkerW`
  or `Progman` window) to the top of the stack. That window covers the whole
  screen and everything below it, and the widget is below it by design.
- When another window takes the foreground, Explorer leaves show-desktop mode
  and sends the desktop window back to the bottom, and the widget shows again.
  That's why *any* window brings it back. A minimized window would stay
  minimized.

Nothing in the code watches for any of this. There's no native Windows code in
the widget at all.

## How Rainmeter does it

Going from memory of Rainmeter's source (`Library/System.cpp`), so worth
re-reading before implementing. "On Desktop" skins are not inside the desktop
window. They're ordinary windows that Rainmeter keeps re-placing:

- normally, at the bottom of the stack, just above the desktop window;
- when the foreground window turns out to be the desktop, above it;
- when anything else takes the foreground, back to the bottom.

It finds the desktop by window class (`WorkerW` / `Progman` with a
`SHELLDLL_DefView` child) and reacts to foreground changes.

## Why not embed it inside the desktop

The other way to live "on the desktop" is what wallpaper apps do: send Progman
the undocumented `0x052C` message and attach the window to the `WorkerW` that
sits behind the icons. It was turned down in `issues.md:103`, and it's worse
for a terminal than for a wallpaper:

- The icon layer (`SHELLDLL_DefView`) covers the whole screen above that point,
  so clicks never reach the widget, and neither does typing. Wallpaper apps can
  live with that; a terminal can't.
- It relies on undocumented Windows behaviour. The window layout changed in
  Windows 11 24H2, and the trick has to be redone every time Explorer restarts.
- It ties our windows to Explorer's: if Explorer hangs, so does the widget, and
  per-pixel translucency on child windows has its own limits.

## The design

A small Windows-only watcher that moves the widget **within the normal (not
topmost) window band** whenever the foreground window changes.

### When it runs

Only when all of these hold:

- `platform_info.is_windows()`;
- the stacking actually in effect is `desktop`, i.e. `_stacking_hint()` returns
  `WindowStaysOnBottomHint` (`window.py:335`). That excludes config mode, which
  already joins the normal order, and the `normal` and `top` settings;
- the window is visible.

On Linux nothing changes: X11's `_NET_WM_STATE_BELOW` is held by the window
manager, and Show desktop there is a separate question (see *Out of scope*).

### What it does

On every `EVENT_SYSTEM_FOREGROUND`, given the new foreground window `fg`:

| `fg` is… | Action |
| --- | --- |
| the desktop window | Place the widget **directly above `fg`**, and remember it |
| the widget itself, just revealed (tray Show, second launch) | Nothing: leave it where activation put it |
| the widget itself, clicked while the desktop is still the remembered one | Place it above that desktop again, so it doesn't sink under it |
| the widget itself, clicked by the user otherwise | Push it back to the bottom, without deactivating it (see *Decisions*) |
| another window of this process (the tray menu) | Nothing |
| no window (Windows reports none mid-switch) | Nothing |
| anything else | Push it back to the bottom, and forget the desktop |

The full decision table, with the state it carries, is in
[`plans/show-desktop/01-decision-logic.md`](plans/show-desktop/01-decision-logic.md).

"Push it back to the bottom" is `SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)`, the same call Qt makes when it
creates the window.

"Directly above `fg`" means inserting the widget after whatever window sits just
above the desktop: `prev = GetWindow(fg, GW_HWNDPREV)`, then
`SetWindowPos(hwnd, prev or HWND_TOP, …)` with the same flags. That's what
handles the two kinds of "desktop is in front":

- **Show desktop / Win+D.** The desktop is at the top of the stack, so `prev` is
  nothing and the widget goes to the top. It's visible.
- **Clicking the empty wallpaper with apps open.** The desktop is in front for
  input but still at the bottom of the stack, so the widget lands just above
  it, still behind the apps. A blunt "desktop is in front, raise the widget"
  rule would pop it over everything here.

### Why this keeps the earlier objection from applying

`issues.md:142-150` rejected a `ctypes` `SetWindowPos` mainly because leaving the
topmost band would make the real window style disagree with Qt's
`windowFlags()`. This design never sets `HWND_TOPMOST` or `HWND_NOTOPMOST`, so
`WS_EX_TOPMOST` is never touched. It only changes the widget's position in the
normal band, which Qt doesn't track. `windowFlags()` stays accurate, and Qt's
deliberate `raise_()` no-op under the bottom hint keeps working.

Not using topmost also means the widget can never appear over a fullscreen game
or a video.

### How Tray → Show stays working

`bring_to_front()` (`window.py:391`) is the only way back to a covered widget,
and it works by *activating* the widget. Activation fires a foreground event for
the widget, and the "pushed back to the bottom" row would undo the reveal
straight away.

So `bring_to_front()` marks the widget as **revealed** before it activates it.
While revealed, the widget's own foreground events are ignored. The flag clears
on the next foreground event for any other window, and the normal rules apply
again from there. The IPC path from a second launch (`__main__.py:107`) goes
through `bring_to_front()`, so it gets this for free.

### Lifecycle

- **One hook per process, not per window.** `SetWinEventHook(EVENT_SYSTEM_FOREGROUND,
  EVENT_SYSTEM_FOREGROUND, 0, callback, 0, 0, WINEVENT_OUTOFCONTEXT)`.
  Out-of-context hooks are delivered through the installing thread's message
  queue, which Qt's event loop already pumps, so the callback runs on the GUI
  thread and needs no locking. Don't pass `WINEVENT_SKIPOWNPROCESS`: the
  widget's own foreground events are needed for the click and reveal rows.
- **Read the HWND fresh on every event** with `int(self.winId())`. A restack
  destroys and recreates the native window (`window.py:354`), so a stored HWND
  would go stale. Because the hook isn't tied to a window, it never needs
  reinstalling.
- **Re-check after anything that changes whether it should run**: the end of
  `_restack()`, `set_config_mode(False)`, and a `stacking` change in
  `apply_config()`. Each runs the table once against the current
  `GetForegroundWindow()`, so closing settings while the desktop is shown puts
  the widget in the right place without waiting for the next event.
- **Keep the ctypes callback object alive** for as long as the hook is
  installed. If it's garbage-collected, the hook calls freed memory and the
  process crashes.
- **Unhook on close** (`UnhookWinEvent`), before `closed` is emitted.
- **Explorer restarts need nothing.** The desktop is identified by class name on
  every event, and no handle is cached. With no Explorer running nothing
  matches, and the widget behaves as it does today.

### Spotting the desktop window

`fg` counts as the desktop when its class is `WorkerW` or `Progman` **and** it
has a `SHELLDLL_DefView` child (`FindWindowExW(fg, 0, "SHELLDLL_DefView", None)`).
The child check matters because Explorer keeps several `WorkerW` windows that
aren't the desktop.

Windows 11 24H2 changed which of the two holds the icons, so checking both
classes is deliberate. Which one actually takes the foreground on Win+D needs
confirming on the target machine.

### Code layout

- **`terminal_widget/desktop_layer.py`** (new). Two parts:
  - A pure decision function, `decide(foreground, state) -> (action, state)`,
    that takes what kind of window is in front plus the remembered state, and
    returns one of *above desktop / to bottom / leave*. It has no Win32 calls,
    so it can be tested on Linux.
  - A thin Win32 adapter: the `ctypes` declarations, hook install and removal,
    and `apply(action)`. Imported only when `is_windows()`.
- **`terminal_widget/window.py`**. `WidgetWindow` owns one optional watcher,
  with an `enabled` property set from the conditions in *When it runs*, plus the
  `revealed` flag in `bring_to_front()`. No change to flags, geometry or
  opacity code.
- No new dependencies. `ctypes` comes with Python, and `pywin32` isn't needed.

Rough size: 100–150 lines of module, about 20 lines in `window.py`, plus tests.

## Testing

**Automated (any OS):** table tests for `classify()` covering every row above,
including the revealed flag and the wallpaper-click case. Window tests asserting
the watcher's `enabled` follows config mode and stacking changes, with the Win32
adapter replaced by a fake. Nothing in the offscreen suite can exercise the real
z-order.

**By hand on Windows:**

- [ ] Show desktop button: widget stays visible
- [ ] Win+D, then Win+D again: widget visible both times; apps come back
      *above* it
- [ ] After Show desktop, open any app: widget goes back behind it
- [ ] After Show desktop, click the widget and type: it stays visible
- [ ] After Show desktop, open the tray menu: widget stays visible
- [ ] Click empty wallpaper with apps open: widget does **not** jump above them
- [ ] Click the widget while an app partly covers it: typing reaches the shell,
      and the widget stays behind the app (pushing it down must not take focus
      away from it)
- [ ] Tray → Show over a covered widget: it comes forward and stays there until
      another window is used
- [ ] Second launch (IPC show): same as Tray → Show
- [ ] Open settings (config mode) and close it: watcher off, then on again, and
      no jumping during the restack
- [ ] `normal` and `top` layers: behave exactly as before
- [ ] Win+M (minimize all): note what happens. A tool window with no minimize
      box is expected to stay put
- [ ] Restart `explorer.exe`: Show desktop still works afterwards
- [ ] Which class takes the foreground on Win+D on this build: `WorkerW` or
      `Progman`
- [ ] Fullscreen app or game, then Win+D: no flicker over it on the way back

## Decisions

Both settled on 2026-10-28.

1. **Clicking a partly covered widget pushes it back to the bottom**, as
   Rainmeter does. You can type into it, but it stays behind the app covering
   it. Someone who picked the `desktop` layer asked for "never above my apps",
   and the `normal` layer already gives click-to-front. This also answers the
   one-shot question in `issues.md:182-184`: the desktop layer is no longer
   one-shot.

   *Rejected:* letting a click bring it forward, as it does today, until another
   window is used.
2. **Foreground event hook, not polling.** `SetWinEventHook` reacts the moment
   the foreground changes, so Win+D has no visible delay, and the process
   sleeps between events.

   *Rejected:* a ~250 ms `QTimer` polling `GetForegroundWindow()`. It's simpler,
   but it adds a visible delay on Win+D and wakes the process for nothing.
   Polling would come back only if the hook turns out to be unreliable on a
   real machine.

## Out of scope

- **Linux "Show desktop".** The window manager handles it differently from
  Windows. Worth one manual check under GNOME and KDE, but not part of this
  change.
- **Living behind the desktop icons.** That's the `WorkerW` approach turned
  down above.
- **Virtual desktops.** Tool windows already follow Windows' own rules there,
  and this change doesn't touch that.
