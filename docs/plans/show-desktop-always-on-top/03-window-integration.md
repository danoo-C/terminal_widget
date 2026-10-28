# Step 3: Window integration check

**Goal:** confirm round one's `WidgetWindow` integration still holds for the
second design, and test the new interplay between a raise and the window's own
moments: config mode, a restack, hide, `bring_to_front()` and close. Little or
no production code is expected to change here.

**Files:** `tests/test_window.py`; `terminal_widget/window.py` only if a test
below shows a gap.

---

## 3.1 What should already be right

`_sync_desktop_layer()` switches the layer off for config mode, the `normal`
and `top` layers, hide and the middle of a restack, and back on afterwards.
Step 1 makes "switching off while raised" lower the widget first. So every way
out of a raise that goes through the window is already covered:

| Window moment | Layer sees | Raise ends by |
| --- | --- | --- |
| Settings opens (config mode) | switched off, before the restack | `place_at_bottom`, on the old native window |
| Layer changed to `normal` or `top` | off, before the restack | same |
| Hidden | off | `place_at_bottom` |
| Closed | `close()` | `place_at_bottom`, then unhook |

The restack then builds a fresh native window, which has no always-on-top
anyway. Qt's flags and Windows agree again.

`bring_to_front()` while raised marks a reveal; the widget is already in front.
Nothing to change.

## 3.2 The `layered` fixture

Round one's fixture builds `DesktopLayer(fake_shell, hwnd)`. Give it the fake
ticker factory from step 1 as the third argument, and expose the ticker:
`yield w, layer, shell, ticker`. Update the round one tests that unpack three
values.

## 3.3 New tests in `tests/test_window.py`

Each starts by raising the widget through the fake: `shell.above = True`, then
`shell.bring(DESKTOP_HWND)`, and asserts `layer.state.raised`.

- **Config mode while raised:** `set_config_mode(True)` → the shell's last
  call is `("bottom", old_hwnd)`, recorded *before* `setWindowFlags` runs. Spy
  on it as round one's restack test does. The layer is off, and the state is
  clean.
- **Leaving config mode afterwards:** the layer is back on, not raised, and
  resynced against the current foreground.
- **A layer change to `top` while raised:** same as config mode.
- **Hide while raised** → `("bottom", hwnd)`.
- **Close while raised** → `("bottom", hwnd)` before the unhook, and both
  before `closed` fires.
- **`bring_to_front()` while raised** → no placement call, and `revealed` is
  set.
- **The ticker never runs while the layer is off:** start a watch, switch to
  config mode, assert the fake ticker is stopped. Then `fire()` it anyway, and
  nothing moves.

## 3.4 If a test fails

The likely gap is ordering inside `_restack()`. The layer must be switched off
(and so lower the widget) **before** `setWindowFlags`, while the old native
window still exists. Round one already put `_sync_desktop_layer()` right after
`self._restacking = True`, so that should hold. If it doesn't, fix the order
there rather than adding a second lowering path.

## Done when

The full suite passes, round one's window tests pass with only the fixture
change, and `pyright` is clean.
