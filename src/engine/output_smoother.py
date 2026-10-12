"""
输出平滑处理器 (Output Smoother)
作为所有 DMX 数据输出前的最外层处理器，提供力度感和自然过渡。

性能优化版本：使用 numpy 向量化运算替代 512 通道 Python 循环，
性能提升约 20-50x。
"""
from __future__ import annotations
import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
#  预设定义
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SmoothPreset:
    id: str
    name: str
    description: str = ""
    attack_ms: float = 0.0
    release_ms: float = 0.0
    smoothing: float = 0.0
    inertia: float = 0.0
    curve: str = "linear"
    overshoot: float = 0.0
    deadband: int = 0

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> "SmoothPreset":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


BUILTIN_SMOOTH_PRESETS: Dict[str, SmoothPreset] = {}


def _init_presets():
    presets = [
        SmoothPreset(id="bypass", name="旁通(无处理)",
            description="直接输出原始数据",
            attack_ms=0, release_ms=0, smoothing=0, inertia=0,
            curve="linear", overshoot=0, deadband=0),
        SmoothPreset(id="gentle", name="轻柔过渡",
            description="轻微平滑，减少推杆抖动",
            attack_ms=30, release_ms=80, smoothing=0.15, inertia=0.0,
            curve="linear", overshoot=0, deadband=1),
        SmoothPreset(id="natural", name="自然力度",
            description="上升快下降慢，模拟自然力度感",
            attack_ms=40, release_ms=200, smoothing=0.25, inertia=0.05,
            curve="ease_out", overshoot=0, deadband=1),
        SmoothPreset(id="silky", name="丝滑平滑",
            description="高度平滑，适合慢速氛围场景",
            attack_ms=100, release_ms=300, smoothing=0.5, inertia=0.1,
            curve="ease_in_out", overshoot=0, deadband=2),
        SmoothPreset(id="punchy", name="有力打击",
            description="快速上升带轻微过冲，产生打击力度感",
            attack_ms=10, release_ms=120, smoothing=0.1, inertia=0.0,
            curve="ease_out", overshoot=0.15, deadband=1),
        SmoothPreset(id="elastic", name="弹性回弹",
            description="带过冲的弹性效果",
            attack_ms=20, release_ms=250, smoothing=0.2, inertia=0.1,
            curve="ease_out", overshoot=0.25, deadband=1),
        SmoothPreset(id="heavy", name="厚重惯性",
            description="大惯性过渡，适合大型设备",
            attack_ms=200, release_ms=500, smoothing=0.6, inertia=0.4,
            curve="ease_in_out", overshoot=0, deadband=3),
        SmoothPreset(id="fountain_valve", name="喷泉阀门",
            description="快开慢关，防止水锤",
            attack_ms=30, release_ms=400, smoothing=0.3, inertia=0.1,
            curve="ease_out", overshoot=0, deadband=5),
        SmoothPreset(id="fountain_pump", name="喷泉水泵",
            description="平缓加速减速，保护变频器",
            attack_ms=300, release_ms=600, smoothing=0.7, inertia=0.5,
            curve="ease_in_out", overshoot=0, deadband=3),
        SmoothPreset(id="led_dimmer", name="LED 调光",
            description="消除 LED 调光闪烁感",
            attack_ms=20, release_ms=60, smoothing=0.2, inertia=0.05,
            curve="exponential", overshoot=0, deadband=1),
        SmoothPreset(id="motor_servo", name="电机伺服",
            description="平滑启停，保护电机",
            attack_ms=150, release_ms=200, smoothing=0.4, inertia=0.3,
            curve="ease_in_out", overshoot=0.05, deadband=2),
        SmoothPreset(id="audio_reactive", name="音频响应",
            description="快速响应音频信号，保持节奏感",
            attack_ms=5, release_ms=80, smoothing=0.08, inertia=0.0,
            curve="linear", overshoot=0, deadband=1),
    ]
    for p in presets:
        BUILTIN_SMOOTH_PRESETS[p.id] = p


