# -*- coding: utf-8 -*-
"""标尺**绘制段**（从 `TimelineRuler.paintEvent` 外提；纯绘制 + 纯换算）。

存在理由（架构门禁）：`timeline_ruler.paintEvent` 在基线里是 104 行 / CC 15，
R183 要往里加 BPM 小节刻度层与标记条 —— 直接写在里面必然"债务恶化"报 ERROR。
按既有纪律（新逻辑优先落新模块）把"刻度换算 / 秒刻度绘制 / BPM 网格绘制 /
标记条绘制"整体外提：paintEvent 只剩编排，行数与 CC 双降，而新增能力在新模块
里自由生长。绘制函数只吃 `(painter, …)` 与几何参数，不持有 ruler 状态。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygon

from .. import qascade_theme as _th
from . import timeline_grid as _grid

#: 秒刻度候选间隔（与既有实现逐值一致；挑第一个"像素间隔 ≥ min_tick_px"的）
SECOND_INTERVALS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0,
                    10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
#: 主刻度最小像素间隔
MIN_TICK_PX = 55
#: 每根秒主刻度之间的次刻度数
SUB_TICKS = 4
#: 标尺顶部标记条高度（Marker Track 语义：贴顶 12px 一条）
MARKER_STRIP_H = 12


def pick_interval(px_per_sec: float, min_tick_px: int = MIN_TICK_PX) -> float:
    """挑秒刻度间隔（>=min_tick_px 的第一个候选；全不够用就取最大候选）。"""
    for iv in SECOND_INTERVALS:
        if iv * px_per_sec >= min_tick_px:
            return iv
    return SECOND_INTERVALS[-1]


def tick_label(t: float, interval: float) -> str:
    """主刻度数字（与既有格式逐字符一致：m:ss / Ns / 两位小数）。"""
    if interval >= 60:
        return f"{int(t // 60)}:{int(t % 60):02d}"
    if interval >= 1:
        return f"{int(t)}s"
    return f"{t:.2f}"


def draw_second_ticks(p, ruler, w: int, h: int, font) -> None:
    """秒刻度层：主刻度线 + 数字 + 次刻度（逐像素输出与改造前一致）。"""
    interval = pick_interval(ruler._px_per_sec)
    visible_start = ruler._px_to_time(0)
    first_tick = int(visible_start / interval) * interval
    loop_limit = ruler._tick_loop_limit(w, interval)
    sub_pen = QPen(QColor(_th.BORDER_NORMAL), 1)
    p.setFont(font)
    t = first_tick
    while t <= loop_limit:
        x = ruler._time_to_px(t)
        if -80 <= x <= w + 80:
            p.setPen(QPen(QColor(_th.TEXT_SECONDARY), 1))
            p.drawLine(x, h - 12, x, h)
            p.setPen(QColor(_th.TEXT_SECONDARY))   # 主刻度数字 text_secondary
            p.drawText(x + 3, 0, 80, h - 14,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       tick_label(t, interval))
            sub = interval / float(SUB_TICKS)
            for k in range(1, SUB_TICKS):
                sx = ruler._time_to_px(t + k * sub)
                if 0 <= sx <= w:
                    p.setPen(sub_pen)
                    p.drawLine(sx, h - 5, sx, h)
        t = round(t + interval, 9)


def draw_bpm_grid(p, ruler, w: int, h: int) -> int:
    """★ R183 T3 BPM 小节/拍刻度层（零数据成本：工程 `bpm` 早就在库里）。

    分级由 `timeline_grid.bpm_ticks` 决定（>8px/拍才画拍线；小节线也糊就整层不画）。
    返回画出的线数（测试用）。bpm<=0 / 无网格 → 0 且零绘制。
    """
    bpm = getattr(ruler, "_bpm", 0.0)
    if not bpm:
        return 0
    t0 = ruler._px_to_time(0)
    t1 = ruler._px_to_time(w + 40)
    ticks = _grid.bpm_ticks(bpm, ruler._px_per_sec, t0, t1)
    if not ticks:
        return 0
    bar_pen = QPen(QColor(_th.FOCUS_BLUE), 1)          # 小节线（焦点蓝，与吸附线同族）
    beat_pen = QPen(QColor(_th.BORDER_NORMAL), 1)
    n = 0
    for t, is_bar in ticks:
        x = ruler._time_to_px(t)
        if x < -2 or x > w + 2:
            continue
        p.setPen(bar_pen if is_bar else beat_pen)
        # 贴顶画（0..)与秒刻度（底部 12px）在纵向上分层，互不遮挡
        p.drawLine(x, 0, x, 6 if is_bar else 3)
        n += 1
    return n


def draw_markers(p, ruler, w: int, h: int, font) -> None:
    """标记条：贴顶 MARKER_STRIP_H 高的色条 + 文字（"Marker Bars" 语义）。

    标记数据由 `TimelineRuler.set_markers([{t,text,color}, …])` 注入；本函数只画，
    命中/交互在 ruler 的鼠标事件里（点击跳转、拖动移动由 MarkerStrip 回调）。
    逐标记绘制抽到 `_draw_one_marker`（外提压本函数 CC）。
    """
    marks = getattr(ruler, "_markers", None) or ()
    if not marks:
        return
    p.setFont(font)
    p.fillRect(QRect(0, 0, w, MARKER_STRIP_H),   # 标记条底（极淡，区分区域）
               QColor(_th.BG_PANEL))
    for m in marks:
        _draw_one_marker(p, ruler, w, h, m)


def _draw_one_marker(p, ruler, w: int, h: int, m: dict) -> None:
    """单条标记：色条 + 文字 + 贯穿标尺的竖线（可见区外直接跳过）。"""
    x = _marker_x(ruler, w, m)
    if x is None:
        return
    color = _marker_color(m)
    bar = _marker_bar_rect(x, ruler)
    p.fillRect(bar, color)
    p.setPen(QColor(0, 0, 0, 200))
    text = str(m.get("text", "") or "")
    if text:
        p.drawText(QRect(x + 3, 0, bar.width() - 5, MARKER_STRIP_H),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   text[:14])
    p.setPen(QPen(QColor(_th.TEXT_SECONDARY), 1))
    p.drawLine(x, 0, x, h)                          # 标记竖线贯穿标尺（便于对位）


def _marker_x(ruler, w: int, m: dict):
    """标记的像素 x；时间非法或落在可见区外（±60px 缓冲）→ None。"""
    try:
        x = ruler._time_to_px(float(m.get("t", 0.0) or 0.0))
    except (TypeError, ValueError):
        return None
    return None if (x < -60 or x > w + 60) else x


def _marker_color(m: dict) -> QColor:
    """标记色（非法/缺失回落 bright_warn —— 与循环终点手柄同色族）。"""
    color = QColor(str(m.get("color", "") or "") or _th.BRIGHT_WARN)
    return color if color.isValid() else QColor(_th.BRIGHT_WARN)


def _marker_bar_rect(x: int, ruler) -> QRect:
    """标记色条矩形（宽度夹 2..120，防坏值画出天量矩形）。"""
    bar_w = max(2, min(120, int(getattr(ruler, "_marker_bar_w", 26))))
    return QRect(x, 0, bar_w, MARKER_STRIP_H)


def draw_loop(p, ruler, w: int, h: int) -> None:
    """循环区：半透填充 + 起止三角手柄 + 端线（与改造前逐值一致）。"""
    if not (ruler._loop_enabled and ruler._loop_end > ruler._loop_start):
        return
    lx1 = ruler._time_to_px(ruler._loop_start)
    lx2 = ruler._time_to_px(ruler._loop_end)
    p.fillRect(lx1, 0, lx2 - lx1, h, QColor(71, 114, 179, 40))   # select_blue 填充
    for t_val, color_str in ((ruler._loop_start, _th.SELECT_BLUE),
                             (ruler._loop_end, _th.BRIGHT_WARN)):
        lx = ruler._time_to_px(t_val)
        if -4 <= lx <= w + 4:
            c = QColor(color_str)
            p.setPen(QPen(c, 2))
            p.drawLine(lx, 0, lx, h)
            pts = QPolygon([QPoint(lx, 0), QPoint(lx + 9, 0), QPoint(lx, 9)])
            p.setBrush(QBrush(c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(pts)


def draw_playhead(p, ruler, w: int, h: int) -> None:
    """播放头：橙竖线 + 顶部三角（与 clip_area 覆盖层同一色）。"""
    ph_x = ruler._time_to_px(ruler._position)
    if not (-4 <= ph_x <= w + 4):
        return
    p.setPen(QPen(QColor("#FFA028"), 2))   # active_orange（与 clip_area.py 同步）
    p.drawLine(ph_x, 0, ph_x, h)
    tri = QPolygon([QPoint(ph_x - 5, 0), QPoint(ph_x + 5, 0), QPoint(ph_x, 9)])
    p.setBrush(QBrush(QColor("#FFA028")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(tri)


def draw_all(p, ruler, w: int, h: int, font) -> None:
    """标尺全层编排（顺序 = 层序）。

    层序：秒刻度 → BPM 网格 → 标记条 → 循环区 → 播放头。
    标记条放在循环区**之后**：两者都占顶部 12px（循环三角手柄也在顶部），
    标记条是"用户主动放置的导航锚点"，被循环底色压住会看不见。
    末行发丝线（separator）保持改造前的位置与颜色。
    """
    draw_second_ticks(p, ruler, w, h, font)
    draw_bpm_grid(p, ruler, w, h)
    draw_loop(p, ruler, w, h)
    draw_markers(p, ruler, w, h, font)
    p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
    p.drawLine(0, h - 1, w, h - 1)
    draw_playhead(p, ruler, w, h)


__all__ = ["SECOND_INTERVALS", "MIN_TICK_PX", "MARKER_STRIP_H",
           "pick_interval", "tick_label", "draw_second_ticks", "draw_bpm_grid",
           "draw_markers", "draw_loop", "draw_playhead", "draw_all"]
