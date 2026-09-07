"""Tests for the settings window's derived colours.

The bug these exist to prevent: `palette(mid)` is a shade of the *window*
colour, so on a dark theme the hint labels were grey on grey at 1.22:1.
"""

import pytest
from PySide6.QtGui import QColor, QPalette

from terminal_widget.settings.theme import (
    MUTED_CONTRAST,
    contrast_ratio,
    is_dark,
    muted_color,
    status_colors,
)

PALETTES = {
    "fusion light": ("#000000", "#efefef"),
    "windows light": ("#000000", "#f3f3f3"),
    "windows dark": ("#ffffff", "#202020"),
    "fusion dark": ("#efefef", "#323232"),
}


def palette_of(text: str, window: str) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    return palette


@pytest.mark.parametrize("name", list(PALETTES))
def test_hint_text_stays_readable(qapp, name):
    text, window = PALETTES[name]
    palette = palette_of(text, window)
    assert contrast_ratio(muted_color(palette), QColor(window)) >= MUTED_CONTRAST


@pytest.mark.parametrize("name", list(PALETTES))
def test_hint_text_is_quieter_than_body_text(qapp, name):
    text, window = PALETTES[name]
    palette = palette_of(text, window)
    assert muted_color(palette) != QColor(text)


def test_a_low_contrast_palette_gets_its_text_colour_back(qapp):
    """Nothing can be faded into a background it barely differs from, so the
    honest answer is to fade it not at all."""
    palette = palette_of("#555555", "#4a4a4a")
    assert muted_color(palette) == QColor("#555555")


@pytest.mark.parametrize(
    "name,expected", [("fusion light", False), ("windows dark", True)]
)
def test_is_dark_classifies_both_schemes(qapp, name, expected):
    assert is_dark(palette_of(*PALETTES[name])) is expected


def test_status_colours_differ_between_schemes(qapp):
    light = status_colors(palette_of(*PALETTES["fusion light"]))
    dark = status_colors(palette_of(*PALETTES["windows dark"]))
    assert light != dark


@pytest.mark.parametrize("name", list(PALETTES))
@pytest.mark.parametrize("index", [0, 1])
def test_status_colours_stay_readable(qapp, name, index):
    text, window = PALETTES[name]
    color = status_colors(palette_of(text, window))[index]
    assert contrast_ratio(color, QColor(window)) >= 4.0
