# Step 1: Decision logic

**Goal:** rework the platform-free half of `terminal_widget/desktop_layer.py`
for the second design: app versus shell, watching after a desktop event, and
raising. Still no Win32 and no Qt in this half; all of it is tested on Linux.

**Files:** `terminal_widget/desktop_layer.py` (above the `# -- Windows` line),
`tests/test_desktop_layer.py`, `tests/conftest.py`.

---

## 1.1 Types

```python
class Kind(Enum):
    NONE = auto()      # no window (mid-switch)
    DESKTOP = auto()   # WorkerW/Progman holding SHELLDLL_DefView
    SELF = auto()      # the widget's own window
    OWN = auto()       # another window of this process (tray menu)
    APP = auto()       # a window Alt+Tab would list      (was part of OTHER)
    SHELL = auto()     # taskbar, Start, flyouts, overlays (was part of OTHER)

@dataclass(frozen=True)
class Foreground:
    hwnd: int
    kind: Kind

@dataclass(frozen=True)
class Tick:
    """One watch check: is the desktop window above the widget right now?"""
    desktop_above: bool

class Action(Enum):
    LEAVE = auto()
    RAISE = auto()       # always-on-top              (replaces ABOVE_DESKTOP)
    TO_BOTTOM = auto()   # HWND_BOTTOM; also ends always-on-top

@dataclass(frozen=True)
class State:
    revealed: bool = False   # unchanged meaning
    raised: bool = False     # we made the widget always-on-top
    desktop: int = 0         # the desktop window being watched
    ticks_left: int = 0      # > 0 while watching

    @property
    def watching(self) -> bool:
        return self.ticks_left > 0

#: Watch every 50 ms for 2 s after the desktop comes forward.
WATCH_INTERVAL_MS = 50
WATCH_TICKS = 40
```

`OTHER` goes away. Every use of it in code and tests becomes `APP` or `SHELL`.

## 1.2 `decide(event: Foreground | Tick, state: State) -> tuple[Action, State]`

**Foreground changes:**

| `kind` | condition | Action | New state |
| --- | --- | --- | --- |
| `NONE`, `OWN`, `SHELL` | | `LEAVE` | unchanged |
| `DESKTOP` | `raised` | `LEAVE` | unchanged |
| `DESKTOP` | not raised | `LEAVE` | `desktop=hwnd`, `ticks_left=WATCH_TICKS` (restarts a watch already running) |
| `SELF` | `revealed` or `raised` | `LEAVE` | unchanged |
| `SELF` | otherwise | `TO_BOTTOM` | unchanged (a running watch carries on) |
| `APP` | | `TO_BOTTOM` | `State()` |

**Ticks:**

| condition | Action | New state |
| --- | --- | --- |
| not watching | `LEAVE` | unchanged (a stray tick after the watch ended) |
| `desktop_above` | `RAISE` | `raised=True`, `ticks_left=0` |
| otherwise | `LEAVE` | `ticks_left - 1` |

Notes on the rows that matter:

- **`SHELL` → `LEAVE`** is what lets the taskbar and Start menu come forward
  during Show desktop without ending it. The first design sent the widget to
  the bottom here.
- **`SELF` while watching but not yet raised → `TO_BOTTOM`, watch continues.**
  If the user clicks the widget in the instant before Explorer lifts the
  desktop, the next tick still raises it.
- **`APP` clears `raised` with `TO_BOTTOM`.** `HWND_BOTTOM` ends always-on-top
  by itself (step 2), so no separate "lower" action is needed.

## 1.3 `DesktopLayer`

Changes from round one:

- **Constructor** gains a ticker factory:

  ```python
  class Ticker(Protocol):
      def start(self) -> None: ...   # repeat every WATCH_INTERVAL_MS until stopped
      def stop(self) -> None: ...

  DesktopLayer(shell, widget_hwnd, make_ticker: Callable[[Callable[[], None]], Ticker])
  ```

  The layer calls `make_ticker(self.on_tick)` once, in the constructor.
