"""Genera assets/app.ico a partir de assets/app.svg (para el .exe y el instalador)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "assets", "app.svg")
TARGET = os.path.join(ROOT, "assets", "app.ico")
ICON_SIZE = 128  # Qt escribe un unico tamano en el .ico; Windows lo reescala


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841 - necesario para QImage/QPainter
    renderer = QSvgRenderer(SOURCE)
    image = QImage(QSize(ICON_SIZE, ICON_SIZE), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, ICON_SIZE, ICON_SIZE))
    painter.end()
    if not image.save(TARGET, "ICO"):
        print("No se ha podido escribir", TARGET)
        return 1
    print("Icono generado:", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
