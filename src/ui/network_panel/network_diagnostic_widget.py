"""
Network Diagnostic Widget — Visual display of network diagnostic results

Shows real-time network status for cross-device DMX output:
- Firewall status with visual indicators
- Network adapter enumeration
- Port availability checks
- IP conflict detection
- Actionable recommendations

R182 changes:
  - Buttons unified via ``network_button_factory`` (22px / 11px / 14px icon /
    theme tokens) — the three inline QSS copies with raw hex are gone.
  - One-click allow reuses ``firewall_allow`` (quoted rule name, single source
    with the backend probe) and reports the **verified** result instead of
    trusting ``ShellExecuteW`` (P1-3 false success).
  - ``showEvent`` re-runs the diagnostic and ``hideEvent`` restores the
    auto-refresh button state, so a tab that was hidden mid-refresh comes back
    consistent (P2).
"""
from __future__ import annotations

import logging

import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from ...network.network_diagnostics import NetworkDiagnostics, DiagnosticStatus
from ..qascade_theme import (
    TEXT_SECONDARY, TEXT_DISABLED, STATUS_SUCCESS_INK as BRIGHT_SUCCESS,
    ERROR_RED as BRIGHT_ERROR, STATUS_WARNING_INK as BRIGHT_WARN, INFO_BLUE as BRIGHT_INFO,
)
from ..widget_kit import PanelSection, EmptyState
from .network_status_helpers import ICON_CHECK, ICON_CROSS, icon_row
from .network_button_factory import make_button
from .firewall_allow import (
    FIREWALL_RULE_BASE, build_allow_command_text, platform_supported,
    rule_name, run_allow_and_verify,
)

log = logging.getLogger(__name__)

#: 一键放行规则名（与后端探测同源；保留常量名兼容既有引用）
FIREWALL_RULE_NAME = FIREWALL_RULE_BASE


def dmx_firewall_rules() -> list:
    """两条放行规则名（In/Out，各覆盖 UDP 6454/5568）。纯函数可测。

    R182 P1-3：构造统一走 ``firewall_allow.build_allow_rules``——修前此处把
    规则名写成 ``name=雾白纪 DMX UDP``（无引号 + 与配置页名字不一致），
    netsh 必败且诊断查不到。
    """
    from .firewall_allow import build_allow_rules
    return build_allow_rules()


def firewall_commands_text() -> str:
    """完整管理员命令串（先删旧名后添加；供剪贴板降级路径）。"""
    return build_allow_command_text()


