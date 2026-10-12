"""
Qascade V1.0 — MainWindow [FIXED v9 + Patch v2 + v3 统一数据流]

合并内容：
  - main_window.py FIXED v9（基础版本）
  - 补丁 FIXED v2：
      [Bug 4]  _on_clip_to_processor —— 先调 _lazy_load_panels() 确保面板存在
      [Bug 6]  _on_process_done      —— 三级兜底查找源 Clip（PM → tracks → orphan）
      [新增]   _ensure_processor_panel() —— 独立懒加载处理面板（供外部调用）
  - ★ v3 统一数据流：
      ProgrammerPanel.dmx_out → NetworkManager.send_raw（硬件输出）
      ProgrammerPanel.dmx_out → RecordEngine.connect_to_signal（录制）
      AudioDriverWidget.dmx_data_changed → ProgrammerPanel._on_audio_dmx_data
"""

from __future__ import annotations
import sys
import re
import uuid
import os
import shutil
import hashlib
import copy
import functools
import json
import zipfile
import datetime
import time
from .project_save_session import close_after_project_saves, note_project_edit
from pathlib import Path
import numpy as np
from typing import Dict, Optional, Tuple
from .project_load_bake_gate import bake_deferred, defer_bake_during_load
from .topview_selection_context import live_base_if_established as _topview_live_base_if_established

# 录制帧组装段共享零帧/零掩码（只读：段内 np.any/==/copy 均不写，merge 写前必 copy）
_EMPTY_BOOL_512 = np.zeros(512, dtype=bool)
_EMPTY_U8_512 = np.zeros(512, dtype=np.uint8)

import logging as _logging

# ★ 2026-09-20：模块级 logger —— 多处 except 处理器引用 `log`/`_log`，
#   而此前本文件只有函数内的局部 `import logging`（作用域割裂 →
#   错误处理自身抛 NameError，把真因吞掉甚至中断整条用户流程）。
log = _logging.getLogger(__name__)
_log = log

from PyQt6.QtWidgets import (QGraphicsDropShadowEffect,
    QMainWindow, QComboBox, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QStatusBar, QLabel, QPushButton,
    QDockWidget, QFileDialog, QMessageBox,
    QDoubleSpinBox, QSizePolicy,
    QDialog, QDialogButtonBox, QSpinBox,
    QGroupBox, QLineEdit, QMenu, QScrollArea,
    QProgressDialog, QApplication, QListWidget, QListWidgetItem,
    QInputDialog, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSettings, QSize
from PyQt6.QtGui import QAction, QKeySequence, QFont, QIcon, QPainter, QColor
from PyQt6 import sip
from ..core.constants import ALL_LAYERS, BASE_LAYERS, SHAPE_LAYERS  # 层名单源
from . import dispatch_flow as _flow
from .dispatch.record_release import finish_record_outputs as _finish_record_outputs
from .program_light_control import install_program_light_control, refresh_program_light_control
from .timeline_program_bake_gate import update_program_bake_gate
from .project_selection_restore import refresh_programmer_geometry
from .route_animator.runner_lifecycle import stop_window_runner
from .window_shutdown import stop_window_linker
from ..engine.timeline_runtime_release import release_playback_runtime
from ..engine.timeline_cache_notifications import notify_project_change
from .timeline.timeline_controller import (
    apply_clip_resize as _apply_clip_resize)  # noqa: F401（resize 语义单一真源）
from .main_icon_library import make_main_icon, load_tabler_icon
from .editor_chrome import install_editor_chrome
from .audio_mod_workspace_binding import (install_audio_workspace, prepare_audio_workspace,
                                         refresh_audio_workspace, dispose_audio_workspace)
from .widget_kit import NumSlider  # D 波标准件：极简数值滑杆
from .responsive_tiers import (  # R173：分级隐藏 + 默认窗口尺寸，新模块零方法侵入
    install_panel_toolbar_tiers, default_main_window_size)

# ── 主题设计令牌：优先引用中心主题（唯一真源），缺失时回退为同值字面 ──
try:
    from .qascade_theme import (
        themed_button_stylesheet as _themed_btn,
        BG_ROOT, BG_DEEP, BG_EDITOR, BG_PANEL, BG_PANEL_SUB,
        BG_INPUT, BG_INPUT_EDIT, BG_BOX,
        BG_BUTTON, BG_BUTTON_HOVER,
        SELECT_BLUE, FOCUS_BLUE, ACTIVE_ORANGE,
        WARN_ORANGE, ERROR_RED, SUCCESS_GREEN, INFO_BLUE,
        TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED,
        TEXT_SELECTED_FG, BORDER_NORMAL, POPUP_BORDER,
        BRIGHT_SUCCESS, BRIGHT_WARN, BRIGHT_ERROR, BRIGHT_INFO,
        SEP_LINE,
    )
except ImportError:
    _themed_btn = None  # 回退：使用内联 _css_btn
    SEP_LINE = "rgba(255,255,255,21)"
    # 与 qascade_theme 完全一致的回退字面值（见 restyle_spec.md §1）
    BG_ROOT = "#1D1D1D"; BG_DEEP = "#181818"; BG_EDITOR = "#303030"
    BG_PANEL = "#3D3D3D"; BG_PANEL_SUB = "#363636"
    BG_INPUT = "#1D1D1D"; BG_INPUT_EDIT = "#181818"; BG_BOX = "#2D2D2D"
    BG_BUTTON = "#545454"; BG_BUTTON_HOVER = "#656565"
    SELECT_BLUE = "#4772B3"; FOCUS_BLUE = "#71A8FF"; ACTIVE_ORANGE = "#FFA028"
    WARN_ORANGE = "#AC8737"; ERROR_RED = "#991616"
    SUCCESS_GREEN = "#188625"; INFO_BLUE = "#28487D"
    TEXT_PRIMARY = "#E6E6E6"; TEXT_SECONDARY = "#999999"
    TEXT_TERTIARY = "#CCCCCC"; TEXT_DISABLED = "#8B8B8B"
    TEXT_SELECTED_FG = "#FFFFFF"; BORDER_NORMAL = "#3D3D3D"
    POPUP_BORDER = "#242424"
    BRIGHT_SUCCESS = "#56B65B"; BRIGHT_WARN = "#E6B455"
    BRIGHT_ERROR = "#E36855"; BRIGHT_INFO = "#6FA8DC"

# 主题内部辅助色阶（与 qascade_theme.GLOBAL_QSS 同值，非新增语义色）
FIELD_DISABLED_BG = "#242424"       # 输入类控件 disabled 底
FIELD_DISABLED_BORDER = "#262626"   # disabled 描边
HOVER_BORDER = "#5C5C5C"            # 按钮 hover 加亮描边
SELECT_BLUE_HOVER = "#5480C5"       # checked:hover 的 select 蓝亮阶

# ── Timeline Widget（含跨轨拖动完整实现）────────────────────────────────────
try:
    from .timeline.timeline_widget import TimelineWidget
except ImportError:
    TimelineWidget = None

# --- New Panels ---
from .patch_panel import PatchPanel
from .topview_panel import TopViewPanel
from .programmer_panel import ProgrammerPanel
from . import error_catalog as ec

# ── 以下仅作兜底，TimelineWidget 已含 TrackList / ClipArea / Ruler ──────────
try:
    from .timeline.track_list import TrackList
except ImportError:
    TrackList = None  # type: ignore

try:
    from .timeline.clip_view import ClipView
except ImportError:
    ClipView = None  # type: ignore

try:
    from .timeline.timeline_ruler import TimelineRuler
except ImportError:
    TimelineRuler = None  # type: ignore


# 旧常量名 → 主题令牌别名（历史引用统一走令牌；语义见右侧注释）
DARK_BG = BG_ROOT            # 主窗根底
PANEL_BG = BG_EDITOR         # 面板/卡片基底
ACCENT = FOCUS_BLUE          # 兼容别名：仅描边/聚焦语义
GREEN = BRIGHT_SUCCESS       # 运行/成功文本色
RED_STOP = BRIGHT_ERROR      # 停止/危险文本色
AMBER = BRIGHT_WARN          # 时间码/数值暖文本
BLUE_LT = FOCUS_BLUE         # 高亮数值蓝文本
APP_NAME = "雾白纪 ULTRA"    # 用户可见品牌名（QSettings/存储路径标识不含此串）

_TRACKLIST_TOP_BAR_H = 32

_PANEL_ICON_NAMES = {
    "panel_default", "monitor", "processor", "network", "patch", "programs",
    "topview", "route_order", "programmer", "matrix", "fixture_group",
    "effects", "smooth", "audio_bridge", "choreography", "track_add",
    "track_delete", "zoom_in", "zoom_out", "network_start", "network_stop",
    "project_new", "project_open", "project_save", "project_save_as",
    "export", "exit", "undo", "redo", "select_all", "fit",
    "layout_programming", "layout_monitoring", "layout_full",
    "layout_minimal", "layout_reset", "clear_output", "about", "audio_sim",
    "audio_file", "audio_mic", "skip_back", "play", "pause", "stop",
    "record", "skip_forward", "render", "unlock", "lock",
    "joystick_device", "dispatch",
}


def _topview_sync_sig(tv, ordered, layout_ver):
    """R165：TopView→effect 同步的**去重键**。

    旧键只有 `(子集, 布局版本)`，于是"选择不变、只换了一组子集划分（如 奇数→偶数）"
    会被守卫吞掉 → effect 面板永远拿不到新的子集标记（用户症状：改了参数却没反应）。
    故把**母集合**与**派生名称**一并折进键。
    """
    try:
        base_sig = tuple(str(x) for x in (getattr(tv, "_live_base_ids", None) or []))
    except Exception:
        base_sig = ()
    sel_name = str(getattr(tv, "_live_selection_name", "") or "")
    return (tuple(ordered), layout_ver, base_sig, sel_name)


def _topview_page_number_base(tv, ordered_ids):
    """R201/P09：普通选择基准 = 页面设备序（画布编号徽标同源）。

    R201/P10（2026-10-06 用户两工程实报）**已废弃为兜底**：真实工程里
    页面设备序≠用户的圆弧链序（多圆页框选整页时页序整体右→左横穿），且
    P10 修复了框选链本身的闭环极角序之后，选择序就是用户看到的编号序——
    效果基准必须与徽标同源（单一真源），不再用页序覆盖。保留本函数仅供
    需要显式页序的调用方使用；`_topview_base_order_ids` 兜底已回退为
    `_topview_order_ids()`（当前选择序）。
    """
    if not ordered_ids or not hasattr(tv, "_page_device_order"):
        return None
    selected = set(ordered_ids)
    page_order = [str(x) for x in (tv._page_device_order() or []) if str(x) in selected]
    if not page_order:
        return None
    present = set(page_order)
    return page_order + [fid for fid in ordered_ids if fid not in present]


def _panel_icon(key: str, size: int = 28) -> QIcon:
    return make_main_icon(key if key in _PANEL_ICON_NAMES else "panel_default", size=size)


def _toolbar_sep() -> QWidget:
    """统一的工具栏竖分隔符：1px 分组竖线（sep_line 令牌，与全局 QSS 同源）。

    旧版为字符 "│"（字体基线随 DPI 漂移），改为纯 QFrame 填充线。
    """
    s = QFrame()
    s.setFrameShape(QFrame.Shape.NoFrame)
    s.setFixedSize(1, 18)
    s.setStyleSheet(f"background:{SEP_LINE}; border:none;")
    return s


def _rgba_i(hex_color: str, alpha: int) -> str:
    """#RRGGBB → rgba(r,g,b,A)（整数 alpha，Qt 原生语义；杜绝 8 位 hex）。"""
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = max(0, min(255, int(alpha)))
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return hex_color


def _css_flat_block(fill_hex: str, base_alpha: int, fg: str,
                    *, checkable: bool = False) -> str:
    """扁平语义状态钮：哑光语义底（低 alpha 叠色）+ 亮色 glyph。

    取代旧版刺眼的实色填充块；checked 态按需求加边框提亮，
    不再整块换成高饱和纯色。语义色值来自中心令牌。
    """
    hi = min(255, base_alpha + 26)
    qss = (
        "QPushButton {"
        f" background:{_rgba_i(fill_hex, base_alpha)}; color:{fg};"
        f" border:1px solid {_rgba_i(fill_hex, max(base_alpha, 46))};"
        " border-radius:4px; font-size:11px; padding:0 10px;"
        "}"
        f"QPushButton:hover {{ background:{_rgba_i(fill_hex, hi)};"
        f" border-color:{fg}; color:{fg}; }}"
        f"QPushButton:pressed {{ background:{_rgba_i(fill_hex, min(220, base_alpha + 60))};"
        f" color:{TEXT_SELECTED_FG}; }}"
        f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        f"QPushButton:disabled {{ background:{BG_PANEL_SUB}; color:{TEXT_DISABLED};"
        f" border-color:{FIELD_DISABLED_BORDER}; }}"
    )
    if checkable:
        # 激活态：同族底加深 + 亮色描边（选中态语义）
        qss += (
            f"QPushButton:checked {{ background:{_rgba_i(fill_hex, min(200, base_alpha + 55))};"
            f" border-color:{fg}; }}"
            f"QPushButton:checked:hover {{ background:{_rgba_i(fill_hex, min(230, base_alpha + 85))}; }}"
        )
    return qss


def _panel_key_from_text(*parts: str) -> str:
    text = " ".join(str(p or "") for p in parts).lower()
    checks = (
        ("skip_back", ("回到开头", "rewind")),
        ("skip_forward", ("跳到末尾", "forward")),
        ("record", ("录制", "record")),
        ("render", ("渲染", "render")),
        ("pause", ("暂停", "pause")),
        ("stop", ("停止", "stop", "esc")),
        ("play", ("播放", "play", "space")),
        ("audio_bridge", ("audio bridge", "音乐桥接", "bridge")),
        ("fixture_group", ("fixture", "设备组", "group")),
        ("route_order", ("route", "order", "顺序")),
        ("programmer", ("programmer", "编程")),
        ("matrix", ("matrix", "矩阵")),
        ("effects", ("effect", "效果")),
        ("smooth", ("smooth", "平滑")),
        ("topview", ("topview", "视图")),
        ("patch", ("patch", "配接")),
        ("programs", ("节目", "program manager")),
        ("processor", ("关联", "processor", "link")),
        ("monitor", ("监视", "monitor")),
        ("network", ("网络", "network")),
        ("track_add", ("添加", "add")),
        ("track_delete", ("删除", "delete")),
        ("zoom_in", ("放大", "zoom in")),
        ("zoom_out", ("缩小", "zoom out")),
        ("network_start", ("启动", "start")),
        ("network_stop", ("停止", "stop")),
    )
    for key, tokens in checks:
        if any(token in text for token in tokens):
            return key
    return "panel_default"


def _hex_alpha(hex_color: str, alpha_hex: str) -> str:
    """将 #RRGGBB + 2位十六进制 alpha 转换为 rgba(r,g,b,a)"""
    try:
        h = hex_color.lstrip('#')
        if len(h) != 6: return hex_color
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(alpha_hex, 16) / 255.0
        return f"rgba({r},{g},{b},{a:.2f})"
    except Exception:
        return hex_color


def _css_btn(bg: str, fg: str, hover_bg: str,
             border: str = BORDER_NORMAL,
             radius: int = 4,
             font_size: int = 11,
             padding: str = "0 10px") -> str:
    return (
        "QPushButton {"
        f" background:{bg}; color:{fg};"
        f" border:1px solid {border}; border-radius:{radius}px;"
        f" font-size:{font_size}px; padding:{padding};"
        "}"
        "QPushButton:hover {"
        f" background:{hover_bg}; border-color:{_hex_alpha(fg, '66')};"
        "}"
        "QPushButton:pressed {"
        f" background:{_hex_alpha(bg, 'aa')};"
        "}"
        f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        "QPushButton:disabled {"
        f" background:{BG_PANEL_SUB}; color:{TEXT_DISABLED};"
        f" border-color:{FIELD_DISABLED_BORDER};"
        "}"
    )


# 旧 _css_toggle/_css_dspin/_css_ispin/_css_lineedit 已删除：
# 面板开关钮统一样式改由 PanelToolbar._css_panel_toggle 提供，
# 可调数值控件已换 NumSlider（restyle_spec §3/§10，调用点同步清理）。


def _css_dialog_btns() -> str:
    return (
        "QPushButton {"
        f" background:{BG_BUTTON}; color:{TEXT_PRIMARY};"
        f" border:1px solid {BORDER_NORMAL}; border-radius:4px; padding:4px 20px;"
        "}"
        f"QPushButton:hover {{ background:{BG_BUTTON_HOVER}; border-color:{HOVER_BORDER}; }}"
        f"QPushButton:default {{ background:{SELECT_BLUE}; color:{TEXT_SELECTED_FG};"
        f" border-color:{SELECT_BLUE}; }}"
        f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        "QPushButton:disabled {"
        f" background:{BG_PANEL_SUB}; color:{TEXT_DISABLED}; border-color:{FIELD_DISABLED_BORDER};"
        "}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TransportBar
# ══════════════════════════════════════════════════════════════════════════════

def _audio_assets_base_file(save_file: Path, real_project_path: str) -> Path:
    """资源目录基准文件：**自动保存**时返回主工程文件（R120）。

    ★★ 根因（实测复现）：`_do_auto_save` 把自动保存写到 `<项目目录>/autosave/`，
      而 `_bundle_audio_path_for_save` 原来以「本次保存的目标文件」为基准 ——
      于是自动保存把 `program.audio_path` 改写成
      `水秀_autosave_20260920_x_assets/audio/audio_ab12.mp3`（相对 autosave 目录），
      该路径在**主工程目录**下不存在。更糟的是这次改写发生在内存对象上，
      下一次正常保存会把它**永久写回**工程文件 → 用户重开项目必然报「音频缺失」，
      只能重新导入音频（用户 2026-09-20 实报）。

    修法：自动保存（`<主工程目录>/autosave/…`）复用**主工程**的
    `<stem>_assets/audio/` 目录 → 路径不变、不重复拷贝、内存不被污染。
    其它情况（正常保存/另存为）保持以目标文件为基准（含换目录另存为）。
    """
    if not real_project_path:
        return save_file
    try:
        real_file = Path(real_project_path).expanduser()
    except (TypeError, ValueError):
        return save_file
    if save_file.parent.name == "autosave" and save_file.parent.parent == real_file.parent:
        return real_file
    return save_file


def _missing_audio_fallback(resolve_fn, project_file: Path, audio_path: str,
                            label: str, show_errors: bool) -> tuple[str, str]:
    """原始音频不可用时：优先复用工程内打包副本，否则退回原路径（R120b）。

    返回 `(要保存的路径, 提示文案或 "")`：
      · 找到工程内副本 → `(副本相对路径, 一句说明)`；
      · 没找到且 `show_errors` → `(原路径, 警告文案)`（调用方弹窗）；
      · 没找到且静默模式 → `(原路径, 状态栏文案)`。
    历史保存已把音频打进 `<stem>_assets/audio/`；原始文件被移走后若不复用副本，
    节目就会停在死路径上 → 重开工程报「音频找不到」（用户 2026-09-20 实报）。
    """
    heal = ""
    if resolve_fn is not None:
        try:
            heal = _heal_stored_audio_path(resolve_fn, str(project_file), audio_path)
            if heal == audio_path or not resolve_fn(str(project_file), heal).is_file():
                heal = ""
        except Exception:
            heal = ""
    if heal:
        return heal, f"{label}：原文件已不在，改用工程内副本"
    if show_errors:
        return audio_path, (f"找不到{label}音频文件：\n{audio_path}\n\n"
                            "项目会保存原路径，但不会复制音频。")
    return audio_path, f"自动保存：找不到{label}音频，已保存原路径"


def _recover_bundled_audio_copy(resolve_fn, project_file: Path,
                                audio_path: str) -> str:
    """原始音频文件不可用时，在工程资源目录里找回**已有打包副本**（R120b）。

    历史保存已把音频打进 `<stem>_assets/audio/`；原始文件被移走后，
    `_bundle_audio_path_for_save` 会失败并把节目留在死路径上 —— 本函数按文件名
    找回在库副本并返回其标准相对路径；找不到或与原路径同一文件时返回 ""。
    """
    if not audio_path or resolve_fn is None:
        return ""
    try:
        heal = _heal_stored_audio_path(resolve_fn, str(project_file), audio_path)
        healed_p = resolve_fn(str(project_file), heal) if heal else None
        if heal and heal != audio_path and healed_p is not None and healed_p.is_file():
            return heal
    except Exception:
        return ""
    return ""


def _mod_normalize(state: dict) -> dict:
    from src.engine.audio_modulation import normalize_state
    return normalize_state(state)


def _mod_signature(state: dict, feature_key: str) -> tuple:
    from src.engine.audio_modulation import signature
    return signature(state, feature_key)


def _on_audio_mod_edited(win, state: dict) -> bool:
    """调制轨编辑提交（模块级；上帝类冻结）：写工程 + 失效驱动缓存 + 推引擎签名 + 置脏。"""
    project = win._project()
    if project is None:
        return False
    state = dict(state or {})
    project.audio_modulation_state = state
    from .timeline.timeline_perf import invalidate_scoped_snapshot_cache
    invalidate_scoped_snapshot_cache(win._pm)
    try:
        from src.engine.audio_mod_driver import invalidate_caches
        invalidate_caches()   # ★ P0-4 修复：驱动层 _STATE_CACHE 不失效=编辑不生效
    except Exception:
        log.debug("invalidate audio mod caches failed", exc_info=True)
    try:
        from src.engine.playback_engine import set_engine_audio_mod_state
        feature_key = str(state.get("audio_sha", "") or "")
        set_engine_audio_mod_state(win._engine, _mod_signature(state, feature_key))
    except Exception:
        log.debug("push audio mod signature failed", exc_info=True)
    win.mark_dirty()
    prepare_audio_workspace(win)
    return True


def _safe_dict(v) -> dict:
    """宽容 dict（非 dict/不可迭代 → 空；`_save_current_timeline_to_program` 用）。"""
    try:
        return dict(v or {})
    except Exception:
        return {}


def _save_audio_mod_state(win, project) -> None:
    """调制轨 widget 状态 → 工程（R174#7；从 _save_current_timeline_to_program 拆出）。

    ★ R182 四轮审计修（P0 数据丢失）：widget **从未被灌过工程状态**（打开工程/
    切节目后未走 `_restore_audio_mod_state`）时不回写——修前把 widget 的默认空态
    写回工程，调制轨 clips/rules/Draw 图在"重开工程后首次保存/自动保存/切节目"
    时被静默清空。
    """
    mod = win._timeline_audio_mod_track()
    if mod is not None:
        if not getattr(mod, "_state_hydrated", False):
            log.debug("save_audio_mod_state: 调制轨未水合，跳过回写（防清空）")
            return
        project.audio_modulation_state = mod.state()


def _clear_audio_for_project_restore(win, project) -> str:
    """Reset the audio loader, then restore the independent saved Draw state."""
    win._clear_timeline_audio()
    _restore_audio_mod_state(win, project)
    return ""


def _restore_audio_mod_after_audio_load(win) -> None:
    """工程文件加载成功后补调制状态恢复（审计修 P0；helper 避上帝类 CC）。

    修前 `_restore_timeline_audio_for_project` 从不调 _restore_audio_mod_state
    → 重开工程后调制轨恒空态，首次保存/自动保存即把 Draw 图静默清空。
    """
    try:
        _restore_audio_mod_state(win, win._pm.project)
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "restore audio mod state failed", exc_info=True)


def _copy_program_mod_state(program, project) -> None:
    """节目 → 工程拷贝调制状态（切节目 P0 修复的拷贝段；模块级减上帝类 CC）。"""
    import copy as _copy
    project.audio_modulation_state = _copy.deepcopy(
        getattr(program, "audio_modulation_state", {}) or {})


def _copy_program_audio_fields(program, project) -> None:
    """节目 → 工程拷贝音频三件 + 调制状态（审计修 P0：修前漏调制 → 被清）。"""
    import copy as _copy
    project.audio_path = getattr(program, "audio_path", "") or ""
    project.audio_clip_state = dict(getattr(program, "audio_clip_state", {}) or {})
    _copy_program_mod_state(program, project)


def _wire_audio_mod(win, ctrl) -> None:
    """R174#7 调制轨三条接线（tick/停止复位/双击编辑），从 _connect_timeline_transport 拆出。"""
    tw = getattr(win, "_timeline_widget", None)
    win._managed_connect("timeline:audio_mod:tick", ctrl.playback_tick,
                         functools.partial(_audio_mod_tick, win))
    win._managed_connect("timeline:audio_mod:stop", ctrl.playback_stopped,
                         functools.partial(_audio_mod_stop, win))
    mod = getattr(tw, "_audio_mod_track", None) if tw is not None else None
    sig = getattr(mod, "open_editor_requested", None)
    if sig is not None:
        win._managed_connect("timeline:audio_mod:editor", sig,
                             functools.partial(_open_audio_mod_editor, win))


def _audio_mod_tick(win, pos: float) -> None:
    """R174#7：每帧把音频调制值写进引擎混音增益（Tier0 渲染前）。

    R178 P1-2：tick 信号不止播放中发（stop 后补发 0.0 / 停止态 seek）——
    非播放态传 playing=False，驱动层一律复位 1.0 不写调制值，防停止态残留
    被烘焙进时间线缓存。
    """
    try:
        from src.engine.audio_mod_driver import update_modulation_gain
        playing = bool(getattr(win._engine, "is_playing", False))
        update_modulation_gain(
            win._engine, win._project(), win._current_timeline_audio_path(),
            float(pos), playing=playing)
    except Exception:
        # R178 P2-13：每帧静默 pass 让调制断链无从排查（43Hz 高频路径只 debug）。
        # 用模块级 log（模块顶层是 `import logging as _logging`，裸 logging 名
        # 在 except 内求值会 NameError）。
        log.debug("audio mod tick failed", exc_info=True)


def _audio_mod_stop(win) -> None:
    """R174#7：播放停止 → 调制增益复位 1.0（防残留压暗）。"""
    try:
        from src.engine.audio_mod_driver import reset_modulation_gain
        reset_modulation_gain(win._engine)
    except Exception:
        # R178 P2-13：复位失败静默会让播放残留增益压暗整场（低频路径，保守
        # debug + 堆栈）。
        log.debug("audio mod stop reset failed", exc_info=True)


def _resolve_audio_file_path(stored_path: str):
    """工程相对音频路径 → 绝对 Path（cwd 兜底；从 _restore_timeline_audio 拆出）。"""
    audio_file = Path(os.path.expanduser(os.fspath(stored_path)))
    if not audio_file.is_absolute():
        audio_file = Path.cwd() / audio_file
    return audio_file


def _push_mod_signature(win, state) -> None:
    """调制状态 → 引擎签名（换工程/换歌/清空同路径；失败 debug 不阻断）。"""
    try:
        from src.engine.playback_engine import set_engine_audio_mod_state
        feature_key = str(_mod_normalize(state).get("audio_sha", "") or "")
        set_engine_audio_mod_state(win._engine, _mod_signature(state, feature_key))
    except Exception:
        log.debug("push audio mod signature failed", exc_info=True)


def _make_edit_listener(win):
    """调制编辑监听（清查②#2 P1）：提交前先拍 DOMAIN_TIMELINE 域快照——修前
    PEN/右键菜单/编辑页/Draw 应用直接落 _on_audio_mod_edited（无快照），
    Ctrl+Z 撤的是更早的无关手势。一步手势=恰好一条撤销步。"""
    def _edit_with_snapshot(st, _w=win):
        try:
            pm = getattr(_w, "_pm", None)
            if pm is not None:
                from ..core.project_manager import DOMAIN_TIMELINE
                from .timeline.timeline_perf import timeline_gesture_snapshot
                if not timeline_gesture_snapshot(pm, DOMAIN_TIMELINE):
                    # 清查②②：快照未压栈（pm 缺失等）→ 编辑照常提交但下次
                    # Ctrl+Z 会撤到更早手势——告警留痕不拦提交。
                    # （清查⑧：本函数体内用模块级 `log`；`logging` 未导入会 NameError）
                    log.warning("调制编辑提交前快照未压栈（下次撤销将跳过本步）")
        except Exception:
            log.warning("调制编辑快照异常（下次撤销将跳过本步）", exc_info=True)
        return _on_audio_mod_edited(_w, st)
    return _edit_with_snapshot


def _page_has_pending_edits(attr: str, page) -> bool:
    """页面有未应用/挂起编辑 → 跳过 undo 回灌（清查③②A/G + ⑤②#6）。

    Draw 页看 _dirty_hint（未应用画布）；映射页看 _commit_timer 活动期
    （250ms 防抖窗口内 set_context 会 stop 丢弃挂起改动且无脏标记可提示）。
    """
    import logging
    if attr == "_audio_mod_draw_editor" and bool(
            getattr(page, "_dirty_hint", False)):
        logging.getLogger(__name__).debug(
            "undo 回灌：Draw 页有未应用编辑，保留画布（工程已回滚）")
        return True
    if attr == "_audio_mod_editor":
        timer = getattr(page, "_commit_timer", None)
        if timer is not None and timer.isActive():
            logging.getLogger(__name__).debug(
                "undo 回灌：映射页有挂起编辑，跳过（防静默丢失）")
            return True
    return False


def _rehydrate_mod_pages(win, st: dict) -> None:
    """undo 后回灌 Draw 大页/映射页（清查③②A/G）。

    A（P1）：Draw 页有未应用画布编辑（_dirty_hint）时**保留画布不重灌**——
    修前任意域 Ctrl+Z 都无条件 set_context → _undo_reset 静默销毁未应用工作
    +清页面撤销栈+抹「未应用」徽标。
    G（P3）：set_context 失败置脏提示，绝不留"以为是最新的陈旧内容"。
    """
    import logging
    for attr in ("_audio_mod_draw_editor", "_audio_mod_editor"):
        page = getattr(win, attr, None)
        if page is None or _page_has_pending_edits(attr, page):
            continue
        try:
            page.set_context(st, _audio_mod_scope_channels(win, st))
        except Exception:
            try:
                page._dirty_hint = True
            except Exception:
                pass
            logging.getLogger(__name__).debug(
                f"undo 回灌：{attr} set_context 失败（已置未应用提示）",
                exc_info=True)


def _restore_mod_after_undo(win, project) -> None:
    """undo/redo 后回灌调制面（清查②#1 P0 + ②②页面版）——修前 _state 停在
    拖后状态：行内画布画 undo 前的 clip（UI/引擎分叉），且下次保存把陈旧
    _state 写回已回滚工程；Draw 大页/映射页持陈旧 _state 同病（PM undo 不发
    graph_changed，无"未应用"提示，下次提交整包写回）。"""
    try:
        _restore_audio_mod_state(win, project)
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "undo 回灌：调制轨恢复失败", exc_info=True)
    st = getattr(project, "audio_modulation_state", {}) or {}
    _rehydrate_mod_pages(win, st)   # ★ R183 清查③②A/G（见 helper）


def _restore_audio_mod_state(win, project) -> None:
    """工程 → 调制轨 widget（首次调用注入编辑监听）；project=None 复位为空。

    ★ R182 四轮审计修：调用即置 `_state_hydrated=True`——`_save_audio_mod_state`
      以此判定"widget 已持有工程状态"，未水合（打开工程未恢复路径）不回写。
    """
    try:
        from src.engine.audio_mod_driver import invalidate_caches
        invalidate_caches()   # 换工程/清空：调制状态与特征缓存失效（R174#7）
    except Exception:
        pass
    mod = win._timeline_audio_mod_track()
    if mod is None:
        return
    mod._state_hydrated = True
    if not getattr(mod, "_listener_wired", False):
        mod.set_edit_listener(_make_edit_listener(win))
        mod._listener_wired = True
    state = getattr(project, "audio_modulation_state", {}) or {} if project else {}
    mod.set_state(state)
    _push_mod_signature(win, state)   # ★ 烘焙失效（换工程/换歌/清空同路径）
    prepare_audio_workspace(win)


def _prefer_live_audio_path(live_path: str, stored_path: str,
                            resolve_fn, project_path: str) -> str:
    """节目切回/编辑时，择优选「实时音频轨路径」还是「节目已存路径」（R120b）。

    ★★ 用户实报根因（实测复现）：音频轨对象**始终持有最初导入时的原始路径**
      （如 `D:/music/songB.mp3`），而 `_save_current_timeline_to_program` 每次
      保存/切节目都拿它去覆盖 `program.audio_path`。原始文件还在时，保存会
      重新打包（能用但每次重拷）；**原始文件一旦不可用**（移走 / U 盘拔了 /
      工程拷到别的机器），打包失败 → 节目路径就停在那个死掉的原始路径上 →
      重开工程报「音频找不到」，尽管工程资源目录里**已有可用的打包副本**。

    规则：实时路径可解析 → 用它（用户可能刚换了音频）；不可解析而节目已存路径
    可解析 → **保留已存路径**（不把好路径覆盖成死路径）。
    """
    live = str(live_path or "")
    if _audio_path_ok(resolve_fn, project_path, live):
        return live
    if _audio_path_ok(resolve_fn, project_path, stored_path):
        return stored_path
    return live or stored_path


def _audio_path_ok(resolve_fn, project_path: str, path: str) -> bool:
    """路径在当前工程下是否可解析为存在的文件（异常一律当不可用）。"""
    if not path or resolve_fn is None:
        return False
    try:
        return bool(resolve_fn(project_path, path).is_file())
    except Exception:
        return False


def _heal_stored_audio_path(resolve_fn, project_path: str, audio_path: str) -> str:
    """把可解析（含兜底找回）的存储路径**回写**成标准相对形式（R120）。

    历史损坏路径（自动保存改基准所致）在 `_resolve_project_audio_path` 的兜底里
    被找回；这里把它规范化后写回工程，使**下次保存即自愈**。
    返回规范化后的路径（无需修改时原样返回）。`resolve_fn` = 调用方的解析器
    （模块级函数化以便 MainWindow 保持方法数基线，上帝类冻结 255 方法）。
    """
    if not audio_path or resolve_fn is None:
        return audio_path
    try:
        resolved = resolve_fn(project_path, audio_path)
    except Exception:
        return audio_path
    if not resolved.is_file():
        return audio_path
    return _stored_audio_path(project_path, resolved) or audio_path


def _stored_audio_path(project_path: str, audio_file: Path) -> str:
    """音频绝对路径 → **相对项目目录**的存储形式（R120 统一基准）。

    与 `_bundle_audio_path_for_save` 的存储口径一致（正斜杠分隔）。跨盘符
    （relpath 抛 ValueError）时退回绝对路径 —— 绝对路径本来就能解析。
    """
    try:
        return os.path.relpath(
            str(audio_file), start=str(Path(project_path).expanduser().parent)
        ).replace(os.sep, "/")
    except ValueError:
        return str(audio_file)


#: R188：音频资产查找结果缓存（含负结果）。修前 200ms 状态栏刷新每次经
#: `_resolve_project_audio_path` → 本函数全盘浅扫（工作区几十个一级子目录 ×
#: 3 个 glob 模式）；音频缺失时永远找不到也永远重复扫描 = 实测 4000 次/秒
#: lstat 全部压在主线程——"每个页面都卡"的通用主犯之一。缓存键含 root/name/
#: max_depth，保存写入资产与打开工程时显式清空（_invalidate_audio_asset_lookup）。
_AUDIO_ASSET_LOOKUP: dict = {}


def _invalidate_audio_asset_lookup() -> None:
    """音频资产目录可能变化时调用（保存复制后 / 打开工程后）。"""
    _AUDIO_ASSET_LOOKUP.clear()


def _find_audio_asset_under(root: Path, name: str, max_depth: int = 2) -> Optional[Path]:
    """在 `root` 下按**文件名**查找音频资源（修复历史损坏的相对路径）。

    ★★ R120：自动保存曾把 `program.audio_path` 改写成「相对 autosave 目录」的
      路径（如 `水秀_autosave_20260920_x_assets/audio/audio_ab12.mp3`），该路径
      在主工程目录下不存在 → 用户重开工程必然报「音频缺失」，且再保存一次就把
      它**永久写回**工程文件。本函数按文件名在项目资源目录里兜底找回：
        · `<root>/*_assets/audio/<name>`（正常布局）
        · `<root>/autosave/*_assets/audio/<name>`（历史自动保存布局）
        · `<root>/*/audio/<name>`（其它一层子目录）
      只做浅层 glob（不递归全盘），命名冲突时按名称排序取第一个（确定性）。
    """
    if not name or not root:
        return None
    key = (str(root), str(name), int(max_depth))
    if key in _AUDIO_ASSET_LOOKUP:
        return _AUDIO_ASSET_LOOKUP[key]     # R188：idle 刷新命中零扫描
    patterns = [f"*_assets/audio/{name}"] if max_depth >= 1 else []
    if max_depth >= 2:
        patterns += [f"autosave/*_assets/audio/{name}", f"*/audio/{name}"]
    found = None
    for pat in patterns:
        try:
            hits = sorted(p for p in root.glob(pat) if p.is_file())
        except OSError:
            continue
        if hits:
            found = hits[0]
            break
    _AUDIO_ASSET_LOOKUP[key] = found
    return found


#: L 条：渲染时间线 primary 主色钮已随 R178 后台自动烘焙移除（按钮+信号+消费
#: 全家退场，烘焙改由 src/engine/timeline_autobake.py 后台接管）。


class TransportBar(QWidget):
    play_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    seek_changed = pyqtSignal(float)
    record_started = pyqtSignal()
    record_stopped = pyqtSignal()
    timecode_fps_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playing = False
        self._setup_ui()

    def _mk_btn(self, text: str, tip: str,
                bg: str = BG_PANEL, fg: str = TEXT_PRIMARY,
                icon_key: str = "") -> QPushButton:
        b = QPushButton(text)
        key = icon_key if icon_key in _PANEL_ICON_NAMES else _panel_key_from_text(text, tip)
        b.setIcon(_panel_icon(key, 16))          # 主传输区图标统一 16px tabler
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setFixedHeight(24)   # L 条：传输条 32px 行内统一 24px 控件高
        # 优先使用主题工厂样式，回退到内联 _css_btn
        if _themed_btn is not None:
            b.setStyleSheet(_themed_btn(bg=bg, fg=fg, hover_bg=BG_BUTTON_HOVER,
                                        font_size=12, padding="0 8px"))
        else:
            b.setStyleSheet(_css_btn(bg, fg, BG_BUTTON_HOVER,
                                     border=BORDER_NORMAL, font_size=12,
                                     padding="0 8px"))
        return b

    def _mk_state_btn(self, text: str, tip: str,
                      fill_hex: str, base_alpha: int, fg: str,
                      icon_key: str = "", checkable: bool = False) -> QPushButton:
        """L 条语义状态钮：**常态无色块底**，与普通平面钮同面，
        语义 hue 只出现在 ≤24px 的亮色 glyph 上（§11.1 净化规则）；
        checkable 的钮在激活（checked）时才叠 rgba 低饱和底 + 同色描边。

        record/play 特例：按规范用 bright_error / bright_success 圆点、
        三角 glyph。fill_hex/fg 形参沿用调用点签名，作为激活态配色来源。
        """
        b = self._mk_btn(text, tip)
        key = icon_key if icon_key in _PANEL_ICON_NAMES else ""
        if key == "record":
            ic = load_tabler_icon("point", 16, BRIGHT_ERROR)
            b.setIcon(ic if not ic.isNull() else make_main_icon("record", 16))
        elif key == "play":
            ic = load_tabler_icon("player-play", 16, BRIGHT_SUCCESS)
            b.setIcon(ic if not ic.isNull() else make_main_icon("play", 16))
        elif key:
            b.setIcon(make_main_icon(key, 16))
        if checkable:
            b.setCheckable(True)
            b.setStyleSheet(
                self._btn_base_qss() +
                f"QPushButton:checked {{ background:{_rgba_i(fill_hex, 55)};"
                f" color:{fg}; border-color:{_rgba_i(fg, 120)}; }}"
                f"QPushButton:checked:hover {{ background:{_rgba_i(fill_hex, 80)};"
                f" border-color:{fg}; }}"
                f"QPushButton:pressed {{ background:{_rgba_i(fill_hex, 100)};"
                f" color:{TEXT_SELECTED_FG}; }}"
            )
        return b

    @staticmethod
    def _btn_base_qss() -> str:
        """传输条平面钮基底样式（与 _mk_btn 的主题工厂输出等价的内联回退）。"""
        return (
            f"QPushButton {{ background:{BG_PANEL}; color:{TEXT_PRIMARY};"
            f" border:1px solid {BORDER_NORMAL}; border-radius:4px;"
            " font-size:12px; padding:0 8px; }"
            f"QPushButton:hover {{ background:{BG_BUTTON_HOVER};"
            f" border-color:{HOVER_BORDER}; }}"
            f"QPushButton:pressed {{ background:{_hex_alpha(BG_PANEL, 'aa')}; }}"
            f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
            "QPushButton:disabled {"
            f" background:{BG_PANEL_SUB}; color:{TEXT_DISABLED};"
            f" border-color:{FIELD_DISABLED_BORDER}; }}"
        )

    @staticmethod
    def _numslider_bind(slider, decimals: int, signal) -> None:
        """NumSlider 内部 int 值（float×10^decimals）→ 对外 float 信号。

        呈现层换控件后的数值适配：对外发射值与旧 QDoubleSpinBox 完全同值，
        下游信号连接对象/顺序保持不变。
        """
        den = 10 ** int(decimals)
        slider.valueChanged.connect(
            lambda v, d=den, n=int(decimals): signal.emit(round(v / d, n)))

    def _setup_ui(self):
        # L 条：传输条行高 32（四行节奏 菜单24/工具栏30/面板30/传输32），
        # 左右内缩 8px；组内控件间距 4px、垂直共中线（内容带 24px）。
        self.setFixedHeight(32)
        self.setStyleSheet(
            f"background:{BG_EDITOR}; border-bottom:1px solid {POPUP_BORDER};")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)   # 组内间距统一 4px

        # ── 组1：走带六键 [⏮ ▶ ⏸ ⏹ ⏺ ⏭] ──────────────────────────────
        self._rew_btn   = self._mk_btn("", "回到开头", icon_key="skip_back")
        # 播放/录制为语义状态钮：常态无色块底，仅 glyph 着亮色；
        # 停止沿用中性面（避免纯红块），录制 checked 才显低饱和红底。
        self._play_btn  = self._mk_state_btn(
            "", "播放 (Space)", SUCCESS_GREEN, 55, GREEN, icon_key="play")
        self._pause_btn = self._mk_btn("", "暂停 (Space)", icon_key="pause")
        self._stop_btn  = self._mk_btn("", "停止 (Esc)", icon_key="stop")
        self._rec_btn   = self._mk_state_btn(
            "", "录制 (R)", ERROR_RED, 55, BRIGHT_ERROR, icon_key="record",
            checkable=True)
        self._fwd_btn   = self._mk_btn("", "跳到末尾", icon_key="skip_forward")

        self._rec_btn.setCheckable(True)

        self._rew_btn.clicked.connect(lambda: self.seek_changed.emit(0.0))
        self._play_btn.clicked.connect(self._on_play)
        self._pause_btn.clicked.connect(self._on_pause)
        self._stop_btn.clicked.connect(self._on_stop)
        self._rec_btn.toggled.connect(self._on_record)
        for b in (self._rew_btn, self._play_btn, self._pause_btn,
                  self._stop_btn, self._rec_btn, self._fwd_btn):
            lay.addWidget(b)

        lay.addWidget(_toolbar_sep())

        # ── 组2：渲染时间线按钮已移除（R178 后台自动烘焙接管） ─────────────
        # 时间线缓存由 TimelineAutoBaker 后台自动烘焙（编辑后 500ms 防抖重烘），
        # 进度见时间线 ruler 下方的烘焙缓存条；帧数据导出会等待烘焙完成。

        lay.addWidget(_toolbar_sep())

        # ── 组3：位置 / BPM / 速率（10px 弱化标签 + 凹陷弹性输入框） ─────
        _val_lbl_qss = (
            f"color:{TEXT_SECONDARY}; font-size:10px;"
            " background:transparent; border:none;")

        pos_lbl = QLabel("位置:")
        pos_lbl.setStyleSheet(_val_lbl_qss)
        lay.addWidget(pos_lbl)
        # 可调数值 → NumSlider（B 条）：值绘在滑槽上，右键弹数值输入；
        # 对外信号经 _numslider_bind 保序保值。L 条：最小宽 64 弹性拉伸。
        self._pos_spin = NumSlider(0.0, 9999.0, 0.0, decimals=2, suffix=" s")
        self._pos_spin.setSingleStep(10)          # 滚轮步进 0.10 s
        self._pos_spin.setMinimumWidth(64)
        self._pos_spin.setToolTip("时间线位置（秒），可拖拽 / 右键键入")
        self._numslider_bind(self._pos_spin, 2, self.seek_changed)
        lay.addWidget(self._pos_spin)

        # BPM / 速率控件已移除（2026-09-12）：PlaybackEngine 从未实现全局 BPM/速率
        # （set_bpm/set_rate 是 pass 兼容桩），引擎真实速度是逐 clip 的
        # _clip_playback_rate（由 clip 自身 speed/时长决定）。保留控件 = 拖了无反应
        # 的死 UI（"签名在、实现空"）。将来要做全局 BPM/速率，须先从引擎接口反推控件。

        lay.addWidget(_toolbar_sep())

        # ── 组4：TC FPS ─────────────────────────────────────────────────
        tc_lbl = QLabel("TC FPS:")
        tc_lbl.setStyleSheet(_val_lbl_qss)
        lay.addWidget(tc_lbl)
        from .slider_input_keys import reserve_input_keys
        self._tc_fps_combo = QComboBox()
        reserve_input_keys(self._tc_fps_combo)
        self._tc_fps_combo.addItem("24", 24)
        self._tc_fps_combo.addItem("25", 25)
        self._tc_fps_combo.addItem("30", 30)
        self._tc_fps_combo.addItem("29.97 DF", 29.97)
        self._tc_fps_combo.addItem("60", 60)
        self._tc_fps_combo.setCurrentIndex(1)  # 25 default
        self._tc_fps_combo.setMinimumWidth(72)
        self._tc_fps_combo.setFixedHeight(24)   # 与同排凹陷输入框共中线
        self._tc_fps_combo.setStyleSheet(
            f"QComboBox {{ background:{BG_INPUT}; color:{TEXT_PRIMARY};"
            f" border:1px solid {BORDER_NORMAL};"
            " border-radius:4px; padding:2px 4px; font-size:11px; }"
            f"QComboBox:hover {{ border-color:{BG_BUTTON_HOVER}; }}"
            f"QComboBox:focus {{ border-color:{FOCUS_BLUE}; }}"
            "QComboBox::drop-down { border:none; width:18px; }"
            f"QComboBox QAbstractItemView {{ background:{BG_DEEP}; color:{TEXT_PRIMARY};"
            f" selection-background-color:{SELECT_BLUE}; selection-color:{TEXT_SELECTED_FG}; }}"
        )
        self._tc_fps_combo.currentIndexChanged.connect(self._on_tc_fps_changed)
        lay.addWidget(self._tc_fps_combo)
        lay.addStretch()
        # 大时间码 → 凹陷数字窗贴右边缘（左右内缩 8 已含在 margins）：
        # bg_input 内底 + 1px 边 + Consolas 13px + bright_warn 数字，
        # 高 24 与同排控件共中线。
        self._timecode = QLabel("00:00.000")
        self._timecode.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        self._timecode.setStyleSheet(
            f"background:{BG_INPUT}; border:1px solid {BORDER_NORMAL};"
            " border-radius:4px; padding:2px 8px; min-width:88px;"
            f" color:{AMBER};"
            " font-family:'Consolas','DejaVu Sans Mono',monospace;"
            " font-size:13px;")
        self._timecode.setFixedHeight(24)
        lay.addWidget(self._timecode)

    def _on_play(self):
        self._playing = True
        self.play_clicked.emit()

    def _on_pause(self):
        self._playing = False
        self.pause_clicked.emit()

    def _on_stop(self):
        self._playing = False
        self.stop_clicked.emit()

    def _on_record(self, checked: bool):
        if checked:
            self.record_started.emit()
        else:
            self.record_stopped.emit()

    def set_recording(self, recording: bool):
        self._rec_btn.blockSignals(True)
        self._rec_btn.setChecked(recording)
        self._rec_btn.blockSignals(False)

    def update_position(self, pos: float):
        # _pos_spin 已换为 NumSlider（呈现层换皮，属性名沿用）：
        # 浮点只走 set_float/float_value，回灌播放位置不触发 seek 回环。
        self._pos_spin.blockSignals(True)
        self._pos_spin.set_float(float(pos))
        self._pos_spin.blockSignals(False)

        m  = int(pos // 60)
        s  = int(pos % 60)
        ms = int((pos - int(pos)) * 1000)
        # L 条大时间码格式 MM:SS.mmm（与初始占位一致）
        self._timecode.setText(f"{m:02d}:{s:02d}.{ms:03d}")

    def set_playing(self, playing: bool):
        self._playing = playing

    def set_tc_fps(self, fps: float):
        """Set combo to match project's timecode_fps value without emitting."""
        self._tc_fps_combo.blockSignals(True)
        idx = self._tc_fps_combo.findData(fps)
        if idx >= 0:
            self._tc_fps_combo.setCurrentIndex(idx)
        else:
            self._tc_fps_combo.setCurrentIndex(1)  # fallback 25
        self._tc_fps_combo.blockSignals(False)

    def _on_tc_fps_changed(self, idx: int):
        fps = float(self._tc_fps_combo.itemData(idx) or 25.0)
        self.timecode_fps_changed.emit(fps)


# ══════════════════════════════════════════════════════════════════════════════
#  AppStatusBar
# ══════════════════════════════════════════════════════════════════════════════

class AppStatusBar(QStatusBar):
    # TC 时间码标签样式（500ms 超时闪烁轮换的"亮"态共用一份字符串）
    # E 条三态对比微调：亮态加字重拉开与熄灭态(text_disabled)的对比，
    # 轮换机制本身保持不变。
    _TC_ON_QSS = (f"color:{BRIGHT_WARN}; padding:0 8px;"
                  " font-family:Consolas,monospace; font-weight:600;")
    _TC_OFF_QSS = (f"color:{TEXT_DISABLED}; padding:0 8px;"
                   " font-family:Consolas,monospace;")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QStatusBar {"
            f" background:{BG_DEEP}; color:{TEXT_SECONDARY};"
            f" border-top:1px solid {POPUP_BORDER}; font-size:11px;"
            "}"
            "QStatusBar::item { border:none; }"
        )

        self._net_label = QLabel("● 网络: 未连接")
        self._fps_label = QLabel("FPS: --")
        self._dmx_label = QLabel("DMX: --")

        # TimeCode 显示
        self._tc_label = QLabel("TC: --:--:--:--")
        self._tc_label.setStyleSheet(self._TC_ON_QSS)
        self._tc_label.setVisible(False)

        # TimeCode 超时检测：>2s 未收到则闪烁
        self._last_tc_time: float = 0.0
        self._tc_visible: bool = True
        self._tc_blink_timer = QTimer(self)
        self._tc_blink_timer.setInterval(500)
        self._tc_blink_timer.timeout.connect(self._check_tc_timeout)
        self._tc_blink_timer.start()

        for i, lbl in enumerate(
                (self._net_label, self._fps_label, self._dmx_label, self._tc_label)):
            if i:
                self.addPermanentWidget(self._make_vsep())
            lbl.setStyleSheet(f"color:{TEXT_DISABLED}; padding:0 8px;")
            self.addPermanentWidget(lbl)

        # Programmer 状态反馈去重（上一次消息）
        self._last_programmer_msg: str = ""

    @staticmethod
    def _make_vsep() -> QLabel:
        """状态栏 1px 竖线分隔（E 条；sep_line 令牌）。"""
        s = QLabel()
        s.setFixedSize(1, 12)
        s.setStyleSheet(f"background:{SEP_LINE};")
        return s

    def set_timecode(self, hours: int, minutes: int, seconds: int, frames: int):
        """Update timecode display and reset timeout timer."""
        self._tc_label.setText(
            f"TC: {hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}")
        self._tc_label.setStyleSheet(self._TC_ON_QSS)
        self._tc_label.setVisible(True)
        self._last_tc_time = time.time()
        self._tc_visible = True

    def _check_tc_timeout(self):
        """Blink timecode label if no TC received for >2 seconds."""
        now = time.time()
        if self._tc_label.isVisible() and (now - self._last_tc_time) > 2.0:
            self._tc_visible = not self._tc_visible
            # 轮换机制保留：亮态 bright_warn / 熄灭态 text_disabled（对比清晰）
            if self._tc_visible:
                self._tc_label.setStyleSheet(self._TC_ON_QSS)
            else:
                self._tc_label.setStyleSheet(self._TC_OFF_QSS)

    def clear_timecode(self):
        """Hide timecode display."""
        self._tc_label.setVisible(False)
        self._last_tc_time = 0.0

    def set_network(self, running: bool, protocol: str = ""):
        if running:
            self._net_label.setText(f"● 网络: {protocol} 运行中")
            style = f"color:{BRIGHT_SUCCESS}; padding:0 8px;"
        else:
            self._net_label.setText("● 网络: 未连接")
            style = f"color:{TEXT_DISABLED}; padding:0 8px;"
        if self._net_label.styleSheet() != style:
            self._net_label.setStyleSheet(style)

    def set_fps(self, fps: float):
        """刷新 FPS 标签；`None`/非正值 = 无播放 tick → 显示 "--"（R182）。"""
        try:
            ok = fps is not None and float(fps) > 0.0
        except (TypeError, ValueError):
            ok = False
        if ok:
            self._fps_label.setText(f"FPS: {float(fps):.1f}")
        else:
            self._fps_label.setText("FPS: --")

    def set_dmx(self, text: str):
        """刷新 DMX 标签（R182）。`text` 为完整文案（如 "DMX: 40Hz·2域"）。"""
        if text:
            self._dmx_label.setText(str(text))

    def set_message(self, msg: str, timeout: int = 3000):
        self.showMessage(msg, timeout)

    def set_programmer_feedback(self, msg: str, timeout: int = 3000):
        """Programmer 模块操作反馈（带去重）"""
        if msg != self._last_programmer_msg:
            self._last_programmer_msg = msg
            self.showMessage(f"◆ {msg}", timeout)


# ══════════════════════════════════════════════════════════════════════════════
#  _MicLevelBar
# ══════════════════════════════════════════════════════════════════════════════

class _MicLevelBar(QWidget):
    """麦克风实时电平条，显示当前麦克风输入的 RMS 电平。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0   # 0.0 ~ 1.0
        self._peak  = 0.0   # 峰値保持
        self._peak_decay = 0
        self.setFixedSize(60, 24)  # 与同排 24px 面板工具栏按钮对齐（行高 30 内容带 24）
        self.setToolTip("麦克风输入电平")

    def set_level(self, rms: float):
        """设置当前电平（rms 范围 0.0~1.0）。"""
        self._level = max(0.0, min(1.0, rms))
        if self._level > self._peak:
            self._peak = self._level
            self._peak_decay = 0
        else:
            self._peak_decay += 1
            if self._peak_decay > 20:
                self._peak = max(0.0, self._peak - 0.02)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 背景
        p.fillRect(0, 0, w, h, QColor(BG_ROOT))
        # 电平条（阈值 0.6/0.85 断点保持不变；颜色映射到 主题亮色家族）
        fill_w = int(w * self._level)
        if fill_w > 0:
            # 根据电平高低变色：绿色→黄色→红色
            if self._level < 0.6:
                color = QColor(BRIGHT_SUCCESS)
            elif self._level < 0.85:
                color = QColor(BRIGHT_WARN)
            else:
                color = QColor(BRIGHT_ERROR)
            p.fillRect(1, 2, fill_w - 2, h - 4, color)
        # 峰値指示线
        peak_x = int(w * self._peak)
        if peak_x > 2:
            p.setPen(QColor(TEXT_SELECTED_FG))
            p.drawLine(peak_x, 2, peak_x, h - 3)
        # 边框
        p.setPen(QColor(BORDER_NORMAL))
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  PanelToolbar
# ══════════════════════════════════════════════════════════════════════════════

class PanelToolbar(QWidget):
    monitor_toggled    = pyqtSignal(bool)
    processor_toggled  = pyqtSignal(bool)
    network_toggled    = pyqtSignal(bool)
    patch_toggled          = pyqtSignal(bool)
    topview_toggled        = pyqtSignal(bool)
    route_order_toggled    = pyqtSignal(bool)
    programmer_toggled     = pyqtSignal(bool)
    matrix_pattern_toggled = pyqtSignal(bool)
    fixture_group_toggled  = pyqtSignal(bool)
    audio_bridge_toggled   = pyqtSignal(bool)
    choreography_toggled   = pyqtSignal(bool)  # AI编舞引擎
    studio_toggled         = pyqtSignal(bool)  # Clip 附加处理 Studio（R172 A6 替换平滑面板钮）
    dispatch_toggled       = pyqtSignal(bool)  # 调度台（效果总线 + 缓存）
    add_track_clicked = pyqtSignal()
    del_track_clicked = pyqtSignal()
    zoom_in_clicked   = pyqtSignal()
    zoom_out_clicked  = pyqtSignal()
    net_start_clicked = pyqtSignal()
    net_stop_clicked  = pyqtSignal()
    # 全局音频来源切换信号："simulate" / "file" / "microphone"
    audio_source_changed = pyqtSignal(str)
    # 外部控制设置入口已从主窗口移除；信号保留仅为兼容旧引用。
    joystick_settings_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # L 条：面板开关行高 30（四行节奏 菜单24/工具栏30/面板30/传输32），
        # 内容带 24px（上下各留 3）使全部钮/标签垂直共中线；
        # 组内间距 4px，组间竖分隔。
        # WA_StyledBackground 让本条的 bg_deep 底色真正绘制（普通 QWidget
        # 不设此属性时样式表背景会被跳过，出现"子件各自补底色"的碎斑）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.setStyleSheet(
            "QWidget {"
            f" background:{BG_DEEP};"
            f" border-bottom:1px solid {POPUP_BORDER};"
            "}"
        )
        self._setup_ui()

    def add_tail_separator(self):
        self._tail_sep.setVisible(True)

    def add_tail_widget(self, w):
        self._tail_lay.addWidget(w)
        w.show()   # 从隐藏的旧父容器搬入后需显式 show

    @staticmethod
    def _cap(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:10px;"
            " background:transparent; border:none;")
        return lbl

    @staticmethod
    def _sep() -> QLabel:
        return _toolbar_sep()

    def _mk_toggle(self, icon: str, label: str,
                   tip: str, accent: str) -> QPushButton:
        key = str(icon or "")
        if key not in _PANEL_ICON_NAMES:
            key = _panel_key_from_text(icon, label, tip)
        btn = QPushButton(label, self)
        btn.setIcon(_panel_icon(key, 16))       # D 条：图标统一 16px tabler
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        btn.setCheckable(True)
        # L 条统一规格：图标+文字平面钮 padding(2px 8px)、行内高 24 共中线；
        # accent 形参保留仅为调用点签名兼容，样式不再逐钮着色
        #（统一切换到 select_blue 轻量激活语义）。
        btn.setFixedHeight(24)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(self._css_panel_toggle())
        return btn

    @staticmethod
    def _css_panel_toggle() -> str:
        """L 条：面板开关钮统一规格。

        未选中 = 透明底 text_secondary；选中 = select_blue 文字 +
        rgba(71,114,179,45) 轻底（不再是实色蓝块），hover 仅提灰阶。
        """
        return (
            "QPushButton {"
            " background:transparent;"
            f" color:{TEXT_SECONDARY};"
            " border:1px solid transparent; border-radius:4px;"
            # ★ 2026-09-14 用户："主界面那个横排后面空间很挤了…把控件宽度调小"
            #   收紧 padding(8→5) 与字号(11→10)，每个开关省 ~8px 宽
            " font-size:10px; padding:2px 5px;"
            "}"
            f"QPushButton:hover:!checked {{ background:{BG_PANEL};"
            f" color:{TEXT_TERTIARY}; }}"
            f"QPushButton:pressed {{ background:{BG_PANEL_SUB}; }}"
            f"QPushButton:checked {{ background:{_rgba_i(SELECT_BLUE, 45)};"
            f" color:{SELECT_BLUE}; }}"
            f"QPushButton:checked:hover {{ background:{_rgba_i(SELECT_BLUE, 70)};"
            f" color:{SELECT_BLUE}; }}"
            f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
            f"QPushButton:disabled {{ color:{TEXT_DISABLED}; }}"
        )

    def _mk_action(self, icon: str, label: str, tip: str,
                   bg: str, fg: str, hover_bg: str) -> QPushButton:
        key = str(icon or "")
        if key not in _PANEL_ICON_NAMES:
            key = _panel_key_from_text(icon, label, tip)
        btn = QPushButton(label, self)
        btn.setIcon(_panel_icon(key, 16))       # 图标统一 16px tabler
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        btn.setFixedHeight(24)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(_css_btn(bg, fg, hover_bg,
                                   border=_hex_alpha(hover_bg, '88'),
                                   font_size=10, padding="0 5px"))
        return btn

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(4)   # L 条：组内控件间距统一 4px

        lay.addWidget(self._cap("面板:"))

        self._monitor_btn = self._mk_toggle(
            "monitor", "监视", "显示 / 隐藏 DMX 通道监视器", BRIGHT_SUCCESS)
        self._monitor_btn.toggled.connect(self.monitor_toggled)
        lay.addWidget(self._monitor_btn)

        self._processor_btn = self._mk_toggle(
            "processor", "关联", "显示 / 隐藏轨道关联器面板", FOCUS_BLUE)
        self._processor_btn.toggled.connect(self.processor_toggled)
        lay.addWidget(self._processor_btn)

        self._network_btn = self._mk_toggle(
            "network", "网络", "显示 / 隐藏网络配置面板", FOCUS_BLUE)
        self._network_btn.toggled.connect(self.network_toggled)
        # 顶层工具栏精简：网络配置入口当前未接实际后端，避免占用主界面横向空间。
        self._network_btn.setVisible(False)

        # ── 新增功能面板按钮 ──────────────────────────────────────────────────
        self._patch_btn = self._mk_toggle(
            "patch", "配接", "显示 / 隐藏 Patch 设备配接面板", BRIGHT_WARN)
        self._patch_btn.toggled.connect(self.patch_toggled)
        lay.addWidget(self._patch_btn)

        self._topview_btn = self._mk_toggle(
            "topview", "TopView", "显示 / 隐藏 TopView 2D 布局面板", BRIGHT_INFO)
        self._topview_btn.toggled.connect(self.topview_toggled)
        lay.addWidget(self._topview_btn)

        self._route_order_btn = self._mk_toggle(
            "route_order", "跑动编排", "显示 / 隐藏跑动编排窗口 (火车物理编辑器)", FOCUS_BLUE)
        self._route_order_btn.toggled.connect(
            lambda v: self.window()._toggle_route_animator(v))
        lay.addWidget(self._route_order_btn)

        self._programmer_btn = self._mk_toggle(
            "programmer", "编程", "显示 / 隐藏 Programmer 音频编程面板", ACTIVE_ORANGE)
        self._programmer_btn.toggled.connect(self.programmer_toggled)
        lay.addWidget(self._programmer_btn)

        self._matrix_pattern_btn = self._mk_toggle(
            "matrix", "矩阵", "显示 / 隐藏矩阵 ON/OFF PPT 编辑器", BRIGHT_SUCCESS)
        self._matrix_pattern_btn.toggled.connect(self.matrix_pattern_toggled)
        lay.addWidget(self._matrix_pattern_btn)

        self._fixture_group_btn = self._mk_toggle(
            "fixture_group", "设备组", "显示 / 隐藏设备选择组面板", BRIGHT_WARN)
        self._fixture_group_btn.toggled.connect(self.fixture_group_toggled)
        lay.addWidget(self._fixture_group_btn)

        self._studio_btn = self._mk_toggle(
            "effects", '附加处理', '显示 / 隐藏 Clip 附加处理（卡片画布 Studio）', FOCUS_BLUE)
        self._studio_btn.toggled.connect(self.studio_toggled)
        lay.addWidget(self._studio_btn)

        # 调度台入口（★ 2026-09-14 恢复；dock 自带"默认独立窗口"，见 _maybe_float_default）
        self._dispatch_btn = self._mk_toggle(
            "dispatch", "调度台",
            "显示 / 隐藏调度台（当前选中设备的实时监控 + 效果场景存储）", SELECT_BLUE)
        self._dispatch_btn.toggled.connect(self.dispatch_toggled)
        lay.addWidget(self._dispatch_btn)

        # AI编舞按钮已删除（后端保留）
        self._choreography_btn = None  # 保留属性避免其他地方引用报错

        # 轨道添加/删除按鈕已从工具条移除（时间线区域的按鈕保留），保留属性和信号占位以兼容旧代码引用。
        self._add_btn = self._mk_action(
            "track_add", "轨道", "添加新 DMX 轨道  (Ctrl+T)",
            bg=BG_BOX, fg=BRIGHT_SUCCESS, hover_bg=BG_PANEL)
        self._add_btn.clicked.connect(self.add_track_clicked)
        self._add_btn.setVisible(False)  # 已从工具条移除

        self._del_btn = self._mk_action(
            "track_delete", "删除", "删除当前选中的轨道  (Del)",
            bg=BG_BOX, fg=BRIGHT_ERROR, hover_bg=BG_PANEL)
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self.del_track_clicked)
        self._del_btn.setVisible(False)  # 已从工具条移除

        # 顶层工具栏精简：轨道缩放与网络启停按钮当前未接入稳定后端/快捷路径，
        # 从一打开就可见的主按钮栏移除，仅保留信号和按钮属性占位以兼容旧代码引用。
        self._zin_btn = self._mk_action("zoom_in", "+", "放大时间线  (=)", bg=BG_BOX, fg=FOCUS_BLUE, hover_bg=BG_PANEL)
        self._zin_btn.clicked.connect(self.zoom_in_clicked)
        self._zin_btn.setVisible(False)
        self._zout_btn = self._mk_action("zoom_out", "−", "缩小时间线  (-)", bg=BG_BOX, fg=FOCUS_BLUE, hover_bg=BG_PANEL)
        self._zout_btn.clicked.connect(self.zoom_out_clicked)
        self._zout_btn.setVisible(False)

        lay.addWidget(self._sep())

        # ── 全局音频来源切换：等宽拼接分段控件（L 条）──────────────────
        lay.addWidget(self._cap("音频源:"))

        for attr, label, icon_key, tip, first, last in (
            ("_audio_sim_btn",  "模拟",   "audio_sim", "音频识别源: 模拟数据",           True,  False),
            ("_audio_file_btn", "文件",   "audio_file", "音频识别源: 导入音频文件",       False, False),
            ("_audio_mic_btn",  "麦克风", "audio_mic",  "音频识别源: 外接麦克风实时识别", False, True),
        ):
            btn = QPushButton(label)
            btn.setIcon(_panel_icon(icon_key, 16))
            btn.setIconSize(QSize(16, 16))
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            # ★ 2026-09-14：原 `setFixedWidth(76)` 三段共 228px **完全不可压**，
            #   是"横排后面很挤"的最大刚性块。改为最小宽可压缩，窗口变窄时能缩。
            btn.setMinimumWidth(52)
            btn.setStyleSheet(self._audio_source_qss(first=first, last=last))
            setattr(self, attr, btn)
            lay.addWidget(btn)

        self._audio_sim_btn.setChecked(True)

        self._audio_sim_btn.clicked.connect(lambda: self._on_audio_source_btn("simulate"))
        self._audio_file_btn.clicked.connect(lambda: self._on_audio_source_btn("file"))
        self._audio_mic_btn.clicked.connect(lambda: self._on_audio_source_btn("microphone"))

        # 麦克风实时电平条（仅麦克风模式显示）
        self._mic_level_bar = _MicLevelBar()
        self._mic_level_bar.setVisible(False)
        lay.addWidget(self._mic_level_bar)

        # R52：尾部挂载区（全清空等入驻；addStretch 之前）
        self._tail_sep = self._sep()
        self._tail_sep.setVisible(False)   # 有尾部控件时才显示
        lay.addWidget(self._tail_sep)

        self._tail_lay = QHBoxLayout()
        self._tail_lay.setContentsMargins(0, 0, 0, 0)
        self._tail_lay.setSpacing(4)
        lay.addLayout(self._tail_lay)

        # 主窗口不再显示外部控制绑定入口。
        # 底层驱动与历史设置逻辑保留；这里仅保留隐藏占位属性，避免旧代码引用报错。
        self._joy_status_lbl = QLabel("", self)
        self._joy_status_lbl.setVisible(False)
        self._joy_settings_btn = QPushButton("", self)
        self._joy_settings_btn.setVisible(False)

        lay.addStretch()

        self._net_start_btn = self._mk_action("network_start", "启动", "启动网络输出", bg=BG_BOX, fg=BRIGHT_SUCCESS, hover_bg=BG_PANEL)
        self._net_start_btn.clicked.connect(self.net_start_clicked)
        self._net_start_btn.setVisible(False)
        self._net_stop_btn = self._mk_action("network_stop", "停止", "停止网络输出", bg=BG_BOX, fg=BRIGHT_ERROR, hover_bg=BG_PANEL)
        self._net_stop_btn.clicked.connect(self.net_stop_clicked)
        self._net_stop_btn.setVisible(False)

        install_panel_toolbar_tiers(self)  # R173：窄屏逐级收进「»」溢出菜单

    @staticmethod
    def _audio_source_qss(first: bool = False, last: bool = False) -> str:
        """音频源三连「单选胶囊」共用样式（D 条）。

        首尾按钮保留外侧圆角，中段直角拼成一体（margin-left -1px 使相邻
        描边贴合不重线）；选中态 = select_blue 底白字，非激活 = 透明底
        text_secondary + hover 提升。旧版按 accent 三分色的复制粘贴串
        已合并为一套。
        """
        radius_l = "4px" if first else "0px"
        radius_r = "4px" if last else "0px"
        return (
            "QPushButton{"
            f"background:transparent;color:{TEXT_SECONDARY};"
            f"border:1px solid {SEP_LINE};"
            f"border-radius:{radius_l} {radius_r} {radius_r} {radius_l};"
            "font-size:11px;padding:0 8px;margin-left:-1px;}"
            "QPushButton:checked{"
            f"background:{SELECT_BLUE};color:{TEXT_SELECTED_FG};"
            f"border-color:{SELECT_BLUE};}}"
            "QPushButton:hover:!checked{"
            f"background:{BG_PANEL};color:{TEXT_TERTIARY};}}"
            f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}"
            "QPushButton:disabled{"
            f"background:{BG_PANEL_SUB};color:{TEXT_DISABLED};"
            f"border-color:{FIELD_DISABLED_BORDER};}}"
        )

    def _on_audio_source_btn(self, mode: str):
        """处理音频来源切换按钮点击，互斥选中，发射信号。
        点击麦克风时弹出设备选择对话框。"""
        if mode == "microphone":
            selected = self._select_mic_device_dialog()
            if selected is None:
                # 用户取消，不切换
                return
            # 记录选中的设备索引（-1 = 系统默认）
            self._selected_mic_device = selected
        for btn, m in (
            (self._audio_sim_btn,  "simulate"),
            (self._audio_file_btn, "file"),
            (self._audio_mic_btn,  "microphone"),
        ):
            btn.blockSignals(True)
            btn.setChecked(m == mode)
            btn.blockSignals(False)
        # 麦克风电平条仅在麦克风模式下显示
        self._mic_level_bar.setVisible(mode == "microphone")
        self.audio_source_changed.emit(mode)

    def _select_mic_device_dialog(self) -> Optional[int]:
        """弹出麦克风设备选择对话框，返回设备索引（-1=系统默认），取消返回 None。"""
        # 枚举输入设备
        devices = []
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if d.get('max_input_channels', 0) > 0:
                    devices.append((i, d['name']))
        except Exception:
            pass

        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
            QDialogButtonBox, QLabel as _QL
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("选择麦克风设备")
        dlg.setMinimumWidth(400)
        # 全局 QSS 已覆盖 QListWidget / 按钮 的通用外观（含四态），
        # 这里只保留弹层基底色特化（restyle_spec §3）。
        dlg.setStyleSheet(
            f"QDialog{{background:{BG_DEEP};color:{TEXT_PRIMARY};}}"
        )
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        hint = _QL("选择用于音频识别的麦克风设备：")
        hint.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        lay.addWidget(hint)

        lst = QListWidget()
        # 第一项：系统默认（emoji 字面量 → 内置矢量图标，Windows 渲染更稳）
        default_item = QListWidgetItem("系统默认麦克风")
        try:
            from .main_icon_library import make_main_icon
            default_item.setIcon(make_main_icon("audio_mic", 18))
        except Exception:
            pass
        default_item.setData(0x100, -1)
        lst.addItem(default_item)
        for idx, name in devices:
            item = QListWidgetItem(f"[{idx}]  {name}")
            item.setData(0x100, idx)
            lst.addItem(item)
        # 选中当前已选设备
        cur = getattr(self, '_selected_mic_device', -1)
        for i in range(lst.count()):
            if lst.item(i).data(0x100) == cur:
                lst.setCurrentRow(i)
                break
        if lst.currentRow() < 0:
            lst.setCurrentRow(0)
        lay.addWidget(lst)

        if not devices:
            no_dev = _QL("未检测到麦克风设备，将使用系统默认")
            no_dev.setStyleSheet(f"color:{BRIGHT_WARN};font-size:11px;")
            lay.addWidget(no_dev)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        cur_item = lst.currentItem()
        return cur_item.data(0x100) if cur_item else -1

    def set_joystick_status(self, connected: bool, name: str = ""):
        """更新实体摇杆连接状态显示。"""
        if connected:
            short = name[:16] + "..." if len(name) > 16 else name
            self._joy_status_lbl.setText("")
            self._joy_status_lbl.setStyleSheet(
                f"color:{BRIGHT_SUCCESS};font-size:10px;padding:0 4px;font-weight:bold;")
        else:
            self._joy_status_lbl.setText("")
            self._joy_status_lbl.setStyleSheet(
                f"color:{TEXT_DISABLED};font-size:10px;padding:0 4px;")

    def update_mic_level(self, rms: float):
        """更新麦克风电平条（rms 范围 0.0~1.0）。"""
        self._mic_level_bar.set_level(rms)

    def set_del_enabled(self, enabled: bool):
        self._del_btn.setEnabled(enabled)

    def sync_dock_state(self, monitor: bool, processor: bool, network: bool,
                        patch: bool = False, topview: bool = False,
                        programmer: bool = False, matrix_pattern: bool = False,
                        fixture_group: bool = False, route_order: bool = False):
        for btn, val in (
            (self._monitor_btn, monitor),
            (self._processor_btn, processor),
            (self._network_btn, network),
            (self._patch_btn, patch),
            (self._topview_btn, topview),
            (self._route_order_btn, route_order),
            (self._programmer_btn, programmer),
            (self._matrix_pattern_btn, matrix_pattern),
            (self._fixture_group_btn, fixture_group),
        ):
            btn.blockSignals(True)
            btn.setChecked(val)
            btn.blockSignals(False)


# ══════════════════════════════════════════════════════════════════════════════
#  FilterEditDialog
# ══════════════════════════════════════════════════════════════════════════════

class _UniverseFilterRow(QWidget):
    """单行域配置：Universe 编号 + 展开/折叠的独立通道过滤 + 删除按钮"""
    def __init__(self, universe: int, filter_rule=None,
                 removable: bool = True, parent=None):
        super().__init__(parent)
        from ..core.models import FilterRule
        self._filter_rule = filter_rule if filter_rule is not None else FilterRule()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(
            f"background:{BG_PANEL_SUB};border-radius:4px;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(6, 4, 6, 4)
        hlay.setSpacing(6)

        hlay.addWidget(QLabel("Universe:"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 32767)
        self.spin.setValue(universe)
        self.spin.setFixedWidth(90)
        self.spin.setStyleSheet(
            f"background:{BG_PANEL};color:{TEXT_PRIMARY};"
            f"border:1px solid {BORDER_NORMAL};"
            "padding:4px 8px;border-radius:4px;"
            f"QSpinBox:hover {{ border-color:{BG_BUTTON_HOVER}; }}"
            f"QSpinBox:focus {{ border-color:{FOCUS_BLUE}; }}")
        hlay.addWidget(self.spin)

        self._filter_label = QLabel(self._make_label())
        self._filter_label.setStyleSheet(f"color:{FOCUS_BLUE};font-size:11px;")
        hlay.addWidget(self._filter_label, stretch=1)

        self._expand_btn = QPushButton("通道过滤")
        self._expand_btn.setCheckable(True)
        self._expand_btn.setChecked(False)
        self._expand_btn.setFixedHeight(24)  # 与同排 ✕ (24×24) 基线一致
        self._expand_btn.setToolTip("展开 / 折叠该域的独立通道过滤设置")
        self._expand_btn.setStyleSheet(
            f"QPushButton{{background:{BG_BOX};border:1px solid {BORDER_NORMAL};color:{TEXT_PRIMARY};"
            "padding:2px 8px;border-radius:4px;font-size:11px;}"
            f"QPushButton:checked{{background:{SELECT_BLUE};border-color:{SELECT_BLUE};color:{TEXT_SELECTED_FG};}}"
            f"QPushButton:hover{{background:{BG_PANEL};}}"
            f"QPushButton:checked:hover{{background:{SELECT_BLUE_HOVER};}}"
            f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}")
        self._expand_btn.toggled.connect(self._toggle_filter)
        hlay.addWidget(self._expand_btn)

        self.del_btn = QPushButton("✕")
        self.del_btn.setFixedSize(24, 24)  # 原为 22×22，命中区过小
        self.del_btn.setToolTip("移除该域")  # 补充 tooltip（审计交互#5）
        self.del_btn.setStyleSheet(
            f"QPushButton{{background:{BG_BOX};border:1px solid {BORDER_NORMAL};color:{BRIGHT_ERROR};"
            "border-radius:4px;font-weight:bold;font-size:12px;}"
            # L 条净化：hover/pressed 不再整块实色红，改 rgba 低饱和底
            f"QPushButton:hover{{background:{_rgba_i(ERROR_RED, 90)};"
            f"border-color:{BRIGHT_ERROR};}}"
            f"QPushButton:pressed{{background:{_rgba_i(ERROR_RED, 120)};}}"
            f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}")
        self.del_btn.setVisible(removable)
        hlay.addWidget(self.del_btn)
        outer.addWidget(header)

        self._filter_panel = QWidget()
        self._filter_panel.setStyleSheet(
            f"background:{BG_BOX};border:1px solid {BORDER_NORMAL};border-radius:4px;")
        fp_lay = QVBoxLayout(self._filter_panel)
        fp_lay.setContentsMargins(8, 6, 8, 6)
        fp_lay.setSpacing(4)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(4)
        for label, fn in (
            ("全选 1-512", self._select_all),
            ("清空",       self._clear_all),
            ("奇数通道",   self._select_odd),
            ("偶数通道",   self._select_even),
        ):
            qb = QPushButton(label)
            qb.setFixedHeight(22)
            qb.setStyleSheet(
                f"QPushButton{{background:{BG_PANEL_SUB};border:1px solid {BORDER_NORMAL};"
                f"color:{TEXT_SECONDARY};padding:0 6px;border-radius:4px;font-size:11px;}}"
                f"QPushButton:hover{{background:{BG_PANEL};color:{TEXT_PRIMARY};}}"
                f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}")
            qb.clicked.connect(fn)
            quick_row.addWidget(qb)
        quick_row.addStretch()
        fp_lay.addLayout(quick_row)

        expr_row = QHBoxLayout()
        expr_row.setSpacing(4)
        self._expr_edit = QLineEdit()
        self._expr_edit.setPlaceholderText("例：1 THRU 100  或  1,5,10-20,50")
        self._expr_edit.setStyleSheet(
            f"background:{BG_INPUT};color:{TEXT_PRIMARY};"
            f"border:1px solid {BORDER_NORMAL};"
            "padding:3px 6px;border-radius:4px;font-size:11px;"
            f"QLineEdit:focus {{ background:{BG_INPUT_EDIT}; border-color:{FOCUS_BLUE}; }}")
        self._expr_edit.returnPressed.connect(self._apply_expr)
        if self._filter_rule.channels:
            self._expr_edit.setText(self._filter_rule.to_expression())
        expr_row.addWidget(self._expr_edit, stretch=1)
        apply_btn = QPushButton("应用")
        apply_btn.setFixedHeight(24)  # 与同行表达式输入框对齐（原 26）
        apply_btn.setStyleSheet(
            f"QPushButton{{background:{SELECT_BLUE};border:1px solid {SELECT_BLUE};"
            f"color:{TEXT_SELECTED_FG};padding:0 10px;border-radius:4px;font-size:11px;}}"
            f"QPushButton:hover{{background:{SELECT_BLUE_HOVER};border-color:{FOCUS_BLUE};}}"
            f"QPushButton:pressed{{background:{_hex_alpha(SELECT_BLUE, 'cc')};}}"
            f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}")
        apply_btn.clicked.connect(self._apply_expr)
        expr_row.addWidget(apply_btn)
        fp_lay.addLayout(expr_row)

        self._filter_panel.setVisible(False)
        outer.addWidget(self._filter_panel)

    @staticmethod
    def _parse_expr(text: str) -> set:
        result: set = set()
        text = text.strip().upper()
        if not text:
            return result
        text = re.sub(r"[,+]", " ", text)
        text = re.sub(r"(\d)\s*-\s*(\d)", r"\1 THRU \2", text)
        tokens = text.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if (tok == "THRU" and i >= 1 and tokens[i-1].isdigit()
                    and i+1 < len(tokens) and tokens[i+1].isdigit()):
                lo, hi = int(tokens[i-1]), int(tokens[i+1])
                result.discard(lo)
                for ch in range(max(1, min(lo, hi)), min(512, max(lo, hi))+1):
                    result.add(ch)
                i += 2
            elif tok.isdigit():
                ch = int(tok)
                if 1 <= ch <= 512:
                    result.add(ch)
                i += 1
            else:
                i += 1
        return result

    def _make_label(self) -> str:
        expr = self._filter_rule.to_expression()
        return f"[{expr}]" if expr else "[全通道]"

    def _toggle_filter(self, checked: bool):
        self._filter_panel.setVisible(checked)
        self._expand_btn.setText("收起通道过滤" if checked else "通道过滤")

    def _select_all(self):
        self._filter_rule.set_all()
        self._expr_edit.setText(self._filter_rule.to_expression())
        self._filter_label.setText(self._make_label())

    def _clear_all(self):
        self._filter_rule.clear()
        self._expr_edit.clear()
        self._filter_label.setText(self._make_label())

    def _select_odd(self):
        self._filter_rule.channels = {ch for ch in range(1, 513) if ch % 2 == 1}
        self._expr_edit.setText(self._filter_rule.to_expression())
        self._filter_label.setText(self._make_label())

    def _select_even(self):
        self._filter_rule.channels = {ch for ch in range(2, 513) if ch % 2 == 0}
        self._expr_edit.setText(self._filter_rule.to_expression())
        self._filter_label.setText(self._make_label())

    def _apply_expr(self):
        text = self._expr_edit.text().strip()
        channels = self._parse_expr(text) if text else set()
        self._filter_rule.channels = channels
        self._filter_label.setText(self._make_label())

    def value(self) -> int:
        return self.spin.value()

    def get_filter_rule(self):
        from ..core.models import FilterRule
        return FilterRule(channels=self._filter_rule.channels.copy())


class FilterEditDialog(QDialog):
    """每个轨道支持多选域（接收/发送），每域独立设置通道过滤范围"""
    def __init__(self, track, parent=None):
        super().__init__(parent)
        self._track = track
        self._universe_rows: list = []
        self.setWindowTitle(f"域 & 过滤配置 — {track.name}")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog{{background:{BG_ROOT};color:{TEXT_PRIMARY};}}"
            f"QLabel{{color:{TEXT_TERTIARY};}}"
            "QGroupBox{border:1px solid rgba(255,255,255,17);border-radius:4px;"
            f"margin-top:8px;color:{TEXT_SECONDARY};font-weight:bold;}}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}"
            "QScrollArea{border:none;background:transparent;}"
        )
        self._setup_ui()

    def _setup_ui(self):
        vlay = QVBoxLayout(self)
        vlay.setSpacing(10)
        vlay.setContentsMargins(12, 12, 12, 12)

        hint = QLabel(
            "每个轨道可同时接收/发送多个 Universe 的数据。\n"
            "点击「通道过滤」展开该域的独立通道过滤设置（空 = 全通道）。"
        )
        hint.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        hint.setWordWrap(True)
        vlay.addWidget(hint)

        uni_group = QGroupBox("接收 / 发送域 (Universe) — 每域可独立设置通道过滤范围")
        uni_outer = QVBoxLayout(uni_group)
        uni_outer.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(360)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._uni_container = QWidget()
        self._uni_layout = QVBoxLayout(self._uni_container)
        self._uni_layout.setContentsMargins(0, 0, 0, 0)
        self._uni_layout.setSpacing(6)
        self._uni_layout.addStretch()
        scroll.setWidget(self._uni_container)
        uni_outer.addWidget(scroll)

        if getattr(self._track, "universe_filters", None):
            existing = sorted(self._track.universe_filters.keys())
        elif getattr(self._track, "universes", None):
            existing = list(self._track.universes)
        else:
            existing = [1]
        for i, u in enumerate(existing):
            existing_rule = None
            if hasattr(self._track, "universe_filters"):
                existing_rule = self._track.universe_filters.get(u)
            if existing_rule is None:
                existing_rule = getattr(self._track, "filter_rule", None)
            self._add_universe_row(u, filter_rule=existing_rule, removable=(i > 0))

        add_uni_btn = QPushButton("＋ 添加域")
        add_uni_btn.setStyleSheet(
            f"QPushButton{{background:{INFO_BLUE};border:1px solid {_hex_alpha(BRIGHT_INFO, '88')};"
            f"color:{TEXT_SELECTED_FG};"
            "padding:4px 12px;border-radius:4px;}"
            f"QPushButton:hover{{background:{SELECT_BLUE};border-color:{FOCUS_BLUE};}}"
            f"QPushButton:pressed{{background:{_hex_alpha(SELECT_BLUE, 'cc')};}}"
            f"QPushButton:focus{{border-color:{FOCUS_BLUE};}}")
        add_uni_btn.clicked.connect(lambda: self._add_universe_row(1, removable=True))
        uni_outer.addWidget(add_uni_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        vlay.addWidget(uni_group)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.setStyleSheet(_css_dialog_btns())
        btns.accepted.connect(self._commit)
        btns.rejected.connect(self.reject)
        vlay.addWidget(btns)

    def _add_universe_row(self, value: int = 1, filter_rule=None, removable: bool = True):
        row = _UniverseFilterRow(value, filter_rule=filter_rule,
                                 removable=removable, parent=self)
        row.del_btn.clicked.connect(lambda: self._remove_universe_row(row))
        self._universe_rows.append(row)
        idx = self._uni_layout.count() - 1
        self._uni_layout.insertWidget(idx, row)

    def _remove_universe_row(self, row):
        if len(self._universe_rows) <= 1:
            return
        self._universe_rows.remove(row)
        row.setParent(None)

    def _commit(self):
        universes = list(dict.fromkeys(r.value() for r in self._universe_rows))
        self._track.universes = universes
        self._track.universe_filters = {}
        for row in self._universe_rows:
            u = row.value()
            self._track.universe_filters[u] = row.get_filter_rule()
        if self._universe_rows:
            self._track.filter_rule = self._universe_rows[0].get_filter_rule()
        self.accept()


# ══════════════════════════════════════════════════════════════════════════════
#  节目管理列表行（C 条 24px 解剖）
# ══════════════════════════════════════════════════════════════════════════════

class _ProgramRow(QWidget):
    """节目管理面板的列表行部件。

    解剖：左侧 7px 激活指示圆点（active_orange，仅当前打开节目）+ 名称
    （11px text_primary）；右侧元数据（10px 右对齐 text_secondary）。
    行内控件一律透明底，让 QListWidget::item 的 hover/select 底色透出；
    选中态文字由 :meth:`set_selected` 统一切白（itemWidget 子控件不随
    QListWidget 配色自动变色，需显式同步）。
    """

    ROW_H = 24

    def __init__(self, name: str, meta: str, active: bool, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.ROW_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 0, 8, 0)
        h.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(7, 7)
        self._dot.setStyleSheet(
            f"background:{ACTIVE_ORANGE if active else 'transparent'};"
            "border-radius:3px; border:none;")
        h.addWidget(self._dot)

        self.name_lbl = QLabel(name)
        self.meta_lbl = QLabel(meta)
        meta_first_css = (
            f"color:{TEXT_SECONDARY}; font-size:10px; background:transparent;"
            "border:none;")
        self.meta_lbl.setStyleSheet(meta_first_css)
        self.meta_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.meta_lbl.setSizePolicy(QSizePolicy.Policy.Maximum,
                                    QSizePolicy.Policy.Preferred)
        h.addWidget(self.name_lbl, 1)
        h.addWidget(self.meta_lbl)
        # 先按非选中态落一次基准配色
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        """选中态：select_blue 底上白字（名称加粗、元数据提亮）。"""
        name_css = (
            ("color:" + TEXT_SELECTED_FG + "; font-size:11px;"
             "background:transparent;border:none;")
            if on else
            ("color:" + TEXT_PRIMARY + "; font-size:11px;"
             "background:transparent;border:none;")
        )
        meta_css = (
            f"color:{TEXT_TERTIARY}; font-size:10px; background:transparent;"
            "border:none;"
            if on else
            f"color:{TEXT_SECONDARY}; font-size:10px; background:transparent;"
            "border:none;"
        )
        self.name_lbl.setStyleSheet(name_css)
        self.meta_lbl.setStyleSheet(meta_css)


# ══════════════════════════════════════════════════════════════════════════════
#  MainWindow
# ══════════════════════════════════════════════════════════════════════════════

def _stop_timeline_writer(win) -> None:
    """停掉时间线生成线程这一 clip 层写入者（"全清空"必须连写入者一起停）。

    ★★ 2026-09-17 深查修正：全清空旧链路只 `release_all_outputs()` + `invalidate_all_caches`
      ，但 `_ctrl._playing` 仍为 True、生成线程未停 → 下一帧 render 重新入队写入
      clip 层（瞬时全黑后又亮回来）。只有 transport 停止才会 stop_gen_thread，
      故释放路径需显式停它。
    """
    try:
        tw = getattr(win, "_timeline_widget", None)
        ctrl = getattr(tw, "ctrl", None) if tw is not None else None
        if ctrl is not None and hasattr(ctrl, "stop"):
            ctrl.stop()
    except Exception:
        pass


def _stop_audio_bridge_output(abw) -> None:
    """停掉音频桥的实时效果输出（释放路径共用；面板不存在时无操作）。"""
    if abw is None:
        return
    try:
        if hasattr(abw, "stop_effect_output"):
            abw.stop_effect_output()
    except Exception:
        pass


def _settle_matrix_hidden_timer(mw) -> None:
    """外部停止输出后，让隐藏中的 Matrix 面板收掉 25fps 主循环。

    ★ 2026-09-17：`hideEvent` 只在隐藏那一刻判定过一次「是否出光中」；若隐藏后
      才由外部释放（全清空/关闭场景/自然结束），没有任何地方重新判定 → 隐藏面板
      会以 PreciseTimer 25fps 空转到会话结束。
    """
    try:
        if hasattr(mw, "settle_hidden_timer"):
            mw.settle_hidden_timer()
    except Exception:
        pass


def _apply_network_config_to_panel(win) -> None:
    """工程加载后把 ``project.network_config`` 灌进网络页（R182 P1-9）。

    实现与异常日志由 ``network_panel.network_config`` 负责。
    """
    try:
        from .network_panel.network_config import apply_project_network_config
        apply_project_network_config(win)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "_load_project: network_config apply failed: %s", e)


# 布局版本：不匹配 = 旧布局一次性作废（删 windowState 回默认布局）。
# v2→v3（R183 A1）：v2 存量 windowState 被"空白窗 bug"时期污染成"全部子模块可见"，
# 修复懒加载后恢复=全部真加载 → 用户报"一开软件子模块页面全部自己打开"。
_LAYOUT_VER = "3"


def _restore_dock_state(win) -> None:
    """恢复 dock 区域/标签页/悬浮位置（**必须**在 `_build_docks()` 之后调用）。

    ★★ 2026-09-17 深查修正（排版永不恢复）：原实现在 `_restore_geometry()` 里调
      `restoreState()`，而那时 `_build_docks()` 还没跑 —— Qt 的 `restoreState`
      按 `objectName` 查找**已存在**的对象，一个 dock 都没有时静默返回 True 却
      什么都没做（已用 Qt6.11 探针实证）。写侧一直正常（closeEvent 写 windowState），
      读侧是空操作 → 用户每次重启都丢失面板区域/吸附/悬浮尺寸。
    R54-B 版本门保持原语义（现为 v3，见 _LAYOUT_VER）：旧版本布局与当前默认
      语义冲突 → 丢弃旧 windowState 并写入新版本标记。
    ★★ R182 P0（启动弹三个空白浮窗）：恢复出的可见 dock 若尚未懒加载面板，
      其内容是 `_make_dock` 的占位 QWidget（纯色空壳）→ 呈现为空白浮窗。
      restoreState 之后对可见且无面板的 dock 补懒加载；仍无面板的（如已废弃的
      跑动顺序 dock）直接 hide，绝不把空白窗留在屏幕上。
    """
    s = QSettings("Qascade", "MainWindow")
    ver = str(s.value("ui_layout_ver", "") or "")
    if ver != _LAYOUT_VER:
        # ★ R183 A1：v2 时期"空白窗 bug"把"全部子模块都可见"的污染布局写进了
        #   windowState。旧版本布局一次性作废：windowState 一并删除，防止下次
        #   启动又把污染布局恢复回来；此后只恢复用户在本版本下自己摆过的布局。
        s.remove("windowState")
        s.setValue("ui_layout_ver", _LAYOUT_VER)
        return
    state = s.value("windowState")
    if not state:
        return
    win._layout_restoring = True
    try:
        win.restoreState(state)
    finally:
        win._layout_restoring = False
    from .startup_workspace import hide_startup_floating_editors
    hide_startup_floating_editors(win)
    _ensure_visible_docks_loaded(win)


def _dock_needs_panel(dock) -> bool:
    """dock 是否仍是"空壳"：无 widget，或仅有 `_make_dock` 的裸占位 QWidget。

    占位件特征：精确类型 QWidget（面板根无一例外是子类）、无布局、无子控件。
    面板真正装入后（`_install_dock_widget`）三者都不满足。
    """
    from PyQt6.QtWidgets import QWidget as _W
    w = dock.widget()
    if w is None:
        return True
    return type(w) is _W and w.layout() is None and not w.children()


def _ensure_visible_docks_loaded(win) -> None:
    """恢复布局后：可见 dock 没有真面板 → 懒加载；仍没有 → 收起来。

    只处理 `_R54_ALL_DOCK_ATTRS` 覆盖的 dock（桌面其余控件不在治理面内）。
    加载失败/无对应分支（如已废弃的 `_route_order_dock`）时 hide 兜底 ——
    空白浮窗比"面板没恢复"更伤害用户（R182 用户实报）。
    """
    for attr in getattr(win, "_R54_ALL_DOCK_ATTRS", ()):
        dock = getattr(win, attr, None)
        if dock is None or not dock.isVisible():
            continue
        if _dock_needs_panel(dock):
            try:
                win._lazy_load_panels(dock)
            except Exception:
                log.debug("restore lazy load failed: %s", attr, exc_info=True)
        if _dock_needs_panel(dock):
            dock.hide()


def _resume_programmer_keepalive(pw) -> None:
    """Programmer dock 重新显示时防御性恢复 keepalive 定时器。

    ★★ 隐藏加载语义（2026-09-17 用户定案）：**面板隐藏绝不等于释放**。
      旧实现在 Programmer 隐藏时调 `clear_live_output()` —— 它会停表 + 清
      `_pending_live`/`_keepalive_data` 缓存 + **清 NM 各层并 flush**，等于用户
      没点 release 就被强制释放：吸附/切 tab（tabify 后非激活页会 hide）/点关闭
      按钮，输出当场消失且重开不再接续（用户实报"一关页面就完全不认人了"）。
      现在隐藏时不动任何层与定时器（keepalive 有层版本守卫，无新写入直接 return，
      空转成本≈0）；显示时只做这一件防御性恢复，保证输出接续。
      释放仍只由主动动作触发：release 按钮 / 全清空 / 关闭场景 / 录制结束。
    """
    try:
        _ka = getattr(pw, "_keepalive_timer", None)
        if _ka is not None and not _ka.isActive():
            _ka.start()
    except Exception:
        pass


def _studio_flush_and_persist(mw) -> None:
    """R172b：保存前先冲掉 Studio 300ms 防抖窗口内的挂起应用（否则最后一笔
    Studio 编辑只进了 sheet 图、clip 字段未落 = 保存丢应用），再按 R97 守卫回写。"""
    panel = getattr(mw, "_clip_studio_widget", None)
    if panel is not None:
        panel.force_apply_now()
    from .clip_studio.studio_panel import persist_clip_studio_state
    persist_clip_studio_state(panel, mw._pm.project)


def _studio_flush_pending(mw) -> None:
    """R173：撤销/重做前先落 Studio 300ms 防抖窗口内的挂起编辑（否则回滚后
    load_state 丢弃它 = 编辑的图已撤销、而 clip 字段从未应用，二者对不上）。"""
    panel = getattr(mw, "_clip_studio_widget", None)
    if panel is not None:
        panel.flush_pending()


def _collect_panel_state_for(win, domain) -> None:
    """按 `domain` 决定是否收集面板状态（收集本身有副作用，不该白做）。

    · `domain is None`（全量）→ 照旧全量收集；
    · 域内**不含任何面板状态字段**（如纯时间线编辑）→ 跳过收集：既省一次全量
      遍历，也避免"为拍快照而回写面板状态"这一副作用。
    """
    if domain is None:
        win._collect_project_state_for_save()
        return
    try:
        from ..core.project_manager import PANEL_STATE_KEYS
    except Exception:
        win._collect_project_state_for_save()
        return
    if set(domain) & set(PANEL_STATE_KEYS):
        win._collect_project_state_for_save()


def _bound_topview_state(win, project):
    """面板**已绑定到 `project`** 时返回其最新状态；未绑定 → None。

    ★★ 2026-09-19 用户实报（第三次复发）："项目保存之后再加载 topview 等功能
      保存的信息丢失"。根因 = **先写回、后灌入**的顺序颠倒：

        `_load_project` 第 4 步 `_sync_motor_defaults()` → `_sync_fixture_defaults()`
        → 无条件 `_sync_xy_reference()`；后者读 `tv.save_state()` 并
        `project.topview_state = state` **回写**。而此刻 TopView 面板还停在
        **上一个工程**（空 Sheet1）—— 于是刚读进来的存档当场被空状态覆盖，
        等真正执行 `load_state(new_proj.topview_state)` 时读到的已是空 dict
        → 回退成默认 Sheet1：页/选中/XY 同步基准/Z 角**全丢**。

      实测（真实 MainWindow + 真实存档，逐步打印）：
        after pm.load: proj=2页 → after motor_defaults: proj=1页（被覆盖）
        → after tv.load_state: 1页（灌入时已空）。

      为什么反复复发：历次修法都是**调顺序**，而"面板→工程"的回写点在任何
      重排下都可能先跑到面板被灌入之前。此处改成**结构性判据**：面板没绑定到
      当前工程就绝不回写 —— 顺序怎么变都不会再丢。
    """
    tv = getattr(win, "_topview_widget", None)
    if tv is None or getattr(tv, "_project", None) is not project:
        return None
    # 面板状态必须正是从**本工程**灌入的（换绑中途不允许回写，见 TopViewPanel
    # `_persist_state` 的同一判据）
    if getattr(tv, "_hydrated_for", None) is not project:
        return None
    if not (hasattr(tv, "save_state") and hasattr(project, "topview_state")):
        return None
    try:
        return tv.save_state()
    except Exception:
        return None


# ═══ R172 W1d2：Clip 附加处理 Studio 胶水（模块级 —— 不给 MainWindow 加方法）═══

def _studio_list_clips(tw) -> list:
    """时间线全部 clip（跨轨扁平；面板绑定勾选列表 / 绑定计数用）。"""
    out: list = []
    for tr in list(getattr(getattr(tw, "_session", None), "tracks", None) or []):
        out.extend(list(getattr(tr, "clips", None) or []))
    return out


def _studio_bind_clip(mw, clip_id, sheet_id) -> None:
    """绑定/解绑 clip ↔ Studio sheet（B11：勾选回调；绑定关系非处理参数）。"""
    tw = getattr(mw, "_timeline_widget", None)
    if tw is None:
        return
    try:
        _track, clip = tw.ctrl._find_track_and_clip(str(clip_id))
    except Exception:
        return
    if clip is None:
        return
    clip.process_sheet_id = str(sheet_id or "")
    try:
        tw._mark_project_dirty()
    except Exception:
        pass


def _studio_state_edited(mw, panel) -> None:
    """面板编辑 → persist（R97 `is` 守卫内置）+ 工程置脏。"""
    proj = getattr(getattr(mw, "_pm", None), "project", None)
    if proj is None:
        return
    try:
        from .clip_studio.studio_panel import persist_clip_studio_state
        persist_clip_studio_state(panel, proj)
    except Exception:
        pass
    # A graph edit changes the joint timeline+studio undo domain.  Drop the
    # previous idle copy immediately, then rebuild it after the edit burst so
    # the next independent node gesture does not serialize all Clips inline.
    try:
        from .timeline.timeline_perf import (invalidate_scoped_snapshot_cache,
                                              schedule_scoped_snapshot_prime)
        from ..core.project_manager import DOMAIN_CLIP_STUDIO, DOMAIN_TIMELINE
        invalidate_scoped_snapshot_cache(mw._pm)
        mw._pm._mark_dirty()
        schedule_scoped_snapshot_prime(
            mw._pm, DOMAIN_TIMELINE | DOMAIN_CLIP_STUDIO)
    except Exception:
        mw._pm._mark_dirty()
    note_project_edit(mw)
    update_title = getattr(mw, '_update_title', None)
    if callable(update_title):
        update_title()


def _lazy_install_clip_studio(mw):
    """构建 ClipStudioPanel 并注入五个闭包（R172 W1d2 懒装载；失败返回 None）。

    依赖注入（MainWindow 是路由层 —— 面板之间零耦合，照调度台先例）：
    apply_fields_fn → studio_apply 真实处理器路由；snapshot_fn → 时间线 scoped
    快照；list_clips_fn → 跨轨 clip 扁平表；bind_clip_fn → process_sheet_id 绑定；
    project_getter → R97 `is` 守卫的回写锚。
    """
    from .clip_studio.studio_apply import apply_compiled_fields as _apply_compiled
    from .clip_studio.studio_panel import ClipStudioPanel as _StudioPanel
    from .clip_studio.studio_session import no_inner_snapshots, snapshot_studio_edit
    from .clip_studio.studio_optimizer import optimize_bound_clips
    tw = getattr(mw, "_timeline_widget", None)

    def _apply(clip_ids, fields, prev_fields, _tw=tw):
        # R173：回传未生效键（原丢 return → 平滑失败永不提示/重试）；槽内快照静默
        # —— 编辑前已由 _snapshot 压过一条联合域快照（一次编辑 = 一条撤销）
        with no_inner_snapshots(_tw):
            failed = _apply_compiled(_tw, clip_ids, fields, prev_fields)
            if fields and clip_ids and _tw is not None:
                _tw._mark_project_dirty()
            return failed

    def _snapshot(_tw=tw):
        # 时间线域（clip 字段）∪ Studio 图域：原只拍时间线域 → 撤销回滚 clip 不回滚图
        snapshot_studio_edit(_tw._get_pm() if _tw is not None and hasattr(_tw, "_get_pm") else None)

    w = _StudioPanel(
        apply_fields_fn=_apply,
        snapshot_fn=_snapshot,
        list_clips_fn=lambda: _studio_list_clips(tw),
        bind_clip_fn=lambda clip_id, sheet_id: _studio_bind_clip(mw, clip_id, sheet_id),
        optimize_fn=lambda ids: optimize_bound_clips(tw, ids, w._snapshot_fn),
        project_getter=lambda: getattr(getattr(mw, "_pm", None), "project", None),
        parent=mw,
    )
    w.state_edited.connect(lambda _w=w: _studio_state_edited(mw, _w))
    proj = getattr(getattr(mw, "_pm", None), "project", None)
    if proj is not None:
        w.load_state(getattr(proj, "clip_studio_state", {}) or {}, proj)
        # Warm the joint domain used by the first Studio node/parameter edit.
        # TimelineWidget may already be warming DOMAIN_TIMELINE; the cache
        # scheduler keys pending work by domain so both copies can be prepared.
        from ..core.project_manager import DOMAIN_CLIP_STUDIO, DOMAIN_TIMELINE
        from .timeline.timeline_perf import schedule_scoped_snapshot_prime
        schedule_scoped_snapshot_prime(
            getattr(mw, "_pm", None), DOMAIN_TIMELINE | DOMAIN_CLIP_STUDIO)
    return w


def _studio_force_float_default(mw, dock) -> None:
    """R182：Clip Studio 打开时若仍是停靠态且无用户拖动记录 → 悬浮并居中。

    背景：`_maybe_float_default`（_toggle_dock 内）已覆盖常规路径，但旧 QSettings
    布局把该 dock 存成底部停靠后，restoreState 之后的 `dockLocationChanged` 可能在
    `_layout_restoring` 复位**之后**派发，把 `_dock_user_docked` 污染成"用户意图"
    → 自动悬浮被永久跳过。这里做一次显式兜底：未悬浮 + 无拖动记录 → setFloating +
    1280×800 + 居中（居中判据与 `_maybe_float_default` 的 size[0] > 600 同源）。
    """
    try:
        if dock.isFloating() or "_clip_studio_dock" in mw._dock_user_docked:
            return
        size = mw._FLOAT_SIZE_OVERRIDE.get("_clip_studio_dock", (1280, 800))
        mw._float_applying = True
        try:
            dock.setFloating(True)
            dock.resize(*size)
            g = mw.frameGeometry()
            dock.move(g.center().x() - size[0] // 2,
                      max(g.top() + 40, g.center().y() - size[1] // 2))
        finally:
            mw._float_applying = False
    except Exception:
        pass


def _open_clip_studio(mw, clip_id) -> None:
    """「Clip 附加处理…」入口（右击菜单信号终点 + 全局入口共用）：
    懒安装 dock → 找 clip → `open_for_clip`（无绑定=新建 sheet+反向卡+绑定）→ 显示。"""
    dock = getattr(mw, "_clip_studio_dock", None)
    if dock is None:
        return
    mw._toggle_dock(dock, True)
    _studio_force_float_default(mw, dock)
    panel = getattr(mw, "_clip_studio_widget", None)
    tw = getattr(mw, "_timeline_widget", None)
    if panel is None or tw is None:
        return
    try:
        _track, clip = tw.ctrl._find_track_and_clip(str(clip_id))
    except Exception:
        return
    if clip is None:
        return
    try:
        panel.open_for_clip(clip)
    except Exception:
        log.debug("open_for_clip failed", exc_info=True)


def _open_audio_mod_editor(mw, at_sec: float = 0.0) -> None:
    """调制轨左双击入口：懒建 **Draw 大页**（R182 用户定形态）→ 注入状态+scope
    识别通道 → 显示。

    形态决策（读现状后二选一）：双击原开「映射编辑页」（AudioModEditorPage，
    linker 式逐通道波形映射）；R182 用户已定 Draw 大页为双击进入的主编辑面
    （卡片画布+连线），故双击改开 Draw 页；映射页保留为 Draw 顶栏「映射页」
    按钮入口（`_open_audio_mod_mapping`），两条链路同源提交
    （`_on_audio_mod_edited`）。
    M1：双击秒位 `at_sec` 沿链保留进 Draw 页（不再丢弃）。
    """
    editor = getattr(mw, "_audio_mod_draw_editor", None)
    if editor is None:
        try:
            from .audio_mod_draw.draw_panel import AudioModDrawPanel
            editor = AudioModDrawPanel()
            editor.applied = _make_edit_listener(mw)   # 清查②③#2：带域快照
            editor._mapping_btn.clicked.connect(
                lambda _c=False, _w=mw, _e=editor:
                _open_audio_mod_mapping(_w, at_sec=float(_e._open_at_sec)))
            dock = mw._make_dock(
                "音频调制 Draw", Qt.DockWidgetArea.RightDockWidgetArea,
                icon_key="effects")
            mw._install_dock_widget(dock, editor)
            editor._close_btn.clicked.connect(dock.hide)
            mw._audio_mod_draw_dock = dock
            mw._audio_mod_draw_editor = editor
        except Exception:
            log.debug("AudioModDrawPanel build failed", exc_info=True)
            return
    dock = getattr(mw, "_audio_mod_draw_dock", None)
    if dock is None:
        return
    project = mw._project()
    state = getattr(project, "audio_modulation_state", {}) or {} if project else {}
    channels = _audio_mod_scope_channels(mw, state)
    try:
        editor.set_context(state, channels, at_sec=float(at_sec or 0.0),
                           project=project)   # 轮⑦：真灯数相位点按工程实配接算
    except Exception:
        log.debug("draw editor set_context failed", exc_info=True)
    mw._toggle_dock(dock, True)


def _open_audio_mod_mapping(mw, at_sec: float = 0.0) -> None:
    """映射编辑页入口（R182：Draw 顶栏「映射页」按钮；原双击行为搬到这里）。

    M1：`at_sec` 沿链保留进映射页（与 Draw 页同口径）。
    """
    editor = getattr(mw, "_audio_mod_editor", None)
    if editor is None:
        try:
            from .timeline.audio_mod_editor_page import AudioModEditorPage
            editor = AudioModEditorPage()
            editor.edited = _make_edit_listener(mw)    # 清查②③#2：带域快照
            dock = mw._make_dock(
                "音频调制编辑", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="effects")
            mw._install_dock_widget(dock, editor)
            editor._close_btn.clicked.connect(dock.hide)
            mw._audio_mod_dock = dock
            mw._audio_mod_editor = editor
        except Exception:
            log.debug("AudioModEditorPage build failed", exc_info=True)
            return
    dock = getattr(mw, "_audio_mod_dock", None)
    if dock is None:
        return
    project = mw._project()
    state = getattr(project, "audio_modulation_state", {}) or {} if project else {}
    channels = _audio_mod_scope_channels(mw, state)
    try:
        editor.set_context(state, channels, at_sec=float(at_sec or 0.0))
    except Exception:
        log.debug("editor set_context failed", exc_info=True)
    mw._toggle_dock(dock, True)


def _audio_mod_scope_channels(mw, state: dict) -> list:
    """scope 文件夹内可出声普通轨的通道类型并集（音频调制识别对象）。"""
    try:
        from src.engine.audio_mod_scope import collect_scope_channels_from_project
        return collect_scope_channels_from_project(
            mw._project(), str(state.get("scope_folder_id", "") or ""))
    except Exception:
        log.debug("scope channels collect failed", exc_info=True)
        return []


def _tracks_to_dicts(tracks) -> list:
    """Track 对象列表 → collect_scope_channel_types 口径的 dict 列表。"""
    return [{
        "id": str(getattr(tr, "id", "") or ""),
        "parent_id": str(getattr(tr, "parent_id", "") or ""),
        "is_folder": bool(getattr(tr, "is_folder", False)),
        "muted": bool(getattr(tr, "muted", False)),
        "solo": bool(getattr(tr, "solo", False)),
        "clips": [{"ch_types": list(getattr(c, "ch_types", []) or [])}
                  for c in (getattr(tr, "clips", []) or [])],
    } for tr in (tracks or [])]


def _load_dispatch_library(win, proj) -> None:
    """打开工程时载入调度台场景库 + 关联注册表，并归还运行期记账。

    ★★ 2026-09-18 回访修正（打开工程后的幽灵出光 + 残留增益）：库整体换新 →
      运行期记账必须一起归还（激活顺序 / 层增益）。否则实测：点 A → 打开另一个
      工程 → 点该工程的 B → 关闭 B 时把**上一个工程的 A** 重新放出来
      （`_restore_previous_scene` 读的就是那份顺序）。
    """
    from ..processing.dispatch import get_effect_cache_store, get_release_registry
    _ds = getattr(proj, "dispatch_state", {}) or {}
    get_effect_cache_store().from_dict(_ds)
    get_release_registry().from_dict(_ds.get("releases", {}) or {})
    w = getattr(win, "_dispatch_widget", None)
    if w is None:
        return
    if hasattr(w, "refresh_saved"):
        w.refresh_saved()
    from .dispatch.scene_release import reset_scene_runtime_state
    reset_scene_runtime_state(w)


def _reset_dispatch_singletons(win) -> None:
    """新建工程时清掉调度台的**进程级单例**（场景库 / 关联注册表）。

    ★★ 深查修正（老工程数据泄漏进新工程）：两者是进程级单例，新建工程时不清 →
      下次保存会把老工程的场景写进新工程（实测确认泄漏）。
    ★★ 2026-09-18 回访修正（工程切换后的幽灵出光 + 残留增益）：库换新时
      **运行期记账也必须一起归还**（激活顺序 + 层增益）。实测：点 A（高亮 + 顺序 [A]）
      → 新建工程（库已空、高亮已空、顺序仍 [A]）→ 点新工程的 B → 关闭 B：
      `_restore_previous_scene` 把**上一个工程的 A** 重新放出来。
      收口在 `scene_release.reset_scene_runtime_state`（与清台同一套动作）。
    """
    try:
        from ..processing.dispatch import get_effect_cache_store, get_release_registry
        get_effect_cache_store().clear()
        get_release_registry().clear()
        if getattr(win, "_dispatch_widget", None) is not None:
            win._dispatch_widget.refresh_saved()
            from .dispatch.scene_release import reset_scene_runtime_state
            reset_scene_runtime_state(win._dispatch_widget)
    except Exception as e:
        _log.getLogger(__name__).warning("_new_project: dispatch reset failed: %s", e)


def _bridge_pm_dirty(win, pm) -> None:
    """把 `ProjectManager` 的脏状态桥接到主窗标记（`closeEvent` 只读后者）。

    ★★ 深查修正（"改完关窗口不提示、改动丢失"——本轮最严重）：
      本工程有**两个互不同步的脏标记** —— `ProjectManager._dirty`（`_mark_dirty()` 设置）
      与 `MainWindow._dirty`（`closeEvent` 只读它）。而 `pm.dirty_changed` **从未被连接**、
      `pm.is_dirty` 也无人读 → 所有只调 `pm._mark_dirty()` 的路径（调度台存/删/改名/
      改模式/绑定/淡化/换位、设备组保存、灯光控制模式…）都不触发未保存提示。
      保存时 PM 会 emit(False)，故必须尊重布尔值，不能一律置 True。
    """
    if not hasattr(pm, "dirty_changed"):
        return
    try:
        pm.dirty_changed.disconnect(win._on_pm_dirty_changed)
    except Exception:
        pass
    try:
        pm.dirty_changed.connect(win._on_pm_dirty_changed)
    except Exception:
        pass


def _confirm_discard_for_project_change(win) -> bool:
    """切换工程前确认未保存改动（返回 True = 可以继续切换）。

    ★ 深查修正：`_new_project` 一直有此保护，`_load_project`（File→Open）没有
      → 有未保存改动时打开别的工程会**静默丢弃**当前工作。
      用户选"保存"但保存失败/取消（仍为脏）→ 中止切换，绝不丢数据。
    """
    try:
        if not win._dirty:
            return True
        reply = QMessageBox.question(
            win, "打开工程",
            "当前项目有未保存改动，是否保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            # ★ R193/SV-01：保存已改异步入队（D01），本栈内 `_dirty` 必然仍为
            #   True——`return not win._dirty` 会把"选了保存"的打开永远中止。
            #   有路径：入队后台保存（身份/打戳校验保护新文档）后放行；
            #   无路径：由 _save_as 返回值判断（取消另存为才中止）。
            if win._project_path:
                win._save_project()
                return True
            return bool(win._save_as())
        return True                          # Discard
    except Exception:
        return True


def _arm_main_path_trajectory(win, ctrl) -> None:
    """★ R193/DM-01：主路录制补接控件轨迹层（上帝类禁新增方法 → 模块级）。

    RecordEngine 的 RECORDING 闸门只由兜底路径 rec.start() 打开；主路
    （TimelineController.start_recording）从不走它 → 不接线则推杆/摇杆/
    效果参数的轨迹事件全部被 record_traj_event 静默丢弃（R193 审计 DM-01）。
    只装轨迹不装帧；窗口起点与主路 clip.start_time 同基。
    """
    rec = getattr(win, "_record_engine", None)
    if rec is None and hasattr(win, "_init_record_engine"):
        try:
            win._init_record_engine()
            rec = getattr(win, "_record_engine", None)
        except Exception:
            rec = None
    if rec is None or not hasattr(rec, "begin_traj_only"):
        return
    try:
        rec.begin_traj_only(start_position=float(
            getattr(ctrl, "_rec_start_play_pos", 0.0) or 0.0))
    except Exception:
        import logging
        logging.getLogger(__name__).debug("主路轨迹装载失败", exc_info=True)


def _disarm_main_path_trajectory(win, recorded_clip) -> None:
    """★ R193/DM-01：主路停止——轨迹流按本次录制窗口切片进录出的 clip。"""
    rec = getattr(win, "_record_engine", None)
    if rec is None or not hasattr(rec, "end_traj_only"):
        return
    try:
        rec.end_traj_only(recorded_clip)
    except Exception:
        import logging
        logging.getLogger(__name__).debug("主路轨迹落盘失败", exc_info=True)


def _rotate_autosave_files(base_dir, stem) -> None:
    """★ 轮换清理(审计 save-load P3) + R193/IO-02：同 stem 旧自动保存只留
    最近 20 份，并同步删除随包 `<stem>_assets` 音频目录——否则每次 autosave
    一个 `untitled_autosave_<ts>_assets/` 永久残留，磁盘无限增长。"""
    try:
        _olds = sorted(base_dir.glob(f"{stem}_autosave_*.dmxt"),
                       key=lambda p: p.stat().st_mtime)
        for _old in _olds[:-20]:
            try:
                _old.unlink()
            except OSError:
                pass
            try:
                _assets = _old.with_name(_old.stem + "_assets")
                if _assets.is_dir():
                    shutil.rmtree(_assets, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def _ensure_timeline_baker(win):
    """R178：惰性挂载时间线**后台自动烘焙器**（MainWindow 上帝类禁新增方法 → 模块级）。

    - 引擎/工程管理器未就绪 → 返回 None（调用方静默保持现状路径）；
    - 进度接主窗状态栏内嵌条（R182；timeline_widget 同名接口保留为兼容壳）；
    - 烘焙本体在 src/engine/timeline_autobake.py（编辑后 500ms 防抖失效重烘，
      可中断；产物与手动锁定路径逐字节一致 —— 见 ab_bake.py A/B 对拍）。
    """
    baker = getattr(win, "_timeline_baker", None)
    if baker is not None:
        return baker
    engine = getattr(win, "_engine", None)
    pm = getattr(win, "_pm", None)
    if engine is None or pm is None:
        return None
    try:
        from ..engine.timeline_autobake import attach_timeline_auto_baker
        import weakref
        provider = lambda: getattr(pm, "project", None)  # noqa: E731
        host = weakref.ref(win)
        playing = lambda: bake_deferred(host(), _timeline_ctrl(host()))  # noqa: E731
        baker = attach_timeline_auto_baker(engine, provider, parent=win,
                                          playing_provider=playing)
    except Exception:
        return None
    win._timeline_baker = baker
    _baker_bind_timeline_bar(win, baker)
    return baker


def _baker_bind_timeline_bar(win, baker) -> None:
    """baker.progress/invalidated → 状态栏内嵌进度条（迟到绑定安全，可重复调用）。

    ★ R182：旧实现把同一对信号**重复 connect 到 TimelineWidget**（每次门禁变化
      调一次本函数 → 连接数累加、回调重入）；且 R181 还并行挂着左下角浮动条。
      现在唯一消费者是状态栏条（`attach_autobake_progress_bar` 内部按
      `_baker_ref` 幂等 + 换 baker 先断旧链），时间线侧只注入转发引用。
    """
    bar = None
    try:   # R182：状态栏「网络」右侧内嵌条（自绘/淡出全在 ui/autobake_progress）
        from .autobake_progress import attach_autobake_progress_bar
        bar = attach_autobake_progress_bar(win, baker)
    except Exception:
        bar = None
    tw = getattr(win, "_timeline_widget", None)
    if bar is None or tw is None:
        return
    try:   # 兼容壳 TimelineWidget.set_bake_progress/clear_bake_progress 的转发目标
        tw._bake_status_bar = bar
        if hasattr(tw, "set_bake_baker"):
            tw.set_bake_baker(baker)
    except Exception:
        pass


# ── R182：状态栏 FPS / DMX 接线（模块级 helper，避免上帝类新增方法） ──────────

def _timeline_ctrl(win):
    """主窗时间线控制器（无时间线宿主 → None）。"""
    tw = getattr(win, "_timeline_widget", None)
    return getattr(tw, "ctrl", None) if tw is not None else None


def _bump_fps_ticks(win) -> None:
    """播放 tick 计数（`playback_tick` ~43Hz 信号槽）。"""
    counter = getattr(win, "_r182_fps_counter", None)
    if counter is not None:
        counter["ticks"] = int(counter.get("ticks", 0)) + 1


def _hook_fps_ticks(win, ctrl) -> None:
    """给控制器挂一次 tick 计数（判据写在控制器上 → 换控制器才重连，幂等）。"""
    if ctrl is None or getattr(ctrl, "_r182_fps_win", None) is win:
        return
    try:
        ctrl.playback_tick.connect(lambda _pos, w=win: _bump_fps_ticks(w))
        ctrl._r182_fps_win = win
    except Exception:
        log.debug("fps tick hook failed", exc_info=True)


def _sample_fps(win) -> Optional[float]:
    """两次 200ms 刷新间实测 FPS = tick 数 / 间隔；无 tick（暂停）→ None。"""
    counter = getattr(win, "_r182_fps_counter", None)
    ctrl = _timeline_ctrl(win)
    if counter is None or counter.get("ctrl") is not ctrl:
        counter = {"ticks": 0, "t0": time.monotonic(), "ctrl": ctrl}
        win._r182_fps_counter = counter
        _hook_fps_ticks(win, ctrl)
    now = time.monotonic()
    dt = now - float(counter.get("t0", now))
    ticks = int(counter.get("ticks", 0))
    counter["ticks"] = 0
    counter["t0"] = now
    if dt <= 0.0 or ticks <= 0:
        return None
    return ticks / dt


def _refresh_fps_label(win) -> None:
    """`_refresh_status` 尾部调用：算 FPS 并写状态栏（无 `set_fps` 宿主静默）。"""
    status = getattr(win, "_status", None)
    if status is None or not hasattr(status, "set_fps"):
        return
    try:
        status.set_fps(_sample_fps(win))
    except Exception:
        log.debug("fps label refresh failed", exc_info=True)


def _dmx_stats(nm):
    """NM 发送统计 dict（缺 API/异常 → None）。"""
    getter = getattr(nm, "get_send_skip_stats", None)
    if not callable(getter):
        return None
    try:
        stats = getter()
    except Exception:
        return None
    return stats if isinstance(stats, dict) else None


def _dmx_sent_count(stats) -> Optional[int]:
    """统计里的累计实发帧数（无统计 → None）。"""
    if not isinstance(stats, dict):
        return None
    try:
        return int(stats.get("sent", 0) or 0)
    except (TypeError, ValueError):
        return None


def _dmx_domain_count(nm, stats) -> int:
    """活跃域数：统计键（另一 agent 扩展）优先，退化为最近发送快照的域数。"""
    for key in ("universes", "active_universes", "domains"):
        val = stats.get(key) if isinstance(stats, dict) else None
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    try:
        snapshot = getattr(nm, "_universe_snapshot", None)
        return len(snapshot) if snapshot else 0
    except Exception:
        return 0


def _dmx_sent_hz(win, sent: Optional[int], now: float) -> Optional[float]:
    """两次采样 sent 差值 / 间隔 = 发送 Hz（>=0.8s 平滑一次；无 sent → None）。"""
    sample = getattr(win, "_r182_dmx_sample", None)
    if sent is None:
        win._r182_dmx_sample = None
        return None
    if not isinstance(sample, dict):
        win._r182_dmx_sample = {"t": now, "sent": sent, "hz": None}
        return None
    dt = now - float(sample.get("t", now))
    delta = sent - int(sample.get("sent", sent))
    sample["t"] = now
    sample["sent"] = sent
    if dt < 0.8 or delta < 0:
        return sample.get("hz")
    sample["hz"] = delta / dt
    return sample["hz"]


def _dmx_running_text(win, nm, now: float) -> str:
    """运行中文案：`DMX: {Hz}Hz·{域数}域`；无统计口退化为 `DMX: 运行中`。"""
    stats = _dmx_stats(nm)
    hz = _dmx_sent_hz(win, _dmx_sent_count(stats), now)
    if hz is None:
        return "DMX: 运行中"
    domains = _dmx_domain_count(nm, stats)
    if domains > 0:
        return f"DMX: {hz:.0f}Hz·{domains}域"
    return f"DMX: {hz:.0f}Hz"


def _refresh_dmx_label(win) -> None:
    """`_refresh_status` 尾部调用：NM 运行态/发送率 → 状态栏 DMX 标签。"""
    status = getattr(win, "_status", None)
    nm = getattr(win, "_nm", None)
    if status is None or nm is None or not hasattr(status, "set_dmx"):
        return
    try:
        running = bool(getattr(nm, "is_running", False))
    except Exception:
        running = False
    if not running:
        win._r182_dmx_sample = None
        status.set_dmx("DMX: --")
        return
    try:
        status.set_dmx(_dmx_running_text(win, nm, time.monotonic()))
    except Exception:
        log.debug("dmx label refresh failed", exc_info=True)


def _on_timeline_program_gate(win, active: bool) -> None:
    """R178：节目门禁变化（载入/切换/关闭都经此）→ 懒挂载自动烘焙器并触发重烘。"""
    try:
        baker = _ensure_timeline_baker(win)
    except Exception:
        return
    if baker is None:
        return
    try:
        update_program_bake_gate(win, baker, active)
        if active:
            _baker_bind_timeline_bar(win, baker)
    except Exception:
        pass


def _wait_timeline_bake_for_export(win) -> bool:
    """帧数据导出前等待后台烘焙完成（R178 用户规格：导出改为等待烘焙）。

    烘焙中弹模态进度框（进度 = baked_until/total），完成/取消即返回。
    返回 False = 用户取消导出。
    """
    baker = getattr(win, "_timeline_baker", None)
    if baker is None or not baker.is_baking():
        return True
    if getattr(win, "_bake_wait_active", False):
        return True  # ★ R193/P2-23：重入守卫（processEvents 可再派发导出/菜单）
    win._bake_wait_active = True
    dlg = QProgressDialog("等待时间线后台烘焙完成...", "取消导出", 0, 100, win)
    dlg.setWindowTitle("导出帧数据")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setMinimumWidth(460)
    try:
        dlg.setStyleSheet(
            f"QProgressDialog {{ background:{BG_EDITOR}; color:{TEXT_PRIMARY}; }}"
            f"QLabel {{ color:{TEXT_PRIMARY}; min-width:420px; }}"
            f"QProgressBar {{ background:{BG_ROOT}; border:1px solid {BORDER_NORMAL};"
            f" border-radius:4px; text-align:center; color:{TEXT_PRIMARY}; }}"
            f"QProgressBar::chunk {{ background:{SELECT_BLUE}; border-radius:4px; }}"
        )
    except Exception:
        pass
    dlg.show()
    try:
        while baker.is_baking():
            total = max(1, int(baker.total_frames()))
            frac = max(0.0, min(1.0, float(baker.baked_until_frame()) / float(total)))
            dlg.setValue(int(round(frac * 100.0)))
            QApplication.processEvents()
            if dlg.wasCanceled():
                return False
            time.sleep(0.03)
    finally:
        win._bake_wait_active = False  # ★ R193/P2-23
        try:
            dlg.close()
        except Exception:
            pass
    return True


class MainWindow(QMainWindow):
    @staticmethod
    def _safe_console_log(message: object):
        text = f"[Log] {message}"
        try:
            print(text)
        except UnicodeEncodeError:
            encoding = getattr(getattr(sys, "stdout", None), "encoding", None) or "ascii"
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe)

    def __init__(self, engine=None, network_manager=None, parent=None):
        super().__init__(parent)

        self._engine          = engine
        self._nm              = network_manager
        self._pm              = None
        self._record_engine   = None

        self._project_path: str       = ""
        self._dirty                   = False
        self._selected_track_id: Optional[str] = None
        self._pending_patch_import = None  # 导入配接表两阶段流程的暂存状态

        self._monitor_widget:        Optional[QWidget] = None
        self._patch_widget:          Optional[QWidget] = None
        self._topview_widget:        Optional[QWidget] = None
        self._route_order_widget:    Optional[QWidget] = None
        self._route_animator_window: Optional[QWidget] = None
        self._program_widget:        Optional[QWidget] = None
        self._program_list:          Optional[QListWidget] = None
        self._program_active_label:  Optional[QLabel] = None
        self._programmer_widget:     Optional[QWidget] = None
        self._matrix_pattern_widget: Optional[QWidget] = None
        self._fixture_group_widget:  Optional[QWidget] = None
        self._syncing_selection_from_topview: bool = False
        self._processor_widget:      Optional[QWidget] = None
        self._network_widget:   Optional[QWidget] = None

        self._timeline_left_width = 204
        self._px_per_sec          = 100.0

        self._clip_views: dict = {}
        self._rec_poll_timer = None
        self._recording_has_record_sources = None
        self._recording_suspended_track_linker_timer = False
        self._managed_signal_slots: dict[str, tuple[object, object]] = {}

        self._use_timeline_widget = False
        self._timeline_widget: Optional[QWidget] = None

        # ★ v3: 存储待连接的 dmx_out 回调（在 programmer 加载前预先登记）
        self._on_programmer_ready_cbs: list = []

        # ── 自动保存 ──────────────────────────────────────────────────────────
        self._auto_save_enabled: bool = False
        self._auto_save_interval: int = 5   # 分钟
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self._load_auto_save_settings()

        self.setWindowTitle(APP_NAME)
        # 2026-08-28 根治：硬编码 至冬软件新版图标.ico/app.ico 相对链在 frozen
        # 态（_MEIPASS 内无这两个文件名）恒落空 → 标题栏/任务栏系统默认图标。
        # 统一走 app_icon_path() 三级回退，与启动画面/dock 同源同图。
        try:
            from .qascade_theme import app_icon_path as _app_icon_path
            _ico = _app_icon_path()
            if _ico:
                self.setWindowIcon(QIcon(_ico))
        except Exception:
            pass
        self.setMinimumSize(900, 600)
        # R54 布局治理状态（A/B/C）：意图集合 + restoreState 期间守卫。
        # 必须先于 _restore_geometry() 存在，dock 信号处理器按 getattr 兜底。
        self._layout_restoring = False
        self._dock_user_docked: set = set()
        # 程序化悬浮守卫：_maybe_float_default 摆放 dock 期间置位，避免
        # topLevelChanged/dockLocationChanged 把「自动悬浮」误记为用户布局意图。
        self._float_applying = False
        self._restore_geometry()

        self._apply_global_style()
        self._build_menu()
        self._build_toolbar()
        self._build_panel_toolbar()
        self._connect_engine()
        self._resolve_pm()
        self._init_record_engine()
        self._build_central()
        self._build_status_bar()
        self._build_docks()
        install_editor_chrome(self)
        # ★ 必须在 dock 建好之后才能恢复其布局（restoreState 按 objectName 匹配
        #   已存在对象；提前调用是静默空操作，见 `_restore_dock_state` 说明）。
        _restore_dock_state(self)
        self._start_ui_timer()
        # ★ 2026-09-18 用户定案（"打开程序时让 topview 在后台完成初始化、隐藏加载、
        #   不要造成卡顿"）：启动后空闲期预热关键面板（含最贵的 matrix ≈645ms）。
        #   延迟 1.2s 起、失败静默、窗口关闭即取消（见 dispatch_flow.warm_up_panels）。
        try:
            from .dispatch_flow import warm_up_panels
            if os.environ.get("QASCADE_NO_WARMUP", "0") != "1":
                warm_up_panels(self)
        except Exception:
            pass
        # 呈现层收尾：首轮 polish 后强制第二行工具栏容器重算样式盒。
        # Qt 首轮布局会把全局 QToolBar 的 padding(2px 6px) 缓存进
        # QToolBarLayout，导致内嵌 PanelToolbar 下坠 6px（顶/左露出容器底色），
        # 与样式串内容无关；重设同一份串即恢复正常。纯视觉规避，不涉业务。
        QTimer.singleShot(0, self._repolish_panel_toolbar_row)

    def _repolish_panel_toolbar_row(self):
        tb = getattr(self, "_panel_toolbar", None)
        cont = tb.parent() if tb is not None else None
        if cont is not None and hasattr(cont, "setStyleSheet"):
            try:
                cont.setStyleSheet(cont.styleSheet())
                if hasattr(cont, "updateGeometry"):
                    cont.updateGeometry()
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  样式
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_global_style(self):
        # 全局 QMenuBar/QMenu/QToolBar/QToolButton/QDockWidget/QSplitter/QScrollBar
        # 的通用外观（含四态与焦点环）已由应用级 GLOBAL_QSS(qascade_theme) 统一提供，
        # 此处原作用域级整块重复定义按 restyle_spec.md §3 删除，避免四层样式互相覆盖。
        self.setStyleSheet(
            f"QMainWindow {{ background:{BG_ROOT}; }}"
        )
        # ★ R176/R178：快捷键动作在独立模块（上帝类冻结；幂等安装）。
        #   挂这里（样式收尾必经点）而非 __init__，避免把 __init__ 顶过 100 行门禁。
        from .main_window_shortcuts import install_shortcuts
        install_shortcuts(self)
        # ★ R180 #20：视图/面板菜单页面项统一 tabler 图标 + 跳转项中文名
        #   （独立模块；内部 singleShot 幂等延迟到菜单与快捷键动作就位后）。
        from .main_window_menu_icons import apply_main_menu_icons
        apply_main_menu_icons(self)

    # ══════════════════════════════════════════════════════════════════════════
    #  菜单 / 工具栏
    # ══════════════════════════════════════════════════════════════════════════

    def _build_menu(self):
        mb = self.menuBar()
        # L 条：菜单行钳高 24、左内缩 8px（四行节奏首行）；
        # 全局 QSS 的 QMenuBar 外观继续生效，这里只补盒模型。
        mb.setFixedHeight(24)
        mb.setStyleSheet("QMenuBar { padding-left:8px; }")

        fm = mb.addMenu("文件(&F)")
        self._act(fm, "新建项目(&N)", QKeySequence.StandardKey.New, self._new_project, icon_key="project_new")
        self._act(fm, "打开项目(&O)…", QKeySequence.StandardKey.Open, self._open_project, icon_key="project_open")
        fm.addSeparator()
        self._act(fm, "保存(&S)", QKeySequence.StandardKey.Save, self._save_project, icon_key="project_save")
        self._act(fm, "另存为(&A)…", QKeySequence.StandardKey.SaveAs, self._save_as, icon_key="project_save_as")
        fm.addSeparator()
        self._act(fm, "导出帧数据…", slot=self._export_frames, icon_key="export")
        self._act(fm, "导出 MVR (.mvr)…", slot=self._export_mvr, icon_key="export")
        fm.addSeparator()
        self._act(fm, "导入配接表 (CSV)…", slot=self._import_patch_table, icon_key="patch")
        fm.addSeparator()
        self._act(fm, "退出(&Q)", QKeySequence.StandardKey.Quit, self.close, icon_key="exit")

        em = mb.addMenu("编辑(&E)")
        self._act(em, "撤销(&Z)", QKeySequence.StandardKey.Undo, self._undo, icon_key="undo")
        self._act(em, "重做(&Y)", QKeySequence.StandardKey.Redo, self._redo, icon_key="redo")
        em.addSeparator()
        self._act(em, "选择全部", QKeySequence.StandardKey.SelectAll, self._select_all, icon_key="select_all")
        self._act(em, "删除选中", QKeySequence(Qt.Key.Key_Delete), self._delete_selected, icon_key="track_delete")

        vm = mb.addMenu("视图(&V)")
        self._act(vm, "放大时间线", QKeySequence(Qt.Key.Key_Equal), self._zoom_in, icon_key="zoom_in")
        self._act(vm, "缩小时间线", QKeySequence(Qt.Key.Key_Minus), self._zoom_out, icon_key="zoom_out")
        self._act(vm, "适配全部", QKeySequence(Qt.Key.Key_F), self._zoom_fit, icon_key="fit")
        vm.addSeparator()
        ws_menu = vm.addMenu("工作区布局 (&W)")
        self._act(ws_menu, "编程视图  (Patch + TopView + Programmer + Groups)",
                  QKeySequence("Ctrl+Shift+1"),
                  slot=self._workspace_programming, icon_key="layout_programming")
        self._act(ws_menu, "监控视图  (Monitor + Network + Processor)",
                  QKeySequence("Ctrl+Shift+2"),
                  slot=self._workspace_monitoring, icon_key="layout_monitoring")
        self._act(ws_menu, "全功能视图  (所有面板)",
                  QKeySequence("Ctrl+Shift+3"),
                  slot=self._workspace_full, icon_key="layout_full")
        self._act(ws_menu, "最小视图  (仅时间线)",
                  QKeySequence("Ctrl+Shift+0"),
                  slot=self._workspace_minimal, icon_key="layout_minimal")
        vm.addSeparator()
        self._act(vm, "重置布局 (最小化所有面板)", slot=self._reset_layout, icon_key="layout_reset")

        # ── 面板菜单：所有浮动面板的开关入口 ──────────────────────────────────
        pm_menu = mb.addMenu("面板(&P)")
        self._act(pm_menu, "节目管理",
                  slot=lambda: self._toggle_dock(self._program_dock,
                                                 not self._program_dock.isVisible()), icon_key="programs")
        self._act(pm_menu, "Patch 配接",
                  slot=lambda: self._toggle_dock(self._patch_dock,
                                                 not self._patch_dock.isVisible()), icon_key="patch")
        self._act(pm_menu, "TopView 视图",
                  slot=lambda: self._toggle_dock(self._topview_dock,
                                                 not self._topview_dock.isVisible()), icon_key="topview")
        # 跑动编排 — 替代旧版 RouteOrder 面板（旧 dock 保留仅作兼容占位）
        self._act(pm_menu, "跑动编排…",
                  slot=self._open_route_animator, icon_key="route_order")
        self._act(pm_menu, "Programmer 编程器",
                  slot=lambda: self._toggle_dock(self._programmer_dock,
                                                 not self._programmer_dock.isVisible()), icon_key="programmer")
        self._act(pm_menu, "矩阵幻灯片",
                  slot=lambda: self._toggle_dock(self._matrix_pattern_dock,
                                                 not self._matrix_pattern_dock.isVisible()), icon_key="matrix")
        self._act(pm_menu, "设备组",
                  slot=lambda: self._toggle_dock(self._fixture_group_dock,
                                                 not self._fixture_group_dock.isVisible()), icon_key="fixture_group")
        self._act(pm_menu, "效果预设库",
                  slot=lambda: self._toggle_dock(self._effect_browser_dock,
                                                 not self._effect_browser_dock.isVisible()), icon_key="effects")
        self._act(pm_menu, "Clip 附加处理…", icon_key="effects",   # R172 A5：全局窗口入口
                  slot=lambda: self._toggle_dock(self._clip_studio_dock, not self._clip_studio_dock.isVisible()))
        self._act(pm_menu, "调度台（选中设备监控 + 场景存储）", icon_key="dispatch",
                  slot=lambda: self._toggle_dock(self._dispatch_dock,
                                                 not self._dispatch_dock.isVisible()))
        self._act(pm_menu, "命令行代码编曲…",
                  slot=self._open_command_arrange, icon_key="command_arrange")
        self._act(pm_menu, "外接摇杆设备…",
                  slot=self._open_external_joystick_dialog, icon_key="panel_default")
        pm_menu.addSeparator()
        self._act(pm_menu, "Monitor 监视器",
                  slot=lambda: self._toggle_dock(self._monitor_dock,
                                                 not self._monitor_dock.isVisible()), icon_key="monitor")
        self._act(pm_menu, "Linker 关联器",
                  slot=lambda: self._toggle_dock(self._processor_dock,
                                                 not self._processor_dock.isVisible()), icon_key="processor")
        self._act(pm_menu, "Network 网络配置",
                  slot=lambda: self._toggle_dock(self._network_dock,
                                                 not self._network_dock.isVisible()), icon_key="network")

        tm = mb.addMenu("传输(&T)")
        self._act(tm, "播放/暂停", QKeySequence(Qt.Key.Key_Space), self._toggle_play, icon_key="play")
        self._act(tm, "停止", QKeySequence(Qt.Key.Key_Escape), self._stop, icon_key="stop")
        self._act(tm, "回到开头", QKeySequence(Qt.Key.Key_Home), self._rewind, icon_key="skip_back")

        nm_menu = mb.addMenu("网络(&N)")
        self._act(nm_menu, "启动网络", slot=self._start_network, icon_key="network_start")
        self._act(nm_menu, "停止网络", slot=self._stop_network, icon_key="network_stop")
        nm_menu.addSeparator()
        self._act(nm_menu, "网络配置…",
                  slot=lambda: self._toggle_dock(self._network_dock, True), icon_key="network")

        hm = mb.addMenu("帮助(&H)")
        self._act(hm, "使用教程 / 说明书", slot=self._open_tutorial, icon_key="about")
        hm.addSeparator()
        self._act(hm, "自动保存设置…", slot=self._show_auto_save_settings, icon_key="project_save")
        hm.addSeparator()
        self._act(hm, "关于 雾白纪 ULTRA…", slot=self._about, icon_key="about")

    def _act(self, menu: QMenu, text: str,
             shortcut=None, slot=None, icon_key: str = "") -> QAction:
        a = QAction(text, self)
        if icon_key:
            a.setIcon(_panel_icon(icon_key, 22))
        if shortcut:
            a.setShortcut(shortcut)
        if slot:
            a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_toolbar(self):
        from PyQt6.QtWidgets import QToolBar

        tb = QToolBar("主工具栏", self)
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        # L 条：主工具栏行高 30（四行节奏 菜单24/工具栏30/面板30/传输32），
        # 左内缩统一 8px；组内间距 4px、组间 1px sep_line 竖分隔（上下留 6px）。
        # QToolButton 钳高 22 —— 内容带 = 30 - padding(2+2) - 底边框 1 ≥ 22，
        # 不再出现旧版"钮高超出容器"的 CLIP。
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet(
            f"QToolBar {{ background:#282828; border-bottom:1px solid {SEP_LINE};"
            " padding:2px 8px; margin:0px; spacing:4px; }"
            f"QToolBar::separator {{ width:1px; background-color:{SEP_LINE};"
            " margin:6px 4px; }"
            "QToolButton { min-height:22px; max-height:22px;"
            " padding:0px 8px; border:1px solid transparent; border-radius:4px; }"
        )
        tb.setFixedHeight(30)
        # R52 用户指令：新建/打开/保存/撤销/重做/直出/软压整排隐藏——
        # 主窗顶部减一横排。tb 对象照常构建（功能入口走「文件」菜单与快捷键），
        # 仅不 addToolBar；全清空钮改挂面板开关行尾部（见 _panel_toolbar 尾）。
        # R52：tb 不入界面且立即销毁——防 QSettings.restoreState 按
        # objectName 把旧工具栏复活成"新建项目残留行"（用户实屏确认）。
        # 动作对象 parent=mw 存活，菜单/快捷键不受影响。
        tb.deleteLater()
        tb.setParent(None)
        self._main_toolbar_hidden = None

        def ta(icon_key: str, tip: str, slot) -> QAction:
            a = QAction(_panel_icon(icon_key, 16), "", self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        # ── 组1：项目 [新建 打开 保存] ──
        ta("project_new", "新建项目", self._new_project)
        ta("project_open", "打开项目", self._open_project)
        ta("project_save", "保存项目", self._save_project)
        tb.addSeparator()
        # ── 组2：编辑 [撤销 重做] ──
        ta("undo", "撤销 (Ctrl+Z)", self._undo)
        ta("redo", "重做 (Ctrl+Y)", self._redo)
        tb.addSeparator()

        # ── 组3：语义状态钮 [全清空 形状混合直出/软压] ──
        # L 条：**常态无色块底**，语义 hue 只留在 ≤24px 的亮色 glyph/文字上；
        # 激活（checked）/按压时才叠 rgba 低饱和底 + 同色描边——杜绝突兀色岛。

        # 「全清空」：平面钮 + bright_error glyph（momentary，hover 仅灰阶提亮）
        from PyQt6.QtWidgets import QToolButton
        release_btn = QToolButton(self)
        _release_ic = load_tabler_icon("circle-x", 16, BRIGHT_ERROR)
        release_btn.setIcon(
            _release_ic if not _release_ic.isNull()
            else _panel_icon("clear_output", 16))
        release_btn.setIconSize(QSize(16, 16))
        release_btn.setToolTip("全清空：停止实时效果并立即将所有 DMX 层输出归零")
        release_btn.setFixedSize(22, 22)   # 与同排 QToolButton 同高
        release_btn.setStyleSheet(
            "QToolButton { background:transparent;"
            " border:1px solid transparent; border-radius:4px; padding:0px; }"
            f"QToolButton:hover {{ background:{BG_PANEL}; }}"
            f"QToolButton:pressed {{ background:{_rgba_i(ERROR_RED, 90)};"
            f" border-color:{_rgba_i(BRIGHT_ERROR, 120)}; }}"
            f"QToolButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        )
        release_btn.clicked.connect(self._release_all_outputs)
        # R54 修复：此钮绝不能再 addWidget 进 tb——tb 已 deleteLater +
        # setParent(None)（R52），函数返回时 sip 会连子件一并销毁，
        # 面板行补挂时拿到的是已删 C++ 对象（启动必崩）。
        # 保持 parent=self 未渲染即可，由 _build_panel_toolbar 尾部补挂。
        # R52：全清空正式入口 = 面板开关行尾。构建顺序上本函数先于
        # PanelToolbar 创建 → 即时挂载失败则登记 pending，由面板行创建点补挂。
        self._clear_all_btn = release_btn
        pt = getattr(self, "_panel_toolbar", None)
        if pt is not None and hasattr(pt, "add_tail_widget"):
            pt.add_tail_separator()
            pt.add_tail_widget(release_btn)
        else:
            self._pending_clear_all_mount = True
        # 「形状混合模式」切换按鈕：直出 / 软压制。checked 态由实色块改为
        # rgba 低饱和绿底 + bright_success 文字 + 同族描边（L 条净化）。
        blend_btn = QToolButton(self)
        _orphans = []
        blend_btn.setText("直出")
        blend_btn.setCheckable(True)
        blend_btn.setChecked(True)   # 默认直出
        blend_btn.setToolTip(
            "形状层混合模式\n"
            "直出：Shape 偏移直接叠加到 base，摆杆/摇杆/音频 100% 响应\n"
            "软压制：base 越接近边界 shape 权重越小，防止过冲截断"
        )
        blend_btn.setFixedHeight(22)   # 与主工具栏行同高
        blend_btn.setMinimumWidth(44)  # 弹性宽度，不再钉死
        # 2026-08-27 用户点名去绿：极简中性切换——选中=提亮面+白字+蓝描边，
        # 语义只保留文字本身（直出/软压），不再使用任何绿色。
        blend_btn.setStyleSheet(
            "QToolButton { background:transparent; color:" + TEXT_SECONDARY + ";"
            " border:1px solid transparent; border-radius:4px;"
            " font-size:11px; font-weight:normal; padding:0px 8px; }"
            f"QToolButton:hover:!checked {{ background:{BG_PANEL};"
            f" color:{TEXT_TERTIARY}; }}"
            f"QToolButton:checked {{ background:{BG_BUTTON_HOVER};"
            f" color:{TEXT_SELECTED_FG}; border-color:{FOCUS_BLUE}; }}"
            f"QToolButton:checked:hover {{ background:#6b6b6b; }}"
            f"QToolButton:pressed {{ background:{SELECT_BLUE}; color:{TEXT_SELECTED_FG}; }}"
            f"QToolButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        )
        self._blend_mode_btn = blend_btn
        blend_btn.hide()

        def _on_blend_toggled(checked: bool):
            nm = getattr(self, '_nm', None)
            if nm is not None and hasattr(nm, 'set_shape_blend_mode'):
                nm.set_shape_blend_mode("direct" if checked else "soft")
            blend_btn.setText("直出" if checked else "软压")

        blend_btn.toggled.connect(_on_blend_toggled)
        for _w in _orphans:
            try:
                _w.setParent(None); _w.deleteLater()
            except Exception:
                pass
        # R54 修复：同理不 addWidget 进 tb（tb 销毁会连带删除本钮），
        # 保持 parent=self 未渲染；引用 _blend_mode_btn 依旧存活可用。

    def _release_all_outputs(self):
        """全清空按鈕处理函数：清除所有 NM 层输出并立即发送全零帧。

        同时停止会持续写入实时层的面板播放状态，避免清零后一帧又被
        Effect/AudioBridge 定时器重新写回。
        """
        nm = getattr(self, '_nm', None)
        if nm is None:
            return
        self._pause_live_effect_outputs()
        _stop_timeline_writer(self)   # 停 clip 写入者（否则清完 ≤23ms 写回）
        try:
            _release_all_outputs_for_ui(nm)
        except Exception:
            pass
        # 只释放播放会话的动态历史；全清空没有改动已烘焙的时间线输入。
        engine = getattr(self, '_engine', None)
        if engine is not None:
            try:
                release_playback_runtime(engine)
            except Exception:
                pass
        # 同步释放 Programmer 输出缓存，但保留 UI 草稿状态。
        pw = getattr(self, '_programmer_widget', None)
        if pw is not None:
            try:
                if hasattr(pw, 'clear_live_output'):
                    pw.clear_live_output()
            except Exception:
                pass
        _clear_editor_release_bookkeeping()

    def _pause_live_effect_outputs(self):
        """Stop panels that can keep writing live effect/editor layers."""
        stop_window_linker(self)
        _stop_audio_bridge_output(getattr(self, '_audio_bridge_widget', None))
        eb = getattr(self, '_effect_browser_widget', None)
        if eb is not None:
            try:
                if hasattr(eb, "stop_external"):
                    eb.stop_external()
                elif hasattr(eb, "_on_stop"):
                    eb._on_stop()
            except Exception:
                pass
        mw = getattr(self, '_matrix_pattern_widget', None)
        if mw is not None:
            try:
                from .matrix_slideshow.matrix_running_outputs import stop_all
                stop_all(mw)
            except Exception:
                pass
            _settle_matrix_hidden_timer(mw)   # 收口：隐藏面板不留 25fps 空转
        choreo = getattr(self, '_choreography_widget', None)
        if choreo is not None:
            try:
                start_btn = getattr(choreo, "_start_btn", None)
                if start_btn is not None and hasattr(start_btn, "isChecked") and start_btn.isChecked():
                    start_btn.setChecked(False)
                elif bool(getattr(choreo, "_engine_running", False)) and hasattr(choreo, "_stop_engine"):
                    choreo._stop_engine()
                elif hasattr(choreo, "_on_stop"):
                    choreo._on_stop()
            except Exception:
                pass
        stop_window_runner(self)

    def _build_panel_toolbar(self):
        from PyQt6.QtWidgets import QToolBar

        container = QToolBar("功能栏", self)
        container.setObjectName("toolbar_panels")
        container.setMovable(False)
        container.setFloatable(False)
        # 注意：这里不能写 background —— 本地一旦带背景属性，Qt 的样式合并
        # 会把全局 QToolBar 的 padding(2px 6px) 一并带回，子件被顶下 6px。
        # 背景沿用全局 QToolBar 底色(#282828)，由 PanelToolbar 自绘整条。
        container.setStyleSheet(
            "QToolBar { padding:0px; margin:0px; border:none; spacing:0px; }")

        if getattr(self, "_main_toolbar_hidden", None) is not None:
            self.addToolBarBreak()
        self.addToolBar(container)

        self._panel_toolbar = PanelToolbar(self)
        install_program_light_control(self)
        # R52：补挂"先于面板行构建"的全清空钮（见 _build_toolbar 尾部登记）
        if getattr(self, "_pending_clear_all_mount", False) and getattr(self, "_clear_all_btn", None) is not None:
            self._panel_toolbar.add_tail_separator()
            self._panel_toolbar.add_tail_widget(self._clear_all_btn)
            self._pending_clear_all_mount = False

        self._panel_toolbar.monitor_toggled.connect(
            lambda v: self._toggle_dock(self._monitor_dock, v))
        self._panel_toolbar.processor_toggled.connect(
            lambda v: self._toggle_dock(self._processor_dock, v))
        self._panel_toolbar.network_toggled.connect(
            lambda v: self._toggle_dock(self._network_dock, v))
        self._panel_toolbar.patch_toggled.connect(
            lambda v: self._toggle_dock(self._patch_dock, v))
        self._panel_toolbar.topview_toggled.connect(
            lambda v: self._toggle_dock(self._topview_dock, v))
        self._panel_toolbar.route_order_toggled.connect(
            lambda v: self._toggle_dock(self._route_order_dock, v))
        self._panel_toolbar.programmer_toggled.connect(
            lambda v: self._toggle_dock(self._programmer_dock, v))
        if hasattr(self._panel_toolbar, "matrix_pattern_toggled"):
            self._panel_toolbar.matrix_pattern_toggled.connect(
                lambda v: self._toggle_dock(self._matrix_pattern_dock, v))
        self._panel_toolbar.fixture_group_toggled.connect(
            lambda v: self._toggle_dock(self._fixture_group_dock, v))
        self._panel_toolbar.studio_toggled.connect(
            lambda v: self._toggle_dock(self._clip_studio_dock, v))
        if hasattr(self._panel_toolbar, "dispatch_toggled"):
            self._panel_toolbar.dispatch_toggled.connect(
                lambda v: self._toggle_dock(self._dispatch_dock, v))
        # ── 全局音频来源切换 ──
        if hasattr(self._panel_toolbar, "audio_source_changed"):
            self._panel_toolbar.audio_source_changed.connect(
                self._on_global_audio_source_changed)

        # 外部控制绑定入口已从主窗口移除；不再把工具条信号连接到设置弹窗。
        # 底层驱动代码仍保留供内部兼容。

        container.addWidget(self._panel_toolbar)
        # L 条：容器 QToolBar 钳高 30 与 PanelToolbar 行高一致，消除全局样式
        # 给 QToolBar 预留的垂直余量（否则子件居中后上下露出容器底色斑）。
        container.setFixedHeight(30)

    # ══════════════════════════════════════════════════════════════════════════
    #  Central Widget
    # ══════════════════════════════════════════════════════════════════════════

    def _build_central(self):
        center = QWidget()
        v_lay = QVBoxLayout(center)
        # F 条：中央区 padding 8（工作区与工具栏/边缘保持 8px 呼吸）
        v_lay.setContentsMargins(8, 8, 8, 8)
        v_lay.setSpacing(0)

        self._transport = TransportBar()
        self._transport.play_clicked.connect(self._play)
        self._transport.pause_clicked.connect(self._pause)
        self._transport.stop_clicked.connect(self._stop)
        self._transport.seek_changed.connect(self._seek)
        self._transport.record_started.connect(self._start_record)
        self._transport.record_stopped.connect(self._stop_record)
        self._transport.timecode_fps_changed.connect(self._on_timecode_fps_changed)
        v_lay.addWidget(self._transport)

        # 无节目空态轻提示 chip（H 条）：半透明 warn 底 + 图标 + 主文案 +
        # 弱提示，靠上 12px 左侧占位、右侧让空，时间线整体禁用期间不遮挡。
        # 纯视觉层：仅由 _apply_timeline_program_gate 控制显隐，不参与业务判断。
        gate_chip = QWidget()
        # R49 空态卡重制：极简中性信息卡（弃警示琥珀）——
        # 居中悬浮、暗卡浅边、info 蓝图标、主/副双行文案，纯展示无警示感。
        gate_chip.setObjectName("gateChip")
        gate_chip.setStyleSheet(
            "QWidget#gateChip {"
            f" background:{_rgba_i(BG_PANEL, 245)};"
            f" border:1px solid {BORDER_NORMAL};"
            " border-radius:6px; }"
            "QWidget#gateChip QLabel { background:transparent; border:none; }"
        )
        gate_chip.setFixedWidth(360)
        g_effect = QGraphicsDropShadowEffect(gate_chip)
        g_effect.setBlurRadius(28)
        g_effect.setOffset(0, 4)
        g_effect.setColor(QColor(0, 0, 0, 38))
        gate_chip.setGraphicsEffect(g_effect)
        g_lay = QHBoxLayout(gate_chip)
        g_lay.setContentsMargins(14, 12, 14, 12)
        g_lay.setSpacing(12)
        gate_ic_lab = QLabel()
        _ic = load_tabler_icon("info-circle", 26, FOCUS_BLUE)
        if not _ic.isNull():
            gate_ic_lab.setPixmap(_ic.pixmap(26, 26))
        gate_ic_lab.setAlignment(Qt.AlignmentFlag.AlignTop)
        g_lay.addWidget(gate_ic_lab)
        gate_txt_col = QVBoxLayout()
        gate_txt_col.setSpacing(3)
        _g_main = QLabel("未打开节目")
        _g_main.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; font-weight:bold;")
        # R182：原两行锁定提示（"已锁定" + 导入路径说明）压成一行短句
        _g_hint1 = QLabel("时间线锁定：请在节目管理新建")
        _g_hint1.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:11px;")
        gate_txt_col.addWidget(_g_hint1)
        gate_txt_col.addStretch(1)
        g_lay.addLayout(gate_txt_col, 1)

        gate_hint = QWidget()   # 靠上定位容器：透明底，卡片水平居中
        gate_hint.setStyleSheet("background:transparent;")
        gh_lay = QHBoxLayout(gate_hint)
        gh_lay.setContentsMargins(0, 26, 0, 2)   # 靠上 26px，不遮刻度
        gh_lay.addStretch(1)
        gh_lay.addWidget(gate_chip)
        gh_lay.addStretch(1)
        gate_hint.setVisible(False)
        self._program_gate_hint = gate_hint
        v_lay.addWidget(gate_hint)

        if TimelineWidget is not None and self._pm is not None:
            self._use_timeline_widget = True
            self._timeline_widget = TimelineWidget(
                session=self._pm.project, parent=center)
            self._timeline_widget.processor_requested.connect(
                self._on_clip_to_processor)
            if hasattr(self._timeline_widget, "clip_fixture_group_load_requested"):
                self._timeline_widget.clip_fixture_group_load_requested.connect(
                    self._on_clip_load_fixture_group_requested)
            if hasattr(self._timeline_widget, "_track_list"):
                tl = self._timeline_widget._track_list
                if hasattr(tl, "filter_edit"):
                    tl.filter_edit.connect(self._on_filter_edit)
            v_lay.addWidget(self._timeline_widget, stretch=1)
            self._connect_timeline_transport()
            # ★ R172 A5：右击菜单「Clip 附加处理…」信号链终点（全链由手术回归钉死）
            if hasattr(self._timeline_widget, "clip_processing_studio_requested"):
                self._timeline_widget.clip_processing_studio_requested.connect(
                    lambda cid: _open_clip_studio(self, str(cid)))
        else:
            self._use_timeline_widget = False
            self._timeline_widget = None
            self._build_central_fallback(v_lay)

        self.setCentralWidget(center)

        # Connect Art-Net TimeCode signal from network manager
        if self._nm is not None and hasattr(self._nm, "timecode_received"):
            self._nm.timecode_received.connect(self._on_timecode_received)

    # ══════════════════════════════════════════════════════════════════════════
    #  Transport ↔ Timeline Ctrl 连接
    # ══════════════════════════════════════════════════════════════════════════

    def _managed_connect(self, key: str, signal, slot) -> None:
        """Keep one live connection per logical signal slot."""
        registry = getattr(self, "_managed_signal_slots", None)
        if registry is None:
            registry = {}
            self._managed_signal_slots = registry
        old = registry.pop(str(key), None)
        if old is not None:
            old_signal, old_slot = old
            try:
                old_signal.disconnect(old_slot)
            except Exception:
                pass
        try:
            signal.disconnect(slot)
        except Exception:
            pass
        try:
            signal.connect(slot)
            registry[str(key)] = (signal, slot)
        except Exception:
            pass

    def _connect_programmer_timeline_signals(self, pw=None) -> None:
        tw = getattr(self, "_timeline_widget", None)
        ctrl = getattr(tw, "ctrl", None) if tw is not None else None
        pw = pw if pw is not None else getattr(self, "_programmer_widget", None)
        if ctrl is None or pw is None:
            return
        if hasattr(pw, "on_timeline_tick"):
            # B3: GIL fleet — programmer panel moved to Tier 1 (every 2nd frame)
            self._managed_connect("timeline:programmer:tick", ctrl.playback_tick_tier1, pw.on_timeline_tick)
        if hasattr(pw, "on_timeline_play"):
            self._managed_connect("timeline:programmer:play", ctrl.playback_started, pw.on_timeline_play)
        if hasattr(pw, "on_timeline_stop"):
            self._managed_connect(
                "timeline:programmer:stop",
                ctrl.playback_stopped,
                lambda _pos, _pw=pw: _pw.on_timeline_stop(),
            )

    def _connect_audio_bridge_timeline_signals(self, abw=None) -> None:
        tw = getattr(self, "_timeline_widget", None)
        ctrl = getattr(tw, "ctrl", None) if tw is not None else None
        abw = abw if abw is not None else getattr(self, "_audio_bridge_widget", None)
        if ctrl is None or abw is None:
            return
        if hasattr(abw, "on_timeline_tick"):
            # B3: GIL fleet — audio bridge moved to Tier 1 (every 2nd frame)
            self._managed_connect("timeline:audio_bridge:tick", ctrl.playback_tick_tier1, abw.on_timeline_tick)
        if hasattr(abw, "on_timeline_play"):
            self._managed_connect("timeline:audio_bridge:play", ctrl.playback_started, abw.on_timeline_play)
        if hasattr(abw, "on_timeline_stop"):
            self._managed_connect(
                "timeline:audio_bridge:stop",
                ctrl.playback_stopped,
                lambda _pos, _w=abw: _w.on_timeline_stop(),
            )

    def _connect_matrix_timeline_signals(self, matrix=None) -> None:
        tw = getattr(self, "_timeline_widget", None)
        ctrl = getattr(tw, "ctrl", None) if tw is not None else None
        matrix = matrix if matrix is not None else getattr(self, "_matrix_pattern_widget", None)
        if ctrl is None or matrix is None:
            return
        if hasattr(matrix, "on_timeline_tick"):
            # B3: GIL fleet — matrix panel moved to Tier 1 (every 2nd frame)
            self._managed_connect("timeline:matrix:tick", ctrl.playback_tick_tier1, matrix.on_timeline_tick)
        if hasattr(matrix, "on_timeline_play"):
            self._managed_connect(
                "timeline:matrix:play",
                ctrl.playback_started,
                lambda pos, _w=matrix: _w.on_timeline_play(pos),
            )
        if hasattr(matrix, "on_timeline_stop"):
            self._managed_connect(
                "timeline:matrix:stop",
                ctrl.playback_stopped,
                lambda _pos, _w=matrix: _w.on_timeline_stop(),
            )

    def _on_clip_advanced_changed(self, clip_id):
        """Tab2 通道延迟比例 / Tab3 延迟函数改动：写入已在面板完成，这里触发实时刷新。"""
        tw = getattr(self, '_timeline_widget', None)
        if tw is None:
            return
        try:
            # R57：clip 属性级变更走精准刷新，替代全量 tracks_changed 重建
            if hasattr(tw, '_refresh_clips_targeted'):
                tw._refresh_clips_targeted([clip_id])
            else:
                if hasattr(tw, '_invalidate_engine_cache'):
                    tw._invalidate_engine_cache(clip_id)
                tw.ctrl.clip_changed.emit(clip_id)
        except Exception:
            pass

    def _connect_timeline_transport(self):
        tw   = self._timeline_widget
        ctrl = tw.ctrl

        # ★ 修复播放信号冲突：使用 TimelineWidget 时断开 MainWindow 的 _play/_pause/_stop，
        #   只让 TimelineController 负责播放控制，避免双重触发。
        try:
            self._transport.play_clicked.disconnect(self._play)
        except Exception:
            pass
        try:
            self._transport.pause_clicked.disconnect(self._pause)
        except Exception:
            pass
        try:
            self._transport.stop_clicked.disconnect(self._stop)
        except Exception:
            pass
        try:
            self._transport.seek_changed.disconnect(self._seek)
        except Exception:
            pass

        # 只连接到 TimelineController. 这些连接可能在工程重载/懒加载后重建，
        # 用 managed key 避免同一个 tick 多次进播放链路。
        self._managed_connect("transport:play", self._transport.play_clicked, self._timeline_play)
        self._managed_connect("transport:pause", self._transport.pause_clicked, ctrl.pause)
        self._managed_connect("transport:stop", self._transport.stop_clicked, ctrl.stop)
        self._managed_connect("transport:seek", self._transport.seek_changed, ctrl.seek)

        # ★ R173 性能（perf-hotspot-survey #12）：update_position 只刷新播放位置数字
        # 显示（NumSlider + 时间码文本），无任何输出/DMX/层依赖 —— 纯 UI 展示，
        # 降到 Tier 2（~10Hz，"pure UI refresh"）而非随 Tier 0（~43Hz，clip 渲染
        # 输出关键路径）陪跑；clip 渲染仍在 playback_tick（见下方 clip_render:tick）。
        self._managed_connect("timeline:transport:tick", ctrl.playback_tick_tier2, self._transport.update_position)
        self._managed_connect("timeline:tracks_changed:program_dirty", ctrl.tracks_changed, self._on_timeline_content_changed)
        self._managed_connect("timeline:clip_changed:program_dirty", ctrl.clip_changed, self._on_timeline_content_changed)
        # transport 播放按钮状态（模块级，见 _wire_transport_play_state）
        _wire_transport_play_state(self, ctrl)

        pw = self._programmer_widget
        at = getattr(tw, '_audio_track', None)
        if at:
            try:
                if hasattr(at, "audio_changed"):
                    self._managed_connect("timeline:audio_track:dirty", at.audio_changed, self.mark_dirty)
            except Exception:
                pass
        if pw is not None:
            self._connect_programmer_timeline_signals(pw)
            if at:
                self._managed_connect(
                    "timeline:programmer:audio_loaded",
                    at.audio_loaded,
                    lambda path, _dur, _pw=pw: _pw.link_timeline_audio(path),
                )
                # ★ 新增：连接删除信号，删除参考轨时通知 Programmer 停止音频链接
                self._managed_connect("timeline:programmer:audio_removed", at.audio_removed, pw.on_timeline_stop)

        # ★ 连接时间线音频事件到 AudioBridgePanel（使音频调制能真正工作）
        abw = getattr(self, '_audio_bridge_widget', None)
        if abw is not None:
            try:
                self._connect_audio_bridge_timeline_signals(abw)
                if at:
                    self._managed_connect(
                        "timeline:audio_bridge:audio_loaded",
                        at.audio_loaded,
                        lambda path, _dur, _w=abw: _w.link_timeline_audio(path),
                    )
                    # 如果音频已经加载，立即传递路径
                    existing_path = getattr(at, '_audio_path', None)
                    if existing_path:
                        abw.link_timeline_audio(existing_path)
            except Exception:
                pass

        # ★ 修复回放空白：把时间线 clip.frames 渲染到 NetworkManager 的
        #   "timeline" 层。先前 TimelineController._on_tick 只发出
        #   playback_tick(pos)，没有任何模块据此渲染 clip 数据，导致 clip
        #   只是 UI 上画了块色块、播放时灯光毫无反应。这里把现成但从未实例化的
        #   PlaybackEngine 接入，让其按 ctrl 的位置广播渲染每一帧。
        try:
            if (self._engine is None
                    or not hasattr(self._engine, "render_at")):
                from ..engine.playback_engine import PlaybackEngine
                self._engine = PlaybackEngine(self._nm, self._pm, parent=self)
            if hasattr(self._engine, "render_at"):
                try:
                    ctrl.playback_tick.disconnect(self._engine.render_at)
                except Exception:
                    pass
                # ── 第3步：生成线程时钟源 + 生命周期(QASCADE_GEN_THREAD=1 才生效) ──
                # 时钟源读 ctrl 的普通属性(GIL 原子读)，用 monotonic 独立算位置：
                # 模态冻结冻住 Qt 事件循环时这些值不变，生成线程照算照 render → 灯不停。
                _gen_on = bool(getattr(self._engine, "_gen_thread_enabled", False))
                if _gen_on and hasattr(self._engine, "set_gen_clock_source"):
                    import time as _t
                    def _clock_source(_ctrl=ctrl):
                        try:
                            if not bool(getattr(_ctrl, "_playing", False)):
                                return (False, 0.0)
                            t0 = float(getattr(_ctrl, "_play_t0", 0.0) or 0.0)
                            return (True, max(0.0, _t.monotonic() - t0))
                        except Exception:
                            return (False, 0.0)
                    self._engine.set_gen_clock_source(_clock_source)
                    def _gen_on_play(_pos, _e=self._engine):
                        try:
                            _e.start_gen_thread()
                        except Exception:
                            pass
                    def _gen_on_stop(_pos, _e=self._engine):
                        try:
                            _e.stop_gen_thread()
                        except Exception:
                            pass
                    self._managed_connect("timeline:gen_thread:start", ctrl.playback_started, _gen_on_play)
                    self._managed_connect("timeline:gen_thread:stop", ctrl.playback_stopped, _gen_on_stop)

                def _render_clip_layer(pos, _ctrl=ctrl, _engine=self._engine):
                    try:
                        if bool(getattr(_ctrl, "is_playing", False)):
                            # 生成线程模式：播放态 render 由生成线程独立驱动，UI tick 不再重复 render
                            # (只保留播针/面板更新经其它消费者)。避免双重 render + 让 UI tick 更轻。
                            if bool(getattr(_engine, "_gen_thread_enabled", False)) \
                                    and _engine.gen_thread_active():
                                return
                            # ★ LTP 规则 3：播放过程中每帧不经过 render_playback_at（否则每帧刷新接管时间戳），
                            # 而是直接调用内部 _render_position，不设置 _ltp_takeover_pending。
                            if hasattr(_engine, "_render_position"):
                                _engine._render_position(float(pos), layer_name="clip_playback")
                            elif hasattr(_engine, "render_playback_at"):
                                _engine.render_playback_at(pos)
                            else:
                                _engine.render_at(pos)
                        else:
                            # 拖针预览：每次落点算一次接管事件，经过 render_preview_at 设置标记。
                            if hasattr(_engine, "render_preview_at"):
                                _engine.render_preview_at(pos)
                            else:
                                _engine.render_at(pos)
                    except Exception:
                        pass
                _wire_audio_mod(self, ctrl)   # ★ R174#7 调制三连接（tick/stop/双击编辑）
                self._managed_connect("timeline:clip_render:tick", ctrl.playback_tick, _render_clip_layer)
                def _clear_preview_on_play(pos, _nm=self._nm, _engine=self._engine):
                    """playback_started 时：清预览层 + ★ LTP 规则 3 点击播放接管通知。"""
                    try:
                        if _nm is not None and hasattr(_nm, "clear_layer"):
                            _nm.clear_layer("clip_preview")
                    except Exception:
                        pass
                    # _timeline_play renders the takeover frame before
                    # starting the clock; this is a fallback for other play callers.
                    try:
                        if (
                            hasattr(_engine, "_ltp_takeover_pending")
                            and not bool(getattr(_engine, "_playback_start_frame_rendered", False))
                        ):
                            _engine._ltp_takeover_pending = True
                            _engine._ltp_takeover_layer = "clip_playback"
                    except Exception:
                        pass
                self._managed_connect("timeline:clip_render:play_start", ctrl.playback_started, _clear_preview_on_play)
            # 播放/预览层与编辑器层分离：暂停或拖针不会清空 Programmer/Matrix 等编辑器数据。
            # 只有明确停止回到 0 秒时，才清理 clip 播放/预览层；编辑层始终不动。
            if self._nm is not None and hasattr(self._nm, "clear_layer"):
                def _clear_timeline_layer_on_stop(pos, _nm=self._nm):
                    try:
                        if float(pos or 0.0) <= 1e-6:
                            for _layer in ("clip_playback", "clip_preview", "timeline"):
                                _nm.clear_layer(_layer)
                    except Exception:
                        pass
                self._managed_connect(
                    "timeline:clip_render:stop_clear",
                    ctrl.playback_stopped,
                    _clear_timeline_layer_on_stop,
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "PlaybackEngine wiring failed (timeline playback may stay silent): %s",
                e, exc_info=True)

        # ★ 修复录制信号：不把 frame_raw_in 直接连接到 ctrl.feed_dmx_frame。
        #   录制数据源改为 ProgrammerPanel.dmx_out（经过信号合成的最终输出）。
        #   frame_raw_in 仍然保持连接用于非录制状态下的网络监控。
        #   录制时的 feed_dmx_frame 由 _start_record 统一管理。
        self._managed_connect("timeline:remap", ctrl.remap_requested, self._on_remap_requested)

    def _on_timeline_content_changed(self, *args):
        # 数值编辑即时解锁；无参数的轨道通知可能只是颜色，交后台内容复核。
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                engine._timeline_render_cache_locked = False
                notify_project_change(_ensure_timeline_baker(self), self._pm.project,
                                      force=bool(args), engine=engine)
            except Exception:
                pass
        if self._active_program() is None:
            return
        note_project_edit(self)
        # ★ 2026-08-13 thr-2: 内容变化失效录制源缓存 —— 修复前
        #   _recording_has_record_sources 只在录制开始赋值/停止重置,
        #   录制中切换 clip「允许接受输出」后缓存仍 False → 该 clip 的
        #   录制源被静默忽略直至下次录制。tracks_changed 仅用户编辑事件
        #   发射(非每 tick), 失效成本一次性 O(n) 重扫。
        self._recording_has_record_sources = None
        try:
            self._update_title()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  兜底布局
    # ══════════════════════════════════════════════════════════════════════════

    def _build_central_fallback(self, v_lay: QVBoxLayout):
        ruler_row = QWidget()
        ruler_row.setFixedHeight(_TRACKLIST_TOP_BAR_H)
        ruler_lay = QHBoxLayout(ruler_row)
        ruler_lay.setContentsMargins(0, 0, 0, 0)
        ruler_lay.setSpacing(0)

        self._ruler_placeholder = QWidget()
        self._ruler_placeholder.setFixedWidth(self._timeline_left_width)
        self._ruler_placeholder.setStyleSheet(f"background:{BG_ROOT};")

        self._ruler = (TimelineRuler(px_per_sec=self._px_per_sec)
                       if TimelineRuler else QWidget())
        if hasattr(self._ruler, "seek_requested"):
            self._ruler.seek_requested.connect(self._seek)

        ruler_lay.addWidget(self._ruler_placeholder)
        ruler_lay.addWidget(self._ruler, stretch=1)
        v_lay.addWidget(ruler_row)

        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._track_list = TrackList() if TrackList else QWidget()
        if hasattr(self._track_list, "add_track"):
            self._track_list.add_track.connect(self._add_track)
        if hasattr(self._track_list, "track_deleted"):
            self._track_list.track_deleted.connect(self._on_track_deleted)
        if hasattr(self._track_list, "filter_edit"):
            self._track_list.filter_edit.connect(self._on_filter_edit)
        if hasattr(self._track_list, "track_selected"):
            self._track_list.track_selected.connect(self._on_track_selected)
        self._h_splitter.addWidget(self._track_list)

        _clip_right = QWidget()
        _clip_right.setStyleSheet(f"background:{BG_EDITOR};")
        _clip_right_lay = QVBoxLayout(_clip_right)
        _clip_right_lay.setContentsMargins(0, 0, 0, 0)
        _clip_right_lay.setSpacing(0)

        _clip_top_spacer = QWidget()
        _clip_top_spacer.setFixedHeight(_TRACKLIST_TOP_BAR_H)
        _clip_top_spacer.setStyleSheet(
            f"background:{BG_ROOT}; border-bottom:1px solid {POPUP_BORDER};")
        _clip_right_lay.addWidget(_clip_top_spacer)

        self._clip_scroll = QScrollArea()
        self._clip_scroll.setWidgetResizable(False)
        self._clip_scroll.setStyleSheet(
            f"QScrollArea {{ background:{BG_EDITOR}; border:none; }}")

        self._clip_container = QWidget()
        self._clip_container.setStyleSheet(f"background:{BG_EDITOR};")
        self._clip_rows_layout = QVBoxLayout(self._clip_container)
        self._clip_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._clip_rows_layout.setSpacing(0)

        self._clip_scroll.setWidget(self._clip_container)
        _clip_right_lay.addWidget(self._clip_scroll, stretch=1)

        self._h_splitter.addWidget(_clip_right)
        self._h_splitter.setSizes([self._timeline_left_width, 900])
        self._h_splitter.setHandleWidth(3)
        self._h_splitter.splitterMoved.connect(self._on_timeline_splitter_moved)

        self._clip_scroll.horizontalScrollBar().valueChanged.connect(
            self._on_clip_scroll_x_changed)
        self._clip_scroll.verticalScrollBar().valueChanged.connect(
            self._on_clip_scroll_y_changed)
        if hasattr(self._track_list, "scroll_y_changed"):
            self._track_list.scroll_y_changed.connect(
                self._on_tracklist_scroll_y_changed)

        v_lay.addWidget(self._h_splitter, stretch=1)

    def _on_timeline_splitter_moved(self, pos: int, index: int):
        sizes = self._h_splitter.sizes()
        if not sizes:
            return
        self._timeline_left_width = sizes[0]
        if hasattr(self, "_ruler_placeholder"):
            self._ruler_placeholder.setFixedWidth(self._timeline_left_width)

    def _on_clip_scroll_x_changed(self, value: int):
        if hasattr(self, "_ruler") and hasattr(self._ruler, "set_scroll_x"):
            self._ruler.set_scroll_x(value)

    def _on_clip_scroll_y_changed(self, value: int):
        if hasattr(self, "_track_list") and hasattr(self._track_list, "sync_scroll"):
            self._track_list.sync_scroll(value)

    def _on_tracklist_scroll_y_changed(self, value: int):
        if hasattr(self, "_clip_scroll"):
            bar = self._clip_scroll.verticalScrollBar()
            if bar.value() != value:
                bar.blockSignals(True)
                bar.setValue(value)
                bar.blockSignals(False)

    # ══════════════════════════════════════════════════════════════════════════
    #  StatusBar / Docks
    # ══════════════════════════════════════════════════════════════════════════

    def _build_status_bar(self):
        self._status = AppStatusBar()
        self.setStatusBar(self._status)

    def _build_docks(self):
        self._monitor_dock   = self._make_dock(
            "监视器 Monitor",   Qt.DockWidgetArea.RightDockWidgetArea,  icon_key="monitor")
        self._processor_dock = self._make_dock(
            "关联器 Linker", Qt.DockWidgetArea.BottomDockWidgetArea,   icon_key="processor")
        self._network_dock   = self._make_dock(
            "网络 Network",     Qt.DockWidgetArea.RightDockWidgetArea,  icon_key="network")

        self.tabifyDockWidget(self._network_dock, self._monitor_dock)

        self._monitor_dock.hide()
        self._processor_dock.hide()
        self._network_dock.hide()

        for dock, attr in (
            (self._monitor_dock,   "_monitor_btn"),
            (self._processor_dock, "_processor_btn"),
            (self._network_dock,   "_network_btn"),
        ):
            self._bind_dock_button(dock, attr)

        self._patch_dock = self._make_dock(
            "Patch 配接", Qt.DockWidgetArea.LeftDockWidgetArea, icon_key="patch")
        self._program_dock = self._make_dock(
            "节目管理", Qt.DockWidgetArea.LeftDockWidgetArea, icon_key="programs")
        self._build_program_manager_panel()
        self._topview_dock = self._make_dock(
            "TopView 视图", Qt.DockWidgetArea.BottomDockWidgetArea, icon_key="topview")
        self._route_order_dock = self._make_dock(
            "Route Order 跑动顺序", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="route_order")
        self._programmer_dock = self._make_dock(
            "Programmer 编程器", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="programmer")
        self._matrix_pattern_dock = self._make_dock(
            "Matrix PPT 矩阵幻灯片", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="matrix")
        self._fixture_group_dock = self._make_dock(
            "Fixture Groups 设备组", Qt.DockWidgetArea.LeftDockWidgetArea, icon_key="fixture_group")
        self._effect_browser_dock = self._make_dock(
            "效果预设库 Effects", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="effects")
        self._effect_browser_widget = None
        # ★ R172 A6/A5：clip高级处理不再单独成子模块 —— 「Clip 附加处理」dock
        #   承载 Studio 卡片画布（ClipStudioPanel，懒加载见 _lazy_load_panels）。
        self._clip_studio_dock = self._make_dock(
            "Clip 附加处理", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="effects")
        self._clip_studio_widget = None
        # 教程与说明书（内置，离线可读；超链接模式见 _on_tutorial_feature）
        self._tutorial_dock = self._make_dock(
            "教程与说明书 Tutorial", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="about")
        self._tutorial_widget = None
        # 调度台（效果制作统一总线 + 可追溯效果缓存）
        self._dispatch_dock = self._make_dock(
            "调度台 Dispatch", Qt.DockWidgetArea.RightDockWidgetArea, icon_key="dispatch")
        self._dispatch_widget = None
        self._audio_mod_dock = None   # ★ R174#7 调制编辑页（双击时懒建，见 _open_audio_mod_editor）
        self._audio_mod_editor = None
        self._feature_callout = None   # 引出标注覆盖层（懒建）
        self._beginner_tour = None     # 零基础模式·引导式巡览（懒建）
        self._patch_dock.hide()
        self._program_dock.show()
        for attr in ("_topview_dock", "_route_order_dock", "_programmer_dock",
                     "_matrix_pattern_dock", "_fixture_group_dock",
                     "_effect_browser_dock", "_clip_studio_dock",
                     "_tutorial_dock", "_dispatch_dock"):
            getattr(self, attr).hide()
        for dock, attr in (
            (self._program_dock,        "_program_btn"),
            (self._patch_dock,          "_patch_btn"),
            (self._topview_dock,        "_topview_btn"),
            (self._route_order_dock,    "_route_order_btn"),
            (self._programmer_dock,     "_programmer_btn"),
            (self._matrix_pattern_dock, "_matrix_pattern_btn"),
            (self._fixture_group_dock,  "_fixture_group_btn"),
            (self._effect_browser_dock, "_effect_browser_btn"),
            (self._clip_studio_dock,    "_studio_btn"),
            (self._tutorial_dock,       "_tutorial_btn"),
            (self._dispatch_dock,       "_dispatch_btn"),
        ):
            self._bind_dock_button(dock, attr)

        # ── R54 布局治理（A/C）───────────────────────────────────────────
        # 意图追踪：用户手动把 dock 拖入主界面（topLevelChanged→False /
        # dockLocationChanged）记入 _dock_user_docked，此后「面板」开关打开
        # 不再强制悬浮（_toggle_dock→_maybe_float_default）。
        # 宽度下限：dock 层 setMinimumWidth(_DOCK_MIN_WIDTHS 对应值) 兜住
        # 拖入后的落位宽度；dockLocationChanged 时再 resizeDocks 抬到下限
        # （每个落位事件各一次，不参与每帧布局争抢）。
        # 仅新增连接，不改任何既有 visibilityChanged 连接与 pre_snap 机制。
        for attr in self._R54_ALL_DOCK_ATTRS:
            d = getattr(self, attr, None)
            if d is None:
                continue
            floor = self._dock_min_width_floor(d)
            if floor > 0:
                d.setMinimumWidth(floor)
            d.topLevelChanged.connect(
                lambda floating, dd=d: self._on_dock_top_level_changed(dd, floating))
            d.dockLocationChanged.connect(
                lambda _area, dd=d: self._on_dock_location_changed(dd))

    def _make_dock(self, title: str, area,
                   icon_key: str = "") -> QDockWidget:
        dock = QDockWidget(title, self)
        key = icon_key if icon_key in _PANEL_ICON_NAMES else _panel_key_from_text(title)
        icon = _panel_icon(key, 24)
        # 浮动 dock 的窗口图标统一用官方应用图标（用户点名：页面左上角出现
        # 系统默认/残缺图标）；面板 glyph 保留在 toggle 按钮上做辨识。
        try:
            from PyQt6.QtGui import QIcon as _QIcon
            # ★ 2026-09-20 修复：原写 `_th.app_icon_path()`，但本文件从未导入 `_th`
            #   → NameError 被下面的 except 静默吞掉 → **这个"统一官方图标"的修复
            #   从未生效**（浮动 dock 退回面板 glyph，用户点名的图标问题依旧）。
            #   改走本文件 2042 行已有的同款局部导入（三级回退，与启动画面同源）。
            from .qascade_theme import app_icon_path as _app_icon_path
            _app_ico = _app_icon_path()
            dock.setWindowIcon(_QIcon(_app_ico) if _app_ico else icon)
        except Exception:
            dock.setWindowIcon(icon)
        try:
            dock.toggleViewAction().setIcon(icon)
        except Exception:
            pass
        safe_name = title.replace(" ", "_").replace("/", "_").encode("ascii", "ignore").decode()
        dock.setObjectName(f"dock_{safe_name}")
        dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea  |
            Qt.DockWidgetArea.LeftDockWidgetArea
        )
        ph = QWidget()
        ph.setStyleSheet(f"background:{PANEL_BG};")
        # 响应式缩放：Dock 占位控件不再设置不可突破的硬最小尺寸，
        # 避免高密度面板在缩小时撑住主窗口。
        ph.setMinimumSize(0, 0)
        ph.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        dock.setWidget(ph)
        self.addDockWidget(area, dock)
        return dock

    #: 各 dock 的可用最小宽（区域有合理下限，中央区让路；
    #: 之前的问题是 900~1090 的病态下限锁死窗口，而非"存在下限"本身）。
    _DOCK_MIN_WIDTHS = {
        "节目管理": 176, "Patch_配接": 240, "设备组": 240,
        "Programmer_编程器": 320, "Monitor_监视器": 340, "Network_网络": 320,
        "TopView_视图": 744, "Matrix_PPT_矩阵幻灯片": 340, "效果预设库_Effects": 280,
        "附加处理": 640, "关联器_Linker": 260, "Route_Order_跑动顺序": 240,
        "AI编舞引擎": 260,
        # 教程/说明书是阅读型面板：窄于此则正文被挤成竖条，必须兜住可读宽度。
        "教程": 480,
        # 调度台是左右分栏的大面板（左窗格区 + 右缓存区），需更宽才可读。
        "调度台": 640,
    }

    #: R54-A：打开时默认悬浮的 9 个子页面 dock（列表序即瀑布坐标槽位）。
    #: 节目管理（固定左侧）、TopView、关联器三个保持原位，不入此列。
    _R54_FLOAT_DOCK_ATTRS = (
        "_monitor_dock", "_network_dock", "_patch_dock", "_route_order_dock",
        "_programmer_dock", "_matrix_pattern_dock", "_fixture_group_dock",
        "_effect_browser_dock", "_clip_studio_dock",
        # R55 用户点名：TopView/关联器默认不再折叠在底部，纳入悬浮组
        "_topview_dock", "_processor_dock",
        # R58 用户点名：教程/说明书是阅读型窗口，**默认独立窗口**（不塞在主界面边缘），
        # 但保留 dock 语义 —— 用户拖入主界面即磁吸分栏（_dock_user_docked 记住意图）。
        "_tutorial_dock",
        # 调度台同样是左右分栏大面板：默认独立窗口，拖回即分栏。
        "_dispatch_dock",
    )
    _R54_ALL_DOCK_ATTRS = _R54_FLOAT_DOCK_ATTRS + (
        "_program_dock",
    )
    #: R54-A：默认悬浮尺寸与瀑布错位基准（用户点名 440×680 + (220,140) 起步）。
    _R54_FLOAT_SIZE = (440, 680)
    _R54_FLOAT_ORIGIN = (220, 140)
    _R54_FLOAT_STEP = (32, 28)
    #: 个别面板的悬浮尺寸覆盖：阅读型/分栏型大窗需要更宽。
    _FLOAT_SIZE_OVERRIDE = {
        "_effect_browser_dock": (1120, 800),
        "_tutorial_dock": (900, 760),
        "_dispatch_dock": (1080, 720),
        # R172：Studio 是 左栏+画布+问题列表 的卡片画布大面板（§3.1）。
        "_clip_studio_dock": (1280, 800),
    }

    def _install_dock_widget(self, dock: QDockWidget, w: QWidget) -> None:
        """面板正式装入 dock 的唯一入口（L 波系统性解锁）。

        面板根水平尺寸策略改 Ignored 以斩断病态 minimumSizeHint 链，
        再按 _DOCK_MIN_WIDTHS 给回"可用下限"：窗口能缩到各区域下限之和，
        任何区域都不会被挤成不可读碎片。
        """
        try:
            w.setMinimumWidth(0)
            sp = w.sizePolicy()
            sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            w.setSizePolicy(sp)
            floor = 0
            for key, v in self._DOCK_MIN_WIDTHS.items():
                if key in dock.objectName() or key in dock.windowTitle():
                    floor = max(floor, v)
            if floor > 0:
                w.setMinimumWidth(floor)
        except Exception:
            pass
        dock.setWidget(w)

    # ── R54 布局治理辅助（A/B/C）──────────────────────────────────────

    def _dock_min_width_floor(self, dock: QDockWidget) -> int:
        """取 _DOCK_MIN_WIDTHS 中该 dock 的下限。

        旧匹配（key in objectName/title）对中英混排键（如 "Patch_配接" vs
        标题 "Patch 配接"、objectName 被 ascii 化）会漏配，这里补两条
        兜底：标题空格换下划线再比、键首尾片段分别比 objectName/标题。
        """
        try:
            name = dock.objectName()
            title = dock.windowTitle()
        except Exception:
            return 0
        floor = 0
        for key, v in self._DOCK_MIN_WIDTHS.items():
            hit = key in name or key in title or key.replace("_", " ") in title
            if not hit and "_" in key:
                head = key.split("_", 1)[0]
                tail = key.rsplit("_", 1)[-1]
                hit = (head in name) or (head in title) or (tail in title)
            if hit:
                floor = max(floor, v)
        return floor

    def _dock_attr_of(self, dock: QDockWidget) -> str:
        for attr in self._R54_ALL_DOCK_ATTRS:
            if getattr(self, attr, None) is dock:
                return attr
        return ""

    #: dock ↔ 顶栏开关按钮的绑定（唯一真源）：面板条上的按钮既是开关，
    #: 也是零基础模式标注「未打开面板」时的可见锚点（`_dock_toggle_button`）。
    _DOCK_BUTTON_ATTRS: Tuple[Tuple[str, str], ...] = (
        ("_monitor_dock",           "_monitor_btn"),
        ("_processor_dock",         "_processor_btn"),
        ("_network_dock",           "_network_btn"),
        ("_program_dock",           "_program_btn"),
        ("_patch_dock",             "_patch_btn"),
        ("_topview_dock",           "_topview_btn"),
        ("_route_order_dock",       "_route_order_btn"),
        ("_programmer_dock",        "_programmer_btn"),
        ("_matrix_pattern_dock",    "_matrix_pattern_btn"),
        ("_fixture_group_dock",     "_fixture_group_btn"),
        ("_effect_browser_dock",    "_effect_browser_btn"),
        ("_clip_studio_dock",       "_studio_btn"),
        ("_tutorial_dock",          "_tutorial_btn"),
        ("_dispatch_dock",          "_dispatch_btn"),
    )

    def _bind_dock_button(self, dock: QDockWidget, btn_attr: str) -> None:
        """绑定 dock ↔ 开关按钮：可见性同步 + 登记反向查表用属性名。"""
        try:
            setattr(dock, "_toggle_btn_attr", btn_attr)
        except Exception:
            pass
        dock.visibilityChanged.connect(
            lambda visible, a=btn_attr: self._on_dock_visibility(a, visible))

    def _dock_toggle_button(self, dock: QDockWidget):
        """取该 dock 在顶栏「面板」条上的开关按钮（找不到返回 None）。

        零基础模式标注「未打开的面板」时锚定到它的开关按钮 —— 标注始终落在
        用户看得见的控件上，不需要把面板弹出来。
        """
        attr = getattr(dock, "_toggle_btn_attr", None)
        if not attr:
            for d_attr, b_attr in self._DOCK_BUTTON_ATTRS:
                if getattr(self, d_attr, None) is dock:
                    attr = b_attr
                    break
        if not attr:
            return None
        return getattr(getattr(self, "_panel_toolbar", None), attr, None)

    def _maybe_float_default(self, dock: QDockWidget) -> None:
        """R54-A：用户尚无布局意图的子页面，打开时默认悬浮。

        仅 9 个可悬浮 dock 生效（节目管理/TopView/关联器保持原位）；
        用户手动拖入过主界面的 dock（_dock_user_docked）不再干预；
        已悬浮的保持原位原尺寸（重开不重摆）。
        """
        try:
            attr = self._dock_attr_of(dock)
            if not attr or attr not in self._R54_FLOAT_DOCK_ATTRS:
                return
            if attr in self._dock_user_docked or dock.isFloating():
                return
            i = self._R54_FLOAT_DOCK_ATTRS.index(attr)
            # 程序化悬浮不该被记成「用户布局意图」（否则 _dock_user_docked 被污染，
            # 之后再也不会自动悬浮）；与 restoreState 的 _layout_restoring 同款守卫。
            self._float_applying = True
            try:
                dock.setFloating(True)
                size = self._FLOAT_SIZE_OVERRIDE.get(attr, self._R54_FLOAT_SIZE)
                dock.resize(*size)
                ox, oy = self._R54_FLOAT_ORIGIN
                sx, sy = self._R54_FLOAT_STEP
                # 阅读型大窗（教程）居中于主窗，避免右下角越界/半屏外。
                if size[0] > 600:
                    try:
                        g = self.frameGeometry()
                        dock.move(g.center().x() - size[0] // 2,
                                  max(g.top() + 40, g.center().y() - size[1] // 2))
                    except Exception:
                        dock.move(ox, oy)
                else:
                    dock.move(ox + i * sx, oy + i * sy)
            finally:
                self._float_applying = False
        except Exception:
            pass

    def _on_dock_top_level_changed(self, dock: QDockWidget, floating: bool) -> None:
        if getattr(self, "_layout_restoring", False) or getattr(self, "_float_applying", False):
            return
        if not floating:
            # 悬浮→停靠（用户拖入主界面）：记录布局意图 + 抬宽度下限。
            attr = self._dock_attr_of(dock)
            if attr:
                self._dock_user_docked.add(attr)
            self._enforce_dock_min_width(dock)

    def _on_dock_location_changed(self, dock: QDockWidget) -> None:
        if getattr(self, "_layout_restoring", False) or getattr(self, "_float_applying", False):
            return
        attr = self._dock_attr_of(dock)
        if attr:
            self._dock_user_docked.add(attr)
        self._enforce_dock_min_width(dock)

    def _enforce_dock_min_width(self, dock: QDockWidget) -> None:
        """R54-C：落位后宽度低于下限时抬到下限（每次落位至多一次）。

        dock 层 setMinimumWidth 已让 Qt 布局自然兜底，这里只兜
        拖入瞬间的实际宽度；不进 paint/resize 循环，无每帧争抢。
        """
        try:
            if dock.isFloating():
                return
            floor = self._dock_min_width_floor(dock)
            if floor > 0 and dock.width() < floor:
                self.resizeDocks([dock], [floor], Qt.Orientation.Horizontal)
        except Exception:
            pass

    def _build_program_manager_panel(self):
        panel = QWidget()
        # C 条：行解剖由 _ProgramRow 提供；列表仅承担 hover/选中底色与
        # 左侧 2px 竖条，不再写重复的通用控件外观（§3）。
        panel.setStyleSheet(
            f"QWidget {{ background:{BG_EDITOR}; color:{TEXT_PRIMARY}; }}"
            f"QLabel {{ color:{TEXT_PRIMARY}; }}"
            f"QListWidget {{ background:{BG_ROOT};"
            f" border:1px solid {SEP_LINE}; color:{TEXT_PRIMARY};"
            " font-size:11px; outline:none; }}"
            "QListWidget::item { padding:0 2px; }"
            f"QListWidget::item:hover {{ background:{BG_PANEL}; }}"
            "QListWidget::item:selected {"
            f" background:{SELECT_BLUE}; color:{TEXT_SELECTED_FG};"
            f" border-left:2px solid {FOCUS_BLUE}; }}"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._program_active_label = QLabel("当前节目: 未打开")
        self._program_active_label.setStyleSheet(f"font-weight:bold; color:{FOCUS_BLUE};")
        lay.addWidget(self._program_active_label)

        # C 条：节目搜索框（22px，纯视图层过滤，不改任何业务信号）
        self._program_search_text = ""
        self._program_search = QLineEdit()
        self._program_search.setPlaceholderText("搜索节目…")
        self._program_search.setClearButtonEnabled(True)
        self._program_search.setFixedHeight(22)
        # C 条搜索框精确 22px：全局 QLineEdit 的样式级盒模型会把实际渲染高
        # 顶到 26，这里给足整套盒属性做尺寸特化（§3 允许范围），
        # min/max 18 + padding1×2 + 边框2 = 22。
        self._program_search.setStyleSheet(
            f"QLineEdit {{ min-height:18px; max-height:18px;"
            f" padding:1px 5px; border-radius:4px;"
            f" background:{BG_INPUT}; color:{TEXT_PRIMARY};"
            f" border:1px solid {BORDER_NORMAL}; font-size:11px; }}")
        self._program_search.setToolTip("按名称过滤节目列表（仅影响显示）")
        self._program_search.textChanged.connect(self._on_program_search_changed)
        lay.addWidget(self._program_search)

        self._program_list = QListWidget()
        self._program_list.itemDoubleClicked.connect(lambda _item: self._open_selected_program())
        self._program_list.itemSelectionChanged.connect(
            self._sync_program_row_selection)   # itemWidget 子控件不自动变色
        lay.addWidget(self._program_list, stretch=1)

        # C 条：空态占位（分「无节目」/「无匹配」两种）
        try:
            from .widget_kit import EmptyState as _EmptyState
            self._program_empty_none = _EmptyState(
                "还没有节目", "请先新建一个节目。",
                icon_key="list-details")
            self._program_empty_match = _EmptyState(
                "无匹配节目", "换个搜索关键词试试", icon_key="search")
            self._program_empty_none.setVisible(False)
            self._program_empty_match.setVisible(False)
            lay.addWidget(self._program_empty_none)
            lay.addWidget(self._program_empty_match)
        except Exception:
            self._program_empty_none = None
            self._program_empty_match = None

        def _btn(text: str, slot, danger: bool = False, icon_key: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(24)   # 面板侧按钮簇统一 24px（C 条）
            # 精确盒模型钳制：全局 QPushButton 样式级 min-height(22)+padding
            # 会把实际渲染高顶到 28，这里压回 24 使 setFixedHeight 生效
            # （_css_btn 纵向 padding 为 0：min/max 22 + 边框2 = 24）。
            _box = "QPushButton { min-height:22px; max-height:22px; }"
            if danger:
                # 危险操作：中性底 + 红文字/红描边，hover 落到错误填充
                b.setStyleSheet(_css_btn(
                    BG_BUTTON, BRIGHT_ERROR, BG_BUTTON_HOVER,
                    border=_hex_alpha(BRIGHT_ERROR, '66')) + _box)
            else:
                b.setStyleSheet(_css_btn(
                    BG_BUTTON, TEXT_PRIMARY, BG_BUTTON_HOVER) + _box)
            if icon_key:
                b.setIcon(make_main_icon(icon_key, 16))
                b.setIconSize(QSize(16, 16))
            b.clicked.connect(slot)
            return b

        rows = QVBoxLayout()
        rows.setSpacing(8)   # C 条：按钮簇间距 8px、图标+文字

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(_btn("新建", self._new_program, icon_key="project_new"))
        row1.addWidget(_btn("打开", self._open_selected_program, icon_key="project_open"))
        rows.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(_btn("保存当前", self._save_current_program_clicked, icon_key="project_save"))
        row2.addWidget(_btn("关闭", self._close_current_program_clicked, icon_key="clear_output"))
        rows.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(_btn("重命名", self._rename_selected_program, icon_key="rename"))
        row3.addWidget(_btn("删除", self._delete_selected_program, danger=True, icon_key="track_delete"))
        rows.addLayout(row3)

        row4 = QHBoxLayout()
        row4.setSpacing(8)
        row4.addWidget(_btn("导入", self._import_program, icon_key="import"))
        row4.addWidget(_btn("导出", self._export_selected_program, icon_key="export"))
        rows.addLayout(row4)
        lay.addLayout(rows)

        hint = QLabel("时间线必须先打开一个节目才可编辑。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:11px;")
        lay.addWidget(hint)

        self._program_widget = panel
        self._install_dock_widget(self._program_dock, panel)
        self._refresh_program_list()

    def _on_program_search_changed(self, text: str):
        """节目搜索框回调：纯视图层过滤（C 条），不触碰业务数据。"""
        self._program_search_text = str(text or "")
        self._refresh_program_list()

    def _sync_program_row_selection(self):
        """把列表选中态同步到自定义行部件的白字配色（C 条）。"""
        lst = getattr(self, "_program_list", None)
        if lst is None:
            return
        for i in range(lst.count()):
            item = lst.item(i)
            widget = lst.itemWidget(item)
            if isinstance(widget, _ProgramRow):
                widget.set_selected(item.isSelected())

    def _project(self):
        if self._pm is None:
            return None
        return getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)

    def _timeline_ctrl(self):
        """命令行代码编曲子窗口用：取时间线控制器（建轨挂 clip / 读播放态）。"""
        tw = getattr(self, "_timeline_widget", None)
        return getattr(tw, "ctrl", None) if tw is not None else None

    def _open_external_joystick_dialog(self):
        """打开外接摇杆设备配置窗口。入口在主界面 面板 菜单。"""
        try:
            from .joystick_control_binding_device import ExternalJoystickConfigDialog
        except Exception as e:
            try:
                self._status.set_message(f"无法打开摇杆设备配置：{e}")
            except Exception:
                pass
            return
        dlg = ExternalJoystickConfigDialog(self)
        dlg.exec()

    def _open_command_arrange(self):
        """打开「命令行代码编曲」子窗口（非模态，单例）。入口在主界面 面板 菜单。

        以主窗口自身作宿主（已提供 _project()/_timeline_ctrl()），不再依赖自动编曲面板。
        """
        try:
            from .audio.command_arrange_dialog import CommandArrangeDialog
        except Exception as e:
            try:
                self._status.set_message(f"无法打开命令行编曲：{e}")
            except Exception:
                pass
            return
        dlg = getattr(self, "_cmd_arrange_dialog", None)
        if dlg is None:
            dlg = CommandArrangeDialog(self, parent=self)
            self._cmd_arrange_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ── Route Animator (跑动编排) ──────────────────────────────────────────

    def _toggle_route_animator(self, visible: bool) -> None:
        """面板工具栏 toggle：显示/隐藏跑动编排窗口，与其它面板按钮同步逻辑一致。"""
        if visible:
            self._open_route_animator()
        else:
            win = getattr(self, "_route_animator_window", None)
            if win is not None and win.isVisible():
                win.close()

    def _on_route_animator_window_closed(self) -> None:
        """跑动编排窗口关闭 → 面板按钮回到未选中态（不回触 toggle）。"""
        btn = getattr(getattr(self, "_panel_toolbar", None), "_route_order_btn", None)
        if btn is not None and btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _open_route_animator(self):
        """打开「跑动编排」子窗口（非模态，单例）。入口在主界面 面板 菜单。"""
        try:
            from .route_animator.runner_panel import RouteRunnerPanel
        except Exception as e:
            try:
                self._status.set_message(f"无法打开跑动编排：{e}")
            except Exception:
                pass
            return
        # 跑动编排依赖 TopView 的设备排布——确保 TopView 面板已构建（懒加载）
        if getattr(self, "_topview_widget", None) is None:
            try:
                self._lazy_load_panels(self._topview_dock)
            except Exception:
                pass
        win = getattr(self, "_route_animator_window", None)
        if win is None:
            win = RouteRunnerPanel(parent=self)
            self._route_animator_window = win
            # 关闭时窗口已把状态写入 project.route_animator_state → 标记项目已修改
            try:
                win.closed.connect(self.mark_dirty)
                win.closed.connect(self._on_route_animator_window_closed)
            except Exception:
                pass
            # 初始上下文
            try:
                audio_path = ""
                project = getattr(self._pm, "project", None) if self._pm else None
                if project is not None:
                    audio_path = getattr(project, "audio_path", "") or ""
                win.set_context(
                    project_manager=self._pm,
                    topview=self._topview_widget,
                    audio_path=audio_path,
                )
            except Exception:
                pass
            # 加载已有状态
            try:
                if project is not None:
                    anim_state = getattr(project, "route_animator_state", {}) or {}
                    if anim_state:
                        win.load_state(anim_state)
            except Exception:
                pass
        else:
            # 复用已有窗口：刷新 TopView 引用 + 分页列表（不重载状态，避免覆盖未保存编辑）
            try:
                win._topview_widget = self._topview_widget
                win._reload_topview_pages()
            except Exception:
                pass
        win.show()
        win.raise_()
        win.activateWindow()
        # 菜单等其它入口打开时，同步面板 toggle 按钮为选中态
        btn = getattr(getattr(self, "_panel_toolbar", None), "_route_order_btn", None)
        if btn is not None and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)

    def _ensure_project_program_fields(self, project=None):
        project = project or self._project()
        if project is None:
            return None
        if not hasattr(project, "programs") or getattr(project, "programs", None) is None:
            project.programs = []
        if not hasattr(project, "current_program_id"):
            project.current_program_id = ""
        return project

    def _program_by_id(self, program_id: str, project=None):
        project = self._ensure_project_program_fields(project)
        if project is None or not program_id:
            return None
        if hasattr(project, "get_program"):
            try:
                return project.get_program(program_id)
            except Exception:
                pass
        for program in getattr(project, "programs", []) or []:
            if str(getattr(program, "id", "")) == str(program_id):
                return program
        return None

    def _active_program(self, project=None):
        project = self._ensure_project_program_fields(project)
        if project is None:
            return None
        return self._program_by_id(getattr(project, "current_program_id", "") or "", project)

    def _selected_program_id(self) -> str:
        lst = getattr(self, "_program_list", None)
        if lst is None:
            return ""
        item = lst.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _require_active_program(self, action: str) -> bool:
        if self._active_program() is not None:
            return True
        msg = f"{action}已禁止：请先在节目管理中创建或打开节目"
        if hasattr(self, "_status"):
            self._status.set_message(msg)
        return False

    def _refresh_program_list(self):
        project = self._ensure_project_program_fields()
        active_id = str(getattr(project, "current_program_id", "") or "") if project is not None else ""
        query = (getattr(self, "_program_search_text", "") or "").strip().lower()
        lst = getattr(self, "_program_list", None)
        if lst is not None:
            current_selection = self._selected_program_id() or active_id
            lst.blockSignals(True)
            lst.clear()
            for program in getattr(project, "programs", []) or []:
                pid = str(getattr(program, "id", "") or "")
                name = str(getattr(program, "name", "") or "未命名节目")
                # 搜索框为纯视图层过滤（C 条）：不匹配的节目只是不显示
                if query and query not in name.lower():
                    continue
                clip_count = 0
                for track in getattr(program, "tracks", []) or []:
                    clip_count += len(getattr(track, "clips", []) or [])
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, pid)
                # 24px 行解剖：名称 11px / 元数据 10px 右对齐（_ProgramRow），
                # 激活指示圆点替代旧文本前缀 ●
                row = _ProgramRow(name, f"{clip_count} clips", pid == active_id)
                item.setSizeHint(QSize(10, _ProgramRow.ROW_H))
                lst.addItem(item)
                lst.setItemWidget(item, row)
                if pid == current_selection:
                    lst.setCurrentItem(item)
            lst.blockSignals(False)
            self._sync_program_row_selection()
            # 空态切换：无节目 vs 无搜索匹配两种占位
            empty_none = getattr(self, "_program_empty_none", None)
            empty_match = getattr(self, "_program_empty_match", None)
            has_query = bool(query)
            if empty_none is not None:
                empty_none.setVisible(lst.count() == 0 and not has_query)
            if empty_match is not None:
                empty_match.setVisible(lst.count() == 0 and has_query)
        label = getattr(self, "_program_active_label", None)
        if label is not None:
            active = self._active_program(project)
            if active is None:
                label.setText("当前节目: 未打开")
            else:
                label.setText(f"当前节目: {getattr(active, 'name', '未命名节目')}")
        refresh_program_light_control(self)

    def _apply_timeline_program_gate(self):
        active = self._active_program() is not None
        tw = getattr(self, "_timeline_widget", None)
        if tw is not None:
            try:
                tw.setEnabled(active)
                tw.setToolTip("" if active else "请先在节目管理中创建或打开节目，才能编辑时间线。")
            except Exception:
                pass
        for attr in ("_track_list", "_clip_scroll", "_clip_container"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.setEnabled(active)
                except Exception:
                    pass
        # 空态横幅显隐：无节目时给出明确禁用观感（原仅置灰几乎不可见）
        # 判自身显隐标志 isHidden()：窗口 show() 前 isVisible() 恒 False，先 load 后 show 会漏掉隐藏
        banner = getattr(self, "_program_gate_hint", None)
        if banner is not None and banner.isHidden() != active:
            banner.setVisible(not active)
        # R178：节目激活态变化（工程载入/切换/关闭都经此）→ 确保自动烘焙器在场。
        _on_timeline_program_gate(self, bool(active))

    def _reset_timeline_head(self):
        try:
            tw = getattr(self, "_timeline_widget", None)
            if tw is not None and hasattr(tw, "ctrl") and hasattr(tw.ctrl, "seek"):
                tw.ctrl.seek(0.0)
            elif tw is not None and hasattr(tw, "seek"):
                tw.seek(0.0)
        except Exception:
            pass
        try:
            self._transport.update_position(0.0)
        except Exception:
            pass

    def _invalidate_timeline_program_cache(self):
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                if hasattr(engine, "invalidate_all_caches"):
                    engine.invalidate_all_caches()
                elif hasattr(engine, "unlock_timeline_render_cache"):
                    engine.unlock_timeline_render_cache()
                if hasattr(engine, "_playback_start_frame_rendered"):
                    engine._playback_start_frame_rendered = False
            except Exception:
                pass
        nm = getattr(self, "_nm", None)
        if nm is not None and hasattr(nm, "clear_layer"):
            for layer in ("clip_playback", "clip_preview", "timeline"):
                try:
                    nm.clear_layer(layer)
                except Exception:
                    pass

    def _save_current_timeline_to_program(self, update_timestamp: bool = True) -> bool:
        project = self._ensure_project_program_fields()
        program = None if project is None else self._active_program(project)
        if program is None:
            return False
        at = self._timeline_audio_track()
        if at is not None:
            project.audio_path = _prefer_live_audio_path(
                self._current_timeline_audio_path(), getattr(program, "audio_path", "") or "",
                self._resolve_project_audio_path, self._project_path)
            project.audio_clip_state = _safe_dict(at.audio_clip_state)
        _save_audio_mod_state(self, project)   # ★ R174#7（模块级 helper 压基线）
        program.audio_path = getattr(project, "audio_path", "") or ""
        program.audio_clip_state = _safe_dict(getattr(project, "audio_clip_state", {}))
        program.audio_modulation_state = copy.deepcopy(getattr(project, "audio_modulation_state", {}) or {})  # 清查⑦A P1-5
        # ★ R194/P1-5：外壳拷贝替代全量 deepcopy（十万帧 ~850ms → 毫秒级；
        #   帧对象共享契约见 _snapshot_track_shallow docstring）
        program.tracks = _snapshot_track_shallow(getattr(project, "tracks", []) or [])
        program.updated_at = time.time() if update_timestamp else program.updated_at
        return True

    def _restore_timeline_audio_for_current_sheet(self, project_path: str = "") -> str:
        project = self._project()
        if project is None:
            return ""
        if project_path:
            return self._restore_timeline_audio_for_project(project_path)
        stored_path = getattr(project, "audio_path", "") or ""
        if not stored_path:
            return _clear_audio_for_project_restore(self, project)
        audio_file = _resolve_audio_file_path(stored_path)
        if not audio_file.is_file():
            _clear_audio_for_project_restore(self, project)
            return f"（音频文件缺失: {stored_path}）"
        at = self._timeline_audio_track()
        if at is None:
            return ""
        try:
            ok = False
            if hasattr(at, "load_audio_file"):
                ok = bool(at.load_audio_file(str(audio_file)))
            elif hasattr(at, "_load_audio"):
                at._load_audio(str(audio_file))
                ok = True
            if ok:
                try:
                    at.set_audio_clip_state(getattr(project, "audio_clip_state", {}) or {})
                except Exception:
                    pass
                _restore_audio_mod_state(self, project)   # ★ R174#7
                return f"（已自动导入音频: {audio_file.name}）"
        except Exception:
            pass
        return "（音频自动导入失败）"

    def _load_program_to_timeline(self, program_id: str, save_current: bool = True) -> bool:
        project = self._ensure_project_program_fields()
        if project is None:
            return False
        program = self._program_by_id(program_id, project)
        if program is None:
            self._status.set_message("打开节目失败：未找到节目")
            return False
        same_program = str(project.current_program_id) == str(program_id)
        if save_current:
            self._save_current_timeline_to_program()
        project.current_program_id = str(getattr(program, "id", "") or "")
        project.tracks = _snapshot_track_shallow(getattr(program, "tracks", []) or [])  # ★ R194/P1-5 外壳拷贝（帧共享契约）
        _copy_program_audio_fields(program, project)   # 审计修 P0：含调制状态
        self._sync_timeline_project()
        _restore_audio_mod_state(self, project)
        self._refresh_tracks()
        audio_status = self._restore_timeline_audio_for_current_sheet(self._project_path or "")
        self._reset_timeline_head()
        from .dispatch.timeline_program_cache import finish_program_open
        finish_program_open(self, program, project, audio_status, same_program)
        return True

    def _close_current_program_to_empty(self, save_current: bool = True) -> bool:
        project = self._ensure_project_program_fields()
        if project is None:
            return False
        if save_current:
            self._save_current_timeline_to_program()
        project.current_program_id = ""
        project.tracks = []
        project.audio_path = ""
        project.audio_clip_state = {}
        self._clear_timeline_audio()
        self._sync_timeline_project()
        self._refresh_tracks()
        self._reset_timeline_head()
        self._invalidate_timeline_program_cache()
        self._refresh_program_list()
        return True

    def _unique_program_name(self, base: str, exclude_id: str = "") -> str:
        project = self._ensure_project_program_fields()
        names = {
            str(getattr(p, "name", "") or "")
            for p in (getattr(project, "programs", []) or [])
            if str(getattr(p, "id", "") or "") != str(exclude_id or "")
        }
        name = (base or "节目").strip() or "节目"
        if name not in names:
            return name
        idx = 2
        while f"{name} {idx}" in names:
            idx += 1
        return f"{name} {idx}"

    def _new_program(self):
        project = self._ensure_project_program_fields()
        if project is None:
            return
        default_name = self._unique_program_name(f"节目 {len(getattr(project, 'programs', []) or []) + 1}")
        name, ok = QInputDialog.getText(self, "新建节目", "节目名称：", text=default_name)
        if not ok:
            return
        name = self._unique_program_name(str(name).strip() or default_name)
        self._save_current_timeline_to_program()
        from ..core.models import TimelineProgram
        program = TimelineProgram(name=name, tracks=[], audio_path="")
        project.programs.append(program)
        self._load_program_to_timeline(program.id, save_current=False)
        self.mark_dirty()
        self._status.set_message(f"已新建并打开节目: {name}")

    def _open_selected_program(self):
        pid = self._selected_program_id()
        if not pid:
            self._status.set_message("请先选择一个节目")
            return
        self._load_program_to_timeline(pid, save_current=True)

    def _save_current_program_clicked(self):
        if not self._require_active_program("保存当前节目"):
            return
        if self._save_current_timeline_to_program():
            self.mark_dirty()
            active = self._active_program()
            self._refresh_program_list()
            self._status.set_message(f"已保存当前节目: {getattr(active, 'name', '未命名节目')}")

    def _close_current_program_clicked(self):
        if self._active_program() is None:
            self._status.set_message("当前没有打开的节目")
            return
        self._close_current_program_to_empty(save_current=True)
        self._status.set_message("已关闭节目，时间线已清空")

    def _rename_selected_program(self):
        project = self._ensure_project_program_fields()
        pid = self._selected_program_id() or str(getattr(project, "current_program_id", "") or "")
        program = self._program_by_id(pid, project)
        if program is None:
            self._status.set_message("请先选择一个节目")
            return
        name, ok = QInputDialog.getText(
            self, "重命名节目", "节目名称：",
            text=str(getattr(program, "name", "") or "未命名节目"))
        if not ok:
            return
        program.name = self._unique_program_name(str(name).strip() or "未命名节目", exclude_id=pid)
        program.updated_at = time.time()
        self.mark_dirty()
        self._refresh_program_list()

    def _delete_selected_program(self):
        project = self._ensure_project_program_fields()
        pid = self._selected_program_id()
        program = self._program_by_id(pid, project)
        if program is None:
            self._status.set_message("请先选择一个节目")
            return
        name = str(getattr(program, "name", "") or "未命名节目")
        reply = QMessageBox.question(
            self, "删除节目",
            f"确定删除节目「{name}」？\n该节目的时间线和歌曲引用会从项目中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        is_active = str(getattr(project, "current_program_id", "") or "") == pid
        project.programs = [p for p in (project.programs or []) if str(getattr(p, "id", "")) != pid]
        if is_active:
            self._close_current_program_to_empty(save_current=False)
        self.mark_dirty()
        self._refresh_program_list()
        self._status.set_message(f"已删除节目: {name}")

    def _audio_path_for_export(self, audio_path: str) -> Optional[Path]:
        if not audio_path:
            return None
        try:
            if self._project_path:
                p = self._resolve_project_audio_path(self._project_path, audio_path)
            else:
                p = Path(os.path.expanduser(os.fspath(audio_path)))
                if not p.is_absolute():
                    p = Path.cwd() / p
            return p if p.is_file() else None
        except Exception:
            return None

    def _export_selected_program(self):
        project = self._ensure_project_program_fields()
        pid = self._selected_program_id() or str(getattr(project, "current_program_id", "") or "")
        program = self._program_by_id(pid, project)
        if program is None:
            self._status.set_message("请先选择一个节目")
            return
        if pid == str(getattr(project, "current_program_id", "") or ""):
            self._save_current_timeline_to_program()
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", str(getattr(program, "name", "") or "program"))
        path, _ = QFileDialog.getSaveFileName(
            self, "导出节目", f"{safe_name}.dmxp",
            "雾白纪 ULTRA 节目 (*.dmxp);;JSON 节目 (*.json)")
        if not path:
            return
        if not (path.endswith(".dmxp") or path.endswith(".json")):
            path += ".dmxp"
        payload = {
            "version": 1,
            "type": "qascade.timeline_program",
            "program": program.to_dict(),
        }
        try:
            if path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            else:
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    audio_src = self._audio_path_for_export(getattr(program, "audio_path", "") or "")
                    program_dict = dict(payload["program"])  # ★ R194/P1-5：已序列化 dict，浅拷贝足够
                    if audio_src is not None:
                        arc = f"audio/{audio_src.name}"
                        zf.write(str(audio_src), arc)
                        program_dict["audio_path"] = arc
                    payload["program"] = program_dict
                    zf.writestr("program.json", json.dumps(payload, indent=2, ensure_ascii=False))
            self._status.set_message(f"已导出节目: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出节目失败", str(exc))

    def _import_program(self):
        project = self._ensure_project_program_fields()
        if project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入节目", "",
            "雾白纪 节目 (*.dmxp *.json);;所有文件 (*)")
        if not path:
            return
        try:
            payload = None
            audio_member = ""
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path, "r") as zf:
                    payload = json.loads(zf.read("program.json").decode("utf-8"))
                    prog_audio = str((payload.get("program") or {}).get("audio_path", "") or "")
                    names = set(zf.namelist())
                    if prog_audio in names:
                        audio_member = prog_audio
                    elif not audio_member:
                        for name in zf.namelist():
                            if name.startswith("audio/") and not name.endswith("/"):
                                audio_member = name
                                break
                    from ..core.models import TimelineProgram
                    program = TimelineProgram.from_dict(payload.get("program", {}) or {})
                    if audio_member:
                        dest_dir = self._program_import_audio_dir()
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        dest = self._unique_file_path(dest_dir / Path(audio_member).name)
                        with zf.open(audio_member) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if self._project_path:
                            try:
                                program.audio_path = os.path.relpath(
                                    str(dest), start=str(Path(self._project_path).expanduser().parent)
                                ).replace(os.sep, "/")
                            except ValueError:
                                program.audio_path = str(dest)
                        else:
                            program.audio_path = str(dest)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                from ..core.models import TimelineProgram
                program = TimelineProgram.from_dict(payload.get("program", payload) or {})
            existing_ids = {str(getattr(p, "id", "") or "") for p in (project.programs or [])}
            if str(getattr(program, "id", "") or "") in existing_ids:
                program.id = str(uuid.uuid4())
            program.name = self._unique_program_name(str(getattr(program, "name", "") or "导入节目"))
            program.updated_at = time.time()
            project.programs.append(program)
            self.mark_dirty()
            self._refresh_program_list()
            self._status.set_message(f"已导入节目: {program.name}")
        except Exception as exc:
            QMessageBox.critical(self, "导入节目失败", str(exc))

    def _program_import_audio_dir(self) -> Path:
        if self._project_path:
            project_file = Path(self._project_path).expanduser()
            return project_file.parent / f"{project_file.stem}_assets" / "audio" / "imported"
        return Path.cwd() / "Qascade_imported_audio"

    def _unique_file_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        idx = 2
        while True:
            candidate = parent / f"{stem}_{idx}{suffix}"
            if not candidate.exists():
                return candidate
            idx += 1

    def _bundled_audio_file_name(self, src: Path) -> str:
        try:
            st = src.stat()
            fingerprint = f"{src.name}|{st.st_size}|{getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000))}"
        except Exception:
            fingerprint = os.fspath(src)
        digest = hashlib.sha1(fingerprint.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        suffix = src.suffix if src.suffix else ""
        return f"audio_{digest}{suffix}"

    def _toggle_dock(self, dock: QDockWidget, show: bool):
        if show:
            self._lazy_load_panels(dock)
            # R54-A：无用户布局意图的子页面打开即悬浮（节目管理/TopView/
            # 关联器除外）；用户拖入过主界面的 dock 不再干预。
            self._maybe_float_default(dock)
            dock.show()
            dock.raise_()
            # 落位兜底：新开 dock 若被中央区挤到下限以下（阅读型面板会变竖条），
            # 抬到 _DOCK_MIN_WIDTHS 的可读宽度。每 dock 至多一次，不进 resize 循环。
            self._enforce_dock_min_width(dock)
            # 配接面板单例缓存，重开只切可见性；显式刷新避免显示陈旧/空白网格。
            if dock is getattr(self, "_patch_dock", None) and self._patch_widget is not None:
                try:
                    self._patch_widget.refresh()
                except Exception:
                    pass
        else:
            dock.hide()

    def _on_dock_visibility(self, btn_attr: str, visible: bool):
        btn = getattr(self._panel_toolbar, btn_attr, None)
        if btn is not None and not sip.isdeleted(btn):
            btn.blockSignals(True)
            btn.setChecked(visible)
            btn.blockSignals(False)
        # 零基础模式是「随界面浏览的标注层」：面板开合会改变可见锚点集合，
        # 通知巡览重算队列（停在同一功能上），标注层始终贴合当前界面。
        tour = getattr(self, "_beginner_tour", None)
        if tour is not None and getattr(tour, "active", False):
            import contextlib
            with contextlib.suppress(Exception):
                tour.refresh()      # 界面变了 → 重铺全部注释（仍是"全铺开"）
        # 隐藏加载语义（2026-09-17 用户定案）：隐藏≠释放，见 _on_dock_shown。
        if visible and btn_attr == "_programmer_btn":
            _resume_programmer_keepalive(getattr(self, "_programmer_widget", None))

    # ══════════════════════════════════════════════════════════════════════════
    #  ★ v3: 辅助方法 — 连接音频 && 注册 dmx_out 回调
    # ══════════════════════════════════════════════════════════════════════════

    def _connect_audio_to_programmer(self, programmer_panel):
        pass

    def _on_generalized_cue_to_programmer(self, cue):
        """把自映射面板产生的广义 Cue 加入 Programmer 的 Cue 列表。"""
        try:
            self._lazy_load_panels(self._programmer_dock)
            if self._programmer_widget is not None and hasattr(self._programmer_widget, "add_generalized_cue"):
                self._programmer_widget.add_generalized_cue(cue)
                if hasattr(self, "_status"):
                    self._status.set_message(f"已发送广义 Cue 到 Programmer: {getattr(cue, 'name', 'Cue')}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("send generalized cue to programmer failed: %s", e)

    def _connect_programmer_dmx_out(self, pw):
        # ★ 修复：如果 _nm 是桩对象，替换为真正的 NetworkManager 实例
        if self._nm is not None and not hasattr(self._nm, 'send_raw'):
            from ..network.network_manager import NetworkManager
            self._nm = NetworkManager.instance()

        # ★ v22 修复：移除 dmx_out → send_raw 直写硬件路径
        # Programmer 现在统一通过 update_layer 写入 NM 各层，
        # 由 NM._mix_and_send() 25ms 混音器统一输出到硬件，
        # 消除双写冲突导致的信号抖动。
        # OutputSmoother 已集成到 NM._mix_and_send() 输出端。

        # dmx_out 保留供外部监听者使用（如 RecordEngine）
        # 但不再连接到 send_raw

    @staticmethod
    def _extract_track_linker_state(processor_state):
        if not isinstance(processor_state, dict):
            return {}
        state = processor_state.get("track_linker") if "track_linker" in processor_state else processor_state
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _wrap_track_linker_state(linker_state):
        state = linker_state if isinstance(linker_state, dict) else {}
        return {"version": 2, "track_linker": state}

    def _project_track_linker_state(self, project=None) -> dict:
        project = project or getattr(getattr(self, "_pm", None), "project", None)
        if project is None:
            return {}
        raw = getattr(project, "processor_state", None) or getattr(project, "linker_state", None) or {}
        return self._extract_track_linker_state(raw)

    def _load_track_linker_state_to_engine(self, state: dict) -> bool:
        linker_state = self._extract_track_linker_state(state)
        if not linker_state:
            return stop_window_linker(self, clear_state=True)
        try:
            from ..processing.track_linker_engine import get_linker_engine
        except Exception:
            try:
                from src.processing.track_linker_engine import get_linker_engine
            except Exception:
                return False
        try:
            engine = get_linker_engine()
            if self._nm is not None and hasattr(engine, "set_network_manager"):
                engine.set_network_manager(self._nm)
            if hasattr(engine, "from_dict"):
                engine.from_dict(linker_state)
                return True
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "TrackLinkerEngine state restore failed", exc_info=True)
        return False

    def _save_track_linker_state_from_engine(self) -> dict:
        try:
            from ..processing.track_linker_engine import get_linker_engine
        except Exception:
            try:
                from src.processing.track_linker_engine import get_linker_engine
            except Exception:
                return {}
        try:
            engine = get_linker_engine()
            if self._nm is not None and hasattr(engine, "set_network_manager"):
                engine.set_network_manager(self._nm)
            if hasattr(engine, "to_dict"):
                return engine.to_dict()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "TrackLinkerEngine state save failed", exc_info=True)
        return {}

    # ══════════════════════════════════════════════════════════════════════════
    #  Lazy Load Panels
    # ══════════════════════════════════════════════════════════════════════════

    def _lazy_load_panels(self, dock=None):
        load_all = dock is None

        def _want(target) -> bool:
            return load_all or dock is target

        def _connect_once(signal, slot):
            try:
                signal.disconnect(slot)
            except Exception:
                pass
            try:
                signal.connect(slot)
            except Exception:
                pass

        if _want(self._monitor_dock) and self._monitor_widget is None:
            try:
                from .monitor.monitor_panel import MonitorPanel
                w = MonitorPanel(self._nm, self._pm)
                self._monitor_widget = w
                self._install_dock_widget(self._monitor_dock, w)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "MonitorPanel lazy load failed: %s", e)

        if _want(self._processor_dock) and self._processor_widget is None:
            try:
                # 关联器直挂独立 dock（报废的 ProcessorTabsPanel 壳已弃用）
                from .processor_panel.track_linker_panel import TrackLinkerPanel
                from .processor_panel.processor_tabs_panel import _DockScrollArea
                from PyQt6.QtWidgets import QAbstractScrollArea, QScrollArea, QSizePolicy
                w = TrackLinkerPanel(self._pm, nm=self._nm)
                w.setMinimumSize(0, 0)
                w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                # 滚动视口：阻断关联器内部大 sizeHint 把底部 Dock 顶宽/顶高
                page = _DockScrollArea()
                page.setWidgetResizable(True)
                page.setFrameShape(QScrollArea.Shape.NoFrame)
                page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                page.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
                page.setMinimumSize(0, 0)
                page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
                page.setWidget(w)
                self._processor_widget = w
                self._install_dock_widget(self._processor_dock, page)
                # 如果 NM 尚未注入，延迟注入
                if self._nm is not None and hasattr(w, 'set_network_manager'):
                    w.set_network_manager(self._nm)
                # 兼容旧的 process_done 信号
                if hasattr(w, "process_done"):
                    w.process_done.connect(self._on_process_done)
                linker_state = self._project_track_linker_state()
                if linker_state and hasattr(w, "load_state"):
                    w.load_state(linker_state)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "TrackLinkerPanel lazy load failed: %s", e)

        if _want(self._network_dock) and self._network_widget is None:
            try:
                from .network_panel.network_config import NetworkConfigPanel
                w = NetworkConfigPanel(self._nm)
                self._network_widget = w
                w._installed_in_window = True                      # ★ R193/HF-01a：隐藏轮询守卫生效位
                if getattr(w, "_discovery_panel", None) is not None:
                    w._discovery_panel._installed_in_window = True  # ★ R193/HF-01b
                self._install_dock_widget(self._network_dock, w)
                _apply_network_config_to_panel(self)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "NetworkConfigPanel lazy load failed: %s", e)

        if _want(self._patch_dock) and self._patch_widget is None:
            try:
                from .patch_panel import PatchPanel
                w = PatchPanel(self._pm.project, project_manager=self._pm)
                self._patch_widget = w
                self._install_dock_widget(self._patch_dock, w)
                _patch_signal = getattr(w, 'patch_changed', None) or getattr(w, 'fixtures_changed', None)
                if _patch_signal is not None:
                    _patch_signal.connect(self._on_patch_changed)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "PatchPanel lazy load failed: %s", e)

        if _want(self._topview_dock) and self._topview_widget is None:
            try:
                from .topview_panel import TopViewPanel
                w = TopViewPanel(self._pm)
                if self._programmer_widget is not None:
                    self._programmer_widget._topview_ref = w
                    if hasattr(self._programmer_widget, "fixture_selection_changed"):
                        _connect_once(
                            self._programmer_widget.fixture_selection_changed,
                            self._sync_fixture_selection_to_topview)
                    w.layout_changed.connect(
                        lambda _tv=w, _pw=self._programmer_widget:
                            _pw.update_fixture_positions(_tv.get_fixture_positions()))
                    if hasattr(w, "selection_changed"):
                        w.selection_changed.connect(
                            lambda ids, _pw=self._programmer_widget:
                                self._load_programmer_from_topview_selection(ids, _pw))
                    if not self._sync_programmer_from_current_topview_selection(
                            self._programmer_widget, w):
                        if hasattr(self._programmer_widget, "get_live_selected_fixture_ids"):
                            self._sync_initial_fixture_selection_to_topview(
                                self._programmer_widget, w)
                if hasattr(w, "layout_changed"):
                    # ★★ 深查修正（关窗口不提示、布局改动丢失）：原 lambda 只把状态写进
                    #   工程、**从不置脏** → 用户调完 TopView 布局关窗口既无提示也丢改动。
                    #   改走方法，写入后一并标脏。
                    w.layout_changed.connect(lambda _w=w: self._on_topview_layout_changed(_w))
                if self._matrix_pattern_widget is not None:
                    # ★ 同步注入 TopView 引用（用于实时位置与实时选择同步）
                    self._matrix_pattern_widget._topview_ref = w
                    # ★ 修复：TopView layout_changed 改用 get_all_fixture_positions，
                    # 这样即使设备未在当前 Sheet 放置也能被 Matrix 识别
                    # ★ R182 收口：默认参数绑定 _tv=w（修前裸闭包 w 被后段
                    #   FixtureGroupPanel 复用覆盖 → 触发时 AttributeError）
                    w.layout_changed.connect(
                        lambda _tv=w, _mp=self._matrix_pattern_widget: (
                            _mp.set_fixtures(
                                _tv.get_all_fixture_positions()
                                if hasattr(_tv, 'get_all_fixture_positions')
                                else _tv.get_fixture_positions())
                            ) if _mp else None)
                    # ★ TopView 刚加载时立即推送一次全量位置给 Matrix
                    try:
                        all_pos = (w.get_all_fixture_positions()
                                   if hasattr(w, 'get_all_fixture_positions')
                                   else w.get_fixture_positions())
                        if all_pos:
                            self._matrix_pattern_widget.set_fixtures(all_pos)
                    except Exception:
                        pass
                    if hasattr(w, "selection_changed"):
                        w.selection_changed.connect(
                            lambda ids, _mp=self._matrix_pattern_widget, _tv=w: (
                                _mp.load_fixture_group(
                                    "__topview_live__", ids, "TopView实时选择", self._pm,
                                    _tv.get_all_fixture_positions()
                                    if hasattr(_tv, "get_all_fixture_positions")
                                    else _tv.get_fixture_positions())
                                if hasattr(_mp, "load_fixture_group") else None))
                if hasattr(w, "selection_changed"):
                    w.selection_changed.connect(self._sync_effect_browser_topview_selection)
                    self._sync_effect_browser_topview_selection(
                        w._ordered_selected_ids()
                        if hasattr(w, "_ordered_selected_ids") else None)
                if hasattr(w, "fixture_group_saved"):
                    w.fixture_group_saved.connect(self._on_topview_fixture_group_saved)
                if hasattr(w, "xy_reference_changed"):
                    w.xy_reference_changed.connect(lambda _state=None: self._sync_xy_reference())
                self._topview_widget = w
                if self._route_order_widget is not None:
                    try:
                        self._route_order_widget.set_context(
                            project_manager=self._pm,
                            topview=w,
                            fixture_group_panel=self._fixture_group_widget,
                        )
                    except Exception:
                        pass
                if self._fixture_group_widget is not None:
                    try:
                        fg = self._fixture_group_widget
                        if hasattr(fg, "fixture_selection_changed"):
                            _connect_once(
                                fg.fixture_selection_changed,
                                self._sync_fixture_selection_to_topview)
                            if hasattr(fg, "get_live_selected_fixture_ids"):
                                self._sync_initial_fixture_selection_to_topview(fg, w)
                    except Exception:
                        pass
                self._sync_xy_reference()
                for _cb in getattr(self, '_on_topview_ready_cbs', []):
                    try:
                        _cb(w)
                    except Exception:
                        pass
                self._install_dock_widget(self._topview_dock, w)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "TopViewPanel lazy load failed: %s", e)

        # ── ★ (deprecated) RouteOrderPanel — replaced by RouteAnimatorWindow ★
        # The old dock object is kept as a silent placeholder; no widget is
        # created inside it.  The new RouteAnimatorWindow is opened from the
        # 面板 > 跑动编排… menu action.
        self._route_order_widget = None

        if _want(self._programmer_dock) and self._programmer_widget is None:
            try:
                from .programmer_panel import ProgrammerPanel
                pw = ProgrammerPanel(self._pm)
                self._programmer_widget = pw
                # ★ R173: 连接 Programmer 状态反馈到状态栏
                pw.status_feedback.connect(self._status.set_programmer_feedback)
                self._install_dock_widget(self._programmer_dock, pw)

                # 注入 NetworkManager 引用
                pw._nm_ref = self._nm
                # 注入 PlaybackEngine 引用（轨迹曲线复用其 per_shape/global 分段与方向定位）
                try:
                    pw._engine_ref = getattr(self, "_engine", None)
                except Exception:
                    pass
                if self._effect_browser_widget is not None and hasattr(
                        self._effect_browser_widget, "set_context"):
                    try:
                        self._effect_browser_widget.set_context(
                            project=self._pm,
                            nm=self._nm,
                            topview=self._topview_widget,
                            fixture_group_panel=self._fixture_group_widget,
                            programmer_panel=pw,
                        )
                    except Exception:
                        pass

                # ★ v3: 连接音频驱动
                self._connect_audio_to_programmer(pw)
                # 外接实体摇杆不再在这里固定直连 Programmer；
                # 统一交给全局外接设备绑定管理器按"页面/功能/轴"关系分发，避免绕过用户手动绑定。

                # ★ v3: 连接 dmx_out → NetworkManager + RecordEngine
                self._connect_programmer_dmx_out(pw)
                if hasattr(pw, "fixture_selection_changed"):
                    _connect_once(
                        pw.fixture_selection_changed,
                        self._sync_fixture_selection_to_topview)

                topview_synced_to_programmer = False
                if self._topview_widget is not None:
                    pw._topview_ref = self._topview_widget
                    self._topview_widget.layout_changed.connect(
                        lambda _pw=pw: _pw.update_fixture_positions(
                            self._topview_widget.get_fixture_positions()))
                    if hasattr(self._topview_widget, "selection_changed"):
                        self._topview_widget.selection_changed.connect(
                            lambda ids, _pw=pw:
                                self._load_programmer_from_topview_selection(ids, _pw))
                    topview_synced_to_programmer = self._sync_programmer_from_current_topview_selection(
                        pw, self._topview_widget)

                if self._fixture_group_widget is not None:
                    try:
                        fg = self._fixture_group_widget
                        _connect_once(
                            fg.group_selected,
                            lambda gid, fids, gname, _pw=pw, _fg=fg: _pw.load_fixture_group(
                                gid, fids, gname,
                                base_order_ids=list(
                                    getattr(_fg._get_group(gid), 'base_order_fixture_ids', []) or fids
                                ) if hasattr(_fg, '_get_group') else list(fids)
                            )
                        )
                        _connect_once(fg.all_selected, pw.load_all_fixtures)
                        if hasattr(pw, 'sync_groups_from_panel'):
                            pw.sync_groups_from_panel(fg)

                        # ★ 修复：Programmer 懒加载时，FixtureGroup 当前选择信号可能
                        # 已经发出过一次；这里主动拉取当前选择，使首次打开 Programmer
                        # 也能立即显示当前设备组，而不必用户再次点击设备组按钮。
                        if not topview_synced_to_programmer:
                            if hasattr(fg, 'get_selected_fixture_ids'):
                                cur_ids = fg.get_selected_fixture_ids()
                            else:
                                cur_ids = []
                            cur_gid = getattr(fg, '_sel_group_id', None)
                            if cur_gid is None:
                                pw.load_all_fixtures(cur_ids)
                            else:
                                group = fg._get_group(cur_gid) if hasattr(fg, '_get_group') else None
                                cur_name = getattr(group, 'name', '设备组')
                                base_order = list(getattr(group, 'base_order_fixture_ids', []) or cur_ids) if group is not None else list(cur_ids)
                                pw.load_fixture_group(cur_gid, cur_ids, cur_name, base_order_ids=base_order)
                    except Exception:
                        pass

                if self._matrix_pattern_widget is not None:
                    pw._matrix_panel = self._matrix_pattern_widget
                    self._matrix_pattern_widget.pattern_updated.connect(
                        pw._on_matrix_pattern_updated)

                if self._timeline_widget is not None and hasattr(self._timeline_widget, 'ctrl'):
                    pw._timeline_ctrl_ref = self._timeline_widget.ctrl
                    self._connect_programmer_timeline_signals(pw)
                    at = getattr(self._timeline_widget, '_audio_track', None)
                    if at and getattr(at, '_audio_path', None):
                        pw.link_timeline_audio(at._audio_path)
                    if at:
                        self._managed_connect(
                            "timeline:programmer:audio_loaded",
                            at.audio_loaded,
                            lambda path, _dur, _pw=pw: _pw.link_timeline_audio(path),
                        )

                # ★ v3: 执行所有预先注册的回调
                for _cb in getattr(self, '_on_programmer_ready_cbs', []):
                    try:
                        _cb(pw)
                    except Exception:
                        pass
                self._on_programmer_ready_cbs.clear()

            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "ProgrammerPanel lazy load failed: %s", e, exc_info=True)

        # ── MatrixPatternPanel ──────────────────────────────────────────────
        if _want(self._matrix_pattern_dock) and self._matrix_pattern_widget is None:
            try:
                from .matrix_slideshow.matrix_slideshow_panel import MatrixSlideshowPanel
                w = MatrixSlideshowPanel(self._pm)
                self._matrix_pattern_widget = w
                self._install_dock_widget(self._matrix_pattern_dock, w)
                # 注入 NetworkManager
                w.set_nm(self._nm)
                # 兼容旧接口：pattern_updated 信号
                if self._programmer_widget is not None:
                    self._programmer_widget._matrix_panel = w
                    w.pattern_updated.connect(
                        self._programmer_widget._on_matrix_pattern_updated)
                # 设备组选择信号
                if self._fixture_group_widget is not None:
                    # ★ 修复：group_selected/all_selected 改用 get_all_fixture_positions
                    def _tv_all_pos():
                        tv = self._topview_widget
                        if tv is None:
                            return []
                        if hasattr(tv, 'get_all_fixture_positions'):
                            return tv.get_all_fixture_positions()
                        return tv.get_fixture_positions()
                    self._fixture_group_widget.group_selected.connect(
                        lambda gid, fids, gname, _w=w: _w.load_fixture_group(
                            gid, fids, gname, self._pm, _tv_all_pos()))
                    self._fixture_group_widget.all_selected.connect(
                        lambda fids, _w=w: _w.load_fixture_group(
                            '__all__', fids, '全部设备', self._pm, _tv_all_pos()))
                    if hasattr(w, 'refresh_filter_group_combo'):
                        w.refresh_filter_group_combo()
                    # ★ 立即同步 fixture_group_widget 当前的选择（之前那次
                    # group_selected/all_selected 信号已经发过、Matrix 没在
                    # 听，得主动拉一次）
                    try:
                        cur_ids = self._fixture_group_widget.get_selected_fixture_ids()
                        fg = self._fixture_group_widget
                        gid = getattr(fg, '_sel_group_id', None)
                        if cur_ids or gid:
                            group = fg._get_group(gid) if gid else None
                            w.load_fixture_group(gid or '__all__', cur_ids,
                                getattr(group, 'name', '全部设备'), self._pm, _tv_all_pos())
                    except Exception:
                        pass
                # TopView 布局变化信号
                if self._topview_widget is not None:
                    # ★ 修复：改用 get_all_fixture_positions 跨所有 Sheet
                    self._topview_widget.layout_changed.connect(
                        lambda _w=w: _w.refresh_from_topview(
                            self._topview_widget.get_all_fixture_positions()
                            if hasattr(self._topview_widget, 'get_all_fixture_positions')
                            else self._topview_widget.get_fixture_positions()))
                    try:
                        all_pos = (self._topview_widget.get_all_fixture_positions()
                                   if hasattr(self._topview_widget, 'get_all_fixture_positions')
                                   else self._topview_widget.get_fixture_positions())
                        w.set_fixtures(all_pos)
                    except Exception:
                        pass
                    if hasattr(self._topview_widget, "selection_changed"):
                        self._topview_widget.selection_changed.connect(
                            lambda ids, _w=w: _matrix_load_from_topview(
                                self, _w, ids))
                # ★ 2026-09-14 用户："其它页面选中的设备应该也先传给 topview 再传给
                #   调度台。比如 matrix 的选中设备的列表…" → 把矩阵的设备选中接入
                #   TopView 统一漏斗（与 Programmer/设备组同一条路）：
                #   矩阵选中 → fixture_selection_changed → _sync_fixture_selection_to_topview
                #   → TopView（emit=False）→ 调度台监控区按其 provider 自然更新。
                if hasattr(w, "fixture_selection_changed"):
                    _connect_once(
                        w.fixture_selection_changed,
                        self._sync_fixture_selection_to_topview)
                    if hasattr(w, "get_live_selected_fixture_ids"):
                        self._sync_initial_fixture_selection_to_topview(w)
                # ★ 注入 TopView 引用，用于实时位置/选择同步
                if self._topview_widget is not None:
                    w._topview_ref = self._topview_widget

                # ★ TopView 还没懒加载也无所谓——MatrixSlideshowPanel.__init__
                # 已经从 PM 兖底拉过 fixtures（_bootstrap_from_pm），打开就有内容
                # 时间线信号（兼容旧接口）
                if self._timeline_widget is not None and hasattr(self._timeline_widget, 'ctrl'):
                    ctrl = self._timeline_widget.ctrl
                    w._timeline_ctrl_ref = ctrl
                    self._connect_matrix_timeline_signals(w)

                # ★ 时间线音频参考轨信号 → matrix 像素引擎音频源
                # 让音频 FFT / 波形 / 能量效果读取时间线导入的音频文件
                # 而非尝试打开麦克风/音频设备（可能不存在）
                try:
                    _tw = self._timeline_widget
                    _at = getattr(_tw, '_audio_track', None) if _tw else None
                    if _at is not None:
                        self._managed_connect(
                            "timeline:matrix:audio_loaded",
                            _at.audio_loaded,
                            lambda path, dur, _w=w: _w.link_timeline_audio(path, dur),
                        )
                        self._managed_connect(
                            "timeline:matrix:audio_removed",
                            _at.audio_removed,
                            lambda _w=w: _w.on_timeline_stop(),
                        )
                        # 立即同步：若时间线已有音频，马上注入
                        _existing = getattr(_at, '_audio_path', None)
                        if _existing:
                            w.link_timeline_audio(_existing)
                except Exception:
                    pass
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "MatrixSlideshowPanel lazy load failed: %s", e, exc_info=True)

        # ── EffectBrowserWidget ─────────────────────────────────────────────
        if (_want(self._effect_browser_dock)
                and self._effect_browser_widget is None):
            try:
                from .effect_browser import EffectBrowserWidget
                w = EffectBrowserWidget(parent=self)
                self._effect_browser_widget = w
                self._install_dock_widget(self._effect_browser_dock, w)
                w.set_context(
                    project=self._pm,
                    nm=self._nm,
                    topview=self._topview_widget,
                    fixture_group_panel=self._fixture_group_widget,
                    programmer_panel=self._programmer_widget,
                )
                w.load_state(getattr(getattr(self._pm, "project", None), "effect_browser_state", None))
                def _inject_topview(tv=None, _w=w):
                    _w.set_context(
                        project=self._pm,
                        nm=self._nm,
                        topview=self._topview_widget,
                        fixture_group_panel=self._fixture_group_widget,
                        programmer_panel=self._programmer_widget,
                    )
                self._on_topview_ready_cbs = getattr(self, '_on_topview_ready_cbs', [])
                self._on_topview_ready_cbs.append(_inject_topview)
                if self._topview_widget is not None and hasattr(self._topview_widget, "selection_changed"):
                    self._topview_widget.selection_changed.connect(self._sync_effect_browser_topview_selection)
                    self._sync_effect_browser_topview_selection()
                w.effect_applied.connect(self._on_effect_applied)
                w.send_to_timeline.connect(self._on_effect_send_to_timeline)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "EffectBrowserWidget lazy load failed: %s", e)
            finally:
                # 轨迹 hub 补挂（幂等）：面板可能晚于 record engine 构建
                hub = getattr(self, "_traj_hub", None)
                w = getattr(self, "_effect_browser_widget", None)
                if hub is not None and w is not None:
                    hub.attach_panel(w)

        # ── Clip 附加处理 Studio（R172 W1d2：五闭包注入 → 模块级装配合）──────
        if _want(self._clip_studio_dock) and self._clip_studio_widget is None:
            try:
                w = _lazy_install_clip_studio(self)
                if w is not None:
                    self._clip_studio_widget = w
                    self._install_dock_widget(self._clip_studio_dock, w)
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning('ClipStudioPanel load failed: %s', _e)

        # ── 教程与说明书（内置 WebEngine 浏览器 + 超链接模式）─────────────────
        if _want(self._tutorial_dock) and self._tutorial_widget is None:
            try:
                from .tutorial_panel import TutorialPanel
                w = TutorialPanel(self)
                self._tutorial_widget = w
                self._install_dock_widget(self._tutorial_dock, w)
                w.featureTriggered.connect(self._on_tutorial_feature)
                w.openExternallyRequested.connect(self._open_url_external)
                w.beginnerTourRequested.connect(self._start_beginner_tour)
                w.beginnerTourStopRequested.connect(self._stop_beginner_tour)
                w.chapterSeen.connect(self._on_tutorial_chapter_seen)
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning('TutorialPanel load failed: %s', _e)

        # ── 调度台（效果制作总线监控 + 可追溯效果缓存）────────────────────────
        if _want(self._dispatch_dock) and self._dispatch_widget is None:
            try:
                from .dispatch import DispatchPanel
                w = DispatchPanel(self)
                self._dispatch_widget = w
                self._install_dock_widget(self._dispatch_dock, w)
                # 依赖注入：MainWindow 是路由层 —— 面板之间零耦合。
                # ★ 表格视图需要"设备表 / 设备定义 / NetworkManager / 按 id 取面板"。
                #   这里用**局部闭包**复用既有属性与方法，不往本上帝类加新方法
                #   （CLAUDE.md 10.1：五个上帝类禁止新增独立职责）。
                def _proj_attr(name, default):
                    try:
                        return getattr(getattr(self._pm, "project", None), name, default) or default
                    except Exception:
                        return default

                def _fx_provider():
                    return list(_proj_attr("fixtures", []))

                def _defs_provider():
                    return {getattr(d, "id", ""): d for d in _proj_attr("fixture_defs", [])}

                def _panel_by_id(pid):
                    try:
                        return (self._dispatch_panel_targets().get(pid) or {}).get("widget")
                    except Exception:
                        return None

                def _focus_panel(payload):
                    """双击调度台格 → 打开目标面板 + **还原该效果编辑状态**。

                    ★ 2026-09-14 用户要求："唤起它的效果来源的页面并且**回到这个效果
                      制作的状态**，programmer 需完全还原翼块/条带/值，effect 要切到
                      该效果的调出页并还原每个控件，matrix 同理。"
                    实现集中在 `dispatch/cell_recall.handle_focus_target`（保持本帧内
                    闭包足够小，架构门禁要求函数 ≤100 行 / CC ≤8）。
                    """
                    try:
                        from .dispatch.cell_recall import handle_focus_target
                        return bool(handle_focus_target(self, payload))
                    except Exception:
                        import logging
                        logging.getLogger(__name__).debug(
                            "dispatch focus panel failed", exc_info=True)
                        return False

                w.set_services(capture_state=self._dispatch_capture_state,
                               recall_nodes=self._dispatch_recall_nodes,
                               release_nodes=self._dispatch_release_nodes,
                               clip_report=self._dispatch_clip_report,
                               groups_provider=self._dispatch_fixture_groups,
                               fixtures_provider=_fx_provider,
                               defs_provider=_defs_provider,
                               nm_provider=lambda: self._nm,
                               panel_provider=_panel_by_id,
                               focus_panel=_focus_panel,
                               selected_ids_provider=self._topview_explicit_selection_ids)
                w.focusRequested.connect(_focus_panel)
                w.cacheChanged.connect(self._on_dispatch_cache_changed)
                # 首次构建即从项目载入缓存（面板可能晚于项目打开才建）
                try:
                    proj = getattr(getattr(self, "_pm", None), "project", None)
                    from ..processing.dispatch import (get_effect_cache_store,
                                                       get_release_registry)
                    _ds = getattr(proj, "dispatch_state", {}) or {}
                    get_effect_cache_store().from_dict(_ds)
                    get_release_registry().from_dict(_ds.get("releases", {}) or {})
                    # ★ 场景库整体换新 → 运行期记账必须作废（深查 A3 + 2026-09-18 回访）：
                    #   残留的层增益会把新工程的层压暗/静音，残留的激活顺序会把
                    #   早已不在的场景重新放出来（幽灵出光）。见 `reset_scene_runtime_state`。
                    try:
                        from .dispatch.scene_release import reset_scene_runtime_state
                        reset_scene_runtime_state(w)
                    except Exception:
                        pass
                except Exception:
                    pass
                w.refresh_saved()
                w.refresh_monitor()
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning('DispatchPanel load failed: %s', _e)

        # ── FixtureGroupPanel ───────────────────────────────────────────────
        if _want(self._fixture_group_dock) and self._fixture_group_widget is None:
            try:
                from .fixture_group_panel import FixtureGroupPanel
                w = FixtureGroupPanel(self._pm)
                self._fixture_group_widget = w
                self._install_dock_widget(self._fixture_group_dock, w)
                if self._route_order_widget is not None:
                    try:
                        self._route_order_widget.set_context(
                            project_manager=self._pm,
                            topview=self._topview_widget,
                            fixture_group_panel=w,
                        )
                    except Exception:
                        pass
                if self._topview_widget is not None and hasattr(w, "fixture_selection_changed"):
                    _connect_once(
                        w.fixture_selection_changed,
                        self._sync_fixture_selection_to_topview)
                    try:
                        if hasattr(w, "get_live_selected_fixture_ids"):
                            self._sync_initial_fixture_selection_to_topview(w)
                    except Exception:
                        pass
                if self._programmer_widget is not None:
                    _connect_once(
                        w.group_selected,
                        lambda gid, fids, gname, _pw=self._programmer_widget, _fg=w: _pw.load_fixture_group(
                            gid, fids, gname,
                            base_order_ids=list(
                                getattr(_fg._get_group(gid), 'base_order_fixture_ids', []) or fids
                            ) if hasattr(_fg, '_get_group') else list(fids)
                        ))
                    _connect_once(
                        w.all_selected,
                        self._programmer_widget.load_all_fixtures)
                    if hasattr(self._programmer_widget, 'sync_groups_from_panel'):
                        self._programmer_widget.sync_groups_from_panel(w)
                    w.group_selected.connect(
                        lambda gid, fids, gname='': (
                            self._programmer_widget.sync_groups_from_panel(w)
                            if hasattr(self._programmer_widget, 'sync_groups_from_panel') else None
                        ))
                if self._matrix_pattern_widget is not None:
                    mw = self._matrix_pattern_widget
                    def _tv_all_pos_fg():
                        tv = self._topview_widget
                        if tv is None:
                            return []
                        if hasattr(tv, 'get_all_fixture_positions'):
                            return tv.get_all_fixture_positions()
                        return tv.get_fixture_positions()
                    w.group_selected.connect(
                        lambda gid, fids, gname, _mw=mw: _mw.load_fixture_group(
                            gid, fids, gname,
                            self._pm,
                            _tv_all_pos_fg()))
                    w.all_selected.connect(
                        lambda fids, _mw=mw: _mw.load_fixture_group(
                            '__all__', fids, '全部设备',
                            self._pm,
                            _tv_all_pos_fg()))
                    try:
                        if hasattr(mw, 'refresh_filter_group_combo'):
                            mw.refresh_filter_group_combo()
                    except Exception:
                        pass
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "FixtureGroupPanel lazy load failed: %s", e)
        elif _want(self._fixture_group_dock) and self._fixture_group_widget is not None:
            if self._programmer_widget is not None:
                try:
                    _connect_once(
                        self._fixture_group_widget.group_selected,
                        lambda gid, fids, gname, _pw=self._programmer_widget, _fg=self._fixture_group_widget: _pw.load_fixture_group(
                            gid, fids, gname,
                            base_order_ids=list(
                                getattr(_fg._get_group(gid), 'base_order_fixture_ids', []) or fids
                            ) if hasattr(_fg, '_get_group') else list(fids)
                        ))
                    _connect_once(
                        self._fixture_group_widget.all_selected,
                        self._programmer_widget.load_all_fixtures)
                except Exception:
                    pass
            if self._matrix_pattern_widget is not None:
                try:
                    mw = self._matrix_pattern_widget
                    def _tv_all_pos2():
                        tv = self._topview_widget
                        if tv is None:
                            return []
                        if hasattr(tv, 'get_all_fixture_positions'):
                            return tv.get_all_fixture_positions()
                        return tv.get_fixture_positions()
                    self._fixture_group_widget.group_selected.connect(
                        lambda gid, fids, gname, _mw=mw: _mw.load_fixture_group(
                            gid, fids, gname, self._pm, _tv_all_pos2()))
                    self._fixture_group_widget.all_selected.connect(
                        lambda fids, _mw=mw: _mw.load_fixture_group(
                            '__all__', fids, '全部设备', self._pm, _tv_all_pos2()))
                    if hasattr(mw, 'refresh_filter_group_combo'):
                        mw.refresh_filter_group_combo()
                except Exception:
                    pass
    # ════════════════════════════════════════════════════════════════════════════
    #  Effect / Processor 回调
    # ══════════════════════════════════════════════════════════════════════════

    def _on_effect_applied(self, *args):
        """EffectBrowser.effect_applied / AudioBridge.effect_apply_request 的统一入口。
        ★ v24: 支持两种签名：
          - (preset_id, params, ch_types, sort_mode) — 旧的单一模式
          - (track_type, preset_id, params, ch_types, sort_mode) — v24多类型模式
        路由到 EffectBrowserWidget.play_preset_for_type() 或 play_preset_external()。
        """
        if len(args) == 6:
            track_type, preset_id, params, ch_types, sort_mode, group_ids = args
        elif len(args) == 5:
            track_type, preset_id, params, ch_types, sort_mode = args
            group_ids = None
        elif len(args) == 4:
            track_type = None
            preset_id, params, ch_types, sort_mode = args
            group_ids = None
        else:
            return
        # 1) 懒加载 EffectBrowser dock
        if self._effect_browser_widget is None:
            try:
                self._lazy_load_panels(self._effect_browser_dock)
            except Exception:
                pass
        # 2) 路由到外部驱动入口口
        eb = self._effect_browser_widget
        if eb is not None:
            try:
                params = self._inject_effect_output_scope(
                    params, group_ids=group_ids, effect_browser=eb)
                if track_type and hasattr(eb, "play_preset_for_type"):
                    # v24 多类型并行模式
                    eb.play_preset_for_type(
                        track_type, preset_id, params, ch_types, sort_mode)
                    return
                elif hasattr(eb, "play_preset_external"):
                    eb.play_preset_external(preset_id, params, ch_types, sort_mode)
                    return
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "EffectBrowser.play_preset routing failed: %s", e)

        # 3) 兆底路径
        if self._programmer_widget is None:
            return
        try:
            if hasattr(self._programmer_widget, '_ch_list'):
                cl = self._programmer_widget._ch_list
                for row in cl._rows:
                    if not any(ct in row._ch_types for ct in ch_types):
                        continue
                    fn = getattr(row, "_apply_preset", None)
                    if callable(fn):
                        fn(preset_id, params, sort_mode=sort_mode)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(
                "_on_effect_applied legacy row path: %s", e)

    def _on_global_audio_source_changed(self, mode: str):
        """
        全局音频来源切换。
        mode: "simulate" | "file" | "microphone"
        切换的是 AudioAnalyzer 的识别输入源，不影响轨道上已加载的音频文件。
        """
        try:
            from .audio_driver import get_audio_driver
            analyzer = get_audio_driver()
            if mode == "microphone":
                # _selected_mic_device 由 PanelToolbar._select_mic_device_dialog 设置
                # -1 表示系统默认，None 也表示系统默认
                mic_dev = None
                if hasattr(self._panel_toolbar, '_selected_mic_device'):
                    dev_idx = self._panel_toolbar._selected_mic_device
                    mic_dev = None if dev_idx == -1 else dev_idx
                if hasattr(analyzer, 'set_mic_device'):
                    analyzer.set_mic_device(mic_dev)
                analyzer.set_mode("microphone")
                # 连接 energy_updated 信号到工具栏电平条
                if not getattr(self, '_mic_level_connected', False):
                    try:
                        analyzer.energy_updated.connect(self._on_mic_energy_updated)
                        self._mic_level_connected = True
                    except Exception:
                        pass
            else:
                analyzer.set_mode(mode)
                # 断开 energy_updated 信号
                if getattr(self, '_mic_level_connected', False):
                    try:
                        analyzer.energy_updated.disconnect(self._on_mic_energy_updated)
                    except Exception:
                        pass
                    self._mic_level_connected = False
                if hasattr(self._panel_toolbar, 'update_mic_level'):
                    self._panel_toolbar.update_mic_level(0.0)
            prepare_audio_workspace(self)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "_on_global_audio_source_changed failed: %s", e)

    def _on_mic_energy_updated(self, rms: float):
        """麦克风实时电平更新到工具栏电平条。"""
        if hasattr(self._panel_toolbar, 'update_mic_level'):
            self._panel_toolbar.update_mic_level(rms)

    def _on_joystick_settings_clicked(self):
        """主窗口外部控制绑定入口已移除；保留空实现以兼容旧信号引用。"""
        return

    def _on_audio_bridge_preset_changed(self, *args):
        """AudioBridge 主对象的 current_preset_changed 监听。
        ★ v24: 签名变为 (track_type, preset_id)，兼容旧的 (preset_id,)。
        当 preset_id 为空字符串时，停止对应类型的外部驱动。
        """
        if len(args) == 2:
            track_type, preset_id = args
        elif len(args) == 1:
            track_type, preset_id = None, args[0]
        else:
            return
        if preset_id:
            return  # 非空：实际预设切换由 effect_apply_request 路径处理
        eb = self._effect_browser_widget
        if eb is not None:
            try:
                if track_type and hasattr(eb, "stop_external_type"):
                    eb.stop_external_type(track_type)
                elif hasattr(eb, "stop_external"):
                    eb.stop_external()
            except Exception:
                pass

    def _on_effect_send_to_timeline(self, preset_id: str, params: dict,
                                    ch_types: list, sort_mode: str,
                                    duration: float, fps: float):
        if not self._require_active_program("发送效果到时间线"):
            return
        if self._programmer_widget is None:
            return
        try:
            from .effect_engine import get_engine as _get_ef
            ef = _get_ef()
            pw = self._programmer_widget
            eb = getattr(self, "_effect_browser_widget", None)
            from .effect_target_scope import timeline_fixtures
            fixtures = timeline_fixtures(eb, pw)
            if not fixtures:
                return
            params = dict(params or {})
            fdefs = getattr(pw, "_fixture_defs_cache", {}) or {}
            if not fdefs:
                fdefs = {
                    str(getattr(fd, "id", "")): fd
                    for fd in (getattr(getattr(self._pm, "project", None), "fixture_defs", []) or [])
                    if getattr(fd, "id", None)
                }
            valid_fixture_ids = {str(fi.id) for fi in fixtures}
            base_order_ids = [
                str(fid)
                for fid in list(
                    (getattr(eb, "_selected_group_base_order_ids", None) if eb is not None else None)
                    or self._topview_order_ids()
                    or getattr(pw, "_current_base_order_ids", [])
                    or [fi.id for fi in fixtures])
                if str(fid) in valid_fixture_ids
            ]
            if not base_order_ids:
                base_order_ids = [str(fi.id) for fi in fixtures]
            output_fixture_ids = [str(fi.id) for fi in fixtures]
            params["_output_fixture_ids"] = list(output_fixture_ids)
            params["_output_base_order_ids"] = list(base_order_ids)
            if eb is not None and hasattr(eb, "_inject_output_spatial_groups"):
                params = eb._inject_output_spatial_groups(params, output_fixture_ids)
            else:
                spatial_groups = self._topview_spatial_groups_for_selection(
                    output_fixture_ids,
                )
                if spatial_groups:
                    params["_output_spatial_groups"] = spatial_groups
            positions = {str(fi.id): {"pos_x": getattr(fi, "pos_x", 0.0),
                                      "pos_y": getattr(fi, "pos_y", 0.0)}
                         for fi in fixtures}
            tv = getattr(self, "_topview_widget", None)
            if tv is not None:
                try:
                    pos_rows = (tv.get_all_fixture_positions()
                                if hasattr(tv, "get_all_fixture_positions")
                                else tv.get_fixture_positions())
                    for row in pos_rows or []:
                        fid = str(row.get("id") or row.get("fixture_id") or "")
                        if fid:
                            positions[fid] = {
                                "pos_x": float(row.get("pos_x", row.get("x", 0.0)) or 0.0),
                                "pos_y": float(row.get("pos_y", row.get("y", 0.0)) or 0.0),
                            }
                except Exception:
                    pass
            def _merge_frames(frame_sets):
                merged = []
                max_len = max((len(fs) for fs in frame_sets), default=0)
                for frame_idx in range(max_len):
                    src = next((fs[frame_idx] for fs in frame_sets if frame_idx < len(fs)), None)
                    if src is None:
                        continue
                    from .effect_engine import EffectFrame
                    out = EffectFrame(t=getattr(src, "t", frame_idx / max(1.0, float(fps))))
                    out.values = [{} for _ in range(len(fixtures))]
                    for fs in frame_sets:
                        if frame_idx >= len(fs):
                            continue
                        frame = fs[frame_idx]
                        for fi_idx, vals in enumerate(getattr(frame, "values", []) or []):
                            if fi_idx >= len(out.values) or not vals:
                                continue
                            for ch, val in dict(vals).items():
                                out.values[fi_idx][ch] = max(
                                    int(out.values[fi_idx].get(ch, 0)),
                                    max(0, min(255, int(val))))
                    merged.append(out)
                return merged

            forms = list(params.get("_channel_forms", []) or []) if isinstance(params, dict) else []
            ch_key = None
            if isinstance(params, dict):
                for key in ("ch_type", "ch", "channel", "target_channel"):
                    if key in params:
                        ch_key = key
                        break
            if forms and ch_key:
                frame_sets = []
                for item in forms:
                    if not isinstance(item, dict):
                        continue
                    ch = str(item.get("ch_type", "") or "")
                    if not ch:
                        continue
                    p = dict(params or {})
                    p.pop("_channel_forms", None)
                    p[ch_key] = ch
                    p.update(dict(item.get("params", {}) or {}))
                    if eb is not None and hasattr(eb, "_params_with_audio"):
                        p = eb._params_with_audio(p, 0.0)
                    if eb is not None and hasattr(eb, "_inject_output_spatial_groups"):
                        p = eb._inject_output_spatial_groups(p, output_fixture_ids)
                    elif params.get("_output_spatial_groups"):
                        p["_output_spatial_groups"] = params.get("_output_spatial_groups")
                    fs = ef.compute(
                        preset_id, p,
                        fixtures, fdefs, positions,
                        duration=duration, fps=fps, sort_mode=sort_mode,
                        base_order_ids=base_order_ids)
                    if fs:
                        frame_sets.append(fs)
                frames = _merge_frames(frame_sets)
            else:
                clean_params = dict(params or {})
                clean_params.pop("_channel_forms", None)
                if eb is not None and hasattr(eb, "_params_with_audio"):
                    clean_params = eb._params_with_audio(clean_params, 0.0)
                if eb is not None and hasattr(eb, "_inject_output_spatial_groups"):
                    clean_params = eb._inject_output_spatial_groups(clean_params, output_fixture_ids)
                elif params.get("_output_spatial_groups"):
                    clean_params["_output_spatial_groups"] = params.get("_output_spatial_groups")
                frames = ef.compute(
                    preset_id, clean_params,
                    fixtures, fdefs, positions,
                    duration=duration, fps=fps, sort_mode=sort_mode,
                    base_order_ids=base_order_ids)
            if not frames:
                return
            from .programmer_panel import CueData
            # 保留完整动态效果序列。旧逻辑只把首帧塞进 CueData.values，
            # 后续按静态 Cue 生成时间线，既丢失动态效果，也容易在首帧为空时生成 0 个 clip。
            timeline_frames_by_fixture = []
            cue_vals = {}
            for frame in frames:
                frame_map = {}
                for orig_i, fi in enumerate(fixtures):
                    vals = {}
                    if orig_i < len(frame.values) and frame.values[orig_i]:
                        vals = dict(frame.values[orig_i])
                    frame_map[fi.id] = vals
                    if vals and fi.id not in cue_vals:
                        cue_vals[fi.id] = vals
                timeline_frames_by_fixture.append(frame_map)

            # 若效果第一批帧恰好为空，也保留 effect 帧列表交给 ProgrammerPanel 生成 clip；
            # 只要存在已配接灯具和有效通道，就应生成时间线 Clip。
            cue = CueData(
                name=f"Effect: {preset_id}",
                values=cue_vals,
                effects={
                    "_timeline_frames_by_fixture": timeline_frames_by_fixture,
                    "_source_fps": fps,
                    "_source_duration": duration,
                    "_preset_id": preset_id,
                    "_base_order_fixture_ids": list(base_order_ids),
                },
            )
            pw._send_cue_to_timeline(cue)
            if eb is not None and hasattr(eb, "_on_stop"):
                eb._on_stop()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "_on_effect_send_to_timeline failed: %s", e)

    def set_processor_widget(self, w: QWidget):
        self._processor_widget = w
        self._install_dock_widget(self._processor_dock, w)
        if hasattr(w, "process_done"):
            try:
                w.process_done.disconnect(self._on_process_done)
            except Exception:
                pass
            w.process_done.connect(self._on_process_done)
        if hasattr(w, "cue_send_to_programmer"):
            try:
                w.cue_send_to_programmer.disconnect(self._on_generalized_cue_to_programmer)
            except Exception:
                pass
            w.cue_send_to_programmer.connect(self._on_generalized_cue_to_programmer)

    def set_network_widget(self, w: QWidget):
        self._network_widget = w
        self._install_dock_widget(self._network_dock, w)
        _apply_network_config_to_panel(self)

    def set_monitor_widget(self, w: QWidget):
        self._monitor_widget = w
        self._install_dock_widget(self._monitor_dock, w)

    def _sync_fixture_defaults(self):
        nm = getattr(self, "_nm", None)
        if nm is None:
            return
        project = getattr(getattr(self, "_pm", None), "project", None)
        try:
            if hasattr(nm, "set_fixture_defaults_from_project"):
                nm.set_fixture_defaults_from_project(project)
            elif hasattr(nm, "set_motor_defaults_from_project"):
                nm.set_motor_defaults_from_project(project)
        except Exception:
            pass
        self._sync_xy_reference()

    def _sync_xy_reference(self):
        nm = getattr(self, "_nm", None)
        project = getattr(getattr(self, "_pm", None), "project", None)
        if nm is None or project is None:
            return
        # ★ 2026-09-19：只在面板**已绑定到本工程**时读它的状态（见
        #   `_bound_topview_state`）—— 否则加载工程时会把上一个工程的空状态
        #   当"最新布局"回写，刚读进来的 topview_state 当场被覆盖（用户实报
        #   "保存后再加载 topview 等信息丢失"，反复复发）。
        state = _bound_topview_state(self, project)
        # ★ 性能(2026-08-05): 输入指纹缓存——单次提交内 layout_changed 双发使
        #   _sync_xy_reference 同输入连调 2 次(每次 ~170ms: save_state + map 重建
        #   + flush 全链)。指纹 = 工程身份 + 配接签名(id/len, 同 _motor_pair_groups_sig
        #   模式) + 完整 save_state 内容(选择/位置/xy map/角度全覆盖); 全同则
        #   跳过整个重建链, 重复调用折叠为 1 次。
        flist = getattr(project, "fixtures", []) or []
        dlist = getattr(project, "fixture_defs", []) or []
        sig = (id(project), id(flist), len(flist), id(dlist), len(dlist), state)
        if state is not None and sig == getattr(self, "_xy_ref_sig", None):
            return
        self._xy_ref_sig = sig
        if state is not None and hasattr(project, "topview_state"):
            try:
                project.topview_state = state
            except Exception:
                pass
        try:
            if hasattr(nm, "set_xy_reference_from_project"):
                nm.set_xy_reference_from_project(project)
            if hasattr(nm, "request_universe_refresh"): nm.request_universe_refresh()  # ★ R194/AUD-05：置脏+唤醒
            elif hasattr(nm, "flush_universes_now"): nm.flush_universes_now()
        except Exception:
            pass

    def _recording_xy_reference_state(self) -> dict:
        project = getattr(getattr(self, "_pm", None), "project", None)
        tv = getattr(self, "_topview_widget", None)
        if tv is not None and hasattr(tv, "save_state"):
            try:
                state = tv.save_state()
                if project is not None and hasattr(project, "topview_state"):
                    project.topview_state = state
                return state if isinstance(state, dict) else {}
            except Exception:
                pass
        state = getattr(project, "topview_state", {}) if project is not None else {}
        return state if isinstance(state, dict) else {}

    def _sync_motor_defaults(self):
        self._sync_fixture_defaults()

    def _topview_order_ids(self):
        tv = getattr(self, "_topview_widget", None)
        if tv is None:
            return []
        try:
            if hasattr(tv, "_ordered_selected_ids"):
                ids = list(tv._ordered_selected_ids())
                if ids:
                    return [str(x) for x in ids]
            if hasattr(tv, "fixture_ids"):
                visible = [str(x) for x in tv.fixture_ids()]
                selected = {str(x) for x in (getattr(tv, "selected_ids", []) or [])}
                if selected:
                    ordered = [fid for fid in visible if fid in selected]
                    if ordered:
                        return ordered
                return visible
            selected = list(getattr(tv, "selected_ids", []) or [])
            if selected:
                return [str(x) for x in selected]
        except Exception:
            return []
        return []

    def _effect_browser_output_scope_ids(self, effect_browser=None):
        eb = effect_browser or getattr(self, "_effect_browser_widget", None)
        if eb is None:
            return [], []
        ids = self._unique_fixture_ids(getattr(eb, "_selected_group_ids", []) or [])
        if not ids:
            return [], []
        base = self._unique_fixture_ids(
            getattr(eb, "_selected_group_base_order_ids", []) or [])
        allowed = set(ids)
        ordered = [fid for fid in base if fid in allowed]
        present = set(ordered)
        for fid in ids:
            if fid not in present:
                ordered.append(fid)
                present.add(fid)
        return ids, (ordered or ids)

    def _programmer_output_scope_ids(self, programmer_widget=None):
        pw = programmer_widget or getattr(self, "_programmer_widget", None)
        if pw is None:
            return [], []
        try:
            group_id = str(getattr(pw, "_current_group_id", "") or "")
            ids = self._unique_fixture_ids(pw.get_live_selected_fixture_ids() or [])
        except Exception:
            return [], []
        if not ids or group_id in ("", "__all__"):
            return [], []
        if self._is_project_all_fixture_selection(ids):
            return [], []
        base = self._unique_fixture_ids(getattr(pw, "_current_base_order_ids", []) or [])
        allowed = set(ids)
        ordered = [fid for fid in base if fid in allowed]
        present = set(ordered)
        for fid in ids:
            if fid not in present:
                ordered.append(fid)
                present.add(fid)
        return ids, (ordered or ids)

    def _inject_effect_output_scope(self, params, group_ids=None, effect_browser=None):
        out = dict(params or {})
        has_scope = group_ids is not None or "_output_fixture_ids" in out
        explicit = self._unique_fixture_ids(
            group_ids if group_ids is not None else out.get("_output_fixture_ids", []))
        if has_scope:
            out["_output_fixture_ids"] = list(explicit)
            out["_output_scope_explicit"] = True
            base = self._unique_fixture_ids(out.get("_output_base_order_ids") or [])
            if not base:
                base = self._unique_fixture_ids(group_ids or []) or list(explicit)
            from src.core.fixture_selection import ordered_members
            out["_output_base_order_ids"] = ordered_members(explicit, base)
            return out

        eb_ids, eb_base = self._effect_browser_output_scope_ids(effect_browser)
        if eb_ids:
            out["_output_fixture_ids"] = eb_ids
            out["_output_base_order_ids"] = eb_base or eb_ids
            return out

        programmer_ids, programmer_base = self._programmer_output_scope_ids()
        if programmer_ids:
            out["_output_fixture_ids"] = programmer_ids
            out["_output_base_order_ids"] = programmer_base or programmer_ids
            return out

        topview_ids = self._topview_explicit_selection_ids()
        if topview_ids:
            out["_output_fixture_ids"] = topview_ids
            out["_output_base_order_ids"] = self._topview_base_order_ids(topview_ids) or topview_ids
        return out

    def _unique_fixture_ids(self, ids):
        out = []
        seen = set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid and sfid not in seen:
                out.append(sfid)
                seen.add(sfid)
        return out

    def _fixture_group_record(self, group_id: str):
        gid = str(group_id or "")
        if not gid:
            return None
        fg = getattr(self, "_fixture_group_widget", None)
        try:
            if fg is not None and hasattr(fg, "_get_group"):
                group = fg._get_group(gid)
                if group is not None:
                    return group
        except Exception:
            pass
        project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        for group in getattr(project, "fixture_groups", []) or []:
            try:
                if isinstance(group, dict) and str(group.get("id", "")) == gid:
                    return group
            except Exception:
                pass
        return None

    def _fixture_group_order_ids(self, group_id: str):
        group = self._fixture_group_record(group_id)
        if group is None:
            return []
        if isinstance(group, dict):
            fixture_ids = group.get("fixture_ids") or group.get("fixtures") or []
            base_order = group.get("base_order_fixture_ids") or []
        else:
            fixture_ids = getattr(group, "fixture_ids", []) or []
            base_order = getattr(group, "base_order_fixture_ids", []) or []
        from src.core.fixture_selection import ordered_members
        return ordered_members(fixture_ids, base_order)

    def _fixture_group_route_sheets(self, group_id: str):
        group = self._fixture_group_record(group_id)
        if group is None:
            return []
        try:
            sheets = group.get("route_order_sheets", []) if isinstance(group, dict) else getattr(group, "route_order_sheets", [])
            return list(sheets or []) if isinstance(sheets, list) else []
        except Exception:
            return []

    def _normalize_spatial_groups_for_selection(self, groups, fixture_ids, keep_single: bool = False):
        allowed_order = self._unique_fixture_ids(fixture_ids)
        allowed = set(allowed_order)
        out = []
        used = set()
        for raw in groups or []:
            if not isinstance(raw, (list, tuple)):
                continue
            segment = []
            local_seen = set()
            for fid in raw:
                sfid = str(fid)
                if not sfid or sfid in local_seen:
                    continue
                if allowed and sfid not in allowed:
                    continue
                segment.append(sfid)
                local_seen.add(sfid)
            if not segment:
                continue
            if any(fid in used for fid in segment):
                return []
            used.update(segment)
            out.append(segment)
        return out if (keep_single or len(out) > 1) else []

    def _spatial_groups_from_route_sheets(self, sheets, fixture_ids, keep_single: bool = False):
        groups = []
        for sheet in sheets or []:
            if not isinstance(sheet, dict):
                continue
            groups.append(self._unique_fixture_ids(
                sheet.get("ordered_ids") or sheet.get("source_ids") or []))
        return self._normalize_spatial_groups_for_selection(
            groups, fixture_ids, keep_single=keep_single)

    def _topview_spatial_groups_for_selection(self, fixture_ids, keep_single: bool = False):
        ordered = self._unique_fixture_ids(fixture_ids)
        if len(ordered) <= 1:
            return []
        # ★ 性能(2026-08-05): 输入指纹缓存——每次选择/页面/分组变化都全量重算
        #   路由表成员归属(1356 设备 ~10-50ms, 全选信号链 3× 触发去重后仍逐次重算)。
        #   指纹 = 工程身份 + 选择序 + 路由表内容 + 当前页身份/放置组 + 活动分组
        #   + 分组清单身份(id+len, 与 _sync_xy_reference/_motor_pair_groups_sig 同模式);
        #   全同则跳过整条重算链, 直接复用上次结果(返回全新副本防下游改写)。
        sig = self._topview_spatial_groups_sig(ordered, keep_single)
        hit = getattr(self, "_topview_spatial_groups_cache", None)
        if hit is not None and hit[0] == sig:
            return [list(g) for g in hit[1]]
        result = self._topview_spatial_groups_compute(ordered, keep_single)
        self._topview_spatial_groups_cache = (sig, result)
        # 命中/未命中都返回全新副本: 缓存里存的是原始对象, 直接返回会被下游改写污染
        return [list(g) for g in result]

    def _topview_spatial_groups_sig(self, ordered, keep_single: bool):
        """_topview_spatial_groups_for_selection 输入指纹(覆盖全部读量; 空容器用
        稳定占位, 避免每次新建空 list 导致 id 抖动使缓存恒不命中)。"""
        project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        row = getattr(self, "_route_order_widget", None)
        sheets = list(getattr(row, "_sheets", []) or []) if row is not None else []
        sheets_sig = tuple(
            tuple(s.get("ordered_ids") or s.get("source_ids") or []) if isinstance(s, dict) else ()
            for s in sheets)
        tv = getattr(self, "_topview_widget", None)
        page = None
        if tv is not None:
            try:
                page = tv.current_page() if hasattr(tv, "current_page") else None
            except Exception:
                page = None
        page_id = id(page) if page is not None else None
        placed_sig = tuple(
            str(gid) for gid in (page.get("placed_group_ids", []) or []) if str(gid)
        ) if page is not None else ()
        gid = str(getattr(tv, "_active_group_id", "") or "") if tv is not None else ""
        group_sig = ()
        if gid:
            try:
                record = self._fixture_group_record(gid)
                if record is not None:
                    gsheets = (record.get("route_order_sheets") if isinstance(record, dict)
                               else getattr(record, "route_order_sheets", None)) or []
                    group_sig = (
                        id(record),
                        tuple(tuple(s.get("ordered_ids") or s.get("source_ids") or [])
                              if isinstance(s, dict) else ()
                              for s in gsheets))
            except Exception:
                group_sig = ()
        fgroups = getattr(project, "fixture_groups", []) or [] if project is not None else []
        return (id(project) if project is not None else None,
                tuple(ordered), bool(keep_single),
                sheets_sig, page_id, placed_sig, gid, group_sig,
                id(fgroups) if fgroups else -1, len(fgroups))

    def _topview_spatial_groups_compute(self, ordered, keep_single: bool = False):
        tv = getattr(self, "_topview_widget", None)
        allowed = set(ordered)

        row = getattr(self, "_route_order_widget", None)
        try:
            if row is not None and getattr(row, "_sheets", None):
                sheets = list(getattr(row, "_sheets", []) or [])
                sheet_groups = self._spatial_groups_from_route_sheets(
                    sheets, ordered, keep_single=keep_single)
                if sheet_groups:
                    return sheet_groups
        except Exception:
            pass

        try:
            gid = str(getattr(tv, "_active_group_id", "") or "") if tv is not None else ""
            if gid:
                sheet_groups = self._spatial_groups_from_route_sheets(
                    self._fixture_group_route_sheets(gid), ordered,
                    keep_single=keep_single)
                if sheet_groups:
                    return sheet_groups
        except Exception:
            pass

        try:
            if tv is not None and hasattr(tv, "spatial_groups_for_fixture_ids"):
                shape_groups = tv.spatial_groups_for_fixture_ids(ordered)
                spatial_groups = self._normalize_spatial_groups_for_selection(
                    shape_groups, ordered, keep_single=keep_single)
                if spatial_groups:
                    return spatial_groups
        except Exception:
            pass

        placed_group_ids = []
        try:
            page = tv.current_page() if tv is not None and hasattr(tv, "current_page") else {}
            placed_group_ids = [str(gid) for gid in (page.get("placed_group_ids", []) or []) if str(gid)]
        except Exception:
            placed_group_ids = []
        groups = []
        for gid in placed_group_ids:
            group_ids = [fid for fid in self._fixture_group_order_ids(gid) if fid in allowed]
            if group_ids:
                groups.append(group_ids)
        spatial_groups = self._normalize_spatial_groups_for_selection(
            groups, ordered, keep_single=keep_single)
        if spatial_groups:
            return spatial_groups

        project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        groups = []
        for group in getattr(project, "fixture_groups", []) or []:
            try:
                gid = str(group.get("id", "")) if isinstance(group, dict) else str(getattr(group, "id", ""))
                group_ids = [fid for fid in self._fixture_group_order_ids(gid) if fid in allowed]
                if group_ids and set(group_ids) != allowed:
                    groups.append(group_ids)
            except Exception:
                pass
        return self._normalize_spatial_groups_for_selection(
            groups, ordered, keep_single=keep_single)

    def _topview_base_order_ids(self, ordered=None):
        tv = getattr(self, "_topview_widget", None)
        ordered_norm = self._unique_fixture_ids(ordered or [])
        if tv is None:
            return list(ordered_norm)
        try:
            established = _topview_live_base_if_established(tv, ordered_norm)
            if established:
                return established
        except Exception:
            pass
        # R201/P10：兜底 = 当前选择序（框选链序，已含闭环极角修复）——
        #   与画布编号徽标同源；页序兜底（P09）在真实两工程上被证伪，见
        #   `_topview_page_number_base` 文档。
        return self._topview_order_ids() or list(ordered_norm)

    def _sync_effect_browser_topview_selection(self, ids=None):
        eb = getattr(self, "_effect_browser_widget", None)
        if eb is None or not hasattr(eb, "set_live_fixture_selection"):
            return
        try:
            source_ids = self._topview_explicit_selection_ids() if ids is None else ids
            ordered = [str(x) for x in (source_ids or [])]
            # 去重守卫:一次"全选"会连发 3 个信号(fixture_selection/all_selected/group_selected)
            # 各触发一遍相同全量同步(含 1356 设备的 spatial group 重算)。选择未变则跳过重复同步,
            # 3×塌缩为1×。仅"同步到 effect browser"这一路做守卫,不影响其他信号语义。
            tv = getattr(self, "_topview_widget", None)
            # ★ R38-D6：去重键必须带上 TopView 布局版本。
            #   旧键只有选择 id，但下面真正发过去的载荷还包含 spatial_groups，
            #   而它是按 TopView 图形几何算出来的。于是"选择不变、图形被拖动/
            #   增删"时守卫会拦掉重发，effect 一直用**过期的空间分组**——这不是
            #   卡顿，是按错误的分组输出。加上版本号后，布局一变必然重发。
            try:
                layout_ver = int(getattr(tv, "_layout_version", 0) or 0)
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "topview layout version unavailable for eb sync guard")
                layout_ver = 0
            sig = _topview_sync_sig(tv, ordered, layout_ver)
            if sig == getattr(self, "_last_eb_sync_sig", None):
                return
            self._last_eb_sync_sig = sig
            base = self._topview_base_order_ids(ordered) or ordered
            spatial_groups = self._topview_spatial_groups_for_selection(ordered)
            name = str(getattr(tv, "_live_selection_name", "") or "TopView实时选择")
            eb.set_live_fixture_selection(
                ordered, base_order_ids=base, name=name,
                spatial_groups=spatial_groups, subset_ids=ordered)
        except Exception:
            pass

    def _topview_explicit_selection_ids(self, topview_widget=None):
        tv = topview_widget or getattr(self, "_topview_widget", None)
        if tv is None:
            return []
        try:
            if hasattr(tv, "_ordered_selected_ids"):
                ids = [str(x) for x in (tv._ordered_selected_ids() or []) if str(x)]
                if ids:
                    return ids
            selected = {str(x) for x in (getattr(tv, "selected_ids", []) or []) if str(x)}
            if not selected:
                return []
            if hasattr(tv, "fixture_ids"):
                ordered = [str(fid) for fid in (tv.fixture_ids() or []) if str(fid) in selected]
                if ordered:
                    return ordered
            return list(selected)
        except Exception:
            return []

    def _project_fixture_ids(self):
        project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        return [
            str(getattr(f, "id", ""))
            for f in (getattr(project, "fixtures", []) or [])
            if str(getattr(f, "id", ""))
        ]

    def _recording_base_order_fixture_ids(self):
        pw = getattr(self, "_programmer_widget", None)
        topview_selected = self._topview_explicit_selection_ids()
        selected_ids = []
        if pw is not None:
            try:
                selected_ids = [
                    str(x) for x in (pw.get_live_selected_fixture_ids() or [])
                    if str(x)
                ]
            except Exception:
                selected_ids = []
        project_all = set(self._project_fixture_ids())
        if topview_selected and (not selected_ids or (project_all and set(selected_ids) == project_all)):
            selected_ids = topview_selected
        if not selected_ids:
            selected_ids = topview_selected or self._topview_order_ids()

        allowed = set(selected_ids) if selected_ids else project_all
        base = []
        if pw is not None:
            try:
                base = [str(x) for x in (getattr(pw, "_current_base_order_ids", []) or []) if str(x)]
            except Exception:
                base = []
        if not base and selected_ids:
            base = self._topview_base_order_ids(selected_ids)
        if not base:
            base = selected_ids or self._project_fixture_ids()

        ordered = [fid for fid in self._unique_fixture_ids(base) if not allowed or fid in allowed]
        present = set(ordered)
        for fid in self._unique_fixture_ids(selected_ids or self._project_fixture_ids()):
            if (not allowed or fid in allowed) and fid not in present:
                ordered.append(fid)
                present.add(fid)
        return ordered

    def _is_project_all_fixture_selection(self, ids):
        ordered = [str(x) for x in (ids or []) if str(x)]
        all_ids = self._project_fixture_ids()
        return bool(ordered) and bool(all_ids) and set(ordered) == set(all_ids)

    def _sync_initial_fixture_selection_to_topview(self, widget, topview_widget=None):
        if widget is None or not hasattr(widget, "get_live_selected_fixture_ids"):
            return False
        # If TopView already has a real live selection, it is the source of truth.
        # Do not let Programmer/FixtureGroup startup state replace it.
        if self._topview_explicit_selection_ids(topview_widget):
            return False
        try:
            ids = [str(x) for x in (widget.get_live_selected_fixture_ids() or []) if str(x)]
        except Exception:
            return False
        # Newly created/refreshed panels often report their default "all fixtures"
        # selection. Do not let that automatic state replace a manual TopView selection.
        if self._is_project_all_fixture_selection(ids):
            return False
        self._sync_fixture_selection_to_topview(ids)
        return bool(ids)

    def _sync_programmer_from_current_topview_selection(self, programmer_widget=None, topview_widget=None):
        ids = self._topview_explicit_selection_ids(topview_widget)
        if not ids:
            return False
        self._load_programmer_from_topview_selection(ids, programmer_widget)
        return True

    def _load_programmer_from_topview_selection(self, ids, programmer_widget=None):
        pw = programmer_widget or getattr(self, "_programmer_widget", None)
        if pw is None:
            return
        ordered = [str(x) for x in (ids or []) if str(x)]
        # 去重守卫:一次全选连发 3 信号→本方法被调 3 次载入相同 1356 设备到 Programmer。
        # 选择未变则跳过重复载入(3×塌缩1×)。programmer_widget 显式传入时不守卫(定向刷新)。
        if programmer_widget is None:
            sig = tuple(ordered)
            # 仅当 programmer 仍停在 topview-live 模式时,相同选择才可安全跳过。
            # 若中途被组面板直调切到具体组(绕过本方法,sig 未更新),必须重载切回。
            still_live = str(getattr(pw, "_current_group_id", "") or "") == "__topview_live__"
            if still_live and sig == getattr(self, "_last_pw_load_sig", None):
                return
            self._last_pw_load_sig = sig
        base_order = self._topview_base_order_ids(ordered) if ordered else []
        base_order = base_order or ordered
        self._syncing_selection_from_topview = True
        old_suppress = bool(getattr(pw, "_suppress_topview_selection_emit", False))
        setattr(pw, "_suppress_topview_selection_emit", True)
        try:
            if hasattr(pw, "load_fixture_group"):
                pw.load_fixture_group(
                    "__topview_live__", ordered, "TopView实时选择",
                    base_order_ids=base_order)
            elif hasattr(pw, "load_all_fixtures"):
                pw.load_all_fixtures(ordered)
        except Exception as e:
            # ★ 2026-08-05：禁止静默吞异常——载入失败若被吞掉，Programmer 会停在
            #   上一组设备上而 TopView 高亮已切到新选择（高亮对但输出错）。
            import logging
            logging.getLogger(__name__).warning(
                "TopView 选择载入 Programmer 失败（输出设备集将不等于选中集）: %s", e)
        finally:
            setattr(pw, "_suppress_topview_selection_emit", old_suppress)
            self._syncing_selection_from_topview = False

    def _sync_fixture_selection_to_topview(self, ids=None):
        syncing_from_topview = bool(getattr(self, "_syncing_selection_from_topview", False))
        tv = getattr(self, "_topview_widget", None)
        sender = self.sender()
        skip_topview_write = syncing_from_topview or tv is None
        # ★ 2026-09-14 泛化守卫：任何面板在"由 TopView 反向驱动"或显式抑制期间都不回写
        #   TopView（防"写 TopView → 面板再回传"绕圈；矩阵新增了回传出口，同受约束）。
        if skip_topview_write or _selected_by_topview(sender):
            skip_topview_write = True
        ordered = [str(x) for x in (ids or [])]
        # ★★ R121：面板的「默认全选」自动状态不得覆盖 TopView 的真实选择（见助手说明）
        if not skip_topview_write and _is_default_all_selection_once(self, ordered, tv):
            skip_topview_write = True
        if not skip_topview_write:
            try:
                if hasattr(tv, "set_external_fixture_selection"):
                    tv.set_external_fixture_selection(ordered, emit=False)
                elif hasattr(tv, "_set_selection"):
                    tv._set_selection(ordered, emit=False)
                    canvas = getattr(tv, "_canvas", None)
                    if canvas is not None and hasattr(canvas, "update"):
                        canvas.update()
            except Exception:
                pass
        try:
            # 直接用 programmer 传入的选择同步到 effect,不绕 TopView 重读:
            # 当 skip_topview_write 时 TopView 未更新,重读会拿到旧/空选择导致 effect 不更新。
            self._sync_effect_browser_topview_selection(ordered if ordered else None)
        except Exception:
            pass

    def _topview_position_rows(self):
        tv = getattr(self, "_topview_widget", None)
        if tv is None:
            return []
        try:
            if hasattr(tv, "get_all_fixture_positions"):
                return tv.get_all_fixture_positions() or []
            if hasattr(tv, "get_fixture_positions"):
                return tv.get_fixture_positions() or []
        except Exception:
            pass
        return []

    def _push_topview_live_selection_to_loaded_panels(self, ids=None):
        """Re-apply the current TopView live selection after project reload/undo."""
        tv = getattr(self, "_topview_widget", None)
        if tv is None:
            return
        if ids is None:
            ordered = self._topview_explicit_selection_ids(tv)
        else:
            ordered = [str(x) for x in (ids or []) if str(x)]
        name = str(getattr(tv, "_live_selection_name", "") or "TopView实时选择")

        if self._programmer_widget is not None:
            self._load_programmer_from_topview_selection(
                ordered, self._programmer_widget)

        _matrix_load_from_topview(self, getattr(self, "_matrix_pattern_widget", None),
                                  ordered, explicit=ids is not None)

        row = getattr(self, "_route_order_widget", None)
        if row is not None and hasattr(row, "on_topview_selection_changed"):
            try:
                row.on_topview_selection_changed(ordered)
            except Exception:
                pass

        from .project_selection_restore import sync_effect_selection
        sync_effect_selection(self, ordered, explicit=ids is not None)

    def _on_topview_live_selection_changed(self, ids=None):
        self._push_topview_live_selection_to_loaded_panels(ids)

    def _ensure_topview_live_selection_bridge(self):
        tv = getattr(self, "_topview_widget", None)
        if tv is None or not hasattr(tv, "selection_changed"):
            return
        try:
            tv.selection_changed.disconnect(self._on_topview_live_selection_changed)
        except Exception:
            pass
        try:
            tv.selection_changed.connect(self._on_topview_live_selection_changed)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  Engine / PM 初始化
    # ══════════════════════════════════════════════════════════════════════════

    def _on_topview_fixture_group_saved(self, group_id="", fixture_ids=None, name="", base_order_ids=None):
        # ★★ 2026-09-20 用户实报修复（"以上几种情况都出现在实时 topview 保存组和
        #   实时选中的时候。大概率这个方向的信号问题"）：
        #   保存设备组会让 FixtureGroupPanel 刷新 → Programmer 的 `load_groups`
        #   重载组列表 → 面板回吐一个"默认全选"的 `fixture_selection_changed` →
        #   `_sync_fixture_selection_to_topview` 把 TopView 的选择**覆盖成全设备**
        #   （实测：用户选的奇数子集在保存组后被改成全部 6 台）。
        #   于是紧接着的"给值 / 存场景"都作用在**被扩大的作用域**上 —— 正是用户报的
        #   "存下来强行变成了整个组都有值"与"存在的东西调用时没有效果"。
        #   修：保存组期间置"由 TopView 反向驱动"抑制标志，面板刷新引发的回写一律
        #   不落到 TopView（保存组**不改变当前选择**，与 TopView 侧同一语义）。
        _prev_sync = bool(getattr(self, "_syncing_selection_from_topview", False))
        self._syncing_selection_from_topview = True
        try:
            for widget, methods in _GROUP_SAVE_REFRESH_TARGETS(self):
                _call_first_available(widget, methods)
        finally:
            self._syncing_selection_from_topview = _prev_sync
        eb = getattr(self, "_effect_browser_widget", None)
        if eb is not None:
            try:
                if hasattr(eb, "set_context"):
                    eb.set_context(
                        project=self._pm,
                        nm=self._nm,
                        topview=getattr(self, "_topview_widget", None),
                        fixture_group_panel=getattr(self, "_fixture_group_widget", None),
                        programmer_panel=getattr(self, "_programmer_widget", None),
                    )
                elif hasattr(eb, "_refresh_group_list"):
                    eb._refresh_group_list()
            except Exception:
                pass

    def _connect_engine(self):
        if self._engine is None:
            return
        install_audio_workspace(self)
        if hasattr(self._engine, "position_changed"):
            self._engine.position_changed.connect(self._on_position_changed)
        if hasattr(self._engine, "playback_started"):
            self._engine.playback_started.connect(
                lambda: self._transport.set_playing(True))
        if hasattr(self._engine, "playback_stopped"):
            self._engine.playback_stopped.connect(
                lambda: self._transport.set_playing(False))

    def _resolve_pm(self):
        import logging
        log = logging.getLogger(__name__)

        pm = None
        if self._engine is not None:
            pm = (getattr(self._engine, "_pm", None)
                  or getattr(self._engine, "project_manager", None))

        if pm is None:
            try:
                import sys, os
                root = os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))))
                if root not in sys.path:
                    sys.path.insert(0, root)
                from ..core.project_manager import ProjectManager
                pm = ProjectManager()
                log.info("ProjectManager 兜底创建成功")
            except Exception as e:
                log.error("ProjectManager 创建失败: %s", e, exc_info=True)
                print(f"[ERROR] ProjectManager 创建失败: {e}")

        if pm is not None:
            self._pm = pm
            self._sync_motor_defaults()
            if hasattr(pm, "project_changed"):
                try: pm.project_changed.disconnect(self._refresh_tracks)
                except Exception: pass
                pm.project_changed.connect(self._refresh_tracks)
            _bridge_pm_dirty(self, pm)   # ★ 见函数（两个脏标记原先互不同步）
        else:
            print("[ERROR] _resolve_pm: ProjectManager 不可用（保存/打开禁用）")

    def _on_pm_dirty_changed(self, is_dirty: bool) -> None:
        """PM 脏状态变化 → 同步到主窗（`closeEvent` 读的那个）标记。

        ★ 深查修正：两个脏标记原先互不同步（保存时 PM emit False，故只置位不强置）。
        """
        try:
            if is_dirty:
                note_project_edit(self)
                self._update_title()
        except Exception:
            pass

    def _on_topview_layout_changed(self, w) -> None:
        """TopView 布局变化 → 写进工程**并置脏**（否则关窗口无提示、改动丢失）。"""
        try:
            if self._pm is None or getattr(self._pm, "project", None) is None:
                return
            if not hasattr(w, "save_state"):
                return
            self._pm.project.topview_state = w.save_state()
            self._sync_xy_reference()
            self.mark_dirty()
        except Exception:
            log.debug("MainWindow: topview layout write failed", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  ★ v3: RecordEngine 初始化（使用 on_programmer_ready 模式）
    # ══════════════════════════════════════════════════════════════════════════

    def _init_record_engine(self):
        self._record_engine = None
        if self._nm is None or self._pm is None:
            return
        try:
            from ..engine.record_engine import RecordEngine
            self._record_engine = RecordEngine(
                network_manager=self._nm,
                project_manager=self._pm,
                parent=self,
            )
            self._traj_buffer = None
            self._traj_hub = None
            # ★ 调度台·控件轨迹：buffer 注入 + hub（面板聚合信号 → 引擎）
            try:
                from ..processing.dispatch.trajectory import ControlTrajectoryBuffer
                from .dispatch.trajectory_hooks import TrajectorySignalHub
                self._traj_buffer = ControlTrajectoryBuffer()
                self._record_engine.set_traj_buffer(self._traj_buffer)
                self._traj_hub = TrajectorySignalHub(self._record_engine, parent=self)
                # 面板可能尚未创建（本方法早于懒加载）→ getattr 防御；
                # 懒加载块尾会再补挂（attach_panel 幂等）。
                for _attr in ("_programmer_widget", "_effect_browser_widget"):
                    _w = getattr(self, _attr, None)
                    if _w is not None:
                        self._traj_hub.attach_panel(_w)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "控件轨迹装配失败（不影响录制）", exc_info=True)

            if hasattr(self._record_engine, "set_record_source_provider"):
                self._record_engine.set_record_source_provider(
                    lambda pos=0.0: (
                        self._engine.refresh_record_source_outputs_at(float(pos))
                        if self._engine is not None
                        and hasattr(self._engine, "refresh_record_source_outputs_at")
                        else {}
                    )
                )

            # ★ v3: 如果 ProgrammerPanel 已经加载，立即连接 dmx_out
            if self._programmer_widget is not None and hasattr(self._programmer_widget, 'dmx_out'):
                if hasattr(self._record_engine, 'connect_to_signal'):
                    self._record_engine.connect_to_signal(
                        self._programmer_widget.dmx_out)
            else:
                # ★ v3: 否则注册回调，等 ProgrammerPanel 加载后连接
                self._on_programmer_ready_cbs.append(
                    lambda pw, re=self._record_engine: (
                        re.connect_to_signal(pw.dmx_out)
                        if hasattr(re, 'connect_to_signal') and hasattr(pw, 'dmx_out')
                        else None
                    )
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "RecordEngine init failed: %s", e)

    # ══════════════════════════════════════════════════════════════════════════
    #  UI 回调
    # ══════════════════════════════════════════════════════════════════════════

    def _on_position_changed(self, pos: float):
        # ★ 性能：引擎每 tick(25ms) 发射 position_changed → 此处 80ms 节流
        #   UI 刷新（播放头/时间码 12.5fps 视觉无感，seek 延迟 <80ms 不可感知）。
        now = time.monotonic()
        if now - float(getattr(self, "_last_pos_ui_t", 0.0) or 0.0) < 0.08:
            return
        self._last_pos_ui_t = now
        self._transport.update_position(pos)
        if not self._use_timeline_widget:
            if hasattr(self, "_ruler") and hasattr(self._ruler, "set_position"):
                self._ruler.set_position(pos)

    def _start_ui_timer(self):
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self._refresh_status)
        self._ui_timer.start()

    def _refresh_status(self):
        if self._nm and hasattr(self._nm, "is_running"):
            proto = getattr(self._nm, "protocol_name", "")
            self._status.set_network(self._nm.is_running, proto)
        _refresh_fps_label(self)     # R182：真实播放 tick → FPS 标签
        _refresh_dmx_label(self)     # R182：NM 运行态/发送率 → DMX 标签
        refresh_audio_workspace(self)

    # ══════════════════════════════════════════════════════════════════════════
    #  播放控制
    # ══════════════════════════════════════════════════════════════════════════

    def _render_timeline_playback_start_frame(self, pos: float):
        engine = getattr(self, "_engine", None)
        if engine is None:
            return
        try:
            if self._nm is not None and hasattr(self._nm, "clear_layer"):
                self._nm.clear_layer("clip_preview")
        except Exception:
            pass
        try:
            if hasattr(engine, "_ltp_takeover_pending"):
                engine._ltp_takeover_pending = True
                engine._ltp_takeover_layer = "clip_playback"
            if hasattr(engine, "_render_position"):
                engine._render_position(float(pos), layer_name="clip_playback")
            elif hasattr(engine, "render_playback_at"):
                engine.render_playback_at(float(pos))
            if hasattr(engine, "_playback_start_frame_rendered"):
                engine._playback_start_frame_rendered = True
        except Exception:
            try:
                if hasattr(engine, "_playback_start_frame_rendered"):
                    engine._playback_start_frame_rendered = False
            except Exception:
                pass

    def _timeline_play(self):
        if not self._require_active_program("播放时间线"):
            try:
                self._transport.set_playing(False)
            except Exception:
                pass
            return
        if self._effect_browser_widget and self._effect_browser_widget._is_playing:
            self._effect_browser_widget._on_stop()
        ctrl = getattr(self._timeline_widget, "ctrl", None) if self._timeline_widget is not None else None
        if ctrl is not None:
            pos = float(getattr(ctrl, "_play_pos", 0.0) or 0.0)
            engine = getattr(self, "_engine", None)
            if engine is not None and hasattr(engine, "_playback_start_frame_rendered"):
                engine._playback_start_frame_rendered = False
            self._render_timeline_playback_start_frame(pos)
            ctrl.play()
            if engine is not None and hasattr(engine, "_playback_start_frame_rendered"):
                engine._playback_start_frame_rendered = False

    def _toggle_play(self):
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                if bool(getattr(ctrl, "is_playing", False)):
                    ctrl.pause()
                else:
                    if not self._require_active_program("播放时间线"):
                        return
                    self._timeline_play()
                return
        if self._engine and getattr(self._engine, "is_playing", False):
            self._pause()
        else:
            if not self._require_active_program("播放时间线"):
                return
            self._play()

    def _play(self):
        if self._use_timeline_widget and self._timeline_widget and not self._require_active_program("播放时间线"):
            return
        if self._effect_browser_widget and self._effect_browser_widget._is_playing:
            self._effect_browser_widget._on_stop()
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                self._timeline_play()
                return
        if self._engine and hasattr(self._engine, "play"):
            if not self._require_active_program("播放时间线"):
                return
            self._engine.play()

    def _pause(self):
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                ctrl.pause()
                return
        if self._engine and hasattr(self._engine, "pause"):
            self._engine.pause()

    def _stop(self):
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                ctrl.stop()
                self._transport.update_position(0.0)
                return
        if self._engine and hasattr(self._engine, "stop"):
            self._engine.stop()
        self._transport.update_position(0.0)
        if hasattr(self, "_ruler") and hasattr(self._ruler, "set_position"):
            self._ruler.set_position(0.0)

    def _rewind(self):
        self._seek(0.0)

    def _seek(self, pos: float):
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                ctrl.seek(pos)
                self._transport.update_position(pos)
                return
        if self._engine and hasattr(self._engine, "seek"):
            self._engine.seek(pos)
        self._transport.update_position(pos)
        if hasattr(self, "_ruler") and hasattr(self._ruler, "set_position"):
            self._ruler.set_position(pos)

    def _on_timecode_fps_changed(self, fps: float):
        """Save timecode_fps to project when the combo changes."""
        if self._pm is not None:
            proj = getattr(self._pm, "_project", None)
            if proj is not None:
                proj.timecode_fps = fps
                note_project_edit(self)
                self._update_title()

    def _on_timecode_received(self, hours: int, minutes: int, seconds: int, frames: int, tc_type: int):
        """Handle incoming Art-Net TimeCode packet."""
        from ..core.timecode_decode import artnet_timecode_seconds
        total_sec = artnet_timecode_seconds(hours, minutes, seconds, frames, tc_type)
        if total_sec is None:
            return
        self._status.set_timecode(hours, minutes, seconds, frames)

        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                ctrl.seek(total_sec)
                self._transport.update_position(total_sec)
                return
        self._seek(total_sec)

    # ══════════════════════════════════════════════════════════════════════════
    #  轨道管理
    # ══════════════════════════════════════════════════════════════════════════

    def _add_track(self):
        if not self._require_active_program("添加轨道"):
            return
        if self._pm and hasattr(self._pm, "add_track"):
            from ..core.models import Track
            track = Track()
            if not hasattr(track, "height"):
                track.height = 60
            if not hasattr(track, "clips"):
                track.clips = []
            self._pm.add_track(track)
            self._status.set_message(f"已添加轨道: {track.name}")
        elif self._engine and hasattr(self._engine, "add_track"):
            track = self._engine.add_track()
            self._refresh_tracks()
            self._status.set_message(f"已添加轨道: {track.name}")
        else:
            self._status.set_message("引擎未连接，无法添加轨道")

    def _on_track_deleted(self, track_id: str):
        if not self._require_active_program("删除轨道"):
            return
        if self._pm and hasattr(self._pm, "remove_track"):
            self._pm.remove_track(track_id)
        elif self._engine and hasattr(self._engine, "remove_track"):
            self._engine.remove_track(track_id)
            self._refresh_tracks()
        else:
            self._status.set_message("删除失败：引擎未连接")
            return
        if self._selected_track_id == track_id:
            self._selected_track_id = None
            self._panel_toolbar.set_del_enabled(False)
        self._status.set_message("轨道已删除")

    def _on_track_selected(self, track_id: str):
        self._selected_track_id = track_id
        self._panel_toolbar.set_del_enabled(track_id is not None)
        if hasattr(self, "_track_list") and hasattr(self._track_list, "set_selected_track"):
            self._track_list.set_selected_track(track_id)
        if self._use_timeline_widget and self._timeline_widget:
            tl = getattr(self._timeline_widget, "_track_list", None)
            if tl and hasattr(tl, "set_selected_track"):
                tl.set_selected_track(track_id)

    def _refresh_tracks(self):
        proj = self._sync_timeline_project()
        if self._use_timeline_widget and self._timeline_widget:
            if proj is None and hasattr(self._timeline_widget, "_reload_tracks"):
                # 性能(2026-08-05): 有工程时 _sync_timeline_project -> rebind_session
                #   已全量 _reload_tracks 一次, 不再重复(原每次轨道结构变更 2× 全量重建);
                #   仅当无工程(rebind 未走)时兜底刷新一次, 行为与原实现一致。
                self._timeline_widget._reload_tracks()
            return

        tracks = self._get_tracks()

        if hasattr(self, "_track_list") and hasattr(self._track_list, "refresh_tracks"):
            self._track_list.refresh_tracks(tracks)

        if not hasattr(self, "_clip_views"):
            return

        current_ids = {t.id for t in tracks}
        for tid in list(self._clip_views):
            if tid not in current_ids:
                w = self._clip_views.pop(tid)
                self._clip_rows_layout.removeWidget(w)
                w.deleteLater()

        for i, track in enumerate(tracks):
            h = getattr(track, "height", 60) or 60
            if track.id not in self._clip_views:
                if ClipView is None:
                    continue
                cv = ClipView(track, self._px_per_sec)
                cv.setFixedHeight(h)
                cv.clip_move_commit.connect(self._on_clip_moved)
                cv.clip_deleted.connect(self._on_clip_deleted)
                cv.clip_to_processor.connect(self._on_clip_to_processor)
                cv.clip_duplicated.connect(self._on_clip_duplicated)
                cv.clip_resize_commit.connect(self._on_clip_resized)
                self._clip_views[track.id] = cv
                self._clip_rows_layout.insertWidget(i, cv)
            else:
                self._clip_views[track.id].setFixedHeight(h)
                self._clip_views[track.id].refresh()

        total_h = sum((getattr(t, "height", 60) or 60) for t in tracks)
        self._clip_container.setFixedHeight(total_h)
        self._clip_container.setMinimumWidth(
            self._clip_scroll.viewport().width())

    # ══════════════════════════════════════════════════════════════════════════
    #  Clip 操作（兜底路径）
    # ══════════════════════════════════════════════════════════════════════════

    def _on_clip_moved(self, clip_id: str, new_start: float):
        if not self._require_active_program("移动时间线片段"):
            return
        for track in self._get_tracks():
            for clip in track.clips:
                if clip.id == clip_id:
                    self._snapshot_for_undo()
                    clip.start_time = new_start
                    if track.id in self._clip_views:
                        self._clip_views[track.id].refresh()
                    self.mark_dirty()
                    return

    def _on_clip_deleted(self, clip_id: str):
        if not self._require_active_program("删除时间线片段"):
            return
        for track in self._get_tracks():
            found = any(c.id == clip_id for c in track.clips)
            if not found:
                continue
            self._snapshot_for_undo()
            if hasattr(track, "remove_clip"):
                track.remove_clip(clip_id)
            else:
                track.clips = [c for c in track.clips if c.id != clip_id]
            if track.id in self._clip_views:
                self._clip_views[track.id].refresh()
            break
        self.mark_dirty()

    def _on_clip_to_processor(self, clip) -> None:
        self._lazy_load_panels(self._processor_dock)
        self._toggle_dock(self._processor_dock, True)

        pw = self._processor_widget
        if pw is None:
            import logging
            logging.getLogger(__name__).error(
                "_on_clip_to_processor: TrackLinkerPanel 仍为 None，"
                "请检查 src/ui/processor_panel/track_linker_panel.py 导入错误")
            QMessageBox.warning(
                self, "处理面板",
                "处理面板初始化失败，请检查控制台日志。"
            )
            return

        if hasattr(pw, "load_clip"):
            pw.load_clip(clip)
        if hasattr(pw, "process_done"):
            try:
                pw.process_done.disconnect(self._on_process_done)
            except Exception:
                pass
            pw.process_done.connect(self._on_process_done)

    def _clip_fixture_ids_from_active_channels(self, clip) -> list[str]:
        active_map = getattr(clip, "active_channels_by_universe", {}) or {}
        if not active_map:
            return []
        project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        fixtures = list(getattr(project, "fixtures", []) or []) if project is not None else []
        fixture_defs = {
            str(getattr(fd, "id", "") or ""): fd
            for fd in (getattr(project, "fixture_defs", []) or [])
            if str(getattr(fd, "id", "") or "")
        } if project is not None else {}
        out = []
        seen = set()
        for fi in sorted(fixtures, key=lambda f: (
                int(getattr(f, "universe", 1) or 1),
                int(getattr(f, "start_channel", 1) or 1),
                str(getattr(f, "id", "") or ""))):
            try:
                uni = int(getattr(fi, "universe", 1) or 1)
                active_channels = {int(ch) for ch in (active_map.get(uni) or active_map.get(str(uni)) or [])}
            except Exception:
                active_channels = set()
            if not active_channels:
                continue
            start = int(getattr(fi, "start_channel", 1) or 1)
            fd = fixture_defs.get(str(getattr(fi, "def_id", "") or ""))
            try:
                count = int(getattr(fd, "channel_count", 0) or 0) if fd is not None else 0
            except Exception:
                count = 0
            if count <= 0 and fd is not None:
                channels = getattr(fd, "channels", None)
                if isinstance(channels, (list, tuple)):
                    count = len(channels)
            count = max(1, count)
            span = set(range(start, min(513, start + count)))
            fid = str(getattr(fi, "id", "") or "")
            if fid and fid not in seen and active_channels.intersection(span):
                out.append(fid)
                seen.add(fid)
        return out

    def _on_clip_load_fixture_group_requested(self, clip) -> None:
        # 2026-08-12 用户要求: 多选 clip 时加载**所有**选中 clip 的设备组。
        # 语义与 _resolve_context_clip_ids 同款: 右键 clip 在选中集且选中>1 → 全选中集。
        clips = [clip] if clip is not None else []
        tw = getattr(self, "_timeline_widget", None)
        if tw is not None and clip is not None:
            sel = getattr(tw, "_selected_clip_ids", None)
            if callable(sel):
                try:
                    sids = sel()
                except Exception:
                    sids = []
                if clip.id in sids and len(sids) > 1:
                    ctrl = getattr(tw, "ctrl", None)
                    for cid in sids:
                        if cid == clip.id:
                            continue
                        try:
                            _, c = ctrl._find_track_and_clip(cid)
                        except Exception:
                            c = None
                        if c is not None:
                            clips.append(c)
        if not clips:
            return
        ids = []
        for c in clips:
            cids = [str(fid) for fid in (getattr(c, "base_order_fixture_ids", []) or []) if str(fid)]
            if not cids:
                cids = self._clip_fixture_ids_from_active_channels(c) or []
            ids.extend(cids)
        ids = self._unique_fixture_ids(ids)
        if not ids:
            self._status.set_message("选中的 Clip 没有保存可加载的设备组")
            return

        self._lazy_load_panels(getattr(self, "_topview_dock", None))
        self._lazy_load_panels(getattr(self, "_programmer_dock", None))
        self._sync_fixture_selection_to_topview(ids)
        self._load_programmer_from_topview_selection(ids)
        tv = getattr(self, "_topview_widget", None)
        if tv is not None:
            try:
                if hasattr(tv, "set_external_fixture_selection"):
                    tv.set_external_fixture_selection(ids, emit=True)
                elif hasattr(tv, "_set_selection"):
                    tv._set_selection(ids, emit=True)
            except Exception:
                pass
        self._status.set_message(f"已加载 Clip 设备组：{len(ids)} 台")

    def _on_process_done(self, src_id: str, frames: list, label: str) -> None:
        if not self._require_active_program("写入处理结果到时间线"):
            return
        if not frames:
            self._status.set_message("处理结果为空，未生成新片段")
            return

        tracks = self._get_tracks()

        src_clip = None
        pm = getattr(self, "_pm", None)
        if pm is not None:
            if hasattr(pm, "get_clip_by_id"):
                src_clip = pm.get_clip_by_id(src_id)
            elif hasattr(pm, "find_clip"):
                src_clip = pm.find_clip(src_id)
            elif hasattr(pm, "current_project"):
                proj = pm.current_project
                if proj is not None and hasattr(proj, "get_clip"):
                    src_clip = proj.get_clip(src_id)

        if src_clip is None:
            for track in tracks:
                for c in getattr(track, "clips", []):
                    if str(getattr(c, "id", "")) == src_id:
                        src_clip = c
                        break
                if src_clip is not None:
                    break

        target_track = None

        if src_clip is None:
            try:
                from ..core.models import Clip as ClipCls
            except ImportError:
                ClipCls = None

            if ClipCls is not None and tracks:
                target_track = tracks[0]
                try:
                    src_clip = ClipCls(label=label, frames=frames)
                except TypeError:
                    src_clip = ClipCls()
                    src_clip.label  = label
                    src_clip.frames = frames

                if hasattr(target_track, "add_clip"):
                    target_track.add_clip(src_clip)
                elif hasattr(target_track, "clips"):
                    target_track.clips.append(src_clip)

                QMessageBox.information(
                    self, "处理完成（提示）",
                    f"未能找到源片段（id={src_id[:8]}…），\n"
                    f"已将处理结果「{label}」作为新片段添加到第一个轨道。"
                )
            else:
                QMessageBox.warning(
                    self, "处理完成（警告）",
                    f"处理完成，但找不到源片段（id={src_id[:8]}…）且无法创建新片段。\n"
                    f"请检查 ProjectManager 是否已正确初始化。"
                )
                return
        else:
            for track in tracks:
                if any(str(getattr(c, "id", "")) == src_id
                       for c in getattr(track, "clips", [])):
                    target_track = track
                    break
            if target_track is None and tracks:
                target_track = tracks[0]

        if target_track is None:
            QMessageBox.warning(self, "处理完成（警告）",
                                "没有可用的轨道，无法插入处理结果。")
            return

        try:
            from ..core.models import Clip as ClipCls
        except ImportError:
            ClipCls = None

        insert_start: Optional[float] = None
        if src_clip is not None:
            src_start = getattr(src_clip, "start_time", None)
            src_dur   = getattr(src_clip, "duration",   None)
            if src_start is not None and src_dur is not None:
                insert_start = src_start + src_dur

        new_clip = None
        if ClipCls is not None:
            try:
                kwargs: dict = {
                    "id":     str(uuid.uuid4()),
                    "label":  label,
                    "frames": frames,
                }
                if insert_start is not None:
                    kwargs["start_time"] = insert_start
                if len(frames) > 1:
                    kwargs["duration"] = (frames[-1].timestamp
                                          - frames[0].timestamp)
                elif src_clip is not None:
                    kwargs["duration"] = getattr(src_clip, "duration", 0.1)
                new_clip = ClipCls(**kwargs)
            except TypeError:
                new_clip = ClipCls(label=label, frames=frames)

        if new_clip is None:
            QMessageBox.warning(self, "处理完成（警告）",
                                "Clip 类不可用，无法创建新片段。")
            return

        self._snapshot_for_undo()
        if hasattr(target_track, "add_clip"):
            target_track.add_clip(new_clip)
        elif hasattr(target_track, "clips"):
            target_track.clips.append(new_clip)

        if self._use_timeline_widget and self._timeline_widget:
            if hasattr(self._timeline_widget, "ctrl"):
                if hasattr(self._timeline_widget.ctrl, "tracks_changed"):
                    self._timeline_widget.ctrl.tracks_changed.emit()
            if hasattr(self._timeline_widget, "refresh"):
                self._timeline_widget.refresh()
        else:
            if target_track.id in self._clip_views:
                self._clip_views[target_track.id].refresh()

        self.mark_dirty()
        self._status.set_message(
            f"已将处理结果「{label}」（{len(frames)} 帧）添加到轨道"
            f"「{getattr(target_track, 'name', '未命名')}」",
            5000,
        )

    def _on_clip_duplicated(self, clip_id: str):
        if not self._require_active_program("复制时间线片段"):
            return
        for track in self._get_tracks():
            for clip in track.clips:
                if clip.id == clip_id:
                    self._snapshot_for_undo()
                    new_clip = clip.copy()
                    new_clip.start_time = clip.end_time() + 0.1
                    if hasattr(track, "add_clip"):
                        track.add_clip(new_clip)
                    else:
                        track.clips.append(new_clip)
                    if track.id in self._clip_views:
                        self._clip_views[track.id].refresh()
                    self.mark_dirty()
                    return

    def _on_clip_resized(self, clip_id: str, new_dur: float):
        if not self._require_active_program("调整时间线片段"):
            return
        for track in self._get_tracks():
            for clip in track.clips:
                if clip.id == clip_id:
                    self._snapshot_for_undo()
                    # 与 timeline_controller 共用**同一份** resize 语义（单一真源，
                    # 避免两条路径漂移）。见 timeline_controller.apply_clip_resize。
                    _apply_clip_resize(clip, new_dur)
                    if track.id in self._clip_views:
                        self._clip_views[track.id].refresh()
                    self.mark_dirty()
                    return

    def _sync_timeline_project(self):
        """把新版时间线控件/控制器绑定到 ProjectManager 当前 project。"""
        proj = None
        if self._pm is not None:
            proj = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        if proj is None and self._engine is not None:
            proj = getattr(self._engine, "project", None) or getattr(self._engine, "_project", None)
        tw = getattr(self, "_timeline_widget", None)
        if proj is not None and tw is not None:
            try:
                if hasattr(tw, "rebind_session"):
                    tw.rebind_session(proj)
                else:
                    tw._session = proj
                    if hasattr(tw, "ctrl"):
                        tw.ctrl._session = proj
                    if hasattr(tw, "_reload_tracks"):
                        tw._reload_tracks()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("timeline project sync failed: %s", e, exc_info=True)
        return proj

    def _snapshot_for_undo(self, domain=None):
        """打撤销快照。

        ★ 2026-09-19 用户实报（"撤销会影响错误判断上一步究竟做了什么…不应该按照
          每个页面来撤销，而是真正纪录上一步究竟精确到干了什么操作"）：
          `domain` 声明**这一步实际改了哪些工程字段**，撤销时只回滚这些字段。
          `domain=None`（默认）= 全量语义，用于结构级操作（加删轨道/节目/导入）。
          这一点很关键：只有**声明了域**的入口（clip 切分、关联器、TopView…）
          才不会在撤销时连带回滚用户在别处随后做的改动。
        """
        pm = getattr(self, "_pm", None)
        if pm is not None and hasattr(pm, "_snapshot"):
            try:
                _collect_panel_state_for(self, domain)
                pm._snapshot(domain=domain)
            except Exception:
                pass

    def _snapshot_for_undo_scoped(self, domain=None):
        """R49 性能：交互级轻快照（剥离 frames 大负重，亚秒级）。

        用于拖拽手势起点（fade/seq_delay/resize 等）——必须在首个预览变异发生前
        捕获 pre-state。任何异常回退到全量快照（宁慢勿错）。`domain` 语义同上。
        """
        pm = getattr(self, "_pm", None)
        if pm is not None and hasattr(pm, "_snapshot_scoped"):
            try:
                _collect_panel_state_for(self, domain)
                pm._snapshot_scoped(domain=domain)
                return
            except Exception:
                pass
        self._snapshot_for_undo(domain=domain)

    def _reload_panels_from_project(self, domain=None):
        """After undo/redo, every loaded panel must point at PM.project again."""
        # 面板从零重建→其内部选择状态归零。清除同步守卫,否则 TopView 发来相同选择时
        # 守卫误命中跳过,新建的 effect browser / programmer 收不到选择(留空)。
        self._last_eb_sync_sig = None
        self._last_pw_load_sig = None
        project = self._sync_timeline_project()
        if project is None:
            project = getattr(getattr(self, "_pm", None), "project", None)

        # ★ 2026-09-13 补（审计发现的 undo 缺口）：快照里字段是全的，但此前只回灌了
        #   topview_state / route_order_state，其余仓库与面板状态**从未回到内存**，
        #   于是 next save 又用"没还原的内存"覆盖刚恢复的 project 值 —— undo 对它们
        #   等于静默无效。这里一行接入集中回灌（实现在新模块，不往本类加方法）。
        try:
            from .dispatch.scoped_state_restore import restore_project_subset, restore_topview_selection
            _n1, _n2 = restore_project_subset(self, project, domain)
            import logging as _lg
            _lg.getLogger(__name__).debug(
                "undo 回灌：仓库 %d 项、面板 %d 项", _n1, _n2)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "undo 回灌失败（不影响其余 reload）", exc_info=True)

        _restore_mod_after_undo(self, project)   # ★ R183 清查②#1（P0）+②②页面版

        if self._patch_widget is not None:
            try:
                self._patch_widget._project = project   # 未绑定形态 setproj 无害
                self._patch_widget.refresh()
            except Exception: pass

        if self._topview_widget is not None:
            try:
                self._topview_widget._project = project
                if hasattr(self._topview_widget, "load_state"):
                    _restore_topview_state(self._topview_widget, project)
                elif hasattr(self._topview_widget, "refresh"):
                    self._topview_widget.refresh()
            except Exception: pass

        # ★ R172 §2.3：undo/redo 回灌 studio 状态（防"没还原的内存"覆盖刚恢复的存档）
        if self._clip_studio_widget is not None:
            self._clip_studio_widget.load_state(
                getattr(project, "clip_studio_state", {}) or {}, project, session_reset=False)

        if self._fixture_group_widget is not None:
            try:
                if hasattr(self._fixture_group_widget, "refresh"):
                    self._fixture_group_widget.refresh()
            except Exception:
                pass

        if self._programmer_widget is not None:
            try:
                if hasattr(self._programmer_widget, "_refresh_fixtures"):
                    self._programmer_widget._refresh_fixtures()
                elif hasattr(self._programmer_widget, "refresh"):
                    self._programmer_widget.refresh()
                if (self._fixture_group_widget is not None
                        and hasattr(self._programmer_widget, "sync_groups_from_panel")):
                    self._programmer_widget.sync_groups_from_panel(
                        self._fixture_group_widget)
                if self._topview_widget is not None and hasattr(
                        self._programmer_widget, "update_fixture_positions"):
                    refresh_programmer_geometry(self._programmer_widget,
                        self._topview_widget.get_fixture_positions())
            except Exception:
                pass

        if self._matrix_pattern_widget is not None:
            try:
                mw = self._matrix_pattern_widget
                if hasattr(mw, "_on_project_changed_for_matrix"):
                    mw._on_project_changed_for_matrix()
                elif hasattr(mw, "refresh_filter_group_combo"):
                    mw.refresh_filter_group_combo()
                if self._topview_widget is not None and hasattr(mw, "set_fixtures"):
                    positions = (
                        self._topview_widget.get_all_fixture_positions()
                        if hasattr(self._topview_widget, "get_all_fixture_positions")
                        else self._topview_widget.get_fixture_positions())
                    mw.set_fixtures(positions)
            except Exception:
                pass

        if self._route_order_widget is not None:
            try:
                self._route_order_widget.set_context(
                    project_manager=self._pm,
                    topview=self._topview_widget,
                    fixture_group_panel=self._fixture_group_widget,
                )
                if hasattr(self._route_order_widget, "load_state"):
                    self._route_order_widget.load_state(
                        getattr(project, "route_order_state", {}) or {})
            except Exception:
                pass

        if self._route_animator_window is not None:
            try:
                audio_path = getattr(project, "audio_path", "") or ""
                self._route_animator_window.set_context(
                    project_manager=self._pm,
                    topview=self._topview_widget,
                    audio_path=audio_path,
                )
            except Exception:
                pass

        self._refresh_tracks()
        self._ensure_topview_live_selection_bridge()
        if self._effect_browser_widget is not None:
            try:
                if hasattr(self._effect_browser_widget, "set_context"):
                    self._effect_browser_widget.set_context(
                        project=self._pm,
                        nm=self._nm,
                        topview=self._topview_widget,
                        fixture_group_panel=self._fixture_group_widget,
                        programmer_panel=self._programmer_widget,
                    )
            except Exception:
                pass
        self._sync_xy_reference()
        restore_topview_selection(self, domain)
        self._sync_motor_defaults()
        engine = getattr(self, "_engine", None)
        from .dispatch.scoped_state_restore import timeline_cache_invalidation_requested
        if timeline_cache_invalidation_requested(engine, domain):
            try:
                engine.invalidate_all_caches()
            except Exception:
                pass
        self._refresh_program_list()
        note_project_edit(self)
        try:
            self._update_title()
        except Exception:
            pass

    def _find_track_anywhere(self, track_id: str):
        """在 ProjectManager、TimelineWidget session/controller 和 TrackList header 中容错查轨道。"""
        if not track_id:
            return None
        candidates = []
        if self._pm is not None:
            candidates.append(getattr(self._pm, "project", None) or getattr(self._pm, "_project", None))
        tw = getattr(self, "_timeline_widget", None)
        if tw is not None:
            candidates.append(getattr(tw, "_session", None))
            ctrl = getattr(tw, "ctrl", None)
            candidates.append(getattr(ctrl, "_session", None))
        if self._engine is not None:
            candidates.append(getattr(self._engine, "project", None) or getattr(self._engine, "_project", None))
        seen = set()
        for proj in candidates:
            if proj is None or id(proj) in seen:
                continue
            seen.add(id(proj))
            if hasattr(proj, "get_track"):
                try:
                    t = proj.get_track(track_id)
                    if t is not None:
                        return t
                except Exception:
                    pass
            for t in getattr(proj, "tracks", []) or []:
                if str(getattr(t, "id", "")) == str(track_id):
                    return t
        # 最后兜底：旧 UI header 里可能还握着旧对象；能编辑比直接失败更好。
        for attr in ("_track_list",):
            tl = getattr(self, attr, None)
            headers = getattr(tl, "_headers", {}) if tl is not None else {}
            h = headers.get(track_id)
            if h is not None and hasattr(h, "track"):
                return h.track
        tw = getattr(self, "_timeline_widget", None)
        tl = getattr(tw, "_track_list", None) if tw is not None else None
        headers = getattr(tl, "_headers", {}) if tl is not None else {}
        h = headers.get(track_id)
        if h is not None and hasattr(h, "track"):
            return h.track
        return None

    def _get_tracks(self) -> list:
        proj = None
        if self._pm is not None:
            proj = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
        if proj is None and self._engine is not None:
            proj = getattr(self._engine, "project", None) or getattr(self._engine, "_project", None)
        if proj is not None:
            return list(getattr(proj, "tracks", []) or [])
        return []

    # ══════════════════════════════════════════════════════════════════════════
    #  录制
    # ══════════════════════════════════════════════════════════════════════════

    def _set_record_playback_running(self, running: bool, use_timeline: bool = True):
        """Record button transport coupling without invoking normal play shortcuts."""
        target = None
        if use_timeline and self._use_timeline_widget and self._timeline_widget:
            target = getattr(self._timeline_widget, "ctrl", None)
        if target is None and not use_timeline:
            target = getattr(self, "_engine", None)
        if target is None:
            return
        method_name = "play" if running else "pause"
        method = getattr(target, method_name, None)
        if not callable(method):
            return
        try:
            is_playing = bool(getattr(target, "is_playing", False))
            if running and not is_playing:
                method()
            elif not running and is_playing:
                method()
        except Exception:
            pass

    def _recordable_track_ids_for_ui(self, tracks: list) -> set:
        """复用 RecordEngine._recordable_track_ids 的 R13 排除语义(文件夹/
        祖先链静音/任意 solo 只录 solo 链), UI 侧不复制第二份判定逻辑。
        引擎不可用时回退为仅排除文件夹(旧行为)。"""
        rec = self._record_engine
        if rec is None and self._pm is not None and self._nm is not None:
            self._init_record_engine()
            rec = self._record_engine
        if rec is not None and hasattr(rec, "_recordable_track_ids"):
            try:
                return {str(tid or "") for tid in rec._recordable_track_ids(tracks)}
            except Exception:
                pass
        return {str(getattr(t, "id", "") or "") for t in tracks
                if not bool(getattr(t, "is_folder", False))}

    def _ask_record_track(self) -> Optional[str]:
        all_tracks = self._get_tracks()
        plain_ids = {str(getattr(t, "id", "") or "") for t in all_tracks
                     if not bool(getattr(t, "is_folder", False))}
        if not plain_ids:
            ec.recording_no_tracks().show(self)
            return None

        # R13 排除语义铁律(文件夹不参与 + 祖先链 + 静音优先 solo): 只显示
        # 引擎判定真实可录的轨道, 避免用户选中不可录轨道后静默录出 0 帧。
        recordable_ids = self._recordable_track_ids_for_ui(all_tracks)
        tracks = [t for t in all_tracks
                  if not bool(getattr(t, "is_folder", False))
                  and str(getattr(t, "id", "") or "") in recordable_ids]
        if not tracks:
            QMessageBox.warning(self, "录制失败",
                                "所有普通轨道均被静音或单播排除，无法录制。\n"
                                "请先解除静音/单播后再录制。")
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle("选择录制目标")
        dlg.setMinimumWidth(300)
        dlg.resize(360, dlg.sizeHint().height())
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("请选择要录制到的轨道："))
        if len(tracks) < len(plain_ids):
            lay.addWidget(QLabel(f"已隐藏 {len(plain_ids) - len(tracks)} 条被静音/单播排除的轨道。"))

        combo = QComboBox()
        for t in tracks:
            combo.addItem(f"{t.name} (U{', '.join(map(str, t.universes))})", t.id)

        if self._selected_track_id:
            idx = combo.findData(self._selected_track_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        lay.addWidget(combo)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_id = combo.currentData()
            self._on_track_selected(selected_id)
            return selected_id
        return None

    def _start_record(self):
        if not self._require_active_program("录制到时间线"):
            self._transport.set_recording(False)
            return
        self._sync_timeline_project()
        target_track_id = self._ask_record_track()
        if not target_track_id:
            self._transport.set_recording(False)
            return

        track_name = "Unknown"
        for t in self._get_tracks():
            if t.id == target_track_id:
                track_name = t.name
                break

        self._safe_console_log(
            f"User selected track '{track_name}' ({target_track_id}) for recording.")

        # ── TimelineWidget 路径 ────────────────────────────────────────────
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl and hasattr(ctrl, "start_recording"):
                # 录制由 ProgrammerPanel 的 flush/keepalive 统一喂帧，避免
                # dmx_out 信号、控件回调和轮询定时器同时生成重复完整帧。
                pw = self._programmer_widget
                if pw is not None:
                    pw._timeline_ctrl_ref = ctrl
                    # 清理旧版本可能留下的信号连接；当前热路径不再依赖 dmx_out。
                    try:
                        pw.dmx_out.disconnect(ctrl.feed_dmx_frame)
                    except Exception:
                        pass

                # 防御式停止旧轮询，避免重复进入 feed_dmx_frame。
                if hasattr(self, '_rec_poll_timer') and self._rec_poll_timer is not None:
                    try:
                        self._rec_poll_timer.stop()
                        self._rec_poll_timer.deleteLater()
                    except Exception:
                        pass
                    self._rec_poll_timer = None
                engine = getattr(self, "_engine", None)
                self._recording_has_record_sources = self._project_has_record_output_sources_for_recording()
                self._set_track_linker_timer_suspended_for_recording(True)
                if engine is not None and hasattr(engine, "clear_record_source_outputs"):
                    try:
                        engine.clear_record_source_outputs()
                    except Exception:
                        pass
                record_base_order = self._recording_base_order_fixture_ids()
                ctrl.start_recording(
                    target_track_id, base_order_fixture_ids=record_base_order,
                ); _arm_main_path_trajectory(self, ctrl)  # ★ R193/DM-01：补接轨迹层
                setattr(ctrl, "_record_editor_mix", True)
                self._transport.set_recording(True)
                self._set_record_playback_running(True, use_timeline=True)
                # ★ 2026-08-12 录制卡顿修复: 项目无任何「允许接受输出」clip 时,
                #   录制混合本来就排除 clip 层、也无 record source 可采样, 此处
                #   冷渲染整个时间线纯属浪费——数百 clip 工程实测 2.5s 主线程冻结
                #   (probe_record_lag_32 dense 场景: 冷渲染 2483ms)。仅在有
                #   record source 时渲染(为首次快照采样播种)。
                if self._recording_has_record_sources and engine is not None and hasattr(engine, "_render_position"):
                    try:
                        engine._render_position(float(getattr(ctrl, "_play_pos", 0.0) or 0.0), layer_name="clip_playback")
                    except Exception:
                        pass
                self._feed_recording_from_editor_mix()
                self._rec_poll_timer = QTimer(self)
                self._rec_poll_timer.setInterval(25)
                self._rec_poll_timer.timeout.connect(self._feed_recording_from_editor_mix)
                self._rec_poll_timer.start()

                # 立即落一帧当前 Programmer 状态，后续由 flush/keepalive 维持。
                if pw is not None:
                    try:
                        if hasattr(pw, '_flush_live_output'):
                            pw._flush_live_output()
                        if hasattr(pw, '_send_keepalive'):
                            pw._send_keepalive()
                        if hasattr(pw, '_feed_recording_frames'):
                            pw._feed_recording_frames()
                    except Exception:
                        pass

                msg = f"正在录制到轨道: {track_name}"
                self._status.set_message(msg)
                self._safe_console_log(msg)
                return

        # ── 兜底路径：RecordEngine ─────────────────────────────────────────
        if self._record_engine is None:
            self._init_record_engine()

        rec = self._record_engine
        if rec is None:
            self._status.set_message("录制模块不可用（请先连接网络和项目）")
            self._transport.set_recording(False)
            return

        track_id = target_track_id
        if track_id is None:
            tracks = self._get_tracks()
            if not tracks:
                self._status.set_message("没有可用轨道，请先添加轨道再录制")
                self._transport.set_recording(False)
                return
            track_id = tracks[0].id

        pos = 0.0
        if self._engine and hasattr(self._engine, "position"):
            pos = self._engine.position

        try:
            rec.clip_finished.disconnect(self._on_clip_finished)
        except Exception:
            pass
        rec.clip_finished.connect(self._on_clip_finished)
        if self._programmer_widget is not None and hasattr(self._programmer_widget, 'dmx_out'):
            try:
                rec.connect_to_signal(self._programmer_widget.dmx_out)
            except Exception:
                pass
        self._set_track_linker_timer_suspended_for_recording(True)
        rec.start(
            track_ids=[track_id],
            start_time=pos,
            start_position=pos,
            base_order_fixture_ids=self._recording_base_order_fixture_ids(),
        )
        self._set_record_playback_running(True, use_timeline=False)
        if self._programmer_widget is not None:
            try:
                if hasattr(self._programmer_widget, '_flush_live_output'):
                    self._programmer_widget._flush_live_output()
                if hasattr(self._programmer_widget, '_send_keepalive'):
                    self._programmer_widget._send_keepalive()
            except Exception:
                pass

        track_name = track_id
        for t in self._get_tracks():
            if t.id == track_id:
                track_name = t.name
                break
        self._status.set_message(f"录制中… 轨道: {track_name}")

    @staticmethod
    def _recording_ctrl_position(ctrl) -> float:
        if ctrl is None:
            return 0.0
        try:
            pos = float(getattr(ctrl, "_play_pos", 0.0) or 0.0)
        except Exception:
            pos = 0.0
        if bool(getattr(ctrl, "is_playing", False)):
            try:
                import time
                pos = time.monotonic() - float(getattr(ctrl, "_play_t0", time.monotonic()) or 0.0)
            except Exception:
                pass
        return max(0.0, float(pos))

    def _record_source_outputs_for_recording(self, ctrl=None) -> dict:
        engine = getattr(self, "_engine", None)
        if engine is None:
            return {}
        if not self._project_has_record_output_sources_for_recording():
            if hasattr(engine, "clear_record_source_outputs"):
                try:
                    engine.clear_record_source_outputs()
                except Exception:
                    pass
            return {}
        if bool(getattr(ctrl, "is_playing", False)):
            if hasattr(engine, "record_source_outputs_snapshot"):
                try:
                    return engine.record_source_outputs_snapshot() or {}
                except Exception:
                    return {}
        pos = self._recording_ctrl_position(ctrl)
        if hasattr(engine, "refresh_record_source_outputs_at"):
            try:
                return engine.refresh_record_source_outputs_at(pos) or {}
            except Exception:
                return {}
        if hasattr(engine, "record_source_outputs_snapshot"):
            try:
                return engine.record_source_outputs_snapshot() or {}
            except Exception:
                return {}
        return {}

    def _project_has_record_output_sources_for_recording(self) -> bool:
        cached = getattr(self, "_recording_has_record_sources", None)
        if cached is not None:
            return bool(cached)
        engine = getattr(self, "_engine", None)
        if engine is not None and hasattr(engine, "_project_has_record_output_sources"):
            try:
                return bool(engine._project_has_record_output_sources())
            except Exception:
                pass
        proj = getattr(self._pm, "project", None) if getattr(self, "_pm", None) is not None else None
        for track in getattr(proj, "tracks", []) or []:
            if bool(getattr(track, "is_folder", False)):
                continue
            for clip in getattr(track, "clips", []) or []:
                if bool(getattr(clip, "record_output_source_enabled", False)):
                    return True
        return False

    @staticmethod
    def _record_sources_any_active(record_sources) -> bool:
        """record 源中是否有任何活跃通道(决定录制 tick 是否需走全链混音)。"""
        for entry in (record_sources or {}).values():
            try:
                active = np.asarray(entry[1], dtype=bool).reshape(-1)
            except Exception:
                continue
            if np.any(active):
                return True
        return False

    def _feed_recording_from_editor_mix(self):
        from ..core.recorded_channel_masks import feed_released_domains, should_record_domain
        if not (self._use_timeline_widget and self._timeline_widget):
            return
        ctrl = getattr(self._timeline_widget, "ctrl", None)
        if ctrl is None or not bool(getattr(ctrl, "_recording", False)):
            return
        nm = getattr(self, "_nm", None)
        if nm is None:
            return
        try:
            layers = getattr(nm, "_layers", {}) or {}
            clip_layers = {"clip_playback", "clip_preview", "timeline"}
            record_source_fn = getattr(self, "_record_source_outputs_for_recording", None)
            if callable(record_source_fn):
                record_sources = record_source_fn(ctrl)
            else:
                record_sources = {}
                engine = getattr(self, "_engine", None)
                if engine is not None and hasattr(engine, "record_source_outputs_snapshot"):
                    try:
                        record_sources = engine.record_source_outputs_snapshot() or {}
                    except Exception:
                        record_sources = {}
            universes = set()
            for layer_name, uni_map in layers.items():
                if layer_name in clip_layers or not isinstance(uni_map, dict):
                    continue
                for uni, entry in uni_map.items():
                    if entry is not None and np.any(np.asarray(entry[1], dtype=bool)):
                        universes.add(int(uni))
            universes.update(int(u) for u in (record_sources or {}).keys())
            universes.update(getattr(ctrl, "_rec_active_masks", {}))
            if not universes:
                return
            # 空态跳过全链混音，只录已开始域的释放，保留源时间。
            if not self._record_sources_any_active(record_sources):
                active_now = {}
                if hasattr(nm, "compute_active_snapshot_now"):
                    active_now = nm.compute_active_snapshot_now(
                        universes={int(u) for u in universes},
                        exclude_layers=list(clip_layers),
                        active_layers=BASE_LAYERS + SHAPE_LAYERS,
                    ) or {}
                if not active_now:
                    feed_released_domains(ctrl)
                    return
            self._refresh_track_linker_for_recording(record_sources=record_sources)
            if hasattr(nm, "compute_universe_snapshots_now"):
                frames, active_maps = nm.compute_universe_snapshots_now(
                    universes={int(u) for u in universes},
                    exclude_layers=list(clip_layers),
                    apply_output_smoothing=False,
                    apply_xy_reference=True,
                    active_layers=BASE_LAYERS + SHAPE_LAYERS,
                )
                for uni in sorted(universes):
                    raw_active = active_maps.get(int(uni))
                    active = _EMPTY_BOOL_512 if raw_active is None else np.asarray(raw_active, dtype=bool)
                    active = active.reshape(-1)
                    if active.size < 512:
                        active = np.pad(active, (0, 512 - active.size), mode="constant")
                    elif active.size > 512:
                        active = active[:512]
                    raw_frame = frames.get(int(uni))
                    frame = _EMPTY_U8_512 if raw_frame is None else np.asarray(raw_frame, dtype=np.uint8)
                    frame = frame.reshape(-1)
                    if frame.size < 512:
                        frame = np.pad(frame, (0, 512 - frame.size), mode="constant")
                    elif frame.size > 512:
                        frame = frame[:512]
                    source_entry = (record_sources or {}).get(int(uni))
                    if source_entry is not None:
                        source_data, source_active = source_entry
                        source_data = np.asarray(source_data, dtype=np.uint8).reshape(-1)
                        source_active = np.asarray(source_active, dtype=bool).reshape(-1)
                        if source_data.size < 512:
                            source_data = np.pad(source_data, (0, 512 - source_data.size), mode="constant")
                        elif source_data.size > 512:
                            source_data = source_data[:512]
                        if source_active.size < 512:
                            source_active = np.pad(source_active, (0, 512 - source_active.size), mode="constant")
                        elif source_active.size > 512:
                            source_active = source_active[:512]
                        if np.any(source_active):
                            source_active = source_active & ~active
                        if np.any(source_active):
                            frame = frame.copy()
                            np.copyto(frame, source_data, where=source_active)
                            active = active | source_active
                    if not should_record_domain(ctrl, uni, active):
                        continue
                    ctrl.feed_dmx_frame(int(uni), frame, active_mask=active)
                return
            for uni in sorted(universes):
                if hasattr(nm, "compute_editor_active_mask"):
                    active = nm.compute_editor_active_mask(int(uni))
                else:
                    active = np.zeros(512, dtype=bool)
                    for layer_name, uni_map in layers.items():
                        if layer_name in clip_layers or not isinstance(uni_map, dict):
                            continue
                        entry = uni_map.get(int(uni))
                        if entry is not None:
                            active |= np.asarray(entry[1], dtype=bool)
                active = np.asarray(active, dtype=bool).reshape(-1)
                if active.size < 512:
                    active = np.pad(active, (0, 512 - active.size), mode="constant")
                elif active.size > 512:
                    active = active[:512]
                frame = nm.compute_editor_universe_now(
                    int(uni),
                    apply_output_smoothing=False,
                    apply_xy_reference=True,
                )
                source_entry = (record_sources or {}).get(int(uni))
                if source_entry is not None:
                    source_data, source_active = source_entry
                    source_data = np.asarray(source_data, dtype=np.uint8)
                    source_active = np.asarray(source_active, dtype=bool)
                    if source_data.size < 512:
                        source_data = np.pad(source_data, (0, 512 - source_data.size), mode="constant")
                    elif source_data.size > 512:
                        source_data = source_data[:512]
                    if source_active.size < 512:
                        source_active = np.pad(source_active, (0, 512 - source_active.size), mode="constant")
                    elif source_active.size > 512:
                        source_active = source_active[:512]
                    if np.any(source_active):
                        source_active = source_active & ~active
                    if np.any(source_active):
                        frame = np.asarray(frame, dtype=np.uint8).copy()
                        frame[source_active] = source_data[source_active]
                        active = active | source_active
                if not should_record_domain(ctrl, uni, active):
                    continue
                ctrl.feed_dmx_frame(int(uni), frame, active_mask=active)
        except Exception:
            # ★ 2026-08-13 thr-1: 录制帧组装失败必须可见 —— 修复前裸 except
            #   pass 让任何瞬时异常(并发 clip 列表变更/畸形 _layers 项/
            #   linker 失败)静默丢弃整个 25ms tick, 录制数据无声缺失且无
            #   任何日志可查。
            import logging
            logging.getLogger(__name__).exception("录制帧组装失败(本 tick 丢弃)")

    def _refresh_track_linker_for_recording(self, record_sources=None):
        """Force TrackLinkerEngine to publish a fresh linker layer before recording."""
        nm = getattr(self, "_nm", None)
        if nm is None:
            return
        try:
            from ..processing.track_linker_engine import get_linker_engine
        except Exception:
            try:
                from src.processing.track_linker_engine import get_linker_engine
            except Exception:
                return
        try:
            linker = get_linker_engine()
            if not bool(getattr(linker, "is_enabled", False)):
                return
            if hasattr(linker, "has_enabled_rules"):
                if not bool(linker.has_enabled_rules()):
                    return
            elif not any(bool(getattr(rule, "enabled", False)) for rule in getattr(linker, "get_rules", lambda: [])()):
                return
            if hasattr(linker, "set_network_manager"):
                # ★ 2026-08-12 录制卡顿修复: set_network_manager 内部会
                #   _invalidate_rule_caches() 清空关联器全部热路径缓存与规则
                #   时序状态(激活时间戳/时差/喷水延时)——录制轮询每 25ms 调用
                #   一次等于每 tick 全量重建, 既费时又让 stagger/spray 时序永不
                #   生效。NM 实例不变时跳过。
                if getattr(linker, "_nm", None) is not nm:
                    linker.set_network_manager(nm)
            exclude_layers = {"clip_playback", "clip_preview", "timeline"}
            if hasattr(linker, "process_once"):
                linker.process_once(exclude_layers=exclude_layers, extra_sources=record_sources)
            elif hasattr(linker, "_tick"):
                linker._tick()
        except Exception:
            pass

    def _set_track_linker_timer_suspended_for_recording(self, suspended: bool):
        current = bool(getattr(self, "_recording_suspended_track_linker_timer", False))
        if bool(suspended) == current:
            return
        try:
            from ..processing.track_linker_engine import get_linker_engine
        except Exception:
            try:
                from src.processing.track_linker_engine import get_linker_engine
            except Exception:
                return
        try:
            linker = get_linker_engine()
            if suspended:
                if hasattr(linker, "suspend_timer"):
                    linker.suspend_timer()
                else:
                    timer = getattr(linker, "_timer", None)
                    if timer is not None:
                        timer.stop()
                self._recording_suspended_track_linker_timer = True
            else:
                if hasattr(linker, "resume_timer"):
                    linker.resume_timer()
                else:
                    timer = getattr(linker, "_timer", None)
                    if timer is not None and bool(getattr(linker, "is_enabled", False)):
                        timer.start()
                self._recording_suspended_track_linker_timer = False
        except Exception:
            pass

    def _rec_poll_tick(self):
        """最简录制时钟：只录 Programmer _keepalive_data 中实际有数据的通道。
        不读 NM 混合输出，避免 release 后其他层的残留数据（如灯）被录进去。
        """
        ctrl = None
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, 'ctrl', None)
        if ctrl is None:
            return
        pw = getattr(self, '_programmer_widget', None)
        if pw is None:
            return
        keepalive_data = getattr(pw, '_keepalive_data', {}) or {}
        # 收集所有有数据的 universe，按通道合并各层
        uni_frames: dict = {}  # {uni: np.ndarray[512, uint8]}
        for layer_name, uni_map in keepalive_data.items():
            for uni, sparse in uni_map.items():
                if not sparse:
                    continue
                if uni not in uni_frames:
                    uni_frames[uni] = np.zeros(512, dtype=np.uint8)
                for addr, val in sparse.items():
                    if 0 <= addr < 512:
                        uni_frames[uni][addr] = int(val) & 0xFF
        for uni, frame in sorted(uni_frames.items()):
            try:
                ctrl.feed_dmx_frame(int(uni), frame)
            except Exception:
                pass

    def _auto_release_after_record(self):
        """录制完成后自动 release：只释放 Programmer 输出和编辑器 NM 层，保留编曲 UI。"""
        from .dispatch.record_release import release_recorded_scenes, reset_timeline_record_runtime
        release_recorded_scenes(self)
        nm = getattr(self, '_nm', None)
        # 1. 清空所有现场输出层；录制结束后必须释放 clip 播放/预览层，
        #    否则上一段 clip 的 LTP 接管会继续盖住新的泵/阀效果。
        if nm is not None:
            # ★ 2026-09-17：改用 constants 权威层名表（旧硬编码漏 matrix_audio → 常亮）
            editor_layers = ["fader", "programmer", "linker"] + list(ALL_LAYERS)
            for layer_name in editor_layers:
                try:
                    if hasattr(nm, 'clear_layer'):
                        nm.clear_layer(layer_name)
                except Exception:
                    pass
        # 2. 释放 Programmer 输出缓存（不清 UI，避免推杆/摇杆复位）
        pw = getattr(self, '_programmer_widget', None)
        if pw is not None:
            try:
                if hasattr(pw, 'clear_live_output'):
                    pw.clear_live_output()
            except Exception:
                pass
        # 3. 通知 PlaybackEngine 清除 fade_in_anchor 缓存
        engine = getattr(self, '_engine', None)
        if engine is not None and hasattr(engine, 'invalidate_all_caches'):
            try:
                reset_timeline_record_runtime(engine)
            except Exception:
                pass

    def _stop_record(self):
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl and hasattr(ctrl, "stop_recording"):
                pw = self._programmer_widget
                if hasattr(self, '_rec_poll_timer') and self._rec_poll_timer is not None:
                    try:
                        self._rec_poll_timer.stop()
                        self._rec_poll_timer.deleteLater()
                    except Exception:
                        pass
                    self._rec_poll_timer = None
                self._recording_has_record_sources = None
                if pw is not None:
                    try:
                        if hasattr(pw, '_flush_live_output'):
                            pw._flush_live_output()
                        if hasattr(pw, '_send_keepalive'):
                            pw._send_keepalive()
                        if hasattr(pw, '_feed_recording_frames'):
                            pw._feed_recording_frames()
                    except Exception:
                        pass
                self._feed_recording_from_editor_mix()
                recorded_clip = ctrl.stop_recording(); _disarm_main_path_trajectory(self, recorded_clip)  # ★ R193/DM-01
                if recorded_clip is not None:
                    try:
                        if hasattr(self._timeline_widget, "_reload_tracks"):
                            self._timeline_widget._reload_tracks()
                    except Exception:
                        pass
                    self.mark_dirty()
                if recorded_clip is not None and hasattr(self, "_engine") and self._engine is not None:
                    try:
                        rec_track, _rec_clip = ctrl._find_track_and_clip(recorded_clip.id)
                        if hasattr(self._engine, "invalidate_clip_cache"):
                            self._engine.invalidate_clip_cache(recorded_clip.id)
                        if hasattr(self._engine, "precompile_clip"):
                            self._engine.precompile_clip(recorded_clip, track=rec_track, proj=getattr(self._pm, "project", None))
                    except Exception:
                        pass
                setattr(ctrl, "_record_editor_mix", False)
                # ★ 停止录制时，断开 Programmer.dmx_out → ctrl.feed_dmx_frame 的录制链路
                if pw is not None:
                    try:
                        pw.dmx_out.disconnect(ctrl.feed_dmx_frame)
                    except Exception:
                        pass
                self._pause_live_effect_outputs()
                self._set_record_playback_running(False, use_timeline=True)
                # ★ 录制完成后自动 release：释放 Programmer 输出，保留 Programmer UI
                self._auto_release_after_record()
                self._transport.set_recording(False)
                self._recording_has_record_sources = None
                self._set_track_linker_timer_suspended_for_recording(False)
                msg = "录制已停止并保存到时间线"
                self._status.set_message(msg)
                self._safe_console_log(msg)
                return

        rec = self._record_engine
        if rec:
            rec.stop()
        self._set_record_playback_running(False, use_timeline=False)
        _finish_record_outputs(self)
        self._transport.set_recording(False)
        self._recording_has_record_sources = None
        self._set_track_linker_timer_suspended_for_recording(False)
        msg = "录制已停止"
        self._status.set_message(msg)
        self._safe_console_log(msg)

    def _on_remap_requested(self, clip_id: str, dst_track_id: str):
        if not self._require_active_program("编辑时间线片段"):
            return
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._do_remap_ask(clip_id, dst_track_id))

    def _do_remap_ask(self, clip_id: str, dst_track_id: str):
        ctrl = None
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, 'ctrl', None)
        if ctrl:
            ctrl.remap_clip_to_track(clip_id, dst_track_id)
            self._status.set_message("片段已移动到新轨道（保留完整数据）")
        return

    def _on_clip_finished(self, track_id: str, clip):
        if self._use_timeline_widget and self._timeline_widget:
            if hasattr(self._timeline_widget, "ctrl"):
                self._timeline_widget.ctrl.tracks_changed.emit()
        else:
            if track_id in self._clip_views:
                self._clip_views[track_id].refresh()

        self.mark_dirty()
        self._status.set_message(
            f"录制完成: {clip.label}  ({clip.frame_count()} 帧)")

    # ══════════════════════════════════════════════════════════════════════════
    #  过滤器编辑
    # ══════════════════════════════════════════════════════════════════════════

    def _on_filter_edit(self, track_id: str):
        """编辑轨道过滤器。

        关键修复：过滤器按钮可能来自复用的 TrackHeader，项目切换或重新绑定后，
        旧逻辑只查 ProjectManager.project，查不到时直接报"未找到轨道"。这里先把
        TimelineWidget/Controller 重新绑定到当前 ProjectManager.project，再走多路容错
        查找，确保 UI header、timeline session 和播放引擎面对同一组 Track。
        """
        if not self._require_active_program("编辑轨道过滤器"):
            return
        self._sync_timeline_project()
        track = self._find_track_anywhere(track_id)

        if track is None:
            def _ids_from_project(proj):
                if proj is None:
                    return []
                return [str(getattr(t, "id", "")) for t in (getattr(proj, "tracks", []) or [])]

            pm_proj = None
            if self._pm is not None:
                pm_proj = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
            tw = getattr(self, "_timeline_widget", None)
            tw_proj = getattr(tw, "_session", None) if tw is not None else None
            ctrl = getattr(tw, "ctrl", None) if tw is not None else None
            ctrl_proj = getattr(ctrl, "_session", None) if ctrl is not None else None
            header_ids = []
            tl = getattr(tw, "_track_list", None) if tw is not None else getattr(self, "_track_list", None)
            if tl is not None:
                header_ids = [str(k) for k in getattr(tl, "_headers", {}).keys()]

            detail = (
                f"未找到轨道 {track_id}；"
                f"ProjectManager轨道={_ids_from_project(pm_proj)}；"
                f"TimelineWidget轨道={_ids_from_project(tw_proj)}；"
                f"Controller轨道={_ids_from_project(ctrl_proj)}；"
                f"Header轨道={header_ids}"
            )
            self._status.set_message(detail)
            print(f"[TimelineFilter] {detail}")
            return

        import copy as _copy
        edit_track = _copy.deepcopy(track)
        dlg = FilterEditDialog(edit_track, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._snapshot_for_undo()
            for attr in ("universes", "universe_filters", "filter_rule"):
                try:
                    setattr(track, attr, _copy.deepcopy(getattr(edit_track, attr)))
                except Exception:
                    pass
            if self._pm and hasattr(self._pm, "_mark_dirty"):
                self._pm._mark_dirty()
            self.mark_dirty()
            label = (track.filter_label()
                     if hasattr(track, "filter_label") else track.name)
            self._status.set_message(f"过滤规则已更新: {label}")
            self._refresh_tracks()


    # ══════════════════════════════════════════════════════════════════════════
    #  缩放 / 编辑
    # ══════════════════════════════════════════════════════════════════════════

    def _delete_selected(self):
        if self._selected_track_id:
            self._on_track_deleted(self._selected_track_id)
        else:
            self._status.set_message("请先在左侧选中一条轨道再删除")

    def _select_all(self):
        """统一接入 Ctrl+A：优先选择当前可见/可用面板中的对象，而不是空提示。"""
        handled = False
        tv = getattr(self, "_topview_widget", None)
        if tv is not None and getattr(self, "_topview_dock", None) is not None and self._topview_dock.isVisible():
            try:
                # ★ 2026-08-05 修复（联动一致性）：直接写 selected_ids 会绕过
                #   _selection_order_ids（emit 只取 order∩selected）——先有旧选择
                #   时 emit 集 ⊂ 高亮集 → Programmer 载入 ≠ 选中集（高亮对但输出错）。
                #   改用面板自身一致的 API：先清空（base 回退整页设备序），再
                #   「全部」包级单元全选（包=整体、成员全展开，气爆页 210 包也能全选）。
                tv._clear_selection(emit=False)
                tv.apply_selection_preset("all")
                tv._canvas.update()
                handled = True
                self._status.set_message("已全选 TopView 当前页面设备")
            except Exception:
                handled = False
        if not handled and self._use_timeline_widget and self._timeline_widget:
            try:
                tracks = self._get_tracks()
                if tracks:
                    self._on_track_selected(tracks[0].id)
                    handled = True
                    self._status.set_message("已选中第一条轨道；多选轨道请在轨道区操作")
            except Exception:
                handled = False
        if not handled:
            self._status.set_message("当前面板没有可全选对象")

    def _zoom_in(self):
        self._adjust_zoom(1.25)

    def _zoom_out(self):
        self._adjust_zoom(0.8)

    def _zoom_fit(self):
        self._px_per_sec = 100.0
        self._apply_zoom()
        from .timeline.timeline_operating_preferences import remember_main_zoom
        remember_main_zoom(self)

    def _adjust_zoom(self, factor: float):
        # ★ 2026-08-13 W1: 快捷键缩放先同步滚轮端基数 —— 修复前 Ctrl+滚轮
        #   只改 TimelineController.px_per_sec 不回写 self._px_per_sec, 滚轮
        #   缩放到任意倍率后按 +/- 用陈旧基数跳回错误倍率(与「以播放指针
        #   为中心缩放」双入口预期相反)。
        if self._use_timeline_widget and self._timeline_widget:
            ctrl = getattr(self._timeline_widget, "ctrl", None)
            if ctrl is not None:
                live = getattr(ctrl, "px_per_sec", None)
                if live:
                    self._px_per_sec = float(live)
        self._px_per_sec = max(10.0, min(2000.0, self._px_per_sec * factor))
        # ★ 2026-08-11 用户要求：缩放以当前播放指针为中心（指针保持不动）
        self._apply_zoom(centered=True)
        from .timeline.timeline_operating_preferences import remember_main_zoom
        remember_main_zoom(self)

    def _apply_zoom(self, centered: bool = False):
        if self._use_timeline_widget and self._timeline_widget:
            if hasattr(self._timeline_widget, "ctrl"):
                if centered and hasattr(self._timeline_widget, "set_zoom_centered"):
                    self._timeline_widget.set_zoom_centered(self._px_per_sec)
                else:
                    self._timeline_widget.ctrl.set_zoom(self._px_per_sec)
        else:
            if hasattr(self, "_ruler") and hasattr(self._ruler, "set_px_per_sec"):
                self._ruler.set_px_per_sec(self._px_per_sec)
            for cv in self._clip_views.values():
                if hasattr(cv, "set_px_per_sec"):
                    cv.set_px_per_sec(self._px_per_sec)

    # ══════════════════════════════════════════════════════════════════════════
    #  项目 I/O
    # ══════════════════════════════════════════════════════════════════════════

    def _timeline_audio_track(self):
        tw = getattr(self, "_timeline_widget", None)
        if tw is None:
            return None
        return getattr(tw, "_audio_track", None)

    def _timeline_audio_mod_track(self):
        """音频调制轨 widget（R174#7；timeline 未建时 None）。"""
        tw = getattr(self, "_timeline_widget", None)
        if tw is None:
            return None
        return getattr(tw, "_audio_mod_track", None)


    def _current_timeline_audio_path(self) -> str:
        at = self._timeline_audio_track()
        if at is None:
            return ""
        path = ""
        try:
            path = getattr(at, "audio_path", "") or ""
        except Exception:
            path = ""
        if not path:
            path = getattr(at, "_audio_path", "") or ""
        return os.fspath(path) if path else ""

    def _clear_timeline_audio(self):
        at = self._timeline_audio_track()
        if at is None:
            return
        try:
            if hasattr(at, "clear_audio"):
                at.clear_audio()
            elif hasattr(at, "_remove_audio"):
                at._remove_audio()
        except Exception:
            pass
        _restore_audio_mod_state(self, None)   # 音频删除 → 调制轨复位

    def _resolve_project_audio_path(self, project_path: str, audio_path: str) -> Path:
        """解析音频路径：绝对直接用；相对 → 项目目录 → 父目录 → 资源目录兜底。

        ★★ R120：相对基准**统一为「.dmxt 文件所在目录」**，并加三级兜底以修复
          历史损坏路径（自动保存曾把 program.audio_path 改写成相对 autosave
          目录的路径 → 重开工程报「音频缺失」）。兜底命中时会由调用方把修好的
          路径回写，使工程**自愈**，用户不必重新导入音频。
        """
        p = Path(os.path.expanduser(os.fspath(audio_path)))
        if p.is_absolute():
            return p
        base = Path(project_path).expanduser().parent
        direct = base / p
        if direct.is_file():
            return direct
        # 兜底 1：父目录（自动保存写在 <项目>/autosave/ 子目录时的相对基准）
        parent_cand = base.parent / p
        if parent_cand.is_file():
            return parent_cand
        # 兜底 2/3：按文件名在项目资源目录里找回（历史损坏路径自愈）
        found = _find_audio_asset_under(base, p.name)
        if found is None:
            found = _find_audio_asset_under(base.parent, p.name)
        return found if found is not None else direct

    def _bundle_audio_path_for_save(self, project_path: str, audio_path: str, label: str = "时间线",
                                    show_errors: bool = True) -> tuple[bool, str]:
        if not audio_path:
            return True, ""

        project_file = _audio_assets_base_file(Path(project_path).expanduser(), self._project_path)
        project_dir = project_file.parent
        try:
            src = Path(os.path.expanduser(audio_path))
            if not src.is_absolute():
                base_dir = Path(self._project_path).expanduser().parent if self._project_path else project_dir
                src = base_dir / src
            src = src.resolve()

            if not src.is_file():
                # ★ R120b：原始文件不可用 → 复用工程内已有打包副本 / 保持原路径
                healed, msg = _missing_audio_fallback(
                    self._resolve_project_audio_path, project_file, audio_path,
                    label, show_errors)
                if msg:
                    if show_errors:
                        QMessageBox.warning(self, "保存音频失败", msg)
                    else:
                        self._status.set_message(msg)
                return True, healed

            asset_dir = project_file.parent / f"{project_file.stem}_assets" / "audio"
            asset_dir.mkdir(parents=True, exist_ok=True)

            try:
                src_in_asset_dir = src.parent.resolve() == asset_dir.resolve()
            except Exception:
                src_in_asset_dir = False
            dest = src if (src_in_asset_dir and len(src.name) <= 80) \
                else asset_dir / self._bundled_audio_file_name(src)
            try:
                copy_needed = not (dest.exists() and os.path.samefile(src, dest))
            except Exception:
                copy_needed = True
            if copy_needed and dest.exists():
                try:
                    copy_needed = src.stat().st_size != dest.stat().st_size
                except Exception:
                    copy_needed = True
                if copy_needed:
                    dest = self._unique_file_path(dest)
            if copy_needed:
                shutil.copy2(src, dest)
                _invalidate_audio_asset_lookup()   # R188：资产目录已变化
            try:
                rel_path = os.path.relpath(str(dest), start=str(project_dir))
            except ValueError:
                rel_path = str(dest)
            return True, rel_path.replace(os.sep, "/")
        except Exception as e:
            if show_errors:
                QMessageBox.critical(
                    self, "保存音频失败",
                    f"无法把{label}音频复制到项目资源目录：\n{audio_path}\n\n{e}"
                )
            else:
                self._status.set_message(f"自动保存失败：无法复制{label}音频")
            return False, audio_path

    def _bundle_timeline_audio_for_save(self, project_path: str, show_errors: bool = True) -> bool:
        if self._pm is None or self._pm.project is None:
            return True

        project = self._ensure_project_program_fields(self._pm.project)
        self._save_current_timeline_to_program()
        active = self._active_program(project)
        if active is None and not getattr(project, "programs", None):
            audio_path = self._current_timeline_audio_path()
            ok, stored = self._bundle_audio_path_for_save(project_path, audio_path, "时间线", show_errors)
            project.audio_path = stored if ok else audio_path
            at = self._timeline_audio_track()
            try:
                project.audio_clip_state = dict(getattr(at, "audio_clip_state", {}) or {})
            except Exception:
                project.audio_clip_state = {}
            return ok

        ok_all = True
        for program in getattr(project, "programs", []) or []:
            label = f"节目「{getattr(program, 'name', '未命名节目')}」"
            ok, stored = self._bundle_audio_path_for_save(
                project_path,
                getattr(program, "audio_path", "") or "",
                label,
                show_errors,
            )
            if ok:
                program.audio_path = stored
                if active is not None and str(getattr(active, "id", "")) == str(getattr(program, "id", "")):
                    project.audio_path = stored
            else:
                ok_all = False
                break
        if active is None:
            project.audio_path = ""
        return ok_all

    def _restore_timeline_audio_for_project(self, project_path: str) -> str:
        if self._pm is None or self._pm.project is None:
            return ""
        at = self._timeline_audio_track()
        if at is None:
            return ""

        stored_path = getattr(self._pm.project, "audio_path", "") or ""
        if not stored_path:
            return _clear_audio_for_project_restore(self, self._pm.project)
        stored_path = _heal_stored_audio_path(
            self._resolve_project_audio_path, project_path, stored_path)
        audio_file = self._resolve_project_audio_path(project_path, stored_path)
        if not audio_file.is_file():
            _clear_audio_for_project_restore(self, self._pm.project)
            return f"（音频文件缺失: {stored_path}）"
        try:
            ok = False
            if hasattr(at, "load_audio_file"):
                ok = bool(at.load_audio_file(str(audio_file)))
            elif hasattr(at, "_load_audio"):
                at._load_audio(str(audio_file))
                ok = True
            if ok:
                self._pm.project.audio_path = stored_path
                try:
                    at.set_audio_clip_state(getattr(self._pm.project, "audio_clip_state", {}) or {})
                except Exception:
                    pass
                _restore_audio_mod_after_audio_load(self)   # 审计修 P0（helper 模块级）
                return f"（已自动导入音频: {audio_file.name}）"
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "_restore_timeline_audio_for_project failed: %s", e, exc_info=True)
        return "（音频自动导入失败）"

    def _rebuild_safety_interlock(self):
        """按当前工程配接重建硬件安全联锁 (SafetyInterlock) 约束剖面。

        加载/导入成功后 ARM，新建空项目(无设备) DISARM。约束语义保守：
        全程 0-255 全范围(min=0/max=255/max_change_rate=255/safe_default=0)，
        保证不误钳正常演出值(电机 0-255 全行程、每帧最大 255 变化均允许)，
        仅对真正越界(<0 或 >255，如引擎异常写出 float 溢出)钳位到合法 DMX 域。

        设备级「类型上限」不收紧：FixtureDefinition 无逐通道 DMX 上限
        (motor_ranges 是角度域、channel_modes 是功能模式区间)，误用会误钳
        演出值并触发黑场。process() 对每宇宙做 O(约束数) 全表扫描，且 CE
        挂钩每帧重建 O(n) 数组 —— 至冬7.0(11382 台 × ~73 域)逐台注册会把
        实时 tick 拖到 ~数百 ms/帧，故超预算时退化为「每宇宙一条粗粒度约束」
        (整域 512 通道全范围)，约束非空、保护仍全局生效、扫描成本有界。
        """
        from ..engine.safety_interlock import get_interlock, make_device_constraint
        scan_us = 1.0          # 每约束每宇宙实测 ~0.65µs(Python)/更高(CE 挂钩)
        budget_us = 2000.0     # 最坏(全宇宙脏)每帧联锁扫描预算 2ms
        ilock = get_interlock()
        proj = self._project()
        if proj is None:
            ilock.disarm()
            ilock.clear_constraints()
            return
        fixtures = list(getattr(proj, "fixtures", None) or [])
        defs_by_id = {str(fd.id): fd for fd in (getattr(proj, "fixture_defs", None) or [])}
        universes = sorted({int(getattr(f, "universe", 1) or 1) for f in fixtures})
        ilock.disarm()
        ilock.clear_constraints()
        if not fixtures:
            return
        per_device = len(fixtures) * max(1, len(universes)) * scan_us <= budget_us
        if per_device:
            for fi in fixtures:
                fd = defs_by_id.get(str(getattr(fi, "def_id", "") or ""))
                nch = int(getattr(fd, "channel_count", 0) or 0) if fd is not None else 0
                if nch <= 0:
                    nch = len(getattr(fd, "channels", None) or []) if fd is not None else 0
                if nch <= 0:
                    nch = 1
                uni = int(getattr(fi, "universe", 1) or 1)
                start = int(getattr(fi, "start_channel", 1) or 1) - 1
                if start < 0 or start >= 512:
                    continue
                nch = min(nch, 512 - start)
                ilock.add_constraint(make_device_constraint(
                    name=str(getattr(fi, "name", "") or getattr(fi, "id", "")),
                    universe=uni,
                    start_channel=start,
                    num_channels=nch,
                    min_val=0.0,
                    max_val=255.0,
                    max_change=255.0,
                    safe_default_val=0.0,
                ))
        else:
            for uni in universes:
                ilock.add_constraint(make_device_constraint(
                    name=f"universe-{uni}",
                    universe=uni,
                    start_channel=0,
                    num_channels=512,
                    min_val=0.0,
                    max_val=255.0,
                    max_change=255.0,
                    safe_default_val=0.0,
                ))
        ilock.arm()

    def _new_project(self):
        _reset_dispatch_singletons(self)   # 新建前清调度台单例（见函数）
        reply = QMessageBox.question(
            self, "新建项目",
            "新建项目前是否保存当前项目？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.Save:
            # ★ R193/SV-01：保存已改异步入队（D01），本栈内 `_dirty` 必然仍为
            #   True——同步判 dirty 会把"选了保存"的新建永远中止。改为：有路径
            #   直接入队（保存会话带身份/打戳校验保护后续文档）；无路径看
            #   _save_as 是否真的发起（取消另存为才中止）。
            if self._project_path:
                self._do_save(self._project_path)
            elif not self._save_as():
                return

        # ★ 彻底重置：在 ProjectManager 上替换为全新的空项目对象
        import logging as _log
        from ..core.models import Project as _Project
        new_proj = _Project()
        if self._pm is not None:
            # 直接替换内部 project，同时清空撤销栈
            self._pm._project = new_proj
            self._pm._undo_stack = []
            self._pm._redo_stack = []
            self._pm._dirty = False
            self._pm._current_path = None
            # 触发 project_changed 信号，通知所有订阅者
            try:
                self._pm.project_changed.emit()
            except Exception:
                pass
        elif self._engine is not None:
            # fallback: 引擎上直接替换
            try:
                self._engine._project = new_proj
            except Exception:
                pass

        # ★ 重置主窗口状态
        self._project_path = ""
        self._dirty = False
        self._update_title()

        # Reset TC FPS combo to default (new project has timecode_fps=25)
        if hasattr(self._transport, "set_tc_fps"):
            self._transport.set_tc_fps(25.0)

        # ★ 重新绑定时间线并刷新轨道
        self._sync_timeline_project()
        self._refresh_tracks()
        self._clear_timeline_audio()
        self._close_current_program_to_empty(save_current=False)

        # ★ 重置各面板（与 _load_project 完全对齐）
        self._sync_xy_reference()
        if self._patch_widget is not None:
            try:
                if hasattr(self._patch_widget, '_project'):
                    try:
                        self._patch_widget._project.project_changed.disconnect(
                            self._patch_widget.refresh)
                    except Exception:
                        pass
                    self._patch_widget._project = new_proj
                    new_proj.project_changed.connect(self._patch_widget.refresh)
                if hasattr(self._patch_widget, 'refresh'):
                    self._patch_widget.refresh()
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: PatchPanel refresh failed: %s", e)

        if self._topview_widget is not None:
            try:
                self._topview_widget._project = new_proj
                self._topview_widget.load_state({})
                self._topview_widget.refresh()
                self._sync_xy_reference()
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: TopViewPanel refresh failed: %s", e)

        if self._clip_studio_widget is not None:      # R172 §2.3：换新工程即清空（load_state 归一容错）
            self._clip_studio_widget.load_state({}, new_proj)

        if self._programmer_widget is not None:
            try:
                if hasattr(self._programmer_widget, 'reset_project_state'):
                    self._programmer_widget.reset_project_state()
                if hasattr(self._programmer_widget, '_refresh_fixtures'):
                    self._programmer_widget._refresh_fixtures()
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: ProgrammerPanel refresh failed: %s", e)

        if self._fixture_group_widget is not None:
            try:
                if hasattr(self._fixture_group_widget, 'refresh'):
                    self._fixture_group_widget.refresh()
                elif hasattr(self._fixture_group_widget, '_refresh_fixtures'):
                    self._fixture_group_widget._refresh_fixtures()
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: FixtureGroupPanel refresh failed: %s", e)

        if self._matrix_pattern_widget is not None:
            try:
                mw = self._matrix_pattern_widget
                if hasattr(mw, '_on_project_changed_for_matrix'):
                    mw._on_project_changed_for_matrix()
                if hasattr(mw, 'refresh_filter_group_combo'):
                    mw.refresh_filter_group_combo()
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: MatrixPatternPanel refresh failed: %s", e)

        if self._processor_widget is not None:
            try:
                if hasattr(self._processor_widget, 'load_state'):
                    self._processor_widget.load_state({})
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: TrackLinkerPanel reset failed: %s", e)

        if self._effect_browser_widget is not None:
            try:
                if hasattr(self._effect_browser_widget, 'load_state'):
                    self._effect_browser_widget.load_state({})
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: EffectBrowser reset failed: %s", e)

        if self._route_order_widget is not None:
            try:
                if hasattr(self._route_order_widget, 'set_context'):
                    self._route_order_widget.set_context(
                        project_manager=self._pm,
                        topview=self._topview_widget,
                        fixture_group_panel=self._fixture_group_widget,
                    )
                if hasattr(self._route_order_widget, 'load_state'):
                    self._route_order_widget.load_state({})
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: RouteOrderPanel reset failed: %s", e)

        if self._route_animator_window is not None:
            try:
                if hasattr(self._route_animator_window, 'load_state'):
                    self._route_animator_window.load_state({})
                if hasattr(self._route_animator_window, 'set_context'):
                    self._route_animator_window.set_context(
                        project_manager=self._pm,
                        topview=self._topview_widget,
                        audio_path="",
                    )
            except Exception as e:
                _log.getLogger(__name__).warning("_new_project: RouteAnimatorWindow reset failed: %s", e)

        # ★ 重置播放头到起始位置
        try:
            tw = getattr(self, "_timeline_widget", None)
            if tw is not None:
                if hasattr(tw, "ctrl") and hasattr(tw.ctrl, "seek"):
                    tw.ctrl.seek(0.0)
                elif hasattr(tw, "seek"):
                    tw.seek(0.0)
        except Exception:
            pass

        self._rebuild_safety_interlock()
        self._status.set_message("新建项目完成，已还原到初始状态")

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目", "",
            "雾白纪 ULTRA 项目 (*.dmxt);;所有文件 (*)")
        if path:
            self._load_project(path)

    @defer_bake_during_load
    def _load_project(self, path: str):
        _invalidate_audio_asset_lookup()        # R188：换工程重新查找音频资产
        if not self._confirm_discard_for_project_change(): return  # ★ 未保存改动保护
        if self._pm is None: self._resolve_pm()
        if self._pm is None:
            QMessageBox.critical(self, "打开失败",
                                 "ProjectManager 未初始化。\n请检查控制台错误输出。")
            return
        ok = self._pm.load(path)
        if ok:
            self._release_all_outputs()
            self._project_path = path
            self._dirty = False
            new_proj = self._pm.project
            # P3-1(审计): 老 .dmxt 未持久化工程名 → 构造默认名时用文件名回填
            if new_proj is not None:
                _name = str(getattr(new_proj, "name", "") or "").strip().lower()
                if _name in ("", "untitled project", "untitled"):
                    try:
                        new_proj.name = os.path.splitext(os.path.basename(path))[0]
                    except Exception:
                        pass
            self._update_title()
            # Sync timecode FPS combo with loaded project setting
            tc_fps = float(getattr(new_proj, "timecode_fps", 25.0) or 25.0)
            if hasattr(self._transport, "set_tc_fps"):
                self._transport.set_tc_fps(tc_fps)
            linker_state = getattr(new_proj, 'processor_state', None) or getattr(new_proj, 'linker_state', None) or {}
            self._load_track_linker_state_to_engine(linker_state)
            self._sync_motor_defaults()
            self._sync_timeline_project()
            self._refresh_tracks()
            active_program = self._active_program(new_proj)
            if active_program is not None:
                audio_status = self._restore_timeline_audio_for_current_sheet(path)
                self._reset_timeline_head()
                self._invalidate_timeline_program_cache()
                self._refresh_program_list()
            else:
                self._close_current_program_to_empty(save_current=False)
                audio_status = ""
            if self._patch_widget is not None:
                try:
                    if hasattr(self._patch_widget, '_project'):
                        try:
                            self._patch_widget._project.project_changed.disconnect(
                                self._patch_widget.refresh)
                        except Exception:
                            pass
                        self._patch_widget._project = new_proj
                        new_proj.project_changed.connect(self._patch_widget.refresh)
                    if hasattr(self._patch_widget, 'refresh'):
                        self._patch_widget.refresh()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: PatchPanel refresh failed: %s", e)
            if self._topview_widget is not None:
                try:
                    if hasattr(self._topview_widget, '_project'):
                        self._topview_widget._project = new_proj
                    if hasattr(self._topview_widget, 'load_state'):
                        _restore_topview_state(self._topview_widget, new_proj)
                    if hasattr(self._topview_widget, 'refresh'):
                        self._topview_widget.refresh()
                    self._sync_xy_reference()
                    # 2026-08-09 打开已有工程兜底：旧工程 topview_state 缺 XY 同步
                    # cfg 时自动初始化（cfg 已存在时 ensure 内部不覆盖，零影响）。
                    _ensure = getattr(self._topview_widget, "ensure_xy_reference_initialized", None)
                    if callable(_ensure):
                        try:
                            if _ensure() > 0:
                                # ★ 与导入尾部同款指纹缓存兜底：ensure 后显式
                                #   失效 + 重建，保证 nm 运行时 map 拿到新 cfg。
                                self._xy_ref_sig = None
                                self._sync_xy_reference()
                        except Exception as _e:
                            import logging
                            logging.getLogger(__name__).warning("XY 同步基准自动初始化失败: %s", _e)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: TopViewPanel refresh/load_state failed: %s", e)
            # ★ R172 §2.3：Studio 面板随工程切换灌入（load_state 首行记 _hydrated_for 守卫）
            if self._clip_studio_widget is not None:
                self._clip_studio_widget.load_state(
                    getattr(new_proj, 'clip_studio_state', {}) or {}, new_proj)
            if self._programmer_widget is not None:
                try:
                    if hasattr(self._programmer_widget, '_refresh_fixtures'):
                        self._programmer_widget._refresh_fixtures()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: ProgrammerPanel refresh failed: %s", e)
            if self._fixture_group_widget is not None:
                try:
                    if hasattr(self._fixture_group_widget, 'refresh'):
                        self._fixture_group_widget.refresh()
                    elif hasattr(self._fixture_group_widget, '_refresh_fixtures'):
                        self._fixture_group_widget._refresh_fixtures()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: FixtureGroupPanel refresh failed: %s", e)
            if self._matrix_pattern_widget is not None:
                try:
                    # ★ 修复：统一使用 _matrix_pattern_widget，废弃旧 _matrix_widget 引用
                    # 优先用 get_all_fixture_positions（跨所有 Sheet），TopView 未加载时兜底从 PM
                    mw = self._matrix_pattern_widget
                    positions = []
                    if self._topview_widget is not None:
                        if hasattr(self._topview_widget, 'get_all_fixture_positions'):
                            positions = self._topview_widget.get_all_fixture_positions()
                        else:
                            positions = self._topview_widget.get_fixture_positions()
                    if hasattr(mw, '_on_project_changed_for_matrix'):
                        mw._on_project_changed_for_matrix()
                    if positions and hasattr(mw, 'set_fixtures'):
                        mw.set_fixtures(positions)
                    if hasattr(mw, 'refresh_filter_group_combo'):
                        mw.refresh_filter_group_combo()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: MatrixPatternPanel refresh failed: %s", e)
            # 轨道关联器状态加载
            if self._processor_widget is not None:
                try:
                    _extracted = self._extract_track_linker_state(linker_state)
                    if _extracted and hasattr(self._processor_widget, 'load_state'):
                        self._processor_widget.load_state(_extracted)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: TrackLinkerPanel load_state failed: %s", e)
            # v25: 恢复音频桥接器状态
            try:
                bridge_state = getattr(new_proj, 'audio_bridge_state', None)
                if bridge_state:
                    from src.audio import get_global_bridge
                    bridge = get_global_bridge()
                    if bridge:
                        bridge.load_dict(bridge_state)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("_load_project: AudioBridge load_state failed: %s", e)
            # v25: 恢复矩阵幻灯片面板状态
            if self._matrix_pattern_widget is not None:
                try:
                    matrix_state = getattr(new_proj, 'matrix_slideshow_state', None)
                    if matrix_state and hasattr(self._matrix_pattern_widget, 'load_state'):
                        self._matrix_pattern_widget.load_state(matrix_state)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: MatrixPatternPanel load_state failed: %s", e)
            # v25: 恢复音频驱动参数
            if self._programmer_widget is not None:
                try:
                    audio_state = getattr(new_proj, 'audio_driver_state', None)
                    if audio_state and hasattr(self._programmer_widget, 'load_audio_driver_state'):
                        self._programmer_widget.load_audio_driver_state(audio_state)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: AudioDriver load_state failed: %s", e)
            # v25: 恢复效果浏览器状态
            if self._effect_browser_widget is not None:
                try:
                    effect_state = getattr(new_proj, 'effect_browser_state', None)
                    if effect_state and hasattr(self._effect_browser_widget, 'load_state'):
                        self._effect_browser_widget.load_state(effect_state)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: EffectBrowser load_state failed: %s", e)
            if self._route_order_widget is not None:
                try:
                    if hasattr(self._route_order_widget, 'set_context'):
                        self._route_order_widget.set_context(
                            project_manager=self._pm,
                            topview=self._topview_widget,
                            fixture_group_panel=self._fixture_group_widget,
                        )
                    if hasattr(self._route_order_widget, 'load_state'):
                        self._route_order_widget.load_state(getattr(new_proj, 'route_order_state', {}) or {})
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: RouteOrderPanel load_state failed: %s", e)
            if self._route_animator_window is not None:
                try:
                    if hasattr(self._route_animator_window, 'set_context'):
                        self._route_animator_window.set_context(
                            project_manager=self._pm,
                            topview=self._topview_widget,
                            audio_path=getattr(new_proj, 'audio_path', '') or ''
                        )
                    anim_state = getattr(new_proj, "route_animator_state", {}) or {}
                    if anim_state and hasattr(self._route_animator_window, "load_state"):
                        self._route_animator_window.load_state(anim_state)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("_load_project: RouteAnimatorWindow load_state failed: %s", e)
            # 调度台：效果缓存库随项目载入（老工程无此键 → 空库）
            try:
                _load_dispatch_library(self, new_proj)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("_load_project: dispatch caches load failed: %s", e)
            # ★ Programmer 面板状态（含条带形状）随工程载入（2026-09-13 加）
            try:
                _ps = getattr(new_proj, "programmer_state", None) or {}
                if _ps and self._programmer_widget is not None:
                    from .project_selection_restore import restore_programmer_state
                    restore_programmer_state(self._programmer_widget, _ps)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("_load_project: programmer_state load failed: %s", e)
            if active_program is not None:
                self._status.set_message(
                    f"已打开: {path}（已自动打开节目: {getattr(active_program, 'name', '未命名节目')}）{audio_status}"
                )
            else:
                self._status.set_message(f"已打开: {path}{audio_status}（请选择节目打开时间线）")
            _apply_network_config_to_panel(self)   # R182 P1-9（helper 见模块级）
            # 新功能 store ← project（Programmer CUE / 参数包络 / 自定义效果库）
            try:
                from .programmer_cue_store import get_programmer_cue_store
                get_programmer_cue_store().from_dict(getattr(new_proj, 'programmer_cues_data', {}) or {})
            except Exception:
                pass
            try:
                from .param_envelope import get_param_envelope_store
                get_param_envelope_store().from_dict(getattr(new_proj, 'param_envelopes_data', {}) or {})
            except Exception:
                pass
            try:
                from .trajectory_curve import get_trajectory_curve_store
                get_trajectory_curve_store().from_dict(getattr(new_proj, 'trajectory_curves_data', {}) or {})
            except Exception:
                pass
            try:
                # ★ R178：自定义效果库已删除 —— 装载点一次性迁移（见 custom_effect_migration）
                from .dispatch.custom_effect_migration import run_migration_ui
                run_migration_ui(self, new_proj)
            except Exception:
                pass
            # 自动编曲预烘焙方案 ← project（绑定音频，换歌失效）
            try:
                from ..engine.auto_arrange.baked_arrangement import get_baked_arrangement_store
                get_baked_arrangement_store().from_dict(getattr(new_proj, 'baked_arrangement_data', {}) or {})
            except Exception:
                pass
            # 刷新相关面板 UI，使加载的 store 内容立即可见
            try:
                eb = self._effect_browser_widget
                if eb is not None:
                    if hasattr(eb, '_refresh_envelope_page'):
                        eb._refresh_envelope_page()
            except Exception:
                pass
            try:
                pw = self._programmer_widget
                if pw is not None and hasattr(pw, '_cue_store_page'):
                    pw._cue_store_page.refresh()
            except Exception:
                pass
            # 过渡链预热: 加载完成后一次性预热过渡签名/compiled universe/锚点缓存,
            # 消除「用户秒按播放」窗口的边界 tick 200ms 级冷构建尖峰。预算 3s 内
            # 未完成的 clip 由 500ms 后台守护 precompile(既有机制)继续。失败不影响
            # 打开流程。
            try:
                _eng = self._engine
                if _eng is not None and hasattr(_eng, "_prewarm_transition_caches"):
                    _eng._prewarm_transition_caches(new_proj, budget_s=3.0)
            except Exception:
                pass
            self._rebuild_safety_interlock()
            # 前端流畅度：加载完成后冻结 GC 永久代（见 _freeze_gc_after_project_load）
            _freeze_gc_after_project_load()
        else:
            detail = str(getattr(self._pm, "_last_load_error", "") or "")
            msg = f"无法读取项目文件：\n{path}\n\n请确认文件格式正确（.dmxt）。"
            if detail:
                msg += f"\n\n错误详情：{detail}"
            QMessageBox.critical(self, "打开失败", msg)

    def _save_project(self):
        if not self._project_path:
            self._save_as()
            return
        self._do_save(self._project_path)

    def _save_as(self) -> bool:
        """★ R193/SV-01：返回是否真的发起了保存（False=用户取消对话框）。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "",
            "雾白纪 ULTRA 项目 (*.dmxt);;所有文件 (*)")
        if path:
            if not path.endswith(".dmxt"):
                path += ".dmxt"
            self._do_save(path)
            return True
        return False  # ★ R193/SV-01：用户取消另存为——调用方据此刻画是否放行

    def _do_save(self, path: str):
        if self._pm is None:
            self._resolve_pm()
        if self._pm is None:
            QMessageBox.critical(self, "保存失败",
                                 "ProjectManager 未初始化。\n请检查控制台错误输出。")
            return
        # 收集各面板状态到项目（与自动保存共用同一入口）
        self._collect_project_state_for_save()
        if not self._bundle_timeline_audio_for_save(path):
            return
        # ★★ R139：序列化+写盘搬后台线程（真工程实测 ~0.9s，原全程卡 UI → 播放中"卡死"）。
        #   实现与分工见 `ui/project_save_async`（MainWindow 是上帝类，不在此加方法）。
        from .project_save_async import save_project_async
        save_project_async(self, path)

    def _export_mvr(self):
        """导出 MVR (.mvr) — 行业开放标准，主流灯光软件均可导入。

        从当前工程的设备实例生成 MVR（设备名/DMX地址/2D位置/占位GDTF引用），
        实现 Qascade→反向配接同步。
        """
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from .export_mvr import export_mvr
        proj = self._project()
        if proj is None:
            QMessageBox.information(self, "导出 MVR", "请先新建或打开一个项目。")
            return
        if not getattr(proj, "fixtures", None):
            ec.mvr_export_failed("项目为空，无设备实例可导出").show(self)
            return
        default_name = (getattr(proj, "name", "") or "qascade") + ".mvr"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 MVR", default_name, "MVR 文件 (*.mvr);;所有文件 (*.*)")
        if not path:
            return
        ok, msg = export_mvr(proj, path)
        if ok:
            # 成功通知：Toast 提示代替阻塞式对话框
            try:
                from .toast_notification import show_success_toast
                import os
                filename = os.path.basename(path)
                show_success_toast(self, "导出完成", f"MVR 文件: {filename}", duration_ms=4000)
            except Exception:
                # 降级为传统对话框
                QMessageBox.information(self, "导出 MVR", msg +
                                        "\n\n可在 R4 中通过 MVR 导入此配接布局。")
        else:
            QMessageBox.warning(self, "导出 MVR", msg)

    def _import_busy(self, label, func):
        """同步导入包装：忙碌光标 + 状态栏提示，**解析在后台线程执行**。

        ★ R194/CC-05：原实现 processEvents 后全程同步执行——至冬7.0 全量
        ET 解析 ~7s 冻结整窗。现 func 搬 QThread + 本地事件循环等待，GUI 保持
        可交互（重绘/输入照常）；对调用方契约不变（阻塞至完成并返回结果，
        func 抛异常原样向上传播，调用方已有 try/except 转中文提示）。
        QThread 生存期照 CC-04 教训（父引用 + finished→deleteLater）。
        """
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._status.showMessage(label)
        try:
            if getattr(self, "_import_thread_active", False):
                return func()  # 已有导入在跑（罕见重入）：退化为旧行为保正确
            from PyQt6.QtCore import QEventLoop, QThread

            class _ImportFnWorker(QThread):
                done_ok = pyqtSignal(object)
                done_err = pyqtSignal(str)

                def run(self):
                    try:
                        self.done_ok.emit(func())
                    except Exception as e:  # noqa: BLE001 - 必须过信号回 UI
                        self.done_err.emit(f"{type(e).__name__}: {e}")

            self._import_thread_active = True
            result = {"value": None, "error": None}
            loop = QEventLoop()
            worker = _ImportFnWorker(self)
            worker.done_ok.connect(
                lambda v: (result.__setitem__("value", v), loop.quit()))
            worker.done_err.connect(
                lambda m: (result.__setitem__("error", m), loop.quit()))
            worker.finished.connect(worker.deleteLater)
            worker.start()
            loop.exec()
            worker.wait(2000)  # ★ R194/P2-10 教训：收线程防 C++ 析构竞态
            self._import_thread_active = False
            if result["error"] is not None:
                raise RuntimeError(result["error"])
            return result["value"]
        finally:
            self._import_thread_active = False
            QApplication.restoreOverrideCursor()
            self._status.clearMessage()

    def _import_patch_table(self):
        """导入 R4 配接表 — 第 1 步：解析 CSV、建设备类型、打开 Patch 面板待补通道。"""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from .import_patch_csv import read_patch_csv, group_by_type, skipped_container_types
        from ..core.models import FixtureDefinition
        proj = self._project()
        if proj is None:
            QMessageBox.information(self, "导入配接表", "请先新建或打开一个项目。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配接表 (R4 CSV)", "", "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        rows, warnings = self._import_busy("正在解析配接表…", lambda: read_patch_csv(path))
        if not rows:
            QMessageBox.warning(self, "导入配接表",
                                "未读取到任何数据行。\n" + "\n".join(warnings))
            return
        groups = group_by_type(rows, patchable_only=True)
        if not groups:
            QMessageBox.warning(self, "导入配接表", "未发现可配接设备（所有行通道数为 0）。")
            return
        skipped = skipped_container_types(rows)
        self._snapshot_for_undo()
        # 幂等：按名复用已存在设备类型，缺失则新建（通道全为占位，待用户补全含义）
        existing = {fd.name: fd.id for fd in proj.fixture_defs}
        def_by_type = {}
        new_defs = 0
        for g in groups:
            if g.type_name in existing:
                def_by_type[g.type_name] = existing[g.type_name]
            else:
                fd = FixtureDefinition(
                    name=g.type_name, channel_count=max(1, g.channel_count),
                    channels=["placeholder"] * max(1, g.channel_count))
                proj.fixture_defs.append(fd)
                def_by_type[g.type_name] = fd.id
                new_defs += 1
        self._pending_patch_import = {
            "rows": rows, "groups": groups, "def_by_type": def_by_type, "src": path,
        }
        # 打开并置顶 Patch 面板，刷新设备类型列表，显示"完成配接"横幅
        self._toggle_dock(self._patch_dock, True)
        if self._patch_widget is not None:
            if hasattr(self._patch_widget, "refresh"):
                self._patch_widget.refresh()
            if hasattr(self._patch_widget, "begin_import_session"):
                try:
                    self._patch_widget.begin_import_session(
                        on_finish=self._finalize_patch_import,
                        def_ids=list(def_by_type.values()))
                except TypeError:
                    # 兼容旧签名（无 def_ids 参数）
                    self._patch_widget.begin_import_session(on_finish=self._finalize_patch_import)
        if self._pm is not None:
            self._pm.project_changed.emit()
        msg = (f"已识别 {len(groups)} 个设备类型（新建 {new_defs} 个，通道含义待填写）。\n"
               f"请在 Patch 面板逐个补全各通道类别，然后点击左上「完成配接」。")
        if skipped:
            shown = "、".join(skipped[:6]) + ("…" if len(skipped) > 6 else "")
            msg += f"\n\n已跳过 {len(skipped)} 个通道数为 0 的容器类型：{shown}"
        if warnings:
            msg += "\n\n警告：\n" + "\n".join(warnings[:8])
        QMessageBox.information(self, "导入配接表 — 第 1 步", msg)
        # 主动逐个唤起「通道仍全为占位」类型的编辑框，免去用户先点「完成配接」再回头找「编辑」
        if self._patch_widget is not None and hasattr(self._patch_widget, "open_placeholder_editors"):
            self._patch_widget.open_placeholder_editors(list(def_by_type.values()))

    def _finalize_patch_import(self):
        """导入配接表 — 第 2 步：建设备实例 + 生成 TopView 各类型 Sheet。"""
        from PyQt6.QtWidgets import QMessageBox
        from ..core.models import FixtureInstance
        from .import_patch_csv import build_type_layout
        import gc
        ctx = self._pending_patch_import
        if not ctx:
            return
        guid_to_key = ctx.get("guid_to_key") or {}
        proj = self._project()
        if proj is None:
            self._pending_patch_import = None
            return
        def_by_type = ctx["def_by_type"]; groups = ctx["groups"]
        # 家族通道共享（安全网）：名称相似+通道数相同的类型，把已填配置传给同族未填者。
        # 现实中非一体机常各配一个泵（气爆泵/跑泉泵），泵通道一样，用户只填一个即可。
        # 「先规划→列出 source→target→用户确认」避免单字 CJK 后缀误判静默污染；范围限本次导入。
        try:
            from .import_patch_csv import plan_family_channels, apply_family_plan
            fam_defs = [proj.get_fixture_def(did) for did in def_by_type.values()]
            fam_defs = [d for d in fam_defs if d is not None]; fam_plan = plan_family_channels(fam_defs)
            if fam_plan:
                pairs = "、".join(
                    f"{getattr(fam_defs[t],'name','?')}←{getattr(fam_defs[s],'name','?')}"
                    for t, s in fam_plan[:10]) + ("…" if len(fam_plan) > 10 else "")
                r = QMessageBox.question(
                    self, "套用同族通道配置？",
                    f"以下设备类型名称相似且通道数相同，可把已填通道配置套用过去，"
                    f"省去重复设置：\n{pairs}\n\n是否套用？（只填尚未配置的类型，不覆盖已填）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    apply_family_plan(fam_defs, fam_plan)
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning("family channel plan failed: %s", _e)
        # 校验通道是否仍全为占位
        unfilled = []
        for name, did in def_by_type.items():
            fd = proj.get_fixture_def(did)
            chs = list(getattr(fd, "channels", []) or []) if fd else []
            if chs and all(c == "placeholder" for c in chs):
                unfilled.append(name)
        if unfilled:
            shown = "、".join(unfilled[:8]) + ("…" if len(unfilled) > 8 else "")
            r = QMessageBox.question(
                self, "完成配接",
                f"以下设备类型的通道含义尚未填写：\n{shown}\n\n仍要继续配接吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self._snapshot_for_undo()
        # 同步建设备实例 + 世界坐标 + TopView 是主线程阻塞热点：先置忙碌光标 + 状态栏
        # 提示（processEvents 让其立即可见），收尾统一恢复。
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._status.showMessage("正在完成配接…")
        QApplication.processEvents()
        # ★ GC 调参（2026-08-14）：建设备实例 + 世界坐标 + 拓扑段大量分配临时对象
        #   （O(N²) 距离矩阵 / 11382 台 FixtureInstance / 包逻辑设备），默认分代 GC
        #   会在此段触发多次全堆扫描造成 2x 抖动；禁用以消除抖动，段末统一 enable。
        gc.collect()
        gc.disable()
        # 幂等：已存在的 (域,起始通道,def_id) 跳过，避免重复实例（R4 导出含重复行）。
        # 键含 def_id：同一地址若被「不同设备类型」占用（手动配接/旧导入残留），
        # 不应误判为重复——否则会跳过本应创建的设备、并把本类型序号映射到那台外来设备。
        # ★ 性能：同时建 key→fid 索引（首现胜出，与下方顺序扫描语义一致），
        #   重复行命中时不再对 11382 台全表扫描。
        existing_by_key: Dict[tuple, str] = {}
        for _f in proj.fixtures:
            _k = (int(getattr(_f, "universe", 1) or 1),
                  int(getattr(_f, "start_channel", 1) or 1),
                  getattr(_f, "def_id", ""))
            if _k not in existing_by_key:
                existing_by_key[_k] = str(getattr(_f, "id", "") or "")
        # (类型名, seq_id) -> fixture_id，供 TopView 摆位反查。
        # 注意：seq_id 跨类型不唯一（不同类型可能复用同一序号），故必须带类型名做键。
        seq_to_fid = {}; created = 0
        new_fids = set()   # 本次导入新建的 fid（再次导入时只对新设备写坐标，保留旧设备的人工摆位/缩放）
        skipped_groups = []
        # ★ 真实安装朝向表（2026-08-09）：_extra=(sel, fmap, patched, guid_to_key)。
        #   guid_to_key 值实为 PatchRow 对象（import_ L736 guid_to_key[fg]=row，
        #   注释写 (type_name, seq_id) 是旧版残留）→ 取 .type_name/.seq_id 构造键，
        #   反查 guid → fmap[guid].rot_y（世界偏航，2D XY 同步基准用）。
        _extra = ctx.get("_extra"); _fmap = _extra[1] if _extra else None
        _key_to_guid = {}
        if _fmap:
            for _g, _k in (_extra[3] or {}).items():
                _key_to_guid[(_k.type_name, _k.seq_id)] = _g
        for g in groups:
            # 横幅非模态期间用户可能删了该设备类型 → def_by_type 里的 id 悬空。
            # 按名兜底重查，仍找不到则跳过该类型，避免建出 def_id 指向不存在定义的孤儿设备。
            did = def_by_type.get(g.type_name)
            if did is None or proj.get_fixture_def(did) is None:
                did = next((fd.id for fd in proj.fixture_defs if fd.name == g.type_name), None)
            if did is None or proj.get_fixture_def(did) is None:
                skipped_groups.append(g.type_name)
                continue
            for row in g.rows:
                key = (int(row.universe), int(row.start_channel), did)
                existing_fid = existing_by_key.get(key)
                if existing_fid is not None:
                    # 已存在(同类型同址)：仍记录其 fid 供 TopView 摆位
                    # ★ 性能：直接索引取值（原实现命中后对 11382 台全表扫描找 fid）。
                    seq_to_fid[(g.type_name, row.seq_id)] = existing_fid
                    continue
                fi = FixtureInstance(
                    def_id=did, name=f"{g.type_name} {row.seq_id}",
                    universe=int(row.universe), start_channel=int(row.start_channel))
                fi.pos_z = float(getattr(row, "y_cm", 0.0) or 0.0)  # 高度(PosY)
                # 安装绕 X 轴旋转（Object RotX）：RotX=180 设备的
                # 翻转门控 = RotZ（见 topview_panel._fixture_is_flipped）：
                # RotZ=180 是纯偏航（改版至冬1，同步零翻转）；
                # RotZ=-180 才是至冬7.0 式倒装约定（同步基准 +180）。
                fi.rot_x = float(getattr(row, "x_rot_deg", 0.0) or 0.0)
                # 安装绕 Z 轴旋转（Object RotZ，翻转门控依据）。
                fi.rot_z = float(getattr(row, "z_rot_deg", 0.0) or 0.0)
                # 安装绕 Y 轴旋转（世界偏航）：fmap 反查真实朝向。
                #   仅导入路径有 fmap；手动配接/CSV 无 → 0.0 回退切线基准。
                fi.rot_y = 0.0
                if _fmap:
                    _g = _key_to_guid.get((g.type_name, row.seq_id))
                    _node = _fmap.get(_g) if _g else None
                    if _node is not None:
                        fi.rot_y = float(getattr(_node, "rot_y", 0.0) or 0.0)
                proj.fixtures.append(fi)
                # ★ 2026-08-13 IMP-01: 批内索引同步 —— 修复前 existing_by_key
                #   只含预建旧设备, 同批重复行(双 Default Patch 工程)每行都
                #   新建幻影重复设备(DMX 双写同一地址)。首建后写入索引,
                #   批内后续同址行命中 skip, 与「幂等跳过」语义一致。
                existing_by_key[key] = fi.id
                seq_to_fid[(g.type_name, row.seq_id)] = fi.id
                new_fids.add(fi.id)
                created += 1
        fix_by_id = {fixture.id: fixture for fixture in proj.fixtures}
        # 生成 TopView：每类型一个 Sheet（摆位 + 按簇拟合图形，不写朝向）
        pages_added = 0; logical_devices_dicts = []
        self._toggle_dock(self._topview_dock, True)
        topview_ok = (self._topview_widget is not None
                      and hasattr(self._topview_widget, "import_layout_pages"))
        if topview_ok:
            from .import_patch_csv import DEFAULT_IMPORT_SCALE
            from .composite_device_dialog import CompositeDeviceDialog
            from ..core.models import LogicalDevice
            # 1) 每类型布局（默认整体缩小 + 微单元检测）
            layouts = {}
            for g in groups:
                if g.type_name in skipped_groups:
                    continue
                layouts[g.type_name] = build_type_layout(g, scale=DEFAULT_IMPORT_SCALE)
            # 2) fid -> 居中坐标 / 类型；并把真实散布坐标写进各 FixtureInstance（引擎回退/各自坐标系据此）
            fid_pos = {}
            fid_type = {}
            for tname, layout in layouts.items():
                for seq_id, (x, y) in layout.positions.items():
                    fid = seq_to_fid.get((tname, seq_id))
                    if not fid:
                        continue
                    fid_pos[fid] = (x, y)
                    fid_type[fid] = tname
                    fi = fix_by_id.get(fid)
                    # 只对本次新建设备写真实坐标；已存在设备保留其人工摆位/整体缩放成果
                    # （再次导入"加一个新类型"不应把旧类型设备弹回默认布局）。
                    if fi is not None and fid in new_fids:
                        fi.pos_x = float(x); fi.pos_y = float(y)
            # 3) 微单元（N 灯围一中心等）：**始终创建**为隐藏单元(hidden=True)，供 Programmer
            #    单控/整体控操作。隐藏=TopView 画每个成员个体、不折叠成点/不画代表点；单元记录
            #    仍存在工程，定义「各自坐标系」分组(单控时每单元各自拟合圆顺逆时针)。
            unit_lds: list[dict] = []
            for tname, layout in layouts.items():
                for u in layout.units:
                    members = [seq_to_fid.get((tname, s)) for s in u.member_seq_ids]
                    members = [m for m in members if m]
                    if len(members) < 2:
                        continue
                    offsets = {}
                    for s in u.member_seq_ids:
                        m = seq_to_fid.get((tname, s))
                        if m:
                            off = u.member_offsets.get(s, (0.0, 0.0))
                            offsets[m] = [float(off[0]), float(off[1])]
                    ld = LogicalDevice(
                        name=f"{tname} 单元", member_fixture_ids=members,
                        pos_x=float(u.center[0]), pos_y=float(u.center[1]),
                        local_offsets=offsets, kind="unit", control_mode="merged",
                        hidden=True)
                    unit_lds.append(ld.to_dict())
            # 4) 按类型合并可选设备表。隐藏单元成员**仍可参与**按类型合并（灯+其它设备→新设备）；
            #    若被合并进 composite，下面会从隐藏单元里剔除（composite 优先，一成员只归一处）。
            fixtures_by_type = {}
            for tname, layout in layouts.items():
                items = []
                for seq_id in sorted(layout.positions.keys()):
                    fid = seq_to_fid.get((tname, seq_id))
                    if fid and fid in fix_by_id:
                        items.append((fid, getattr(fix_by_id[fid], "name", fid)))
                if items:
                    fixtures_by_type[tname] = items
            # 5) 弹「按类型虚拟合并」对话框（可跳过）。用户在对话框里给每类型选角色
            #    （灯/电机/阀/泵/其它），下面只提供**默认猜测**(通道+名称)，用户可改。
            composite_lds = []
            if fixtures_by_type:
                defs_by_id = {fd.id: fd for fd in getattr(proj, "fixture_defs", [])}
                def _guess_role(fd, tname):
                    chans = [str(c).strip().lower() for c in (getattr(fd, "channels", []) or [])]
                    if any(c in ("r", "red", "g", "green", "b", "blue", "w", "white") for c in chans):
                        return "light"
                    if any(c.startswith("motor") for c in chans):
                        return "motor"
                    if any("valve" in c for c in chans):
                        return "valve"
                    if any("pump" in c for c in chans):
                        return "pump"
                    nm = str(tname)
                    low = nm.lower()
                    if any(k in nm for k in ("灯", "光源")) or any(k in low for k in ("led", "rgb", "light", "laser")):
                        return "light"
                    if "电机" in nm or "motor" in low or "axis" in low or "jet" in low:
                        return "motor"
                    if "阀" in nm or "valve" in low or "nozzle" in low or "气爆" in nm:
                        return "valve"
                    if "泵" in nm or "pump" in low:
                        return "pump"
                    return "other"
                type_roles = {}
                light_types = set()
                for tname in fixtures_by_type:
                    for fid, _disp in fixtures_by_type[tname]:
                        fi = fix_by_id.get(fid)
                        fd = defs_by_id.get(getattr(fi, "def_id", "")) if fi is not None else None
                        if fd is not None:
                            r = _guess_role(fd, tname)
                            type_roles[tname] = r
                            if r == "light":
                                light_types.add(tname)
                            break
                positions = {str(fid): (float(p[0]), float(p[1])) for fid, p in fid_pos.items()}
                # 模态对话框前恢复忙碌光标，避免对话框内仍显示等待光标；结束后重新置位。
                QApplication.restoreOverrideCursor()
                try:
                    dlg = CompositeDeviceDialog(fixtures_by_type, positions=positions,
                                                light_types=light_types, type_roles=type_roles,
                                                parent=self)
                    dlg.exec()
                    composite_lds = dlg.result_logical_devices()
                except Exception:
                    composite_lds = []
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                self._status.showMessage("正在完成配接…")
                QApplication.processEvents()
            # 隐藏单元**不剪枝**：灯即使被并入 composite(显示层)，仍保留在隐藏单元(控制层)——
            # 灯的整体控/单控表现与未合并时一模一样，合并只改 TopView 显示。两层独立、互不冲突
            # (GroupSelector 跳过 kind=unit、page 不挂隐藏单元，故不会重复显示)。
            # ★★ R127：识别「父管 X + 嵌套子管 Y」的二维云台（通道取自 fixture_defs）
            _xy = _build_xy_gimbal_devices(
                (guid_to_key, seq_to_fid, fix_by_id, composite_lds),
                ctx.get("project_root"), _ld_channel_types(proj, fix_by_id))
            composite_lds += _xy[0]; _xy_info(self, _xy[1])
            logical_devices_dicts = unit_lds + composite_lds
            # 6) 组合设备代表点 + local_offsets。代表点 = 首个有坐标成员(锚)的真实位置；
            #    local_offsets = 各成员相对代表点的**真实**偏移。**不改写**成员真实坐标——
            #    所有类型同处一个场地居中帧(已验证 centroid 接近)，灯就近分配本就基于真实坐标，
            #    改写成小环会毁掉灯的 per_shape 几何，使合并后单控效果与未合并时不一致。
            for ld in logical_devices_dicts:
                if ld.get("kind") != "composite":
                    continue
                members = [str(m) for m in ld.get("member_fixture_ids", [])]
                rep = None
                for m in members:
                    fi = fix_by_id.get(m)
                    if fi is not None:
                        rep = (float(getattr(fi, "pos_x", 0.0) or 0.0),
                               float(getattr(fi, "pos_y", 0.0) or 0.0))
                        break
                if rep is None:
                    rep = fid_pos.get(members[0], (0.0, 0.0)) if members else (0.0, 0.0)
                ld["pos_x"], ld["pos_y"] = rep
                offs = {}
                for m in members:
                    fi = fix_by_id.get(m)
                    if fi is not None:
                        offs[m] = [float(getattr(fi, "pos_x", 0.0) or 0.0) - rep[0],
                                   float(getattr(fi, "pos_y", 0.0) or 0.0) - rep[1]]
                ld["local_offsets"] = offs
            # 7) 写入工程。再次导入时按「本次导入涉及的全部设备」(fid_type 的键，含已存在复用的)
            #    剔除旧逻辑设备——而非仅按新建 UUID，否则重复导入同一表会因无新 UUID 而把旧的
            #    单元/合并条目原样留存并叠加，导致 Programmer 残留两两一对的旧组。
            import_pool = {str(k) for k in fid_type.keys()}
            existing_ld = list(getattr(proj, "logical_devices", []) or [])
            existing_ld = [ld for ld in existing_ld
                           if not ({str(m) for m in ld.get("member_fixture_ids", [])} & import_pool)]
            existing_ld.extend(logical_devices_dicts)
            proj.logical_devices = existing_ld
            # 8) 成员集 + 每个逻辑设备的归属页（首成员类型）。
            #    只有 composite(显示成单点)的成员从 fixtures 移除；hidden 单元的成员保持可见
            #    （TopView 画每个个体灯，单元只是隐藏的控制分组）。
            member_fids = set()
            for ld in logical_devices_dicts:
                if ld.get("hidden"):
                    continue
                for m in ld.get("member_fixture_ids", []):
                    member_fids.add(str(m))
            ld_home = {}
            for ld in logical_devices_dicts:
                for m in ld.get("member_fixture_ids", []):
                    t = fid_type.get(str(m))
                    if t:
                        ld_home[ld.get("id")] = t
                        break
            # 9) page_specs：composite 成员不进 fixtures（仅画代表点）；hidden 单元不挂页面
            #    （不画代表点、不折叠）；逻辑设备(仅 composite)挂到归属页。
            #    图形拟合：按**合并后实际显示的点**(可见个体 + composite 代表点)重新拟合圆/弧/线，
            #    而非用原始(未合并)的 layout.shapes——否则合并把成员折叠走后曲线与显示不符/消失。
            from .import_patch_csv import fit_shapes_for_points
            page_specs = []
            for tname, layout in layouts.items():
                fixtures = {}
                for seq_id, (x, y) in layout.positions.items():
                    fid = seq_to_fid.get((tname, seq_id))
                    if fid and fid not in member_fids:
                        fixtures[fid] = (x, y)
                page_lds = [ld for ld in logical_devices_dicts
                            if (not ld.get("hidden")) and ld_home.get(ld.get("id")) == tname]
                if fixtures or page_lds:
                    # 重新拟合：可见个体坐标 + 本页 composite 代表点，先分簇再各自拟合多条圆/弧/线
                    fit_pts = [(float(x), float(y)) for (x, y) in fixtures.values()]
                    for ld in page_lds:
                        try:
                            fit_pts.append((float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0))))
                        except Exception:
                            continue
                    refit_shapes = fit_shapes_for_points(fit_pts)
                    page_specs.append({
                        "name": f"配接·{tname}",
                        "fixtures": fixtures,
                        "shapes": refit_shapes,
                        "logical_devices": page_lds,
                    })
            if page_specs:
                pages_added = self._topview_widget.import_layout_pages(page_specs)
            # 9.5) 每页自动「全选」设备组：含该页全部设备(可见个体 + 该页 composite 成员)，
            #      供 Programmer/Matrix 一键选中整页。按组名去重(再次导入同页不累积)。
            try:
                page_groups = []
                for spec in page_specs:
                    pname = str(spec.get("name", "") or "")
                    page_fids = [str(f) for f in (spec.get("fixtures", {}) or {}).keys()]
                    for ld in (spec.get("logical_devices", []) or []):
                        for m in (ld.get("member_fixture_ids", []) or []):
                            page_fids.append(str(m))
                    # 去重保序
                    seen = set(); ordered = []
                    for f in page_fids:
                        if f and f not in seen:
                            seen.add(f); ordered.append(f)
                    if ordered:
                        page_groups.append((f"{pname} 全选", ordered))
                if page_groups:
                    existing_groups = list(getattr(proj, "fixture_groups", []) or [])
                    new_names = {n for n, _ in page_groups}
                    existing_groups = [g for g in existing_groups
                                       if str((g.get("name", "") if isinstance(g, dict) else "")) not in new_names]
                    import uuid as _uuid
                    for gname, gfids in page_groups:
                        existing_groups.append({
                            "id": str(_uuid.uuid4()),
                            "name": gname,
                            "color": "#3A6EA5",
                            "fixture_ids": list(gfids),
                            "base_order_fixture_ids": list(gfids),
                            "route_order_sheets": [],
                        })
                    proj.fixture_groups = existing_groups
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("auto per-page select-all group failed: %s", e)
            if hasattr(proj, "topview_state") and hasattr(self._topview_widget, "save_state"):
                proj.topview_state = self._topview_widget.save_state()
        # ★ 按设备类型分组：每种设备一个组（名=「类型·类型名」，成员=该类型全部设备），
        #   Programmer 左侧按类型选组 → 通道按设备类型区分；同种设备合并控制（行收起
        #   只显示合并条带，拖条带=该类型全部设备一起动），无需逐台打开。幂等：按组名
        #   重建，重复导入同名类型不累积；「类型·」前缀避免与选择组/页全选组撞名。
        try:
            fids_by_def: dict = {}
            for _fi in proj.fixtures:
                fids_by_def.setdefault(str(getattr(_fi, "def_id", "")), []).append(str(_fi.id))
            if fids_by_def:
                _defs_by_id = {str(fd.id): fd for fd in proj.fixture_defs}
                _type_groups = []
                for _did, _fids in fids_by_def.items():
                    _fd = _defs_by_id.get(_did)
                    _tname = str(_fd.name) if _fd is not None else "未知类型"
                    _type_groups.append((f"类型·{_tname}", list(_fids)))
                _type_groups.sort(key=lambda t: -len(t[1]))
                _tg_names = {n for n, _ in _type_groups}
                _tg_existing = [g for g in (proj.fixture_groups or [])
                                if str((g.get("name", "") if isinstance(g, dict) else "")) not in _tg_names]
                import uuid as _uuid_tg
                # 按成员数降序时用 reversed 逐项前置，避免 insert(0) 使顺序反转
                # （审查 Bug-6：大组应排最前）
                for _gname, _gfids in reversed(_type_groups):
                    _tg_existing.insert(0, {
                        "id": str(_uuid_tg.uuid4()),
                        "name": _gname,
                        "color": "#2E7D5B",
                        "fixture_ids": list(_gfids),
                        "base_order_fixture_ids": list(_gfids),
                        "route_order_sheets": [],
                    })
                proj.fixture_groups = _tg_existing
        except Exception:
            import logging
            logging.getLogger(__name__).warning("按设备类型建组失败", exc_info=True)
        gc.enable()
        # 收尾
        if self._patch_widget is not None and hasattr(self._patch_widget, "end_import_session"):
            self._patch_widget.end_import_session()
        self._pending_patch_import = None
        self._on_patch_changed()
        if self._pm is not None:
            self._pm.project_changed.emit()
        self._rebuild_safety_interlock()
        # 2026-08-09 导入自动初始化 XY 同步基准：导入路径此前无 cfg 写入，
        # 用户导入后直接推摇杆 = cfg 空 = 无同步输出（波束=安装朝向=切线）。
        # ensure_xy_reference_initialized 由并行 agent 在 topview_panel 同期添加，
        # 用 getattr 防御：方法未就绪时本次静默跳过，下次导入自动生效。
        _ensure = getattr(self._topview_widget, "ensure_xy_reference_initialized", None)
        if callable(_ensure):
            try:
                if _ensure() > 0:
                    # ★ 指纹缓存兜底：导入尾部布局刷新链可能已用空 cfg 缓存
                    #   _xy_ref_sig，ensure 的 emit 再被命中即跳过重建 → 运行时
                    #   map 旧/空 = 推摇杆透传 = 光束切线。显式失效 + 重建一次，
                    #   保证 nm 运行时 map 拿到最新 cfg（导入后直接推=世界方向）。
                    self._xy_ref_sig = None
                    self._sync_xy_reference()
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning("XY 同步基准自动初始化失败: %s", _e)
        # R56e: 导入后激活第一个有内容的生成页——此前 _active_page 停在默认
        # 空 Sheet1（索引 0）, 用户导入完打开 TopView 只见空白画布,
        # 误判「TopView 导入丢失」（内容页都在后面的 tab 里）。
        try:
            _tvp = self._topview_widget
            if _tvp is not None and pages_added > 0:
                _pgs = getattr(_tvp, "_pages", []) or []
                _target = -1
                for _i, _pg in enumerate(_pgs):
                    if _pg.get("fixtures") or _pg.get("logical_devices"):
                        _target = _i
                        break
                if _target >= 0 and _target != getattr(_tvp, "_active_page", -1):
                    _tabs = getattr(_tvp, "_tabs", None)
                    if _tabs is not None:
                        _tabs.setCurrentIndex(_target)
                    else:
                        _tvp._active_page = _target
                    _refresh = getattr(_tvp, "refresh", None)
                    if callable(_refresh):
                        _refresh()
        except Exception:
            pass
        msg = f"已配接 {created} 台设备，生成 {pages_added} 个 TopView 页面。"
        if logical_devices_dicts:
            n_unit = sum(1 for d in logical_devices_dicts if d.get("kind") == "unit")
            n_comp = sum(1 for d in logical_devices_dicts if d.get("kind") == "composite")
            n_pkg = sum(1 for d in logical_devices_dicts if d.get("kind") == "package")
            parts = []
            if n_unit:
                parts.append(f"{n_unit} 个隐藏单元（灯单独显示，可在 Programmer 切单控/整体控）")
            if n_comp:
                parts.append(f"{n_comp} 个按类型合并（显示成单点）")
            if n_pkg:
                parts.append(f"{n_pkg} 个包（整套设备，显示成单点）")
            if parts:
                msg += "\n虚拟设备：" + " + ".join(parts) + "。"
        if skipped_groups:
            shown = "、".join(skipped_groups[:6]) + ("…" if len(skipped_groups) > 6 else "")
            msg += f"\n\n跳过 {len(skipped_groups)} 个设备类型（其定义已被删除）：{shown}"
        QApplication.restoreOverrideCursor()
        self._status.clearMessage()
        QMessageBox.information(self, "导入配接表 — 完成", msg)

    def _on_patch_changed(self):
        """Patch 设备配接变化后的统一刷新入口。

        该槽函数供 PatchPanel.patch_changed 信号直接连接使用。这里保持轻量，
        只负责标记工程已修改并刷新依赖设备列表/位置的面板；具体面板不存在
        或尚未懒加载时不抛异常，避免影响软件启动和面板打开。
        """
        note_project_edit(self)
        # ★★ R141：配接变更 → 递增工程配接版本号，令按"地址几何"缓存的下游失效。
        #   为什么必须在这里做：滤网掩码（泵/电机粗精/灯）缓存键原本只有
        #   `id(proj)/len(fixtures)/len(fixture_defs)` + 列表身份，而配接面板
        #   **原地改 `start_channel`**（拖格子 / 导入配接表 / 套用家族计划）时
        #   这三者全不变 → 陈旧命中 → 泵主控、电机限位限速**按旧地址**打
        #   （实测：设备从地址 1 挪到 100，掩码仍命中地址 2）。本槽是所有配接
        #   变更的必经收口（`fixtures_changed` 直连），一处即全覆盖。
        try:
            self._pm.project.bump_patch_rev()
        except Exception:
            pass
        from ..engine.patch_runtime_invalidation import invalidate_patch_runtime
        invalidate_patch_runtime(self)
        # ★★ R183 A10 审计 P0：配接变更同步失效音频调制地址缓存——修前仅工程
        #   切换/调制编辑才失效，原地改 start_channel 后调制按旧地址缩放=打错
        #   通道（_ch_type_addr_map 现另带 patch_rev 键，此处双保险）。
        try:
            from src.engine.audio_mod_driver import invalidate_caches
            invalidate_caches()
        except Exception:
            pass
        self._sync_motor_defaults()
        try:
            self._update_title()
        except Exception:
            pass
        self._refresh_patch_dependent_panels()

    def _refresh_patch_dependent_panels(self):
        """配接变更后逐面板刷新（_on_patch_changed 拆出，压 CC；缺面板静默）。"""
        for widget, methods in (
            (getattr(self, '_topview_widget', None), ('refresh',)),
            (getattr(self, '_patch_widget', None), ('refresh', '_refresh_all')),
            (getattr(self, '_programmer_widget', None), ('_refresh_fixtures', 'refresh')),
            (getattr(self, '_fixture_group_widget', None), ('refresh', '_refresh_fixtures')),
            (getattr(self, '_matrix_pattern_widget', None), ('_on_project_changed_for_matrix', 'refresh_filter_group_combo')),
        ):
            if widget is None:
                continue
            for method_name in methods:
                method = getattr(widget, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
                    break

    def _export_frames(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出帧数据", "",
            "JSON 帧数据 (*.json)")
        if not (path and self._engine and hasattr(self._engine, "export_frames")):
            return
        # R178：后台烘焙进行中 → 等待烘焙完成再导出（规格：导出改为等待烘焙）。
        try:
            _ensure_timeline_baker(self)
            if not _wait_timeline_bake_for_export(self):
                self._status.set_message("已取消导出（后台烘焙未完成）")
                return
        except Exception:
            pass
        try:
            self._engine.export_frames(path)
            self._status.set_message(f"已导出: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _undo(self):
        from .dispatch.scoped_state_restore import perform_history_step
        perform_history_step(self, "undo")

    def _redo(self):
        from .dispatch.scoped_state_restore import perform_history_step
        perform_history_step(self, "redo")

    def _start_network(self):
        if self._programmer_widget is not None:
            try:
                if hasattr(self._programmer_widget, "clear_live_output"):
                    self._programmer_widget.clear_live_output()
            except Exception:
                pass
        if self._nm and hasattr(self._nm, "start"):
            try:
                # R182 审计修：bind 失败（ReceiverBindError）在此捕获 → 状态栏
                # 红字提示；修前裸 start() 抛异常走全局 excepthook 弹框。
                self._nm.start()
                self._status.set_message("网络已启动")
            except Exception as e:
                self._status.set_message(f"网络启动失败：{e}")

    def _stop_network(self):
        if self._nm and hasattr(self._nm, "stop"):
            try:
                self._nm.stop()
                self._status.set_message("网络已停止")
                self._status.clear_timecode()
            except Exception as e:
                self._status.set_message(f"网络停止失败：{e}")

    def _reset_layout(self):
        for dock in (self._monitor_dock, self._processor_dock, self._network_dock,
                     self._patch_dock, self._topview_dock, self._route_order_dock,
                     self._programmer_dock,
                     self._fixture_group_dock):
            dock.hide()
        self._panel_toolbar.sync_dock_state(False, False, False)

    def _workspace_programming(self):
        for dock in (
            self._fixture_group_dock,
            self._patch_dock,
            self._programmer_dock,
            self._topview_dock,
        ):
            self._lazy_load_panels(dock)
        self._monitor_dock.hide()
        self._processor_dock.hide()
        self._network_dock.hide()
        self._fixture_group_dock.show()
        self._patch_dock.show()
        self._programmer_dock.show()
        self._topview_dock.show()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._fixture_group_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._patch_dock)
        self.tabifyDockWidget(self._fixture_group_dock, self._patch_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._programmer_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._route_order_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._topview_dock)
        self._fixture_group_dock.raise_()
        self._programmer_dock.raise_()
        self._panel_toolbar.sync_dock_state(
            False, False, False,
            patch=True, topview=True, programmer=True, fixture_group=True,
            route_order=self._route_order_dock.isVisible())
        self._status.set_message("工作区：编程视图")

    def _workspace_monitoring(self):
        for dock in (
            self._monitor_dock,
            self._network_dock,
            self._processor_dock,
        ):
            self._lazy_load_panels(dock)
        self._patch_dock.hide()
        self._topview_dock.hide()
        self._route_order_dock.hide()
        self._programmer_dock.hide()
        self._fixture_group_dock.hide()
        self._monitor_dock.show()
        self._network_dock.show()
        self._processor_dock.show()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._monitor_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._network_dock)
        self.tabifyDockWidget(self._monitor_dock, self._network_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._processor_dock)
        self._monitor_dock.raise_()
        self._panel_toolbar.sync_dock_state(
            True, True, True,
            patch=False, topview=False, programmer=False, fixture_group=False,
            route_order=False)
        self._status.set_message("工作区：监控视图")

    def _workspace_full(self):
        self._lazy_load_panels()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._fixture_group_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._patch_dock)
        self.tabifyDockWidget(self._fixture_group_dock, self._patch_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._programmer_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._route_order_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._monitor_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._network_dock)
        self.tabifyDockWidget(self._monitor_dock, self._network_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._topview_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._processor_dock)
        self.tabifyDockWidget(self._topview_dock, self._processor_dock)
        for dock in (self._monitor_dock, self._processor_dock, self._network_dock,
                     self._patch_dock, self._topview_dock, self._route_order_dock,
                     self._programmer_dock,
                     self._fixture_group_dock):
            dock.show()
        self._fixture_group_dock.raise_()
        self._programmer_dock.raise_()
        self._topview_dock.raise_()
        self._monitor_dock.raise_()
        self._panel_toolbar.sync_dock_state(
            True, True, True,
            patch=True, topview=True, programmer=True, fixture_group=True,
            route_order=self._route_order_dock.isVisible())
        self._status.set_message("工作区：全功能视图")

    def _workspace_minimal(self):
        self._reset_layout()
        self._status.set_message("工作区：最小视图")

    # ══════════════════════════════════════════════════════════════════════════
    #  面板导航快捷键
    # ══════════════════════════════════════════════════════════════════════════

    def _update_title(self):
        name  = self._project_path or "未命名项目"
        dirty = " *" if self._dirty else ""
        self.setWindowTitle(f"{APP_NAME} — {name}{dirty}")

    def mark_dirty(self):
        note_project_edit(self)
        self._update_title()
        # 保存脏标记不等于渲染输入变化；原地帧写仍由后台完整签名检查。
        baker = _ensure_timeline_baker(self)
        if baker is not None:
            notify_project_change(baker, self._pm.project)

    # ══════════════════════════════════════════════════════════════════════════
    #  自动保存
    # ══════════════════════════════════════════════════════════════════════════

    def _load_auto_save_settings(self):
        """从 QSettings 加载自动保存设置并启动/停止定时器。"""
        s = QSettings("Qascade", "MainWindow")
        self._auto_save_enabled  = s.value("autosave/enabled",  False, type=bool)
        self._auto_save_interval = s.value("autosave/interval", 5,     type=int)
        self._apply_auto_save_timer()

    def _apply_auto_save_timer(self):
        """根据当前设置启动或停止自动保存定时器。"""
        self._auto_save_timer.stop()
        if self._auto_save_enabled and self._auto_save_interval > 0:
            self._auto_save_timer.start(self._auto_save_interval * 60 * 1000)

    def _do_auto_save(self):
        """执行一次自动保存：生成带时间戳的新文件，不覆盖原文件。"""
        if self._pm is None:
            return
        # 确定自动保存目录：优先放在当前项目目录下的 autosave 子目录；
        # 若当前项目未保存，则放在用户主目录下的 Qascade_autosave 目录
        if self._project_path:
            base_dir = Path(self._project_path).parent / "autosave"
            stem     = Path(self._project_path).stem
        else:
            base_dir = Path.home() / "Qascade_autosave"
            stem     = "untitled"
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(base_dir / f"{stem}_autosave_{ts}.dmxt")
        # 复用 _do_save 的状态收集逻辑，但不更新 _project_path / _dirty
        self._collect_project_state_for_save()
        if not self._bundle_timeline_audio_for_save(path, show_errors=False):
            return
        ok = self._pm.save(path, mark_clean=False, set_current_path=False)  # ★ R193/SV-02：不清 dirty/不改绑路径
        if ok:
            self._status.set_message(f"自动保存: {path}")
            # 轮换清理(审计 save-load P3): 同 stem 旧自动保存只保留最近 20 份,
            # 防止 autosave/ 无限堆积(历史: 测试项目2 曾堆积数百份)。
            _rotate_autosave_files(base_dir, stem)  # ★ R193/IO-02：含 <stem>_assets 目录
        else:
            self._status.set_message("自动保存失败")

    def _collect_project_state_for_save(self):
        """将各面板状态写入 PM.project，供保存前调用（不修改 _project_path/_dirty）。"""
        if self._pm is None or self._pm.project is None:
            return
        self._ensure_project_program_fields(self._pm.project)
        self._save_current_timeline_to_program()
        at = self._timeline_audio_track()
        if at is not None and self._active_program() is not None:
            self._pm.project.audio_path = self._current_timeline_audio_path()
            try:
                self._pm.project.audio_clip_state = dict(at.audio_clip_state or {})
            except Exception:
                self._pm.project.audio_clip_state = {}
        try:
            if self._processor_widget is not None and hasattr(self._processor_widget, 'save_state'):
                # TrackLinkerPanel.save_state() 返回裸 linker 状态，保持 v2 信封格式落盘
                self._pm.project.processor_state = self._wrap_track_linker_state(
                    self._processor_widget.save_state())
            else:
                linker_state = self._save_track_linker_state_from_engine()
                if linker_state:
                    self._pm.project.processor_state = self._wrap_track_linker_state(linker_state)
        except Exception:
            pass
        try:
            from src.audio import get_global_bridge
            bridge = get_global_bridge()
            if bridge:
                self._pm.project.audio_bridge_state = bridge.to_dict()
        except Exception:
            pass
        if self._matrix_pattern_widget is not None:
            try:
                self._pm.project.matrix_slideshow_state = self._matrix_pattern_widget.save_state()
            except Exception: pass
        if self._programmer_widget is not None:
            try:
                if hasattr(self._programmer_widget, 'save_audio_driver_state'):
                    self._pm.project.audio_driver_state = self._programmer_widget.save_audio_driver_state()
            except Exception:
                pass
            # ★ 2026-09-13 加：把 Programmer 完整面板状态写进工程 → 搭上
            #   工程级 undo。此前它只写 audio_driver_state，导致 undo 对它
            #   无状态可还原（用户："拉了条带，还原只恢复一个值、控件没还原"）。
            try:
                if hasattr(self._programmer_widget, 'save_state'):
                    self._pm.project.programmer_state = self._programmer_widget.save_state()
            except Exception:
                pass
        if self._effect_browser_widget is not None:
            try:
                self._pm.project.effect_browser_state = self._effect_browser_widget.save_state()
            except Exception: pass
        try:   # R172 §2.3：Studio sheet 状态（persist 内置 R97 `is` 守卫；未装载=None 静默）
            _studio_flush_and_persist(self)
        except Exception: pass
        # 新功能 store → project（Programmer CUE / 参数包络 / 自定义效果库）
        try:
            from .programmer_cue_store import get_programmer_cue_store
            self._pm.project.programmer_cues_data = get_programmer_cue_store().to_dict()
        except Exception:
            pass
        try:
            from .param_envelope import get_param_envelope_store
            self._pm.project.param_envelopes_data = get_param_envelope_store().to_dict()
        except Exception:
            pass
        try:
            from .trajectory_curve import get_trajectory_curve_store
            self._pm.project.trajectory_curves_data = get_trajectory_curve_store().to_dict()
        except Exception:
            pass
        try:
            # ★ R178：自定义效果库已删除（旧数据加载时迁往调度台）——保存不再
            #   写出该字段（置空防 models.to_dict 把内存残留原样带回磁盘）。
            self._pm.project.custom_effects_data = {}
        except Exception:
            pass
        try:
            from ..engine.auto_arrange.baked_arrangement import get_baked_arrangement_store
            self._pm.project.baked_arrangement_data = get_baked_arrangement_store().to_dict()
        except Exception:
            pass
        if self._topview_widget is not None:
            try:
                if hasattr(self._topview_widget, 'save_state'):
                    self._pm.project.topview_state = self._topview_widget.save_state()
            except Exception:
                pass
        if self._route_order_widget is not None:
            try:
                if hasattr(self._route_order_widget, 'save_state'):
                    self._pm.project.route_order_state = self._route_order_widget.save_state()
            except Exception:
                pass
        if self._route_animator_window is not None:
            try:
                if hasattr(self._route_animator_window, 'save_state'):
                    self._pm.project.route_animator_state = self._route_animator_window.save_state()
            except Exception:
                pass
        # 调度台：效果缓存库随项目持久化（唯一真源 = EffectCacheStore）
        try:
            from ..processing.dispatch import (get_effect_cache_store,
                                               get_release_registry)
            self._pm.project.dispatch_state = {
                **get_effect_cache_store().to_dict(),
                "releases": get_release_registry().to_dict(),
            }
        except Exception:
            pass

    def _show_auto_save_settings(self):
        """显示自动保存设置对话框。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("自动保存设置")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(
            f"QDialog {{ background:{BG_EDITOR}; color:{TEXT_PRIMARY}; }}"
            f"QLabel  {{ color:{TEXT_PRIMARY}; }}"
            f"QCheckBox {{ color:{TEXT_PRIMARY}; spacing:6px; }}"
            f"QSpinBox {{ background:{BG_INPUT}; color:{TEXT_PRIMARY}; border:1px solid {BORDER_NORMAL};"
            "           border-radius:4px; padding:2px 4px; }"
            f"QSpinBox:focus {{ background:{BG_INPUT_EDIT}; border-color:{FOCUS_BLUE}; }}"
            f"QGroupBox {{ color:{TEXT_SECONDARY}; border:1px solid rgba(255,255,255,17); border-radius:4px;"
            "            margin-top:8px; padding-top:8px; }}"
            "QGroupBox::title { subcontrol-origin:margin; left:8px; }"
            f"QPushButton {{ background:{BG_BUTTON}; color:{TEXT_PRIMARY}; border:1px solid {BORDER_NORMAL};"
            "              border-radius:4px; padding:4px 14px; }"
            f"QPushButton:hover {{ background:{BG_BUTTON_HOVER}; border-color:{HOVER_BORDER}; }}"
            f"QPushButton:focus {{ border-color:{FOCUS_BLUE}; }}"
        )
        from PyQt6.QtWidgets import QCheckBox
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(16, 16, 16, 16)

        # 开关
        chk = QCheckBox("启用自动保存")
        chk.setChecked(self._auto_save_enabled)
        lay.addWidget(chk)

        # 间隔时间
        grp = QGroupBox("保存间隔")
        grp_lay = QHBoxLayout(grp)
        grp_lay.setContentsMargins(10, 6, 10, 6)
        grp_lay.addWidget(QLabel("每隔"))
        spin = QSpinBox()
        spin.setRange(1, 120)
        spin.setValue(self._auto_save_interval)
        spin.setSuffix(" 分钟")
        spin.setFixedWidth(100)
        grp_lay.addWidget(spin)
        grp_lay.addStretch()
        lay.addWidget(grp)

        # 说明
        note = QLabel(
            "自动保存会在当前项目目录下创建 <b>autosave/</b> 子文件夹，\n"
            "每次生成带时间戳的新文件，<b>不覆盖</b>原文件。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:11px;")
        lay.addWidget(note)

        # 按钮
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._auto_save_enabled  = chk.isChecked()
            self._auto_save_interval = spin.value()
            # 持久化
            s = QSettings("Qascade", "MainWindow")
            s.setValue("autosave/enabled",  self._auto_save_enabled)
            s.setValue("autosave/interval", self._auto_save_interval)
            self._apply_auto_save_timer()
            state = "已开启" if self._auto_save_enabled else "已关闭"
            self._status.set_message(
                f"自动保存{state}（间隔 {self._auto_save_interval} 分钟）"
            )

    # ── 教程 / 说明书（帮助菜单 + 超链接模式）────────────────────────────
    def _open_tutorial(self):
        """帮助 ▸ 使用教程 / 说明书：打开内置教程面板（默认软件说明书）。"""
        self._show_tutorial_tab(0)

    def _show_tutorial_tab(self, prefer_index: int = 0):
        dock = getattr(self, "_tutorial_dock", None)
        if dock is None:
            return
        self._stop_beginner_tour()             # 重开教程 → 先退出进行中的巡览
        self._toggle_dock(dock, True)          # 内部会 _lazy_load_panels 建面板
        w = getattr(self, "_tutorial_widget", None)
        if w is None:
            return
        try:
            w.open_manual()
        except Exception:
            import logging
            logging.getLogger(__name__).warning("打开教程页失败", exc_info=True)

    def _open_url_external(self, url: str):
        """用系统默认浏览器打开 URL（面板顶栏「在系统浏览器打开」）。"""
        try:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(str(url)))
        except Exception:
            import logging
            logging.getLogger(__name__).warning("打开外部 URL 失败: %s", url)

    def _ensure_feature_callout(self):
        """懒建引出标注覆盖层（透明覆盖整窗，绘制在顶层）。"""
        ov = getattr(self, "_feature_callout", None)
        if ov is None:
            try:
                from .feature_callout import FeatureCalloutOverlay
                ov = FeatureCalloutOverlay(self)
                self._feature_callout = ov
            except Exception:
                import logging
                logging.getLogger(__name__).warning("引出标注覆盖层创建失败", exc_info=True)
        return ov

    def _on_tutorial_feature(self, feature_id: str):
        """超链接模式：说明页点功能链接 → 打开对应面板 + 引出标注高亮。

        映射见 `tutorial_features.resolve`（纯数据表）；解析失败静默降级，
        绝不因教程点击影响主流程。
        """
        try:
            from . import tutorial_features as _tf
            got = _tf.resolve(self, feature_id)
            if not got:
                return
            target, spec = got
            ov = self._ensure_feature_callout()
            if ov is None:
                return
            ov.show_for(target, spec.title, spec.body)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "教程功能高亮失败: %s", feature_id, exc_info=True)

    # ── 零基础模式：随浏览的引出标注层（教程阅读器顶栏入口）────────────────
    def _start_beginner_tour(self):
        """开启「边读说明书、边在界面上引出注释」的标注层（不打开任何面板）。"""
        try:
            self._show_tutorial_tab(0)
            w = getattr(self, "_tutorial_widget", None)
            ov = self._ensure_feature_callout()
            if w is None or ov is None:
                return
            from .tutorial_panel import BeginnerTour
            self._stop_beginner_tour()          # 保险：同一时刻只允许一个巡览
            self._park_tutorial_beside_main()   # 阅读窗让到一侧，别挡住要标注的界面
            tour = BeginnerTour(self, ov, panel=w, parent=self)
            tour.finished.connect(self._on_beginner_tour_finished)
            tour.progressed.connect(self._on_beginner_tour_progress)
            self._beginner_tour = tour
            tour.start()
            self._sync_tour_button()
        except Exception:
            import logging
            logging.getLogger(__name__).warning("启动零基础模式失败", exc_info=True)
            self._stop_beginner_tour()

    def _park_tutorial_beside_main(self) -> None:
        """把悬浮的教程阅读窗靠到屏幕右侧，让主界面左侧/顶部留给引出标注。

        零基础模式的标注画在主窗口上，若阅读窗居中压在中间，标注就被挡住了。
        这里只挪阅读窗（不动用户的主窗口），把它固定为「右侧阅读栏」——
        即用户要的「旁边放完整的文档供查阅」。仅对悬浮态生效；
        用户已把教程磁吸进主界面（分栏）时保持原样。
        """
        dock = getattr(self, "_tutorial_dock", None)
        if dock is None or not dock.isFloating():
            return
        try:
            from PyQt6.QtGui import QGuiApplication
            scr = QGuiApplication.screenAt(self.frameGeometry().center()) \
                or QGuiApplication.primaryScreen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            main = self.frameGeometry()
            w = max(480, min(1000, int(avail.width() * 0.34)))
            # 右侧贴边；若与主窗口完全不重叠则更好，重叠时也保证主窗左侧可见
            x = avail.right() - w - 12
            y = max(avail.top() + 12, main.top())
            h = min(avail.bottom() - y - 12, max(520, main.height()))
            if h >= 400:
                dock.resize(w, h)
            dock.move(x, y)
        except Exception:
            pass

    def _stop_beginner_tour(self):
        """退出零基础模式并清理（可重复调用；覆盖层/按钮状态一并复位）。"""
        tour = getattr(self, "_beginner_tour", None)
        self._beginner_tour = None
        if tour is not None:
            try:
                tour.stop()
            except Exception:
                import logging
                logging.getLogger(__name__).warning("退出零基础模式失败", exc_info=True)
        # 兜底：即使 tour 构造半途失败，也确保不残留标注
        ov = getattr(self, "_feature_callout", None)
        if ov is not None:
            try:
                if not ov.isHidden():
                    ov.dismiss()
            except Exception:
                pass
        self._sync_tour_button()

    def _sync_tour_button(self) -> None:
        w = getattr(self, "_tutorial_widget", None)
        tour = getattr(self, "_beginner_tour", None)
        if w is not None and hasattr(w, "set_tour_running"):
            import contextlib
            with contextlib.suppress(Exception):
                w.set_tour_running(bool(tour is not None and tour.active))

    # ── 调度台：路由层（MainWindow 是效果面板与调度台之间的唯一中介）──────────
    #  设计原因：DispatchPanel **不 import 任何效果面板**，否则面板间耦合会滚雪球。
    def _dispatch_capture_state(self, panel: str) -> dict:
        """抓某面板的可还原状态（调度台保存缓存时回调）。"""
        try:
            tgt = self._dispatch_panel_targets().get(str(panel))
            if tgt and callable(tgt.get("capture")):
                return dict(tgt["capture"]() or {})
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "调度台抓状态失败: %s", panel, exc_info=True)
        return {}

    #  路由表 `_dispatch_panel_targets()` 是"面板 id → (widget 属性, capture, recall)"
    #  的唯一真源；新增面板只在这里加一行。
    def _dispatch_panel_targets(self) -> dict:
        """面板 id → {"widget", "capture", "recall", "release"} 四个回调。"""
        def _mk(w, cap_name, rec_name):
            if w is None:
                return None
            return {"widget": w,
                    "capture": getattr(w, cap_name, None),
                    "recall": getattr(w, rec_name, None),
                    # ★ 释放回调（关闭场景用）：只清 NM 层是不够的 —— 面板自己的输出
                    #   定时器还在每帧重写，必须停面板输出（用户 2026-09-14 实报）。
                    "release": getattr(w, "dispatch_release_state", None)}

        return {
            "effect":     _mk(self._effect_browser_widget,
                              "dispatch_capture_state", "dispatch_recall_state"),
            "programmer": _mk(self._programmer_widget,
                              "dispatch_capture_state", "dispatch_recall_state"),
            "matrix":     _mk(self._matrix_pattern_widget,
                              "dispatch_capture_state", "dispatch_recall_state"),
            "linker":     _mk(self._processor_widget,
                              "dispatch_capture_state", "dispatch_recall_state"),
            # ★★ 2026-09-14 深查修正（静默丢数据）：audio 面板**自己**有完整的
            #   `dispatch_capture_state` / `dispatch_recall_state`（复用 bridge 的
            #   to_dict/load_dict），也确实上报 `EffectNode(panel="audio")`，但这里
            #   映射成了 `_mk(None, ...)` → 音频窗格存进场景时 state 恒为 `{}`、
            #   调用时恒失败 → **音频场景存了等于没存**（用户看不到任何报错）。
            "audio":      _mk(getattr(self, "_audio_bridge_widget", None),
                              "dispatch_capture_state", "dispatch_recall_state"),
            "clip":       _mk(getattr(self, "_timeline_widget", None),
                               "dispatch_capture_state", "dispatch_recall_state"),
            "route":      _mk(self._route_animator_window,
                              "dispatch_capture_state", "dispatch_recall_state"),
            "joystick":   _mk(None, "", ""),
        }

    def _dispatch_recall_nodes(self, entries: list, sync_selection: bool = False) -> bool:
        """把缓存节点还原到各自面板（调度台调用/同步时回调）。返回是否全部成功。

        ★★ 2026-09-14 用户补充定案（行为切分）：
          "如果仅仅是点击了保存区的 scene，那么不应该把设备选中状态刷新，也不应该
           调入监控区。仅仅作为一个效果的启用播放。"
          "如果启用'调入监控区修改'…把监控区切换为仅显示我新调入的设备。（并且
           topview 和各种页面的设备选中状态也切过去）"

        → `sync_selection` **默认 False**（纯点击 = 只播放，不动选中/监控区）；
          只有「编辑（调入监控区后修改）」传 True 才回写 TopView 选中。
        实现体在 `dispatch_flow`（god-class 禁止新增方法，见 CLAUDE.md 10.1）。
        """
        return _flow.recall_nodes(self, entries, sync_selection)

    def _dispatch_release_nodes(self, entries: list) -> bool:
        """把缓存节点从各面板**释放**（关闭场景 / 取消激活时回调）。

        ★★ 用户 2026-09-14："再点一次（hold 模式）它却没有 release，一直消不掉。"
        真因：只清 NetworkManager 层，面板定时器下一帧又写回 → 必须停面板输出。
        实现体在 `dispatch_flow`（god-class 禁止新增方法，见 CLAUDE.md 10.1）。
        """
        return _flow.release_nodes(self, entries)

    def _ensure_dispatch_panel(self, panel: str) -> None:
        """按面板 id 懒建对应 dock（还原时面板可能还没建过）。"""
        # ★★ 2026-09-18 用户实报（问题1："matrix 是漏网之鱼：没法存效果、不能回溯、
        #   不能唤起这个页面"）：matrix 的 recall/release 依赖本表把 dock 懒建出来、
        #   再由 `_lazy_load_panels` 赋值 `_matrix_pattern_widget`；缺失则整条链静默
        #   失效（点击方块"无任何加载"）。
        #   注：`audio` / `route` 不是 dock（独立窗口，见 `_audio_bridge_widget` /
        #   `_route_animator_window`），没有懒建路径，故不列入本表。
        dock_attr = {
            "clip": None,   # 时间线常驻，无需懒建
            "effect": "_effect_browser_dock",
            "programmer": "_programmer_dock",
            "matrix": "_matrix_pattern_dock",
            "linker": "_processor_dock",
        }.get(str(panel))
        if not dock_attr:
            return
        dock = getattr(self, dock_attr, None)
        if dock is None:
            return
        try:
            self._lazy_load_panels(dock)
        except Exception:
            pass

    def _dispatch_clip_report(self) -> None:
        """把时间线 clip 拉进总线（调度台刷新时调用；节流在 timeline 侧）。"""
        try:
            tw = getattr(self, "_timeline_widget", None)
            if tw is not None and hasattr(tw, "report_to_bus"):
                tw.report_to_bus()
        except Exception:
            pass

    def _dispatch_fixture_groups(self) -> list:
        """把项目的已创建设备组喂给调度台（子母文件夹归类用）。

        返回 `project.fixture_groups`（list[dict]）；无项目返回空列表。
        调度台侧用 group_resolver 反查"临时选中隶属哪个已创建组"。
        """
        try:
            proj = getattr(getattr(self, "_pm", None), "project", None)
            return list(getattr(proj, "fixture_groups", []) or [])
        except Exception:
            return []

    def _on_dispatch_cache_changed(self) -> None:
        """调度台改了缓存库 → 标脏并收集进项目（保证随项目持久化）。"""
        try:
            if self._pm is not None and self._pm.project is not None:
                from ..processing.dispatch import (get_effect_cache_store,
                                                   get_release_registry)
                # ★ 增量路径也必须带上 releases：原实现只写 cache，
                #   导致 releases 在增量保存后从工程里消失（实测踩到）。
                self._pm.project.dispatch_state = {
                    **get_effect_cache_store().to_dict(),
                    "releases": get_release_registry().to_dict(),
                }
                self._pm._mark_dirty()
        except Exception:
            pass

    def _on_beginner_tour_progress(self, index: int, total: int, title: str) -> None:
        """零基础模式（全注释铺开）：状态栏展示已铺开条数。"""
        try:
            if hasattr(self, "_status") and self._status is not None:
                self._status.set_message(f"零基础模式：{title}")
        except Exception:
            pass

    def _on_tutorial_chapter_seen(self, chapter_id: str) -> None:
        """说明书滚动到某章 → 零基础模式下把标注切到该章功能（**随浏览展示**）。

        这是用户要的主交互：读者读到哪，界面上就引出哪的注释；不读不弹、不打开面板。
        映射表见 `tutorial_features.CHAPTER_FEATURES`（唯一真源）。
        """
        tour = getattr(self, "_beginner_tour", None)
        if tour is None or not getattr(tour, "active", False):
            return
        try:
            from . import tutorial_features as _tf
            fid = _tf.feature_for_chapter(chapter_id)
            if fid:
                tour.show_feature(fid)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "随浏览标注切换失败: %s", chapter_id, exc_info=True)

    def _on_beginner_tour_finished(self) -> None:
        """巡览结束/退出（含用户 Esc/点框外关闭）：复位按钮与覆盖层。"""
        self._beginner_tour = None
        ov = getattr(self, "_feature_callout", None)
        if ov is not None:
            try:
                if not ov.isHidden():
                    ov.dismiss()
            except Exception:
                pass
        self._sync_tour_button()

    def _about(self):
        QMessageBox.about(
            self, f"关于 {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            "<p>专业 DMX 视觉控制软件<br>"
            "支持 Art-Net / sACN 协议</p>"
            "<p><small>© 2026 雾白纪 ULTRA</small></p>")

    def _restore_geometry(self):
        s     = QSettings("Qascade", "MainWindow")
        geom  = s.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            size = default_main_window_size(minimum=QSize(900, 600))
            self.resize(size.width(), size.height())

    @close_after_project_saves
    def closeEvent(self, event):
        if self._dirty:
            reply = QMessageBox.question(
                self, "退出", "项目未保存，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        from .timeline.waveform_load_session import shutdown_waveform_loader
        if not shutdown_waveform_loader(self):
            event.ignore()
            return
        s = QSettings("Qascade", "MainWindow")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        # R54-B：布局版本标记（v3 = 作废"空白窗 bug"污染布局后的格式，见 _LAYOUT_VER）。
        s.setValue("ui_layout_ver", _LAYOUT_VER)
        self._ui_timer.stop()
        self._auto_save_timer.stop()
        dispose_audio_workspace(self)
        from .window_shutdown import shutdown_window_workers
        shutdown_window_workers(self)
        if self._nm and hasattr(self._nm, "stop"):
            self._nm.stop()
            self._status.clear_timecode()
        super().closeEvent(event)


# ── 项目加载后的 GC 调优（模块级：上帝类禁止新增方法，CLAUDE.md 10.1）──────────
def _freeze_gc_after_project_load() -> None:
    """把已存活的长生命周期对象移入 GC 永久代，消除分代 GC 的全堆扫描尖峰。

    ★ 实测依据（2026-09-14，至冬1 / 4130 台 / 调度台选中 723 台，60 次 tick 取样）：
        · 默认 GC：`refresh_monitor` p50 9.3ms / max **70.3ms**，>30ms 尖峰 1 次/200 tick
          （肉眼可见的偶发顿挫）；
        · 调用本函数后：p50 10.0ms / max **16.5ms**，>30ms 尖峰 **0 次/200 tick**。
      尖峰成因是分代 GC 扫描整个堆（含加载期产生的大量长寿命对象），
      `gc.freeze()` 把这些对象移出扫描范围；**之后新分配的对象仍正常回收**。

    幂等且容错：GC 被禁用/平台不支持时静默跳过，绝不影响打开工程。
    """
    try:
        import gc
        gc.collect()      # 先把可回收的清掉，避免把垃圾冻结成永久对象
        gc.freeze()
    except Exception:
        pass


def _snapshot_value(v, _depth: int = 0):
    """★ R194/P1-5：快照值策略——帧对象共享、模型外壳拷贝、容器新建。"""
    if v is None or isinstance(v, (int, float, str, bool, bytes)) or _depth > 6:
        return v
    if type(v).__name__ in ("DMXFrame", "_LazyDMXFrame"):
        return v  # 帧对象按契约不可原地改写 → 共享（P1-5 性能核心）
    import dataclasses as _dc
    if _dc.is_dataclass(v) and not isinstance(v, type):
        nt = object.__new__(type(v))
        for f in _dc.fields(type(v)):
            setattr(nt, f.name, _snapshot_value(getattr(v, f.name, None), _depth + 1))
        return nt
    return _snap_container(v, _depth + 1)


def _snap_container(v, depth: int):
    """list/tuple/set/dict 新建（元素递归）；numpy 数组/任意对象按契约共享。"""
    if isinstance(v, list):
        return [_snapshot_value(x, depth) for x in v]
    if isinstance(v, tuple):
        return tuple(_snapshot_value(x, depth) for x in v)
    if isinstance(v, set):
        return set(v)
    if isinstance(v, dict):
        return {k: _snapshot_value(x, depth) for k, x in v.items()}
    return v


def _snapshot_track_shallow(tracks):
    """★ R194/P1-5（SV-03）：program 快照外壳拷贝——替代全量 deepcopy。

    契约（R194 A3 终波核定）：帧对象捕获后不可原地改写（录制/加载全部新建
    或整表替换；平滑走 copy 事务；全仓无 frame.data[:] 原地写）→ frames
    列表新建、帧对象共享即可隔离。容器字段全部新建（防 append 污染快照），
    numpy 数组/标量共享。泄漏检测：test_r193_audit_fixes 的变更隔离契约测试。
    """
    import dataclasses as _dc
    import copy as _copy
    out = []
    for t in tracks or []:
        if not _dc.is_dataclass(t) or isinstance(t, type):
            out.append(_copy.deepcopy(t))
            continue
        nt = object.__new__(type(t))
        for f in _dc.fields(type(t)):
            setattr(nt, f.name, _snapshot_value(getattr(t, f.name, None), 0))
        out.append(nt)
    return out


def _release_all_outputs_for_ui(nm) -> None:
    """★ R194/AUD-05：全清空的 GUI 口径——worker 启用时零帧延后 worker 下一拍
    （原锁内全域混音+发送 ≈100ms 卡 GUI）；worker 关闭回退同步 release。"""
    if (getattr(nm, "_running", False) and getattr(nm, "_output_worker_enabled", False)
            and hasattr(nm, "release_all_outputs")):
        nm.release_all_outputs(defer_send=True)
    else:
        nm.release_all_outputs()


def _clear_editor_release_bookkeeping() -> None:
    """Drop aggregate save ownership after the visible output has been cleared."""
    try:
        from ..processing.dispatch import EffectBus, get_release_registry
        EffectBus.instance().clear()
        get_release_registry().clear()
    except Exception:
        pass


def _matrix_load_from_topview(window, matrix_widget, ids, *, explicit=True) -> None:
    """TopView 选择变化 → 载入矩阵面板（**抑制回传**，避免绕圈）。

    ★ 2026-09-14：矩阵新增了"设备选中回传 TopView"的出口后，这条**反向**驱动必须
      显式抑制回传，否则会 TopView → 矩阵 → TopView 无限绕。
      与 Programmer 的 `_load_programmer_from_topview_selection` 同款约定。
    """
    from .matrix_selection_bridge import preserve_restored_matrix_scope
    if not explicit and preserve_restored_matrix_scope(window, matrix_widget):
        return
    if matrix_widget is None or not hasattr(matrix_widget, "load_fixture_group"):
        return
    tv = getattr(window, "_topview_widget", None)
    if tv is None:
        return
    positions = (tv.get_all_fixture_positions()
                 if hasattr(tv, "get_all_fixture_positions")
                 else tv.get_fixture_positions())
    old = bool(getattr(matrix_widget, "_suppress_topview_selection_emit", False))
    try:
        setattr(matrix_widget, "_suppress_topview_selection_emit", True)
        matrix_widget.load_fixture_group(
            "__topview_live__", list(ids or ()), "TopView实时选择",
            getattr(window, "_pm", None), positions)
    except Exception:
        pass
    finally:
        setattr(matrix_widget, "_suppress_topview_selection_emit", old)


def _restore_topview_state(topview, project):
    from .project_selection_restore import restore_topview_state
    restore_topview_state(topview, project)


def _call_first_available(widget, methods) -> None:
    """按顺序调用 widget 上第一个可用的无参方法（调用方负责 None 判定）。"""
    if widget is None:
        return
    for name in methods:
        fn = getattr(widget, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            return


def _GROUP_SAVE_REFRESH_TARGETS(self):
    """保存设备组后需要刷新组列表的面板 → (widget, 候选方法名)。

    ★ 2026-09-20：从 `_on_topview_fixture_group_saved` 抽出为**模块级**函数 ——
      MainWindow 是上帝类（方法数硬顶 255），内联循环也会把该函数的嵌套深度
      顶过门禁（实测深度 5 > 4）。
    """
    return (
        (getattr(self, "_fixture_group_widget", None), ("refresh", "_refresh_fixtures")),
        (getattr(self, "_programmer_widget", None), ("_refresh_fixtures", "refresh")),
        (getattr(self, "_matrix_pattern_widget", None),
         ("_on_project_changed_for_matrix", "refresh_filter_group_combo")),
    )


def _xy_info(win, msg: str) -> None:
    """把云台配对提示写到状态栏（空消息不发；状态栏缺失时静默）。"""
    if not msg:
        return
    try:
        win._status.showMessage(msg)
    except Exception:
        pass




def _ld_channel_types(proj, fix_by_id) -> dict:
    """{fid: {通道类型}}（供 R127 云台配对判定；缺定义时跳过该设备）。"""
    defs = {str(getattr(d, "id", "")): d for d in (getattr(proj, "fixture_defs", None) or [])}
    out: dict = {}
    for fid, fi in (fix_by_id or {}).items():
        d = defs.get(str(getattr(fi, "def_id", "") or ""))
        if d is None:
            continue
        out[str(fid)] = {str(c) for c in (getattr(d, "channels", None) or [])}
    return out


def _fid_of_guid(guid_to_key, seq_to_fid, g: str) -> str:
    """GUID → fid（经 (type_name, seq_id) 反查；seq_id 跨类型不唯一）。"""
    row = guid_to_key.get(str(g))
    if row is None:
        return ""
    fid = seq_to_fid.get((str(getattr(row, "type_name", "") or ""),
                          getattr(row, "seq_id", None)))
    return str(fid) if fid else ""


def _collect_objects_by_guid(project_root) -> dict:
    """project.xml 全树 → {GUID: 带 TypeName 的元素}（供祖先链解析）。"""
    objs: dict = {}
    for el in project_root.iter():
        g = el.get("GUID")
        if g and el.get("TypeName"):
            objs.setdefault(str(g), el)
    return objs


def _xy_gimbal_result(guid_to_key, pairs, out):
    """(逻辑设备列表, 用户提示文案)。"""
    if not out:
        return [], ""
    rx0 = str(getattr(guid_to_key.get(pairs[0][0]), "type_name", "") or "")
    ry0 = str(getattr(guid_to_key.get(pairs[0][1]), "type_name", "") or "")
    return out, (f"已识别 {len(out)} 组二维云台（父设备管 X + 嵌套子设备管 Y），"
                 f"已合成组合设备「{rx0}+{ry0}」——选中后摇杆 XY 两轴均可控。")


def _xy_gimbal_entry(ctx, guid_pair, index: int):
    """构造一组云台的逻辑设备 dict；不适用（缺 fid / 已被手动合并）返回 None。"""
    guid_to_key, seq_to_fid, fix_by_id, consumed = ctx
    xg, yg = guid_pair
    xf = _fid_of_guid(guid_to_key, seq_to_fid, xg)
    yf = _fid_of_guid(guid_to_key, seq_to_fid, yg)
    skippable = (not xf or not yf or xf in consumed or yf in consumed
                 or xf not in fix_by_id or yf not in fix_by_id)
    if skippable:
        return None
    rx = str(getattr(guid_to_key.get(xg), "type_name", "") or "")
    ry = str(getattr(guid_to_key.get(yg), "type_name", "") or "")
    from ..core.models import LogicalDevice
    return LogicalDevice(
        name=f"{rx}+{ry} #{index + 1}", member_fixture_ids=[xf, yf],
        pos_x=float(getattr(fix_by_id[xf], "pos_x", 0.0) or 0.0),
        pos_y=float(getattr(fix_by_id[xf], "pos_y", 0.0) or 0.0),
        kind="composite", control_mode="decomposed",
        merge_group=f"XY云台·{rx}").to_dict()



def _at(el, key: str):
    """元素属性快捷读取（缺失/无值返回 None）。"""
    v = el.get(key)
    return v if (v is not None and str(v).strip() != "") else None


def _ancestor_guids(guid: str, objs, max_depth: int = 16):
    """沿 ParentObjectGUID 向上收集祖先 GUID（不含自身；防环/防越界）。"""
    out = []
    seen = {str(guid)}
    cur = str(guid)
    for _ in range(max_depth):
        el = objs.get(cur)
        if el is None:
            break
        p = (_at(el, "ParentObjectGUID") or "").strip()
        if not p or p in seen or p.startswith("00000000"):
            break
        out.append(p)
        seen.add(p)
        cur = p
    return out


def _split_single_axis_guids(chan_by_fid):
    """把设备分成「只含 X 轴」与「只含 Y 轴」两组 fid（只看物理通道）。"""
    has_x = {str(f) for f, ch in (chan_by_fid or {}).items() if "motor_x" in ch}
    has_y = {str(f) for f, ch in (chan_by_fid or {}).items() if "motor_y" in ch}
    return has_x - has_y, has_y - has_x


def _pair_x_ancestor(y_guid: str, objs, x_only_guids: set):
    """Y 设备 → (其 X 轴祖先 guid, Y guid)；无祖先返回 None。"""
    if not y_guid:
        return None
    hit = next((a for a in _ancestor_guids(y_guid, objs) if a in x_only_guids), None)
    return (hit, y_guid) if hit is not None else None


def detect_xy_axis_pairs(guid_to_key, chan_by_fid, objs):
    """检测「父设备管 X + 嵌套子设备管 Y」的二维云台配对（用户 2026-09-21 定案）。

    判定条件（**两条同时满足**才配对，避免误拟合无关设备）：
      ① Y 设备是 X 设备的**后代**（沿 ParentObjectGUID 向上可达）；
      ② X 设备含 motor_x 且**不含** motor_y；Y 设备含 motor_y 且**不含** motor_x。
    其余情形（同名但不嵌套、互为兄弟、单轴设备无配对）一概不拟合。

    返回 [(x_guid, y_guid), ...]。
    """
    x_only, y_only = _split_single_axis_guids(chan_by_fid)
    if not x_only or not y_only:
        return []
    guid_by_fid = {}
    for g, row in (guid_to_key or {}).items():
        rfid = str(getattr(row, "id", "") or "")
        if rfid:
            guid_by_fid.setdefault(rfid, str(g))
    x_only_guids = {guid_by_fid[f] for f in x_only if f in guid_by_fid}
    return [pr for yf in sorted(y_only)
            for pr in (_pair_x_ancestor(guid_by_fid.get(yf, ""), objs, x_only_guids),)
            if pr is not None]


def _build_xy_gimbal_devices(ctx, project_root, chan_by_fid):
    """识别二维云台并合成组合设备（R127；返回 (逻辑设备 dict 列表, 提示文案)）。

    用户定案（2026-09-21）::

        "里面有一个设备叫'全能卡二维单卡'…你识别到叫做二维但是发现里面有两个设备
         分别管理X Y 的，你应该把xy轴合并控制，这样摇杆才对…其它的名称的设备和不
         具有这种特点的情况不要这样拟合。"

    为什么要合并：只选父设备（管 X）时 `motor_y` 不在已载通道里 → 摇杆推 Y 无反应。
    两者一起载入后，`_ordered_fixtures_for_channel` 按"该设备是否支持此通道"自动
    分流 → 推 X 只写给管 X 的、推 Y 只写给管 Y 的，摇杆代码无需改动。

    control_mode 用 **decomposed**（不能 merged：后者把同一值广播给两轴，X/Y 变联动）。

    判据严格（两条同时满足，全部在 `detect_xy_axis_pairs` 内实现）：
      ① Y 设备是 X 设备的**嵌套后代**（沿 ParentObjectGUID 可达）；
      ② X 含 motor_x 不含 motor_y；Y 含 motor_y 不含 motor_x。
    不满足者一律不配对 —— 实测真实工程：24 组正确配对，同族"一维两仪"（18+1 台，
    只有 X 且无嵌套 Y 后代）零误配。
    """
    guid_to_key, seq_to_fid, fix_by_id, composite_lds = ctx
    if not guid_to_key or project_root is None:
        return [], ""
    try:
        pairs = detect_xy_axis_pairs(
            guid_to_key, chan_by_fid, _collect_objects_by_guid(project_root))
        consumed = {str(m) for ld in (composite_lds or [])
                    for m in (ld.get("member_fixture_ids") or [])}
        # ★ 用**算好的 consumed 集合**重建 ctx —— 直接把外层 ctx 传进去的话，
        #   第 5 位是 composite_lds（dict 列表）而非设备 id 集 → "已手动合并"判定失效。
        entry_ctx = (guid_to_key, seq_to_fid, fix_by_id, consumed)
        out = [ld for i, pr in enumerate(pairs)
               if (ld := _xy_gimbal_entry(entry_ctx, pr, i)) is not None]
        return _xy_gimbal_result(guid_to_key, pairs, out)
    except Exception as e:
        return [], f"二维云台配对检测跳过：{e}"


def _is_default_all_selection_once(win, ordered, tv) -> bool:
    """本次回写是否为面板重载后吐出的「默认全选」自动状态（R121 用户实报修复）。

    场景：保存设备组 → `project_changed` → 面板 `_bootstrap_from_pm` /
    `load_fixture_group` 重载 → 面板回吐一个"全部设备"的 `fixture_selection_changed`。
    若直接写入 TopView，用户刚框选的子集就被**扩成整组**（实测：奇数子集 → 全部 6 台），
    随后"给值 / 存场景"全部作用在被放大的作用域上 —— 用户报的"存下来强行变成了
    整个组都有值"、"存的东西调用时没效果"。

    判据：发来的 `ordered` == 工程全设备，且 TopView 此刻有**不同的**真实选择 → 忽略。
    """
    if not ordered or tv is None:
        return False
    try:
        cur = [str(x) for x in (tv._ordered_selected_ids() or []) if str(x)]
    except Exception:
        return False
    if not cur or set(ordered) == set(cur):
        return False
    return bool(win._is_project_all_fixture_selection(ordered))


def _wire_transport_play_state(win, ctrl) -> None:
    """transport 播放按钮两态同步（R123 时迁到模块级以守基线；隔离功能已移除）。"""
    win._managed_connect(
        "timeline:transport:play_state_on", ctrl.playback_started,
        lambda _pos: win._transport.set_playing(True))
    win._managed_connect(
        "timeline:transport:play_state_off", ctrl.playback_stopped,
        lambda _pos: win._transport.set_playing(False))


def _selected_by_topview(sender) -> bool:
    """该面板当前的选择是否**由 TopView 驱动**（或正被抑制）→ 不该再回写 TopView。

    · `_current_group_id == "__topview_live__"`：面板正跟随 TopView 实时选择；
    · `_suppress_topview_selection_emit`：调用方显式抑制（如 Programmer/矩阵的
      "从 TopView 载入"路径）。
    取不到属性时按"是"处理（保守：宁可不回写，也不制造绕圈）。
    """
    try:
        return (str(getattr(sender, "_current_group_id", "") or "") == "__topview_live__"
                or bool(getattr(sender, "_suppress_topview_selection_emit", False)))
    except Exception:
        return True


MainWindow._confirm_discard_for_project_change = _confirm_discard_for_project_change
