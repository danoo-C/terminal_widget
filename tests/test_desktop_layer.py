"""Tests for keeping the desktop layer visible through Show desktop.

All platform-free: the decisions are pure, and the Win32 side is either the
FakeShell from conftest or Win32Shell over a fake user32. What Windows
actually does is checked by hand, per
docs/plans/show-desktop-always-on-top/04-verify-on-windows.md.
"""

import ctypes
import inspect
import sys

import pytest

from terminal_widget import desktop_layer
from terminal_widget.desktop_layer import (
    SHELL_CLASSES,
    WATCH_TICKS,
    Action,
    DesktopLayer,
    Foreground,
    Kind,
    State,
    Tick,
    Win32Shell,
    classify_foreign,
    create,
    decide,
    is_desktop_class,
)

WIDGET, DESKTOP, APP, TASKBAR, MENU = 0x100, 0x200, 0x300, 0x400, 0x500

HWNDS = {
    Kind.NONE: 0,
    Kind.DESKTOP: DESKTOP,
    Kind.SELF: WIDGET,
    Kind.OWN: MENU,
    Kind.APP: APP,
    Kind.SHELL: TASKBAR,
}


def fg(kind):
    return Foreground(HWNDS[kind], kind)


ABOVE, BELOW = Tick(desktop_above=True), Tick(desktop_above=False)


def run(*events, state=State()):
    """Feed events (Kinds for foreground changes, or Ticks) through decide()."""
    actions = []
    for event in events:
        action, state = decide(fg(event) if isinstance(event, Kind) else event, state)
        actions.append(action)
    return actions, state


WATCHING = State(desktop=DESKTOP, ticks_left=WATCH_TICKS)
RAISED = State(raised=True, desktop=DESKTOP)
REVEALED = State(revealed=True)
ANY_STATE = [State(), WATCHING, RAISED, REVEALED]


# -- decide(): foreground changes, one row each ------------------------------


@pytest.mark.parametrize("kind", [Kind.NONE, Kind.OWN, Kind.SHELL])
@pytest.mark.parametrize("state", ANY_STATE)
def test_no_window_our_own_windows_and_the_shell_change_nothing(kind, state):
    assert decide(fg(kind), state) == (Action.LEAVE, state)


def test_the_desktop_starts_a_watch_and_moves_nothing():
    assert decide(fg(Kind.DESKTOP), State()) == (Action.LEAVE, WATCHING)


def test_the_desktop_while_raised_changes_nothing():
    assert decide(fg(Kind.DESKTOP), RAISED) == (Action.LEAVE, RAISED)


def test_the_desktop_again_mid_watch_restarts_the_countdown():
    half_done = State(desktop=DESKTOP, ticks_left=5)
    assert decide(fg(Kind.DESKTOP), half_done) == (Action.LEAVE, WATCHING)


@pytest.mark.parametrize("state", [REVEALED, RAISED])
def test_the_widget_revealed_or_raised_is_left_alone(state):
    assert decide(fg(Kind.SELF), state) == (Action.LEAVE, state)


def test_the_widget_clicked_otherwise_goes_back_to_the_bottom():
    assert decide(fg(Kind.SELF), State()) == (Action.TO_BOTTOM, State())


def test_the_widget_clicked_mid_watch_keeps_the_watch():
    assert decide(fg(Kind.SELF), WATCHING) == (Action.TO_BOTTOM, WATCHING)


@pytest.mark.parametrize("state", ANY_STATE)
def test_an_app_sends_it_to_the_bottom_and_clears_everything(state):
    assert decide(fg(Kind.APP), state) == (Action.TO_BOTTOM, State())


# -- decide(): watch ticks ---------------------------------------------------


@pytest.mark.parametrize("tick", [ABOVE, BELOW])
@pytest.mark.parametrize("state", [State(), RAISED, REVEALED])
def test_a_tick_with_no_watch_changes_nothing(tick, state):
    assert decide(tick, state) == (Action.LEAVE, state)


def test_the_desktop_above_the_widget_raises_it_and_ends_the_watch():
    assert decide(ABOVE, WATCHING) == (Action.RAISE, RAISED)


