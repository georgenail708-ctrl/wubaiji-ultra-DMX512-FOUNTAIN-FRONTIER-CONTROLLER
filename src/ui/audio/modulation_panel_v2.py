"""
modulation_panel_v2.py — 自动调制面板 v2 (滑块拖动式UI)
=========================================================
重构要点：
  1. 用滑块(Slider)替代SpinBox进行参数调整，与effect原始设置一致
  2. 整合选项：将音频源+目标参数+范围+平滑+曲线整合为紧凑的卡片式布局
  3. 减少选项数量：常用调制规则以模板形式提供一键添加
  4. 每条规则显示为一个紧凑的水平卡片，滑块实时调整
"""
from __future__ import annotations
from typing import Dict, List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QComboBox, QCheckBox,
    QFrame, QTabWidget, QScrollArea, QGridLayout,
    QSizePolicy,
)

from .. import qascade_theme as _th

# ─── 颜色常量（引用主题令牌，不再自建十六进制）───────────────────────────────
BG_MAIN     = _th.BG_ROOT
BG_PANEL    = _th.BG_EDITOR
BG_CARD     = _th.BG_ROOT
FG_MAIN     = _th.TEXT_PRIMARY
FG_DIM      = _th.TEXT_SECONDARY
FG_ACCENT   = _th.FOCUS_BLUE
FG_GREEN    = _th.BRIGHT_SUCCESS
FG_ORANGE   = _th.BRIGHT_WARN
FG_RED      = _th.BRIGHT_ERROR

# ─── 样式（通用外观交给全局主题接管，此处只留语义色特化）───────────────────
# 已删除 _BTN：定义后从未被引用（本文件死代码，与全局 QPushButton 外观也
# 完全重复，§3 结构性统一）。
_BTN_ADD = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_GREEN};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:3px 10px;font-size:{_th.FS_SMALL}px;}}"
)
_BTN_DEL = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_RED};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:10px;font-size:11px;font-weight:bold;}}"
)
_BTN_TEMPLATE = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:#a098cf;border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:3px;padding:2px 8px;font-size:{_th.FS_SMALL}px;}}"
)
# 已删除 _COMBO/_TAB_INNER：与全局 QComboBox/QTabBar 外观完全重复（§3 结构性统一）。
_SLIDER = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    "QSlider::handle:horizontal{background:#71A8FF;width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3a4a5a;border-radius:2px;}"
)
_SLIDER_GREEN = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    "QSlider::handle:horizontal{background:#56B65B;width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3a4a3a;border-radius:2px;}"
)
_SLIDER_ORANGE = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    "QSlider::handle:horizontal{background:#E6B455;width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3a3a2a;border-radius:2px;}"
)
_SCROLL = (
    "QScrollArea{border:none;background:transparent;}"
    f"QScrollBar:vertical{{background:{_th.BG_ROOT};width:6px;border-radius:3px;}}"
    f"QScrollBar::handle:vertical{{background:{_th.SCROLLBAR_HANDLE};border-radius:3px;min-height:20px;}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
)

# ─── 尝试导入调制器 ─────────────────────────────────────────────────────────
try:
    from ...audio.audio_param_modulator import (
        ModRule, TypeModConfig, AUDIO_SOURCES, MODULATABLE_PARAMS,
        get_global_modulator, get_default_mod_config,
    )
    _HAS_MODULATOR = True
except ImportError:
    _HAS_MODULATOR = False
    AUDIO_SOURCES = {}
    MODULATABLE_PARAMS = {}

