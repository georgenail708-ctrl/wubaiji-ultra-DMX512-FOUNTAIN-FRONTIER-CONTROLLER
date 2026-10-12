# -*- coding: utf-8 -*-
"""画布总览管线（R183 W2-ST #5 小地图 / #13 缩放指示 / #16 sheet 缩略图）—— 无副作用绘制。

**为什么不用 `QWidget.grab()` 降采样**（先搜后写结论，含实测理由）：

* 小地图要画的是**整图 + 视口框**；`grab()` 抓到的只是当前视口画面 —— 视口框
 恒等于整张小地图，「视口矩形同步」失去意义；
* 抓拍前临时 hide 会带来 200ms 一次的可见闪烁；且小地图作为画布子控件时
 `grab()` 自指递归。
* 本仓画布是单 QWidget 自绘、sheet 是纯 dict —— 用与绘制同源的世界坐标数据画
 「降采样总览」（框 / 边 / 卡 chip）比抓拍更便宜、无副作用，并且**同一管线**
 直接服务 sheet 缩略图条（非 active 页没有活画布可抓）。

**缓存纪律（关键）**：小地图把总览画进一张 `QPixmap` 缓存，只在 200ms 节流定时器
到点（`refresh_now`）时重建 —— 逐像素回归测试（`test_r182_studio_visual` 的拖动
残影扫描）取整帧 `grab()`，若每帧都重算总览就会在拖动测试里引入"小地图区域
像素变化但不在脏区"的假失败。缓存后拖动期间总览保持不变，测试语义不受扰动。
"""
from __future__ import annotations
from src.ui.hidpi import hidpi_pixmap

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QWidget

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_cards import GROUP_COLORS
from src.ui.clip_studio.studio_frames import frame_rect
from src.ui.clip_studio.studio_note import NOTE_FILL, is_note

__all__ = ["MINI_W", "MINI_H", "MINI_MARGIN", "THUMB_W", "THUMB_H", "OVERVIEW_REFRESH_MS",
      "world_bounds", "fit_transform", "world_to_px", "px_to_world", "view_rect_px",
      "node_boxes", "paint_sheet_overview", "render_overview_pixmap",
      "MinimapWidget", "ZoomLabel"]

#: 角落小地图尺寸（R183 任务书：160×100）。
MINI_W, MINI_H = 160, 100
#: 小地图距画布右/下边距（屏幕像素）。
MINI_MARGIN = 12
#: sheet 缩略图尺寸（80px 宽档）。
THUMB_W, THUMB_H = 80, 50
#: 刷新节流（任务书：拖动/改动 200ms）。
OVERVIEW_REFRESH_MS = 200

_MIN_PAD = 4.0      # 总览四周留白（缩略图坐标）
_EMPTY_GAP = 60.0     # 空图兜底世界跨度（避免零除）
_CHIP_ALPHA = 200
_EDGE_ALPHA = 90


def _card_size() -> tuple:
  """卡尺寸唯一真源在 `studio_canvas`（函数内 import 断模块级循环）。"""
  from src.ui.clip_studio.studio_canvas import CARD_H, CARD_W
  return float(CARD_W), float(CARD_H)


def node_boxes(nodes) -> list:
  """可见节点 → 卡矩形表 ``[(x, y, w, h), …]``（世界坐标；折叠子卡剔除）。"""
  from src.ui.clip_studio.studio_collapse import hidden_ids
  w, h = _card_size()
  hidden = hidden_ids(nodes)
  out = []
  for n in nodes or ():
    if not isinstance(n, dict) or str(n.get("id")) in hidden:
      continue
    try:
      cx, cy = float(n.get("x", 0.0)), float(n.get("y", 0.0))
    except (TypeError, ValueError):
      continue
    out.append((cx - w / 2.0, cy - h / 2.0, w, h))
  return out


def world_bounds(sheet) -> tuple | None:
  """整图世界包围盒 ``(x0, y0, x1, y1)``（卡 + 分组框）；空图 → None。"""
  graph = (sheet or {}).get("graph", {}) if isinstance(sheet, dict) else {}
  graph = graph if isinstance(graph, dict) else {}
  boxes = list(node_boxes(graph.get("nodes") or []))
  for f in graph.get("frames") or []:
    if isinstance(f, dict):
      x, y, w, h = frame_rect(f)
      boxes.append((x, y, w, h))
  if not boxes:
    return None
  x0 = min(b[0] for b in boxes)
  y0 = min(b[1] for b in boxes)
  x1 = max(b[0] + b[2] for b in boxes)
  y1 = max(b[1] + b[3] for b in boxes)
  return x0, y0, max(x1, x0 + _EMPTY_GAP), max(y1, y0 + _EMPTY_GAP)


