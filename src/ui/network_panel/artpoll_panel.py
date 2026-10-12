# -*- coding: utf-8 -*-
"""R181 — 网络页「设备发现 ArtPoll」面板。

后端 ``src/network/artpoll_discovery.py`` 此前只有发现表、无 UI 消费者，
``poll_network()`` 无调用方。本面板补齐三件事：

  - 「扫描网络」按钮调用 ``ArtPollDiscovery.poll_network()``：广播 ArtPoll 的
    收发全部在该方法内部的后台线程执行，UI 线程零阻塞（R178 netsh 卡死教训）；
  - 发现节点表：IP / 短名 / 端口 / 最后响应（1s 刷新一次响应年龄）；
  - 行选中（或双击）后点「填入目标地址」，经 ``fill_target_requested(ip)``
    信号交由 NetworkConfigPanel 写进输出目标 IP 输入框。

发现表来源：优先复用 NetworkManager 运行期挂接的 ``_artpoll_discovery``
（被动 ArtPoll + 主动扫描同表）；网络未启动时本面板自建一个实例。

裸 socket 纪律：本模块不创建任何 socket，全部委托后端 ArtPollDiscovery。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...network.artpoll_discovery import ArtPollDiscovery
from ..qascade_theme import (
    BG_INPUT, BORDER_NORMAL, BRIGHT_INFO, BRIGHT_SUCCESS, RADIUS, SEP_LINE,
    TEXT_DISABLED, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_SELECTED_FG,
)
from ..main_icon_library import load_tabler_icon
from .network_button_factory import make_button

log = logging.getLogger(__name__)

#: 主动扫描的收集窗口（秒）；poll_network 在该窗口内后台收 ArtPollReply。
SCAN_TIMEOUT_SEC = 1.5

#: 发现表列（顺序即列序）
TABLE_HEADERS = ("IP 地址", "短名", "端口", "最后响应")

_TABLE_QSS = f"""
QTableWidget {{ background: {BG_INPUT}; border: 1px solid {SEP_LINE};
    border-radius: {RADIUS}px; outline: none; }}
QTableWidget::item {{ padding: 0 6px; border: none; }}
QTableWidget::item:selected {{ background: rgba(71,114,179,150); color: {TEXT_SELECTED_FG}; }}
QHeaderView::section {{ background: {BORDER_NORMAL}; color: {TEXT_SECONDARY};
    border: none; border-right: 1px solid rgba(255,255,255,15);
    padding: 3px 8px; font-weight: normal; }}
