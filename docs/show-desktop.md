# Surviving "Show desktop" on Windows

**Status:** second design, no code for it yet. Fixes GitHub issue #6. Branch
`issue-6-show-desktop`. The first design (2026-10-28) was built and tested on
the target machine, and failed there; see *History* at the end. Plans for the
second design are in [`plans/show-desktop-always-on-top/`](plans/show-desktop-always-on-top/README.md).

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
  (`issues.md:133-140`). Normally the widget then sits directly above Explorer's
  desktop window (`Progman` on the target machine), next to Rainmeter's skins.
- On Show desktop, Explorer makes `Progman` the foreground window and then, a
  moment later, lifts it above every normal window. The widget is a normal
  window, so it ends up underneath.
- When an app comes forward again, Explorer sends `Progman` back to the bottom,
  and the widget shows again. That's why *any* window brings it back.

All of this was observed on the target machine with read-only probes; the logs
are summarised in [`plans/show-desktop/04-verify-on-windows.md`](plans/show-desktop/04-verify-on-windows.md)
and under *History*.

## How Rainmeter does it

Going from memory of Rainmeter's source (`Library/System.cpp`), now matching
what the probes saw on the target machine:

- **Normally**, "On Desktop" skins are ordinary windows directly above the
  desktop window. The probe saw exactly that: `RainmeterMeterWindow` windows
  were the nearest ones above `Progman`.
- **While the desktop is shown**, the skins are made **always-on-top**. The
  probe saw them stop counting as normal windows over the desktop at that
  moment.
- **Detection is by the window stack, with a wait.** On a foreground change it
  checks whether the desktop now sits above its own windows. Because Explorer's
  rearranging lags behind the foreground change, it keeps checking for up to
  about two seconds. As remembered, its source has a comment about waiting for
  this; worth re-reading.

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

1. When the desktop comes to the foreground, **watch the window stack** for up
   to 2 seconds.
2. If the desktop rises above the widget in that time, that's Show desktop:
   **make the widget always-on-top.**
3. When an **app** comes to the foreground, **send the widget back to the
   bottom**, which also ends always-on-top.

### When it runs

Only when all of these hold:

- `platform_info.is_windows()`;
- the stacking actually in effect is `desktop`, i.e. `_stacking_hint()` returns
  `WindowStaysOnBottomHint`. That excludes config mode, which already joins the
  normal order, and the `normal` and `top` settings;
- the window is visible.

This part is unchanged from the first design and already built
(`WidgetWindow._sync_desktop_layer()`).

### What comes to the foreground

Every `EVENT_SYSTEM_FOREGROUND` window is sorted into one of six kinds. The
first design had five; *anything else* is now split in two, because the
taskbar and the Start menu come forward *during* Show desktop and must not end
it.

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
`Shell_SecondaryTrayWnd`, `Windows.UI.Core.CoreWindow` and a few more) count as
**shell** whatever their styles say.

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

**Watching** means checking every 50 ms, for at most 2 seconds, whether the
desktop window is now *above* the widget in the stack. The first check runs at
once, without waiting 50 ms.

- **Above:** that's Show desktop. Make the widget always-on-top
  (`SetWindowPos(hwnd, HWND_TOPMOST, …, SWP_NOMOVE | SWP_NOSIZE |
  SWP_NOACTIVATE)`), mark it **raised**, and stop watching.
- **Still below after 2 seconds:** that was a click on the wallpaper, or
  anything else that brought the desktop forward without lifting it. Stop
  watching and do nothing. The widget stays behind your apps.

"Back to the bottom" is `SetWindowPos(hwnd, HWND_BOTTOM, …)` with the same
flags. Windows documents that this also takes away always-on-top, so one call
undoes a raise. `HWND_NOTOPMOST` is never needed.

### Why "is the desktop above the widget?"

It's the bug itself, measured directly, and it can't fire by mistake:

- **Show desktop / Win+D** lifts the desktop above every normal window,
  including the widget. The check fires within one tick of Explorer doing it.
- **Clicking the wallpaper** makes the desktop the foreground window but leaves
  it at the bottom of the stack, under the widget. The check never fires.

Rainmeter uses the same test, with its own helper window in place of the
widget.

### Always-on-top, and the objection it reverses

The first design ruled always-on-top out (see decision 3 for the reversal). The
reasons, and why each is acceptable now:

- **Qt's `windowFlags()` would disagree with Windows** (`issues.md:142-150`).
  True, but only while raised, and nothing reads the difference. Qt applies
  stacking only when it creates the native window (a restack), and `raise_()`
  is a no-op under the bottom hint. A restack creates a fresh window without
  always-on-top anyway. To be safe, switching the watcher off while raised
  sends the widget back to the bottom first.
- **It could appear over a fullscreen game or a video.** It's raised only
  after the desktop has come above it, which only happens while the desktop is
  shown and no app is in front. The moment an app comes forward, it goes back
  to the bottom.

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

With no app to restore (nothing was open), the widget stays raised over an
uncovered desktop. That looks the same as not raised, and the next app to come
forward ends it.

### How Tray → Show stays working

Unchanged from the first design and already built. `bring_to_front()` marks the
widget as **revealed** before activating it, so its own foreground event
doesn't send it back down. The mark clears when an app comes forward.

### Lifecycle

Unchanged from the first design and already built, plus one timer:

- One `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)` per process, out-of-context,
  so events arrive on Qt's GUI thread. The ctypes callback is kept alive for as
  long as the hook is installed.
- The widget's HWND is read fresh on every event, because a restack replaces
  the native window.
- **New: one 50 ms `QTimer`**, running only while watching (at most 2 seconds
  after the desktop comes forward). It lives on the GUI thread like the hook.
- Switching the watcher off stops the timer, and sends the widget back to the
  bottom if it was raised.
