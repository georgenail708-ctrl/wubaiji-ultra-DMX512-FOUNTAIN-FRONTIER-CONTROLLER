"""
device_capability.py
──────────────────────────────────────────────────────────────────────────────
DeepAqua 设备能力自动识别与编舞策略选择系统

核心职责：
  从 FixtureDefinition 的通道列表中自动识别每台设备的运动能力，
  并为其选择最合适的 AI 编舞策略。

设计原则：
  "对于无电机的设备，或者是开合方式的电机，能够自动识别，采用合适的编曲方式"

设备运动能力分类（MotorCapability）：
  FULL_XYZ        全自由度电机（X+Y+Z 三轴）→ 细腻路径生成，旋律音高→仰角，走向→摆动
  FULL_XY         双轴电机（X+Y）→ 平面路径生成，旋律驱动二维轨迹
  SINGLE_AXIS     单轴连续电机（仅 X 或仅 Y）→ 旋律音高→角度，节拍→速度
  OPEN_CLOSE      开合式电机（单轴，二值/有限角度）→ 节拍触发开合，张力→开合幅度
  NO_MOTOR        无电机（纯泵+灯）→ 泵高度+灯光承担全部表现力
  PUMP_ONLY       仅泵（无电机无灯）→ 旋律音高→水柱高度
  LIGHT_ONLY      仅灯光（无电机无泵）→ 颜色/强度/频闪承担全部编舞
  MATRIX_CELL     矩阵单元（水膜/矩阵喷泉的一格）→ 2D 图案生成
  FOUNTAIN_JET    直流跑泉水柱（独立流量控制）→ 旋律音符→水柱高度

编舞策略（ChoreographyStrategy）：
  每种能力对应一套编舞参数权重，决定各通道的表现力分配比例。
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any


# ═══════════════════════════════════════════════════════════════════════════
#  1. 设备运动能力枚举
# ═══════════════════════════════════════════════════════════════════════════

class MotorCapability(Enum):
    """设备的电机运动能力分类"""
    FULL_XYZ    = "full_xyz"      # 三轴全自由度
    FULL_XY     = "full_xy"       # 双轴（X+Y）
    SINGLE_AXIS = "single_axis"   # 单轴连续
    OPEN_CLOSE  = "open_close"    # 开合式（二值/有限角度）
    NO_MOTOR    = "no_motor"      # 无电机
    PUMP_ONLY   = "pump_only"     # 仅泵
    LIGHT_ONLY  = "light_only"    # 仅灯光
    MATRIX_CELL = "matrix_cell"   # 矩阵单元
    FOUNTAIN_JET = "fountain_jet" # 直流跑泉水柱


class ChoreographyMode(Enum):
    """编舞模式：决定 AI 如何为该设备生成动作"""
    # 电机主导模式
    MELODIC_PATH    = "melodic_path"    # 旋律路径（全自由电机）：音高→仰角，走向→摆动
    PLANAR_PATH     = "planar_path"     # 平面路径（双轴电机）：旋律驱动 XY 轨迹
    LINEAR_SWEEP    = "linear_sweep"    # 线性扫描（单轴电机）：音高→角度，节拍→速度
    RHYTHMIC_GATE   = "rhythmic_gate"   # 节奏开合（开合电机）：节拍触发，张力→幅度

    # 无电机模式（泵+灯承担全部表现力）
    PUMP_MELODY     = "pump_melody"     # 泵旋律（无电机）：旋律→水柱高度，和弦→水压
    LIGHT_HARMONY   = "light_harmony"   # 灯光和声（仅灯）：和弦→颜色，能量→强度
    PUMP_LIGHT_FULL = "pump_light_full" # 泵+灯全表现（无电机）：双通道共同承担

    # 矩阵/跑泉模式
    MATRIX_PATTERN  = "matrix_pattern"  # 矩阵图案：2D 波形生成
    FOUNTAIN_NOTE   = "fountain_note"   # 跑泉音符：旋律音符→对应水柱高度


# ═══════════════════════════════════════════════════════════════════════════
#  2. 设备能力档案
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceCapabilityProfile:
    """
    单台设备的完整能力档案。

    由 DeviceCapabilityClassifier 自动生成，包含：
    - 运动能力分类
    - 推荐编舞模式
    - 各通道的表现力权重
    - 运动参数约束
    """
    device_id: str = ""
    name: str = ""

    # ── 能力分类 ─────────────────────────────────────────────────────────
    motor_capability: MotorCapability = MotorCapability.NO_MOTOR
    choreography_mode: ChoreographyMode = ChoreographyMode.PUMP_LIGHT_FULL

    # ── 通道存在性 ────────────────────────────────────────────────────────
    has_motor_x: bool = False
    has_motor_y: bool = False
    has_motor_z: bool = False
    has_pump: bool = False
    has_valve: bool = False
    has_dimmer: bool = False
    has_rgb: bool = False           # 有 R/G/B 通道
    has_white: bool = False

    # ── 电机参数约束 ──────────────────────────────────────────────────────
    motor_axes: int = 0             # 电机轴数（0/1/2/3）
    motor_range_deg: float = 360.0  # 电机运动范围（度）
    is_open_close: bool = False     # 是否为开合式（二值）
    open_close_threshold: float = 0.5  # 开合阈值

    # ── 表现力权重（各通道对旋律/节奏/和弦的响应比例）────────────────────
    # 这些权重决定在 ExpressionMatrixDriver 中各通道的基础响应强度
    motor_expression_weight: float = 1.0   # 电机对旋律的响应权重
    pump_expression_weight: float = 0.8    # 泵对旋律的响应权重
    light_expression_weight: float = 0.7   # 灯光对旋律的响应权重
    valve_expression_weight: float = 0.5   # 阀门对节拍的响应权重

    # ── 编舞参数 ──────────────────────────────────────────────────────────
    melody_to_motor_axis: str = "motor_x"  # 旋律音高映射到哪个电机轴
    rhythm_to_motor_axis: str = "motor_y"  # 节奏映射到哪个电机轴
    pitch_range: Tuple[float, float] = (0.0, 1.0)  # 音高映射范围（归一化）

    # ── 特殊标记 ──────────────────────────────────────────────────────────
    is_matrix_cell: bool = False    # 是矩阵单元
    is_fountain_jet: bool = False   # 是跑泉水柱
    fountain_index: int = -1        # 跑泉水柱索引

    # ── DMX 合法输出域 ────────────────────────────────────────────────────
    # 设备能接受的 DMX 输出值范围（0..255 域内）。
    # 默认 0/255 表示全域可用（与历史行为一致）；
    # 部分设备（如限幅水泵、限亮灯具）只在子区间内有效。
    out_lo: int = 0                 # DMX 合法输出下限
    out_hi: int = 255               # DMX 合法输出上限

    def get_active_channels(self) -> List[str]:
        """返回该设备有效的通道类型列表"""
        channels = []
        if self.has_motor_x:
            channels.append("motor_x")
        if self.has_motor_y:
            channels.append("motor_y")
        if self.has_motor_z:
            channels.append("motor_z")
        if self.has_pump:
            channels.append("pump")
        if self.has_valve:
            channels.append("valve")
        if self.has_dimmer:
            channels.append("dimmer")
        if self.has_rgb:
            channels.extend(["R", "G", "B"])
        if self.has_white:
            channels.append("W")
        return channels

    def can_express_melody(self) -> bool:
        """是否能够表达旋律（有连续控制通道）"""
        return (
            self.has_motor_x or self.has_motor_y or self.has_motor_z
            or self.has_pump
        ) and not self.is_open_close

    def can_express_rhythm(self) -> bool:
        """是否能够表达节奏（有快速响应通道）"""
        return self.has_valve or self.has_pump or self.is_open_close

    def can_express_harmony(self) -> bool:
        """是否能够表达和声（有颜色通道）"""
        return self.has_rgb or self.has_dimmer

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为 dict。

        当前只导出对外部消费者（如音乐桥的公共区间计算）有意义的稳定字段，
        其中 out_lo/out_hi 为 DMX 合法输出域。其余分类字段由 classifier
        自动再生成，无需持久化。
        """
        return {
            "device_id": self.device_id,
            "name": self.name,
            "out_lo": int(self.out_lo),
            "out_hi": int(self.out_hi),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DeviceCapabilityProfile":
        """
        从 dict 还原。缺省 out_lo/out_hi 时回落到 0/255（全域），
        保证旧数据与缺字段场景下行为与历史一致。
        """
        d = d or {}
        try:
            out_lo = int(d.get("out_lo", 0))
        except (TypeError, ValueError):
            out_lo = 0
        try:
            out_hi = int(d.get("out_hi", 255))
        except (TypeError, ValueError):
            out_hi = 255
        return cls(
            device_id=str(d.get("device_id", "")),
            name=str(d.get("name", "")),
            out_lo=out_lo,
            out_hi=out_hi,
        )


# ═══════════════════════════════════════════════════════════════════════════
#  3. DeviceCapabilityClassifier：自动识别设备能力
# ═══════════════════════════════════════════════════════════════════════════

class DeviceCapabilityClassifier:
    """
    设备能力自动识别器。

    从 FixtureDefinition 的通道列表（channels: List[str]）中
    自动识别每台设备的运动能力，并生成对应的编舞策略。

    识别逻辑：
    1. 解析通道列表，统计各类通道的存在性
    2. 根据电机轴数和组合判断运动自由度
    3. 检测开合式电机（单轴 + 特定命名规则）
    4. 根据能力组合选择最优编舞模式
    5. 计算各通道的表现力权重

    开合式电机识别规则：
    - 仅有 motor_x（无 motor_y/motor_z）
    - 且设备名称包含 "开合"/"open"/"gate"/"shutter"/"door" 等关键词
    - 或者通道数量 <= 3（简单设备）且仅有单轴电机
    """

    # 开合式设备名称关键词
    OPEN_CLOSE_KEYWORDS = {
        "开合", "open", "close", "gate", "shutter", "door",
        "flap", "iris", "diaphragm", "百叶", "翻板", "翻转",
        "flip", "rotate_gate", "swing",
    }

    # 直流跑泉设备名称关键词
    FOUNTAIN_KEYWORDS = {
        "跑泉", "fountain", "jet", "nozzle", "直流", "water_jet",
        "stream", "column", "水柱", "喷嘴",
    }

    # 矩阵单元设备名称关键词
    MATRIX_KEYWORDS = {
        "矩阵", "matrix", "grid", "cell", "水膜", "film",
        "curtain", "wall", "screen", "panel",
    }

    def classify(
        self,
        device_id: str,
        device_name: str,
        channels: List[str],
        extra_hints: Optional[Dict[str, Any]] = None,
    ) -> DeviceCapabilityProfile:
        """
        对单台设备进行能力分类。

        Args:
            device_id:    设备 ID
            device_name:  设备名称（用于关键词识别）
            channels:     通道类型列表（ChannelType.value 字符串）
            extra_hints:  额外提示信息（如 motor_range_deg 等）

        Returns:
            DeviceCapabilityProfile
        """
        profile = DeviceCapabilityProfile(
            device_id=device_id,
            name=device_name,
        )

        # 1. 解析通道存在性
        ch_set = set(ch.lower() for ch in channels)
        profile.has_motor_x = "motor_x" in ch_set
        profile.has_motor_y = "motor_y" in ch_set
        profile.has_motor_z = "motor_z" in ch_set
        profile.has_pump    = "pump" in ch_set
        profile.has_valve   = "valve" in ch_set
        profile.has_dimmer  = "dimmer" in ch_set
        profile.has_rgb     = ("r" in ch_set or "red" in ch_set) and \
                              ("g" in ch_set or "green" in ch_set) and \
                              ("b" in ch_set or "blue" in ch_set)
        profile.has_white   = "w" in ch_set or "white" in ch_set

        # 2. 计算电机轴数
        motor_axes = sum([
            profile.has_motor_x,
            profile.has_motor_y,
            profile.has_motor_z,
        ])
        profile.motor_axes = motor_axes

        # 3. 从 extra_hints 读取额外信息
        if extra_hints:
            profile.motor_range_deg = float(extra_hints.get("motor_range_deg", 360.0))
            profile.fountain_index = int(extra_hints.get("fountain_index", -1))

        # 4. 名称关键词检测
        name_lower = device_name.lower()
        is_open_close_name = any(kw in name_lower for kw in self.OPEN_CLOSE_KEYWORDS)
        is_fountain_name   = any(kw in name_lower for kw in self.FOUNTAIN_KEYWORDS)
        is_matrix_name     = any(kw in name_lower for kw in self.MATRIX_KEYWORDS)

        # 5. 开合式电机检测
        # 规则：单轴电机 + 名称关键词（必须有关键词才能识别为开合，避免误判连续单轴电机）
        if motor_axes == 1 and not profile.has_motor_y and not profile.has_motor_z:
            if is_open_close_name:
                profile.is_open_close = True

        # 6. 特殊设备类型标记
        if is_fountain_name or profile.fountain_index >= 0:
            profile.is_fountain_jet = True
        if is_matrix_name:
            profile.is_matrix_cell = True

        # 7. 确定运动能力分类
        profile.motor_capability = self._classify_motor(profile, is_open_close_name)

        # 8. 选择编舞模式
        profile.choreography_mode = self._select_choreography_mode(profile)

        # 9. 计算表现力权重
        self._compute_expression_weights(profile)

        # 10. 设置电机轴映射
        self._setup_motor_axis_mapping(profile)

        return profile

    def classify_batch(
        self,
        fixture_defs: Dict[str, Any],
        fixture_instances: Optional[List[Dict]] = None,
    ) -> Dict[str, DeviceCapabilityProfile]:
        """
        批量分类所有设备。

        Args:
            fixture_defs: {fixture_id: FixtureDefinition 或 dict}
            fixture_instances: 设备实例列表（可选，用于获取设备名称）

        Returns:
            {device_id: DeviceCapabilityProfile}
        """
        profiles = {}

        # 构建 instance_id → name 映射
        instance_names: Dict[str, str] = {}
        if fixture_instances:
            for inst in fixture_instances:
                iid = str(inst.get("id", ""))
                name = str(inst.get("name", iid))
                instance_names[iid] = name

        for fid, fdef in fixture_defs.items():
            # 支持 dict 或 FixtureDefinition 对象
            if hasattr(fdef, "channels"):
                channels = list(fdef.channels)
                name = getattr(fdef, "name", str(fid))
            elif isinstance(fdef, dict):
                channels = list(fdef.get("channels", []))
                name = fdef.get("name", str(fid))
            else:
                continue

            # 支持 [{"type": "motor_x"}, ...] 或 ["motor_x", ...] 两种 channels 格式
            if channels and isinstance(channels[0], dict):
                channels = [c.get("type", c.get("ch_type", "")) for c in channels if isinstance(c, dict)]
            # 优先使用实例名称
            display_name = instance_names.get(str(fid), name)
            profile = self.classify(str(fid), display_name, channels)
            profiles[str(fid)] = profile

        return profiles

    # ─────────────────────────────────────────────────────────────────────
    #  内部分类逻辑
    # ─────────────────────────────────────────────────────────────────────

    def _classify_motor(
        self,
        profile: DeviceCapabilityProfile,
        is_open_close_name: bool,
    ) -> MotorCapability:
        """根据通道组合判断电机能力"""
        axes = profile.motor_axes

        # 特殊设备优先
        if profile.is_fountain_jet:
            return MotorCapability.FOUNTAIN_JET
        if profile.is_matrix_cell:
            return MotorCapability.MATRIX_CELL

        # 无电机
        if axes == 0:
            if not profile.has_pump and not profile.has_valve:
                if profile.has_dimmer or profile.has_rgb:
                    return MotorCapability.LIGHT_ONLY
                return MotorCapability.NO_MOTOR
            if not profile.has_dimmer and not profile.has_rgb:
                return MotorCapability.PUMP_ONLY
            return MotorCapability.NO_MOTOR

        # 有电机
        if axes >= 3:
            return MotorCapability.FULL_XYZ
        if axes == 2:
            return MotorCapability.FULL_XY
        # 单轴
        if profile.is_open_close or is_open_close_name:
            return MotorCapability.OPEN_CLOSE
        return MotorCapability.SINGLE_AXIS

    def _select_choreography_mode(
        self,
        profile: DeviceCapabilityProfile,
    ) -> ChoreographyMode:
        """根据运动能力选择最优编舞模式"""
        cap = profile.motor_capability

        if cap == MotorCapability.FULL_XYZ:
            return ChoreographyMode.MELODIC_PATH
        elif cap == MotorCapability.FULL_XY:
            return ChoreographyMode.PLANAR_PATH
        elif cap == MotorCapability.SINGLE_AXIS:
            return ChoreographyMode.LINEAR_SWEEP
        elif cap == MotorCapability.OPEN_CLOSE:
            return ChoreographyMode.RHYTHMIC_GATE
        elif cap == MotorCapability.FOUNTAIN_JET:
            return ChoreographyMode.FOUNTAIN_NOTE
        elif cap == MotorCapability.MATRIX_CELL:
            return ChoreographyMode.MATRIX_PATTERN
        elif cap == MotorCapability.LIGHT_ONLY:
            return ChoreographyMode.LIGHT_HARMONY
        elif cap == MotorCapability.PUMP_ONLY:
            return ChoreographyMode.PUMP_MELODY
        else:
            # NO_MOTOR：泵+灯共同承担
            if profile.has_pump and (profile.has_dimmer or profile.has_rgb):
                return ChoreographyMode.PUMP_LIGHT_FULL
            elif profile.has_pump:
                return ChoreographyMode.PUMP_MELODY
            else:
                return ChoreographyMode.LIGHT_HARMONY

    def _compute_expression_weights(self, profile: DeviceCapabilityProfile) -> None:
        """
        计算各通道的表现力权重。

        原则：
        - 有电机的设备：电机承担主要旋律表现，泵/灯辅助
        - 无电机的设备：泵和灯分担全部表现力（权重提升）
        - 开合式电机：节奏权重高，旋律权重低
        - 仅灯设备：灯光权重最高
        """
        cap = profile.motor_capability

        if cap in (MotorCapability.FULL_XYZ, MotorCapability.FULL_XY):
            # 全自由电机：电机主导旋律，泵辅助高度，灯辅助氛围
            profile.motor_expression_weight = 1.0
            profile.pump_expression_weight  = 0.6
            profile.light_expression_weight = 0.5
            profile.valve_expression_weight = 0.4

        elif cap == MotorCapability.SINGLE_AXIS:
            # 单轴电机：电机表达旋律走向，泵表达音高
            profile.motor_expression_weight = 0.9
            profile.pump_expression_weight  = 0.8
            profile.light_expression_weight = 0.6
            profile.valve_expression_weight = 0.5

        elif cap == MotorCapability.OPEN_CLOSE:
            # 开合式：节奏驱动开合，泵和灯承担旋律/和声
            profile.motor_expression_weight = 0.3  # 电机只做节拍开合
            profile.pump_expression_weight  = 1.0  # 泵承担旋律高度
            profile.light_expression_weight = 0.9  # 灯承担和声颜色
            profile.valve_expression_weight = 0.8  # 阀门跟随节拍

        elif cap == MotorCapability.NO_MOTOR:
            # 无电机：泵+灯共同承担全部表现力（权重提升）
            profile.motor_expression_weight = 0.0
            profile.pump_expression_weight  = 1.0
            profile.light_expression_weight = 1.0
            profile.valve_expression_weight = 0.7

        elif cap == MotorCapability.PUMP_ONLY:
            # 仅泵：泵承担全部表现力
            profile.motor_expression_weight = 0.0
            profile.pump_expression_weight  = 1.0
            profile.light_expression_weight = 0.0
            profile.valve_expression_weight = 0.6

        elif cap == MotorCapability.LIGHT_ONLY:
            # 仅灯：灯光承担全部表现力
            profile.motor_expression_weight = 0.0
            profile.pump_expression_weight  = 0.0
            profile.light_expression_weight = 1.0
            profile.valve_expression_weight = 0.0

        elif cap == MotorCapability.FOUNTAIN_JET:
            # 直流跑泉：泵高度是主要表达，灯辅助
            profile.motor_expression_weight = 0.0
            profile.pump_expression_weight  = 1.0
            profile.light_expression_weight = 0.8
            profile.valve_expression_weight = 0.9

        elif cap == MotorCapability.MATRIX_CELL:
            # 矩阵单元：泵/阀门做图案，灯做颜色
            profile.motor_expression_weight = 0.0
            profile.pump_expression_weight  = 0.9
            profile.light_expression_weight = 0.9
            profile.valve_expression_weight = 1.0

    def _setup_motor_axis_mapping(self, profile: DeviceCapabilityProfile) -> None:
        """设置旋律/节奏到电机轴的映射"""
        if profile.motor_axes == 0:
            return
        if profile.has_motor_x and profile.has_motor_y:
            # 双轴：X 轴表达旋律音高（仰角），Y 轴表达旋律走向（方向）
            profile.melody_to_motor_axis = "motor_x"
            profile.rhythm_to_motor_axis = "motor_y"
        elif profile.has_motor_x:
            profile.melody_to_motor_axis = "motor_x"
            profile.rhythm_to_motor_axis = "motor_x"
        elif profile.has_motor_y:
            profile.melody_to_motor_axis = "motor_y"
            profile.rhythm_to_motor_axis = "motor_y"
        elif profile.has_motor_z:
            profile.melody_to_motor_axis = "motor_z"
            profile.rhythm_to_motor_axis = "motor_z"


# ═══════════════════════════════════════════════════════════════════════════
#  4. CapabilityAwareChoreographyEngine：能力感知编舞引擎
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ChoreographyFrame:
    """
    单台设备在单帧的编舞输出。
    包含所有通道的目标值（0..1 归一化，DMX 层再乘以 255）。
    """
    device_id: str = ""
    # 电机通道
    motor_x: float = 0.5       # 0=最左/最低，0.5=中位，1=最右/最高
    motor_y: float = 0.5
    motor_z: float = 0.5
    # 泵通道
    pump: float = 0.0           # 0=关，1=最大流量
    # 阀门通道
    valve: float = 0.0          # 0=关，1=全开
    # 灯光通道
    dimmer: float = 0.0         # 0=熄灭，1=全亮
    red: float = 0.0
    green: float = 0.0
    blue: float = 0.0
    white: float = 0.0
    # 元数据
    choreography_mode: str = ""
    is_solo_mode: bool = False


class CapabilityAwareChoreographyEngine:
    """
    能力感知编舞引擎。

    根据每台设备的 DeviceCapabilityProfile，
    将音频信号（旋律/节奏/和弦）转换为该设备的最优动作输出。

    核心方法：
    compute_frame() — 计算单台设备在当前时刻的所有通道目标值
    """

    def compute_frame(
        self,
        cap_profile: DeviceCapabilityProfile,
        # 音频信号
        melody_pitch: float = 0.5,       # 旋律音高 [0,1]（0=最低，1=最高）
        melody_contour: float = 0.0,     # 旋律走向 [-1,1]（-1=下行，+1=上行）
        melody_velocity: float = 0.7,    # 旋律力度 [0,1]
        chord_tension: float = 0.3,      # 和弦张力 [0,1]
        chord_hue: float = 0.0,          # 和弦色相 [0,1]（由调性决定）
        chord_brightness: float = 0.7,   # 和弦亮度 [0,1]
        energy: float = 0.7,             # 音频能量 [0,1]
        beat_phase: float = 0.0,         # 节拍相位 [0,1]（0=强拍，0.5=弱拍）
        beat_strength: float = 0.5,      # 节拍强度 [0,1]
        phrase_phase: float = 0.5,       # 乐句相位 [0,1]
        is_solo: bool = False,           # 是否为 Solo 段落
        # 波形参数
        wave_value: float = 0.5,         # 来自 TopologyAwareWaveGenerator 的波形值
        linear_phase: float = 0.5,       # 设备在线性排列中的相位
    ) -> ChoreographyFrame:
        """
        计算单台设备在当前时刻的编舞输出。

        根据设备的 ChoreographyMode 选择对应的计算逻辑。
        """
        frame = ChoreographyFrame(
            device_id=cap_profile.device_id,
            choreography_mode=cap_profile.choreography_mode.value,
            is_solo_mode=is_solo,
        )

        mode = cap_profile.choreography_mode

        if mode == ChoreographyMode.MELODIC_PATH:
            self._compute_melodic_path(frame, cap_profile, melody_pitch, melody_contour,
                                        melody_velocity, chord_tension, chord_hue,
                                        chord_brightness, energy, beat_phase, wave_value, is_solo)

        elif mode == ChoreographyMode.PLANAR_PATH:
            self._compute_planar_path(frame, cap_profile, melody_pitch, melody_contour,
                                       chord_hue, chord_brightness, energy, beat_phase,
                                       wave_value, is_solo)

        elif mode == ChoreographyMode.LINEAR_SWEEP:
            self._compute_linear_sweep(frame, cap_profile, melody_pitch, melody_contour,
                                        energy, beat_phase, wave_value)

        elif mode == ChoreographyMode.RHYTHMIC_GATE:
            self._compute_rhythmic_gate(frame, cap_profile, chord_tension, energy,
                                         beat_phase, beat_strength, melody_pitch,
                                         chord_hue, chord_brightness)

        elif mode == ChoreographyMode.FOUNTAIN_NOTE:
            self._compute_fountain_note(frame, cap_profile, melody_pitch, melody_contour,
                                         chord_hue, chord_brightness, energy, wave_value)

        elif mode == ChoreographyMode.MATRIX_PATTERN:
            self._compute_matrix_pattern(frame, cap_profile, chord_hue, chord_brightness,
                                          energy, beat_phase, wave_value)

        elif mode == ChoreographyMode.PUMP_MELODY:
            self._compute_pump_melody(frame, cap_profile, melody_pitch, melody_velocity,
                                       chord_hue, chord_brightness, energy, beat_phase)

        elif mode == ChoreographyMode.LIGHT_HARMONY:
            self._compute_light_harmony(frame, cap_profile, chord_hue, chord_brightness,
                                         energy, beat_phase, melody_pitch)

        else:  # PUMP_LIGHT_FULL
            self._compute_pump_light_full(frame, cap_profile, melody_pitch, melody_contour,
                                           melody_velocity, chord_tension, chord_hue,
                                           chord_brightness, energy, beat_phase, wave_value)

        return frame

    # ─────────────────────────────────────────────────────────────────────
    #  各编舞模式的计算逻辑
    # ─────────────────────────────────────────────────────────────────────

    def _compute_melodic_path(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_contour: float, melody_velocity: float,
        chord_tension: float, chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float, wave_value: float, is_solo: bool,
    ) -> None:
        """
        全自由度电机的旋律路径编舞。

        Solo 模式：精细路径生成
          - motor_x（仰角）：旋律音高直接映射，高音=高仰角
          - motor_y（方向）：旋律走向驱动左右摆动
          - 装饰音：微颤（小幅快速振动）

        非 Solo 模式：波形驱动
          - 使用 TopologyAwareWaveGenerator 的波形值
          - 叠加旋律偏置
        """
        if is_solo:
            # Solo 模式：精细旋律路径
            # motor_x = 音高（仰角）
            frame.motor_x = 0.2 + 0.6 * melody_pitch  # 留出边界余量
            # motor_y = 旋律走向（摆动方向）
            # contour: -1=下行→左摆，+1=上行→右摆
            swing_amount = 0.15 + 0.2 * chord_tension  # 张力越大，摆动幅度越大
            frame.motor_y = 0.5 + swing_amount * melody_contour
            # motor_z = 力度（如有）
            if cap.has_motor_z:
                frame.motor_z = 0.3 + 0.5 * melody_velocity
        else:
            # 非 Solo：波形驱动 + 旋律偏置
            base = wave_value
            pitch_bias = (melody_pitch - 0.5) * 0.3
            frame.motor_x = max(0.0, min(1.0, base + pitch_bias))
            frame.motor_y = 0.5 + 0.3 * melody_contour * energy
            if cap.has_motor_z:
                frame.motor_z = 0.4 + 0.3 * energy

        # 泵：旋律音高 + 能量
        if cap.has_pump:
            frame.pump = (0.3 + 0.5 * melody_pitch) * cap.pump_expression_weight * energy

        # 阀门：跟随节拍
        if cap.has_valve:
            beat_open = 0.6 + 0.4 * math.sin(2 * math.pi * beat_phase)
            frame.valve = max(0.0, min(1.0, beat_open * cap.valve_expression_weight))

        # 灯光：和弦调性颜色 + 能量强度
        if cap.has_dimmer or cap.has_rgb:
            self._apply_light_harmony(frame, cap, chord_hue, chord_brightness, energy, beat_phase)

    def _compute_planar_path(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_contour: float,
        chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float, wave_value: float, is_solo: bool,
    ) -> None:
        """双轴电机的平面路径编舞"""
        if is_solo:
            frame.motor_x = 0.2 + 0.6 * melody_pitch
            frame.motor_y = 0.5 + 0.3 * melody_contour
        else:
            frame.motor_x = max(0.0, min(1.0, wave_value + (melody_pitch - 0.5) * 0.2))
            frame.motor_y = 0.5 + 0.25 * melody_contour * energy

        if cap.has_pump:
            frame.pump = (0.2 + 0.6 * melody_pitch) * energy
        if cap.has_valve:
            frame.valve = 0.5 + 0.4 * math.sin(2 * math.pi * beat_phase)
        if cap.has_dimmer or cap.has_rgb:
            self._apply_light_harmony(frame, cap, chord_hue, chord_brightness, energy, beat_phase)

    def _compute_linear_sweep(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_contour: float,
        energy: float, beat_phase: float, wave_value: float,
    ) -> None:
        """单轴连续电机的线性扫描编舞"""
        # 旋律音高→角度，波形叠加
        target = 0.2 + 0.6 * melody_pitch
        blended = target * 0.6 + wave_value * 0.4
        if cap.has_motor_x:
            frame.motor_x = blended
        elif cap.has_motor_y:
            frame.motor_y = blended
        elif cap.has_motor_z:
            frame.motor_z = blended

        if cap.has_pump:
            frame.pump = (0.2 + 0.5 * melody_pitch) * energy
        if cap.has_valve:
            frame.valve = 0.4 + 0.5 * energy
        if cap.has_dimmer or cap.has_rgb:
            self._apply_light_harmony(frame, cap, 0.5, 0.7, energy, beat_phase)

    def _compute_rhythmic_gate(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        chord_tension: float, energy: float, beat_phase: float, beat_strength: float,
        melody_pitch: float, chord_hue: float, chord_brightness: float,
    ) -> None:
        """
        开合式电机的节奏开合编舞。

        开合逻辑：
        - 强拍（beat_phase < 0.15）→ 全开
        - 弱拍 → 根据张力决定开合幅度
        - 张力高 → 开合幅度大（戏剧性）
        - 张力低 → 微开（柔和）
        """
        # 节拍触发开合
        is_strong_beat = beat_phase < 0.15
        open_amount = 0.0

        if is_strong_beat:
            # 强拍：根据能量和张力决定开合幅度
            open_amount = 0.5 + 0.5 * min(1.0, energy + chord_tension * 0.3)
        else:
            # 弱拍：根据张力保持一定开度
            open_amount = 0.1 + 0.3 * chord_tension * (1.0 - beat_phase)

        # 应用到电机（开合式：0=关，1=全开）
        if cap.has_motor_x:
            frame.motor_x = open_amount
        elif cap.has_motor_y:
            frame.motor_y = open_amount

        # 泵：旋律音高（开合设备的泵承担旋律表现）
        if cap.has_pump:
            frame.pump = (0.3 + 0.6 * melody_pitch) * energy * cap.pump_expression_weight

        # 阀门：跟随开合
        if cap.has_valve:
            frame.valve = open_amount * cap.valve_expression_weight

        # 灯光：和弦颜色（开合设备的灯光承担和声表现）
        if cap.has_dimmer or cap.has_rgb:
            self._apply_light_harmony(frame, cap, chord_hue, chord_brightness,
                                       energy * (0.5 + 0.5 * open_amount), beat_phase)

    def _compute_fountain_note(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_contour: float,
        chord_hue: float, chord_brightness: float,
        energy: float, wave_value: float,
    ) -> None:
        """
        直流跑泉水柱的旋律音符编舞。

        wave_value 已由 TopologyAwareWaveGenerator.fountain_melody() 计算，
        包含了旋律音高→水柱高度的映射。
        这里直接使用 wave_value 作为泵高度。
        """
        # 泵高度 = 波形值（已包含旋律映射）
        if cap.has_pump:
            frame.pump = wave_value * energy * cap.pump_expression_weight

        # 阀门：控制水流开关（旋律有音符时开，无音符时关）
        if cap.has_valve:
            frame.valve = 1.0 if wave_value > 0.25 else 0.0

        # 灯光：和弦颜色 + 水柱高度调制亮度
        if cap.has_dimmer or cap.has_rgb:
            brightness = chord_brightness * (0.3 + 0.7 * wave_value)
            self._apply_light_harmony(frame, cap, chord_hue, brightness, energy, 0.0)

    def _compute_matrix_pattern(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float, wave_value: float,
    ) -> None:
        """矩阵单元的图案编舞"""
        # 泵/阀门：波形图案
        if cap.has_pump:
            frame.pump = wave_value * energy
        if cap.has_valve:
            frame.valve = 1.0 if wave_value > 0.3 else 0.0

        # 灯光：和弦颜色 + 波形调制
        if cap.has_dimmer or cap.has_rgb:
            brightness = chord_brightness * wave_value
            self._apply_light_harmony(frame, cap, chord_hue, brightness, energy, beat_phase)

    def _compute_pump_melody(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_velocity: float,
        chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float,
    ) -> None:
        """仅泵设备的旋律编舞"""
        if cap.has_pump:
            # 旋律音高→水柱高度，力度→流量
            frame.pump = (0.2 + 0.7 * melody_pitch) * melody_velocity * energy
        if cap.has_valve:
            frame.valve = 0.4 + 0.5 * energy
        if cap.has_dimmer or cap.has_rgb:
            self._apply_light_harmony(frame, cap, chord_hue, chord_brightness, energy, beat_phase)

    def _compute_light_harmony(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float, melody_pitch: float,
    ) -> None:
        """仅灯光设备的和声编舞"""
        # 灯光承担全部表现力：颜色=和弦调性，亮度=能量+节拍
        beat_pulse = 0.7 + 0.3 * math.sin(2 * math.pi * beat_phase)
        brightness = chord_brightness * energy * beat_pulse
        # 音高微调色相
        adjusted_hue = (chord_hue + melody_pitch * 0.1) % 1.0
        self._apply_light_harmony(frame, cap, adjusted_hue, brightness, energy, beat_phase)

    def _compute_pump_light_full(
        self, frame: ChoreographyFrame, cap: DeviceCapabilityProfile,
        melody_pitch: float, melody_contour: float, melody_velocity: float,
        chord_tension: float, chord_hue: float, chord_brightness: float,
        energy: float, beat_phase: float, wave_value: float,
    ) -> None:
        """
        无电机设备的泵+灯全表现编舞。

        泵承担旋律高度，灯承担和声颜色。
        两者共同完成电机设备本应承担的全部表现力。
        """
        # 泵：旋律音高→水柱高度（主要旋律表达）
        if cap.has_pump:
            # 旋律音高 + 走向 + 波形叠加
            pitch_height = 0.2 + 0.6 * melody_pitch
            contour_mod = melody_contour * 0.15  # 走向微调
            frame.pump = max(0.0, min(1.0,
                (pitch_height + contour_mod) * melody_velocity * energy
                * cap.pump_expression_weight
            ))

        # 阀门：节拍触发（节奏表达）
        if cap.has_valve:
            beat_gate = 0.5 + 0.4 * math.sin(2 * math.pi * beat_phase)
            tension_mod = 0.2 * chord_tension
            frame.valve = max(0.0, min(1.0,
                (beat_gate + tension_mod) * cap.valve_expression_weight
            ))

        # 灯光：和弦调性颜色（和声表达）
        if cap.has_dimmer or cap.has_rgb:
            # 亮度跟随能量和节拍
            beat_brightness = chord_brightness * (0.6 + 0.4 * energy)
            self._apply_light_harmony(frame, cap, chord_hue, beat_brightness,
                                       energy, beat_phase)

    # ─────────────────────────────────────────────────────────────────────
    #  灯光颜色计算（共用）
    # ─────────────────────────────────────────────────────────────────────

    def _apply_light_harmony(
        self,
        frame: ChoreographyFrame,
        cap: DeviceCapabilityProfile,
        hue: float,          # 色相 [0,1]
        brightness: float,   # 亮度 [0,1]
        energy: float,
        beat_phase: float,
    ) -> None:
        """
        将和弦调性色彩应用到灯光通道。

        hue 来自 MelodyAnalyzer 的 chord_hue（基于五度圈色彩系统）。
        """
        w = cap.light_expression_weight
        brightness = max(0.0, min(1.0, brightness * w))

        if cap.has_rgb:
            r, g, b = self._hue_to_rgb(hue)
            frame.red   = r * brightness
            frame.green = g * brightness
            frame.blue  = b * brightness

        if cap.has_dimmer:
            frame.dimmer = brightness

        if cap.has_white:
            # 白色通道：能量高时补白（增加亮度）
            frame.white = max(0.0, (energy - 0.7) * 2.0) * brightness

    @staticmethod
    def _hue_to_rgb(hue: float) -> Tuple[float, float, float]:
        """将色相值 [0,1] 转换为 RGB（基于 HSV，饱和度=1，明度=1）"""
        h = hue * 6.0
        i = int(h)
        f = h - i
        q = 1.0 - f
        if i % 6 == 0:
            return 1.0, f, 0.0
        elif i % 6 == 1:
            return q, 1.0, 0.0
        elif i % 6 == 2:
            return 0.0, 1.0, f
        elif i % 6 == 3:
            return 0.0, q, 1.0
        elif i % 6 == 4:
            return f, 0.0, 1.0
        else:
            return 1.0, 0.0, q


# ═══════════════════════════════════════════════════════════════════════════
#  5. 设备能力摘要（用于调试和 UI 显示）
# ═══════════════════════════════════════════════════════════════════════════

def summarize_capabilities(profiles: Dict[str, DeviceCapabilityProfile]) -> str:
    """生成设备能力摘要报告（用于调试）"""
    lines = ["=== DeepAqua 设备能力识别报告 ==="]
    cap_counts: Dict[MotorCapability, int] = {}
    mode_counts: Dict[ChoreographyMode, int] = {}

    for pid, p in profiles.items():
        cap_counts[p.motor_capability] = cap_counts.get(p.motor_capability, 0) + 1
        mode_counts[p.choreography_mode] = mode_counts.get(p.choreography_mode, 0) + 1

    lines.append(f"\n共 {len(profiles)} 台设备：")
    lines.append("\n运动能力分布：")
    for cap, count in sorted(cap_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {cap.value:20s} × {count}")

    lines.append("\n编舞模式分布：")
    for mode, count in sorted(mode_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {mode.value:20s} × {count}")

    lines.append("\n设备详情：")
    for pid, p in list(profiles.items())[:20]:  # 最多显示20台
        axes_str = f"X{'Y' if p.has_motor_y else ''}{'Z' if p.has_motor_z else ''}" if p.motor_axes > 0 else "-"
        channels_str = "+".join(filter(None, [
            f"M{axes_str}" if p.motor_axes > 0 else None,
            "P" if p.has_pump else None,
            "V" if p.has_valve else None,
            "L" if (p.has_dimmer or p.has_rgb) else None,
        ]))
        lines.append(
            f"  [{p.name[:16]:16s}] {p.motor_capability.value:15s} "
            f"→ {p.choreography_mode.value:20s} [{channels_str}]"
        )

    return "\n".join(lines)
