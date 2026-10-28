# Plans: surviving "Show desktop" on Windows

Implementation plans for the design in [`docs/show-desktop.md`](../../show-desktop.md),
which fixes GitHub issue #6. Read the design first: these plans say *how* and
*in what order*, and don't repeat the *why*.

Branch: `issue-6-show-desktop`.

## The steps

| # | Plan | Touches | Can be checked on Linux |
| --- | --- | --- | --- |
| 1 | [Decision logic](01-decision-logic.md) | new `terminal_widget/desktop_layer.py` (pure part), new `tests/test_desktop_layer.py` | fully |
| 2 | [Win32 adapter](02-win32-adapter.md) | `desktop_layer.py` (Windows part) | imports and pyright only |
| 3 | [Window integration](03-window-integration.md) | `terminal_widget/window.py`, `tests/test_window.py` | fully, with a fake adapter |
| 4 | [Verify on Windows and close out](04-verify-on-windows.md) | `issues.md`, `README.md`, the PR | no, needs a real Windows desktop |

Each step builds on the one before it. Commit after each step, with the full
test suite passing; step 4 is mostly hand testing and documentation.

## The shape of it

```
            Windows                          platform-free
 ┌──────────────────────────┐      ┌───────────────────────────────┐
 │ Win32Shell (step 2)      │      │ decide() (step 1)             │
 │  SetWinEventHook ────────┼─fg──▶│  (foreground, state)          │
 │  GetClassNameW, …        │      │    -> (action, new state)     │
 │  SetWindowPos  ◀─────────┼──────┤                               │
 └──────────────────────────┘ act  │ DesktopLayer (step 1)         │
                                   │  holds state, enabled flag    │
                                   └──────────────▲────────────────┘
                                                  │ enable / resync / reveal
                                   ┌──────────────┴────────────────┐
                                   │ WidgetWindow (step 3)         │
                                   └───────────────────────────────┘
```

Everything that decides is pure Python and testable on Linux. The Win32 part
only reports what it sees and carries out what it's told.

## Done when

- The full suite passes on Linux: `.venv/bin/python -m pytest`.
- `pyright` is clean. The project checks with `pythonPlatform = "All"`, so the
  `ctypes.windll` code has to type-check from Linux too.
- Every box in the hand checklist in step 4 is ticked on a real Windows machine.
- `issues.md` has an entry for this, and the PR says `Fixes #6`.

## Ground rules from the design

- Never touch `HWND_TOPMOST` / `HWND_NOTOPMOST`. Only the position within the
  normal band changes, so Qt's `windowFlags()` stays the truth.
- No change to window flags, geometry or opacity code.
- No new dependencies. `ctypes` only.
- On anything other than Windows, behaviour is byte-for-byte what it is today.
