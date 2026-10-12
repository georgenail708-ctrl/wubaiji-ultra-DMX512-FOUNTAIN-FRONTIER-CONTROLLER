# -*- coding: utf-8 -*-
"""主控过渡卡双击弹窗（R182）—— 行表增删 + 淡入/淡出曲线分设。

从 studio_master 拆出（≤500 行门禁）。画布内嵌控件（row 里点一下就改）覆盖日常调参；
本弹窗是**高级路径**：可以精确输入百分比、逐行改类型、给淡入/淡出设不同曲线
（曲线在引擎里是 clip 级——分设只能在这里做）。

返回 schema 与节点 params 完全一致：`{"curve_in", "curve_out", "rows"}`。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QToolButton, QVBoxLayout, QWidget)

from src.ui.clip_studio.studio_cards import _RATIO_TYPES
from src.ui.clip_studio.studio_master import (CARD_KEY, CURVE_LABELS, RATIO_MAX,
                                              RATIO_NEUTRAL, _CURVE_VALUES, _clamp_ratio,
                                              curve_of, default_rows, norm_ch_type, normalize_rows,
                                              rows_of)

__all__ = ["MasterTransitionDialog", "_curve_index"]


def _curve_index(value: str) -> int:
    return _CURVE_VALUES.index(value) if value in _CURVE_VALUES else 0


class _RowEditor(QWidget):
    """单行编辑器：类型 combo（可编辑）+ 淡入比例 spin + 淡出比例 spin + 删除。"""

    def __init__(self, row: dict, on_delete, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.type_cb = QComboBox()
        self.type_cb.setEditable(True)
        self.type_cb.addItems([cid for cid, _l in _RATIO_TYPES])
        self.type_cb.setCurrentText(str(row.get("type", "")))
        h.addWidget(self.type_cb, 1)
        self.in_sp, self.out_sp = QSpinBox(), QSpinBox()
        for spin, value in ((self.in_sp, row.get("ratio_in")), (self.out_sp, row.get("ratio_out"))):
            spin.setRange(0, RATIO_MAX)
            spin.setValue(_clamp_ratio(value))
            spin.setSuffix("%")
            h.addWidget(spin)
        kill = QToolButton()
        kill.setText("×")
        kill.setAutoRaise(True)
        kill.clicked.connect(lambda _c=False: on_delete(self))
        h.addWidget(kill)

    def value(self) -> dict:
        return {"type": norm_ch_type(self.type_cb.currentText()),
                "ratio_in": int(self.in_sp.value()), "ratio_out": int(self.out_sp.value())}


class MasterTransitionDialog(QDialog):
    """主控过渡卡双击弹窗：行表增删 + 淡入/淡出曲线分设。"""

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑卡片：主控过渡")
        self.setModal(True)
        self.setMinimumWidth(460)
        node = {"key": CARD_KEY, "params": dict(params or {})}
        self._editors: list = []
        v = QVBoxLayout(self)
        tip = QLabel("按通道类型单独设置过渡比例；曲线为 Clip 级（引擎无逐类型曲线消费点）")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addLayout(self._curve_row(curve_of(node, "curve_in"), curve_of(node, "curve_out")))
        self._body = QVBoxLayout()
        self._body.setSpacing(4)
        v.addLayout(self._body)
        for row in (rows_of(node) or default_rows()):
            self._append(row)
        add = QPushButton("添加行")
        add.clicked.connect(lambda _c=False: self._append(
            {"type": "", "ratio_in": RATIO_NEUTRAL, "ratio_out": RATIO_NEUTRAL}))
        v.addWidget(add)
        v.addLayout(self._button_row())

    def _curve_row(self, curve_in: str, curve_out: str) -> QHBoxLayout:
        """淡入/淡出曲线下拉（clip 级真实字段；分设只能在本弹窗里做）。"""
        head = QHBoxLayout()
        head.addWidget(QLabel("淡入曲线"))
        self.in_cb = self._curve_combo(curve_in)
        head.addWidget(self.in_cb, 1)
        head.addWidget(QLabel("淡出曲线"))
        self.out_cb = self._curve_combo(curve_out)
        head.addWidget(self.out_cb, 1)
        return head

    @staticmethod
    def _curve_combo(current: str) -> QComboBox:
        cb = QComboBox()
        cb.addItems(list(CURVE_LABELS))
        cb.setCurrentIndex(_curve_index(current))
        return cb

    def _button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        ok, cancel = QPushButton("确定"), QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        return row

    def _append(self, row: dict) -> None:
        ed = _RowEditor(dict(row), self._remove, self)
        self._editors.append(ed)
        self._body.addWidget(ed)

    def _remove(self, editor) -> None:
        if editor in self._editors:
            self._editors.remove(editor)
            editor.setParent(None)
            editor.deleteLater()

    def values(self) -> dict:
        """→ `{"curve_in", "curve_out", "rows"}`（与节点 params schema 一致）。"""
        return {"curve_in": _CURVE_VALUES[max(0, self.in_cb.currentIndex())],
                "curve_out": _CURVE_VALUES[max(0, self.out_cb.currentIndex())],
                "rows": normalize_rows([ed.value() for ed in self._editors])}
