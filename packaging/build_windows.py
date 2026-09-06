"""Compila el lector a un .exe de Windows con Nuitka (modo directorio standalone).

Se usa **standalone** y NO ``--onefile``: onefile descomprime la aplicacion en
``%TEMP%`` en cada arranque y anade 1-4 s de latencia, lo que romperia el
requisito de abrir un PDF en menos de un segundo.

Uso (en Windows, con el entorno de Python del proyecto activado):

    pip install -r requirements.txt nuitka
    python packaging/build_windows.py

Resultado: ``build/main.dist/LectorPDF.exe`` junto a sus DLL. Esa carpeta es la
que empaqueta ``packaging/installer.iss`` con Inno Setup.

Nota sobre ``--noinclude-qt-plugins`` (comprobado contra Nuitka 4.2.1 y
PySide6 6.11, no supuesto): sus valores son **familias de plugins de Qt**, es
decir los subdirectorios de ``PySide6/plugins``, no modulos como QtWebEngine.
Nuitka parte de un conjunto "sensible" (imageformats, iconengines, platforms,
platformthemes, styles, printsupport, tls, mediaservice y los especificos de
Linux) y esta opcion solo descarta elementos de ese conjunto: los nombres que
no existen se ignoran sin error (la validacion estricta la hace
``--include-qt-plugins``, no esta). Los modulos Qt no usados se excluyen
aparte con ``--nofollow-import-to``.

Si una version futura cambia estos nombres, comprueba la lista con
``nuitka --help-plugins`` y con el contenido de ``PySide6/plugins``, o compila
sin la optimizacion: ``python packaging/build_windows.py --no-exclude-plugins``.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.0.0"

#: Familias de plugins Qt que la aplicacion no usa. Las dos primeras son las
#: que realmente pesan aqui, porque son las unicas de la lista "sensible" de
#: Nuitka que no necesitamos (no se imprime ni se usa la red); el resto no
#: entra por defecto y se deja como salvaguarda por si eso cambia.
EXCLUDED_QT_PLUGINS = [
    "printsupport", "tls",
    "qmltooling", "qmllint", "multimedia", "sensors", "sqldrivers",
    "designer", "webview", "geoservices", "position", "texttospeech",
    "canbus", "scxmldatamodel", "assetimporters", "geometryloaders",
    "renderers", "renderplugins", "sceneparsers",
]

#: Modulos de PySide6 que no se usan y que no deben arrastrarse. Comprobado
#: que QtCore/QtGui/QtWidgets/QtSvg (lo unico que importa la app) no dependen
#: de ninguno de ellos. Si un binario compilado fallara al arrancar por un
#: import inesperado, basta con quitar la entrada correspondiente.
EXCLUDED_MODULES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtNetwork",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtCharts", "PySide6.Qt3DCore", "PySide6.QtDesigner",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "tkinter", "unittest", "pydoc_data",
]


def build(exclude_plugins: bool = True) -> int:
    command = [
        sys.executable, "-m", "nuitka",
        "--standalone",                       # carpeta con exe + dll (NO --onefile)
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        f"--output-dir={os.path.join(ROOT, 'build')}",
        "--output-filename=LectorPDF.exe",
        "--include-data-dir=" + os.path.join(ROOT, "assets") + "=assets",
        "--windows-console-mode=disable",     # sin ventana de consola
        "--windows-icon-from-ico=" + os.path.join(ROOT, "assets", "app.ico"),
        "--company-name=LectorPDF",
        "--product-name=Lector PDF",
        f"--file-version={VERSION}",
        f"--product-version={VERSION}",
        "--file-description=Lector de PDF ligero",
        "--remove-output",
    ]
    if exclude_plugins:
        command.append("--noinclude-qt-plugins=" + ",".join(EXCLUDED_QT_PLUGINS))
    for module in EXCLUDED_MODULES:
        command.append(f"--nofollow-import-to={module}")
    command.append(os.path.join(ROOT, "main.py"))

    print(" ".join(command), "\n")
    return subprocess.call(command, cwd=ROOT)


def main() -> int:
    if not sys.platform.startswith("win"):
        print("Aviso: este script esta pensado para Windows; en otros sistemas "
              "Nuitka generara un binario para el sistema actual.\n")
    icon = os.path.join(ROOT, "assets", "app.ico")
    if not os.path.exists(icon):
        print("Generando el icono...")
        subprocess.call([sys.executable, os.path.join(ROOT, "packaging", "make_icon.py")])
    return build(exclude_plugins="--no-exclude-plugins" not in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
