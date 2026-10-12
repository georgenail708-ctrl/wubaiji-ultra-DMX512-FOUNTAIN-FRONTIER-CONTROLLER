"""Qascade V1.0 — ArtPoll 设备发现表（R178 网络输出革新）。

职责：登记局域网里的 Art-Net 节点（其他控台的 ArtPoll / 节点的
ArtPollReply），按 30s 超时移除失联设备；提供主动发现 ``poll_network()``
（广播 ArtPoll → 收 ArtPollReply，后台线程执行不阻塞 UI）。

裸 socket 纪律：本模块不创建任何 socket —— 统一走
``artnet_node.make_discovery_socket`` 工厂与协议层纯函数。

调用方：``src/network/network_manager.py``（NetworkManager.start 接线）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .artnet_node import (
    ARTNET_PORT,
    _build_artpoll,
    _parse_artpollreply,
    make_discovery_socket,
)
from .nic_broadcast import directed_broadcast_targets

log = logging.getLogger(__name__)

#: 设备失联判定：超过该秒数没有再次出现即移除（ArtPoll 周期通常 ≤10s）。
DEVICE_TIMEOUT_SEC = 30.0


def _poll_recv_loop(sock, deadline: float, on_reply: Callable[[dict], None]) -> None:
    """窗口期内收 ArtPollReply（socket 由调用方提供/关闭）。"""
    while time.monotonic() < deadline:
        try:
            pkt, addr = sock.recvfrom(1024)
        except TimeoutError:      # socket.timeout 的 3.10+ 别名，免 import socket
            continue
        except OSError:
            break
        info = _parse_artpollreply(pkt)
        if info:
            info["ip"] = addr[0]
            # R183 端口自适应：包内 port 字段无效(0)时回退实际 UDP 源端口
            #（与 ArtNetReceiver._handle_packet 被动路径同款语义）。
            if not info.get("port"):
                info["port"] = addr[1]
            on_reply(info)


def _active_poll_once(bind_ip: str, timeout_sec: float,
                      on_reply: Callable[[dict], None]) -> None:
    """跑一轮主动 ArtPoll（阻塞，供后台线程调用）。

    向 255.255.255.255 + 各网卡定向广播地址发 ArtPoll，窗口期内收集
    ArtPollReply。每条回复回调 ``on_reply(info)``（info.ip 以收包地址为准）。
    """
    try:
        sock = make_discovery_socket(bind_ip)
    except OSError as e:
        log.warning("[ArtPoll] discovery socket 建立失败: %s", e)
        return
    try:
        poll = _build_artpoll()
        targets = ["255.255.255.255"] + directed_broadcast_targets()
        for tgt in targets:
            try:
                sock.sendto(poll, (tgt, ARTNET_PORT))
            except OSError:
                log.debug("[ArtPoll] sendto 失败 (target=%s)", tgt)
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        _poll_recv_loop(sock, deadline, on_reply)
    finally:
        try:
            sock.close()
        except OSError:
            log.debug("[ArtPoll] discovery socket close 失败", exc_info=True)


class ArtPollDiscovery(QObject):
    """Art-Net 节点发现表。

    信号：
      - ``devices_found(list)``：新设备出现（list 里是设备 dict，通常单条）；
      - ``device_lost(str)``：设备 30s 未再现，参数为其 IP。

    数据源：
      - ``handle_poll(info)``：被动——网络里其他控台广播 ArtPoll（经
        ArtNetReceiver.poll_received 转发）→ 轮询方本身即被登记；
      - ``poll_network()``：主动——广播 ArtPoll 收 ArtPollReply。
    """

    devices_found = pyqtSignal(list)
    device_lost = pyqtSignal(str)

    def __init__(self, parent=None, bind_ip: str = "0.0.0.0",
                 timeout_sec: float = DEVICE_TIMEOUT_SEC):
        super().__init__(parent)
        self._bind_ip = bind_ip or "0.0.0.0"
        self._timeout_sec = float(timeout_sec)
        self._devices: Dict[str, dict] = {}   # ip -> 设备信息（含 last_seen）
        # R181：`_poll_worker` 线程在扫描窗口内写入 `_devices`，UI 侧 1s 一次
        # `devices()` 读取——加锁避免 "dict changed size during iteration"。
        self._devices_lock = threading.Lock()
        # R182 P2：轮询标志加锁 —— UI 线程 poll_network 与扫描线程收尾的
        # `_polling = False` 并发读写（GIL 下单写原子，但"读-判-写"竞态会
        # 让连点扫描按钮起两个重叠窗口 → 收包乱序）。
        self._polling_lock = threading.Lock()
        self._polling = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._sweep_expired)

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ── 登记与查询 ───────────────────────────────────────────────────────────

    def devices(self) -> List[dict]:
        """当前设备表快照（按 IP 排序；含 seen_sec 可见年龄）。线程安全。"""
        now = time.monotonic()
        out = []
        with self._devices_lock:
            for ip in sorted(self._devices):
                entry = dict(self._devices[ip])
                entry["seen_sec"] = round(now - entry.pop("last_seen", now), 2)
                out.append(entry)
        return out

    def handle_poll(self, info: dict) -> None:
        """被动登记：其他控台发来的 ArtPoll（ArtNetReceiver.poll_received）。"""
        self._register({
            "ip": str(info.get("ip", "")),
            "port": int(info.get("port", 0) or ARTNET_PORT),
            "source": "artpoll",
            "short_name": "",
            "talk_to_me": int(info.get("talk_to_me", 0)),
        })

    def handle_reply(self, info: dict) -> None:
        """主动/转推登记：ArtPollReply 解析结果。"""
        entry = dict(info)
        entry.setdefault("source", "reply")
        self._register(entry)

    def _register(self, dev: dict) -> None:
        """登记设备；新 IP → devices_found（去重：已知 IP 只刷新时间戳）。"""
        ip = str(dev.get("ip", "")).strip()
        if not ip:
            return
        entry = dict(dev)
        entry["last_seen"] = time.monotonic()
        with self._devices_lock:
            is_new = ip not in self._devices
            self._devices[ip] = entry
        if is_new:
            log.info("[ArtPoll] 发现设备 %s:%s (%s)",
                     ip, entry.get("port"), entry.get("source"))
            self.devices_found.emit([dict(entry)])

    def _sweep_expired(self) -> None:
        """超时清扫：30s 未再现的设备移除并发 device_lost。"""
        now = time.monotonic()
        with self._devices_lock:
            expired = [ip for ip, e in self._devices.items()
                       if now - e.get("last_seen", now) > self._timeout_sec]
            for ip in expired:
                del self._devices[ip]
        for ip in expired:
            log.info("[ArtPoll] 设备超时移除 %s", ip)
            self.device_lost.emit(ip)

    # ── 主动发现 ─────────────────────────────────────────────────────────────

    def poll_network(self, timeout_sec: float = 1.5) -> bool:
        """主动发现：后台线程广播 ArtPoll 并收集 ArtPollReply。

        立即返回；结果经 ``devices_found`` 信号送达。已有轮询在跑时幂等
        （返回 False 不重复发起）。检查与置位在 :attr:`_polling_lock` 内
        原子完成（R182 P2），连点不会起两个重叠扫描窗口。
        """
        with self._polling_lock:
            if self._polling:
                return False
            self._polling = True
        try:
            thread = threading.Thread(
                target=self._poll_worker, args=(float(timeout_sec),),
                daemon=True, name="artpoll-discovery",
            )
            thread.start()
        except Exception:
            with self._polling_lock:
                self._polling = False       # 线程起不来时别把表卡在"扫描中"
            raise
        return True

    def _poll_worker(self, timeout_sec: float) -> None:
        try:
            _active_poll_once(self._bind_ip, timeout_sec, self.handle_reply)
        except Exception:
            log.exception("[ArtPoll] 主动发现失败")
        finally:
            with self._polling_lock:
                self._polling = False

    @property
    def is_polling(self) -> bool:
        """是否有扫描窗口在跑（UI 用；线程安全）。"""
        with self._polling_lock:
            return self._polling
