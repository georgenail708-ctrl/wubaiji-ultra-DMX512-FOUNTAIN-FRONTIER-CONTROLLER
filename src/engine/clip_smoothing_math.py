"""R35 离线录制平滑：按实际秒数滤波并保护峰谷、停留和端点。

直接复用 SciPy 1.17.1 PchipInterpolator / savgol_filter / find_peaks（BSD-3-Clause）：
https://github.com/scipy/scipy/blob/v1.17.1/scipy/interpolate/_cubic.py
https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_savitzky_golay.py
https://github.com/scipy/scipy/blob/v1.17.1/scipy/signal/_peak_finding.py
时间重采样、有效通道过滤和录制关键点保全是本项目适配。此离线实现不改
网络 OutputSmoother 或 CE 的实时输出合同，不声称验证了物理设备响应。
"""
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks, savgol_filter

from .clip_smoothing_profiles import controls, directional_windows
from .output_smoother import EASE_FUNCS_V


def smooth_recording(frames, preset, channel_masks, custom, pairs=None):
    """构造独立结果，未选通道和采样时刻逐字节保留。"""
    result = [frame.copy() for frame in frames]
    for universe in sorted({int(f.universe) for f in result}):
        group = sorted((f for f in result if int(f.universe) == universe),
                       key=lambda f: float(f.timestamp))
        _smooth_group(group, universe, (preset, channel_masks, custom, pairs or {}))
    return result


def _target_channels(matrix, universe, masks):
    """显式 mask（包含空选区）优先；旧无 mask 录制仅处理有变化通道。"""
    if masks:
        selected = masks.get(universe, masks.get(str(universe), []))
        return sorted({int(ch) - 1 for ch in selected if 1 <= int(ch) <= matrix.shape[1]})
    return np.flatnonzero(np.ptp(matrix, axis=0) > 0).tolist()


def _smooth_group(group, universe, context):
    """每域独立，最多按64通道批处理，避免大工程临时矩阵膨胀。"""
    if len(group) < 3:
        return
    preset, masks, custom, pairs = context
    times = np.array([float(f.timestamp) for f in group])
    if not np.all(np.isfinite(times)) or np.any(np.diff(times) < 0):
        raise ValueError("录制时间戳无效")
    matrix = np.stack([np.asarray(f.data, dtype=np.uint8) for f in group])
    channels = _target_channels(matrix, universe, masks)
    consumed = _smooth_pairs(matrix, times, channels, (pairs.get(universe, ()), preset, custom))
    channels = [channel for channel in channels if channel not in consumed]
    for start in range(0, len(channels), 64):
        batch = channels[start:start + 64]
        for channel in batch:
            matrix[:, channel] = smooth_channel(times, matrix[:, channel], preset, custom)
    for frame, data in zip(group, matrix):
        frame.data = data.copy()


def _smooth_pairs(matrix, times, channels, context):
    pairs, preset, custom = context
    consumed = set()
    for coarse, fine in pairs:
        if coarse not in channels or fine not in channels:
            continue
        if coarse in consumed or fine in consumed:
            raise ValueError("录制目标存在冲突的16位通道配对")
        raw = (matrix[:, coarse].astype(np.uint16) << 8) | matrix[:, fine]
        output = smooth_channel(times, raw, preset, custom)
        matrix[:, coarse], matrix[:, fine] = output >> 8, output & 255
        consumed.update((coarse, fine))
    return consumed


def _uniform_filter(times, values, window_seconds):
    """不规则采样在时间轴上计算，结果回到原始时刻，不添加/删除采样。"""
    unique, index = np.unique(times, return_index=True)
    if len(unique) < 3:
        return values.copy()
    step = max(float(np.median(np.diff(unique))), (unique[-1] - unique[0]) / 200000)
    count = max(3, int(round((unique[-1] - unique[0]) / step)) + 1)
    grid = np.linspace(unique[0], unique[-1], count)
    width = max(3, int(round(window_seconds / (grid[1] - grid[0]))) | 1)
    width = min(width, count if count % 2 else count - 1)
    sampled = np.interp(grid, unique, values[index])
    filtered = savgol_filter(sampled, width, min(2, width - 1), mode="interp")
    return np.interp(times, grid, filtered)


