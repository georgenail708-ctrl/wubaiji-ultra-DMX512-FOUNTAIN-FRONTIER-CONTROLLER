"""
audio_bridge_panel.py — 音乐结构 → effect 桥接器 UI (v24)
===========================================================
v24 重构：
  - 移除单一"轨道类型"下拉框，改为按效果类型分Tab管理
  - 每种效果类型（灯光/泵/电机/阀门）独立启用/禁用、独立预设配置
  - 多种效果类型可同时并行输出
  - 新增"自动调制"Tab：音频参数自动调制配置页面
  - UI分层：主面板 → 主Tab → 子Tab/子面板，避免拥挤
"""
from __future__ import annotations

import logging
import os

import random
import time
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSettings
from PyQt6.QtGui import QColor, QPainter, QFont, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QDoubleSpinBox, QComboBox,
    QHeaderView, QSizePolicy, QFrame, QCheckBox,
    QPlainTextEdit, QSpinBox, QTabWidget, QScrollArea,
    QGridLayout, QSplitter,
)

from ...audio import (
    MusicStructureAnalyzer, MusicStructureEvent,
    MusicToEffectBridge, EffectMapping, SectionLabel,
    PresetPoolEntry, SectionPresetSet, TRACK_TYPES,
    INSTRUMENTS,
    get_global_structure_analyzer, get_global_bridge,
    get_default_preset_sets,
)

# D 波标准件：对话框统一外框 + 主题令牌 + 矢量图标
from ..widget_kit import apply_dialog_chrome, PanelSection
from .. import qascade_theme as _th
from ..main_icon_library import load_tabler_icon
from . import audio_dispatch_state as _dispatch_state

#: 调度台用：记录本面板实例上次上报过的窗格身份键（id(panel) → set）。
#: 仅在"本面板窗格减少时删掉消失的键"时使用，避免误删其它面板/实例的窗格。

# ── 尝试导入调制器（可选模块）──────────────────────────────────────────────
try:
    from ...audio import (
        AudioParamModulator, ModRule, TypeModConfig,
        AUDIO_SOURCES, MODULATABLE_PARAMS,
        get_global_modulator,
    )
    _HAS_MODULATOR = True
except ImportError:
    _HAS_MODULATOR = False

# ─── 颜色常量（引用主题令牌，不再自建十六进制）───────────────────────────────
BG_MAIN     = _th.BG_ROOT
BG_PANEL    = _th.BG_ROOT
BG_HEADER   = _th.BG_EDITOR
BG_ITEM     = _th.BG_ROOT
BG_ITEM_SEL = _th.BG_BOX
FG_MAIN     = _th.TEXT_PRIMARY
FG_DIM      = _th.TEXT_SECONDARY
FG_ACCENT   = _th.FOCUS_BLUE
FG_GREEN    = _th.BRIGHT_SUCCESS
FG_ORANGE   = _th.BRIGHT_WARN
FG_RED      = _th.BRIGHT_ERROR
ACCENT      = _th.BRIGHT_ERROR

# ─── 样式表（通用外观交给全局主题接管，此处只留语义色特化）───────────────────
# 已删除 _BTN：与全局 QPushButton 外观重复且从未 setCheckable，无 checked/pressed
# 语义需要（§3 结构性统一）。
_BTN_ADD = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_GREEN};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:2px 8px;font-size:13px;font-weight:bold;min-width:28px;}}"
)
_BTN_DEL = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_RED};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:2px 6px;font-size:11px;font-weight:bold;min-width:24px;}}"
)
_BTN_RAND = (
    # 紫罗兰夜底退役→中性面+focus_blue 文字
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_ACCENT};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:3px 8px;font-size:10px;}}"
)
_BTN_ENABLE = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_MAIN};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:4px 14px;font-size:12px;font-weight:bold;}}"
    f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};}}"
    f"QPushButton:checked{{background:#2A6A2A;color:{FG_GREEN};border-color:#3A8A3A;}}"
)
# 已删除 _COMBO/_SPINBOX/_TAB/_TAB_INNER：与全局 QComboBox/QSpinBox/QTabBar 外观
# 完全重复（§3 结构性统一）。
_SLIDER = (
    f"QSlider::groove:horizontal{{height:4px;background:{_th.BG_EDITOR};border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_ACCENT};width:12px;margin:-4px 0;border-radius:6px;}}"
    f"QSlider::sub-page:horizontal{{background:{FG_ACCENT};border-radius:2px;}}"
)
_SLIDER_GREEN = (
    f"QSlider::groove:horizontal{{height:4px;background:{_th.BG_EDITOR};border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_GREEN};width:12px;margin:-4px 0;border-radius:6px;}}"
    f"QSlider::sub-page:horizontal{{background:{FG_GREEN};border-radius:2px;}}"
)
_SLIDER_ORANGE = (
    f"QSlider::groove:horizontal{{height:4px;background:{_th.BG_EDITOR};border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_ORANGE};width:12px;margin:-4px 0;border-radius:6px;}}"
    f"QSlider::sub-page:horizontal{{background:{FG_ORANGE};border-radius:2px;}}"
)
_LOG = (
    f"QPlainTextEdit{{background:{_th.BG_ROOT};color:{FG_ACCENT};border:1px solid {_th.BG_EDITOR};"
    f"border-radius:{_th.RADIUS}px;font-family:Consolas,Menlo,monospace;font-size:11px;}}"
)
# 已删除 _GROUP：QGroupBox 通用外观由全局主题接管（§3 结构性统一）。
_SCROLL = (
    "QScrollArea{border:none;background:transparent;}"
    f"QScrollBar:vertical{{background:{_th.BG_ROOT};width:8px;border-radius:{_th.RADIUS}px;}}"
    f"QScrollBar::handle:vertical{{background:{_th.SCROLLBAR_HANDLE};border-radius:{_th.RADIUS}px;min-height:20px;}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
    f"QScrollBar:horizontal{{background:{_th.BG_ROOT};height:8px;border-radius:{_th.RADIUS}px;}}"
    f"QScrollBar::handle:horizontal{{background:{_th.SCROLLBAR_HANDLE};border-radius:{_th.RADIUS}px;min-width:20px;}}"
    "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;}"
)

_SECTION_LIST = [
    SectionLabel.INTRO, SectionLabel.VERSE, SectionLabel.BUILD,
    SectionLabel.CHORUS, SectionLabel.DROP,
    SectionLabel.BREAKDOWN, SectionLabel.OUTRO,
]
_SECTION_LABELS = {
    SectionLabel.INTRO:     "intro  引入段",
    SectionLabel.VERSE:     "verse  主歌",
    SectionLabel.BUILD:     "build  上升段",
    SectionLabel.CHORUS:    "chorus 副歌",
    SectionLabel.DROP:      "drop   段",
    SectionLabel.BREAKDOWN: "breakdown 中断",
    SectionLabel.OUTRO:     "outro  收尾",
}
_SECTION_COLORS = {
    SectionLabel.INTRO:     "#66AAFF",
    SectionLabel.VERSE:     "#56B65B",
    SectionLabel.BUILD:     "#E6B455",
    SectionLabel.CHORUS:    "#FF88AA",
    SectionLabel.DROP:      "#FF4466",
    SectionLabel.BREAKDOWN: "#AA88FF",
    SectionLabel.OUTRO:     "#88CCFF",
}
_SORT_LIST = ["index", "linear_x", "linear_y", "radial", "grid_row", "grid_col"]

_TYPE_COLORS = {
    "light": "#71A8FF",
    "pump":  "#44DDAA",
    "motor": "#E6B455",
    "valve": "#FF6688",
    "color": "#AA88FF",
}


