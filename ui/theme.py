"""Temas claro/oscuro e iconos SVG.

Los iconos son SVG monocromos con el color de trazo ``#333333`` como marcador;
al cargarlos se sustituye por el color del tema activo, de modo que un unico
juego de assets sirve para los dos temas.
"""

from __future__ import annotations

import os
from typing import Dict

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_PLACEHOLDER = "#333333"

LIGHT = "light"
DARK = "dark"

#: Colores usados tambien por el visor (fondo de la escena, borde de pagina...).
PALETTE = {
    LIGHT: {
        "icon": "#3c3c3c",
        "scene": "#9a9a9a",
        "page": "#ffffff",
        "page_border": "#6f6f6f",
        "window": "#f4f4f4",
        "text": "#202020",
        "accent": "#2f6feb",
    },
    DARK: {
        "icon": "#d8d8d8",
        "scene": "#2a2b2e",
        "page": "#ffffff",
        "page_border": "#101114",
        "window": "#1f2023",
        "text": "#e6e6e6",
        "accent": "#5b9dff",
    },
}

_STYLESHEET = """
QWidget {{ background: {window}; color: {text}; }}
QMainWindow::separator {{ background: {sep}; width: 1px; height: 1px; }}
QToolBar {{ border: 0; padding: 3px; spacing: 2px; background: {window}; }}
QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 4px; }}
QToolButton:hover {{ background: {hover}; }}
QToolButton:pressed, QToolButton:checked {{ background: {pressed}; border-color: {sep}; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {field}; border: 1px solid {sep}; border-radius: 4px; padding: 3px 6px;
    selection-background-color: {accent}; selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
QMenuBar, QMenu {{ background: {window}; color: {text}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {accent}; color: #ffffff; }}
QMenu {{ border: 1px solid {sep}; }}
QStatusBar {{ background: {window}; color: {text}; }}
QStatusBar::item {{ border: 0; }}
QDockWidget::title {{ background: {window}; padding: 5px; }}
QListWidget {{ background: {field}; border: 0; }}
QListWidget::item:selected {{ background: {accent}; color: #ffffff; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {scroll}; border-radius: 5px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {window}; color: {text}; border: 1px solid {sep}; }}
"""

_TOKENS = {
    LIGHT: dict(
        window="#f4f4f4", text="#202020", sep="#c8c8c8", hover="#e2e2e2",
        pressed="#d2d2d2", field="#ffffff", accent="#2f6feb",
        scroll="#b6b6b6", scroll_hover="#9a9a9a",
    ),
    DARK: dict(
        window="#1f2023", text="#e6e6e6", sep="#3a3c41", hover="#2c2e33",
        pressed="#3a3d44", field="#141518", accent="#5b9dff",
        scroll="#4a4d54", scroll_hover="#63676f",
    ),
}

_icon_cache: Dict[str, QIcon] = {}


def stylesheet(theme: str) -> str:
    return _STYLESHEET.format(**_TOKENS.get(theme, _TOKENS[LIGHT]))


def color(theme: str, role: str) -> QColor:
    return QColor(PALETTE.get(theme, PALETTE[LIGHT])[role])


def clear_icon_cache() -> None:
    _icon_cache.clear()


def icon(name: str, theme: str = LIGHT) -> QIcon:
    """Carga ``assets/<name>.svg`` recoloreado segun el tema."""
    key = f"{theme}/{name}"
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    path = os.path.join(ASSETS_DIR, f"{name}.svg")
    result = QIcon()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            data = handle.read().replace(_PLACEHOLDER, PALETTE[theme]["icon"])
        renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
        for size in (16, 20, 24, 32, 48):
            image = QImage(QSize(size, size), QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            result.addPixmap(QPixmap.fromImage(image))
    _icon_cache[key] = result
    return result
