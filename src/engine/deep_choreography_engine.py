"""
deep_choreography_engine.py — 深度AI编舞引擎核心
==================================================
四维编舞模型：
  维度1: 动作元（ActionAtom）      — 描述"怎么动"的基本运动语言
  维度2: 激活拓扑元（TopoAtom）    — 描述"谁在动"的空间激活图案
  维度3: 通道参数（ChannelParams） — 描述每个通道的具体值（泵压/电机角度/阀门开度）
  维度4: 时间节奏（RhythmGrid）    — 描述动作与BPM的对齐关系

复杂度系统：
  - 复杂度 0.0~1.0 控制同时运行的"声部"数量（1~N个并行动作层）
  - 每个声部独立拥有：动作元 + 激活拓扑元 + 节奏相位
  - 声部之间的设备分配不重叠（空间分离的多声部）

直接写入 NetworkManager 的 "effect_auto" 层（AI 自动编排专用层，与 effect 层平级）。
"""
# ⚠ FROZEN — NO UI ENTRY（2026-09-12 审计确认）
# 本模块在当前仓库无任何 UI 入口（原 src/ui/deep_choreography_panel.py 已删除）。
# 改动前请先确认/恢复入口，否则你改的是死代码；新增功能不要建在此处。
# 见 ai-memory/maintainability-audit-2026-09-12.md 易损点 5。
from __future__ import annotations

import math
import time
import random
import threading

# threadsafe 装饰器引用 — 标记共享状态访问点（perf 模块提供）
# 用法: @threadsafe("choreo_state") 应用于 self._lock 保护的方法
try:
    from ..utils.perf import threadsafe  # noqa: F401
except ImportError:
    pass  # perf 模块不可用时跳过（非关键路径）
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from .deep_music_analyzer import (
    MusicContext, PhraseRole, EmotionArc,
    get_deep_music_analyzer, DeepMusicAnalyzer,
)
try:
    from .motif_system import (
        MotifVocabulary, MotifType, InstrumentAffinity,
        CorrespondenceType, MotionCurve,
    )
    _MOTIF_VOCAB: Optional[MotifVocabulary] = None
    def _get_motif_vocab() -> MotifVocabulary:
        global _MOTIF_VOCAB
        if _MOTIF_VOCAB is None:
            _MOTIF_VOCAB = MotifVocabulary()
        return _MOTIF_VOCAB
    _MOTIF_AVAILABLE = True
except ImportError:
    _MOTIF_AVAILABLE = False
    def _get_motif_vocab():  # type: ignore
        return None

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  动作元类型
# ═══════════════════════════════════════════════════════════════════════════

class AtomType(Enum):
    # ── 基础波动类 ──
    WAVE_SINE       = "wave_sine"       # 正弦波：平滑上下
    WAVE_PULSE      = "wave_pulse"      # 脉冲波：快速开关
    WAVE_BREATH     = "wave_breath"     # 呼吸波：慢速涨落
    WAVE_SAWTOOTH   = "wave_sawtooth"   # 锯齿波：快升慢降
    WAVE_TRIANGLE   = "wave_triangle"   # 三角波：线性升降

    # ── 空间追逐类 ──
    CHASE_LINEAR    = "chase_linear"    # 线性追逐：从A到B
    CHASE_BOUNCE    = "chase_bounce"    # 弹跳追逐：A→B→A
    CHASE_SPIRAL    = "chase_spiral"    # 螺旋追逐：沿弧线旋转
    CHASE_RANDOM    = "chase_random"    # 随机跳跃追逐

    # ── 交叉对称类 ──
    CROSS_SINGLE    = "cross_single"    # 单次交叉：两组设备交叉
    CROSS_WAVE      = "cross_wave"      # 波浪交叉：多组依次交叉
    CROSS_MIRROR    = "cross_mirror"    # 镜像交叉：左右对称

    # ── 扩散聚合类 ──
    DIFFUSE_OUT     = "diffuse_out"     # 向外扩散
    DIFFUSE_IN      = "diffuse_in"      # 向内聚合
    DIFFUSE_RIPPLE  = "diffuse_ripple"  # 涟漪扩散：多圈向外

    # ── 爆发类 ──
    BURST_ALL       = "burst_all"       # 全部爆发：同时全开
    BURST_STAGGER   = "burst_stagger"   # 交错爆发：依次快速开
    BURST_RANDOM    = "burst_random"    # 随机爆发：随机位置快速开

    # ── 扫描类 ──
    SCAN_LINEAR     = "scan_linear"     # 线性扫描：从一端到另一端
    SCAN_ROTATE     = "scan_rotate"     # 旋转扫描：绕中心旋转
    SCAN_PING_PONG  = "scan_ping_pong"  # 乒乓扫描：来回扫描

    # ── 形状类 ──
    SHAPE_ARC       = "shape_arc"       # 弧形：激活弧线段
    SHAPE_RING      = "shape_ring"      # 环形：激活外环
    SHAPE_CENTER    = "shape_center"    # 中心：激活中心区域
    SHAPE_HALF      = "shape_half"      # 半场：激活左/右半

    # ── 静止类 ──
    HOLD_ALL        = "hold_all"        # 保持全开
    HOLD_PATTERN    = "hold_pattern"    # 保持图案
    SILENCE         = "silence"         # 全部关闭（静默）

# ═══════════════════════════════════════════════════════════════════════════
#  激活拓扑元类型
# ═══════════════════════════════════════════════════════════════════════════

class TopoAtomType(Enum):
    """空间激活图案的基本类型"""
    FULL            = "full"            # 全部激活
    ARC_SEGMENT     = "arc_segment"     # 弧线段（连续一段）
    ALTERNATE       = "alternate"       # 交替（奇偶）
    OUTER_RING      = "outer_ring"      # 外环
    INNER_CORE      = "inner_core"      # 内核
    LEFT_HALF       = "left_half"       # 左半
    RIGHT_HALF      = "right_half"      # 右半
    FRONT_HALF      = "front_half"      # 前半
    BACK_HALF       = "back_half"       # 后半
    DIAGONAL_A      = "diagonal_a"      # 对角线A
    DIAGONAL_B      = "diagonal_b"      # 对角线B
    SPARSE_RANDOM   = "sparse_random"   # 稀疏随机
    DENSE_RANDOM    = "dense_random"    # 密集随机
    SINGLE_POINT    = "single_point"    # 单点
    CLUSTER         = "cluster"         # 聚簇（空间相邻的一组）

# ═══════════════════════════════════════════════════════════════════════════
#  设备通道描述
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FixtureChannel:
    """一个设备的一个通道"""
    fixture_id: str
    fixture_name: str
    universe: int
    dmx_addr: int           # 0-indexed
    ch_type: str            # ChannelType.value
    pos_x: float = 0.0
    pos_y: float = 0.0
    fixture_index: int = 0  # 在设备组中的索引

@dataclass
class FixtureChannelSet:
    """一组设备的所有通道，按类型分组"""
    pump_channels: List[FixtureChannel] = field(default_factory=list)
    valve_channels: List[FixtureChannel] = field(default_factory=list)
    motor_x_channels: List[FixtureChannel] = field(default_factory=list)
    motor_y_channels: List[FixtureChannel] = field(default_factory=list)
    motor_z_channels: List[FixtureChannel] = field(default_factory=list)
    dimmer_channels: List[FixtureChannel] = field(default_factory=list)
    all_fixtures: List[dict] = field(default_factory=list)  # 原始fixture数据

    @property
    def fixture_count(self) -> int:
        return len(self.all_fixtures)

    @property
    def has_pumps(self) -> bool:
        return len(self.pump_channels) > 0

    @property
    def has_motors(self) -> bool:
        return len(self.motor_x_channels) > 0 or len(self.motor_y_channels) > 0

    @property
    def has_valves(self) -> bool:
        return len(self.valve_channels) > 0

    @property
    def has_dimmers(self) -> bool:
        return len(self.dimmer_channels) > 0

# ═══════════════════════════════════════════════════════════════════════════
#  空间拓扑分析器
# ═══════════════════════════════════════════════════════════════════════════

class SpatialTopologyAnalyzer:
    """
    根据设备的空间坐标（pos_x, pos_y）自动分析拓扑结构，
    生成各种激活图案（TopoAtom）对应的设备索引集合。
    """

    def __init__(self, fixtures: List[dict]):
        self._fixtures = fixtures
        self._positions = self._extract_positions(fixtures)
        self._n = len(fixtures)
        self._topo_cache: Dict[str, List[int]] = {}
        self._arc_segments: List[List[int]] = []
        self._center_x = 0.5
        self._center_y = 0.5
        self._analyze()

    def _extract_positions(self, fixtures: List[dict]) -> List[Tuple[float, float]]:
        positions = []
        for f in fixtures:
            x = float(f.get('pos_x', 0.0))
            y = float(f.get('pos_y', 0.0))
            positions.append((x, y))
        return positions

    def _analyze(self):
        if not self._positions:
            return

        xs = [p[0] for p in self._positions]
        ys = [p[1] for p in self._positions]
        self._center_x = float(np.mean(xs))
        self._center_y = float(np.mean(ys))

        # 计算每个设备到中心的距离和角度
        self._distances = []
        self._angles = []
        for x, y in self._positions:
            dx = x - self._center_x
            dy = y - self._center_y
            dist = math.sqrt(dx * dx + dy * dy)
            angle = math.atan2(dy, dx)
            self._distances.append(dist)
            self._angles.append(angle)

        # 检测弧线段
        self._arc_segments = self._detect_arc_segments()

    def _detect_arc_segments(self) -> List[List[int]]:
        """
        检测设备中的弧线段。
        通过对设备按角度排序，找到连续的弧线段。
        """
        if self._n < 3:
            return [list(range(self._n))]

        # 按角度排序
        sorted_by_angle = sorted(range(self._n), key=lambda i: self._angles[i])

        # 检测是否形成完整的圆形（首尾相接）
        max_dist = max(self._distances) if self._distances else 0
        min_dist = min(self._distances) if self._distances else 0
        dist_range = max_dist - min_dist

        # 如果距离变化不大，认为是圆形排列
        if dist_range < max_dist * 0.3 and max_dist > 0:
            # 圆形：整体是一个弧线段
            return [sorted_by_angle]

        # 否则尝试按空间聚类分割弧线段
        segments = []
        current_segment = [sorted_by_angle[0]]
        for i in range(1, len(sorted_by_angle)):
            prev_idx = sorted_by_angle[i - 1]
            curr_idx = sorted_by_angle[i]
            # 角度差
            angle_gap = abs(self._angles[curr_idx] - self._angles[prev_idx])
            if angle_gap > math.pi * 0.4:  # 超过72度认为是新段
                if len(current_segment) >= 2:
                    segments.append(current_segment)
                current_segment = [curr_idx]
            else:
                current_segment.append(curr_idx)
        if current_segment:
            segments.append(current_segment)

        return segments if segments else [list(range(self._n))]

    def get_active_indices(
        self,
        topo_type: TopoAtomType,
        density: float = 0.5,
        phase: float = 0.0,
        rng: Optional[random.Random] = None,
    ) -> List[int]:
        """
        根据拓扑类型和参数，返回应该激活的设备索引列表。
        
        density: 0..1，激活比例
        phase:   0..1，相位（用于追逐/扫描类）
        rng:     随机数生成器（用于随机类）
        """
        if self._n == 0:
            return []
        if rng is None:
            rng = random.Random(42)

        n_active = max(1, int(self._n * density))

        if topo_type == TopoAtomType.FULL:
            return list(range(self._n))

        elif topo_type == TopoAtomType.ARC_SEGMENT:
            # 从弧线段中选取连续的一段
            if not self._arc_segments:
                return list(range(n_active))
            # 选择最长的弧线段
            longest_seg = max(self._arc_segments, key=len)
            # 根据phase选择起始位置
            seg_len = len(longest_seg)
            start = int(phase * seg_len) % seg_len
            count = max(1, int(seg_len * density))
            indices = []
            for i in range(count):
                indices.append(longest_seg[(start + i) % seg_len])
            return indices

        elif topo_type == TopoAtomType.ALTERNATE:
            # 奇偶交替
            offset = int(phase * 2) % 2
            return [i for i in range(self._n) if i % 2 == offset]

        elif topo_type == TopoAtomType.OUTER_RING:
            # 外环：距离中心最远的设备
            sorted_by_dist = sorted(range(self._n), key=lambda i: -self._distances[i])
            return sorted_by_dist[:n_active]

        elif topo_type == TopoAtomType.INNER_CORE:
            # 内核：距离中心最近的设备
            sorted_by_dist = sorted(range(self._n), key=lambda i: self._distances[i])
            return sorted_by_dist[:n_active]

        elif topo_type == TopoAtomType.LEFT_HALF:
            xs = [p[0] for p in self._positions]
            median_x = float(np.median(xs))
            return [i for i in range(self._n) if self._positions[i][0] <= median_x]

        elif topo_type == TopoAtomType.RIGHT_HALF:
            xs = [p[0] for p in self._positions]
            median_x = float(np.median(xs))
            return [i for i in range(self._n) if self._positions[i][0] > median_x]

        elif topo_type == TopoAtomType.FRONT_HALF:
            ys = [p[1] for p in self._positions]
            median_y = float(np.median(ys))
            return [i for i in range(self._n) if self._positions[i][1] <= median_y]

        elif topo_type == TopoAtomType.BACK_HALF:
            ys = [p[1] for p in self._positions]
            median_y = float(np.median(ys))
            return [i for i in range(self._n) if self._positions[i][1] > median_y]

        elif topo_type == TopoAtomType.DIAGONAL_A:
            # 对角线A：左上到右下
            sorted_diag = sorted(range(self._n),
                                  key=lambda i: self._positions[i][0] + self._positions[i][1])
            return sorted_diag[:n_active]

        elif topo_type == TopoAtomType.DIAGONAL_B:
            # 对角线B：右上到左下
            sorted_diag = sorted(range(self._n),
                                  key=lambda i: self._positions[i][0] - self._positions[i][1])
            return sorted_diag[:n_active]

        elif topo_type == TopoAtomType.SPARSE_RANDOM:
            # 稀疏随机：随机选取少量设备
            n = max(1, int(self._n * min(density, 0.4)))
            return rng.sample(range(self._n), min(n, self._n))

        elif topo_type == TopoAtomType.DENSE_RANDOM:
            # 密集随机：随机选取大量设备
            n = max(1, int(self._n * max(density, 0.6)))
            return rng.sample(range(self._n), min(n, self._n))

        elif topo_type == TopoAtomType.SINGLE_POINT:
            # 单点：根据phase选择一个设备
            idx = int(phase * self._n) % self._n
            return [idx]

        elif topo_type == TopoAtomType.CLUSTER:
            # 聚簇：选择空间上相邻的一组设备
            center_idx = int(phase * self._n) % self._n
            cx, cy = self._positions[center_idx]
            # 按距离排序，选最近的n_active个
            dists = [math.sqrt((p[0]-cx)**2 + (p[1]-cy)**2) for p in self._positions]
            sorted_by_dist = sorted(range(self._n), key=lambda i: dists[i])
            return sorted_by_dist[:n_active]

        return list(range(min(n_active, self._n)))

    def get_arc_phase(self, fixture_idx: int) -> float:
        """获取设备在弧线上的相位（0..1）"""
        if self._n == 0:
            return 0.0
        # 按角度归一化
        angle = self._angles[fixture_idx]
        # 归一化到0..1
        return (angle + math.pi) / (2 * math.pi)

    def get_radial_phase(self, fixture_idx: int) -> float:
        """获取设备的径向相位（0=中心，1=外圈）"""
        if not self._distances:
            return 0.5
        max_d = max(self._distances)
        if max_d == 0:
            return 0.0
        return self._distances[fixture_idx] / max_d

    def get_spatial_weight(self, fixture_idx: int, focus_x: float = 0.5, focus_y: float = 0.5) -> float:
        """获取设备相对于焦点的空间权重（越近权重越高）"""
        if fixture_idx >= len(self._positions):
            return 1.0
        x, y = self._positions[fixture_idx]
        dist = math.sqrt((x - focus_x)**2 + (y - focus_y)**2)
        return float(math.exp(-dist * 3.0))

