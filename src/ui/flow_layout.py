# -*- coding: utf-8 -*-
"""自动换行布局（01 清单 C3「工具行控件溢出/挤压」；02 规范 §6.3）。

参考：Qt 官方 Widgets「Flow Layout Example」（BSD-3-Clause；doc.qt.io
qtwidgets/layouts-flowlayout-example）——同一 addItem/itemAt/takeAt/heightForWidth/
setGeometry 骨架，按本仓需要补两处：

  * ``addStretch()`` / ``addSpacing(n)`` 兼容 QBoxLayout 调用点（工具栏构建代码
    原样复用，只把 ``QHBoxLayout(bar)`` 换成 ``FlowLayout(bar)``）：stretch 在流式
    布局里无意义 → 视作一次 8px 分组间隔；spacing → 固定宽空白项；
  * 行高取该行最高项，控件纵向居中（工具栏混排按钮/下拉/标签时不参差）。

放不下一行时换到下一行（不再硬挤到文字被截断/控件消失）；宿主布局通过
``hasHeightForWidth`` 自动给出第二行高度。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSpacerItem, QSizePolicy

__all__ = ["FlowLayout"]

_DEFAULT_SPACING = 8   # 02 §1 SM
_GROUP_GAP = 8


class FlowLayout(QLayout):
    """横向排布、宽度不足自动换行的布局（纵向居中每行）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    # ── QLayout 必需接口 ────────────────────────────────────────────────
    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ── QBoxLayout 兼容 ─────────────────────────────────────────────────
    def addSpacing(self, size: int) -> None:  # noqa: N802
        self.addItem(QSpacerItem(int(size), 1, QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Minimum))

    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802
        _ = stretch
        self.addSpacing(_GROUP_GAP)

    # ── 排布 ────────────────────────────────────────────────────────────
    def _gap(self) -> int:
        s = self.spacing()
        return s if s >= 0 else _DEFAULT_SPACING

    def _rows(self, width: int) -> list:
        """按可用宽把可见项切成行：[[item, ...], ...]。"""
        rows, row, x = [], [], 0
        gap = self._gap()
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            w = item.sizeHint().width()
            if row and x + gap + w > width:
                rows.append(row)
                row, x = [], 0
            x = w if not row else x + gap + w
            row.append(item)
        if row:
            rows.append(row)
        return rows

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        gap = self._gap()
        y = area.y()
        for row in self._rows(max(1, area.width())):
            line_h = max(item.sizeHint().height() for item in row)
            x = area.x()
            for item in row:
                hint = item.sizeHint()
                if apply:
                    top = y + (line_h - hint.height()) // 2
                    item.setGeometry(QRect(QPoint(x, top), hint))
                x += hint.width() + gap
            y += line_h + gap
        return max(0, y - gap - rect.y()) + m.bottom()
