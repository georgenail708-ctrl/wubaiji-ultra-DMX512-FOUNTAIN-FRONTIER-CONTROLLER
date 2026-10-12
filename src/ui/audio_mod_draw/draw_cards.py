# -*- coding: utf-8 -*-
"""Draw 大页卡片目录（R182）—— 源卡规格 + 效果卡模型（唯一数据来源）。

两类节点：
  · **源卡**（kind="source"）：可选的外部调制信号（feature）+ 预设模板；
  · **效果卡**（kind="rule"）：一个**通用音频效果实例**（效果库见
    `draw_effects.EFFECT_CATALOG`，85 种预设），携带完整参数表 params 与
    目标通道列表 ch_types（按 clip 含有的通道勾选；任何效果可调制任意类型）。

R182 重构（用户规格）：效果卡不再绑定单一通道类型——`effect_card_node()`
是唯一构造点；`rule_card_node()` 保留为兼容包装（旧测试/旧调用）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ...engine.audio_mod_draw_compile import SOURCE_KINDS
from ..qascade_theme import (
    BRIGHT_INFO,
    BRIGHT_SUCCESS,
)
from .draw_effects import (EFFECT_CATALOG, EFFECT_CARD_H, EFFECT_CARD_W,
                           default_params, effect_icon, effect_label)

#: 源卡目录：key → (显示名, feature, 说明, tabler 图标名)
SOURCE_CATALOG: Dict[str, tuple] = {
    "src_beat": ("节拍强度", "beat_strength", "跟拍网格取强度，鼓点处最亮", "wave-sine"),
    "src_bpm": ("BPM 相位", "bpm_phase", "按 BPM 循环的相位斜坡，做呼吸/匀速律动", "clock"),
    "src_low": ("低频能量", "band_low", "低频段能量，水景/泵的涌动感", "wave-saw-tool"),
    "src_high": ("高频能量", "band_high", "高频段能量，镲片/闪烁响应", "wave-square"),
    "src_ratio": ("频段能量比", "band_ratio", "低/高频能量比，旋律性起伏", "adjustments"),
}

#: 预设模板：key → (显示名, feature, phase, 说明)
TEMPLATE_CATALOG: Dict[str, tuple] = {
    "tpl_aqua": ("水景涌动", "band_low", 0.0, "低频驱动，长涌起落"),
    "tpl_breath": ("灯光呼吸", "bpm_phase", 0.25, "BPM 相位，均匀呼吸"),
    "tpl_groove": ("泵速律动", "bpm_phase", 0.0, "相位斜坡推进泵速"),
}

#: 通道类型目录（scope 识别为空时的全量回退；效果卡目标勾选用）
DEFAULT_CH_TYPES: Tuple[str, ...] = (
    "pump", "valve", "dimmer", "red", "green", "blue", "white", "amber",
    "uv", "motor_x", "motor_y", "motor_z", "pan", "tilt", "zoom", "gobo",
)

#: 通道类型 → 中文短标签（未知键回退原键）
CH_LABELS: Dict[str, str] = {
    "pump": "泵", "valve": "阀", "dimmer": "调光", "red": "R", "green": "G",
    "blue": "B", "white": "W", "amber": "琥珀", "uv": "UV",
    "motor_x": "电机 X", "motor_y": "电机 Y", "motor_z": "电机 Z",
    "pan": "水平", "tilt": "垂直", "zoom": "缩放", "gobo": "图案",
}

#: 波形下拉全集（恒定 + effect_engine.WAVE_FUNCS 全集；标签 → 值）
WAVE_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("恒定", ""), ("Sine", "Sine"), ("Square", "Square"), ("Sawtooth", "Sawtooth"),
    ("Triangle", "Triangle"), ("Cosine", "Cosine"), ("Bounce", "Bounce"),
    ("Expo", "Expo"), ("Random", "Random"),
    # R183 成熟化轮②（波形表语义）
    ("鲨齿", "Shark Tooth"), ("采样保持", "S&H"), ("游走", "Wander"),
)

#: 效果卡/源卡尺寸（世界单位；别名到 draw_effects 单一来源）
RULE_CARD_W, RULE_CARD_H = EFFECT_CARD_W, EFFECT_CARD_H
SOURCE_CARD_W, SOURCE_CARD_H = 150.0, 64.0
CARD_GAP = 132.0

#: 卡片色带（源卡=焦点蓝系，效果卡=调制绿系；走主题令牌）
SOURCE_BAR = BRIGHT_INFO
RULE_BAR = BRIGHT_SUCCESS


def ch_label(ch_type: str) -> str:
    """通道类型 → 中文短标签（未知回退原键）。"""
    key = str(ch_type or "")
    return CH_LABELS.get(key, key)


def type_label(ch_type: str) -> str:
    """通道类型显示文案（"泵 (pump)" 形态；未知键只显示原键）。"""
    key = str(ch_type or "")
    label = ch_label(key)
    return f"{label} ({key})" if label and label != key else key


def effect_card_node(nid: str, effect: str, pos, *, params=None,
                     ch_types=None) -> dict:
    """效果卡节点（唯一构造点；委托 draw_effects.effect_card_node）。"""
    from .draw_effects import effect_card_node as _make
    return _make(nid, effect, pos, params=params, ch_types=ch_types)


def rule_card_node(nid: str, ch_type: str, pos, *, wave: str = "",
                   amount: float = 1.0, wave_freq: float = 2.0) -> dict:
    """兼容包装：单通道旧形态 → 效果卡（默认「节拍脉冲」+ 单目标）。"""
    node = effect_card_node(nid, "pulse_beat", pos,
                            ch_types=[str(ch_type)] if ch_type else [])
    p = node["params"]
    p["wave"], p["depth"], p["freq"] = str(wave or ""), float(amount), float(wave_freq)
    node["wave"] = str(wave or "")
    node["amount"] = float(amount)
    node["wave_freq"] = float(wave_freq)
    return node


def source_card_node(nid: str, feature: str, pos, *, phase: float = 0.0,
                     template: str = "") -> dict:
    """源卡节点（feature 限目录；template 仅作徽标/记忆；pos=(x,y)）。"""
    kinds = {k for k, _ in SOURCE_KINDS}
    feat = str(feature or "")
    feat = feat if feat in kinds else "beat_strength"
    return {"id": str(nid), "kind": "source", "x": float(pos[0]),
            "y": float(pos[1]), "feature": feat,
            "phase": float(phase or 0.0), "template": str(template or ""),
            "icon": icon_for_feature(feat)}


def source_choices() -> List[tuple]:
    """源卡下拉/左栏条目：[(key, 显示名, feature, 说明, 图标)]。"""
    return [(key,) + spec for key, spec in SOURCE_CATALOG.items()]


def template_choices() -> List[tuple]:
    """预设模板条目：[(key, 显示名, feature, phase, 说明)]。"""
    return [(key,) + spec for key, spec in TEMPLATE_CATALOG.items()]


def wave_label(value: str) -> str:
    """波形值 → 下拉标签（未知值回退值本身）。"""
    val = str(value or "")
    for label, data in WAVE_CHOICES:
        if data == val:
            return label
    return val or "恒定"


#: feature → 默认图标（源卡绘制/建卡共用）
FEATURE_ICONS: Dict[str, str] = {
    "beat_strength": "wave-sine", "bpm_phase": "clock", "band_low": "wave-saw-tool",
    "band_high": "wave-square", "band_ratio": "adjustments",
}


def icon_for_feature(feature: str) -> str:
    """源卡 feature → tabler 图标名（未知回退 wave-sine）。"""
    return FEATURE_ICONS.get(str(feature or ""), "wave-sine")


def feature_label(feature: str) -> str:
    """源卡 feature → 中文显示名（未知回退原键）。"""
    key = str(feature or "")
    for k, label in SOURCE_KINDS:
        if k == key:
            return label
    return key


def template_label(key: str) -> str:
    """模板 key → 显示文案（未知 → "源信号"）。"""
    spec = TEMPLATE_CATALOG.get(str(key))
    return f"模板 · {spec[0]}" if spec else "源信号"