# ═══════════════════════════════════════════════════════════════════════════
#  声部（Voice）— 一个并行运行的编舞层
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Voice:
    """
    一个编舞声部。
    复杂度决定同时运行的声部数量。
    每个声部独立拥有：动作元 + 激活拓扑元 + 节奏相位 + 设备子集
    """
    voice_id: int = 0
    atom_type: AtomType = AtomType.WAVE_SINE
    topo_type: TopoAtomType = TopoAtomType.ARC_SEGMENT

    # 当前状态
    phase: float = 0.0          # 动作相位 0..1
    topo_phase: float = 0.0     # 拓扑相位 0..1
    speed: float = 1.0          # 速度乘数
    amplitude: float = 1.0      # 幅度 0..1
    density: float = 0.5        # 设备激活密度 0..1

    # 时间追踪
    start_t: float = 0.0
    age: float = 0.0
    duration: float = 4.0       # 预期持续时长（秒）

    # 过渡状态
    is_transitioning: bool = False
    transition_progress: float = 0.0
    transition_duration: float = 0.5
    next_atom_type: Optional[AtomType] = None
    next_topo_type: Optional[TopoAtomType] = None

    # 变奏参数（由变奏引擎填充）
    variation_offset: float = 0.0       # 相位偏移（变奏用）
    variation_scale: float = 1.0        # 幅度缩放
    variation_reverse: bool = False     # 方向反转
    ornament_beats: List[float] = field(default_factory=list)  # 加花节拍位置

    # 当前激活的设备索引
    active_fixture_indices: List[int] = field(default_factory=list)

    # 输出缓冲（fixture_index -> channel_type -> value 0..255）
    output: Dict[int, Dict[str, float]] = field(default_factory=dict)

    # ── 增强1：motif语义字段 ──
    motif_type_name: str = ""           # 对应的MotifType.value（空=未绑定）
    motif_curve_val: float = 0.5        # motif词汇库计算出的当前曲线值（0..1）

    # ── 增强2：问答空间映射字段 ──
    spatial_role: str = "neutral"       # "question"=左半场/向外 "answer"=右半场/向内 "neutral"=全场

    # ── 增强5：节拍量化字段 ──
    quantize_pending: bool = False      # 是否等待下一个节拍边界才切换
    quantize_next_atom: Optional[AtomType] = None   # 等待量化的下一个动作元
    quantize_next_topo: Optional[TopoAtomType] = None

    # ── 增强6：声部呼应字段 ──
    is_follower: bool = False           # 是否是跟随声部（从声部）
    leader_voice_id: int = -1           # 主声部ID（-1=无主声部）
    canon_delay: float = 0.0            # 卡农延迟（秒）
    canon_phase_offset: float = 0.0     # 相位延迟（已转换为相位偏移）

    def get_effective_phase(self) -> float:
        """获取考虑变奏的有效相位"""
        p = self.phase + self.variation_offset
        if self.variation_reverse:
            p = 1.0 - p
        return p % 1.0

# ═══════════════════════════════════════════════════════════════════════════
#  DMX值生成器 — 将动作元+拓扑元转换为实际DMX值
# ═══════════════════════════════════════════════════════════════════════════

