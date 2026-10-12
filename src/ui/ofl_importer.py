# -*- coding: utf-8 -*-
"""
ofl_importer.py — Open Fixture Library (通用) Importer
Provides 通用ImporterDialog for browsing and importing fixture definitions
into Qascade as FixtureDefinition objects.
"""
from __future__ import annotations
import logging
import uuid
from typing import Dict, List, Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QLineEdit, QComboBox,
    QListWidget, QListWidgetItem, QTextEdit,
    QDialogButtonBox, QMessageBox, QCheckBox, QWidget,
)
from PyQt6.QtCore import Qt
from ..core.models import FixtureDefinition
from . import qascade_theme as _th

logger = logging.getLogger(__name__)

# --- channel template -> Qascade mapping ---
TEMPLATE_TO_QASCADE = {
    "Intensity": "dimmer", "Master": "dimmer",
    "Red": "R", "Green": "G", "Blue": "B", "White": "W",
    "Amber": "W", "UV": "W", "Lime": "W", "Cyan": "W", "Magenta": "W", "Yellow": "W",
    "Pan": "motor_x", "Tilt": "motor_y", "Pan/Tilt": "motor_x",
}
FINE_SUFFIXES = (" fine", " fine 16 bit", " fine 8 bit", " fine 16bit")
CAP_TYPE_LABEL = {
    "Intensity": "I", "Color": "C", "Strobe": "Strobe", "Shutter": "Shut",
    "Prism": "Prism", "Gobo": "Gobo", "Frost": "Frost", "Zoom": "Zoom",
    "Focus": "Focus", "Pan": "Pan", "Tilt": "Tilt", "Speed": "Spd",
    "Effect": "Fx", "Maintenance": "Mnt", "Rotation": "Rot",
}

def _resolve_channel(ofl: dict, ref: str) -> str:
    tpl = ofl.get("templateChannels", {}) or {}
    if ref in tpl:
        return str(tpl[ref].get("channel") or tpl[ref].get("fineChannel") or ref)
    return ref

def _fine_aliases(ch_def: dict) -> List[str]:
    return [str(a) for a in (ch_def.get("fineChannelAliases", []) or [])]

def _is_fine(name: str) -> bool:
    return any(name.lower().endswith(s) for s in FINE_SUFFIXES)

def _cap_label(ch_def: dict) -> str:
    caps = ch_def.get("capabilities", []) or []
    if not caps and ch_def.get("capability"):
        caps = [ch_def["capability"]]
    parts = []
    for c in caps[:4]:
        r = c.get("range", [0, 255])
        t = CAP_TYPE_LABEL.get(c.get("type", ""), (c.get("type", "")[:5] or "?"))
        parts.append(f"{r[0]}-{r[1]}={t}")
    if len(caps) > 4:
        parts.append(f"...+{len(caps)-4}")
    return ", ".join(parts)