"""


def format_age(seen_sec: float) -> str:
    """响应年龄转人类可读文本（纯函数，便于测试）。"""
    s = max(0.0, float(seen_sec or 0.0))
    if s < 1.5:
        return "刚刚"
    if s < 60.0:
        return f"{int(s)} 秒前"
    return f"{int(s // 60)} 分 {int(s % 60)} 秒前"


class ArtPollDiscoveryPanel(QWidget):
    """ArtPoll 设备发现面板（扫描 / 发现表 / 填入目标地址）。"""

    fill_target_requested = pyqtSignal(str)

    def __init__(self, network_manager=None, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._own: ArtPollDiscovery | None = None
        self._wired: ArtPollDiscovery | None = None
        self._row_sig: tuple | None = None

        self._setup_ui()

        self._scan_end = QTimer(self)
        self._scan_end.setSingleShot(True)
        self._scan_end.timeout.connect(self._on_scan_end)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_table)
        self._refresh_timer.start()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)

        self._scan_btn = make_button(
            "扫描网络", "radar", "neutral",
            tooltip=(
                "向 255.255.255.255 与本机各网卡定向广播地址发 ArtPoll，\n"
                f"在 {SCAN_TIMEOUT_SEC:g}s 窗口内收集 ArtPollReply。\n"
                "收发在后台线程执行，不会卡住界面。"))
        self._scan_btn.clicked.connect(self._on_scan)
        bar.addWidget(self._scan_btn)

        self._status = QLabel(
            "未扫描。点击「扫描网络」广播 ArtPoll 发现局域网节点；\n"
            "节点 30 秒无响应会自动从表中移除。")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        bar.addWidget(self._status, 1)

        self._fill_btn = make_button(
            "填入目标地址", "arrow-up-right", "neutral",
            tooltip=(
                "把选中节点的 IP 写进「输出」的目标 IP 输入框（自动切到单播），\n"
                "核对后点「应用配置」生效。"))
        self._fill_btn.setEnabled(False)
        self._fill_btn.clicked.connect(self._on_fill)
        bar.addWidget(self._fill_btn)
        root.addLayout(bar)

        self._table = QTableWidget(0, len(TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(list(TABLE_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._table.setMinimumHeight(120)
        self._table.setMaximumHeight(200)
        self._table.setStyleSheet(_TABLE_QSS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 72)
        self._table.setColumnWidth(3, 96)
        self._table.itemSelectionChanged.connect(self._update_fill_enabled)
        self._table.itemDoubleClicked.connect(lambda _item: self._on_fill())
        root.addWidget(self._table)

    # ── 发现源管理 ────────────────────────────────────────────────────────

    def _active_discovery(self) -> ArtPollDiscovery | None:
        """优先用 NetworkManager 的发现表；未运行时用本面板自建实例。

        R182 P2：NetworkManager **单点持有**（start 时无条件建立），UI 只
        经 ``getattr(nm, '_artpoll_discovery')`` 只读消费。自建实例仅作为
        "NM 未启动/无 NM"时的降级路径（离线浏览），不再是常态第二实例。
        """
        managed = getattr(self._nm, "_artpoll_discovery", None) if self._nm else None
        if managed is not None:
            self._retire_own(managed)
            return managed
        return self._own

    def _retire_own(self, managed: ArtPollDiscovery) -> None:
        """NM 已提供发现表时收掉自建实例（避免两表并行、列表互相打脸）。"""
        own = self._own
        if own is None or own is managed:
            return
        if self._wired is own:
            self._wired = None
        try:
            own.stop()
        except Exception:
            log.debug("自建发现表回收失败", exc_info=True)
        self._own = None

    def _ensure_discovery(self) -> ArtPollDiscovery:
        """取/建可扫描的发现表（NM 托管优先，降级自建）。"""
        d = self._active_discovery()
        if d is None:
            cfg = getattr(self._nm, "_config", None)
            bind_ip = ""
            if cfg is not None:
                bind_ip = str(getattr(cfg, "input_ip", "") or "").strip()
            d = ArtPollDiscovery(self, bind_ip=bind_ip or "0.0.0.0")
            d.start()
            self._own = d
        self._wire(d)
        return d

    def _wire(self, discovery: ArtPollDiscovery) -> None:
        """把发现表的 devices_found / device_lost 接到刷新槽（换实例时重接）。"""
        if self._wired is discovery:
            return
        old = self._wired
        if old is not None:
            for sig in (old.devices_found, old.device_lost):
                try:
                    sig.disconnect(self._refresh_table)
                except TypeError:
                    pass
        discovery.devices_found.connect(self._refresh_table)
        discovery.device_lost.connect(self._refresh_table)
        self._wired = discovery

    # ── 扫描 ──────────────────────────────────────────────────────────────

    def _on_scan(self):
        """扫描按钮：调 poll_network（其后端线程收包，UI 立即返回）。

        后端 ``_polling`` 已加锁（R182 P2），连点不会起重叠扫描窗口；
        返回 False 表示已有窗口在跑。
        """
        d = self._ensure_discovery()
        if not d.poll_network(SCAN_TIMEOUT_SEC):
            self._set_status("扫描进行中，请稍候…", BRIGHT_INFO)
            return
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("扫描中…")
        self._scan_btn.setIcon(load_tabler_icon("loader-2", 14, TEXT_DISABLED))
        self._set_status(
            f"已广播 ArtPoll，正在收集应答（窗口 {SCAN_TIMEOUT_SEC:g}s）…",
            BRIGHT_INFO)
        self._scan_end.start(int(SCAN_TIMEOUT_SEC * 1000) + 400)

    def _on_scan_end(self):
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("扫描网络")
        self._scan_btn.setIcon(load_tabler_icon("radar", 14, BRIGHT_INFO))
        self._refresh_table()
        count = self._table.rowCount()
        if count:
            self._set_status(
                f"扫描结束：当前发现 {count} 个节点（30s 无响应自动移除）",
                BRIGHT_SUCCESS)
        else:
            self._set_status("扫描结束：未发现节点。确认目标设备在线且防火墙放行 UDP 6454。",
                             TEXT_SECONDARY)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color:{color};font-size:11px;background:transparent;")

    # ── 发现表刷新 ────────────────────────────────────────────────────────

    def _refresh_table(self, *_args):
        # R193/HF-01b：已装入主窗时隐藏期 1s 轮询早退（同 HF-01a 口径）
        if getattr(self, "_installed_in_window", False) and not self.isVisible():
            return
        d = self._active_discovery(); rows = d.devices() if d is not None else []
        sig = self._signature(rows)
        if sig != self._row_sig:
            self._rebuild_rows(rows)
        else:
            self._update_ages(rows)
        self._update_fill_enabled()

    @staticmethod
    def _signature(rows: list) -> tuple:
        return tuple((str(r.get("ip", "")), str(r.get("short_name", "")),
                      r.get("port", 0)) for r in rows)

    def _rebuild_rows(self, rows: list):
        selected = self._selected_ip()
        self._table.setRowCount(len(rows))
        for i, dev in enumerate(rows):
            short = str(dev.get("short_name", "") or "")
            port = dev.get("port") or 0
            self._set_cell(i, 0, str(dev.get("ip", "")), TEXT_PRIMARY)
            self._set_cell(i, 1, short or "（未命名）",
                           TEXT_SECONDARY if short else TEXT_DISABLED)
            self._set_cell(i, 2, str(port) if port else "-", TEXT_SECONDARY)
            self._set_cell(i, 3, format_age(dev.get("seen_sec", 0.0)), TEXT_SECONDARY)
            if dev.get("ip") == selected:
                self._table.selectRow(i)
        self._row_sig = self._signature(rows)

    def _update_ages(self, rows: list):
        for i, dev in enumerate(rows):
            item = self._table.item(i, 3)
            if item is not None:
                item.setText(format_age(dev.get("seen_sec", 0.0)))

    def _set_cell(self, row: int, col: int, text: str, color: str):
        item = QTableWidgetItem(text)
        item.setForeground(QColor(color))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(row, col, item)

    # ── 填入目标地址 ──────────────────────────────────────────────────────

    def _selected_ip(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return item.text().strip() if item is not None else ""

    def _update_fill_enabled(self):
        self._fill_btn.setEnabled(bool(self._selected_ip()))

    def _on_fill(self):
        ip = self._selected_ip()
        if not ip:
            return
        self.fill_target_requested.emit(ip)
        self._set_status(f"已把 {ip} 填入输出目标地址（单播）；核对后点「应用配置」。",
                         BRIGHT_SUCCESS)

    # ── 收尾 ──────────────────────────────────────────────────────────────

    def shutdown(self):
        """停止面板自建发现表（NetworkManager 托管的不动）。"""
        self._refresh_timer.stop()
        self._scan_end.stop()
        if self._own is not None:
            if self._wired is self._own:
                self._wired = None
            self._own.stop()
            self._own = None
