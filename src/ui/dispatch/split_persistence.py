# -*- coding: utf-8 -*-
"""调度台分界持久化与装配（R183 A8；修复轮 C2「依然挡住」）—— 纯逻辑 + 薄接线。

A8 已做：① 双区显式最小宽；② 初始比例 62/38；③ 手拖分界持久化。
用户第三次实报"依然挡住"的剩余根因（C2）：
  * 存档存的是**绝对像素** ``[left, right]``：在宽窗口拖好的分界，到窄窗口/停靠
    状态被原样 ``setSizes`` → 右存储区保持旧像素宽，左监控被压到被遮挡；
  * 之后窗口再缩放，QSplitter 只按 stretch 分配增量，从不回到合理比例。
修法：存档改存**比例**（旧像素档自动换算）；装一个 Resize 事件过滤器，任何尺寸
变化都按比例重新落位，并钳制「左监控 ≥ 50% 且 ≥ 360px、右存储 ≥ 280px」——
1280×720 下两区都完整可见（左 ≈ 790 / 右 ≈ 490）。
"""
from __future__ import annotations

_SPLIT_SETTINGS_KEY = "dispatch/split_sizes"      # 旧像素档（只读兼容 + 双写）
_RATIO_SETTINGS_KEY = "dispatch/split_ratio"
#: 双区最小宽：左监控（表格不横向滚动）/ 右存储（槽位网格可用）。
MONITOR_MIN_W = 360
STORE_MIN_W = 280
DEFAULT_RATIO = 0.62
#: 左监控占比下限：存储区永远不许挤掉监控区的一半以上
MIN_LEFT_RATIO = 0.5
_FALLBACK_W = 1180


def clamp_sizes(total: int, ratio: float) -> list:
    """按比例分配 total 并钳制最小宽/左区占比下限（纯函数，可单测）。

    R188-④：可行性收缩——面板实际宽 < 双区最小宽和时，**先等比收缩两区最小宽**
    再分配（保两者俱在），修前 t 被抬到 646 而 QSplitter 实际只有 624：槽位
    分配与子控件 minimumWidth 冲突 → left 几何 360/right 起始 344 **重叠 16px**，
    右存储区（后建=z 序在上）盖住监控表格最右列——用户实报"隔断挡住监控区格子"
    （真实 DPR=2.0 截图实锤）。
    """
    t = max(int(total), 2)
    lo, hi = MONITOR_MIN_W, STORE_MIN_W
    if t < lo + hi:                      # 可行性收缩（面板过窄时两区俱在优先）
        scale = t / float(lo + hi)
        lo = max(40, int(lo * scale))
        hi = max(40, int(hi * scale))
        if lo + hi > t:                  # 极端窄：直接按比例劈开，保证不出负尺寸
            lo = max(1, int(t * 0.55))
            hi = max(1, t - lo)
    r = min(1.0, max(MIN_LEFT_RATIO, float(ratio)))
    left = int(round(t * r))
    left = max(lo, min(left, t - hi))
    left = max(1, min(left, t - 1))      # 最终钳制：两侧恒 ≥1px（修前窄帧可出负数）
    return [left, t - left]


def _parse_sizes(v) -> list:
    try:
        return [int(x) for x in (v if isinstance(v, (list, tuple)) else str(v).split(","))]
    except (TypeError, ValueError):
        return []


def _load_ratio():
    """读比例存档；无则由旧像素档换算；都无 → None。"""
    try:
        from PyQt6.QtCore import QSettings
        s = QSettings("Qascade", "MainWindow")
        v = s.value(_RATIO_SETTINGS_KEY)
        if v is not None:
            return float(v)
        sizes = _parse_sizes(s.value(_SPLIT_SETTINGS_KEY) or "")
    except Exception:
        return None
    if len(sizes) == 2 and min(sizes) > 0:
        return sizes[0] / float(sum(sizes))
    return None


def available_width(split) -> int:
    """Child budget excludes the handles consumed by Qt's splitter layout."""
    total = int(split.contentsRect().width() or 0) or _FALLBACK_W
    handles = sum(split.handle(i).sizeHint().width() for i in range(1, split.count()))
    return max(2, total - handles)


def apply_ratio(split) -> None:
    """按当前比例把分界落到真实宽度（宽度未知时按 1180 粗排）。"""
    total = available_width(split)
    split.setSizes(clamp_sizes(total, getattr(split, "_q_split_ratio", DEFAULT_RATIO)))


def restore_splitter(split) -> bool:
    """恢复分界比例；返回 True=用了存档（False → 缺省 62/38）。"""
    ratio = _load_ratio()
    split._q_split_ratio = DEFAULT_RATIO if ratio is None else float(ratio)
    apply_ratio(split)
    return ratio is not None


def save_splitter(split) -> None:
    """持久化分界（拖动结束调用；比例 + 旧像素键双写；异常静默）。"""
    s = split.sizes()
    if len(s) != 2 or min(s) <= 0:
        return
    split._q_split_ratio = s[0] / float(sum(s))
    try:
        from PyQt6.QtCore import QSettings
        settings = QSettings("Qascade", "MainWindow")
        settings.setValue(_RATIO_SETTINGS_KEY, float(split._q_split_ratio))
        settings.setValue(_SPLIT_SETTINGS_KEY, [int(s[0]), int(s[1])])
    except Exception:
        return


def _install_ratio_keeper(split, left=None, right=None) -> None:
    """Resize 事件过滤器：窗口/停靠尺寸任何变化都按比例重新落位（C2 根治）。

    R188-④：同时刷新两区动态最小宽（消除窄面板重叠遮挡）。
    """
    try:
        from PyQt6.QtCore import QEvent, QObject
    except Exception:
        return
    left_holder = [left]
    right_holder = [right]

    class _RatioKeeper(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            if ev.type() == QEvent.Type.Resize and obj.width() > 0:
                # R188-④：任何宽度变化先更新动态最小宽，再按比例落位
                _sync_dynamic_min(obj, left_holder[0], right_holder[0])
                apply_ratio(obj)
            return False

    keeper = _RatioKeeper(split)
    split.installEventFilter(keeper)
    split._q_ratio_keeper = keeper   # 强引用（父对象也持有；双保险防回收）


def _sync_dynamic_min(split, left, right) -> None:
    """R188-④：按 split 当前宽动态设两区最小宽（窄面板收缩，杜绝重叠）。

    修前固定 360/280：面板一旦窄于 646 就几何重叠（右区盖住左区右缘）。
    """
    try:
        total = available_width(split)
    except Exception:
        return
    if total <= 0:
        return
    lo, hi = MONITOR_MIN_W, STORE_MIN_W
    if total < lo + hi:
        scale = total / float(lo + hi)
        lo = max(120, int(lo * scale))
        hi = max(96, int(hi * scale))
    left.setMinimumWidth(lo)
    right.setMinimumWidth(hi)


def wire_splitter(split, left, right) -> bool:
    """A8/C2/R188 分界装配：动态最小宽 + 弹性 + 比例存档 + 尺寸守比例 + 拖动落盘。"""
    _sync_dynamic_min(split, left, right)
    split.setStretchFactor(0, 3)
    split.setStretchFactor(1, 2)
    has_saved = restore_splitter(split)
    _install_ratio_keeper(split, left, right)
    split.splitterMoved.connect(lambda *_: save_splitter(split))
    return has_saved
