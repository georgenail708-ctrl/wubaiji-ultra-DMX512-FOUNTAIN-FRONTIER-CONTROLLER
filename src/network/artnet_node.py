"""
Qascade V1.0 — Art-Net Node
Art-Net 收发节点，基于UDP
★ v4: 标准阻塞模式，简单稳定
★ v5 (R178): ArtPoll 发现（parse/reply/主动轮询 socket 工厂）+
  逐网卡定向广播纯函数。全模块唯一裸 socket 出口（架构纪律）。
★ v6 (R183): 定向广播改 ifaddr 真掩码（/24 假定退役）；收包侧
  SO_RCVBUF 放大 + 非阻塞排空循环 + ArtPollReply 端口自适应。
  NIC 枚举/广播推导/收包助手拆至 nic_broadcast（此处 re-export 兼容）。
"""

from __future__ import annotations
import logging
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from .nic_broadcast import (        # R183 拆分：真源在 nic_broadcast；
    amplify_rcvbuf,                 # re-export 保持旧 import 路径可用。
    broadcast_addr_of,
    directed_broadcast_targets,
    drain_socket_wake,
    enumerate_ipv4_interfaces,
    local_ipv4_addresses,
)
from .output_sink import IOutputSink


ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
OPCODE_DMX       = 0x5000
OPCODE_POLL      = 0x2000
OPCODE_REPLY     = 0x2100
OPCODE_TIMECODE  = 0x9700

log = logging.getLogger(__name__)


class ReceiverBindError(OSError):
    """接收端口绑定失败（R182 P1-5 专用异常）。

    定位于本模块：artnet_node 是全 network 包的裸 socket 工具与协议出口
    （见 :func:`make_discovery_socket` 的架构纪律说明），sACN 节点复用本类。

    修前两种坏行为：
      - ``SACNReceiver.start`` 裸 ``bind`` → OSError 冒到 NetworkManager.start
        → 用户看到全局未捕获异常框；
      - ``ArtNetReceiver.start`` 只 ``log.warning`` + return → UI 显示"运行中"
        但 6454 根本没绑上，静默丢全部输入。
    统一改为上抛本异常，由 NetworkManager 捕获为可展示的 ``last_bind_error``。
    """

    def __init__(self, port: int, cause: BaseException | None = None):
        self.port = int(port)
        detail = f"（{cause}）" if cause is not None else ""
        super().__init__(f"UDP {self.port} 绑定失败{detail}")


def _build_artdmx(universe: int, data: bytes | np.ndarray) -> bytes:
    """构建 ArtDMX 数据包"""
    if isinstance(data, np.ndarray):
        data = data.tobytes()
    length = len(data)
    pkt = (
        ARTNET_HEADER
        + struct.pack("<H", OPCODE_DMX)
        + struct.pack(">H", 14)
        + b"\x00"
        + b"\x00"
        + struct.pack("<H", universe)
        + struct.pack(">H", length)
        + data
    )
    return pkt


def _parse_artdmx(pkt: bytes) -> Optional[tuple[int, np.ndarray]]:
    """解析 ArtDMX 数据包，返回 (universe, data) 或 None"""
    if len(pkt) < 18:
        return None
    if pkt[:8] != ARTNET_HEADER:
        return None
    opcode = struct.unpack("<H", pkt[8:10])[0]
    if opcode != OPCODE_DMX:
        return None
    universe = struct.unpack("<H", pkt[14:16])[0]
    length   = struct.unpack(">H", pkt[16:18])[0]
    if length < 2 or length > 512:
        return None
    if len(pkt) < 18 + length:
        return None
    raw = pkt[18: 18 + length]
    data = np.frombuffer(raw, dtype=np.uint8).copy()
    if len(data) < 512:
        data = np.pad(data, (0, 512 - len(data)))
    return universe, data[:512]


