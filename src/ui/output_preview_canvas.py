"""Shared Qt painter viewport; final values only, no effect evaluation.

Qt matrix/painter adoption: r185-basic-closeout-preview.md, CLOSE-P.
"""
from math import cos, radians, sin, hypot
from time import monotonic

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QRadialGradient, QPolygonF
from PyQt6.QtWidgets import QWidget, QToolTip

from . import qascade_theme as theme
from .output_preview_camera import PreviewCamera, fixture_direction
from .output_preview_data import describe_output


class OutputPreviewCanvas(QWidget):
    picked = pyqtSignal(str, bool)
    box_selected = pyqtSignal(list, bool)

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self.camera = PreviewCamera()
        self.selected = set()
        self._points = []
        self._directions = {}
        self._key = None
        self._last = self._press = None
        self._dragged = False
        self._box = None
        self._background = QColor(theme.OUTPUT_BG_ROOT)
        self._grid_pen = QPen(QColor(theme.OUTPUT_BG_EDITOR), 1)
        self._idle_pen = QPen(QColor(theme.OUTPUT_TEXT_SECONDARY), 1)
        self._selected_pen = QPen(QColor(theme.KEYFRAME_GOLD), 2)
        self._water_pen = QPen(QColor(theme.FOCUS_BLUE), 2)
        self._axis_pen = QPen(QColor(theme.KEYFRAME_GOLD), 2)
        self._colors = {}
        self.setMinimumSize(120, 120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def fit(self, selected=False):
        self.camera.fit(self.data.outputs, self.selected if selected else ())
        self.invalidate()

    def invalidate(self):
        self._key = None
        self.update()

    def set_selection(self, ids):
        self.selected = set(ids)
        self.update()

    def _color(self, rgb):
        key = tuple(round(value * 31) for value in rgb)
        if key not in self._colors:
            if len(self._colors) >= 256:
                self._colors.clear()
            color = QColor.fromRgbF(*(value / 31 for value in key))
            glow = QRadialGradient(QPointF(), 24)
            center, edge = QColor(color), QColor(color)
            center.setAlpha(165)
            edge.setAlpha(0)
            glow.setColorAt(0, center)
            glow.setColorAt(1, edge)
            self._colors[key] = (QPen(color, 2), QBrush(color), QBrush(glow))
        return self._colors[key]

    def _project(self):
        key = (self.width(), self.height(), self.devicePixelRatioF(),
               self.data.revision, self.camera.zoom,
               self.camera.pan.x(), self.camera.pan.y())
        if key == self._key:
            return
        self.camera.configure(self.width(), self.height())
        self._points = [(output, self.camera.position(output),
                         self.camera.project(self.camera.position(output)))
                        for output in self.data.outputs]
        self._directions = {output.fixture.fixture_id: fixture_direction(output)
                            for output in self.data.outputs}
        self._key = key

    def paintEvent(self, event):
        self._project()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._background)
        self._draw_grid(painter)
        now = monotonic()
        for output, position, point in self._points:
            if point is not None:
                self._draw_fixture(painter, output, position, point, now=now)
        if self._box is not None:
            painter.setPen(self._selected_pen)
            painter.setBrush(QColor(113, 168, 255, 30))
            painter.drawRect(self._box)
        painter.end()

    def _draw_grid(self, painter):
        painter.setPen(self._grid_pen)
        span = self.camera.span
        center = self.camera.center
        for index in range(-5, 6):
            offset = index * span / 10
            pairs = (((center.x() + offset, 0, center.z() - span / 2),
                      (center.x() + offset, 0, center.z() + span / 2)),
                     ((center.x() - span / 2, 0, center.z() + offset),
                      (center.x() + span / 2, 0, center.z() + offset)))
            for start, end in pairs:
                a, b = self.camera.project(start), self.camera.project(end)
                if a is not None and b is not None:
                    painter.drawLine(a, b)

    def _draw_fixture(self, painter, output, position, point, *, now):
        fresh = output.observed and now - output.attempted_at < 2.0
        pen, brush, glow = self._color(output.rgb if fresh else (0.2, 0.2, 0.2))
        if fresh and output.light > 0:
            painter.save()
            painter.translate(point)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(), 24, 24)
            painter.restore()
        painter.setPen(self._selected_pen if output.fixture.fixture_id in self.selected else pen)
        painter.setBrush(brush)
        painter.drawEllipse(point, 5.0, 5.0)
        if not fresh:
            return
        direction = self._display_direction(output)
        self._draw_beam(painter, output, position, point, direction=direction, pen=pen)
        if output.water > 0:
            self._draw_water(painter, position, direction, output, now=now)
        if output.movements:
            self._draw_motion(painter, output, point)

    def _display_direction(self, output):
        direction = self._directions[output.fixture.fixture_id]
        # A plan-view glyph for upright sources; lengths show relative output,
        # not invented metres. Real rotation still determines the direction.
        direction = (direction[0], 0.0, direction[2])
        if abs(direction[0]) + abs(direction[2]) < 0.05:
            direction = (0.0, 0.0, -1.0)
        return direction

    def _screen_direction(self, position, direction):
        start = self.camera.project(position)
        end = self.camera.project(tuple(position[i] + direction[i] * 100 for i in range(3)))
        if start is None or end is None:
            return QPointF(0, -1)
        delta = end - start
        size = hypot(delta.x(), delta.y())
        return delta / size if size > .001 else QPointF(0, -1)

    def _draw_beam(self, painter, output, position, point, *, direction, pen):
        if output.light <= 0:
            return
        vector = self._screen_direction(position, direction)
        endpoint = point + vector * (28 + 42 * output.light)
        normal = QPointF(-vector.y(), vector.x()) * 10
        tint = QColor(pen.color())
        tint.setAlpha(65)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawPolygon(QPolygonF([point, endpoint + normal, endpoint - normal]))
        painter.setPen(pen)
        painter.drawLine(point, endpoint)

    def _draw_water(self, painter, position, direction, output, *, now):
        painter.setPen(self._water_pen)
        count = 3 if len(self._points) > 500 else 8
        start = self.camera.project(position)
        if start is None:
            return
        vector = self._screen_direction(position, direction)
        length = 80 * output.water
        endpoint = start + vector * length
        painter.drawLine(start, endpoint)
        painter.drawText(endpoint + QPointF(5, -4), f"{output.water:.0%}")
        for index in range(count):
            phase = (now * 0.7 + index / count) % 1
            painter.drawEllipse(start + vector * (length * phase), 2, 2)

    def _draw_motion(self, painter, output, point):
        """Show only real nozzle rotation, range and current position."""
        painter.setPen(self._axis_pen)
        labels = []
        for kind, motion, actual, value in output.movements:
            painter.drawArc(QRectF(point.x() - 12, point.y() - 12, 24, 24),
                            int(motion["min"] * 16), int((motion["max"] - motion["min"]) * 16))
            angle = radians(actual)
            painter.drawLine(point, point + QPointF(cos(angle), -sin(angle)) * 15)
            labels.append(f"{kind[-1]} {actual:.0f} {motion['unit']}")
        painter.drawText(point + QPointF(18, -10), " · ".join(labels))

    def _pick(self, point):
        self._project()
        candidates = [(abs(p.x() - point.x()) ** 2 + abs(p.y() - point.y()) ** 2, output)
                      for output, _, p in self._points if p is not None]
        if not candidates:
            return None
        distance, output = min(candidates, key=lambda item: item[0])
        return output if distance <= 144 else None

    def mousePressEvent(self, event):
        self.setFocus()
        self._press = self._last = event.position()
        self._dragged = False
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._box = QRectF(self._press, self._press)

    def mouseMoveEvent(self, event):
        if self._last is not None and event.buttons():
            if self._box is not None:
                self._box = QRectF(self._press, event.position()).normalized()
                self._dragged = True
                self.update()
                return
            delta = event.position() - self._last
            self._last = event.position()
            self._dragged |= (event.position() - self._press).manhattanLength() > 4
            pan = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            pan |= bool(event.buttons() & Qt.MouseButton.MiddleButton)
            self.camera.drag(delta, pan=pan)
            self.invalidate()
            return
        output = self._pick(event.position())
        if output is not None:
            text = describe_output(output, self.data.frames.get(output.fixture.universe))
            QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def mouseReleaseEvent(self, event):
        if self._box is not None:
            self._project()
            ids = [output.fixture.fixture_id for output, _, point in self._points
                   if point is not None and self._box.contains(point)]
            self.box_selected.emit(ids, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            self._box = None
            self._dragged = True
            self.update()
        if not self._dragged and event.button() == Qt.MouseButton.LeftButton:
            output = self._pick(event.position())
            if output is not None:
                self.picked.emit(output.fixture.fixture_id,
                                 bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        self._last = self._press = None

    def wheelEvent(self, event):
        self.camera.wheel(event.angleDelta().y() / 120)
        self.invalidate()
        event.accept()

    def event(self, event):
        """Keep focused viewport fit keys ahead of Main's WindowShortcut."""
        if (event.type() == QEvent.Type.ShortcutOverride and self.hasFocus()
                and event.key() == Qt.Key.Key_F):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F:
            self.fit(selected=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            event.accept()
        else:
            super().keyPressEvent(event)
