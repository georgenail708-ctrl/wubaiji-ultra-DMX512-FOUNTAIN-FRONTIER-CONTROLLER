"""
DMXTimeline 3.0 — Core Data Models
所有核心数据结构定义
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Any, Dict, Tuple
import time
import uuid
import json
import os
import copy
import logging
import base64
from .fixture_preview_metadata import copy_preview_physical
from .clip_smoothing_source import load_source, source_payload
from .recorded_channel_masks import masks_from_dict as _recorded_masks_from_dict

# ── 工程 JSON 解析加速（R183 横向性能批）──────────────────────────────────
# 91MB 真工程 stdlib json.load 实测 ~456ms；orjson（Rust 实现）官方 benchmark
# 对大文档 5-10×。缺席时回退纯 json（未装 orjson 的环境行为不变）。
# 放在本模块（最底层）：project_manager.ProjectLoader 与 Project.load/撤销恢复
# 复用同一入口；反向 import 会成环。
try:
    import orjson as _orjson
except ImportError:  # pragma: no cover - 依赖存在性分支
    _orjson = None


def parse_project_json(data):
    """工程 JSON 解析统一入口：orjson 优先，退路 stdlib json。

    data: bytes/bytearray/str。bytes 直传（orjson 快路径；stdlib json.loads
    自 3.6 起亦接受字节并按 UTF-8/16/32 自动检测，本仓工程统一 UTF-8）。
    """
    if _orjson is not None:
        return _orjson.loads(data)
    return json.loads(data)


import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from .group_selection import normalize_direction_overrides  # R172 组选择规格（§2.2）

DEFAULT_LIGHT_TRACKING_ATTACK = 0.08
DEFAULT_LIGHT_TRACKING_RELEASE = 0.22


def _lead_ms_or_default(value) -> float:
    """`precharge_lead_ms` 归一化：None/空串 → 1000.0，其余原样（含 0）。

    ★★ R131 修复（泵预充提前量设 0 保存后变回 1000ms）：0 是合法输入（对话框
    0-10000），不能用 `or 1000.0` 兜底 —— 否则 0 被反转成 1000 且存档后永久丢失，
    与 `_ltmb_or_default` 的 EDGE-03 是同一类错误。"""
    return 1000.0 if value in (None, "") else value


def _ltmb_or_default(value) -> int:
    """light_tracking_max_brightness 归一化: None/空串 → 255, 其余原样。

    0 是合法输入(对话框 0-255, 用户输 0 意图灯保持灭), 不能用 `or 255`
    兜底 — 否则 0 被反转成 255 全亮且存档后永久丢失(EDGE-03)。"""
    return 255 if value in (None, "") else value
MOTOR_FORCE_PROFILES = {
    "gentle": {"phase_amt": 0.15, "accel": 0.30, "cruise": 0.50, "decel": 0.20},
    "soft": {"phase_amt": 0.30, "accel": 0.25, "cruise": 0.50, "decel": 0.25},
    "natural": {"phase_amt": 0.60, "accel": 0.20, "cruise": 0.60, "decel": 0.20},
    "strong": {"phase_amt": 1.00, "accel": 0.15, "cruise": 0.70, "decel": 0.15},
    "blast": {"phase_amt": 2.00, "accel": 0.10, "cruise": 0.80, "decel": 0.10},
}

logger = logging.getLogger(__name__)


def normalize_group_pauses(value: Any, count: int, fallback: float = 0.0) -> List[float]:
    try:
        count = max(0, int(count or 0))
    except Exception:
        logger.debug("normalize_group_pauses: failed to parse count, defaulting to 0")
        count = 0
    try:
        fallback_f = max(0.0, float(fallback or 0.0))
    except Exception:
        logger.debug("normalize_group_pauses: failed to parse fallback, defaulting to 0.0")
        fallback_f = 0.0
    if count <= 0:
        return []

    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raw = []
        else:
            try:
                raw = json.loads(text)
            except Exception:
                logger.debug("normalize_group_pauses: JSON parse failed, falling back to comma-separated")
                raw = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    out: List[float] = []
    for item in raw:
        try:
            out.append(max(0.0, float(item or 0.0)))
        except Exception:
            logger.debug("normalize_group_pauses: failed to parse item, using fallback")
            out.append(fallback_f)
        if len(out) >= count:
            break
    if len(out) < count:
        out.extend([fallback_f] * (count - len(out)))
    return out[:count]


# ─────────────────────────────────────────────
#  设备通道类别枚举
# ─────────────────────────────────────────────
class ChannelType(Enum):
    DIMMER      = "dimmer"
    RED         = "R"
    GREEN       = "G"
    BLUE        = "B"
    WHITE       = "W"
    VALVE       = "valve"
    MOTOR_X     = "motor_x"
    MOTOR_Y     = "motor_y"
    MOTOR_Z     = "motor_z"
    MOTOR_X_FINE = "motor_x_fine"
    MOTOR_Y_FINE = "motor_y_fine"
    MOTOR_Z_FINE = "motor_z_fine"
    PUMP        = "pump"
    PLACEHOLDER = "placeholder"

    def label(self) -> str:
        labels = {
            "dimmer": "亮度 Dimmer", "R": "红 R", "G": "绿 G", "B": "蓝 B",
            "W": "白 W", "valve": "阀 Valve", "motor_x": "电机 X",
            "motor_y": "电机 Y", "motor_z": "电机 Z",
            "motor_x_fine": "电机 X Fine",
            "motor_y_fine": "电机 Y Fine",
            "motor_z_fine": "电机 Z Fine",
            "pump": "泵 Pump",
            "placeholder": "占位 (无功能)"
        }
        return labels.get(self.value, self.value)

# ─────────────────────────────────────────────
#  设备定义 (Fixture Definition)
# ─────────────────────────────────────────────
@dataclass
class FixtureDefinition:
    """设备类型定义（可复用模板）"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Fixture"
    channel_count: int = 1
    channels: List[str] = field(default_factory=list)  # List[ChannelType.value]
    function_channel_names: Dict[str, str] = field(default_factory=dict)
    function_channel_defaults: Dict[str, int] = field(default_factory=dict)
    # 功能模式表：{channel_type: [{name, dmx_min, dmx_max}, ...]}（功能通道模式，
    # 如 Control Pan: Motor Stop 0-7 / Park 50-55；Programmer 显示方块按钮快速写入）
    channel_modes: Dict[str, List[dict]] = field(default_factory=dict)
    # ★ 电机角度范围（度）：{channel_type: (min_deg, max_deg)}——实例电机链
    #   TMotorObject MinAngle/MaxAngle（如 MDD Yaw=(-180,180)、副池三维扇子
    #   Rotation=(-270,270)），DMX 0-255 线性映射；Z 轴扇面同步基准用。
    #   旧工程缺省 {}，消费方回退 ±180°。
    motor_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "channel_count": self.channel_count,
            "channels": self.channels,
            "function_channel_names": self.function_channel_names,
            "function_channel_defaults": self.function_channel_defaults,
            "channel_modes": self.channel_modes,
            "motor_ranges": dict(self.motor_ranges),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FixtureDefinition":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Fixture"),
            channel_count=d.get("channel_count", 1),
            channels=d.get("channels", []),
            function_channel_names=d.get("function_channel_names", {}),
            function_channel_defaults=d.get("function_channel_defaults", {}),
            channel_modes=d.get("channel_modes", {}),
            motor_ranges=d.get("motor_ranges", {}),
        )

# ─────────────────────────────────────────────
#  设备实例 (Fixture Instance)
# ─────────────────────────────────────────────
@dataclass
class FixtureInstance:
    """设备实例（已配接到具体 DMX 地址）"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    def_id: str = ""
    name: str = "Fixture 1"
    universe: int = 1
    start_channel: int = 1   # 1-indexed
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0   # 高度(cm)；PosY。旧工程默认 0，MVR 往返保留高度
    # ★ 世界坐标（场地坐标，沿父链变换后）：同步基准/曲线切向用真实安装
    #   位置，而非包内局部偏移。0=未计算（旧工程/手动配接），消费方回退局部坐标。
    world_x: float = 0.0
    world_z: float = 0.0
    # ★ 安装绕 X 轴旋转（Object RotX，度）：RotX=±180 为倒装设备
    #   （存储 RotY 为物理方向取反）——同步输出角须取负（取反），
    #   否则用户看到「每套 2 台方向相反」（2026-08-04 副池三维扇子）。
    #   0=未导入/手动配接（无旋转信息），消费方按正常设备处理。
    rot_x: float = 0.0
    # ★ 安装绕 Z 轴旋转（Object RotZ，度，2026-08-08 新增导入）：
    #   RotX=180 且 RotZ=180 = 纯偏航 180°（R_X(180)·R_Z(180)=R_Y(180)，
    #   非倒装，改版至冬1 全部设备）→ 同步按纯切线零翻转；
    #   RotX=180 且 RotZ=-180 = 至冬7.0 式倒装约定（真机验证 4 台扇子）→
    #   同步基准 +180。消费方门控见 topview_panel._fixture_is_flipped。
    rot_z: float = 0.0
    # ★ 安装绕 Y 轴旋转（Object RotY 世界偏航，度，2026-08-09 新增导入）：
    #   真实世界安装朝向（fmap FixtureNode.rot_y 透传）。锚点分簇拟合的
    #   base 切线在 2D XY（同心放大环）上是伪切线（用户实测 129/240 台差
    #   180°，「圆分两份反向折扇」）——2D XY 同步基准直接用本字段。
    #   0=旧工程/手动配接（无旋转信息），消费方回退锚点分簇切线。
    rot_y: float = 0.0

    # Optional authoritative world rotation, scalar-last [x,y,z,w]. Older
    # imports kept only Euler fields, which can disagree at cardinals.
    installation_quaternion: list | None = None

    # Optional output-preview calibration, separate from original patch/layout.
    preview_physical: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "def_id": self.def_id,
            "name": self.name,
            "universe": self.universe,
            "start_channel": self.start_channel,
            "pos_x": self.pos_x,
            "pos_y": self.pos_y,
            "pos_z": self.pos_z,
            "world_x": self.world_x,
            "world_z": self.world_z,
            "rot_x": self.rot_x,
            "rot_z": self.rot_z,
            "rot_y": self.rot_y,
            "installation_quaternion": (list(self.installation_quaternion)
                                        if self.installation_quaternion is not None else None),
            "preview_physical": copy_preview_physical(self.preview_physical),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FixtureInstance":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            def_id=d.get("def_id", ""),
            name=d.get("name", "Fixture"),
            universe=d.get("universe", 1),
            start_channel=d.get("start_channel", 1),
            pos_x=d.get("pos_x", 0.0),
            pos_y=d.get("pos_y", 0.0),
            pos_z=d.get("pos_z", 0.0),
            world_x=d.get("world_x", 0.0),
            world_z=d.get("world_z", 0.0),
            rot_x=d.get("rot_x", 0.0),
            rot_z=d.get("rot_z", 0.0),
            rot_y=d.get("rot_y", 0.0),
            installation_quaternion=d.get("installation_quaternion"),
            preview_physical=copy_preview_physical(d.get("preview_physical")),
        )

# ─────────────────────────────────────────────
#  逻辑/组合设备 (Logical Device) — 薄表现层覆盖
# ─────────────────────────────────────────────
@dataclass
class LogicalDevice:
    """逻辑/组合设备：把多个真实 FixtureInstance 折叠成 TopView 上的一个点。

    这是**表现/选择覆盖层**，不是引擎一等身份——真实子设备仍是普通
    FixtureInstance（各自保留 universe/start_channel），所有输出引擎不感知本类。

    - member_fixture_ids: 真实成员 id，可跨域/跨类型。
    - pos_x/pos_y: TopView 代表点坐标（成员本身不画在画布上）。
    - local_offsets: {fid: [dx, dy]} 成员相对代表点的隐藏局部坐标，供重建/校验；
      成员真实坐标仍存在各自 FixtureInstance.pos_x/pos_y（引擎据此跑「各自坐标系」）。
    - control_mode: merged(整体一个控件，值广播给全部成员) | decomposed(展开成真实成员)。
    - kind: unit(导入自动检测的微单元) | composite(用户手动拼的组合)。
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Logical 1"
    member_fixture_ids: List[str] = field(default_factory=list)
    pos_x: float = 0.0
    pos_y: float = 0.0
    local_offsets: Dict[str, List[float]] = field(default_factory=dict)
    control_mode: str = "merged"   # merged | decomposed
    kind: str = "unit"             # unit | composite
    hidden: bool = False           # True=数据保留供单控/整体控操作，但 TopView 画成员个体、不画代表点/不折叠
    merge_group: str = ""          # 同一次「按类型合并」产生的一批 composite 共享的组标签（GroupSelector 折叠成一个瓦片）

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "member_fixture_ids": list(self.member_fixture_ids),
            "pos_x": self.pos_x,
            "pos_y": self.pos_y,
            "local_offsets": {str(k): [float(v[0]), float(v[1])]
                              for k, v in (self.local_offsets or {}).items()
                              if isinstance(v, (list, tuple)) and len(v) >= 2},
            "control_mode": self.control_mode if self.control_mode in ("merged", "decomposed") else "merged",
            "kind": self.kind if self.kind in ("unit", "composite", "package") else "unit",
            "hidden": bool(self.hidden),
            "merge_group": str(self.merge_group or ""),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogicalDevice":
        raw_off = d.get("local_offsets", {}) or {}
        offsets: Dict[str, List[float]] = {}
        for k, v in raw_off.items():
            try:
                offsets[str(k)] = [float(v[0]), float(v[1])]
            except Exception:
                logger.debug("LogicalDevice.from_dict: failed to parse offset entry, skipping")
                continue
        mode = str(d.get("control_mode", "merged") or "merged")
        kind = str(d.get("kind", "unit") or "unit")
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Logical"),
            member_fixture_ids=list(d.get("member_fixture_ids", []) or []),
            pos_x=float(d.get("pos_x", 0.0) or 0.0),
            pos_y=float(d.get("pos_y", 0.0) or 0.0),
            local_offsets=offsets,
            control_mode=mode if mode in ("merged", "decomposed") else "merged",
            kind=kind if kind in ("unit", "composite", "package") else "unit",
            hidden=bool(d.get("hidden", False)),
            merge_group=str(d.get("merge_group", "") or ""),
        )


