"""
Qascade V1.0 — sACN (E1.31) Node
sACN 收发节点

★ v2 更新：
  - SACNReceiver 新增 wildcard=True 模式：
    监听所有 sACN 包（不限 universe），支持接收任意域数据。
    wildcard 模式下不加入多播组，依赖网络广播或单播接收。
  - SACNSender 保持单域绑定（由 NetworkManager 的发送器池管理多域）。
★ v3 (R183) 接收合并语义（对照 python-sacn ReceiverHandler，仅收包侧；
  发送路径字节序列不动）：
  - sequence 乱序/重传丢弃：diff ∈ (-20, 0] 判旧包；
  - priority 取高：低于现存胜者的包丢弃，胜者缺席/超时/≥时刷新；
  - 2.5s 无包过期删状态（StreamTerminated 位立即失效）。
"""

from __future__ import annotations
import logging
import socket
import struct
import threading
import time
import uuid
from typing import Any, Dict, Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from .artnet_node import ReceiverBindError
from .nic_broadcast import amplify_rcvbuf, drain_socket_wake
from .output_sink import IOutputSink


SACN_PORT = 5568
SACN_MULTICAST_BASE = "239.255.0."

#: E1.31 network data loss timeout（python-sacn receiver_handler.py:9 的 2500ms）：
#: 域内超过该秒数没有任何合法包 → 删该域 seq/priority 状态，迟到源可重新接管。
SACN_SOURCE_TIMEOUT_SEC = 2.5
#: sequence 合法性窗口（python-sacn is_legal_sequence）：
#: diff = seq - last，diff ∈ (-20, 0] → 旧包/重传，丢弃。
SACN_SEQ_STALE_WINDOW = 20

# sACN CID（本软件固定）
_SOURCE_CID = uuid.uuid4().bytes
_SOURCE_NAME = b"Qascade V1.0\x00" + b"\x00" * (64 - 13)

log = logging.getLogger(__name__)


def _universe_to_multicast(universe: int) -> str:
    """将 universe 编号转换为 sACN 多播地址（E1.31 规范）"""
    hi = (universe >> 8) & 0xFF
    lo = universe & 0xFF
    return f"239.255.{hi}.{lo}"


def _build_sacn(universe: int, data: bytes | np.ndarray, priority: int = 100, seq: int = 0) -> bytes:
    """构建 sACN E1.31 数据包"""
    if isinstance(data, np.ndarray):
        data = data.tobytes()
    length = len(data)

    # DMP Layer
    dmp_len = 11 + length
    dmp = struct.pack(">H", 0x70_00 | dmp_len)  # Flags & Length
    dmp += b"\x02"                               # Vector VECTOR_DMP_SET_PROP
    dmp += b"\xa1"                               # Address & Data Type
    dmp += struct.pack(">H", 0)                  # First Property Address
    dmp += struct.pack(">H", 1)                  # Address Increment
    dmp += struct.pack(">H", length + 1)         # Property count
    dmp += b"\x00"                               # DMX start code
    dmp += data

    # Framing Layer
    framing_len = 77 + len(dmp)
    framing = struct.pack(">H", 0x70_00 | framing_len)
    framing += b"\x00\x00\x00\x02"              # VECTOR_E131_DATA_PACKET
    framing += _SOURCE_NAME                      # 64 bytes source name
    framing += struct.pack(">B", priority)       # Priority
    framing += b"\x00\x00"                       # Synchronization Address
    framing += struct.pack(">B", seq & 0xFF)     # Sequence Number
    framing += b"\x00"                           # Options
    framing += struct.pack(">H", universe)       # Universe
    framing += dmp

    # ACN Root Layer
    preamble  = b"\x00\x10\x00\x00"             # Preamble size, postamble
    acn_pid   = b"ASC-E1.17\x00\x00\x00"        # ACN Packet Identifier (12 bytes)
    root_flags_len = struct.pack(">H", 0x70_00 | (len(framing) + 22))
    vector    = b"\x00\x00\x00\x04"             # VECTOR_ROOT_E131_DATA
    cid       = _SOURCE_CID

    root = preamble + acn_pid + root_flags_len + vector + cid + framing
    return root


