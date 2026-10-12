"""
effect_presets_motor_mech.py — R173 电机【编组/相位差/奇偶】机制族

用户领域纠正（原话，2026-09）："喷泉编曲里面电机效果其实是靠编组和延迟和反相相位差
里面制作的水型，也就是大家都是套用的普通正弦波，整体左到右和交叉和龙形波动其实就是
靠奇偶反相，调整相位差，每几个喷头几个喷头分组调而已，这个才是电机效果真正的制作和
区分的要点……也不要忽略调整波形效果本身数值的魅力，只不过它不是重点而已"。

只用一种普通正弦波（可选三角/平滑为次要变奏，非重点），差异全部来自：编组（按位置序
每 N 台一组）/ 奇偶·镜像反相（设备级、组级、以中心对称三种判据）/ 相位差（组内步进、
组间步进，单位=度）/ 起点方向（左→右、右→左、中心→两边、两边→中心）/ 时间演变
（奇偶自动交替、相位差扫描 同步→龙形→交叉、分组接力）。

★ 与既有 `motor_cross_nn_v35`/`motor_cross22_xy_v35`（effect_presets_v35.py）的边界：
  那一族已覆盖"组间反相 + 组间静态相位差，永远按序列从左到右分组"。本族只做它们没有
  的部分——组内相位步进、镜像/中心判据反相、起点方向重排、相位差随时间演变——避免
  默认参数输出与既有电机预设逐字节雷同（指纹脚本核对互相关 < 0.97，见交接报告）。

★ 与引擎通用后处理参数（`_append_universal_params`：wings/block_size/effect_width/
  phase_spread/subset_delay_phase）的边界：那些是 `_run_compute_fn` 对任意预设都会做
  的通用空间重复/量化/收窄/整体错相包装，wings=1、block_size=1 时零开销直调本族内核
  一次（effect_engine.py 2929-2954 行）。本族内核只按 `enumerate(sorted_fx)` 实现
  "分组/奇偶/相位差"本身，不重复实现"复制 N 份翼"或"整体错相"——那两件事引擎已通用
  提供，重复实现会和引擎的翼/块广播打架。
"""
from __future__ import annotations
from . import effect_width_field as _width_field

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .effect_engine import EffectEngine

# ── 常量 ──────────────────────────────────────────────────────────────────────
CAT = "电机/编组"
MOTOR_CH = ["motor_x", "motor_y", "motor_z"]
WAVE_CHOICES = ["正弦", "三角", "平滑"]
ORIGIN_CHOICES = ["左→右", "右→左", "中心→两边", "两边→中心"]
_ORIGIN_MAP = {
    "左→右": "left_right", "右→左": "right_left",
    "中心→两边": "center_out", "两边→中心": "edge_in",
}
TAG_BASIC = ["motor", "mech", "r173", "基础"]
TAG_MID = ["motor", "mech", "r173", "中级"]
TAG_ADV = ["motor", "mech", "r173", "高级"]

# ── 波形/分组工具 ────────────────────────────────────────────────────────────
def _sine_raw(x: float) -> float:
    return math.sin(2.0 * math.pi * x)

def _tri_raw(x: float) -> float:
    x = x % 1.0
    return 2.0 * abs(2.0 * x - 1.0) - 1.0

def _smooth_raw(x: float) -> float:
    """三角波经 smoothstep 缓动，波峰更圆润——"次要变奏"波形，非重点。"""
    tri = _tri_raw(x)
    u = (tri + 1.0) * 0.5
    eased = u * u * (3.0 - 2.0 * u)
    return eased * 2.0 - 1.0

def _waveshape(shape: str, x: float) -> float:
    if shape == "三角":
        return _tri_raw(x)
    if shape == "平滑":
        return _smooth_raw(x)
    return _sine_raw(x)

def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))

