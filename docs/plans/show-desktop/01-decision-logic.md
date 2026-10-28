# Step 1: Decision logic

**Goal:** all of the "where should the widget be now?" reasoning, as pure Python
with no Win32 and no Qt, fully tested on Linux.

**Files:** new `terminal_widget/desktop_layer.py` (this step writes only its
platform-free half), new `tests/test_desktop_layer.py`.

---

## 1.1 Types

```python
class Kind(Enum):
    NONE = auto()       # GetForegroundWindow() returned NULL (mid-switch)
    DESKTOP = auto()    # WorkerW/Progman holding a SHELLDLL_DefView
    SELF = auto()       # the widget's own top-level window
    OWN = auto()        # another window of this process (the tray menu, …)
    OTHER = auto()      # anything else

@dataclass(frozen=True)
class Foreground:
    hwnd: int
    kind: Kind

class Action(Enum):
    LEAVE = auto()          # do nothing
    ABOVE_DESKTOP = auto()  # place directly above State.desktop
    TO_BOTTOM = auto()      # HWND_BOTTOM

@dataclass(frozen=True)
class State:
    revealed: bool = False   # set by bring_to_front(), see the design
    desktop: int = 0         # hwnd of the desktop while it is "in front", else 0
```

HWNDs are plain `int`s here: opaque handles, compared and passed back, never
looked into.

## 1.2 `decide(fg: Foreground, state: State) -> tuple[Action, State]`

| `fg.kind` | condition | Action | New state |
| --- | --- | --- | --- |
| `NONE` | | `LEAVE` | unchanged |
| `OWN` | | `LEAVE` | unchanged |
| `DESKTOP` | | `ABOVE_DESKTOP` | `revealed=False, desktop=fg.hwnd` |
| `SELF` | `state.revealed` | `LEAVE` | unchanged |
| `SELF` | `state.desktop` set | `ABOVE_DESKTOP` | unchanged |
| `SELF` | otherwise | `TO_BOTTOM` | unchanged |
| `OTHER` | | `TO_BOTTOM` | `State()` (both cleared) |

Three rows go beyond the table in the design doc, and each fixes something:

- **`SELF` while the desktop is in front → `ABOVE_DESKTOP`, not `TO_BOTTOM`.**
  After Show desktop, clicking the widget to type in it must not sink it under
  the desktop window that is still covering everything, or it disappears
  again: issue #6 in a new form. Placing it above the remembered desktop is
  right whether or not Explorer has left show-desktop mode by then. If it has,
  "above the desktop" is just the bottom of the stack.
- **`OWN` → `LEAVE`.** Opening the tray menu makes one of this process's hidden
  Qt windows the foreground window. Treating that as `OTHER` would sink the
  widget under the desktop while the menu is open, and clear `desktop`.
- **`NONE` → `LEAVE`.** Windows briefly reports no foreground window during
  some switches. It says nothing, so nothing should happen.

## 1.3 `is_desktop_class(class_name: str, has_defview: bool) -> bool`

`class_name in {"WorkerW", "Progman"} and has_defview`. A one-liner, but kept
separate so the Win32 adapter's classification is tested here rather than only
by hand.

## 1.4 `DesktopLayer`

The stateful holder the window talks to. Platform-free: it takes the adapter as
a constructor argument, so tests pass a fake.

```python
class DesktopLayer:
    def __init__(self, shell: Shell, widget_hwnd: Callable[[], int]) -> None
    enabled: bool                      # property; see below
    def on_foreground(self, hwnd: int) -> None
    def resync(self) -> None
    def reveal(self) -> None
    def close(self) -> None
```

`Shell` is a `Protocol` describing what step 2 provides:

```python
class Shell(Protocol):
    def install(self, callback: Callable[[int], None]) -> bool
    def uninstall(self) -> None
    def foreground(self) -> int
    def classify(self, hwnd: int, widget: int) -> Kind
    def place_above(self, widget: int, desktop: int) -> None
    def place_at_bottom(self, widget: int) -> None
```

Behaviour:

- **`enabled = True`**, the first time: calls `shell.install(self.on_foreground)`.
  If that returns `False`, the layer stays disabled for good and says so once on
  stderr. The widget then behaves exactly as it does today. Turning `enabled`
  off doesn't uninstall the hook; the callback just returns early. Setting it to
  `True` again calls `resync()`.
- **`on_foreground(hwnd)`**: if disabled, return. Otherwise read
  `widget = widget_hwnd()`, `kind = shell.classify(hwnd, widget)`, run
  `decide()`, store the new state, and carry out the action
  (`place_above(widget, state.desktop)` or `place_at_bottom(widget)`). Wrap the
  body in `try/except Exception` that writes one line to stderr. An exception
  escaping a ctypes callback is printed and dropped by ctypes anyway, so this
  only makes it readable.
- **`resync()`**: reset to `State()`, then `on_foreground(shell.foreground())`.
  Called whenever `enabled` turns on, so the widget lands in the right place
  without waiting for the next event.
- **`reveal()`**: set `revealed=True`. Doesn't move anything itself;
  `bring_to_front()` does the moving.
- **`close()`**: `shell.uninstall()` if installed. Safe to call twice.

## 1.5 Tests: `tests/test_desktop_layer.py`

Table tests for `decide()`, one per row, plus these sequences:

- Show desktop → click widget → stays `ABOVE_DESKTOP`; → open an app →
  `TO_BOTTOM`, state cleared.
- reveal → `SELF` → `LEAVE`; → `SELF` again → still `LEAVE` (one reveal can
  produce more than one foreground event for the widget); → `OTHER` →
  `TO_BOTTOM` and `revealed` cleared; → `SELF` → `TO_BOTTOM` (the reveal is
  over).
- Show desktop → tray menu (`OWN`) → `LEAVE`, `desktop` still remembered.
- `NONE` in the middle of any sequence changes nothing.

`DesktopLayer` with a `FakeShell` that records calls:

- disabled: `on_foreground` does nothing and calls nothing;
- first enable installs once; a second enable doesn't install again;
- an install that fails leaves it permanently disabled;
- enable calls `resync`, which queries `foreground()` and acts on it;
- the widget HWND is read on every event (change what the fake returns between
  events; the second placement uses the new value), which is the restack case;
- an exception raised from the fake's `place_*` doesn't escape `on_foreground`;
- `close()` uninstalls, and a second `close()` is harmless.

`is_desktop_class`: both classes with the DefView child, both without it, and an
unrelated class with it.

## Done when

`tests/test_desktop_layer.py` passes, the rest of the suite is untouched, and
`desktop_layer.py` imports on Linux with no Windows-only import at module level.