def _dwell_mask(times, values, minimum_seconds):
    """原有连续停留整体固定，不能让平滑提前动作或削短停留。"""
    boundaries = np.r_[0, np.flatnonzero(np.diff(values) != 0) + 1, len(values)]
    result = np.zeros(len(values), dtype=bool)
    for first, last in zip(boundaries[:-1], boundaries[1:]):
        if last - first > 1 and times[last - 1] - times[first] >= minimum_seconds:
            result[first:last] = True
    return result


def _landmarks(times, values, prominence, custom):
    """有意义峰谷按 prominence 识别，细小抖动不强制逐点保留。"""
    peaks, _ = find_peaks(values, prominence=prominence, plateau_size=(None, None))
    valleys, _ = find_peaks(-values, prominence=prominence, plateau_size=(None, None))
    fixed = _dwell_mask(times, values, float(custom.get("dwell_seconds", 0.06)))
    fixed[np.r_[0, peaks, valleys, len(values) - 1]] = True
    return fixed


def _pchip(times, anchors, values):
    """Use installed SciPy's C1 shape-preserving interpolation, never copied code."""
    unique, first = np.unique(times[anchors], return_index=True)
    if len(unique) < 2:
        return np.full(len(times), float(values[first[0]]))
    return PchipInterpolator(unique, values[first], extrapolate=False)(times)


def _profile_curve(times, values, anchors, preset):
    """Existing easing vocabulary affects only intervals between authored anchors."""
    result = values.copy()
    ease = EASE_FUNCS_V.get(preset.curve, EASE_FUNCS_V["linear"])
    for first, last in zip(anchors[:-1], anchors[1:]):
        duration = times[last] - times[first]
        if duration <= 0:
            continue
        phase = (times[first:last + 1] - times[first]) / duration
        delta = values[last] - values[first]
        curve = values[first] + delta * ease(phase)
        if preset.id in ("punchy", "elastic"):
            curve += float(preset.overshoot) * delta * np.sin(np.pi * phase) * (1 - phase)
        result[first:last + 1] = curve
    return result


def _shape_target(times, values, filtered, fixed, preset):
    anchors = np.flatnonzero(fixed)
    corrected = filtered + _pchip(times, anchors, (values - filtered)[anchors])
    shape = _pchip(times, anchors, values[anchors])
    curved = _profile_curve(times, values, anchors, preset)
    weight = min(.8, max(0., float(preset.inertia)) + .15)
    target = corrected * (1 - weight) + (shape + curved) * (.5 * weight)
    if preset.id not in ("punchy", "elastic"):
        target = np.clip(target, values.min(), values.max())
    return target


def smooth_channel(times, raw, preset, custom):
    """Separate detail, timing and strength; preserve all authored anchors exactly."""
    params = controls(preset, custom)
    preset = params["preset"]
    if not params["strength"] or len(np.unique(times)) < 3:
        return raw.copy()
    scale = 256. if raw.dtype == np.uint16 else 1.
    values = raw.astype(np.float64) / scale
    attack, release = directional_windows(preset, params)
    rising = _uniform_filter(times, values, attack)
    falling = _uniform_filter(times, values, release)
    filtered = np.where(np.gradient(values) >= 0, rising, falling)
    fixed = _landmarks(times, values, params["detail_threshold"], params)
    target = _shape_target(times, values, filtered, fixed, preset)
    output = values + params["strength"] * (target - values)
    output[fixed] = values[fixed]
    return np.clip(np.rint(output * scale), 0, np.iinfo(raw.dtype).max).astype(raw.dtype)