# --- JSON -> FixtureDefinition ---
def ofl_to_fixture_definitions(ofl_data: dict) -> List[FixtureDefinition]:
    name = str(ofl_data.get("name", "Fixture") or "")
    avail = ofl_data.get("availableChannels", {}) or {}
    modes = ofl_data.get("modes", []) or []
    if not modes:
        modes = [{"name": "Default", "channels": list(avail.keys())}]
    results, fn_counter = [], 0
    for mi, mode in enumerate(modes):
        mode_name = mode.get("name", f"M{mi+1}")
        q_chan, fn_names, fn_defaults = [], {}, {}
        for raw_ref in mode.get("channels", []):
            resolved = _resolve_channel(ofl_data, str(raw_ref))
            cdef = avail.get(resolved, {}) or {}
            if _is_fine(resolved):
                # Determine if tilt or pan fine
                base = resolved.lower()
                for s in FINE_SUFFIXES:
                    if base.endswith(s):
                        base = base[:-len(s)]
                        break
                ftype = "motor_y_fine" if "tilt" in base else "motor_x_fine"
                q_chan.append(ftype)
                fn_defaults[ftype] = max(0, min(255, int(cdef.get("defaultValue", 0) or 0)))
                continue
            qtype = TEMPLATE_TO_QASCADE.get(str(raw_ref))
            if qtype is None:
                fn_counter += 1; qtype = f"function_{fn_counter}"
                cl = _cap_label(cdef)
                fn_names[qtype] = f"{resolved} [{cl}]" if cl else resolved
                fn_defaults[qtype] = max(0, min(255, int(cdef.get("defaultValue", 0) or 0)))
            q_chan.append(qtype)
            for fa in _fine_aliases(cdef):
                fd = avail.get(fa, {}) or {}
                ft = "motor_y_fine" if "tilt" in str(fa).lower() else "motor_x_fine"
                q_chan.append(ft)
                fn_defaults[ft] = max(0, min(255, int(fd.get("defaultValue", 0) or 0)))
        if not q_chan:
            q_chan = ["placeholder"]
        display = f"{name} ({mode_name})" if len(modes) > 1 else name
        fd = FixtureDefinition(
            name=display, channel_count=len(q_chan), channels=q_chan,
            function_channel_names=fn_names or {},
            function_channel_defaults=fn_defaults or {},
        )
        meta = {"ofl_name": name, "ofl_categories": ofl_data.get("categories", []),
                "ofl_mode": mode_name, "ofl_source": "ofl"}
        setattr(fd, "_ofl_meta", meta)
        results.append(fd)
    return results

