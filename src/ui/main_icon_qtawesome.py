# -*- coding: utf-8 -*-
"""make_main_icon 回退链第 2 级：qtawesome 矢量兜底（从 main_icon_library 整块搬移）。

搬移原因（R183 修复轮 C8）：main_icon_library 原本恰好 500 行（门禁上限），为新增
「键即 tabler 文件名」直查级（修前 16 个直传 tabler 名的调用点全部落到 emoji 回退）
腾出行数。映射表与函数体与搬移前逐字一致；原文件保留同名 ``_qa_icon`` 薄包装
（延迟导入本模块 → 本模块顶层导入 main_icon_library 时其已加载完毕，无循环依赖）。
"""
from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtGui import QIcon

from .main_icon_library import DEFAULT_ICON_COLOR, RECORD_RED

_QA_ICON_MAP: Dict[str, tuple] = {
    # 兜底映射原样保留旧 fa5/mdi 目标（仅在 tabler 缺字形时被 _qa_icon 使用）
    "panel_default":   ("fa5s.window-maximize", {}),
    "monitor":         ("fa5s.chart-line", {}),
    "processor":       ("fa5s.link", {}),
    "network":         ("fa5s.network-wired", {}),
    "patch":           ("fa5s.plug", {}),
    "programs":        ("fa5s.list-alt", {}),
    "topview":         ("fa5s.map", {}),
    "route_order":     ("fa5s.route", {}),
    "programmer":      ("fa5s.keyboard", {}),
    "matrix":          ("fa5s.th", {}),
    "fixture_group":   ("fa5s.puzzle-piece", {}),
    "effects":         ("fa5s.magic", {}),
    "smooth":          ("fa5s.sliders-h", {}),
    "audio_bridge":    ("fa5s.headphones", {}),
    "brightness":      ("fa5s.sun", {}),
    "command_arrange": ("fa5s.terminal", {}),
    "rename":          ("fa5s.edit", {}),
    "folder":          ("fa5s.folder", {}),
    "folder_toggle":   ("fa5s.folder-open", {}),
    "folder_move":     ("fa5s.level-up-alt", {}),
    "track_height":    ("fa5s.arrows-alt-v", {}),
    "indent":          ("fa5s.indent", {}),
    "color":           ("fa5s.palette", {}),
    "filter":          ("fa5s.filter", {}),
    "move_up":         ("fa5s.arrow-up", {}),
    "move_down":       ("fa5s.arrow-down", {}),
    "track_copy":      ("fa5s.copy", {}),
    "choreography":    ("fa5s.robot", {}),
    "track_add":       ("fa5s.plus-circle", {}),
    "track_delete":    ("fa5s.trash-alt", {}),
    "zoom_in":         ("fa5s.search-plus", {}),
    "zoom_out":        ("fa5s.search-minus", {}),
    "network_start":   ("fa5s.play-circle", {}),
    "network_stop":    ("fa5s.stop-circle", {}),
    "project_new":     ("fa5s.file", {}),
    "project_open":    ("fa5s.folder-open", {}),
    "project_save":    ("fa5s.save", {}),
    "project_save_as": ("fa5s.save", {}),
    "export":          ("fa5s.file-export", {}),
    "exit":            ("fa5s.sign-out-alt", {}),
    "undo":            ("fa5s.undo", {}),
    "redo":            ("fa5s.redo", {}),
    "select_all":      ("fa5s.check-square", {}),
    "select":          ("fa5s.mouse-pointer", {}),
    "razor":           ("fa5s.cut", {}),
    "pump":            ("fa5s.tint", {}),
    "transition":      ("fa5s.exchange-alt", {}),
    "motor":           ("fa5s.cog", {}),
    "fit":             ("fa5s.expand", {}),
    "layout_programming": ("fa5s.code", {}),
    "layout_monitoring":  ("fa5s.desktop", {}),
    "layout_full":     ("fa5s.expand-arrows-alt", {}),
    "layout_minimal":  ("fa5s.compress-arrows-alt", {}),
    "layout_reset":    ("fa5s.sync-alt", {}),
    "clear_output":    ("fa5s.times-circle", {}),
    "edge_in":         ("fa5s.sign-in-alt", {}),
    "edge_out":        ("fa5s.sign-out-alt", {}),
    "about":           ("fa5s.info-circle", {}),
    "audio_sim":       ("fa5s.broadcast-tower", {}),
    "audio_file":      ("fa5s.music", {}),
    "audio_mic":       ("fa5s.microphone", {}),
    "skip_back":       ("fa5s.step-backward", {}),
    "play":            ("fa5s.play", {}),
    "pause":           ("fa5s.pause", {}),
    "stop":            ("fa5s.stop", {}),
    "record":          ("fa5s.circle", {"color": RECORD_RED}),
    "skip_forward":    ("fa5s.step-forward", {}),
    "render":          ("fa5s.play", {}),
    "unlock":          ("fa5s.unlock", {}),
    "lock":            ("fa5s.lock", {}),
    "joystick_device": ("fa5s.gamepad", {}),
    "add":            ("fa5s.plus-circle", {}),
    "edit":           ("fa5s.edit", {}),
    "import":         ("fa5s.file-import", {}),
    "3d-cube-sphere": ("fa5s.cube", {}),
    "users-group":    ("fa5s.users", {}),
    "separator":      ("fa5s.minus", {}),
    "search":         ("fa5s.search", {}),
    "brain":          ("fa5s.brain", {}),
    "help-circle":    ("fa5s.question-circle", {}),
    "refresh":        ("fa5s.sync-alt", {}),
    "dispatch":       ("fa5s.th-large", {}),
    "file-export":    ("fa5s.file-export", {}),
    "music":          ("fa5s.music", {}),
    "player-play":    ("fa5s.play", {}),
    "bolt":           ("fa5s.bolt", {}),
}

_QA_AVAILABLE: Optional[bool] = None


def _qa_available() -> bool:
    """延迟检测 qtawesome 是否可用。"""
    global _QA_AVAILABLE
    if _QA_AVAILABLE is None:
        try:
            import qtawesome as qta  # noqa: F401
            _QA_AVAILABLE = True
        except ImportError:
            _QA_AVAILABLE = False
    return _QA_AVAILABLE


def _qa_icon(key: str, size: int) -> Optional[QIcon]:
    """从 qtawesome 获取矢量兜底图标。不可用或无映射时返回 None。"""
    if not _qa_available():
        return None
    mapping = _QA_ICON_MAP.get(key)
    if not mapping:
        return None
    try:
        import qtawesome as qta
        name, options = mapping
        color = options.get("color", DEFAULT_ICON_COLOR) if isinstance(options, dict) else DEFAULT_ICON_COLOR
        return qta.icon(name, color=color)
    except Exception:
        return None
