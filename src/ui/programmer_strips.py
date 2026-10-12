# -*- coding: utf-8 -*-
"""programmer_strips — Programmer 条带控件（项 9/13/16）的模块级注入助手。

为什么独立：`ProgrammerRangeStrip` / `RangeFaderWidget` 与 `ChannelTypeRow` 都是既有
大文件里的既有类（不得新增上帝类方法），条带相关的新逻辑（相对编码 hover 气泡、
双读数标签、抖动%）集中放这里，`programmer_panel.py` 只在既有方法体内加一行调用。

注入点（面板文件内按函数名搜索）：
  `_r183_prepare_range_write`  → `ChannelTypeRow._apply_channel_range`（写值前）
  `_r183_set_range_readout`    → `ChannelTypeRow._set_range_state`（行尾标签）
  `_r183_strip_hover_tooltip`  → 两个条带控件的 `mouseMoveEvent`
  `_r183_strip_label_text`     → `RangeFaderWidget.paintEvent`（条带文字）
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import programmer_tools as _ptool

# ── 写值序 → hover 条带表（项 16）────────────────────────────────────────

def _r183_hover_slots(row: Any, ch_type: str, slots: List[Any]) -> None:
    """把本次写值的 (设备, slot, total) 序交给该通道的条带控件（hover 反查用）。

    覆盖两类控件：常显「范围条带」(`ProgrammerRangeStrip`) 与控制器子页里的
    融合条带 (`RangeFaderWidget`)；两者都有 `_hover_slots` 字段。
    """
    strips = [
        (getattr(row, "_range_strips", None) or {}).get(ch_type),
        (getattr(row, "_ctrl_fader_strips", None) or {}).get(ch_type),
        (getattr(row, "_ctrl_vslider_strips", None) or {}).get(ch_type),
        (getattr(row, "_ctrl_joy_strips", None) or {}).get(ch_type),
    ]
    for strip in strips:
        if strip is not None and hasattr(strip, "_hover_slots"):
            strip._hover_slots = list(slots)


# ── RangeFaderWidget 注入点 ───────────────────────────────────────────────

def _strip_track_rect(strip: Any):
    """条带可拖动轨区（widget 局部坐标）。

    `RangeFaderWidget` 自带 `_track_rect()`；`ProgrammerRangeStrip` 无此方法，
    其轨区几何 = `rect().adjusted(12, 8, -12, -8)`（与自身 `_value_from_x`
    同口径，hover slot 才会与拖动手感一致）。修前此处直接调
    `strip._track_rect()` → ProgrammerRangeStrip hover 即 AttributeError
    （用户实测 traceback：programmer_strips.py:49）。
    """
    fn = getattr(strip, "_track_rect", None)
    if callable(fn):
        return fn()
    return strip.rect().adjusted(12, 8, -12, -8)


def _r183_strip_hover_tooltip(strip: Any, event: Any) -> None:
    """条带 hover 值气泡（项 16）：x → 扇区 slot → "设备名 DMX值(百分比)"。

    无槽位表 / 越界 / 空白 → no-op（不弹空气泡）。
    """
    slots = getattr(strip, "_hover_slots", None)
    if not slots:
        return
    rect = _strip_track_rect(strip)
    if rect.width() <= 0:
        return
    idx = _ptool.slot_index_for_x(event.position().x() - rect.left(),
                                  rect.width(), len(slots))
    if not (0 <= idx < len(slots)):
        return
    fi = slots[idx][0]
    fid = str(getattr(fi, "id", ""))
    name = str(getattr(fi, "name", "") or fid)
    raw = strip._target
    owner = getattr(strip, "_owner_row", None)
    if owner is not None:
        try:
            cur = (owner._values.get(fid) or {}).get(strip._ch_type)
            raw = strip._target if cur is None else cur
        except Exception:
            pass
    from PyQt6.QtWidgets import QToolTip
    QToolTip.showText(event.globalPosition().toPoint(),
                      f"{name}  {_ptool.format_value_dual(raw)}", strip)


def _r183_strip_label_text(strip: Any, is_range: bool) -> str:
    """条带标签文本（项 13 双读数）：`"128(50%)"` / `"0(0%)→128(50%)"`。"""
    mode_tag = {"flat": "", "one_way": " CTRL", "two_way": " SHIFT"}.get(strip._mode, "")
    if is_range:
        return (f"{strip._label}{mode_tag}  "
                f"{_ptool.format_range_dual(strip._base, strip._target)}  翼{strip._wings}")
    return f"{strip._label}  {_ptool.format_value_dual(strip._target)}"


def _r183_set_range_readout(row: Any, ch_type: str, st: dict) -> None:
    """行尾读数标签（项 13）：flat 显 `"128(50%)"`，范围显两端双读数 + 翼/块。"""
    from .programmer_panel import _qobject_alive
    lbl = row._range_value_labels.get(ch_type)
    if _qobject_alive(lbl):
        if st["mode"] == "flat":
            lbl.setText(_ptool.format_value_dual(st["target"]))
        else:
            lbl.setText(f"{_ptool.format_range_dual(st['base'], st['target'])}"
                        f"  ·  翼{row._value_wings}/块{row._value_block_size}")
    elif lbl is not None:
        row._range_value_labels.pop(ch_type, None)


def _r183_prepare_range_write(row: Any, ch_type: str, slots: List[Any]) -> float:
    """写值前的两件事：登记 hover 反查表（项 16）+ 读抖动%（项 10）。返回抖动%。

    抖动从**面板**读（`_set_jitter_of_row` 同步写面板与行），行上兜底。
    """
    _r183_hover_slots(row, ch_type, slots)
    from .programmer_panel import _panel_of
    host = _panel_of(row) or row
    return float(getattr(host, "_jitter_pct", 0.0) or 0.0)
