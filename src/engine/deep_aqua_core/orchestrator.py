"""
DeepAqua 生成式 AI 编舞引擎 — 核心调度器 (v3.0)
=================================================
架构：真正的自主生成编舞，而非参数调制。

决策流水线（每帧）：
  MusicContext
      │
      ▼
  MelodyAnalyzer ──────────────────────────────────────────────────────────┐
      │ MelodyAnalysisResult                                                │
      ▼                                                                     │
  DeviceGroupSelector ─── 音频驱动偏好预设 ─── 8种策略                     │
      │ SelectionDecision                                                   │
      │  ├─ selected_devices (哪些设备激活)                                 │
      │  ├─ n_groups (分几组)                                               │
      │  ├─ geometry_result (空间构型：对称/间隔/螺旋等)                    │
      │  └─ recommended_presets [(preset_id, score), ...]                  │
      ▼                                                                     │
  DynamicAggregationScheduler ─── 设备元动态聚合/解散                      │
      │ ActionAtomState (per voice)                                         │
      │  ├─ action_type (WAVE_PEAK/SPIRAL/BURST等，含降级逻辑)             │
      │  └─ bound_device_atoms (锁定的设备元列表)                           │
      ▼                                                                     │
  EffectAtomRegistry ─── AI 自主选择最匹配的 Effect preset                 │
      │ PresetAtomSpec                                                       │
      │  ├─ action_spec (min_devices, topology_preference)                  │
      │  └─ audio_affinity (energy/rhythm/tension/melody 亲和度)           │
      ▼                                                                     │
  TopologyAwareWaveGenerator ─── 基于真实 TopView 坐标生成波形             │
      │ {device_id: wave_value}                                             │
      ▼                                                                     │
  CapabilityAwareChoreographyEngine ─── 按设备能力生成编舞帧               │
      │ ChoreographyFrame (motor_x/y/z, pump, valve, R/G/B, dimmer)        │
      ▼                                                                     │
  ExpressionMatrixDriver ─── 旋律信号全量流入，统一通道权重调制 ◄──────────┘
      │ {channel: dmx_value (0..255)}
      ▼
  DMX Output → NetworkManager.effect_layer
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── 内部模块导入 ──────────────────────────────────────────────────────────
try:
    from .topview_reader import (
        TopViewReader, TopologyDetector, TopologyAwareWaveGenerator,
        TopologyProfile, TopologyType, DeviceNode,
    )
    from .device_capability import (
        DeviceCapabilityClassifier, CapabilityAwareChoreographyEngine,
        DeviceCapabilityProfile, ChoreographyFrame, MotorCapability,
        ChoreographyMode, summarize_capabilities,
    )
    from .melody_analyzer import (
        MelodyAnalyzer, MelodyAnalysisResult, ChordQuality,
    )
    from .channel_coherence import (
        ChannelCoherenceManager, PhaseOffsetLayer, TopologyScaler,
        ChannelType, InterpolationMode,
    )
    from .dynamic_aggregation import (
        DynamicAggregationScheduler, ActionAtomType, AggregationTopology,
        ActionAtomState,
    )
    from .device_group_selector import (
        DeviceGroupSelector, SelectionDecision, SelectionProfile,
    )
    from .effect_atom_registry import (
        EffectAtomRegistry, PresetAtomSpec, EffectActionClass,
    )
    from .param_drivers import (
        ExpressionMatrixDriver, ExpressionWeights,
        ComplexityDriver, DeviceDensityDriver,
        VariationStrengthDriver, TransitionController,
    )
except ImportError as e:
    log.warning("DeepAqua orchestrator: 部分模块导入失败 (%s)，将使用降级模式", e)


# ═══════════════════════════════════════════════════════════════════════════
#  1. 编舞输入上下文（每帧的完整输入）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ChoreographyInput:
    """
    每帧的完整编舞输入。
    来源：MusicContext（外部音频分析引擎）。
    所有参数均无硬性上限，DMX 层做最终截断。
    """
    # ── 音频基础参数 ──────────────────────────────────────────────────────
    bpm: float = 120.0              # 当前 BPM（无限制）
    energy: float = 0.5             # 音频能量 [0, ∞)
    tension: float = 0.3            # 和弦张力 [0, 1]
    beat_phase: float = 0.0         # 节拍相位 [0, 1]
    beat_strength: float = 0.5      # 节拍强度 [0, 1]
    phrase_phase: float = 0.0       # 乐句相位 [0, 1]
    phrase_role: str = "body"       # 乐句角色（intro/body/climax/break/outro/solo）

    # ── 旋律参数 ──────────────────────────────────────────────────────────
    melody_pitch: float = 0.5       # 旋律音高归一化 [0, 1]（0=最低，1=最高）
    melody_contour: float = 0.0     # 旋律走向 [-1, 1]（-1=下行，+1=上行）
    melody_velocity: float = 0.5    # 旋律力度 [0, 1]
    chord_root: int = 0             # 和弦根音（0-11，-1=未知）
    chord_quality: str = "major"    # 和弦性质字符串

    # ── 系统控制参数（无硬性上限）──────────────────────────────────────────
    complexity: float = 3.0         # 复杂度（声部数，无上限）
    device_density: float = 0.75    # 设备激活率 [0, 1]
    variation_strength: float = 0.5 # 变奏强度（无上限）
    transition_urgency: float = 0.5 # 过渡紧迫度（无上限）
    expression_style: str = "balanced"  # 表现力风格

    # ── 空间叙事参数 ──────────────────────────────────────────────────────
    spatial_direction: float = 0.0  # 空间方向 [-1=左/内，+1=右/外]
    is_question: bool = False        # 是否问句（触发向外拓扑）
    is_answer: bool = False          # 是否答句（触发向内拓扑）

    # ── 模式标志 ──────────────────────────────────────────────────────────
    is_solo: bool = False            # 是否旋律 Solo 模式

    # ── 内部计算字段（由 MelodyAnalyzer 填充）────────────────────────────
    chord_hue: float = 0.0           # 和弦色相 [0, 1]（由 MelodyAnalyzer 填充）
    chord_brightness: float = 0.8    # 和弦明度 [0, 1]

    @classmethod
    def from_music_context(cls, ctx: Any) -> "ChoreographyInput":
        """从 MusicContext 对象构建（兼容现有引擎接口）"""
        inp = cls()
        if ctx is None:
            return inp
        # 基础参数
        inp.bpm = float(getattr(ctx, "bpm", 120.0))
        inp.energy = float(getattr(ctx, "energy", 0.5))
        inp.tension = float(getattr(ctx, "tension", 0.3))
        inp.beat_phase = float(getattr(ctx, "beat_phase", 0.0))
        inp.beat_strength = float(getattr(ctx, "beat_strength", 0.5))
        inp.phrase_phase = float(getattr(ctx, "phrase_phase", 0.0))
        # 乐句角色
        role = getattr(ctx, "phrase_role", None) or getattr(ctx, "section_label", "body")
        inp.phrase_role = str(role)
        # 旋律参数
        inp.melody_pitch = float(getattr(ctx, "melody_pitch", 0.5))
        inp.melody_contour = float(getattr(ctx, "melody_contour", 0.0))
        inp.melody_velocity = float(getattr(ctx, "melody_velocity", 0.5))
        inp.chord_root = int(getattr(ctx, "chord_root", 0))
        inp.chord_quality = str(getattr(ctx, "chord_quality", "major"))
        # 系统参数
        inp.complexity = float(getattr(ctx, "complexity", 3.0))
        inp.device_density = float(getattr(ctx, "device_density", 0.75))
        inp.variation_strength = float(getattr(ctx, "variation_strength", 0.5))
        inp.transition_urgency = float(getattr(ctx, "transition_urgency", 0.5))
        inp.expression_style = str(getattr(ctx, "expression_style", "balanced"))
        # 空间叙事
        inp.spatial_direction = float(getattr(ctx, "spatial_direction", 0.0))
        inp.is_question = bool(getattr(ctx, "is_question", False))
        inp.is_answer = bool(getattr(ctx, "is_answer", False))
        inp.is_solo = bool(getattr(ctx, "is_solo", False))
        return inp


def make_choreography_input(**kwargs) -> ChoreographyInput:
    """便捷工厂函数，用于测试"""
    inp = ChoreographyInput()
    for k, v in kwargs.items():
        if hasattr(inp, k):
            setattr(inp, k, v)
    return inp


# ═══════════════════════════════════════════════════════════════════════════
#  2. 编舞决策上下文（每帧的内部决策状态）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ChoreographyDecision:
    """
    每帧的完整编舞决策状态。
    记录 AI 在这一帧做出的所有决策，用于调试和可视化。
    """
    # 旋律分析结果
    melody_result: Optional[Any] = None
    # 设备选取决策（每个声部一个）
    selection_decisions: List[Any] = field(default_factory=list)
    # 动作元状态（每个声部一个）
    action_atoms: List[Any] = field(default_factory=list)
    # 选中的 Effect preset（每个声部一个）
    selected_presets: List[Tuple[str, float]] = field(default_factory=list)
    # 波形值（每台设备）
    wave_values: Dict[str, float] = field(default_factory=dict)
    # 最终 DMX 输出
    dmx_output: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # 激活设备数
    active_device_count: int = 0
    # 当前拓扑类型
    topology_type: str = "unknown"
    # 当前声部数
    voice_count: int = 1


# ═══════════════════════════════════════════════════════════════════════════
#  3. DeepAquaOrchestrator — 核心调度器
# ═══════════════════════════════════════════════════════════════════════════

class DeepAquaOrchestrator:
    """
    DeepAqua 生成式 AI 编舞引擎核心调度器。

    设计原则：
    - 不是"特效播放器"，而是以 Effect 库为原子语义的生成式编排系统
    - 每帧自主决策：选哪些设备、用什么构型、播哪个 Effect、如何调制
    - 所有参数无硬性上限，DMX 层做最终截断
    - 从 TopView 读取真实设备坐标，自动适应任意布局

    使用方式：
        orch = DeepAquaOrchestrator.from_topview(positions, fixture_defs, bpm=128.0)
        while True:
            inp = make_choreography_input(bpm=128.0, energy=audio.energy, ...)
            dmx = orch.tick(inp, dt=0.033)
            network_manager.write_effect_layer(dmx)
    """

    def __init__(self):
        # ── 子模块 ────────────────────────────────────────────────────────
        self._effect_engine: Any = None  # 可选注入的 EffectEngine。用于 compute_at 采样。
        self._topology_detector = TopologyDetector()
        self._wave_generator = TopologyAwareWaveGenerator()
        self._cap_classifier = DeviceCapabilityClassifier()
        self._cap_engine = CapabilityAwareChoreographyEngine()
        self._melody_analyzer = MelodyAnalyzer()
        self._coherence_manager = ChannelCoherenceManager()
        self._phase_offset = PhaseOffsetLayer(bpm=120.0)

        # ── 核心决策模块（新增串联）──────────────────────────────────────
        self._effect_registry = EffectAtomRegistry()
        self._group_selector: Optional[DeviceGroupSelector] = None  # 初始化后创建
        self._aggregation_scheduler: Optional[DynamicAggregationScheduler] = None
        self._expression_matrix = ExpressionMatrixDriver()
        self._complexity_driver = ComplexityDriver()
        self._density_driver = DeviceDensityDriver()
        self._variation_driver = VariationStrengthDriver()
        self._transition_weaver = TransitionController()

        # ── 设备数据 ──────────────────────────────────────────────────────
        self._topology_profile: Optional[TopologyProfile] = None
        self._cap_profiles: Dict[str, DeviceCapabilityProfile] = {}
        self._device_nodes: List[DeviceNode] = []
        self._device_positions: List[Tuple[float, float]] = []

        # ── 时间与相位 ────────────────────────────────────────────────────
        self._bpm: float = 120.0
        self._current_time: float = 0.0
        self._global_phase: float = 0.0
        self._frame_count: int = 0

        # ── 旋律上下文缓存 ────────────────────────────────────────────────
        self._melody_ctx: Optional[MelodyAnalysisResult] = None

        # ── 上一帧决策（用于过渡和变奏）──────────────────────────────────
        self._last_decision: Optional[ChoreographyDecision] = None
        self._last_presets: List[str] = []   # 上一帧选中的 preset_id 列表
        self._preset_hold_frames: int = 0    # 当前 preset 已持续帧数
        self._preset_change_interval: int = 8  # 每 N 帧允许换一次 preset

        # ── H4: AI 编舞流水线降频缓存（opt-in，默认 1 = 每帧全跑 = 现状）──────
        # decision_interval > 1 时，"决策类"结果（选设备/选 preset/聚合）缓存复用，
        # 仅在 _frame_count % interval == 0 或音频突变（energy/beat 跳变超阈值）时
        # 重算，中间帧复用；时间推进/相位/wave 采样/preset 采样/逐设备 compute 仍
        # 每帧全跑。decision_interval == 1 时每帧都重算，输出与未引入缓存时完全一致。
        self._decision_interval: int = 1
        self._cached_decision: Optional[Dict[str, Any]] = None
        # 上次重算决策时的音频快照 (energy, beat_strength)，用于检测突变
        self._decision_audio_snapshot: Optional[Tuple[float, float]] = None
        self._decision_energy_threshold: float = 0.25  # 能量跳变阈值
        self._decision_beat_threshold: float = 0.35    # 节拍强度跳变阈值
        # _apply_channel_coherence 二次 wave compute 的帧内去重缓存（按 phase 量化）。
        # 同一帧内 global_phase 与 pump_phase_offset 对所有设备恒定，原本每设备都会
        # 用相同参数重算一遍 compute，这里按 round(phase,3) 缓存，整帧只算一次。
        self._coherence_wave_cache: Dict[float, Dict[str, float]] = {}

        # ── UI 参数状态（由 update_params 维护）────────────────────────────
        self._audio_sensitivity: float = 1.0
        self._section_aware: bool = True
        self._allowed_atoms: List[str] = []
        self._beat_quantize: float = 0.7
        self._canon_enabled: bool = True
        self._canon_delay_beats: float = 1.0

        # ── 初始化标志 ────────────────────────────────────────────────────
        self._is_initialized: bool = False

    # ─────────────────────────────────────────────────────────────────────
    #  工厂方法
    # ─────────────────────────────────────────────────────────────────────

    @classmethod
    def from_topview(
        cls,
        topview_positions: List[Dict[str, Any]],
        fixture_defs: Optional[Dict[str, Any]] = None,
        bpm: float = 120.0,
        effect_engine: Any = None,
    ) -> "DeepAquaOrchestrator":
        """
        从 TopView 俯视图数据创建编舞引擎。

        Args:
            topview_positions: TopViewPanel.get_fixture_positions() 的输出
                               [{"id": "...", "name": "...", "pos_x": ..., "pos_y": ...}, ...]
            fixture_defs:      fixture 定义字典（用于设备能力识别）
            bpm:               初始 BPM
        Returns:
            已初始化的 DeepAquaOrchestrator
        """
        orch = cls()
        orch.initialize(topview_positions, fixture_defs, bpm, effect_engine=effect_engine)
        return orch

    def initialize(
        self,
        topview_positions: List[Dict[str, Any]],
        fixture_defs: Optional[Dict[str, Any]] = None,
        bpm: float = 120.0,
        effect_engine: Any = None,
    ) -> None:
        """初始化引擎（可重复调用，用于重新配置）"""
        self._bpm = bpm
        self._phase_offset.update_bpm(bpm)

        # 1. 自动检测拓扑类型（同时生成 DeviceNode 列表）
        self._topology_profile = self._topology_detector.detect(topview_positions)

        if not self._topology_profile or not self._topology_profile.nodes:
            return

        # 2. 从 TopologyProfile 获取 DeviceNode 列表
        self._device_nodes = self._topology_profile.nodes
        n = len(self._device_nodes)

        # 3. 提取设备物理坐标（归一化）
        self._device_positions = [
            (node.norm_x, node.norm_y) for node in self._device_nodes
        ]

        # 4. 自动识别每台设备的运动能力
        # classify_batch 按 fixture_defs 的 key（== 实例 id）建键，与 topview_positions
        # 的 "id" 同源，故直接按 dev_id 取用即可。逐实例 deepcopy 覆写 device_id/name，
        # 避免多台设备共用同一 profile 对象造成互干扰。
        if fixture_defs:
            type_profiles = self._cap_classifier.classify_batch(fixture_defs)
            expanded: Dict[str, DeviceCapabilityProfile] = {}
            for entry in topview_positions:
                dev_id = str(entry.get("id", ""))
                if not dev_id:
                    continue
                cap = type_profiles.get(dev_id)
                if cap is None:
                    continue
                # 避免多台设备共用同一个 profile 对象带来的互干扰；复一份并覆写 device_id
                try:
                    from copy import deepcopy as _dc
                    cap = _dc(cap)
                    cap.device_id = dev_id
                    cap.name = str(entry.get("name") or dev_id)
                except Exception:
                    pass
                expanded[dev_id] = cap
            if expanded:
                self._cap_profiles = expanded
            else:
                # 未能映射 → 回退到推断路径
                self._cap_profiles = self._infer_cap_profiles_from_topology()
        else:
            # 无 fixture_defs 时：基于拓扑类型和设备名称自动推断能力
            self._cap_profiles = self._infer_cap_profiles_from_topology()

        # 5. 初始化 EffectAtomRegistry（扫描所有 preset）
        # 修复：优先使用调用方注入的 effect_engine，其次尝试新建一个。
        # 同时缓存 engine 供后续 compute_at 采样使用。
        if effect_engine is not None:
            self._effect_engine = effect_engine
        else:
            try:
                from ...ui.effect_engine import EffectEngine as _EE  # type: ignore
                self._effect_engine = _EE()
            except Exception:
                try:
                    from src.ui.effect_engine import EffectEngine as _EE  # type: ignore
                    self._effect_engine = _EE()
                except Exception:
                    self._effect_engine = None
        try:
            self._effect_registry.scan_from_engine(self._effect_engine)
        except Exception:
            pass  # 无 EffectEngine 时降级

        # 6. 初始化 DeviceGroupSelector（含 EffectAtomRegistry）
        self._group_selector = DeviceGroupSelector(
            registry=self._effect_registry,
        )

        # 7. 初始化 DynamicAggregationScheduler
        self._aggregation_scheduler = DynamicAggregationScheduler(
            fixture_positions=self._device_positions,
        )

        # 8. 注册设备到 ChannelCoherenceManager
        self._register_device_coherence()

        # 9. 初始化参数驱动器
        self._complexity_driver.update_params(3.0, max_voices=n)
        self._density_driver.update_params(0.75)

        self._is_initialized = True
        log.info(
            "DeepAqua 初始化完成: %d 台设备, 拓扑=%s (置信度=%.2f)",
            n,
            self._topology_profile.topo_type.value,
            self._topology_profile.confidence,
        )

    def _infer_cap_profiles_from_topology(self) -> Dict[str, "DeviceCapabilityProfile"]:
        """无 fixture_defs 时，基于拓扑类型和设备名称自动推断设备能力配置"""
        profiles: Dict[str, DeviceCapabilityProfile] = {}
        topo_type = self._topology_profile.topo_type.value if self._topology_profile else "unknown"
        n = len(self._device_nodes)

        for idx, node in enumerate(self._device_nodes):
            dev_id = node.device_id
            name = (node.name if hasattr(node, 'name') and node.name else dev_id).lower()

            # 基于名称关键词推断设备类型
            is_motor = any(k in name for k in ('motor', '电机', 'swing', 'pan', 'tilt', 'nozzle', '喂头'))
            is_pump = any(k in name for k in ('pump', '泵', 'jet', '水柱', 'fountain'))
            is_light = any(k in name for k in ('light', '灯', 'led', 'rgb', 'color', 'lamp'))
            is_matrix = topo_type == 'grid' or any(k in name for k in ('matrix', '矩阵', 'grid'))
            is_fountain = topo_type == 'fountain' or any(k in name for k in ('fountain', '跑泉', 'jet'))

            # 默认：如果名称中没有明确关键词，根据拓扑类型分配
            if not (is_motor or is_pump or is_light):
                if topo_type in ('fountain', 'linear'):
                    is_motor = True
                    is_pump = True
                elif topo_type == 'grid':
                    is_matrix = True
                    is_pump = True
                else:
                    is_motor = True
                    is_pump = True
            # 水柱喷泉系统：有电机的设备通常也有泵（除非明确是纯灯光设备）
            if is_motor and not is_light:
                is_pump = True

            # 构建能力配置
            from .device_capability import (
                DeviceCapabilityProfile, MotorCapability, ChoreographyMode
            )
            profile = DeviceCapabilityProfile(
                device_id=dev_id,
                name=name,
                has_motor_x=is_motor,
                has_motor_y=is_motor,
                has_pump=is_pump,
                has_valve=is_pump,
                has_dimmer=True,
                has_rgb=is_light or True,  # 默认所有设备有灯光
                motor_axes=2 if is_motor else 0,
                motor_range_deg=180.0 if is_motor else 0.0,
                is_open_close=False,
                is_matrix_cell=is_matrix,
                is_fountain_jet=is_fountain,
                fountain_index=idx if is_fountain else -1,
                motor_capability=(
                    MotorCapability.FULL_XY if is_motor
                    else (MotorCapability.FOUNTAIN_JET if is_fountain
                          else (MotorCapability.MATRIX_CELL if is_matrix
                                else MotorCapability.NO_MOTOR))
                ),
                choreography_mode=(
                    ChoreographyMode.PLANAR_PATH if is_motor
                    else (ChoreographyMode.FOUNTAIN_NOTE if is_fountain
                          else (ChoreographyMode.MATRIX_PATTERN if is_matrix
                                else (ChoreographyMode.PUMP_LIGHT_FULL if is_pump
                                      else ChoreographyMode.LIGHT_HARMONY)))
                ),
                motor_expression_weight=1.0 if is_motor else 0.0,
                pump_expression_weight=0.9 if is_pump else 0.0,
                light_expression_weight=0.8,
                valve_expression_weight=0.5 if is_pump else 0.0,
            )
            profiles[dev_id] = profile
        return profiles

    def _register_device_coherence(self) -> None:
        """将所有设备注册到 ChannelCoherenceManager"""
        for i, node in enumerate(self._device_nodes):
            cap = self._cap_profiles.get(node.device_id)
            if not cap:
                continue
            ch_map: Dict[ChannelType, int] = {}
            if cap.has_motor_x:
                ch_map[ChannelType.MOTOR] = 1
            if cap.has_pump:
                ch_map[ChannelType.PUMP] = 2
            if cap.has_valve:
                ch_map[ChannelType.VALVE] = 3
            if cap.has_dimmer or cap.has_rgb:
                ch_map[ChannelType.LIGHT] = 4
            pos = node.norm_x
            try:
                self._coherence_manager.register_device(
                    device_id=node.device_id,
                    position=pos,
                    channels=ch_map,
                )
            except Exception:
                pass

        all_ids = [node.device_id for node in self._device_nodes]
        try:
            self._coherence_manager.create_group(group_id="main", device_ids=all_ids)
        except Exception:
            pass

        # ───────────────────────────────────────────────────────────────────
    #  主循环接口
    # ───────────────────────────────────────────────────────────────────

    def update_params(self, params: Dict[str, Any]) -> None:
        """
        从 UI 同步参数到 Orchestrator。
        支持的键：complexity, device_density, variation_strength,
        expression_style, transition_style, audio_sensitivity,
        section_aware, allowed_atoms, beat_quantize,
        canon_enabled, canon_delay_beats,
        motor_weight, pump_weight, valve_weight, light_weight
        """
        p = params
        # 更新复杂度驱动器
        if 'complexity' in p:
            n = len(self._device_nodes) if self._device_nodes else 32
            self._complexity_driver.update_params(
                float(p['complexity']), max_voices=n
            )
        # 更新密度驱动器
        if 'device_density' in p:
            self._density_driver.update_params(float(p['device_density']))
        # 更新表现力风格（修复：使用正确的 _expression_matrix 字段名，并做旧名称兼容映射）
        if 'expression_style' in p:
            try:
                _style_alias = {
                    'motion_design': 'harmonic',
                    'rhythmic_beat': 'rhythmic',
                    'narrative_emo': 'narrative',
                    'custom':        'harmonic',
                    'balanced':      'harmonic',
                }
                style_raw = str(p['expression_style'])
                style_key = _style_alias.get(style_raw, style_raw)
                self._expression_matrix.update_style(style_key)
            except Exception:
                pass
        # 更新过渡风格
        if 'transition_style' in p:
            try:
                from .param_drivers import TransitionStyle
                _ts_map = {
                    'organic':  TransitionStyle.ORGANIC,
                    'rhythmic': TransitionStyle.RHYTHMIC,
                    'morph':    TransitionStyle.MORPH,
                }
                ts = _ts_map.get(str(p['transition_style']), TransitionStyle.ORGANIC)
                self._transition_weaver.update_style(ts)
            except Exception:
                pass
        # 更新音频灵敏度（存储后在 tick 中乘以 inp.energy）
        if 'audio_sensitivity' in p:
            self._audio_sensitivity = float(p['audio_sensitivity'])
        # 更新段落感知开关
        if 'section_aware' in p:
            self._section_aware = bool(p['section_aware'])
        # 更新允许的动作元列表
        if 'allowed_atoms' in p:
            self._allowed_atoms = list(p['allowed_atoms'])
        # 更新节拍量化强度
        if 'beat_quantize' in p:
            self._beat_quantize = float(p['beat_quantize'])
        # 更新声部呼应（卡农）开关
        if 'canon_enabled' in p:
            self._canon_enabled = bool(p['canon_enabled'])
        # 更新卡农延迟节拍数
        if 'canon_delay_beats' in p:
            self._canon_delay_beats = float(p['canon_delay_beats'])
        # 主题记忆开关：本编排器无主题记忆子系统，无法安全接线，如实记录并忽略
        if 'theme_memory_enabled' in p:
            log.warning("theme_memory_enabled 在本编排器中无对应子系统，已忽略（UI 开关无效果）")
        # 更新通道权重
        for key, attr in [
            ('motor_weight', 'motor_expression_weight'),
            ('pump_weight', 'pump_expression_weight'),
            ('light_weight', 'light_expression_weight'),
            ('valve_weight', 'valve_expression_weight'),
        ]:
            if key in p:
                for cap in self._cap_profiles.values():
                    try:
                        setattr(cap, attr, float(p[key]))
                    except Exception:
                        pass
        # 更新变奏强度
        if 'variation_strength' in p:
            self._variation_driver.update_params(
                float(p['variation_strength'])
            )
        # H4: 决策降频间隔（opt-in）。默认 1 = 每帧全跑决策链 = 现状。
        # >1 时决策类结果缓存复用，详见 __init__ 中字段说明。
        if 'decision_interval' in p:
            try:
                self._decision_interval = max(1, int(p['decision_interval']))
            except Exception:
                pass

    def tick(
        self,
        inp: ChoreographyInput,
        dt: float = 0.033,
    ) -> Dict[str, Dict[str, float]]:
        """
        主循环：每帧调用，输出所有设备的 DMX 值。

        完整决策流水线：
        1. 更新时间/相位
        2. MelodyAnalyzer → 旋律分析
        3. DeviceGroupSelector → 音频驱动设备分组
        4. DynamicAggregationScheduler → 设备元聚合
        5. EffectAtomRegistry → AI 自主选 Effect preset
        6. TopologyAwareWaveGenerator → 基于真实坐标生成波形
        7. CapabilityAwareChoreographyEngine → 按设备能力生成编舞帧
        8. ExpressionMatrixDriver → 旋律信号全量流入，统一调制

        Returns:
            {device_id: {channel_type: dmx_value(0..255)}}
        """
        if not self._is_initialized:
            return {}
        # ── 0. 应用 UI 参数到输入 ───────────────────────────────────────────────
        # 音频灵敏度：缩放 inp.energy，让表现力整体加强或减弱
        inp.energy = inp.energy * self._audio_sensitivity
        # 节拍量化：将 beat_phase 向最近节拍点吸附
        if self._beat_quantize > 0.0:
            quantized = round(inp.beat_phase * 4) / 4.0  # 量化到 1/4 节拍
            inp.beat_phase = (
                inp.beat_phase * (1.0 - self._beat_quantize)
                + quantized * self._beat_quantize
            )
        # ── 1. 时间与相位更新 ────────────────────────────────────────────────────
        self._current_time += dt
        beat_duration = 60.0 / max(1.0, inp.bpm)
        self._global_phase = (self._current_time / beat_duration) % 1.0
        if abs(inp.bpm - self._bpm) > 0.1:
            self._bpm = inp.bpm
            self._phase_offset.update_bpm(inp.bpm)
        self._phase_offset.tick(dt)
        self._phase_offset.modulate_by_audio(
            inp.energy, inp.tension, 1.0 if inp.is_solo else 0.0
        )

        # ── 2. 旋律分析 ──────────────────────────────────────────────────
        # 每帧必跑：旋律分析驱动颜色/Solo 标志，且 MelodyAnalyzer 内部有状态累积，
        # 缓存会让颜色/音高在 interval 帧内冻结成阶梯，破坏视觉连续性。
        self._melody_ctx = self._run_melody_analysis(inp)

        # ── 3~7. 决策类流水线（声部数/激活率/分组/聚合/选 preset）────────────
        # H4 降频缓存边界：以下结果属于"决策类"（选哪些设备、分几组、聚合构型、
        # 选哪个 preset），本是低频事件，可在 interval 帧内复用。
        # 复用时仍逐帧用当前 _current_time 做 preset 采样与逐设备 compute，
        # 因此动画连续不冻结，只是"决策"在窗口内保持稳定。
        # decision_interval == 1 时每帧都重算，与未引入缓存时逐字节一致。
        if self._should_recompute_decision(inp):
            dec = self._compute_decision_pipeline(inp)
            self._cached_decision = dec
            self._decision_audio_snapshot = (
                float(inp.energy), float(inp.beat_strength)
            )
        else:
            dec = self._cached_decision

        n_voices = dec["n_voices"]
        selection = dec["selection"]
        action_atoms = dec["action_atoms"]
        selected_presets = dec["selected_presets"]

        # 卡农：按当前聚合结果构建设备→声部映射（声部 0 为主声部，不延迟）
        voice_of_device: Dict[int, int] = {}
        if self._canon_enabled:
            for _vid, _atom in enumerate(action_atoms):
                for _fi in _atom.get_all_fixture_indices():
                    if _fi not in voice_of_device:
                        voice_of_device[_fi] = _vid

        # ── 7.5 采样 AI 选中的 Effect preset（每帧：compute_at 用当前 _current_time）─
        # 修复：让选出的 effect preset 真正参与 DMX 合成，而不仅仅被记录。
        preset_contrib: Dict[str, Dict[str, float]] = self._sample_selected_presets(
            selected_presets, inp
        )

        # ── 7.6 驱动 TransitionController：检测 preset 集变化，启动过渡 ────────
        # 修复：原本 TransitionController 在 tick 中从不被调用，过渡风格 UI 形同虚设。
        new_pids = tuple(pid for pid, _ in selected_presets)
        if new_pids != tuple(self._last_presets) and new_pids:
            try:
                # 使用 voice 0 作为全局过渡载体
                self._transition_weaver.start_transition(
                    voice_id=0,
                    urgency=float(min(1.0, max(0.2, inp.energy))),
                    bpm=float(self._bpm),
                    from_state={"amplitude": 0.8, "density": 0.5, "phase": 0.0},
                    to_state={"amplitude": 1.0, "density": 0.7, "phase": 0.0},
                )
            except Exception:
                pass
        try:
            _is_done, _blend = self._transition_weaver.update(0, float(dt))
            transition_blend = float(_blend) if not _is_done else 1.0
        except Exception:
            transition_blend = 1.0

        # ── 8. 波形生成（TopologyAwareWaveGenerator）─────────────────
        wave_values = self._generate_wave_values(inp)

        # ── 9. 逐设备生成编舞帧 ──────────────────────────────────────────
        # H4: 清空帧内 wave 二次采样缓存。该缓存仅在单帧内有效（按量化 phase 去重），
        # 跨帧不复用 —— 因为 global_phase/能量等每帧都变。
        self._coherence_wave_cache = {}
        output: Dict[str, Dict[str, float]] = {}
        active_device_ids = set(
            self._device_nodes[i].device_id
            for i in selection.active_devices
            if i < len(self._device_nodes)
        )

        for i, node in enumerate(self._device_nodes):
            device_id = node.device_id
            cap = self._cap_profiles.get(device_id)
            if not cap:
                continue

            if device_id not in active_device_ids:
                # 未激活设备：归零
                output[device_id] = self._build_zero_output(cap)
                continue

            # 获取该设备的波形值
            wave_value = wave_values.get(device_id, 0.5)

            # 计算设备在排列中的线性相位
            linear_phase = node.linear_phase

            # 计算相位错位（通道间时序偏移）
            from .channel_coherence import ChannelType as _CT
            motor_phase_offset = self._phase_offset.get_offset_seconds(_CT.MOTOR) if hasattr(self._phase_offset, 'get_offset_seconds') else 0.0
            pump_phase_offset = self._phase_offset.get_offset_seconds(_CT.PUMP) if hasattr(self._phase_offset, 'get_offset_seconds') else 0.0
            # 卡农延迟：该设备所属声部的节拍相位偏移（0 声部/关闭时为 0）
            canon_offset = self._apply_voice_canon_offset(voice_of_device.get(i, 0))

            # 生成编舞帧（按设备能力）
            frame = self._cap_engine.compute_frame(
                cap_profile=cap,
                melody_pitch=inp.melody_pitch,
                melody_contour=inp.melody_contour,
                melody_velocity=inp.melody_velocity,
                chord_tension=inp.tension,
                chord_hue=inp.chord_hue,
                chord_brightness=inp.chord_brightness,
                energy=inp.energy,
                beat_phase=(inp.beat_phase + motor_phase_offset + canon_offset) % 1.0,
                beat_strength=inp.beat_strength,
                phrase_phase=inp.phrase_phase,
                is_solo=inp.is_solo,
                wave_value=wave_value,
                linear_phase=linear_phase,
            )

            # ── 10. ExpressionMatrixDriver 全量旋律信号调制 ──────────────
            raw_values = self._frame_to_raw_dict(frame, cap)
            modulated = self._expression_matrix.apply_full(
                raw_values=raw_values,
                energy=inp.energy,
                rhythm=inp.beat_strength,
                tension=inp.tension,
                melody=inp.melody_velocity,
                melody_result=self._melody_ctx,
                device_index=i,
                total_devices=len(self._device_nodes),
            )

            # ── 11. 变奏强度调制 ─────────────────────────────────────────
            modulated = self._apply_variation(modulated, inp)

            # ── 12. 空间叙事调制 ─────────────────────────────────────────
            modulated = self._apply_spatial_narrative(modulated, inp, node)

            # ── 13. 通道协同：泵/灯跟随电机空间位置 ─────────────────────
            modulated = self._apply_channel_coherence(
                modulated, cap, node, wave_value, inp, pump_phase_offset
            )

            # ── 13.5 叠加 AI Preset 贡献值（HTP/电机均值）────────────
            # 修复：selected_presets 现在真正影响输出，
            #       并且在 preset 切换瞬间由 TransitionController.blend 控制混入软硬。
            pc = preset_contrib.get(device_id)
            if pc:
                _blend = transition_blend
                for ch_t, p_val in pc.items():
                    cur = float(modulated.get(ch_t, 0.0))
                    blended_p = cur * (1.0 - _blend) + float(p_val) * _blend
                    if ch_t.startswith("motor"):
                        # 电机：均值叠加，避免两个源争夺中位
                        modulated[ch_t] = (cur + blended_p) * 0.5
                    else:
                        # HTP：取较大者
                        modulated[ch_t] = max(cur, blended_p)

            # ── 14. DMX 截断（0..255）───────────────────────────────
            output[device_id] = self._clamp_dmx(modulated)

        # ── 记录决策状态 ─────────────────────────────────────────────────
        decision = ChoreographyDecision(
            melody_result=self._melody_ctx,
            selection_decisions=[selection],
            action_atoms=action_atoms,
            selected_presets=selected_presets,
            wave_values=wave_values,
            dmx_output=output,
            active_device_count=len(active_device_ids),
            topology_type=self._topology_profile.topo_type.value
            if self._topology_profile else "unknown",
            voice_count=n_voices,
        )
        self._last_decision = decision
        self._frame_count += 1
        self._preset_hold_frames += 1

        return output

    def tick_from_music_context(
        self,
        music_ctx: Any,
        dt: float = 0.033,
    ) -> Dict[str, Dict[str, float]]:
        """从 MusicContext 对象驱动（兼容现有引擎接口）"""
        inp = ChoreographyInput.from_music_context(music_ctx)
        return self.tick(inp, dt)

    # ─────────────────────────────────────────────────────────────────────
    #  决策子流程
    # ─────────────────────────────────────────────────────────────────────

    def _should_recompute_decision(self, inp: ChoreographyInput) -> bool:
        """
        H4: 判定本帧是否需要重算"决策类"流水线。

        返回 True 的情形（任一满足）：
        - decision_interval <= 1（默认，等于现状：每帧重算）
        - 尚无缓存（首帧或 reset 后）
        - 到达节流窗口边界：_frame_count % interval == 0
        - 音频突变：energy 或 beat_strength 相对上次重算时的快照跳变超阈值
          （让强拍/落点/能量骤变能即时响应，而非等到窗口边界）

        绝不依赖时间推进量（_current_time/_global_phase），只看离散帧序号与音频。
        """
        interval = self._decision_interval
        if interval <= 1:
            return True
        if self._cached_decision is None:
            return True
        if self._frame_count % interval == 0:
            return True
        snap = self._decision_audio_snapshot
        if snap is None:
            return True
        d_energy = abs(float(inp.energy) - snap[0])
        d_beat = abs(float(inp.beat_strength) - snap[1])
        if (d_energy >= self._decision_energy_threshold
                or d_beat >= self._decision_beat_threshold):
            return True
        return False

    def _compute_decision_pipeline(self, inp: ChoreographyInput) -> Dict[str, Any]:
        """
        H4: 决策类流水线（原 tick 步骤 3~7 的内容，原样搬运）。

        计算并返回可在 interval 帧内复用的决策结果：
          - n_voices         声部数（复杂度驱动）
          - activation_rate  设备激活率（密度驱动）
          - selection        设备分组决策（DeviceGroupSelector）
          - action_atoms     设备元聚合状态（DynamicAggregationScheduler）
          - selected_presets AI 选中的 Effect preset 列表

        注意：本方法内不推进任何时间/相位量，仅读取当前 inp 与已更新好的
        self._current_time / self._global_phase（聚合调度按 current_time 锁定设备元，
        复用窗口内 current_time 不回退，故复用安全）。
        """
        # ── 3. 复杂度驱动：计算声部数 ────────────────────────────────────
        # base_complexity 归一化为 [0,1]（用户可输入 0~N，除以 MAX_VOICES 归一化）
        max_v = max(1, len(self._device_nodes) // 2)
        base_norm = min(1.0, inp.complexity / max(1.0, max_v))
        self._complexity_driver.update_params(base_norm, max_voices=max_v)
        section_aware = self._section_aware
        is_climax = section_aware and inp.phrase_role in ('climax', 'drop')
        in_buildup = section_aware and inp.phrase_role in ('buildup', 'build')
        n_voices = self._complexity_driver.compute_target_voice_count(
            inp.energy, inp.tension,
            in_buildup=in_buildup,
            buildup_progress=inp.phrase_phase,
            is_climax=is_climax,
        )
        n_voices = max(1, n_voices)

        # ── 4. 密度驱动：计算激活率 ──────────────────────────────────
        self._density_driver.update_params(inp.device_density)
        activation_rate = self._density_driver.compute_music_driven_density(
            base_density=inp.device_density,
            energy=inp.energy,
            rhythm_intensity=inp.beat_strength,
            in_buildup=in_buildup,
            buildup_progress=inp.phrase_phase,
        )

        # ── 5. 设备分组决策（DeviceGroupSelector）────────────────────────
        all_device_indices = list(range(len(self._device_nodes)))
        selection = self._run_group_selection(
            inp, all_device_indices, activation_rate, n_voices
        )

        # ── 6. 设备元聚合（DynamicAggregationScheduler）──────────────────
        action_atoms = self._run_aggregation(inp, selection, n_voices)

        # ── 7. AI 自主选择 Effect preset ─────────────────────────────────
        selected_presets = self._select_effect_presets(
            inp, selection, action_atoms
        )

        return {
            "n_voices": n_voices,
            "activation_rate": activation_rate,
            "selection": selection,
            "action_atoms": action_atoms,
            "selected_presets": selected_presets,
        }

    def _run_melody_analysis(self, inp: ChoreographyInput) -> Optional[MelodyAnalysisResult]:
        """Step 2: 旋律分析"""
        try:
            pitch_midi = 48.0 + inp.melody_pitch * 48.0  # 48..96 MIDI
            result = self._melody_analyzer.analyze(
                energy=inp.energy,
                melody=inp.melody_velocity,
                tension=inp.tension,
                rhythm=inp.beat_strength,
                bpm=inp.bpm,
                pitch_midi=pitch_midi,
                chord_root=inp.chord_root,
                chord_quality_hint=inp.chord_quality,
                note_on=inp.melody_velocity > 0.1,
                section_label=inp.phrase_role,
                dt=0.033,
            )
            # 将旋律分析结果反馈到 inp（更新颜色和 Solo 标志）
            if result:
                inp.chord_hue = result.color_hue / 360.0
                inp.chord_brightness = result.color_brightness
                if result.is_solo_mode:
                    inp.is_solo = True
            return result
        except Exception as e:
            log.debug("旋律分析失败: %s", e)
            return None

    def _run_group_selection(
        self,
        inp: ChoreographyInput,
        all_device_indices: List[int],
        activation_rate: float,
        n_voices: int,
    ) -> SelectionDecision:
        """Step 5: 音频驱动设备分组（DeviceGroupSelector）"""
        if not self._group_selector:
            # 降级：简单按激活率选取
            n_active = max(1, round(len(all_device_indices) * activation_rate))
            return SelectionDecision(
                active_devices=all_device_indices[:n_active],
                n_groups=min(n_voices, n_active),
                reason="降级模式",
            )

        section_aware = self._section_aware
        is_climax = section_aware and inp.phrase_role in ("climax", "drop")
        in_buildup = section_aware and inp.phrase_role in ("buildup", "build")
        in_breakdown = section_aware and inp.phrase_role in ("break", "breakdown", "outro")

        try:
            decision = self._group_selector.select(
                available_devices=all_device_indices,
                energy=inp.energy,
                rhythm=inp.beat_strength,
                tension=inp.tension,
                melody=inp.melody_velocity,
                bpm=inp.bpm,
                phrase_role=inp.phrase_role,
                is_climax=is_climax,
                in_buildup=in_buildup,
                in_breakdown=in_breakdown,
                density_activation_rate=activation_rate,
                positions=self._device_positions,
                complexity=inp.complexity,
            )
            return decision
        except Exception as e:
            log.debug("DeviceGroupSelector 失败: %s", e)
            n_active = max(1, round(len(all_device_indices) * activation_rate))
            return SelectionDecision(
                active_devices=all_device_indices[:n_active],
                n_groups=1,
                reason=f"降级: {e}",
            )

    def _run_aggregation(
        self,
        inp: ChoreographyInput,
        selection: SelectionDecision,
        n_voices: int,
    ) -> List[ActionAtomState]:
        """Step 6: 设备元动态聚合（DynamicAggregationScheduler）"""
        if not self._aggregation_scheduler:
            return []

        # 根据音频参数选择动作元类型
        action_type = self._select_action_type(inp)

        # 根据拓扑和能量选择聚合拓扑
        agg_topology = self._select_aggregation_topology(inp)

        action_atoms = []
        try:
            for voice_id in range(n_voices):
                atom = self._aggregation_scheduler.aggregate_for_action(
                    voice_id=voice_id,
                    action_type=action_type,
                    topology=agg_topology,
                    density=inp.device_density,
                    current_time=self._current_time,
                    priority=n_voices - voice_id,  # 声部 0 优先级最高
                )
                action_atoms.append(atom)
        except Exception as e:
            log.debug("DynamicAggregationScheduler 失败: %s", e)

        return action_atoms

    def _select_effect_presets(
        self,
        inp: ChoreographyInput,
        selection: SelectionDecision,
        action_atoms: List[ActionAtomState],
    ) -> List[Tuple[str, float]]:
        """
        Step 7: AI 自主选择 Effect preset。

        优先使用 DeviceGroupSelector 推荐的 preset（已经过音频亲和度评分），
        同时考虑：
        - 设备数量可行性（min_devices 门槛）
        - 拓扑偏好匹配
        - 变奏：每 N 帧允许切换一次 preset，避免频繁跳变
        """
        # 如果 DeviceGroupSelector 已推荐了 preset，优先使用
        recommended = getattr(selection, "recommended_presets", [])
        if recommended:
            # 过滤可行的 preset
            n_devices = len(selection.active_devices)
            feasible = []
            for pid, score in recommended[:10]:
                try:
                    spec = self._effect_registry.get_spec(pid)
                    if spec and spec.action_spec.min_devices <= n_devices:
                        feasible.append((pid, score))
                except Exception:
                    feasible.append((pid, score))

            if feasible:
                # 变奏控制：每 N 帧才允许换 preset
                if (self._preset_hold_frames >= self._preset_change_interval
                        or not self._last_presets):
                    # 选最高分 preset
                    best = feasible[0]
                    self._last_presets = [best[0]]
                    self._preset_hold_frames = 0
                    # 根据变奏强度动态调整切换间隔
                    vs = inp.variation_strength
                    self._preset_change_interval = max(2, int(8 / max(0.1, vs)))
                    return [best]
                else:
                    # 保持上一帧的 preset
                    return [(pid, 0.0) for pid in self._last_presets]

        # 降级：直接查询 EffectAtomRegistry
        try:
            results = self._effect_registry.query_by_audio(
                energy=inp.energy,
                rhythm=inp.beat_strength,
                tension=inp.tension,
                melody=inp.melody_velocity,
                n_devices=len(selection.active_devices),
                top_k=3,
            )
            if results:
                best = results[0]
                self._last_presets = [best[0]]
                return [best]
        except Exception:
            pass

        return []

    # ─────────────────────────────────────────────────────────────────────
    #  Effect Preset 采样：让选中的 preset 真正影响 DMX
    # ─────────────────────────────────────────────────────────────────────
    def _sample_selected_presets(
        self,
        selected: List[Tuple[str, float]],
        inp: "ChoreographyInput",
    ) -> Dict[str, Dict[str, float]]:
        """
        对选中的 effect preset 调用 EffectEngine.compute_at 获得单帧贡献。
        返回: {device_id: {channel_type: dmx_value(0..255)}}
        """
        if not selected or self._effect_engine is None or not self._device_nodes:
            return {}

        # 构造伪 fixture 列表（只需 .id 属性）与 positions/fixture_defs
        class _F:
            __slots__ = ("id",)
            def __init__(self, _id):
                self.id = _id
        fixtures = [_F(node.device_id) for node in self._device_nodes]
        positions = {
            node.device_id: {"pos_x": float(node.norm_x), "pos_y": float(node.norm_y)}
            for node in self._device_nodes
        }
        # fixture_defs 传空字典即可，v27 所有 preset 只读 params 不读 fdefs
        fixture_defs: Dict[str, Any] = {}

        out: Dict[str, Dict[str, float]] = {}
        t = float(self._current_time)
        for preset_id, score in selected:
            try:
                ef = self._effect_engine.compute_at(
                    preset_id=preset_id,
                    t=t,
                    params={},   # 使用每个 preset 的内置默认参数
                    fixtures=fixtures,
                    fixture_defs=fixture_defs,
                    positions=positions,
                    fps=40.0,
                    sort_mode="index",
                )
            except Exception as e:
                log.debug("compute_at(%s) 失败: %s", preset_id, e)
                continue
            if ef is None or not ef.values:
                continue
            # 将帧按设备索引反映射为 device_id → 通道
            for idx, ch_dict in enumerate(ef.values):
                if not ch_dict or idx >= len(self._device_nodes):
                    continue
                dev_id = self._device_nodes[idx].device_id
                bucket = out.setdefault(dev_id, {})
                # 贡献按得分权重缩放（score≥0.4 作为着色强度）
                w = max(0.4, min(1.0, float(score)))
                for ch_t, v in ch_dict.items():
                    new_v = float(v) * w
                    # 多 preset 在同一设备同一通道上：HTP
                    bucket[ch_t] = max(bucket.get(ch_t, 0.0), new_v)
        return out

    def _generate_wave_values(self, inp: ChoreographyInput) -> Dict[str, float]:
        """Step 8: 基于真实 TopView 坐标生成波形值"""
        if not self._topology_profile:
            return {node.device_id: 0.5 for node in self._device_nodes}

        wave_mode = self._select_wave_mode(inp)
        try:
            values = self._wave_generator.compute(
                self._topology_profile,
                wave_mode=wave_mode,
                phase=self._global_phase,
                energy=inp.energy,
                tension=inp.tension,
                melody_pitch=inp.melody_pitch,
                melody_contour=inp.melody_contour,
            )
            return values
        except Exception:
            # 降级：简单正弦波
            result = {}
            n = len(self._device_nodes)
            for i, node in enumerate(self._device_nodes):
                t = (node.linear_phase + self._global_phase) % 1.0
                result[node.device_id] = 0.5 + 0.5 * math.sin(2 * math.pi * t)
            return result

    # ─────────────────────────────────────────────────────────────────────
    #  调制子流程
    # ─────────────────────────────────────────────────────────────────────

    def _apply_variation(
        self,
        values: Dict[str, float],
        inp: ChoreographyInput,
    ) -> Dict[str, float]:
        """
        变奏强度调制：结构性重组，非随机扰动。
        - 相位翻转：vs > 1.0 时，电机方向反转
        - 时域拉伸：vs 影响动态范围
        - 振幅膨胀：高能量 + 高 vs → 超量输出（DMX 层截断）
        """
        vs = inp.variation_strength
        if vs < 0.1:
            return values

        result = dict(values)

        # 动态范围扩展（vs 越大，偏离中心越远）
        stretch = 1.0 + (vs - 0.5) * 0.6 if vs > 0.5 else 1.0
        for ch in ("motor_x", "motor_y", "motor_z"):
            if ch in result:
                v = result[ch]
                result[ch] = 0.5 + (v - 0.5) * stretch

        # 相位翻转（vs > 1.2 时，电机 Y 轴翻转）
        if vs > 1.2 and "motor_y" in result:
            result["motor_y"] = 1.0 - result["motor_y"]

        # 泵高度超量（vs 驱动水柱超出常规范围）
        if "pump" in result and vs > 0.8:
            result["pump"] = result["pump"] * (1.0 + (vs - 0.8) * 0.4)

        return result

    def _apply_spatial_narrative(
        self,
        values: Dict[str, float],
        inp: ChoreographyInput,
        node: DeviceNode,
    ) -> Dict[str, float]:
        """
        空间叙事调制：
        - 问句 → 向外/左侧拓扑（motor_y 偏左，泵向外扩散）
        - 答句 → 向内/右侧拓扑（motor_y 偏右，泵向内收拢）
        - spatial_direction 连续控制
        """
        result = dict(values)
        spatial = inp.spatial_direction

        # 问句/答句覆盖 spatial_direction
        if inp.is_question:
            spatial = -0.6
        elif inp.is_answer:
            spatial = 0.6

        if abs(spatial) < 0.05:
            return result

        # 电机 Y 轴（左右方向）
        if "motor_y" in result:
            result["motor_y"] = result["motor_y"] + spatial * 0.2

        # 泵高度：左侧设备（norm_x < 0.5）在问句时增强
        if "pump" in result:
            if spatial < 0:  # 问句：左侧增强
                boost = (0.5 - node.norm_x) * abs(spatial) * 0.3
            else:  # 答句：右侧增强
                boost = (node.norm_x - 0.5) * abs(spatial) * 0.3
            result["pump"] = result["pump"] + boost

        return result

    def _apply_channel_coherence(
        self,
        values: Dict[str, float],
        cap: DeviceCapabilityProfile,
        node: DeviceNode,
        wave_value: float,
        inp: ChoreographyInput,
        pump_phase_offset: float,
    ) -> Dict[str, float]:
        """
        通道协同：确保泵/灯/阀门在空间上与电机对应。
        支持相位错位（高级编舞效果）。

        规则：
        1. 基本协同：泵高度 = 电机仰角的函数（同一空间位置）
        2. 相位错位：泵可以比电机晚 pump_phase_offset 拍
        3. 波形拓展：灯光颜色跟随波形值（而不是固定颜色）
        4. 错位重叠：允许不同通道类型在时序上有意识地偏移
        """
        result = dict(values)

        # 相位错位后的波形值（泵用偏移后的相位）
        if pump_phase_offset > 0.01:
            offset_phase = (self._global_phase + pump_phase_offset) % 1.0
            # H4: 帧内 wave 二次采样缓存。同一帧内所有设备共用同一 offset_phase
            # 与 wave_mode，compute 结果对全设备一致，按 round(offset_phase,3)
            # 量化缓存，整帧只算一次而非每设备各算一次。
            cache_key = round(offset_phase, 3)
            offset_waves = self._coherence_wave_cache.get(cache_key)
            if offset_waves is None:
                try:
                    offset_waves = self._wave_generator.compute(
                        self._topology_profile,
                        wave_mode=self._select_wave_mode(inp),
                        phase=offset_phase,
                        energy=inp.energy,
                        tension=inp.tension,
                        melody_pitch=inp.melody_pitch,
                        melody_contour=inp.melody_contour,
                    )
                except Exception:
                    offset_waves = None
                # 仅缓存成功结果；失败时不污染缓存，回退到 wave_value
                if offset_waves is not None:
                    self._coherence_wave_cache[cache_key] = offset_waves
            if offset_waves is not None:
                pump_wave = offset_waves.get(node.device_id, wave_value)
            else:
                pump_wave = wave_value
        else:
            pump_wave = wave_value

        # 泵高度：跟随（偏移后的）波形 + 旋律音高
        if cap.has_pump and "pump" in result:
            pump_base = result["pump"]
            # 波形调制：波形高点 → 泵高
            wave_boost = (pump_wave - 0.5) * 0.3
            result["pump"] = pump_base + wave_boost

        # 灯光颜色：跟随波形值（波形高点 → 更亮/更饱和）
        if cap.has_dimmer and "dimmer" in result:
            wave_dimmer_boost = (wave_value - 0.5) * 0.2
            result["dimmer"] = result["dimmer"] + wave_dimmer_boost

        # RGB：波形值影响亮度（不改变色相）
        if cap.has_rgb:
            wave_brightness = 0.8 + wave_value * 0.4
            for ch in ("R", "G", "B"):
                if ch in result:
                    result[ch] = result[ch] * wave_brightness

        return result

    # ─────────────────────────────────────────────────────────────────────
    #  辅助选择方法
    # ─────────────────────────────────────────────────────────────────────

    def _select_action_type(self, inp: ChoreographyInput) -> ActionAtomType:
        """根据音频参数选择最合适的动作元类型（受 section_aware / allowed_atoms 约束）"""
        energy = inp.energy
        tension = inp.tension
        phrase = inp.phrase_role
        section_aware = self._section_aware

        if section_aware and phrase in ("climax", "drop") and energy > 0.8:
            atom = ActionAtomType.BURST_CENTER if tension > 0.6 else ActionAtomType.WAVE_PEAK
        elif section_aware and phrase in ("buildup", "build"):
            atom = ActionAtomType.RIPPLE_OUT
        elif section_aware and phrase in ("break", "breakdown"):
            atom = ActionAtomType.WAVE_VALLEY
        elif inp.is_solo:
            atom = ActionAtomType.WAVE_PEAK  # Solo：波峰跟随旋律
        elif energy > 0.7 and tension > 0.5:
            atom = ActionAtomType.CROSS_ASYNC
        elif energy > 0.6:
            atom = ActionAtomType.SPIRAL_CW if self._global_phase < 0.5 else ActionAtomType.SPIRAL_CCW
        elif tension > 0.6:
            atom = ActionAtomType.BURST_SCATTER
        else:
            atom = ActionAtomType.WAVE_VALLEY

        return self._filter_allowed_atom(atom)

    def _filter_allowed_atom(self, atom: ActionAtomType) -> ActionAtomType:
        """allowed_atoms 过滤：非空时仅允许列表内动作元，否则回退到首个可用项。"""
        allowed = self._allowed_atoms
        if not allowed:
            return atom
        if atom.value in allowed or atom.name in allowed:
            return atom
        for name in allowed:
            try:
                return ActionAtomType(name)
            except ValueError:
                continue
        return ActionAtomType.IDLE

    def _apply_voice_canon_offset(self, voice_id: int) -> float:
        """
        卡农延迟：返回第 voice_id 声部应落后的节拍相位偏移 [0,1)。

        主声部（voice 0）不延迟；从声部按 canon_delay_beats 逐声部错开，
        应用到 beat_phase 驱动阀门/灯光等节拍通道，产生多声部卡农式延迟呼应。
        关闭卡农或非从声部时返回 0。
        """
        if not self._canon_enabled or voice_id <= 0:
            return 0.0
        return (voice_id * self._canon_delay_beats) % 1.0

    def _select_aggregation_topology(self, inp: ChoreographyInput) -> AggregationTopology:
        """根据音频参数选择聚合拓扑"""
        energy = inp.energy
        n = len(self._device_nodes)

        if energy > 0.8 and n >= 8:
            return AggregationTopology.CROSS_4X4
        elif energy > 0.6 and n >= 4:
            return AggregationTopology.CROSS_2X2
        elif inp.is_solo:
            return AggregationTopology.LINEAR
        else:
            return AggregationTopology.LINEAR

    def _select_wave_mode(self, inp: ChoreographyInput) -> str:
        """根据音频参数和拓扑类型选择波形模式"""
        topo = self._topology_profile
        topo_type = topo.topo_type if topo else None

        # 直流跑泉：旋律音符映射
        if topo_type and topo_type.value == "fountain":
            return "fountain_melody" if inp.is_solo else "sine"

        # 矩阵设备：2D 波形
        if topo_type and topo_type.value == "grid":
            if inp.energy > 0.8:
                return "checkerboard"
            elif inp.tension > 0.6:
                return "radial"
            elif inp.is_solo:
                return "col_wave"
            else:
                return "row_wave"

        # 圆形/弧线：径向或螺旋
        if topo_type and topo_type.value in ("circle", "arc"):
            if inp.energy > 0.7:
                return "radial"
            else:
                return "sine"

        # 默认：正弦波（能量高时用高斯波峰）
        if inp.energy > 0.75:
            return "gaussian"
        elif inp.tension > 0.6:
            return "sawtooth"
        else:
            return "sine"

    # ─────────────────────────────────────────────────────────────────────
    #  格式转换
    # ─────────────────────────────────────────────────────────────────────

    def _frame_to_raw_dict(
        self,
        frame: ChoreographyFrame,
        cap: DeviceCapabilityProfile,
    ) -> Dict[str, float]:
        """将 ChoreographyFrame 转为 raw dict（0..1 范围）"""
        raw: Dict[str, float] = {}
        if cap.has_motor_x:
            raw["motor_x"] = frame.motor_x
        if cap.has_motor_y:
            raw["motor_y"] = frame.motor_y
        if cap.has_motor_z:
            raw["motor_z"] = frame.motor_z
        if cap.has_pump:
            raw["pump"] = frame.pump
        if cap.has_valve:
            raw["valve"] = frame.valve
        if cap.has_dimmer:
            raw["dimmer"] = frame.dimmer
        if cap.has_rgb:
            raw["R"] = frame.red
            raw["G"] = frame.green
            raw["B"] = frame.blue
        if cap.has_white:
            raw["W"] = frame.white
        return raw

    def _clamp_dmx(self, values: Dict[str, float]) -> Dict[str, float]:
        """将 0..1 范围的值转换为 DMX 0..255，并截断"""
        result: Dict[str, float] = {}
        for ch, v in values.items():
            result[ch] = max(0.0, min(255.0, v * 255.0))
        return result

    def _build_zero_output(self, cap: DeviceCapabilityProfile) -> Dict[str, float]:
        """构建全零 DMX 输出（设备未激活）"""
        result: Dict[str, float] = {}
        for attr, ch in [
            ("has_motor_x", "motor_x"), ("has_motor_y", "motor_y"),
            ("has_motor_z", "motor_z"), ("has_pump", "pump"),
            ("has_valve", "valve"), ("has_dimmer", "dimmer"),
        ]:
            if getattr(cap, attr, False):
                result[ch] = 0.0
        if cap.has_rgb:
            result["R"] = result["G"] = result["B"] = 0.0
        if cap.has_white:
            result["W"] = 0.0
        return result

    # ─────────────────────────────────────────────────────────────────────
    #  状态查询接口
    # ─────────────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return self._is_initialized and len(self._device_nodes) > 0

    def get_device_count(self) -> int:
        return len(self._device_nodes)

    def get_frame_count(self) -> int:
        return self._frame_count

    def get_topology_info(self) -> str:
        if not self._topology_profile:
            return "未初始化"
        t = self._topology_profile
        return (f"拓扑类型: {t.topo_type.value}  "
                f"设备数: {len(t.nodes)}  "
                f"置信度: {t.confidence:.2f}")

    def get_capability_summary(self) -> str:
        """获取设备能力摘要"""
        n = len(self._device_nodes)
        if n == 0:
            return "无设备"
        try:
            return summarize_capabilities(list(self._cap_profiles.values()))
        except Exception:
            return f"{n} 台设备"

    def get_last_decision(self) -> Optional[ChoreographyDecision]:
        """获取上一帧的完整决策状态（用于调试/可视化）"""
        return self._last_decision

    def get_selected_presets(self) -> List[str]:
        """获取当前选中的 Effect preset ID 列表"""
        return list(self._last_presets)

    def update_bpm(self, bpm: float) -> None:
        """更新 BPM"""
        self._bpm = bpm
        self._phase_offset.update_bpm(bpm)

    def reset(self) -> None:
        """重置时间和相位（用于新曲目开始）"""
        self._current_time = 0.0
        self._global_phase = 0.0
        self._frame_count = 0
        self._preset_hold_frames = 0
        self._last_presets = []
        self._melody_ctx = None
        # H4: 清空决策/波形缓存，新曲目从干净状态重新决策
        self._cached_decision = None
        self._decision_audio_snapshot = None
        self._coherence_wave_cache = {}
        if self._aggregation_scheduler:
            self._aggregation_scheduler.release_all()
