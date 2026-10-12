"""
integration.py — DeepAqua 外部集成层（轻量数据结构与映射表）
=========================================================
本模块只保留跨模块共享的轻量数据结构与映射表：

  - MusicContextSnapshot：音乐上下文快照
  - VoiceRuntimeState：声部运行时状态
  - ACTION_TO_PRESET：动作元 → Effect preset 映射
  - ACTION_TO_TOPOLOGY：动作元 → 聚合拓扑映射

主编排器 DeepAquaOrchestrator 定义在 orchestrator.py（本模块不再重复定义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .dynamic_aggregation import (
    ActionAtomType,
    ActionAtomState,
    AggregationTopology,
)


# ═══════════════════════════════════════════════════════════════════════
#  Action Atom → Effect Preset 映射表
# ═══════════════════════════════════════════════════════════════════════

# 将 ActionAtomType 映射到 EffectEngine 中的 preset_id
ACTION_TO_PRESET: Dict[ActionAtomType, str] = {
    ActionAtomType.WAVE_PEAK:     "wave_sine",
    ActionAtomType.WAVE_VALLEY:   "wave_breathe",
    ActionAtomType.CROSS_SYNC:    "cross_single",
    ActionAtomType.CROSS_ASYNC:   "cross_wave",
    ActionAtomType.SPIRAL_CW:     "chase_spiral",
    ActionAtomType.SPIRAL_CCW:    "chase_spiral",
    ActionAtomType.BURST_CENTER:  "burst_all",
    ActionAtomType.BURST_SCATTER: "burst_stagger",
    ActionAtomType.RIPPLE_OUT:    "diffuse_out",
    ActionAtomType.RIPPLE_IN:     "diffuse_in",
    ActionAtomType.IDLE:          "hold_all",
}

# 将 ActionAtomType 映射到拓扑模式
ACTION_TO_TOPOLOGY: Dict[ActionAtomType, AggregationTopology] = {
    ActionAtomType.WAVE_PEAK:     AggregationTopology.RING,
    ActionAtomType.WAVE_VALLEY:   AggregationTopology.LINEAR,
    ActionAtomType.CROSS_SYNC:    AggregationTopology.CROSS_2X2,
    ActionAtomType.CROSS_ASYNC:   AggregationTopology.CROSS_4X4,
    ActionAtomType.SPIRAL_CW:     AggregationTopology.RING,
    ActionAtomType.SPIRAL_CCW:    AggregationTopology.RING,
    ActionAtomType.BURST_CENTER:  AggregationTopology.QUAD,
    ActionAtomType.BURST_SCATTER: AggregationTopology.SCATTER,
    ActionAtomType.RIPPLE_OUT:    AggregationTopology.RING,
    ActionAtomType.RIPPLE_IN:     AggregationTopology.RING,
    ActionAtomType.IDLE:          AggregationTopology.LINEAR,
}


# ═══════════════════════════════════════════════════════════════════════
#  音乐上下文快照（轻量级，不依赖 deep_music_analyzer）
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MusicContextSnapshot:
    """
    音乐上下文快照：供编排器使用的轻量级数据结构。
    可直接从 MusicContext 转换，也可手动构建（用于测试）。
    """
    t: float = 0.0
    bpm: float = 120.0
    beat_phase: float = 0.0

    # 能量与张力
    energy: float = 0.5       # 0~1，整体能量
    tension: float = 0.5      # 0~1，紧张度
    rhythm: float = 0.5       # 0~1，节奏强度
    melody: float = 0.5       # 0~1，旋律强度

    # 乐句角色
    phrase_role: str = "neutral"   # antecedent/consequent/climax/transition/neutral
    phrase_progress: float = 0.0   # 0~1，在当前乐句中的进度

    # Build-up
    in_buildup: bool = False
    buildup_progress: float = 0.0

    # 段落标签
    section_label: str = "unknown"

    # 是否高潮
    is_climax: bool = False

    # 是否节拍点
    is_beat: bool = False

    @classmethod
    def from_music_context(cls, ctx: Any) -> "MusicContextSnapshot":
        """从 MusicContext 对象转换"""
        phrase_role = "neutral"
        try:
            phrase_role = getattr(
                getattr(ctx, "phrase_role", None), "value", "neutral"
            )
        except Exception:
            pass

        return cls(
            t=getattr(ctx, "t", 0.0),
            bpm=getattr(ctx, "bpm", 120.0),
            beat_phase=getattr(ctx, "beat_phase", 0.0),
            energy=getattr(ctx, "energy_position", 0.5),
            tension=getattr(ctx, "tension_level", 0.5),
            rhythm=getattr(ctx, "bar_kick_strength", 0.5),
            melody=getattr(ctx, "bar_melody_strength", 0.5),
            phrase_role=phrase_role,
            phrase_progress=getattr(ctx, "phrase_progress", 0.0),
            in_buildup=getattr(ctx, "in_buildup", False),
            buildup_progress=getattr(ctx, "buildup_progress", 0.0),
            section_label=getattr(ctx, "section_label", "unknown"),
            is_climax=getattr(ctx, "is_climax", False),
        )


# ═══════════════════════════════════════════════════════════════════════
#  声部运行时状态
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class VoiceRuntimeState:
    """单个声部的完整运行时状态"""
    voice_id: int
    action_atom: Optional[ActionAtomState] = None
    preset_id: str = "hold_all"
    effect_params: Dict[str, Any] = field(default_factory=dict)
    # 当前形态特征（用于过渡计算）
    amplitude: float = 0.8
    density: float = 0.5
    phase: float = 0.0
    speed: float = 1.0
    # 空间角色
    spatial_role: str = "neutral"
    # 上次切换时间
    last_switch_t: float = 0.0
    # 当前动作持续时间
    duration: float = 4.0
