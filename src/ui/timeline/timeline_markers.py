# -*- coding: utf-8 -*-
"""★ R183 T2/T6：时间线**标记轨 + 循环区**的存取与撤销域。

语义来源：
  · 3 Marker Track —— "Draw the Marker Bars into the Marker Track by using
    the mouse. Change Color and Text in the Settings View."（官方文档
    help3..com/-show-and-animation/-show-sequencer/
    -track-types/-marker-track.md）；标记条贴在时间线**顶部**。
  · 3 Loop Region（help3..com/.../-loop-region.md）+
    loop brace：循环区间随工程持久化，与"循环开关"是两件事。

为什么独立成模块（而不是写在 `timeline_widget.py` 里）：
  `timeline_widget.py` 已是基线冻结的 2044 行 / 53 个方法的既有大文件，架构门禁
  只许缩小不许增长；本模块把"纯取值 / 纯写值 / 撤销域"三件事做成**模块级纯函数**
  （只吃 session + controller），widget 侧只留薄壳转发。

数据真源：`Project.markers`（list）/ `Project.loop`（dict）——两者都是 R183 新增
  顶层键，真工程 TTM 复核证实零占用（零冲突、零迁移）。撤销域是**独立**的
  `DOMAIN_MARKERS`（审计⑦B P1-5，见 `marker_undo_domain()`）。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

#: 新标记的默认色（bright_warn：与循环终点手柄同色族，语义"导航锚点"）
DEFAULT_MARKER_COLOR = "#E6B455"

#: 标记与循环区所属的撤销域（★ R183 审计⑦B P1-5：**独立域**，不并入 DOMAIN_TIMELINE）。
#: 并入时间线域会让"拖 clip 的 Ctrl+Z"连带删掉拖之后加的标记（markers 回到那次
#: clip 快照的时刻）；独立域下两者互不回滚，各自只回滚自己这一步。
_MARKER_DOMAIN_FIELDS = ("markers", "loop")


def marker_undo_domain() -> frozenset:
    """标记/循环操作的撤销域（`project_manager.DOMAIN_MARKERS` 的单一真源转发）。"""
    try:
        from ...core.project_manager import DOMAIN_MARKERS
        return DOMAIN_MARKERS
    except Exception:
        return frozenset(_MARKER_DOMAIN_FIELDS)


def _pm_of(ctrl):
    """从 controller / widget 取 ProjectManager（★ P1-4：含单例兜底）。

    ★★ 审计⑦B P1-4 实锤（标记轨/循环区撤销死链）：原实现只查 ctrl 上的
      `_pm / pm / _project_manager / _pm_provider`，而生产装配里**一个都没有**
      （探针实测 ctrl 属性列表为空）→ `snapshot_markers` 恒返回 False →
      标记增删/循环拖动**从来没有**撤销条目（用户 Ctrl+Z 什么都不回滚）。
      修法：与 `TimelineWidget._get_pm` 同源 —— 属性 → provider → 进程内单例。
    """
    for obj in _pm_candidates(ctrl):
        if obj is not None:
            return obj
    try:
        from ...core.project_manager import ProjectManager
        obj = ProjectManager.instance()
        return obj if obj is not None and hasattr(obj, "_snapshot_scoped") else None
    except Exception:
        return None


def _pm_candidates(ctrl):
    """ctrl 上可能的 PM 持有点（属性 + provider），逐个产出可用者。"""
    for attr in ("_pm", "pm", "_project_manager"):
        yield from _yield_pm(_safe_attr(ctrl, attr))
    for attr in ("_pm_provider", "_project_manager_provider"):
        fn = _safe_attr(ctrl, attr)
        if callable(fn):
            yield from _yield_pm(_call_safe(fn))


def _yield_pm(obj):
    """是可用 PM 才产出（否则什么都不产出）。"""
    if obj is not None and hasattr(obj, "_snapshot_scoped"):
        yield obj


def _call_safe(fn):
    """调 provider（异常 → None）。"""
    try:
        return fn()
    except Exception:
        return None


def _safe_attr(obj, name):
    """安全取属性（访问器自身抛异常时也返回 None，不只兜 AttributeError）。"""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def snapshot_markers(session, ctrl=None) -> bool:
    """压一条"只回滚 markers/loop"的撤销条目（`DOMAIN_MARKERS`）。返回是否压栈成功。

    `pm` 从 controller 取（属性 → provider → 单例，见 `_pm_of`）；取不到
    （纯数据单测）→ 返回 False，调用方继续执行（功能优先于撤销）。
    """
    pm = _pm_of(ctrl)
    if pm is None:
        return False
    try:
        from .timeline_perf import timeline_gesture_snapshot
        return bool(timeline_gesture_snapshot(pm, marker_undo_domain()))
    except Exception:
        return False


# ── 读 ────────────────────────────────────────────────────────────────
def load_markers(session) -> List[dict]:
    """工程里的标记列表（副本；非法条目在 models 归一化时已剔除）。"""
    try:
        raw = getattr(session, "markers", None) or []
    except Exception:
        return []
    out = []
    for m in raw:
        if isinstance(m, dict):
            out.append({"t": float(m.get("t", 0.0) or 0.0),
                        "text": str(m.get("text", "") or ""),
                        "color": str(m.get("color", "") or "")})
    out.sort(key=lambda x: x["t"])
    return out


def load_loop(session) -> Tuple[float, float, bool]:
    """工程里的循环区 `(start, end, enabled)`；无存档 → `(0, 0, False)`。"""
    try:
        raw = getattr(session, "loop", None) or {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        return 0.0, 0.0, False
    try:
        start = max(0.0, float(raw.get("start", 0.0) or 0.0))
        end = float(raw.get("end", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0, False
    if end <= start:
        return 0.0, 0.0, False
    return start, end, bool(raw.get("enabled", True))


# ── 写（写前一律先压撤销条目；写后标脏由 widget 侧统一做）────────────────
def add_marker(session, ctrl, t: float, text: str = "",
               color: str = DEFAULT_MARKER_COLOR) -> Optional[dict]:
    """在 `t` 秒加标记；返回新标记（t 非法 → None，不写）。"""
    try:
        tt = max(0.0, float(t))
    except (TypeError, ValueError):
        return None
    if tt != tt:
        return None
    snapshot_markers(session, ctrl)
    m = {"t": tt, "text": str(text or ""), "color": str(color or DEFAULT_MARKER_COLOR)}
    items = load_markers(session)
    items.append(m)
    items.sort(key=lambda x: x["t"])
    _write_markers(session, items)
    return m


def update_marker(session, ctrl, index: int, *, t=None, text=None,
                  color=None, snapshot: bool = True) -> bool:
    """改标记（只改传入的非 None 字段）。下标越界 → False，不压快照。

    ★ 清查⑧A P1-2：`snapshot=False` 供**拖动中**的连续帧使用（与 `set_loop`
      同款形态）——修前每帧无条件压一条，一次拖动 15 帧 = 15 条撤销，要按 15
      次 Ctrl+Z 才回拖动前。手势起点由 `on_press` 压一条（见 timeline_marker_menu）。
    """
    items = load_markers(session)
    if not (0 <= int(index) < len(items)):
        return False
    if snapshot:
        snapshot_markers(session, ctrl)
    m = items[int(index)]
    if t is not None:
        try:
            tt = max(0.0, float(t))
        except (TypeError, ValueError):
            tt = m["t"]
        if tt == tt:
            m["t"] = tt
    if text is not None:
        m["text"] = str(text)
    if color is not None:
        m["color"] = str(color)
    items.sort(key=lambda x: x["t"])
    _write_markers(session, items)
    return True


def remove_marker(session, ctrl, index: int) -> bool:
    """删标记；下标越界 → False（不压快照）。"""
    items = load_markers(session)
    if not (0 <= int(index) < len(items)):
        return False
    snapshot_markers(session, ctrl)
    items.pop(int(index))
    _write_markers(session, items)
    return True


def set_loop(session, ctrl, start: float, end: float, enabled: bool = True, *,
             snapshot: bool = True) -> bool:
    """写循环区（`end <= start` 或非法 → False，不写不压快照）。

    `snapshot=False` 供**拖动进行中**的连续写入（一次手势只压一条撤销条目：
    拖动起点压、drag 中不压、release 不再压）。
    """
    try:
        s = max(0.0, float(start))
        e = float(end)
    except (TypeError, ValueError):
        return False
    if s != s or e != e or e <= s:
        return False
    if snapshot:
        snapshot_markers(session, ctrl)
    try:
        session.loop = {"start": s, "end": e, "enabled": bool(enabled)}
    except Exception:
        return False
    return True


def _write_markers(session, items) -> None:
    """写回工程（失败静默：session 可能是只读替身）。"""
    try:
        session.markers = list(items)
    except Exception:
        pass


__all__ = ["DEFAULT_MARKER_COLOR", "marker_undo_domain", "snapshot_markers",
           "load_markers", "load_loop", "add_marker", "update_marker",
           "remove_marker", "set_loop"]