class NetworkDiagnosticWidget(QWidget):
    """Network diagnostic widget with auto-refresh"""

    run_diagnostic_requested = pyqtSignal()  # User clicked manual refresh
    _diag_finished = pyqtSignal(object)      # 后台诊断完成（None=失败）
    _allow_finished = pyqtSignal(object)     # 后台放行+回探完成（AllowState）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._diagnostics = NetworkDiagnostics()
        self._last_result = None
        self._allow_running = False

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._run_diagnostic)
        self._auto_refresh_enabled = False

        self._setup_ui()
        self._allow_finished.connect(self._on_allow_finished)

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        # Header with refresh button
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("网络诊断 Network Diagnostics")
        title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 13px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self._refresh_btn = make_button(
            "刷新诊断", "refresh", "neutral",
            tooltip="手动运行网络诊断检查（防火墙 / 端口 / 适配器 / IP 冲突）")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        header.addWidget(self._refresh_btn)

        self._auto_refresh_btn = make_button(
            "自动刷新: 关", "", "muted", checkable=True,
            tooltip="启用自动刷新（每 5 秒重跑一次诊断）")
        self._auto_refresh_btn.toggled.connect(self._on_auto_refresh_toggled)
        header.addWidget(self._auto_refresh_btn)

        # 一键放行 DMX 端口（Art-Net 6454 / sACN 5568，in/out 两条规则）
        self._firewall_btn = make_button(
            "一键放行 DMX 端口", "lock-open", "success",
            tooltip=(
                "以管理员权限添加 2 条防火墙放行规则（入/出各一条，"
                "UDP 6454/5568，规则名「" + rule_name("in") + "」/「"
                + rule_name("out") + "」）。\n"
                "会弹出 UAC 确认；提交后自动回探实际规则状态。\n"
                "失败时命令自动复制到剪贴板，可手动执行。"))
        self._firewall_btn.clicked.connect(self._on_firewall_whitelist_clicked)
        header.addWidget(self._firewall_btn)

        main.addLayout(header)

        # 放行结果行（提交/回探状态；平时隐藏，避免占版面）
        self._allow_label = QLabel("")
        self._allow_label.setWordWrap(True)
        self._allow_label.setVisible(False)
        self._allow_label.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        main.addWidget(self._allow_label)

        # Scroll area for results
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(8)

        # Empty state (shown before first diagnostic)
        self._empty_state = EmptyState(
            "网络诊断待运行",
            "点击「刷新诊断」运行网络检查，或启用自动刷新",
            "test-pipe"
        )
        self._content_lay.addWidget(self._empty_state)

        # Result sections (created on demand)
        self._overall_section = None
        self._firewall_section = None
        self._adapter_section = None
        self._port_section = None
        self._recommendations_section = None

        self._content_lay.addStretch()
        scroll.setWidget(self._content)
        main.addWidget(scroll, stretch=1)

    def _on_refresh_clicked(self):
        """User clicked manual refresh button"""
        self._run_diagnostic()
        self.run_diagnostic_requested.emit()

    # ── 一键放行 DMX 端口（P1-3：提交 + 回探确认）──────────────────────────

    def _on_firewall_whitelist_clicked(self):
        """一键放行：UAC 提权跑 netsh 规则，后台回探真实状态后出结论。

        修前只 ``_elevated_add_rules``（看 ShellExecuteW 返回值）就显示
        "已提交放行规则"，且 netsh 参数无引号必失败 -> 假成功。现在把
        "提交"与"结论"分开：提交成功仅提示等待，结论由回探给出。
        """
        if self._allow_running:
            return
        if not platform_supported():
            self._show_allow("warn", "非 Windows 平台无需放行；命令已复制到剪贴板。")
            self._copy_firewall_commands()
            return

        self._allow_running = True
        self._firewall_btn.setEnabled(False)
        self._firewall_btn.setText("正在放行…")
        self._show_allow(
            "info", "已提交提权请求，请在 UAC 弹窗中点「是」；随后自动回探实际规则状态…")

        def _work():
            try:
                state = run_allow_and_verify(timeout_sec=6.0)
            except Exception as e:      # pragma: no cover - 后台兜底
                log.error("防火墙放行失败: %s", e, exc_info=True)
                state = None
            self._allow_finished.emit(state)

        threading.Thread(target=_work, daemon=True, name="fw-allow").start()

    def _on_allow_finished(self, state):
        """回探结论落 UI（成功 / 部分成功 / 失败 + 降级复制命令）。"""
        self._allow_running = False
        self._firewall_btn.setEnabled(True)
        self._firewall_btn.setText("一键放行 DMX 端口")
        if state is None:
            self._show_allow("error", "放行流程异常（详情见日志）；命令已复制到剪贴板。")
            self._copy_firewall_commands()
        elif state.all_allowed:
            self._show_allow(
                "success",
                f"放行已生效并已验证：规则「{rule_name('in')}」与「{rule_name('out')}」"
                "覆盖 UDP 6454/5568。")
        elif state.any_allowed:
            self._show_allow("warn", "部分生效：" + "；".join(state.details))
        else:
            self._show_allow(
                "error", "放行未生效（UAC 可能被拒绝或系统策略阻止）："
                         + "；".join(state.details) + "；命令已复制到剪贴板。")
            self._copy_firewall_commands()
        self._run_diagnostic()      # 放行后复检（诊断页四项标志同步刷新）

    def _show_allow(self, kind: str, text: str) -> None:
        """放行状态行：四态配色 + 强制可见（不允许静默）。"""
        color = {
            "success": BRIGHT_SUCCESS, "warn": BRIGHT_WARN,
            "error": BRIGHT_ERROR, "info": BRIGHT_INFO,
        }.get(kind, TEXT_SECONDARY)
        self._allow_label.setStyleSheet(
            f"color:{color};font-size:11px;background:transparent;")
        self._allow_label.setText(text)
        self._allow_label.setVisible(True)

    def _copy_firewall_commands(self) -> None:
        """降级路径：命令复制到剪贴板并提示手动执行。"""
        from PyQt6.QtWidgets import QApplication
        text = firewall_commands_text()
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            log.debug("复制防火墙命令失败", exc_info=True)
        QMessageBox.warning(
            self, "需要管理员权限",
            "自动放行未完成（UAC 被拒绝或系统限制）。\n"
            "已把 netsh 命令复制到剪贴板，请在「管理员」CMD/PowerShell 中粘贴执行：\n\n"
            f"{text}")

    def _on_auto_refresh_toggled(self, enabled: bool):
        """Toggle auto-refresh timer"""
        self._auto_refresh_enabled = enabled
        if enabled:
            self._auto_refresh_btn.setText("自动刷新: 开")
            self._auto_refresh_timer.start(5000)  # 5 seconds
            self._run_diagnostic()  # Run immediately
        else:
            self._auto_refresh_btn.setText("自动刷新: 关")
            self._auto_refresh_timer.stop()

    def _run_diagnostic(self):
        """Run diagnostic and update UI.

        R178 修（用户实测"开网络页监视器卡死"）：run_full_diagnostic 内含
          netsh/PowerShell 子进程 + 端口/适配器枚举——在 UI 线程同步执行时
          整窗冻结。改为后台 daemon 线程执行，结果经
          `_diag_finished` 信号回 UI 线程展示；进行中防重入。
        """
        if getattr(self, "_diag_running", False):
            return
        if not getattr(self, "_diag_conn_wired", False):
            self._diag_finished.connect(self._on_diag_finished)   # 审计修复：漏接线
            self._diag_conn_wired = True
        self._diag_running = True
        self._refresh_btn.setEnabled(False)
        diagnostics = self._diagnostics

        def _work():
            try:
                result = diagnostics.run_full_diagnostic()
            except Exception as e:
                log.error("Diagnostic failed: %s", e, exc_info=True)
                result = None
            # 信号回 UI 线程（QApplication 单实例，跨线程 safe）
            self._diag_finished.emit(result)

        threading.Thread(target=_work, daemon=True, name="net-diag").start()

    def _on_diag_finished(self, result):
        self._diag_running = False
        self._refresh_btn.setEnabled(True)
        if result is None:
            self._show_error("网络诊断执行失败（详情见日志）")
            return
        self._last_result = result
        self._display_result(result)

    def _display_result(self, result):
        """Display diagnostic result in UI"""
        # Hide empty state
        self._empty_state.setVisible(False)

        # Clear old sections
        self._clear_result_sections()

        # Overall status
        self._overall_section = self._create_overall_section(result)
        self._content_lay.insertWidget(0, self._overall_section)

        # Firewall section
        self._firewall_section = self._create_firewall_section(result)
        self._content_lay.insertWidget(1, self._firewall_section)

        # Adapter section
        self._adapter_section = self._create_adapter_section(result)
        self._content_lay.insertWidget(2, self._adapter_section)

        # Port section
        self._port_section = self._create_port_section(result)
        self._content_lay.insertWidget(3, self._port_section)

        # Recommendations
        if result.recommendations:
            self._recommendations_section = self._create_recommendations_section(result)
            self._content_lay.insertWidget(4, self._recommendations_section)

    def _clear_result_sections(self):
        """Remove old result sections"""
        for section in [
            self._overall_section,
            self._firewall_section,
            self._adapter_section,
            self._port_section,
            self._recommendations_section,
        ]:
            if section:
                self._content_lay.removeWidget(section)
                section.deleteLater()

        self._overall_section = None
        self._firewall_section = None
        self._adapter_section = None
        self._port_section = None
        self._recommendations_section = None

    def _create_overall_section(self, result) -> QWidget:
        """Create overall status section"""
        section = PanelSection("整体状态 Overall Status")
        icon, text, color = self._status_display(result.overall_status)
        section.add_widget(icon_row(icon, text, color, size=16, font_size=14,
                                    bold=True))
        return section

    def _status_head(self, section, status) -> None:
        """分区首行：状态图标 + 状态文字（四态同一实现）。"""
        icon, text, color = self._status_display(status)
        section.add_widget(icon_row(icon, text, color, font_size=12))

    @staticmethod
    def _add_checks(section, checks) -> None:
        """端口/防火墙逐条勾选行（checks: [(label, ok), ...]）。"""
        for label, ok in checks:
            section.add_widget(icon_row(
                ICON_CHECK if ok else ICON_CROSS, label,
                BRIGHT_SUCCESS if ok else BRIGHT_ERROR, indent=8))

    @staticmethod
    def _add_details(section, details) -> None:
        """细节行（tabler 折角标记 + 弱化文字）。"""
        for detail in details or ():
            section.add_widget(icon_row(
                "corner-down-right", str(detail), TEXT_SECONDARY,
                size=12, indent=8, font_size=10))

    def _create_firewall_section(self, result) -> QWidget:
        """Create firewall status section"""
        section = PanelSection("防火墙状态 Firewall Status")
        self._status_head(section, result.firewall_status)
        self._add_checks(section, [
            ("Art-Net 入站 (6454)", result.firewall_artnet_in),
            ("Art-Net 出站 (6454)", result.firewall_artnet_out),
            ("sACN 入站 (5568)", result.firewall_sacn_in),
            ("sACN 出站 (5568)", result.firewall_sacn_out),
        ])
        self._add_details(section, result.firewall_details)
        return section

    def _create_adapter_section(self, result) -> QWidget:
        """Create network adapter section"""
        section = PanelSection("网络适配器 Network Adapters")
        self._status_head(section, result.adapter_status)
        for adapter in result.adapters or ():
            if adapter.is_loopback:
                continue  # Skip loopback in display
            up = adapter.status == "up"
            section.add_widget(icon_row(
                "circle-dot" if up else "circle-off",
                f"{adapter.name}: {adapter.ip_address}",
                BRIGHT_SUCCESS if up else BRIGHT_ERROR, indent=8))
        self._add_details(section, result.adapter_details)
        return section

    def _create_port_section(self, result) -> QWidget:
        """Create port availability section"""
        section = PanelSection("端口可用性 Port Availability")
        self._status_head(section, result.port_status)
        self._add_checks(section, [
            ("Art-Net (UDP 6454)", result.port_6454_available),
            ("sACN (UDP 5568)", result.port_5568_available),
        ])
        self._add_details(section, result.port_details)
        return section

    def _create_recommendations_section(self, result) -> QWidget:
        """Create recommendations section"""
        section = PanelSection("建议 Recommendations", expanded=True)
        for i, rec in enumerate(result.recommendations, 1):
            section.add_widget(icon_row(
                "info-circle", f"{i}. {rec}", BRIGHT_WARN, size=12, font_size=11))
        return section

    def _status_display(self, status: DiagnosticStatus) -> tuple[str, str, str]:
        """诊断状态 -> (tabler 图标名, 文本, 颜色)。"""
        mapping = {
            DiagnosticStatus.PASS: (ICON_CHECK, "通过 PASS", BRIGHT_SUCCESS),
            DiagnosticStatus.WARN: ("alert-triangle", "警告 WARN", BRIGHT_WARN),
            DiagnosticStatus.FAIL: (ICON_CROSS, "失败 FAIL", BRIGHT_ERROR),
        }
        return mapping.get(status, ("alert-circle", "未知 UNKNOWN", TEXT_DISABLED))

    def _show_error(self, error_msg: str):
        """Display error message"""
        self._empty_state.setVisible(False)
        self._clear_result_sections()

        error_section = PanelSection("诊断错误 Diagnostic Error")
        error_lbl = QLabel(f"诊断运行失败: {error_msg}")
        error_lbl.setWordWrap(True)
        error_lbl.setStyleSheet(f"color: {BRIGHT_ERROR}; font-size: 11px;")
        error_section.add_widget(error_lbl)

        self._content_lay.insertWidget(0, error_section)

    def get_last_result(self):
        """Get last diagnostic result (for external access)"""
        return self._last_result

    def showEvent(self, event):
        """显示时刷新诊断（R182 P2：每次显示都重跑，不再只在首次）。"""
        super().showEvent(event)
        # 隐藏期间网络状态可能已变（启动/停止/改配置）-> 重新出结果，
        # 而不是拿几十秒前的陈旧结果当现状。
        QTimer.singleShot(100, self._run_diagnostic)

    def hideEvent(self, event):
        """隐藏时停自动刷新定时器，并回到与开关一致的稳定态（R182 P2）。

        要点：同步按钮态时必须 blockSignals——``setChecked`` 触发的 ``toggled``
        会回调 ``_on_auto_refresh_toggled``，把刚停掉的定时器又启起来
        （隐藏后仍在后台每 5 秒跑 netsh/PowerShell 子进程）。
        """
        super().hideEvent(event)
        self._auto_refresh_timer.stop()
        btn = self._auto_refresh_btn
        btn.blockSignals(True)
        try:
            btn.setChecked(self._auto_refresh_enabled)
            btn.setText("自动刷新: 开" if self._auto_refresh_enabled
                        else "自动刷新: 关")
        finally:
            btn.blockSignals(False)

    def closeEvent(self, event):
        """Clean up timers"""
        self._auto_refresh_timer.stop()
        super().closeEvent(event)

    # 兼容旧调用名（既有测试可能引用 _elevated_add_rules）
    @staticmethod
    def _elevated_add_rules() -> None:
        """提权提交放行（委托 firewall_allow；旧签名无参兼容）。"""
        from .firewall_allow import run_elevated_allow
        run_elevated_allow()
