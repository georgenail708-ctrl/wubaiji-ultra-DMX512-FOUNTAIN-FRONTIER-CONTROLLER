# ⚠ FROZEN — NO UI ENTRY（2026-09-12 审计确认）
# 本包在当前仓库无任何 UI 入口（原 src/ui/deep_choreography_panel.py 已删除）。
# 改动前请先确认/恢复入口，否则你改的是死代码；新增功能不要建在此处。
# 见 ai-memory/maintainability-audit-2026-09-12.md 易损点 5。
"""
deep_aqua_core — DeepAqua 生成式 AI 编舞引擎核心包
===================================================
实现《全量设计规格书》中的完整编舞调度系统。

主要模块：
- dynamic_aggregation: 动态聚合编排核心（"编珠子"式资源调度）
- param_drivers: 六大参数驱动模块
- integration: 外部集成层（轻量数据结构与映射表）
- orchestrator: 核心调度器（DeepAquaOrchestrator）

快速使用：
    from src.engine.deep_aqua_core import DeepAquaOrchestrator
    from src.engine.deep_aqua_core.orchestrator import make_choreography_input

    orch = DeepAquaOrchestrator.from_topview(topview_positions, fixture_defs, bpm=128.0)

    # 每帧调用
    inp = make_choreography_input(bpm=128.0, energy=0.8, tension=0.6)
    dmx = orch.tick(inp, dt=0.033)
"""

from .dynamic_aggregation import (
    DynamicAggregationScheduler,
    ActionAtomType,
    ActionAtomState,
    DeviceAtomState,
    AggregationTopology,
    ACTION_THRESHOLD_TABLE,
    ACTION_FALLBACK_TABLE,
)

from .param_drivers import (
    ComplexityDriver,
    DeviceDensityDriver,
    ActivationMaskMode,
    ExpressionMatrixDriver,
    ExpressionWeights,
    VariationStrengthDriver,
    VariationMode,
    TransitionController,
    TransitionStyle,
    TransitionState,
    SpatialNarrativeEngine,
    SpatialRole,
    ThemeMemory,
)

from .integration import (
    MusicContextSnapshot,
    VoiceRuntimeState,
    ACTION_TO_PRESET,
    ACTION_TO_TOPOLOGY,
)

from .orchestrator import DeepAquaOrchestrator

__all__ = [
    # 动态聚合
    "DynamicAggregationScheduler",
    "ActionAtomType",
    "ActionAtomState",
    "DeviceAtomState",
    "AggregationTopology",
    "ACTION_THRESHOLD_TABLE",
    "ACTION_FALLBACK_TABLE",
    # 参数驱动
    "ComplexityDriver",
    "DeviceDensityDriver",
    "ActivationMaskMode",
    "ExpressionMatrixDriver",
    "ExpressionWeights",
    "VariationStrengthDriver",
    "VariationMode",
    "TransitionController",
    "TransitionStyle",
    "TransitionState",
    "SpatialNarrativeEngine",
    "SpatialRole",
    "ThemeMemory",
    # 集成层
    "DeepAquaOrchestrator",
    "MusicContextSnapshot",
    "VoiceRuntimeState",
    "ACTION_TO_PRESET",
    "ACTION_TO_TOPOLOGY",
]