def test_the_desktop_still_below_counts_down():
    action, state = decide(BELOW, WATCHING)
    assert action is Action.LEAVE
    assert state.ticks_left == WATCH_TICKS - 1


# -- decide(): sequences -----------------------------------------------------


def test_show_desktop():
    """Explorer lifts the desktop a moment after it comes forward."""
    actions, state = run(Kind.DESKTOP, BELOW, BELOW, ABOVE)
    assert actions[-1] is Action.RAISE
    assert state == RAISED


def test_a_click_on_the_wallpaper_never_raises():
    """The desktop comes forward but stays under the widget: the watch runs
    out, and nothing moves."""
    actions, state = run(Kind.DESKTOP, *[BELOW] * WATCH_TICKS, BELOW)
    assert set(actions) == {Action.LEAVE}
    assert not state.watching and not state.raised


def test_the_taskbar_does_not_end_a_raise_and_the_app_after_it_does():
    """A click on an app's taskbar button: the taskbar comes forward first."""
    actions, state = run(Kind.SHELL, Kind.APP, state=RAISED)
    assert actions == [Action.LEAVE, Action.TO_BOTTOM]
    assert state == State()


def test_the_start_menu_during_show_desktop():
    actions, state = run(Kind.SHELL, Kind.DESKTOP, state=RAISED)
    assert actions == [Action.LEAVE, Action.LEAVE]
    assert state == RAISED


def test_clicking_the_widget_just_before_explorer_lifts_the_desktop():
    actions, state = run(Kind.DESKTOP, BELOW, Kind.SELF, ABOVE)
    assert actions == [Action.LEAVE, Action.LEAVE, Action.TO_BOTTOM, Action.RAISE]
    assert state.raised


def test_the_tray_menu_and_typing_during_show_desktop():
    actions, state = run(Kind.OWN, Kind.SELF, Kind.NONE, state=RAISED)
    assert set(actions) == {Action.LEAVE}
    assert state == RAISED


def test_a_reveal_lasts_until_an_app_comes_forward():
    actions, state = run(Kind.SELF, Kind.SELF, Kind.APP, Kind.SELF, state=REVEALED)
    assert actions == [Action.LEAVE, Action.LEAVE, Action.TO_BOTTOM, Action.TO_BOTTOM]
    assert state == State()


# -- Classification rules ----------------------------------------------------


@pytest.mark.parametrize("name", ["WorkerW", "Progman"])
def test_a_desktop_class_holding_the_icon_view_is_the_desktop(name):
    assert is_desktop_class(name, has_defview=True)


@pytest.mark.parametrize("name", ["WorkerW", "Progman"])
def test_a_desktop_class_without_the_icon_view_is_not(name):
    """The target machine has fourteen WorkerW windows that are not it."""
    assert not is_desktop_class(name, has_defview=False)


def test_another_class_is_never_the_desktop():
    assert not is_desktop_class("Notepad", has_defview=True)


@pytest.mark.parametrize("name", sorted(SHELL_CLASSES))
def test_shell_classes_are_shell_even_when_they_look_like_apps(name):
    assert classify_foreign(name, is_app_window=True) is Kind.SHELL


def test_the_taskbar_is_shell():
    """Seen at the start of every Show desktop corner click on the target."""
    assert classify_foreign("Shell_TrayWnd", is_app_window=False) is Kind.SHELL


@pytest.mark.parametrize("is_app, kind", [(True, Kind.APP), (False, Kind.SHELL)])
def test_other_classes_go_by_the_alt_tab_test(is_app, kind):
    assert classify_foreign("Chrome_WidgetWin_1", is_app_window=is_app) is kind


# -- DesktopLayer --------------------------------------------------------------


@pytest.fixture
def shell(fake_shell):
    fake_shell.kinds.update({DESKTOP: Kind.DESKTOP, MENU: Kind.OWN, TASKBAR: Kind.SHELL})
    return fake_shell


@pytest.fixture
def layer(shell, ticker_factory):
    return DesktopLayer(shell, lambda: WIDGET, ticker_factory)


@pytest.fixture
def ticker(layer, ticker_factory):
    return ticker_factory.ticker


@pytest.fixture
def on(layer, shell):
    """An enabled layer, with whatever enabling did cleared away."""
    layer.enabled = True
    shell.calls.clear()
    return layer