# ─── 调制模板（常用组合） ────────────────────────────────────────────────────
_MOD_TEMPLATES = {
    "light": [
        ("力度→亮度", "rms", "max", 60, 255, 0.12, "log"),
        ("BPM→速度", "bpm", "speed", 0.3, 2.5, 0.05, "linear"),
        ("节拍→相位", "beat_phase", "phase_step", 0.0, 0.3, 0.0, "linear"),
        ("踢鼓→闪烁", "kick", "min", 0, 80, 0.25, "exp"),
        ("低频→宽度", "band_bass", "width", 1, 8, 0.15, "linear"),
    ],
    "pump": [
        ("力度→水压", "rms", "max", 80, 255, 0.10, "log"),
        ("BPM→频率", "bpm", "speed", 0.2, 2.0, 0.08, "linear"),
        ("低频→最小", "bass", "min", 0, 100, 0.20, "exp"),
        ("旋律→相位", "melody", "phase_step", 0.0, 0.2, 0.15, "linear"),
        ("踢鼓→脉冲", "kick", "rate", 1.0, 8.0, 0.10, "exp"),
    ],
    "motor": [
        ("力度→速度", "rms", "speed", 0.2, 2.0, 0.15, "linear"),
        ("BPM→转速", "bpm", "speed", 0.3, 2.0, 0.06, "linear"),
        ("旋律→相位差", "melody", "phase_step", 0.0, 0.3, 0.20, "linear"),
        ("低频→幅度", "band_bass", "max", 60, 200, 0.12, "log"),
        ("踢鼓→密度", "kick", "density", 0.2, 1.0, 0.10, "exp"),
    ],
    "valve": [
        ("踢鼓→频率", "kick", "rate", 0.5, 10.0, 0.10, "exp"),
        ("军鼓→开度", "snare", "max", 80, 255, 0.15, "linear"),
        ("BPM→速度", "bpm", "speed", 0.3, 3.0, 0.05, "linear"),
        ("力度→密度", "rms", "density", 0.2, 1.0, 0.12, "log"),
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
#  单条调制规则卡片（滑块式）
# ═══════════════════════════════════════════════════════════════════════════
class _ModRuleCardV2(QFrame):
    """单条调制规则 - 紧凑卡片式布局，全部使用滑块调整参数"""
    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, rule=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"background:{BG_CARD};border:1px solid #303030;border-radius:4px;")
        self.setFixedHeight(90)
        if _HAS_MODULATOR and rule is None:
            rule = ModRule(source="rms", param_key="speed",
                          out_min=0.5, out_max=4.0, smooth=0.15)
        self._rule = rule
        self._build_ui()

    def _build_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(8, 4, 8, 4)
        main_lay.setSpacing(2)

        # ── 第一行：启用 + 音频源 → 目标参数 + 曲线 + 删除 ──
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self._cb_enabled = QCheckBox()
        self._cb_enabled.setChecked(getattr(self._rule, 'enabled', True))
        self._cb_enabled.setStyleSheet("background:transparent;border:none;")
        self._cb_enabled.toggled.connect(self.changed)
        row1.addWidget(self._cb_enabled)

        # 音频源选择
        self._combo_source = QComboBox()
        self._combo_source.setFixedWidth(110)
        if _HAS_MODULATOR:
            for key, label in AUDIO_SOURCES.items():
                self._combo_source.addItem(label, key)
            idx = self._combo_source.findData(getattr(self._rule, 'source', 'rms'))
            if idx >= 0:
                self._combo_source.setCurrentIndex(idx)
        self._combo_source.currentIndexChanged.connect(self.changed)
        row1.addWidget(self._combo_source)

        arrow = QLabel("→")
        arrow.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;")
        row1.addWidget(arrow)

        # 目标参数选择
        self._combo_param = QComboBox()
        self._combo_param.setFixedWidth(100)
        if _HAS_MODULATOR:
            for key, label in MODULATABLE_PARAMS.items():
                self._combo_param.addItem(label, key)
            idx = self._combo_param.findData(getattr(self._rule, 'param_key', 'speed'))
            if idx >= 0:
                self._combo_param.setCurrentIndex(idx)
        self._combo_param.currentIndexChanged.connect(self.changed)
        row1.addWidget(self._combo_param)

        # 曲线选择
        self._combo_curve = QComboBox()
        self._combo_curve.setFixedWidth(65)
        for c in ["linear", "exp", "log", "step"]:
            self._combo_curve.addItem(c, c)
        idx = self._combo_curve.findData(getattr(self._rule, 'curve', 'linear'))
        if idx >= 0:
            self._combo_curve.setCurrentIndex(idx)
        self._combo_curve.currentIndexChanged.connect(self.changed)
        row1.addWidget(self._combo_curve)

        row1.addStretch()

        # 删除按钮
        btn_del = QPushButton("×")
        btn_del.setStyleSheet(_BTN_DEL)
        btn_del.setFixedSize(20, 20)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        row1.addWidget(btn_del)

        main_lay.addLayout(row1)

        # ── 第二行：输出范围滑块（最小值 ~ 最大值） ──
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        lbl_range = QLabel("范围:")
        lbl_range.setStyleSheet(
            f"color:{FG_DIM};font-size:{_th.FS_SMALL}px;background:transparent;border:none;")
        lbl_range.setFixedWidth(30)
        row2.addWidget(lbl_range)

        self._sld_min = QSlider(Qt.Orientation.Horizontal)
        self._sld_min.setRange(-1000, 10000)
        self._sld_min.setValue(int(getattr(self._rule, 'out_min', 0.0) * 100))
        self._sld_min.setStyleSheet(_SLIDER)
        self._sld_min.setMinimumWidth(80)
        self._sld_min.valueChanged.connect(self._on_range_changed)
        row2.addWidget(self._sld_min)

        self._lbl_min = QLabel(f"{getattr(self._rule, 'out_min', 0.0):.2f}")
        self._lbl_min.setStyleSheet(
            f"color:{FG_ACCENT};font-size:{_th.FS_SMALL}px;background:transparent;border:none;"
            f"min-width:38px;")
        row2.addWidget(self._lbl_min)

        sep = QLabel("~")
        sep.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        row2.addWidget(sep)

        self._sld_max = QSlider(Qt.Orientation.Horizontal)
        self._sld_max.setRange(-1000, 10000)
        self._sld_max.setValue(int(getattr(self._rule, 'out_max', 1.0) * 100))
        self._sld_max.setStyleSheet(_SLIDER_GREEN)
        self._sld_max.setMinimumWidth(80)
        self._sld_max.valueChanged.connect(self._on_range_changed)
        row2.addWidget(self._sld_max)

        self._lbl_max = QLabel(f"{getattr(self._rule, 'out_max', 1.0):.2f}")
        self._lbl_max.setStyleSheet(
            f"color:{FG_GREEN};font-size:{_th.FS_SMALL}px;background:transparent;border:none;"
            f"min-width:38px;")
        row2.addWidget(self._lbl_max)

        main_lay.addLayout(row2)

        # ── 第三行：平滑度滑块 ──
        row3 = QHBoxLayout()
        row3.setSpacing(6)

        lbl_smooth = QLabel("平滑:")
        lbl_smooth.setStyleSheet(
            f"color:{FG_DIM};font-size:{_th.FS_SMALL}px;background:transparent;border:none;")
        lbl_smooth.setFixedWidth(30)
        row3.addWidget(lbl_smooth)

        self._sld_smooth = QSlider(Qt.Orientation.Horizontal)
        self._sld_smooth.setRange(0, 100)
        self._sld_smooth.setValue(int(getattr(self._rule, 'smooth', 0.15) * 100))
        self._sld_smooth.setStyleSheet(_SLIDER_ORANGE)
        self._sld_smooth.setMinimumWidth(120)
        self._sld_smooth.valueChanged.connect(self._on_smooth_changed)
        row3.addWidget(self._sld_smooth)

        self._lbl_smooth = QLabel(
            f"{getattr(self._rule, 'smooth', 0.15):.0%}")
        self._lbl_smooth.setStyleSheet(
            f"color:{FG_ORANGE};font-size:{_th.FS_SMALL}px;background:transparent;border:none;"
            f"min-width:30px;")
        row3.addWidget(self._lbl_smooth)

        # 平滑模式：EMA(一阶) / FIR5(加权核,抗瞬时爆音)
        self._combo_smooth_mode = QComboBox()
        self._combo_smooth_mode.setFixedWidth(72)
        self._combo_smooth_mode.setToolTip(
            "平滑模式：\nEMA=一阶指数(响应快)\nFIR5=加权核(1,2,3,2,1)/9，"
            "抗瞬时爆音、保留强拍，自动包络更耐听")
        self._combo_smooth_mode.addItem("EMA", "ema")
        self._combo_smooth_mode.addItem("FIR5", "fir5")
        _smode = getattr(self._rule, 'smooth_mode', 'ema')
        _si = self._combo_smooth_mode.findData(_smode)
        if _si >= 0:
            self._combo_smooth_mode.setCurrentIndex(_si)
        self._combo_smooth_mode.currentIndexChanged.connect(self.changed)
        row3.addWidget(self._combo_smooth_mode)

        row3.addStretch()
        main_lay.addLayout(row3)

    def _on_range_changed(self):
        v_min = self._sld_min.value() / 100.0
        v_max = self._sld_max.value() / 100.0
        self._lbl_min.setText(f"{v_min:.2f}")
        self._lbl_max.setText(f"{v_max:.2f}")
        self.changed.emit()

    def _on_smooth_changed(self, val):
        self._lbl_smooth.setText(f"{val}%")
        self.changed.emit()

    def to_mod_rule(self):
        if not _HAS_MODULATOR:
            return None
        return ModRule(
            source=str(self._combo_source.currentData() or "rms"),
            param_key=str(self._combo_param.currentData() or "speed"),
            out_min=self._sld_min.value() / 100.0,
            out_max=self._sld_max.value() / 100.0,
            smooth=self._sld_smooth.value() / 100.0,
            enabled=self._cb_enabled.isChecked(),
            curve=str(self._combo_curve.currentData() or "linear"),
            smooth_mode=str(self._combo_smooth_mode.currentData() or "ema"),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  单种效果类型的调制配置面板 v2
# ═══════════════════════════════════════════════════════════════════════════
class TypeModPanelV2(QWidget):
    """v2版调制配置面板 - 滑块式 + 模板快捷添加"""
    changed = pyqtSignal()

    def __init__(self, track_type: str, parent=None):
        super().__init__(parent)
        self._track_type = track_type
        self._rule_cards: List[_ModRuleCardV2] = []
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # ── 启用开关 + 模板按钮行 ──
        header = QHBoxLayout()
        header.setSpacing(8)

        self._cb_enabled = QCheckBox("启用自动调制")
        self._cb_enabled.setStyleSheet(
            f"color:{FG_GREEN};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;")
        self._cb_enabled.toggled.connect(self.changed)
        header.addWidget(self._cb_enabled)

        header.addStretch()

        # 快捷模板按钮
        templates = _MOD_TEMPLATES.get(self._track_type, [])
        if templates:
            lbl_tpl = QLabel("快捷:")
            lbl_tpl.setStyleSheet(
                f"color:{FG_DIM};font-size:{_th.FS_SMALL}px;background:transparent;border:none;")
            header.addWidget(lbl_tpl)
            for tpl_name, src, param, out_min, out_max, smooth, curve in templates:
                btn = QPushButton(tpl_name)
                btn.setStyleSheet(_BTN_TEMPLATE)
                btn.setToolTip(f"{src} → {param} [{out_min}~{out_max}]")
                btn.clicked.connect(
                    lambda checked, s=src, p=param, mn=out_min, mx=out_max,
                           sm=smooth, cv=curve: self._add_from_template(s, p, mn, mx, sm, cv))
                header.addWidget(btn)

        btn_add = QPushButton("+ 自定义")
        btn_add.setStyleSheet(_BTN_ADD)
        btn_add.clicked.connect(lambda: self._add_rule_card())
        header.addWidget(btn_add)

        lay.addLayout(header)

        # ── 规则卡片滚动区 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL)
        container = QWidget()
        container.setStyleSheet(f"background:{BG_MAIN};")
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(4)
        self._cards_layout.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, 1)

    def _add_from_template(self, source, param_key, out_min, out_max, smooth, curve):
        """从模板添加规则"""
        if not _HAS_MODULATOR:
            return
        rule = ModRule(
            source=source, param_key=param_key,
            out_min=out_min, out_max=out_max,
            smooth=smooth, curve=curve, enabled=True,
        )
        self._add_rule_card(rule)

    def _add_rule_card(self, rule=None):
        card = _ModRuleCardV2(rule)
        card.changed.connect(self.changed)
        card.remove_requested.connect(self._remove_rule_card)
        self._rule_cards.append(card)
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self.changed.emit()

    def _remove_rule_card(self, card):
        if card in self._rule_cards:
            self._rule_cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self.changed.emit()

    def load_config(self, config):
        """加载TypeModConfig到UI"""
        if not _HAS_MODULATOR:
            return
        self._cb_enabled.blockSignals(True)
        self._cb_enabled.setChecked(config.enabled)
        self._cb_enabled.blockSignals(False)
        # 清除现有卡片
        for card in list(self._rule_cards):
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._rule_cards.clear()
        # 加载规则
        for rule in config.rules:
            self._add_rule_card(rule)

    def collect_config(self):
        """收集UI配置为TypeModConfig"""
        if not _HAS_MODULATOR:
            return None
        return TypeModConfig(
            track_type=self._track_type,
            enabled=self._cb_enabled.isChecked(),
            rules=[c.to_mod_rule() for c in self._rule_cards if c.to_mod_rule()],
        )


# ═══════════════════════════════════════════════════════════════════════════
#  自动调制主面板 v2（替换原有的_build_modulation_tab）
# ═══════════════════════════════════════════════════════════════════════════
class ModulationPanelV2(QWidget):
    """v2版自动调制面板 - 整合后的滑块式UI"""
    changed = pyqtSignal()

    def __init__(self, modulator=None, parent=None):
        super().__init__(parent)
        self._modulator = modulator
        self._type_panels: Dict[str, TypeModPanelV2] = {}
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ── 全局开关 + 说明 ──
        header = QFrame()
        header.setStyleSheet(
            f"background:{BG_PANEL};border-radius:{_th.RADIUS}px;border:1px solid #303030;")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 6, 12, 6)
        header_lay.setSpacing(10)

        self._cb_global = QCheckBox("启用音频参数自动调制")
        self._cb_global.setStyleSheet(
            f"color:{FG_GREEN};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        if self._modulator:
            self._cb_global.setChecked(self._modulator.global_enabled)
        self._cb_global.toggled.connect(self._on_global_toggled)
        header_lay.addWidget(self._cb_global)

        header_lay.addStretch()

        info = QLabel(
            "音频数据实时驱动效果参数变化 | "
            "点击快捷模板一键添加常用映射规则")
        info.setStyleSheet(
            f"color:{FG_DIM};font-size:{_th.FS_SMALL}px;background:transparent;border:none;")
        info.setWordWrap(True)
        header_lay.addWidget(info, 1)

        lay.addWidget(header)

        # ── 效果类型子Tab ──
        self._tabs = QTabWidget()

        try:
            from ...audio import TRACK_TYPES
        except ImportError:
            TRACK_TYPES = {
                "light": {"label": "灯光", "icon": "L"},
                "pump": {"label": "水泵", "icon": "P"},
                "motor": {"label": "电机", "icon": "M"},
                "valve": {"label": "阀门", "icon": "V"},
            }

        for tt, info_dict in TRACK_TYPES.items():
            if tt == "color":
                continue
            panel = TypeModPanelV2(tt)
            panel.changed.connect(self._on_panel_changed)
            self._type_panels[tt] = panel
            icon = info_dict.get("icon", "?")
            self._tabs.addTab(panel, f"[{icon}] {info_dict['label']}")

        lay.addWidget(self._tabs, 1)

    def _on_global_toggled(self, checked):
        if self._modulator:
            self._modulator.set_global_enabled(checked)
        self.changed.emit()

    def _on_panel_changed(self):
        self._sync_to_modulator()
        self.changed.emit()

    def _sync_to_modulator(self):
        if not self._modulator:
            return
        for tt, panel in self._type_panels.items():
            config = panel.collect_config()
            if config:
                self._modulator.set_config(config)

    def load_from_modulator(self):
        """从modulator加载配置到UI"""
        if not self._modulator:
            return
        self._cb_global.blockSignals(True)
        self._cb_global.setChecked(self._modulator.global_enabled)
        self._cb_global.blockSignals(False)
        for tt, panel in self._type_panels.items():
            config = self._modulator.get_config(tt)
            panel.load_config(config)

    def get_type_panels(self) -> Dict[str, TypeModPanelV2]:
        return self._type_panels
