"""Colours for the settings window that survive the system theme.

Qt hands the application a palette that follows the OS. What it does not hand
out is a *secondary text* colour. The role that looks like one, ``Mid``, is
defined as a shade of the window colour, so on a dark theme it is grey on
grey: the hint labels in this app used it and measured 1.22:1 against their
background, which is not dim, it is absent.

Nor can a stylesheet fix it. ``color: palette(mid)`` is honoured, but
``color: palette(placeholderText)`` is silently ignored and falls back to the
ordinary text colour, so the fix has to set a real palette on the label.

Everything here is derived from the live palette rather than named, so it
stays correct in both directions and under any accent colour. Kept free of
project imports so it can be tested against a synthetic palette.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

#: Contrast that hint text keeps against its background, as a WCAG ratio.
#: Above the 4.5:1 threshold for body text, with enough margin that rounding
#: and unusual palettes cannot push it under.
MUTED_CONTRAST = 5.0

#: Granularity of the search below: 2.5% of the distance to the background.
_MUTED_STEPS = 40

#: (ok, error) status colours. The light pair is the original; the dark pair
#: is lightened, because the originals measure about 3:1 on a dark window.
STATUS_LIGHT = (QColor("#2e7d32"), QColor("#c62828"))
STATUS_DARK = (QColor("#81c995"), QColor("#f28b82"))


def is_dark(palette: QPalette) -> bool:
    """Whether this palette is a dark one."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def status_colors(palette: QPalette) -> tuple[QColor, QColor]:
    """The (ok, error) pair that reads on this palette."""
    return STATUS_DARK if is_dark(palette) else STATUS_LIGHT


def muted_color(palette: QPalette) -> QColor:
    """A secondary-text colour: as quiet as it can be and still readable.

    Fades the palette's own text towards its own background, stopping at
    :data:`MUTED_CONTRAST`. A fixed blend cannot do this -- the same fraction
    that looks right on one palette is illegible on another -- and a palette
    whose own text barely contrasts with its background simply gets the text
    colour back, unfaded.
    """
    text = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
    back = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
    muted = text
    for step in range(1, _MUTED_STEPS + 1):
        candidate = blend(text, back, step / _MUTED_STEPS)
        if contrast_ratio(candidate, back) < MUTED_CONTRAST:
            break
        muted = candidate
    return muted


def blend(color: QColor, towards: QColor, ratio: float) -> QColor:
    """``color`` moved ``ratio`` of the way to ``towards``."""
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        *(
            round(a + (b - a) * ratio)
            for a, b in (
                (color.red(), towards.red()),
                (color.green(), towards.green()),
                (color.blue(), towards.blue()),
            )
        )
    )


def contrast_ratio(one: QColor, other: QColor) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    first, second = _luminance(one), _luminance(other)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )
