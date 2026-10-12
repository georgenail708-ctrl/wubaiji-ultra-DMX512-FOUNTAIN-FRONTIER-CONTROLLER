# -*- coding: utf-8 -*-
"""调度台表格的**共享造型常量与字体助手**（从 `table_view` 拆出）。

存在理由：常量与字体助手同时被 `table_view`（布局/输入）与 `table_paint`（绘制）
使用；放在第三方模块里，两者都单向依赖它，**避免两个实现模块互相 import 成环**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Tuple

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QFont

from .. import qascade_theme as _th
from ...processing.dispatch.table_model import CHANNEL_PANELS


# ── 造型常量（4px 基准网格；见计划 §0.3 美术规格）─────────────────────────
PAD = 8                  # 视图四周留白
SEC_H = 20               # 分区条高（模块来源）
SEC_GAP = 10             # 分区之间呼吸（分区间距）
GRP_H = 16               # 组条高（已创建组母/临时选中子）
GRP_INDENT = 10          # 子组条（临时选中）相对母组条的缩进
HDR_H = 24               # 值类型列头高
GRP_HDR_H = 15           # 参数分组条高（列头之上）
ROW_H = 24               # 数据行高
ROWF_H = 0               # 独立行头列宽在 _row_header_width() 里算
CELL_R = 3.0             # 数据格圆角（★ 表格用窄圆角，避免相邻格出现视觉缝隙）
CHROME_R = 4.0           # 列头/组条圆角
GRID_A = 13              # 网格线 α（与主题 QSS gridline-color 同值）
ZEBRA_A = 4              # 斑马行 α（与主题 QSS alternate-background-color 同值）
VALUE_BAR_H = 2          # 格底部分类色值条高（液态玻璃招牌）
EDGE_SCROLL = 24         # 拖拽到边缘的自动滚动感区

#: 状态码 → 是否显示数字
_STATE_NA = "na"
_STATE_EMPTY = "empty"

#: QFont 可用的字族列表（**顺序真源**）。
#  ★ 为什么不用 `_th.FONT_MAIN`：那是给 QSS 用的**引号串**（"'Inter','Segoe UI',…"），
#    直接 `QFont(_th.FONT_MAIN)` 会把整串当字族名 → 解析失败 → 回退到无中文字形的字体，
#    结果是**全中文变方框**（离屏渲染实测踩到，见 _ui_validation/dispatch_table）。
#    QFont 必须用 `setFamilies([...])` 传**逐个字族名**。
_UI_FAMILIES = ("Inter", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei")
_MONO_FAMILIES = ("Consolas", "DejaVu Sans Mono", "Courier New")

#: 来源面板 → 角标单字（克制技术感；不用图标资源）
_SOURCE_GLYPH = {
    "programmer": "P", "effect": "E", "matrix": "M", "linker": "L",
    "audio": "A", "joystick": "J", "clip": "C", "route": "R",
}
#: 来源面板 → 角标色（各自可辨；与分区标题的左侧色条互相呼应）
_SOURCE_COLOR = {
    "programmer": _th.FOCUS_BLUE, "effect": _th.BRIGHT_SUCCESS,
    "matrix": _th.BRIGHT_WARN, "linker": _th.BRIGHT_INFO,
    "audio": "#B07BE0", "joystick": "#E08BB0",
    "clip": _th.ACTIVE_ORANGE, "route": _th.SELECT_ORANGE,
}


#: 归一化通道类型 → `_th.CATEGORY_COLORS` 的键（该表用全名 red/green/blue/white）。
#  ★ 不写进 CATEGORY_COLORS 本身：那是**跨页面共享**色表，其它页面可能遍历其键
#    （加别名键会让它们的循环多出重复项）。故映射放在表格视图本地。
_CAT_COLOR_ALIAS = {
    "r": "red", "g": "green", "b": "blue", "w": "white",
    "dimmer": "dimmer", "intensity": "intensity",
    "motor_x": "pan", "motor_y": "tilt", "motor_z": "fx",
    "pump": "other", "valve": "other",
}


# ── 运行时度量（缩放）────────────────────────────────────────────────────
# 用户 2026-09-14："这个页面明明很多空位，设备通道表格做的过于小了，横向纵向空间都有
# 很多显示空间。你应该调整大一些，并且随着页面的缩放自动调整。"
#
# 做法：上表的常量是**基准**（1.0×），实际渲染尺寸 = 基准 × 当前缩放。缩放由视图宽度
# 推出（见 `compute_scale`），在布局重算时 `set_scale()` 落地 —— 布局/绘制/命中都读
# `row_h()` / `hdr_h()` 等，故三者永远一致（同一份真源）。
_SCALE: float = 1.0
#: 缩放上下限：太小的屏不挤压可读性，太大的屏不过度稀疏
SCALE_MIN = 1.0
SCALE_MAX = 1.9
#: 基准尺寸（放大后：行高 24→32、列头 24→30、列最小宽 44→64）
ROW_H_BASE = 32
HDR_H_BASE = 30
#: 单元格最小宽度（基准 × 缩放）——放宽后长文本（效果名/条带）才放得下
COL_MIN_BASE = 64
#: 行头（设备名列）宽度上限（基准 × 缩放）
ROW_HDR_MAX_BASE = 220


def set_scale(scale: float) -> bool:
    """设置全局缩放（夹到 [SCALE_MIN, SCALE_MAX]）；变化时返回 True。"""
    global _SCALE
    try:
        s = float(scale)
    except (TypeError, ValueError):
        return False
    s = max(SCALE_MIN, min(SCALE_MAX, s))
    if abs(s - _SCALE) < 0.01:
        return False
    _SCALE = s
    return True


def scale() -> float:
    """当前缩放（≥1.0）。"""
    return float(_SCALE)


def compute_scale(view_width: int, content_width: int) -> float:
    """由"可用宽 / 内容自然宽"推缩放：空位多就放大，内容超宽则保持 1.0。

    `view_width`    视口可用宽（像素）
    `content_width` 内容在 1.0× 下的自然总宽（行头 + 各列）
    → 比率即"空位程度"；夹到 [SCALE_MIN, SCALE_MAX]，并对比率做温和压缩
      （`sqrt`）避免列少时一下放到最大而显得空旷。
    """
    try:
        vw = max(1, int(view_width))
        cw = max(1, int(content_width))
    except (TypeError, ValueError):
        return SCALE_MIN
    if cw >= vw:
        return SCALE_MIN
    ratio = (float(vw) / float(cw)) ** 0.5
    return max(SCALE_MIN, min(SCALE_MAX, ratio))


def row_h() -> int:
    """数据行高（基准 × 缩放）。"""
    return max(ROW_H, int(round(ROW_H_BASE * _SCALE)))


def hdr_h() -> int:
    """列头高（基准 × 缩放）。"""
    return max(HDR_H, int(round(HDR_H_BASE * _SCALE)))


def grp_hdr_h() -> int:
    """参数分组条高（随缩放，保证不挤到列头）。"""
    return max(GRP_HDR_H, int(round(GRP_HDR_H * _SCALE)))


def col_min_w() -> int:
    """单元格最小宽（基准 × 缩放）。"""
    return max(44, int(round(COL_MIN_BASE * _SCALE)))


def row_hdr_max() -> int:
    """行头（设备名）列宽上限（基准 × 缩放）。"""
    return max(150, int(round(ROW_HDR_MAX_BASE * _SCALE)))


def cell_font_px(base: int) -> int:
    """单元格/表头字体像素（随缩放，保证大格子里字不显小）。"""
    return max(int(base), int(round(int(base) * (0.85 + 0.15 * _SCALE) * _SCALE)))


def ui_font(pixel: int, bold: bool = False) -> QFont:
    """界面字体（支持中文）：按 `_UI_FAMILIES` 逐个字族设置。

    R183 横向性能批：表格 paint 逐格取字 → 模块级缓存（共享只读实例，
    调用方只 setFont，不得 set* 变异）。
    """
    return _th.cached_font(int(pixel), bold, families=_UI_FAMILIES)


def mono_font(pixel: int, bold: bool = False) -> QFont:
    """等宽字体（数字/地址）：按 `_MONO_FAMILIES` 逐个字族设置。（缓存同上）"""
    return _th.cached_font(int(pixel), bold, families=_MONO_FAMILIES)


def _split_col_key(col_key: str) -> Tuple[str, str]:
    """格键 → (列键, 子序号串)。无 `@` 时子序号为空。"""
    s = str(col_key or "")
    if "@" in s:
        head, _, tail = s.rpartition("@")
        return head, tail
    return s, ""


#: 需要"斜纹 N/A"语义的分区：**只有通道类**（Programmer）——它的空格表示
#  "该设备没有这个通道"（信息性）。效果/Clip 区的空格表示"该效果没作用到这台
#  设备"，用纯平空格（`_draw_blank`），避免把"无关"误报成"缺失"。
_CHANNEL_SECTIONS = frozenset(CHANNEL_PANELS)


@dataclass
class _LayItem:
    """布局项（**唯一真源**，_relayout 与 paintEvent 共用）。"""
    kind: str               # 'section'|'group'|'group_hdr'|'col_hdr'|'row_hdr'|'cell'|'clipnote'
    rect: QRect
    section: str = ""
    folder: str = ""
    label: str = ""
    col_key: str = ""
    col: Any = None
    row: Any = None
    color: Any = None
    depth: int = 0
    count: int = 0
    #: 布局期**预解析**的格与分区引用：绘制热路径直接用，避免每帧做
    #  `row.cell_of()`（线性）/ `model.section()`（线性）查找。
    cell: Any = None
    sec: Any = None
