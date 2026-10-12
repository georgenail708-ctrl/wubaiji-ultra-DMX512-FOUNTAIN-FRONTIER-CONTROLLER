# -*- coding: utf-8 -*-
"""网络页速率/绑定表/黑洞检测的**纯逻辑层**（深查③ N1/N4/N5/N10）。

三个纯函数 + 一个小采样器，全部不碰 socket、不碰 QWidget：
  - :class:`SenderRateSampler` —— 从既有发送器计数器（`network_manager.
    _record_perf_stats` 已在数的 packets/errors 累计值 + `get_sender_status`
    的快照）算「实际发送 Hz + 丢包/跳过计数」；节流 ≥250ms 才重算一次
    （N1+N10：状态卡刷新要"活"，但不必每帧算）；
  - :func:`binding_table_rows` —— 每域 → 实际目标网卡/IP（N5）：
    广播模式 = 配置 IP + 逐网卡定向广播（`nic_broadcast.
    directed_broadcast_targets` 现成），单播模式 = 配置 IP；
  - :func:`blackhole_report` —— 黑洞检测（N4）：把「发送目标」与「本机网段」
    对账，抓两类现场高频故障：
      ① 目标 IP 不落在任何本机活动网段（跨网段直发不可达 = 典型双 NAT/发错网卡）；
      ② 单播目标存在但 ArtPoll 发现表里无任何节点（发了没人收）。
    真工程当前全子网广播（output_ip=255.255.255.255）→ 本函数对广播不给
    假告警，仅在**定向/单播**目标下做对账。
"""
from __future__ import annotations

import ipaddress
import time
from typing import Dict, List, Optional, Sequence, Tuple

#: 速率采样窗口下限（秒）：≥250ms 才重算（N1 节流规格）
MIN_SAMPLE_INTERVAL = 0.25

#: 低于此 Hz 视为"无实际输出"（心跳 1s 重发 ≈1Hz；真输出 25~44Hz）
STALL_HZ = 0.5


def format_rate_line(pps: float, universes: int, skipped: int = 0,
                     errors: int = 0) -> str:
    """速率行文案（N1+N10）：`12.3 包/秒 · 25 域 · 跳过 1204 · 错误 3`。

    零值项不出现（保持状态卡紧凑）；整包/秒给整数（不显示 "44.0"）。
    """
    pps = max(0.0, float(pps or 0.0))
    if pps >= 10.0:
        rate = f"{pps:.0f}"
    elif pps > 0.0:
        rate = f"{pps:.1f}"
    else:
        rate = "0"
    parts = [f"{rate} 包/秒"]
    if universes:
        parts.append(f"{int(universes)} 域")
    if skipped:
        parts.append(f"跳过 {int(skipped):,}")
    if errors:
        parts.append(f"错误 {int(errors):,}")
    return " · ".join(parts)


class SenderRateSampler:
    """发送速率采样器（N1：只读既有累计计数器，节流 ≥250ms 重算）。

    数据源 = ``NetworkManager.get_sender_status()`` 的 packets 累计值（
    `network_manager.py:2356` 一带已在数的同一批计数器），本类只做差分：
    ``hz = Δpackets / Δt``。首次采样没有基线 → 速率为 None（UI 显示"—"），
    避免把"从零开始的累计值"误报成瞬时风暴。
    """

    def __init__(self, min_interval: float = MIN_SAMPLE_INTERVAL) -> None:
        self._min_interval = max(0.05, float(min_interval))
        self._last_t: Optional[float] = None
        self._last_packets: int = 0
        self._last_errors: int = 0
        self.rate_hz: Optional[float] = None
        self.error_hz: float = 0.0

    def sample(self, packets: int, errors: int = 0,
               now: Optional[float] = None) -> Optional[float]:
        """喂入累计值；未到节流窗口返回上次速率（不重算）。首采样返回 None。"""
        packets = max(0, int(packets or 0))
        errors = max(0, int(errors or 0))
        t = time.monotonic() if now is None else float(now)
        if self._last_t is None:
            self._last_t = t
            self._last_packets = packets
            self._last_errors = errors
            return None
        dt = t - self._last_t
        if dt < self._min_interval:
            return self.rate_hz
        d_packets = packets - self._last_packets
        d_errors = errors - self._last_errors
        self._last_t = t
        self._last_packets = packets
        self._last_errors = errors
        if d_packets < 0 or d_errors < 0:      # 计数器重置（重启会话）→ 从零起算
            self.rate_hz = None
            self.error_hz = 0.0
            return None
        self.rate_hz = d_packets / dt if dt > 0.0 else None
        self.error_hz = d_errors / dt if dt > 0.0 else 0.0
        return self.rate_hz

    def reset(self) -> None:
        """会话重启（stop/start）：清基线，下一次采样重新建立。"""
        self._last_t = None
        self._last_packets = 0
        self._last_errors = 0
        self.rate_hz = None
        self.error_hz = 0.0


