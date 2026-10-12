# -*- coding: utf-8 -*-
"""T11 TopView 交互几何剩余 Python 热点 → numpy 批量/C 内核（隔离版）。

盘点（render_lab/bench/ce_probe_topview_interact.py 实测, 至冬7.0 11382 台）:
  P1  translate_current  34ms/move（_xform_targets 24ms + 逐台坐标循环 6ms）
  P2  rotate_current     37ms/move（24ms + 逐台 _rotate_point_xy 10ms + cos/sin 每台重算）
  P4  _selection_box_fixture_ids  11-16ms/框选（逐台 QPointF+QRectF.contains）
  P5  _build_fixture_shape_map    11.4ms（逐台×逐圆 math.hypot）
  P6  _ensure_fixture_hover_index  8.6ms/次重建（每次拖动 move 后）
  P7a _build_spatial_index        12.8ms（布局变更后一次）
  +   TopologyDetector._detect_x/y_symmetry  O(N²)（11382 台 → 分钟级）
  +   TopologyDetector._simple_cluster       O(N²) BFS（11382 台 → 分钟级）

接入（全部不碰 src/；monkeypatch + 门控回退）:
  A1  _ordered_selected_ids      结构指纹缓存（页面/选择/工程对象身份+长度）
  A2  _xform_targets             去重 + 结果缓存（72baed4 同键同存; 拖动会话 O(1)）
  A3  translate_current          numpy 批量平移（逐元素同一 IEEE 表达式）+
                                  ★ 增量脏区重绘 + 质心增量更新（72baed4 同步）
  A4  rotate_current             numpy 批量旋转（cos/sin 一次; 成员批量同式）+
                                  ★ 增量脏区重绘 + 质心增量更新（72baed4 同步）
  B1  _selection_box_fixture_ids numpy 矩形包含（Qt contains 语义逐位复刻）
  B2  _build_fixture_shape_map   ce_assign_shapes C 内核（S5 fuzz 已证位级）+
                                  角度仍用原版 atan2 公式逐台
  B3  _ensure_fixture_hover_index 批量取坐标 + 原版循环体
  B4  _build_spatial_index        批量取坐标
  C1  _detect_x/y_symmetry       排序 + searchsorted 窗口（原版布尔结论不变）
  C2  _simple_cluster            网格候选 + 并查集（连通分量与 BFS 等价）
                                  —— 输出 = 连通分量按最小索引升序（原版 BFS
                                     逐字节等价, 无浮点运算只有同式比较）

自门控: 每个 hook 在值类型/结构异常时回退原版（逐字节不变）;
环境门: QASCADE_CE_T11=1 才安装（默认关）。
"""
from __future__ import annotations

import logging
import math
import os

import numpy as np

log = logging.getLogger(__name__)

# 激活计数（诊断: >0 = 批量/C 路径真实执行, 非静默回退原版）
_T11_CNT = {
    "ordered": 0, "xform": 0, "translate": 0, "rotate": 0, "rotate_members": 0,
    "box": 0, "shape_map": 0, "hover": 0, "spatial": 0,
    "sym_x": 0, "sym_y": 0, "cluster": 0,
}

_NUM = (int, float)
_MISSING = object()

_INSTALLED = False


# ─────────────────────────────────────────────────────────────────────
#  公共: 批量坐标收集（缺失位物化默认坐标, 同 fixture_pos 语义）
# ─────────────────────────────────────────────────────────────────────
def _gather_positions(panel, fids, fpos):
    """按 fids 顺序批量取 (xs, ys) np.float64。

    物化语义与 fixture_pos 一致: 缺失键 → _default_fixture_pos 写入 fpos。
    值类型非 int/float（Decimal/Fraction/str/None）或结构异常 →
    返回 (None, None)，调用方回退原版循环（逐字节不变）。
    """
    n = len(fids)
    if n == 0:
        return np.empty(0, np.float64), np.empty(0, np.float64)
    xl = []
    yl = []
    try:
        for fid in fids:
            v = fpos.get(fid, _MISSING)
            if v is _MISSING:
                fx, fy = panel._default_fixture_pos(fid)
                fpos[fid] = {"x": fx, "y": fy}
                xl.append(fx)
                yl.append(fy)
                continue
            xv = v.get("x")
            yv = v.get("y")
            if not (isinstance(xv, _NUM) and isinstance(yv, _NUM)):
                return None, None
            xl.append(xv)
            yl.append(yv)
    except Exception:
        return None, None
    try:
        xs = np.asarray(xl, np.float64)
        ys = np.asarray(yl, np.float64)
    except Exception:
        return None, None
    return xs, ys


def _sel_scope_key(panel):
    """_ordered_selected_ids 读取依赖的结构指纹; 任一变化 → 缓存失效。

    覆盖: 页面列表结构(增删页)、每页 visible_fixture_ids/logical_devices
    （对象身份+长度, 列表被整体替换或原位增删均触发）、选择集、工程设备表。
    依赖全部由「替换列表对象」或「新建字典」的写法更新（代码库无原位元素
    替换 visible/ld 列表的模式）; 任何异常 → None（不缓存, 走原版）。
    """
    try:
        pages = panel._pages
        return (
            id(pages),
            id(panel._selection_order_ids), len(panel._selection_order_ids),
            id(panel.selected_ids), len(panel.selected_ids),
            id(panel.fixtures_by_id()),
            tuple(
                (id(p),
                 id(p.get("visible_fixture_ids") or ()),
                 len(p.get("visible_fixture_ids") or ()),
                 id(p.get("logical_devices") or ()),
                 len(p.get("logical_devices") or ()))
                for p in pages
            ),
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
#  A1: _ordered_selected_ids 缓存（结构指纹失效, 逐字节同原版）
# ─────────────────────────────────────────────────────────────────────
def _hook_ordered_selected_ids(panel_cls, orig):
    def ce_ordered_selected_ids(self):
        key = _sel_scope_key(self)
        if key is not None:
            cached = getattr(self, "_t11_sel_cached", None)
            if getattr(self, "_t11_sel_key", None) == key and cached is not None:
                _T11_CNT["ordered"] += 1
                return list(cached)  # 新列表, 防止调用方改写缓存
        ordered = orig(self)
        if key is not None:
            self._t11_sel_key = key
            self._t11_sel_cached = ordered
        return ordered

    panel_cls._ordered_selected_ids = ce_ordered_selected_ids
    log.info("[CE] T11 A1 已接入: _ordered_selected_ids → 结构指纹缓存")


# ─────────────────────────────────────────────────────────────────────
#  A2: _xform_targets 去重 + 结果缓存（72baed4 同键同存: 键 = _xform_cache_key
#      结构指纹, 拖动会话内 O(1) 命中; 值持引用不复制, 与原版语义一致）
# ─────────────────────────────────────────────────────────────────────
def _hook_xform_targets(panel_cls, orig):
    def ce_xform_targets(self, scope="selection"):
        key = self._xform_cache_key()
        cached = getattr(self, "_xform_targets_cache", None)
        if cached is not None and cached[0] == key:
            _T11_CNT["xform"] += 1
            return cached[1]
        vis = set(self.fixture_ids())
        all_lds = self.current_page_logical_devices()
        ordered = self._ordered_selected_ids()
        sel_full = set(ordered)
        sel = [fid for fid in ordered if fid in vis]
        lds = []
        member_ids: set = set()
        for ld in all_lds:
            members = {str(m) for m in (ld.get("member_fixture_ids", []) or [])}
            if members and members <= sel_full:
                lds.append(ld)
                member_ids |= members
        # 直接可见选中的真实设备里，剔除已被逻辑设备接管的成员（避免重复变换）
        sel = [fid for fid in sel if fid not in member_ids]
        result = (sel, [], lds)
        self._xform_targets_cache = (key, result)
        _T11_CNT["xform"] += 1
        return result

    panel_cls._xform_targets = ce_xform_targets
    log.info("[CE] T11 A2 已接入: _xform_targets → 去重 _ordered_selected_ids + 结果缓存")


def _bb_add(min_x, min_y, max_x, max_y, n_tgt, x, y, nx, ny):
    """拖动脏区逐项比较累加（与原版 translate/rotate 同式: NaN 不参与,
    min/max 同时覆盖旧+新位置）——返回新 bbox 状态。"""
    if x < min_x:
        min_x = x
    if y < min_y:
        min_y = y
    if nx < min_x:
        min_x = nx
    if ny < min_y:
        min_y = ny
    if x > max_x:
        max_x = x
    if y > max_y:
        max_y = y
    if nx > max_x:
        max_x = nx
    if ny > max_y:
        max_y = ny
    return min_x, min_y, max_x, max_y, n_tgt + 1


# ─────────────────────────────────────────────────────────────────────
#  A3: translate_current numpy 批量（逐元素同一 IEEE 表达式 → 位级等价）
#      ★ 72baed4 性能同步: 增量脏区重绘（defer 路径）+ 质心增量更新
# ─────────────────────────────────────────────────────────────────────
def _hook_translate_current(panel_cls, orig, tp_mod):
    def ce_translate_current(self, dx, dy, defer_ui=False):
        try:
            dx = float(dx)
            dy = float(dy)
        except Exception:
            return
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return
        page = self.current_page()
        fpos = page.setdefault("fixtures", {})
        vis = set(self.fixture_ids())
        # ── 拖动脏区: 目标 旧+新 位置 bbox（世界坐标; 与原版同式）──────
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n_tgt = 0
        # ── T11 批量平移: 仅可见集, 去重后批量（位级等价 + 回退原版循环）──
        fids_v = [fid for fid in fids if fid in vis]
        if fids_v and len(fids_v) == len(set(fids_v)):
            xs, ys, = _gather_positions(self, fids_v, fpos)
            if xs is not None:
                nxs = xs + dx
                nys = ys + dy
                # 批量 bbox（np.fmin/fmax.reduce 忽略 NaN, 与原版逐项比较一致）
                min_x = float(np.fmin.reduce(np.fmin(xs, nxs)))
                min_y = float(np.fmin.reduce(np.fmin(ys, nys)))
                max_x = float(np.fmax.reduce(np.fmax(xs, nxs)))
                max_y = float(np.fmax.reduce(np.fmax(ys, nys)))
                n_tgt = len(fids_v)
                for fid, nx, ny in zip(fids_v, nxs.tolist(), nys.tolist()):
                    fpos[fid] = {"x": nx, "y": ny}
                _T11_CNT["translate"] += 1
            else:
                for fid in fids_v:
                    x, y = self.fixture_pos(fid)
                    nx, ny = float(x) + dx, float(y) + dy
                    fpos[fid] = {"x": nx, "y": ny}
                    min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                        min_x, min_y, max_x, max_y, n_tgt, x, y, nx, ny)
        else:
            for fid in fids:
                if fid not in vis:
                    continue
                x, y = self.fixture_pos(fid)
                nx, ny = float(x) + dx, float(y) + dy
                fpos[fid] = {"x": nx, "y": ny}
                min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                    min_x, min_y, max_x, max_y, n_tgt, x, y, nx, ny)
        for shape in shapes:
            tp_mod._translate_shape(shape, dx, dy)
        fix_by_id = self._proj_fixture_map()
        for ld in lds:
            ld_ok = False
            try:
                nx = float(ld.get("pos_x", 0.0)) + dx
                ny = float(ld.get("pos_y", 0.0)) + dy
                ld["pos_x"], ld["pos_y"] = nx, ny
                ld_ok = True
            except Exception:
                pass
            if ld_ok:
                min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                    min_x, min_y, max_x, max_y, n_tgt, nx, ny, nx, ny)
            # local_offsets 是相对量，平移不改；成员真实坐标整体平移（与代表点同步）
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x = float(getattr(fi, "pos_x", 0.0)) + dx
                    fi.pos_y = float(getattr(fi, "pos_y", 0.0)) + dy
                except Exception:
                    continue
        self._pos_version = getattr(self, "_pos_version", 0) + 1
        # ★ 质心缓存增量更新（质心平移 = 平移到质心; 与原版同序列）→ gizmo O(1)
        self._xform_centroid_running_update(dx, dy)
        if defer_ui:
            self._canvas_update_dirty(min_x, min_y, max_x, max_y, n_tgt)
            return
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    panel_cls.translate_current = ce_translate_current
    log.info("[CE] T11 A3 已接入: translate_current → numpy 批量平移")


def _rotate_members(tp_mod, lds, fix_by_id, cx, cy, angle_deg, cos_a, sin_a):
    """成员真实坐标批量旋转（cos/sin 一次; 重复键或异常 → 回退逐台原版式）。
    纯语句搬移（原内联块）, 语义/计数不变。"""
    mem_objs = []
    mem_keys = []
    for ld in lds:
        for fid in ld.get("member_fixture_ids", []) or []:
            fi = fix_by_id.get(str(fid))
            if fi is not None:
                mem_objs.append(fi)
                mem_keys.append(fid)
    if mem_objs and len(set(mem_keys)) == len(mem_keys):
        try:
            mx = np.asarray([getattr(fi, "pos_x", 0.0) for fi in mem_objs], np.float64)
            my = np.asarray([getattr(fi, "pos_y", 0.0) for fi in mem_objs], np.float64)
            mdx = mx - cx
            mdy = my - cy
            mnx = cx + mdx * cos_a - mdy * sin_a
            mny = cy + mdx * sin_a + mdy * cos_a
            for fi, nx, ny in zip(mem_objs, mnx.tolist(), mny.tolist()):
                fi.pos_x = nx
                fi.pos_y = ny
            _T11_CNT["rotate_members"] += 1
            return
        except Exception:
            pass
    for ld in lds:
        for fid in ld.get("member_fixture_ids", []) or []:
            fi = fix_by_id.get(str(fid))
            if fi is None:
                continue
            try:
                fi.pos_x, fi.pos_y = tp_mod._rotate_point_xy(
                    float(getattr(fi, "pos_x", 0.0)),
                    float(getattr(fi, "pos_y", 0.0)), cx, cy, angle_deg)
            except Exception:
                continue