def raise_it(shell, ticker):
    shell.bring(DESKTOP)
    shell.above = True
    ticker.fire()
    shell.calls.clear()


def test_starts_disabled_and_installs_nothing(layer, shell, ticker):
    assert not layer.enabled
    assert shell.installs == 0
    assert not ticker.running


def test_disabled_ignores_events_and_ticks(layer, shell, ticker):
    layer.on_foreground(DESKTOP)
    layer.on_tick()
    assert shell.calls == [] and shell.asked == []
    assert not ticker.running


def test_first_enable_installs_the_hook(layer, shell):
    layer.enabled = True
    assert layer.enabled
    assert shell.installed and shell.installs == 1


def test_enabling_again_does_not_install_again(layer, shell):
    layer.enabled = True
    layer.enabled = False
    layer.enabled = True
    assert shell.installs == 1


def test_a_failed_install_leaves_it_disabled_for_good(shell, ticker_factory, capsys):
    shell.install_ok = False
    layer = DesktopLayer(shell, lambda: WIDGET, ticker_factory)
    layer.enabled = True
    layer.enabled = True
    assert not layer.enabled
    assert shell.installs == 1
    assert capsys.readouterr().err.count("Show desktop") == 1


def test_enabling_acts_on_whatever_is_in_front(layer, shell):
    shell.fg = APP
    layer.enabled = True
    assert shell.calls == [("bottom", WIDGET)]


def test_enabling_with_the_desktop_in_front_starts_a_watch(layer, shell, ticker):
    shell.fg = DESKTOP
    layer.enabled = True
    assert layer.state.watching
    assert ticker.running


def test_re_asserting_enabled_does_not_resync(on, shell):
    """The window sets this from several places; a repeat must not wipe a
    pending reveal or move anything."""
    on.reveal()
    on.enabled = True
    assert shell.calls == []
    assert on.state.revealed


def test_the_desktop_starts_the_ticker_and_looks_at_once(on, shell, ticker):
    shell.bring(DESKTOP)
    assert ticker.running
    assert shell.asked == [(WIDGET, DESKTOP)]
    assert shell.calls == []


def test_a_desktop_already_above_raises_without_waiting_for_a_tick(on, shell, ticker):
    shell.above = True
    shell.bring(DESKTOP)
    assert shell.calls == [("raise", WIDGET)]
    assert on.state.raised
    assert not ticker.running


def test_show_desktop_raises_on_the_tick_that_sees_it(on, shell, ticker):
    shell.bring(DESKTOP)
    ticker.fire(3)
    assert shell.calls == []
    shell.above = True
    ticker.fire()
    assert shell.calls == [("raise", WIDGET)]
    assert not ticker.running


def test_a_watch_that_sees_nothing_stops_after_two_seconds(on, shell, ticker):
    shell.bring(DESKTOP)
    ticker.fire(WATCH_TICKS + 5)
    assert not ticker.running
    assert shell.calls == []
    assert len(shell.asked) == WATCH_TICKS


def test_an_app_ends_a_raise(on, shell, ticker):
    raise_it(shell, ticker)
    shell.bring(TASKBAR)
    assert shell.calls == []
    shell.bring(APP)
    assert shell.calls == [("bottom", WIDGET)]
    assert on.state == State()


def test_an_app_mid_watch_stops_the_ticker(on, shell, ticker):
    shell.bring(DESKTOP)
    shell.bring(APP)
    assert not ticker.running


def test_a_reveal_holds_through_activation(on, shell):
    on.reveal()
    shell.bring(WIDGET)
    assert shell.calls == []


def test_the_widget_handle_is_read_on_every_event(shell, ticker_factory):
    """A restack recreates the native window; a stored handle goes stale."""
    handles = iter([WIDGET, WIDGET + 1])
    layer = DesktopLayer(shell, lambda: next(handles), ticker_factory)
    layer.enabled = True  # resync with fg 0: reads the first handle
    shell.bring(APP)
    assert shell.calls == [("bottom", WIDGET + 1)]


def test_switching_off_while_raised_lowers_first(on, shell, ticker):
    raise_it(shell, ticker)
    on.enabled = False
    assert shell.calls == [("bottom", WIDGET)]
    assert on.state == State()