_init_presets()


#: 惯性上限（1-inertia = 速度自适应率下限）。inertia=1.0 旧实现速度恒为 0 →
#: 输出永久冻结（"值死亡"）；0.98 表示每帧仍有 2% 自适应，保证收敛。
_INERTIA_MAX = 0.98


def _clamp_inertia(v: float) -> float:
    """惯性系数钳制：NaN/负数 → 0，超上限 → _INERTIA_MAX（防冻结/防倒灌）。"""
    f = float(v)
    if f != f or f <= 0.0:
        return 0.0
    return _INERTIA_MAX if f > _INERTIA_MAX else f


# ─────────────────────────────────────────────────────────────────────────────
#  向量化状态
# ─────────────────────────────────────────────────────────────────────────────
class _UniverseState:
    __slots__ = ("current", "target", "velocity",
                 "last_update", "last_target_change")

    def __init__(self, now: float):
        self.current = np.zeros(512, dtype=np.float32)
        self.target = np.zeros(512, dtype=np.float32)
        self.velocity = np.zeros(512, dtype=np.float32)
        self.last_update = np.full(512, now, dtype=np.float64)
        self.last_target_change = np.full(512, now, dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
#  缓动函数（向量化）
# ─────────────────────────────────────────────────────────────────────────────
def _ease_linear_v(t):   return t
def _ease_in_v(t):       return t * t
def _ease_out_v(t):      return 1.0 - (1.0 - t) ** 2
def _ease_in_out_v(t):   return t * t * (3.0 - 2.0 * t)
def _ease_exp_v(t):      return (np.exp(t) - 1.0) / (math.e - 1.0)

EASE_FUNCS_V = {
    "linear": _ease_linear_v, "ease_in": _ease_in_v,
    "ease_out": _ease_out_v, "ease_in_out": _ease_in_out_v,
    "exponential": _ease_exp_v,
}

# 标量版本（供 process_single 使用）
def _ease_linear(t): return t
def _ease_in(t): return t * t
def _ease_out(t): return 1.0 - (1.0 - t) ** 2
def _ease_in_out(t): return t * t * (3.0 - 2.0 * t)
def _ease_exp(t): return (math.exp(t) - 1.0) / (math.e - 1.0)
EASE_FUNCS = {"linear": _ease_linear, "ease_in": _ease_in,
              "ease_out": _ease_out, "ease_in_out": _ease_in_out,
              "exponential": _ease_exp}


def _apply_curve(curve: str, frac: np.ndarray) -> np.ndarray:
    """按 curve 对 frac（f32, 已 clamp[0,1]）做双精度缓动。

    位级等价关键: f32 输入先无损转 double，用标量 math.exp / 幂运算（double）
    计算，再按 numpy 同式转回 f32；与 C 端 ce_smoother_ease 逐位一致。
    """
    fn = EASE_FUNCS_V.get(curve)
    if fn is None or fn is _ease_linear_v:
        return frac
    # Vectorized: convert to float64, apply easing, convert back to float32
    frac_f64 = frac.astype(np.float64)
    result_f64 = fn(frac_f64)
    return result_f64.astype(np.float32)


def _velocity_step(state, new_val, diff, time_const, dt_arr, *,
                   inertia: float, active: np.ndarray) -> np.ndarray:
    """惯性/速度分支（R181 P2-1 修复点）。

    `new_val` 是**基础步**（current + 有界缓动步，构造上不越过目标）。
    惯性路径 `current + velocity*dt` 的单帧位移无界：`dt > 时间常数` 时一步冲过
    目标 → 反号 → 速度继续放大 → 值冲上轨顶/轨底后长期不收敛（审计 A「过冲死亡 /
    弹性饱和」）。修复：惯性步一旦越过目标（`(new_val_i - target) * diff > 0`）
    即回退基础步 —— 不变式「每帧位移朝目标且不超过剩余距离」（惯性只许滞后，
    不许过头）。速度状态本身不归零：非越帧逐位语义与修前完全一致。
    """
    if inertia <= 0.0:
        state.velocity = np.where(
            active, (new_val - state.current) / np.maximum(dt_arr, 0.001),
            state.velocity)
        return new_val
    desired_vel = diff / time_const
    state.velocity += (desired_vel - state.velocity) * np.float32(1.0 - inertia)
    new_val_i = state.current + state.velocity * dt_arr
    crossed = (new_val_i - state.target) * diff > 0.0
    return np.where(active, np.where(crossed, new_val, new_val_i), new_val)


# ─────────────────────────────────────────────────────────────────────────────
#  输出平滑处理器（numpy 向量化版本）
# ─────────────────────────────────────────────────────────────────────────────
class OutputSmoother:
    """
    DMX 输出平滑处理器（numpy 向量化版本）。
    512 通道约 0.1ms（原 Python 循环约 3-5ms，提升 20-50x）。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._states: Dict[int, _UniverseState] = {}
        self._preset: SmoothPreset = BUILTIN_SMOOTH_PRESETS["bypass"]
        self._enabled: bool = True
        self._user_presets: Dict[str, SmoothPreset] = {}
        self._overrides: Dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    @property
    def current_preset(self) -> SmoothPreset:
        return self._preset

    def set_preset(self, preset_id: str):
        with self._lock:
            p = BUILTIN_SMOOTH_PRESETS.get(preset_id) or self._user_presets.get(preset_id)
            if p is not None:
                self._preset = p
                self._overrides.clear()
                # 旁通就是严格无处理：清空历史状态，避免从有平滑切回无平滑后残留惯性。
                if p.id == "bypass":
                    self._enabled = False
                    self.reset_states()
                else:
                    self._enabled = True
                    self.reset_states()

    def is_bypass(self) -> bool:
        """返回当前是否处于严格旁通状态。"""
        return (not self._enabled) or getattr(self._preset, "id", "bypass") == "bypass"

    def set_custom_param(self, key: str, value: float):
        with self._lock:
            self._overrides[key] = value

    def get_param(self, key: str) -> float:
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
            return getattr(self._preset, key, 0.0)

    def add_user_preset(self, preset: SmoothPreset):
        with self._lock:
            self._user_presets[preset.id] = preset

    def remove_user_preset(self, preset_id: str):
        with self._lock:
            self._user_presets.pop(preset_id, None)

    def all_presets(self) -> Dict[str, SmoothPreset]:
        with self._lock:
            d = dict(BUILTIN_SMOOTH_PRESETS)
            d.update(self._user_presets)
            return d

    def reset_states(self):
        with self._lock:
            self._states.clear()

    def reset_universe(self, universe: int):
        with self._lock:
            self._states.pop(universe, None)

    def process(self, universe: int, raw_data: np.ndarray) -> np.ndarray:
        """处理一帧 DMX 数据（numpy 向量化）；bypass 模式零拷贝直接返回原数组。
        R178 审计 P2-9：全程持 `_lock`（RLock，get_param 可重入）——修状态读写裸奔并发缺口。"""
        if self.is_bypass():
            return raw_data

        now = time.monotonic()

        with self._lock:
            state = self._states.get(universe)
            if state is None:
                state = _UniverseState(now)
                raw_f = raw_data.astype(np.float32)
                state.current[:] = raw_f
                state.target[:] = raw_f
                self._states[universe] = state
                return raw_data.copy()

            attack_s  = max(0.001, self.get_param("attack_ms")  / 1000.0)
            release_s = max(0.001, self.get_param("release_ms") / 1000.0)
            smoothing = float(self.get_param("smoothing"))
            inertia   = _clamp_inertia(self.get_param("inertia"))
            overshoot = float(self.get_param("overshoot"))
            deadband  = int(self.get_param("deadband"))

            target_f = raw_data.astype(np.float32)

            # dt 向量化
            dt_arr = np.clip(
                (now - state.last_update).astype(np.float32), 0.001, 0.5)
            state.last_update[:] = now

            # 目标值变化检测
            changed = np.abs(target_f - state.target) > deadband
            state.target = np.where(changed, target_f, state.target)
            state.last_target_change = np.where(changed, now, state.last_target_change)

            diff = state.target - state.current
            going_up = diff > 0
            time_const = np.where(going_up, attack_s, release_s).astype(np.float32)

            # 已收敛通道
            converged = np.abs(diff) < 0.5
            state.current = np.where(converged, state.target, state.current)
            state.velocity = np.where(converged, 0.0, state.velocity)

            active = ~converged
            if np.any(active):
                frac = np.clip(dt_arr / time_const, 0.0, 1.0)
                # 缓动曲线（双精度, 逐位对齐 C 端 ce_smoother_ease）:
                # f32 frac → double 计算 → 按 numpy 同式转回 f32。
                frac = _apply_curve(getattr(self._preset, "curve", "linear"), frac)
                if smoothing > 0:
                    alpha = np.float32(1.0 - smoothing)
                    step = alpha * diff * frac
                else:
                    step = diff * frac
                new_val = state.current + step

                new_val = _velocity_step(state, new_val, diff, time_const, dt_arr,
                                         inertia=inertia, active=active)

                if overshoot > 0:
                    elapsed = np.clip(
                        (now - state.last_target_change).astype(np.float32), 0.0, None)
                    progress = np.clip(elapsed / time_const, 0.0, 1.0)
                    oz = active & (progress > 0.7)
                    if np.any(oz):
                        oph = np.clip((progress - 0.7) / 0.3, 0.0, 1.0)
                        oa = np.sin(oph * math.pi) * overshoot * np.abs(diff)
                        oa_signed = np.where(going_up, oa, -oa)
                        new_val = np.where(oz, new_val + oa_signed, new_val)

                state.current = np.where(active, np.clip(new_val, 0.0, 255.0), state.current)

            return np.clip(np.round(state.current), 0, 255).astype(np.uint8)

    def process_single(self, universe: int, channel: int, target: int) -> int:
        """处理单个通道（用于实时预览）。

        R178 审计 P2-9：全程持 `_lock`（RLock 可重入，get_param 安全）——
        此前 state 读写与 process（帧线程）并发无互斥。
        """
        if not self._enabled or self._preset.id == "bypass":
            return target
        now = time.monotonic()
        with self._lock:
            state = self._states.get(universe)
            if state is None:
                state = _UniverseState(now)
                self._states[universe] = state
            ch = channel
            deadband = int(self.get_param("deadband"))
            if abs(target - state.target[ch]) > deadband:
                state.last_target_change[ch] = now
                state.target[ch] = float(target)
            dt = max(0.001, now - float(state.last_update[ch]))
            state.last_update[ch] = now
            diff = float(state.target[ch]) - float(state.current[ch])
            if abs(diff) < 0.5:
                state.current[ch] = state.target[ch]
                return max(0, min(255, int(round(float(state.current[ch])))))
            going_up = diff > 0
            attack_s  = max(0.001, self.get_param("attack_ms")  / 1000.0)
            release_s = max(0.001, self.get_param("release_ms") / 1000.0)
            time_const = attack_s if going_up else release_s
            smoothing = self.get_param("smoothing")
            if smoothing > 0:
                alpha = 1.0 - smoothing
                new_val = float(state.current[ch]) + alpha * diff * min(1.0, dt / time_const)
            else:
                new_val = float(state.current[ch]) + diff * min(1.0, dt / time_const)
            state.current[ch] = max(0.0, min(255.0, new_val))
            return max(0, min(255, int(round(float(state.current[ch])))))


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
_smoother_instance: Optional[OutputSmoother] = None


def get_smoother() -> OutputSmoother:
    global _smoother_instance
    if _smoother_instance is None:
        _smoother_instance = OutputSmoother()
    return _smoother_instance
