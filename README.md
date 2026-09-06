# Lector PDF ligero

Lector de PDF minimalista para Windows, pensado para trabajar a diario con
escrituras, sentencias y expedientes escaneados: abre en milisegundos, consume
poca RAM y solo incluye lo que se usa (ver, navegar, buscar, anotar).

Sin nube, sin cuentas, sin telemetria, sin comprobacion de actualizaciones,
sin publicidad y sin funciones "premium". La aplicacion no hace ninguna
conexion a internet.

## Funciones

**Visualizacion y navegacion**
- Apertura por dialogo, por arrastrar y soltar o por linea de comandos
  (`LectorPDF.exe documento.pdf`, que es lo que usa la asociacion de `.pdf`).
- Scroll continuo vertical (por defecto) y modo de una pagina.
- Zoom: ajustar al ancho, ajustar a la pagina, niveles predefinidos y zoom
  libre con `Ctrl` + rueda del raton.
- Ir a una pagina concreta, primera/ultima, anterior/siguiente.
- Panel lateral de miniaturas.
- Rotacion de la vista (90/180/270) sin tocar el archivo original.

**Busqueda**
- Busqueda en todo el documento, en segundo plano (la interfaz nunca se
  bloquea) y empezando por la pagina que se esta leyendo.
- Resaltado progresivo de todas las coincidencias, con la actual destacada.
- Contador "3 de 27" y navegacion con `F3` / `Mayus+F3`.

**Anotaciones**
- Resaltado (5 colores), subrayado, tachado y notas adhesivas.
- Menu contextual para editar una nota o eliminar la anotacion bajo el cursor.
- Se guardan **dentro del PDF** en formato estandar, de modo que cualquier
  otro lector (Adobe incluido) las muestra igual.

**Gestion**
- Lista de documentos recientes.
- Cada documento se reabre donde se dejo: pagina, zoom, rotacion y modo.
- Multiventana: cada PDF en su propia ventana, dentro del mismo proceso.
- Tema claro y oscuro.

## Uso desde el codigo fuente

```bash
pip install -r requirements.txt
python main.py                 # ventana vacia
python main.py documento.pdf   # abre un documento
```

Requiere Python 3.11 o superior.

## Atajos de teclado

| Atajo | Accion |
|---|---|
| `Ctrl+O` | Abrir |
| `Ctrl+Shift+O` | Abrir en ventana nueva |
| `Ctrl+S` | Guardar anotaciones en el PDF |
| `Ctrl++` / `Ctrl+-` | Acercar / alejar |
| `Ctrl+0` | Zoom 100% |
| `Ctrl+1` / `Ctrl+2` | Ajustar al ancho / a la pagina |
| `Ctrl+rueda` | Zoom con el raton |
| `Ctrl+G` | Ir a pagina |
| `PgUp` / `PgDn` / `Espacio` | Navegar |
| `Inicio` / `Fin` | Primera / ultima pagina |
| `Ctrl+Shift+Izq/Der` | Rotar la vista |
| `Ctrl+F` | Buscar |
| `F3` / `Mayus+F3` | Coincidencia siguiente / anterior |
| `Esc` | Cerrar la busqueda |
| `H` / `U` / `T` | Resaltar / subrayar / tachar la seleccion |
| `N` | Nota adhesiva |
| `Ctrl+C` | Copiar el texto seleccionado |
| `F4` | Panel de miniaturas |
| `Ctrl+D` | Tema claro / oscuro |
| `F1` | Ver todos los atajos |
| Boton central | Desplazar arrastrando |

## Arquitectura

```
main.py                   punto de entrada
core/
  pdf_backend.py          import compatible de PyMuPDF (pymupdf / fitz)
  document.py             wrapper de fitz.Document, tamanos de pagina, lock
  render_engine.py        renderizado en hilos + cache LRU de paginas
  search.py               busqueda global asincrona y cancelable
  annotations.py          resaltados, notas y guardado incremental en el PDF
  recent_files.py         configuracion JSON, recientes y estado por documento
ui/
  main_window.py          ventana, menus, atajos, multiventana
  viewer_widget.py        QGraphicsView + QGraphicsScene (scroll, zoom, giro)
  thumbnail_panel.py      miniaturas bajo demanda
  search_bar.py           barra de busqueda flotante
  toolbar.py              barra de herramientas minimalista
  theme.py                temas claro/oscuro e iconos SVG recoloreados
assets/                   iconos SVG e icono de la aplicacion
config/                   configuracion opcional en modo portable
packaging/                script de compilacion Nuitka e instalador Inno Setup
```

### Decisiones de rendimiento

