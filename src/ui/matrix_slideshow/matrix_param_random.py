# -*- coding: utf-8 -*-
"""matrix_param_random.py — 像素效果参数随机化（深查②三.Matrix #16）。

先例复用（effect_browser._randomize_current_params）：
  * 只动**数值参数**（float/int），bool/str/颜色/区域/路径一律不碰；
  * 写入走引擎属性 + 面板既有同步链（``_sync_pixel_param_widgets``），
    等价于手工拖滑杆的信号路径，不绕过 UI。

本项按任务书要求是**限幅扰动**而非全域随机：
    新值 = clamp(现值 + U(-strength, +strength) × 区间跨度, lo, hi)
  * 好处：连续点按是"在现有画面附近微调"，不会一次跳到区间另一端
    （全域随机会让亮度/速度突变到不可用值，现场调效果时反而危险）；
  * ``strength=0.25`` 时单参数最大移动区间 25%，连续点的期望漂移为 0
    （对称扰动，围绕现值的稳定游走）。
"""
from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 参与随机的数值参数表：(属性名, 下界, 上界, 是否整数)
#:  取值区间与参数面板旋钮 setRange 同口径（阈值/速度/波形造型/屏幕色调/
#:  音频条与峰值保真参数）；**含 ON-OFF 阈值**（现场最常调）。
NUMERIC_PARAM_SPECS: Tuple[Tuple[str, float, float, bool], ...] = (
    ("threshold", 0.0, 1.0, False),
    ("speed", 0.1, 5.0, False),
    ("matrix_wave_density", 0.01, 12.0, False),
    ("matrix_wave_width", 0.0, 1.0, False),
    ("matrix_wave_twist", -6.283185307179586, 6.283185307179586, False),
    ("matrix_symmetry_mix", 0.0, 1.0, False),
    ("screen_brightness_boost", 0.0, 3.0, False),
    ("screen_contrast", 0.0, 3.0, False),
    ("screen_gamma", 0.05, 4.0, False),
    ("screen_smooth", 0.0, 0.98, False),
    ("audio_amplitude", 0.0, 12.0, False),
    ("audio_norm_offset", -1.0, 1.0, False),
    ("audio_bar_gamma", 0.05, 4.0, False),
    ("audio_bar_softness", 0.0, 0.5, False),
    ("audio_bar_smoothing", 0.0, 0.98, False),
    ("audio_bar_motion", 0.0, 8.0, False),
    ("audio_peak_hold", 0.0, 3.0, False),
    ("audio_peak_decay", 0.0, 0.5, False),
)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _current_value(engine, attr: str, lo: float, hi: float) -> float:
    """现值的浮点读数（非法/NaN → 区间中点；helper）。"""
    try:
        cur = float(getattr(engine, attr))
    except (TypeError, ValueError):
        cur = (float(lo) + float(hi)) / 2.0
    if cur != cur:                       # NaN 现值
        cur = (float(lo) + float(hi)) / 2.0
    return cur


def _perturbed_value(engine, spec, amp: float, rand) -> object:
    """单参数限幅扰动后的新值（spec = (attr, lo, hi, is_int)；helper 压 CC）。"""
    attr, lo, hi, is_int = spec
    span = float(hi) - float(lo)
    cur = _current_value(engine, attr, lo, hi)
    new = _clamp(cur + rand.uniform(-amp, amp) * span, float(lo), float(hi))
    if is_int:
        return int(round(new))
    if isinstance(getattr(engine, attr), float):
        return float(new)
    return new


def randomize_values(engine, rng: Optional[random.Random] = None,
                     strength: float = 0.25) -> Dict[str, object]:
    """按限幅扰动算出新的数值参数（**不改引擎**；纯函数，rng 可注入）。

    只返回真的发生变化的键；缺失属性跳过；NaN/非法现值按区间中点起扰动。
    """
    rand = rng or random
    try:
        amp = min(1.0, max(0.01, float(strength)))
    except (TypeError, ValueError):
        amp = 0.25
    out: Dict[str, object] = {}
    for spec in NUMERIC_PARAM_SPECS:
        attr = spec[0]
        if not hasattr(engine, attr) or float(spec[2]) <= float(spec[1]):
            continue
        new = _perturbed_value(engine, spec, amp, rand)
        if new != getattr(engine, attr):
            out[str(attr)] = new
    return out


def apply_values(engine, values: Dict[str, object]) -> int:
    """把随机值写进引擎（只写数值属性；返回写入个数）。"""
    wrote = 0
    for attr, val in (values or {}).items():
        if not hasattr(engine, attr):
            continue
        try:
            if isinstance(getattr(engine, attr), int) and not isinstance(val, bool):
                setattr(engine, attr, int(round(float(val))))
            else:
                setattr(engine, attr, val)
            wrote += 1
        except Exception:
            logger.debug("matrix: randomize set %s failed", attr, exc_info=True)
    return wrote


def randomize_pixel_params(panel, rng: Optional[random.Random] = None,
                           strength: float = 0.25) -> Dict[str, object]:
    """UI 入口：随机化 ``panel``（PixelEffectParamPanel）绑定引擎的参数。

    写入后走面板既有同步链（``_sync_pixel_param_widgets`` + params_changed），
    与手工改参数等价；返回 {"changed": {...}, "count": n}。
    """
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return {"changed": {}, "count": 0}
    values = randomize_values(engine, rng=rng, strength=strength)
    count = apply_values(engine, values)
    if not count:
        return {"changed": {}, "count": 0}
    sync = getattr(panel, "_sync_pixel_param_widgets", None)
    if callable(sync):
        try:
            sync()
        except Exception:
            logger.debug("matrix: sync after randomize failed", exc_info=True)
    sig = getattr(panel, "params_changed", None)
    if sig is not None:
        try:
            sig.emit()
        except Exception:
            logger.debug("matrix: emit params_changed after randomize failed",
                         exc_info=True)
    return {"changed": values, "count": count}


def spec_table() -> List[Tuple[str, float, float, bool]]:
    """参数表副本（测试/文档用）。"""
    return list(NUMERIC_PARAM_SPECS)


__all__ = ["NUMERIC_PARAM_SPECS", "apply_values", "randomize_pixel_params",
           "randomize_values", "spec_table"]
