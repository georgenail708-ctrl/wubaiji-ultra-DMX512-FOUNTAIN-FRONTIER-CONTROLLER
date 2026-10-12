# -*- coding: utf-8 -*-
"""Draw 常驻属性区，复用原生字段工厂与本图QUndoStack。

参考 专业属性侧栏；标准命令模式
已在 draw_canvas_undo 落实，本件只做完成输入与原节点的薄适配。
"""
from __future__ import annotations

import copy
import math

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (QFrame, QLabel, QListWidget, QListWidgetItem,
                             QScrollArea, QVBoxLayout, QWidget)

from src.ui import qascade_theme as _th
from src.ui.widget_kit import PanelSection, scope_qss
from src.ui.clip_studio.studio_param_form import CardParamForm
from . import draw_graph as dc
from .draw_canvas_undo import _mirror_legacy_fields, snap_node
from .draw_cards import feature_label, template_label, type_label
from .draw_effects import PARAM_SPECS, effect_label, effect_spec
from .draw_param_schema import group_values, rule_groups, rule_values, target_types


def commit_values(canvas, context, node, values) -> None:
    """不换图、不换对象；一个完成编辑仍是一条原节点双态命令。"""
    if id(canvas.graph()) != context[0] or dc.node_by_id(canvas.graph(), context[1]) is not node:
        return
    incoming = copy.deepcopy(values)
    if "phase" in incoming:
        incoming["phase"] = float(incoming["phase"])
        if not math.isfinite(incoming["phase"]):
            raise ValueError("相位需要有限数值")
    if isinstance(incoming.get("invert"), bool):
        incoming["invert"] = float(incoming["invert"])
    current = rule_values(node)
    params = {**current, **incoming}
    if params == current:
        return
    old = snap_node(node)
    node["params"] = params
    node["wave"] = str(params.get("wave", "") or "")
    _mirror_legacy_fields(node, params)
    canvas._undo_push_node_state(context[1], old, "编辑规则属性")
    canvas.update()


