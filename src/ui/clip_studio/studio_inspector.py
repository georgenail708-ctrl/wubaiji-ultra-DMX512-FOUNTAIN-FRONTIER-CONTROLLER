# -*- coding: utf-8 -*-
"""Studio 常驻属性区：只做原节点/字段/撤销回路的呈现与适配。

参考 节点属性侧栏 与本项目 CardSpec。选中一个节点直接编辑原生
表单，复杂行表/便签保留原双击入口；绑定数为只读实际上下文，无新播放层。
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from src.ui import qascade_theme as _th
from src.ui.widget_kit import PanelSection, scope_qss
from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_cards import CARD_CATALOG
from src.ui.clip_studio.studio_param_form import CardParamForm


def commit_params(panel, sheet_id, node_id, params) -> None:
    """拒绝旧上下文，复用 canvas 的编辑前快照和原 graph_changed 应用回路。"""
    if str(panel._state.get("active_sheet", "")) != str(sheet_id):
        return
    canvas = panel.canvas()
    node = canvas.node_by_id(str(node_id))
    if node is None or node.get("key") == "trans_opt" or node.get("params", {}) == params:
        return
    canvas._begin_undo_op()
    node["params"] = copy.deepcopy(params)
    canvas._commit_graph_change()
    panel._refresh_compile_status(reset_canvas=False)
    canvas.update()


class StudioInspector(QScrollArea):
    """节点对象只在选择/模型变更时刷新，未选择时给出可操作的空态。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._context = None
        self._form = None
        self._node = None
        self._params = None
        self._editing = False
        self.setObjectName("studioProperties")
        self.setMinimumWidth(240)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        scope_qss(self, f"background: {_th.BG_EDITOR}; border-left: 1px solid {_th.SEP_LINE};")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self._heading = QLabel("节点属性")
        self._heading.setStyleSheet("font-weight: bold; background: transparent;")
        layout.addWidget(self._heading)
        self._name = QLabel("")
        self._name.setWordWrap(True)
        layout.addWidget(self._name)
        self._description = QLabel("")
        self._description.setWordWrap(True)
        self._description.setStyleSheet(f"color: {_th.TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(self._description)
        self._section = PanelSection("参数")
        self._form_host = QWidget()
        self._form_layout = QVBoxLayout(self._form_host)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(4)
        self._section.add_widget(self._form_host)
        layout.addWidget(self._section)
        self._scope = QLabel("")
        self._scope.setWordWrap(True)
        self._scope.setStyleSheet(f"color: {_th.TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(self._scope)
        layout.addStretch(1)
        self.setWidget(body)
        self.refresh("")

    def refresh(self, node_id=None) -> None:
        panel = self._panel
        sheet = panel._active_sheet() or {}
        nid = str(panel.canvas()._sel_node if node_id is None else node_id)
        node = panel.canvas().node_by_id(nid)
        context = (str(sheet.get("id", "")), nid)
        self._refresh_scope(context[0])
        if self._editing:
            return
        params = copy.deepcopy((node or {}).get("params", {}))
        if self._context == context and self._node is node and self._params == params:
            return
        self.discard()
        self._context, self._node, self._params = context, node, params
        self._show_node(node, params, context)

    def _show_node(self, node, params, context) -> None:
        spec = CARD_CATALOG.get(str((node or {}).get("key", "")))
        self._section.setVisible(spec is not None and bool(spec.params))
        if spec is None:
            self._name.setText("注释便签" if studio_note.is_note(node or {}) else "选择一个节点")
            self._description.setText("双击便签编辑文本。" if studio_note.is_note(node or {}) else
                                      "点击卡片查看参数；从左侧拖入卡片，连接端口组合处理。")
            return
        self._name.setText(spec.name)
        self._description.setText(spec.desc)
        if spec.key == "trans_opt":
            from src.ui.clip_studio.studio_optimizer import OptimizerActionForm
            self._form = OptimizerActionForm(self._panel, context[1], self._form_host)
            self._form_layout.addWidget(self._form)
            return
        self._form = CardParamForm(spec, params, self._form_host, live=True)
        self._form.values_edited.connect(
            lambda values, sid=context[0], selected=context[1]: self._commit(sid, selected, values))
        self._form_layout.addWidget(self._form)
        if any(ps.type == "rows" for ps in spec.params):
            self._description.setText(f"{spec.desc}\n双击卡片编辑完整通道行表。")

    def _commit(self, sheet_id, node_id, values) -> None:
        self._editing = True
        try:
            self._params = copy.deepcopy(values)
            commit_params(self._panel, sheet_id, node_id, values)
        finally:
            self._editing = False

    def _refresh_scope(self, sheet_id) -> None:
        clips = self._panel._bound_clips(sheet_id)
        names = [str(getattr(clip, "label", "") or getattr(clip, "id", "")) for clip in clips]
        self._scope.setText(f"此页绑定 Clip：{len(clips)}\n编辑后自动应用到所有绑定片段。" if clips
                            else "此页未绑定 Clip。\n在 Sheet 标签的右键菜单管理绑定。")
        self._scope.setToolTip("\n".join(names))

    def flush(self) -> None:
        if self._form is not None:
            self._form.flush()

    def discard(self) -> None:
        if self._form is None:
            return
        self._form.discard()
        self._form_layout.removeWidget(self._form)
        self._form.hide()
        self._form.deleteLater()
        self._form = None


def refresh_inspector(panel, node_id=None) -> None:
    inspector = getattr(panel, "_inspector", None)
    if inspector is not None:
        inspector.refresh(node_id)
