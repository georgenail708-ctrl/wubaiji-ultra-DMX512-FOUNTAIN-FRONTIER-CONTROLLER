# -*- coding: utf-8 -*-
"""跑动引擎缓动预设库（R2 深查③）。

纯数学实现（不依赖 Qt）：与 ``PyQt6.QtCore.QEasingCurve`` 同名同语义的
缓动函数表，供 ``runner_engine`` 油门斜坡（R1）每帧逼近目标值时使用。
UI 侧（runner_panel 缓动下拉）列出的名字即本表键名；未知名一律回退
Linear——新名字进引擎不需要改 UI。

`easing_value(name, t)` 契约：t∈[0,1] → 缓动进度（与
``QEasingCurve.valueForProgress`` 逐点同义；曲线族参数取 Qt 默认值：
Back 过冲 s=1.70158、Elastic 周期 p=0.3、Bounce 四段系数）。

★ 值域说明：单体曲线（Quad/Cubic/…/Expo/Circ/Sine）恒在 [0,1]；
**Back / Elastic 族按定义过冲**（如 InBack 起点下沉、OutElastic 越过 1）——
这是它们在 Qt 里的本义，故本模块**不裁剪**（裁剪会消灭过冲手感）。
消费方（油门斜坡）必须自行钳位，见 runner_engine._advance_throttle_ramp。
"""
from __future__ import annotations

import math
from typing import Callable, Dict

__all__ = ["easing_value", "easing_names", "easing_lut", "LINEAR",
           "LEGACY_TRANSITION_CURVES", "transition_curve_value",
           "transition_curve_gains"]


