# -*- coding: utf-8 -*-
"""
main_icon_library.py — 全应用统一图标库（tabler-first）

设计语言统一改造（_ui_restyle/notes/restyle_spec.md §6 图标策略）：
  - 第一优先级：仓库内置 tabler SVG 包（MIT，viewBox 0 0 24，stroke=currentColor 可染）
    通过公开 API :func:`load_tabler_icon` 加载染色，lru_cache 缓存渲染结果。
  - ``make_main_icon(key, size)`` 签名不变、全部调用点兼容；内部映射表改为
    key → tabler 图标名。仅当 tabler 缺字形才逐级回退：
    qtawesome 矢量 → fluent_emoji PNG → emoji 渲染（fluent_emoji 不再新增调用）。

调用约定：key 是本模块私有的逻辑键（如 "patch"/"track_add"），跨面板共享；
直接要 tabler 具象图标的场景请用 :func:`load_tabler_icon("wave-sine")`。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

try:
    from . import qascade_theme as theme
except ImportError:  # 允许以 src.ui.main_icon_library 绝对路径方式导入
    from src.ui import qascade_theme as theme

logger = logging.getLogger(__name__)

_FLUENT_DIR = Path(__file__).resolve().parent / "icon_assets" / "fluent_emoji" / "3d"

# 内置 tabler 包目录（outline 5093 枚为主，filled 仅作个别实心字形的补充来源）
_TABLER_DIR = Path(__file__).resolve().parent / "icon_assets" / "tabler" / "package"
_TABLER_OUTLINE_DIR = _TABLER_DIR / "icons" / "outline"
_TABLER_FILLED_DIR = _TABLER_DIR / "icons" / "filled"

#: 默认 glyph 颜色 = 正文主色（单色克制线稿风格）
DEFAULT_ICON_COLOR = theme.TEXT_PRIMARY

#: 录制红（语义保护：与 main_window 录制按钮前景一致，勿随主题灰化）
RECORD_RED = "#ff4444"


# ── 公开 API：tabler 直载 ──────────────────────────────────────────

@lru_cache(maxsize=4096)
def _tabler_svg_text(name: str) -> Optional[str]:
    """按 kebab-case 名读 tabler svg 原文（outline 优先，filled 补充）；缺失返回 None。"""
    safe = "".join(ch for ch in str(name or "") if ch.isalnum() or ch == "-")
    if not safe:
        return None
    for directory in (_TABLER_OUTLINE_DIR, _TABLER_FILLED_DIR):
        path = directory / f"{safe}.svg"
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                logger.debug("tabler 图标读取失败: %s", path)
                return None
    return None


def _screen_dpr() -> float:
    """主屏 devicePixelRatio（HiDPI 下按物理像素渲染，图标才够锐利）。

    2026-08-27 修复"图标模糊"：此前 QPixmap(s,s) 不带 DPR，在 125%/150%
    缩放屏上被拉伸发虚。现在按 s×DPR 栅格化并标注 DPR，Qt 直接 1:1 上屏。
    """
    try:
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        dpr = float(scr.devicePixelRatio()) if scr is not None else 1.0
    except Exception:
        dpr = 1.0
    return max(1.0, min(dpr, 3.0))


@lru_cache(maxsize=2048)
def _tabler_pixmap_cached(
    name: str, size: int, color: str, dpr_key: float,
) -> "QPixmap":
    svg_text = _tabler_svg_text(name)
    if svg_text is None:
        logger.debug("tabler 图标不存在: %s", name)
        return QPixmap()
    s = max(8, int(size))
    dpr = max(1.0, float(dpr_key))
    data = QByteArray(svg_text.replace("currentColor", color).encode("utf-8"))
    renderer = QSvgRenderer(data)
    if not renderer.isValid():
        logger.debug("tabler 图标解析失败: %s", name)
        return QPixmap()
    pix = QPixmap(int(round(s * dpr)), int(round(s * dpr)))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(s), float(s)))
    painter.end()
    return pix


def load_tabler_pixmap(
    name: str,
    size: int = 16,
    color: str = DEFAULT_ICON_COLOR,
) -> "QPixmap":
    """DPR 感知的 tabler glyph 位图（需要 QPixmap 的绘制方请用本 API）。"""
    return _tabler_pixmap_cached(str(name), max(8, int(size)), str(color),
                                 _screen_dpr())


@lru_cache(maxsize=2048)
def _tabler_icon_cached(name: str, size: int, color: str, dpr_key: float) -> QIcon:
    pix = _tabler_pixmap_cached(name, size, color, dpr_key)
    return QIcon(pix) if pix is not None and not pix.isNull() else QIcon()


def load_tabler_icon(
    name: str,
    size: int = 16,
    color: str = DEFAULT_ICON_COLOR,
) -> QIcon:
    """加载内置 tabler SVG 并染成指定颜色（HiDPI 锐利版）。

    :param name:  tabler 图标名（kebab-case，如 "wave-sine"、"layout-sidebar"）
    :param size:  目标位图边长（逻辑像素，最小 8）
    :param color: glyph 颜色（替换 svg 中所有 currentColor）
    :return:      渲染好的 QIcon；文件缺失或渲染失败时返回空 QIcon 并记 debug 日志，
                  由调用方决定是否走旧回退链。
    """
    return _tabler_icon_cached(str(name), max(8, int(size)), str(color),
                               _screen_dpr())


# ── make_main_icon 逻辑键 → tabler 图标名映射 ───────────────────────
# （key 集合与旧 _QA_ICON_MAP 一一对应，未收录的 key 保持旧回退链行为；
#   value = (tabler 名, 定制色 or ""=默认正文色)。全部条目已核实在 v3.44.0 存在。）

_TABLER_ICON_MAP: Dict[str, Tuple[str, str]] = {
    "image":           ("photo", ""),        # C5：Matrix「导入图片帧」（修前回退 emoji 文件夹）
    # 面板图标
    "panel_default":   ("layout-sidebar", ""),
    "monitor":         ("chart-line", ""),
    "processor":       ("link", ""),
    "network":         ("network", ""),
    "patch":           ("plug", ""),
    "programs":        ("list-details", ""),
    "topview":         ("map", ""),
    "route_order":     ("route", ""),
    "programmer":      ("keyboard", ""),
    "matrix":          ("layout-grid", ""),
    "fixture_group":   ("puzzle", ""),
    "effects":         ("wand", ""),
    "smooth":          ("adjustments-horizontal", ""),
    "audio_bridge":    ("headphones", ""),
    "brightness":      ("sun", ""),
    "command_arrange": ("terminal-2", ""),  # 命令行编曲：更贴合"输入指令编排"语义
    "rename":          ("pencil", ""),
    "folder":          ("folder", ""),
    "folder_toggle":   ("folder-open", ""),
    "folder_move":     ("folder-up", ""),
    "track_height":    ("arrows-vertical", ""),
    "indent":          ("indent-increase", ""),
    "color":           ("palette", ""),
    "filter":          ("filter", ""),
    "move_up":         ("arrow-up", ""),
    "move_down":       ("arrow-down", ""),
    "track_copy":      ("copy", ""),
    "choreography":    ("robot", ""),
    # 操作图标
    "track_add":       ("circle-plus", ""),
    "track_delete":    ("trash", ""),
    "zoom_in":         ("zoom-in", ""),
    "zoom_out":        ("zoom-out", ""),
    "network_start":   ("player-play", ""),
    "network_stop":    ("player-stop", ""),
    "project_new":     ("file", ""),
    "project_open":    ("folder-open", ""),
    "project_save":    ("device-floppy", ""),
    "project_save_as": ("device-floppy", ""),
    "export":          ("file-export", ""),
    "exit":            ("logout", ""),
    "undo":            ("arrow-back-up", ""),
    "redo":            ("arrow-forward-up", ""),
    "select_all":      ("checkbox", ""),
    "select":          ("pointer", ""),
    "razor":           ("scissors", ""),
    "pump":            ("droplet", ""),
    "transition":      ("arrows-exchange", ""),
    "motor":           ("settings", ""),
    "fit":             ("arrows-diagonal", ""),
    "layout_programming": ("code", ""),
    "layout_monitoring":  ("device-desktop", ""),
    "layout_full":     ("arrows-maximize", ""),
    "layout_minimal":  ("arrows-minimize", ""),
    "layout_reset":    ("refresh", ""),
    "clear_output":    ("circle-x", ""),
    "edge_in":         ("login", ""),      # 分组方向过渡应用边=淡入（2026-08-12）
    "edge_out":        ("logout", ""),     # 分组方向过渡应用边=淡出（2026-08-12）
    "about":           ("info-circle", ""),
    # 音频图标
    "audio_sim":       ("broadcast", ""),
    "audio_file":      ("music", ""),
    "audio_mic":       ("microphone", ""),
    # 播放控制
    "skip_back":       ("player-track-prev", ""),
    "play":            ("player-play", ""),
    "pause":           ("player-pause", ""),
    "stop":            ("player-stop", ""),
    "record":          ("point", RECORD_RED),
    "skip_forward":    ("player-track-next", ""),
    "render":          ("player-play", ""),
    "unlock":          ("lock-open", ""),
    "lock":            ("lock", ""),
    "joystick_device": ("device-gamepad", ""),
    # patch_panel 按钮 key (2026-08-13 icon-01: 修复前三级全 miss 落 🗂)
    "add":            ("circle-plus", ""),
    "edit":           ("pencil", ""),
    "import":         ("file-import", ""),
    # auto_arrange_panel 按钮 key (2026-08-13 icon-02: 12 处裸 FA 字形名落 🗂)
    "3d-cube-sphere": ("cube-3d-sphere", ""),
    "users-group":    ("users-group", ""),
    "separator":      ("separator-horizontal", ""),
    "search":         ("search", ""),
    "brain":          ("brain", ""),
    "help-circle":    ("help-circle", ""),
    "refresh":        ("refresh", ""),
    # 调度台（效果制作统一总线）：仪表盘仪表语义 = "总控台"
    "dispatch":       ("layout-dashboard", ""),
    "file-export":    ("file-export", ""),
    "music":          ("music", ""),
    "player-play":    ("player-play", ""),
    "bolt":           ("bolt", ""),
}


# ── 旧回退链（仅当 tabler 缺字形时启用；不再新增调用） ────────────────

def _qa_icon(key: str, size: int) -> Optional[QIcon]:
    """qtawesome 兜底（实现整块搬移至 main_icon_qtawesome；延迟导入防循环）。"""
    from .main_icon_qtawesome import _qa_icon as _impl
    return _impl(key, size)



_FALLBACK_EMOJI: Dict[str, str] = {
    "panel_default": "\U0001F5C2",
    "monitor": "\U0001F4C8",
    "processor": "⛓",
    "network": "\U0001F310",
    "patch": "\U0001F50C",
    "programs": "\U0001F4CB",
    "topview": "\U0001F5FA",
    "route_order": "\U0001F9ED",
    "programmer": "⌨",
    "matrix": "\U0001F4A0",
    "fixture_group": "\U0001F9E9",
    "effects": "\U0001FA84",
    "smooth": "\U0001F39B",
    "audio_bridge": "\U0001F3A7",
    "brightness": "☀",
    "command_arrange": "\U0001F4DC",
    "rename": "✏",
    "folder": "\U0001F4C1",
    "folder_toggle": "\U0001F4C2",
    "folder_move": "\U0001F4C1",
    "track_height": "↕",
    "indent": "↔",
    "color": "🎨",
    "filter": "🔧",
    "move_up": "⬆",
    "move_down": "⬇",
    "track_copy": "📋",
    "choreography": "\U0001F916",
    "track_add": "➕",
    "track_delete": "\U0001F5D1",
    "zoom_in": "\U0001F50D",
    "zoom_out": "\U0001F50E",
    "network_start": "▶",
    "network_stop": "⏹",
    "project_new": "\U0001F4C4",
    "project_open": "\U0001F4C2",
    "project_save": "\U0001F4BE",
    "project_save_as": "\U0001F4E4",
    "export": "\U0001F4E4",
    "exit": "\U0001F6AA",
    "undo": "↩",
    "redo": "↪",
    "select_all": "☑",
    "select": "↖",
    "razor": "✂",
    "pump": "◉",
    "transition": "⇄",
    "motor": "⚙",
    "fit": "\U0001F5D6",
    "layout_programming": "\U0001F527",
    "layout_monitoring": "\U0001F4CA",
    "layout_full": "\U0001F5A5",
    "layout_minimal": "\U0001F5D5",
    "layout_reset": "\U0001F504",
    "clear_output": "✖",
    "edge_in": "⤵",
    "edge_out": "⤴",
    "about": "ℹ",
    "audio_sim": "\U0001F4F6",
    "audio_file": "\U0001F3B5",
    "audio_mic": "\U0001F399",
    "skip_back": "⏮",
    "play": "▶",
    "pause": "⏸",
    "stop": "⏹",
    "record": "⏺",
    "skip_forward": "⏭",
    "render": "\U0001F39E",
    "unlock": "\U0001F513",
    "lock": "\U0001F512",
    "joystick_device": "\U0001F3AE",
    "add": "➕",
    "edit": "✏",
    "import": "\U0001F4E5",
    "3d-cube-sphere": "\U0001F9CA",
    "users-group": "\U0001F465",
    "separator": "▬",
    "search": "\U0001F50D",
    "brain": "\U0001F9E0",
    "help-circle": "❓",
    "refresh": "\U0001F504",
    "dispatch": "\U0001F5C2",
    "file-export": "\U0001F4E4",
    "music": "\U0001F3B5",
    "player-play": "▶",
    "bolt": "⚡",
}


# ── 公开 API ─────────────────────────────────────────────────────

@lru_cache(maxsize=1024)
def make_main_icon(key: str, size: int = 24) -> QIcon:
    """生成面板/操作图标（签名与历史调用完全兼容）。

    回退链：tabler SVG（首选）→ qtawesome 矢量 → fluent_emoji PNG → emoji 渲染。
    """
    resolved = str(key or "panel_default")
    s = max(8, int(size))

    # 第1级：内置 tabler SVG（专业设计语言统一来源）
    entry = _tabler_entry(resolved)   # C8：映射表 → 未登记再按 tabler 文件名直查
    if entry is not None:
        icon = load_tabler_icon(entry[0], s, entry[1] or DEFAULT_ICON_COLOR)
        if not icon.isNull():
            return icon

    # 第2级：qtawesome 矢量兜底（仅 tabler 缺字形时）
    legacy = _qa_icon(resolved, s)
    if legacy is not None and not legacy.isNull():
        return legacy

    # 第3级：本地 PNG 位图兜底（遗留资源，不新增使用方）
    png_path = _FLUENT_DIR / f"{resolved}.png"
    if png_path.is_file():
        return QIcon(str(png_path))

    # 第4级：emoji 回退
    return _emoji_fallback_icon(resolved, s)


def _tabler_entry(key: str):
    """逻辑键 → (tabler 名, 色)：先查映射表；未登记再按 tabler 文件名直查
    （kebab / snake 两写法，outline 优先）——C8：修前直传 tabler 名的 16 个调用点
    （presentation / grid-dots / train …）全部落到 emoji 回退。无 → None。"""
    entry = _TABLER_ICON_MAP.get(key)
    if entry is not None:
        return entry
    for name in dict.fromkeys((key, key.replace("_", "-"))):
        if (_TABLER_OUTLINE_DIR / f"{name}.svg").is_file() or \
                (_TABLER_FILLED_DIR / f"{name}.svg").is_file():
            return (name, "")
    return None


def _emoji_fallback_icon(key: str, size: int) -> QIcon:
    """用 emoji 字符渲染为图标（最后回退方案）。"""
    emoji = _FALLBACK_EMOJI.get(key, _FALLBACK_EMOJI["panel_default"])
    px = max(18, int(size))
    pix = QPixmap(QSize(px, px))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont("Segoe UI Emoji")
    font.setPixelSize(int(px * 0.78))
    painter.setFont(font)
    painter.setPen(QColor(theme.TEXT_PRIMARY))
    painter.drawText(QRect(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return QIcon(pix)
