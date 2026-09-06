"""Wrapper minimo sobre ``pymupdf.Document``.

Responsabilidades:
  * abrir / cerrar el documento y exponer metadatos basicos,
  * cachear los tamanos de pagina (necesarios para maquetar la escena),
  * serializar el acceso al documento entre el hilo GUI y los hilos de
    renderizado mediante un ``RLock`` (los objetos de MuPDF no son
    thread-safe).
"""

from __future__ import annotations

import os
import threading
from typing import List, Tuple

from core.pdf_backend import pymupdf


class DocumentError(Exception):
    """Error al abrir o manipular un PDF."""


class PdfDocument:
    """Documento PDF abierto, con acceso protegido por lock."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self.lock = threading.RLock()
        self._closed = False
        try:
            self._doc = pymupdf.open(self.path)
        except Exception as exc:  # noqa: BLE001 - se reenvia como error propio
            raise DocumentError(f"No se ha podido abrir el archivo:\n{exc}") from exc

        # Los PDF cifrados quedan fuera del alcance (seccion 5 de la spec).
        if self._doc.needs_pass:
            self._doc.close()
            raise DocumentError("El documento esta protegido con contrasena.")

        # Tamanos de pagina (en puntos PDF, ya con el /Rotate del documento
        # aplicado). Medido: ~30 ms para 500 paginas.
        self._sizes: List[Tuple[float, float]] = [
            (self._doc[i].rect.width, self._doc[i].rect.height)
            for i in range(self._doc.page_count)
        ]

    # ---------------------------------------------------------------- estado

    @property
    def raw(self):
        """Documento ``pymupdf`` subyacente. Usar SIEMPRE dentro de ``lock``."""
        return self._doc

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def page_count(self) -> int:
        return len(self._sizes)

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def title(self) -> str:
        with self.lock:
            meta = self._doc.metadata or {}
        return (meta.get("title") or "").strip() or self.filename

    def page_size(self, index: int) -> Tuple[float, float]:
        """Ancho y alto de la pagina en puntos PDF."""
        return self._sizes[index]

    # ------------------------------------------------------------- contenido

    def page_text(self, index: int) -> str:
        with self.lock:
            return self._doc.load_page(index).get_text()

    def page_words(self, index: int):
        """Palabras de la pagina: ``(x0, y0, x1, y1, texto, bloque, linea, n)``."""
        with self.lock:
            return self._doc.load_page(index).get_text("words")

    def toc(self):
        with self.lock:
            return self._doc.get_toc()

    # ---------------------------------------------------------------- cierre

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self.lock:
            try:
                self._doc.close()
            except Exception:  # noqa: BLE001 - cierre best-effort
                pass

    def __repr__(self) -> str:  # pragma: no cover - depuracion
        return f"<PdfDocument {self.filename!r} paginas={self.page_count}>"