def _parse_sacn(pkt: bytes) -> Optional[tuple[int, np.ndarray]]:
    """简单解析 sACN 包，返回 (universe, data512)"""
    try:
        if len(pkt) < 126:
            return None
        # ACN Root: preamble=4, acnpid=12, flags=2, vector=4, cid=16 → offset=38
        if pkt[4:16] != b"ASC-E1.17\x00\x00\x00":
            return None
        if pkt[18:22] != b"\x00\x00\x00\x04":
            return None
        # Framing: flags=2, vector=4, name=64, prio=1, sync=2, seq=1, opt=1,
        #           univ=2 → universe at 113:115 (38 + 2+4+64+1+2+1+1 = 113)
        if pkt[40:44] != b"\x00\x00\x00\x02":
            return None
        universe = struct.unpack(">H", pkt[113:115])[0]
        if not (1 <= universe <= 63999):
            return None
        # DMP starts at 115: flags=2, vector=1, addrtype=1, firstaddr=2, inc=2,
        # count=2 → start code at 125, data from 126
        if pkt[117] != 0x02:
            return None
        raw = pkt[126:]
        data = np.frombuffer(raw, dtype=np.uint8).copy()
        if len(data) < 512:
            data = np.pad(data, (0, 512 - len(data)))
        return universe, data[:512]
    except Exception:
        log.debug("sACN: failed to parse sACN packet")
        return None


def _sacn_header_fields(pkt: bytes) -> Optional[tuple]:
    """提取 sACN framing 层 ``(universe, sequence, priority, terminated)``。

    偏移与 ``_parse_sacn`` 同源（root 38 基准；对 python-sacn
    ``data_packet.make_data_packet`` 逐字段核验一致）：
    priority=108、sync=109:111、sequence=111、options=112（bit6=Stream
    Terminated）、universe=113:115。非 sACN 数据包返回 None。
    """
    try:
        if len(pkt) < 126:
            return None
        if pkt[4:16] != b"ASC-E1.17\x00\x00\x00":
            return None
        if pkt[18:22] != b"\x00\x00\x00\x04":
            return None
        if pkt[40:44] != b"\x00\x00\x00\x02":
            return None
        universe = struct.unpack(">H", pkt[113:115])[0]
        if not (1 <= universe <= 63999):
            return None
        return universe, pkt[111], pkt[108], bool(pkt[112] & 0x40)
    except Exception:
        return None


