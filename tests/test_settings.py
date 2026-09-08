"""Tests for the settings window itself."""

import pytest

from terminal_widget.config import (
    OPACITY_BACKGROUND,
    OPACITY_WINDOW,
    STACKING_CHOICES,
    STACKING_DESKTOP,
    STACKING_TOP,
    Config,
)


@pytest.fixture
def settings(qapp, monkeypatch):
    from terminal_widget.ipc import SettingsClient
    from terminal_widget.settings.__main__ import SettingsWindow

    # Never open a socket from a test. Takes `name` because the settings app
    # passes the per-config socket name.
    monkeypatch.setattr(SettingsClient, "start", lambda self, name=None: None)
    window = SettingsWindow(Config(), None)
    yield window
    window.close()


def test_opens_wide_enough_for_its_widest_row(settings):
    assert settings.size().width() == 560


def test_background_mode_reaches_zero(settings):
    settings.radio_bg.setChecked(True)
    assert settings.slider_opacity.minimum() == 0
    settings.slider_opacity.setValue(0)
    assert settings._config_from_ui().opacity == 0


def test_window_mode_stops_before_it_disappears(settings):
    """At 0 the whole widget fades, and an invisible window cannot be found
    again to turn back up."""
    settings.radio_bg.setChecked(True)
    settings.slider_opacity.setValue(0)
    settings.radio_win.setChecked(True)
    assert settings.slider_opacity.minimum() == 10
    assert settings._config_from_ui().opacity == 10


def test_loading_a_zero_opacity_config_keeps_it(settings):
    """The floor has to be lowered before the value lands, or loading a
    saved 0% quietly clamps it back to 10."""
    settings._config = Config(opacity=0, opacity_mode=OPACITY_BACKGROUND)
    settings._load_into_ui()
    assert settings.slider_opacity.value() == 0


def test_reading_the_form_back_keeps_fields_it_does_not_edit(settings):
    """_config_from_ui runs on every keystroke and its result is what gets
    saved, so a field missing from it is a field silently reset."""
    settings._config = Config(
        foreground="#abcdef", background="#123456", working_dir="/tmp", scrollback=1234
    )
    settings._load_into_ui()
    result = settings._config_from_ui()
    assert result.foreground == "#abcdef"
    assert result.background == "#123456"
    assert result.working_dir == "/tmp"
    assert result.scrollback == 1234


def test_every_hint_label_is_registered_for_theming(settings):
    """An unregistered label keeps whatever colour it was born with, which
    is the whole bug on a dark theme."""
    assert len(settings._muted) >= 8


def test_the_status_line_survives_a_restyle(settings):
    settings._set_status("error", "boom")
    settings._restyle()
    assert settings.status.text() == "boom"


# -- Window layer -----------------------------------------------------


def test_the_layer_dropdown_offers_every_mode(settings):
    keys = [
        settings.combo_stacking.itemData(i)
        for i in range(settings.combo_stacking.count())
    ]
    assert keys == [key for key, _label in STACKING_CHOICES]


def test_the_layer_dropdown_round_trips(settings):
    settings._config = Config(stacking=STACKING_TOP)
    settings._load_into_ui()
    assert settings.combo_stacking.currentData() == STACKING_TOP
    assert settings._config_from_ui().stacking == STACKING_TOP


def test_an_unknown_layer_shows_as_the_default(settings):
    """findData misses and falls back to index 0, which has to be the same
    value clamped() picks or the form would lie about what will be saved."""
    settings._config = Config(stacking="sideways")
    settings._load_into_ui()
    assert settings.combo_stacking.currentData() == STACKING_DESKTOP


def test_changing_the_layer_reaches_the_widget(settings):
    """The settings app always sends it; the widget is what defers applying
    it until config mode ends."""
    sent = []
    settings.client.send_config = sent.append
    settings.combo_stacking.setCurrentIndex(
        settings.combo_stacking.findData(STACKING_TOP)
    )
    assert sent and sent[-1].stacking == STACKING_TOP


def test_the_scroll_to_top_checkbox_round_trips(settings):
    settings._config = Config(scroll_top_on_start=True)
    settings._load_into_ui()
    assert settings.check_scroll_top.isChecked()
    assert settings._config_from_ui().scroll_top_on_start is True


def test_ticking_scroll_to_top_reaches_the_widget(settings):
    sent = []
    settings.client.send_config = sent.append
    settings.check_scroll_top.setChecked(True)
    assert sent and sent[-1].scroll_top_on_start is True


# -- One widget, and the button that restarts it -----------------------


@pytest.fixture
def spawns(monkeypatch):
    """Capture what would have been launched."""
    from terminal_widget import launch as launch_module

    calls = []

    class FakeProcess:
        @staticmethod
        def startDetached(program, args):  # noqa: N802
            calls.append((program, args))
            return True, 1234

    monkeypatch.setattr(launch_module, "QProcess", FakeProcess)
    return calls


def connected(monkeypatch, yes=True):
    from terminal_widget.ipc import SettingsClient

    monkeypatch.setattr(SettingsClient, "is_connected", property(lambda self: yes))


def test_the_button_offers_launch_when_no_widget_is_running(settings, monkeypatch):
    connected(monkeypatch, False)
    settings._refresh_launch_button()
    assert settings.btn_launch.text() == "Launch widget"
    assert settings.btn_launch.isEnabled()


def test_the_button_offers_restart_while_a_widget_is_running(settings, monkeypatch):
    """It used to hide itself instead, which left it armed and clickable for
    the second before this app reached a widget that was already running."""
    connected(monkeypatch, True)
    settings._refresh_launch_button()
    assert settings.btn_launch.text() == "Restart widget"


def test_restarting_asks_the_widget_rather_than_starting_a_second_one(
    settings, monkeypatch, spawns
):
    connected(monkeypatch, True)
    sent = []
    settings.client.send_quit = lambda restart=False: sent.append(restart)
    settings._launch_widget()
    assert sent == [True]
    assert spawns == []  # the widget starts its own replacement


def test_one_click_is_one_widget_however_fast_you_click(settings, monkeypatch, spawns):
    """The 400ms window that made two widgets possible from one button."""
    connected(monkeypatch, False)
    settings._launch_widget()
    settings._launch_widget()
    assert len(spawns) == 1


def test_the_widget_is_launched_with_the_settings_apps_own_config(
    settings, monkeypatch, spawns, tmp_path
):
    connected(monkeypatch, False)
    settings._path = tmp_path / "elsewhere.json"
    settings._launch_widget()
    assert spawns and "--config" in spawns[0][1]
    assert str(tmp_path / "elsewhere.json") in spawns[0][1]


def test_a_widget_that_never_arrives_is_reported(settings, monkeypatch, spawns):
    connected(monkeypatch, False)
    settings._launch_widget()
    assert settings._waiting
    settings._settle()
    assert not settings._waiting
    assert "did not start" in settings.status.text()
