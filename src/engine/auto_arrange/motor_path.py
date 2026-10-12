"""
motor_path — 电机摇杆路径生成（逻辑域，中心=128.0）

产出逐帧 motor_x / motor_y 的虚拟路径，目标是"像人手摇"而不是几何完美圆。
本模块只做纯数值计算（numpy，无 Qt、无 time/random 全局态），完全确定性：
同样的入参 + seed 永远给同样的结果，可复现、可单测对标真实数据。

对标依据：用户 30 个真实 clip 实测（HJC，逻辑中心 128，见 .codex_motor_report.txt）
  - 速度：中位 76 DMX/s，常规段最高 ~169，"电机过渡"瞬间冲 200~550。
  - 半径（相对中心 128）：中位 31，多在 16~60，极少满程 128（仅 1 clip 到 ±128）。
  - 形状：30 个全是"不规则"（半径变异 cv 0.4~1.0，纯圆 < 0.35），绝不能是完美圆。

三道硬保险（与上面三类风险一一对应）：
  1. 防过快  —— 转差率硬限幅：生成后逐帧把 motor 步进 clamp 到 ≤ max_step
                 = speed_limit_dmx_per_s / fps。
  2. 防范围错 —— 半径按能量缩放 radius=lerp(18,60,energy)，封顶 90，并把瞬时
                 半径夹在 ≤120（永不超满程 128，即落点恒在 [0,256] 内）。
  3. 防不匹配 —— 有机层：基础参数路径 + 二次谐波 + 低频确定性噪声扰动 + 可选
                 旋律(f0)调制，使半径变异 cv ≈ 0.5，呈现手摇的不规则摆动。

不在本模块做的事：
  - xy_reference 每台 angle_deg 旋转 —— 在输出收口处统一做，本模块只产虚拟路径。
  - 16bit 粗精调拆分 —— 渲染层做。本模块输出 0..255 的 float（保留小数供 16bit）。
"""
from __future__ import annotations

import math
import os
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# ── numba 门控（R183 3.3 计算层批，与 route_animator.track_smooth 同款）──────
# QASCADE_NUMBA=0（或 false/off/no）退纯 Python：两条路径同一算法文本，
# 金标对拍逐位一致（tests/test_r183_compute_golden.py）。
def _env_enabled(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "off", "no")


try:
    if _env_enabled("QASCADE_NUMBA"):
        from numba import njit as _njit
    else:
        _njit = None
except Exception:  # pragma: no cover - numba 缺失/损坏时静默回退
    _njit = None

NUMBA_ACTIVE = _njit is not None


# ════════════════════════════════════════════════════════════════════════
# 常量：与真实实测对齐
# ════════════════════════════════════════════════════════════════════════
_RADIUS_MIN = 18.0     # energy=0 时的基础半径
_RADIUS_MAX = 60.0     # energy=1 时的基础半径
_RADIUS_CAP = 90.0     # 基础半径封顶
_RADIUS_INST_CAP = 120.0  # 瞬时半径封顶（< 满程 128，确保落点不越界）

_FREQ_MIN = 0.20       # energy=0 时的基础角频率（Hz，约 5s 一圈）
_FREQ_MAX = 0.45       # energy=1 时的基础角频率（约 2.2s 一圈）

# 转差率（速度上限，DMX/s）：柔美 ≈ 60，激烈 ≈ 150，电机过渡瞬间 ≈ 400
_SPEED_LIMIT_CALM = 60.0
_SPEED_LIMIT_FIERCE = 150.0
_SPEED_LIMIT_TRANSITION = 400.0

