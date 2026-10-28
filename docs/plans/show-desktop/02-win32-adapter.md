# Step 2: Win32 adapter

**Goal:** `Win32Shell`, the real `Shell` from step 1. It's the only code that
touches `user32`, and it's kept as thin as possible: report what Windows says,
and do what it's told.

**Files:** `terminal_widget/desktop_layer.py` (Windows half), plus a `create()`
factory.

---

## 2.1 Where it lives

Same module, below the platform-free part. Every `ctypes.windll` reference sits
inside `Win32Shell` or a helper it calls, never at module import time, so the
module still imports on Linux (step 1's tests import it).

```python
def create(widget_hwnd: Callable[[], int]) -> DesktopLayer | None:
    """The desktop layer for this platform, or None where none is needed."""
    if not is_windows():
        return None
    return DesktopLayer(Win32Shell(), widget_hwnd)
```

## 2.2 Declarations

Declare `argtypes` and `restype` for every function. This is not optional: on
64-bit Python the default `restype` is a C `int`, which silently truncates
handles. `ctypes.wintypes` gives `HWND`, `HANDLE`, `DWORD`, `LONG`, `UINT`,
`BOOL`, `LPWSTR`.

| Function | Use |
| --- | --- |
| `SetWinEventHook(DWORD, DWORD, HMODULE, WINEVENTPROC, DWORD, DWORD, DWORD) -> HANDLE` | install |
| `UnhookWinEvent(HANDLE) -> BOOL` | uninstall |
| `GetForegroundWindow() -> HWND` | `foreground()` |
| `GetClassNameW(HWND, LPWSTR, int) -> int` | `classify()` |
| `FindWindowExW(HWND, HWND, LPCWSTR, LPCWSTR) -> HWND` | DefView child check |
| `GetWindowThreadProcessId(HWND, POINTER(DWORD)) -> DWORD` | `OWN` check |
| `GetWindow(HWND, UINT) -> HWND` | window just above the desktop |
| `SetWindowPos(HWND, HWND, int, int, int, int, UINT) -> BOOL` | placement |

```python
WINEVENTPROC = WINFUNCTYPE(None, HANDLE, DWORD, HWND, LONG, LONG, DWORD, DWORD)

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT   = 0x0000
GW_HWNDPREV             = 3
HWND_TOP                = 0
HWND_BOTTOM             = 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
```

`HWND_TOPMOST` and `HWND_NOTOPMOST` are deliberately **not** defined, so they
can't creep in later (see the ground rules in the README).

## 2.3 Methods

- **`install(callback)`**: wrap `callback` in a `WINEVENTPROC` that passes on
  only the `hwnd` argument as an `int` (`0` for NULL). **Store the wrapper on
  `self`**: if it's garbage-collected while the hook is live, Windows calls into
  freed memory and the process crashes. Call `SetWinEventHook(FG, FG, None,
  proc, 0, 0, WINEVENT_OUTOFCONTEXT)`, with no `WINEVENT_SKIPOWNPROCESS` (the
  widget's own events are needed). Return whether the handle is non-NULL.
- **`uninstall()`**: `UnhookWinEvent`, then drop both the handle and the
  wrapper, in that order.
- **`foreground()`**: `GetForegroundWindow() or 0`.
- **`classify(hwnd, widget)`**:
  - `0` → `NONE`
  - `hwnd == widget` → `SELF`
  - same process as us (`GetWindowThreadProcessId` vs `os.getpid()`) → `OWN`
  - `is_desktop_class(GetClassNameW(hwnd), FindWindowExW(hwnd, 0,
    "SHELLDLL_DefView", None) != 0)` → `DESKTOP`
  - otherwise → `OTHER`

  A 256-character buffer for the class name; class names are capped at 256.
- **`place_above(widget, desktop)`**: `prev = GetWindow(desktop, GW_HWNDPREV)`.
  If `prev` is the widget itself it's already in place, so return. Otherwise
  `SetWindowPos(widget, prev or HWND_TOP, 0, 0, 0, 0, FLAGS)`. `GetWindow`
  returning NULL means the desktop is at the top of the stack (Show desktop),
  and `HWND_TOP` puts the widget above it.
- **`place_at_bottom(widget)`**: `SetWindowPos(widget, HWND_BOTTOM, 0, 0, 0, 0,
  FLAGS)`.

`FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE`. A `SetWindowPos` that
returns `FALSE` is ignored: the next foreground change tries again, and failing
loudly from inside a hook callback helps nobody.

## 2.4 Threading and re-entrancy

An out-of-context hook delivers events through the message queue of the thread
that installed it, which is Qt's GUI thread, pumped by `app.exec()`. So the
callback runs on the GUI thread, in between Qt events, and needs no locking.
Install it from the GUI thread; `enabled` is only ever set there.

`SetWindowPos` with `SWP_NOACTIVATE` doesn't change the foreground window, so
the callback can't trigger itself.

## 2.5 Checks from Linux

What can't be tested here still has to type-check and import:

- `pyright` is clean. With `pythonPlatform = "All"` it should accept
  `ctypes.windll` and `WINFUNCTYPE`. If it doesn't, narrow with
  `if sys.platform == "win32":` around the declarations rather than adding
  `# type: ignore`.
- `create()` returns `None` on Linux; add that as one test in
  `tests/test_desktop_layer.py`.
- `python -c "import terminal_widget.desktop_layer"` succeeds on Linux.

Everything else in this step is checked by hand in step 4.

## Done when

The full suite and `pyright` pass on Linux, and a Python REPL on Windows can run
`Win32Shell().classify(Win32Shell().foreground(), 0)` and get a sensible `Kind`
for the window that's in front.

## Result (2026-10-28)

Done. A read-only probe with 64-bit Windows Python on the target machine
moved no windows. It found:

- **`Progman` is the desktop on this build.** It's the only candidate holding
  `SHELLDLL_DefView`, so it's the newer (24H2-style) layout. All 14 `WorkerW`
  windows classified as `OTHER`, which confirms the DefView check is needed:
  the class name alone would have matched all 15.
- The foreground window at the time (a browser, `Chrome_WidgetWin_1`) came out
  as `OTHER`; NULL came out as `NONE`; the widget's own handle as `SELF`.
- `SetWinEventHook` installed and `UnhookWinEvent` removed cleanly.
- `create()` returned a real `DesktopLayer`.

Still open, for step 4: which window actually takes the foreground during
Win+D. Given the above, `Progman` is expected.
