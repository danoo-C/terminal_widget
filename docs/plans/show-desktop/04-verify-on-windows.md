# Step 4: Verify on Windows and close out

**Goal:** prove it on a real Windows desktop, write down what was found, and
ship it.

**Files:** `issues.md`, `README.md`, `docs/show-desktop.md`, and the PR.

---

## 4.1 Set-up

On the Windows machine, from the branch:

```
git switch issue-6-show-desktop
py install.py
```

Run the widget from the installed launcher (the `pythonw` path users get), not
from a terminal: that's the path where stderr is discarded, and it should still
work there. Keep a second copy runnable with `python -m terminal_widget` for
reading stderr when something misbehaves.

Note the Windows version and build (`winver`); 24H2 changed the desktop's window
layout, and the result depends on it.

## 4.2 Hand checklist

The checklist is copied from the design doc, with the order fixed so each item
starts from a known state. Tick them in `docs/show-desktop.md` as they pass.

**Show desktop**
- [ ] Show desktop button: widget stays visible
- [ ] Win+D, then Win+D again: widget visible both times; apps come back
      *above* it
- [ ] After Show desktop, open any app: widget goes back behind it
- [ ] After Show desktop, click the widget and type: it stays visible, and typing
      reaches the shell
- [ ] After Show desktop, right-click the tray icon: widget stays visible while
      the menu is open

**Normal use**
- [ ] Click empty wallpaper with apps open: widget does **not** jump above them
- [ ] Click the widget while an app partly covers it: typing reaches the shell,
      and the widget stays behind the app
- [ ] Tray → Show over a covered widget: it comes forward and stays there until
      another window is used
- [ ] Second launch (IPC show): same as Tray → Show

**Other layers and modes**
- [ ] Open settings (config mode) and close it: no jumping during the restack;
      Show desktop works again afterwards
- [ ] `normal` and `top` layers: behave exactly as before
- [ ] Win+M (minimize all): note what happens. A tool window with no minimize
      box is expected to stay put

**Robustness**
- [ ] Restart `explorer.exe` (Task Manager → Restart): Show desktop still works
      afterwards
- [ ] Fullscreen app or game, then Win+D: no flicker over it on the way back
- [ ] Which class takes the foreground on Win+D on this build: `WorkerW` or
      `Progman` (a one-off print from `classify`, then removed)
- [ ] Quit from the tray: no error output, no hang. This checks that the hook
      is uninstalled.

## Probe results so far (2026-10-28)

A read-only probe on the target machine logged foreground changes and the
window stack while Win+D and the Show desktop corner were used:

- **Every time the desktop came to cover everything, `Progman` had become the
  foreground window just before.** For the corner, the taskbar
  (`Shell_TrayWnd`) comes forward first, then `Progman`. The hook's trigger is
  the right one.
- **In that state the window directly above `Progman` is always-on-top.** The
  first `place_above` would have inserted the widget after it and made the
  widget always-on-top. It now uses `HWND_TOP` instead (step 2 plan, 2.3).
- **Rainmeter's skins (`RainmeterMeterWindow`) normally sit directly above
  `Progman`**, where this widget belongs. While the desktop is uncovered they
  no longer count as covering it, which suggests Rainmeter makes them
  always-on-top for that moment.
- **The installed widget (`Qt6112QWindowToolSaveBits`) also sits directly
  above `Progman` normally,** and drops out of sight on Show desktop: the bug,
  observed.
- Clicking the empty wallpaper wasn't captured. It's still on the checklist.

## 4.3 If something fails

- **The widget still disappears on Win+D:** first check what `classify` returns
  for the foreground window during Win+D. If it isn't `DESKTOP`, the class check
  needs another case for this build. If it is `DESKTOP`, `place_above` lost a
  race with Explorer. Try deferring the placement by one event-loop turn
  (`QTimer.singleShot(0, …)`) before considering anything heavier.
- **The hook never fires:** `install` returned `False` (stderr says so), or the
  callback wrapper was collected. Check both before suspecting the event itself.
  Polling is the documented fallback (design doc, decision 2), but only once the
  hook is shown to be unreliable.
- **Clicking the widget takes focus away:** `SetWindowPos` with `HWND_BOTTOM`
  on the active window deactivated it on this build. Record it, and reopen
  decision 1 with the user rather than working around it quietly.

## 4.4 Write it down

- **`issues.md`**: add **section 4, "Show desktop hides the widget"**, in the
  file's usual shape (status, what happens, why it matters, cause, what landed,
  what was verified, to verify). Keep it short and link to
  `docs/show-desktop.md` for the design rather than repeating it.
- **`issues.md`** section 1: tick the Win+D item at line 180 and point it at
  section 4. Mark the one-shot question at lines 182-184 as answered by
  decision 1. Update the "ctypes fast path" paragraph (lines 142-155) with one
  sentence saying the `WM_ACTIVATE` re-sink it described has now landed, as a
  foreground hook.
- **`README.md`**: if the layering paragraph says the widget sits below other
  windows, add that it also stays up through Show desktop on Windows. One
  sentence, no more.
- **`docs/show-desktop.md`**: change **Status** to "landed", with the date and
  the Windows build it was verified on.

## 4.5 Ship

- Push the branch and open a PR to `main` with `Fixes #6` in the body, so the
  issue closes on merge.
- Reply on issue #6 (in Slovak, matching the reporter) once it's merged, asking
  them to update and confirm.

## Done when

Every box in 4.2 is ticked, the docs say what was found, and the PR is open.