def _config_out_ip(cfg) -> str:
    """配置输出 IP 归一化（空/0.0.0.0 → 全子网广播）。"""
    out_ip = str(getattr(cfg, "output_ip", "") or "").strip()
    if out_ip in ("", "0.0.0.0"):
        return "255.255.255.255"
    return out_ip


def _targets_of(cfg, ifaces: Optional[Sequence] = None, universe: int = 1) -> List[str]:
    """按配置推导实际发送目标 IP 列表（与 network_manager 出口同口径）。"""
    actual = _running_targets(cfg, universe)
    if actual is not None:
        return actual
    mode = str(getattr(cfg, "output_mode", "broadcast") or "broadcast").lower()
    if "sacn" in str(getattr(cfg, "protocol", "")).lower() and mode != "unicast":
        from .sacn_node import _universe_to_multicast
        return [_universe_to_multicast(universe)]
    out_ip = _config_out_ip(cfg)
    return [out_ip] if mode != "broadcast" or out_ip == "127.0.0.1" else _broadcast_targets(out_ip, ifaces)


def _running_targets(cfg, universe: int):
    actual = getattr(cfg, "_actual_targets_by_universe", None)
    if actual is not None:
        return list(actual.get(universe, ()))
    actual = getattr(cfg, "_actual_targets", None)
    return list(actual) if actual is not None else None


def _broadcast_targets(out_ip: str, ifaces: Optional[Sequence]):
    from .nic_broadcast import directed_broadcast_targets
    targets = [out_ip]
    for t in directed_broadcast_targets(ifaces):
        if t not in targets:
            targets.append(t)
    if "255.255.255.255" not in targets:
        targets.append("255.255.255.255")
    return targets


def binding_table_rows(cfg, ifaces: Optional[Sequence] = None,
                       universes: Sequence[int] = ()) -> List[dict]:
    """N5 每域绑定表：每域 → 实际目标 IP / 模式 / 是否落在本机网段。

    `ifaces=None` 用 ifaddr 真掩码枚举（与发送链同一函数）；传入
    ``[(ip, prefix)]`` 可离线测试。返回行字典列表（UI 直接铺表）：
    ``{universe, mode, targets, local_ok, note}``。
    """
    from .nic_broadcast import enumerate_ipv4_interfaces, local_ipv4_addresses
    real = list(ifaces) if ifaces is not None else enumerate_ipv4_interfaces()
    if not real:
        real = [(ip, 24) for ip in local_ipv4_addresses()]
    nets = _local_networks(real)
    mode = str(getattr(cfg, "output_mode", "broadcast") or "broadcast").lower()
    unis = [int(u) for u in (universes or ())]
    if not unis:
        unis = [int(getattr(cfg, "output_universe", 1) or 1)]
    rows: List[dict] = []
    for uni in unis:
        targets = _targets_of(cfg, real, uni)
        ok, note = _targets_local_ok(targets, nets)
        rows.append({
            "universe": uni,
            "mode": mode,
            "targets": list(targets),
            "local_ok": ok,
            "note": note,
        })
    return rows


def _local_networks(ifaces: Sequence) -> List:
    """本机活动网段列表（IPv4Network；非法条目跳过）。"""
    nets = []
    for entry in ifaces or ():
        try:
            ip, prefix = (entry if not isinstance(entry, str) else (entry, 24))
            nets.append(ipaddress.ip_network(f"{ip}/{int(prefix)}", strict=False))
        except (ValueError, TypeError):
            continue
    return nets


def classify_target(target: str, nets: Sequence) -> str:
    """单个目标 IP 的对账分类：``"global"|"local"|"multicast"|"offnet"|"bad"``。

    黑洞检测按**逐目标**判定（不能"有一个落在本机网段"就整体放行——混合
    目标里那个跨网段的仍在打水漂）。
    """
    t = str(target or "").strip()
    if t == "255.255.255.255":
        return "global"
    try:
        addr = ipaddress.ip_address(t)
    except ValueError:
        return "bad"
    return _classify_addr(addr, nets)


def _classify_addr(addr, nets: Sequence) -> str:
    """已解析地址的分类（回环/多播/网段/网段外）。"""
    if addr.is_loopback:
        return "local"
    if addr.is_multicast:
        return "multicast"
    if not nets:
        return "local"          # 本机网卡未枚举到 → 无法对账，不误报
    if any(addr in net for net in nets):
        return "local"
    return "offnet"