def _parse_arttimecode(pkt: bytes):
    """Art-Net TimeCode 数据包 (OpCode 0x9700) 解析。

    19 字节包结构（Art-Net 规范）：
      [0-7]   Art-Net header
      [8-9]   OpCode (0x9700)
      [10]    ProtVerHi
      [11]    ProtVerLo
      [12]    Filler1
      [13]    Filler2
      [14]    Frames
      [15]    Seconds
      [16]    Minutes
      [17]    Hours
      [18]    Type (0=24fps, 1=25fps, 2=29.97fpsDF, 3=30fps)

    Returns (hours, minutes, seconds, frames, tc_type) or None.
    """
    if len(pkt) < 19:
        return None
    if pkt[:8] != ARTNET_HEADER:
        return None
    opcode = struct.unpack("<H", pkt[8:10])[0]
    if opcode != OPCODE_TIMECODE:
        return None
    frames = pkt[14]
    seconds = pkt[15]
    minutes = pkt[16]
    hours = pkt[17]
    tc_type = pkt[18]
    from ..core.timecode_decode import is_valid_artnet_timecode
    if not is_valid_artnet_timecode(hours, minutes, seconds, frames, tc_type):
        return None
    return hours, minutes, seconds, frames, tc_type


# ─────────────────────────────────────────────────────────────────────────────
#  R178: ArtPoll 发现 / 定向广播（协议层纯函数 + socket 工厂）
# ─────────────────────────────────────────────────────────────────────────────

def _parse_artpoll(pkt: bytes) -> Optional[dict]:
    """解析 ArtPoll (OpCode 0x2000, 14B)。

    校验 Art-Net 头 / Opcode / 协议版本 (>=14) / 最小长度，通过返回
    ``{"talk_to_me": bit0..1, "priority": int}``，否则 None。
    """
    if len(pkt) < 14 or pkt[:8] != ARTNET_HEADER:
        return None
    if struct.unpack("<H", pkt[8:10])[0] != OPCODE_POLL:
        return None
    if struct.unpack(">H", pkt[10:12])[0] < 14:
        return None
    return {"talk_to_me": pkt[12] & 0x03, "priority": pkt[13]}


def _build_artpoll(talk_to_me: int = 0, priority: int = 0) -> bytes:
    """构建 14 字节 ArtPoll（主动发现用）。"""
    return (
        ARTNET_HEADER
        + struct.pack("<H", OPCODE_POLL)
        + struct.pack(">H", 14)
        + bytes([talk_to_me & 0x03, priority & 0xFF])
    )


def _fix_str_field(text: str, size: int) -> bytes:
    """字符串 → 定长 NUL 结尾字段（超长截断，不足补 0）。"""
    return text.encode("utf-8", "replace")[: size - 1].ljust(size, b"\x00")


def _build_artpollreply(ip: str, port: int = ARTNET_PORT,
                        short_name: str = "Qascade DMX",
                        long_name: str = "Qascade V1.0 Art-Net Node") -> bytes:
    """构建标准 239B ArtPollReply (OpCode 0x2100)。

    最小合法布局：ID/Op/Ver/Ip:4B/Port/VersInfo/Net/Sub/OEM/UBEA/Status1/
    ShortName(18)/LongName(64)/NodeReport(64)/NumPortsHi/Lo(0)/PortTypes(4)/
    GoodInput(4)/GoodOutput(4)/SwIn(4)/SwOut(4)/SwVideo/SwMacro/SwRemote/
    Spare(4)/Style=0x00，尾部（Status2/MAC/Bind 区）补零到 239B。
    """
    try:
        parts = [int(x) for x in str(ip).split(".")]
        if len(parts) != 4 or any(not (0 <= p <= 255) for p in parts):
            raise ValueError(ip)
        octets = bytes(parts)
    except (ValueError, TypeError):
        octets = bytes((127, 0, 0, 1))
    pkt = bytearray()
    pkt += ARTNET_HEADER
    pkt += struct.pack("<H", OPCODE_REPLY)
    pkt += struct.pack(">H", 14)                    # ProtVer
    pkt += octets                                   # 本机 IP
    pkt += struct.pack(">H", int(port) & 0xFFFF)    # 6454
    pkt += struct.pack(">H", 1)                     # VersInfo（固件版）
    pkt += bytes((0x00, 0x00))                      # NetSwitch / SubSwitch
    pkt += struct.pack(">H", 0xFFFF)                # OEM
    pkt += bytes((0x00, 0x00))                      # UBEA / Status1
    pkt += _fix_str_field(short_name, 18)
    pkt += _fix_str_field(long_name, 64)
    pkt += _fix_str_field("#0001 [0000] Qascade Art-Net ready", 64)
    pkt += bytes((0x00, 0x00))                      # NumPortsHi/Lo = 0
    pkt += bytes(4 * 4)                             # PortTypes/GoodIn/GoodOut/SwIn
    pkt += bytes(4)                                 # SwOut
    pkt += bytes((0x00, 0x00, 0x00))                # SwVideo / SwMacro / SwRemote
    pkt += bytes(4)                                 # Spare
    pkt += bytes((0x00,))                           # Style = 0x00（节点）
    pkt += bytes(239 - len(pkt))                    # Status2/MAC/Bind 区补齐
    return bytes(pkt)