class DMXGenerator:
    """
    核心DMX值生成器。
    输入：Voice状态 + 设备通道集合 + 拓扑分析器
    输出：{fixture_index: {ch_type: dmx_value(0..255)}}
    """

    def __init__(self, channel_set: FixtureChannelSet, topology: SpatialTopologyAnalyzer):
        self._ch_set = channel_set
        self._topo = topology
        self._n = channel_set.fixture_count

    def generate(self, voice: Voice, t: float, audio_rms: float = 0.5,
                 audio_bands: Optional[List[float]] = None) -> Dict[int, Dict[str, float]]:
        """
        为一个声部生成所有设备的DMX值。
        返回 {fixture_index: {ch_type: value(0..255)}}
        """
        if self._n == 0:
            return {}

        if audio_bands is None:
            audio_bands = [audio_rms] * 8

        output: Dict[int, Dict[str, float]] = {}

        # 先生成所有设备的"基础激活值"（0..1）
        activation_values = self._compute_activation(voice, t, audio_rms, audio_bands)

        # 对每个激活设备，生成各通道的DMX值
        for fixture_idx in voice.active_fixture_indices:
            if fixture_idx >= self._n:
                continue
            activation = activation_values.get(fixture_idx, 0.0)
            output[fixture_idx] = self._compute_channels(
                fixture_idx, activation, voice, t, audio_rms, audio_bands
            )

        # 非激活设备输出0
        for i in range(self._n):
            if i not in output:
                output[i] = self._zero_channels(i)

        return output

    def _compute_activation(
        self, voice: Voice, t: float,
        rms: float, bands: List[float]
    ) -> Dict[int, float]:
        """计算每个设备的激活值（0..1）"""
        result: Dict[int, float] = {}
        phase = voice.get_effective_phase()
        amp = voice.amplitude * voice.variation_scale

        # 音频调制：低频影响幅度
        bass_energy = float(np.mean(bands[:2])) if len(bands) >= 2 else rms
        audio_mod = 0.7 + 0.3 * bass_energy

        atom = voice.atom_type

        for idx in voice.active_fixture_indices:
            arc_phase = self._topo.get_arc_phase(idx)
            radial_phase = self._topo.get_radial_phase(idx)

            val = self._compute_atom_value(
                atom, phase, arc_phase, radial_phase,
                voice.topo_phase, amp, rms, bands
            )

            # 过渡混合
            if voice.is_transitioning and voice.next_atom_type is not None:
                next_val = self._compute_atom_value(
                    voice.next_atom_type, phase, arc_phase, radial_phase,
                    voice.topo_phase, amp, rms, bands
                )
                tp = voice.transition_progress
                # 有机过渡：不是简单线性混合，而是基于空间位置的渐变
                spatial_weight = self._topo.get_spatial_weight(idx)
                blend_t = self._organic_blend(tp, spatial_weight)
                val = val * (1.0 - blend_t) + next_val * blend_t

            # 加花：在特定节拍位置插入短促脉冲
            beat_phase = (t * voice.speed) % 1.0
            for ornament_beat in voice.ornament_beats:
                dist = abs(beat_phase - ornament_beat)
                if dist < 0.05:
                    val = min(1.0, val + 0.3 * (1.0 - dist / 0.05))

            result[idx] = float(np.clip(val * audio_mod, 0.0, 1.0))

        return result

    def _compute_atom_value(
        self, atom: AtomType, phase: float, arc_phase: float,
        radial_phase: float, topo_phase: float,
        amp: float, rms: float, bands: List[float]
    ) -> float:
        """计算单个设备在特定动作元下的激活值"""

        # ── 波动类 ──
        if atom == AtomType.WAVE_SINE:
            # 正弦波，每个设备有arc_phase偏移（形成波浪）
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * (phase - arc_phase * 0.5)))

        elif atom == AtomType.WAVE_PULSE:
            # 脉冲波：快速开关
            p = (phase - arc_phase * 0.3) % 1.0
            return amp if p < 0.3 else 0.0

        elif atom == AtomType.WAVE_BREATH:
            # 呼吸波：慢速涨落，更平滑
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * phase - math.pi / 2))

        elif atom == AtomType.WAVE_SAWTOOTH:
            # 锯齿波：快升慢降
            p = (phase - arc_phase * 0.2) % 1.0
            if p < 0.2:
                return amp * p / 0.2
            else:
                return amp * (1.0 - (p - 0.2) / 0.8)

        elif atom == AtomType.WAVE_TRIANGLE:
            # 三角波：线性升降
            p = (phase - arc_phase * 0.3) % 1.0
            return amp * (1.0 - abs(2 * p - 1.0))

        # ── 追逐类 ──
        elif atom == AtomType.CHASE_LINEAR:
            # 线性追逐：激活点从arc_phase=0移动到1
            dist = abs(arc_phase - phase)
            dist = min(dist, 1.0 - dist)
            return amp * max(0.0, 1.0 - dist * 8.0)

        elif atom == AtomType.CHASE_BOUNCE:
            # 弹跳追逐：来回
            p = phase % 1.0
            effective_phase = p if p < 0.5 else 1.0 - p
            dist = abs(arc_phase - effective_phase * 2)
            return amp * max(0.0, 1.0 - dist * 8.0)

        elif atom == AtomType.CHASE_SPIRAL:
            # 螺旋追逐：弧线相位 + 径向相位组合
            combined = (arc_phase * 0.7 + radial_phase * 0.3)
            dist = abs(combined - phase)
            dist = min(dist, 1.0 - dist)
            return amp * max(0.0, 1.0 - dist * 6.0)

        elif atom == AtomType.CHASE_RANDOM:
            # 随机跳跃：基于相位的伪随机
            seed = int(phase * 8) + int(arc_phase * 100)
            rng_val = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
            return amp if (rng_val % 3 == 0) else 0.0

        # ── 交叉类 ──
        elif atom == AtomType.CROSS_SINGLE:
            # 单次交叉：两组设备交叉
            group_a = arc_phase < 0.5
            cross_phase = phase % 1.0
            if group_a:
                return amp * (1.0 if cross_phase < 0.5 else 0.0)
            else:
                return amp * (0.0 if cross_phase < 0.5 else 1.0)

        elif atom == AtomType.CROSS_WAVE:
            # 波浪交叉：多组依次交叉
            offset = arc_phase * 3
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * (phase - offset / 3.0)))

        elif atom == AtomType.CROSS_MIRROR:
            # 镜像交叉：左右对称
            mirror_phase = arc_phase if arc_phase < 0.5 else 1.0 - arc_phase
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * (phase - mirror_phase)))

        # ── 扩散类 ──
        elif atom == AtomType.DIFFUSE_OUT:
            # 向外扩散：从中心向外
            dist = abs(radial_phase - phase)
            return amp * max(0.0, 1.0 - dist * 4.0)

        elif atom == AtomType.DIFFUSE_IN:
            # 向内聚合：从外向内
            dist = abs((1.0 - radial_phase) - phase)
            return amp * max(0.0, 1.0 - dist * 4.0)

        elif atom == AtomType.DIFFUSE_RIPPLE:
            # 涟漪扩散：多圈向外
            ripple = (radial_phase - phase * 2) % 1.0
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * ripple))

        # ── 爆发类 ──
        elif atom == AtomType.BURST_ALL:
            # 全部爆发：同时全开，快速衰减
            return amp * max(0.0, 1.0 - phase * 3.0)

        elif atom == AtomType.BURST_STAGGER:
            # 交错爆发：依次快速开
            delay = arc_phase * 0.3
            p = max(0.0, phase - delay)
            return amp * max(0.0, 1.0 - p * 4.0)

        elif atom == AtomType.BURST_RANDOM:
            # 随机爆发
            seed = int(arc_phase * 100) + int(phase * 10)
            rng_val = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
            return amp * max(0.0, 1.0 - phase * 3.0) if (rng_val % 4 < 2) else 0.0

        # ── 扫描类 ──
        elif atom == AtomType.SCAN_LINEAR:
            # 线性扫描
            dist = abs(arc_phase - phase)
            return amp * max(0.0, 1.0 - dist * 10.0)

        elif atom == AtomType.SCAN_ROTATE:
            # 旋转扫描
            angle_phase = (arc_phase - phase) % 1.0
            return amp * max(0.0, 1.0 - angle_phase * 8.0)

        elif atom == AtomType.SCAN_PING_PONG:
            # 乒乓扫描
            p = phase % 1.0
            effective = p if p < 0.5 else 1.0 - p
            dist = abs(arc_phase - effective * 2)
            return amp * max(0.0, 1.0 - dist * 10.0)

        # ── 形状类 ──
        elif atom == AtomType.SHAPE_ARC:
            # 弧形：激活弧线段，整体呼吸
            return amp * 0.5 * (1.0 + math.sin(2 * math.pi * phase))

        elif atom == AtomType.SHAPE_RING:
            # 环形：外环整体呼吸
            ring_val = 1.0 if radial_phase > 0.6 else 0.0
            return amp * ring_val * 0.5 * (1.0 + math.sin(2 * math.pi * phase))

        elif atom == AtomType.SHAPE_CENTER:
            # 中心：内核整体呼吸
            center_val = 1.0 if radial_phase < 0.4 else 0.0
            return amp * center_val * 0.5 * (1.0 + math.sin(2 * math.pi * phase))

        elif atom == AtomType.SHAPE_HALF:
            # 半场：左/右半交替
            half_val = 1.0 if (arc_phase < 0.5) == (phase < 0.5) else 0.0
            return amp * half_val

        # ── 静止类 ──
        elif atom == AtomType.HOLD_ALL:
            return amp

        elif atom == AtomType.HOLD_PATTERN:
            return amp * (1.0 if arc_phase < 0.5 else 0.5)

        elif atom == AtomType.SILENCE:
            return 0.0

        return amp * 0.5

    def _organic_blend(self, progress: float, spatial_weight: float) -> float:
        """
        有机过渡混合函数。
        不是简单的线性插值，而是基于空间位置的渐变：
        空间权重高的设备先完成过渡，形成"波浪式"的动作交接。
        """
        # 空间权重影响过渡速度：权重高的设备过渡更快
        adjusted_progress = progress + spatial_weight * 0.2
        adjusted_progress = float(np.clip(adjusted_progress, 0.0, 1.0))
        # 使用平滑的S曲线
        t = adjusted_progress
        return t * t * (3.0 - 2.0 * t)

    def _compute_pump_value(
        self, atom: AtomType, activation: float,
        phase: float, arc_phase: float,
        rms: float, bands: List[float]
    ) -> float:
        """增强3：泵通道专属曲线。
        泵压不是简单的开关，而是根据动作语义具有不同的压力曲线。
        """
        bass = float(np.mean(bands[:2])) if len(bands) >= 2 else rms

        if atom == AtomType.BURST_STAGGER:
            # 交错爆发：依次延迟的脉冲序列（核心增强）
            # 每个设备按arc_phase延追，形成波浪式爆发
            delay = arc_phase * 0.4  # 0..0.4秒的延迟展开
            local_phase = (phase - delay) % 1.0
            # 快起慢衰的脉冲包络
            if local_phase < 0.15:
                # 上升段：快速上升
                pump_val = local_phase / 0.15
            elif local_phase < 0.45:
                # 保持段：高压保持
                pump_val = 1.0 - (local_phase - 0.15) / 0.30 * 0.3  # 轻微下降
            else:
                # 衰减段：缓慢衰减
                pump_val = 0.7 * max(0.0, 1.0 - (local_phase - 0.45) / 0.55)
            return float(np.clip(pump_val * activation * (0.8 + 0.2 * bass), 0.0, 1.0)) * 255.0

        elif atom == AtomType.BURST_ALL:
            # 全部爆发：同相瞬间全开，然后快速衰减
            if phase < 0.08:
                pump_val = phase / 0.08  # 极快上升
            else:
                pump_val = max(0.0, 1.0 - (phase - 0.08) / 0.92 * 1.5)
            return float(np.clip(pump_val * (0.9 + 0.1 * bass), 0.0, 1.0)) * 255.0

        elif atom == AtomType.WAVE_BREATH:
            # 呼吸波：深呼吸曲线（慢起慢落，顶部有停顿）
            # 使用非对称的正弦曲线模拟呼吸
            breath = 0.5 * (1.0 + math.sin(2 * math.pi * phase - math.pi / 2))
            # 顶部平坦化（模拟呼吸顶部的停顿）
            breath = min(1.0, breath * 1.2)
            return float(np.clip(breath * activation, 0.0, 1.0)) * 255.0

        elif atom == AtomType.WAVE_SAWTOOTH:
            # 锅齿波：快升慢降的泵压曲线
            p = (phase - arc_phase * 0.15) % 1.0
            if p < 0.12:
                pump_val = p / 0.12  # 快速上升
            else:
                pump_val = 1.0 - (p - 0.12) / 0.88  # 缓慢下降
            return float(np.clip(pump_val * activation, 0.0, 1.0)) * 255.0

        elif atom in (AtomType.CHASE_LINEAR, AtomType.CHASE_SPIRAL):
            # 追逐：泵压跟随激活点移动，在激活点附近形成高压尖峰
            # 已经由activation表达了激活强度，这里加入泵压响应延迟
            attack = min(1.0, activation * 1.5)  # 快速响应
            return float(np.clip(attack, 0.0, 1.0)) * 255.0

        elif atom == AtomType.DIFFUSE_RIPPLE:
            # 涥漪扩散：多圈波纹，泵压随径向相位起伏
            ripple_phase = (arc_phase * 2 - phase * 3) % 1.0
            ripple = 0.5 * (1.0 + math.sin(2 * math.pi * ripple_phase))
            return float(np.clip(ripple * activation * (0.7 + 0.3 * bass), 0.0, 1.0)) * 255.0

        elif atom in (AtomType.HOLD_ALL, AtomType.HOLD_PATTERN):
            # 保持：泵压平稳，轻微呼吸感
            hold_breath = 0.9 + 0.1 * math.sin(2 * math.pi * phase * 0.25)
            return float(np.clip(hold_breath * activation, 0.0, 1.0)) * 255.0

        elif atom == AtomType.SILENCE:
            return 0.0

        # 默认：激活值直接映射
        return float(np.clip(activation, 0.0, 1.0)) * 255.0

    def _compute_valve_value(
        self, atom: AtomType, activation: float,
        phase: float, arc_phase: float,
        rms: float, bands: List[float]
    ) -> float:
        """增强3：阀门通道专属曲线。
        阀门是开关式元件，但不同动作元对应不同的开关时序和开度。
        """
        treble = float(np.mean(bands[5:])) if len(bands) >= 6 else rms

        if atom == AtomType.BURST_STAGGER:
            # 交错爆发：阀门依次延迟打开，形成逐步开放的视觉效果
            delay = arc_phase * 0.35
            local_phase = (phase - delay) % 1.0
            # 阀门：开关更快、关闭更快（脉冲感）
            if local_phase < 0.2:
                valve_val = local_phase / 0.2
            elif local_phase < 0.5:
                valve_val = 1.0
            else:
                valve_val = max(0.0, 1.0 - (local_phase - 0.5) / 0.5 * 2)
            return float(np.clip(valve_val * activation, 0.0, 1.0)) * 255.0

        elif atom == AtomType.BURST_ALL:
            # 全部爆发：阀门同时全开，瞬时关闭
            valve_val = 1.0 if phase < 0.4 else max(0.0, 1.0 - (phase - 0.4) / 0.6 * 3)
            return float(np.clip(valve_val, 0.0, 1.0)) * 255.0

        elif atom == AtomType.WAVE_PULSE:
            # 脉冲波：阀门快速开关（尖锐的方波）
            p = (phase - arc_phase * 0.2) % 1.0
            valve_val = 1.0 if p < 0.25 else 0.0
            return float(np.clip(valve_val * activation, 0.0, 1.0)) * 255.0

        elif atom in (AtomType.DIFFUSE_OUT, AtomType.DIFFUSE_IN):
            # 扩散/收拢：阀门按径向顺序依次开关
            radial = self._topo.get_radial_phase(0)  # 近似
            if atom == AtomType.DIFFUSE_OUT:
                open_t = radial  # 从内到外依次开放
            else:
                open_t = 1.0 - radial  # 从外到内依次开放
            valve_val = 1.0 if phase > open_t else activation
            return float(np.clip(valve_val, 0.0, 1.0)) * 255.0

        elif atom == AtomType.WAVE_BREATH:
            # 呼吸波：阀门全开，由泵压控制流量
            return float(np.clip(activation, 0.0, 1.0)) * 255.0

        elif atom == AtomType.SILENCE:
            return 0.0

        # 默认：激活值直接映射
        return float(np.clip(activation, 0.0, 1.0)) * 255.0

    def _compute_channels(
        self, fixture_idx: int, activation: float,
        voice: Voice, t: float,
        rms: float, bands: List[float]
    ) -> Dict[str, float]:
        """增强3：将激活值转换为各通道的DMX值（泵/阀/电机各有专属曲线）"""
        channels: Dict[str, float] = {}
        atom = voice.atom_type
        phase = voice.get_effective_phase()
        arc_phase = self._topo.get_arc_phase(fixture_idx)

        # 增强3：泵通道——专属泵压曲线
        for ch in self._ch_set.pump_channels:
            if ch.fixture_index == fixture_idx:
                channels[ch.ch_type] = self._compute_pump_value(
                    atom, activation, phase, arc_phase, rms, bands
                )

        # 增强3：阀门通道——专属阀门开关时序曲线
        for ch in self._ch_set.valve_channels:
            if ch.fixture_index == fixture_idx:
                channels[ch.ch_type] = self._compute_valve_value(
                    atom, activation, phase, arc_phase, rms, bands
                )

        # 调光通道：激活值映射到亮度
        for ch in self._ch_set.dimmer_channels:
            if ch.fixture_index == fixture_idx:
                channels[ch.ch_type] = activation * 255.0

        # 电机X通道：根据动作元生成运动轨迹
        for ch in self._ch_set.motor_x_channels:
            if ch.fixture_index == fixture_idx:
                motor_val = self._compute_motor_value(
                    atom, 'x', phase, arc_phase,
                    activation, rms, bands
                )
                channels[ch.ch_type] = motor_val

        # 电机Y通道
        for ch in self._ch_set.motor_y_channels:
            if ch.fixture_index == fixture_idx:
                motor_val = self._compute_motor_value(
                    atom, 'y', phase, arc_phase,
                    activation, rms, bands
                )
                channels[ch.ch_type] = motor_val

        # 电机Z通道
        for ch in self._ch_set.motor_z_channels:
            if ch.fixture_index == fixture_idx:
                motor_val = self._compute_motor_value(
                    atom, 'z', phase, arc_phase,
                    activation, rms, bands
                )
                channels[ch.ch_type] = motor_val

        return channels

    def _compute_motor_value(
        self, atom: AtomType, axis: str, phase: float,
        arc_phase: float, activation: float,
        rms: float, bands: List[float]
    ) -> float:
        """
        计算电机通道值（0..255，128=中位）。
        电机不是简单的开关，而是有方向和速度的运动。
        """
        center = 128.0
        range_val = 100.0  # 运动范围（±100）

        if atom in (AtomType.WAVE_SINE, AtomType.WAVE_BREATH):
            # 正弦运动
            offset = arc_phase * 0.5
            if axis == 'x':
                return center + range_val * math.sin(2 * math.pi * (phase - offset))
            elif axis == 'y':
                return center + range_val * math.cos(2 * math.pi * (phase - offset))
            else:
                return center + range_val * 0.5 * math.sin(4 * math.pi * phase)

        elif atom in (AtomType.CHASE_LINEAR, AtomType.SCAN_LINEAR):
            # 线性扫描运动
            if axis == 'x':
                return center + range_val * (2 * phase - 1.0)
            elif axis == 'y':
                return center + range_val * arc_phase * math.sin(2 * math.pi * phase)
            else:
                return center

        elif atom in (AtomType.CROSS_SINGLE, AtomType.CROSS_MIRROR):
            # 交叉运动：X轴来回
            if axis == 'x':
                return center + range_val * math.sin(2 * math.pi * phase) * (1.0 if arc_phase < 0.5 else -1.0)
            elif axis == 'y':
                return center + range_val * 0.5 * math.cos(2 * math.pi * phase)
            else:
                return center

        elif atom in (AtomType.DIFFUSE_OUT, AtomType.DIFFUSE_RIPPLE):
            # 扩散运动：向外展开
            radial = self._topo.get_radial_phase(0)  # 近似
            if axis == 'x':
                angle = arc_phase * 2 * math.pi
                return center + range_val * phase * math.cos(angle)
            elif axis == 'y':
                angle = arc_phase * 2 * math.pi
                return center + range_val * phase * math.sin(angle)
            else:
                return center + range_val * phase

        elif atom in (AtomType.BURST_ALL, AtomType.BURST_STAGGER):
            # 爆发：快速扩展
            burst_val = max(0.0, 1.0 - phase * 2)
            if axis == 'x':
                return center + range_val * burst_val * math.cos(arc_phase * 2 * math.pi)
            elif axis == 'y':
                return center + range_val * burst_val * math.sin(arc_phase * 2 * math.pi)
            else:
                return center + range_val * burst_val

        # 默认：中位
        return center

    def _zero_channels(self, fixture_idx: int) -> Dict[str, float]:
        """返回设备的全零通道值"""
        channels: Dict[str, float] = {}
        for ch_list in [
            self._ch_set.pump_channels, self._ch_set.valve_channels,
            self._ch_set.dimmer_channels, self._ch_set.motor_x_channels,
            self._ch_set.motor_y_channels, self._ch_set.motor_z_channels,
        ]:
            for ch in ch_list:
                if ch.fixture_index == fixture_idx:
                    if ch.ch_type.startswith('motor'):
                        channels[ch.ch_type] = 128.0  # 电机中位
                    else:
                        channels[ch.ch_type] = 0.0
        return channels

# ═══════════════════════════════════════════════════════════════════════════
#  变奏引擎 — 对动作元进行实时改编
# ═══════════════════════════════════════════════════════════════════════════

class VariationEngine:
    """
    对Voice进行实时变奏。
    变奏不改变动作元的类型，而是在其内部做改编：
    - 节奏变奏：速度缩放、相位偏移、节拍对齐
    - 空间变奏：镜像翻转、方向反转
    - 动态变奏：幅度调制、密度变化
    - 装饰变奏：加花（在特定节拍位置插入短促脉冲）
    """

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random(int(time.time()))

    def apply_variation(
        self,
        voice: Voice,
        music_ctx: MusicContext,
        audio_rms: float,
        bpm: float,
        variation_strength: float = 0.5,
    ) -> None:
        """
        根据音乐上下文对声部进行变奏。
        直接修改voice的变奏参数。
        """
        if variation_strength <= 0:
            return

        # ── 动态变奏：幅度跟随音乐能量 ──
        target_amp = self._compute_target_amplitude(music_ctx, audio_rms)
        # 平滑过渡
        voice.amplitude = voice.amplitude * 0.9 + target_amp * 0.1

        # ── 速度变奏：跟随BPM和音乐紧张度 ──
        target_speed = self._compute_target_speed(music_ctx, bpm, variation_strength)
        voice.speed = voice.speed * 0.95 + target_speed * 0.05

        # ── 相位偏移变奏：问句时轻微加速相位 ──
        if music_ctx.is_question_phrase and variation_strength > 0.3:
            voice.variation_offset = 0.05 * music_ctx.phrase_progress * variation_strength

        # ── 方向反转变奏：答句时反转方向 ──
        if music_ctx.is_answer_phrase and variation_strength > 0.5:
            # 在答句开始时反转方向
            if music_ctx.phrase_progress < 0.1:
                voice.variation_reverse = not voice.variation_reverse

        # ── 加花变奏：在高能量时插入装饰音符 ──
        if variation_strength > 0.6 and audio_rms > 0.6:
            self._add_ornaments(voice, bpm, music_ctx)
        else:
            voice.ornament_beats = []

    def _compute_target_amplitude(self, ctx: MusicContext, rms: float) -> float:
        """计算目标幅度"""
        base = 0.5 + 0.5 * rms

        if ctx.is_climax or ctx.at_peak:
            return min(1.0, base * 1.3)
        if ctx.in_buildup:
            return base * (0.7 + 0.3 * ctx.buildup_progress)
        if ctx.at_valley:
            return base * 0.5
        if ctx.is_answer_phrase:
            return base * (1.0 - 0.2 * ctx.phrase_progress)
        return base

    def _compute_target_speed(self, ctx: MusicContext, bpm: float, strength: float) -> float:
        """计算目标速度"""
        # 基础速度：BPM归一化到1.0（120BPM=1.0）
        base_speed = bpm / 120.0

        if ctx.in_buildup:
            # build-up时逐渐加速
            return base_speed * (1.0 + 0.5 * ctx.buildup_progress * strength)
        if ctx.is_climax:
            return base_speed * (1.0 + 0.3 * strength)
        if ctx.at_valley:
            return base_speed * (0.5 + 0.5 * (1.0 - strength))
        if ctx.is_question_phrase:
            # 问句：轻微加速（积累紧张）
            return base_speed * (1.0 + 0.1 * ctx.phrase_progress * strength)
        if ctx.is_answer_phrase:
            # 答句：轻微减速（释放）
            return base_speed * (1.0 - 0.1 * ctx.phrase_progress * strength)
        return base_speed

    def _add_ornaments(self, voice: Voice, bpm: float, ctx: MusicContext) -> None:
        """在高能量时添加加花装饰"""
        # 在每拍的1/4和3/4位置添加短促脉冲
        ornaments = []
        if ctx.is_climax:
            ornaments = [0.0, 0.25, 0.5, 0.75]
        elif ctx.at_peak:
            ornaments = [0.0, 0.5]
        elif ctx.is_question_phrase and ctx.phrase_progress > 0.7:
            ornaments = [0.25, 0.75]
        voice.ornament_beats = ornaments

