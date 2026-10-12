# -*- coding: utf-8 -*-
"""
DeviceTypeRegistry — 喷泉设备类型注册表

提供预定义设备类型模板（来自 Huang Gong 参考 + To The Moon 基准测试），
以及设备类型的注册/查询/分类功能。

每个 FountainDeviceType 包含：
  - 通道数量与布局
  - 友好名称映射
  - 安全范围（逐通道最小/最大值、每帧最大变化率）
  - 物理行为描述
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, ClassVar, Optional


@dataclass
class FountainDeviceType:
    """喷泉设备类型定义"""

    # 唯一标识键，如 "hjc_3d"
    id: str = ""
    # 中文显示名称，如 "HJC 三维摇摆喷泉"
    name: str = ""
    # 设备类别：motor / light / pump / valve / composite
    category: str = ""
    # 物理行为描述
    description: str = ""
    # 默认通道布局（ChannelType 枚举值列表）
    channels: List[str] = field(default_factory=list)
    # 每通道友好名称（key=通道类型值 | value=显示名）
    channel_names: Dict[str, str] = field(default_factory=dict)
    # 每通道安全默认值
    channel_defaults: Dict[str, int] = field(default_factory=dict)
    # 每通道最小值
    safe_min: Dict[str, int] = field(default_factory=dict)
    # 每通道最大值
    safe_max: Dict[str, int] = field(default_factory=dict)
    # 每通道每帧最大变化量（0-255）
    max_change_rate: Dict[str, int] = field(default_factory=dict)


class DeviceTypeRegistry:
    """设备类型注册表（单例风格，全部类方法）"""

    _types: ClassVar[Dict[str, FountainDeviceType]] = {}

    @classmethod
    def register(cls, device_type: FountainDeviceType) -> FountainDeviceType:
        """注册一个设备类型。若 id 重复则覆盖。"""
        cls._types[device_type.id] = device_type
        return device_type

    @classmethod
    def get(cls, type_id: str) -> Optional[FountainDeviceType]:
        """按 id 查询设备类型，不存在时返回 None。"""
        return cls._types.get(type_id)

    @classmethod
    def list_all(cls) -> List[FountainDeviceType]:
        """列出所有已注册的设备类型。"""
        return list(cls._types.values())

    @classmethod
    def list_by_category(cls, category: str) -> List[FountainDeviceType]:
        """按类别列出设备类型。"""
        return [t for t in cls._types.values() if t.category == category]


# ════════════════════════════════════════════════════════════════════════
#  预注册常用喷泉设备类型
# ════════════════════════════════════════════════════════════════════════

DeviceTypeRegistry.register(FountainDeviceType(
    id="hjc_3d",
    name="HJC 三维摇摆喷泉",
    category="composite",
    description="HJC 三维摇摆喷泉，支持 X/Y 轴水平+垂直摆动、"
               "水泵调压、RGB 全彩灯光及两路功能通道",
    channels=["motor_x", "motor_y", "pump", "R", "G", "B",
              "function_1", "function_2"],
    channel_names={
        "motor_x": "水平摆动", "motor_y": "垂直摆动", "pump": "水泵",
        "R": "红色", "G": "绿色", "B": "蓝色",
        "function_1": "功能1", "function_2": "功能2",
    },
    channel_defaults={
        "motor_x": 127, "motor_y": 127, "pump": 0,
        "R": 255, "G": 255, "B": 255,
        "function_1": 0, "function_2": 0,
    },
    safe_min={"motor_x": 0, "motor_y": 0, "pump": 0,
              "R": 0, "G": 0, "B": 0,
              "function_1": 0, "function_2": 0},
    safe_max={"motor_x": 255, "motor_y": 255, "pump": 255,
              "R": 255, "G": 255, "B": 255,
              "function_1": 255, "function_2": 255},
    max_change_rate={
        "motor_x": 8, "motor_y": 8, "pump": 5,
        "R": 255, "G": 255, "B": 255,
        "function_1": 255, "function_2": 255,
    },
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="led_light",
    name="LED 灯组",
    category="light",
    description="标准 RGB 全彩 LED 灯组，3 通道",
    channels=["R", "G", "B"],
    channel_names={"R": "红色", "G": "绿色", "B": "蓝色"},
    channel_defaults={"R": 255, "G": 255, "B": 255},
    safe_min={"R": 0, "G": 0, "B": 0},
    safe_max={"R": 255, "G": 255, "B": 255},
    max_change_rate={"R": 255, "G": 255, "B": 255},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="led_light_4ch",
    name="LED 灯组 (RGBW)",
    category="light",
    description="RGBW 全彩 LED 灯组，4 通道，含独立白色温通道",
    channels=["R", "G", "B", "W"],
    channel_names={"R": "红色", "G": "绿色", "B": "蓝色", "W": "白色"},
    channel_defaults={"R": 255, "G": 255, "B": 255, "W": 255},
    safe_min={"R": 0, "G": 0, "B": 0, "W": 0},
    safe_max={"R": 255, "G": 255, "B": 255, "W": 255},
    max_change_rate={"R": 255, "G": 255, "B": 255, "W": 255},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="airburst_valve",
    name="气爆/阀门组",
    category="composite",
    description="气爆装置，含阀门控制+调压泵+4组RGBW全彩灯光（共18通道）",
    channels=[
        "valve", "pump",
        "R", "G", "B", "W",
        "R", "G", "B", "W",
        "R", "G", "B", "W",
        "R", "G", "B", "W",
    ],
    channel_names={
        "valve": "气爆阀", "pump": "调压泵",
        "R": "红色", "G": "绿色", "B": "蓝色", "W": "白色",
    },
    channel_defaults={
        "valve": 0, "pump": 0,
        "R": 255, "G": 255, "B": 255, "W": 255,
    },
    safe_min={
        "valve": 0, "pump": 0,
        "R": 0, "G": 0, "B": 0, "W": 0,
    },
    safe_max={
        "valve": 255, "pump": 255,
        "R": 255, "G": 255, "B": 255, "W": 255,
    },
    max_change_rate={
        "valve": 255, "pump": 5,
        "R": 255, "G": 255, "B": 255, "W": 255,
    },
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="high_pressure_pump",
    name="高喷",
    category="pump",
    description="高压水泵，用于高喷水柱，单通道调压",
    channels=["pump"],
    channel_names={"pump": "水泵"},
    channel_defaults={"pump": 0},
    safe_min={"pump": 0},
    safe_max={"pump": 255},
    max_change_rate={"pump": 5},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="1d_swing",
    name="一维摇摆喷泉",
    category="motor",
    description="单轴摇摆喷泉，水平摆动+水泵调压",
    channels=["motor_x", "pump"],
    channel_names={"motor_x": "水平摆动", "pump": "水泵"},
    channel_defaults={"motor_x": 127, "pump": 0},
    safe_min={"motor_x": 0, "pump": 0},
    safe_max={"motor_x": 255, "pump": 255},
    max_change_rate={"motor_x": 8, "pump": 5},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="2d_swing",
    name="二维摇摆喷泉",
    category="motor",
    description="双轴摇摆喷泉，水平+垂直摆动+水泵调压",
    channels=["motor_x", "motor_y", "pump"],
    channel_names={
        "motor_x": "水平摆动", "motor_y": "垂直摆动", "pump": "水泵",
    },
    channel_defaults={"motor_x": 127, "motor_y": 127, "pump": 0},
    safe_min={"motor_x": 0, "motor_y": 0, "pump": 0},
    safe_max={"motor_x": 255, "motor_y": 255, "pump": 255},
    max_change_rate={"motor_x": 8, "motor_y": 8, "pump": 5},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="running_light",
    name="跑泉灯组",
    category="light",
    description="跑泉追逐灯组，4组RGB逐次点亮形成流水效果（12通道）",
    channels=[
        "R", "G", "B",
        "R", "G", "B",
        "R", "G", "B",
        "R", "G", "B",
    ],
    channel_names={"R": "红色", "G": "绿色", "B": "蓝色"},
    channel_defaults={"R": 255, "G": 255, "B": 255},
    safe_min={"R": 0, "G": 0, "B": 0},
    safe_max={"R": 255, "G": 255, "B": 255},
    max_change_rate={"R": 255, "G": 255, "B": 255},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="variable_pump",
    name="变频水泵",
    category="pump",
    description="变频调速水泵，支持线性调压及预充水控制，单通道调压",
    channels=["pump"],
    channel_names={"pump": "水泵"},
    channel_defaults={"pump": 0},
    safe_min={"pump": 0},
    safe_max={"pump": 255},
    max_change_rate={"pump": 3},
))

DeviceTypeRegistry.register(FountainDeviceType(
    id="cedar_changer",
    name="雪松/百变喷泉",
    category="composite",
    description="雪松造型喷泉，双轴电机旋转+水泵调压+阀门控制",
    channels=["motor_x", "motor_y", "pump", "valve"],
    channel_names={
        "motor_x": "水平旋转", "motor_y": "垂直升降",
        "pump": "水泵", "valve": "阀门",
    },
    channel_defaults={
        "motor_x": 127, "motor_y": 127, "pump": 0, "valve": 0,
    },
    safe_min={"motor_x": 0, "motor_y": 0, "pump": 0, "valve": 0},
    safe_max={"motor_x": 255, "motor_y": 255, "pump": 255, "valve": 255},
    max_change_rate={
        "motor_x": 8, "motor_y": 8, "pump": 5, "valve": 255,
    },
))
