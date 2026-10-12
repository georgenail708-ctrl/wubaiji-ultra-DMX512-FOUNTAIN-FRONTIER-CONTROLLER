# -*- coding: utf-8 -*-
"""Backdrop 分组框的数据/几何纯函数（R183 深查② #5，节点编辑器语义）。

来源（语义 + 数字）：参考节点库 backdrop 实现

* ``calc_backdrop_size``：成员包围盒四周 **padding = 40**（本模块 ``FRAME_PAD``）；
* ``_min_size = (80, 80)``（``FRAME_MIN_W/H``）；
* 填充 ``color[:3] + (50,)``、圆角 2.6、描边常态 0.8 / 选中 1.0、默认色
  ``(5, 129, 138, 255)`` —— 数字沉淀在 `studio_theme_nodes`（本模块只引用）。

为什么不用现成：是 PySide 实现（0.6.44）且框是节点对象（要整条
节点/端口/属性体系），本仓画布是单 QWidget 自绘、sheet 是纯 dict —— 按 R183
既定纪律「只搬语义」。框内成员该用显式列表（显式列表）还是
几何判定：本仓取**卡中心落在框内**即随框移动（等价效果，省一步「先框选再包裹」）。

schema（唯一契约）::

    sheet["graph"]["frames"] = [{"id", "title", "x", "y", "w", "h", "color"}]

``x/y`` = 框**左上角**（节点 x/y 是卡中心，勿混用）；``color`` = ``#RRGGBB``。
旧档无 ``frames`` 键 → :func:`frames_of` 现造空表 + :func:`normalize_frames` 收敛，
不报错（向后兼容）。
"""
from __future__ import annotations

from src.ui import qascade_theme as _th
from src.ui.clip_studio import studio_theme_nodes as _nt

__all__ = [
    "FRAME_PAD", "FRAME_MIN_W", "FRAME_MIN_H", "DEFAULT_FRAME_TITLE",
    "DEFAULT_FRAME_COLOR", "FRAME_COLOR_CHOICES",
    "frames_of", "frame_rect", "frame_color", "is_hex_color", "new_frame",
    "wrap_rect", "frame_at", "resize_hit", "frame_members", "remove_frame",
    "normalize_frames",
]

#: 成员包围盒四周外扩（外扩 padding = 40）。
FRAME_PAD = 40.0
#: 最小框尺寸（最小 (80, 80)）。
FRAME_MIN_W = 80.0
FRAME_MIN_H = 80.0
#: 新框默认标题 / 默认底色（默认色 (5,129,138)）。
DEFAULT_FRAME_TITLE = "分组"
DEFAULT_FRAME_COLOR = _nt.BACKDROP_COLOR
#: 右键「颜色」子菜单预设（(显示名, #RRGGBB)；全部取 qascade_theme 令牌）。
FRAME_COLOR_CHOICES = (
    ("青（默认）", DEFAULT_FRAME_COLOR),
    ("蓝", _th.FOCUS_BLUE),
    ("绿", _th.BRIGHT_SUCCESS),
    ("黄", _th.BRIGHT_WARN),
    ("橙", _th.ACTIVE_ORANGE),
    ("红", _th.BRIGHT_ERROR),
)


def _f(value, default: float = 0.0) -> float:
    """容错取浮点（畸形值 → default；归一/命中一律走这里，永不抛）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _new_id() -> str:
    """框 id（与节点/边同一发号器，前缀 ``f_``；延迟 import 断 studio_graph 环）。"""
    from src.ui.clip_studio.studio_graph import _gen_id
    return _gen_id("f")


def frames_of(sheet) -> list:
    """sheet 的 frames 列表（**活引用**，就地增删）；缺键/畸形 → 建空表（旧档兼容）。"""
    graph = sheet.get("graph") if isinstance(sheet, dict) else None
    if not isinstance(graph, dict):
        return []
    frames = graph.get("frames")
    if not isinstance(frames, list):
        frames = []
        graph["frames"] = frames
    return frames


def frame_rect(frame) -> tuple:
    """框 → ``(x, y, w, h)``（浮点；w/h 钳到最小尺寸，防畸形项画出零面积框）。"""
    return (_f(frame.get("x")), _f(frame.get("y")),
            max(FRAME_MIN_W, _f(frame.get("w"), FRAME_MIN_W)),
            max(FRAME_MIN_H, _f(frame.get("h"), FRAME_MIN_H)))


def is_hex_color(value) -> bool:
    """``#RRGGBB``（大小写不限）合法性；非法 → 调用方回默认色。"""
    s = str(value or "")
    return len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])


