"""Barra de busqueda flotante sobre el visor."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QWidget,
)

from ui import theme


class SearchBar(QFrame):
    """Campo de busqueda con contador y navegacion entre coincidencias."""

    search_requested = Signal(str)   # texto (busqueda incremental)
    next_requested = Signal()
    previous_requested = Signal()
    closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchBar")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(6)

        self.field = QLineEdit(self)
        self.field.setPlaceholderText("Buscar en el documento...")
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(240)
        layout.addWidget(self.field)

        self.counter = QLabel("", self)
        self.counter.setMinimumWidth(78)
        self.counter.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.counter)

        self.button_prev = QToolButton(self)
        self.button_prev.setAutoRaise(True)
        self.button_prev.setToolTip("Coincidencia anterior (Mayus+F3)")
        layout.addWidget(self.button_prev)

        self.button_next = QToolButton(self)
        self.button_next.setAutoRaise(True)
        self.button_next.setToolTip("Coincidencia siguiente (F3)")
        layout.addWidget(self.button_next)

        self.button_close = QToolButton(self)
        self.button_close.setAutoRaise(True)
        self.button_close.setText("✕")
        self.button_close.setToolTip("Cerrar (Esc)")
        layout.addWidget(self.button_close)

        # Busqueda incremental: se espera a que el usuario deje de teclear.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(lambda: self.search_requested.emit(self.field.text()))

        self.field.textChanged.connect(lambda _: self._debounce.start())
        self.field.returnPressed.connect(self._on_return)
        self.button_next.clicked.connect(self.next_requested)
        self.button_prev.clicked.connect(self.previous_requested)
        self.button_close.clicked.connect(self.hide_bar)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.hide_bar)
        QShortcut(QKeySequence("Shift+Return"), self, activated=self.previous_requested.emit)

        self.apply_theme(theme.LIGHT)
        self.hide()

    def _on_return(self) -> None:
        if self._debounce.isActive():
            self._debounce.stop()
            self.search_requested.emit(self.field.text())
        else:
            self.next_requested.emit()

    # ---------------------------------------------------------------- estado

    def show_bar(self, initial_text: str = "") -> None:
        if initial_text:
            self.field.setText(initial_text)
        self.show()
        self.raise_()
        self.field.setFocus()
        self.field.selectAll()

    def hide_bar(self) -> None:
        self.hide()
        self.closed.emit()

    def set_counter(self, current: int, total: int, searching: bool = False) -> None:
        if not self.field.text():
            self.counter.setText("")
        elif total == 0:
            self.counter.setText("Buscando..." if searching else "Sin resultados")
        else:
            suffix = "..." if searching else ""
            self.counter.setText(f"{current} de {total}{suffix}")
        self.button_next.setEnabled(total > 0)
        self.button_prev.setEnabled(total > 0)

    def term(self) -> str:
        return self.field.text()

    def apply_theme(self, name: str) -> None:
        self.button_prev.setIcon(theme.icon("prev", name))
        self.button_next.setIcon(theme.icon("next", name))
        tokens = {
            theme.LIGHT: ("#ffffff", "#c8c8c8"),
            theme.DARK: ("#26272b", "#3a3c41"),
        }[name if name in (theme.LIGHT, theme.DARK) else theme.LIGHT]
        self.setStyleSheet(
            f"#searchBar {{ background: {tokens[0]}; border: 1px solid {tokens[1]};"
            f" border-radius: 6px; }}"
        )