# --- Built-in cache (50 fixtures, compact format) ---
# Format: (manufacturer, name, category_code, [(mode_name, [channels...]), ...])
# Category codes: C=Color Changer, H=Moving Head, S=Strobe, L=Laser
#   F=Follow Spot, M=Matrix, B=Blinder, K=Smoke, E=Effect
FIXTURE_DATA = [
    ("Generic", "LED Par 64 18x12W", "C", [
        ("6ch",  ["dimmer","R","G","B","W","function_Strobe"]),
        ("8ch",  ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro","function_AutoSpeed"]),
    ]),
    ("Generic", "LED Par 54 7x10W", "C", [("7ch", ["dimmer","R","G","B","W","function_ColorMacro","function_Strobe"])]),
    ("Generic", "LED Par 36 3W", "C", [("4ch", ["dimmer","R","G","B"]), ("6ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro"])]),
    ("Generic", "RGBWA UV Par 6ch", "C", [("6ch", ["dimmer","R","G","B","W","W"]), ("10ch", ["dimmer","R","G","B","W","W","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "Flat Par 12x8W RGBWA", "C", [("6ch", ["dimmer","R","G","B","W","W"]), ("11ch", ["dimmer","R","G","B","W","W","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode","function_DimCurve"])]),
    ("Generic", "Wash Light Zoom 19x15W", "C", [("12ch", ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro","function_Zoom","function_Focus","motor_x","motor_y","function_Speed"]), ("21ch", ["dimmer","R","G","B","W","function_RFine","function_GFine","function_BFine","function_WFine","function_Strobe","function_ColorMacro","function_Zoom","motor_x_fine","function_Focus","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset","function_LampCtrl"])]),
    ("Generic", "LED Wash Light 7x12W", "C", [("8ch", ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro","function_AutoSpeed"])]),
    ("Generic", "COB Par 100W", "C", [("4ch", ["dimmer","function_CCT","G","M"])]),
    ("Generic", "Moving Head Beam 7R", "H", [
        ("16ch", ["dimmer","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Prism","function_PrismRotate","function_Focus","function_Zoom","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset","function_LampCtrl"]),
        ("14ch", ["dimmer","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Prism","function_Focus","function_Zoom","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset"]),
    ]),
    ("Generic", "Moving Head Spot 150W", "H", [("18ch", ["dimmer","motor_x_fine","function_Strobe","function_ColorWheel","function_ColorWheelFine","function_GoboWheel","function_GoboRotate","function_Prism","function_PrismRotate","function_Frost","function_Focus","function_Zoom","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset"])]),
    ("Generic", "Moving Head Wash 19x15W", "H", [("22ch", ["dimmer","motor_x_fine","R","G","B","W","function_Strobe","function_ColorMacro","function_ColorWheel","function_Zoom","motor_x_fine","function_Focus","motor_x_fine","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset","function_LampCtrl","function_FanMode","function_AutoMode"])]),
    ("Generic", "Mini Moving Head 60W", "H", [("12ch", ["dimmer","function_Strobe","R","G","B","function_ColorMacro","function_GoboWheel","function_Focus","motor_x","motor_x_fine","motor_y","motor_y_fine"]), ("8ch", ["dimmer","function_Strobe","function_ColorMacro","function_GoboWheel","motor_x","motor_x_fine","motor_y","motor_y_fine"])]),
    ("Generic", "Beam Moving Head 230W", "H", [("16ch", ["dimmer","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Prism","function_PrismRotate","function_Frost","function_Focus","function_Zoom","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset"])]),
    ("Generic", "Hybrid Moving Head 280W", "H", [("20ch", ["dimmer","motor_x_fine","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Prism","function_PrismRotate","function_Frost","function_Focus","motor_x_fine","function_Zoom","motor_x_fine","motor_x","motor_x_fine","motor_y","motor_y_fine","function_Speed","function_Reset","function_LampCtrl"])]),
    ("Generic", "LED Bar 6x18W RGBWA", "C", [("6ch", ["dimmer","R","G","B","W","W"]), ("12ch", ["dimmer","R","G","B","W","W","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode","function_DimCurve","function_MasterDim"])]),
    ("Generic", "LED Bar 12x10W RGB", "C", [("4ch", ["dimmer","R","G","B"]), ("8ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "LED Batten 1m RGBWA", "C", [("5ch", ["dimmer","R","G","B","W"]), ("9ch", ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "LED Pixel Bar 10x12W", "M", [("10ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode","function_AutoSens","function_DimCurve"]), ("30ch", ["R","G","B","R","G","B","R","G","B","R","G","B","R","G","B","R","G","B","R","G","B","R","G","B","R","G","B","R","G","B"])]),
    ("Generic", "LED Curtain 8x8 RGB", "M", [("8ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "Strobe LED 100W", "S", [("2ch", ["dimmer","function_StrobeSpeed"]), ("4ch", ["dimmer","function_StrobeSpeed","function_BlinderDim","function_BlinderStrobe"])]),
    ("Generic", "Strobe Double LED 200W", "S", [("3ch", ["dimmer","function_StrobeSpeed","function_Mode"]), ("5ch", ["function_DimL","function_StrobeL","function_DimR","function_StrobeR","function_Mode"])]),
    ("Generic", "Strobe White 3000W", "S", [("1ch", ["dimmer"]), ("2ch", ["dimmer","function_StrobeSpeed"])]),
    ("Generic", "Strobe Bar 6Cell RGB", "S", [("5ch", ["dimmer","function_StrobeSpeed","function_ColorMacro","function_AutoSpeed","function_Microphone"]), ("18ch", ["dimmer","R","G","B","dimmer","R","G","B","dimmer","R","G","B","dimmer","R","G","B","function_StrobeMaster","function_Mode"])]),
    ("Generic", "Laser RGB 500mW", "L", [("4ch", ["R","G","B","function_Pattern"]), ("7ch", ["R","G","B","function_Pattern","function_PatternRotate","function_Strobe","function_Mode"])]),
    ("Generic", "Laser Single Green 200mW", "L", [("3ch", ["dimmer","function_Pattern","function_PatternRotate"]), ("5ch", ["dimmer","function_Pattern","function_PatternRotate","function_Strobe","function_Mode"])]),
    ("Generic", "Laser RGB Full Color 1W", "L", [("8ch", ["R","G","B","function_Pattern","function_PatternRotate","function_Strobe","function_Zoom","function_Mode"])]),
    ("Generic", "Follow Spot 200W LED", "F", [("6ch", ["dimmer","function_Strobe","function_ColorWheel","function_GoboWheel","function_Zoom","function_Focus"]), ("10ch", ["dimmer","motor_x_fine","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Frost","function_Zoom","motor_x_fine","function_Focus"])]),
    ("Generic", "Follow Spot 400W Discharge", "F", [("4ch", ["dimmer","function_Strobe","function_Zoom","function_Focus"])]),
    ("Generic", "Follow Spot 100W LED", "F", [("8ch", ["dimmer","motor_x_fine","function_Strobe","function_ColorWheel","function_GoboWheel","function_Frost","function_Zoom","function_Focus"])]),
    ("Generic", "LED Matrix 5x5 RGB", "M", [("25ch", ["R","G","B"]*8+["R"]), ("75ch", ["R","G","B"]*25)]),
    ("Generic", "Blinder 4x100W LED", "B", [("4ch", ["dimmer","dimmer","dimmer","dimmer"]), ("8ch", ["dimmer","dimmer","dimmer","dimmer","R","G","B","function_Mode"]), ("12ch", ["dimmer","R","G","B","dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "Smoke Machine 1500W", "K", [("2ch", ["valve","pump"]), ("4ch", ["valve","pump","function_FanSpeed","function_Mode"])]),
    ("Generic", "Hazer 900W", "K", [("1ch", ["pump"]), ("2ch", ["pump","function_FanSpeed"])]),
    ("Generic", "Bubble Machine 600W", "K", [("2ch", ["pump","function_FanSpeed"]), ("3ch", ["pump","function_FanSpeed","function_Rotation"])]),
    ("Generic", "Snow Machine 1000W", "K", [("1ch", ["pump"]), ("2ch", ["pump","function_FanSpeed"])]),
    ("Generic", "CO2 Jet 6m", "K", [("1ch", ["valve"])]),
    ("Generic", "Flame Projector 3m", "E", [("1ch", ["valve"]), ("2ch", ["valve","function_Ignition"])]),
    ("Generic", "Moonflower LED 36x1W", "E", [("7ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed"]), ("14ch", ["R","G","B","function_RFade","function_GFade","function_BFade","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode","motor_x","motor_y","function_Speed","function_Reset"])]),
    ("Generic", "Derby Effect LED 8x3W", "E", [("6ch", ["dimmer","R","G","B","function_Strobe","function_AutoSpeed"]), ("12ch", ["dimmer","R","G","B","function_Motor1","function_Motor2","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode","function_Sound","function_Reset"])]),
    ("Generic", "Scanner Effect 100W", "E", [("9ch", ["dimmer","function_Strobe","function_ColorWheel","function_GoboWheel","function_GoboRotate","function_Prism","function_Focus","motor_x","motor_y"])]),
    ("Generic", "LED Ring Light 60cm RGB", "E", [("9ch", ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "Moving Mirror Effect", "E", [("8ch", ["motor_x","motor_y","function_Speed","dimmer","function_Strobe","function_ColorWheel","function_Pattern","function_Reset"])]),
    ("Generic", "UV Blacklight Bar 6x40W", "E", [("1ch", ["dimmer"]), ("2ch", ["dimmer","function_Strobe"])]),
    ("Generic", "Strip Light 8ch RGB", "C", [("8ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed","function_AutoMode"])]),
    ("Generic", "LED Stick 1m RGB", "C", [("4ch", ["dimmer","R","G","B"]), ("7ch", ["dimmer","R","G","B","function_Strobe","function_ColorMacro","function_AutoSpeed"])]),
    ("Generic", "IP65 Par Light 9x12W", "C", [("7ch", ["dimmer","R","G","B","W","function_Strobe","function_ColorMacro"]), ("11ch", ["dimmer","R","G","B","W","motor_x_fine","function_Strobe","function_ColorMacro","function_AutoMode","function_AutoSpeed","function_DimCurve"])]),
]
_CATEGORY_LABELS = {"C": "颜色灯", "H": "摇头灯", "S": "频闪灯",
    "L": "激光灯", "F": "追光灯", "M": "矩阵灯", "B": "观众灯", "K": "烟机/雾机", "E": "效果灯"}
_MANUFACTURERS = ["Generic"]

# Channel label mapping for built-in function_XXX names
_CH_LABELS = {
    "Strobe": "Strobe", "StrobeSpeed": "Strobe Speed", "ColorMacro": "Color Macro",
    "AutoSpeed": "Auto Speed", "AutoMode": "Auto Mode", "Focus": "Focus", "Zoom": "Zoom",
    "ColorWheel": "Color Wheel", "GoboWheel": "Gobo Wheel", "GoboRotate": "Gobo Rotate",
    "Prism": "Prism", "PrismRotate": "Prism Rotate", "Frost": "Frost", "Speed": "Speed",
    "Reset": "Reset", "LampCtrl": "Lamp Control", "FanSpeed": "Fan Speed",
    "MasterDim": "Master Dimmer", "DimCurve": "Dim Curve", "Sound": "Sound Sensitivity",
    "Pattern": "Pattern", "PatternRotate": "Pattern Rotate", "Fade": "Fade",
    "CCT": "CCT", "Ignition": "Ignition", "Mode": "Mode", "Rotation": "Rotation",
    "FanMode": "Fan Mode", "ColorWheelFine": "Color Wheel Fine",
    "Microphone": "Microphone", "AutoSens": "Auto Sensitivity",
    "StrobeMaster": "Strobe Master", "BlinderDim": "Blinder Dimmer",
    "BlinderStrobe": "Blinder Strobe", "StrobeL": "Strobe Left", "StrobeR": "Strobe Right",
    "DimL": "Dimmer Left", "DimR": "Dimmer Right", "Motor1": "Motor 1", "Motor2": "Motor 2",
    "RFade": "R Fade", "GFade": "G Fade", "BFade": "B Fade",
    "RFine": "R Fine", "GFine": "G Fine", "BFine": "B Fine", "WFine": "W Fine",
    "LampOnOff": "Lamp On/Off",
}

def _chname(key: str) -> str:
    """Convert function_XXX to human-readable label."""
    return _CH_LABELS.get(key, key.replace("_", " ").title())

# --- Built-in expansion ---
def _expand_builtin() -> List[FixtureDefinition]:
    """Generate FixtureDefinitions from compact built-in data."""
    results, fn_counter = [], [0]
    for manu, name, cat, modes in FIXTURE_DATA:
        for mi, (mname, raw_chan) in enumerate(modes):
            qc, fnn, fnd = [], {}, {}
            for ch in raw_chan:
                if ch in ("dimmer","R","G","B","W","valve","pump","motor_x","motor_y","motor_z","motor_x_fine","motor_y_fine","motor_z_fine"):
                    qc.append(ch)
                elif "_" in ch and ch.split("_")[1] in ("1","2","3","4","5","6","7","8","9","10"):
                    qc.append(ch.replace("function_", ""))  # Handle per-cell channels
                else:
                    fn_counter[0] += 1
                    qt = f"function_{fn_counter[0]}"
                    label_key = ch.replace("function_", "")
                    fnn[qt] = _chname(label_key)
                    fnd[qt] = 0
                    qc.append(qt)
            if not qc:
                qc = ["placeholder"]
            display = f"{name} ({mname})" if len(modes) > 1 else name
            fd = FixtureDefinition(
                name=display, channel_count=len(qc), channels=qc,
                function_channel_names=fnn or {},
                function_channel_defaults=fnd or {},
            )
            setattr(fd, "_ofl_meta", {"ofl_name": name, "ofl_categories": [cat],
                                       "ofl_mode": mname, "ofl_source": "builtin"})
            results.append(fd)
    return results

def _search_builtin(query: str, manu: str, cat: str) -> List[FixtureDefinition]:
    """Search built-in fixtures by query, manufacturer, category."""
    q = query.strip().lower()
    all_defs = _expand_builtin()
    filt = []
    for fd in all_defs:
        m = getattr(fd, "_ofl_meta", {}) or {}
        fm = "Generic"; fc = m.get("ofl_categories", [""])[0] if m.get("ofl_categories") else ""
        if manu and manu != "ALL" and fm != manu:
            continue
        if cat and cat != "ALL" and fc != cat:
            continue
        if q and q not in fd.name.lower() and q not in fm.lower():
            continue
        filt.append(fd)
    return filt

# --- UI ---
# 旧版独立 STYLE（QWidget{} 通配 QSS）已整体删除：它会打穿对话框内所有子控件、
# 叠加覆盖全局主题；通用外观现完全由 qascade_theme.GLOBAL_QSS 提供，本页不再造轮子。

class 通用ImporterDialog(QDialog):
    """Dialog for browsing and importing fixture definitions."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_defs: List[FixtureDefinition] = []
        self._selected_defs: List[FixtureDefinition] = []
        self.setWindowTitle("Open Fixture Library 设备导入")
        self.setMinimumSize(760, 500)
        self._build_ui()
        self._load_fixtures()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setSpacing(8); lay.setContentsMargins(10, 10, 10, 10)
        # Search bar
        srch = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("按名称搜索设备…")
        self._search_input.textChanged.connect(self._apply_filters)
        srch.addWidget(self._search_input, 2)
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("全部类别", "ALL")
        for ck, cl in sorted(_CATEGORY_LABELS.items()):
            self._cat_combo.addItem(cl, ck)
        self._cat_combo.currentIndexChanged.connect(self._apply_filters)
        srch.addWidget(QLabel("类别:")); srch.addWidget(self._cat_combo, 1)
        lay.addLayout(srch)
        # Splitter
        sp = QSplitter(Qt.Orientation.Horizontal)
        # Left: list
        lw = QWidget(); ll = QVBoxLayout(lw); ll.setContentsMargins(0, 0, 0, 0)
        self._fixture_list = QListWidget()
        self._fixture_list.setAlternatingRowColors(True)
        self._fixture_list.currentRowChanged.connect(self._on_select)
        ll.addWidget(self._fixture_list)
        self._batch_check = QCheckBox("导入全部模式")
        self._batch_check.setToolTip("一次性导入所选设备的全部模式。")
        ll.addWidget(self._batch_check)
        sp.addWidget(lw)
        # Right: preview
        rw = QWidget(); rl = QVBoxLayout(rw); rl.setContentsMargins(6, 0, 0, 0)
        self._preview_name = QLabel("选择设备以预览")
        self._preview_name.setStyleSheet(f"font-size:14px;font-weight:bold;color:{_th.FOCUS_BLUE};")
        self._preview_name.setWordWrap(True)
        rl.addWidget(self._preview_name)
        self._preview_info = QLabel(""); self._preview_info.setWordWrap(True)
        self._preview_info.setStyleSheet(f"color:{_th.TEXT_PRIMARY};")
        rl.addWidget(self._preview_info)
        self._preview_chan = QTextEdit(); self._preview_chan.setReadOnly(True)
        self._preview_chan.setMaximumHeight(220)
        self._preview_chan.setPlaceholderText("通道映射…")
        rl.addWidget(self._preview_chan)
        sp.addWidget(rw); sp.setStretchFactor(0, 1); sp.setStretchFactor(1, 1)
        lay.addWidget(sp, 1)
        # Buttons
        btns = QHBoxLayout()
        self._import_btn = QPushButton("导入选中")
        self._import_btn.setStyleSheet("QPushButton{font-weight:bold;}")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        close_btn = QPushButton("关闭"); close_btn.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(self._import_btn); btns.addWidget(close_btn)
        lay.addLayout(btns)

    def _load_fixtures(self):
        self._all_defs = _expand_builtin()
        self._refresh_list()

    def _refresh_list(self, filter_defs: Optional[List[FixtureDefinition]] = None):
        self._fixture_list.blockSignals(True)
        self._fixture_list.clear()
        defs = filter_defs if filter_defs is not None else self._all_defs
        for fd in defs:
            m = getattr(fd, "_ofl_meta", {}) or {}
            cats = m.get("ofl_categories", [])
            cat_str = _CATEGORY_LABELS.get(cats[0], "") if cats else ""
            tip = f"类别：{cat_str} | 通道数：{fd.channel_count}" if cat_str else f"通道数：{fd.channel_count}"
            item = QListWidgetItem(f"{fd.name}  [{fd.channel_count}ch]")
            item.setData(Qt.ItemDataRole.UserRole, id(fd))
            item.setData(Qt.ItemDataRole.ToolTipRole, tip)
            self._fixture_list.addItem(item)
        self._fixture_list.blockSignals(False)
        if self._fixture_list.count() > 0:
            self._fixture_list.setCurrentRow(0)

    def _apply_filters(self):
        q = self._search_input.text(); c = str(self._cat_combo.currentData() or "ALL")
        self._refresh_list(_search_builtin(q, "ALL", c))

    def _on_select(self, row: int):
        item = self._fixture_list.item(row)
        if item is None:
            self._import_btn.setEnabled(False)
            self._preview_name.setText("选择设备以预览")
            self._preview_info.setText(""); self._preview_chan.clear(); self._selected_defs = []
            return
        obj_id = item.data(Qt.ItemDataRole.UserRole)
        matched = [d for d in self._all_defs if id(d) == obj_id]
        if not matched:
            return
        fd = matched[0]
        self._selected_defs = [fd]
        meta = getattr(fd, "_ofl_meta", {}) or {}
        ofl_name = meta.get("ofl_name", fd.name)
        same = [d for d in self._all_defs if getattr(d, "_ofl_meta", {}).get("ofl_name") == ofl_name]
        self._batch_check.show() if len(same) > 1 else self._batch_check.hide()
        self._import_btn.setEnabled(True)
        self._show_preview(fd)

    def _show_preview(self, fd: FixtureDefinition):
        meta = getattr(fd, "_ofl_meta", {}) or {}
        cats = meta.get("ofl_categories", [])
        cat_str = _CATEGORY_LABELS.get(cats[0], ", ".join(cats)) if cats else "-"
        self._preview_name.setText(meta.get("ofl_name", fd.name))
        self._preview_info.setText(
            f"<b>类别：</b>{cat_str}<br><b>模式：</b>{meta.get('ofl_mode','')}<br>"
            f"<b>通道数：</b>{fd.channel_count}<br><b>来源：</b>内置缓存")
        self._preview_info.setTextFormat(Qt.TextFormat.RichText)
        fnn = getattr(fd, "function_channel_names", {}) or {}
        fnd = getattr(fd, "function_channel_defaults", {}) or {}
        rows = ["<table><tr><th>#</th><th>Type</th><th>Default</th>" + ("<th>Label</th>" if fnn else "") + "</tr>"]
        for i, ct in enumerate(fd.channels):
            dflt = str(fnd.get(ct, "255" if ct == "dimmer" else ""))
            lbl = f"<td>{fnn[ct]}</td>" if ct in fnn else ""
            rows.append(f"<tr><td>{i+1}</td><td>{ct}</td><td>{dflt}</td>{lbl}</tr>")
        rows.append("</table>")
        self._preview_chan.setHtml("\n".join(rows))

    def _do_import(self):
        if self._batch_check.isChecked() and self._batch_check.isVisible() and self._selected_defs:
            ofl_name = getattr(self._selected_defs[0], "_ofl_meta", {}).get("ofl_name", "")
            to_import = [d for d in self._all_defs if getattr(d, "_ofl_meta", {}).get("ofl_name") == ofl_name]
        else:
            to_import = list(self._selected_defs)
        if not to_import:
            return QMessageBox.information(self, "导入", "未选择任何设备。")
        existing = getattr(self, "_existing_names", set())
        new = [d for d in to_import if d.name not in existing]
        if not new:
            return QMessageBox.information(self, "导入", "所有设备均已存在（名称重复）。")
        ret = QMessageBox.question(self, "确认导入",
            f"确定导入 <b>{len(new)}</b> 个新设备？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._result_defs = new
        self.accept()

    def get_imported_defs(self) -> List[FixtureDefinition]:
        return getattr(self, "_result_defs", self._selected_defs)

    @staticmethod
    def import_to_project(project, parent=None) -> bool:
        existing = {fd.name for fd in getattr(project, "fixture_defs", [])}
        dlg = 通用ImporterDialog(parent)
        dlg._existing_names = existing
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        for fd in dlg.get_imported_defs():
            if fd.name not in existing:
                project.fixture_defs.append(fd)
                existing.add(fd.name)
        return True
