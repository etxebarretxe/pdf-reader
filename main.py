"""Lector PDF ligero - punto de entrada."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

# Permite ejecutar 'python main.py' desde cualquier directorio.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import APP_NAME, MainWindow  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # Escalado HiDPI de Windows (125% / 150% / 200%) sin redondeos.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("LectorPDF")
    app.setApplicationDisplayName(APP_NAME)

    window = MainWindow()
    window.show()

    files = [arg for arg in argv[1:] if not arg.startswith("-")]
    if files:
        window.open_path(files[0])
        from ui.main_window import open_document_window

        for path in files[1:]:
            open_document_window(path, sibling=window)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