# ═══════════════════════════════════════════════════════════════════════════
#  过渡编织器 — 结构性过渡，不是简单淡入淡出
# ═══════════════════════════════════════════════════════════════════════════

class TransitionWeaver:
    """
    过渡编织器：实现结构性的动作过渡。
    
    三种过渡策略：
    1. 有机过渡（ORGANIC）：从动作A的尾部自然生长出动作B的开头
       - 空间权重高的设备先完成过渡（波浪式交接）
       - 保持节奏连续性
    
    2. 节奏过渡（RHYTHMIC）：在BPM节拍点切换
       - 等待下一个强拍（1拍或1小节边界）
       - 保持节奏的完整性
    
    3. 形态变换（MORPH）：两个动作的权重图在空间上渐变融合
       - 不是时间上的淡入淡出，而是空间上的渐变
       - 形成"动作A在左边，动作B在右边"的过渡状态
    """

    ORGANIC  = "organic"
    RHYTHMIC = "rhythmic"
    MORPH    = "morph"

    def __init__(self):
        self._strategy = self.ORGANIC

    def set_strategy(self, strategy: str) -> None:
        self._strategy = strategy

    def start_transition(
        self,
        voice: Voice,
        next_atom: AtomType,
        next_topo: TopoAtomType,
        music_ctx: MusicContext,
        bpm: float,
        transition_urgency: float = 0.5,
    ) -> None:
        """
        启动一个声部的过渡。
        
        transition_urgency: 0..1，越高过渡越快越剧烈
        """
        voice.is_transitioning = True
        voice.transition_progress = 0.0
        voice.next_atom_type = next_atom
        voice.next_topo_type = next_topo

        # 根据策略和紧迫度计算过渡时长
        if self._strategy == self.RHYTHMIC:
            # 节奏过渡：对齐到BPM
            beat_duration = 60.0 / max(bpm, 60.0)
            if transition_urgency > 0.7:
                voice.transition_duration = beat_duration  # 1拍
            else:
                voice.transition_duration = beat_duration * 2  # 2拍
        elif self._strategy == self.MORPH:
            # 形态变换：较慢的空间渐变
            voice.transition_duration = 2.0 * (1.0 - transition_urgency * 0.5)
        else:  # ORGANIC
            # 有机过渡：根据紧迫度
            voice.transition_duration = 1.5 * (1.0 - transition_urgency * 0.6)

    def update_transition(self, voice: Voice, dt: float) -> bool:
        """
        更新过渡进度。
        返回True表示过渡完成。
        """
        if not voice.is_transitioning:
            return False

        voice.transition_progress += dt / max(voice.transition_duration, 0.01)

        if voice.transition_progress >= 1.0:
            # 过渡完成：切换到新动作
            voice.atom_type = voice.next_atom_type or voice.atom_type
            voice.topo_type = voice.next_topo_type or voice.topo_type
            voice.next_atom_type = None
            voice.next_topo_type = None
            voice.is_transitioning = False
            voice.transition_progress = 0.0
            voice.variation_reverse = False
            voice.ornament_beats = []
            return True

        return False

    def find_bridge_atom(
        self, from_atom: AtomType, to_atom: AtomType,
        music_ctx: MusicContext
    ) -> Optional[AtomType]:
        """
        寻找从A到B的"桥接动作元"。
        如果A和B差异太大，可以先经过一个中间动作元。
        """
        # 定义动作元的"家族"
        wave_family = {AtomType.WAVE_SINE, AtomType.WAVE_BREATH,
                       AtomType.WAVE_TRIANGLE, AtomType.WAVE_SAWTOOTH}
        chase_family = {AtomType.CHASE_LINEAR, AtomType.CHASE_BOUNCE,
                        AtomType.CHASE_SPIRAL}
        burst_family = {AtomType.BURST_ALL, AtomType.BURST_STAGGER,
                        AtomType.BURST_RANDOM}

        from_family = self._get_family(from_atom)
        to_family = self._get_family(to_atom)

        if from_family == to_family:
            return None  # 同族，直接过渡

        # 跨族过渡：使用WAVE_PULSE作为通用桥接
        if from_family in ('burst', 'chase') and to_family == 'wave':
            return AtomType.WAVE_PULSE
        if from_family == 'wave' and to_family in ('burst', 'chase'):
            return AtomType.WAVE_PULSE

        return None

    def _get_family(self, atom: AtomType) -> str:
        if atom in {AtomType.WAVE_SINE, AtomType.WAVE_BREATH,
                    AtomType.WAVE_TRIANGLE, AtomType.WAVE_SAWTOOTH, AtomType.WAVE_PULSE}:
            return 'wave'
        if atom in {AtomType.CHASE_LINEAR, AtomType.CHASE_BOUNCE,
                    AtomType.CHASE_SPIRAL, AtomType.CHASE_RANDOM}:
            return 'chase'
        if atom in {AtomType.CROSS_SINGLE, AtomType.CROSS_WAVE, AtomType.CROSS_MIRROR}:
            return 'cross'
        if atom in {AtomType.DIFFUSE_OUT, AtomType.DIFFUSE_IN, AtomType.DIFFUSE_RIPPLE}:
            return 'diffuse'
        if atom in {AtomType.BURST_ALL, AtomType.BURST_STAGGER, AtomType.BURST_RANDOM}:
            return 'burst'
        if atom in {AtomType.SCAN_LINEAR, AtomType.SCAN_ROTATE, AtomType.SCAN_PING_PONG}:
            return 'scan'
        if atom in {AtomType.SHAPE_ARC, AtomType.SHAPE_RING,
                    AtomType.SHAPE_CENTER, AtomType.SHAPE_HALF}:
            return 'shape'
        return 'other'

# ═══════════════════════════════════════════════════════════════════════════
#  编舞调度器 — 根据音乐上下文调度声部
# ═══════════════════════════════════════════════════════════════════════════

