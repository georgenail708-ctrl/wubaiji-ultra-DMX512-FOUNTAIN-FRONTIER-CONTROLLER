"""
unified_params.py — Unified 18-Slot Parameter System
=====================================================
Provides a single parameter vocabulary across all device channel types,
analogous to the Huang Gong 9 ASS-slots convention.

Each device channel type (dimmer, motor_x, pump, etc.) registers semantics
for the slots it understands. The UI then renders the same slot numbers
across device types, adapting label and range per type.

Architecture:
  UnifiedParamSlot  →  18 canonical slot names (P1-P9 + ASS1-ASS9)
  SlotSemantics     →  What a slot means for ONE channel type
  SlotRegistry      →  (channel_type, slot) → SlotSemantics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
#  18 Unified Parameter Slots
# ─────────────────────────────────────────────────────────────────────────────

class UnifiedParamSlot(Enum):
    """18 canonical slots shared across all channel types.

    P1-P9: General-purpose slots common to most device categories.
    ASS1-ASS9: Device-specific auxiliary slots (Huang Gong convention).
    """
    # ── General-purpose (P1-P9) ──────────────────────────────────────────
    P1_MODE          = "p1_mode"           # 模式选择
    P2_HEIGHT        = "p2_height"         # 高度/亮度/压力
    P3_TRIGGER       = "p3_trigger"        # 触发/音量
    P4_PAGE          = "p4_page"           # 页面/选页模式
    P5_DELAY         = "p5_delay"          # 延迟模式
    P6_DELAY_TIME    = "p6_delay_time"     # 延迟时长
    P7_PAGE_SPEED    = "p7_page_speed"     # 页速度
    P8_PAGE_MODE     = "p8_page_mode"      # 页模式
    P9_PRESET_FREQ   = "p9_preset_freq"    # 预置频率
    # ── Auxiliary (ASS1-ASS9) ────────────────────────────────────────────
    ASS1_ANGLE_CTRL    = "ass1_angle_ctrl"       # 角度控制
    ASS2_ANGLE_AUX     = "ass2_angle_aux"        # 角度辅助
    ASS3_TARGET_ANGLE  = "ass3_target_angle"     # 目标角度
    ASS4_MODE          = "ass4_mode"             # 辅助模式
    ASS5_CLICK         = "ass5_click"            # 点击/触发
    ASS6_TWEEN_SPEED   = "ass6_tween_speed"      # 补间速度
    ASS7_TWEEN_START   = "ass7_tween_start"      # 补间起始
    ASS8_WATER_OFF     = "ass8_water_off"        # 关水模式
    ASS9_WATER_OFF_ANGLE = "ass9_water_off_angle"  # 关水角度


# ─────────────────────────────────────────────────────────────────────────────
#  Slot Semantics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SlotSemantics:
    """Describes what a unified slot means for a specific device channel type.

    Fields mirror ParamSpec so the UI can build widgets from the same helpers.
    """
    slot: UnifiedParamSlot
    label_cn: str                     # Chinese label shown in UI
    description: str                  # Tooltip / help text
    param_type: str                   # "float" | "int" | "bool" | "choice"
    default: Any = 0
    min_val: float = 0.0
    max_val: float = 1.0
    choices: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
#  SlotRegistry — (channel_type, slot) → SlotSemantics
# ─────────────────────────────────────────────────────────────────────────────

def _norm_ch_type(raw: str) -> str:
    """Normalise a channel-type string to its canonical form."""
    value = str(raw).strip().lower()
    aliases = {
        "red": "r", "green": "g", "blue": "b", "white": "w",
        "intensity": "dimmer", "brightness": "dimmer", "light": "dimmer",
        "pan": "motor_x", "x": "motor_x", "x_axis": "motor_x",
        "tilt": "motor_y", "y": "motor_y", "y_axis": "motor_y",
        "z_axis": "motor_z",
    }
    return aliases.get(value, value)


class SlotRegistry:
    """Maps (channel_type, slot) -> SlotSemantics.

    Thread-safe for read operations on the DMX tick loop (the registry is
    populated once at import / startup and never mutated afterwards).
    """
    _registry: Dict[Tuple[str, UnifiedParamSlot], SlotSemantics] = {}

    # ── Registration ─────────────────────────────────────────────────────

    @classmethod
    def register(cls, ch_type: str, slot: UnifiedParamSlot,
                 semantics: SlotSemantics) -> None:
        """Register semantics for a (channel-type, slot) pair."""
        key = (_norm_ch_type(ch_type), slot)
        cls._registry[key] = semantics

    @classmethod
    def bulk_register(cls, ch_type: str,
                      entries: List[Tuple[UnifiedParamSlot, SlotSemantics]]) -> None:
        """Register multiple slots for the same channel type at once."""
        norm = _norm_ch_type(ch_type)
        for slot, sem in entries:
            cls._registry[(norm, slot)] = sem

    # ── Query ────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, ch_type: str, slot: UnifiedParamSlot) -> Optional[SlotSemantics]:
        return cls._registry.get((_norm_ch_type(ch_type), slot))

    @classmethod
    def list_for_channel(cls, ch_type: str) -> List[SlotSemantics]:
        """Return all registered semantics for *ch_type*, sorted by slot order."""
        norm = _norm_ch_type(ch_type)
        out: List[SlotSemantics] = []
        for (ct, sl), sem in cls._registry.items():
            if ct == norm:
                out.append(sem)
        slot_order = list(UnifiedParamSlot)
        out.sort(key=lambda s: slot_order.index(s.slot)
                 if s.slot in slot_order else 999)
        return out

    @classmethod
    def list_for_channel_filtered(cls, ch_type: str,
                                  slot_names: List[str]) -> List[SlotSemantics]:
        """Return semantics for *ch_type* restricted to the given slot names."""
        norm = _norm_ch_type(ch_type)
        wanted = set(slot_names)
        out: List[SlotSemantics] = []
        for (ct, sl), sem in cls._registry.items():
            if ct == norm and sl.value in wanted:
                out.append(sem)
        slot_order = list(UnifiedParamSlot)
        out.sort(key=lambda s: slot_order.index(s.slot)
                 if s.slot in slot_order else 999)
        return out

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (primarily for testing)."""
        cls._registry.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  Convenience helper
