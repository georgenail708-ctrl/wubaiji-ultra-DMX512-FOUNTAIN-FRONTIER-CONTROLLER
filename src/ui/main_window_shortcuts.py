# -*- coding: utf-8 -*-
"""主窗快捷键动作（R176 原设计，R178 以独立模块重做，CLAUDE.md §10.1 上帝类冻结）。

`install_shortcuts(win)` 在 `MainWindow.__init__` 末尾调用一次；菜单项通过
`menuBar().findChildren(QMenu)` 按标题定位后动态 addAction（**不动** `_build_menu`，
它已回基线行数）。动作实现里的 4 个关键修正（旧实现是坏的）：

  * 复制：走 `timeline_widget._selected_clip_ids()`（`_selected_clips` 属性不存在），
    后处理照抄 `_on_clip_duplicated_request`（逐 clip 失效引擎缓存 + 置脏）。
  * 分割：走 `ctrl.razor_cut_clip(clip_id, 绝对时间)`（手工 duplicate+改时长会丢
    右片段 0 秒边界帧）；多选先 `_snapshot_for_undo()` 一次（与 duplicate_many 同口径）。
  * 文本焦点守卫：焦点在 QLineEdit/QTextEdit/QPlainTextEdit 时单键动作直接跳过
    （否则在搜索框打字会触发剪辑操作）。
  * 撤销/重做置灰：挂 `edit_menu.aboutToShow` 刷新（菜单打开才算，零信号开销）。

★ R182 P0（菜单跳转断链）：旧 `_act` 用 `win = menu.window()` 取宿主 —— QMenu 的
  `window()` 返回 **QMenu 自身**（不是 MainWindow），于是跳转动作把 QMenu 传给
  `jump_to_panel` → `getattr(menu, "_programmer_dock")` 恒为 None → 静默 return，
  所有经 `_act` 的动作（跳转×9 / 复制 / 查找 / 关闭面板 / 循环 / 录制 / 分割）
  全部失效或对错误对象操作。现在 `install_shortcuts` 收到的真 `win` 显式贯穿。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QLineEdit, QMenu, QTextEdit, QPlainTextEdit

log = logging.getLogger(__name__)

# 面板跳转顺序（Ctrl+1..9；索引即快捷键序号-1）
_JUMP_DOCKS = (
    "_programmer_dock", "_topview_dock", "_effect_browser_dock",
    "_dispatch_dock", "_fixture_group_dock", "_patch_dock",
    "_matrix_pattern_dock", "_clip_studio_dock", "_network_dock",
)
# 循环切换覆盖面（比跳转多监视器/关联器/节目管理）
_CYCLE_DOCKS = _JUMP_DOCKS + ("_monitor_dock", "_processor_dock", "_program_dock")
# 文本输入控件：焦点在其中时单键快捷键不劫持
_TEXT_WIDGETS = (QLineEdit, QTextEdit, QPlainTextEdit)


def _text_focus() -> bool:
    """当前焦点是否在文本输入控件上（是 → 单键动作让路给打字）。"""
    from PyQt6.QtWidgets import QApplication
    focus = QApplication.focusWidget()
    return isinstance(focus, _TEXT_WIDGETS)


# ── 动作实现（win 为 MainWindow，全部模块级函数，不进上帝类）────────────

def toggle_record(win) -> None:
    """切换录制状态 (R)。"""
    if _text_focus():
        return
    transport = getattr(win, "_transport", None)
    if transport is not None:
        btn = getattr(transport, "_rec_btn", None)
        if btn is not None:
            btn.setChecked(not btn.isChecked())


def duplicate_selected(win) -> None:
    """复制选中的 Clip (Ctrl+D)。守卫+执行拆分（压 CC）。"""
    if _duplicate_guard(win):
        return
    _duplicate_exec(win)


def _duplicate_guard(win) -> bool:
    """True=应跳过（文本焦点/无选中/时间线不可用——均已提示）。"""
    if _text_focus():
        return True
    tw = getattr(win, "_timeline_widget", None)
    if not getattr(win, "_use_timeline_widget", False) or tw is None:
        win._status.set_message("请先选中要复制的项目")
        return True
    if not tw._selected_clip_ids():
        win._status.set_message("请先选中要复制的项目")
        return True
    return False


def _duplicate_exec(win) -> None:
    tw = win._timeline_widget
    ctrl = tw.ctrl
    try:
        from .timeline.timeline_perf import duplicate_many
        created = duplicate_many(ctrl, list(tw._selected_clip_ids()))
        if not created:
            return
        for cid in created:
            tw._invalidate_engine_cache(cid)
        tw._mark_project_dirty()
    except Exception as e:
        win._status.set_message(f"复制失败: {e}")


def _split_context(win):
    """分割前置：返回 (tw, ctrl, ids)；时间线不可用/无选中/文本聚焦 → None（已提示）。"""
    if _text_focus():
        return None
    tw = getattr(win, "_timeline_widget", None)
    if not getattr(win, "_use_timeline_widget", False) or tw is None:
        win._status.set_message("时间线不可用")
        return None
    ctrl = getattr(tw, "ctrl", None)
    if ctrl is None:
        return None
    ids = tw._selected_clip_ids()
    if not ids:
        win._status.set_message("请先选中要分割的 Clip")
        return None
    return tw, ctrl, ids


def split_clip_at_playhead(win) -> None:
    """在播放位置分割选中的 Clip (S)——razor_cut_clip 吃绝对时间且自带边界帧。"""
    ctx = _split_context(win)
    if ctx is None:
        return
    tw, ctrl, ids = ctx
    try:
        pos = float(getattr(ctrl, "_play_pos", 0.0) or 0.0)
        ctrl._snapshot_for_undo()
        for clip_id in list(ids):
            ctrl.razor_cut_clip(clip_id, pos)
        if hasattr(tw, "_reload_tracks"):
            tw._reload_tracks()
    except Exception as e:
        win._status.set_message(f"分割失败: {e}")


def global_find(win) -> None:
    """全局查找 (Ctrl+F)：打开节目管理并聚焦搜索框。"""
    # ★ 审计 B/P2-1：搜索框在主窗自身（self._program_search，:3801），
    #   不在节目面板 widget 内（旧实现找错对象 → 永远退化为状态栏提示）。
    box = getattr(win, "_program_search", None)
    if box is not None:
        try:
            win._toggle_dock(win._program_dock, True)
            box.setFocus()
            box.selectAll()
            return
        except Exception:
            log.debug("global_find: 节目管理聚焦失败", exc_info=True)
    win._status.set_message("查找功能：在节目管理面板中搜索")


def _focused_dock(win):
    """焦点控件沿祖先链找所属 QDockWidget（审计 M3：isActiveWindow 对 dock
    恒等于主窗激活态，永远命中第一个可见 dock——必须用焦点链定位）。"""
    from PyQt6.QtWidgets import QApplication, QDockWidget
    w = QApplication.focusWidget()
    while w is not None:
        cur = w
        while cur is not None:
            if isinstance(cur, QDockWidget):
                return cur
            cur = cur.parentWidget()
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
    return None


def close_current_panel(win) -> None:
    """关闭当前活动的浮动面板 (Ctrl+W)：优先焦点所在 dock，无则退回首可见。"""
    docks = [getattr(win, a, None) for a in (
        "_program_dock", "_patch_dock", "_topview_dock", "_programmer_dock",
        "_matrix_pattern_dock", "_fixture_group_dock", "_effect_browser_dock",
        "_processor_dock", "_network_dock", "_monitor_dock")]
    focused = _focused_dock(win)
    if focused is not None and focused in docks and focused.isVisible():
        focused.setVisible(False)
        return
    visibles = [d for d in docks if d is not None and d.isVisible()]
    if visibles:
        visibles[0].setVisible(False)
        return
    win._status.set_message("没有打开的面板可关闭")


def jump_to_panel(win, index: int) -> None:
    """跳转到指定索引的面板（Ctrl+1-9）。找不到 dock 时明确提示不静默。"""
    if not 0 <= index < len(_JUMP_DOCKS):
        return
    dock = getattr(win, _JUMP_DOCKS[index], None)
    if dock is None:
        _notify(win, "面板不可用")
        return
    try:
        win._toggle_dock(dock, True)
    except Exception:
        log.warning("面板跳转失败: %s", _JUMP_DOCKS[index], exc_info=True)
        _notify(win, "面板不可用")


def _notify(win, msg: str) -> None:
    """状态栏提示（宿主无 `_status`/方法缺失的桩对象下静默）。"""
    status = getattr(win, "_status", None)
    if status is not None and hasattr(status, "set_message"):
        status.set_message(msg)


def cycle_panels(win) -> None:
    """循环切换到下一个面板 (Ctrl+Tab)。"""
    panels = [getattr(win, a, None) for a in _CYCLE_DOCKS]
    panels = [d for d in panels if d is not None]
    if not panels:
        return
    current = _active_index(panels)
    if current == -1:
        current = _first_visible(panels)
    if _focus_next(panels, (current + 1) % len(panels)):
        return
    win._toggle_dock(panels[0], True)


def _active_index(panels) -> int:
    """当前可见且活跃（最前/持焦）的面板下标；无则 -1。"""
    for i, dock in enumerate(panels):
        if dock.isVisible() and not dock.isHidden():
            if dock.isActiveWindow() or (dock.widget() and dock.widget().hasFocus()):
                return i
    return -1


def _first_visible(panels) -> int:
    """第一个可见面板下标；全不可见 -1。"""
    for i, dock in enumerate(panels):
        if dock.isVisible():
            return i
    return -1


def _focus_next(panels, start: int) -> bool:
    """自 start 起循环聚焦下一个可见面板；一圈全不可见返回 False。"""
    idx, attempts = start, 0
    while attempts < len(panels):
        dock = panels[idx]
        if dock.isVisible() and not dock.isHidden():
            dock.raise_()
            dock.activateWindow()
            if dock.widget():
                dock.widget().setFocus()
            return True
        idx = (idx + 1) % len(panels)
        attempts += 1
    return False


def show_shortcuts(win) -> None:
    """帮助 ▸ 键盘快捷键 (F1)。"""
    try:
        from .shortcuts_dialog import show_shortcuts_dialog
        show_shortcuts_dialog(win)
    except Exception:
        log.warning("打开快捷键对话框失败", exc_info=True)


# ── 撤销/重做状态刷新（aboutToShow 时算，零信号开销）──────────────────

def refresh_undo_redo(win) -> None:
    """刷新编辑菜单里撤销/重做动作的可用态（栈空则置灰）。"""
    pm = getattr(win, "_pm", None)
    undo = getattr(win, "_shortcut_undo_act", None)
    redo = getattr(win, "_shortcut_redo_act", None)
    if undo is None and redo is None:
        return
    can_undo = bool(getattr(pm, "_undo_stack", None))
    can_redo = bool(getattr(pm, "_redo_stack", None))
    if undo is not None:
        undo.setEnabled(can_undo)
        undo.setToolTip("撤销上一步操作" if can_undo else "无可撤销操作")
    if redo is not None:
        redo.setEnabled(can_redo)
        redo.setToolTip("重做已撤销的操作" if can_redo else "无可重做操作")


# ── 安装（一次调用；菜单按标题定位，不动 _build_menu）────────────────

def _find_menu(win, title: str) -> Optional[QMenu]:
    for menu in win.menuBar().findChildren(QMenu):
        if menu.title() == title:
            return menu
    return None


#: QAction 上记录"本模块已装此动作"的属性名（幂等判据）。
#: 值 = 首次安装时的规范文案（R180 会把「跳转到 programmer」改中文名，
#: 按 text 匹配会失配 → 必须用属性匹配）。
_INSTALLED_PROP = "_qascade_r176_text"
#: 「视图」块分隔条的标记值（重复安装不得再插一根）。
_SEP_MARK = "__r176_view_sep__"


def _existing_action(menu: QMenu, text: str) -> Optional[QAction]:
    """按安装属性/文本找菜单里已有的动作（撤改中文名后仍能命中 → 幂等不叠加）。"""
    for a in menu.actions():
        if a.property(_INSTALLED_PROP) == text:
            return a
    for a in menu.actions():
        if a.text() == text:
            return a
    return None


def _add_unique(menu: QMenu, text: str, key: str, slot, icon_key: str = "",
                *, win=None) -> Optional[QAction]:
    """已装则不动，未装才 `_act`（返回新动作；已存在返回 None）。"""
    if _existing_action(menu, text) is not None:
        return None
    return _act(menu, text, key, slot, icon_key, win=win)


def _act(menu: QMenu, text: str, key: str, slot, icon_key: str = "",
         *, win=None) -> QAction:
    """在 menu 上挂一个带快捷键的动作（slot 统一吃 win 单参）。

    `win` 必须是真 MainWindow（install_shortcuts 显式贯穿）；缺省回退
    `menu.window()` 仅为兼容外部旧调用 —— 注意 QMenu.window() 对无顶层窗口
    的菜单返回 QMenu 自身，**不能**当宿主用于 `getattr(win, "_xxx_dock")`。
    """
    host = win if win is not None else menu.window()
    try:   # 非 QObject 宿主（测试桩）→ 回退到菜单自身窗口，保证 QAction 可构造
        from PyQt6.QtCore import QObject
        if not isinstance(host, QObject):
            host = menu.window()
    except Exception:
        pass
    a = QAction(text, host)
    if icon_key:
        try:
            from .main_window import _panel_icon
            a.setIcon(_panel_icon(icon_key, 22))
        except Exception:
            pass
    a.setShortcut(QKeySequence(key))
    a.setToolTip(text)
    a.setProperty(_INSTALLED_PROP, text)
    if slot is not None:
        a.triggered.connect(lambda checked=False, s=slot, w=host: s(w))
    menu.addAction(a)
    return a


def install_shortcuts(win) -> None:
    """在既有菜单上补挂 R176 快捷键动作（幂等：重复调用不重复添加）。

    菜单尚未建好（如接线点在 `_build_menu` 之前）时，singleShot(0) 下一拍重试——
    初始化事件循环转起来后菜单必在。"""
    if getattr(win, "_shortcuts_installed", False):
        return
    if _find_menu(win, "编辑(&E)") is None and _find_menu(win, "传输(&T)") is None:
        retries = getattr(win, "_shortcut_install_retries", 0) + 1
        win._shortcut_install_retries = retries
        if retries <= 50:   # ★ 审计 L4：无菜单宿主（测试）不无限空转
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: install_shortcuts(win))
            return
        return
    win._shortcuts_installed = True
    _install_menu_actions(win)
    log.info("R176 快捷键已安装（独立模块 main_window_shortcuts）")


def _install_menu_actions(win) -> None:
    """把各动作挂到对应菜单（install_shortcuts 已完成幂等守卫后调用）。

    全部经 `_add_unique`：即使 `win._shortcuts_installed` 标记被人为清掉
    （或旧版本已装过），重复执行也只补缺、不叠动作。
    """
    em = _find_menu(win, "编辑(&E)")
    if em is not None:
        undo = _existing_action(em, "撤销(&Z)")
        if undo is not None:
            win._shortcut_undo_act = undo
            win._shortcut_redo_act = _existing_action(em, "重做(&Y)")
            if not getattr(em, "_r176_undo_hooked", False):
                em._r176_undo_hooked = True
                em.aboutToShow.connect(lambda: refresh_undo_redo(win))
        _add_unique(em, "复制选中 Clip", "Ctrl+D", duplicate_selected, "track_add",
                    win=win)
        _add_unique(em, "全局查找", "Ctrl+F", global_find, "search", win=win)

    vm = _find_menu(win, "视图(&V)")
    if vm is not None:
        _install_view_actions(vm, win)

    tm = _find_menu(win, "传输(&T)")
    if tm is not None:
        _add_unique(tm, "录制开关", "R", toggle_record, "record", win=win)
        _add_unique(tm, "分割 Clip", "S", split_clip_at_playhead, "track_add",
                    win=win)

    hm = _find_menu(win, "帮助(&H)")
    if hm is not None:
        _add_unique(hm, "键盘快捷键(&K)", "F1", show_shortcuts, "keyboard", win=win)


def _install_view_actions(vm, win) -> None:
    """视图菜单：跳转 ×9 + 循环/关闭（幂等；分隔条只插一次）。"""
    if _existing_action(vm, f"跳转到 {_JUMP_DOCKS[0][1:].replace('_dock', '')}") is None:
        _ensure_view_separator(vm)
    for i, attr in enumerate(_JUMP_DOCKS, start=1):
        text = f"跳转到 {attr[1:].replace('_dock', '')}"
        _add_unique(vm, text, f"Ctrl+{i}",
                    lambda w, idx=i - 1: jump_to_panel(w, idx), win=win)
    for text, key, slot in (("循环切换面板", "Ctrl+Tab", cycle_panels),
                            ("关闭当前面板", "Ctrl+W", close_current_panel)):
        _add_unique(vm, text, key, slot, win=win)


def _ensure_view_separator(vm) -> None:
    """在视图菜单尾部插一根带标记的分隔条（重复调用不再插）。"""
    for a in vm.actions():
        if a.isSeparator() and a.property(_INSTALLED_PROP) == _SEP_MARK:
            return
    sep = vm.addSeparator()
    sep.setProperty(_INSTALLED_PROP, _SEP_MARK)

