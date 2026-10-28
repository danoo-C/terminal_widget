# Step 3: Window integration

**Goal:** `WidgetWindow` owns one `DesktopLayer`, turns it on exactly when the
widget is visible on the `desktop` layer, and tells it about reveals and
shutdown. About 20 lines in `window.py`.

**Files:** `terminal_widget/window.py`, `tests/test_window.py`, and possibly
`tests/conftest.py` for a fixture.

---

## 3.1 Construction

In `WidgetWindow.__init__`, next to the other plain fields and **before** the
flags are set, so no show or hide event can arrive before the field exists:

```python
self._desktop_layer = create_desktop_layer(lambda: int(self.winId()))
```

Import `create` from `.desktop_layer` as `create_desktop_layer` at module level
in `window.py`, so tests can replace it with `monkeypatch.setattr(window_module,
"create_desktop_layer", …)` **before** the window is built. Off Windows it
returns `None`, and every call below is guarded by `if self._desktop_layer is
not None`.

The HWND is a callable, not a value, because `_restack()` destroys and
recreates the native window (`window.py:354`).

## 3.2 One place that decides "enabled"

```python
def _sync_desktop_layer(self) -> None:
    if self._desktop_layer is None:
        return
    self._desktop_layer.enabled = (
        self.isVisible()
        and not self._restacking
        and self._stacking_hint() == Qt.WindowType.WindowStaysOnBottomHint
    )
```

`_stacking_hint()` already folds in config mode (`window.py:335`), so config
mode, `normal` and `top` all come out as disabled without being listed here.

Call it from:

| Where | Why |
| --- | --- |
| `showEvent` (new override, call `super()` first) | the first show; `winId()` exists from here on |
| the end of `_restack()`, **after** the `finally` clears `_restacking` | the HWND has just changed, and the layer may have too |
| the end of `set_config_mode()` | `_restack()` is a no-op when the flags don't change (e.g. the `normal` layer), but config mode still changed |
| `hideEvent` (new override) | a hidden widget shouldn't be moved around |

`apply_config()` needs nothing extra: a stacking change goes through
`_restack()`.

The layer also has to be off *during* a restack, while the old HWND is gone and
the new one isn't shown yet. `_restacking` is `True` for exactly that span, so
call `_sync_desktop_layer()` once more right **after** `self._restacking = True`
(`window.py:372`). It reads the flag and switches the layer off, and the call at
the end switches it back on.

## 3.3 Reveal

In `bring_to_front()`, before `activateWindow()`:

```python
if self._desktop_layer is not None:
    self._desktop_layer.reveal()
```

Tray Show, tray click and the second-launch IPC path all go through
`bring_to_front()` (`tray.py:99,126,133`, `__main__.py:107`), so this one line
covers all of them.

## 3.4 Shutdown

In `closeEvent`, after the session is stopped and before `closed` is emitted:

```python
if self._desktop_layer is not None:
    self._desktop_layer.close()
```

## 3.5 Tests in `tests/test_window.py`

A fixture, `layered`, that monkeypatches `create_desktop_layer` to build a real
`DesktopLayer` over step 1's `FakeShell` (move the fake into `conftest.py` or a
small `tests/fakes.py` so both test files share it), then builds and shows a
window. Default stacking is `desktop`.

- **Off Windows by default:** with no monkeypatch, `window._desktop_layer is
  None`, and the existing suite is unaffected.
- **Shown on the desktop layer** → enabled, hook installed, `resync` ran.
- **Config mode on** → disabled; **off** → enabled again, and `resync` ran.
- **`normal` and `top` layers** (via the `replace_stacking` helper at
  `tests/test_window.py:323`) → disabled; back to `desktop` → enabled.
- **A layer change in config mode** stays disabled until config mode ends,
  matching `test_a_layer_change_in_config_mode_waits_for_it_to_end`.
- **During a restack the layer is disabled:** have the fake record `enabled` at
  the moment `setWindowFlags` runs (wrap it with monkeypatch), and assert it was
  `False`.
- **Hidden** → disabled.
- **`bring_to_front()`** sets `revealed` on the layer.
- **A foreground event from the fake** for a `DESKTOP` hwnd produces a
  `place_above` with the widget's current `int(winId())`.
- **`close()`** uninstalls the hook before `closed` fires (connect to `closed`
  and check the fake's state inside the slot).

## Done when

The full suite passes on Linux, the existing window tests pass untouched, and
`pyright` is clean.