class SACNReceiver(QObject):
    """
    sACN 接收器。

    wildcard=True（默认）：接收所有 universe 的数据包，不加入多播组。
                          适用于单播环境或需要接收多个 universe 的场景。
    wildcard=False：加入指定 universe 的多播组，只接收该 universe 的多播包。
                   适用于只需要接收单个 universe 的多播场景。

    R183：收包带合并门（见 :meth:`_accept_packet`）——乱序/重传 sACN 包、
    低优先级包不再逐包透传；2.5s 静默的域自动复位。发送侧不受影响。
    """
    frame_received = pyqtSignal(int, object)   # universe, np.ndarray

    def __init__(self, universe: int = 1, multicast: bool = True,
                 bind_ip: str = "0.0.0.0", wildcard: bool = True, parent=None):
        super().__init__(parent)
        self.universe = universe
        self.multicast = multicast
        self.bind_ip = bind_ip
        self.wildcard = wildcard   # ★ True = 接收所有 universe
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._timeout: float = 0.5            # 排空循环后恢复的阻塞超时
        # R183 接收合并状态（per-universe 键，python-sacn ReceiverHandler 同款
        # 简化——库亦按 universe 而非 per-CID）：仅接收线程读写，无需加锁。
        self._last_seq: Dict[int, int] = {}        # universe -> 上个 sequence
        self._prio: Dict[int, tuple] = {}          # universe -> (priority, 刷新时刻)
        self._last_seen: Dict[int, float] = {}     # universe -> 最后合法包时刻

    def start(self):
        """启动接收循环；端口被占用时抛 :class:`ReceiverBindError`（R182 P1-5）。

        修前 ``bind`` 裸调用：OSError 从 start() 冒到 NetworkManager.start →
        UI 全局异常框，且已被 close 但未清理的 socket 残留。
        """
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # R183：接收缓冲放大（1MB，失败静默）——多域并发时给排空循环更多窗口。
        amplify_rcvbuf(self._sock)
        self._sock.settimeout(self._timeout)
        try:
            self._sock.bind(("", SACN_PORT))
        except OSError as e:
            log.warning("[sACN] Bind error: %s", e)
            try:
                self._sock.close()
            except Exception:
                log.debug("[sACN] bind 失败后关闭 socket 失败", exc_info=True)
            self._sock = None
            raise ReceiverBindError(SACN_PORT, e) from e

        if not self.wildcard and self.multicast:
            # 非通配模式：加入指定 universe 的多播组
            mcast = _universe_to_multicast(self.universe)
            mreq = struct.pack("4s4s", socket.inet_aton(mcast),
                               socket.inet_aton(self.bind_ip))
            try:
                self._sock.setsockopt(socket.IPPROTO_IP,
                                      socket.IP_ADD_MEMBERSHIP, mreq)
                log.info("[sACN] Joined multicast group %s for universe %s", mcast, self.universe)
            except Exception as e:
                log.warning("[sACN] Multicast join error: %s", e)
        else:
            # 通配模式：不加入特定多播组，接收所有到达的 sACN 包
            log.info("[sACN] Wildcard mode: receiving all universes")

        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        mode = "wildcard" if self.wildcard else f"universe {self.universe}"
        log.info("[sACN] Receiver started (%s)", mode)

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=2)
        log.info("[sACN] Receiver stopped")

    def _recv_loop(self):
        while self._running:
            try:
                pkt, _ = self._sock.recvfrom(1144)
            except socket.timeout:
                # R183：空闲 wake 顺带做 2.5s 过期清扫（不新增线程/定时器；
                # 持续流量下该分支少跑，但活跃域本就不过期，语义无差）。
                self._sweep_expired_sources()
                continue
            except Exception as e:
                if self._running:
                    log.debug("[sACN] Recv error: %s", e)
                continue
            try:
                self._dispatch_packet(pkt)
                # R183：非阻塞排空——一次 wake 收到空为止，包风暴不再积压。
                drain_socket_wake(self._sock, self._on_drained_packet,
                                  self._timeout, 1144)
            except Exception as e:
                if self._running:
                    log.debug("[sACN] Recv error: %s", e)

    def _on_drained_packet(self, pkt: bytes, _addr) -> None:
        """排空循环包回调（drain_socket_wake 签名）。"""
        self._dispatch_packet(pkt)

    def _dispatch_packet(self, pkt: bytes) -> None:
        """解析 + 合并门 + 发信号（通过门才 emit）。"""
        result = self._accept_packet(pkt)
        if result is not None:
            universe, data = result
            self.frame_received.emit(universe, data)

    def _accept_packet(self, pkt: bytes) -> Optional[tuple]:
        """sACN 接收合并门（python-sacn ReceiverHandler 语义，per-universe 键）。

        ① StreamTerminated 位 → 该域状态立即失效，包本身不产出；
        ② 任意合法包刷新域活性 ``_last_seen``（库同款：低优先级包也维持域
           活性，胜负判定由 ``_prio`` 时间戳独立管辖）；
        ③ priority：现存胜者缺席 / 超 2.5s 未刷新 / 包 prio ≥ 胜者 → 刷新
           胜者（见 :meth:`_refresh_source_priority`）；包 prio < 胜者 → 丢弃；
        ④ sequence：``diff = seq - last``，diff ∈ (-20, 0]（乱序/重传）→ 丢弃
           （见 :meth:`_is_stale_seq`）。
        通过返回 ``(universe, data512)``，否则 None。
        """
        hdr = _sacn_header_fields(pkt)
        if hdr is None:
            return None
        universe, seq, priority, terminated = hdr
        if terminated:
            self._drop_source_state(universe)
            return None
        now = time.monotonic()
        self._last_seen[universe] = now
        self._refresh_source_priority(universe, priority, now)
        if priority < self._prio[universe][0]:
            return None
        if self._is_stale_seq(universe, seq):
            return None
        self._last_seq[universe] = seq
        return _parse_sacn(pkt)

    def _refresh_source_priority(self, universe: int, priority: int,
                                 now: float) -> None:
        """③ priority 门：胜者缺席/超时/让步时刷新；超时让位顺带清 seq。

        ★ R183 F4：胜者超时让位时顺带删该域 seq——python-sacn
        receiver_handler 周期回调"超时源连 seq 一起删"的语义在此在线
        补齐（修前 _sweep_expired_sources 只挂在 socket 超时分支，繁忙
        网络下永不执行 → 旧源残留 seq 把接管新源的流误判乱序丢包）。
        活跃流量必然走到本分支，无需新线程。
        """
        cur = self._prio.get(universe)
        if not (cur is None or now - cur[1] > SACN_SOURCE_TIMEOUT_SEC
                or cur[0] <= priority):
            return
        if cur is not None and now - cur[1] > SACN_SOURCE_TIMEOUT_SEC:
            self._last_seq.pop(universe, None)
        self._prio[universe] = (priority, now)

    def _is_stale_seq(self, universe: int, seq: int) -> bool:
        """④ sequence 门：diff ∈ (-20, 0] 为乱序/重传 → True（丢弃）。"""
        last = self._last_seq.get(universe)
        if last is None:
            return False
        return -SACN_SEQ_STALE_WINDOW < (seq - last) <= 0

    def _drop_source_state(self, universe: int) -> None:
        """该域源状态失效（stream terminated / 过期）：seq 同删——库注释原话
        "delete sequence entries so that no packet out of order problems occur"
        （重置后的新流不会被残留 seq 误判为旧包）。"""
        self._prio.pop(universe, None)
        self._last_seq.pop(universe, None)
        self._last_seen.pop(universe, None)

    def _sweep_expired_sources(self) -> None:
        """2.5s 无任何合法包的域删状态（接收线程 0.5s timeout 分支调用）。"""
        now = time.monotonic()
        expired = [u for u, ts in self._last_seen.items()
                   if now - ts > SACN_SOURCE_TIMEOUT_SEC]
        for u in expired:
            self._drop_source_state(u)


