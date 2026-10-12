"""Clip context-menu construction split from the timeline item widget."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu

from src.ui import qascade_theme as _th
from src.ui.main_icon_library import make_main_icon, load_tabler_icon
from src.ui.timeline import sticky_menu as _sm

log = logging.getLogger(__name__)


# 2026-08-27 换肤：原 _CLIP_MENU_QSS（#3c3f41/#1a3a6b 浅灰专属样式）按规范 §3/§5 删除，
# 右键菜单统一走全局主题的 QMenu 四态样式（bg_deep 底 + select_blue 选中）。
# 子菜单经 _add_submenu 继承 parent.styleSheet()（现为空串），行为不变。

# 危险/清空类动作：bright_error 着色图标提示（F 任务；不改动作文本与行为）
# ★ R172 菜单手术（docs/clip_studio_r172_design.md §1）：清空类条目已整体撤出
#   菜单（去向 Studio 卡片画布），这里只留菜单里仍存在的两个键。
_DANGER_ACTION_ICONS = {
    "删除片段": "trash",
    "剪切片段": "scissors",
}


# ★ R172 Studio 复用：电机力度 5 预设（「优化电机力度」子菜单已撤出菜单，
#   Clip 附加处理 Studio 的「电机力度卡」沿用同一份数据，勿删）。
_MOTOR_FORCE_PRESETS = (
    ("gentle", "极柔和", "长加速段，平缓启停，相位差最小"),
    ("soft", "柔和", "对称缓入缓出，模拟 ASS6 补间低速预加速"),
    ("natural", "自然", "S 曲线平滑，梯形速度剖面均衡感"),
    ("strong", "强力", "短加减速，快启快停，相位差明显"),
    ("blast", "爆发", "极短加减速 + 急停，冲击力最强"),
)
# ★ R172 Studio 复用：16 种过渡方向（「分组方向过渡」子菜单已撤出菜单，
#   Studio「过渡方向 ×16」卡片与引擎归一名沿用同一份清单，勿删）。
_DIRECTIONS = (
    ("none", "整体 / 无方向"), ("ltr", "从左到右"), ("rtl", "从右到左"),
    ("ftb", "从前到后"), ("btf", "从后到前"), ("cw", "顺时针"),
    ("ccw", "逆时针"), ("ctr", "中心向两边"), ("rtc", "两边向中心"),
    ("alt", "奇偶交替"), ("alt_block", "块交替"), ("wave", "波浪"),
    ("cascade", "级联"), ("rev_cascade", "反向级联"),
    ("zigzag", "折返"), ("random", "随机"),
)


def _set_icon(action: QAction, key: str) -> None:
    action.setIcon(make_main_icon(key, 16))


def _add_action(
    menu: QMenu,
    text: str,
    icon_key: str,
    callback: Callable[..., Any],
    *,
    checkable: bool = False,
    checked: bool = False,
    enabled: bool = True,
) -> QAction:
    action = menu.addAction(text)
    _set_icon(action, icon_key)
    action.setCheckable(checkable)
    if checkable:
        action.setChecked(checked)
    action.setEnabled(enabled)
    action.triggered.connect(callback)
    return action


def _add_submenu(parent: QMenu, text: str, icon_key: str) -> QMenu:
    """加子菜单。

    ★ R142：改用**常开菜单**（继承父菜单的 sticky/veto）—— 原生 QMenu 的子项
      点击会 `hideUpToMenuBar` 收掉**整棵树**，只拦根菜单是不够的。
    """
    submenu = _sm.add_sticky_submenu(parent, text, icon_key)
    submenu.setStyleSheet(parent.styleSheet())
    return submenu


def ensure_menu_action_icons(menu: QMenu) -> None:
    """Ensure every non-separator entry has a vector icon."""
    for action in menu.actions():
        if action.isSeparator():
            continue
        if action.icon().isNull():
            _set_icon(action, "panel_default")
        submenu = action.menu()
        if submenu is not None:
            ensure_menu_action_icons(submenu)


def tint_danger_action_icons(menu: QMenu) -> None:
    """危险动作图标统一 bright_error 着色（含全部子菜单层级）。"""
    for action in menu.actions():
        if action.isSeparator():
            continue
        tabler_name = _DANGER_ACTION_ICONS.get(action.text())
        if tabler_name:
            action.setIcon(load_tabler_icon(tabler_name, 16, _th.BRIGHT_ERROR))
        submenu = action.menu()
        if submenu is not None:
            tint_danger_action_icons(submenu)


class ClipContextMenuBuilder:
    """Build the Clip menu while keeping each functional group isolated."""
    def __init__(self, item: Any) -> None:
        self._item = item
        self._clip = item._clip
    def build(self) -> QMenu:
        """建菜单。

        ★★ R142 用户需求（原话见 `sticky_menu` 模块头）：右击页**常开**、每个功能项
          **高亮**显示是否已启用、顶部一个"常开"开关（开灯/关灯式）。
          机制全部在 `sticky_menu`（QMenu 子类 + QSS），这里只负责"怎么用"。
        """
        # ★★ R146 崩溃修复：根菜单**不能挂在 ClipItem 上**。
        #   常开菜单执行动作后会触发 `_refresh_clips_targeted` 等刷新，而刷新可能
        #   销毁/重建 ClipItem —— 菜单是它的子控件，会在**自己的事件处理过程中**
        #   被连带销毁 → Qt 访问已释放对象 → **闪退**（用户实报"这已经引发闪退了"）。
        #   改挂 None：菜单的存活由 `exec()` 期间的 Python 引用保证，与 item 解耦。
        menu = _sm.StickyMenu("")
        # 危险/清空类 + 「Clip 附加处理…」（会打开 Studio 页面）点了要收菜单：
        # 常开菜单若杵在后面会挡住用户，且这些是"做完这一件就走"的动作。
        menu.veto(*_TERMINAL_ACTIONS)
        _sm.add_pin_action(menu, _on_pin_toggled)
        menu.addSeparator()
        self._add_clip_operations(menu)
        menu.addSeparator()
        tools_menu = self._add_tools_menu(menu)
        self._add_hold_menu(menu)
        self._add_processing_entry(menu)
        menu.addSeparator()
        # ★ R172 B11：clip 绑定了 Studio sheet 时在摘要后追加 "· Sheet:<名>"
        _sm.add_info_line(menu, _summary_text(self._clip) + _sheet_suffix(self._clip))
        self._item._add_conflict_menu(tools_menu)
        ensure_menu_action_icons(menu)
        tint_danger_action_icons(menu)
        return menu
    def _add_clip_operations(self, menu: QMenu) -> None:
        item = self._item
        clip_id = self._clip.id
        _add_action(
            menu, "选择片段", "select",
            lambda: item.clip_selected.emit(clip_id, False, False),
        )
        _add_action(menu, "剪切片段", "track_delete", lambda: item.clip_cut.emit(clip_id))
        _add_action(menu, "复制片段", "project_new", lambda: item.clip_copy.emit(clip_id))
        # ★ R172 A1：「复制片段副本」菜单项删除（A1 与「复制片段」合并）；
        #   后端 duplicate_many 信号链保留不动（其他入口仍可用）。
        _add_action(
            menu, "删除片段", "clear_output",
            lambda: item.clip_delete_req.emit(clip_id),
        )
    def _add_processing_entry(self, menu: QMenu) -> None:
        """★ R172 A5：单入口「Clip 附加处理…」。

        原「Clip 优化」/「Clip 附加处理」两个子菜单**整体撤出菜单**（泵预充、
        优化过渡方式、电机力度、电机相位差、速度/时长、过渡/淡入淡出、应用边、
        分组方向过渡、自动平滑、自动灯跟踪 —— 全部去向 Studio 卡片画布，
        见设计文档 §1/§3）。点击 = 发信号 + 收菜单（veto 名单已含本条文案）。
        """
        item = self._item
        clip_id = self._clip.id
        _add_action(
            menu, "Clip 附加处理…", "effects",
            lambda: item.clip_open_processing_studio.emit(clip_id),
        )
    def _add_tools_menu(self, menu: QMenu) -> QMenu:
        tools_menu = _add_submenu(menu, "工具", "fit")
        fixture_ids = [
            str(fid)
            for fid in (getattr(self._clip, "base_order_fixture_ids", []) or [])
            if str(fid)
        ]
        active_map = getattr(self._clip, "active_channels_by_universe", {}) or {}
        _add_action(
            tools_menu, "加载该 Clip 设备组", "fixture_group",
            lambda: self._item.clip_load_fixture_group.emit(self._clip),
            enabled=bool(fixture_ids) or bool(active_map),
        )
        record_action = _add_action(
            tools_menu, "允许录制接收该 Clip 输出", "record",
            lambda checked=False: self._item._emit_record_output_source_action_state(
                record_action,
            ),
            checkable=True,
            checked=bool(getattr(self._clip, "record_output_source_enabled", False)),
        )
        return tools_menu
    def _add_hold_menu(self, menu: QMenu) -> None:
        """★ R172 A3：「保持模式」从（已撤除的）附加处理子菜单**上提到一级**。

        二级菜单三项（不保持/保持最后一帧/保持最后一个周期）、`_bind` 状态判据
        高亮与 `mark_submenu` ●/○ 标记原样保留 —— 它同时是 sticky `bind_mark`
        机制的验证载体（回归测试 r146/r142 仍以它为准）。
        """
        hold_menu = _add_submenu(menu, "保持模式", "lock")
        current = self._item._hold_mode()
        for mode, label in (
            ("none", "不保持"),
            ("last_frame", "保持最后一帧"),
            ("last_cycle", "保持最后一个周期"),
        ):
            act = _add_action(
                hold_menu, label, "lock",
                lambda checked=False, value=mode:
                    self._item.clip_hold_mode_changed.emit(self._clip.id, value),
            )
            _bind(act, lambda value=mode: self._item._hold_mode() == value)
        _sm.mark_submenu(hold_menu, current != "none")


#: 「终止动作」文案 —— 点了**允许关闭**菜单（R142 常开菜单的 veto 名单）。
#: 判据 = 点完就"这一趟结束"：删除/剪切（clip 可能没了）、以及会打开
#: **Studio 页面**的「Clip 附加处理…」（R172 A5 单入口；原"自定义…"族与
#: 清空类条目已随菜单手术撤出，文案同步移除）。
_TERMINAL_ACTIONS = ("删除片段", "剪切片段", "Clip 附加处理…")


def _on_pin_toggled(menu: QMenu, action: QAction) -> None:
    """顶部「常开」开关：开灯/关灯式 —— 亮着=常开，点一下关掉并收起菜单。

    ★ 记忆用户的选择（会话级）：下次右击直接按上次的偏好开，不用每次重设。
    """
    now_on = bool(action.isChecked())
    _sm.set_keep_open_pref(now_on)
    _sm.set_tree_sticky(menu, now_on)
    _sm.refresh_pin(menu, action)
    log.debug("clip 右击页：常开 = %s", now_on)
    if not now_on:
        menu.close()          # 关灯 → 收起来（用户明确要的"再点一下就关掉"）


#: 信息行（`_summary_text` 总览）的像素预算（R182 菜单收窄为根宽 300 服务）。
_SUMMARY_MAX_PX = 260


def _menu_font_metrics():
    """QMenu 默认字体的度量（截断宽预算用；无 QApplication 时 None）。"""
    try:
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None
        return QFontMetrics(QApplication.font())
    except Exception:
        return None


def _summary_pairs(clip):
    """右击页两类（R111 唯一清单）的非默认项 `[(标签, 值文本)]`；异常 → None。"""
    try:
        from ...core.clip_postprocess import GROUP_CONTEXT, GROUP_NEW, describe_groups
    except Exception:
        return None
    try:
        groups = describe_groups(clip)
    except Exception:
        return None
    return [(label, value) for gname, items in groups
            if gname in (GROUP_CONTEXT, GROUP_NEW) for label, value in items]


def _short_label(label: str) -> str:
    """标签去掉括注单位（`淡入时长(秒)` → `淡入时长`；紧凑总览行用）。"""
    text = str(label or "")
    for ch in ("(", "（"):
        idx = text.find(ch)
        if idx > 0:
            text = text[:idx]
    return text.strip()


def _summary_text(clip) -> str:
    """底部总览行：已启用哪些后处理（与右击页/高级页同一份白名单真源）。

    复用 `core.clip_postprocess.describe_groups`（R111 建立的唯一清单），
    只取**右击页**那两类，避免把"高级处理页"的设置也混进右击页的读数。
    R182：长文案按 `_SUMMARY_MAX_PX` 用 `ElideRight` 截断（信息行是菜单里最长
    的文本，不裁会把 popup 撑宽 —— QMenu 布局按最宽 action 走）；超预算时
    先退化为"只列名称（去括注单位）"，仍超才截断。
    """
    rows = _summary_pairs(clip)
    if rows is None:
        return ""
    if not rows:
        return _sm.summary_line("", 0)
    tail = "…" if len(rows) > 6 else ""
    full = _sm.summary_line(
        "、".join(f"{label}={value}" for label, value in rows[:6]) + tail, len(rows))
    fm = _menu_font_metrics()
    out = _sm.elide_text(full, fm, _SUMMARY_MAX_PX)
    if out == full:
        return out
    names = "、".join(_short_label(label) for label, _value in rows[:6]) + tail
    return _sm.elide_text(f"已启用后处理：{names}", fm, _SUMMARY_MAX_PX)


def _studio_sheet_by_id(sheets, sheet_id):
    """在 sheets 列表里找 id 匹配的 dict 表项；找不到/畸形返回 None。"""
    for sheet in sheets or []:
        if isinstance(sheet, dict) and str(sheet.get("id", "") or "") == sheet_id:
            return sheet
    return None


def _studio_sheet_name(clip) -> str:
    """R172 B11：解析 clip 绑定的 Studio sheet 名（全防御式，缺链/异常返回 ""）。

    取名链：`ProjectManager.instance()`（取用法同原 `_follow_resolved_scope_label`）
    → project → `clip_studio_state` dict → `sheets` 列表里
    `id == clip.process_sheet_id` 的 `name`。
    """
    try:
        sheet_id = str(getattr(clip, "process_sheet_id", "") or "")
        if not sheet_id:
            return ""
        from ...core.project_manager import ProjectManager as _PM
        pm = _PM.instance()
        state = getattr(getattr(pm, "project", None), "clip_studio_state", None)
        sheets = state.get("sheets") if isinstance(state, dict) else None
        sheet = _studio_sheet_by_id(sheets, sheet_id)
        return str(sheet.get("name", "") or "") if sheet else ""
    except Exception:
        pass
    return ""


def _sheet_suffix(clip) -> str:
    """R172 B11：clip 绑定了 Studio sheet 时信息行追加 "· Sheet:<名>"（缺失/异常静默不追加）。"""
    name = _studio_sheet_name(clip)
    return f"· Sheet:{name}" if name else ""


def _bind(action: QAction, state_fn) -> QAction:
    """可勾选 + 登记状态判据（每次菜单里执行完动作后自动重算高亮）。

    ★★ R146 用户实报：常开菜单连点互斥项后，"设置为 2 组"与"设置为 4 组"
      会**同时亮着**（旧实现在建菜单时定死勾选态，之后不再更新）。
      `state_fn` 从 **clip 现况**取值 → 互斥族天然只剩一条亮。
    """
    return _sm.bind_mark(action, state_fn)


# ★★★ R172 Studio 复用区（勿删）★★★
# 以下 clip 状态判据 helpers 是「已启用高亮」（bind_mark）与 Studio 卡片
# "已应用"描边（§3.3 反向编译，同菜单绿亮语义）的**同一份取值口径**。
# `_dir_highlight` / `_edge_highlight` / `_menu_groups` / `_menu_scope` 经
# grep 确认当前无外部代码引用，但为 W1c Studio 波次预留复用，保留并注明。
def _menu_groups(clip) -> int:
    """该 clip 当前组数（供方向/组数高亮与指令一致取值，坏值 → 1）。Studio 复用"""
    try:
        return max(1, int(getattr(clip, "fade_groups", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _menu_scope(clip) -> str:
    """分组坐标系（坏值 → per_shape，与菜单显示同口径）。Studio 复用"""
    cur = str(getattr(clip, "fade_group_spatial_scope", "per_shape") or "per_shape")
    cur = cur.strip().lower()
    return cur if cur in ("per_shape", "global", "follow") else "per_shape"


def _clip_attr(clip, attr: str, default=0.0):
    """读 clip 字段（坏值 → default），供状态判据安全取值。Studio 复用"""
    try:
        v = getattr(clip, attr, default)
        return default if v is None else v
    except Exception:
        return default


def _dir_highlight(clip) -> str:
    """「分组方向过渡」该高亮哪条方向 —— 取决于**当前在编辑哪一边**。Studio 复用

    ★★ R146 用户定案（原话）::

        "单独选择淡入或者单独选择淡出，或者两个都亮，那么应该方向只能选一个啊…
         单独点亮其中一个只会看到其中一个正在应用的方向，两个都点亮就显示淡入的方向。"

    于是：`fade_direction_edge == "out"` → 看淡出方向；否则（`both`/`in`）→ 看淡入方向。
    旧实现要求 `fade_in_direction == fade_out_direction` 才亮 —— 于是"两边设了不同
    方向"时**一条都不亮**（用户看到的是"没有方向被选中"）。
    """
    edge = str(_clip_attr(clip, "fade_direction_edge", "both") or "both").strip().lower()
    attr = "fade_out_direction" if edge == "out" else "fade_in_direction"
    return str(_clip_attr(clip, attr, "none") or "none")


def _edge_highlight(clip) -> str:
    """「应用边」该亮哪条（`in`/`out`）；`both` → 都不亮（=淡入淡出一致）。Studio 复用"""
    edge = str(_clip_attr(clip, "fade_direction_edge", "both") or "both").strip().lower()
    return edge if edge in ("in", "out") else ""


def build_clip_context_menu(item: Any, global_pos: Any) -> None:
    """Create and execute the context menu for one Clip item."""
    menu = ClipContextMenuBuilder(item).build()
    menu.exec(global_pos)
