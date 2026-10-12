"""
DMXTimeline — SelfMapEngine  v1.0
通道自映射处理引擎

职责：
  对选中设备组的指定通道类型进行二次处理（自己映射自己），支持：
    - 反转（Invert）：255 - 原值
    - 扇形展开（Fan）：按设备在选择组中的顺序，给每台设备添加固定差值
    - 定值赋值（Set Value）：将选中通道统一设为指定值
    - 缩放偏移（Scale+Offset）：线性变换
    - 正弦调制（Sine Modulate）：在原值基础上叠加正弦波
    - 自定义函数（Custom Function）：用户输入 numpy 表达式
    - 渐变分配（Gradient）：按位置线性渐变分配值
    - 随机散布（Random Scatter）：在指定范围内随机分配

  每个自映射规则（SelfMapRule）描述：
    - 目标设备组（fixture_ids 列表）
    - 目标通道类型（ch_type）
    - 处理预设（SelfMapPreset）
    - 参数字典

  引擎每 25ms tick 一次：
    - 从 NetworkManager 读取目标通道当前值
    - 经预设函数处理
    - 写回 NM linker 层（最高优先级 BASE 层）

  可序列化为广义 Cue 预设，保存到 Programmer 并集成到项目管理器。

★ 这是用户需求2的核心实现：
  "在通道链接页面实现自己类型通道数据的二次处理，也就是自己映射自己"
"""
from __future__ import annotations

import math
import uuid
import time
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from src.utils.safe_expression import safe_eval_expression

