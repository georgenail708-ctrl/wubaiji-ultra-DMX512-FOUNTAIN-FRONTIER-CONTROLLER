# -*- coding: utf-8 -*-
"""art_asset_library.py — B0 真实美术资产库加载 API（R183）。

与 ``main_icon_library`` 的区别（勿混用）：

* ``main_icon_library``：**图标**（tabler 24px 线稿，工具栏/按钮用）。
* 本模块：**美术资产**（卡面底图 / 效果卡面 / 空态插画 / 纹理 / 面板头图），
  落库在 ``src/ui/art_assets/``，逐件登记来源与许可（见 ``manifest.json``
  与 ``LICENSES.md``），全部为 CC0 / MIT / CC-BY / Open Doodles 可商用许可。

公开 API::

    art_path(id)                       -> Path | None     资产路径（找不到返回 None）
    load_art_pixmap(id, w, h)          -> QPixmap          等比适配到 w×h（找不到返回空 QPixmap）
    load_art_icon(id, size, color)     -> QIcon            tintable 资产按 color 染色
    has_art(id) / art_ids(prefix=None) -> bool / list[str]
    art_meta(id)                       -> dict | None      manifest 原始条目（许可/来源）

渲染约定（与 ``main_icon_library`` 同机制）：

* SVG 走 ``QSvgRenderer`` + ``currentColor`` 替换（落库前已把可染色资产统一
  归一成 ``currentColor``，见 ``normalize`` 字段），HiDPI 按 DPR 栅格化。
* **静默回退**：未登记 id、文件缺失、SVG 解析失败、位图解码失败 —— 一律返回
  空 ``QPixmap``/``QIcon``（不抛异常、不弹窗），调用方自行决定是否退回程序化底图
  （如 ``clip_studio.studio_art`` 的 QPainter 方案）。
* 结果进 LRU 缓存（``_pixmap_cached``），DPR 参与缓存键，缩放屏不发虚。

新增资产纪律见 ``src/ui/art_assets/README.md``——**必须先有许可证据**，
再登记 manifest，最后才允许在本模块引用；本模块不内置任何资产清单副本。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

try:
    from . import qascade_theme as theme
except ImportError:  # 允许以 src.ui.art_asset_library 绝对路径方式导入
    from src.ui import qascade_theme as theme

logger = logging.getLogger(__name__)

__all__ = [
    "ART_DIR", "MANIFEST_PATH", "DEFAULT_ART_COLOR",
    "art_path", "art_meta", "has_art", "art_ids", "load_art_pixmap",
    "load_art_icon", "clear_art_cache",
]

#: 资产根目录（本文件同级 art_assets/）
ART_DIR = Path(__file__).resolve().parent / "art_assets"
MANIFEST_PATH = ART_DIR / "manifest.json"

#: 默认染色 = 正文主色（与 main_icon_library.DEFAULT_ICON_COLOR 同源）
DEFAULT_ART_COLOR = theme.TEXT_PRIMARY

_SVG_SUFFIXES = (".svg",)


# ── manifest 读取（模块级一次，文件缺失时安全空表）──────────────────

@lru_cache(maxsize=1)
def _manifest() -> Dict[str, dict]:
    """读 manifest.json → {id: entry}；任何读取/解析失败 → 空表（静默）。"""
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("art_assets manifest 不可读: %s", MANIFEST_PATH)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, dict] = {}
    for key, entry in raw.items():
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out[str(key)] = entry
    return out


def art_meta(art_id) -> Optional[dict]:
    """id 的 manifest 条目（许可/来源/作者）；未登记返回 None。"""
    return _manifest().get(str(art_id or ""))


def art_path(art_id) -> Optional[Path]:
    """id → 资产文件路径；未登记或文件不存在 → None。"""
    entry = art_meta(art_id)
    if entry is None:
        return None
    path = ART_DIR / entry["path"]
    return path if path.is_file() else None


def has_art(art_id) -> bool:
    """id 是否有可用资产文件（未登记/缺失 → False）。"""
    return art_path(art_id) is not None


def art_ids(prefix: str = "") -> Tuple[str, ...]:
    """全部已登记 id（可给前缀过滤，如 ``"empty_"``、``"eff_"``）；字典序。"""
    pre = str(prefix or "")
    return tuple(sorted(k for k in _manifest() if k.startswith(pre)))


def _tintable(art_id: str) -> bool:
    entry = art_meta(art_id)
    return bool(entry and entry.get("tintable"))


# ── 渲染 ────────────────────────────────────────────────────────────

def _screen_dpr() -> float:
    """主屏 devicePixelRatio（与 main_icon_library 同策略：HiDPI 物理像素栅格化）。"""
    try:
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        dpr = float(scr.devicePixelRatio()) if scr is not None else 1.0
    except Exception:
        dpr = 1.0
    return max(1.0, min(dpr, 3.0))


def _fit_rect(src_w: float, src_h: float, dst_w: float, dst_h: float) -> QRectF:
    """按等比缩放在 dst 内居中（含超采样后的物理像素尺寸）。"""
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return QRectF()
    scale = min(dst_w / src_w, dst_h / src_h)
    w, h = src_w * scale, src_h * scale
    return QRectF((dst_w - w) / 2.0, (dst_h - h) / 2.0, w, h)


def _render_svg(path, art_id: str, color: str, *, pw: int, ph: int,
                dpr: float) -> QPixmap:
    """SVG 渲染（_pixmap_cached 拆出压 CC）；失败 → 空 QPixmap。"""
    try:
        svg = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("art SVG 读取失败: %s", path)
        return QPixmap()
    if _tintable(art_id):
        svg = svg.replace("currentColor", str(color or DEFAULT_ART_COLOR))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        logger.debug("art SVG 解析失败: %s", path)
        return QPixmap()
    vb = renderer.viewBoxF()
    src_w = vb.width() if vb.width() > 0 else float(renderer.defaultSize().width())
    src_h = vb.height() if vb.height() > 0 else float(renderer.defaultSize().height())
    target = _fit_rect(src_w or 1.0, src_h or 1.0, float(pw), float(ph))
    pix = QPixmap(pw, ph)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, target)
    painter.end()
    return pix


def _render_bitmap(path, pw: int, ph: int, dpr: float) -> QPixmap:
    """位图（JPG/PNG）解码+等比缩放（_pixmap_cached 拆出压 CC）。"""
    pix = QPixmap(str(path))
    if pix.isNull():
        logger.debug("art 位图解码失败: %s", path)
        return QPixmap()
    scaled = pix.scaled(pw, ph, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(pw, ph)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(int((pw - scaled.width()) / 2),
                       int((ph - scaled.height()) / 2), scaled)
    painter.end()
    return out


@lru_cache(maxsize=2048)
def _pixmap_cached(art_id: str, w: int, h: int, color: str, dpr_key: float) -> QPixmap:
    """实际渲染（缓存键含 DPR 与颜色）。任何失败 → 空 QPixmap。"""
    path = art_path(art_id)
    if path is None:
        logger.debug("art 资产不存在: %s", art_id)
        return QPixmap()
    dpr = max(1.0, float(dpr_key))
    pw, ph = int(round(w * dpr)), int(round(h * dpr))
    if pw <= 0 or ph <= 0:
        return QPixmap()
    if path.suffix.lower() in _SVG_SUFFIXES:
        return _render_svg(path, art_id, color, pw=pw, ph=ph, dpr=dpr)
    return _render_bitmap(path, pw, ph, dpr)


def load_art_pixmap(
    art_id,
    w: int = 128,
    h: int = 128,
    color: str = DEFAULT_ART_COLOR,
) -> QPixmap:
    """加载资产为 w×h 位图（等比适配居中，透明底）。

    :param art_id: manifest 中登记的 id（如 ``"arrow_ltr"``/``"empty_float"``）
    :param w, h:   目标逻辑像素尺寸（**须为正**；非正值视为非法请求 → 空 QPixmap）
    :param color:  染色（仅 ``tintable`` 资产生效；位图与多色插画忽略）
    :return:       渲染好的 QPixmap；**任何失败返回空 QPixmap（不抛异常）**
    """
    try:
        iw, ih = int(w), int(h)
    except (TypeError, ValueError):
        return QPixmap()
    if iw <= 0 or ih <= 0:
        return QPixmap()
    return _pixmap_cached(str(art_id or ""), iw, ih, str(color or DEFAULT_ART_COLOR),
                          _screen_dpr())


@lru_cache(maxsize=1024)
def _icon_cached(art_id: str, size: int, color: str, dpr_key: float) -> QIcon:
    pix = _pixmap_cached(art_id, size, size, color, dpr_key)
    return QIcon(pix) if not pix.isNull() else QIcon()


def load_art_icon(
    art_id,
    size: int = 24,
    color: str = DEFAULT_ART_COLOR,
) -> QIcon:
    """加载资产为正方形 QIcon（tintable 资产按 color 染色）。

    未登记/缺失/渲染失败 → 空 QIcon（调用方走自己的回退链）。
    """
    try:
        s = max(8, int(size))
    except (TypeError, ValueError):
        s = 24
    return _icon_cached(str(art_id or ""), s, str(color or DEFAULT_ART_COLOR), _screen_dpr())


def clear_art_cache() -> None:
    """清空 LRU 缓存（测试/主题切换用；不影响磁盘资产）。"""
    _pixmap_cached.cache_clear()
    _icon_cached.cache_clear()
