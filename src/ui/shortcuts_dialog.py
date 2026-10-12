# -*- coding: utf-8 -*-
"""键盘快捷键参考对话框

帮助 > 键盘快捷键 (F1)：显示所有可用快捷键，分类组织，支持搜索过滤。
商业软件标准功能，提升新用户学习效率与专业形象。
"""
from __future__ import annotations

from typing import List, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QPushButton, QWidget,
)

from . import qascade_theme as _th
from .widget_kit import apply_dialog_chrome, set_button_roles


# 快捷键数据：(类别, 功能, 快捷键)——R178：与 install_shortcuts + 基线 _build_menu 全表对齐
SHORTCUTS_DATA: List[Tuple[str, str, str]] = [
    # 文件操作（基线菜单）
    ("文件", "新建项目", "Ctrl+N"),
    ("文件", "打开项目", "Ctrl+O"),
    ("文件", "保存", "Ctrl+S"),
    ("文件", "另存为", "Ctrl+Shift+S"),
    ("文件", "退出", "Ctrl+Q"),

    # 编辑操作（基线菜单 + R176 模块）
    ("编辑", "撤销", "Ctrl+Z"),
    ("编辑", "重做", "Ctrl+Y"),
    ("编辑", "选择全部", "Ctrl+A"),
    ("编辑", "删除选中", "Delete"),
    ("编辑", "复制选中 Clip", "Ctrl+D"),
    ("编辑", "全局查找", "Ctrl+F"),

    # 视图操作（基线菜单）
    ("视图", "放大时间线", "="),
    ("视图", "缩小时间线", "-"),
    ("视图", "适配全部", "F"),
    ("视图", "编程视图", "Ctrl+Shift+1"),
    ("视图", "监控视图", "Ctrl+Shift+2"),
    ("视图", "全功能视图", "Ctrl+Shift+3"),
    ("视图", "最小视图", "Ctrl+Shift+0"),

    # 面板导航（R176 模块）
    ("面板", "跳转 Programmer", "Ctrl+1"),
    ("面板", "跳转 TopView", "Ctrl+2"),
    ("面板", "跳转 效果预设库", "Ctrl+3"),
    ("面板", "跳转 调度台", "Ctrl+4"),
    ("面板", "跳转 设备组", "Ctrl+5"),
    ("面板", "跳转 Patch", "Ctrl+6"),
    ("面板", "跳转 矩阵幻灯片", "Ctrl+7"),
    ("面板", "跳转 Clip 附加处理", "Ctrl+8"),
    ("面板", "跳转 网络配置", "Ctrl+9"),
    ("面板", "循环切换面板", "Ctrl+Tab"),
    ("面板", "关闭当前面板", "Ctrl+W"),

    # 传输控制（基线菜单 + R176 模块）
    ("传输", "播放/暂停", "Space"),
    ("传输", "停止", "Esc"),
    ("传输", "回到开头", "Home"),
    ("传输", "录制开关", "R"),
    ("传输", "分割 Clip", "S"),

    # 时间线（Clip 视图既有）
    ("时间线", "复制/剪切/粘贴 Clip", "Ctrl+C / Ctrl+X / Ctrl+V"),
    ("时间线", "放置模式", "V"),

    # 帮助
    ("帮助", "键盘快捷键", "F1"),
]


