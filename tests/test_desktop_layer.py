"""Tests for keeping the desktop layer visible through Show desktop.

All platform-free: the decisions are pure, and the Win32 side is the
FakeShell from conftest. What Windows actually does is checked by hand,
per docs/plans/show-desktop/04-verify-on-windows.md.
"""

import sys

import pytest

from terminal_widget import desktop_layer
from terminal_widget.desktop_layer import (
    Action,
    DesktopLayer,
    Foreground,
    Kind,
    State,
    Win32Shell,
    create,
    decide,
    is_desktop_class,
)

WIDGET, DESKTOP, APP, MENU = 100, 200, 300, 400


def fg(kind, hwnd=None):
    hwnds = {Kind.NONE: 0, Kind.DESKTOP: DESKTOP, Kind.SELF: WIDGET,
             Kind.OWN: MENU, Kind.OTHER: APP}
    return Foreground(hwnds[kind] if hwnd is None else hwnd, kind)


def run(*kinds, state=State()):
    """Feed a sequence of foreground kinds; return the actions and end state."""
    actions = []
    for kind in kinds:
        action, state = decide(fg(kind), state)
        actions.append(action)
    return actions, state


# -- decide(): one row of the table each -------------------------------

SHOWN = State(desktop=DESKTOP)
REVEALED = State(revealed=True)


@pytest.mark.parametrize("state", [State(), SHOWN, REVEALED])
def test_no_foreground_window_changes_nothing(state):
    assert decide(fg(Kind.NONE), state) == (Action.LEAVE, state)


@pytest.mark.parametrize("state", [State(), SHOWN, REVEALED])
def test_our_own_other_windows_change_nothing(state):
    assert decide(fg(Kind.OWN), state) == (Action.LEAVE, state)


@pytest.mark.parametrize("state", [State(), SHOWN, REVEALED])
def test_the_desktop_lifts_the_widget_above_it_and_is_remembered(state):
    assert decide(fg(Kind.DESKTOP), state) == (Action.ABOVE_DESKTOP, SHOWN)


def test_a_revealed_widget_is_left_where_activation_put_it():
    assert decide(fg(Kind.SELF), REVEALED) == (Action.LEAVE, REVEALED)


def test_clicking_the_widget_during_show_desktop_keeps_it_above():
    assert decide(fg(Kind.SELF), SHOWN) == (Action.ABOVE_DESKTOP, SHOWN)


def test_clicking_the_widget_otherwise_sends_it_back_to_the_bottom():
    assert decide(fg(Kind.SELF), State()) == (Action.TO_BOTTOM, State())


@pytest.mark.parametrize("state", [State(), SHOWN, REVEALED])
def test_any_other_window_sends_it_to_the_bottom_and_forgets(state):
    assert decide(fg(Kind.OTHER), state) == (Action.TO_BOTTOM, State())


# -- decide(): sequences -----------------------------------------------


def test_show_desktop_then_click_the_widget_then_open_an_app():
    actions, state = run(Kind.DESKTOP, Kind.SELF, Kind.OTHER)
    assert actions == [Action.ABOVE_DESKTOP, Action.ABOVE_DESKTOP, Action.TO_BOTTOM]
    assert state == State()


def test_a_reveal_lasts_until_another_window_comes_forward():
    actions, state = run(Kind.SELF, Kind.SELF, Kind.OTHER, Kind.SELF, state=REVEALED)
    assert actions == [Action.LEAVE, Action.LEAVE, Action.TO_BOTTOM, Action.TO_BOTTOM]
    assert state == State()


def test_the_tray_menu_during_show_desktop_keeps_the_desktop_remembered():
    actions, state = run(Kind.DESKTOP, Kind.OWN, Kind.SELF)
    assert actions == [Action.ABOVE_DESKTOP, Action.LEAVE, Action.ABOVE_DESKTOP]
    assert state == SHOWN


def test_a_null_foreground_mid_sequence_changes_nothing():
    with_null, end_with = run(Kind.DESKTOP, Kind.NONE, Kind.SELF, Kind.NONE, Kind.OTHER)
    without, end_without = run(Kind.DESKTOP, Kind.SELF, Kind.OTHER)
    assert [a for a in with_null if a is not Action.LEAVE] == without
    assert end_with == end_without


# -- is_desktop_class() ------------------------------------------------


@pytest.mark.parametrize("name", ["WorkerW", "Progman"])
def test_a_desktop_class_holding_the_icon_view_is_the_desktop(name):
    assert is_desktop_class(name, has_defview=True)


