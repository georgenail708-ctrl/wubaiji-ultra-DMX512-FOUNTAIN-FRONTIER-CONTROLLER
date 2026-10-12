"""浮动编辑器沿Qt parent寻找真实工程宿主；迁自Draw原owner适配。

QWidget.window()在浮动dock中返回QDockWidget，不能代表工程主窗。
保留独立构造时的原window回退，不创建新工程/窗口注册表。
"""
from __future__ import annotations


def find_project_window(widget):
    owner = widget
    while owner is not None:
        if callable(getattr(owner, "_project", None)):
            return owner
        parent = getattr(owner, "parentWidget", None)
        owner = parent() if callable(parent) else None
    return widget.window()
