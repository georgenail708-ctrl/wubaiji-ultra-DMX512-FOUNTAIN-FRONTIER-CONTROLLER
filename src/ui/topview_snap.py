# -*- coding: utf-8 -*-
"""TopView 吸附分级（R183/F2 · 深查③ T6）+ 小地图（T3）。

吸附三级（任务书定案）：
  1. **网格**（步长 = `TopViewCanvas._draw_grid` 的实时步长）；
  2. **对象缘**（拖动选中集包围盒的四条边与其他设备的 x/y 轴对齐）；
  3. **参考线**（`topview_rulers.GuideSet`，锁定线同样参与吸附）。
优先级：参考线 > 对象缘 > 网格（后者只在前者无命中时生效）；Alt 临时关闭
（按下时读修饰键，等价节点编辑器的"临时禁用吸附"）。

吸附判定对**选中集包围盒**做（拖动多台时整体贴合，不只是光标点）：候选对齐
轴 = 参考线 ∪ 其他设备中心轴 ∪ 网格；对每条候选轴取「bbox 的 min/center/max
三个吸附点」中位移最小者 → 输出整体位移 (dx, dy)，再由调用方一次性平移。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: 吸附容差（屏幕像素）：命中半径，按画布缩放换算世界单位。
SNAP_TOL_PX = 7.0
#: 网格档优先级最低：只有参考线/对象缘都未命中时才回落到网格。
TIER_PRIORITY = {"guide": 3, "edge": 2, "grid": 1, "none": 0}
#: 网格量化步长在屏幕上的上限：太密时不再吸附网格（避免"处处都能吸"的手感）。
GRID_SNAP_MAX_STEP_PX = 60.0


def _axis_offsets(lo: float, hi: float) -> List[Tuple[float, str]]:
    """bbox 在一条轴上的三个吸附参考点：min / center / max。"""
    return [(lo, "min"), ((lo + hi) / 2.0, "center"), (hi, "max")]


def _best_axis_shift(candidates: Sequence[Tuple[float, str]],
                     refs: Sequence[Tuple[float, str]],
                     tol: float) -> Optional[Tuple[float, str, float]]:
    """在候选轴 `candidates=(轴值, 档位)` 中为参考点找最小位移。

    返回 `(位移, 档位, 命中的候选轴值)`；容差内无命中返回 None。
    档位高者优先（同级时取位移最小者）——参考线 > 对象缘 > 网格。
    """
    best: Optional[Tuple[float, str, float]] = None
    best_key: Optional[Tuple[int, float]] = None
    for value, tier in candidates:
        for ref, _tag in refs:
            shift = float(value) - float(ref)
            if abs(shift) > tol:
                continue
            key = (TIER_PRIORITY.get(tier, 0), -abs(shift))
            if best_key is None or key > best_key:
                best_key, best = key, (shift, tier, float(value))
    return best


def _grid_candidates(lo: float, hi: float, step: float) -> List[Tuple[float, str]]:
    """bbox 的 min/max 两端最近的网格线（各一条，避免候选爆炸）。"""
    out: List[Tuple[float, str]] = []
    for v in (lo, hi):
        out.append((round(float(v) / step) * step, "grid"))
    return out


def _axis_candidates(lo: float, hi: float, gvals: Sequence[float],
                     ovals: Sequence[float], grid_step: Optional[float],
                     ) -> List[Tuple[float, str]]:
    """一条轴的候选吸附线：参考线 ∪ 对象缘 ∪ 网格（各带档位标签）。"""
    cands: List[Tuple[float, str]] = [(float(v), "guide") for v in gvals]
    cands += [(float(v), "edge") for v in ovals]
    if grid_step and float(grid_step) > 0.0:
        cands += _grid_candidates(lo, hi, float(grid_step))
    return cands


def snap_translation(bbox: Optional[Sequence[float]], *,
                     guide_xs: Sequence[float] = (),
                     guide_ys: Sequence[float] = (),
                     other_xs: Sequence[float] = (),
                     other_ys: Sequence[float] = (),
                     grid_step: Optional[float] = None,
                     tol: float = SNAP_TOL_PX,
                     alt_off: bool = False,
                     ) -> Tuple[float, float, str, Optional[float], Optional[float]]:
    """计算选中集 bbox 的吸附位移。

    返回 `(dx, dy, tier, line_x, line_y)`：
      * `tier ∈ {"guide","edge","grid","none"}` —— x/y 两轴命中的最高档位；
      * `line_x/line_y` = 吸附命中的世界轴线坐标（供覆盖层画指示线），未命中 None。

    Alt 关闭（`alt_off=True`）恒返回零位移 + "none"（任务书：Alt 临时关）。
    """
    if alt_off or not bbox:
        return 0.0, 0.0, "none", None, None
    lo_x, lo_y, hi_x, hi_y = (float(bbox[0]), float(bbox[1]),
                              float(bbox[2]), float(bbox[3]))
    tol = max(1e-6, float(tol))
    axes = (
        ("x", _axis_offsets(lo_x, hi_x), lo_x, hi_x, guide_xs, other_xs),
        ("y", _axis_offsets(lo_y, hi_y), lo_y, hi_y, guide_ys, other_ys),
    )
    dx = dy = 0.0
    line_x = line_y = None
    tier_rank, tier_name = 0, "none"
    for axis, refs, lo, hi, gvals, ovals in axes:
        hit = _best_axis_shift(
            _axis_candidates(lo, hi, gvals, ovals, grid_step), refs, tol)
        if hit is None:
            continue
        shift, tier, value = hit
        if axis == "x":
            dx, line_x = shift, value
        else:
            dy, line_y = shift, value
        if TIER_PRIORITY.get(tier, 0) >= tier_rank:
            tier_rank, tier_name = TIER_PRIORITY.get(tier, 0), tier
    return dx, dy, tier_name, line_x, line_y


def fixture_centers(panel) -> Dict[str, Tuple[float, float]]:
    """当前页可见设备中心表（吸附候选源）。"""
    out: Dict[str, Tuple[float, float]] = {}
    try:
        ids = panel.fixture_ids()
    except Exception:
        return out
    for fid in ids:
        try:
            x, y = panel.fixture_pos(str(fid))
        except Exception:
            continue
        out[str(fid)] = (float(x), float(y))
    return out


def center_axes(centers: Dict[str, Tuple[float, float]],
                exclude: Iterable[str] = ()) -> Tuple[List[float], List[float]]:
    """其他设备的 x/y 轴值（排除正在拖动的设备）。"""
    skip = {str(x) for x in exclude}
    xs: List[float] = []
    ys: List[float] = []
    for fid, (x, y) in centers.items():
        if fid in skip:
            continue
        xs.append(x)
        ys.append(y)
    return xs, ys


def compute_drag_bbox(cursor: Tuple[float, float], offsets: Dict[str, Tuple[float, float]],
                      dx: float = 0.0, dy: float = 0.0) -> Optional[Tuple[float, float, float, float]]:
    """拖动中的选中集包围盒。

    必须用「光标 + 按下时偏移量」推算，**不能**读 `page['fixtures']` 的当前
    坐标：画布 `mouseMoveEvent` 每帧已把设备写到光标位（读回会得到"已吸附过的
    位置"，导致吸附自我叠加、拖不动）。偏移量语义与 `mousePressEvent` 一致
    （`offset = 按下时设备坐标 − 按下时光标`），故 `cursor + offset` 恒等于
    "若本帧不吸附，设备将被写到的位置"。
    """
    xs: List[float] = []
    ys: List[float] = []
    for off in offsets.values():
        xs.append(float(cursor[0]) + float(off[0]) + float(dx))
        ys.append(float(cursor[1]) + float(off[1]) + float(dy))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
