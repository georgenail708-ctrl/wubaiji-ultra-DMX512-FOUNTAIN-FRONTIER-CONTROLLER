"""Offline R35 controls derived from the existing preset metadata, in seconds.

Presets guide the recorded trajectory; they are not hardware safety limits.
The network OutputSmoother and its CE contract remain their original owners.
"""
import math
from dataclasses import replace


def effective_preset(preset, custom):
    """Keep old saved preset controls meaningful rather than silently dropping them."""
    bounds = {"attack_ms": (0., 5000.), "release_ms": (0., 5000.), "smoothing": (0., 1.),
              "inertia": (0., .98), "overshoot": (0., 1.), "deadband": (0., 255.)}
    changes = {}
    for key, (low, high) in bounds.items():
        if key not in custom:
            continue
        value = float(custom[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须为 {low:g}–{high:g} 的有限数值")
        changes[key] = value
    if "curve" in custom:
        _custom_curve(custom, changes)
    return replace(preset, **changes)


def _custom_curve(custom, changes):
    from .output_smoother import EASE_FUNCS_V
    curve = str(custom["curve"])
    if curve not in EASE_FUNCS_V:
        raise ValueError("未知的平滑过渡曲线")
    changes["curve"] = curve


def control_defaults(preset):
    return {"strength": float(preset.smoothing),
            "window_seconds": max(.05, 2 * max(preset.attack_ms, preset.release_ms) / 1000),
            "detail_threshold": max(4., float(preset.deadband) * 2)}


def controls(preset, custom):
    """Validate all consumed controls before any frame or history changes."""
    preset = effective_preset(preset, dict(custom or {}))
    values = control_defaults(preset)
    values["dwell_seconds"] = .06
    values.update(dict(custom or {}))
    bounds = {"strength": (0., 1.), "window_seconds": (.01, 5.),
              "detail_threshold": (0., 255.), "dwell_seconds": (0., 5.)}
    for key, (low, high) in bounds.items():
        value = float(values[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须为 {low:g}–{high:g} 的有限数值")
        values[key] = value
    values["preset"] = preset
    return values


def directional_windows(preset, values):
    """Keep actual attack/release asymmetry and preset inertia at fixed timing."""
    maximum = max(float(preset.attack_ms), float(preset.release_ms), 1.)
    inertia = 1. + max(0., min(.98, float(preset.inertia)))
    return tuple(max(.01, values["window_seconds"] * max(1., ms) / maximum * inertia)
                 for ms in (preset.attack_ms, preset.release_ms))


def offline_description(preset):
    """Describe only the software behavior of the offline clip processor."""
    descriptions = {
        "bypass": "逐采样恢复保留的原录制；其它后处理编辑保留。",
        "gentle": "轻量去除小抖动，保留原力度与转折。",
        "natural": "上升较快、回落较慢，保留原节奏点。",
        "silky": "放缓段内变化，保留峰谷与停留，适合慢速氛围。",
        "punchy": "快速起势与短暂力度强调，保留原打击时刻。",
        "elastic": "段内加入弹性回弹；保留原端点与转折。",
        "heavy": "较强的段内惯性，保留原停留与行程。",
        "fountain_valve": "快起慢落的录制轨迹，保留开合时刻与力度细节。",
        "fountain_pump": "平缓起落，保留水形峰谷、停留与细节。",
        "led_dimmer": "轻量调光过渡，保留原亮度和节奏变化。",
        "motor_servo": "保形缓启缓停；16位粗细值成对处理，原录制可精确恢复。",
        "audio_reactive": "短时间窗处理抖动，保留音频节奏和强弱变化。",
    }
    return descriptions.get(preset.id, "保留录制峰谷、停留与时间；可调处理强度。")
