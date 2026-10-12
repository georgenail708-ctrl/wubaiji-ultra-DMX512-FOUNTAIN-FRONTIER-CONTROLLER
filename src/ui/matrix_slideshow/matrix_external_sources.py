# -*- coding: utf-8 -*-
"""matrix_external_sources.py — 外部纹理源登记位（深查②三.Matrix #4 Spout / #6 NDI）。

侦察结论（2026-09-30，本机 venv 实测）：
  * ``pip list`` 无 SpoutGL；``importlib.util.find_spec("SpoutGL") is None``。
  * NDI 无任何 pip 绑定（``cyndilib`` / ``ndi`` / ``NDIlib`` 全部 None）。
  * 任务书硬约束：**不新增依赖**。所以本模块只做**登记位 + 探测门控**：
      - ``MODE_EXT_SPOUT`` / ``MODE_EXT_NDI`` 两个 MODE 枚举登记位（挂进 PIXEL_MODES）；
      - ``probe_availability()`` 用 importlib.util.find_spec 探测并缓存 ——
        **无库 = 零开销**：不 import、不起线程、不建定时器、不探测设备；
      - 库真的存在时 ``ExternalFrameBridge`` 才收帧（装机后自动可用）。
  * 收帧侧只调用官方示例用到的 API（SpoutReceiver / setReceiverName /
    receiveImage / isUpdated / getSenderWidth / getSenderHeight /
    waitFrameSync；参考 https://github.com/jlai/Python-SpoutGL
    的 README 与 examples/image/receiver.py）。
  * 收帧后复用**现有亮度网格采样链**：``MatrixPixelEngine._frame_to_video_maps()``
    （四角透视 #2 / 掩码 #3 / 色调链照旧），桥接把 BGR 帧写进 ``_video_preview_grid``
    并让引擎保持 MODE_VIDEO_SAMPLE → tick / 预览 / 输出零改动。

NDI 为远期项：NDI SDK 是 NewTek/Vizrt 的专有运行时（非 pip 可装），
本模块只登记 kind 与理由文本，不实现收帧。
"""
from __future__ import annotations

import importlib.util
import logging
from typing import Dict, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QTimer

from .matrix_pixel_engine import MODE_VIDEO_SAMPLE

logger = logging.getLogger(__name__)

#: MODE 枚举登记位（引擎不认识这两个 id；选择时由 resolve_pixel_mode 归一到
#: MODE_VIDEO_SAMPLE + 帧源 kind —— 见 `apply_external_source`）。
MODE_EXT_SPOUT = "ext_spout"
MODE_EXT_NDI = "ext_ndi"

#: `engine.video_source` 的扩展取值（原只有 file / camera）。
SOURCE_KIND_SPOUT = "spout"
SOURCE_KIND_NDI = "ndi"
EXTERNAL_SOURCE_KINDS = (SOURCE_KIND_SPOUT, SOURCE_KIND_NDI)

#: 登记表：mode id → 元数据（kind / 显示名 / pip 线索 / 引擎实际模式）。
EXTERNAL_MODES: Dict[str, dict] = {
    MODE_EXT_SPOUT: {
        "kind": SOURCE_KIND_SPOUT,
        "label": "Spout 外部纹理（外部 VJ 软件）",
        "module": "SpoutGL",
        "pip_hint": "pip install SpoutGL",
        "engine_mode": MODE_VIDEO_SAMPLE,
        "future": False,
    },
    MODE_EXT_NDI: {
        "kind": SOURCE_KIND_NDI,
        "label": "NDI 输入（远期登记位）",
        "module": "",
        "pip_hint": "待评估：NDI SDK 需专有运行时，非 pip 可装",
        "engine_mode": MODE_VIDEO_SAMPLE,
        "future": True,
    },
}

#: 探测用模块名（Spout 为 pip 包 SpoutGL；NDI 为候选绑定，全部缺失 = 登记位不可用）。
_PROBE_MODULES = {
    SOURCE_KIND_SPOUT: ("SpoutGL",),
    SOURCE_KIND_NDI: ("cyndilib", "ndi", "NDIlib"),
}

#: 可注入的 find_spec（测试 monkeypatch 此别名，避免真装机才可测）。
_spec_finder = importlib.util.find_spec

_AVAILABILITY_CACHE: Optional[Dict[str, object]] = None


def module_available(name: str) -> bool:
    """find_spec 探测单个模块（异常/空名 → False）。"""
    if not name:
        return False
    try:
        return _spec_finder(name) is not None
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return False


