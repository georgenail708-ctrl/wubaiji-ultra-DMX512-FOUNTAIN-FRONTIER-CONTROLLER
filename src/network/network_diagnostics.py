"""
Network Diagnostic Tool for Cross-Device DMX Output Failures

Auto-checks:
- Firewall status for UDP ports 6454 (Art-Net) and 5568 (sACN)
- IP conflicts and network adapter state
- UDP port availability (binding test)
- Network interface enumeration

Usage:
    from src.network.network_diagnostics import NetworkDiagnostics
    diag = NetworkDiagnostics()
    result = diag.run_full_diagnostic()
    print(result.to_dict())
"""
from __future__ import annotations

import logging
import socket
import subprocess
import platform
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum

from src.network.firewall_probe import (
    FirewallRule,
    get_firewall_rules,  # noqa: F401 — 保持既有再导出面（外部诊断脚本引用）
    parse_rule_dict,
    probe_dmx_firewall_rules,
    rule_allows,
    rule_display_names,
)
# N6/N7 纯逻辑在 network_health（本文件受 500 行门禁约束）；名字在此再导出，
# 既有调用方（网络页/测试脚本）零迁移。
from src.network.network_health import (  # noqa: F401
    HEALTH_WEIGHTS,
    MULTICAST_RECOMMENDATIONS,
    append_health_summary,
    compute_health_score,
    health_grade,
    health_summary_line,
    recommend_for_multicast,
)
#: 旧/内部名兼容（W2 拆分前叫 `_recommend_for_multicast`）
_recommend_for_multicast = recommend_for_multicast

log = logging.getLogger(__name__)


class DiagnosticStatus(Enum):
    """Diagnostic check status"""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class NetworkAdapter:
    """Network adapter information"""
    name: str
    ip_address: str
    subnet_mask: str
    status: str  # "up" or "down"
    is_loopback: bool = False


@dataclass
class PortCheck:
    """UDP port binding check result"""
    port: int
    bindable: bool
    error_msg: Optional[str] = None


@dataclass
class DiagnosticResult:
    """Complete diagnostic result"""
    firewall_status: DiagnosticStatus = DiagnosticStatus.UNKNOWN
    firewall_artnet_in: bool = False
    firewall_artnet_out: bool = False
    firewall_sacn_in: bool = False
    firewall_sacn_out: bool = False
    firewall_details: List[str] = field(default_factory=list)

    adapters: List[NetworkAdapter] = field(default_factory=list)
    adapter_status: DiagnosticStatus = DiagnosticStatus.UNKNOWN
    adapter_details: List[str] = field(default_factory=list)

    port_6454_available: bool = False
    port_5568_available: bool = False
    port_status: DiagnosticStatus = DiagnosticStatus.UNKNOWN
    port_details: List[str] = field(default_factory=list)

    ip_conflicts: List[str] = field(default_factory=list)
    conflict_status: DiagnosticStatus = DiagnosticStatus.UNKNOWN

    overall_status: DiagnosticStatus = DiagnosticStatus.UNKNOWN
    recommendations: List[str] = field(default_factory=list)
    #: N7 健康评分（0-100；None = 未评分，旧调用方零影响）
    health_score: Optional[int] = None
    #: N7 扣分明细（tooltip 用；空表 = 无扣分）
    health_deductions: List[str] = field(default_factory=list)
    #: N6 输出模式（"multicast" 时追加 IGMP 建议；缺省广播 = 不追加）
    output_mode: str = "broadcast"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "overall_status": self.overall_status.value,
            "health_score": self.health_score,
            "health_deductions": self.health_deductions,
            "firewall": {
                "status": self.firewall_status.value,
                "artnet_in": self.firewall_artnet_in,
                "artnet_out": self.firewall_artnet_out,
                "sacn_in": self.firewall_sacn_in,
                "sacn_out": self.firewall_sacn_out,
                "details": self.firewall_details,
            },
            "adapters": {
                "status": self.adapter_status.value,
                "count": len(self.adapters),
                "details": self.adapter_details,
                "interfaces": [
                    {
                        "name": a.name,
                        "ip": a.ip_address,
                        "mask": a.subnet_mask,
                        "status": a.status,
                        "loopback": a.is_loopback,
                    }
                    for a in self.adapters
                ],
            },
            "ports": {
                "status": self.port_status.value,
                "artnet_6454": self.port_6454_available,
                "sacn_5568": self.port_5568_available,
                "details": self.port_details,
            },
            "conflicts": {
                "status": self.conflict_status.value,
                "found": self.ip_conflicts,
            },
            "recommendations": self.recommendations,
        }


