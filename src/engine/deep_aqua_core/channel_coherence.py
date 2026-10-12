"""
channel_coherence.py
──────────────────────────────────────────────────────────────────────────────
DeepAqua 通道协同系统

核心职责：
  确保 AI 编舞时所有通道类型（电机/泵/灯光/阀门）在空间和时序上协同一致。

三大子系统：

1. ChannelCoherenceManager（通道协同管理器）
   - 基本原则：开了哪几台电机，对应位置的泵/灯/阀门也跟着激活
   - 支持设备元聚合后的通道完整性校验
   - 拼接/拆分/变形时自动同步所有通道

2. PhaseOffsetLayer（相位错位层）
   - 高级编舞：允许不同通道类型之间产生时序错位
   - 例如：泵比电机晚半拍，灯比泵超前四分之一拍
   - 错位量可由音频参数动态调制

3. TopologyScaler（波形拓扑弹性映射）
   - 设备数量 ≠ 波形节点数
   - 5台设备可映射到7节点波形（拉伸），或7台设备表达5节点波形（压缩）
   - 支持：线性插值、三次样条、余弦插值、步进映射
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Set

# ═══════════════════════════════════════════════════════════════════════════
#  1. 通道类型定义
# ═══════════════════════════════════════════════════════════════════════════

class ChannelType(Enum):
    """物理通道类型"""
    MOTOR   = "motor"   # 电机（摆动/仰角）
    PUMP    = "pump"    # 水泵（水柱高度）
    LIGHT   = "light"   # 灯光（颜色/强度）
    VALVE   = "valve"   # 阀门（开合/切分）

# 通道类型的默认激活优先级（越低越优先）
CHANNEL_PRIORITY: Dict[ChannelType, int] = {
    ChannelType.MOTOR: 1,   # 电机是主导通道
    ChannelType.PUMP:  2,   # 泵跟随电机
    ChannelType.LIGHT: 3,   # 灯光跟随泵
    ChannelType.VALVE: 4,   # 阀门最后
}

# 通道类型的默认相位偏移（相对于电机，单位：拍的分数）
DEFAULT_PHASE_OFFSETS: Dict[ChannelType, float] = {
    ChannelType.MOTOR: 0.0,    # 电机：基准
    ChannelType.PUMP:  0.0,    # 泵：与电机同步（基本原则）
    ChannelType.LIGHT: 0.0,    # 灯光：与电机同步（基本原则）
    ChannelType.VALVE: 0.0,    # 阀门：与电机同步（基本原则）
}

# ═══════════════════════════════════════════════════════════════════════════
#  2. 设备通道映射
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceChannelMap:
    """
    单台设备的通道映射。

    每台物理设备可能包含多种通道类型（电机+泵+灯+阀门），
    也可能只有其中几种。
    """
    device_id: str
    position: float = 0.0          # 设备在空间中的归一化位置 [0,1]
    channels: Dict[ChannelType, int] = field(default_factory=dict)
    # {ChannelType: DMX 起始地址}
    is_active: bool = True

    def has_channel(self, ch_type: ChannelType) -> bool:
        return ch_type in self.channels and self.is_active

@dataclass
class CoherentGroup:
    """
    协同设备组：一组在空间上对应的设备。

    基本原则：组内所有设备的所有通道类型必须协同激活。
    高级设计：允许通道间的相位错位（PhaseOffsetLayer 控制）。
    """
    group_id: str
    devices: List[DeviceChannelMap] = field(default_factory=list)
    # 当前激活的通道类型集合
    active_channels: Set[ChannelType] = field(default_factory=lambda: {
        ChannelType.MOTOR, ChannelType.PUMP,
        ChannelType.LIGHT, ChannelType.VALVE
    })
    # 通道间相位偏移（相对于 MOTOR 通道，单位：拍）
    phase_offsets: Dict[ChannelType, float] = field(default_factory=lambda: dict(DEFAULT_PHASE_OFFSETS))

    def get_positions(self) -> List[float]:
        """获取组内所有设备的空间位置"""
        return [d.position for d in self.devices]

    def get_device_count(self) -> int:
        return len(self.devices)

# ═══════════════════════════════════════════════════════════════════════════
#  3. ChannelCoherenceManager（通道协同管理器）
# ═══════════════════════════════════════════════════════════════════════════

class ChannelCoherenceManager:
    """
    通道协同管理器。

    核心职责：
    1. 确保设备元聚合时，所有通道类型在空间上对应
    2. 拼接/拆分/变形时自动同步所有通道
    3. 校验通道完整性，缺失通道时发出警告并降级处理

    基本原则（不可违反）：
    - 开了哪几台电机，对应位置的泵/灯/阀门也必须激活
    - 改变编组（合并/拆分）时，所有通道同步变化

    高级设计（可选）：
    - 通道间相位错位（由 PhaseOffsetLayer 控制）
    - 不同通道类型的激活率可以不同（由 ExpressionMatrixDriver 控制）
    """

    def __init__(self):
        self._groups: Dict[str, CoherentGroup] = {}
        self._device_registry: Dict[str, DeviceChannelMap] = {}

    def register_device(
        self,
        device_id: str,
        position: float,
        channels: Dict[ChannelType, int],
    ) -> None:
        """注册设备及其通道映射"""
        self._device_registry[device_id] = DeviceChannelMap(
            device_id=device_id,
            position=position,
            channels=channels,
        )

    def create_group(
        self,
        group_id: str,
        device_ids: List[str],
        phase_offsets: Optional[Dict[ChannelType, float]] = None,
    ) -> CoherentGroup:
        """
        创建协同设备组。

        所有指定设备的所有通道类型将被协同激活。
        缺失某种通道类型的设备会被标记，但不影响组的创建。

        Args:
            group_id:      组 ID
            device_ids:    设备 ID 列表
            phase_offsets: 通道间相位偏移（可选，默认全部同步）

        Returns:
            CoherentGroup
        """
        devices = []
        for did in device_ids:
            if did in self._device_registry:
                devices.append(self._device_registry[did])

        offsets = dict(DEFAULT_PHASE_OFFSETS)
        if phase_offsets:
            offsets.update(phase_offsets)

        group = CoherentGroup(
            group_id=group_id,
            devices=devices,
            phase_offsets=offsets,
        )
        self._groups[group_id] = group
        return group

    def merge_groups(
        self,
        new_group_id: str,
        group_ids: List[str],
        phase_offsets: Optional[Dict[ChannelType, float]] = None,
    ) -> CoherentGroup:
        """
        合并多个组为一个更大的组。

        合并后，所有原组的设备通道全部协同激活。
        这对应设计规格书中的"一组 4x4 交叉"场景。
        """
        all_devices = []
        for gid in group_ids:
            if gid in self._groups:
                all_devices.extend(self._groups[gid].devices)
                # 合并后删除原组
                del self._groups[gid]

        offsets = dict(DEFAULT_PHASE_OFFSETS)
        if phase_offsets:
            offsets.update(phase_offsets)

        new_group = CoherentGroup(
            group_id=new_group_id,
            devices=all_devices,
            phase_offsets=offsets,
        )
        self._groups[new_group_id] = new_group
        return new_group

    def split_group(
        self,
        group_id: str,
        split_indices: List[List[int]],
        new_group_ids: Optional[List[str]] = None,
    ) -> List[CoherentGroup]:
        """
        将一个组拆分为多个子组。

        拆分后，每个子组内的通道仍然协同激活。
        这对应"两组 2x2 交叉"场景。

        Args:
            group_id:      原组 ID
            split_indices: 每个子组包含的设备索引列表
                           例如 [[0,1], [2,3]] 表示拆分为两组
            new_group_ids: 新组 ID 列表（可选，默认自动生成）

        Returns:
            新创建的子组列表
        """
        if group_id not in self._groups:
            return []

        original = self._groups[group_id]
        devices = original.devices
        new_groups = []

        for i, indices in enumerate(split_indices):
            gid = (new_group_ids[i] if new_group_ids and i < len(new_group_ids)
                   else f"{group_id}_split_{i}")
            sub_devices = [devices[j] for j in indices if j < len(devices)]
            new_group = CoherentGroup(
                group_id=gid,
                devices=sub_devices,
                phase_offsets=dict(original.phase_offsets),
            )
            self._groups[gid] = new_group
            new_groups.append(new_group)

        # 删除原组
        del self._groups[group_id]
        return new_groups

    def validate_coherence(self, group_id: str) -> Dict[str, List[str]]:
        """
        校验组内通道完整性。

        Returns:
            {
                "missing": [设备ID列表（缺少某种通道类型）],
                "warnings": [警告信息列表],
                "ok": True/False
            }
        """
        if group_id not in self._groups:
            return {"missing": [], "warnings": [f"Group {group_id} not found"], "ok": False}

        group = self._groups[group_id]
        missing = []
        warnings = []

        for device in group.devices:
            missing_types = []
            for ch_type in group.active_channels:
                if not device.has_channel(ch_type):
                    missing_types.append(ch_type.value)
            if missing_types:
                missing.append(device.device_id)
                warnings.append(
                    f"Device {device.device_id} missing channels: {missing_types}"
                )

        return {
            "missing": missing,
            "warnings": warnings,
            "ok": len(missing) == 0,
        }

    def get_group(self, group_id: str) -> Optional[CoherentGroup]:
        return self._groups.get(group_id)

    def get_all_groups(self) -> Dict[str, CoherentGroup]:
        return dict(self._groups)

    def apply_coherent_values(
        self,
        group_id: str,
        motor_values: Dict[str, float],
        pump_scale: float = 1.0,
        light_scale: float = 1.0,
        valve_scale: float = 1.0,
    ) -> Dict[str, Dict[ChannelType, float]]:
        """
        将电机值同步到组内所有设备的所有通道。

        基本原则：电机值决定基础，其他通道按比例跟随。
        高级设计：各通道的 scale 可以不同（由 ExpressionMatrixDriver 决定）。

        Args:
            group_id:     组 ID
            motor_values: {device_id: motor_value} 电机值字典
            pump_scale:   泵相对于电机的响应比例
            light_scale:  灯光相对于电机的响应比例
            valve_scale:  阀门相对于电机的响应比例

        Returns:
            {device_id: {ChannelType: value}} 各设备各通道的输出值
        """
        group = self._groups.get(group_id)
        if not group:
            return {}

        result = {}
        for device in group.devices:
            did = device.device_id
            motor_val = motor_values.get(did, 0.5)
            result[did] = {
                ChannelType.MOTOR: motor_val,
                ChannelType.PUMP:  motor_val * pump_scale,
                ChannelType.LIGHT: motor_val * light_scale,
                ChannelType.VALVE: motor_val * valve_scale,
            }
        return result


# ═══════════════════════════════════════════════════════════════════════════
#  4. PhaseOffsetLayer（相位错位层）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PhaseOffsetPreset:
    """相位错位预设"""
    name: str
    offsets: Dict[ChannelType, float]  # {通道类型: 相位偏移（拍）}
    description: str = ""

# 内置相位错位预设
PHASE_OFFSET_PRESETS: Dict[str, PhaseOffsetPreset] = {
    # 完全同步（基本原则，默认）
    "sync": PhaseOffsetPreset(
        name="sync",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.0,
            ChannelType.LIGHT: 0.0,
            ChannelType.VALVE: 0.0,
        },
        description="所有通道完全同步（基本原则）",
    ),
    # 泵滞后半拍（水柱跟随电机，有物理延迟感）
    "pump_lag": PhaseOffsetPreset(
        name="pump_lag",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.5,    # 泵滞后半拍
            ChannelType.LIGHT: 0.0,
            ChannelType.VALVE: 0.0,
        },
        description="泵滞后半拍，模拟水柱物理延迟",
    ),
    # 灯光超前（灯光预示动作，视觉引导）
    "light_lead": PhaseOffsetPreset(
        name="light_lead",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.0,
            ChannelType.LIGHT: -0.25,  # 灯光超前四分之一拍
            ChannelType.VALVE: 0.0,
        },
        description="灯光超前四分之一拍，视觉引导动作",
    ),
    # 瀑布错位（各通道依次延迟，形成流动感）
    "cascade": PhaseOffsetPreset(
        name="cascade",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.25,   # 泵延迟四分之一拍
            ChannelType.LIGHT: 0.5,    # 灯光延迟半拍
            ChannelType.VALVE: 0.75,   # 阀门延迟四分之三拍
        },
        description="瀑布错位：各通道依次延迟，形成流动感",
    ),
    # 对位（泵与电机反相，形成对话感）
    "counterpoint": PhaseOffsetPreset(
        name="counterpoint",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  1.0,    # 泵反相（延迟一拍，形成对位）
            ChannelType.LIGHT: 0.5,    # 灯光在中间
            ChannelType.VALVE: 0.0,
        },
        description="对位：泵与电机形成一拍的对话",
    ),
    # 强拍同步（所有通道在强拍对齐，弱拍各自发展）
    "downbeat_sync": PhaseOffsetPreset(
        name="downbeat_sync",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.0,
            ChannelType.LIGHT: 0.0,
            ChannelType.VALVE: 0.125,  # 阀门轻微错位，切分感
        },
        description="强拍同步，阀门轻微切分",
    ),
    # Solo 模式（电机完全主导，其他通道跟随）
    "solo_lead": PhaseOffsetPreset(
        name="solo_lead",
        offsets={
            ChannelType.MOTOR: 0.0,
            ChannelType.PUMP:  0.125,  # 泵轻微滞后
            ChannelType.LIGHT: -0.125, # 灯光轻微超前
            ChannelType.VALVE: 0.25,   # 阀门滞后四分之一拍
        },
        description="Solo 模式：电机主导，其他通道围绕旋律",
    ),
}

class PhaseOffsetLayer:
    """
    相位错位层。

    高级编舞功能：允许不同通道类型之间产生时序错位，
    制造层次感和流动感。

    工作原理：
    - 每个通道类型有一个相位偏移量（单位：拍的分数）
    - 正值=延迟，负值=超前
    - 偏移量可由音频参数动态调制
    - 可以从预设切换，也可以实时设置

    注意：这是高级设计功能，基本原则（通道协同）仍然成立，
    只是在时序上产生错位，而不是空间上的不对应。
    """

    def __init__(self, preset: str = "sync", bpm: float = 120.0):
        self._bpm = bpm
        self._beat_duration = 60.0 / bpm
        self._current_offsets: Dict[ChannelType, float] = dict(DEFAULT_PHASE_OFFSETS)
        self._target_offsets: Dict[ChannelType, float] = dict(DEFAULT_PHASE_OFFSETS)
        self._blend_speed: float = 2.0  # 预设切换平滑速度（拍/秒）
        self.load_preset(preset)

    def update_bpm(self, bpm: float) -> None:
        self._bpm = bpm
        self._beat_duration = 60.0 / bpm

    def load_preset(self, preset_name: str) -> None:
        """加载相位错位预设"""
        preset = PHASE_OFFSET_PRESETS.get(preset_name, PHASE_OFFSET_PRESETS["sync"])
        self._target_offsets = dict(preset.offsets)

    def set_offset(self, ch_type: ChannelType, offset_beats: float) -> None:
        """实时设置单个通道的相位偏移（单位：拍）"""
        self._target_offsets[ch_type] = offset_beats

    def tick(self, dt: float) -> None:
        """更新相位偏移（平滑过渡到目标值）"""
        for ch_type in ChannelType:
            current = self._current_offsets.get(ch_type, 0.0)
            target = self._target_offsets.get(ch_type, 0.0)
            diff = target - current
            step = self._blend_speed * dt
            if abs(diff) <= step:
                self._current_offsets[ch_type] = target
            else:
                self._current_offsets[ch_type] = current + math.copysign(step, diff)

    def get_offset_seconds(self, ch_type: ChannelType) -> float:
        """获取通道的相位偏移（秒）"""
        beats = self._current_offsets.get(ch_type, 0.0)
        return beats * self._beat_duration

    def get_delayed_value(
        self,
        ch_type: ChannelType,
        value_timeline: List[Tuple[float, float]],
        current_time: float,
    ) -> float:
        """
        从时间线中获取考虑相位偏移后的值。

        Args:
            ch_type:        通道类型
            value_timeline: [(时间戳, 值)] 时间线列表（按时间排序）
            current_time:   当前时间（秒）

        Returns:
            考虑相位偏移后的插值结果
        """
        offset_sec = self.get_offset_seconds(ch_type)
        lookup_time = current_time - offset_sec  # 正偏移=查找更早的值（延迟）

        if not value_timeline:
            return 0.5

        # 二分查找最近的时间点
        if lookup_time <= value_timeline[0][0]:
            return value_timeline[0][1]
        if lookup_time >= value_timeline[-1][0]:
            return value_timeline[-1][1]

        for i in range(len(value_timeline) - 1):
            t0, v0 = value_timeline[i]
            t1, v1 = value_timeline[i + 1]
            if t0 <= lookup_time <= t1:
                alpha = (lookup_time - t0) / max(t1 - t0, 0.0001)
                return v0 + (v1 - v0) * alpha

        return value_timeline[-1][1]

    def modulate_by_audio(
        self,
        energy: float,
        tension: float,
        solo_intensity: float,
        base_preset: str = "sync",
    ) -> None:
        """
        根据音频参数动态调制相位偏移。

        规则：
        - 高能量 → 偏移减小（趋向同步，冲击感强）
        - 高张力 → 偏移增大（趋向瀑布，层次感强）
        - Solo 模式 → 切换到 solo_lead 预设
        """
        if solo_intensity > 0.6:
            self.load_preset("solo_lead")
            return

        if tension > 0.7:
            # 高张力：瀑布错位
            target_preset = "cascade"
        elif energy > 0.8:
            # 高能量：完全同步
            target_preset = "sync"
        elif tension > 0.4:
            # 中等张力：灯光超前
            target_preset = "light_lead"
        else:
            target_preset = base_preset

        self.load_preset(target_preset)


# ═══════════════════════════════════════════════════════════════════════════
#  5. TopologyScaler（波形拓扑弹性映射）
# ═══════════════════════════════════════════════════════════════════════════

class InterpolationMode(Enum):
    """插值模式"""
    LINEAR  = "linear"   # 线性插值
    COSINE  = "cosine"   # 余弦插值（平滑）
    CUBIC   = "cubic"    # 三次样条（最平滑）
    STEP    = "step"     # 步进（无插值）
    NEAREST = "nearest"  # 最近邻

class TopologyScaler:
    """
    波形拓扑弹性映射。

    核心功能：
    设备数量 ≠ 波形节点数，通过弹性映射实现任意数量的设备
    都能完整表达波形的所有形态特征。

    典型场景：
    - 5台设备表达7节点波峰：波形被压缩，每台设备承担约1.4个节点
    - 7台设备表达5节点波峰：波形被拉伸，相邻设备之间有插值过渡
    - 3台设备表达完整正弦波：3个采样点，通过插值重建完整波形

    支持的波形类型：
    - sine:      正弦波（平滑起伏）
    - triangle:  三角波（线性起伏）
    - sawtooth:  锯齿波（快升慢降）
    - pulse:     脉冲波（快速开关）
    - gaussian:  高斯波（中心高两端低，波峰形）
    - custom:    自定义节点序列
    """

    def __init__(self, interpolation: InterpolationMode = InterpolationMode.COSINE):
        self._interpolation = interpolation

    def set_interpolation(self, mode: InterpolationMode) -> None:
        self._interpolation = mode

    # ─────────────────────────────────────────────────────────────────────
    #  波形生成
    # ─────────────────────────────────────────────────────────────────────

    def generate_waveform(
        self,
        waveform_type: str,
        num_nodes: int,
        phase: float = 0.0,
        amplitude: float = 1.0,
        offset: float = 0.0,
    ) -> List[float]:
        """
        生成标准波形节点序列。

        Args:
            waveform_type: 波形类型（sine/triangle/sawtooth/pulse/gaussian）
            num_nodes:     节点数量
            phase:         相位偏移 [0,1]
            amplitude:     振幅 [0,1]
            offset:        直流偏置 [0,1]

        Returns:
            节点值列表（长度=num_nodes，值域 [0,1]）
        """
        nodes = []
        for i in range(num_nodes):
            t = (i / num_nodes + phase) % 1.0
            if waveform_type == "sine":
                v = 0.5 + 0.5 * math.sin(2 * math.pi * t)
            elif waveform_type == "triangle":
                v = 1.0 - abs(2 * t - 1.0)
            elif waveform_type == "sawtooth":
                v = t
            elif waveform_type == "reverse_sawtooth":
                v = 1.0 - t
            elif waveform_type == "pulse":
                v = 1.0 if t < 0.5 else 0.0
            elif waveform_type == "gaussian":
                # 高斯波峰（中心最高，两端最低）
                center = 0.5
                sigma = 0.2
                v = math.exp(-((t - center) ** 2) / (2 * sigma ** 2))
            elif waveform_type == "double_peak":
                # 双峰波（两个高点）
                v = max(
                    math.exp(-((t - 0.25) ** 2) / (2 * 0.1 ** 2)),
                    math.exp(-((t - 0.75) ** 2) / (2 * 0.1 ** 2)),
                )
            elif waveform_type == "valley":
                # 谷形波（中心最低，两端最高）
                v = 1.0 - math.exp(-((t - 0.5) ** 2) / (2 * 0.2 ** 2))
            else:
                v = 0.5  # 默认平稳
            nodes.append(max(0.0, min(1.0, v * amplitude + offset)))
        return nodes

    # ─────────────────────────────────────────────────────────────────────
    #  弹性映射：波形节点 → 设备值
    # ─────────────────────────────────────────────────────────────────────

    def scale_to_devices(
        self,
        waveform_nodes: List[float],
        num_devices: int,
        device_positions: Optional[List[float]] = None,
    ) -> List[float]:
        """
        将波形节点序列弹性映射到实际设备数量。

        支持任意节点数 → 任意设备数的映射（拉伸/压缩）。

        Args:
            waveform_nodes:   波形节点值列表（任意长度）
            num_devices:      目标设备数量
            device_positions: 设备的空间位置列表 [0,1]（可选）
                              如果提供，则按实际位置采样；
                              否则均匀分布。

        Returns:
            设备值列表（长度=num_devices）
        """
        if not waveform_nodes:
            return [0.5] * num_devices
        if num_devices <= 0:
            return []
        if num_devices == 1:
            return [waveform_nodes[len(waveform_nodes) // 2]]

        n_nodes = len(waveform_nodes)

        # 确定采样位置
        if device_positions is not None and len(device_positions) == num_devices:
            # 按实际设备位置采样
            sample_positions = device_positions
        else:
            # 均匀分布
            sample_positions = [i / (num_devices - 1) for i in range(num_devices)]

        # 在波形节点序列上采样
        result = []
        for pos in sample_positions:
            # pos [0,1] → 节点索引
            idx_float = pos * (n_nodes - 1)
            result.append(self._interpolate(waveform_nodes, idx_float))

        return result

    def _interpolate(self, nodes: List[float], idx_float: float) -> float:
        """在节点序列上进行插值"""
        n = len(nodes)
        if n == 0:
            return 0.5
        if n == 1:
            return nodes[0]

        idx_float = max(0.0, min(n - 1, idx_float))
        i0 = int(idx_float)
        i1 = min(i0 + 1, n - 1)
        alpha = idx_float - i0

        if self._interpolation == InterpolationMode.STEP:
            return nodes[i0]
        elif self._interpolation == InterpolationMode.NEAREST:
            return nodes[round(idx_float)]
        elif self._interpolation == InterpolationMode.LINEAR:
            return nodes[i0] + (nodes[i1] - nodes[i0]) * alpha
        elif self._interpolation == InterpolationMode.COSINE:
            # 余弦插值（更平滑）
            alpha_cos = (1.0 - math.cos(alpha * math.pi)) / 2.0
            return nodes[i0] + (nodes[i1] - nodes[i0]) * alpha_cos
        elif self._interpolation == InterpolationMode.CUBIC:
            # 三次样条插值（最平滑）
            i_prev = max(0, i0 - 1)
            i_next = min(n - 1, i1 + 1)
            p0, p1, p2, p3 = nodes[i_prev], nodes[i0], nodes[i1], nodes[i_next]
            a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3
            b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3
            c = -0.5 * p0 + 0.5 * p2
            d = p1
            return a * alpha**3 + b * alpha**2 + c * alpha + d
        else:
            return nodes[i0] + (nodes[i1] - nodes[i0]) * alpha

    # ─────────────────────────────────────────────────────────────────────
    #  高级：多通道波形叠加
    # ─────────────────────────────────────────────────────────────────────

    def overlay_waveforms(
        self,
        waveforms: List[Tuple[List[float], float]],
        num_devices: int,
        device_positions: Optional[List[float]] = None,
    ) -> List[float]:
        """
        将多个波形叠加后映射到设备。

        用于高级编舞效果：多个波形同时作用于同一组设备，
        形成复杂的叠加形态。

        Args:
            waveforms:        [(节点序列, 权重)] 列表
            num_devices:      目标设备数量
            device_positions: 设备空间位置（可选）

        Returns:
            叠加后的设备值列表
        """
        if not waveforms:
            return [0.5] * num_devices

        total_weight = sum(w for _, w in waveforms)
        if total_weight <= 0:
            return [0.5] * num_devices

        result = [0.0] * num_devices
        for nodes, weight in waveforms:
            scaled = self.scale_to_devices(nodes, num_devices, device_positions)
            for i, v in enumerate(scaled):
                result[i] += v * weight / total_weight

        return [max(0.0, min(1.0, v)) for v in result]

    def compute_wave_topology(
        self,
        action_type: str,
        num_devices: int,
        device_positions: Optional[List[float]] = None,
        phase: float = 0.0,
        energy: float = 0.5,
        tension: float = 0.3,
    ) -> List[float]:
        """
        根据动作类型和音频参数计算设备波形拓扑。

        这是 AI 编舞的核心接口：给定动作类型（波峰/波谷/追逐等）
        和实际设备数量，自动计算每台设备的目标值。

        Args:
            action_type:      动作类型（wave_peak/wave_valley/chase/pulse/fountain等）
            num_devices:      实际设备数量
            device_positions: 设备空间位置（可选）
            phase:            当前相位 [0,1]
            energy:           音频能量（影响振幅）
            tension:          音频张力（影响波形复杂度）

        Returns:
            每台设备的目标值列表
        """
        amplitude = 0.5 + 0.5 * energy  # 能量决定振幅

        if action_type in ("wave_peak", "fountain", "peak"):
            # 波峰：中心高两端低（高斯波）
            nodes = self.generate_waveform("gaussian", max(num_devices, 7),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("wave_valley", "valley"):
            # 谷形：中心低两端高
            nodes = self.generate_waveform("valley", max(num_devices, 7),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("chase", "wave", "running"):
            # 追逐/行波：正弦波
            nodes = self.generate_waveform("sine", max(num_devices, 8),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("pulse", "beat", "strobe"):
            # 脉冲：方波
            nodes = self.generate_waveform("pulse", max(num_devices, 4),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("rise", "buildup"):
            # 渐升：锯齿波
            nodes = self.generate_waveform("sawtooth", max(num_devices, 6),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("fall", "decay"):
            # 渐降：反向锯齿
            nodes = self.generate_waveform("reverse_sawtooth", max(num_devices, 6),
                                           phase=phase, amplitude=amplitude)
        elif action_type in ("double_peak", "twin"):
            # 双峰
            nodes = self.generate_waveform("double_peak", max(num_devices, 8),
                                           phase=phase, amplitude=amplitude)
        else:
            # 默认：正弦波
            nodes = self.generate_waveform("sine", max(num_devices, 8),
                                           phase=phase, amplitude=amplitude)

        # 高张力时叠加谐波（增加复杂度）
        if tension > 0.6:
            harmonic_nodes = self.generate_waveform(
                "sine", max(num_devices, 8),
                phase=phase * 2, amplitude=amplitude * 0.3 * tension
            )
            return self.overlay_waveforms(
                [(nodes, 1.0), (harmonic_nodes, 0.3)],
                num_devices, device_positions
            )

        return self.scale_to_devices(nodes, num_devices, device_positions)
