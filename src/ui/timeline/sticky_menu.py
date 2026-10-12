# -*- coding: utf-8 -*-
"""常开右键菜单 + 「已启用项」高亮（R142 用户需求）。

用户原话（2026-09-22）::

    "对于clip的右击页面菜单，每次我添加一个功能点击了之后菜单就关了，又需要重新
     右击重新选中应用，经常一个clip我需要上很多后处理，每次都需要点半天。现在优化
     显示逻辑，右击页面打开之后就常开，然后每个功能选项做一个高亮显示，启用的功能
     一览无余，我一次可以连续选择多个后处理功能全部应用好，都高亮显示了，再去关闭
     clip右击页面。不要每次都是一个一个选。"
    "右击页面的打开和关闭状态本身也可以做成高亮显示。就跟开灯关灯一样，点一下就
     常开，高亮显示，再点一下就关掉。"

## 机制（每条都用真事件探针实测过，见 `_offline_render_work/probe_r142_*.py`）

* **点条目不关闭**：`QMenu::mouseReleaseEvent` 是 QMenu 关自己的唯一鼠标路径
  （`activateAction` → `hideUpToMenuBar`）。拦下它、自己 `action.trigger()`，
  就得到"执行但不关闭"（探针实测：`isVisible=True`、`super` 调用次数 0、条目正常触发）。
* **子菜单也必须拦**：点子项会 `hideUpToMenuBar` 把**整棵树**收掉，
  所以每一层都要是 `StickyMenu`。
* **高亮确实渲染**：`QMenu::item:checked` 的 QSS 生效
  （探针抓图：未勾选条目像素 `#303030`、已勾选 `#2d5a3d`）。
* **该关的项照关**：删除/剪切/`自定义…`（模态对话框）等"终止动作"在 `veto` 名单里
  → 走 `super()` → 正常关闭（探针实测 `isVisible=False`）。

## 与门禁的关系

`clip_context_menu.py` 行数正好顶基线（646），故机制与样式全部收在本模块，
那边只留"怎么用"（每个功能族一两行）。
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Set

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeyEvent
from PyQt6.QtWidgets import QMenu

log = logging.getLogger(__name__)

#: QAction 上挂"状态判据"的属性名（`bind_mark` 写、`refresh_states` 读）。
#: 刻意不建注册表：属性随控件生命周期走，菜单销毁即自动清理，无泄漏面。
_STATE_FN_PROP = "_qascade_checked_when"

#: QAction 上挂"是否可点"判据的属性名（`bind_enabled` 写、`refresh_states` 读）。
_ENABLED_FN_PROP = "_qascade_enabled_when"

#: 菜单样式（含**已启用项高亮**）。灰底沿用 R56 定案的 `#303030`。
#: `:checked` 是这套样式的关键 —— 勾选中的条目自动变绿底亮字，无需逐条设色。
MENU_QSS = (
    "QMenu { background-color: #303030; color: #E0E0E0;"
    " border: 1px solid #454545; border-radius: 0px; padding: 2px;"
    " max-width: 360px; }"
    "QMenu::item { padding: 4px 24px 4px 8px; border-radius: 3px;"
    " background-color: transparent; }"
    "QMenu::item:selected { background-color: #4772B3; color: #FFFFFF; }"
    "QMenu::item:disabled { color: #6E6E6E; }"
    "QMenu::item:checked { background-color: #2D5A3D; color: #8FE9A8;"
    " font-weight: bold; }"
    "QMenu::separator { height: 1px; background-color: #3D3D3D;"
    " margin: 3px 4px; }"
)

#: 子菜单标题的"本族里有东西启用"标记（写在标题最前）。
MARK_ON = "● "
MARK_OFF = "○ "

#: 「常开」开关的两态文案（开=与关灯隐喻一致：亮着就是常开）。
PIN_TEXT_ON = "● 常开（菜单保持打开 · 点此关闭）"
PIN_TEXT_OFF = "○ 常开（点此改为保持打开）"

#: 会话级偏好：用户上次把菜单留在"常开"还是"普通"。默认常开（用户明确要求）。
_KEEP_OPEN_PREF = True


def keep_open_pref() -> bool:
    """用户上次选择（True = 常开）。"""
    return bool(_KEEP_OPEN_PREF)


def set_keep_open_pref(value: bool) -> None:
    """记住用户这次的选择（会话级，不落盘）。"""
    global _KEEP_OPEN_PREF
    _KEEP_OPEN_PREF = bool(value)


class StickyMenu(QMenu):
    """点条目**不关闭**的菜单；`veto` 名单里的条目照常关闭。

    `sticky=False` 时完全等价于原生 `QMenu`（任何条目点了都关）。
    """

    #: 根菜单最大宽度（R182）：QSS 的 `max-width: 360px` 只钳**窗口**，QMenu 的
    #: 布局仍按最宽 action 走 —— 一条长信息行就把 popup 撑到 360。Qt 官方结论
    #: 方案是在 `sizeHint()` 里主动收窄（窗口尺寸 = sizeHint）。只对**根菜单**
    #: 生效：子菜单标题短，且较窄子菜单里长条目会被硬裁（沿用系统宽即可）。
    MAX_WIDTH = 300

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(title, parent)
        self.sticky: bool = keep_open_pref()
        self._veto: Set[str] = set()
        self.setStyleSheet(MENU_QSS)

    # ── 宽度（R182）─────────────────────────────────────────────────────────

    def is_root_menu(self) -> bool:
        """根菜单 = parent 链上没有 QMenu（子菜单沿用系统宽度）。"""
        return not isinstance(self.parent(), QMenu)

    def sizeHint(self):                                 # noqa: N802 (Qt 覆写)
        """根菜单宽度上限 `MAX_WIDTH`（子菜单原样返回系统 sizeHint）。"""
        hint = super().sizeHint()
        if self.is_root_menu() and hint.width() > self.MAX_WIDTH:
            hint.setWidth(self.MAX_WIDTH)
        return hint

    # ── veto（点了要关闭的项）────────────────────────────────────────────────

    def veto(self, *texts: str) -> None:
        """把这些文案的条目登记为"终止动作"（点了正常关闭菜单）。"""
        for t in texts:
            if t:
                self._veto.add(str(t))

    def vetoed(self, action: Optional[QAction]) -> bool:
        return action is not None and action.text() in self._veto

    # ── 关闭拦截 ────────────────────────────────────────────────────────────

    def _keep_open_for(self, action: Optional[QAction]) -> bool:
        """该条目是否"执行但不关闭"（子菜单与 veto 项除外）。"""
        if not self.sticky or action is None:
            return False
        if action.menu() is not None or not action.isEnabled():
            return False
        return not self.vetoed(action)

    def trigger_keep_open(self, action: Optional[QAction]) -> bool:
        """执行该条目**并在执行后刷新全树高亮**；成功返回 True。

        ★★ R146 用户实报（"这些东西怎么能同时点亮呢？而且这已经引发闪退了"）：
          R142 只做了"点了不关"，**漏了"点完要高亮跟着变"** ——
          菜单打开时按当时的 clip 状态定好了勾选态，之后用户连点几个互斥项，
          旧的那条**永远是亮的**（"设置为 2 组"和"设置为 4 组"同时亮）。
          修法：每次执行后重跑一遍全部状态判据（`refresh_states`），
          互斥项自然只剩一条亮 —— 不需要逐族手写"取消其它"。
        """
        if action is None:
            return False
        action.trigger()
        refresh_states(self.root_menu())
        return True

    def root_menu(self) -> "QMenu":
        """本菜单所在树的**根**（沿 parent 链上溯到最外层菜单）。

        根菜单刻意不挂在 ClipItem 上（见 `clip_context_menu` 的注释），
        故 parent 链止于根菜单本身。
        """
        node = self
        while True:
            parent = node.parent()
            if isinstance(parent, QMenu):
                node = parent
                continue
            return node

    def mouseReleaseEvent(self, e) -> None:            # noqa: N802 (Qt 命名)
        """拦下 QMenu 的"触发即关闭"，改为一手触发、一手不放行关闭。"""
        action = self.activeAction()
        if self._keep_open_for(action):
            self.trigger_keep_open(action)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:      # noqa: N802 (Qt 命名)
        """键盘同理：回车/空格也执行但不关闭（Esc 仍走原生 = 关闭）。

        没有这一步时，方向键选中 + 回车会把整棵树收掉 —— 鼠标改了、键盘没改
        就成了一条静默的不一致路径。
        """
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            action = self.activeAction()
            if self._keep_open_for(action):
                self.trigger_keep_open(action)
                e.accept()
                return
        super().keyPressEvent(e)


# ── 建菜单的小工具（各功能族共用，避免逐处重复三行）─────────────────────────


def make_sticky_menu(parent: Optional[QMenu], title: str) -> StickyMenu:
    """建一个子菜单（**继承父菜单的 sticky / veto**）。"""
    parent_keep = bool(getattr(parent, "sticky", True))
    sub = StickyMenu(title, parent)
    sub.sticky = parent_keep
    inherited = getattr(parent, "_veto", None)
    if inherited:
        sub._veto = set(inherited)
    return sub


def add_sticky_submenu(parent: QMenu, title: str, icon_key: str) -> StickyMenu:
    """`parent` 下加一个带图标、继承常开属性的子菜单。"""
    from .clip_context_menu import _set_icon            # 复用既有图标助手
    sub = make_sticky_menu(parent, title)
    parent.addMenu(sub)
    _set_icon(sub.menuAction(), icon_key)
    sub.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    return sub


def mark(action: Optional[QAction], enabled: bool) -> Optional[QAction]:
    """把条目设成"可勾选 + 当前是否启用"→ 靠 QSS 的 `:checked` 自动高亮。

    这是**一次性**设定（建菜单时按当时状态定）。**常开菜单里必须改用 `bind_mark`**
    —— 否则用户连点几个互斥项后，旧的那条会永远亮着（R146 用户实报）。
    """
    if action is None:
        return action
    action.setCheckable(True)
    action.setChecked(bool(enabled))
    return action


def bind_mark(action: Optional[QAction], state_fn) -> Optional[QAction]:
    """把条目设为可勾选，并**登记状态判据**（每次菜单里执行完动作后重算）。

    `state_fn()` 返回 True/False（该条目"当前是否处于启用/选中态"）。
    判据从 **clip 现况**取值（不是记住点击结果），故：

    * 互斥族（"设置为 2 组"/"设置为 4 组"）天然只剩一条亮 ——
      因为 `groups` 只有一个值，判据不可能同时为真；
    * 动作没生效（例如被上层拦下）时不亮，**不会骗人**。
    """
    if action is None:
        return action
    action.setCheckable(True)
    action.setChecked(bool(state_fn()))
    action.setProperty(_STATE_FN_PROP, state_fn)
    return action


def bind_enabled(action: Optional[QAction], enabled_fn) -> Optional[QAction]:
    """登记"该条目**是否可点**"的判据（每次执行完后一并重算）。

    ★★ R146 用户实报："我现在点击不了组间停顿了"。
      真因：可点态同样是在**建菜单那一刻**算好的 —— 用户在常开菜单里先点了
      「组间运行方式 → 顺序分组」后，停顿条目仍是灰的（`pause_enabled` 冻在
      False），于是一直点不动。
      凡"某条目的可用性依赖另一个条目的效果"（停顿依赖运行方式、批次数依赖组数…），
      都必须用本函数登记判据，不能建菜单时一锤定音。
    """
    if action is None:
        return action
    action.setEnabled(bool(enabled_fn()))
    action.setProperty(_ENABLED_FN_PROP, enabled_fn)
    return action


def refresh_states(root: Optional[QMenu]) -> None:
    """按各条目登记的判据**重算整棵树的高亮与可点态**（就地改，不重建控件）。

    ★ 必须"就地改"：常开菜单在执行动作后仍然是活的，重建整棵树会把用户
      正在点的那个菜单拆掉（Qt 侧会崩）。这里只 `setChecked` / `setEnabled`，
      零结构变更。

    ★★ R146：同时刷新**可点态** —— 用户连点几个动作后，某些条目的可用性会变
      （"顺序分组" → "组间停顿" 从灰变亮）。旧实现只刷高亮不刷可用性，
      于是停顿永远点不动。
    """
    if root is None:
        return
    for menu in walk_menus(root):
        if menu is None:
            continue
        for act in menu.actions():
            _refresh_checked(act)
            _refresh_enabled(act)


def _refresh_checked(act) -> None:
    """按登记判据重算该条目的勾选态（顺带刷子菜单的 ●/○ 标记）。"""
    fn = act.property(_STATE_FN_PROP)
    if callable(fn):
        try:
            want = bool(fn())
        except Exception:
            log.debug("menu state refresh failed for %s", act.text(), exc_info=True)
        else:
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
    sub = act.menu()
    if sub is not None:
        mark_submenu(sub, bool(act.isChecked()))


def _refresh_enabled(act) -> None:
    """按登记判据重算该条目的可用性。"""
    fn = act.property(_ENABLED_FN_PROP)
    if not callable(fn):
        return
    try:
        want = bool(fn())
    except Exception:
        log.debug("menu enabled refresh failed for %s", act.text(), exc_info=True)
        return
    if act.isEnabled() != want:
        act.setEnabled(want)


def mark_submenu(menu: Optional[QMenu], active: bool) -> Optional[QMenu]:
    """在子菜单标题前打 `●`/`○`，让"这一族里有没有启用"在父级一眼可见。

    原标题存在 `menuAction().data()` 里，反复调用不会把标记叠加成 `●●`。
    """
    if menu is None:
        return menu
    act = menu.menuAction()
    if act is None:
        return menu
    base = act.data()
    if not isinstance(base, str) or not base:
        base = menu.title()
        act.setData(base)
    act.setText((MARK_ON if active else MARK_OFF) + base)
    return menu


def add_pin_action(menu: StickyMenu, on_toggle) -> QAction:
    """加顶部「常开」开关（开灯/关灯式：亮着=常开，点一下关掉并收起菜单）。

    `on_toggle(menu, action)` 由调用方决定副作用（写偏好、关菜单）。
    """
    act = menu.addAction(PIN_TEXT_ON if menu.sticky else PIN_TEXT_OFF)
    act.setCheckable(True)
    act.setChecked(bool(menu.sticky))
    act.triggered.connect(lambda _checked=False: on_toggle(menu, act))
    return act


def add_info_line(menu: QMenu, text: str) -> Optional[QAction]:
    """加一条不可点的信息行（用于"已启用 N 项…"总览）。"""
    if not text:
        return None
    act = menu.addAction(text)
    act.setEnabled(False)
    return act


def refresh_pin(menu: StickyMenu, action: Optional[QAction]) -> None:
    """开关文案/勾选态与 `menu.sticky` 对齐（点击后回调里调一次）。"""
    if action is None:
        return
    action.blockSignals(True)
    action.setText(PIN_TEXT_ON if menu.sticky else PIN_TEXT_OFF)
    action.setChecked(bool(menu.sticky))
    action.blockSignals(False)


def set_tree_sticky(root: QMenu, value: bool) -> None:
    """递归把整棵树（含所有子菜单）的 sticky 设成同一值。"""
    for menu in walk_menus(root):
        if isinstance(menu, StickyMenu):
            menu.sticky = bool(value)


def walk_menus(root: QMenu) -> Iterable[QMenu]:
    """深度遍历该菜单树（含 root）。"""
    stack = [root]
    while stack:
        menu = stack.pop()
        yield menu
        if menu is None:
            continue
        for act in menu.actions():
            sub = act.menu()
            if sub is not None:
                stack.append(sub)


def count_checked(root: QMenu) -> int:
    """整棵树里"已启用（勾选中）"的条目数 —— 总览与测试共用。"""
    n = 0
    for menu in walk_menus(root):
        if menu is None:
            continue
        for act in menu.actions():
            if act.isSeparator() or act.menu() is not None:
                continue
            if act.isChecked():
                n += 1
    return n


def summary_line(text: str, count: int) -> str:
    """总览行文案（`count<=0` → 明确说"未启用"，不留空行让人猜）。"""
    if count <= 0:
        return "未启用任何后处理"
    tail = f"：{text}" if text else ""
    return f"已启用后处理 {count} 项{tail}"


def elide_text(text: str, fm, max_px: int) -> str:
    """按像素预算截断文本（`ElideRight`；无 fm / 预算未给 / 放得下 → 原样返回）。

    R182 菜单宽度真修：QMenu 按最宽 action 布局，长信息行会把 popup 撑到 QSS
    的 `max-width`；配合 `StickyMenu.sizeHint()` 收窄，用本函数把信息行裁到预算内。
    """
    if fm is None or max_px <= 0:
        return text
    try:
        if fm.horizontalAdvance(text) <= max_px:
            return text
        from PyQt6.QtCore import Qt as _Qt
        return fm.elidedText(text, _Qt.TextElideMode.ElideRight, max_px)
    except Exception:
        log.debug("menu text elide failed", exc_info=True)
        return text
