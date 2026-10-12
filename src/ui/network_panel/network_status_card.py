# -*- coding: utf-8 -*-
"""网络页顶部状态卡（R182 前端重做：三区范式 · 顶部大卡）。

一行大卡显示四项：协议图标+名称 / 运行状态（big dot） / 绑定 IP /
输出目标摘要。数据一律经 ``NetworkManager`` 的**实际运行对象**读取
（``is_running`` / ``protocol_name`` / ``get_sender_status()``），不再
只读 ``_config``——修前"改了配置但没重启"时状态行显示的是配置而非实效
（R182 P1-4）。

本模块只做呈现与格式化（``format_status_card`` 为纯函数，可离线测试），
不做任何网络 I/O。
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..main_icon_library import load_tabler_icon, load_tabler_pixmap
from ..qascade_theme import (
    BG_BOX, BORDER_NORMAL, ERROR_RED as BRIGHT_ERROR, INFO_BLUE as BRIGHT_INFO,
    STATUS_SUCCESS_INK as BRIGHT_SUCCESS, STATUS_WARNING_INK as BRIGHT_WARN,
    RADIUS, SEP_LINE, TEXT_DISABLED, TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ...network.net_stats import SenderRateSampler, format_rate_line

#: 状态灯直径（"big dot"规格）
DOT_SIZE = 12


@dataclass(frozen=True)
class StatusCardModel:
    """状态卡数据模型（纯数据，便于测试断言格式化结果）。"""
    protocol: str
    running: bool
    bind_ip: str
    target_summary: str
    bind_error: str | None = None
    packets: int = 0
    packet_errors: int = 0
    rate_line: str = ""        # N1+N10：实际发送 Hz + 跳过/错误（节流重算）


def format_status_card(m: StatusCardModel) -> tuple[str, str, str]:
    """模型 -> (状态文本, 状态语义色 role, 详情行文本)。

    四态：error（绑定失败）> success（运行中）> warn（运行但无发送器）>
    dim（未连接）。
    """
    if m.bind_error:
        return ("启动失败", "error", m.bind_error)
    if not m.running:
        return ("未启动", "dim", "配置后点「启动网络」开始输出 DMX 信号")
    detail = f"输入监听 {m.bind_ip or '未启用'} · {m.target_summary}"
    if m.rate_line:
        detail += f" · {m.rate_line}"       # N1+N10：速率行优先于累计包数
    elif m.packets:
        detail += f" · 已发 {m.packets:,} 包"
    if m.packet_errors:
        detail += f" · 发送错误 {m.packet_errors:,}"
    return ("运行中", "success", detail)


def status_color(role: str) -> str:
    """role -> 色令牌（与 STATUS_TEXT 同键语义）。"""
    return {
        "success": BRIGHT_SUCCESS,
        "warn": BRIGHT_WARN,
        "error": BRIGHT_ERROR,
        "info": BRIGHT_INFO,
        "dim": TEXT_SECONDARY,
    }.get(role, TEXT_SECONDARY)


def protocol_icon_name(protocol: str) -> str:
    """协议 -> tabler 图标名（Art-Net=sitemap 拓扑 / sACN=broadcast 广播）。"""
    return "sitemap" if "art" in str(protocol or "").lower() else "broadcast"


class NetworkStatusCard(QWidget):
    """顶部状态卡：协议 + 运行状态灯 + 绑定 IP + 输出目标摘要 + 发送速率。

    N1+N10：速率行由 :class:`SenderRateSampler` 喂入（节流 ≥250ms），
    只读既有发送器累计计数器——数据早就在数（`network_manager.
    _record_perf_stats`），此处只做表面化。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rate = SenderRateSampler()
        self.setObjectName("networkStatusCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#networkStatusCard {{ background:{BG_BOX};"
            f" border:1px solid {BORDER_NORMAL}; border-radius:{RADIUS}px; }}")

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(12)

        self._proto_icon = QLabel()
        self._proto_icon.setFixedSize(28, 28)
        self._proto_icon.setStyleSheet("background:transparent;")
        root.addWidget(self._proto_icon)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._dot = QLabel()
        self._dot.setFixedSize(DOT_SIZE, DOT_SIZE)
        self._dot.setStyleSheet("background:transparent;")
        head.addWidget(self._dot)
        self._proto_label = QLabel("—")
        self._proto_label.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:13px;font-weight:bold;"
            "background:transparent;")
        head.addWidget(self._proto_label)
        self._state_label = QLabel("未启动")
        self._state_label.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:12px;background:transparent;")
        head.addWidget(self._state_label)
        head.addStretch()
        text_col.addLayout(head)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        self._detail_label.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        text_col.addWidget(self._detail_label)
        root.addLayout(text_col, 1)

    # ── 刷新 ──────────────────────────────────────────────────────────────

    def update_from_manager(self, nm) -> None:
        """从 NetworkManager 实际状态刷新（None 时显示未连接空态）。

        N1+N10：每次刷新顺带喂速率采样器（内部 ≥250ms 节流重算）——
        状态卡本就被 `NetworkConfigPanel._timer` 按 500ms 轮询调用，
        速率行随之自动变"活"，无需新增定时器。
        """
        if nm is None:
            self._rate.reset()
            self._apply(StatusCardModel(
                protocol="—", running=False, bind_ip="", target_summary="",
                bind_error="网络管理器未初始化"))
            return
        status = {}
        try:
            status = nm.get_sender_status() or {}
        except Exception:
            status = {}
        proto = str(getattr(nm, "protocol_name", "") or status.get("protocol") or "—")
        running = bool(getattr(nm, "is_running", False))
        bind_error = getattr(nm, "last_bind_error", None)
        cfg = getattr(nm, "_config", None)
        from .network_runtime_view import active_output_config, output_summary
        actual = active_output_config(nm)
        bind_ip = str(getattr(actual, "_runtime_input", "未启用"))
        target = output_summary(actual) or self._target_summary(status, cfg, proto)
        self._apply(StatusCardModel(
            protocol=proto, running=running, bind_ip=bind_ip,
            target_summary=target, bind_error=bind_error,
            packets=int(status.get("packets", 0) or 0),
            packet_errors=int(status.get("errors", 0) or 0),
            rate_line=self._rate_line(nm, status, running),
        ))

    def _rate_line(self, nm, status: dict, running: bool) -> str:
        """N1+N10：实际发送 Hz + 跳过计数（停机会话显示空，不误导）。"""
        if not running:
            self._rate.reset()
            return ""
        try:
            hz = self._rate.sample(int(status.get("packets", 0) or 0),
                                   int(status.get("errors", 0) or 0))
        except Exception:
            hz = None
        if hz is None:
            return ""
        return format_rate_line(hz, int(status.get("sacn_senders", 0) or 0),
                                self._skipped(nm),
                                int(status.get("errors", 0) or 0))

    @staticmethod
    def _skipped(nm) -> int:
        """变化域跳过累计（`get_send_skip_stats` 缺失给 0）。"""
        try:
            getter = getattr(nm, "get_send_skip_stats", None)
            return int((getter() or {}).get("skipped", 0) or 0) if callable(getter) else 0
        except Exception:
            return 0

    def _apply(self, model: StatusCardModel) -> None:
        state_text, role, detail = format_status_card(model)
        color = status_color(role)
        try:
            self._proto_icon.setPixmap(load_tabler_pixmap(
                protocol_icon_name(model.protocol), 28, color))
            # tabler 无实心圆点图标：circle-dot 是"环+心"，配合语义色即状态灯。
            self._dot.setPixmap(load_tabler_pixmap("circle-dot", DOT_SIZE, color))
        except Exception:
            pass
        self._proto_label.setText(model.protocol or "—")
        self._state_label.setText(state_text)
        self._state_label.setStyleSheet(
            f"color:{color};font-size:12px;background:transparent;")
        self._detail_label.setText(detail)
        self._detail_label.setStyleSheet(
            f"color:{BRIGHT_ERROR if role == 'error' else TEXT_SECONDARY};"
            "font-size:11px;background:transparent;")

    @staticmethod
    def _target_summary(status: dict, cfg, proto: str) -> str:
        """输出目标摘要：优先实际发送器，回退配置（并注明"未生效"）。"""
        artnet_target = status.get("artnet_target")
        if artnet_target:
            return f"输出 -> {artnet_target}"
        senders = int(status.get("sacn_senders", 0) or 0)
        if senders:
            return f"{senders} 个域发送器（目标以运行中发送器为准）"
        cfg_ip = str(getattr(cfg, "output_ip", "") or "")
        if status.get("running"):
            return f"输出目标未建立（配置 {cfg_ip} 待重启生效）"
        return f"目标 {cfg_ip}（{proto}）" if cfg_ip else "尚未配置输出目标"

    # 兼容旧调用名（既有测试/外部引用）
    def refresh(self, nm) -> None:
        self.update_from_manager(nm)
