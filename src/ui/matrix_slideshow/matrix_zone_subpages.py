# -*- coding: utf-8 -*-
"""matrix_zone_subpages.py — Matrix 面板布局构建（R182 拆页；R183 子页改独立窗口）。

用户规格（R183）::

    主界面：顶部通用行（设备组选择 + 协议无关全局开关 + 两个开窗按钮）
            + 中央即时预览展示区（设备排布画布占主位）
            + 底部通用控件（播放控制 / 全局平滑入口 / 输出开关）
    幻灯片独立窗口：帧列表（缩略图 + 增删/上下移）+ 过渡设置 + 幻灯片参数
                    + 设备排布（帧编辑）与保存/更新帧操作
    像素效果独立窗口：PIXEL_MODES 分组下拉 + 像素参数 + 视频/屏幕源设置

设计纪律：**只搬布局，不动业务**。所有构建函数接收 MatrixSlideshowPanel
实例，把控件按**原字段名**挂回面板（`_play_btn` / `_pixel_effect_combo` /
`_thumb_lay` / `_slide_param_panel` …），业务方法（引擎/输出/调度台/切片）
一律不改、不新增。宿主面板经延迟导入取常量与组件类，避免模块级循环依赖。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from .matrix_child_window import MatrixChildWindow


def _host():
    """延迟导入宿主模块（本模块只在控件构建期读其常量与组件类）。"""
    from . import matrix_slideshow_panel as mp
    return mp


# ══════════════════════════════════════════════════════════════════════════════
#  主界面：通用行 + 中央预览 + 底部通用控件
# ══════════════════════════════════════════════════════════════════════════════

def build_main_zone(panel) -> QWidget:
    """M01编辑器装配；独立PPT/pixel窗口的构建继续留在本模块。"""
    from .matrix_workspace_ui import build_main_workspace
    return build_main_workspace(panel)


# ══ 幻灯片独立窗口：帧列表 + 过渡设置 + 幻灯片参数 + 帧操作 ══════════════════

def build_ppt_window(panel) -> QWidget:
    """M02独立帧编辑器装配，原实例/字段/操作合同由panel持有。"""
    from .matrix_ppt_workspace_ui import build_ppt_workspace
    return build_ppt_workspace(panel)


# ══ 像素效果独立窗口：模式下拉 + 参数 + 视频/屏幕源 ══════════════════════════

def build_pixel_window(panel) -> QWidget:
    """M03 independent editor; original mode and media contracts stay on panel."""
    from .matrix_pixel_workspace_ui import build_pixel_workspace
    return build_pixel_workspace(panel)