# ─────────────────────────────────────────────────────────────────────────────

def slot_semantics(
    slot: UnifiedParamSlot,
    label_cn: str,
    description: str,
    param_type: str = "float",
    default: Any = 0,
    min_val: float = 0.0,
    max_val: float = 1.0,
    choices: Optional[List[str]] = None,
) -> SlotSemantics:
    """Short-hand factory for building SlotSemantics with less boilerplate."""
    return SlotSemantics(
        slot=slot,
        label_cn=label_cn,
        description=description,
        param_type=param_type,
        default=default,
        min_val=min_val,
        max_val=max_val,
        choices=choices or [],
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Pre-defined Registrations
# ─────────────────────────────────────────────────────────────────────────────

def _register_all() -> None:
    """Populate the SlotRegistry with semantics for every channel type.

    Called once at module import.  Extend this function when adding new
    channel types or new slot semantics.
    """
    S = slot_semantics  # short alias

    # ═══════════════════════════════════════════════════════════════════════
    #  MOTOR_X / MOTOR_Y
    # ═══════════════════════════════════════════════════════════════════════
    for ch in ("motor_x", "motor_y"):
        SlotRegistry.bulk_register(ch, [
            (UnifiedParamSlot.P1_MODE, S(
                UnifiedParamSlot.P1_MODE, "运行模式",
                "电机运行模式：停止、摆幅摆动、绝对定位或跟踪目标",
                "choice", "摆幅", choices=["停止", "摆幅", "定位", "跟踪"])),
            (UnifiedParamSlot.ASS1_ANGLE_CTRL, S(
                UnifiedParamSlot.ASS1_ANGLE_CTRL, "角度控制",
                "手动控制或偏移当前角度（度）",
                "float", 0.0, 0.0, 360.0)),
            (UnifiedParamSlot.ASS3_TARGET_ANGLE, S(
                UnifiedParamSlot.ASS3_TARGET_ANGLE, "目标角度",
                "绝对定位的目标角度（度）",
                "float", 180.0, 0.0, 360.0)),
            (UnifiedParamSlot.ASS6_TWEEN_SPEED, S(
                UnifiedParamSlot.ASS6_TWEEN_SPEED, "补间速度",
                "角度过渡/补间速度系数；越大过渡越快",
                "float", 1.0, 0.01, 10.0)),
            (UnifiedParamSlot.ASS8_WATER_OFF, S(
                UnifiedParamSlot.ASS8_WATER_OFF, "关水模式",
                "电机收到关水信号时的行为",
                "choice", "关闭", choices=["关闭", "定位关水", "归零关水"])),
        ])

    # ═══════════════════════════════════════════════════════════════════════
    #  MOTOR_Z
    # ═══════════════════════════════════════════════════════════════════════
    SlotRegistry.bulk_register("motor_z", [
        (UnifiedParamSlot.P1_MODE, S(
            UnifiedParamSlot.P1_MODE, "运行模式",
            "Z轴运行模式：停止、绝对定位或跟踪",
            "choice", "停止", choices=["停止", "定位", "跟踪"])),
        (UnifiedParamSlot.ASS3_TARGET_ANGLE, S(
            UnifiedParamSlot.ASS3_TARGET_ANGLE, "目标位置",
            "Z轴目标位置（度）",
            "float", 128.0, 0.0, 255.0)),
        (UnifiedParamSlot.ASS8_WATER_OFF, S(
            UnifiedParamSlot.ASS8_WATER_OFF, "关水模式",
            "Z轴收到关水信号时的行为",
            "choice", "关闭", choices=["关闭", "归零关水"])),
    ])

    # ═══════════════════════════════════════════════════════════════════════
    #  PUMP
    # ═══════════════════════════════════════════════════════════════════════
    SlotRegistry.bulk_register("pump", [
        (UnifiedParamSlot.P2_HEIGHT, S(
            UnifiedParamSlot.P2_HEIGHT, "高度/压力",
            "泵的输出压力/高度（0-255）",
            "int", 128, 0.0, 255.0)),
        (UnifiedParamSlot.P9_PRESET_FREQ, S(
            UnifiedParamSlot.P9_PRESET_FREQ, "预置频率",
            "泵的预设摆动/脉冲频率（Hz）",
            "float", 1.0, 0.1, 50.0)),
        (UnifiedParamSlot.ASS8_WATER_OFF, S(
            UnifiedParamSlot.ASS8_WATER_OFF, "关水模式",
            "泵收到关水信号时的行为",
            "choice", "关闭", choices=["关闭", "归零关水"])),
    ])

    # ═══════════════════════════════════════════════════════════════════════
    #  VALVE
    # ═══════════════════════════════════════════════════════════════════════
    SlotRegistry.bulk_register("valve", [
        (UnifiedParamSlot.P2_HEIGHT, S(
            UnifiedParamSlot.P2_HEIGHT, "开关状态",
            "阀门的开启程度（0=关闭, 255=全开）",
            "int", 255, 0.0, 255.0)),
        (UnifiedParamSlot.P5_DELAY, S(
            UnifiedParamSlot.P5_DELAY, "延迟模式",
            "阀门开启的延迟策略",
            "choice", "无延迟", choices=["无延迟", "固定延迟", "随机延迟"])),
    ])

    # ═══════════════════════════════════════════════════════════════════════
    #  DIMMER
    # ═══════════════════════════════════════════════════════════════════════
    SlotRegistry.bulk_register("dimmer", [
        (UnifiedParamSlot.P1_MODE, S(
            UnifiedParamSlot.P1_MODE, "灯模式",
            "灯光工作模式：常亮、呼吸、闪烁或渐变",
            "choice", "常亮", choices=["常亮", "呼吸", "闪烁", "渐变"])),
        (UnifiedParamSlot.P2_HEIGHT, S(
            UnifiedParamSlot.P2_HEIGHT, "亮度",
            "灯光亮度值（0-255）",
            "int", 255, 0.0, 255.0)),
    ])

    # ═══════════════════════════════════════════════════════════════════════
    #  R / G / B / W  (same semantics as dimmer)
    # ═══════════════════════════════════════════════════════════════════════
    for ch in ("r", "g", "b", "w"):
        SlotRegistry.bulk_register(ch, [
            (UnifiedParamSlot.P1_MODE, S(
                UnifiedParamSlot.P1_MODE, "颜色通道模式",
                f"颜色通道工作模式：常亮、呼吸、闪烁或渐变",
                "choice", "常亮", choices=["常亮", "呼吸", "闪烁", "渐变"])),
            (UnifiedParamSlot.P2_HEIGHT, S(
                UnifiedParamSlot.P2_HEIGHT, "通道强度",
                f"颜色通道强度值（0-255）",
                "int", 255, 0.0, 255.0)),
        ])


# ── Register at module import ──────────────────────────────────────────────
_register_all()
