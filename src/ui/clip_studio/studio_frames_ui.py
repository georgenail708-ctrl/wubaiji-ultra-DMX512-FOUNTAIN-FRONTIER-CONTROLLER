# -*- coding: utf-8 -*-
"""Backdrop 分组框的绘制与交互 + 卡片对齐吸附接入（R183 深查② #5/#6）。

拆成独立混入的理由：`studio_canvas*` 三个文件都贴着 500 行门禁（R183-B1 与
R182 已各拆过一次），框的绘制/拖动/右键菜单自成一块，单独文件最省改动。

宿主（ClipStudioCanvas）提供：`_frames/_frame_at/_frame_resize_at/_frame_qrect/
_node_by_id/_card_rect/_dirty_rect_for/_world_to_screen/_px/update/graph_changed/
selection_changed` 与属性 `_sel_frame/_hover_fid/_snap_guides/_snap_suspended/
_drag_frame`；几何与序列化唯一实现点在 `studio_frames`（纯函数），本文件只做
Qt 侧接线。

Z 序（参考: ``Z_VAL_BACKDROP = -2`` < ``Z_VAL_PIPE = -1`` <
``Z_VAL_NODE = 1``）：paintEvent 顺序「框 → 边 → 卡」等价落地；命中同理
「卡 > 边 > 框」。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainterPath, QPen
from PyQt6.QtWidgets import QInputDialog, QMenu

from src.ui import qascade_theme as _th
from src.ui.canvas_materials import tone
from src.ui.clip_studio import studio_theme_nodes as _nt
from src.ui.clip_studio.studio_frames import (DEFAULT_FRAME_TITLE, FRAME_COLOR_CHOICES,
                                              FRAME_MIN_H, FRAME_MIN_W, frame_at, frame_color,
                                              frame_members, frame_rect, frames_of, new_frame,
                                              remove_frame, resize_hit, wrap_rect)
from src.ui.clip_studio.studio_snap import GUIDE_COLOR, SNAP_TOL_PX, snap_rect

__all__ = ["FrameUiMixin"]

_FRAME_DIRTY_PAD = 10.0        # 框拖动脏区外扩（屏幕像素；与卡片拖动同口径）
_GUIDE_OVERHANG = 8.0          # 参考线两端外伸（屏幕像素，视觉上越过对齐对象）
_TITLE_PX = 12.0               # 框标题字号上限（屏幕像素档位，随缩放折半）


class FrameUiMixin:
    """分组框绘制 + 拖动/缩放/右键菜单 + 卡片拖动吸附接入（宿主 = 画布）。

    R183 W2-ST：框的**数据访问/增删**也收在本混入（原在 `studio_canvas`，为守其
    500 行门禁迁入）；这些方法对宿主是同一协议，绘制/拖动/右键三块共用。
    """

    # ── 数据访问 / 增删（R183 W2-ST 从 studio_canvas 迁入）──────────────
    def _frames(self) -> list:
        """当前 sheet 的框列表（活引用；旧档缺键自动补）。"""
        return frames_of(self._sheet)

    def _frame_at(self, wp) -> dict | None:
        """点命中框（倒序 = 后建者优先）。**卡片层优先**由调用方保证：
        鼠标按下先查桩/卡，未中才落到框（卡 1 > 框 -2）。"""
        return frame_at(self._frames(), wp.x(), wp.y())

    def _frame_resize_at(self, wp) -> dict | None:
        """命中框右下角缩放手柄（先于框体移动判定）。"""
        return resize_hit(self._frames(), wp.x(), wp.y(), self._px(_nt.BACKDROP_HANDLE_PX))

    def frame_by_id(self, fid) -> dict | None:
        """按 id 取框（面板/测试入口）。"""
        return next((f for f in self._frames() if str(f.get("id")) == str(fid)), None)

    def selected_frame_id(self) -> str:
        return self._sel_frame

    def add_frame(self, rect, title: str = "", color: str = "") -> dict:
        """新建分组框并选中（面板工具栏/右键入口；调用方负责 graph_changed）。"""
        frame = new_frame(rect, title=title, color=color)
        self._frames().append(frame)
        self._sel_frame = str(frame.get("id"))
        self.update()
        return frame

    def add_frame_wrapping(self, nids=None, title: str = "", color: str = "") -> dict | None:
        """按卡集（缺省=当前选中；再缺省=全部卡）包裹出框（搬运容器语义）。

        无卡 → None（不建空框）。返回新框。
        """
        ids = {str(x) for x in nids} if nids else (
            set(self._sel_nodes) or ({self._sel_node} if self._sel_node else set()))
        rects = [self._card_rect(n) for n in self._active_nodes()
                 if not ids or str(n.get("id")) in ids]
        if not rects:
            return None
        rects = [(r.x(), r.y(), r.width(), r.height()) for r in rects]
        box = wrap_rect(rects)
        return self.add_frame(box, title=title, color=color) if box else None

    def delete_frame(self, fid) -> bool:
        """删框（框本身消失；框内卡片保留）→ 是否真删到。"""
        ok = remove_frame(self._frames(), fid)
        if ok:
            if str(self._sel_frame) == str(fid):
                self._sel_frame = ""
            self._snap_guides = [[], []]
            self.update()
        return ok

    def frame_members_of(self, frame) -> list:
        """框内成员卡 id（拖动框时随框平移的集合；几何判定在 `studio_frames`）。"""
        return frame_members(self._sheet, frame)

    # ── 绘制（paintEvent 在边/卡之前调用 = 最底层）──────────────────────
    def _draw_frames(self, p, vr) -> None:
        """框层：半透明填充 → 标题条 → 描边 → 右下缩放手柄（视口外剔除）。"""
        pad = self._px(6.0)
        for f in self._frames():
            r = self._frame_qrect(f)
            if not vr.adjusted(-pad, -pad, pad, pad).intersects(r):
                continue
            self._draw_frame(p, f, r)

    def _frame_qrect(self, frame) -> QRectF:
        """框 → 世界坐标 QRectF（左上角 + 宽高，最小尺寸钳制在 `studio_frames`）。"""
        x, y, w, h = frame_rect(frame)
        return QRectF(x, y, w, h)

    def _draw_frame(self, p, frame, r: QRectF) -> None:
        """填充 alpha 50 / 圆角 2.6 / 描边 0.8（选中 1.0）—— 参考数字。"""
        fid = str(frame.get("id", ""))
        lit = fid == self._sel_frame or fid == self._hover_fid
        base = QColor(frame_color(frame))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(base.red(), base.green(), base.blue(),
                                 min(28, _nt.BACKDROP_ALPHA))))
        p.drawRoundedRect(r, _nt.BACKDROP_RADIUS, _nt.BACKDROP_RADIUS)
        self._draw_frame_title(p, frame, r, lit)
        width = _nt.BACKDROP_STROKE_SELECTED if fid == self._sel_frame else _nt.BACKDROP_STROKE
        p.setPen(QPen(base, self._px(width)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, _nt.BACKDROP_RADIUS, _nt.BACKDROP_RADIUS)
        self._draw_frame_handle(p, r, base, lit)

    def _frame_title_h(self, r: QRectF) -> float:
        """标题条高（屏幕恒定 ~22px，卡高 32% 上限——低缩放不吞框）。"""
        return min(max(self._px(22.0), self._px(14.0)), r.height() * 0.32)

    def _draw_frame_title(self, p, frame, r: QRectF, lit: bool) -> None:
        """标题条：深色半透明带 + 居中白字（超宽 elide）。"""
        band = QRectF(r.left(), r.top(), r.width(), self._frame_title_h(r))
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, _nt.BACKDROP_RADIUS, _nt.BACKDROP_RADIUS)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(tone(_th.CANVAS_HEADER if lit else _th.CANVAS_CARD, 235)))
        p.drawRect(band)
        p.restore()
        f = QFont()
        f.setPixelSize(max(8, min(int(_TITLE_PX), int(self._px(_TITLE_PX)))))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor(_th.CANVAS_INK)))
        text = QFontMetrics(f).elidedText(str(frame.get("title", "") or DEFAULT_FRAME_TITLE),
                                         Qt.TextElideMode.ElideRight, int(max(16.0, r.width() - 12.0)))
        p.drawText(band, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_frame_handle(self, p, r: QRectF, base: QColor, lit: bool) -> None:
        """右下角缩放三角（缩放手柄 语义；屏幕恒定 12px）。"""
        d = min(self._px(_nt.BACKDROP_HANDLE_PX), r.width() * 0.25, r.height() * 0.25)
        if d < 3.0:
            return
        path = QPainterPath()
        path.moveTo(r.right() - d, r.bottom())
        path.lineTo(r.right(), r.bottom())
        path.lineTo(r.right(), r.bottom() - d)
        path.closeSubpath()
        col = QColor(base)
        col.setAlpha(200 if lit else 80)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        p.drawPath(path)

    def _draw_snap_guides(self, p) -> None:
        """对齐参考线：1px 屏幕恒定品红实线，两端外伸 8px（越过对齐对象更易读）。"""
        pen = QPen(QColor(GUIDE_COLOR), self._px(1.0))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        over = self._px(_GUIDE_OVERHANG)
        for x, y0, y1 in self._snap_guides[0]:
            p.drawLine(QPointF(x, y0 - over), QPointF(x, y1 + over))
        for y, x0, x1 in self._snap_guides[1]:
            p.drawLine(QPointF(x0 - over, y), QPointF(x1 + over, y))

    # ── 命中 / 按下 / 拖动 / 松手 ───────────────────────────────────────
    def _frame_hit_id(self, wp) -> str:
        """悬停用框命中（卡/边未中时才问）→ 框 id 或 ""。"""
        f = self._frame_at(wp)
        return str(f.get("id", "")) if f else ""

    def _press_frame(self, wp, ev) -> bool:
        """左键按下命中框 / 右下缩放手柄 → 选中并进入拖动；未命中 False。

        卡片与桩位在 `mousePressEvent` 里**先**判（Z 序：卡 > 边 > 框），
        所以压在框内的卡片仍走卡片拖动（同款）。
        """
        handle = self._frame_resize_at(wp)
        frame = handle if handle is not None else self._frame_at(wp)
        if frame is None:
            return False
        self._clear_selection()
        self._sel_frame = str(frame.get("id", ""))
        self._begin_frame_drag(frame, wp, resize=handle is not None)
        self.selection_changed.emit("")
        self.update()
        return True

    def _begin_frame_drag(self, frame, wp, resize: bool) -> None:
        """登记拖动上下文：起点、原矩形、成员卡起点（成员在按下时快照，不中途吸附）。"""
        x, y, w, h = frame_rect(frame)
        self._drag_frame = {"frame": frame, "resize": bool(resize), "start": QPointF(wp),
                            "origin": (x, y, w, h), "moved": False,
                            "members": self._frame_member_origins(frame)}

    def _frame_member_origins(self, frame) -> list:
        """成员卡 ``[(nid, x0, y0)]``（拖动框时按同一位移整体平移）。"""
        out = []
        for nid in self.frame_members_of(frame):
            node = self._node_by_id(nid)
            if node is not None:
                out.append((nid, float(node.get("x", 0.0)), float(node.get("y", 0.0))))
        return out

    def _drag_frame_to(self, wp) -> None:
        """框拖动一帧：整框平移（带成员）或右下角缩放；脏区 = 框 ∪ 成员卡及连线。

        R183 W2-ST #15：框拖动首次真移动前压一条撤销（拖动一次=一条）。"""
        drag = self._drag_frame
        if drag is None:
            return
        d = wp - drag["start"]
        if not drag["moved"] and abs(d.x()) < 1.0 and abs(d.y()) < 1.0:
            return
        self._ensure_drag_undo()             # 撤销：内容 = 拖动前（框 + 成员卡）
        dirty = self._frame_drag_dirty(drag)
        if drag["resize"]:
            self._resize_frame_to(drag, d)
        else:
            self._move_frame_to(drag, d)
        drag["moved"] = True
        dirty = dirty.united(self._frame_drag_dirty(drag))
        self.update(dirty.adjusted(-_FRAME_DIRTY_PAD, -_FRAME_DIRTY_PAD,
                                   _FRAME_DIRTY_PAD, _FRAME_DIRTY_PAD).toAlignedRect())

    def _move_frame_to(self, drag, d) -> None:
        """整框平移 + 成员卡同步位移（框=搬运容器，wrap_nodes 语义）。"""
        frame = drag["frame"]
        x0, y0 = drag["origin"][0], drag["origin"][1]
        frame["x"], frame["y"] = x0 + d.x(), y0 + d.y()
        for nid, nx, ny in drag["members"]:
            node = self._node_by_id(nid)
            if node is not None:
                node["x"], node["y"] = nx + d.x(), ny + d.y()

    def _resize_frame_to(self, drag, d) -> None:
        """右下角缩放：只改宽高（最小尺寸钳制），成员不动。"""
        frame = drag["frame"]
        w0, h0 = drag["origin"][2], drag["origin"][3]
        frame["w"] = max(FRAME_MIN_W, w0 + d.x())
        frame["h"] = max(FRAME_MIN_H, h0 + d.y())

    def _frame_drag_dirty(self, drag) -> QRectF:
        """框 + 成员卡（含相连边鼓包）的当前屏幕脏区；成员脏区是整数 QRect，先转 QRectF。"""
        tl = self._world_to_screen(self._frame_qrect(drag["frame"]).topLeft())
        br = self._world_to_screen(self._frame_qrect(drag["frame"]).bottomRight())
        dirty = QRectF(tl, br).normalized()
        for nid, _x, _y in drag["members"]:
            dirty = dirty.united(QRectF(self._dirty_rect_for(nid)))
        return dirty

    def _end_frame_drag(self) -> None:
        """松手：真移动过才发一次 graph_changed（框位置/尺寸入签名 → 面板防抖落盘）。"""
        drag = self._drag_frame
        self._drag_frame = None
        if drag is None:
            return
        if drag.get("moved"):
            self._commit_graph_change()
        else:
            self._undo_token = False
        self.update()

    # ── 右键菜单（改名 / 改色 / 删除）──────────────────────────────────
    def _frame_menu(self, wp, sp) -> bool:
        """右键命中框 → 上下文菜单；未命中 False（交回右键平移通道）。"""
        frame = self._frame_at(wp)
        if frame is None:
            return False
        self._sel_frame = str(frame.get("id", ""))
        self.update()
        menu, actions = self._build_frame_menu()
        self._dispatch_frame_action(frame, menu.exec(self.mapToGlobal(sp.toPoint())),
                                    actions)   # B3：sp 为局部坐标，exec 要全局坐标
        return True

    def _build_frame_menu(self) -> tuple:
        """菜单 + 动作表（颜色子菜单项 → (action, 色值) 列表）。"""
        menu = QMenu(self)
        actions = {"rename": menu.addAction("重命名…"), "colors": []}
        color_menu = menu.addMenu("颜色")
        for name, value in FRAME_COLOR_CHOICES:
            actions["colors"].append((color_menu.addAction(name), value))
        menu.addSeparator()
        actions["delete"] = menu.addAction("删除分组框")
        return menu, actions

    def _dispatch_frame_action(self, frame, chosen, actions) -> None:
        """菜单选择分发（取消 → 什么都不做；各分支各自提交图变更）。"""
        if chosen is None:
            return
        if chosen is actions["rename"]:
            self._rename_frame(frame)
            return
        if chosen is actions["delete"]:
            if self.delete_frame(str(frame.get("id", ""))):
                self._commit_graph_change()
            return
        color = next((v for a, v in actions["colors"] if a is chosen), "")
        if color:
            frame["color"] = color
            self._after_frame_edit()

    def _rename_frame(self, frame) -> None:
        """改名（QInputDialog；空串回默认标题）。"""
        text, ok = QInputDialog.getText(self, "分组框名称", "名称：",
                                        text=str(frame.get("title", "")))
        if not ok:
            return
        frame["title"] = str(text).strip()[:64] or DEFAULT_FRAME_TITLE
        self._after_frame_edit()

    def _after_frame_edit(self) -> None:
        """框编辑收尾：发图变更（→ 防抖落盘/撤销快照）+ 重绘。"""
        self._commit_graph_change()
        self.update()

    def create_frame_around_selection(self) -> dict | None:
        """工具栏/测试入口：包裹当前选中卡（无选中 → 全部卡）；无卡 → None。

        **不**自行 emit `graph_changed`（画布变更方法一律由调用方提交，与
        `add_frame/delete_frame` 同口径）：面板工具栏路径走 `_on_graph_changed`。
        """
        frame = self.add_frame_wrapping()
        if frame is not None:
            self.update()
        return frame

    # ── 卡片拖动对齐吸附接入（R183 深查② #6）────────────────────────────
    def _snap_drag_delta(self, delta, origins) -> QPointF:
        """拖动位移 → 吸附后位移（并把参考线写进 `_snap_guides`）。

        `origins` = 正在被拖的卡的**按下时起点** ``{nid: (x0, y0)}``（单选一张 / 整组
        多张）。候选盒一律按 ``起点 + 位移`` 外推 —— 不能读卡的当前 x/y：本函数在拖动
        过程中会被反复调用，而卡坐标在第一次移动后就已被改写（读当前值会二次累加，
        吸附静默失效，实测踩过）。他卡取原位；阈值 = 屏幕恒定 ``SNAP_TOL_PX``
        （``_px`` 折算世界单位）。Alt 按住（`_snap_suspended`）= 临时禁用（手势）。
        """
        if self._snap_suspended:
            self._set_guides([], [])
            return QPointF(delta)
        box, others = self._snap_candidates(delta, origins)
        if box is None:
            self._set_guides([], [])
            return QPointF(delta)
        dx, dy, vg, hg = snap_rect(box, others, self._px(SNAP_TOL_PX))
        self._set_guides(vg, hg)
        return QPointF(delta.x() + dx, delta.y() + dy)

    def _snap_candidates(self, delta, origins) -> tuple:
        """(外推后的移动集包围盒 ``(x,y,w,h)``, 他卡**原位**矩形表)；任一侧空 → (None, [])。"""
        moving, others = [], []
        for n in self._active_nodes():
            r = self._card_rect(n)
            nid = str(n.get("id"))
            origin = origins.get(nid)
            if origin is None:
                others.append((r.x(), r.y(), r.width(), r.height()))
            else:
                moving.append(self._card_rect({"x": origin[0] + delta.x(),
                                               "y": origin[1] + delta.y()}))
        if not moving or not others:
            return None, []
        x0 = min(r.x() for r in moving)
        y0 = min(r.y() for r in moving)
        x1 = max(r.x() + r.width() for r in moving)
        y1 = max(r.y() + r.height() for r in moving)
        return (x0, y0, x1 - x0, y1 - y0), others

    def _set_guides(self, vg, hg) -> None:
        """参考线状态（内容变化才整图重绘：旧线可能落在任何脏区之外）。"""
        new = [list(vg), list(hg)]
        if new != self._snap_guides:
            self._snap_guides = new
            self.update()

    def clear_snap_guides(self) -> None:
        """清空参考线（拖动结束调用；避免残留旧线）。"""
        self._set_guides([], [])
