# -*- coding: utf-8 -*-
"""Draw 大页左栏「用户预设库」区（R183-H 拆分）—— PresetRow / PresetSection。

原在 `draw_palette.py`（该文件 500 行门禁，R183-H 美术接线后超限）；语义与信号面
**逐位不变**，`draw_palette` 原位 re-export 保既有导入路径零改动：

* `PresetRow`：单条预设（点击加载；右键载入/重命名/删除）。
* `PresetSection`：命名输入 + 保存/导入/导出 + 预设行列表（空态带 B0 小插画）。

只发信号、不碰存储（存储/对话框由面板接线）——纯 UI 可离屏测试。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QToolButton, QVBoxLayout, QWidget)

from ..main_icon_library import load_tabler_icon
from ..qascade_theme import BG_PANEL, TEXT_DISABLED, TEXT_PRIMARY
from .draw_palette_art import PRESET_EMPTY_ART, load_art_pixmap_20

__all__ = ["PresetRow", "PresetSection"]


def _bookmark_pixmap():
    """预设行 bookmark 图标（16px；失败 → None 不阻塞布局，照 draw_palette 旧路径）。"""
    try:
        pm = load_tabler_icon("bookmark", 16, TEXT_PRIMARY).pixmap(16, 16)
    except Exception:                                # pragma: no cover
        return None
    return pm


class PresetRow(QFrame):
    """用户预设条目（M4）：点击 = 加载；右键 = 载入/重命名/删除（成熟化轮①）。"""

    load_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)   # 旧名, 新名
    delete_requested = pyqtSignal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = str(name)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"点击加载预设「{self._name}」到画布")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 1, 6, 1)
        lay.setSpacing(6)
        icon_label = QLabel()
        pm = _bookmark_pixmap()
        if pm is not None and not pm.isNull():
            icon_label.setPixmap(pm)
        icon_label.setFixedSize(16, 16)
        icon_label.setStyleSheet("background: transparent;")
        lay.addWidget(icon_label)
        lab = QLabel(self._name)
        lab.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px;"
                          "background: transparent; border: none;")
        lay.addWidget(lab, 1)
        self.setStyleSheet(
            "PresetRow { background: transparent; border: none; border-radius: 4px; }"
            f"PresetRow:hover {{ background: {BG_PANEL}; }}")

    def preset_name(self) -> str:
        return self._name

    def matches(self, query: str) -> bool:
        q = (query or "").strip().lower()
        return (not q) or q in self._name.lower()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.load_requested.emit(self._name)
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev):  # noqa: N802
        """右键管理菜单（照调度台卡片右键同构；成熟化轮①）。"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("载入", lambda: self.load_requested.emit(self._name))
        menu.addAction("重命名…",
                       lambda: self.rename_requested.emit(
                           self._name, self._name))
        menu.addAction("删除", lambda: self.delete_requested.emit(self._name))
        menu.exec(ev.globalPos())


def _preset_tool_btn(icon: str, tip: str) -> QToolButton:
    """预设库小图标按钮（暗色令牌；图缺失不阻塞）。"""
    btn = QToolButton()
    try:
        btn.setIcon(load_tabler_icon(icon, 16))
    except Exception:
        pass
    btn.setToolTip(tip)
    btn.setAutoRaise(True)
    btn.setFixedSize(24, 22)
    btn.setStyleSheet("QToolButton { background: transparent; border: none; }")
    return btn


class PresetSection(QWidget):
    """用户预设库区（M4）：命名输入 + 保存/导入/导出 + 预设行列表。

    只发信号，不碰存储（存储/对话框由面板接线）——纯 UI 可离屏测试。
    R183-H：空态行带 B0 小插画（20px muted 灰，Open Doodles `empty_coffee`）；
    资产缺失 → 只有文字（永不抛）。
    """

    save_requested = pyqtSignal(str)     # 名称（非空已 trim）
    load_requested = pyqtSignal(str)     # 预设名
    import_requested = pyqtSignal()
    export_requested = pyqtSignal()
    # 成熟化轮①：重命名(旧,新)/删除——对话框由面板负责，这里只转发
    rename_requested = pyqtSignal(str, str)
    delete_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(4)
        self._name = QLineEdit()
        self._name.setPlaceholderText("预设名…")
        top.addWidget(self._name, 1)
        save = _preset_tool_btn("device-floppy", "保存当前画布为预设")
        save.clicked.connect(self._emit_save)
        top.addWidget(save)
        imp = _preset_tool_btn("file-import", "从 JSON 导入预设")
        imp.clicked.connect(self.import_requested.emit)
        top.addWidget(imp)
        exp = _preset_tool_btn("file-export", "导出预设到 JSON")
        exp.clicked.connect(self.export_requested.emit)
        top.addWidget(exp)
        v.addLayout(top)
        self._empty_row = QWidget()
        er = QHBoxLayout(self._empty_row)
        er.setContentsMargins(2, 2, 2, 2)
        er.setSpacing(6)
        empty_pm = load_art_pixmap_20(PRESET_EMPTY_ART)
        if empty_pm is not None:
            art = QLabel()
            art.setPixmap(empty_pm)
            art.setFixedSize(20, 20)
            art.setStyleSheet("background: transparent;")
            er.addWidget(art)
        self._empty = QLabel("暂无预设——命名后点保存")
        self._empty.setStyleSheet(f"color: {TEXT_DISABLED}; font-size: 10px;"
                                  "background: transparent;")
        er.addWidget(self._empty, 1)
        v.addWidget(self._empty_row)
        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(1)
        v.addLayout(self._list)
        self.set_presets([])

    def _emit_save(self) -> None:
        name = self._name.text().strip()
        if name:
            self.save_requested.emit(name)

    def set_presets(self, names) -> None:
        """重建预设行列表（面板保存/导入后调用；空表出空态提示）。"""
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for name in (names or []):
            nm = str(name)
            if not nm:
                continue
            row = PresetRow(nm)
            row.load_requested.connect(self.load_requested.emit)
            row.rename_requested.connect(self.rename_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            self._list.addWidget(row)
            self._rows.append(row)
        self._empty_row.setVisible(not self._rows)

    def preset_names(self) -> list:
        return [r.preset_name() for r in self._rows]

    def apply_filter(self, query: str) -> int:
        """按名过滤预设行；返回命中数（分区显隐由外层决定）。"""
        hits = 0
        for row in self._rows:
            show = row.matches(query)
            row.setVisible(show)
            hits += 1 if show else 0
        return hits
