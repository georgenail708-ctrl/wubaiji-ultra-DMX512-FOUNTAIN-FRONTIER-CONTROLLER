from __future__ import annotations

import os
import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QRectF, Qt, QByteArray, QPointF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QBrush, QPixmap
from PyQt6.QtSvg import QSvgRenderer

try:
    from . import qascade_theme as theme
except ImportError:  # 允许以 src.ui.effect_icon_library 绝对路径方式导入
    from src.ui import qascade_theme as theme


_TABLER_DIR = os.path.join(
    os.path.dirname(__file__),
    "icon_assets",
    "tabler",
    "package",
    "icons",
    "outline",
)

# ── 扁平卡片风（restyle_spec §4-C）───────────────────────────────
# 渐变彩虹底已拍平为纯 BG_EDITOR + 1px 白描边；FX 特征 11 色只允许落在
# glyph / 右下角角标小圆点上，不再做大面积填充（背景恒为中性与字无关）。


def stable_visual_seed(text: Any) -> int:
    total = 0
    for idx, ch in enumerate(str(text or "")):
        total = (total + (idx + 1) * ord(ch)) % 1000003
    return total


def icon_color(seed: int, offset: int = 0, sat: int = 170, val: int = 245) -> QColor:
    """历史遗留：seed→HSV 连续色。重皮后主路径不再使用，仅保留签名兼容。"""
    return QColor.fromHsv((int(seed) * 37 + offset * 61) % 360, sat, val)


#: FX 特征编码 11 色（restyle_spec §4-C：每个 hue 家族保留，只上 glyph/角标）。
#: 取色规则 = stable_visual_seed(identity) % 11 —— key→seed 的确定性映射不变。
FX_FEATURE_COLORS: tuple[str, ...] = (
    theme.FOCUS_BLUE,      # 蓝 #71A8FF —— 音频/通用强调
    "#54B9C9",             # 青 —— 水型/泵
    "#56B65B",             # 绿 bright_success —— 生长类
    theme.ACTIVE_ORANGE,   # 橙 #FFA028 —— 运动/追逐
    theme.BRIGHT_ERROR,    # 红 #E36855 —— 频闪/爆闪
    theme.BRIGHT_WARN,     # 金 #E6B455 —— 灯光亮度
    theme.KEYFRAME_GOLD,   # 深金 #B29700 —— 脉冲节拍
    "#9B6DD6",             # 紫 —— 变形/色相
    "#D179C6",             # 洋红 —— 彩虹颜色类
    "#7091C9",             # 蓝灰 fx —— 矩阵/图案
    "#8A8A8A",             # 中性灰 —— 常规兜底
)


def _marker_from_fields(*fields: Any) -> str:
    return " ".join(str(x or "") for x in fields).lower()


def _preset_marker(obj: Any) -> str:
    return _marker_from_fields(
        getattr(obj, "id", ""),
        getattr(obj, "name", ""),
        getattr(obj, "category", ""),
        getattr(obj, "description", ""),
        " ".join(str(t) for t in (getattr(obj, "tags", []) or [])),
        " ".join(str(ch) for ch in (getattr(obj, "target_channels", []) or [])),
        " ".join(str(getattr(p, "key", "")) for p in (getattr(obj, "params", []) or [])),
        " ".join(str(getattr(p, "label", "")) for p in (getattr(obj, "params", []) or [])),
    )


def _has_any(marker: str, words: tuple[str, ...]) -> bool:
    return any(w in marker for w in words)


_EQ_TOKEN_RE = re.compile(r"(?<![a-z0-9])eq(?![a-z0-9])")


def _has_eq_token(marker: str) -> bool:
    """整词 eq（均衡器）：前后须为字母数字之外（含下划线/连字符），
    避免命中 spatial_freq / cur_fseq_ 等含 "eq" 子串的非音频预设。"""
    return bool(_EQ_TOKEN_RE.search(marker))


@lru_cache(maxsize=2048)
def _icon_exists(icon_name: str) -> bool:
    safe = "".join(ch for ch in str(icon_name or "") if ch.isalnum() or ch == "-")
    return bool(safe) and os.path.exists(os.path.join(_TABLER_DIR, f"{safe}.svg"))


def _pick_icon(seed_text: Any, names: tuple[str, ...]) -> str:
    """从候选池取第一个可用的 tabler 字形（restyle_spec §10.5 图标语义化）。

    历史版本按 seed 在池内抽签（同族效果各自随机一个字形）；重皮后逐条化：
    规则池首项即该族的**规范语义字形**（wave→wave-sine、chase→arrows-right、
    pulse→activity…），可用性过滤后顺序取首个，不再做随机洗牌。
    ``seed_text`` 参数保留仅为调用点/缓存键兼容。
    """
    del seed_text
    available = [name for name in names if _icon_exists(name)]
    if not available:
        return "sparkles"
    return available[0]


