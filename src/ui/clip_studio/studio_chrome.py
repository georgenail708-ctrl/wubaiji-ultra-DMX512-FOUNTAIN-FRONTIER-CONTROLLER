# -*- coding: utf-8 -*-
"""Clip Studio 面板 chrome 混入（R178 重做）—— 右栏布局 + 状态条 + 竖向工具栏。

从 studio_panel 拆出（≤500 行门禁）。节点编辑器级交互范式：

* 右栏排版：顶部**一行状态条**（页名 · 绑定数 · 编译状态钮=问题入口）+ 画布左侧
  **竖向工具栏**（tabler 图标 24px 竖排、tooltip 承载语义，无文字堆叠）+ 画布区。
* 原画布下方消息列表（_issues QListWidget）删除——问题并入状态条：文本=计数摘要
  （error 压过提示）、tooltip 列问题全文、点击定位首个问题节点。
* `_focus_first_issue`/`issues()` 的数据面在宿主（studio_panel），本混入只管呈现。

与宿主（ClipStudioPanel）以鸭子类型协作，依赖：`_canvas/_grid_canvases/_stack/
_grid_lay/_palette/_status_label/_sheet_label/_bind_label/_issue_items/_on_view_mode_clicked/
_show_help/_on_grid_preview_activated` 与方法 `_canvas()` 等（signals 由宿主定义）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_canvas import ClipStudioCanvas
from src.ui.main_icon_library import load_tabler_icon

__all__ = ["ChromeMixin"]


class ChromeMixin:
    """右栏 chrome（状态条 + 竖工具栏 + 画布区）构建；宿主 = ClipStudioPanel。"""

    # ── 右栏 ────────────────────────────────────────────────────────────
    def _build_right_side(self) -> QWidget:
        """R178 排版：顶部一行状态条 + 画布左竖工具栏 + 画布区（无底部消息区）。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(self._build_status_bar())
        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        h.addWidget(self._build_canvas_toolbar())
        self._stack = QStackedLayout()
        self._stack.addWidget(self._canvas)
        grid_host = QWidget()
        self._grid_lay = QGridLayout(grid_host)
        self._grid_lay.setContentsMargins(0, 0, 0, 0)
        self._grid_lay.setSpacing(3)
        for i, cv in enumerate(self._grid_canvases):
            self._grid_lay.addWidget(cv, i // 2, i % 2)
        self._stack.addWidget(grid_host)
        h.addLayout(self._stack, 1)
        v.addWidget(body, 1)
        return w

    # ── 竖向工具栏（工具栏范式）───────────────────────────────────
    def _build_canvas_toolbar(self) -> QWidget:
        """画布左上竖向工具栏：视图/分组/连线样式/自动布局/对齐/便签/导出/帮助。

        R183 W2-ST：新增四项（连线样式三态 / 自动布局 / 对齐分布 / 便签）+ 导出，
        仍守 R178 契约「图标钮无文字、语义归 tooltip」。
        """
        bar = QWidget()
        bar.setFixedWidth(30)
        v = QVBoxLayout(bar)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(3)
        self._btn_single = self._tool_button("layout-2", "单页视图（主画布）")
        self._btn_single.setCheckable(True)
        self._btn_grid = self._tool_button("layout-grid", "四宫格视图（前 4 个后处理页）")
        self._btn_grid.setCheckable(True)
        self._btn_group = self._tool_button(
            "box-multiple", "分组框：包住选中卡片（无选中=全部；右键框可改名/改色）")
        self._btn_edge_style = self._tool_button(
            "vector-bezier", "连线样式：点击切换 曲线/直角/直线")
        self._btn_layout = self._tool_button(
            "sitemap", "自动布局：按依赖拓扑分层（有环则不动并提示）")
        self._btn_align = self._tool_button(
            "layout-align-left", "对齐选中卡（Ctrl+L/R/T/B）；Ctrl+D 等距均分")
        self._btn_note = self._tool_button("note", "新建注释便签（双击编辑文本）")
        self._btn_overview = self._tool_button("arrows-maximize",
                                               "全览：缩放平移到全图可见（Home）")
        self._btn_export = self._tool_button("download", "导出图像：整帧存为 PNG")
        self._btn_help = self._tool_button("help-circle", "Studio 面板帮助")
        for b in (self._btn_single, self._btn_grid):
            b.clicked.connect(self._on_view_mode_clicked)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._btn_single)
        self._mode_group.addButton(self._btn_grid)
        self._btn_single.setChecked(True)
        self._btn_group.clicked.connect(self._add_group_frame)
        self._btn_edge_style.clicked.connect(self._cycle_edge_style)
        self._btn_layout.clicked.connect(self._auto_layout)
        self._btn_align.clicked.connect(self._align_selection)
        self._btn_note.clicked.connect(self._add_note_center)
        self._btn_overview.clicked.connect(lambda: self._canvas.zoom_to_fit())
        self._btn_export.clicked.connect(self._export_png_dialog)
        self._btn_help.clicked.connect(self._show_help)
        for b in (self._btn_single, self._btn_grid, self._btn_group, self._btn_edge_style,
                  self._btn_layout, self._btn_align, self._btn_note, self._btn_overview,
                  self._btn_export, self._btn_help):
            v.addWidget(b)
        v.addStretch(1)
        self._sync_edge_style_button()          # 初始档位进 tooltip（画布已就绪）
        return bar

    def _add_group_frame(self) -> None:
        """分组框入口：包住选中卡（无选中=全部卡）→ 一次图变更（账号落盘/撤销）。"""
        if self._canvas.create_frame_around_selection() is None:
            return
        self._on_graph_changed(str(self._state.get("active_sheet", "")), self._canvas)

    # ── R183 W2-ST 工具栏动作 ──────────────────────────────────────────
    def _cycle_edge_style(self) -> None:
        """连线样式三态切换（曲线 → 直角 → 直线）；tooltip 同步当前档位。"""
        self._canvas.cycle_edge_style()
        self._sync_edge_style_button()

    def _sync_edge_style_button(self) -> None:
        """把当前样式写进按钮 tooltip（无文字堆叠契约下的状态反馈）。"""
        self._btn_edge_style.setToolTip(self._canvas._toolbar_edge_style_label())

    def _auto_layout(self) -> None:
        """自动布局入口（有环 → 画布提示，不改图）。"""
        self._canvas.auto_layout_cards()
        self._refresh_compile_status(reset_canvas=False)

    def _align_selection(self) -> None:
        """对齐入口（左对齐兜底；精确档位走 Ctrl+L/R/T/B 与 Ctrl+D）。"""
        self._canvas.align_selection("left")

    def _add_note_center(self) -> None:
        """在视口中心建便签（落点避让交给 `_free_spot` 同款逻辑之外的最简路径）。"""
        from PyQt6.QtCore import QPointF
        cv = self._canvas
        cv.add_note(cv._s2w(QPointF(cv.width() / 2.0, cv.height() / 2.0)))

    def _export_png_dialog(self) -> None:
        """导出图像入口（转面板工具层：QFileDialog + 画布整帧）。"""
        self.export_canvas_png(self._canvas)

    @staticmethod
    def _tool_button(icon_key: str, tooltip: str) -> QToolButton:
        """竖栏图标钮：tabler 矢量 16px、无文字、tooltip 承载语义。"""
        b = QToolButton()
        b.setIcon(load_tabler_icon(icon_key, 16, _th.TEXT_PRIMARY))
        b.setToolTip(tooltip)
        b.setAutoRaise(True)
        b.setFixedSize(26, 26)
        return b

    # ── 状态条（消息区一行化）───────────────────────────────────────────
    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(8)
        self._sheet_label = QLabel("")
        self._sheet_label.setStyleSheet(f"color: {_th.TEXT_PRIMARY}; font-weight: bold;"
                                        "background: transparent;")
        h.addWidget(self._sheet_label)
        self._bind_label = QLabel("绑定 Clip: 0")
        self._bind_label.setStyleSheet(f"color: {_th.TEXT_SECONDARY};"
                                       "background: transparent;")
        h.addWidget(self._bind_label)
        h.addStretch(1)
        # 编译状态钮（原消息区一行化）：文本=摘要，tooltip 列问题，点击定位首个问题卡
        self._status_label = QToolButton()
        self._status_label.setAutoRaise(True)
        self._status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._status_label.clicked.connect(self._focus_first_issue)
        h.addWidget(self._status_label)
        return bar

    def _focus_first_issue(self) -> None:
        """状态条点击 → 定位首个问题节点（原问题列表「单击定位」语义收编为一行）。"""
        for issue in getattr(self, "_issue_items", []):
            nid = str(getattr(issue, "node_id", "") or "")
            if nid:
                self._canvas.focus_node(nid)
                return
