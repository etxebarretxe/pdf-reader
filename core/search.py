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
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from core.pdf_backend import pymupdf

Rect = Tuple[float, float, float, float]

#: Cada cuantas paginas se emiten los resultados acumulados.
_BATCH_PAGES = 4


class _SearchSignals(QObject):
    """Senales del controlador (no de cada tarea: los QRunnable mueren al
    terminar y una emision en cola podria perderse con ellos). El primer
    argumento es el token de la busqueda, para descartar resultados de una
    busqueda ya cancelada."""

    batch = Signal(int, object)          # (token, {pagina: [rect, ...]})
    finished = Signal(int, int, bool)    # (token, paginas escaneadas, completada)


class SearchTask(QRunnable):
    """Escaneo completo del documento en segundo plano."""

    def __init__(self, signals: "_SearchSignals", token: int, path: str, term: str,
                 page_count: int, start_page: int = 0) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._signals = signals
        self._token = token
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
                    self._signals.batch.emit(self._token, batch)
                    batch = {}
            if batch and not self._cancel.is_set():
                self._signals.batch.emit(self._token, batch)
        except Exception:  # noqa: BLE001 - un fallo de busqueda no rompe la UI
            pass
        finally:
            if document is not None:
                document.close()
            self._signals.finished.emit(self._token, scanned, not self._cancel.is_set())


class SearchController(QObject):
    """Gestiona la tarea de busqueda activa y la lista ordenada de resultados."""

    matches_updated = Signal(object)   # {pagina: [rect, ...]} acumulado
    progress = Signal(int, int)        # (coincidencias, paginas escaneadas)
    finished = Signal(int)             # total de coincidencias

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _SearchSignals(self)
        self._signals.batch.connect(self._on_batch, Qt.QueuedConnection)
        self._signals.finished.connect(self._on_finished, Qt.QueuedConnection)
        self._token = 0
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
        self._token += 1
        task = SearchTask(self._signals, self._token, path, term, page_count, start_page)
        self._task = task
        self._pool.start(task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._token += 1  # invalida los resultados que lleguen con retraso

    def shutdown(self) -> None:
        self.cancel()
        self._pool.waitForDone(2000)

    # --------------------------------------------------------------- ranuras

    def _on_batch(self, token: int, batch: Dict[int, List[Rect]]) -> None:
        if token != self._token or self._task is None:
            return
        for index, rects in batch.items():
            self.matches.setdefault(index, []).extend(rects)
        self._rebuild_order()
        self.matches_updated.emit(self.matches)
        self.progress.emit(len(self.ordered), len(self.matches))

    def _on_finished(self, token: int, _scanned: int, completed: bool) -> None:
        if token != self._token:
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
