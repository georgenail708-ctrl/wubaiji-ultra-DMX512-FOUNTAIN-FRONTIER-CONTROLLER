# -*- coding: utf-8 -*-
"""
src/ui/widget_kit.py — 专业设计语言 v2 共享控件骨架
=========================================================
深度重构波次(D波)的标准件。设计解剖学对齐 专业属性面板布局：
  PanelSection  可折叠分区（tabler caret 折叠头行 + 内容体）
  NumSlider     极简数值滑杆（值绘在滑槽上；右键弹数值输入）
  prop_row      属性行（左标签右控件，Properties 行版式）
  apply_dialog_chrome  对话框浮窗标准外框（标题行/内容边距/按钮行）
  EmptyState    空态占位（图标+说明居中）
  scope_qss     容器 QSS 作用域化（#objectName{decl}，不级联子控件）

纪律：
  - 全部纯 QPainter/QSS 实现，禁止 image:url()（Qt6.11 全局回退坑）
  - 颜色一律引用 qascade_theme 令牌
  - 本文件只做呈现，不接业务信号
"""

from __future__ import annotations
from .hidpi import hidpi_pixmap, logical_size  # 再导出：HiDPI 离屏位图属 UI 标准件（C7）

from PyQt6.QtCore import Qt, QRectF, QSize, QEvent
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPainterPath
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QSlider, QToolButton, QVBoxLayout,
                             QWidget)

from . import qascade_theme as _th
from .main_icon_library import load_tabler_pixmap
from .number_slider_style import numeric_style
from .editor_materials import apply_material, chrome_icon, chrome_icons, editor_palette, warm_control_qss

ROW_H = 22
HEADER_H = 26
PAD_S, PAD_M, PAD_L = 4, 8, 12

# ─────────────────────────────────────────────────────────────────────
#  QSS 作用域化（R184-B：容器样式表不得级联子控件）
# ─────────────────────────────────────────────────────────────────────

_QSS_SCOPE_SEQ = iter(range(1, 1_000_000))


def scope_qss(widget: QWidget, decl: str) -> None:
    """容器 QSS 作用域化：写成 ``#objectName{decl}`` 只命中容器自身。

    无选择器的容器 ``setStyleSheet`` 等价 ``*{}``——Qt 里父样式表优先于应用级
    QSS 且不看特异性，会把边框/内边距/底色/文字色强加给全部子控件，并压掉
    全局 QSS 的 hover 态与焦点环（R183-C4 只修了效果库工具行一处，本件全仓
    推广）。复用已有 objectName，没有则自动命名；裸 QWidget 的自绘底色需开
    ``WA_StyledBackground``（QFrame 子类自画背景，不依赖该属性）。
    """
    name = widget.objectName()
    if not name:
        name = f"qssScope{next(_QSS_SCOPE_SEQ)}"
        widget.setObjectName(name)
    if not isinstance(widget, QFrame):
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setStyleSheet(f"#{name}{{{decl}}}")

# ─────────────────────────────────────────────────────────────────────
#  基础小件
# ─────────────────────────────────────────────────────────────────────