class ChoreographyScheduler:
    """
    根据音乐上下文实时调度多个声部（Voice）。
    
    核心逻辑：
    1. 根据复杂度参数决定声部数量
    2. 根据音乐语义选择每个声部的动作元和拓扑元
    3. 分配设备子集给每个声部（不重叠）
    4. 管理声部的生命周期和过渡
    """

    def __init__(self, params: Optional[Dict] = None):
        self._params = params or self._default_params()
        self._voices: List[Voice] = []
        self._rng = random.Random(int(time.time()))
        self._variation_engine = VariationEngine(self._rng)
        self._transition_weaver = TransitionWeaver()
        self._last_section: str = 'unknown'
        self._last_phrase_role: PhraseRole = PhraseRole.NEUTRAL
        self._last_schedule_t: float = 0.0
        self._voice_history: deque = deque(maxlen=20)

        # 增强1：motif词汇库（延迟初始化，首次使用时加载）
        self._motif_vocab: Optional[Any] = None

        # 增强4：主题记忆状态机
        # 格式：{section_label: [(atom_type, topo_type, voice_id), ...]}
        self._theme_memory: Dict[str, List[Tuple]] = {}
        # 已见过的段落顺序（用于判断是否是“再现”）
        self._section_visit_count: Dict[str, int] = {}
        # 当前段落第一次出现时的主题组合（用于小节层面的主题变奏）
        self._current_theme_atoms: List[AtomType] = []

        # 增强5：节拍量化状态
        self._last_beat_phase: float = 0.0   # 上一帧的节拍相位
        self._beat_count: int = 0             # 已经过去的节拍数

        # 增强6：声部呼应规则
        # 主声部历史动作元缓冲（用于从声部延迟跟随）
        self._leader_atom_history: deque = deque(maxlen=16)  # (t, atom_type, topo_type)
        self._canon_delay_beats: float = 1.0  # 卡农延迟节拍数（1拍）

    def _default_params(self) -> Dict:
        return {
            # 复杂度：0.0~1.0，控制并行声部数量
            'complexity': 0.5,
            # 音乐响应强度
            'audio_sensitivity': 1.0,
            # 变奏强度：0.0~1.0
            'variation_strength': 0.5,
            # 过渡策略：organic/rhythmic/morph
            'transition_style': 'organic',
            # 设备激活基础密度：0.1~1.0
            'base_device_density': 0.5,
            # 节拍跟踪强度：0.0~1.0
            'beat_tracking': 0.7,
            # 段落感知：是否根据段落切换编舞风格
            'section_aware': True,
            # 手调混合比：0.0=完全AI，1.0=完全手调
            'manual_blend': 0.0,
            # 允许的动作元类型（空=全部允许）
            'allowed_atoms': [],
            # 允许的拓扑类型（空=全部允许）
            'allowed_topos': [],
            # 增强4：主题记忆（开关）
            'theme_memory_enabled': True,
            # 增强5：节拍量化强度（0=不量化，1=强制对齐节拍）
            'beat_quantize': 0.7,
            # 增强6：声部呼应（开关）
            'canon_enabled': True,
            # 卡农延迟（节拍数）
            'canon_delay_beats': 1.0,
        }

    def update_params(self, params: Dict) -> None:
        self._params.update(params)
        # 更新过渡策略
        self._transition_weaver.set_strategy(
            self._params.get('transition_style', 'organic')
        )

    def get_voice_count(self) -> int:
        """根据复杂度计算当前应有的声部数量"""
        complexity = float(self._params.get('complexity', 0.5))
        # 复杂度0=1个声部，复杂度1=6个声部
        return max(1, int(1 + complexity * 5))

    def tick(
        self,
        t: float,
        dt: float,
        music_ctx: MusicContext,
        audio_rms: float,
        audio_bands: List[float],
        bpm: float,
        section_label: str,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
    ) -> List[Voice]:
        """
        主更新函数，每帧调用。
        返回当前所有活跃声部的列表（已更新状态）。
        """
        target_voice_count = self.get_voice_count()

        # 音乐复杂度乘数（由音乐上下文动态调整）
        complexity_mult = music_ctx.suggested_complexity_multiplier()
        effective_voice_count = max(1, int(target_voice_count * complexity_mult))

        # 调整声部数量
        self._adjust_voice_count(
            effective_voice_count, t, music_ctx, channel_set, topology
        )

        # 检测段落变化
        section_changed = section_label != self._last_section
        if section_changed:
            self._on_section_change(t, section_label, music_ctx, channel_set, topology)
            self._last_section = section_label

        # 检测乐句角色变化
        phrase_changed = music_ctx.phrase_role != self._last_phrase_role
        if phrase_changed:
            self._on_phrase_change(t, music_ctx, channel_set, topology, bpm)
            self._last_phrase_role = music_ctx.phrase_role

        # 增强5：节拍边界检测（用于量化）
        beat_phase = music_ctx.bar_progress  # 0..1，小节内进度
        beat_crossed = (self._last_beat_phase > 0.75 and beat_phase < 0.25)  # 跨过节拍边界
        half_beat_crossed = (self._last_beat_phase > 0.25 and beat_phase < 0.75 and
                              self._last_beat_phase < 0.5 and beat_phase > 0.4)  # 半拍边界近似
        quantize_strength = float(self._params.get('beat_quantize', 0.7))
        on_quantize_boundary = beat_crossed and quantize_strength > 0.3
        self._last_beat_phase = beat_phase

        # 增强5：如果到达节拍边界，触发等待中的量化切换
        if on_quantize_boundary:
            self._beat_count += 1
            for voice in self._voices:
                if voice.quantize_pending and voice.quantize_next_atom is not None:
                    self._transition_weaver.start_transition(
                        voice, voice.quantize_next_atom,
                        voice.quantize_next_topo or voice.topo_type,
                        music_ctx, bpm, 0.8
                    )
                    voice.age = 0.0
                    voice.duration = self._compute_atom_duration(voice.quantize_next_atom, music_ctx)
                    voice.quantize_pending = False
                    voice.quantize_next_atom = None
                    voice.quantize_next_topo = None

        # 增强6：更新主声部历史缓冲（用于从声部延迟跟随）
        canon_enabled = bool(self._params.get('canon_enabled', True))
        if canon_enabled:
            leader_voices = [v for v in self._voices if not v.is_follower]
            if leader_voices:
                primary = leader_voices[0]
                self._leader_atom_history.append((t, primary.atom_type, primary.topo_type))

        # 更新每个声部
        for voice in self._voices:
            # 更新相位
            voice.age += dt
            phase_delta = dt * voice.speed / max(voice.duration, 0.1)
            voice.phase = (voice.phase + phase_delta) % 1.0

            # 增强6：从声部延迟跟随主声部
            if canon_enabled and voice.is_follower and voice.leader_voice_id >= 0:
                self._apply_canon_follow(voice, t, bpm)

            # 增强1：更新motif曲线值
            if voice.motif_type_name and _MOTIF_AVAILABLE:
                try:
                    vocab = self._get_motif_vocab_instance()
                    if vocab is not None:
                        mt = MotifType(voice.motif_type_name)
                        audio_frame = {'rms': audio_rms, 'beat_phase': beat_phase}
                        voice.motif_curve_val = vocab.compute_motif_value(
                            mt, voice.get_effective_phase(), audio_frame
                        )
                except Exception:
                    log.debug("deep_choreography: voice motif update failed, skipping")
                    pass

            # 更新过渡
            self._transition_weaver.update_transition(voice, dt)

            # 应用变奏
            self._variation_engine.apply_variation(
                voice, music_ctx, audio_rms, bpm,
                float(self._params.get('variation_strength', 0.5))
            )

            # 检查是否需要切换动作元
            if self._should_switch_atom(voice, t, music_ctx, bpm):
                self._schedule_next_atom(voice, t, music_ctx, channel_set, topology, bpm)

            # 更新激活设备（拓扑相位随时间变化）
            voice.topo_phase = (voice.topo_phase + dt * 0.1 * voice.speed) % 1.0

        return self._voices

    def _adjust_voice_count(
        self, target: int, t: float,
        music_ctx: MusicContext,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
    ) -> None:
        """调整声部数量"""
        current = len(self._voices)

        if current < target:
            # 添加新声部
            for _ in range(target - current):
                voice = self._create_new_voice(t, len(self._voices), music_ctx, channel_set, topology)
                self._voices.append(voice)
        elif current > target:
            # 移除多余声部（移除最旧的）
            self._voices = self._voices[:target]

        # 重新分配设备
        self._redistribute_devices(channel_set, topology, music_ctx)

    def _create_new_voice(
        self, t: float, voice_id: int,
        music_ctx: MusicContext,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
    ) -> Voice:
        """创建一个新声部"""
        atom_type = self._select_atom_for_context(music_ctx, voice_id)
        topo_type = self._select_topo_for_context(music_ctx, voice_id)
        density = self._compute_device_density(music_ctx)

        voice = Voice(
            voice_id=voice_id,
            atom_type=atom_type,
            topo_type=topo_type,
            phase=self._rng.random(),  # 随机初始相位，避免所有声部同步
            topo_phase=self._rng.random(),
            speed=self._compute_voice_speed(music_ctx, voice_id),
            amplitude=0.8,
            density=density,
            start_t=t,
            duration=self._compute_atom_duration(atom_type, music_ctx),
        )
        return voice

    def _redistribute_devices(
        self,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
        music_ctx: MusicContext,
    ) -> None:
        """
        将设备分配给各个声部，确保不重叠。
        这是实现"多声部不同区域同时运行"的关键。
        """
        n_voices = len(self._voices)
        if n_voices == 0:
            return

        n_fixtures = channel_set.fixture_count
        if n_fixtures == 0:
            return

        # 计算每个声部应激活的设备密度
        base_density = float(self._params.get('base_device_density', 0.5))
        music_density = music_ctx.suggested_device_density()
        effective_density = base_density * music_density * float(self._params.get('audio_sensitivity', 1.0))
        effective_density = float(np.clip(effective_density, 0.1, 1.0))

        for i, voice in enumerate(self._voices):
            # 根据声部ID和拓扑类型获取激活设备
            voice.density = effective_density
            # 每个声部使用不同的相位偏移，形成空间分离
            topo_phase_offset = i / max(n_voices, 1)
            active_indices = topology.get_active_indices(
                voice.topo_type,
                density=effective_density / max(n_voices, 1) * 1.5,  # 多声部时每个声部激活更少设备
                phase=(voice.topo_phase + topo_phase_offset) % 1.0,
                rng=self._rng,
            )
            voice.active_fixture_indices = active_indices

    # ── AtomType.value → MotifType.value 映射表（增强1核心映射） ──
    _ATOM_TO_MOTIF: Dict[str, str] = {
        'wave_sine':      'wave_travel',
        'wave_pulse':     'pulse_beat',
        'wave_breath':    'wave_breath',
        'wave_sawtooth':  'wave_single',
        'wave_triangle':  'wave_standing',
        'chase_linear':   'chase_linear',
        'chase_bounce':   'chase_bidirectional',
        'chase_spiral':   'chase_spiral',
        'chase_random':   'chase_random',
        'cross_single':   'cross_single',
        'cross_wave':     'cross_cascade',
        'cross_mirror':   'cross_mirror',
        'diffuse_out':    'diffuse_center',
        'diffuse_in':     'diffuse_edge',
        'diffuse_ripple': 'diffuse_radial',
        'burst_all':      'emotion_burst',
        'burst_stagger':  'pulse_cascade',
        'burst_random':   'pulse_random',
        'scan_linear':    'chase_linear',
        'scan_rotate':    'chase_circular',
        'scan_ping_pong': 'chase_bidirectional',
        'shape_arc':      'shape_arc',
        'shape_ring':     'shape_ring',
        'shape_center':   'shape_sector',
        'shape_half':     'shape_grid',
        'hold_all':       'hold_all',
        'hold_pattern':   'hold_pattern',
        'silence':        'hold_none',
    }

    def _get_motif_vocab_instance(self):
        """延迟初始化motif词汇库"""
        if not _MOTIF_AVAILABLE:
            return None
        if self._motif_vocab is None:
            self._motif_vocab = _get_motif_vocab()
        return self._motif_vocab

    def _select_atom_for_context(self, ctx: MusicContext, voice_id: int) -> AtomType:
        """根据音乐上下文选择动作元（增强1：集成motif词汇库乐器亲和力评分）"""
        allowed = self._params.get('allowed_atoms', [])

        # 高潮/顶点：爆发类
        if ctx.is_climax or ctx.at_peak:
            candidates = [AtomType.BURST_ALL, AtomType.BURST_STAGGER,
                          AtomType.DIFFUSE_RIPPLE, AtomType.CROSS_WAVE]
        # Build-up：追逐类（积累感）
        elif ctx.in_buildup:
            progress = ctx.buildup_progress
            if progress < 0.4:
                candidates = [AtomType.WAVE_SINE, AtomType.CHASE_LINEAR]
            elif progress < 0.7:
                candidates = [AtomType.CHASE_SPIRAL, AtomType.SCAN_ROTATE,
                              AtomType.DIFFUSE_OUT]
            else:
                candidates = [AtomType.BURST_STAGGER, AtomType.CROSS_WAVE,
                              AtomType.DIFFUSE_RIPPLE]
        # 问句（上行/紧张积累）：追逐类、扫描类
        elif ctx.is_question_phrase:
            candidates = [AtomType.CHASE_LINEAR, AtomType.CHASE_SPIRAL,
                          AtomType.SCAN_LINEAR, AtomType.DIFFUSE_OUT,
                          AtomType.WAVE_SAWTOOTH]
        # 答句（下行/释放）：波动类、形状类
        elif ctx.is_answer_phrase:
            candidates = [AtomType.WAVE_BREATH, AtomType.WAVE_SINE,
                          AtomType.DIFFUSE_IN, AtomType.SHAPE_ARC,
                          AtomType.WAVE_TRIANGLE]
        # 过渡句：交叉类
        elif ctx.is_transition:
            candidates = [AtomType.CROSS_SINGLE, AtomType.CROSS_MIRROR,
                          AtomType.SCAN_PING_PONG, AtomType.CHASE_BOUNCE]
        # 谷底：静止类
        elif ctx.at_valley:
            candidates = [AtomType.WAVE_BREATH, AtomType.HOLD_PATTERN,
                          AtomType.SHAPE_CENTER, AtomType.SILENCE]
        # 默认：波动类
        else:
            candidates = [AtomType.WAVE_SINE, AtomType.WAVE_TRIANGLE,
                          AtomType.CHASE_LINEAR, AtomType.SCAN_LINEAR]

        # 过滤允许的动作元
        if allowed:
            filtered = [a for a in candidates if a.value in allowed]
            if filtered:
                candidates = filtered

        # ── 增强1：motif乐器亲和力评分，对候选列表重新排序 ──
        vocab = self._get_motif_vocab_instance()
        if vocab is not None and len(candidates) > 1:
            # 构建乐器提示列表（来自MusicContext的乐器字段）
            instrument_hints: List[str] = []
            if getattr(ctx, 'bar_kick_strength', 0.0) > 0.5:
                instrument_hints.append('bass_kick')
            if getattr(ctx, 'bar_snare_strength', 0.0) > 0.4:
                instrument_hints.append('snare')
            if getattr(ctx, 'bar_melody_strength', 0.0) > 0.4:
                instrument_hints.append('melody')
            if getattr(ctx, 'bar_hihat_strength', 0.0) > 0.4:
                instrument_hints.append('hihat')
            if not instrument_hints:
                instrument_hints = ['all']

            # 确定乐句角色字符串
            if ctx.is_question_phrase:
                phrase_role_str = 'question'
            elif ctx.is_answer_phrase:
                phrase_role_str = 'answer'
            elif ctx.is_climax:
                phrase_role_str = 'climax'
            elif ctx.is_transition:
                phrase_role_str = 'transition'
            else:
                phrase_role_str = 'neutral'

            # 用motif词汇库对候选列表评分
            scored: List[Tuple[float, AtomType]] = []
            for atom in candidates:
                motif_name = self._ATOM_TO_MOTIF.get(atom.value)
                if motif_name is None:
                    scored.append((0.0, atom))
                    continue
                try:
                    mt = MotifType(motif_name)
                    motif_def = vocab.get(mt)
                    if motif_def is None:
                        scored.append((0.0, atom))
                        continue
                    score = 0.0
                    # 乐器亲和力评分
                    for hint in instrument_hints:
                        for aff in motif_def.instrument_affinities:
                            if aff.value == hint or aff.value == 'all':
                                score += 1.5
                                break
                    # 乐句角色评分
                    if phrase_role_str in motif_def.preferred_phrase_roles:
                        score += 2.0
                    # 能量范围评分
                    e = ctx.global_energy_position
                    e_min, e_max = motif_def.energy_range
                    if e_min <= e <= e_max:
                        score += 1.5
                    scored.append((score, atom))
                except (ValueError, AttributeError):
                    scored.append((0.0, atom))

            # 按评分降序排列，保留最佳候选
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates = [a for _, a in scored]

        # 多声部时，不同声部选择不同的动作元（形成对位）
        if len(candidates) > 1 and voice_id > 0:
            idx = voice_id % len(candidates)
            return candidates[idx]

        return self._rng.choice(candidates)

    def _select_topo_for_context(self, ctx: MusicContext, voice_id: int) -> TopoAtomType:
        """根据音乐上下文选择拓扑元（增强2：问答空间映射）"""
        allowed = self._params.get('allowed_topos', [])

        if ctx.is_climax or ctx.at_peak:
            candidates = [TopoAtomType.FULL, TopoAtomType.OUTER_RING,
                          TopoAtomType.DENSE_RANDOM]
        elif ctx.in_buildup:
            progress = ctx.buildup_progress
            if progress < 0.5:
                candidates = [TopoAtomType.INNER_CORE, TopoAtomType.ARC_SEGMENT]
            else:
                candidates = [TopoAtomType.OUTER_RING, TopoAtomType.FULL]
        elif ctx.is_question_phrase:
            # 增强2：问句→左半场向外扩散
            # 主声部占据左半场，次声部占据外圈向外扩散
            if voice_id == 0:
                candidates = [TopoAtomType.LEFT_HALF, TopoAtomType.FRONT_HALF,
                              TopoAtomType.DIAGONAL_A]
            else:
                candidates = [TopoAtomType.OUTER_RING, TopoAtomType.ARC_SEGMENT,
                              TopoAtomType.DIAGONAL_A]
        elif ctx.is_answer_phrase:
            # 增强2：答句→右半场向内收拢
            # 主声部占据右半场，次声部占据内核向内收拢
            if voice_id == 0:
                candidates = [TopoAtomType.RIGHT_HALF, TopoAtomType.BACK_HALF,
                              TopoAtomType.DIAGONAL_B]
            else:
                candidates = [TopoAtomType.INNER_CORE, TopoAtomType.ARC_SEGMENT,
                              TopoAtomType.DIAGONAL_B]
        elif ctx.is_transition:
            candidates = [TopoAtomType.ALTERNATE, TopoAtomType.CLUSTER,
                          TopoAtomType.DIAGONAL_A]
        elif ctx.at_valley:
            candidates = [TopoAtomType.SINGLE_POINT, TopoAtomType.INNER_CORE,
                          TopoAtomType.SPARSE_RANDOM]
        else:
            candidates = [TopoAtomType.ARC_SEGMENT, TopoAtomType.ALTERNATE,
                          TopoAtomType.CLUSTER]

        if allowed:
            filtered = [t for t in candidates if t.value in allowed]
            if filtered:
                candidates = filtered

        # 多声部：不同声部使用不同拓扑（空间分离）
        if len(candidates) > 1 and voice_id > 0:
            idx = voice_id % len(candidates)
            return candidates[idx]

        return self._rng.choice(candidates)

    def _compute_device_density(self, ctx: MusicContext) -> float:
        """计算设备激活密度"""
        base = float(self._params.get('base_device_density', 0.5))
        music_density = ctx.suggested_device_density()
        return float(np.clip(base * music_density, 0.1, 1.0))

    def _compute_voice_speed(self, ctx: MusicContext, voice_id: int) -> float:
        """计算声部速度"""
        base = 1.0
        if ctx.in_buildup:
            base = 1.0 + ctx.buildup_progress
        elif ctx.is_climax:
            base = 1.5
        elif ctx.at_valley:
            base = 0.5

        # 多声部时，不同声部有不同速度（形成节奏对位）
        speed_ratios = [1.0, 0.5, 2.0, 1.5, 0.75, 3.0]
        ratio = speed_ratios[voice_id % len(speed_ratios)]
        return base * ratio

    def _compute_atom_duration(self, atom: AtomType, ctx: MusicContext) -> float:
        """计算动作元的持续时长"""
        # 基础时长：2~8秒
        if ctx.is_climax:
            return 2.0
        if ctx.in_buildup:
            return 3.0 * (1.0 - ctx.buildup_progress * 0.5)
        if ctx.at_valley:
            return 8.0
        if ctx.is_question_phrase:
            return 4.0
        if ctx.is_answer_phrase:
            return 4.0
        return 4.0

    def _should_switch_atom(self, voice: Voice, t: float,
                             ctx: MusicContext, bpm: float) -> bool:
        """判断是否应该切换动作元"""
        if voice.is_transitioning:
            return False

        # 动作已超过预期时长
        if voice.age > voice.duration:
            return True

        # 音乐高潮时强制切换到爆发类
        if ctx.is_climax and voice.atom_type not in {
            AtomType.BURST_ALL, AtomType.BURST_STAGGER, AtomType.DIFFUSE_RIPPLE
        }:
            return True

        # 段落变化时切换
        if ctx.phrase_progress < 0.05 and voice.age > 1.0:
            return True

        return False

    def _schedule_next_atom(
        self, voice: Voice, t: float,
        ctx: MusicContext,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
        bpm: float,
    ) -> None:
        """调度下一个动作元（增强2+4+5）"""
        next_atom = self._select_atom_for_context(ctx, voice.voice_id)
        next_topo = self._select_topo_for_context(ctx, voice.voice_id)

        # 增强4：主题记忆——如果是已见段落的再现，使用记忆的主题并做变奏
        if bool(self._params.get('theme_memory_enabled', True)):
            next_atom, next_topo = self._apply_theme_memory(
                voice, ctx, next_atom, next_topo
            )

        # 增强2：更新声部空间角色
        if ctx.is_question_phrase:
            voice.spatial_role = 'question'
        elif ctx.is_answer_phrase:
            voice.spatial_role = 'answer'
        else:
            voice.spatial_role = 'neutral'

        # 增强1：更新声部的motif绑定
        motif_name = self._ATOM_TO_MOTIF.get(next_atom.value, '')
        voice.motif_type_name = motif_name

        # 检查是否需要桥接动作元
        bridge = self._transition_weaver.find_bridge_atom(
            voice.atom_type, next_atom, ctx
        )

        urgency = ctx.suggested_transition_urgency()

        # 增强5：节拍量化——如果量化强度足够高，将切换排队到下一个节拍边界
        quantize_strength = float(self._params.get('beat_quantize', 0.7))
        beat_phase = getattr(ctx, 'bar_progress', 0.0)
        # 如果当前不在节拍边界附近（前10%）且量化强度足够，就排队等待
        near_boundary = beat_phase < 0.1 or beat_phase > 0.9
        if quantize_strength > 0.5 and not near_boundary and not voice.is_transitioning:
            voice.quantize_pending = True
            voice.quantize_next_atom = next_atom
            voice.quantize_next_topo = next_topo
            # 尚未到达节拍边界，先不切换
            return

        if bridge is not None and voice.atom_type != bridge:
            # 先过渡到桥接动作元
            self._transition_weaver.start_transition(
                voice, bridge, voice.topo_type, ctx, bpm, urgency
            )
        else:
            # 直接过渡到下一个动作元
            self._transition_weaver.start_transition(
                voice, next_atom, next_topo, ctx, bpm, urgency
            )

        # 重置年龄
        voice.age = 0.0
        voice.duration = self._compute_atom_duration(next_atom, ctx)

    def _apply_theme_memory(
        self, voice: Voice, ctx: MusicContext,
        next_atom: AtomType, next_topo: TopoAtomType
    ) -> Tuple[AtomType, TopoAtomType]:
        """增强4：主题记忆应用。如果当前段落是已见段落的再现，使用记忆的主题并做变奏。"""
        section = self._last_section
        visit_count = self._section_visit_count.get(section, 0)

        if visit_count <= 1:
            # 第一次出现：直接使用选择的动作元，并记忆到主题库
            return next_atom, next_topo

        # 再现：尝试从主题记忆中取出对应声部的历史主题
        if section in self._theme_memory:
            history = self._theme_memory[section]
            # 找到对应当前声部ID的记忆
            matching = [(a, tp) for (a, tp, vid) in history if vid == voice.voice_id]
            if matching:
                orig_atom, orig_topo = matching[0]
                # 对原始主题做变奏：根据再现次数递增变化
                variation_level = min(visit_count - 1, 3)  # 最多3级变奏
                if variation_level >= 1:
                    # 变奏级刡12：保留拓扑类型，动作元切换到同类的更强版本
                    # 如 WAVE_SINE → WAVE_SAWTOOTH，CHASE_LINEAR → CHASE_SPIRAL
                    variation_map: Dict[AtomType, AtomType] = {
                        AtomType.WAVE_SINE: AtomType.WAVE_SAWTOOTH,
                        AtomType.WAVE_BREATH: AtomType.WAVE_TRIANGLE,
                        AtomType.CHASE_LINEAR: AtomType.CHASE_SPIRAL,
                        AtomType.SCAN_LINEAR: AtomType.SCAN_ROTATE,
                        AtomType.DIFFUSE_OUT: AtomType.DIFFUSE_RIPPLE,
                        AtomType.SHAPE_ARC: AtomType.SHAPE_RING,
                    }
                    next_atom = variation_map.get(orig_atom, orig_atom)
                else:
                    next_atom = orig_atom
                next_topo = orig_topo
                log.debug(
                    "Theme memory: section=%s visit=%d voice=%d "
                    "orig=%s -> varied=%s",
                    section, visit_count, voice.voice_id,
                    orig_atom.value, next_atom.value
                )
        return next_atom, next_topo

    def _on_section_change(
        self, t: float, new_section: str,
        ctx: MusicContext,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
    ) -> None:
        """段落变化时的处理（增强4：主题记忆保存）"""
        if not self._params.get('section_aware', True):
            return

        # 增强4：在段落切换时保存当前声部主题到记忆库
        if bool(self._params.get('theme_memory_enabled', True)):
            old_section = self._last_section
            if old_section not in self._theme_memory and self._voices:
                # 首次离开该段落：保存当前声部的主题
                self._theme_memory[old_section] = [
                    (v.atom_type, v.topo_type, v.voice_id)
                    for v in self._voices
                ]
                log.debug("Theme memory saved: section=%s atoms=%s",
                          old_section,
                          [v.atom_type.value for v in self._voices])
            # 更新段落访问计数
            self._section_visit_count[new_section] = \
                self._section_visit_count.get(new_section, 0) + 1

        # 强制所有声部开始过渡
        for voice in self._voices:
            if not voice.is_transitioning:
                next_atom = self._select_atom_for_context(ctx, voice.voice_id)
                next_topo = self._select_topo_for_context(ctx, voice.voice_id)
                urgency = 0.8 if new_section in ('chorus', 'drop', 'build') else 0.4
                self._transition_weaver.start_transition(
                    voice, next_atom, next_topo, ctx, 120.0, urgency
                )

    def _apply_canon_follow(
        self, follower: Voice, t: float, bpm: float
    ) -> None:
        """增强6：将从声部的动作元对齐到主声部的历史状态（延迟跟随）"""
        if not self._leader_atom_history:
            return
        # 计算延迟时间（秒）
        delay_beats = float(self._params.get('canon_delay_beats', 1.0))
        beat_sec = 60.0 / max(bpm, 1.0)
        delay_sec = delay_beats * beat_sec
        target_t = t - delay_sec

        # 在历史缓冲中找到最接近target_t的记录
        best: Optional[Tuple] = None
        best_diff = float('inf')
        for record in self._leader_atom_history:
            rec_t, rec_atom, rec_topo = record
            diff = abs(rec_t - target_t)
            if diff < best_diff:
                best_diff = diff
                best = record

        if best is None or best_diff > delay_sec * 2:
            return  # 没有合适的历史记录

        _, target_atom, target_topo = best

        # 如果从声部的动作元与目标不同，触发过渡
        if follower.atom_type != target_atom and not follower.is_transitioning:
            follower.next_atom_type = target_atom
            follower.next_topo_type = target_topo
            follower.is_transitioning = True
            follower.transition_progress = 0.0
            follower.transition_duration = beat_sec * 0.5  # 半拍过渡
            log.debug(
                "Canon follow: follower=%d atom %s -> %s (delay=%.2fs)",
                follower.voice_id, follower.atom_type.value,
                target_atom.value, delay_sec
            )

    def _on_phrase_change(
        self, t: float,
        ctx: MusicContext,
        channel_set: FixtureChannelSet,
        topology: SpatialTopologyAnalyzer,
        bpm: float,
    ) -> None:
        """乐句角色变化时的处理（增强2：空间角色切换）"""
        # 增强2：问答句切换时，立即触发空间角色翻转，并强制拓扑切换
        if ctx.is_answer_phrase and self._last_phrase_role == PhraseRole.ANTECEDENT:
            # 问句→答句：
            # 1. 方向反转（原有逻辑）
            # 2. 将拓扑切换到右半场/内收（增强2）
            for voice in self._voices:
                voice.spatial_role = 'answer'
                if not voice.is_transitioning:
                    voice.variation_reverse = not voice.variation_reverse
                    # 强制切换到答句对应的拓扑
                    answer_topo = self._select_topo_for_context(ctx, voice.voice_id)
                    self._transition_weaver.start_transition(
                        voice, voice.atom_type, answer_topo, ctx, bpm, 0.7
                    )

        elif ctx.is_question_phrase and self._last_phrase_role == PhraseRole.CONSEQUENT:
            # 答句→问句：将拓扑切换到左半场/向外（增强2）
            for voice in self._voices:
                voice.spatial_role = 'question'
                if not voice.is_transitioning:
                    question_topo = self._select_topo_for_context(ctx, voice.voice_id)
                    self._transition_weaver.start_transition(
                        voice, voice.atom_type, question_topo, ctx, bpm, 0.6
                    )

        # 高潮句开始时，强制切换到爆发类
        if ctx.is_climax:
            for voice in self._voices:
                if voice.atom_type not in {AtomType.BURST_ALL, AtomType.BURST_STAGGER,
                                            AtomType.DIFFUSE_RIPPLE, AtomType.CROSS_WAVE}:
                    self._schedule_next_atom(voice, t, ctx, channel_set, topology, bpm)

    def force_voice_atom(self, voice_id: int, atom_type: AtomType,
                          topo_type: Optional[TopoAtomType] = None) -> None:
        """强制指定声部切换到特定动作元（手动触发）"""
        for voice in self._voices:
            if voice.voice_id == voice_id:
                voice.atom_type = atom_type
                if topo_type is not None:
                    voice.topo_type = topo_type
                voice.age = 0.0
                voice.is_transitioning = False
                break

    def set_canon_follower(self, voice_id: int, leader_id: int,
                            delay_beats: Optional[float] = None) -> None:
        """增强6：将指定声部设置为卡农从声部，跟随指定主声部"""
        if delay_beats is not None:
            self._canon_delay_beats = delay_beats
            self._params['canon_delay_beats'] = delay_beats
        for voice in self._voices:
            if voice.voice_id == voice_id:
                voice.is_follower = True
                voice.leader_voice_id = leader_id
                voice.canon_delay = delay_beats or self._canon_delay_beats
                break

    def get_status(self) -> Dict:
        """获取调度器状态（增强1/2/4/5/6新字段）"""
        return {
            'voice_count': len(self._voices),
            'theme_memory_sections': list(self._theme_memory.keys()),
            'section_visits': dict(self._section_visit_count),
            'beat_count': self._beat_count,
            'voices': [
                {
                    'id': v.voice_id,
                    'atom': v.atom_type.value,
                    'topo': v.topo_type.value,
                    'phase': v.phase,
                    'speed': v.speed,
                    'amplitude': v.amplitude,
                    'density': v.density,
                    'age': v.age,
                    'duration': v.duration,
                    'transitioning': v.is_transitioning,
                    'transition_progress': v.transition_progress,
                    'active_count': len(v.active_fixture_indices),
                    # 增强1：motif语义
                    'motif': v.motif_type_name,
                    'motif_curve': round(v.motif_curve_val, 3),
                    # 增强2：空间角色
                    'spatial_role': v.spatial_role,
                    # 增强5：节拍量化
                    'quantize_pending': v.quantize_pending,
                    # 增强6：声部呼应
                    'is_follower': v.is_follower,
                    'leader_id': v.leader_voice_id,
                }
                for v in self._voices
            ]
        }


