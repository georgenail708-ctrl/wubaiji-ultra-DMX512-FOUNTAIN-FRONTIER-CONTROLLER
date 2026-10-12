# -*- coding: utf-8 -*-
"""programmer_tools — Programmer 面板工具箱（R183 批次 I）。

纯函数段（无 Qt / 无面板状态，pytest 直接断言）：
  · 双读数 `pct_of` / `format_value_dual` / `format_range_dual`（gMA3 数值栏习惯）；
  · Post-Selection 过滤谓词 `build_filter_predicate`（选中集内再筛）；
  · Panorama Alignment `sort_ids_by_pos_x`（按物理 x 排翼序）；
  · Step `step_target_index`（步进越界环绕）；
  · hover 气泡 `slot_index_for_x`；组内抖动 `jitter_value`（0 = 逐字节旧行为）；
  · 摇杆路径 `joystick_axis_samples` / `pick_primary_axis`；
  · 滚轮相对编码 `wheel_delta` / `shift_range`。

面板操作段（Highlight 试灯 / 后选过滤 / Panorama / Step / Clear 三级释放 /
hover 文本）见 `programmer_highlight.py`（同样只被既有方法体内的注入行调用）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple



#: DMX 满量程（0..255）。
DMX_MAX = 255.0

# ── 双读数（项 13）──────────────────────────────────────────────────────────

def pct_of(value: Any) -> int:
    """DMX 值 → 百分比整数（0..100，四舍五入；坏输入按 0）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    v = max(0.0, min(DMX_MAX, v))
    return int(round(v / DMX_MAX * 100.0))


def format_value_dual(value: Any) -> str:
    """单值双读数 `"128(50%)"`。"""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = 0
    v = max(0, min(255, v))
    return f"{v}({pct_of(v)}%)"


def format_range_dual(base: Any, target: Any) -> str:
    """范围条带双读数 `"0(0%)→128(50%)"`。"""
    return f"{format_value_dual(base)}→{format_value_dual(target)}"


# ── Post-Selection 后选过滤（项 3）──────────────────────────────────────────

def fixture_universe(fixture: Any) -> int:
    """设备宇宙号（缺省/坏值按 1）。"""
    try:
        return int(getattr(fixture, "universe", 1) or 1)
    except (TypeError, ValueError):
        return 1


def fixture_def_id(fixture: Any) -> str:
    return str(getattr(fixture, "def_id", "") or "")


def fixture_addr_span(fixture: Any, defs: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """设备占用地址段（1 基闭区间）。通道数取自 `defs[def_id].channels` 长度；
    查不到定义 → 退化为 `(起始, 起始)`（仍可参与"包含该起点"判定）。"""
    try:
        start = max(1, int(getattr(fixture, "start_channel", 1) or 1))
    except (TypeError, ValueError):
        start = 1
    count = 1
    fd = (defs or {}).get(fixture_def_id(fixture))
    channels = getattr(fd, "channels", None) if fd is not None else None
    if channels:
        try:
            count = max(1, len(channels))
        except TypeError:
            count = 1
    return start, start + count - 1


def _filter_axis_args(universe, def_ids, addr_lo, addr_hi):
    """过滤三条件的规范化：(宇宙|None, 类型集合, lo, hi, 是否不限地址段)。"""
    uni = None
    if universe is not None:
        try:
            uni = int(universe)
        except (TypeError, ValueError):
            uni = None
    wanted = {str(d) for d in (def_ids or []) if str(d)}
    try:
        lo, hi = int(addr_lo), int(addr_hi)
    except (TypeError, ValueError):
        lo, hi = 0, 512
    if lo > hi:
        lo, hi = hi, lo
    return uni, wanted, lo, hi, (lo <= 0 and hi >= 512)


def _match_addr_window(fixture: Any, lo: int, hi: int, defs) -> bool:
    """设备地址段与 [lo,hi] 是否相交（跨段设备命中任意一段即保留）。"""
    start, end = fixture_addr_span(fixture, defs)
    return not (end < lo or start > hi)


def build_filter_predicate(universe: Optional[int] = None,
                           def_ids: Optional[Iterable[str]] = None,
                           addr_lo: int = 0, addr_hi: int = 512,
                           defs: Optional[Dict[str, Any]] = None) -> Callable[[Any], bool]:
    """构造后选过滤谓词（universe / 设备类型 def / 地址段 三条件**与**组合）。

    不设的条件自动跳过（universe=None、def_ids 空、地址段未收窄即"不限"）；
    地址段按与设备地址段**相交**判定。谓词对 None / 畸形设备安全返回 False。
    """
    uni, wanted, lo, hi, addr_all = _filter_axis_args(universe, def_ids, addr_lo, addr_hi)

    def _predicate(fixture: Any) -> bool:
        if fixture is None:
            return False
        if uni is not None and fixture_universe(fixture) != uni:
            return False
        if wanted and fixture_def_id(fixture) not in wanted:
            return False
        return addr_all or _match_addr_window(fixture, lo, hi, defs)

    return _predicate


def apply_post_selection_filter(ids: Sequence[str], fixtures_by_id: Dict[str, Any],
                                predicate: Optional[Callable[[Any], bool]]) -> List[str]:
    """在 `ids`（有序选择集）内应用谓词，保持原序（谓词 None = 原样返回）。"""
    ordered = [str(fid) for fid in (ids or []) if str(fid)]
    if predicate is None:
        return ordered
    return [fid for fid in ordered if predicate(fixtures_by_id.get(fid))]


def available_universes(ids: Sequence[str], fixtures_by_id: Dict[str, Any]) -> List[int]:
    """选择集里出现过的宇宙号（升序去重）。"""
    found = set()
    for fid in ids or []:
        fx = fixtures_by_id.get(str(fid))
        if fx is not None:
            found.add(fixture_universe(fx))
    return sorted(found)


def available_def_ids(ids: Sequence[str], fixtures_by_id: Dict[str, Any],
                      defs: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """选择集里出现过的 (def_id, 显示名)，按名称排序去重。"""
    defs = defs or {}
    seen: Dict[str, str] = {}
    for fid in ids or []:
        fx = fixtures_by_id.get(str(fid))
        if fx is None:
            continue
        did = fixture_def_id(fx)
        if did and did not in seen:
            fd = defs.get(did)
            seen[did] = str(getattr(fd, "name", "") or did)
    return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))


