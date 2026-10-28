# Step 4: Verify on Windows and close out

**Goal:** prove the second design on the target machine with the debug
launcher, write down what was found, and ship.

**Files:** the debug launcher (scratch, not committed), `issues.md`,
`README.md`, `docs/show-desktop.md`, the PR.

---

## 4.1 Update the debug launcher

Round one's `run_debug.py` wraps `Win32Shell` methods to log them. Update it
for the new surface:

- wrap `raise_topmost` and `place_at_bottom`: log the call, whether
  `SetWindowPos` succeeded, and afterwards whether the widget is **topmost**
  and **above or below the desktop**, at once and again 150 ms and 600 ms
  later;
- wrap `desktop_above`: log each tick's answer, but only when it changes, so a
  2 s watch is one or two lines, not 40;
- keep logging `classify`, now with `APP` / `SHELL`. Any `SHELL` that should be
  `APP`, or the reverse, is a gap in `SHELL_CLASSES` or `_is_app_window`;
- keep the independent 50 ms poller, and add whether the widget is topmost.

Run it the same way as round one: the installed venv's `python.exe`, the
checkout's path as the argument, daily widget quit first.

## 4.2 Hand checklist

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
14. Note how long the widget blinks out on Show desktop, if at all

For each item, paste the launcher's log lines. The design's prediction for
each is in its tables, so a mismatch shows which row is wrong.

## 4.3 If something fails

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

## 4.4 Write it down

- **`issues.md`**: add **section 4, "Show desktop hides the widget"**, in the
  file's usual shape. Say plainly that the first design failed and why, link
  to `docs/show-desktop.md` for the rest, and list what was verified.
- **`issues.md`** section 1: tick the Win+D item (line 180) and point it at
  section 4. Mark the one-shot question (lines 182-184) as answered by
  decision 1. Add one sentence to the "ctypes fast path" paragraph (lines
  142-155): native `SetWindowPos` calls have now landed, including a temporary
  `HWND_TOPMOST`, for the reasons in decision 3.
- **`README.md`**: one sentence in the layering paragraph saying the widget
  stays up through Show desktop on Windows.
- **`docs/show-desktop.md`**: **Status** becomes "landed", with the date; tick
  the checklist.

## 4.5 Ship

- Push the branch, and open a PR to `main` with `Fixes #6` in the body.
- After merge, reply on issue #6 in Slovak, asking the reporter to update and
  confirm.
- Reinstall from `main` (`py install.py`) so the daily widget is the fixed one.
  Until then it's still the old copy in `%LOCALAPPDATA%\TerminalWidget`.

## Done when

Every checklist item behaves as the design predicts, the docs say what was
found, and the PR is open.
