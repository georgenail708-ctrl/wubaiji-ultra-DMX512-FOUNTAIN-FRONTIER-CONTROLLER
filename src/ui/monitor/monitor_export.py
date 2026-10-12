# -*- coding: utf-8 -*-
"""当前域 CSV 导出（R183 W2；深查② §四 #8）。

需求原文：「CSV/剪贴板导出："导出当前域"（stdlib csv；512 行 ch,value
(+快照 delta)；QFileDialog；Excel 直接开走 utf-8-sig）」。

实现要点：
  * stdlib ``csv``（不新增依赖）；``utf-8-sig`` 写盘 → Excel 双击不乱码；
  * 有快照时追加 ``delta`` 列（值 = 当前 - 快照，带符号），无快照保持两列；
  * QFileDialog 只负责取路径，序列化走纯函数 → 可单测。
"""
from __future__ import annotations

import csv
import io

import numpy as np

from PyQt6.QtWidgets import QFileDialog

CSV_HEADER = ("channel", "value")
CSV_HEADER_DELTA = ("channel", "value", "delta")


def build_csv_text(data, universe: int = 1, snapshot=None) -> str:
    """纯函数：显示帧 (+快照) → CSV 文本（含表头；行数=帧长度）。

    无快照 → 两列；有快照 → 三列 delta（int16 运算防 uint8 回绕，与
    channel_grid_delta.delta_mask_of 同判据）。
    """
    arr = np.asarray(data, dtype=np.uint8).reshape(-1)
    snap = None
    if snapshot is not None:
        snap = np.asarray(snapshot, dtype=np.int16).reshape(-1)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADER_DELTA if snap is not None else CSV_HEADER)
    for i in range(arr.size):
        ch = i + 1
        val = int(arr[i])
        if snap is None:
            writer.writerow((ch, val))
        else:
            ref = int(snap[i]) if i < snap.size else 0
            writer.writerow((ch, val, val - ref))
    return buf.getvalue()


def default_filename(universe: int) -> str:
    """建议文件名（导出对话框默认值；域号写入文件名便于归档）。"""
    return f"monitor_universe{int(universe)}.csv"


def save_csv_file(path: str, text: str) -> str:
    """写盘（utf-8-sig，Excel 直开）；返回实际路径。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)
    return path


def ask_save_path(parent, universe: int) -> str:
    """QFileDialog 取保存路径；取消返回空串。"""
    path, _selected = QFileDialog.getSaveFileName(
        parent, "导出当前域 CSV", default_filename(universe),
        "CSV 文件 (*.csv)")
    return str(path or "")


def export_current_domain(parent, data, universe: int = 1,
                          snapshot=None, path: str = "") -> str:
    """导出入口：无 path 则弹对话框；返回写盘路径（取消返回空串）。"""
    target = path or ask_save_path(parent, universe)
    if not target:
        return ""
    if not target.lower().endswith(".csv"):
        target += ".csv"
    return save_csv_file(target, build_csv_text(data, universe, snapshot))