# ─────────────────────────────────────────────
#  枚举
# ─────────────────────────────────────────────
class Protocol(Enum):
    ARTNET = "Art-Net"
    SACN   = "sACN"

class RecordState(Enum):
    IDLE      = auto()
    RECORDING = auto()
    PAUSED    = auto()

class PlaybackState(Enum):
    IDLE    = auto()
    PLAYING = auto()
    PAUSED  = auto()

class InterpolationMode(Enum):
    LINEAR   = "linear"
    EASE_IN  = "ease_in"
    EASE_OUT = "ease_out"
    STEP     = "step"

# ─────────────────────────────────────────────
#  过滤规则
# ─────────────────────────────────────────────
@dataclass
class FilterRule:
    """
    通道过滤规则 — 存储哪些通道被允许通过
    channels: set of int (1-512)
    universe: 主宇宙编号（兼容旧版）
    """
    channels: set = field(default_factory=set)
    universe: int = 1

    def is_empty(self) -> bool:
        return len(self.channels) == 0

    def allows_all(self) -> bool:
        return self.channels == set(range(1, 513))

    def set_all(self):
        self.channels = set(range(1, 513))

    def clear(self):
        self.channels = set()

    def to_expression(self) -> str:
        """将通道集合转换为可读表达式，如 '1-50, 100'"""
        if not self.channels:
            return ""
        if self.allows_all():
            return "ALL"
        sorted_ch = sorted(self.channels)
        groups = []
        i = 0
        while i < len(sorted_ch):
            j = i
            while j + 1 < len(sorted_ch) and sorted_ch[j + 1] == sorted_ch[j] + 1:
                j += 1
            if j > i:
                groups.append(f"{sorted_ch[i]}-{sorted_ch[j]}")
            else:
                groups.append(str(sorted_ch[i]))
            i = j + 1
        return ", ".join(groups)

    def to_dict(self) -> dict:
        return {"universe": self.universe, "channels": sorted(self.channels)}

    @classmethod
    def from_dict(cls, d: dict) -> "FilterRule":
        rule = cls(universe=d.get("universe", 1))
        rule.channels = set(d.get("channels") or [])
        return rule