def _origin_pos(seq_i: int, n: int, origin_label: str) -> float:
    """按起点方向把设备位置序重排成"相位序"，只影响相位/分组计算，不改变输出目标。"""
    code = _ORIGIN_MAP.get(origin_label, "left_right")
    if code == "right_left":
        return float(n - 1 - seq_i)
    if n <= 1:
        return 0.0
    mid = (n - 1) / 2.0
    dist = abs(seq_i - mid)
    if code == "center_out":
        return dist
    if code == "edge_in":
        return mid - dist
    return float(seq_i)

def _group_of(pos: float, group_size: int) -> tuple:
    gs = max(1, int(group_size))
    group_idx = int(pos // gs)
    pos_in_group = pos - group_idx * gs
    return group_idx, pos_in_group

def _read_common(params: dict) -> tuple:
    """10/12 个内核共用的基础五参数：speed/amplitude/center/waveform/ch。"""
    return (float(params.get("speed", 1.0)), float(params.get("amplitude", 60.0)),
            float(params.get("center", 128.0)), params.get("waveform", "正弦"),
            params.get("ch", "motor_x"))

def _read_xy(params: dict, amp_x_def: float, amp_y_def: float) -> tuple:
    """双轴内核（cross_weave/dragon_relay）共用：speed/amp_x/amp_y/center_x/center_y/waveform。"""
    return (float(params.get("speed", 1.0)), float(params.get("amp_x", amp_x_def)),
            float(params.get("amp_y", amp_y_def)), float(params.get("center_x", 128.0)),
            float(params.get("center_y", 128.0)), params.get("waveform", "正弦"))

# ── ParamSpec 构造小工具（复用同一批常用参数；param_spec=局部 import 到的类，按参数传入） ──
def _p_ch(param_spec, default: str = "motor_x"):
    return param_spec("ch", "通道", "choice", default, choices=MOTOR_CH,
                       tooltip="选择效果作用的电机通道：motor_x/motor_y/motor_z")

def _p_speed(param_spec):
    return param_spec("speed", "速度", "float", 1.0, 0.0, 10.0, 0.1,
                       tooltip="控制正弦摆动的速度，值越大摆动越快")

def _p_amp(param_spec, default: float = 60.0):
    return param_spec("amplitude", "幅度(度)", "float", default, 0.0, 180.0, 1.0,
                       tooltip="控制摆动幅度，值越大摆动范围越大")

def _p_center(param_spec):
    return param_spec("center", "中心值", "float", 128.0, 0.0, 255.0, 1.0,
                       tooltip="摆动的中心基准值")

def _p_wave(param_spec):
    return param_spec("waveform", "波形", "choice", "正弦", choices=WAVE_CHOICES,
                       tooltip="正弦为基础波形；三角/平滑为次要变奏，不改变分组/相位机制本身")

def _p_group(param_spec, default: int = 4):
    return param_spec("group_size", "每组大小", "int", default, 1, 16, 1,
                       tooltip="按位置序每 N 台设备分为一组，用于奇偶/相位差分组判据")

def _p_invert(param_spec):
    return param_spec("invert", "反转", "bool", False,
                       tooltip="反转正负相位分配（哪一半/哪一组为正相）")

def _p_origin(param_spec, default: str = "左→右"):
    return param_spec("origin", "起点方向", "choice", default, choices=ORIGIN_CHOICES,
                       tooltip="相位序的起点与方向：左→右/右→左/中心→两边/两边→中心")

def _p_deg(param_spec, key: str, label: str, default: float, tip: str):
    return param_spec(key, label, "float", default, 0.0, 360.0, 1.0, unit="度", tooltip=tip)

def _p_float(param_spec, key, label, default, *, lo, hi, step, tip, unit=""):
    return param_spec(key, label, "float", default, lo, hi, step, unit=unit, tooltip=tip)

# ── 基础：单一判据反相/分组 ──────────────────────────────────────────────────
def _oddeven(ef, t, sorted_fx, fdefs, params, fps):
    """奇偶反相摆动：最基础的编组水型——同一条正弦波，按位置序逐台判断奇偶反相。"""
    speed, amp, center, shape, ch = _read_common(params)
    invert = bool(params.get("invert", False))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        sign = -1.0 if (seq_i % 2 == 1) else 1.0
        if invert:
            sign = -sign
        raw = _waveshape(shape, t * speed) * sign
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _cluster_step(ef, t, sorted_fx, fdefs, params, fps):
    """分组相位步进：每组内同相，组与组之间按度数递增相位——纯行波，不反相。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 4))
    out_deg = float(params.get("group_phase_deg", 45.0))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, _pig = _group_of(float(seq_i), group_size)
        phase = t * speed + out_deg / 360.0 * group_idx
        raw = _waveshape(shape, phase)
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _mirror_invert(ef, t, sorted_fx, fdefs, params, fps):
    """中心镜像反相：以中点为轴按"距中心带宽"分同心带交替反相，判据是几何对称距离而非序号分块，带宽默认 3 台。"""
    speed, amp, center, shape, ch = _read_common(params)
    invert = bool(params.get("invert", False))
    band = max(1, int(params.get("mirror_band", 3)))
    n = len(sorted_fx)
    mid = (n - 1) / 2.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        band_idx = int(abs(seq_i - mid) // band)
        sign = -1.0 if (band_idx % 2 == 1) else 1.0
        if invert:
            sign = -sign
        raw = _waveshape(shape, t * speed) * sign
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _register_motor_mech_basic(engine) -> int:
    """注册 3 个"基础"预设：奇偶反相 / 分组相位步进 / 中心镜像反相。"""
    from .effect_engine import EffectPreset, ParamSpec

    def reg(preset, fn): engine._reg(preset, fn)

    reg(EffectPreset(
        id="motor_grp_oddeven", name="奇偶反相摆动", category=CAT,
        description="喷头按位置序逐台判断奇偶：偶数台与奇数台反相摆动。同一条正弦波，只靠奇偶分组产生左右交替水型。",
        target_channels=MOTOR_CH, icon="±1", tags=TAG_BASIC,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_invert(ParamSpec)],
    ), _oddeven)

    reg(EffectPreset(
        id="motor_grp_cluster_step", name="分组相位步进", category=CAT,
        description="按位置序每 N 台分为一组，组内同相、组间按度数递增相位，形成阶梯式行波（不反相，纯相位差）。",
        target_channels=MOTOR_CH, icon="⊞N", tags=TAG_BASIC,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 4),
                _p_deg(ParamSpec, "group_phase_deg", "组间相位差(度)", 45.0, "相邻两组之间的相位差，0°=同步，180°=反相")],
    ), _cluster_step)

    reg(EffectPreset(
        id="motor_grp_mirror_invert", name="中心镜像反相", category=CAT,
        description="以中点为轴按距中心带宽分同心带交替反相，带宽默认3台，非简单两分，呈现中心对称的多层水型。",
        target_channels=MOTOR_CH, icon="⇋|", tags=TAG_BASIC,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_invert(ParamSpec),
                ParamSpec("mirror_band", "镜像带宽", "int", 3, 1, 8, 1,
                          tooltip="按距中心的台数每 N 台为一带，相邻带交替反相")],
    ), _mirror_invert)

    return 3

# ── 中级：编组 + 起点方向 + 组内步进 ─────────────────────────────────────────
def _center_alt(ef, t, sorted_fx, fdefs, params, fps):
    """中心扩散交替分组：先按起点方向重排位置序，再做组级奇偶反相 + 组间相位差。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 3))
    out_deg = float(params.get("group_phase_deg", 20.0))
    origin = params.get("origin", "中心→两边")
    invert = bool(params.get("invert", False))
    n = len(sorted_fx)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = _origin_pos(seq_i, n, origin)
        group_idx, _pig = _group_of(pos, group_size)
        sign = -1.0 if (group_idx % 2 == 1) else 1.0
        if invert:
            sign = -sign
        phase = t * speed + out_deg / 360.0 * group_idx
        raw = _waveshape(shape, phase) * sign
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _wave_step(ef, t, sorted_fx, fdefs, params, fps):
    """组内行波步进：组内按位置递增相位（组内小龙形），组与组之间保持同步。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 4))
    in_deg = float(params.get("in_group_phase_deg", 30.0))
    origin = params.get("origin", "左→右")
    n = len(sorted_fx)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = _origin_pos(seq_i, n, origin)
        _gidx, pos_in_group = _group_of(pos, group_size)
        phase = t * speed + in_deg / 360.0 * pos_in_group
        raw = _waveshape(shape, phase)
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _origin_wave(ef, t, sorted_fx, fdefs, params, fps):
    """起点方向行波：不分组，按起点方向对每台设备本身施加相位步进，形成连续行波。"""
    speed, amp, center, shape, ch = _read_common(params)
    out_deg = float(params.get("phase_step_deg", 15.0))
    origin = params.get("origin", "中心→两边")
    n = len(sorted_fx)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = _origin_pos(seq_i, n, origin)
        phase = t * speed + out_deg / 360.0 * pos
        raw = _waveshape(shape, phase)
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _breathing_groups(ef, t, sorted_fx, fdefs, params, fps):
    """分组呼吸振幅：全组共享同一相位摆动，只有振幅按组做慢速呼吸并组间错相。"""
    speed, amp, center, shape, ch = _read_common(params)
    envs = _width_field.breathing_envelope((t, len(sorted_fx), params), (_group_of, _sine_raw))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        raw = _waveshape(shape, t * speed)
        ef.values[orig_i][ch] = _clamp(center + amp * envs[seq_i] * raw)

def _register_motor_mech_mid(engine) -> int:
    """注册 4 个"中级"预设：中心扩散交替分组 / 组内行波步进 / 起点方向行波 / 分组呼吸振幅。"""
    from .effect_engine import EffectPreset, ParamSpec

    def reg(preset, fn): engine._reg(preset, fn)

    reg(EffectPreset(
        id="motor_grp_center_alt", name="中心扩散交替分组", category=CAT,
        description="按起点方向（默认中心→两边）重排位置序后做组级奇偶反相，再叠加组间相位差，形成从中心向两端扩散的交替水型。",
        target_channels=MOTOR_CH, icon="◎⇋", tags=TAG_MID,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 3),
                _p_origin(ParamSpec, "中心→两边"), _p_invert(ParamSpec),
                _p_deg(ParamSpec, "group_phase_deg", "组间相位差(度)", 20.0,
                       "相邻组之间的相位差")],
    ), _center_alt)

    reg(EffectPreset(
        id="motor_grp_wave_step", name="组内行波步进", category=CAT,
        description="组内按位置递增相位形成小行波，组与组之间保持同步——与'分组相位步进'互补：那个是组间步进，这个是组内步进。",
        target_channels=MOTOR_CH, icon="≋N", tags=TAG_MID,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 4),
                _p_origin(ParamSpec, "左→右"),
                _p_deg(ParamSpec, "in_group_phase_deg", "组内相位差(度)", 30.0, "同一组内相邻设备之间的相位差")],
    ), _wave_step)

    reg(EffectPreset(
        id="motor_grp_origin_wave", name="起点方向行波", category=CAT,
        description="不分组，按起点方向对每台设备施加连续相位步进，默认中心→两边：水型从中心向两端连续扩散（可切换四种起点方向）。",
        target_channels=MOTOR_CH, icon="⇢◎", tags=TAG_MID,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_origin(ParamSpec, "中心→两边"),
                _p_deg(ParamSpec, "phase_step_deg", "逐台相位差(度)", 15.0, "相邻两台设备之间的相位差")],
    ), _origin_wave)

    reg(EffectPreset(
        id="motor_grp_breathing_groups", name="分组呼吸振幅", category=CAT,
        description="全组共享同一相位摆动，只有振幅按组做慢速呼吸并组间错相——振幅域分组，区别于本族其余预设的相位域分组。",
        target_channels=MOTOR_CH, icon="◐N", tags=TAG_MID,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 3),
                _p_float(ParamSpec, "breath_speed", "呼吸速度", 0.3,
                         lo=0.0, hi=2.0, step=0.05, tip="振幅呼吸包络的速度"),
                _p_deg(ParamSpec, "breath_phase_deg", "组间呼吸相位差(度)", 60.0,
                       "相邻组呼吸包络之间的相位差")],
    ), _breathing_groups)

    return 4

# ── 高级：多机制组合 + 时间演变 ──────────────────────────────────────────────
def _cross_weave(ef, t, sorted_fx, fdefs, params, fps):
    """分组交叉编织：组级奇偶反相 + 组间相位差 + 组内相位差三者叠加，XY 双轴联动。"""
    speed, amp_x, amp_y, center_x, center_y, shape = _read_xy(params, 60.0, 40.0)
    group_size = int(params.get("group_size", 2))
    out_deg = float(params.get("group_phase_deg", 90.0))
    in_deg = float(params.get("in_group_phase_deg", 60.0))
    en_x = bool(params.get("enable_x", True))
    en_y = bool(params.get("enable_y", True))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, pos_in_group = _group_of(float(seq_i), group_size)
        sign = -1.0 if (group_idx % 2 == 1) else 1.0
        base_phase = (t * speed + out_deg / 360.0 * group_idx
                      + in_deg / 360.0 * pos_in_group)
        if en_x:
            raw_x = _waveshape(shape, base_phase) * sign
            ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x)
        if en_y:
            raw_y = _waveshape(shape, base_phase + 0.25) * sign
            ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y)

def _parity_autoswap(ef, t, sorted_fx, fdefs, params, fps):
    """奇偶自动交替演变：组级奇偶反相的"哪一组为正"每隔固定周期整体互换。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 2))
    swap_period = max(0.5, float(params.get("swap_period", 4.0)))
    swap_sign = -1.0 if (int(t // swap_period) % 2 == 1) else 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, _pig = _group_of(float(seq_i), group_size)
        base_sign = -1.0 if (group_idx % 2 == 1) else 1.0
        raw = _waveshape(shape, t * speed) * base_sign * swap_sign
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _phase_morph(ef, t, sorted_fx, fdefs, params, fps):
    """相位演变 同步→龙形→交叉：组间相位差按三角包络随时间从 0°扫到最大值再回落。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 3))
    morph_period = max(1.0, float(params.get("morph_period", 8.0)))
    max_deg = float(params.get("max_phase_deg", 180.0))
    env = 1.0 - abs(((t / morph_period) % 2.0) - 1.0)  # 0→1→0 三角包络
    out_deg = env * max_deg
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, _pig = _group_of(float(seq_i), group_size)
        phase = t * speed + out_deg / 360.0 * group_idx
        raw = _waveshape(shape, phase)
        ef.values[orig_i][ch] = _clamp(center + amp * raw)

def _relay(ef, t, sorted_fx, fdefs, params, fps):
    """分组接力：同一相位摆动下，"接力棒"按周期在各组间轮转，接力组振幅全开、其余变暗。"""
    speed, amp, center, shape, ch = _read_common(params)
    group_size = int(params.get("group_size", 4))
    relay_period = max(0.2, float(params.get("relay_period", 2.0)))
    rest_scale = max(0.0, min(1.0, float(params.get("rest_scale", 0.25))))
    n = len(sorted_fx)
    group_count = max(1, -(-n // max(1, group_size)))
    lead = int(t // relay_period) % group_count
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, _pig = _group_of(float(seq_i), group_size)
        weight = 1.0 if group_idx == lead else rest_scale
        raw = _waveshape(shape, t * speed)
        ef.values[orig_i][ch] = _clamp(center + amp * weight * raw)

def _dragon_relay(ef, t, sorted_fx, fdefs, params, fps):
    """龙形接力演变：相位差扫描(同步→龙形→交叉) + 分组接力振幅同时叠加，XY 双轴。"""
    speed, amp_x, amp_y, center_x, center_y, shape = _read_xy(params, 60.0, 30.0)
    group_size = int(params.get("group_size", 3))
    morph_period = max(1.0, float(params.get("morph_period", 10.0)))
    max_deg = float(params.get("max_phase_deg", 200.0))
    relay_period = max(0.2, float(params.get("relay_period", 2.5)))
    rest_scale = max(0.0, min(1.0, float(params.get("rest_scale", 0.35))))
    en_y = bool(params.get("enable_y", True))
    n = len(sorted_fx)
    group_count = max(1, -(-n // max(1, group_size)))
    lead = int(t // relay_period) % group_count
    env = 1.0 - abs(((t / morph_period) % 2.0) - 1.0)
    out_deg = env * max_deg
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        group_idx, _pig = _group_of(float(seq_i), group_size)
        weight = 1.0 if group_idx == lead else rest_scale
        phase = t * speed + out_deg / 360.0 * group_idx
        raw_x = _waveshape(shape, phase)
        ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * weight * raw_x)
        if en_y:
            raw_y = _waveshape(shape, phase + 0.25)
            ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * weight * raw_y)

def _register_motor_mech_advanced(engine) -> int:
    """注册 5 个"高级"预设：分组交叉编织 / 奇偶自动交替演变 / 相位演变 / 分组接力 / 龙形接力演变。"""
    from .effect_engine import EffectPreset, ParamSpec

    def reg(preset, fn): engine._reg(preset, fn)

    reg(EffectPreset(
        id="motor_grp_cross_weave", name="分组交叉编织", category=CAT,
        description="组级奇偶反相 + 组间相位差 + 组内相位差三者叠加，XY 双轴联动，呈现较复杂的交叉编织水型。",
        target_channels=["motor_x", "motor_y"], icon="⇄◇", tags=TAG_ADV,
        params=[_p_speed(ParamSpec),
                _p_float(ParamSpec, "amp_x", "X幅度(度)", 60.0, lo=0.0, hi=180.0, step=1.0, tip="X轴摆动幅度"),
                _p_float(ParamSpec, "amp_y", "Y幅度(度)", 40.0, lo=0.0, hi=180.0, step=1.0, tip="Y轴摆动幅度"),
                _p_float(ParamSpec, "center_x", "X中心值", 128.0, lo=0.0, hi=255.0, step=1.0, tip="X轴中心基准值"),
                _p_float(ParamSpec, "center_y", "Y中心值", 128.0, lo=0.0, hi=255.0, step=1.0, tip="Y轴中心基准值"),
                _p_wave(ParamSpec), _p_group(ParamSpec, 2),
                _p_deg(ParamSpec, "group_phase_deg", "组间相位差(度)", 90.0, "相邻组之间的相位差"),
                _p_deg(ParamSpec, "in_group_phase_deg", "组内相位差(度)", 60.0, "组内相邻设备之间的相位差"),
                ParamSpec("enable_x", "启用X轴", "bool", True, tooltip="是否输出 motor_x"),
                ParamSpec("enable_y", "启用Y轴", "bool", True, tooltip="是否输出 motor_y")],
    ), _cross_weave)

    reg(EffectPreset(
        id="motor_grp_parity_autoswap", name="奇偶自动交替演变", category=CAT,
        description="组级奇偶反相的'哪一组为正'每隔固定周期整体互换，呈现周期性的整体极性翻转（区别于静态奇偶反相）。",
        target_channels=MOTOR_CH, icon="⇄T", tags=TAG_ADV,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 2),
                _p_float(ParamSpec, "swap_period", "交替周期(秒)", 4.0,
                         lo=0.5, hi=30.0, step=0.5, tip="整体奇偶极性互换的周期", unit="秒")],
    ), _parity_autoswap)

    reg(EffectPreset(
        id="motor_grp_phase_morph", name="相位演变：同步→龙形→交叉", category=CAT,
        description="组间相位差按三角包络随时间从 0°(同步)升到最大值(交叉/近似整组反相)再回落，"
                     "中间过程呈现行波式龙形摆动——用一个预设演示相位差如何驱动'同步→龙形→交叉'的连续演变。",
        target_channels=MOTOR_CH, icon="∿⇌", tags=TAG_ADV,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 3),
                _p_float(ParamSpec, "morph_period", "演变周期(秒)", 8.0, lo=1.0, hi=30.0, step=0.5,
                         tip="同步→龙形→交叉→回落一次循环所需时间", unit="秒"),
                _p_deg(ParamSpec, "max_phase_deg", "最大相位差(度)", 180.0,
                       "演变峰值时的组间相位差，180°=相邻组完全反相(交叉)")],
    ), _phase_morph)

    reg(EffectPreset(
        id="motor_grp_relay", name="分组接力", category=CAT,
        description="全体同相摆动，但'接力棒'按周期在各组之间轮转：接力组振幅全开，其余组振幅降至设定比例，形成分组接力式的强弱轮转水型。",
        target_channels=MOTOR_CH, icon="▶N", tags=TAG_ADV,
        params=[_p_ch(ParamSpec), _p_speed(ParamSpec), _p_amp(ParamSpec),
                _p_center(ParamSpec), _p_wave(ParamSpec), _p_group(ParamSpec, 4),
                _p_float(ParamSpec, "relay_period", "接力周期(秒)", 2.0, lo=0.2, hi=10.0, step=0.1,
                         tip="接力棒轮转到下一组所需时间", unit="秒"),
                _p_float(ParamSpec, "rest_scale", "非接力组幅度比例", 0.25, lo=0.0, hi=1.0, step=0.05,
                         tip="未接力时组振幅相对全开振幅的比例")],
    ), _relay)

    reg(EffectPreset(
        id="motor_grp_dragon_relay", name="龙形接力演变", category=CAT,
        description="相位差扫描(同步→龙形→交叉)与分组接力振幅同时叠加，XY 双轴联动——本族'集大成'高级预设，展示编组+相位差+演变的复杂水型。",
        target_channels=["motor_x", "motor_y"], icon="龍▶", tags=TAG_ADV,
        params=[_p_speed(ParamSpec),
                _p_float(ParamSpec, "amp_x", "X幅度(度)", 60.0, lo=0.0, hi=180.0, step=1.0, tip="X轴摆动幅度"),
                _p_float(ParamSpec, "amp_y", "Y幅度(度)", 30.0, lo=0.0, hi=180.0, step=1.0, tip="Y轴摆动幅度"),
                _p_float(ParamSpec, "center_x", "X中心值", 128.0, lo=0.0, hi=255.0, step=1.0, tip="X轴中心基准值"),
                _p_float(ParamSpec, "center_y", "Y中心值", 128.0, lo=0.0, hi=255.0, step=1.0, tip="Y轴中心基准值"),
                _p_wave(ParamSpec), _p_group(ParamSpec, 3),
                _p_float(ParamSpec, "morph_period", "演变周期(秒)", 10.0, lo=1.0, hi=30.0, step=0.5,
                         tip="相位差扫描一次循环所需时间", unit="秒"),
                _p_deg(ParamSpec, "max_phase_deg", "最大相位差(度)", 200.0, "演变峰值时的组间相位差"),
                _p_float(ParamSpec, "relay_period", "接力周期(秒)", 2.5, lo=0.2, hi=10.0, step=0.1,
                         tip="接力棒轮转到下一组所需时间", unit="秒"),
                _p_float(ParamSpec, "rest_scale", "非接力组幅度比例", 0.35, lo=0.0, hi=1.0, step=0.05,
                         tip="未接力时组振幅相对全开振幅的比例"),
                ParamSpec("enable_y", "启用Y轴", "bool", True, tooltip="是否输出 motor_y")],
    ), _dragon_relay)

    return 5

def register_motor_mech_presets(engine: EffectEngine) -> int:
    """R173 链尾入口：注册全部电机编组机制族预设，断言条数（v37-v40 死注册教训）。"""
    ids_before = set(engine._presets.keys())
    count = (
        _register_motor_mech_basic(engine)
        + _register_motor_mech_mid(engine)
        + _register_motor_mech_advanced(engine)
    )
    ids_after = set(engine._presets.keys())
    added = ids_after - ids_before
    assert len(added) == count, f"motor-mech registration lost presets: {len(added)} != {count}"
    return count