# ─────────────────────────────────────────────────────────────────────
#  A4: rotate_current numpy 批量（cos/sin 一次; 成员批量同式）
#      ★ 72baed4 性能同步: 增量脏区重绘（defer 路径）+ 质心增量更新
# ─────────────────────────────────────────────────────────────────────
def _hook_rotate_current(panel_cls, orig, tp_mod):
    def ce_rotate_current(self, angle_deg, center=None, defer_ui=False):
        try:
            angle_deg = float(angle_deg)
        except Exception:
            return
        if abs(angle_deg) < 1e-6:
            return
        scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return
        if center is None:
            center = self._xform_centroid(fids, shapes, lds)
        if center is None:
            return
        cx, cy = float(center[0]), float(center[1])
        page = self.current_page()
        fpos = page.setdefault("fixtures", {})
        vis = set(self.fixture_ids())
        # ── 拖动脏区: 目标 旧+新 位置 bbox（世界坐标; 与原版同式）──────
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n_tgt = 0
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        # ── T11 批量旋转: 逐元素同 _rotate_point_xy 表达式（位级等价）──
        fids_v = [fid for fid in fids if fid in vis]
        if fids_v and len(fids_v) == len(set(fids_v)):
            xs, ys, = _gather_positions(self, fids_v, fpos)
            if xs is not None:
                rdxs = xs - cx
                rdys = ys - cy
                rnxs = cx + rdxs * cos_a - rdys * sin_a
                rnys = cy + rdxs * sin_a + rdys * cos_a
                # 批量 bbox（np.fmin/fmax.reduce 忽略 NaN, 与原版逐项比较一致）
                min_x = float(np.fmin.reduce(np.fmin(xs, rnxs)))
                min_y = float(np.fmin.reduce(np.fmin(ys, rnys)))
                max_x = float(np.fmax.reduce(np.fmax(xs, rnxs)))
                max_y = float(np.fmax.reduce(np.fmax(ys, rnys)))
                n_tgt = len(fids_v)
                for fid, nx, ny in zip(fids_v, rnxs.tolist(), rnys.tolist()):
                    fpos[fid] = {"x": nx, "y": ny}
                _T11_CNT["rotate"] += 1
            else:
                for fid in fids_v:
                    x, y = self.fixture_pos(fid)
                    nx, ny = tp_mod._rotate_point_xy(float(x), float(y), cx, cy, angle_deg)
                    fpos[fid] = {"x": nx, "y": ny}
                    min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                        min_x, min_y, max_x, max_y, n_tgt, x, y, nx, ny)
        else:
            for fid in fids:
                if fid not in vis:
                    continue
                x, y = self.fixture_pos(fid)
                nx, ny = tp_mod._rotate_point_xy(float(x), float(y), cx, cy, angle_deg)
                fpos[fid] = {"x": nx, "y": ny}
                min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                    min_x, min_y, max_x, max_y, n_tgt, x, y, nx, ny)
        for shape in shapes:
            tp_mod._rotate_shape_about(shape, cx, cy, angle_deg)
        fix_by_id = self._proj_fixture_map()
        _rotate_members(tp_mod, lds, fix_by_id, cx, cy, angle_deg, cos_a, sin_a)
        for ld in lds:
            ld_ok = False
            try:
                nx, ny = tp_mod._rotate_point_xy(
                    float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0)),
                    cx, cy, angle_deg)
                ld["pos_x"], ld["pos_y"] = nx, ny
                ld_ok = True
            except Exception:
                pass
            if ld_ok:
                min_x, min_y, max_x, max_y, n_tgt = _bb_add(
                    min_x, min_y, max_x, max_y, n_tgt, nx, ny, nx, ny)
            # local_offsets 是相对向量：仅旋转方向，不平移
            offs = ld.get("local_offsets", {}) or {}
            for k in list(offs.keys()):
                try:
                    ox, oy = float(offs[k][0]), float(offs[k][1])
                    rx, ry = tp_mod._rotate_point_xy(ox, oy, 0.0, 0.0, angle_deg)
                    offs[k] = [rx, ry]
                except Exception:
                    continue
        self._pos_version = getattr(self, "_pos_version", 0) + 1
        # ★ 质心缓存增量更新（旋转线性作用于质心, 支点 = center; 与原版同序列）
        self._xform_centroid_running_update(0.0, 0.0, angle_deg, (cx, cy))
        if defer_ui:
            self._canvas_update_dirty(min_x, min_y, max_x, max_y, n_tgt)
            return
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    panel_cls.rotate_current = ce_rotate_current
    log.info("[CE] T11 A4 已接入: rotate_current → numpy 批量旋转 + 脏区/质心增量")


