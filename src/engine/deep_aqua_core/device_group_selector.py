"""
device_group_selector.py — DeepAqua 音频驱动的设备元选取偏好系统
================================================================
根据实时音频参数（Energy、Rhythm、Tension、BPM、Phrase Role 等），
从可用设备池中按照偏好策略选取最合适的设备元构型。

核心设计：
  - 8 种偏好预设（SelectionProfile）：每种预设对应一种音乐情绪/场景
  - 实时可行性评估：检查设备数是否满足目标构型的最低要求
  - 与 DeviceDensityDriver 深度集成：激活率 × 构型选取 = 完整设备元决策
  - 参数范围宽泛：所有阈值均可运行时覆盖，不硬编码

8 种偏好预设：
  1. INTENSE      — 激烈模式：大组优先，高密度，矩阵/爆发类
  2. GENTLE       — 柔和模式：小组精细，低密度，呼吸/渐变类
  3. RHYTHMIC     — 节奏模式：偶数分组，追逐/脉冲类，跟随 BPM
  4. BUILDUP      — 渐进模式：从小到大渐进扩张，波浪/龙摆类
  5. CLIMAX       — 高潮模式：全量设备，最大规模，螺旋/爆发类
  6. BREAKDOWN    — 消解模式：从大到小逐步收缩，呼吸/渐变类
  7. MELODIC      — 旋律模式：对称分组，喷泉/位置类，跟随旋律
  8. TENSION      — 张力模式：交叉/对位，奇偶分组，交错类
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .device_atom_geometry import DeviceAtomGeometry, GeometryResult
from .effect_atom_registry import EffectAtomRegistry, EffectActionClass

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  偏好预设枚举
# ═══════════════════════════════════════════════════════════════════════════

class SelectionProfile(Enum):
    """8 种音频驱动的设备元选取偏好预设"""
    INTENSE    = "intense"     # 激烈：大组优先
    GENTLE     = "gentle"      # 柔和：小组精细
    RHYTHMIC   = "rhythmic"    # 节奏：偶数分组跟 BPM
    BUILDUP    = "buildup"     # 渐进：从小到大扩张
    CLIMAX     = "climax"      # 高潮：全量最大规模
    BREAKDOWN  = "breakdown"   # 消解：从大到小收缩
    MELODIC    = "melodic"     # 旋律：对称跟旋律
    TENSION    = "tension"     # 张力：交叉对位


# ═══════════════════════════════════════════════════════════════════════════
#  选取决策结果
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SelectionDecision:
    """
    设备元选取决策结果。
    包含选取的设备分组、构型信息和可行性评估。
    """
    # 激活的设备 ID 列表（从可用设备中选出）
    active_devices: List[int] = field(default_factory=list)
    # 空间构型结果
    geometry: Optional[GeometryResult] = None
    # 使用的偏好预设
    profile: SelectionProfile = SelectionProfile.INTENSE
    # 推荐的 preset_id 列表（按适合度排序）
    recommended_presets: List[Tuple[str, float]] = field(default_factory=list)
    # 可行性评估
    feasibility_score: float = 1.0
    # 激活率（active_devices / total_available）
    activation_rate: float = 1.0
    # 构型算子名称
    geometry_op: str = "linear"
    # 构型参数
    geometry_params: Dict[str, Any] = field(default_factory=dict)
    # 决策说明（调试用）
    reason: str = ""
    # 是否降级（目标设备数不足，已降级处理）
    is_degraded: bool = False


# ═══════════════════════════════════════════════════════════════════════════
#  偏好预设配置
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ProfileConfig:
    """单个偏好预设的配置参数"""
    name: str
    # 目标激活率范围 [min, max]（相对于可用设备总数）
    activation_rate_range: Tuple[float, float] = (0.5, 1.0)
    # 优先的动作大类（按优先级排序）
    preferred_classes: List[str] = field(default_factory=list)
    # 优先的构型算子
    preferred_geometry: str = "linear"
    # 构型算子参数
    geometry_params: Dict[str, Any] = field(default_factory=dict)
    # 分组策略：large/small/even/odd/all/progressive
    group_strategy: str = "all"
    # 最小分组大小
    min_group_size: int = 1
    # 最大分组数
    max_groups: int = 16
    # 是否优先选取空间连续的设备
    prefer_contiguous: bool = True
    # 是否需要偶数设备
    prefer_even: bool = False
    # 音频触发阈值（超过此值自动切换到该预设）
    energy_threshold: float = 0.0
    rhythm_threshold: float = 0.0
    tension_threshold: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  8 种预设配置定义
# ═══════════════════════════════════════════════════════════════════════════

PROFILE_CONFIGS: Dict[SelectionProfile, ProfileConfig] = {

    SelectionProfile.INTENSE: ProfileConfig(
        name="激烈模式",
        # 激烈时尽量激活更多设备，激活率高
        activation_rate_range=(0.7, 1.0),
        preferred_classes=["pulse", "burst", "matrix", "cross", "audio_sync"],
        preferred_geometry="grid",
        geometry_params={"group_by": "all"},
        group_strategy="large",
        min_group_size=4,
        max_groups=8,
        prefer_contiguous=True,
        prefer_even=True,
        energy_threshold=0.7,
        rhythm_threshold=0.6,
    ),

    SelectionProfile.GENTLE: ProfileConfig(
        name="柔和模式",
        # 柔和时激活较少设备，精细控制
        activation_rate_range=(0.2, 0.6),
        preferred_classes=["breath", "fader", "fountain", "color"],
        preferred_geometry="linear_uniform",
        geometry_params={},
        group_strategy="small",
        min_group_size=1,
        max_groups=4,
        prefer_contiguous=True,
        prefer_even=False,
        energy_threshold=0.0,
        rhythm_threshold=0.0,
    ),

    SelectionProfile.RHYTHMIC: ProfileConfig(
        name="节奏模式",
        # 节奏模式：偶数分组，跟随 BPM 节拍
        activation_rate_range=(0.4, 0.9),
        preferred_classes=["chase", "pulse", "audio_sync", "dragon"],
        preferred_geometry="split_equal",
        geometry_params={"n_groups": 2},
        group_strategy="even",
        min_group_size=2,
        max_groups=8,
        prefer_contiguous=False,
        prefer_even=True,
        rhythm_threshold=0.6,
    ),

    SelectionProfile.BUILDUP: ProfileConfig(
        name="渐进模式",
        # Build-up：从小组开始，随能量增长扩张
        activation_rate_range=(0.2, 1.0),  # 动态范围最宽
        preferred_classes=["wave", "dragon", "chase", "ripple"],
        preferred_geometry="linear_uniform",
        geometry_params={},
        group_strategy="progressive",
        min_group_size=1,
        max_groups=16,
        prefer_contiguous=True,
        prefer_even=False,
    ),

    SelectionProfile.CLIMAX: ProfileConfig(
        name="高潮模式",
        # 高潮：全量设备，最大规模，最强表现力
        activation_rate_range=(0.85, 1.0),
        preferred_classes=["spiral", "burst", "matrix", "audio_sync", "cross"],
        preferred_geometry="grid",
        geometry_params={"group_by": "all"},
        group_strategy="all",
        min_group_size=8,
        max_groups=1,
        prefer_contiguous=True,
        prefer_even=True,
        energy_threshold=0.8,
        tension_threshold=0.7,
    ),

    SelectionProfile.BREAKDOWN: ProfileConfig(
        name="消解模式",
        # Breakdown：从大到小逐步收缩
        activation_rate_range=(0.1, 0.7),  # 动态范围宽
        preferred_classes=["breath", "fader", "wave", "fountain"],
        preferred_geometry="radial",
        geometry_params={"n_layers": 2},
        group_strategy="small",
        min_group_size=1,
        max_groups=4,
        prefer_contiguous=True,
        prefer_even=False,
    ),

    SelectionProfile.MELODIC: ProfileConfig(
        name="旋律模式",
        # 旋律：对称分组，跟随旋律线
        activation_rate_range=(0.3, 0.8),
        preferred_classes=["fountain", "position", "wave", "color"],
        preferred_geometry="symmetric",
        geometry_params={"n_groups": 2},
        group_strategy="even",
        min_group_size=2,
        max_groups=4,
        prefer_contiguous=True,
        prefer_even=True,
    ),

    SelectionProfile.TENSION: ProfileConfig(
        name="张力模式",
        # 张力：交叉/对位，奇偶交错
        activation_rate_range=(0.5, 1.0),
        preferred_classes=["cross", "wave", "chase", "spiral"],
        preferred_geometry="split_interleave",
        geometry_params={"n_groups": 2},
        group_strategy="odd",
        min_group_size=2,
        max_groups=4,
        prefer_contiguous=False,
        prefer_even=True,
        tension_threshold=0.5,
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
#  DeviceGroupSelector 主类
# ═══════════════════════════════════════════════════════════════════════════

class DeviceGroupSelector:
    """
    音频驱动的设备元选取偏好系统。

    核心功能：
    1. 根据音频参数自动推断最合适的 SelectionProfile
    2. 按照 Profile 的策略从可用设备中选取设备子集
    3. 应用空间构型算子生成 GeometryResult
    4. 查询 EffectAtomRegistry 推荐最合适的 preset
    5. 实时可行性评估与降级处理

    与 DeviceDensityDriver 集成：
      - DeviceDensityDriver 决定"激活多少设备"（激活率）
      - DeviceGroupSelector 决定"激活哪些设备"（选取策略）和"如何排列"（构型）

    使用方式：
        selector = DeviceGroupSelector(registry, geometry)
        decision = selector.select(
            available_devices=[0,1,2,...,15],
            music_ctx=ctx,
            density_activation_rate=0.75,
            profile=SelectionProfile.INTENSE,
        )
    """

    def __init__(
        self,
        registry: Optional[EffectAtomRegistry] = None,
        geometry: Optional[DeviceAtomGeometry] = None,
    ):
        self._registry = registry
        self._geometry = geometry or DeviceAtomGeometry()
        # 运行时可覆盖的配置
        self._profile_overrides: Dict[SelectionProfile, Dict[str, Any]] = {}
        # 当前激活的 profile（可被外部设置）
        self._current_profile: Optional[SelectionProfile] = None
        # 历史记录（用于 buildup/breakdown 的渐进逻辑）
        self._history_activation_rate: float = 0.5
        self._history_group_count: int = 2

    # ─────────────────────────────────────────────────────────────────────
    #  主入口
    # ─────────────────────────────────────────────────────────────────────

    def select(
        self,
        available_devices: List[int],
        energy: float = 0.5,
        rhythm: float = 0.5,
        tension: float = 0.5,
        melody: float = 0.5,
        bpm: float = 120.0,
        phrase_role: str = "neutral",
        is_climax: bool = False,
        in_buildup: bool = False,
        in_breakdown: bool = False,
        density_activation_rate: float = 0.75,
        profile: Optional[SelectionProfile] = None,
        target_preset_id: Optional[str] = None,
        positions: Optional[List[Tuple[float, float]]] = None,
        complexity: float = 1.0,
    ) -> SelectionDecision:
        """
        核心选取方法。

        Args:
            available_devices: 当前可用设备 ID 列表（未被其他声部占用）
            energy: 音频能量 [0, ∞)，无上限限制
            rhythm: 节奏强度 [0, 1]
            tension: 张力 [0, 1]
            melody: 旋律强度 [0, 1]
            bpm: 当前 BPM（无限制）
            phrase_role: 乐句角色
            is_climax: 是否高潮段落
            in_buildup: 是否 build-up 段落
            in_breakdown: 是否 breakdown 段落
            density_activation_rate: DeviceDensityDriver 给出的激活率 [0, 1]
            profile: 强制指定偏好预设（None 则自动推断）
            target_preset_id: 目标 preset（用于可行性检查）
            positions: 设备物理坐标
            complexity: 复杂度系数（影响分组数，无上限）

        Returns:
            SelectionDecision
        """
        if not available_devices:
            return SelectionDecision(reason="无可用设备")

        # 1. 推断偏好预设
        if profile is None:
            profile = self._infer_profile(
                energy, rhythm, tension, melody,
                is_climax, in_buildup, in_breakdown,
            )
        self._current_profile = profile

        # 2. 获取预设配置（支持运行时覆盖）
        config = self._get_config(profile)

        # 3. 计算目标激活设备数
        n_available = len(available_devices)
        target_rate = self._compute_activation_rate(
            config, density_activation_rate, energy, in_buildup, in_breakdown
        )
        n_target = max(1, round(n_available * target_rate))

        # 4. 按策略选取设备子集
        selected_devices = self._select_devices(
            available_devices, n_target, config, positions
        )

        # 5. 可行性检查（针对目标 preset）
        is_degraded = False
        if target_preset_id and self._registry:
            feasibility = self._registry.check_feasibility(
                target_preset_id, len(selected_devices)
            )
            if not feasibility["feasible"]:
                # 尝试扩充设备数
                min_req = feasibility["min_required"]
                if min_req <= n_available:
                    selected_devices = self._select_devices(
                        available_devices, min_req, config, positions
                    )
                    is_degraded = True
                else:
                    # 设备完全不足，使用全部可用设备
                    selected_devices = list(available_devices)
                    is_degraded = True

        # 6. 计算分组数（受 complexity 影响，无上限）
        n_groups = self._compute_group_count(
            config, len(selected_devices), complexity, energy, rhythm
        )

        # 7. 应用空间构型算子
        geo_op, geo_params = self._compute_geometry(
            config, n_groups, len(selected_devices), energy, rhythm, bpm
        )
        geometry_result = self._geometry.apply(
            geo_op, selected_devices, positions, **geo_params
        )

        # 8. 查询推荐 preset
        recommended_presets = []
        if self._registry:
            recommended_presets = self._registry.query_by_audio(
                energy=energy,
                rhythm=rhythm,
                tension=tension,
                melody=melody,
                n_devices=len(selected_devices),
                is_climax=is_climax,
                in_buildup=in_buildup,
                phrase_role=phrase_role,
                top_k=5,
            )

        # 9. 更新历史
        self._history_activation_rate = target_rate
        self._history_group_count = n_groups

        actual_rate = len(selected_devices) / max(n_available, 1)
        return SelectionDecision(
            active_devices=selected_devices,
            geometry=geometry_result,
            profile=profile,
            recommended_presets=recommended_presets,
            feasibility_score=min(1.0, len(selected_devices) / max(n_target, 1)),
            activation_rate=actual_rate,
            geometry_op=geo_op,
            geometry_params=geo_params,
            reason=f"[{config.name}] {n_groups}组 × {len(selected_devices)//max(n_groups,1)}台, 激活率={actual_rate:.2f}",
            is_degraded=is_degraded,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  自动推断 Profile
    # ─────────────────────────────────────────────────────────────────────

    def _infer_profile(
        self,
        energy: float,
        rhythm: float,
        tension: float,
        melody: float,
        is_climax: bool,
        in_buildup: bool,
        in_breakdown: bool,
    ) -> SelectionProfile:
        """
        根据音频特征自动推断最合适的偏好预设。
        使用加权评分，不硬编码单一阈值。
        """
        scores: Dict[SelectionProfile, float] = {}

        # CLIMAX：高能量 + 高张力 + 高潮标志
        scores[SelectionProfile.CLIMAX] = (
            energy * 0.4 + tension * 0.3 + (0.5 if is_climax else 0.0) * 0.3
        )

        # INTENSE：高能量 + 高节奏
        scores[SelectionProfile.INTENSE] = (
            energy * 0.5 + rhythm * 0.4 + (1.0 - melody) * 0.1
        )

        # RHYTHMIC：高节奏 + 中等能量
        scores[SelectionProfile.RHYTHMIC] = (
            rhythm * 0.6 + energy * 0.2 + (1.0 - tension) * 0.2
        )

        # TENSION：高张力 + 中等能量
        scores[SelectionProfile.TENSION] = (
            tension * 0.5 + energy * 0.3 + rhythm * 0.2
        )

        # BUILDUP：build-up 标志 + 中等能量增长
        scores[SelectionProfile.BUILDUP] = (
            (0.6 if in_buildup else 0.1) + energy * 0.2 + rhythm * 0.2
        )

        # BREAKDOWN：breakdown 标志 + 低能量
        scores[SelectionProfile.BREAKDOWN] = (
            (0.6 if in_breakdown else 0.1) + (1.0 - energy) * 0.3 + melody * 0.1
        )

        # MELODIC：高旋律 + 低节奏
        scores[SelectionProfile.MELODIC] = (
            melody * 0.6 + (1.0 - rhythm) * 0.2 + (1.0 - tension) * 0.2
        )

        # GENTLE：低能量 + 低节奏 + 高旋律
        scores[SelectionProfile.GENTLE] = (
            (1.0 - energy) * 0.4 + (1.0 - rhythm) * 0.3 + melody * 0.3
        )

        # 返回得分最高的预设
        return max(scores, key=lambda p: scores[p])

    # ─────────────────────────────────────────────────────────────────────
    #  激活率计算
    # ─────────────────────────────────────────────────────────────────────

    def _compute_activation_rate(
        self,
        config: ProfileConfig,
        density_rate: float,
        energy: float,
        in_buildup: bool,
        in_breakdown: bool,
    ) -> float:
        """
        计算最终激活率。
        综合考虑：DeviceDensityDriver 给出的基础激活率 + 偏好预设范围 + 音频特征。
        不硬编码上下限，允许极端值。
        """
        lo, hi = config.activation_rate_range

        # 渐进模式：随能量动态插值
        if in_buildup:
            # build-up：激活率随能量从 lo 增长到 hi
            target = lo + (hi - lo) * energy
        elif in_breakdown:
            # breakdown：激活率随能量从 hi 降低到 lo
            target = hi - (hi - lo) * (1.0 - energy)
        else:
            # 普通模式：在预设范围内，以 density_rate 为基准插值
            target = lo + (hi - lo) * density_rate

        # 与 density_rate 加权融合（不完全覆盖，保留 density 的影响）
        final = target * 0.6 + density_rate * 0.4

        # 允许超出 [0,1] 范围的极端情况（由调用方保证设备数合理）
        return max(0.0, min(1.0, final))

    # ─────────────────────────────────────────────────────────────────────
    #  设备选取
    # ─────────────────────────────────────────────────────────────────────

    def _select_devices(
        self,
        available: List[int],
        n_target: int,
        config: ProfileConfig,
        positions: Optional[List[Tuple[float, float]]],
    ) -> List[int]:
        """按策略从可用设备中选取 n_target 台"""
        n = len(available)
        n_target = max(1, min(n_target, n))

        strategy = config.group_strategy

        if strategy == "all" or n_target >= n:
            return list(available)

        elif strategy == "large":
            # 大组优先：选取空间上连续的最大块
            if config.prefer_contiguous:
                # 选取中间连续的一段（视觉上更集中）
                start = (n - n_target) // 2
                return available[start: start + n_target]
            else:
                return available[:n_target]

        elif strategy == "small":
            # 小组精细：均匀间隔选取少量设备
            if n_target <= 1:
                return [available[n // 2]]
            step = (n - 1) / (n_target - 1)
            return [available[round(i * step)] for i in range(n_target)]

        elif strategy == "even":
            # 偶数分组：确保选取偶数台
            if n_target % 2 != 0:
                n_target = max(2, n_target - 1)
            # 均匀间隔选取
            if n_target >= n:
                return list(available)
            step = (n - 1) / (n_target - 1)
            return [available[round(i * step)] for i in range(n_target)]

        elif strategy == "odd":
            # 奇偶交错：选取奇数位置的设备
            odd_devs = [d for i, d in enumerate(available) if i % 2 == 1]
            even_devs = [d for i, d in enumerate(available) if i % 2 == 0]
            # 混合选取
            combined = []
            for i in range(max(len(odd_devs), len(even_devs))):
                if i < len(odd_devs):
                    combined.append(odd_devs[i])
                if i < len(even_devs):
                    combined.append(even_devs[i])
            return combined[:n_target]

        elif strategy == "progressive":
            # 渐进策略：基于历史激活率，渐进扩张或收缩
            prev_n = round(n * self._history_activation_rate)
            # 每次最多扩张/收缩 20%
            max_delta = max(1, round(n * 0.2))
            if n_target > prev_n:
                actual_n = min(n_target, prev_n + max_delta)
            else:
                actual_n = max(n_target, prev_n - max_delta)
            actual_n = max(1, min(actual_n, n))
            return available[:actual_n]

        else:
            return available[:n_target]

    # ─────────────────────────────────────────────────────────────────────
    #  分组数计算
    # ─────────────────────────────────────────────────────────────────────

    def _compute_group_count(
        self,
        config: ProfileConfig,
        n_selected: int,
        complexity: float,
        energy: float,
        rhythm: float,
    ) -> int:
        """
        计算分组数。
        complexity 参数无上限，允许极大值（受设备数约束）。
        """
        # 基础分组数：随 complexity 线性增长
        base_groups = max(1, round(complexity))

        # 音频修正：高能量/高节奏时增加分组
        audio_factor = 1.0 + (energy - 0.5) * 0.4 + (rhythm - 0.5) * 0.3
        audio_factor = max(0.5, audio_factor)  # 不低于 0.5

        target_groups = max(1, round(base_groups * audio_factor))

        # 受设备数约束（每组至少 config.min_group_size 台）
        max_by_devices = max(1, n_selected // max(config.min_group_size, 1))

        # 受预设配置约束（max_groups 可运行时覆盖）
        max_by_config = config.max_groups

        return min(target_groups, max_by_devices, max_by_config)

    # ─────────────────────────────────────────────────────────────────────
    #  构型算子选择
    # ─────────────────────────────────────────────────────────────────────

    def _compute_geometry(
        self,
        config: ProfileConfig,
        n_groups: int,
        n_devices: int,
        energy: float,
        rhythm: float,
        bpm: float,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        根据预设配置和音频特征，决定最终的构型算子和参数。
        """
        base_op = config.preferred_geometry
        base_params = dict(config.geometry_params)

        # 动态调整构型参数
        if base_op == "split_equal":
            base_params["n_groups"] = n_groups

        elif base_op == "split_interleave":
            base_params["n_groups"] = n_groups

        elif base_op == "symmetric":
            base_params["n_groups"] = max(2, n_groups)

        elif base_op == "grid":
            cols = max(2, round(math.sqrt(n_devices)))
            base_params.setdefault("cols", cols)

        elif base_op == "radial":
            base_params["n_layers"] = max(2, n_groups)

        elif base_op == "ring":
            base_params["n_rings"] = max(1, n_groups // 2)

        elif base_op == "linear_uniform":
            base_params["n"] = n_devices

        # 高 BPM 时倾向于更多分组（节奏感更强）
        if bpm > 140 and base_op in ("split_equal", "split_interleave"):
            base_params["n_groups"] = min(
                base_params.get("n_groups", n_groups) + 1,
                config.max_groups,
            )

        return base_op, base_params

    # ─────────────────────────────────────────────────────────────────────
    #  配置管理
    # ─────────────────────────────────────────────────────────────────────

    def _get_config(self, profile: SelectionProfile) -> ProfileConfig:
        """获取预设配置（支持运行时覆盖）"""
        config = PROFILE_CONFIGS[profile]
        overrides = self._profile_overrides.get(profile, {})
        if overrides:
            import copy
            config = copy.copy(config)
            for k, v in overrides.items():
                if hasattr(config, k):
                    setattr(config, k, v)
        return config

    def override_profile(
        self,
        profile: SelectionProfile,
        **kwargs: Any,
    ) -> None:
        """
        运行时覆盖某个预设的配置参数。
        支持覆盖任意 ProfileConfig 字段，无限制。

        示例：
            selector.override_profile(
                SelectionProfile.INTENSE,
                activation_rate_range=(0.9, 1.0),
                min_group_size=8,
            )
        """
        self._profile_overrides[profile] = kwargs

    def reset_override(self, profile: Optional[SelectionProfile] = None) -> None:
        """重置覆盖配置"""
        if profile:
            self._profile_overrides.pop(profile, None)
        else:
            self._profile_overrides.clear()

    # ─────────────────────────────────────────────────────────────────────
    #  批量决策（多声部并行）
    # ─────────────────────────────────────────────────────────────────────

    def select_multi_voice(
        self,
        all_devices: List[int],
        voice_params: List[Dict[str, Any]],
        complexity: float = 1.0,
    ) -> List[SelectionDecision]:
        """
        多声部并行设备分配。
        按声部优先级依次分配设备，避免设备竞争。

        Args:
            all_devices: 全量设备列表
            voice_params: 每个声部的参数字典（包含 energy/rhythm 等）
            complexity: 全局复杂度（影响声部数，无上限）

        Returns:
            每个声部的 SelectionDecision 列表
        """
        n_voices = len(voice_params)
        if n_voices == 0:
            return []

        # 按能量降序排列声部（高能量声部优先获取设备）
        voice_order = sorted(
            range(n_voices),
            key=lambda i: voice_params[i].get("energy", 0.5),
            reverse=True,
        )

        remaining_devices = list(all_devices)
        decisions: List[Optional[SelectionDecision]] = [None] * n_voices

        for vi in voice_order:
            params = voice_params[vi]
            if not remaining_devices:
                decisions[vi] = SelectionDecision(reason="无剩余设备")
                continue

            decision = self.select(
                available_devices=remaining_devices,
                complexity=complexity,
                **{k: v for k, v in params.items() if k != "complexity"},
            )
            decisions[vi] = decision

            # 从剩余设备中移除已分配的设备
            used = set(decision.active_devices)
            remaining_devices = [d for d in remaining_devices if d not in used]

        return decisions

    # ─────────────────────────────────────────────────────────────────────
    #  与 DeviceDensityDriver 的集成接口
    # ─────────────────────────────────────────────────────────────────────

    def integrate_with_density_driver(
        self,
        density_driver: Any,
        available_devices: List[int],
        music_ctx: Any,
        complexity: float = 1.0,
    ) -> SelectionDecision:
        """
        与 DeviceDensityDriver 深度集成的统一入口。

        DeviceDensityDriver 负责：
          - 计算整体激活率（Mask/Step 模式）
          - 决定哪些设备"应该激活"

        DeviceGroupSelector 负责：
          - 在激活设备中进一步选取子集
          - 决定空间构型和分组方式
          - 推荐最合适的 preset

        Args:
            density_driver: DeviceDensityDriver 实例
            available_devices: 全量可用设备
            music_ctx: MusicContext 实例
            complexity: 复杂度（无上限）
        """
        # 从 DeviceDensityDriver 获取激活率和激活掩码
        try:
            activation_rate = getattr(density_driver, '_activation_rate', 0.75)
            # 尝试获取已激活的设备列表
            if hasattr(density_driver, 'get_active_devices'):
                pre_activated = density_driver.get_active_devices(available_devices)
            else:
                n_active = max(1, round(len(available_devices) * activation_rate))
                pre_activated = available_devices[:n_active]
        except Exception:
            log.debug("Failed to query density_driver activation, falling back to all available devices")
            pre_activated = available_devices
            activation_rate = 1.0

        # 提取音频参数
        energy  = getattr(music_ctx, 'energy',  0.5)
        rhythm  = getattr(music_ctx, 'rhythm',  0.5)
        tension = getattr(music_ctx, 'tension', 0.5)
        melody  = getattr(music_ctx, 'melody',  0.5)
        bpm     = getattr(music_ctx, 'bpm',     120.0)
        phrase_role = getattr(music_ctx, 'phrase_role', 'neutral')

        return self.select(
            available_devices=pre_activated,
            energy=energy,
            rhythm=rhythm,
            tension=tension,
            melody=melody,
            bpm=bpm,
            phrase_role=phrase_role,
            density_activation_rate=activation_rate,
            complexity=complexity,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  状态查询
    # ─────────────────────────────────────────────────────────────────────

    @property
    def current_profile(self) -> Optional[SelectionProfile]:
        return self._current_profile

    def get_profile_description(self, profile: SelectionProfile) -> str:
        config = PROFILE_CONFIGS[profile]
        return (
            f"{config.name}: "
            f"激活率={config.activation_rate_range}, "
            f"策略={config.group_strategy}, "
            f"构型={config.preferred_geometry}, "
            f"优先类={config.preferred_classes[:3]}"
        )

    def list_profiles(self) -> List[Dict[str, Any]]:
        """列出所有预设信息"""
        return [
            {
                "profile": p.value,
                "name": PROFILE_CONFIGS[p].name,
                "activation_range": PROFILE_CONFIGS[p].activation_rate_range,
                "geometry": PROFILE_CONFIGS[p].preferred_geometry,
                "strategy": PROFILE_CONFIGS[p].group_strategy,
                "preferred_classes": PROFILE_CONFIGS[p].preferred_classes,
            }
            for p in SelectionProfile
        ]
