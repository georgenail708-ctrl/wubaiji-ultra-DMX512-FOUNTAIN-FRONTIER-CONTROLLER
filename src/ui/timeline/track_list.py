"""
Qascade V1.0 — Track List  [FIXED v6]

v6 修复：
  - 移除 TrackList 内部工具栏，避免左侧轨道头内容区比右侧 ClipArea 多出 32px 垂直偏移。
  - 轨道头高度、容器总高度与 ClipArea 使用同一公式：track.height + TRACK_GAP。
  - TrackHeader 支持底部拖拽调整高度，实时发出 track_height_changed。
  - TrackHeader 支持拖拽到文件夹改变 parent_id，拖到空白/非文件夹区域则移回顶层。
  - 选择、高亮、拖放目标提示改由 paintEvent 绘制，避免 stylesheet 覆盖自绘背景导致状态不明显。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QMenu, QInputDialog, QColorDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRect, QRectF, QSettings, QEvent
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QPaintEvent, QFont, QCursor,
    QPolygonF, QWheelEvent,
)

from ...core.models import Track
from .. import qascade_theme as _th
from ..main_icon_library import make_main_icon, load_tabler_icon

DEFAULT_TRACK_HEIGHT = 64
MIN_TRACK_HEIGHT = 36
MAX_TRACK_HEIGHT = 240
TRACK_GAP = 4
RESIZE_HANDLE = 7
DRAG_THRESHOLD = 6
TRACK_LIST_WIDTH = 204
# 右侧 ClipArea 的轨道容器最小高度为 400px。
# 左侧 TrackList 使用相同的最小内容高度，不做额外补偿，保证两侧垂直滚动范围完全一致。
CLIP_AREA_MIN_CONTENT_HEIGHT = 400
BOTTOM_SCROLL_COMPENSATION = 0  # 已废弃，保留常量避免引用报错
DEFAULT_INDENT_WIDTH = 26
MIN_INDENT_WIDTH = 10
MAX_INDENT_WIDTH = 54


def _track_height(track: Track | None) -> int:
    if track is None:
        return DEFAULT_TRACK_HEIGHT
    h = getattr(track, "height", DEFAULT_TRACK_HEIGHT)
    try:
        value = int(h)
    except Exception:
        value = DEFAULT_TRACK_HEIGHT
    return max(MIN_TRACK_HEIGHT, min(MAX_TRACK_HEIGHT, value))


def _color_strip_h(track_h: int) -> int:
    """轨道色条高度：钳在 [10, 34]，保证名行(≥28px)+开关行(22px)排得下。"""
    return max(10, min(34, int(track_h) - 30))


class ChevronButton(QPushButton):
    """文件夹展开/折叠按钮：QPainter 三角（禁 QSS url 形方案），22px 高。

    点击语义与原 QPushButton 完全一致（clicked → folder_toggled）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self._hover = False
        self.setFixedSize(20, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        # 自绘外观：抵消全局 QPushButton 盒模型（min-height/padding 会撑到 28px）
        self.setStyleSheet(
            "QPushButton{background:transparent;border:none;min-height:0px;padding:0px;}")

    def set_expanded(self, on: bool):
        if self._expanded != bool(on):
            self._expanded = bool(on)
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(_th.BG_BUTTON_HOVER)))
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 3, 3)
        col = QColor(_th.FOCUS_BLUE if (self._hover or self.isDown()) else _th.TEXT_SECONDARY)
        p.setBrush(QBrush(col))
        p.setPen(Qt.PenStyle.NoPen)
        size = 9
        cx = (self.width() - size) / 2 + 2   # 视觉居中（三角重心偏左，右移补偿）
        cy = (self.height() - size) / 2
        if self._expanded:
            tri = QPolygonF([
                QPointF(cx, cy + size * 0.25), QPointF(cx + size, cy + size * 0.25),
                QPointF(cx + size * 0.5, cy + size * 0.78),
            ])
        else:
            tri = QPolygonF([
                QPointF(cx + size * 0.28, cy), QPointF(cx + size * 0.28, cy + size),
                QPointF(cx + size * 0.75, cy + size * 0.5),
            ])
        p.drawPolygon(tri)
        p.end()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)


