"""Programmer共享状态的展示接线，业务真值仍由原Panel/Grid持有。

直接使用Qt 6.11 QObject所有权、QSignalBlocker及原PanelSection API。
固定静默回显语义；
只借行为，不复制上游源码。采用证据见r185-programmer-shared-state-adoption.md。
"""
from __future__ import annotations

import weakref
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, QSignalBlocker, pyqtSlot


def bind_strip_signals(strip: Any, row: Any,
                       range_handler: Callable[..., None] | None = None) -> None:
    """原条带范围与释放成对接线；滑块保留原映射回调。"""
    strip.range_changed.connect(range_handler if range_handler is not None else row._on_range_strip_changed)
    strip.stripReleaseRequested.connect(row._release_strip_layers)


class _CueSectionController(QObject):
    """跟随原Grid寿命，只在空态转换时控制现有分区展开。"""

    def __init__(self, grid: Any, section: Any):
        super().__init__(grid)
        self._grid = weakref.ref(grid)
        self._section = weakref.ref(section)
        self._had_content = bool(grid.get_cues())
        grid.contents_changed.connect(self.refresh)

    @pyqtSlot()
    def refresh(self) -> None:
        grid, section = self._grid(), self._section()
        if grid is None or section is None or sip.isdeleted(grid) or sip.isdeleted(section):
            return
        has_content = bool(grid.get_cues())
        if not has_content:
            section.set_expanded(False)
        elif not self._had_content:
            section.set_expanded(True)
        # 持续非空时不写展开状态，保留操作者最近一次手动折叠。
        self._had_content = has_content


def bind_cue_section(grid: Any, section: Any) -> None:
    """保留Python接收者并由原Grid拥有，销毁时由Qt自动断开。"""
    grid._cue_section_controller = _CueSectionController(grid, section)


def _mirror_row_jitter(row: Any, value: float) -> None:
    """回显不发业务信号；阻断范围结束后恢复原先的信号状态。"""
    if sip.isdeleted(row):
        return
    row._jitter_pct = value
    spin = getattr(row, "_jitter_spin", None)
    if spin is None or sip.isdeleted(spin) or spin.value() == int(value):
        return
    with QSignalBlocker(spin):
        spin.setValue(int(value))


def mirror_jitter_rows(panel: Any) -> None:
    """用户编辑一次后同步全部保留行，隐藏行也沿用同一Panel真值。"""
    value = float(getattr(panel, "_jitter_pct", 0.0) or 0.0)
    rows = getattr(getattr(panel, "_ch_list", None), "_rows", ())
    for row in rows:
        _mirror_row_jitter(row, value)


def sync_jitter_row(row: Any) -> None:
    """原owner建立后同步新建/保留行；独立行保持原本地兜底。"""
    from .programmer_panel import _panel_of
    panel = _panel_of(row)
    if panel is not None:
        _mirror_row_jitter(row, float(getattr(panel, "_jitter_pct", 0.0) or 0.0))