# ═══════════════════════════════════════════════════════════════════════════
#  乐器电平表
# ═══════════════════════════════════════════════════════════════════════════
class _InstrumentMeter(QWidget):
    _COLORS = {
        "kick": "#FF4466", "snare": "#E6B455", "hihat": "#FFEE66",
        "bass": "#44AAFF", "melody": "#AA66FF", "pad": "#66FF99",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(72)
        self.setMaximumHeight(88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._levels = {k: 0.0 for k in INSTRUMENTS}
        self._section_label = ""
        self._bpm = 120.0

    def update_event(self, ev):
        for k in INSTRUMENTS:
            target = float(ev.instruments.get(k, 0.0))
            cur = self._levels.get(k, 0.0)
            self._levels[k] = max(target, cur * 0.85)
        self._section_label = ev.section_label
        self._bpm = float(ev.bpm)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#1D1D1D"))
        f = QFont(); f.setPointSize(8); p.setFont(f)
        n = len(INSTRUMENTS)
        if n == 0 or w <= 4:
            p.end(); return
        bar_w = (w - 8) / n
        for i, name in enumerate(INSTRUMENTS):
            x = 4 + i * bar_w
            level = max(0.0, min(1.0, self._levels.get(name, 0.0)))
            color = QColor(self._COLORS.get(name, "#999999"))
            color.setAlphaF(0.35 + 0.65 * level)
            bar_h = max(2, int(level * (h - 22)))
            p.fillRect(int(x), h - 16 - bar_h, int(bar_w - 2), bar_h, color)
            p.setPen(QPen(QColor("#999999")))
            p.drawText(int(x), h - 14, int(bar_w - 2), 14,
                       int(Qt.AlignmentFlag.AlignCenter), name)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
#  单个预设槽行
# ═══════════════════════════════════════════════════════════════════════════
class _PresetSlotRow(QWidget):
    remove_requested = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, preset_items: list, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._combo = QComboBox()
        self._combo.setMinimumWidth(150)
        self._combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo.addItem("(空)", "")
        for pid, label in preset_items:
            self._combo.addItem(label, pid)
        lay.addWidget(self._combo, 1)
        btn_del = QPushButton("−")
        btn_del.setStyleSheet(_BTN_DEL)
        btn_del.setFixedSize(26, 26)
        btn_del.setToolTip("删除此效果槽")
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(btn_del)
        self._combo.currentIndexChanged.connect(self.changed)

    @property
    def combo(self):
        return self._combo

    def preset_id(self) -> str:
        return str(self._combo.currentData() or "")

    def set_preset_id(self, pid: str):
        idx = self._combo.findData(pid)
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(max(0, idx))
        self._combo.blockSignals(False)

    def refresh_items(self, preset_items: list):
        cur = self.preset_id()
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem("(空)", "")
        for pid, label in preset_items:
            self._combo.addItem(label, pid)
        idx = self._combo.findData(cur)
        self._combo.setCurrentIndex(max(0, idx))
        self._combo.blockSignals(False)


# ═══════════════════════════════════════════════════════════════════════════
#  单个段落行
# ═══════════════════════════════════════════════════════════════════════════
class _SectionRow(QFrame):
    changed = pyqtSignal()

    def __init__(self, section: str, preset_items: list, parent=None):
        super().__init__(parent)
        self._section = section
        self._preset_items = list(preset_items)
        self._sec_slots: List[_PresetSlotRow] = []
        self._bar_slots: List[_PresetSlotRow] = []
        self._build_ui()

    def _build_ui(self):
        color = _SECTION_COLORS.get(self._section, FG_ACCENT)
        label_text = _SECTION_LABELS.get(self._section, self._section)
        self.setStyleSheet(
            f"QFrame{{background:{BG_PANEL};border-radius:4px;"
            f"border:1px solid #303030;}}")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        # 标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(
            f"color:{color};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        lbl.setMinimumWidth(120)
        title_row.addWidget(lbl)
        self._cb_enabled = QCheckBox("启用")
        self._cb_enabled.setChecked(True)
        self._cb_enabled.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        self._cb_enabled.toggled.connect(self.changed)
        title_row.addWidget(self._cb_enabled)
        title_row.addStretch()
        sort_lbl = QLabel("排序:")
        sort_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        title_row.addWidget(sort_lbl)
        self._cb_sort = QComboBox()
        self._cb_sort.setMinimumWidth(90)
        for s in _SORT_LIST:
            self._cb_sort.addItem(s, s)
        self._cb_sort.currentIndexChanged.connect(self.changed)
        title_row.addWidget(self._cb_sort)
        btn_rand = QPushButton(" 随机配置")
        btn_rand.setIcon(load_tabler_icon("dice", 14, FG_ACCENT))
        btn_rand.setStyleSheet(_BTN_RAND)
        btn_rand.setToolTip("从预设库随机填充所有槽位")
        btn_rand.clicked.connect(self._on_randomize)
        title_row.addWidget(btn_rand)
        root.addLayout(title_row)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#303030;background:#303030;border:none;max-height:1px;")
        root.addWidget(sep)
        # 结构级预设区
        sec_group = QWidget()
        sec_group.setStyleSheet("background:transparent;border:none;")
        sec_vlay = QVBoxLayout(sec_group)
        sec_vlay.setContentsMargins(0, 0, 0, 0)
        sec_vlay.setSpacing(3)
        sec_header = QHBoxLayout()
        sec_header.setSpacing(6)
        sec_hdr_lbl = QLabel("结构级预设")
        sec_hdr_lbl.setStyleSheet(
            f"color:{FG_ACCENT};font-size:10px;font-weight:bold;"
            f"background:transparent;border:none;")
        sec_header.addWidget(sec_hdr_lbl)
        sec_header.addStretch()
        btn_add_sec = QPushButton("+")
        btn_add_sec.setStyleSheet(_BTN_ADD)
        btn_add_sec.setFixedSize(28, 24)
        btn_add_sec.setToolTip("添加一个结构级预设槽")
        btn_add_sec.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_sec.clicked.connect(lambda: self._add_slot("section"))
        sec_header.addWidget(btn_add_sec)
        sec_vlay.addLayout(sec_header)
        self._sec_slots_container = QVBoxLayout()
        self._sec_slots_container.setContentsMargins(0, 0, 0, 0)
        self._sec_slots_container.setSpacing(3)
        sec_vlay.addLayout(self._sec_slots_container)
        root.addWidget(sec_group)
        # 小节级预设区
        bar_group = QWidget()
        bar_group.setStyleSheet("background:transparent;border:none;")
        bar_vlay = QVBoxLayout(bar_group)
        bar_vlay.setContentsMargins(0, 0, 0, 0)
        bar_vlay.setSpacing(3)
        bar_header = QHBoxLayout()
        bar_header.setSpacing(6)
        bar_hdr_lbl = QLabel("小节级预设")
        bar_hdr_lbl.setStyleSheet(
            f"color:{FG_ORANGE};font-size:10px;font-weight:bold;"
            f"background:transparent;border:none;")
        bar_header.addWidget(bar_hdr_lbl)
        bar_header.addStretch()
        btn_add_bar = QPushButton("+")
        btn_add_bar.setStyleSheet(_BTN_ADD)
        btn_add_bar.setFixedSize(28, 24)
        btn_add_bar.setToolTip("添加一个小节级预设槽")
        btn_add_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_bar.clicked.connect(lambda: self._add_slot("bar"))
        bar_header.addWidget(btn_add_bar)
        bar_vlay.addLayout(bar_header)
        self._bar_slots_container = QVBoxLayout()
        self._bar_slots_container.setContentsMargins(0, 0, 0, 0)
        self._bar_slots_container.setSpacing(3)
        bar_vlay.addLayout(self._bar_slots_container)
        root.addWidget(bar_group)
        for _ in range(2):
            self._add_slot("section", emit=False)
        for _ in range(3):
            self._add_slot("bar", emit=False)

    def _add_slot(self, level: str, emit: bool = True) -> _PresetSlotRow:
        row = _PresetSlotRow(self._preset_items)
        row.changed.connect(self.changed)
        if level == "section":
            row.remove_requested.connect(lambda r: self._remove_slot("section", r))
            self._sec_slots.append(row)
            self._sec_slots_container.addWidget(row)
        else:
            row.remove_requested.connect(lambda r: self._remove_slot("bar", r))
            self._bar_slots.append(row)
            self._bar_slots_container.addWidget(row)
        if emit:
            self.changed.emit()
        return row

    def _remove_slot(self, level: str, row: _PresetSlotRow):
        slots = self._sec_slots if level == "section" else self._bar_slots
        container = self._sec_slots_container if level == "section" else self._bar_slots_container
        if len(slots) <= 1:
            return
        if row in slots:
            slots.remove(row)
            container.removeWidget(row)
            row.deleteLater()
            self.changed.emit()

    def _on_randomize(self):
        available = [pid for pid, _ in self._preset_items if pid]
        if not available:
            return
        for row in self._sec_slots:
            row.set_preset_id(random.choice(available))
        for row in self._bar_slots:
            row.set_preset_id(random.choice(available))
        self.changed.emit()

    def refresh_preset_items(self, preset_items: list):
        self._preset_items = list(preset_items)
        for row in self._sec_slots:
            row.refresh_items(preset_items)
        for row in self._bar_slots:
            row.refresh_items(preset_items)

    def get_section_preset_ids(self) -> List[str]:
        return [r.preset_id() for r in self._sec_slots if r.preset_id()]

    def get_bar_preset_ids(self) -> List[str]:
        return [r.preset_id() for r in self._bar_slots if r.preset_id()]

    def get_sort_mode(self) -> str:
        return str(self._cb_sort.currentData() or "index")

    def is_enabled(self) -> bool:
        return self._cb_enabled.isChecked()

    def load_from_preset_set(self, pset: SectionPresetSet):
        self._cb_enabled.blockSignals(True)
        self._cb_enabled.setChecked(pset.enabled)
        self._cb_enabled.blockSignals(False)
        sort_idx = _SORT_LIST.index(pset.sort_mode) if pset.sort_mode in _SORT_LIST else 0
        self._cb_sort.blockSignals(True)
        self._cb_sort.setCurrentIndex(sort_idx)
        self._cb_sort.blockSignals(False)
        self._sync_slots_from_entries("section", pset.section_presets)
        self._sync_slots_from_entries("bar", pset.bar_presets)

    def _sync_slots_from_entries(self, level: str, entries: list):
        slots = self._sec_slots if level == "section" else self._bar_slots
        container = self._sec_slots_container if level == "section" else self._bar_slots_container
        while len(slots) > max(1, len(entries)):
            row = slots.pop()
            container.removeWidget(row)
            row.deleteLater()
        while len(slots) < max(1, len(entries)):
            self._add_slot(level, emit=False)
        for i, entry in enumerate(entries):
            if i < len(slots):
                slots[i].set_preset_id(entry.preset_id)


# ═══════════════════════════════════════════════════════════════════════════
#  单种效果类型的预设配置子面板
# ═══════════════════════════════════════════════════════════════════════════
class _TypePresetPanel(QWidget):
    """一种效果类型（如灯光/泵/电机/阀门）的完整预设配置面板。"""
    changed = pyqtSignal()

    def __init__(self, track_type: str, parent=None, defer_build: bool = False):
        super().__init__(parent)
        self._track_type = track_type
        self._section_rows: Dict[str, _SectionRow] = {}
        self._ui_built = False
        self._pending_preset_items = None   # 延迟构建期间收到的预设项，构建后补用
        self.setStyleSheet(f"background:{BG_MAIN};")
        # defer_build=True：打开音频桥接器时只同步构建首个类型面板，其余 3 个
        # 在面板显示后再建（showEvent 或被直接访问时），把 ~520ms 摊到事件循环外，
        # 不改变任何最终结构与行为。
        if not defer_build:
            self._ensure_built()

    def _ensure_built(self):
        """幂等地构建面板内容。延迟构建的面板在首次 showEvent 或被消费者访问时调用。"""
        if self._ui_built:
            return
        self._ui_built = True
        self._build_ui()
        if self._pending_preset_items is not None:
            try:
                self.refresh_preset_items(self._pending_preset_items)
            except Exception:
                pass
            self._pending_preset_items = None

    def showEvent(self, event):
        # 切到该类型子 Tab 时才真正构建（若之前被延迟）。
        self._ensure_built()
        super().showEvent(event)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL)
        container = QWidget()
        container.setStyleSheet(f"background:{BG_MAIN};")
        self._preset_sets_layout = QVBoxLayout(container)
        self._preset_sets_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_sets_layout.setSpacing(6)
        for section in _SECTION_LIST:
            row = _SectionRow(section, [])
            row.changed.connect(self.changed)
            self._section_rows[section] = row
            self._preset_sets_layout.addWidget(row)
        self._preset_sets_layout.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, 1)

    def refresh_preset_items(self, preset_items: list):
        if not self._ui_built:
            # 尚未构建：记下来，构建后再应用，避免提前触发重型构建。
            self._pending_preset_items = preset_items
            return
        for row in self._section_rows.values():
            row.refresh_preset_items(preset_items)

    def load_from_bridge(self, bridge: MusicToEffectBridge):
        self._ensure_built()
        sets = bridge.get_preset_sets(self._track_type)
        sets_by_section = {s.section: s for s in sets}
        for section, row in self._section_rows.items():
            pset = sets_by_section.get(section)
            if pset:
                row.load_from_preset_set(pset)

    def collect_preset_sets(self) -> List[SectionPresetSet]:
        self._ensure_built()
        tt = self._track_type
        result = []
        for section in _SECTION_LIST:
            row = self._section_rows.get(section)
            if not row:
                continue
            ch_types = list(TRACK_TYPES.get(tt, {}).get("ch_types", ["dimmer"]))
            result.append(SectionPresetSet(
                section=section, track_type=tt,
                section_presets=[PresetPoolEntry(pid) for pid in row.get_section_preset_ids()],
                bar_presets=[PresetPoolEntry(pid) for pid in row.get_bar_preset_ids()],
                ch_types=ch_types, sort_mode=row.get_sort_mode(),
                enabled=row.is_enabled(),
            ))
        return result


# ═══════════════════════════════════════════════════════════════════════════
#  单条调制规则行（仅在有调制器模块时使用）
# ═══════════════════════════════════════════════════════════════════════════
class _ModRuleRow(QFrame):
    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, rule=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{BG_ITEM};border-radius:4px;"
            f"border:1px solid #303030;}}")
        if _HAS_MODULATOR and rule is None:
            rule = ModRule(source="rms", param_key="speed",
                           out_min=0.5, out_max=4.0)
        self._rule = rule
        self._build_ui()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        # 启用
        self._cb_enabled = QCheckBox()
        self._cb_enabled.setChecked(getattr(self._rule, 'enabled', True))
        self._cb_enabled.setStyleSheet("background:transparent;border:none;")
        self._cb_enabled.toggled.connect(self.changed)
        lay.addWidget(self._cb_enabled)

        # 音频源
        self._combo_source = QComboBox()
        self._combo_source.setMinimumWidth(100)
        if _HAS_MODULATOR:
            for key, label in AUDIO_SOURCES.items():
                self._combo_source.addItem(label, key)
            idx = self._combo_source.findData(getattr(self._rule, 'source', 'rms'))
            if idx >= 0:
                self._combo_source.setCurrentIndex(idx)
        self._combo_source.currentIndexChanged.connect(self.changed)
        lay.addWidget(self._combo_source)

        arrow = QLabel("→")
        arrow.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        lay.addWidget(arrow)

        # 目标参数
        self._combo_param = QComboBox()
        self._combo_param.setMinimumWidth(90)
        if _HAS_MODULATOR:
            for key, label in MODULATABLE_PARAMS.items():
                self._combo_param.addItem(label, key)
            idx = self._combo_param.findData(getattr(self._rule, 'param_key', 'speed'))
            if idx >= 0:
                self._combo_param.setCurrentIndex(idx)
        self._combo_param.currentIndexChanged.connect(self.changed)
        lay.addWidget(self._combo_param)

        # 输出范围
        _mk = lambda t: self._mk_label(t)
        lay.addWidget(_mk("范围:"))
        self._spin_min = QDoubleSpinBox()
        self._spin_min.setRange(-1000, 10000)
        self._spin_min.setDecimals(2)
        self._spin_min.setSingleStep(0.1)
        self._spin_min.setValue(getattr(self._rule, 'out_min', 0.0))
        self._spin_min.setFixedWidth(70)
        self._spin_min.valueChanged.connect(self.changed)
        lay.addWidget(self._spin_min)
        lay.addWidget(_mk("~"))
        self._spin_max = QDoubleSpinBox()
        self._spin_max.setRange(-1000, 10000)
        self._spin_max.setDecimals(2)
        self._spin_max.setSingleStep(0.1)
        self._spin_max.setValue(getattr(self._rule, 'out_max', 1.0))
        self._spin_max.setFixedWidth(70)
        self._spin_max.valueChanged.connect(self.changed)
        lay.addWidget(self._spin_max)

        # 平滑
        lay.addWidget(_mk("平滑:"))
        self._sld_smooth = QSlider(Qt.Orientation.Horizontal)
        self._sld_smooth.setRange(0, 100)
        self._sld_smooth.setValue(int(getattr(self._rule, 'smooth', 0.3) * 100))
        self._sld_smooth.setStyleSheet(_SLIDER)
        self._sld_smooth.setFixedWidth(60)
        self._sld_smooth.valueChanged.connect(self.changed)
        lay.addWidget(self._sld_smooth)

        # 曲线
        self._combo_curve = QComboBox()
        self._combo_curve.setFixedWidth(65)
        for c in ["linear", "exp", "log", "step"]:
            self._combo_curve.addItem(c, c)
        idx = self._combo_curve.findData(getattr(self._rule, 'curve', 'linear'))
        if idx >= 0:
            self._combo_curve.setCurrentIndex(idx)
        self._combo_curve.currentIndexChanged.connect(self.changed)
        lay.addWidget(self._combo_curve)

        # 删除
        btn_del = QPushButton("×")
        btn_del.setStyleSheet(_BTN_DEL)
        btn_del.setFixedSize(24, 24)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(btn_del)

    def _mk_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        return lbl

    def to_mod_rule(self):
        if not _HAS_MODULATOR:
            return None
        return ModRule(
            source=str(self._combo_source.currentData() or "rms"),
            param_key=str(self._combo_param.currentData() or "speed"),
            out_min=self._spin_min.value(),
            out_max=self._spin_max.value(),
            smooth=self._sld_smooth.value() / 100.0,
            enabled=self._cb_enabled.isChecked(),
            curve=str(self._combo_curve.currentData() or "linear"),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  单种效果类型的调制配置子面板
# ═══════════════════════════════════════════════════════════════════════════
class _TypeModPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, track_type: str, parent=None):
        super().__init__(parent)
        self._track_type = track_type
        self._rule_rows: List[_ModRuleRow] = []
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # 启用开关 + 添加按钮
        header = QHBoxLayout()
        header.setSpacing(10)
        self._cb_enabled = QCheckBox("启用此类型的自动调制")
        self._cb_enabled.setStyleSheet(
            f"color:{FG_MAIN};font-size:11px;background:transparent;border:none;")
        self._cb_enabled.toggled.connect(self.changed)
        header.addWidget(self._cb_enabled)
        header.addStretch()
        btn_add = QPushButton("+ 添加调制规则")
        btn_add.setStyleSheet(_BTN_ADD)
        btn_add.setToolTip("新增一条 音频源 → 效果参数 的调制规则行")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(lambda: self._add_rule_row())
        header.addWidget(btn_add)
        lay.addLayout(header)

        info = QLabel(
            "每条规则将一个音频源参数（如力度/BPM/乐器）映射到一个效果参数（如速度/相位差/幅度）。"
            "多条规则可同时生效，后面的规则覆盖前面的同名参数。")
        info.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        info.setWordWrap(True)
        lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL)
        container = QWidget()
        container.setStyleSheet(f"background:{BG_MAIN};")
        self._rules_layout = QVBoxLayout(container)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(4)
        self._rules_layout.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, 1)

    def _add_rule_row(self, rule=None):
        row = _ModRuleRow(rule)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_rule_row)
        self._rule_rows.append(row)
        self._rules_layout.insertWidget(self._rules_layout.count() - 1, row)
        self.changed.emit()

    def _remove_rule_row(self, row):
        if row in self._rule_rows:
            self._rule_rows.remove(row)
            self._rules_layout.removeWidget(row)
            row.deleteLater()
            self.changed.emit()

    def load_config(self, config):
        if not _HAS_MODULATOR:
            return
        self._cb_enabled.blockSignals(True)
        self._cb_enabled.setChecked(config.enabled)
        self._cb_enabled.blockSignals(False)
        for row in list(self._rule_rows):
            self._rules_layout.removeWidget(row)
            row.deleteLater()
        self._rule_rows.clear()
        for rule in config.rules:
            self._add_rule_row(rule)

    def collect_config(self):
        if not _HAS_MODULATOR:
            return None
        return TypeModConfig(
            track_type=self._track_type,
            enabled=self._cb_enabled.isChecked(),
            rules=[r.to_mod_rule() for r in self._rule_rows if r.to_mod_rule()],
        )


