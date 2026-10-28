# Step 2: Win32 adapter

**Goal:** bring `Win32Shell` and `create()` in line with step 1. Teach
`classify()` app versus shell, add the stack check and the raise, give the
layer a `QTimer`, and remove `place_above()`.

**Files:** `terminal_widget/desktop_layer.py` (below `# -- Windows`),
`tests/test_desktop_layer.py`.

---

## 2.1 Constants and declarations

Add:

```python
HWND_TOPMOST = -1          # used only by raise_topmost()
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
DWMWA_CLOAKED = 14
```

`HWND_NOTOPMOST` stays undefined. Update the comment that says topmost
constants are "deliberately absent" to say why `HWND_TOPMOST` is now there
(design, decision 3) and why `HWND_NOTOPMOST` still isn't (`HWND_BOTTOM` ends a
raise).

New declarations in `_bind_user32()`:

- `IsWindowVisible(HWND) -> BOOL`
- `IsIconic(HWND) -> BOOL`, only if the stack check ends up needing it (2.3)
- `GetWindowLongPtrW` is already declared, from round one's workaround. Keep it.
- `dwmapi.DwmGetWindowAttribute(HWND, DWORD, c_void_p, DWORD) -> HRESULT`. Bind
  `dwmapi` alongside `user32`. If it fails to load, treat every window as not
  cloaked rather than failing.

`_bind_user32()` returns what `Win32Shell` needs. If it grows a third item,
switch it to returning a small namespace object rather than a longer tuple.

## 2.2 `classify(hwnd, widget)`

Same order as now, with the last branch split:

1. `0` → `NONE`
2. `== widget` → `SELF`
3. same process → `OWN`
4. desktop class with DefView → `DESKTOP`
5. class in `SHELL_CLASSES` → `SHELL`
6. `_is_app_window(hwnd)` → `APP`
7. otherwise → `SHELL`

```python
#: Shell UI that comes forward during Show desktop without ending it.
SHELL_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd",       # taskbars
    "NotifyIconOverflowWindow",                      # tray overflow (Win10)
    "TopLevelWindowForOverflowXamlIsland",           # tray overflow (Win11)
    "Windows.UI.Core.CoreWindow",                    # Start, search, flyouts
    "XamlExplorerHostIslandWindow",                  # Win11 shell flyouts
    "ForegroundStaging", "MultitaskingViewFrame",    # Alt+Tab / Win+Tab
})
```

Put the class check in a pure helper, `classify_foreign(class_name,
is_app_window) -> Kind`, next to `is_desktop_class()`, so the rule is tested
without Windows.

`_is_app_window(hwnd)`, the usual Alt+Tab test:

- `IsWindowVisible(hwnd)`, and not cloaked;
- the extended style has no `WS_EX_TOOLWINDOW` and no `WS_EX_NOACTIVATE`;
- `GetWindow(hwnd, GW_OWNER)` is NULL, **or** the extended style has
  `WS_EX_APPWINDOW`.

## 2.3 `desktop_above(widget, desktop) -> bool`

Walk up from the widget with `GetWindow(h, GW_HWNDPREV)` until the walk runs
out (`False`) or meets `desktop` (`True`). There are a few hundred top-level
windows at most, and this runs at most 40 times per desktop event.

- If `widget` or `desktop` is 0, return `False`.
- Cap the walk at 10,000 steps. A z-order that changes during the walk can't
  loop forever then, and running out counts as `False`.

Visibility isn't checked. The question is only "which of these two windows is
higher", and the desktop window is visible whenever it matters.

## 2.4 `raise_topmost(widget)`

`SetWindowPos(widget, HWND_TOPMOST, 0, 0, 0, 0, _PLACE_FLAGS)`. Same flags as
every other placement, so it never takes focus.

## 2.5 `place_at_bottom(widget)`

Unchanged, but its comment now says why it's also the way down from a raise.
The `SetWindowPos` documentation says that for `HWND_BOTTOM`, *"if the hWnd
parameter identifies a topmost window, the window loses its topmost status and
is placed at the bottom of all other windows."* Step 4 confirms it on the
machine.

## 2.6 Remove `place_above()`

Delete `place_above()` and `_is_topmost()`, together with the five
`test_above_the_desktop_*` / `test_already_directly_above_*` /
`test_to_the_bottom_*` tests and the `FakeUser32` that served them.
`GetWindowLongPtrW` stays declared, since `_is_app_window` uses it.

## 2.7 The ticker in `create()`

```python
def create(widget_hwnd):
    ...
    from PySide6.QtCore import QTimer

    def make_ticker(callback):
        timer = QTimer()
        timer.setInterval(WATCH_INTERVAL_MS)
        timer.timeout.connect(callback)
        return timer          # QTimer already has start() and stop()

    return DesktopLayer(shell, widget_hwnd, make_ticker)
```

The import stays inside `create()`, so the platform-free half of the module
still has no Qt dependency for step 1's tests. No parent is needed: the layer
holds the timer, and the window holds the layer.

## 2.8 Tests

Against a fake `user32` object, as round one did for `place_above`:

- `classify_foreign()`: every `SHELL_CLASSES` entry is `SHELL` even when it
  looks like an app; an unknown class is `APP` or `SHELL` per the flag.
- `_is_app_window`: one test per rule (hidden, cloaked, tool window,
  no-activate, owned, owned with `WS_EX_APPWINDOW`, plain unowned visible).
- `desktop_above`: desktop directly above, several windows above, not above
  at all, either handle 0, and the step cap.
- `raise_topmost` calls `SetWindowPos` with `HWND_TOPMOST` and the
  no-activate flags.
- Replace `test_no_topmost_constant_exists` with: `HWND_NOTOPMOST` is not
  defined, and `HWND_TOPMOST` appears in exactly one function (`inspect`
  `getsource` on the module, counting uses).
- `create()` off Windows still returns `None`, and still never imports Qt.

## Done when

The full suite and `pyright` pass on Linux, and the probe from round one
(`watch_foreground`) runs on Windows with the new `classify()`. Taskbar clicks
should show as `SHELL` and app windows as `APP`.
