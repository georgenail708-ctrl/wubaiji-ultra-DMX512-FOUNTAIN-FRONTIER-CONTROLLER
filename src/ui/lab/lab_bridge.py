"""实验室 3D 预览的 QWebChannel 桥 —— 宿主 → 预览页的单向数据推送 + ready 握手。

为什么走 WebChannel 而不是 runJavaScript 拼字符串：
  逐帧推送（30fps）若每次 runJavaScript(json.dumps(...)) 会反复跨进程序列化
  大数组；QWebChannel 只需注册对象，JS 侧直接收对象/数组，且宿主可同步读到
  ready 状态。页面仍在本地 HTTP 上加载（避开 file:// 的 ES module / wasm MIME 限制）。

预览页职责单一：只做可视化渲染器，由宿主喂 {positions, gridSize, frame}；
真正的逐帧通道值以 Python 为准（预览与输出因此天然一致——同一份 field）。
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class LabPreviewBridge(QObject):
    """注册到 QWebChannel 的对象名：labBridge。"""

    ready_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_ready = False
        self._pending_layout: str = ""
        self._pending_frames: dict = {}     # {idx: [ch值...]} 供页面重连后补发

    # ── JS → Python ──────────────────────────────────────────────
    @pyqtSlot()
    def pageReady(self) -> None:
        self._page_ready = True
        self.ready_changed.emit(True)

    @pyqtSlot(str)
    def log(self, msg: str) -> None:
        print(f"[lab-preview] {msg}", flush=True)

    @property
    def page_ready(self) -> bool:
        return self._page_ready

    def reset(self) -> None:
        self._page_ready = False
        self._pending_layout = ""
        self._pending_frames.clear()

    # ── Python → JS（经 view.page().runJavaScript 推，由窗口调用）──
    def layout_js(self) -> str:
        """摆位注入脚本（真实 TopView 坐标；预览即现场）。"""
        return f"window.__labSetLayout({self._pending_layout or 'null'});"

    def set_layout(self, preview_json: dict) -> str:
        self._pending_layout = json.dumps(preview_json, ensure_ascii=False)
        return self.layout_js()

    def frame_js(self, idx: int, values: list) -> str:
        arr = ",".join(f"{float(v):.5f}" for v in values)
        return f"window.__labSetFrame({int(idx)},[{arr}]);"

    def device_js(self, device: str) -> str:
        return f"window.__labSetDevice({json.dumps(device)});"