def _clamp01(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t


def _linear(t: float) -> float:
    return t


def _in_quad(t: float) -> float:
    return t * t


def _out_quad(t: float) -> float:
    return 1.0 - (1.0 - t) * (1.0 - t)


def _in_cubic(t: float) -> float:
    return t * t * t


def _out_cubic(t: float) -> float:
    t = 1.0 - t
    return 1.0 - t * t * t


def _in_quart(t: float) -> float:
    return t * t * t * t


def _out_quart(t: float) -> float:
    t = 1.0 - t
    return 1.0 - t * t * t * t


def _in_quint(t: float) -> float:
    return t ** 5.0


def _out_quint(t: float) -> float:
    t = 1.0 - t
    return 1.0 - t ** 5.0


def _in_sine(t: float) -> float:
    return 1.0 - math.cos(t * math.pi / 2.0)


def _out_sine(t: float) -> float:
    return math.sin(t * math.pi / 2.0)


def _in_expo(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 2.0 ** (10.0 * t - 10.0)


def _out_expo(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - 2.0 ** (-10.0 * t)


def _in_circ(t: float) -> float:
    return 1.0 - math.sqrt(max(0.0, 1.0 - t * t))


def _out_circ(t: float) -> float:
    t -= 1.0
    return math.sqrt(max(0.0, 1.0 - t * t))


def _in_elastic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    p = 0.3
    s = p / 4.0
    t -= 1.0
    return -(2.0 ** (10.0 * t)) * math.sin((t - s) * (2.0 * math.pi) / p)


def _out_elastic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    p = 0.3
    s = p / 4.0
    return (2.0 ** (-10.0 * t)) * math.sin((t - s) * (-2.0 * math.pi / p)) + 1.0


def _in_back(t: float) -> float:
    s = 1.70158
    return t * t * ((s + 1.0) * t - s)


def _out_back(t: float) -> float:
    s = 1.70158
    t -= 1.0
    return t * t * ((s + 1.0) * t + s) + 1.0


def _in_bounce(t: float) -> float:
    return 1.0 - _out_bounce(1.0 - t)


def _out_bounce(t: float) -> float:
    if t < 1.0 / 2.75:
        return 7.5625 * t * t
    if t < 2.0 / 2.75:
        t -= 1.5 / 2.75
        return 7.5625 * t * t + 0.75
    if t < 2.5 / 2.75:
        t -= 2.25 / 2.75
        return 7.5625 * t * t + 0.9375
    t -= 2.625 / 2.75
    return 7.5625 * t * t + 0.984375


def _in_out(in_fn: Callable[[float], float], out_fn: Callable[[float], float]) \
        -> Callable[[float], float]:
    """前半程 in、后半程 out（与 QEasingCurve InOut* 同语义）。"""

    def f(t: float) -> float:
        if t < 0.5:
            return 0.5 * in_fn(t * 2.0)
        return 0.5 + 0.5 * out_fn((t - 0.5) * 2.0)

    return f


def _out_in(out_fn: Callable[[float], float], in_fn: Callable[[float], float]) \
        -> Callable[[float], float]:
    """前半程 out、后半程 in（与 QEasingCurve OutIn* 同语义）。"""

    def f(t: float) -> float:
        if t < 0.5:
            return 0.5 * out_fn(t * 2.0)
        return 0.5 + 0.5 * in_fn((t - 0.5) * 2.0)

    return f


#: 主表：名字 → 缓动函数（键名与 QEasingCurve.Type 枚举键一致）
_EASINGS: Dict[str, Callable[[float], float]] = {
    "Linear": _linear,
    "InQuad": _in_quad, "OutQuad": _out_quad,
    "InOutQuad": _in_out(_in_quad, _out_quad),
    "OutInQuad": _out_in(_out_quad, _in_quad),
    "InCubic": _in_cubic, "OutCubic": _out_cubic,
    "InOutCubic": _in_out(_in_cubic, _out_cubic),
    "OutInCubic": _out_in(_out_cubic, _in_cubic),
    "InQuart": _in_quart, "OutQuart": _out_quart,
    "InOutQuart": _in_out(_in_quart, _out_quart),
    "OutInQuart": _out_in(_out_quart, _in_quart),
    "InQuint": _in_quint, "OutQuint": _out_quint,
    "InOutQuint": _in_out(_in_quint, _out_quint),
    "OutInQuint": _out_in(_out_quint, _in_quint),
    "InSine": _in_sine, "OutSine": _out_sine,
    "InOutSine": _in_out(_in_sine, _out_sine),
    "OutInSine": _out_in(_out_sine, _in_sine),
    "InExpo": _in_expo, "OutExpo": _out_expo,
    "InOutExpo": _in_out(_in_expo, _out_expo),
    "OutInExpo": _out_in(_out_expo, _in_expo),
    "InCirc": _in_circ, "OutCirc": _out_circ,
    "InOutCirc": _in_out(_in_circ, _out_circ),
    "OutInCirc": _out_in(_out_circ, _in_circ),
    "InElastic": _in_elastic, "OutElastic": _out_elastic,
    "InOutElastic": _in_out(_in_elastic, _out_elastic),
    "OutInElastic": _out_in(_out_elastic, _in_elastic),
    "InBack": _in_back, "OutBack": _out_back,
    "InOutBack": _in_out(_in_back, _out_back),
    "OutInBack": _out_in(_out_back, _in_back),
    "InBounce": _in_bounce, "OutBounce": _out_bounce,
    "InOutBounce": _in_out(_in_bounce, _out_bounce),
    "OutInBounce": _out_in(_out_bounce, _in_bounce),
}

#: 缺省/回退缓动
LINEAR = "Linear"

#: 大小写不敏感索引（引擎过渡曲线存小写名 → 同一张表命中）
_EASINGS_CF: Dict[str, Callable[[float], float]] = {
    k.lower(): fn for k, fn in _EASINGS.items()
}

#: 时间线过渡曲线的 5 个旧名（引擎 `TRANSITION_CURVES` 历史词汇，语义冻结）
LEGACY_TRANSITION_CURVES = ("linear", "easein", "easeout", "easeinout", "bezier")


def easing_value(name: str, t: float) -> float:
    """按名字取缓动进度：t∈[0,1] → [0,1]（未知名回退 Linear）。

    名字大小写不敏感（引擎过渡曲线把小写名存进 Clip 字段）。
    """
    key = str(name or LINEAR)
    fn = _EASINGS.get(key) or _EASINGS_CF.get(key.lower()) or _linear
    try:
        return float(fn(_clamp01(float(t))))
    except Exception:
        return _clamp01(float(t))


def easing_names() -> list[str]:
    """全部缓动预设名（UI 下拉顺序）。"""
    return list(_EASINGS.keys())


# ── 时间线过渡曲线（R16：5 旧名 + QEasingCurve 全名共用同一求值入口） ──────

def _legacy_smoothstep(t: float) -> float:
    """旧 `easeinout` 的 t²(3-2t)（≠ 标准 InOutQuad，语义冻结不可换）。"""
    return t * t * (3.0 - 2.0 * t)


def _legacy_bezier(t: float) -> float:
    """旧 `bezier` 三次式（引擎历史公式，逐字节保留）。"""
    inv = 1.0 - t
    return 0.3 * inv * inv * t + 3.0 * inv * t * t + t * t * t


def transition_curve_value(name: str, t: float) -> float:
    """过渡曲线求值（标量）：5 个旧名走原公式，其余走缓动表。

    过渡增益只在 `_clip_transition_enabled` 为真时使用，值域钳到 [0,1]。
    """
    low = str(name or "linear").strip().lower()
    if low == "easein":
        return _clamp01(_in_quad(_clamp01(_as_float(t))))
    if low == "easeout":
        return _clamp01(_out_quad(_clamp01(_as_float(t))))
    if low == "easeinout":
        return _clamp01(_legacy_smoothstep(_clamp01(_as_float(t))))
    if low == "bezier":
        return _clamp01(_legacy_bezier(_clamp01(_as_float(t))))
    return easing_value(low, t)


def _as_float(t) -> float:
    try:
        return float(t)
    except (TypeError, ValueError):
        return 0.0


def easing_lut(name: str, size: int = 256) -> list[float]:
    """过渡曲线的 256 点查表（`_ease_apply` 同口径：idx = t×255 取整）。

    用于数组路径（批量通道增益）：旧名与缓动库都逐点采样同一求值入口。
    """
    n = max(2, int(size))
    return [transition_curve_value(name, i / (n - 1)) for i in range(n)]


def transition_curve_gains(name: str, tt) -> "object":
    """过渡曲线求值（数组路径，R16）：旧名以外的一切名字走缓动库查表。

    只服务**非 5 旧名**：调用方（playback_engine）对 5 旧名保留原闭式公式
    （逐字节等价），只有缓动库名与未知名落到这里——库名用 1024 点 LUT 采样，
    ``linear``/未知名按**恒等**返回（不走 LUT，保证与旧 `gains = tt` 逐位一致）。
    返回 float32 数组，与引擎增益管线同 dtype。
    """
    import numpy as np
    arr = np.clip(np.asarray(tt, dtype=np.float64), 0.0, 1.0)
    key = str(name or "").strip().lower()
    if key not in _EASINGS_CF or key == "linear":
        return arr.astype(np.float32)           # 恒等（未知名的旧回退语义）
    lut = np.asarray(easing_lut(key, 1024), dtype=np.float64)
    idx = np.clip(np.rint(arr * 1023.0).astype(np.int32), 0, 1023)
    return lut[idx].astype(np.float32)
