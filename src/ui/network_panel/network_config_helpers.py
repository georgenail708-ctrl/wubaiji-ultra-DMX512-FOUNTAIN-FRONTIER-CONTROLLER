"""Network configuration controls and application, shared by the real panel."""
from __future__ import annotations

import ipaddress
import logging
import time

from ...core.models import Protocol

log = logging.getLogger(__name__)


def set_panel_status(panel, kind: str, text: str, hold_seconds: float) -> None:
    panel._feedback_until = time.monotonic() + hold_seconds if kind != "dim" else 0.0
    panel._status_label.set_state(kind, text)
    panel._status_label.setVisible(kind != "dim")
    panel._network_empty_state.setVisible(kind == "dim")


def refresh_panel_status(panel) -> None:
    if getattr(panel, "_installed_in_window", False) and not panel.isVisible():
        return
    if panel._nm is None:
        panel._status_card.update_from_manager(None)
        panel.set_status("dim", "未连接")
        return
    panel._status_card.update_from_manager(panel._nm)
    panel._refresh_binding_section()
    if time.monotonic() < getattr(panel, "_feedback_until", 0.0):
        return
    bind_error = getattr(panel._nm, "last_bind_error", None)
    if bind_error:
        panel.set_status("error", f"网络启动失败：{bind_error}", hold_seconds=0)
    elif panel._nm.is_running:
        panel._show_running_status()
    elif not panel._pending_allow_verify:
        panel.set_status("dim", "未连接")


def apply_control_states(panel) -> None:
    from .network_sections import apply_fixed_control_states
    controls = {"in_mode": panel._in_mode_combo,
                "in_universes": panel._in_universe_panel,
                "out_mode": panel._out_mode_combo,
                "out_port": panel._out_port_spin}
    apply_fixed_control_states(controls, artnet=panel._artnet_radio.isChecked())
    panel._in_ip.setEnabled(panel._in_enabled_check.isChecked())
    update_output_ip_control(panel)


def update_output_ip_control(panel) -> None:
    """Keep the target field meaningful for Art-Net and sACN modes."""
    multicast = not panel._artnet_radio.isChecked() and panel._out_mode_combo.currentIndex() == 0
    panel._out_ip.setEnabled(not multicast)
    if multicast:
        panel._out_ip.setPlaceholderText("多播地址按输出宇宙自动生成")
        panel._out_ip.setToolTip("sACN 多播目标由宇宙编号生成；此 IP 不参与多播输出。")
    elif panel._out_mode_combo.currentIndex() == 1:
        if panel._out_ip.text().strip() in ("255.255.255.255", "0.0.0.0"):
            panel._out_ip.clear()
        panel._out_ip.setPlaceholderText("接收主机 IP，例如 192.168.1.50")
        panel._out_ip.setToolTip("单播只发送到此主机 IP。同机可用 127.0.0.1；跨机填接收网卡 IP。")
    else:
        if not panel._out_ip.text().strip():
            panel._out_ip.setText("255.255.255.255")
        panel._out_ip.setPlaceholderText("255.255.255.255（全子网广播）")
        panel._out_ip.setToolTip("Art-Net 广播模式会附加本机网卡的定向广播目标。")


def output_configuration_error(cfg) -> str:
    """Reject addresses that cannot represent a unicast receiver."""
    if cfg.protocol == Protocol.SACN and cfg.output_mode != "unicast":
        return ""
    try:
        addr = ipaddress.IPv4Address(cfg.output_ip)
    except ipaddress.AddressValueError:
        return "目标 IP 无效，请填写接收主机的 IPv4 地址。"
    if cfg.output_mode != "unicast":
        return ""
    if addr.is_unspecified or addr.is_multicast or str(addr) == "255.255.255.255":
        return "单播需要具体的接收主机 IP，不能使用空地址、广播或多播地址。"
    from ...network.nic_broadcast import directed_broadcast_targets
    if str(addr) in directed_broadcast_targets():
        return "该 IP 是本机网段的广播地址；单播请填写接收主机的网卡 IP。"
    return ""


def apply_configuration(panel) -> None:
    """Apply valid output settings, preserving the existing restart contract."""
    if panel._nm is None:
        return
    cfg = panel.get_config()
    error = output_configuration_error(cfg)
    if error:
        panel.set_status("error", error)
        return
    panel._nm.configure(cfg)
    panel.config_changed.emit(cfg)
    if callable(panel._project_sink):
        try:
            panel._project_sink(cfg)
        except Exception:
            log.debug("网络配置回写工程失败", exc_info=True)
    running = bool(getattr(panel._nm, "is_running", False))
    panel._restart_pending = running
    mode = "单播" if cfg.output_mode == "unicast" else (
        "多播" if cfg.protocol == Protocol.SACN else "广播")
    target = cfg.output_ip if mode != "多播" else "按宇宙生成目标"
    if running:
        panel.set_status("warn", f"配置已保存（{cfg.protocol.value} · {mode} {target}）；停止并重新启动网络后生效")
    else:
        panel.set_status("info", f"配置已保存（{cfg.protocol.value} · {mode} {target}），点「启动网络」开始输出")


def start_network(panel) -> None:
    nm = panel._nm
    if nm is not None and hasattr(nm, "start"):
        error = output_configuration_error(nm._config)
        if error:
            panel.set_status("error", error + " 请修改并应用配置后再启动。")
            return
        panel._feedback_until = 0.0
        try:
            nm.start()
            if nm.is_running:
                panel._restart_pending = False
        except Exception as error:
            log.error("启动网络失败: %s", error, exc_info=True)
            panel.set_status("error", f"启动失败：{error}")
            panel.start_network.emit()
            return
    panel.start_network.emit()
    panel._refresh_status()


def stop_network(panel) -> None:
    if panel._nm is not None and hasattr(panel._nm, "stop"):
        panel._nm.stop()
    panel.stop_network.emit()
    panel._restart_pending = False
    panel._feedback_until = 0.0
    panel._refresh_status()


def show_running_status(panel) -> None:
    from .network_runtime_view import active_output_config, output_summary
    try:
        status = panel._nm.get_sender_status() or {}
    except Exception:
        status = {}
    cfg = getattr(panel._nm, "_config", None)
    actual = output_summary(active_output_config(panel._nm))
    detail = panel._running_detail(status, cfg, actual)
    if panel._restart_pending:
        panel.set_status("warn", f"运行中  {detail} · 配置已改，重启后生效", hold_seconds=0)
    else:
        panel.set_status("success", f"运行中  {detail}", hold_seconds=0)
