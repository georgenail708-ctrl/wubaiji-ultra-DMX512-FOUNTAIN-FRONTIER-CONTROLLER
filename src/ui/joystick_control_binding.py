"""
joystick_control_binding.py
────────────────────────────────────────────────────────────────────────────
外接摇杆控件绑定模块

功能：
  - JoystickControlBindingRule — 绑定规则 dataclass（ch_type ↔ 物理轴映射）
  - JoystickControlBindManager — 全局单例，绑定注册表 + 轴数据路由引擎
  - AxisDetectPopup — 单轴自动检测弹窗（普通控件用）
  - MultiAxisDetectPopup — 多轴顺序检测弹窗（XYJoystick 用）
  - BindingMappingDialog — 映射调整弹窗（曲线/范围/死区/反转）
  - BindingManagerDialog — 绑定管理子窗口（非模态表格）
  - ExternalJoystickConfigDialog — 设备配置窗口（菜单入口）
  - 内置摇杆模拟器 — 绕过 pygame 驱动绑定全链路验证

依赖：PyQt6, pygame (via PhysicalJoystickDriver)
不导入 programmer_panel 的任何符号 — 通过回调解耦
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLabel, QDialog, QDialogButtonBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QProgressBar, QMessageBox, QFrame,
    QAbstractItemView, QSlider,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QObject, QTimer, QSettings, QSize,
)
from PyQt6.QtGui import QColor, QFont

# ── 从同包导入物理摇杆驱动 ────────────────────────────────────────────────
from .physical_joystick_driver import (
    PhysicalJoystickDriver, JoystickDeviceInfo, get_physical_joystick_driver,
)

# ═══════════════════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════════════════
_SETTINGS_ORG = "Qascade"
_SETTINGS_APP = "JoystickControlBindings"
_SETTINGS_KEY = "control_bindings_v1"
# XYZ 轴输出放缩比例持久化键（与绑定键分开，避免旧档格式迁移）
_SETTINGS_SCALE_KEY = "axis_scale_v1"

_DETECT_TIMEOUT_S = 10       # 轴检测超时（秒）
_DETECT_THRESHOLD  = 0.15    # 轴动作判定阈值
_AXIS_LABELS = {"x": "X 轴", "y": "Y 轴", "z": "Z 轴", "w": "W 轴"}

CURVE_TYPES = ["linear", "gamma", "s_curve", "stepped", "inverted", "feel_balance"]
CURVE_LABELS = {
    "linear":       "线性",
    "gamma":        "伽马",
    "s_curve":      "S 曲线",
    "stepped":      "阶梯",
    "inverted":     "反转",
    "feel_balance": "手感配平",
}

# 暗色主题配色 —— 统一映射自 qascade_theme 的 主题令牌（原私有夜紫底已收敛）
from .qascade_theme import (
    BG_ROOT as _J_BG_ROOT,
    BG_BOX as _J_BG_CARD,
    BG_INPUT as _J_BG_INPUT,
    BORDER_NORMAL as _J_BORDER,
    TEXT_PRIMARY as _J_FG_MAIN,
    TEXT_SECONDARY as _J_FG_DIM,
    FOCUS_BLUE as _J_FG_ACCENT,
    BRIGHT_INFO as _J_FG_CYAN,
    BRIGHT_SUCCESS as _J_FG_GREEN,
    BRIGHT_ERROR as _J_FG_RED,
)

BG_DARK   = _J_BG_ROOT       # #1D1D1D 根底
BG_CARD   = _J_BG_CARD       # #2D2D2D 卡片
BG_INPUT  = _J_BG_INPUT      # #1D1D1D 输入内底
FG_MAIN   = _J_FG_MAIN       # #E6E6E6 正文
FG_DIM    = _J_FG_DIM        # #999999 弱化
FG_ACCENT = _J_FG_ACCENT     # #71A8FF 强调
FG_CYAN   = _J_FG_CYAN       # #6FA8DC 信息蓝
FG_GREEN  = _J_FG_GREEN      # #56B65B 成功绿
FG_RED    = _J_FG_RED        # #E36855 错误红
BORDER    = _J_BORDER        # #3D3D3D 描边


# ═══════════════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _apply_curve(normalized: float, curve: str, gamma: float = 1.0, steps: int = 8) -> float:
    """将 0.0-1.0 归一化输入应用曲线映射，返回 0.0-1.0。"""
    v = max(0.0, min(1.0, normalized))
    if curve == "gamma":
        v = v ** max(0.1, gamma)
    elif curve == "s_curve":
        if v < 0.5:
            v = 2.0 * (v ** max(0.1, gamma))
        else:
            v = 1.0 - 2.0 * ((1.0 - v) ** max(0.1, gamma))
        v = max(0.0, min(1.0, v / 2.0 if v < 0.5 else 0.5 + (v - 1.0) / -2.0))
        v = max(0.0, min(1.0, v))
    elif curve == "stepped":
        s = max(2, steps)
        v = math.floor(v * s) / (s - 1)
        v = max(0.0, min(1.0, v))
    elif curve == "inverted":
        v = 1.0 - v
    elif curve == "feel_balance":
        # 手感配平：近中位放大输出，远中位压缩输出
        # 补偿弹簧摇杆中心阻力大、远中位阻力小的问题
        # 配平系数 (gamma): 1.0=标准, >1 更强, 0=线性
        balance = max(0.0, min(5.0, gamma))
        if balance < 0.01:
            return v  # 系数为 0 → 完全线性
        # 对偏离中位的量应用幂函数：指数 <1 放大小偏差(近中位)，指数 >1 压缩大偏差(远中位)
        # 使用平滑的变指数：指数随偏差从 lo_exp渐变到 hi_exp
        v_centered = v - 0.5
        sign = 1.0 if v_centered >= 0 else -1.0
        v_abs = abs(v_centered) * 2.0
        # 指数随配平系数调整
        lo_exp = max(0.15, 0.55 - balance * 0.18)   # 近中位指数
        hi_exp = min(4.0, 1.0 + balance * 0.8)       # 远中位指数
        # 连续变指数：exp = lo_exp + (hi_exp - lo_exp) * v_abs
        exp = lo_exp + (hi_exp - lo_exp) * v_abs
        v_out = v_abs ** exp
        v_out = max(0.0, min(1.0, v_out))
        v = 0.5 + sign * v_out / 2.0
    # "linear" or unrecognized: pass through
    return max(0.0, min(1.0, v))


def _normalize_axis(raw: float, deadzone: float, invert: bool) -> float:
    """原始轴值 [0,1] → 死区 + 反转 → 归一化 [0,1]。"""
    v = raw - 0.5                     # 中心归零
    half_dz = deadzone / 2.0
    if abs(v) < half_dz:
        return 0.5                    # 死区内 → 中位
    if v > 0:
        v = (v - half_dz) / (0.5 - half_dz)
    else:
        v = (v + half_dz) / (0.5 - half_dz)
    v = v * 0.5 + 0.5                 # 映射回 [0,1]
    if invert:
        v = 1.0 - v
    return max(0.0, min(1.0, v))


# ═══════════════════════════════════════════════════════════════════════════
#  1. JoystickControlBindingRule
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class JoystickControlBindingRule:
    """一条控件绑定规则：物理摇杆轴 → 通道/effect 参数值映射。"""
    ch_type: str              # 通道类型 "dimmer", "R", "motor_x"（effect 参数时=显示名）
    physical_axis: str        # 物理轴 "x" | "y"
    sub_axis: str = ""        # 控件子轴(仅 XYJoystick): "x"/"y"/"z"，空=普通控件
    space_mode: str = ""      # 空间模式(仅 XYJoystick): "2d"/"3d"，空=不区分
    invert: bool = False
    deadzone: float = 0.0     # ★ 修复: 默认0.0, 驱动层 _apply_deadzone 已处理死区, 避免双重压缩
    range_lo: int = 0
    range_hi: int = 255
    curve: str = "linear"
    gamma: float = 1.0
    steps: int = 8
    # ★ effect 参数绑定扩展：target_kind="effect_param" 时绑定目标是效果参数滑块。
    #   channel 上下文 = 当前通道表单（每通道页独立绑定）；param_key = 参数键。
    #   preset_id = 绑定时的效果种类（语义冲突按「同效果种类+同参数」判定）。
    target_kind: str = "channel"   # "channel" | "effect_param"
    channel: str = ""              # effect 参数绑定的通道上下文（空=全局）
    param_key: str = ""            # effect 参数键（如 speed/amplitude）
    param_label: str = ""          # effect 参数显示名
    preset_id: str = ""            # 效果种类 ID（语义冲突判定用）
    # ★ 串台修复：绑定目标设备类型容器 def_id（类型分区后同一 ch_type 存在多行，
    #   如数控基底电机 vs 数控三维通用二仪 均为 motor_x 单轴行）。绑定时刻记录，
    #   分发时刻按 def_id 精确解析目标行——不再依赖「可见行优先+行顺序」这类
    #   随 UI 状态（容器折叠/组切换）漂移的可变状态。空=旧式通用绑定（保持
    #   旧行为：可见行优先回退），兼容历史持久化数据。
    target_def_id: str = ""

    @property
    def binding_key(self) -> Tuple[str, str]:
        """复合唯一键：(ch_type, space_mode)。"""
        return (self.ch_type, self.space_mode)

    @property
    def is_xy_joystick(self) -> bool:
        return bool(self.sub_axis)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["binding_key"] = list(self.binding_key)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JoystickControlBindingRule":
        d.pop("binding_key", None)
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════
#  2. JoystickControlBindManager
# ═══════════════════════════════════════════════════════════════════════════

class JoystickControlBindManager(QObject):
    """
    绑定规则注册表 + 轴数据路由引擎（全局单例）。

    信号：
      binding_added(key: tuple)        — (ch_type, space_mode)
      binding_removed(key: tuple)
      bindings_cleared()
    """
    binding_added   = pyqtSignal(tuple)
    binding_removed = pyqtSignal(tuple)
    bindings_cleared = pyqtSignal()
    # ★ effect 参数绑定：物理轴 → 效果参数滑块值（0-255 归一）
    effect_param_changed = pyqtSignal(str, str, int)   # (channel, param_key, value)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bindings: Dict[Tuple[str, str], JoystickControlBindingRule] = {}
        self._value_handler: Optional[Callable] = None
        self._row_space_modes: Dict[str, str] = {}   # row_id → "2d"/"3d"
        # ★ XYZ 轴输出放缩比例（0.0-1.0，1.0=100% 原样输出）
        self._axis_scale: Dict[str, float] = {"x": 1.0, "y": 1.0, "z": 1.0, "w": 1.0}
        # ★ 串台二次修复: 绑定目标行无法确定性解析时的一次性提示标志
        #   (旧数据未迁移/跨工程 def 失效), ProgrammerPanel 分发与迁移共用。
        self.unresolved_warned: bool = False
        # 模拟器
        self._simulator_active: bool = False
        self._sim_x: float = 0.5
        self._sim_y: float = 0.5
        self._sim_z: float = 0.5
        self._sim_w: float = 0.5
        # 连接物理驱动 + MIDI 驱动
        self._driver: PhysicalJoystickDriver = get_physical_joystick_driver()
        self._driver.axis3d_changed.connect(self._on_axis3d_changed)
        try:
            from .midi_joystick_driver import get_midi_joystick_driver
            self._midi_driver = get_midi_joystick_driver()
            # MIDI signal has 3 args (x,y,z), pad w=0.5
            self._midi_driver.axis3d_changed.connect(
                lambda x, y, z: self._on_axis3d_changed(x, y, z, 0.5))
        except Exception:
            self._midi_driver = None
        # ★ 兜底：pygame 缺失/实体摇杆驱动初始化失败时，自动启用内置模拟器，
        #   保证无硬件/无 pygame 环境下摇杆绑定链路仍可验证。
        try:
            import pygame  # noqa: F401
            _joystick_backend_ok = True
        except Exception:
            _joystick_backend_ok = False
        if not _joystick_backend_ok:
            self.enable_simulator()
            print("[JoystickBind] pygame 不可用，已自动启用内置摇杆模拟器")
        # 恢复上次会话的绑定与轴放缩配置
        self.load()

    # ── 绑定规则 CRUD ─────────────────────────────────────────────────────

    def add_binding(
        self, ch_type: str, physical_axis: str,
        sub_axis: str = "", space_mode: str = "", target_def_id: str = "",
    ) -> Optional[JoystickControlBindingRule]:
        """添加绑定。key 已存在则返回 None（不重复绑定）。"""
        rule = JoystickControlBindingRule(
            ch_type=ch_type, physical_axis=physical_axis,
            sub_axis=sub_axis, space_mode=space_mode,
            target_def_id=target_def_id,
        )
        key = rule.binding_key
        if key in self._bindings:
            return None
        self._bindings[key] = rule
        self.binding_added.emit(key)
        self.save()
        return rule

    def add_bindings_batch(self, rules_data: List[dict]) -> List[JoystickControlBindingRule]:
        """批量添加（XYJoystick 多轴一次性注册），跳过已存在的。"""
        added = []
        for rd in rules_data:
            rule = self.add_binding(
                ch_type=rd.get("ch_type", ""),
                physical_axis=rd.get("physical_axis", "x"),
                sub_axis=rd.get("sub_axis", ""),
                space_mode=rd.get("space_mode", ""),
                target_def_id=rd.get("target_def_id", ""),
            )
            if rule is not None:
                for field in ("invert", "deadzone", "range_lo", "range_hi",
                              "curve", "gamma", "steps"):
                    if field in rd:
                        setattr(rule, field, rd[field])
                added.append(rule)
        return added

    def remove_binding(self, ch_type: str, space_mode: str = "") -> bool:
        key = (ch_type, space_mode)
        if key in self._bindings:
            del self._bindings[key]
            self.binding_removed.emit(key)
            self.save()
            return True
        return False

    def remove_bindings_for_control(self, ch_types: List[str], space_mode: str) -> int:
        """按控件批量取消（XYJoystick 一次性取消所有子轴）。"""
        removed = 0
        for ct in ch_types:
            if self.remove_binding(ct, space_mode):
                removed += 1
        return removed

    def get_binding(self, ch_type: str, space_mode: str = "") -> Optional[JoystickControlBindingRule]:
        return self._bindings.get((ch_type, space_mode))

    def has_binding(self, ch_type: str, space_mode: str = "") -> bool:
        return (ch_type, space_mode) in self._bindings

    def get_all_bindings(self) -> List[JoystickControlBindingRule]:
        return list(self._bindings.values())

    def get_active_bindings(self, row_space_mode: str = "") -> List[JoystickControlBindingRule]:
        """获取某空间模式下的活跃绑定（含 space_mode="" 的通用绑定）。"""
        result = []
        for key, rule in self._bindings.items():
            if rule.space_mode == "" or rule.space_mode == row_space_mode:
                result.append(rule)
        return result

    def clear_all_bindings(self):
        self._bindings.clear()
        self.bindings_cleared.emit()
        self.save()

    # ── 轴输出放缩比例 ─────────────────────────────────────────────────────

    def get_axis_scale(self, axis: str) -> float:
        """获取某物理轴（x/y/z/w）的输出放缩比例（0.0-1.0，1.0=100% 原样）。"""
        return self._axis_scale.get(axis, 1.0)

    def set_axis_scale(self, axis: str, scale: float):
        """设置某物理轴（x/y/z/w）的输出放缩比例并持久化。"""
        self._axis_scale[axis] = max(0.0, min(1.0, float(scale)))
        self.save()

    # ── 值回调 ────────────────────────────────────────────────────────────

    def set_value_handler(self, handler: Callable):
        """设置值回调 (rule, mapped_value) → None。由 ProgrammerPanel 注入。"""
        self._value_handler = handler

    # ── 空间模式追踪 ──────────────────────────────────────────────────────

    def set_row_space_mode(self, row_id: str, mode: str):
        self._row_space_modes[row_id] = mode

    def get_row_space_mode(self, row_id: str) -> str:
        return self._row_space_modes.get(row_id, "2d")

    # ── 轴路由引擎 ────────────────────────────────────────────────────────

    def _on_axis3d_changed(self, x: float, y: float, z: float, w: float = 0.5):
        """接收物理/MIDI 摇杆轴值（4D），路由到所有匹配的绑定规则。"""
        if self._simulator_active:
            return
        self._route_axis_values(x, y, z, w)

    def _route_axis_values(self, x: float, y: float, z: float = 0.5, w: float = 0.5):
        for rule in self._bindings.values():
            # space_mode filtering done in ProgrammerPanel value handler
            if rule.physical_axis == "x":
                raw = x
            elif rule.physical_axis == "y":
                raw = y
            elif rule.physical_axis == "z":
                raw = z
            elif rule.physical_axis == "w":
                raw = w
            else:
                continue
            norm = _normalize_axis(raw, rule.deadzone, rule.invert)
            curved = _apply_curve(norm, rule.curve, rule.gamma, rule.steps)
            mapped = int(round(rule.range_lo + curved * (rule.range_hi - rule.range_lo)))
            if rule.target_kind == "effect_param":
                # effect 参数绑定可以超过 DMX 0-255(如跑动编排滑块内部 0-1000)
                mapped = max(rule.range_lo, min(rule.range_hi, mapped))
            else:
                mapped = max(0, min(255, mapped))
            # ★ XYZ 轴输出放缩（中位对称）：输出 = 中位 + (原始映射值 − 中位) × 比例。
            #   中位（范围中点，0-255 时=128）恒不动；50% = 摇满只偏离到半程
            #   （0-255 范围 → 64/192）；0% = 无偏离（输出停在中位）。100% 跳过
            #   该分支，与旧路径逐字节一致。
            scale = self._axis_scale.get(rule.physical_axis, 1.0)
            if scale < 1.0:
                mid = round((rule.range_lo + rule.range_hi) / 2.0)
                mapped = int(round(mid + (mapped - mid) * scale))
                if rule.target_kind == "effect_param":
                    mapped = max(rule.range_lo, min(rule.range_hi, mapped))
                else:
                    mapped = max(0, min(255, mapped))
            if rule.target_kind == "effect_param":
                if rule.param_key:
                    self.effect_param_changed.emit(rule.channel or "", rule.param_key, mapped)
                continue
            if self._value_handler is None:
                continue
            self._value_handler(rule, float(mapped))

    # ── effect 参数绑定（独立命名空间，与通道绑定不冲突）───────────────────

    @staticmethod
    def _effect_key(channel: str, param_key: str) -> Tuple[str, str]:
        return (f"effect:{channel or ''}:{param_key}", "")

    def add_effect_param_binding(
        self, channel: str, param_key: str, param_label: str,
        physical_axis: str = "x", invert: bool = False, deadzone: float = 0.0,
        range_lo: int = 0, range_hi: int = 255, curve: str = "linear",
        gamma: float = 1.0, steps: int = 8, preset_id: str = "",
    ) -> "JoystickControlBindingRule":
        """新增/替换一条 effect 参数绑定（同 channel+param 即语义冲突 → 覆盖）。"""
        rule = JoystickControlBindingRule(
            ch_type=str(param_label or param_key), physical_axis=physical_axis,
            invert=invert, deadzone=deadzone, range_lo=range_lo, range_hi=range_hi,
            curve=curve, gamma=gamma, steps=steps,
            target_kind="effect_param", channel=str(channel or ""),
            param_key=str(param_key), param_label=str(param_label or param_key),
            preset_id=str(preset_id or ""))
        key = self._effect_key(channel, param_key)
        self._bindings[key] = rule
        self.binding_added.emit(key)
        return rule

    def find_effect_param_conflict(self, param_key: str, preset_id: str = ""):
        """语义冲突检测：**同效果种类 + 同参数** 即冲突（跨通道页也算）。

        同一参数的绑定若属不同效果种类（preset 不同）则不冲突——不同效果各自
        独立编排，摇杆驱动互不干扰。返回冲突的既有规则或 None。
        """
        if not param_key:
            return None
        for rule in self._bindings.values():
            if rule.target_kind != "effect_param" or rule.param_key != param_key:
                continue
            if rule.preset_id and preset_id and rule.preset_id != preset_id:
                continue    # 不同效果种类：允许
            return rule     # 同效果（或任一方未记效果）：语义冲突
        return None

    def get_effect_param_binding(self, channel: str, param_key: str):
        return self._bindings.get(self._effect_key(channel, param_key))

    def has_effect_param_binding(self, channel: str, param_key: str) -> bool:
        return self._effect_key(channel, param_key) in self._bindings

    def remove_effect_param_binding(self, channel: str, param_key: str) -> bool:
        key = self._effect_key(channel, param_key)
        if key in self._bindings:
            del self._bindings[key]
            self.binding_removed.emit(key)
            return True
        return False

    # ── 持久化 ────────────────────────────────────────────────────────────

    def save(self):
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            data = []
            for rule in self._bindings.values():
                data.append(rule.to_dict())
            s.setValue(_SETTINGS_KEY, json.dumps(data, ensure_ascii=False))
            s.setValue(_SETTINGS_SCALE_KEY, json.dumps(self._axis_scale, ensure_ascii=False))
        except Exception:
            pass

    def load(self):
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            raw = s.value(_SETTINGS_KEY, "", type=str)
            if raw:
                data = json.loads(raw)
                self._bindings.clear()
                for item in data:
                    rule = JoystickControlBindingRule.from_dict(item)
                    self._bindings[rule.binding_key] = rule
            scale_raw = s.value(_SETTINGS_SCALE_KEY, "", type=str)
            if scale_raw:
                loaded = json.loads(scale_raw)
                if isinstance(loaded, dict):
                    for axis in ("x", "y", "z", "w"):
                        v = loaded.get(axis)
                        if isinstance(v, (int, float)):
                            self._axis_scale[axis] = max(0.0, min(1.0, float(v)))
        except Exception:
            pass

    # ── 模拟器 ────────────────────────────────────────────────────────────

    def enable_simulator(self):
        self._simulator_active = True
        self._sim_x = 0.5
        self._sim_y = 0.5

    def disable_simulator(self):
        self._simulator_active = False

    def is_simulator_active(self) -> bool:
        return self._simulator_active

    def set_sim_axis(self, axis: str, value: float):
        """设置模拟轴值 (0.0~1.0) 并触发路由。"""
        if not self._simulator_active:
            return
        v = max(0.0, min(1.0, float(value)))
        if axis == "x":
            self._sim_x = v
        elif axis == "y":
            self._sim_y = v
        elif axis == "z":
            self._sim_z = v
        elif axis == "w":
            self._sim_w = v
        self._route_axis_values(self._sim_x, self._sim_y, self._sim_z, self._sim_w)


# ── 全局单例 ──────────────────────────────────────────────────────────────
_global_bind_mgr: Optional[JoystickControlBindManager] = None


def get_joystick_bind_manager() -> JoystickControlBindManager:
    """取全局绑定管理器单例（**自愈**：C++ 侧已销毁则重建）。

    ★ 2026-09-20：单例无 parent，宿主窗口被销毁时它可能被连带删除，而模块
      全局仍指着那个 Python 包装 → 后续调用方拿到**悬空对象**（构造面板时
      `mgr.binding_added.connect(...)` 抛 `wrapped C/C++ object has been
      deleted`，整个面板懒加载静默失败）。这里检测并重建，使调用方永远拿到
      健康实例（生产环境关窗重开同样受益）。
    """
    global _global_bind_mgr
    if _global_bind_mgr is not None:
        try:
            from PyQt6 import sip
            if not sip.isdeleted(_global_bind_mgr):
                return _global_bind_mgr
        except Exception:
            return _global_bind_mgr
    _global_bind_mgr = JoystickControlBindManager()
    return _global_bind_mgr


# ═══════════════════════════════════════════════════════════════════════════
#  3. AxisDetectPopup — 单轴检测
# ═══════════════════════════════════════════════════════════════════════════


# ── Re-exports (lazy — avoid circular imports in Python 3.13) ────────────
_AxisDetectPopup = None
_MultiAxisDetectPopup = None
_BindingMappingDialog = None
_BindingManagerDialog = None


def __getattr__(name):
    """Lazy re-export: import split modules only when accessed."""
    if name == "AxisDetectPopup":
        global _AxisDetectPopup
        if _AxisDetectPopup is None:
            from .joystick_control_binding_popups import AxisDetectPopup as _A
            _AxisDetectPopup = _A
        return _AxisDetectPopup
    if name == "MultiAxisDetectPopup":
        global _MultiAxisDetectPopup
        if _MultiAxisDetectPopup is None:
            from .joystick_control_binding_popups import MultiAxisDetectPopup as _M
            _MultiAxisDetectPopup = _M
        return _MultiAxisDetectPopup
    if name == "BindingMappingDialog":
        global _BindingMappingDialog
        if _BindingMappingDialog is None:
            from .joystick_control_binding_dialogs import BindingMappingDialog as _B
            _BindingMappingDialog = _B
        return _BindingMappingDialog
    if name == "BindingManagerDialog":
        global _BindingManagerDialog
        if _BindingManagerDialog is None:
            from .joystick_control_binding_dialogs import BindingManagerDialog as _BM
            _BindingManagerDialog = _BM
        return _BindingManagerDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
