# Step 4: No Qt bottom hint on Windows

**Goal:** the fix the experiment proved. While a watcher exists, the `desktop`
layer has no `WindowStaysOnBottomHint`, and the watcher keeps the widget at the
bottom itself. Design: *Without Qt's bottom hint* and decision 4 in
[`docs/show-desktop.md`](../../show-desktop.md).

**Files:** `terminal_widget/desktop_layer.py`, `terminal_widget/window.py`,
`tests/test_desktop_layer.py`, `tests/test_window.py`.

---

## 4.1 Install the hook when the watcher is created

"A watcher exists" has to mean "the watcher is doing the hint's job". So the
hook can't be allowed to fail later, when it's too late to put the hint back
without a restack.

- `DesktopLayer` gets a public `install() -> bool`, which does what
  `_ensure_installed()` does now: install once, print the one-line message
  once on failure.
- `create()` calls `layer.install()` and returns `None` if it fails. The window
  then falls back to Qt's hint, exactly as off Windows.
- `enabled = True` keeps calling `_ensure_installed()`, so a layer built
  directly (as the tests do) still installs on first use.

## 4.2 Switching on sends the widget to the bottom

In the `enabled` setter, after installing and before `resync()`:
`place_at_bottom(widget)`, guarded like every other call. This replaces what
Qt's hint did when the native window was created, and it runs:

- when the widget is first shown;
- when settings closes (config mode ends);
- when the layer setting changes back to `desktop`;
- after a restack, when the watcher switches back on with the new window.

`resync()` may then send it to the bottom a second time (an app in front).
That's harmless, and not worth a special case.

## 4.3 `window.py`

**`_stacking_hint()`:**

```python
if self._config_mode:
    return _NO_HINT
hint = _STACKING_HINTS.get(self._config.stacking, BOTTOM)
if hint == BOTTOM and self._desktop_layer is not None:
    # Qt rewrites every stacking change of a bottom-hint window to
    # HWND_BOTTOM, which would undo the watcher (docs/show-desktop.md).
    # The watcher keeps the widget at the bottom instead.
    return _NO_HINT
return hint
```

`__init__` already creates `_desktop_layer` before the first
`setWindowFlags()`, so the very first native window is built without the hint.
Check this stays true.

**`_sync_desktop_layer()`** can no longer read the hint, so it reads the
setting:

```python
self._desktop_layer.enabled = (
    self.isVisible()
    and not self._restacking
    and not self._config_mode
    and self._config.stacking == STACKING_DESKTOP
)
```

**`apply_config()`:** on a `stacking` change, call `_sync_desktop_layer()`
after `_restack()`. On Windows, `desktop` → `normal` no longer changes the
flags, so `_restack()` returns early and nothing else would switch the watcher
off. `normal` → `desktop` needs it switched back on, which sends the widget to
the bottom.

**`bring_to_front()`:** update the docstring. `raise_()` is no longer a no-op
on Windows when there's a watcher, only when there isn't (the hint fallback).

No other change. `set_config_mode()` already syncs at its end. The restack
spy tests still hold for `desktop` ↔ `top`, which still changes flags.

## 4.4 Tests

`tests/test_desktop_layer.py`:

- switching on calls `("bottom", widget)` first, even with nothing in front;
- switching on with an app in front: bottom, then bottom again (no special
  case);
- `install()` succeeds once and is idempotent; on failure it prints once and
  returns `False`;
- `create()` with a fake `Win32Shell` whose install fails returns `None`.
  Monkeypatch `is_windows` and `Win32Shell` in the module.

`tests/test_window.py`, with the `layered` fixture:

- **the `desktop` layer has no bottom hint when a watcher exists**
  (`not windowFlags() & BOTTOM`), and still has it without one: the existing
  `test_the_default_layer_is_behind_other_windows`, on Linux, unchanged;
- **entering config mode doesn't restack** with a watcher (spy on
  `setWindowFlags`: not called), and still lowers a raised widget;
- **`desktop` → `normal` switches the watcher off** without a restack;
  `normal` → `desktop` switches it on, and the first call is `("bottom", hwnd)`;
- **`desktop` → `top` still restacks**, with the watcher off during it (the
  existing spy test);
- the round-two test `test_config_mode_while_raised_lowers_before_the_restack`
  becomes "config mode while raised lowers it". There's no restack to come
  before any more.

## 4.5 The debug launcher

Remove the `nohint` experiment block from `run_debug.py`. The product does
this itself now.

## Done when

The full suite and `pyright` pass, and `run_debug.py` without `nohint`
behaves as the experiment did: Show desktop leaves the widget visible.
