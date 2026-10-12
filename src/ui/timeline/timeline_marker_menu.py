# -*- coding: utf-8 -*-
"""★ R183 T2：标记轨**交互**（右键增删改名改色 / 点击跳转 / 拖动移动）。

从 `timeline_ruler.py` 外提：标尺本就是"刻度 + 鼠标手势"的既有大文件，而标记
交互是**一整套菜单流程**（QMenu + QInputDialog + QColorDialog）。按既有纪律
（新逻辑落新模块）做成**模块级函数**，ruler 只留 `marker_*` 薄壳转发。

分工：
  · 数据读写 / 撤销域 → `timeline_markers`（纯函数，可无 GUI 单测）；
  · 本文 → 菜单与鼠标手势（需要 Qt Widgets）；
  · 绘制 → `timeline_ruler_paint.draw_markers`。

用户语义（Marker Track + Locator）：
  右键空白 = 在该时间点新建标记；右键标记 = 改名 / 改色 / 删除 / 跳转；
  左键点标记 = 跳转；按住拖动 = 移动标记（拖动起点压一条撤销条目，drag 中不压）。
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from PyQt6.QtWidgets import QColorDialog, QInputDialog, QMenu

from . import timeline_markers as _mk

_log = logging.getLogger(__name__)

#: 标记色板（§4.C 十一色编码里的高可读档；官方"Change Color"给的是自由色，
#: 这里给 6 个常用锚点色 + "更多颜色…"走 QColorDialog）
MARKER_COLORS = (
    ("琥珀（默认）", "#E6B455"),
    ("信息蓝", "#6FA8DC"),
    ("成功绿", "#56B65B"),
    ("警告橙", "#FFA028"),
    ("错误红", "#E36855"),
    ("紫", "#9B6DD6"),
)

#: 命中容差（px）：标记竖线左右各这么宽内算点到标记
HIT_TOL_PX = 6


def hit_marker(ruler, x: float) -> Tuple[Optional[int], Optional[dict]]:
    """命中标记 → `(下标, 标记)`；未命中 `(None, None)`。"""
    try:
        return ruler.marker_at(x, HIT_TOL_PX)
    except Exception:
        return None, None


def _find_by_key(ruler, key: str):
    """按**稳定键**（拖动期冻结的 `t`+文本）重新定位标记下标；取不到 → None。

    ★★ 审计⑦B P2-11 修复（标记拖动跨相邻标记串台）：原实现拖动期持**下标**游标，
      而 `update_marker` 每次写完都按 `t` 重排 —— 把标记 A 拖过相邻标记 B 之后，
      A 的下标与 B 互换，后续 mousemove 就变成了"拖 B"（用户看到"拖着拖着换了
      一个标记"）。修法：按下时冻结身份键，拖动期按**身份**定位（拖的还是那一个）。
    """
    if not key:
        return None
    for i, m in enumerate(_mk.load_markers(ruler._session)):
        if _identity_key(m) == key:
            return i
    return None


def _identity_key(mark: dict) -> str:
    """标记的身份键：原始 `t` + 文本 + 颜色（拖动只改 `t`，故文本/颜色区分度足够；
    两者都相同且时间也相同 = 语义上不可区分的两条标记，取第一条无害）。"""
    return f"{float(mark.get('t', 0.0) or 0.0):.6f}|{mark.get('text', '')}|" \
           f"{mark.get('color', '')}"


def _marker_drag_index(ruler):
    """当前拖动中的标记下标：优先按冻结身份定位（P2-11），退路用记下的下标。"""
    key = getattr(ruler, "_marker_drag_key", None)
    by_key = _find_by_key(ruler, key)
    if by_key is not None:
        return by_key
    return getattr(ruler, "_marker_drag_idx", None)


def on_press(ruler, time_at_x: float, x: float, button_left: bool = True) -> bool:
    """左键按下：点中标记 → 起拖（返回 True）；未点中 → False（让标尺走原逻辑）。"""
    if not button_left:
        return False
    idx, m = hit_marker(ruler, x)
    if idx is None:
        return False
    # ★ 清查⑧A P1-2：手势**起点**压一条撤销（与 _loop_snapshot 同款）；拖动中
    #   各帧传 snapshot=False —— 一次拖动=恰好一条 Ctrl+Z 可回（修前 15 帧 15 条）。
    _mk.snapshot_markers(ruler._session, ruler._ctrl)
    ruler._marker_drag_idx = int(idx)
    ruler._marker_drag_key = _identity_key(m)     # ★ P2-11：冻结身份（防串台）
    ruler._marker_drag_moved = False
    return True


def on_move(ruler, time_at_x: float) -> bool:
    """拖动中：把**按下时那一个**标记移到 `time_at_x`（snapshot=False —— 起点已压一条）。"""
    idx = _marker_drag_index(ruler)
    if idx is None:
        return False
    ruler._marker_drag_moved = True
    moved = _mk.update_marker(ruler._session, ruler._ctrl, idx, t=time_at_x,
                              snapshot=False)
    if moved:
        ruler.set_markers(_mk.load_markers(ruler._session))
        # 重排后该标记下标可能已变 → 用新身份键续拖（下一帧仍定位到同一个）
        items = _mk.load_markers(ruler._session)
        for m in items:
            if abs(float(m.get("t", 0.0) or 0.0) - max(0.0, float(time_at_x))) < 1e-6:
                ruler._marker_drag_key = _identity_key(m)
                ruler._marker_drag_idx = None
                break
    return True


def on_release(ruler) -> bool:
    """松手：若**没拖动**则视为"点击跳转"（Locator 语义）。返回是否跳转。"""
    idx = _marker_drag_index(ruler)
    ruler._marker_drag_idx = None
    ruler._marker_drag_key = None
    if idx is None:
        return False
    if getattr(ruler, "_marker_drag_moved", False):
        return False                      # 拖过 = 移动，不跳转
    items = _mk.load_markers(ruler._session)
    if not (0 <= int(idx) < len(items)):
        return False
    t = float(items[int(idx)]["t"])
    try:
        ruler.seek_requested.emit(t)      # 走既有 seek 链（controller.seek）
        ruler.set_position(t)
    except Exception:
        return False
    return True


def context_menu(ruler, global_pos, time_at_x: float, x: float) -> None:
    """右键：循环区动作（若有） + 标记动作（空白=新建 / 标记=改名改色删跳转）。

    三段式（外提压 CC/嵌套：本方法原先是一条内联长链）：
      ① `_build_ctx_menu` 建菜单并返回动作句柄；② `exec`；③ `_apply_choice` 执行。
    """
    m, acts = _build_ctx_menu(ruler, x)
    chosen = m.exec(global_pos)
    if chosen is None:
        return
    _apply_choice(ruler, chosen, acts, time_at_x)


def _build_ctx_menu(ruler, x: float):
    """建右键菜单；返回 `(menu, acts)` —— `acts` 是 `{动作键: QAction}`。

    同时把命中标记的**身份键**记到 ruler 上（`_ctx_marker_key`）：菜单是模态的，
    期间库可能被外部改（undo 回灌等），执行时按身份键复核而不是用旧下标。
    """
    idx, mark = hit_marker(ruler, float(x))
    ruler._ctx_marker_idx = idx
    ruler._ctx_marker_key = _identity_key(mark) if idx is not None else None
    m = QMenu(ruler)
    acts = {}
    # ★ R183 审计⑦B P3-13：循环关闭入口（只在循环开着时出现）
    if _loop_enabled(ruler):
        acts["loop_off"] = m.addAction("关闭循环区（保留区间）")
        m.addSeparator()
    acts["add"] = m.addAction("在此添加标记")
    if idx is not None:
        acts.update(_marker_actions(m, mark))
    return m, acts


def _marker_actions(m, mark: dict) -> dict:
    """标记命中时的菜单项（跳转 / 改名 / 改色子菜单 / 删除）。"""
    acts = {
        "go": m.addAction(f"跳转到「{mark.get('text') or '标记'}」"),
        "rename": m.addAction("重命名…"),
    }
    color_menu = m.addMenu("改颜色")
    for name, hexv in MARKER_COLORS:
        acts[f"color:{hexv}"] = color_menu.addAction(name)
    acts["color_more"] = color_menu.addAction("更多颜色…")
    m.addSeparator()
    acts["delete"] = m.addAction("删除标记")
    return acts


def _apply_choice(ruler, chosen, acts, time_at_x: float) -> None:
    """执行菜单选择（查表：动作键 → 处理函数，避免长 if/elif 链）。"""
    key = next((k for k, a in acts.items() if a is chosen), "")
    if key == "add":
        _add_dialog(ruler, time_at_x)
    elif key == "loop_off":
        _apply_loop_off(ruler)
    elif key.startswith("color:"):
        _edit_marker(ruler, color=key.split(":", 1)[1])
    else:
        handler = _CHOICE_HANDLERS.get(key)
        if handler is not None:
            handler(ruler, acts, time_at_x)
    _after_write(ruler)


def _choice_go(ruler, acts, time_at_x: float) -> None:
    """跳转到标记（走既有 seek 链）。"""
    idx, mark = _ctx_target(ruler, acts)
    if mark is None:
        return
    try:
        ruler.seek_requested.emit(float(mark["t"]))
        ruler.set_position(float(mark["t"]))
    except Exception:
        _log.debug("标记跳转失败", exc_info=True)


def _choice_rename(ruler, acts, time_at_x: float) -> None:
    """重命名标记。"""
    idx, mark = _ctx_target(ruler, acts)
    if mark is None:
        return
    text, ok = QInputDialog.getText(ruler, "标记名称", "名称：",
                                   text=str(mark.get("text", "") or ""))
    if ok and idx is not None:
        _mk.update_marker(ruler._session, ruler._ctrl, idx, text=str(text))


def _choice_delete(ruler, acts, time_at_x: float) -> None:
    """删除标记。"""
    idx, mark = _ctx_target(ruler, acts)
    if idx is not None:
        _mk.remove_marker(ruler._session, ruler._ctrl, idx)


def _choice_color_more(ruler, acts, time_at_x: float) -> None:
    """自定义颜色（QColorDialog）。"""
    idx, mark = _ctx_target(ruler, acts)
    if idx is None:
        return
    c = QColorDialog.getColor(parent=ruler, title="标记颜色")
    if c.isValid():
        _mk.update_marker(ruler._session, ruler._ctrl, idx, color=c.name())


def _edit_marker(ruler, color: str) -> None:
    """色板改色（`_ctx_target` 用的下标存在 ruler 的临时槽里，见 `_build_ctx_menu`）。"""
    idx = getattr(ruler, "_ctx_marker_idx", None)
    if idx is not None:
        _mk.update_marker(ruler._session, ruler._ctrl, idx, color=color)


def _ctx_target(ruler, acts):
    """取菜单打开时刻的标记下标与数据（菜单期间库可能被外部改，按身份键复核）。"""
    key = getattr(ruler, "_ctx_marker_key", None)
    idx = _find_by_key(ruler, key)
    if idx is not None:
        items = _mk.load_markers(ruler._session)
        if 0 <= idx < len(items):
            ruler._ctx_marker_idx = idx
            return idx, items[idx]
    return getattr(ruler, "_ctx_marker_idx", None), None


#: 菜单动作键 → 处理函数（`_apply_choice` 查表用；色板项前缀特殊处理）
_CHOICE_HANDLERS = {
    "go": _choice_go,
    "rename": _choice_rename,
    "delete": _choice_delete,
    "color_more": _choice_color_more,
}


def _loop_enabled(ruler) -> bool:
    """循环是否开着（含"区间有效"判定 —— 只有真的在循环才提供关闭入口）。"""
    try:
        return bool(getattr(ruler, "_loop_enabled", False)) and \
            float(getattr(ruler, "_loop_end", 0.0) or 0.0) > \
            float(getattr(ruler, "_loop_start", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False


def _apply_loop_off(ruler) -> bool:
    """关闭循环（**保留区间**）：写内存 + 写工程 + 标脏。返回是否执行。

    ★ 直写工程（`session.loop`）而不是只发 `loop_committed`：标尺右键菜单是
    **独立于 TimelineWidget 的入口**（单测/嵌入式场景下可能没人接信号），
    写工程这件事必须由本动作自己负责到底；信号仍发，作为"UI 已落位"的通知。
    """
    try:
        start = float(getattr(ruler, "_loop_start", 0.0) or 0.0)
        end = float(getattr(ruler, "_loop_end", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if end <= start:
        return False
    from . import timeline_markers as _m
    session = getattr(ruler, "_session", None)
    _m.snapshot_markers(session, getattr(ruler, "_ctrl", None))
    try:
        ruler.set_loop(start, end, False)
        if session is not None:
            try:
                session.loop = {"start": start, "end": end, "enabled": False}
            except Exception:
                _log.debug("关闭循环：写工程失败（内存已关）", exc_info=True)
        ruler.loop_committed.emit(start, end, False)
    except Exception:
        return False
    return True


def _add_dialog(ruler, time_at_x: float) -> None:
    """新建标记：先问名称（空名也允许 —— 的标记可以只有颜色）。"""
    text, ok = QInputDialog.getText(ruler, "新建标记", "名称（可留空）：", text="")
    if not ok:
        return
    _mk.add_marker(ruler._session, ruler._ctrl, float(time_at_x), text=str(text))


def _after_write(ruler) -> None:
    """写库后刷新标记条 + 标脏（随工程持久化）。"""
    try:
        ruler.set_markers(_mk.load_markers(ruler._session))
    except Exception:
        pass
    try:
        ruler.markers_changed.emit()
    except Exception:
        pass


__all__ = ["MARKER_COLORS", "HIT_TOL_PX", "hit_marker", "on_press", "on_move",
           "on_release", "context_menu", "_loop_enabled", "_apply_loop_off"]
