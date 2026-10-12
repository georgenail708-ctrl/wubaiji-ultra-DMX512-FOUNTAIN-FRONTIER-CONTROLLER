# -*- coding: utf-8 -*-
"""调度台·控件轨迹信号 Hub（唯一 Qt 触点）。

职责仅一件事：把各面板的 `controlTrajectoryEvent(key, value)` 聚合信号
转发给 RecordEngine 的 `record_traj_event`（录制态才收；非录制零成本）。

装配（MainWindow）：`hub.attach_panel(programmer_widget)` / `attach_panel(effect_widget)`
——面板级单信号连接，永不因行/控件重建而失效（这正是聚合信号存在的理由）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject


class TrajectorySignalHub(QObject):
    """面板轨迹信号 → RecordEngine 的转发器（可重复 attach，幂等）。"""

    def __init__(self, engine=None, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._attached: list = []

    def set_engine(self, engine) -> None:
        self._engine = engine

    def attach_panel(self, panel) -> None:
        """连接一个面板的 controlTrajectoryEvent（幂等；异常静默）。"""
        if panel is None or panel in self._attached:
            return
        sig = getattr(panel, "controlTrajectoryEvent", None)
        if sig is None:
            return
        try:
            sig.connect(self._on_event)
            self._attached.append(panel)
        except Exception:
            pass

    def detach_all(self) -> None:
        for panel in list(self._attached):
            try:
                panel.controlTrajectoryEvent.disconnect(self._on_event)
            except Exception:
                pass
        self._attached.clear()

    def _on_event(self, key: str, value) -> None:
        eng = self._engine
        if eng is not None and hasattr(eng, "record_traj_event"):
            try:
                eng.record_traj_event(str(key or ""), value)
            except Exception:
                # ★ R193/P0-2：转发失败留 debug 痕（引擎侧另有首错告警）
                import logging
                logging.getLogger(__name__).debug(
                    "轨迹事件转发失败 key=%s", key, exc_info=True)
