"""
param_drivers.py — DeepAqua 六大参数驱动模块
============================================
实现《全量设计规格书》第三章"全量功能参数规格"中的六大参数驱动：

1. Complexity（复杂度）：声部层次与资源调度
2. DeviceDensity（设备激活率）：空间分辨率（Mask/Step 结构化激活）
3. ExpressionMatrix（表现力矩阵）：通道响应深度
4. VariationStrength（变奏强度）：逻辑重组烈度（相位重构、时域拉伸）
5. TransitionController（衔接与演化）：形态连续性控制
6. SpatialNarrative（空间叙事与记忆）：问答拓扑映射 + 主题记忆

设计原则：
  - 变奏必须是结构的重组（相位翻转、时域拉伸），严禁无意义的随机扰动
  - 设备激活通过步进或逻辑掩码实现，从"骨架形态"到"饱和形态"动态调节
  - 空间叙事：问句触发向外/左侧拓扑，答句触发向内/右侧拓扑
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  1. 复杂度驱动器（Complexity Driver）
# ═══════════════════════════════════════════════════════════════════════════

class ComplexityDriver:
    """
    复杂度驱动器：管理 1~6 个并行的"动作元 × 设备元"声部。

    职责：
    - 根据音乐能量动态调整声部数量
    - 处理声部间的设备竞争（高能量时增加声部层数）
    - 实现交响感（多声部同时运行）
    """

    MIN_VOICES = 1
    MAX_VOICES = 32   # 解除硬编码：支持最多 32 声部（实际受设备数约束）
    def __init__(self, base_complexity: float = 0.4,
                 min_voices: int = 1, max_voices: int = 32):
        # 允许运行时自定义范围，不硬编码
        self.MIN_VOICES = max(1, min_voices)
        self.MAX_VOICES = max(self.MIN_VOICES, max_voices)
        self._base = float(np.clip(base_complexity, 0.0, 1.0))
        self._current_voice_count = 1
        self._target_voice_count = 1
    def update_params(self, base_complexity: float,
                      min_voices: Optional[int] = None,
                      max_voices: Optional[int] = None) -> None:
        self._base = float(np.clip(base_complexity, 0.0, 1.0))
        if min_voices is not None:
            self.MIN_VOICES = max(1, int(min_voices))
        if max_voices is not None:
            self.MAX_VOICES = max(self.MIN_VOICES, int(max_voices))

    def compute_target_voice_count(
        self,
        energy: float,
        tension: float,
        in_buildup: bool,
        buildup_progress: float,
        is_climax: bool,
    ) -> int:
        """
        根据音乐上下文计算目标声部数量。

        逻辑：
        - 基础声部数 = base_complexity * MAX_VOICES
        - 高潮：最大声部数
        - Build-up：随进度递增
        - 高能量：额外加声部
        """
        base_count = max(1, int(self._base * self.MAX_VOICES))

        # 音乐驱动的乘数
        multiplier = 1.0
        if is_climax:
            multiplier = 2.0
        elif in_buildup:
            multiplier = 1.0 + buildup_progress
        elif energy > 0.8:
            multiplier = 1.5
        elif energy < 0.2:
            # 低能量时使用更激进的缩减，确保少量声部（骨架模式）
            # energy=0.1, base=0.3, MAX=32 时：base_count=9, multiplier=0.2 → target=1
            multiplier = max(0.1, 0.2 - energy * 0.5)

        target = int(base_count * multiplier)
         # 不再硬性截断，允许超出预设范围（由调用方保证设备数合理）
        target = max(self.MIN_VOICES, min(target, self.MAX_VOICES))
        self._target_voice_count = target
        return target

    def smooth_voice_count(self, dt: float, urgency: float = 0.3) -> int:
        """
        平滑过渡到目标声部数（避免突变）。
        urgency: 0=慢速过渡, 1=立即切换
        """
        if self._current_voice_count == self._target_voice_count:
            return self._current_voice_count

        # 每次最多增减 1 个声部
        if self._current_voice_count < self._target_voice_count:
            self._current_voice_count += 1
        else:
            self._current_voice_count -= 1

        return self._current_voice_count

    @property
    def current_voice_count(self) -> int:
        return self._current_voice_count


# ═══════════════════════════════════════════════════════════════════════════
#  2. 设备激活率驱动器（Device Density Driver）
# ═══════════════════════════════════════════════════════════════════════════

class ActivationMaskMode(Enum):
    """激活掩码模式"""
    FULL        = "full"        # 全部激活（饱和形态）
    STEP_EVEN   = "step_even"   # 步进偶数（骨架形态）
    STEP_ODD    = "step_odd"    # 步进奇数
    STEP_N      = "step_n"      # 每 N 个激活 1 个
    FRONT_HALF  = "front_half"  # 前半激活
    BACK_HALF   = "back_half"   # 后半激活
    OUTER_RING  = "outer_ring"  # 外环激活
    INNER_CORE  = "inner_core"  # 内核激活
    GRADIENT    = "gradient"    # 渐变激活（从头到尾密度递增）


class DeviceDensityDriver:
    """
    设备激活率驱动器：在设备元内部实现结构化（非随机）的设备选取。

    通过步进（Step）或逻辑掩码（Mask）实现：
    - "骨架形态"（低密度）：只激活关键节点
    - "饱和形态"（高密度）：激活所有设备
    - 中间形态：通过掩码渐变

    严禁：随机扰动（会产生无意义的视觉噪声）
    """

    def __init__(self, base_density: float = 0.5):
        self._base = float(np.clip(base_density, 0.0, 1.0))
        self._current_density = base_density
        self._mask_mode = ActivationMaskMode.FULL

    def update_params(self, base_density: float) -> None:
        self._base = float(np.clip(base_density, 0.0, 1.0))

    def compute_activation_mask(
        self,
        n_fixtures: int,
        density: float,
        mode: Optional[ActivationMaskMode] = None,
        phase: float = 0.0,
    ) -> List[bool]:
        """
        计算设备激活掩码（结构化，非随机）。

        Args:
            n_fixtures: 设备总数
            density: 激活密度（0.0~1.0）
            mode: 掩码模式（None 则自动选择）
            phase: 相位偏移（0.0~1.0），用于时域拉伸

        Returns:
            List[bool]: 每个设备是否激活
        """
        if n_fixtures == 0:
            return []

        density = float(np.clip(density, 0.0, 1.0))

        # 自动选择掩码模式
        if mode is None:
            mode = self._auto_select_mode(density)

        mask = [False] * n_fixtures

        if mode == ActivationMaskMode.FULL:
            mask = [True] * n_fixtures

        elif mode == ActivationMaskMode.STEP_EVEN:
            # 激活偶数索引（骨架形态）
            for i in range(n_fixtures):
                mask[i] = (i % 2 == 0)

        elif mode == ActivationMaskMode.STEP_ODD:
            # 激活奇数索引
            for i in range(n_fixtures):
                mask[i] = (i % 2 == 1)

        elif mode == ActivationMaskMode.STEP_N:
            # 每 N 个激活 1 个
            step = max(1, int(1.0 / max(density, 0.01)))
            # 相位偏移：通过 phase 控制从哪个位置开始步进
            offset = int(phase * step) % step
            for i in range(n_fixtures):
                mask[i] = ((i + offset) % step == 0)

        elif mode == ActivationMaskMode.FRONT_HALF:
            half = int(n_fixtures * density)
            for i in range(n_fixtures):
                mask[i] = (i < half)

        elif mode == ActivationMaskMode.BACK_HALF:
            start = n_fixtures - int(n_fixtures * density)
            for i in range(n_fixtures):
                mask[i] = (i >= start)

        elif mode == ActivationMaskMode.OUTER_RING:
            # 外环激活：激活最外圈的设备
            n_outer = max(1, int(n_fixtures * density))
            # 假设设备已按极角排序，外环 = 前 n_outer 个
            for i in range(n_fixtures):
                mask[i] = (i < n_outer)

        elif mode == ActivationMaskMode.INNER_CORE:
            # 内核激活：激活中心区域
            n_inner = max(1, int(n_fixtures * density))
            start = (n_fixtures - n_inner) // 2
            for i in range(n_fixtures):
                mask[i] = (start <= i < start + n_inner)

        elif mode == ActivationMaskMode.GRADIENT:
            # 渐变激活：从头到尾密度递增（相位控制起点）
            # 使用确定性的渐变，不是随机
            for i in range(n_fixtures):
                # 归一化位置（加入相位偏移）
                pos = (i / n_fixtures + phase) % 1.0
                # 渐变阈值：density 控制激活比例
                mask[i] = (pos < density)

        return mask

    def _auto_select_mode(self, density: float) -> ActivationMaskMode:
        """根据密度自动选择最合适的掩码模式"""
        if density >= 0.9:
            return ActivationMaskMode.FULL
        elif density >= 0.6:
            return ActivationMaskMode.GRADIENT
        elif density >= 0.4:
            return ActivationMaskMode.STEP_EVEN
        elif density >= 0.2:
            return ActivationMaskMode.STEP_N
        else:
            return ActivationMaskMode.INNER_CORE

    def compute_music_driven_density(
        self,
        base_density: float,
        energy: float,
        rhythm_intensity: float,
        in_buildup: bool,
        buildup_progress: float,
    ) -> float:
        """
        根据音乐特征计算实时设备激活率。
        """
        density = base_density

        # 能量驱动：高能量 → 更高密度
        density = density * (0.5 + 0.5 * energy)

        # Build-up：随进度递增密度
        if in_buildup:
            density = density * (0.5 + 0.5 * buildup_progress)

        # 节奏强度：高节奏 → 更高密度（更多设备参与节拍）
        density = density * (0.7 + 0.3 * rhythm_intensity)

          # 下限从 0.05 降至 0.0，允许完全骨架模式（单台设备独奏）
        return float(np.clip(density, 0.0, 1.0))

    def get_active_devices(self, devices: List[int],
                           density: Optional[float] = None,
                           mode: Optional[ActivationMaskMode] = None) -> List[int]:
        """返回当前密度下应激活的设备列表（供 DeviceGroupSelector 集成使用）"""
        d = density if density is not None else self._base
        mask = self.compute_activation_mask(len(devices), d, mode)
        return [dev for dev, active in zip(devices, mask) if active]

    @property
    def activation_rate(self) -> float:
        """当前激活率（供 DeviceGroupSelector 查询）"""
        return float(self._base)
# ═══════════════════════════════════════════════════════════════════════════
#  3. 表现力矩阵（Expression Matrix Driver）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ExpressionWeights:
    """
    表现力权重矩阵：定义各设备类型对音乐特征的响应权重。

    映射规则（规格书定义）：
    - 水泵（pump）：控高度 ← 能量（Energy）+ 旋律音高（Pitch）
    - 阀门（valve）：控切分 ← 节奏（Rhythm）+ 节拍冲击（Beat Impulse）
    - 电机（motor）：控轨迹 ← 旋律（Melody）+ 轮廓走向（Contour）+ Solo 路径
    - 灯光（light）：控氛围 ← 张力（Tension）+ 和弦调性色彩（Chord Color）+ ADSR

    所有权重均无上限限制，允许超量表达（DMX 层做最终截断）。
    """
    # ── 泵的权重（能量+音高映射 → 水柱高度）──────────────────────────────
    pump_energy_weight: float = 0.8      # 能量驱动水柱高度
    pump_rhythm_weight: float = 0.2      # 节奏驱动水柱脉冲
    pump_tension_weight: float = 0.1     # 张力驱动水柱变化速率
    pump_pitch_weight: float = 0.7       # 旋律音高驱动水柱高度（高音=高水柱）
    pump_adsr_weight: float = 0.5        # ADSR 包络驱动水柱起伏
    pump_beat_weight: float = 0.3        # 节拍冲击驱动水柱脉冲

    # ── 阀门的权重（节奏+节拍映射 → 切分开合）───────────────────────────
    valve_energy_weight: float = 0.3
    valve_rhythm_weight: float = 0.9
    valve_tension_weight: float = 0.2
    valve_beat_weight: float = 0.8       # 节拍冲击驱动阀门切分
    valve_adsr_weight: float = 0.2       # ADSR Attack 驱动阀门开启

    # ── 电机的权重（旋律+轮廓映射 → 路径轨迹）──────────────────────────
    motor_energy_weight: float = 0.4
    motor_rhythm_weight: float = 0.3
    motor_tension_weight: float = 0.5
    motor_melody_weight: float = 0.8     # 旋律强度驱动电机响应幅度
    motor_pitch_weight: float = 0.9      # 旋律音高驱动电机仰角
    motor_contour_weight: float = 0.85   # 旋律走向驱动电机摆动方向
    motor_ornament_weight: float = 0.7   # 装饰音驱动电机微颤
    motor_solo_weight: float = 1.0       # Solo 模式下电机路径生成权重
    motor_adsr_weight: float = 0.4       # ADSR 驱动电机速度包络

    # ── 灯光的权重（张力+调性色彩映射 → 氛围）──────────────────────────
    light_energy_weight: float = 0.5
    light_rhythm_weight: float = 0.4
    light_tension_weight: float = 0.9
    light_color_weight: float = 0.7      # 和弦调性色相权重
    light_adsr_weight: float = 0.8       # ADSR 包络驱动灯光强度
    light_beat_weight: float = 0.6       # 节拍冲击驱动灯光闪烁
    light_chord_tension_weight: float = 0.85  # 和弦张力驱动灯光色彩饱和度
    light_pitch_weight: float = 0.3      # 旋律音高驱动灯光明度


class ExpressionMatrixDriver:
    """
    表现力矩阵驱动器：全通道音频驱动的统一调度中心。

    架构设计：
    「音频信号 → ExpressionWeights 权重矩阵 → 各通道输出」

    输入信号（来自 MelodyAnalyzer 和 MusicContext）：
    - energy:          音频能量
    - rhythm:          节奏强度
    - tension:         张力
    - melody:          旋律强度
    - pitch_norm:      旋律音高归一化 [0,1]（高音=1）
    - contour_vector:  旋律走向 [-1,1]（上行=+1，下行=-1）
    - adsr_level:      ADSR 包络电平 [0,1]
    - beat_impulse:    节拍冲击 [0,1]
    - ornament_signal: 装饰音信号 [0,1]
    - solo_intensity:  Solo 强度 [0,1]
    - chord_tension:   和弦张力 [0,1]
    - color_hue:       和弦色相 [0,360]
    - color_sat:       色彩饱和度 [0,1]
    - color_bri:       色彩明度 [0,1]

    输出：各通道调制值（允许超量，DMX 层做最终截断）
    """

    # ── 预设风格：包含全量旋律信号权重 ────────────────────────────────────────
    PRESET_STYLES: Dict[str, Dict[str, float]] = {
        # 和谐风格：旋律和和弦为主，平滑优雅
        "harmonic": {
            "pump_energy_weight": 0.8,  "pump_pitch_weight": 0.7,  "pump_adsr_weight": 0.5,
            "pump_rhythm_weight": 0.2,  "pump_beat_weight": 0.2,
            # 和谐风格下阀门节奏权重较低，确保 rhythmic 风格的阀门响应更强
            "valve_rhythm_weight": 0.4, "valve_beat_weight": 0.3,  "valve_energy_weight": 0.3,
            "motor_melody_weight": 0.9, "motor_pitch_weight": 0.9, "motor_contour_weight": 0.85,
            "motor_ornament_weight": 0.6, "motor_solo_weight": 1.0, "motor_adsr_weight": 0.5,
            "motor_tension_weight": 0.4,
            "light_tension_weight": 0.9, "light_adsr_weight": 0.8, "light_beat_weight": 0.4,
            "light_chord_tension_weight": 0.85, "light_color_weight": 0.9,
            "light_energy_weight": 0.5, "light_pitch_weight": 0.4,
        },
        # 节奏风格：节拍为主，阀门切分锐利，灯光闪烁强烈
        "rhythmic": {
            "pump_energy_weight": 0.6,  "pump_pitch_weight": 0.4,  "pump_adsr_weight": 0.3,
            "pump_rhythm_weight": 0.9,  "pump_beat_weight": 0.8,
            # 节奏风格：valve_beat_weight 降低避免无节拍时拖累分母，提高 valve_rhythm_weight 确保节奏信号优先
            "valve_rhythm_weight": 1.5, "valve_beat_weight": 0.4,  "valve_energy_weight": 0.4,
            "valve_tension_weight": 0.1,
            "motor_melody_weight": 0.5, "motor_pitch_weight": 0.4, "motor_contour_weight": 0.4,
            "motor_ornament_weight": 0.3, "motor_solo_weight": 0.6, "motor_adsr_weight": 0.3,
            "motor_tension_weight": 0.3,
            "light_tension_weight": 0.6, "light_adsr_weight": 0.5, "light_beat_weight": 1.0,
            "light_chord_tension_weight": 0.5, "light_color_weight": 0.5,
            "light_energy_weight": 0.8, "light_pitch_weight": 0.2,
        },
        # 叙事风格：旋律主导，电机路径细腻，灯光色彩丰富
        "narrative": {
            "pump_energy_weight": 0.7,  "pump_pitch_weight": 0.9,  "pump_adsr_weight": 0.7,
            "pump_rhythm_weight": 0.1,  "pump_beat_weight": 0.2,
            "valve_rhythm_weight": 0.6, "valve_beat_weight": 0.5,  "valve_energy_weight": 0.5,
            "motor_melody_weight": 1.0, "motor_pitch_weight": 1.0, "motor_contour_weight": 1.0,
            "motor_ornament_weight": 0.9, "motor_solo_weight": 1.2, "motor_adsr_weight": 0.7,
            "motor_tension_weight": 0.8,
            "light_tension_weight": 1.0, "light_adsr_weight": 0.9, "light_beat_weight": 0.3,
            "light_chord_tension_weight": 1.0, "light_color_weight": 1.0,
            "light_energy_weight": 0.4, "light_pitch_weight": 0.6,
        },
        # 爆发风格：全通道满载，泵到最高，灯光全亮
        "burst": {
            "pump_energy_weight": 1.2,  "pump_pitch_weight": 0.8,  "pump_adsr_weight": 0.9,
            "pump_rhythm_weight": 0.6,  "pump_beat_weight": 0.9,
            "valve_rhythm_weight": 0.9, "valve_beat_weight": 1.0,  "valve_energy_weight": 1.0,
            "motor_melody_weight": 0.5, "motor_pitch_weight": 0.6, "motor_contour_weight": 0.7,
            "motor_ornament_weight": 0.4, "motor_solo_weight": 0.7, "motor_adsr_weight": 0.8,
            "motor_tension_weight": 0.9,
            "light_tension_weight": 0.8, "light_adsr_weight": 1.0, "light_beat_weight": 1.0,
            "light_chord_tension_weight": 0.7, "light_color_weight": 0.6,
            "light_energy_weight": 1.2, "light_pitch_weight": 0.5,
        },
        # Solo 风格：电机路径生成最强，旋律主导所有通道
        "solo": {
            "pump_energy_weight": 0.5,  "pump_pitch_weight": 1.0,  "pump_adsr_weight": 0.8,
            "pump_rhythm_weight": 0.1,  "pump_beat_weight": 0.1,
            "valve_rhythm_weight": 0.4, "valve_beat_weight": 0.3,  "valve_energy_weight": 0.2,
            "motor_melody_weight": 1.0, "motor_pitch_weight": 1.2, "motor_contour_weight": 1.2,
            "motor_ornament_weight": 1.0, "motor_solo_weight": 1.5, "motor_adsr_weight": 0.8,
            "motor_tension_weight": 0.6,
            "light_tension_weight": 0.7, "light_adsr_weight": 1.0, "light_beat_weight": 0.2,
            "light_chord_tension_weight": 0.9, "light_color_weight": 1.2,
            "light_energy_weight": 0.3, "light_pitch_weight": 0.8,
        },
        # 氛围风格：灯光和泵主导，电机平滑，阀门少动
        "ambient": {
            "pump_energy_weight": 0.9,  "pump_pitch_weight": 0.6,  "pump_adsr_weight": 0.9,
            "pump_rhythm_weight": 0.1,  "pump_beat_weight": 0.1,
            "valve_rhythm_weight": 0.3, "valve_beat_weight": 0.2,  "valve_energy_weight": 0.4,
            "motor_melody_weight": 0.6, "motor_pitch_weight": 0.5, "motor_contour_weight": 0.5,
            "motor_ornament_weight": 0.3, "motor_solo_weight": 0.7, "motor_adsr_weight": 0.6,
            "motor_tension_weight": 0.4,
            "light_tension_weight": 0.8, "light_adsr_weight": 1.0, "light_beat_weight": 0.2,
            "light_chord_tension_weight": 1.0, "light_color_weight": 1.2,
            "light_energy_weight": 0.6, "light_pitch_weight": 0.5,
        },
        # 建构风格：渐进增强，泵和灯光逐渐充能
        "buildup": {
            "pump_energy_weight": 1.0,  "pump_pitch_weight": 0.8,  "pump_adsr_weight": 0.7,
            "pump_rhythm_weight": 0.5,  "pump_beat_weight": 0.6,
            "valve_rhythm_weight": 0.8, "valve_beat_weight": 0.8,  "valve_energy_weight": 0.7,
            "motor_melody_weight": 0.7, "motor_pitch_weight": 0.7, "motor_contour_weight": 0.8,
            "motor_ornament_weight": 0.5, "motor_solo_weight": 0.8, "motor_adsr_weight": 0.6,
            "motor_tension_weight": 0.7,
            "light_tension_weight": 0.9, "light_adsr_weight": 0.9, "light_beat_weight": 0.8,
            "light_chord_tension_weight": 0.8, "light_color_weight": 0.8,
            "light_energy_weight": 0.9, "light_pitch_weight": 0.4,
        },
        # 自定义风格：中性默认权重。取调和/节奏/叙事三者均值，允许外部逐渐覆盖。
        # 修复：避免 UI 选中 "custom" 后被静默回落到 harmonic，错觉成 "什么都没变"。
        "custom": {
            "pump_energy_weight": 0.8,  "pump_pitch_weight": 0.7,  "pump_adsr_weight": 0.6,
            "pump_rhythm_weight": 0.5,  "pump_beat_weight": 0.5,
            "valve_rhythm_weight": 0.7, "valve_beat_weight": 0.6,  "valve_energy_weight": 0.5,
            "motor_melody_weight": 0.8, "motor_pitch_weight": 0.8, "motor_contour_weight": 0.8,
            "motor_ornament_weight": 0.6, "motor_solo_weight": 1.0, "motor_adsr_weight": 0.6,
            "motor_tension_weight": 0.6,
            "light_tension_weight": 0.8, "light_adsr_weight": 0.8, "light_beat_weight": 0.7,
            "light_chord_tension_weight": 0.8, "light_color_weight": 0.9,
            "light_energy_weight": 0.8, "light_pitch_weight": 0.5,
        },
    }

    def __init__(self, style: str = "harmonic"):
        self._weights = ExpressionWeights()
        self._style = style
        self._load_style(style)
        # Solo 模式自动切换状态
        self._auto_solo_enabled: bool = True
        self._solo_blend: float = 0.0   # 当前 Solo 混合比例 [0,1]

    def _load_style(self, style: str) -> None:
        """加载预设风格，先重置默认权重避免残留污染"""
        self._weights = ExpressionWeights()
        preset = self.PRESET_STYLES.get(style, self.PRESET_STYLES["harmonic"])
        for key, val in preset.items():
            if hasattr(self._weights, key):
                setattr(self._weights, key, float(val))
        self._style = style

    def update_style(self, style: str) -> None:
        """切换风格预设"""
        self._load_style(style)

    def enable_auto_solo(self, enabled: bool = True) -> None:
        """是否允许 Solo 模式自动切换"""
        self._auto_solo_enabled = enabled

    def set_weight(self, key: str, value: float) -> None:
        """实时修改单个权重（无范围限制）"""
        if hasattr(self._weights, key):
            setattr(self._weights, key, float(value))

    def get_weights(self) -> ExpressionWeights:
        """获取当前权重矩阵副本"""
        import copy
        return copy.copy(self._weights)

    # ─────────────────────────────────────────────────────────────────────
    #  泵高度计算（能量 + 旋律音高 + ADSR + 节拍冲击）
    # ─────────────────────────────────────────────────────────────────────

    def compute_pump_value(
        self,
        base_val: float,
        energy: float,
        rhythm: float,
        tension: float,
        pitch_norm: float = 0.5,
        adsr_level: float = 0.7,
        beat_impulse: float = 0.0,
    ) -> float:
        """
        计算泵的输出值（水柱高度）。

        水柱高度由三层信号共同决定：
        - 能量层：音频整体能量决定基础高度
        - 旋律层：旋律音高决定精确高度（高音=高水柱）
        - 动态层：ADSR 包络决定水柱起伏曲线，节拍冲击制造脉冲感
        """
        w = self._weights
        total_w = (
            w.pump_energy_weight + w.pump_rhythm_weight + w.pump_tension_weight +
            w.pump_pitch_weight + w.pump_adsr_weight + w.pump_beat_weight
        )
        if total_w <= 0:
            return float(base_val)
        modulation = (
            energy       * w.pump_energy_weight +
            rhythm       * w.pump_rhythm_weight +
            tension      * w.pump_tension_weight +
            pitch_norm   * w.pump_pitch_weight +
            adsr_level   * w.pump_adsr_weight +
            beat_impulse * w.pump_beat_weight
        ) / total_w
        return float(base_val * modulation)

    # ─────────────────────────────────────────────────────────────────────
    #  阀门切分计算（节奏 + 节拍冲击 + ADSR Attack）
    # ─────────────────────────────────────────────────────────────────────

    def compute_valve_value(
        self,
        base_val: float,
        energy: float,
        rhythm: float,
        tension: float,
        beat_impulse: float = 0.0,
        adsr_level: float = 0.7,
    ) -> float:
        """
        计算阀门的输出值（切分开合）。

        阀门切分由三层信号决定：
        - 节奏层：节奏强度决定切分频率
        - 节拍层：节拍冲击决定切分时点（强拍开阀）
        - ADSR：Attack 阶段强制开阀，Release 阶段关阀
        """
        w = self._weights
        total_w = (
            w.valve_rhythm_weight + w.valve_energy_weight +
            w.valve_tension_weight + w.valve_beat_weight + w.valve_adsr_weight
        )
        if total_w <= 0:
            return float(base_val)
        modulation = (
            rhythm       * w.valve_rhythm_weight +
            energy       * w.valve_energy_weight +
            tension      * w.valve_tension_weight +
            beat_impulse * w.valve_beat_weight +
            adsr_level   * w.valve_adsr_weight
        ) / total_w
        return float(base_val * modulation)

    # ─────────────────────────────────────────────────────────────────────
    #  电机路径计算（旋律 + 音高 + 轮廓 + 装饰音 + Solo 路径）
    # ─────────────────────────────────────────────────────────────────────

    def compute_motor_value(
        self,
        base_val: float,
        energy: float,
        rhythm: float,
        tension: float,
        melody: float = 0.5,
        pitch_norm: float = 0.5,
        contour_vector: float = 0.0,
        ornament_signal: float = 0.0,
        solo_intensity: float = 0.0,
        adsr_level: float = 0.7,
    ) -> float:
        """
        计算电机的输出值（幅度调制）。

        电机路径由六层信号决定：
        - 旋律强度：整体响应幅度
        - 旋律音高：仰角目标位置（高音=高仰角）
        - 旋律走向：摆动幅度（走向越强，摆动越大）
        - 装饰音：微颤/轻抖幅度
        - Solo 强度：路径生成权重
        - ADSR：速度包络
        """
        w = self._weights
        total_w = (
            w.motor_melody_weight + w.motor_pitch_weight + w.motor_contour_weight +
            w.motor_ornament_weight + w.motor_adsr_weight + w.motor_tension_weight +
            w.motor_energy_weight + w.motor_rhythm_weight + w.motor_solo_weight
        )
        if total_w <= 0:
            return float(base_val)
        modulation = (
            melody              * w.motor_melody_weight +
            pitch_norm          * w.motor_pitch_weight +
            abs(contour_vector) * w.motor_contour_weight +
            ornament_signal     * w.motor_ornament_weight +
            adsr_level          * w.motor_adsr_weight +
            tension             * w.motor_tension_weight +
            energy              * w.motor_energy_weight +
            rhythm              * w.motor_rhythm_weight +
            solo_intensity      * w.motor_solo_weight
        ) / total_w
        return float(base_val * modulation)

    def compute_motor_path(
        self,
        pitch_norm: float,
        contour_vector: float,
        ornament_signal: float,
        adsr_level: float,
        solo_intensity: float,
        tension: float,
        device_index: int = 0,
        total_devices: int = 1,
    ) -> Dict[str, float]:
        """
        AI 实时电机路径生成（Solo 模式核心功能）。

        绕过 Effect 库，直接生成逐帧的电机角度轨迹。

        映射规则：
        - pitch_norm [0,1]     → motor_y（仰角，高音=高仰角）
        - contour_vector[-1,1] → motor_x（水平摆动，上行=向左）
        - ornament_signal[0,1] → 微颤叠加（在 motor_x 上叠加微小振动）
        - adsr_level[0,1]      → speed（运动速度）
        - device_index         → 卡农偏移（多设备时形成追随效果）

        Args:
            pitch_norm:      旋律音高归一化 [0,1]
            contour_vector:  旋律走向 [-1,1]
            ornament_signal: 装饰音信号 [0,1]
            adsr_level:      ADSR 包络 [0,1]
            solo_intensity:  Solo 强度 [0,1]
            tension:         张力 [0,1]
            device_index:    设备索引（用于卡农偏移）
            total_devices:   设备总数

        Returns:
            {"motor_y": float, "motor_x": float, "speed": float, "path_mode": str}
        """
        w = self._weights

        # 仰角：音高直接映射，张力增加幅度
        motor_y = pitch_norm * w.motor_pitch_weight / max(w.motor_pitch_weight, 1.0)
        motor_y = float(max(0.0, min(1.0, motor_y + tension * 0.1)))

        # 水平摆动：轮廓走向映射，中心=0.5
        # 上行旋律→向左摆（<0.5），下行旋律→向右摆（>0.5）
        base_x = 0.5 - contour_vector * 0.4 * w.motor_contour_weight / max(w.motor_contour_weight, 1.0)

        # 装饰音微颤：在 base_x 上叠加微小振动
        ornament_offset = ornament_signal * 0.08 * w.motor_ornament_weight / max(w.motor_ornament_weight, 1.0)
        # 振动方向交替（基于设备索引）
        vibrato_sign = 1.0 if (device_index % 2 == 0) else -1.0
        motor_x = float(max(0.0, min(1.0, base_x + ornament_offset * vibrato_sign)))

        # 卡农偏移：多设备时按索引逐步延迟，形成追随效果
        if total_devices > 1:
            canon_offset = (device_index / total_devices) * 0.15
            motor_x = float(max(0.0, min(1.0, motor_x + canon_offset * contour_vector)))

        # 运动速度：ADSR 包络驱动，Solo 时加快响应
        speed = adsr_level * w.motor_adsr_weight / max(w.motor_adsr_weight, 1.0)
        speed = float(max(0.0, min(1.0, speed + solo_intensity * 0.2)))

        # 路径模式标识
        if solo_intensity > 0.6:
            path_mode = "melody_path"   # 旋律路径模式
        elif ornament_signal > 0.5:
            path_mode = "ornament"      # 装饰音模式
        else:
            path_mode = "effect_blend"  # Effect 混合模式

        return {
            "motor_y": motor_y,
            "motor_x": motor_x,
            "speed": speed,
            "path_mode": path_mode,
        }

    # ─────────────────────────────────────────────────────────────────────
    #  灯光计算（张力 + 和弦调性色彩 + ADSR + 节拍闪烁）
    # ─────────────────────────────────────────────────────────────────────

    def compute_light_value(
        self,
        base_val: float,
        energy: float,
        rhythm: float,
        tension: float,
        adsr_level: float = 0.7,
        beat_impulse: float = 0.0,
        chord_tension: float = 0.0,
        pitch_norm: float = 0.5,
    ) -> float:
        """
        计算灯光强度的输出值。

        灯光强度由四层信号决定：
        - 张力层：张力越高，灯光越暗（紧张感）
        - ADSR：包络直接驱动灯光强度起伏
        - 节拍：强拍闪烁
        - 和弦张力：和弦紧张度调制强度
        """
        w = self._weights
        total_w = (
            w.light_tension_weight + w.light_energy_weight + w.light_rhythm_weight +
            w.light_adsr_weight + w.light_beat_weight +
            w.light_chord_tension_weight + w.light_pitch_weight
        )
        if total_w <= 0:
            return float(base_val)
        modulation = (
            tension       * w.light_tension_weight +
            energy        * w.light_energy_weight +
            rhythm        * w.light_rhythm_weight +
            adsr_level    * w.light_adsr_weight +
            beat_impulse  * w.light_beat_weight +
            chord_tension * w.light_chord_tension_weight +
            pitch_norm    * w.light_pitch_weight
        ) / total_w
        return float(base_val * modulation)

    def compute_light_color(
        self,
        color_hue: float,
        color_saturation: float,
        color_brightness: float,
        energy: float,
        tension: float,
        adsr_level: float,
        beat_impulse: float,
    ) -> Dict[str, float]:
        """
        计算灯光色彩输出（RGB + 调光）。

        色彩系统基于和弦调性：
        - 色相：和弦根音映射到五度圈色相环
        - 饱和度：和弦张力高→饱和度高（紧张色彩鲜艳）
        - 明度：ADSR 包络驱动，节拍强拍闪烁

        Returns:
            {"red": float, "green": float, "blue": float, "dimmer": float}
        """
        w = self._weights

        # HSV → RGB 转换
        h = color_hue % 360.0
        # 饱和度由张力和和弦张力共同调制
        s = color_saturation * (
            0.5 + 0.5 * tension * w.light_chord_tension_weight / max(w.light_chord_tension_weight, 0.01)
        )
        s = float(max(0.0, min(1.0, s)))
        # 明度由 ADSR 和节拍冲击驱动
        v = adsr_level * w.light_adsr_weight / max(w.light_adsr_weight, 1.0)
        v = float(max(0.0, min(1.0, v + beat_impulse * 0.3 * w.light_beat_weight / max(w.light_beat_weight, 1.0))))
        v *= color_brightness

        # HSV → RGB
        hi = int(h / 60.0) % 6
        f = (h / 60.0) - int(h / 60.0)
        p = v * (1.0 - s)
        q = v * (1.0 - f * s)
        t = v * (1.0 - (1.0 - f) * s)
        rgb_table = [
            (v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q),
        ]
        r, g, b = rgb_table[hi]

        # 调光：能量 + ADSR 共同决定
        dimmer = float(max(0.0, min(1.0,
            energy * w.light_energy_weight / max(w.light_energy_weight, 1.0) * 0.7 +
            adsr_level * 0.3
        )))

        return {
            "red":    float(max(0.0, min(1.0, r))),
            "green":  float(max(0.0, min(1.0, g))),
            "blue":   float(max(0.0, min(1.0, b))),
            "dimmer": dimmer,
        }

    # ─────────────────────────────────────────────────────────────────────
    #  统一调度入口：将旋律分析结果应用到全部 DMX 通道
    # ─────────────────────────────────────────────────────────────────────

    def apply_to_dmx_values(
        self,
        raw_values: Dict[str, float],
        energy: float,
        rhythm: float,
        tension: float,
        melody: float = 0.5,
        # 旋律分析信号（可选，不提供则使用默认值）
        pitch_norm: float = 0.5,
        contour_vector: float = 0.0,
        adsr_level: float = 0.7,
        beat_impulse: float = 0.0,
        ornament_signal: float = 0.0,
        solo_intensity: float = 0.0,
        chord_tension: float = 0.0,
        color_hue: float = 0.0,
        color_saturation: float = 0.7,
        color_brightness: float = 0.8,
        device_index: int = 0,
        total_devices: int = 1,
    ) -> Dict[str, float]:
        """
        统一调度入口：将表现力矩阵应用到 DMX 原始值字典。

        支持两种模式：
        1. Effect 混合模式：使用 Effect 库的原始值作为基础，由权重矩阵调制
        2. Solo 路径模式：电机通道由 compute_motor_path 直接生成，绕过 Effect

        Args:
            raw_values:       {ch_type: value(0~1)}，来自 Effect 库的计算结果
            energy/rhythm/tension/melody: 音乐特征
            pitch_norm:       旋律音高归一化 [0,1]
            contour_vector:   旋律走向 [-1,1]
            adsr_level:       ADSR 包络 [0,1]
            beat_impulse:     节拍冲击 [0,1]
            ornament_signal:  装饰音信号 [0,1]
            solo_intensity:   Solo 强度 [0,1]
            chord_tension:    和弦张力 [0,1]
            color_hue:        和弦色相 [0,360]
            color_saturation: 色彩饱和度 [0,1]
            color_brightness: 色彩明度 [0,1]
            device_index:     设备索引（卡农偏移）
            total_devices:    设备总数

        Returns:
            调制后的 DMX 值字典
        """
        # Solo 模式自动切换：平滑过渡
        if self._auto_solo_enabled and solo_intensity > 0.5 and self._style != "solo":
            self._solo_blend = min(1.0, self._solo_blend + 0.05)
        elif self._solo_blend > 0:
            self._solo_blend = max(0.0, self._solo_blend - 0.02)

        result = {}
        for ch_type, val in raw_values.items():

            if ch_type == "pump":
                result[ch_type] = self.compute_pump_value(
                    val, energy, rhythm, tension,
                    pitch_norm, adsr_level, beat_impulse
                )

            elif ch_type == "valve":
                result[ch_type] = self.compute_valve_value(
                    val, energy, rhythm, tension, beat_impulse, adsr_level
                )

            elif ch_type in ("motor_x", "motor_y", "speed"):
                if solo_intensity > 0.5:
                    # Solo 模式：电机路径直接生成
                    path = self.compute_motor_path(
                        pitch_norm, contour_vector, ornament_signal,
                        adsr_level, solo_intensity, tension,
                        device_index, total_devices
                    )
                    # 在 Effect 路径和旋律路径之间平滑过渡
                    effect_val = self.compute_motor_value(
                        val, energy, rhythm, tension, melody,
                        pitch_norm, contour_vector, ornament_signal, solo_intensity, adsr_level
                    )
                    blend = self._solo_blend
                    path_val = path.get(ch_type, effect_val)
                    result[ch_type] = effect_val * (1 - blend) + path_val * blend
                else:
                    result[ch_type] = self.compute_motor_value(
                        val, energy, rhythm, tension, melody,
                        pitch_norm, contour_vector, ornament_signal, solo_intensity, adsr_level
                    )

            elif ch_type == "dimmer":
                result[ch_type] = self.compute_light_value(
                    val, energy, rhythm, tension,
                    adsr_level, beat_impulse, chord_tension, pitch_norm
                )

            elif ch_type in ("red", "green", "blue", "white"):
                # 灯光色彩由和弦调性决定
                color_out = self.compute_light_color(
                    color_hue, color_saturation, color_brightness,
                    energy, tension, adsr_level, beat_impulse
                )
                result[ch_type] = color_out.get(ch_type, val)

            else:
                result[ch_type] = val

        return result

    def apply_full(
        self,
        raw_values: Dict[str, float],
        energy: float,
        rhythm: float,
        tension: float,
        melody: float = 0.5,
        melody_result=None,  # MelodyAnalysisResult 对象（可选）
        device_index: int = 0,
        total_devices: int = 1,
    ) -> Dict[str, float]:
        """
        全量调度入口：直接接受 MelodyAnalysisResult 对象。

        这是最推荐的调用方式：将 MelodyAnalyzer 的分析结果
        直接传入，无需手动拆解各个字段。
        """
        kwargs: Dict[str, float] = {}
        if melody_result is not None:
            kwargs = {
                "pitch_norm":       melody_result.pitch_normalized,
                "contour_vector":   melody_result.contour_vector,
                "adsr_level":       melody_result.adsr.level,
                "beat_impulse":     melody_result.beat_impulse,
                "ornament_signal":  melody_result.ornament_signal,
                "solo_intensity":   melody_result.solo_intensity,
                "chord_tension":    melody_result.chord_tension,
                "color_hue":        melody_result.color_hue,
                "color_saturation": melody_result.color_saturation,
                "color_brightness": melody_result.color_brightness,
            }
        return self.apply_to_dmx_values(
            raw_values, energy, rhythm, tension, melody,
            device_index=device_index,
            total_devices=total_devices,
            **kwargs
        )


class VariationMode(Enum):
    """变奏模式：结构性重组，非随机扰动"""
    PHASE_INVERT    = "phase_invert"    # 相位翻转（同步→交错）
    PHASE_SHIFT     = "phase_shift"     # 相位偏移（整体移相）
    TIME_STRETCH    = "time_stretch"    # 时域拉伸（速度变化）
    TIME_COMPRESS   = "time_compress"   # 时域压缩
    GENE_CROSS      = "gene_cross"      # 动作基因杂交（两种动作混合）
    DENSITY_MORPH   = "density_morph"   # 密度形态变换（骨架→饱和）
    AMPLITUDE_SWELL = "amplitude_swell" # 振幅膨胀（能量积累）
    CANON_OFFSET    = "canon_offset"    # 卡农偏移（时间延迟追随）


class VariationStrengthDriver:
    """
    变奏强度驱动器：实现动作元的结构性演化。

    核心原则（规格书明确要求）：
    - 变奏必须是结构的重组（相位翻转、时域拉伸）
    - 严禁无意义的随机扰动
    - 手段：相位重构、时域重组、动作基因杂交

    实现的变奏手段：
    1. 相位重构：将同步改为交错，或将交错改为同步
    2. 时域重组：拉伸/压缩动作的时间尺度
    3. 动作基因杂交：将两种动作元的参数混合
    """

    def __init__(self, base_strength: float = 0.5):
        # 允许 base_strength > 1.0（极端变奏，如相位完全翻转 + 时域极速压缩）
        self._base_strength = float(max(0.0, base_strength))
        self._current_mode = VariationMode.PHASE_SHIFT
        self._mode_history: List[VariationMode] = []
    def update_params(self, base_strength: float) -> None:
        # 不硬性截断，允许超量变奏强度
        self._base_strength = float(max(0.0, base_strength))

    def compute_phase_variation(
        self,
        base_phase: float,
        voice_id: int,
        n_voices: int,
        strength: float,
        mode: VariationMode = VariationMode.PHASE_SHIFT,
    ) -> float:
        """
        计算相位变奏值（结构性，非随机）。

        Args:
            base_phase: 基础相位（0~1）
            voice_id: 声部 ID
            n_voices: 总声部数
            strength: 变奏强度（0~1）
            mode: 变奏模式

        Returns:
            变奏后的相位值
        """
        if mode == VariationMode.PHASE_INVERT:
            # 相位翻转：同步→交错
            # 每个声部获得均匀分布的相位偏移（结构性，非随机）
            offset = (voice_id / max(n_voices, 1)) * strength
            return (base_phase + offset) % 1.0

        elif mode == VariationMode.PHASE_SHIFT:
            # 相位偏移：整体移相
            shift = strength * 0.5  # 最大移相半个周期
            return (base_phase + shift * voice_id / max(n_voices, 1)) % 1.0

        elif mode == VariationMode.CANON_OFFSET:
            # 卡农偏移：每个声部延迟固定时间
            delay_fraction = 0.25 * voice_id  # 每声部延迟 1/4 周期
            return (base_phase + delay_fraction * strength) % 1.0

        else:
            return base_phase

    def compute_speed_variation(
        self,
        base_speed: float,
        voice_id: int,
        n_voices: int,
        strength: float,
        mode: VariationMode = VariationMode.TIME_STRETCH,
    ) -> float:
        """
        计算速度变奏值（时域重组）。

        Args:
            base_speed: 基础速度（Hz）
            voice_id: 声部 ID
            n_voices: 总声部数
            strength: 变奏强度（0~1）
            mode: 变奏模式

        Returns:
            变奏后的速度值
        """
        if mode == VariationMode.TIME_STRETCH:
            # 时域拉伸：速度降低（时间拉长）
            # 使用有理数比例，保持节奏结构
            ratios = [1.0, 0.75, 0.5, 1.5, 2.0]
            idx = int(voice_id * strength * len(ratios)) % len(ratios)
            return base_speed * ratios[idx]

        elif mode == VariationMode.TIME_COMPRESS:
            # 时域压缩：速度提高
            ratios = [1.0, 1.5, 2.0, 3.0, 4.0]
            idx = int(voice_id * strength * len(ratios)) % len(ratios)
            return base_speed * ratios[idx]

        elif mode == VariationMode.AMPLITUDE_SWELL:
            # 振幅膨胀：速度随能量积累
            swell_factor = 1.0 + strength * 0.5 * voice_id
            return base_speed * swell_factor

        else:
            return base_speed

    def compute_gene_cross(
        self,
        params_a: Dict[str, float],
        params_b: Dict[str, float],
        cross_ratio: float,
        strength: float,
    ) -> Dict[str, float]:
        """
        动作基因杂交：将两种动作元的参数按比例混合。

        这是规格书中"动作基因杂交"的核心实现：
        - 不是简单的线性插值（那是随机扰动）
        - 而是按照结构性的比例（如 1:3, 1:1, 3:1）进行混合

        Args:
            params_a: 动作元 A 的参数
            params_b: 动作元 B 的参数
            cross_ratio: 杂交比例（0=纯A, 1=纯B）
            strength: 变奏强度（控制杂交的激进程度）

        Returns:
            杂交后的参数字典
        """
        # 使用结构性比例（非连续线性）
        # 强度越高，杂交越激进（跳跃式而非渐变）
        if strength < 0.3:
            # 低强度：轻微杂交（接近 A）
            effective_ratio = cross_ratio * 0.3
        elif strength < 0.7:
            # 中强度：均衡杂交
            effective_ratio = cross_ratio
        else:
            # 高强度：激进杂交（接近 B 或完全翻转）
            effective_ratio = min(1.0, cross_ratio * 1.5)

        result = {}
        all_keys = set(params_a.keys()) | set(params_b.keys())
        for key in all_keys:
            val_a = params_a.get(key, 0.0)
            val_b = params_b.get(key, 0.0)
            result[key] = val_a * (1.0 - effective_ratio) + val_b * effective_ratio

        return result

    def select_variation_mode(
        self,
        energy: float,
        tension: float,
        in_buildup: bool,
        phrase_progress: float,
    ) -> VariationMode:
        """
        根据音乐上下文自动选择最合适的变奏模式。
        """
        if in_buildup:
            # Build-up：时域压缩（速度加快，制造紧张感）
            return VariationMode.TIME_COMPRESS

        if tension > 0.8:
            # 高张力：相位翻转（制造混乱感）
            return VariationMode.PHASE_INVERT

        if energy > 0.7:
            # 高能量：振幅膨胀
            return VariationMode.AMPLITUDE_SWELL

        if phrase_progress > 0.7:
            # 乐句末尾：基因杂交（为下一乐句做准备）
            return VariationMode.GENE_CROSS

        # 默认：相位偏移（卡农效果）
        return VariationMode.CANON_OFFSET


# ═══════════════════════════════════════════════════════════════════════════
#  5. 衔接与演化控制器（Transition Controller）
# ═══════════════════════════════════════════════════════════════════════════

class TransitionStyle(Enum):
    """过渡风格"""
    ORGANIC  = "organic"   # 有机过渡：自然生长与消融
    RHYTHMIC = "rhythmic"  # 节奏过渡：对齐到 BPM
    MORPH    = "morph"     # 形态变换：空间渐变
    SNAP     = "snap"      # 即时切换（高紧迫度）


@dataclass
class TransitionState:
    """过渡状态"""
    is_active: bool = False
    progress: float = 0.0           # 0~1
    duration: float = 1.0           # 秒
    urgency: float = 0.5            # 过渡迫切度（0~1）
    style: TransitionStyle = TransitionStyle.ORGANIC
    # 形态特征（用于计算演化路径）
    from_amplitude: float = 0.8
    to_amplitude: float = 0.8
    from_density: float = 0.5
    to_density: float = 0.5
    from_phase: float = 0.0
    to_phase: float = 0.0


class TransitionController:
    """
    衔接与演化控制器：控制动作切换时的形态连续性。

    核心逻辑：
    - 根据过渡迫切度（Urgency）计算形态演化路径
    - 确保水柱在空间和动态上的自然生长与消融
    - 不是简单的淡入淡出，而是形态特征的结构性演化
    """

    def __init__(self, style: TransitionStyle = TransitionStyle.ORGANIC):
        self._style = style
        self._states: Dict[int, TransitionState] = {}  # voice_id → state

    def update_style(self, style: TransitionStyle) -> None:
        self._style = style

    def start_transition(
        self,
        voice_id: int,
        urgency: float,
        bpm: float,
        from_state: Dict[str, float],
        to_state: Dict[str, float],
    ) -> TransitionState:
        """
        启动一个声部的形态过渡。

        Args:
            voice_id: 声部 ID
            urgency: 过渡迫切度（0=慢速, 1=立即）
            bpm: 当前 BPM
            from_state: 起始形态特征
            to_state: 目标形态特征

        Returns:
            TransitionState: 过渡状态
        """
        # urgency 允许 > 1.0（超紧急：立即切换）
        urgency = float(max(0.0, urgency))
        beat_sec = 60.0 / max(bpm, 1.0)  # BPM 下限从 60 降至 1，支持极慢速

        # 根据风格和紧迫度计算过渡时长
        if urgency > 0.9 or self._style == TransitionStyle.SNAP:
            duration = 0.05  # 即时切换
        elif self._style == TransitionStyle.RHYTHMIC:
            # 节奏过渡：对齐到 BPM
            if urgency > 0.7:
                duration = beat_sec      # 1 拍
            else:
                duration = beat_sec * 2  # 2 拍
        elif self._style == TransitionStyle.MORPH:
            # 形态变换：较慢的空间渐变
            duration = 2.0 * (1.0 - urgency * 0.5)
        else:  # ORGANIC
            # 有机过渡：根据紧迫度
            duration = 1.5 * (1.0 - urgency * 0.6)

        state = TransitionState(
            is_active=True,
            progress=0.0,
            duration=max(0.05, duration),
            urgency=urgency,
            style=self._style,
            from_amplitude=from_state.get("amplitude", 0.8),
            to_amplitude=to_state.get("amplitude", 0.8),
            from_density=from_state.get("density", 0.5),
            to_density=to_state.get("density", 0.5),
            from_phase=from_state.get("phase", 0.0),
            to_phase=to_state.get("phase", 0.0),
        )
        self._states[voice_id] = state
        return state

    def update(self, voice_id: int, dt: float) -> Tuple[bool, float]:
        """
        更新过渡进度。

        Returns:
            (is_complete, blend_factor)
            blend_factor: 0=起始形态, 1=目标形态
        """
        state = self._states.get(voice_id)
        if state is None or not state.is_active:
            return False, 1.0

        state.progress += dt / max(state.duration, 0.001)

        if state.progress >= 1.0:
            state.progress = 1.0
            state.is_active = False
            return True, 1.0

        # 计算混合因子（使用有机曲线，非线性）
        blend = self._compute_blend_curve(state.progress, state.style)
        return False, blend

    def _compute_blend_curve(self, progress: float, style: TransitionStyle) -> float:
        """
        计算过渡混合曲线（有机曲线，非线性插值）。
        确保水柱的自然生长与消融。
        """
        t = float(np.clip(progress, 0.0, 1.0))

        if style == TransitionStyle.ORGANIC:
            # 有机曲线：慢起快中慢收（S 形）
            return t * t * (3.0 - 2.0 * t)

        elif style == TransitionStyle.RHYTHMIC:
            # 节奏曲线：在节拍点快速切换（阶梯形）
            if t < 0.5:
                return t * 0.2  # 前半段慢速准备
            else:
                return 0.1 + (t - 0.5) * 1.8  # 后半段快速切换

        elif style == TransitionStyle.MORPH:
            # 形态变换：线性渐变（空间上的均匀过渡）
            return t

        elif style == TransitionStyle.SNAP:
            # 即时切换
            return 1.0 if t > 0.1 else 0.0

        return t

    def get_interpolated_params(
        self, voice_id: int
    ) -> Optional[Dict[str, float]]:
        """
        获取当前过渡进度下的插值参数。
        """
        state = self._states.get(voice_id)
        if state is None:
            return None

        _, blend = self.update.__func__(self, voice_id, 0.0)  # 不更新，只读取

        return {
            "amplitude": state.from_amplitude * (1 - blend) + state.to_amplitude * blend,
            "density": state.from_density * (1 - blend) + state.to_density * blend,
            "phase": state.from_phase * (1 - blend) + state.to_phase * blend,
            "blend": blend,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  6. 空间叙事与记忆（Spatial Narrative）
# ═══════════════════════════════════════════════════════════════════════════

class SpatialRole(Enum):
    """空间叙事角色"""
    QUESTION = "question"   # 问句：向外/左侧拓扑
    ANSWER   = "answer"     # 答句：向内/右侧拓扑
    NEUTRAL  = "neutral"    # 中性


@dataclass
class ThemeMemory:
    """主题记忆：记录某个段落的动作主题，用于再现时复用"""
    section_label: str
    action_type_name: str
    topo_type_name: str
    phase: float
    speed: float
    density: float
    visit_count: int = 1
    last_seen_t: float = 0.0


class SpatialNarrativeEngine:
    """
    空间叙事与记忆引擎：实现规格书第六章的空间叙事逻辑。

    核心功能：
    1. 问答映射：
       - 问句（Antecedent）→ 向外/左侧拓扑（DIFFUSE_OUT / LEFT_HALF）
       - 答句（Consequent）→ 向内/右侧拓扑（DIFFUSE_IN / RIGHT_HALF）

    2. 主题记忆：
       - 段落再现时复用并演化之前的主题动作
       - 记录每个段落的动作基因，形成音乐记忆
    """

    def __init__(self):
        self._theme_memory: Dict[str, ThemeMemory] = {}
        self._current_spatial_role = SpatialRole.NEUTRAL
        self._last_phrase_role: Optional[str] = None

    # ── 问答拓扑映射 ─────────────────────────────────────────────────────

    def get_spatial_topo_for_phrase(
        self,
        phrase_role: str,
        voice_id: int,
        n_voices: int,
    ) -> str:
        """
        根据乐句角色返回对应的拓扑类型名称。

        规格书定义：
        - 问句 → 向外/左侧拓扑
        - 答句 → 向内/右侧拓扑
        """
        if phrase_role == "antecedent":
            # 问句：向外/左侧拓扑
            self._current_spatial_role = SpatialRole.QUESTION
            if voice_id % 2 == 0:
                return "diffuse_out"   # 向外扩散
            else:
                return "left_half"     # 左半场

        elif phrase_role == "consequent":
            # 答句：向内/右侧拓扑
            self._current_spatial_role = SpatialRole.ANSWER
            if voice_id % 2 == 0:
                return "diffuse_in"    # 向内聚合
            else:
                return "right_half"    # 右半场

        elif phrase_role == "climax":
            # 高潮：全场扩散
            self._current_spatial_role = SpatialRole.NEUTRAL
            return "full"

        else:
            # 中性/过渡：保持当前
            return "full"

    def get_spatial_role(self) -> SpatialRole:
        return self._current_spatial_role

    # ── 主题记忆 ─────────────────────────────────────────────────────────

    def record_theme(
        self,
        section_label: str,
        action_type_name: str,
        topo_type_name: str,
        phase: float,
        speed: float,
        density: float,
        current_t: float,
    ) -> None:
        """
        记录当前段落的动作主题。
        """
        if section_label in self._theme_memory:
            # 更新已有记忆
            mem = self._theme_memory[section_label]
            mem.visit_count += 1
            mem.last_seen_t = current_t
            # 演化：速度和密度随访问次数微调（结构性演化，非随机）
            evolution_factor = 1.0 + 0.05 * (mem.visit_count - 1)
            mem.speed = speed * evolution_factor
            mem.density = min(1.0, density * evolution_factor)
        else:
            # 新段落，创建记忆
            self._theme_memory[section_label] = ThemeMemory(
                section_label=section_label,
                action_type_name=action_type_name,
                topo_type_name=topo_type_name,
                phase=phase,
                speed=speed,
                density=density,
                visit_count=1,
                last_seen_t=current_t,
            )

    def recall_theme(
        self, section_label: str
    ) -> Optional[ThemeMemory]:
        """
        召回某段落的主题记忆。
        段落再现时调用此方法，复用并演化之前的主题动作。
        """
        return self._theme_memory.get(section_label)

    def has_theme(self, section_label: str) -> bool:
        return section_label in self._theme_memory

    def get_all_themes(self) -> Dict[str, ThemeMemory]:
        return dict(self._theme_memory)

    def on_phrase_change(
        self,
        new_phrase_role: str,
        old_phrase_role: Optional[str],
    ) -> Optional[str]:
        """
        乐句角色变化时的处理。
        返回建议的空间拓扑切换方向（可为 None）。
        """
        if new_phrase_role == old_phrase_role:
            return None

        self._last_phrase_role = old_phrase_role

        if new_phrase_role == "antecedent" and old_phrase_role == "consequent":
            # 答句→问句：切换到向外/左侧
            return "outward_left"
        elif new_phrase_role == "consequent" and old_phrase_role == "antecedent":
            # 问句→答句：切换到向内/右侧
            return "inward_right"
        elif new_phrase_role == "climax":
            # 进入高潮：全场扩散
            return "full_burst"

        return None