def frame_color(frame) -> str:
    """框底色（非法/缺失 → 默认色）。"""
    return str(frame.get("color") or "") if is_hex_color(frame.get("color")) \
        else DEFAULT_FRAME_COLOR


def new_frame(rect, title: str = "", color: str = "") -> dict:
    """新建框 dict（``rect=(x, y, w, h)``；w/h 钳最小；title/color 空或非法 → 默认值）。"""
    x, y, w, h = (float(v) for v in rect)
    return {"id": _new_id(), "title": str(title or DEFAULT_FRAME_TITLE)[:64],
            "x": x, "y": y,
            "w": max(FRAME_MIN_W, w), "h": max(FRAME_MIN_H, h),
            "color": color if is_hex_color(color) else DEFAULT_FRAME_COLOR}


def wrap_rect(rects) -> tuple | None:
    """若干卡矩形包围盒 + padding → ``(x, y, w, h)``；空输入 → None。

    ``rects`` = ``[(x, y, w, h), ...]``（世界坐标，卡矩形）；公式 = 参考实现
    ``calc_backdrop_size``（四周 padding，最小尺寸兜底）。"""
    if not rects:
        return None
    x0 = min(_f(r[0]) for r in rects)
    y0 = min(_f(r[1]) for r in rects)
    x1 = max(_f(r[0]) + _f(r[2]) for r in rects)
    y1 = max(_f(r[1]) + _f(r[3]) for r in rects)
    return (x0 - FRAME_PAD, y0 - FRAME_PAD,
            max(FRAME_MIN_W, (x1 - x0) + 2.0 * FRAME_PAD),
            max(FRAME_MIN_H, (y1 - y0) + 2.0 * FRAME_PAD))


def frame_at(frames, x: float, y: float) -> dict | None:
    """点命中框（列表倒序 = 后建者优先）；未命中 → None。"""
    for f in reversed(frames or ()):
        rx, ry, rw, rh = frame_rect(f)
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return f
    return None


def resize_hit(frames, x: float, y: float, handle: float) -> dict | None:
    """右下角缩放手柄命中（缩放手柄 语义）；未命中 → None。"""
    for f in reversed(frames or ()):
        rx, ry, rw, rh = frame_rect(f)
        if rx + rw - handle <= x <= rx + rw and ry + rh - handle <= y <= ry + rh:
            return f
    return None


def frame_members(sheet, frame) -> list:
    """框内成员卡 id：**卡中心落在框内**即算（拖动框时随框平移）。"""
    rx, ry, rw, rh = frame_rect(frame)
    out = []
    for n in (sheet.get("graph") or {}).get("nodes") or []:
        if not isinstance(n, dict):
            continue
        cx, cy = _f(n.get("x")), _f(n.get("y"))
        if rx <= cx <= rx + rw and ry <= cy <= ry + rh:
            out.append(str(n.get("id")))
    return out


def remove_frame(frames, fid) -> bool:
    """按 id 就地删框；删到返回 True。"""
    before = len(frames)
    frames[:] = [f for f in frames if str(f.get("id")) != str(fid)]
    return len(frames) != before


def _norm_frame(raw, used: set) -> dict | None:
    """单项归一（非 dict → None；id 缺失/重复重排；w/h 钳最小；色非法回默认）。"""
    if not isinstance(raw, dict):
        return None
    fid = str(raw.get("id", "") or "")
    if (not fid) or fid in used:
        fid = _new_id()
    used.add(fid)
    return {"id": fid, "title": str(raw.get("title", "") or DEFAULT_FRAME_TITLE)[:64],
            "x": _f(raw.get("x")), "y": _f(raw.get("y")),
            "w": max(FRAME_MIN_W, _f(raw.get("w"), FRAME_MIN_W)),
            "h": max(FRAME_MIN_H, _f(raw.get("h"), FRAME_MIN_H)),
            "color": raw.get("color") if is_hex_color(raw.get("color")) else DEFAULT_FRAME_COLOR}


def normalize_frames(raw) -> list:
    """frames 归一（缺键/非列表 → ``[]``；畸形项剔除；永不抛异常，不修改入参）。"""
    out, used = [], set()
    for item in (raw if isinstance(raw, list) else ()):
        f = _norm_frame(item, used)
        if f is not None:
            out.append(f)
    return out