class NetworkDiagnostics:
    """Network diagnostics for cross-device DMX output"""

    DMX_PORTS = [6454, 5568]  # Art-Net, sACN
    RULE_NAMES = ["雾白纪 ULTRA", "雾白纪", "冬极白星", "Qascade"]  # Historical names

    def __init__(self, output_mode: str = ""):
        self.is_windows = platform.system() == "Windows"
        #: N6 输出模式（调用方注入；空 = 运行诊断时从网络管理器读取）
        self.output_mode = str(output_mode or "")

    def _resolve_output_mode(self) -> str:
        """N6：当前输出模式（显式注入优先，否则只读**已存在**的网络管理器实例）。

        刻意不调 `NetworkManager.instance()`——那会在无 GUI/无音频环境里现建
        一个管理器（QTimer 需要 QApplication）。这里只读已存在的单例配置。
        """
        if self.output_mode:
            return self.output_mode
        try:
            from .network_manager import NetworkManager
            mgr = getattr(NetworkManager, "_instance", None)
            cfg = getattr(mgr, "_config", None) if mgr is not None else None
            mode = str(getattr(cfg, "output_mode", "") or "")
            if mode:
                return mode
        except Exception:
            log.debug("output mode read failed", exc_info=True)
        return ""

    def run_full_diagnostic(self) -> DiagnosticResult:
        """Run all diagnostic checks and return comprehensive result"""
        result = DiagnosticResult()
        result.output_mode = self._resolve_output_mode() or "broadcast"

        # Check firewall rules (Windows only)
        if self.is_windows:
            self._check_firewall(result)
        else:
            result.firewall_status = DiagnosticStatus.UNKNOWN
            result.firewall_details.append("Firewall check only supported on Windows")

        # Check network adapters
        self._check_adapters(result)

        # Check port availability
        self._check_ports(result)

        # Check for IP conflicts
        self._check_ip_conflicts(result)

        # Compute overall status, health score and recommendations
        self._compute_overall_status(result)
        compute_health_score(result)
        self._generate_recommendations(result)

        return result

    def _check_firewall(self, result: DiagnosticResult) -> None:
        """Check Windows firewall rules for DMX ports"""
        try:
            rules, probe_ok = self._get_firewall_rules_ex()
            if not probe_ok:
                # ★ R182 审计修：探测链整体不可用 ≠ 无规则（修前误报 FAIL）。
                result.firewall_status = DiagnosticStatus.UNKNOWN
                result.firewall_details.append(
                    "防火墙探测不可用（PowerShell/netsh 均失败；权限或环境受限）")
                return

            # Check for DMX rules
            result.firewall_artnet_in = rule_allows(rules, "in", "6454")
            result.firewall_artnet_out = rule_allows(rules, "out", "6454")
            result.firewall_sacn_in = rule_allows(rules, "in", "5568")
            result.firewall_sacn_out = rule_allows(rules, "out", "5568")

            _summarize_firewall(result)

        except Exception as e:
            result.firewall_status = DiagnosticStatus.UNKNOWN
            result.firewall_details.append(f"Firewall check failed: {e}")
            log.warning("Firewall check failed: %s", e, exc_info=True)

    def _get_firewall_rules_ex(self) -> "Tuple[List[FirewallRule], bool]":
        """三态探测：``(rules, probe_ok)``（probe_ok=False → 探测不可用）。

        R182 审计修：修前空表无法区分"无规则"与"查不了"，诊断页一律 FAIL。
        """
        from ..network.firewall_probe import probe_dmx_firewall_rules_ex
        return probe_dmx_firewall_rules_ex(rule_display_names(self.RULE_NAMES))

    def _get_firewall_rules(self) -> List[FirewallRule]:
        """查询本软件 DMX 防火墙规则（R182 P0-2 语言无关定向探测）。

        修前 ``get_firewall_rules`` 跑 netsh 全表 + 写死英文标签 → 中文
        Windows 恒返回空表 → 诊断页把已放行误报成 FAIL。现按 RULE_NAMES
        展开出完整规则名列表，交 :func:`probe_dmx_firewall_rules`
        （PowerShell 定向 JSON 优先，netsh 定向 + 中英双标签回退）。
        """
        return probe_dmx_firewall_rules(rule_display_names(self.RULE_NAMES))

    def _parse_rule_dict(self, d: Dict[str, str]) -> FirewallRule:
        """Parse netsh rule dict to FirewallRule"""
        return parse_rule_dict(d)

    def _check_adapters(self, result: DiagnosticResult) -> None:
        """Enumerate network adapters and check status"""
        try:
            adapters = self._get_network_adapters()
            result.adapters = adapters

            active = [a for a in adapters if a.status == "up" and not a.is_loopback]

            if not active:
                result.adapter_status = DiagnosticStatus.FAIL
                result.adapter_details.append("No active network adapters found")
            elif len(active) == 1:
                result.adapter_status = DiagnosticStatus.PASS
                result.adapter_details.append(
                    f"1 active adapter: {active[0].name} ({active[0].ip_address})")
            else:
                result.adapter_status = DiagnosticStatus.WARN
                result.adapter_details.append(
                    f"{len(active)} active adapters - ensure correct one for output")
                for a in active:
                    result.adapter_details.append(f"  - {a.name}: {a.ip_address}")

        except Exception as e:
            result.adapter_status = DiagnosticStatus.UNKNOWN
            result.adapter_details.append(f"Adapter check failed: {e}")
            log.warning("Adapter check failed: %s", e, exc_info=True)

    def _get_network_adapters(self) -> List[NetworkAdapter]:
        """Get list of network adapters using socket interface"""
        adapters = []
        try:
            import socket
            hostname = socket.gethostname()
            # Get all addresses for this host
            addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)

            seen_ips = set()
            for addr_info in addrs:
                ip = addr_info[4][0]
                if ip not in seen_ips:
                    seen_ips.add(ip)
                    is_loopback = ip.startswith("127.")
                    adapters.append(NetworkAdapter(
                        name=f"Interface-{ip}",
                        ip_address=ip,
                        subnet_mask="255.255.255.0",  # Default assumption
                        status="up",
                        is_loopback=is_loopback,
                    ))

            # Add common loopback if not present
            if "127.0.0.1" not in seen_ips:
                adapters.append(NetworkAdapter(
                    name="Loopback",
                    ip_address="127.0.0.1",
                    subnet_mask="255.0.0.0",
                    status="up",
                    is_loopback=True,
                ))

        except Exception as e:
            log.warning("Failed to enumerate adapters: %s", e, exc_info=True)

        return adapters

    def _check_ports(self, result: DiagnosticResult) -> None:
        """Check if DMX ports can be bound (availability test)"""
        try:
            port_6454 = self._check_port_binding(6454)
            port_5568 = self._check_port_binding(5568)

            result.port_6454_available = port_6454.bindable
            result.port_5568_available = port_5568.bindable

            if port_6454.bindable and port_5568.bindable:
                result.port_status = DiagnosticStatus.PASS
                result.port_details.append("Both Art-Net (6454) and sACN (5568) ports available")
            elif port_6454.bindable or port_5568.bindable:
                result.port_status = DiagnosticStatus.WARN
                if not port_6454.bindable:
                    result.port_details.append(f"Art-Net port 6454 in use: {port_6454.error_msg}")
                if not port_5568.bindable:
                    result.port_details.append(f"sACN port 5568 in use: {port_5568.error_msg}")
            else:
                result.port_status = DiagnosticStatus.FAIL
                result.port_details.append("Both DMX ports are in use by other processes")
                result.port_details.append(f"Port 6454: {port_6454.error_msg}")
                result.port_details.append(f"Port 5568: {port_5568.error_msg}")

        except Exception as e:
            result.port_status = DiagnosticStatus.UNKNOWN
            result.port_details.append(f"Port check failed: {e}")
            log.warning("Port check failed: %s", e, exc_info=True)

    def _check_port_binding(self, port: int) -> PortCheck:
        """Test if a UDP port can be bound"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            return PortCheck(port=port, bindable=True)
        except OSError as e:
            return PortCheck(
                port=port,
                bindable=False,
                error_msg=f"{e.errno} - {e.strerror}"
            )
        except Exception as e:
            return PortCheck(port=port, bindable=False, error_msg=str(e))
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _check_ip_conflicts(self, result: DiagnosticResult) -> None:
        """Check for duplicate IP addresses (basic heuristic)"""
        try:
            ip_counts = {}
            for adapter in result.adapters:
                if not adapter.is_loopback:
                    ip_counts[adapter.ip_address] = ip_counts.get(adapter.ip_address, 0) + 1

            conflicts = [ip for ip, count in ip_counts.items() if count > 1]
            result.ip_conflicts = conflicts

            if conflicts:
                result.conflict_status = DiagnosticStatus.FAIL
                result.adapter_details.append(
                    f"IP conflict detected: {', '.join(conflicts)}")
            else:
                result.conflict_status = DiagnosticStatus.PASS

        except Exception as e:
            result.conflict_status = DiagnosticStatus.UNKNOWN
            log.warning("IP conflict check failed: %s", e, exc_info=True)

    def _compute_overall_status(self, result: DiagnosticResult) -> None:
        """Compute overall diagnostic status from individual checks"""
        statuses = [
            result.firewall_status,
            result.adapter_status,
            result.port_status,
            result.conflict_status,
        ]

        if any(s == DiagnosticStatus.FAIL for s in statuses):
            result.overall_status = DiagnosticStatus.FAIL
        elif any(s == DiagnosticStatus.WARN for s in statuses):
            result.overall_status = DiagnosticStatus.WARN
        elif all(s == DiagnosticStatus.PASS for s in statuses):
            result.overall_status = DiagnosticStatus.PASS
        else:
            result.overall_status = DiagnosticStatus.UNKNOWN

    def _generate_recommendations(self, result: DiagnosticResult) -> None:
        """Generate actionable recommendations based on diagnostic results"""
        _recommend_for_failures(result)
        _recommend_for_firewall_warnings(result)
        _recommend_for_adapters(result)
        recommend_for_multicast(result)    # N6：多播输出 → IGMP/交换机建议
        append_health_summary(result)      # N7：评分收尾（可见于既有建议区）


# ── 拆分出来的纯逻辑段（R178 架构门禁：原 _check_firewall /
#    _generate_recommendations 内联分支，语义与文案逐行保留，顺序不变）──

def _summarize_firewall(result: DiagnosticResult) -> None:
    """按四个"方向 × 端口"标志汇总防火墙状态与明细。"""
    if (result.firewall_artnet_in and result.firewall_artnet_out
            and result.firewall_sacn_in and result.firewall_sacn_out):
        result.firewall_status = DiagnosticStatus.PASS
        result.firewall_details.append("All DMX ports allowed in firewall")
    elif result.firewall_artnet_in or result.firewall_sacn_in:
        result.firewall_status = DiagnosticStatus.WARN
        result.firewall_details.append("Some DMX ports allowed, but not all")
        _append_blocked_details(result)
    else:
        result.firewall_status = DiagnosticStatus.FAIL
        result.firewall_details.append("No DMX firewall rules found")
        result.firewall_details.append(
            "Cross-device UDP will likely be blocked by Windows Firewall")


def _append_blocked_details(result: DiagnosticResult) -> None:
    """WARN 分支：逐条列出被拦的方向（原 _check_firewall 内联四个 if）。"""
    if not result.firewall_artnet_in:
        result.firewall_details.append("Art-Net inbound (6454) blocked")
    if not result.firewall_artnet_out:
        result.firewall_details.append("Art-Net outbound (6454) blocked")
    if not result.firewall_sacn_in:
        result.firewall_details.append("sACN inbound (5568) blocked")
    if not result.firewall_sacn_out:
        result.firewall_details.append("sACN outbound (5568) blocked")


def _recommend_for_failures(result: DiagnosticResult) -> None:
    """防火墙 FAIL / 端口占用 / 无网卡 / IP 冲突 四条建议（保持原顺序）。"""
    if result.firewall_status == DiagnosticStatus.FAIL:
        result.recommendations.append(
            "Click 'Firewall Allow' button in Network Config to open UDP ports 6454/5568")

    if not result.port_6454_available or not result.port_5568_available:
        result.recommendations.append(
            "DMX ports in use - stop other DMX software or use different protocol")

    if result.adapter_status == DiagnosticStatus.FAIL:
        result.recommendations.append(
            "No active network adapters - check network cable/WiFi connection")

    if result.ip_conflicts:
        result.recommendations.append(
            f"IP conflict on {', '.join(result.ip_conflicts)} - reconfigure network adapters")


def _recommend_for_firewall_warnings(result: DiagnosticResult) -> None:
    """防火墙 WARN 时的两条部分放行建议（非 WARN 直接返回，等价于原 if 包裹）。"""
    if result.firewall_status != DiagnosticStatus.WARN:
        return

    if not result.firewall_artnet_in or not result.firewall_artnet_out:
        result.recommendations.append(
            "Art-Net (6454) partially blocked - cross-device output may fail")
    if not result.firewall_sacn_in or not result.firewall_sacn_out:
        result.recommendations.append(
            "sACN (5568) partially blocked - cross-device output may fail")


def _recommend_for_adapters(result: DiagnosticResult) -> None:
    """多网卡提示 + 全绿收尾语（原 _generate_recommendations 末两段）。"""
    if len([a for a in result.adapters if a.status == "up" and not a.is_loopback]) > 1:
        result.recommendations.append(
            "Multiple network adapters active - ensure output IP targets correct subnet")

    if result.overall_status == DiagnosticStatus.PASS:
        result.recommendations.append(
            "Network configuration looks good for cross-device DMX output")