#: C6：取景四周留白（占跨度比例）/ 最小取景跨度（卡片数）
_VIEW_MARGIN = 0.12
_MIN_SPAN_CARDS = 3.0


def _grow_span(a: float, b: float, minimum: float) -> tuple:
  """区间 [a, b] 居中扩到至少 minimum 长。"""
  extra = max(0.0, minimum - (b - a)) / 2.0
  return a - extra, b + extra


def _overview_bounds(bounds, view_world=None) -> tuple:
  """总览取景 = 内容包围盒 ∪ 当前视口，最小跨度 3 张卡，四周留 12%。

  C6 修（截图 13-19-47「小地图纯蓝块」）：修前只取卡片包围盒 → 单卡时那张卡被
  放大到填满小地图；视口框反而比取景大、被裁出图外，小地图失去"我在哪"的作用。
  （/ UE 节点编辑器小地图同为「内容 ∪ 视口」取景。）
  """
  x0, y0, x1, y1 = (float(v) for v in bounds)
  view = _rect_tuple(view_world)
  if view is not None:
    x0, y0 = min(x0, view[0]), min(y0, view[1])
    x1, y1 = max(x1, view[0] + view[2]), max(y1, view[1] + view[3])
  cw, ch = _card_size()
  x0, x1 = _grow_span(x0, x1, cw * _MIN_SPAN_CARDS)
  y0, y1 = _grow_span(y0, y1, ch * _MIN_SPAN_CARDS)
  m = max(x1 - x0, y1 - y0) * _VIEW_MARGIN
  return x0 - m, y0 - m, x1 + m, y1 + m


def fit_transform(bounds, out_w: float, out_h: float, pad: float = _MIN_PAD) -> tuple:
  """世界包围盒 → 等比内接变换 ``(scale, ox, oy)``：``px = world * scale + o``。

  包围盒为空/退化 → 以 ``(0,0,1,1)`` 兜底（调用方通常先查 `world_bounds`）。
  """
  if not bounds:
    bounds = (0.0, 0.0, 1.0, 1.0)
  x0, y0, x1, y1 = (float(v) for v in bounds)
  bw = max(1e-6, x1 - x0)
  bh = max(1e-6, y1 - y0)
  scale = min(max(1e-6, (float(out_w) - 2 * pad)) / bw,
        max(1e-6, (float(out_h) - 2 * pad)) / bh)
  ox = (float(out_w) - bw * scale) * 0.5 - x0 * scale
  oy = (float(out_h) - bh * scale) * 0.5 - y0 * scale
  return scale, ox, oy


def world_to_px(tf, x: float, y: float) -> tuple:
  """世界点 → 缩略图像素点（`tf` = `fit_transform` 结果）。"""
  scale, ox, oy = tf
  return ox + float(x) * scale, oy + float(y) * scale


def px_to_world(tf, px: float, py: float) -> tuple:
  """缩略图像素点 → 世界点（导航反解）。"""
  scale, ox, oy = tf
  return (float(px) - ox) / scale, (float(py) - oy) / scale


def _rect_tuple(rect) -> tuple | None:
  """QRectF/QRect/4 元组 → ``(x, y, w, h)``；不可用 → None。"""
  if rect is None:
    return None
  try:
    if hasattr(rect, "width"):
      return float(rect.left()), float(rect.top()), float(rect.width()), float(rect.height())
    return float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
  except (TypeError, ValueError, IndexError):
    return None


def view_rect_px(tf, rect) -> tuple | None:
  """视口世界矩形 → 缩略图像素矩形 ``(x, y, w, h)``（未给/畸形 → None）。"""
  box = _rect_tuple(rect)
  if box is None:
    return None
  px, py = world_to_px(tf, box[0], box[1])
  return px, py, max(2.0, box[2] * tf[0]), max(2.0, box[3] * tf[0])


def _chip_color(node) -> QColor:
  """卡 chip 颜色：便签用贴纸色；其余按效果大类组色（未知回退 BORDER_NORMAL）。"""
  if is_note(node):
    base = QColor(NOTE_FILL)
  else:
    from src.ui.clip_studio.studio_graph import node_group
    base = QColor(GROUP_COLORS.get(node_group(node), "") or _th.BORDER_NORMAL)
  base.setAlpha(_CHIP_ALPHA)
  return base


def _visible_nodes(graph) -> list:
  """可见节点（与 `node_boxes` 同一折叠判据；两处顺序一致 = zip 可对齐）。"""
  from src.ui.clip_studio.studio_collapse import hidden_ids
  nodes = [n for n in graph.get("nodes") or [] if isinstance(n, dict)]
  hidden = hidden_ids(nodes)
  return [n for n in nodes if str(n.get("id")) not in hidden]


