# -*- coding: utf-8 -*-
"""responsive_tiers.py — 通用「逐级隐藏」响应式控制器（R173 新模块）。

背景（`_offline_render_work/r173/survey_1.md`）：全项目仅
`joystick_control_binding_device.py` 一处真正的分级隐藏（且只有 compact/full 二态、
只管一个小对话框），四个冻结上帝类（MainWindow/EffectBrowserWidget/ProgrammerPanel/
TopViewPanel）零 `resizeEvent` 覆写。本模块提供一个**不侵入任何既有类方法体**的
通用机制：极简——窗口变窄时次要功能先收起（进「»」溢出菜单，仍可点），
而不是被裁切或整页横向滚动。

设计要点：
  * ``TierRule``：一条"低于 min_px 就该隐藏"的规则，不可变（frozen dataclass）。
  * ``TieredCollapse``：靠 ``target.installEventFilter(self)`` 拦截
    ``QEvent.Type.Resize`` / ``Show``，**绝不覆写目标类的 resizeEvent**，对冻结
    上帝类零方法侵入。每条规则的显隐状态独立按"当前宽度 vs (min_px, min_px+
    hysteresis)"判定，天然带迟滞防抖动闪烁，且天然实现"逐级隐藏/逆序恢复"
    （门槛越高的规则窗口越窄时越先隐藏，窗口变宽时门槛越低的规则——也就是最后
    被隐藏的那些——最先恢复，无需额外排序逻辑）。
  * **只恢复自己隐藏的部件**：用 ``set`` 记录被本控制器 ``setVisible(False)``
    过的部件；恢复时只对这个集合里的部件 ``setVisible(True)``。隐藏前用
    ``widget.isHidden()``（部件自身显式标志，不受祖先未 show() 影响——
    `survey_3.md` 已证实 ``isVisible()`` 在顶层窗口 show() 前恒为 False，
    不能用来判断"外部是否已经隐藏了它"）判断是否已被外部隐藏，是则跳过，
    绝不擅自显示外部隐藏的部件。
  * **不用 id() 做缓存键**——直接把 ``QWidget`` 对象本身放进 ``set``/``dict``
    （PyQt 部件对象按身份 hash/eq，天然规避项目记忆里反复出现的"id 键复用
    /未存强引用"这一类 bug；本模块的强引用本身就是 set/dict 的元素）。
  * 溢出按钮"»"（tabler ``dots``）+ ``QMenu``：被收起的 ``QAbstractButton``
    （含 checkable 开关）镜像成 ``QAction``（文字/图标/checked 状态），点击时
    调用原按钮 ``.click()``——保证 ``toggled`` 信号语义与点原按钮完全一致，
    隐藏本身绝不改变按钮的 checked 状态、不触发 toggled。无收起项时溢出按钮
    自身隐藏。
  * ``attach()`` 返回控制器并把强引用挂在 ``target`` 上（防 GC）；``detach()``
    还原全部隐藏、拆掉事件过滤器与溢出按钮。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import QEvent, QObject, QSize, QTimer
from PyQt6.QtGui import QAction, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractButton, QBoxLayout, QMenu, QSizePolicy, QToolButton, QWidget,
)

from .main_icon_library import load_tabler_icon
from .qascade_theme import BG_PANEL, FOCUS_BLUE, TEXT_SECONDARY

_DEFAULT_DEBOUNCE_MS = 50
_DEFAULT_HYSTERESIS_PX = 16
_ATTR_STRONG_REF = "_qa_responsive_tiers"

# PanelToolbar 行内"非 10 个开关按钮"部分的估算宽度（面板:标题+分隔线+音频源
# 三连胶囊+尾部分隔/全清空+软压制钮+溢出按钮自身，见 main_window.py:1068 起
# `_setup_ui`）。用于把光量出来的可用宽度换算成各按钮的收起阈值；只是估算值，
# 已用离屏截图（900/1280/1600/1920 宽）核对不出现"要么全收要么全不收"的极端
# 情况，不要求逐像素精确。
_TOOLBAR_CHROME_RESERVE_PX = 380


@dataclass(frozen=True)
class TierRule:
    """一条"宽度低于 min_px 时收起 widgets"的规则（不可变）。

    :param widgets:  同一档一起收起/恢复的部件（通常 1 个；也可以是一组，
                      比如"标题 Label + 紧跟其后的按钮"）。
    :param min_px:   目标宽度低于此值时收起；恢复阈值 = ``min_px + hysteresis``
                      （由 :class:`TieredCollapse` 统一应用，本类不含迟滞值）。
    :param label:    溢出菜单/调试用的人类可读标签，可为空。
    :param priority: 数值越小越重要（越晚收起/恢复越靠前），仅用于溢出菜单
                      排序，不参与收起/恢复判定本身。
    """

    widgets: Sequence[QWidget]
    min_px: int
    label: str = ""
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.widgets:
            raise ValueError("TierRule.widgets 不能为空")
        object.__setattr__(self, "widgets", tuple(self.widgets))


class TieredCollapse(QObject):
    """事件过滤器驱动的宽度分级隐藏控制器（不覆写 target 的 resizeEvent）。"""

    def __init__(
        self,
        target: QWidget,
        rules: Sequence[TierRule],
        hysteresis_px: int = _DEFAULT_HYSTERESIS_PX,
        debounce_ms: int = _DEFAULT_DEBOUNCE_MS,
    ) -> None:
        super().__init__(target)
        self._target = target
        self._rules: Tuple[TierRule, ...] = tuple(rules)
        self._hysteresis = max(0, int(hysteresis_px))
        self._collapsed: Set[TierRule] = set()
        self._hidden_by_us: Set[QWidget] = set()
        self._menu_entries: List[Tuple[QAction, QAbstractButton]] = []
        self._overflow_btn: Optional[QToolButton] = None
        self._overflow_menu: Optional[QMenu] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms)))
        self._timer.timeout.connect(self._apply)
        self._install_overflow_button()
        target.installEventFilter(self)
        self._apply()

    # ── 事件过滤：只拦截 Resize/Show 去抖，从不消费事件 ──────────────────
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._target and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show,
        ):
            self._timer.start()
        return False

    # ── 核心判定：每条规则独立按迟滞带决定显隐 ───────────────────────────
    def _apply(self) -> None:
        width = self._target.width()
        changed = False
        for rule in self._rules:
            was = rule in self._collapsed
            want = self._want_collapsed(rule, width, was)
            if want == was:
                continue
            self._set_rule_collapsed(rule, want)
            changed = True
        if changed:
            self._rebuild_overflow()

    def _want_collapsed(self, rule: TierRule, width: int, was: bool) -> bool:
        if width < rule.min_px:
            return True
        if width >= rule.min_px + self._hysteresis:
            return False
        return was  # 迟滞死区内保持原状态，防抖动闪烁

    def _set_rule_collapsed(self, rule: TierRule, collapsed: bool) -> None:
        if collapsed:
            for w in rule.widgets:
                if w in self._hidden_by_us or w.isHidden():
                    continue  # 已隐藏（含外部主动隐藏）——不重复记账
                w.setVisible(False)
                self._hidden_by_us.add(w)
            self._collapsed.add(rule)
        else:
            for w in rule.widgets:
                if w in self._hidden_by_us:
                    w.setVisible(True)
                    self._hidden_by_us.discard(w)
            self._collapsed.discard(rule)

    # ── 溢出按钮 + 菜单 ──────────────────────────────────────────────────
    def _install_overflow_button(self) -> None:
        btn = QToolButton(self._target)
        btn.setIcon(load_tabler_icon("dots", 16, TEXT_SECONDARY))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip("更多（已收起的面板开关）")
        btn.setFixedHeight(24)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setStyleSheet(
            "QToolButton { background:transparent;"
            " border:1px solid transparent; border-radius:4px; padding:2px 4px; }"
            f"QToolButton:hover {{ background:{BG_PANEL}; }}"
            f"QToolButton::menu-indicator {{ image:none; }}"
            f"QToolButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        )
        menu = QMenu(btn)
        menu.aboutToShow.connect(self._refresh_action_states)
        btn.setMenu(menu)
        btn.setVisible(False)
        layout = self._target.layout()
        if isinstance(layout, QBoxLayout):
            layout.addWidget(btn)
        self._overflow_btn = btn
        self._overflow_menu = menu

    def _rebuild_overflow(self) -> None:
        menu = self._overflow_menu
        btn = self._overflow_btn
        if menu is None or btn is None:
            return
        menu.clear()
        self._menu_entries.clear()
        entries: List[Tuple[int, TierRule, QAbstractButton]] = []
        for rule in self._collapsed:
            for w in rule.widgets:
                if isinstance(w, QAbstractButton) and w in self._hidden_by_us:
                    entries.append((rule.priority, rule, w))
        entries.sort(key=lambda item: item[0])
        for _, rule, button in entries:
            self._menu_entries.append((self._mirror_action(menu, button), button))
        btn.setVisible(bool(self._menu_entries))

    def _mirror_action(self, menu: QMenu, button: QAbstractButton) -> QAction:
        action = QAction(button.icon(), button.text(), menu)
        action.setCheckable(button.isCheckable())
        if button.isCheckable():
            action.setChecked(button.isChecked())
        action.setToolTip(button.toolTip())
        action.triggered.connect(partial(self._trigger_original, button))
        menu.addAction(action)
        return action

    @staticmethod
    def _trigger_original(button: QAbstractButton, _checked: bool = False) -> None:
        button.click()  # 复用原按钮 clicked/toggled 语义，不重复实现开关逻辑

    def _refresh_action_states(self) -> None:
        for action, button in self._menu_entries:
            if button.isCheckable():
                action.setChecked(button.isChecked())
            action.setText(button.text())

    # ── 生命周期 ─────────────────────────────────────────────────────────
    def detach(self) -> None:
        """还原全部本控制器隐藏的部件，拆掉事件过滤器与溢出按钮。"""
        self._timer.stop()
        self._target.removeEventFilter(self)
        for w in list(self._hidden_by_us):
            w.setVisible(True)
        self._hidden_by_us.clear()
        self._collapsed.clear()
        self._menu_entries.clear()
        if self._overflow_btn is not None:
            self._overflow_btn.setParent(None)
            self._overflow_btn.deleteLater()
            self._overflow_btn = None
        self._overflow_menu = None
        if getattr(self._target, _ATTR_STRONG_REF, None) is self:
            delattr(self._target, _ATTR_STRONG_REF)


def attach(
    target: QWidget,
    rules: Sequence[TierRule],
    hysteresis_px: int = _DEFAULT_HYSTERESIS_PX,
    debounce_ms: int = _DEFAULT_DEBOUNCE_MS,
) -> TieredCollapse:
    """挂载分级隐藏控制器到 ``target``，强引用挂在 target 上防 GC。"""
    controller = TieredCollapse(target, rules, hysteresis_px, debounce_ms)
    setattr(target, _ATTR_STRONG_REF, controller)
    return controller


# ── PanelToolbar 接入（main_window.py 只加一行调用，规则表全在这里） ─────────
# 按使用频率排序：越靠前越次要（窗口越宽才显示，越先被收起）；
# 越靠后越高频（窗口越窄仍尽量留底，见 survey_1.md「首批建议应用面板」）。
_PANEL_TOOLBAR_ORDER: Tuple[Tuple[str, str], ...] = (
    ("_matrix_pattern_btn", "矩阵"),
    ("_fixture_group_btn", "设备组"),
    ("_route_order_btn", "跑动编排"),
    ("_processor_btn", "关联"),
    ("_monitor_btn", "监视"),
    ("_topview_btn", "TopView"),
    ("_programmer_btn", "编程"),
    ("_patch_btn", "配接"),
    ("_studio_btn", "附加处理"),
    ("_dispatch_btn", "调度台"),
)


def install_panel_toolbar_tiers(toolbar: QWidget) -> Optional[TieredCollapse]:
    """给 ``PanelToolbar`` 挂分级折叠；规则表按累计自然宽度换算收起阈值。

    只在 ``PanelToolbar._setup_ui`` 末尾加一行 ``install_panel_toolbar_tiers(self)``
    调用；本函数自己按属性名读按钮（鸭子类型，不要求调用方传按钮列表），
    对未来新增/删除按钮保持稳健——某个属性不存在就跳过，不报错。
    """
    buttons = [
        (getattr(toolbar, attr, None), label)
        for attr, label in _PANEL_TOOLBAR_ORDER
    ]
    buttons = [(btn, label) for btn, label in buttons if btn is not None]
    if not buttons:
        return None
    gap = 4
    layout = toolbar.layout()
    if isinstance(layout, QBoxLayout) and layout.spacing() >= 0:
        gap = layout.spacing()
    # 按"重要性降序"（最重要排最前）累加自然宽度，越重要的按钮累计阈值越小
    # → 窗口越窄也能留到最后；见模块头注释"逐级隐藏/逆序恢复"的推导。
    importance_order = list(reversed(buttons))
    running = _TOOLBAR_CHROME_RESERVE_PX
    rules: List[TierRule] = []
    for idx, (btn, label) in enumerate(importance_order):
        running += btn.sizeHint().width() + gap
        rules.append(TierRule(widgets=(btn,), min_px=int(running),
                               label=label, priority=idx))
    return attach(toolbar, rules)


# ── 首次启动默认窗口尺寸（main_window.py._restore_geometry 只加一行调用） ───

def compute_default_window_size(
    available: QSize,
    minimum: Optional[QSize] = None,
    ratio: float = 0.85,
) -> QSize:
    """纯函数：按可用桌面区域比例算默认窗口尺寸，钳到 [minimum, available]。

    ``available`` 为非正尺寸（探测失败）时回退到 ``minimum``（或 1100x720，
    与改动前的硬编码常量一致）。
    """
    floor = minimum or QSize(900, 600)
    if available.width() <= 0 or available.height() <= 0:
        return QSize(max(floor.width(), 1100), max(floor.height(), 720))
    w = max(floor.width(), min(int(available.width() * ratio), available.width()))
    h = max(floor.height(), min(int(available.height() * ratio), available.height()))
    return QSize(w, h)


def default_main_window_size(
    minimum: Optional[QSize] = None,
    ratio: float = 0.85,
) -> QSize:
    """首次启动（无存档几何）时的默认窗口尺寸；感知当前主屏可用区域。"""
    screen = QGuiApplication.primaryScreen()
    available = screen.availableGeometry().size() if screen is not None else QSize(0, 0)
    return compute_default_window_size(available, minimum, ratio)
