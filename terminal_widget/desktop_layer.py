"""Keeping the desktop layer visible through "Show desktop" on Windows.

The ``desktop`` layer is a single ``SetWindowPos(HWND_BOTTOM)`` that Qt makes
when the native window is created. On Show desktop (and Win+D) Explorer makes
its desktop window the foreground window and then lifts it above every normal
window, the widget included, until an app comes forward again (GitHub issue
#6).

The fix is what Rainmeter does. When the desktop comes forward, watch the
window stack for a couple of seconds; if the desktop rises above the widget,
that is Show desktop, and the widget is made always-on-top. When an app comes
forward, it goes back to the bottom, which also ends always-on-top.

On Windows this replaces Qt's ``WindowStaysOnBottomHint``, which can't stay:
Qt rewrites every stacking change of such a window to ``HWND_BOTTOM``, and so
silently undid every raise until it was dropped. The design and that history
are in ``docs/show-desktop.md``.

This module is split in two. :func:`decide` and :class:`DesktopLayer` make
every decision, in plain Python with no Win32 and no Qt, so they are tested on
any OS. The Win32 part only reports what Windows says and carries out what it
is told, behind the :class:`Shell` protocol.
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
    #: A window Alt+Tab would list. One coming forward ends Show desktop.
    APP = auto()
    #: Anything else: the taskbar, Start, flyouts, overlays. These come
    #: forward *during* Show desktop -- a click on the Show desktop corner
    #: starts with the taskbar -- so they must not end it.
    SHELL = auto()


@dataclass(frozen=True)
class Foreground:
    """A foreground change."""

    hwnd: int
    kind: Kind


@dataclass(frozen=True)
class Tick:
    """One watch check: is the desktop window above the widget right now?"""

    desktop_above: bool


class Action(Enum):
    LEAVE = auto()
    #: Always-on-top, over the shown desktop.
    RAISE = auto()
    #: ``HWND_BOTTOM``, where Qt put it to begin with. Also ends a raise.
    TO_BOTTOM = auto()


#: After the desktop comes forward, check the stack this often...
WATCH_INTERVAL_MS = 50
#: ...for this many checks (2 s) after the Show desktop delay. Explorer lifts
#: the desktop a moment *after* it becomes the foreground window, so one look
#: at the event is not enough.
WATCH_TICKS = 40


@dataclass(frozen=True)
class State:
    #: Set by a reveal (tray Show, second launch), so the activation that
    #: follows is not mistaken for a click and undone. Cleared when an app
    #: comes forward.
    revealed: bool = False
    #: The widget is always-on-top because the desktop is shown.
    raised: bool = False
    #: The desktop window being watched; 0 when not watching.
    desktop: int = 0
    #: Checks made in this watch so far. The one made at once is check 0, and
    #: each timer tick after it is one more, WATCH_INTERVAL_MS apart.
    elapsed: int = 0
    #: The Show desktop delay for this watch, in checks. Fixed when the watch
    #: starts, so changing the setting mid-watch does not move the goalposts.
    raise_after: int = 0

    @property
    def watching(self) -> bool:
        return self.desktop != 0

    def watch_over(self) -> State:
        return replace(self, desktop=0, elapsed=0, raise_after=0)


#: Explorer window classes that can hold the desktop's icon view. Windows 11
#: 24H2 moved the view from a WorkerW to Progman, so both are checked.
DESKTOP_CLASSES = frozenset({"WorkerW", "Progman"})

#: Shell UI that can come forward during Show desktop without ending it,
#: whatever its window styles say.
SHELL_CLASSES = frozenset(
    {
        "Shell_TrayWnd",  # the taskbar
        "Shell_SecondaryTrayWnd",  # the taskbar on other monitors
        "NotifyIconOverflowWindow",  # tray overflow, Windows 10
        "TopLevelWindowForOverflowXamlIsland",  # tray overflow, Windows 11
        "Windows.UI.Core.CoreWindow",  # Start, search, notification flyouts
        "XamlExplorerHostIslandWindow",  # Windows 11 shell flyouts
        "ForegroundStaging",  # Alt+Tab and Win+Tab, in passing
        "MultitaskingViewFrame",  # Alt+Tab
    }
)


def is_desktop_class(class_name: str, has_defview: bool) -> bool:
    """Whether a window is the desktop, from its class and its children.

    The ``SHELLDLL_DefView`` child is what tells the desktop apart: Explorer
    keeps several other WorkerW windows that are not it.
    """
    return class_name in DESKTOP_CLASSES and has_defview


def classify_foreign(class_name: str, is_app_window: bool) -> Kind:
    """APP or SHELL, for a window that is not ours and not the desktop."""
    if class_name in SHELL_CLASSES:
        return Kind.SHELL
    return Kind.APP if is_app_window else Kind.SHELL


def decide(
    event: Foreground | Tick, state: State, raise_after: int = 0
) -> tuple[Action, State]:
    """What to do with the widget, given what just happened.

    ``raise_after`` is the Show desktop delay in watch checks. Only a desktop
    coming forward reads it, to start a watch with it.
    """
    if isinstance(event, Tick):
        return _decide_tick(event, state)
    return _decide_foreground(event, state, raise_after)


def _decide_foreground(
    fg: Foreground, state: State, raise_after: int
) -> tuple[Action, State]:
    if fg.kind in (Kind.NONE, Kind.OWN, Kind.SHELL):
        # A mid-switch NULL says nothing. The tray menu, the taskbar and the
        # Start menu are not the user leaving the desktop.
        return Action.LEAVE, state
    if fg.kind is Kind.DESKTOP:
        if state.raised:
            return Action.LEAVE, state
        # Explorer has not lifted the desktop yet, if it is going to: a click
        # on the wallpaper brings it forward without lifting it at all. A
        # second desktop event mid-watch starts the count again.
        return Action.LEAVE, replace(
            state.watch_over(), desktop=fg.hwnd, raise_after=max(0, raise_after)
        )
    if fg.kind is Kind.SELF:
        if state.revealed or state.raised:
            # Revealed: activation put it in front on purpose, and one reveal
            # can produce more than one foreground event. Raised: clicked
            # during Show desktop, where it must stay visible.
            return Action.LEAVE, state
        # Clicked while an app covers it: it stays below the app (decision 1).
        # A watch in progress carries on, so a click in the instant before
        # Explorer lifts the desktop is still followed by a raise.
        return Action.TO_BOTTOM, state
    # An app: Show desktop is over, if it was on. HWND_BOTTOM also ends a raise.
    return Action.TO_BOTTOM, State()


def _decide_tick(tick: Tick, state: State) -> tuple[Action, State]:
    if not state.watching:
        return Action.LEAVE, state
    if tick.desktop_above and state.elapsed >= state.raise_after:
        # Show desktop, and the minimise animation has had its time.
        return Action.RAISE, replace(state.watch_over(), raised=True)
    if state.elapsed + 1 >= state.raise_after + WATCH_TICKS:
        # The desktop never rose: a click on the wallpaper, most likely.
        return Action.LEAVE, state.watch_over()
    return Action.LEAVE, replace(state, elapsed=state.elapsed + 1)


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

    def desktop_above(self, widget: int, desktop: int) -> bool:
        """Whether ``desktop`` is higher in the window stack than ``widget``."""
        ...

    def raise_topmost(self, widget: int) -> None: ...

    def place_at_bottom(self, widget: int) -> None: ...


class Ticker(Protocol):
    """Calls back every :data:`WATCH_INTERVAL_MS` while started."""

    def start(self) -> None: ...

    def stop(self) -> None: ...


class DesktopLayer:
    """Keeps the widget where the ``desktop`` layer means it to be.

    The window turns it on while the widget is visible on the desktop layer
    and off otherwise -- config mode, the other layers, mid-restack. The hook
    is installed the first time it turns on and stays installed until
    :meth:`close`; while off, events are simply ignored. The ticker runs only
    while a watch is in progress.
    """

    def __init__(
        self,
        shell: Shell,
        widget_hwnd: Callable[[], int],
        make_ticker: Callable[[Callable[[], None]], Ticker],
    ) -> None:
        self._shell = shell
        # A callable rather than a value: a restack destroys and recreates
        # the native window, so the handle has to be read fresh every time.
        self._widget_hwnd = widget_hwnd
        self._ticker = make_ticker(self.on_tick)
        self._ticking = False
        self._state = State()
        self._enabled = False
        self._installed = False
        self._failed = False
        self._raise_delay_ms = 0

    @property
    def raise_delay_ms(self) -> int:
        """The Show desktop delay: how long after the desktop comes forward
        the widget waits before coming up on top. Applies from the next watch."""
        return self._raise_delay_ms

    @raise_delay_ms.setter
    def raise_delay_ms(self, ms: int) -> None:
        self._raise_delay_ms = max(0, int(ms))

    def _raise_after_ticks(self) -> int:
        # Rounded up: the setting promises *at least* this long.
        return -(-self._raise_delay_ms // WATCH_INTERVAL_MS)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, on: bool) -> None:
        if on == self._enabled:
            # The window re-asserts this from several places. Re-running
            # resync on each would reset the state, a pending reveal included.
            return
        if not on:
            self._switch_off()
            return
        if not self.install():
            return
        self._enabled = True
        # Without Qt's bottom hint (docs/show-desktop.md, decision 4) nothing
        # else puts the widget at the bottom: not startup, not the end of
        # config mode, not a fresh native window after a restack.
        self._guarded(lambda: self._shell.place_at_bottom(self._widget_hwnd()))
        self.resync()

    def _switch_off(self) -> None:
        self._enabled = False
        self._set_ticking(False)
        if self._state.raised:
            # Whenever the layer stops looking -- config mode, a restack, a
            # hide, close -- the widget goes back to where Qt believes it is.
            # Before a restack this lands on the old native window, just
            # before it is replaced by one that was never always-on-top.
            self._guarded(lambda: self._shell.place_at_bottom(self._widget_hwnd()))
        self._state = State()

    def install(self) -> bool:
        """Install the foreground hook, once. False if Windows refused it.

        ``create()`` calls this up front, so that a watcher that exists is one
        that works: the window drops Qt's bottom hint only when it has one.
        """
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

        def step() -> None:
            widget = self._widget_hwnd()
            fg = Foreground(hwnd, self._shell.classify(hwnd, widget))
            self._apply(decide(fg, self._state, self._raise_after_ticks()), widget)
            if fg.kind is Kind.DESKTOP and self._state.watching:
                # Look at once: a desktop that is already up should not wait
                # a tick to be answered.
                self.on_tick()

        self._guarded(step)
        self._set_ticking(self._state.watching)

    def on_tick(self) -> None:
        """One watch check. Called by the ticker, and once when a watch starts."""
        if self._enabled and self._state.watching:

            def step() -> None:
                widget = self._widget_hwnd()
                above = self._shell.desktop_above(widget, self._state.desktop)
                self._apply(decide(Tick(above), self._state), widget)

            if not self._guarded(step):
                # A check that throws once will throw forty times; end the
                # watch rather than print every 50 ms for two seconds.
                self._state = self._state.watch_over()
        self._set_ticking(self._enabled and self._state.watching)

    def _apply(self, result: tuple[Action, State], widget: int) -> None:
        action, self._state = result
        if action is Action.RAISE:
            self._shell.raise_topmost(widget)
        elif action is Action.TO_BOTTOM:
            self._shell.place_at_bottom(widget)

    def _set_ticking(self, on: bool) -> None:
        if on and not self._ticking:
            self._ticker.start()
        elif not on and self._ticking:
            self._ticker.stop()
        self._ticking = on

    @staticmethod
    def _guarded(step: Callable[[], None]) -> bool:
        """Run ``step``; report an exception in one line instead of raising.

        Exceptions would otherwise escape into a ctypes callback or a Qt
        slot, which print them at length and carry on anyway.
        """
        try:
            step()
        except Exception as exc:
            print(f"terminal-widget: desktop layer: {exc!r}", file=sys.stderr)
            return False
        return True

    def resync(self) -> None:
        """Start over from whatever is in the foreground right now."""
        self._set_ticking(False)
        self._state = State()
        self.on_foreground(self._shell.foreground())

    def reveal(self) -> None:
        """Let the next activation of the widget stand. See ``bring_to_front``."""
        self._state = replace(self._state, revealed=True)

    def close(self) -> None:
        if self._enabled:
            self._switch_off()
        self._set_ticking(False)
        if self._installed:
            self._installed = False
            self._shell.uninstall()


# -- Windows ------------------------------------------------------------

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
GW_HWNDPREV = 3
GW_OWNER = 4
GWL_EXSTYLE = -20
HWND_BOTTOM = 1
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
DWMWA_CLOAKED = 14
#: Every placement keeps the widget's size, position and focus: only its
#: place in the stack changes.
_PLACE_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

# HWND_TOPMOST is used in one place, raise_topmost(), and only while the
# desktop is shown: no normal-band placement survives Explorer lifting the
# desktop (docs/show-desktop.md, decision 3). HWND_NOTOPMOST is deliberately
# absent: HWND_BOTTOM ends always-on-top by itself, so there is one way down.

#: Class names are capped at 256 characters.
_CLASS_NAME_LEN = 256
#: A z-order that changes during a walk cannot keep it going forever.
_MAX_WALK = 10_000


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
        api = _bind()
        self._user32 = api.user32
        self._dwmapi = api.dwmapi
        self._proc_type = api.proc_type
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
        name = self._class_name(hwnd)
        has_defview = bool(
            self._user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        )
        if is_desktop_class(name, has_defview):
            return Kind.DESKTOP
        return classify_foreign(name, self._is_app_window(hwnd))

    def _is_app_window(self, hwnd: int) -> bool:
        """The usual "would Alt+Tab list it?" test."""
        u = self._user32
        if not u.IsWindowVisible(hwnd) or self._cloaked(hwnd):
            return False
        ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return False
        return not u.GetWindow(hwnd, GW_OWNER) or bool(ex & WS_EX_APPWINDOW)

    def _cloaked(self, hwnd: int) -> bool:
        """Hidden by DWM: another virtual desktop, a suspended UWP app."""
        if self._dwmapi is None:
            return False
        import ctypes

        value = ctypes.c_uint32()
        hr = self._dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
        return hr == 0 and value.value != 0

    def desktop_above(self, widget: int, desktop: int) -> bool:
        # Walk up from the widget. Visibility does not matter: the question is
        # only which of the two is higher.
        if not widget or not desktop:
            return False
        h = widget
        for _ in range(_MAX_WALK):
            h = int(self._user32.GetWindow(h, GW_HWNDPREV) or 0)
            if not h:
                return False
            if h == desktop:
                return True
        return False

    def raise_topmost(self, widget: int) -> None:
        self._set_pos(widget, HWND_TOPMOST)

    def place_at_bottom(self, widget: int) -> None:
        # Also the way down from raise_topmost(). SetWindowPos documents that
        # a topmost window sent to HWND_BOTTOM "loses its topmost status and
        # is placed at the bottom of all other windows".
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


@dataclass(frozen=True)
class _Api:
    user32: Any
    #: None if dwmapi would not load; windows then count as not cloaked.
    dwmapi: Any
    proc_type: Any


def _bind() -> _Api:
    """``user32`` and ``dwmapi`` with every function this module uses declared.

    Declared in full rather than called bare: ctypes' default return type is
    a C ``int``, which silently truncates a 64-bit window handle.
    """
    if sys.platform != "win32":
        raise OSError("user32 exists only on Windows")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    hwnd, dword, uint, boolean = (
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.BOOL,
    )
    hook_handle = wintypes.HANDLE

    proc_type = ctypes.WINFUNCTYPE(
        None, hook_handle, dword, hwnd, wintypes.LONG, wintypes.LONG, dword, dword
    )

    def declare(dll: Any, name: str, restype: Any, *argtypes: Any) -> None:
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)

    declare(
        user32,
        "SetWinEventHook",
        hook_handle,
        uint, uint, wintypes.HMODULE, proc_type, dword, dword, uint,
    )
    declare(user32, "UnhookWinEvent", boolean, hook_handle)
    declare(user32, "GetForegroundWindow", hwnd)
    declare(user32, "GetClassNameW", ctypes.c_int, hwnd, wintypes.LPWSTR, ctypes.c_int)
    declare(
        user32, "FindWindowExW", hwnd, hwnd, hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR
    )
    declare(user32, "GetWindowThreadProcessId", dword, hwnd, ctypes.POINTER(dword))
    declare(user32, "GetWindow", hwnd, hwnd, uint)
    declare(user32, "IsWindowVisible", boolean, hwnd)
    if not hasattr(user32, "GetWindowLongPtrW"):
        # 32-bit user32 has no Ptr variant; there it is a macro for this.
        setattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    declare(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, hwnd, ctypes.c_int)
    declare(
        user32,
        "SetWindowPos",
        boolean,
        hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, uint,
    )

    try:
        dwmapi: Any = ctypes.WinDLL("dwmapi")
        declare(
            dwmapi,
            "DwmGetWindowAttribute",
            ctypes.c_long,
            hwnd, dword, ctypes.c_void_p, dword,
        )
    except OSError:
        dwmapi = None

    return _Api(user32=user32, dwmapi=dwmapi, proc_type=proc_type)


def create(widget_hwnd: Callable[[], int]) -> DesktopLayer | None:
    """The desktop layer for this platform, or None where none is needed.

    Only Windows gets one. Elsewhere, and on Windows when the binding or the
    hook fails, the result is None, and the window keeps Qt's bottom hint:
    the widget behaves as it did before this existed rather than not
    starting. Show desktop on Linux is out of scope for issue #6.
    """
    if not is_windows():
        return None
    try:
        shell = Win32Shell()
    except OSError as exc:
        print(f"terminal-widget: desktop layer unavailable: {exc}", file=sys.stderr)
        return None

    # Imported here so the platform-free half of this module stays Qt-free.
    from PySide6.QtCore import QTimer

    def make_ticker(callback: Callable[[], None]) -> Ticker:
        timer = QTimer()
        timer.setInterval(WATCH_INTERVAL_MS)
        timer.timeout.connect(callback)
        return timer

    layer = DesktopLayer(shell, widget_hwnd, make_ticker)
    # Up front: the window decides from this whether to keep Qt's bottom
    # hint, and it cannot take that back later without a restack.
    if not layer.install():
        return None
    return layer