def _preset_pool_key(preset: Any, marker: str) -> str:
    return "|".join(
        [
            str(getattr(preset, "id", "") or ""),
            str(getattr(preset, "name", "") or ""),
            str(getattr(preset, "category", "") or ""),
            marker,
        ]
    )


# 语义字形池（restyle_spec §10.5）：**首项 = 该族规范语义 glyph**
# （wave-sine / arrows-right / activity / palette / bolt / rotate / droplet /
#   layout-grid…），逐条化后 _pick_icon 顺序取首个可用项。
_WAVE_ICONS = (
    "wave-sine", "wave-square", "wave-saw-tool", "waves-electricity",
    "arrow-wave-right-up", "arrow-wave-right-down", "arrow-wave-left-up",
    "arrow-wave-left-down", "ripple", "chart-line", "chart-area-line",
)
_CHASE_ICONS = (
    "arrows-right", "route", "route-2", "route-square", "route-alt-right", "route-alt-left",
    "map-route", "arrow-right-tail", "arrow-forward", "arrow-iteration",
    "arrows-shuffle", "arrows-random", "arrows-split", "arrows-join",
    "arrows-right-down", "player-track-next",
)
_AUDIO_ICONS = (
    "activity-heartbeat", "chart-bar", "chart-bar-popular", "chart-dots",
    "chart-histogram", "chart-column", "chart-arrows-vertical", "music",
    "microphone", "volume-2", "headphones", "speakerphone",
)
_COLOR_ICONS = (
    "palette", "color-swatch", "color-picker", "rainbow", "gradienter",
    "paint", "brush", "contrast", "droplet-half-2", "sun",
)
_LIGHT_ICONS = (
    "bulb", "circuit-bulb", "brightness-up", "brightness-down",
    "brightness-half", "sun", "flare", "sparkles", "eye-spark",
)
_STROBE_ICONS = (
    "bolt", "droplet-bolt", "adjustments-bolt", "battery-spark",
    "sparkles", "sparkle", "flare", "cloud-bolt",
)
_MOTOR_ICONS = (
    "rotate", "rotate-2", "rotate-clockwise", "rotate-clockwise-2",
    "rotate-dot", "rotate-rectangle", "view-360", "propeller", "arrows-move",
    "arrows-diagonal", "arrow-loop-right", "arrow-loop-left", "target",
    "focus", "aperture",
)
_PUMP_ICONS = (
    "droplet", "fountain", "droplets", "bucket-droplet", "droplet-up",
    "droplet-down", "droplet-plus", "droplet-half-2", "ripple",
)
_VALVE_ICONS = (
    "switch-2", "toggle-right", "droplet-half-2", "droplet-check", "droplet-pause",
    "droplet-cog", "droplet-minus", "droplet-plus",
)
_MATRIX_ICONS = (
    "layout-grid", "grid-dots", "grid-4x4", "grid-3x3", "grid-pattern", "grid-scan",
    "chart-grid-dots", "border-outer", "scan-position",
    "scan", "shape", "shape-2", "shape-3", "apps",
)
_FADE_ICONS = (
    "chart-area-line", "chart-line", "chart-arrows-vertical", "trending-up",
    "trending-down", "arrows-transfer-up", "arrows-transfer-down",
    "adjustments-horizontal",
)
_PULSE_ICONS = (
    "activity", "activity-heartbeat", "heartbeat", "refresh",
    "repeat", "clock-bolt", "alarm", "sparkle",
)
_GENERAL_ICONS = (
    "sparkles", "sparkle", "sparkles-2", "wand", "aperture", "target",
    "focus", "adjustments-spark", "settings-spark", "slideshow",
)