def overview_transform(sheet, width, height, view_world=None):
  """R194/R4-02：总览变换唯一实现点（渲染与点击导航必须共用）。

  `_overview_bounds` 含 3 卡最小跨度+12% 边距，恒大于裸内容包围盒——
  此前导航用裸 bounds 反解，偏心点击落点系统性偏向中心（测试只点正中心
  恰好重合，掩盖了偏差）。返回 None = 空表。
  """
  bounds = world_bounds(sheet)
  if bounds is None:
    return None
  return fit_transform(_overview_bounds(bounds, view_world), width, height)


def paint_sheet_overview(p: QPainter, rect, sheet, view_world=None, *,
             radius: float = 2.0) -> None:
  """整图总览（世界→`rect` 降采样）：框 → 边 → 卡 chip（+ 视口框）。

  `rect` = QRectF（缩略图区域）；`view_world` = 当前视口世界矩形（可选画蓝框）。
  小地图与 sheet 缩略图共用本函数（唯一实现点）。
  """
  r = QRectF(rect)
  p.save()
  p.setClipRect(r)
  p.fillRect(r, QColor(_th.BG_PANEL_SUB))
  bounds = world_bounds(sheet)
  if bounds is None:
    p.setPen(QPen(QColor(_th.TEXT_DISABLED)))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "空")
    p.restore()
    return
  tf = overview_transform(sheet, r.width(), r.height(), view_world)  # C6（R194/R4-02 唯一实现点）
  p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
  graph = (sheet or {}).get("graph", {}) if isinstance(sheet, dict) else {}
  graph = graph if isinstance(graph, dict) else {}
  for f in graph.get("frames") or []:
    if not isinstance(f, dict):
      continue
    x, y, w, h = frame_rect(f)
    x0, y0 = world_to_px(tf, x, y)
    col = QColor(_th.BG_BUTTON)
    col.setAlpha(70)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(col))
    p.drawRoundedRect(QRectF(r.left() + x0, r.top() + y0, w * tf[0], h * tf[0]),
             radius, radius)
  _paint_overview_edges(p, r, tf, graph)
  p.setPen(Qt.PenStyle.NoPen)
  for node, box in zip(_visible_nodes(graph), node_boxes(graph.get("nodes"))):
    x0, y0 = world_to_px(tf, box[0], box[1])
    p.setBrush(QBrush(_chip_color(node)))
    p.drawRoundedRect(QRectF(r.left() + x0, r.top() + y0,
                 max(2.0, box[2] * tf[0]), max(2.0, box[3] * tf[0])),
             radius, radius)
  box = view_rect_px(tf, view_world)
  if box is not None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(_th.CANVAS_SELECTION), 1.0))
    p.drawRect(QRectF(r.left() + box[0], r.top() + box[1], box[2], box[3]))
  p.restore()


def _paint_overview_edges(p: QPainter, r: QRectF, tf, graph) -> None:
  """边 → 缩略图直线（总览只需走向；端点缺失/隐藏 → 跳过）。"""
  from src.ui.clip_studio.studio_collapse import hidden_ids
  nodes = [n for n in graph.get("nodes") or [] if isinstance(n, dict)]
  by_id = {str(n.get("id")): n for n in nodes}
  hidden = hidden_ids(nodes)
  p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1.0))
  for e in graph.get("edges") or []:
    if not isinstance(e, dict):
      continue
    src, dst = by_id.get(str(e.get("from"))), by_id.get(str(e.get("to")))
    if src is None or dst is None or str(dst.get("id")) in hidden:
      continue
    try:
      x0, y0 = world_to_px(tf, float(src.get("x", 0.0)), float(src.get("y", 0.0)))
      x1, y1 = world_to_px(tf, float(dst.get("x", 0.0)), float(dst.get("y", 0.0)))
    except (TypeError, ValueError):
      continue
    p.drawLine(QPointF(r.left() + x0, r.top() + y0), QPointF(r.left() + x1, r.top() + y1))


def render_overview_pixmap(sheet, w: int, h: int, view_world=None) -> QPixmap:
  """sheet → 总览位图（sheet 缩略图条 / 小地图缓存共用）。"""
  lw, lh = max(8, int(w)), max(8, int(h))
  pm = hidpi_pixmap(lw, lh)     # C7：总览/小地图按 DPR 栅格化（逻辑尺寸不变）
  pm.fill(QColor(_th.BG_PANEL_SUB))
  p = QPainter(pm)
  try:
    paint_sheet_overview(p, QRectF(0.0, 0.0, float(lw), float(lh)), sheet, view_world)
  finally:
    p.end()
  return pm


