"""Ventana principal: menus, atajos, dock de miniaturas y barra de estado.

Multiventana (no multipestana): abrir otro PDF crea otra ``QMainWindow`` dentro
del mismo proceso y la misma ``QApplication``, con su propia escena y su propia
cache de paginas.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
)

from core.document import DocumentError, PdfDocument
from ui import theme
from ui.thumbnail_panel import ThumbnailPanel
from ui.toolbar import build_toolbar
from ui.viewer_widget import (
    VIEW_CONTINUOUS,
    VIEW_SINGLE,
    ZOOM_FIT_PAGE,
    ZOOM_FIT_WIDTH,
    ZOOM_FREE,
    ViewerWidget,
)

APP_NAME = "Lector PDF"

#: Ventanas abiertas (evita que el recolector de basura las destruya).
_WINDOWS: List["MainWindow"] = []


def open_document_window(path: str, sibling: Optional["MainWindow"] = None) -> Optional["MainWindow"]:
    """Abre ``path`` en una ventana nueva del mismo proceso."""
    window = MainWindow(theme_name=sibling.theme_name if sibling else None)
    if not window.open_path(path):
        window.deleteLater()
        return None
    if sibling is not None:
        geometry = sibling.geometry()
        window.setGeometry(geometry.translated(28, 28))
    window.show()
    return window


class MainWindow(QMainWindow):
    def __init__(self, theme_name: Optional[str] = None) -> None:
        super().__init__()
        _WINDOWS.append(self)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAcceptDrops(True)
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 820)

        self.document: Optional[PdfDocument] = None
        self.settings = QSettings("LectorPDF", "LectorPDF")
        self.theme_name = theme_name or self.settings.value("theme", theme.LIGHT)
        if self.theme_name not in (theme.LIGHT, theme.DARK):
            self.theme_name = theme.LIGHT

        self.viewer = ViewerWidget(self)
        self.setCentralWidget(self.viewer)

        self.thumbnails = ThumbnailPanel(self)
        self.thumb_dock = QDockWidget("Miniaturas", self)
        self.thumb_dock.setObjectName("thumbnails")
        self.thumb_dock.setWidget(self.thumbnails)
        self.thumb_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.thumb_dock)
        self.thumb_dock.hide()

        self._create_actions()
        self.addToolBar(build_toolbar(self))
        self._create_menus()
        self._create_statusbar()
        self._connect()
        self._create_shortcuts()
        self.apply_theme(self.theme_name)
        self._update_enabled_state()

    # ---------------------------------------------------------------- acciones

    def _act(self, text: str, icon_name: Optional[str] = None, shortcut=None, tip: str = "") -> QAction:
        action = QAction(text, self)
        if icon_name:
            action.setIcon(theme.icon(icon_name, self.theme_name))
            action.setProperty("icon_name", icon_name)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.setToolTip(tip or text)
        action.setStatusTip(tip or text)
        return action

    def _create_actions(self) -> None:
        self.action_open = self._act("Abrir...", "open", QKeySequence.Open, "Abrir un PDF (Ctrl+O)")
        self.action_open_window = self._act("Abrir en ventana nueva...", None, "Ctrl+Shift+O")
        self.action_close = self._act("Cerrar ventana", None, QKeySequence.Close)
        self.action_quit = self._act("Salir", None, "Ctrl+Q")

        self.action_prev = self._act("Pagina anterior", "prev", None, "Pagina anterior")
        self.action_next = self._act("Pagina siguiente", "next", None, "Pagina siguiente")
        self.action_first = self._act("Primera pagina", None, "Ctrl+Home")
        self.action_last = self._act("Ultima pagina", None, "Ctrl+End")
        self.action_goto = self._act("Ir a pagina...", None, "Ctrl+G", "Ir a pagina (Ctrl+G)")

        self.action_zoom_in = self._act("Acercar", "zoom-in", QKeySequence.ZoomIn, "Acercar (Ctrl++)")
        self.action_zoom_out = self._act("Alejar", "zoom-out", QKeySequence.ZoomOut, "Alejar (Ctrl+-)")
        self.action_zoom_reset = self._act("Zoom 100%", None, "Ctrl+0")
        self.action_fit_width = self._act("Ajustar al ancho", "fit-width", "Ctrl+1")
        self.action_fit_page = self._act("Ajustar a la pagina", "fit-page", "Ctrl+2")
        self.action_fit_width.setCheckable(True)
        self.action_fit_page.setCheckable(True)

        self.action_rotate_left = self._act("Girar a la izquierda", "rotate-left", "Ctrl+Shift+Left")
        self.action_rotate_right = self._act("Girar a la derecha", "rotate-right", "Ctrl+Shift+Right")

        self.action_thumbnails = self._act("Miniaturas", "thumbnails", "F4", "Panel de miniaturas (F4)")
        self.action_thumbnails.setCheckable(True)

        self.action_continuous = self._act("Scroll continuo", None, None)
        self.action_continuous.setCheckable(True)
        self.action_continuous.setChecked(True)
        self.action_single = self._act("Una pagina", None, None)
        self.action_single.setCheckable(True)

        self.action_search = self._act("Buscar", "search", QKeySequence.Find, "Buscar en el documento (Ctrl+F)")
        self.action_theme = self._act("Tema claro / oscuro", "theme", "Ctrl+D", "Cambiar tema (Ctrl+D)")

    def _create_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&Archivo")
        file_menu.addAction(self.action_open)
        file_menu.addAction(self.action_open_window)
        file_menu.addSeparator()
        file_menu.addAction(self.action_close)
        file_menu.addAction(self.action_quit)

        view_menu = menubar.addMenu("&Ver")
        view_menu.addAction(self.action_zoom_in)
        view_menu.addAction(self.action_zoom_out)
        view_menu.addAction(self.action_zoom_reset)
        view_menu.addAction(self.action_fit_width)
        view_menu.addAction(self.action_fit_page)
        view_menu.addSeparator()
        view_menu.addAction(self.action_rotate_left)
        view_menu.addAction(self.action_rotate_right)
        view_menu.addSeparator()
        view_menu.addAction(self.action_continuous)
        view_menu.addAction(self.action_single)
        view_menu.addSeparator()
        view_menu.addAction(self.action_thumbnails)
        view_menu.addAction(self.action_theme)

        go_menu = menubar.addMenu("&Ir")
        go_menu.addAction(self.action_prev)
        go_menu.addAction(self.action_next)
        go_menu.addAction(self.action_first)
        go_menu.addAction(self.action_last)
        go_menu.addAction(self.action_goto)

        self.menu_search = menubar.addMenu("&Buscar")
        self.menu_search.addAction(self.action_search)

    def _create_statusbar(self) -> None:
        self.status_page = QLabel("", self)
        self.status_zoom = QLabel("", self)
        self.statusBar().addPermanentWidget(self.status_page)
        self.statusBar().addPermanentWidget(self.status_zoom)

    def _connect(self) -> None:
        self.action_open.triggered.connect(self.on_open)
        self.action_open_window.triggered.connect(lambda: self.on_open(new_window=True))
        self.action_close.triggered.connect(self.close)
        self.action_quit.triggered.connect(QApplication.instance().closeAllWindows)

        self.action_prev.triggered.connect(self.viewer.previous_page)
        self.action_next.triggered.connect(self.viewer.next_page)
        self.action_first.triggered.connect(self.viewer.first_page)
        self.action_last.triggered.connect(self.viewer.last_page)
        self.action_goto.triggered.connect(self.on_goto_page)

        self.action_zoom_in.triggered.connect(lambda: self.viewer.zoom_in())
        self.action_zoom_out.triggered.connect(lambda: self.viewer.zoom_out())
        self.action_zoom_reset.triggered.connect(lambda: self.viewer.set_zoom(1.0, ZOOM_FREE))
        self.action_fit_width.triggered.connect(lambda: self.viewer.set_zoom_mode(ZOOM_FIT_WIDTH))
        self.action_fit_page.triggered.connect(lambda: self.viewer.set_zoom_mode(ZOOM_FIT_PAGE))

        self.action_rotate_left.triggered.connect(lambda: self.viewer.rotate(-90))
        self.action_rotate_right.triggered.connect(lambda: self.viewer.rotate(90))

        self.action_thumbnails.toggled.connect(self.thumb_dock.setVisible)
        self.thumb_dock.visibilityChanged.connect(self.action_thumbnails.setChecked)

        self.action_continuous.triggered.connect(lambda: self.set_view_mode(VIEW_CONTINUOUS))
        self.action_single.triggered.connect(lambda: self.set_view_mode(VIEW_SINGLE))
        self.action_theme.triggered.connect(self.toggle_theme)

        self.viewer.page_changed.connect(self.on_page_changed)
        self.viewer.zoom_changed.connect(self.on_zoom_changed)
        self.thumbnails.page_selected.connect(self.viewer.goto_page)
        self.page_spin.valueChanged.connect(lambda value: self.viewer.goto_page(value - 1))
        self.zoom_combo.activated.connect(self.on_zoom_combo)
        self.zoom_combo.lineEdit().returnPressed.connect(
            lambda: self.on_zoom_combo(self.zoom_combo.currentIndex())
        )

    def _create_shortcuts(self) -> None:
        # Atajos de navegacion tipicos de lector.
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=self._page_down)
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=self._page_up)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._page_down)
        QShortcut(QKeySequence("Shift+Space"), self, activated=self._page_up)
        QShortcut(QKeySequence(Qt.Key_Home), self, activated=self.viewer.first_page)
        QShortcut(QKeySequence(Qt.Key_End), self, activated=self.viewer.last_page)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.viewer.zoom_in())

    def _page_down(self) -> None:
        if self.viewer.view_mode == VIEW_SINGLE:
            self.viewer.next_page()
        else:
            self.viewer.scroll_by(int(self.viewer.viewport().height() * 0.92))

    def _page_up(self) -> None:
        if self.viewer.view_mode == VIEW_SINGLE:
            self.viewer.previous_page()
        else:
            self.viewer.scroll_by(-int(self.viewer.viewport().height() * 0.92))

    # ------------------------------------------------------------- documentos

    def on_open(self, new_window: bool = False) -> None:
        start_dir = os.path.dirname(self.document.path) if self.document else ""
        path, _ = QFileDialog.getOpenFileName(self, "Abrir PDF", start_dir, "Documentos PDF (*.pdf)")
        if not path:
            return
        if new_window or self.document is not None:
            open_document_window(path, sibling=self)
        else:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        try:
            document = PdfDocument(path)
        except DocumentError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False

        self._release_document()
        self.document = document
        self.viewer.set_document(document)
        self.thumbnails.set_document(document)
        self.setWindowTitle(f"{document.filename} - {APP_NAME}")
        self.page_spin.setRange(1, document.page_count)
        self.page_total.setText(f" / {document.page_count}")
        self._update_enabled_state()
        self.on_page_changed(0)
        self.on_zoom_changed(self.viewer.zoom)
        return True

    def _release_document(self) -> None:
        self.thumbnails.set_document(None)
        self.viewer.close_document()
        if self.document is not None:
            self.document.close()
            self.document = None

    def _update_enabled_state(self) -> None:
        has_doc = self.document is not None
        for action in (
            self.action_prev, self.action_next, self.action_first, self.action_last,
            self.action_goto, self.action_zoom_in, self.action_zoom_out,
            self.action_zoom_reset, self.action_fit_width, self.action_fit_page,
            self.action_rotate_left, self.action_rotate_right, self.action_search,
            self.action_thumbnails, self.action_continuous, self.action_single,
        ):
            action.setEnabled(has_doc)
        for action in getattr(self, "annotation_actions", []):
            action.setEnabled(has_doc)
        self.page_spin.setEnabled(has_doc)
        self.zoom_combo.setEnabled(has_doc)
        if not has_doc:
            self.status_page.setText("")
            self.status_zoom.setText("")
            self.statusBar().showMessage("Abre un PDF con Ctrl+O o arrastralo a la ventana")

    # ------------------------------------------------------------------ vista

    def set_view_mode(self, mode: str) -> None:
        self.action_continuous.setChecked(mode == VIEW_CONTINUOUS)
        self.action_single.setChecked(mode == VIEW_SINGLE)
        self.viewer.set_view_mode(mode)

    def on_page_changed(self, index: int) -> None:
        if self.document is None:
            return
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self.status_page.setText(f"Pagina {index + 1} de {self.document.page_count}   ")
        self.thumbnails.set_current_page(index)

    def on_zoom_changed(self, zoom: float) -> None:
        self.status_zoom.setText(f"{round(zoom * 100)}%")
        self.action_fit_width.setChecked(self.viewer.zoom_mode == ZOOM_FIT_WIDTH)
        self.action_fit_page.setChecked(self.viewer.zoom_mode == ZOOM_FIT_PAGE)
        edit = self.zoom_combo.lineEdit()
        if self.viewer.zoom_mode == ZOOM_FIT_WIDTH:
            edit.setText("Ancho")
        elif self.viewer.zoom_mode == ZOOM_FIT_PAGE:
            edit.setText("Pagina")
        else:
            edit.setText(f"{round(zoom * 100)}%")

    def on_zoom_combo(self, _index: int) -> None:
        text = self.zoom_combo.currentText().strip().lower()
        if text.startswith("anch"):
            self.viewer.set_zoom_mode(ZOOM_FIT_WIDTH)
            return
        if text.startswith("pag"):
            self.viewer.set_zoom_mode(ZOOM_FIT_PAGE)
            return
        try:
            value = float(text.replace("%", "").replace(",", ".").strip())
        except ValueError:
            self.on_zoom_changed(self.viewer.zoom)
            return
        self.viewer.set_zoom(value / 100.0, ZOOM_FREE)

    def on_goto_page(self) -> None:
        if self.document is None:
            return
        value, ok = QInputDialog.getInt(
            self, "Ir a pagina", f"Pagina (1-{self.document.page_count}):",
            self.viewer.current_page() + 1, 1, self.document.page_count,
        )
        if ok:
            self.viewer.goto_page(value - 1)

    # ------------------------------------------------------------------- tema

    def toggle_theme(self) -> None:
        self.apply_theme(theme.DARK if self.theme_name == theme.LIGHT else theme.LIGHT)

    def apply_theme(self, name: str) -> None:
        self.theme_name = name
        self.settings.setValue("theme", name)
        self.setStyleSheet(theme.stylesheet(name))
        self.viewer.apply_theme(name)
        for action in self.findChildren(QAction):
            icon_name = action.property("icon_name")
            if icon_name:
                action.setIcon(theme.icon(icon_name, name))

    # --------------------------------------------------------- arrastrar/soltar

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(
            url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith(".pdf")
        ]
        if not paths:
            return
        event.acceptProposedAction()
        if self.document is None:
            self.open_path(paths[0])
            paths = paths[1:]
        for path in paths:
            open_document_window(path, sibling=self)

    # ----------------------------------------------------------------- cierre

    def closeEvent(self, event) -> None:  # noqa: N802
        self.thumbnails.shutdown()
        self._release_document()
        if self in _WINDOWS:
            _WINDOWS.remove(self)
        super().closeEvent(event)
