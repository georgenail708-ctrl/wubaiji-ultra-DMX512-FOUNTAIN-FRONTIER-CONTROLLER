"""
SafetyInterlock — 硬件安全联锁 (Hardware Safety Interlock)

六态安全状态机，插入于 output_smoother 与 _send_to_hw 之间。
在 DMX 数据到达网络层之前执行每设备约束检查：
  min/max、max_change_rate、safe_default
违规时根据严重程度执行 钳位(clamp) 或 停机(stop)。

状态流：
  Disarmed → Armed → Warning → SoftLimit → HardStop → Blackout

设计原则：
- 纯 numpy 向量化，512 通道全检 < 0.01ms
- 状态在 SafetyInterlock 实例内维护，不依赖外部状态
- Blackout 是熔断级保护：需显式 reset() 恢复
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Tuple

import numpy as np

_log = logging.getLogger(__name__)

# ── 状态枚举 ─────────────────────────────────────────────────────────────


class InterlockState(IntEnum):
    """六态安全状态机。数值越高越严重。"""
    DISARMED = 0       # 未启用，所有数据直通
    ARMED = 1          # 已启用，正常监控
    WARNING = 2        # 检测到违规，执行软钳位
    SOFT_LIMIT = 3     # 持续违规，执行硬钳位
    HARD_STOP = 4      # 严重违规，特定设备停机
    BLACKOUT = 5       # 熔断：全输出归零


# ── 设备约束剖面 ────────────────────────────────────────────────────────


@dataclass
class DeviceConstraint:
    """单个设备的 DMX 约束剖面。

    所有 value 数组形状均为 (num_channels,)。
    min/max 不用 scalar 而用数组，以支持同一设备内不同通道有不同的安全范围。
    """
    name: str
    universe: int
    start_channel: int
    num_channels: int
    min: np.ndarray           # 每通道最小值
    max: np.ndarray           # 每通道最大值
    max_change_rate: np.ndarray  # 每帧最大变化量
    safe_default: np.ndarray  # 停机时的安全回退值

    def __post_init__(self):
        n = self.num_channels
        for attr in ("min", "max", "max_change_rate", "safe_default"):
            arr = getattr(self, attr)
            if arr.shape != (n,):
                raise ValueError(
                    f"{self.name}.{attr}: expected ({n},), got {arr.shape}"
                )


# ── 默认剖面工厂 ─────────────────────────────────────────────────────────


def make_device_constraint(
    name: str,
    universe: int,
    start_channel: int,
    num_channels: int,
    *,
    min_val: float = 0.0,
    max_val: float = 255.0,
    max_change: float = 255.0,
    safe_default_val: float = 0.0,
) -> DeviceConstraint:
    """创建每通道值均相同的 DeviceConstraint。"""
    return DeviceConstraint(
        name=name,
        universe=universe,
        start_channel=start_channel,
        num_channels=num_channels,
        min=np.full(num_channels, min_val, dtype=np.float32),
        max=np.full(num_channels, max_val, dtype=np.float32),
        max_change_rate=np.full(num_channels, max_change, dtype=np.float32),
        safe_default=np.full(num_channels, safe_default_val, dtype=np.float32),
    )


# ── 安全联锁器 ───────────────────────────────────────────────────────────


class SafetyInterlock:
    """硬件安全联锁器。

    用法：
        interlock = SafetyInterlock()
        interlock.arm()

        # 注册设备约束
        interlock.add_constraint(make_device_constraint(
            "servo-1", universe=1, start_channel=0, num_channels=6,
            min_val=0, max_val=200, max_change=50, safe_default_val=0,
        ))

        # 在输出管线中调用（插入 smoother 之后、_send_to_hw 之前）
        safe_data = interlock.process(universe, mixed_data)
    """

    def __init__(self):
        self._constraints: List[DeviceConstraint] = []
        # universe -> [(constraint_index, constraint)]，避免每帧全表扫描
        self._constraints_by_universe: Dict[int, List[Tuple[int, DeviceConstraint]]] = {}
        # 每个约束（按 list index）的状态
        self._states: Dict[int, InterlockState] = {}
        self._violation_count: Dict[int, int] = {}
        # 上一帧值，用于变化率检测
        self._last_values: Dict[int, np.ndarray] = {}

        self._global_state: InterlockState = InterlockState.DISARMED

        # 调参
        self._warning_threshold: int = 1       # 连续违规次数 ≥ 1 → Warning
        self._softlimit_threshold: int = 3     # ≥ 3 → SoftLimit
        self._hardstop_threshold: int = 6      # ≥ 6 → HardStop
        self._blackout_device_ratio: float = 0.5  # 超过一半约束处于 HardStop → Blackout

        self._last_log: float = 0.0
        _log.info("SafetyInterlock created (DISARMED)")

    # ── 公共配置 ──────────────────────────────────────────────────────────

    @property
    def state(self) -> InterlockState:
        return self._global_state

    @state.setter
    def state(self, val: InterlockState):
        self._global_state = val

    @property
    def is_armed(self) -> bool:
        return self._global_state >= InterlockState.ARMED

    def set_thresholds(
        self,
        *,
        warning: Optional[int] = None,
        softlimit: Optional[int] = None,
        hardstop: Optional[int] = None,
        blackout_ratio: Optional[float] = None,
    ):
        if warning is not None:
            self._warning_threshold = max(1, warning)
        if softlimit is not None:
            self._softlimit_threshold = max(1, softlimit)
        if hardstop is not None:
            self._hardstop_threshold = max(1, hardstop)
        if blackout_ratio is not None:
            self._blackout_device_ratio = max(0.1, min(1.0, blackout_ratio))

    # ── 状态控制 ─────────────────────────────────────────────────────────

    def arm(self):
        """启用联锁：所有已注册约束进入 ARMED 状态。"""
        self._global_state = InterlockState.ARMED
        for idx in range(len(self._constraints)):
            self._states[idx] = InterlockState.ARMED
        _log.info("SafetyInterlock ARMED (%d constraints)", len(self._constraints))

    def disarm(self):
        """停用联锁：所有数据直通，清空状态。"""
        self._global_state = InterlockState.DISARMED
        self._states.clear()
        self._violation_count.clear()
        self._last_values.clear()
        _log.info("SafetyInterlock DISARMED")

    def reset(self):
        """重置：回到 ARMED，清除违规计数。不解除 Blackout。"""
        if self._global_state == InterlockState.DISARMED:
            return
        self._global_state = InterlockState.ARMED
        self._states.clear()
        self._violation_count.clear()
        self._last_values.clear()
        for idx in range(len(self._constraints)):
            self._states[idx] = InterlockState.ARMED
        _log.info("SafetyInterlock RESET → ARMED")

    # ── 约束管理 ────────────────────────────────────────────────────────

    def add_constraint(self, constraint: DeviceConstraint) -> int:
        """注册一个设备约束。返回约束索引。"""
        idx = len(self._constraints)
        self._constraints.append(constraint)
        self._constraints_by_universe.setdefault(int(constraint.universe), []).append(
            (idx, constraint))
        if self._global_state >= InterlockState.ARMED:
            self._states[idx] = InterlockState.ARMED
        _log.debug("SafetyInterlock: added constraint '%s' @ universe=%d ch=%d..%d",
                     constraint.name, constraint.universe,
                     constraint.start_channel,
                     constraint.start_channel + constraint.num_channels - 1)
        return idx

    def remove_constraint(self, index: int) -> bool:
        """按索引移除约束。"""
        if 0 <= index < len(self._constraints):
            self._constraints.pop(index)
            self._states.pop(index, None)
            self._violation_count.pop(index, None)
            self._last_values.pop(index, None)
            # 重排后续索引
            for idx in range(index, len(self._constraints)):
                if idx + 1 in self._states:
                    self._states[idx] = self._states.pop(idx + 1)
                if idx + 1 in self._violation_count:
                    self._violation_count[idx] = self._violation_count.pop(idx + 1)
                if idx + 1 in self._last_values:
                    self._last_values[idx] = self._last_values.pop(idx + 1)
            # 重建 universe 索引（移除/重排低频，重建成本可忽略）
            self._constraints_by_universe.clear()
            for _idx, _c in enumerate(self._constraints):
                self._constraints_by_universe.setdefault(int(_c.universe), []).append(
                    (_idx, _c))
            return True
        return False

    def clear_constraints(self):
        """移除所有约束。"""
        self._constraints.clear()
        self._constraints_by_universe.clear()
        self._states.clear()
        self._violation_count.clear()
        self._last_values.clear()

    def constraint_count(self) -> int:
        return len(self._constraints)

    def get_constraint(self, index: int) -> Optional[DeviceConstraint]:
        if 0 <= index < len(self._constraints):
            return self._constraints[index]
        return None

    def get_device_state(self, index: int) -> InterlockState:
        return self._states.get(index, InterlockState.DISARMED)

    # ── 核心处理 ─────────────────────────────────────────────────────────

    def process(self, universe: int, data: np.ndarray) -> np.ndarray:
        """处理一帧 DMX 数据。

        根据当前全局状态和每设备违规情况：
        - DISARMED → 直通
        - BLACKOUT → 全零
        - ARMED → 监控但不修改
        - WARNING → 钳位到 min/max，记录警告
        - SOFT_LIMIT → 钳位到 min/max，可能降低输出
        - HARD_STOP → 违规设备输出 safe_default

        返回修改后的 uint8 数组，形状与 data 相同。
        """
        if self._global_state == InterlockState.DISARMED:
            return data
        if self._global_state == InterlockState.BLACKOUT:
            return np.zeros_like(data)

        if data.dtype != np.float32:
            result = data.astype(np.float32)
        else:
            result = data.copy()

        timestamp = 0.0  # lazy log
        any_hard_stop_this_frame = False

        for idx, c in self._constraints_by_universe.get(universe, ()):
            state = self._states.get(idx, InterlockState.DISARMED)
            if state == InterlockState.DISARMED:
                continue

            ch_end = c.start_channel + c.num_channels
            if ch_end > len(result):
                ch_end = len(result)
            if c.start_channel >= len(result):
                continue

            device_slice = result[c.start_channel:ch_end]
            length = ch_end - c.start_channel

            min_arr = c.min[:length]
            max_arr = c.max[:length]
            rate_arr = c.max_change_rate[:length]
            default_arr = c.safe_default[:length]

            # ── 检测违规 ──
            below_min = device_slice < min_arr
            above_max = device_slice > max_arr
            range_violation = np.any(below_min) or np.any(above_max)

            rate_violation = False
            if idx in self._last_values:
                prev = self._last_values[idx]
                if len(prev) >= length:
                    change = np.abs(device_slice - prev[:length])
                    rate_violation = np.any(change > rate_arr)

            has_violation = range_violation or rate_violation

            # ── 违规计数与状态转换 ──
            if has_violation:
                count = self._violation_count.get(idx, 0) + 1
                self._violation_count[idx] = count

                if state == InterlockState.ARMED:
                    if count >= self._hardstop_threshold:
                        state = InterlockState.HARD_STOP
                    elif count >= self._softlimit_threshold:
                        state = InterlockState.SOFT_LIMIT
                    elif count >= self._warning_threshold:
                        state = InterlockState.WARNING

                elif state == InterlockState.WARNING:
                    if count >= self._hardstop_threshold:
                        state = InterlockState.HARD_STOP
                    elif count >= self._softlimit_threshold:
                        state = InterlockState.SOFT_LIMIT

                elif state == InterlockState.SOFT_LIMIT:
                    if count >= self._hardstop_threshold + 3:
                        state = InterlockState.HARD_STOP

                # HARD_STOP 保持，不自动降级

            else:
                # 无违规 → 计数器衰减
                count = self._violation_count.get(idx, 0)
                if count > 0:
                    count -= 1
                    self._violation_count[idx] = count
                else:
                    # 完全恢复 → ARMED
                    if state in (InterlockState.WARNING, InterlockState.SOFT_LIMIT):
                        state = InterlockState.ARMED

            self._states[idx] = state

            # ── 根据状态执行动作 ──
            if state >= InterlockState.HARD_STOP:
                # 停机：写入 safe_default
                result[c.start_channel:ch_end] = default_arr
                any_hard_stop_this_frame = True

            elif state >= InterlockState.WARNING:
                # 钳位：将数据限制在安全范围
                clamped = np.clip(device_slice, min_arr, max_arr)
                result[c.start_channel:ch_end] = clamped

                if range_violation:
                    if timestamp == 0.0:
                        timestamp = time.monotonic()
                    if timestamp - self._last_log > 2.0:
                        viol_details = []
                        if np.any(below_min):
                            viol_details.append("below_min")
                        if np.any(above_max):
                            viol_details.append("above_max")
                        if rate_violation:
                            viol_details.append("rate")
                        _log.warning(
                            "SafetyInterlock: %s state=%s violations=%s "
                            "[universe=%d ch=%d..%d count=%d]",
                            c.name, state.name, "+".join(viol_details),
                            c.universe, c.start_channel, ch_end - 1, count,
                        )
                        self._last_log = timestamp

            # 存储当前值用于变化率检测
            self._last_values[idx] = result[c.start_channel:ch_end].copy()

        # ── 全局 Blackout 检查 ──
        if any_hard_stop_this_frame:
            total = len(self._constraints)
            if total > 0:
                hs_count = sum(
                    1 for s in self._states.values()
                    if s >= InterlockState.HARD_STOP
                )
                if hs_count / total >= self._blackout_device_ratio:
                    self._global_state = InterlockState.BLACKOUT
                    _log.critical(
                        "SafetyInterlock: BLACKOUT triggered (%d/%d devices in HARD_STOP)",
                        hs_count, total,
                    )
                    return np.zeros_like(data)

        return np.clip(np.round(result), 0, 255).astype(np.uint8)


# ── 全局单例 ─────────────────────────────────────────────────────────────

_instance: Optional[SafetyInterlock] = None


def get_interlock() -> SafetyInterlock:
    """获取 SafetyInterlock 全局单例。"""
    global _instance
    if _instance is None:
        _instance = SafetyInterlock()
    return _instance


def reset_interlock():
    """重置全局单例。"""
    global _instance
    if _instance is not None:
        _instance.reset()
