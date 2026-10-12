# -*- coding: utf-8 -*-
"""网络页「输出绑定表 + 黑洞检测」区（深查③ N4/N5）。

挂在网络配置页状态卡之后的独立 PanelSection（不往 `network_config.
_build_config_tab` 里加逻辑，只加一行 `bind_output_audit_section`）。

N5 每域绑定表：工程当前是全子网广播（真工程 `output_ip=255.255.255.255`），
本表把"实际发到哪"摊开——配置 IP + 逐网卡定向广播（`nic_broadcast.
directed_broadcast_targets` 现成，OLA `bcast_address` 口径）——让"960 台 LED
与气爆分区走不同目标"这类改造有可视化落点。

N4 黑洞检测：逐目标对本机活动网段对账，抓跨网段直发（双 NAT / 发错网卡）与
单播无人响应两类现场高频故障；全子网广播不判黑洞（无法对账，不给假告警）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHBoxLayout, QVBoxLayout, QWidget,
)

from ..widget_kit import PanelSection
from ..qascade_theme import (
    BG_DEEP, BORDER_NORMAL, ERROR_RED as BRIGHT_ERROR,
    STATUS_SUCCESS_INK as BRIGHT_SUCCESS, STATUS_WARNING_INK as BRIGHT_WARN,
    TEXT_PRIMARY, TEXT_SECONDARY,
)
from ...network.net_stats import binding_table_rows, blackhole_report

#: 表头（域 / 模式 / 实际目标 / 对账）
HEADERS = ("域", "模式", "发送目标", "网段提示（非接收确认）")


def _target_text(targets) -> str:
    return ", ".join(str(t) for t in (targets or ())) or "—"


def _ok_text(local_ok: bool, note: str) -> str:
    return f"地址提示（{note}）" if local_ok else f"路由待确认（{note}）"


def fill_binding_table(table: QTableWidget, rows) -> int:
    """把 ``binding_table_rows`` 的行铺进表格（返回行数；可离线断言数值）。"""
    table.setRowCount(0)
    for row in rows or ():
        r = table.rowCount()
        table.insertRow(r)
        cells = (
            str(row.get("universe", "")),
            str(row.get("mode", "")),
            _target_text(row.get("targets")),
            _ok_text(bool(row.get("local_ok", True)),
                     str(row.get("note", ""))),
        )
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if c == 3:
                color = (BRIGHT_SUCCESS if row.get("local_ok", True)
                         else BRIGHT_WARN)
                item.setForeground(_brush(color))
            item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
            table.setItem(r, c, item)
    return table.rowCount()


def _brush(color: str):
    from PyQt6.QtGui import QBrush, QColor
    return QBrush(QColor(color))


class OutputBindingSection(PanelSection):
    """输出绑定表 + 黑洞检测（N4/N5）。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__("输出绑定表 / 黑洞检测", expanded=True)
        self._panel = panel
        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMaximumHeight(160)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{BG_DEEP};color:{TEXT_PRIMARY};"
            f"border:1px solid {BORDER_NORMAL};"
            f"gridline-color:{BORDER_NORMAL};font-size:11px;}}")
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.add_widget(self._table)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._btn = QPushButton("刷新绑定表")
        self._btn.setFixedHeight(22)
        self._btn.setToolTip(
            "运行中显示当前发送器的实际目标；未启动时显示已应用配置的目标预览。"
            "网段与发现提示不代表接收端已收到 DMX。")
        self._btn.clicked.connect(self.refresh_now)
        row.addWidget(self._btn)
        row.addStretch()
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        row.addWidget(self._status, 1)
        self.add_layout(row)
        self.refresh_now()

    # ── 数据刷新 ────────────────────────────────────────────────────────

    def _config(self):
        panel = self._panel
        nm = getattr(panel, "_nm", None)
        from .network_runtime_view import active_output_config
        actual = active_output_config(nm)
        if actual is not None:
            return actual
        cfg = getattr(nm, "_config", None)
        if cfg is not None:
            return cfg
        # 无 NM: 从界面控件现读（配置尚未应用到 NM 时也能看）
        return self._config_from_widgets()

    def _config_from_widgets(self):
        try:
            return self._panel.get_config()
        except AttributeError:
            from ...core.models import NetworkConfig
            return NetworkConfig()

    def _universes(self) -> list:
        cfg = self._config()
        unis = [int(u) for u in (getattr(cfg, "output_universes", []) or [])]
        return unis or [int(getattr(cfg, "output_universe", 1) or 1)]

    def refresh_now(self) -> int:
        """重算绑定表 + 黑洞检测并刷新界面（返回域行数）。"""
        cfg = self._config()
        rows = binding_table_rows(cfg, universes=self._universes())
        if "sacn" in str(cfg.protocol).lower() and cfg.output_mode != "unicast":
            for row in rows:
                row["mode"] = "多播"
        count = fill_binding_table(self._table, rows)
        report = blackhole_report(cfg, discovered=self._discovered())
        color = {"error": BRIGHT_ERROR, "warn": BRIGHT_WARN,
                 "info": BRIGHT_SUCCESS}.get(report.get("level"), TEXT_SECONDARY)
        actual = hasattr(cfg, "_runtime_endpoints")
        prefix = "当前发送器目标 · " if actual else "已应用配置预览（未启动）· "
        self._status.setText(prefix + str(report.get("reason", "")))
        self._status.setStyleSheet(
            f"color:{color};font-size:11px;background:transparent;")
        return count

    def _discovered(self) -> list:
        """ArtPoll 发现表（NM 单点持有；缺失给空表 = 不误判）。"""
        nm = getattr(self._panel, "_nm", None)
        disc = getattr(nm, "_artpoll_discovery", None)
        if disc is None:
            return []
        try:
            return list(disc.devices() or [])
        except Exception:
            return []


def bind_output_audit_section(panel) -> OutputBindingSection:
    """构建并挂到网络配置页（调用方一行；失败由调用方兜住不阻断页面）。"""
    return OutputBindingSection(panel)


__all__ = ["OutputBindingSection", "bind_output_audit_section",
           "fill_binding_table", "HEADERS"]