_PATH_TYPES = ("circle", "arc", "lissajous", "breath", "custom")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ════════════════════════════════════════════════════════════════════════
# 有机层：确定性低频半径调制（破除完美圆，制造 cv≈0.5 的不规则）
# ════════════════════════════════════════════════════════════════════════
def _radius_envelope(vt: np.ndarray, base_freq: float, duration: float,
                     rng: np.random.RandomState) -> np.ndarray:
    """乘性半径包络：1 + 几个低频谐波之和。确定性（由 rng/seed 决定）。

    每个谐波幅度 ~0.30..0.45，叠加后使半径 cv 落在目标带内。频率有下限
    （保证整段内至少摆动 ~1.5 个周期），否则短窗里看起来像没变，退化成接近
    完美圆（cv 掉到 0.35 以下）。第一个谐波给一个保底的"主摆动"幅度，避免
    几个随机谐波相位恰好抵消导致 cv 偏低（曾见 seed 不巧时 cv≈0.29）。
    下夹到 0.40 防半径塌成针尖、并稳住中位半径不被极小值拖到 16 以下。
    """
    env = np.ones_like(vt)
    # 至少 ~1.5 个周期落在 [0,duration] 内（duration<=0 时退化为只用 base_freq）
    f_floor = (1.5 / duration) if duration > 1e-6 else base_freq
    # 主摆动：保底幅度，保证 cv 不被随机抵消打垮
    f0 = max(base_freq * rng.uniform(0.4, 0.9), f_floor)
    a0 = rng.uniform(0.45, 0.55)
    ph0 = rng.uniform(0.0, 2.0 * np.pi)
    env += a0 * np.sin(2.0 * np.pi * f0 * vt + ph0)
    # 次级谐波：制造不规律
    for _ in range(2):
        fk = max(base_freq * rng.uniform(0.25, 1.4), f_floor)
        ak = rng.uniform(0.25, 0.40)
        phk = rng.uniform(0.0, 2.0 * np.pi)
        env += ak * np.sin(2.0 * np.pi * fk * vt + phk)
    return np.clip(env, 0.40, None)


