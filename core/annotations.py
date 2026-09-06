"""Anotaciones basicas guardadas dentro del propio PDF.

Se usan anotaciones estandar del formato (Highlight, Underline, StrikeOut,
Text), de modo que cualquier otro lector las muestra igual. El guardado es
**incremental** siempre que el documento lo permita: anade los cambios al final
del fichero en milisegundos, sin reescribir un expediente de cientos de MB.
Cuando no es posible, se hace un guardado completo, pero siempre en un hilo
secundario para no bloquear la interfaz.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal

from core.document import PdfDocument
from core.pdf_backend import pymupdf

Rect = Tuple[float, float, float, float]

HIGHLIGHT = "highlight"
UNDERLINE = "underline"
STRIKEOUT = "strikeout"

#: Colores de resaltado ofrecidos en la interfaz (RGB 0..1).
HIGHLIGHT_COLORS: List[Tuple[str, Tuple[float, float, float]]] = [
    ("Amarillo", (1.0, 0.92, 0.23)),
    ("Verde", (0.55, 0.90, 0.45)),
    ("Azul", (0.50, 0.78, 1.0)),
    ("Rosa", (1.0, 0.60, 0.78)),
    ("Naranja", (1.0, 0.72, 0.30)),
]

#: Color de linea para subrayado y tachado.
LINE_COLORS: List[Tuple[str, Tuple[float, float, float]]] = [
    ("Rojo", (0.85, 0.15, 0.15)),
    ("Azul", (0.15, 0.35, 0.85)),
    ("Verde", (0.10, 0.55, 0.20)),
    ("Negro", (0.0, 0.0, 0.0)),
]

NOTE_COLOR = (1.0, 0.85, 0.30)


class AnnotationError(Exception):
    """No se ha podido crear, modificar o guardar una anotacion."""


def is_writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def add_text_markup(
    document: PdfDocument,
    page_index: int,
    rects: Sequence[Rect],
    kind: str = HIGHLIGHT,
    color: Tuple[float, float, float] = HIGHLIGHT_COLORS[0][1],
    author: str = "",
) -> bool:
    """Anade resaltado / subrayado / tachado sobre los rectangulos indicados."""
    if not rects:
        return False
    quads = [pymupdf.Rect(*rect) for rect in rects]
    with document.lock:
        page = document.raw.load_page(page_index)
        try:
            if kind == UNDERLINE:
                annot = page.add_underline_annot(quads)
            elif kind == STRIKEOUT:
                annot = page.add_strikeout_annot(quads)
            else:
                annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=color)
            if author:
                annot.set_info(title=author)
            annot.update()
        except Exception as exc:  # noqa: BLE001
            raise AnnotationError(str(exc)) from exc
        finally:
            del page
    return True


def add_note(
    document: PdfDocument,
    page_index: int,
    point: Tuple[float, float],
    text: str,
    author: str = "",
) -> bool:
    """Anade una nota adhesiva (anotacion Text) en un punto de la pagina."""
    if not text.strip():
        return False
    with document.lock:
        page = document.raw.load_page(page_index)
        try:
            annot = page.add_text_annot(pymupdf.Point(*point), text, icon="Note")
            annot.set_colors(stroke=NOTE_COLOR)
            annot.set_info(title=author or "Nota", content=text)
            annot.update()
        except Exception as exc:  # noqa: BLE001
            raise AnnotationError(str(exc)) from exc
        finally:
            del page
    return True


def annotation_at(document: PdfDocument, page_index: int, x: float, y: float):
    """Anotacion bajo un punto: ``(xref, tipo, contenido)`` o ``None``."""
    point = pymupdf.Point(x, y)
    with document.lock:
        page = document.raw.load_page(page_index)
        try:
            found = None
            for annot in page.annots():
                # La ultima que contiene el punto es la de encima.
                if annot.rect.contains(point):
                    found = (annot.xref, annot.type[1], annot.info.get("content", ""))
            return found
        finally:
            del page


def update_note(document: PdfDocument, page_index: int, xref: int, text: str) -> bool:
    with document.lock:
        page = document.raw.load_page(page_index)
        try:
            for annot in page.annots():
                if annot.xref == xref:
                    info = annot.info
                    info["content"] = text
                    annot.set_info(info)
                    annot.update()
                    return True
            return False
        finally:
            del page


def delete_annotation(document: PdfDocument, page_index: int, xref: int) -> bool:
    with document.lock:
        page = document.raw.load_page(page_index)
        try:
            for annot in page.annots():
                if annot.xref == xref:
                    page.delete_annot(annot)
                    return True
            return False
        finally:
            del page


class SaveSignals(QObject):
    """Senales del guardado.

    Las crea y conserva la ventana, no la tarea: un ``QRunnable`` con
    ``autoDelete`` se destruye al terminar y arrastraria consigo una emision
    en cola aun sin procesar por el hilo GUI.
    """

    done = Signal(bool, str)   # (incremental, ruta temporal si fue completo)
    failed = Signal(str)


class SaveTask(QRunnable):
    """Guarda las anotaciones en el PDF desde un hilo secundario."""

    def __init__(self, document: PdfDocument, signals: SaveSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._signals = signals
        self._document = document

    def run(self) -> None:  # pragma: no cover - se ejecuta en el pool
        document = self._document
        try:
            with document.lock:
                if document.closed:
                    return
                if document.raw.can_save_incrementally():
                    # Rapido: solo se anaden los objetos nuevos al final.
                    document.raw.save(
                        document.path,
                        incremental=True,
                        encryption=pymupdf.PDF_ENCRYPT_KEEP,
                    )
                    self._signals.done.emit(True, "")
                    return
                # El documento no admite guardado incremental (p. ej. venia
                # danado y MuPDF lo reparo al abrirlo): guardado completo a un
                # temporal; el reemplazo lo hace la UI tras cerrar el handle.
                temp_path = document.path + ".lectorpdf.tmp"
                document.raw.save(temp_path, garbage=3, deflate=True)
            self._signals.done.emit(False, temp_path)
        except Exception as exc:  # noqa: BLE001 - se informa en la UI
            self._signals.failed.emit(str(exc))


def replace_with_temp(path: str, temp_path: str) -> None:
    """Sustituye el original por el temporal del guardado completo."""
    backup = path + ".lectorpdf.bak"
    try:
        if os.path.exists(backup):
            os.remove(backup)
        os.replace(path, backup)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(backup):
            os.remove(backup)
