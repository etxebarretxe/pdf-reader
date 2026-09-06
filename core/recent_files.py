"""Configuracion local y estado por documento (sin nube, sin telemetria).

Todo se guarda en un unico JSON:

  * modo portable: ``config/settings.json`` junto al ejecutable, si ese fichero
    ya existe (util para llevar el lector en un USB),
  * modo normal: ``%APPDATA%\\LectorPDF\\settings.json`` en Windows
    (``~/.config/LectorPDF/settings.json`` en Linux/macOS), que es donde se
    puede escribir aunque la app este instalada en ``Archivos de programa``.

Guarda: tema, geometria de la ventana, lista de recientes y, por documento,
la ultima pagina vista, el zoom, la rotacion y el modo de vista.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

APP_DIR_NAME = "LectorPDF"
MAX_RECENT = 12
#: Documentos de los que se recuerda la posicion de lectura.
MAX_DOCUMENT_STATES = 300


def _app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def portable_settings_path() -> str:
    return os.path.join(_app_root(), "config", "settings.json")


def user_settings_path() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME, "settings.json")


def settings_path() -> str:
    portable = portable_settings_path()
    if os.path.isfile(portable):
        return portable
    return user_settings_path()


class Settings:
    """Configuracion persistente en JSON."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or settings_path()
        self.data: Dict[str, Any] = {
            "theme": "light",
            "window": {},
            "recent": [],
            "documents": {},
        }
        self.load()

    # ----------------------------------------------------------------- disco

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass  # primera ejecucion o fichero corrupto: se usan los valores por defecto

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
            # Escritura atomica: no se corrompe la configuracion si algo falla.
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.path)
        except OSError:
            pass  # no poder guardar la configuracion nunca debe romper la app

    # ---------------------------------------------------------------- basico

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    # -------------------------------------------------------------- recientes

    def recent_files(self) -> List[Dict[str, Any]]:
        entries = [e for e in self.data.get("recent", []) if isinstance(e, dict) and e.get("path")]
        return entries[:MAX_RECENT]

    def add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        entries = [e for e in self.recent_files() if e.get("path") != path]
        entries.insert(0, {
            "path": path,
            "name": os.path.basename(path),
            "opened_at": int(time.time()),
        })
        self.data["recent"] = entries[:MAX_RECENT]

    def remove_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        self.data["recent"] = [e for e in self.recent_files() if e.get("path") != path]

    def clear_recent(self) -> None:
        self.data["recent"] = []

    def prune_missing(self) -> None:
        """Quita de la lista los documentos que ya no existen en disco."""
        self.data["recent"] = [e for e in self.recent_files() if os.path.exists(e["path"])]

    # ---------------------------------------------------- estado por documento

    def document_state(self, path: str) -> Dict[str, Any]:
        return dict(self.data.get("documents", {}).get(os.path.abspath(path), {}))

    def remember_document(self, path: str, state: Dict[str, Any]) -> None:
        documents = self.data.setdefault("documents", {})
        entry = dict(state)
        entry["seen_at"] = int(time.time())
        documents[os.path.abspath(path)] = entry
        if len(documents) > MAX_DOCUMENT_STATES:
            ordered = sorted(documents.items(), key=lambda kv: kv[1].get("seen_at", 0), reverse=True)
            self.data["documents"] = dict(ordered[:MAX_DOCUMENT_STATES])