def _offnet_targets(targets: Sequence[str], nets: Sequence) -> List[str]:
    """目标集合里落在全部本机网段之外的那些（即"打水漂"的目标）。"""
    return [t for t in targets if classify_target(t, nets) == "offnet"]


def _targets_local_ok(targets: Sequence[str], nets: Sequence) -> Tuple[bool, str]:
    """目标集合是否至少有一个可达（全子网广播/多播/本机网段都算可达）。"""
    if not nets:
        return True, "本机网卡未枚举到（无法对账）"
    kinds = [classify_target(t, nets) for t in targets]
    if "global" in kinds:
        return True, "全子网广播"
    if "local" in kinds:
        return True, "落在本机网段"
    if "multicast" in kinds:
        return True, "多播目标（接收端需加入对应宇宙的多播组；尚未确认接收）"
    return False, "目标在本机网段外，需确认路由；尚未验证接收"


def _is_full_broadcast(cfg) -> bool:
    """配置是否为"全子网广播"（无法用发现表对账，黑洞检测不判）。"""
    if "sacn" in str(getattr(cfg, "protocol", "")).lower():
        return False
    mode = str(getattr(cfg, "output_mode", "broadcast") or "broadcast").lower()
    out_ip = str(getattr(cfg, "output_ip", "") or "").strip()
    return mode == "broadcast" and out_ip in ("", "255.255.255.255", "0.0.0.0")


def _unicast_discovery_count(discovered) -> int:
    """ArtPoll 发现表里"在线且有 IP"的节点数（stuck 行不计）。"""
    return len([d for d in (discovered or ())
                if d.get("ip") and not d.get("stuck")])


def blackhole_report(cfg, ifaces: Optional[Sequence] = None,
                     discovered: Optional[Sequence[dict]] = None) -> dict:
    """N4 黑洞检测：返回 ``{blackhole: bool, reason: str, level: str,
    targets: [...], offnet: [...]}``。

    只在**定向/单播**目标下判定（全子网广播视为"无法对账"，不给假告警）：
      · 存在目标不在任何本机网段 → level="error"（跨网段直发 = 发了没人收）；
      · 单播目标 + ArtPoll 发现表为空 → level="warn"（目标未响应发现）。
    """
    if _is_full_broadcast(cfg):
        return {"blackhole": False, "level": "info", "offnet": [],
                "reason": "全子网广播模式：无法用发现表对账（不判黑洞）"}
    ifaces_list = list(ifaces) if ifaces is not None else _real_ifaces()
    rows = binding_table_rows(cfg, ifaces_list, getattr(cfg, "output_universes", ()))
    targets = list(dict.fromkeys(t for row in rows for t in row["targets"]))
    nets = _local_networks(ifaces_list)
    offnet = _offnet_targets(targets, nets)
    if offnet:
        return {"blackhole": False, "level": "warn", "offnet": offnet,
                "reason": "目标在本机网段外，请确认路由（不能据此判定不可达）："
                          + ", ".join(offnet[:3]),
                "targets": list(targets)}
    mode = str(getattr(cfg, "output_mode", "broadcast") or "broadcast").lower()
    if "sacn" in str(getattr(cfg, "protocol", "")).lower():
        return {"blackhole": False, "level": "info", "offnet": [],
                "reason": "sACN 目标按发送模式配置；ArtPoll 发现不能确认 sACN 接收。",
                "targets": list(targets)}
    if mode == "unicast":
        return _unicast_report(targets, discovered)
    _, note = _targets_local_ok(targets, nets)
    return {"blackhole": False, "level": "info", "offnet": [],
            "reason": f"广播目标与会话对账通过（{note}）",
            "targets": list(targets)}


def _unicast_report(targets: Sequence[str], discovered) -> dict:
    """单播目标对账：发现表为空 → warn（发了没人收的软黑洞）。"""
    count = _unicast_discovery_count([d for d in (discovered or ())
                                      if d.get("ip") in targets])
    if count == 0:
        return {"blackhole": False, "level": "warn", "offnet": [],
                "reason": "此单播目标未响应 ArtPoll；部分接收软件不回发现，尚未确认实际接收。",
                "targets": list(targets)}
    return {"blackhole": False, "level": "info", "offnet": [],
            "reason": f"此单播目标已有 {count} 个节点响应发现（发现在线不等于接收确认）",
            "targets": list(targets)}


def _real_ifaces() -> List:
    from .nic_broadcast import enumerate_ipv4_interfaces
    return enumerate_ipv4_interfaces()


__all__ = ["SenderRateSampler", "format_rate_line", "binding_table_rows",
           "blackhole_report", "classify_target", "MIN_SAMPLE_INTERVAL",
           "STALL_HZ"]