# ═══════════════════════════════════════════════════════════════════════════
#  DeepChoreographyEngine — 顶层主引擎
#  整合所有子系统，直接写入 NetworkManager 的 "effect_auto" 层
# ═══════════════════════════════════════════════════════════════════════════

class DeepChoreographyEngine:
    """
    深度AI编舞引擎主类。

    工作流程：
    1. 接收音频帧事件（来自 MusicStructureAnalyzer / AudioDriver）
    2. 通过 DeepMusicAnalyzer 查询当前音乐上下文（MusicContext）
    3. 通过 ChoreographyScheduler 生成/更新多声部（Voice）
    4. 通过 DMXGenerator + ExpressionMatrix 计算每个设备的 DMX 值
    5. 通过 NetworkManager.update_layer_sparse() 写入 "effect_auto" 层

    参数系统：
    - complexity:         0.0~1.0，声部数量/动作复杂度
    - variation_strength: 0.0~1.0，变奏强度
    - base_device_density:0.0~1.0，基础设备激活率
    - transition_style:   organic/rhythmic/morph
    - expression_style:   harmonic/motion_design/rhythmic_beat/narrative_emo/custom
    - section_aware:      bool，是否响应段落变化
    - audio_sensitivity:  0.0~2.0，音频响应灵敏度
    - manual_blend:       0.0~1.0，手调混合比（0=全AI，1=全手调）
    - motor_weight:       0.0~1.0，电机表现力权重
    - pump_weight:        0.0~1.0，泵表现力权重
    - valve_weight:       0.0~1.0，阀门表现力权重
    - light_weight:       0.0~1.0，灯光表现力权重
    """

    LAYER_NAME = "effect_auto"   # 写入 NM 的层名称（AI 自动编排专用层）

    def __init__(self):
        self._running = False
        self._t_start = 0.0
        self._t_last = 0.0
        self._lock = threading.Lock()

        # 参数
        self._params: Dict[str, Any] = self._default_params()

        # 子系统（延迟初始化）
        self._channel_set: Optional[FixtureChannelSet] = None
        self._topology: Optional[SpatialTopologyAnalyzer] = None
        self._scheduler: Optional[ChoreographyScheduler] = None
        self._dmx_gen: Optional[DMXGenerator] = None
        self._deep_analyzer: Optional[Any] = None   # DeepMusicAnalyzer
        self._expr_matrix: Optional[Any] = None     # ExpressionMatrix
        self._dac_orchestrator: Optional[Any] = None  # DeepAquaOrchestrator
        self._raw_fixture_list: List[dict] = []
        self._raw_fixture_defs: Dict[str, Any] = {}

        # 当前音频状态
        self._audio_rms: float = 0.0
        self._audio_bands: List[float] = [0.0] * 8
        self._audio_bpm: float = 120.0
        self._audio_beat_phase: float = 0.0
        # C5 真实音高/和弦（None=无真实音高，回退频段估算）
        self._melody_pitch_norm: Optional[float] = None
        self._melody_velocity: float = 0.0
        self._chord_root: int = -1
        self._chord_quality: str = "major"

        # 当前音乐上下文
        self._music_ctx: Optional[Any] = None  # MusicContext

        # 当前段落标签（从 MusicStructureAnalyzer 事件中提取）
        self._current_section: str = "unknown"

        # 输出统计
        self._frame_count: int = 0
        self._last_output_time: float = 0.0

        # 回调（可选）
        self._on_status_changed: Optional[Callable] = None

        log.info("DeepChoreographyEngine 已创建")

    # ── 默认参数 ─────────────────────────────────────────────────────────────

    def _default_params(self) -> Dict[str, Any]:
        return {
            'complexity':           0.4,
            'variation_strength':   0.5,
            'base_device_density':  0.5,
            'transition_style':     'organic',
            'expression_style':     'harmonic',
            'section_aware':        True,
            'audio_sensitivity':    1.0,
            'manual_blend':         0.0,
            'motor_weight':         0.7,
            'pump_weight':          0.6,
            'valve_weight':         0.5,
            'light_weight':         0.4,
            'allowed_atoms':        [],
            'allowed_topos':        [],
        }

    # ── 初始化设备 ───────────────────────────────────────────────────────────

    def setup_fixtures(
        self,
        fixtures: List[Any],          # List[FixtureInstance]
        fixture_defs: Dict[str, Any], # {def_id: FixtureDefinition}
    ) -> None:
        """
        根据设备实例和定义，构建 FixtureChannelSet 和 SpatialTopologyAnalyzer。
        必须在 start() 之前调用。
        """
        # ── 线程安全保护（perf.threadsafe 装饰器等价语义）────
        with self._lock:
            ch_set = FixtureChannelSet()
            fixture_list = []

            for idx, fi in enumerate(fixtures):
                fdef = fixture_defs.get(getattr(fi, 'def_id', ''))
                if fdef is None:
                    continue

                channels = getattr(fdef, 'channels', []) or []
                start_ch = getattr(fi, 'start_channel', 1) - 1  # 0-indexed
                universe = getattr(fi, 'universe', 1)
                pos_x = float(getattr(fi, 'pos_x', 0.0))
                pos_y = float(getattr(fi, 'pos_y', 0.0))
                fixture_name = getattr(fi, 'name', f'Fixture_{idx}')
                fixture_id = getattr(fi, 'id', str(idx))

                fixture_list.append({
                    'id': fixture_id,
                    'name': fixture_name,
                    'universe': universe,
                    'start_channel': start_ch,
                    'pos_x': pos_x,
                    'pos_y': pos_y,
                    'channels': channels,
                    'index': idx,
                })

                for ch_offset, ch_type_str in enumerate(channels):
                    dmx_addr = start_ch + ch_offset
                    fc = FixtureChannel(
                        fixture_id=fixture_id,
                        fixture_name=fixture_name,
                        universe=universe,
                        dmx_addr=dmx_addr,
                        ch_type=ch_type_str,
                        pos_x=pos_x,
                        pos_y=pos_y,
                        fixture_index=idx,
                    )

                    if ch_type_str == 'pump':
                        ch_set.pump_channels.append(fc)
                    elif ch_type_str == 'valve':
                        ch_set.valve_channels.append(fc)
                    elif ch_type_str == 'motor_x':
                        ch_set.motor_x_channels.append(fc)
                    elif ch_type_str == 'motor_y':
                        ch_set.motor_y_channels.append(fc)
                    elif ch_type_str == 'motor_z':
                        ch_set.motor_z_channels.append(fc)
                    elif ch_type_str in ('dimmer', 'red', 'green', 'blue', 'white'):
                        ch_set.dimmer_channels.append(fc)

            ch_set.all_fixtures = fixture_list
            self._channel_set = ch_set
            self._topology = SpatialTopologyAnalyzer(fixture_list)
            # 保存原始 fixture 数据供 DeepAquaOrchestrator 使用
            self._raw_fixture_list = fixture_list
            self._raw_fixture_defs = fixture_defs

            log.info(
                "设备初始化完成: %d个设备, 泵=%d, 阀门=%d, 电机X=%d, 调光=%d",
                len(fixture_list),
                len(ch_set.pump_channels),
                len(ch_set.valve_channels),
                len(ch_set.motor_x_channels),
                len(ch_set.dimmer_channels),
            )

    # ── 启动/停止 ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """启动引擎"""
        if self._running:
            return True
        if self._channel_set is None or self._channel_set.fixture_count == 0:
            log.warning("DeepChoreographyEngine: 未设置设备，无法启动")
            return False

        with self._lock:
            # 初始化子系统
            self._init_subsystems()
            self._running = True
            self._t_start = time.time()
            self._t_last = self._t_start
            self._frame_count = 0

        log.info("DeepChoreographyEngine 已启动")
        return True

    def stop(self) -> None:
        """停止引擎，清空输出层"""
        with self._lock:
            self._running = False

        # 清空 NM 层
        self._clear_nm_layer()
        log.info("DeepChoreographyEngine 已停止")

    def is_running(self) -> bool:
        return self._running

    def _init_subsystems(self) -> None:
        """初始化所有子系统"""
        # 深度音乐分析器
        try:
            from .deep_music_analyzer import get_deep_music_analyzer
            self._deep_analyzer = get_deep_music_analyzer()
        except Exception as e:
            log.warning("DeepMusicAnalyzer 初始化失败: %s", e)

        # 表现力矩阵（仅作配置容器；其权重不参与输出，见 _apply_expression_matrix）
        try:
            from .expression_matrix import ExpressionMatrix, ExpressionStyle
            self._expr_matrix = ExpressionMatrix()
            style_name = self._params.get('expression_style', 'harmonic')
            try:
                style = ExpressionStyle(style_name)
                self._expr_matrix.set_style(style)
            except ValueError:
                pass
        except Exception as e:
            log.warning("ExpressionMatrix 初始化失败: %s", e)

        # 动作元选择器（MotifSelector）特性未实现：motif_system 仅有 MotifVocabulary，
        # 无 MotifSelector 类，原 import 每次抛 ImportError 被静默吞掉，且 _motif_selector
        # 赋值后从不读取。已移除死代码；需要时基于 _get_motif_vocab_instance() 再实现。

        # 编舞调度器
        self._scheduler = ChoreographyScheduler(self._params.copy())

        # DMX生成器
        self._dmx_gen = DMXGenerator(self._channel_set, self._topology)

        # ★ DeepAqua 核心编舞引擎（接管所有核心计算）
        try:
            from .deep_aqua_core.orchestrator import DeepAquaOrchestrator
            self._dac_orchestrator = DeepAquaOrchestrator()
            # 构建 fixture_defs 格式：{fixture_id: {name, channels, pos_x, pos_y}}
            dac_fixture_defs = {}
            for fix in self._raw_fixture_list:
                dac_fixture_defs[fix['id']] = {
                    'name': fix['name'],
                    'channels': fix.get('channels', []),
                    'pos_x': fix.get('pos_x', 0.0),
                    'pos_y': fix.get('pos_y', 0.0),
                }
            self._dac_orchestrator.initialize(
                topview_positions=self._raw_fixture_list,
                fixture_defs=dac_fixture_defs,
            )
            # 将 UI 参数同步到 Orchestrator
            self._sync_params_to_orchestrator()
            log.info("DeepAquaOrchestrator 初始化完成，设备数=%d",
                     len(self._raw_fixture_list))
        except Exception as e:
            log.warning("DeepAquaOrchestrator 初始化失败，降级为旧引擎: %s", e)
            self._dac_orchestrator = None

    # ── 参数设置 ─────────────────────────────────────────────────────────────

    def set_params(self, params: Dict[str, Any]) -> None:
        """更新引擎参数"""
        with self._lock:
            self._params.update(params)
            if self._scheduler:
                self._scheduler.update_params(self._params.copy())
            if self._expr_matrix:
                try:
                    from .expression_matrix import ExpressionStyle
                    style_name = self._params.get('expression_style', 'harmonic')
                    try:
                        style = ExpressionStyle(style_name)
                    except ValueError:
                        log.warning("非法 expression_style '%s'，回退 harmonic", style_name)
                        self._params['expression_style'] = 'harmonic'
                        style = ExpressionStyle.HARMONIC
                    self._expr_matrix.set_style(style)
                    # 更新各设备权重
                    self._expr_matrix.set_master_level(
                        float(self._params.get('audio_sensitivity', 1.0))
                    )
                    self._expr_matrix.set_device_master_weight(
                        'motor', float(self._params.get('motor_weight', 0.7))
                    )
                    self._expr_matrix.set_device_master_weight(
                        'pump', float(self._params.get('pump_weight', 0.6))
                    )
                    self._expr_matrix.set_device_master_weight(
                        'valve', float(self._params.get('valve_weight', 0.5))
                    )
                    self._expr_matrix.set_device_master_weight(
                        'light', float(self._params.get('light_weight', 0.4))
                    )
                    if self._params.get('transition_style'):
                        if self._scheduler:
                            self._scheduler._transition_weaver.set_strategy(
                                self._params['transition_style']
                            )
                except Exception:
                    log.debug("deep_choreography: update_params failed")
            # 同步到 DeepAquaOrchestrator
            if self._dac_orchestrator is not None:
                try:
                    self._sync_params_to_orchestrator()
                except Exception:
                    log.debug("deep_choreography: sync_params_to_orchestrator failed")

    def get_params(self) -> Dict[str, Any]:
        return self._params.copy()

    # ── 音频事件接收 ─────────────────────────────────────────────────────────

    def on_audio_event(self, event: Any) -> None:
        """
        接收来自 MusicStructureAnalyzer 的 analysis_tick 事件。
        event 是 MusicStructureEvent 对象或 dict。
        """
        if not self._running:
            return

        # 提取音频参数（共享状态写入纳入 self._lock，与 tick 持锁读对齐）
        try:
            with self._lock:
                if hasattr(event, 'rms'):
                    self._audio_rms = float(event.rms)
                    self._audio_bpm = float(getattr(event, 'bpm', 120.0))
                    self._audio_beat_phase = float(getattr(event, 'beat_phase', 0.0))
                    bands = getattr(event, 'bands', None)
                    if bands is not None:
                        self._audio_bands = [float(b) for b in bands[:8]]
                    # 提取段落标签（MusicStructureEvent 独有字段）
                    sl = getattr(event, 'section_label', None)
                    if sl is not None:
                        self._current_section = str(sl)
                elif isinstance(event, dict):
                    self._audio_rms = float(event.get('rms', 0.5))
                    self._audio_bpm = float(event.get('bpm', 120.0))
                    self._audio_beat_phase = float(event.get('beat_phase', 0.0))
                    bands = event.get('bands', None)
                    if bands is not None:
                        self._audio_bands = [float(b) for b in bands[:8]]
                    sl = event.get('section_label', None)
                    if sl is not None:
                        self._current_section = str(sl)
        except Exception:
            log.debug("deep_choreography: on_audio_event failed")
            pass

        # 更新深度音乐分析器
        if self._deep_analyzer is not None:
            try:
                self._deep_analyzer.on_msa_event(
                    event if isinstance(event, dict) else event.__dict__
                )
            except Exception:
                log.debug("deep_choreography: on_audio_frame deep_analyzer tick failed")

        # 触发一帧生成
        self.tick()

    def on_audio_frame(self, frame: Any) -> None:
        """
        接收来自 AudioDriver 的 frame_ready 信号（AudioFrame 对象）。
        """
        if not self._running:
            return
        try:
            with self._lock:
                self._audio_rms = float(getattr(frame, 'rms', 0.5))
                self._audio_bpm = float(getattr(frame, 'bpm', 120.0))
                self._audio_beat_phase = float(getattr(frame, 'beat_phase', 0.0))
                bands = getattr(frame, 'bands', None)
                if bands is not None:
                    self._audio_bands = [float(b) for b in bands[:8]]
                # C5：真实音高/和弦（pitch_midi<0 表示无音高/未检测）
                pm = float(getattr(frame, 'pitch_midi', -1.0))
                if pm >= 0.0:
                    self._melody_pitch_norm = max(0.0, min(1.0, (pm - 48.0) / 48.0))
                    self._melody_velocity = float(getattr(frame, 'pitch_confidence', 0.0))
                    self._chord_root = int(getattr(frame, 'chord_root', -1))
                    self._chord_quality = str(getattr(frame, 'chord_quality', '') or "major")
                else:
                    self._melody_pitch_norm = None
        except Exception:
            log.debug("deep_choreography: on_audio_frame failed, tick fallback")
            pass
        self.tick()

    # ── 核心 tick ────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """
        核心帧生成函数。
        计算当前帧的所有设备 DMX 值，并写入 NM 的 effect_auto 层。
        """
        if not self._running:
            return

        t_now = time.time()
        dt = t_now - self._t_last
        if dt <= 0:
            return
        self._t_last = t_now
        t = t_now - self._t_start

        # 限制帧率（最高 50fps）
        if dt < 0.02:
            return

        with self._lock:
            if not self._running:
                return
            if self._scheduler is None or self._dmx_gen is None:
                return

            # 查询音乐上下文
            music_ctx = self._get_music_context(t)

            manual_blend = float(self._params.get('manual_blend', 0.0))

            # ★ DeepAqua 核心引擎接管计算
            if self._dac_orchestrator is not None:
                try:
                    from .deep_aqua_core.orchestrator import ChoreographyInput
                    # 构建编舞输入对象
                    inp = ChoreographyInput.from_music_context(music_ctx)
                    # 覆盖音频实时参数
                    inp.bpm = self._audio_bpm
                    inp.energy = self._audio_rms
                    inp.beat_phase = self._audio_beat_phase
                    inp.beat_strength = float(self._audio_bands[0]) if self._audio_bands else 0.5
                    # 覆盖 UI 参数
                    p = self._params
                    inp.complexity = float(p.get('complexity', 3.0))
                    inp.device_density = float(p.get('base_device_density', 0.75))
                    inp.variation_strength = float(p.get('variation_strength', 0.5))
                    inp.expression_style = str(p.get('expression_style', 'harmonic'))
                    # 空间叙事参数
                    inp.spatial_direction = float(p.get('spatial_direction', 0.0))
                    inp.is_question = bool(p.get('is_question', False))
                    inp.is_answer = bool(p.get('is_answer', False))
                    # 旋律/和声参数：C5 真实音高优先，无则回退频段估算
                    if self._melody_pitch_norm is not None:
                        inp.melody_pitch = self._melody_pitch_norm
                        inp.melody_velocity = self._melody_velocity
                        if self._chord_root >= 0:
                            inp.chord_root = self._chord_root
                            inp.chord_quality = self._chord_quality
                    elif not hasattr(music_ctx, 'melody_pitch'):
                        # 用音频频谱估算旋律音高
                        if self._audio_bands and len(self._audio_bands) >= 4:
                            high_energy = sum(self._audio_bands[4:]) / max(1, len(self._audio_bands[4:]))
                            inp.melody_pitch = float(high_energy)
                    dac_output = self._dac_orchestrator.tick(inp, dt=dt)
                    # dac_output: {device_id: {ch_type: dmx_val}}
                    # 转换为 {universe: {dmx_addr: val}}
                    dmx_output = self._convert_dac_output(dac_output)
                    if manual_blend < 1.0:
                        self._write_to_nm(dmx_output, 1.0 - manual_blend)
                    self._frame_count += 1
                    self._last_output_time = t_now
                    return
                except Exception as e:
                    log.debug("DeepAquaOrchestrator tick 失败，降级为旧引擎: %s", e)

            # 降级：旧引擎路径
            # 调度声部
            voices = self._scheduler.tick(
                t=t,
                dt=dt,
                music_ctx=music_ctx,
                audio_rms=self._audio_rms,
                audio_bands=self._audio_bands,
                bpm=self._audio_bpm,
                section_label=self._current_section,
                channel_set=self._channel_set,
                topology=self._topology,
            )

            # 生成 DMX 値
            dmx_output = self._generate_dmx(voices, t)

            # 写入 NM 层
            if manual_blend < 1.0:
                self._write_to_nm(dmx_output, 1.0 - manual_blend)

            self._frame_count += 1
            self._last_output_time = t_now

    def _get_music_context(self, t: float) -> Any:
        """获取当前音乐上下文"""
        if self._deep_analyzer is not None:
            try:
                ctx = self._deep_analyzer.query(t)
                self._music_ctx = ctx
                return ctx
            except Exception:
                log.debug("deep_choreography: deep_analyzer.query failed")
                pass

        # 回退：从音频参数构建简单上下文
        return self._build_fallback_context(t)

    def _build_fallback_context(self, t: float) -> Any:
        """当深度分析器不可用时，从音频参数构建简单上下文"""
        try:
            from .deep_music_analyzer import MusicContext, PhraseRole, EmotionArc
            ctx = MusicContext(
                t=t,
                phrase_role=PhraseRole.NEUTRAL,
                phrase_progress=0.5,
                emotion_arc=EmotionArc.STABLE,
                tension_level=self._audio_rms,
                in_buildup=False,
                buildup_progress=0.0,
                global_energy_position=self._audio_rms,
                bar_energy=self._audio_rms,
                bar_progress=self._audio_beat_phase,
                next_phrase_role=PhraseRole.NEUTRAL,
            )
            return ctx
        except Exception:
            # 最后的回退：返回一个简单对象
            class SimpleCtx:
                phrase_role = None
                phrase_progress = 0.5
                energy_position = 0.5
                global_energy_position = 0.5
                tension_level = 0.5
                in_buildup = False
                buildup_progress = 0.0
                section_label = 'verse'
                bar_progress = 0.0
                bar_beat = 0.0
                bpm = 120.0
                is_climax = False
                at_peak = False
                at_valley = False
                is_question_phrase = False
                is_answer_phrase = False
                is_transition = False
                is_bar_start = False
                is_phrase_start = False
                is_section_start = False
                next_phrase_role = None
                bars_since_section_start = 0
                def suggested_complexity_multiplier(self): return 1.0
                def suggested_device_density(self): return 0.5
                def suggested_transition_urgency(self): return 0.5
            return SimpleCtx()

    def _sync_params_to_orchestrator(self) -> None:
        """将 UI 参数同步到 DeepAquaOrchestrator"""
        if self._dac_orchestrator is None:
            return
        try:
            p = self._params
            self._dac_orchestrator.update_params({
                'complexity':            float(p.get('complexity', 0.4)),
                'variation_strength':    float(p.get('variation_strength', 0.5)),
                'device_density':        float(p.get('base_device_density', 0.5)),
                'expression_style':      str(p.get('expression_style', 'harmonic')),
                'transition_style':      str(p.get('transition_style', 'organic')),
                'audio_sensitivity':     float(p.get('audio_sensitivity', 1.0)),
                'section_aware':         bool(p.get('section_aware', True)),
                'allowed_atoms':         list(p.get('allowed_atoms', [])),
                'beat_quantize':         float(p.get('beat_quantize', 0.7)),
                'canon_enabled':         bool(p.get('canon_enabled', True)),
                'canon_delay_beats':     float(p.get('canon_delay_beats', 1.0)),
                'theme_memory_enabled':  bool(p.get('theme_memory_enabled', True)),
                'motor_weight':          float(p.get('motor_weight', 0.7)),
                'pump_weight':           float(p.get('pump_weight', 0.6)),
                'valve_weight':          float(p.get('valve_weight', 0.5)),
                'light_weight':          float(p.get('light_weight', 0.4)),
            })
        except Exception as e:
            log.debug("_sync_params_to_orchestrator 失败: %s", e)

    def _convert_dac_output(
        self,
        dac_output: Dict[str, Dict[str, float]],
    ) -> Dict[int, Dict[int, float]]:
        """
        将 DeepAquaOrchestrator 的输出格式
        {device_id: {ch_type: dmx_val}}
        转换为 NetworkManager 需要的格式
        {universe: {dmx_addr: value}}
        """
        result: Dict[int, Dict[int, float]] = {}
        if not dac_output or not self._channel_set:
            return result

        # 构建 device_id 到 fixture_info 的映射
        id_to_fix: Dict[str, dict] = {}
        for fix in self._channel_set.all_fixtures:
            id_to_fix[str(fix.get('id', ''))] = fix

        for device_id, ch_vals in dac_output.items():
            fix = id_to_fix.get(str(device_id))
            if fix is None:
                continue
            universe = fix['universe']
            start_ch = fix['start_channel']
            channels = fix.get('channels', [])
            if universe not in result:
                result[universe] = {}
            for ch_offset, ch_type_str in enumerate(channels):
                dmx_addr = start_ch + ch_offset
                # 尝试直接匹配通道类型
                val = ch_vals.get(ch_type_str)
                if val is None:
                    # 尝试带前缀匹配（如 motor_x 匹配 motor）
                    for k, v in ch_vals.items():
                        if ch_type_str.startswith(k) or k.startswith(ch_type_str):
                            val = v
                            break
                if val is not None:
                    result[universe][dmx_addr] = float(np.clip(val, 0, 255))
        return result

    def _generate_dmx(
        self,
        voices: List[Voice],
        t: float,
    ) -> Dict[int, Dict[int, float]]:
        """
        生成所有声部的 DMX 値。
        返回: {universe: {dmx_addr: value(0..255)}}
        """
        # 合并所有声部的输出
        merged: Dict[int, Dict[str, float]] = {}  # {fixture_idx: {ch_type: value}}

        for voice in voices:
            voice_output = self._dmx_gen.generate(
                voice=voice,
                t=t,
                audio_rms=self._audio_rms,
                audio_bands=self._audio_bands,
            )
            # 合并（取最大值，HTP混音）
            for fix_idx, ch_vals in voice_output.items():
                if fix_idx not in merged:
                    merged[fix_idx] = {}
                for ch_type, val in ch_vals.items():
                    if ch_type.startswith('motor'):
                        # 电机：取平均值（避免多声部冲突）
                        if ch_type in merged[fix_idx]:
                            merged[fix_idx][ch_type] = (merged[fix_idx][ch_type] + val) / 2
                        else:
                            merged[fix_idx][ch_type] = val
                    else:
                        # 其他：HTP（取最大值）
                        merged[fix_idx][ch_type] = max(
                            merged[fix_idx].get(ch_type, 0.0), val
                        )

        # 应用表现力矩阵
        if self._expr_matrix is not None:
            merged = self._apply_expression_matrix(merged, t)

        # 转换为 {universe: {dmx_addr: value}}
        result: Dict[int, Dict[int, float]] = {}
        for fix_idx, ch_vals in merged.items():
            if fix_idx >= len(self._channel_set.all_fixtures):
                continue
            fix_info = self._channel_set.all_fixtures[fix_idx]
            universe = fix_info['universe']
            start_ch = fix_info['start_channel']
            channels = fix_info['channels']

            if universe not in result:
                result[universe] = {}

            for ch_offset, ch_type_str in enumerate(channels):
                dmx_addr = start_ch + ch_offset
                if ch_type_str in ch_vals:
                    result[universe][dmx_addr] = ch_vals[ch_type_str]

        return result

    def _apply_expression_matrix(
        self,
        merged: Dict[int, Dict[str, float]],
        t: float,
    ) -> Dict[int, Dict[str, float]]:
        """应用表现力矩阵，调整各通道的表现强度。

        设计决定（module-12 审计）：这是 DeepAquaOrchestrator 降级时的旧引擎回退路径。
        主输出路径是 DeepAquaOrchestrator.tick → deep_aqua_core.param_drivers.ExpressionMatrixDriver，
        由 expression_style + motor/pump/valve/light_weight 驱动，与本函数无关。
        本函数按 ch_type 使用 self._params 中的硬编码权重（motor/pump/valve/light_weight）直接缩放；
        src/engine/expression_matrix.py 的 ExpressionMatrix.compute_*_value 计算层为历史死代码，
        已删除，其 set_style/set_dim_weight/set_conflict_strategy 不影响本路径输出。
        电机通道绕中位 128 缩放（128 + (val-128)*weight），避免中性点漂移。
        """
        try:
            result = {}
            for fix_idx, ch_vals in merged.items():
                new_vals = {}
                for ch_type, raw_val in ch_vals.items():
                    # 根据通道类型应用对应的表现力权重
                    if ch_type == 'pump':
                        weight = float(self._params.get('pump_weight', 0.6))
                        new_vals[ch_type] = raw_val * weight
                    elif ch_type == 'valve':
                        weight = float(self._params.get('valve_weight', 0.5))
                        new_vals[ch_type] = raw_val * weight
                    elif ch_type.startswith('motor'):
                        weight = float(self._params.get('motor_weight', 0.7))
                        # 电机：中位128，偏移量乘以权重
                        offset = raw_val - 128.0
                        new_vals[ch_type] = 128.0 + offset * weight
                    elif ch_type in ('dimmer', 'red', 'green', 'blue', 'white'):
                        weight = float(self._params.get('light_weight', 0.4))
                        new_vals[ch_type] = raw_val * weight
                    else:
                        new_vals[ch_type] = raw_val
                result[fix_idx] = new_vals
            return result
        except Exception:
            return merged

    def _write_to_nm(
        self,
        dmx_output: Dict[int, Dict[int, float]],
        engine_blend: float = 1.0,
    ) -> None:
        """
        将 DMX 值写入 NetworkManager 的 effect_auto 层。
        engine_blend: 引擎输出的混合比例（1.0=全AI，0.0=不写入）
        """
        try:
            from ..network.network_manager import NetworkManager
            nm = NetworkManager.instance()
        except ImportError:
            try:
                from src.network.network_manager import NetworkManager
                nm = NetworkManager.instance()
            except Exception:
                return

        for universe, addr_vals in dmx_output.items():
            if not addr_vals:
                continue
            # 稀疏写入
            channels = {
                addr: int(np.clip(val * engine_blend, 0, 255))
                for addr, val in addr_vals.items()
            }
            try:
                nm.update_layer_sparse(self.LAYER_NAME, universe, channels)
            except Exception as e:
                log.debug("NM写入失败: %s", e)

    def _clear_nm_layer(self) -> None:
        """清空 NM effect_auto 层的编舞输出"""
        try:
            from ..network.network_manager import NetworkManager
            nm = NetworkManager.instance()
        except ImportError:
            try:
                from src.network.network_manager import NetworkManager
                nm = NetworkManager.instance()
            except Exception:
                return
        try:
            nm.clear_layer(self.LAYER_NAME)
        except Exception:
            log.debug("deep_choreography: clear_layer failed")
            pass

    # ── 手动控制 ─────────────────────────────────────────────────────────────

    def force_atom(self, atom_type_value: str, voice_id: int = 0) -> None:
        """强制指定声部切换到特定动作元（手动触发）"""
        if self._scheduler is None:
            return
        try:
            atom = AtomType(atom_type_value)
            self._scheduler.force_voice_atom(voice_id, atom)
        except (ValueError, Exception) as e:
            log.debug("force_atom 失败: %s", e)

    # ── 状态查询 ─────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        status: Dict[str, Any] = {
            'running': self._running,
            'frame_count': self._frame_count,
            'audio_rms': self._audio_rms,
            'audio_bpm': self._audio_bpm,
            'audio_beat_phase': self._audio_beat_phase,
            'audio_bands': self._audio_bands,
            'session_time': time.time() - self._t_start if self._running else 0.0,
        }

        if self._scheduler:
            sched_status = self._scheduler.get_status()
            status.update({
                'voice_count': sched_status.get('voice_count', 0),
                'voices': sched_status.get('voices', []),
            })

        # 修复：energy / tension 必须始终返回，否则 UI 表现为“不显示”。
        # 优先取 MusicContext；没有时用 audio_rms+bands 作轻量估算。
        energy_est: float = 0.0
        tension_est: float = 0.0
        try:
            rms = float(self._audio_rms or 0.0)
            bands = list(self._audio_bands or [])
            if bands:
                low = sum(bands[:max(1, len(bands)//3)]) / max(1, len(bands)//3)
                high = sum(bands[2*len(bands)//3:]) / max(1, len(bands) - 2*len(bands)//3)
            else:
                low = high = 0.0
            # energy：以 RMS 为主，低频能量作加成；映射到 [0,1]
            energy_est = max(0.0, min(1.0, rms * 6.0 + low * 0.3))
            # tension：高/低频能量比 + RMS 调制
            denom = max(1e-3, low + high)
            tension_est = max(0.0, min(1.0, (high / denom) * 0.7 + rms * 1.5))
        except Exception:
            log.debug("deep_choreography: energy/tension estimation failed")
            pass
        # 先写入估算值，这样即使下面 MusicContext 分支异常也有双保险
        status['energy'] = float(energy_est)
        status['tension'] = float(tension_est)
        status.setdefault('section', 'unknown')
        status.setdefault('phrase_role', 'neutral')
        status.setdefault('in_buildup', False)
        status.setdefault('buildup_progress', 0.0)
        status.setdefault('is_climax', False)
        status.setdefault('bpm', self._audio_bpm)

        if self._music_ctx is not None:
            try:
                ctx_energy = getattr(self._music_ctx, 'energy_position', None)
                ctx_tension = getattr(self._music_ctx, 'tension_level', None)
                status.update({
                    'section': getattr(self._music_ctx, 'section_label', 'unknown'),
                    'phrase_role': getattr(
                        getattr(self._music_ctx, 'phrase_role', None),
                        'value', 'neutral'
                    ),
                    'in_buildup': getattr(self._music_ctx, 'in_buildup', False),
                    'buildup_progress': getattr(self._music_ctx, 'buildup_progress', 0.0),
                    'is_climax': getattr(self._music_ctx, 'is_climax', False),
                    'bpm': getattr(self._music_ctx, 'bpm', self._audio_bpm),
                })
                # MusicContext 不为 None 但未填能量/张力时保留估算值
                if ctx_energy is not None:
                    status['energy'] = float(ctx_energy)
                if ctx_tension is not None:
                    status['tension'] = float(ctx_tension)
            except Exception:
                log.debug("deep_choreography: failed to copy energy/tension from MusicContext")

        if self._channel_set:
            status['fixture_count'] = self._channel_set.fixture_count

        return status

    def get_voice_details(self) -> List[Dict[str, Any]]:
        """获取所有声部的详细状态"""
        if self._scheduler is None:
            return []
        return self._scheduler.get_status().get('voices', [])


# ── 便捷单例 ─────────────────────────────────────────────────────────────────

_global_deep_engine: Optional[DeepChoreographyEngine] = None

def get_deep_choreography_engine() -> DeepChoreographyEngine:
    """获取全局 DeepChoreographyEngine 单例"""
    global _global_deep_engine
    if _global_deep_engine is None:
        _global_deep_engine = DeepChoreographyEngine()
    return _global_deep_engine
