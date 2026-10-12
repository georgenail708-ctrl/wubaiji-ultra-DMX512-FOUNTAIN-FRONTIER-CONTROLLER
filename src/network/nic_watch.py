"""Qascade V1.0 — 网卡热监视（R178 网络输出革新）。

职责：感知网络适配器集合变化（网线插拔 / Wi-Fi 漫游 / DHCP 换址 /
虚拟网卡增删），发 ``nics_changed`` 信号，供 NetworkManager 热重建
发送链路（层状态不动）。

实现：
- 优先 QNetworkInformation（PyQt6 QtNetwork）：``loadDefaultBackend()``
  成功后连接 reachability/transportMediumChanged，链路变化即时触发；
  本 PyQt6 无 addressChanged，且 reachability 不覆盖纯换 IP →
  因此始终叠加 QTimer(3000ms) 轮询网卡 IP 集合做差分，作为检出底线，
  QNetworkInformation 信号只是把检出的即时性提前。
- 本模块不创建任何 socket —— IP 枚举走 ``artnet_node.local_ipv4_addresses``
  （getaddrinfo，非裸 socket）。

调用方：``src/network/network_manager.py``（NetworkManager.start 挂载）。
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .artnet_node import local_ipv4_addresses

log = logging.getLogger(__name__)


class NicWatch(QObject):
    """网络适配器变化监视器。

    ``nics_changed`` 信号在"本机活动 IPv4 集合"发生变化时发出
    （启动时的首个基线快照不触发）。
    """

    nics_changed = pyqtSignal()

    def __init__(self, parent=None, poll_interval_ms: int = 3000):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(max(250, int(poll_interval_ms)))
        self._timer.timeout.connect(self.check_now)
        self._signature: Optional[Tuple[str, ...]] = None
        self._net_info = None
        self._signal_targets: list = []

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._signature = tuple(local_ipv4_addresses())
        self._net_info = self._load_network_information()
        if self._net_info is not None:
            self._connect_change_signals(self._net_info)
            log.info("NicWatch: QNetworkInformation 后端已挂接（%s）+ %sms 轮询",
                     self._net_info.backendName(), self._timer.interval())
        else:
            log.info("NicWatch: QNetworkInformation 不可用，回退 %sms 轮询",
                     self._timer.interval())
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        for sig, slot in self._signal_targets:
            try:
                sig.disconnect(slot)
            except TypeError:
                pass
        self._signal_targets.clear()

    # ── 检测 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_network_information():
        """尝试加载 QNetworkInformation 默认后端；失败返回 None（可回退）。"""
        try:
            from PyQt6.QtNetwork import QNetworkInformation
            if QNetworkInformation.loadDefaultBackend():
                return QNetworkInformation.instance()
        except Exception:
            log.debug("NicWatch: QNetworkInformation 加载失败", exc_info=True)
        return None

    def _connect_change_signals(self, info) -> None:
        """链路级即时信号（isSupported 无此绑定，用 supports/信号存在性兜底）。"""
        for name in ("reachabilityChanged", "transportMediumChanged"):
            sig = getattr(info, name, None)
            if sig is None:
                continue
            try:
                sig.connect(self.check_now)
                self._signal_targets.append((sig, self.check_now))
            except TypeError:
                log.debug("NicWatch: %s 连接失败", name, exc_info=True)

    def _current_signature(self) -> Tuple[str, ...]:
        return tuple(local_ipv4_addresses())

    def check_now(self) -> None:
        """比对当前网卡 IP 集合与上次快照；变化发 nics_changed。"""
        try:
            sig = self._current_signature()
        except Exception:
            log.debug("NicWatch: 网卡枚举失败", exc_info=True)
            return
        if sig == self._signature:
            return
        prev = self._signature
        self._signature = sig
        log.info("NicWatch: 网卡集合变化 %s -> %s", prev, sig)
        self.nics_changed.emit()
