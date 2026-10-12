# -*- coding: utf-8 -*-
"""matrix_child_window.py — Matrix 面板的独立子窗口容器（R183）。

用户规格（R183）::

    幻灯片 / 像素效果不再被 QTabWidget 收在同一窗格里当"表单页"，
    而是各自一个**独立顶层窗口**：可同时打开、可分别移动缩放、关闭即隐藏。

设计（照本仓既有两个独立窗口先例，不发明新范式）::

  * ``src/ui/route_animator/runner_panel.py:1540`` —
    ``QWidget(parent, Qt.WindowType.Window)`` + ``closed`` 信号 + 关闭即隐藏；
  * ``src/ui/lab/lab_window.py:48`` — 同款顶层窗口单例。

关键取舍（Qt 文档见报告 URL 节）::

  * parent 仍指向宿主面板 —— Qt 的父子链随之生效：宿主销毁时子窗一并销毁，
    程序退出无残留窗口；同时指定 ``Qt::Window`` 后几何不再受 parent 约束
    （Qt: "it will be a child of parent, and be constrained by parent's geometry
    (unless you specify Qt::Window as window flag)"）。
  * ``WA_DeleteOnClose=False`` → 关闭只 ``hide()``：窗口对象、内部控件与几何
    尺寸在整个会话内复用，重开 ``show()+raise_()`` 即回到原位，无重建、无状态
    丢失（也避免"关一次窗口，幻灯片/像素参数面板就重建一次"的隐患）。
  * ``WA_QuitOnClose=False`` → 辅助窗**不参与"最后一个窗口关闭即退出"**判定。
    Qt 默认为所有 ``Qt::Window`` 置该属性（本仓实测：``QWidget(parent, Window)``
    的 ``testAttribute(WA_QuitOnClose)`` 为 True），若不关掉，用户开着矩阵子窗
    点主窗关闭时主窗虽关、进程却被这个辅助窗"吊住"不退。辅助视图随宿主 dock
    走，不该延长应用寿命。
  * show/hide 事件回调宿主 ``_sync_visibility_gate()``：25fps 主循环与 UI 预览
    门控从旧单窗假设（只看宿主面板是否可见）升级为
    **「主窗或任一子窗可见即刷」** —— 关掉 dock 但子窗还开着时预览不停。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


class MatrixChildWindow(QWidget):
    """矩阵独立子窗口：parent=宿主面板、关闭=隐藏、会话内复用同一实例。"""

    def __init__(self, panel: Optional[QWidget], title: str,
                 size: tuple = (980, 640), min_size: tuple = (640, 420)):
        super().__init__(panel, Qt.WindowType.Window)
        self._panel = panel
        self.setWindowTitle(title)
        # 关闭 = hide（不销毁）：状态与尺寸会话保留；重开 show()+raise_()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 辅助窗不吊住进程：主窗关闭即退出（Qt 默认 True，须显式关掉）
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.resize(*size)
        self.setMinimumSize(*min_size)
        self.hide()

    # ── 可见性 → 宿主门控 ──────────────────────────────────────────────────
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._notify_panel()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._notify_panel()

    def _notify_panel(self) -> None:
        """可见性变化 → 宿主重算「主窗或任一子窗可见」的 25fps / 预览门控。"""
        cb = getattr(self._panel, "_sync_visibility_gate", None)
        if cb is not None:
            cb()