# ─────────────────────────────────────────────────────────────────────
#  B1: _selection_box_fixture_ids numpy 包含（Qt contains 语义逐位复刻）
#      Qt: 零宽/零高矩形恒 False; 否则 四边闭区间比较（实测 30 万样本）
# ─────────────────────────────────────────────────────────────────────
def _hook_selection_box_fixture_ids(canvas_cls, orig):
    def ce_selection_box_fixture_ids(self, start, end):
        """B1 加速（numpy 矩形包含，Qt contains 语义逐位复刻）+ 排序委托。

        R201/P10（用户 2026-10-06 两工程实报）：本挂钩旧版自带一套平行排序
        规则（单图形=拖动方向 x/y 序、未匹配=x/y 序）——真实应用挂 CE 后
        框选整圆被"与Y轴平行的平行线"横切，P10 的极角/设备序修复全部被
        绕过。现排序统一委托 `order_box_units` 单一真源（图形组极角序、
        组间/簇间按页面设备序），本函数只保留收集加速与单元构建。
        """
        from PyQt6.QtCore import QPointF, QRectF  # noqa: F401  (与原版同环境)
        rect = QRectF(start, end).normalized()
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        L, R = rect.left(), rect.right()
        T, B = rect.top(), rect.bottom()
        fids = self.panel.fixture_ids()
        fpos = self.panel.current_page().setdefault("fixtures", {})
        valid = self.panel.fixtures_by_id()

        # ── B1 收集加速：numpy 矩形包含（零宽/零高恒 False，与 Qt 同式）──
        contained = None
        xs, ys = _gather_positions(self.panel, fids, fpos)
        if xs is not None and (R - L) != 0.0 and (B - T) != 0.0:
            mask = (xs >= L) & (xs <= R) & (ys >= T) & (ys <= B)
            if mask.any():
                xl = xs.tolist()
                yl = ys.tolist()
                contained = {fid: (xl[i], yl[i])
                             for i, fid in enumerate(fids) if mask[i]}
        if contained is None:
            contained = {}
            for fid in fids:
                x, y = self.panel.fixture_pos(fid)
                if rect.contains(QPointF(x, y)):
                    contained[fid] = (float(x), float(y))

        # ── 单元构建：包=代表点在框内的原子单元（与 _page_units 同式）──
        from src.ui.topview_selection_order import _package_unit
        units = []
        package_members = set()
        for ld in self.panel.current_page_logical_devices():
            unit = _package_unit(ld, valid)
            if unit is None:
                continue
            if not rect.contains(QPointF(unit["x"], unit["y"])):
                continue
            units.append(unit)
            package_members.update(unit["ids"])
        for fid, (x, y) in contained.items():
            if str(fid) in package_members:
                continue
            units.append({"fid": str(fid), "ids": [str(fid)], "x": float(x), "y": float(y)})

        if len(units) <= 1:
            return [fid for unit in units for fid in unit["ids"]]

        from src.ui.topview_selection_order import order_box_units
        page_rank = {str(fid): i for i, fid in enumerate(self.panel._page_device_order())}
        units = order_box_units(self, units, dx, dy, page_rank)
        _T11_CNT["box"] += 1
        return [fid for unit in units for fid in unit["ids"]]

    canvas_cls._selection_box_fixture_ids = ce_selection_box_fixture_ids
    log.info("[CE] T11 B1 已接入: _selection_box_fixture_ids → numpy 包含")