def _angle_wobble(theta: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    """角度抖动：叠加二次谐波 + 一个低相位扰动，让转角忽快忽慢（手摇感）。"""
    a2 = rng.uniform(0.12, 0.28)
    ph2 = rng.uniform(0.0, 2.0 * np.pi)
    a3 = rng.uniform(0.06, 0.16)
    ph3 = rng.uniform(0.0, 2.0 * np.pi)
    return theta + a2 * np.sin(2.0 * theta + ph2) + a3 * np.sin(3.0 * theta + ph3)


def _resample_curve(curve: Sequence[float], n: int) -> np.ndarray:
    """把任意长度的 f0/自定义曲线重采样到 n 点（线性插值）。"""
    arr = np.asarray(curve, dtype=float).ravel()
    if arr.size == 0:
        return np.zeros(n)
    if arr.size == 1:
        return np.full(n, float(arr[0]))
    src = np.linspace(0.0, 1.0, arr.size)
    dst = np.linspace(0.0, 1.0, n)
    return np.interp(dst, src, arr)


def _f0_scale(f0_curve: Sequence[float], n: int) -> np.ndarray:
    """旋律轮廓 → 半径缩放因子。音高高→摆幅大。归一到 [0.75, 1.35]。

    对 f0 在其自身极差内归一化（绝对音高单位无关，MIDI/Hz 皆可），
    无有效极差（全常数）时返回全 1（不调制）。
    """
    res = _resample_curve(f0_curve, n)
    # 只用 >0 的部分判定极差（0 常表示静音/无基频）
    valid = res[res > 0]
    if valid.size == 0:
        return np.ones(n)
    lo = float(valid.min())
    hi = float(valid.max())
    if hi - lo < 1e-6:
        return np.ones(n)
    norm = np.clip((res - lo) / (hi - lo), 0.0, 1.0)
    return 0.75 + 0.60 * norm


# ════════════════════════════════════════════════════════════════════════
# 基础参数路径：给定半径序列 r 与虚拟时间 vt，产出单位前的 (ux, uy)
# ════════════════════════════════════════════════════════════════════════
def _base_path(path_type: str, vt: np.ndarray, base_freq: float,
               rng: np.random.RandomState) -> Tuple[np.ndarray, np.ndarray]:
    """返回 (cosish, sinish) 角向分量（未乘半径）。"""
    w = 2.0 * np.pi * base_freq
    phase0 = rng.uniform(0.0, 2.0 * np.pi)

    if path_type == "circle":
        theta = w * vt + phase0
        theta = _angle_wobble(theta, rng)
        return np.cos(theta), np.sin(theta)

    if path_type == "arc":
        # 钟摆式：角度在一段弧内来回扫，不绕整圈
        arc_center = rng.uniform(0.0, 2.0 * np.pi)
        arc_half = rng.uniform(0.9, 1.6)  # 约 ±55°..±92°
        theta = arc_center + arc_half * np.sin(w * vt + phase0)
        theta = _angle_wobble(theta, rng)
        return np.cos(theta), np.sin(theta)

    if path_type == "lissajous":
        a = rng.choice([2, 3])
        b = a + rng.choice([1, 2])
        delta = rng.uniform(0.0, np.pi)
        # 频率压低：利萨如的角速度 ~max(a,b)*w，太高会被转差率限幅打到塌缩成
        # 小半径（速度被钳到上限、轨迹追不上目标点）。压到 1/max(a,b) 量级。
        wl = w / float(max(a, b))
        ux = np.sin(a * wl * vt + phase0)
        uy = np.sin(b * wl * vt + delta)
        return ux, uy

    if path_type == "breath":
        # 柔美呼吸摆动：慢角速 + 明显径向脉动（半径在外层已脉动），小范围游走。
        # slow 取 0.9：再低则即便在最低能量下，切向速度也会跌破实测速度带下限
        # （~40 DMX/s）；这里保持"慢但仍明确在动"。
        slow = 0.9
        theta = (w * slow) * vt + phase0
        theta = _angle_wobble(theta, rng)
        # 叠一个缓慢漂移，让中心轻微游走而非死绕一点
        drift = 0.25 * np.sin(w * 0.2 * vt + phase0)
        return np.cos(theta) + drift, np.sin(theta + 0.5) - drift

    # custom：默认 8 字 / 复合游走（含额外低频，更"无规律"）
    theta = w * vt + phase0
    theta = _angle_wobble(theta, rng)
    f2 = base_freq * rng.uniform(0.4, 0.9)
    ph2 = rng.uniform(0.0, 2.0 * np.pi)
    ux = np.cos(theta) + 0.35 * np.cos(2.0 * np.pi * f2 * vt + ph2)
    uy = np.sin(2.0 * theta) * 0.8 + 0.30 * np.sin(2.0 * np.pi * f2 * vt + ph2)
    return ux, uy


# ════════════════════════════════════════════════════════════════════════
# 转差率硬限幅：逐帧把欧氏步进夹到 max_step（速度 = 步进*fps ≤ speed_limit）
# R183 3.3 计算层批：逐帧 Python 循环（roadmap 实测 47ms）→ numba
# njit(cache=True) 内核；QASCADE_NUMBA=0 走 _slew_clamp_py 退路（原实现
# 复件），两路逐位一致（math.hypot 与 np.hypot 同为 libm hypot）。
# ════════════════════════════════════════════════════════════════════════
def _slew_clamp_py(x: np.ndarray, y: np.ndarray,
                   max_step: float) -> Tuple[np.ndarray, np.ndarray]:
    """原纯 Python 实现复件：QASCADE_NUMBA=0 退路 + 金标对拍参照。"""
    n = x.size
    ox = np.empty(n)
    oy = np.empty(n)
    if n == 0:
        return ox, oy
    ox[0] = x[0]
    oy[0] = y[0]
    for i in range(1, n):
        dx = x[i] - ox[i - 1]
        dy = y[i] - oy[i - 1]
        dist = np.hypot(dx, dy)
        if dist > max_step and dist > 1e-12:
            scale = max_step / dist
            dx *= scale
            dy *= scale
        ox[i] = ox[i - 1] + dx
        oy[i] = oy[i - 1] + dy
    return ox, oy


if _njit is not None:
    @_njit(cache=True)
    def _slew_clamp_kernel(x, y, max_step):
        n = x.shape[0]
        ox = np.empty(n)
        oy = np.empty(n)
        if n == 0:
            return ox, oy
        ox[0] = x[0]
        oy[0] = y[0]
        for i in range(1, n):
            dx = x[i] - ox[i - 1]
            dy = y[i] - oy[i - 1]
            dist = math.hypot(dx, dy)
            if dist > max_step and dist > 1e-12:
                scale = max_step / dist
                dx *= scale
                dy *= scale
            ox[i] = ox[i - 1] + dx
            oy[i] = oy[i - 1] + dy
        return ox, oy
else:
    _slew_clamp_kernel = None


def _slew_clamp(x: np.ndarray, y: np.ndarray,
                max_step: float) -> Tuple[np.ndarray, np.ndarray]:
    if _slew_clamp_kernel is not None:
        return _slew_clamp_kernel(x, y, float(max_step))
    return _slew_clamp_py(x, y, float(max_step))


# ════════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════════
def gen_motor_path(path_type: str,
                   duration: float,
                   fps: int = 40,
                   speed: float = 1.0,
                   energy: float = 0.5,
                   radius: Optional[float] = None,
                   center: Tuple[float, float] = (128.0, 128.0),
                   seed: int = 0,
                   f0_curve: Optional[Sequence[float]] = None,
                   amplitude_mode: bool = True,
                   motor_transition: bool = False) -> Dict[str, np.ndarray]:
    """生成电机摇杆虚拟路径。

    参数
    ----
    path_type : {'circle','arc','lissajous','breath','custom'}
    duration  : 秒。帧数 n = int(round(duration*fps)) + 1。
    fps       : 帧率（默认 40）。
    speed     : 时间缩放。虚拟时间 vt = i/fps * speed（确定性，不读系统时钟）。
    energy    : 0..1，驱动基础半径与角频率（越大越大越快）。
    radius    : 显式基础半径（覆盖 energy 推导），仍封顶 90。
    center    : 逻辑中心，默认 (128,128)。
    seed      : 确定性伪随机种子（numpy RandomState）。
    f0_curve  : 可选旋律基频轮廓，调制摆幅（高音→大摆幅）。
    amplitude_mode : True 时叠加慢速幅度包络（手摇时摆幅时大时小）。
    motor_transition : True 时放开速度上限到 ~400（电机过渡瞬冲）。

    返回
    ----
    {'x': np.ndarray(n) float 0..255, 'y': np.ndarray(n) float 0..255}
    """
    if path_type not in _PATH_TYPES:
        raise ValueError(f"path_type 必须是 {_PATH_TYPES} 之一，收到 {path_type!r}")

    energy = float(np.clip(energy, 0.0, 1.0))
    fps = int(fps)
    if fps <= 0:
        raise ValueError("fps 必须为正")

    n = int(round(float(duration) * fps)) + 1
    if n < 1:
        n = 1

    cx, cy = float(center[0]), float(center[1])
    i = np.arange(n, dtype=float)
    vt = i / fps * float(speed)   # 虚拟时间（秒），完全确定

    rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)

    # ── 基础半径（保险 2）──────────────────────────────────────────────
    if radius is None:
        base_r = _lerp(_RADIUS_MIN, _RADIUS_MAX, energy)
    else:
        base_r = float(radius)
    base_r = min(base_r, _RADIUS_CAP)

    # ── 基础角频率（随能量）────────────────────────────────────────────
    base_freq = _lerp(_FREQ_MIN, _FREQ_MAX, energy)

    # ── 基础参数路径 ───────────────────────────────────────────────────
    ux, uy = _base_path(path_type, vt, base_freq, rng)

    # ── 有机层：半径调制（保险 3，制造 cv≈0.5 的不规则）────────────────
    r_env = _radius_envelope(vt, base_freq, float(duration), rng)
    r = base_r * r_env

    # 幅度包络（手摇感：摆幅缓慢起伏）
    if amplitude_mode:
        amp_ph = rng.uniform(0.0, 2.0 * np.pi)
        amp_env = 1.0 + 0.30 * np.sin(2.0 * np.pi * base_freq * 0.2 * vt + amp_ph)
        r = r * amp_env

    # 旋律调制（可选）
    if f0_curve is not None:
        r = r * _f0_scale(f0_curve, n)

    # 瞬时半径封顶（保险 2：永不超满程，落点恒在 [0,256] 内）
    r = np.clip(r, 0.0, _RADIUS_INST_CAP)

    # 角向分量本身可能 >1（lissajous/custom 是和），按其向量长度把半径理解为
    # "标称摆幅"作用在归一方向上，避免复合路径把落点推出界。
    norm = np.hypot(ux, uy)
    norm = np.where(norm < 1e-9, 1.0, norm)
    dirx = ux / norm
    diry = uy / norm
    x_raw = cx + r * dirx
    y_raw = cy + r * diry

    # ── 转差率硬限幅（保险 1）──────────────────────────────────────────
    if motor_transition:
        speed_limit = _SPEED_LIMIT_TRANSITION
    else:
        speed_limit = _lerp(_SPEED_LIMIT_CALM, _SPEED_LIMIT_FIERCE, energy)
    max_step = speed_limit / fps
    x_c, y_c = _slew_clamp(x_raw, y_raw, max_step)

    # 最终夹到 0..255（保留小数供 16bit 渲染层使用）
    x_out = np.clip(x_c, 0.0, 255.0)
    y_out = np.clip(y_c, 0.0, 255.0)

    return {"x": x_out, "y": y_out}
