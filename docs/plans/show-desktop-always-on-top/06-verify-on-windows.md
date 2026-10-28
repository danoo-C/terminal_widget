# Step 6: Verify on Windows and close out

**Goal:** prove the second design on the target machine with the debug
launcher, write down what was found, and ship.

**Files:** the debug launcher (scratch, not committed), `issues.md`,
`README.md`, `docs/show-desktop.md`, the PR.

---

## 6.1 The debug launcher

Already updated during round two (`run_debug.py` in the session scratchpad).
It logs `classify` with `APP` / `SHELL`, each change in `desktop_above`, every
`SetWindowPos` with whether it succeeded, and afterwards whether the widget is
topmost and above or below the desktop. It also runs an independent 50 ms
poller.

Still to do, after steps 4 and 5:

- remove the `nohint` experiment (step 4.5);
- log the delay each watch started with, and the tick it raised on. Then the
  setting can be tuned against the animation from the log, not just by eye.

Run it as before: the installed venv's `python.exe`, the checkout's path as
the argument, daily widget quit first.

## 6.2 Hand checklist

The checklist in `docs/show-desktop.md` (*Testing*), in this order:

1. Show desktop corner, then click an app on the taskbar
2. Win+D, then Win+D again
3. Win+D, then Alt+Tab to an app
4. Win+D, open the Start menu, close it, then Win+D
5. Win+D, click the widget and type, then Win+D
6. Win+D, right-click the tray icon, close the menu, then Win+D
7. With apps open, click the empty wallpaper and wait 3 seconds
8. Click the widget while an app partly covers it, and type
9. Tray → Show over a covered widget, then click an app
10. Win+D, open Settings from the tray, close Settings, then click an app
11. Switch the layer to `normal`, try 1–2; then `top`, try 1–2; then back to
    `desktop`
12. A fullscreen video or game, Win+D, then back to it
13. Restart `explorer.exe`, then 1 again
14. Tune the Show desktop delay in settings until the widget appears as the
    minimise animation ends; note the value
15. Win+D, then Win+D again within the delay: the widget never jumps on top
16. Start the widget: it starts behind other windows
17. Change the delay in settings, close settings, Win+D: the new delay applies

For each item, paste the launcher's log lines. The design's prediction for
each is in its tables, so a mismatch shows which row is wrong.

## 6.3 If something fails

- **Still hidden on Show desktop, and the log shows `raise` succeeded with the
  widget topmost:** something other than `Progman` is covering it. Log every
  window above the widget at that moment (class, topmost, cloaked) and look.
- **No `raise` at all:** check whether `desktop_above` ever answered `True`
  during the watch. If it stayed `False` for the full 2 s, Explorer is slower
  than that, or it covers the widget some other way than lifting `Progman`.
  Lengthen the watch first; if that doesn't help, look at what the poller saw.
- **The widget stays always-on-top after an app comes forward:** the app was
  classified `SHELL`. Fix the classification rather than adding a fallback.
- **The blink is long or ugly:** shorten `WATCH_INTERVAL_MS` to 16 ms (one
  frame) and measure again. The stack check is cheap.
- **Anything raises the widget over a fullscreen app:** stop. That breaks
  decision 3's premise and needs a design change, not a tweak.

## 6.4 Write it down

- **`issues.md`**: add **section 4, "Show desktop hides the widget"**, in the
  file's usual shape. Say plainly that the first design failed and why, link
  to `docs/show-desktop.md` for the rest, and list what was verified.
- **`issues.md`** section 1: tick the Win+D item (line 180) and point it at
  section 4. Mark the one-shot question (lines 182-184) as answered by
  decision 1. Add one sentence to the "ctypes fast path" paragraph (lines
  142-155): native `SetWindowPos` calls have now landed, including a temporary
  `HWND_TOPMOST`, for the reasons in decision 3. Also correct the section's
  claim (lines 133-140) that `WindowStaysOnBottomHint` is "one-shot on
  Windows". Qt re-applies it on every stacking change, which is why the
  desktop layer drops it on Windows (decision 4).
- **`README.md`**: one sentence in the layering paragraph saying the widget
  stays up through Show desktop on Windows.
- **`docs/show-desktop.md`**: **Status** becomes "landed", with the date; tick
  the checklist.

## 6.5 Ship

- Push the branch, and open a PR to `main` with `Fixes #6` in the body.
- After merge, reply on issue #6 in Slovak, asking the reporter to update and
  confirm.
- Reinstall from `main` (`py install.py`) so the daily widget is the fixed one.
  Until then it's still the old copy in `%LOCALAPPDATA%\TerminalWidget`.

## Done when

Every checklist item behaves as the design predicts, the docs say what was
found, and the PR is open.
