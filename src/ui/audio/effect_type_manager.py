"""
effect_type_manager.py — 效果类型管理子页面 (v26)
=====================================================
功能：
  1. 按设备类型分Tab（灯光/泵/电机/阀门）
  2. 灯光细分：亮度类效果 / 颜色类效果
  3. 电机细分：交叉类 / 龙摆类 / 波形类 / 形象类 / 高级类
  4. 泵细分：跑泉类 / 波浪类 / 渐变类 / 脉冲类
  5. 分类随机配置：从已分好的效果类别中随机挑选
  6. 一键随机配置：直接设置效果数量并随机填充
  7. 配置导入/导出/保存/加载功能
"""
from __future__ import annotations
import json
import random
import os
from typing import Dict, List, Optional, Tuple
from PyQt6.QtCore import Qt, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QComboBox, QCheckBox,
    QFrame, QTabWidget, QScrollArea, QSpinBox,
    QSizePolicy, QGridLayout, QFileDialog, QMessageBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

from .. import qascade_theme as _th
from ..main_icon_library import load_tabler_icon

# ─── 颜色常量（引用主题令牌，不再自建十六进制）───────────────────────────────
BG_MAIN     = _th.BG_ROOT
BG_PANEL    = _th.BG_EDITOR
BG_HEADER   = _th.BG_EDITOR
BG_ITEM     = _th.BG_ROOT
FG_MAIN     = _th.TEXT_PRIMARY
FG_DIM      = _th.TEXT_SECONDARY
FG_ACCENT   = _th.FOCUS_BLUE
FG_GREEN    = _th.BRIGHT_SUCCESS
FG_ORANGE   = _th.BRIGHT_WARN
FG_RED      = _th.BRIGHT_ERROR

# ─── 样式表（通用外观交给全局主题接管，此处只留语义色特化）───────────────────
# 已删除 _BTN：与全局 QPushButton 外观重复，从未 setCheckable，无 checked
# 语义需要（§3 结构性统一）。
_BTN_GREEN = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_GREEN};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:4px 10px;font-size:11px;}}"
)
_BTN_ORANGE = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_ORANGE};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:4px 10px;font-size:11px;}}"
)
_BTN_RAND = (
    # 紫罗兰夜底退役→中性面+focus_blue 文字
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_ACCENT};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:4px 12px;font-size:11px;font-weight:bold;}}"
)
# 已删除 _COMBO/_SPIN/_TAB_INNER：与全局 QComboBox/QSpinBox/QTabBar 外观完全
# 重复（§3 结构性统一）。
# 列表行解剖：24px 行高；选中=select_blue 实底白字 + 左侧 focus_blue 竖条；
# 夜蓝悬停底 #0F0F2E 退役 → BG_PANEL。
_LIST = (
    f"QListWidget{{background:{_th.BG_ROOT};color:{FG_MAIN};"
    "border:1px solid rgba(255,255,255,17);"
    f"border-radius:{_th.RADIUS}px;font-size:11px;outline:none;}}"
    "QListWidget::item{min-height:24px;padding:0 8px;"
    "border-radius:3px;border-left:3px solid transparent;color:#CCCCCC;}"
    f"QListWidget::item:selected{{background:{_th.SELECT_BLUE};color:#FFFFFF;"
    f"border-left:3px solid {FG_ACCENT};}}"
    f"QListWidget::item:hover:!selected{{background:{_th.BG_BUTTON_HOVER};color:{FG_MAIN};}}"
)
# 已删除 _SCROLL / _GROUP：本文件无引用，且与全局主题 QSS 重复（§3 结构性统一）。

