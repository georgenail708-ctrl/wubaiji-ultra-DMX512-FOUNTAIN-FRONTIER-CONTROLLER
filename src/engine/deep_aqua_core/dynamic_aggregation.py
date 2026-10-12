"""
dynamic_aggregation.py — DeepAqua 动态聚合编排核心引擎
======================================================
实现《全量设计规格书》第一章核心哲学与第二章双元动态耦合模型：

核心逻辑：
  - 设备元（Device Atom）的动态生命周期管理
  - 动作元（Action Atom）的形态阈值触发机制
  - "编珠子"式资源调度（支持 2x2/4x4 动态重构）
  - 声部间的设备竞争与逻辑互补

设计原则：
  - 设备元不是预设的静态组，而是根据动作元的"形态需求"实时聚合
  - 同一个设备在不同时刻可属于不同编组
  - AI 像"编珠子"一样实时锁定物理设备并将其串成设备元
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  聚合拓扑模式
# ═══════════════════════════════════════════════════════════════════════════

class AggregationTopology(Enum):
    """
    设备元的聚合拓扑模式。
    描述同一批设备如何被"编珠子"成不同的编组结构。
    """
    CROSS_2X2   = "cross_2x2"   # 两组 2x2 交叉（4台设备 → 2组）
    CROSS_4X4   = "cross_4x4"   # 一组 4x4 交叉（16台设备 → 1组）
    LINEAR      = "linear"      # 线性串联（N台设备 → 1组）
    RING        = "ring"        # 环形聚合（N台设备 → 1组环形）
    SPLIT_HALF  = "split_half"  # 左右分半（N台设备 → 2组）
    QUAD        = "quad"        # 四象限（N台设备 → 4组）
    SCATTER     = "scatter"     # 散点（每台设备独立 → N组）


# ═══════════════════════════════════════════════════════════════════════════
#  设备元（Device Atom）动态生命周期
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceAtomState:
    """
    设备元的运行时状态。
    一个设备元是一组被动态聚合在一起的物理设备索引。
    """
    atom_id: str
    fixture_indices: List[int]          # 物理设备索引
    topology: AggregationTopology       # 聚合拓扑
    locked_by_voice: Optional[int]      # 被哪个声部锁定（None = 自由）
    created_at: float                   # 创建时间戳
    ttl: float                          # 生存时间（秒），-1 = 永久
    # 空间中心点（用于拓扑分析）
    centroid_x: float = 0.5
    centroid_y: float = 0.5

    @property
    def is_locked(self) -> bool:
        return self.locked_by_voice is not None

    @property
    def size(self) -> int:
        return len(self.fixture_indices)

    def is_expired(self, current_time: float) -> bool:
        if self.ttl < 0:
            return False
        return (current_time - self.created_at) > self.ttl


# ═══════════════════════════════════════════════════════════════════════════
#  动作元（Action Atom）形态阈值门控
# ═══════════════════════════════════════════════════════════════════════════

class ActionAtomType(Enum):
    """动作元类型，每种类型有不同的设备数量门槛"""
    WAVE_PEAK       = "wave_peak"       # 波峰：需要 ≥4 个设备元
    WAVE_VALLEY     = "wave_valley"     # 波谷：需要 ≥2 个设备元
    CROSS_SYNC      = "cross_sync"      # 同步交叉：需要 ≥2 个设备元（偶数）
    CROSS_ASYNC     = "cross_async"     # 异步交叉：需要 ≥4 个设备元
    SPIRAL_CW       = "spiral_cw"       # 顺时针螺旋：需要 ≥4 个设备元
    SPIRAL_CCW      = "spiral_ccw"      # 逆时针螺旋：需要 ≥4 个设备元
    BURST_CENTER    = "burst_center"    # 中心爆发：需要 ≥1 个设备元
    BURST_SCATTER   = "burst_scatter"   # 散射爆发：需要 ≥3 个设备元
    RIPPLE_OUT      = "ripple_out"      # 向外涟漪：需要 ≥3 个设备元
    RIPPLE_IN       = "ripple_in"       # 向内涟漪：需要 ≥3 个设备元
    IDLE            = "idle"            # 待机：无需求


# 形态阈值表：每种动作元需要的最少设备元数量
ACTION_THRESHOLD_TABLE: Dict[ActionAtomType, int] = {
    ActionAtomType.WAVE_PEAK:    4,
    ActionAtomType.WAVE_VALLEY:  2,
    ActionAtomType.CROSS_SYNC:   2,
    ActionAtomType.CROSS_ASYNC:  4,
    ActionAtomType.SPIRAL_CW:    4,
    ActionAtomType.SPIRAL_CCW:   4,
    ActionAtomType.BURST_CENTER: 1,
    ActionAtomType.BURST_SCATTER:3,
    ActionAtomType.RIPPLE_OUT:   3,
    ActionAtomType.RIPPLE_IN:    3,
    ActionAtomType.IDLE:         0,
}

# 动作元降级表：当设备数量不足时，降级到更简单的动作
ACTION_FALLBACK_TABLE: Dict[ActionAtomType, ActionAtomType] = {
    ActionAtomType.WAVE_PEAK:    ActionAtomType.WAVE_VALLEY,
    ActionAtomType.WAVE_VALLEY:  ActionAtomType.BURST_CENTER,
    ActionAtomType.CROSS_ASYNC:  ActionAtomType.CROSS_SYNC,
    ActionAtomType.CROSS_SYNC:   ActionAtomType.BURST_CENTER,
    ActionAtomType.SPIRAL_CW:    ActionAtomType.RIPPLE_OUT,
    ActionAtomType.SPIRAL_CCW:   ActionAtomType.RIPPLE_IN,
    ActionAtomType.BURST_SCATTER:ActionAtomType.BURST_CENTER,
    ActionAtomType.RIPPLE_OUT:   ActionAtomType.BURST_CENTER,
    ActionAtomType.RIPPLE_IN:    ActionAtomType.BURST_CENTER,
    ActionAtomType.BURST_CENTER: ActionAtomType.IDLE,
    ActionAtomType.IDLE:         ActionAtomType.IDLE,
}


@dataclass
class ActionAtomState:
    """动作元运行时状态"""
    voice_id: int
    action_type: ActionAtomType
    # 绑定的设备元列表
    bound_device_atoms: List[DeviceAtomState] = field(default_factory=list)
    # 动作参数（由 VariationEngine 实时调制）
    speed: float = 1.0
    phase: float = 0.0
    density: float = 1.0
    amplitude: float = 0.8
    # 降级历史（避免频繁降级）
    fallback_count: int = 0
    # 激活状态
    is_active: bool = False

    @property
    def required_device_count(self) -> int:
        return ACTION_THRESHOLD_TABLE.get(self.action_type, 1)

    @property
    def can_activate(self) -> bool:
        """形态阈值触发：是否满足最低设备元数量门槛"""
        return len(self.bound_device_atoms) >= self.required_device_count

    def try_activate(self) -> bool:
        """尝试激活，若不满足阈值则返回 False"""
        if self.can_activate:
            self.is_active = True
            return True
        self.is_active = False
        return False

    def get_all_fixture_indices(self) -> List[int]:
        """获取所有绑定设备元的物理设备索引"""
        indices = []
        for da in self.bound_device_atoms:
            indices.extend(da.fixture_indices)
        return indices


# ═══════════════════════════════════════════════════════════════════════════
#  动态聚合调度器 — "编珠子"式资源管理
# ═══════════════════════════════════════════════════════════════════════════

class DynamicAggregationScheduler:
    """
    动态聚合调度器：实现"编珠子"式的设备元生命周期管理。

    核心职责：
    1. 根据动作元的形态需求，实时聚合物理设备为设备元
    2. 管理设备元的锁定/解锁（资源竞争）
    3. 支持 2x2/4x4 等不同拓扑的动态重构
    4. 处理声部间的设备竞争与逻辑互补
    """

    def __init__(
        self,
        fixture_positions: List[Tuple[float, float]],  # (x, y) 坐标列表
        rng: Optional[random.Random] = None,
    ):
        self._positions = fixture_positions
        self._n_fixtures = len(fixture_positions)
        self._rng = rng or random.Random()

        # 当前所有活跃的设备元
        self._device_atoms: Dict[str, DeviceAtomState] = {}

        # 当前所有活跃的动作元（按声部 ID 索引）
        self._action_atoms: Dict[int, ActionAtomState] = {}

        # 设备占用表：fixture_index → voice_id（-1 = 自由）
        self._fixture_occupation: Dict[int, int] = {
            i: -1 for i in range(self._n_fixtures)
        }

        # 空间分析缓存
        self._spatial_clusters: Optional[List[List[int]]] = None
        self._build_spatial_clusters()

        log.info(
            "DynamicAggregationScheduler 初始化: %d 个设备",
            self._n_fixtures,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  空间聚类预分析
    # ─────────────────────────────────────────────────────────────────────

    def _build_spatial_clusters(self) -> None:
        """
        对设备进行空间聚类预分析。
        用于后续"编珠子"时的智能分组。
        """
        if self._n_fixtures == 0:
            self._spatial_clusters = []
            return

        positions = np.array(self._positions)
        n = len(positions)

        # 按 X 坐标排序（用于线性/交叉拓扑）
        sorted_by_x = sorted(range(n), key=lambda i: positions[i][0])
        # 按 Y 坐标排序（用于前后拓扑）
        sorted_by_y = sorted(range(n), key=lambda i: positions[i][1])
        # 按极角排序（用于环形/螺旋拓扑）
        cx = float(np.mean(positions[:, 0]))
        cy = float(np.mean(positions[:, 1]))
        sorted_by_angle = sorted(
            range(n),
            key=lambda i: math.atan2(positions[i][1] - cy, positions[i][0] - cx)
        )
        # 按到中心距离排序（用于涟漪拓扑）
        sorted_by_radius = sorted(
            range(n),
            key=lambda i: math.hypot(positions[i][0] - cx, positions[i][1] - cy)
        )

        self._sorted_by_x = sorted_by_x
        self._sorted_by_y = sorted_by_y
        self._sorted_by_angle = sorted_by_angle
        self._sorted_by_radius = sorted_by_radius
        self._center_x = cx
        self._center_y = cy

        # 构建四象限分组
        self._quadrants: List[List[int]] = [[], [], [], []]
        for i in range(n):
            x, y = positions[i]
            q = (0 if x >= cx else 1) + (0 if y >= cy else 2)
            self._quadrants[q].append(i)

        self._spatial_clusters = [sorted_by_x, sorted_by_y, sorted_by_angle]
        log.debug("空间聚类完成，中心=(%.2f, %.2f)", cx, cy)

    # ─────────────────────────────────────────────────────────────────────
    #  核心接口：动态聚合（"编珠子"）
    # ─────────────────────────────────────────────────────────────────────

    def aggregate_for_action(
        self,
        voice_id: int,
        action_type: ActionAtomType,
        topology: AggregationTopology,
        density: float,
        current_time: float,
        priority: int = 0,
    ) -> ActionAtomState:
        """
        为指定声部的动作元动态聚合设备元。

        这是"编珠子"逻辑的核心：
        1. 根据拓扑模式确定需要多少组、每组多少设备
        2. 从自由设备中按空间逻辑选取设备
        3. 将设备锁定并串成设备元
        4. 检查是否满足动作元的形态阈值
        5. 若不满足，降级到更简单的动作元

        Args:
            voice_id: 声部 ID
            action_type: 目标动作元类型
            topology: 聚合拓扑模式
            density: 设备激活率（0.0~1.0）
            current_time: 当前时间戳
            priority: 声部优先级（高优先级可抢占低优先级设备）

        Returns:
            ActionAtomState: 最终激活的动作元状态（可能已降级）
        """
        # 先释放该声部之前持有的设备元
        self._release_voice_devices(voice_id)

        # 根据拓扑计算需要的设备元结构
        device_groups = self._plan_device_groups(topology, density)

        # 构建动作元状态
        action_state = ActionAtomState(
            voice_id=voice_id,
            action_type=action_type,
        )

        # 尝试锁定设备元
        for group_indices in device_groups:
            if not group_indices:
                continue
            # 检查设备是否可用（未被更高优先级声部占用）
            available = self._filter_available_devices(
                group_indices, voice_id, priority
            )
            if not available:
                continue

            # 创建设备元
            da = self._create_device_atom(
                available, topology, voice_id, current_time
            )
            self._device_atoms[da.atom_id] = da
            action_state.bound_device_atoms.append(da)

        # 形态阈值门控：检查是否满足激活条件
        final_action = self._apply_threshold_gate(action_state)

        # 注册到调度表
        self._action_atoms[voice_id] = final_action

        log.debug(
            "Voice %d: 聚合 %s → %s (设备元=%d, 激活=%s)",
            voice_id,
            action_type.value,
            final_action.action_type.value,
            len(final_action.bound_device_atoms),
            final_action.is_active,
        )
        return final_action

    def _plan_device_groups(
        self,
        topology: AggregationTopology,
        density: float,
    ) -> List[List[int]]:
        """
        根据拓扑模式规划设备分组。
        这是"两组 2x2 交叉"到"一组 4x4 交叉"动态重构的实现。
        """
        n = self._n_fixtures
        if n == 0:
            return []

        # 计算激活设备总数
        n_active = max(1, int(n * density))

        if topology == AggregationTopology.CROSS_2X2:
            # 两组 2x2 交叉：将设备按 X 排序后分成两半
            half = n_active // 2
            left = self._sorted_by_x[:half]
            right = self._sorted_by_x[half : half * 2]
            return [left, right]

        elif topology == AggregationTopology.CROSS_4X4:
            # 一组 4x4 交叉：全部设备作为一组
            return [self._sorted_by_x[:n_active]]

        elif topology == AggregationTopology.LINEAR:
            # 线性串联：按 X 坐标顺序选取
            return [self._sorted_by_x[:n_active]]

        elif topology == AggregationTopology.RING:
            # 环形聚合：按极角顺序选取
            return [self._sorted_by_angle[:n_active]]

        elif topology == AggregationTopology.SPLIT_HALF:
            # 左右分半：按 X 坐标分成两组
            half = n_active // 2
            left = self._sorted_by_x[:half]
            right = self._sorted_by_x[half : half * 2]
            return [left, right]

        elif topology == AggregationTopology.QUAD:
            # 四象限：每个象限独立成组
            groups = []
            for q in self._quadrants:
                q_active = [i for i in q if i < n]
                n_q = max(1, int(len(q_active) * density))
                groups.append(q_active[:n_q])
            return [g for g in groups if g]

        elif topology == AggregationTopology.SCATTER:
            # 散点：每台设备独立成组
            active = self._sorted_by_x[:n_active]
            return [[i] for i in active]

        else:
            return [self._sorted_by_x[:n_active]]

    def _filter_available_devices(
        self,
        candidate_indices: List[int],
        requesting_voice: int,
        priority: int,
    ) -> List[int]:
        """
        过滤出可用的设备（未被占用或可被抢占）。
        高优先级声部可以抢占低优先级声部的设备。
        """
        available = []
        for idx in candidate_indices:
            owner = self._fixture_occupation.get(idx, -1)
            if owner == -1 or owner == requesting_voice:
                available.append(idx)
            # 抢占逻辑：高优先级可抢占（此处简化为不抢占，保持稳定性）
            # 实际可扩展为：if priority > owner_priority: available.append(idx)
        return available

    def _create_device_atom(
        self,
        fixture_indices: List[int],
        topology: AggregationTopology,
        voice_id: int,
        current_time: float,
    ) -> DeviceAtomState:
        """创建并锁定一个设备元"""
        import uuid as _uuid
        atom_id = str(_uuid.uuid4())[:8]

        # 计算空间中心点
        if fixture_indices and self._positions:
            xs = [self._positions[i][0] for i in fixture_indices if i < len(self._positions)]
            ys = [self._positions[i][1] for i in fixture_indices if i < len(self._positions)]
            cx = float(np.mean(xs)) if xs else 0.5
            cy = float(np.mean(ys)) if ys else 0.5
        else:
            cx, cy = 0.5, 0.5

        da = DeviceAtomState(
            atom_id=atom_id,
            fixture_indices=list(fixture_indices),
            topology=topology,
            locked_by_voice=voice_id,
            created_at=current_time,
            ttl=-1,  # 永久，直到被显式释放
            centroid_x=cx,
            centroid_y=cy,
        )

        # 标记设备占用
        for idx in fixture_indices:
            self._fixture_occupation[idx] = voice_id

        return da

    def _apply_threshold_gate(
        self, action_state: ActionAtomState
    ) -> ActionAtomState:
        """
        形态阈值门控：检查动作元是否满足最低设备元数量门槛。
        若不满足，自动降级到更简单的动作元。
        """
        max_fallback = 3  # 最多降级 3 次
        current = action_state

        while not current.can_activate and current.action_type != ActionAtomType.IDLE:
            fallback_type = ACTION_FALLBACK_TABLE.get(
                current.action_type, ActionAtomType.IDLE
            )
            log.debug(
                "Voice %d: 动作元 %s 设备不足(%d<%d)，降级为 %s",
                current.voice_id,
                current.action_type.value,
                len(current.bound_device_atoms),
                current.required_device_count,
                fallback_type.value,
            )
            current.fallback_count += 1
            current.action_type = fallback_type

            if current.fallback_count >= max_fallback:
                current.action_type = ActionAtomType.IDLE
                break

        current.try_activate()
        return current

    # ─────────────────────────────────────────────────────────────────────
    #  资源管理：释放与重分配
    # ─────────────────────────────────────────────────────────────────────

    def _release_voice_devices(self, voice_id: int) -> None:
        """释放指定声部持有的所有设备元"""
        # 释放设备占用
        for idx in range(self._n_fixtures):
            if self._fixture_occupation.get(idx) == voice_id:
                self._fixture_occupation[idx] = -1

        # 移除设备元
        to_remove = [
            aid for aid, da in self._device_atoms.items()
            if da.locked_by_voice == voice_id
        ]
        for aid in to_remove:
            del self._device_atoms[aid]

    def release_all(self) -> None:
        """释放所有设备元（重置状态）"""
        self._device_atoms.clear()
        self._action_atoms.clear()
        for idx in range(self._n_fixtures):
            self._fixture_occupation[idx] = -1

    def get_free_device_count(self) -> int:
        """获取当前自由设备数量"""
        return sum(
            1 for v in self._fixture_occupation.values() if v == -1
        )

    def get_occupation_map(self) -> Dict[int, int]:
        """获取设备占用图（fixture_index → voice_id）"""
        return dict(self._fixture_occupation)

    # ─────────────────────────────────────────────────────────────────────
    #  动态重构：在不同拓扑间切换
    # ─────────────────────────────────────────────────────────────────────

    def reconstruct_topology(
        self,
        voice_id: int,
        new_topology: AggregationTopology,
        density: float,
        current_time: float,
    ) -> Optional[ActionAtomState]:
        """
        在保持动作元类型不变的情况下，重构设备元的聚合拓扑。
        实现"两组 2x2 交叉 → 一组 4x4 交叉"的动态重构。
        """
        existing = self._action_atoms.get(voice_id)
        if existing is None:
            return None

        # 重新聚合
        return self.aggregate_for_action(
            voice_id=voice_id,
            action_type=existing.action_type,
            topology=new_topology,
            density=density,
            current_time=current_time,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  状态查询
    # ─────────────────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """获取调度器状态快照"""
        return {
            "n_fixtures": self._n_fixtures,
            "free_devices": self.get_free_device_count(),
            "active_device_atoms": len(self._device_atoms),
            "active_action_atoms": len(self._action_atoms),
            "occupation_map": self.get_occupation_map(),
            "action_atoms": {
                vid: {
                    "type": aa.action_type.value,
                    "active": aa.is_active,
                    "device_atoms": len(aa.bound_device_atoms),
                    "fixture_count": len(aa.get_all_fixture_indices()),
                }
                for vid, aa in self._action_atoms.items()
            },
        }