- Unhooked on close, before `closed` is emitted.

### Code layout

Changes to what's already built:

- **`terminal_widget/desktop_layer.py`**
  - `Kind`: `OTHER` splits into `APP` and `SHELL`.
  - `decide()` handles two kinds of input, a foreground change and a watch
    tick. `State` gains `raised` and a tick countdown. `ABOVE_DESKTOP` gives way
    to `RAISE`, and the state no longer needs to remember the desktop's handle
    for placement, only for the stack check.
  - `DesktopLayer` gains a ticker (a `QTimer` in production, a fake in tests).
  - `Win32Shell`: `classify()` learns app versus shell; new
    `desktop_above(widget, desktop)` and `raise_topmost(widget)`.
    `place_above()` goes, together with its topmost workaround and tests.
- **`terminal_widget/window.py`**: nothing expected beyond what step 3 built.

## Testing

**Automated (any OS):** every row of both tables, the watch countdown, the
wallpaper case (desktop never rises, so no raise), a raise then each way of
ending it, and switching off while raised. The Win32 parts that can be faked
(app versus shell classification, the stack walk) are tested against a fake
`user32`, as `place_above` was.

**By hand on Windows**, with the debug launcher from the first round:

- [ ] Show desktop corner: widget stays visible (a blink is acceptable, see
      below)
- [ ] Win+D, then Win+D again: visible both times; apps come back *above* it
- [ ] After Show desktop, click an app on the taskbar: widget goes back behind
      it
- [ ] After Show desktop, Alt+Tab to an app: same
- [ ] After Show desktop, open and close the Start menu: widget stays visible
- [ ] After Show desktop, click the widget and type: stays visible
- [ ] After Show desktop, open the tray menu: stays visible
- [ ] Click empty wallpaper with apps open: widget does **not** jump above them,
      even after 2 seconds
- [ ] Click the widget while an app partly covers it: typing reaches the shell,
      and the widget stays behind the app
- [ ] Tray → Show over a covered widget: comes forward, stays until an app is
      used
- [ ] Open settings during Show desktop, then close it: no leftover
      always-on-top
- [ ] `normal` and `top` layers: behave exactly as before
- [ ] Fullscreen game or video, then Win+D and back: the widget never ends up
      over it
- [ ] Restart `explorer.exe`: Show desktop still works afterwards
- [ ] How long the widget blinks out on Show desktop, if at all

**The blink:** Explorer lifts the desktop, and only then can the watcher see it
and raise the widget. That's up to one 50 ms tick, plus however long Explorer
takes to draw. If that's visible and bothersome, the tick can be shortened;
the stack check is cheap.

## Decisions

1. **Clicking a partly covered widget pushes it back to the bottom**, as
   Rainmeter does. You can type into it, but it stays behind the app covering
   it. Someone who picked the `desktop` layer asked for "never above my apps",
   and the `normal` layer already gives click-to-front. This also answers the
   one-shot question in `issues.md:182-184`. *(2026-10-28, unchanged.)*

   *Rejected:* letting a click bring it forward until another window is used.
2. **The foreground event hook is the trigger, plus a short bounded check
   after a desktop event.** The hook still decides *when* to look. The 50 ms
   check runs only for the 2 seconds after the desktop comes forward, because
   the target machine showed Explorer lifting the desktop *after* the event.
   *(2026-10-28; amended the same day after the first design failed.)*

   *Rejected:* continuous polling. It isn't needed, because every Show desktop
   observed started with the desktop coming to the foreground.
3. **Always-on-top while the desktop is shown.** This reverses the first
   design's "never always-on-top" rule. *(2026-10-28, after the first design
   failed.)* On the target machine, placing the widget directly above the
   desktop in the normal band lost to Explorer within one check. Explorer lifts
   the desktop above every normal window, so no normal-band placement can win.
   Rainmeter's skins survive the same Show desktop on the same machine by being
   always-on-top.

   *Rejected:* staying in the normal band and re-placing the widget repeatedly.
   That fights Explorer on every tick and still loses between ticks.

## Out of scope

- **Linux "Show desktop".** The window manager handles it differently from
  Windows. Worth one manual check under GNOME and KDE, but not part of this
  change.
- **Living behind the desktop icons.** That's the `WorkerW` approach turned
  down above.
- **Virtual desktops.** Tool windows already follow Windows' own rules there,
  and this change doesn't touch that.
- **The desktop rising with no foreground change.** Seen once during a restore
  in the first round (8.86 s in the log below), when the widget wasn't raised,
  and it ended a moment later when the restored app came forward. If it shows
  up as a problem, a slow background check (once a second) would cover it.

## History: the first design, and why it failed

The first design (plans in [`plans/show-desktop/`](plans/show-desktop/README.md),
steps 1–3 built) never used always-on-top. On a desktop foreground event it
inserted the widget directly above the desktop *within the normal band*, and
treated every other foreign window as "anything else, back to the bottom".

Run on the target machine with a debug launcher logging every step:

- The hook installed, the watcher switched on, and Win+D and the corner were
  both seen as `Progman -> DESKTOP`. Every `SetWindowPos` succeeded.
- At 14.07 s the widget was placed directly above the desktop. The very next
  check already showed it **below** the desktop. Explorer finishes lifting the
  desktop *after* the foreground event, over every normal window.
- At 8.86 s the widget dropped below the desktop with no event at all, while
  Explorer was restoring apps.
- An earlier probe had also shown the taskbar coming to the foreground at the
  start of a corner click. The first design treated that as "anything else" and
  would have sent the widget to the bottom at the wrong moment.

Those three findings are decisions 2 and 3 and the app/shell split above. The
first design's state handling, hook, window integration and tests are kept;
only the decisions and the Windows placement change.