from ..engine.xy_reference import (
    logical_to_16bit_pair,
    read_axis_logical_value,
)
from ..core.constants import (  # 常量单源(2026-09-12)
    MOTOR_FINE_CHANNELS, MOTOR_FINE_TO_COARSE,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  自映射预设定义
# ─────────────────────────────────────────────────────────────────────────────

class SelfMapPresetDef:
    """一个自映射预设的完整定义。"""
    def __init__(
        self,
        id: str,
        name: str,
        category: str,
        desc: str,
        params_def: List[dict],
        fn: Callable,
    ):
        self.id         = id
        self.name       = name
        self.category   = category
        self.desc       = desc
        self.params_def = params_def  # [{"key","label","min","max","default","scale","unit"}]
        self.fn         = fn


# ── 预设函数实现 ──────────────────────────────────────────────────────────────

def _invert(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """反转：255 - 原值。"""
    return 255.0 - values


def _fan_spread(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """扇形展开：按设备索引添加固定差值，类似 MA 控台的 Fan 功能。
    center_value: 中心值
    spread: 总展开范围（第一台到最后一台的差值）
    """
    center = params.get("center_value", 128.0)
    spread = params.get("spread", 128.0)
    n = len(values)
    if n <= 1:
        return np.full_like(values, center)
    # 按索引线性分配：第一台 = center - spread/2, 最后一台 = center + spread/2
    offsets = np.linspace(-spread / 2.0, spread / 2.0, n)
    return np.full(n, center) + offsets


def _set_value(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """定值赋值：所有设备统一设为指定值。"""
    val = params.get("value", 128.0)
    return np.full_like(values, val)


def _scale_offset(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """缩放+偏移：output = input * scale + offset。"""
    scale = params.get("scale", 1.0)
    offset = params.get("offset", 0.0)
    return values * scale + offset


def _sine_modulate(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """正弦调制：在原值基础上叠加正弦波。
    freq: 频率（Hz）
    depth: 振幅
    phase_spread: 设备间相位展开（度）
    """
    freq = params.get("freq", 1.0)
    depth = params.get("depth", 64.0)
    phase_spread = params.get("phase_spread", 360.0)
    t = params.get("_time", 0.0)
    n = len(values)
    phases = np.linspace(0, math.radians(phase_spread), n) if n > 1 else np.array([0.0])
    wave = depth * np.sin(2 * math.pi * freq * t + phases)
    return values + wave


def _gradient(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """渐变分配：按设备索引从 start_val 线性渐变到 end_val。"""
    start_val = params.get("start_value", 0.0)
    end_val = params.get("end_value", 255.0)
    n = len(values)
    if n <= 1:
        return np.array([(start_val + end_val) / 2.0])
    return np.linspace(start_val, end_val, n)


def _random_scatter(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """随机散布：在指定范围内随机分配值。"""
    lo = params.get("lo", 0.0)
    hi = params.get("hi", 255.0)
    n = len(values)
    return np.random.uniform(lo, hi, n)


def _clamp_range(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """限幅：将值限制在指定范围内。"""
    lo = params.get("lo", 0.0)
    hi = params.get("hi", 255.0)
    return np.clip(values, lo, hi)


def _custom_function(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """自定义函数：用户输入 numpy 表达式。
    变量：v=当前值数组, i=设备索引数组, n=设备总数, t=时间, np=numpy
    """
    expr = params.get("expression", "v")
    n = len(values)
    t = params.get("_time", 0.0)
    try:
        result = safe_eval_expression(expr, {
            "v": values.copy(),
            "i": indices.copy(),
            "n": n,
            "t": t,
        })
        if isinstance(result, (int, float)):
            return np.full(n, float(result))
        return np.asarray(result, dtype=np.float64)[:n]
    except Exception as e:
        log.debug("SelfMap custom function error: %s", e)
        return values.copy()


def _phase_offset(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """相位偏移：给每台设备的值添加基于索引的固定偏移。
    类似 MA 控台的 Phase 功能。
    """
    total_offset = params.get("total_offset", 128.0)
    n = len(values)
    if n <= 1:
        return values.copy()
    offsets = np.linspace(0, total_offset, n)
    return values + offsets


def _mirror(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """镜像：从中间向两侧对称分配。
    第一台和最后一台相同，第二台和倒数第二台相同，以此类推。
    """
    n = len(values)
    if n <= 1:
        return values.copy()
    center = params.get("center_value", 128.0)
    spread = params.get("spread", 128.0)
    half = n // 2
    # 生成半边的值
    half_vals = np.linspace(center - spread / 2, center + spread / 2, half + 1)
    result = np.zeros(n)
    for idx in range(n):
        mirror_idx = min(idx, n - 1 - idx)
        pos = mirror_idx / max(1, half)
        result[idx] = center + (pos * spread - spread / 2)
    return result


def _joystick_mapping(values: np.ndarray, indices: np.ndarray, params: dict) -> np.ndarray:
    """实时摇杆映射关系。

    该预设不再代表“启用后立即展开角度”的静态 Fan，而是以当前输入值相对
    127.5 的偏移作为摇杆运动量，再根据 mapping_mode 改变各设备的方向、倍率
    与相位关系。输入接近中心时输出仍接近中心，因此 Cue 加载/启用本身不会
    制造可见角度。
    """
    n = len(values)
    if n <= 0:
        return values.copy()

    mode = str(params.get("mapping_mode", "normal") or "normal")
    slot = float(params.get("slot_value", params.get("value", 128.0)))
    strength = np.clip(slot / 255.0, 0.0, 1.0)
    center = float(params.get("center", 127.5))

    delta = values.astype(np.float64) - center
    # 中心死区：摇杆/输入接近中心时不产生映射差异。
    deadband = float(params.get("deadband", 1.0))
    delta[np.abs(delta) <= deadband] = 0.0

    if n <= 1:
        rel = np.zeros(n, dtype=np.float64)
    else:
        rel = np.linspace(-1.0, 1.0, n)

    gains = np.ones(n, dtype=np.float64)
    signs = np.ones(n, dtype=np.float64)

    if mode == "invert":
        signs *= -1.0
    elif mode == "fan":
        # 扇形不再直接设展开角度；它只让摇杆偏移按顺序产生相位/倍率差。
        gains = 1.0 + rel * strength
    elif mode == "cross":
        signs = np.where((indices.astype(int) % 2) == 0, 1.0, -1.0)
        gains = 1.0 + np.abs(rel) * strength * 0.5
    elif mode == "mirror":
        signs = np.where(rel < 0, -1.0, 1.0)
        gains = 1.0 + np.abs(rel) * strength * 0.5
    elif mode == "normal":
        gains = np.ones(n, dtype=np.float64)
    else:
        gains = np.ones(n, dtype=np.float64)

    return center + delta * signs * gains


# ── 内置预设列表 ──────────────────────────────────────────────────────────────

BUILTIN_SELFMAP_PRESETS: List[SelfMapPresetDef] = [
    SelfMapPresetDef(
        id="selfmap_joystick_map",
        name="摇杆映射关系",
        category="实时映射",
        desc="保存摇杆到设备的方向、相位和倍率关系；Cue 启用本身不直接产生角度输出。",
        params_def=[
            {"key": "slot_value", "label": "数值槽", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_joystick_mapping,
    ),
    SelfMapPresetDef(
        id="selfmap_invert",
        name="反转（255 - 原值）",
        category="基础变换",
        desc="将选中通道的值反转：output = 255 - input",
        params_def=[],
        fn=_invert,
    ),
    SelfMapPresetDef(
        id="selfmap_fan_spread",
        name="扇形展开（Fan）",
        category="空间分配",
        desc="按设备顺序线性展开，类似 MA 控台 Fan 功能。中心值固定，两端对称展开。",
        params_def=[
            {"key": "center_value", "label": "中心值",   "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "spread",       "label": "展开范围", "min": 0, "max": 510, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_fan_spread,
    ),
    SelfMapPresetDef(
        id="selfmap_set_value",
        name="定值赋值",
        category="基础变换",
        desc="将选中通道统一设为指定值",
        params_def=[
            {"key": "value", "label": "目标值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_set_value,
    ),
    SelfMapPresetDef(
        id="selfmap_scale_offset",
        name="缩放+偏移",
        category="基础变换",
        desc="线性变换：output = input × scale + offset",
        params_def=[
            {"key": "scale",  "label": "缩放系数", "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量",   "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_scale_offset,
    ),
    SelfMapPresetDef(
        id="selfmap_sine_modulate",
        name="正弦调制",
        category="动态效果",
        desc="在原值基础上叠加正弦波，设备间可设相位展开",
        params_def=[
            {"key": "freq",         "label": "频率",     "min": 1,   "max": 1000, "default": 100, "scale": 0.01, "unit": "Hz"},
            {"key": "depth",        "label": "振幅",     "min": 0,   "max": 255,  "default": 64,  "scale": 1.0,  "unit": ""},
            {"key": "phase_spread", "label": "相位展开", "min": 0,   "max": 720,  "default": 360, "scale": 1.0,  "unit": "°"},
        ],
        fn=_sine_modulate,
    ),
    SelfMapPresetDef(
        id="selfmap_gradient",
        name="渐变分配",
        category="空间分配",
        desc="按设备顺序从起始值线性渐变到结束值",
        params_def=[
            {"key": "start_value", "label": "起始值", "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "end_value",   "label": "结束值", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_gradient,
    ),
    SelfMapPresetDef(
        id="selfmap_random_scatter",
        name="随机散布",
        category="空间分配",
        desc="在指定范围内为每台设备随机分配值",
        params_def=[
            {"key": "lo", "label": "下限", "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "hi", "label": "上限", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_random_scatter,
    ),
    SelfMapPresetDef(
        id="selfmap_clamp",
        name="限幅",
        category="基础变换",
        desc="将值限制在指定范围内",
        params_def=[
            {"key": "lo", "label": "下限", "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "hi", "label": "上限", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_clamp_range,
    ),
    SelfMapPresetDef(
        id="selfmap_custom_function",
        name="自定义函数",
        category="高级",
        desc="输入 numpy 表达式进行自定义处理。变量：v=值数组, i=索引, n=设备数, t=时间",
        params_def=[
            {"key": "expression", "label": "表达式", "min": 0, "max": 0, "default": "v", "scale": 1.0, "unit": ""},
        ],
        fn=_custom_function,
    ),
    SelfMapPresetDef(
        id="selfmap_phase_offset",
        name="相位偏移",
        category="空间分配",
        desc="给每台设备添加基于索引的固定偏移量",
        params_def=[
            {"key": "total_offset", "label": "总偏移量", "min": -510, "max": 510, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_phase_offset,
    ),
    SelfMapPresetDef(
        id="selfmap_mirror",
        name="镜像对称",
        category="空间分配",
        desc="从中间向两侧对称分配值",
        params_def=[
            {"key": "center_value", "label": "中心值",   "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "spread",       "label": "展开范围", "min": 0, "max": 510, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_mirror,
    ),
]

# 按 ID 索引
SELFMAP_PRESET_BY_ID: Dict[str, SelfMapPresetDef] = {p.id: p for p in BUILTIN_SELFMAP_PRESETS}


# ─────────────────────────────────────────────────────────────────────────────
#  自映射规则数据模型
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SelfMapRule:
    """一条自映射规则。"""
    id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    name:        str = "新自映射规则"
    enabled:     bool = True

    # 目标设备 ID 列表（按顺序，顺序影响 Fan/Gradient 等空间分配效果）
    fixture_ids: List[str] = field(default_factory=list)

    # 目标通道类型
    ch_type:     str = "pump"

    # 自映射预设
    preset_id:   str = "selfmap_joystick_map"
    params:      Dict[str, Any] = field(default_factory=dict)

    # 设备组标签（仅用于 UI 显示）
    group_label: str = ""

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "name":        self.name,
            "enabled":     self.enabled,
            "fixture_ids": list(self.fixture_ids),
            "ch_type":     self.ch_type,
            "preset_id":   self.preset_id,
            "params":      dict(self.params),
            "group_label": self.group_label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SelfMapRule":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "规则"),
            enabled=d.get("enabled", True),
            fixture_ids=list(d.get("fixture_ids", [])),
            ch_type=d.get("ch_type", "pump"),
            preset_id=d.get("preset_id", "selfmap_joystick_map"),
            params=dict(d.get("params", {})),
            group_label=d.get("group_label", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  广义 Cue 预设数据模型
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneralizedCue:
    """广义 Cue：包含一组自映射规则 + 关联规则的完整预设。
    不再是静态快照，而是一整套动态处理逻辑。
    """
    id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    name:        str = "Cue Preset"
    description: str = ""
    color:       str = "#4A90D9"

    # 自映射规则列表
    self_map_rules: List[SelfMapRule] = field(default_factory=list)

    # 可选：关联规则列表（复用 LinkRule 格式）
    link_rules_data: List[dict] = field(default_factory=list)

    # 创建时间
    created_at:  float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "name":            self.name,
            "description":     self.description,
            "color":           self.color,
            "self_map_rules":  [r.to_dict() for r in self.self_map_rules],
            "link_rules_data": list(self.link_rules_data),
            "created_at":      self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GeneralizedCue":
        cue = cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Cue"),
            description=d.get("description", ""),
            color=d.get("color", "#4A90D9"),
            created_at=d.get("created_at", time.time()),
        )
        cue.self_map_rules = [
            SelfMapRule.from_dict(r) for r in d.get("self_map_rules", [])
        ]
        cue.link_rules_data = list(d.get("link_rules_data", []))
        return cue


# ─────────────────────────────────────────────────────────────────────────────
#  SelfMapEngine
# ─────────────────────────────────────────────────────────────────────────────

class SelfMapEngine(QObject):
    """
    自映射处理引擎。
    - 持有 SelfMapRule 列表
    - 每 25ms tick 一次，读取 NM 快照，计算自映射值，写入 NM linker 层
    - 支持全局启用/停用
    - 支持广义 Cue 的加载/保存
    """

    rules_changed = pyqtSignal()
    cue_saved     = pyqtSignal(object)  # GeneralizedCue

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules:   List[SelfMapRule] = []
        self._enabled: bool = False
        self._nm = None
        self._pm = None  # ProjectManager 引用

        # 设备地址缓存：{fixture_id: {ch_type: [(universe, dmx_addr)]}}
        self._addr_cache: Dict[str, Dict[str, List[Tuple[int, int]]]] = {}

        # 广义 Cue 库
        self._cue_library: List[GeneralizedCue] = []

        # 时间基准
        self._start_time: float = time.time()

        self._timer = QTimer(self)
        # ★ PreciseTimer：Qt 默认 CoarseTimer 会把 interval 取整到
        #   15.625ms 倍数（25ms→31.68ms），主链路定时器必须精确。
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._tick)

    # ── 注入 ─────────────────────────────────────────────────────────────────

    def set_network_manager(self, nm):
        self._nm = nm

    def set_project_manager(self, pm):
        self._pm = pm
        self._rebuild_addr_cache()

    def _rebuild_addr_cache(self):
        """从 ProjectManager 构建设备地址缓存。

        该方法需要同时兼容项目里的正式数据模型和 UI/测试中常见的字典结构：
        1. FixtureInstance + FixtureDefinition：通过 fi.def_id 找定义中的 channels；
        2. 字典设备：id/universe/address 或 start_channel/channels；
        3. 设备实例自身携带 channels，而没有 fixture_defs 的场景。

        缓存格式统一为：{fixture_id: {ch_type: [(universe, dmx_addr0)]}}，其中 dmx_addr0 为 0 基地址。
        """
        self._addr_cache.clear()
        if self._pm is None:
            return

        def _get(obj, *names, default=None):
            for name in names:
                if isinstance(obj, dict) and name in obj:
                    return obj.get(name)
                if hasattr(obj, name):
                    return getattr(obj, name)
            return default

        def _iter_fixtures(pm):
            for attr in ("fixtures", "fixture_instances", "devices"):
                val = _get(pm, attr, default=None)
                if val is not None:
                    try:
                        return list(val.values()) if isinstance(val, dict) else list(val)
                    except Exception:
                        log.debug("self_map: failed to iterate fixtures")
                        pass
            for meth in ("get_fixtures", "list_fixtures", "get_devices"):
                fn = getattr(pm, meth, None)
                if callable(fn):
                    try:
                        val = fn()
                        return list(val.values()) if isinstance(val, dict) else list(val)
                    except Exception:
                        log.debug("self_map: failed to get fixtures from method")
                        pass
            return []

        def _definition_map(pm):
            defs_raw = _get(pm, "fixture_defs", "definitions", "fixture_definitions", default=None)
            if defs_raw is None:
                return {}
            if isinstance(defs_raw, dict):
                return defs_raw
            out = {}
            try:
                for d in defs_raw:
                    did = _get(d, "id", "def_id", default=None)
                    if did is not None:
                        out[str(did)] = d
            except Exception:
                log.debug("self_map: failed to build definition map")
                pass
            return out

        fixtures = _iter_fixtures(self._pm)
        defs = _definition_map(self._pm)

        for fi in fixtures:
            fi_id = _get(fi, "id", "fixture_id", default=None)
            if fi_id is None:
                continue
            uni = int(_get(fi, "universe", "uni", default=1) or 1)
            start_ch = _get(fi, "start_channel", "address", "dmx_address", "start", default=1)
            try:
                start_ch = int(start_ch or 1)
            except Exception:
                start_ch = 1

            ch_list = _get(fi, "channels", "channel_types", default=None)
            if not ch_list:
                def_id = _get(fi, "def_id", "definition_id", "fixture_def_id", default=None)
                fd = defs.get(def_id) or defs.get(str(def_id)) if def_id is not None else None
                ch_list = _get(fd, "channels", "channel_types", default=[]) if fd is not None else []
            if isinstance(ch_list, dict):
                # 支持 {"motor": 0} 或 {0: "motor"} 两种映射。
                converted = []
                for k, v in ch_list.items():
                    if isinstance(k, int):
                        converted.append((int(k), str(v)))
                    else:
                        try:
                            converted.append((int(v), str(k)))
                        except Exception:
                            converted.append((len(converted), str(k)))
                pairs = converted
            else:
                pairs = []
                for offset, ch in enumerate(ch_list or []):
                    if isinstance(ch, dict):
                        ch_type = _get(ch, "type", "ch_type", "name", "value", default="")
                    else:
                        ch_type = getattr(ch, "value", ch)
                    pairs.append((offset, str(ch_type)))

            ch_map: Dict[str, List[Tuple[int, int]]] = {}
            for offset, ch_type in pairs:
                dmx_addr = start_ch - 1 + int(offset)
                if 0 <= dmx_addr < 512 and ch_type:
                    ch_map.setdefault(ch_type, []).append((uni, dmx_addr))
            if ch_map:
                self._addr_cache[fi_id] = ch_map
                self._addr_cache[str(fi_id)] = ch_map
    # ── 启停 ─────────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if enabled:
            self._start_time = time.time()
            self._rebuild_addr_cache()
            self._timer.start()
        else:
            self._timer.stop()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── 规则管理 ─────────────────────────────────────────────────────────────

    def add_rule(self, rule: SelfMapRule):
        self._rules.append(rule)
        self.rules_changed.emit()

    def remove_rule(self, rule_id: str):
        self._rules = [r for r in self._rules if r.id != rule_id]
        self.rules_changed.emit()

    def update_rule(self, rule: SelfMapRule):
        for i, r in enumerate(self._rules):
            if r.id == rule.id:
                self._rules[i] = rule
                self.rules_changed.emit()
                return

    def get_rules(self) -> List[SelfMapRule]:
        return list(self._rules)

    def clear_rules(self):
        self._rules.clear()
        self.rules_changed.emit()

    # ── 广义 Cue 管理 ────────────────────────────────────────────────────────

    def save_as_cue(self, name: str = "", description: str = "") -> GeneralizedCue:
        """将当前所有自映射规则保存为广义 Cue。"""
        cue = GeneralizedCue(
            name=name or f"Cue {len(self._cue_library) + 1}",
            description=description,
            self_map_rules=[SelfMapRule.from_dict(r.to_dict()) for r in self._rules],
        )
        self._cue_library.append(cue)
        self.cue_saved.emit(cue)
        return cue

    def load_cue(self, cue: GeneralizedCue):
        """加载广义 Cue 到当前规则列表。"""
        self._rules = [SelfMapRule.from_dict(r.to_dict()) for r in cue.self_map_rules]
        self.rules_changed.emit()

    def get_cue_library(self) -> List[GeneralizedCue]:
        return list(self._cue_library)

    def add_cue_to_library(self, cue: GeneralizedCue):
        self._cue_library.append(cue)

    def remove_cue_from_library(self, cue_id: str):
        self._cue_library = [c for c in self._cue_library if c.id != cue_id]

    # ── 核心计算 ─────────────────────────────────────────────────────────────

    def _tick(self):
        """每 25ms 执行一次自映射计算。"""
        if not self._enabled or self._nm is None:
            return
        if not self._rules:
            return

        t = time.time() - self._start_time
        writes: Dict[int, Dict[int, int]] = {}

        for rule in self._rules:
            if not rule.enabled:
                continue
            try:
                self._process_rule(rule, t, writes)
            except Exception as e:
                log.debug("SelfMapRule %s error: %s", rule.id, e)

        # 写入 NM linker 层
        writes = {int(uni): ch_map for uni, ch_map in writes.items() if ch_map}
        if writes:
            try:
                if hasattr(self._nm, "update_layer_sparse_batch"):
                    self._nm.update_layer_sparse_batch("linker", writes)
                else:
                    for uni, ch_map in writes.items():
                        self._nm.update_layer_sparse("linker", uni, ch_map)
            except Exception as e:
                log.debug("SelfMap write linker failed: %s", e)

    def _process_rule(self, rule: SelfMapRule, t: float,
                      writes: Dict[int, Dict[int, int]]):
        """处理单条自映射规则。"""
        preset_def = SELFMAP_PRESET_BY_ID.get(rule.preset_id)
        if preset_def is None:
            return

        fixture_ids = rule.fixture_ids
        if not fixture_ids:
            return

        current_values = []
        addr_items = []  # [(fixture_id, universe, dmx_addr, fine_addr, ch_type)]
        snapshot_cache: Dict[int, np.ndarray] = {}

        def _fine_addr_for_fixture(fi_id: str, coarse_type: str, uni: int) -> Optional[int]:
            fine_type = MOTOR_FINE_CHANNELS.get(str(coarse_type))
            if not fine_type:
                return None
            cache = self._addr_cache.get(fi_id, {}) or self._addr_cache.get(str(fi_id), {}) or {}
            for cand_uni, cand_addr in cache.get(fine_type, []) or []:
                try:
                    if int(cand_uni) == int(uni) and 0 <= int(cand_addr) < 512:
                        return int(cand_addr)
                except Exception:
                    continue
            try:
                if self._nm is not None and hasattr(self._nm, "find_fixture_channel_addr"):
                    found = self._nm.find_fixture_channel_addr(fi_id, fine_type, universe=uni)
                    if found is not None and 0 <= int(found) < 512:
                        return int(found)
            except Exception:
                log.debug("self_map: failed to find fine channel address")
                pass
            return None

        def _snapshot_for_universe(uni: int) -> np.ndarray:
            uni = int(uni)
            cached = snapshot_cache.get(uni)
            if cached is not None:
                return cached
            data = None
            try:
                if self._nm is not None and hasattr(self._nm, "compute_virtual_universe_now"):
                    data = self._nm.compute_virtual_universe_now(
                        uni,
                        exclude_layers=["linker"],
                        exclude_ltp_editor_layers={"linker"},
                    )
                elif self._nm is not None and hasattr(self._nm, "get_virtual_universe_data"):
                    data = self._nm.get_virtual_universe_data(uni)
                elif self._nm is not None:
                    data = self._nm.get_universe_data(uni)
            except Exception:
                try:
                    data = self._nm.get_universe_data(uni) if self._nm is not None else None
                except Exception:
                    data = None
            if data is None:
                data = np.zeros(512, dtype=np.uint8)
            arr = np.asarray(data, dtype=np.uint8).reshape(-1)
            if arr.size != 512:
                fixed = np.zeros(512, dtype=np.uint8)
                n = min(arr.size, 512)
                if n > 0:
                    fixed[:n] = arr[:n]
                arr = fixed
            snapshot_cache[uni] = arr
            return arr

        for fi_id in fixture_ids:
            cache = self._addr_cache.get(fi_id, {}) or self._addr_cache.get(str(fi_id), {}) or {}
            addrs = cache.get(rule.ch_type, [])
            if not addrs:
                continue
            uni, addr = addrs[0]
            uni = int(uni)
            addr = int(addr)
            rule_type = str(rule.ch_type)
            coarse_type = MOTOR_FINE_TO_COARSE.get(rule_type, rule_type)
            fine_addr = _fine_addr_for_fixture(str(fi_id), coarse_type, uni)

            val = None
            try:
                if rule.preset_id == "selfmap_joystick_map" and hasattr(self._nm, "_layers"):
                    layer_entry = self._nm._layers.get("joystick", {}).get(uni)
                    if layer_entry is not None:
                        layer_data, layer_active = layer_entry
                        fine_active = (
                            fine_addr is not None
                            and 0 <= int(fine_addr) < len(layer_active)
                            and bool(layer_active[int(fine_addr)])
                        )
                        if 0 <= addr < len(layer_data) and (bool(layer_active[addr]) or fine_active):
                            if rule_type in MOTOR_FINE_CHANNELS:
                                merged = _snapshot_for_universe(uni).copy()
                                if bool(layer_active[addr]):
                                    merged[addr] = layer_data[addr]
                                if fine_active:
                                    merged[int(fine_addr)] = layer_data[int(fine_addr)]
                                val = read_axis_logical_value(merged, addr, fine_addr)
                            else:
                                val = float(layer_data[addr])
            except Exception:
                val = None
            if val is None:
                uni_data = _snapshot_for_universe(uni)
                if rule_type in MOTOR_FINE_CHANNELS:
                    val = read_axis_logical_value(uni_data, addr, fine_addr)
                else:
                    val = float(uni_data[addr])
            current_values.append(val)
            addr_items.append((str(fi_id), uni, addr, fine_addr, rule_type))

        if not current_values:
            return

        values = np.array(current_values, dtype=np.float64)
        indices = np.arange(len(values), dtype=np.float64)

        params = dict(rule.params)
        params["_time"] = t

        result = preset_def.fn(values, indices, params)
        result = np.clip(result, 0.0, 255.0)

        for idx, (_fi_id, uni, addr, fine_addr, rule_type) in enumerate(addr_items):
            if idx < len(result):
                if uni not in writes:
                    writes[uni] = {}
                out_val = float(np.clip(result[idx], 0.0, 255.0))
                if rule_type in MOTOR_FINE_CHANNELS:
                    coarse, fine = logical_to_16bit_pair(out_val)
                    writes[uni][addr] = coarse
                    if fine_addr is not None and 0 <= int(fine_addr) < 512:
                        writes[uni][int(fine_addr)] = fine
                else:
                    writes[uni][addr] = int(out_val)

    # ── 序列化 ───────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "enabled": self._enabled,
            "rules": [r.to_dict() for r in self._rules],
            "cue_library": [c.to_dict() for c in self._cue_library],
        }

    def from_dict(self, d: dict):
        self._enabled = d.get("enabled", False)
        self._rules = [SelfMapRule.from_dict(r) for r in d.get("rules", [])]
        self._cue_library = [
            GeneralizedCue.from_dict(c) for c in d.get("cue_library", [])
        ]
        if self._enabled:
            self._timer.start()
        else:
            self._timer.stop()
        self.rules_changed.emit()


# ── 单例 ─────────────────────────────────────────────────────────────────────

_selfmap_engine_instance: Optional[SelfMapEngine] = None


def get_selfmap_engine() -> SelfMapEngine:
    global _selfmap_engine_instance
    if _selfmap_engine_instance is None:
        _selfmap_engine_instance = SelfMapEngine()
    return _selfmap_engine_instance
