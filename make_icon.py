"""Generate build_icon.ico from public/closed.png at multiple sizes.

PySide6 can read the PNG; we write each size into an ICO. Pure Qt, no Pillow.
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage


def make_ico(src_png: Path, dst_ico: Path) -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    src = QImage(str(src_png))
    if src.isNull():
        raise SystemExit(f"could not load {src_png}")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs: list[bytes] = []
    for s in sizes:
        scaled = src.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        scaled.save(buf, "PNG")
        pngs.append(bytes(ba))

    with open(dst_ico, "wb") as f:
        # ICONDIR
        f.write(struct.pack("<HHH", 0, 1, len(sizes)))
        offset = 6 + 16 * len(sizes)
        # ICONDIRENTRY for each
        for s, data in zip(sizes, pngs):
            w = 0 if s >= 256 else s
            h = 0 if s >= 256 else s
            f.write(struct.pack(
                "<BBBBHHII",
                w, h,    # width, height (0 == 256)
                0,       # color count
                0,       # reserved
                1,       # planes
                32,      # bits per pixel
                len(data),
                offset,
            ))
            offset += len(data)
        # Image data
        for data in pngs:
            f.write(data)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    make_ico(here / "public" / "closed.png", here / "build_icon.ico")
    print("wrote build_icon.ico")
