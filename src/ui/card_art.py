# -*- coding: utf-8 -*-
"""卡面画作（01 清单 D1「卡面应该是一幅画，不是图标」）—— 公共领域画作 cover 底图。

资源：``art_assets/paintings/``（The Metropolitan Museum of Art Open Access；作品
本身因作者 1890 年去世属公共领域，Met 对其数字影像以 CC0 发布；逐件登记见
``art_assets/manifest.json`` 与 ``LICENSES.md``）。

加载只走 ``art_asset_library.load_art_pixmap``（既有 LRU + DPR 通道；按画作原
宽高比请求 = 满幅无留边），本模块在其上做两件事：

1. **稳定取景**：按卡片 key 的 md5（非进程随机 ``hash``）选画作、放大倍率与
   取景偏移——同一种卡每次打开都是同一幅画的同一局部，不同卡不同局部；
2. **cover 裁切 + 二级缓存**：卡片宽高比 ≠ 画作宽高比，按 (key, 尺寸, dpr)
   缓存成品位图（paint 热路径零 IO、零重采样）。

绘制侧 ``paint_face(p, rect, key)``：低透明（0.35）画在卡片内容区，由调用方
负责圆角裁切；未登记/加载失败静默返回 False（调用方走既有程序化底图兜底）。
画作池只有 2 幅——缺口清单见交付说明 D2。
"""
from __future__ import annotations

import hashlib
from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap

__all__ = ["PAINTINGS", "FACE_OPACITY", "FACE_OPACITY_LOD", "pick_painting",
           "view_window", "face_pixmap", "paint_face", "clear_face_cache"]

#: 画作池：(manifest id, 原图宽, 原图高)——尺寸与 art_assets/paintings/*.jpg 一致
PAINTINGS = (
    ("paint_met_gogh_wheat_field_with_cypresses", 599, 477),
    ("paint_met_gogh_cypresses", 495, 624),
)
#: 卡面透明度（02 规范 §8：~0.35，不压字）；缩小到 LOD 时画面即主体，提一档
FACE_OPACITY = 0.35
FACE_OPACITY_LOD = 0.5
#: 成品位图相对世界尺寸的超采样倍率（缩放到 2× 仍清晰）
FACE_RES = 2.0
_CACHE: dict = {}
_CACHE_MAX = 256


def _digest(key: str) -> int:
    return int(hashlib.md5(str(key).encode("utf-8")).hexdigest()[:8], 16)


def pick_painting(key: str) -> tuple:
    """卡片 key → (art_id, 原宽, 原高)（稳定散列选画）。"""
    return PAINTINGS[_digest(key) % len(PAINTINGS)]


def view_window(key: str, src_w: float, src_h: float, dst_w: float, dst_h: float) -> tuple:
    """cover 取景窗（源图逻辑像素）：放大 1.0-1.6×，偏移由散列决定，恒在图内。"""
    d = _digest(key)
    zoom = 1.0 + ((d >> 8) % 7) / 10.0
    aspect = max(1e-6, float(dst_w)) / max(1e-6, float(dst_h))
    w = src_w / zoom
    h = w / aspect
    if h > src_h / zoom:
        h = src_h / zoom
        w = h * aspect
    fx = ((d >> 12) % 101) / 100.0
    fy = ((d >> 20) % 101) / 100.0
    return (fx * (src_w - w), fy * (src_h - h), w, h)


def _dpr() -> float:
    try:
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        return max(1.0, float(scr.devicePixelRatio())) if scr is not None else 1.0
    except Exception:
        return 1.0


def _render_face(key: str, dst_w: int, dst_h: int, dpr: float) -> Optional[QPixmap]:
    """按取景窗把画作 cover 绘入 dst 尺寸位图（失败 → None，不抛）。"""
    try:
        from .art_asset_library import load_art_pixmap
        art_id, sw, sh = pick_painting(key)
        src = load_art_pixmap(art_id, sw, sh)
        if src is None or src.isNull():
            return None
        sd = float(src.devicePixelRatio() or 1.0)
        x, y, vw, vh = view_window(key, sw, sh, dst_w, dst_h)
        out = QPixmap(max(1, int(dst_w * dpr)), max(1, int(dst_h * dpr)))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(QRectF(0.0, 0.0, float(dst_w), float(dst_h)), src,
                     QRectF(x * sd, y * sd, vw * sd, vh * sd))
        p.end()
        return out
    except Exception:
        return None


def face_pixmap(key: str, w: int, h: int) -> Optional[QPixmap]:
    """卡片 key + 逻辑尺寸 → 卡面位图（带 DPR；缓存命中零开销）。"""
    iw, ih = int(w), int(h)
    if iw < 8 or ih < 8:
        return None
    dpr = _dpr()
    ck = (str(key), iw, ih, round(dpr, 2))
    if ck in _CACHE:
        return _CACHE[ck]
    pm = _render_face(str(key), iw, ih, dpr)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[ck] = pm
    return pm


def paint_face(p: QPainter, rect: QRectF, key: str, *,
               opacity: float = FACE_OPACITY) -> bool:
    """在 rect（世界/屏幕坐标均可）内低透明绘制卡面画作；返回是否画了。"""
    if rect is None or rect.width() < 8 or rect.height() < 8:
        return False
    pm = face_pixmap(key, int(rect.width() * FACE_RES), int(rect.height() * FACE_RES))
    if pm is None or pm.isNull():
        return False
    p.save()
    p.setOpacity(p.opacity() * max(0.0, min(1.0, float(opacity))))
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(rect, pm, QRectF(pm.rect()))
    p.restore()
    return True


def clear_face_cache() -> None:
    """清成品缓存（换屏 DPR / 资产热替换时调用）。"""
    _CACHE.clear()
