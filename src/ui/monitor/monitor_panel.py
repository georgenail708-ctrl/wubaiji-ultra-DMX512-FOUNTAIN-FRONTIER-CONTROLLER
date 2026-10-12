""" 
Qascade V1.0 — Universe Monitor Panel
[B] 常驻独立窗口：域-通道监控器 + 过滤配置
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QScrollArea, QSplitter,
    QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from .monitor_scroll_column import NoSqueezeColumn, wrap_right_column
from .channel_grid import ChannelGrid, PeakHold   # PeakHold：A10 G20 峰值保持
from .filter_numpad import FilterNumpad
from .monitor_histogram import ValueHistogram
from .universe_bar import UniverseBar
from ...core.models import FilterRule

# S1 令牌化：配色统一走中心主题令牌
from ..qascade_theme import (
    BG_ROOT, BG_INPUT, SEP_LINE, TEXT_SECONDARY,
    STATUS_TEXT, BRIGHT_SUCCESS, BRIGHT_INFO,
)
# D波标准件
from ..widget_kit import PanelSection, scope_qss
# R183 D：状态行装配拆至 monitor_status_row（同名 re-export 保兼容）
from .monitor_status_row import (  # noqa: F401
    _build_heatmap_toggle, _build_status_row, _sync_smooth_badge,
)
from .monitor_legacy_buttons import build_hidden_buttons as _build_hidden_buttons
# R183 W2：扩展区行为层（冻结/标注/组折叠/统计/录制/导出/blink/对照视图）
from .monitor_panel_extras import (  # noqa: F401  （常量 re-export 便于测试）
    ANNOT_ADDR, ANNOT_NAME, ANNOT_OFF, MonitorPanelExtrasMixin,
)
from .monitor_panel_view import (
    build_export_row, build_io_section, build_recorder_section,
    build_selection_row, build_sparkline_section, make_extras_button,
)

# 语义按钮 rgba 分量：SUCCESS_GREEN #188625 / ERROR_RED #991616 / WARN_ORANGE #AC8737
_G = (24, 134, 37)
_R = (153, 22, 22)
_W = (172, 135, 55)

# 类别色点（过滤规则图例行用——与 qascade_theme.CATEGORY_COLORS 同源的语义点缀）
_ROW_DOT_INPUT = BRIGHT_SUCCESS    # 输入=接收语义
_ROW_DOT_OUTPUT = BRIGHT_INFO      # 输出=发送语义


class MonitorPanel(MonitorPanelExtrasMixin, QWidget):
    """
    域-通道监控器（常驻独立窗口）
    功能：
    1. 选择域，显示512通道实时数值
    2. 小键盘+框选配置输入/输出过滤
    3. 可直接框选通道用于轨道录制范围配置
    """
    input_filter_changed  = pyqtSignal(object)   # FilterRule（设计预留, 无消费方）
    output_filter_changed = pyqtSignal(object)   # FilterRule（设计预留, 无消费方）
    selection_for_track   = pyqtSignal(set)      # 用户确认录制范围（设计预留, 无消费方）

    def __init__(self, network_manager, project_manager, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._pm = project_manager
        self._current_universe = 1
        self._filter_mode = "input"   # "input" or "output"
        self._last_running_state = None
        # A10 G20：取数链峰值保持（_refresh_data 喂 update_data 前合成）
        self._peak = PeakHold()
        # R183 D：右栏直方图（无 pyqtgraph → available=False 静默隐藏）
        self._hist = ValueHistogram()
        self._universe_bar: UniverseBar | None = None
        self._uni_snapshot_source = None   # 测试注入用只读快照源（callable）
        # R183 W2：扩展区状态（冻结/录制/blink/标注/统计节流）
        self._init_extras()
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(60)   # blink 半周期 250ms，60ms 采样足够

        self.setWindowTitle("域-通道监控器")
        self.setMinimumSize(720, 480)
        # S1 重构：删除整窗 QWidget{} 通配覆写（audit-B 点名：
        # 会击穿所有子控件的继承预期），通用控件外观统一走 GLOBAL_QSS。
        # D波：独立窗口本体补根底色——类限定选择器只作用面板自身，不影响子树。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"MonitorPanel{{background:{BG_ROOT};}}")
        self._setup_ui()
        self._connect_signals()

        # 刷新定时器
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_data)
        self._timer.start(50)

    def set_data_refresh_paused(self, paused: bool) -> None:
        """渲染等模态重活期间暂停 20Hz 取数/512 格重绘。

        背景（R56b 实测）：模态渲染期间 GUI 线程只在进度回调的
        processEvents 泵里处理事件，监视器的周期性重绘全部堆积到泵里
        同步执行——61s 渲染中 ~50s 是被吸进来的重绘风暴。暂停后渲染
        墙钟时间只含引擎工作本身。
        """
        try:
            if paused:
                self._timer.stop()
            else:
                self._timer.start(50)
        except Exception:
            pass

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── R55 顶栏大精简（用户点名）：顶部空间全部让给每域格子。
        # 域选择器迁右列顶部；过滤双钮/应用/清除仅保活（默认 input 方向）。
        # R53/R55 隐藏保活钮装配拆至 monitor_legacy_buttons（R183 D 压文件行数）。
        self._universe_spin = QSpinBox()
        self._universe_spin.setRange(1, 32767)  # Art-Net 最大域编号
        self._universe_spin.setValue(1)
        self._universe_spin.setFixedWidth(90)
        self._universe_spin.setFixedHeight(26)
        self._universe_spin.setToolTip("输入域编号（1-32767），支持任意 Art-Net / sACN 域")
        self._universe_spin.valueChanged.connect(self._on_universe_spin_changed)

        (self._input_btn, self._output_btn, self._apply_filter_btn,
         self._clear_filter_btn, self._to_track_btn) = _build_hidden_buttons(self)

        # 网络状态行（R55；★ R178 热图切换按钮一并由 helper 装配）
        # ★ R183 A10-S3b：「平滑」徽标同排（可见性修复——区分"值被平滑"与"输出坏"）
        (self._status_label, self._mode_btn,
         self._smooth_label, _st_wrap) = _build_status_row(self)
        main_layout.insertLayout(0, _st_wrap)


        # ── 主体：左通道格 + 右小键盘 ─────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        # S1 重构：删除与全局主题重复的 QSplitter::handle 样式（走 GLOBAL_QSS）

        # 左：通道格（滚动区）
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)

        # 当前过滤状态（D波：24px 解剖详情行）
        self._filter_status_label = QLabel("输入过滤: 全通")
        self._filter_status_label.setFixedHeight(24)
        self._filter_status_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 0 8px; "
            f"background: {BG_INPUT}; border-radius: 2px;"
        )
        left_lay.addWidget(self._filter_status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 局部布局特化：仅描边；D波补 viewport 底色选择器防原生白底透出
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {SEP_LINE}; background: {BG_ROOT}; }}"
            f"QScrollArea > QWidget {{ background: {BG_ROOT}; }}")

        self._channel_grid = ChannelGrid()
        scroll.setWidget(self._channel_grid)
        left_lay.addWidget(scroll)

        # ★ R183 W2（#16）：同域 输入 vs 输出 对照条（轻量双条，隐藏的过滤
        #   方向双钮语义不符——现状核实见 monitor_io_compare 模块头）
        left_lay.addWidget(build_io_section(self))

        # 底部选中行（D波 24px；W2 补迷你电平条 + 常驻统计 #3/#11）
        sel_row = QWidget()
        sel_lay = QHBoxLayout(sel_row)
        sel_lay.setContentsMargins(0, 0, 0, 0)
        sel_lay.setSpacing(6)
        self._sel_label = QLabel("未选择通道 | 按 Ctrl+A 全选 | 框选或点击选择")
        self._sel_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 0 4px;")
        sel_lay.addWidget(self._sel_label, 1)
        sel_lay.addWidget(build_selection_row(self))
        scope_qss(sel_row, f"border-top: 1px solid {SEP_LINE};")
        left_lay.addWidget(sel_row)

        splitter.addWidget(left_widget)

        # 右：小键盘 + 过滤规则列表
        right_widget = NoSqueezeColumn()   # C1：只滚不挤（外包 QScrollArea）
        right_widget.setFixedWidth(300)
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)

        # R55：域选择器迁至右列顶部（用户点名释放上部空间给格子）
        uni_row = QHBoxLayout()
        uni_row.setContentsMargins(0, 0, 0, 0)
        uni_row.setSpacing(6)
        uni_lbl = QLabel("域 Universe:")
        uni_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        uni_row.addWidget(uni_lbl)
        uni_row.addWidget(self._universe_spin, 1)
        uni_row.addStretch()
        right_lay.addLayout(uni_row)

        # ★ R183 D（深查② Top10 #10）：多域总览条——真工程 25 域不再盲翻。
        # 数据只读 NM._universe_snapshot；点击格走既有 SpinBox 槽切域。
        self._universe_bar = UniverseBar()
        self._universe_bar.set_current(self._current_universe)
        self._universe_bar.universe_clicked.connect(
            self._on_universe_bar_clicked)
        right_lay.addWidget(self._universe_bar)

        # ★ R183 D（深查② Top10 #2）：亮度分布直方图——无 pyqtgraph 静默隐藏。
        if self._hist.available and self._hist.widget() is not None:
            hist_group = PanelSection("亮度分布")
            hist_group.add_widget(self._hist.widget())
            right_lay.addWidget(hist_group)

        # ★ R183 W2（#7）：选中通道 10s 历史曲线——无 pyqtgraph 静默跳过。
        spark_group = build_sparkline_section(self)
        if spark_group is not None:
            right_lay.addWidget(spark_group)

        # ★ R183 W2（#14）：录制 / 回放区（30s 环形缓冲 + 时间轴滑条）。
        right_lay.addWidget(build_recorder_section(self))

        # ★ R183 W2（#8）：导出 CSV（Excel 直开 utf-8-sig）。
        right_lay.addWidget(build_export_row(self))

        # 小键盘（D波：可折叠 PanelSection 分区）
        self._numpad = FilterNumpad()
        kp_group = PanelSection("通道选择 (MA 小键盘)")
        kp_group.add_widget(self._numpad)
        right_lay.addWidget(kp_group)

        # 当前过滤规则（D波：图例行 prop_row 式——色点 + 11px 名称）
        rule_group = PanelSection("当前过滤规则")
        self._input_rule_label = QLabel("输入过滤: 全通")
        self._output_rule_label = QLabel("输出过滤: 全通")
        rule_group.add_widget(self._make_legend_row(_ROW_DOT_INPUT, self._input_rule_label))
        rule_group.add_widget(self._make_legend_row(_ROW_DOT_OUTPUT, self._output_rule_label))
        right_lay.addWidget(rule_group)
        right_lay.addStretch()

        splitter.addWidget(wrap_right_column(right_widget, 300))   # C1：右栏可滚动
        splitter.setSizes([760, 300])
        # ★ R183 W2：扩展区装配后接线（冻结/标注/分组/录制/导出/blink 定时器）
        self._wire_extras()

        main_layout.addWidget(splitter, 1)
        from .monitor_workspace_ui import install
        install(self, splitter)

    def _make_legend_row(self, dot_color: str, text_label: QLabel) -> QWidget:
        """图例行：8px 色点 + 11px 文本（prop_row 式行版式）。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 0, 4, 0)
        h.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 4px;")
        h.addWidget(dot)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        h.addWidget(text_label, 1)
        return row

    def _make_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        # audit-B：QFrame VLine 上 color: 不生效，须用 background/border
        sep.setStyleSheet(f"background: {SEP_LINE}; border: none;")
        return sep

    def _set_status_style(self, kind: str):
        """连接状态文字色统一走中心 STATUS_TEXT（success/dim）。"""
        self._status_label.setStyleSheet(
            f"color: {STATUS_TEXT[kind]}; font-size: 11px;")

    # ── 信号连接 ──────────────────────────────────

    def _connect_signals(self):
        self._channel_grid.selection_changed.connect(self._on_grid_selection)
        self._numpad.rule_changed.connect(self._on_numpad_rule)
        # ★ R183 W2（#16）：入网原始帧 → 输入/输出对照条（有信号才接）
        raw_in = getattr(self._nm, "frame_raw_in", None)
        if raw_in is not None and hasattr(raw_in, "connect"):
            try:
                raw_in.connect(self._on_raw_in)
            except Exception:
                pass

    # ── 事件处理 ──────────────────────────────────

    def _on_universe_spin_changed(self, value: int):
        """★ 域 SpinBox 变化 → 更新当前监控域"""
        self._current_universe = value
        # A10 G20：旧域的峰值状态不得渗入新域显示
        self._peak.reset()
        # R183 D：总览条高亮跟随（装配期 _universe_bar 尚未创建）
        bar = getattr(self, "_universe_bar", None)
        if bar is not None:
            bar.set_current(value)
        # R183 W2：分组/标注/对照视图随域重建（旧域数据不得渗入）
        self._input_frame = None
        self._group_sig = None
        self._refresh_group_view(force=True)
        if self._annot_mode:
            self._apply_annotations()

    def _on_universe_bar_clicked(self, universe: int) -> None:
        """R183 D：总览条点格 → 复用既有 SpinBox 槽切域（不新增切换路径）。"""
        u = int(universe)
        if u <= 0 or u == self._universe_spin.value():
            return
        self._universe_spin.setValue(u)   # valueChanged → _on_universe_spin_changed

    def _snapshot_map(self) -> dict:
        """R183 D：只读多域发送快照（测试可注入 _uni_snapshot_source）。"""
        src = self._uni_snapshot_source
        if callable(src):
            try:
                return src() or {}
            except Exception:
                return {}
        snap = getattr(self._nm, "_universe_snapshot", None)
        return snap if isinstance(snap, dict) else {}

    def _refresh_aux_views(self, data) -> None:
        """R183 D：直方图 + 多域总览条更新（各自内部节流；异常不拖 20Hz 链）。"""
        try:
            self._hist.maybe_update(data)
            bar = self._universe_bar
            if bar is not None:
                bar.maybe_refresh(self._snapshot_map())
        except Exception:
            pass

    # ═══════════════════════════════════════════════
    # 🔥 已替换：加保护的方法
    # ═══════════════════════════════════════════════

    def _set_filter_mode(self, mode: str):
        self._filter_mode = mode
        self._input_btn.setChecked(mode == "input")
        self._output_btn.setChecked(mode == "output")
        if self._pm is None or not hasattr(self._pm, "project") or self._pm.project is None:
            return
        proj = self._pm.project
        rule = proj.input_filter if mode == "input" else proj.output_filter
        self._numpad.set_channels_from_selection(rule.channels)
        self._channel_grid.set_selected_channels(rule.channels)

    def _apply_filter(self):
        if self._pm is None or not hasattr(self._pm, "project") or self._pm.project is None:
            return
        channels = self._channel_grid.get_selected_channels()
        rule = FilterRule(channels=channels.copy(), universe=self._current_universe)
        if self._filter_mode == "input":
            self._pm.set_input_filter(rule)
            if self._nm:
                self._nm.set_input_filter(rule)
            expr = rule.to_expression() or "全通"
            self._input_rule_label.setText(f"输入过滤: {expr}")
            self._filter_status_label.setText(f"输入过滤: {expr}")
            self.input_filter_changed.emit(rule)
        else:
            self._pm.set_output_filter(rule)
            if self._nm:
                self._nm.set_output_filter(rule)
            expr = rule.to_expression() or "全通"
            self._output_rule_label.setText(f"输出过滤: {expr}")
            self.output_filter_changed.emit(rule)

    def _clear_filter(self):
        if self._pm is None or not hasattr(self._pm, "project") or self._pm.project is None:
            return
        rule = FilterRule()
        rule.set_all()
        if self._filter_mode == "input":
            self._pm.set_input_filter(rule)
            if self._nm:
                self._nm.set_input_filter(None)
            self._input_rule_label.setText("输入过滤: 全通")
            self._filter_status_label.setText("输入过滤: 全通")
        else:
            self._pm.set_output_filter(rule)
            if self._nm:
                self._nm.set_output_filter(None)
            self._output_rule_label.setText("输出过滤: 全通")
        self._channel_grid.clear_selection()
        self._numpad.set_channels_from_selection(set())

    def _on_grid_selection(self, channels: set):
        """通道格框选/点选 → 同步到小键盘"""
        self._numpad.set_channels_from_selection(channels)
        n = len(channels)
        self._sel_label.setText(
            f"已选 {n} 个通道" if n > 0 else "未选择通道 | 按 Ctrl+A 全选 | 框选或点击选择"
        )
        # R183 W2（#7）：点击通道后 sparkline 切到该通道（无选择回退游标）
        self._on_selection_extras(channels)

    def _on_numpad_rule(self, channels: set):
        """小键盘 ENTER → 同步到通道格"""
        self._channel_grid.set_selected_channels(channels)
        n = len(channels)
        self._sel_label.setText(f"已选 {n} 个通道")
        self._on_selection_extras(channels)

    def _send_to_track(self):
        """将当前选择发送为轨道录制范围"""
        channels = self._channel_grid.get_selected_channels()
        self.selection_for_track.emit(channels)

    # ── 数据刷新 ──────────────────────────────────

    def _refresh_data(self):
        # 面板不可见(dock 隐藏/折叠)时跳过昂贵的取数+512格刷新;仍按20Hz轻量查连接态。
        _sync_smooth_badge(self)          # A10-S3b：平滑徽标（两分支都刷）
        if not self.isVisible():
            # ★ R178 修（用户实报"开网络了还显示未连接"）：隐藏分支的轻量路径
            #   原先只记账不改标签 → 面板再次可见时 running==last 恒等，标签
            #   永远停在旧文案。标签更新本身极廉价（QLabel.setText），移进来。
            running = bool(getattr(self._nm, 'is_running', False))
            if running != self._last_running_state:
                self._last_running_state = running
                if running:
                    self._status_label.setText("●  已连接")
                    self._set_status_style("success")
                else:
                    self._status_label.setText("●  未连接")
                    self._set_status_style("dim")
            return
        # 修复：_StubNetworkManager 没有 get_universe_data，用 hasattr 守卫
        try:
            if hasattr(self._nm, 'get_universe_data'):
                data = self._nm.get_universe_data(self._current_universe)
            else:
                data = bytes(512)   # stub：返回512个零字节（空域）
            # A10 G20：峰值保持合成后再喂格子（40Hz 混音峰值不再被 20Hz 采样漏看）
            raw = data
            data = self._peak.feed(data)
            self._channel_grid.update_data(data)
            # R183 W2（#5）：直方图/对照条跟"显示帧"走——冻结时全板同帧一致
            # （电平条仍吃实时帧：定格画面也能继续看现场电平）
            shown = self._channel_grid.get_data()
            # R183 D：直方图/多域总览条（各自 ≥200ms / ≥100ms 节流，异常内吞）
            self._refresh_aux_views(shown)
            # R183 W2：电平/统计/录制/sparkline/对照视图（异常内吞）
            self._refresh_extras(data, shown, raw)
        except Exception:
            pass   # 网络未就绪时静默忽略，不让定时器触发崩溃循环
        # 更新网络状态
        running = bool(getattr(self._nm, 'is_running', False))
        if running != self._last_running_state:
            self._last_running_state = running
            if running:
                self._status_label.setText("●  已连接")
                self._set_status_style("success")
            else:
                self._status_label.setText("●  未连接")
                self._set_status_style("dim")

    # ── 窗口控制 ──────────────────────────────────

    def closeEvent(self, e):
        """不关闭，只隐藏"""
        e.ignore()
        self.hide()

    def _on_display_mode_toggled(self, checked: bool) -> None:
        """热图/数值显示模式切换（R178）。"""
        self._mode_btn.setText("数值" if checked else "热图")
        if hasattr(self._channel_grid, "set_display_mode"):
            self._channel_grid.set_display_mode("heatmap" if checked else "value")