# ─── 效果分类映射 ───────────────────────────────────────────────────────────
# 根据category前缀自动分类
EFFECT_SUBCATEGORIES = {
    "light": {
        "亮度": ["灯光/亮度"],
        "颜色": ["灯光/颜色"],
        "跑灯": ["灯光/跑灯"],
        "闪烁": ["灯光/闪烁"],
        "开合": ["灯光/开合"],
    },
    "pump": {
        "跑泉": ["泵/跑泉"],
        "波浪": ["泵/波浪"],
        "渐变": ["泵/渐变"],
        "脉冲": ["泵/脉冲"],
    },
    "motor": {
        "龙摆": ["电机/龙摆"],
        "交叉": ["电机/交叉"],
        "开合": ["电机/开合"],
        "波形": ["电机/波形"],
        "造型": ["电机/造型"],
        "高级": ["电机/高级"],
        "Cue": ["电机/Cue"],
    },
    "valve": {
        "开合": ["阀门/开合"],
        "脉冲": ["阀门/脉冲"],
        "跑泉": ["阀门/跑泉"],
        "渐变": ["阀门/渐变"],
    },
}


def _get_presets_by_subcategory(track_type: str, subcategory: str) -> List[str]:
    """获取指定设备类型和子分类下的所有预设ID"""
    try:
        from ...ui.effect_engine import get_engine
        eng = get_engine()
        if eng is None:
            return []
        cat_prefixes = EFFECT_SUBCATEGORIES.get(track_type, {}).get(subcategory, [])
        result = []
        for p in eng.all_presets():
            for prefix in cat_prefixes:
                if p.category.startswith(prefix):
                    result.append(p.id)
                    break
        return result
    except Exception:
        return []


def _get_all_presets_for_type(track_type: str) -> List[str]:
    """获取指定设备类型的所有预设ID"""
    try:
        from ...ui.effect_engine import get_engine
        eng = get_engine()
        if eng is None:
            return []
        subcats = EFFECT_SUBCATEGORIES.get(track_type, {})
        all_prefixes = []
        for prefixes in subcats.values():
            all_prefixes.extend(prefixes)
        result = []
        for p in eng.all_presets():
            for prefix in all_prefixes:
                if p.category.startswith(prefix):
                    result.append(p.id)
                    break
        return result
    except Exception:
        return []


def _get_preset_display(preset_id: str) -> str:
    """获取预设的显示名称"""
    try:
        from ...ui.effect_engine import get_engine
        eng = get_engine()
        if eng is None:
            return preset_id
        p = eng.get_preset(preset_id)
        if p:
            return f"{p.name} [{p.category}]"
        return preset_id
    except Exception:
        return preset_id