def probe_availability(force: bool = False) -> Dict[str, object]:
    """探测各外部源是否可用；结果缓存（无库路径零开销，仅一次 find_spec）。

    返回 ``{"spout": bool, "ndi": bool, "modules": {...}}``。
    """
    global _AVAILABILITY_CACHE
    if _AVAILABILITY_CACHE is not None and not force:
        return _AVAILABILITY_CACHE
    found: Dict[str, object] = {}
    modules: Dict[str, str] = {}
    for kind, names in _PROBE_MODULES.items():
        hit = next((n for n in names if module_available(n)), "")
        found[kind] = bool(hit) and not bool(EXTERNAL_MODES.get(mode_of_kind(kind), {}).get("future"))
        modules[kind] = hit
    _AVAILABILITY_CACHE = {"spout": bool(found.get("spout")),
                           "ndi": bool(found.get("ndi")),
                           "modules": modules}
    return _AVAILABILITY_CACHE


def is_available(kind: str) -> bool:
    """某帧源 kind 当前是否可用（无库 → False，不做任何 import）。

    注：NDI 即使找到绑定也因 ``future`` 标记而保持 False（登记位语义：
    本仓不实现 NDI 收帧，理由见模块 docstring 与报告）。
    """
    data = probe_availability()
    return bool(data.get(str(kind or "")))


def mode_of_kind(kind: str) -> str:
    """帧源 kind → MODE 登记位 id（无匹配 → 空串）。"""
    want = str(kind or "")
    for mode_id, meta in EXTERNAL_MODES.items():
        if meta.get("kind") == want:
            return mode_id
    return ""


def normalize_source_kind(value) -> str:
    """任意值 → "file" | "camera" | "spout" | "ndi"（口径与 video_transforms 同一实现）。"""
    from .matrix_video_transforms import normalize_video_source_kind
    return normalize_video_source_kind(value)


def resolve_pixel_mode(mode_id: str) -> Tuple[str, str]:
    """像素模式 id → (引擎实际模式, 外部帧源 kind)。

    非外部模式原样返回 ``(mode_id, "")``；外部登记位归一到视频采样 + kind。
    """
    meta = EXTERNAL_MODES.get(str(mode_id or ""))
    if not meta:
        return str(mode_id or ""), ""
    return str(meta.get("engine_mode", MODE_VIDEO_SAMPLE)), str(meta.get("kind", ""))


def register_external_modes(pixel_modes: dict, group_title: str = "外部纹理（Spout / NDI）") -> None:
    """把外部源登记位挂进 PIXEL_MODES（幂等；面板导入期调用一次）。"""
    try:
        bucket = pixel_modes.setdefault(str(group_title), [])
        for mode_id, meta in EXTERNAL_MODES.items():
            item = (mode_id, str(meta.get("label", mode_id)))
            if item not in bucket:
                bucket.append(item)
    except Exception:
        logger.debug("matrix: register external modes failed", exc_info=True)


def describe_slot(kind: str) -> str:
    """登记位状态文本（状态标签用；无库时给出装机提示）。"""
    kind = normalize_source_kind(kind)
    if kind not in EXTERNAL_SOURCE_KINDS:
        return "未启用外部纹理源"
    mode_id = mode_of_kind(kind)
    meta = EXTERNAL_MODES.get(mode_id, {})
    if meta.get("future"):
        return f"{kind.upper()} 为远期项（{meta.get('pip_hint', '')}）"
    if is_available(kind):
        return f"{kind.upper()} 可用（{meta.get('module', '')} 已安装）"
    return (f"{kind.upper()} 未安装（{meta.get('pip_hint', '')}）—— "
            "装机后自动可用，当前零开销")


def rgba_to_bgr(raw: bytes, width: int, height: int) -> Optional[np.ndarray]:
    """RGBA 字节流 → BGR uint8 (H,W,3)；尺寸非法/字节不足 → None（纯函数）。"""
    w, h = int(width or 0), int(height or 0)
    if w <= 0 or h <= 0:
        return None
    try:
        arr = np.frombuffer(bytes(raw), dtype=np.uint8)
    except (TypeError, ValueError):
        return None
    need = w * h * 4
    if arr.size < need:
        return None
    rgba = arr[:need].reshape(h, w, 4)
    return np.ascontiguousarray(rgba[:, :, 2::-1])   # RGBA → BGR（拷贝保证连续）