def _sacn_packet_offsets(universe: int, priority: int) -> tuple[int, int]:
    """程序化定位 seq 与数据区偏移（对 _build_sacn 输出逐字节差分，防手算错位）。

    Returns (seq_offset, data_offset)。_build_sacn 是唯一事实源——
    逐字节等价由构造方式直接保证：前缀即 _build_sacn 产物，逐帧仅改写
    seq 字节与数据区。
    """
    z = np.zeros(512, dtype=np.uint8)
    p0 = _build_sacn(universe, z, priority, 0)
    p1 = _build_sacn(universe, z, priority, 1)
    seq_off = next(i for i, (a, b) in enumerate(zip(p0, p1)) if a != b)
    z2 = z.copy()
    z2[0] = 7
    p2 = _build_sacn(universe, z2, priority, 0)
    data_off = next(i for i, (a, b) in enumerate(zip(p0, p2)) if a != b)
    return seq_off, data_off


class SACNSender:
    """
    sACN 单域发送器。
    每个实例绑定一个 universe。
    多域发送由 NetworkManager 的发送器池（_sacn_tx_pool）管理。

    Implements ``IOutputSink`` via virtual registration.

    ★ v3 (2026-08-04) 发送链优化：
      - 零分配打包：构造时用 _build_sacn 生成 638B 前缀缓冲（seq=0），
        逐帧仅改写 1 字节 seq + 512B 数据区，省掉每次 _build_sacn 的
        struct.pack×12 + 拼接分配（原实现 ~2.8µs/帧 → ~0.5µs）；
      - seq/data 偏移由 _sacn_packet_offsets 逐字节差分定位，与
        _build_sacn 逐字节等价；
      - SO_SNDBUF 放大（512KB）+ _send_lock 串行化（同 ArtNetSender）。
    """

    def __init__(self, target_ip: str = "", universe: int = 1,
                 multicast: bool = True, priority: int = 100):
        self.universe = universe
        self.priority = priority
        self.multicast = multicast
        self._seq = 0
        self._packets: int = 0
        self._errors: int = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                   socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512 * 1024)
        except OSError:
            pass

        if multicast:
            self.target_ip = _universe_to_multicast(universe)
            self._sock.setsockopt(socket.IPPROTO_IP,
                                  socket.IP_MULTICAST_TTL, 20)
        else:
            self.target_ip = target_ip

        # 零分配打包缓冲: 完整 638B 包（seq=0）逐帧原地改写 seq + 数据
        self._seq_off, self._data_off = _sacn_packet_offsets(universe, priority)
        self._pkt_buf = bytearray(
            _build_sacn(universe, np.zeros(512, dtype=np.uint8), priority, 0)
        )
        self._send_lock = threading.Lock()

    # ── IOutputSink ────────────────────────────────────────────────

    def _send_packet(self, data: np.ndarray | bytes) -> None:
        """打包并发送（512B 连续 uint8 走零分配快路径）。"""
        if isinstance(data, np.ndarray):
            arr = np.asarray(data, dtype=np.uint8)
            if arr.ndim == 1 and arr.size == 512:
                buf = self._pkt_buf
                do = self._data_off
                with self._send_lock:
                    seq = self._seq
                    self._seq = (self._seq + 1) % 256
                    self._packets += 1
                    buf[self._seq_off] = seq
                    buf[do:do + 512] = memoryview(arr)
                    try:
                        self._sock.sendto(buf, (self.target_ip, SACN_PORT))
                    except Exception as e:
                        self._errors += 1
                        log.debug("[sACN] Send error (universe=%s): %s",
                                  self.universe, e)
                return
        # 非 512 整帧 / 非常规 dtype: 回退原打包路径（语义不变）
        # ★ 2026-08-19: 短 buffer 必须先补零到 512，否则 sACN 包长度与“逐包/环+PACK”
        #   两条路径不一致，导致跨 stage 逐字节对拍 FAIL（probe_send_deep）。
        if isinstance(data, np.ndarray):
            arr = np.asarray(data, dtype=np.uint8)
            if arr.ndim == 1 and arr.size < 512:
                padded = np.zeros(512, dtype=np.uint8)
                padded[:arr.size] = arr
                data = padded
        with self._send_lock:
            seq = self._seq
            self._seq = (self._seq + 1) % 256
            self._packets += 1
            pkt = _build_sacn(self.universe, data, self.priority, seq)
            try:
                self._sock.sendto(pkt, (self.target_ip, SACN_PORT))
            except Exception as e:
                self._errors += 1
                log.debug("[sACN] Send error (universe=%s): %s", self.universe, e)

    def send_dmx(self, universe: int, data: np.ndarray) -> None:
        """See ``IOutputSink.send_dmx``.

        Note: *universe* is validated against the bound universe.
        A mismatch is silently ignored to prevent cross-universe bleed.
        """
        if int(universe) != self.universe:
            log.warning("[sACN] send_dmx universe mismatch: got %s, bound %s",
                        universe, self.universe)
            return
        self._send_packet(data)

    def get_status(self) -> Dict[str, Any]:
        """See ``IOutputSink.get_status``."""
        return {
            "protocol": "sACN",
            "target": self.target_ip,
            "universe": self.universe,
            "packets": self._packets,
            "errors": self._errors,
            "alive": self._sock is not None,
        }

    # ── Original API (kept for backward compatibility) ─────────────

    def send(self, data: np.ndarray | bytes):
        self._send_packet(data)

    def close(self):
        try:
            self._sock.close()
        except Exception:
            log.debug("sACN: failed to close socket")


IOutputSink.register(SACNSender)
