# Step 5: The Show desktop delay setting

**Goal:** a setting for how long after Show desktop starts the widget waits
before coming up on top, so it appears as the minimise animation ends rather
than during it. Design: *The Show desktop delay* and decision 5 in
[`docs/show-desktop.md`](../../show-desktop.md).

**Files:** `terminal_widget/desktop_layer.py`, `terminal_widget/config.py`,
`terminal_widget/window.py`, `terminal_widget/settings/__main__.py`, and the
tests for each.

---

## 5.1 The watch counts time from the event

`State.ticks_left` can't express "not before X, and give up after Y". Replace
it with:

```python
@dataclass(frozen=True)
class State:
    revealed: bool = False
    raised: bool = False
    desktop: int = 0        # the desktop being watched; 0 when not watching
    elapsed: int = 0        # ticks since the desktop came forward
    raise_after: int = 0    # ticks of delay for this watch

    @property
    def watching(self) -> bool:
        return self.desktop != 0
```

`decide(event, state, raise_after=0)` takes the delay in ticks. Only the
`DESKTOP` row reads it, storing it in the new watch. So a watch keeps the delay
it started with, even if the setting changes mid-watch.

**Foreground rows:** as now, except that `DESKTOP` (not raised) starts
`State(revealed=state.revealed, desktop=hwnd, elapsed=0,
raise_after=raise_after)`. Every row that ended a watch now clears `desktop`
rather than `ticks_left`.

**Tick rows**, each looking at the current `elapsed`, then moving it on:

| condition | Action | New state |
| --- | --- | --- |
| not watching | `LEAVE` | unchanged |
| `desktop_above` and `elapsed >= raise_after` | `RAISE` | `raised=True`, watch over |
| `elapsed + 1 >= raise_after + WATCH_TICKS` | `LEAVE` | watch over |
| otherwise | `LEAVE` | `elapsed + 1` |

The check made at once when the watch starts is the tick at `elapsed == 0`.
The first timer tick is `elapsed == 1`, 50 ms later. So with a delay of 0 the
behaviour is exactly what was proven in the experiment. With 250 ms
(`raise_after == 5`) the widget comes up on the first tick at or after 250 ms
where the desktop is above it.

So a watch makes at most `raise_after + WATCH_TICKS` checks: 40 with no
delay, as before. (Written as `>` at first, which would have made 41.)

`raised` stays separate from `watching`, as now. "Watch over" means `desktop=0,
elapsed=0, raise_after=0`.

## 5.2 `DesktopLayer.raise_delay_ms`

A property, default 0, clamped at 0. The layer converts it to ticks for
`decide()` with `ceil(ms / WATCH_INTERVAL_MS)`. Rounding up means "at least
this long", which is the promise the setting makes.

## 5.3 Config

`terminal_widget/config.py`:

```python
#: How long after Show desktop starts the widget waits before coming up on
#: top (Windows only). About the length of the minimise animation, so the
#: widget appears as the windows finish shrinking rather than over them.
show_desktop_delay_ms: int = 250
```

`clamped()`: `min(2000, max(0, int(...)))`, next to `scrollback`. The IPC
layer and the JSON file pick the field up through `dataclasses.fields` and
`asdict`. A config file without it loads with the default. Check both rather
than assume.

## 5.4 The window passes it on

- In `__init__`, right after `create_desktop_layer(...)`, if there's a layer:
  `layer.raise_delay_ms = config.show_desktop_delay_ms`.
- In `apply_config()`, if it changed: the same. No restack, no resync.

## 5.5 Settings app

In `_build_behaviour()`, right after the *Window layer* row and its hint:

- `self.spin_show_desktop_delay = QSpinBox(minimum=0, maximum=2000,
  singleStep=50)`, suffix `" ms"`, `setSpecialValueText("Immediately")`,
  `valueChanged` → `_on_ui_changed`.
- Row label: "Show desktop delay". Hint below it: *"How long to wait, after
  Show desktop or Win+D, before the widget comes up on top. Set it to about
  the length of your minimise animation, so the widget appears as the windows
  finish shrinking."*
- **Enabled** only while *Window layer* is "Behind other windows". Update it
  from the combo's `currentIndexChanged`, and once in `_load_into_ui()`.
- **Visible** only on Windows (`platform_info.is_windows()`). Hide the spin
  box, its label and its hint together. Keep the label and hint widgets in
  attributes so they can be hidden; `QFormLayout.setRowVisible` needs Qt 6.4+,
  which PySide6 6.6 has, so that's the simpler way.
- `_load_into_ui()` sets it from the config; the collect step reads it into
  `show_desktop_delay_ms`.

## 5.6 Tests

- `test_desktop_layer.py`: rewrite the watch tests for `elapsed` /
  `raise_after`.
  - Delay 0 behaves as before.
  - Delay 5: the desktop is above from tick 0, and the widget is raised at tick
    5, not before.
  - The desktop rises at tick 8 with delay 5: raised at tick 8.
  - The wallpaper with delay 5: gives up after `5 + WATCH_TICKS`.
  - An app at tick 3 of 5: never raised.
  - The setting changes mid-watch: the running watch keeps its delay.
  - `raise_delay_ms` rounds up (`1` → 1 tick, `50` → 1, `51` → 2) and clamps
    negatives to 0.
- `test_config.py`: default 250, clamping both ends, and a JSON round trip.
- `test_settings.py`: the spin box loads and saves the value. It's disabled
  for the `normal` and `top` layers and enabled for `desktop`. Visibility
  follows a monkeypatched `is_windows`.
- `test_window.py`: the layer gets the configured delay at construction, and
  the new one after `apply_config()`.

## Done when

The full suite and `pyright` pass. Then on the target machine: setting the
delay to the length of the minimise animation makes the widget appear as the
animation ends (step 6).
