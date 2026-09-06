"""Renderizado de paginas en hilos secundarios.

Reglas criticas (seccion 10.3 de la especificacion):
  * los hilos de trabajo generan UNICAMENTE ``QImage`` (nunca ``QPixmap``,
    que no es thread-safe); la conversion a ``QPixmap`` ocurre en el hilo GUI,
  * antes de renderizar, la tarea comprueba si la pagina sigue siendo
    necesaria (viewport actual + generacion) y aborta si ya no lo es,
  * la matriz de PyMuPDF se multiplica por el ``devicePixelRatio`` de la
    pantalla para no ver texto borroso con escalado de Windows,
  * la cache LRU libera explicitamente los ``QPixmap`` y los ``fitz.Pixmap``
    de las paginas que salen del buffer visible.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, Optional, Set, Tuple

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from core.document import PdfDocument
from core.pdf_backend import pymupdf

# Clave de cache: (pagina, zoom en milesimas, rotacion, dpr en centesimas)
CacheKey = Tuple[int, int, int, int]

#: Presupuesto de memoria de la cache de paginas renderizadas (bytes).
DEFAULT_CACHE_BUDGET = 64 * 1024 * 1024
#: Numero minimo de paginas que se mantienen aunque se supere el presupuesto.
MIN_CACHED_PAGES = 3


def make_key(index: int, zoom: float, rotation: int, dpr: float) -> CacheKey:
    return (index, int(round(zoom * 1000)), int(rotation) % 360, int(round(dpr * 100)))


def render_page_image(
    document: PdfDocument,
    index: int,
    zoom: float,
    rotation: int = 0,
    dpr: float = 1.0,
) -> Optional[QImage]:
    """Renderiza una pagina a ``QImage``. Seguro para hilos secundarios."""
    scale = zoom * dpr
    matrix = pymupdf.Matrix(scale, scale).prerotate(rotation % 360)
    with document.lock:
        if document.closed:
            return None
        page = document.raw.load_page(index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        try:
            # copy() es obligatorio: el buffer 'samples' pertenece a MuPDF y
            # se libera al destruir el Pixmap.
            image = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888
            ).copy()
        finally:
            # Liberacion explicita de la memoria C++ (no depender del GC).
            del pix
            del page
    image.setDevicePixelRatio(dpr)
    return image


class _RenderSignals(QObject):
    """Senales del renderizado.

    Vive en el motor, no en cada tarea: un ``QRunnable`` con ``autoDelete`` se
    destruye en cuanto termina ``run()``, y si las senales colgaran de el, una
    emision en cola podria perderse al recolectarse el objeto antes de que el
    hilo GUI la procese.
    """

    done = Signal(object, QImage)  # (CacheKey, imagen)
    failed = Signal(object, str)
    aborted = Signal(object)


class RenderTask(QRunnable):
    """Tarea de renderizado de una pagina."""

    def __init__(self, engine: "RenderEngine", key: CacheKey, generation: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._engine = engine
        self._key = key
        self._generation = generation

    def run(self) -> None:  # pragma: no cover - se ejecuta en el pool
        signals = self._engine.signals
        index, zoom_m, rotation, dpr_c = self._key
        # Abortar si el usuario ya ha hecho scroll/zoom y esta pagina ya no
        # se necesita: evita saturar el QThreadPool con trabajo obsoleto.
        if not self._engine.is_wanted(index, self._generation):
            signals.aborted.emit(self._key)
            return
        try:
            image = render_page_image(
                self._engine.document,
                index,
                zoom_m / 1000.0,
                rotation,
                dpr_c / 100.0,
            )
        except Exception as exc:  # noqa: BLE001 - se informa a la UI
            signals.failed.emit(self._key, str(exc))
            return
        if image is None or not self._engine.is_wanted(index, self._generation):
            signals.aborted.emit(self._key)
            return
        signals.done.emit(self._key, image)


class PageCache:
    """Cache LRU de ``QPixmap`` con presupuesto de memoria."""

    def __init__(self, budget: int = DEFAULT_CACHE_BUDGET) -> None:
        self._items: "OrderedDict[CacheKey, QPixmap]" = OrderedDict()
        self._budget = budget
        self._bytes = 0

    @staticmethod
    def _size_of(pixmap: QPixmap) -> int:
        return pixmap.width() * pixmap.height() * (pixmap.depth() // 8 or 3)

    def get(self, key: CacheKey) -> Optional[QPixmap]:
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
        return pixmap

    def put(self, key: CacheKey, pixmap: QPixmap) -> None:
        self.discard(key)
        self._items[key] = pixmap
        self._bytes += self._size_of(pixmap)
        self._enforce_budget()

    def discard(self, key: CacheKey) -> None:
        pixmap = self._items.pop(key, None)
        if pixmap is not None:
            self._bytes -= self._size_of(pixmap)
            del pixmap  # libera la copia local; Qt cuenta referencias

    def find_similar(self, index: int, rotation: int) -> Optional[QPixmap]:
        """Mejor pixmap disponible de la pagina a otro zoom (vista previa)."""
        best = None
        best_area = -1
        for (idx, _zoom, rot, _dpr), pixmap in self._items.items():
            if idx != index or rot != rotation % 360:
                continue
            area = pixmap.width() * pixmap.height()
            if area > best_area:
                best, best_area = pixmap, area
        return best

    def keep_only(self, keys: Set[CacheKey]) -> None:
        """Descarta todo lo que no este en ``keys`` (buffer visible +/- 2)."""
        for key in [k for k in self._items if k not in keys]:
            self.discard(key)

    def drop_page(self, index: int) -> None:
        for key in [k for k in self._items if k[0] == index]:
            self.discard(key)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def _enforce_budget(self) -> None:
        while self._bytes > self._budget and len(self._items) > MIN_CACHED_PAGES:
            key, pixmap = self._items.popitem(last=False)
            self._bytes -= self._size_of(pixmap)
            del pixmap

    def __len__(self) -> int:
        return len(self._items)

    @property
    def bytes_used(self) -> int:
        return self._bytes


class RenderEngine(QObject):
    """Coordina las tareas de renderizado y la cache de paginas."""

    page_ready = Signal(int)          # indice de pagina listo para repintar
    render_failed = Signal(int, str)

    def __init__(self, document: PdfDocument, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.document = document
        self.cache = PageCache()
        self.signals = _RenderSignals(self)
        self.signals.done.connect(self._on_done, Qt.QueuedConnection)
        self.signals.failed.connect(self._on_failed, Qt.QueuedConnection)
        self.signals.aborted.connect(self._on_aborted, Qt.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, QThreadPool.globalInstance().maxThreadCount())))
        self._pending: Dict[CacheKey, int] = {}
        self._wanted: Set[int] = set()
        self._generation = 0

    # ------------------------------------------------------------- consultas

    def is_wanted(self, index: int, generation: int) -> bool:
        return generation == self._generation and index in self._wanted

    def pixmap(self, index: int, zoom: float, rotation: int, dpr: float) -> Optional[QPixmap]:
        return self.cache.get(make_key(index, zoom, rotation, dpr))

    def preview(self, index: int, rotation: int) -> Optional[QPixmap]:
        """Pixmap de otro zoom para dibujar mientras llega el definitivo."""
        return self.cache.find_similar(index, rotation)

    def drop_page(self, index: int) -> None:
        """Invalida todo lo cacheado de una pagina (p. ej. tras anotarla)."""
        self.cache.drop_page(index)

    # -------------------------------------------------------------- peticion

    def invalidate(self) -> None:
        """Invalida el trabajo en curso (cambio de zoom, rotacion o dpr)."""
        self._generation += 1
        self._pending.clear()

    def set_wanted(self, indices: Iterable[int]) -> None:
        self._wanted = set(indices)

    def request(self, index: int, zoom: float, rotation: int, dpr: float) -> Optional[QPixmap]:
        """Devuelve la pagina de cache o encola su renderizado."""
        key = make_key(index, zoom, rotation, dpr)
        pixmap = self.cache.get(key)
        if pixmap is not None:
            return pixmap
        if key in self._pending:
            return None
        self._pending[key] = self._generation
        self._pool.start(RenderTask(self, key, self._generation))
        return None

    def prune(self, keys: Set[CacheKey]) -> None:
        self.cache.keep_only(keys)

    # --------------------------------------------------------------- ranuras

    def _on_done(self, key: CacheKey, image: QImage) -> None:
        """Ejecutado en el hilo GUI: aqui (y solo aqui) se crea el QPixmap."""
        self._pending.pop(key, None)
        index = key[0]
        if index not in self._wanted:
            return
        pixmap = QPixmap.fromImage(image)
        self.cache.put(key, pixmap)
        self.page_ready.emit(index)

    def _on_failed(self, key: CacheKey, message: str) -> None:
        self._pending.pop(key, None)
        self.render_failed.emit(key[0], message)

    def _on_aborted(self, key: CacheKey) -> None:
        self._pending.pop(key, None)

    # ---------------------------------------------------------------- cierre

    def shutdown(self) -> None:
        self.invalidate()
        self._wanted.clear()
        self._pool.clear()
        self._pool.waitForDone(3000)
        self.cache.clear()