def _parse_artpollreply(pkt: bytes) -> Optional[dict]:
    """解析 ArtPollReply (OpCode 0x2100)，返回 ip/port/short_name/long_name。"""
    if len(pkt) < 44 or pkt[:8] != ARTNET_HEADER:
        return None
    if struct.unpack("<H", pkt[8:10])[0] != OPCODE_REPLY:
        return None
    ip = ".".join(str(b) for b in pkt[12:16])
    port = struct.unpack(">H", pkt[16:18])[0]
    short = pkt[26:44].split(b"\x00")[0].decode("utf-8", "replace")
    long_name = ""
    if len(pkt) >= 108:
        long_name = pkt[44:108].split(b"\x00")[0].decode("utf-8", "replace")
    return {"ip": ip, "port": port, "short_name": short, "long_name": long_name}


def make_discovery_socket(bind_ip: str = "0.0.0.0") -> socket.socket:
    """ArtPoll 主动发现用 UDP socket（SO_BROADCAST|SO_REUSEADDR + 0.5s 超时）。

    绑定临时端口（0）；收发后由调用方 close。全仓裸 socket 纪律出口之一。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    s.bind((bind_ip or "0.0.0.0", 0))
    return s


class ArtNetReceiver(QObject):
    """Art-Net 接收器"""
    frame_received = pyqtSignal(int, object)
    timecode_received = pyqtSignal(int, int, int, int, int)  # hours, minutes, seconds, frames, tc_type
    poll_received = pyqtSignal(dict)  # ArtPoll: {ip, port, talk_to_me, priority}
    #: R182 P2：ArtPollReply（其他节点/控台对**它们**收到的 ArtPoll 的应答，
    #: 广播到 6454）→ 发现表。修前只在主动扫描窗口内读，被动一无所获。
    reply_received = pyqtSignal(dict)  # {ip, port, short_name, long_name}

    def __init__(self, bind_ip: str = "0.0.0.0", parent=None):
        super().__init__(parent)
        self.bind_ip = bind_ip
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reply_ip: Optional[str] = None
        self._timeout: float = 0.5            # 排空循环后恢复的阻塞超时
        self._dmx_enabled = True             # NM只需TimeCode时跳过自广播DMX解码。

    def start(self):
        """启动接收循环；6454 被占用时抛 :class:`ReceiverBindError`（R182 P1-5）。

        修前只 ``log.warning`` + return：调用方无从感知 → UI 显示"运行中"却
        整段收不到外部 DMX（静默失败）。
        """
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # R183：接收缓冲放大（1MB，失败静默）——包风暴下给排空循环更多窗口。
        amplify_rcvbuf(self._sock)
        self._sock.settimeout(self._timeout)
        try:
            self._sock.bind((self.bind_ip, ARTNET_PORT))
        except OSError as e:
            log.warning("[ArtNet] Bind error: %s", e)
            try:
                self._sock.close()
            except Exception:
                log.debug("[ArtNet] bind 失败后关闭 socket 失败", exc_info=True)
            self._sock = None
            raise ReceiverBindError(ARTNET_PORT, e) from e
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        log.info("[ArtNet] Receiver started on %s:%s", self.bind_ip, ARTNET_PORT)

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=2)
        log.info("[ArtNet] Receiver stopped")

    def _recv_loop(self):
        while self._running:
            try:
                pkt, addr = self._sock.recvfrom(600)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.debug("[ArtNet] Recv error: %s", e)
                continue
            try:
                self._handle_packet(pkt, addr)
                # R183：非阻塞排空——一次 wake 收到空为止，包风暴不再积压。
                drain_socket_wake(self._sock, self._handle_packet, self._timeout, 600)
            except Exception as e:
                if self._running:
                    log.debug("[ArtNet] Recv error: %s", e)

    def _handle_packet(self, pkt: bytes, addr) -> bool:
        """分发一包：ArtPoll → 信号+单播 Reply；ArtPollReply → 信号；ArtDMX/TC → 信号。"""
        if (not self._dmx_enabled and len(pkt) >= 10
                and pkt[:8] == ARTNET_HEADER and pkt[8:10] == b'\x00\x50'):
            return True
        poll = _parse_artpoll(pkt)
        if poll is not None:
            poll["ip"] = addr[0]
            poll["port"] = addr[1]
            self.poll_received.emit(poll)
            self._reply_poll(addr)
            return True
        reply = _parse_artpollreply(pkt)
        if reply is not None:
            # R182 P2：广播到 6454 的 ArtPollReply 也进发现表（此前只在主动
            # 扫描窗口内收 → 别的控台在扫描时我们完全不知道网络里有节点）。
            # R183 端口自适应：包内 port 字段无效(0)时回退实际 UDP 源端口，
            # 非标实现（从临时端口回包）也能被发现表正确登记。
            reply["ip"] = addr[0]
            if not reply.get("port"):
                reply["port"] = addr[1]
            self.reply_received.emit(reply)
            return True
        result = _parse_artdmx(pkt)
        if result:
            universe, data = result
            self.frame_received.emit(universe, data)
            return True
        tc = _parse_arttimecode(pkt)
        if tc:
            hours, minutes, seconds, frames, tc_type = tc
            self.timecode_received.emit(hours, minutes, seconds, frames, tc_type)
            return True
        return False

    def _reply_poll(self, addr) -> None:
        """ArtPoll → 单播 ArtPollReply 回轮询方（每次至少一答，规范行为）。"""
        if self._reply_ip is None:
            ips = [ip for ip in local_ipv4_addresses() if not ip.startswith("127.")]
            self._reply_ip = ips[0] if ips else "127.0.0.1"
        try:
            self._sock.sendto(_build_artpollreply(self._reply_ip), (addr[0], addr[1]))
        except Exception as e:
            log.debug("[ArtNet] ArtPollReply send failed: %s", e)


def _artdmx_header(universe: int, length: int, sequence: int = 0) -> bytes:
    """ArtDMX 18 字节头（零分配路径用）。sequence=包头偏移 12 的序列号
    （A10 P2-6 逐域 1..255；默认 0 与旧版逐字节一致，逐帧发送时覆写）。"""
    return (
        ARTNET_HEADER
        + struct.pack("<H", OPCODE_DMX)
        + struct.pack(">H", 14)
        + bytes((sequence & 0xFF, 0x00))
        + struct.pack("<H", universe)
        + struct.pack(">H", length)
    )


class ArtNetSender(QObject):
    """Art-Net 发送器（标准阻塞模式）

    Implements ``IOutputSink`` via virtual registration (see end of file).

    ★ v3 (2026-08-04) 发送链优化：
      - 零分配打包：逐域预分配 530B bytearray（18B 头预计算 + 512B 数据区），
        每帧仅一次 512B 原地写入 + sendto，省掉每次 _build_artdmx 的
        拼接/struct.pack/tobytes 分配（73 域/tick 少 ~73 次分配）；
      - 发送缓冲复用：socket 单实例常驻（本就复用）；
      - SO_SNDBUF 放大（512KB）：接收端背压时 UDP 发送缓冲排队，避免
        sendto 偶发阻塞拖慢发送线程（实测回环/广播突发场景）；
      - _send_lock 串行化缓冲写入 + 发送（无竞争 ~0.1µs；防 UI 线程直发
        与 router 线程并发写同一缓冲导致字节撕裂）。
    """

    def __init__(self, target_ip: str = "255.255.255.255", port: int = ARTNET_PORT,
                 extra_targets: tuple = ()):
        super().__init__()
        self.target_ip = target_ip
        self.port = port
        # 多目标发送：主目标 + 附加目标（如 255.255.255.255 广播兜底）。
        # 单播主目标时自动附加广播，实现"填一个 IP 同时覆盖本机/同子网/跨网段"。
        self._targets: list[tuple[str, int]] = [(target_ip, port)]
        for t in extra_targets:
            t = str(t).strip()
            if t and t != target_ip and (t, port) not in self._targets:
                self._targets.append((t, port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512 * 1024)
        except OSError:
            pass
        self._packets: int = 0
        self._errors: int = 0
        # 零分配缓冲池: universe -> (bytearray(18+512), header_bytes)
        # 惰性创建; 键数上限 = universe 数(ArtNet ≤ 512)。
        self._pkt_bufs: Dict[int, bytearray] = {}
        # ★ A10 P2-6：逐域 ArtDMX 序列号（1..255 回绕；0=规范保留不使用）。
        self._seqs: Dict[int, int] = {}
        self._send_lock = threading.Lock()

    # ── IOutputSink ────────────────────────────────────────────────

    def _pkt_buffer(self, universe: int, length: int = 512) -> bytearray:
        """取该 universe 的预分配打包缓冲（头已预计算，数据区可原地覆写）。"""
        buf = self._pkt_bufs.get(universe)
        if buf is None:
            buf = bytearray(_artdmx_header(universe, length) + bytes(length))
            self._pkt_bufs[universe] = buf
        return buf

    def _next_seq(self, universe: int) -> int:
        """该域下一个 ArtDMX 序列号：1..255 自增回绕（packetizer
        :155-158 等价算术式）；0 永不产生（规范保留值）。"""
        self._seqs[universe] = nxt = self._seqs.get(universe, 0) % 255 + 1
        return nxt

    def _sendto_all(self, pkt, universe: int) -> None:
        """逐目标发送（sendto 异常仅计 _errors++，不中断其余目标）。"""
        for tgt in self._targets:
            try:
                self._sock.sendto(pkt, tgt)
            except Exception:
                self._errors += 1
                log.debug("ArtNet: send failed (target=%s, universe=%s)",
                          tgt[0], universe)

    def _send_packet(self, universe: int, data: np.ndarray | bytes) -> None:
        """打包并发送到全部目标。512B 连续 uint8 数组走零分配快路径。"""
        if isinstance(data, np.ndarray):
            arr = np.asarray(data, dtype=np.uint8)
            if arr.ndim == 1 and arr.size == 512:
                buf = self._pkt_buffer(int(universe))
                with self._send_lock:
                    buf[18:] = memoryview(arr)
                    buf[12] = self._next_seq(int(universe))   # A10 P2-6：偏移 12
                    self._sendto_all(buf, universe)
                return
        # 非 512 整帧 / 非常规 dtype: 回退原打包路径（语义不变）
        pkt = bytearray(_build_artdmx(universe, data))
        with self._send_lock:
            pkt[12] = self._next_seq(int(universe))   # A10 P2-6：同域 seq 流连续
            self._sendto_all(pkt, universe)

    def send_dmx(self, universe: int, data: np.ndarray) -> None:
        """See ``IOutputSink.send_dmx``."""
        self._packets += 1
        self._send_packet(universe, data)

    def get_status(self) -> Dict[str, Any]:
        """See ``IOutputSink.get_status``."""
        return {
            "protocol": "ArtNet",
            "target": self.target_ip,
            "universe": 0,       # ArtNet is stateless — send_dmx carries universe
            "packets": self._packets,
            "errors": self._errors,
            "alive": self._sock is not None,
        }

    # ── Original API (kept for backward compatibility) ─────────────

    def send(self, universe: int, data: np.ndarray | bytes):
        self._send_packet(universe, data)

    def send_test(self, universe: int, value: int = 255):
        data = np.full(512, value, dtype=np.uint8)
        self.send(universe, data)

    def close(self):
        self._sock.close()


# Virtual registration avoids metaclass conflict between QObject and ABCMeta
IOutputSink.register(ArtNetSender)