def effect_icon_name_for_preset(preset: Any) -> str:
    marker = _preset_marker(preset)
    key = _preset_pool_key(preset, marker)
    icon = str(getattr(preset, "icon", "") or "").lower()
    if _has_any(marker, ("saw", "锯齿")):
        return "wave-saw-tool"
    if _has_any(marker, ("square", "方波", "gate", "门控")):
        return "wave-square"
    if _has_any(marker, ("sine", "cosine", "正弦", "余弦")):
        return "wave-sine"
    if _has_any(marker, ("ripple", "涟漪")):
        return "ripple"
    # ── 电机水型扩充(2026-08-12): 具象语义规则 ──
    # 编组规则全放 motor 规则之前(类别含"电机"会被 motor 规则截胡),
    # token 均为新效果独有词(已核旧名无碰撞)。
    if _has_any(marker, ("星夜",)):
        return "stars"
    if _has_any(marker, ("四季",)):
        return "flower"
    if _has_any(marker, ("揽月",)):
        return "moon"
    if _has_any(marker, ("吞卷",)):
        return "tornado"
    if _has_any(marker, ("飞雪", "雪片")):
        return "snowflake"
    if _has_any(marker, ("太极",)):
        return "yin-yang"
    if _has_any(marker, ("风铃",)):
        return "bell"
    if _has_any(marker, ("蒲公英",)):
        return "flower"
    if _has_any(marker, ("星轨",)):
        return "star"
    if _has_any(marker, ("流星",)):
        return "meteor"
    if _has_any(marker, ("月相", "盈缺")):
        return "moon"
    if _has_any(marker, ("蜂巢", "六边")):
        return "hexagon"
    if _has_any(marker, ("星座",)):
        return "stars"
    if _has_any(marker, ("飞瀑",)):
        return "droplet"
    if _has_any(marker, ("极光",)):
        return "wind"
    if _has_any(marker, ("洄澜",)):
        return "ripple-up"
    if _has_any(marker, ("绸带",)):
        return "bow"
    if _has_any(marker, ("编钟",)):
        return "bell"
    if _has_any(marker, ("水车",)):
        return "windmill"
    if _has_any(marker, ("纸鸢",)):
        return "triangle"
    if _has_any(marker, ("烟花",)):
        return "flare"
    if _has_any(marker, ("滑音",)):
        return "music"
    if _has_any(marker, ("水母",)):
        return "droplets"
    if _has_any(marker, ("苍鹰",)):
        return "feather"
    if _has_any(marker, ("罗盘",)):
        return "compass"
    if _has_any(marker, ("audio", "bpm", "音频", "节拍", "频谱", "vu")) or _has_eq_token(marker):
        if _has_any(marker, ("bar", "条", "柱", "band", "频段")):
            return _pick_icon(key, ("chart-bar", "chart-bar-popular", "chart-column", "chart-histogram"))
        return _pick_icon(key, _AUDIO_ICONS)
    if _has_any(marker, ("strobe", "频闪", "闪烁", "flash", "爆闪")):
        return _pick_icon(key, _STROBE_ICONS)
    if _has_any(marker, ("color", "rgb", "彩虹", "颜色", "色", "hue", "palette")):
        if _has_any(marker, ("rainbow", "彩虹")):
            return "rainbow"
        return _pick_icon(key, _COLOR_ICONS)
    if _has_any(marker, ("pump", "fountain", "泵", "跑泉", "喷泉", "water")):
        if _has_any(marker, ("chase", "跑", "追逐", "sequence")):
            return _pick_icon(key, ("fountain", "route", "route-2", "droplets", "ripple"))
        return _pick_icon(key, _PUMP_ICONS)
    if _has_any(marker, ("valve", "阀")):
        return _pick_icon(key, _VALVE_ICONS)
    if _has_any(marker, ("motor", "pan", "tilt", "电机", "摇头", "位置", "position")):
        if _has_any(marker, ("cue", "提示动作", "goto", "定位", "路径")):
            return _pick_icon(key, ("route", "route-2", "map-route", "target", "focus", "crosshair"))
        if _has_any(marker, ("rotate", "旋转", "circle", "圆")):
            return _pick_icon(key, ("rotate", "rotate-2", "rotate-clockwise", "view-360", "rotate-dot"))
        return _pick_icon(key, _MOTOR_ICONS)
    if _has_any(marker, ("matrix", "pattern", "矩阵", "图案", "像素", "点阵")):
        if _has_any(marker, ("scan", "扫描")):
            return _pick_icon(key, ("grid-scan", "scan", "route-scan", "line-scan", "scan-position"))
        if _has_any(marker, ("border", "边框")):
            return "border-outer"
        if _has_any(marker, ("arrow", "箭头", "方向")):
            return _pick_icon(key, ("arrow-up-right", "arrow-right", "arrow-wave-right-up", "arrows-up-right"))
        return _pick_icon(key, _MATRIX_ICONS)
    if _has_any(marker, ("chase", "跑灯", "追逐", "tail", "流动", "扫")):
        if _has_any(marker, ("center", "中心")):
            return "arrows-split"
        if _has_any(marker, ("edge", "边缘")):
            return "arrows-join"
        if _has_any(marker, ("random", "随机")):
            return _pick_icon(key, ("arrows-shuffle", "arrows-shuffle-2", "arrows-random"))
        return _pick_icon(key, _CHASE_ICONS)
    if _has_any(marker, ("fader", "fade", "渐变", "淡入", "淡出")):
        return _pick_icon(key, _FADE_ICONS)
    if _has_any(marker, ("wave", "波")) or icon in {"~", "〜"}:
        return _pick_icon(key, _WAVE_ICONS)
    if _has_any(marker, ("open", "开合", "shutter")):
        return _pick_icon(key, ("arrows-horizontal", "arrows-left-right", "arrows-split", "arrows-join"))
    if _has_any(marker, ("pulse", "脉冲", "beat", "心跳")):
        return _pick_icon(key, _PULSE_ICONS)
    if _has_any(marker, ("joystick", "摇杆")):
        return "arrows-move"   # tabler 无 joystick 字形，摇杆→方向移动语义
    if _has_any(marker, ("light", "dimmer", "灯光", "亮度", "intensity", "level")):
        return _pick_icon(key, _LIGHT_ICONS)
    return _pick_icon(key, _GENERAL_ICONS)


