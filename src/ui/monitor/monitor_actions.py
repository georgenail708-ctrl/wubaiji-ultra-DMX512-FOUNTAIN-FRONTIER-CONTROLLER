# -*- coding: utf-8 -*-
"""监视器右键菜单与选区动作（R183 W2；深查② §四 #9/#10/#17）。

需求原文：「右键菜单：选中集 → 复制值 / 写入过滤（复用 `_on_numpad_rule`）/
送轨道（复用 `_send_to_track`）」；并承载 #10 blink 与 #17 作用域两个
入口（#17 的现状核实见 ``monitor_scope`` 模块头：R55 用户点名顶栏精简，
不复活隐藏按钮，改由本菜单进入）。

设计：菜单构建与动作实现分离——**动作函数**都是纯宿主调用（无 QMenu），
QMenu 装配只在 ``build_context_menu`` 内，且延迟到 exec 前才创建
（offscreen 测试直调动作函数即可全覆盖）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QMenu

from .monitor_export import build_csv_text
from .monitor_scope import push_channels_to_scope

MENU_COPY_VALUES = "复制选中通道值"
MENU_WRITE_FILTER = "写入过滤（小键盘）"
MENU_TO_TRACK = "送为轨道录制范围"
MENU_SCOPE = "→ 送到效果作用域"
MENU_BLINK = "Blink 定位（2s）"
MENU_EXPORT = "导出当前域 CSV…"


def selection_channels(panel) -> set:
    """当前"监视器选中集合"（#17 真源）。

    优先取网格选择集（右键/框选/键盘都写这里）；网格为空时回退
    ``FilterNumpad.get_channels()``（小键盘表达式解析结果——用户按 ENTER
    后若网格未同步，过滤集合仍是唯一意图表达）。
    """
    grid = getattr(panel, "_channel_grid", None)
    picked = set(getattr(grid, "get_selected_channels", lambda: set())())
    if picked:
        return picked
    numpad = getattr(panel, "_numpad", None)
    getter = getattr(numpad, "get_channels", None)
    return set(getter()) if callable(getter) else set()


def channels_text(channels, data) -> str:
    """纯函数：选中集 + 显示帧 → 多行 "ch<TAB>value" 文本（升序）。"""
    if data is None:
        return ""
    rows = []
    for ch in sorted({int(c) for c in (channels or ()) if 1 <= int(c) <= len(data)}):
        rows.append(f"{ch}\t{int(data[ch - 1])}")
    return "\n".join(rows)


def copy_channels_to_clipboard(channels, data) -> int:
    """复制选中通道值到系统剪贴板；返回复制行数（无剪贴板/空集返回 0）。

    只写文本，不写文件、不发网络 —— 与 AGENTS.md「不主动外发」一致：
    剪贴板是用户本机剪贴板，非外部通道。
    """
    text = channels_text(channels, data)
    if not text:
        return 0
    try:
        cb = QApplication.clipboard()
        if cb is None:
            return 0
        cb.setText(text)
        return text.count("\n") + 1
    except Exception:
        return 0


def write_filter_from_selection(panel, channels) -> int:
    """写入选定方向过滤（复用 ``_on_numpad_rule`` 同步语义）；返回通道数。"""
    chans = set(channels or ())
    if not panel or not chans:
        return 0
    slot = getattr(panel, "_on_numpad_rule", None)
    if callable(slot):
        slot(chans)
    return len(chans)


def send_selection_to_track(panel, channels) -> int:
    """送轨道范围（复用 ``_send_to_track`` 信号路径）；返回通道数。"""
    chans = set(channels or ())
    if not panel or not chans:
        return 0
    slot = getattr(panel, "_send_to_track", None)
    if callable(slot):
        slot()
    return len(chans)


def push_selection_to_effect_scope(panel, channels, universe=None) -> int:
    """选中集 → EB 作用域；返回生效设备数（0=无映射/无 EB）。"""
    pm = getattr(panel, "_pm", None)
    proj = getattr(pm, "project", None) if pm is not None else None
    if proj is None:
        return 0
    uni = int(universe if universe is not None
              else getattr(panel, "_current_universe", 1))
    return push_channels_to_scope(
        panel, channels, getattr(proj, "fixtures", None) or [],
        getattr(proj, "fixture_defs", None) or [], uni)


def start_blink(panel, channels) -> bool:
    """启动选中集 blink（2s 自动停）；返回是否已启动。"""
    controller = getattr(panel, "_blink", None)
    stopper = getattr(controller, "stop", None)
    if callable(stopper):
        stopper()                     # 再次触发 = 重启（先停旧的）
    maker = getattr(panel, "_make_blink", None)
    if not callable(maker):
        return False
    ctrl = maker(set(channels or ()))
    if ctrl is None:
        return False
    started = bool(ctrl.start())
    if not started:
        return False
    panel._blink = ctrl
    hook = getattr(panel, "_blink_started", None)   # 宿主挂定时器/反馈
    if callable(hook):
        hook(ctrl)
    return True


def export_current_domain(panel) -> str:
    """导出当前域 CSV（走 panel._do_export_csv 装配；无则空串）。"""
    slot = getattr(panel, "_do_export_csv", None)
    if not callable(slot):
        return ""
    try:
        return str(slot() or "")
    except Exception:
        return ""


# ── QMenu 装配（仅 UI 层；延迟到弹出时调用）─────────────────

def build_context_menu(panel, grid, global_pos) -> QMenu:
    """构建右键菜单并 exec；返回已执行的菜单（测试可传入不 exec 的包装）。"""
    channels = selection_channels(panel)
    menu = QMenu(grid)
    n = len(channels)
    head = menu.addAction(f"已选 {n} 个通道" if n else "未选择通道")
    head.setEnabled(False)
    menu.addSeparator()
    act_copy = menu.addAction(MENU_COPY_VALUES)
    act_filter = menu.addAction(MENU_WRITE_FILTER)
    act_track = menu.addAction(MENU_TO_TRACK)
    act_scope = menu.addAction(MENU_SCOPE)
    act_blink = menu.addAction(MENU_BLINK)
    menu.addSeparator()
    act_export = menu.addAction(MENU_EXPORT)
    for act in (act_copy, act_filter, act_track, act_scope, act_blink):
        act.setEnabled(bool(n))
    # 动作直连（offscreen 下不 exec：测试调 *_for_menu 系列函数即可覆盖）
    act_copy.triggered.connect(
        lambda: copy_channels_to_clipboard(channels, grid.get_data()))
    act_filter.triggered.connect(lambda: write_filter_from_selection(panel, channels))
    act_track.triggered.connect(lambda: send_selection_to_track(panel, channels))
    act_scope.triggered.connect(lambda: push_selection_to_effect_scope(panel, channels))
    act_blink.triggered.connect(lambda: start_blink(panel, channels))
    act_export.triggered.connect(lambda: export_current_domain(panel))
    menu.exec(global_pos)
    return menu


def csv_preview(data, universe: int = 1, snapshot=None) -> str:
    """导出前预览文本（测试/调试用；与写盘内容同源）。"""
    return build_csv_text(data, universe, snapshot)
