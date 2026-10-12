# -*- coding: utf-8 -*-
"""NetworkConfigPanel 的共享构建段：状态行、图标行、占位 Tab。

R181 排版重做：
  - ``StatusLine``：tabler 状态图标 + 彩色文字（替代旧的状态色点文本前缀）；
  - ``icon_row``：tabler 图标 + 文字的行控件（诊断页状态/端口/适配器行共用）；
  - Tab 标题去掉 emoji，改用 ``load_tabler_icon`` 矢量图标。

本模块不 import network_config（避免循环）；状态色一律走 qascade_theme 令牌。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QWidget, QVBoxLayout, QHBoxLayout

from ..widget_kit import EmptyState
from ..qascade_theme import STATUS_TEXT, TEXT_SECONDARY
from ..main_icon_library import load_tabler_icon, load_tabler_pixmap

#: 状态四态 -> tabler 图标名（与 STATUS_TEXT 同键）
STATUS_ICON = {
    "success": "circle-check",
    "warn": "alert-triangle",
    "error": "circle-x",
    "info": "info-circle",
    "dim": "circle-dot",
}

#: 诊断页复用的对/错图标名
ICON_CHECK = "circle-check"
ICON_CROSS = "circle-x"

#: Tab 图标名（网络配置 / 网络诊断 / 信号测试）
TAB_ICON_CONFIG = "settings"
TAB_ICON_DIAGNOSTIC = "activity"
TAB_ICON_TESTER = "wave-sine"


class StatusLine(QWidget):
    """状态行：tabler 状态图标 + 彩色文字（紧凑行版式，图标 14px 垂直居中）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._icon = QLabel()
        self._icon.setFixedSize(14, 14)
        self._icon.setStyleSheet("background: transparent;")
        lay.addWidget(self._icon)
        self._text = QLabel()
        self._text.setWordWrap(True)
        lay.addWidget(self._text, 1)
        self.set_state("dim", "未连接")

    def set_state(self, kind: str, text: str) -> None:
        """按四态（success/warn/error/info/dim）刷新图标与文字色。

        R182 P1-8：info/warn/error/success 一律**强制可见**——修前停机态把
        状态行 ``setVisible(False)``，而 fill / 预设 / 防火墙提示只调
        ``set_state`` -> 内容写进去了却看不见（用户实报"点了没反应"）。
        dim（空态）由调用方决定是否让位给空态引导。
        """
        if kind != "dim":
            self.setVisible(True)
        color = STATUS_TEXT.get(kind, STATUS_TEXT["dim"])
        self._icon.setPixmap(
            load_tabler_pixmap(STATUS_ICON.get(kind, "circle-dot"), 14, color))
        self._text.setText(text)
        self._text.setStyleSheet(
            f"color:{color};font-size:12px;background:transparent;")


def icon_row(icon_name: str, text: str, color: str, size: int = 14, *,
             indent: int = 0, font_size: int = 11, bold: bool = False) -> QWidget:
    """tabler 图标 + 文字的一行（诊断页状态行/端口行/适配器行共用）。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(indent, 0, 0, 0)
    lay.setSpacing(6)
    ic = QLabel()
    ic.setFixedSize(size, size)
    ic.setPixmap(load_tabler_pixmap(icon_name, size, color))
    ic.setStyleSheet("background: transparent;")
    lay.addWidget(ic)
    tx = QLabel(text)
    tx.setWordWrap(True)
    tx.setStyleSheet(f"color:{color};font-size:{font_size}px;background:transparent;"
                     + ("font-weight:bold;" if bold else ""))
    lay.addWidget(tx, 1)
    return w


def build_status_container() -> tuple:
    """状态区：状态行 + 空态引导容器（网络未运行时空态可见、状态行隐藏）。

    返回 (container, status_line, empty_state)；状态行由调用方 ``set_state``。
    """
    container = QWidget()
    lay = QVBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    status_line = StatusLine()
    lay.addWidget(status_line)

    empty_state = EmptyState(
        "网络未启动",
        "配置协议和 IP 后，点击「应用配置」和「启动网络」开始输出 DMX 信号",
        "network")
    empty_state.setVisible(False)
    lay.addWidget(empty_state)

    return container, status_line, empty_state


def add_diagnostic_tab(tabs) -> None:
    """Tab 2：网络诊断占位页（切换到此页懒加载 NetworkDiagnosticWidget）。"""
    placeholder = QWidget()
    ph_lay = QVBoxLayout(placeholder)
    ph_lay.setContentsMargins(12, 12, 12, 12)
    ph_lay.addWidget(EmptyState(
        "网络诊断待加载",
        "切换到此标签页即加载网络诊断工具（防火墙 / 端口 / 适配器状态检查）",
        "activity"))
    tabs.addTab(placeholder, load_tabler_icon(TAB_ICON_DIAGNOSTIC, 14, TEXT_SECONDARY),
                "网络诊断")


def add_signal_tester_tab(tabs) -> QWidget:
    """Tab 3：信号测试占位页（返回 placeholder 供懒加载替换）。"""
    placeholder = QWidget()
    ph_lay = QVBoxLayout(placeholder)
    ph_lay.setContentsMargins(12, 12, 12, 12)
    ph_lay.addWidget(EmptyState(
        "信号测试器待加载",
        "切换到此标签页即加载信号测试工具（512 通道热图 / 多模式测试信号发生器）",
        "test-pipe"))
    tabs.addTab(placeholder, load_tabler_icon(TAB_ICON_TESTER, 14, TEXT_SECONDARY),
                "信号测试")
    return placeholder
