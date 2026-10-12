# -*- coding: utf-8 -*-
"""TopView 画布 PNG 导出（R183/F2 · 深查③ T8）。

全文件原无 `grab()`/`saveImage` 命中 —— 页面内导出图片完全缺失。本模块用
Qt 内建 `QWidget.grab()`（零依赖）把画布存 PNG：

  * `include_rulers=False`（默认）：只抓画布本体，位图干净可直接给施工方；
  * `include_rulers=True`：抓图前把标尺/参考线覆盖层/小地图抬到最上层
    （它们是画布的**兄弟**控件与子控件，故需 composite —— 见 `compose_with_chrome`）；
  * `crop_to_content=True`：按当前页设备的包围盒 + 边距裁剪。

`TopViewPanel`/`TopViewCanvas` 是冻结上帝类，本模块由
  `topview_overlays.install_overlays` 注入调用，面板源码零改动。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap

#: 裁剪边距（世界单位）——灯位图留白，避免设备贴边。
CROP_PAD = 40.0


def export_canvas_png(panel, path: str, *, include_rulers: bool = False,
                      crop_to_content: bool = False) -> bool:
    """把 TopView 画布存为 PNG。返回是否成功落盘。"""
    canvas = getattr(panel, "_canvas", None)
    if canvas is None:
        return False
    try:
        canvas.repaint()
        pix = canvas.grab()
        if include_rulers:
            pix = compose_with_chrome(panel, pix)
        if crop_to_content:
            pix = crop_to_content_pixmap(canvas, pix)
        if pix is None or pix.isNull():
            return False
        out = str(path)
        if not out.lower().endswith(".png"):
            out += ".png"
        return bool(pix.save(out, "PNG"))
    except Exception:
        return False


def compose_with_chrome(panel, canvas_pix: QPixmap) -> QPixmap:
    """把画布抓图与画布外的标尺拼成一张（标尺在画布栅格之外，抓画布抓不到）。"""
    canvas = getattr(panel, "_canvas", None)
    top = getattr(canvas, "_top_ruler", None) if canvas is not None else None
    left = getattr(canvas, "_left_ruler", None) if canvas is not None else None
    if canvas is None or (top is None and left is None):
        return canvas_pix
    tw = top.width() if top is not None else 0
    th = top.height() if top is not None else 0
    lw = left.width() if left is not None else 0
    lh = left.height() if left is not None else 0
    out = QPixmap(canvas_pix.width() + lw, canvas_pix.height() + th)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    try:
        if top is not None:
            p.drawPixmap(QPoint(lw, 0), top.grab())
        if left is not None:
            p.drawPixmap(QPoint(0, th), left.grab())
        p.drawPixmap(QPoint(lw, th), canvas_pix)
        _draw_guides(panel, p, lw, th)
    finally:
        p.end()
    del tw, lh
    return out


def _draw_guides(panel, painter: QPainter, dx: int, dy: int) -> None:
    """在合成图上补画参考线（覆盖层是画布子控件，抓画布时可能未被 composite）。"""
    canvas = getattr(panel, "_canvas", None)
    overlay = getattr(canvas, "_guide_overlay", None) if canvas is not None else None
    if overlay is None:
        return
    try:
        painter.drawPixmap(QPoint(dx, dy), overlay.grab())
    except Exception:
        pass


def crop_to_content_pixmap(canvas, pix: QPixmap) -> QPixmap:
    """按当前页设备包围盒 + 边距裁剪（无设备 → 原图）。"""
    centers = _fixture_points(canvas.panel)
    if not centers:
        return pix
    xs = [p[0] for p in centers]
    ys = [p[1] for p in centers]
    tl = canvas._world_to_screen(QPointF(min(xs) - CROP_PAD, min(ys) - CROP_PAD))
    br = canvas._world_to_screen(QPointF(max(xs) + CROP_PAD, max(ys) + CROP_PAD))
    full = QRectF(0.0, 0.0, float(canvas.width()), float(canvas.height())).toAlignedRect()
    rect = QRectF(tl, br).normalized().toAlignedRect().intersected(full)
    if rect.width() < 8 or rect.height() < 8:
        return pix
    out = QPixmap(rect.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    try:
        p.drawPixmap(QPoint(0, 0), pix, rect)
    finally:
        p.end()
    return out


def _fixture_points(panel) -> list:
    try:
        ids = panel.fixture_ids()
    except Exception:
        return []
    pts = []
    for fid in ids:
        try:
            pts.append(panel.fixture_pos(str(fid)))
        except Exception:
            continue
    return pts


def png_export_slot(panel) -> None:
    """工具条按钮回调：文件对话框 + 导出 + 状态提示。"""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from .table_io_fallback import notify_user
    canvas = getattr(panel, "_canvas", None)
    if canvas is None:
        return
    default = _default_name(panel)
    path, _ = QFileDialog.getSaveFileName(
        panel, "导出 TopView 画布为 PNG", default, "PNG 图片 (*.png)")
    if not path:
        return
    if export_canvas_png(panel, path,
                         include_rulers=bool(getattr(panel, "_png_include_rulers", True))):
        notify_user(panel, "TopView 已导出 PNG", str(path), kind="success")
    else:
        QMessageBox.warning(panel, "导出失败",
                            "无法生成 PNG 图片（画布不可用或路径不可写）。")


def _default_name(panel) -> str:
    try:
        name = str(panel.current_page().get("name") or "topview").strip()
    except Exception:
        name = "topview"
    return f"{name or 'topview'}.png"
