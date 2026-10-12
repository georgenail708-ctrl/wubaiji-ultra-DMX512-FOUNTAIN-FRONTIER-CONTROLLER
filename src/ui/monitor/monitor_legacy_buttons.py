# -*- coding: utf-8 -*-
"""MonitorPanel 隐藏保活控件装配（R183 D 从 monitor_panel.py 拆出，压文件行数）。

R53/R55 用户指令：应用/清除过滤彩底钮、输入/输出过滤钮、用于轨道范围钮
从界面移除（逻辑保留保活）。本模块集中装配这些"界面不显示但功能保活"的
按钮，`MonitorPanel._setup_ui` 只做一次赋值接线。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import Qt

from ..qascade_theme import STATUS_TEXT

try:
    from ..main_icon_library import make_main_icon as _make_icon
except ImportError:
    _make_icon = None


def build_hidden_buttons(panel):
    """装配并返回 5 个保活按钮：input/output/apply/clear/to_track。

    调用方保留 ``panel`` 属性入口，Qt parent 由原面板持有；除 input/output 的
    checkable 语义外全部保持隐藏，不加入布局。
    """
    input_btn = QPushButton(" 输入过滤", panel)
    if _make_icon:
        input_btn.setIcon(_make_icon("project_open", 14))
    output_btn = QPushButton(" 输出过滤", panel)
    if _make_icon:
        output_btn.setIcon(_make_icon("export", 14))
    for btn in (input_btn, output_btn):
        btn.setCheckable(True)
        btn.setFixedHeight(24)
        btn.setToolTip("切换当前配置的过滤方向（输入/输出）")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
    input_btn.setChecked(True)
    input_btn.clicked.connect(lambda: panel._set_filter_mode("input"))
    output_btn.clicked.connect(lambda: panel._set_filter_mode("output"))
    input_btn.hide()
    output_btn.hide()

    apply_btn = QPushButton("应用过滤", panel)
    apply_btn.setFixedHeight(24)
    apply_btn.setToolTip("把当前框选/小键盘得到的通道集合写入选定方向的过滤规则")
    apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    apply_btn.setStyleSheet(f"""
        QPushButton {{ background: #545454;
            color: {STATUS_TEXT['success']};
            border: 1px solid #3D3D3D; border-radius: 4px;
            padding: 0 12px; font-weight: bold; }}
        QPushButton:hover {{ background: #656565; }}
        QPushButton:pressed {{ background: #4772B3; color: #FFFFFF; }}
    """)
    apply_btn.clicked.connect(panel._apply_filter)
    apply_btn.setVisible(False)

    clear_btn = QPushButton(" 清除过滤", panel)
    clear_btn.setFixedHeight(24)
    if _make_icon:
        clear_btn.setIcon(_make_icon("clear_output", 14))
    clear_btn.setToolTip("清空选定方向的过滤规则（恢复全通）")
    clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    clear_btn.setStyleSheet(f"""
        QPushButton {{ background: #545454;
            color: {STATUS_TEXT['error']};
            border: 1px solid #3D3D3D; border-radius: 4px;
            padding: 0 12px; }}
        QPushButton:hover {{ background: #656565; border-color: #5C5C5C; }}
        QPushButton:pressed {{ background: #4772B3; color: #FFFFFF; }}
    """)
    clear_btn.clicked.connect(panel._clear_filter)
    clear_btn.setVisible(False)

    to_track_btn = QPushButton("→ 用于轨道范围", panel)
    to_track_btn.setFixedHeight(24)
    to_track_btn.setToolTip("把当前选中的通道集合发送为轨道录制范围")
    to_track_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    to_track_btn.setStyleSheet(f"""
        QPushButton {{ background: #545454;
            color: #E6B455;
            border: 1px solid #3D3D3D; border-radius: 4px;
            padding: 0 12px; }}
        QPushButton:hover {{ background: #656565; border-color: #5C5C5C; }}
        QPushButton:pressed {{ background: #4772B3; color: #FFFFFF; }}
    """)
    to_track_btn.clicked.connect(panel._send_to_track)
    # 该钮不加入顶栏布局，保留隐藏兼容入口与原面板的 Qt owner。
    # 无需脱 parent：没有布局项就不会占位，关闭原面板时应自动删除。
    to_track_btn.hide()

    return input_btn, output_btn, apply_btn, clear_btn, to_track_btn
