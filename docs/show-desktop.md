# Surviving "Show desktop" on Windows

**Status:** landed, 2026-10-28. Built as plans 01–05 in
[`plans/show-desktop-always-on-top/`](plans/show-desktop-always-on-top/README.md),
and confirmed working on the target machine (Windows 11). The hand checklist
under *Testing* is partly done. `issues.md` section 4 has the summary.
Fixes GitHub issue #6. Branch `issue-6-show-desktop`. The two earlier attempts
and what actually defeated them are under *History* at the end.

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

- On Show desktop, Explorer makes its desktop window (`Progman` on the target
  machine) the foreground window, and within about 50 ms lifts it above every
  normal window. The widget is a normal window, so it ends up underneath.
- When an app comes forward again, Explorer sends `Progman` back to the bottom,
  and the widget shows again. That's why *any* window brings it back.

All of this was observed on the target machine with read-only probes and a
debug launcher; see *History*.

### And why it couldn't simply be fixed: Qt

The `desktop` layer is `Qt::WindowStaysOnBottomHint`. On Windows, Qt doesn't
just place such a window at the bottom once. Its window procedure rewrites
**every** change to the window's place in the stack, from anyone, into "go to
the bottom". In `QWindowsWindow::handleGeometryChangingMessage`
(`qtbase/src/plugins/platforms/windows/qwindowswindow.cpp`):

```cpp
if (qWindow->flags().testFlag(Qt::WindowStaysOnBottomHint))
    windowPos->hwndInsertAfter = HWND_BOTTOM;
```

