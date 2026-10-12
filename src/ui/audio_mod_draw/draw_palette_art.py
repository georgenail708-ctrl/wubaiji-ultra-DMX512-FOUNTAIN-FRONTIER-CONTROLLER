# -*- coding: utf-8 -*-
"""Draw 左栏源卡库美术通道（R183-H）—— 源卡/模板/预设库的 B0 资产配对。

从 `draw_palette.py` 拆出（该文件 500 行门禁），**数据唯一真源仍在此**：

* `SOURCE_ART` / `TEMPLATE_ART`：源卡与预设模板 key → manifest art id（语义配对；
  未登记 key → 空 → 条目照旧 tabler 图标，零回归）。
* `art_id_for_payload`：条目 payload（``"source:<key>"``/``"template:<key>"``）→ id。
* `load_art_pixmap_16` / `load_art_pixmap_20`：16px 缩略 / 20px 空态小插画
  （muted 灰）；失败一律返回 None 由调用方回退，**永不抛异常**。

资产落库/许可见 ``src/ui/art_assets/manifest.json`` 与 ``LICENSES.md``；
加载一律走 ``art_asset_library``（勿直接拼路径）。
"""
from __future__ import annotations

try:                                                 # B0 资产库（缺失 → 全回退图标）
    from .. import art_asset_library as _art_lib
except Exception:                                    # pragma: no cover
    _art_lib = None

try:                                                 # 主题令牌（缺失 → 内置灰兜底）
    from ..qascade_theme import TEXT_DISABLED as _TEXT_DISABLED
    from ..qascade_theme import TEXT_SECONDARY as _TEXT_SECONDARY
except Exception:                                    # pragma: no cover
    _TEXT_SECONDARY, _TEXT_DISABLED = "#999999", "#8B8B8B"

__all__ = ["SOURCE_ART", "TEMPLATE_ART", "art_id_for_payload",
           "load_art_pixmap_16", "load_art_pixmap_20"]

#: 源卡 B0 美术通道：源 key → manifest art id（语义配对）。
SOURCE_ART: dict = {
    "src_beat": "eff_pulse_beat",     # 心跳脉冲 = 节拍强度
    "src_bpm": "clock",               # cards/clock = BPM 相位
    "src_low": "pump",                # cards/pump（manual-water-pump）= 低频涌动
    "src_high": "eff_high_sparkle",   # 高频闪烁（stars）
    "src_ratio": "percent",           # cards/percent = 频段能量比
}

#: 预设模板 B0 美术通道：模板 key → manifest art id。
TEMPLATE_ART: dict = {
    "tpl_aqua": "eff_swell",          # big-wave = 水景涌动
    "tpl_breath": "eff_breath_slow",  # wind = 灯光呼吸
    "tpl_groove": "pump",             # manual-water-pump = 泵速律动
}

#: 预设库空态小插画（Open Doodles；20px muted 灰）。
PRESET_EMPTY_ART = "empty_coffee"


def art_id_for_payload(payload: str) -> str:
    """条目 payload（"source:<key>"/"template:<key>"）→ B0 art id（无 → ""）。"""
    head, _, tail = str(payload).partition(":")
    if head == "source":
        return SOURCE_ART.get(tail, "")
    if head == "template":
        return TEMPLATE_ART.get(tail, "")
    return ""


def _pixmap(art_id: str, size: int, color: str):
    """B0 资产 → QPixmap（失败 → None；公共渲染/回退语义见模块 docstring）。"""
    if not art_id or _art_lib is None:
        return None
    try:
        pm = _art_lib.load_art_pixmap(art_id, size, size, color)
    except Exception:                                # pragma: no cover
        return None
    return pm if (pm is not None and not pm.isNull()) else None


def load_art_pixmap_16(art_id: str, color: str = ""):
    """16px B0 美术缩略（默认正文灰；失败 → None，调用方回退 tabler 图标）。"""
    return _pixmap(art_id, 16, color or _TEXT_SECONDARY)


def load_art_pixmap_20(art_id: str, color: str = ""):
    """20px B0 空态小插画（默认禁用灰；失败 → None）。"""
    return _pixmap(art_id, 20, color or _TEXT_DISABLED)


def translucent_grab(widget, opacity: float = 0.65):
    """控件 → 半透明拖拽影像（01 清单 B1；与 honeycomb_grid.make_drag_pixmap 同手法：
    grab 所见即所得 + 保 DPR；失败 → None，调用方走裸光标）。"""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPainter, QPixmap
        pm = widget.grab()
        if pm.isNull():
            return None
        out = QPixmap(pm.size())
        out.setDevicePixelRatio(pm.devicePixelRatio())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setOpacity(max(0.2, min(1.0, float(opacity))))
        p.drawPixmap(0, 0, pm)
        p.end()
        return out
    except Exception:
        return None