# ─────────────────────────────────────────────────────────────────────
#  B2: _build_fixture_shape_map → ce_assign_shapes C 内核（距离/择优/阈值
#      S5 fuzz 位级一致）+ 原版 atan2 角度公式（保持未归一化原始角）
# ─────────────────────────────────────────────────────────────────────
def _hook_build_fixture_shape_map(canvas_cls, orig, engine):
    def ce_build_fixture_shape_map(self):
        shape_map = {}
        page = self.panel.current_page()
        # R201/P10：工程整圆环是样条（type=spline），样条按点集外接矩
        # 形拟合椭圆后与圆/椭圆同一 C 内核匹配，否则整页样条环 0 匹配。
        shapes = [s for s in (page.get("cad_shapes", []) or [])
                  if str(s.get("type", "")) in ("circle", "ellipse", "spline")]
        if not shapes:
            return shape_map

        geoms = []  # (shape_id, cx, cy, rx, ry)
        from src.ui.topview_selection_order import _spline_ellipse_geometry
        for s in shapes:
            if str(s.get("type", "")) == "spline":
                geometry = _spline_ellipse_geometry(s)
                if geometry is not None:
                    geoms.append(geometry)
                continue
            try:
                sid = str(s.get("id", ""))
                cx = float(s.get("cx"))
                cy = float(s.get("cy"))
                rx = abs(float(s.get("rx", s.get("r", 0.0))))
                ry = abs(float(s.get("ry", rx)))
            except Exception:
                continue
            if rx <= 1e-3:
                continue
            if ry <= 1e-3:
                ry = rx
            geoms.append((sid, cx, cy, rx, ry))

        if not geoms:
            return shape_map

        fids = self.panel.fixture_ids()
        fpos = page.setdefault("fixtures", {})
        xs, ys = _gather_positions(self.panel, fids, fpos)
        if xs is None:
            return orig(self)
        try:
            n_pts = len(fids)
            n_shapes = len(geoms)
            scx = np.fromiter((g[1] for g in geoms), np.float64, n_shapes)
            scy = np.fromiter((g[2] for g in geoms), np.float64, n_shapes)
            srx = np.fromiter((g[3] for g in geoms), np.float64, n_shapes)
            sry = np.fromiter((g[4] for g in geoms), np.float64, n_shapes)
            out_cx = np.zeros(n_shapes, np.float64)
            out_cy = np.zeros(n_shapes, np.float64)
            out_r = np.zeros(n_shapes, np.float64)
            out_cnt = np.zeros(n_shapes, np.int32)
            out_midx = np.zeros(max(1, n_pts), np.int32)
            out_mang = np.zeros(max(1, n_pts), np.float64)
            import ctypes
            _DP = ctypes.POINTER(ctypes.c_double)
            _IP32 = ctypes.POINTER(ctypes.c_int32)
            ng = engine.ce_assign_shapes(
                xs.ctypes.data_as(_DP), ys.ctypes.data_as(_DP), n_pts,
                scx.ctypes.data_as(_DP), scy.ctypes.data_as(_DP),
                srx.ctypes.data_as(_DP), sry.ctypes.data_as(_DP), n_shapes,
                out_cx.ctypes.data_as(_DP), out_cy.ctypes.data_as(_DP),
                out_r.ctypes.data_as(_DP), out_cnt.ctypes.data_as(_IP32),
                out_midx.ctypes.data_as(_IP32), out_mang.ctypes.data_as(_DP))
            if ng < 0:
                return orig(self)
            member_total = 0
            xl = xs.tolist()
            yl = ys.tolist()
            for i in range(ng):
                sid = geoms[i][0]
                if not sid:
                    # 原版: 空 id 形状参与距离竞争但结果不入图
                    member_total += int(out_cnt[i])
                    continue
                _, cx, cy, rx, ry = geoms[i]
                cnt = int(out_cnt[i])
                for t in range(cnt):
                    p = int(out_midx[member_total + t])
                    x = float(xl[p])
                    y = float(yl[p])
                    ang = math.atan2((y - cy) / max(ry, 1e-6),
                                     (x - cx) / max(rx, 1e-6))
                    shape_map[str(fids[p])] = (sid, float(ang))
                member_total += cnt
            _T11_CNT["shape_map"] += 1
        except Exception:
            return orig(self)
        return shape_map

    canvas_cls._build_fixture_shape_map = ce_build_fixture_shape_map
    log.info("[CE] T11 B2 已接入: _build_fixture_shape_map → ce_assign_shapes")


# ─────────────────────────────────────────────────────────────────────
#  B3: _ensure_fixture_hover_index 批量取坐标（原版循环体/异常语义保留）
# ─────────────────────────────────────────────────────────────────────
def _hook_ensure_fixture_hover_index(canvas_cls, orig):
    def ce_ensure_fixture_hover_index(self):
        key = (id(self.panel.current_page()), getattr(self.panel, "_pos_version", 0))
        if getattr(self, "_hover_index_key", None) == key and getattr(self, "_hover_index", None) is not None:
            return self._hover_index
        fids = self.panel.fixture_ids()
        fpos = self.panel.current_page().setdefault("fixtures", {})
        xs, ys = _gather_positions(self.panel, fids, fpos)
        idx: dict = {}
        if xs is not None:
            cell = 200.0
            xl = xs.tolist()
            yl = ys.tolist()
            for fid, x, y in zip(fids, xl, yl):
                # int() 不在 try 内——与原版一致（NaN 时同样向外抛）
                idx.setdefault((int(x / cell), int(y / cell)), []).append(fid)
            self._hover_index = idx
            self._hover_index_key = key
            _T11_CNT["hover"] += 1
            return idx
        return orig(self)

    canvas_cls._ensure_fixture_hover_index = ce_ensure_fixture_hover_index
    log.info("[CE] T11 B3 已接入: _ensure_fixture_hover_index → 批量取坐标")


