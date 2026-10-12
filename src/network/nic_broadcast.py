"""Qascade V1.0 — NIC 枚举与定向广播（R183，自 artnet_node 拆出）。

职责三件：
  - 枚举：ifaddr 真掩码（适配器 + IPv4 + prefixlen）；不可用退
    getaddrinfo+/24（R178 旧行为），绝不阻塞启动；
  - 推导：broadcast_addr_of / directed_broadcast_targets
    （OLA Interface.bcast_address 口径：真实子网广播，无 /24 假定）；
  - 收包行为助手：SO_RCVBUF 放大 + 非阻塞排空循环
    （ArtNetReceiver / SACNReceiver 共用）。

裸 socket 纪律不变：socket 的**创建**仍只发生在 artnet_node
（make_discovery_socket / 各收发器）；本模块只对传入 socket 调
setsockopt / settimeout / recvfrom，不创建、不绑定。

上游核验（ai-memory/_references/net-adoption.md §一）：
  - OLA ArtNetNode.cpp：ArtPoll/ArtDMX/Reply 全发 interface.bcast_address；
  - artnetplugin.cpp：QNetworkAddressEntry.prefixLength 全程保留；
  - 修前 /24 假定在 /16、/8、VPN 点对点网卡上算错定向广播 → 丢 universe。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Callable, List, Optional, Sequence, Tuple, Union

try:                                    # ifaddr 为可选依赖（venv 已装）：
    import ifaddr                       # 缺失/异常时枚举退路 getaddrinfo+/24，
except Exception:                       # 绝不阻塞启动（网络输出可用性优先）。
    ifaddr = None

log = logging.getLogger(__name__)

#: 旧签名（纯 str 列表）兼容口径：无前缀信息时的 /24 假定（R178 语义保留）。
_LEGACY_PREFIX = 24


def local_ipv4_addresses() -> List[str]:
    """枚举本机活动 IPv4（getaddrinfo 主机名解析；失败回退 127.0.0.1）。"""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return ["127.0.0.1"]
    ips: List[str] = []
    for info in infos:
        ip = str(info[4][0])
        if ip not in ips:
            ips.append(ip)
    return ips or ["127.0.0.1"]


def _adapter_ipv4_entries(adapter) -> List[Tuple[str, int]]:
    """单适配器 → IPv4 ``(ip, prefixlen)`` 条目（IPv6 条目 ip 为 tuple，跳过）。"""
    out: List[Tuple[str, int]] = []
    for aip in getattr(adapter, "ips", None) or ():
        ip = getattr(aip, "ip", None)
        prefix = getattr(aip, "network_prefix", None)
        if not (isinstance(ip, str) and ip) or prefix is None:
            continue
        try:
            out.append((ip, int(prefix)))
        except (TypeError, ValueError):
            continue
    return out


def enumerate_ipv4_interfaces() -> List[Tuple[str, int]]:
    """ifaddr 枚举本机 IPv4 接口 ``[(ip, prefixlen), ...]``（R183 真掩码）。

    含 loopback（调用方按需过滤）；ifaddr 缺失/异常 → 返回 []
    （调用方走 getaddrinfo+/24 退路，不阻塞启动）。
    """
    if ifaddr is None:
        return []
    try:
        adapters = ifaddr.get_adapters()
    except Exception:
        log.debug("ifaddr 网卡枚举失败", exc_info=True)
        return []
    out: List[Tuple[str, int]] = []
    for adapter in adapters:
        out.extend(_adapter_ipv4_entries(adapter))
    return out


def broadcast_addr_of(ip: str, prefixlen: int) -> Optional[str]:
    """(ip, prefixlen) → 该子网定向广播地址；非法组合返回 None（纯函数）。

    OLA ``Interface.bcast_address`` 的等价算术（真实掩码，无 /24 假定）：
    /8→x.255.255.255、/16→x.x.255.255、/24→x.x.x.255；
    /0→255.255.255.255；/32 点对点数学广播=自身（枚举层跳过——发自身
    无意义，跨网段兜底仍由 255.255.255.255 承担）。
    """
    try:
        prefix = int(prefixlen)
        if not (0 <= prefix <= 32):
            return None
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        return str(net.broadcast_address)
    except (ValueError, TypeError):
        return None


def _entry_broadcast(entry: Union[str, Tuple[str, int]],
                     exclude_loopback: bool) -> Optional[str]:
    """单条目 → 定向广播地址；回环、/32 点对点、非法条目返回 None。"""
    if isinstance(entry, str):                    # 旧签名（离线测试）：/24 假定
        ip, prefix = entry, _LEGACY_PREFIX
    else:
        ip, prefix = entry[0], entry[1]
    ip = str(ip)
    if exclude_loopback and ip.startswith("127."):
        return None
    try:
        prefix = int(prefix)
    except (TypeError, ValueError):
        return None
    if prefix >= 32:                              # /32 点对点：广播=自身，无意义
        return None
    return broadcast_addr_of(ip, prefix)


def directed_broadcast_targets(
        ifaces: Optional[Sequence[Union[str, Tuple[str, int]]]] = None,
        exclude_loopback: bool = True) -> List[str]:
    """逐网卡定向广播地址（R183：ifaddr 真掩码；OLA bcast_address 口径）。

    - ``ifaces=None``：ifaddr 真掩码枚举；ifaddr 不可用/为空退
      ``(getaddrinfo IP, /24)`` 旧路（退路只影响掩码精度，不阻塞）；
    - ``ifaces=[(ip, prefixlen), ...]``：按传入前缀精确推导（离线可测）；
    - ``ifaces=["ip", ...]``（旧签名兼容）：按 /24 假定推导（既有测试语义）；
    - 127.0.0.0/8 不产生广播（纯同机场景不打扰网络）；/32 点对点跳过；
    - 同广播地址去重；非法条目跳过。
    """
    if ifaces is None:
        real = enumerate_ipv4_interfaces()
        ifaces = real if real else local_ipv4_addresses()
    targets: List[str] = []
    for entry in ifaces:
        bcast = _entry_broadcast(entry, exclude_loopback)
        if bcast is not None and bcast not in targets:
            targets.append(bcast)
    return targets


def amplify_rcvbuf(sock: socket.socket, size: int = 1024 * 1024) -> None:
    """接收缓冲放大（默认 1MB；R183 收包健壮）。

    包风暴场景内核缓冲溢出即静默丢包；放大后接收线程的排空循环有更长
    窗口。失败静默（OS 上限/权限差异不允许它阻塞收包路径）。
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(size))
    except OSError:
        log.debug("SO_RCVBUF 放大失败 (size=%s)", size, exc_info=True)


def drain_socket_wake(sock: socket.socket, on_packet: Callable[[bytes, tuple], None],
                      timeout: float, bufsize: int) -> None:
    """非阻塞排空：一次 wake 后把内核缓冲收空为止（readyRead 口径）。

    修前每 wake 只收一包、再等 0.5s 超时周期——包风暴下接收速率被钳在
    ~2 包/秒，积压溢出丢包。本函数收空（BlockingIOError）或出错即返回，
    并恢复调用方的阻塞超时语义。
    """
    try:
        sock.settimeout(0.0)                      # 非阻塞
    except OSError:
        return
    try:
        while True:
            try:
                pkt, addr = sock.recvfrom(bufsize)
            except OSError:                       # 收空 / socket 被关闭
                return
            on_packet(pkt, addr)
    finally:
        try:
            sock.settimeout(timeout)
        except OSError:
            pass