`SetWindowPos` still reports success. So any attempt to lift the widget while it
carries the hint is silently undone. During Show desktop, "the bottom" is
underneath the lifted desktop. The same file also refuses to bring such a
window to the foreground (`requestActivateWindow`: *"Windows with
Qt::WindowStaysOnBottomHint can't be brought to the foreground"*).

## How Rainmeter does it

Going from memory of Rainmeter's source (`Library/System.cpp`), and matching
what the probes saw on the target machine:

- **Normally**, "On Desktop" skins are ordinary windows directly above the
  desktop window. The probe saw `RainmeterMeterWindow` windows as the nearest
  ones above `Progman`.
- **While the desktop is shown**, the skins are made **always-on-top**. The
  probe saw them stop counting as normal windows over the desktop at that
  moment.
- **Detection is by the window stack, with a wait.** On a foreground change it
  checks whether the desktop now sits above its own windows, and keeps checking
  for a while, because Explorer's lifting lags behind the foreground change.
- Rainmeter is a Win32 program with no toolkit between it and `SetWindowPos`,
  so nothing undoes its moves.

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

A small Windows-only watcher, doing what Rainmeter does:

1. **On Windows the `desktop` layer doesn't use Qt's bottom hint.** The watcher
   keeps the widget at the bottom itself. Qt sees an ordinary tool window, and
   stops undoing the watcher's moves.
2. When the desktop comes to the foreground, **watch the window stack**.
3. If the desktop rises above the widget, that's Show desktop: once the
   **Show desktop delay** has passed, **make the widget always-on-top**.
4. When an **app** comes to the foreground, **send the widget back to the
   bottom**, which also ends always-on-top.

### Without Qt's bottom hint

On Windows, when the desktop layer is available, `_stacking_hint()` returns no
hint for the `desktop` setting. The watcher takes over what the hint did:

- **When it switches on**, it sends the widget to the bottom (`HWND_BOTTOM`)
  before anything else. That covers startup, closing settings, and switching
  the layer back to `desktop`.
- **When an app comes forward**, or the widget is clicked, it goes back to the
  bottom (the tables below), as before.

Elsewhere nothing changes. On Linux, or on Windows when the watcher can't be
set up, the `desktop` layer is Qt's bottom hint, exactly as today.

The hint also decided whether the watcher runs. With no hint to look at,
"should the watcher run" becomes: on the `desktop` setting, not in config mode,
visible, and not mid-restack.

Two side effects, both welcome:

- **Fewer restacks.** The `desktop` layer and config mode now have the same
  flags on Windows, so opening and closing settings no longer destroys and
  recreates the native window.
- **Tray → Show works fully.** Qt's `raise_()` and `activateWindow()` no longer
  refuse the widget. The *revealed* rule below keeps it in front until an app
  comes forward.

### When it runs

Only when all of these hold:

- `platform_info.is_windows()`, and the watcher was set up (its hook installed);
- the `stacking` setting is `desktop`, and config mode is off;
- the window is visible, and not in the middle of a restack.

### What comes to the foreground

Every `EVENT_SYSTEM_FOREGROUND` window is sorted into one of six kinds:

| Kind | Meaning |
| --- | --- |
| **none** | No window. Windows reports this briefly mid-switch |
| **desktop** | `WorkerW` or `Progman` holding a `SHELLDLL_DefView` child |
| **self** | The widget's own window |
| **own** | Another window of this process (the tray menu) |
| **app** | A window Alt+Tab would list (see below) |
| **shell** | Anything else: the taskbar, Start, flyouts, overlays |

"A window Alt+Tab would list" is the usual test: visible, not cloaked, not a
tool window (`WS_EX_TOOLWINDOW`), not `WS_EX_NOACTIVATE`, and either unowned or
marked `WS_EX_APPWINDOW`. Known shell classes (`Shell_TrayWnd`,
`Windows.UI.Core.CoreWindow` and a few more) count as **shell** whatever their
styles say. The target machine confirmed the taskbar and Start as shell, and
the terminal as app.

### What it does

On a foreground change:

| New foreground | Widget state | Action |
| --- | --- | --- |
| none, own, shell | any | Nothing |
| desktop | already raised | Nothing |
| desktop | not raised | Start **watching** (below) |
| self | revealed, or raised | Nothing |
| self | otherwise | Back to the bottom (decision 1) |
| app | any | Back to the bottom, stop watching, clear everything |

**Watching** means checking every 50 ms whether the desktop window is now
*above* the widget in the stack. The first check runs at once.

- The widget is raised (always-on-top) on the first check where the desktop is
  above it **and** the **Show desktop delay** has passed, counted from when the
  desktop came to the foreground. Then watching stops.
- If the desktop hasn't risen within 2 seconds after the delay, it isn't
  going to. That was a click on the wallpaper. Watching stops and nothing
  moves.
- An app coming forward during the wait ends it, as always, so a quick Win+D,
  Win+D never raises the widget at all.

The delay is counted from the foreground event rather than from the moment the
desktop is seen rising. The event is when Windows starts the minimise
animation, so "delay = animation length" means what it says.

"Back to the bottom" is `SetWindowPos(hwnd, HWND_BOTTOM, …, SWP_NOMOVE |
SWP_NOSIZE | SWP_NOACTIVATE)`. Windows documents that this also takes away
always-on-top, so one call undoes a raise.

### The Show desktop delay

A setting, because the right value is however long *this* machine's minimise
animation takes, and that depends on the Windows version and the user's
animation settings. Without it the widget pops up on top while the windows are
still shrinking, which looks rushed. That was the first thing noticed when the
fix worked.

- **Config:** `show_desktop_delay_ms`, an integer. Default **250**. Clamped to
  0–2000.
- **Settings app:** Behaviour group, under *Window layer*: "Show desktop delay",
  a spin box from 0 to 2000 ms in steps of 50, where 0 reads "Immediately".
  Enabled only while *Window layer* is "Behind other windows". Shown only on
  Windows, since nowhere else uses it.
- **Applies live**, like scrollback: the next Show desktop uses the new value.
  A watch already running keeps the delay it started with.
- **Resolution** is the 50 ms watch tick. That's why the spin box steps by 50.

### Why "is the desktop above the widget?"

It's the bug itself, measured directly, and it can't fire by mistake:

- **Show desktop / Win+D** lifts the desktop above every normal window,
  including the widget. The target machine showed this within one tick.
- **Clicking the wallpaper** makes the desktop the foreground window but leaves
  it at the bottom of the stack, under the widget. The check never fires.

### Always-on-top

Always-on-top is entered in one place, only after the desktop has been seen
covering the widget, and only while no app is in front. So the widget can't
end up over a game or a video. It's left by `HWND_BOTTOM` the moment an app
comes forward, or whenever the watcher stops looking.

The old worry about it (`issues.md:142-150`) was that Qt's `windowFlags()`
would disagree with Windows. On Windows the desktop layer no longer has a
stacking flag at all, so there's nothing to disagree with: the widget's place
in the stack belongs to the watcher alone. A restack builds a fresh native
window, and the watcher sends it to the bottom when it switches back on.

### What ends a raise

| What happens | Foreground sequence | Result |
| --- | --- | --- |
| Win+D again | the restored app | app → bottom |
| Click an app on the taskbar | taskbar (shell), then the app | app → bottom |
| Alt+Tab | the switcher (shell), then the app | app → bottom |
| Show desktop corner again | taskbar (shell), then the restored app | app → bottom |
| Open the Start menu, then close it | Start (shell), desktop | stays raised |
| Click the widget and type | self | stays raised |
| Tray menu | own | stays raised |
| Settings opens | (watcher switched off) | → bottom |

### How Tray → Show stays working

`bring_to_front()` marks the widget as **revealed** before activating it, so
its own foreground event doesn't send it back down. The mark clears when an app
comes forward. Without Qt's bottom hint, `raise_()` and `activateWindow()` now
both work on Windows, so the reveal actually brings the widget in front.

### Lifecycle

- **The hook is installed when the watcher is created**, not when it first
  switches on. If installing fails, there is no watcher, and the `desktop`
  layer falls back to Qt's bottom hint. "Is there a watcher?" then reliably
  means "the watcher is doing the hint's job".
- One `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)` per process, out-of-context,
  so events arrive on Qt's GUI thread. The ctypes callback is kept alive for as
  long as the hook is installed.
- The widget's HWND is read fresh on every event, because a restack replaces
  the native window.
- One 50 ms `QTimer`, running only while watching.
- Switching the watcher on sends the widget to the bottom. Switching it off
  stops the timer, and sends the widget to the bottom if it was raised.
- Unhooked on close, before `closed` is emitted.

### Code layout

Changes from what's built (plans 04 and 05):

- **`terminal_widget/desktop_layer.py`**: `create()` installs the hook and
  returns `None` if that fails. The watcher sends the widget to the bottom when
  switched on. `decide()` takes the delay. The watch counts ticks from the
  foreground event, and raises once the delay has passed and the desktop is
  above. The watcher gains a `raise_delay_ms` property.
- **`terminal_widget/window.py`**: `_stacking_hint()` drops the bottom hint
  when a watcher exists. `_sync_desktop_layer()` reads the setting and config
  mode instead of the hint. The delay is passed on at construction and in
  `apply_config()`.
- **`terminal_widget/config.py`**: `show_desktop_delay_ms`.
- **`terminal_widget/settings/__main__.py`**: the spin box.

## Testing

**Automated (any OS):** every row of both tables; the watch with delays of 0,
a few ticks, and longer than Explorer takes; the wallpaper case at each delay;
an app arriving during the delay; switching on sends to the bottom; the window
dropping its bottom hint only when a watcher exists; the setting's clamping,
round trip and settings-app control.

**By hand on Windows**, with the debug launcher:

- [x] Show desktop corner and Win+D: widget stays visible *(2026-10-28, first
      in the experiment, then in the built code)*
- [x] With the delay set, the widget appears as the minimise animation ends,
      not during it *(2026-10-28)*
- [ ] Win+D, then Win+D again quickly (within the delay): widget never jumps
      on top
- [ ] Win+D, then Win+D again: apps come back *above* it
- [ ] After Show desktop, click an app on the taskbar: widget goes back behind
      it
- [ ] After Show desktop, Alt+Tab to an app: same
- [ ] After Show desktop, open and close the Start menu: widget stays visible
- [ ] After Show desktop, click the widget and type: stays visible
- [ ] After Show desktop, open the tray menu: stays visible
- [ ] Click empty wallpaper with apps open: widget does **not** jump above them
- [ ] Click the widget while an app partly covers it: typing reaches the shell,
      and the widget stays behind the app
- [ ] At startup the widget is behind other windows, as before
- [ ] Tray → Show over a covered widget: comes forward, stays until an app is
      used
- [ ] Open settings during Show desktop, then close it: widget back at the
      bottom, not left on top
- [ ] Changing the delay in settings takes effect on the next Show desktop
- [ ] `normal` and `top` layers: behave exactly as before
- [ ] Fullscreen game or video, then Win+D and back: never over it
- [ ] Restart `explorer.exe`: Show desktop still works afterwards

## Decisions

1. **Clicking a partly covered widget pushes it back to the bottom**, as
   Rainmeter does. You can type into it, but it stays behind the app covering
   it. Someone who picked the `desktop` layer asked for "never above my apps",
   and the `normal` layer already gives click-to-front. *(2026-10-28.)*

   *Rejected:* letting a click bring it forward until another window is used.
2. **The foreground event hook is the trigger, plus a bounded 50 ms check
   after a desktop event**, because Explorer lifts the desktop up to about
   50 ms after the event. *(2026-10-28, amended the same day.)*

   *Rejected:* continuous polling. Every Show desktop observed started with the
   desktop coming to the foreground.
3. **Always-on-top while the desktop is shown.** *(2026-10-28.)* It's what
   Rainmeter does, and it's what worked on the target machine.

   This decision was first justified by "no placement in the normal band
   survives Explorer". That reasoning was wrong: those placements were being
   undone by Qt (decision 4), not Explorer. A normal-band placement might work
   now that Qt is out of the way, but it was never fairly tried, and
   always-on-top is proven. Not worth reopening.
4. **No Qt bottom hint on Windows while the watcher exists.** *(2026-10-28.)*
   Qt rewrites every stacking change of a bottom-hint window to `HWND_BOTTOM`,
   so the hint and the watcher can't coexist. The watcher sends the widget to
   the bottom whenever it switches on, which covers what the hint did.

   *Rejected:* keeping the hint and intercepting `WM_WINDOWPOSCHANGING` with a
   native event filter. Qt's own handler runs after the filter and rewrites the
   message anyway. Toggling the hint off and on around each raise would cost a
   destroyed and recreated native window every time.
5. **A Show desktop delay setting, default 250 ms, counted from the desktop
   coming forward.** *(2026-10-28, asked for after the experiment.)* Raising at
   once pops the widget up while windows are still minimising. The animation
   length varies by machine, so it's a setting, not a constant.

## Out of scope

- **Linux "Show desktop".** The window manager handles it differently from
  Windows. Worth one manual check under GNOME and KDE, but not part of this
  change.
- **Living behind the desktop icons.** That's the `WorkerW` approach turned
  down above.
- **Virtual desktops.** Tool windows already follow Windows' own rules there,
  and this change doesn't touch that.
- **The desktop rising with no foreground change.** Not seen since Qt was taken
  out of the way. If it shows up, a slow background check (once a second)
  would cover it.

## History

### Round one: the normal band

Plans in [`plans/show-desktop/`](plans/show-desktop/README.md), steps 1–3
built. On a desktop foreground event, the widget was inserted directly above
the desktop within the normal band. A debug run on the target machine showed
the widget **below** the desktop immediately after every placement, even
though `SetWindowPos` reported success.

At the time this was read as Explorer lifting the desktop over the widget
after the event. **That was wrong.** It was Qt rewriting each placement to
`HWND_BOTTOM` (see *Cause*). One real finding survived: a corner click brings
the taskbar forward first, so "any other window" can't mean "the user left the
desktop". That led to the app/shell split.

### Round two: always-on-top, still under Qt's hint

Plans 01–03 in [`plans/show-desktop-always-on-top/`](plans/show-desktop-always-on-top/README.md),
built and committed. The watch saw the desktop rise above the widget within one
tick, and `SetWindowPos(HWND_TOPMOST)` reported success. One millisecond later
the widget was below the desktop and **not** always-on-top.

A change that fast can't come from another process. It had to be the widget's
own window procedure, and Qt's source confirmed the bottom-hint rewrite.

### The experiment that confirmed it

The debug launcher was given a switch that removes the bottom hint from the
`desktop` layer and lets the watcher place the widget. Run on the target
machine, Show desktop and Win+D both left the widget visible. The only
complaint: it appeared on top before the minimise animation had finished.
That's decision 5.