class TrackHeader(QWidget):
    mute_toggled  = pyqtSignal(str, bool)
    solo_toggled  = pyqtSignal(str, bool)
    track_renamed = pyqtSignal(str, str)
    track_deleted = pyqtSignal(str)
    track_moved   = pyqtSignal(str, int)
    color_changed = pyqtSignal(str, str)
    filter_edit   = pyqtSignal(str)
    folder_toggled = pyqtSignal(str)
    folder_requested = pyqtSignal(str)
    assign_parent_requested = pyqtSignal(str)
    track_clicked = pyqtSignal(str)
    track_height_changed = pyqtSignal(str, int)
    header_drag_preview = pyqtSignal(str, object)
    header_drop_requested = pyqtSignal(str, object)
    header_drag_cancelled = pyqtSignal(str)
    indent_width_changed = pyqtSignal(int)
    track_copy = pyqtSignal(str)   # 复制轨道到剪贴板

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self._track = track
        self._selected = False
        self._drop_target = False
        self._resizing = False
        self._resize_origin_y = 0
        self._resize_origin_h = _track_height(track)
        self._drag_candidate = False
        self._dragging = False
        self._press_pos = QPoint()
        self._indent_width = DEFAULT_INDENT_WIDTH
        self._main_layout = None
        self._row2_layout = None
        self.setFixedSize(TRACK_LIST_WIDTH, _track_height(track) + TRACK_GAP)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        self._main_layout = main
        main.setContentsMargins(4, 4, 4, 6)
        main.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setSpacing(5)

        self._color_btn = QPushButton()
        # D5 头行解剖：色条不再按整行高拉满（挤掉 M/S/F 行），
        # 高度钳制后名行 + 开关行在默认 64px 头内完整排布。
        self._color_btn.setFixedSize(8, _color_strip_h(_track_height(self._track)))
        self._color_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9 交互润色
        self._color_btn.setStyleSheet(
            f"background:{self._track.color}; border:none; border-radius:2px;"
            "min-height:0px; padding:0px;")
        self._color_btn.clicked.connect(self._pick_color)
        row1.addWidget(self._color_btn)

        self._fold_btn = ChevronButton()
        self._fold_btn.clicked.connect(lambda: self.folder_toggled.emit(self._track.id))
        row1.addWidget(self._fold_btn)

        self._folder_icon_label = QLabel()
        self._folder_icon_label.setFixedSize(14, 14)
        row1.addWidget(self._folder_icon_label)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)

        self._name_label = QLabel(self._track.name)
        self._name_label.setStyleSheet(f"color:{_th.TEXT_PRIMARY}; font-size:11px; font-weight:bold;")
        name_col.addWidget(self._name_label)

        self._filter_label = QLabel()
        # 微字号 9→10px（§5 最小允许值）；TrackHeader 为固定高度，min 高度 36px 行内仍可容纳
        self._filter_label.setStyleSheet(
            f"color:{_th.FOCUS_BLUE}; font-size:10px; font-family:'Consolas','DejaVu Sans Mono';")
        name_col.addWidget(self._filter_label)

        row1.addLayout(name_col)
        row1.addStretch()

        # Universe 信息右对齐（D5 解剖：名左 / 元信息右）
        self._universe_label = QLabel()
        self._universe_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._universe_label.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; font-size:10px;")
        row1.addWidget(self._universe_label)

        main.addLayout(row1)

        # M/S/F 开关行：右对齐（与 Universe 元信息同侧）
        row2 = QHBoxLayout()
        self._row2_layout = row2
        row2.setSpacing(3)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.addStretch()

        self._mute_btn = self._make_btn("M", _th.WARN_ORANGE, _th.TEXT_SELECTED_FG, checkable=True)    # warn_orange（§4.F 橙语义保 hue）
        self._mute_btn.toggled.connect(lambda v: self.mute_toggled.emit(self._track.id, v))
        self._solo_btn = self._make_btn("S", _th.SUCCESS_GREEN, _th.TEXT_SELECTED_FG, checkable=True)   # success_green（§4.F 绿语义保 hue）
        self._solo_btn.toggled.connect(lambda v: self.solo_toggled.emit(self._track.id, v))
        self._filter_btn = self._make_btn("F", _th.INFO_BLUE, _th.TEXT_SELECTED_FG)                     # info_blue（§4.F 蓝语义保 hue）
        self._filter_btn.clicked.connect(lambda: self.filter_edit.emit(self._track.id))

        for btn in (self._mute_btn, self._solo_btn, self._filter_btn):
            row2.addWidget(btn)
        main.addLayout(row2)

    @staticmethod
    def _make_btn(text: str, bg_on: str, fg_on: str, checkable: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(checkable)
        btn.setFixedSize(22, 22)     # D5 轨道头解剖：控件统一 22px（§5 次工具栏档）
        btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9 交互润色
        # min-height:0/padding:0 抵消全局 QPushButton 的 min-height+padding 盒模型，
        # 否则 setFixedSize 在 Qt 样式表下会被撑到 28px 高。
        btn.setStyleSheet(f"""
            QPushButton {{
                background:{_th.BG_BUTTON}; color:{_th.TEXT_TERTIARY};
                border:1px solid {_th.BORDER_NORMAL}; border-radius:3px;
                font-size:10px; font-weight:bold;
                min-height:0px; padding:0px;
            }}
            QPushButton:checked {{
                background:{bg_on}; color:{fg_on}; border-color:{bg_on};
            }}
            QPushButton:hover {{ background:{_th.BG_BUTTON_HOVER}; }}
        """)
        return btn

    def set_highlight(self, highlighted: bool):
        self._selected = bool(highlighted)
        self.update()

    def set_drop_target(self, highlighted: bool):
        if self._drop_target != bool(highlighted):
            self._drop_target = bool(highlighted)
            self.update()

    def refresh(self):
        depth = int(getattr(self._track, "_ui_depth", 0) or 0)
        self._apply_depth_layout(depth)
        is_folder = bool(getattr(self._track, "is_folder", False))
        # 多层级时显示对应的缩进标记
        if depth == 0:
            child_mark = ""
        else:
            child_mark = "└ "
        self._name_label.setText(child_mark + self._track.name)
        self._folder_icon_label.setVisible(is_folder)
        if is_folder:
            self._folder_icon_label.setPixmap(make_main_icon("folder", 14).pixmap(14, 14))
        parent_name = getattr(self._track, "_ui_parent_name", "") or getattr(self._track, "parent_id", "")
        if depth <= 0:
            self._name_label.setToolTip(self._track.name)
        else:
            self._name_label.setToolTip(f"层级 {depth}，附属于：{parent_name}\n{self._track.name}")
        is_folder = bool(getattr(self._track, "is_folder", False))
        self._fold_btn.setVisible(is_folder)
        self._fold_btn.set_expanded(bool(getattr(self._track, "folder_expanded", True)))
        if is_folder:
            f = QFont(self._name_label.font()); f.setBold(True); self._name_label.setFont(f)
        else:
            f = QFont(self._name_label.font()); f.setBold(False); self._name_label.setFont(f)
        fl = self._track.filter_label() if hasattr(self._track, "filter_label") else ""
        self._filter_label.setStyleSheet(
            f"color:{_th.FOCUS_BLUE}; font-size:10px; font-family:'Consolas','DejaVu Sans Mono';")
        self._filter_label.setText(fl)
        universes = getattr(self._track, "universes", None)
        if universes and len(universes) > 1:
            u_str = ",".join(f"U{u}" for u in universes)
            self._universe_label.setText(u_str)
            self._universe_label.setStyleSheet(f"color:{_th.FOCUS_BLUE}; font-size:10px;")
        else:
            univ = getattr(self._track, "universe", 1)
            self._universe_label.setText(f"U{univ}")
            self._universe_label.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; font-size:10px;")
        self._color_btn.setStyleSheet(f"background:{self._track.color}; border:none; border-radius:2px;")
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(getattr(self._track, "muted", False))
        self._mute_btn.blockSignals(False)
        self._solo_btn.blockSignals(True)
        self._solo_btn.setChecked(getattr(self._track, "solo", False))
        self._solo_btn.blockSignals(False)
        self.update_height()

    def update_height(self):
        h = _track_height(self._track)
        self.setFixedSize(TRACK_LIST_WIDTH, h + TRACK_GAP)
        self._color_btn.setFixedHeight(_color_strip_h(h))
        self.update()

    def set_indent_width(self, value: int):
        self._indent_width = max(MIN_INDENT_WIDTH, min(MAX_INDENT_WIDTH, int(value)))
        self._apply_depth_layout(int(getattr(self._track, "_ui_depth", 0) or 0))
        self.update()

    def _apply_depth_layout(self, depth: int):
        depth = max(0, int(depth or 0))
        indent = depth * self._indent_width
        if self._main_layout is not None:
            self._main_layout.setContentsMargins(4 + indent, 4, 4, 6)
        if self._row2_layout is not None:
            # M/S/F 右对齐后无需左侧内容缩进
            self._row2_layout.setContentsMargins(0, 0, 0, 0)
        self._name_label.setIndent(0)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._track.color), self, "选择轨道颜色")
        if c.isValid():
            self.color_changed.emit(self._track.id, c.name())

    def _show_menu(self, pos):
        menu = QMenu(self)
        # 2026-08-27 换肤：原自定义 QMenu 样式表（#2b2b2b/#1a5c8f/#444）与全局主题重复，
        # 按 §3/§5 删除，统一使用全局 QMenu 四态样式。
        def _menu_item(text, icon_key, slot):
            act = menu.addAction(text)
            act.setIcon(make_main_icon(icon_key, 16))
            act.triggered.connect(slot)
            return act

        _menu_item("重命名", "rename", self._rename)
        if bool(getattr(self._track, "is_folder", False)):
            _menu_item("展开/折叠文件夹", "folder_toggle", lambda: self.folder_toggled.emit(self._track.id))
        else:
            _menu_item("放入/更换上级文件夹…", "folder_move", lambda: self.assign_parent_requested.emit(self._track.id))
        _menu_item("设置轨道高度…", "track_height", self._set_height_from_menu)
        _menu_item("设置文件夹缩进余量…", "indent", self._set_indent_from_menu)
        _menu_item("恢复默认高度", "undo", lambda: self._emit_height(DEFAULT_TRACK_HEIGHT))
        _menu_item("更改颜色", "color", self._pick_color)
        _menu_item("编辑过滤范围", "filter", lambda: self.filter_edit.emit(self._track.id))
        menu.addSeparator()
        _menu_item("上移轨道", "move_up", lambda: self.track_moved.emit(self._track.id, -1))
        _menu_item("下移轨道", "move_down", lambda: self.track_moved.emit(self._track.id, +1))
        menu.addSeparator()
        _menu_item("复制轨道（含所有 Clip）", "track_copy", lambda: self.track_copy.emit(self._track.id))
        # 危险动作（删除）：bright_error 着色图标提示
        act_del = menu.addAction("删除轨道")
        act_del.setIcon(load_tabler_icon("trash", 16, _th.BRIGHT_ERROR))
        act_del.triggered.connect(lambda: self.track_deleted.emit(self._track.id))
        menu.exec(self.mapToGlobal(pos))

    def _set_height_from_menu(self):
        current = _track_height(self._track)
        value, ok = QInputDialog.getInt(self, "设置轨道高度", "轨道内容高度（像素）:", current, MIN_TRACK_HEIGHT, MAX_TRACK_HEIGHT, 1)
        if ok:
            self._emit_height(value)

    def _set_indent_from_menu(self):
        value, ok = QInputDialog.getInt(self, "设置文件夹缩进余量", "每一级文件夹的横向缩进（像素）:", self._indent_width, MIN_INDENT_WIDTH, MAX_INDENT_WIDTH, 1)
        if ok:
            self.indent_width_changed.emit(int(value))

    def _emit_height(self, value: int):
        new_h = max(MIN_TRACK_HEIGHT, min(MAX_TRACK_HEIGHT, int(value)))
        self._track.height = new_h
        self.track_height_changed.emit(self._track.id, new_h)
        self.update_height()

    def _rename(self):
        name, ok = QInputDialog.getText(self, "重命名轨道", "轨道名称:", text=self._track.name)
        if ok and name.strip():
            self.track_renamed.emit(self._track.id, name.strip())

    def _in_resize_zone(self, pos) -> bool:
        return pos.y() >= self.height() - RESIZE_HANDLE

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.track_clicked.emit(self._track.id)
            if self._in_resize_zone(event.position().toPoint() if hasattr(event, "position") else event.pos()):
                self._resizing = True
                self._resize_origin_y = self._event_global_pos(event).y()
                self._resize_origin_h = _track_height(self._track)
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                event.accept()
                return
            self._drag_candidate = True
            self._dragging = False
            self._press_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self._resizing:
            dy = self._event_global_pos(event).y() - self._resize_origin_y
            self._emit_height(self._resize_origin_h + dy)
            event.accept()
            return
        if self._drag_candidate and (event.buttons() & Qt.MouseButton.LeftButton):
            moved = (pos - self._press_pos).manhattanLength()
            if moved >= DRAG_THRESHOLD:
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.header_drag_preview.emit(self._track.id, self._event_global_pos(event))
                event.accept()
                return
        if self._in_resize_zone(pos):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._resizing:
                self._resizing = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                event.accept()
                return
            if self._drag_candidate and self._dragging:
                self.header_drop_requested.emit(self._track.id, self._event_global_pos(event))
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self._drag_candidate = False
                self._dragging = False
                event.accept()
                return
            self.header_drag_cancelled.emit(self._track.id)
            self._drag_candidate = False
            self._dragging = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._resizing and not self._dragging:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)  # R47-④ 文字/边线平滑
        bg = QColor(_th.BG_EDITOR)      # bg_editor 常态
        if self._selected:
            bg = QColor(_th.BG_PANEL)   # bg_panel 选中
        if self._drop_target:
            bg = QColor(_th.SELECT_BLUE)  # select_blue 拖放目标（深蓝选中族）
        p.fillRect(self.rect(), bg)
        # D5 选中指示：左侧 select_blue 竖条（替代原整圈描边）
        content_h = max(0, self.height() - TRACK_GAP)
        if self._selected:
            p.fillRect(QRect(0, 0, 3, content_h), QColor(_th.SELECT_BLUE))
        depth = int(getattr(self._track, "_ui_depth", 0) or 0)
        if depth > 0:
            line_pen = QPen(QColor(_th.BORDER_NORMAL), 1)  # 树缩进参考线（8%白内一档）
            p.setPen(line_pen)
            top = 0
            bottom = self.height() - TRACK_GAP
            for level in range(depth):
                x = 12 + level * self._indent_width
                p.drawLine(x, top, x, bottom)
            elbow_x = 12 + (depth - 1) * self._indent_width
            text_x = 4 + depth * self._indent_width
            mid_y = max(14, min(self.height() - 12, 22))
            p.drawLine(elbow_x, mid_y, text_x - 2, mid_y)
        if self._drop_target:
            p.setPen(QPen(QColor(_th.FOCUS_BLUE), 2))   # focus_blue 拖放高亮描边
            p.drawRect(1, 1, self.width() - 3, self.height() - 3)
            p.setPen(QPen(QColor(_th.FOCUS_BLUE), 1))
            is_folder = bool(getattr(self._track, "is_folder", False))
            hint = "拖放到此文件夹" if is_folder else "拖放到同级"
            p.drawText(32, self.height() - 12, hint)
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))   # separator 底/右发丝线
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY), 1))           # resize 把手
        y = self.height() - 5
        p.drawLine(self.width() - 36, y, self.width() - 8, y)
        p.drawLine(self.width() - 30, y + 2, self.width() - 14, y + 2)
        p.end()

    def set_track(self, track: Track):
        self._track = track
        self.update_height()
        self.refresh()

    def set_audio_mod_marker(self, on: bool):
        """R179 音频调制轨标记（wave-sine 图标；refresh 行数基线冻结，
        标记由 TrackList.refresh_tracks 侧在 refresh 之后补挂）。"""
        lab = self._folder_icon_label
        if on:
            icon = load_tabler_icon("wave-sine", 14, _th.FOCUS_BLUE)
            if not icon.isNull():
                lab.setPixmap(icon.pixmap(14, 14))
            lab.setVisible(True)
        else:
            lab.setVisible(bool(getattr(self._track, "is_folder", False)))

    @property
    def track(self) -> Track:
        return self._track


