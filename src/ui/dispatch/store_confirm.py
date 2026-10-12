# -*- coding: utf-8 -*-
"""存场景前的**确认对话框**（把"将存什么"亮给用户看，杜绝"乱存"）。

★ 用户痛点（R178）："存效果乱存——连带其它页面/其它组"。
  `_current_effect_nodes` 只回"未被 recall 的真实编辑节点"（clip 与
  `extra["recalled"]` 已在 `_is_editable_node` 过滤），但用户在存之前
  **看不到**将存进这张卡的清单 —— 心里没底、存错了也只能删。
  这里在 `_do_save` 之前弹一张轻量清单（面板 / 效果名 / 设备组 / 设备数），
  用户确认才落盘。

设计纪律：
  · 轻量：`QMessageBox` + 纯文本条目（不建表格/树，不塞上帝类）；
  · 可测：清单文案生成是纯函数（`build_save_summary`），对话框只是壳；
  · 只在**空槽保存**主路径弹（2026-09-14 定案的用户主入口）。
"""
from __future__ import annotations

import os
from typing import List

from PyQt6.QtWidgets import QMessageBox

#: 面板 id → 显示名（清单里给用户看的；未知面板回退原 id）
_PANEL_LABELS = {
    "effect": "效果",
    "programmer": "程序员",
    "matrix": "矩阵",
    "audio": "音频",
    "linker": "轨道联动",
    "route": "路线动画",
    "joystick": "摇杆",
    "clip": "时间线",
}

#: 清单最多显示条数（更多时折叠为"…等 N 项"，防超长对话框）
_MAX_LINES = 12


def _panel_label(panel: str) -> str:
    return _PANEL_LABELS.get(str(panel or ""), str(panel or "未知名"))


def _node_line(n) -> str:
    """一个节点 → 一行清单文案（面板 · 效果名 · 设备组 · N 台设备）。"""
    panel = _panel_label(getattr(n, "panel", ""))
    effect = str(getattr(n, "effect_name", "") or "").strip()
    group = str(getattr(n, "group_name", "") or "").strip()
    try:
        count = len([x for x in (getattr(n, "fixture_ids", []) or []) if str(x)])
    except Exception:
        count = 0
    who = effect or ("静态通道" if str(getattr(n, "panel", "")) == "programmer" else "（未命名）")
    where = group or "临时选中"
    return f"· [{panel}] {who} · 设备组：{where}（{count} 台）"


def build_save_summary(nodes) -> str:
    """节点清单 → 对话框正文（纯函数，供单测）。"""
    lines = [_node_line(n) for n in (nodes or [])]
    if len(lines) > _MAX_LINES:
        head = lines[:_MAX_LINES]
        return "\n".join(head) + f"\n… 等共 {len(lines)} 项"
    return "\n".join(lines)


def confirm_save_nodes(parent, nodes) -> bool:
    """弹"将存以下 N 项"确认框；用户点「存」返回 True，否则 False。

    `nodes` 为空时返回 False（调用方已有空清单提示，这里不重复弹）。
    ★ 面板**不可见**（程序化调用 / 无头测试）时直接放行 —— 模态框在没有
      真实交互者时会永久阻塞；真实用户点空槽时面板必然可见，不受影响。
    """
    items = list(nodes or [])
    if not items:
        return False
    if _no_interactive_session():
        return True
    # ★ 审计二次补强：panel 自带 _quiet_dialogs（项目既有静默约定，r130 测试
    #   经 _world→MainWindow 构造链设置）→ 无头语义，直接放行。
    quiet = getattr(parent, "_quiet_dialogs", None)
    if quiet:
        return True
    if parent is not None and not _is_shown(parent):
        return True
    # ★ R178 审计终修：走 **QMessageBox.question 静态方法**——项目测试基建
    #   （tests/conftest.py:278）全局把它 patch 成"无阻塞返回 Yes"，无头测试
    #   天然兼容（旧实例 .exec() 在 dock 可见性判据漏掉的路径下永久阻塞，
    #   py-spy 栈铁证 ×2）；生产路径 question() 弹真模态框，行为不变。
    ans = QMessageBox.question(
        parent, "确认保存场景",
        "将把以下 %d 项正在编辑的效果存进该场景卡：\n\n%s"
        % (len(items), build_save_summary(items)),
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
    )
    # ★ 审计注：conftest 全局 patch question 返回 Yes——按「非 Cancel/No 即
    #   确认」判定（Yes 与 Ok 都放行），用户点取消/ESC 不存。
    return ans not in (QMessageBox.StandardButton.Cancel,
                       QMessageBox.StandardButton.No)


def _is_shown(parent) -> bool:
    """父面板当前是否真实可见（异常一律按可见处理 = 宁弹窗不误放行）。"""
    try:
        return bool(parent.isVisible())
    except Exception:
        return True


def _no_interactive_session() -> bool:
    """无交互会话（无头测试/offscreen）：应用没有任何可见顶层窗口时 True。

    ★ R178 审计补强：旧守卫只看 parent.isVisible()，但 dock 内嵌面板的可见性
    随主窗布局变化——真实主窗初始化路径会把 dispatch dock show 出来，无头测试
    走同一构造链即 isVisible=True → exec() 永久阻塞（r130 全量卡死栈铁证）。
    全局无可见顶层窗口 = 一定没有真人可点这个弹窗。
    """
    # ★ 终版判据（py-spy 铁证迭代）：QT_QPA_PLATFORM=offscreen = 程序化会话
    #   （测试/探针）——真人在场必然非 offscreen。可见窗口计数不可靠：主窗
    #   close 后浮动 QDockWidget 仍是可见顶层窗口（实测复现）。
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return True
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return True
        return not any(w.isVisible() and w.isWindow()
                       for w in app.topLevelWidgets())
    except Exception:
        return False


__all__ = ["build_save_summary", "confirm_save_nodes", "_node_line",
           "_panel_label"]
