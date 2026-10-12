# -*- coding: utf-8 -*-
"""Draw schema 的原生表单适配；图/DSP/存储仍使用原模型。

参考 Modulator Inspector、Qt QFormLayout；复用仓内 ParamSpec 与
CardParamForm。范围对齐 audio_mod_draw_compile._rule_params，非截图推断。
"""
from __future__ import annotations

import copy

from src.engine.audio_mod_draw_compile_spec import FREQ_UNITS, RANDOM_MODES, SIGNAL_KEYS
from src.ui.clip_studio.studio_cards import CardSpec, ParamSpec
from .draw_canvas_edit import materialize_params
from .draw_cards import DEFAULT_CH_TYPES, WAVE_CHOICES, feature_label
from .draw_effects import FREQ_UNIT_LABELS, PARAM_SPECS, RANDOM_MODE_LABELS


def rule_values(node) -> dict:
    """只读展开旧卡；开属性页不物化活节点，也不丢未知参数或高精度值。"""
    snapshot = copy.deepcopy(node)
    params = materialize_params(snapshot)
    if isinstance(node.get("params"), dict):
        # 缺键按原编译器默认，而非效果目录的Sine默认；未知/原精度优先。
        return {**materialize_params({}), **params}
    if not isinstance(node.get("params"), dict):
        # 卡拖杆仍用原0.25..8；属性编辑保留编译器允许的旧wave_freq到16。
        params["freq"] = max(0.25, min(16.0, float(node.get("wave_freq", 2.0) or 2.0)))
    return params


def _number(key, lo, hi, step, *, unit="") -> ParamSpec:
    label, default, _description = PARAM_SPECS[key]
    return ParamSpec(key, label, "float", default, lo, hi, step, unit=unit)


def _choice(key, choices, current) -> ParamSpec:
    label, default, _description = PARAM_SPECS[key]
    options = tuple(choices)
    if str(current) not in {str(value) for value, _label in options}:
        options += ((str(current), str(current) or "恒定"),)
    return ParamSpec(key, label, "choice", default, choices=options)


def _spec(title, fields) -> CardSpec:
    return CardSpec(key="draw_properties", group="draw", name=title, desc="",
                    icon="activity", art="", ports=(), params=tuple(fields))


def rule_groups(node) -> tuple:
    """仅把真实规则字段分区，参数键/时基/波形目录继续来自原定义。"""
    values = rule_values(node)
    waves = tuple((value, label) for label, value in WAVE_CHOICES)
    rhythm = _spec("波形与节奏", (
        _choice("wave", waves, values.get("wave", "")),
        _number("depth", 0.0, 1.0, 0.01),
        _number("freq", 0.25, 16.0, 0.01),
        _choice("freq_unit", ((key, FREQ_UNIT_LABELS[key]) for key in FREQ_UNITS),
                values.get("freq_unit", "beat")),
        ParamSpec("phase", PARAM_SPECS["phase"][0], "str", "0.0"),
        ParamSpec("invert", PARAM_SPECS["invert"][0], "bool", False),
    ))
    response = _spec("响应与门限", (
        _number("attack", 0.0, 2.0, 0.01, unit="s"),
        _number("release", 0.0, 2.0, 0.01, unit="s"),
        _number("threshold", 0.0, 1.0, 0.01),
        _number("smoothing", 0.0, 1.0, 0.01),
        _number("threshold_close", 0.0, 1.0, 0.01),
        _number("hold_ms", 0.0, 2000.0, 1.0, unit="ms"),
    ))
    signal = _spec("信号与多灯", (
        _choice("signal", ((key, feature_label(key)) for key in SIGNAL_KEYS),
                values.get("signal", "beat_strength")),
        _choice("random_mode", ((key, RANDOM_MODE_LABELS[key]) for key in RANDOM_MODES),
                values.get("random_mode", "")),
        _number("spread_deg", 0.0, 360.0, 1.0, unit="°"),
    ))
    return rhythm, response, signal


def group_values(spec, params) -> dict:
    """每区只发自己的字段；避免另一分区的旧原值覆盖后来编辑。"""
    values = {ps.key: copy.deepcopy(params[ps.key]) for ps in spec.params if ps.key in params}
    if "phase" in values:
        values["phase"] = str(values["phase"])
    return values


def target_types(canvas) -> list:
    """真实scope加已存目标有序并集；无scope时保留原全量目录回退。"""
    known = list(getattr(canvas, "_target_types", ()) or DEFAULT_CH_TYPES)
    for node in canvas.graph().get("nodes") or []:
        for key in node.get("ch_types") or [node.get("ch_type", "")]:
            if key and str(key) not in known:
                known.append(str(key))
    return known