def test_switching_off_when_not_raised_moves_nothing(on, shell):
    on.enabled = False
    assert shell.calls == []


def test_switching_off_mid_watch_stops_the_ticker(on, shell, ticker):
    shell.bring(DESKTOP)
    on.enabled = False
    assert not ticker.running
    on.on_tick()  # a tick already queued must do nothing
    assert shell.calls == []


def test_close_while_raised_lowers_and_unhooks(on, shell, ticker):
    raise_it(shell, ticker)
    on.close()
    assert shell.calls == [("bottom", WIDGET)]
    assert not shell.installed and not ticker.running


def test_close_twice_is_harmless(on, shell):
    on.close()
    on.close()
    assert shell.uninstalls == 1


def test_close_without_ever_enabling_is_harmless(layer, shell):
    layer.close()
    assert shell.uninstalls == 0


def test_a_failing_placement_does_not_escape(on, shell, capsys):
    def boom(widget):
        raise OSError("SetWindowPos failed")

    shell.place_at_bottom = boom
    shell.bring(APP)  # must not raise
    assert "SetWindowPos failed" in capsys.readouterr().err


def test_a_failing_check_ends_the_watch_instead_of_repeating(on, shell, ticker, capsys):
    def boom(widget, desktop):
        raise OSError("GetWindow failed")

    shell.desktop_above = boom
    shell.bring(DESKTOP)
    ticker.fire(10)
    assert not ticker.running
    assert capsys.readouterr().err.count("GetWindow failed") == 1


# -- create() and the Windows binding ----------------------------------------
#
# Win32Shell's hook only runs on Windows and is checked by hand there. What
# can be checked anywhere is that nothing Windows-only runs elsewhere, and
# that a binding failure degrades to today's behaviour instead of crashing.


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows path")
def test_no_layer_is_created_off_windows():
    assert create(lambda: WIDGET) is None


@pytest.mark.skipif(sys.platform == "win32", reason="user32 exists there")
def test_the_win32_shell_refuses_to_bind_off_windows():
    with pytest.raises(OSError):
        Win32Shell()


@pytest.mark.skipif(sys.platform == "win32", reason="user32 would bind there")
def test_a_failed_binding_means_no_layer_rather_than_a_crash(monkeypatch, capsys):
    monkeypatch.setattr(desktop_layer, "is_windows", lambda: True)
    assert create(lambda: WIDGET) is None
    assert "desktop layer unavailable" in capsys.readouterr().err


def test_always_on_top_is_entered_in_one_place_and_never_left_by_notopmost():
    """HWND_BOTTOM is the only way down (docs/show-desktop.md, decision 3)."""
    assert not hasattr(desktop_layer, "HWND_NOTOPMOST")
    source = inspect.getsource(desktop_layer)
    uses = [line for line in source.splitlines()
            if "HWND_TOPMOST" in line and not line.lstrip().startswith("#")]
    assert uses == [
        "HWND_TOPMOST = -1",
        "        self._set_pos(widget, HWND_TOPMOST)",
    ]


# -- Win32Shell, against a fake user32 -----------------------------------------

FLAGS = (desktop_layer.SWP_NOMOVE | desktop_layer.SWP_NOSIZE
         | desktop_layer.SWP_NOACTIVATE)
OWNER = 0x900


class FakeUser32:
    """Just enough user32 for the stack walk, the Alt+Tab test and placement.

    ``stack`` lists top-level windows from the top of the z-order down.
    """

    def __init__(self, stack=(), visible=(), exstyle=None, owner=None):
        self.stack = list(stack)
        self.visible = set(visible)
        self.exstyle = exstyle or {}
        self.owner = owner or {}
        self.moves = []
        self.walked = 0

    def GetWindow(self, hwnd, cmd):  # noqa: N802
        if cmd == desktop_layer.GW_OWNER:
            return self.owner.get(hwnd)
        assert cmd == desktop_layer.GW_HWNDPREV
        self.walked += 1
        i = self.stack.index(hwnd)
        return self.stack[i - 1] if i > 0 else None

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return hwnd in self.visible

    def GetWindowLongPtrW(self, hwnd, index):  # noqa: N802
        assert index == desktop_layer.GWL_EXSTYLE
        return self.exstyle.get(hwnd, 0)

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):  # noqa: N802
        self.moves.append((hwnd, after, flags))
        return 1


