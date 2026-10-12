# -*- coding: utf-8 -*-
"""matrix_frame_library.py — 帧库预设导出/导入 .json（深查②三.Matrix #14）。

为什么自写（现成资源核查）：
  * 仓内序列化先例是**工程态持久化**（``matrix_slideshow_state`` → 面板
    save_state/load_state 走工程文件），没有"独立可分享的帧库文件"格式；
  * TopView 布局导出先例（``src/ui/topview_layout_io.py``）是表格导出，
    结构不匹配帧序列（缩略图/命名/ON-OFF 值/过渡模式/过渡时长都要保）；
  * 结论：用 **stdlib json + QFileDialog** 自写一个小格式（无新依赖），
    与工程文件解耦，可跨工程/跨机器分享"帧序列模板"。

格式（版本化，向后兼容）::

    {"format": "dmx-matrix-frame-library", "version": 1,
     "fixture_count": 120, "created": "2026-09-30 12:00:00",
     "slides": [{"name": "...", "states": {"fid": 0-255}, "hold_time": 1.0,
                 "transition_time": 0.2, "transition_mode": "cut",
                 "on_value": 255, "off_value": 0, "note": ""}, ...]}

导入侧只关心 slides：帧名/灰度值/过渡参数全保留，灯光地址按**设备 id** 匹配，
导入到另一工程时 id 不同的设备自然被忽略（值仍在，可再编辑）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 文件格式标识与版本（导入侧据此拒绝陌生文件）
LIBRARY_FORMAT = "dmx-matrix-frame-library"
LIBRARY_VERSION = 1
#: 单次导入上限（防误选超大文件把面板卡死）
MAX_SLIDES = 500


def library_payload(slides, fixture_count: int = 0) -> Dict:
    """帧列表 → 可 JSON 序列化的帧库 dict（纯函数；含灰度值 int 0-255）。"""
    items: List[Dict] = []
    for slide in (slides or []):
        if hasattr(slide, "to_dict"):
            items.append(dict(slide.to_dict()))
        elif isinstance(slide, dict):
            items.append(dict(slide))
    return {
        "format": LIBRARY_FORMAT,
        "version": LIBRARY_VERSION,
        "fixture_count": int(fixture_count or 0),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "slides": items,
    }


def _header_version(data: Dict) -> int:
    """校验 format/version；不符抛 ValueError（helper）。"""
    if str(data.get("format", "")) != LIBRARY_FORMAT:
        raise ValueError("不是矩阵帧库文件（format 不匹配）")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < 1 or version > LIBRARY_VERSION:
        raise ValueError(f"帧库版本不支持：{version}（本机支持 1-{LIBRARY_VERSION}）")
    return version


def _slides_from_raw(raw) -> List:
    """原始 slides 列表 → [MatrixSlide]（坏项跳过；全坏 → ValueError）。"""
    from .matrix_slide_engine import MatrixSlide
    if not isinstance(raw, list) or not raw:
        raise ValueError("帧库里没有帧（slides 为空）")
    if len(raw) > MAX_SLIDES:
        raise ValueError(f"帧数过多（{len(raw)} > {MAX_SLIDES}）")
    slides = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            slides.append(MatrixSlide.from_dict(item))
        except Exception:
            logger.debug("matrix: skip bad slide in library", exc_info=True)
    if not slides:
        raise ValueError("帧库里的帧全部无法解析")
    return slides


def parse_library(data) -> Tuple[List, Dict]:
    """帧库 dict → ([MatrixSlide], meta)；格式不符 → ValueError。

    meta = {"fixture_count": int, "created": str, "count": int}。
    """
    if not isinstance(data, dict):
        raise ValueError("帧库文件内容不是 JSON 对象")
    _header_version(data)
    slides = _slides_from_raw(data.get("slides"))
    meta = {
        "fixture_count": _safe_int(data.get("fixture_count")),
        "created": str(data.get("created", "") or ""),
        "count": len(slides),
    }
    return slides, meta


def _safe_int(v) -> int:
    """任意值 → int（失败 0；helper）。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def export_frame_library(panel, path: Optional[str] = None) -> int:
    """面板当前帧序列 → .json 帧库；返回写出帧数（0=用户取消，-1=失败）。"""
    engine = getattr(panel, "_engine", None)
    slides = list(getattr(engine, "slides", []) or []) if engine is not None else []
    if not slides:
        _warn(panel, "导出帧库", "当前没有帧可导出（先在幻灯片窗口做几帧）。")
        return -1
    if path is None:
        from PyQt6.QtWidgets import QFileDialog
        path, _flt = QFileDialog.getSaveFileName(
            panel, "导出矩阵帧库", "matrix_frames.json", "帧库文件 (*.json)")
    if not path:
        return 0
    payload = library_payload(slides, fixture_count=len(getattr(panel, "_fixtures", []) or []))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        _warn(panel, "导出帧库", f"写入失败：\n{exc}")
        return -1
    return len(slides)


def _read_library_file(path: str):
    """读 + 解析帧库文件；失败 → (None, 错误文本)（helper）。"""
    if not os.path.isfile(path):
        return None, f"文件不存在：{path}"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), ""
    except (OSError, ValueError) as exc:
        return None, f"读取/解析失败：\n{exc}"


def import_frame_library(panel, path: Optional[str] = None) -> int:
    """.json 帧库 → 追加到当前帧序列；返回新增帧数（0=取消，-1=失败）。"""
    if path is None:
        from PyQt6.QtWidgets import QFileDialog
        path, _flt = QFileDialog.getOpenFileName(
            panel, "导入矩阵帧库", "", "帧库文件 (*.json);;所有文件 (*.*)")
    if not path:
        return 0
    data, err = _read_library_file(path)
    if data is None:
        _warn(panel, "导入帧库", err)
        return -1
    try:
        slides, meta = parse_library(data)
    except ValueError as exc:
        _warn(panel, "导入帧库", str(exc))
        return -1
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return -1
    from .matrix_gray_frames import _append_slides_to_engine
    if _append_slides_to_engine(panel, slides) < 0:
        return -1
    _notify_fixture_mismatch(panel, meta)
    return len(slides)


def _notify_fixture_mismatch(panel, meta: Dict) -> None:
    """设备数与帧库记录不一致时提示（帧仍已导入；helper）。"""
    saved = int(meta.get("fixture_count", 0) or 0)
    now = len(getattr(panel, "_fixtures", []) or [])
    if saved and now and saved != now:
        _warn(panel, "导入帧库",
              f"已导入 {meta.get('count', 0)} 帧；注意：帧库记录 {saved} 台设备，"
              f"当前设备组 {now} 台，未匹配的 id 不会亮。")


def _warn(panel, title: str, text: str) -> None:
    """面板提示（非 Qt 宿主/异常时静默；helper）。"""
    try:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(panel, title, text)
    except Exception:
        logger.debug("matrix: warn dialog failed", exc_info=True)


__all__ = ["LIBRARY_FORMAT", "LIBRARY_VERSION", "MAX_SLIDES",
           "export_frame_library", "import_frame_library", "library_payload",
           "parse_library"]
