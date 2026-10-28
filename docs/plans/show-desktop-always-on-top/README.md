# Plans: always-on-top while the desktop is shown

Implementation plans for the **second** design in
[`docs/show-desktop.md`](../../show-desktop.md), which fixes GitHub issue #6.
Read the design first, especially *The design* and *Decisions*.

Branch: `issue-6-show-desktop`.

## Where this starts from

The first design's plans ([`../show-desktop/`](../show-desktop/README.md)) got
through steps 1–3, and step 4 failed on the target machine. This round **changes**
that code; it doesn't start over:

| Built in round one | This round |
| --- | --- |
| `decide()`, `State`, `Kind`, `Action` | reworked: app/shell split, watch ticks, raise |
| `DesktopLayer` (enable, resync, reveal, close) | kept, plus a ticker and "lower when switched off while raised" |
| `Win32Shell` hook, declarations, `classify()` | kept; `classify()` learns app versus shell |
| `Win32Shell.place_above()` and its topmost workaround | **removed** |
| `WidgetWindow` integration (`_sync_desktop_layer`, reveal, close) | kept unchanged |
| Tests in `test_desktop_layer.py` and `test_window.py` | decision and placement tests rewritten; window tests kept |

The `place_above()` topmost workaround and its five tests were written during
round one's step 4 and are uncommitted at the time of writing. They're
superseded here: commit them with the docs as a record, then remove them in
step 2.

## The steps

| # | Plan | Touches | Can be checked on Linux |
| --- | --- | --- | --- |
| # | Plan | Touches | Can be checked on Linux | State |
| --- | --- | --- | --- | --- |
| 1 | [Decision logic](01-decision-logic.md) | `desktop_layer.py` (pure part), tests | fully | built, `a618134` |
| 2 | [Win32 adapter](02-win32-adapter.md) | `desktop_layer.py` (Windows part), tests | the faked parts | built, `a618134` |
| 3 | [Window integration check](03-window-integration.md) | `tests/test_window.py` | fully, with fakes | built, `a618134` |
| 4 | [No Qt bottom hint on Windows](04-no-qt-bottom-hint.md) | `desktop_layer.py`, `window.py`, tests | fully, with fakes | to do |
| 5 | [Show desktop delay setting](05-show-desktop-delay.md) | `desktop_layer.py`, `config.py`, `window.py`, settings app, tests | fully | to do |
| 6 | [Verify on Windows and close out](06-verify-on-windows.md) | the debug launcher, `issues.md`, `README.md`, the PR | no, needs the real desktop | to do |

Commit after each step, with the full suite and `pyright` passing.

**Steps 4 and 5 were added after steps 1–3 failed on the target machine.** Qt
rewrites every stacking change of a window with `WindowStaysOnBottomHint` to
`HWND_BOTTOM`, which silently undid every raise. With the hint removed (an
experiment in the debug launcher), the design worked, and the user asked for a
delay so the widget doesn't come up before the minimise animation ends. See
*Cause* and *History* in the design doc.

## The shape of it

```
            Windows                              platform-free
 ┌───────────────────────────────┐      ┌──────────────────────────────────┐
 │ Win32Shell                    │      │ decide(event, state)             │
 │  foreground hook ─────────────┼─fg──▶│   event = Foreground | Tick      │
 │  classify: desktop/self/own/  │      │   -> (action, new state)         │
 │            app/shell          │      │                                  │
 │  desktop_above(widget, desk) ─┼─tick▶│ DesktopLayer                     │
 │  raise_topmost / to bottom ◀──┼──────┤   state, enabled, ticker         │
 └───────────────────────────────┘ act  └───────────────▲──────────────────┘
                                                        │ 50 ms, while watching
                                         ┌──────────────┴───────┐
                                         │ ticker (QTimer)      │
                                         └──────────────────────┘
```

## Done when

- Full suite passes on Linux, and `pyright` is clean.
- Every box in the design doc's hand checklist is ticked on the target machine,
  using the debug launcher.
- `issues.md` has the entry, and the PR says `Fixes #6`.

## Ground rules

- On Windows, while a watcher exists, the `desktop` layer never carries Qt's
  `WindowStaysOnBottomHint` (step 4). Without a watcher it always does.
- `HWND_TOPMOST` is used in exactly one place, `raise_topmost()`.
  `HWND_NOTOPMOST` is never used; `HWND_BOTTOM` ends a raise.
- The ticker runs only while watching. No background polling.
- No change to window flags, geometry or opacity code.
- No new dependencies. `ctypes`, plus the `QTimer` the app already has.
- Off Windows, behaviour is unchanged.
