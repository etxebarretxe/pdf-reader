"""Panel lateral de miniaturas.

Las miniaturas se generan bajo demanda (solo las filas visibles) en un pool de
hilos propio y reducido, para no competir con el renderizado de las paginas
grandes.
"""

from __future__ import annotations

from typing import Optional, Set

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem, QWidget

from core.document import PdfDocument
from core.render_engine import render_page_image

THUMB_WIDTH = 116


class _ThumbSignals(QObject):
    """Senales del panel (no de cada tarea: los QRunnable se autodestruyen)."""

    done = Signal(int, QImage)
    failed = Signal(int)


class _ThumbTask(QRunnable):
    def __init__(self, panel: "ThumbnailPanel", document: PdfDocument, index: int, generation: int):
        super().__init__()
        self.setAutoDelete(True)
        self._panel = panel
        self._document = document
        self._index = index
        self._generation = generation

    def run(self) -> None:  # pragma: no cover - se ejecuta en el pool
        signals = self._panel.signals
        if not self._panel.is_current(self._generation):
            signals.failed.emit(self._index)
            return
        width_pt, _ = self._document.page_size(self._index)
        zoom = THUMB_WIDTH / max(1.0, width_pt)
        try:
            image = render_page_image(self._document, self._index, zoom)
        except Exception:  # noqa: BLE001 - una miniatura fallida no es critica
            signals.failed.emit(self._index)
            return
        if image is None or not self._panel.is_current(self._generation):
            signals.failed.emit(self._index)
            return
        signals.done.emit(self._index, image)


class ThumbnailPanel(QListWidget):
    """Lista de miniaturas para saltar entre paginas."""

    page_selected = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.document: Optional[PdfDocument] = None
        self._loaded: Set[int] = set()
        self._pending: Set[int] = set()
        self._generation = 0
        self._syncing = False

        self.signals = _ThumbSignals(self)
        self.signals.done.connect(self._on_thumb, Qt.QueuedConnection)
        self.signals.failed.connect(self._pending.discard, Qt.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setWordWrap(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(THUMB_WIDTH + 46)
        self.setIconSize(QSize(THUMB_WIDTH, int(THUMB_WIDTH * 1.5)))
        self.currentRowChanged.connect(self._on_row_changed)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._load_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._timer.start())

    # ------------------------------------------------------------- documento

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    def set_document(self, document: Optional[PdfDocument]) -> None:
        self._generation += 1
        self._pool.clear()
        self._loaded.clear()
        self._pending.clear()
        self.clear()
        self.document = document
        if document is None:
            return

        placeholder = QPixmap(self.iconSize())
        placeholder.fill(QColor("#ffffff"))
        icon = QIcon(placeholder)
        for index in range(document.page_count):
            item = QListWidgetItem(icon, str(index + 1))
            item.setTextAlignment(Qt.AlignHCenter)
            item.setSizeHint(QSize(self.iconSize().width() + 16, self.iconSize().height() + 24))
            self.addItem(item)
        self._timer.start()

    def clear_document(self) -> None:
        self.set_document(None)

    # ------------------------------------------------------------------ carga

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._timer.start()

    def _load_visible(self) -> None:
        if self.document is None or not self.isVisible():
            return
        area = self.viewport().rect()
        first = self.indexAt(area.topLeft()).row()
        last = self.indexAt(area.bottomLeft()).row()
        if first < 0:
            first = 0
        if last < 0:
            last = min(self.count() - 1, first + 12)
        for index in range(max(0, first - 2), min(self.count() - 1, last + 2) + 1):
            if index in self._loaded or index in self._pending:
                continue
            self._pending.add(index)
            self._pool.start(_ThumbTask(self, self.document, index, self._generation))

    def _on_thumb(self, index: int, image: QImage) -> None:
        self._pending.discard(index)
        item = self.item(index)
        if item is None:
            return
        self._loaded.add(index)
        item.setIcon(QIcon(QPixmap.fromImage(image)))

    # ------------------------------------------------------------ navegacion

    def _on_row_changed(self, row: int) -> None:
        if self._syncing or row < 0:
            return
        self.page_selected.emit(row)

    def set_current_page(self, index: int) -> None:
        if index == self.currentRow() or index < 0 or index >= self.count():
            return
        self._syncing = True
        self.setCurrentRow(index)
        self.scrollToItem(self.item(index), QListWidget.PositionAtCenter)
        self._syncing = False
        self._timer.start()

    def shutdown(self) -> None:
        self._generation += 1
        self._pool.clear()
        self._pool.waitForDone(2000)
