"""Ventana principal: menus, atajos, dock de miniaturas y barra de estado.

Multiventana (no multipestana): abrir otro PDF crea otra ``QMainWindow`` dentro
del mismo proceso y la misma ``QApplication``, con su propia escena y su propia
cache de paginas.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
)

from core import annotations
from core.document import DocumentError, PdfDocument
from core.recent_files import Settings
from core.search import SearchController
from ui import theme
from ui.search_bar import SearchBar
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

SHORTCUTS_HELP = """<b>Archivo</b><br>
Ctrl+O abrir &nbsp;|&nbsp; Ctrl+Mayus+O abrir en ventana nueva &nbsp;|&nbsp; Ctrl+S guardar anotaciones<br><br>
<b>Navegacion</b><br>
PgUp / PgDn / Espacio avanzar &nbsp;|&nbsp; Inicio / Fin primera y ultima pagina<br>
Ctrl+G ir a pagina &nbsp;|&nbsp; F4 miniaturas<br><br>
<b>Vista</b><br>
Ctrl++ / Ctrl+- zoom &nbsp;|&nbsp; Ctrl+0 al 100% &nbsp;|&nbsp; Ctrl+1 ajustar al ancho<br>
Ctrl+2 ajustar a la pagina &nbsp;|&nbsp; Ctrl+rueda zoom con el raton<br>
Ctrl+Mayus+Izq / Der girar &nbsp;|&nbsp; Ctrl+D tema claro / oscuro<br>
Boton central del raton: desplazar arrastrando<br><br>
<b>Busqueda</b><br>
Ctrl+F buscar &nbsp;|&nbsp; F3 / Mayus+F3 siguiente y anterior &nbsp;|&nbsp; Esc cerrar<br><br>
<b>Anotaciones</b><br>
Arrastrar para seleccionar texto (doble clic selecciona una palabra)<br>
H resaltar &nbsp;|&nbsp; U subrayar &nbsp;|&nbsp; T tachar &nbsp;|&nbsp; N nota adhesiva<br>
Ctrl+C copiar &nbsp;|&nbsp; boton derecho: menu contextual"""

#: Ventanas abiertas (evita que el recolector de basura las destruya).
_WINDOWS: List["MainWindow"] = []

#: Configuracion compartida por todas las ventanas del proceso.
_SETTINGS: Optional[Settings] = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS


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
        self.settings = get_settings()
        self.theme_name = theme_name or self.settings.get("theme", theme.LIGHT)
        if self.theme_name not in (theme.LIGHT, theme.DARK):
            self.theme_name = theme.LIGHT
        self._restore_geometry()

        self.viewer = ViewerWidget(self)
        self.setCentralWidget(self.viewer)

        self.thumbnails = ThumbnailPanel(self)
        self.thumb_dock = QDockWidget("Miniaturas", self)
        self.thumb_dock.setObjectName("thumbnails")
        self.thumb_dock.setWidget(self.thumbnails)
        self.thumb_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.thumb_dock)
        self.thumb_dock.hide()

        self.save_pool = QThreadPool(self)
        self.save_pool.setMaxThreadCount(1)
        self._dirty = False
        self._saving = False
        self._read_only = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self.save_annotations)

        self.search = SearchController(self)
        self.search_bar = SearchBar(self.viewer)
        self._current_match = None
        self._searching = False
        self.viewer.installEventFilter(self)

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
        self.action_find_next = self._act("Buscar siguiente", None, QKeySequence.FindNext)
        self.action_find_prev = self._act("Buscar anterior", None, QKeySequence.FindPrevious)
        self.action_theme = self._act("Tema claro / oscuro", "theme", "Ctrl+D", "Cambiar tema (Ctrl+D)")

        self.action_highlight = self._act(
            "Resaltar seleccion", "highlight", "H", "Resaltar el texto seleccionado (H)"
        )
        self.action_underline = self._act(
            "Subrayar seleccion", "underline", "U", "Subrayar el texto seleccionado (U)"
        )
        self.action_strikeout = self._act(
            "Tachar seleccion", "strike", "T", "Tachar el texto seleccionado (T)"
        )
        self.action_note = self._act(
            "Nota adhesiva", "note", "N", "Colocar una nota en la pagina (N)"
        )
        self.action_save = self._act(
            "Guardar anotaciones", None, QKeySequence.Save, "Guardar las anotaciones en el PDF (Ctrl+S)"
        )
        self.action_copy = self._act("Copiar texto seleccionado", None, QKeySequence.Copy)
        self.action_shortcuts = self._act("Atajos de teclado", None, "F1")
        self.annotation_actions = [
            self.action_highlight, self.action_underline,
            self.action_strikeout, self.action_note,
        ]

        # Color activo de cada tipo de marca.
        self.highlight_color = annotations.HIGHLIGHT_COLORS[0][1]
        self.line_color = annotations.LINE_COLORS[0][1]

    def _create_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&Archivo")
        file_menu.addAction(self.action_open)
        file_menu.addAction(self.action_open_window)
        self.menu_recent = file_menu.addMenu("Documentos &recientes")
        self.menu_recent.aboutToShow.connect(self._fill_recent_menu)
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

        annot_menu = menubar.addMenu("&Anotar")
        annot_menu.addAction(self.action_highlight)
        annot_menu.addMenu(self._color_menu(
            "Color de resaltado", annotations.HIGHLIGHT_COLORS, self._set_highlight_color
        ))
        annot_menu.addSeparator()
        annot_menu.addAction(self.action_underline)
        annot_menu.addAction(self.action_strikeout)
        annot_menu.addMenu(self._color_menu(
            "Color de linea", annotations.LINE_COLORS, self._set_line_color
        ))
        annot_menu.addSeparator()
        annot_menu.addAction(self.action_note)
        annot_menu.addSeparator()
        annot_menu.addAction(self.action_copy)
        annot_menu.addAction(self.action_save)

        self.menu_search = menubar.addMenu("&Buscar")
        self.menu_search.addAction(self.action_search)
        self.menu_search.addAction(self.action_find_next)
        self.menu_search.addAction(self.action_find_prev)

        help_menu = menubar.addMenu("A&yuda")
        help_menu.addAction(self.action_shortcuts)

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
        self.action_shortcuts.triggered.connect(self.on_shortcuts)

        self.action_highlight.triggered.connect(
            lambda: self.annotate(annotations.HIGHLIGHT))
        self.action_underline.triggered.connect(
            lambda: self.annotate(annotations.UNDERLINE))
        self.action_strikeout.triggered.connect(
            lambda: self.annotate(annotations.STRIKEOUT))
        self.action_note.triggered.connect(self.on_add_note)
        self.action_save.triggered.connect(self.save_annotations)
        self.action_copy.triggered.connect(self.on_copy)
        self.viewer.note_point_picked.connect(self.on_note_point)
        self.viewer.context_requested.connect(self.on_context_menu)
        self.viewer.selection_changed.connect(self.on_selection_changed)

        self.action_search.triggered.connect(self.on_search)
        self.action_find_next.triggered.connect(self.on_find_next)
        self.action_find_prev.triggered.connect(self.on_find_previous)
        self.search_bar.search_requested.connect(self.on_search_term)
        self.search_bar.next_requested.connect(self.on_find_next)
        self.search_bar.previous_requested.connect(self.on_find_previous)
        self.search_bar.closed.connect(self.on_search_closed)
        self.search.matches_updated.connect(self.on_matches_updated)
        self.search.finished.connect(self.on_search_finished)

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
        self._read_only = not annotations.is_writable(document.path)
        self._dirty = False
        self._update_title()
        self.page_spin.setRange(1, document.page_count)
        self.page_total.setText(f" / {document.page_count}")
        self._update_enabled_state()
        self.on_selection_changed(False)
        if self._read_only:
            self.statusBar().showMessage(
                "Archivo de solo lectura: no se podran guardar anotaciones", 6000
            )
        self._restore_document_state(document.path)
        self.settings.add_recent(document.path)
        self.settings.save()
        self.on_page_changed(self.viewer.current_page())
        self.on_zoom_changed(self.viewer.zoom)
        return True

    def _release_document(self) -> None:
        self._remember_document()
        self._save_timer.stop()
        self.search.cancel()
        self._current_match = None
        self.search_bar.hide()
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
            self.action_find_next, self.action_find_prev, self.action_copy,
        ):
            action.setEnabled(has_doc)
        for action in getattr(self, "annotation_actions", []):
            action.setEnabled(has_doc and not self._read_only)
        self.action_save.setEnabled(has_doc and not self._read_only)
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

    # ------------------------------------------------------------ anotaciones

    def _color_menu(self, title: str, colors, setter) -> QMenu:
        menu = QMenu(title, self)
        for name, value in colors:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setData(value)
            action.triggered.connect(lambda _checked=False, v=value, m=menu: setter(v, m))
        menu.actions()[0].setChecked(True)
        return menu

    def _set_highlight_color(self, value, menu: QMenu) -> None:
        self.highlight_color = value
        for action in menu.actions():
            action.setChecked(action.data() == value)

    def _set_line_color(self, value, menu: QMenu) -> None:
        self.line_color = value
        for action in menu.actions():
            action.setChecked(action.data() == value)

    def on_selection_changed(self, has_selection: bool) -> None:
        enabled = has_selection and self.document is not None and not self._read_only
        for action in (self.action_highlight, self.action_underline, self.action_strikeout):
            action.setEnabled(enabled)
        self.action_copy.setEnabled(has_selection)

    def on_copy(self) -> None:
        if self.viewer.has_selection():
            QApplication.clipboard().setText(self.viewer.selection.text)
            self.statusBar().showMessage("Texto copiado al portapapeles", 2000)

    def _check_writable(self) -> bool:
        if self.document is None:
            return False
        if self._read_only:
            QMessageBox.information(
                self, APP_NAME,
                "El archivo es de solo lectura, no se pueden guardar anotaciones en el.",
            )
            return False
        return True

    def annotate(self, kind: str) -> None:
        if not self.viewer.has_selection() or not self._check_writable():
            return
        selection = self.viewer.selection
        color = self.highlight_color if kind == annotations.HIGHLIGHT else self.line_color
        try:
            annotations.add_text_markup(
                self.document, selection.page_index, selection.rects, kind, color,
                author=self.author_name,
            )
        except annotations.AnnotationError as exc:
            QMessageBox.warning(self, APP_NAME, f"No se ha podido anotar:\n{exc}")
            return
        page = selection.page_index
        self.viewer.clear_selection()
        self.viewer.refresh_page(page)
        self.mark_dirty()

    def on_add_note(self) -> None:
        if not self._check_writable():
            return
        self.viewer.set_note_mode(True)
        self.statusBar().showMessage(
            "Haz clic en el punto de la pagina donde quieres colocar la nota", 6000
        )

    def on_note_point(self, page_index: int, x: float, y: float) -> None:
        if not self._check_writable():
            return
        text, ok = QInputDialog.getMultiLineText(self, "Nota adhesiva", "Texto de la nota:")
        if not ok or not text.strip():
            return
        try:
            annotations.add_note(self.document, page_index, (x, y), text, self.author_name)
        except annotations.AnnotationError as exc:
            QMessageBox.warning(self, APP_NAME, f"No se ha podido crear la nota:\n{exc}")
            return
        self.viewer.refresh_page(page_index)
        self.mark_dirty()

    def on_context_menu(self, page_index: int, x: float, y: float, global_pos) -> None:
        if self.document is None:
            return
        menu = QMenu(self)
        if self.viewer.has_selection():
            menu.addAction(self.action_highlight)
            menu.addAction(self.action_underline)
            menu.addAction(self.action_strikeout)
            menu.addAction(self.action_copy)
            menu.addSeparator()
        menu.addAction(self.action_note)

        found = annotations.annotation_at(self.document, page_index, x, y)
        if found is not None and not self._read_only:
            xref, kind, content = found
            menu.addSeparator()
            if kind == "Text":
                edit = menu.addAction("Editar nota...")
                edit.triggered.connect(
                    lambda _checked=False: self._edit_note(page_index, xref, content)
                )
            delete = menu.addAction("Eliminar anotacion")
            delete.triggered.connect(
                lambda _checked=False: self._delete_annotation(page_index, xref)
            )
        menu.exec(global_pos)

    def _edit_note(self, page_index: int, xref: int, content: str) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Editar nota", "Texto de la nota:", content)
        if not ok:
            return
        annotations.update_note(self.document, page_index, xref, text)
        self.viewer.refresh_page(page_index)
        self.mark_dirty()

    def _delete_annotation(self, page_index: int, xref: int) -> None:
        if annotations.delete_annotation(self.document, page_index, xref):
            self.viewer.refresh_page(page_index)
            self.mark_dirty()

    # ------------------------------------------------------------- guardado

    @property
    def author_name(self) -> str:
        return str(self.settings.get("author", "") or "")

    def mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()
        self._save_timer.start()

    def _update_title(self) -> None:
        if self.document is None:
            self.setWindowTitle(APP_NAME)
            return
        mark = " *" if self._dirty else ""
        suffix = "  [solo lectura]" if self._read_only else ""
        self.setWindowTitle(f"{self.document.filename}{mark}{suffix} - {APP_NAME}")

    def save_annotations(self) -> None:
        if self.document is None or not self._dirty or self._saving or self._read_only:
            return
        self._saving = True
        self._save_timer.stop()
        self.statusBar().showMessage("Guardando anotaciones...", 2000)
        task = annotations.SaveTask(self.document)
        task.signals.done.connect(self._on_saved, Qt.QueuedConnection)
        task.signals.failed.connect(self._on_save_failed, Qt.QueuedConnection)
        self.save_pool.start(task)

    def _on_saved(self, incremental: bool, temp_path: str) -> None:
        self._saving = False
        self._dirty = False
        if not incremental and temp_path:
            self._finish_full_save(temp_path)
        self._update_title()
        self.statusBar().showMessage("Anotaciones guardadas en el PDF", 3000)

    def _on_save_failed(self, message: str) -> None:
        self._saving = False
        QMessageBox.warning(
            self, APP_NAME, f"No se han podido guardar las anotaciones:\n{message}"
        )

    def _finish_full_save(self, temp_path: str) -> None:
        """Documento sin guardado incremental: se sustituye y se reabre."""
        if self.document is None:
            return
        path = self.document.path
        page = self.viewer.current_page()
        zoom, zoom_mode = self.viewer.zoom, self.viewer.zoom_mode
        rotation = self.viewer.rotation
        self._release_document()
        try:
            annotations.replace_with_temp(path, temp_path)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"No se ha podido guardar:\n{exc}")
        self.open_path(path)
        self.viewer.rotation = rotation
        self.viewer.set_zoom(zoom, zoom_mode)
        self.viewer.goto_page(page)

    def _save_before_close(self) -> None:
        """Guardado sincrono al cerrar (incremental: milisegundos)."""
        if self.document is None or not self._dirty or self._read_only:
            return
        try:
            with self.document.lock:
                if self.document.raw.can_save_incrementally():
                    self.document.raw.save(
                        self.document.path, incremental=True,
                        encryption=annotations.pymupdf.PDF_ENCRYPT_KEEP,
                    )
                    self._dirty = False
                    return
                temp_path = self.document.path + ".lectorpdf.tmp"
                self.document.raw.save(temp_path, garbage=3, deflate=True)
            path = self.document.path
            self.document.close()
            self.document = None
            annotations.replace_with_temp(path, temp_path)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, APP_NAME, f"No se han podido guardar las anotaciones:\n{exc}"
            )

    # --------------------------------------------------------------- busqueda

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.viewer and event.type() == QEvent.Resize:
            self._place_search_bar()
        return super().eventFilter(obj, event)

    def _place_search_bar(self) -> None:
        bar = self.search_bar
        bar.adjustSize()
        bar.move(max(8, self.viewer.width() - bar.width() - 22), 10)

    def on_search(self) -> None:
        if self.document is None:
            return
        selected = self.viewer.selection.text if self.viewer.has_selection() else ""
        self._place_search_bar()
        self.search_bar.show_bar(selected[:120])
        if selected:
            self.on_search_term(selected[:120])

    def on_search_term(self, term: str) -> None:
        if self.document is None:
            return
        self._current_match = None
        self.viewer.clear_search()
        term = term.strip()
        if not term:
            self.search.cancel()
            self._searching = False
            self.search_bar.set_counter(0, 0)
            return
        self._searching = True
        self.search_bar.set_counter(0, 0, searching=True)
        self.search.start(
            self.document.path, term, self.document.page_count, self.viewer.current_page()
        )

    def on_matches_updated(self, matches) -> None:
        # Resaltado progresivo: se pintan las coincidencias segun llegan.
        self.viewer.set_search_matches({page: list(rects) for page, rects in matches.items()})
        if self._current_match is None:
            first = self.search.first_from(self.viewer.current_page())
            if first is not None:
                self._select_match(first)
        self._update_search_counter()

    def on_search_finished(self, total: int) -> None:
        self._searching = False
        self._update_search_counter()
        if total == 0 and self.search_bar.term().strip():
            self.statusBar().showMessage(
                f"Sin coincidencias para \u00ab{self.search_bar.term()}\u00bb", 4000
            )

    def _update_search_counter(self) -> None:
        total = self.search.count()
        position = 0
        if self._current_match is not None:
            position = self.search.index_of(*self._current_match) + 1
        self.search_bar.set_counter(position, total, self._searching)

    def _select_match(self, match) -> None:
        self._current_match = match
        self.viewer.set_current_match(match[0], match[1])
        self._update_search_counter()

    def on_find_next(self) -> None:
        if self.search.count() == 0:
            return
        reference = self._current_match or (self.viewer.current_page() - 1, 1 << 30)
        match = self.search.next_after(*reference)
        if match is not None:
            self._select_match(match)

    def on_find_previous(self) -> None:
        if self.search.count() == 0:
            return
        reference = self._current_match or (self.viewer.current_page(), 0)
        match = self.search.previous_before(*reference)
        if match is not None:
            self._select_match(match)

    def on_search_closed(self) -> None:
        self.search.cancel()
        self._searching = False
        self._current_match = None
        self.viewer.clear_search()
        self.viewer.setFocus()

    def on_shortcuts(self) -> None:
        QMessageBox.information(self, "Atajos de teclado", SHORTCUTS_HELP)

    # --------------------------------------------------- recientes y estado

    def _fill_recent_menu(self) -> None:
        self.menu_recent.clear()
        self.settings.prune_missing()
        entries = self.settings.recent_files()
        if not entries:
            empty = self.menu_recent.addAction("(vacio)")
            empty.setEnabled(False)
            return
        for position, entry in enumerate(entries, start=1):
            path = entry["path"]
            label = f"&{position}  {entry.get('name', os.path.basename(path))}"
            action = self.menu_recent.addAction(label)
            action.setStatusTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self._open_recent(p))
        self.menu_recent.addSeparator()
        clear = self.menu_recent.addAction("Vaciar la lista")
        clear.triggered.connect(self._clear_recent)

    def _open_recent(self, path: str) -> None:
        if not os.path.exists(path):
            QMessageBox.information(self, APP_NAME, "El archivo ya no existe:\n" + path)
            self.settings.remove_recent(path)
            self.settings.save()
            return
        if self.document is None:
            self.open_path(path)
        else:
            open_document_window(path, sibling=self)

    def _clear_recent(self) -> None:
        self.settings.clear_recent()
        self.settings.save()

    def _current_document_state(self) -> dict:
        return {
            "page": self.viewer.current_page(),
            "zoom": round(self.viewer.zoom, 4),
            "zoom_mode": self.viewer.zoom_mode,
            "rotation": self.viewer.rotation,
            "view_mode": self.viewer.view_mode,
        }

    def _remember_document(self) -> None:
        """Guarda la posicion de lectura del documento actual."""
        if self.document is None:
            return
        self.settings.add_recent(self.document.path)
        self.settings.remember_document(self.document.path, self._current_document_state())
        self.settings.save()

    def _restore_document_state(self, path: str) -> None:
        """Reabre el documento donde se dejo (pagina, zoom, giro, modo)."""
        state = self.settings.document_state(path)
        if not state:
            return
        rotation = int(state.get("rotation", 0)) % 360
        if rotation:
            self.viewer.rotation = rotation
        view_mode = state.get("view_mode")
        if view_mode in (VIEW_CONTINUOUS, VIEW_SINGLE):
            self.set_view_mode(view_mode)
        zoom_mode = state.get("zoom_mode", ZOOM_FIT_WIDTH)
        if zoom_mode == ZOOM_FREE:
            self.viewer.set_zoom(float(state.get("zoom", 1.0)), ZOOM_FREE)
        else:
            self.viewer.set_zoom_mode(zoom_mode)
        page = int(state.get("page", 0))
        if page:
            self.viewer.goto_page(page)

    def _restore_geometry(self) -> None:
        window = self.settings.get("window", {}) or {}
        width, height = window.get("width"), window.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 400 and height > 300:
            self.resize(width, height)
        if window.get("maximized"):
            self.showMaximized()

    def _remember_geometry(self) -> None:
        size = self.normalGeometry().size()
        self.settings.set("window", {
            "width": size.width(),
            "height": size.height(),
            "maximized": self.isMaximized(),
        })

    # ------------------------------------------------------------------- tema

    def toggle_theme(self) -> None:
        self.apply_theme(theme.DARK if self.theme_name == theme.LIGHT else theme.LIGHT)

    def apply_theme(self, name: str) -> None:
        self.theme_name = name
        self.settings.set("theme", name)
        self.settings.save()
        self.setStyleSheet(theme.stylesheet(name))
        self.viewer.apply_theme(name)
        self.search_bar.apply_theme(name)
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
        self._remember_geometry()
        # Se anota la posicion de lectura antes de guardar: un guardado
        # completo cierra y sustituye el documento.
        self._remember_document()
        self._save_timer.stop()
        self.save_pool.waitForDone(5000)
        self._save_before_close()
        self.search.shutdown()
        self.thumbnails.shutdown()
        self._release_document()
        self.settings.save()
        if self in _WINDOWS:
            _WINDOWS.remove(self)
        super().closeEvent(event)