def matrix_icon_name_for_mode(mode_id: Any, label: Any, category: Any) -> str:
    marker = _marker_from_fields(mode_id, label, category)
    key = f"{mode_id}|{label}|{category}"
    if _has_any(marker, ("audio", "vu", "频谱", "音频", "eq", "band")):
        if _has_any(marker, ("bar", "条", "eq", "柱")):
            return _pick_icon(key, ("chart-bar", "chart-column", "chart-histogram", "chart-arrows-vertical"))
        if _has_any(marker, ("waveform", "波形")):
            return _pick_icon(key, ("wave-sine", "waves-electricity", "activity-heartbeat"))
        return _pick_icon(key, _AUDIO_ICONS)
    if _has_any(marker, ("screen", "video", "采样", "后处理")):
        return _pick_icon(key, ("device-desktop", "device-desktop-bolt", "screen-share", "scan", "photo-scan"))
    if _has_any(marker, ("particle", "粒子", "physics", "物理")):
        return _pick_icon(key, ("atom", "atom-2", "chart-bubble", "circles", "meteor", "sparkles"))
    if _has_any(marker, ("shape", "morph", "形变", "变形", "形状")):
        return _pick_icon(key, ("shape", "shape-2", "shape-3", "triangle-square-circle", "square-rotated", "geometry"))
    if _has_any(marker, ("asym", "非对称")):
        return _pick_icon(key, ("arrows-random", "arrows-shuffle", "arrows-diff", "arrows-diagonal"))
    if _has_any(marker, ("scan", "扫描", "门控")):
        return _pick_icon(key, ("grid-scan", "line-scan", "route-scan", "scan-position", "scan-traces"))
    if _has_any(marker, ("dot", "点阵", "grid", "网格")):
        return _pick_icon(key, ("grid-dots", "chart-grid-dots", "grid-pattern", "grid-4x4", "layout-grid"))
    if _has_any(marker, ("ripple", "涟漪")):
        return "ripple"
    if _has_any(marker, ("wave", "波")):
        return _pick_icon(key, _WAVE_ICONS)
    if _has_any(marker, ("chase", "snake", "追逐", "跑灯")):
        return _pick_icon(key, _CHASE_ICONS)
    if _has_any(marker, ("meteor", "流星")):
        return "meteor"
    if _has_any(marker, ("rain", "雨")):
        return "cloud-rain"
    if _has_any(marker, ("fire", "flame", "火")):
        return "flame"
    if _has_any(marker, ("explode", "burst", "爆")):
        return _pick_icon(key, ("sparkles", "sparkles-2", "flare", "adjustments-spark"))
    if _has_any(marker, ("sym", "对称", "镜像")):
        return "arrows-split"
    return _pick_icon(key, _MATRIX_ICONS)


@lru_cache(maxsize=4096)
def _svg_bytes(icon_name: str, stroke_hex: str) -> bytes | None:
    safe = "".join(ch for ch in str(icon_name or "") if ch.isalnum() or ch == "-")
    path = os.path.join(_TABLER_DIR, f"{safe}.svg")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read()
    except OSError:
        logger.debug("读取SVG图标文件失败: %s", path)
        return None
    data = data.replace('stroke="currentColor"', f'stroke="{stroke_hex}"')
    # 尺寸交给 QSvgRenderer 按目标矩形缩放，不再改写 svg 的 width/height。
    return data.encode("utf-8")


