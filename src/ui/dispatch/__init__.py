# -*- coding: utf-8 -*-
"""调度台前端包（Qt）：监控区自绘窗格 + 保存区卡片 + 调度台面板。

- `monitor_cells.EffectBusView`  左监控区：自绘窗格 + 框选/多选
- `dispatch_panel.DispatchPanel` 调度台面板：顶栏 + 左监控 + 右保存 + 状态栏

职责边界：本包**只做 UI 与交互**；抓/还原状态由 MainWindow 通过
`set_services(capture_state, recall_nodes)` 注入（MainWindow 是路由层）。
"""
from .dispatch_panel import DispatchPanel  # noqa: F401
from .monitor_cells import EffectBusView, PANEL_LABELS  # noqa: F401

__all__ = ["DispatchPanel", "EffectBusView", "PANEL_LABELS"]