# ═══════════════════════════════════════════════════════════════════════════
#  主面板
# ═══════════════════════════════════════════════════════════════════════════
class AudioBridgePanel(QWidget):
    """v24 重构版音乐桥接器面板 — 多效果类型并行 + 自动调制。"""

    # ★ v24: 信号增加 track_type
    effect_apply_request = pyqtSignal(str, str, dict, list, str)
    # (track_type, preset_id, params, ch_types, sort_mode)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG_MAIN};color:{FG_MAIN};")
        self._msa = get_global_structure_analyzer()
        self._bridge = get_global_bridge()
        self._modulator = get_global_modulator() if _HAS_MODULATOR else None

        self._bridge.effect_apply_request.connect(self._on_bridge_apply)
        self._bridge.status_text.connect(self._on_status)
        self._bridge.current_preset_changed.connect(self._on_current_preset)
        self._bridge.mapping_triggered.connect(self._on_mapping_triggered)
        self._msa.analysis_tick.connect(self._on_tick)
        self._msa.structure_event.connect(self._on_structure_event_ui)

        self._tick_throttle = 0
        self._t0 = time.monotonic()
        # ── 设备组过滤 ──
        self._effect_browser = None          # 由 MainWindow 注入
        self._project_manager = None         # 由 MainWindow 注入
        # ── 效果速度：手调优先 ──
        self._effect_speed_manual: bool = False   # True = 用户手动拖过滑块，锁定手调
        self._pending_effect_speed: float = 1.0   # 等待 EB 注入后补发的速度值
        self._bpm_speed_throttle: int = 0         # BPM 自动速度节流计数器
        self._selected_group_id: Optional[str] = None  # 当前选中的组 ID（兼容旧接口）
        # ── 多设备组输出 ──
        self._output_group_ids: List[str] = []    # 当前激活的输出设备组 ID 列表（空=全部）
        self._output_group_widgets: List[QFrame] = []  # 每个设备组的 UI 行控件
        self._type_preset_panels: Dict[str, _TypePresetPanel] = {}
        self._type_enable_cbs: Dict[str, QCheckBox] = {}
        self._type_mod_panels: Dict[str, _TypeModPanel] = {}
        self._type_current_labels: Dict[str, QLabel] = {}
        self._settings_ready: bool = False
        self._heavy_tabs_built: bool = False
        self._settings_loaded: bool = False
        self._preset_items_cache = None
        self._preset_refresh_pending: bool = False
        self._preset_refresh_force: bool = False
        self._preset_refresh_generation: int = 0
        self._meter = None
        self._lbl_energy2 = None

        self._build_ui()
        # 只做轻量收尾。其它重型 Tab 保持占位，用户切换过去时再构建，
        # 避免打开音频桥接器后 80~400ms 内连续抢占 UI 主线程。
        QTimer.singleShot(0, self._finish_deferred_init)

    # ─────────────────────────────────────────────────────────────────────
    #  UI 构建
    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ★ 顶部状态栏——同步构建，小控件无性能压力
        root.addWidget(self._build_header())

        self._curtain_btn = QPushButton("▼ 展开音频桥接内容")
        self._curtain_btn.setCheckable(True)
        self._curtain_btn.setChecked(True)
        self._curtain_btn.setFixedHeight(22)
        self._curtain_btn.setStyleSheet(
            "QPushButton{background:#1D1D1D;color:#71A8FF;border:1px solid #3D3D3D;"
            "border-radius:4px;text-align:left;padding-left:8px;font-size:11px;}"
            "QPushButton:hover{background:#2D2D2D;}"
        )
        self._curtain_btn.toggled.connect(self._toggle_curtain_content)
        root.addWidget(self._curtain_btn, 0)

        self._curtain_body = QWidget()
        curtain_lay = QVBoxLayout(self._curtain_body)
        curtain_lay.setContentsMargins(0, 0, 0, 0)
        curtain_lay.setSpacing(6)

        # ★ 主 Tab——同步构建「效果类型管理」主 Tab，用户打开就看到完整内容
        self._tabs = QTabWidget()
        # ★ 立即构建主 Tab（最常用的效果类型管理），不留空白占位符
        self._tabs.addTab(self._build_effect_types_tab(), "效果类型管理")
        # 其余重型 Tab 先用占位符占位，分帧填充
        self._tabs.addTab(self._mk_placeholder_tab("效果库管理"), "效果库管理")
        self._tabs.addTab(self._mk_placeholder_tab("识别参数调节"), "识别参数调节")
        if _HAS_MODULATOR:
            self._tabs.addTab(self._mk_placeholder_tab("自动调制"), "自动调制")
        # “实体摇杆绑定”不再作为程序主界面入口显示；底层代码保留，避免破坏历史配置或内部调用。
        self._tabs.addTab(self._mk_placeholder_tab("乐器电平"), "乐器电平")
        # 切换 Tab 时按需构建
        self._tabs.currentChanged.connect(self._on_tab_switched)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.setHandleWidth(6)
        content_splitter.addWidget(self._tabs)
        log_panel = self._build_log_box()
        log_panel.setMinimumWidth(220)
        log_panel.setMaximumWidth(360)
        content_splitter.addWidget(log_panel)
        content_splitter.setStretchFactor(0, 4)
        content_splitter.setStretchFactor(1, 1)
        content_splitter.setSizes([760, 260])
        curtain_lay.addWidget(content_splitter, 1)

        # 自由缩放视口：面板缩小时不再隐藏功能，而是上下滑动浏览。
        self._curtain_scroll = QScrollArea()
        self._curtain_scroll.setWidgetResizable(True)
        self._curtain_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._curtain_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._curtain_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._curtain_scroll.setStyleSheet(_SCROLL)
        self._curtain_scroll.setMinimumSize(0, 0)
        self._curtain_body.setMinimumSize(0, 0)
        self._tabs.setMinimumSize(0, 0)
        self._curtain_scroll.setWidget(self._curtain_body)
        root.addWidget(self._curtain_scroll, 1)

        # 加载设置（不需要等待 UI）
        try:
            self._load_settings()
            self._settings_loaded = True
        except Exception:
            self._settings_loaded = False
        self._heavy_tabs_built = True

    def _toggle_curtain_content(self, checked: bool):
        target = getattr(self, "_curtain_scroll", None) or getattr(self, "_curtain_body", None)
        if target is not None:
            target.setVisible(bool(checked))
        if hasattr(self, "_curtain_btn"):
            self._curtain_btn.setText("展开音频桥接内容" if checked else "音频桥接内容已折叠")

    @staticmethod
    def _mk_placeholder_tab(name: str) -> QWidget:
        """轻量占位符，显示载入提示而不是空白。"""
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        w._is_placeholder = True   # ★ 标记为占位符，供 _on_tab_switched 检测
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"正在加载 {name}…")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:12px;padding:24px;background:transparent;")
        lay.addWidget(lbl)
        return w

    def _on_tab_switched(self, idx: int):
        """Tab 切换时按需构建重型内容。"""
        tab_title = self._tabs.tabText(idx)
        # 判断是否为占位符（内容区只有一个 QLabel）
        w = self._tabs.widget(idx)
        if w is None:
            return
        # 如果已是占位符，就异步构建真实内容
        if getattr(w, "_is_placeholder", False):
            QTimer.singleShot(30, lambda: self._build_tab_by_title(idx, tab_title))

    def _build_tab_by_title(self, idx: int, title: str):
        """根据 Tab 标题构建对应内容并替换占位符。"""
        w = self._tabs.widget(idx)
        # 防止重复构建
        if w is not None and not getattr(w, "_is_placeholder", False):
            return
        new_w = None
        try:
            if title == "效果库管理":
                from .effect_type_manager import EffectTypeManagerWidget
                self._effect_mgr = EffectTypeManagerWidget()
                self._effect_mgr.config_applied.connect(self._refresh_all_preset_ui)
                new_w = self._effect_mgr
            elif title == "识别参数调节":
                new_w = self._build_analysis_params_tab()
            elif title == "自动调制":
                if _HAS_MODULATOR:
                    new_w = self._build_modulation_tab()
            elif title == "乐器电平":
                new_w = self._build_meters_tab()
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_w = QWidget()
            err_w.setStyleSheet(f"background:{BG_MAIN};")
            lay = QVBoxLayout(err_w)
            lbl = QLabel(f"加载失败: {e}")
            lbl.setStyleSheet(
                f"color:{FG_RED};font-size:11px;background:transparent;border:none;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            lay.addStretch()
            new_w = err_w
        if new_w is not None:
            self._tabs.removeTab(idx)
            self._tabs.insertTab(idx, new_w, title)
            self._tabs.setCurrentIndex(idx)
        if title == "效果库管理":
            self._refresh_all_preset_ui()
        if _HAS_MODULATOR and title == "自动调制":
            self._load_modulator_ui()
        self._settings_ready = True

    # ── 分帧构建（在后台预热各 Tab，用户未切换时也会预建好）──────────────
    def _build_deferred_tab_2(self):
        """Tab 2: 效果库管理——在后台预建。"""
        if self._tabs.count() < 2:
            return
        w = self._tabs.widget(1)
        if w is not None and not getattr(w, "_is_placeholder", False):
            return
        try:
            from .effect_type_manager import EffectTypeManagerWidget
            self._effect_mgr = EffectTypeManagerWidget()
            self._effect_mgr.config_applied.connect(self._refresh_all_preset_ui)
            self._tabs.removeTab(1)
            self._tabs.insertTab(1, self._effect_mgr, "效果库管理")
        except Exception:
            pass

    def _build_deferred_tab_3(self):
        """Tab 3: 识别参数调节——在后台预建。"""
        idx = 2
        if self._tabs.count() <= idx:
            return
        w = self._tabs.widget(idx)
        if w is not None and not getattr(w, "_is_placeholder", False):
            return
        try:
            new_w = self._build_analysis_params_tab()
            self._tabs.removeTab(idx)
            self._tabs.insertTab(idx, new_w, "识别参数调节")
        except Exception:
            pass

    def _build_deferred_tab_4(self):
        """实体摇杆绑定入口已从程序主界面隐藏，不再后台预建。"""
        return

    def _build_deferred_tab_5(self):
        """Tab 5: 乐器电平——在后台预建。"""
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "乐器电平":
                w = self._tabs.widget(i)
                if w is not None and not getattr(w, "_is_placeholder", False):
                    return
                try:
                    new_w = self._build_meters_tab()
                    self._tabs.removeTab(i)
                    self._tabs.insertTab(i, new_w, "乐器电平")
                except Exception:
                    pass
                return

    def _finish_deferred_init(self):
        """最终收尾：不再后台预建重型 Tab，只标记面板可持久化。"""
        if _HAS_MODULATOR and getattr(self, "_bridge_mod_panel", None):
            self._load_modulator_ui()
        self._settings_ready = True

    # ── 顶部状态栏 ──────────────────────────────────────────────────────
    def _build_header(self):
        box = QFrame()
        box.setStyleSheet(
            f"background:{BG_HEADER};border-radius:4px;border:1px solid #303030;")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)

        self._enable_btn = QPushButton("启用桥接")
        self._enable_btn.setCheckable(True)
        self._enable_btn.setStyleSheet(_BTN_ENABLE)
        self._enable_btn.toggled.connect(self._on_enable_toggled)
        lay.addWidget(self._enable_btn)
        lay.addWidget(self._mk_vsep())

        # ── 多设备组输出管理区 ──
        grp_frame = QFrame()
        grp_frame.setStyleSheet(
            f"background:#181818;border-radius:4px;border:1px solid #303030;")
        grp_outer = QHBoxLayout(grp_frame)
        grp_outer.setContentsMargins(6, 4, 6, 4)
        grp_outer.setSpacing(4)

        lbl_grp = QLabel("输出设备组:")
        lbl_grp.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        grp_outer.addWidget(lbl_grp)

        # 设备组列表容器（水平排列，每个设备组一个小标签+删除按钮）
        self._grp_list_frame = QFrame()
        self._grp_list_frame.setStyleSheet("background:transparent;border:none;")
        self._grp_list_lay = QHBoxLayout(self._grp_list_frame)
        self._grp_list_lay.setContentsMargins(0, 0, 0, 0)
        self._grp_list_lay.setSpacing(4)
        self._lbl_no_group = QLabel("全部设备")
        self._lbl_no_group.setStyleSheet(
            f"color:{FG_GREEN};font-size:10px;background:transparent;border:none;")
        self._grp_list_lay.addWidget(self._lbl_no_group)
        grp_outer.addWidget(self._grp_list_frame)

        # 添加设备组按钮
        self._btn_add_group = QPushButton("+")
        self._btn_add_group.setStyleSheet(_BTN_ADD)
        self._btn_add_group.setFixedSize(24, 22)
        self._btn_add_group.setToolTip("添加输出设备组")
        self._btn_add_group.clicked.connect(self._on_add_output_group)
        grp_outer.addWidget(self._btn_add_group)

        lay.addWidget(grp_frame)
        lay.addWidget(self._mk_vsep())

        for icon, attr, color, width, tip in [
            ("段落", "_lbl_section", FG_GREEN,  90, "当前音乐段落"),
            ("BPM",  "_lbl_bpm",     FG_ACCENT, 60, "当前节拍速度"),
            ("小节", "_lbl_bar",     FG_ORANGE, 50, "当前小节编号"),
        ]:
            lbl_key = QLabel(f"{icon}:")
            lbl_key.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
            lay.addWidget(lbl_key)
            lbl_val = QLabel("--")
            lbl_val.setStyleSheet(
                f"color:{color};font-size:12px;font-weight:bold;"
                f"background:transparent;border:none;")
            lbl_val.setMinimumWidth(width)
            lbl_val.setToolTip(tip)
            setattr(self, attr, lbl_val)
            lay.addWidget(lbl_val)
            lay.addWidget(self._mk_vsep())

        self._lbl_energy = QLabel("能量: 0.00")
        self._lbl_energy.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        lay.addWidget(self._lbl_energy)
        lay.addStretch()

        # 各效果类型当前预设状态
        for tt, info in TRACK_TYPES.items():
            if tt == "color":
                continue
            color = _TYPE_COLORS.get(tt, FG_ACCENT)
            lbl = QLabel(f"[{info['icon']}]--")
            lbl.setStyleSheet(
                f"color:{color};font-size:10px;background:transparent;border:none;")
            lbl.setMinimumWidth(80)
            lbl.setToolTip(f"{info['label']}当前预设")
            self._type_current_labels[tt] = lbl
            lay.addWidget(lbl)

        lay.addWidget(self._mk_vsep())
        self._btn_test_drop = QPushButton(" 测试 Drop")
        self._btn_test_drop.setIcon(load_tabler_icon("bolt", 14, FG_ACCENT))
        self._btn_test_drop.clicked.connect(lambda: self._fire_test("drop"))
        lay.addWidget(self._btn_test_drop)
        self._btn_reset = QPushButton("↺ 重置")
        self._btn_reset.clicked.connect(self._on_reset)
        lay.addWidget(self._btn_reset)
        return box

    # ── Tab1: 效果类型管理 ──────────────────────────────────────────────
    def _build_effect_types_tab(self):
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 全局控制栏
        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:4px;border:1px solid #303030;")
        ctrl_lay = QHBoxLayout(ctrl_frame)
        ctrl_lay.setContentsMargins(12, 8, 12, 8)
        ctrl_lay.setSpacing(14)

        ctrl_lay.addWidget(self._mk_label("切换速度:"))
        self._sld_speed = QSlider(Qt.Orientation.Horizontal)
        self._sld_speed.setRange(5, 1600)
        self._sld_speed.setValue(100)
        self._sld_speed.setStyleSheet(_SLIDER)
        self._sld_speed.setMinimumWidth(100)
        self._sld_speed.setMaximumWidth(160)
        self._sld_speed.valueChanged.connect(self._on_speed_changed)
        ctrl_lay.addWidget(self._sld_speed)
        self._lbl_speed = QLabel("1.0x")
        self._lbl_speed.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;background:transparent;border:none;")
        self._lbl_speed.setMinimumWidth(40)
        ctrl_lay.addWidget(self._lbl_speed)
        ctrl_lay.addWidget(self._mk_vsep())
        # ── 效果动画速度倍率（全局减慢/加快效果播放速度）──
        ctrl_lay.addWidget(self._mk_label("效果速度:"))
        self._sld_effect_speed = QSlider(Qt.Orientation.Horizontal)
        self._sld_effect_speed.setRange(5, 400)   # 0.05x ~ 4.0x
        self._sld_effect_speed.setValue(100)       # 默认 1.0x
        self._sld_effect_speed.setStyleSheet(_SLIDER)
        self._sld_effect_speed.setMinimumWidth(80)
        self._sld_effect_speed.setMaximumWidth(140)
        self._sld_effect_speed.sliderPressed.connect(self._on_effect_speed_manual_start)
        self._sld_effect_speed.valueChanged.connect(self._on_effect_speed_changed)
        ctrl_lay.addWidget(self._sld_effect_speed)
        self._lbl_effect_speed = QLabel("1.00x")
        self._lbl_effect_speed.setStyleSheet(
            f"color:#56B65B;font-size:11px;background:transparent;border:none;")
        self._lbl_effect_speed.setMinimumWidth(44)
        ctrl_lay.addWidget(self._lbl_effect_speed)
        # ── 自动BPM速度重置按鈕 ──
        self._btn_auto_speed = QPushButton("自动")
        self._btn_auto_speed.setCheckable(True)
        self._btn_auto_speed.setChecked(False)   # 默认手调模式
        self._btn_auto_speed.setFixedSize(42, 22)
        self._btn_auto_speed.setToolTip("开启后按 BPM 自动驱动效果速度；手动拖滑块会自动切回手调。")
        self._btn_auto_speed.setStyleSheet(
            "QPushButton{background:#1D1D1D;color:#56B65B;border:1px solid #3D3D3D;"
            "border-radius:4px;font-size:10px;}"
            "QPushButton:checked{background:#188625;color:#FFFFFF;border-color:#56B65B;}"
            "QPushButton:hover{background:#656565;}")
        self._btn_auto_speed.clicked.connect(self._on_auto_speed_toggled)
        ctrl_lay.addWidget(self._btn_auto_speed)

        ctrl_lay.addWidget(self._mk_label("乐句长度:"))
        self._combo_phrase = QComboBox()
        self._combo_phrase.setMinimumWidth(130)
        for beats, label in (
            (4,  "1 小节 (4 beats)"),
            (8,  "2 小节 (8 beats)"),
            (16, "4 小节 (16 beats)"),
            (32, "8 小节 (32 beats)"),
        ):
            self._combo_phrase.addItem(label, beats)
        idx = self._combo_phrase.findData(16)
        if idx >= 0:
            self._combo_phrase.setCurrentIndex(idx)
            self._msa.set_beats_per_bar(16)
        self._combo_phrase.currentIndexChanged.connect(self._on_phrase_length_changed)
        ctrl_lay.addWidget(self._combo_phrase)
        ctrl_lay.addStretch()
        lay.addWidget(ctrl_frame)

        # ── 一键配置工具栏 ──────────────────────────────────────────────────
        onekey_frame = QFrame()
        onekey_frame.setStyleSheet(
            "QFrame{background:#181818;border-radius:4px;border:1px solid #2D2D2D;}")
        onekey_lay = QHBoxLayout(onekey_frame)
        onekey_lay.setContentsMargins(12, 8, 12, 8)
        onekey_lay.setSpacing(10)

        # 标题
        ok_title = QLabel("一键配置")
        ok_title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        onekey_lay.addWidget(ok_title)
        onekey_lay.addWidget(self._mk_vsep())

        # 一键随机全部
        btn_rand_all = QPushButton(" 随机全部四类型")
        btn_rand_all.setIcon(load_tabler_icon("dice", 14, "#FFFFFF"))
        btn_rand_all.setStyleSheet(
            # 一键主行动：select_blue 实心档（紫罗兰夜底退役）
            f"QPushButton{{background:{_th.SELECT_BLUE};color:#FFFFFF;border:1px solid #5480C5;"
            f"border-radius:{_th.RADIUS}px;padding:5px 14px;font-size:12px;font-weight:bold;}}"
            "QPushButton:hover{background:#5480C5;}"
            "QPushButton:pressed{background:#35507C;}")
        btn_rand_all.setToolTip(
            "从灯光/水泵/电机/电磁阀各自的分类效果中随机配置所有段落")
        btn_rand_all.clicked.connect(self._on_onekey_random_all)
        onekey_lay.addWidget(btn_rand_all)

        # 每个类型的效果数量设置
        onekey_lay.addWidget(self._mk_vsep())
        for tt, label, color in [  # 图例色与 _TYPE_COLORS 对齐，四类型全页一致
            ("light",  "灯", "#71A8FF"),   # 原 #FFDD88 → focus_blue（跟模块 light 族）
            ("pump",   "泵", "#44DDAA"),   # 原 #88DDFF
            ("motor",  "机", "#E6B455"),   # 原 #56B65B → bright_warn（跟模块 motor 族）
            ("valve",  "阀", "#FF6688"),   # 原 #FF8888 → 与 _TYPE_COLORS.valve 同源
        ]:
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet(
                f"color:{color};font-size:11px;font-weight:bold;"
                f"background:transparent;border:none;")
            onekey_lay.addWidget(lbl)
            spin = QSpinBox()
            spin.setRange(1, 8)
            spin.setValue(2)
            spin.setFixedWidth(48)
            spin.setToolTip(f"每个段落为{label}光随机选取的效果数量")
            if not hasattr(self, '_onekey_spins'):
                self._onekey_spins = {}
            self._onekey_spins[tt] = spin
            onekey_lay.addWidget(spin)

        onekey_lay.addWidget(self._mk_vsep())

        # 保存配置
        btn_save_cfg = QPushButton(" 保存配置")
        btn_save_cfg.setIcon(load_tabler_icon("device-floppy", 14, FG_GREEN))
        btn_save_cfg.setStyleSheet(
            f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_GREEN};border:1px solid {_th.BORDER_NORMAL};"
            f"border-radius:{_th.RADIUS}px;padding:5px 12px;font-size:11px;}}"
        )
        btn_save_cfg.setToolTip("将当前所有效果配置保存到JSON文件")
        btn_save_cfg.clicked.connect(self._on_save_config_file)
        onekey_lay.addWidget(btn_save_cfg)

        # 打开配置
        btn_load_cfg = QPushButton(" 打开配置")
        btn_load_cfg.setIcon(load_tabler_icon("folder-open", 14, FG_ORANGE))
        btn_load_cfg.setStyleSheet(
            f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_ORANGE};border:1px solid {_th.BORDER_NORMAL};"
            f"border-radius:{_th.RADIUS}px;padding:5px 12px;font-size:11px;}}"
        )
        btn_load_cfg.setToolTip("从JSON文件加载效果配置")
        btn_load_cfg.clicked.connect(self._on_load_config_file)
        onekey_lay.addWidget(btn_load_cfg)

        onekey_lay.addStretch()
        lay.addWidget(onekey_frame)

        # 效果类型子Tab
        self._type_tabs = QTabWidget()

        _built_one = False
        for tt, info in TRACK_TYPES.items():
            if tt == "color":
                continue
            tab_w = QWidget()
            tab_w.setStyleSheet(f"background:{BG_MAIN};")
            tab_lay = QVBoxLayout(tab_w)
            tab_lay.setContentsMargins(6, 6, 6, 6)
            tab_lay.setSpacing(6)

            # 效果类型启用开关 + 测试按钮
            enable_row = QHBoxLayout()
            enable_row.setSpacing(10)
            cb = QCheckBox(f"启用 {info['label']} 效果输出")
            cb.setChecked(self._bridge.is_type_active(tt))
            cb.setStyleSheet(
                f"color:{_TYPE_COLORS.get(tt, FG_ACCENT)};font-size:12px;"
                f"font-weight:bold;background:transparent;border:none;")
            cb.toggled.connect(
                lambda checked, t=tt: self._on_type_enable_toggled(t, checked))
            self._type_enable_cbs[tt] = cb
            enable_row.addWidget(cb)
            enable_row.addStretch()
            for section in [SectionLabel.INTRO, SectionLabel.CHORUS, SectionLabel.DROP]:
                btn = QPushButton(f"测试 {section}")
                btn.setFixedHeight(24)
                btn.setToolTip(f"手动触发一次 {section} 段落事件，预览该段效果配置")
                btn.clicked.connect(
                    lambda checked, s=section: self._fire_test(s))
                enable_row.addWidget(btn)
            tab_lay.addLayout(enable_row)

            # 预设配置面板：只同步构建首个可见类型，其余延迟到该子 Tab 首次显示时
            # （showEvent）或被消费者访问时构建（_ensure_built），把 ~520ms 摊出去。
            panel = _TypePresetPanel(tt, defer_build=_built_one)
            _built_one = True
            panel.changed.connect(lambda t=tt: self._sync_type_to_bridge(t))
            self._type_preset_panels[tt] = panel
            tab_lay.addWidget(panel, 1)

            icon = info.get("icon", "?")
            self._type_tabs.addTab(tab_w, f"[{icon}] {info['label']}")

        lay.addWidget(self._type_tabs, 1)
        # 预设列表可能触发 EffectEngine 懒加载并刷新大量下拉框，延后并分批执行。
        self._request_preset_ui_refresh(delay_ms=220)
        return w

    # ── Tab2: 识别参数调节 ──────────────────────────────────────────────
    def _build_analysis_params_tab(self):
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        info_lbl = QLabel(
            "调节音频识别算法的各项参数，以适配不同风格的音乐。"
            "参数变化实时生效，无需重启。")
        info_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        info_lbl.setWordWrap(True)
        lay.addWidget(info_lbl)

        grid_frame = QFrame()
        grid_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:4px;border:1px solid #303030;")
        grid = QGridLayout(grid_frame)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(12)

        params = [
            ("段落灵敏度",
             "控制段落切换的灵敏度。值越大越容易切换。推荐：0.3~1.0",
             5, 200, 55, "", _SLIDER_GREEN, "_sld_section",
             lambda v: self._msa.set_sensitivity(section=v / 100.0)),
            ("Drop 灵敏度",
             "控制 Drop 事件的触发灵敏度。推荐：20~60",
             10, 80, 40, "", _SLIDER_ORANGE, "_sld_drop",
             self._on_drop_sens_changed),
            ("乐器灵敏度",
             "控制乐器检测灵敏度。推荐：0.5~2.0",
             10, 400, 100, "", _SLIDER, "_sld_instrument",
             lambda v: self._msa.set_sensitivity(instrument=v / 100.0)),
            ("噪声底限",
             "低于此值视为静音。推荐：0.01~0.10",
             0, 30, 2, "%", _SLIDER, "_sld_noise_floor",
             lambda v: self._msa.set_sensitivity(noise_floor=v / 100.0)),
            ("段落确认时长",
             "候选段落需持续此时长才确认切换。推荐：0.5~3.0秒",
             10, 500, 120, "×0.01s", _SLIDER_GREEN, "_sld_section_hold",
             lambda v: self._msa.set_sensitivity(section_hold=v / 100.0)),
            ("Drop 冷却时间",
             "两次 Drop 之间的最小间隔。推荐：2~8秒",
             10, 120, 40, "×0.1s", _SLIDER_ORANGE, "_sld_drop_cooldown",
             self._on_drop_cooldown_changed),
        ]

        for row_idx, (label, tip, lo, hi, default, unit, style, attr, cb) in enumerate(params):
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color:{FG_MAIN};font-size:11px;background:transparent;border:none;")
            lbl.setToolTip(tip)
            lbl.setMinimumWidth(90)
            grid.addWidget(lbl, row_idx, 0)
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setRange(lo, hi)
            sld.setValue(default)
            sld.setStyleSheet(style)
            sld.setMinimumWidth(180)
            sld.setToolTip(tip)
            sld.valueChanged.connect(cb)
            setattr(self, attr, sld)
            grid.addWidget(sld, row_idx, 1)
            val_lbl = QLabel(f"{default}{unit}")
            val_lbl.setStyleSheet(
                f"color:{FG_ACCENT};font-size:11px;min-width:60px;"
                f"background:transparent;border:none;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            setattr(self, attr + "_lbl", val_lbl)
            grid.addWidget(val_lbl, row_idx, 2)
            tip_lbl = QLabel(tip.split("\n")[0].split("。")[0])
            tip_lbl.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
            grid.addWidget(tip_lbl, row_idx, 3)
            sld.valueChanged.connect(
                lambda v, lbl=val_lbl, u=unit: lbl.setText(f"{v}{u}"))

        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)
        lay.addWidget(grid_frame)

        # 风格快捷预设
        preset_frame = QFrame()
        preset_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:4px;border:1px solid #303030;")
        preset_lay = QVBoxLayout(preset_frame)
        preset_lay.setContentsMargins(12, 10, 12, 10)
        preset_lay.setSpacing(8)
        preset_title = QLabel("风格快捷预设")
        preset_title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;")
        preset_lay.addWidget(preset_title)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        style_presets = [
            ("电子/EDM",    dict(section=0.7, instrument=1.2, drop=3.0, noise_floor=0.02, section_hold=1.0)),
            ("流行/Pop",    dict(section=0.5, instrument=1.0, drop=4.0, noise_floor=0.03, section_hold=1.5)),
            ("摇滚/Rock",   dict(section=0.6, instrument=1.5, drop=2.5, noise_floor=0.03, section_hold=1.2)),
            ("古典/Classical", dict(section=0.35, instrument=0.8, drop=6.0, noise_floor=0.01, section_hold=2.5)),
            ("嘈杂环境",   dict(section=0.55, instrument=1.0, drop=3.5, noise_floor=0.08, section_hold=1.5)),
        ]
        for name, params_dict in style_presets:
            btn = QPushButton(name)
            btn.clicked.connect(
                lambda checked, pd=params_dict: self._apply_style_preset(pd))
            btn_row.addWidget(btn)
        btn_row.addStretch()
        preset_lay.addLayout(btn_row)
        lay.addWidget(preset_frame)
        lay.addStretch()
        return w

    # ── Tab3: 自动调制 (v2 滑块式) ─────────────────────────────────────
    def _build_modulation_tab(self):
        """构建自动调制 Tab（新版：BridgeModulationPanel）"""
        try:
            from .bridge_modulation_panel import BridgeModulationPanel
            self._bridge_mod_panel = BridgeModulationPanel()
            # 叠加参数更新 → 注入到 EffectBrowser
            self._bridge_mod_panel.overlay_params_updated.connect(
                self._on_overlay_params_updated)
            # 识别参数变更 → 同步到 MusicStructureAnalyzer
            self._bridge_mod_panel.recognition_params_changed.connect(
                self._on_recognition_params_changed)
            # 将 analysis_tick 信号转发给调制面板
            self._msa.analysis_tick.connect(
                self._bridge_mod_panel.on_analysis_tick)
            return self._bridge_mod_panel
        except Exception as e:
            import traceback
            traceback.print_exc()
            w = QWidget()
            w.setStyleSheet(f"background:{BG_MAIN};")
            lay = QVBoxLayout(w)
            lbl = QLabel(f"自动调制模块加载失败: {e}")
            lbl.setStyleSheet(
                f"color:{FG_RED};font-size:11px;background:transparent;border:none;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            lay.addStretch()
            return w
    # ── Tab: 摇杆绑定 ───────────────────────────────────────────────────
    def _build_joystick_binding_tab(self):
        """构建摇杆绑定 Tab（新版：BridgeJoystickPanel）"""
        try:
            from .bridge_joystick_panel import BridgeJoystickPanel
            self._joy_panel = BridgeJoystickPanel()
            # 叠加参数更新 → 注入到 EffectBrowser
            self._joy_panel.overlay_params_updated.connect(
                self._on_overlay_params_updated)
            # 连接实体摇杆驱动器（如果已初始化）
            try:
                from ..physical_joystick_driver import get_physical_joystick_driver  # src/ui/physical_joystick_driver.py
                phys_joy = get_physical_joystick_driver()
                phys_joy.axis_changed.connect(self._joy_panel.inject_external_joystick)
            except Exception:
                pass  # 实体摇杆未安装或未初始化，忽略
            # 加载已保存的配置
            self._load_joystick_binder_settings()
            return self._joy_panel
        except Exception as e:
            import traceback
            traceback.print_exc()
            w = QWidget()
            w.setStyleSheet(f"background:{BG_MAIN};")
            lay = QVBoxLayout(w)
            lay.setContentsMargins(12, 12, 12, 12)
            lbl = QLabel(f"摇杆绑定模块加载失败: {e}")
            lbl.setStyleSheet(
                f"color:{FG_RED};font-size:11px;background:transparent;border:none;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            lay.addStretch()
            return w

    # ── 叠加参数注入（自动调制 + 摇杆绑定共用）────────────────────────────
    def _on_overlay_params_updated(self, overlays: dict):
        """
        接收叠加参数字典，在当前效果基础参数上做增量叠加后注入到 EffectBrowser。

        叠加参数字典格式：
          {
            "speed_delta":      float,   # 叠加速度（相对量，±）
            "amplitude_delta":  float,   # 动作幅度叠加量（±）
            "phase_step_delta": float,   # 密度（相位差）叠加量（±）
          }

        叠加逻辑：
          - 读取 EffectBrowser 当前效果的基础参数（_get_current_base_params）
          - 在基础参数上加上 delta，约束在合理范围内
          - 通过 update_param_overrides 写回
        """
        eb = self._effect_browser or self._get_effect_browser()
        if eb is None:
            return

        speed_delta      = float(overlays.get("speed_delta", 0.0))
        amplitude_delta  = float(overlays.get("amplitude_delta", 0.0))
        phase_step_delta = float(overlays.get("phase_step_delta", 0.0))
        wings_delta      = float(overlays.get("wings_delta", 0.0))
        block_delta      = float(overlays.get("block_size_delta", 0.0))

        # 读取当前效果基础参数
        base_params = {}
        if hasattr(eb, '_get_current_params'):
            try:
                base_params = eb._get_current_params() or {}
            except Exception:
                base_params = {}
        elif hasattr(eb, '_param_overrides'):
            base_params = dict(eb._param_overrides or {})

        # 叠加：在基础参数上增量
        base_speed      = float(base_params.get("speed", 1.0))
        base_amplitude  = float(base_params.get("amplitude", 1.0))
        base_phase_step = float(base_params.get("phase_step", 0.1))
        base_wings      = int(round(float(base_params.get("wings", 1))))
        base_block_size = int(round(float(base_params.get("block_size", 1))))

        new_speed      = max(0.05, min(8.0,  base_speed      + speed_delta))
        new_amplitude  = max(0.0,  min(2.0,  base_amplitude  + amplitude_delta))
        new_phase_step = max(0.0,  min(1.0,  base_phase_step + phase_step_delta))
        new_wings      = max(1,    min(32,   int(round(base_wings + wings_delta))))
        new_block_size = max(1,    min(64,   int(round(base_block_size + block_delta))))

        overlay_result = {
            "speed":      new_speed,
            "amplitude":  new_amplitude,
            "phase_step": new_phase_step,
            "wings":      new_wings,
            "block_size": new_block_size,
        }

        if hasattr(eb, 'update_param_overrides'):
            eb.update_param_overrides(overlay_result)

    def _on_recognition_params_changed(self, params: dict):
        """同步识别参数到 MusicStructureAnalyzer"""
        try:
            msa = self._msa
            if hasattr(msa, 'set_sensitivity'):
                msa.set_sensitivity(params.get("sensitivity", 1.0))
            if hasattr(msa, 'set_attack'):
                msa.set_attack(params.get("attack", 0.1))
            if hasattr(msa, 'set_release_ms'):
                msa.set_release_ms(params.get("release_ms", 200))
            if hasattr(msa, 'set_noise_floor'):
                msa.set_noise_floor(params.get("noise_floor", 0.02))
            if hasattr(msa, 'set_section_sensitivity'):
                msa.set_section_sensitivity(params.get("section_sens", 0.55))
            if hasattr(msa, 'set_mode'):
                msa.set_mode(params.get("mode", "auto"))
        except Exception:
            pass

    _SETTINGS_JOY_KEY = "joystick_binder_state_json_v1"

    def _load_joystick_binder_settings(self):
        """从 QSettings 加载摇杆绑定配置"""
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            raw = s.value(self._SETTINGS_JOY_KEY, "", type=str)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and hasattr(self, '_joy_panel'):
                    self._joy_panel.load_dict(data)
        except Exception:
            pass

    def _save_joystick_binder_settings(self):
        """保存摇杆绑定配置到 QSettings"""
        if not getattr(self, "_settings_ready", False):
            return
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            if hasattr(self, '_joy_panel'):
                data = self._joy_panel.to_dict()
                s.setValue(self._SETTINGS_JOY_KEY,
                           json.dumps(data, ensure_ascii=False))
                s.sync()
        except Exception:
            pass

    # ── Tab4: 乐器电平 ─────────────────────────────────────────────────
    def _build_meters_tab(self):
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        info_frame = QFrame()
        info_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:4px;border:1px solid #303030;")
        info_lay = QHBoxLayout(info_frame)
        info_lay.setContentsMargins(12, 6, 12, 6)
        info_lay.setSpacing(16)
        info_lay.addWidget(self._mk_label("乐器在场度（实时检测）"))
        info_lay.addStretch()
        self._lbl_energy2 = QLabel("能量: 0.00  段龄: 0.0s")
        self._lbl_energy2.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        info_lay.addWidget(self._lbl_energy2)
        lay.addWidget(info_frame)
        self._meter = _InstrumentMeter()
        self._meter.setMinimumHeight(120)
        self._meter.setMaximumHeight(200)
        lay.addWidget(self._meter)
        lay.addStretch()
        return w

    # ── 日志区 ──────────────────────────────────────────────────────────
    def _build_log_box(self):
        sec = PanelSection("实时事件日志")
        self._log = QPlainTextEdit()
        self._log.setStyleSheet(_LOG)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(300)
        self._log.setMinimumHeight(160)
        self._log.setMaximumHeight(16777215)
        sec.add_widget(self._log)
        return sec

    # ─────────────────────────────────────────────────────────────────────
    #  辅助构建方法
    # ─────────────────────────────────────────────────────────────────────
    def _mk_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:11px;background:transparent;border:none;")
        return lbl

    def _mk_vsep(self):
        s = QFrame()
        s.setFrameShape(QFrame.Shape.VLine)
        s.setStyleSheet("color:#303030;background:#303030;border:none;max-width:1px;")
        s.setFixedWidth(1)
        return s

    # ─────────────────────────────────────────────────────────────────────
    #  预设套 UI 刷新
    # ─────────────────────────────────────────────────────────────────────
    def _fetch_preset_items(self, force: bool = False):
        """获取所有已注册效果预设列表。
        ★ 修复：
          1. 使用正确的相对导入路径 ..effect_engine（从 ui.audio 往上2层到 ui）
          2. get_engine() 是懒加载单例，调用即自动初始化，不需要先打开效果库
          3. 多条备用路径确保在任何包结构下都能找到引擎
        """
        if not force and self._preset_items_cache is not None:
            return self._preset_items_cache
        eng = None
        # 按正确层级顺序尝试导入
        import sys as _sys
        _import_attempts = [
            lambda: __import__('ui.effect_engine', fromlist=['get_engine']).get_engine,
        ]
        for _attempt in range(4):
            try:
                if _attempt == 0:
                    # ★ 正确路径：从 ui.audio 往上2层到 ui，再进 effect_engine
                    from ..effect_engine import get_engine as _ge
                elif _attempt == 1:
                    # 备用1：从 sys.modules 查找已加载的模块（最快，不触发新导入）
                    _ge = None
                    for _mod_name in list(_sys.modules.keys()):
                        if _mod_name.endswith('.effect_engine') or _mod_name == 'effect_engine':
                            _mod = _sys.modules[_mod_name]
                            if hasattr(_mod, 'get_engine'):
                                _ge = _mod.get_engine
                                break
                    if _ge is None:
                        continue
                elif _attempt == 2:
                    # 备用2：绝对导入（适用于 src 在 sys.path 中的情况）
                    from ui.effect_engine import get_engine as _ge  # type: ignore
                else:
                    # 备用3：三层相对导入（兼容旧包结构）
                    from ...ui.effect_engine import get_engine as _ge
                eng = _ge()
                if eng is not None:
                    break
            except Exception:
                continue
        if eng is None:
            return []
        try:
            items = []
            for p in eng.all_presets():
                items.append((p.id, f"{p.id} ({p.name})"))
            self._preset_items_cache = sorted(items, key=lambda x: x[0])
            return self._preset_items_cache
        except Exception:
            return []

    def _request_preset_ui_refresh(self, delay_ms: int = 0, force: bool = False):
        if force:
            self._preset_refresh_force = True
        if self._preset_refresh_pending:
            return
        self._preset_refresh_pending = True
        delay_ms = max(0, int(delay_ms))
        QTimer.singleShot(delay_ms, self._start_preset_ui_refresh)

    def _start_preset_ui_refresh(self):
        self._preset_refresh_pending = False
        self._preset_refresh_generation += 1
        generation = self._preset_refresh_generation
        force = bool(self._preset_refresh_force)
        self._preset_refresh_force = False
        preset_items = self._fetch_preset_items(force=force)
        panels = list(self._type_preset_panels.items())
        self._refresh_preset_ui_batch(generation, preset_items, panels, 0)

    def _refresh_preset_ui_batch(self, generation: int, preset_items: list,
                                 panels: list, index: int):
        if generation != self._preset_refresh_generation:
            return
        if index >= len(panels):
            return
        _tt, panel = panels[index]
        try:
            panel.refresh_preset_items(preset_items)
            panel.load_from_bridge(self._bridge)
        except Exception:
            pass
        QTimer.singleShot(
            0,
            lambda g=generation, items=preset_items, ps=panels, i=index + 1:
                self._refresh_preset_ui_batch(g, items, ps, i)
        )

    def _refresh_all_preset_ui(self):
        self._request_preset_ui_refresh(delay_ms=0, force=True)

    def _sync_type_to_bridge(self, track_type: str):
        panel = self._type_preset_panels.get(track_type)
        if not panel:
            return
        sets = panel.collect_preset_sets()
        self._bridge.replace_preset_sets(track_type, sets)
        self._save_settings()

    # ─────────────────────────────────────────────────────────────────────
    #  调制器 UI 同步
    # ─────────────────────────────────────────────────────────────────────
    def _load_modulator_ui(self):
        if not self._modulator:
            return
        # 新版 BridgeModulationPanel（不依赖 modulator，跳过）
        if hasattr(self, '_bridge_mod_panel') and self._bridge_mod_panel:
            return  # BridgeModulationPanel 有自己的持久化，不需要从 modulator 加载
        # 旧版面板
        for tt, panel in self._type_mod_panels.items():
            config = self._modulator.get_config(tt)
            panel.load_config(config)

    def _sync_modulator_from_ui(self):
        if not self._modulator:
            return
        for tt, panel in self._type_mod_panels.items():
            config = panel.collect_config()
            if config:
                self._modulator.set_config(config)
        self._save_settings()

    def _on_mod_v2_changed(self):
        """v2调制面板变更回调"""
        self._save_settings()
    def _on_mod_global_toggled(self, checked):
        if self._modulator:
            self._modulator.set_global_enabled(checked)
        self._log_line(
            f"自动调制 {'启用' if checked else '禁用'}", color=FG_GREEN)
        self._save_settings()

    # ─────────────────────────────────────────────────────────────────────
    #  持久化
    # ─────────────────────────────────────────────────────────────────────
    _SETTINGS_ORG = "Qascade"
    _SETTINGS_APP = "AudioBridge"
    _SETTINGS_KEY = "bridge_state_json_v2"
    _SETTINGS_MOD_KEY = "modulator_state_json_v1"
    _SETTINGS_BRIDGE_MOD_KEY = "bridge_mod_panel_state_json_v1"
    _SETTINGS_JOY_PANEL_KEY = "bridge_joy_panel_state_json_v1"

    def _load_settings(self):
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            raw = s.value(self._SETTINGS_KEY, "", type=str)
            if not raw:
                raw = s.value("bridge_state_json_v1", "", type=str)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._bridge.load_dict(data)
            if self._modulator:
                raw_mod = s.value(self._SETTINGS_MOD_KEY, "", type=str)
                if raw_mod:
                    data_mod = json.loads(raw_mod)
                    if isinstance(data_mod, dict):
                        self._modulator.load_dict(data_mod)
            # 加载新版自动调制面板配置
            raw_bmod = s.value(self._SETTINGS_BRIDGE_MOD_KEY, "", type=str)
            if raw_bmod and hasattr(self, '_bridge_mod_panel') and self._bridge_mod_panel:
                try:
                    d = json.loads(raw_bmod)
                    if isinstance(d, dict):
                        self._bridge_mod_panel.load_dict(d)
                except Exception:
                    pass
            # 加载新版摇杆面板配置
            raw_joy = s.value(self._SETTINGS_JOY_PANEL_KEY, "", type=str)
            if raw_joy and hasattr(self, '_joy_panel') and self._joy_panel:
                try:
                    d = json.loads(raw_joy)
                    if isinstance(d, dict):
                        self._joy_panel.load_dict(d)
                except Exception:
                    pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "AudioBridge load_settings failed: %s", e, exc_info=True)
        # 设置已写入 bridge，UI 下拉框延后分批同步，避免打开面板时一次性卡住。
        self._request_preset_ui_refresh(delay_ms=260)

    # ─────────────────────────────────────────────────────────────────────
    #  一键配置操作
    # ─────────────────────────────────────────────────────────────────────
    def _on_onekey_random_all(self):
        """一键随机全部四类型效果配置"""
        import random
        try:
            from ..effect_engine import get_engine
        except ImportError:
            try:
                from ui.effect_engine import get_engine  # type: ignore
            except ImportError:
                from ...ui.effect_engine import get_engine  # type: ignore
        try:
            eng = get_engine()
            if eng is None:
                self._log_line("无法获取效果引擎", color="#E36855")
                return
        except Exception as e:
            self._log_line(f"效果引擎错误: {e}", color="#E36855")
            return

        # 效果分类映射（与effect_type_manager.py保持一致）
        SUBCATS = {
            "light": ["灯光/", "light/"],
            "pump":  ["泵/", "pump/"],
            "motor": ["电机/", "motor/"],
            "valve": ["阀门/", "valve/"],
        }

        preset_items = self._fetch_preset_items()
        if not preset_items:
            self._log_line("没有可用的效果预设", color="#E36855")
            return

        # 按类型分组预设
        type_presets = {}
        for tt, prefixes in SUBCATS.items():
            matched = []
            for pid, _ in preset_items:
                try:
                    p = eng.get_preset(pid)
                    if p:
                        cat = p.category or ""
                        for pf in prefixes:
                            if cat.startswith(pf) or pf.rstrip('/') in cat:
                                matched.append(pid)
                                break
                except Exception:
                    pass
            # 如果分类匹配为空，使用所有预设作为后备
            type_presets[tt] = matched if matched else [pid for pid, _ in preset_items]

        # 对每个类型的每个段落随机填充
        spins = getattr(self, '_onekey_spins', {})
        changed_types = []
        for tt, panel in self._type_preset_panels.items():
            available = type_presets.get(tt, [])
            if not available:
                continue
            count = spins.get(tt, None)
            n = count.value() if count else 2
            if hasattr(panel, "_ensure_built"):
                panel._ensure_built()
            for section, row in panel._section_rows.items():
                # 随机填充结构级槽位
                while len(row._sec_slots) < n:
                    row._add_slot("section")
                while len(row._sec_slots) > n:
                    if row._sec_slots:
                        slot = row._sec_slots[-1]
                        row._sec_slots.remove(slot)
                        slot.setParent(None)
                for slot in row._sec_slots:
                    slot.set_preset_id(random.choice(available))
                # 随机填充小节级槽位
                while len(row._bar_slots) < n:
                    row._add_slot("bar")
                while len(row._bar_slots) > n:
                    if row._bar_slots:
                        slot = row._bar_slots[-1]
                        row._bar_slots.remove(slot)
                        slot.setParent(None)
                for slot in row._bar_slots:
                    slot.set_preset_id(random.choice(available))
                row.changed.emit()
            changed_types.append(tt)

        # 同步到桥接器
        for tt in changed_types:
            self._sync_type_to_bridge(tt)

        type_labels = {"light": "灯光", "pump": "水泵", "motor": "电机", "valve": "电磁阀"}
        labels_str = "/".join(type_labels.get(t, t) for t in changed_types)
        self._log_line(f"一键随机配置完成: {labels_str}", color="#56B65B")

    def _on_save_config_file(self):
        """保存当前所有效果配置到JSON文件"""
        import json
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "保存效果配置",
            os.path.expanduser("~/bridge_config.json"),
            "JSON配置文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            data = self._bridge.to_dict()
            data["_version"] = "v26"
            data["_saved_at"] = __import__('datetime').datetime.now().isoformat()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log_line(f"配置已保存: {os.path.basename(path)}", color="#56B65B")
        except Exception as e:
            self._log_line(f"保存失败: {e}", color="#E36855")

    def _on_load_config_file(self):
        """从JSON文件加载效果配置"""
        import json
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getOpenFileName(
            self, "打开效果配置",
            os.path.expanduser("~/"),
            "JSON配置文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("配置文件格式错误")
            self._bridge.load_dict(data)
            self._refresh_all_preset_ui()
            self._log_line(f"配置已加载: {os.path.basename(path)}", color="#56B65B")
        except Exception as e:
            self._log_line(f"加载失败: {e}", color="#E36855")
            QMessageBox.warning(self, "加载失败", f"无法加载配置文件：\n{e}")

    def _save_settings(self):
        if not getattr(self, "_settings_ready", False):
            return
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            data = self._bridge.to_dict()
            s.setValue(self._SETTINGS_KEY, json.dumps(data, ensure_ascii=False))
            if self._modulator:
                data_mod = self._modulator.to_dict()
                s.setValue(self._SETTINGS_MOD_KEY,
                           json.dumps(data_mod, ensure_ascii=False))
            # 保存新版自动调制面板配置
            if hasattr(self, '_bridge_mod_panel') and self._bridge_mod_panel:
                try:
                    s.setValue(self._SETTINGS_BRIDGE_MOD_KEY,
                               json.dumps(self._bridge_mod_panel.to_dict(),
                                          ensure_ascii=False))
                except Exception:
                    pass
            # 保存新版摇杆面板配置
            if hasattr(self, '_joy_panel') and self._joy_panel:
                try:
                    s.setValue(self._SETTINGS_JOY_PANEL_KEY,
                               json.dumps(self._joy_panel.to_dict(),
                                          ensure_ascii=False))
                except Exception:
                    pass
            s.sync()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "AudioBridge save_settings failed: %s", e, exc_info=True)

    # ─────────────────────────────────────────────────────────────────────
    #  事件处理
    # ─────────────────────────────────────────────────────────────────────
    def _on_enable_toggled(self, checked):
        self._bridge.set_enabled(checked)
        self._enable_btn.setText("桥接已启用" if checked else "启用桥接")
        self._log_line(f"桥接 {'启用' if checked else '禁用'}", color=FG_GREEN)
        self._save_settings()

    def _on_type_enable_toggled(self, track_type: str, checked: bool):
        self._bridge.set_type_active(track_type, checked)
        label = TRACK_TYPES.get(track_type, {}).get("label", track_type)
        self._log_line(
            f"{label} 效果 {'启用' if checked else '禁用'}", color=FG_GREEN)
        self._save_settings()

    def _on_speed_changed(self, val):
        speed = val / 100.0
        self._bridge.set_switch_speed(speed)
        self._lbl_speed.setText(f"{speed:.2f}x")
        self._save_settings()

    # ── 效果速度：手调优先 + BPM 自动驱动 ─────────────────────────────────
    def _on_effect_speed_manual_start(self):
        """用户按下滑块时立即标记为手调模式，锁定手调。"""
        self._effect_speed_manual = True
        if hasattr(self, '_btn_auto_speed'):
            self._btn_auto_speed.setChecked(False)

    def _on_auto_speed_toggled(self, checked: bool):
        """点击"自动"按钮：切换 BPM 自动驱动模式。"""
        self._effect_speed_manual = not checked
        if checked:
            self._log_line("效果速度：BPM 自动驱动已启用", color=FG_GREEN)
        else:
            self._log_line("效果速度：手调模式", color=FG_ACCENT)

    def _apply_effect_speed(self, factor: float, update_slider: bool = True):
        """统一下发速度到 EffectBrowserWidget；EB 未注入时缓存到 _pending_effect_speed。"""
        factor = max(0.05, min(4.0, factor))
        self._pending_effect_speed = factor
        if update_slider and hasattr(self, '_sld_effect_speed'):
            self._sld_effect_speed.blockSignals(True)
            self._sld_effect_speed.setValue(int(factor * 100))
            self._sld_effect_speed.blockSignals(False)
        if hasattr(self, '_lbl_effect_speed'):
            self._lbl_effect_speed.setText(f"{factor:.2f}x")
        eb = self._effect_browser or self._get_effect_browser()
        if eb is not None and hasattr(eb, 'set_global_speed'):
            try:
                eb.set_global_speed(factor)
            except Exception:
                pass

    def _on_effect_speed_changed(self, val: int):
        """效果动画速度倍率回调：手调时锁定，同步到 EffectBrowserWidget。"""
        factor = val / 100.0
        if hasattr(self, '_lbl_effect_speed'):
            self._lbl_effect_speed.setText(f"{factor:.2f}x")
        # 只要滑块值变化就视为手调（sliderPressed 已标记，这里做双重保险）
        # 但若是被程序自动设置（blockSignals 不够时），通过 _effect_speed_manual 区分
        self._apply_effect_speed(factor, update_slider=False)
        self._save_settings()

    def _get_effect_browser(self):
        """获取 EffectBrowserWidget 实例（通过 MainWindow 引用）。"""
        try:
            # 尝试从父级链找到 MainWindow
            p = self.parent()
            while p is not None:
                if hasattr(p, '_effect_browser'):
                    return p._effect_browser
                if hasattr(p, 'effect_browser'):
                    return p.effect_browser
                p = p.parent() if hasattr(p, 'parent') else None
        except Exception:
            pass
        return None

    def _on_drop_sens_changed(self, val):
        ratio = max(0.0, min(1.0, (80 - val + 10) / 100.0))
        threshold = 1.2 + ratio * (8.0 - 1.2)
        self._msa.set_sensitivity(drop=threshold)

    def _on_drop_cooldown_changed(self, val):
        cooldown = val * 0.1
        self._msa._drop_cooldown_s = float(max(0.5, min(20.0, cooldown)))

    def _on_phrase_length_changed(self, idx):
        beats = self._combo_phrase.currentData()
        try:
            beats = int(beats)
        except (TypeError, ValueError):
            return
        self._msa.set_beats_per_bar(beats)
        self._log_line(f"乐句长度: {beats} beats", color=FG_ACCENT)

    def _apply_style_preset(self, params: dict):
        self._msa.set_sensitivity(
            section=params.get("section"),
            instrument=params.get("instrument"),
            drop=params.get("drop"),
            noise_floor=params.get("noise_floor"),
            section_hold=params.get("section_hold"),
        )
        if "section" in params:
            self._sld_section.blockSignals(True)
            self._sld_section.setValue(int(params["section"] * 100))
            self._sld_section.blockSignals(False)
        if "instrument" in params:
            self._sld_instrument.blockSignals(True)
            self._sld_instrument.setValue(int(params["instrument"] * 100))
            self._sld_instrument.blockSignals(False)
        if "drop" in params:
            ratio = (params["drop"] - 1.2) / (8.0 - 1.2)
            sld_val = int(80 - ratio * 70 + 10)
            self._sld_drop.blockSignals(True)
            self._sld_drop.setValue(max(10, min(80, sld_val)))
            self._sld_drop.blockSignals(False)
        if "noise_floor" in params:
            self._sld_noise_floor.blockSignals(True)
            self._sld_noise_floor.setValue(int(params["noise_floor"] * 100))
            self._sld_noise_floor.blockSignals(False)
        if "section_hold" in params:
            self._sld_section_hold.blockSignals(True)
            self._sld_section_hold.setValue(int(params["section_hold"] * 100))
            self._sld_section_hold.blockSignals(False)
        self._log_line("已应用风格快捷预设", color=FG_GREEN)

    def _on_reset(self):
        self._msa.reset()
        self._log_line("已重置音乐结构状态", color=FG_GREEN)

    def _fire_test(self, trigger):
        ev = MusicStructureEvent(
            t=time.monotonic() - self._t0,
            event_type="section_change" if trigger in _SECTION_LIST else trigger,
            section_label=trigger,
            prev_section="",
            rms=0.6, peak=0.9,
            bands=[0.5] * 8,
            bpm=120.0, beat_phase=0.0, is_beat=False,
            instruments={k: 0.4 for k in INSTRUMENTS},
            section_age=0.0,
        )
        prev_enabled = self._bridge.enabled
        if not prev_enabled:
            self._bridge.set_enabled(True)
        self._bridge._on_structure_event(ev)
        if not prev_enabled:
            self._bridge.set_enabled(False)
        self._log_line(f"手动触发: {trigger}", color=FG_ORANGE)

    # ─────────────────────────────────────────────────────────────────────
    #  设备组过滤
    # ─────────────────────────────────────────────────────────────────────
    def set_context(self, project_manager=None, effect_browser=None):
        """由 MainWindow 在懒加载后注入运行时依赖。"""
        if project_manager is not None:
            self._project_manager = project_manager
        if effect_browser is not None:
            self._effect_browser = effect_browser
            # EB 刚注入：补发之前缓存的速度值
            pending = getattr(self, '_pending_effect_speed', 1.0)
            if abs(pending - 1.0) > 0.01 or getattr(self, '_effect_speed_manual', False):
                try:
                    effect_browser.set_global_speed(pending)
                except Exception:
                    pass
        self._refresh_group_combo()

    def link_timeline_audio(self, audio_path: str):
        """将时间线导入的音频文件加载到全局 AudioAnalyzer，使音频分析和调制能真正工作。
        由 MainWindow 在时间线导入音频时调用。
        """
        try:
            from ..audio_driver import get_audio_driver
        except ImportError:
            try:
                from src.ui.audio_driver import get_audio_driver
            except ImportError:
                return
        try:
            analyzer = get_audio_driver()
            analyzer.load_audio_file(audio_path)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "AudioBridgePanel.link_timeline_audio failed: %s", e)

    def on_timeline_tick(self, pos: float):
        """接收时间线播放位置更新，传递给全局 AudioAnalyzer。"""
        try:
            from ..audio_driver import get_audio_driver
            get_audio_driver().set_timeline_position(pos)
        except Exception:
            pass

    def on_timeline_play(self, pos: float):
        """时间线开始播放，启动音频分析。"""
        try:
            from ..audio_driver import get_audio_driver
            an = get_audio_driver()
            an.set_timeline_position(pos)
            an.set_timeline_playing(True)
        except Exception:
            pass

    def on_timeline_stop(self):
        """时间线停止播放。"""
        try:
            from ..audio_driver import get_audio_driver
            get_audio_driver().set_timeline_playing(False)
        except Exception:
            pass

    def stop_effect_output(self):
        """Clear bridge-owned EffectBrowser playback without disabling settings."""
        try:
            if hasattr(self._bridge, "clear_current_presets"):
                self._bridge.clear_current_presets()
        except Exception:
            pass

    def _get_fixture_groups(self) -> list:
        """从 ProjectManager 读取 fixture_groups，兼容多种存储位置。"""
        pm = self._project_manager
        if pm is None:
            return []
        # 优先 pm.fixture_groups，回退 pm._fixture_groups，再回退 pm.project.fixture_groups
        groups = getattr(pm, 'fixture_groups', None)
        if groups is None:
            groups = getattr(pm, '_fixture_groups', None)
        if groups is None:
            proj = getattr(pm, 'project', None) or getattr(pm, '_project', None)
            if proj is not None:
                groups = getattr(proj, 'fixture_groups', None)
        return list(groups) if groups else []

    def _refresh_group_combo(self):
        """从 ProjectManager.fixture_groups 刷新设备组下拉框。"""
        combo = getattr(self, '_group_combo', None)
        if combo is None:
            return
        prev_id = self._selected_group_id
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部设备", None)
        restore_idx = 0
        groups = self._get_fixture_groups()
        for i, gd in enumerate(groups):
            if isinstance(gd, dict):
                gid = gd.get('id', '')
                name = gd.get('name', 'Group')
                fids = gd.get('fixture_ids', [])
            else:
                gid = getattr(gd, 'id', '')
                name = getattr(gd, 'name', 'Group')
                fids = getattr(gd, 'fixture_ids', [])
            combo.addItem(f"{name}（{len(fids)}台）", gid)
            if gid == prev_id:
                restore_idx = i + 1
        combo.setCurrentIndex(restore_idx)
        combo.blockSignals(False)
        self._apply_group_filter(restore_idx)

    def _on_group_filter_changed(self, index: int):
        """用户切换设备组下拉框。"""
        self._apply_group_filter(index)

    def _apply_group_filter(self, index: int):
        """将选中的设备组 fixture_ids 同步到 EffectBrowser._selected_group_ids。"""
        combo = getattr(self, '_group_combo', None)
        if combo is None:
            return
        gid = combo.itemData(index)
        self._selected_group_id = gid
        fixture_ids = []
        if gid is not None:
            for gd in self._get_fixture_groups():
                g_id = gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')
                if g_id == gid:
                    fixture_ids = list(
                        gd.get('fixture_ids', []) if isinstance(gd, dict)
                        else getattr(gd, 'fixture_ids', []))
                    break
        # 同步到 EffectBrowser
        eb = self._effect_browser
        if eb is not None and hasattr(eb, '_selected_group_ids'):
            eb._selected_group_ids = fixture_ids
        # 更新状态标签
        lbl = getattr(self, '_lbl_group_status', None)
        if lbl is not None:
            if gid is None:
                lbl.setText("全部设备")
            else:
                lbl.setText(f"{combo.currentText()}")
        self._log_line(
            f"设备组过滤: {'全部设备' if gid is None else combo.currentText()}",
            color=FG_GREEN)

    # ── 多设备组输出管理 ───────────────────────────────────────────────────
    def _on_add_output_group(self):
        """弹出设备组选择对话框，将选中的设备组添加到输出列表。"""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem
        groups = self._get_fixture_groups()
        if not groups:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "设备组", "当前项目中没有可用的设备组。\n请先在设备管理中创建设备组。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("选择输出设备组")
        apply_dialog_chrome(dlg)   # D 波：统一浮窗外框
        dlg.setMinimumWidth(280)
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.setContentsMargins(12, 12, 12, 12)
        dlg_lay.setSpacing(8)

        hint = QLabel("选择要添加的设备组（可多选）：")
        hint.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        dlg_lay.addWidget(hint)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        # 列表行解剖：24px 行高、选中 select_blue 白字 + 左竖条
        lst.setStyleSheet(
            f"QListWidget{{background:#1D1D1D;color:{FG_MAIN};"
            f"border:1px solid rgba(255,255,255,17);border-radius:4px;font-size:11px;outline:none;}}"
            "QListWidget::item{min-height:24px;padding:0 8px;border-radius:3px;"
            "border-left:3px solid transparent;}"
            "QListWidget::item:selected{background:#4772B3;color:#FFFFFF;"
            "border-left:3px solid #71A8FF;}"
            "QListWidget::item:hover:!selected{background:#3D3D3D;}"
        )
        for gd in groups:
            gid  = gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')
            name = gd.get('name', 'Group') if isinstance(gd, dict) else getattr(gd, 'name', 'Group')
            fids = gd.get('fixture_ids', []) if isinstance(gd, dict) else getattr(gd, 'fixture_ids', [])
            if gid in self._output_group_ids:
                continue  # 已添加的不再显示
            item = QListWidgetItem(f"{name}（{len(fids)}台）")
            item.setData(Qt.ItemDataRole.UserRole, gid)
            lst.addItem(item)
        dlg_lay.addWidget(lst)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setObjectName("primary")     # 确定 → primary 蓝底语义
        if cancel_btn is not None:
            cancel_btn.setProperty("role", "cancel")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        for item in lst.selectedItems():
            gid = item.data(Qt.ItemDataRole.UserRole)
            if gid and gid not in self._output_group_ids:
                _dispatch_state.append_output_group(self, gid)
        self._refresh_output_groups_ui()
        self._log_line(
            f"输出设备组更新: {len(self._output_group_ids)} 个组",
            color=FG_GREEN)

    def _on_remove_output_group(self, gid: str):
        """从输出列表中删除指定设备组。"""
        if gid in self._output_group_ids:
            _dispatch_state.remove_output_group(self, gid)
        self._refresh_output_groups_ui()
        self._log_line(
            f"移除输出设备组: {gid}",
            color=FG_ORANGE)

    def _refresh_output_groups_ui(self):
        """刷新多设备组输出的 UI 显示。"""
        # 清空列表容器
        while self._grp_list_lay.count():
            item = self._grp_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._output_group_ids:
            # 无选中设备组：显示“全部设备”
            lbl = QLabel("全部设备")
            lbl.setStyleSheet(
                f"color:{FG_GREEN};font-size:10px;background:transparent;border:none;")
            self._grp_list_lay.addWidget(lbl)
            return

        # 显示每个设备组的小标签 + 删除按钮
        groups = self._get_fixture_groups()
        gid_to_name = {}
        for gd in groups:
            gid  = gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')
            name = gd.get('name', 'Group') if isinstance(gd, dict) else getattr(gd, 'name', 'Group')
            gid_to_name[gid] = name

        for gid in list(self._output_group_ids):
            name = gid_to_name.get(gid, gid[:8])
            chip = QFrame()
            chip.setStyleSheet(
                f"background:#2D2D2D;border:1px solid #4772B3;border-radius:4px;")  # 药丸 10px → 统一 4px
            chip_lay = QHBoxLayout(chip)
            chip_lay.setContentsMargins(6, 2, 4, 2)
            chip_lay.setSpacing(4)
            lbl = QLabel(name)
            lbl.setStyleSheet(
                f"color:{FG_ACCENT};font-size:10px;background:transparent;border:none;")
            chip_lay.addWidget(lbl)
            btn_del = QPushButton("×")
            btn_del.setStyleSheet(
                f"QPushButton{{background:transparent;color:{FG_RED};border:none;"
                f"font-size:11px;font-weight:bold;padding:0 2px;}}"
                f"QPushButton:hover{{color:#E36855;}}"
            )
            btn_del.setFixedSize(16, 16)
            btn_del.setToolTip(f"移除 {name}")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            _gid = gid  # 闭包捕获
            btn_del.clicked.connect(lambda _, g=_gid: self._on_remove_output_group(g))
            chip_lay.addWidget(btn_del)
            self._grp_list_lay.addWidget(chip)

    def _get_output_fixture_ids(self) -> List[str]:
        """
        返回当前所有输出设备组的合并 fixture_id 列表。
        如果没有选中任何设备组，返回空列表（表示全部设备）。
        """
        ids, _base_order = self._get_output_fixture_order()
        return ids

    def _get_output_fixture_order(self) -> tuple[List[str], List[str]]:
        """Return output fixture ids and their authoritative base sequence."""
        if not self._output_group_ids:
            return [], []
        groups = self._get_fixture_groups()
        by_gid = {
            str(gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')): gd
            for gd in groups
        }
        output_ids: List[str] = []
        base_order: List[str] = []
        seen_ids = set()
        seen_base = set()
        for gid in list(self._output_group_ids):
            gd = by_gid.get(str(gid))
            if gd is None:
                continue
            fids = list(gd.get('fixture_ids', []) if isinstance(gd, dict)
                        else getattr(gd, 'fixture_ids', []))
            base = list(gd.get('base_order_fixture_ids', []) if isinstance(gd, dict)
                        else getattr(gd, 'base_order_fixture_ids', []))
            allowed = {str(fid) for fid in fids}
            ordered = [str(fid) for fid in base if str(fid) in allowed]
            present = set(ordered)
            for fid in fids:
                sfid = str(fid)
                if sfid and sfid not in present:
                    ordered.append(sfid)
                    present.add(sfid)
            for sfid in ordered:
                if sfid and sfid not in seen_ids:
                    output_ids.append(sfid)
                    seen_ids.add(sfid)
                if sfid and sfid not in seen_base:
                    base_order.append(sfid)
                    seen_base.add(sfid)
        return output_ids, base_order

    def _on_bridge_apply(self, track_type, preset_id, params, ch_types, sort_mode):
        """
        效果应用回调。
        如果配置了多设备组输出，将设备组的 fixture_ids 合并后注入到 params 中再发射。
        """
        # 将输出设备组的 fixture_ids 合并注入到 params
        output_ids, base_order_ids, params = _dispatch_state.scope_for_apply(
            self, (track_type, preset_id, params, ch_types, sort_mode))
        if output_ids:
            merged_params = dict(params)
            merged_params['_output_fixture_ids'] = output_ids
            merged_params['_output_base_order_ids'] = base_order_ids or output_ids
            self.effect_apply_request.emit(track_type, preset_id, merged_params, ch_types, sort_mode)
        else:
            # 未选中任何设备组，发射到全部设备
            self.effect_apply_request.emit(track_type, preset_id, params, ch_types, sort_mode)
        # 调度台：效果应用是离散状态变化点 → 立即上报
        self.report_to_bus(force=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：音频桥总线上报（唯一出口）
    #
    #  为什么在这里：音频桥的"效果"就是**每种启用的效果类型当前绑定的预设**
    #  （bridge._active_types + bridge._current_presets），作用域是输出设备组。
    #
    #  ★ 身份契约：一窗格 = (panel, track_type, group_key)。track_type = 效果类型
    #  标签，group_key 由输出设备组设备 id 内容寻址 → "同类型不同设备组"天然两格；
    #  **绝不按 preset_id/名称/分类合并**（用户：微差即两种效果）。
    # ══════════════════════════════════════════════════════════════════════════

    #: 上报节流（秒）：analysis_tick 可达 30Hz+，总线只需人眼刷新率级别
    _BUS_REPORT_INTERVAL = 0.25

    @staticmethod
    def _dispatch_group_key(ids) -> str:
        """设备 id 内容寻址的设备集身份（与 EffectBrowser._group_key_from_ids 同规则）。"""
        out, seen = [], set()
        for fid in (ids or []):
            sfid = str(fid)
            if sfid and sfid not in seen:
                seen.add(sfid)
                out.append(sfid)
        return "grp:" + "|".join(out) if out else "__all__"

    def _bus_nodes(self) -> list:
        """Report each actual bridge application with its own target/channel."""
        return _dispatch_state.bus_nodes(self)

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前音频窗格到总线（节流；失败绝不影响音频输出）。"""
        try:
            now = time.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ...processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 逐键 upsert + 差集删除：类型停用时只删它自己的格，不整体 clear_panel
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    def dispatch_capture_state(self) -> dict:
        """给调度台捕获可完整还原的音频桥状态（复用全局 bridge 既有 to_dict）。"""
        try:
            bridge = getattr(self, "_bridge", None)
            if bridge is not None and hasattr(bridge, "to_dict"):
                return _dispatch_state.capture_state(self, bridge.to_dict())
            return {}
        except Exception:
            return {}

    def dispatch_release_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """**释放**音频桥（关闭场景时调用）：停掉桥的输出并清当前预设。

        ★★ 2026-09-14 深查修正：本面板只有 capture/recall、**没有 release**，
          释放路由对"无 release"静默算成功 → 关闭含音频的场景后桥仍在驱动效果
          （"关不掉"）。复用桥既有 `set_enabled(False)`（其内部会
          `clear_current_presets()` 停掉所有它拥有的输出）。
        """
        try:
            bridge = getattr(self, "_bridge", None)
            if bridge is not None and hasattr(bridge, "set_enabled"):
                _dispatch_state.release_node(self, node, bridge)
            try:
                self.report_to_bus(force=True)
            except Exception:
                pass
            return True
        except Exception:
            logging.getLogger(__name__).debug(
                "audio_bridge: dispatch_release_state failed", exc_info=True)
            return False

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None,
                              *, for_edit: bool = False) -> bool:
        """按调度台节点还原音频桥（复用全局 bridge 既有 load_dict）。"""
        try:
            bridge = getattr(self, "_bridge", None)
            if bridge is None or not state:
                return False
            _dispatch_state.restore_node(self, node, state, for_edit=for_edit)
            self.report_to_bus(force=True)     # 立即反映到调度台
            return True
        except Exception:
            logging.getLogger(__name__).debug(
                "audio_bridge: dispatch_recall_state failed", exc_info=True)
            return False

    def _on_status(self, text):
        self._log_line(text, color=FG_ACCENT)

    def _on_current_preset(self, track_type, preset_id):
        lbl = self._type_current_labels.get(track_type)
        if lbl:
            icon = TRACK_TYPES.get(track_type, {}).get("icon", "?")
            lbl.setText(f"[{icon}]{preset_id or '--'}")

    def _on_mapping_triggered(self, m):
        pass

    def _on_tick(self, ev):
        self._tick_throttle = (self._tick_throttle + 1) % 4
        if self._tick_throttle != 0:
            return
        meter = getattr(self, "_meter", None)
        if meter is not None:
            meter.update_event(ev)
        if hasattr(self, "_lbl_section"):
            self._lbl_section.setText(ev.section_label or "--")
        if hasattr(self, "_lbl_bpm"):
            self._lbl_bpm.setText(f"{ev.bpm:.1f}")
        bar_num = getattr(ev, 'bar_number', -1)
        if bar_num >= 0 and hasattr(self, "_lbl_bar"):
            self._lbl_bar.setText(str(bar_num))
        energy_text = f"能量: {ev.rms:.2f}  段龄: {ev.section_age:.1f}s"
        if hasattr(self, "_lbl_energy"):
            self._lbl_energy.setText(energy_text)
        lbl_energy2 = getattr(self, "_lbl_energy2", None)
        if lbl_energy2 is not None:
            lbl_energy2.setText(energy_text)
        # ── BPM 自动驱动效果速度（手调优先）──
        # 手调模式下跳过；自动按鈕未开启时也跳过
        if not getattr(self, '_effect_speed_manual', True):
            self._bpm_speed_throttle = (self._bpm_speed_throttle + 1) % 8
            if self._bpm_speed_throttle == 0:
                bpm = max(40.0, min(240.0, float(ev.bpm or 120.0)))
                # 以 120 BPM 为基准评率 1.0x，线性映射到 [0.3x, 2.5x]
                factor = max(0.3, min(2.5, bpm / 120.0))
                self._apply_effect_speed(factor, update_slider=True)
        # 调度台上报（节流在 report_to_bus 内部；失败不影响音频输出）
        self.report_to_bus()

    def _on_structure_event_ui(self, ev):
        et = ev.event_type
        if et == "section_change":
            self._log_line(
                f"[{ev.t:6.2f}] 结构变化: {ev.prev_section or '?'} → {ev.section_label}",
                color=FG_GREEN)
        elif et == "bar_change":
            self._log_line(f"[{ev.t:6.2f}] 小节#{ev.bar_number}", color=FG_DIM)
        elif et == "tempo_change":
            self._log_line(
                f"[{ev.t:6.2f}] 速度变化: BPM={ev.bpm:.0f} delta={ev.tempo_delta:+.1f}",
                color=FG_ORANGE)
        elif et == "drop":
            self._log_line(f"[{ev.t:6.2f}] DROP (rms={ev.rms:.2f})", color=ACCENT)
        elif et == "buildup_start":
            self._log_line(f"[{ev.t:6.2f}] buildup 开始", color=FG_ORANGE)
        elif et == "buildup_end":
            self._log_line(f"[{ev.t:6.2f}] buildup 结束", color=FG_DIM)

    def _log_line(self, text, color=FG_MAIN):
        self._log.appendPlainText(text)

    # ─────────────────────────────────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────────────────────────────────
    def shutdown(self):
        for sig, slot in [
            (self._msa.analysis_tick, self._on_tick),
            (self._msa.structure_event, self._on_structure_event_ui),
            (self._bridge.effect_apply_request, self._on_bridge_apply),
        ]:
            try:
                sig.disconnect(slot)
            except Exception:
                pass


AudioBridgePanel.dispatch_recall_state.__accepts_for_edit__ = True
