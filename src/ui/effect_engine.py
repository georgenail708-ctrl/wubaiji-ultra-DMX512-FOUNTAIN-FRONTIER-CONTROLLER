"""
effect_engine.py — DMXTimeline 效果预设引擎
============================================
提供：
  - EffectPreset 数据模型（可序列化）
  - EffectEngine  预设注册 / 计算引擎
  - 大量内置预设（波浪、矩阵图案、单点跳、三联条、音频驱动、摇杆、推杆等）
  - PatternDef    自定义矩阵图案定义
  - EffectTimeline 将预设展开为 DMXFrame 序列（用于写入时间线）

设备排序策略（来自 TopView 坐标）：
  - "linear_x"  : 按 pos_x 升序
  - "linear_y"  : 按 pos_y 升序
  - "radial"    : 按极角升序（圆形排列）
  - "grid_row"  : 先行后列（矩阵）
  - "grid_col"  : 先列后行（矩阵）
  - "index"     : 保持原始顺序
"""

from __future__ import annotations

import math
import uuid
import json
import time
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable

import numpy as np
from src.core.fixture_selection import ordered_members
from src.utils.wing_block import build_wing_block_units
from .effect_audio_runtime import offline_audio_params as _audio_offline_params
from . import effect_order_execution as _order_execution

# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────

SORT_MODES = ["index", "linear_x", "linear_y", "radial", "grid_row", "grid_col"]

# Effect 页面通用"应用方向"层。
# default 表示保持效果预设与 TopView 排序的默认方向；其余选项在进入具体
# effect 计算函数前统一重排设备队列，避免逐个预设重复实现方向参数。
from .effect_directions import (          # noqa: E402
    ADVANCED_DIRECTIONS as _ADVANCED_DIRECTIONS,
    PHASE_DIRECTIONS as _PHASE_DIRECTIONS,
    advanced_direction_order,
    direction_phases,
    is_advanced as is_advanced_direction,
    normalize as _normalize_advanced_direction,
)
from .effect_spatial_semantics import (   # noqa: E402  ★ R118 方向/宽窄统一层
    effective_params,
    is_phase_advance,
    narrowed_params,
    width_factor,
)
from .effect_presets_data_width_r141 import (   # noqa: E402  ★ R141 实测名单
    WIDTH_PARAM_PATH_PRESETS,
)
from . import effect_width_support as _width_support
from . import effect_width_capabilities as _width_capabilities
from . import effect_width_execution as _width_execution
from .effect_presets_data_angle_r141 import (   # noqa: E402  ★ R141 角度声明实测名单
    NORMALIZED_ANGLE_PARAMS,
)
from src.utils.identity_cache import (  # noqa: E402  ★ R173 性能：有界身份缓存帮手
    IdentityCache,
    fixture_ids_signature,
    bounded_put,
)


EFFECT_DIRECTIONS = [
    "default", "reverse",
    "left_to_right", "right_to_left",
    "front_to_back", "back_to_front",
    "diag_tl_br", "diag_br_tl",
    "diag_bl_tr", "diag_tr_bl",
    "center_out", "edge_in",
    # ★ 2026-09-19 用户要求「方向种类不低于 clip 的过渡方向」：补齐 clip 有而
    #   effect 缺的 9 种（语义逐字对齐 clip 的 _compute_direction_positions，
    #   见 `effect_directions` 模块头）。**追加在末尾**，既有 12 项的索引不变。
    "clockwise", "counterclockwise",
    "alternate", "alternate_block", "wave", "cascade", "reverse_cascade",
    "zigzag", "random",
]

EFFECT_APPLICATION_SCOPES = ["both", "whole", "sequence"]
# 方向性分组的空间作用域：per_shape=按各自选择组坐标系，global=整体坐标系
EFFECT_SPATIAL_SCOPES = ["per_shape", "global"]

CH_TYPES = ["dimmer", "R", "G", "B", "W", "motor_x", "motor_y",
            "motor_z", "valve", "pump"]

# ─────────────────────────────────────────────────────────────────────────────
#  跑动覆盖遮罩门(route_mask) 提供者
# ─────────────────────────────────────────────────────────────────────────────
# 跑动覆盖掩码两种形态(见 set_route_mask):
#   * np.uint8 数组(索引=设备注册序, 0=未覆盖 255=全覆盖) — 旧语义, 仅供老测试
#   * dict[str,int](键=设备 id) — 生产口径, 面板每帧用 route_mask_map() 注册
# None(=不遮罩); effect 在唯一输出收口点(_finish)对强度通道乘掩码, 未覆盖设备
# 输出=0。mask=None 时逐字节零影响(铁律)。与 effect_browser/网络层 chain 对齐:
# 本模块只负责在 EffectFrame.values 写定后过滤, 不改任何 effect 计算函数/排序/
# 方向/聚块链。
_route_mask: np.ndarray | None = None
_route_mask_map: dict[str, int] | None = None


def set_route_mask(mask) -> None:
    """注册跑动覆盖掩码(遮罩门)。支持三种形态:

    * np.ndarray → 存 _route_mask(旧语义: 索引=设备注册序, 只给老测试用)
    * dict[str, int] → 存 _route_mask_map(按设备 id, 生产唯一口径)
    * None → 两个都清空(不遮罩, 效果输出逐字节不变)

    调用方(面板)在 runner_engine.tick 后用 route_mask_map() 注册; 数组形态只因
    既有桥接测试保留, 新代码禁止再用数组(索引口径与 effect fixtures 下标错配)。
    """
    global _route_mask, _route_mask_map
    if mask is None:
        _route_mask = None
        _route_mask_map = None
    elif isinstance(mask, np.ndarray):
        _route_mask = mask
        _route_mask_map = None
    elif isinstance(mask, dict):
        # 生产路径 route_mask_map() 已经是 str->int, 直接复用；不再每帧对
        # 11382 项做全量 isinstance 扫描(开 Programmer 时卡顿根因之一)。
        _route_mask_map = mask
        _route_mask = None
    else:
        raise TypeError(f"unsupported route mask type: {type(mask).__name__}")


def get_route_mask():
    """返回当前跑动覆盖掩码(测试/诊断用)。

    生产口径为 dict[str,int]（按设备 id）；旧数组口径仅老测试使用。
    返回当前实际生效的那一种；无掩码返回 None。
    """
    if _route_mask_map is not None:
        return dict(_route_mask_map)
    return _route_mask


def _smoothstep_interp(a: float, b: float, u: float) -> float:
    """smoothstep 插值（VCV SMTH 语义；3u²-2u³ 缓动）。"""
    u = max(0.0, min(1.0, float(u)))
    s = u * u * (3.0 - 2.0 * u)
    return float(a) + (float(b) - float(a)) * s


