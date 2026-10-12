"""
effect_atom_registry.py — DeepAqua Effect 原子注册表
=====================================================
自动扫描 EffectEngine 全量 preset（442 个），通过规则引擎反推每个 preset
所对应的：
  - ActionAtomSpec  : 动作元规格（类型、最小设备数、拓扑偏好、通道需求）
  - DeviceAtomSpec  : 设备元规格（推荐分组数、空间构型、最小/最大规模）
  - AudioAffinity   : 音频亲和度（哪些音乐特征最适合触发该 preset）

设计原则：
  - 完全自动推断，无需手工标注
  - 推断规则基于 preset 的 category、id 关键词、target_channels、params 结构
  - 所有规格均为"建议值"，AI 编舞时可根据实际设备数动态覆盖
  - 参数范围宽泛，不做主观收窄
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  动作元类型（与 dynamic_aggregation.py 对齐，扩展版）
# ═══════════════════════════════════════════════════════════════════════════

class EffectActionClass(Enum):
    """
    Effect 动作大类——比 ActionAtomType 更粗粒度，用于 preset 分类。
    每个大类对应一组 ActionAtomType 候选。
    """
    WAVE        = "wave"        # 波浪/波形类：连续相位传播
    CHASE       = "chase"       # 追逐/跑动类：单点或多点移动
    MATRIX      = "matrix"      # 矩阵/空间编组类：需要二维布局
    PULSE       = "pulse"       # 脉冲/闪烁类：周期性爆发
    BREATH      = "breath"      # 呼吸/渐变类：缓慢幅度变化
    SPIRAL      = "spiral"      # 螺旋/旋转类：旋转或螺旋路径
    BURST       = "burst"       # 爆发/扩散类：从中心向外扩散
    RIPPLE      = "ripple"      # 涟漪/扩散类：同心圆扩散
    CROSS       = "cross"       # 交叉/对位类：两组或多组交叉运动
    DRAGON      = "dragon"      # 龙摆/串联类：顺序延迟传播
    FOUNTAIN    = "fountain"    # 喷泉/水柱类：垂直高度控制
    AUDIO_SYNC  = "audio_sync"  # 音频同步类：直接响应音频特征
    STATIC      = "static"      # 静态/保持类：无运动
    POSITION    = "position"    # 位置/路径类：电机路径控制
    COLOR       = "color"       # 颜色/色彩类：颜色变化
    FADER       = "fader"       # 推杆/渐变类：单通道渐变


# ═══════════════════════════════════════════════════════════════════════════
#  空间构型偏好
# ═══════════════════════════════════════════════════════════════════════════

class TopologyPreference(Enum):
    """设备元的空间构型偏好"""
    LINEAR      = "linear"      # 线性排列（一维）
    GRID        = "grid"        # 网格排列（二维矩阵）
    RING        = "ring"        # 环形排列
    SYMMETRIC   = "symmetric"   # 对称排列（左右/上下）
    SPLIT       = "split"       # 分组排列（N 等分）
    SCATTER     = "scatter"     # 散点排列（无约束）
    CROSS       = "cross"       # 交叉排列（两轴）
    SPIRAL      = "spiral"      # 螺旋排列
    RADIAL      = "radial"      # 放射状排列
    ANY         = "any"         # 无偏好


# ═══════════════════════════════════════════════════════════════════════════
#  音频亲和度
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AudioAffinity:
    """
    描述一个 preset 对哪些音频特征最有表现力。
    所有值域 [0.0, 1.0]，越高表示越适合该音频状态。
    """
    # 能量亲和度：高能量时更适合
    energy_affinity: float = 0.5
    # 节奏亲和度：强节奏时更适合
    rhythm_affinity: float = 0.5
    # 张力亲和度：高张力时更适合
    tension_affinity: float = 0.5
    # 旋律亲和度：旋律突出时更适合
    melody_affinity: float = 0.5
    # BPM 亲和度：高 BPM 时更适合（0=慢速，1=快速）
    bpm_affinity: float = 0.5
    # 适合的乐句角色
    phrase_roles: List[str] = field(default_factory=lambda: ["neutral"])
    # 是否适合高潮段落
    climax_suitable: bool = False
    # 是否适合 build-up
    buildup_suitable: bool = False
    # 是否适合过渡段
    transition_suitable: bool = False


# ═══════════════════════════════════════════════════════════════════════════
#  动作元规格
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ActionAtomSpec:
    """
    从 preset 反推的动作元规格。
    描述该 preset 作为"动作元"时的行为特征。
    """
    action_class: EffectActionClass
    # 最小设备数（低于此数量无法触发，需降级）
    min_devices: int = 1
    # 推荐设备数（最佳表现力的设备数量）
    recommended_devices: int = 4
    # 最大有效设备数（超过此数量边际效益递减）
    max_devices: int = 64
    # 是否需要偶数设备（如交叉类）
    requires_even: bool = False
    # 是否需要正方形矩阵（如矩阵类）
    requires_square: bool = False
    # 是否需要最小列数（矩阵类）
    min_cols: int = 1
    # 推荐分组数（如 2 组交叉、4 组脉冲）
    recommended_groups: int = 1
    # 空间构型偏好
    topology_preference: TopologyPreference = TopologyPreference.ANY
    # 需要的通道类型
    required_channels: List[str] = field(default_factory=list)
    # 关键参数（需要从音乐上下文调制的参数名）
    key_params: List[str] = field(default_factory=lambda: ["speed"])
    # 变奏维度（哪些参数适合做结构性变奏）
    variation_dims: List[str] = field(default_factory=lambda: ["speed", "phase_step"])
    # 音频亲和度
    audio_affinity: AudioAffinity = field(default_factory=AudioAffinity)
    # 降级候选（设备不足时的备选 preset_id）
    fallback_presets: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  设备元规格
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceAtomSpec:
    """
    从 preset 反推的设备元规格。
    描述该 preset 对设备元的空间构型要求。
    """
    # 推荐的设备元数量（一个动作元包含几个设备元）
    atom_count: int = 1
    # 每个设备元的推荐大小（设备数）
    atom_size: int = 4
    # 最小设备元大小
    min_atom_size: int = 1
    # 空间构型算子（见 DeviceAtomGeometry）
    geometry_op: str = "linear"
    # 是否需要空间对称
    needs_symmetry: bool = False
    # 是否需要均匀间隔
    needs_uniform_spacing: bool = False
    # 是否支持动态合并（运行时可以把多个小组合并成大组）
    supports_merge: bool = True
    # 是否支持动态拆分（运行时可以把大组拆分成小组）
    supports_split: bool = True
    # 推荐的空间排列方向（horizontal/vertical/radial/any）
    preferred_direction: str = "any"
    # 矩阵列数（矩阵类 preset 专用）
    matrix_cols: int = 0
    # 矩阵行数（矩阵类 preset 专用）
    matrix_rows: int = 0


# ═══════════════════════════════════════════════════════════════════════════
#  完整的 Preset 原子规格
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PresetAtomSpec:
    """单个 preset 的完整原子规格（动作元 + 设备元 + 音频亲和度）"""
    preset_id: str
    preset_name: str
    category: str
    target_channels: List[str]
    action_spec: ActionAtomSpec
    device_spec: DeviceAtomSpec
    # 原始参数列表（key: default_value）
    param_defaults: Dict[str, Any] = field(default_factory=dict)
    # 参数范围（key: (min, max)）
    param_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
#  规则推断引擎
# ═══════════════════════════════════════════════════════════════════════════

class _InferenceEngine:
    """
    基于 preset 元数据的规则推断引擎。
    通过 category、id 关键词、target_channels、params 推断动作元/设备元规格。
    """

    # ── Category → ActionClass 映射 ──────────────────────────────────────
    CATEGORY_TO_CLASS: Dict[str, EffectActionClass] = {
        "Wave":         EffectActionClass.WAVE,
        "Chase":        EffectActionClass.CHASE,
        "Matrix":       EffectActionClass.MATRIX,
        "Fader":        EffectActionClass.FADER,
        "Joystick":     EffectActionClass.POSITION,
        "Audio":        EffectActionClass.AUDIO_SYNC,
        "Color":        EffectActionClass.COLOR,
        "Fountain":     EffectActionClass.FOUNTAIN,
        "Position":     EffectActionClass.POSITION,
        # 中文 category
        "泵/波浪":      EffectActionClass.WAVE,
        "泵/渐变":      EffectActionClass.BREATH,
        "泵/矩阵":      EffectActionClass.MATRIX,
        "泵/脉冲":      EffectActionClass.PULSE,
        "泵/跑泉":      EffectActionClass.CHASE,
        "灯光/亮度":    EffectActionClass.FADER,
        "灯光/呼吸":    EffectActionClass.BREATH,
        "灯光/开合":    EffectActionClass.FADER,
        "灯光/彩色":    EffectActionClass.COLOR,
        "灯光/矩阵":    EffectActionClass.MATRIX,
        "灯光/跑灯":    EffectActionClass.CHASE,
        "灯光/闪烁":    EffectActionClass.PULSE,
        "灯光/颜色":    EffectActionClass.COLOR,
        "电机/交叉":    EffectActionClass.CROSS,
        "电机/圆摇":    EffectActionClass.SPIRAL,
        "电机/延时":    EffectActionClass.DRAGON,
        "电机/开合":    EffectActionClass.BREATH,
        "电机/形象":    EffectActionClass.POSITION,
        "电机/扇形":    EffectActionClass.BREATH,
        "电机/旋转":    EffectActionClass.SPIRAL,
        "电机/波形":    EffectActionClass.WAVE,
        "电机/渐变":    EffectActionClass.BREATH,
        "电机/矩阵":    EffectActionClass.MATRIX,
        "电机/脉冲":    EffectActionClass.PULSE,
        "电机/造型":    EffectActionClass.POSITION,
        "电机/高级":    EffectActionClass.DRAGON,
        "电机/龙摆":    EffectActionClass.DRAGON,
        "矩阵/空间编组": EffectActionClass.MATRIX,
        "阀门/开合":    EffectActionClass.FADER,
        "阀门/渐变":    EffectActionClass.BREATH,
        "阀门/跑泉":    EffectActionClass.CHASE,
    }

    # ── ID 关键词 → ActionClass 精细修正 ─────────────────────────────────
    ID_KEYWORD_CLASS: List[Tuple[str, EffectActionClass]] = [
        ("spiral",      EffectActionClass.SPIRAL),
        ("burst",       EffectActionClass.BURST),
        ("explode",     EffectActionClass.BURST),
        ("explosion",   EffectActionClass.BURST),
        ("ripple",      EffectActionClass.RIPPLE),
        ("concentric",  EffectActionClass.RIPPLE),
        ("dragon",      EffectActionClass.DRAGON),
        ("chase",       EffectActionClass.CHASE),
        ("cross",       EffectActionClass.CROSS),
        ("breath",      EffectActionClass.BREATH),
        ("breathe",     EffectActionClass.BREATH),
        ("pulse",       EffectActionClass.PULSE),
        ("strobe",      EffectActionClass.PULSE),
        ("flash",       EffectActionClass.PULSE),
        ("wave",        EffectActionClass.WAVE),
        ("scan",        EffectActionClass.CHASE),
        ("fountain",    EffectActionClass.FOUNTAIN),
        ("audio",       EffectActionClass.AUDIO_SYNC),
        ("bpm",         EffectActionClass.AUDIO_SYNC),
        ("color",       EffectActionClass.COLOR),
        ("rainbow",     EffectActionClass.COLOR),
        ("matrix",      EffectActionClass.MATRIX),
        ("grid",        EffectActionClass.MATRIX),
        ("circle",      EffectActionClass.SPIRAL),
        ("rotation",    EffectActionClass.SPIRAL),
        ("figure8",     EffectActionClass.POSITION),
        ("lissajous",   EffectActionClass.POSITION),
        ("random",      EffectActionClass.PULSE),
        ("gradient",    EffectActionClass.BREATH),
        ("fade",        EffectActionClass.BREATH),
        ("fader",       EffectActionClass.FADER),
        ("alternate",   EffectActionClass.CROSS),
        ("dual",        EffectActionClass.CROSS),
        ("triple",      EffectActionClass.DRAGON),
        ("cascade",     EffectActionClass.DRAGON),
        ("delay",       EffectActionClass.DRAGON),
        ("sequential",  EffectActionClass.DRAGON),
        ("comet",       EffectActionClass.CHASE),
        ("tail",        EffectActionClass.CHASE),
        ("bounce",      EffectActionClass.WAVE),
        ("sawtooth",    EffectActionClass.WAVE),
        ("triangle",    EffectActionClass.WAVE),
        ("sine",        EffectActionClass.WAVE),
        ("fm",          EffectActionClass.WAVE),
        ("compound",    EffectActionClass.WAVE),
    ]

    # ── ActionClass → 设备数规格 ──────────────────────────────────────────
    CLASS_DEVICE_SPEC: Dict[EffectActionClass, Dict[str, Any]] = {
        EffectActionClass.WAVE:       {"min": 2,  "rec": 8,  "max": 256, "topo": TopologyPreference.LINEAR},
        EffectActionClass.CHASE:      {"min": 2,  "rec": 6,  "max": 256, "topo": TopologyPreference.LINEAR},
        EffectActionClass.MATRIX:     {"min": 4,  "rec": 16, "max": 256, "topo": TopologyPreference.GRID,    "square": True},
        EffectActionClass.PULSE:      {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.BREATH:     {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.SPIRAL:     {"min": 3,  "rec": 8,  "max": 256, "topo": TopologyPreference.RING},
        EffectActionClass.BURST:      {"min": 3,  "rec": 8,  "max": 256, "topo": TopologyPreference.RADIAL},
        EffectActionClass.RIPPLE:     {"min": 3,  "rec": 9,  "max": 256, "topo": TopologyPreference.RING},
        EffectActionClass.CROSS:      {"min": 2,  "rec": 4,  "max": 256, "topo": TopologyPreference.CROSS,   "even": True},
        EffectActionClass.DRAGON:     {"min": 3,  "rec": 8,  "max": 256, "topo": TopologyPreference.LINEAR},
        EffectActionClass.FOUNTAIN:   {"min": 2,  "rec": 6,  "max": 256, "topo": TopologyPreference.LINEAR},
        EffectActionClass.AUDIO_SYNC: {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.STATIC:     {"min": 1,  "rec": 1,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.POSITION:   {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.COLOR:      {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
        EffectActionClass.FADER:      {"min": 1,  "rec": 4,  "max": 256, "topo": TopologyPreference.ANY},
    }

    # ── ActionClass → 音频亲和度模板 ─────────────────────────────────────
    CLASS_AUDIO_AFFINITY: Dict[EffectActionClass, Dict[str, Any]] = {
        EffectActionClass.WAVE:       {"energy": 0.5, "rhythm": 0.6, "tension": 0.4, "melody": 0.7, "bpm": 0.5, "climax": False, "buildup": True},
        EffectActionClass.CHASE:      {"energy": 0.7, "rhythm": 0.8, "tension": 0.5, "melody": 0.4, "bpm": 0.7, "climax": False, "buildup": True},
        EffectActionClass.MATRIX:     {"energy": 0.6, "rhythm": 0.5, "tension": 0.6, "melody": 0.5, "bpm": 0.5, "climax": True,  "buildup": False},
        EffectActionClass.PULSE:      {"energy": 0.8, "rhythm": 0.9, "tension": 0.6, "melody": 0.3, "bpm": 0.8, "climax": True,  "buildup": True},
        EffectActionClass.BREATH:     {"energy": 0.3, "rhythm": 0.3, "tension": 0.5, "melody": 0.8, "bpm": 0.2, "climax": False, "buildup": False},
        EffectActionClass.SPIRAL:     {"energy": 0.7, "rhythm": 0.6, "tension": 0.7, "melody": 0.5, "bpm": 0.6, "climax": True,  "buildup": True},
        EffectActionClass.BURST:      {"energy": 0.9, "rhythm": 0.7, "tension": 0.8, "melody": 0.3, "bpm": 0.7, "climax": True,  "buildup": False},
        EffectActionClass.RIPPLE:     {"energy": 0.5, "rhythm": 0.5, "tension": 0.6, "melody": 0.6, "bpm": 0.4, "climax": False, "buildup": True},
        EffectActionClass.CROSS:      {"energy": 0.7, "rhythm": 0.7, "tension": 0.7, "melody": 0.5, "bpm": 0.6, "climax": True,  "buildup": True},
        EffectActionClass.DRAGON:     {"energy": 0.6, "rhythm": 0.7, "tension": 0.5, "melody": 0.6, "bpm": 0.6, "climax": False, "buildup": True},
        EffectActionClass.FOUNTAIN:   {"energy": 0.5, "rhythm": 0.5, "tension": 0.4, "melody": 0.7, "bpm": 0.4, "climax": False, "buildup": False},
        EffectActionClass.AUDIO_SYNC: {"energy": 0.9, "rhythm": 0.9, "tension": 0.7, "melody": 0.6, "bpm": 0.8, "climax": True,  "buildup": True},
        EffectActionClass.STATIC:     {"energy": 0.1, "rhythm": 0.1, "tension": 0.2, "melody": 0.3, "bpm": 0.1, "climax": False, "buildup": False},
        EffectActionClass.POSITION:   {"energy": 0.5, "rhythm": 0.4, "tension": 0.5, "melody": 0.7, "bpm": 0.4, "climax": False, "buildup": False},
        EffectActionClass.COLOR:      {"energy": 0.4, "rhythm": 0.4, "tension": 0.5, "melody": 0.8, "bpm": 0.3, "climax": False, "buildup": False},
        EffectActionClass.FADER:      {"energy": 0.4, "rhythm": 0.3, "tension": 0.4, "melody": 0.6, "bpm": 0.3, "climax": False, "buildup": False},
    }

    def infer(self, preset_id: str, preset_name: str, category: str,
              target_channels: List[str], params: List[Any]) -> PresetAtomSpec:
        """推断单个 preset 的完整原子规格"""

        # 1. 推断动作大类
        action_class = self._infer_action_class(preset_id, category)

        # 2. 提取参数信息
        param_defaults: Dict[str, Any] = {}
        param_ranges: Dict[str, Tuple[float, float]] = {}
        param_keys: List[str] = []
        for ps in params:
            key = getattr(ps, "key", "")
            default = getattr(ps, "default", None)
            min_val = getattr(ps, "min_val", 0.0)
            max_val = getattr(ps, "max_val", 1.0)
            param_defaults[key] = default
            param_ranges[key] = (float(min_val), float(max_val))
            param_keys.append(key)

        # 3. 推断设备数规格（结合 params 中的分组参数）
        action_spec = self._infer_action_spec(
            preset_id, action_class, param_keys, param_defaults, param_ranges, target_channels
        )

        # 4. 推断设备元构型规格
        device_spec = self._infer_device_spec(
            preset_id, action_class, param_keys, param_defaults
        )

        return PresetAtomSpec(
            preset_id=preset_id,
            preset_name=preset_name,
            category=category,
            target_channels=target_channels,
            action_spec=action_spec,
            device_spec=device_spec,
            param_defaults=param_defaults,
            param_ranges=param_ranges,
        )

    def _infer_action_class(self, preset_id: str, category: str) -> EffectActionClass:
        """推断动作大类"""
        # 先从 category 查
        ac = self.CATEGORY_TO_CLASS.get(category)
        if ac:
            # 再用 id 关键词精细修正
            pid_lower = preset_id.lower()
            for kw, refined_ac in self.ID_KEYWORD_CLASS:
                if kw in pid_lower:
                    return refined_ac
            return ac
        # 纯靠 id 关键词
        pid_lower = preset_id.lower()
        for kw, ac in self.ID_KEYWORD_CLASS:
            if kw in pid_lower:
                return ac
        return EffectActionClass.WAVE  # 默认

    def _infer_action_spec(
        self,
        preset_id: str,
        action_class: EffectActionClass,
        param_keys: List[str],
        param_defaults: Dict[str, Any],
        param_ranges: Dict[str, Tuple[float, float]],
        target_channels: List[str],
    ) -> ActionAtomSpec:
        """推断动作元规格"""
        base = self.CLASS_DEVICE_SPEC.get(action_class, {"min": 1, "rec": 4, "max": 256, "topo": TopologyPreference.ANY})
        audio_tmpl = self.CLASS_AUDIO_AFFINITY.get(action_class, {})

        min_dev = base["min"]
        rec_dev = base["rec"]
        max_dev = base["max"]
        topo = base.get("topo", TopologyPreference.ANY)
        requires_even = base.get("even", False)
        requires_square = base.get("square", False)

        # 从 params 中精细推断设备数
        if "groups" in param_keys:
            groups = int(param_defaults.get("groups", 2))
            min_dev = max(min_dev, groups)
            rec_dev = max(rec_dev, groups * 2)
            requires_even = True

        if "cols" in param_keys:
            cols = int(param_defaults.get("cols", 4))
            min_dev = max(min_dev, cols * 2)
            rec_dev = max(rec_dev, cols * cols)
            requires_square = True

        if "n_group" in param_keys or "n_groups" in param_keys:
            ng = int(param_defaults.get("n_group", param_defaults.get("n_groups", 2)))
            min_dev = max(min_dev, ng)
            rec_dev = max(rec_dev, ng * 3)

        if "layers" in param_keys:
            layers = int(param_defaults.get("layers", 3))
            min_dev = max(min_dev, layers)
            rec_dev = max(rec_dev, layers * 2)

        # 推断关键参数
        key_params = []
        variation_dims = []
        for k in ["speed", "phase_step", "phase_offset", "phase_diff",
                  "phase_spread", "width", "density", "rate", "freq", "bpm"]:
            if k in param_keys:
                key_params.append(k)
        for k in ["speed", "phase_step", "phase_offset", "width"]:
            if k in param_keys:
                variation_dims.append(k)
        if not key_params:
            key_params = ["speed"]
        if not variation_dims:
            variation_dims = ["speed"]

        # 推断降级候选
        fallback_presets = self._get_fallback_presets(action_class)

        # 构建音频亲和度
        affinity = AudioAffinity(
            energy_affinity=audio_tmpl.get("energy", 0.5),
            rhythm_affinity=audio_tmpl.get("rhythm", 0.5),
            tension_affinity=audio_tmpl.get("tension", 0.5),
            melody_affinity=audio_tmpl.get("melody", 0.5),
            bpm_affinity=audio_tmpl.get("bpm", 0.5),
            climax_suitable=audio_tmpl.get("climax", False),
            buildup_suitable=audio_tmpl.get("buildup", False),
            transition_suitable=action_class in (
                EffectActionClass.BREATH, EffectActionClass.FADER,
                EffectActionClass.WAVE, EffectActionClass.RIPPLE,
            ),
            phrase_roles=self._infer_phrase_roles(action_class),
        )

        # 根据 id 关键词微调亲和度
        pid = preset_id.lower()
        if "audio" in pid or "bpm" in pid or "beat" in pid:
            affinity.energy_affinity = min(1.0, affinity.energy_affinity + 0.2)
            affinity.rhythm_affinity = min(1.0, affinity.rhythm_affinity + 0.2)
            affinity.climax_suitable = True
        if "breath" in pid or "breathe" in pid or "gradient" in pid:
            affinity.energy_affinity = max(0.0, affinity.energy_affinity - 0.2)
            affinity.melody_affinity = min(1.0, affinity.melody_affinity + 0.2)
        if "burst" in pid or "explode" in pid or "explosion" in pid:
            affinity.energy_affinity = min(1.0, affinity.energy_affinity + 0.3)
            affinity.climax_suitable = True
        if "fountain" in pid or "water" in pid:
            affinity.melody_affinity = min(1.0, affinity.melody_affinity + 0.1)
            affinity.tension_affinity = max(0.0, affinity.tension_affinity - 0.1)

        return ActionAtomSpec(
            action_class=action_class,
            min_devices=min_dev,
            recommended_devices=rec_dev,
            max_devices=max_dev,
            requires_even=requires_even,
            requires_square=requires_square,
            min_cols=int(param_defaults.get("cols", 1)),
            recommended_groups=int(param_defaults.get("groups", param_defaults.get("n_group", 1))),
            topology_preference=topo,
            required_channels=list(target_channels[:3]),
            key_params=key_params,
            variation_dims=variation_dims,
            audio_affinity=affinity,
            fallback_presets=fallback_presets,
        )

    def _infer_device_spec(
        self,
        preset_id: str,
        action_class: EffectActionClass,
        param_keys: List[str],
        param_defaults: Dict[str, Any],
    ) -> DeviceAtomSpec:
        """推断设备元构型规格"""
        atom_count = 1
        atom_size = 4
        geometry_op = "linear"
        needs_symmetry = False
        needs_uniform = False
        supports_merge = True
        supports_split = True
        preferred_dir = "any"
        matrix_cols = 0
        matrix_rows = 0

        pid = preset_id.lower()

        # 矩阵类：需要网格构型
        if action_class == EffectActionClass.MATRIX or "matrix" in pid:
            cols = int(param_defaults.get("cols", 4))
            matrix_cols = cols
            matrix_rows = cols  # 默认正方形
            geometry_op = "grid"
            atom_count = 1
            atom_size = cols * cols
            needs_uniform = True

        # 交叉类：需要对称分组
        elif action_class == EffectActionClass.CROSS or "cross" in pid or "alternate" in pid:
            groups = int(param_defaults.get("groups", 2))
            atom_count = groups
            atom_size = 4
            geometry_op = "symmetric"
            needs_symmetry = True

        # 龙摆/串联类：线性均匀间隔
        elif action_class == EffectActionClass.DRAGON or "dragon" in pid or "cascade" in pid:
            atom_count = 1
            atom_size = 8
            geometry_op = "linear_uniform"
            needs_uniform = True
            preferred_dir = "horizontal"

        # 螺旋/环形类：环形构型
        elif action_class == EffectActionClass.SPIRAL or "spiral" in pid or "circle" in pid or "ring" in pid:
            atom_count = 1
            atom_size = 8
            geometry_op = "ring"
            needs_uniform = True

        # 爆发/涟漪类：放射状
        elif action_class in (EffectActionClass.BURST, EffectActionClass.RIPPLE):
            atom_count = 1
            atom_size = 6
            geometry_op = "radial"
            needs_uniform = True

        # 喷泉类：线性垂直
        elif action_class == EffectActionClass.FOUNTAIN or "fountain" in pid:
            atom_count = 1
            atom_size = 4
            geometry_op = "linear_uniform"
            preferred_dir = "vertical"

        # 脉冲/闪烁类：可分组
        elif action_class == EffectActionClass.PULSE:
            groups = int(param_defaults.get("groups", 1))
            atom_count = max(1, groups)
            atom_size = 3
            geometry_op = "split_equal"

        # 呼吸/渐变类：无约束
        elif action_class in (EffectActionClass.BREATH, EffectActionClass.FADER):
            atom_count = 1
            atom_size = 4
            geometry_op = "any"
            supports_merge = True
            supports_split = True

        # 追逐类：线性
        elif action_class == EffectActionClass.CHASE:
            atom_count = 1
            atom_size = 6
            geometry_op = "linear"

        # 波浪类：线性或环形
        elif action_class == EffectActionClass.WAVE:
            atom_count = 1
            atom_size = 6
            geometry_op = "linear"

        return DeviceAtomSpec(
            atom_count=atom_count,
            atom_size=atom_size,
            min_atom_size=1,
            geometry_op=geometry_op,
            needs_symmetry=needs_symmetry,
            needs_uniform_spacing=needs_uniform,
            supports_merge=supports_merge,
            supports_split=supports_split,
            preferred_direction=preferred_dir,
            matrix_cols=matrix_cols,
            matrix_rows=matrix_rows,
        )

    def _infer_phrase_roles(self, action_class: EffectActionClass) -> List[str]:
        """推断适合的乐句角色"""
        role_map = {
            EffectActionClass.WAVE:       ["antecedent", "neutral"],
            EffectActionClass.CHASE:      ["antecedent", "buildup"],
            EffectActionClass.MATRIX:     ["climax", "neutral"],
            EffectActionClass.PULSE:      ["climax", "antecedent"],
            EffectActionClass.BREATH:     ["consequent", "neutral"],
            EffectActionClass.SPIRAL:     ["climax", "antecedent"],
            EffectActionClass.BURST:      ["climax"],
            EffectActionClass.RIPPLE:     ["antecedent", "consequent"],
            EffectActionClass.CROSS:      ["climax", "antecedent"],
            EffectActionClass.DRAGON:     ["antecedent", "neutral"],
            EffectActionClass.FOUNTAIN:   ["neutral", "consequent"],
            EffectActionClass.AUDIO_SYNC: ["climax", "antecedent"],
            EffectActionClass.STATIC:     ["neutral"],
            EffectActionClass.POSITION:   ["neutral", "consequent"],
            EffectActionClass.COLOR:      ["neutral", "consequent"],
            EffectActionClass.FADER:      ["consequent", "neutral"],
        }
        return role_map.get(action_class, ["neutral"])

    def _get_fallback_presets(self, action_class: EffectActionClass) -> List[str]:
        """获取降级候选 preset"""
        fallback_map = {
            EffectActionClass.MATRIX:   ["wave_sine", "chase_single"],
            EffectActionClass.SPIRAL:   ["wave_sine", "chase_single"],
            EffectActionClass.BURST:    ["pulse_all", "chase_single"],
            EffectActionClass.RIPPLE:   ["wave_sine", "fader_breath"],
            EffectActionClass.CROSS:    ["wave_dual", "chase_alternate"],
            EffectActionClass.DRAGON:   ["wave_sine", "chase_fade_tail"],
            EffectActionClass.FOUNTAIN: ["fader_breath", "wave_sine"],
        }
        return fallback_map.get(action_class, ["fader_breath"])


# ═══════════════════════════════════════════════════════════════════════════
#  EffectAtomRegistry 主类
# ═══════════════════════════════════════════════════════════════════════════

class EffectAtomRegistry:
    """
    DeepAqua Effect 原子注册表。

    自动扫描 EffectEngine 全量 preset，建立完整的动作元/设备元规格数据库。
    供 DynamicAggregationScheduler 和 DeviceGroupSelector 查询。

    使用方式：
        registry = EffectAtomRegistry.build_from_engine(effect_engine)
        spec = registry.get_spec("wave_sine")
        candidates = registry.query_by_audio(energy=0.8, rhythm=0.7, n_devices=16)
    """

    def __init__(self):
        self._specs: Dict[str, PresetAtomSpec] = {}
        self._by_class: Dict[EffectActionClass, List[str]] = {}
        self._by_channel: Dict[str, List[str]] = {}
        self._by_category: Dict[str, List[str]] = {}
        self._inference = _InferenceEngine()

    # ─────────────────────────────────────────────────────────────────────
    #  构建方法
    # ─────────────────────────────────────────────────────────────────────

    @classmethod
    def build_from_engine(cls, effect_engine: Any) -> "EffectAtomRegistry":
        """
        从 EffectEngine 实例自动构建注册表。
        扫描所有已注册的 preset，推断其动作元/设备元规格。
        """
        registry = cls()
        try:
            presets = effect_engine.all_presets()
        except Exception as e:
            log.warning("无法获取 preset 列表: %s", e)
            return registry

        ok = 0
        fail = 0
        for p in presets:
            try:
                spec = registry._inference.infer(
                    preset_id=p.id,
                    preset_name=p.name,
                    category=p.category,
                    target_channels=list(p.target_channels),
                    params=p.params,
                )
                registry._register(spec)
                ok += 1
            except Exception as e:
                log.debug("推断 preset %s 失败: %s", p.id, e)
                fail += 1

        log.info(
            "EffectAtomRegistry 构建完成: %d 个 preset 成功, %d 个失败",
            ok, fail,
        )
        return registry

    @classmethod
    def build_from_json(cls, json_data: List[Dict[str, Any]]) -> "EffectAtomRegistry":
        """从 JSON 数据（all_presets.json 格式）构建注册表"""
        registry = cls()

        class _FakeParamSpec:
            def __init__(self, d):
                self.key = d.get("key", "")
                self.default = d.get("default", 0)
                self.min_val = d.get("min_val", 0.0)
                self.max_val = d.get("max_val", 1.0)
                self.choices = d.get("choices", [])

        ok = 0
        for p in json_data:
            try:
                params = [_FakeParamSpec(ps) for ps in p.get("params", [])]
                spec = registry._inference.infer(
                    preset_id=p["id"],
                    preset_name=p.get("name", p["id"]),
                    category=p.get("category", ""),
                    target_channels=p.get("target_channels", []),
                    params=params,
                )
                registry._register(spec)
                ok += 1
            except Exception as e:
                log.debug("推断 preset %s 失败: %s", p.get("id", "?"), e)

        log.info("EffectAtomRegistry 从 JSON 构建完成: %d 个 preset", ok)
        return registry

    def scan_from_engine(self, effect_engine: Any = None) -> int:
        """
        将一个 EffectEngine 的全部 preset 扫描进当前注册表（实例方法）。
        • 如果未传 effect_engine，会尝试用 src.ui.effect_engine.EffectEngine() 新建一个；
        • 与 build_from_engine 的区别：build_* 是 classmethod 返回新对象，
          scan_from_engine 是把 preset 注入 *现有* registry，便于 orchestrator
          在 __init__ 已经持有 self._effect_registry 的情况下补全数据。
        Returns: 成功注册的 preset 数。
        """
        if effect_engine is None:
            try:
                from src.ui.effect_engine import EffectEngine as _EE  # type: ignore
            except Exception:
                try:
                    from ...ui.effect_engine import EffectEngine as _EE  # type: ignore
                except Exception:
                    log.warning("scan_from_engine: 无法导入 EffectEngine")
                    return 0
            try:
                effect_engine = _EE()
            except Exception as e:
                log.warning("scan_from_engine: EffectEngine 初始化失败 %s", e)
                return 0
        try:
            presets = effect_engine.all_presets()
        except Exception as e:
            log.warning("scan_from_engine: all_presets 调用失败 %s", e)
            return 0
        ok = 0
        for p in presets:
            try:
                spec = self._inference.infer(
                    preset_id=p.id,
                    preset_name=p.name,
                    category=p.category,
                    target_channels=list(p.target_channels),
                    params=p.params,
                )
                self._register(spec)
                ok += 1
            except Exception as e:
                log.debug("推断 preset %s 失败: %s", getattr(p, 'id', '?'), e)
        log.info("scan_from_engine: 已装载 %d 个 preset", ok)
        return ok

    def _register(self, spec: PresetAtomSpec) -> None:
        """注册一个 preset 规格"""
        self._specs[spec.preset_id] = spec

        # 按 ActionClass 索引
        ac = spec.action_spec.action_class
        self._by_class.setdefault(ac, []).append(spec.preset_id)

        # 按通道索引
        for ch in spec.target_channels:
            self._by_channel.setdefault(ch, []).append(spec.preset_id)

        # 按 category 索引
        self._by_category.setdefault(spec.category, []).append(spec.preset_id)

    # ─────────────────────────────────────────────────────────────────────
    #  查询方法
    # ─────────────────────────────────────────────────────────────────────

    def get_spec(self, preset_id: str) -> Optional[PresetAtomSpec]:
        """获取单个 preset 的完整规格"""
        return self._specs.get(preset_id)

    def get_action_spec(self, preset_id: str) -> Optional[ActionAtomSpec]:
        """获取动作元规格"""
        spec = self._specs.get(preset_id)
        return spec.action_spec if spec else None

    def get_device_spec(self, preset_id: str) -> Optional[DeviceAtomSpec]:
        """获取设备元规格"""
        spec = self._specs.get(preset_id)
        return spec.device_spec if spec else None

    def get_min_devices(self, preset_id: str) -> int:
        """获取 preset 的最小设备数要求"""
        spec = self._specs.get(preset_id)
        return spec.action_spec.min_devices if spec else 1

    def get_recommended_devices(self, preset_id: str) -> int:
        """获取 preset 的推荐设备数"""
        spec = self._specs.get(preset_id)
        return spec.action_spec.recommended_devices if spec else 4

    def list_by_class(self, action_class: EffectActionClass) -> List[str]:
        """列出某动作大类的所有 preset_id"""
        return list(self._by_class.get(action_class, []))

    def list_by_channel(self, channel: str) -> List[str]:
        """列出支持某通道类型的所有 preset_id"""
        return list(self._by_channel.get(channel, []))

    def list_by_category(self, category: str) -> List[str]:
        """列出某 category 的所有 preset_id"""
        return list(self._by_category.get(category, []))

    def query_by_audio(
        self,
        energy: float = 0.5,
        rhythm: float = 0.5,
        tension: float = 0.5,
        melody: float = 0.5,
        n_devices: int = 8,
        channels: Optional[List[str]] = None,
        is_climax: bool = False,
        in_buildup: bool = False,
        phrase_role: str = "neutral",
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        根据音频特征查询最适合的 preset 列表。

        Args:
            energy: 能量 [0, 1]（无上限限制）
            rhythm: 节奏强度 [0, 1]
            tension: 张力 [0, 1]
            melody: 旋律强度 [0, 1]
            n_devices: 当前可用设备数（无上限限制）
            channels: 可用通道类型（None 表示不限制）
            is_climax: 是否高潮
            in_buildup: 是否 build-up
            phrase_role: 乐句角色
            top_k: 返回前 K 个结果

        Returns:
            [(preset_id, score)] 按得分降序排列
        """
        results: List[Tuple[str, float]] = []

        for pid, spec in self._specs.items():
            aspec = spec.action_spec
            aff = aspec.audio_affinity

            # 设备数可行性检查（不满足最低要求则跳过）
            if n_devices < aspec.min_devices:
                continue

            # 通道可行性检查
            if channels:
                ch_ok = any(ch in channels for ch in spec.target_channels)
                if not ch_ok:
                    continue

            # 计算亲和度得分
            score = (
                aff.energy_affinity  * energy  * 0.30 +
                aff.rhythm_affinity  * rhythm  * 0.25 +
                aff.tension_affinity * tension * 0.20 +
                aff.melody_affinity  * melody  * 0.15 +
                aff.bpm_affinity     * 0.10
            )

            # 高潮/build-up 加权
            if is_climax and aff.climax_suitable:
                score *= 1.4
            if in_buildup and aff.buildup_suitable:
                score *= 1.2

            # 乐句角色匹配加权
            if phrase_role in aff.phrase_roles:
                score *= 1.15

            # 设备数匹配加权（推荐设备数越接近越好）
            rec = aspec.recommended_devices
            if rec > 0:
                dev_ratio = min(n_devices, rec) / max(n_devices, rec)
                score *= (0.7 + 0.3 * dev_ratio)

            results.append((pid, score))

        # 排序并返回 top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def check_feasibility(
        self,
        preset_id: str,
        n_available: int,
        available_positions: Optional[List[Tuple[float, float]]] = None,
    ) -> Dict[str, Any]:
        """
        检查 preset 在当前设备条件下的可行性。

        Returns:
            {
                "feasible": bool,
                "reason": str,
                "min_required": int,
                "available": int,
                "recommended": int,
                "quality": float,  # 0~1，越高越好
                "fallback": Optional[str],
            }
        """
        spec = self._specs.get(preset_id)
        if spec is None:
            return {"feasible": False, "reason": "preset 不存在", "quality": 0.0}

        aspec = spec.action_spec
        min_req = aspec.min_devices
        rec = aspec.recommended_devices

        if n_available < min_req:
            # 不可行，返回降级建议
            fallback = None
            for fb_id in aspec.fallback_presets:
                fb_spec = self._specs.get(fb_id)
                if fb_spec and n_available >= fb_spec.action_spec.min_devices:
                    fallback = fb_id
                    break
            return {
                "feasible": False,
                "reason": f"设备不足: 需要 {min_req}，可用 {n_available}",
                "min_required": min_req,
                "available": n_available,
                "recommended": rec,
                "quality": 0.0,
                "fallback": fallback,
            }

        # 可行，计算质量分
        quality = min(1.0, n_available / max(rec, 1))

        # 偶数检查
        if aspec.requires_even and n_available % 2 != 0:
            quality *= 0.9  # 轻微降分，但不阻止

        # 矩阵检查
        if aspec.requires_square:
            import math
            sqrt_n = math.sqrt(n_available)
            if abs(sqrt_n - round(sqrt_n)) > 0.1:
                quality *= 0.85

        return {
            "feasible": True,
            "reason": "ok",
            "min_required": min_req,
            "available": n_available,
            "recommended": rec,
            "quality": float(quality),
            "fallback": None,
        }

    def get_fallback_chain(
        self,
        preset_id: str,
        n_available: int,
        max_depth: int = 5,
    ) -> List[str]:
        """
        获取完整的降级链：从目标 preset 开始，逐级降级直到找到可行的。

        Returns:
            [preset_id, fallback1, fallback2, ...] 第一个可行的 preset
        """
        chain = [preset_id]
        visited: Set[str] = {preset_id}
        current = preset_id

        for _ in range(max_depth):
            result = self.check_feasibility(current, n_available)
            if result["feasible"]:
                return chain
            fb = result.get("fallback")
            if fb is None or fb in visited:
                break
            chain.append(fb)
            visited.add(fb)
            current = fb

        # 最终兜底：返回最简单的 preset
        return chain + ["fader_breath"]

    # ─────────────────────────────────────────────────────────────────────
    #  统计信息
    # ─────────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """返回注册表统计信息"""
        class_counts = {ac.value: len(ids) for ac, ids in self._by_class.items()}
        cat_counts = {cat: len(ids) for cat, ids in self._by_category.items()}
        return {
            "total_presets": len(self._specs),
            "by_action_class": class_counts,
            "by_category": cat_counts,
            "channels_covered": list(self._by_channel.keys()),
        }

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, preset_id: str) -> bool:
        return preset_id in self._specs