# ── Panorama Alignment（项 6）──────────────────────────────────────────────

def _raw_pos_x(fid: str, positions: Optional[Dict[str, Any]], fixtures_by_id) -> Any:
    """按"TopView 真源优先、fixture.pos_x 兜底"取原始 x（未解析/未校验）。"""
    item = (positions or {}).get(str(fid))
    if isinstance(item, (tuple, list)) and item:
        return item[0]
    if item is not None and not isinstance(item, (int, float)):
        raw = getattr(item, "x", None)
        if raw is not None:
            return raw
    fx = (fixtures_by_id or {}).get(str(fid))
    return getattr(fx, "pos_x", None) if fx is not None else None


def pos_x_of(fid: str, positions: Optional[Dict[str, Any]],
             fixtures_by_id: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """设备 x 坐标：优先 TopView 真源 `positions`，回退 `fixture.pos_x`。

    缺失/非法/NaN/inf → None（该设备排序不可用，由调用方排到末尾）。
    """
    raw = _raw_pos_x(fid, positions, fixtures_by_id)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val or val in (float("inf"), float("-inf")):
        return None
    return val


def sort_ids_by_pos_x(ids: Sequence[str], positions: Optional[Dict[str, Any]] = None,
                      fixtures_by_id: Optional[Dict[str, Any]] = None) -> Tuple[List[str], int]:
    """按 pos_x 升序排设备序列（稳定；缺坐标者排末尾且保持原序）。

    返回 `(有序 id, 缺坐标台数)`。
    """
    keyed: List[Tuple[Optional[float], int, str]] = []
    missing = 0
    for idx, fid in enumerate(str(x) for x in (ids or [])):
        if not fid:
            continue
        x = pos_x_of(fid, positions, fixtures_by_id)
        if x is None:
            missing += 1
        keyed.append((x, idx, fid))
    with_x = sorted((k for k in keyed if k[0] is not None), key=lambda k: (k[0], k[1]))
    without_x = [k for k in keyed if k[0] is None]
    return [k[2] for k in (with_x + without_x)], missing


# ── Step 步进（项 4）───────────────────────────────────────────────────────

def wrap_index(index: int, total: int) -> int:
    """索引环绕进 [0, total)；total<=0 → -1。"""
    if total <= 0:
        return -1
    try:
        i = int(index)
    except (TypeError, ValueError):
        i = 0
    return i % total


def step_target_index(current: int, total: int, delta: int) -> int:
    """组内前后步进一格（越界环绕）；total<=0 → -1。"""
    if total <= 0:
        return -1
    try:
        d = int(delta)
    except (TypeError, ValueError):
        d = 0
    return wrap_index(wrap_index(current, total) + d, total)


# ── hover 值气泡（项 16）──────────────────────────────────────────────────

def slot_index_for_x(x: float, width: float, total_slots: int) -> int:
    """条带横坐标 → 扇区 slot 下标（线性等分，越界钳制）；不可映射 → -1。"""
    try:
        n = int(total_slots)
    except (TypeError, ValueError):
        return -1
    if n <= 0:
        return -1
    try:
        w, px = float(width), float(x)
    except (TypeError, ValueError):
        return -1
    if w <= 0.0:
        return -1
    t = max(0.0, min(1.0, px / w))
    return min(n - 1, max(0, int(t * n)))


# ── 组内随机抖动（项 10）──────────────────────────────────────────────────

def jitter_amount(pct: Any) -> float:
    """抖动百分比(0..100) → 满量程幅度（±DMX 级）；0/坏值 → 0.0（关闭）。"""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0.0:
        return 0.0
    return min(100.0, p) / 100.0 * DMX_MAX


def jitter_value(value: float, pct: Any, rand_fn: Callable[[float, float], float]) -> float:
    """写入值加均匀抖动 ±d（d=pct%×255），钳 0..255；pct<=0 → 原值逐字节不变。"""
    v = float(value)
    d = jitter_amount(pct)
    if d <= 0.0:
        return v
    return max(0.0, min(DMX_MAX, v + float(rand_fn(-d, d))))


# ── 摇杆路径录制 → 轨迹样本（项 12）───────────────────────────────────────

#: 轴名 → 样本下标（样本形如 (t, x, y) 或 (t, x, y, z)）。
JOYSTICK_AXES = {"x": 1, "y": 2, "z": 3}


def _axis_points(samples: Sequence[Sequence[float]], idx: int) -> List[Tuple[float, float]]:
    """(t, 轴值) 点列；畸形/越界样本跳过，轴值钳 0..1。"""
    pts: List[Tuple[float, float]] = []
    for s in samples or []:
        try:
            pts.append((float(s[0]), max(0.0, min(1.0, float(s[idx])))))
        except (TypeError, ValueError, IndexError):
            continue
    return pts


def joystick_axis_samples(samples: Sequence[Sequence[float]], axis: str = "x") -> List[List[float]]:
    """摇杆录制样本 → 单轴 `[[相对t, v], ...]`（t 归一 0..1，v 钳 0..1）。

    畸形样本跳过；<2 点 → 空列表（调用方给提示，不抛）。
    """
    idx = JOYSTICK_AXES.get(str(axis).lower())
    if idx is None:
        return []
    pts = _axis_points(samples, idx)
    if len(pts) < 2:
        return []
    t0, span = pts[0][0], pts[-1][0] - pts[0][0]
    if span <= 1e-9:
        return [[0.0, pts[0][1]], [1.0, pts[-1][1]]]
    out: List[List[float]] = []
    last_x = -1.0
    for t, v in pts:
        x = max(0.0, min(1.0, (t - t0) / span))
        if x <= last_x:            # 时间轴严格单调（浮点并列微量前推）
            x = min(1.0, last_x + 1e-6)
        out.append([x, v])
        last_x = x
    return out


def axis_variation(samples: Sequence[Sequence[float]], axis: str = "x") -> float:
    """某轴录制极差（max-min）；样本不足 → 0.0（用于挑选"动过的那一轴"）。"""
    idx = JOYSTICK_AXES.get(str(axis).lower())
    if idx is None:
        return 0.0
    vals: List[float] = []
    for s in samples or []:
        try:
            vals.append(float(s[idx]))
        except (TypeError, ValueError, IndexError):
            continue
    return (max(vals) - min(vals)) if len(vals) >= 2 else 0.0


def pick_primary_axis(samples: Sequence[Sequence[float]],
                      candidates: Sequence[str] = ("x", "y", "z")) -> str:
    """录制样本里"动得最多"的那一轴（并列按 candidates 顺序）。"""
    best_axis, best_span = "", -1.0
    for axis in candidates:
        span = axis_variation(samples, axis)
        if span > best_span:
            best_axis, best_span = str(axis), span
    return best_axis or "x"


# ── 滚轮相对编码（项 9）────────────────────────────────────────────────────

def wheel_delta(modifiers: Any, invert: bool = False) -> int:
    """滚轮修饰键 → 增量：裸轮 ±1、Shift ±10、Ctrl ±10（gMA3 编码器粗细两档）。

    `modifiers` 多为 Qt.KeyboardModifier；也接受裸 int 位掩码（0 = 无修饰）。
    ★ PyQt6 的枚举**不可 `int()`**（`TypeError`），须读 `.value`，故此处两级兜底。
    """
    from PyQt6.QtCore import Qt as _Qt
    try:
        mods = modifiers if isinstance(modifiers, _Qt.KeyboardModifier) else _Qt.KeyboardModifier(
            int(getattr(modifiers, "value", modifiers)))
    except (TypeError, ValueError):
        mods = _Qt.KeyboardModifier.NoModifier
    shift = _Qt.KeyboardModifier.ShiftModifier
    ctrl = _Qt.KeyboardModifier.ControlModifier
    step = 10 if (mods & shift or mods & ctrl) else 1
    return -step if invert else step


def shift_range(state: Tuple[str, int, int], delta: int) -> Tuple[str, int, int]:
    """条带形状整体平移 delta 级（钳 0..255），保持 mode 与形状。"""
    mode, base, target = state
    lo, hi = min(int(base), int(target)), max(int(base), int(target))
    d = int(delta)
    d = max(d, -lo)
    d = min(d, int(DMX_MAX) - hi)
    return str(mode), int(base) + d, int(target) + d


# ── 摇杆路径 → 轨迹曲线（项 12）───────────────────────────────────────────

def _fill_trajectory_points(tr: Any, reduced: List[List[float]]) -> None:
    """把抽稀点写进曲线（优先 CurveMapping；不可用时回退纯点列）。"""
    try:
        from ..core.models import CurveMapping, CurvePoint
        tr.curve = CurveMapping(points=[CurvePoint(float(x), float(y)) for x, y in reduced],
                                source="recorded")
        tr.points = []
    except Exception:
        tr.curve = None
        tr.points = [[float(x), float(y)] for x, y in reduced]


def _show_trajectory_in_panel(panel: Any, tr: Any) -> None:
    """轨迹页选中新曲线（列表/绘图/子页切换；任一步失败不影响其余）。"""
    panel._traj_selected_id = tr.id
    try:
        panel._traj_refresh_list()
        if hasattr(panel, "_traj_view"):
            panel._traj_view.set_trajectory(tr)
    except Exception:
        pass
    try:
        tabs = getattr(panel, "_sub_tabs", None)
        for i in range(tabs.count()):
            if tabs.tabText(i) == "轨迹曲线":
                tabs.setCurrentIndex(i)
                break
    except Exception:
        pass


def send_joystick_path_to_trajectory(panel: Any, samples: Sequence[Sequence[float]],
                                     axis: str = "",
                                     duration: Optional[float] = None) -> str:
    """摇杆录制样本 → RDP 抽稀 → 生成路径曲线并载入轨迹页（返回曲线 id）。

    链路全部复用既有件：`joystick_axis_samples`（单轴折取）→
    `trajectory_reduce.reduce_recorded_samples`（RDP 抽稀，R180）→
    `trajectory_curve.get_trajectory_curve_store().create(source="recorded")` →
    轨迹页刷新 + 绘图载入。样本不足 / 任何异常 → "" + 状态提示（安全降级）。
    """
    from .programmer_highlight import status as _status
    try:
        axis = str(axis or pick_primary_axis(samples)).lower()
        cleaned = joystick_axis_samples(samples, axis)
        if len(cleaned) < 2:
            _status(panel, "摇杆路径：样本不足（至少点动一下摇杆再停录）")
            return ""
        reduced = _reduce_joystick_samples(cleaned)
        from .trajectory_curve import CURVE_TYPE_PATH, get_trajectory_curve_store
        tr = get_trajectory_curve_store().create(name=f"摇杆路径 {len(reduced)}点",
                                                duration=float(duration) if duration else 4.0,
                                                source="recorded", curve_type=CURVE_TYPE_PATH)
        _fill_trajectory_points(tr, reduced)
        _show_trajectory_in_panel(panel, tr)
        _status(panel, f"摇杆路径已送入轨迹页：{tr.name}（{len(reduced)} 点，轴 {axis.upper()}）")
        return tr.id
    except Exception:
        _status(panel, "摇杆路径：生成轨迹失败（已安全降级，未改动任何数据）")
        return ""


def _reduce_joystick_samples(cleaned: List[List[float]]) -> List[List[float]]:
    """RDP 抽稀（复用 R180 既有模块）；抽稀后少于 2 点则回退原样本。"""
    try:
        from .trajectory_reduce import DEFAULT_RDP_EPSILON, reduce_recorded_samples
        reduced = reduce_recorded_samples(cleaned, DEFAULT_RDP_EPSILON)
    except Exception:
        reduced = []
    return reduced if len(reduced) >= 2 else cleaned
