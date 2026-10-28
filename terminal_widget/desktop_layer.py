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

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Any, Protocol

from .platform_info import is_windows


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


# -- Windows ------------------------------------------------------------

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
GW_HWNDPREV = 3
HWND_TOP = 0
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
#: Every placement keeps the widget's size, position and focus: only its
#: place in the stack changes.
_PLACE_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

# HWND_TOPMOST and HWND_NOTOPMOST are deliberately absent. Leaving the normal
# window band would make the real window style disagree with Qt's
# windowFlags() -- the reason a ctypes restack was turned down before
# (issues.md, "The ctypes fast path") -- and it could put the widget over a
# fullscreen game.

#: Class names are capped at 256 characters.
_CLASS_NAME_LEN = 256


class Win32Shell:
    """The real :class:`Shell`: ``user32`` through ctypes, and nothing else.

    Thin on purpose. It reports what Windows says and does what it is told;
    every decision lives in :func:`decide`, where it can be tested.

    The hook is out-of-context, so Windows delivers events through the
    message queue of the thread that installed it. That is Qt's GUI thread,
    pumped by ``app.exec()``, so the callback runs between Qt events and needs
    no locking.
    """

    def __init__(self) -> None:
        self._user32, self._proc_type = _bind_user32()
        self._hook: int | None = None
        # The ctypes wrapper around the callback. It must outlive the hook:
        # if it were collected while hooked, Windows would call freed memory
        # and take the process down.
        self._proc: Any = None
        self._pid = os.getpid()

    def install(self, callback: Callable[[int], None]) -> bool:
        def on_event(hook, event, hwnd, id_object, id_child, thread, time_ms):
            callback(int(hwnd or 0))

        proc = self._proc_type(on_event)
        # No WINEVENT_SKIPOWNPROCESS: the widget's own foreground events are
        # what tell a click from a reveal.
        hook = self._user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            return False
        self._hook, self._proc = hook, proc
        return True

    def uninstall(self) -> None:
        if self._hook is not None:
            self._user32.UnhookWinEvent(self._hook)
        # Only now, with no hook left to call it.
        self._hook = self._proc = None

    def foreground(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def classify(self, hwnd: int, widget: int) -> Kind:
        if not hwnd:
            return Kind.NONE
        if hwnd == widget:
            return Kind.SELF
        if self._process_of(hwnd) == self._pid:
            return Kind.OWN
        has_defview = bool(
            self._user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        )
        if is_desktop_class(self._class_name(hwnd), has_defview):
            return Kind.DESKTOP
        return Kind.OTHER

    def place_above(self, widget: int, desktop: int) -> None:
        # Inserting after the window just above the desktop puts the widget
        # directly on top of it. After a click on the wallpaper the desktop
        # is still at the bottom, so the widget stays behind every app.
        prev = int(self._user32.GetWindow(desktop, GW_HWNDPREV) or 0)
        if prev == widget:
            return
        if prev and self._is_topmost(prev):
            # Topmost windows always sit above every normal one, so a topmost
            # window directly above the desktop means no normal window is:
            # Show desktop, where the taskbar is what sits above it. Going in
            # after a topmost window would make the widget topmost too, so
            # take the top of the normal band instead.
            prev = 0
        self._set_pos(widget, prev or HWND_TOP)

    def _is_topmost(self, hwnd: int) -> bool:
        return bool(self._user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)

    def place_at_bottom(self, widget: int) -> None:
        self._set_pos(widget, HWND_BOTTOM)

    def _set_pos(self, widget: int, insert_after: int) -> None:
        # A failure is not reported: the next foreground change places the
        # widget again, and shouting from inside a hook helps nobody.
        self._user32.SetWindowPos(widget, insert_after, 0, 0, 0, 0, _PLACE_FLAGS)

    def _process_of(self, hwnd: int) -> int:
        import ctypes
        from ctypes import wintypes

        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _class_name(self, hwnd: int) -> str:
        import ctypes

        buf = ctypes.create_unicode_buffer(_CLASS_NAME_LEN)
        self._user32.GetClassNameW(hwnd, buf, _CLASS_NAME_LEN)
        return buf.value


def _bind_user32() -> tuple[Any, Any]:
    """``user32`` with every function this module uses declared.

    Declared in full rather than called bare: ctypes' default return type is
    a C ``int``, which silently truncates a 64-bit window handle.
    """
    if sys.platform != "win32":
        raise OSError("user32 exists only on Windows")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    hwnd, dword, uint = wintypes.HWND, wintypes.DWORD, wintypes.UINT
    hook_handle = wintypes.HANDLE

    proc_type = ctypes.WINFUNCTYPE(
        None, hook_handle, dword, hwnd, wintypes.LONG, wintypes.LONG, dword, dword
    )

    def declare(name: str, restype: Any, *argtypes: Any) -> None:
        fn = getattr(user32, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)

    declare(
        "SetWinEventHook",
        hook_handle,
        uint, uint, wintypes.HMODULE, proc_type, dword, dword, uint,
    )
    declare("UnhookWinEvent", wintypes.BOOL, hook_handle)
    declare("GetForegroundWindow", hwnd)
    declare("GetClassNameW", ctypes.c_int, hwnd, wintypes.LPWSTR, ctypes.c_int)
    declare("FindWindowExW", hwnd, hwnd, hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR)
    declare("GetWindowThreadProcessId", dword, hwnd, ctypes.POINTER(dword))
    declare("GetWindow", hwnd, hwnd, uint)
    if not hasattr(user32, "GetWindowLongPtrW"):
        # 32-bit user32 has no Ptr variant; there it is a macro for this.
        setattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    declare("GetWindowLongPtrW", ctypes.c_ssize_t, hwnd, ctypes.c_int)
    declare(
        "SetWindowPos",
        wintypes.BOOL,
        hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, uint,
    )
    return user32, proc_type


def create(widget_hwnd: Callable[[], int]) -> DesktopLayer | None:
    """The desktop layer for this platform, or None where none is needed.

    Only Windows gets one: there the bottom hint is a single ``SetWindowPos``
    that nothing maintains. X11 has ``_NET_WM_STATE_BELOW``, which the window
    manager holds, and Show desktop on Linux is out of scope for issue #6.
    A binding failure also means None, so the widget behaves as it did
    before this existed rather than not starting.
    """
    if not is_windows():
        return None
    try:
        shell = Win32Shell()
    except OSError as exc:
        print(f"terminal-widget: desktop layer unavailable: {exc}", file=sys.stderr)
        return None
    return DesktopLayer(shell, widget_hwnd)