class PanelHeader(QWidget):
    """分区头行：三角 + （可选图标）+ 标题，点击折叠/展开。

    `icon_key`（2026-09-20 加）：可选矢量图标 key（走 `main_icon_library`，
    tabler 矢量、**非 emoji**）。本控件是**自绘**的（无 layout，子控件插不进来），
    故图标在 `paintEvent` 里直接绘制 —— 见 `_draw_icon`。给 None/空则不画，
    标题仍从 x=24 起（旧版逐像素不变）。
    """

    def __init__(self, title: str, expanded: bool = True, parent=None,
                 icon_key: str = ""):
        super().__init__(parent)
        self._title = title
        self._expanded = expanded
        self._hover = False
        self._icon_key = str(icon_key or "")
        self.setFixedHeight(HEADER_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def _icon_pixmap(self, ink):
        """懒取图标 pixmap（失败 → None）；同一 key 只取一次。"""
        if not self._icon_key:
            return None
        cached = getattr(self, "_icon_cache", None)
        if cached is not None and cached[0] == (self._icon_key, ink):
            return cached[1]
        px = None
        try:
            from .main_icon_library import make_main_icon
            ic = make_main_icon(self._icon_key, 15)
            if not ic.isNull():
                px = chrome_icon(ic, QSize(15, 15), ink).pixmap(15, 15)
        except Exception:
            px = None
        self._icon_cache = ((self._icon_key, ink), px)
        return px

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        surface = getattr(self, "_numeric_style", None) or numeric_style()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(surface["hover" if self._hover else "base"])
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 4, 4)
        col = surface["text"].color()
        # R183 B4：折叠指示三角改 tabler 矢量（caret-down/caret-right，实存已核），
        # 不再 QPolygonF 自绘；缺图标时静默不画（不阻塞面板头渲染）。
        car = load_tabler_pixmap("caret-down" if self._expanded else "caret-right",
                                 12, col.name())
        if car is not None and not car.isNull():
            p.drawPixmap(6, int(round((self.height() - car.height()) / 2)), car)
        p.setFont(_th.cached_font(point_size=8.5))
        p.setPen(surface["text"])
        text_x = 24.0
        px = self._icon_pixmap(col.name())
        if px is not None and not px.isNull():
            p.drawPixmap(int(text_x), int((self.height() - px.height()) / 2), px)
            text_x += px.width() + 6.0
        p.drawText(QRectF(text_x, 0, self.width() - text_x - 4, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._title)
        p.end()

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self.update()
            parent = self.parent()
            while parent is not None and not isinstance(parent, PanelSection):
                parent = parent.parentWidget()
            if isinstance(parent, PanelSection):
                parent.set_expanded(self._expanded)


class PanelSection(QFrame):
    """可折叠分区容器（属性面板式）。

    用法::

        sec = PanelSection("缓动曲线")
        sec.add_layout(some_layout)      # 或 add_widget
        outer_layout.addWidget(sec)
    """

    def __init__(self, title: str, expanded: bool = True, parent=None,
                 icon_key: str = ""):
        super().__init__(parent)
        self.setObjectName("flatSection")
        self.setStyleSheet(
            "#flatSection { background: transparent; border: none; }")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._header = PanelHeader(title, expanded, icon_key=icon_key)
        v.addWidget(self._header)
        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(PAD_M, PAD_S, PAD_M, PAD_S)
        self._body_lay.setSpacing(4)
        v.addWidget(self._body)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool):
        self._body.setVisible(on)
        self._header._expanded = on
        self._header.update()

    def add_widget(self, w: QWidget):
        self._body_lay.addWidget(w)

    def add_layout(self, lay):
        self._body_lay.addLayout(lay)

    def add_separator(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_th.SEP_LINE}; border: none;")
        self._body_lay.addWidget(line)


def prop_row(label: str, widget: QWidget, label_width: int = 96) -> QWidget:
    """属性行：左标签(text_secondary 11px 右对齐) + 右控件拉伸。

    属性行版式；行高由控件决定，行间距交给调用方布局(4px)。
    """
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(PAD_M, 0, PAD_M, 0)
    h.setSpacing(PAD_M)
    lab = QLabel(label)
    lab.setStyleSheet(
        f"color: {_th.EDITOR_IVORY['muted']}; font-size: 11px; background: transparent;")
    lab.setMinimumWidth(label_width)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    w._kit_label = lab
    return w


# ─────────────────────────────────────────────────────────────────────
#  极简数值滑杆
# ─────────────────────────────────────────────────────────────────────

