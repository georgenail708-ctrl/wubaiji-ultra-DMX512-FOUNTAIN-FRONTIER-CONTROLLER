# -*- coding: utf-8 -*-
"""Draw 规则卡 DSP 原语（R183 M2；纯逻辑无 Qt，自 audio_mod_draw_compile 抽出
以满足文件行数门禁——同 draw_canvas_edit 先例）。

出处登记：ai-memory/_references/m2-dsp-adoption.md（先搜后写纪律）：
  · 一阶包络跟随  musicdsp.org #136：g=exp(-1/(sr·τ))，帧口径 sr=1/dt；
  · EMA 平滑      CCRMA JOS One-Pole：α=1-exp(-dt/τ)，τ 由旧 0..1 系数反解；
  · 双阈值迟滞门  dsp-envelope-formulas §3（threshold/threshold_close/hold_ms）；
  · 时基换算      Timebase（beat/hz/bars/dotted/triplet，乐理系数）；
  · Random 模式   Random discrete/slew（确定性，烘焙/回放可复现）。
per-rule 帧状态（_RULE_PREV_STATE）挂本模块；失效键 = audio_modulation.rules_key
（状态/参数编辑即自动失效），driver.invalidate_caches 经
audio_mod_draw_compile.clear_caches 挂钩清空。
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Optional

#: 时基单位 → 每单位周期占拍数（freq 语义=每该单位的周期数；乐理：
#: 附点=1.5 拍、三连音=2/3 拍、小节按 4/4；hz 动态=每秒拍数见下）
_BEATS_PER_CYCLE = {"beat": 1.0, "bars": 4.0, "dotted": 1.5,
                    "triplet": 2.0 / 3.0, "sixteenth": 0.25}
#: Random 滑移模式：新值在周期前多大比例内线性滑入（确定性，无逐帧状态）
_RANDOM_SLEW_FRAC = 0.25
#: 帧间步长上界：dt ∉ (0, 0.5s]（首帧/回退/seek 大跳）→ 丢弃滤波状态直通
_MAX_FILTER_DT = 0.5
#: 滤波缺省帧率口径（EMA τ 反解基准；musicdsp #136 sr 的帧等价物）
_DEFAULT_FPS = 60.0
#: 多灯展开的 per-(规则, 桶) 状态槽步长（R183 3.4b；桶数上限同 audio_mod_spread）
FAN_STRIDE = 64

#: 规则卡 per-rule 帧状态（engine 级单例；键=rules_key 指纹）。
#: vals = {规则行下标: {"y": EMA 状态, "fy": 跟随状态, "gate": (开, 保持计时)}}
_RULE_PREV_STATE: dict = {"key": None, "t": None, "vals": {}}
# Existing live callers/exports keep the original dict. Offline jobs bind their
# own pure history, using installed CPython 3.13 ContextVar/PSF-2.0 semantics.
_RULE_HISTORY = ContextVar("qascade_rule_history", default=_RULE_PREV_STATE)


@contextmanager
def isolated_rule_history():
    """Original DSP math with one job-owned filter history; never restore live data."""
    token = _RULE_HISTORY.set({"key": None, "t": None, "vals": {}})
    try:
        yield
    finally:
        _RULE_HISTORY.reset(token)


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp01(v) -> float:
    return min(1.0, max(0.0, _f(v, 0.0)))


def clear_caches() -> None:
    """清理入口（R183 M2：清 per-rule 滤波状态；driver.invalidate_caches
    经 audio_mod_draw_compile 挂钩调用；接口与 driver 对齐）。"""
    _RULE_HISTORY.get().update(key=None, t=None, vals={})


def _frame_dt(state: dict, t: float) -> float:
    """规则路径帧间步长（R183 M2 滤波状态管理单点）。

    键 = `audio_modulation.rules_key` 指纹（含完整 params 表，规则/参数编辑
    即自动失效重播种）。dt ∉ (0, 0.5s]（首帧/回退/seek 大跳）→ 返回 0 =
    本帧直通并重播种（防大跳拖尾）。指纹现算异常 → 键 None 恒不等 → 滤波
    退化为直通（安全降级）。
    """
    try:
        from .audio_modulation import rules_key
        key = rules_key(state)
    except Exception:
        key = None
    history = _RULE_HISTORY.get()
    if key != history["key"]:
        history.update(key=key, t=None, vals={})
    prev_t = history["t"]
    history["t"] = t
    if prev_t is None:
        return 0.0
    dt = t - prev_t
    return dt if 0.0 < dt <= _MAX_FILTER_DT else 0.0


def rules_ctx(state: dict, features, t: float, env: float, amount: float) -> dict:
    """规则求值上下文（`scale_map` / 展开路径共用单点；R183 3.4b 移入）。

    ★ 关键纪律：`_frame_dt` 每帧只能调一次（它推进 `_RULE_PREV_STATE["t"]`），
    调用方按帧择一入口（有展开走 fan 模块，否则 `scale_map`）。
    """
    return {
        "sources": {str(s.get("id", "")): s for s in (state.get("sources") or [])
                    if isinstance(s, dict) and s.get("id")},
        "features": features,
        "bpm": _f(getattr(features, "bpm", 0.0), 0.0) or 120.0,
        "t": float(t),
        "env": max(0.0, _f(env, 0.0)) * _clamp01(amount),
        "dt": _frame_dt(state, float(t)),
        "frames": _RULE_HISTORY.get()["vals"],
    }


def _beats_per_cycle(unit, bpm: float) -> float:
    """时基单位 → 每单位周期占的拍数（命名对照 Timebase）。

    beat=1 拍；hz=bpm/60（1 Hz 周期=一秒=每秒拍数拍）；bars=4（4/4）；
    dotted=1.5（附点）；triplet=2/3（三连音）；sixteenth=0.25（十六分，
    R183 成熟化轮②）。未知单位回退 beat（旧档兼容）。
    """
    u = str(unit or "beat")
    if u == "hz":
        return max(1e-6, _f(bpm, 120.0) or 120.0) / 60.0
    return _BEATS_PER_CYCLE.get(u, 1.0)


def _random_shaped(raw: float, mode: str) -> float:
    """Random 波形 discrete/slew 模式（对照 Random "discrete or
    slewed"；确定性——同一 raw 恒同值，烘焙/回放/导出可复现）。

    discrete = 整周期 S&H（周期边界跳变）；slew = 上一周期持有值在本周期
    前 _RANDOM_SLEW_FRAC 段线性滑向新值（周期边界连续、无逐帧状态）。
    """
    from ..ui.effect_engine import _det_rand
    x = _f(raw, 0.0)
    n = math.floor(x)
    target = _clamp01(_det_rand(float(n)))
    if str(mode) != "slew":
        return target
    prev = _clamp01(_det_rand(float(n - 1.0)))
    frac = x - n
    k = min(1.0, frac / _RANDOM_SLEW_FRAC)
    return prev + (target - prev) * k


def _gate_step(val: float, params: dict, gate,
              dt: float):
    """双阈值迟滞门一步（dsp-envelope-formulas §3）。

    threshold=开阈值（≥ 开）、threshold_close=关阈值（< 关）、两值之间保持
    当前态（临界不抖）；hold_ms=最短保持（开态跌破关阈值后累计时间未满
    hold 前维持开）。返回 (是否开, 新gate, 值)。close/hold 缺省 0 → 逐位
    等价旧单阈值硬门（布尔序列相同，test_r183_m2_dsp 在案）。
    """
    thr = _clamp01(params.get("threshold", 0.0))
    if thr <= 0.0:
        return True, gate, val
    close = _clamp01(params.get("threshold_close", 0.0)) or thr
    hold_ms = max(0.0, _f(params.get("hold_ms"), 0.0))
    opn, held = gate if isinstance(gate, tuple) else (False, 0.0)
    if opn and val < close:
        held += max(0.0, _f(dt, 0.0))
        opn = held * 1000.0 < hold_ms      # hold（最短保持 ms）未满不开门
    elif opn:
        held = 0.0                          # 高于关阈值：保持计时清零
    elif val >= thr:
        opn, held = True, 0.0
    return bool(opn), (bool(opn), held), val


def _ema_step(val: float, prev, sm: float, dt: float) -> float:
    """EMA 平滑一步（CCRMA JOS 一阶低通；dsp-envelope-formulas §2）。

    α = 1 − exp(−dt/τ)，τ 由旧 0..1 系数反解：τ = −(1/60)/ln(sm)（sm 即旧
    60fps 口径的 1−α）→ 60fps 下数值等价旧 `prev*sm + val*(1−sm)`，其他帧率
    下帧率无关。sm≤0 / prev 缺省 / dt≤0 → 直通（旧档/首帧零回归）。
    """
    if prev is None or sm <= 0.0 or dt <= 0.0:
        return val
    if sm >= 1.0:
        return float(prev)                  # τ→∞：冻结（旧 sm=1 的解析延续）
    tau = -(1.0 / _DEFAULT_FPS) / math.log(sm)
    alpha = 1.0 - math.exp(-dt / tau)
    return float(prev) + alpha * (val - float(prev))


def _follow_step(val: float, prev, params: dict, dt: float) -> float:
    """attack/release 一阶包络跟随一步（musicdsp #136 单乘法形式）。

    g = exp(−dt/τ)（帧口径 sr=1/dt，与原式 exp(−1/(sr·τ)) 等价）；升用
    attack、降用 release；τ≤0 = 瞬时。prev 缺省 / dt≤0 → 直通播种
    （首帧/回退/seek 零拖尾）。
    """
    if prev is None or dt <= 0.0:
        return val
    tau = (_f(params.get("attack"), 0.0) if val > prev
           else _f(params.get("release"), 0.0))
    if tau <= 0.0:
        return val
    g = math.exp(-dt / tau)
    return float(prev) + (1.0 - g) * (val - float(prev))


def _resolve_wave_fn(wave: str, u: float, params: dict, bpm: float,
                     phase_lift: float):
    """波形函数解析 + 相位驱动量计算（_envelope_shape 前段拆出压 CC）。

    返回 (fn, raw, mode)；wave 空/未知 → None（调用方回 1.0）。
    """
    try:
        from ..ui.effect_engine import WAVE_FUNCS
        fn = WAVE_FUNCS.get(wave)
    except Exception:
        fn = None
    if fn is None:
        return None
    rate = (max(0.25, _f(params.get("freq"), 2.0))
            / _beats_per_cycle(params.get("freq_unit"), bpm))
    raw = u * rate + _f(params.get("phase"), 0.0) + _f(phase_lift, 0.0)
    return fn, raw, str(params.get("random_mode", "") or "")


def _ordinal_wave_value(wave: str, fn, raw: float) -> float:
    """S&H/Wander 求值（清查②①F1/F2）：吃**周期序数** raw（floor 选步/步间
    插值）——修前统一 raw % 1.0 → floor 恒 0：S&H 恒 0、Wander 锁死单条
    S 曲线。legacy 8 波形维持取模零回归。"""
    return _clamp01(_f(fn(raw), 1.0))


def _envelope_shape(params: dict, u: float, bpm: float = 120.0,
                    phase_lift: float = 0.0) -> float:
    """参数化包络：波形(wave)×频率(freq) 与 相位（返回 0..1）。

    `u` = 拍位驱动量（t·bpm/60）。wave 空 = 恒定 1（纯信号调制）。
    R183 M2：freq 吃时基单位（缺省 beat 逐位不变）；Random 支持 discrete/slew。
    R183 3.4b：`phase_lift`（0..1 周期）= 多灯展开逐灯相位偏移（与卡上 phase
    相加；缺省 0 → 逐位回旧口径）。
    """
    wave = str(params.get("wave", "") or "")
    if not wave:
        return 1.0
    resolved = _resolve_wave_fn(wave, u, params, bpm, phase_lift)
    if resolved is None:
        return 1.0
    fn, raw, mode = resolved
    if wave == "Random" and mode:
        return _random_shaped(raw, mode)
    if wave in ("S&H", "Wander"):        # F1/F2：吃周期序数（见 helper）
        return _ordinal_wave_value(wave, fn, raw)
    return _clamp01(_f(fn(raw % 1.0), 1.0))


def _apply_dynamics(val: float, params: dict, prev_smoothed=None,
                   gate=None, dt: float = 0.0):
    """阈值门/反相/EMA 平滑 → (输出值, 新平滑状态, 新门状态)。

    R183 M2 真接线（dsp-envelope-formulas §1/§2/§3）：
      · threshold 分支走 `_gate_step`（threshold_close/hold_ms 可选；缺省 0 =
        旧单阈值硬门逐位同口径；门关返回 0.0——与旧 `return 0.0` 一致地跳过
        invert/smoothing）；
      · smoothing 走 `_ema_step`（EMA τ；prev 由 _rule_value 的 per-rule 帧状态
        传入——修前调用处从不传 prev = 无状态假接线，审计问题在案）；
      · attack/release 一阶跟随在本函数之后（_rule_value → `_follow_step`）。
    """
    opn, gate, val = _gate_step(val, params, gate, dt)
    if not opn:
        return 0.0, prev_smoothed, gate
    if _f(params.get("invert"), 0.0) > 0.5:
        val = 1.0 - val
    new_y = _ema_step(val, prev_smoothed,
                     _clamp01(params.get("smoothing", 0.0)), dt)
    return _clamp01(new_y), new_y, gate