# ─────────────────────────────────────────────────────────────────────
#  B4: _build_spatial_index 批量取坐标
# ─────────────────────────────────────────────────────────────────────
def _hook_build_spatial_index(canvas_cls, orig):
    def ce_build_spatial_index(self):
        fids = self.panel.fixture_ids()
        fpos = self.panel.current_page().setdefault("fixtures", {})
        xs, ys = _gather_positions(self.panel, fids, fpos)
        if xs is not None:
            self._spatial_index.rebuild(
                zip(fids, xs.tolist(), ys.tolist()))
            _T11_CNT["spatial"] += 1
        else:
            orig(self)
        self._spatial_index_dirty = False

    canvas_cls._build_spatial_index = ce_build_spatial_index
    log.info("[CE] T11 B4 已接入: _build_spatial_index → 批量取坐标")


# ─────────────────────────────────────────────────────────────────────
#  C1: 对称性 O(N²) → 排序 + searchsorted 窗口（布尔结论与原版逐位一致:
#      窗口为超集（相对 1e-6 余量覆盖除法/减法舍入）, 窗内逐候选用
#      原版同一比较式 abs(p - mirror) < tolerance 复核）
# ─────────────────────────────────────────────────────────────────────
def _ce_symmetry_pass(xs, ys, mirror, tol, by_x):
    """按 by_x 轴排序开窗, 对每点判另一轴有无镜像命中。"""
    n = len(xs)
    if by_x:
        order = np.argsort(xs, kind="stable")
        axis_s = xs[order]
        cross_s = ys[order]
        cross_i = ys
    else:
        order = np.argsort(ys, kind="stable")
        axis_s = ys[order]
        cross_s = xs[order]
        cross_i = xs
    for i in range(n):
        m = mirror[i]
        margin = 1e-6 * (abs(float(m)) + abs(float(tol)) + 1.0)
        lo = int(np.searchsorted(axis_s, m - tol - margin, side="left"))
        hi = int(np.searchsorted(axis_s, m + tol + margin, side="right"))
        if lo >= hi:
            return False
        w = axis_s[lo:hi]
        d = w - m
        okm = (d > -tol) & (d < tol)
        if not okm.any():
            return False
        wy = cross_s[lo:hi][okm]
        dy = wy - cross_i[i]
        if not ((dy > -tol) & (dy < tol)).any():
            return False
    return True


def _hook_detect_symmetry(det_cls, orig_x, orig_y):
    def ce_detect_x_symmetry(self, norm_positions, cx, tolerance=0.1):
        try:
            xs = np.asarray([p[0] for p in norm_positions], np.float64)
            ys = np.asarray([p[1] for p in norm_positions], np.float64)
        except Exception:
            return orig_x(self, norm_positions, cx, tolerance)
        n = len(xs)
        if n == 0 or not (np.isfinite(xs).all() and np.isfinite(ys).all()):
            return orig_x(self, norm_positions, cx, tolerance)
        mirror = 2.0 * cx - xs
        _T11_CNT["sym_x"] += 1
        return _ce_symmetry_pass(xs, ys, mirror, float(tolerance), True)

    def ce_detect_y_symmetry(self, norm_positions, cy, tolerance=0.1):
        try:
            xs = np.asarray([p[0] for p in norm_positions], np.float64)
            ys = np.asarray([p[1] for p in norm_positions], np.float64)
        except Exception:
            return orig_y(self, norm_positions, cy, tolerance)
        n = len(xs)
        if n == 0 or not (np.isfinite(xs).all() and np.isfinite(ys).all()):
            return orig_y(self, norm_positions, cy, tolerance)
        mirror = 2.0 * cy - ys
        _T11_CNT["sym_y"] += 1
        return _ce_symmetry_pass(xs, ys, mirror, float(tolerance), False)

    det_cls._detect_x_symmetry = ce_detect_x_symmetry
    det_cls._detect_y_symmetry = ce_detect_y_symmetry
    log.info("[CE] T11 C1 已接入: _detect_x/y_symmetry → 窗口搜索 O(N log N)")


