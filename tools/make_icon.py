"""Generate the Framecheck application icon.

Drawn in code rather than committed as a binary blob, so the mark can be
adjusted by editing values here and re-running:

    python tools/make_icon.py

Writes assets/framecheck.ico (multi-size, for the Windows taskbar and the
PyInstaller build) and assets/framecheck.png for documentation.

The mark: two corner brackets forming a video frame, with a check mark cut
through the lower-right bracket. Frame + check -- the whole product in one
glyph. At 16px the brackets are dropped and only the check survives, because
four thin brackets at that size turn into grey mush.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from framecheck.app.ui.theme import Color  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)

BACKGROUND = QColor(Color.ACCENT)
MARK = QColor("#ffffff")


def draw_icon(size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    # Rounded square, proportioned like a Windows 11 app tile.
    inset = s * 0.045
    radius = s * 0.22
    painter.setPen(Qt.NoPen)
    painter.setBrush(BACKGROUND)
    painter.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset), radius, radius)

    stroke = max(1.0, s * 0.075)
    pen = QPen(MARK, stroke, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)

    if size >= 24:
        # Frame brackets: top-left and bottom-right, so the eye reads a frame
        # without four corners crowding the check.
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        m = s * 0.245  # margin from the tile edge
        arm = s * 0.165  # bracket arm length

        path = QPainterPath()
        path.moveTo(m, m + arm)
        path.lineTo(m, m)
        path.lineTo(m + arm, m)
        painter.drawPath(path)

        path = QPainterPath()
        path.moveTo(s - m, s - m - arm)
        path.lineTo(s - m, s - m)
        path.lineTo(s - m - arm, s - m)
        painter.drawPath(path)

    # The check. Scaled up at 16px, where it is the only thing on the tile.
    check_scale = 1.0 if size >= 24 else 1.28
    cx, cy = s * 0.485, s * 0.53
    # Kept narrow enough that the rising tip clears the lower-right bracket at
    # 32-48px, where the two were touching and read as one smudged shape.
    w = s * 0.26 * check_scale
    h = s * 0.21 * check_scale

    check_pen = QPen(MARK, max(1.2, stroke * 1.1), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(check_pen)
    path = QPainterPath()
    path.moveTo(cx - w * 0.52, cy)
    path.lineTo(cx - w * 0.12, cy + h * 0.60)
    path.lineTo(cx + w * 0.55, cy - h * 0.72)
    painter.drawPath(path)

    painter.end()
    return image


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QImage/QPainter

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)

    images = [draw_icon(size) for size in SIZES]

    ico_path = assets / "framecheck.ico"
    # QImage cannot write a multi-size .ico directly; build one from the frames.
    write_ico(images, ico_path)
    print(f"wrote {ico_path} ({', '.join(str(s) for s in SIZES)})")

    png_path = assets / "framecheck.png"
    draw_icon(512).save(str(png_path))
    print(f"wrote {png_path} (512)")
    return 0


def write_ico(images: list[QImage], path: Path) -> None:
    """Write a multi-resolution .ico with PNG-compressed frames.

    Qt's ICO writer only emits a single size, and Windows picks poor scalings
    from one frame; an explicit directory of per-size PNGs renders crisply from
    the taskbar to the Alt-Tab switcher.
    """
    import struct
    from io import BytesIO

    from PySide6.QtCore import QBuffer

    frames: list[tuple[int, bytes]] = []
    for image in images:
        # QBuffer() with no argument owns its storage. Passing a temporary
        # QByteArray instead lets Python collect it while Qt still holds the
        # pointer, which segfaults.
        buffer = QBuffer()
        buffer.open(QBuffer.WriteOnly)
        image.save(buffer, "PNG")
        frames.append((image.width(), bytes(buffer.data())))
        buffer.close()

    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(frames)))  # reserved, type=icon, count
    offset = 6 + 16 * len(frames)
    for size, payload in frames:
        # 0 in the width/height byte means 256.
        dimension = 0 if size >= 256 else size
        out.write(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,  # palette size
                0,  # reserved
                1,  # colour planes
                32,  # bits per pixel
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    for _, payload in frames:
        out.write(payload)

    path.write_bytes(out.getvalue())


if __name__ == "__main__":
    raise SystemExit(main())