- Los hilos de renderizado producen **solo** `QImage`; el `QPixmap` se crea
  unicamente en el hilo GUI (`QPixmap` no es thread-safe).
- Antes de renderizar, la tarea comprueba si la pagina sigue en el viewport y
  aborta si ya no lo esta, para no saturar el `QThreadPool` tras un scroll
  rapido.
- Solo se renderizan las paginas visibles +/- 2; el resto se libera de la
  cache, que ademas tiene un presupuesto de memoria (64 MB) y destruye
  explicitamente los `fitz.Pixmap` y los `QPixmap` que expulsa.
- La `fitz.Matrix` se multiplica por el `devicePixelRatio()` de la pantalla,
  para que el texto sea nitido con el escalado de Windows al 125/150/200%.
- El zoom no es una transformacion de la vista: se recalcula la maquetacion y
  se vuelve a renderizar a esa escala, de modo que el texto nunca se ve
  interpolado. Mientras llega la pagina definitiva se dibuja la version
  cacheada a otro zoom, para que el zoom no parpadee.
- El acceso al documento esta serializado con un lock (MuPDF no es
  thread-safe); la busqueda abre su propio handle del fichero para no
  competir con el renderizado.
- Las anotaciones se guardan con
  `doc.save(ruta, incremental=True, encryption=PDF_ENCRYPT_KEEP)` cuando
  `can_save_incrementally()` lo permite: milisegundos y sin reescribir un
  expediente de cientos de MB. Si no es posible, guardado completo, tambien en
  un hilo secundario.

### Medidas (criterios de aceptacion de la especificacion)

Medido con Python interpretado, ventana de 1200x900 y ajuste al ancho:

| Criterio | Objetivo | Medido |
|---|---|---|
| PDF de 50 paginas: hasta ver la primera pagina | < 1 s | **0,32 s** |
| RAM con un documento de 100 paginas abierto | < 150 MB | **138 MB** |
| PDF de 500 paginas: `open_path` | — | 0,16 s |
| Busqueda en 500 paginas sin bloquear la UI | sin bloqueo | primeras coincidencias en < 0,15 s |

El arranque en frio del proceso (cargar Qt y PyMuPDF) anade ~1,5 s en modo
interpretado; compilado con Nuitka en modo directorio standalone se reduce
notablemente, y por eso no se usa `--onefile`, que volveria a anadir de 1 a 4 s
en cada arranque.

## Configuracion

Se guarda en un unico JSON, sin nube:

- `%APPDATA%\LectorPDF\settings.json` en Windows
  (`~/.config/LectorPDF/settings.json` en Linux/macOS).
- **Modo portable:** si existe `config/settings.json` junto al programa, se usa
  ese archivo. Copia `config/settings.example.json` para activarlo.

Guarda el tema, el tamano de la ventana, los documentos recientes y, por
documento, la ultima pagina, el zoom, la rotacion y el modo de vista. El campo
`author` se usa como autor de las anotaciones.

## Compilar el .exe para Windows

```bat
pip install -r requirements.txt nuitka
python packaging\build_windows.py
```

Genera `build\main.dist\LectorPDF.exe` junto a sus DLL. Se usa **modo
directorio standalone y no `--onefile`**: onefile descomprime la aplicacion en
`%TEMP%` en cada arranque y anade 1-4 s de latencia, lo que romperia el
objetivo de abrir un PDF en menos de un segundo.

Para el instalador, compilar `packaging\installer.iss` con Inno Setup 6:

```bat
ISCC.exe packaging\installer.iss
```

El instalador ofrece (desmarcada por defecto) la asociacion de la extension
`.pdf`. El tamano en disco ronda los 80-150 MB, que es lo normal con Qt +
PyMuPDF; lo que importa es la RAM en ejecucion, no el peso del instalador.

> Los valores de `--noinclude-qt-plugins` son **categorias de plugins de Qt**
> (los subdirectorios de `PySide6/plugins`), no modulos como QtWebEngine, y sus
> nombres han cambiado entre versiones. La lista del script esta comprobada
> contra PySide6 6.11; si una version futura rechaza algun nombre, Nuitka
> imprime la lista valida y puedes compilar mientras tanto con
> `python packaging\build_windows.py --no-exclude-plugins`.

## Funciones deliberadamente excluidas

Firma digital, formularios (AcroForms/XFA), nube, revision colaborativa,
exportacion a Office, OCR, edicion del contenido del PDF, cifrado y DRM,
telemetria, cuentas de usuario, publicidad y actualizaciones automaticas. No
hay stubs ni menus deshabilitados de ninguna de ellas.