class SpoutFrameSource:
    """Spout 接收端（**无库时 open() 直接 False，不 import、零开销**）。"""

    #: SpoutGL.enums.GL_RGBA 的常量值（避免依赖 PyOpenGL 的 GL 模块）。
    GL_RGBA = 0x1908

    def __init__(self, sender_name: str = ""):
        self.sender_name = str(sender_name or "")
        self._rx = None
        self._buffer = None
        self.last_error = ""

    def open(self, sender_name: str = "") -> bool:
        """打开接收端；库缺失 → False（last_error 带 pip 提示）。"""
        if not is_available(SOURCE_KIND_SPOUT):
            self.last_error = describe_slot(SOURCE_KIND_SPOUT)
            return False
        try:
            import SpoutGL  # type: ignore  # noqa: PLC0415
        except Exception:
            self.last_error = "SpoutGL 导入失败"
            return False
        name = str(sender_name or self.sender_name or "")
        try:
            rx = SpoutGL.SpoutReceiver()
            if name and hasattr(rx, "setReceiverName"):
                rx.setReceiverName(name)
            self._rx = rx
            self.sender_name = name
            self.last_error = ""
            return True
        except Exception:
            logger.debug("matrix: spout receiver open failed", exc_info=True)
            self.last_error = "SpoutReceiver 初始化失败"
            self._rx = None
            return False

    def _ensure_buffer(self) -> bool:
        """按发送端分辨率准备缓冲（分辨率变了就重建；helper）。"""
        try:
            w = int(self._rx.getSenderWidth() or 0)
            h = int(self._rx.getSenderHeight() or 0)
        except Exception:
            return False
        if w <= 0 or h <= 0:
            return False
        need = w * h * 4
        if self._buffer is None or len(self._buffer) != need:
            self._buffer = bytearray(need)
        return True

    def read_frame(self) -> Optional[np.ndarray]:
        """拉一帧并转 BGR；无帧/未连接 → None（调用方保持上一网格）。"""
        rx = self._rx
        if rx is None:
            return None
        try:
            self._ensure_buffer()
            result = rx.receiveImage(self._buffer, self.GL_RGBA, False, 0)
            if not bool(result) or self._buffer is None:
                return None
            w = int(rx.getSenderWidth() or 0)
            h = int(rx.getSenderHeight() or 0)
            return rgba_to_bgr(bytes(self._buffer), w, h)
        except Exception:
            logger.debug("matrix: spout read frame failed", exc_info=True)
            return None

    def close(self) -> None:
        """释放接收端（SpoutGL 的 ctx 关闭由 GC/with 语义承担，这里只清引用）。"""
        self._rx = None
        self._buffer = None

    def info(self) -> str:
        """接收端状态一行文本。"""
        if self._rx is None:
            return self.last_error or "未连接"
        try:
            return (f"发送端 {self.sender_name or '(任意)'} "
                    f"{int(self._rx.getSenderWidth() or 0)}x"
                    f"{int(self._rx.getSenderHeight() or 0)}")
        except Exception:
            return f"发送端 {self.sender_name or '(任意)'}"


def make_frame_source(kind: str):
    """kind → 帧源对象（spout 有无库都返回对象；ndi 返回 None）。"""
    kind = normalize_source_kind(kind)
    if kind == SOURCE_KIND_SPOUT:
        return SpoutFrameSource()
    return None


class ExternalFrameBridge(QObject):
    """把外部帧推进现有视频采样链的桥（引擎上单例；未启用时零开销）。

    每 tick（R194/HF-02 后）：``source.read_frame()`` →
    ``engine.submit_external_video_frame(frame)``（latest-wins 原始帧槽），
    warp/掩码/色调链由引擎解码线程统一跑；GUI 不再做像素转换。
    自愈守卫：引擎运行结束或 ``video_source`` 与 kind 不一致 → 自动停表。
    """

    def __init__(self, engine, kind: str = "", parent: Optional[QObject] = None):
        super().__init__(parent)
        self._engine = engine
        self._kind = normalize_source_kind(kind)
        self._source = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)          # ~30fps
        self._timer.timeout.connect(self._on_tick)
        self.last_frame_ok = False

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def active(self) -> bool:
        return bool(self._timer.isActive())

    def enable(self, kind: str) -> Tuple[bool, str]:
        """启用外部帧源；返回 (是否真的在收帧, 状态文本)。"""
        kind = normalize_source_kind(kind)
        if kind not in EXTERNAL_SOURCE_KINDS:
            return False, "未选择外部帧源"
        if _is_same_active(self, kind):
            # 幂等：同 kind 且已在收帧 → 不重开设备（工程还原/参数同步会重复调用）
            return True, f"{kind.upper()} 已启用；{self.source_info()}"
        if not is_available(kind):
            self.disable()
            return False, describe_slot(kind)
        source = _open_source(kind)
        if source is None:
            self.disable()
            return False, describe_slot(kind)
        self._source = source
        self._kind = kind
        _persist_source_intent(self._engine, kind)
        if not self._timer.isActive():
            self._timer.start()
        return True, f"{kind.upper()} 已启用；{source.info()}"

    def disable(self) -> None:
        """停表 + 释放帧源（不依赖库是否可用，随时可调）。"""
        self._timer.stop()
        if self._source is not None:
            try:
                self._source.close()
            except Exception:
                logger.debug("matrix: close external source failed", exc_info=True)
        self._source = None
        self.last_frame_ok = False

    def source_info(self) -> str:
        if self._source is None:
            return "未连接"
        try:
            return self._source.info()
        except Exception:
            return "未连接"

    def _on_tick(self) -> None:
        """定时器回调：自愈守卫 + 拉帧 + **投递原始帧**（R194/HF-02）。

        GUI 只做 ``read_frame()``；warp/RGB 转换/掩码链全部移交解码线程
        （``engine.submit_external_video_frame`` latest-wins 槽）——Spout 是
        接收型源，与 cv2 句柄生命周期不纠缠。无帧 → 保持上一网格。
        """
        engine = self._engine
        if engine is None or self._source is None:
            self.disable()
            return
        if not bool(getattr(engine, "_running", False)):
            return                                    # 引擎未跑：不拉帧（零开销）
        if normalize_source_kind(getattr(engine, "video_source", "file")) != self._kind:
            self.disable()                            # 源被换掉（清台/切相机）→ 自愈停表
            return
        frame = self._source.read_frame()
        if frame is None or getattr(frame, "size", 0) == 0:
            self.last_frame_ok = False
            return
        self.last_frame_ok = True
        try:
            engine.submit_external_video_frame(frame)  # 解码线程统一 warp/转换
        except Exception:
            logger.debug("matrix: push external frame failed", exc_info=True)


