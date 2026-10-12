# -*- coding: utf-8 -*-
"""监视器选中通道 → 效果作用域（R183 W2；深查② §四 #17）。

需求原文：「过滤集合与效果 scope 打通："监视器选中的通道 → 效果作用域"
（FilterNumpad.get_channels → EB `_scoped_key`/subset 注入；按钮 R55 起
隐藏——核实现状，若破坏"用户极简定案"则改为右键菜单入口，报告说明）」。

现状核实（2026-09-28，读码）：
  * R55 用户点名"顶栏大精简"，输入/输出过滤双钮在
    ``monitor_legacy_buttons.build_hidden_buttons`` 里 ``hide()`` 保活；
    monitor_panel 也未保留该行的可见入口。
  * 故**不新增顶栏按钮**（尊重极简定案），作用域打通以右键菜单项
    "→ 送到效果作用域" 落地（见 ``monitor_actions``）。

映射语义：渠道 → 设备 id 列表（该域内地址区间与选中集相交的设备），
顺序按起始通道升序；设备 id 交给 EB 的 ``_apply_playback_scope``
（纯播放语义：只设引擎读取的作用域字段，不动用户编辑态，
见 ``src/ui/effect_browser.py:9240``）。
"""
from __future__ import annotations

from .monitor_groups import fixture_def_index, fixture_span

# 惰性缓存的 EB 作用域写入函数（首次使用时才 import 9019 行大模块）
_apply_scope_impl = None


def fixture_ids_for_channels(fixtures, defs, universe: int, channels) -> list:
    """纯函数：选中通道 → 相交设备 id（按起始通道升序，去重）。

    相交判据：设备地址区间 [start, start+count-1] 与选中集有交集。
    """
    wanted = {int(c) for c in (channels or ()) if 1 <= int(c) <= 512}
    if not wanted:
        return []
    hits = _span_hits(fixtures, defs, universe, wanted)
    hits.sort(key=lambda item: item[0])
    return [fid for _s, fid in hits]


def _span_hits(fixtures, defs, universe: int, wanted: set) -> list:
    """[(起始通道, 设备id)]：该域内地址区间与 wanted 相交的设备。"""
    index = fixture_def_index(defs)
    hits = []
    for fx in fixtures or ():
        if not _same_universe(fx, universe):
            continue
        span = fixture_span(fx, index)
        fid = str(getattr(fx, "id", "") or "")
        if span is None or not fid:
            continue
        if wanted & set(range(span[0], span[1] + 1)):
            hits.append((span[0], fid))
    return hits


def _same_universe(fixture, universe: int) -> bool:
    """设备是否属于该域（脏值按不匹配处理）。"""
    try:
        return int(getattr(fixture, "universe", 0) or 0) == int(universe)
    except (TypeError, ValueError):
        return False


def resolve_effect_browser(host):
    """从宿主（面板/主窗）解析 Effect Browser 控件；缺失返回 None。

    主窗持有 ``_effect_browser_widget``（懒加载面板）；测试可注入任意带该
    属性的宿主对象。QWidget 宿主先查自身再查 ``window()``。
    """
    direct = getattr(host, "_effect_browser_widget", None)
    if direct is not None:
        return direct
    try:
        top = host.window()
    except Exception:
        return None
    direct = getattr(top, "_effect_browser_widget", None) if top is not None else None
    if direct is not None:
        return direct
    from ..workspace_owner import find_project_window
    return getattr(find_project_window(host), "_effect_browser_widget", None)


def _apply_scope(eb, ids) -> None:
    """写 EB 作用域（惰性 import 大模块；EB 与主窗同进程，无反向依赖问题）。"""
    global _apply_scope_impl
    if _apply_scope_impl is None:
        from ..effect_browser import _apply_playback_scope as impl
        _apply_scope_impl = impl
    _apply_scope_impl(eb, list(ids))


def push_channels_to_scope(host, channels, fixtures, defs, universe: int) -> int:
    """把选中通道夹到的设备集写进 EB 作用域；返回生效设备数（0=未生效）。

    两步注入（对应任务书 "_scoped_key/subset 注入"）：
      ① 作用域 = 映射设备集（``_apply_playback_scope``，纯播放语义）；
      ② 子集标记 = 同一集合 ∩ 作用域（``_selected_group_subset_ids``）——
         已有 "子集 delay 相位" 才起作用（``effect_engine.subset_split_enabled``
         要求相位 ≠ 0 + 子集非空），相位为 0 时全路径逐字节不变。
    """
    ids = fixture_ids_for_channels(fixtures, defs, universe, channels)
    if not ids:
        return 0
    eb = resolve_effect_browser(host)
    if eb is None:
        return 0
    try:
        _apply_scope(eb, ids)
        _mark_subset(eb, ids)
    except Exception:
        return 0
    return len(ids)


def _mark_subset(eb, ids: list) -> None:
    """标记子集 id（限在刚写入的作用域内，越界 id 会让引擎分组表算错）。"""
    scope = {str(x) for x in (getattr(eb, "_selected_group_ids", None) or [])}
    eb._selected_group_subset_ids = [str(i) for i in ids if not scope
                                     or str(i) in scope]