class MinimapWidget(QWidget):
  """画布右下角小地图：缓存总览 + 视口框 + 点击/拖动导航（200ms 节流刷新）。

  宿主（ClipStudioCanvas）需提供：`sheet()/_vr()/navigate_to_world(QPointF)` 与
  信号 `view_changed/graph_changed`（鸭子类型）。
  """

  def __init__(self, canvas, parent=None) -> None:
    super().__init__(parent if parent is not None else canvas)
    self._canvas = canvas
    self.setFixedSize(MINI_W, MINI_H)
    self.setCursor(Qt.CursorShape.PointingHandCursor)
    self.setToolTip("小地图：点击/拖动导航（含视口框）")
    self._pm: QPixmap | None = None
    self._refresh_count = 0
    self._timer = QTimer(self)
    self._timer.setSingleShot(True)
    self._timer.setInterval(OVERVIEW_REFRESH_MS)
    self._timer.timeout.connect(self.refresh_now)
    for sig in ("view_changed", "graph_changed"):
      s = getattr(canvas, sig, None)
      if s is not None:
        s.connect(self.request_refresh)

  # ── 刷新（200ms 节流；缓存位图只在到点重建）─────────────────────────
  def request_refresh(self) -> None:
    """登记一次待刷新（节流窗口内合并；重复调用不叠加）。"""
    if not self._timer.isActive():
      self._timer.start()

  def refresh_now(self) -> None:
    """立即重建缓存总览并重绘（节流到点 / 测试入口）。"""
    self._timer.stop()
    self._refresh_count += 1
    sheet = self._canvas.sheet() if self._canvas is not None else {}
    vr = self._view_rect()
    self._pm = render_overview_pixmap(sheet, self.width(), self.height(), vr)
    # R194/R4-02：缓存渲染变换供导航反解（与位图严格同源）
    self._nav_tf = overview_transform(sheet, self.width(), self.height(), vr)
    self.update()

  def refresh_count(self) -> int:
    return int(self._refresh_count)

  def cached_pixmap(self):
    """当前缓存位图（未建 → None；测试/调试入口）。"""
    return self._pm

  def _view_rect(self):
    try:
      return self._canvas._vr()
    except Exception:
      return None

  # ── 绘制（只画缓存，不重算 —— 见模块 docstring 缓存纪律）────────────
  def paintEvent(self, ev): # noqa: N802
    p = QPainter(self)
    if self._pm is None:
      self._pm = render_overview_pixmap(
        self._canvas.sheet() if self._canvas is not None else {},
        self.width(), self.height(), self._view_rect())
    p.drawPixmap(0, 0, self._pm)
    p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0))
    p.end()

  # ── 导航 ────────────────────────────────────────────────────────────
  def _navigate(self, pos) -> None:
    sheet = self._canvas.sheet() if self._canvas is not None else {}
    # R194/R4-02：反解必须用渲染时的同一变换（缓存 tf），否则偏心
    #  点击落点系统性偏向中心。
    tf = getattr(self, "_nav_tf", None)
    if tf is None:
      tf = overview_transform(sheet, self.width(), self.height(),
                  self._view_rect())
    if tf is None:
      return
    wx, wy = px_to_world(tf, pos.x(), pos.y())
    self._canvas.navigate_to_world(QPointF(wx, wy))

  def mousePressEvent(self, ev): # noqa: N802
    if ev.button() == Qt.MouseButton.LeftButton:
      self._navigate(ev.position())
      ev.accept()
      return
    super().mousePressEvent(ev)

  def mouseMoveEvent(self, ev): # noqa: N802
    if ev.buttons() & Qt.MouseButton.LeftButton:
      self._navigate(ev.position())
      ev.accept()
      return
    super().mouseMoveEvent(ev)


class ZoomLabel(QLabel):
  """角落缩放指示（百分比）；点击 = 回到 100%（R183 W2-ST #13）。"""

  clicked = pyqtSignal()

  def __init__(self, parent=None) -> None:
    super().__init__(parent)
    self.setCursor(Qt.CursorShape.PointingHandCursor)
    self.setToolTip("当前缩放；点击回到 100%（快捷键 0；+/- 缩放）")
    self.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.setFixedSize(52, 18)
    self.setStyleSheet(f"color: {_th.TEXT_SECONDARY}; background: transparent;")

  def set_scale(self, scale: float) -> None:
    """按缩放比例刷新文本（`100%` 档；测试入口）。"""
    text = f"{float(scale):.0%}"
    if text != self.text():
      self.setText(text)

  def mousePressEvent(self, ev): # noqa: N802
    if ev.button() == Qt.MouseButton.LeftButton:
      self.clicked.emit()
      ev.accept()
      return
    super().mousePressEvent(ev)
