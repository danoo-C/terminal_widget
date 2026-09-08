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

    # Never open a socket from a test.
    monkeypatch.setattr(SettingsClient, "start", lambda self: None)
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