class FakeDwmapi:
    def __init__(self, cloaked=()):
        self.cloaked = set(cloaked)

    def DwmGetWindowAttribute(self, hwnd, attr, ref, size):  # noqa: N802
        assert attr == desktop_layer.DWMWA_CLOAKED
        ref._obj.value = 1 if hwnd in self.cloaked else 0
        return 0


def fake_win32(dwmapi=None, **kwargs):
    shell = Win32Shell.__new__(Win32Shell)  # skip binding the real user32
    shell._user32 = FakeUser32(**kwargs)
    shell._dwmapi = dwmapi
    return shell, shell._user32


@pytest.mark.parametrize("stack, above", [
    ([DESKTOP, WIDGET], True),                 # Show desktop: lifted over it
    ([DESKTOP, APP, TASKBAR, WIDGET], True),   # further up, with others between
    ([APP, WIDGET, DESKTOP], False),           # normal: widget just above it
    ([WIDGET, APP, DESKTOP], False),
])
def test_desktop_above_walks_up_from_the_widget(stack, above):
    shell, _ = fake_win32(stack=stack)
    assert shell.desktop_above(WIDGET, DESKTOP) is above


@pytest.mark.parametrize("widget, desktop", [(0, DESKTOP), (WIDGET, 0)])
def test_desktop_above_with_no_handle_is_false(widget, desktop):
    shell, u = fake_win32(stack=[DESKTOP, WIDGET])
    assert shell.desktop_above(widget, desktop) is False
    assert u.walked == 0


def test_desktop_above_gives_up_on_a_stack_that_never_ends(monkeypatch):
    monkeypatch.setattr(desktop_layer, "_MAX_WALK", 5)
    shell, u = fake_win32()
    u.GetWindow = lambda hwnd, cmd: APP  # a cycle, as a changing z-order could
    assert shell.desktop_above(WIDGET, DESKTOP) is False


def test_raise_is_always_on_top_without_activating():
    shell, u = fake_win32()
    shell.raise_topmost(WIDGET)
    assert u.moves == [(WIDGET, desktop_layer.HWND_TOPMOST, FLAGS)]


def test_to_the_bottom_is_hwnd_bottom_without_activating():
    shell, u = fake_win32()
    shell.place_at_bottom(WIDGET)
    assert u.moves == [(WIDGET, desktop_layer.HWND_BOTTOM, FLAGS)]


EX = desktop_layer


@pytest.mark.parametrize("visible, exstyle, owner, cloaked, is_app", [
    (True, 0, None, False, True),                        # a plain app window
    (False, 0, None, False, False),                      # hidden
    (True, 0, None, True, False),                        # cloaked
    (True, EX.WS_EX_TOOLWINDOW, None, False, False),     # tool window
    (True, EX.WS_EX_NOACTIVATE, None, False, False),     # never activates
    (True, 0, OWNER, False, False),                      # owned (a dialog)
    (True, EX.WS_EX_APPWINDOW, OWNER, False, True),      # owned, but says app
])
def test_the_alt_tab_test(visible, exstyle, owner, cloaked, is_app):
    shell, _ = fake_win32(
        dwmapi=FakeDwmapi(cloaked=[APP] if cloaked else []),
        visible=[APP] if visible else [],
        exstyle={APP: exstyle},
        owner={APP: owner} if owner else {},
    )
    assert shell._is_app_window(APP) is is_app


def test_without_dwmapi_nothing_counts_as_cloaked():
    shell, _ = fake_win32(dwmapi=None, visible=[APP])
    assert shell._is_app_window(APP) is True


def test_the_cloak_check_passes_a_four_byte_value():
    seen = []

    class Dwm:
        def DwmGetWindowAttribute(self, hwnd, attr, ref, size):  # noqa: N802
            seen.append(size)
            return 0

    shell, _ = fake_win32(dwmapi=Dwm())
    shell._cloaked(APP)
    assert seen == [ctypes.sizeof(ctypes.c_uint32)] == [4]