class ShortcutsDialog(QDialog):
    """键盘快捷键参考对话框：分类显示 + 搜索过滤。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("键盘快捷键")
        self.setMinimumSize(640, 520)
        self._build_ui()
        apply_dialog_chrome(self)

    def _build_ui(self):
        """构建对话框界面。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 标题行
        title_row = self._create_title_row()
        root.addWidget(title_row)

        # 搜索框
        search_row = self._create_search_row()
        root.addWidget(search_row)

        # 快捷键树形视图
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["功能", "快捷键"])
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {_th.BG_INPUT};
                border: 1px solid {_th.BORDER_NORMAL};
                border-radius: 4px;
                color: {_th.TEXT_PRIMARY};
                font-size: 12px;
            }}
            QTreeWidget::item {{
                padding: 4px 8px;
                min-height: 24px;
            }}
            QTreeWidget::item:selected {{
                background-color: {_th.SELECT_BLUE};
                color: {_th.TEXT_SELECTED_FG};
            }}
            QTreeWidget::branch {{
                background-color: transparent;
            }}
            QHeaderView::section {{
                background-color: {_th.BG_EDITOR};
                color: {_th.TEXT_PRIMARY};
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {_th.BORDER_NORMAL};
                font-weight: bold;
            }}
        """)
        self._tree.setColumnWidth(0, 400)
        root.addWidget(self._tree, 1)

        # 提示文本
        hint = QLabel("提示：使用搜索框快速查找快捷键")
        hint.setProperty("hint", True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {_th.TEXT_SECONDARY}; font-size: 11px;")
        root.addWidget(hint)

        # 关闭按钮
        btn_row = QWidget()
        btn_h = QHBoxLayout(btn_row)
        btn_h.setContentsMargins(0, 0, 0, 0)
        btn_h.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        set_button_roles(self, primary=close_btn)
        btn_h.addWidget(close_btn)
        root.addWidget(btn_row)

        # 填充数据
        self._populate_tree()

    def _create_title_row(self) -> QWidget:
        """创建标题行。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        # 标题
        title = QLabel("键盘快捷键")
        title.setStyleSheet(
            f"color: {_th.TEXT_PRIMARY}; font-size: 16px; "
            f"font-weight: bold; background: transparent;"
        )
        h.addWidget(title)
        h.addStretch(1)

        row.setFixedHeight(28)
        return row

    def _create_search_row(self) -> QWidget:
        """创建搜索框行。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        label = QLabel("搜索:")
        label.setStyleSheet(f"color: {_th.TEXT_PRIMARY}; background: transparent;")
        h.addWidget(label)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("输入功能名称或快捷键...")
        self._search_edit.textChanged.connect(self._on_search_changed)
        self._search_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_th.BG_INPUT};
                border: 1px solid {_th.BORDER_NORMAL};
                border-radius: 4px;
                padding: 6px 8px;
                color: {_th.TEXT_PRIMARY};
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {_th.FOCUS_BLUE};
            }}
        """)
        h.addWidget(self._search_edit, 1)

        return row

    def _populate_tree(self):
        """填充快捷键树形视图。"""
        self._tree.clear()

        # 按类别分组
        categories = {}
        for category, action, shortcut in SHORTCUTS_DATA:
            if category not in categories:
                categories[category] = []
            categories[category].append((action, shortcut))

        # 创建树形结构
        for category in ["文件", "编辑", "视图", "传输", "帮助"]:
            if category not in categories:
                continue

            cat_item = QTreeWidgetItem(self._tree, [category, ""])
            cat_item.setExpanded(True)
            cat_item.setFont(0, self._tree.font())
            cat_item.font(0).setBold(True)
            cat_item.setForeground(0, QColor(_th.TEXT_PRIMARY))

            for action, shortcut in categories[category]:
                child = QTreeWidgetItem(cat_item, [action, shortcut])
                child.setForeground(0, QColor(_th.TEXT_PRIMARY))
                child.setForeground(1, QColor(_th.TEXT_SECONDARY))

    def _on_search_changed(self, text: str):
        """搜索框内容变化时过滤显示。"""
        query = text.lower().strip()

        if not query:
            # 清空搜索，恢复分类显示
            self._populate_tree()
            return

        # 搜索匹配
        self._tree.clear()
        matched = []

        for category, action, shortcut in SHORTCUTS_DATA:
            if (query in action.lower() or
                query in shortcut.lower() or
                query in category.lower()):
                matched.append((category, action, shortcut))

        if not matched:
            # 无结果提示
            item = QTreeWidgetItem(self._tree, ["未找到匹配的快捷键", ""])
            item.setForeground(0, QColor(_th.TEXT_DISABLED))
            return

        # 显示搜索结果（扁平列表，不分类）
        for category, action, shortcut in matched:
            item = QTreeWidgetItem(
                self._tree, [f"{action} ({category})", shortcut]
            )
            item.setForeground(0, QColor(_th.TEXT_PRIMARY))
            item.setForeground(1, QColor(_th.TEXT_SECONDARY))


def show_shortcuts_dialog(parent=None) -> None:
    """显示快捷键参考对话框（便捷函数）。"""
    dlg = ShortcutsDialog(parent)
    dlg.exec()