# ─────────────────────────────────────────────────────────────────────
#  C2: _simple_cluster O(N²) BFS → 网格候选 + 并查集
#      （原版输出 = 距离<阈值无向图的连通分量, 按最小索引升序, 簇内索引
#        升序 —— 与遍历顺序无关; 边判定逐对同式: np.sqrt == math.sqrt
#        fuzz 已验证, dx*dx+dy*dy 同 IEEE 序列 → 逐对位级等价）
# ─────────────────────────────────────────────────────────────────────
def _hook_simple_cluster(det_cls, orig):
    def ce_simple_cluster(self, norm_positions, threshold=0.25):
        n = len(norm_positions)
        if n == 0:
            return []
        try:
            xs = np.asarray([p[0] for p in norm_positions], np.float64)
            ys = np.asarray([p[1] for p in norm_positions], np.float64)
        except Exception:
            return orig(self, norm_positions, threshold)
        if not (np.isfinite(xs).all() and np.isfinite(ys).all()):
            return orig(self, norm_positions, threshold)
        # 巨值域: 除法舍入使 3×3 网格窗口不再是可靠超集 → 原版
        if float(np.abs(xs).max(initial=0.0)) > 1e12 or float(np.abs(ys).max(initial=0.0)) > 1e12:
            return orig(self, norm_positions, threshold)
        tol = float(threshold)
        cell = float(tol)
        gx = np.floor(xs / cell).astype(np.int64)
        gy = np.floor(ys / cell).astype(np.int64)
        grid: dict = {}
        for i in range(n):
            grid.setdefault((int(gx[i]), int(gy[i])), []).append(i)
        parent = list(range(n))

        def _find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in range(n):
            bx = int(gx[i])
            by = int(gy[i])
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    lst = grid.get((bx + ddx, by + ddy))
                    if not lst:
                        continue
                    for j in lst:
                        if j <= i:
                            continue
                        dxv = xs[i] - xs[j]
                        dyv = ys[i] - ys[j]
                        if np.sqrt(dxv * dxv + dyv * dyv) < tol:
                            ri = _find(i)
                            rj = _find(j)
                            if ri != rj:
                                parent[ri] = rj
        comp: dict = {}
        for i in range(n):
            comp.setdefault(_find(i), []).append(i)
        clusters = [sorted(v) for v in comp.values()]
        clusters.sort(key=lambda c: c[0])
        _T11_CNT["cluster"] += 1
        return clusters

    det_cls._simple_cluster = ce_simple_cluster
    log.info("[CE] T11 C2 已接入: _simple_cluster → 网格 + 并查集")


# ─────────────────────────────────────────────────────────────────────
#  安装入口（QASCADE_CE_T11=1 才生效; 任何模块导入失败 → 跳过, 原版不变）
# ─────────────────────────────────────────────────────────────────────
def install_t11(engine):
    global _INSTALLED
    if _INSTALLED:
        return
    if os.environ.get("QASCADE_CE_T11", "1") != "1":
        return
    try:
        from src.ui import topview_panel as tp
        from src.engine.deep_aqua_core import topview_reader as tvr

        _hook_ordered_selected_ids(tp.TopViewPanel, tp.TopViewPanel._ordered_selected_ids)
        _hook_xform_targets(tp.TopViewPanel, tp.TopViewPanel._xform_targets)
        _hook_translate_current(tp.TopViewPanel, tp.TopViewPanel.translate_current, tp)
        _hook_rotate_current(tp.TopViewPanel, tp.TopViewPanel.rotate_current, tp)
        _hook_selection_box_fixture_ids(
            tp.TopViewCanvas, tp.TopViewCanvas._selection_box_fixture_ids)
        _hook_build_fixture_shape_map(
            tp.TopViewCanvas, tp.TopViewCanvas._build_fixture_shape_map, engine)
        _hook_ensure_fixture_hover_index(
            tp.TopViewCanvas, tp.TopViewCanvas._ensure_fixture_hover_index)
        _hook_build_spatial_index(
            tp.TopViewCanvas, tp.TopViewCanvas._build_spatial_index)
        _hook_detect_symmetry(
            tvr.TopologyDetector,
            tvr.TopologyDetector._detect_x_symmetry,
            tvr.TopologyDetector._detect_y_symmetry)
        _hook_simple_cluster(tvr.TopologyDetector, tvr.TopologyDetector._simple_cluster)
        _INSTALLED = True
        log.info("[CE] T11 已接入: TopView 交互几何批量/C 路径 (%d 个替换点)", 10)
    except Exception as e:  # noqa: BLE001 — 隔离测试版: 任何失败回退原版
        log.info("[CE] T11 安装失败, 保持原版: %s", e)
