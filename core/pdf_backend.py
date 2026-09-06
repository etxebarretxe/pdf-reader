"""Importacion unificada del backend PyMuPDF.

PyMuPDF >= 1.24.3 expone el modulo como ``pymupdf``; en versiones anteriores
solo existia el alias historico ``fitz`` (hoy deprecado). Todo el codigo del
proyecto importa desde aqui para no depender de la version instalada.
"""

from __future__ import annotations

try:  # PyMuPDF moderno
    import pymupdf
except ImportError:  # PyMuPDF < 1.24.3
    import fitz as pymupdf  # type: ignore[no-redef]

__all__ = ["pymupdf"]