class NumSlider(QSlider):
    """值绘在滑槽上的 数值滑杆。

    - 横向拖拽调值；滚轮微调（Shift 大步进×10 / Alt 精细×0.1 由 Qt 原生行为近似）
    - 右键（或双击）弹出数值输入框，回车确认、Esc 取消
    - signal valueChanged(float)（继承 QSlider 的 int 信号在此转为 float 语义）
    """

    def __init__(self, minimum: float = 0.0, maximum: float = 1.0, value: float = 0.0,
                 decimals: int = 2, suffix: str = "", parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._decimals = max(0, int(decimals))
        self._suffix = suffix
        self._scale = 10 ** self._decimals
        self._hover = False
        self._dragging = False
        self.setMinimum(int(round(minimum * self._scale)))
        self.setMaximum(int(round(maximum * self._scale)))
        self.setValue(int(round(value * self._scale)))
        self.setFixedHeight(ROW_H + 2)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setRange(self.minimum(), self.maximum())

    # ── 值转换 ──
    def float_value(self) -> float:
        return super().value() / self._scale

    def set_float(self, v: float):
        super().setValue(int(round(v * self._scale)))

    def set_range_float(self, lo: float, hi: float):
        self.setRange(int(round(lo * self._scale)), int(round(hi * self._scale)))

    def _fmt(self) -> str:
        v = self.float_value()
        return f"{v:.{self._decimals}f}{self._suffix}"

    # ── 绘制 ──
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        style = getattr(self, "_numeric_style", None) or numeric_style()
        p.setPen(style["border"])
        p.setBrush(style["base"])
        p.drawRoundedRect(r, 4, 4)
        # 填充段（select_blue；拖拽时提亮）
        frac = 0.0
        span = self.maximum() - self.minimum()
        if span > 0:
            frac = (super().value() - self.minimum()) / span
        fill_w = max(0.0, r.width() * frac)
        fill_brush = style["fill"]
        if self._dragging:
            fill_brush = style["drag"]
        elif self._hover:
            fill_brush = style["hover"]
        if fill_w > 0.5:
            if getattr(self, "_numeric_path_size", None) != self.size():
                self._numeric_fill_path = QPainterPath()
                self._numeric_fill_path.addRoundedRect(r, 4, 4)
                self._numeric_path_size = self.size()
            p.save()
            p.setClipRect(QRectF(0, 0, fill_w + 0.5, self.height()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill_brush)
            p.drawPath(self._numeric_fill_path)
            p.restore()
        # 数值文本（描边保证任意填充上可读）
        p.setFont(_th.cached_font(point_size=8.5))
        txt = self._fmt()
        p.setPen(style["shadow"])
        p.drawText(r.adjusted(1, 1, 1, 1), Qt.AlignmentFlag.AlignCenter, txt)
        p.setPen(style["selected_text"] if fill_w > r.width()*0.18 else style["text"])
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()

    # ── 交互 ──
    def _value_at(self, x: float) -> int:
        """把横坐标映射为滑杆整数刻度（点击定位/连续拖拽共用）。"""
        span = self.maximum() - self.minimum()
        if span <= 0 or self.width() <= 10:
            return self.minimum()
        frac = (x - 5.0) / max(1.0, self.width() - 10.0)
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        return int(round(self.minimum() + frac * span))

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def mousePressEvent(self, e):
        # 右键（或 Alt+左键）= 弹数值键入；左键 = 点哪到哪 + 按住连续拖拽
        #（R47-⑤ 用户点名：此前重复定义导致点击定位从未生效）
        if e.button() == Qt.MouseButton.RightButton or (
                e.button() == Qt.MouseButton.LeftButton
                and e.modifiers() & Qt.KeyboardModifier.AltModifier):
            self._open_edit_popup()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(e.position().x()))
            self._dragging = True
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self._dragging:
            self.setValue(self._value_at(e.position().x()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.update()
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        step = max(1, int(round(self.singleStep())))
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step *= 10
        delta = 1 if e.angleDelta().y() > 0 else -1
        super().setValue(super().value() + step * delta)
        e.accept()

    # ── 右键数值输入弹层 ──
    def _open_edit_popup(self):
        dlg = QWidget(self.window(), Qt.WindowType.Popup)
        dlg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dlg.setStyleSheet(
            f"QWidget {{ background: {_th.EDITOR_IVORY['panel']}; border: 1px solid {_th.EDITOR_IVORY['border']};"
            f" border-radius: 4px; }}")
        h = QHBoxLayout(dlg)
        h.setContentsMargins(PAD_M, PAD_S, PAD_M, PAD_S)
        h.setSpacing(6)
        edit = QLineEdit(self._fmt().rstrip(self._suffix) if self._suffix else self._fmt())
        edit.setFixedWidth(90)
        edit.setStyleSheet(
            f"QLineEdit {{ background: {_th.EDITOR_IVORY['input']}; color: {_th.EDITOR_IVORY['ink']};"
            f" border: 1px solid {_th.EDITOR_IVORY['focus']}; border-radius: 4px;"
            f" min-height: 20px; padding: 1px 6px; font-family: 'Consolas'; }}")
        edit.selectAll()
        h.addWidget(edit)

        def commit():
            try:
                self.set_float(float(edit.text().strip().rstrip('%')))
            except ValueError:
                pass
            dlg.close()

        edit.returnPressed.connect(commit)
        edit.editingFinished.connect(dlg.close)
        apply_material(dlg, kind="shell")
        dlg.show()
        dlg.move(self.mapToGlobal(self.rect().bottomLeft()))
        edit.setFocus()

    # 注意：不覆写 QSlider.value()/setValue()——Qt 内部（setRange 等）
    # 会以原始 int 语义调用它们，覆写会造成双重换算。
    # 浮点 API 只走 float_value()/set_float()。


# ─────────────────────────────────────────────────────────────────────
#  对话框外框 / 空态
# ─────────────────────────────────────────────────────────────────────

_DIALOG_QSS = f"""
QDialog {{
    background-color: {_th.BG_EDITOR};
    border: 1px solid rgba(255,255,255,17);
    border-radius: 6px;
}}
QDialog QLabel {{ background: transparent; color: {_th.TEXT_PRIMARY}; }}
QDialog QLabel[hint=\"true\"] {{ color: {_th.TEXT_SECONDARY}; font-size: 11px; }}
QDialog QPushButton#primary {{
    background-color: {_th.SELECT_BLUE}; color: {_th.TEXT_SELECTED_FG};
    border: 1px solid {_th.SELECT_BLUE}; border-radius: 4px;
    min-height: 24px; padding: 2px 18px; font-weight: normal;
}}
QDialog QPushButton#primary:hover {{ background-color: #5480C5; }}
QDialog QPushButton#primary:pressed {{ background-color: #35507C; }}
QDialog QPushButton[role=\"cancel\"] {{
    background-color: {_th.BG_BUTTON}; color: {_th.TEXT_PRIMARY};
    border: 1px solid {_th.BORDER_NORMAL}; border-radius: 4px;
    min-height: 24px; padding: 2px 18px;
}}
QDialog QPushButton[role=\"cancel\"]:hover {{ background-color: {_th.BG_BUTTON_HOVER}; }}
"""


def apply_dialog_chrome(dialog: QDialog) -> None:
    """统一浮窗外框：圆角面板底 + 主/次按钮语义。

    用法：对话框 __init__ 末尾调用；确定按钮 setObjectName("primary")，
    取消类按钮 setProperty("role", "cancel")。
    """
    dialog.setPalette(editor_palette())
    dialog.setStyleSheet(warm_control_qss(_DIALOG_QSS, _th.EDITOR_IVORY))
    apply_material(dialog, kind="shell")
    chrome_icons(dialog)


def set_button_roles(dialog: QDialog, primary=None, cancel=()) -> None:
    """便捷标记：确定/取消按钮的语义外观。"""
    if primary is not None:
        primary.setObjectName("primary")
    for b in cancel:
        b.setProperty("role", "cancel")
    apply_dialog_chrome(dialog)


# EmptyState（R183-B2 Carbon 五段式扩参）拆到 `empty_state.py`（本文件 500 行门禁）；
# 此处原位 re-export —— 既有 21 个构造点 `from .widget_kit import EmptyState` 零改动。
from .empty_state import EmptyState  # noqa: E402,F401


class ClickSlider(QSlider):
    """极简横向滑杆：点击定位 + 连续拖拽 + 自带中性主题。

    用于替换裸 QSlider（矩阵参数/信号测试等）——裸控件回退全局样式时
    手柄为 10×14 圆角3（用户观感"椭圆"）且点击只翻页不到位。
    """

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        if orientation == Qt.Orientation.Horizontal:
            self.setStyleSheet(
                "QSlider::groove:horizontal { background: "
                + _th.EDITOR_IVORY["input"] + "; height: 6px; border-radius: 3px; }"
                "QSlider::sub-page:horizontal { background: " + _th.EDITOR_IVORY["accent"]
                + "; border-radius: 3px; }"
                "QSlider::handle:horizontal { background: " + _th.EDITOR_IVORY["surface"] + "; width: 14px;"
                " height: 14px; margin: -4px 0; border-radius: 7px;"
                " max-height: 14px; min-height: 14px; }"
                "QSlider::handle:horizontal:hover { background: " + _th.EDITOR_IVORY["hover"] + "; }"
                "QSlider::handle:horizontal:pressed { background: "
                + _th.EDITOR_IVORY["focus"] + "; }")

    def _value_at(self, x: float) -> int:
        span = self.maximum() - self.minimum()
        if span <= 0 or self.width() <= 10:
            return self.minimum()
        frac = (x - 5.0) / max(1.0, self.width() - 10.0)
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        return int(round(self.minimum() + frac * span))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            super().setValue(self._value_at(e.position().x()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            super().setValue(self._value_at(e.position().x()))
            e.accept()
            return
        super().mouseMoveEvent(e)
