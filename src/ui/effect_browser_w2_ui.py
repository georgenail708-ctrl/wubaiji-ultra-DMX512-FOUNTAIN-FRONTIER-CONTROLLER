# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_ui.py — R183 W2-FX 的三个 UI 小件（工具栏/宏区/标签）。

只做"控件树 + 信号接线"，逻辑一律落在功能模块；每个控件类都把构建过程包在
``try/except`` 里 —— 样式表或图标在异常环境下失败时**退化成空控件**，面板
照常可用（增益功能不该让主界面起不来）。

  · :class:`ToolsRow`  —— #5/#6/#9/#10/#11/#14/#15/#17 的入口按钮与开关；
  · :class:`MacroArea` —— #8 三个宏旋钮（中性位 50 = 预设默认值）；
  · :class:`DirtyLabel` —— #17「已改 N 项」。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)

from . import effect_browser_w2_dirty as _dirty
from .flow_layout import FlowLayout
from . import qascade_theme as _th
from .widget_kit import scope_qss

logger = logging.getLogger(__name__)

#: 控件高度（与既有工具栏按钮同档）
BTN_H = 22
#: 宏旋钮滑杆宽度
KNOB_W = 96
#: 宏滑杆中性位（50/100 = 预设默认值）
NEUTRAL_PCT = 50


def _btn(text: str, tip: str, slot) -> QPushButton:
    """创建并接线的工具按钮。"""
    btn = QPushButton(text)
    btn.setToolTip(tip)
    btn.setFixedHeight(BTN_H)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(lambda: slot())
    return btn


def _chk(text: str, tip: str, on: bool, slot) -> QCheckBox:
    """创建并接线的开关。"""
    box = QCheckBox(text)
    box.setToolTip(tip)
    box.setChecked(bool(on))
    box.toggled.connect(lambda v: slot(bool(v)))
    return box


class ToolsRow(QWidget):
    """W2 功能条（挂在工具栏下方、分类条上方）。"""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        # C4：样式表加选择器（修前无选择器 → 底色/下边框级联到每个按钮与开关）
        self.setObjectName("fxToolsRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#fxToolsRow{{background:{_th.BG_PANEL};"
                           f"border-bottom:1px solid {_th.BORDER_NORMAL};}}")
        lay = FlowLayout(self)            # C4：窄窗换行（修前单行硬挤）
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)
        try:
            for widget in self._entries():
                lay.addWidget(widget)
        except Exception:
            logger.debug("effect_browser_w2_ui: tools row build failed",
                         exc_info=True)
        lay.addStretch()

    def _entries(self) -> list:
        """按钮与开关清单（顺序即界面顺序；关闭态异常只影响该项）。"""
        from . import effect_browser_w2_hover as _hover
        from . import effect_browser_w2_pack as _pack
        from . import effect_browser_w2_random as _random
        from . import effect_browser_w2_store as _store
        from . import effect_browser_w2_view as _view
        panel = self._panel
        return [
            _btn("随机预设", "在当前可见预设里随机挑一个并载入（换的是效果本身，"
                           "不是参数）", lambda: _random.random_preset(panel)),
            _btn("存A", "把当前参数快照存入 A 槽（A/B 对比）",
                 lambda: _ab_capture(panel, "A")),
            _btn("存B", "把当前参数快照存入 B 槽（A/B 对比）",
                 lambda: _ab_capture(panel, "B")),
            _btn("A/B 互换", "当前参数与另一槽互换，一键对比两套调参",
                 lambda: _ab_swap(panel)),
            _btn("回到默认", "只把改动过的参数恢复预设默认（未改动的一个不碰）",
                 lambda: _reset(panel)),
            _btn("导出", "把「我的效果」导出为 .json 预设包",
                 lambda: _pack.export_dialog(panel)),
            _btn("导入", "导入 .json 预设包（同名自动加后缀，绝不覆盖）",
                 lambda: _pack.import_dialog(panel)),
            _chk("热度优先", "列表内按使用次数排序（数据来自使用计数）",
                 _store.pref("heat_first", False),
                 lambda v: _view.set_heat_first(panel, v)),
            _chk("试听", "鼠标停留 400ms 试听该预设（移出即停，不改参数）",
                 _store.pref("hover_preview", False),
                 lambda v: _hover.set_enabled(panel, v)),
            _chk("缩略", "当前可见卡片显示 3 相位亮度缩略条（低频刷新）",
                 _store.pref("thumb_anim", True),
                 lambda v: _view.set_thumb_anim(panel, v)),
            _chk("微扰", "「随机」按钮改为当前值 ±30% 微扰（关掉 = 全域随机）",
                 _store.pref("dice_perturb", True),
                 lambda v: _random.set_perturb_mode(panel, v)),
        ]


def _ab_capture(panel, slot: str) -> None:
    from . import effect_browser_w2_ab as _ab
    _ab.ab_capture(panel, slot)


def _ab_swap(panel) -> None:
    from . import effect_browser_w2_ab as _ab
    _ab.ab_swap(panel)


def _reset(panel) -> None:
    """「回到默认」：只复原改动过的参数（#17）。"""
    _dirty.reset_dirty(panel)


class MacroArea(QWidget):
    """参数宏旋钮区（挂在参数容器下方）。"""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        scope_qss(self, f"background:{_th.BG_PANEL};"
                      f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;")
        lay = FlowLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        lay.addWidget(self._title())
        panel._w2_macro_sliders = {}
        panel._w2_macro_labels = {}
        try:
            from . import effect_browser_w2_store as _store
            for mkey in _store.MACRO_KEYS:
                lay.addWidget(self._knob(str(mkey)))
        except Exception:
            logger.debug("effect_browser_w2_ui: macro knobs failed", exc_info=True)
        lay.addStretch()

    def _title(self) -> QLabel:
        label = QLabel("宏")
        label.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
        label.setToolTip("一个旋钮同时驱动多个参数：在参数上右键「分配到宏…」建立映射")
        return label

    def _knob(self, mkey: str) -> QWidget:
        """单个宏槽（标签 + 滑杆；改动即写回映射参数）。"""
        from . import effect_browser_w2_macro as _macro
        box = QWidget()
        box.setStyleSheet("background:transparent;border:none;")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        label = QLabel(f"{mkey.upper()}(0)")
        label.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:10px;")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(NEUTRAL_PCT)
        slider.setFixedWidth(KNOB_W)
        slider.setToolTip(f"{mkey.upper()} 宏：中位 = 预设默认值，"
                          "左右拖动同时推动映射参数")
        slider.valueChanged.connect(
            lambda v, _m=mkey: _macro.apply_macro(self._panel, _m, v / 100.0))
        lay.addWidget(label)
        lay.addWidget(slider)
        self._panel._w2_macro_sliders[mkey] = slider
        self._panel._w2_macro_labels[mkey] = label
        return box


class DirtyLabel(QLabel):
    """标题行「已改 N 项」标签（无改动时自动隐藏）。"""

    def __init__(self, panel, parent=None):
        super().__init__("", parent)
        self.setStyleSheet(f"color:{_th.BRIGHT_WARN};font-size:11px;"
                           "background:transparent;")
        self.setToolTip("当前预设被改动的参数数；可用「回到默认」只复原这些")
        self.setVisible(False)


__all__ = ["BTN_H", "KNOB_W", "NEUTRAL_PCT", "DirtyLabel", "MacroArea", "ToolsRow"]
