# -*- coding: utf-8 -*-
"""调度台面板（扁平风格）：左=监控区（自绘窗格），右=保存区（效果缓存卡片）。

用户要的闭环（本面板全部落实）：左监控区记录"各效果子页面对不同设备组正在做什么
效果"→ 彩色小窗格，框选/Ctrl/Shift 选中若干窗格 = 待保存内容；右保存区卡片列表
（多选/框选）持久化到项目，单击卡片 = 调用（像 MA 存好的 Cue），右击 = 调用/编辑
（调入监控区并还原各页面状态）/复制为变式/重命名/备注/删除。

职责边界（可维护性）：本面板**只做 UI 与交互**，不 import 任何效果面板——抓状态/
还原状态由 MainWindow 注入（`set_services`），MainWindow 按 node["panel"] 路由到
对应面板的 `dispatch_capture_state`/`dispatch_recall_state`；面板间零耦合，
加面板只改 MainWindow 一处。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSplitter, QVBoxLayout, QWidget)

from .. import qascade_theme as _th
from .monitor_cells import PANEL_FOLDERS, PANEL_LABELS, EffectBusView
from .panel_library import LibraryMixin
from .panel_table_host import TableHostMixin
from .scene_store_view import SceneStoreView
from .cell_ops import CellOps
from .group_resolution_cache import resolve_cells_cached
from . import table_ops as _ops
from ...processing.dispatch import (
    SCOPE_EFFECT_ONLY,
    build_fixture_info,
)

def _wire_store_growth(scroll, view) -> None:
    """滚到接近底部时让存储区续铺空槽（用户 2026-09-14 定案"无限列表"）。

    真正的"无限"会无限分配/重绘；这里按需增长，**绘制量始终 ∝ 可见槽数**
    （性能纪律：复杂度不得 ∝ 场景数），观感等同无限。
    """
    try:
        sb = scroll.verticalScrollBar()
        sb.valueChanged.connect(
            lambda _v, s=scroll, v=view: v.ensure_growth_for_scroll(
                s.verticalScrollBar().value() + s.viewport().height()))
    except Exception:
        pass


# ── R183 A8：调度台分界持久化（拆至 split_persistence；无 QApplication 时静默）──
from .split_persistence import (restore_splitter as _restore_splitter,
                                save_splitter as _save_splitter,
                                wire_splitter as _wire_splitter)


_SCROLL_QSS = (
    f"QScrollArea{{background:{_th.BG_ROOT};border:none;}}"
    f"QScrollBar:vertical{{background:{_th.BG_ROOT};width:12px;}}"
    f"QScrollBar::handle:vertical{{background:{_th.BG_BUTTON};"
    "border-radius:6px;min-height:24px;}}")


def _build_left_zone(panel, split) -> QWidget:
    """左监控区：标题行 + EffectBusView 滚动容器（接线在 panel 回调）。"""
    left = QWidget(split)
    lv = QVBoxLayout(left)
    lv.setContentsMargins(0, 0, 0, 0)
    lv.setSpacing(0)
    lh = QLabel("监控区 · 行=设备类型，列=值类型（只显示当前选中的设备）", left)
    lh.setStyleSheet(
        f"color:{_th.TEXT_SECONDARY};font-size:10px;padding:4px 8px;"
        f"background:{_th.BG_DEEP};border-bottom:1px solid {_th.SEP_LINE};")
    lv.addWidget(lh)
    panel.view = EffectBusView(left)
    panel.view.selection_changed.connect(panel._on_monitor_selection)
    panel.view.cell_activated.connect(panel._on_cell_activated)
    # ★ 2026-09-14 接线（此前**从未连接** → 右击菜单/右拖调值静默失效）：
    #   菜单构造在 table_ops、落值在 cell_ops，路由在 cell_handlers。
    panel.view.context_menu.connect(panel._on_table_context_menu)
    panel.view.quick_edit.connect(panel._on_table_quick_edit)
    panel.view.edit_committed.connect(panel._on_table_edit_committed)
    scroll = QScrollArea(left)
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel.view)
    scroll.setStyleSheet(_SCROLL_QSS)
    lv.addWidget(scroll, 1)
    split.addWidget(left)
    return left


def _build_right_zone(panel, split) -> QWidget:
    """右存储区：标题行（+场景页条） + SceneStoreView 滚动容器（键盘过滤/teardown 同装）。

    标题行装配与 teardown 接线分别外提到 `scene_pages.build_header_row` 与
    `_wire_zone_teardown`（R183 新增让它越过 500 行上限；本文件是既有大文件，
    新逻辑按项目纪律落新模块或模块级小函数）。
    """
    right = QWidget(split)
    rv = QVBoxLayout(right)
    rv.setContentsMargins(0, 0, 0, 0)
    rv.setSpacing(0)
    from .scene_pages import build_header_row
    rv.addWidget(build_header_row(panel, right))
    # ★ 存储区 = 场景槽位网格（编号 + 空槽 + 拖拽换位）。
    scroll2 = QScrollArea(right)
    scroll2.setWidgetResizable(True)
    panel.list = SceneStoreView(scroll2)
    panel.list.slot_activated.connect(panel._on_store_activated)
    # ★★ 2026-09-14 flash 模式需要"按住才应用、松手即释放"：原始鼠标事件。
    panel.list.slot_pressed.connect(panel._on_store_pressed)
    panel.list.slot_released.connect(panel._on_store_released)
    panel.list.empty_slot_clicked.connect(panel._on_store_empty_slot)
    panel.list.context_menu.connect(panel._on_store_menu)
    panel.list.reordered.connect(panel._on_store_reordered)
    from .scene_pages import wire_zone_teardown
    wire_zone_teardown(panel)
    # 框选/多选区右键（一键开启/关闭/改模式）
    panel.list.area_context_menu.connect(panel._on_store_area_menu)
    scroll2.setWidget(panel.list)
    _wire_store_growth(scroll2, panel.list)   # ★ 无限列表：滚到底续铺
    scroll2.setStyleSheet(_SCROLL_QSS)
    rv.addWidget(scroll2, 1)
    split.addWidget(right)
    return right


class DispatchPanel(LibraryMixin, TableHostMixin, QWidget):
    """调度台：左监控区 + 右保存区。"""

    #: 请求主窗把（选中的）缓存节点还原到各效果面板
    recallRequested = pyqtSignal(object)     # list[{"node":dict,"state":dict}]
    #: 保存后请求把缓存库写入项目（主窗负责置 dirty / 收集）
    cacheChanged = pyqtSignal()
    #: 请求主窗打开某面板并聚焦到这些设备（双击格 → 对应编辑界面）
    focusRequested = pyqtSignal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._capture_state: Optional[Callable[[str], dict]] = None
        self._recall_nodes: Optional[Callable[[list], bool]] = None
        #: 释放（关闭场景）回调：把节点从各面板停掉（停面板输出，不只清 NM 层）。
        self._release_nodes: Optional[Callable[[list], bool]] = None
        self._clip_report: Optional[Callable[[], None]] = None
        self._cell_state: Dict[Tuple[str, str, str], dict] = {}   # 最近一次调用带来的状态
        self._editing_id: str = ""
        # 表格视图偏好（记忆自 QSettings，见 table_ops）与依赖
        self._unit: str = _ops.load_unit()
        self._row_mode: str = _ops.load_row_mode()
        #: 列/展示分组：**默认合并**（不按来源分区、不画来源角标；用户 2026-09-14 定案）
        self._col_grouping: str = _ops.load_col_group()
        # ★ 2026-09-14 用户定案：只显示**当前真实在输出的设备**（不输出的不常驻、直接隐藏）。
        #   故默认 scope = SCOPE_EFFECT_ONLY（只列效果/节点覆盖到的设备），
        #   不列全工程设备（真实工程 1356 台里绝大多数没输出，全列出来毫无意义）。
        self._row_scope: str = SCOPE_EFFECT_ONLY
        self._cell_ops = None
        self._fixtures_provider = None
        self._defs_provider = None
        self._nm_provider = None
        self._panel_provider = None
        #: 已创建分组表 provider（`set_services` 注入）。★ 必须在 `__init__` 初始化：
        #  读先于写会抛 AttributeError、被 refresh_monitor 的宽 except 吞掉 → 表格永远空白。
        self._groups_provider = None
        # 当前选中设备 provider（见 panel_table_host）；★ __init__ 必须先初始化成
        # None（读先于写 → 宽 except 吞掉 → 表格永远空白，本项目踩过）。
        self._selected_ids_provider = None
        #: 复制/粘贴值（以值类型为键；见 cell_handlers）
        self._cell_clipboard = None
        self._info_cache: dict = {}
        self._info_cache_key = None
        #: 表格模型结构指纹缓存（命中则**不重建**，见 panel_table_host）
        self._model_fp = None
        self._model_cache = None
        #: 组归属解析的输入指纹（未变则跳过 O(节点×设备) 解析，见 refresh_monitor）
        self._resolve_sig = None
        self._build_ui()
        # 监控区刷新：从总线拉取（总线写入端已节流；这里再按人眼频率兜底）
        # ★★ 2026-09-14 双速率（用户实报"格子的刷新还是一卡一卡的"）：
        #   实测整 tick p50 仅 ~8ms（CPU 占空 400ms 下只有 2%）→ **不是负载问题**，
        #   而是 400ms 的**刷新节拍**（2.5Hz）本身看起来就是一跳一跳的：拖 Programmer
        #   推子时数值每 400ms 才跳一次，对编辑的响应延迟最高 400ms。
        #   故拆两条：
        #     · 慢表（400ms）→ 结构 + 值全刷（建表/解析/取值的全部工作）；
        #     · 快表（100ms）→ **只刷值**（`_refresh_values`，是廉价的那一半），
        #       把可视格数值刷新率提到 10Hz，肉眼即连续。
        #   间隔取 100ms 而非更快的理由：单次值刷新实测 6~17ms（随可视格数），
        #   100ms 下占空约 6%~17%，既不抢 40Hz 混音线程的 GIL，又明显比 2.5Hz 顺。
        #   结构类工作（`_resolve_cell_groups` O(节点×设备)、`set_nodes`）仍只在
        #   慢表跑，不会被快表放大。
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.refresh_monitor)
        self._fast_timer = QTimer(self)
        self._fast_timer.setInterval(100)
        self._fast_timer.timeout.connect(self._refresh_values_only)
        #: 最近一次全刷时刻（快表在刚全刷过时跳过，避免同一窗口重复取值）
        self._last_full_refresh = 0.0
        # ★ 2026-09-13 修性能（用户报"整个软件卡得跟 PPT 一样"）：原实现在 `__init__`
        #   里 `timer.start()` 且从不停止 → 面板隐藏后仍每 400ms 重建整张表（实测
        #   1000 台 207ms ≈ 常占 52% 一个核心）；现改为 showEvent 启动 / hideEvent 停止。

    def _refresh_values_only(self) -> None:
        """快表：只刷可视格的**值**（不动结构），把刷新节拍提到 10Hz。

        结构（分区/行/列/几何）与组解析只在 400ms 慢表跑；本方法只做
        `_refresh_values(full=False)`（可视范围）。
        刚做过全刷（慢表 tick）时跳过 —— 同一时刻不重复取一遍值。
        面板不可见 / 无表时直接返回（零开销）。
        """
        try:
            if self.view.model() is None or self._cell_ops is None:
                return
            if (time.monotonic() - float(self._last_full_refresh or 0.0)) < 0.05:
                return
            self._refresh_values(full=False)
        except Exception:
            pass

    def showEvent(self, ev):  # noqa: N802
        """面板可见 → 启动刷新定时器（不可见时不烧 CPU，见 __init__ 的说明）。

        ★ R183 A8：无存档分界时，此刻才有真实宽度 → 一次性按 62/38 落位
          （构建期宽度为 0，固定像素假定正是"监控区被挡住"的根因）。
        """
        super().showEvent(ev)
        try:
            if not getattr(self, "_split_has_saved", True):
                self._split_has_saved = True
                _restore_splitter(self._split)
            if not self._timer.isActive():
                self._timer.start()
                self.refresh_monitor()
            if not self._fast_timer.isActive():
                self._fast_timer.start()
        except Exception:
            pass

    def hideEvent(self, ev):  # noqa: N802
        """面板隐藏 → 停止刷新（"隐藏后仍卡"的根治点）。"""
        try:
            self._timer.stop()
            self._fast_timer.stop()
        except Exception:
            pass
        super().hideEvent(ev)

    # ── 依赖注入（MainWindow 提供路由）────────────────────────────────────
    def set_services(self, capture_state=None, recall_nodes=None,
                     clip_report=None, groups_provider=None,
                     fixtures_provider=None, defs_provider=None,
                     nm_provider=None, panel_provider=None,
                     focus_panel=None, *, selected_ids_provider=None,
                     release_nodes=None) -> None:
        self._capture_state = capture_state
        self._recall_nodes = recall_nodes
        #: ★ 释放回调（关闭场景用）：走各面板 `dispatch_release_state` 停掉面板输出。
        #:  只清 NM 层不够 —— 面板定时器会每帧重写（用户 2026-09-14 实报"消不掉"）。
        self._release_nodes = release_nodes
        self._clip_report = clip_report
        #: 提供 project.fixture_groups（已创建组）——供监视器做子母文件夹
        #: 归类：临时选中的格子挂到它隶属的已创建组之下。MainWindow 注入。
        self._groups_provider = groups_provider
        # ★ 表格视图新增注入（2026-09-13）：
        #   fixtures_provider → 工程设备表（行=设备）
        #   defs_provider     → 设备类型定义（列=值类型 + 中文名）
        #   nm_provider       → NetworkManager（取值/归属；逐格读写走 cell_ops）
        #   panel_provider    → 按面板 id 取面板实例（cell_ops 写值走 Programmer 行）
        self._fixtures_provider = fixtures_provider
        self._defs_provider = defs_provider
        self._nm_provider = nm_provider
        self._panel_provider = panel_provider
        # 监控区只反映"当前选中设备"（行=设备类型）；未注入 → 回退 build_table。
        self._selected_ids_provider = selected_ids_provider
        #: 双击格 → 唤起对应面板的编辑器（MainWindow 提供；保持零耦合）
        self._focus_panel_fn = focus_panel
        # 逐格读写/清空/标记的**唯一入口**（含聚合格展开）。info_provider：清空聚合格
        # 时按 `fids × ch_type` **现算**地址（`src_addrs` 被 `_AGG_ADDR_CAP` 截断；R136）。
        self._cell_ops = CellOps(nm_provider=nm_provider, panel_provider=panel_provider,
                                 info_provider=self._fixture_info)
        # 表视图构造器：节点 + 设备表 → 表格模型
        try:
            self.view.set_model_builder(self._build_table_model)
        except Exception:
            pass
        self._apply_view_prefs()

    # ── UI ────────────────────────────────────────────────────────────────
    def _btn(self, text: str, tip: str, accent: bool = False) -> QPushButton:
        b = QPushButton(text, self)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setFixedHeight(24)
        if accent:
            b.setObjectName("dispatchAccentBtn")
            b.setStyleSheet(
                f"#dispatchAccentBtn{{background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};"
                f"border:1px solid {_th.FOCUS_BLUE};border-radius:4px;padding:2px 10px;"
                "font-size:11px;font-weight:600;}"
                f"#dispatchAccentBtn:hover{{background:{_th.FOCUS_BLUE};color:{_th.BG_DEEP};}}"
                f"#dispatchAccentBtn:disabled{{color:{_th.TEXT_DISABLED};"
                f"background:{_th.BG_BUTTON};border-color:{_th.BORDER_NORMAL};}}")
        else:
            b.setObjectName("dispatchBtn")
            b.setStyleSheet(
                f"#dispatchBtn{{background:{_th.BG_BUTTON};color:{_th.TEXT_PRIMARY};"
                f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:2px 9px;"
                "font-size:11px;}"
                f"#dispatchBtn:hover{{background:{_th.BG_BUTTON_HOVER};}}"
                f"#dispatchBtn:disabled{{color:{_th.TEXT_DISABLED};}}")
        return b

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = self._build_toolbar()
        outer.addWidget(bar)
        self._build_body(outer)
        from .dispatch_workspace_ui import install
        install(self)
    def _build_toolbar(self) -> QWidget:
        """极简顶栏（用户 2026-09-14 定案）。

        ★ 用户原话："不要再设计这种丑陋原始的交互体验了！！顶部很多功能控件完全
          不需要…比如我要保存一个效果，直接让用户点击保存区任意一个格子，然后就弹
          出询问效果名称，然后就直接把效果存进格子了！！"

        因此**删除**了原先的一排控件（单位切换 / 行模式 / 展示分组 / 保存选中 /
        全部保存 / 覆盖更新 / 同步到面板 / 删除选中 / 释放全部调用层 / 搜索框）——
        保存改由"点存储区空格"完成（见 store_handlers._on_store_empty_slot），
        删除/重命名/复制走存储区右键，换位走拖拽。顶栏只留标题 + 一句操作提示。
        """
        bar = QWidget(self)
        bar.setObjectName("dispatchBar")
        bar.setStyleSheet(
            f"#dispatchBar{{background:{_th.BG_EDITOR};"
            f"border-bottom:1px solid {_th.SEP_LINE};}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(6)
        title = QLabel("监控 · 当前选中设备", bar)
        title.setStyleSheet(
            f"color:{_th.TEXT_PRIMARY};font-size:12px;font-weight:600;"
            "background:transparent;")
        h.addWidget(title)
        self._bus_lbl = QLabel("", bar)
        self._bus_lbl.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:10px;background:transparent;")
        h.addWidget(self._bus_lbl)
        h.addStretch(1)
        hint = QLabel("点击右侧空格保存当前效果 · 右键管理 · 拖拽换位", bar)
        hint.setStyleSheet(
            f"color:{_th.TEXT_DISABLED};font-size:10px;background:transparent;")
        h.addWidget(hint)
        return bar


    def _build_body(self, outer) -> None:
        """构建主体：左监控（表格）+ 右存储区（场景槽位）+ 状态条。"""
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        split.setStyleSheet(
            "QSplitter::handle{background:" + _th.BG_DEEP + ";}"
            "QSplitter::handle:horizontal{"
            "border-left:1px solid " + _th.SEP_LINE + ";"
            "border-right:1px solid " + _th.SEP_LINE + ";}"
            "QSplitter::handle:hover{background:" + _th.FOCUS_BLUE + ";}")

        left = _build_left_zone(self, split)
        right = _build_right_zone(self, split)
        # ★★ R183 A8（用户实报"保存区会把我的监控区挡住"）三件修：
        #   ① 双区显式最小宽；② 初始比例随可用宽自适应（弃固定 760/460 像素
        #   假定）；③ 手拖分界位置持久化。装配在模块级 `_wire_splitter`。
        self._split = split
        self._split_has_saved = _wire_splitter(split, left, right)
        outer.addWidget(split, 1)

        self._status = QLabel("", self)
        self._status.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:10px;padding:3px 8px;"
            f"background:{_th.BG_DEEP};border-top:1px solid {_th.SEP_LINE};")
        outer.addWidget(self._status)

    # ── 表格视图支撑（设备表 / 取值 / 偏好）────────────────────────────────
    def refresh_monitor(self, force: bool = False) -> None:
        """从总线拉取并刷新（保留选择）。

        ★ 性能（用户报"任何操作都卡"）：本方法每 400ms 被定时器调用。现在
          · `set_nodes` 会做**结构签名短路**：结构未变时不重建模型/几何/映射；
          · 取值默认只覆盖**可视范围**（`_refresh_values(full=False)`），
            避免为 8000 个离屏格白算；
          `force=True` 用于导入/换工程/改分组等"必须全量重建"的场合。
        """
        try:
            if self._clip_report is not None:
                self._clip_report()      # 时间线 clip 先入总线（节流在 timeline 侧）
        except Exception:
            pass
        try:
            from ...processing.dispatch import EffectBus
            bus = EffectBus.instance()
            cells = self._resolve_cell_groups(bus.cells())
            # 结构重建（或首次）时全量取值一次，保证离屏格也有值；否则只刷可视范围。
            # ★ 选中集变化也在 `_set_nodes_with_selection` 内强制重建（见该方法）。
            rebuilt = self._set_nodes_with_selection(cells, force)
            self._refresh_values(full=rebuilt)
            # 记下全刷时刻：快表在刚全刷过的窗口内跳过（避免同一时刻取两遍值）
            self._last_full_refresh = time.monotonic()
            self._bus_lbl.setText(
                f"{bus.count()} 个效果 · {bus.live_count()} 活跃")
            self._on_monitor_selection(self.view.selected_keys())
        except Exception:
            pass

    def _resolve_cell_groups(self, cells):
        """复用派生组身份，节点内容始终来自当前总线上报。"""
        from ...processing.dispatch import resolve_for_node
        groups = self._groups_provider() if self._groups_provider else None
        return resolve_cells_cached(self, cells, groups, resolve_for_node)

# 存储区动作（场景）在独立模块安装：避免继续往本文件堆方法。
from .cell_handlers import _install_cell_handlers  # noqa: E402
from .store_handlers import _install_store_handlers  # noqa: E402
from .grand_master_ui import install_grand_master_to_panel  # noqa: E402  R183 T5

_install_store_handlers(DispatchPanel)
_install_cell_handlers(DispatchPanel)
install_grand_master_to_panel(DispatchPanel)   # 总控滑杆（装配在 grand_master_ui）
