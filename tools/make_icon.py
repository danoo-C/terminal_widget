"""Generate the application icon at every size we ship.

Run with the project's venv:  python tools/make_icon.py
Writes PNGs plus a Windows .ico into terminal_widget/assets/.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ASSETS = Path(__file__).resolve().parent.parent / "terminal_widget" / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256]

BACKGROUND = QColor("#15151b")
BORDER = QColor("#2c2c38")
PROMPT = QColor("#0dbc79")
CURSOR = QColor("#e0e0e0")


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded dark panel, inset slightly so the corners are not clipped.
    inset = max(0.5, size * 0.03)
    body = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = size * 0.22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BACKGROUND)
    p.drawRoundedRect(body, radius, radius)

    if size >= 32:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(BORDER, max(1.0, size * 0.012)))
        p.drawRoundedRect(body, radius, radius)

    # The ">" prompt, drawn as strokes so it stays crisp at small sizes.
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(PROMPT, max(1.6, size * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    left, mid, right = size * 0.28, size * 0.47, size * 0.30
    top, bottom = size * 0.34, size * 0.66
    p.drawLine(QPointF(left, top), QPointF(mid, size * 0.5))
    p.drawLine(QPointF(mid, size * 0.5), QPointF(right, bottom))

    # Block cursor sitting after the prompt.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(CURSOR)
    cw, ch = size * 0.17, size * 0.26
    p.drawRect(QRectF(size * 0.56, size * 0.5 - ch / 2, cw, ch))
    p.end()
    return image


def write_ico(images: list[QImage], path: Path) -> None:
    """Minimal ICO writer: a directory of embedded PNGs.

    Note: PySide6's stubs declare QImage.save's format parameter as bytes,
    but the runtime rejects bytes and requires str. The type: ignore
    comments below are deliberate -- the running code is correct and the
    stub is not.

    Qt's ICO plugin is read-only in some builds, and the format is simple
    enough that depending on it is not worth the risk.
    """
    from PySide6.QtCore import QBuffer, QByteArray

    blobs = []
    for image in images:
        # Hold the QByteArray in a named local: QBuffer keeps a pointer to it,
        # and passing a temporary segfaults once Python collects it.
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")  # type: ignore[arg-type]  # stub says bytes; runtime wants str
        buf.close()
        blobs.append((image.width(), data.data()))

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = b"", b""
    for width, blob in blobs:
        dim = 0 if width >= 256 else width
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        payload += blob
        offset += len(blob)
    path.write_bytes(header + entries + payload)


def main() -> None:
    QGuiApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)
    images = []
    for size in SIZES:
        image = render(size)
        images.append(image)
        out = ASSETS / f"terminal-widget-{size}.png"
        image.save(str(out), "PNG")  # type: ignore[arg-type]  # stub says bytes; runtime wants str
        print("wrote", out.name)
    render(256).save(str(ASSETS / "terminal-widget.png"), "PNG")  # type: ignore[arg-type]  # stub says bytes; runtime wants str
    print("wrote terminal-widget.png")
    write_ico(images, ASSETS / "terminal-widget.ico")
    print("wrote terminal-widget.ico")


if __name__ == "__main__":
    main()