def _det_rand(x: float) -> float:
    """确定性伪随机 [0,1)：同一相位-时间永远得同一值(录制/回放/导出可复现)，
    相邻输入经 splitmix 雪崩去相关，视觉上仍是随机闪烁。替代不可复现的 random.random()。"""
    k = int(round(float(x) * 100000.0)) & 0xFFFFFFFFFFFFFFFF
    k = (k ^ (k >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    k = (k ^ (k >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    k = (k ^ (k >> 31)) & 0xFFFFFFFFFFFFFFFF
    return (k & 0xFFFFFFFF) / 4294967296.0


WAVE_FUNCS: Dict[str, Callable] = {
    "Sine":     lambda t: (math.sin(t * 2 * math.pi) + 1) / 2,
    "Square":   lambda t: 1.0 if (t % 1.0) < 0.5 else 0.0,
    "Sawtooth": lambda t: t % 1.0,
    "Triangle": lambda t: 1 - abs(2 * (t % 1.0) - 1),
    "Random":   _det_rand,
    "Cosine":   lambda t: (math.cos(t * 2 * math.pi) + 1) / 2,
    "Bounce":   lambda t: abs(math.sin(t * math.pi)),
    "Expo":     lambda t: (math.exp(t % 1.0) - 1) / (math.e - 1),
    # R183 成熟化轮②（标准 LFO 波形表语义；全部复用 _det_rand 确定性源）
    "Shark Tooth": lambda t: (1 - math.exp(-5.0 * (t % 1.0))) / (1 - math.exp(-5.0)),
    "S&H":         lambda t: _det_rand(math.floor(t)),
    "Wander":      lambda t: _smoothstep_interp(_det_rand(math.floor(t)),
                                                _det_rand(math.floor(t) + 1),
                                                t % 1.0),
}

# ─────────────────────────────────────────────────────────────────────────────
#  图案定义（用于矩阵效果）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PatternDef:
    """自定义矩阵图案（rows × cols 的 0/1 网格）"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Pattern"
    rows: int = 4
    cols: int = 4
    cells: List[List[int]] = field(default_factory=list)  # cells[row][col] = 0/1

    def __post_init__(self):
        if not self.cells:
            self.cells = [[0] * self.cols for _ in range(self.rows)]

    def get(self, row: int, col: int) -> int:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.cells[row][col]
        return 0

    def set(self, row: int, col: int, val: int):
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.cells[row][col] = val

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "rows": self.rows, "cols": self.cols, "cells": self.cells}

    @classmethod
    def from_dict(cls, d: dict) -> "PatternDef":
        p = cls(id=d.get("id", str(uuid.uuid4())),
                name=d.get("name", "Pattern"),
                rows=d.get("rows", 4), cols=d.get("cols", 4))
        p.cells = d.get("cells", [[0]*p.cols for _ in range(p.rows)])
        return p

    def as_flat_mask(self, n_fixtures: int) -> List[float]:
        """将图案展平为 n_fixtures 长度的 0/1 列表（按行扫描）"""
        flat = []
        for row in self.cells:
            flat.extend(row)
        # 截断或重复填充到 n_fixtures
        if len(flat) == 0:
            return [0.0] * n_fixtures
        result = []
        for i in range(n_fixtures):
            result.append(float(flat[i % len(flat)]))
        return result


# ─────────────────────────────────────────────────────────────────────────────
#  内置图案库
# ─────────────────────────────────────────────────────────────────────────────

def _make_builtin_patterns() -> Dict[str, PatternDef]:
    patterns: Dict[str, PatternDef] = {}

    def add(name: str, rows: int, cols: int, cells: List[List[int]]):
        p = PatternDef(id=f"builtin_{name}", name=name, rows=rows, cols=cols)
        p.cells = cells
        patterns[p.id] = p

    # 全亮
    add("全亮", 4, 4, [[1,1,1,1]]*4)
    # 棋盘格
    add("棋盘格", 4, 4, [
        [1,0,1,0],[0,1,0,1],[1,0,1,0],[0,1,0,1]])
    # 对角线
    add("对角线↘", 4, 4, [
        [1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
    add("对角线↗", 4, 4, [
        [0,0,0,1],[0,0,1,0],[0,1,0,0],[1,0,0,0]])
    # 十字
    add("十字", 4, 4, [
        [0,1,1,0],[1,1,1,1],[1,1,1,1],[0,1,1,0]])
    # X形
    add("X形", 4, 4, [
        [1,0,0,1],[0,1,1,0],[0,1,1,0],[1,0,0,1]])
    # 边框
    add("边框", 4, 4, [
        [1,1,1,1],[1,0,0,1],[1,0,0,1],[1,1,1,1]])
    # 中心点
    add("中心点", 4, 4, [
        [0,0,0,0],[0,1,1,0],[0,1,1,0],[0,0,0,0]])
    # 水平条纹
    add("水平条纹", 4, 4, [
        [1,1,1,1],[0,0,0,0],[1,1,1,1],[0,0,0,0]])
    # 垂直条纹
    add("垂直条纹", 4, 4, [
        [1,0,1,0],[1,0,1,0],[1,0,1,0],[1,0,1,0]])
    # 三角形
    add("三角形↑", 4, 4, [
        [0,0,0,0],[0,1,1,0],[1,1,1,1],[0,0,0,0]])
    # Z形
    add("Z形", 4, 4, [
        [1,1,0,0],[0,1,0,0],[0,1,0,0],[0,0,1,1]])
    # 随机稀疏
    add("随机稀疏", 4, 4, [
        [1,0,0,1],[0,0,1,0],[0,1,0,0],[1,0,0,1]])
    # 密集
    add("密集3/4", 4, 4, [
        [1,1,1,0],[1,1,0,1],[1,0,1,1],[0,1,1,1]])
    # 单行
    add("第1行", 4, 4, [[1,1,1,1],[0,0,0,0],[0,0,0,0],[0,0,0,0]])
    add("第2行", 4, 4, [[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]])
    add("第3行", 4, 4, [[0,0,0,0],[0,0,0,0],[1,1,1,1],[0,0,0,0]])
    add("第4行", 4, 4, [[0,0,0,0],[0,0,0,0],[0,0,0,0],[1,1,1,1]])

    return patterns

BUILTIN_PATTERNS: Dict[str, PatternDef] = _make_builtin_patterns()


# ─────────────────────────────────────────────────────────────────────────────
#  预设参数描述符
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParamSpec:
    """单个参数的描述（用于 UI 自动生成控件）"""
    key: str
    label: str
    type: str          # "float" | "int" | "bool" | "choice" | "pattern"
    default: Any
    min_val: float = 0.0
    max_val: float = 1.0
    step: float = 0.01
    choices: List[str] = field(default_factory=list)
    unit: str = ""
    tooltip: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  效果预设定义
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EffectPreset:
    """
    一个效果预设的完整描述。
    包含：元数据、参数规格、计算函数引用（按 preset_id 查找）
    """
    id: str
    name: str
    category: str          # 已在注册入口统一为中文规范分类，例如"灯光/颜色"
    description: str
    target_channels: List[str]   # 适用的通道类型
    params: List[ParamSpec]
    icon: str = "⚡"
    tags: List[str] = field(default_factory=list)

    def default_params(self) -> Dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "category": self.category,
            "params": {p.key: p.default for p in self.params},
        }


CATEGORY_ALIASES: Dict[str, str] = {
    # old English categories
    "Wave": "通用/波形",
    "Chase": "通用/跑灯",
    "Fader": "通用/渐变",
    "Custom": "通用/编排",
    "Fountain": "水景/泵",
    "Color": "灯光/颜色",
    # synonymous Chinese → canonical (13 categories)
    "灯光/彩色": "灯光/颜色",
    "灯光/颜色": "灯光/颜色",
    "灯光/亮度": "灯光/明暗",
    "灯光/呼吸": "灯光/明暗",
    "灯光/闪烁": "灯光/明暗",
    "灯光/氛围": "灯光/颜色",
    "灯光/开合": "灯光/明暗",
    "电机/形象": "电机/单体",
    "电机/造型": "电机/单体",
    "电机/旋转": "电机/编组",
    "电机/扇形": "电机/编组",
    "电机/延时": "电机/编组",
    "电机/圆摇": "电机/编组",
    "电机/位置": "电机/单体",
    "电机/分组": "电机/编组",
    "电机/圆形": "电机/编组",
    "电机/抖动": "电机/编组",
    "电机/波浪": "电机/单体",
    "电机/渐变": "电机/编组",
    "电机/脉冲": "电机/编组",
    "电机/追逐": "电机/编组",
    "电机/轨迹": "电机/单体",
    "电机/波形": "电机/单体",
    "电机/扇": "电机/编组",
    "电机/开合": "电机/编组",
    "电机/交叉": "电机/编组",
    "电机/龙摆": "电机/编组",
    "电机/高级": "电机/编组",
    "电机/Cue": "电机/单体",
    "通用/喷泉": "水景/泵",
    "通用/交替覆盖": "通用/编排",
    "通用/序列": "通用/编排",
    "通用/摇杆": "通用/编排",
    "通用/位置": "通用/编排",
    "通用/自定义": "通用/编排",
    # R172 三族新分类（规范名自映射 + 旧同义词归一）
    "通用/随机": "通用/随机",
    "通用/变速": "通用/变速",
    "通用/级联": "通用/级联",
    "通用/伪随机": "通用/随机",
    "通用/随机增强": "通用/随机",
    "通用/忽快忽慢": "通用/变速",
    "通用/可变速度": "通用/变速",
    "通用/折返": "通用/级联",
    "通用/折返级联": "通用/级联",
    "水景/喷射": "水景/泵",
    "水景/波浪": "水景/泵",
    "水景/脉冲": "水景/泵",
    "水景/阀门": "水景/阀",
    "泵/跑泉": "水景/泵",
    "泵/波浪": "水景/泵",
    "泵/渐变": "水景/泵",
    "泵/脉冲": "水景/泵",
    "阀门/开合": "水景/阀",
    "阀门/脉冲": "水景/阀",
    "阀门/跑泉": "水景/阀",
    "阀门/渐变": "水景/阀",
}

CATEGORY_ORDER = [
    # 通用（channel-agnostic）
    "通用/波形", "通用/跑灯", "通用/渐变",
    # ★ R172 三族新分类（插在 通用/渐变 之后）
    "通用/随机", "通用/变速", "通用/级联",
    "通用/编排", "通用/高级",
    "通用/音频",          # ★ R132 真音频分支（无音频不出效果，见 effect_presets_audio_true）
    # 灯光
    "灯光/颜色", "灯光/明暗", "灯光/跑灯",
    # 电机
    "电机/编组", "电机/序列", "电机/单体",
    # 水景
    "水景/泵", "水景/阀", "水景/编程秀",
    # 音频反应（新增）
    "音频反应",
    # 自定义
    "自定义/我的效果",
]


def normalize_effect_category(category: str) -> str:
    """统一效果库分类名称，兼容旧英文分类和多次扩展产生的同义分类。

    全部输出均为 CATEGORY_ORDER 中的规范分类（含空/纯空白输入的兜底）。
    """
    cat = str(category or "通用/编排").strip()
    if not cat:
        return "通用/编排"
    return CATEGORY_ALIASES.get(cat, cat)


def effect_category_sort_key(category: str):
    """稳定的分类显示排序。未知分类排在规范分类之后。"""
    cat = normalize_effect_category(category)
    try:
        return (0, CATEGORY_ORDER.index(cat), cat)
    except ValueError:
        return (1, len(CATEGORY_ORDER), cat)


# ─────────────────────────────────────────────────────────────────────────────
#  效果计算结果
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EffectFrame:
    """
    一帧效果数据：每个设备每个通道的值 (0-255)
    values[fixture_idx][ch_type] = int(0-255)
    """
    values: List[Dict[str, int]] = field(default_factory=list)
    t: float = 0.0   # 时间戳（秒）


# ─────────────────────────────────────────────────────────────────────────────
#  排序工具
# ─────────────────────────────────────────────────────────────────────────────

def sort_fixtures(fixtures: list, positions: Dict[str, dict],
                  mode: str,
                  base_order_ids: Optional[List[str]] = None,
                  skip_base_order: bool = False) -> List[Tuple[int, object]]:
    """
    ?? [(original_idx, fixture_instance), ...] ????????
    positions: {fixture_id: {"pos_x": float, "pos_y": float}}
    base_order_ids: TopView/?????????????
    """
    def pos(fi):
        p = positions.get(fi.id, {})
        return p.get("pos_x", 0.0), p.get("pos_y", 0.0)

    indexed = list(enumerate(fixtures))

    # ★ 2026-08-11 #18 跳过编号排序：编号序(整块重排 + base_key 前置)全部失效，
    #   只按设备坐标按应用方向排序。默认 False = 原逻辑零影响。
    if skip_base_order:
        base_order_ids = None

    if base_order_ids:
        by_id = {str(getattr(fi, "id", "")): (idx, fi) for idx, fi in indexed}
        ordered = []
        for fid in base_order_ids:
            item = by_id.get(str(fid))
            if item is not None:
                ordered.append(item)
        if ordered:
            present = {str(fid) for fid in base_order_ids}
            ordered.extend(
                (idx, fi) for idx, fi in indexed
                if str(getattr(fi, "id", "")) not in present
            )
            return ordered
    base_rank: Dict[str, int] = {}
    if base_order_ids:
        for i, fid in enumerate(base_order_ids):
            sfid = str(fid)
            if sfid not in base_rank:
                base_rank[sfid] = i
    fallback_rank = len(base_rank) + len(indexed) + 1000

    def base_key(iv):
        fi = iv[1]
        if skip_base_order:
            # skip：编号层失效（各坐标模式里恒 0 → 坐标成主排序键）
            return 0
        return base_rank.get(getattr(fi, "id", ""), fallback_rank + iv[0])

    def key_linear_x(iv):
        x, y = pos(iv[1])
        return (base_key(iv), x, y, iv[0])

    def key_linear_y(iv):
        x, y = pos(iv[1])
        return (base_key(iv), y, x, iv[0])

    def key_radial(iv):
        x, y = pos(iv[1])
        return (base_key(iv), math.atan2(y, x), x, y, iv[0])

    def key_grid_row(iv):
        x, y = pos(iv[1])
        return (base_key(iv), round(y / 50), x, iv[0])

    def key_grid_col(iv):
        x, y = pos(iv[1])
        return (base_key(iv), round(x / 50), y, iv[0])

    if mode == "linear_x":
        indexed.sort(key=key_linear_x)
    elif mode == "linear_y":
        indexed.sort(key=key_linear_y)
    elif mode == "radial":
        indexed.sort(key=key_radial)
    elif mode == "grid_row":
        indexed.sort(key=key_grid_row)
    elif mode == "grid_col":
        indexed.sort(key=key_grid_col)
    else:
        # ★★ R117：skip 时「序号」走纯空间键（= 线阵X 键；base_key 恒 0 →
        #   键 (x, y, 下标)）。旧键 (0, iv[0]) 只剩原始下标 = 开关空操作。
        indexed.sort(key=key_linear_x
                     if skip_base_order else lambda iv: (base_key(iv), iv[0]))
    return indexed


# ★ 2026-09-12：方向选项 → 坐标轴映射（Effect 页方向语义的唯一真源）。
#   必须与引擎层 clip 方向别名表 playback_engine._CLIP_DIRECTION_ALIASES 保持
#   一致，使「Effect 页方向」与「时间线/clip 方向」语义同一；改任一处须同步
#   另一处（tests/test_effect_direction_semantics.py 交叉断言护住）。
#   取值 (轴, 是否反向)：ltr/rtl 走 x 轴；ftb/btf 走 y 轴（画布 y = −世界 Z，
#   与 clip 侧 _linear_direction_positions 的 use_y_axis 一致）；斜向按引擎既有
#   别名归入 ltr/rtl（diag_tl_br/diag_bl_tr→ltr，diag_br_tl/diag_tr_bl→rtl）。
_EFFECT_DIRECTION_AXIS: dict[str, tuple[str, bool]] = {
    "left_to_right": ("x", False),
    "right_to_left": ("x", True),
    "front_to_back": ("y", False),
    "back_to_front": ("y", True),
    "diag_tl_br": ("x", False),
    "diag_bl_tr": ("x", False),
    "diag_br_tl": ("x", True),
    "diag_tr_bl": ("x", True),
}

# 由坐标定序的方向（含 center_out/edge_in 的距离序）；reverse 单独处理（整表反转）。
_EFFECT_DIRECTION_COORD = frozenset(_EFFECT_DIRECTION_AXIS) | {"center_out", "edge_in"}
_EFFECT_DIRECTION_ALL = (_EFFECT_DIRECTION_COORD | {"reverse"}
                         | frozenset(_ADVANCED_DIRECTIONS))


def _effect_fixture_xy(positions: dict[str, dict], fi: object) -> tuple[float, float]:
    """取设备在坐标表中的 (x, y)；缺失/非法一律按 (0,0)（与 sort_fixtures 同宽厚）。"""
    if not positions:
        return 0.0, 0.0
    p = positions.get(str(getattr(fi, "id", "") or "")) or {}
    try:
        return float(p.get("pos_x", 0.0) or 0.0), float(p.get("pos_y", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _effect_direction_block_order(block: list[tuple[int, object]],
                                  positions: dict[str, dict],
                                  direction: str) -> list[tuple[int, object]]:
    """对**单个块**（一个微单元 / 整表）按方向重排；块内原序只作平局决胜。

    - ltr/rtl/ftb/btf/diag_*：按坐标轴升/降序（轴与反向由 _EFFECT_DIRECTION_AXIS 定）；
    - center_out/edge_in：按到「块坐标中心」的距离升/降序（主轴 = 跨度较大者，
      与 clip 侧 ctr/rtc 的 dominant-axis 规则一致）；
    - reverse：整块反转（仅整表调用时使用）。
    """
    if len(block) <= 1:
        return list(block)
    if direction == "reverse":
        return list(reversed(block))
    # ★ 进阶方向（顺时针/交替/波浪/级联/折返/随机…）：语义见 effect_directions
    _adv = advanced_direction_order(block, positions, direction)
    if _adv is not None:
        return _adv
    xy = [_effect_fixture_xy(positions, it[1]) for it in block]
    if direction in _EFFECT_DIRECTION_AXIS:
        axis, rev = _EFFECT_DIRECTION_AXIS[direction]
        primary = 0 if axis == "x" else 1
        span_primary = max(p[primary] for p in xy) - min(p[primary] for p in xy)
        # ★ 主轴无跨度时回退到另一轴，且**反向仍作用于实际使用的轴**——与引擎层
        #   clip 侧 _linear_direction_positions 的 primary/fallback 语义一致
        #   （否则纯 x 线阵上 back_to_front 会退化成 left_to_right，方向失效）。
        if span_primary <= 1e-6:
            primary = 1 - primary
        secondary = 1 - primary

        def _axis_key(pair):
            i, _it = pair
            pv = -xy[i][primary] if rev else xy[i][primary]
            return (pv, xy[i][secondary], i)

        return [it for _i, it in sorted(enumerate(block), key=_axis_key)]
    # center_out / edge_in：**到块质心的径向距离**（升序/降序）。
    #
    # ★★ 2026-09-20 用户实报修复（"我这里布局很对称，但中间到两边这些效果怎么不齐"）：
    #   旧实现把**单轴投影距离**（`|x - x_center|` 或 `|y - y_center|`，轴取跨度较大者）
    #   当成"到中心的距离"。在**圆排/同心环/斜向对称**布局上二者不等价 ——
    #   实测随机布局中 96.8% 的顺序不同，圆排上更会跨半径混排（外圈靠轴的设备
    #   排在里圈偏离轴的设备之前）→ 用户看到的"中到边不齐"。
    #   修：用真径向距离 `hypot(x - cx, y - cy)`（cx/cy = 块质心），
    #   语义与 clip 侧 ctr/rtc 的"到中心"一致，且在轴对齐网格上与原实现同序
    #   （纯线阵/规整网格：径向与单轴投影单调同序）。
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    def _dist_key(pair):
        i, _it = pair
        d = math.hypot(xy[i][0] - cx, xy[i][1] - cy)
        return (-d if direction == "edge_in" else d, i)

    return [it for _i, it in sorted(enumerate(block), key=_dist_key)]


def _effect_direction_blocks(items: list[tuple[int, object]],
                             unit_groups) -> list[list[tuple[int, object]]]:
    """按 _unit_groups 把（已由 apply_spatial_scope 聚块的）队列切成连续块。

    per_shape 时 apply_spatial_scope 已把每个微单元排成相邻块；此处按「成员所属
    单元」把连续同单元项并为一块，**块序不变**——方向只在块内重排，单元块不被
    撤销（审查 P2-2 语义）。不在任何单元中的项各自成块（保持原相对位置）。
    无 unit_groups（global/未解析坐标系）→ 单块 = 整表。
    """
    if not unit_groups:
        return [list(items)]
    fid_unit: dict[str, int] = {}
    for ui, group in enumerate(unit_groups):
        for f in (group or ()):
            sf = str(f)
            if sf:
                fid_unit.setdefault(sf, ui)
    blocks: list[list[tuple[int, object]]] = []
    cur: list[tuple[int, object]] = []
    cur_key = None
    for it in items:
        fid = str(getattr(it[1], "id", "") or "")
        key = fid_unit.get(fid, ("__solo__", id(it[1])))
        if cur and key != cur_key:
            blocks.append(cur)
            cur = []
        cur.append(it)
        cur_key = key
    if cur:
        blocks.append(cur)
    return blocks


def _has_any_coords(items: list, positions: Dict[str, dict]) -> bool:
    """是否至少有一台设备带坐标（决定走坐标定序还是序号兜底）。"""
    if not positions:
        return False
    return any(str(getattr(it[1], "id", "") or "") in positions for it in items)


def _apply_advanced_direction(items: list, positions: Dict[str, dict],
                              direction: str, unit_groups) -> list:
    """进阶方向：块内应用（cw/ccw 重排；相位型由 `_inject_matrix_coords` 注入）。

    ★ 与坐标无关（相位曲线只看块内序号）→ 无坐标也生效，否则"选了没反应"
      （用户历史反复报过的形态）。
    """
    out: list = []
    for blk in _effect_direction_blocks(items, unit_groups):
        out.extend(advanced_direction_order(blk, positions, direction) or blk)
    return out


def apply_effect_direction(sorted_fixtures: List[Tuple[int, object]],
                           positions: Dict[str, dict],
                           direction: str,
                           unit_groups=None) -> List[Tuple[int, object]]:
    """在效果默认排序结果之上应用通用方向变换。

    direction 来自 Effect 页面全局方向选择，默认值 default 不改变预设原本
    的方向。对于左/右、前/后、斜向和中心扩散类选项，使用 TopView 坐标
    重新排列 sorted_fixtures；没有坐标时自动退回原队列或简单反向队列。

    unit_groups：per_shape 的微单元成员表（params["_unit_groups"]）。给定且
    本表已聚块时，方向**只在各单元块内部**生效、块序保持不变（不撤销
    apply_spatial_scope 的聚块，审查 P2-2）。global/无坐标系为单块=整表。

    ★ 修复（2026-09-12）：旧实现虽接收 positions 却从不使用——left_to_right /
    front_to_back / diag_tl_br / diag_bl_tr 恒为 no-op（选方向「无反应」），
    right_to_left/back_to_front/diag_br_tl/diag_tr_bl 仅整表反转（与坐标无关），
    center_out/edge_in 按**序号**而非坐标取中心（斜排/非均匀布局时方向错乱）。
    现按坐标轴/距离定序，并与 clip 侧方向别名语义对齐（见 _EFFECT_DIRECTION_AXIS）。
    """
    direction = str(direction or "default")
    items = list(sorted_fixtures or [])
    if len(items) <= 1 or direction == "default" or direction not in _EFFECT_DIRECTION_ALL:
        return items
    # 坐标全缺 → 保留旧兜底语义（左/前/斜正类不动作、右/后/斜反类整表反转、
    # 中心类按序号），避免零坐标调用方（纯序号序、无 TopView）行为突变。
    has_coords = _has_any_coords(items, positions)
    if _normalize_advanced_direction(direction) in _ADVANCED_DIRECTIONS:
        return _apply_advanced_direction(items, positions, direction, unit_groups)
    if not has_coords:
        if direction == "reverse" or direction in {
                "right_to_left", "back_to_front", "diag_br_tl", "diag_tr_bl"}:
            return list(reversed(items))
        if direction in {"left_to_right", "front_to_back", "diag_tl_br", "diag_bl_tr"}:
            return items
        center = (len(items) - 1) / 2.0
        return [
            item for _, item in sorted(
                enumerate(items),
                key=lambda pair: (abs(pair[0] - center), pair[0]),
                reverse=(direction == "edge_in"),
            )
        ]
    if direction == "reverse":        # 整表反转（块仍连续），既有语义
        return list(reversed(items))
    out: list[tuple[int, object]] = []
    for block in _effect_direction_blocks(items, unit_groups):
        out.extend(_effect_direction_block_order(block, positions, direction))
    return out


def normalize_spatial_scope(scope: Any, light_control_mode: Any = None) -> str:
    """归一化「坐标系」参数为 per_shape / global（与 clip 侧**同式**）。

    真源：`playback_engine._clip_spatial_scope`（clip 的 fade_group_spatial_scope）。

    ★★ 2026-09-20 用户追问修复（"整体坐标系下的和单独坐标系下的也对吗"）：
      `follow` 哨兵必须按**工程 light_control_mode** 解析 ——
      `single`(单控灯)→ per_shape(各自坐标系)；`whole`(整体控灯)→ global(整体坐标系)。
      **clip 侧一直是这样做的**，而 effect 侧此前把 follow 一律当 global：
      同一份工程、同一个"跟随全局灯控"，clip 走各自坐标系、effect 走整体坐标系
      —— 两边行为不一致（单控灯工程下 effect 的 follow 是错的）。
      现补 `light_control_mode` 参数：给了就按 clip 同式解析；
      **未给**（无工程上下文的调用方）→ 沿用旧行为按 global（零回归）。
    """
    scope = str(scope or "").strip().lower()
    aliases = {
        "shape": "per_shape",
        "local": "per_shape",
        "group": "per_shape",
        "fixture_group": "per_shape",
        "overall": "global",
        "world": "global",
    }
    scope = aliases.get(scope, scope)
    if scope in ("follow", "follow_global", ""):
        mode = str(light_control_mode or "").strip().lower()
        if not mode:
            return "global"            # 无工程上下文 → 旧行为（零回归）
        return "global" if mode == "whole" else "per_shape"
    return scope if scope in ("per_shape", "global") else "global"


def _raw_xy_map(positions: Dict[str, dict]) -> Dict[str, tuple]:
    """工程坐标 → {设备id: (x, y)}（非法项跳过；与既有实现同宽容度）。"""
    raw: dict[str, tuple] = {}
    for fid, p in (positions or {}).items():
        if not isinstance(p, dict):
            continue
        try:
            raw[str(fid)] = (float(p.get("pos_x", 0.0)), float(p.get("pos_y", 0.0)))
        except Exception:
            continue
    return raw


def _inject_phase_direction(params: Any, raw: Dict[str, tuple]) -> bool:
    """相位曲线型方向 → 写 `_matrix_coords`；非该类型返回 False（不改动）。

    ★ 2026-09-19（用户要求方向种类不低于 clip）：奇偶交替/块交替/波浪/级联/
      反向级联/折返/随机 是 clip 的相位曲线 —— 不换设备顺序，只改每台相位值。
    """
    phases = direction_phases_for(params, raw)
    if phases is None:
        return False
    params["_matrix_coords"] = phases
    params["_matrix_x_span"] = (min(phases.values()), max(phases.values()))
    return True


def direction_phases_for(params: Any, raw: Dict[str, tuple]):
    """进阶**相位型**方向 → {设备id: 相位(0..n-1)}；非该类型返回 None。

    ★ 对应 clip 的 `playback_engine._compute_direction_positions`：对 K 个元素算
      `pos=linspace(0,1,K)` 再按方向整形；此处 K = 设备数、元素序 = `raw` 插入序
      （= positions 遍历序，稳定），结果乘 (n-1) → 与 seq_i 同量纲。
      **不改变设备顺序**：换序会让级联/波浪语义错误（第一版我按排序实现，
      被自己的回归测试当场抓出"选了没反应"）。
    """
    if not isinstance(params, dict) or not raw:
        return None
    direction = str(params.get("_app_direction", "default") or "default")
    if not is_advanced_direction(direction):
        return None
    if _normalize_advanced_direction(direction) not in _PHASE_DIRECTIONS:
        return None           # cw/ccw 是重排型，走 apply_effect_direction
    keys = [str(fid) for fid in raw.keys()]
    return direction_phases(direction, keys, n=len(keys))


def _inject_matrix_coords(params: dict[str, Any],
                          positions: dict[str, dict]) -> None:
    """global 整体坐标系：把方向语义投影成连续相位源（默认 = 全局 pos_x）。

    根因：波浪/追逐类 kernel 经 spatial_phase_index() 读的是**注入坐标**（按设备
    id），不是列表 seq_i；apply_effect_direction 只重排列表 → 旧实现在 global 下
    「切方向输出逐字节不变」（用户「选方向无反应」）。故此处按 _app_direction
    直接把方向写进相位源：轴/反向复用 _EFFECT_DIRECTION_AXIS（与 per_shape 坐标
    定序、与引擎 _CLIP_DIRECTION_ALIASES 同一语义）——ltr/rtl→x 正/反，
    ftb/btf→y 正/反，diag_* 按别名归 x，reverse→x 反（别名=rtl），
    center_out/edge_in→主轴(跨度大者)距中心升/降。
    default/未知方向 → 原 pos_x 投影逐字节不变（零回归）。
    """
    if not isinstance(params, dict) or not positions:
        return
    raw = _raw_xy_map(positions)
    if not raw:
        return
    direction = str(params.get("_app_direction", "default") or "default")
    if _inject_phase_direction(params, raw):      # 相位曲线型（见 effect_directions）
        return
    axis_rev = _EFFECT_DIRECTION_AXIS.get(direction)
    if direction == "reverse":
        # 与 _CLIP_DIRECTION_ALIASES 的 reverse→rtl 同义，不另立语义。
        axis_rev = _EFFECT_DIRECTION_AXIS["right_to_left"]
    if axis_rev is not None:
        axis, rev = axis_rev
        i = 0 if axis == "x" else 1
        vals = {f: v[i] for f, v in raw.items()}
        # 主轴无跨度 → 回退另一轴，反向仍作用于实际轴（与 apply_effect_direction /
        # clip 侧 primary/fallback 一致，否则单排线阵上 ftb/btf 方向失效）。
        if max(vals.values()) - min(vals.values()) <= 1e-6:
            vals = {f: v[1 - i] for f, v in raw.items()}
        if rev:
            vals = {f: -v for f, v in vals.items()}
    elif direction in ("center_out", "edge_in"):
        xs = [v[0] for v in raw.values()]
        ys = [v[1] for v in raw.values()]
        use_x = (max(xs) - min(xs)) >= (max(ys) - min(ys))
        lo, hi = (min(xs), max(xs)) if use_x else (min(ys), max(ys))
        center = (lo + hi) / 2.0
        j = 0 if use_x else 1
        vals = {f: abs(v[j] - center) for f, v in raw.items()}
        if direction == "edge_in":
            vals = {f: -v for f, v in vals.items()}
    else:
        vals = {f: v[0] for f, v in raw.items()}
    params["_matrix_coords"] = vals
    params["_matrix_x_span"] = (min(vals.values()), max(vals.values()))


def spatial_phase_index(params: Any, fid: str, seq_i: int, n: int) -> float:
    """空间相位源：global 整体坐标系 → 全局矩阵 x 连续投影；其余 → seq_i。

    量纲与 seq_i 一致（0..n-1），phase_step 语义不变：global 下相邻设备相位差
    ∝ 全局坐标差 → 波浪整体扫过全矩阵；per_shape/无坐标/坐标无跨度时返回
    seq_i，与旧行为逐字节一致（每台设备自己的圆）。
    """
    if not isinstance(params, dict):
        return float(seq_i)
    coords = params.get("_matrix_coords")
    if not isinstance(coords, dict):
        return float(seq_i)
    x = coords.get(str(fid))
    if x is None:
        return float(seq_i)
    span = params.get("_matrix_x_span", (0.0, 1.0))
    try:
        lo, hi = float(span[0]), float(span[1])
    except Exception:
        return float(seq_i)
    if hi - lo <= 1e-9:
        return float(seq_i)
    x_norm = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return x_norm * max(1, n - 1)


# ══════════════════════════════════════════════════════════════════════════
# R165/R170 子集 delay 相位：**TopView 里选中的那些设备**就是子集，它们相对母集合
# 整体错开一个波形相位差。只有这一个参数 —— 没有"子集数"、没有"子集来源"。
# ══════════════════════════════════════════════════════════════════════════

#: 「子集 delay 相位」参数名（0 = 关）。
_SUBSET_PHASE_KEY = "subset_delay_phase"
#: 运行时注入的「子集设备 id 列表」（由 effect 面板从 TopView 选择注入，不进存档）。
_SUBSET_IDS_KEY = "_subset_ids"


def _subset_marked_ids(params: Any) -> List[str]:
    """从 params 取本次的**子集设备 id**（TopView 里选中的那些）；无 → 空表。

    子集的唯一真源就是这个列表 —— 用户在 TopView 里选中哪些设备，哪些设备就是子集。
    """
    if not isinstance(params, dict):
        return []
    raw = params.get(_SUBSET_IDS_KEY)
    if not raw:
        return []
    try:
        out: List[str] = []
        seen: set = set()
        for item in raw:
            s = str(item or "")
            if s and s not in seen:
                out.append(s); seen.add(s)
        return out
    except Exception:
        return []


def _subset_phase_deg(params: Any) -> float:
    """「子集 delay 相位」的度数（0-360）；坏值 → 0。"""
    if not isinstance(params, dict):
        return 0.0
    try:
        return float(params.get(_SUBSET_PHASE_KEY, 0.0) or 0.0)
    except Exception:
        return 0.0


def subset_split_enabled(params: Any) -> bool:
    """该次计算是否启用子集相位差。

    ★★ R170 定案（用户纠正）：**判据只有两个** —— ① 相位 ≠ 0；② TopView 里有选中的子集。
      旧实现多加了"子集数 > 1"的门（默认 1），于是用户"明明选了子集、调了 delay 也没反应"
      —— 因为那扇门根本没开。用户原话：
        "effect 页面里面什么选择子集来源和子集数，这些完全是不必要的逻辑，就是因为
         我没有改子集数才没有 delay 变化…根本不要来源和子集数目，完全是你自己乱加"
      现在参数里只剩 `subset_delay_phase` 一个；默认 0 → 全路径逐字节不变（零回归）。
    """
    if not isinstance(params, dict):
        return False
    if abs(_subset_phase_deg(params)) <= 1e-12:
        return False
    return bool(_subset_marked_ids(params))


def subset_phase_seconds(params: Any, speed: Any) -> float:
    """「子集 delay 相位」→ **子集相对母集合的时间差（秒）**（未实测周期时的回退公式）。

    内核普遍是 `f(t*speed + …)`，故一个波周期 = `1/speed` 秒。用户定案的单位是
    **0-360° 波形相位差**（180°=反相位，360°=挪一个完整波峰即波形完全一致），于是

        dt = (角度 / 360) / speed

    校验：360° → dt = 1/speed = 恰好一个完整周期 → 波形与母集合重合 ✓；
          180° → dt = 1/(2·speed) = 半周期 → 反相 ✓。

    ★ R170：本公式**只是回退**。真实周期由 `_measure_kernel_period` 实测（大批预设的
      真实周期不是 `1/speed`：速率键是 freq/bpm，或内核按 N×speed 跑），实测成功时
      由 `_subset_dt_measured` 覆盖本值。
    """
    deg = _subset_phase_deg(params)
    if abs(deg) <= 1e-12:
        return 0.0
    try:
        sp = abs(float(speed))
    except Exception:
        sp = 1.0
    return (deg / 360.0) / max(sp, 1e-6)


def _subset_rate_hint(params: Any) -> float:
    """参数里的"标称频率"（Hz）估计 —— 不同预设的速率键不同（speed/rate/freq/bpm/tempo）。"""
    def _f(key: str) -> float:
        try:
            v = params.get(key, 0.0)
            return abs(float(v)) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    cands = [_f("speed"), _f("rate"), _f("freq"), _f("bpm") / 60.0, _f("tempo")]
    cands = [c for c in cands if c > 1e-6]
    return max(cands) if cands else 1.0


def _collect_series_into(series: Dict[tuple, list], values, n_units: int) -> None:
    """把一帧的 `ef.values` 追加进 `series`（`(单元号, 通道)` → 序列）。"""
    for k in range(min(n_units, len(values or ()))):
        for ch, val in (values[k] or {}).items():
            try:
                series.setdefault((k, str(ch)), []).append(float(val))
            except (TypeError, ValueError):
                pass


def _sample_kernel_series(units, ctx, rate: float, n: int):
    """按时间采样**一组代表设备**的内核输出 → `{(单元号, 通道): [取值序列]}`（供测周期）。

    `ctx = (fn, params, fixture_defs, fps)`（打包以压位置参数门禁）。

    ★ `units` 必须是 **`(orig_index, fixture)` 元组的列表** —— 内核签名统一是
      `fn(frame, t, units, defs, params, fps)`，其中单元以元组形式传入（`seq_i` 取自
      元组第 0 位）。传裸 fixture 会让内核抛异常 → 这里返回 `{}` → 测不出周期 →
      静默回退旧公式（R170 第一版就是这么失效的）。

    ★ 为什么不能只用 1 台：空间型内核（矩阵滚动 / 追逐）在"只有一台"时空间图案
      退化（`seq_i` 恒 0）→ 该台可能是常量 → 周期测不出。用多台后至少有一台的
      取值随时间变化。
    """
    fn, params, fixture_defs, fps = ctx
    units = list(units or ())[:8]
    series: Dict[tuple, list] = {}
    for i in range(n):
        t = i / rate
        ef = EffectFrame(t=t)
        ef.values = [{} for _ in units]
        try:
            fn(ef, t, units, fixture_defs, params, fps)
        except Exception:
            return {}
        _collect_series_into(series, ef.values, len(units))
    return series



def _ac_rise_lag(ac, n: int) -> int:
    """自相关序列里**第一个回升起点**（跳过低位区段后遇到的第一个回升处）。"""
    lag = 2
    while lag < n and ac[lag] > 0.2:
        lag += 1
    while lag < n and ac[lag] < 0.2:
        lag += 1
    return lag


def _ac_peak(ac, n: int, lag: int):
    """从 `lag` 起找**回升段的峰值** `(best_lag, best_val)`（跌破峰值 0.05 即止）。"""
    best_lag, best_val = None, -2.0
    while lag < n:
        if float(ac[lag]) > best_val:
            best_val, best_lag = float(ac[lag]), int(lag)
        elif best_lag is not None and float(ac[lag]) < best_val - 0.05:
            break
        lag += 1
    return best_lag, best_val


def _ac_refine_lag(ac, best_lag: int, n: int) -> float:
    """抛物线插值求亚采样峰位（峰过尖/过平时退回整数 lag）。"""
    l0 = float(best_lag)
    if 0 < best_lag < n - 1:
        y0, y1, y2 = float(ac[best_lag - 1]), float(ac[best_lag]), float(ac[best_lag + 1])
        den = y0 - 2.0 * y1 + y2
        if abs(den) > 1e-12:
            l0 = best_lag + 0.5 * (y0 - y2) / den
    return l0


def _autocorr_period(a, rate: float):
    """归一化自相关找**基波周期**（秒）；不可判定返回 None。

    用经典"第一个回升峰"法：先跳过相关值跌到低位的区段，再等它回升、取该段峰值 ——
    对含**平台**的波形（频闪/阶梯）也稳（纯"最小差值"法会被平台骗到过小的周期）。
    """
    n = int(np.asarray(a).size)
    if n < 8:
        return None
    x = np.asarray(a, dtype=np.float64)
    x = x - float(x.mean())
    denom = float(np.dot(x, x))
    if denom <= 1e-9:
        return None
    ac = np.correlate(x, x, mode="full")[n - 1:] / denom
    best_lag, best_val = _ac_peak(ac, n, _ac_rise_lag(ac, n))
    if best_lag is None or best_val < 0.6 or best_lag < 3:
        return None
    period = _ac_refine_lag(ac, best_lag, n) / float(rate)
    return period if period > 2.0 / rate else None


def _half_cycle_from_extremes(a, rate: float):
    """从**极值间距**估半个周期（秒）：`max → 之后第一个 min` 的时间差。

    ★ 为什么要这条（R170 实测）：部分内核极慢（如 `seq_*` 族：相位速度
      0.02×fps×0.04 ≈ 0.032/s → 真实周期 **31 秒**）。这类内核在固定窗口里
      **跑不满一个周期** → 自相关法必然失败 → 回退旧公式（= 无效相位）。
      但"半周期"只需要窗口里出现一次极值到反极值 —— 门槛低得多，且正是
      180° 相移所需的量（半周期）。
    """
    x = np.asarray(a, dtype=np.float64)
    if x.size < 8:
        return None
    span = float(x.max() - x.min())
    if span <= 1.0:
        return None
    flat = float(x.max()) - 0.15 * span          # 只认"接近极值"的点，忽略噪声
    flab = float(x.min()) + 0.15 * span
    imax = int(np.argmax(x))
    tail = np.flatnonzero(x[imax:] <= flab)
    if tail.size == 0:
        return None
    half = float(tail[0]) / float(rate)
    head = np.flatnonzero(x[:imax + 1] >= flat)
    if head.size == 0:
        return None
    return half if half > 1e-4 else None


def _corr_at_lag(x, lag: int) -> float:
    """`x` 与自身平移 `lag` 的**归一化相关系数**（逐滞后归一化，得真 `[-1,1]`）。"""
    xa, xb = x[:-lag], x[lag:]
    if xa.size < 8:
        return 1.0
    da = float(np.dot(xa, xa)) ** 0.5
    db = float(np.dot(xb, xb)) ** 0.5
    if da <= 1e-9 or db <= 1e-9:
        return 1.0
    return float(np.dot(xa - xa.mean(), xb - xb.mean())) / (da * db)


def _scan_antiphase(x, max_lag: int, min_corr: float, similar: float):
    """扫相关曲线取 `(半周期样本数, 谷底值, 完整周期样本数)`；不可信 → None。

    判据链（R171 用户定案的模型）：**先跌到显著反对 → 翻过够高的峰顶** = 一个完整周期；
    半周期 = 它的一半。返回的第三个量是周期样本数（供汇总时做可信度判断）。
    """
    entered = False
    depth, prev = 0.0, 1.0
    for lag in range(2, max_lag):
        v = _corr_at_lag(x, lag)
        if not entered:
            if v <= min_corr:              # 先跌到显著反对（越过初段的高相关区）
                entered, depth = True, v
            prev = v
            continue
        if v < depth:
            depth = v
        elif v < prev and prev >= similar:
            # ★ 已**翻过峰顶**：`prev`（= lag-1 处的值）是够高的局部极大
            #   ⇒ 那就是第一个完整周期终点，半周期 = 它的一半。
            #   ⚠ 不能在"升到 ≥similar"时就返回：sin 的 cos 在 0.898T 就到 0.8，
            #   那样周期会被算短 ~10%（实测 dt 偏小 → 用户看到的交叉跑到 164°）。
            return ((lag - 1) / 2, depth, (lag - 1))
        prev = v
    # 窗口内没走完一个完整周期（没出现够高的峰顶）→ 不可信，换更长窗口
    return None


def _antiphase_lag_from_series(a, rate: float, min_corr: float = -0.2,
                               similar: float = 0.8):
    """**半周期**（秒）= 该台信号**第一个完整周期的一半**；不可信 → None。

    ★★★ R171 用户定案的模型（原话）::

        "现在要的是0-360完成一整个相位差的演变，180应该刚好跟之前是交叉半相位反相，
         现在复现出来明明是260左右才会出现交叉反相半波长"

    定义（与用户说法逐字对应）：**先跌到反对、再回到相似**构成一个完整周期，
    取该周期的一半 = 180°。返回 `(半周期秒, 深度, 周期秒)`。

    ★ 为什么不用"最深谷底"（本函数的前两版）：多频/多节拍预设里，不同设备的谷底
      可能出现在不同位置（实测 `cur_motor_fig8_fan` 有 0.5s/1.0s 两簇），逐台取谷底
      再汇总会选到某个节拍，用户看到的交叉就偏到 240~360°。
      "第一个完整周期的一半"恒等于该周期的**最深反对点**，两者同时成立。

    ★ 为什么用逐滞后归一化相关：经典写法"除以全序列能量"在大滞后处天然衰减，
      会把"回到相似"的滞后压小 —— 正是旧实现把 180° 算成 260° 的病根。
    """
    x = np.asarray(a, dtype=np.float64)
    n = x.size
    if n < 16 or not np.isfinite(x).all():
        return None
    x = x - float(x.mean())
    if float(np.dot(x, x)) <= 1e-9:
        return None
    got = _scan_antiphase(x, int(n * 0.7), min_corr, similar)
    if got is None:
        return None
    half_lag, depth, period_lag = got
    period = float(period_lag) / float(rate)
    return (float(half_lag) / float(rate), depth, period) if period > 1e-3 else None


def _antiphase_candidates(units, ctx4, span: float, want: float = 900.0) -> list:
    """在**单个时间跨度**上逐台求反对候选 `[(lag秒, 深度, 看到完整周期), …]`。

    逐台取再汇总（而不是先平均再算）：跑动/波浪类各台相位不同，平均会互相抵消
    （R171 第一版探针就栽在这，报出大量假偏差）。
    """
    span = float(span)
    if span <= 0.0:
        return []
    n = _period_sample_budget(len(units), min(float(want), max(24.0, span * 60.0)))
    if n < 16:
        return []
    rate = n / span
    series = _sample_kernel_series(units, ctx4, rate, n)
    if not series:
        return []
    out = []
    for _key, vals in series.items():
        if len(vals) != n:
            continue
        a = np.asarray(vals, dtype=np.float64)
        if float(a.max() - a.min()) < 2.0:          # 静止通道跳过
            continue
        got = _antiphase_lag_from_series(a, rate)
        if got:
            out.append(got)
    return out


def _consensus_lag(cands) -> Optional[float]:
    """从逐台反对候选里取**共识滞后**：成员最多的那一簇；并列时取中位。

    ★★ R171 实测为什么必须分簇（而不是取中位）：个别预设的设备响应差异较大
      （实测 `cur_motor_fig8_fan` 有 0.5s / 1.0s 两簇），直接取中位会落在簇与簇
      之间 —— **两组都不满足**时用户看到的交叉就偏到 240~300°。分簇取多数派
      等于最大化"真正反相的设备数"，与用户"哪半台看起来在交叉"的视觉判据一致。

    `cands` = `[(半周期秒, 深度, 完整周期秒), …]`
    """
    usable = [(lag, d) for lag, d, _p in cands if lag]
    if not usable:
        return None
    usable.sort()
    clusters: list = []
    for lag, _d in usable:
        if clusters and lag <= clusters[-1][-1] * 1.2:
            clusters[-1].append(lag)
        else:
            clusters.append([lag])

    def _depth(cl) -> float:
        ds = [d for lag, d in usable if lag in cl]
        return min(ds) if ds else 0.0

    clusters.sort(key=lambda cl: (len(cl), -_depth(cl)))
    best = clusters[-1]
    return float(np.median(best))


def _measure_antiphase_lag(ctx, extra_spans=None):
    """测出 **180° 对应的位移**（= 半周期）；失败 → None。

    `ctx = (fn, params, units, fixture_defs, fps)`（打包以压位置参数门禁）。

    ★★ 为什么按窗口**由短到长**逐级尝试（R171 实测）：窗口比信号周期短时，
      逐滞后相关会给出**随机深坑**（实测 `motor_swan_lake_v25` 周期 ~12.6s：
      8 秒窗口在 4.2s 报出 −0.95 的假坑，真反对点 6.3s/−1.0）。
      只有"先跌到反对、**再回到相似（≥+0.8）**"才说明窗口真的装下一个完整周期，
      `_antiphase_lag_from_series` 正是按这个证据链返回的 —— 拿不到就换更长窗口。
    """
    fn, params, units, fixture_defs, fps = ctx
    units = list(units or ())
    if not units:
        return None
    ctx4 = (fn, params, fixture_defs, fps)
    f0 = _subset_rate_hint(params)
    base_span = min(8.0, max(1.0, 4.0 / max(f0, 0.05)))
    for span in (base_span,) + tuple(extra_spans or _SLOW_SPANS):
        cands = _antiphase_candidates(units, ctx4, span)
        if not cands:
            continue                       # 该窗口装不下一个完整周期 → 换更长

        strong = [c for c in cands if c[1] <= -0.5]
        if not strong:
            continue                       # 有完整周期但没有真反对 → 换更长
        lag = _consensus_lag(strong)
        if lag:
            return lag
    return None


def _measure_period_over(units, ctx4, span: float, want: float = 900.0):
    """在**指定时间跨度**上采样求周期（自相关 → 极值间距）；测不出返回 None。

    慢内核（真实周期几十秒）在常规 8 秒窗口里跑不满一个周期 → 常规路径必然失败；
    这里按跨度自适应降采样（采样点数按 `_period_sample_budget` 封顶），
    既看得够长又不会爆开销。
    """
    span = float(span)
    if span <= 0.0:
        return None
    n = _period_sample_budget(len(units), min(float(want), max(8.0, span * 4.0)))
    if n < 8:
        return None
    rate = n / span
    series = _sample_kernel_series(units, ctx4, rate, n)
    best, best_ptp = _widest_variation(series, n)
    if best is None or best_ptp < 2.0:
        return None
    period = _autocorr_period(best, rate)
    if period:
        return period
    half = _half_cycle_from_extremes(best, rate)
    return (2.0 * half) if half else None


#: 慢内核兜底的候选采样跨度（秒）；逐级放大直到看得见完整周期。
_SLOW_SPANS = (24.0, 72.0, 200.0)
#: 「差异最大」兜底的 dt 候选阶梯（秒）——周期未知时也要保证**看得见变化**。
_DT_LADDER = (0.04, 0.07, 0.12, 0.2, 0.35, 0.6, 1.0, 1.7, 2.9, 5.0, 8.5, 14.0, 24.0)


def _mismatch_for_pair(ctx4, units, t0: float, dt: float) -> Optional[float]:
    """在 `t0` 与 `t0+dt` 各跑一次内核 → 归一化差异；出错/无通道 → None。"""
    fn, params, fixture_defs, fps = ctx4
    vals = []
    for t in (float(t0), float(t0) + float(dt)):
        ef = EffectFrame(t=t)
        ef.values = [{} for _ in units]
        try:
            fn(ef, t, list(units), fixture_defs, params, fps)
        except Exception:
            return None
        vals.append(ef.values)
    return _output_dissimilarity(vals[0], vals[1])


def _worst_mismatch_over_probes(ctx4, units, dt: float, probe_ts) -> Optional[float]:
    """该 dt 在多个探针时刻上的**最差**（=最保守）差异；全失败 → None。"""
    worst = None
    for t0 in probe_ts:
        s = _mismatch_for_pair(ctx4, units, t0, dt)
        if s is None:
            continue
        worst = s if worst is None else min(worst, s)
    return worst


def _pick_dt_by_dissimilarity(units, ctx4, probe_ts=(0.0, 7.0, 23.0)) -> Optional[float]:
    """**周期测不出时的终极兜底**：挑一个让"错相前后差异最大"的 dt（秒）；无 → None。

    为什么这是正确兜底（R170 实测：`seq_*` 族默认 `phase_speed=0.009` → 真实周期
    约 69 秒，8 秒窗口测不出；旧公式给的 0.5s 只占周期 0.7% → **调了完全没有反应**）：
    用户的硬要求是"调『子集 delay 相位』必须真的有反应"。周期性内核的差异最大点
    正是半周期（= 180° 该有的相移），所以本方法对周期型内核与自相关法结论一致；
    对测不出周期的稀疏/慢内核，它至少给一个**确实看得见的**分离量，而不是零反应。

    成本：候选数 × 探针时刻数 × 2 次内核调用（约 80 次），可忽略。
    """
    best_dt, best_score = None, 0.0
    for dt in _DT_LADDER:
        score = _worst_mismatch_over_probes(ctx4, units, dt, probe_ts)
        if score is not None and score > best_score:
            best_score, best_dt = score, float(dt)
    # 阈值：差异至少要有"一个可感知的台阶"（>2% 量程），否则等于没反应
    if best_dt is None or best_score < 0.02:
        return None
    return best_dt


def _pair_diff(x, y) -> Optional[Tuple[float, float]]:
    """一个通道对 `(差值, 归一化分母)`；坏值 → None。"""
    try:
        xf, yf = float(x), float(y)
    except (TypeError, ValueError):
        return None
    return abs(yf - xf), max(abs(xf), abs(yf), 1.0)


def _channel_pair_diff(va, vb) -> Tuple[float, float]:
    """一帧对里所有共有通道的 `(总差值, 总归一化分母)`。"""
    num = 0.0
    den = 0.0
    for ch, x in (va or {}).items():
        y = (vb or {}).get(ch)
        if y is None:
            continue
        pair = _pair_diff(x, y)
        if pair is None:
            continue
        num += pair[0]
        den += pair[1]
    return num, den


def _frame_diff_totals(a, b) -> Tuple[float, float]:
    """两帧的 `(总差值, 总归一化分母)`（逐通道比较，坏值/缺通道跳过）。"""
    num = 0.0
    den = 0.0
    for va, vb in zip(a or (), b or ()):
        n1, d1 = _channel_pair_diff(va, vb)
        num += n1
        den += d1
    return num, den


def _output_dissimilarity(a, b) -> Optional[float]:
    """两组输出帧的**归一化差异**（0=完全相同，1=完全相反）；无有效通道 → None。"""
    num, den = _frame_diff_totals(a, b)
    return (num / den) if den > 0.0 else None


def _measure_kernel_period(ctx):
    """**实测内核的真实时间周期**（秒）；测不出返回 None（调用方回退旧公式）。

    `ctx = (fn, params, units, fixture_defs, fps)`（打包以压位置参数门禁）。

    为什么必须实测（R170 用户实报"调 delay 根本没反应"）：旧式 `dt=(度/360)/speed`
    隐含假设"内核一个周期 = 1/speed"，但大量预设并不满足 ——
      · 速率键是 `freq` / `bpm` / `phase_speed` 而非 `speed`（默认取 1.0）；
      · 内核按 **N×speed** 跑（如 figure8 / 矩阵滚动）；
      · 内核极慢（如 `seq_*` 族真实周期约 69 秒）。
    此时 dt 可能正好是**整数个真实周期**或**远小于一个周期** → 相移不可见。

    ★★ `units` 必须是**与真实渲染相同的完整设备列表**（不是前 8 台）——
      部分内核的几何由设备总数决定（`matrix_scroll_v`：`rows = ceil(n/cols)`，
      `offset = int(t·speed·rows) % rows`），用子集测出的 dt 在整表上可能**不可见**
      （实测：16 台时"前 8 台"判可见、完整列表判不可见）。开销改由
      `_period_sample_budget` 按设备数限制采样点数（总写入量封顶）。

    `units` = **真实设备单元列表**（`[(orig_i, fixture), …]`）——
      还必须**位置各异**：位置驱动的图案（`seq_*` 用 pos_x/pos_y 算权重）
      若把同一个 fixture 复制 8 份，空间图案恒为常量 → 测不出任何东西。

    三级测量（越往后越贵，但只在前面都失败时执行；结果按 `(预设, 设备, 参数)` 缓存）：
      ① 常规窗口：自相关求基波周期（覆盖绝大多数）；
      ② 慢内核：按 `_SLOW_SPANS` 逐级放大窗口重测（`seq_*` 这类几十秒周期的靠这级）；
      ③ 终极兜底：周期仍未知时，挑"让两组差异最大"的 dt（保证调了**看得见**反应，
         而不是旧公式那种零反应）。
    """
    fn, params, units, fixture_defs, fps = ctx
    units = list(units or ())
    if not units:
        return None
    ctx4 = (fn, params, fixture_defs, fps)
    f0 = _subset_rate_hint(params)
    rate = min(400.0, max(100.0, f0 * 20.0))
    span = min(8.0, max(1.0, 4.0 / max(f0, 0.05)))
    want = int(min(1200.0, rate * span))
    n = _period_sample_budget(len(units), want)
    period = _period_from_series(units, ctx4, n / span, n)
    if period:
        return period
    for slow_span in _SLOW_SPANS:
        period = _measure_period_over(units, ctx4, slow_span)
        if period:
            return period
    dt = _pick_dt_by_dissimilarity(units, ctx4)
    return (2.0 * dt) if dt else None


def _period_from_series(units, ctx4, rate: float, n: int):
    """采样 `n` 点求周期（自相关 → 极值间距）；不足/静止 → None。"""
    if n < 8:
        return None
    series = _sample_kernel_series(units, ctx4, rate, n)
    best, best_ptp = _widest_variation(series, n)
    if best is None or best_ptp < 2.0:
        return None
    period = _autocorr_period(best, rate)
    if period:
        return period
    half = _half_cycle_from_extremes(best, rate)
    return (2.0 * half) if half else None


def _widest_variation(series, n: int):
    """从采样序列里挑**峰峰值最大**的那条通道 `(ndarray, 峰峰值)`；都不动 → `(None, 0)`。

    取"变化最大"而不是固定通道：不同内核的有效通道不同（电机/强度/多通道），
    常量通道做自相关会被 DC 抵消（denom≈0）永远测不出周期。
    """
    best, best_ptp = None, 0.0
    for _key, vals in (series or {}).items():
        if len(vals) != n:
            continue
        a = np.asarray(vals, dtype=np.float64)
        ptp = float(a.max() - a.min())
        if ptp > best_ptp:
            best_ptp, best = ptp, a
    return best, best_ptp


def _period_key_items(params: Any) -> tuple:
    """缓存键里的"标量参数"部分（私有键与子集相位键除外；排序保证可比）。"""
    items = []
    for k, v in (params or {}).items():
        ks = str(k)
        if ks.startswith("_") or ks == _SUBSET_PHASE_KEY:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        items.append((ks, round(float(v), 4)))
    items.sort()
    return tuple(items)


def _period_sample_budget(n_units: int, want: int) -> int:
    """按设备数限制**时间采样点数**（内核每次要写 n 台，成本 ∝ n×采样数）。

    ★★ R170 为什么必须用**完整设备列表**（而不是前 8 台）：部分内核的几何由
      设备总数决定（`matrix_scroll_v`：`rows = ceil(n/cols)`，
      `offset = int(t·speed·rows) % rows`）→ 用 8 台测出的 dt 在 42 台上**不可见**
      （实测：N=16 时"前 8 台"判可见、真列表判不可见）。故列表必须与真实渲染一致；
      开销改由**采样点数**控制（总写入量封顶 ~4 万次）。
    """
    n_units = max(1, int(n_units))
    cap = max(48, min(int(want), 40000 // n_units))
    return int(cap)


def _subset_period_key(preset_id: Any, params: Any, unit: Any) -> tuple:
    """实测周期的缓存键（含全部会改速率的标量参数；私有键与子集相位键除外）。"""
    fid = str(getattr(unit, "id", "") or "") if unit is not None else ""
    return (str(preset_id or ""), fid, _period_key_items(params))


def _phase_probe_ts(units) -> tuple:
    """验证"这个 dt 真的看得见"时用的采样时刻（覆盖多个相位，避免撞上巧合）。"""
    return (0.0, 0.37, 1.9)


def _dt_is_visible(ctx, dt: float) -> bool:
    """`dt` 这个相位差在输出上**真的看得见**吗（至少一个采样时刻有可感知差异）。

    `ctx = (fn, params, units, fixture_defs, fps)`（打包以压位置参数门禁）。

    ★★ R170 为什么要这一步（`matrix_scroll_v` 实测）：内核是**离散时间**的
      `offset = int(t·speed·rows) % rows`，而图案自身的**空间**周期可能整除
      时间半周期 → `period/2` 算出来的位移正好让图案**复原**（调 180° 毫无变化）。
      所以"周期测得对"≠"该 dt 可见"。这里做一次**廉价实测校验**：不可见就换
      差异最大化的 dt（`_pick_dt_by_dissimilarity`）。
    """
    if not dt or dt <= 0.0:
        return False
    fn, params, units, fixture_defs, fps = ctx
    ctx4 = (fn, params, fixture_defs, fps)
    for t0 in _phase_probe_ts(units):
        s = _mismatch_for_pair(ctx4, units, t0, dt)
        if s is not None and s >= 0.02:
            return True
    return False


def _cached_period(engine, preset_id, params, key_unit, ctx4) -> Any:
    """取（必要时实测）该上下文的**180° 位移**（秒）；无 → None。

    按 `(预设, 首个设备, 标量参数)` 缓存。`ctx4 = (fn, units, fixture_defs, fps)`；
    `units` 是**真实设备单元列表**（位置各异、且与真实渲染同规模）。

    ★★★ R171（用户定案）：180° 的定义 = **反对滞后**（自相关第一段显著负相关的谷底），
      **不再是"周期/2"** —— 多频叠加的波形没有唯一周期，除以 2 会落到 260° 附近
      （用户实测："180 应该刚好交叉反相，现在复现出来明明 260 左右才会出现"）。
      对纯正弦两者恰好相等，所以旧实现只在复杂波形上暴露。

    仍未测出时（信号非周期/太弱），退回"让两帧差异最大"的经验位移
    （`_pick_dt_by_dissimilarity`），保证"调了看得见反应"而不是零反应。
    """
    fn, units, fixture_defs, fps = ctx4
    cache = getattr(engine, "_subset_period_cache", None)
    if cache is None:
        cache = {}
        engine._subset_period_cache = cache
    key = _subset_period_key(preset_id, params, key_unit)
    if key not in cache:
        half = None
        lag = _measure_antiphase_lag((fn, params, units, fixture_defs, fps))
        if lag and float(lag) > 1e-6:
            if _dt_is_visible((fn, params, units, fixture_defs, fps), float(lag)):
                half = float(lag)
        if half is None:
            half = _pick_dt_by_dissimilarity(
                units, (fn, params, fixture_defs, fps))
        bounded_put(cache, key, half, 256)
    return cache[key]


def _subset_dt_measured(ctx, dt_fallback: float) -> float:
    """用**实测 180° 相位差**重算子集相位差；测不出时回退 `dt_fallback`（旧公式）。

    `ctx = (engine, fn, params, preset_id, units, fixture_defs, fps)`
    （打包以压位置参数门禁）；`units` 是**真实设备单元列表**（前几台即可）。
    """
    engine, fn, params, preset_id, units, fixture_defs, fps = ctx
    if not units or not isinstance(params, dict):
        return dt_fallback
    deg = _subset_phase_deg(params)
    if abs(deg) <= 1e-12:
        return dt_fallback
    cycle_key = getattr(fn, "_cycle_rate_key", None)
    if cycle_key is not None:
        return subset_phase_seconds(params, params.get(cycle_key, 1.0))
    key_unit = units[0][1] if isinstance(units[0], tuple) else units[0]
    half = _cached_period(engine, preset_id, params, key_unit,
                          (fn, units, fixture_defs, fps))
    if not half or float(half) <= 1e-6:
        return dt_fallback
    # 用户调的是 0-360°：以实测的"180° = 半周期"为基准线性换算
    return (deg / 180.0) * float(half)


def subset_index_table(unit_ids: Any, marked: Any = None) -> List[int]:
    """给一串虚拟单元算「该单元的时间偏移倍数」（1 = 子集，要错相；0 = 母集合余下）。

    这是子集相位机制的**唯一分组真源**，抽成独立函数便于单测与反向验证。

    ★★ R170 定案（用户纠正）：**只有两组**，而且分组完全由"TopView 里选中了谁"决定：
        · 在 `marked` 里的单元 → **1（子集：整体错开一个相位）**
        · 其余（母集合里没被选中的） → 0（保持原相位）
      旧实现还有"子集数 + 来源（按母集合顺序 / 按TopView标记）"两组额外参数，
      用户明确否掉："根本不要来源和子集数目，完全是你自己乱加" —— 已全部删除。
      "奇数/偶数错相"这类需求由用户在 TopView 直接用奇偶选择工具选中即得。

    ★ 极性（R170 实测校正）：偏移必须落在**被选中的那些**设备上 —— 用户语义是
      "选中什么设备为子集就改哪些设备的效果"。旧写法极性相反（改的是没选中的一半），
      表现为"选中的那半不动、没选的反而动"，与用户预期不符。
    """
    ids = list(unit_ids or ())
    if not ids:
        return []
    try:
        marked_set = set(str(x) for x in (marked or ()))
    except Exception:
        marked_set = set()
    if not marked_set:
        return [0] * len(ids)
    return [1 if str(uid) in marked_set else 0 for uid in ids]


def _subset_offset_at(subs, k, subset_dt) -> float:
    """第 k 个单元的子集相位时间偏移（秒）。越界/空表 → 0（= 不偏移）。"""
    if not subs or k >= len(subs):
        return 0.0
    return float(subs[k]) * float(subset_dt)


def _make_subset_ctx(params: dict):
    """子集相位的运行时上下文：`(enabled, dt, ids_of, routing_on)`。

    用户原话::

        "我选中一组设备，让他们 effect 整体往复运动，再选中这个设备里面的奇数个，
         我想让他们跟偶数个的 delay 相位差 180 度"
        "我明明要的是我根据 topview 里面选中的子集去改选中什么设备为子集就改哪些
         设备的效果，根本不要来源和子集数目"

    语义（R170 定案）：**TopView 里选中哪些设备，哪些设备就是子集**（整体错后
    `子集 delay 相位` 对应的时长），母集合里没被选中的保持原相位。只有一个参数。

    量纲：内核普遍是 `f(t*speed + …)`，一个波周期 = `1/speed` 秒，故回退
    `dt = (角度/360)/speed`（180°=半周期=反相；360°=整周期=波形重合）；
    实测成功的真实周期由 `_subset_dt_measured` 覆盖（见其 docstring）。

    `ids_of(units)` 给出每个单元属于第几组；`routing_on(units)` 表示该队列是否
    **真的需要按单元分别算时间**（子集无差异时返回 False → 走单帧快路径，零开销）。
    routing 闸门必须覆盖 wings / block / per-shape 三条路径，否则子集相位会被
    **静默跳过**（改了参数却没反应）。抽成模块级是为了压 `_run_compute_fn` 的行数/CC。
    """
    enabled = subset_split_enabled(params)
    dt = (subset_phase_seconds(params, params.get("speed", params.get("rate", 1.0)))
          if enabled else 0.0)
    marked = _subset_marked_ids(params)

    def ids_of(units):
        if not enabled:
            return None
        ids = [str(getattr(fi, "id", "") or "") for _oi, fi in units]
        return subset_index_table(ids, marked)

    def routing_on(units):
        return bool(enabled and subset_has_gap(ids_of(units)))

    return enabled, dt, ids_of, routing_on


def _unit_values_by_time(ctx, call_sorted, frame_len, read_indices, subs) -> list:
    """逐虚拟单元算通道值 —— 三条分流：子集分组 / 单帧分发 / 逐单元。

    `ctx` = `(fn, effect_t, phase_spread, subset_dt, fixture_defs, params, fps)`
    （打包是为了压位置参数门禁）。抽成模块级函数为了让 `_compute_unit_values`
    保持薄壳 —— EffectEngine 的方法行数/CC 有门禁。

    分流语义（与改前逐字节等价，仅多出子集那一支）：
      ① 开了子集相位、没开 effect 相位 → **按子集分组**跑 `fn`（count 次，不是 N 次）；
      ② 两个机制都没开 → t_k 全同 → 只跑一次 `fn` 再按 read_indices 分发；
      ③ 否则逐单元（effect 相位传统路径，或两机制同时开）。
    """
    fn, effect_t, phase_spread, subset_dt, fixture_defs, params, fps = ctx
    n = len(call_sorted)
    out = [dict() for _ in range(n)]
    gap = subset_has_gap(subs)
    if not phase_spread and gap:
        return _subset_group_frames(
            (fn, effect_t, subset_dt, fixture_defs, params, fps),
            call_sorted, frame_len, read_indices, subs)
    if not phase_spread:
        tmp = EffectFrame(t=effect_t)
        tmp.values = [{} for _ in range(frame_len)]
        fn(tmp, effect_t, call_sorted, fixture_defs, params, fps)
        for k in range(n):
            ri = read_indices[k]
            if 0 <= ri < len(tmp.values):
                out[k] = dict(tmp.values[ri])
        return out
    xs_ctx = ctx + (out,)
    return _per_unit_frames(xs_ctx, call_sorted, frame_len, read_indices, subs)


def _per_unit_frames(xs_ctx, call_sorted, frame_len, read_indices, subs) -> list:
    """逐单元各跑一次 `fn`（effect 相位的传统路径，或两机制同时开时）。

    `t_k = effect_t + effect相位*(k/(N-1)) + 子集序号*subset_dt` —— 两分量**相加**，
    因此"组内逐台铺开"与"子集之间错相"彼此正交、可同时使用。
    """
    fn, effect_t, phase_spread, subset_dt, fixture_defs, params, fps, out = xs_ctx
    n = len(call_sorted)
    denom = max(1, n - 1)
    for k in range(n):
        offset = float(subs[k]) * float(subset_dt) if (subs and k < len(subs)) else 0.0
        t_k = effect_t + phase_spread * (k / denom) + offset
        tmp = EffectFrame(t=t_k)
        tmp.values = [{} for _ in range(frame_len)]
        # ★ R170：按子集给独立状态命名空间（有状态内核不会让两组互相推进时间线）
        fn(tmp, t_k, call_sorted, fixture_defs,
           _tagged_params(params, int(subs[k])) if subs and k < len(subs) else params,
           fps)
        ri = read_indices[k]
        if 0 <= ri < len(tmp.values):
            out[k] = dict(tmp.values[ri])
    return out


def _tagged_params(params: Any, tag: Any) -> Any:
    """给参数加一个「子集状态命名空间」尾缀（**不改原字典**）。

    ★★ R170：部分预设内核是**有状态状态机**（如 `effect_presets_v29` 的矩阵族，
      用全局 `_STATES` 按 `t` 推进 `last_beat`/活跃组）。子集相位要对每组各跑一次
      内核（时间不同），若两组共用一份状态会互相推进 → 未选中的那半也跟着变
      （全量扫描实测 51 个 `matrix_*_v29` 就是这个症状）。
      按子集分组给一个独立命名空间 → 每组各自连贯、彼此只差一个相位。
      子集未启用时不会调用本函数 → 逐字节零回归。
    """
    if not isinstance(params, dict):
        return params
    out = dict(params)
    out["_subset_state_tag"] = str(tag)
    return out


def _subset_group_frames(ctx, call_sorted, frame_len, read_indices, subs) -> list:
    """**按子集分组**跑内核：每组只算一次，再把该组的帧分发给组内各单元。

    这是「子集 delay 相位」的主力路径（只开子集、未开逐台相位铺开时）。
    两组 → 只调 `fn` **2 次**（而不是 N 次），组内 t_k 相同、组间相差 `subset_dt`。
    抽成模块级函数是为了让 `_compute_unit_values` 不超行数/CC 门禁。

    `ctx = (fn, effect_t, subset_dt, fixture_defs, params, fps)`（压位置参数门禁）。
    返回与 `call_sorted` 下标对齐的 values dict 列表。
    """
    fn, effect_t, subset_dt, fixture_defs, params, fps = ctx
    n = len(call_sorted)
    out = [dict() for _ in range(n)]
    if n <= 0:
        return out
    group_frames: Dict[int, Any] = {}
    for k in range(n):
        g = int(subs[k]) if k < len(subs) else 0
        frame = group_frames.get(g)
        if frame is None:
            # ★ R170：偏移**统一**走 `_subset_offset_at`（与 `_per_unit_frames` 同一处换算）
            #   —— 旧写法在这里内联 `g * subset_dt`，两处各算各的，反向验证
            #   抽掉偏移时主路径不受影响（两条链路的偏移不是同一个真源）。
            t_k = float(effect_t) + _subset_offset_at(subs, k, subset_dt)
            frame = EffectFrame(t=t_k)
            frame.values = [{} for _ in range(frame_len)]
            fn(frame, t_k, call_sorted, fixture_defs,
               _tagged_params(params, g), fps)
            group_frames[g] = frame
        ri = read_indices[k]
        if 0 <= ri < len(frame.values):
            out[k] = dict(frame.values[ri])
    return out


def subset_has_gap(subs: Any) -> bool:
    """子集分组里是否**真的存在**组间差异（全 0 组 = 无需分组重跑，走原快路径）。

    用于性能分流：子集相位为 0、或 TopView 没选中任何子集时不应多算。
    """
    if not subs:
        return False
    first = subs[0]
    for v in subs:
        if v != first:
            return True
    return False


def apply_spatial_scope(sorted_fixtures: List[Tuple[int, object]],
                        positions: Dict[str, dict],
                        params: Dict[str, Any],
                        sort_mode: str = "index") -> List[Tuple[int, object]]:
    """按 spatial_scope 重排设备应用顺序（只改顺序，不进每帧热路径）。

    per_shape: 把 params["_unit_groups"] 中每个微单元（工程 kind=unit/package 的
    成员表）聚拢为相邻块，块内按成员相对单元质心的极角**归一化到 [0, 2π)、从
    统一参考角 0°(正右方)起逆时针升序**排列——各单元圆上第 k 盏灯位于相同的
    相对角度，圆/绕圈类效果在所有设备上「时针同步」(同一时刻点亮角度一致)。
    无单元数据的设备保持原顺序。
    global / follow(未解析) / 缺省: 保持入参序（R201/P10 起，见下方注）。
    """
    scope = normalize_spatial_scope(params.get("spatial_scope"))
    items = list(sorted_fixtures or [])
    if scope != "per_shape":
        # R201/P10：不再按全局矩阵坐标重排（旧 `_sort_global_matrix` 把基准
        # 序打回左→右横扫，用户实报"丢掉设备排序"）。基准序=画布编号徽标
        # 同源（逐圆转圈、圆间按设备序）；全局扫描用坐标模式+跳过编号。
        return items
    if len(items) < 2:
        return items
    unit_groups = params.get("_unit_groups")
    if not isinstance(unit_groups, (list, tuple)) or not unit_groups:
        return items

    unit_by_fid = {}
    units = []
    for idx, g in enumerate(unit_groups):
        fids = [str(f) for f in (g or []) if str(f)]
        if len(fids) < 2:
            continue
        units.append((idx, fids))
        for f in fids:
            unit_by_fid.setdefault(f, idx)
    if not units:
        return items

    unit_items = {idx: [] for idx, _fids in units}
    for item in items:
        fid = str(getattr(item[1], "id", "") or "")
        uidx = unit_by_fid.get(fid)
        if uidx is not None and uidx in unit_items:
            unit_items[uidx].append(item)

    def _pos(fid):
        p = positions.get(fid, {}) if positions else {}
        return float(p.get("pos_x", 0.0)), float(p.get("pos_y", 0.0))

    ordered_units = {}
    for idx, fids in units:
        members = unit_items.get(idx) or []
        if len(members) < 2:
            continue
        xs = [px for px, _py in map(_pos, fids)]
        ys = [py for _px, py in map(_pos, fids)]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        def _angle(item, cx=cx, cy=cy):
            px, py = _pos(str(getattr(item[1], "id", "") or ""))
            # ★ 2026-08-04 圆效果时针同步：归一化到 [0, 2π) 并从统一参考角
            #   0°(正右方)起逆时针编号。旧实现用 atan2(-π..π)，各单元排序起点
            #   = 各自第一个成员的原角度（配灯布置基准/朝向不同 → 起点角各异），
            #   导致各设备圆效果「时针」不同步。取模后所有单元第 k 盏灯位于
            #   相同相对角度 → 同一时刻点亮角度一致。
            # ★ 浮点钳制：质心为成员坐标均值(浮点误差 ~1e-14)，0° 灯的角度
            #   atan2(-ε, r) % 2π 会落到 2π-ε 而非 0.0——若不钳制,该灯排到
            #   单元末尾,各单元起点角又错开一位(实测 2026-08-04 多单元不同步)。
            a = math.atan2(py - cy, px - cx) % math.tau
            if a >= math.tau - 1e-12:
                return 0.0
            return a

        ordered_units[idx] = sorted(members, key=_angle)

    result = []
    emitted = set()
    for item in items:
        fid = str(getattr(item[1], "id", "") or "")
        uidx = unit_by_fid.get(fid)
        if uidx is None:
            result.append(item)
            continue
        if uidx in emitted:
            continue
        if uidx not in ordered_units:
            result.append(item)
            continue
        emitted.add(uidx)
        result.extend(ordered_units[uidx])
    return result


def normalize_effect_application_scope(scope: Any) -> str:
    """Normalize Effect page application scope.

    both: keep legacy behavior; preset motion and sequence window both apply.
    whole: use the preset's time motion as one group, without the sequence window.
    sequence: use a stable one-fixture preset value and apply it through the sequence window.
    """
    scope = str(scope or "both").strip().lower()
    aliases = {
        "all": "both",
        "legacy": "both",
        "combined": "both",
        "global": "whole",
        "overall": "whole",
        "index": "sequence",
        "ordered": "sequence",
        "order": "sequence",
    }
    scope = aliases.get(scope, scope)
    return scope if scope in EFFECT_APPLICATION_SCOPES else "both"


def build_effect_spatial_groups(
    sorted_fixtures: List[Tuple[int, object]],
    params: Dict[str, Any],
) -> List[List[Tuple[int, object]]]:
    """Return fixture groups after block and wing parameters are applied."""
    items = [(int(orig_i), fi) for orig_i, fi in (sorted_fixtures or [])]
    if not items:
        return []
    try:
        wings = max(1, int(params.get("wings", 1) or 1))
    except Exception:
        wings = 1
    try:
        block_size = max(1, int(params.get("block_size", 1) or 1))
    except Exception:
        block_size = 1

    return build_wing_block_units(items, wings, block_size,
        mirror=bool(params.get("wing_mirror", False)),
        reverse=bool(params.get("wing_reverse", False)))


def _per_shape_group_member_lists(groups: list) -> List[List[str]]:
    """把工程 fixture_groups 条目归一成「按组基准序的成员 id 表」(2026-08-08)。

    条目为 FixtureGroup.to_dict 形态: dict{fixture_ids, base_order_fixture_ids};
    fixture_ids 决定成员，base_order_fixture_ids 只给成员排序，不能扩员；
    兼容裸 id 列表条目。组成员 <2 的组丢弃(与 _unit_groups_for_ids
    门槛对齐)。供 _per_shape_unit_blocks 无配灯单元回退使用。
    """
    out: List[List[str]] = []
    for g in (groups or []):
        if isinstance(g, dict):
            raw = ordered_members(g.get("fixture_ids"), g.get("base_order_fixture_ids"))
        elif isinstance(g, (list, tuple)):
            raw = g
        else:
            continue
        fids: List[str] = []
        seen = set()
        for f in raw:
            sfid = str(f)
            if sfid and sfid not in seen:
                fids.append(sfid)
                seen.add(sfid)
        if len(fids) >= 2:
            out.append(fids)
    return out


def _per_shape_blocks_from_groups(sorted_fixtures: list,
                                  ordered_groups: List[List[str]]) -> Optional[list]:
    """按组把选中设备切成 per_shape 单元块(2026-08-08)。

    每组设备一个块,块内保持 sorted_fixtures 基准序(选择序/CCW 弧序,圆内
    连续);组外设备进末尾 singles 块。组间重叠时按「选择序连续度」择优归属:
    组员在选择序中越连续(整圆被一段选中 → 1 段)越优先,同连续度取小组成员
    少者——To The Moon 大组(LED 960/HJC 174/调压气爆 222)虽也整段命中,
    但成员数远大于圆分组,圆分组先认领全部成员,大组退化为空块被丢弃 →
    分块即 9 个圆,确定性。门槛 ≥2 台成块;无任何命中返回 None → 调用方走
    原扁平路径。
    """
    rank = {}
    for pos, item in enumerate(sorted_fixtures):
        fid = str(getattr(item[1], "id", "") or "")
        if fid and fid not in rank:
            rank[fid] = pos
    entries = []
    for uidx, ol in enumerate(ordered_groups):
        present = [f for f in ol if f in rank]
        if len(present) < 2:
            continue
        present.sort(key=rank.get)
        runs = 1 + sum(
            1 for i in range(1, len(present))
            if rank[present[i]] != rank[present[i - 1]] + 1
        )
        entries.append((runs, len(present), uidx, present))
    if not entries:
        return None
    entries.sort(key=lambda e: (e[0], e[1]))
    uid_by_fid = {}
    for _runs, _sz, uidx, present in entries:
        for f in present:
            if f not in uid_by_fid:
                uid_by_fid[f] = uidx
    blocks_by_unit = {}
    singles = []
    for item in sorted_fixtures:
        fid = str(getattr(item[1], "id", "") or "")
        uidx = uid_by_fid.get(fid)
        if uidx is None:
            singles.append(item)
        else:
            blocks_by_unit.setdefault(uidx, []).append(item)
    blocks = []
    for _runs, _sz, uidx, present in entries:
        blk = blocks_by_unit.get(uidx)
        if blk and len(blk) >= 2:
            blocks.append(blk)
    if not blocks:
        return None
    if singles:
        blocks.append(singles)
    return blocks


def _angle_decl_widens(preset_id: Any, spec: ParamSpec) -> bool:
    """该参数是否属于"角度类"、应被自动加宽到 360。

    ★★ R141 角度声明统一（用户实报"设 90° 被夹成 1.0"，206 个预设命中）：
    原规则按**标签文本**判定角度（`key == "angle_range" or "角度" in label`），于是把
    内核其实按**归一化 0~1** 消费的参数（「展开角度」= `rad = 127*clamp01(angle)` 这类
    展开量）也声明成 0~360 —— UI 让用户拉到 90°，内核 `min(1.0, …)` 把它夹回 1.0。

    现在先查**实测名单** `NORMALIZED_ANGLE_PARAMS`（`"<preset_id>|<key>"`；判定方式：
    K=1.0 与 K=360.0 输出逐位相同 ⇒ 内核根本没消费 >1 的值）：名单内 → 跳过加宽，
    声明回到内核真实语义；真按度消费的参数（扇形角度/角度范围/内层外层角度…）照旧加宽。

    **只改 UI 量程**：内核本来就夹到 1.0，任何已存值的渲染输出逐位不变
    （`tests/test_r141_prog_angle_decl.py` 第 3 组实证）。
    """
    if spec.key != "angle_range" and "角度" not in spec.label:
        return False
    return f"{preset_id or ''}|{spec.key}" not in NORMALIZED_ANGLE_PARAMS


# ─────────────────────────────────────────────────────────────────────────────
#  效果引擎核心
# ─────────────────────────────────────────────────────────────────────────────

#: motor 通道 → 摇杆幅度轴下标（0=x / 1=y / 2=z）。
_JOY_AMP_AXIS_INDEX: Dict[str, int] = {"motor_x": 0, "motor_y": 1, "motor_z": 2}
#: 该轴未接时的回退顺序（与 R118 起 `compute_at` 的旧选择逻辑逐位一致）。
_JOY_AMP_AXIS_FALLBACK = {0: (1,), 1: (0,), 2: (1, 0)}


def joy_amp_for_channel(ch: str, ch_type_param: Any, amps: tuple):
    """摇杆幅度：通道 → 该通道应乘的幅度值（None = 该轴未接）。

    ★★ R141 修 `joy_amplitude_z` **死键**（用户绑定 Z 轴幅度后完全没作用）：
    Programmer 摇杆面板的「Z 轴 →」下拉写得出 `joy_amplitude_z`
    （`programmer_panel._JOY_AMP_OVERRIDE_KEYS` 三键之一），但 `compute_at`
    原先只读 x/y —— motor_z 分支写的是"回退用 y"，于是选 Z 轴时那个键
    进了 `_param_overrides` 却从无人消费。

    现在：motor_z **优先**用 z；z 未接时**保持旧的"回退 y、再退 x"**（逐位零回归）。
    非电机通道（dimmer/valve/pump 等）跟随 X 轴 —— 旧行为不变。
    轴判定只看 `ch`：旧条件里的 `ch_type_param == axis and ch == ch_type_param`
    与"`ch` 就是该 motor 通道"等价（已逐条对照）。`amps` = (x, y, z)。
    """
    amp_x, amp_y, amp_z = amps
    idx = _JOY_AMP_AXIS_INDEX.get(str(ch))
    if idx is None:
        return amp_x if amp_x is not None else amp_y
    if amps[idx] is not None:
        return amps[idx]
    for fb in _JOY_AMP_AXIS_FALLBACK[idx]:
        if amps[fb] is not None:
            return amps[fb]
    return None


def _effect_width_subset(sorted_fixtures: list, params: Dict[str, Any]) -> list:
    """宽窄 → 剖面参数收窄（**不改设备序列**）（R118 用户二次澄清定案）。

    用户 2026-09-20 原话::

        "效果宽窄调小只是蛇的长度减少，它还是要跑完整个迷宫，而不是你现在这样
         只让他跑一半…这个效果还是涉及全部设备，只不过这个波峰或者说效果的
         单元设备减少，波峰承载的设备少，而不是效果只在一部分设备上应用。"

      · 2026-09-19 版（**错**）：截取居中 `M = round(width × n)` 台设备 → 效果
        只跑中间一段，两侧设备完全不参与（用户看到"只跑一半"）。
      · 本版（对）：**设备序列原样返回**；收窄由 `narrowed_params()` 完成 ——
        相位步进类参数 × 1/width（每台设备的相位位移变大 = 波峰更窄），
        剖面宽度类参数 × width。时间项不动 → 波峰仍从序列头跑到尾。

    参数收窄在 `_run_compute_fn` 里以 `params = narrowed_params(params)` 应用
    （本函数只保留"返回原序列"的语义壳，避免调用点到处改）。

    ★★ R141 缺口：`narrowed_params()` 只对**真的消费**宽度类参数的预设有效 ——
    实测全量 1185 个预设里只有 382 个在 width=0.5 时输出会变，**803 个完全无反应**
    （内核自己用 seq_i 算相位，宽度键要么没声明、要么是装饰性声明）。用户拖这个
    滑块对它们曾是空操作。补齐见 `_effect_width_profile_apply()`（剖面重采样），
    生效名单见 `effect_presets_data_width_r141`（实测生成）。
    """
    return list(sorted_fixtures or [])


#: 剖面重采样至少要覆盖的设备数（更短的序列上"环绕点采"退化成镜像/换位而非收窄）。
_WIDTH_PROFILE_MIN_FIXTURES = 4


def _width_profile_map(n: int, w: float) -> Optional[list]:
    """收窄映射：第 k 台设备改取序列上第 `src[k]` 台设备的值。

    剖面被压缩到 1/w 倍空间频率后**环绕**（`% n`）：波峰仍会从序列头跑到尾
    （只是同时重复出现 `floor(1/w)` 次）——这正是用户 2026-09-20 要的
    "蛇身变短、仍跑完整个迷宫"，而**不是**截取中间一段。

    返回 None = 不产生任何变化（零写入 = 零回归）。
    """
    if n < _WIDTH_PROFILE_MIN_FIXTURES:
        return None
    w_eff = max(float(w), 2.0 / n)     # 下限：至少两台设备一个周期，波峰必有落点
    if w_eff >= 0.999:
        return None
    center = (n - 1) / 2.0
    src = [int(round(center + (k - center) / w_eff)) % n for k in range(n)]
    if all(s == k for k, s in enumerate(src)):
        return None
    return src


def _width_profile_selected(preset_id: Any, params: Any, n: int) -> Optional[list]:
    """需要走"剖面重采样"时返回映射表，否则 None。

    守卫顺序即开销顺序：非 dict / width≥1（**默认 8.0 → 第一步就返回**）→
    名单内（参数路径已生效，**逐位零回归**）→ 序列太短 → 映射退化。
    """
    if not isinstance(params, dict) or width_factor(params) >= 0.999:
        return None
    if _width_support.profile_handled(params) or str(preset_id or "") in WIDTH_PARAM_PATH_PRESETS:
        return None
    return _width_profile_map(n, width_factor(params))


def _width_profile_snapshot(ef, idx: list):
    """取序列各设备的通道值快照；设备通道集不一致时返回 None（不做原地换值）。"""
    rows = []
    for orig_i in idx:
        if not (0 <= orig_i < len(ef.values)):
            return None
        rows.append(ef.values[orig_i])
    first = frozenset(rows[0].keys()) if rows else frozenset()
    for vals in rows:
        if frozenset(vals.keys()) != first:
            return None
    return [dict(v) for v in rows]


def _effect_width_profile_apply(ef, sorted_fixtures: list, params: Dict[str, Any],
                                preset_id: Any = None,
                                per_shape_units: bool = False) -> None:
    """通用「效果宽窄」：按**剖面重采样**收窄波峰（对参数路径覆盖不到的预设生效）。

    语义与 `narrowed_params()` 完全一致（R118 用户定案）：设备序列不变、全部
    设备都参与、只有波峰变窄；区别只是对"内核自己算相位"的预设改为在
    **结果剖面上**做等价的 1/w 空间重采样（点采 + 环绕，峰值幅度不衰减）。

    零回归四条（缺一不可）：
      ① width≥1（默认 8.0，UI 初始化值）→ `_width_profile_selected` 第一道返回 None；
      ② 名单内（参数路径实测生效的 382 个）→ 跳过，输出逐位不变；
      ③ 序列 <4 台 / 映射退化为恒等 / 各设备通道集不一致 → 不写一个字节；
      ④ per_shape 单元块（块内各自成圆）→ 跳过，否则破坏"时钟指针"同步。
    """
    if _width_execution.profile_complete(ef, sorted_fixtures, params, per_shape_units):
        return
    fixtures = list(sorted_fixtures or [])
    src = _width_profile_selected(preset_id, params, len(fixtures))
    if src is None:
        return
    idx = [orig_i for orig_i, _fi in fixtures]
    snap = _width_profile_snapshot(ef, idx)
    if snap is None:
        return
    for k, orig_i in enumerate(idx):
        ef.values[orig_i].update(snap[src[k]])



def _append_subset_params(preset, existing_keys) -> None:
    """追加 **子集 delay 相位**（唯一一个子集参数）——抽出来压 `_append_universal_params` 行数。

    用户需求原话::

        "我选中一组设备，让他们 effect 整体往复运动，再选中这个设备里面的奇数个，
         我想让他们跟偶数个的 delay 相位差 180 度，即就能让奇数和偶数个按照
         「delay 相位」可追溯的精确拉开差距。"
        "我明明要的是我根据 topview 里面选中的子集去改选中什么设备为子集就改哪些
         设备的效果，根本不要来源和子集数目，完全是你自己乱加"

    ★★ R170 定案：**TopView 里选中谁，谁就是子集**。所以这里只有相位一个参数 ——
      「子集数」「子集来源」都已删除（用户明确否掉；"奇数/偶数错相"直接在 TopView 用
      奇偶选择工具选中即可，不需要引擎再猜）。
    """
    if "subset_delay_phase" not in existing_keys:
        preset.params.append(ParamSpec(
            "subset_delay_phase", "子集 delay 相位", "float", 0.0, 0.0, 360.0, 1.0,
            unit="°",
            tooltip="【TopView 里选中的那些设备】相对母集合余下的**波形相位差**。"
                    "例：42 台电机整体摇摆时，在 TopView 选中奇数台再调 180° → "
                    "奇偶交叉摆（一组起一组落）。360°=正好挪一个完整波峰(波形重合)。"
                    "0=关。未在 TopView 选中子集时本参数无效。"))


def _append_universal_params(preset) -> None:
    """给每个预设追加**通用参数**（wings/block/宽窄/effect 相位/子集 delay 相位…）。

    从 `EffectEngine._reg` 抽出：`_reg` 是上帝类方法、有行数/CC 门禁，而这些追加
    逻辑只依赖 `preset.params`，抽到模块级可读性更好、也好单测。
    """
    # ★ 通用空间参数：所有 Programmer 效果均支持"翼 / 块"。
    # wings：把排序后的设备拆成 N 段，每段从同一个 local index 开始跑完全一致的波形；
    # block_size：相邻 N 台设备作为一个同步单元，共用同一个效果值，形成阶梯感。
    existing_keys = {p.key for p in preset.params}
    from .effect_wing_controls import append_specs
    append_specs(preset, existing_keys)
    if "effect_width" not in existing_keys:   # ★ 2026-09-20 修正旧文案
        preset.params.append(ParamSpec(
            "effect_width", "效果宽窄", "float", 1.0, 0.001, 1.0, 0.001,
            tooltip="波峰宽窄：0.001–1.000，1=原样，0.5≈波峰细一半；效果仍跑完全部设备。旧工程大于1的原值保留，显示为1。"))
    # ★ 通用相位铺开（B2）：按虚拟同步单元在时间上注入归一化相位偏移，
    # 让整组设备在一个波周期内依次错相形成波浪。默认 0=关，逐字节走原路径。
    # ★★ R170 恢复原名（用户纠正）：R165 曾把它改名成「effect 相位（组内逐台铺开）」，
    #   但用户说的「effect 相位」指的是**效果自己本来就有的相位步长类参数**
    #   （`phase_step` 等，由各预设自带），再挂一个同名的通用控件是**冗余**。
    #   故恢复本名「相位铺开 / 波浪错相」，与预设自带的相位参数、以及
    #   「子集 delay 相位」（子集**之间**的相位差）三者互不重名。
    if "phase_spread" not in existing_keys:
        preset.params.append(ParamSpec(
            "phase_spread", "相位铺开/波浪错相", "float", 0.0, 0.0, 1.0, 0.01,
            tooltip="按设备顺序在时间上铺开相位：0=关(整组同相)，>0 时整组在一个波周期内"
                    "依次错相形成波浪(同一块内成员同相)。子集之间的相位差请用『子集 delay 相位』。"))
    _append_subset_params(preset, existing_keys)
    # ★ 标量×轨迹正交合成（B1/D2）：用某强度通道(dimmer/pump)的值作为包络，
    # 收缩电机 motor_x/y/z 偏移的幅度——强度高走满轨迹、强度低收向中位 128。
    # source 为空或 gain=0 时完全关闭，电机值逐字节不变；强度通道本身不受影响。
    if "xy_envelope_source" not in existing_keys:
        preset.params.append(ParamSpec(
            "xy_envelope_source", "轨迹包络源", "choice", "",
            choices=["", "dimmer", "pump"],
            tooltip="选一个强度通道作为电机轨迹幅度的包络：强度高电机走满轨迹，强度低收向中位 128。空=关闭。"))
    if "xy_envelope_gain" not in existing_keys:
        preset.params.append(ParamSpec(
            "xy_envelope_gain", "轨迹包络强度", "float", 0.0, 0.0, 1.0, 0.01,
            tooltip="0=关(电机轨迹不受强度影响)，1=电机轨迹幅度完全由强度通道调制；强度通道本身的值不变。"))
    # ★ 跑动覆盖遮罩(route_mask) 开关: 默认关闭（零影响铁律），由用户按效果手动勾选。
    if "route_mask_enabled" not in existing_keys:
        preset.params.append(ParamSpec(
            "route_mask_enabled", "接收跑动编排", "bool", False,
            tooltip="勾选后本效果输出跟随跑动覆盖遮罩（未覆盖设备强度通道=0、"
                    "电机中位128）；不勾=完全不受跑动编排影响。"))


class EffectEngine:
    """
    效果引擎：
    - 注册所有内置预设
    - 根据参数和设备列表计算 EffectFrame 序列
    - 支持自定义图案
    """

    def __init__(self):
        self._presets: Dict[str, EffectPreset] = {}
        # R173 下架预设：不入列表，但按 id 仍可解析（旧场景/自定义效果兼容，见 effect_retire）
        self._retired_presets: Dict[str, EffectPreset] = {}
        self._user_patterns: Dict[str, PatternDef] = {}
        self._compute_fns: Dict[str, Callable] = {}
        # ★ 性能：compute_at 排序/聚块/方向链记忆化。R173 前是单槽（key==比较），
        #   多 effect/多通道共享同一个 EffectEngine 实例时互相挤占，且 key 只放
        #   id() 不持强引用——GC 后 id 被别的 fixtures/positions 对象复用会误命中
        #   （P0：排错设备的方向/排序）。改 IdentityCache：guards 持
        #   (fixtures, positions, base_order_ids, _unit_groups) 强引用 + `is`
        #   复核，多槽（容量 32）避免多通道互相挤占。
        self._sort_chain_cache = IdentityCache(32)
        # ★ 性能：跑动遮罩门按 sorted_fixtures 缓存设备 id/下标，按 mask_map
        #   对象缓存对齐后的 mask 值，避免 Programmer 多通道每帧重复 11382 次
        #   getattr/str/dict 查找。
        # ★ R173：旧版 dict + 整表 clear() → IdentityCache 逐出最旧条目，避免
        #   多样化 clip/effect 组合频繁全清导致 ~16ms 重算尖刺。
        self._route_mask_fixture_cache = IdentityCache(64)
        # ★ R173：旧版键含 id(sorted_fixtures) 但只复核 mask_map，未对
        #   sorted_fixtures 持强引用/复核——sorted_fixtures 被回收后 id 被别的
        #   列表复用会误命中，返回按旧设备顺序对齐的 mask 值（P0：写错设备）。
        #   改 IdentityCache：guards=(sorted_fixtures, mask_map) 双持强引用 +
        #   `is` 复核。
        self._route_mask_values_cache = IdentityCache(64)
        # ★ R173：per_shape 分块缓存。guards=(sorted_fixtures, unit_groups,
        #   fixture_groups) 强引用 + `is` 复核；spatial_groups 每帧新拷贝改用
        #   内容签名键。容量 64，满时只逐出最旧一条（bounded_put），不做整表清空。
        self._unit_blocks_cache = IdentityCache(64)
        self._register_all()

    # ── 注册 ─────────────────────────────────────────────────────────────────

    def _reg(self, preset: EffectPreset, fn: Callable):
        """注册效果预设。

        矩阵类效果不再被过滤，而是在注册入口统一归入"矩阵效果"。
        这样音频桥接的一键随机可以只勾选这个分类，实现矩阵效果与
        普通效果的明确隔离，同时保留全部已有矩阵预设。
        """
        fields = [
            getattr(preset, 'id', ''),
            getattr(preset, 'name', ''),
            getattr(preset, 'category', ''),
            getattr(preset, 'description', ''),
            ' '.join(getattr(preset, 'tags', []) or []),
        ]
        marker = ' '.join(str(x) for x in fields).lower()
        preset.category = normalize_effect_category(getattr(preset, 'category', ''))
        # ★ 自动扩展关键参数范围
        for p in preset.params:
            if p.key == "speed" or "速度" in p.label or "speed" in p.key.lower():
                p.min_val = 0.0  # 允许 speed=0（摇杆幅度控制模式需要）
            if _angle_decl_widens(getattr(preset, 'id', ''), p):
                p.max_val = max(p.max_val, 360.0)  # 扩展角度范围
            if p.key == "radius" or "半径" in p.label:
                p.max_val = max(p.max_val, 180.0)  # 扩展半径范围
            if p.key == "amplitude" or "幅度" in p.label:
                p.max_val = max(p.max_val, 2.0)  # 扩展幅度范围
        _append_universal_params(preset)
        self._presets[preset.id] = preset
        self._compute_fns[preset.id] = fn

    def _register_all(self):
        self._register_wave_presets()
        self._register_chase_presets()
        self._register_fader_presets()
        self._register_matrix_presets()
        self._register_joystick_presets()
        self._register_audio_presets()

    # ── 公共接口 ─────────────────────────────────────────────────────────────

    @property
    def presets(self) -> Dict[str, EffectPreset]:
        return dict(self._presets)

    def all_presets(self) -> List[EffectPreset]:
        """返回所有已注册预设的 list（按 id 排序）。

        ★ AudioBridgePanel 依赖此方法填充预设下拉框。原代码只暴露了 presets
        property，audio_bridge 调用 eng.all_presets() 抛 AttributeError 被静默
        吞掉，导致桥接器的预设导入选择全部为空。
        """
        return sorted(self._presets.values(), key=lambda p: p.id)

    def get_preset(self, preset_id: str) -> Optional[EffectPreset]:
        preset = self._presets.get(preset_id)
        if preset is None:
            preset = self._retired_presets.get(preset_id)
        return preset

    def get_by_category(self, category: str) -> List[EffectPreset]:
        return [p for p in self._presets.values() if p.category == category]

    def all_patterns(self) -> Dict[str, PatternDef]:
        d = dict(BUILTIN_PATTERNS)
        d.update(self._user_patterns)
        return d

    def add_user_pattern(self, p: PatternDef):
        self._user_patterns[p.id] = p

    def remove_user_pattern(self, pid: str):
        self._user_patterns.pop(pid, None)

    def _control_group_map(self, groups: list) -> dict:
        """按组对象身份缓存 {fid: [组内全部 fid]} 成员归属表（只读，播放期稳定）。"""
        cache = getattr(self, "_control_group_map_cache", None)
        if cache is None:
            cache = self._control_group_map_cache = {}
        key = id(groups)
        hit = cache.get(key)
        if hit is not None and hit[0] is groups:
            return hit[1]
        group_of = {}
        for g in groups:
            fids = [str(f) for f in (g or []) if str(f)]
            if len(fids) < 2:
                continue
            for f in fids:
                group_of[f] = fids
        cache[key] = (groups, group_of)
        return group_of

    def _apply_control_groups(self, ef: EffectFrame, sorted_fixtures: list,
                              params: Dict[str, Any]) -> None:
        """整体控灯(粒度,与坐标系 orthogonal):包(kind=package)内成员共享同一效果值。

        工程 logical_devices 中 kind="package" 的包(如「气爆+4 灯」整套设备,
        导入时建立 member_fixture_ids)由 effect_browser 注入
        _control_groups(成员 fid 表)。整体控灯(_control_mode != "single")时,
        包内成员取同组**第一个成员**的效果值广播——4 灯当作一盏灯控制;
        单控灯(single)不生效,包内成员各自独立值。本逻辑是粒度维度,
        与 spatial_scope(各自/整体坐标系)正交,任何坐标系下都成立。

        ★ 2026-08-04 固定整体控设备(如数控,设计上写死只能整体控):即使
        _control_mode=single 也恒整体控——由 effect_browser 注入
        _fixed_whole_groups(固定整体控包的成员表,是 _control_groups 子集),
        本方法在 single 时只对这些组执行广播。
        """
        mode = str(params.get("_control_mode", "whole") or "whole").strip().lower()
        groups = params.get("_control_groups")
        if mode == "single":
            # 单控灯：只对固定整体控设备(数控等)仍整体控,其余逐个。
            # 能力表由 effect_browser 注入 _light_caps(fixed_whole_groups)。
            caps = params.get("_light_caps")
            fixed = caps.get("fixed_whole_groups") if isinstance(caps, dict) else None
            if not isinstance(fixed, (list, tuple)) or not fixed:
                return
            groups = fixed
        if not isinstance(groups, (list, tuple)) or not groups:
            return
        group_of = self._control_group_map(groups)
        if not group_of:
            return
        # 同组成员按 sorted 序聚拢,取第一个成员为值代表,其余成员复制其值。
        entries: dict = {}
        for _seq_i, (orig_i, fi) in enumerate(sorted_fixtures):
            members = group_of.get(str(getattr(fi, "id", "") or ""))
            if members:
                entries.setdefault(id(members), []).append(orig_i)
        for gid, origs in entries.items():
            if len(origs) < 2:
                continue
            rep = origs[0]
            if not (0 <= rep < len(ef.values)):
                continue
            base = dict(ef.values[rep])
            for oi in origs[1:]:
                if 0 <= oi < len(ef.values):
                    ef.values[oi].clear()
                    ef.values[oi].update(base)

    def _apply_fill_lights(self, ef: EffectFrame, sorted_fixtures: list,
                           params: Dict[str, Any]) -> None:
        """补光灯 = 效果灯总亮度 / 全开总亮度(0-100 映射),任何控制模式下恒成立。

        ★ 2026-08-04 用户实测修复(绕圈闪烁):两重灯设备(如百变:底座补光灯 +
        周围效果灯)的补光灯值 = 同包效果灯**当前总亮度 / 全开总亮度**——每灯
        亮度 = 最亮光通道(R/G/B/W)归一化,全开总亮度 = 效果灯灯数 N,再映射回
        0-255 DMX。绕圈(同一时刻 1/6 亮)恒定 16.7% 不闪;全熄灭 = 0;全开 =
        100%;半亮 = 50%。旧实现分母取「当帧有值的灯数」(逐帧漂移)且总亮度
        ≤0 时跳过 → chase 索引经过补光灯/喷头/泵时补光灯保留效果对自身的
        写入 → 每圈闪白 255(用户实测"一闪一闪")。
        ★ 跑动维持:效果灯总亮度 = 0 但选中集中仍有成员当帧非零(亮点落在
        喷头/泵/补光灯等非效果灯成员上,效果仍在跑动——per_shape 时钟同步下
        各设备圆同时刻亮点角度一致)→ 维持上一非零比例(绕圈恒定);全零帧
        (效果把全部成员驱动到 0,如波浪波谷/全熄灭)→ 补光灯 = 0。
        ★ 效果灯组**全部无光值**(效果未驱动光通道,如纯泵/阀效果)时不动
        补光灯(保持自身值)。只对**确实有补光灯+效果灯两重灯**的设备生效
        (当前至冬工程仅百变);气爆/数控等无补光灯设备不适用(常规控灯)。

        effect_browser 注入能力表 _light_caps.fill_map = {补光灯 fid: [同包
        效果灯 fid, ...]};无注入时零开销直返。与坐标系/粒度均正交。
        """
        caps = params.get("_light_caps")
        fill_map = caps.get("fill_map") if isinstance(caps, dict) else None
        if not isinstance(fill_map, dict) or not fill_map:
            return
        # fid → 输出槽 orig_i(按选中设备列表构建,播放期稳定)。
        cache = getattr(self, "_fill_slot_cache", None)
        if cache is None:
            cache = self._fill_slot_cache = {}
        key = (id(fill_map), id(sorted_fixtures))
        hit = cache.get(key)
        if hit is not None and hit[0] is fill_map and hit[1] is sorted_fixtures:
            slots = hit[2]
        else:
            idx_of = {}
            for orig_i, fi in (sorted_fixtures or []):
                idx_of[str(getattr(fi, "id", "") or "")] = int(orig_i)
            slots = {}
            for fid, effs in fill_map.items():
                fill_oi = idx_of.get(str(fid))
                eff_ois = [oi for oi in (idx_of.get(str(e)) for e in (effs or []))
                           if oi is not None]
                if fill_oi is not None and eff_ois:
                    # [补光灯槽, 效果灯槽表, 跑动比例(初值 = 1/N:首帧即落在
                    # 非灯成员上时按"1 盏点亮"计,绕圈首帧不闪)]
                    slots[fid] = [fill_oi, eff_ois, 1.0 / len(eff_ois)]
            cache[key] = (fill_map, sorted_fixtures, slots)
        if not slots:
            return
        vals = ef.values
        # 惰性扫描(仅当某补光灯组效果灯总亮度 = 0 时触发一次):选中集中任一
        # 成员当帧非零 → 效果仍在跑动(亮点在非效果灯成员上)→ 维持跑动比例;
        # 全零帧 → 补光灯 = 0。正常帧所有组总亮度 > 0 → 零开销。
        any_nonzero = None  # None=未扫描;False/True=本次调用结果
        for fid, slot in slots.items():
            fill_oi, eff_ois = slot[0], slot[1]
            if not (0 <= fill_oi < len(vals)):
                continue
            # 效果灯总亮度(0-1):每灯亮度 = 最亮光通道(R/G/B/W)归一化;无光键
            # (效果未驱动该灯光通道)的灯按 0 计(单控灯下未点亮 = 熄灭)。
            any_light = False
            total = 0.0
            for oi in eff_ois:
                if 0 <= oi < len(vals):
                    fv = vals[oi]
                    mx = 0
                    for ckey in ("R", "G", "B", "W"):
                        v = fv.get(ckey)
                        if v is not None:
                            any_light = True
                            mx = max(mx, int(v))
                    total += mx / 255.0
            if not any_light:
                continue
            if total <= 0.0:
                if any_nonzero is None:
                    any_nonzero = False
                    for fv in vals:
                        if not fv:
                            continue
                        for v in fv.values():
                            if int(v) != 0:
                                any_nonzero = True
                                break
                        if any_nonzero:
                            break
                # 效果灯全 0:效果仍在跑动(亮点在非灯成员上)→ 维持上一非零
                # 比例(绕圈恒定不闪);全零帧(全熄灭/波谷)→ 0。
                ratio = slot[2] if any_nonzero else 0.0
            else:
                ratio = total / len(eff_ois)
                slot[2] = ratio
            fill_dmx = int(round(ratio * 255.0))
            fill_dmx = max(0, min(255, fill_dmx))
            # 补光灯输出 = 总亮度比例映射到 0-255(白光伪装"照明来自效果灯")；
            # 其余非光通道(如 function/valve)原样保留。
            out = dict(vals[fill_oi])
            for ckey in ("R", "G", "B", "W"):
                out[ckey] = fill_dmx
            vals[fill_oi] = out

    def _per_shape_unit_blocks(self, sorted_fixtures: list,
                               params: Dict[str, Any]):
        """per_shape(各自坐标系)模式:按 _unit_groups 把选中设备切成「单元块」。

        ★ 2026-08-04 时钟同步的通用机制:每台多灯设备(百变 6 灯/气爆 4 灯/
        副池 6 灯…任何配灯包)的配灯在**各自圆**上做效果,同一时刻第 k 盏灯
        必须处于相同相对角度(时钟指针)。本方法把每个单元的成员聚成一个块
        (顺序即 apply_spatial_scope 的 0° 基准极角序,含方向翻转);非单元
        成员(单件)聚成末尾一块(保持相对顺序)。无单元块时返回 None →
        _run_compute_fn 走原路径(与旧行为一致)。

        ★ 2026-08-08 无配灯单元回退:logical_devices 恒空的工程(To The Moon
        类,9 圆 210 台)per_shape 无块 → 210 台一条扁平序列,末尾圆被压缩在
        波形尾部(用户实测「卡住后全亮」)。回退来源按优先级:
          1. _output_spatial_groups —— TopView 图形归属分组(effect_browser 已
             注入,layout shapes 即用户看到的圆/弧线,分段按图形不相交);
          2. _fixture_groups —— 工程设备组(effect_browser 注入的 dict 列表);
        每块独立 0..m-1 序列、组内按 sorted_fixtures 基准序(选择序/CCW 弧序),
        组外设备进末尾 singles——每圆独立序列,末尾圆不再被压缩。有
        _unit_groups 时回退不触发,单元路径逐字节不变。
        """
        if normalize_spatial_scope(params.get("spatial_scope")) != "per_shape":
            return None
        unit_groups = params.get("_unit_groups")
        spatial_groups = params.get("_output_spatial_groups")
        fixture_groups = params.get("_fixture_groups")
        has_units = isinstance(unit_groups, (list, tuple)) and bool(unit_groups)
        has_spatial = isinstance(spatial_groups, (list, tuple)) and bool(spatial_groups)
        has_fgroups = isinstance(fixture_groups, (list, tuple)) and bool(fixture_groups)
        if not (has_units or has_spatial or has_fgroups):
            return None
        # ★ 性能(2026-08-04 1204 单元实测,R173 更新):分块解析每帧 ~16ms(8336 台)。
        #   键=(id(sorted_fixtures), id(_unit_groups), spatial_groups 内容签名,
        #   id(_fixture_groups))——sorted_fixtures/_unit_groups/_fixture_groups
        #   为播放期稳定对象仍走 id+强引用+is 复核；spatial_groups 由
        #   effect_browser 每帧新拷贝返回，改用内容签名键才能命中。
        #   R173 后改用 IdentityCache：guards 持强引用 + `is` 复核，满时只逐出
        #   最旧一条（bounded_put），不做整表清空（cache.clear() 反模式）。
        spatial_groups_sig = (tuple(fixture_ids_signature(s) for s in spatial_groups)
                              if isinstance(spatial_groups, (list, tuple)) else None)
        content_key = (id(sorted_fixtures), id(unit_groups), spatial_groups_sig, id(fixture_groups))
        blocks = self._unit_blocks_cache.get((sorted_fixtures, unit_groups, fixture_groups), content_key)
        if blocks is not None:
            return blocks
        blocks = None
        if has_units:
            uid_by_fid = {}
            for uidx, g in enumerate(unit_groups):
                fids = [str(f) for f in (g or []) if str(f)]
                if len(fids) < 2:
                    continue
                for f in fids:
                    uid_by_fid[f] = uidx
            if uid_by_fid:
                blocks_by_unit = {}
                singles = []
                for item in sorted_fixtures:
                    fid = str(getattr(item[1], "id", "") or "")
                    uidx = uid_by_fid.get(fid)
                    if uidx is None:
                        singles.append(item)
                    else:
                        blocks_by_unit.setdefault(uidx, []).append(item)
                blocks = [blk for blk in blocks_by_unit.values() if len(blk) >= 2]
                if not blocks:
                    blocks = None
                elif singles:
                    blocks.append(singles)
        if blocks is None and has_spatial:
            # ★ 2026-08-08 回退一级:TopView 图形归属分组(layout shapes 即用户
            #   看到的 9 圆,分段按图形不相交)——按图形切块,每圆独立序列。
            segments = [[str(f) for f in (seg or []) if str(f)]
                        for seg in spatial_groups]
            blocks = _per_shape_blocks_from_groups(sorted_fixtures, segments)
            if not blocks:
                blocks = None
        if blocks is None and has_fgroups:
            # ★ 2026-08-08 回退二级:工程设备组(fixture_groups dict 列表,
            #   TopView 圆组)——无 layout shapes 时的兜底,按组基准序切块。
            ordered_groups = _per_shape_group_member_lists(fixture_groups)
            if not ordered_groups:
                return None
            blocks = _per_shape_blocks_from_groups(sorted_fixtures, ordered_groups)
            if not blocks:
                return None
        self._unit_blocks_cache.put((sorted_fixtures, unit_groups, fixture_groups), content_key, blocks)
        return blocks

    def _resolve_route_mask_ch_types(self, preset_id: Optional[str],
                                     params: Dict[str, Any]) -> Optional[set]:
        """解析当前 effect 实际执行的强度通道类型集合。

        单通道效果优先用 params['ch_type']（如 valve/pump 二选一）；多通道效果
        （如彩虹 R/G/B）没有 ch_type，回退到 preset.target_channels。返回 None
        时 _apply_route_mask 保持旧行为：遮罩帧内出现的全部强度通道。
        """
        if isinstance(params, dict):
            ch_type = params.get("ch_type")
            if ch_type:
                if isinstance(ch_type, (list, tuple, set, frozenset)):
                    return {str(c) for c in ch_type}
                return {str(ch_type)}
        if preset_id:
            preset = self.get_preset(preset_id)
            if preset is not None:
                targets = getattr(preset, "target_channels", None)
                if targets:
                    return {str(c) for c in targets}
        return None

    def _run_compute_fn(self, fn: Callable, ef: EffectFrame, t: float,
                        sorted_fixtures: list, fixture_defs: Dict[str, Any],
                        params: Dict[str, Any], fps: float,
                        preset_id: Optional[str] = None):
        """运行效果函数，并统一处理"翼 / 块"空间分组参数。

        ★ 宽窄（R118/R141 定案）：**设备序列不变**；读得到宽度参数的预设走
          `narrowed_params()` 收窄剖面参数（相位步进 ×1/width、剖面宽度 ×width），
          不读的由 `_finish` 的剖面重采样接管 → 波峰变窄但跑完全部设备。旧效果函数
        普遍使用 enumerate(sorted_fx) 的 seq_i 作为空间相位，故把真实设备队列转换成
        "虚拟同步单元"而不逐一改内核：
        - block_size > 1 时，连续 block_size 台设备映射到同一虚拟单元；
        - wings > 1 时，设备队列按翼数切成多个段，各段相同 local index 的虚拟单元
          复用同一计算结果，因此每个翼都会运行完全一致的波形（例如 2 翼就是
          两把一模一样同步打开的扇子）。
        计算结束后再把虚拟单元的通道值广播回真实设备。默认 wings=1、block_size=1
        时保持原计算路径，完全兼容旧工程。
        """
        # ★ 宽窄（R118）：不改设备序列、只收窄剖面参数（跑完全部设备）
        params = _width_capabilities.kernel_params(fn, params, preset_id)
        sorted_fixtures = _effect_width_subset(sorted_fixtures, params)
        scope = normalize_effect_application_scope(params.get("_app_scope", "both"))
        sequence_enabled = scope == "both"; width_enabled = scope in {"both", "sequence"}
        def _snapshot_time_for_sequence() -> float:
            try:
                speed = float(params.get("speed", params.get("rate", 1.0)) or 1.0)
            except Exception:
                speed = 1.0
            speed = max(0.001, abs(speed))
            wave_name = str(params.get("wave", "") or "")
            if any(k in params for k in ("open_val", "duty", "pulses")):
                phase = 0.0
            elif wave_name == "Cosine":
                phase = 0.0
            elif wave_name == "Triangle":
                phase = 0.5
            else:
                phase = 0.25
            return phase / speed

        effect_t = _snapshot_time_for_sequence() if scope == "sequence" else t

        # ── B2 相位铺开（通用）────────────────────────────────────────────────
        # phase_spread>0 时按虚拟单元注入时间相位 effect_t + spread*(k/(N-1))，
        # 整组一个波周期内依次错相；块内同相（与 wings/block 广播正交）。
        try:
            phase_spread = float(params.get("phase_spread", 0.0) or 0.0)
        except Exception:
            phase_spread = 0.0
        phase_spread = max(0.0, min(1.0, phase_spread))
        spread_on = phase_spread > 1e-9

        # ── ★★★ R165 子集 delay 相位（用户需求；见 `_make_subset_ctx` docstring）──
        (subset_on, subset_dt, _subset_ids_of, _subset_routing_on) = _make_subset_ctx(params)
        if subset_on:
            # ★★★ R170：按**实测内核周期**修正相位差（旧式按 1/speed 假设，对大量预设
            #   会让 180° 变成"整数个真实周期"→ 相移不可见；见 `_measure_kernel_period`）。
            #   ★ 必须传**与真实渲染相同的完整设备列表**（不是前 8 台）：部分内核的
            #   几何由设备总数决定（`matrix_scroll_v` 的 `rows=ceil(n/cols)`），
            #   用子集测出的 dt 在整表上可能不可见（实测 N=16 时 8 台判可见、
            #   整表判不可见）。开销由 `_period_sample_budget` 按设备数封顶。
            subset_dt = _subset_dt_measured(
                (self, fn, params, preset_id,
                 list(sorted_fixtures), fixture_defs, fps),
                subset_dt)

        def _compute_unit_values(call_sorted, frame_len, read_indices):
            """对每个虚拟单元用各自的时间相位跑一次 fn，取回该单元的通道值。

            call_sorted 完整传入(保留 seq_i 语义)，只改时间；read_indices[k] 指明第 k
            个单元的输出落在 tmp.values 的哪个下标(真实路径=orig_i，虚拟路径=虚拟下标)。
            返回与 call_sorted 对齐的 values dict 列表。"""
            if not call_sorted:
                return []
            subs = _subset_ids_of(call_sorted)
            return _width_execution.time_values(
                (fn, effect_t, phase_spread, subset_dt, fixture_defs, params, fps),
                call_sorted, (frame_len, read_indices), subs, _unit_values_by_time)

        def _merge_values(prev: dict, vals: dict) -> dict:
            merge_channels = {"dimmer", "R", "G", "B", "W", "valve", "pump", "level"}
            motor_channels = {"motor_x", "motor_y", "motor_z"}
            merged = dict(prev or {})
            for ch, val in (vals or {}).items():
                if ch in motor_channels:
                    merged[ch] = val
                elif ch in merge_channels:
                    try:
                        merged[ch] = max(int(merged.get(ch, 0) or 0), int(val or 0))
                    except Exception:
                        merged[ch] = val
                else:
                    merged[ch] = val
            return merged

        def _finish():
            # ★★ R141 通用宽窄：参数路径覆盖不到的预设（803/1185）在**剖面**做 1/w 重采样
            #   （序列不变、波峰变窄）；per_shape 单元块跳过。名单内逐位不变。
            _effect_width_profile_apply(ef, sorted_fixtures, params, preset_id,
                                        unit_blocks is not None)
            self._apply_xy_envelope(ef, params)
            # 粒度(整体控灯)：包内成员共享同组第一个成员的效果值——4 灯当一个灯；与坐标系
            # (spatial_scope)正交，无 _control_groups 注入时零开销直返。补光灯(仅两重灯设
            # 备如百变) = 效果灯总亮度/全开比例(0-100 映射)；无 _fill_map 时同样零开销。
            self._apply_control_groups(ef, sorted_fixtures, params)
            self._apply_fill_lights(ef, sorted_fixtures, params)
            # 跑动覆盖遮罩门: 最后应用——效果勾选「接收跑动编排」时按各自通道类型接收遮罩
            # (未覆盖设备强度通道=0)；只遮该 effect 实际执行的通道类型, 不串台。
            mask_enabled = bool(params.get("route_mask_enabled", False))
            self._apply_route_mask(
                ef, sorted_fixtures, mask_enabled,
                self._resolve_route_mask_ch_types(preset_id, params) if mask_enabled else None)

        def _has_duplicate_indices() -> bool:
            seen = set()
            for orig_i, _fi in sorted_fixtures:
                if orig_i in seen:
                    return True
                seen.add(orig_i)
            return False

        def _run_per_shape_units(blocks):
            """原单元时间分发和局部下标保持，完成局部支持后才写真实设备。"""
            _width_execution.run_groups((fn, effect_t, fixture_defs, params, fps), ef, blocks,
                (phase_spread, subset_dt, _subset_ids_of, _subset_routing_on), _unit_values_by_time)

        def _sequence_target_value(ch: str, current: Any) -> Any:
            if scope != "sequence":
                return current
            ch_norm = str(ch)
            motor_channels = {"motor_x", "motor_y", "motor_z", "pan", "tilt"}
            keys = ("hi", "max", "open_val", "on_val")
            if ch_norm in motor_channels:
                keys = ("target_val", "end_val") + keys
            for key in keys:
                if key in params:
                    try:
                        return max(0, min(255, int(round(float(params.get(key))))))
                    except Exception:
                        pass
            if "base_pressure" in params and "amplitude" in params:
                try:
                    return max(0, min(255, int(round(
                        float(params.get("base_pressure")) + float(params.get("amplitude"))
                    ))))
                except Exception:
                    pass
            return current

        def _run_without_sequence(seq_fixtures):
            N = len(seq_fixtures)
            denom = max(1, N - 1)
            subs = _subset_ids_of(seq_fixtures)
            for k, (orig_i, fi) in enumerate(seq_fixtures):
                t_k = effect_t + _subset_offset_at(subs, k, subset_dt)
                if spread_on:
                    t_k = t_k + phase_spread * (k / denom)
                fn(ef, t_k, [(orig_i, fi)], fixture_defs, params, fps)
                if scope == "sequence" and 0 <= orig_i < len(ef.values):
                    vals = ef.values[orig_i]
                    for ch, val in list(vals.items()):
                        vals[ch] = _sequence_target_value(ch, val)

        def _run_with_virtual_sequence(seq_fixtures):
            virtual_sorted = [
                (seq_i if sequence_enabled else 0, fi)
                for seq_i, (_orig_i, fi) in enumerate(seq_fixtures)
            ]
            if spread_on or _subset_routing_on(virtual_sorted):
                # 每个虚拟单元用各自时间相位计算；写入下标即 virtual_sorted 的 seq_i。
                unit_vals = _compute_unit_values(
                    virtual_sorted, len(seq_fixtures),
                    [si for si, _fi in virtual_sorted])
            else:
                unit_vals = _width_execution.direct_values((fn, effect_t, fixture_defs, params, fps),
                    virtual_sorted, (len(seq_fixtures), [si for si, _fi in virtual_sorted]))
            merged_by_orig = {}
            for seq_i, (orig_i, _fi) in enumerate(seq_fixtures):
                if seq_i >= len(unit_vals):
                    continue
                merged_by_orig[orig_i] = _merge_values(merged_by_orig.get(orig_i, {}), unit_vals[seq_i])
            for orig_i, vals in merged_by_orig.items():
                if not (0 <= orig_i < len(ef.values)):
                    continue
                ef.values[orig_i].update(vals)

        # ── ★ 2026-08-04 各自坐标系(per_shape)时钟同步:空间相位按单元归一 ──
        # per_shape 模式(单控灯下的自然语义)下,每台多灯设备的配灯在各自圆上
        # 做效果,同一时刻第 k 盏灯必须同角度(时钟指针)——「波浪跑动」等
        # 位置驱动效果此前用全局 seq_i 算空间相位,跨单元相位漂移 → 不同步。
        # 每个单元独立跑 fn(单元内 seq_i 从 0 起),全部单元第 k 盏灯同相位。
        # 仅 per_shape + 存在单元块时生效;global/无单元走原路径,零回归。
        unit_blocks = self._per_shape_unit_blocks(sorted_fixtures, params)
        if unit_blocks is not None:
            _run_per_shape_units(unit_blocks)
            _finish()
            return

        try:
            wings = max(1, int(params.get("wings", 1) or 1))
        except Exception:
            wings = 1
        try:
            block_size = max(1, int(params.get("block_size", 1) or 1))
        except Exception:
            block_size = 1

        if (wings <= 1 and block_size <= 1
                and not params.get("wing_mirror") and not params.get("wing_reverse")):
            if not sequence_enabled:
                _run_without_sequence(sorted_fixtures)
            elif _has_duplicate_indices():
                _run_with_virtual_sequence(sorted_fixtures)
            elif spread_on or _subset_routing_on(sorted_fixtures):
                # 直跑路径：每个设备(虚拟单元)用各自时间相位计算，写回其 orig_i。
                unit_vals = _compute_unit_values(
                    sorted_fixtures, len(ef.values),
                    [orig_i for orig_i, _fi in sorted_fixtures])
                for k, (orig_i, _fi) in enumerate(sorted_fixtures):
                    if 0 <= orig_i < len(ef.values):
                        ef.values[orig_i].update(unit_vals[k])
            else:
                fn(ef, effect_t, sorted_fixtures, fixture_defs, params, fps)
            _finish()
            return

        if not sequence_enabled:
            _run_without_sequence(sorted_fixtures)
            _finish()
            return

        n = len(sorted_fixtures)
        if n <= 0:
            return

        from . import effect_wing_execution
        effect_wing_execution.run((fn, effect_t, fixture_defs, params, fps), ef, sorted_fixtures,
            (phase_spread, subset_dt, _subset_ids_of, _subset_routing_on), _unit_values_by_time)
        _finish()

    def _apply_xy_envelope(self, ef: EffectFrame, params: Dict[str, Any]):
        """B1/D2 标量×轨迹正交合成：用强度通道(source)的值作为包络收缩电机偏移。

        对每个单元：env = source 通道值 / 255 ∈[0,1]；把 motor_x/motor_y/motor_z
        的偏移按 128 + (val-128)*(1-gain + gain*env) 收缩——强度高(env→1)走满轨迹，
        强度低(env→0)收向中位 128。强度通道本身的值完全不变(正交)。
        source 为空或 gain<=0 时第一行 return，电机值逐字节不变。
        不触碰 xy_reference 的坐标旋转(它在出图末端独立运行)。
        """
        source = str(params.get("xy_envelope_source", "") or "").strip()
        try:
            gain = float(params.get("xy_envelope_gain", 0.0) or 0.0)
        except Exception:
            gain = 0.0
        if not source or gain <= 1e-9:
            return
        gain = max(0.0, min(1.0, gain))
        motor_channels = ("motor_x", "motor_y", "motor_z")
        for vals in ef.values:
            if not vals or source not in vals:
                continue
            try:
                env = max(0.0, min(1.0, int(vals[source]) / 255.0))
            except Exception:
                continue
            scale = (1.0 - gain) + gain * env
            for ch in motor_channels:
                if ch not in vals:
                    continue
                try:
                    v = int(vals[ch])
                except Exception:
                    continue
                vals[ch] = max(0, min(255, int(round(128 + (v - 128) * scale))))


    def _apply_param_envelopes(self, preset_id: str, t: float,
                               params: Dict[str, Any]) -> Dict[str, Any]:
        """D1 通用参数包络：在调用 compute_fn 前，把绑定了包络的参数从常数替换成
        包络在时刻 t 的值，使 effect 播放时该参数按曲线一直循环变化。

        对 630 个 compute 完全透明：它们仍只看到一个 float。未绑定任何包络时第一行
        即返回原 params（同一对象，零拷贝零开销），逐字节行为与原先完全一致。

        包络值是归一化 [0,1]，这里线性映射回该参数 ParamSpec 的 (min_val, max_val)，
        并按参数类型(int/float)取整，保证写入的值落在参数自身的合法范围内。
        """
        try:
            from .param_envelope import get_param_envelope_store
        except Exception:
            return params
        try:
            store = get_param_envelope_store()
            bindings = store.get_bindings(str(preset_id or ""))
        except Exception:
            return params
        if not bindings:
            return params

        preset = self.get_preset(preset_id)
        spec_by_key = {}
        if preset is not None:
            for sp in preset.params:
                spec_by_key[getattr(sp, "key", None)] = sp

        out = dict(params)
        for param_key, env_id in bindings.items():
            env = store.get(env_id)
            if env is None:
                continue
            try:
                norm = float(env.value_at(t))
            except Exception:
                continue
            norm = max(0.0, min(1.0, norm))
            sp = spec_by_key.get(param_key)
            if sp is not None:
                lo = float(getattr(sp, "min_val", 0.0))
                hi = float(getattr(sp, "max_val", 1.0))
                # 可选输出子范围：把 [0,1] 只映射到参数内的有用子区间(任一端 None=回退)。
                try:
                    rng = store.get_binding_range(str(preset_id or ""), param_key)
                except Exception:
                    rng = None
                if rng is not None:
                    r_lo, r_hi = rng
                    if r_lo is not None:
                        lo = float(r_lo)
                    if r_hi is not None:
                        hi = float(r_hi)
                val = lo + norm * (hi - lo)
                if str(getattr(sp, "type", "")) == "int":
                    val = int(round(val))
                out[param_key] = val
            else:
                # 未知参数：直接给归一化值（极少见，绑定 UI 只暴露已知数值参数）。
                out[param_key] = norm
        return out

    def _apply_route_mask(self, ef: EffectFrame, sorted_fixtures: list,
                          route_mask_enabled: bool = False,
                          ch_types: Optional[set] = None) -> None:
        """遮罩门: 默认开启（接收跑动编排），route_mask 已注册时按设备级 on/off 过滤。

        未覆盖设备输出关断, 覆盖设备按 mask/255 缩放。适配所有 effect/通道:
          - 强度类(dimmer/R/G/B/W/level/valve/pump): 关断=0, 缩放=val*m/255;
          - 电机/位置类(motor_x/y/z/pan/tilt): 关断=中位 128, 缩放围绕中位
            new=128+(val-128)*m/255 (避免把摆头拉到极端)。
        ch_types 非空时只遮 effect 实际执行的通道类型（不串台）；None 遮帧内
        出现的全部通道。
        """
        if not route_mask_enabled:
            return
        mask = _route_mask
        mask_map = _route_mask_map
        if mask is None and mask_map is None:
            return
        motor_channels = {"motor_x", "motor_y", "motor_z", "pan", "tilt"}
        active_channels = {str(c) for c in ch_types} if ch_types is not None else None
        values = ef.values
        n_vals = len(values)
        if mask_map is not None:
            orig_indices, m_list = self._route_mask_values(sorted_fixtures, mask_map)
        else:
            orig_indices = [oi for oi, _fi in sorted_fixtures]
            n_mask = len(mask)
            m_list = [int(mask[oi]) if 0 <= oi < n_mask else 255 for oi in orig_indices]
        self._apply_mask_values(
            values, orig_indices, m_list, active_channels, motor_channels, n_vals)

    def _route_mask_fixture_meta(self, sorted_fixtures: list) -> tuple:
        """按 sorted_fixtures 缓存 orig_i/id 列表（同一 tick 多通道复用）。"""
        content_key = len(sorted_fixtures)
        cached = self._route_mask_fixture_cache.get((sorted_fixtures,), content_key)
        if cached is not None:
            return cached
        orig_indices = [oi for oi, _fi in sorted_fixtures]
        fids = []
        for _oi, fi in sorted_fixtures:
            fid = getattr(fi, "id", "") or ""
            fids.append(fid if isinstance(fid, str) else str(fid))
        result = (orig_indices, fids)
        self._route_mask_fixture_cache.put((sorted_fixtures,), content_key, result)
        return result

    def _route_mask_values(self, sorted_fixtures: list,
                           mask_map: dict) -> tuple:
        """按 (sorted_fixtures, mask_map) 双对象缓存对齐后的 mask 列表（同一帧多通道复用）。"""
        orig_indices, fids = self._route_mask_fixture_meta(sorted_fixtures)
        m_key = (id(sorted_fixtures), id(mask_map))
        m_list = self._route_mask_values_cache.get((sorted_fixtures, mask_map), m_key)
        if m_list is None:
            get = mask_map.get
            m_list = [get(fid, 0) for fid in fids]
            self._route_mask_values_cache.put((sorted_fixtures, mask_map), m_key, m_list)
        return orig_indices, m_list

    def _apply_mask_values(self, values: list, orig_indices: list,
                           m_list: list, active_channels: Optional[set],
                           motor_channels: set, n_vals: int) -> None:
        """把 mask 列表应用到一帧 values（设备级 on/off + 强度缩放）。"""
        for oi, m in zip(orig_indices, m_list):
            if oi < 0 or oi >= n_vals:
                continue
            if m >= 255:
                continue
            vals = values[oi]
            for ch, v in vals.items():
                if active_channels is not None and ch not in active_channels:
                    continue
                if ch in motor_channels:
                    if m == 0:
                        vals[ch] = 128
                    else:
                        vals[ch] = int(round(128.0 + (float(v) - 128.0) * m / 255.0))
                else:
                    if m == 0:
                        vals[ch] = 0
                    else:
                        vals[ch] = int(round(float(v) * m / 255.0))

    def compute(self, preset_id: str, params: Dict[str, Any],
                fixtures: list, fixture_defs: Dict[str, Any],
                positions: Dict[str, dict],
                duration: float = 4.0, fps: float = 40.0,
                sort_mode: str = "index",
                base_order_ids: Optional[List[str]] = None) -> List[EffectFrame]:
        """
        计算效果序列。
        返回 duration 秒 * fps 帧 的 EffectFrame 列表。
        """
        fn = self._compute_fns.get(preset_id)
        if fn is None:
            return []
        params = _audio_offline_params(preset_id, effective_params(preset_id, params))
        sorted_fixtures = _order_execution.ordered_fixtures(
            self, fixtures, positions, sort_mode=sort_mode,
            base_order_ids=base_order_ids, params=params)
        params = _order_execution.prepare_params(params, positions, ordered=sorted_fixtures)
        n = len(sorted_fixtures)
        if n == 0:
            return []
        n_frames = max(1, int(duration * fps))
        frames: List[EffectFrame] = []
        # Audio-reactive effects: skip if audio is required but not available.
        _audio_required = params.get("_audio_required", False) if isinstance(params, dict) else False
        _audio_provider = params.get("_audio_provider") if isinstance(params, dict) else None
        _use_provider = callable(_audio_provider)
        if _audio_required and not _use_provider:
            # No audio source available — return frames with lo values (silent)
            ch_key = params.get("ch_type", "dimmer") if isinstance(params, dict) else "dimmer"
            lo = int(params.get("lo", 0)) if isinstance(params, dict) else 0
            n_frames = max(1, int(duration * fps))
            frames = []
            mask_enabled = bool(params.get("route_mask_enabled", False))
            for frame_idx in range(n_frames):
                t = frame_idx / fps
                ef = EffectFrame(t=t)
                ef.values = [{ch_key: lo} for _ in range(len(fixtures))]
                self._apply_route_mask(
                    ef, sorted_fixtures, mask_enabled,
                    self._resolve_route_mask_ch_types(preset_id, params) if mask_enabled else None)
                frames.append(ef)
            return frames
        for frame_idx in range(n_frames):
            t = frame_idx / fps
            ef = EffectFrame(t=t)
            ef.values = [{} for _ in range(len(fixtures))]
            frame_params = params
            if _use_provider:
                try:
                    ad = _audio_provider(t)
                    if ad:
                        frame_params = dict(params)
                        frame_params["_audio_data"] = ad
                        frame_params["_audio_frame"] = ad
                        if ad.get("has_audio"):
                            frame_params["bpm"] = ad.get("bpm", frame_params.get("bpm", 120.0))
                except Exception:
                    frame_params = params
            frame_params = self._apply_param_envelopes(preset_id, t, frame_params)
            self._run_compute_fn(fn, ef, t, sorted_fixtures, fixture_defs,
                                 frame_params, fps, preset_id)
            frames.append(ef)
        return frames

    def compute_at(self, preset_id: str, t: float, params: dict,
                   fixtures: list, fixture_defs: dict,
                   positions: dict,
                   fps: float = 40.0,
                   sort_mode: str = "index",
                   base_order_ids: Optional[List[str]] = None):
        """
        计算指定时刻 t（秒）的单帧效果，用于连续实时播放。
        比 compute() 开销小，不分配整个帧序列。
        返回 EffectFrame 或 None（preset_id 不存在时）。

        ★ 摇杆幅度控制模式：
          当 params 中包含 joy_amplitude_x / joy_amplitude_y 时，
          如果 speed==0，则先用 speed=极小值 计算效果形态快照，
          然后将输出偏移量乘以 joy_amplitude 因子。
          如果 speed>0，则在正常计算后将偏移量乘以 joy_amplitude 因子。
        """
        fn = self._compute_fns.get(preset_id)
        if fn is None:
            return None
        # D1 通用参数包络：把绑定包络的参数替换成时刻 t 的曲线值（未绑定时零开销）。
        params = self._apply_param_envelopes(preset_id, t, params)
        # ★★ R118 推进族方向名镜像（需在排序链之前，否则排序与相位源打架）
        params = effective_params(preset_id, params)
        sorted_fixtures = _order_execution.ordered_fixtures(
            self, fixtures, positions, sort_mode=sort_mode,
            base_order_ids=base_order_ids, params=params)
        params = _order_execution.prepare_params(params, positions, ordered=sorted_fixtures)
        if not sorted_fixtures:
            return None

        # ★ 摇杆幅度控制逻辑
        #   ★ R141：三个键都参与判定（原先只看 x/y → `joy_amplitude_z` 是死键）。
        joy_amp_x = params.get("joy_amplitude_x")
        joy_amp_y = params.get("joy_amplitude_y")
        joy_amp_z = params.get("joy_amplitude_z")
        has_joy_amp = (joy_amp_x is not None) or (joy_amp_y is not None) or (joy_amp_z is not None)

        if has_joy_amp:
            joy_amp_x = max(-1.0, min(1.0, float(joy_amp_x))) if joy_amp_x is not None else None
            joy_amp_y = max(-1.0, min(1.0, float(joy_amp_y))) if joy_amp_y is not None else None
            joy_amp_z = max(-1.0, min(1.0, float(joy_amp_z))) if joy_amp_z is not None else None
            amps = (joy_amp_x, joy_amp_y, joy_amp_z)

            speed = float(params.get("speed", 0.0))
            actual_params = dict(params)
            # 移除 joy_amplitude 参数，避免传入效果函数引起意外
            actual_params.pop("joy_amplitude_x", None)
            actual_params.pop("joy_amplitude_y", None)
            actual_params.pop("joy_amplitude_z", None)

            if speed == 0 or abs(speed) < 0.001:
                # speed=0 模式：用一个参考速度计算形态快照
                # 使用 t=0.25/ref_speed 使 sin(2π*t*speed) 达到峰值(=1)
                # 这样能获取效果在最大振幅时的形态
                ref_speed = 1.0
                actual_params["speed"] = ref_speed
                ref_t = 0.25 / ref_speed  # sin(2π * 0.25) = 1.0（正峰值）

                ef = EffectFrame(t=ref_t)
                ef.values = [{} for _ in range(len(fixtures))]
                self._run_compute_fn(fn, ef, ref_t, sorted_fixtures, fixture_defs,
                                     actual_params, fps, preset_id)

                # 后处理：将偏移量乘以 joy_amplitude
                # 对于单轴效果（ch_type参数），判断目标通道
                ch_type_param = params.get("ch_type", "")

                for fv in ef.values:
                    for ch, val in list(fv.items()):
                        offset = val - 128
                        if offset == 0:
                            continue
                        # 确定使用哪个轴的 amplitude
                        amp = joy_amp_for_channel(ch, ch_type_param, amps)

                        if amp is not None:
                            new_val = 128 + offset * amp
                            fv[ch] = max(0, min(255, int(new_val)))
                # 摇杆后处理可能把已遮罩的强度通道重新抬高 → 最后再应用一次遮罩门
                mask_enabled = bool(params.get("route_mask_enabled", False))
                self._apply_route_mask(
                    ef, sorted_fixtures, mask_enabled,
                    self._resolve_route_mask_ch_types(preset_id, params) if mask_enabled else None)
                return ef

            else:
                # speed>0 模式：正常计算，然后缩放偏移量
                ef = EffectFrame(t=t)
                ef.values = [{} for _ in range(len(fixtures))]
                self._run_compute_fn(fn, ef, t, sorted_fixtures, fixture_defs,
                                     actual_params, fps, preset_id)

                ch_type_param = params.get("ch_type", "")

                for fv in ef.values:
                    for ch, val in list(fv.items()):
                        offset = val - 128
                        if offset == 0:
                            continue
                        amp = joy_amp_for_channel(ch, ch_type_param, amps)

                        if amp is not None:
                            new_val = 128 + offset * amp
                            fv[ch] = max(0, min(255, int(new_val)))
                # 摇杆后处理可能把已遮罩的强度通道重新抬高 → 最后再应用一次遮罩门
                mask_enabled = bool(params.get("route_mask_enabled", False))
                self._apply_route_mask(
                    ef, sorted_fixtures, mask_enabled,
                    self._resolve_route_mask_ch_types(preset_id, params) if mask_enabled else None)
                return ef

        # 无摇杆幅度控制：原始逻辑
        ef = EffectFrame(t=t)
        ef.values = [{} for _ in range(len(fixtures))]
        self._run_compute_fn(fn, ef, t, sorted_fixtures, fixture_defs,
                             params, fps, preset_id)
        return ef

    # ─────────────────────────────────────────────────────────────────────────
    #  波浪预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_wave_presets(self):

        def _wave_generic(ef, t, sorted_fx, fdefs, params, fps):
            wave_name = params.get("wave", "Sine")
            wave_fn = _width_support.generic_sampler(wave_name, params, t, sorted_fx, WAVE_FUNCS)
            speed = float(params.get("speed", 1.0))
            phase_step = float(params.get("phase_step", 0.1))
            lo = int(params.get("min", 0))
            hi = int(params.get("max", 255))
            ch = params.get("ch_type", "dimmer")
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                # 空间相位：per_shape=seq_i(每台设备自己的圆)；
                # global 整体坐标系=_matrix_coords 全局 x 连续投影(波浪整体扫过全矩阵)。
                phase = spatial_phase_index(params, str(fi.id), seq_i, len(sorted_fx)) * phase_step
                v = wave_fn(t * speed + phase)
                val = int(lo + v * (hi - lo))
                ef.values[orig_i][ch] = max(0, min(255, val))
        for wave_name in WAVE_FUNCS:
            wn = wave_name
            def make_fn(wn=wn):
                def fn(ef, t, sorted_fx, fdefs, params, fps):
                    p2 = dict(params)
                    p2["wave"] = wn
                    _wave_generic(ef, t, sorted_fx, fdefs, p2, fps)
                fn._cycle_rate_key = None if wn in ("Random", "S&H", "Wander") else "speed"
                return fn

            self._reg(EffectPreset(
                id=f"wave_{wave_name.lower()}",
                name=f"{wave_name} 波浪",
                category="Wave",
                description=f"{wave_name} 波形，按设备顺序依次相位偏移",
                target_channels=CH_TYPES,
                icon="〜",
                params=[
                    ParamSpec("ch_type", "通道类别", "choice", "dimmer",
                              choices=CH_TYPES,
                        tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                    ParamSpec("speed", "速度 (Hz)", "float", 1.0, 0.01, 20.0, 0.1,
                              unit="Hz",
                        tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                    ParamSpec("phase_step", "相位步长", "float", 0.1, 0.0, 1.0, 0.01,
                        tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                    ParamSpec("min", "最小值", "int", 0, 0, 255, 1,
                        tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                    ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                        tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
                ],
            ), make_fn())

        # 彩虹色波浪（RGB）
        def _rainbow_wave(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            phase_step = float(params.get("phase_step", 0.08))
            brightness = float(params.get("brightness", 1.0))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                # 空间相位：per_shape=seq_i；global 整体坐标系=全局 x 连续投影。
                hue = (t * speed + spatial_phase_index(params, str(fi.id), seq_i, len(sorted_fx)) * phase_step) % 1.0
                r, g, b = _hsv_to_rgb(hue, 1.0, brightness)
                ef.values[orig_i]["R"] = r
                ef.values[orig_i]["G"] = g
                ef.values[orig_i]["B"] = b

        self._reg(EffectPreset(
            id="wave_rainbow",
            name="彩虹波浪",
            category="Wave",
            description="RGB 彩虹色按顺序流动",
            target_channels=["R", "G", "B"],
            icon="🌈",
            params=[
                ParamSpec("speed", "速度", "float", 0.5, 0.01, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("phase_step", "相位步长", "float", 0.08, 0.0, 0.5, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                ParamSpec("brightness", "亮度", "float", 1.0, 0.0, 1.0, 0.05,
                    tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
            ],
        ), _rainbow_wave)

        # 双向波浪
        def _dual_wave(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.0))
            phase_step = float(params.get("phase_step", 0.15))
            lo = int(params.get("min", 0))
            hi = int(params.get("max", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                # 两个方向的波叠加；空间相位 per_shape=seq_i，
                # global 整体坐标系=全局 x 连续投影(波浪整体扫过全矩阵)。
                sp = spatial_phase_index(params, str(fi.id), seq_i, len(sorted_fx))
                v1 = (math.sin((t * speed + sp * phase_step) * 2 * math.pi) + 1) / 2
                v2 = (math.sin((t * speed - sp * phase_step) * 2 * math.pi) + 1) / 2
                v = (v1 + v2) / 2
                val = int(lo + v * (hi - lo))
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="wave_dual",
            name="双向波浪",
            category="Wave",
            description="两个方向的波形叠加，产生驻波效果",
            target_channels=CH_TYPES,
            icon="≋",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 1.0, 0.01, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("phase_step", "相位步长", "float", 0.15, 0.0, 1.0, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                ParamSpec("min", "最小值", "int", 0, 0, 255, 1,
                    tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                    tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _dual_wave)

    # ─────────────────────────────────────────────────────────────────────────
    #  追逐（Chase）预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_chase_presets(self):

        # 单点跳（每次只有一个设备亮）
        def _single_chase(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 2.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            if n == 0:
                return
            idx = int(t * speed * n) % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if seq_i == idx else lo

        self._reg(EffectPreset(
            id="chase_single",
            name="单点跳",
            category="Chase",
            description="每次只有一个设备亮，依次跳动",
            target_channels=CH_TYPES,
            icon="●",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度 (圈/秒)", "float", 2.0, 0.1, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _single_chase)

        # 三联条（每次3个设备亮）
        def _triple_chase(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 2.0))
            width = max(1, int(params.get("width", 3)))   # ★ 截断到 0 会整条熄灭
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            if n == 0:
                return
            head = int(t * speed * n) % n
            on_set = set((head + i) % n for i in range(width))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if seq_i in on_set else lo

        self._reg(EffectPreset(
            id="chase_triple",
            name="三联条",
            category="Chase",
            description="宽度可调的追逐条，默认3个设备同时亮",
            target_channels=CH_TYPES,
            icon="▌▌▌",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 2.0, 0.1, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度（设备数）", "int", 3, 1, 32, 1,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _triple_chase)

        # 双向追逐（两个追逐条相向而行）
        def _dual_chase(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 2.0))
            width = max(1, int(params.get("width", 2)))   # ★ 见 _triple_chase 同款修复
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            if n == 0:
                return
            head1 = int(t * speed * n) % n
            head2 = int(n - t * speed * n) % n
            on_set = set()
            for i in range(width):
                on_set.add((head1 + i) % n)
                on_set.add((head2 + i) % n)
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if seq_i in on_set else lo

        self._reg(EffectPreset(
            id="chase_dual",
            name="双向追逐",
            category="Chase",
            description="两个追逐条相向而行，碰撞后穿越",
            target_channels=CH_TYPES,
            icon="◄►",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 2.0, 0.1, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "int", 2, 1, 16, 1,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _dual_chase)

        # 彩色追逐（RGB 三色追逐）
        def _color_chase(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.5))
            n = len(sorted_fx)
            if n == 0:
                return
            colors = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (0, 255, 255), (255, 0, 255),
            ]
            n_colors = int(params.get("n_colors", 3))
            n_colors = max(1, min(n_colors, len(colors)))
            seg = max(1, n // n_colors)
            head = int(t * speed * n) % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                rel = (seq_i - head) % n
                ci = rel // seg % n_colors
                r, g, b = colors[ci]
                ef.values[orig_i]["R"] = r
                ef.values[orig_i]["G"] = g
                ef.values[orig_i]["B"] = b

        self._reg(EffectPreset(
            id="chase_color",
            name="彩色追逐",
            category="Chase",
            description="多色分段追逐，颜色数量可调",
            target_channels=["R", "G", "B"],
            icon="🎨",
            params=[
                ParamSpec("speed", "速度", "float", 1.5, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("n_colors", "颜色数量", "int", 3, 1, 6, 1,
                    tooltip="控制颜色数量的值。范围 1~6，值越大越明显"),
            ],
        ), _color_chase)

        # 随机闪烁
        def _random_strobe(ef, t, sorted_fx, fdefs, params, fps):
            rate = float(params.get("rate", 5.0))
            density = float(params.get("density", 0.3))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            rng = random.Random(int(t * rate * 1000))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if rng.random() < density else lo

        self._reg(EffectPreset(
            id="chase_strobe_random",
            name="随机闪烁",
            category="Chase",
            description="每帧随机选取部分设备闪烁",
            target_channels=CH_TYPES,
            icon="✦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("rate", "频率 (Hz)", "float", 5.0, 0.5, 40.0, 0.5,
                    tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
                ParamSpec("density", "密度 (0-1)", "float", 0.3, 0.0, 1.0, 0.05,
                    tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _random_strobe)

        # 全局频闪
        def _global_strobe(ef, t, sorted_fx, fdefs, params, fps):
            rate = float(params.get("rate", 8.0))
            duty = float(params.get("duty", 0.5))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            phase = (t * rate) % 1.0
            val = hi if phase < duty else lo
            for orig_i, fi in [iv for iv in enumerate(
                    [iv2[1] for iv2 in sorted_fx])]:
                ef.values[orig_i][ch] = val

        self._reg(EffectPreset(
            id="chase_strobe_global",
            name="全局频闪",
            category="Chase",
            description="所有设备同步频闪，可调频率和占空比",
            target_channels=CH_TYPES,
            icon="⚡",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("rate", "频率 (Hz)", "float", 8.0, 0.5, 40.0, 0.5,
                    tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
                ParamSpec("duty", "占空比", "float", 0.5, 0.05, 0.95, 0.05,
                    tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _global_strobe)

        # 交替闪烁（奇偶交替）
        def _alternate_flash(ef, t, sorted_fx, fdefs, params, fps):
            rate = float(params.get("rate", 4.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            phase = int(t * rate) % 2
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                is_on = (seq_i % 2) == phase
                ef.values[orig_i][ch] = hi if is_on else lo

        self._reg(EffectPreset(
            id="chase_alternate",
            name="奇偶交替",
            category="Chase",
            description="奇数和偶数位置设备交替闪烁",
            target_channels=CH_TYPES,
            icon="⬛⬜",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("rate", "频率 (Hz)", "float", 4.0, 0.5, 20.0, 0.5,
                    tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _alternate_flash)

        # 渐入渐出追逐（带拖尾）
        def _chase_fade(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.5))
            tail = int(params.get("tail", 4))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            if n == 0:
                return
            head = (t * speed * n) % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                dist = (head - seq_i) % n
                if dist < tail:
                    v = hi * (1.0 - dist / tail)
                    ef.values[orig_i][ch] = max(lo, int(v))
                else:
                    ef.values[orig_i][ch] = lo

        self._reg(EffectPreset(
            id="chase_fade_tail",
            name="拖尾追逐",
            category="Chase",
            description="追逐条带有渐变拖尾效果",
            target_channels=CH_TYPES,
            icon="☄",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 1.5, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("tail", "拖尾长度（设备数）", "int", 4, 1, 32, 1,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("on_val", "头部亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _chase_fade)

    # ─────────────────────────────────────────────────────────────────────────
    #  矩阵图案预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_matrix_presets(self):

        # 静态图案
        def _static_pattern(ef, t, sorted_fx, fdefs, params, fps):
            pattern_id = params.get("pattern_id", "builtin_棋盘格")
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pattern_id)
            if pat is None:
                return
            mask = pat.as_flat_mask(len(sorted_fx))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if mask[seq_i] > 0.5 else lo

        self._reg(EffectPreset(
            id="matrix_static",
            name="静态图案",
            category="Matrix",
            description="将选定图案静态应用到设备矩阵",
            target_channels=CH_TYPES,
            icon="▦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案", "pattern", "builtin_棋盘格",
                    tooltip="控制图案的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _static_pattern)

        # 图案滚动（横向）
        def _pattern_scroll_h(ef, t, sorted_fx, fdefs, params, fps):
            pattern_id = params.get("pattern_id", "builtin_垂直条纹")
            speed = float(params.get("speed", 1.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            cols = int(params.get("cols", 4))
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pattern_id)
            if pat is None:
                return
            offset = int(t * speed * cols) % cols
            n = len(sorted_fx)
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                row = seq_i // cols
                col = (seq_i % cols + offset) % cols
                val_bit = pat.get(row % pat.rows, col % pat.cols)
                ef.values[orig_i][ch] = hi if val_bit else lo

        self._reg(EffectPreset(
            id="matrix_scroll_h",
            name="图案横向滚动",
            category="Matrix",
            description="图案在矩阵中横向滚动",
            target_channels=CH_TYPES,
            icon="▶▦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案", "pattern", "builtin_垂直条纹",
                    tooltip="控制图案的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("cols", "矩阵列数", "int", 4, 1, 32, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "滚动速度", "float", 1.0, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _pattern_scroll_h)

        # 图案滚动（纵向）
        def _pattern_scroll_v(ef, t, sorted_fx, fdefs, params, fps):
            pattern_id = params.get("pattern_id", "builtin_水平条纹")
            speed = float(params.get("speed", 1.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            cols = int(params.get("cols", 4))
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pattern_id)
            if pat is None:
                return
            n = len(sorted_fx)
            rows = max(1, (n + cols - 1) // cols)
            offset = int(t * speed * rows) % rows
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                row = (seq_i // cols + offset) % rows
                col = seq_i % cols
                val_bit = pat.get(row % pat.rows, col % pat.cols)
                ef.values[orig_i][ch] = hi if val_bit else lo

        self._reg(EffectPreset(
            id="matrix_scroll_v",
            name="图案纵向滚动",
            category="Matrix",
            description="图案在矩阵中纵向滚动",
            target_channels=CH_TYPES,
            icon="▼▦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案", "pattern", "builtin_水平条纹",
                    tooltip="控制图案的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("cols", "矩阵列数", "int", 4, 1, 32, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "滚动速度", "float", 1.0, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _pattern_scroll_v)

        # 图案闪烁（整体图案周期性闪烁）
        def _pattern_blink(ef, t, sorted_fx, fdefs, params, fps):
            pattern_id = params.get("pattern_id", "builtin_棋盘格")
            rate = float(params.get("rate", 2.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pattern_id)
            if pat is None:
                return
            mask = pat.as_flat_mask(len(sorted_fx))
            blink_on = (int(t * rate) % 2) == 0
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                if mask[seq_i] > 0.5:
                    ef.values[orig_i][ch] = hi if blink_on else lo
                else:
                    ef.values[orig_i][ch] = lo

        self._reg(EffectPreset(
            id="matrix_blink",
            name="图案闪烁",
            category="Matrix",
            description="图案整体周期性闪烁",
            target_channels=CH_TYPES,
            icon="▦✦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案", "pattern", "builtin_棋盘格",
                    tooltip="控制图案的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("rate", "闪烁频率 (Hz)", "float", 2.0, 0.5, 20.0, 0.5,
                    tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _pattern_blink)

        # 图案渐变（图案内设备有亮度渐变）
        def _pattern_gradient(ef, t, sorted_fx, fdefs, params, fps):
            pattern_id = params.get("pattern_id", "builtin_对角线↘")
            speed = float(params.get("speed", 0.5))
            lo = int(params.get("min", 0))
            hi = int(params.get("max", 255))
            ch = params.get("ch_type", "dimmer")
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pattern_id)
            if pat is None:
                return
            mask = pat.as_flat_mask(len(sorted_fx))
            phase = (t * speed) % 1.0
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                if mask[seq_i] > 0.5:
                    v = (math.sin((phase + seq_i * 0.05) * 2 * math.pi) + 1) / 2
                    ef.values[orig_i][ch] = int(lo + v * (hi - lo))
                else:
                    ef.values[orig_i][ch] = lo

        self._reg(EffectPreset(
            id="matrix_gradient",
            name="图案渐变波",
            category="Matrix",
            description="图案内的设备有缓慢渐变效果",
            target_channels=CH_TYPES,
            icon="▦〜",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案", "pattern", "builtin_对角线↘",
                    tooltip="控制图案的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("min", "最小值", "int", 0, 0, 255, 1,
                    tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                    tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _pattern_gradient)

        # 图案交替（两个图案交替显示）
        def _pattern_alternate(ef, t, sorted_fx, fdefs, params, fps):
            pat1_id = params.get("pattern_id", "builtin_棋盘格")
            pat2_id = params.get("pattern2_id", "builtin_全亮")
            rate = float(params.get("rate", 1.0))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            all_pats = dict(BUILTIN_PATTERNS)
            all_pats.update(self._user_patterns)
            pat = all_pats.get(pat1_id if (int(t * rate) % 2 == 0) else pat2_id)
            if pat is None:
                return
            mask = pat.as_flat_mask(len(sorted_fx))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if mask[seq_i] > 0.5 else lo

        self._reg(EffectPreset(
            id="matrix_alternate",
            name="双图案交替",
            category="Matrix",
            description="两个图案交替显示",
            target_channels=CH_TYPES,
            icon="▦↔▦",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("pattern_id", "图案1", "pattern", "builtin_棋盘格",
                    tooltip="控制图案1的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("pattern2_id", "图案2", "pattern", "builtin_全亮",
                    tooltip="控制图案2的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("rate", "切换频率 (Hz)", "float", 1.0, 0.1, 10.0, 0.1,
                    tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _pattern_alternate)

        # 矩阵扫描行
        def _matrix_scan_row(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 2.0))
            cols = int(params.get("cols", 4))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            rows = max(1, (n + cols - 1) // cols)
            cur_row = int(t * speed) % rows
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                row = seq_i // cols
                ef.values[orig_i][ch] = hi if row == cur_row else lo

        self._reg(EffectPreset(
            id="matrix_scan_row",
            name="矩阵行扫描",
            category="Matrix",
            description="逐行扫描矩阵",
            target_channels=CH_TYPES,
            icon="▬",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("cols", "列数", "int", 4, 1, 32, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "速度 (行/秒)", "float", 2.0, 0.1, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _matrix_scan_row)

        # 矩阵扫描列
        def _matrix_scan_col(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 2.0))
            cols = int(params.get("cols", 4))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            cur_col = int(t * speed) % cols
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                col = seq_i % cols
                ef.values[orig_i][ch] = hi if col == cur_col else lo

        self._reg(EffectPreset(
            id="matrix_scan_col",
            name="矩阵列扫描",
            category="Matrix",
            description="逐列扫描矩阵",
            target_channels=CH_TYPES,
            icon="▮",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("cols", "列数", "int", 4, 1, 32, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "速度 (列/秒)", "float", 2.0, 0.1, 20.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _matrix_scan_col)

        # 矩阵螺旋
        def _matrix_spiral(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.0))
            cols = int(params.get("cols", 4))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            rows = max(1, (n + cols - 1) // cols)
            # 生成螺旋顺序
            spiral_order = _spiral_indices(rows, cols)
            cur = int(t * speed * n) % n
            on_idx = spiral_order[cur] if cur < len(spiral_order) else cur
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if seq_i == on_idx else lo

        self._reg(EffectPreset(
            id="matrix_spiral",
            name="矩阵螺旋",
            category="Matrix",
            description="按螺旋顺序依次点亮设备",
            target_channels=CH_TYPES,
            icon="🌀",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("cols", "列数", "int", 4, 1, 16, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _matrix_spiral)

        # 矩阵爆炸（从中心向外扩散）
        def _matrix_explode(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.5))
            cols = int(params.get("cols", 4))
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            ch = params.get("ch_type", "dimmer")
            n = len(sorted_fx)
            rows = max(1, (n + cols - 1) // cols)
            cx, cy = (cols - 1) / 2, (rows - 1) / 2
            radius = (t * speed) % (max(rows, cols) / 2 + 1)
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                row = seq_i // cols
                col = seq_i % cols
                dist = math.sqrt((col - cx)**2 + (row - cy)**2)
                ef.values[orig_i][ch] = hi if abs(dist - radius) < 0.8 else lo

        self._reg(EffectPreset(
            id="matrix_explode",
            name="矩阵爆炸",
            category="Matrix",
            description="从中心向外扩散的圆形波",
            target_channels=CH_TYPES,
            icon="💥",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("cols", "列数", "int", 4, 1, 16, 1,
                    tooltip="控制矩阵的列数。值越大每行设备越多"),
                ParamSpec("speed", "扩散速度", "float", 1.5, 0.1, 10.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _matrix_explode)

    # ─────────────────────────────────────────────────────────────────────────
    #  推杆（Fader）预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_fader_presets(self):

        # 线性渐变（全部设备从 A 渐变到 B）
        def _fader_linear(ef, t, sorted_fx, fdefs, params, fps):
            duration = float(params.get("duration", 4.0))
            val_a = int(params.get("val_a", 0))
            val_b = int(params.get("val_b", 255))
            ch = params.get("ch_type", "dimmer")
            progress = min(1.0, t / max(0.001, duration))
            val = int(val_a + (val_b - val_a) * progress)
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="fader_linear",
            name="线性推杆",
            category="Fader",
            description="所有设备同步从 A 值线性渐变到 B 值",
            target_channels=CH_TYPES,
            icon="▲",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("val_a", "起始值", "int", 0, 0, 255, 1,
                    tooltip="控制交替模式中第一组的值。用于奇偶分组交替输出"),
                ParamSpec("val_b", "目标值", "int", 255, 0, 255, 1,
                    tooltip="控制交替模式中第二组的值。用于奇偶分组交替输出"),
                ParamSpec("duration", "时长 (秒)", "float", 4.0, 0.1, 60.0, 0.1,
                    tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ],
        ), _fader_linear)

        # 顺序推杆（按设备顺序依次渐入）
        def _fader_sequential(ef, t, sorted_fx, fdefs, params, fps):
            delay_per = float(params.get("delay_per", 0.2))
            fade_time = float(params.get("fade_time", 0.5))
            val_a = int(params.get("val_a", 0))
            val_b = int(params.get("val_b", 255))
            ch = params.get("ch_type", "dimmer")
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                start = seq_i * delay_per
                progress = max(0.0, min(1.0, (t - start) / max(0.001, fade_time)))
                val = int(val_a + (val_b - val_a) * progress)
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="fader_sequential",
            name="顺序渐入",
            category="Fader",
            description="按设备顺序依次渐入，形成瀑布效果",
            target_channels=CH_TYPES,
            icon="▲▲▲",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("val_a", "起始值", "int", 0, 0, 255, 1,
                    tooltip="控制交替模式中第一组的值。用于奇偶分组交替输出"),
                ParamSpec("val_b", "目标值", "int", 255, 0, 255, 1,
                    tooltip="控制交替模式中第二组的值。用于奇偶分组交替输出"),
                ParamSpec("delay_per", "每设备延迟 (秒)", "float", 0.2, 0.01, 2.0, 0.01,
                    tooltip="控制每设备延迟 (秒)的值。范围 0.01~2.0，值越大越强"),
                ParamSpec("fade_time", "渐变时长 (秒)", "float", 0.5, 0.05, 5.0, 0.05,
                    tooltip="控制渐变时长 (秒)的值。范围 0.05~5.0，值越大越强"),
            ],
        ), _fader_sequential)

        # 呼吸灯
        def _fader_breath(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            lo = int(params.get("min", 10))
            hi = int(params.get("max", 255))
            ch = params.get("ch_type", "dimmer")
            v = (math.sin(t * speed * 2 * math.pi) + 1) / 2
            val = int(lo + v * (hi - lo))
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="fader_breath",
            name="呼吸灯",
            category="Fader",
            description="所有设备同步缓慢呼吸",
            target_channels=CH_TYPES,
            icon="♡",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "呼吸速度 (Hz)", "float", 0.5, 0.05, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("min", "最小值", "int", 10, 0, 255, 1,
                    tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                    tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _fader_breath)

        # 函数曲线推杆（支持多种曲线形状）
        def _fader_curve(ef, t, sorted_fx, fdefs, params, fps):
            duration = float(params.get("duration", 4.0))
            val_a = int(params.get("val_a", 0))
            val_b = int(params.get("val_b", 255))
            ch = params.get("ch_type", "dimmer")
            curve = params.get("curve", "EaseInOut")
            progress = min(1.0, t / max(0.001, duration))
            # 曲线映射
            if curve == "EaseIn":
                p = progress ** 2
            elif curve == "EaseOut":
                p = 1 - (1 - progress) ** 2
            elif curve == "EaseInOut":
                p = progress ** 2 * (3 - 2 * progress)
            elif curve == "Bounce":
                p = abs(math.sin(progress * math.pi * 3)) * (1 - progress)
            elif curve == "Elastic":
                p = math.sin(progress * math.pi * 5) * (1 - progress) + progress
            else:
                p = progress
            val = int(val_a + (val_b - val_a) * p)
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="fader_curve",
            name="曲线推杆",
            category="Fader",
            description="带缓动曲线的推杆效果",
            target_channels=CH_TYPES,
            icon="⌒",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("val_a", "起始值", "int", 0, 0, 255, 1,
                    tooltip="控制交替模式中第一组的值。用于奇偶分组交替输出"),
                ParamSpec("val_b", "目标值", "int", 255, 0, 255, 1,
                    tooltip="控制交替模式中第二组的值。用于奇偶分组交替输出"),
                ParamSpec("duration", "时长 (秒)", "float", 4.0, 0.1, 60.0, 0.1,
                    tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
                ParamSpec("curve", "曲线类型", "choice", "EaseInOut",
                          choices=["Linear", "EaseIn", "EaseOut", "EaseInOut",
                                   "Bounce", "Elastic"],
                    tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ],
        ), _fader_curve)

    # ─────────────────────────────────────────────────────────────────────────
    #  摇杆（Joystick）预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_joystick_presets(self):

        # 圆形路径（所有设备同步走圆）
        def _joystick_circle(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            radius_x = float(params.get("radius_x", 0.5))
            radius_y = float(params.get("radius_y", 0.5))
            cx = float(params.get("center_x", 0.5))
            cy = float(params.get("center_y", 0.5))
            x_ch = params.get("x_ch", "motor_x")
            y_ch = params.get("y_ch", "motor_y")
            phase_step = float(params.get("phase_step", 0.0))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = seq_i * phase_step
                angle = t * speed * 2 * math.pi + phase
                x = cx + radius_x * math.cos(angle)
                y = cy + radius_y * math.sin(angle)
                ef.values[orig_i][x_ch] = max(0, min(255, int(x * 255)))
                ef.values[orig_i][y_ch] = max(0, min(255, int(y * 255)))

        self._reg(EffectPreset(
            id="joystick_circle",
            name="圆形路径",
            category="Joystick",
            description="电机沿圆形路径运动，支持相位偏移",
            target_channels=["motor_x", "motor_y"],
            icon="○",
            params=[
                ParamSpec("x_ch", "X轴通道", "choice", "motor_x",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制X轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("y_ch", "Y轴通道", "choice", "motor_y",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制Y轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("speed", "速度 (圈/秒)", "float", 0.5, 0.01, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("radius_x", "X半径 (0-1)", "float", 0.4, 0.0, 0.5, 0.01,
                    tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
                ParamSpec("radius_y", "Y半径 (0-1)", "float", 0.4, 0.0, 0.5, 0.01,
                    tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
                ParamSpec("center_x", "中心X (0-1)", "float", 0.5, 0.0, 1.0, 0.01,
                    tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
                ParamSpec("center_y", "中心Y (0-1)", "float", 0.5, 0.0, 1.0, 0.01,
                    tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
                ParamSpec("phase_step", "相位步长", "float", 0.0, 0.0, 1.0, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ],
        ), _joystick_circle)

        # 8字形路径
        def _joystick_figure8(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            scale = float(params.get("scale", 0.4))
            cx = float(params.get("center_x", 0.5))
            cy = float(params.get("center_y", 0.5))
            x_ch = params.get("x_ch", "motor_x")
            y_ch = params.get("y_ch", "motor_y")
            phase_step = float(params.get("phase_step", 0.0))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = seq_i * phase_step
                angle = t * speed * 2 * math.pi + phase
                x = cx + scale * math.sin(angle)
                y = cy + scale * math.sin(2 * angle) / 2
                ef.values[orig_i][x_ch] = max(0, min(255, int(x * 255)))
                ef.values[orig_i][y_ch] = max(0, min(255, int(y * 255)))

        self._reg(EffectPreset(
            id="joystick_figure8",
            name="8字形路径",
            category="Joystick",
            description="电机沿∞形路径运动",
            target_channels=["motor_x", "motor_y"],
            icon="∞",
            params=[
                ParamSpec("x_ch", "X轴通道", "choice", "motor_x",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制X轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("y_ch", "Y轴通道", "choice", "motor_y",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制Y轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("speed", "速度", "float", 0.5, 0.01, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("scale", "幅度 (0-0.5)", "float", 0.4, 0.05, 0.5, 0.01,
                    tooltip="控制缩放比例。值越大视觉元素放大越多"),
                ParamSpec("center_x", "中心X", "float", 0.5, 0.0, 1.0, 0.01,
                    tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
                ParamSpec("center_y", "中心Y", "float", 0.5, 0.0, 1.0, 0.01,
                    tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
                ParamSpec("phase_step", "相位步长", "float", 0.0, 0.0, 1.0, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ],
        ), _joystick_figure8)

        # 扫描路径（左右扫描）
        def _joystick_scan(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            lo = float(params.get("lo", 0.1))
            hi = float(params.get("hi", 0.9))
            y_fixed = float(params.get("y_fixed", 0.5))
            x_ch = params.get("x_ch", "motor_x")
            y_ch = params.get("y_ch", "motor_y")
            phase_step = float(params.get("phase_step", 0.0))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = seq_i * phase_step
                x = lo + (hi - lo) * (math.sin(t * speed * 2 * math.pi + phase) + 1) / 2
                ef.values[orig_i][x_ch] = max(0, min(255, int(x * 255)))
                ef.values[orig_i][y_ch] = max(0, min(255, int(y_fixed * 255)))

        self._reg(EffectPreset(
            id="joystick_scan",
            name="左右扫描",
            category="Joystick",
            description="电机在水平方向来回扫描",
            target_channels=["motor_x", "motor_y"],
            icon="↔",
            params=[
                ParamSpec("x_ch", "X轴通道", "choice", "motor_x",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制X轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("y_ch", "Y轴通道", "choice", "motor_y",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制Y轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("speed", "速度", "float", 0.5, 0.01, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("lo", "左边界 (0-1)", "float", 0.1, 0.0, 0.5, 0.01,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "右边界 (0-1)", "float", 0.9, 0.5, 1.0, 0.01,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
                ParamSpec("y_fixed", "Y固定值 (0-1)", "float", 0.5, 0.0, 1.0, 0.01,
                    tooltip="控制Y固定值 (0-1)的比例。0 为最小，1.0 为最大"),
                ParamSpec("phase_step", "相位步长", "float", 0.0, 0.0, 1.0, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ],
        ), _joystick_scan)

        # 波浪扫描（设备依次相位偏移的扫描）
        def _joystick_wave_scan(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 0.5))
            phase_step = float(params.get("phase_step", 0.15))
            lo_x = float(params.get("lo_x", 0.1))
            hi_x = float(params.get("hi_x", 0.9))
            lo_y = float(params.get("lo_y", 0.1))
            hi_y = float(params.get("hi_y", 0.9))
            x_ch = params.get("x_ch", "motor_x")
            y_ch = params.get("y_ch", "motor_y")
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = seq_i * phase_step
                x = lo_x + (hi_x - lo_x) * (math.sin(t * speed * 2 * math.pi + phase) + 1) / 2
                y = lo_y + (hi_y - lo_y) * (math.cos(t * speed * 2 * math.pi + phase) + 1) / 2
                ef.values[orig_i][x_ch] = max(0, min(255, int(x * 255)))
                ef.values[orig_i][y_ch] = max(0, min(255, int(y * 255)))

        self._reg(EffectPreset(
            id="joystick_wave_scan",
            name="波浪扫描",
            category="Joystick",
            description="设备依次相位偏移形成波浪扫描效果",
            target_channels=["motor_x", "motor_y"],
            icon="〜↔",
            params=[
                ParamSpec("x_ch", "X轴通道", "choice", "motor_x",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制X轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("y_ch", "Y轴通道", "choice", "motor_y",
                          choices=["motor_x", "motor_y", "motor_z"],
                    tooltip="控制Y轴通道的具体数值。值越大效果越强，值越小效果越弱"),
                ParamSpec("speed", "速度", "float", 0.5, 0.01, 5.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("phase_step", "相位步长", "float", 0.15, 0.0, 1.0, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                ParamSpec("lo_x", "X最小", "float", 0.1, 0.0, 0.5, 0.01,
                    tooltip="控制X最小的比例。0 为最小，1.0 为最大"),
                ParamSpec("hi_x", "X最大", "float", 0.9, 0.5, 1.0, 0.01,
                    tooltip="控制X最大的比例。0 为最小，1.0 为最大"),
                ParamSpec("lo_y", "Y最小", "float", 0.1, 0.0, 0.5, 0.01,
                    tooltip="控制Y最小的比例。0 为最小，1.0 为最大"),
                ParamSpec("hi_y", "Y最大", "float", 0.9, 0.5, 1.0, 0.01,
                    tooltip="控制Y最大的比例。0 为最小，1.0 为最大"),
            ],
        ), _joystick_wave_scan)

    # ─────────────────────────────────────────────────────────────────────────
    #  音频驱动预设
    # ─────────────────────────────────────────────────────────────────────────

    def _register_audio_presets(self):
        """
        音频驱动预设：这些预设在没有实时音频数据时使用模拟数据，
        在有音频分析结果时使用真实的 BPM/能量/频谱数据。
        音频数据通过 params["_audio_data"] 传入（可选）。
        """

        # BPM 同步闪烁
        def _audio_bpm_flash(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            beat_period = 60.0 / bpm
            phase = (t % beat_period) / beat_period
            val = hi if phase < 0.1 else lo
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = val

        self._reg(EffectPreset(
            id="audio_bpm_flash",
            name="BPM 同步闪烁",
            category="Audio",
            description="按 BPM 节拍同步闪烁，可手动输入或从音频分析获取",
            target_channels=CH_TYPES,
            icon="♩",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _audio_bpm_flash)

        # BPM 追逐
        def _audio_bpm_chase(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("off_val", 0))
            hi = int(params.get("on_val", 255))
            n = len(sorted_fx)
            if n == 0:
                return
            beat_period = 60.0 / bpm
            beat_idx = int(t / beat_period)
            cur = beat_idx % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = hi if seq_i == cur else lo

        self._reg(EffectPreset(
            id="audio_bpm_chase",
            name="BPM 节拍追逐",
            category="Audio",
            description="每个节拍跳到下一个设备",
            target_channels=CH_TYPES,
            icon="♩●",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
                ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1,
                    tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
                ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1,
                    tooltip="控制关闭/熄灭时的输出值。效果结束后设备回到此值"),
            ],
        ), _audio_bpm_chase)

        # BPM 波浪（节拍触发波浪）
        def _audio_bpm_wave(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("min", 0))
            hi = int(params.get("max", 255))
            phase_step = float(params.get("phase_step", 0.1))
            beat_period = 60.0 / bpm
            beat_phase = (t % beat_period) / beat_period
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = seq_i * phase_step
                v = math.exp(-((beat_phase - phase) % 1.0) * 5)
                val = int(lo + v * (hi - lo))
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="audio_bpm_wave",
            name="BPM 节拍波浪",
            category="Audio",
            description="每个节拍触发一次波浪，从头部向尾部传播",
            target_channels=CH_TYPES,
            icon="♩〜",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
                ParamSpec("phase_step", "相位步长", "float", 0.1, 0.0, 0.5, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                ParamSpec("min", "最小值", "int", 0, 0, 255, 1,
                    tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                    tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _audio_bpm_wave)

        # 能量响应（模拟音频能量驱动亮度）
        def _audio_energy(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("min", 0))
            hi = int(params.get("max", 255))
            # 模拟能量包络（节拍触发衰减）
            beat_period = 60.0 / bpm
            beat_phase = (t % beat_period) / beat_period
            energy = math.exp(-beat_phase * 4)
            val = int(lo + energy * (hi - lo))
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = max(0, min(255, val))

        self._reg(EffectPreset(
            id="audio_energy",
            name="能量响应",
            category="Audio",
            description="亮度跟随节拍能量包络变化（节拍后快速衰减）",
            target_channels=CH_TYPES,
            icon="♩↓",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
                ParamSpec("min", "最小值", "int", 0, 0, 255, 1,
                    tooltip="控制最小值的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("max", "最大值", "int", 255, 0, 255, 1,
                    tooltip="控制最大值的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _audio_energy)

        # 节拍颜色变换
        def _audio_color_beat(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            beat_period = 60.0 / bpm
            beat_idx = int(t / beat_period)
            colors = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (0, 255, 255), (255, 0, 255),
                (255, 128, 0), (128, 0, 255),
            ]
            r, g, b = colors[beat_idx % len(colors)]
            beat_phase = (t % beat_period) / beat_period
            fade = 1.0 - beat_phase * 0.5
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i]["R"] = int(r * fade)
                ef.values[orig_i]["G"] = int(g * fade)
                ef.values[orig_i]["B"] = int(b * fade)

        self._reg(EffectPreset(
            id="audio_color_beat",
            name="节拍颜色变换",
            category="Audio",
            description="每个节拍切换一种颜色，节拍内渐暗",
            target_channels=["R", "G", "B"],
            icon="♩🎨",
            params=[
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ],
        ), _audio_color_beat)

        # 段落响应（副歌/主歌切换）
        def _audio_section(ef, t, sorted_fx, fdefs, params, fps):
            bpm = float(params.get("bpm", 120.0))
            bars_per_section = int(params.get("bars_per_section", 8))
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("verse_val", 100))
            hi = int(params.get("chorus_val", 255))
            beat_period = 60.0 / bpm
            bar_period = beat_period * 4
            section_period = bar_period * bars_per_section
            section_idx = int(t / section_period)
            is_chorus = (section_idx % 2) == 1
            val = hi if is_chorus else lo
            for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
                ef.values[orig_i][ch] = val

        self._reg(EffectPreset(
            id="audio_section",
            name="段落响应",
            category="Audio",
            description="主歌/副歌交替时切换亮度，模拟音乐段落结构",
            target_channels=CH_TYPES,
            icon="♩♫",
            params=[
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                    tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
                ParamSpec("bars_per_section", "每段小节数", "int", 8, 2, 32, 1,
                    tooltip="控制每段小节数的值。范围 2~32，值越大越明显"),
                ParamSpec("verse_val", "主歌亮度", "int", 100, 0, 255, 1,
                    tooltip="控制主歌亮度的输出强度。0 为最小值，255 为最大值"),
                ParamSpec("chorus_val", "副歌亮度", "int", 255, 0, 255, 1,
                    tooltip="控制副歌亮度的输出强度。0 为最小值，255 为最大值"),
            ],
        ), _audio_section)


# ─────────────────────────────────────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """HSV (0-1) → RGB (0-255)

    R183 统一：薄封装 src/utils/color_math.py:hsv_to_rgb_u8（标准库 colorsys
    内核，与 CE C 侧 `_ce_hsv_to_rgb` 同一逐位口径）。原式与统一实现 256×N
    采样 + 20 万随机位级对拍 0 差异（tests/test_r183_perf.py）；实测不劣化。
    导入按可行性收在定义体内（顶层 import 受本批行约束禁用）。
    """
    try:
        return _hsv_to_rgb._impl(h, s, v)
    except AttributeError:
        # 多前缀兜底：app/src 两种导入根（顶层 ui 时相对 `..` 越顶）
        try:
            from ..utils.color_math import hsv_to_rgb_u8 as _impl
        except ImportError:
            try:
                from utils.color_math import hsv_to_rgb_u8 as _impl
            except ImportError:
                from src.utils.color_math import hsv_to_rgb_u8 as _impl
        _hsv_to_rgb._impl = _impl
        return _impl(h, s, v)


def _spiral_indices(rows: int, cols: int) -> List[int]:
    """生成矩阵螺旋顺序的线性索引列表"""
    result = []
    top, bottom, left, right = 0, rows - 1, 0, cols - 1
    while top <= bottom and left <= right:
        for c in range(left, right + 1):
            result.append(top * cols + c)
        top += 1
        for r in range(top, bottom + 1):
            result.append(r * cols + right)
        right -= 1
        if top <= bottom:
            for c in range(right, left - 1, -1):
                result.append(bottom * cols + c)
            bottom -= 1
        if left <= right:
            for r in range(bottom, top - 1, -1):
                result.append(r * cols + left)
            left += 1
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: Optional[EffectEngine] = None

def get_engine() -> EffectEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = EffectEngine()
    return _engine_instance


# ─────────────────────────────────────────────────────────────────────────────
#  v21 扩展：针对不同轨道类型的专属预设（在 EffectEngine 初始化后注册）
# ─────────────────────────────────────────────────────────────────────────────

def _register_v21_presets(engine: "EffectEngine"):
    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════════
    #  Dimmer 专属预设
    # ═══════════════════════════════════════════════════════════════════════

    def _dimmer_pulse(ef, t, sorted_fx, fdefs, params, fps):
        rate  = float(params.get("rate",  2.0))
        width = float(params.get("width", 0.1))
        peak  = int(params.get("peak",  255))
        base  = int(params.get("base",  0))
        phase = (t * rate) % 1.0
        v = int(peak * (1.0 - phase / width)) if phase < width else base
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i]["dimmer"] = max(0, min(255, v))

    reg(EffectPreset(
        id="dimmer_pulse", name="脉冲闪光", category="Fader",
        description="快速脉冲闪光，可调频率和脉宽",
        target_channels=["dimmer"], icon="⚡",
        params=[
            ParamSpec("rate",  "频率 (Hz)",  "float", 2.0,  0.1, 20.0, 0.1,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("width", "脉宽 (0-1)", "float", 0.1,  0.01, 0.9, 0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("peak",  "峰值",       "int",   255,  0, 255, 1,
                tooltip="控制输出范围的上限值。效果输出的最高峰值"),
            ParamSpec("base",  "基础值",     "int",   0,    0, 255, 1,
                tooltip="控制效果的基准值。所有变化以此值为中心上下波动"),
        ],
    ), _dimmer_pulse)

    def _dimmer_random_jump(ef, t, sorted_fx, fdefs, params, fps):
        rate = float(params.get("rate", 3.0))
        lo   = int(params.get("lo", 0))
        hi   = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = int(t * rate + seq_i * 1000)
            ef.values[orig_i]["dimmer"] = random.Random(seed).randint(lo, hi)

    reg(EffectPreset(
        id="dimmer_random_jump", name="随机跳变", category="Fader",
        description="每台设备独立随机跳变亮度",
        target_channels=["dimmer"], icon="🎲",
        params=[
            ParamSpec("rate", "跳变频率 (Hz)", "float", 3.0, 0.5, 30.0, 0.5,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("lo",   "最小值",        "int",   0,   0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi",   "最大值",        "int",   255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _dimmer_random_jump)

    def _dimmer_keyframe(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 8.0))
        v0, v1, v2, v3 = (int(params.get(k, d)) for k, d in
                          [("v0",0),("v1",255),("v2",128),("v3",0)])
        loop = bool(params.get("loop", True))
        tt = (t % duration) if loop else min(t, duration)
        p = tt / duration
        if p < 0.33:
            val = int(v0 + (v1 - v0) * (p / 0.33))
        elif p < 0.66:
            val = int(v1 + (v2 - v1) * ((p - 0.33) / 0.33))
        else:
            val = int(v2 + (v3 - v2) * ((p - 0.66) / 0.34))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i]["dimmer"] = max(0, min(255, val))

    reg(EffectPreset(
        id="dimmer_keyframe", name="关键帧序列", category="Fader",
        description="4个关键帧的亮度序列，可循环",
        target_channels=["dimmer"], icon="⬛⬜⬛",
        params=[
            ParamSpec("v0",       "关键帧0",     "int",   0,   0, 255, 1,
                tooltip="控制关键帧0的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("v1",       "关键帧1",     "int",   255, 0, 255, 1,
                tooltip="控制关键帧1的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("v2",       "关键帧2",     "int",   128, 0, 255, 1,
                tooltip="控制关键帧2的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("v3",       "关键帧3",     "int",   0,   0, 255, 1,
                tooltip="控制关键帧3的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("duration", "总时长 (秒)", "float", 8.0, 0.5, 60.0, 0.5,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("loop",     "循环",        "bool",  True,
                tooltip="控制循环的开关状态。开启时启用该功能，关闭时禁用"),
        ],
    ), _dimmer_keyframe)

    def _dimmer_group_alt(ef, t, sorted_fx, fdefs, params, fps):
        rate       = float(params.get("rate", 1.0))
        group_size = int(params.get("group_size", 2))
        lo         = int(params.get("lo", 0))
        hi         = int(params.get("hi", 255))
        fade       = float(params.get("fade", 0.3))
        phase = (t * rate) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = ((seq_i // group_size) * 0.5) % 1.0
            p = (phase + offset) % 1.0
            if p < 0.5:
                v = hi if fade == 0 else int(hi * min(1.0, p / max(1e-9, fade)))
            else:
                v = lo if fade == 0 else int(hi * max(0.0, 1.0 - (p - 0.5) / max(1e-9, fade)))
            ef.values[orig_i]["dimmer"] = max(0, min(255, v))

    reg(EffectPreset(
        id="dimmer_group_alt", name="分组交替", category="Fader",
        description="按组交替亮暗，可调组大小和渐变",
        target_channels=["dimmer"], icon="⬛⬜⬛⬜",
        params=[
            ParamSpec("rate",       "速度 (Hz)", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("group_size", "组大小",    "int",   2,   1, 16, 1,
                tooltip="控制每组包含的设备数量。值越大同组运行的设备越多"),
            ParamSpec("lo",         "暗值",      "int",   0,   0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi",         "亮值",      "int",   255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("fade",       "渐变比例",  "float", 0.3, 0.0, 0.5, 0.05,
                tooltip="控制效果渐弱/消退时长。值越大消退越平缓自然"),
        ],
    ), _dimmer_group_alt)

    # ═══════════════════════════════════════════════════════════════════════
    #  Color 专属预设
    # ═══════════════════════════════════════════════════════════════════════

    def _color_rainbow_v21(ef, t, sorted_fx, fdefs, params, fps):
        speed      = float(params.get("speed", 0.2))
        phase_step = float(params.get("phase_step", 0.05))
        sat        = float(params.get("saturation", 1.0))
        bri        = float(params.get("brightness", 1.0))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = (t * speed + seq_i * phase_step) % 1.0
            r, g, b = _hsv_to_rgb(hue, sat, bri)
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="color_rainbow_v21", name="彩虹循环", category="Wave",
        description="所有设备彩虹色循环，相邻设备相位偏移",
        target_channels=["R","G","B"], icon="🌈",
        params=[
            ParamSpec("speed",      "速度",     "float", 0.2,  0.01, 2.0,  0.01,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("phase_step", "相位步长", "float", 0.05, 0.0,  0.5,  0.01,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ParamSpec("saturation", "饱和度",   "float", 1.0,  0.0,  1.0,  0.05,
                tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
            ParamSpec("brightness", "亮度",     "float", 1.0,  0.0,  1.0,  0.05,
                tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
        ],
    ), _color_rainbow_v21)

    def _color_temperature(ef, t, sorted_fx, fdefs, params, fps):
        speed  = float(params.get("speed", 0.1))
        warm   = [int(params.get(k, d)) for k, d in
                  [("warm_r",255),("warm_g",180),("warm_b",80),("warm_w",0)]]
        cool   = [int(params.get(k, d)) for k, d in
                  [("cool_r",180),("cool_g",210),("cool_b",255),("cool_w",255)]]
        p = (math.sin(t * speed * 2 * math.pi) + 1) / 2
        vals = [int(w + (c - w) * p) for w, c in zip(warm, cool)]
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            for ch, v in zip(["R","G","B","W"], vals):
                ef.values[orig_i][ch] = v

    reg(EffectPreset(
        id="color_temperature", name="色温渐变", category="Wave",
        description="暖白到冷白之间缓慢渐变，适合 RGBW 设备",
        target_channels=["R","G","B","W"], icon="🌡",
        params=[
            ParamSpec("speed",  "速度",   "float", 0.1, 0.01, 1.0, 0.01,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("warm_r", "暖色 R", "int",   255, 0, 255, 1,
                tooltip="控制暖色 R的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("warm_g", "暖色 G", "int",   180, 0, 255, 1,
                tooltip="控制暖色 G的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("warm_b", "暖色 B", "int",   80,  0, 255, 1,
                tooltip="控制暖色 B的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("warm_w", "暖色 W", "int",   0,   0, 255, 1,
                tooltip="控制暖色 W的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("cool_r", "冷色 R", "int",   180, 0, 255, 1,
                tooltip="控制冷色 R的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("cool_g", "冷色 G", "int",   210, 0, 255, 1,
                tooltip="控制冷色 G的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("cool_b", "冷色 B", "int",   255, 0, 255, 1,
                tooltip="控制冷色 B的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("cool_w", "冷色 W", "int",   255, 0, 255, 1,
                tooltip="控制冷色 W的输出强度。0 为最小值，255 为最大值"),
        ],
    ), _color_temperature)

    def _color_complementary(ef, t, sorted_fx, fdefs, params, fps):
        bpm  = float(params.get("bpm", 120.0))
        hue_a = float(params.get("hue_a", 0.0))
        sat  = float(params.get("saturation", 1.0))
        bri  = float(params.get("brightness", 1.0))
        beat_period = 60.0 / bpm
        beat_idx    = int(t / beat_period)
        beat_phase  = (t % beat_period) / beat_period
        hue = hue_a if beat_idx % 2 == 0 else (hue_a + 0.5) % 1.0
        r, g, b = _hsv_to_rgb(hue, sat, bri * (1.0 - beat_phase * 0.3))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="color_complementary", name="互补色切换", category="Chase",
        description="每节拍切换互补色对（如红↔青），节拍内渐暗",
        target_channels=["R","G","B"], icon="⬤⬤",
        params=[
            ParamSpec("bpm",        "BPM",    "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("hue_a",      "基础色相", "float", 0.0,  0.0,  1.0,  0.01,
                tooltip="控制第一种颜色的色相。0~360 度覆盖完整色环"),
            ParamSpec("saturation", "饱和度",  "float", 1.0,  0.0,  1.0,  0.05,
                tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
            ParamSpec("brightness", "亮度",    "float", 1.0,  0.0,  1.0,  0.05,
                tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
        ],
    ), _color_complementary)

    def _color_hsv_scan(ef, t, sorted_fx, fdefs, params, fps):
        h_speed    = float(params.get("h_speed", 0.1))
        s_speed    = float(params.get("s_speed", 0.0))
        v_speed    = float(params.get("v_speed", 0.0))
        h_base     = float(params.get("h_base",  0.0))
        s_base     = float(params.get("s_base",  1.0))
        v_base     = float(params.get("v_base",  1.0))
        phase_step = float(params.get("phase_step", 0.03))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            off = seq_i * phase_step
            h = (h_base + t * h_speed + off) % 1.0
            s = max(0.0, min(1.0, s_base + math.sin(t * s_speed * 2*math.pi + off) * 0.3))
            v = max(0.0, min(1.0, v_base + math.sin(t * v_speed * 2*math.pi + off) * 0.3))
            r, g, b = _hsv_to_rgb(h, s, v)
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="color_hsv_scan", name="HSV 扫描", category="Wave",
        description="色相/饱和度/亮度独立扫描，高度可定制",
        target_channels=["R","G","B"], icon="HSV",
        params=[
            ParamSpec("h_speed",    "色相速度",   "float", 0.1,  0.0, 2.0, 0.01,
                tooltip="控制色相速度的值。范围 0.0~2.0，值越大越强"),
            ParamSpec("s_speed",    "饱和度速度", "float", 0.0,  0.0, 2.0, 0.01,
                tooltip="控制饱和度速度的值。范围 0.0~2.0，值越大越强"),
            ParamSpec("v_speed",    "亮度速度",   "float", 0.0,  0.0, 2.0, 0.01,
                tooltip="控制亮度速度的值。范围 0.0~2.0，值越大越强"),
            ParamSpec("h_base",     "基础色相",   "float", 0.0,  0.0, 1.0, 0.01,
                tooltip="控制基础色相的比例。0 为最小，1.0 为最大"),
            ParamSpec("s_base",     "基础饱和度", "float", 1.0,  0.0, 1.0, 0.05,
                tooltip="控制基础饱和度的比例。0 为最小，1.0 为最大"),
            ParamSpec("v_base",     "基础亮度",   "float", 1.0,  0.0, 1.0, 0.05,
                tooltip="控制基础亮度的比例。0 为最小，1.0 为最大"),
            ParamSpec("phase_step", "相位步长",   "float", 0.03, 0.0, 0.3, 0.01,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _color_hsv_scan)

    def _color_explosion(ef, t, sorted_fx, fdefs, params, fps):
        bpm        = float(params.get("bpm", 120.0))
        sat        = float(params.get("saturation", 1.0))
        bri        = float(params.get("brightness", 1.0))
        per_fix    = bool(params.get("per_fixture", False))
        decay      = float(params.get("decay", 0.8))
        beat_period = 60.0 / bpm
        beat_idx    = int(t / beat_period)
        beat_phase  = (t % beat_period) / beat_period
        fade = math.exp(-beat_phase * decay * 5)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = beat_idx * 1000 + (seq_i if per_fix else 0)
            hue = random.Random(seed).random()
            r, g, b = _hsv_to_rgb(hue, sat, bri * fade)
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="color_explosion", name="颜色爆炸", category="Audio",
        description="每节拍随机切换颜色，节拍后快速衰减",
        target_channels=["R","G","B"], icon="💥",
        params=[
            ParamSpec("bpm",         "BPM",        "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("saturation",  "饱和度",     "float", 1.0,   0.0,  1.0,  0.05,
                tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
            ParamSpec("brightness",  "亮度",       "float", 1.0,   0.0,  1.0,  0.05,
                tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
            ParamSpec("decay",       "衰减速度",   "float", 0.8,   0.1,  3.0,  0.1,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("per_fixture", "每设备独立", "bool",  False,
                tooltip="控制每设备独立的开关状态。开启时启用该功能，关闭时禁用"),
        ],
    ), _color_explosion)

    # ═══════════════════════════════════════════════════════════════════════
    #  Motor 专属预设（摇杆增强）
    # ═══════════════════════════════════════════════════════════════════════

    def _motor_spiral(ef, t, sorted_fx, fdefs, params, fps):
        speed        = float(params.get("speed", 0.5))
        radius_speed = float(params.get("radius_speed", 0.3))
        cx           = float(params.get("center_x", 0.5))
        cy           = float(params.get("center_y", 0.5))
        max_r        = float(params.get("max_radius", 0.4))
        n = len(sorted_fx)
        angle  = t * speed * 2 * math.pi
        radius = (t * radius_speed) % max_r
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = seq_i / max(1, n) * 2 * math.pi
            a = angle + phase
            x = max(0, min(255, int((cx + radius * math.cos(a)) * 255)))
            y = max(0, min(255, int((cy + radius * math.sin(a)) * 255)))
            ef.values[orig_i]["motor_x"] = x
            ef.values[orig_i]["motor_y"] = y

    reg(EffectPreset(
        id="motor_spiral", name="螺旋扫描", category="Joystick",
        description="电机从中心向外螺旋扫描",
        target_channels=["motor_x","motor_y"], icon="🌀",
        params=[
            ParamSpec("speed",        "旋转速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius_speed", "半径速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制半径速度的值。范围 0.05~2.0，值越大越强"),
            ParamSpec("center_x",     "中心 X",   "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y",     "中心 Y",   "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("max_radius",   "最大半径", "float", 0.4, 0.1, 0.5, 0.05,
                tooltip="控制运动的最大半径限制。限制运动范围的外边界"),
        ],
    ), _motor_spiral)

    def _motor_random_jump(ef, t, sorted_fx, fdefs, params, fps):
        rate   = float(params.get("rate", 1.0))
        smooth = float(params.get("smooth", 0.0))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = int(t * rate + seq_i * 777)
            rng  = random.Random(seed)
            tx, ty = rng.randint(0, 255), rng.randint(0, 255)
            if smooth > 0:
                px = ef.values[orig_i].get("motor_x", 128)
                py = ef.values[orig_i].get("motor_y", 128)
                tx = int(px + (tx - px) * smooth)
                ty = int(py + (ty - py) * smooth)
            ef.values[orig_i]["motor_x"] = max(0, min(255, tx))
            ef.values[orig_i]["motor_y"] = max(0, min(255, ty))

    reg(EffectPreset(
        id="motor_random_jump", name="随机跳位", category="Joystick",
        description="每台电机独立随机跳到新位置",
        target_channels=["motor_x","motor_y"], icon="🎯",
        params=[
            ParamSpec("rate",   "跳变频率 (Hz)", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("smooth", "平滑度 (0=无)", "float", 0.0, 0.0, 1.0,  0.05,
                tooltip="控制平滑滤波的程度。值越大输出变化越平滑但响应越迟钝"),
        ],
    ), _motor_random_jump)

    def _motor_scan_wall(ef, t, sorted_fx, fdefs, params, fps):
        speed      = float(params.get("speed", 0.3))
        rows       = int(params.get("rows", 3))
        row_phase  = float(params.get("row_phase", 0.2))
        y_spread   = float(params.get("y_spread", 0.8))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i % rows
            phase_off = row * row_phase
            x = (math.sin((t * speed + phase_off) * 2 * math.pi) + 1) / 2
            y = 0.1 + (row / max(1, rows - 1)) * y_spread
            ef.values[orig_i]["motor_x"] = int(x * 255)
            ef.values[orig_i]["motor_y"] = int(y * 255)

    reg(EffectPreset(
        id="motor_scan_wall", name="扫描墙", category="Joystick",
        description="多行设备同步水平扫描，行间有相位差",
        target_channels=["motor_x","motor_y"], icon="⬅➡",
        params=[
            ParamSpec("speed",     "扫描速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("rows",      "行数",     "int",   3,   1, 16, 1,
                tooltip="控制矩阵的行数。值越大每列设备越多"),
            ParamSpec("row_phase", "行相位差", "float", 0.2, 0.0, 1.0, 0.05,
                tooltip="控制行相位差的比例。0 为最小，1.0 为最大"),
            ParamSpec("y_spread",  "Y轴范围",  "float", 0.8, 0.1, 1.0, 0.05,
                tooltip="控制Y轴范围的比例。0 为最小，1.0 为最大"),
        ],
    ), _motor_scan_wall)

    def _motor_chase_target(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        delay = float(params.get("delay", 0.1))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_d = max(0.0, t - seq_i * delay)
            x = 0.5 + 0.4 * math.cos(t_d * speed * 2 * math.pi)
            y = 0.5 + 0.4 * math.sin(t_d * speed * 2 * math.pi)
            ef.values[orig_i]["motor_x"] = int(x * 255)
            ef.values[orig_i]["motor_y"] = int(y * 255)

    reg(EffectPreset(
        id="motor_chase_target", name="追逐指向", category="Joystick",
        description="所有电机依次追逐同一个运动目标点",
        target_channels=["motor_x","motor_y"], icon="🎯→",
        params=[
            ParamSpec("speed", "目标速度", "float", 0.5, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("delay", "追逐延迟", "float", 0.1, 0.0,  1.0, 0.01,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
        ],
    ), _motor_chase_target)

    def _motor_figure8(ef, t, sorted_fx, fdefs, params, fps):
        import math as _math
        speed  = float(params.get("speed", 1.0))
        radius = float(params.get("radius", 0.4))
        phase  = float(params.get("phase", 0.0))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_off = t * speed * 2 * _math.pi + seq_i * phase
            x = 0.5 + radius * _math.sin(t_off)
            y = 0.5 + radius * _math.sin(t_off * 2) / 2
            ef.values[orig_i]["motor_x"] = int(max(0, min(255, x * 255)))
            ef.values[orig_i]["motor_y"] = int(max(0, min(255, y * 255)))
    reg(EffectPreset(
        id="motor_figure8", name="8字形运动", category="Joystick",
        description="电机沿8字形轨迹运动，可调速度、半径和相位偏移",
        target_channels=["motor_x","motor_y"], icon="8",
        params=[
            ParamSpec("speed",  "速度",     "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius", "半径",     "float", 0.4, 0.1, 0.5, 0.05,
                tooltip="控制轨道或图案的半径。值越大运动轨迹的范围越广"),
            ParamSpec("phase",  "相位偏移", "float", 0.0, 0.0, 6.28, 0.1,
                tooltip="控制相位偏移的值。范围 0.0~6.28，值越大越强"),
        ],
    ), _motor_figure8)

    def _motor_circle(ef, t, sorted_fx, fdefs, params, fps):
        import math as _math
        speed  = float(params.get("speed", 1.0))
        radius = float(params.get("radius", 0.4))
        phase  = float(params.get("phase", 0.5))
        n      = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i * (2 * _math.pi / n) * phase
            angle  = t * speed * 2 * _math.pi + offset
            x = 0.5 + radius * _math.cos(angle)
            y = 0.5 + radius * _math.sin(angle)
            ef.values[orig_i]["motor_x"] = int(max(0, min(255, x * 255)))
            ef.values[orig_i]["motor_y"] = int(max(0, min(255, y * 255)))
    reg(EffectPreset(
        id="motor_circle", name="圆形扫描", category="Joystick",
        description="电机沿圆形轨迹扫描，多设备均匀分布在圆上",
        target_channels=["motor_x","motor_y"], icon="O",
        params=[
            ParamSpec("speed",  "速度",     "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius", "半径",     "float", 0.4, 0.1, 0.5, 0.05,
                tooltip="控制轨道或图案的半径。值越大运动轨迹的范围越广"),
            ParamSpec("phase",  "相位间隔", "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制相位间隔的比例。0 为最小，1.0 为最大"),
        ],
    ), _motor_circle)


    # ═══════════════════════════════════════════════════════════════════════
    #  Valve / Pump 专属预设
    # ═══════════════════════════════════════════════════════════════════════

    def _valve_pulse(ef, t, sorted_fx, fdefs, params, fps):
        rate      = float(params.get("rate", 2.0))
        open_val  = int(params.get("open_val", 255))
        close_val = int(params.get("close_val", 0))
        duty      = float(params.get("duty", 0.5))
        ch        = params.get("ch_type", "valve")
        val = open_val if (t * rate) % 1.0 < duty else close_val
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="valve_pulse", name="脉冲开合", category="Fader",
        description="阀门/泵按频率脉冲开合，可调占空比",
        target_channels=["valve","pump"], icon="⊙",
        params=[
            ParamSpec("ch_type",   "通道类别", "choice", "valve", choices=["valve","pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("rate",      "频率 (Hz)", "float", 2.0, 0.1, 20.0, 0.1,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("open_val",  "开启值",   "int",   255, 0, 255, 1,
                tooltip="控制设备开启/激活时的输出数值。表示效果作用时的强度"),
            ParamSpec("close_val", "关闭值",   "int",   0,   0, 255, 1,
                tooltip="控制设备关闭时的输出数值。表示效果未作用时的基准"),
            ParamSpec("duty",      "占空比",   "float", 0.5, 0.05, 0.95, 0.05,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
        ],
    ), _valve_pulse)

    def _valve_sequential(ef, t, sorted_fx, fdefs, params, fps):
        rate      = float(params.get("rate", 1.0))
        open_val  = int(params.get("open_val", 255))
        close_val = int(params.get("close_val", 0))
        overlap   = bool(params.get("overlap", False))
        ch        = params.get("ch_type", "valve")
        n = len(sorted_fx)
        if n == 0:
            return
        period   = 1.0 / rate
        t_in_p   = t % period
        active   = int(t_in_p / period * n)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            is_on = seq_i == active or (overlap and seq_i == (active + 1) % n)
            ef.values[orig_i][ch] = open_val if is_on else close_val

    reg(EffectPreset(
        id="valve_sequential", name="顺序开合", category="Chase",
        description="依次开启每个阀门/泵，形成追逐效果",
        target_channels=["valve","pump"], icon="⊙⊙⊙",
        params=[
            ParamSpec("ch_type",   "通道类别", "choice", "valve", choices=["valve","pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("rate",      "速度 (Hz)", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("open_val",  "开启值",   "int",   255, 0, 255, 1,
                tooltip="控制设备开启/激活时的输出数值。表示效果作用时的强度"),
            ParamSpec("close_val", "关闭值",   "int",   0,   0, 255, 1,
                tooltip="控制设备关闭时的输出数值。表示效果未作用时的基准"),
            ParamSpec("overlap",   "重叠",     "bool",  False,
                tooltip="控制重叠的开关状态。开启时启用该功能，关闭时禁用"),
        ],
    ), _valve_sequential)

    def _pump_pressure_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        base_p   = int(params.get("base_pressure", 128))
        amp      = int(params.get("amplitude", 80))
        p_step   = float(params.get("phase_step", 0.15))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = seq_i * p_step
            v = base_p + int(amp * math.sin((t * speed + phase) * 2 * math.pi))
            ef.values[orig_i]["pump"] = max(0, min(255, v))

    reg(EffectPreset(
        id="pump_pressure_wave", name="压力波", category="Wave",
        description="泵的压力波动，相邻设备有相位差",
        target_channels=["pump"], icon="〜",
        params=[
            ParamSpec("speed",         "速度",     "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("base_pressure", "基础压力", "int",   128, 0, 255, 1,
                tooltip="控制基础压力的输出强度。0 为最小值，255 为最大值"),
            ParamSpec("amplitude",     "振幅",     "int",   80,  0, 127, 1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("phase_step",    "相位步长", "float", 0.15, 0.0, 0.5, 0.01,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _pump_pressure_wave)

    # ═══════════════════════════════════════════════════════════════════════
    #  音频增强预设（通用，适配所有通道类型）
    # ═══════════════════════════════════════════════════════════════════════

    def _audio_spectrum_map(ef, t, sorted_fx, fdefs, params, fps):
        bpm   = float(params.get("bpm", 120.0))
        ch    = params.get("ch_type", "dimmer")
        lo    = int(params.get("lo", 0))
        hi    = int(params.get("hi", 255))
        bands = 8
        beat_period = 60.0 / bpm
        n = len(sorted_fx)
        if n == 0:
            return
        beat_phase = (t % beat_period) / beat_period
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            band_idx = int(seq_i / n * bands)
            energy = math.exp(-beat_phase * (3 + band_idx * 0.5)) * (1.0 - band_idx / bands * 0.6)
            ef.values[orig_i][ch] = max(0, min(255, int(lo + energy * (hi - lo))))

    reg(EffectPreset(
        id="audio_spectrum_map", name="频谱映射", category="Audio",
        description="将频谱能量映射到设备序列（低频→前排，高频→后排）",
        target_channels=CH_TYPES, icon="📊",
        params=[
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm",     "BPM",      "float",  120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("lo",      "最小值",   "int",    0,     0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi",      "最大值",   "int",    255,   0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _audio_spectrum_map)

    def _audio_beat_fader(ef, t, sorted_fx, fdefs, params, fps):
        bpm        = float(params.get("bpm", 120.0))
        ch         = params.get("ch_type", "dimmer")
        peak       = int(params.get("peak", 255))
        base       = int(params.get("base", 0))
        decay      = float(params.get("decay", 4.0))
        offset_per = float(params.get("offset_per", 0.0))
        beat_period = 60.0 / bpm
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            beat_phase = ((t + seq_i * offset_per) % beat_period) / beat_period
            energy = math.exp(-beat_phase * decay)
            ef.values[orig_i][ch] = max(0, min(255, int(base + energy * (peak - base))))

    reg(EffectPreset(
        id="audio_beat_fader", name="节拍推杆", category="Audio",
        description="节拍时推到峰值，然后按指数衰减，适配所有通道",
        target_channels=CH_TYPES, icon="♩▲",
        params=[
            ParamSpec("ch_type",    "通道类别", "choice", "dimmer", choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm",        "BPM",      "float",  120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("peak",       "峰值",     "int",    255,   0, 255, 1,
                tooltip="控制输出范围的上限值。效果输出的最高峰值"),
            ParamSpec("base",       "基础值",   "int",    0,     0, 255, 1,
                tooltip="控制效果的基准值。所有变化以此值为中心上下波动"),
            ParamSpec("decay",      "衰减速度", "float",  4.0,   0.5, 20.0, 0.5,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("offset_per", "设备偏移", "float",  0.0,   0.0, 0.5,  0.01,
                tooltip="控制设备偏移的比例。0 为最小，1.0 为最大"),
        ],
    ), _audio_beat_fader)

    def _audio_beat_joystick(ef, t, sorted_fx, fdefs, params, fps):
        bpm          = float(params.get("bpm", 120.0))
        return_speed = float(params.get("return_speed", 3.0))
        spread       = float(params.get("spread", 0.4))
        beat_period  = 60.0 / bpm
        beat_idx     = int(t / beat_period)
        beat_phase   = (t % beat_period) / beat_period
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            rng = random.Random(beat_idx * 1000 + seq_i)
            tx = 0.5 + (rng.random() - 0.5) * spread * 2
            ty = 0.5 + (rng.random() - 0.5) * spread * 2
            x = tx + (0.5 - tx) * min(1.0, beat_phase * return_speed)
            y = ty + (0.5 - ty) * min(1.0, beat_phase * return_speed)
            ef.values[orig_i]["motor_x"] = int(max(0.0, min(1.0, x)) * 255)
            ef.values[orig_i]["motor_y"] = int(max(0.0, min(1.0, y)) * 255)

    reg(EffectPreset(
        id="audio_beat_joystick", name="节拍摇杆", category="Audio",
        description="节拍时电机跳到随机位置，然后回归中心",
        target_channels=["motor_x","motor_y"], icon="♩🎯",
        params=[
            ParamSpec("bpm",          "BPM",      "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("return_speed", "回归速度", "float", 3.0,   0.5, 10.0, 0.5,
                tooltip="控制回归速度的值。范围 0.5~10.0，值越大越强"),
            ParamSpec("spread",       "扩散范围", "float", 0.4,   0.1, 0.5,  0.05,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ],
    ), _audio_beat_joystick)

    def _audio_beat_valve(ef, t, sorted_fx, fdefs, params, fps):
        bpm        = float(params.get("bpm", 120.0))
        open_val   = int(params.get("open_val", 255))
        close_val  = int(params.get("close_val", 0))
        open_ratio = float(params.get("open_ratio", 0.3))
        ch         = params.get("ch_type", "valve")
        beat_period = 60.0 / bpm
        beat_phase  = (t % beat_period) / beat_period
        val = open_val if beat_phase < open_ratio else close_val
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="audio_beat_valve", name="节拍阀门", category="Audio",
        description="节拍时开阀，节拍后关阀，适配阀门和泵",
        target_channels=["valve","pump"], icon="♩⊙",
        params=[
            ParamSpec("ch_type",    "通道类别", "choice", "valve", choices=["valve","pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm",        "BPM",      "float",  120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("open_val",   "开启值",   "int",    255,   0, 255, 1,
                tooltip="控制设备开启/激活时的输出数值。表示效果作用时的强度"),
            ParamSpec("close_val",  "关闭值",   "int",    0,     0, 255, 1,
                tooltip="控制设备关闭时的输出数值。表示效果未作用时的基准"),
            ParamSpec("open_ratio", "开启比例", "float",  0.3,   0.05, 0.95, 0.05,
                tooltip="控制开启比例的比例。0 为最小，1.0 为最大"),
        ],
    ), _audio_beat_valve)


# # 在 EffectEngine._register_all 中自动调用 v21 + v22 预设
_orig_register_all = EffectEngine._register_all
def _patched_register_all(self):
    import logging
    _log = logging.getLogger(__name__)
    _orig_register_all(self)
    try:
        _register_v21_presets(self)
    except Exception as e:
        _log.warning("v21 preset registration failed: %s", e, exc_info=True)
    # v22 扩展预设
    try:
        from .effect_presets_v22 import _register_v22_presets
        _register_v22_presets(self)
    except Exception as e:
        _log.warning("v22 preset registration failed: %s", e, exc_info=True)
    # v23 空间图案预设（基于 TopView 坐标，支持音频/摇杆调制）
    # ★ 修复：原 try/except 静默吞掉异常，导致用户看不到"图案/..."类别
    #   下的预设却没任何错误提示。现在记 warning 让排查容易。
    try:
        from .effect_presets_v23 import _register_v23_presets
        _register_v23_presets(self)
    except Exception as e:
        _log.warning("v23 preset registration failed: %s", e, exc_info=True)
    # v24 喷泉专用效果库大幅扩充
    try:
        from .effect_presets_v24 import _register_v24_presets
        _register_v24_presets(self)

        from .effect_presets_v25 import _register_v25_presets
        _register_v25_presets(self)
        from .effect_presets_v26 import _register_v26_presets
        _register_v26_presets(self)
    except Exception as e:
        _log.warning("v24 preset registration failed: %s", e, exc_info=True)
    # v27 矩阵规律性图案超大扩充
    try:
        from .effect_presets_v27 import _register_v27_presets
        _register_v27_presets(self)
    except Exception as e:
        _log.warning("v27 preset registration failed: %s", e, exc_info=True)
    # v28 效果库超大扩充（矩阵+灯光+泵+电机变式）
    try:
        from .effect_presets_v28 import _register_v28_presets
        _register_v28_presets(self)
    except Exception as e:
        _log.warning("v28 preset registration failed: %s", e, exc_info=True)
EffectEngine._register_all = _patched_register_all

# ─── 追加注册 v29-v32 ───────────────────────────────────────────────
_patched_v28 = EffectEngine._register_all
def _patched_register_all_v29_v32(self):
    import logging
    _log = logging.getLogger(__name__)
    _patched_v28(self)
    # v29 空间编组触发矩阵效果（三角/菱形/方形/星形序列动画）
    try:
        from .effect_presets_v29 import _register_v29_presets
        _register_v29_presets(self)
    except Exception as e:
        _log.warning("v29 preset registration failed: %s", e, exc_info=True)
    # v30 灯光效果大扩充
    try:
        from .effect_presets_v30 import _register_v30_presets
        _register_v30_presets(self)
    except Exception as e:
        _log.warning("v30 preset registration failed: %s", e, exc_info=True)
    # v31 泵效果大扩充
    try:
        from .effect_presets_v31 import _register_v31_presets
        _register_v31_presets(self)
    except Exception as e:
        _log.warning("v31 preset registration failed: %s", e, exc_info=True)
    # v32 电机效果大扩充
    try:
        from .effect_presets_v32 import _register_v32_presets
        _register_v32_presets(self)
    except Exception as e:
        _log.warning("v32 preset registration failed: %s", e, exc_info=True)
EffectEngine._register_all = _patched_register_all_v29_v32


# ─── 追加注册 v33：普通效果极大量变式扩充（无 matrix / 矩阵） ───────
_patched_v32 = EffectEngine._register_all
def _patched_register_all_v33(self):
    import logging
    _log = logging.getLogger(__name__)
    _patched_v32(self)
    try:
        from .effect_presets_v33 import _register_v33_presets
        _register_v33_presets(self)
    except Exception as e:
        _log.warning("v33 preset registration failed: %s", e, exc_info=True)
EffectEngine._register_all = _patched_register_all_v33

# ─── 追加注册 v34/v35/v36：电机动作大量扩充 + Cue一次性动作 ───────────
_patched_v33 = EffectEngine._register_all
def _patched_register_all_v34_v36(self):
    import logging
    _log = logging.getLogger(__name__)
    _patched_v33(self)
    # v34 单轴电机基础动作大量变式（约65个）
    try:
        from .effect_presets_v34 import _register_v34_presets
        _register_v34_presets(self)
    except Exception as e:
        _log.warning("v34 preset registration failed: %s", e, exc_info=True)
    # v35 XY双轴联动动作（22/44交叉、扇形开合、圆形、波浪等，约59个）
    try:
        from .effect_presets_v35 import _register_v35_presets
        _register_v35_presets(self)
    except Exception as e:
        _log.warning("v35 preset registration failed: %s", e, exc_info=True)
    # v36 Cue一次性电机动作（约65个，参数丰富，支持发送到时间线）
    try:
        from .effect_presets_v36 import _register_v36_presets
        _register_v36_presets(self)
    except Exception as e:
        _log.warning("v36 preset registration failed: %s", e, exc_info=True)
EffectEngine._register_all = _patched_register_all_v34_v36

# ─────────────────────────────────────────────────────────────────────────────
#  精炼库（审计后重建）：注册手工精品效果 + 剪除审计未通过的填充效果
#  matrix（矩阵效果 / 图案 类）完全不动。
# ─────────────────────────────────────────────────────────────────────────────
_patched_v36 = EffectEngine._register_all
def _patched_register_all_curated(self):
    import logging
    _log = logging.getLogger(__name__)
    _patched_v36(self)
    # 1) 剪除审计未通过(范围内,非matrix)的旧效果
    try:
        from .effect_prune_list import PRUNE_IDS
        for pid in list(PRUNE_IDS):
            self._presets.pop(pid, None)
            if hasattr(self, "_compute_fns"):
                self._compute_fns.pop(pid, None)
    except Exception as e:
        _log.warning("effect prune failed: %s", e, exc_info=True)
    # 2) 注册精炼库手工精品效果
    try:
        from .effect_presets_curated import _register_curated_presets
        n = _register_curated_presets(self)
        _log.info("curated presets registered: %s", n)
    except Exception as e:
        _log.warning("curated preset registration failed: %s", e, exc_info=True)
    # 3) 审计第二轮：额外剪除(英文名matrix图案/机械堆量/电机Cue/旧泵阀) + ASCII占位图标改写 + 分类合并
    try:
        from .effect_audit_patch import EXTRA_PRUNE_IDS, ICON_OVERRIDES, CATEGORY_REMAP
        from .effect_legacy_presets import prune_catalog_entry
        for pid in list(EXTRA_PRUNE_IDS):
            prune_catalog_entry(self, pid)
        for pid, glyph in ICON_OVERRIDES.items():
            p = self._presets.get(pid)
            if p is not None:
                p.icon = glyph
        remapped = 0
        for p in self._presets.values():
            nc = CATEGORY_REMAP.get(p.category)
            if nc:
                p.category = nc
                remapped += 1
        _log.info("audit patch applied: pruned %s, re-iconed %s, remapped %s",
                  len(EXTRA_PRUNE_IDS), len(ICON_OVERRIDES), remapped)
    except Exception as e:
        _log.warning("audit patch failed: %s", e, exc_info=True)
    # 4) R8 再创作新效果（2026-08-13：水镜涟漪/水面风纹/涌浪拍岸/
    #    等离子流彩/噪声流/烛光摇曳/萤火虫场/聚阵焦点）
    try:
        from .effect_presets_r8 import _register_r8_presets
        n = _register_r8_presets(self)
        _log.info("r8 presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 preset registration failed: %s", e, exc_info=True)
    # 5) R8 第二轮·水形家族（2026-08-13：波尔卡跳跃/郁金香杯/扇形分层/水幕梳/
    #    踏浪/碎浪/水圈套水圈/皇冠喷头）
    try:
        from .effect_presets_r8_water import _register_r8_water
        n = _register_r8_water(self)
        _log.info("r8 water presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 water registration failed: %s", e, exc_info=True)
    # 6) R8 第二轮·扫描追逐家族（2026-08-13：慧星扫尾/双头对扫/十字交叉扫/
    #    回旋镖/流星雨/蛇形折返/接力跑/乒乓对弹）
    try:
        from .effect_presets_r8_scan import _register_r8_scan
        n = _register_r8_scan(self)
        _log.info("r8 scan presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 scan registration failed: %s", e, exc_info=True)
    # 7) R8 第二轮·颜色旅程家族（2026-08-13：色相环巡游/双色碰撞融合/温度渐变带/
    #    分频带色彩/极光色带/油膜干涉/双色串扰/色温呼吸）
    try:
        from .effect_presets_r8_color import _register_r8_color
        n = _register_r8_color(self)
        _log.info("r8 color presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 color registration failed: %s", e, exc_info=True)
    # 8) R8 第二轮·空间游戏家族（2026-08-13：象限轮舞/内外圈对冲/对角张力/
    #    棋盘翻牌/螺旋内外交替/两极磁吸/风车四叶/潮汐分界）
    try:
        from .effect_presets_r8_space import _register_r8_space
        n = _register_r8_space(self)
        _log.info("r8 space presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 space registration failed: %s", e, exc_info=True)
    # 9) R8 第二轮·音乐联动家族（2026-08-13：节拍脉冲群/起音余辉/频谱分带/
    #    节拍折返/低频驱动波/呼吸节律/音落涟漪/频谱仰角）
    try:
        from .effect_presets_r8_audio import _register_r8_audio
        n = _register_r8_audio(self)
        _log.info("r8 audio presets registered: %s", n)
    except Exception as e:
        _log.warning("r8 audio registration failed: %s", e, exc_info=True)
    # 10) ★ R132 通用/音频 —— **真音频**分支（见函数末尾的链式注册）


def _register_true_audio_presets(engine) -> int:
    """注册【通用/音频】真音频效果族（★ R132 通用/音频分支，52 种，无音频不出效果）。

    用户："现在 effect 里面关于音频的这些效果全是'以音频为题材的假效果'…有必要加
    一套关于通用-音频分支的效果预设系统…在无音频的时候点击播放不出效果"
    """
    from .effect_presets_audio_true_data import build_true_audio_presets
    from .effect_presets_audio_true import true_audio_kernel
    from .effect_audio_schema import audio_params_for
    from .effect_presets_curated import CH_TYPES, ParamSpec, icon_glyph
    count = 0
    for variant, name, desc, icon, tier, pv in build_true_audio_presets():
        params = [
            ParamSpec("ch_type", "通道类别", "choice", pv.get("ch_type", "dimmer"),
                      choices=CH_TYPES,
                      tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、"
                              "调光器控制灯光、电机控制摆头位置"),
            ParamSpec("rate", "速率", "float", pv["rate"], 0.05, 8.0, 0.05,
                      tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
            ParamSpec("sensitivity", "灵敏度", "float", pv["sensitivity"], 0.05, 1.0, 0.05,
                      tooltip="控制音频输入的灵敏度。值越低需要越强的音频信号才能触发反应"),
            ParamSpec("decay", "衰减", "float", pv["decay"], 0.02, 1.0, 0.01,
                      tooltip="控制节拍/脉冲的衰减时间。值越小越短促，越大余韵越长"),
            ParamSpec("threshold", "阈值", "float", pv["threshold"], 0.0, 1.0, 0.01,
                      tooltip="触发门槛。音频量低于该值时不响应，用于避开底噪"),
            ParamSpec("duty", "占空比", "float", pv["duty"], 0.05, 0.95, 0.05,
                      tooltip="频闪类效果的亮/灭时间占比。值越小亮的时间越短"),
            ParamSpec("floor", "最低亮度", "float", pv["floor"], 0.0, 0.9, 0.05,
                      tooltip="地板值。效果不会低于此亮度，用于弱音时不至于全黑"),
            ParamSpec("pulses", "脉冲数", "int", pv["pulses"], 1, 16, 1,
                      tooltip="欧几里得节奏的脉冲个数。值越大节奏越密"),
            ParamSpec("steps", "步数", "int", pv["steps"], 2, 32, 1,
                      tooltip="欧几里得节奏的总步数。与脉冲数配合决定节奏型"),
            ParamSpec("spread", "相位展开", "float", pv["spread"], 0.0, 2.0, 0.05,
                      tooltip="控制效果在空间上的扩散程度。值越大前后差异越明显"),
            ParamSpec("lo", "低值", "int", pv["lo"], 0, 255, 1,
                      tooltip="输出范围下限。与高值配合定义动态区间"),
            ParamSpec("hi", "高值", "int", pv["hi"], 0, 255, 1,
                      tooltip="输出范围上限。与低值配合定义动态区间"),
        ]
        preset = EffectPreset(
            id=f"atx_{variant}", name=name, category="通用/音频",
            description=desc, target_channels=CH_TYPES,
            icon=icon_glyph(icon), tags=["音频真效果", tier],
            params=audio_params_for(variant, params))
        engine._reg(preset, true_audio_kernel(variant))
        count += 1
    return count


def _register_all_with_true_audio(engine) -> None:
    """★ R132 链尾：在既有注册链之后追加【通用/音频】真音频族。"""
    _patched_register_all_curated(engine)
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        n = _register_true_audio_presets(engine)
        _log.info("true-audio presets registered: %s", n)
    except Exception as e:
        _log.warning("true-audio registration failed: %s", e, exc_info=True)


def _register_all_with_families(engine) -> None:
    """★ R172 链尾：在真音频族之后追加三族新效果（伪随机增强/变速/折返级联）。

    按 v33 的链式 monkey-patch 模式再加一环；ImportError 时 log.warning（静默降级），
    其余异常带栈告警。注册条数断言在 `_register_fam_presets` 内部（死注册教训）。
    """
    _register_all_with_true_audio(engine)
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from .effect_presets_fam_data import _register_fam_presets
        n = _register_fam_presets(engine)
        _log.info("r172 family presets registered: %s", n)
    except ImportError as e:
        _log.warning("r172 family registration failed: %s", e)
    except Exception as e:
        _log.warning("r172 family registration failed: %s", e, exc_info=True)


def _register_all_with_motor_mech(engine) -> None:
    """★ R173 链尾：在三族新效果之后追加【电机/编组】机制族（编组·奇偶反相·相位差演变）。"""
    _register_all_with_families(engine)
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from .effect_presets_motor_mech import register_motor_mech_presets
        n = register_motor_mech_presets(engine)
        _log.info("r173 motor-mech presets registered: %s", n)
    except ImportError as e:
        _log.warning("r173 motor-mech registration failed: %s", e)
    except Exception as e:
        _log.warning("r173 motor-mech registration failed: %s", e, exc_info=True)


def _register_all_with_retire(engine) -> None:
    """★ R173 链尾：全部注册完成后执行"下架"（列表隐藏、按 id 仍可解析，见 effect_retire）。"""
    _register_all_with_motor_mech(engine)
    from .effect_catalog_additions import register_music_presets
    register_music_presets(engine)
    import logging as _logging
    try:
        from .effect_retire import apply_retire
        apply_retire(engine)
    except Exception as e:
        _logging.getLogger(__name__).warning("r173 retire failed: %s", e, exc_info=True)


EffectEngine._register_all = _register_all_with_retire
