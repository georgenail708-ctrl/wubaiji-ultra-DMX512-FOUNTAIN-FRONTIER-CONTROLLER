"""
expression_matrix.py — 表现力分配矩阵
=======================================
管理每类设备的表现维度权重分配，决定引擎用多深的音乐语义驱动每类设备。

核心概念：
  - 表现维度（ExpressionDim）：每类设备可用的表现方式
  - 表现力风格预设（ExpressionStyle）：内置不同的权重分配策略
  - 冲突解决策略（ConflictStrategy）：多维度叠加时的处理方式
  - 全局表现力总量（master_level）：统一缩放所有输出

设备类型 × 表现维度 = 表现力矩阵
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
#  表现维度定义
# ═══════════════════════════════════════════════════════════════════════════

class ExpressionDim(Enum):
    """所有可用的表现维度"""

    # ── 电机维度 ──
    MOTOR_FIXED_WAVE      = "motor_fixed_wave"      # 固定波形摆动（可预测）
    MOTOR_AUDIO_PATH      = "motor_audio_path"      # 音频实时路径（深度响应）
    MOTOR_PHRASE_REVERSE  = "motor_phrase_reverse"  # 乐句问答方向反转
    MOTOR_BEAT_ACCEL      = "motor_beat_accel"      # 节拍加速/减速
    MOTOR_BUILDUP_EXPAND  = "motor_buildup_expand"  # buildup时展开角度

    # ── 泵维度 ──
    PUMP_BREATH_SYNC      = "pump_breath_sync"      # 整体呼吸（同步涨落）
    PUMP_WAVE_TRAVEL      = "pump_wave_travel"      # 波形跑动（空间传递）
    PUMP_BEAT_GATE        = "pump_beat_gate"        # 节拍间隔开关（打击感）
    PUMP_PHRASE_GATE      = "pump_phrase_gate"      # 乐句边界开关（叙事感）
    PUMP_ENERGY_SCALE     = "pump_energy_scale"     # 能量比例缩放（动态范围）

    # ── 阀门维度 ──
    VALVE_BEAT_SWITCH     = "valve_beat_switch"     # 节拍同步开关
    VALVE_PATTERN_MORPH   = "valve_pattern_morph"   # 空间图案切换
    VALVE_PHRASE_SWITCH   = "valve_phrase_switch"   # 乐句边界切换
    VALVE_RANDOM_JUMP     = "valve_random_jump"     # 随机跳跃
    VALVE_TOPO_CHASE      = "valve_topo_chase"      # 拓扑追逐（沿弧线传递）

    # ── 灯光维度 ──
    LIGHT_BRIGHTNESS_RMS  = "light_brightness_rms"  # 亮度响应能量（RMS）
    LIGHT_HUE_BAND        = "light_hue_band"        # 色相跟随频段
    LIGHT_SAT_TENSION     = "light_sat_tension"     # 饱和度跟随紧张度
    LIGHT_STROBE_BEAT     = "light_strobe_beat"     # 频闪跟随节拍
    LIGHT_COLOR_PHRASE    = "light_color_phrase"    # 色彩随乐句情绪变化


# ═══════════════════════════════════════════════════════════════════════════
#  冲突解决策略
# ═══════════════════════════════════════════════════════════════════════════

class ConflictStrategy(Enum):
    """多维度贡献叠加时的冲突解决策略"""
    ADDITIVE_SOFT    = "additive_soft"    # 叠加 + 软饱和压缩（自然压限）
    NORMALIZED_SUM   = "normalized_sum"  # 归一化叠加（保证不超范围）
    PRIORITY_WINNER  = "priority_winner" # 优先级最高的维度获得话语权
    WEIGHTED_BLEND   = "weighted_blend"  # 加权混合（权重归一化后混合）


# ═══════════════════════════════════════════════════════════════════════════
#  风格预设
# ═══════════════════════════════════════════════════════════════════════════

class ExpressionStyle(Enum):
    HARMONIC        = "harmonic"        # 整体和谐风
    MOTION_DESIGN   = "motion_design"   # 动作设计风
    RHYTHMIC_BEAT   = "rhythmic_beat"   # 节奏打击风
    NARRATIVE_EMO   = "narrative_emo"   # 叙事情绪风
    CUSTOM          = "custom"          # 自由混合风


# ═══════════════════════════════════════════════════════════════════════════
#  单个维度的配置
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DimConfig:
    """一个表现维度的配置"""
    dim: ExpressionDim
    weight: float = 0.5         # 0.0~1.0，该维度的表现力权重
    enabled: bool = True
    priority: int = 0           # 优先级（用于PRIORITY_WINNER策略）
    # 该维度的输出范围（相对于通道满量程的比例）
    output_range: float = 1.0   # 0.0~1.0，该维度最大能贡献多少幅度


# ═══════════════════════════════════════════════════════════════════════════
#  设备类型的表现力配置
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceExpressionConfig:
    """一类设备的完整表现力配置"""
    device_type: str            # "motor" / "pump" / "valve" / "light"
    dims: List[DimConfig] = field(default_factory=list)
    conflict_strategy: ConflictStrategy = ConflictStrategy.ADDITIVE_SOFT
    master_weight: float = 1.0  # 该设备类型的总体权重（0.0~1.0）

    def get_dim(self, dim: ExpressionDim) -> Optional[DimConfig]:
        for d in self.dims:
            if d.dim == dim:
                return d
        return None

    def set_dim_weight(self, dim: ExpressionDim, weight: float) -> None:
        for d in self.dims:
            if d.dim == dim:
                d.weight = float(np.clip(weight, 0.0, 1.0))
                return


# ═══════════════════════════════════════════════════════════════════════════
#  表现力矩阵
# ═══════════════════════════════════════════════════════════════════════════

class ExpressionMatrix:
    """
    表现力分配矩阵。
    管理所有设备类型的表现维度权重（配置容器）。
    """

    def __init__(self):
        self._style = ExpressionStyle.HARMONIC
        self._master_level = 1.0    # 全局表现力总量 0.0~1.0
        self._configs: Dict[str, DeviceExpressionConfig] = {}
        self._load_style(ExpressionStyle.HARMONIC)

    # ─────────────────────────────────────────────────────────────────────
    #  风格预设加载
    # ─────────────────────────────────────────────────────────────────────

    def _load_style(self, style: ExpressionStyle) -> None:
        """加载风格预设，初始化所有设备类型的配置"""
        self._style = style

        if style == ExpressionStyle.HARMONIC:
            self._configs = self._preset_harmonic()
        elif style == ExpressionStyle.MOTION_DESIGN:
            self._configs = self._preset_motion_design()
        elif style == ExpressionStyle.RHYTHMIC_BEAT:
            self._configs = self._preset_rhythmic_beat()
        elif style == ExpressionStyle.NARRATIVE_EMO:
            self._configs = self._preset_narrative_emo()
        else:  # CUSTOM — 保留当前配置
            if not self._configs:
                self._configs = self._preset_harmonic()

    def set_style(self, style: ExpressionStyle) -> None:
        """切换风格预设"""
        if style == ExpressionStyle.CUSTOM:
            self._style = style
        else:
            self._load_style(style)

    def set_master_level(self, level: float) -> None:
        self._master_level = float(np.clip(level, 0.0, 1.0))

    def set_device_master_weight(self, device_type: str, weight: float) -> None:
        if device_type in self._configs:
            self._configs[device_type].master_weight = float(np.clip(weight, 0.0, 1.0))

    def set_dim_weight(self, device_type: str, dim: ExpressionDim, weight: float) -> None:
        if device_type in self._configs:
            self._configs[device_type].set_dim_weight(dim, weight)
            self._style = ExpressionStyle.CUSTOM

    def set_conflict_strategy(self, device_type: str, strategy: ConflictStrategy) -> None:
        if device_type in self._configs:
            self._configs[device_type].conflict_strategy = strategy

    def get_config(self, device_type: str) -> Optional[DeviceExpressionConfig]:
        return self._configs.get(device_type)

    def get_all_configs(self) -> Dict[str, DeviceExpressionConfig]:
        return dict(self._configs)


    # ─────────────────────────────────────────────────────────────────────
    #  风格预设定义
    # ─────────────────────────────────────────────────────────────────────

    def _preset_harmonic(self) -> Dict[str, DeviceExpressionConfig]:
        """整体和谐风：所有设备协同呼吸，归一化叠加，各维度平衡"""
        return {
            "motor": DeviceExpressionConfig(
                device_type="motor",
                dims=[
                    DimConfig(ExpressionDim.MOTOR_FIXED_WAVE, weight=0.7, priority=1),
                    DimConfig(ExpressionDim.MOTOR_AUDIO_PATH, weight=0.4, priority=0),
                    DimConfig(ExpressionDim.MOTOR_PHRASE_REVERSE, weight=0.3, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.7,
            ),
            "pump": DeviceExpressionConfig(
                device_type="pump",
                dims=[
                    DimConfig(ExpressionDim.PUMP_BREATH_SYNC, weight=0.8, priority=1),
                    DimConfig(ExpressionDim.PUMP_ENERGY_SCALE, weight=0.5, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.7,
            ),
            "valve": DeviceExpressionConfig(
                device_type="valve",
                dims=[
                    DimConfig(ExpressionDim.VALVE_PATTERN_MORPH, weight=0.6, priority=1),
                    DimConfig(ExpressionDim.VALVE_BEAT_SWITCH, weight=0.4, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.6,
            ),
            "light": DeviceExpressionConfig(
                device_type="light",
                dims=[
                    DimConfig(ExpressionDim.LIGHT_BRIGHTNESS_RMS, weight=0.7, priority=1),
                    DimConfig(ExpressionDim.LIGHT_HUE_BAND, weight=0.5, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.6,
            ),
        }

    def _preset_motion_design(self) -> Dict[str, DeviceExpressionConfig]:
        """动作设计风：电机造型是主角，其他设备是稳定背景"""
        return {
            "motor": DeviceExpressionConfig(
                device_type="motor",
                dims=[
                    DimConfig(ExpressionDim.MOTOR_AUDIO_PATH, weight=1.0, priority=3),
                    DimConfig(ExpressionDim.MOTOR_PHRASE_REVERSE, weight=0.8, priority=2),
                    DimConfig(ExpressionDim.MOTOR_BUILDUP_EXPAND, weight=0.9, priority=2),
                    DimConfig(ExpressionDim.MOTOR_BEAT_ACCEL, weight=0.6, priority=1),
                    DimConfig(ExpressionDim.MOTOR_FIXED_WAVE, weight=0.3, priority=0),
                ],
                conflict_strategy=ConflictStrategy.ADDITIVE_SOFT,
                master_weight=1.0,
            ),
            "pump": DeviceExpressionConfig(
                device_type="pump",
                dims=[
                    DimConfig(ExpressionDim.PUMP_BREATH_SYNC, weight=0.4, priority=1),
                    DimConfig(ExpressionDim.PUMP_ENERGY_SCALE, weight=0.3, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.4,
            ),
            "valve": DeviceExpressionConfig(
                device_type="valve",
                dims=[
                    DimConfig(ExpressionDim.VALVE_PATTERN_MORPH, weight=0.5, priority=1),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.5,
            ),
            "light": DeviceExpressionConfig(
                device_type="light",
                dims=[
                    DimConfig(ExpressionDim.LIGHT_BRIGHTNESS_RMS, weight=0.3, priority=0),
                ],
                conflict_strategy=ConflictStrategy.NORMALIZED_SUM,
                master_weight=0.3,
            ),
        }

    def _preset_rhythmic_beat(self) -> Dict[str, DeviceExpressionConfig]:
        """节奏打击风：泵和阀门的开关节奏是主角，强调打击感"""
        return {
            "motor": DeviceExpressionConfig(
                device_type="motor",
                dims=[
                    DimConfig(ExpressionDim.MOTOR_FIXED_WAVE, weight=0.5, priority=0),
                    DimConfig(ExpressionDim.MOTOR_BEAT_ACCEL, weight=0.7, priority=1),
                ],
                conflict_strategy=ConflictStrategy.PRIORITY_WINNER,
                master_weight=0.5,
            ),
            "pump": DeviceExpressionConfig(
                device_type="pump",
                dims=[
                    DimConfig(ExpressionDim.PUMP_BEAT_GATE, weight=1.0, priority=3),
                    DimConfig(ExpressionDim.PUMP_WAVE_TRAVEL, weight=0.8, priority=2),
                    DimConfig(ExpressionDim.PUMP_ENERGY_SCALE, weight=0.5, priority=1),
                ],
                conflict_strategy=ConflictStrategy.PRIORITY_WINNER,
                master_weight=1.0,
            ),
            "valve": DeviceExpressionConfig(
                device_type="valve",
                dims=[
                    DimConfig(ExpressionDim.VALVE_BEAT_SWITCH, weight=1.0, priority=3),
                    DimConfig(ExpressionDim.VALVE_TOPO_CHASE, weight=0.7, priority=2),
                    DimConfig(ExpressionDim.VALVE_RANDOM_JUMP, weight=0.4, priority=1),
                ],
                conflict_strategy=ConflictStrategy.PRIORITY_WINNER,
                master_weight=1.0,
            ),
            "light": DeviceExpressionConfig(
                device_type="light",
                dims=[
                    DimConfig(ExpressionDim.LIGHT_STROBE_BEAT, weight=0.8, priority=2),
                    DimConfig(ExpressionDim.LIGHT_BRIGHTNESS_RMS, weight=0.5, priority=1),
                ],
                conflict_strategy=ConflictStrategy.PRIORITY_WINNER,
                master_weight=0.7,
            ),
        }

    def _preset_narrative_emo(self) -> Dict[str, DeviceExpressionConfig]:
        """叙事情绪风：灯光和泵跟随情绪弧线，强调起承转合"""
        return {
            "motor": DeviceExpressionConfig(
                device_type="motor",
                dims=[
                    DimConfig(ExpressionDim.MOTOR_PHRASE_REVERSE, weight=0.8, priority=2),
                    DimConfig(ExpressionDim.MOTOR_BUILDUP_EXPAND, weight=0.7, priority=1),
                    DimConfig(ExpressionDim.MOTOR_FIXED_WAVE, weight=0.4, priority=0),
                ],
                conflict_strategy=ConflictStrategy.WEIGHTED_BLEND,
                master_weight=0.7,
            ),
            "pump": DeviceExpressionConfig(
                device_type="pump",
                dims=[
                    DimConfig(ExpressionDim.PUMP_PHRASE_GATE, weight=1.0, priority=3),
                    DimConfig(ExpressionDim.PUMP_BREATH_SYNC, weight=0.6, priority=2),
                    DimConfig(ExpressionDim.PUMP_ENERGY_SCALE, weight=0.4, priority=1),
                ],
                conflict_strategy=ConflictStrategy.WEIGHTED_BLEND,
                master_weight=0.8,
            ),
            "valve": DeviceExpressionConfig(
                device_type="valve",
                dims=[
                    DimConfig(ExpressionDim.VALVE_PHRASE_SWITCH, weight=0.9, priority=2),
                    DimConfig(ExpressionDim.VALVE_PATTERN_MORPH, weight=0.6, priority=1),
                ],
                conflict_strategy=ConflictStrategy.WEIGHTED_BLEND,
                master_weight=0.7,
            ),
            "light": DeviceExpressionConfig(
                device_type="light",
                dims=[
                    DimConfig(ExpressionDim.LIGHT_COLOR_PHRASE, weight=1.0, priority=3),
                    DimConfig(ExpressionDim.LIGHT_SAT_TENSION, weight=0.8, priority=2),
                    DimConfig(ExpressionDim.LIGHT_BRIGHTNESS_RMS, weight=0.5, priority=1),
                ],
                conflict_strategy=ConflictStrategy.WEIGHTED_BLEND,
                master_weight=0.9,
            ),
        }

    # ─────────────────────────────────────────────────────────────────────
    #  序列化（用于保存/加载参数）
    # ─────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        result = {
            'style': self._style.value,
            'master_level': self._master_level,
            'configs': {},
        }
        for dev_type, cfg in self._configs.items():
            result['configs'][dev_type] = {
                'master_weight': cfg.master_weight,
                'conflict_strategy': cfg.conflict_strategy.value,
                'dims': [
                    {
                        'dim': d.dim.value,
                        'weight': d.weight,
                        'enabled': d.enabled,
                        'priority': d.priority,
                        'output_range': d.output_range,
                    }
                    for d in cfg.dims
                ],
            }
        return result

    def from_dict(self, data: dict) -> None:
        style_str = data.get('style', 'harmonic')
        try:
            style = ExpressionStyle(style_str)
        except ValueError:
            style = ExpressionStyle.HARMONIC
        self._load_style(style)
        self._master_level = float(data.get('master_level', 1.0))

        for dev_type, cfg_data in data.get('configs', {}).items():
            if dev_type not in self._configs:
                continue
            cfg = self._configs[dev_type]
            cfg.master_weight = float(cfg_data.get('master_weight', 1.0))
            try:
                cfg.conflict_strategy = ConflictStrategy(
                    cfg_data.get('conflict_strategy', 'additive_soft')
                )
            except ValueError:
                pass
            for dim_data in cfg_data.get('dims', []):
                try:
                    dim = ExpressionDim(dim_data['dim'])
                    for d in cfg.dims:
                        if d.dim == dim:
                            d.weight = float(dim_data.get('weight', d.weight))
                            d.enabled = bool(dim_data.get('enabled', d.enabled))
                            d.priority = int(dim_data.get('priority', d.priority))
                            d.output_range = float(dim_data.get('output_range', d.output_range))
                            break
                except (ValueError, KeyError):
                    pass