- **`Shell` protocol:** drop `place_above`; add
  `desktop_above(widget: int, desktop: int) -> bool` and
  `raise_topmost(widget: int) -> None`.
- **`on_foreground(hwnd)`:** as before, then `_after(old_state)`: start the
  ticker if a watch just began, and **tick once at once** so a desktop that's
  already above doesn't wait 50 ms. Stop the ticker if the watch ended.
- **New `on_tick()`:** if disabled or not watching, stop the ticker and return.
  Otherwise ask `shell.desktop_above(widget, state.desktop)`, `decide()` a
  `Tick`, apply the action, stop the ticker if the watch ended. Same one-line
  stderr `try/except` as `on_foreground`.
- **Applying actions:** `RAISE` → `shell.raise_topmost(widget)`; `TO_BOTTOM` →
  `shell.place_at_bottom(widget)`.
- **Switching off** (`enabled = False`): stop the ticker; **if raised, call
  `place_at_bottom(widget)` first**, then reset to `State()`. This keeps
  Windows and Qt in agreement whenever the layer stops looking: config mode, a
  restack (the call lands on the old window just before it's destroyed), hide,
  and close.
- **`close()`:** stop the ticker, lower if raised (via switching off), then
  uninstall as before.
- `resync()` and `reveal()`: unchanged.

## 1.4 Fakes in `tests/conftest.py`

- `FakeShell`:
  - unknown hwnds classify as `APP`, not `OTHER`;
  - a settable `above` flag that `desktop_above()` returns, and a record of
    what it was asked;
  - `raise_topmost()` records `("raise", widget)`;
  - `place_above()` removed.
- New `FakeTicker`, built by a `make_ticker` the tests pass in: `running` flag,
  `starts` / `stops` counters, and `fire()` that calls the layer's callback
  once. A `fake_ticker` fixture, or a small factory that remembers the last
  ticker it made.

## 1.5 Tests: `tests/test_desktop_layer.py`

Replace round one's `decide()` tests with tests for the new tables, one per
row, plus these sequences:

- **Show desktop:** `DESKTOP` → tick (not above) → tick (above) → `RAISE` →
  watch over, `raised`.
- **The wallpaper:** `DESKTOP` → 40 ticks, never above → no `RAISE`, watch
  over, nothing moved.
- **Ending a raise:** raised → `SHELL` (taskbar) → still raised → `APP` →
  `TO_BOTTOM`, `State()`.
- **Start menu during Show desktop:** raised → `SHELL` → `DESKTOP` → still
  raised, no new watch.
- **Clicking the widget mid-watch:** watching → `SELF` → `TO_BOTTOM`, still
  watching → tick (above) → `RAISE`.
- **A reveal:** unchanged from round one, with `APP` in place of `OTHER`.
- **A stray tick** with no watch → `LEAVE`, nothing changes.
- **A second `DESKTOP` mid-watch** restarts the countdown.

`DesktopLayer` with `FakeShell` and the fake ticker: keep round one's tests
(adjusted for `APP`), and add:

- a desktop event starts the ticker **and** ticks once at once (a desktop
  already above raises with no `fire()`);
- the ticker stops when a raise happens, when 40 ticks pass, and when an app
  comes forward;
- `fire()` while disabled does nothing and stops the ticker;
- **switching off while raised lowers first**: calls `("bottom", widget)`,
  then the state is clean;
- switching off when not raised moves nothing;
- `close()` while raised lowers, stops the ticker and uninstalls;
- an exception from `desktop_above` or `raise_topmost` doesn't escape
  `on_tick`.

## Done when

`tests/test_desktop_layer.py` passes against the new logic, nothing in the
platform-free half refers to `OTHER`, `ABOVE_DESKTOP` or `place_above`, and
`pyright` is clean. (Step 2 hasn't updated `Win32Shell` yet, so commit 1 and 2
together if `pyright` objects to the protocol mismatch in between.)
