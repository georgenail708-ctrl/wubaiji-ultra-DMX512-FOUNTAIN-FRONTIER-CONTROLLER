# -*- coding: utf-8 -*-
"""工程制图 / 论文注释风格的**引出标注**覆盖层（扁平风格皮肤）。

用途：
  1) **超链接模式**（单标注）：说明里点功能链接 → 打开对应面板 + 引出标注。
  2) **零基础模式**（多标注）：像论文**全部脚注当场标明**那样，把当前界面上所有
     可标注功能的引出标注**一次性全部铺开**（编号对应高亮目标），可逐个 ✕ 或一键退出。
     —— 用户明确否掉了"上一步/下一步逐个巡览"的交互。

视觉规格（用户点名：论文注释 / 工程制图引出规格箭头；皮肤走 qascade_theme 令牌）：
  - 配色**一律取 `qascade_theme` 令牌**（唯一真源），本模块不自定义色值。
  - 目标描边高亮：2px FOCUS_BLUE 圆角外框 + 极淡填充 + **编号徽标**（与注释框同号）。
  - 引出线：标注框边缘先走一小段「肩线」，再斜拉到目标边缘；线宽 1.4px；
    末端实心三角箭头（长 12 / 半宽 4.5），尖端恰好触到目标边缘。
  - 标注框：左 3px FOCUS_BLUE 强调条 + 1px 细边 + 圆角 6；标题粗体 + 正文自动换行 +
    右上角矢量 ✕。多标注时框体自动**避让**（纵向错开，不叠盖）。
  - 编号为**矢量绘制**（圆 + 数字），不依赖 emoji/符号字体。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter,
                         QPainterPath, QPen, QPolygonF)
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import qascade_theme as _th

# ── 皮肤令牌（唯一真源：qascade_theme；此处仅做 QColor 包装，不引入新色值）──
def _c(hex_or_rgba: str, alpha: Optional[int] = None) -> QColor:
    col = QColor(hex_or_rgba)
    if alpha is not None:
        col.setAlpha(alpha)
    return col


_ACCENT      = _c(_th.FOCUS_BLUE)                  # 高亮 / 引线 / 箭头 / 强调条
_ACCENT_FILL = _c(_th.FOCUS_BLUE, 38)              # 目标淡填充（很低，避免遮挡）
_BOX_BG      = _c(_th.BG_BOX)                      # 标注框底（抬升面板）
_BOX_BORDER  = _c(_th.BORDER_NORMAL)
_TITLE_FG    = _c(_th.TEXT_PRIMARY)
_BODY_FG     = _c(_th.TEXT_TERTIARY)
_CLOSE_FG    = _c(_th.TEXT_DISABLED)
_CLOSE_HOVER = _c(_th.TEXT_PRIMARY)
_BADGE_BG    = _c(_th.SELECT_BLUE)                 # 编号徽标底（与选中同源）

_BOX_W = 272            # 标注框目标宽度（正文在此宽度内换行）
_BOX_PAD = 10
_ACCENT_BAR = 3         # 左侧强调条宽（面板语汇）
_SHOULDER = 16          # 肩线长度（制图引线与标注框相接的水平段）
_ARROW_LEN = 12.0
_ARROW_HALF_W = 4.5
_GAP = 10               # 标注框与目标之间的最小净距
_BADGE_R = 7.0          # 编号徽标半径
_MAX_ITEMS = 40         # 多标注上限（防极端工程一次画上千个）
# 多标注（零基础模式）用**紧凑页边注**：单行小框，按左右页边成列排开，
# 悬停看全文 —— 这样"每个页面的注释全铺开"仍然可读（不再互相叠盖）。
_CW = 196               # 紧凑框宽
_CH = 22                # 紧凑框高
_CGAP = 3               # 紧凑框列内间距
_EDGE = 6               # 距离窗口边缘
_LANE_GAP = 8           # 页边列与目标区的最小留白


@dataclass
class _Callout:
    """一条引出标注（多标注模式下逐个持有自己的几何）。"""
    target: QWidget
    title: str = ""
    body: str = ""
    index: int = 0
    on_click: Optional[Callable] = None
    keyword: str = ""                    # 定位用键（如 dock 名，供滚动/高亮复用）
    box_rect: QRect = field(default_factory=QRect)
    shoulder: QPointF = field(default_factory=QPointF)
    shoulder_end: QPointF = field(default_factory=QPointF)
    arrow_tip: QPointF = field(default_factory=QPointF)
    close_rect: QRect = field(default_factory=QRect)
    badge_center: QPointF = field(default_factory=QPointF)
    side: str = "right"
    dismissed: bool = False        # 单条 ✕ 关闭（其余标注保留）


class FeatureCalloutOverlay(QWidget):
    """引出标注覆盖层（父窗口 = 主窗口；覆盖整个父窗口，绘制在顶层）。

    - `show_for(...)`：单标注（超链接模式，行为与旧版一致）。
    - `show_all([...])`：**多标注同时铺开**（零基础模式；像论文全部脚注当场标明）。
    """

    closed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.hide()
        self._items: List[_Callout] = []
        self._hover_close: int = -1        # 悬停的 ✕ 所属 item 序号
        self._hover_box: int = -1          # 悬停的框体序号
        self._multi: bool = False          # 多标注模式（点框外不关闭）
        # 可选控制条（单标注 actions / 多标注退出按钮）；默认不建。
        self._bar: Optional[QWidget] = None
        self._bar_label: Optional[QLabel] = None
        self._bar_buttons: list = []
        parent.installEventFilter(self)

    # ── 对外接口 ────────────────────────────────────────────────────────────
    def show_for(self, target: QWidget, title: str, body: str,
                 actions: Optional[list] = None, status: str = "",
                 on_click=None) -> None:
        """单标注（超链接模式）：行为与旧版一致（点框外/✕/Esc 关闭）。"""
        if target is None:
            return
        self._multi = False
        self._items = [_Callout(target=target, title=str(title or ""),
                                body=str(body or ""), index=0, on_click=on_click)]
        self._set_actions(actions, status)
        self._present()

    def show_all(self, entries: List[Tuple[QWidget, str, str]],
                 on_click_factory: Optional[Callable[[str], Callable]] = None,
                 status: str = "", on_exit: Optional[Callable] = None,
                 keyword_of: Optional[Callable[[object], str]] = None) -> int:
        """**多标注同时铺开**（零基础模式）。

        entries: [(target, title, body), ...]；keyword_of 可选（由入口给定位键）。
        on_click_factory(title_or_key) → 点该框的回调（打开对应面板）。
        返回实际铺开的标注数（0 = 无可见目标）。
        """
        items: List[_Callout] = []
        for i, ent in enumerate(list(entries or [])[:_MAX_ITEMS]):
            try:
                target, title, body = ent[0], ent[1], ent[2]
            except Exception:
                continue
            if target is None:
                continue
            kw = ""
            if keyword_of is not None:
                try:
                    kw = str(keyword_of(ent) or "")
                except Exception:
                    kw = ""
            cb = None
            if on_click_factory is not None:
                try:
                    cb = on_click_factory(kw or str(title))
                except Exception:
                    cb = None
            items.append(_Callout(target=target, title=str(title or ""),
                                  body=str(body or ""), index=len(items) + 1,
                                  on_click=cb, keyword=kw))
        self._clear_actions()
        if not items:
            self.dismiss()
            return 0
        self._multi = True
        self._items = items
        # 顶部控制条：进度 + 单一「退出」（用户：不是上一步/下一步）
        actions = [("退出", on_exit)] if (status or on_exit) else None
        self._set_actions(actions, status)
        self._present()
        return len(items)

    def dismiss(self) -> None:
        self._items = []
        self._hover_close = -1
        self._hover_box = -1
        self._multi = False
        self._clear_actions()
        if self._bar is not None:
            self._bar.hide()
        self.hide()
        self.closed.emit()

    def item_count(self) -> int:
        return len(self._items)

    # ── 控制条（可选）──────────────────────────────────────────────────────
    def _bar_button_style(self) -> str:
        return (f"QPushButton{{background:{_th.BG_BUTTON};color:{_th.TEXT_PRIMARY};"
                f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;"
                "padding:2px 8px;font-size:11px;}"
                f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};}}")

    def _ensure_bar(self) -> None:
        if self._bar is not None:
            return
        bar = QWidget(self)
        bar.setObjectName("featureCalloutBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 不让控制条上的鼠标事件冒泡到覆盖层（否则点条空白处会被当成「框外」而关闭）
        bar.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        bar.setStyleSheet(
            f"#featureCalloutBar{{background:{_th.BG_BOX};"
            f"border:1px solid {_th.BORDER_NORMAL};border-radius:6px;}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(6)
        label = QLabel("", bar)
        label.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:10px;background:transparent;border:none;")
        h.addWidget(label)
        h.addStretch(1)
        self._bar = bar
        self._bar_label = label
        bar.hide()

    def _set_actions(self, actions: Optional[list], status: str) -> None:
        self._clear_actions()
        if not actions and not status:
            return
        self._ensure_bar()
        assert self._bar is not None and self._bar_label is not None
        self._bar_label.setText(str(status or ""))
        self._bar_label.setVisible(bool(status))
        layout = self._bar.layout()
        for label, cb in (actions or []):
            b = QPushButton(str(label), self._bar)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(self._bar_button_style())
            if cb is not None:
                b.clicked.connect(cb)
            layout.addWidget(b)
            self._bar_buttons.append(b)
        self._bar.adjustSize()
        self._bar.show()

    def _clear_actions(self) -> None:
        import contextlib
        for b in self._bar_buttons:
            with contextlib.suppress(Exception):
                b.setParent(None)
                b.deleteLater()
        self._bar_buttons = []
        if self._bar is not None:
            self._bar.hide()

    def _sync_bar(self) -> None:
        """控制条固定停靠覆盖层顶部居中（多标注不宜贴着某个框，避免歧义）。"""
        if self._bar is None:
            return
        self._bar.show()
        self._bar.adjustSize()
        bw, bh = self._bar.width(), self._bar.height()
        host = self.rect()
        self._bar.move(max(host.left() + 4, (host.width() - bw) // 2), host.top() + 8)
        self._bar.raise_()

    # ── 几何 ───────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):  # noqa: N802 (Qt 覆写)
        if obj is self.parentWidget() and ev.type() in (
                ev.Type.Resize, ev.Type.Move, ev.Type.Show):
            self._sync_geometry()
            if self.isVisible():
                self._relayout_all()
                self._sync_bar()
            self.update()
        return super().eventFilter(obj, ev)

    def _sync_geometry(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())

    @staticmethod
    def _rect_of(target: QWidget, parent: QWidget) -> Optional[QRect]:
        """目标控件在覆盖层坐标系里的矩形（绘制时实时取 → 自动跟随）。"""
        try:
            if target is None or not target.isVisible():
                return None
            tl = target.mapTo(parent, QPoint(0, 0))
            return QRect(tl, target.size())
        except Exception:
            return None

    def _font(self, pt: float, bold: bool = False) -> QFont:
        f = QFont(); f.setPointSizeF(pt); f.setBold(bold)
        try:
            f.setFamily(_th.FONT_MAIN)
        except Exception:
            pass
        return f

    def _present(self) -> None:
        self._sync_geometry()
        self.show()
        self.raise_()
        self._relayout_all()
        self._sync_bar()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _box_size(self, item: _Callout) -> Tuple[int, int]:
        if self._multi:
            return _CW, _CH          # 紧凑页边注（全文在悬停提示里）
        fm_t = QFontMetrics(self._font(9.5, True))
        fm_b = QFontMetrics(self._font(9.0))
        text_w = _BOX_W - _BOX_PAD * 2 - _ACCENT_BAR - 18   # 18 = 编号/关闭预留
        body_h = fm_b.boundingRect(QRect(0, 0, text_w, 10000),
                                   int(Qt.TextFlag.TextWordWrap), item.body).height()
        hint_h = QFontMetrics(self._font(8.0)).height() + 2 if item.on_click is not None else 0
        return _BOX_W, _BOX_PAD * 2 + fm_t.height() + 4 + body_h + hint_h

    def _place_one(self, item: _Callout, tr: QRect, taken: List[QRect]) -> QRect:
        """给一条标注选位置：优先目标四周，逐个候选做**避让**（不与已放置框重叠）。"""
        _w, box_h = self._box_size(item)
        host = self.rect()
        cands = [
            (QPoint(tr.right() + _GAP + _SHOULDER, tr.center().y() - box_h // 2), "right"),
            (QPoint(tr.left() - _GAP - _SHOULDER - _w, tr.center().y() - box_h // 2), "left"),
            (QPoint(tr.center().x() - _w // 2, tr.bottom() + _GAP + _SHOULDER), "bottom"),
            (QPoint(tr.center().x() - _w // 2,
                    tr.top() - _GAP - _SHOULDER - box_h), "top"),
        ]
        margin = 6
        for tl, side in cands:
            r = QRect(tl.x(), tl.y(), _w, box_h)
            r.moveLeft(max(host.left() + margin, min(r.left(), host.right() - _w - margin)))
            r.moveTop(max(host.top() + margin, min(r.top(), host.bottom() - box_h - margin)))
            if not any(r.intersects(q.adjusted(-2, -2, 2, 2)) for q in taken):
                item.side = side
                return r
        # 冲突：从"目标下方"起向下逐格挪，直到不冲突（或到边界）
        base = QRect(cands[2][0].x(), cands[2][0].y(), _w, box_h)
        base.moveLeft(max(host.left() + margin, min(base.left(), host.right() - _w - margin)))
        step = max(12, box_h // 3)
        y = base.top()
        for _ in range(60):
            r = QRect(base.left(), y, _w, box_h)
            if y + box_h > host.bottom() - margin:
                break
            if not any(r.intersects(q.adjusted(-2, -2, 2, 2)) for q in taken):
                item.side = "bottom"
                return r
            y += step
        item.side = "bottom"
        r = base
        r.moveTop(max(host.top() + margin, min(r.top(), host.bottom() - box_h - margin)))
        return r

    def _relayout_all(self) -> None:
        """为全部标注算几何。

        多标注（零基础模式）：**页边注**布局 —— 左右两条页边成列排开（不在窗口
        中间互相叠盖），逐条引线指向目标；这是"每个页面注释全铺开仍可读"的关键。
        单标注（超链接模式）：目标四周择优 + 避让（原行为不变）。
        """
        host = self.rect()
        if self._multi:
            self._relayout_marginal(host)
            return
        taken: List[QRect] = []
        for item in self._items:
            if item.dismissed:
                item.box_rect = QRect()
                continue
            tr = self._rect_of(item.target, self.parentWidget())
            if tr is None:
                item.box_rect = QRect()      # 不可见 → 不画（但仍留在列表里）
                continue
            item.box_rect = self._place_one(item, tr, taken)
            taken.append(QRect(item.box_rect))
            self._leader(item, tr)
            item.close_rect = QRect(item.box_rect.right() - 18, item.box_rect.top() + 3, 15, 15)
            item.badge_center = QPointF(max(host.left() + 10, tr.left() + 6),
                                        max(host.top() + 10, tr.top() - 4))

    def _relayout_marginal(self, host: QRect) -> None:
        """页边注布局（论文式，就近分栏）：

        - 目标在左半屏 → 左栏；右半屏 → 右栏（注释贴着它所标注的对象，可读性强）；
        - 栏内按目标纵向位置排序，注释框纵向对齐到目标附近（被挡则顺延）；
        - 一栏装不下 → 向内再开一列（最多 4 列），保证"全部铺开"仍不叠盖。
        """
        vis = []
        for item in self._items:
            if item.dismissed:
                item.box_rect = QRect()
                continue
            tr = self._rect_of(item.target, self.parentWidget())
            if tr is None:
                item.box_rect = QRect()
                continue
            vis.append((item, tr))
        if not vis:
            return
        mid_x = host.center().x()
        per_col = max(1, (host.height() - _EDGE * 2) // (_CH + _CGAP))
        left = sorted([p for p in vis if p[1].center().x() < mid_x],
                      key=lambda p: p[1].center().y())
        right = sorted([p for p in vis if p[1].center().x() >= mid_x],
                       key=lambda p: p[1].center().y())

        def _lanes(side: str, items_, k_from: int) -> int:
            """把 items_ 铺进该侧的若干列；返回用掉的列数。"""
            k = 0
            while items_:
                lane = items_[:per_col]
                items_ = items_[per_col:]
                if len(items_) == 0 and not lane:
                    break
                if k >= 2:                      # 每侧最多 2 列
                    lane = lane + items_
                    items_ = []
                inset = k_from + k * (_CW + _LANE_GAP)
                x = _EDGE + inset if side == "left" else host.right() - _CW - _EDGE - inset
                y_prev = None
                for item, tr in lane:
                    y = tr.center().y() - _CH // 2
                    y = max(host.top() + _EDGE,
                            min(y, host.bottom() - _EDGE - _CH))
                    if y_prev is not None and y < y_prev + _CH + _CGAP:
                        y = y_prev + _CH + _CGAP
                    y = min(y, host.bottom() - _EDGE - _CH)
                    y_prev = y
                    item.box_rect = QRect(int(x), int(y), _CW, _CH)
                    self._leader(item, tr)
                    item.close_rect = QRect(item.box_rect.right() - 16,
                                            item.box_rect.top() + 3, 14, 14)
                    item.badge_center = QPointF(max(host.left() + 10, tr.left() + 6),
                                                max(host.top() + 10, tr.top() - 4))
                k += 1
            return max(1, k)

        _lanes("left", left, 0)
        _lanes("right", right, 0)

    def _leader(self, item: _Callout, tr: QRect) -> None:
        """引线：肩点（贴框边中线）→ 肩线端点 → 箭头尖端（贴目标边）。"""
        br = item.box_rect
        if item.side == "right":
            item.shoulder = QPointF(br.left(), br.center().y())
            item.arrow_tip = QPointF(tr.right(), tr.center().y())
            item.shoulder_end = QPointF(item.shoulder.x() - _SHOULDER, item.shoulder.y())
        elif item.side == "left":
            item.shoulder = QPointF(br.right(), br.center().y())
            item.arrow_tip = QPointF(tr.left(), tr.center().y())
            item.shoulder_end = QPointF(item.shoulder.x() + _SHOULDER, item.shoulder.y())
        elif item.side == "bottom":
            item.shoulder = QPointF(br.center().x(), br.top())
            item.arrow_tip = QPointF(tr.center().x(), tr.bottom())
            item.shoulder_end = QPointF(item.shoulder.x(), item.shoulder.y() - _SHOULDER)
        else:  # top
            item.shoulder = QPointF(br.center().x(), br.bottom())
            item.arrow_tip = QPointF(tr.center().x(), tr.top())
            item.shoulder_end = QPointF(item.shoulder.x(), item.shoulder.y() + _SHOULDER)

    # ── 绘制 ───────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):  # noqa: N802 (Qt 覆写)
        self._relayout_all()
        self._sync_bar()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for item in self._items:
            if item.dismissed:
                continue
            tr = self._rect_of(item.target, self.parentWidget())
            if tr is None or item.box_rect.isNull():
                continue
            self._draw_one(p, item, tr)
        p.end()

    def _draw_one(self, p: QPainter, item: _Callout, tr: QRect) -> None:
        # 1) 目标高亮。
        #    多标注（论文式）：**不画长引线**，靠"目标旁编号徽标 ↔ 页边注同号"对应，
        #      避免几十条引线横穿整屏；目标只细描边，小目标才淡填充（大面板不糊界面）。
        #    单标注（超链接模式）：保持原样式（淡填充 + 2px 描边 + 引线 + 箭头）。
        #    填充一律显式 setBrush+drawPath —— fillPath(path, QColor) 在本 PyQt6
        #    构建下会沿用 painter 遗留 brush，导致框体被涂成强调色。
        hl = QRectF(tr).adjusted(-2.0, -2.0, 2.0, 2.0)
        path = QPainterPath(); path.addRoundedRect(hl, 5.0, 5.0)
        if self._multi:
            small = tr.width() * tr.height() <= 90000      # ≤约 300×300
            if small:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_ACCENT_FILL))
                p.drawPath(path)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_ACCENT, 1.4)); p.drawPath(path)
            else:
                # 大目标（面板/整窗）：只画**左上角标记**（工程制图裁切标记式）+ 编号，
                # 不整块描边 —— 避免几十个大框把界面糊成蓝色。
                p.setPen(QPen(_ACCENT, 2.0))
                arm = 14
                p.drawLine(tr.left(), tr.top(), tr.left() + arm, tr.top())
                p.drawLine(tr.left(), tr.top(), tr.left(), tr.top() + arm)
            self._draw_badge(p, item)
            self._draw_box(p, item)
            if item is self._hover_item():
                bp = QPainterPath(); bp.addRoundedRect(QRectF(item.box_rect), 6.0, 6.0)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_ACCENT if item.on_click is not None else _BOX_BORDER,
                              1.4 if item.on_click is not None else 1.0))
                p.drawPath(bp)
            return
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_ACCENT_FILL))
        p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_ACCENT, 2.0)); p.drawPath(path)

        # 2) 引出线：框边 → 肩线 → 尖端
        p.setPen(QPen(_ACCENT, 1.4))
        p.drawLine(item.shoulder, item.shoulder_end)
        p.drawLine(item.shoulder_end, item.arrow_tip)

        # 3) 实心三角箭头（尖端在目标边缘）
        self._draw_arrow(p, item.shoulder_end, item.arrow_tip)

        # 5) 标注框
        self._draw_box(p, item)

        # 6) 悬浮态描边（可点击时暗示「点我打开」）
        if item is self._hover_item():
            br = QRectF(item.box_rect)
            bp = QPainterPath(); bp.addRoundedRect(br, 6.0, 6.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_ACCENT if item.on_click is not None else _BOX_BORDER,
                          1.4 if item.on_click is not None else 1.0))
            p.drawPath(bp)

    def _draw_badge(self, p: QPainter, item: _Callout) -> None:
        c = item.badge_center
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_ACCENT))
        p.drawEllipse(c, _BADGE_R, _BADGE_R)
        f = QFont(); f.setPointSizeF(7.5); f.setBold(True)
        try:
            f.setFamily(_th.FONT_MAIN)
        except Exception:
            pass
        p.setFont(f); p.setPen(QColor(_th.TEXT_SELECTED_FG))
        p.drawText(QRectF(c.x() - _BADGE_R, c.y() - _BADGE_R, _BADGE_R * 2, _BADGE_R * 2),
                   int(Qt.AlignmentFlag.AlignCenter), str(item.index))

    def _draw_arrow(self, p: QPainter, tail: QPointF, tip: QPointF) -> None:
        ang = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
        bx = tip.x() - math.cos(ang) * _ARROW_LEN
        by = tip.y() - math.sin(ang) * _ARROW_LEN
        nx, ny = -math.sin(ang), math.cos(ang)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_ACCENT))
        p.drawPolygon(QPolygonF([
            QPointF(tip.x(), tip.y()),
            QPointF(bx + nx * _ARROW_HALF_W, by + ny * _ARROW_HALF_W),
            QPointF(bx - nx * _ARROW_HALF_W, by - ny * _ARROW_HALF_W),
        ]))

    def _draw_box(self, p: QPainter, item: _Callout) -> None:
        r = QRectF(item.box_rect)
        path = QPainterPath(); path.addRoundedRect(r, 6.0, 6.0)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_BOX_BG))
        p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_BOX_BORDER, 1.0)); p.drawPath(path)
        # 左侧 3px FOCUS_BLUE 强调条（裁剪在圆角内绘制）
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(_ACCENT))
        p.drawRect(QRectF(r.left(), r.top(), _ACCENT_BAR, r.height()))
        p.restore()

        if self._multi:
            # 紧凑页边注：编号 + 标题（全文在悬停提示里；点框=打开对应功能）
            fm = QFontMetrics(self._font(8.6, True))
            tx = item.box_rect.left() + 7 + _ACCENT_BAR
            tw = item.box_rect.width() - 30 - _ACCENT_BAR
            badge = f"{item.index}."
            bw = QFontMetrics(self._font(8.6, True)).horizontalAdvance(badge) + 4
            p.setFont(self._font(8.6, True)); p.setPen(_ACCENT)
            p.drawText(QRect(tx, item.box_rect.top(), bw, item.box_rect.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), badge)
            p.setFont(self._font(8.6)); p.setPen(_TITLE_FG)
            p.drawText(QRect(tx + bw, item.box_rect.top(), tw - bw, item.box_rect.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       fm.elidedText(item.title or "（未命名）",
                                     Qt.TextElideMode.ElideRight, tw - bw))
            # 关闭 ✕（紧凑）
            pen = QPen(_CLOSE_HOVER if (self._hover_close == item.index) else _CLOSE_FG, 1.5)
            p.setPen(pen)
            c = item.close_rect.adjusted(3, 3, -3, -3)
            p.drawLine(c.topLeft(), c.bottomRight())
            p.drawLine(c.topRight(), c.bottomLeft())
            return

        fm_t = QFontMetrics(self._font(9.5, True))
        inner_x = item.box_rect.left() + _BOX_PAD + _ACCENT_BAR
        inner_w = item.box_rect.width() - _BOX_PAD * 2 - _ACCENT_BAR

        # 标题（多标注时前缀同号：与目标旁徽标一对一）
        title = (f"{item.index}. {item.title}" if self._multi and item.title
                 else item.title)
        p.setFont(self._font(9.5, True)); p.setPen(_TITLE_FG)
        p.drawText(QRect(inner_x, item.box_rect.top() + _BOX_PAD,
                         inner_w - 18, fm_t.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   title)

        p.setFont(self._font(9.0)); p.setPen(_BODY_FG)
        body_top = item.box_rect.top() + _BOX_PAD + fm_t.height() + 4
        body_h = item.box_rect.height() - _BOX_PAD * 2 - fm_t.height() - 4
        hint = "点此打开该面板" if item.on_click is not None else ""
        if hint:
            fm_h = QFontMetrics(self._font(8.0))
            body_h = max(0, body_h - fm_h.height() - 2)
        p.drawText(QRect(inner_x, body_top, inner_w, body_h),
                   int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignTop), item.body)
        if hint:
            fm_h = QFontMetrics(self._font(8.0))
            p.setFont(self._font(8.0))
            p.setPen(_ACCENT if item is self._hover_item() else _CLOSE_FG)
            p.drawText(QRect(inner_x, item.box_rect.bottom() - _BOX_PAD - fm_h.height(),
                             inner_w, fm_h.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       hint)

        # 关闭 ✕ —— 两条矢量斜线（不用 emoji/符号字体）
        hovered = (self._hover_item() is item and self._hover_close == item.index)
        pen = QPen(_CLOSE_HOVER if hovered else _CLOSE_FG, 1.6)
        p.setPen(pen)
        c = item.close_rect.adjusted(4, 4, -4, -4)
        p.drawLine(c.topLeft(), c.bottomRight())
        p.drawLine(c.topRight(), c.bottomLeft())

    # ── 命中辅助 ───────────────────────────────────────────────────────────
    def _hover_item(self) -> Optional[_Callout]:
        if 0 <= self._hover_box < len(self._items):
            it = self._items[self._hover_box]
            return None if it.dismissed else it
        return None

    def _item_at(self, pos: QPoint) -> Tuple[int, Optional[_Callout]]:
        for i, it in enumerate(self._items):
            if it.dismissed or it.box_rect.isNull():
                continue
            if it.box_rect.contains(pos):
                return i, it
        return -1, None

    # ── 交互 ───────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, ev):  # noqa: N802 (Qt 覆写)
        pos = ev.position().toPoint()
        hc, hb = -1, -1
        for i, it in enumerate(self._items):
            if it.dismissed:
                continue
            if it.close_rect.contains(pos):
                hc = i
                break
            if it.box_rect.contains(pos):
                hb = i
        item_idx, item = self._item_at(pos)
        self._hover_close = hc
        self._hover_box = item_idx
        # 紧凑页边注：悬停弹出全文（论文注脚的可展开细节）
        if self._multi and item is not None and hc < 0 and item.body:
            try:
                from PyQt6.QtWidgets import QToolTip
                QToolTip.showText(
                    ev.globalPosition().toPoint(),
                    "%d. %s" % (item.index, item.title) + (chr(10) * 2) + item.body)
            except Exception:
                pass
        if hc >= 0 or (item is not None and item.on_click is not None):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, ev):  # noqa: N802 (Qt 覆写)
        pos = ev.position().toPoint()
        # 1) 逐个 ✕：只关这一条（其余标注保留）
        for it in self._items:
            if not it.dismissed and it.close_rect.contains(pos):
                it.dismissed = True
                self.update()
                return
        # 2) 框体：可点击 → 打开对应功能（保留标注，方便继续对照）
        idx, item = self._item_at(pos)
        if item is not None:
            if item.on_click is not None:
                import contextlib
                with contextlib.suppress(Exception):
                    item.on_click()
            return
        # 3) 框外：多标注模式不关闭（用户要"全部铺开慢慢读"）；单标注沿用旧行为
        if not self._multi:
            self.dismiss()

    def keyPressEvent(self, ev):  # noqa: N802 (Qt 覆写)
        if ev.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(ev)
