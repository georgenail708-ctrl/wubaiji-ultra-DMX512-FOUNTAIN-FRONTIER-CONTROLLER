# -*- coding: utf-8 -*-
"""matrix_grid_detect.py — 贴片网格自动检测（深查②三.Matrix #9 P1）。

输入设备归一化坐标点集，按 **1 维聚类**（排序后按间距阈值切分）分别求出
列簇与行簇 → 网格 W×H + 每台设备的 (行,列) 号 → 可选把坐标**吸附**到
规整网格（新工程零手工）。

纯 numpy、无 Qt（确认弹窗在 `detect_and_apply_grid` 里延迟导入）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _cluster_1d(values: Sequence[float], tol_scale: float = 0.5) -> Tuple[List[float], np.ndarray]:
    """一维聚类：排序后相邻间距 > 阈值 即切新簇。

    阈值 = 相邻唯一值间距的中位数 × tol_scale（兜底最大间距 × 0.02）。
    返回 (每簇中心列表, 每个输入值所属簇号)。
    """
    vals = np.asarray([float(v) for v in values], dtype=np.float64)
    n = vals.size
    if n == 0:
        return [], np.zeros(0, dtype=np.int32)
    uniq = np.unique(vals)
    if uniq.size == 1:
        return [float(uniq[0])], np.zeros(n, dtype=np.int32)
    gaps = np.diff(uniq)
    med = float(np.median(gaps))
    thr = max(med * tol_scale, float(gaps.max()) * 0.02, 1e-6)
    centers: List[float] = [float(uniq[0])]
    key_to_cluster: Dict[float, int] = {float(uniq[0]): 0}
    cid = 0
    for v in uniq[1:]:
        fv = float(v)
        if fv - centers[-1] > thr:
            cid += 1
            centers.append(fv)
        key_to_cluster[fv] = cid
    idx = np.asarray([key_to_cluster[float(v)] for v in vals], dtype=np.int32)
    return centers, idx


def detect_grid(points: Sequence[Tuple[float, float]]) -> Optional[Dict]:
    """坐标点集 → 网格检测结果 dict（不足 2 点/退化为 1×1 时返回 None）。

    返回::

        {
          "cols": int, "rows": int,
          "col_idx": np.ndarray, "row_idx": np.ndarray,   # 与输入同序
          "xs": List[float], "ys": List[float],           # 簇中心（原坐标）
        }
    """
    if not points or len(points) < 2:
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    x_centers, col_idx = _cluster_1d(xs)
    y_centers, row_idx = _cluster_1d(ys)
    cols = max(1, len(x_centers))
    rows = max(1, len(y_centers))
    if cols * rows < len(points) or (cols <= 1 and rows <= 1):
        # 全部点挤在一簇（如所有坐标相同）→ 无网格意义
        if cols <= 1 and rows <= 1:
            return None
    return {
        "cols": cols,
        "rows": rows,
        "col_idx": col_idx,
        "row_idx": row_idx,
        "xs": x_centers,
        "ys": y_centers,
    }


def snap_positions(fixtures: Sequence[Dict], result: Dict) -> List[Dict]:
    """按检测结果把设备 pos_x/pos_y 吸附到规整网格（返回新 fixture 列表拷贝）。"""
    cols = max(1, int(result.get("cols", 1)))
    rows = max(1, int(result.get("rows", 1)))
    col_idx = result.get("col_idx")
    row_idx = result.get("row_idx")
    out: List[Dict] = []
    for i, f in enumerate(fixtures or []):
        g = dict(f)
        try:
            c = int(col_idx[i]) if col_idx is not None and i < len(col_idx) else 0
            r = int(row_idx[i]) if row_idx is not None and i < len(row_idx) else 0
        except (TypeError, ValueError):
            c = r = 0
        nx = 0.5 if cols <= 1 else c / float(cols - 1)
        ny = 0.5 if rows <= 1 else r / float(rows - 1)
        g["pos_x"] = nx
        g["pos_y"] = ny
        out.append(g)
    return out


def _ask_snap(panel, cols: int, rows: int, n: int) -> bool:
    """确认对话框：是否吸附坐标（helper；返回 True=用户选择吸附）。"""
    from PyQt6.QtWidgets import QMessageBox
    box = QMessageBox(panel)
    box.setWindowTitle("网格检测")
    box.setText(
        f"检测到网格：{cols} 列 × {rows} 行（{n} 台设备）。\n\n"
        "「吸附坐标」会把设备 pos_x/pos_y 重写为规整网格位置"
        "（采样/跑泉方向更整齐）。")
    snap_btn = box.addButton("吸附坐标", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is snap_btn


def _apply_snapped(panel, snapped) -> None:
    """把吸附后的设备列表回写画布/像素引擎并置脏（helper）。"""
    panel._fixtures = snapped
    canvas = getattr(panel, "_canvas", None)
    if canvas is not None:
        try:
            canvas.set_fixtures(snapped)
        except Exception:
            logger.debug("matrix: canvas rebind after snap failed", exc_info=True)
    px = getattr(panel, "_pixel_engine", None)
    if px is not None:
        try:
            px.set_fixtures(snapped)
        except Exception:
            logger.debug("matrix: pixel engine rebind after snap failed", exc_info=True)
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)
    except Exception:
        logger.debug("matrix: mark dirty after grid snap failed", exc_info=True)


def detect_and_apply_grid(panel) -> Optional[Dict]:
    """面板交互入口：检测当前设备组的网格，可选拾附坐标。

    返回检测结果 dict（取消/失败返回 None）。
    """
    from PyQt6.QtWidgets import QMessageBox
    fixtures = list(getattr(panel, "_fixtures", []) or [])
    points = [(f.get("pos_x", 0.5), f.get("pos_y", 0.5)) for f in fixtures]
    result = detect_grid(points)
    if result is None:
        QMessageBox.information(
            panel, "网格检测",
            "无法检测出网格：设备少于 2 台，或所有坐标重合。\n"
            "设备需要按行列排布（坐标成簇）才能自动识别。")
        return None
    if not _ask_snap(panel, result["cols"], result["rows"], len(fixtures)):
        return None
    _apply_snapped(panel, snap_positions(fixtures, result))
    return result