# ═══════════════════════════════════════════════════════════════════════════
#  随机配置面板
# ═══════════════════════════════════════════════════════════════════════════
class RandomConfigPanel(QWidget):
    """分类随机配置面板：从已分好的效果类别中随机挑选"""
    config_changed = pyqtSignal(str, list)  # (track_type, [preset_ids])

    def __init__(self, track_type: str, parent=None):
        super().__init__(parent)
        self._track_type = track_type
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # ── 一键随机配置区 ──
        quick_frame = QFrame()
        quick_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:{_th.RADIUS}px;border:1px solid #303030;")
        quick_lay = QVBoxLayout(quick_frame)
        quick_lay.setContentsMargins(12, 10, 12, 10)
        quick_lay.setSpacing(8)

        title = QLabel("一键随机配置")
        title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        quick_lay.addWidget(title)

        # 效果数量设置
        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        count_row.addWidget(self._mk_label("结构级效果数量:"))
        self._spin_section_count = QSpinBox()
        self._spin_section_count.setRange(1, 200)
        self._spin_section_count.setValue(12)
        self._spin_section_count.setToolTip("结构级预设池数量：可一次随机 1~200 个效果，写入所有音乐段落")
        self._spin_section_count.setFixedWidth(74)
        count_row.addWidget(self._spin_section_count)

        count_row.addWidget(self._mk_label("小节级效果数量:"))
        self._spin_bar_count = QSpinBox()
        self._spin_bar_count.setRange(1, 300)
        self._spin_bar_count.setValue(24)
        self._spin_bar_count.setToolTip("小节级预设池数量：可一次随机 1~300 个效果，用于编曲回放的逐小节变化")
        self._spin_bar_count.setFixedWidth(74)
        count_row.addWidget(self._spin_bar_count)
        count_row.addStretch()
        quick_lay.addLayout(count_row)

        # 子分类选择
        subcat_row = QHBoxLayout()
        subcat_row.setSpacing(8)
        subcat_row.addWidget(self._mk_label("从以下分类中随机:"))

        subcats = EFFECT_SUBCATEGORIES.get(self._track_type, {})
        self._subcat_checks: Dict[str, QCheckBox] = {}
        for subcat_name in subcats.keys():
            cb = QCheckBox(subcat_name)
            cb.setChecked(True)
            cb.setStyleSheet(
                f"color:{FG_MAIN};font-size:10px;background:transparent;border:none;")
            self._subcat_checks[subcat_name] = cb
            subcat_row.addWidget(cb)
        subcat_row.addStretch()
        quick_lay.addLayout(subcat_row)

        # 随机按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_random_all = QPushButton(" 一键随机全部")
        btn_random_all.setIcon(load_tabler_icon("dice", 14, FG_ACCENT))
        btn_random_all.setStyleSheet(_BTN_RAND)
        btn_random_all.setToolTip("从选中的分类中随机填充所有段落的效果槽")
        btn_random_all.clicked.connect(self._on_random_all)
        btn_row.addWidget(btn_random_all)

        btn_random_section = QPushButton(" 随机结构级")
        btn_random_section.setIcon(load_tabler_icon("dice-5", 14, FG_MAIN))
        btn_random_section.setToolTip("只对段落级预设池按上方勾选分类重新随机抽取")
        btn_random_section.clicked.connect(lambda: self._on_random_level("section"))
        btn_row.addWidget(btn_random_section)

        btn_random_bar = QPushButton(" 随机小节级")
        btn_random_bar.setIcon(load_tabler_icon("arrows-shuffle", 14, FG_MAIN))
        btn_random_bar.setToolTip("只对小节级预设池按上方勾选分类重新随机抽取")
        btn_random_bar.clicked.connect(lambda: self._on_random_level("bar"))
        btn_row.addWidget(btn_random_bar)

        btn_row.addStretch()
        quick_lay.addLayout(btn_row)

        lay.addWidget(quick_frame)

        # ── 配置导入/导出区 ──
        io_frame = QFrame()
        io_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:{_th.RADIUS}px;border:1px solid #303030;")
        io_lay = QVBoxLayout(io_frame)
        io_lay.setContentsMargins(12, 10, 12, 10)
        io_lay.setSpacing(8)

        io_title = QLabel("配置管理")
        io_title.setStyleSheet(
            f"color:{FG_ORANGE};font-size:12px;font-weight:bold;"
            f"background:transparent;border:none;")
        io_lay.addWidget(io_title)

        io_btn_row = QHBoxLayout()
        io_btn_row.setSpacing(8)

        btn_export = QPushButton("导出配置")
        btn_export.setStyleSheet(_BTN_GREEN)
        btn_export.setToolTip("将当前配置导出为JSON文件")
        btn_export.clicked.connect(self._on_export)
        io_btn_row.addWidget(btn_export)

        btn_import = QPushButton("导入配置")
        btn_import.setStyleSheet(_BTN_ORANGE)
        btn_import.setToolTip("从JSON文件导入配置")
        btn_import.clicked.connect(self._on_import)
        io_btn_row.addWidget(btn_import)

        btn_save = QPushButton("保存为预设")
        btn_save.setToolTip("保存当前配置到内部预设库")
        btn_save.clicked.connect(self._on_save_preset)
        io_btn_row.addWidget(btn_save)

        btn_load = QPushButton("加载预设")
        btn_load.setToolTip("从内部预设库加载配置")
        btn_load.clicked.connect(self._on_load_preset)
        io_btn_row.addWidget(btn_load)

        io_btn_row.addStretch()
        io_lay.addLayout(io_btn_row)

        # 已保存的预设列表
        self._preset_list = QListWidget()
        self._preset_list.setStyleSheet(_LIST)
        self._preset_list.setMaximumHeight(100)
        self._refresh_saved_presets()
        io_lay.addWidget(self._preset_list)

        lay.addWidget(io_frame)

        # ── 当前配置预览 ──
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:{_th.RADIUS}px;border:1px solid #303030;")
        preview_lay = QVBoxLayout(preview_frame)
        preview_lay.setContentsMargins(12, 10, 12, 10)
        preview_lay.setSpacing(4)

        preview_title = QLabel("当前随机结果预览")
        preview_title.setStyleSheet(
            f"color:{FG_GREEN};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;")
        preview_lay.addWidget(preview_title)

        self._preview_label = QLabel("(尚未生成随机配置)")
        self._preview_label.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        self._preview_label.setWordWrap(True)
        preview_lay.addWidget(self._preview_label)

        lay.addWidget(preview_frame)
        lay.addStretch()

    def _mk_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        return lbl

    def _get_selected_presets(self) -> List[str]:
        """获取选中分类下的所有预设ID"""
        result = []
        for subcat_name, cb in self._subcat_checks.items():
            if cb.isChecked():
                presets = _get_presets_by_subcategory(self._track_type, subcat_name)
                result.extend(presets)
        return list(dict.fromkeys(result))

    def _pick_many(self, available: List[str], count: int) -> List[str]:
        """按轮洗牌抽取指定数量，优先避免短周期重复，数量超过候选池时自动循环补足。"""
        clean = list(dict.fromkeys([p for p in available if p]))
        if not clean or count <= 0:
            return []
        picks: List[str] = []
        while len(picks) < count:
            batch = clean[:]
            random.shuffle(batch)
            picks.extend(batch[: max(0, count - len(picks))])
        return picks[:count]

    def _on_random_all(self):
        """一键随机全部"""
        available = self._get_selected_presets()
        if not available:
            self._preview_label.setText("没有可用的效果预设！请检查分类选择。")
            return
        sec_count = self._spin_section_count.value()
        bar_count = self._spin_bar_count.value()
        section_picks = self._pick_many(available, sec_count)
        bar_picks = self._pick_many(available, bar_count)
        all_picks = section_picks + bar_picks
        # 更新预览
        preview_lines = []
        preview_lines.append(f"结构级({sec_count}): " +
                           ", ".join(_get_preset_display(p) for p in section_picks))
        preview_lines.append(f"小节级({bar_count}): " +
                           ", ".join(_get_preset_display(p) for p in bar_picks))
        self._preview_label.setText("\n".join(preview_lines))
        self.config_changed.emit(self._track_type, all_picks)

    def _on_random_level(self, level: str):
        """随机指定级别"""
        available = self._get_selected_presets()
        if not available:
            return
        if level == "section":
            count = self._spin_section_count.value()
        else:
            count = self._spin_bar_count.value()
        picks = self._pick_many(available, count)
        self.config_changed.emit(self._track_type, picks)
        display = ", ".join(_get_preset_display(p) for p in picks)
        self._preview_label.setText(f"{level}级随机({count}): {display}")

    def _on_export(self):
        """导出配置为JSON"""
        try:
            from ...audio import get_global_bridge
            bridge = get_global_bridge()
            data = bridge.to_dict()
            # 只导出当前类型的配置
            type_data = {
                "track_type": self._track_type,
                "preset_sets": data.get("preset_sets", {}).get(self._track_type, []),
                "switch_speed": data.get("switch_speed", 1.0),
            }
            path, _ = QFileDialog.getSaveFileName(
                self, "导出配置", f"bridge_config_{self._track_type}.json",
                "JSON Files (*.json)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(type_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _on_import(self):
        """从JSON导入配置"""
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, "导入配置", "", "JSON Files (*.json)")
            if not path:
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            from ...audio import get_global_bridge, SectionPresetSet
            bridge = get_global_bridge()
            sets_data = data.get("preset_sets", [])
            if sets_data:
                sets = [SectionPresetSet.from_dict(s) for s in sets_data]
                bridge.replace_preset_sets(self._track_type, sets)
            speed = data.get("switch_speed")
            if speed is not None:
                bridge.set_switch_speed(float(speed))
            self._preview_label.setText(f"已导入配置: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _on_save_preset(self):
        """保存当前配置到内部预设库"""
        try:
            from ...audio import get_global_bridge
            bridge = get_global_bridge()
            data = bridge.to_dict()
            type_data = {
                "track_type": self._track_type,
                "preset_sets": data.get("preset_sets", {}).get(self._track_type, []),
            }
            # 使用QSettings保存
            s = QSettings("Qascade", "BridgePresets")
            existing = json.loads(s.value("saved_presets", "[]", type=str))
            name = f"{self._track_type}_preset_{len(existing)+1}"
            existing.append({"name": name, "data": type_data})
            s.setValue("saved_presets", json.dumps(existing, ensure_ascii=False))
            s.sync()
            self._refresh_saved_presets()
            self._preview_label.setText(f"已保存预设: {name}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _on_load_preset(self):
        """从内部预设库加载配置"""
        try:
            item = self._preset_list.currentItem()
            if not item:
                QMessageBox.information(self, "提示", "请先选择一个预设")
                return
            idx = self._preset_list.row(item)
            s = QSettings("Qascade", "BridgePresets")
            existing = json.loads(s.value("saved_presets", "[]", type=str))
            if idx >= len(existing):
                return
            preset_data = existing[idx]["data"]
            from ...audio import get_global_bridge, SectionPresetSet
            bridge = get_global_bridge()
            sets_data = preset_data.get("preset_sets", [])
            if sets_data:
                sets = [SectionPresetSet.from_dict(s_d) for s_d in sets_data]
                bridge.replace_preset_sets(self._track_type, sets)
            self._preview_label.setText(f"已加载预设: {existing[idx]['name']}")
        except Exception as e:
            QMessageBox.warning(self, "加载失败", str(e))

    def _refresh_saved_presets(self):
        """刷新已保存的预设列表"""
        self._preset_list.clear()
        try:
            s = QSettings("Qascade", "BridgePresets")
            existing = json.loads(s.value("saved_presets", "[]", type=str))
            for preset in existing:
                if preset.get("data", {}).get("track_type") == self._track_type:
                    self._preset_list.addItem(preset["name"])
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  效果类型管理主页面
# ═══════════════════════════════════════════════════════════════════════════
class EffectTypeManagerWidget(QWidget):
    """效果类型管理的完整页面，包含分类浏览和随机配置功能"""
    config_applied = pyqtSignal()  # 配置已应用信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG_MAIN};color:{FG_MAIN};")
        self._random_panels: Dict[str, RandomConfigPanel] = {}
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 设备类型Tab
        self._type_tabs = QTabWidget()

        from ...audio import TRACK_TYPES
        type_icons = {"light": "L", "pump": "P", "motor": "M", "valve": "V"}
        type_labels = {"light": "灯光", "pump": "水泵/变频", "motor": "数控电机", "valve": "电磁阀"}

        for tt in ["light", "pump", "motor", "valve"]:
            info = TRACK_TYPES.get(tt, {})
            tab_w = self._build_type_tab(tt)
            icon = type_icons.get(tt, "?")
            label = type_labels.get(tt, tt)
            self._type_tabs.addTab(tab_w, f"[{icon}] {label}")

        lay.addWidget(self._type_tabs)

    def _build_type_tab(self, track_type: str) -> QWidget:
        """构建单个设备类型的管理Tab"""
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # 子Tab: 效果浏览 / 随机配置
        sub_tabs = QTabWidget()

        # 子Tab1: 效果分类浏览
        browse_w = self._build_browse_tab(track_type)
        sub_tabs.addTab(browse_w, "效果分类浏览")

        # 子Tab2: 随机配置
        random_panel = RandomConfigPanel(track_type)
        random_panel.config_changed.connect(self._on_random_config_changed)
        self._random_panels[track_type] = random_panel
        sub_tabs.addTab(random_panel, "随机配置")

        lay.addWidget(sub_tabs)
        return w

    def _build_browse_tab(self, track_type: str) -> QWidget:
        """构建效果分类浏览Tab"""
        w = QWidget()
        w.setStyleSheet(f"background:{BG_MAIN};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        subcats = EFFECT_SUBCATEGORIES.get(track_type, {})
        if not subcats:
            lay.addWidget(QLabel("暂无分类效果"))
            return w

        # 子分类Tab
        subcat_tabs = QTabWidget()

        for subcat_name, prefixes in subcats.items():
            subcat_w = QWidget()
            subcat_w.setStyleSheet(f"background:{BG_MAIN};")
            subcat_lay = QVBoxLayout(subcat_w)
            subcat_lay.setContentsMargins(4, 4, 4, 4)
            subcat_lay.setSpacing(4)

            # 效果列表
            list_w = QListWidget()
            list_w.setStyleSheet(_LIST)
            list_w.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)

            # 填充效果
            presets = _get_presets_by_subcategory(track_type, subcat_name)
            for pid in presets:
                display = _get_preset_display(pid)
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, pid)
                list_w.addItem(item)

            subcat_lay.addWidget(list_w, 1)

            # 操作按钮
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            lbl_count = QLabel(f"共 {len(presets)} 个效果")
            lbl_count.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
            btn_row.addWidget(lbl_count)
            btn_row.addStretch()
            subcat_lay.addLayout(btn_row)

            subcat_tabs.addTab(subcat_w, subcat_name)

        lay.addWidget(subcat_tabs)
        return w

    def _on_random_config_changed(self, track_type: str, preset_ids: list):
        """随机配置变更回调：把随机池写入音频桥接器，确保编曲回放可直接使用。"""
        try:
            from ...audio import get_global_bridge
            from ...audio.music_to_effect_bridge import SectionPresetSet, PresetPoolEntry, SectionLabel, TRACK_TYPES
            bridge = get_global_bridge()
            ids = list(dict.fromkeys([str(pid) for pid in (preset_ids or []) if pid]))
            if ids:
                # 随机结果前半用于结构级，后半用于小节级；数量很大时保留完整池，供回放逐次抽取。
                split = max(1, len(ids) // 2)
                section_ids = ids[:split]
                bar_ids = ids[split:] or ids
                ch_types = list(TRACK_TYPES.get(track_type, {}).get("ch_types", []))
                sections = [
                    SectionLabel.INTRO, SectionLabel.VERSE, SectionLabel.BUILD,
                    SectionLabel.CHORUS, SectionLabel.DROP, SectionLabel.BREAKDOWN,
                    SectionLabel.OUTRO,
                ]
                sets = []
                for sec in sections:
                    sets.append(SectionPresetSet(
                        section=sec,
                        track_type=track_type,
                        section_presets=[PresetPoolEntry(pid) for pid in section_ids],
                        bar_presets=[PresetPoolEntry(pid) for pid in bar_ids],
                        ch_types=ch_types,
                        sort_mode="index",
                        enabled=True,
                    ))
                bridge.replace_preset_sets(track_type, sets)
        except Exception:
            # UI 随机预览不能因桥接器暂未初始化而中断。
            pass
        self.config_applied.emit()
