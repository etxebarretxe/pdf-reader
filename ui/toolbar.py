"""Barra de herramientas minimalista.

Solo los controles de uso diario: abrir, navegacion, zoom, miniaturas,
busqueda y anotaciones. Nada de paneles vacios ni funciones de pago.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QComboBox, QLabel, QSizePolicy, QSpinBox, QToolBar, QWidget


def build_toolbar(window) -> QToolBar:
    """Construye la barra usando las acciones ya creadas por la ventana."""
    bar = QToolBar("Principal", window)
    bar.setMovable(False)
    bar.setIconSize(QSize(20, 20))
    bar.setToolButtonStyle(Qt.ToolButtonIconOnly)

    bar.addAction(window.action_open)
    bar.addSeparator()
    bar.addAction(window.action_thumbnails)
    bar.addSeparator()
    bar.addAction(window.action_prev)

    window.page_spin = QSpinBox(bar)
    window.page_spin.setRange(1, 1)
    window.page_spin.setFixedWidth(62)
    window.page_spin.setAlignment(Qt.AlignRight)
    window.page_spin.setToolTip("Ir a pagina (Ctrl+G)")
    window.page_spin.setKeyboardTracking(False)
    bar.addWidget(window.page_spin)

    window.page_total = QLabel(" / 0", bar)
    bar.addWidget(window.page_total)

    bar.addAction(window.action_next)
    bar.addSeparator()
    bar.addAction(window.action_zoom_out)

    window.zoom_combo = QComboBox(bar)
    window.zoom_combo.setEditable(True)
    window.zoom_combo.setFixedWidth(92)
    window.zoom_combo.setToolTip("Nivel de zoom")
    window.zoom_combo.addItems(
        ["Ancho", "Pagina", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"]
    )
    bar.addWidget(window.zoom_combo)

    bar.addAction(window.action_zoom_in)
    bar.addAction(window.action_fit_width)
    bar.addAction(window.action_fit_page)
    bar.addSeparator()
    bar.addAction(window.action_rotate_left)
    bar.addAction(window.action_rotate_right)
    bar.addSeparator()
    bar.addAction(window.action_search)

    for action in getattr(window, "annotation_actions", []):
        bar.addAction(action)

    spacer = QWidget(bar)
    spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    bar.addWidget(spacer)
    bar.addAction(window.action_theme)
    return bar
