"""Busqueda de texto asincrona.

La busqueda global NUNCA se ejecuta en el hilo de la GUI: en un expediente de
cientos de paginas congelaria la interfaz varios segundos. Se ejecuta en un
``QRunnable`` que:

  * abre su propio handle de PyMuPDF sobre el mismo fichero, para no competir
    por el lock del documento con el renderizado,
  * emite las coincidencias por Signals **conforme las encuentra** (resaltado
    progresivo), no solo al terminar,
  * atiende un ``threading.Event`` de cancelacion inmediata, de modo que al
    seguir escribiendo el usuario la busqueda anterior se detiene al instante.
"""

from __future__ import annotations

import threading
from functools import partial
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from core.pdf_backend import pymupdf

Rect = Tuple[float, float, float, float]

#: Cada cuantas paginas se emiten los resultados acumulados.
_BATCH_PAGES = 4


class _SearchSignals(QObject):
    batch = Signal(object)          # {pagina: [rect, ...]}
    finished = Signal(int, bool)    # (paginas escaneadas, completada)


class SearchTask(QRunnable):
    """Escaneo completo del documento en segundo plano."""

    def __init__(self, path: str, term: str, page_count: int, start_page: int = 0) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.signals = _SearchSignals()
        self._path = path
        self._term = term
        self._page_count = page_count
        self._start_page = start_page
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # pragma: no cover - se ejecuta en el pool
        document = None
        scanned = 0
        try:
            document = pymupdf.open(self._path)
            batch: Dict[int, List[Rect]] = {}
            # Se empieza por la pagina actual y se da la vuelta al documento,
            # asi el usuario ve resultados cerca de donde esta leyendo.
            order = [
                (self._start_page + offset) % self._page_count
                for offset in range(self._page_count)
            ]
            for position, index in enumerate(order, start=1):
                if self._cancel.is_set():
                    break
                page = document.load_page(index)
                try:
                    hits = page.search_for(self._term)
                finally:
                    del page
                scanned = position
                if hits:
                    batch[index] = [(r.x0, r.y0, r.x1, r.y1) for r in hits]
                if batch and (position % _BATCH_PAGES == 0):
                    self.signals.batch.emit(batch)
                    batch = {}
            if batch and not self._cancel.is_set():
                self.signals.batch.emit(batch)
        except Exception:  # noqa: BLE001 - un fallo de busqueda no rompe la UI
            pass
        finally:
            if document is not None:
                document.close()
            self.signals.finished.emit(scanned, not self._cancel.is_set())


class SearchController(QObject):
    """Gestiona la tarea de busqueda activa y la lista ordenada de resultados."""

    matches_updated = Signal(object)   # {pagina: [rect, ...]} acumulado
    progress = Signal(int, int)        # (coincidencias, paginas escaneadas)
    finished = Signal(int)             # total de coincidencias

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._task: Optional[SearchTask] = None
        self.term = ""
        self.matches: Dict[int, List[Rect]] = {}
        self.ordered: List[Tuple[int, int]] = []  # (pagina, indice dentro de la pagina)

    # ------------------------------------------------------------- ciclo vida

    def start(self, path: str, term: str, page_count: int, start_page: int = 0) -> None:
        self.cancel()
        self.term = term
        self.matches = {}
        self.ordered = []
        if not term.strip():
            self.matches_updated.emit(self.matches)
            self.finished.emit(0)
            return
        task = SearchTask(path, term, page_count, start_page)
        # Las senales llevan la tarea emisora: los resultados de una busqueda
        # ya cancelada se descartan al llegar.
        task.signals.batch.connect(partial(self._on_batch, task), Qt.QueuedConnection)
        task.signals.finished.connect(partial(self._on_finished, task), Qt.QueuedConnection)
        self._task = task
        self._pool.start(task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def shutdown(self) -> None:
        self.cancel()
        self._pool.waitForDone(2000)

    # --------------------------------------------------------------- ranuras

    def _on_batch(self, task: SearchTask, batch: Dict[int, List[Rect]]) -> None:
        if task is not self._task:
            return
        for index, rects in batch.items():
            self.matches.setdefault(index, []).extend(rects)
        self._rebuild_order()
        self.matches_updated.emit(self.matches)
        self.progress.emit(len(self.ordered), len(self.matches))

    def _on_finished(self, task: SearchTask, _scanned: int, completed: bool) -> None:
        if task is not self._task:
            return
        if completed:
            self._task = None
        self.finished.emit(len(self.ordered))

    def _rebuild_order(self) -> None:
        self.ordered = [
            (page, position)
            for page in sorted(self.matches)
            for position in range(len(self.matches[page]))
        ]

    # ------------------------------------------------------------ navegacion

    def count(self) -> int:
        return len(self.ordered)

    def index_of(self, page: int, position: int) -> int:
        try:
            return self.ordered.index((page, position))
        except ValueError:
            return -1

    def next_after(self, page: int, position: int) -> Optional[Tuple[int, int]]:
        """Primera coincidencia estrictamente posterior (con vuelta al inicio)."""
        if not self.ordered:
            return None
        for item in self.ordered:
            if item > (page, position):
                return item
        return self.ordered[0]

    def previous_before(self, page: int, position: int) -> Optional[Tuple[int, int]]:
        if not self.ordered:
            return None
        for item in reversed(self.ordered):
            if item < (page, position):
                return item
        return self.ordered[-1]

    def first_from(self, page: int) -> Optional[Tuple[int, int]]:
        """Primera coincidencia a partir de la pagina indicada."""
        if not self.ordered:
            return None
        for item in self.ordered:
            if item[0] >= page:
                return item
        return self.ordered[0]
