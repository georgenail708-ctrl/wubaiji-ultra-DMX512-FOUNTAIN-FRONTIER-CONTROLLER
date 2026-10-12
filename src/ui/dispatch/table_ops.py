# -*- coding: utf-8 -*-
"""调度台表格视图的**交互策略层**：单位/行模式偏好 + 右键菜单 + 设值对话框。

职责边界：表视图（`table_view.py`）只管画与命中；本模块把"用户想干什么"翻译成
对 `CellOps` 的调用与对面板的回调。分开放的理由：
  · 右键菜单有 10+ 项，混进 paintEvent 所在文件会把 500 行预算撑爆（架构门禁）；
  · 菜单构造需要 Qt 控件（QMenu/QInputDialog），而表视图应尽量只依赖 QPainter；
  · 偏好读写（QSettings）是**另一个职责**，与渲染无关，便于单测。

偏好（单位 / 行模式）存 `QSettings("Qascade", "DispatchPanel")`，**不进工程数据**——
它是"我这台机器习惯怎么看表"，不是工程内容（跟随工程会让协作方视角被改）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QInputDialog, QMenu, QMessageBox

from ...processing.dispatch.table_model import (
    COL_GROUP_MERGE,
    COL_GROUP_SOURCE,
    COL_GROUPINGS,
    ROW_MODE_GROUPS,
    ROW_MODE_LABELS,
    ROW_MODE_EFFECT_ONLY,
    ROW_MODES,
)
from ...processing.dispatch.fixture_table import UNIT_LABELS, UNITS

_SETTINGS_ORG = "Qascade"
_SETTINGS_APP = "DispatchPanel"
_KEY_UNIT = "view/unit_v1"
_KEY_ROW_MODE = "view/row_mode_v1"
_KEY_COL_GROUP = "view/col_group_v1"


# ── 偏好 ────────────────────────────────────────────────────────────────────
def load_unit() -> str:
    """读取记忆的显示单位（非法值回退 DMX）。"""
    try:
        val = str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_KEY_UNIT, "dmx",
                                                                type=str) or "dmx")
    except Exception:
        return "dmx"
    return val if val in UNITS else "dmx"


def save_unit(unit: str) -> None:
    if unit not in UNITS:
        return
    try:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_KEY_UNIT, str(unit))
    except Exception:
        pass


def load_row_mode() -> str:
    """读取记忆的行排布模式（默认"按组整合"）。

    ★ 迁移：历史版本有过 `"all"`（逐台展开）模式，已按用户要求**取消**。
      旧偏好值读到后不再是合法模式 → 回退 `groups`，避免"读回旧值 → 行模式非法"。
    """
    try:
        val = str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(
            _KEY_ROW_MODE, ROW_MODE_GROUPS, type=str) or ROW_MODE_GROUPS)
    except Exception:
        return ROW_MODE_GROUPS
    return val if val in ROW_MODES else ROW_MODE_GROUPS


def save_row_mode(mode: str) -> None:
    if mode not in ROW_MODES:
        return
    try:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_KEY_ROW_MODE, str(mode))
    except Exception:
        pass


def load_col_group() -> str:
    """读取"列/展示分组"偏好（★ 默认**按来源**——各区标题/列都在，clip 不会被挤掉）。

    ★ 2026-09-14 用户定案：默认按来源分区；合并视图作为**可选**（工具栏「分组」按钮切）。
      旧版本没有这个键 → 取默认 `source`。
    """
    try:
        val = str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(
            _KEY_COL_GROUP, COL_GROUP_SOURCE, type=str) or COL_GROUP_SOURCE)
    except Exception:
        return COL_GROUP_SOURCE
    return val if val in COL_GROUPINGS else COL_GROUP_SOURCE


def save_col_group(mode: str) -> None:
    if mode not in COL_GROUPINGS:
        return
    try:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_KEY_COL_GROUP, str(mode))
    except Exception:
        pass


# ── 设值对话框 ──────────────────────────────────────────────────────────────
def ask_value(parent, current: int = 0, unit: str = "dmx") -> Optional[int]:
    """弹设值对话框；返回 0-255 的目标值，取消返回 None。

    同时给出三种单位下的等价值（的设值体验：不管我用哪种单位看表，
    输入时都能按当时的单位想），因此提示文本里带上换算。
    """
    cur = max(0, min(255, int(current or 0)))
    hint = (f"当前 {cur}（DMX）　= {int(round(cur * 100 / 255))}%　"
            f"= {cur:02X}（HEX）")
    tip = f"0-255\n{hint}"
    val, ok = QInputDialog.getInt(parent, "设值", f"目标值（当前单位 {UNIT_LABELS.get(unit, unit)}）：",
                                  cur, 0, 255, 1)
    if not ok:
        return None
    return int(val)


# ── 右键菜单 ────────────────────────────────────────────────────────────────
#: 菜单动作标识（视图/面板据此分支；用字符串便于日志与测试断言）
ACT_SET_VALUE = "set_value"
ACT_SET_FULL = "set_full"
ACT_SET_ZERO = "set_zero"
ACT_CLEAR = "clear"
ACT_CLEAR_TYPE = "clear_value_type"
ACT_RELEASE_COL = "release_column"
ACT_MARK = "mark"
ACT_UNMARK = "unmark"
ACT_HIDE_COL = "hide_column"
ACT_ROW_SET = "row_set"
ACT_ROW_CLEAR = "row_clear"
ACT_ROW_ONLY = "row_only"
ACT_ROW_LOCATE = "row_locate"
ACT_COPY = "copy"
ACT_PASTE = "paste"
ACT_CALL = "call"


def build_cell_menu(parent, global_pos=None, *, has_selection: bool, is_agg: bool,
                    has_value_type: bool, clipboard: bool) -> Optional[str]:
    """格右键菜单 → 返回被选动作标识（取消返回 None）。

    ★ 菜单项逐条对齐 截图，并**刻意把两个易混语义并列摆出**：
        「设为 0（有效果）」写值 0 且 active=True（有效果、只是暗）
        「清空所选格」    active=False（真清空、不再算有效果）
      用户最容易在这里误解，所以不做合并、不做二次确认。
    """
    m = QMenu(parent)
    m.addAction("设值…", lambda: None).setData(ACT_SET_VALUE)
    m.addAction("设为 255（全开）").setData(ACT_SET_FULL)
    m.addAction("设为 0（有效果）").setData(ACT_SET_ZERO)
    m.addSeparator()
    m.addAction("标记保存").setData(ACT_MARK)
    m.addAction("取消标记").setData(ACT_UNMARK)
    m.addSeparator()
    act_clear = m.addAction("清空所选格" + ("（整行 N 台）" if is_agg else ""))
    act_clear.setData(ACT_CLEAR)
    if has_value_type:
        m.addAction("仅清空当前值类型").setData(ACT_CLEAR_TYPE)
    m.addSeparator()
    m.addAction("复制值").setData(ACT_COPY)
    act_paste = m.addAction("粘贴值")
    act_paste.setData(ACT_PASTE)
    act_paste.setEnabled(bool(clipboard))
    if not has_selection:
        act_clear.setEnabled(False)
    chosen = m.exec(global_pos) if global_pos is not None else m.exec()
    return chosen.data() if chosen is not None else None


def build_column_menu(parent, global_pos=None, *, col_label: str) -> Optional[str]:
    """列头右键菜单：整列设为 / 仅清空该值类型 / 释放该列 / 隐藏该列。"""
    m = QMenu(parent)
    m.addAction(f"全列设为…（{col_label}）").setData(ACT_SET_VALUE)
    m.addAction("仅清空该值类型").setData(ACT_CLEAR_TYPE)
    m.addAction("释放该列（全部设备）").setData(ACT_RELEASE_COL)
    m.addSeparator()
    m.addAction("隐藏该列").setData(ACT_HIDE_COL)
    chosen = m.exec(global_pos) if global_pos is not None else m.exec()
    return chosen.data() if chosen is not None else None


def build_row_menu(parent, global_pos=None, *, row_label: str,
                   row_only_allowed: bool = True) -> Optional[str]:
    """行头右键菜单：整行设为 / 整行清空 / 只显示该设备 / 定位到 TopView。"""
    m = QMenu(parent)
    m.addAction(f"整行设为…（{row_label}）").setData(ACT_ROW_SET)
    m.addAction("整行清空").setData(ACT_ROW_CLEAR)
    m.addSeparator()
    act_only = m.addAction("只显示该设备")
    act_only.setData(ACT_ROW_ONLY)
    act_only.setEnabled(bool(row_only_allowed))
    m.addAction("定位到 TopView").setData(ACT_ROW_LOCATE)
    chosen = m.exec(global_pos) if global_pos is not None else m.exec()
    return chosen.data() if chosen is not None else None


__all__ = [
    "load_unit", "save_unit", "load_row_mode", "save_row_mode",
    "load_col_group", "save_col_group",
    "ask_value", "build_cell_menu", "build_column_menu", "build_row_menu",
    "ACT_SET_VALUE", "ACT_SET_FULL", "ACT_SET_ZERO", "ACT_CLEAR",
    "ACT_CLEAR_TYPE", "ACT_RELEASE_COL", "ACT_MARK", "ACT_UNMARK",
    "ACT_HIDE_COL", "ACT_ROW_SET", "ACT_ROW_CLEAR", "ACT_ROW_ONLY",
    "ACT_ROW_LOCATE", "ACT_COPY", "ACT_PASTE", "ACT_CALL",
]