@pytest.mark.parametrize("name", ["WorkerW", "Progman"])
def test_a_desktop_class_without_the_icon_view_is_not(name):
    """Explorer keeps other WorkerW windows that are not the desktop."""
    assert not is_desktop_class(name, has_defview=False)


def test_another_class_is_never_the_desktop():
    assert not is_desktop_class("Notepad", has_defview=True)


# -- DesktopLayer ------------------------------------------------------


@pytest.fixture
def shell(fake_shell):
    fake_shell.kinds[DESKTOP] = Kind.DESKTOP
    fake_shell.kinds[MENU] = Kind.OWN
    return fake_shell


@pytest.fixture
def layer(shell):
    return DesktopLayer(shell, lambda: WIDGET)


def test_starts_disabled_and_installs_nothing(layer, shell):
    assert not layer.enabled
    assert shell.installs == 0


def test_disabled_ignores_events(layer, shell):
    layer.on_foreground(DESKTOP)
    assert shell.calls == []


def test_first_enable_installs_the_hook(layer, shell):
    layer.enabled = True
    assert layer.enabled
    assert shell.installed and shell.installs == 1


def test_enabling_again_does_not_install_again(layer, shell):
    layer.enabled = True
    layer.enabled = False
    layer.enabled = True
    assert shell.installs == 1


def test_disabling_leaves_the_hook_installed_but_ignores_events(layer, shell):
    layer.enabled = True
    layer.enabled = False
    shell.calls.clear()
    shell.bring(DESKTOP)
    assert shell.installed
    assert shell.calls == []


def test_a_failed_install_leaves_it_disabled_for_good(shell, capsys):
    shell.install_ok = False
    layer = DesktopLayer(shell, lambda: WIDGET)
    layer.enabled = True
    layer.enabled = True
    assert not layer.enabled
    assert shell.installs == 1
    assert capsys.readouterr().err.count("Show desktop") == 1


def test_enabling_acts_on_whatever_is_in_front(layer, shell):
    shell.fg = DESKTOP
    layer.enabled = True
    assert shell.calls == [("above", WIDGET, DESKTOP)]


def test_enabling_starts_from_a_clean_state(layer, shell):
    layer.reveal()
    shell.fg = WIDGET
    layer.enabled = True
    assert shell.calls == [("bottom", WIDGET)]


def test_re_asserting_enabled_does_not_resync(layer, shell):
    """The window sets this from several places; a repeat must not wipe a
    pending reveal or move anything."""
    layer.enabled = True
    layer.reveal()
    shell.calls.clear()
    layer.enabled = True
    assert shell.calls == []
    assert layer.state.revealed


def test_events_drive_placements(layer, shell):
    layer.enabled = True
    shell.calls.clear()
    shell.bring(DESKTOP)
    shell.bring(WIDGET)
    shell.bring(MENU)
    shell.bring(APP)
    assert shell.calls == [
        ("above", WIDGET, DESKTOP),
        ("above", WIDGET, DESKTOP),
        ("bottom", WIDGET),
    ]


def test_a_reveal_holds_through_activation(layer, shell):
    layer.enabled = True
    shell.calls.clear()
    layer.reveal()
    shell.bring(WIDGET)
    assert shell.calls == []


def test_the_widget_handle_is_read_on_every_event(shell):
    """A restack recreates the native window; a stored handle goes stale."""
    handles = iter([WIDGET, WIDGET + 1])
    layer = DesktopLayer(shell, lambda: next(handles))
    layer.enabled = True  # resync with fg 0: reads the first handle
    shell.bring(APP)
    assert shell.calls == [("bottom", WIDGET + 1)]


def test_a_failing_placement_does_not_escape(layer, shell, capsys):
    def boom(widget):
        raise OSError("SetWindowPos failed")

    shell.place_at_bottom = boom
    layer.enabled = True
    shell.bring(APP)  # must not raise
    assert "SetWindowPos failed" in capsys.readouterr().err


def test_close_uninstalls(layer, shell):
    layer.enabled = True
    layer.close()
    assert not shell.installed
    assert not layer.enabled


def test_close_twice_is_harmless(layer, shell):
    layer.enabled = True
    layer.close()
    layer.close()
    assert shell.uninstalls == 1


def test_close_without_ever_enabling_is_harmless(layer, shell):
    layer.close()
    assert shell.uninstalls == 0


# -- create() and the Windows binding ------------------------------------
#
# Win32Shell itself only runs on Windows and is checked by hand there. What
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


def test_no_topmost_constant_exists():
    """The layer must never leave the normal window band (see the module)."""
    assert not hasattr(desktop_layer, "HWND_TOPMOST")
    assert not hasattr(desktop_layer, "HWND_NOTOPMOST")
