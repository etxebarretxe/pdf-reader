"""Visor de paginas basado en QGraphicsView + QGraphicsScene.

Cada pagina es un ``PageItem`` colocado en una columna vertical. El zoom NO se
aplica como transformacion de la vista (eso emborronaria el texto): se cambia
el tamano de los items y se vuelve a renderizar la pagina a esa escala, de modo
que el texto siempre esta nitido. La transformacion de la vista permanece en la
identidad, lo que ademas simplifica el calculo de offsets y anclajes de scroll.

Solo se renderizan las paginas visibles +/- ``PAGE_BUFFER``; el resto se libera
de la cache.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from core.document import PdfDocument
from core.render_engine import RenderEngine, make_key
from ui import theme

#: Paginas de margen que se mantienen renderizadas fuera del viewport.
PAGE_BUFFER = 2
#: Separacion vertical entre paginas y margen alrededor de la columna (px).
PAGE_GAP = 12
PAGE_MARGIN = 12

ZOOM_LEVELS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0]
MIN_ZOOM, MAX_ZOOM = 0.1, 6.0

# Modos de zoom
ZOOM_FREE = "free"
ZOOM_FIT_WIDTH = "fit-width"
ZOOM_FIT_PAGE = "fit-page"

# Modos de vista
VIEW_CONTINUOUS = "continuous"
VIEW_SINGLE = "single"


class PageItem(QGraphicsItem):
    """Una pagina del PDF dentro de la escena."""

    def __init__(self, viewer: "ViewerWidget", index: int, width_pt: float, height_pt: float):
        super().__init__()
        self.viewer = viewer
        self.index = index
        self.width_pt = width_pt
        self.height_pt = height_pt
        self._size = QRectF(0, 0, width_pt, height_pt)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    # ------------------------------------------------------------ geometria

    def display_size(self, zoom: float, rotation: int) -> Tuple[float, float]:
        if rotation % 180:
            return self.height_pt * zoom, self.width_pt * zoom
        return self.width_pt * zoom, self.height_pt * zoom

    def update_size(self, zoom: float, rotation: int) -> None:
        width, height = self.display_size(zoom, rotation)
        self.prepareGeometryChange()
        self._size = QRectF(0, 0, width, height)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API de Qt
        return self._size

    def map_point(self, x: float, y: float) -> QPointF:
        """Punto en coordenadas de pagina (puntos PDF) -> coordenadas del item."""
        zoom, rot = self.viewer.zoom, self.viewer.rotation % 360
        if rot == 90:
            return QPointF((self.height_pt - y) * zoom, x * zoom)
        if rot == 180:
            return QPointF((self.width_pt - x) * zoom, (self.height_pt - y) * zoom)
        if rot == 270:
            return QPointF(y * zoom, (self.width_pt - x) * zoom)
        return QPointF(x * zoom, y * zoom)

    def unmap_point(self, point: QPointF) -> Tuple[float, float]:
        """Coordenadas del item -> coordenadas de pagina (puntos PDF)."""
        zoom, rot = self.viewer.zoom, self.viewer.rotation % 360
        ix, iy = point.x() / zoom, point.y() / zoom
        if rot == 90:
            return iy, self.height_pt - ix
        if rot == 180:
            return self.width_pt - ix, self.height_pt - iy
        if rot == 270:
            return self.width_pt - iy, ix
        return ix, iy

    def map_rect(self, rect: Sequence[float]) -> QRectF:
        """Rect (x0, y0, x1, y1) en puntos PDF -> QRectF en coordenadas del item."""
        first = self.map_point(rect[0], rect[1])
        second = self.map_point(rect[2], rect[3])
        return QRectF(first, second).normalized()

    # ------------------------------------------------------------- pintado

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        rect = self._size
        palette = theme.PALETTE[self.viewer.theme_name]
        painter.fillRect(rect, QColor(palette["page"]))

        pixmap = self.viewer.engine.pixmap(
            self.index, self.viewer.zoom, self.viewer.rotation, self.viewer.dpr
        )
        if pixmap is not None:
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        else:
            preview = self.viewer.engine.preview(self.index, self.viewer.rotation)
            if preview is not None:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(rect, preview, QRectF(preview.rect()))

        self._paint_overlays(painter)

        painter.setPen(QPen(QColor(palette["page_border"]), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def _paint_overlays(self, painter: QPainter) -> None:
        viewer = self.viewer
        painter.save()
        painter.setPen(Qt.NoPen)

        matches = viewer.search_matches.get(self.index)
        if matches:
            painter.setBrush(QBrush(QColor(255, 214, 0, 90)))
            for rect in matches:
                painter.drawRect(self.map_rect(rect))
            current = viewer.current_match
            if current is not None and current[0] == self.index:
                painter.setBrush(QBrush(QColor(255, 132, 0, 130)))
                painter.drawRect(self.map_rect(matches[current[1]]))

        selection = viewer.selection
        if selection is not None and selection.page_index == self.index:
            painter.setBrush(QBrush(QColor(47, 111, 235, 70)))
            for rect in selection.rects:
                painter.drawRect(self.map_rect(rect))

        painter.restore()


class Selection:
    """Seleccion de texto activa dentro de una pagina."""

    __slots__ = ("page_index", "rects", "text")

    def __init__(self, page_index: int, rects: List[Tuple[float, float, float, float]], text: str):
        self.page_index = page_index
        self.rects = rects
        self.text = text

    def __bool__(self) -> bool:
        return bool(self.rects)


class ViewerWidget(QGraphicsView):
    """Visor de PDF con scroll continuo, zoom, rotacion y seleccion de texto."""

    page_changed = Signal(int)
    zoom_changed = Signal(float)
    selection_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.document: Optional[PdfDocument] = None
        self.engine: Optional[RenderEngine] = None
        self.items: List[PageItem] = []

        self.zoom = 1.0
        self.zoom_mode = ZOOM_FIT_WIDTH
        self.rotation = 0
        self.view_mode = VIEW_CONTINUOUS
        self.theme_name = theme.LIGHT
        self.dpr = 1.0

        self.search_matches: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self.current_match: Optional[Tuple[int, int]] = None
        self.selection: Optional[Selection] = None

        self._current_page = 0
        self._single_page = 0
        self._words_cache: Dict[int, list] = {}
        self._drag_origin: Optional[Tuple[int, float, float]] = None
        self._panning = False
        self._pan_anchor = QPointF()

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setBackgroundBrush(theme.color(self.theme_name, "scene"))

        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(30)
        self._visible_timer.timeout.connect(self._update_visible)

        self.verticalScrollBar().valueChanged.connect(self._schedule_update)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_update)

    # -------------------------------------------------------------- documento

    def set_document(self, document: PdfDocument) -> None:
        self.close_document()
        self.document = document
        self.engine = RenderEngine(document, self)
        self.engine.page_ready.connect(self._on_page_ready)
        self.dpr = self._screen_dpr()

        self._scene.clear()
        self.items = []
        for index in range(document.page_count):
            width_pt, height_pt = document.page_size(index)
            item = PageItem(self, index, width_pt, height_pt)
            self._scene.addItem(item)
            self.items.append(item)

        self._current_page = 0
        self._single_page = 0
        self.relayout()
        self.verticalScrollBar().setValue(0)
        self._update_visible()

    def close_document(self) -> None:
        if self.engine is not None:
            self.engine.shutdown()
            self.engine.deleteLater()
            self.engine = None
        self._scene.clear()
        self.items = []
        self._words_cache.clear()
        self.search_matches = {}
        self.current_match = None
        self.selection = None
        self.document = None

    def _screen_dpr(self) -> float:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen else 1.0

    # ----------------------------------------------------------- maquetacion

    def _visible_items(self) -> List[PageItem]:
        if not self.items:
            return []
        if self.view_mode == VIEW_SINGLE:
            return [self.items[self._single_page]]
        return self.items

    def relayout(self) -> None:
        """Recoloca las paginas para el zoom/rotacion/modo actuales."""
        if not self.items:
            return
        if self.zoom_mode in (ZOOM_FIT_WIDTH, ZOOM_FIT_PAGE):
            self.zoom = self._fit_zoom(self.zoom_mode)

        shown = self._visible_items()
        for item in self.items:
            item.setVisible(False)

        widths = []
        heights = []
        for item in shown:
            width, height = item.display_size(self.zoom, self.rotation)
            widths.append(width)
            heights.append(height)

        content_width = max(widths) if widths else 0.0
        total_height = sum(heights) + PAGE_GAP * max(0, len(shown) - 1) + 2 * PAGE_MARGIN
        viewport = self.viewport().size()
        scene_width = max(content_width + 2 * PAGE_MARGIN, float(viewport.width()))
        scene_height = max(total_height, float(viewport.height()))

        y = PAGE_MARGIN
        if total_height < scene_height:
            y = (scene_height - total_height) / 2 + PAGE_MARGIN
        for item, width, height in zip(shown, widths, heights):
            item.update_size(self.zoom, self.rotation)
            item.setPos((scene_width - width) / 2, y)
            item.setVisible(True)
            y += height + PAGE_GAP

        self._scene.setSceneRect(0, 0, scene_width, scene_height)
        if self.engine is not None:
            self.engine.invalidate()
        self._scene.update()
        self._schedule_update()

    def _fit_zoom(self, mode: str) -> float:
        item = self.items[self._current_page if self.view_mode == VIEW_CONTINUOUS else self._single_page]
        width_pt, height_pt = item.width_pt, item.height_pt
        if self.rotation % 180:
            width_pt, height_pt = height_pt, width_pt
        available_width = max(50, self.viewport().width() - 2 * PAGE_MARGIN - 4)
        zoom = available_width / width_pt
        if mode == ZOOM_FIT_PAGE:
            available_height = max(50, self.viewport().height() - 2 * PAGE_MARGIN)
            zoom = min(zoom, available_height / height_pt)
        return max(MIN_ZOOM, min(MAX_ZOOM, zoom))

    # ------------------------------------------------------- paginas visibles

    def _schedule_update(self) -> None:
        if not self._visible_timer.isActive():
            self._visible_timer.start()

    def _viewport_scene_rect(self) -> QRectF:
        top_left = self.mapToScene(0, 0)
        return QRectF(top_left, self.mapToScene(self.viewport().width(), self.viewport().height()))

    def _update_visible(self) -> None:
        if self.engine is None or not self.items:
            return
        area = self._viewport_scene_rect()
        visible: List[int] = []
        for item in self._visible_items():
            rect = QRectF(item.pos(), item.boundingRect().size())
            if rect.intersects(area):
                visible.append(item.index)

        if not visible:
            visible = [self._current_page]

        first, last = visible[0], visible[-1]
        limit = len(self.items) - 1
        wanted = [i for i in range(max(0, first - PAGE_BUFFER), min(limit, last + PAGE_BUFFER) + 1)]
        if self.view_mode == VIEW_SINGLE:
            wanted = [self._single_page]

        self.engine.set_wanted(wanted)
        self.engine.prune({make_key(i, self.zoom, self.rotation, self.dpr) for i in wanted})
        # Primero las paginas realmente visibles, despues el buffer.
        for index in visible + [i for i in wanted if i not in visible]:
            self.engine.request(index, self.zoom, self.rotation, self.dpr)

        current = self._page_at_center(area, visible)
        if current != self._current_page:
            self._current_page = current
            self.page_changed.emit(current)

    def _page_at_center(self, area: QRectF, visible: List[int]) -> int:
        if self.view_mode == VIEW_SINGLE:
            return self._single_page
        center_y = area.center().y()
        best, best_distance = visible[0], None
        for index in visible:
            item = self.items[index]
            rect = QRectF(item.pos(), item.boundingRect().size())
            distance = abs(rect.center().y() - center_y)
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    def _on_page_ready(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.items[index].update()

    def refresh_page(self, index: int) -> None:
        """Fuerza el re-renderizado de una pagina (tras anotarla)."""
        if self.engine is None:
            return
        self.engine.drop_page(index)
        self._words_cache.pop(index, None)
        self.engine.request(index, self.zoom, self.rotation, self.dpr)
        if 0 <= index < len(self.items):
            self.items[index].update()

    # ------------------------------------------------------------------ zoom

    def _capture_anchor(self, viewport_pos: Optional[QPointF] = None):
        if not self.items:
            return None
        if viewport_pos is None:
            viewport_pos = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        scene_pos = self.mapToScene(viewport_pos.toPoint())
        item = self._item_at(scene_pos) or self.items[self._current_page]
        page_x, page_y = item.unmap_point(scene_pos - item.pos())
        return item.index, page_x, page_y, viewport_pos

    def _restore_anchor(self, anchor) -> None:
        if anchor is None:
            return
        index, page_x, page_y, viewport_pos = anchor
        item = self.items[index]
        scene_pos = item.pos() + item.map_point(page_x, page_y)
        self.horizontalScrollBar().setValue(int(scene_pos.x() - viewport_pos.x()))
        self.verticalScrollBar().setValue(int(scene_pos.y() - viewport_pos.y()))

    def set_zoom(self, zoom: float, mode: str = ZOOM_FREE, anchor_pos: Optional[QPointF] = None) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        anchor = self._capture_anchor(anchor_pos)
        self.zoom_mode = mode
        self.zoom = zoom
        self.relayout()
        self._restore_anchor(anchor)
        self.zoom_changed.emit(self.zoom)

    def set_zoom_mode(self, mode: str) -> None:
        anchor = self._capture_anchor()
        self.zoom_mode = mode
        self.relayout()
        if mode == ZOOM_FIT_PAGE:
            self.goto_page(self._current_page)
        else:
            self._restore_anchor(anchor)
        self.zoom_changed.emit(self.zoom)

    def zoom_in(self, anchor_pos: Optional[QPointF] = None) -> None:
        target = next((z for z in ZOOM_LEVELS if z > self.zoom + 1e-3), min(MAX_ZOOM, self.zoom * 1.25))
        self.set_zoom(target, ZOOM_FREE, anchor_pos)

    def zoom_out(self, anchor_pos: Optional[QPointF] = None) -> None:
        target = next((z for z in reversed(ZOOM_LEVELS) if z < self.zoom - 1e-3), max(MIN_ZOOM, self.zoom / 1.25))
        self.set_zoom(target, ZOOM_FREE, anchor_pos)

    def set_rotation(self, degrees: int) -> None:
        self.rotation = degrees % 360
        anchor = self._capture_anchor()
        self.relayout()
        if anchor is not None:
            self.goto_page(anchor[0])
        self.zoom_changed.emit(self.zoom)

    def rotate(self, delta: int) -> None:
        self.set_rotation(self.rotation + delta)

    # ------------------------------------------------------------ navegacion

    def set_view_mode(self, mode: str) -> None:
        if mode == self.view_mode:
            return
        page = self._current_page
        self.view_mode = mode
        self._single_page = page
        self.relayout()
        self.goto_page(page)

    def goto_page(self, index: int) -> None:
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, index))
        if self.view_mode == VIEW_SINGLE:
            self._single_page = index
            self.relayout()
            self.verticalScrollBar().setValue(0)
        else:
            item = self.items[index]
            self.verticalScrollBar().setValue(int(item.pos().y() - PAGE_MARGIN))
        if index != self._current_page:
            self._current_page = index
            self.page_changed.emit(index)
        self._update_visible()

    def current_page(self) -> int:
        return self._current_page

    def next_page(self) -> None:
        self.goto_page(self._current_page + 1)

    def previous_page(self) -> None:
        self.goto_page(self._current_page - 1)

    def first_page(self) -> None:
        self.goto_page(0)

    def last_page(self) -> None:
        self.goto_page(len(self.items) - 1)

    def scroll_by(self, delta: int) -> None:
        bar = self.verticalScrollBar()
        value = bar.value() + delta
        if self.view_mode == VIEW_SINGLE:
            if value > bar.maximum() and self._current_page < len(self.items) - 1:
                self.next_page()
                return
            if value < bar.minimum() and self._current_page > 0:
                self.previous_page()
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
                return
        bar.setValue(value)

    # ---------------------------------------------------------------- eventos

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        anchor = self._capture_anchor(QPointF(0, 0))
        self.relayout()
        self._restore_anchor(anchor)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                position = QPointF(event.position())
                if delta > 0:
                    self.zoom_in(position)
                else:
                    self.zoom_out(position)
            event.accept()
            return
        if self.view_mode == VIEW_SINGLE:
            bar = self.verticalScrollBar()
            delta = event.angleDelta().y()
            at_bottom = bar.value() >= bar.maximum() - 1
            at_top = bar.value() <= bar.minimum() + 1
            if delta < 0 and at_bottom and self._current_page < len(self.items) - 1:
                self.next_page()
                event.accept()
                return
            if delta > 0 and at_top and self._current_page > 0:
                self.previous_page()
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
                event.accept()
                return
        super().wheelEvent(event)

    def _item_at(self, scene_pos: QPointF) -> Optional[PageItem]:
        for item in self._visible_items():
            rect = QRectF(item.pos(), item.boundingRect().size())
            if rect.contains(scene_pos):
                return item
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            item = self._item_at(scene_pos)
            if item is not None:
                page_x, page_y = item.unmap_point(scene_pos - item.pos())
                self._drag_origin = (item.index, page_x, page_y)
                self.clear_selection()
                event.accept()
                return
        elif event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_anchor = QPointF(event.position())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            delta = QPointF(event.position()) - self._pan_anchor
            self._pan_anchor = QPointF(event.position())
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            event.accept()
            return
        if self._drag_origin is not None and (event.buttons() & Qt.LeftButton):
            self._update_selection(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == Qt.MiddleButton:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        if self._drag_origin is not None and event.button() == Qt.LeftButton:
            self._update_selection(event.position())
            self._drag_origin = None
            self.selection_changed.emit(bool(self.selection))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Doble clic: selecciona la palabra bajo el cursor."""
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._item_at(scene_pos)
        if item is None:
            super().mouseDoubleClickEvent(event)
            return
        page_x, page_y = item.unmap_point(scene_pos - item.pos())
        words = self._words(item.index)
        for word in words:
            if word[0] <= page_x <= word[2] and word[1] <= page_y <= word[3]:
                self.selection = Selection(item.index, [(word[0], word[1], word[2], word[3])], word[4])
                item.update()
                self.selection_changed.emit(True)
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------- seleccion

    def _words(self, index: int) -> list:
        cached = self._words_cache.get(index)
        if cached is None:
            if self.document is None:
                return []
            cached = sorted(self.document.page_words(index), key=lambda w: (w[5], w[6], w[7]))
            self._words_cache[index] = cached
            if len(self._words_cache) > 8:
                self._words_cache.pop(next(iter(self._words_cache)))
        return cached

    @staticmethod
    def _nearest_word(words: list, x: float, y: float) -> int:
        best, best_distance = 0, None
        for position, word in enumerate(words):
            dx = max(word[0] - x, 0, x - word[2])
            dy = max(word[1] - y, 0, y - word[3])
            distance = dx * dx + dy * dy * 4  # se prioriza la misma linea
            if best_distance is None or distance < best_distance:
                best, best_distance = position, distance
        return best

    def _update_selection(self, viewport_pos: QPointF) -> None:
        if self._drag_origin is None:
            return
        index, start_x, start_y = self._drag_origin
        item = self.items[index]
        scene_pos = self.mapToScene(viewport_pos.toPoint())
        end_x, end_y = item.unmap_point(scene_pos - item.pos())

        words = self._words(index)
        if not words:
            return
        first = self._nearest_word(words, start_x, start_y)
        last = self._nearest_word(words, end_x, end_y)
        if first > last:
            first, last = last, first
        chosen = words[first:last + 1]
        if not chosen:
            self.clear_selection()
            return

        rects = self._merge_lines(chosen)
        text = " ".join(word[4] for word in chosen)
        self.selection = Selection(index, rects, text)
        item.update()

    @staticmethod
    def _merge_lines(words: list) -> List[Tuple[float, float, float, float]]:
        """Une las palabras de una misma linea en un unico rectangulo."""
        rects: List[Tuple[float, float, float, float]] = []
        current = None
        current_line = None
        for word in words:
            line = (word[5], word[6])
            box = [word[0], word[1], word[2], word[3]]
            if current is not None and line == current_line:
                current[0] = min(current[0], box[0])
                current[1] = min(current[1], box[1])
                current[2] = max(current[2], box[2])
                current[3] = max(current[3], box[3])
            else:
                if current is not None:
                    rects.append(tuple(current))
                current = box
                current_line = line
        if current is not None:
            rects.append(tuple(current))
        return rects

    def clear_selection(self) -> None:
        if self.selection is None:
            return
        index = self.selection.page_index
        self.selection = None
        if 0 <= index < len(self.items):
            self.items[index].update()
        self.selection_changed.emit(False)

    def has_selection(self) -> bool:
        return bool(self.selection)

    # -------------------------------------------------------------- busqueda

    def set_search_matches(self, matches: Dict[int, list]) -> None:
        previous = set(self.search_matches)
        self.search_matches = matches
        for index in previous | set(matches):
            if 0 <= index < len(self.items):
                self.items[index].update()

    def add_search_matches(self, index: int, rects: list) -> None:
        if not rects:
            return
        self.search_matches.setdefault(index, []).extend(rects)
        if 0 <= index < len(self.items):
            self.items[index].update()

    def clear_search(self) -> None:
        self.set_search_matches({})
        self.current_match = None

    def set_current_match(self, page_index: int, position: int) -> None:
        previous = self.current_match
        self.current_match = (page_index, position)
        for index in {page_index} | ({previous[0]} if previous else set()):
            if 0 <= index < len(self.items):
                self.items[index].update()
        self._center_on_match(page_index, position)

    def _center_on_match(self, page_index: int, position: int) -> None:
        rects = self.search_matches.get(page_index)
        if not rects or position >= len(rects):
            return
        if self.view_mode == VIEW_SINGLE and page_index != self._single_page:
            self._single_page = page_index
            self.relayout()
        item = self.items[page_index]
        target = item.map_rect(rects[position]).translated(item.pos())
        area = self._viewport_scene_rect()
        if not area.contains(target):
            self.verticalScrollBar().setValue(int(target.center().y() - self.viewport().height() / 2))
            if target.width() < area.width():
                self.horizontalScrollBar().setValue(
                    max(0, int(target.center().x() - self.viewport().width() / 2))
                )
        self._current_page = page_index
        self.page_changed.emit(page_index)
        self._update_visible()

    # ------------------------------------------------------------------ tema

    def apply_theme(self, name: str) -> None:
        self.theme_name = name
        self.setBackgroundBrush(theme.color(name, "scene"))
        self._scene.update()
