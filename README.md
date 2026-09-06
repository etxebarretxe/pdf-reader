# Lector PDF ligero

Lector de PDF minimalista para Windows, pensado para trabajar a diario con
escrituras, sentencias y expedientes: abre rapido, consume poca RAM y solo
incluye lo que se usa (ver, navegar, buscar, anotar).

## Estado

- **Fase 1 — Visualizador (completa):** apertura de PDF, renderizado en hilos
  secundarios con cache de paginas visibles, scroll continuo y modo pagina,
  zoom (libre, ajustar a ancho, ajustar a pagina), rotacion de vista,
  navegacion, panel de miniaturas, tema claro/oscuro, arrastrar y soltar,
  multiventana.
- **Fase 2 — Busqueda (completa):** busqueda de texto en todo el documento en
  hilo secundario (la UI nunca se bloquea), resaltado progresivo de todas las
  coincidencias, navegacion entre resultados con contador "3 de 27" y
  cancelacion inmediata al reescribir el termino.
- **Fase 3 — Anotaciones (completa):** resaltado (5 colores), subrayado,
  tachado y notas adhesivas, con menu contextual para editar o eliminar. Las
  anotaciones se escriben **dentro del propio PDF** en formato estandar, con
  guardado incremental en hilo secundario (milisegundos, sin reescribir el
  fichero completo).

## Requisitos

- Python 3.11 o superior
- `pip install -r requirements.txt` (PySide6 + PyMuPDF)

## Ejecucion

```bash
python main.py                 # ventana vacia
python main.py documento.pdf   # abre un documento
```

## Atajos de teclado

| Atajo | Accion |
|---|---|
| `Ctrl+O` | Abrir |
| `Ctrl+Shift+O` | Abrir en ventana nueva |
| `Ctrl++` / `Ctrl+-` | Acercar / alejar |
| `Ctrl+0` | Zoom 100% |
| `Ctrl+1` / `Ctrl+2` | Ajustar al ancho / a la pagina |
| `Ctrl+G` | Ir a pagina |
| `Ctrl+Shift+Izq/Der` | Rotar la vista |
| `PgUp` / `PgDn` / `Espacio` | Navegar |
| `Ctrl+Inicio` / `Ctrl+Fin` | Primera / ultima pagina |
| `Ctrl+F` | Buscar |
| `F3` / `Mayus+F3` | Coincidencia siguiente / anterior |
| `Esc` | Cerrar la busqueda |
| `H` | Resaltar la seleccion |
| `U` / `T` | Subrayar / tachar la seleccion |
| `N` | Colocar una nota adhesiva |
| `Ctrl+C` | Copiar el texto seleccionado |
| `Ctrl+S` | Guardar anotaciones en el PDF |
| `F4` | Panel de miniaturas |
| `Ctrl+D` | Tema claro / oscuro |
| `Ctrl+rueda` | Zoom con el raton |
| Boton central | Desplazar arrastrando |

## Arquitectura

```
core/    motor: documento, renderizado en hilos, cache LRU
ui/      interfaz: ventana, visor QGraphicsView, miniaturas, barra de herramientas
assets/  iconos SVG monocromos (se recolorean segun el tema)
```

Reglas de rendimiento que sigue el codigo:

- Los hilos de renderizado producen **solo** `QImage`; el `QPixmap` se crea en
  el hilo GUI (`QPixmap` no es thread-safe).
- Se renderizan unicamente las paginas visibles +/- 2; el resto se libera.
- La matriz de PyMuPDF se multiplica por el `devicePixelRatio` de la pantalla
  para que el texto sea nitido con escalado de Windows al 125/150/200%.
- El acceso al documento esta serializado con un lock (MuPDF no es thread-safe).
- Las anotaciones se guardan con `doc.save(..., incremental=True,
  encryption=PDF_ENCRYPT_KEEP)` cuando `can_save_incrementally()` lo permite;
  si no, guardado completo, tambien en segundo plano.

## Anotaciones

Seleccion de texto arrastrando con el raton (doble clic selecciona una
palabra) y despues `H`, `U` o `T`. El boton derecho abre el menu contextual
para anotar, editar una nota o eliminar la anotacion que este bajo el cursor.
Los cambios se guardan solos 1,2 s despues de la ultima anotacion, con
`Ctrl+S`, y al cerrar la ventana. Si el archivo es de solo lectura la
aplicacion lo indica y desactiva las anotaciones.
