# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_dialogs.py — R183 W2-FX：对话框薄封装（无状态）。

六个模态问答的**统一入口**：文本 / 是否 / 单选 / 浮点 / 选保存路径 / 选打开路径。
存在的理由：
  · 每个问答都要 `try/except`（对话框在无头/主题异常时可能抛）—— 集中一处，
    功能模块的圈复杂度就不会被这些 try 顶穿门禁（§10.2 CC ≤ 8）；
  · 取消语义统一：一律返回"空/假/None"，调用方无需区分"用户取消"与"控件故障"。

参考: 本仓 ``main_window`` 的 QFileDialog 用法（先取路径再判空）。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

logger = logging.getLogger(__name__)

#: 效果预设包的默认文件过滤串
PACK_FILTER = "效果预设包 (*.json);;所有文件 (*.*)"


def ask_text(panel, title: str, label: str, default: str = "") -> str:
    """文本输入；取消/异常 → ""（默认值仅在用户确认时生效）。"""
    try:
        text, ok = QInputDialog.getText(panel, title, label, text=str(default))
        return str(text) if ok else ""
    except Exception:
        logger.debug("effect_browser_w2: text dialog failed", exc_info=True)
        return ""


def ask_yes_no(panel, title: str, text: str) -> bool:
    """是否确认；取消/异常 → False（保守侧 = 不改变现状）。"""
    try:
        ans = QMessageBox.question(panel, title, text,
                                   QMessageBox.StandardButton.Yes |
                                   QMessageBox.StandardButton.No)
        return ans == QMessageBox.StandardButton.Yes
    except Exception:
        logger.debug("effect_browser_w2: question dialog failed", exc_info=True)
        return False


def ask_choice(panel, title: str, label: str, items) -> str:
    """单选；取消/异常 → ""。"""
    options = [str(x) for x in (items or [])]
    if not options:
        return ""
    try:
        item, ok = QInputDialog.getItem(panel, title, label, options, 0, False)
        return str(item) if ok else ""
    except Exception:
        logger.debug("effect_browser_w2: choice dialog failed", exc_info=True)
        return ""


def ask_double(panel, title: str, label: str, *, value: float = 1.0,
               lo: float = 0.0, hi: float = 1.0, decimals: int = 2
               ) -> Optional[float]:
    """浮点输入（带范围，数值参数一律**仅关键字**：门禁位置参数 ≤5）；取消 → None。"""
    try:
        out, ok = QInputDialog.getDouble(panel, title, label, float(value),
                                         float(lo), float(hi), int(decimals))
        return float(out) if ok else None
    except Exception:
        logger.debug("effect_browser_w2: double dialog failed", exc_info=True)
        return None


def ask_save_path(panel, title: str, default_name: str,
                  filters: str = PACK_FILTER) -> str:
    """选保存路径；取消/异常 → ""。"""
    try:
        path, _sel = QFileDialog.getSaveFileName(panel, title, default_name, filters)
        return str(path or "")
    except Exception:
        logger.debug("effect_browser_w2: save dialog failed", exc_info=True)
        return ""


def ask_open_path(panel, title: str, filters: str = PACK_FILTER) -> str:
    """选打开路径；取消/异常 → ""。"""
    try:
        path, _sel = QFileDialog.getOpenFileName(panel, title, "", filters)
        return str(path or "")
    except Exception:
        logger.debug("effect_browser_w2: open dialog failed", exc_info=True)
        return ""


def ask_choice_and_double(panel, title: str, label: str, items, *,
                          value: float = 1.0, lo: float = 0.0, hi: float = 1.0
                          ) -> Tuple[str, Optional[float]]:
    """先单选再浮点（"选槽位 + 给倍数"这类两步问答的组合入口）；取消 → ("", None)。"""
    choice = ask_choice(panel, title, label, items)
    if not choice:
        return "", None
    return choice, ask_double(panel, title, label, value=value, lo=lo, hi=hi)


__all__ = ["PACK_FILTER", "ask_choice", "ask_choice_and_double", "ask_double",
           "ask_open_path", "ask_save_path", "ask_text", "ask_yes_no"]
_ = List
