"""ML 实验室（实验室分区）——外部 ML 自动编曲模块的宿主桥接层。

模块本体在 exe 旁的绿色目录 `ml_stage_lab/`（独立项目/独立 venv/独立 HTTP 子进程），
与主系统 src/ 零耦合。本包只做三件事：
  1. 探测模块是否存在（存在即启用、缺失即回退——同 qascade_ce 范式）
  2. 把 TopView 真实布局 → 逐帧真实 DMX 通道值 → 写主系统 `ml_lab` 混音层
  3. 独立窗口（含 LLM 提供方配置与测试按钮）

对外入口：LabModule（探测+服务）、LabWindow（界面）。
"""
from __future__ import annotations

__all__ = ["LabModule", "LabWindow"]


def __getattr__(name):
    # 延迟导入：避免 QWebEngine 未就绪时（QApplication 之前）拖入重依赖
    if name == "LabModule":
        from .module_service import LabModule
        return LabModule
    if name == "LabWindow":
        from .lab_window import LabWindow
        return LabWindow
    raise AttributeError(name)
