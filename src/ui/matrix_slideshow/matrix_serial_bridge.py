# -*- coding: utf-8 -*-
"""matrix_serial_bridge.py — WS2812/串口直出的桥接登记与说明（深查②三.Matrix #17）。

侦察结论（2026-09-30）：
  * 本仓网络层是 Art-Net / sACN（``src/network/``），没有串口像素协议栈；
  * 开源参考 串口固件本身提供
    **Art-Net / DMX 输入**，
    即"DMX 控制器 → → WS2812"这条链路已由本仓现有网络层覆盖；
  * 因此**不自写串口协议栈**：重复造轮子、且 固件侧的 WS2812 时序/
    刷新率/伽马修正远非本仓职责（自写需处理 800kHz 时序、DMA、配色序）。
  * 本模块只做两件事：登记位常量 ``SERIAL_BRIDGE_SLOT`` + 面向用户的
    桥接说明文本（弹层展示），不改动网络层。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 登记位：若将来确需串口直出，协议对象应落在这里（当前不实现）。
SERIAL_BRIDGE_SLOT = "serial_ws2812_bridge"

#: 不自写串口协议栈的理由（报告与弹层同用）。
SERIAL_SKIP_REASON = (
    "不自写串口协议栈：固件已提供 Art-Net/E1.31 入口，"
    "本仓网络层可直接对接（自写需处理 800kHz 时序/DMA/配色序，收益为负）。"
)

串口固件_BRIDGE_TEXT = """/ WS2812 桥接说明（经 Art-Net，不自写串口）

一、链路
    本仓（DMX 控制器） ── Art-Net ──▶ 固件 ──▶ WS2812/WS2815 灯带

二、侧设置（固件 Web 界面）
    1. Settings → LED Preferences：设置灯带类型（WS2812/SK6812）、
       长度、GPIO、颜色顺序（RGB/GRB）、最大亮度限制。
    2. Settings → Sync Interfaces → Art-Net / E1.31：
       开启 "Art-Net"，设置起始 universe（例如 1）与每 universe 的像素数
       （默认 170 像素 = 510 通道/域）。
    3. 若要按 DMX 通道精确定位，把 设为 "DMX" 模式或使用
       "Multi RGB" 灯段映射。

三、本仓侧设置
    1. 网络页把对应 universe 的接收方地址指向 设备 IP，
       Art-Net 广播/单播均可（同一网段推荐单播，减少广播风暴）。
    2. 用矩阵面板的"像素效果/幻灯片"直接出图；通道类型选 r/g/b/w 或 dimmer
       与 灯段一致。

四、为什么不做串口直出
    {reason}

五、常见问题
    * 灯带闪烁：确认 universe 内像素数不超过 170，或降低刷新率。
    * 颜色错位：颜色顺序与本仓通道顺序不一致，改 的 RGB order。
    * 只亮前几十颗：Art-Net 单域 512 通道上限，多域请逐个配置并配接。
"""


def serial_bridge_text() -> str:
    """完整桥接说明（弹层/帮助文本）。"""
    return 串口固件_BRIDGE_TEXT.format(reason=SERIAL_SKIP_REASON)


def open_serial_bridge_info(panel) -> None:
    """弹层展示 串口桥接说明（非模态信息框，不改配置）。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox
    box = QMessageBox(panel)
    box.setWindowTitle("/ WS2812 桥接说明")
    box.setTextFormat(Qt.TextFormat.PlainText)   # A2：PyQt6 枚举严格化，裸 int 会 TypeError
    box.setText(serial_bridge_text())
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


__all__ = ["SERIAL_BRIDGE_SLOT", "SERIAL_SKIP_REASON", "串口固件_BRIDGE_TEXT",
           "open_serial_bridge_info", "serial_bridge_text"]