def bridge_of(engine) -> Optional[ExternalFrameBridge]:
    """取引擎上的桥单例（没有则返回 None；不隐式创建）。"""
    return getattr(engine, "external_source_bridge", None)


def _is_same_active(self, kind: str) -> bool:
    """同 kind 且桥已在收帧、引擎源未变 → 幂等命中（helper）。"""
    if not self._timer.isActive() or self._kind != kind:
        return False
    engine_kind = normalize_source_kind(getattr(self._engine, "video_source", "file"))
    return engine_kind == kind


def _open_source(kind: str):
    """建 + 打开帧源；不可用/打开失败 → None（helper）。"""
    source = make_frame_source(kind)
    if source is None:
        return None
    try:
        if not source.open():
            return None
    except Exception:
        logger.debug("matrix: open external source failed", exc_info=True)
        return None
    return source


def _reset_engine_source(engine) -> None:
    """把引擎帧源归 "file"（关桥用；helper）。"""
    try:
        engine.video_source = "file"
    except Exception:
        logger.debug("matrix: reset video_source failed", exc_info=True)


def _persist_source_intent(engine, kind: str) -> None:
    """把帧源意图写进引擎（无库也落盘：装机后自动可用；helper）。"""
    try:
        engine.video_source = kind
    except Exception:
        logger.debug("matrix: persist source intent failed", exc_info=True)


def _attach_bridge(engine) -> "ExternalFrameBridge":
    """取/建引擎上的桥单例（helper）。"""
    bridge = bridge_of(engine)
    if bridge is not None:
        return bridge
    bridge = ExternalFrameBridge(engine)
    try:
        engine.external_source_bridge = bridge
    except Exception:
        logger.debug("matrix: attach bridge failed", exc_info=True)
    return bridge


def apply_external_source(engine, kind: str) -> Tuple[bool, str]:
    """面板入口：kind 空/非法 → 关桥并把 video_source 归 "file"；否则建/启用。

    注意：**意图总是落盘**（video_source 立刻写成 kind），即使当前无库 ——
    客户装机（pip install SpoutGL）后打开工程即自动可用。
    """
    kind = normalize_source_kind(kind)
    if engine is None:
        return False, "无像素引擎"
    if kind not in EXTERNAL_SOURCE_KINDS:
        old = bridge_of(engine)
        if old is not None:
            old.disable()
        _reset_engine_source(engine)
        return False, "外部纹理源已关闭"
    ok, msg = _attach_bridge(engine).enable(kind)
    if not ok:
        _persist_source_intent(engine, kind)
    return ok, msg


def sync_external_source(engine) -> None:
    """工程还原后按 ``engine.video_source`` 重建/停用桥（load_state 尾部调用）。"""
    if engine is None:
        return
    kind = normalize_source_kind(getattr(engine, "video_source", "file"))
    if kind in EXTERNAL_SOURCE_KINDS:
        apply_external_source(engine, kind)
    else:
        old = bridge_of(engine)
        if old is not None:
            old.disable()


__all__ = [
    "EXTERNAL_MODES", "EXTERNAL_SOURCE_KINDS", "ExternalFrameBridge",
    "MODE_EXT_NDI", "MODE_EXT_SPOUT", "SOURCE_KIND_NDI", "SOURCE_KIND_SPOUT",
    "SpoutFrameSource", "apply_external_source", "bridge_of", "describe_slot",
    "is_available", "make_frame_source", "mode_of_kind", "module_available",
    "normalize_source_kind", "probe_availability", "register_external_modes",
    "resolve_pixel_mode", "rgba_to_bgr", "sync_external_source",
]
