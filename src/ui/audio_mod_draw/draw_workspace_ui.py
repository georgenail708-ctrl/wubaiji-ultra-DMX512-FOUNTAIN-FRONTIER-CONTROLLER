# -*- coding: utf-8 -*-
"""Draw 库/画布/属性工作台装配；业务仍走原panel与本图撤销。

参考 专业属性侧栏，使用现有Qt QSplitter、
FlowLayout（Qt BSD示例的仓内适配）、Tabler MIT图标与同节点属性工厂。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from src.ui import qascade_theme as _th
from src.ui.widget_kit import scope_qss
from src.ui.flow_layout import FlowLayout
from src.ui.main_icon_library import load_tabler_icon
from src.ui.workspace_owner import find_project_window
from .draw_canvas import DrawCanvas
from .draw_graph import default_rule_types
from .draw_palette import SourcePalette
from .draw_properties_ui import DrawProperties
from .draw_processor_draft import ProcessorDraft
from .draw_processor_chain_ui import ProcessorChainPage

logger = logging.getLogger(__name__)


def build_workspace(panel) -> None:
    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(panel._build_top_bar())
    panel._palette = _build_palette(panel)
    panel._canvas = DrawCanvas()
    panel._processor_draft = ProcessorDraft(panel)
    panel._properties = DrawProperties(panel)
    panel._canvas._properties = panel._properties
    panel._canvas.undo_redo_state_changed.connect(panel._on_undo_state)
    panel._canvas.graph_changed.connect(panel._on_graph_changed)
    panel._canvas.selection_changed.connect(lambda _nid: panel._refresh_summary())
    panel._canvas.card_dropped.connect(panel.add_payload_at)
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setObjectName("drawWorkspaceSplit")
    split.setHandleWidth(4)
    split.addWidget(panel._palette)
    split.addWidget(panel._canvas)
    split.addWidget(panel._properties)
    split.setCollapsible(1, False)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setStretchFactor(2, 0)
    split.setSizes([240, 720, 300])
    panel._workspace_split = split
    panel._workspace_tabs = QTabWidget()
    panel._workspace_tabs.setObjectName("drawWorkspacePages")
    panel._processor_chain = ProcessorChainPage(panel)
    panel._workspace_tabs.addTab(panel._processor_chain, "处理链")
    panel._workspace_tabs.addTab(split, "信号图")
    # Keep original canvas visible for old graph gestures and saved projects.
    panel._workspace_tabs.setCurrentWidget(split)
    root.addWidget(panel._workspace_tabs, 1)


def _build_palette(panel):
    palette = SourcePalette(default_rule_types(()))
    palette.layout().setContentsMargins(8, 8, 8, 8)
    palette.layout().setSpacing(8)
    palette.drag_started.connect(panel._on_drag_started)
    palette.entry_clicked.connect(panel._on_entry_clicked)
    palette.preset_save_requested.connect(panel._on_preset_save)
    palette.preset_load_requested.connect(panel.load_preset)
    palette.preset_import_requested.connect(panel._on_preset_import)
    palette.preset_export_requested.connect(panel._on_preset_export)
    palette.preset_rename_requested.connect(panel._on_preset_rename)
    palette.preset_delete_requested.connect(panel._on_preset_delete)
    panel._palette = palette
    panel._refresh_presets()
    return palette


def _button(text, tooltip, callback, *, icon=""):
    button = QPushButton(text)
    button.setToolTip(tooltip)
    button.setMinimumHeight(28)
    if icon:
        button.setIcon(load_tabler_icon(icon, 16))
    button.clicked.connect(callback)
    return button


def build_top_bar(panel) -> QWidget:
    bar = QWidget()
    scope_qss(bar, f"background:{_th.BG_PANEL_SUB}; border-bottom:1px solid {_th.SEP_LINE};")
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(4)
    title = QLabel("音频调制 · Draw")
    title.setStyleSheet(f"color:{_th.TEXT_PRIMARY}; font-weight:bold; background:transparent;")
    layout.addWidget(title)
    panel._summary = QLabel()
    panel._summary.setWordWrap(True)
    panel._summary.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
    layout.addWidget(panel._summary)
    actions = QWidget()
    flow = FlowLayout(actions)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(4)
    panel._undo_btn = _button("撤销", "撤销 (Ctrl+Z)", lambda: panel._canvas.undo(), icon="arrow-back-up")
    panel._redo_btn = _button("重做", "重做 (Ctrl+Y)", lambda: panel._canvas.redo(), icon="arrow-forward-up")
    panel._undo_btn.setEnabled(False)
    panel._redo_btn.setEnabled(False)
    fit = _button("全览", "全览 (Home)", panel._canvas_fit, icon="arrows-maximize")
    panel._mapping_btn = _button("映射页", "识别通道与波形映射", lambda: panel._properties.flush())
    panel._apply_btn = _button("应用", "编译当前图并提交工程；输出仍由原播放链负责", panel.apply_now)
    panel._close_btn = _button("关闭", "关闭编辑页", lambda: panel._properties.flush())
    for button in (panel._undo_btn, panel._redo_btn, fit, panel._mapping_btn, panel._apply_btn, panel._close_btn):
        flow.addWidget(button)
    layout.addWidget(actions)
    panel._apply_status = QLabel()
    panel._apply_status.setWordWrap(True)
    layout.addWidget(panel._apply_status)
    panel._apply_error = ""
    panel._apply_note = ""
    return bar


def refresh_summary(panel) -> None:
    graph = panel._canvas.graph()
    nodes = graph.get("nodes") or []
    sources = sum(1 for node in nodes if node.get("kind") == "source")
    rules = sum(1 for node in nodes if node.get("kind") == "rule")
    edges = len(graph.get("edges") or [])
    scope = panel._scope_track_id or panel._state.get("scope_folder_id", "") or "未绑定"
    selected = len(panel._canvas._sel_ids)
    dirty = " · 未应用" if panel._dirty_hint else ""
    panel._summary.setText(f"范围 {scope} · {len(panel._scope_channels)} 类型 · {panel._open_at_sec:.2f}s\n"
                           f"源卡 {sources} · 规则卡 {rules} · 连线 {edges} · 选中 {selected}{dirty}")
    message = panel._apply_error or ("未应用：当前图的编辑尚未提交。" if panel._dirty_hint else panel._apply_note)
    panel._apply_status.setText(message)
    panel._apply_status.setVisible(bool(message))
    tint = _th.BRIGHT_ERROR if panel._apply_error else _th.TEXT_SECONDARY
    panel._apply_status.setStyleSheet(f"color:{tint}; background:transparent;")
    panel._properties.refresh()
    panel._processor_chain.refresh_status()


def _failed(panel, text) -> None:
    panel._dirty_hint = True
    panel._apply_error = text
    panel._refresh_summary()


def _host(panel):
    """浮动dock仍沿Qt parent找到实际主窗，保持原工程/共享轨作用域。"""
    return find_project_window(panel)


def sync_share_track(panel, payload):
    try:
        timeline = getattr(_host(panel), "_timeline_widget", None)
        track = getattr(timeline, "_audio_mod_track", None)
        if track is not None and hasattr(track, "absorb_state"):
            track.absorb_state(payload)
    except Exception:
        logger.debug("draw share track sync failed", exc_info=True)
        return False
    return None


def _submit_payload(panel, payload) -> str:
    project_getter = getattr(_host(panel), "_project", None)
    if callable(project_getter) and project_getter() is None:
        return "未提交：当前没有工程，图仍待应用。"
    if callable(panel.applied) and panel.applied(payload) is False:
        return "提交未成功：当前图仍待应用。"
    return ""


def apply_graph(panel) -> None:
    """保留原Apply，只在成功回调后更新已应用基态；异常保留图/pending。"""
    if not panel._properties.flush():
        _failed(panel, "属性输入无效：修正后应用，当前图仍待应用。")
        return
    if not panel._processor_chain.flush():
        _failed(panel, "处理链参数无效：修正后应用，当前编辑仍待应用。")
        return
    panel._canvas._settle_slider()
    try:
        payload = panel.compile_payload()
    except Exception:
        logger.warning("draw compile failed", exc_info=True)
        _failed(panel, "编译失败：当前图仍待应用。")
        return
    try:
        error = _submit_payload(panel, payload)
    except Exception:
        logger.warning("draw apply callback failed", exc_info=True)
        _failed(panel, "提交失败：当前图仍待应用，可重试。")
        return
    if error:
        _failed(panel, error)
        return
    panel._state = payload
    if panel._sync_share_track(payload) is False:
        _failed(panel, "状态已提交，但共享轨同步失败；当前图仍待应用。")
        return
    panel._dirty_hint = False
    panel._apply_error = ""
    panel._apply_note = "已提交调制状态；播放与输出按原调制链处理。" if callable(panel.applied) else "本地图已编译。"
    panel._refresh_summary()


def merged_rule_types(panel) -> list:
    """换工程时从本次scope起算，保留所有已存规则目标的回显。"""
    base = default_rule_types(panel._scope_channels)
    stored = [str(key) for rule in panel._state.get("rules") or [] if isinstance(rule, dict)
              for key in rule.get("ch_types") or [rule.get("ch_type", "")] if key]
    return list(dict.fromkeys((*base, *stored)))