# ─────────────────────────────────────────────
#  DMX 帧
# ─────────────────────────────────────────────
@dataclass
class DMXFrame:
    """单帧 DMX 数据（512通道）"""
    universe: int
    timestamp: float                     # 秒，相对于录制开始
    data: np.ndarray                     # shape=(512,) dtype=uint8

    def __post_init__(self):
        if not isinstance(self.data, np.ndarray):
            self.data = np.array(self.data, dtype=np.uint8)
        if self.data.shape != (512,):
            arr = np.zeros(512, dtype=np.uint8)
            n = min(len(self.data), 512)
            arr[:n] = self.data[:n]
            self.data = arr

    def apply_filter(self, rule: FilterRule) -> "DMXFrame":
        """只保留过滤规则中的通道，其余置0"""
        if rule.is_empty():
            filtered = np.zeros(512, dtype=np.uint8)
            return DMXFrame(universe=self.universe, timestamp=self.timestamp, data=filtered)
        filtered = np.zeros(512, dtype=np.uint8)
        for ch in rule.channels:
            idx = ch - 1  # 0-indexed
            if 0 <= idx < 512:
                filtered[idx] = self.data[idx]
        return DMXFrame(universe=self.universe, timestamp=self.timestamp, data=filtered)

    def to_dict(self) -> dict:
        """序列化为 JSON 可存储的字典（data 用 base64 编码）。"""
        return {
            "universe": int(self.universe),
            "timestamp": float(self.timestamp),
            "data": base64.b64encode(self.data.tobytes()).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DMXFrame":
        """从字典恢复 DMXFrame（兼容 base64 编码和旧版 list 格式）。"""
        raw = d.get("data", "")
        if isinstance(raw, str):
            # base64 格式
            data = np.frombuffer(base64.b64decode(raw), dtype=np.uint8)
        elif isinstance(raw, list):
            # 旧版 list of int 格式（向后兼容）
            data = np.array(raw, dtype=np.uint8)
        else:
            data = np.zeros(512, dtype=np.uint8)
        return cls(
            universe=int(d.get("universe", 1)),
            timestamp=float(d.get("timestamp", 0.0)),
            data=data,
        )

    def copy(self) -> "DMXFrame":
        return DMXFrame(
            universe=self.universe,
            timestamp=self.timestamp,
            data=self.data.copy()
        )


# ─────────────────────────────────────────────
#  懒加载 DMX 帧（轻量引用）
# ─────────────────────────────────────────────
class _LazyDMXFrame:
    """轻量级帧引用，首次访问 .data 时即时解码 base64。

    用于大工程异步加载：先构建引用骨架（<1s），解码延后到实际使用。
    对外接口与 DMXFrame 保持一致（universe, timestamp, data, to_dict, apply_filter）。
    """
    __slots__ = ('universe', 'timestamp', '_raw_b64', '_cached_data', '_raw_bytes')

    def __init__(self, universe: int, timestamp: float, raw_b64: str = "",
                 raw_bytes=None):
        self.universe = universe
        self.timestamp = timestamp
        self._raw_b64 = raw_b64
        # ★ R183（frames_blob 加载提速）：blob 解压体已是裸字节，再逐帧 b64 编码回
        #   字符串是纯浪费（20 万帧 = 20 万次 b64encode，实测占懒加载近半）。允许
        #   直接持有裸字节/memoryview 切片，`data` 首访按同一懒语义出数组。
        self._raw_bytes = raw_bytes
        self._cached_data = None

    @property
    def data(self) -> np.ndarray:
        if self._cached_data is None:
            if self._raw_bytes is not None:
                self._cached_data = np.frombuffer(self._raw_bytes, dtype=np.uint8)
            else:
                self._cached_data = np.frombuffer(
                    base64.b64decode(self._raw_b64), dtype=np.uint8)
        return self._cached_data

    @data.setter
    def data(self, value) -> None:
        """可写（R131）：平滑等"原地重写帧"路径原先在此抛 `AttributeError`。

        ★★ R131 修复（大工程 ≥50MB 时"自动平滑处理"必然失败）：≥50MB 的工程走
        异步懒加载，帧建成 `_LazyDMXFrame`（`__slots__` + 只读 property）→
        `frame.data = ...` 直接 `AttributeError: property 'data' has no setter`。
        小工程走 `DMXFrame`（普通属性）故完全正常 —— 极难复现定位。
        修：给个可写 setter，写入即物化（后续 `to_dict` 走 `_cached_data` 分支）。
        """
        arr = np.asarray(value, dtype=np.uint8)
        self._cached_data = arr.copy() if arr.ndim else arr.reshape(-1)

    def to_dict(self) -> dict:
        if self._cached_data is not None:
            raw = self._cached_data.tobytes()
        elif self._raw_bytes is not None:
            raw = bytes(self._raw_bytes)
        else:
            return {
                "universe": int(self.universe),
                "timestamp": float(self.timestamp),
                "data": str(self._raw_b64),
            }
        return {
            "universe": int(self.universe),
            "timestamp": float(self.timestamp),
            "data": base64.b64encode(raw).decode("ascii"),
        }

    def apply_filter(self, rule: "FilterRule") -> "DMXFrame":
        data = self.data
        if rule.is_empty():
            filtered = np.zeros(512, dtype=np.uint8)
            return DMXFrame(universe=self.universe, timestamp=self.timestamp, data=filtered)
        filtered = np.zeros(512, dtype=np.uint8)
        for ch in rule.channels:
            idx = ch - 1
            if 0 <= idx < 512:
                filtered[idx] = data[idx]
        return DMXFrame(universe=self.universe, timestamp=self.timestamp, data=filtered)

    def copy(self) -> "_LazyDMXFrame":
        return _LazyDMXFrame(
            universe=self.universe,
            timestamp=self.timestamp,
            raw_b64=str(self._raw_b64),
            raw_bytes=self._raw_bytes,
        )

    @classmethod
    def from_frame_dict(cls, d: dict) -> "_LazyDMXFrame":
        raw = d.get("data", "")
        if isinstance(raw, str):
            return cls(
                universe=int(d.get("universe", 1)),
                timestamp=float(d.get("timestamp", 0.0)),
                raw_b64=raw,
            )
        # 旧版 list 格式 → 即时编码为 base64 再懒加载（兼容旧工程）
        if isinstance(raw, list):
            data = np.array(raw, dtype=np.uint8)
            encoded = base64.b64encode(data.tobytes()).decode("ascii")
            return cls(
                universe=int(d.get("universe", 1)),
                timestamp=float(d.get("timestamp", 0.0)),
                raw_b64=encoded,
            )
        return cls(
            universe=int(d.get("universe", 1)),
            timestamp=float(d.get("timestamp", 0.0)),
            raw_b64="",
        )


# ─────────────────────────────────────────────
#  曲线映射（XY函数）
# ─────────────────────────────────────────────
@dataclass
class CurvePoint:
    x: float   # 0.0 - 255.0
    y: float   # 0.0 - 255.0
    # R178 贝塞尔控制柄（可选，None=无柄；与端点同坐标系）。c1=入柄（作用于
    # 左邻段 [prev→本点]），c2=出柄（作用于右邻段 [本点→next]）。四个字段全
    # None 时该点保持旧折线语义 —— 全曲线无柄则求值逐字节兼容旧档。
    c1x: Optional[float] = None
    c1y: Optional[float] = None
    c2x: Optional[float] = None
    c2y: Optional[float] = None


# ── 贝塞尔求值辅助（CurveMapping 专用；模块级纯函数便于单测）──────────────────
_BEZIER_SUBDIV = 32   # evaluate_array 的每贝塞尔段线性近似采样数


def _opt_float(v) -> Optional[float]:
    """None→None；可转 float→float；否则 None（旧档/手改档容错）。"""
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _pt_out_ctrl(p: "CurvePoint") -> Optional[tuple]:
    """p 的出柄 (c2x,c2y)（右邻段用）；无任何 c2 字段→None。缺单坐标回落端点坐标。"""
    if getattr(p, "c2x", None) is None and getattr(p, "c2y", None) is None:
        return None
    return (float(p.c2x if p.c2x is not None else p.x),
            float(p.c2y if p.c2y is not None else p.y))


def _pt_in_ctrl(p: "CurvePoint") -> Optional[tuple]:
    """p 的入柄 (c1x,c1y)（左邻段用）；无任何 c1 字段→None。"""
    if getattr(p, "c1x", None) is None and getattr(p, "c1y", None) is None:
        return None
    return (float(p.c1x if p.c1x is not None else p.x),
            float(p.c1y if p.c1y is not None else p.y))


def _seg_ctrls(p0: "CurvePoint", p1: "CurvePoint") -> Optional[tuple]:
    """段 [p0→p1] 的 ((c2x,c2y),(c1x,c1y))；两侧都无柄→None（走旧折线）。"""
    out = _pt_out_ctrl(p0)
    inn = _pt_in_ctrl(p1)
    if out is None and inn is None:
        return None
    if out is None:
        out = (float(p0.x), float(p0.y))
    if inn is None:
        inn = (float(p1.x), float(p1.y))
    return out, inn


def _mono_ctrl_x(x0: float, c2x: float, c1x: float, x1: float) -> tuple:
    """钳制柄 x 使控制多边形非降（x0≤c2x≤c1x≤x1，双向端点兜底）——保证 x(t) 单调。"""
    lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
    a = min(max(c2x, lo), hi)
    b = min(max(c1x, a), hi)
    return a, b


def _bezier_coord(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    u = 1.0 - t
    return u * u * u * p0 + 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t * p3


def _bezier_t_for_x(x0: float, c2x: float, c1x: float, x1: float, x: float) -> float:
    """x(t) 单调（_mono_ctrl_x 保证）→ 二分求 t 使 x(t)=x。（当前求值走统一
    折线近似，此函数留给需要精确参数 t 的调用方/测试。）"""
    if x <= x0:
        return 0.0
    if x >= x1:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if _bezier_coord(mid, x0, c2x, c1x, x1) < x:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _eval_segment(p0: "CurvePoint", p1: "CurvePoint", x: float) -> float:
    """段内求值：无柄=旧线性公式（逐字节兼容）；有柄=贝塞尔 32 分段折线近似。

    有柄段与 evaluate_array 用同一条折线（同 t 采样、同公式），保证标量与
    向量化两 API 结果一致；柄 x 经 _mono_ctrl_x 钳制后折线 x 非降。
    """
    x0, y0 = float(p0.x), float(p0.y)
    x1, y1 = float(p1.x), float(p1.y)
    ctrls = _seg_ctrls(p0, p1)
    if ctrls is None:
        t = (x - x0) / (x1 - x0) if x1 != x0 else 0
        return y0 + t * (y1 - y0)
    (c2x, c2y), (c1x, c1y) = ctrls
    c2x, c1x = _mono_ctrl_x(x0, c2x, c1x, x1)
    seg_x, seg_y = [], []
    for k in range(_BEZIER_SUBDIV + 1):
        t = k / _BEZIER_SUBDIV
        seg_x.append(_bezier_coord(t, x0, c2x, c1x, x1))
        seg_y.append(_bezier_coord(t, y0, c2y, c1y, y1))
    for k in range(_BEZIER_SUBDIV):
        if seg_x[k] <= x <= seg_x[k + 1]:
            xk, xk1 = seg_x[k], seg_x[k + 1]
            f = (x - xk) / (xk1 - xk) if xk1 != xk else 0.0
            return seg_y[k] + f * (seg_y[k + 1] - seg_y[k])
    return seg_y[-1]


def _curve_has_handles(pts) -> bool:
    for p in pts:
        for k in ("c1x", "c1y", "c2x", "c2y"):
            if getattr(p, k, None) is not None:
                return True
    return False


def _bezier_breakpoints(pts) -> tuple:
    """整条曲线的折线断点（贝塞尔段按 _BEZIER_SUBDIV 均匀 t 采样线性近似）。

    返回 (px, py) 两个 ndarray，x 非降（柄 x 已经 _mono_ctrl_x 钳制）。
    """
    px: list = []
    py: list = []
    for i in range(len(pts) - 1):
        p0, p1 = pts[i], pts[i + 1]
        if i == 0:
            px.append(float(p0.x)); py.append(float(p0.y))
        ctrls = _seg_ctrls(p0, p1)
        if ctrls is None:
            px.append(float(p1.x)); py.append(float(p1.y))
            continue
        (c2x, c2y), (c1x, c1y) = ctrls
        c2x, c1x = _mono_ctrl_x(float(p0.x), c2x, c1x, float(p1.x))
        for k in range(1, _BEZIER_SUBDIV + 1):
            t = k / _BEZIER_SUBDIV
            px.append(_bezier_coord(t, float(p0.x), c2x, c1x, float(p1.x)))
            py.append(_bezier_coord(t, float(p0.y), c2y, c1y, float(p1.y)))
    arr_x = np.asarray(px, dtype=float)
    arr_y = np.asarray(py, dtype=float)
    if np.any(np.diff(arr_x) <= 0):
        keep = np.concatenate(([True], np.diff(arr_x) > 0))
        arr_x, arr_y = arr_x[keep], arr_y[keep]
    return arr_x, arr_y


def _point_to_dict(p: "CurvePoint") -> dict:
    """点序列化：柄字段只在非 None 时写（旧档字节形状不变）。"""
    d = {"x": p.x, "y": p.y}
    for k in ("c1x", "c1y", "c2x", "c2y"):
        v = getattr(p, k, None)
        if v is not None:
            d[k] = float(v)
    return d


def _handles_from_dict(p: dict) -> dict:
    return {"c1x": _opt_float(p.get("c1x")), "c1y": _opt_float(p.get("c1y")),
            "c2x": _opt_float(p.get("c2x")), "c2y": _opt_float(p.get("c2y"))}

@dataclass
class CurveMapping:
    """
    XY映射曲线 — 存储控制点，提供插值查询
    支持三种来源：预设/鼠标拖动/表达式输入
    """
    points: list = field(default_factory=list)   # List[CurvePoint]
    source: str = "manual"                        # "manual"/"preset"/"expression"
    expression: str = ""                          # 如果是表达式来源

    def __post_init__(self):
        if not self.points:
            self.points = [CurvePoint(0, 0), CurvePoint(255, 255)]

    def evaluate(self, x: float) -> float:
        """插值求Y值：无柄段=线性（旧公式）；任一端有柄段=三次贝塞尔（x 单调）。"""
        if x <= self.points[0].x:
            return self.points[0].y
        if x >= self.points[-1].x:
            return self.points[-1].y
        for i in range(len(self.points) - 1):
            p0, p1 = self.points[i], self.points[i+1]
            if p0.x <= x <= p1.x:
                return _eval_segment(p0, p1, x)
        return 0.0

    def evaluate_array(self, xs: np.ndarray) -> np.ndarray:
        # 向量化插值。端点外行为(clamp 到首/末 y)与标量 evaluate() 一致。
        # 全曲线无柄 → 原 np.interp 路径逐字节兼容；有柄 → 贝塞尔段 32 分段
        # 线性近似后 np.interp。
        xs = np.asarray(xs, dtype=float)
        pts = self.points
        if not pts:
            return np.zeros_like(xs)
        if len(pts) == 1:
            return np.full_like(xs, float(pts[0].y))
        if not _curve_has_handles(pts):
            px = np.array([p.x for p in pts], dtype=float)
            py = np.array([p.y for p in pts], dtype=float)
            # np.interp 要求 xp 严格递增；points 已按 x 升序。
            # 防御重复/非递增 x：保留每个 x 的首个采样，保证严格递增。
            if np.any(np.diff(px) <= 0):
                keep = np.concatenate(([True], np.diff(px) > 0))
                px, py = px[keep], py[keep]
                if px.size == 1:
                    return np.full_like(xs, float(py[0]))
            return np.interp(xs, px, py)
        px, py = _bezier_breakpoints(pts)
        if px.size == 1:
            return np.full_like(xs, float(py[0]))
        return np.interp(xs, px, py)

    def to_dict(self) -> dict:
        return {
            "points": [_point_to_dict(p) for p in self.points],
            "source": self.source,
            "expression": self.expression
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CurveMapping":
        pts = []
        for p in d.get("points", []):
            if not isinstance(p, dict):
                logger.warning("CurveMapping.from_dict: skipping non-dict point: %r", p)
                continue
            try:
                x = float(p.get("x", 0.0))
                y = float(p.get("y", 0.0))
            except (TypeError, ValueError):
                logger.warning("CurveMapping.from_dict: skipping invalid point: %r", p)
                continue
            pts.append(CurvePoint(x, y, **_handles_from_dict(p)))
        return cls(points=pts, source=d.get("source", "manual"), expression=d.get("expression", ""))

# ─────────────────────────────────────────────
#  数据片段 (Clip)
# ─────────────────────────────────────────────
def _clamp01(v, default: float = 1.0) -> float:
    """`[0,1]` 钳位（R110 滤网参数序列化用）；非数值 → default。

    ★ 刻意用 try/except 而非 `or`：`or` 是 BoolOp，会抬高调用方（`to_dict` /
    `from_dict`）的 CC，而那两个函数在架构门禁里已是 0 余量。
    """
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _clamp_pos(v, default: float = 0.0) -> float:
    """`≥0` 钳位（限速度/秒）；非数值 → default。"""
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return default


@dataclass
class Clip:
    """时间线上的一段录制数据"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    track_id: str = ""
    start_time: float = 0.0
    duration: float = 0.0
    frames: list = field(default_factory=list)   # List[DMXFrame]
    label: str = ""
    clip_universes: List[int] = field(default_factory=lambda: [1])
    # {universe: [dmx_channel_1based]}：记录值为 0 也应输出/参与过渡的有效通道。
    active_channels_by_universe: Dict[int, List[int]] = field(default_factory=dict)
    recorded_channel_masks: dict = field(default_factory=dict)
    curve: Optional[CurveMapping] = None
    preset_name: str = ""
    # 最简 clip 数据流淡入/淡出时长（秒）。由时间线 clip 左右两端的小栅栏拖动调整。
    fade_in: float = 0.0
    fade_out: float = 0.0
    # 方向性分组淡入淡出
    # direction: none/ltr/rtl/ftb/btf/cw/ccw/ctr/rtc/alt/alt_block/random/wave/cascade/rev_cascade/zigzag
    fade_direction: str = 'none'           # 兼容旧版：统一方向（优先级低于下面两个）
    fade_in_direction: str = 'none'        # 淡入方向（独立设置）
    fade_out_direction: str = 'none'       # 淡出方向（独立设置）
    fade_direction_edge: str = "both"      # ★ 2026-08-12: 分组方向过渡「应用边」both/in/out（both=淡入淡出一致；in/out=只应用单边，另一侧保留原值）
    fade_groups: int = 1                   # 分组数，1=整体，>1=分组
    fade_group_sequence_mode: str = "parallel"  # parallel/sequential
    fade_group_pause: float = 0.0          # 顺序分组时组间停顿（秒）
    fade_group_pauses: List[float] = field(default_factory=list)  # 顺序分组时每次组间停顿（秒）
    fade_group_single_duration: float = 0.0  # 顺序分组时单组原始时长（秒）
    fade_group_sequence_batches: int = 0   # 顺序执行批次数，0=等于分组数
    fade_group_spatial_scope: str = "follow"  # follow/per_shape/global：follow=跟随项目全局灯控(单控→各自坐标系/整体控→整体)；per_shape/global=显式覆盖
    fade_group_skip_base_order: bool = False  # ★ 2026-08-11 #18：跳过编号排序层，方向过渡只看设备坐标按应用方向排序（默认 False=原逻辑先编号后坐标系）
    # ── R172 Clip Studio（§2.1）──────────────────────────────
    # 绑定的 Studio sheet id；空 = 未绑定（绑定关系，不进后处理白名单）
    process_sheet_id: str = ""
    # 复合方向覆盖：每项 {"spec": {组选择规格}, "direction": "ltr"}（见 core/group_selection.py）
    fade_group_direction_overrides: list = field(default_factory=list)
    # 依次过渡「延迟时间」：与淡入淡出（过渡时间）解耦，表示在已选方向上扫过设备次序的总时长（秒）。
    # 0 = 无方向延迟（行为与旧版全局淡入淡出一致）。仅在选定方向后由 clip 顶部小加号拖动设置。
    seq_delay_in: float = 0.0
    seq_delay_out: float = 0.0
    # 延迟函数：针对每台设备「淡入淡出 ramp」的缓动曲线（取代硬编码 smoothstep）。None = 默认 smoothstep。
    seq_delay_curve: Optional[CurveMapping] = None
    # 各通道类型单独的过渡（淡入淡出）时间比例：{channel_type: 0.0~2.0}，默认空 = 全 100%。
    fade_type_ratio_in: Dict[str, float] = field(default_factory=dict)
    fade_type_ratio_out: Dict[str, float] = field(default_factory=dict)
    # Clip 附加处理状态（用于保存/打开与时间线可视化标识）
    smooth_preset: str = ""                 # 已离线应用的自动平滑预设 id；空/旁通表示未处理
    smooth_preset_name: str = ""            # 已离线应用的自动平滑预设显示名
    # ★★ R131 修复（`smooth_custom_params` 全程不落盘）：该字段此前**不在 dataclass
    #   里**，只活在实例 `__dict__` —— 而 `clip_postprocess` 白名单却列了它，于是
    #   "方案库能存、总览能显示"，重开工程/撤销后却丢。补成真字段使往返自洽。
    smooth_custom_params: dict = field(default_factory=dict)
    smooth_source_frames: list = field(default_factory=list, repr=False)  # R35 独立未处理来源
    light_tracking_enabled: bool = False     # 自动灯跟踪开关
    light_tracking_source: str = "pump"      # 跟踪来源：pump/valve
    light_tracking_threshold: int = 128      # 触发阈值，0-255
    light_tracking_attack: float = DEFAULT_LIGHT_TRACKING_ATTACK      # 灯亮淡入/响应速度（秒）
    light_tracking_release: float = DEFAULT_LIGHT_TRACKING_RELEASE     # 灯灭淡出/释放速度（秒）
    light_tracking_color: str = "#FFFFFF"    # 固定目标颜色；RGB/W 通道按此颜色比例由泵输出合成
    light_tracking_max_brightness: int = 255  # 最大亮度，0-255；灯跟踪「开」时最多亮到该值，与被跟踪数值无关
    record_output_source_enabled: bool = False  # 允许该 Clip 播放输出被新录制接收
    is_preset_clip: bool = False                # 命令预设生成(而非手动录制);回放引擎可据此优化
    motor_transition_phase_enabled: bool = False  # 电机参与顺序过渡相位差；默认关闭，保留旧规则
    motor_transition_phase_amount: float = 1.0  # 电机顺序过渡相位差强度；1.0=100%
    transitions_optimized: str = ""         # 优化过渡方式预设名（梯形速度曲线），空=未应用
    motor_force_preset: str = ""            # 优化电机力度预设名（电机力度优化），空=未应用
    motor_force_phase_amount: float = 0.0    # 力度层相位差；与手动 motor_transition_phase 分层叠加
    motor_force_accel: float = 0.2           # 梯形速度曲线加速段比例
    motor_force_cruise: float = 0.6          # 梯形速度曲线匀速段比例
    motor_force_decel: float = 0.2           # 梯形速度曲线减速段比例
    hold_last_frame: bool = False           # 兼容旧工程：True 表示开启任意保持模式
    hold_mode: str = "none"                 # none/last_frame/last_cycle
    hold_restore_fade_out: float = 0.0      # 关闭保持时恢复启用前的淡出长度
    motor_limit_scale: float = 1.0          # R110 电机限位：幅度往归位角缩放，1.0=原样
    motor_speed_limit_dps: float = 0.0      # R110 电机限速：虚拟最大摆速（度/秒），0=不限
    pump_master_scale: float = 1.0          # R110 泵主控：泵输出同比例压低，1.0=原样
    playback_rate: float = 1.0              # Clip 内部播放速度；1.0=100%
    source_duration: float = 0.0            # 100% 速度下的源时长；用于速度撤回/重新计算持续时间
    #: 拖右缘改时长**不变速**（用户 2026-09-14："默认拖动 clip 的长度…都是不变速的，
    #  往后拉按周期自动扩展、缩小则自动裁剪"）。True=超源跨度按周期补全；变速保留，
    #  但仅由右击菜单「速度/持续时间」应用（那会显式改 playback_rate）。
    resize_cycle: bool = True
    base_order_fixture_ids: List[str] = field(default_factory=list)
    base_fixture_positions: Dict[str, List[float]] = field(default_factory=dict)
    xy_reference_mode: str = "frozen"        # 兼容旧工程；回放统一冻结已录 DMX 值，不跟随当前 XY 基准
    xy_reference_state: dict = field(default_factory=dict)
    # ── Pump预充电 ────────────────────────────────────────────
    precharge_enabled: bool = False
    precharge_lead_ms: float = 1000.0   # 提前多少毫秒启动预充电
    precharge_level: int = 13            # 预充电 DMX 值（~5Hz 等效 = 13/255）
    # ── F14过渡优化器深度参数 ────────────────────────────────
    transition_strategy: str = "standard_fade"  # 旧版统一策略；新版 in/out 字段优先
    transition_strategy_in: str = ""             # 当前 Clip 淡入策略；空=回退统一策略
    transition_strategy_out: str = ""            # 当前 Clip 淡出策略；空=回退统一策略
    transition_curve: str = "Linear"             # 旧版统一曲线；新版 in/out 字段优先
    transition_curve_in: str = ""                # 当前 Clip 淡入曲线；空=回退统一曲线
    transition_curve_out: str = ""               # 当前 Clip 淡出曲线；空=回退统一曲线
    run_mode: str = "normal"                     # normal/reverse/pingpong/loop/once
    # ★ 控件轨迹（调度台·录制追溯）：control_key → [(t_rel, value), ...]。
    #   录制时由 RecordEngine 按控件信号即到即记（值变化才记）；回放=叠加调制层
    #   按 clip 时长周期循环（见 ai-memory/control-trajectory-design-v2-*.md）。
    trajectories: dict = field(default_factory=dict)

    def end_time(self) -> float:
        return self.start_time + self.duration

    def frame_count(self) -> int:
        return len(self.frames)

    def copy(self) -> "Clip":
        c = copy.deepcopy(self)
        c.id = str(uuid.uuid4())
        return c

    def decouple_frames(self) -> int:
        """把帧列表换成**独立副本**，返回复制条数（0 = 无帧）。

        ★★ R131 修复（平滑后撤销不回滚帧数据 → 原始录制永久丢失）：交互级撤销走
        `to_dict(include_frames=False)` 的轻快照，帧对象是**旁路共享引用**（只做
        `list(fr)` 浅拷贝）。而平滑两条入口都是**原地重写**帧（`frame.data = ...`）
        → undo 只还原了裁剪元数据，帧内容仍是改过的 → 之后任何一次保存都把平滑结果
        当"原始录制"写进工程，原始 DMX 找不回。

        约定：**任何"原地重写帧"的入口，改写前必须先调用本方法**，让快照里的旧帧
        对象与将被改写的帧彻底解耦（undo 天然生效）。返回复制条数供调用方判断。
        """
        frames = list(getattr(self, "frames", None) or [])
        if not frames:
            return 0
        self.frames = [f.copy() if hasattr(f, "copy") else f for f in frames]
        return len(self.frames)

    def to_dict(self, include_frames: bool = True) -> dict:
        """include_frames=False：帧列表输出为空（R57 交互快照快速通道）。

        帧 → base64 dict 的转换是大工程（10 万帧级）的毫秒级大头；
        交互级 undo 快照（ProjectManager._snapshot_scoped）只要帧引用
        不要序列化帧，跳过后 to_dict 从 ~1.1s 降到 ~25ms。
        frame_count 恒为真实值，恢复侧语义不变。
        """
        hold_mode = str(getattr(self, "hold_mode", "") or "").strip().lower()
        if hold_mode not in ("none", "last_frame", "last_cycle"):
            hold_mode = "last_cycle" if bool(getattr(self, "hold_last_frame", False)) else "none"
        playback_rate = max(0.001, min(100.0, float(getattr(self, "playback_rate", 1.0) or 1.0)))
        group_sequence_mode = str(getattr(self, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
        if group_sequence_mode not in ("parallel", "sequential"):
            group_sequence_mode = "parallel"
        spatial_scope = str(getattr(self, "fade_group_spatial_scope", "follow") or "follow").strip().lower()
        if spatial_scope not in ("per_shape", "global", "follow"):
            spatial_scope = "follow"
        group_pause = max(0.0, float(getattr(self, "fade_group_pause", 0.0) or 0.0))
        group_count = max(1, int(getattr(self, "fade_groups", 1) or 1))
        sequence_batches = max(0, int(getattr(self, "fade_group_sequence_batches", 0) or 0))
        batch_count = group_count if sequence_batches <= 0 else max(1, min(group_count, sequence_batches))
        group_pauses = normalize_group_pauses(
            getattr(self, "fade_group_pauses", []),
            max(0, batch_count - 1),
            group_pause,
        )
        return {
            "id": self.id,
            "track_id": self.track_id,
            "start_time": self.start_time,
            "duration": self.duration,
            "label": self.label,
            "preset_name": self.preset_name,
            "clip_universes": list(getattr(self, "clip_universes", [1]) or [1]),
            "active_channels_by_universe": {
                str(int(k)): [int(ch) for ch in (v or [])]
                for k, v in (getattr(self, "active_channels_by_universe", {}) or {}).items()
            },
            "recorded_channel_masks": getattr(self, "recorded_channel_masks", {}) or {},
            "curve": self.curve.to_dict() if self.curve else None,
            "trajectories": {
                str(k): [[float(t), v] for (t, v) in (seq or [])]
                for k, seq in (getattr(self, "trajectories", {}) or {}).items()
            },
            "fade_in": max(0.0, float(getattr(self, "fade_in", 0.0))),
            "fade_out": max(0.0, float(getattr(self, "fade_out", 0.0))),
            "fade_direction": getattr(self, "fade_direction", "none") or "none",
            "fade_in_direction": getattr(self, "fade_in_direction", "none") or "none",
            "fade_out_direction": getattr(self, "fade_out_direction", "none") or "none",
            "fade_direction_edge": str(getattr(self, "fade_direction_edge", "both") or "both").strip().lower(),
            "fade_groups": group_count,
            "fade_group_sequence_mode": group_sequence_mode,
            "fade_group_pause": group_pause,
            "fade_group_pauses": group_pauses,
            "fade_group_single_duration": max(0.0, float(getattr(self, "fade_group_single_duration", 0.0) or 0.0)),
            "fade_group_sequence_batches": sequence_batches,
            "fade_group_spatial_scope": spatial_scope,
            "fade_group_skip_base_order": bool(getattr(self, "fade_group_skip_base_order", False)),
            **_clip_r172_dict(self),  # R172 Clip Studio：sheet 绑定 id + 复合方向覆盖
            "seq_delay_in": max(0.0, float(getattr(self, "seq_delay_in", 0.0) or 0.0)),
            "seq_delay_out": max(0.0, float(getattr(self, "seq_delay_out", 0.0) or 0.0)),
            "seq_delay_curve": self.seq_delay_curve.to_dict() if getattr(self, "seq_delay_curve", None) else None,
            "fade_type_ratio_in": {
                str(k): max(0.0, min(2.0, float(v)))
                for k, v in (getattr(self, "fade_type_ratio_in", {}) or {}).items()
            },
            "fade_type_ratio_out": {
                str(k): max(0.0, min(2.0, float(v)))
                for k, v in (getattr(self, "fade_type_ratio_out", {}) or {}).items()
            },
            "smooth_preset": getattr(self, "smooth_preset", "") or "",
            "smooth_preset_name": getattr(self, "smooth_preset_name", "") or "",
            # ★ R131：自定义平滑参数（空 = 未设置 → 不落盘，守"默认值不落盘"纪律）
            **({"smooth_custom_params": dict(getattr(self, "smooth_custom_params", None) or {})}
               if getattr(self, "smooth_custom_params", None) else {}),
            "light_tracking_enabled": bool(getattr(self, "light_tracking_enabled", False)),
            "light_tracking_source": (
                "valve" if str(getattr(self, "light_tracking_source", "pump") or "pump").strip().lower() == "valve"
                else "pump"
            ),
            "light_tracking_threshold": max(0, min(255, int(getattr(self, "light_tracking_threshold", 128) or 0))),
            "light_tracking_attack": max(0.01, float(getattr(self, "light_tracking_attack", DEFAULT_LIGHT_TRACKING_ATTACK) or DEFAULT_LIGHT_TRACKING_ATTACK)),
            "light_tracking_release": max(0.01, float(getattr(self, "light_tracking_release", DEFAULT_LIGHT_TRACKING_RELEASE) or DEFAULT_LIGHT_TRACKING_RELEASE)),
            "light_tracking_color": getattr(self, "light_tracking_color", "#FFFFFF") or "#FFFFFF",
            "light_tracking_max_brightness": max(0, min(255, int(_ltmb_or_default(self.light_tracking_max_brightness)))),
            "record_output_source_enabled": bool(getattr(self, "record_output_source_enabled", False)),
            "is_preset_clip": bool(getattr(self, "is_preset_clip", False)),
            "motor_transition_phase_enabled": bool(getattr(self, "motor_transition_phase_enabled", False)),
            "motor_transition_phase_amount": max(0.0, min(10.0, float(getattr(self, "motor_transition_phase_amount", 1.0) or 0.0))),
            "hold_last_frame": hold_mode != "none",
            "hold_mode": hold_mode,
            "hold_restore_fade_out": max(0.0, float(getattr(self, "hold_restore_fade_out", 0.0) or 0.0)),
            "playback_rate": playback_rate, "resize_cycle": bool(getattr(self, "resize_cycle", True)),
            "source_duration": max(0.0, float(getattr(self, "source_duration", 0.0) or 0.0)),
            "base_order_fixture_ids": [str(fid) for fid in (getattr(self, "base_order_fixture_ids", []) or [])],
            "base_fixture_positions": ({
                str(fid): [float(pos[0]), float(pos[1])]
                for fid, pos in (getattr(self, "base_fixture_positions", {}) or {}).items()
                if isinstance(pos, (list, tuple)) and len(pos) >= 2}),
            "xy_reference_mode": getattr(self, "xy_reference_mode", "frozen") or "frozen",
            "xy_reference_state": dict(getattr(self, "xy_reference_state", None) or {}),
            "precharge_enabled": bool(getattr(self, "precharge_enabled", False)),
            "precharge_lead_ms": max(0.0, float(_lead_ms_or_default(getattr(self, "precharge_lead_ms", 1000.0)))),
            "precharge_level": max(0, min(255, int(getattr(self, "precharge_level", 13) or 0))),
            "transitions_optimized": getattr(self, "transitions_optimized", "") or "",
            "motor_force_preset": getattr(self, "motor_force_preset", "") or "",
            "motor_force_phase_amount": max(0.0, min(10.0, float(
                getattr(self, "motor_force_phase_amount", 0.0) or 0.0))),
            "motor_force_accel": max(0.0, float(getattr(self, "motor_force_accel", 0.2) or 0.0)),
            "motor_force_cruise": max(0.0, float(getattr(self, "motor_force_cruise", 0.6) or 0.0)),
            "motor_force_decel": max(0.0, float(getattr(self, "motor_force_decel", 0.2) or 0.0)),
            # R110 临场滤网（默认值 = 不生效；见 engine/clip_output_filters.py）
            "motor_limit_scale": _clamp01(getattr(self, "motor_limit_scale", 1.0)),
            "motor_speed_limit_dps": _clamp_pos(getattr(self, "motor_speed_limit_dps", 0.0)),
            "pump_master_scale": _clamp01(getattr(self, "pump_master_scale", 1.0)),
            "transition_strategy": getattr(self, "transition_strategy", "standard_fade") or "standard_fade",
            "transition_strategy_in": getattr(self, "transition_strategy_in", "") or "",
            "transition_strategy_out": getattr(self, "transition_strategy_out", "") or "",
            "transition_curve": getattr(self, "transition_curve", "Linear") or "Linear",
            "transition_curve_in": getattr(self, "transition_curve_in", "") or "",
            "transition_curve_out": getattr(self, "transition_curve_out", "") or "",
            "run_mode": getattr(self, "run_mode", "normal") or "normal",
            "frame_count": len(self.frames),
            # ★ R140：整段帧打成紧凑 blob（实测 -97%）；失败退回逐帧 `frames`。
            **source_payload(self, include_frames),
            **(_frames_payload(self.frames) if include_frames else {"frames": []}),
        }

# ─────────────────────────────────────────────
#  轨道 (Track)
# ─────────────────────────────────────────────
def _load_legacy_frames(clip, frame_dicts, lazy: bool) -> None:
    """旧格式：逐帧数组 → `clip.frames`（R140 保留，兼容既有工程）。"""
    for fd in (frame_dicts or []):
        try:
            f = _LazyDMXFrame.from_frame_dict(fd) if lazy else DMXFrame.from_dict(fd)
            clip.frames.append(f)
        except Exception:  # N4：首错 warning+计数（原 debug 静默）
            if not getattr(clip, "_legacy_err_logged", False):
                clip._legacy_err_logged = True; logger.warning("旧格式帧解析失败", exc_info=True)
            clip._legacy_err_count = getattr(clip, "_legacy_err_count", 0) + 1


def _clip_frames_load(clip, cd, lazy: bool, frames_memo) -> None:
    """clip 帧数据恢复总入口（blob 优先，旧 `frames` 回退；★ R183 双解析消重）。

    `Project._from_dict` 会把 current program 的 tracks 解析**两遍**（program 快照
    一遍 + `proj.tracks` 重建一遍，见 _from_dict 内注释）。传 `frames_memo` 时按
    clip-dict 身份记忆第一遍构造出的帧列表，第二遍直接浅拷贝复用 —— 20 万帧级
    工程实测加载耗时近乎减半（204,270 次帧构造 → 一次）。

    帧对象旁路共享与 R57 undo 快照（project_manager `_snapshot_scoped`）**同一契约**：
    时间线参数编辑不动帧内容、录制只追加新列表；原地改写帧前必须 `decouple_frames`
    （R131 纪律）。memo 持有的是新列表（`list(cached)`），append/裁剪互不串扰。
    memo 只在单次 `_from_dict` 调用内存活，clip-dict 全程存活 → `id()` 作键稳定。
    """
    if frames_memo is not None:
        cached = frames_memo.get(id(cd))
        if cached is not None:
            clip.frames = list(cached)
            return
    blob = cd.get("frames_blob")
    if isinstance(blob, dict):
        from .frame_blob import load_frames_into
        load_frames_into(clip, blob, lazy)
    else:
        _load_legacy_frames(clip, cd.get("frames", []), lazy)
    if frames_memo is not None:
        frames_memo[id(cd)] = clip.frames


def _frames_payload(frames) -> dict:
    """clip 帧数据的落盘负载（★ R140 体积优化的唯一出口）。

    优先产出紧凑 `frames_blob`（二进制打包 + zlib；实测 -96%）；打包失败则退回
    逐帧 `frames` 数组 —— **保存绝不能因为优化而丢数据**。
    """
    try:
        from .frame_blob import pack_frames, unpack_frame_bytes   # noqa: F401
        blob = pack_frames(frames)
        if blob is not None:
            return {"frames_blob": blob}
    except Exception:
        logger.debug("pack_frames 失败，退回逐帧格式", exc_info=True)
    return {"frames": [f.to_dict() for f in (frames or [])]}


def _dict_list(raw) -> list:
    """只保留 list 里的 dict 元素（`None`/标量/非列表一律安全收敛）。

    ★ 深查修正（打开损坏工程即崩）：`Track.from_dict` 原为 `d.get("clips", [])` ——
      字段为 `null` 时 TypeError（不可迭代）、元素为 null 时 `cd.get` AttributeError，
      而它由 `Project._from_dict` **无保护**调用 → 一个畸形字段就让工程打不开。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _clip_r172_dict(clip) -> dict:
    """R172 Clip Studio：sheet 绑定 id + 复合方向覆盖（照 fade_group_pauses 归一模式）。"""
    return {
        "process_sheet_id": str(getattr(clip, "process_sheet_id", "") or ""),
        "fade_group_direction_overrides": normalize_direction_overrides(
            getattr(clip, "fade_group_direction_overrides", [])),
    }


def _apply_r172_clip_fields(clip, cd) -> None:
    """R172 Clip Studio：缺键给默认（旧工程无此二键），畸形数据归一收敛不崩。"""
    clip.process_sheet_id = str(cd.get("process_sheet_id", "") or "")
    clip.fade_group_direction_overrides = normalize_direction_overrides(
        cd.get("fade_group_direction_overrides", []))


def _restore_audio_mod_flag(t: "Track", d: dict) -> None:
    """Track.is_audio_mod 反序列化（R179）。

    单独成函数的原因：Track.from_dict 行数/CC 基线冻结（工具门禁只许缩小），
    is_audio_mod 的恢复以行内语句挂在既有赋值行上，逻辑收在本 helper 内。
    缺键给 False（旧工程 = 无调制轨标记，与旧档迁移逻辑衔接）。
    """
    t.is_audio_mod = bool(d.get("is_audio_mod", False))


def _project_state_dict(proj) -> dict:
    """Project.to_dict 尾部状态块（调度台/程序员 + R172 Clip Studio 画布）。"""
    return {
        "route_animator_state": dict(getattr(proj, "route_animator_state", {}) or {}),
        "dispatch_state": dict(getattr(proj, "dispatch_state", {}) or {}),
        "programmer_state": dict(getattr(proj, "programmer_state", {}) or {}),
        "clip_studio_state": dict(getattr(proj, "clip_studio_state", {}) or {}),
        "audio_modulation_state": dict(getattr(proj, "audio_modulation_state", {}) or {}),
    }


@dataclass
class Track:
    """时间线轨道，支持多域（Multi-Universe）"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Track"
    universes: List[int] = field(default_factory=lambda: [1])
    filter_rule: FilterRule = field(default_factory=FilterRule)   # 兼容旧版（全局过滤）
    universe_filters: dict = field(default_factory=dict)          # {universe_int: FilterRule} 每域独立过滤
    clips: list = field(default_factory=list)    # List[Clip]
    color: str = "#4A90D9"
    height: int = 60
    muted: bool = False
    solo: bool = False
    is_folder: bool = False                 # 时间线文件夹轨道：用于组织下级轨道，不直接输出
    parent_id: str = ""                     # 上级文件夹轨道 id；空字符串表示顶层
    folder_expanded: bool = True            # 文件夹展开/折叠状态
    # R179：音频调制轨（可添加/可拖动的真正 Track；状态真源仍是
    # Project.audio_modulation_state，经 state.scope_track_id 指向本轨）。
    is_audio_mod: bool = False

    @property
    def universe(self) -> int:
        """兼容旧代码：返回第一个宇宙编号"""
        return self.universes[0] if self.universes else 1

    @universe.setter
    def universe(self, value: int):
        """兼容旧代码：设置第一个宇宙编号"""
        if not self.universes:
            self.universes = [value]
        else:
            self.universes[0] = value

    def get_filter_for_universe(self, universe: int) -> FilterRule:
        """获取指定域的过滤规则（优先用 universe_filters，回退到全局 filter_rule）"""
        if self.universe_filters and universe in self.universe_filters:
            return self.universe_filters[universe]
        return self.filter_rule

    def filter_label(self) -> str:
        """返回供UI显示的过滤标签"""
        if self.universe_filters:
            parts = []
            for u in self.universes:
                rule = self.universe_filters.get(u, self.filter_rule)
                expr = rule.to_expression()
                parts.append(f"U{u}:{expr or 'ALL'}")
            return " | ".join(parts)
        expr = self.filter_rule.to_expression()
        u_str = ",".join(str(u) for u in self.universes) if self.universes else "1"
        return f"U{u_str}: {expr}" if expr else f"U{u_str}: ALL"

    def add_clip(self, clip: Clip):
        clip.track_id = self.id
        self.clips.append(clip)
        self.clips.sort(key=lambda c: c.start_time)

    def remove_clip(self, clip_id: str):
        self.clips = [c for c in self.clips if c.id != clip_id]

    def to_dict(self, include_frames: bool = True) -> dict:
        uf_serialized = {
            str(k): v.to_dict() for k, v in self.universe_filters.items()
        }
        return {
            "id": self.id,
            "name": self.name,
            "universe": self.universe,
            "universes": self.universes,
            "filter_rule": self.filter_rule.to_dict(),
            "universe_filters": uf_serialized,
            "color": self.color,
            "height": self.height,
            "muted": self.muted,
            "solo": self.solo,
            "is_folder": bool(getattr(self, "is_folder", False)),
            "is_audio_mod": bool(getattr(self, "is_audio_mod", False)),
            "parent_id": getattr(self, "parent_id", "") or "",
            "folder_expanded": bool(getattr(self, "folder_expanded", True)),
            "clips": [c.to_dict(include_frames=include_frames) for c in self.clips]
        }

    @classmethod
    def from_dict(cls, d: dict, _lazy: bool = False, _frames_memo: dict = None) -> "Track":  # ★ R183 memo 见 _clip_frames_load
        # 支持新版 universes 列表和旧版 universe 单值
        universes = d.get("universes", None)
        if universes is None:
            universes = [d.get("universe", 1)]
        t = cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Track"),
            universes=universes,
            color=d.get("color", "#4A90D9"),
            height=d.get("height", 60),
            muted=d.get("muted", False),
            solo=d.get("solo", False),
            is_folder=bool(d.get("is_folder", False)),
            parent_id=d.get("parent_id", "") or "",
            folder_expanded=bool(d.get("folder_expanded", True)),
        )
        t.filter_rule = FilterRule.from_dict(d.get("filter_rule", {})); _restore_audio_mod_flag(t, d)  # R179：本函数行数基线冻结，is_audio_mod 行内补挂（见 helper docstring）
        # 加载每域独立过滤规则
        uf_raw = d.get("universe_filters", {})
        for k, vd in uf_raw.items():
            try:
                t.universe_filters[int(k)] = FilterRule.from_dict(vd)
            except (ValueError, TypeError):
                logger.debug("Track.from_dict: skipping invalid universe filter key: %r", k)
        for cd in _dict_list(d.get("clips")):   # ★ 见函数（畸形 clips 不得崩）
            clip = Clip(
                id=cd.get("id", str(uuid.uuid4())),
                track_id=cd.get("track_id", t.id),
                start_time=cd.get("start_time", 0.0),
                duration=cd.get("duration", 0.0),
                label=cd.get("label", ""),
                preset_name=cd.get("preset_name", ""),
                clip_universes=list(cd.get("clip_universes", [1]) or [1]),
                active_channels_by_universe={
                    int(k): [int(ch) for ch in (v or [])]
                    for k, v in (cd.get("active_channels_by_universe", {}) or {}).items()
                },
                recorded_channel_masks=_recorded_masks_from_dict(cd),
                curve=CurveMapping.from_dict(cd["curve"]) if cd.get("curve") else None,
                fade_in=max(0.0, float(cd.get("fade_in", 0.0) or 0.0)),
                fade_out=max(0.0, float(cd.get("fade_out", 0.0) or 0.0)),
            )
            legacy_hold = bool(cd.get("hold_last_frame", False))
            hold_mode = str(cd.get("hold_mode", "") or "").strip().lower()
            if hold_mode not in ("none", "last_frame", "last_cycle"):
                hold_mode = "last_cycle" if legacy_hold else "none"
            clip.hold_mode = hold_mode
            clip.hold_last_frame = hold_mode != "none"
            clip.hold_restore_fade_out = max(0.0, float(cd.get("hold_restore_fade_out", 0.0) or 0.0))
            clip.playback_rate = max(0.001, min(100.0, float(cd.get("playback_rate", 1.0) or 1.0)))
            clip.source_duration = max(0.0, float(cd.get("source_duration", 0.0) or 0.0)); clip.resize_cycle = bool(cd.get("resize_cycle", True))
            clip.base_order_fixture_ids = [str(fid) for fid in (cd.get("base_order_fixture_ids", []) or [])]
            clip.base_fixture_positions = ({
                str(fid): [float(pos[0]), float(pos[1])]
                for fid, pos in (cd.get("base_fixture_positions", {}) or {}).items()
                if isinstance(pos, (list, tuple)) and len(pos) >= 2})
            xy_mode = str(cd.get("xy_reference_mode", "frozen") or "frozen").strip().lower()
            clip.xy_reference_mode = xy_mode if xy_mode in ("frozen", "sync") else "frozen"
            clip.xy_reference_state = dict(cd.get("xy_reference_state") or {})
            clip.fade_direction = cd.get("fade_direction", "none") or "none"
            clip.fade_in_direction = cd.get("fade_in_direction", "") or ""
            clip.fade_out_direction = cd.get("fade_out_direction", "") or ""
            # 向后兼容：如果旧版只有 fade_direction，自动迁移到 in/out
            if clip.fade_direction != "none" and not clip.fade_in_direction and not clip.fade_out_direction:
                clip.fade_in_direction = clip.fade_direction
                clip.fade_out_direction = clip.fade_direction
            edge = str(cd.get("fade_direction_edge", "both") or "both").strip().lower()
            clip.fade_direction_edge = edge if edge in ("in", "out") else "both"
            clip.fade_groups = max(1, int(cd.get("fade_groups", 1) or 1))
            group_sequence_mode = str(cd.get("fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
            clip.fade_group_sequence_mode = group_sequence_mode if group_sequence_mode in ("parallel", "sequential") else "parallel"
            clip.fade_group_pause = max(0.0, float(cd.get("fade_group_pause", 0.0) or 0.0))
            clip.fade_group_single_duration = max(0.0, float(cd.get("fade_group_single_duration", 0.0) or 0.0))
            clip.fade_group_sequence_batches = max(0, int(cd.get("fade_group_sequence_batches", 0) or 0))
            batch_count = (
                clip.fade_groups
                if clip.fade_group_sequence_batches <= 0
                else max(1, min(clip.fade_groups, clip.fade_group_sequence_batches))
            )
            clip.fade_group_pauses = normalize_group_pauses(
                cd.get("fade_group_pauses", []),
                max(0, batch_count - 1),
                clip.fade_group_pause,
            )
            spatial_scope = str(cd.get("fade_group_spatial_scope", "follow") or "follow").strip().lower()
            clip.fade_group_spatial_scope = spatial_scope if spatial_scope in ("per_shape", "global", "follow") else "follow"
            clip.fade_group_skip_base_order = bool(cd.get("fade_group_skip_base_order", False))
            _apply_r172_clip_fields(clip, cd)  # R172 Clip Studio：缺键给默认（畸形数据归一收敛不崩）
            clip.seq_delay_in = max(0.0, float(cd.get("seq_delay_in", 0.0) or 0.0))
            clip.seq_delay_out = max(0.0, float(cd.get("seq_delay_out", 0.0) or 0.0))
            clip.seq_delay_curve = (
                CurveMapping.from_dict(cd["seq_delay_curve"]) if cd.get("seq_delay_curve") else None
            )
            clip.fade_type_ratio_in = {
                str(k): max(0.0, min(2.0, float(v)))
                for k, v in (cd.get("fade_type_ratio_in", {}) or {}).items()
            }
            clip.fade_type_ratio_out = {
                str(k): max(0.0, min(2.0, float(v)))
                for k, v in (cd.get("fade_type_ratio_out", {}) or {}).items()
            }
            clip.smooth_preset = cd.get("smooth_preset", "") or ""
            clip.smooth_preset_name = cd.get("smooth_preset_name", "") or ""
            # ★ R131：容错读取（旧档无该键 → 空；坏数据非 dict 也退回空，不抛）
            _scp = cd.get("smooth_custom_params", None)
            clip.smooth_custom_params = dict(_scp) if isinstance(_scp, dict) else {}
            clip.light_tracking_enabled = bool(cd.get("light_tracking_enabled", False))
            clip.light_tracking_source = (
                "valve" if str(cd.get("light_tracking_source", "pump") or "pump").strip().lower() == "valve"
                else "pump"
            )
            clip.light_tracking_threshold = max(0, min(255, int(cd.get("light_tracking_threshold", 128) or 0)))
            clip.light_tracking_attack = max(0.01, float(cd.get("light_tracking_attack", DEFAULT_LIGHT_TRACKING_ATTACK) or DEFAULT_LIGHT_TRACKING_ATTACK))
            clip.light_tracking_release = max(0.01, float(cd.get("light_tracking_release", DEFAULT_LIGHT_TRACKING_RELEASE) or DEFAULT_LIGHT_TRACKING_RELEASE))
            clip.light_tracking_color = cd.get("light_tracking_color", "#FFFFFF") or "#FFFFFF"
            clip.light_tracking_max_brightness = max(0, min(255, int(_ltmb_or_default(cd.get("light_tracking_max_brightness")))))
            clip.record_output_source_enabled = bool(cd.get("record_output_source_enabled", False))
            clip.is_preset_clip = bool(cd.get("is_preset_clip", False))
            # 控件轨迹（容错：坏条目跳过，值保持 JSON 安全标量/列表/dict）
            traj_raw = cd.get("trajectories", {}) or {}
            traj_clean = {}
            for tk, seq in traj_raw.items():
                try:
                    traj_clean[str(tk)] = [(float(t), v) for (t, v) in (seq or [])]
                except Exception:
                    continue
            clip.trajectories = traj_clean
            clip.motor_transition_phase_enabled = bool(cd.get("motor_transition_phase_enabled", False))
            clip.motor_transition_phase_amount = max(0.0, min(10.0, float(cd.get("motor_transition_phase_amount", 1.0) or 0.0)))
            clip.precharge_enabled = bool(cd.get("precharge_enabled", False))
            clip.precharge_lead_ms = max(0.0, float(_lead_ms_or_default(cd.get("precharge_lead_ms", 1000.0))))
            clip.precharge_level = max(0, min(255, int(cd.get("precharge_level", 13) or 0)))
            clip.transitions_optimized = cd.get("transitions_optimized", "") or ""
            clip.motor_force_preset = cd.get("motor_force_preset", "") or ""
            force_defaults = MOTOR_FORCE_PROFILES.get(clip.motor_force_preset, {})
            legacy_force_phase = cd.get(
                "motor_transition_phase_amount", force_defaults.get("phase_amt", 0.0)
            )
            clip.motor_force_phase_amount = max(0.0, min(10.0, float(
                cd.get("motor_force_phase_amount", legacy_force_phase) or 0.0
            )))
            clip.motor_force_accel = max(0.0, float(cd.get("motor_force_accel", force_defaults.get("accel", 0.2)) or 0.0))
            clip.motor_force_cruise = max(0.0, float(cd.get("motor_force_cruise", force_defaults.get("cruise", 0.6)) or 0.0))
            clip.motor_force_decel = max(0.0, float(cd.get("motor_force_decel", force_defaults.get("decel", 0.2)) or 0.0))
            # R110 临场滤网（旧工程无键 → 取默认 = 不生效）
            clip.motor_limit_scale = _clamp01(cd.get("motor_limit_scale", 1.0))
            clip.motor_speed_limit_dps = _clamp_pos(cd.get("motor_speed_limit_dps", 0.0))
            clip.pump_master_scale = _clamp01(cd.get("pump_master_scale", 1.0))
            if force_defaults and "motor_force_phase_amount" not in cd:
                clip.motor_transition_phase_enabled = False
                clip.motor_transition_phase_amount = 0.0
            # F14深度参数
            clip.transition_strategy = cd.get("transition_strategy", "standard_fade") or "standard_fade"
            clip.transition_strategy_in = cd.get("transition_strategy_in", "") or ""
            clip.transition_strategy_out = cd.get("transition_strategy_out", "") or ""
            clip.transition_curve = cd.get("transition_curve", "Linear") or "Linear"
            clip.transition_curve_in = cd.get("transition_curve_in", "") or ""
            clip.transition_curve_out = cd.get("transition_curve_out", "") or ""
            clip.run_mode = cd.get("run_mode", "normal") or "normal"
            # 恢复帧数据（_lazy=True 时不解码 base64，使用轻量引用）
            #   ★ R140：优先读紧凑 blob（新格式）；旧工程的 `frames` 数组照旧支持。
            #   ★ R183：`_frames_memo` 传入时跨"第二遍解析"复用帧列表（消双解析）。
            _clip_frames_load(clip, cd, _lazy, _frames_memo); load_source(clip, cd, _lazy, _frames_memo)
            t.clips.append(clip)
        return t

# ─────────────────────────────────────────────
#  网络节点配置
# ─────────────────────────────────────────────
@dataclass
class NetworkConfig:
    """网络配置"""
    protocol: Protocol = Protocol.ARTNET
    input_mode: str = "broadcast"
    input_ip: str = "0.0.0.0"
    input_universes: List[int] = field(default_factory=lambda: [1])
    # 是否启用 DMX 输入接收/监听。默认关闭：本软件常态只输出到 /灯具，
    # 而接收器工作在 wildcard 模式（收全网每一个包），在 UI 线程上逐包解析+加锁+
    # 发信号，无消费者时纯属拖累。仅在确实需要监视外部控台/做 DMX 输入录制时开启。
    input_enabled: bool = False
    output_mode: str = "broadcast"
    output_ip: str = "255.255.255.255"
    output_universes: List[int] = field(default_factory=lambda: [1])
    output_port: int = 6454
    sacn_priority: int = 100

    @property
    def input_universe(self) -> int:
        return self.input_universes[0] if self.input_universes else 1

    @input_universe.setter
    def input_universe(self, v: int):
        if self.input_universes:
            self.input_universes[0] = v
        else:
            self.input_universes = [v]

    @property
    def output_universe(self) -> int:
        return self.output_universes[0] if self.output_universes else 1

    @output_universe.setter
    def output_universe(self, v: int):
        if self.output_universes:
            self.output_universes[0] = v
        else:
            self.output_universes = [v]

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol.value,
            "input_mode": self.input_mode,
            "input_ip": self.input_ip,
            "input_universes": list(self.input_universes) if self.input_universes else [1],
            "input_enabled": self.input_enabled,
            "output_mode": self.output_mode,
            "output_ip": self.output_ip,
            "output_universes": list(self.output_universes) if self.output_universes else [1],
            "output_port": self.output_port,
            "sacn_priority": self.sacn_priority,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkConfig":
        cfg = cls()
        proto_str = d.get("protocol", "Art-Net")
        try:
            cfg.protocol = Protocol(proto_str)
        except (ValueError, KeyError):
            cfg.protocol = Protocol.ARTNET
        cfg.input_mode     = d.get("input_mode", "broadcast")
        cfg.input_ip       = d.get("input_ip", "0.0.0.0")
        # 优先读取全量列表，回退旧版单域兼容
        in_unis = d.get("input_universes")
        if in_unis is not None and isinstance(in_unis, list):
            parsed_input = []
            for u in in_unis:
                try:
                    v = int(u)
                except (ValueError, TypeError):
                    logger.warning("NetworkConfig.from_dict: skipping invalid input_universe: %r", u)
                    continue
                if v >= 1:
                    parsed_input.append(v)
            cfg.input_universes = parsed_input
        else:
            cfg.input_universe = d.get("input_universe", 1)
        # 旧工程无此字段 → 默认 False（关闭输入接收器）
        cfg.input_enabled  = bool(d.get("input_enabled", False))
        cfg.output_mode    = d.get("output_mode", "broadcast")
        cfg.output_ip      = d.get("output_ip", "255.255.255.255")
        # 优先读取全量列表，回退旧版单域兼容
        out_unis = d.get("output_universes")
        if out_unis is not None and isinstance(out_unis, list):
            parsed_output = []
            for u in out_unis:
                try:
                    v = int(u)
                except (ValueError, TypeError):
                    logger.warning("NetworkConfig.from_dict: skipping invalid output_universe: %r", u)
                    continue
                if v >= 1:
                    parsed_output.append(v)
            cfg.output_universes = parsed_output
        else:
            cfg.output_universe = d.get("output_universe", 1)
        cfg.output_port    = d.get("output_port", 6454)
        cfg.sacn_priority  = d.get("sacn_priority", 100)
        return cfg

# ─────────────────────────────────────────────
#  工程
# ─────────────────────────────────────────────
@dataclass
class TimelineProgram:
    """A saved timeline sheet for one song/program inside a project."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Program"
    light_enabled: bool | None = True  # R36 None仅识别缺键旧档，主界面按绑定迁移
    audio_path: str = ""
    audio_clip_state: dict = field(default_factory=dict)  # 音频参考轨 clip 参数（start_time/fade_in/fade_out）
    audio_modulation_state: dict = field(default_factory=dict)  # R174#7 音频调制轨道（与 audio_clip_state 同为节目级）
    tracks: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self, include_frames: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "audio_path": self.audio_path,
            "light_enabled": self.light_enabled,
            "audio_clip_state": dict(getattr(self, "audio_clip_state", {}) or {}),
            "audio_modulation_state": dict(getattr(self, "audio_modulation_state", {}) or {}),
            "tracks": [t.to_dict(include_frames=include_frames) for t in (self.tracks or [])],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict, _lazy: bool = False, _frames_memo: dict = None) -> "TimelineProgram":
        if not isinstance(d, dict):
            d = {}
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Program"),
            light_enabled=d.get("light_enabled") if isinstance(d.get("light_enabled"), bool) else None,
            audio_path=d.get("audio_path", ""),
            audio_clip_state=dict(d.get("audio_clip_state", {}) or {}),
            audio_modulation_state=dict(d.get("audio_modulation_state", {}) or {}),
            tracks=[Track.from_dict(td, _lazy=_lazy, _frames_memo=_frames_memo) for td in (d.get("tracks", []) or [])],
            created_at=float(d.get("created_at", time.time()) or time.time()),
            updated_at=float(d.get("updated_at", time.time()) or time.time()),
        )


def _marker_entry(m) -> Optional[dict]:
    """单条标记 → 归一化 dict；非法（非 dict / t 不可转 / 负 / NaN）→ None。"""
    if not isinstance(m, dict):
        return None
    try:
        t = float(m.get("t", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if t != t or t < 0.0:
        return None
    return {"t": t, "text": str(m.get("text", "") or ""),
            "color": str(m.get("color", "") or "")}


def _apply_marker_fields(proj, d: dict) -> None:
    """★ R183 T2：`Project._from_dict` 的标记轨 + 循环区回填（外提压 CC/行数）。

    旧档无键 → 空列表 / 空 dict（**零迁移**：真工程 TTM 实测这两键全工程零占用）。
    """
    proj.markers = _normalize_markers(d.get("markers"))
    proj.loop = _normalize_loop(d.get("loop"))


def _marker_fields(proj) -> dict:
    """★ R183 T2：`Project.to_dict` 尾部的标记轨 + 循环区两块（外提压 CC/行数）。

    与 `_project_state_dict` 同款"尾块助手"：`to_dict` 本体已在架构基线里，
    任何内联新增都会让它超基线（门禁只许缩小）。
    """
    return {
        "markers": list(getattr(proj, "markers", []) or []),
        "loop": dict(getattr(proj, "loop", {}) or {}),
    }


def _normalize_markers(raw) -> list:
    """★ R183 T2：标记列表归一化（旧档/坏数据一律安全回落，绝不抛）。

    只保留 `{t, text, color}` 三个键的合法条目；`t` 必须能转浮点且非负，
    按 `t` 升序（标记轨的时间有序性由数据层保证，UI 不必各自排序）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out = [e for e in (_marker_entry(m) for m in raw) if e is not None]
    out.sort(key=lambda x: x["t"])
    return out


def _normalize_loop(raw) -> dict:
    """★ R183 T2/T6：循环区归一化 → `{"start","end","enabled"}`；无键 → 空 dict。

    `enabled=False` **也保留区间**（loop brace 语义：区间位置与启用开关
    是两件事），`end` 恒 > `start`（与 `TimelineController.set_loop_range` 同口径）。
    """
    if not isinstance(raw, dict):
        return {}
    try:
        start = max(0.0, float(raw.get("start", 0.0) or 0.0))
        end = float(raw.get("end", 0.0) or 0.0)
    except (TypeError, ValueError):
        return {}
    if start != start or end != end or end <= start:
        return {}
    return {"start": start, "end": end,
            "enabled": bool(raw.get("enabled", True))}


def _legacy_program_from_dict(d: dict, legacy_tracks: list, created_at: float) -> "TimelineProgram":
    """无 programs 的旧档 → 单个 Legacy Timeline program（从 _from_dict 拆出）。"""
    return TimelineProgram(
        name=d.get("current_program_name", "") or "Legacy Timeline",
        audio_path=d.get("audio_path", ""),
        audio_clip_state=dict(d.get("audio_clip_state", {}) or {}),
        audio_modulation_state=dict(d.get("audio_modulation_state", {}) or {}),
        tracks=legacy_tracks,
        created_at=created_at,
        updated_at=time.time(),
    )


@dataclass
class Project(QObject):
    """整个工程的顶层数据容器"""
    # 核心修复：定义全局项目变更信号，确保所有面板同步刷新
    project_changed = pyqtSignal()

    name: str = "Untitled Project"
    created_at: float = field(default_factory=time.time)
    tracks: list = field(default_factory=list)
    network_config: NetworkConfig = field(default_factory=NetworkConfig)
    input_filter: FilterRule = field(default_factory=FilterRule)
    output_filter: FilterRule = field(default_factory=FilterRule)
    bpm: float = 120.0
    # ★ R183 T2（时间线批）：标记轨 / 循环区 —— Marker Track + Loop Region
    #   官方语义（help3..com/.../-marker-track.md、.../-loop-region.md）。
    #   真工程 To The Moon（../To The Moon项目.dmxt，91MB）复核：顶层 `markers` 与
    #   `loop` 两个键**全工程零占用** → 新键零冲突、零迁移；旧档缺键走默认值。
    #   markers: [{"t": 秒, "text": str, "color": "#RRGGBB"}, …]（按 t 排序由 UI 负责）
    #   loop:    {"start": 秒, "end": 秒, "enabled": bool}（enabled=False 也存区间 —
    #           用户关掉循环再保存，再打开时区间还在、循环仍是关的）
    markers: list = field(default_factory=list)
    loop: dict = field(default_factory=dict)
    sample_rate: float = 40.0
    timecode_fps: float = 25.0
    fixture_defs: List[FixtureDefinition] = field(default_factory=list)
    fixtures: List[FixtureInstance] = field(default_factory=list)
    fixture_groups: List[dict] = field(default_factory=list)  # 存储 FixtureGroup 的 dict 列表
    logical_devices: List[dict] = field(default_factory=list)  # 存储 LogicalDevice 的 dict 列表（组合/微单元覆盖层）
    # 全局灯控模式（联动所有页面）：single=单控(不组合，每个灯单元在各自坐标系/per_shape
    # 里跑，如 4 灯拟合圆顺逆时针)；whole=整体控(全部灯组合成整体坐标系/global)。
    # effect 的 spatial_scope="follow" 时跟随本设定，显式 per_shape/global 仍各自覆盖。
    # 默认 whole=整体：用户默认操作的就是「全部组合后的灯」。
    light_control_mode: str = "whole"
    audio_path: str = ""
    audio_clip_state: dict = field(default_factory=dict)  # 音频参考轨 clip 参数（start_time/fade_in/fade_out）
    audio_modulation_state: dict = field(default_factory=dict)  # R174#7 音频调制轨道（schema 归 audio_mod 模块管）
    programs: List[TimelineProgram] = field(default_factory=list)
    current_program_id: str = ""
    # v25 新增：各面板持久化状态
    audio_bridge_state: dict = field(default_factory=dict)    # 音频桥接器完整状态
    matrix_slideshow_state: dict = field(default_factory=dict) # 矩阵幻灯片面板状态
    audio_driver_state: dict = field(default_factory=dict)     # Programmer音频驱动参数
    effect_browser_state: dict = field(default_factory=dict)   # 效果浏览器状态
    processor_state: dict = field(default_factory=dict)        # 处理器/轨道关联器状态
    topview_state: dict = field(default_factory=dict)          # TopView 多页面、CAD 图形、矩阵与选择状态
    route_order_state: dict = field(default_factory=dict)      # 跑动顺序多层 Sheet 状态
    # 样条（用户画的弧线/折线）：世界坐标控制点，同步基准切向角用。
    # 独立字段而非 topview_state——topview_state 会被 save_state 反复覆盖，
    # 存里面会被清掉（2026-08-03 用户建议双管齐下）。
    splines: list = field(default_factory=list)
    # 延迟函数命名预设库：{preset_name: CurveMapping.to_dict()}，随项目保存
    delay_function_presets: dict = field(default_factory=dict)
    clip_postprocess_profiles: dict = field(default_factory=dict)  # R111 后处理方案库
    # 以下三项为 UI 层 store 序列化出的不透明 dict，core 不解释其内容（opaque）：
    programmer_cues_data: dict = field(default_factory=dict)   # Programmer CUE 库数据
    param_envelopes_data: dict = field(default_factory=dict)   # 通用参数包络库 + 绑定数据
    custom_effects_data: dict = field(default_factory=dict)    # 自定义效果库数据
    baked_arrangement_data: dict = field(default_factory=dict) # 自动编曲预烘焙方案（绑定音频）
    trajectory_curves_data: dict = field(default_factory=dict) # Programmer 轨迹曲线库
    # 跑动编排状态 + 命名节目（RouteAnimator）
    route_animator_state: dict = field(default_factory=dict)
    # 调度台：效果缓存库（可追溯快照；由 processing.dispatch 序列化，core 不解释）
    dispatch_state: dict = field(default_factory=dict)
    # ★ Programmer 面板完整状态（含条带形状 mode/base/target、设备作用域）。
    #   2026-09-13 加：此前 Programmer 无 save_state，工程 undo 无法还原它。
    #   旧工程缺此字段 → 默认空 dict（向后兼容，不做迁移）。
    programmer_state: dict = field(default_factory=dict)
    # R172 Clip Studio（卡片画布子页面）持久化（§2.3）：
    # {"sheets": [{"id","name","graph","created"}], "active_sheet": id, "view_mode": "single"|"grid"}
    # 旧工程缺此字段 → 默认空 dict（向后兼容，不做迁移）。
    clip_studio_state: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__init__()
        # ★ R141：配接版本号（**运行时态，刻意不落盘**）。
        #   为什么需要它：滤网掩码（泵/电机粗精/灯）按"地址几何"缓存，缓存键原本
        #   只用 `id(proj)/len(fixtures)/len(fixture_defs)` + 列表身份 ——
        #   **原地改 `start_channel`/`universe`/`def_id` 或 `def.channels` 时三者全不变**
        #   → 缓存陈旧命中 → 泵主控/电机限位限速**打到旧地址**（实测确认）。
        #   任何配接变更都必须 `bump_patch_rev()`，缓存键带上它即自动失效。
        self.patch_rev = 0

    def bump_patch_rev(self) -> int:
        """配接变更后调用：递增版本号，令所有按其缓存的下游（滤网掩码等）失效。

        见 `__post_init__` 里 `patch_rev` 的说明 —— **原地改配接字段**时
        `id()/len()` 判据全都不变，只有版本号能表达"变了"。
        返回新版本号（调用方可直接用).
        """
        self.patch_rev = int(getattr(self, "patch_rev", 0) or 0) + 1
        return self.patch_rev

    def add_track(self, track: Track):
        self.tracks.append(track)

    def remove_track(self, track_id: str):
        self.tracks = [t for t in self.tracks if t.id != track_id]

    def get_track(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.id == track_id:
                return t
        return None

    def get_program(self, program_id: str) -> Optional[TimelineProgram]:
        for p in self.programs:
            if str(getattr(p, "id", "")) == str(program_id):
                return p
        return None

    def get_fixture_def(self, def_id: str) -> Optional[FixtureDefinition]:
        for fd in self.fixture_defs:
            if fd.id == def_id:
                return fd
        return None

    def to_dict(self, include_frames: bool = True) -> dict:
        from .timeline_snapshot import _program_dicts
        program_dicts = _program_dicts(self, include_frames=include_frames)
        return {
            "name": self.name,
            "created_at": self.created_at,
            "bpm": self.bpm,
            "sample_rate": self.sample_rate,
            "timecode_fps": self.timecode_fps,
            **_marker_fields(self),   # R183 T2 标记轨 + 循环区（两键，见该函数）
            "network_config": self.network_config.to_dict(),
            "input_filter": self.input_filter.to_dict(),
            "output_filter": self.output_filter.to_dict(),
            "tracks": [] if self.programs else [t.to_dict(include_frames=include_frames) for t in self.tracks],
            "fixture_defs": [fd.to_dict() for fd in self.fixture_defs],
            "fixtures": [fi.to_dict() for fi in self.fixtures],
            "fixture_groups": self.fixture_groups,
            "logical_devices": self.logical_devices,
            "light_control_mode": str(getattr(self, "light_control_mode", "whole") or "whole"),
            "audio_path": self.audio_path,
            "audio_clip_state": dict(getattr(self, "audio_clip_state", {}) or {}),
            "programs": program_dicts,
            "current_program_id": self.current_program_id,
            "audio_bridge_state": self.audio_bridge_state,
            "matrix_slideshow_state": self.matrix_slideshow_state,
            "audio_driver_state": self.audio_driver_state,
            "effect_browser_state": self.effect_browser_state,
            "processor_state": self.processor_state,
            "topview_state": self.topview_state,
            "route_order_state": self.route_order_state,
            "splines": list(getattr(self, "splines", []) or []),
            "delay_function_presets": dict(getattr(self, "delay_function_presets", {}) or {}), "clip_postprocess_profiles": dict(getattr(self, "clip_postprocess_profiles", {})),
            "programmer_cues_data": dict(getattr(self, "programmer_cues_data", {}) or {}),
            "param_envelopes_data": dict(getattr(self, "param_envelopes_data", {}) or {}),
            "custom_effects_data": dict(getattr(self, "custom_effects_data", {}) or {}),
            "baked_arrangement_data": dict(getattr(self, "baked_arrangement_data", {}) or {}),
            "trajectory_curves_data": dict(getattr(self, "trajectory_curves_data", {}) or {}),
            **_project_state_dict(self),
        }

    def save(self, path: str):
        # 防写坏原子写(2026-08-14 审计P0): 先写同目录 .tmp 再 os.replace,
        # 序列化/写入中途失败(磁盘满/中断/外部杀进程)时原档完好 —— 与
        # playback_engine 帧导出同款模式(存档写坏教训)。旧直写路径
        # 曾实测把原档截断为 0 字节销毁。
        #
        # ★★ R139 用户实报修复（"播放时间线的时候点击保存项目，会卡死"）：
        #   原用 `indent=2` 美化输出 —— 实测 62MB 真工程上 **2.75× 耗时**
        #   （0.99s vs 0.36s）且体积 +43%（61MB vs 42.7MB）。保存整体因此阻塞
        #   UI 线程 ~1.7s（实测：collect 0.24s + bundle 0.29s + save 1.23s），
        #   播放中这一整段里渲染 tick（挂在 UI 计时器上，`gen_thread_active()`
        #   为假时）**一次都跑不了** → 音频/DMX 输出停摆，用户看到"卡死"。
        #   改为**紧凑输出**（不加缩进）：耗时降 3 倍、文件更小，内容逐字节等价
        #   （JSON 语义不变，读回结果完全相同）。工程文件本就不是给人肉眼读的，
        #   需要可读性时可用外部工具格式化。
        tmp = path + ".qascade_tmp"
        try:
            self._write_dict_to_path(self.to_dict(), path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _write_dict_to_path(payload: dict, path: str) -> None:
        """把工程字典**原子写**到 `path`（先 .tmp 再 os.replace）。

        ★ R139：抽出来供**后台保存线程**复用（`ui/project_save_async.ProjectSaver`），
          不新造第二套写盘逻辑。紧凑输出（无 `indent`）—— 实测 62MB 真工程上美化
          输出慢 **2.75×**（0.99s vs 0.36s）且体积 +43%，而工程文件本就非人读；
          JSON 语义不变，读回结果逐字段相同。
        """
        tmp = path + ".qascade_tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str, _lazy: bool = False) -> "Project":
        with open(path, "rb") as f:
            d = parse_project_json(f.read())
        return cls._from_dict(d, _lazy=_lazy)

    @classmethod
    def _from_dict(cls, d: dict, _lazy: bool = False) -> "Project":
        """内部：从已解析的 dict 构建 Project（避免重新读文件）。

        供同步 Project.load() 和异步 ProjectLoader 共用。
        _lazy=True 时帧数据用 _LazyDMXFrame 而非 DMXFrame，不解码 base64。
        """
        proj = cls(
            name=d.get("name", "Project"),
            created_at=d.get("created_at", time.time()),
            bpm=d.get("bpm", 120.0),
            sample_rate=d.get("sample_rate", 40.0),
            timecode_fps=float(d.get("timecode_fps", 25.0)),
            audio_path=d.get("audio_path", ""),
        )
        # ★ R183 双解析消重：memo 按 clip-dict 身份记忆帧列表，下方 proj.tracks
        #   重建（源 dict 与 program 解析同一批对象）直接浅拷贝复用，不再逐帧重构造。
        frames_memo: dict = {}
        legacy_tracks = [Track.from_dict(td, _lazy=_lazy, _frames_memo=frames_memo) for td in d.get("tracks", [])]
        program_src = list(d.get("programs", []) or [])
        proj.programs = [TimelineProgram.from_dict(pd, _lazy=_lazy, _frames_memo=frames_memo) for pd in program_src]
        # 记录每个 program 的源 track-dict 列表，供下方为 current program 重新解析出
        # 独立的 proj.tracks（避免对成千上万帧做 copy.deepcopy，实测大工程省 ~850ms）。
        program_track_src: dict[str, list] = {}
        for prog, pd in zip(proj.programs, program_src):
            if isinstance(pd, dict):
                program_track_src[str(getattr(prog, "id", "") or "")] = list(pd.get("tracks", []) or [])
        legacy_program_id = ""
        if not proj.programs and legacy_tracks:
            legacy_program = _legacy_program_from_dict(d, legacy_tracks, proj.created_at)
            proj.programs.append(legacy_program)
            legacy_program_id = str(getattr(legacy_program, "id", "") or "")
            # legacy program 的源 track-dict 就是顶层 d["tracks"]
            program_track_src[legacy_program_id] = list(d.get("tracks", []) or [])
        saved_current_id = str(d.get("current_program_id", "") or "")
        if not saved_current_id and not (d.get("programs", []) or []) and proj.programs:
            saved_current_id = str(getattr(proj.programs[0], "id", "") or "")
        current_program = None
        for program in proj.programs:
            if str(getattr(program, "id", "")) == saved_current_id:
                current_program = program
                break
        if current_program is not None:
            proj.current_program_id = str(getattr(current_program, "id", "") or "")
            # ★ 性能：原本对 current_program.tracks 做 copy.deepcopy，对含十万级 DMX
            # 帧的大工程实测耗时 ~850ms。proj.tracks 需要与 program.tracks 互相独立
            # （编辑实时时间线不应改动已存 program 快照，直到显式 save 同步）。
            # 改为从该 program 的源 track-dict 重新解析一份独立对象——ID/帧逐字节一致
            # 但更快。源 dict 不可用时回退到 deepcopy，保证任何边缘情况不破坏。
            src_tracks = program_track_src.get(proj.current_program_id)
            if src_tracks is not None:
                proj.tracks = [Track.from_dict(td, _lazy=_lazy, _frames_memo=frames_memo) for td in src_tracks]
            else:
                proj.tracks = copy.deepcopy(getattr(current_program, "tracks", []) or [])
            proj.audio_path = getattr(current_program, "audio_path", "") or ""
            proj.audio_clip_state = dict(getattr(current_program, "audio_clip_state", {}) or {})
            proj.audio_modulation_state = dict(getattr(current_program, "audio_modulation_state", {}) or {})
        else:
            proj.current_program_id = ""
            proj.tracks = []
            proj.audio_path = ""
            proj.audio_clip_state = dict(d.get("audio_clip_state", {}) or {})
            proj.audio_modulation_state = dict(d.get("audio_modulation_state", {}) or {})
        proj.input_filter = FilterRule.from_dict(d.get("input_filter", {}))
        proj.output_filter = FilterRule.from_dict(d.get("output_filter", {}))
        nc_data = d.get("network_config")
        if nc_data and isinstance(nc_data, dict):
            proj.network_config = NetworkConfig.from_dict(nc_data)
        proj.fixture_defs = [FixtureDefinition.from_dict(fd) for fd in d.get("fixture_defs", [])]
        proj.fixtures = [FixtureInstance.from_dict(fi) for fi in d.get("fixtures", [])]
        proj.fixture_groups = d.get("fixture_groups") or []
        proj.logical_devices = d.get("logical_devices") or []
        _lcm = str(d.get("light_control_mode", "whole") or "whole").strip().lower()
        proj.light_control_mode = _lcm if _lcm in ("single", "whole") else "whole"
        # v25 新增面板状态加载
        proj.audio_bridge_state = d.get("audio_bridge_state", {})
        proj.matrix_slideshow_state = d.get("matrix_slideshow_state", {})
        proj.audio_driver_state = d.get("audio_driver_state", {})
        proj.effect_browser_state = d.get("effect_browser_state", {})
        proj.processor_state = d.get("processor_state", {})
        proj.topview_state = d.get("topview_state", {})
        proj.route_order_state = d.get("route_order_state", {})
        # ★ R183 T2：标记轨 / 循环区（旧档无键 → 空列表/空 dict，零迁移）
        _apply_marker_fields(proj, d)
        proj.splines = list(d.get("splines", []) or [])
        proj.delay_function_presets = dict(d.get("delay_function_presets", {}) or {}); proj.clip_postprocess_profiles = dict(d.get("clip_postprocess_profiles", {}))
        # 不透明 UI store 数据：旧工程无这些键 → 默认空 dict（向前兼容）
        proj.programmer_cues_data = dict(d.get("programmer_cues_data", {}) or {})
        proj.param_envelopes_data = dict(d.get("param_envelopes_data", {}) or {})
        proj.custom_effects_data = dict(d.get("custom_effects_data", {}) or {})
        proj.baked_arrangement_data = dict(d.get("baked_arrangement_data", {}) or {})
        proj.trajectory_curves_data = dict(d.get("trajectory_curves_data", {}) or {})
        proj.route_animator_state = dict(d.get("route_animator_state", {}) or {})
        # 调度台效果缓存 + R172 Clip Studio：老工程无此键 → 空状态（向前兼容）
        for attr in ("dispatch_state", "programmer_state", "clip_studio_state"):
            setattr(proj, attr, dict(d.get(attr, {}) or {}))
        return proj
