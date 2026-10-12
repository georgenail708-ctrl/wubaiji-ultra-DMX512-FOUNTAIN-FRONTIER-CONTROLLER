"""TopView box selection in displayed unit order, expanded to exact members.

Reuse the existing CAD ellipse parameter and validated nearest-neighbour walk.
Packages participate by their visible representative, then expand atomically;
this module owns no selection state, group cache or geometry mutation.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF


def _package_unit(item, valid):
    ids = [str(fid) for fid in (item.get("member_fixture_ids") or [])
           if str(fid) in valid]
    if not ids:
        return None
    try:
        return dict(fid=ids[0], ids=ids, x=float(item.get("pos_x", 0.0)),
                    y=float(item.get("pos_y", 0.0)))
    except (TypeError, ValueError):
        return None


def _page_units(panel):
    valid = panel.fixtures_by_id()
    packages = [unit for item in panel.current_page_logical_devices()
                if (unit := _package_unit(item, valid)) is not None]
    package_members = {fid for unit in packages for fid in unit["ids"]}
    units = []
    for fid in panel.fixture_ids():
        if fid not in package_members:
            x, y = panel.fixture_pos(fid)
            units.append(dict(fid=fid, ids=[fid], x=float(x), y=float(y)))
    return units + packages


def _spline_ellipse_geometry(shape):
    """样条点集外接矩形 → 拟合椭圆 (id, cx, cy, rx, ry)；开弧样条中心偏差大，
    匹配不上会安全落回未匹配链路。"""
    pts = shape.get("points") or []
    if len(pts) < 6:
        return None
    try:
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
    except (TypeError, ValueError, IndexError):
        return None
    rx, ry = (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0
    if rx <= 1e-3 or ry <= 1e-3:
        return None
    return (str(shape.get("id", "")), (max(xs) + min(xs)) / 2.0,
            (max(ys) + min(ys)) / 2.0, rx, ry)


def _ellipse_geometry(shape):
    kind = shape.get("type")
    if kind in ("circle", "ellipse"):
        try:
            rx = abs(float(shape.get("rx", shape.get("r", 0.0))))
            ry = abs(float(shape.get("ry", rx)))
            if rx <= 1e-3:
                return None
            return (str(shape.get("id", "")), float(shape["cx"]),
                    float(shape["cy"]), rx, ry if ry > 1e-3 else rx)
        except (TypeError, ValueError, KeyError):
            return None
    if kind == "spline":
        # R201/P10（用户 2026-10-06 实报）：导入的整圆环是样条
        # （dp-spline-*-circle-*，闭环折线），旧匹配只认 circle/ellipse →
        # 整页 0 匹配 → 闭环链验证不过 → 拖动方向竖线横切。样条拟合后
        # 并入同一极角序。
        return _spline_ellipse_geometry(shape)
    return None


def _shape_match(unit, geometries):
    best = None
    for sid, cx, cy, rx, ry in geometries:
        nx, ny = (unit["x"] - cx) / rx, (unit["y"] - cy) / ry
        residual = abs(math.hypot(nx, ny) - 1.0)
        if best is None or residual < best[0]:
            best = residual, sid, math.atan2(ny, nx)
    if best is not None and best[1] and best[0] <= 0.35:
        return best[1:]
    return None


def fixture_shape_map(panel):
    """Existing ellipse-boundary membership, including package representatives."""
    geometries = [geometry for shape in (panel.current_page().get("cad_shapes") or [])
                  if (geometry := _ellipse_geometry(shape)) is not None]
    result = {}
    for unit in _page_units(panel):
        match = _shape_match(unit, geometries)
        if match is not None:
            result.update((fid, match) for fid in unit["ids"])
    return result


def _angle_order(group, shape_map, dx, dy):
    """Cut the angular cycle at its largest empty gap, then orient the chain."""
    from .topview_panel import _chain_ends_disagree_drag

    group.sort(key=lambda unit: shape_map[unit["fid"]][1])
    angles = [shape_map[unit["fid"]][1] for unit in group]
    gaps = [(angles[(i + 1) % len(angles)] - angle) % math.tau
            for i, angle in enumerate(angles)]
    cut = (max(range(len(gaps)), key=gaps.__getitem__) + 1) % len(group)
    group[:] = group[cut:] + group[:cut]
    if _chain_ends_disagree_drag(group, dx, dy):
        group.reverse()


def _scan_key(unit, dx, dy):
    if abs(dx) >= abs(dy):
        return -unit["x"] if dx < 0.0 else unit["x"]
    return -unit["y"] if dy < 0.0 else unit["y"]


def order_box_units(canvas, units, dx, dy, page_rank=None):
    """框选排序的唯一真源（R201/P10）：图形组极角序（组间按设备序）+
    未匹配簇（闭环极角/开曲线链序，簇间按设备序）。Python 路径与 CE 加速
    路径都必须落到这里，禁止再维护平行排序规则。"""
    from .topview_panel import _order_unassigned_curve_aware

    shape_map = canvas._build_fixture_shape_map()
    groups, unassigned = {}, []
    for unit in units:
        match = shape_map.get(unit["fid"])
        if match is None:
            unassigned.append(unit)
        else:
            groups.setdefault(match[0], []).append(unit)
    for group in groups.values():
        _angle_order(group, shape_map, dx, dy)
    if page_rank:
        # R201/P10（用户定案）：圆与圆之间按「设备顺序」走到下一个圆——
        # 组序用页面设备序（编号徽标同源），不再按拖动方向扫描键。
        ordered_groups = sorted(groups.values(),
                                key=lambda group: min(page_rank.get(str(u["fid"]), len(page_rank))
                                                      for u in group))
    else:
        ordered_groups = sorted(groups.values(),
                                key=lambda group: min(_scan_key(u, dx, dy) for u in group))
    _order_unassigned_curve_aware(unassigned, dx, dy, page_rank)
    return [unit for group in ordered_groups for unit in group] + unassigned


def box_fixture_ids(canvas, start, end):
    rect = QRectF(start, end).normalized()
    units = [unit for unit in _page_units(canvas.panel)
             if rect.contains(QPointF(unit["x"], unit["y"]))]
    if len(units) > 1:
        page_rank = {str(fid): i for i, fid in enumerate(canvas.panel._page_device_order())}
        units = order_box_units(canvas, units, end.x() - start.x(), end.y() - start.y(),
                                page_rank)
    return [fid for unit in units for fid in unit["ids"]]


def rectangular_lattice(points):
    """A two-dimensional Cartesian grid, not merely mirrored x/y coordinates.

    Preserve the existing 0.001 coordinate precision. A symmetric ring repeats
    both axes but occupies only its boundary, so it is not a filled grid.
    """
    cells = {(round(x, 3), round(y, 3)) for x, y in points}
    xs, ys = {x for x, _ in cells}, {y for _, y in cells}
    return len(xs) > 1 and len(ys) > 1 and len(cells) == len(xs) * len(ys)