class TrackList(QWidget):
    mute_toggled     = pyqtSignal(str, bool)
    solo_toggled     = pyqtSignal(str, bool)
    track_renamed    = pyqtSignal(str, str)
    track_deleted    = pyqtSignal(str)
    track_moved      = pyqtSignal(str, int)
    color_changed    = pyqtSignal(str, str)
    filter_edit      = pyqtSignal(str)
    track_selected   = pyqtSignal(str)
    add_track        = pyqtSignal()
    add_folder       = pyqtSignal()
    folder_toggled   = pyqtSignal(str)
    assign_parent_requested = pyqtSignal(str)
    track_height_changed = pyqtSignal(str, int)
    reparent_track_requested = pyqtSignal(str, str)
    scroll_y_changed = pyqtSignal(int)
    track_copy       = pyqtSignal(str)  # 复制轨道到剪贴板
    wheel_requested = pyqtSignal(object)  # 左侧轨道区滚轮转交 ClipArea 统一处理

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(TRACK_LIST_WIDTH)
        self._headers: dict[str, TrackHeader] = {}
        self._track_order: list[str] = []
        self._all_tracks: list[Track] = []
        self._drop_target_id: str | None = None
        self._indent_width = self._load_indent_width()
        self._setup_ui()

    def _load_indent_width(self) -> int:
        try:
            value = int(QSettings("Qascade", "DMXTimeline").value("timeline/folder_indent_width", DEFAULT_INDENT_WIDTH))
        except Exception:
            value = DEFAULT_INDENT_WIDTH
        return max(MIN_INDENT_WIDTH, min(MAX_INDENT_WIDTH, value))

    def _save_indent_width(self):
        try:
            QSettings("Qascade", "DMXTimeline").setValue("timeline/folder_indent_width", int(self._indent_width))
        except Exception:
            pass

    def _setup_ui(self):
        self.setStyleSheet("background:#303030;")  # bg_editor
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ★ 与 ClipArea 完全一致的手动滚动方式：
        #   _viewport 是可见区域（裁剪容器），_container 通过 move() 实现滚动。
        #   这样 sync_scroll 直接设置 _scroll_y 并移动容器，
        #   不依赖 QScrollArea 的自动 range 计算，彻底消除两侧 maximum 不一致的问题。
        self._scroll_y = 0
        self._viewport = QWidget(self)
        self._viewport.setStyleSheet("background:#303030;")  # bg_editor
        self._viewport.setContentsMargins(0, 0, 0, 0)
        # 将 _viewport 加入布局，它会随父控件拉伸
        main.addWidget(self._viewport, stretch=1)

        self._container = QWidget(self._viewport)
        self._container.setStyleSheet("background:#303030;")  # bg_editor
        self._container.installEventFilter(self)
        self._container_lay = QVBoxLayout(self._container)
        self._container_lay.setContentsMargins(0, 0, 0, 0)
        self._container_lay.setSpacing(0)
        # ★ 末尾弹性占位：把容器多余空间全部推到底部
        self._container_lay.addStretch(1)
        self._container.move(0, 0)

        self.installEventFilter(self)
        self._viewport.installEventFilter(self)

    def _visible_tracks(self, tracks: list[Track]) -> list[Track]:
        track_ids = {t.id for t in tracks}
        name_by_id = {t.id: getattr(t, "name", "") for t in tracks}
        # 2026-08-13 R13: 移除「父不在列表即清空 parent_id」的模型重写 —— 该重写
        # 会把折叠/筛选等场景下父轨道暂时不在传入子集的子轨道父链永久切断,
        # 导致文件夹静音/单播漏掉它们(REPRO-A)。引擎祖先链已对缺失父轨道安全
        # (break 当顶层), 孤儿只做显示降级, 模型字段保持原样。
        by_parent: dict[str, list[Track]] = {}
        for t in tracks:
            by_parent.setdefault(getattr(t, "parent_id", "") or "", []).append(t)
        visible: list[Track] = []
        visited: set[str] = set()

        def walk(parent_id: str, depth: int):
            for t in by_parent.get(parent_id, []):
                if t.id in visited:
                    continue
                visited.add(t.id)
                setattr(t, "_ui_depth", depth)
                setattr(t, "_ui_parent_name", name_by_id.get(getattr(t, "parent_id", "") or "", ""))
                visible.append(t)
                # 任意轨道都可以有子轨道（不限 is_folder）
                # 如果是文件夹且未展开，则不递归展示子轨道
                is_folder = bool(getattr(t, "is_folder", False))
                is_expanded = bool(getattr(t, "folder_expanded", True))
                has_children = bool(by_parent.get(t.id))
                if has_children and (not is_folder or is_expanded):
                    walk(t.id, depth + 1)

        id_map = {t.id: t for t in tracks}

        def hidden_by_collapsed_parent(track: Track) -> bool:
            seen: set[str] = set()
            pid = getattr(track, "parent_id", "") or ""
            while pid:
                if pid in seen:
                    return False
                seen.add(pid)
                parent = id_map.get(pid)
                if parent is None:
                    return False
                if bool(getattr(parent, "is_folder", False)) and not bool(getattr(parent, "folder_expanded", True)):
                    return True
                pid = getattr(parent, "parent_id", "") or ""
            return False

        walk("", 0)
        for t in tracks:
            if t.id not in visited:
                pid = getattr(t, "parent_id", "") or ""
                # ★ 修复：只要 parent_id 指向一个在本次轨道列表中实际存在的轨道，
                # 就一律不清空 parent_id——只在 UI 中隐藏或降级展示。
                # 这样可以防止折叠/展开操作时子轨道隔属关系被永久清除。
                if pid and pid in track_ids:
                    # 父轨道存在但未被遇到（即父轨道自身被折叠祖先隐藏）
                    # 隐藏处理：不展示、不清空 parent_id
                    continue
                # parent_id 指向不存在的轨道（孤儿）——只降级展示, 不清空 parent_id:
                # 引擎静音/单播祖先链依赖真实父链, 清空会永久切断(R13 REPRO-A)。
                setattr(t, "_ui_depth", 0)
                setattr(t, "_ui_parent_name", "")
                visible.append(t)
        return visible

    def refresh_tracks(self, tracks: list[Track]):
        self._all_tracks = list(tracks)
        tracks = self._visible_tracks(tracks)
        self._track_order = [t.id for t in tracks]

        for tid in list(self._headers):
            if tid not in self._track_order:
                h = self._headers.pop(tid)
                self._container_lay.removeWidget(h)
                h.deleteLater()

        for h in self._headers.values():
            self._container_lay.removeWidget(h)

        total_h = 0
        for i, track in enumerate(tracks):
            tid = track.id
            if tid not in self._headers:
                header = TrackHeader(track)
                header.set_indent_width(self._indent_width)
                self._wire_header(header)
                self._headers[tid] = header
            else:
                header = self._headers[tid]
                header.set_indent_width(self._indent_width)
                header.set_track(track)
            # R179：调制轨轨头补挂 wave-sine 标记（refresh 之后，防被覆盖）
            header.set_audio_mod_marker(bool(getattr(track, "is_audio_mod", False)))
            self._container_lay.insertWidget(i, header)
            total_h += _track_height(track) + TRACK_GAP

        self._container.setFixedHeight(max(CLIP_AREA_MIN_CONTENT_HEIGHT, total_h))
        self._container.setFixedWidth(TRACK_LIST_WIDTH)
        # ★ 手动滚动模式：容器高度变化后重新将容器位置对齐到当前滚动位置
        self._container.move(0, -self._scroll_y)
        self._clear_drop_target()

    def _wire_header(self, header: TrackHeader):
        header.mute_toggled.connect(self.mute_toggled)
        header.solo_toggled.connect(self.solo_toggled)
        header.track_renamed.connect(self._on_renamed)
        header.track_deleted.connect(self.track_deleted)
        header.track_moved.connect(self.track_moved)
        header.color_changed.connect(self._on_color_changed)
        header.filter_edit.connect(self.filter_edit)
        header.folder_toggled.connect(self.folder_toggled)
        header.assign_parent_requested.connect(self.assign_parent_requested)
        header.track_clicked.connect(self._on_header_clicked)
        header.track_height_changed.connect(self._on_header_height_changed)
        header.header_drag_preview.connect(self._on_header_drag_preview)
        header.header_drop_requested.connect(self._on_header_drop_requested)
        header.header_drag_cancelled.connect(lambda _tid: self._clear_drop_target())
        header.indent_width_changed.connect(self._on_indent_width_changed)
        header.track_copy.connect(self.track_copy)
        self._install_wheel_forwarding(header)

    def _install_wheel_forwarding(self, widget: QWidget):
        """让轨道头及其内部按钮/标签的滚轮事件统一交给时间线滚动逻辑处理。

        左侧 TrackList 使用手动 move() 滚动，没有 QScrollArea。这里对轨道头及子控件安装
        事件过滤器，捕获 Wheel 后通过 wheel_requested 信号交由 ClipArea._handle_wheel
        统一处理，从而保持左侧轨道头和右侧 Clip 区的上下滚动完全同步。
        """
        widgets = [widget]
        try:
            widgets.extend(widget.findChildren(QWidget))
        except Exception:
            pass
        for w in widgets:
            try:
                if not bool(w.property("_timeline_wheel_forwarding")):
                    w.installEventFilter(self)
                    w.setProperty("_timeline_wheel_forwarding", True)
            except Exception:
                pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            self._forward_wheel_event(event)
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, e: QWheelEvent):
        self._forward_wheel_event(e)

    def _forward_wheel_event(self, e: QWheelEvent):
        self.wheel_requested.emit(e)
        e.accept()

    def _on_header_clicked(self, track_id: str):
        self.track_selected.emit(track_id)

    def _on_indent_width_changed(self, value: int):
        self._indent_width = max(MIN_INDENT_WIDTH, min(MAX_INDENT_WIDTH, int(value)))
        self._save_indent_width()
        for header in self._headers.values():
            header.set_indent_width(self._indent_width)
            header.refresh()

    def _on_header_height_changed(self, track_id: str, height: int):
        t = self._track_by_id(track_id)
        if t is not None:
            t.height = _track_height(t)
        self.track_height_changed.emit(track_id, _track_height(t) if t is not None else height)

    def _on_renamed(self, track_id: str, name: str):
        if track_id in self._headers:
            self._headers[track_id].track.name = name
            self._headers[track_id].refresh()
        self.track_renamed.emit(track_id, name)

    def _on_color_changed(self, track_id: str, color: str):
        if track_id in self._headers:
            self._headers[track_id].track.color = color
            self._headers[track_id].refresh()
        self.color_changed.emit(track_id, color)

    def _track_by_id(self, track_id: str) -> Track | None:
        for t in self._all_tracks:
            if t.id == track_id:
                return t
        return None

    def _descendant_ids(self, track_id: str) -> set[str]:
        result: set[str] = set()
        changed = True
        while changed:
            changed = False
            for t in self._all_tracks:
                pid = getattr(t, "parent_id", "") or ""
                if pid == track_id or pid in result:
                    if t.id not in result:
                        result.add(t.id)
                        changed = True
        return result

    def _resolve_drop_parent_id(self, dragged_id: str, global_pos) -> tuple[bool, str]:
        """
        解析拖放目标的 parent_id。

        语义：
          - 拖到文件夹轨道上  → 成为该文件夹的子级（parent_id = folder.id）
          - 拖到普通轨道上    → 成为与该轨道同级（parent_id = target.parent_id）
          - 拖到空白区域      → 移到顶层（parent_id = ""）

        返回 (ok, new_parent_id)：ok=False 表示操作非法，不应执行。
        """
        local = self._container.mapFromGlobal(global_pos)
        if local.y() < 0 or local.y() > self._container.height():
            return True, ""  # 拖到容器外 → 顶层
        target_header = self._container.childAt(local)
        while target_header is not None and not isinstance(target_header, TrackHeader):
            target_header = target_header.parentWidget()
        if target_header is None:
            return True, ""  # 拖到空白 → 顶层
        target = target_header.track
        if target.id == dragged_id:
            return False, ""  # 拖到自身 → 非法
        # 防止将父节点拖入自己的子孙节点（循环）
        desc = self._descendant_ids(dragged_id)
        if target.id in desc:
            return False, ""  # 拖入后代 → 非法
        is_folder = bool(getattr(target, "is_folder", False))
        if is_folder:
            # 拖到文件夹轨道上 → 成为其子级
            return True, target.id
        else:
            # 拖到普通轨道上 → 成为同级（继承目标轨道的 parent_id）
            target_parent = getattr(target, "parent_id", "") or ""
            return True, target_parent

    def _on_header_drag_preview(self, dragged_id: str, global_pos):
        ok, parent_id = self._resolve_drop_parent_id(dragged_id, global_pos)
        if not ok:
            self._clear_drop_target()
            return
        # 高亮目标：拖到文件夹内则高亮文件夹，拖到同级则高亮 parent_id 对应的文件夹
        # 如果 parent_id 为空（顶层）则不高亮任何轨道
        new_target = parent_id if parent_id else None
        if new_target != self._drop_target_id:
            self._clear_drop_target()
            self._drop_target_id = new_target
            if new_target and new_target in self._headers:
                self._headers[new_target].set_drop_target(True)

    def _on_header_drop_requested(self, dragged_id: str, global_pos):
        ok, parent_id = self._resolve_drop_parent_id(dragged_id, global_pos)
        self._clear_drop_target()
        if ok:
            self.reparent_track_requested.emit(dragged_id, parent_id)

    def _clear_drop_target(self):
        if self._drop_target_id and self._drop_target_id in self._headers:
            self._headers[self._drop_target_id].set_drop_target(False)
        self._drop_target_id = None

    def sync_scroll(self, y: int):
        """由 ClipArea.scroll_y_changed 驱动，直接移动容器实现同步滚动。"""
        ch = self._container.height()
        vh = self._viewport.height()
        max_y = max(0, ch - vh)
        target = max(0, min(max_y, int(y)))
        if self._scroll_y != target:
            self._scroll_y = target
            self._container.move(0, -self._scroll_y)

    def get_scroll_y(self) -> int:
        return self._scroll_y

    def set_selected_track(self, track_id: str):
        for tid, header in self._headers.items():
            header.set_highlight(tid == track_id)

    def track_row_index(self, track_id: str) -> int:
        try:
            return self._track_order.index(track_id)
        except ValueError:
            return 0
