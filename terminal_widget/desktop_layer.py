"""Keeping the desktop layer visible through "Show desktop" on Windows.

The ``desktop`` layer is a single ``SetWindowPos(HWND_BOTTOM)`` that Qt makes
when the native window is created. Show desktop (and Win+D) brings Explorer's
desktop window to the top of the stack, and a widget at the bottom ends up
underneath it until some other window comes forward (GitHub issue #6).

The fix is what Rainmeter does: watch which window is in the foreground, and
place the widget directly above the desktop when the desktop comes forward and
back at the bottom when anything else does. It only ever moves the widget
within the normal window band; it never makes it topmost, so Qt's
``windowFlags()`` stays the truth. The design is in ``docs/show-desktop.md``.

This module is split in two. :func:`decide` and :class:`DesktopLayer` make
every decision, in plain Python with no Win32, so they are tested on any OS.
The Win32 part only reports what Windows says and carries out what it is told,
behind the :class:`Shell` protocol.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Protocol


class Kind(Enum):
    """What sort of window just came to the foreground."""

    #: No window at all. Windows reports this briefly during some switches.
    NONE = auto()
    #: Explorer's desktop: a WorkerW or Progman holding the icon view.
    DESKTOP = auto()
    #: The widget's own top-level window.
    SELF = auto()
    #: Another window of this process, such as the tray menu.
    OWN = auto()
    #: Anything else.
    OTHER = auto()


@dataclass(frozen=True)
class Foreground:
    hwnd: int
    kind: Kind


class Action(Enum):
    """What to do with the widget after a foreground change."""

    LEAVE = auto()
    #: Directly above :attr:`State.desktop` in the stack.
    ABOVE_DESKTOP = auto()
    #: ``HWND_BOTTOM``, where Qt put it to begin with.
    TO_BOTTOM = auto()


@dataclass(frozen=True)
class State:
    #: Set by a reveal (tray Show, second launch), so the activation that
    #: follows is not mistaken for a click and undone. Cleared when another
    #: window comes forward.
    revealed: bool = False
    #: The desktop window while it is the last thing to have come forward,
    #: else 0. Remembered so that clicking the widget during Show desktop
    #: keeps it above the desktop instead of sinking it back underneath.
    desktop: int = 0


#: Explorer window classes that can hold the desktop's icon view. Windows 11
#: 24H2 moved the view from a WorkerW to Progman, so both are checked.
DESKTOP_CLASSES = frozenset({"WorkerW", "Progman"})


def is_desktop_class(class_name: str, has_defview: bool) -> bool:
    """Whether a window is the desktop, from its class and its children.

    The ``SHELLDLL_DefView`` child is what tells the desktop apart: Explorer
    keeps several other WorkerW windows that are not it.
    """
    return class_name in DESKTOP_CLASSES and has_defview


def decide(fg: Foreground, state: State) -> tuple[Action, State]:
    """Where the widget should go, given what just came to the foreground."""
    if fg.kind in (Kind.NONE, Kind.OWN):
        # A mid-switch NULL says nothing. Our own windows -- the tray menu --
        # are not the user leaving the desktop, and sinking the widget while
        # the menu is open would hide it under the desktop again.
        return Action.LEAVE, state
    if fg.kind is Kind.DESKTOP:
        return Action.ABOVE_DESKTOP, State(desktop=fg.hwnd)
    if fg.kind is Kind.SELF:
        if state.revealed:
            # Activation put it in front on purpose; leave it there until
            # another window comes forward. Not spent here: one reveal can
            # produce more than one foreground event for the widget, and the
            # second must not undo the first.
            return Action.LEAVE, state
        if state.desktop:
            # Clicked during Show desktop. "Above the desktop" is right whether
            # or not Explorer has left show-desktop mode by now: if it has,
            # the desktop is back at the bottom and so is the widget.
            return Action.ABOVE_DESKTOP, state
        return Action.TO_BOTTOM, state
    return Action.TO_BOTTOM, State()


class Shell(Protocol):
    """What the desktop layer needs from the windowing system."""

    def install(self, callback: Callable[[int], None]) -> bool:
        """Call ``callback(hwnd)`` on every foreground change. True if hooked."""
        ...

    def uninstall(self) -> None: ...

    def foreground(self) -> int:
        """The current foreground window, or 0."""
        ...

    def classify(self, hwnd: int, widget: int) -> Kind: ...

    def place_above(self, widget: int, desktop: int) -> None: ...

    def place_at_bottom(self, widget: int) -> None: ...


class DesktopLayer:
    """Keeps the widget where the ``desktop`` layer means it to be.

    The window turns it on while the widget is visible on the desktop layer
    and off otherwise -- config mode, the other layers, mid-restack. The hook
    is installed the first time it turns on and stays installed until
    :meth:`close`; while off, events are simply ignored.
    """

    def __init__(self, shell: Shell, widget_hwnd: Callable[[], int]) -> None:
        self._shell = shell
        # A callable rather than a value: a restack destroys and recreates
        # the native window, so the handle has to be read fresh every time.
        self._widget_hwnd = widget_hwnd
        self._state = State()
        self._enabled = False
        self._installed = False
        self._failed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, on: bool) -> None:
        if on == self._enabled:
            # The window re-asserts this from several places. Re-running
            # resync on each would reset the state, a pending reveal included.
            return
        if on and not self._ensure_installed():
            return
        self._enabled = on
        if on:
            self.resync()

    def _ensure_installed(self) -> bool:
        if self._installed:
            return True
        if self._failed:
            return False
        if self._shell.install(self.on_foreground):
            self._installed = True
            return True
        # Said once, and then the widget simply behaves as it did before
        # this existed: correct everywhere except through Show desktop.
        self._failed = True
        print(
            "terminal-widget: could not watch the foreground window; "
            "Show desktop will hide the widget",
            file=sys.stderr,
        )
        return False

    @property
    def state(self) -> State:
        return self._state

    def on_foreground(self, hwnd: int) -> None:
        """React to a foreground change. Called by the shell's hook."""
        if not self._enabled:
            return
        try:
            widget = self._widget_hwnd()
            fg = Foreground(hwnd, self._shell.classify(hwnd, widget))
            action, self._state = decide(fg, self._state)
            if action is Action.ABOVE_DESKTOP:
                self._shell.place_above(widget, self._state.desktop)
            elif action is Action.TO_BOTTOM:
                self._shell.place_at_bottom(widget)
        except Exception as exc:
            # ctypes would print and drop it anyway; this makes it one line.
            print(f"terminal-widget: desktop layer: {exc!r}", file=sys.stderr)

    def resync(self) -> None:
        """Start over from whatever is in the foreground right now."""
        self._state = State()
        self.on_foreground(self._shell.foreground())

    def reveal(self) -> None:
        """Let the next activation of the widget stand. See ``bring_to_front``."""
        self._state = replace(self._state, revealed=True)

    def close(self) -> None:
        self._enabled = False
        if self._installed:
            self._installed = False
            self._shell.uninstall()