class DrawProperties(QScrollArea):
    """只为单选对象持有原生表单；多选显示范围，拒绝晚到的旧编辑。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._context = None
        self._node = None
        self._params = None
        self._targets = None
        self._forms = []
        self._editing = False
        self.setObjectName("drawProperties")
        self.setMinimumWidth(240)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        scope_qss(self, f"background:{_th.BG_EDITOR}; border-left:1px solid {_th.SEP_LINE};")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        heading = QLabel("规则属性")
        heading.setStyleSheet("font-weight:bold; background:transparent;")
        layout.addWidget(heading)
        self._name = QLabel()
        self._name.setWordWrap(True)
        layout.addWidget(self._name)
        self._description = QLabel()
        self._description.setWordWrap(True)
        self._description.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
        layout.addWidget(self._description)
        self._content = QVBoxLayout()
        self._content.setSpacing(8)
        layout.addLayout(self._content)
        self._feedback = QLabel()
        self._feedback.setWordWrap(True)
        self._feedback.setStyleSheet(f"color:{_th.BRIGHT_ERROR}; background:transparent;")
        layout.addWidget(self._feedback)
        self._connections = QLabel()
        self._connections.setWordWrap(True)
        self._connections.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
        layout.addWidget(self._connections)
        layout.addStretch(1)
        self.setWidget(body)
        self.refresh()

    def refresh(self, _nid=None) -> None:
        canvas = self._panel._canvas
        selected = canvas._sel_ids
        nid = next(iter(selected)) if len(selected) == 1 else ""
        node = dc.node_by_id(canvas.graph(), nid)
        context = (id(canvas.graph()), nid)
        self._refresh_connections(node)
        if self._editing:
            return
        params = rule_values(node) if node and node.get("kind") == "rule" else None
        targets = copy.deepcopy((node or {}).get("ch_types", []))
        if self._context == context and self._node is node and self._params == params and self._targets == targets:
            return
        if self._context and self._context[0] == context[0] and self._context != context:
            self.flush()
        self.discard()
        self._context, self._node, self._params, self._targets = context, node, params, targets
        self._show_node(node, selected)

    def _show_node(self, node, selected) -> None:
        if node is None:
            self._name.setText(f"已选择 {len(selected)} 张卡" if selected else "选择一张规则卡")
            self._description.setText("多选用于画布整理；单选规则卡编辑属性。" if selected else
                                      "从左侧点选或拖入卡片，源卡右端连接规则卡左端。")
            return
        if node.get("kind") == "source":
            self._name.setText(feature_label(node.get("feature", "")))
            template = template_label(node.get("template", "")) if node.get("template") else "无模板"
            self._description.setText(f"信号源 · {template}\n相位 {node.get('phase', 0.0)}\n右端为信号输出。")
            return
        self._name.setText(effect_label(node.get("effect", "")))
        self._description.setText(str(effect_spec(node.get("effect", ""))[3]))
        for spec in rule_groups(node):
            self._add_form(spec)
        self._add_targets(node)

    def _add_form(self, spec) -> None:
        section = PanelSection(spec.name)
        form = CardParamForm(spec, group_values(spec, self._params), section, live=True)
        context, node = self._context, self._node
        form.values_edited.connect(lambda values, ctx=context, obj=node: self._commit(ctx, obj, values))
        for ps in spec.params:
            widget = form.findChild(QWidget, ps.key)
            if widget is not None:
                widget.setToolTip(PARAM_SPECS[ps.key][2])
        for widget in form.findChildren(QWidget):
            widget.installEventFilter(self)
        section.add_widget(form)
        self._forms.append(form)
        self._content.addWidget(section)

    def _add_targets(self, node) -> None:
        section = PanelSection("目标通道")
        targets = QListWidget()
        targets.setObjectName("drawPropertyTargets")
        targets.setMaximumHeight(160)
        enabled = set(node.get("ch_types") or [node.get("ch_type", "")])
        for key in target_types(self._panel._canvas):
            item = QListWidgetItem(type_label(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in enabled else Qt.CheckState.Unchecked)
            targets.addItem(item)
        context, obj = self._context, node
        targets.itemChanged.connect(lambda item, ctx=context, n=obj: self._target_changed(ctx, n, item))
        section.add_widget(targets)
        self._content.addWidget(section)

    def _target_changed(self, context, node, item) -> None:
        canvas = self._panel._canvas
        if self._context != context or dc.node_by_id(canvas.graph(), context[1]) is not node:
            return
        self._editing = True
        try:
            canvas._toggle_target(node, str(item.data(Qt.ItemDataRole.UserRole)))
            self._targets = copy.deepcopy(node.get("ch_types", []))
        finally:
            self._editing = False

    def _commit(self, context, node, values) -> None:
        if self._context != context:
            return
        self._editing = True
        try:
            commit_values(self._panel._canvas, context, node, values)
            self._params = rule_values(node)
            self._feedback.clear()
        except (TypeError, ValueError) as exc:
            self._feedback.setText(str(exc))
        finally:
            self._editing = False

    def _refresh_connections(self, node) -> None:
        nid = str((node or {}).get("id", ""))
        edges = self._panel._canvas.graph().get("edges") or []
        incoming = sum(1 for edge in edges if str(edge.get("to", "")) == nid) if nid else 0
        outgoing = sum(1 for edge in edges if str(edge.get("from", "")) == nid) if nid else 0
        self._connections.setText(f"已连接 {outgoing} 条规则\n编辑后需要显式应用。" if node and node.get("kind") == "source"
                                  else f"输入连线 {incoming} 条\n原卡内控件与属性区编辑同一规则；完成编辑可撤销，应用后写入工程。")

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.discard()
            self.refresh()
            return True
        return super().eventFilter(obj, event)

    def flush(self) -> bool:
        for form in tuple(self._forms):
            form.flush()
        return not bool(self._feedback.text())

    def discard(self) -> None:
        for form in self._forms:
            form.discard()
        self._forms = []
        while self._content.count():
            widget = self._content.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._context = None
        self._feedback.clear()

    def hideEvent(self, event):
        self.flush()
        super().hideEvent(event)


def discard_properties(canvas) -> None:
    properties = getattr(canvas, "_properties", None)
    if properties is not None:
        properties.discard()