def _draw_badge(p: QPainter, rect: QRectF, seed: int, color: QColor, badge: str = ""):
    """右下角 FX 特征角标（§4-C 允许的特征色落点：角标/glyph）。

    扁平设计语言：
    - 无字母：9px 特征色实心圆点；
    - 有字母：特征色单字直接绘于角部（不再铺 10px 色块底，特征色只落在
      文字/glyph 上）。
    ``seed`` 参数保留仅为签名兼容，不再驱动形状。
    """
    del seed
    text = str(badge or "").strip()[:1]
    if text:
        box = QRectF(rect.right() - 13.0, rect.bottom() - 13.0, 11.0, 11.0)
        font = p.font()
        font.setPixelSize(8)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QPen(color, 1.0))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        return
    dot = QRectF(rect.right() - 12.0, rect.bottom() - 12.0, 9.0, 9.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(dot)


def _draw_accent_bar(p: QPainter, rect: QRectF, color: QColor):
    """卡片左侧 2px 分类/特征竖条（§10.5：分类色仅允许 竖条/9px 圆点/glyph）。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    # 内缩 1px 让竖条完整落在 1px 描边内侧、与 4px 圆角相切
    bar = QRectF(rect.left() + 1.0, rect.top() + 3.0, 2.0, rect.height() - 6.0)
    p.drawRoundedRect(bar, 1.0, 1.0)


def make_tabler_effect_icon(
    icon_name: str,
    identity: Any,
    *,
    size: int = 34,
    badge: str = "",
) -> QIcon:
    """扁平 扁平卡片风效果图标（签名与离线渲染架构不变）。

    - 底：BG_EDITOR #303030 平涂 + 1px rgba(255,255,255,17) 描边、4px 圆角；
    - glyph：tabler 单色 TEXT_PRIMARY；
    - 特征色 FX_FEATURE_COLORS[seed % 11]（seed 由 identity 确定性派生，
      映射关系与旧版一致）只落在左侧 2px 竖条 + 右下角角标
      （9px 圆点 / 特征色单字），不再做大面积填充。
    """
    seed = stable_visual_seed(identity)
    try:
        from .main_icon_library import _screen_dpr
        dpr = _screen_dpr()
    except Exception:
        dpr = 1.0
    pix = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 卡片底：1px 描边内缩半像素避免被裁切；圆角统一 4px。
    rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    p.setPen(QPen(QColor(255, 255, 255, 17), 1.0))
    p.setBrush(QBrush(QColor(theme.BG_EDITOR)))
    p.drawRoundedRect(rect, 4.0, 4.0)

    glyph_color = QColor(theme.TEXT_PRIMARY)
    svg = _svg_bytes(icon_name, glyph_color.name())
    margin = size * 0.24   # 左侧让出竖条空间，glyph 略右移保持居中观感
    if svg:
        renderer = QSvgRenderer(QByteArray(svg))
        glyph_rect = QRectF(margin, margin, size - margin - size * 0.18, size - 2 * margin)
        if renderer.isValid():
            renderer.render(p, glyph_rect)
        else:
            _fallback_icon(p, glyph_rect, seed, glyph_color)
    else:
        _fallback_icon(p, QRectF(margin, margin, size - margin - size * 0.18, size - 2 * margin),
                       seed, glyph_color)

    accent = QColor(FX_FEATURE_COLORS[seed % len(FX_FEATURE_COLORS)])
    _draw_accent_bar(p, rect, accent)   # 分类/特征色仅上左竖条
    _draw_badge(p, rect, seed, accent, badge)
    p.end()
    return QIcon(pix)


def _fallback_icon(p: QPainter, rect: QRectF, seed: int, color: QColor):
    pen = QPen(color, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    x0 = rect.left() + 7.0
    y0 = rect.center().y()
    last = None
    for i in range(8):
        x = x0 + i * (rect.width() - 14.0) / 7.0
        y = y0 + (((seed >> (i % 8)) & 3) - 1.5) * 2.8
        if last is not None:
            p.drawLine(QPointF(last[0], last[1]), QPointF(x, y))
        last = (x, y)


def icon_badge_for_text(text: Any) -> str:
    marker = str(text or "").lower()
    if "wave" in marker or "波" in marker:
        return "~"
    if "chase" in marker or "跑" in marker or "追" in marker:
        return ">"
    if "audio" in marker or "音" in marker:
        return "A"
    if "matrix" in marker or "矩阵" in marker or "pixel" in marker:
        return "M"
    if "motor" in marker or "电机" in marker:
        return "X"
    if "pump" in marker or "泵" in marker:
        return "P"
    if "valve" in marker or "阀" in marker:
        return "V"
    if "color" in marker or "色" in marker:
        return "C"
    return ""
