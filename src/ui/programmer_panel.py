"""
DMXTimeline — Programmer Panel  v21
工作流重构：
  1. Patch 配接全部设备（输出）
  2. Fixture 界面管理设备组
  3. Programmer 左侧选择设备组 → 中间按通道类型折叠展示 → 右侧操作数值/预设/函数

布局：
    ┌─────────────────────────────────────────────────────────────────┐
    │  工具栏：Release | Full Open | 平滑 | 刷新                       │
    ├──────────────────┬──────────────────────────────────────────────┤
    │  左侧：设备组选择 │  中部：通道类型折叠列表（同种通道合并）       │
    │  [全部设备]       │  ▶ Intensity  [合并推杆]                     │
    │  [Group 1]       │  ▶ Color       [RGB拾色器]                   │
    │  [Group 2]       │  ▶ Motor XY    [XY摇杆]                      │
    │  ...             │  ▶ Valve/Pump  [推杆]                        │
    └──────────────────┴──────────────────────────────────────────────┘

★ R182：静态位置 CUE 存储前端（Store 按钮 / 静态 CUE 子页 / 静态卡片）已按用户
  指令删除；`CueGrid` 仅保留为**自映射 Cue 载体**（`add_generalized_cue` 依赖），
  静态值快照仍经 `snapshot_static_values()` 供调度台消费，CUE 数据模型
  （programmer_cue_store）保留不动。
"""
from __future__ import annotations
import uuid
import math
import random as _random
import json
import logging
import functools
import time as _time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from PyQt6 import sip
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QDialog, QDialogButtonBox,
    QLineEdit, QScrollArea, QFrame, QSizePolicy,
    QGridLayout, QTabWidget, QSlider, QSpinBox,
    QDoubleSpinBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QColorDialog,
    QMessageBox, QGroupBox, QCheckBox, QTextEdit, QApplication,
    QListWidget, QListWidgetItem, QInputDialog,
    QTreeWidget, QTreeWidgetItem, QFileDialog,
    QGraphicsDropShadowEffect, QToolTip,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QTimer, QPointF, QRectF, QSize,
    QThread,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath, QMouseEvent,
)

from .fixture_group_activation import selected_groups, preserve_group_space
from . import wing_flag_controls as _wing_flags

from ..utils.wing_block import (
    WING_DIRECTION_LABELS,
    WING_DIRECTION_LOW_TO_HIGH,
    WING_DIRECTION_MODES,
    WING_DIRECTION_SYMBOLS,
    build_wing_block_units,
    normalize_wing_direction,
    wing_direction_factor,
    wing_block_distribution_slots,
)
from ..utils.identity_cache import bounded_put
from ..core.dmx_channel_values import motor_8bit_to_16bit_pair as _logical_motor_pair
from ..engine.xy_reference import (
    LOGICAL_CENTER,
    logical_z_to_world_angle,
)

from .programmer_cue_store import (
    get_programmer_cue_store,   # R182：仅项目级清理用（前端展示已删除）
)
from .trajectory_curve import (
    get_trajectory_curve_store, TrajectoryCurve, TrajectorySegment,
    DIR_WHOLE, DIR_LTR, DIR_RTL, DIR_FTB, DIR_BTF, DIR_CTR, DIR_RTC, DIR_CW, DIR_CCW, DIR_ALT,
    SEGMENT_DIRECTIONS, DIRECTION_LABELS,
    SPATIAL_SCOPES as TRAJ_SPATIAL_SCOPES, SCOPE_LABELS as TRAJ_SCOPE_LABELS,
    GROUP_MODES as TRAJ_GROUP_MODES, GROUP_MODE_LABELS as TRAJ_GROUP_MODE_LABELS,
    SCOPE_FOLLOW, GROUP_PARALLEL,
    CURVE_TYPE_PATH, CURVE_TYPE_SHAPE, CURVE_TYPES, CURVE_TYPE_LABELS,
)
from .trajectory_curve_widget import TrajectoryCurveView
from .widget_kit import (
    EmptyState,
    NumSlider,
    PanelSection,
    prop_row, scope_qss,
)
from . import qascade_theme as _th
from . import programmer_runtime_targets as _runtime_targets
from .main_icon_library import load_tabler_pixmap  # R173：绑定指示器 emoji→tabler
from src.ui.programmer_panel_lifecycle import (  # R178：上帝类搬出的生命周期 helper
    apply_full_open,
    build_programmer_scroll,
    emit_range_batch_status,
    finalize_programmer_init,
)
from .programmer_automation import (  # R178：DAW 自动化录制（四模式+录制源）新模块
    automation_begin,
    automation_is_read,
    automation_read_step,
    automation_reduce,
    make_icon_button,
    set_play_icon,
    style_record_button,
    automation_tick,
    build_recording_section,
)
# ★ R183 批次 I：Programmer 深查 17 项的新逻辑（上帝类不得新增方法 → 全在新模块）
from . import programmer_tools as _ptool          # 纯函数 + 摇杆路径送轨迹
from . import programmer_highlight as _phl        # 试灯/后选过滤/排序/步进/三级释放/hover
from . import programmer_palette as _ppal         # Palette 调色板（存储 + UI 条）
from . import programmer_color_mix as _pcm        # Color Mix 编辑器（色轮/基色/色纸）
from . import programmer_sheet as _psheet         # 只读 Fixture Sheet + 最近效果快捷条
from . import programmer_inject as _pinj          # 注入助手（模块级；上帝类零新增方法）
from .programmer_shared_state_ui import bind_strip_signals
from .programmer_author_scope import capture_editor_scope, restore_editor_state
from . import programmer_workspace_ui as _pwui
from . import programmer_channel_ui as _pcui
from .programmer_strips import (   # R183：条带控件注入（项 9/13/16）
    _r183_prepare_range_write, _r183_set_range_readout,
    _r183_strip_hover_tooltip, _r183_strip_label_text,
)
from .programmer_inject import (
    _r183_init_state, _r183_build_toolbar_extras, _r183_build_left_column, FlowLayout,
    _r183_build_ch_column, _r183_after_group_load, _r183_add_color_mix_button,
    _r183_add_row_align_controls, _r183_show_row_context_menu,
    _r183_add_joystick_record, _r183_narrow_to_post_filter,
)

log = logging.getLogger(__name__)

#: 撤销域（Programmer 相关字段）—— 见 core.project_manager
try:
    from ..core.project_manager import DOMAIN_PROGRAMMER as _DOMAIN_PROGRAMMER
except Exception:  # pragma: no cover
    _DOMAIN_PROGRAMMER = None

#: 调度台用：记录本面板实例上次上报过的窗格身份键（id(panel) → set）。
#: 仅在"本面板窗格减少时删掉消失的键"时使用，避免误删其它面板/实例的窗格。


def _sync_scope_ids(tv, fixture_ids) -> list:
    """从本次写入的设备中筛出处于 TopView 同步态者（Z 组命令的作用范围）。

    tv 不可用/无该 API/筛完为空 → 返回 []（调用方据此跳过推送，行为同旧版）。
    """
    if not fixture_ids or not _qobject_alive(tv)             or not hasattr(tv, "xy_reference_mode_for_fixture"):
        return []
    try:
        return [str(fid) for fid in fixture_ids
                if tv.xy_reference_mode_for_fixture(str(fid)) == "sync"]
    except Exception:
        return []


def _resolve_topview(panel):
    """取 TopView 面板引用：优先面板自身，其次主窗兜底（接线顺序无关）。

    ★★ 2026-09-19 用户实报（"同步做不了 / 摇不动被锁住"）：
      `ProgrammerPanel._topview_ref` 只在 **Programmer 创建那一刻**、且
      `MainWindow._topview_widget` 已存在时才被赋值（main_window:4430）。
      两个面板都懒加载 → 先开 Programmer 后开 TopView（或反之）时该引用恒为
      None → `_sync_scope_ids` 恒返回 [] → **同步 Z 命令永远推不出去**：
      用户拖 Z 却没有任何世界角联动（"同步做不了"），或残留命令永不清除
      （"摇不动被锁住"）。按需回填，与顺序无关。
    """
    tv = getattr(panel, "_topview_ref", None)
    if _qobject_alive(tv):
        return tv
    try:
        win = panel.window()
        tv = getattr(win, "_topview_widget", None)
        if _qobject_alive(tv):
            panel._topview_ref = tv
            return tv
    except Exception:
        pass
    return None


def _clear_sync_z(nm, scope=None) -> None:
    """清除 Z 组命令（会话级实时状态；残留会把 Z 轴永久按在旧角上）。

    `scope` 非 None 时随角度一起送（既有测试钉住的调用形态：回中位传
    `(None, [设备])`）；scope=None = 无同步设备时的**兜底清理**（只在确实有
    残留命令时才发 —— 无残留则一个调用都不发，保持"无同步行不推送"的既有语义）。
    """
    if nm is None:
        return
    if scope is None and getattr(nm, "_xy_reference_z_command_deg", None) is None:
        # 无范围、无残留 → 无可清（调用方在"默认模式也发信号"路径会带范围进来，
        # 不会落到本分支）。
        return
    if hasattr(nm, "set_xy_reference_z_command"):
        try:
            nm.set_xy_reference_z_command(None, scope)
        except Exception:
            log.exception("clear xy_reference_z_command failed")


def _push_sync_z_command(panel, angle_deg, fixture_ids) -> None:
    """同步组 Z 命令推送（`ProgrammerPanel._on_sync_z_command` 的实现）。

    ★ 2026-09-18：命令随身携带本次写入设备集（旧实现全组广播，只选中内圈 12 台
      时全部扇子 Z 都在动）。

    ★★ 2026-09-19 用户实报修复（"选中同步模式…准备做电机通道的时候我发现我要么
      就摇不动被锁住，要不就是只能非同步模式，同步做不了"）：
      原实现 `if not sync_ids: return` —— **清除命令也被这道门挡住**。而
      `set_xy_reference_z_command` 在引擎里是**强制覆写** z_ids 内设备的 Z 输出
      （`_apply_z_output` 优先于一切编辑器写入），**唯一清除入口**就是带
      angle=None 的这次调用。后果链：曾拖过 Z（命令=某角）→ 之后设备不在同步态
      （取消同步/切组/引用未接）→ 拖 Z 回中位时 sync_ids 为空 → 直接 return
      → 命令永久残留 → Z 被锁死，必须"非同步模式"或重启。
      实测：注入 30° 后非同步态拖回中位，命令仍是 30.0。
      修：**回中位（angle=None）无条件放行**（清除不该依赖"此刻有没有同步设备"）；
      非同步设备带角度被拖时，只在**确有残留命令**才兜底清（无残留不产生调用，
      保持既有"无同步行不推送"语义）。
    """
    nm = getattr(panel, "_nm_ref", None)
    written = [str(f) for f in (fixture_ids or ()) if str(f)]
    if angle_deg is None:
        # 回中位 = 明确的清除意图：无条件送（带本次设备集，与既有调用形态一致）。
        _clear_sync_z(nm, written or None)
        return
    sync_ids = _sync_scope_ids(_resolve_topview(panel), written)
    # ★★ 2026-09-20 用户定案（"不要只有同步才发信号，默认模式也要发信号，及时随
    #   clip 接管规则覆盖"）：**默认模式同样发信号**（带本次写入范围显式清除）——
    #   旧实现"只在真有残留时才清"会让命令停在上一次同步态，切默认后无人覆盖。
    if not sync_ids:
        _clear_sync_z(nm, written or None)
    else:
        _set_sync_z_angle(nm, angle_deg, sync_ids)


def _set_sync_z_angle(nm, angle_deg, sync_ids) -> None:
    """把统一世界角写进 NM（无该 API 时静默跳过；R122 小助手）。"""
    if nm is None or not hasattr(nm, "set_xy_reference_z_command"):
        return
    try:
        nm.set_xy_reference_z_command(angle_deg, sync_ids)
    except Exception:
        log.exception("set_xy_reference_z_command failed")


def _qobject_alive(obj) -> bool:
    """Return True only when a PyQt wrapper still owns a live C++ QObject.

    Qt widgets deleted via deleteLater() may leave a Python wrapper behind; any
    method call on that stale wrapper raises RuntimeError: wrapped C/C++ object
    has been deleted.  All UI-reference caches in Programmer must pass through
    this guard before touching cached widgets.
    """
    if obj is None:
        return False
    try:
        return not sip.isdeleted(obj)
    except Exception:
        return False

# 输出平滑处理器
try:
    from ..engine.output_smoother import get_smoother, OutputSmoother, BUILTIN_SMOOTH_PRESETS
    _SMOOTHER_AVAILABLE = True
except ImportError:
    get_smoother = None
    OutputSmoother = None
    BUILTIN_SMOOTH_PRESETS = {}
    _SMOOTHER_AVAILABLE = False

# 延迟导入音频驱动和通用控制器（避免循环依赖）
try:
    from .audio_driver import AudioDriverWidget, get_audio_driver
    _AUDIO_AVAILABLE = True
except Exception:
    _AUDIO_AVAILABLE = False
try:
    from .channel_controller import (
        UniversalFader, UniversalSlider, UniversalJoystick,
        ChannelMapping,
    )
    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

# ─── 颜色常量（统一引用 主题设计令牌，见 qascade_theme.py） ────────────────
BG_MAIN     = _th.BG_ROOT          # #1D1D1D 页面根底
BG_PANEL    = _th.BG_EDITOR        # #303030 面板/卡片基底
BG_TOOLBAR  = _th.BG_EDITOR        # 工具栏/标题条（原灰黑 #2a2a2a 收敛）
BG_HEADER   = _th.BG_EDITOR
BG_ROW_EVEN = "#242424"            # 次级行底（输入类深底家族）
BG_CELL_SET = _th.SELECT_BLUE      # 选中深蓝底统一 select_blue
BG_CELL_SEL = _th.SELECT_BLUE
BG_CELL_MRK = _th.WARN_ORANGE     # 标记格：琥珀警示语义保留
FG_MAIN     = _th.TEXT_PRIMARY     # #E6E6E6
FG_DIM      = _th.TEXT_SECONDARY   # #999999
FG_ACCENT   = _th.FOCUS_BLUE       # #71A8FF 强调文字/焦点
FG_GREEN    = _th.BRIGHT_SUCCESS
FG_ORANGE   = _th.BRIGHT_WARN
FG_RED      = _th.BRIGHT_ERROR
FG_CYAN     = _th.BRIGHT_INFO
FG_PINK     = _th.TEXT_SECONDARY   # 原品红已无专属语义，归并次级文字

# 通道类别颜色：并轨全站统一 CATEGORY_COLORS（qascade_theme），键名语义不变。
# 中央表没有的键按就近语义映射（与 channel_controller 波次同一张对照）：
#   valve→amber、pump→uv、motor_x/y/z→pan/tilt/fx、fine 档随其粗调档、placeholder→other。
CH_COLORS = {
    "dimmer":       _th.CATEGORY_COLORS["dimmer"],
    "R":            _th.CATEGORY_COLORS["red"],
    "G":            _th.CATEGORY_COLORS["green"],
    "B":            _th.CATEGORY_COLORS["blue"],
    "W":            _th.CATEGORY_COLORS["white"],
    "valve":        _th.CATEGORY_COLORS["amber"],
    "motor_x":      _th.CATEGORY_COLORS["pan"],
    "motor_y":      _th.CATEGORY_COLORS["tilt"],
    "motor_z":      _th.CATEGORY_COLORS["fx"],
    "motor_x_fine": _th.CATEGORY_COLORS["pan"],
    "motor_y_fine": _th.CATEGORY_COLORS["tilt"],
    "motor_z_fine": _th.CATEGORY_COLORS["fx"],
    "pump":         _th.CATEGORY_COLORS["uv"],
    "placeholder":  _th.CATEGORY_COLORS["other"],
}

FUNCTION_CHANNEL_PREFIX = "function_"
MOTOR_FINE_CHANNELS = {
    "motor_x": "motor_x_fine",
    "motor_y": "motor_y_fine",
    "motor_z": "motor_z_fine",
}
MOTOR_FINE_TO_COARSE = {fine: coarse for coarse, fine in MOTOR_FINE_CHANNELS.items()}
# 电机类通道集合(粗+细 6 通道): Programmer 条带双击归中 128 的适用集合
MOTOR_CHANNELS = frozenset(MOTOR_FINE_CHANNELS.keys()) | frozenset(MOTOR_FINE_TO_COARSE.keys())


def _is_function_channel(ch_type: str) -> bool:
    return str(ch_type or "").startswith(FUNCTION_CHANNEL_PREFIX)


def _function_channel_number(ch_type: str) -> int:
    try:
        return int(str(ch_type).split("_", 1)[1])
    except Exception:
        return 0


def _ch_color(ch_type: str) -> str:
    if _is_function_channel(ch_type):
        return "#607D8B"
    return CH_COLORS.get(ch_type, "#999999")


def _ch_label(ch_type: str, names: dict[str, str] | None = None) -> str:
    if _is_function_channel(ch_type):
        if names:
            custom = str(names.get(str(ch_type), "") or "").strip()
            if custom:
                return custom
        n = _function_channel_number(ch_type)
        return f"功能通道 {n}" if n > 0 else "功能通道"
    return CH_LABELS.get(ch_type, ch_type)


def _motor_8bit_to_16bit_pair(value: float) -> tuple[int, int]:
    """Map a logical 0-255 motor position to DMX coarse/fine channels.

    Integer positions keep the coarse byte unchanged; fractional positions fill
    the fine byte: 128.5 -> (128, 128).
    """
    return _logical_motor_pair(value)


@functools.lru_cache(maxsize=512)
def _norm_channel_type_cached(key: str) -> str:
    return key.strip().lower()


def _norm_channel_type(ch_type: Any) -> str:
    # 纯函数：把通道类型（枚举或字符串）归一化为小写字符串。一次 _refresh_fixtures
    # 实测调用 10万+ 次，故对可哈希的字符串结果做 lru_cache。
    # 行为与原实现 `str(getattr(ch_type,"value",ch_type) or "").strip().lower()` 完全一致。
    raw = getattr(ch_type, "value", ch_type)
    if not raw:                      # 复刻原实现的 `or ""`：None/0/False/"" 一律视作空
        return ""
    if isinstance(raw, str):
        return _norm_channel_type_cached(raw)
    return str(raw).strip().lower()


def _is_light_channel(ch_type: Any) -> bool:
    return _norm_channel_type(ch_type) in {"r", "red", "g", "green", "b", "blue", "w", "white"}


def _fixture_has_light_channels(fd) -> bool:
    return any(_is_light_channel(ch) for ch in (getattr(fd, "channels", []) or []))


def _fixture_supports_logical_channel(fd, ch_type: str) -> bool:
    if fd is None:
        return False
    target = _norm_channel_type(ch_type)
    if target == "dimmer":
        return _fixture_has_light_channels(fd)
    return any(_norm_channel_type(ch) == target for ch in (getattr(fd, "channels", []) or []))


#: 由**自动驱动**写入的层（不是用户编辑）。判定口径 —— **按层名**，不按代码路径：
#:   · `"audio"`  —— 音频驱动/频谱映射每帧写（用户没碰任何控件）；
#:   · `"matrix"` —— 矩阵/像素效果每帧写（动画自动跑）。
#: 反面（**必须**仍算用户编辑，R141 明确要求别误伤）：
#:   · `"fader"`  —— 推杆/条带/调度台格子，以及 **MIDI/OSC 遥控**；
#:   · `"joystick"` —— 用户亲手拖屏幕摇杆、或由实体摇杆绑定转发的摇杆输入
#:     （`inject_external_joystick` / `joystick_control_binding`），是人的意图，
#:     不是自动驱动。
#: ★★ R141：外部驱动**只更新层值**，不进 `_marked` —— 否则用户没碰控件，
#:   CUE/场景里却出现"被音频驱动的通道 + 当时的瞬时值"，重放时值被冻结成那一刻。
_EXTERNAL_DRIVER_LAYERS: frozenset = frozenset(("audio", "matrix"))


def _is_external_driver_layer(layer: Any) -> bool:
    """该层是否由自动驱动写入（音频/矩阵）——是则不得记入 `_marked`。"""
    return str(layer or "") in _EXTERNAL_DRIVER_LAYERS


def _external_residue_cells(row) -> set:
    """本行"被外部驱动写过"的格集合 `{(fid, ch_type)}`（惰性创建，不改构造函数）。

    为什么需要一张**独立**记录：`_flush_live_output` 会在音频停止时清掉 audio
    **层**（`row._layers["audio"].clear()`），而 `_values`（显示缓存）按设计不清 ——
    于是"音频停后"这个时间点上，光看层已经**指认不出**哪些格是音频写的。
    """
    cells = getattr(row, "_external_cells", None)
    if cells is None:
        cells = set()
        try:
            row._external_cells = cells
        except Exception:
            return set()
    return cells


def _note_external_cell(row, fid: str, ch_type: str) -> None:
    """外部驱动（音频/矩阵）写值 → 记下该格，供静态快照排除。"""
    _external_residue_cells(row).add((str(fid), str(ch_type)))


def _record_layer_cell(row, fid: str, ch_type: str, merged: int, external_driver: bool) -> None:
    """写侧收口：外部驱动只记残留（**不进 `_marked`**），用户来源照旧标记。

    ★★ R141（音频值泄漏进 CUE）：`_marked` 的语义是"用户已动过"，是 CUE/场景快照的
    作用域来源。此前音频每一帧都标记 → 用户没碰控件，存下来的 CUE 却含"被音频驱动的
    通道 + 当前瞬时值"（重放被冻结）。外部驱动改记 `_external_cells`：audio 层会被
    `_flush_live_output` 清掉，清掉后仍要能认出这些格（`_values` 里留的最后一帧是残影，
    不能让快照回退到它）。
    """
    if external_driver:
        _note_external_cell(row, fid, ch_type)
    else:
        row._marked.setdefault(fid, {})[ch_type] = merged


def _clear_external_cells(row) -> None:
    """清台时同步清掉外部驱动残留记录（清台后没有"被外部写过"的格了）。"""
    cells = getattr(row, "_external_cells", None)
    if cells is not None:
        cells.clear()


def _drop_external_cell(row, fid: str, ct: str) -> bool:
    """清掉该格的外部驱动残留记录（该格已不存在，快照不该再为它做剔除）。"""
    cells = getattr(row, "_external_cells", None)
    if not cells or (str(fid), str(ct)) not in cells:
        return False
    cells.discard((str(fid), str(ct)))
    return True


def _effective_external_residue(row) -> set:
    """`_external_cells` 里**仍算外部残留**的格（用户改过的自动出局）。

    出局条件（任一）：`_marked` 里有它（用户真动过，含手动推回默认值），或
    fader/joystick 这类**非外部层**里有它（推杆/条带/调度台/**MIDI-OSC 遥控**）。
    """
    cells = getattr(row, "_external_cells", None)
    if not cells:
        return set()
    marked = getattr(row, "_marked", None) or {}
    layers = getattr(row, "_layers", None) or {}
    user_layers = [lm for name, lm in layers.items()
                   if name not in _EXTERNAL_DRIVER_LAYERS and isinstance(lm, dict)]
    out = set()
    for fid, ch_type in cells:
        if marked.get(fid, {}).get(ch_type) is not None:
            continue
        if any(lm.get(fid, {}).get(ch_type) is not None for lm in user_layers):
            continue
        out.add((fid, ch_type))
    return out


def _keep_static_cells(row, fid: str, chans: dict, residue: set) -> Dict[str, int]:
    """该设备下**保留**进静态快照的格。

    `residue` 里的格 = 外部驱动写过且用户侧没碰过 → 剔除；若用户侧仍有贡献
    （用户编辑 + 音频同时驱动同一格）→ 保留但值**重算**为排除外部层后的合并值
    （存的是用户的值，不是音频瞬时值）。不在 `residue` 里的格原样保留。
    """
    keep: Dict[str, int] = {}
    for ch_type, val in (chans or {}).items():
        if (str(fid), str(ch_type)) not in residue:
            keep[ch_type] = val
        elif _has_non_external_contributor(row, fid, ch_type):
            keep[ch_type] = int(round(row._compute_merged_value(
                fid, ch_type, exclude_layers=set(_EXTERNAL_DRIVER_LAYERS))))
    return keep


def _static_values_skip_external(row, values: dict) -> dict:
    """从一行的静态值里剔除**外部驱动残留**的 (设备, 通道)。

    ★★ R141（音频值泄漏进 CUE 的兜底半边）：光把 `_marked` 修干净还不够 ——
    `snapshot_static_values()` 在 `_marked` 为空时会**回退到全量合并值**，而
    `_values` 里还留着音频最后一帧（`_flush_live_output` 只清 audio **层**，
    `_values` 是显示缓存，刻意不清）→ "用户没碰任何控件"仍然存下 `{dimmer: 210}`。

    残留判据见 `_effective_external_residue`（真源 = `_external_cells` + `_marked`
    + 非外部层，**不看 `_values`**）。零回归：行内从没有过外部驱动写值
    （`_external_cells` 空）→ **原样返回同一对象**，一行不重算。
    """
    residue = _effective_external_residue(row)
    if not isinstance(values, dict) or not values or not residue:
        return values
    out: Dict[str, Dict[str, int]] = {}
    for fid, chans in values.items():
        keep = _keep_static_cells(row, fid, chans, residue)
        if keep:
            out[fid] = keep
    return out


def _mark_cell_edited(row, fixture_id: str, ch_type: str, value: int) -> None:
    """Reattach one cell after a full release while preserving the UI cache."""
    marked = row._marked.setdefault(fixture_id, {})
    panel = _panel_of(row)
    if not getattr(panel, "_cue_recall_in_progress", False):
        row._detached_cells.discard((str(fixture_id), str(ch_type)))
    marked[ch_type] = value


def _row_marked_values(row, include_detached: bool) -> Dict[str, Dict[str, int]]:
    detached = set() if include_detached else set(getattr(row, "_detached_cells", set()) or ())
    return {
        str(fid): {str(ct): value for ct, value in (channels or {}).items()
                   if (str(fid), str(ct)) not in detached}
        for fid, channels in row._marked.items()
        if channels
    }


def _merge_nondefault_fader_values(row, result: Dict[str, Dict[str, int]],
                                   include_detached: bool) -> None:
    detached = set() if include_detached else set(getattr(row, "_detached_cells", set()) or ())
    for fid, channels in (row._layers.get("fader", {}) or {}).items():
        bucket = result.setdefault(fid, {})
        _merge_fader_channels(row, fid, channels, bucket, detached)
        if not bucket:
            result.pop(fid, None)


def _merge_fader_channels(row, fid, channels, bucket, detached) -> None:
    for ch_type, value in channels.items():
        if ch_type not in row._ch_types or (str(fid), str(ch_type)) in detached:
            continue
        if ch_type in bucket or int(value) == int(row._channel_default_255(fid, ch_type)):
            continue
        bucket[ch_type] = value


def _has_non_external_contributor(row, fid: str, ch_type: str) -> bool:
    """该 (设备, 通道) 是否由用户侧（`_marked` 或非外部层）贡献过值。"""
    marked = getattr(row, "_marked", None) or {}
    if marked.get(fid, {}).get(ch_type) is not None:
        return True          # 用户真编辑过（含"手动推回默认值"）
    layers = getattr(row, "_layers", None) or {}
    for name, layer_map in layers.items():
        if name in _EXTERNAL_DRIVER_LAYERS or not isinstance(layer_map, dict):
            continue
        if layer_map.get(fid, {}).get(ch_type) is not None:
            return True
    return False


#: 切组形状存档的组数上限（超过逐出最早一条；组身份 = 设备集签名）。
_RANGE_STATES_BY_GROUP_MAX = 16


def _stash_range_states(row) -> None:
    """切组**前**把条带形状按当前设备集签名存档。

    ★★ R141（切组丢形状）：右击/形状编辑得到的范围（mode/base/target）**只**存在
    `ChannelTypeList._range_states` 里，而组切换会把它清空。用户画完形状、切一下组
    再存 CUE，存进去的是"平均后的"同一个 flat 值 —— 每台设备的差异丢失（实测
    复现：one_way 128→255 → 切 A→B→A 后变成 flat 191→191）。

    键用 `_fixture_signature`（= 设备 id/名/类型/地址/通道表的元组）：同一组设备
    的签名恒定，来回切能精确取回各自的形状，且**不需要**给 `load_fixtures` 再加
    参数（它只收 `group_name`，拿不到 group_id）。
    """
    states = getattr(row, "_range_states", None) or {}
    sig = getattr(row, "_fixture_signature", None)
    if not states or sig is None:
        return
    store = getattr(row, "_range_states_by_group", None)
    if not isinstance(store, dict):
        store = {}
        try:
            row._range_states_by_group = store
        except Exception:
            return
    if sig not in store and len(store) >= _RANGE_STATES_BY_GROUP_MAX:
        store.pop(next(iter(store)), None)
    store[sig] = {str(ct): dict(st) for ct, st in states.items() if isinstance(st, dict)}


def _restore_range_states_for_group(row) -> None:
    """切组**后**按新设备集签名取回该组上次的形状（没有就保持清空的旧行为）。"""
    sig = getattr(row, "_fixture_signature", None)
    store = getattr(row, "_range_states_by_group", None)
    if sig is None or not isinstance(store, dict):
        return
    saved = store.get(sig)
    if not saved:
        return
    row._range_states = {str(ct): dict(st) for ct, st in saved.items()}


# 通道类别分组
PARAM_GROUPS = [
    ("Intensity",  ["dimmer"]),
    ("Color",      ["R", "G", "B", "W"]),
    ("Motor XYZ",  ["motor_x", "motor_y", "motor_z"]),
    ("Valve",      ["valve"]),
    ("Pump",       ["pump"]),
]

CH_LABELS = {
    "dimmer": "亮度 Dimmer", "R": "红 R", "G": "绿 G", "B": "蓝 B",
    "W": "白 W", "valve": "阀 Valve", "motor_x": "电机 X",
    "motor_y": "电机 Y", "motor_z": "电机 Z",
    "motor_x_fine": "电机 X Fine", "motor_y_fine": "电机 Y Fine",
    "motor_z_fine": "电机 Z Fine",
    "pump": "泵 Pump",
    "placeholder": "占位",
}


def _channel_type_label(ch_type: str) -> str:
    """获取通道类型的显示标签（用于状态反馈）"""
    return CH_LABELS.get(ch_type, ch_type.capitalize())


def _hex_alpha(hex_color: str, alpha_hex: str) -> str:
    try:
        h = hex_color.lstrip('#')
        if len(h) != 6: return hex_color
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(alpha_hex, 16) / 255.0
        return f"rgba({r},{g},{b},{a:.2f})"
    except Exception:
        return hex_color


# ─── 标准件适配助手（D 波·形态级重构，只做呈现适配） ─────────────
def _ns_value(w) -> float:
    """读 NumSlider 浮点值；兼容仍是 SpinBox/QDoubleSpinBox 的控件。"""
    fv = getattr(w, "float_value", None)
    if callable(fv):
        try:
            return float(fv())
        except Exception:
            pass
    return float(w.value())


def _ns_set(w, v):
    """写 NumSlider 浮点值；兼容仍是 SpinBox 的控件（块信号语义由调用方负责）。"""
    sf = getattr(w, "set_float", None)
    if callable(sf):
        sf(float(v))
    else:
        w.setValue(v)


class _KitSection(PanelSection):
    """PanelSection + QGroupBox.setTitle 兼容薄壳（改造点位最小化）。

    只加呈现层方法，不改任何折叠/信号行为。
    """

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(title, expanded, parent)
        # L 波 §11/A1 参数区块化：每个语义参数组套"凹陷卡"——
        # bg_editor 底 + 1px rgba(255,255,255,17) 发丝边 + 4px 圆角 + 内距 8；
        # 卡间距由父级布局统一给 8，禁止两团控件共用无界背景。
        self.setStyleSheet(
            "#flatSection {"
            f" background: {_th.BG_EDITOR};"
            " border: 1px solid rgba(255,255,255,17);"
            f" border-radius: {_th.RADIUS}px;"
            " }")
        self._body_lay.setContentsMargins(8, 4, 8, 8)
        self._body_lay.setSpacing(4)

    def set_title(self, title: str):
        self._header._title = str(title or "")
        self._header.update()

    def title(self) -> str:
        return self._header._title


def _p_label(text: str, accent: bool = False) -> QLabel:
    """属性行小标签：11px text_secondary（D波排版统一）。"""
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color:{_th.FOCUS_BLUE if accent else _th.TEXT_SECONDARY};"
        f"font-size:11px;background:transparent;")
    return lab


# ─── 数据模型 ─────────────────────────────────────────────────────────────────
@dataclass
class CueData:
    """一个 Cue（动作）存储所有设备的通道值快照"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Cue 1"
    color: str = "#2A4A7A"
    values: Dict[str, Dict[str, int]] = field(default_factory=dict)
    effects: Dict[str, Dict[str, dict]] = field(default_factory=dict)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "color": self.color,
            "values": {k: dict(v) for k, v in self.values.items()},
            "effects": {k: dict(v) for k, v in self.effects.items()},
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(id=d.get("id", str(uuid.uuid4())),
                  name=d.get("name", "Cue"),
                  color=d.get("color", "#2A4A7A"))
        obj.values = {k: dict(v) for k, v in d.get("values", {}).items()}
        obj.effects = {k: dict(v) for k, v in d.get("effects", {}).items()}
        return obj


# ══════════════════════════════════════════════════════════════════════════════
#  XY Joystick
# ══════════════════════════════════════════════════════════════════════════════
class XYJoystick(QWidget):
    position_changed = pyqtSignal(float, float)
    position3d_changed = pyqtSignal(float, float, float)
    bind_clicked = pyqtSignal()  # emitted when clicked in binding mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self._mode = "2d"
        self._x = 0.5
        self._y = 0.5
        self._z = 0.5
        self._target_x = 0.5
        self._target_y = 0.5
        self._target_z = 0.5
        self._plane_x = 0.0
        self._plane_y = 0.0
        self._target_plane_x = 0.0
        self._target_plane_y = 0.0
        self._dragging = False
        # Binding mode support
        self._binding_mode = False
        self._binding_hover = False
        self._hover_2d = False
        self.setMouseTracking(True)
        self._spring = False
        self._recording = False
        self._record_path: List[Tuple[float, ...]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._physics_step)
        # ★ R183 3.1：不可见不空转（照 dispatch_panel showEvent/hideEvent 先例）。
        #   原 `__init__` 直接 start 且从不停止 → 面板切走后仍 16ms PreciseTimer
        #   常烧 CPU；物理步进是纯 UI 动画，隐藏期间无观测意义。
        self._timer.start()

    def showEvent(self, event):  # noqa: N802
        """可见 → 恢复物理步进（隐藏期间暂停，见 __init__ 说明）。"""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        """隐藏 → 停 16ms 轮询（重见时 showEvent 恢复）。"""
        self._timer.stop()
        super().hideEvent(event)

    def set_mode(self, mode: str):
        mode = "3d" if str(mode).lower() in {"3d", "xyz", "space"} else "2d"
        if mode == self._mode:
            return
        self._mode = mode
        if mode == "3d":
            self._target_plane_x = self._plane_x
            self._target_plane_y = self._plane_y
            self._update_values_from_plane()
        else:
            self._target_x = self._x
            self._target_y = self._y
        self.update()

    def set_spring(self, enabled: bool):
        self._spring = enabled

    def start_record(self):
        self._recording = True
        self._record_path = []

    def stop_record(self) -> List[Tuple[float, float, float]]:
        self._recording = False
        return list(self._record_path)

    def get_position(self) -> Tuple[float, float]:
        return self._x, self._y

    def get_position3d(self) -> Tuple[float, float, float]:
        return self._x, self._y, self._z

    def set_position(self, x: float, y: float):
        self._x = max(0.0, min(1.0, x))
        self._y = max(0.0, min(1.0, y))
        self._target_x = self._x
        self._target_y = self._y
        self._plane_x = (self._x - 0.5) * 2.0
        self._plane_y = (self._y - 0.5) * 2.0
        self._target_plane_x = self._plane_x
        self._target_plane_y = self._plane_y
        if self._mode == "3d":
            self._update_values_from_plane()
        self.update()

    def set_position3d(self, x: float, y: float, z: float):
        self._x = max(0.0, min(1.0, x))
        self._y = max(0.0, min(1.0, y))
        self._z = max(0.0, min(1.0, z))
        self._target_x = self._x
        self._target_y = self._y
        self._target_z = self._z
        self._plane_x, self._plane_y = self._plane_from_values(self._x, self._y, self._z)
        self._target_plane_x = self._plane_x
        self._target_plane_y = self._plane_y
        self.update()

    def _physics_step(self):
        if self._mode == "3d":
            if self._spring and not self._dragging:
                self._target_plane_x = 0.0
                self._target_plane_y = 0.0
            dx = self._target_plane_x - self._plane_x
            dy = self._target_plane_y - self._plane_y
            if abs(dx) < 0.002 and abs(dy) < 0.002:
                if self._plane_x != self._target_plane_x or self._plane_y != self._target_plane_y:
                    self._plane_x = self._target_plane_x
                    self._plane_y = self._target_plane_y
                    self._update_values_from_plane()
                    self.update()
                return
            damp = 0.18
            self._plane_x += dx * damp
            self._plane_y += dy * damp
            self._update_values_from_plane()
            self.update()
            self.position3d_changed.emit(self._x, self._y, self._z)
            if self._recording:
                self._record_path.append((_time.time(), self._x, self._y, self._z))
            return

        if self._spring and not self._dragging:
            self._target_x = 0.5
            self._target_y = 0.5
        dx = self._target_x - self._x
        dy = self._target_y - self._y
        if abs(dx) < 0.002 and abs(dy) < 0.002:
            if self._x != self._target_x or self._y != self._target_y:
                self._x = self._target_x
                self._y = self._target_y
                self.update()
            return
        damp = 0.18
        new_x = self._x + dx * damp
        new_y = self._y + dy * damp
        self._x = new_x
        self._y = new_y
        self.update()
        self.position_changed.emit(self._x, self._y)
        if self._recording:
            self._record_path.append((_time.time(), self._x, self._y))

    def set_binding_mode(self, enabled: bool):
        """Enable/disable binding mode highlight + click interception."""
        self._binding_mode = enabled
        self._binding_hover = False  # reset hover state on mode change
        # 绑定模式下点击=发起绑定，手型光标提示可点
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def enterEvent(self, event):
        self._hover_2d = True
        if self._binding_mode:
            self._binding_hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover_2d = False
        if self._binding_mode:
            self._binding_hover = False
            self.update()
        else:
            self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if self._binding_mode and event.button() == Qt.MouseButton.LeftButton:
            self.bind_clicked.emit()
            return  # don't start drag in binding mode
        self._dragging = True
        self._update_target(event.position())
        if self._mode == "3d":
            self._plane_x = self._target_plane_x
            self._plane_y = self._target_plane_y
            self._update_values_from_plane()
        else:
            self._x = self._target_x
            self._y = self._target_y
        self.update()
        if self._mode == "3d":
            self.position3d_changed.emit(self._x, self._y, self._z)
        else:
            self.position_changed.emit(self._x, self._y)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_target(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False
        if not self._spring:
            self._target_x = self._x
            self._target_y = self._y

    def _update_target(self, pos: QPointF):
        w, h = self.width(), self.height()
        if self._mode == "3d":
            radius = max(1.0, min(w, h) * 0.42)
            cx, cy = w * 0.5, h * 0.5
            self._target_plane_x = max(-1.0, min(1.0, (float(pos.x()) - cx) / radius))
            self._target_plane_y = max(-1.0, min(1.0, (float(pos.y()) - cy) / radius))
            self._target_x, self._target_y, self._target_z = self._values_from_plane(
                self._target_plane_x, self._target_plane_y)
            return
        self._target_x = max(0.0, min(1.0, pos.x() / w))
        self._target_y = max(0.0, min(1.0, pos.y() / h))

    def _axis_vectors(self):
        raw = {
            "x": QPointF(0.94, 0.34),
            "y": QPointF(-0.82, 0.46),
            "z": QPointF(0.0, -1.0),
        }
        out = {}
        for key, vec in raw.items():
            length = max(1e-6, math.hypot(float(vec.x()), float(vec.y())))
            out[key] = QPointF(float(vec.x()) / length, float(vec.y()) / length)
        return out

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _values_from_plane(self, px: float, py: float) -> Tuple[float, float, float]:
        axes = self._axis_vectors()
        vals = []
        for key in ("x", "y", "z"):
            vec = axes[key]
            amp = max(-1.0, min(1.0, float(px) * float(vec.x()) + float(py) * float(vec.y())))
            vals.append((amp + 1.0) * 0.5)
        return vals[0], vals[1], vals[2]

    def _plane_from_values(self, x: float, y: float, z: float) -> Tuple[float, float]:
        axes = self._axis_vectors()
        amps = {
            "x": (self._clamp01(x) - 0.5) * 2.0,
            "y": (self._clamp01(y) - 0.5) * 2.0,
            "z": (self._clamp01(z) - 0.5) * 2.0,
        }
        sx = sum(float(axes[k].x()) * amps[k] for k in ("x", "y", "z")) / 3.0
        sy = sum(float(axes[k].y()) * amps[k] for k in ("x", "y", "z")) / 3.0
        return max(-1.0, min(1.0, sx)), max(-1.0, min(1.0, sy))

    def _update_values_from_plane(self):
        self._x, self._y, self._z = self._values_from_plane(self._plane_x, self._plane_y)
        self._target_x, self._target_y, self._target_z = self._values_from_plane(
            self._target_plane_x, self._target_plane_y)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(_th.BG_DEEP))
        # Binding mode highlight（绑定绿高亮：语义 hue 保留；纯视觉色值映射到
        # 主题令牌——§11.1 大红大绿净化，hover 亮/常态暗皆走 bright_success 族）
        if self._binding_mode:
            border_color = (QColor(_th.ANIM_GREEN) if self._binding_hover
                            else QColor(_th.BRIGHT_SUCCESS))
            p.setPen(QPen(border_color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 4, 4)
        else:
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 4, 4)
        if self._mode == "3d":
            self._paint_3d(p, w, h)
            p.end()
            return
        cross = QColor(_th.BORDER_NORMAL)
        if self._hover_2d:
            cross = QColor(_th.BORDER_NORMAL)
        p.setPen(QPen(cross, 1, Qt.PenStyle.DotLine))
        p.drawLine(w // 2, 4, w // 2, h - 4)
        p.drawLine(4, h // 2, w - 4, h // 2)
        cx = int(self._x * w)
        cy = int(self._y * h)
        p.setPen(QPen(QColor(FG_ACCENT), 2))
        p.setBrush(QBrush(QColor(113, 168, 255, 68)))
        p.drawEllipse(cx - 10, cy - 10, 20, 20)
        p.end()

    def _paint_3d(self, p: QPainter, w: int, h: int):
        cx, cy = w * 0.5, h * 0.5
        radius = max(1.0, min(w, h) * 0.42)
        axes = self._axis_vectors()
        colors = {"x": QColor("#44AAFF"), "y": QColor("#44FFAA"), "z": QColor("#AA44FF")}
        labels = {"x": "X", "y": "Y", "z": "Z"}

        # 三维模式使用空间直角坐标系投影：原点固定在摇杆中心。
        for key in ("x", "y", "z"):
            vec = axes[key]
            col = colors[key]
            ex = cx + float(vec.x()) * radius
            ey = cy + float(vec.y()) * radius
            neg_x = cx - float(vec.x()) * radius * 0.62
            neg_y = cy - float(vec.y()) * radius * 0.62
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 72), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx, cy), QPointF(neg_x, neg_y))
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 210), 2))
            p.drawLine(QPointF(cx, cy), QPointF(ex, ey))

            tick_step = radius / 3.0
            perp_x = -float(vec.y())
            perp_y = float(vec.x())
            for i in (1, 2, 3):
                tx = cx + float(vec.x()) * tick_step * i
                ty = cy + float(vec.y()) * tick_step * i
                p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 120), 1))
                p.drawLine(QPointF(tx - perp_x * 3.0, ty - perp_y * 3.0),
                           QPointF(tx + perp_x * 3.0, ty + perp_y * 3.0))

            # Arrow head on the positive end of each projected axis.
            ah = 8.0
            back_x = ex - float(vec.x()) * ah
            back_y = ey - float(vec.y()) * ah
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 210), 2))
            p.drawLine(QPointF(ex, ey), QPointF(back_x + perp_x * 4.0, back_y + perp_y * 4.0))
            p.drawLine(QPointF(ex, ey), QPointF(back_x - perp_x * 4.0, back_y - perp_y * 4.0))

            p.setBrush(QBrush(col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(ex, ey), 4.0, 4.0)
            p.setPen(QPen(col, 1))
            # R183 横向性能批：paint 逐帧 QFont 构造 → _th.cached_font 缓存（共享只读）
            p.setFont(_th.cached_font(point_size=9, bold=True,
                                      families=("Consolas",)))
            p.drawText(QRectF(ex - 10, ey - 18, 20, 14),
                       Qt.AlignmentFlag.AlignCenter, labels[key])

        p.setPen(QPen(QColor(_th.TEXT_TERTIARY), 1))
        p.setBrush(QBrush(QColor(_th.BG_DEEP)))
        p.drawEllipse(QPointF(cx, cy), 4.0, 4.0)
        p.setPen(QPen(QColor(_th.TEXT_TERTIARY), 1))
        p.setFont(_th.cached_font(point_size=9, families=("Consolas",)))
        p.drawText(QRectF(cx + 5, cy + 4, 18, 12), Qt.AlignmentFlag.AlignLeft, "O")

        hx = cx + self._plane_x * radius
        hy = cy + self._plane_y * radius
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(cx, cy), QPointF(hx, hy))
        p.setPen(QPen(QColor(FG_ACCENT), 2))
        p.setBrush(QBrush(QColor(113, 168, 255, 72)))
        p.drawEllipse(QPointF(hx, hy), 11.0, 11.0)
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY), 1))
        p.setFont(_th.cached_font(point_size=9, families=("Consolas",)))
        text = f"X {self._x:.2f}  Y {self._y:.2f}  Z {self._z:.2f}"
        p.drawText(QRectF(6, h - 20, w - 12, 16),
                   Qt.AlignmentFlag.AlignCenter, text)



class ProgrammerRangeStrip(QWidget):
    """Programmer 条带式范围控件。

    语义分两层：不按修饰键拖动时平移当前动作的整体范围；按 Ctrl 时
    调整单向开合目标；按 Shift 时调整以中心为锚点的双向开合目标。
    base 表示固定端/中心值，target 表示边缘或末端值。
    """

    range_changed = pyqtSignal(str, str, float, float)
    # ★ 调度台·释放调用层（该控件；跨所有 clip）
    stripReleaseRequested = pyqtSignal(str)

    def __init__(self, ch_type: str, color: str, parent=None):
        super().__init__(parent)
        self._ch_type = ch_type
        self._color = color
        self._mode = "flat"      # flat / one_way / two_way
        self._base = 0
        self._target = 0
        self._dragging = False
        self._press_value = 0
        self._press_base = 0
        self._press_target = 0
        self._press_mode = "flat"
        self._owner_row = None      # 所属 ChannelTypeRow(双击归中/收回用)
        self._hover = False         # 纯呈现：hover 时槽底/文字提亮
        # ★ R183 批次 I 项 16：hover 值气泡的 slot 表（(fixture, slot_idx, total)；
        #   由 `_apply_channel_range` 的注入行写入；空 = 无可映射设备 → 气泡 no-op）。
        self._hover_slots: list = []
        self.setMinimumHeight(32)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip(
            "条带：拖动=平移整体范围；Ctrl+拖动=单向开合；Shift+拖动=双向开合"
            "；滚轮=相对编码（裸轮±1，Shift/Ctrl±10；R183 项 9）")

    def contextMenuEvent(self, ev):  # noqa: N802 (Qt 覆写)
        """右键：释放调用层（该控件）。"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act = menu.addAction(f"释放调用层（{self._ch_type}）")
        chosen = menu.exec(ev.globalPos())
        if chosen is act:
            self.stripReleaseRequested.emit(self._ch_type)
        ev.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """双击条带: 任意类型展开态(Shift/Ctrl 拉出)先收回为 flat(不写值);
        电机类收起态(flat)双击归中 128; 其余类型收起态行为不变(不归中)。

        双击序列的第一次 press 事件会进入 33ms 节流队列, 此处先清掉残留,
        避免收回/归中后被旧值覆盖(竞态)。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return
        owner = getattr(self, "_owner_row", None)
        if owner is None:
            return
        if getattr(owner, "_is_refreshing", False):
            return
        owner._pending_strip = None
        owner._strip_timer.stop()
        if self._mode != "flat":
            # 展开态(Shift/Ctrl 拖出的条带): 双击 = 收回(收起为 flat,
            # 值保持当前 target), 不归中; 电机类第二次双击才归中。
            owner._apply_channel_range(
                self._ch_type, "flat",
                float(self._target), float(self._target))
            return
        if self._ch_type not in MOTOR_CHANNELS:
            # 其余类型收起态(flat): 双击行为不变(不归中)
            return
        # 电机类收起态(flat): 双击 = 归中 128(全部设备同值)
        owner._apply_channel_range(self._ch_type, "flat", LOGICAL_CENTER, LOGICAL_CENTER)

    def set_state(self, mode: str, base: int, target: int):
        self._mode = mode if mode in ("flat", "one_way", "two_way") else "flat"
        self._base = max(0, min(255, int(base)))
        self._target = max(0, min(255, int(target)))
        self.update()

    def state(self):
        return self._mode, self._base, self._target

    def _value_from_x(self, x: float) -> float:
        rect = self.rect().adjusted(12, 8, -12, -8)
        if rect.width() <= 0:
            return 0.0
        t = (float(x) - rect.left()) / float(rect.width())
        return max(0.0, min(255.0, t * 255.0))

    @staticmethod
    def _clamped_delta(a: int, b: int, delta: int) -> int:
        lo = min(a, b)
        hi = max(a, b)
        if lo + delta < 0:
            delta = -lo
        if hi + delta > 255:
            delta = 255 - hi
        return delta

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_event(event)
            return
        # ★ R183 批次 I 项 16：hover 值气泡（x → 扇区 slot → "设备名 DMX(百分比)"）。
        _r183_strip_hover_tooltip(self, event)

    def wheelEvent(self, event):  # noqa: N802 (Qt 覆写)
        """★ R183 批次 I 项 9：相对编码（裸轮 ±1 / Shift·Ctrl ±10），保持条带形状。"""
        steps = event.angleDelta().y() / 120.0
        if abs(steps) < 1e-6:
            return
        delta = _ptool.wheel_delta(event.modifiers()) * (1 if steps > 0 else -1)
        mode, base, target = _ptool.shift_range(self.state(), delta)
        if (mode, base, target) == (self._mode, self._base, self._target):
            event.accept()
            return
        self.set_state(mode, base, target)
        self.range_changed.emit(self._ch_type, mode, float(base), float(target))
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._press_value = self._value_from_x(event.position().x())
        self._press_base = self._base
        self._press_target = self._target
        self._press_mode = self._mode
        self._update_from_event(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_event(event)
        self._dragging = False
        self.update()

    def _update_from_event(self, event: QMouseEvent):
        value = self._value_from_x(event.position().x())
        mods = event.modifiers()
        reanchor = False
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mode = "two_way"
            base = self._press_base
            target = value
            span = abs(target - base)
            left = max(0, min(255, base - span))
            right = max(0, min(255, base + span))
            if left == 0 and right == 255:
                base = int(round((left + right) / 2.0))
                target = left if target < self._press_base else right
                reanchor = True
        elif mods & Qt.KeyboardModifier.ControlModifier:
            mode = "one_way"
            base = self._press_base
            target = value
            reanchor = target in (0, 255)
        else:
            mode = self._press_mode
            delta = value - self._press_value
            base = max(0, min(255, self._press_base + delta))
            target = max(0, min(255, self._press_target + delta))
        self.set_state(mode, base, target)
        if reanchor:
            self._press_value = value
            self._press_base = self._base
            self._press_target = self._target
            self._press_mode = self._mode
        self.range_changed.emit(self._ch_type, mode, float(base), float(target))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(10, 8, -10, -8)
        # 槽底：bg_input + 1px 边（数值滑杆 同款），hover 轻提亮
        groove = QColor(_th.BG_INPUT)
        if self._hover:
            groove = QColor(_th.BG_INPUT)
        painter.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        painter.setBrush(groove)
        painter.drawRoundedRect(rect, 4, 4)

        def x_for(v: int) -> float:
            return rect.left() + rect.width() * (max(0, min(255, int(v))) / 255.0)

        base_x = x_for(self._base)
        target_x = x_for(self._target)
        col = QColor(self._color)
        fill = QColor(col)
        fill.setAlpha(104 if (self._hover or self._dragging) else 92)
        pen = QPen(col, 2)
        painter.setPen(pen)
        painter.setBrush(fill)

        cy = rect.center().y()
        if self._mode == "two_way":
            left_x = x_for(max(0, min(255, self._base - abs(self._target - self._base))))
            right_x = x_for(max(0, min(255, self._base + abs(self._target - self._base))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(left_x, rect.top()+3, right_x-left_x, rect.height()-6), 3, 3)
            painter.setPen(QPen(col, 1.25))
            painter.drawLine(QPointF(base_x, rect.top()), QPointF(base_x, rect.bottom()))
            tag = "SHIFT 双向"
        elif self._mode == "one_way":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(min(base_x, target_x), rect.top()+3, abs(target_x-base_x), rect.height()-6), 3, 3)
            painter.setPen(QPen(col, 1.25))
            painter.setBrush(QColor(_th.BG_INPUT))
            painter.drawEllipse(QPointF(base_x, cy), 3.5, 3.5)
            tag = "CTRL 单向"
        else:
            painter.setPen(QPen(col.lighter(130) if self._hover else col, 1.5))
            painter.setBrush(col)
            painter.drawEllipse(QPointF(target_x, cy), 5.0, 5.0)
            tag = "FLAT 平移"

        # 数值文字：Consolas，带描边阴影保证任意槽底可读
        # R183 横向性能批：QFont 逐帧构造 → _th.cached_font 缓存（共享只读；
        # 字族保持原二元组，不并入 monospace 兜底，避免解析差异）
        painter.setFont(_th.cached_font(point_size=8,
                                        families=("Consolas", "DejaVu Sans Mono")))
        txt = f"{tag}  {self._base}→{self._target}"
        trect = rect.adjusted(6, -2, -6, 2)
        painter.setPen(QPen(QColor(0, 0, 0, 160)))
        painter.drawText(trect.translated(1, 1),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)
        painter.setPen(QPen(QColor(_th.TEXT_PRIMARY if self._hover else _th.TEXT_TERTIARY)))
        painter.drawText(trect,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)

# ══════════════════════════════════════════════════════════════════════════════
#  RangeFaderWidget  —  推杆 + 条带融合控件
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  RangeFaderWidget  —  推杆 + 条带融合控件
# ══════════════════════════════════════════════════════════════════════════════
class RangeFaderWidget(QWidget):
    """推杆与条带可视化融合为一体的控件。

    设计语义：
    - flat 模式（无修饰键）：普通进度条滑块，从左端填充到 target 位置。
    - one_way 模式（Ctrl+拖）：轨道内展开矩形条带，左=base（锚点），右=target。
    - two_way 模式（Shift+拖）：以 base 为中心双向展开矩形条带。
    - 条带矩形内按翼数画分段线，直观显示扇形分布。
    - 无修饰键拖动：flat 直接移动；有范围时整体平移（保持 base-target 差值）。

    外部通过 set_wing_block(wings, block_size) 更新翼/块参数。
    """
    range_changed = pyqtSignal(str, str, float, float)   # ch_type, mode, base, target
    # ★ 调度台·释放调用层（该控件；跨所有 clip）
    stripReleaseRequested = pyqtSignal(str)

    _H        = 28    # 控件总高度
    _PAD_X    = 10    # 左右内边距
    _HANDLE_R = 7     # 手柄半径

    def __init__(self, ch_type: str, color: str, label: str = "", parent=None):
        super().__init__(parent)
        self._ch_type    = ch_type
        self._color      = color
        self._label      = label
        self._mode       = "flat"
        self._base       = 0
        self._target     = 0
        self._wings      = 1
        self._block_size = 1
        # 拖动快照
        self._dragging     = False
        self._press_x      = 0.0
        self._press_base   = 0
        self._press_target = 0
        self._press_mode   = "flat"
        self._hover        = False
        # ★ R183 批次 I 项 16：条带 hover 值气泡的 slot 表（(fixture, slot_idx, total)，
        #   由 ChannelTypeRow 在写入条带状态时同步；空 = 无可映射设备 → 气泡 no-op）。
        self._hover_slots: list = []
        self.setMinimumSize(120, self._H)
        self.setFixedHeight(self._H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip(
            f"{label}  拖动=平移  Ctrl+拖=单边开合  Shift+拖=双边开合  双击=归位\n"
            "滚轮=相对编码（裸轮±1，Shift/Ctrl±10；R183 项 9）")

    def contextMenuEvent(self, ev):  # noqa: N802 (Qt 覆写)
        """右键：释放调用层（该控件）。"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act = menu.addAction(f"释放调用层（{self._ch_type}）")
        chosen = menu.exec(ev.globalPos())
        if chosen is act:
            self.stripReleaseRequested.emit(self._ch_type)
        ev.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    # ── 公共 API ──────────────────────────────────────────────────────────
    def set_state(self, mode: str, base: int, target: int):
        self._mode   = mode if mode in ("flat", "one_way", "two_way") else "flat"
        self._base   = max(0, min(255, int(base)))
        self._target = max(0, min(255, int(target)))
        self.update()

    def set_wing_block(self, wings: int, block_size: int):
        self._wings      = max(1, int(wings))
        self._block_size = max(1, int(block_size))
        self.update()

    def state(self):
        return self._mode, self._base, self._target

    # ── 坐标转换 ──────────────────────────────────────────────────────────
    def _track_rect(self):
        r = self.rect()
        return r.adjusted(self._PAD_X, 4, -self._PAD_X, -4)

    def _x_for(self, v: int) -> float:
        r = self._track_rect()
        return r.left() + r.width() * max(0.0, min(1.0, v / 255.0))

    def _v_from_x(self, x: float) -> float:
        r = self._track_rect()
        if r.width() <= 0:
            return 0.0
        t = (x - r.left()) / r.width()
        return max(0.0, min(255.0, t * 255.0))

    @staticmethod
    def _clamped_delta(a: int, b: int, delta: int) -> int:
        lo, hi = min(a, b), max(a, b)
        if lo + delta < 0:   delta = -lo
        if hi + delta > 255: delta = 255 - hi
        return delta

    # ── 鼠标事件 ──────────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging     = True
        self._press_x      = event.position().x()
        self._press_base   = self._base
        self._press_target = self._target
        self._press_mode   = self._mode
        self._update_from_event(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_event(event)
            return
        # ★ R183 批次 I 项 16：hover 值气泡（x → 扇区 slot → "设备名 DMX(百分比)"）。
        _r183_strip_hover_tooltip(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_event(event)
        self._dragging = False
        self.update()

    def wheelEvent(self, event):  # noqa: N802 (Qt 覆写)
        """★ R183 批次 I 项 9：相对编码（gMA3 编码器语义）。

        裸轮 ±1、Shift ±10、Ctrl ±10；经既有 `range_changed` 通道走
        `_apply_channel_range` 平移通路 —— 条带形状（mode/base/target 差值）
        逐字节保持，只是整体平移（0..255 钳制）。
        """
        steps = event.angleDelta().y() / 120.0
        if abs(steps) < 1e-6:
            return
        delta = _ptool.wheel_delta(event.modifiers()) * (1 if steps > 0 else -1)
        mode, base, target = _ptool.shift_range(self.state(), delta)
        if (mode, base, target) == (self._mode, self._base, self._target):
            event.accept()
            return
        self.set_state(mode, base, target)
        self.range_changed.emit(self._ch_type, mode, float(base), float(target))
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode != "flat":
            self._mode = "flat"
            self._base = self._target
            self.update()
            self.range_changed.emit(self._ch_type, "flat", float(self._target), float(self._target))

    def _update_from_event(self, event: QMouseEvent):
        cur_v = self._v_from_x(event.position().x())
        mods = event.modifiers()
        reanchor = False
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mode = "two_way"
            base = self._press_base
            target = cur_v
            span = abs(target - base)
            left = max(0, min(255, base - span))
            right = max(0, min(255, base + span))
            if left == 0 and right == 255:
                base = int(round((left + right) / 2.0))
                target = left if target < self._press_base else right
                reanchor = True
        elif mods & Qt.KeyboardModifier.ControlModifier:
            mode = "one_way"
            base = self._press_base
            target = cur_v
            reanchor = target in (0, 255)
        else:
            press_v = self._v_from_x(self._press_x)
            if self._press_mode == "flat":
                mode = "flat"
                base = cur_v
                target = cur_v
            else:
                delta = cur_v - press_v
                mode = self._press_mode
                base = max(0, min(255, self._press_base + delta))
                target = max(0, min(255, self._press_target + delta))
        self.set_state(mode, base, target)
        if reanchor:
            self._press_x = event.position().x()
            self._press_base = self._base
            self._press_target = self._target
            self._press_mode = self._mode
        self.range_changed.emit(self._ch_type, mode, float(base), float(target))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col  = QColor(self._color)
        fill = QColor(col)
        if self._dragging:
            fill.setAlpha(210)
        elif self._hover:
            fill.setAlpha(110)
        else:
            fill.setAlpha(60)

        r  = self._track_rect()
        cy = r.top() + r.height() / 2.0
        tx = self._x_for(self._target)
        bx = self._x_for(self._base)

        # ── 轨道背景：bg_input 槽 + 1px 描边（数值滑杆 同款）
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.setBrush(QColor(_th.BG_BUTTON_HOVER if (self._hover and not self._dragging) else _th.BG_INPUT))
        p.drawRoundedRect(r, 4, 4)

        is_range = (self._mode != "flat") and (self._base != self._target)

        if self._mode == "two_way" and is_range:
            # 双边：以 base 为中心，两侧对称展开矩形
            span = abs(self._target - self._base)
            lx = self._x_for(max(0,   self._base - span))
            rx = self._x_for(min(255, self._base + span))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(lx, r.top()+2, rx-lx, r.height()-4), 3, 3)
            # 翼分段线
            wings = max(1, self._wings)
            if wings > 1 and (rx - lx) > 0:
                seg_w = (rx - lx) / wings
                seg_pen = QPen(QColor(col.red(), col.green(), col.blue(), 130), 1)
                p.setPen(seg_pen)
                for i in range(1, wings):
                    sx_line = lx + i * seg_w
                    p.drawLine(QPointF(sx_line, r.top()+3), QPointF(sx_line, r.bottom()-3))
            # 中心锚线
            p.setPen(QPen(col, 1.5))
            p.drawLine(QPointF(bx, r.top()+2), QPointF(bx, r.bottom()-2))

        elif self._mode == "one_way" and is_range:
            # 单边：base 到 target 展开矩形
            lx = min(bx, tx); rx = max(bx, tx)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(lx, r.top()+2, rx-lx, r.height()-4), 3, 3)
            # 翼分段线
            wings = max(1, self._wings)
            if wings > 1 and (rx - lx) > 0:
                seg_w = (rx - lx) / wings
                seg_pen = QPen(QColor(col.red(), col.green(), col.blue(), 130), 1)
                p.setPen(seg_pen)
                for i in range(1, wings):
                    sx_line = lx + i * seg_w
                    p.drawLine(QPointF(sx_line, r.top()+3), QPointF(sx_line, r.bottom()-3))
            # base 锚点小圆
            p.setPen(QPen(col, 1.5))
            p.setBrush(QColor(_th.BG_INPUT))
            p.drawEllipse(QPointF(bx, cy), 3.5, 3.5)

        else:
            # flat 模式：普通进度条，从左端填充到 target
            if tx > r.left():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(
                    QRectF(r.left(), r.top()+2, tx - r.left(), r.height()-4), 3, 3)

        # ── 手柄（target 位置）：hover/拖动提亮 + 1px 白描边提升对比
        if self._dragging:
            hcol = col.lighter(180)
        elif self._hover:
            hcol = col.lighter(140)
        else:
            hcol = col
        p.setPen(QPen(hcol, 2))
        p.setBrush(QColor(_th.BG_PANEL))
        p.drawEllipse(QPointF(tx, cy), self._HANDLE_R, self._HANDLE_R)
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.setBrush(col)
        p.drawEllipse(QPointF(tx, cy), 3.0, 3.0)

        # ── 标签文字（叠加在轨道上；描边阴影保证亮填区可读）
        text = _r183_strip_label_text(self, is_range)   # R183 项 13：双读数
        # R183 横向性能批：QFont 逐帧构造 → _th.cached_font 缓存（共享只读）
        p.setFont(_th.cached_font(point_size=8,
                                  families=("Consolas", "DejaVu Sans Mono")))
        trect = r.adjusted(self._HANDLE_R + 4, 0, -4, 0)
        frac = tx / max(1.0, (r.left() + r.width()))
        bright_fill = (fill.alpha() >= 140 and frac > 0.35)
        p.setPen(QPen(QColor(0, 0, 0, 160)))
        p.drawText(trect.translated(1, 1),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)
        p.setPen(QPen(QColor(_th.TEXT_SELECTED_FG if bright_fill else _th.TEXT_PRIMARY)))
        p.drawText(trect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)
        p.end()
# ══════════════════════════════════════════════════════════════════════════════
#  ChannelTypeRow
# ══════════════════════════════════════════════════════════════════════════════
def _clean_range_state(st) -> dict:
    """规整单条条带形状（mode/base/target；坏数据安全）。"""
    return {"mode": str(st.get("mode", "flat") or "flat"),
            "base": int(st.get("base", 0) or 0),
            "target": int(st.get("target", 0) or 0)}


def _range_state_key(def_id: str, ch_type: str) -> str:
    """条带形状的存储键：`"def_id|ch_type"`。

    ★ 2026-09-14 深查修正（数据丢失）：原实现只按 `ch_type` 作键，而 `_range_states`
      是**每行**状态；同一选择里两个设备类型都有同名通道（如都有 pump）时，后写的一行
      会**静默覆盖**前一行的条带（实测 d1 的 10-20 one_way 被 d2 的 200-255 two_way
      覆盖）。行身份 = (def_id, ch_type)，键必须与之同构。
    """
    return f"{str(def_id or '')}|{str(ch_type or '')}"


def _range_state_for(range_states, def_id, ch_type):
    """取"该行（def_id）该通道"的条带形状；兼容旧格式（仅按 ch_type）。

    旧格式（本修正前保存的工程/场景）没有 def_id 维度：单行时无歧义，
    多行同名通道时只能给所有同名行同一个形状（保持旧行为，不误删数据）。
    """
    if not range_states:
        return None
    got = range_states.get(_range_state_key(def_id, ch_type))
    if isinstance(got, dict):
        return got
    legacy = range_states.get(str(ch_type or ""))
    return legacy if isinstance(legacy, dict) else None


def _iter_range_states(range_states, ch_types, def_id):
    """遍历该行在 `range_states` 里命中的 (ch_type, 形状)（新旧格式都支持）。"""
    for ct in (ch_types or ()):
        st = _range_state_for(range_states, def_id, ct)
        if isinstance(st, dict):
            yield str(ct), st


def _restore_row_range_shapes(row, per_ch_vals, range_states) -> None:
    """把一个 Row 的条带/推杆控件按 `range_states` 还原（`restore_static_snapshot` 尾段）。

    · 有保存的形状 → **按原形状还原**（mode/base/target）；
    · 无形状（旧快照）→ 回退均值 flat（旧行为，向后兼容）。
    ★ 纯播放（`touch_shape=False`）由调用方**整段跳过** —— 那是"不碰控件"的语义，
      绝不能让本函数的"无形状兜底"把用户拉好的 one_way 条带压平。
    """
    for ct, vals in (per_ch_vals or {}).items():
        if not vals:
            continue
        mean = int(round(sum(vals) / len(vals)))
        saved = _range_state_for(range_states, getattr(row, "_def_id", ""), ct)
        try:
            if saved:
                mode = str(saved.get("mode", "flat") or "flat")
                base = int(saved.get("base", 0))
                target = int(saved.get("target", base))
                row._set_range_state(ct, mode, base, target)
                rep = int(round((base + target) / 2)) if mode != "flat" else base
            else:
                row._set_range_state(ct, "flat", mean, mean)
                rep = mean
        except Exception:
            row._set_range_state(ct, "flat", mean, mean)
            rep = mean
        _set_row_master_slider(row, ct, rep)


def _set_row_master_slider(row, ct: str, rep: int) -> None:
    """同步该行的主推杆/数值标签（仅单通道行才有；控件缺失时静默跳过）。"""
    slider = getattr(row, "_master_slider", None)
    if slider is None:
        return
    if list(getattr(row, "_ch_types", []) or []) != [ct]:
        return
    slider.blockSignals(True)
    try:
        slider.setValue(rep)
    finally:
        slider.blockSignals(False)
    label = getattr(row, "_val_label", None)
    if label is not None:
        label.setText(str(rep))


def _lookup_channel_default(panel, fid: str, ch_type: str) -> int:
    """设备通道的默认值（0-255）；查不到回落"电机 128 / 其余 0"（★ R138）。

    数据源 = fixture def 的 `function_channel_defaults`（patch 侧按通道语义填：
    motor_* 归中、其余按配置）。
    """
    raw = _raw_channel_default(panel, fid, ch_type)
    if raw is not None:
        return max(0, min(255, int(raw)))
    return 128 if ch_type.startswith("motor") else 0


def _raw_channel_default(panel, fid: str, ch_type: str):
    """`function_channel_defaults` 里的原始默认值；查不到 → None（含 motor_fine 回退）。"""
    fx = _panel_fixture_by_id(panel, fid)
    if fx is None:
        return None
    fcd = _function_channel_defaults(panel, fx)
    raw = fcd.get(ch_type)
    if raw is None and ch_type in MOTOR_FINE_TO_COARSE:
        raw = fcd.get(MOTOR_FINE_TO_COARSE[ch_type])
    return raw


def _panel_fixture_by_id(panel, fid: str):
    """面板内的设备实例（按 id 查；缓存与 `_traj_fx_by_id` 同生命周期）。"""
    try:
        cache = getattr(panel, "_traj_fx_by_id", None)
        if cache is None:
            cache = {str(getattr(f, "id", "")): f
                     for f in (getattr(panel, "_current_fixtures", None) or [])}
            panel._traj_fx_by_id = cache
        return cache.get(fid)
    except Exception:
        return None


def _function_channel_defaults(panel, fx) -> dict:
    """该设备定义里的 `function_channel_defaults`（取不到 → 空 dict）。"""
    try:
        defs = getattr(panel, "_fixture_defs_cache", {}) or {}
        fd = defs.get(getattr(fx, "def_id", None))
        return (getattr(fd, "function_channel_defaults", None) or {}) if fd else {}
    except Exception:
        return {}


def _scope_static_values(vals, scope_ids):
    """只保留**作用域设备**内的静态值条目（`scope_ids` 为空 → 空表）。

    ★★ 2026-09-17 用户实报（存储区场景范围污染）：面板行是**跨组容器**
      （切组不清 `_values/_marked/_layers`，见 `load_fixtures` 注释"切组不丢值"），
      快照天然会带上"早先选过、现已从选择组去掉"的设备条目；而场景节点只记
      当前选择（`node["fixture_ids"]`）。抓取与写回**两侧**都必须按作用域过滤，
      隐藏行的旧 `_fixtures` 才不会被写出去。

    注意语义：**空作用域 = 无设备在范围内 → 空表**（无选择时不该抓到任何条目）。
      还原侧"作用域未知"（旧档无设备集）不应调用本函数，否则会误删旧数据。
    """
    allowed = {str(x) for x in (scope_ids or []) if str(x)}
    return {str(k): v for k, v in (vals or {}).items() if str(k) in allowed}


def _def_ids_for(panel, scope_ids):
    """作用域设备 id → 其设备类型 id 集合（`range_states` 按 def_id 过滤用）。"""
    want = {str(x) for x in (scope_ids or []) if str(x)}
    out = set()
    try:
        for f in (getattr(panel, "_current_fixtures", None) or []):
            if str(getattr(f, "id", "")) in want:
                out.add(str(getattr(f, "def_id", "") or ""))
    except Exception:
        return set()
    out.discard("")
    return out


def _scope_range_states(rs, def_ids):
    """只保留作用域设备类型的条带形状（键 = `def_id|ch_type`）。

    作用域为空（旧档无设备集）→ 原样返回，交由 `_range_state_for` 的行级
    def 匹配与旧格式兼容处理，避免误删数据。
    """
    rst = rs if isinstance(rs, dict) else {}
    keep = {str(x) for x in (def_ids or []) if str(x)}
    if not keep:
        return dict(rst)
    out = {}
    for key in rst:
        skey = str(key)
        did = skey.split("|", 1)[0]
        # 旧格式键（仅 ch_type、无 def_id 维度）没有归属信息 → 保留
        if did in keep or "|" not in skey:
            out[key] = rst[key]
    return out


def _recall_scope_ids(ids):
    """还原作用域 = **存档自报设备集**（`node["fixture_ids"]` / CUE 的 source_group_ids）。

    ★ 刻意**不回落当前 live 选择**：旧档设备集未知时按"不过滤"处理（保持旧行为），
      用调用方此刻的选择去猜作用域只会误删旧数据（如工程加载时残留的选择）。
    """
    return [str(x) for x in (ids or []) if str(x)]


def _dispatch_scope_ids(panel) -> list:
    """调度台**编辑作用域** = 大组基准序 ∪ 当前子集选择。

    ★★ 2026-09-19 用户实报（"调度台存不了一个大组里面的子集的不同的通道值"）::

        "例如二二交叉，每两个的x轴方向不同，存下来只能存到最后选择到的子设备
         的值，我是选2个，从第一个，间隔2个选了一次做了一个角度，
         然后从第三个开始做一个角度。然后发现存不下来第一次选中的角度值了。"

      真因：捕获只按**当前 live 选择**过滤（R87 防污染）—— 用户做第一批子集、
      再切到第二批子集，捕获时 live 选择只剩第二批 → 第一批的值被整批丢掉。

    语义边界（R87 防污染**不回归**）：
      · `_current_base_order_ids` = 该**大组**的完整基准序（来自 TopView / 设备组的
        base_order；同一大组内多次「间隔选择」共享同一个 base，**跨大组切换才变**）
        → 同组内多个子集都是"用户刚在这个组里做的编辑"，必须一起存；
      · 不属于当前大组的陈旧条目（上一组切走后遗留的隐藏行）不在 base 里 → 仍被过滤。
      · base 缺失（旧调用/未提供）→ 回落当前 live 选择 = 原行为，零回归。
    """
    base: list = []
    try:
        base = [str(x) for x in (getattr(panel, "_current_base_order_ids", []) or []) if str(x)]
    except Exception:
        base = []
    live: list = []
    try:
        live = [str(x) for x in (panel.get_live_selected_fixture_ids() or []) if str(x)]
    except Exception:
        live = []
    out, seen = [], set()
    for fid in base + live:
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


class ChannelTypeRow(QWidget):
    value_changed = pyqtSignal(str, int)
    fixture_value_changed = pyqtSignal(str, str, int)
    fixture_layer_value_changed = pyqtSignal(str, str, int, str)
    fixture_layer_batch_changed = pyqtSignal(dict, str)
    joy_amplitude_changed = pyqtSignal(dict)  # ★ 摇杆幅度控制信号 {param_key: value}
    # ★ 2026-09-17：本类内部会 emit 轨迹事件，而该信号原先只在 ProgrammerPanel
    #   上 → AttributeError 被 except 吞掉（轨迹录制静默丢失）。声明同名信号并逐级转发。
    controlTrajectoryEvent = pyqtSignal(str, object)
    # ★ 同步组 Z 命令信号（2026-08-04 用户需求）：(统一世界扇面角或 None, 行内设备 id 列表)。
    #   motor_z 手动值 ≠ 中心 → 广播统一角到全部同步设备；回到中心 → None 清除命令。
    sync_z_command_changed = pyqtSignal(object, list)
    # ★ R173/R174: 状态栏反馈信号，本类内部会 emit（line 3537），而该信号原先只在
    #   ProgrammerPanel 上 → AttributeError。声明同名信号并逐级转发（同 controlTrajectoryEvent 模式）。
    status_feedback = pyqtSignal(str, int)

    def __init__(self, group_name: str, ch_types: List[str], parent=None):
        super().__init__(parent)
        self._group_name = group_name
        self._ch_types = ch_types
        # ★ 串台修复：行所属设备类型容器 def_id（类型分区后同一 ch_type 有多行，
        #   摇杆绑定按此确定性解析目标行；空=未分区/旧路径）。
        self._def_id = ""
        self._expanded = False
        self._fixtures = []
        self._fixture_defs = {}
        self._fixture_id_set: set = set()
        # ★ 性能：签名缓存。同一类型容器所有行接收的是同一个 dfixtures 列表对象，
        #   按 (列表对象id, defs字典对象id) 记忆化，签名内容与原逐台构建完全一致。
        self._sig_cache: Dict[tuple, tuple] = {}
        self._values: Dict[str, Dict[str, int]] = {}
        self._marked: Dict[str, Dict[str, int]] = {}
        # Retained visual values that are detached from future captures.
        self._detached_cells: set[tuple[str, str]] = set()
        _LAYERS = ["fader", "joystick", "audio", "matrix"]
        self._layers: Dict[str, Dict[str, Dict[str, int]]] = {k: {} for k in _LAYERS}
        self._layer_modes: Dict[str, str] = {
            k: ("add" if k == "joystick" else "override") for k in _LAYERS
        }
        self._fader_curve: str = "linear"
        self._fader_lo: int = 0
        self._fader_hi: int = 255
        self._fader_gamma: float = 1.0
        self._fader_steps: int = 4
        self._joy_curve: str = "linear"
        self._joy_lo: int = 0
        self._joy_hi: int = 255
        self._joy_gamma: float = 1.0
        self._joy_steps: int = 4
        self._joy_coord_mode: str = "cartesian"
        self._joy_space_mode: str = "2d"
        self._joy_mode = "additive"
        self._joy_amp_x_target = "joy_amplitude_x"
        self._joy_amp_y_target = "joy_amplitude_y"
        self._joy_amp_z_target = "joy_amplitude_z"
        self._is_refreshing: bool = False
        # ★ 性能：主控件（滑块/摇杆）事件 33ms 合并节流。
        #   pending 按 (ch_type, layer) 分槽——不同通道的控件(如 dimmer 滑块 +
        #   motor_x 摇杆)互不覆盖(审查发现3)。
        self._last_master_t: float = 0.0
        self._pending_master: Dict[tuple, tuple] = {}
        self._master_timer = QTimer(self)
        self._master_timer.setSingleShot(True)
        self._master_timer.setInterval(33)
        self._master_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._master_timer.timeout.connect(self._process_pending_master)
        # ★ 性能：条带拖动事件 33ms 合并节流
        self._last_strip_t: float = 0.0
        self._pending_strip: Optional[tuple] = None
        self._strip_timer = QTimer(self)
        self._strip_timer.setSingleShot(True)
        self._strip_timer.setInterval(33)
        self._strip_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._strip_timer.timeout.connect(self._process_pending_strip)
        self._audio_active: bool = False
        # Programmer 数值控制的空间分布参数。wings/block_size 即使为 1，
        # 也允许 Ctrl/Shift 临时进入范围拉伸模式，便于直接做扇形、渐变展开。
        self._value_wings: int = 1
        self._value_block_size: int = 1
        self._value_wing_direction: str = WING_DIRECTION_LOW_TO_HIGH
        self._value_wing_mirror = False
        self._value_wing_reverse = False
        self._audio_widget_ref = None
        self._spec_analyzer_ref = None
        self._owner_list = None
        self._last_spectrum_output_t = 0.0
        self._last_audio_preview_t = 0.0
        self._spectrum_env_by_slot: Dict[tuple, float] = {}
        self._spectrum_sorted_cache: Dict[tuple, List[Any]] = {}
        self._ctrl_sliders: Dict[str, QSlider] = {}
        # ★ 功能模式表：{ct: [QPushButton, ...]}（方块按钮，点击=快速写入对应 DMX 值）
        self._mode_buttons: Dict[str, List[QPushButton]] = {}
        self._modes_by_ch: Dict[str, List[dict]] = {}
        # ★ 包排列（纯展示整理）：包内子设备按文件夹嵌套显示，可点击折叠
        self._pkg_arrange: bool = False
        self._pkg_of: Dict[str, dict] = {}      # member_fid → {"id","name"}
        self._pkg_collapsed: set = set()
        self._range_states: Dict[str, Dict[str, Any]] = {}
        self._range_strips: Dict[str, ProgrammerRangeStrip] = {}
        self._range_value_labels: Dict[str, QLabel] = {}
        self._wing_block_labels: Dict[str, QLabel] = {}
        self._wing_block_spins: List[QSpinBox] = []
        self._wing_direction_buttons: List[QPushButton] = []
        self._responsive_secondary_widgets: List[QWidget] = []
        # ★ 控制器面板各Tab中的条带控件（每个通道类型独立一条）
        self._ctrl_fader_strips: Dict[str, ProgrammerRangeStrip] = {}
        self._ctrl_vslider_strips: Dict[str, ProgrammerRangeStrip] = {}
        self._ctrl_joy_strips: Dict[str, ProgrammerRangeStrip] = {}
        self._body_cell_widgets: Dict[Tuple[str, str], QWidget] = {}
        self._ordered_fixture_cache: Dict[str, List[Any]] = {}
        self._body_needs_rebuild: bool = True
        self._fixture_signature: Tuple = ()
        self._build_ui()

    def _release_strip_layers(self, ch_type: str) -> None:
        """右键条带 → 释放该控件键的调用层（strip 与 range 两键都释放）。

        ★ 2026-09-13 修（用户明确要求分清"值0"与"真清空"）：
          原实现**只写释放注册表**（供轨迹回放跳过），**完全不碰 NetworkManager
          层**。后果：手工设置的静态值 release 后 `active` 掩码仍为 True、值仍在
          → 监视器（只读混合值）看不出任何变化，用户以为"释放无效"。
          现同时调 `clear_layer_channels()` 置 `active=False` —— 这才是"真清空"
          的语义（区别于"人为设成 0" = 有效果且值为 0）。
        """
        cleared = 0
        try:
            from ..processing.dispatch import get_release_registry
            reg = get_release_registry()
            n1 = reg.release_control(f"programmer|strip|{ch_type}")
            n2 = reg.release_control(f"programmer|range|{ch_type}")
            n3 = reg.release_control(f"programmer|master|{ch_type}")
            cleared = self._clear_nm_layer_channels(ch_type)
            w = self.window()
            st = getattr(w, "_status", None)
            if st is not None and hasattr(st, "set_message"):
                st.set_message(f"已释放 {ch_type} 条带调用层（注册 {n1 + n2 + n3} 项，"
                               f"清空 {cleared} 个通道）")
        except Exception:
            pass

    def _clear_nm_layer_channels(self, ch_type: str, layer: str = "fader") -> int:
        """把该 ch_type 在指定层的所有设备通道**释放**（active=False）。

        与 `_mark_layer_sparse_from_fixture_values` 的地址换算同源（复用
        `_addr_cache`），但写的是"清除"而非"赋值"。返回清除的通道数。

        ★★ 2026-09-17 深查修正（右键"释放调用层"是空操作）：`_nm_ref` /
          `_addr_cache` / `_layer_sparse_cache` 都只在 **ProgrammerPanel** 上，
          `ChannelTypeRow` 没有 —— 本方法定义在行类上且 `self` 恒为行对象，
          旧实现的 `getattr(self, "_nm_ref", None)` 永远是 None → 直接 `return 0`，
          用户右键条带释放后值仍留在 NM 层（"释放无效"）。现在先回到面板。
        """
        panel = _panel_of(self) or self
        nm = getattr(panel, "_nm_ref", None)
        if nm is None or not hasattr(nm, "clear_layer_channels"):
            return 0
        by_uni = _addrs_by_universe(panel, ch_type)
        total = 0
        for uni, addr_set in by_uni.items():
            try:
                nm.clear_layer_channels(layer, uni, sorted(addr_set))
                total += len(addr_set)
            except Exception:
                continue
        # 同步清本面板该层稀疏缓存，防 keepalive 把旧值再发回去。
        # ★ 结构是 {layer: {universe: {addr: value}}}（地址键），旧实现按
        #   `chs.pop(ch_type, None)` 在**地址字典**上按通道名 pop → 永远打不中。
        #   正确的做法是按 `_addr_cache` 反查该 ch_type 的全部 (uni, addr) 再删。
        flat = [(u, a) for u, s_ in by_uni.items() for a in s_]
        _drop_addrs_from_layer_cache(panel, layer, flat)
        try:
            panel._layer_sparse_version += 1
        except Exception:
            pass
        return total

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        _indicator_color = (_ch_color(self._ch_types[0]) if self._ch_types
                            else _th.CATEGORY_COLORS["fx"])

        self._header = QWidget()
        self._header.setFixedHeight(38)
        scope_qss(self._header,
            f"background:{BG_HEADER};"
            f"border-bottom:1px solid {_th.BORDER_NORMAL};"
            f"border-left:4px solid {_indicator_color};")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = lambda e: self._toggle()
        hlay = QHBoxLayout(self._header)
        hlay.setContentsMargins(10, 0, 8, 0)
        hlay.setSpacing(8)

        self._arrow = QLabel()
        self._arrow.setFixedWidth(14)
        self._set_expand_arrow(False, _indicator_color)
        hlay.addWidget(self._arrow)

        self._title = QLabel(self._group_name)
        self._title.setStyleSheet(
            f"color:#FFFFFF;font-size:12px;font-weight:bold;letter-spacing:0.5px;")
        self._title.setFixedWidth(90)
        hlay.addWidget(self._title)

        for ct in self._ch_types:
            c = _ch_color(ct)
            lbl = QLabel(ct.upper()[:4])
            lbl.setStyleSheet(
                f"color:{c};font-size:10px;font-weight:bold;"
                f"background:{_hex_alpha(c, '2A')};border:1px solid {_hex_alpha(c, '55')};"
                f"border-radius:3px;padding:1px 4px;")
            lbl.setFixedWidth(36)
            lbl.setToolTip(_ch_label(ct))
            hlay.addWidget(lbl)

        # ── 摇杆绑定指示器 ──────────────────────────────────────────────
        self._binding_indicators: Dict[str, QLabel] = {}
        for ct in self._ch_types:
            ind = QLabel("")
            ind.setFixedSize(18, 18)
            ind.setToolTip("已绑定到外接摇杆 — 左键调整映射，右键管理")
            ind.setVisible(False)
            ind.setCursor(Qt.CursorShape.PointingHandCursor)
            ind.mousePressEvent = lambda e, c=ct: self._on_binding_indicator_click(c, e)
            hlay.addWidget(ind)
            self._binding_indicators[ct] = ind

        self._header_wing_label = self._mk_small_label("翼")
        self._header_wing_spin = QSpinBox()
        self._header_wing_spin.setRange(1, 32)
        self._header_wing_spin.setValue(self._value_wings)
        self._header_wing_spin.setFixedWidth(52)
        self._header_wing_spin.setToolTip("Programmer 翼：把当前通道设备序列分成多个翼后再由条带/Ctrl/Shift 写入")
        # 数字框通用外观由全局 QSS 提供；此处仅保留翼=蓝辨识色
        self._header_wing_spin.setStyleSheet(f"QSpinBox{{color:{FG_ACCENT};}}")
        self._header_wing_spin.valueChanged.connect(self._on_value_wings_changed)
        hlay.addWidget(self._header_wing_label)
        hlay.addWidget(self._header_wing_spin)
        self._responsive_secondary_widgets.extend([self._header_wing_label, self._header_wing_spin])

        self._header_block_label = self._mk_small_label("块")
        self._header_block_spin = QSpinBox()
        self._header_block_spin.setRange(1, 64)
        self._header_block_spin.setValue(self._value_block_size)
        self._header_block_spin.setFixedWidth(52)
        self._header_block_spin.setToolTip("Programmer 块：每 N 台作为一个整体，共用同一个条带插值位置")
        # 数字框通用外观由全局 QSS 提供；此处仅保留块=青辨识色
        self._header_block_spin.setStyleSheet(f"QSpinBox{{color:{FG_CYAN};}}")
        self._header_block_spin.valueChanged.connect(self._on_value_block_changed)
        hlay.addWidget(self._header_block_label)
        hlay.addWidget(self._header_block_spin)
        self._responsive_secondary_widgets.extend([self._header_block_label, self._header_block_spin])
        self._wing_block_spins.extend([self._header_wing_spin, self._header_block_spin])
        self._header_wing_dir_widget = self._make_wing_direction_toolbar()
        hlay.addWidget(self._header_wing_dir_widget)
        self._responsive_secondary_widgets.append(self._header_wing_dir_widget)


        _r183_add_row_align_controls(self, hlay)   # R183 项 6/10：排序 + 抖动% + 行头右键
        hlay.addStretch()

        if len(self._ch_types) == 1:
            ct = self._ch_types[0]
            c = _ch_color(ct)
            self._master_slider = QSlider(Qt.Orientation.Horizontal)
            self._master_slider.setRange(0, 255)
            self._master_slider.setValue(0)
            self._master_slider.setFixedWidth(120)
            self._master_slider.setToolTip(f"{_ch_label(ct)} 主推杆（Shift=双边开合 Ctrl=单边开合）")
            # 审计 C + D 波精修：单条 QSS、按压态伪态、手柄尺寸恒定不跳变；
            # 槽底用 bg_input，填充/手柄保留通道类别色（§4A 保护语义）。
            _ss_slider = (
                f"QSlider::groove:horizontal{{background:{_th.BG_INPUT};height:6px;"
                f"border-radius:3px;}}"
                f"QSlider::handle:horizontal{{background:{c};width:14px;"
                f"height:14px;max-width:14px;min-width:14px;max-height:14px;"
                f"min-height:14px;border-radius:7px;margin:-4px 0;}}"
                f"QSlider::groove:horizontal{{max-height:6px;min-height:6px;}}"
                f"QSlider::sub-page:horizontal{{background:{_hex_alpha(c, '88')};border-radius:3px;}}"
                f"QSlider::handle:horizontal:hover{{background:{_hex_alpha(c, 'CC')};"
                f"border:1px solid rgba(255,255,255,190);}}"
                f"QSlider::handle:horizontal:pressed{{background:{_hex_alpha(c, 'CC')};"
                f"border:1px solid {_th.FOCUS_BLUE};}}")
            self._master_slider.setStyleSheet(_ss_slider)
            self._master_slider.valueChanged.connect(
                lambda v, c=ct: self._on_master_changed(c, v))
            hlay.addWidget(self._master_slider)

            self._val_label = QLabel("0")
            self._val_label.setStyleSheet(
                f"color:{c};font-size:11px;font-family:Consolas;min-width:30px;")
            hlay.addWidget(self._val_label)

        elif "R" in self._ch_types:
            self._color_preview = QLabel()
            self._color_preview.setFixedSize(40, 20)
            self._color_preview.setStyleSheet(
                "background:#000000;border:1px solid #3D3D3D;border-radius:3px;")
            hlay.addWidget(self._color_preview)
            pick_btn = QPushButton("拾色")
            pick_btn.setFixedHeight(24)
            pick_btn.setToolTip("按当前 R/G/B 均值打开系统取色器（次级入口）")
            # 通用按钮四态外观由全局 QSS 提供，不再重复定义
            pick_btn.clicked.connect(self._pick_color)
            hlay.addWidget(pick_btn)
            _r183_add_color_mix_button(self, hlay)   # R183 项 1/2：Color Mix 入口
        ctrl_btn = QPushButton("控制器")
        ctrl_btn.setFixedSize(64, 22)
        ctrl_btn.setToolTip("展开控制器子面板（推杆/滑块/摇杆/音频四个标签页）")
        # 通用四态（含 checked=select_blue 白字）走全局 QSS；仅收窄内边距适配窄行头
        ctrl_btn.setStyleSheet("QPushButton{padding:0 4px;font-size:11px;}")
        ctrl_btn.setCheckable(True)
        ctrl_btn.clicked.connect(self._toggle_controller)
        hlay.addWidget(ctrl_btn)
        self._ctrl_btn = ctrl_btn

        outer.addWidget(self._header)

        # ★ 常显合并条带区：收起时也直接可见/可拖（合并编辑当前通道的**全部**设备），
        #   展开才显示逐台设备行。每通道类型一条。init 时构建一次，组切换只同步状态。
        for _ct in self._ch_types:
            _c = _ch_color(_ct)
            _strip_row = QWidget()
            scope_qss(_strip_row,
                f"background:{BG_PANEL};border-bottom:1px solid {_th.SEP_LINE_DARK};")
            _slay = QHBoxLayout(_strip_row)
            _slay.setContentsMargins(16, 3, 8, 3)
            _slay.setSpacing(6)
            _slay.addWidget(self._mk_small_label("范围条带:"))
            _strip = ProgrammerRangeStrip(_ct, _c)
            _strip._owner_row = self
            _st = self._get_range_state(_ct)
            _strip.set_state(_st.get("mode", "flat"), _st.get("base", 0), _st.get("target", 0))
            bind_strip_signals(_strip, self)
            self._range_strips[_ct] = _strip
            _slay.addWidget(_strip, 1)
            _vl = QLabel("0")
            _vl.setStyleSheet(
                f"color:{_c};font-size:10px;font-family:Consolas;min-width:92px;")
            self._range_value_labels[_ct] = _vl
            _slay.addWidget(_vl)
            _wl = QLabel(self._wing_block_label_text())
            _wl.setStyleSheet(f"color:{FG_DIM};font-size:10px;min-width:70px;")
            _wl.setToolTip("当前条带写入会先按这里的翼/块设置进行空间分组")
            self._wing_block_labels[_ct] = _wl
            _slay.addWidget(_wl)
            outer.addWidget(_strip_row)
            self._set_range_state(_ct, _st.get("mode", "flat"),
                                  _st.get("base", 0), _st.get("target", 0))

        # 控制器面板(推杆/滑块/摇杆/音频 4 标签)默认隐藏,构造昂贵。用占位容器,
        # 延到用户首次展开控制器时再建真正内容——消除打开 Programmer 时每卡片×4标签
        # 的一次性构建卡顿(N 卡片时可省数秒)。
        self._ctrl_panel = QWidget()
        self._ctrl_panel.setVisible(False)
        self._ctrl_panel_lay = QVBoxLayout(self._ctrl_panel)
        self._ctrl_panel_lay.setContentsMargins(0, 0, 0, 0)
        self._ctrl_panel_lay.setSpacing(0)
        self._ctrl_built = False
        outer.addWidget(self._ctrl_panel)

        self._body = QWidget()
        self._body.setStyleSheet(f"background:{BG_PANEL};")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(12, 4, 8, 4)
        # L 波 §11/A2：行距 ≥4px（原 2px 粘连）
        self._body_lay.setSpacing(4)
        self._body.setVisible(False)
        outer.addWidget(self._body)
        _pcui.arrange_channel_header(self, _indicator_color)

    def _ensure_controller_built(self):
        """首次需要时才构建控制器 4 标签内容(懒构建)。"""
        if self._ctrl_built:
            return
        self._ctrl_built = True
        inner = self._build_controller_panel()
        self._ctrl_panel_lay.addWidget(inner)

    def _toggle_controller(self, checked: bool):
        if checked:
            self._ensure_controller_built()
        self._ctrl_panel.setVisible(checked)

    def set_group_name(self, group_name: str):
        self._group_name = str(group_name or "")
        if hasattr(self, "_title") and _qobject_alive(self._title):
            self._title.setText(self._group_name)
        if hasattr(self, "_controller_title") and _qobject_alive(self._controller_title):
            self._controller_title.setText(f"{self._group_name} 控制器")

    def _on_value_wings_changed(self, value: int):
        self._set_wing_block(max(1, int(value)), self._value_block_size)

    def _on_value_block_changed(self, value: int):
        self._set_wing_block(self._value_wings, max(1, int(value)))

    def _set_wing_block(self, wings: int, block_size: int):
        self._value_wings = max(1, int(wings))
        self._value_block_size = max(1, int(block_size))
        self._sync_wing_block_controls()
        # ★ 同步所有融合条带控件的翼/块参数
        for _strips_dict in (
            getattr(self, "_ctrl_fader_strips",   {}),
            getattr(self, "_ctrl_vslider_strips", {}),
            getattr(self, "_ctrl_joy_strips",     {}),
        ):
            for rfw in _strips_dict.values():
                if hasattr(rfw, "set_wing_block"):
                    rfw.set_wing_block(self._value_wings, self._value_block_size)
        self._reapply_range_states_for_wing_block()

    def _make_wing_direction_toolbar(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("wingDirectionToolbar")
        # 裸声明会级联到子按钮，覆盖全局 QPushButton:checked 高亮；必须用 id 选择器限定本容器
        widget.setStyleSheet("#wingDirectionToolbar{background:transparent;border:none;}")
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        # L 波 §11/A2：同一行可交互件之间 ≥6px（原 1px 粘连）
        lay.setSpacing(6)
        for mode in WING_DIRECTION_MODES:
            btn = QPushButton(WING_DIRECTION_SYMBOLS.get(mode, "/"))
            btn.setCheckable(True)
            btn.setFixedSize(20, 20)
            btn.setToolTip(WING_DIRECTION_LABELS.get(mode, mode))
            # 微型方向钮：仅特化紧凑内边距与字号，四态外观走全局主题
            btn.setStyleSheet(
                "QPushButton{min-height:18px;padding:0;font-size:11px;font-weight:bold;}"
            )
            btn.clicked.connect(lambda _checked=False, m=mode: self._set_wing_direction(m))
            lay.addWidget(btn)
            self._wing_direction_buttons.append(btn)
        lay.addWidget(_wing_flags.create(self, self._reapply_range_states_for_wing_block, "_value_"))
        self._sync_wing_direction_controls()
        return widget

    def _set_wing_direction(self, mode: str):
        normalized = normalize_wing_direction(mode)
        if self._value_wing_direction == normalized:
            self._sync_wing_direction_controls()
            return
        self._value_wing_direction = normalized
        self._sync_wing_direction_controls()
        self._reapply_range_states_for_wing_block()

    def _sync_wing_direction_controls(self):
        live_buttons = []
        current = normalize_wing_direction(getattr(self, "_value_wing_direction", WING_DIRECTION_LOW_TO_HIGH))
        for btn in list(getattr(self, "_wing_direction_buttons", [])):
            if not _qobject_alive(btn):
                continue
            live_buttons.append(btn)
            mode = normalize_wing_direction(btn.toolTip())
            symbol = btn.text()
            for key, value in WING_DIRECTION_SYMBOLS.items():
                if value == symbol:
                    mode = key
                    break
            btn.blockSignals(True)
            try:
                btn.setChecked(mode == current)
            finally:
                btn.blockSignals(False)
        self._wing_direction_buttons = live_buttons

    def _wing_block_label_text(self) -> str:
        symbol = WING_DIRECTION_SYMBOLS.get(
            normalize_wing_direction(getattr(self, "_value_wing_direction", WING_DIRECTION_LOW_TO_HIGH)),
            "/",
        )
        return f"翼{self._value_wings} / 块{self._value_block_size} {symbol}"

    def _sync_wing_block_controls(self):
        live_spins = []
        for sp in list(getattr(self, "_wing_block_spins", [])):
            if not _qobject_alive(sp):
                continue
            live_spins.append(sp)
            sp.blockSignals(True)
            try:
                if sp in (getattr(self, "_header_wing_spin", None), getattr(self, "_spin_value_wings", None)):
                    sp.setValue(self._value_wings)
                elif sp in (getattr(self, "_header_block_spin", None), getattr(self, "_spin_value_block", None)):
                    sp.setValue(self._value_block_size)
            finally:
                sp.blockSignals(False)
        self._wing_block_spins = live_spins
        label_text = self._wing_block_label_text()
        labels = getattr(self, "_wing_block_labels", {})
        for ct, lbl in list(labels.items()):
            if _qobject_alive(lbl):
                lbl.setText(label_text)
            else:
                labels.pop(ct, None)
        self._sync_wing_direction_controls()

    def _reapply_range_states_for_wing_block(self):
        if getattr(self, "_is_refreshing", False):
            return
        _mark_pm_dirty(_panel_of(self))
        for ch_type, st in list(getattr(self, "_range_states", {}).items()):
            self._apply_channel_range(
                ch_type,
                st.get("mode", "flat"),
                int(st.get("base", 0)),
                int(st.get("target", 0)),
                layer="fader",
            )

    def _build_controller_panel(self) -> QWidget:
        panel = QWidget()
        scope_qss(panel,
            f"background:{BG_MAIN};border-top:1px solid {_th.BORDER_NORMAL};"
            f"border-bottom:1px solid {_th.BORDER_NORMAL};")
        main_lay = QVBoxLayout(panel)
        main_lay.setContentsMargins(8, 6, 8, 6)
        main_lay.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._controller_title = QLabel(f"{self._group_name} 控制器")
        self._controller_title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;")
        title_row.addWidget(self._controller_title)
        title_row.addStretch()
        wing_lab1 = self._mk_small_label("翼:")
        wing_lab1.setToolTip("把当前通道设备序列分成多个翼后再由条带/Ctrl/Shift 写入")
        title_row.addWidget(wing_lab1)
        self._spin_value_wings = QSpinBox()
        self._spin_value_wings.setRange(1, 32)
        self._spin_value_wings.setValue(self._value_wings)
        self._spin_value_wings.setFixedHeight(22)
        self._spin_value_wings.setFixedWidth(52)
        self._spin_value_wings.setStyleSheet(f"QSpinBox{{color:{FG_ACCENT};}}")
        self._spin_value_wings.valueChanged.connect(self._on_value_wings_changed)
        title_row.addWidget(self._spin_value_wings)
        self._wing_block_spins.append(self._spin_value_wings)
        blk_lab = self._mk_small_label("块:")
        blk_lab.setToolTip("每 N 台作为一个整体，共用同一个条带插值位置")
        title_row.addWidget(blk_lab)
        self._spin_value_block = QSpinBox()
        self._spin_value_block.setRange(1, 64)
        self._spin_value_block.setValue(self._value_block_size)
        self._spin_value_block.setFixedHeight(22)
        self._spin_value_block.setFixedWidth(52)
        self._spin_value_block.setStyleSheet(f"QSpinBox{{color:{FG_CYAN};}}")
        self._spin_value_block.valueChanged.connect(self._on_value_block_changed)
        title_row.addWidget(self._spin_value_block)
        self._wing_block_spins.append(self._spin_value_block)
        title_row.addWidget(self._make_wing_direction_toolbar())
        main_lay.addLayout(title_row)

        tabs = QTabWidget()
        # Tab pane/tab 四态外观由全局 QSS 统一提供，此处不再重复定义

        # ── 局部排版助手 ──────────────────────────────────────────────
        def _mk_range_pair(lo_spin, hi_spin):
            lo_dash = QLabel("-")
            lo_dash.setStyleSheet(f"color:{FG_DIM};font-size:10px;background:transparent;")
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            rl = QHBoxLayout(wrap)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)
            rl.addWidget(lo_spin)
            rl.addWidget(lo_dash)
            rl.addWidget(hi_spin)
            rl.addStretch(1)
            return wrap

        def _indent(widget, label_width=96):
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            il = QHBoxLayout(wrap)
            il.setContentsMargins(0, 0, 0, 0)
            il.addSpacing(label_width + 8)
            il.addWidget(widget)
            il.addStretch(1)
            return wrap

        # ══ Tab 1: 推杆（prop_row + PanelSection 版式）════════════════
        fader_tab = QWidget()
        fader_tab.setStyleSheet(f"background:{BG_PANEL};")
        fader_lay = QVBoxLayout(fader_tab)
        fader_lay.setContentsMargins(4, 4, 4, 4)
        fader_lay.setSpacing(8)   # L 波 §11/A1：卡间距 8

        sec_fmap = _KitSection("推杆映射")
        fader_curve_combo = QComboBox()
        fader_curve_combo.addItems(["线性", "Gamma", "S曲线", "反向", "阶梯"])
        fader_curve_combo.setFixedWidth(110)
        fader_curve_combo.setToolTip("推杆响应曲线：线性 / Gamma / S曲线 / 反向 / 阶梯")
        sec_fmap.add_widget(prop_row("曲线", fader_curve_combo))

        fader_blend_combo = QComboBox()
        fader_blend_combo.addItems(["覆盖", "叠加", "最大値", "相乘"])
        fader_blend_combo.setFixedWidth(90)
        fader_blend_combo.setToolTip("推杆层与其它来源的混合方式：覆盖/叠加/最大值/相乘")
        sec_fmap.add_widget(prop_row("叠加模式", fader_blend_combo))

        fader_lo_spin = QSpinBox()
        fader_lo_spin.setRange(0, 255)
        fader_lo_spin.setValue(0)
        fader_lo_spin.setFixedHeight(22)
        fader_lo_spin.setFixedWidth(54)
        fader_lo_spin.setToolTip("曲线输出下限（DMX 0-255）")
        fader_hi_spin = QSpinBox()
        fader_hi_spin.setRange(0, 255)
        fader_hi_spin.setValue(255)
        fader_hi_spin.setFixedHeight(22)
        fader_hi_spin.setFixedWidth(54)
        fader_hi_spin.setToolTip("曲线输出上限（DMX 0-255）")
        sec_fmap.add_widget(prop_row("输出范围",
                                     _mk_range_pair(fader_lo_spin, fader_hi_spin)))

        fader_gamma_slider = NumSlider(0.1, 5.0, 1.0, decimals=1, suffix="x")
        fader_gamma_slider.setToolTip("Gamma 指数：<1 暗部变亮，>1 暗部压缩（右键输入数值，滚轮微调）")
        sec_fmap.add_widget(prop_row("指数 Gamma", fader_gamma_slider))
        fader_lay.addWidget(sec_fmap)

        _FADER_CURVE_MAP = {"线性": "linear", "Gamma": "gamma",
                            "S曲线": "scurve", "反向": "invert", "阶梯": "step"}
        _FADER_BLEND_MAP = {"覆盖": "override", "叠加": "add",
                            "最大値": "max", "相乘": "multiply"}

        def _on_fader_curve(text):
            self._fader_curve = _FADER_CURVE_MAP.get(text, "linear")
        def _on_fader_blend(text):
            self._layer_modes["fader"] = _FADER_BLEND_MAP.get(text, "override")
        def _on_fader_lo(v): self._fader_lo = v
        def _on_fader_hi(v): self._fader_hi = v
        def _on_fader_gamma(v): self._fader_gamma = v
        fader_curve_combo.currentTextChanged.connect(_on_fader_curve)
        fader_blend_combo.currentTextChanged.connect(_on_fader_blend)
        fader_lo_spin.valueChanged.connect(_on_fader_lo)
        fader_hi_spin.valueChanged.connect(_on_fader_hi)
        # NumSlider 的原生信号是缩放后的 int 值；float 读数统一走 _ns_value。
        fader_gamma_slider.valueChanged.connect(
            lambda *_i: _on_fader_gamma(_ns_value(fader_gamma_slider)))

        for ct in self._ch_types:
            c = _ch_color(ct)
            ct_label = _ch_label(ct)
            # ★ 推杆+条带融合控件
            rfw = RangeFaderWidget(ct, c, label=ct_label)
            rfw.set_wing_block(self._value_wings, self._value_block_size)
            st = self._get_range_state(ct)
            rfw.set_state(st.get("mode", "flat"), st.get("base", 0), st.get("target", 0))
            bind_strip_signals(rfw, self)
            self._ctrl_fader_strips[ct] = rfw
            fader_lay.addWidget(rfw)

        fader_lay.addStretch()
        tabs.addTab(fader_tab, "推杆")

        # ══ Tab 2: 滑块 ══════════════════════════════════════════════
        slider_tab = QWidget()
        slider_tab.setStyleSheet(f"background:{BG_PANEL};")
        slider_lay = QVBoxLayout(slider_tab)
        slider_lay.setContentsMargins(4, 4, 4, 4)
        slider_lay.setSpacing(8)   # L 波 §11/A1：卡间距 8

        sec_smap = _KitSection("滑块映射")
        lo_spin = QSpinBox()
        lo_spin.setRange(0, 255)
        lo_spin.setValue(0)
        lo_spin.setFixedHeight(22)
        lo_spin.setFixedWidth(54)
        lo_spin.setToolTip("映射范围下限（DMX 0-255）")
        hi_spin = QSpinBox()
        hi_spin.setRange(0, 255)
        hi_spin.setValue(255)
        hi_spin.setFixedHeight(22)
        hi_spin.setFixedWidth(54)
        hi_spin.setToolTip("映射范围上限（DMX 0-255）")
        sec_smap.add_widget(prop_row("映射范围", _mk_range_pair(lo_spin, hi_spin)))
        slider_lay.addWidget(sec_smap)

        for ct in self._ch_types:
            c = _ch_color(ct)
            ct_label = _ch_label(ct)
            # ★ 滑块Tab也使用融合控件，映射范围由 lo_spin/hi_spin 控制
            rfw_s = RangeFaderWidget(ct, c, label=ct_label)
            rfw_s.set_wing_block(self._value_wings, self._value_block_size)
            st2 = self._get_range_state(ct)
            rfw_s.set_state(st2.get("mode", "flat"), st2.get("base", 0), st2.get("target", 0))
            def _on_rfw_slider(ch, mode, base, target, lo=lo_spin, hi=hi_spin):
                # 将 0-255 范围映射到 lo-hi 范围
                scale = (hi.value() - lo.value()) / 255.0
                mapped_base   = int(lo.value() + base   * scale)
                mapped_target = int(lo.value() + target * scale)
                self._on_range_strip_changed(ch, mode, mapped_base, mapped_target)
            bind_strip_signals(rfw_s, self, _on_rfw_slider)
            self._ctrl_vslider_strips[ct] = rfw_s
            slider_lay.addWidget(rfw_s)

        slider_lay.addStretch()
        tabs.addTab(slider_tab, "滑块")

        # ══ Tab 3: 摇杆 ══════════════════════════════════════════════
        joy_tab = QWidget()
        joy_tab.setStyleSheet(f"background:{BG_PANEL};")
        joy_lay = QHBoxLayout(joy_tab)
        joy_lay.setContentsMargins(8, 6, 8, 6)
        joy_lay.setSpacing(12)

        joy_info = QVBoxLayout()
        joy_info.setSpacing(4)

        # ★ 摇杆模式切换：叠加模式 / 幅度控制模式
        sec_jmode = _KitSection("摇杆模式")
        joy_mode_combo = QComboBox()
        joy_mode_combo.addItems(["叠加模式", "幅度控制模式"])
        joy_mode_combo.setCurrentText("幅度控制模式" if self._joy_mode == "amplitude" else "叠加模式")
        joy_mode_combo.setFixedWidth(130)
        joy_mode_combo.setToolTip(
            "叠加模式=摇杆位置直接驱动通道；幅度控制模式=把摇杆注入效果的参数覆盖")
        amp_title = QLabel("幅度控制配置")
        amp_title.setStyleSheet(
            f"color:{FG_GREEN};font-size:11px;font-weight:bold;background:transparent;")
        amp_x_combo = QComboBox()
        amp_x_combo.addItems(["joy_amplitude_x", "joy_amplitude_y", "joy_amplitude_z", "(不使用)"])
        amp_x_combo.setCurrentText(self._joy_amp_x_target or "(不使用)")
        amp_x_combo.setFixedWidth(130)
        amp_x_combo.setToolTip("物理摇杆 X 轴注入的效果参数（幅度控制模式生效）")
        amp_y_combo = QComboBox()
        amp_y_combo.addItems(["joy_amplitude_y", "joy_amplitude_x", "joy_amplitude_z", "(不使用)"])
        amp_y_combo.setCurrentText(self._joy_amp_y_target or "(不使用)")
        amp_y_combo.setFixedWidth(130)
        amp_y_combo.setToolTip("物理摇杆 Y 轴注入的效果参数（幅度控制模式生效）")
        amp_z_combo = QComboBox()
        amp_z_combo.addItems(["joy_amplitude_z", "joy_amplitude_x", "joy_amplitude_y", "(不使用)"])
        amp_z_combo.setCurrentText(self._joy_amp_z_target or "(不使用)")
        amp_z_combo.setFixedWidth(130)
        amp_z_combo.setToolTip("物理摇杆 Z 轴注入的效果参数（幅度控制模式生效）")

        # L 波 §11/A1：幅度控制配置=独立凹陷卡（bg_editor+发丝边+4px 圆角+内距8）
        amp_config_frame = QFrame()
        amp_config_frame.setStyleSheet(
            f"QFrame{{background:{_th.BG_EDITOR};"
            f"border:1px solid rgba(255,255,255,17);"
            f"border-radius:{_th.RADIUS}px;}}")
        amp_config_lay = QVBoxLayout(amp_config_frame)
        amp_config_lay.setContentsMargins(8, 6, 8, 8)
        # L 波 §11/A2：行距 ≥4px（原 2px 粘连）
        amp_config_lay.setSpacing(4)
        amp_config_lay.addWidget(amp_title)
        amp_config_lay.addWidget(prop_row("X 轴 →", amp_x_combo))
        amp_config_lay.addWidget(prop_row("Y 轴 →", amp_y_combo))
        amp_config_lay.addWidget(prop_row("Z 轴 →", amp_z_combo))

        amp_config_frame.setVisible(self._joy_mode == "amplitude")
        sec_jmode.add_widget(prop_row("模式", joy_mode_combo))
        sec_jmode.add_widget(_indent(amp_config_frame))
        joy_info.addWidget(sec_jmode)

        # 存储引用供回调使用
        self._joy_mode_combo = joy_mode_combo
        self._joy_amp_x_combo = amp_x_combo
        self._joy_amp_y_combo = amp_y_combo
        self._joy_amp_z_combo = amp_z_combo
        self._joy_amp_config_frame = amp_config_frame

        def _on_joy_mode_changed(text, _frame=amp_config_frame):
            if text == "幅度控制模式":
                self._joy_mode = "amplitude"
                _frame.setVisible(True)
            else:
                self._joy_mode = "additive"
                _frame.setVisible(False)
                # 切回叠加模式时清除 param_overrides
                self._clear_joy_amplitude_overrides()

        def _on_amp_x_target(text):
            self._joy_amp_x_target = text if text != "(不使用)" else None
        def _on_amp_y_target(text):
            self._joy_amp_y_target = text if text != "(不使用)" else None
        def _on_amp_z_target(text):
            self._joy_amp_z_target = text if text != "(不使用)" else None

        joy_info.addWidget(self._mk_small_label("轴映射"))

        sec_axes = _KitSection("轴映射")
        space_combo = QComboBox()
        space_combo.addItems(["二维模式", "三维模式"])
        space_combo.setFixedWidth(110)
        space_combo.setToolTip("二维=平面 XY；三维=增加 Z 油门轴")
        sec_axes.add_widget(prop_row("空间模式", space_combo))
        coord_combo = QComboBox()
        coord_combo.addItems(["XY直角", "极坐标"])
        coord_combo.setFixedWidth(110)
        coord_combo.setToolTip("XY 直角输出或极坐标（半径/角度）输出")
        sec_axes.add_widget(prop_row("坐标系", coord_combo))

        self._axis_x_combo = QComboBox()
        self._axis_x_combo.addItems(["(无)"] + [_ch_label(ct) for ct in self._ch_types])
        self._axis_x_combo.setFixedWidth(110)
        if "motor_x" in self._ch_types:
            self._axis_x_combo.setCurrentIndex(self._ch_types.index("motor_x") + 1)
        sec_axes.add_widget(prop_row("X 轴 →", self._axis_x_combo))

        self._axis_y_combo = QComboBox()
        self._axis_y_combo.addItems(["(无)"] + [_ch_label(ct) for ct in self._ch_types])
        self._axis_y_combo.setFixedWidth(110)
        if "motor_y" in self._ch_types:
            self._axis_y_combo.setCurrentIndex(self._ch_types.index("motor_y") + 1)
        sec_axes.add_widget(prop_row("Y 轴 →", self._axis_y_combo))

        self._axis_z_combo = QComboBox()
        self._axis_z_combo.addItems(["(无)"] + [_ch_label(ct) for ct in self._ch_types])
        self._axis_z_combo.setFixedWidth(110)
        if "motor_z" in self._ch_types:
            self._axis_z_combo.setCurrentIndex(self._ch_types.index("motor_z") + 1)
        z_ax_wrap = prop_row("Z 轴 →", self._axis_z_combo)
        sec_axes.add_widget(z_ax_wrap)
        z_row_widget_items = [self._axis_z_combo]

        spring_chk = QCheckBox("弹性回中")
        spring_chk.setChecked(False)
        spring_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        spring_chk.setToolTip("松手后摇杆自动回到中心位")
        sec_axes.add_widget(_indent(spring_chk))
        joy_info.addWidget(sec_axes)

        joy_mode_combo.currentTextChanged.connect(_on_joy_mode_changed)
        amp_x_combo.currentTextChanged.connect(_on_amp_x_target)
        amp_y_combo.currentTextChanged.connect(_on_amp_y_target)
        amp_z_combo.currentTextChanged.connect(_on_amp_z_target)

        sec_curve = _KitSection("曲线与范围")
        joy_curve_combo = QComboBox()
        joy_curve_combo.addItems(["线性", "Gamma", "S曲线", "反向", "阶梯"])
        joy_curve_combo.setFixedWidth(95)
        joy_curve_combo.setToolTip("摇杆响应曲线（同推杆曲线族）")
        sec_curve.add_widget(prop_row("曲线", joy_curve_combo))
        joy_blend_combo = QComboBox()
        joy_blend_combo.addItems(["覆盖", "叠加", "最大値", "相乘"])
        joy_blend_combo.setCurrentText("叠加")
        joy_blend_combo.setFixedWidth(95)
        joy_blend_combo.setToolTip("摇杆层与其它来源的混合方式")
        sec_curve.add_widget(prop_row("叠加模式", joy_blend_combo))
        joy_lo_spin = QSpinBox()
        joy_lo_spin.setRange(0, 255)
        joy_lo_spin.setValue(0)
        joy_lo_spin.setFixedHeight(22)
        joy_lo_spin.setFixedWidth(50)
        joy_lo_spin.setToolTip("摇杆输出范围下限")
        joy_hi_spin = QSpinBox()
        joy_hi_spin.setRange(0, 255)
        joy_hi_spin.setValue(255)
        joy_hi_spin.setFixedHeight(22)
        joy_hi_spin.setFixedWidth(50)
        joy_hi_spin.setToolTip("摇杆输出范围上限")
        sec_curve.add_widget(prop_row(
            "范围", _mk_range_pair(joy_lo_spin, joy_hi_spin)))
        joy_info.addWidget(sec_curve)
        joy_info.addStretch()
        joy_lay.addLayout(joy_info)

        _JOY_CURVE_MAP = {"线性": "linear", "Gamma": "gamma",
                          "S曲线": "scurve", "反向": "invert", "阶梯": "step"}
        _JOY_BLEND_MAP = {"覆盖": "override", "叠加": "add",
                          "最大値": "max", "相乘": "multiply"}

        def _on_joy_curve(text): self._joy_curve = _JOY_CURVE_MAP.get(text, "linear")
        def _on_joy_blend(text): self._layer_modes["joystick"] = _JOY_BLEND_MAP.get(text, "override")
        def _on_joy_lo(v): self._joy_lo = v
        def _on_joy_hi(v): self._joy_hi = v
        def _on_joy_coord(text): self._joy_coord_mode = "polar" if text == "极坐标" else "cartesian"
        coord_combo.currentTextChanged.connect(_on_joy_coord)
        joy_curve_combo.currentTextChanged.connect(_on_joy_curve)
        joy_blend_combo.currentTextChanged.connect(_on_joy_blend)
        joy_lo_spin.valueChanged.connect(_on_joy_lo)
        joy_hi_spin.valueChanged.connect(_on_joy_hi)

        self.ctrl_joy = XYJoystick()
        self.ctrl_joy.setFixedSize(200, 200)
        self.ctrl_joy.set_spring(False)
        spring_chk.toggled.connect(self.ctrl_joy.set_spring)

        def _on_space_mode(text):
            use_3d = text == "三维模式"
            self._joy_space_mode = "3d" if use_3d else "2d"
            self.ctrl_joy.set_mode(self._joy_space_mode)
            coord_combo.setEnabled(not use_3d)
            for w in z_row_widget_items:
                w.setEnabled(use_3d)
            if not use_3d:
                self._clear_joy_amplitude_overrides()
        space_combo.currentTextChanged.connect(_on_space_mode)
        for w in z_row_widget_items:
            w.setEnabled(False)

        def _on_ctrl_joy(x, y, z=0.5, xc=self._axis_x_combo, yc=self._axis_y_combo, zc=self._axis_z_combo):
            if getattr(self, '_joy_mode', 'additive') == "amplitude":
                # ★ 幅度控制模式：将摇杆位置映射为有符号幅度因子 (-1~+1)
                # x: 0.0~1.0 → -1.0~+1.0
                # y: 0.0~1.0 → -1.0~+1.0
                amp_x = (x - 0.5) * 2.0  # 中位=0, 左=-1, 右=+1
                amp_y = (y - 0.5) * 2.0  # 中位=0, 下=-1, 上=+1
                amp_z = (z - 0.5) * 2.0
                self._send_joy_amplitude_overrides(amp_x, amp_y, amp_z)
            else:
                # 叠加模式：原始逻辑
                xi = xc.currentIndex() - 1
                yi = yc.currentIndex() - 1
                zi = zc.currentIndex() - 1
                out_x, out_y, out_z = self._convert_joystick_xyz(x, y, z)
                if 0 <= xi < len(self._ch_types):
                    raw_x = out_x * 255.0
                    mapped_x = self._apply_curve_float(
                        raw_x, self._joy_curve, self._joy_lo, self._joy_hi,
                        self._joy_gamma, self._joy_steps)
                    self._on_master_changed(self._ch_types[xi], mapped_x, layer="joystick")
                if 0 <= yi < len(self._ch_types):
                    raw_y = out_y * 255.0
                    mapped_y = self._apply_curve_float(
                        raw_y, self._joy_curve, self._joy_lo, self._joy_hi,
                        self._joy_gamma, self._joy_steps)
                    self._on_master_changed(self._ch_types[yi], mapped_y, layer="joystick")
                if getattr(self, "_joy_space_mode", "2d") == "3d" and 0 <= zi < len(self._ch_types):
                    raw_z = out_z * 255.0
                    mapped_z = self._apply_curve_float(
                        raw_z, self._joy_curve, self._joy_lo, self._joy_hi,
                        self._joy_gamma, self._joy_steps)
                    self._on_master_changed(self._ch_types[zi], mapped_z, layer="joystick")
        self.ctrl_joy.position_changed.connect(_on_ctrl_joy)
        self.ctrl_joy.position3d_changed.connect(_on_ctrl_joy)
        joy_lay.addWidget(self.ctrl_joy)

        # ★ 摇杆Tab的条带区域：每个通道类型独立一条（融合控件）。
        # L 波原型A 净化：BG_DEEP 纯深黑大底改 bg_box 半浮卡+发丝边（禁大片纯黑）。
        joy_strip_frame = QFrame()
        joy_strip_frame.setStyleSheet(
            f"QFrame{{background:{_th.BG_BOX};"
            f"border:1px solid rgba(255,255,255,17);"
            f"border-radius:{_th.RADIUS}px;}}")
        joy_strip_vlay = QVBoxLayout(joy_strip_frame)
        joy_strip_vlay.setContentsMargins(6, 4, 6, 4)
        joy_strip_vlay.setSpacing(4)
        joy_strip_title = QLabel("条带应用（拖动=平移 | Ctrl=单边 | Shift=双边）")
        joy_strip_title.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;")
        joy_strip_vlay.addWidget(joy_strip_title)
        for ct in self._ch_types:
            c = _ch_color(ct)
            ct_label = _ch_label(ct)
            rfw_j = RangeFaderWidget(ct, c, label=ct_label)
            rfw_j.set_wing_block(self._value_wings, self._value_block_size)
            st_j = self._get_range_state(ct)
            rfw_j.set_state(st_j.get("mode", "flat"), st_j.get("base", 0), st_j.get("target", 0))
            bind_strip_signals(rfw_j, self)
            self._ctrl_joy_strips[ct] = rfw_j
            joy_strip_vlay.addWidget(rfw_j)
        joy_lay.addWidget(joy_strip_frame, 1)
        tabs.addTab(joy_tab, "摇杆")

        # ══ Tab 4: 音频 ══════════════════════════════════════════════
        audio_tab = QWidget()
        audio_tab.setStyleSheet(f"background:{BG_PANEL};")
        audio_lay = QVBoxLayout(audio_tab)
        audio_lay.setContentsMargins(4, 4, 4, 4)
        audio_lay.setSpacing(8)   # L 波 §11/A1：卡间距 8

        if _AUDIO_AVAILABLE:
            sec_drive = _KitSection("音频驱动")
            audio_mode_combo = QComboBox()
            audio_mode_combo.addItems(["单通道驱动", "频谱分段映射"])
            audio_mode_combo.setFixedWidth(140)
            audio_mode_combo.setToolTip("单通道=单一通道跟随能量；频谱=按频段分布到设备")
            self._audio_mode_combo_ref = audio_mode_combo
            sec_drive.add_widget(prop_row("驱动方式", audio_mode_combo))
            audio_start_btn = QPushButton("启动音频驱动")
            self._audio_start_btn_ref = audio_start_btn
            audio_start_btn.setFixedHeight(24)
            audio_start_btn.setToolTip("启动/停止音频能量驱动（再次点击停止）")
            # 启动/停止单钮双态，通用四态外观由全局主题提供
            audio_start_btn.setCheckable(True)
            sec_drive.add_widget(_indent(audio_start_btn))
            audio_lay.addWidget(sec_drive)

            from PyQt6.QtWidgets import QStackedWidget
            audio_stack = QStackedWidget()
            audio_stack.setStyleSheet(f"background:{BG_PANEL};")

            # ── 单通道页 ─────────────────────────────────────────────
            single_page = QWidget()
            single_page.setStyleSheet(f"background:{BG_PANEL};")
            single_lay = QVBoxLayout(single_page)
            single_lay.setContentsMargins(0, 4, 0, 0)
            single_lay.setSpacing(4)

            sec_single = _KitSection("单通道参数")
            ch_sel = QComboBox()
            ch_sel.addItems([_ch_label(ct) for ct in self._ch_types])
            ch_sel.setFixedWidth(110)
            ch_sel.setToolTip("受音频能量驱动的通道")
            sec_single.add_widget(prop_row("驱动通道", ch_sel))

            audio_color_mode_combo = QComboBox()
            audio_color_mode_combo.addItems([
                "单通道驱动",          # 0: 原有模式，驱动单个通道
                "选色应用亮度",        # 1: 固定颜色，亮度随音频能量变化
                "叠加颜色",            # 2: 将选定颜色叠加到现有 RGB 上
            ])
            audio_color_mode_combo.setFixedWidth(120)
            audio_color_mode_combo.setToolTip("颜色驱动：固定色随能量明暗，或向目标色偏转")
            # 颜色拾色色块（属性行同款：14px 高圆角色块）
            audio_color_btn = QPushButton()
            audio_color_btn.setFixedSize(28, 16)
            audio_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            audio_color_btn.setStyleSheet(
                f"QPushButton{{background:#FF4444;border:1px solid {_th.BORDER_NORMAL};"
                f"border-radius:3px;}}"
                f"QPushButton:hover{{border-color:{_th.FOCUS_BLUE};}}")
            audio_color_btn.setToolTip("点击选择颜色")

            color_wrap = QWidget()
            color_wrap.setStyleSheet("background:transparent;")
            cw = QHBoxLayout(color_wrap)
            cw.setContentsMargins(0, 0, 0, 0)
            cw.setSpacing(6)
            cw.addWidget(audio_color_mode_combo)
            cw.addWidget(audio_color_btn)
            cw.addStretch(1)
            sec_single.add_widget(prop_row("颜色模式", color_wrap))
            single_lay.addWidget(sec_single)

            # 内部状态：当前选定的颜色（默认红色）
            self._audio_color_rgb = (255, 68, 68)  # (R, G, B)

            def _update_audio_color_btn(r, g, b, btn=audio_color_btn):
                btn.setStyleSheet(
                    f"QPushButton{{background:rgb({r},{g},{b});border:1px solid {_th.BORDER_NORMAL};"
                    f"border-radius:3px;}}QPushButton:hover{{border-color:{_th.FOCUS_BLUE};}}")

            def _on_audio_color_pick():
                r0, g0, b0 = self._audio_color_rgb
                color = QColorDialog.getColor(QColor(r0, g0, b0), self, "音频驱动颜色")
                if color.isValid():
                    self._audio_color_rgb = (color.red(), color.green(), color.blue())
                    _update_audio_color_btn(*self._audio_color_rgb)
            audio_color_btn.clicked.connect(_on_audio_color_pick)

            def _on_audio_color_mode_changed(idx, btn=audio_color_btn, csel=ch_sel):
                # 单通道模式下显示通道选择，颜色模式下隐藏通道选择
                csel.setVisible(idx == 0)
                btn.setVisible(idx > 0)
            audio_color_mode_combo.currentIndexChanged.connect(_on_audio_color_mode_changed)
            _on_audio_color_mode_changed(0)  # 初始化显示状态

            audio_widget = AudioDriverWidget(
                ch_type=self._ch_types[0] if self._ch_types else "dimmer")

            def _on_audio_ch_changed(idx, aw=audio_widget, cts=self._ch_types):
                if 0 <= idx < len(cts):
                    aw.set_channel_type(cts[idx])
            ch_sel.currentIndexChanged.connect(_on_audio_ch_changed)

            # ★ 音频值直接走 channel_value_changed → _on_master_changed
            # 在颜色模式下拦截并转化为 RGB 输出
            def _on_audio_value_with_color(ch_type, val,
                                           mode_combo=audio_color_mode_combo):
                mode = mode_combo.currentIndex()
                if mode == 0:
                    # 单通道模式：原有逻辑
                    self._on_audio_value(ch_type, val)
                elif mode == 1:
                    # 选色应用亮度：固定颜色，亮度随音频能量变化
                    if not self._audio_active:
                        return
                    r0, g0, b0 = self._audio_color_rgb
                    scale = val / 255.0
                    rv = int(r0 * scale)
                    gv = int(g0 * scale)
                    bv = int(b0 * scale)
                    batch: Dict[str, Dict[str, int]] = {}
                    for fi in _runtime_targets.audio_fixtures(self):
                        fd = self._fixture_defs.get(fi.def_id)
                        for ct, cv in [("R", rv), ("G", gv), ("B", bv)]:
                            if _fixture_supports_logical_channel(fd, ct):
                                batch.setdefault(fi.id, {})[ct] = max(0, min(255, int(cv)))
                    self._apply_realtime_layer_batch(batch, "audio", refresh_body=False)
                elif mode == 2:
                    # 叠加颜色：以当前颜色为基准，随音频能量向目标颜色偏转
                    if not self._audio_active:
                        return
                    r0, g0, b0 = self._audio_color_rgb
                    scale = max(0.0, min(1.0, float(val) / 255.0))
                    batch: Dict[str, Dict[str, int]] = {}
                    for fi in _runtime_targets.audio_fixtures(self):
                        fd = self._fixture_defs.get(fi.def_id)
                        for ct, target in [("R", r0), ("G", g0), ("B", b0)]:
                            if not _fixture_supports_logical_channel(fd, ct):
                                continue
                            base = self._compute_scene_value(
                                fi.id, ct, exclude_layers={"audio"})
                            blended = int(round(base + (int(target) - base) * scale))
                            blended = max(0, min(255, blended))
                            batch.setdefault(fi.id, {})[ct] = blended
                    self._apply_realtime_layer_batch(batch, "audio", refresh_body=False)

            audio_widget.channel_value_changed.connect(_on_audio_value_with_color)

            self._audio_widget_ref = audio_widget
            single_lay.addWidget(audio_widget)
            audio_stack.addWidget(single_page)

            spectrum_page = QWidget()
            spectrum_page.setStyleSheet(f"background:{BG_PANEL};")
            spec_lay = QVBoxLayout(spectrum_page)
            spec_lay.setContentsMargins(0, 4, 0, 0)
            spec_lay.setSpacing(6)

            # ── 频谱映射分区（prop_row 版式）──────────────────────────
            sec_spec = _KitSection("频谱映射")

            spec_ch_sel = QComboBox()
            spec_ch_sel.addItems([_ch_label(ct) for ct in self._ch_types])
            spec_ch_sel.setFixedWidth(110)
            spec_ch_sel.setToolTip("受频谱驱动的通道（多色彩色条模式下仅作单色回退）")
            sec_spec.add_widget(prop_row("映射通道", spec_ch_sel))

            spec_bands_spin = QSpinBox()
            spec_bands_spin.setRange(1, 8)
            spec_bands_spin.setValue(8)
            spec_bands_spin.setFixedHeight(22)
            spec_bands_spin.setFixedWidth(54)
            spec_bands_spin.setToolTip("把设备序列分成的频段数（1-8）")
            sec_spec.add_widget(prop_row("分段数", spec_bands_spin))

            spec_dir_combo = QComboBox()
            spec_dir_combo.addItems(["X 轴（左→右）", "Y 轴（上→下）"])
            spec_dir_combo.setFixedWidth(130)
            spec_dir_combo.setToolTip("按设备的空间 X 或 Y 排布分配频段")
            sec_spec.add_widget(prop_row("排列方向", spec_dir_combo))

            spec_layout_combo = QComboBox()
            spec_layout_combo.addItems([
                "线性（左→右）",       # 0: 默认，低频在左高频在右
                "线性（右→左）",       # 1: 左右翻转
                "中间低频/两边高频",  # 2: 中心是低频，向两边高频延伸
                "两边低频/中间高频",  # 3: 两边是低频，中心是高频
                "随机洗牌",           # 4: 随机分配频段
                "进阶阶梯",           # 5: 频段分布按幂次方式层叠
            ])
            spec_layout_combo.setFixedWidth(150)
            spec_layout_combo.setToolTip("频段在设备序列上的分布预设")
            sec_spec.add_widget(prop_row("分频布局", spec_layout_combo))

            spec_offset_spin = QSpinBox()
            spec_offset_spin.setRange(0, 7)
            spec_offset_spin.setValue(0)
            spec_offset_spin.setFixedHeight(22)
            spec_offset_spin.setFixedWidth(54)
            spec_offset_spin.setToolTip("频段起始偏移：0=从最低频开始，7=从最高频开始")
            sec_spec.add_widget(prop_row("基线偏移", spec_offset_spin))

            spec_multicolor_check = QCheckBox("启用多色彩色条")
            spec_multicolor_check.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            spec_multicolor_check.setToolTip("启用后，每个频段使用独立颜色驱动 RGB")
            sec_spec.add_widget(_indent(spec_multicolor_check))

            # ★ 多色彩色条配置区（默认隐藏，启用多色模式后显示）
            spec_multicolor_widget = QWidget()
            spec_multicolor_widget.setVisible(False)
            spec_mc_lay = QHBoxLayout(spec_multicolor_widget)
            spec_mc_lay.setContentsMargins(104 + 8, 2, 0, 2)
            spec_mc_lay.setSpacing(3)
            # 8 个频段的颜色按鈕
            _spec_band_colors = [(255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 0),
                                 (0, 255, 255), (0, 128, 255), (128, 0, 255), (255, 0, 255)]
            self._spec_band_colors = list(_spec_band_colors)
            _spec_color_btns = []
            for _bi, (_br, _bg, _bb) in enumerate(_spec_band_colors):
                _btn = QPushButton()
                _btn.setFixedSize(20, 16)
                _btn.setCursor(Qt.CursorShape.PointingHandCursor)
                _btn.setStyleSheet(
                    f"QPushButton{{background:rgb({_br},{_bg},{_bb});"
                    f"border:1px solid {_th.BORDER_NORMAL};border-radius:3px;}}"
                    f"QPushButton:hover{{border-color:{_th.FOCUS_BLUE};}}")
                _btn.setToolTip(f"{['Sub','Bass','LowMid','Mid','HighMid','Presence','Brilliance','Air'][_bi]}频段颜色")
                def _make_color_picker(bi, btn):
                    def _pick():
                        r0, g0, b0 = self._spec_band_colors[bi]
                        color = QColorDialog.getColor(QColor(r0, g0, b0), self,
                                                      f"频段{bi+1}颜色")
                        if color.isValid():
                            self._spec_band_colors[bi] = (color.red(), color.green(), color.blue())
                            btn.setStyleSheet(
                                f"QPushButton{{background:rgb({color.red()},{color.green()},{color.blue()});"
                                f"border:1px solid {_th.BORDER_NORMAL};border-radius:3px;}}"
                                f"QPushButton:hover{{border-color:{_th.FOCUS_BLUE};}}")
                    return _pick
                _btn.clicked.connect(_make_color_picker(_bi, _btn))
                _spec_color_btns.append(_btn)
                spec_mc_lay.addWidget(_btn)
            spec_mc_lay.addStretch()
            sec_spec.add_widget(spec_multicolor_widget)
            spec_lay.addWidget(sec_spec)

            # ── 动态包络分区 ──────────────────────────────────────────
            sec_dyn = _KitSection("动态包络")
            # 浮点参数换 widget_kit.NumSlider：值绘在滑槽上、右键弹数值输入。
            # 变量名保持 *_spin 以复用下游 handler 引用；读值统一走 _ns_value。
            spec_gain_spin = NumSlider(0.05, 10.0, 1.0, decimals=2, suffix="x")
            spec_gain_spin.setToolTip("能量增益倍率（右键输入数值）")
            sec_dyn.add_widget(prop_row("增益", spec_gain_spin))
            spec_threshold_spin = NumSlider(0.0, 1.0, 0.0, decimals=2, suffix="")
            spec_threshold_spin.setToolTip("低于该能量的频段视为静默（0-1 归一化）")
            sec_dyn.add_widget(prop_row("阈值", spec_threshold_spin))
            spec_attack_spin = QSpinBox()
            spec_attack_spin.setRange(1, 1000)
            spec_attack_spin.setValue(25)
            spec_attack_spin.setSuffix("ms")
            spec_attack_spin.setFixedHeight(22)
            spec_attack_spin.setFixedWidth(72)
            spec_attack_spin.setToolTip("起音时间：能量上升的跟随速度")
            sec_dyn.add_widget(prop_row("Attack", spec_attack_spin))
            spec_release_spin = QSpinBox()
            spec_release_spin.setRange(1, 3000)
            spec_release_spin.setValue(180)
            spec_release_spin.setSuffix("ms")
            spec_release_spin.setFixedHeight(22)
            spec_release_spin.setFixedWidth(78)
            spec_release_spin.setToolTip("释放时间：能量衰减的跟随速度")
            sec_dyn.add_widget(prop_row("Release", spec_release_spin))
            spec_lay.addWidget(sec_dyn)

            def _on_spec_multicolor_toggled(checked, w=spec_multicolor_widget):
                w.setVisible(checked)
            spec_multicolor_check.toggled.connect(_on_spec_multicolor_toggled)

            try:
                from .audio_driver import SpectrumBar as _SpectrumBar
                spec_bar = _SpectrumBar()
                spec_lay.addWidget(spec_bar)
            except Exception:
                spec_bar = None

            try:
                from .audio_driver import _ensure_valid_analyzer as _eva
                _spec_analyzer = _eva()
                _spec_frame_handler = lambda frame: self._on_spectrum_frame(
                    frame, spec_bar, spec_bands_spin, spec_dir_combo, spec_ch_sel,
                    spec_layout_combo, spec_offset_spin, spec_multicolor_check,
                    spec_gain_spin, spec_threshold_spin, spec_attack_spin,
                    spec_release_spin)
                self._spec_frame_handler = _spec_frame_handler
                _spec_analyzer.frame_ready.connect(_spec_frame_handler)
                self._spec_analyzer_ref = _spec_analyzer
            except Exception:
                pass

            audio_stack.addWidget(spectrum_page)

            def _on_audio_mode_changed(idx, stack=audio_stack):
                stack.setCurrentIndex(idx)
            audio_mode_combo.currentIndexChanged.connect(_on_audio_mode_changed)

            def _toggle_audio(checked, aw=audio_widget):
                self._audio_active = checked
                if checked:
                    _runtime_targets.start_audio(self)
                    aw.start()
                    spec = getattr(self, '_spec_analyzer_ref', None)
                    handler = getattr(self, '_spec_frame_handler', None)
                    if spec is not None and handler is not None:
                        try:
                            spec.frame_ready.disconnect(handler)
                        except Exception:
                            pass
                        spec.frame_ready.connect(handler)
                    audio_start_btn.setText("停止音频驱动")
                else:
                    _runtime_targets.stop_audio(self)
                    spec = getattr(self, '_spec_analyzer_ref', None)
                    handler = getattr(self, '_spec_frame_handler', None)
                    if spec is not None and handler is not None:
                        try:
                            spec.frame_ready.disconnect(handler)
                        except Exception:
                            pass
                    audio_start_btn.setText("启动音频驱动")
            audio_start_btn.clicked.connect(_toggle_audio)

            audio_lay.addWidget(audio_stack)
        else:
            audio_lay.addWidget(EmptyState(
                "音频驱动不可用",
                "安装 sounddevice 后即可使用能量驱动与频谱分段映射：\n"
                "pip install sounddevice",
                "music"))

        audio_lay.addStretch()
        tabs.addTab(audio_tab, "音频")

        return _pcui.finish_controller_ui(self, panel, tabs)

    def _on_spectrum_frame(self, frame, spec_bar, spec_bands_spin, spec_dir_combo,
                            spec_ch_sel, spec_layout_combo=None, spec_offset_spin=None,
                            spec_multicolor_check=None, spec_gain_spin=None,
                            spec_threshold_spin=None, spec_attack_spin=None,
                            spec_release_spin=None):
        """频谱分段映射帧处理。
        ★ v28 新增参数：
          - spec_layout_combo: 分频布局预设（线性/中间低频/两边低频/随机/进阶阶梯）
          - spec_offset_spin:  基线偏移（频段起始偏移量 0-7）
          - spec_multicolor_check: 多色彩色条开关（每个频段独立颜色驱动 RGB）
        """
        if not self._audio_active:
            return
        combo = getattr(self, '_audio_mode_combo_ref', None)
        if combo is None or combo.currentIndex() != 1:
            return
        now_ui = _time.monotonic()
        update_ui = now_ui - float(getattr(self, "_last_spectrum_ui_frame_t", 0.0) or 0.0) >=             SPECTRUM_UI_FRAME_INTERVAL
        if update_ui:
            self._last_spectrum_ui_frame_t = now_ui
        if update_ui and spec_bar is not None:
            try:
                spec_bar.update_frame(frame)
            except Exception:
                pass
        fixtures = _runtime_targets.audio_fixtures(self)
        n_total = len(fixtures)
        if n_total <= 0:
            return
        if n_total >= 1000:
            output_interval = 1.0 / 15.0
        elif n_total >= 540:
            output_interval = 1.0 / 20.0
        elif n_total >= 240:
            output_interval = 1.0 / 25.0
        else:
            output_interval = 1.0 / 35.0
        last_output = float(getattr(self, "_last_spectrum_output_t", 0.0) or 0.0)
        if now_ui - last_output < output_interval:
            return
        self._last_spectrum_output_t = now_ui
        bands = frame.bands  # 8 个频段能量 (0-1)
        # 浮点参数（可能为 widget_kit.NumSlider）读值统一走 _ns_value。
        try:
            gain = _ns_value(spec_gain_spin) if spec_gain_spin is not None else 1.0
        except Exception:
            gain = 1.0
        try:
            threshold = _ns_value(spec_threshold_spin) if spec_threshold_spin is not None else 0.0
        except Exception:
            threshold = 0.0
        try:
            attack_ms = float(spec_attack_spin.value()) if spec_attack_spin is not None else 25.0
        except Exception:
            attack_ms = 25.0
        try:
            release_ms = float(spec_release_spin.value()) if spec_release_spin is not None else 180.0
        except Exception:
            release_ms = 180.0
        gain = max(0.05, min(10.0, gain))
        threshold = max(0.0, min(0.99, threshold))
        attack_ms = max(1.0, min(1000.0, attack_ms))
        release_ms = max(1.0, min(3000.0, release_ms))
        dt = 1.0 / 40.0
        now_env = _time.monotonic()
        last_env = float(getattr(self, "_last_spectrum_env_t", now_env) or now_env)
        if last_env > 0.0:
            dt = max(0.001, min(0.2, now_env - last_env))
        self._last_spectrum_env_t = now_env
        n_bands = spec_bands_spin.value()
        use_x = spec_dir_combo.currentIndex() == 0
        ct_idx = spec_ch_sel.currentIndex()
        ch_type = (self._ch_types[ct_idx]
                   if 0 <= ct_idx < len(self._ch_types)
                   else (self._ch_types[0] if self._ch_types else "dimmer"))
        # 基线偏移：频段起始索引
        offset = spec_offset_spin.value() if spec_offset_spin is not None else 0
        # 多色彩色条模式
        use_multicolor = (spec_multicolor_check is not None
                          and spec_multicolor_check.isChecked())
        # 分频布局预设：计算每个设备位置对应的频段索引映射
        layout_idx = spec_layout_combo.currentIndex() if spec_layout_combo is not None else 0

        def _get_band_src_idx(band_idx: int, n: int, layout: int, off: int) -> int:
            """根据布局预设和偏移，将设备的频段槽位映射到实际频段索引。"""
            # 先应用布局预设
            if layout == 0:  # 线性（左→右）：低频在左
                mapped = band_idx
            elif layout == 1:  # 线性（右→左）：高频在左
                mapped = (n - 1 - band_idx)
            elif layout == 2:  # 中间低频/两边高频
                # 中心位置 band_idx=0 映射到最低频，越往两边频率越高
                center = (n - 1) / 2.0
                dist = abs(band_idx - center) / max(1, center)
                mapped = int(dist * (n - 1))
            elif layout == 3:  # 两边低频/中间高频
                center = (n - 1) / 2.0
                dist = 1.0 - abs(band_idx - center) / max(1, center)
                mapped = int(dist * (n - 1))
            elif layout == 4:  # 随机洗牌
                import random
                if not hasattr(self, '_spec_shuffle_map') or len(self._spec_shuffle_map) != n:
                    self._spec_shuffle_map = list(range(n))
                    random.shuffle(self._spec_shuffle_map)
                mapped = self._spec_shuffle_map[band_idx % n]
            elif layout == 5:  # 进阶阶梯：幂次分布，低频区域更密集
                t = band_idx / max(1, n - 1)
                mapped = int((t ** 0.5) * (n - 1))
            else:
                mapped = band_idx
            if n <= 1:
                src = 0
            else:
                src = int(round(max(0, min(n - 1, mapped)) * 7.0 / max(1, n - 1)))
            return (src + int(off or 0)) % 8

        cache_key = ("x" if use_x else "y", id(fixtures))
        sorted_fis = getattr(self, "_spectrum_sorted_cache", {}).get(cache_key)
        if sorted_fis is None:
            fixtures_with_pos = []
            for _idx, fi in enumerate(fixtures):
                px = getattr(fi, 'pos_x', 0.0)
                py = getattr(fi, 'pos_y', 0.0)
                sort_key = px if use_x else py
                fixtures_with_pos.append((sort_key, _idx, fi))
            fixtures_with_pos.sort(key=lambda t: (t[0], t[1]))
            sorted_fis = [fi for _, _, fi in fixtures_with_pos]
            self._spectrum_sorted_cache[cache_key] = sorted_fis
        n_fix = len(sorted_fis)
        if n_fix == 0:
            return
        batch: Dict[str, Dict[str, int]] = {}
        band_energy: Dict[tuple, float] = {}
        env_cache = getattr(self, "_spectrum_env_by_slot", None)
        if env_cache is None:
            env_cache = {}
            self._spectrum_env_by_slot = env_cache
        for i, fi in enumerate(sorted_fis):
            fd = self._fixture_defs.get(fi.def_id)
            if not fd:
                continue
            band_idx = min(int(i * n_bands / n_fix), n_bands - 1)
            src_idx = _get_band_src_idx(band_idx, n_bands, layout_idx, offset)
            env_key = ("spectrum", int(band_idx), int(src_idx))
            energy = band_energy.get(env_key)
            if energy is None:
                raw_energy = bands[src_idx] if src_idx < len(bands) else 0.0
                energy = (float(raw_energy) * gain - threshold) / max(1e-6, 1.0 - threshold)
                energy = max(0.0, min(1.0, energy))
                prev_env = float(env_cache.get(env_key, 0.0) or 0.0)
                coeff = math.exp(-dt / ((attack_ms if energy >= prev_env else release_ms) / 1000.0))
                energy = max(0.0, min(1.0, prev_env * coeff + energy * (1.0 - coeff)))
                env_cache[env_key] = energy
                band_energy[env_key] = energy

            if use_multicolor:
                # 多色彩色条：每个频段独立颜色，能量驱动该颜色的 RGB 亮度
                band_colors = getattr(self, '_spec_band_colors', None)
                if band_colors and band_idx < len(band_colors):
                    br, bg, bb = band_colors[band_idx]
                else:
                    # 默认彩虹色
                    hue = band_idx / 8.0
                    import colorsys
                    br, bg, bb = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0)]
                scale = energy
                for ct, c0 in [("R", br), ("G", bg), ("B", bb)]:
                    if not _fixture_supports_logical_channel(fd, ct):
                        continue
                    cv = int(c0 * scale)
                    batch.setdefault(fi.id, {})[ct] = max(0, min(255, cv))
            else:
                # 单通道模式（原有逻辑）
                if not _fixture_supports_logical_channel(fd, ch_type):
                    continue
                val = int(energy * 255)
                val = max(0, min(255, val))
                batch.setdefault(fi.id, {})[ch_type] = val
        self._apply_realtime_layer_batch(batch, "audio", refresh_body=False)
    def _on_audio_value(self, ch_type: str, val: int):
        """单通道模式：只输出到用户在驱动通道中选择的通道类型"""
        if not self._audio_active:
            return
        combo = getattr(self, '_audio_mode_combo_ref', None)
        if combo is not None and combo.currentIndex() == 1:
            return
        batch: Dict[str, Dict[str, int]] = {}
        value = max(0, min(255, int(round(float(val)))))
        for fi in _runtime_targets.audio_fixtures(self):
            fd = self._fixture_defs.get(fi.def_id)
            if _fixture_supports_logical_channel(fd, ch_type):
                batch.setdefault(fi.id, {})[ch_type] = value
        self._apply_realtime_layer_batch(batch, "audio", refresh_body=False)
    def _mk_small_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        return lbl

    def _set_expand_arrow(self, expanded: bool, color: str = "") -> None:
        """折叠指示三角：tabler chevron 矢量（禁 ▶▼ 字形）。"""
        pm = None
        try:
            from .main_icon_library import load_tabler_pixmap
            pm = load_tabler_pixmap(
                "chevron-down" if expanded else "chevron-right",
                12, color or "#AAAAAA")
        except Exception:
            pm = None
        if pm is not None and not pm.isNull():
            self._arrow.setPixmap(pm)
        else:
            self._arrow.clear()

    def _toggle(self):
        self._expanded = not self._expanded
        self._set_expand_arrow(self._expanded)
        if self._expanded and getattr(self, "_body_needs_rebuild", False):
            self._rebuild_body()
        self._body.setVisible(self._expanded)

    def _send_joy_amplitude_overrides(self, amp_x: float, amp_y: float, amp_z: Optional[float] = None):
        """
        ★ 幅度控制模式：将摇杆幅度因子发送到 EffectBrowser 的 param_overrides。
        通过 ProgrammerPanel 的引用链找到 EffectBrowser 并注入参数。
        """
        overrides = {}
        x_target = getattr(self, '_joy_amp_x_target', 'joy_amplitude_x')
        y_target = getattr(self, '_joy_amp_y_target', 'joy_amplitude_y')
        z_target = getattr(self, '_joy_amp_z_target', 'joy_amplitude_z')
        if x_target:
            overrides[x_target] = amp_x
        if y_target:
            overrides[y_target] = amp_y
        if z_target and amp_z is not None:
            overrides[z_target] = amp_z
        if not overrides:
            return
        # 通过信号链路将 overrides 传递出去
        # 发射专用信号，由 ProgrammerPanel 接收并转发到 EffectBrowser
        if hasattr(self, 'joy_amplitude_changed'):
            self.joy_amplitude_changed.emit(overrides)

    def _clear_joy_amplitude_overrides(self):
        """清除摇杆幅度控制的 param_overrides。"""
        overrides = {"joy_amplitude_x": None, "joy_amplitude_y": None, "joy_amplitude_z": None}
        if hasattr(self, 'joy_amplitude_changed'):
            self.joy_amplitude_changed.emit(overrides)

    def _convert_joystick_xy(self, x: float, y: float) -> Tuple[float, float]:
        """Convert joystick plane position to the selected two-axis device model."""
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        if getattr(self, "_joy_coord_mode", "cartesian") != "polar":
            return x, y

        dx = x - 0.5
        dy = 0.5 - y
        radius = min(1.0, math.hypot(dx, dy) * 2.0)
        angle = (math.atan2(dy, dx) + math.pi) / (2.0 * math.pi)
        return radius, angle

    def _convert_joystick_xyz(self, x: float, y: float, z: Optional[float] = None) -> Tuple[float, float, float]:
        """Convert current joystick values to logical X/Y/Z output axes."""
        if getattr(self, "_joy_space_mode", "2d") == "3d":
            return (
                max(0.0, min(1.0, float(x))),
                max(0.0, min(1.0, float(y))),
                max(0.0, min(1.0, float(0.5 if z is None else z))),
            )
        out_x, out_y = self._convert_joystick_xy(x, y)
        return out_x, out_y, max(0.0, min(1.0, float(0.5 if z is None else z)))

    @staticmethod
    def _apply_curve_float(raw: float, curve: str, lo: int, hi: int,
                           gamma: float = 1.0, steps: int = 4) -> float:
        t = max(0.0, min(1.0, raw / 255.0))
        if curve == "gamma":
            t = t ** max(0.01, gamma)
        elif curve == "scurve":
            t = t * t * (3.0 - 2.0 * t)
        elif curve == "invert":
            t = 1.0 - t
        elif curve == "step":
            n = max(2, steps)
            t = math.floor(t * n) / (n - 1) if n > 1 else t
            t = max(0.0, min(1.0, t))
        return max(0.0, min(255.0, float(lo) + t * (float(hi) - float(lo))))

    @staticmethod
    def _apply_curve(raw: float, curve: str, lo: int, hi: int,
                     gamma: float = 1.0, steps: int = 4) -> int:
        return int(ChannelTypeRow._apply_curve_float(raw, curve, lo, hi, gamma, steps))

    def _compute_merged_value(self, fixture_id: str, ch_type: str,
                              exclude_layers: Optional[set] = None) -> int:
        LAYER_ORDER = ["fader", "joystick", "audio", "matrix"]
        excluded = set(exclude_layers or ())
        result = 0
        for layer in LAYER_ORDER:
            if layer in excluded:
                continue
            layer_val = self._layers[layer].get(fixture_id, {}).get(ch_type)
            if layer_val is None:
                continue
            mode = self._layer_modes.get(layer, "override")
            if mode == "override":
                result = layer_val
            elif mode == "add":
                result = min(255, result + layer_val)
            elif mode == "multiply":
                result = int(result * layer_val / 255)
            elif mode == "max":
                result = max(result, layer_val)
        return result

    def _ordered_fixtures_for_channel(self, ch_type: str) -> List[Any]:
        """按设备编号（TopView 选中设备的自动排序）生成稳定设备序列，供翼/块和快捷范围使用。

        ★ 2026-08-11 用户定案：方向条带 / \\ < > 的效果只看设备编号排序
        （= TopView 选中顺序，本行 _fixtures 即按该顺序载入），与摆放位置
        （舞台 X/Y）无关——不再按坐标重排，保持 _fixtures 原顺序。
        """
        cache = getattr(self, "_ordered_fixture_cache", None)
        if cache is not None and ch_type in cache:
            return cache[ch_type]
        result = [
            fi for fi in self._fixtures
            if _fixture_supports_logical_channel(self._fixture_defs.get(fi.def_id), ch_type)
        ]
        if cache is not None:
            cache[ch_type] = result
        return result

    def _spatial_factor(self, logical_index: int, total: int, modifiers) -> float:
        """返回当前设备在 Ctrl/Shift 范围拉伸中的插值因子。

        无修饰键时返回 1.0，即传统全选同值。Ctrl 为单方向拉伸：左侧
        保持原值，越向右越接近目标值。Shift 为双方向拉伸：中心保持
        原值，两侧同步接近目标值。
        """
        if total <= 1:
            t = 1.0
        else:
            t = max(0.0, min(1.0, logical_index / float(total - 1)))
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if shift:
            return abs(t * 2.0 - 1.0)
        if ctrl:
            return t
        return 1.0

    def _range_factor(self, logical_index: int, total: int, mode: str) -> float:
        if total <= 1:
            t = 1.0
        else:
            t = max(0.0, min(1.0, logical_index / float(total - 1)))
        if mode == "two_way":
            return abs(t * 2.0 - 1.0)
        if mode == "one_way":
            return wing_direction_factor(
                logical_index,
                total,
                getattr(self, "_value_wing_direction", WING_DIRECTION_LOW_TO_HIGH),
            )
        return 1.0

    def _get_range_state(self, ch_type: str) -> Dict[str, Any]:
        if ch_type not in self._range_states:
            cur = self._get_merged_value(ch_type) if hasattr(self, "_fixtures") else 0
            self._range_states[ch_type] = {"mode": "flat", "base": int(cur), "target": int(cur)}
        return self._range_states[ch_type]

    def _set_range_state(self, ch_type: str, mode: str, base: int, target: int):
        st = self._get_range_state(ch_type)
        st["mode"] = mode if mode in ("flat", "one_way", "two_way") else "flat"
        st["base"] = max(0, min(255, int(base)))
        st["target"] = max(0, min(255, int(target)))
        strip = self._range_strips.get(ch_type)
        if _qobject_alive(strip):
            strip.set_state(st["mode"], st["base"], st["target"])
        elif strip is not None:
            self._range_strips.pop(ch_type, None)
        # ★ 同步推杆/滑块/摇杆Tab中的融合条带控件；先过滤 deleteLater 后的悬空包装对象。
        for _strips_dict in (
            getattr(self, "_ctrl_fader_strips",   {}),
            getattr(self, "_ctrl_vslider_strips", {}),
            getattr(self, "_ctrl_joy_strips",     {}),
        ):
            ctrl_strip = _strips_dict.get(ch_type)
            if _qobject_alive(ctrl_strip) and hasattr(ctrl_strip, "set_state"):
                ctrl_strip.set_state(st["mode"], st["base"], st["target"])
            elif ctrl_strip is not None:
                _strips_dict.pop(ch_type, None)
        _r183_set_range_readout(self, ch_type, st)   # R183 项 13：行尾双读数标签
        wb_lbl = self._wing_block_labels.get(ch_type) if hasattr(self, "_wing_block_labels") else None
        if _qobject_alive(wb_lbl):
            wb_lbl.setText(self._wing_block_label_text())
        elif wb_lbl is not None:
            self._wing_block_labels.pop(ch_type, None)

    def _collapse_package_light_slots(self, slots: list) -> list:
        """灯光条带「包原子」：同包成员共享首个成员的条带插值位置(slot_idx,total)。

        ★ 2026-08-04 用户语义定案:Programmer 灯光条带 = **整体明暗**——包内灯
        永远一起亮暗(绝不做灯级单控);渐变(one_way/two_way)只在包之间展开,
        包内同值(气爆/百变等配灯包拖动亮度 = 整包同进退)。散台 1 台 1 slot
        不受影响。_pkg_of = {成员fid: {id,name}}(_refresh_fixtures 构建,工程级
        稳定);无包时零开销直返。翼/块显式分组之后仍生效(包折叠在其基础上)。
        """
        pkg_of = getattr(self, "_pkg_of", None) or {}
        if not pkg_of:
            return slots
        idx_by_pkg: dict = {}
        out = []
        for fi, slot_idx, slot_total in slots:
            pkg = pkg_of.get(str(getattr(fi, "id", "") or ""))
            if pkg is None:
                out.append((fi, slot_idx, slot_total))
                continue
            key = str(pkg.get("id", "") or "")
            if key not in idx_by_pkg:
                idx_by_pkg[key] = (slot_idx, slot_total)
            out.append((fi,) + idx_by_pkg[key])
        return out

    def _apply_channel_range(self, ch_type: str, mode: str, base: float, target: float, layer: str = "fader"):
        base_f = max(0.0, min(255.0, float(base)))
        target_f = max(0.0, min(255.0, float(target)))
        self._set_range_state(ch_type, mode, int(round(base_f)), int(round(target_f)))
        slots = self._wing_block_distribution_slots(self._ordered_fixtures_for_channel(ch_type))
        if ch_type == "dimmer" or _is_light_channel(ch_type):
            # ★ 包原子(2026-08-04 用户语义):灯光条带(亮度/颜色)包内灯整体明暗,
            #   不做灯级单控。dimmer 是灯光条带主通道(_is_light_channel 仅含色通道)。
            slots = self._collapse_package_light_slots(slots)
        _jit_pct = _r183_prepare_range_write(self, ch_type, slots); self._is_refreshing = True
        batch: Dict[str, Dict[str, int]] = {}
        try:
            layer_map = self._layers[layer]
            # ★ 性能：除被写入层外全部层为空（含 fader——导入后直接拖 dimmer 的
            #   典型场景）且被写入层混合模式非 multiply 时，merged 恒等于本次
            #   写入值（result 初始 0：override→x / add→min(255,0+x)=x /
            #   max→max(0,x)=x；multiply 的 0×x=0 与原路径不同 → 排除，含摇杆
            #   拖动 layer="joystick" 时 joystick 层自身 multiply、以及 fader 层
            #   已有值叠加/最大模式的情形——审查M4+A），跳过逐台 4 层查找
            #   （2107 台 ≈ 3-4ms/事件 → 0）。
            layers_quiet = (
                self._layer_modes.get(layer, "override") != "multiply"
                and not any(
                    self._layers[_l]
                    for _l in ("fader", "joystick", "audio", "matrix")
                    if _l != layer
                )
            )
            for fi, slot_idx, slot_total in slots:
                fid = fi.id
                factor = self._range_factor(slot_idx, slot_total, mode)
                out_f = _ptool.jitter_value(max(0.0, min(255.0, base_f + (target_f - base_f) * factor)),
                                            _jit_pct, _random.uniform)  # ★ 项10：抖动只加本次写入值
                out_value = out_f if ch_type in MOTOR_FINE_CHANNELS else int(round(out_f))
                if fid not in layer_map:
                    layer_map[fid] = {}
                layer_map[fid][ch_type] = out_value
                if _is_light_channel(ch_type):
                    self._bump_vd_color_version()
                if layers_quiet:
                    # fader 为唯一有值层(override 模式) → merged == 本次写入值
                    # （恒 int(round)，与 _compute_merged_value 路径的类型逐位一致）
                    merged = int(round(out_value))
                else:
                    merged = int(round(self._compute_merged_value(fid, ch_type)))
                # ★ 性能：合并值未变化时跳过 _values 重复写入（状态逐字节相同；
                #   _marked 保留无条件写入——它是"用户已动过"标记，CUE 快照依赖其存在）。
                cur_fid_vals = self._values.get(fid)
                if cur_fid_vals is None:
                    cur_fid_vals = self._values[fid] = {}
                if cur_fid_vals.get(ch_type) != merged:
                    cur_fid_vals[ch_type] = merged
                    if self._expanded and self._body.isVisible():
                        self._refresh_body_cell(fid, ch_type, merged)
                _mark_cell_edited(self, fid, ch_type, merged)
                batch.setdefault(fid, {})[ch_type] = out_value
            self.value_changed.emit(ch_type, int(round(target_f)))
        finally:
            self._is_refreshing = False
        emit_range_batch_status(self, batch, layer, ch_type)
        # ★ 同步模式 Z 组命令（2026-08-04 用户需求「拖 Z 滑块全组世界扇面同角」）：
        #   motor_z 手动值 ≠ 中心 → 广播统一世界扇面角（128=0°，±180° 全量程）到
        #   全部同步设备，引擎按每台自己 Z 范围映射 DMX → 世界扇面同角（修复
        #   「只写当前拖动设备 → 扇面分块」）；回到中心 128（±1 死区，摇杆中心
        #   127.5 亦覆盖）→ 发 None 清除命令，回退扇面角度/全局角基准。
        #   面板侧过滤非同步行（普通单控不受影响）。仅 motor_z 粗通道驱动命令，
        #   motor_z_fine 微调不改变统一目标角。
        if ch_type == "motor_z" and slots:
            if abs(target_f - LOGICAL_CENTER) < 1.0:
                z_cmd = None
            else:
                z_cmd = logical_z_to_world_angle(target_f)
            self.sync_z_command_changed.emit(z_cmd, [fi.id for fi, _s, _t in slots])

    def _emit_traj_overrides(self, prefix: str, overrides: dict) -> None:
        """把 {param_key: value} 型事件按 key 展开成独立轨迹（摇杆幅度等）。"""
        _emit_traj_overrides_to(self.controlTrajectoryEvent, prefix, overrides)

    def _on_range_strip_changed(self, ch_type: str, mode: str, base: int, target: int):
        if getattr(self, "_is_refreshing", False):
            return
        try:
            self.controlTrajectoryEvent.emit(
                f"programmer|range|{ch_type}", (str(mode), float(base), float(target)))
        except Exception:
            pass
        # ★ 性能：条带拖动每像素一发事件（2107 台行内写值 13-17ms/事件）——
        #   33ms 窗口合并，最终值由最后一次事件携带、timer 触发时应用，不丢。
        now = _time.monotonic()
        self._pending_strip = (ch_type, mode, base, target)
        if now - float(getattr(self, "_last_strip_t", 0.0) or 0.0) < 0.033:
            if not self._strip_timer.isActive():
                self._strip_timer.start()
            return
        self._last_strip_t = now
        self._apply_channel_range(ch_type, mode, base, target, layer="fader")

    def _process_pending_strip(self):
        """节流窗口结束：应用最后一次条带事件（值不丢）。"""
        pm = self._pending_strip
        if pm is None:
            return
        if getattr(self, "_is_refreshing", False):
            # ★ 正确性(审查L1)：与 master 路径一致——刷新期(还原优先)丢弃
            #   窗口内残留事件，避免还原值被还原前的旧事件覆盖。
            self._pending_strip = None
            return
        self._pending_strip = None
        self._on_range_strip_changed(pm[0], pm[1], pm[2], pm[3])

    def _apply_wing_block_order(self, fixtures: List[Any]) -> List[Any]:
        """按块大小和翼数量重排逻辑序列；默认 1/1 时保持原序。"""
        return [fi for fi, _, _ in self._wing_block_distribution_slots(fixtures)]

    def _wing_block_distribution_slots(self, fixtures: List[Any]) -> List[Tuple[Any, int, int]]:
        """返回 (fixture, 逻辑块索引, 逻辑块总数)。

        “块”仍然表示相邻 block_size 台设备共用同一个值；“翼”表示把块序列
        切成 N 段后，每一段都从 0 开始跑同一套逻辑索引。也就是说，2 翼会
        生成两把完全一致、同步打开的扇子。
        """
        fixtures = list(fixtures or [])
        if not fixtures:
            return []
        block_size = max(1, int(getattr(self, "_value_block_size", 1)))
        wings = max(1, int(getattr(self, "_value_wings", 1)))
        if (block_size == 1 and wings == 1
                and not getattr(self, "_value_wing_mirror", False)
                and not getattr(self, "_value_wing_reverse", False)):
            # ★ 性能：默认 1翼1块时单位即单台设备（direction=low_to_high → 恒等序），
            #   拖动热路径每事件调用，跳过 build_wing_block_units 的列表/块分配。
            #   结果与通用路径逐项一致：(fi, 逻辑下标, 总数)。
            _n = len(fixtures)
            return [(fi, i, _n) for i, fi in enumerate(fixtures)]
        return wing_block_distribution_slots(
            fixtures,
            wings=wings,
            block_size=block_size,
            mirror=bool(getattr(self, "_value_wing_mirror", False)),
            reverse=bool(getattr(self, "_value_wing_reverse", False)),
        )

    def _on_master_changed(self, ch_type: str, value: float, layer: str = "fader"):
        if getattr(self, "_is_refreshing", False):
            return
        value_f = max(0.0, min(255.0, float(value)))
        value_i = int(round(value_f))
        if hasattr(self, "_val_label"):
            self._val_label.setText(_ptool.format_value_dual(value_i))  # R183 项 13
        # ★ 性能：滑块/屏上摇杆拖动事件可达 1kHz（每像素一发），2107 台行内写值
        #   每事件 13-17ms——33ms 窗口合并（输出本就由 33ms flush 决定，视觉无感；
        #   最终值由最后一次事件携带、timer 触发时应用，不丢）。
        now = _time.monotonic()
        self._pending_master[(ch_type, layer)] = (ch_type, value_f, layer)
        if now - float(getattr(self, "_last_master_t", 0.0) or 0.0) < 0.033:
            if not self._master_timer.isActive():
                self._master_timer.start()
            return
        self._last_master_t = now
        self._pending_master.pop((ch_type, layer), None)  # 防节流窗口回放覆盖新值
        modifiers = QApplication.keyboardModifiers() if 'QApplication' in globals() else Qt.KeyboardModifier.NoModifier
        st = self._get_range_state(ch_type)
        # ★ 不做同值去重：NM 层刻意保留"同值触达"（回碰推杆夺权），UI 层吞掉会破坏该语义
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift: 双边开合，base固定，target随拖动变化
            mode = "two_way"
            base = float(st.get("base", self._get_merged_value(ch_type)))
            target = value_f
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl: 单边开合，base固定，target随拖动变化
            mode = "one_way"
            base = float(st.get("base", self._get_merged_value(ch_type)))
            target = value_f
        else:
            # 无修饰键: 整体平移，保持已有 base-target 差值
            old_base   = float(st.get("base",   value_f))
            old_target = float(st.get("target", value_f))
            old_mode   = st.get("mode", "flat")
            if old_mode == "flat":
                # flat 模式直接更新 target
                mode   = "flat"
                base   = value_f
                target = value_f
            else:
                # 有范围时整体平移
                delta  = value_f - old_target
                lo = min(old_base, old_target)
                hi = max(old_base, old_target)
                if lo + delta < 0:   delta = -lo
                if hi + delta > 255: delta = 255 - hi
                mode   = old_mode
                base   = old_base   + delta
                target = old_target + delta
        self._apply_channel_range(ch_type, mode, base, target, layer=layer)
        self._update_mode_checks(ch_type)

    def _process_pending_master(self):
        """节流窗口结束：应用各槽最后一次主控件事件（值不丢）。"""
        if not self._pending_master:
            return
        if getattr(self, "_is_refreshing", False):
            # CUE 调入/条带还原期间：与旧行为一致丢弃窗口内残留事件
            #   （还原优先于还原前的旧事件，审查发现5确认旧语义）。
            self._pending_master.clear()
            return
        pending = self._pending_master
        self._pending_master = {}
        for _k, pm in pending.items():
            # ★ 轮内逐条复位共享节流时钟：首条(motor_x)应用会刷新 _last_master_t，
            #   后续条(motor_y/motor_z)若不复位将再次命中 <0.033 节流 → 重新排队
            #   → Y/Z 永远饿死、X 每 33ms 无限重复下发（33ms livelock，审查 R1；
            #   对齐 _on_mode_clicked 既有先例 _last_master_t = 0.0）。
            self._last_master_t = 0.0
            self._on_master_changed(pm[0], pm[1], pm[2])

    def _on_mode_clicked(self, ch_type: str, value: int):
        """功能模式按钮：把模式对应 DMX 值快速写入本通道（不影响其它功能）。

        ★ 性能：按钮=离散即时意图，绕过节流窗口直接应用（审查发现4）。
        """
        self._last_master_t = 0.0
        self._pending_master.pop((ch_type, "fader"), None)
        self._on_master_changed(ch_type, float(value))
        self._update_mode_checks(ch_type)

    def set_pkg_arrange(self, on: bool, pkg_of: dict):
        """包排列模式（纯展示整理）：包内子设备按文件夹嵌套；不改变任何通道逻辑。"""
        on = bool(on)
        pkg_of = pkg_of or {}
        # ★ 性能：包状态未变直接跳过（组切换时每行都被调用，重建 body ≈ 1-3ms/行）。
        #   视觉结果完全一致：包状态与重建前相同，body 无需刷新。
        if self._pkg_arrange == on and self._pkg_of is pkg_of:
            return
        self._pkg_arrange = on
        self._pkg_of = pkg_of
        if (getattr(self, "_expanded", False)
                or (getattr(self, "_body", None) is not None and self._body.isVisible())):
            self._rebuild_body()
        else:
            self._body_needs_rebuild = True

    def _update_mode_checks(self, ch_type: str):
        """按当前通道均值高亮命中的模式按钮（值落在该模式的 DMX 区间内）。"""
        btns = self._mode_buttons.get(ch_type) or []
        if not btns:
            return
        # ★ 性能：纯显示高亮节流到 150ms——拖动/摇杆每事件调用时跳过
        #   _get_merged_value 的 O(N) 全行均值遍历（load_fixtures 已重置计时）。
        now = _time.monotonic()
        if now - float(getattr(self, "_last_mode_check_t", 0.0) or 0.0) < 0.15:
            return
        self._last_mode_check_t = now
        cur = self._get_merged_value(ch_type)
        hit = None
        for _b, _m in zip(btns, self._modes_by_ch.get(ch_type, []) or []):
            lo = int(_m.get("dmx_min", 0) or 0)
            hi = int(_m.get("dmx_max", 255) or 255)
            if lo <= cur <= hi:
                hit = _b
        for _b in btns:
            if _b.isChecked() is not (_b is hit):
                _b.setChecked(_b is hit)

    def _pick_color(self):
        r = self._get_merged_value("R")
        g = self._get_merged_value("G")
        b = self._get_merged_value("B")
        init_color = QColor(r, g, b)
        color = QColorDialog.getColor(init_color, self, "选择颜色")
        if color.isValid():
            for ct, val in [("R", color.red()), ("G", color.green()),
                            ("B", color.blue())]:
                for fi in self._fixtures:
                    fd = self._fixture_defs.get(fi.def_id)
                    if _fixture_supports_logical_channel(fd, ct):
                        self._set_value(fi.id, ct, val)
                        self.value_changed.emit(ct, val)
            self._update_color_preview()

    def _get_merged_value(self, ch_type: str) -> int:
        vals = [self._values.get(fi.id, {}).get(ch_type, 0)
                for fi in self._fixtures]
        return int(sum(vals) / len(vals)) if vals else 0

    def _update_color_preview(self):
        if hasattr(self, "_color_preview"):
            r = self._get_merged_value("R")
            g = self._get_merged_value("G")
            b = self._get_merged_value("B")
            self._color_preview.setStyleSheet(
                f"background:rgb({r},{g},{b});"
                "border:1px solid #3D3D3D;border-radius:3px;")

    def _set_layer_value(self, fixture_id: str, ch_type: str, value: int,
                         layer: str = "fader", emit_value: bool = True,
                         emit_layer: bool = True, refresh_body: bool = True) -> int:
        layer = layer if layer in self._layers else "fader"
        value = max(0, min(255, int(round(float(value)))))
        old_layer_val = self._layers.setdefault(layer, {}).setdefault(fixture_id, {}).get(ch_type)
        if old_layer_val == value and not (emit_value or emit_layer):
            return int(round(self._compute_merged_value(fixture_id, ch_type)))
        self._layers[layer][fixture_id][ch_type] = value
        if _is_light_channel(ch_type):
            self._bump_vd_color_version()
        merged = int(round(self._compute_merged_value(fixture_id, ch_type)))
        if fixture_id not in self._values:
            self._values[fixture_id] = {}
        self._values[fixture_id][ch_type] = merged
        _mark_cell_edited(self, fixture_id, ch_type, merged)
        if refresh_body and self._expanded and self._body.isVisible():
            self._refresh_body_cell(fixture_id, ch_type, merged)
        if emit_value:
            self.fixture_value_changed.emit(fixture_id, ch_type, merged)
        if emit_layer:
            self.fixture_layer_value_changed.emit(fixture_id, ch_type, value, layer)
        return merged

    # ── 调度台表格视图接口（2026-09-13）───────────────────────────────────
    #  ★ 为什么是"窄接口"：本行是调度台格子值的**唯一真源**（`_layers/_values/
    #    `_marked`）。调度台（`ui/dispatch/cell_ops.py`）需要按"设备×通道"读/写/
    #    清/标记，但不得触碰 `_layers` 内部结构（那会把调度台绑死到本行实现，
    #    改一处崩两处）。故只暴露这三个最小动作，内部仍走既有 `_set_layer_value`
    #    路径（保证 pending/flush/keepalive/录制链路一致，双方不打架）。
    def set_cell_value(self, fixture_id: str, ch_type: str, value: int,
                       layer: str = "fader") -> int:
        """调度台设值：写值 + 更新 `_values`/`_marked` + 触发出光（既有全链路）。"""
        return self._set_layer_value(str(fixture_id), str(ch_type), int(value),
                                     layer=str(layer))

    def unmark_cell(self, fixture_id: str, ch_type: str) -> bool:
        """取消该格的 S 标记（**只清标记，不动值**）。

        ★ 为什么需要：本行原先只能整体 `clear_values()`，没有"取消单格标记"的能力，
          而 的表格明确要求"取消标记"是独立动作（值仍在、只是不再算作要存的）。
          返回是否真的清掉了一项（供调用方统计）。
        """
        fid = str(fixture_id)
        ct = str(ch_type)
        marked = self._marked.get(fid)
        if not marked or ct not in marked:
            return False
        marked.pop(ct, None)
        if not marked:
            self._marked.pop(fid, None)
        return True

    def clear_cell_store(self, fixture_id: str, ch_type: str) -> bool:
        """清掉本行缓存的该格（四层 + `_values` + `_marked`）。

        ★ 与 `unmark_cell` 的区别：这是"该格不再存在"（release），必须连
          `_layers` 一起清 —— 否则 keepalive / `_rebuild_live_layer_caches_from_rows`
          会把旧值重新灌回 NetworkManager（历史上"release 后监视器不变"的根因）。
          NetworkManager 侧的 `active=False` 由调用方（cell_ops）负责。
        """
        fid = str(fixture_id)
        ct = str(ch_type)
        changed = _drop_row_cell_caches(self, fid, ct)
        if changed:
            self._bump_vd_color_version()
            # ★★ 2026-09-17 深查修正（"调度台清空格被 keepalive 复活"）：本行缓存
            #   清了，但 keepalive 读的是**面板级** `_layer_sparse_cache`（地址键）
            #   —— 不清它，用户下次动任何控件触发版本递增重发时，这个格的旧值会
            #   连同 active 掩码一起写回 NM（实测：清空 dimmer=180 后，动另一格，
            #   100ms 内该地址又变回 180）。
            _forget_panel_cell_cache(self, fid, ct)
        return changed

    def _compute_scene_value(self, fixture_id: str, ch_type: str,
                             exclude_layers: Optional[set] = None) -> int:
        """Read a channel value from the row that owns it, so dimmer audio can use Color row bases."""
        if ch_type in self._ch_types:
            return self._compute_merged_value(fixture_id, ch_type, exclude_layers=exclude_layers)
        owner = getattr(self, "_owner_list", None)
        fid_s = str(fixture_id)
        # 类型分区后同一 ct 有多行（每设备类型一行）：优先按 fid 匹配拥有该设备的行
        for row in getattr(owner, "_rows", []) or []:
            if row is self:
                continue
            if ch_type in getattr(row, "_ch_types", []) and \
                    fid_s in getattr(row, "_fixture_id_set", set()):
                return row._compute_merged_value(fixture_id, ch_type, exclude_layers=exclude_layers)
        for row in getattr(owner, "_rows", []) or []:
            if row is self:
                continue
            if ch_type in getattr(row, "_ch_types", []):
                return row._compute_merged_value(fixture_id, ch_type, exclude_layers=exclude_layers)
        return self._compute_merged_value(fixture_id, ch_type, exclude_layers=exclude_layers)

    def _apply_realtime_layer_batch(self, batch: dict, layer: str = "audio",
                                    refresh_body: bool = False) -> dict:
        """Apply high-rate audio/spectrum values without repainting every fixture cell."""
        if not batch:
            return {}
        layer = layer if layer in self._layers else "audio"
        fixture_id_set = _runtime_targets.realtime_fixture_ids(self, layer)
        changed: Dict[str, Dict[str, int]] = {}
        external_driver, layer_map = _is_external_driver_layer(layer), self._layers.setdefault(layer, {})
        for fid, channels in (batch or {}).items():
            if fid not in fixture_id_set:
                continue
            dst = layer_map.setdefault(fid, {})
            for ch_type, raw_val in (channels or {}).items():
                try:
                    val = max(0, min(255, int(round(float(raw_val)))))
                except Exception:
                    continue
                if dst.get(ch_type) == val:
                    continue
                dst[ch_type] = val
                if _is_light_channel(ch_type):
                    self._bump_vd_color_version()
                merged = int(round(self._compute_merged_value(fid, ch_type)))
                self._values.setdefault(fid, {})[ch_type] = merged
                _record_layer_cell(self, fid, ch_type, merged, external_driver)
                if refresh_body and self._expanded and self._body.isVisible():
                    self._refresh_body_cell(fid, ch_type, merged)
                changed.setdefault(fid, {})[ch_type] = val
        if changed:
            self.fixture_layer_batch_changed.emit(changed, layer)
            if "R" in self._ch_types:
                now = _time.monotonic()
                if now - float(getattr(self, "_last_audio_preview_t", 0.0) or 0.0) >= 0.10:
                    self._last_audio_preview_t = now
                    self._update_color_preview()
        return changed

    def _set_value(self, fixture_id: str, ch_type: str, value: int):
        self._set_layer_value(fixture_id, ch_type, value, "fader")

    def _refresh_body_cell(self, fixture_id: str, ch_type: str, value: int):
        w = getattr(self, "_body_cell_widgets", {}).get((fixture_id, ch_type))
        if _qobject_alive(w) and hasattr(w, "_val_spin") and _qobject_alive(w._val_spin):
            w._val_spin.blockSignals(True)
            try:
                w._val_spin.setValue(value)
            finally:
                w._val_spin.blockSignals(False)
            return
        if w is not None:
            self._body_cell_widgets.pop((fixture_id, ch_type), None)
        # 逐台设备页面已移除（用户定案 2026-08-02）：权威值始终在 _values/_layers，
        # 显示层不再有逐台控件，此处仅清理可能残留的单元格缓存，不做任何 UI 构建。

    def _bump_vd_color_version(self):
        """颜色通道（R/G/B/W）写入后递增版本号，失效 ProgrammerPanel 的虚拟
        亮度基色采样缓存（采样只读颜色通道；dimmer 等非色通道写入不触发）。"""
        lst = getattr(self, "_owner_list", None)
        if lst is not None:
            lst._vd_color_version = getattr(lst, "_vd_color_version", 0) + 1

    def _iter_defs(self):
        """当前行设备的去重 def 对象迭代器（设备能力全部由 def 决定）。

        ★ 性能：行内设备共享同一 def（按 def_id 分区），去重后通常只有 1 个，
        避免所有「设备是否支持某通道」判定退化为逐台扫描。
        """
        seen = set()
        for fi in self._fixtures:
            fd = self._fixture_defs.get(fi.def_id)
            if fd is None or id(fd) in seen:
                continue
            seen.add(id(fd))
            yield fd

    def load_fixtures(self, fixtures, fixture_defs: dict):
        new_fixtures = list(fixtures)
        # ★ 性能：同一类型容器内所有行收到的是同一个 dfixtures 列表对象，签名按 (列表对象,
        #   defs对象) 记忆化——组切换时一个类型只构建一次，不再每行重复 getattr。
        # ★ 正确性(审查H1)：value 持有列表/defs 引用 + 命中身份校验——旧列表释放后新列表
        #   复用其 id 时不得返回旧组签名（否则编辑打到上一组错误设备）；容量上限防增长。
        sig_key = (id(fixtures), id(fixture_defs))
        cached = self._sig_cache.get(sig_key)
        if cached is not None and cached[0] is fixtures and cached[1] is fixture_defs:
            new_sig = cached[2]
        else:
            new_sig = tuple(
                (
                    getattr(f, "id", ""),
                    getattr(f, "name", ""),
                    getattr(f, "def_id", ""),
                    int(getattr(f, "universe", 1) or 1),
                    int(getattr(f, "start_channel", 1) or 1),
                    tuple(getattr(fixture_defs.get(getattr(f, "def_id", "")), "channels", []) or []),
                )
                for f in new_fixtures
            )
            # ★ 性能（R173 审查）：容量满时逐出最旧一条而非整表清空，防击穿其它有效缓存
            bounded_put(self._sig_cache, sig_key, (fixtures, fixture_defs, new_sig), 8)
        if new_sig == self._fixture_signature and fixture_defs is self._fixture_defs:
            return
        _stash_range_states(self)     # ★ R141：切组前按旧签名存档条带形状
        self._fixtures = new_fixtures
        self._fixture_signature = new_sig
        self._fixture_defs = fixture_defs
        self._fixture_id_set = {f.id for f in self._fixtures}
        # 组切换 → 条带回到 flat：防上一组渐变残留（最左设备恒 0）
        self._range_states = {}
        _restore_range_states_for_group(self)   # ★ R141：取回本组形状（首次进组仍空）
        # ★ 性能：组切换后立即允许 _update_mode_checks 刷新一次高亮
        self._last_mode_check_t = 0.0
        self._bump_vd_color_version()
        # 条带常显：重置后同步条带控件与数值标签回 flat（行收起时条带仍可见）
        for _ct, _strip in self._range_strips.items():
            _st = self._get_range_state(_ct)
            _strip.set_state(_st.get("mode", "flat"), _st.get("base", 0), _st.get("target", 0))
            _lbl = self._range_value_labels.get(_ct)
            if _lbl is not None and _qobject_alive(_lbl):
                _lbl.setText(_ptool.format_value_dual(_st.get("target", 0)))
        self._ordered_fixture_cache.clear()
        if not self._audio_active:
            self._spectrum_sorted_cache.clear()
            self._spectrum_env_by_slot.clear()
        # 收集本行通道类型的 功能模式表（首个有模式的设备类型为准）
        new_modes_by_ch = {}
        for _fd in (fixture_defs or {}).values():
            for _ct, _modes in (getattr(_fd, "channel_modes", {}) or {}).items():
                if _ct in self._ch_types and _modes and _ct not in new_modes_by_ch:
                    new_modes_by_ch[_ct] = list(_modes)
        # ★ 性能：body 内容只取决于 def 级信息（模式条/通道头/摇杆，不含逐台 widget）+ _ch_types
        #   (固定)。切组时 def 未变 → body 依然有效：展开态下仅当模式表变化才重建、折叠态下仅当
        #   模式表变化才标脏（新建行 _body_needs_rebuild=True 与 restore/clear 路径已置脏）。
        modes_changed = new_modes_by_ch != getattr(self, "_modes_by_ch", {})
        self._modes_by_ch = new_modes_by_ch
        if self._expanded or self._body.isVisible():
            if modes_changed:
                self._rebuild_body()
        elif modes_changed:
            self._body_needs_rebuild = True

    def apply_batch_values(self, dmx_data: dict, emit_signals: bool = True, refresh_body: bool = True):
        changed = False
        fixture_id_set = getattr(self, '_fixture_id_set', None)
        if fixture_id_set is None:
            fixture_id_set = {f.id for f in self._fixtures}
            self._fixture_id_set = fixture_id_set
        for fid, channels in dmx_data.items():
            if fid in fixture_id_set:
                for ch_type, val in channels.items():
                    if ch_type in self._ch_types:
                        val = min(255, max(0, int(val)))
                        old_layer_val = self._layers.get("matrix", {}).get(fid, {}).get(ch_type)
                        old_merged = self._values.get(fid, {}).get(ch_type)
                        if old_layer_val == val:
                            continue
                        if fid not in self._layers["matrix"]:
                            self._layers["matrix"][fid] = {}
                        self._layers["matrix"][fid][ch_type] = val
                        if _is_light_channel(ch_type):
                            self._bump_vd_color_version()
                        merged = self._compute_merged_value(fid, ch_type)
                        if old_merged == merged:
                            continue
                        if fid not in self._values:
                            self._values[fid] = {}
                        self._values[fid][ch_type] = merged
                        # 矩阵是自动驱动 → 只记外部残留，不记 `_marked`（见写侧收口）。
                        _note_external_cell(self, fid, ch_type)
                        if emit_signals:
                            self.fixture_value_changed.emit(fid, ch_type, merged)
                            self.fixture_layer_value_changed.emit(fid, ch_type, val, "matrix")
                        changed = True
        if changed:
            if "R" in self._ch_types:
                self._update_color_preview()
            if refresh_body and self._expanded:
                for fid, channels in dmx_data.items():
                    for ch_type, val in channels.items():
                        merged_v = self._values.get(fid, {}).get(ch_type, val)
                        self._refresh_body_cell(fid, ch_type, merged_v)

    def _rebuild_body(self):
        self._body_needs_rebuild = False
        self._body_cell_widgets = {}
        self._mode_buttons = {}
        # ★ 用户定案（2026-08-02）：不再构建逐台设备行（大组卡顿源已移除），
        #   无分块/包排列状态；合并编辑由常显条带承担。
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_any = False
        # ★ 性能：设备能力只取决于 def，行内设备共享 def → 按 def 去重检查，
        #   避免对整组设备（最多 2100+ 台）× 通道全量扫描（实测展开占 69ms）。
        for fd in self._iter_defs():
            if any(_fixture_supports_logical_channel(fd, ct) for ct in self._ch_types):
                has_any = True
                break

        if not has_any:
            lbl = QLabel("（当前设备组无此类通道）")
            lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;padding:4px;")
            self._body_lay.addWidget(lbl)
            return

        for ct in self._ch_types:
            c = _ch_color(ct)
            ct_label = _ch_label(ct)

            ct_header = QLabel(f"  {ct_label}")
            ct_header.setStyleSheet(
                f"color:{c};font-size:10px;font-weight:bold;"
                f"background:{_hex_alpha(c, '11')};padding:2px 4px;border-left:2px solid {c};")
            self._body_lay.addWidget(ct_header)

            # ★ 功能模式条：横排方块按钮，点击=把该模式对应的 DMX 值快速写入
            #   本通道（等效于拖动滑块到该值，不影响其它功能）。随通道展开/收起。
            modes = self._modes_by_ch.get(ct) or []
            if modes:
                mode_row = QWidget()
                mode_row.setStyleSheet(f"background:{BG_PANEL};")
                mode_lay = QHBoxLayout(mode_row)
                mode_lay.setContentsMargins(16, 3, 8, 3)
                mode_lay.setSpacing(4)
                mode_lay.addWidget(self._mk_small_label("模式:"))
                mode_btns = []
                for _m in modes:
                    _mname = str(_m.get("name", "?"))
                    _mval = (int(_m.get("dmx_min", 0) or 0) + int(_m.get("dmx_max", 255) or 255)) // 2
                    _b = QPushButton(_mname)
                    _b.setCheckable(True)
                    _b.setFixedHeight(26)
                    _b.setCursor(Qt.CursorShape.PointingHandCursor)
                    _b.setToolTip(f"{_mname}：DMX {_m.get('dmx_min')}-{_m.get('dmx_max')}（点击快速写入 {_mval}）")
                    # 模式方块：checked 命中高亮统一 select_blue 白字（外观其余部分走全局）
                    _b.setStyleSheet("QPushButton{font-size:10px;padding:0 8px;}")
                    _b.clicked.connect(
                        lambda _c=False, _ct=ct, _v=_mval: self._on_mode_clicked(_ct, _v))
                    mode_lay.addWidget(_b)
                    mode_btns.append(_b)
                mode_lay.addStretch()
                self._body_lay.addWidget(mode_row)
                self._mode_buttons[ct] = mode_btns
                self._update_mode_checks(ct)

            # ★ 用户定案（2026-08-02）：展开不再显示逐台设备页面（大组构建卡顿源）。
            #   合并编辑由 header 下方常显条带承担；此处只保留模式条（功能模式按钮）。

        if "motor_x" in self._ch_types and "motor_y" in self._ch_types:
            self._add_xy_joystick()

    def _add_xy_joystick(self):
        joy_row = QWidget()
        scope_qss(joy_row, f"background:{BG_PANEL};padding:8px;")
        joy_lay = QHBoxLayout(joy_row)
        joy_lay.setContentsMargins(16, 8, 8, 8)
        joy_lay.setSpacing(12)

        lbl = QLabel("XYZ 摇杆")
        lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        joy_lay.addWidget(lbl)

        self._joystick = XYJoystick()
        self._joystick.set_mode(getattr(self, "_joy_space_mode", "2d"))
        self._joystick.position_changed.connect(self._on_xy_changed)
        self._joystick.position3d_changed.connect(self._on_xy_changed)
        joy_lay.addWidget(self._joystick)

        mode_combo = QComboBox()
        mode_combo.addItems(["二维模式", "三维模式"])
        mode_combo.setCurrentText("三维模式" if getattr(self, "_joy_space_mode", "2d") == "3d" else "二维模式")
        mode_combo.setFixedWidth(92)
        # 通用下拉框外观（含弹层选中色）由全局 QSS 提供，删除本页重复定义
        def _on_body_mode(text):
            self._joy_space_mode = "3d" if text == "三维模式" else "2d"
            self._joystick.set_mode(self._joy_space_mode)
            if self._joy_space_mode != "3d":
                self._clear_joy_amplitude_overrides()
        mode_combo.currentTextChanged.connect(_on_body_mode)
        joy_lay.addWidget(mode_combo)

        spring_chk = QCheckBox("弹性回中")
        spring_chk.setChecked(False)
        spring_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        spring_chk.toggled.connect(self._joystick.set_spring)
        joy_lay.addWidget(spring_chk)
        _r183_add_joystick_record(self, joy_lay, self)   # R183 项 12：录制路径钮
        joy_lay.addStretch()
        self._body_lay.addWidget(joy_row)

    def inject_external_joystick(self, x: float, y: float, z: Optional[float] = None):
        """从外部（实体摇杆驱动器）注入轴值。
        x, y, z 均为 [0, 1] 归一化值（0.5 为中心）。旧外部摇杆只传 X/Y 时 Z 保持中心。
        ★ 放缩：中位对称输出 = 中位 + (原始 − 中位) × 比例（与绑定路由 _route_axis_values 一致）。
        """
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        z_val = max(0.0, min(1.0, 0.5 if z is None else z))
        try:
            from .joystick_control_binding import get_joystick_bind_manager
            mgr = get_joystick_bind_manager()
            x = 0.5 + (x - 0.5) * mgr.get_axis_scale("x")
            y = 0.5 + (y - 0.5) * mgr.get_axis_scale("y")
            z_val = 0.5 + (z_val - 0.5) * mgr.get_axis_scale("z")
        except Exception:
            pass
        if hasattr(self, '_joystick'):
            if getattr(self, "_joy_space_mode", "2d") == "3d" and hasattr(self._joystick, "set_position3d"):
                self._joystick.set_position3d(x, y, z_val)
            else:
                self._joystick.set_position(x, y)
        self._on_xy_changed(x, y, z_val)

    def _on_xy_changed(self, x: float, y: float, z: float = 0.5):
        try:
            self.controlTrajectoryEvent.emit(
                "programmer|joystick|xyz", (float(x), float(y), float(z)))
        except Exception:
            pass
        if getattr(self, '_joy_mode', 'additive') == "amplitude":
            # ★ 幅度控制模式：XY 摇杆也走幅度控制路径
            amp_x = (x - 0.5) * 2.0
            amp_y = (y - 0.5) * 2.0
            amp_z = (z - 0.5) * 2.0
            self._send_joy_amplitude_overrides(amp_x, amp_y, amp_z)
        else:
            # 叠加模式：原始逻辑
            out_x, out_y, out_z = self._convert_joystick_xyz(x, y, z)
            x_val = out_x * 255.0
            y_val = out_y * 255.0
            z_val = out_z * 255.0
            if "motor_x" in self._ch_types:
                self._on_master_changed("motor_x", x_val, layer="joystick")
            if "motor_y" in self._ch_types:
                self._on_master_changed("motor_y", y_val, layer="joystick")
            if getattr(self, "_joy_space_mode", "2d") == "3d" and "motor_z" in self._ch_types:
                self._on_master_changed("motor_z", z_val, layer="joystick")

    def get_values(self) -> Dict[str, Dict[str, int]]:
        result = {k: dict(v) for k, v in self._values.items()}
        for fid, channels in (self._layers.get("fader", {}) or {}).items():
            if fid not in result:
                result[fid] = {}
            for ch_type, value in channels.items():
                if ch_type in self._ch_types:
                    result[fid][ch_type] = value
        return result

    def get_marked_values(self, *, include_detached: bool = True) -> Dict[str, Dict[str, int]]:
        """用户**真正编辑过**的通道值（`_marked` ∪ "层里偏离默认值"的部分）。

        ★★ R138 用户实报修复（"存效果的时候会把当前并没有在编辑的设备
          （因为有默认值 128 等等）混入当前的效果"）：
          原实现把 `_layers['fader']` 里**所有**值都并进结果 —— 而 fader 层里存着
          每台设备的**默认负载**（电机默认归中 128、泵默认 0 等，由
          `load_fixtures` 装载时写入）。于是"从没被用户碰过"的设备也带上
          `{motor_x: 128, pump: 0}` 进了快照 → 场景/效果作用域被撑成整组
          （实测：清空 `_marked` 后仍返回 16 台，期望 0）。

          修：层值只有**偏离该通道默认值**时才算"被编辑过"。
          默认值口径见 `_channel_default_255`（fixture def 的
          `function_channel_defaults`，电机=128、功能通道=其配置值、灯=0）。
          这样"手动把电机推回 128"仍算编辑（`_marked` 里有它 —— 用户真动过），
          而"从没动过、只是默认值躺着"不会被算进来（零误报方向）。
        """
        result = _row_marked_values(self, include_detached)
        result = {fid: channels for fid, channels in result.items() if channels}
        _merge_nondefault_fader_values(self, result, include_detached)
        return result

    def _channel_default_255(self, fid: str, ch_type: str) -> int:
        """该设备该通道的**默认值**（0-255）。查不到回落 0；电机回落 128。

        ★ R138：`get_marked_values` 用它区分"用户编辑过"与"只是默认值躺着"。
          电机类通道的"默认"是**归中 128**（不是 0）—— 见 `function_channel_defaults`
          由 patch 侧按通道语义填（motor_* 归中、其余按配置）。
        """
        cache = getattr(self, "_ch_default_255_cache", None)
        if cache is None:
            cache = self._ch_default_255_cache = {}
        key = (str(fid), str(ch_type))
        if key in cache:
            return cache[key]
        out = _lookup_channel_default(self, str(fid), str(ch_type))
        cache[key] = out
        return out

    def set_values_from_cue(self, cue: CueData):
        for fi in self._fixtures:
            fi_vals = cue.values.get(fi.id, {})
            for ct in self._ch_types:
                if ct in fi_vals:
                    self._values.setdefault(fi.id, {})[ct] = fi_vals[ct]
        self._bump_vd_color_version()
        if self._expanded or self._body.isVisible():
            self._rebuild_body()
        else:
            self._body_needs_rebuild = True
        if "R" in self._ch_types:
            self._update_color_preview()

    def restore_static_snapshot(self, snapshot: Dict[str, Dict[str, int]],
                                range_states: Optional[Dict[str, dict]] = None,
                                touch_shape: bool = True) -> bool:
        """把 Programmer CUE 库的静态快照写回本 Row 的控制条带并下发。

        与 set_values_from_cue 的区别：那个只改 _values 显示缓存，不会还原
        条带/推杆控件位置，也不下发。这里把值写进 fader 层（与用户手动拖动
        条带等价），刷新条带控件位置、body 单元格，并发出 layer batch 信号，
        由 ProgrammerPanel 调度实时输出。只处理本 Row 拥有的通道类型。

        ★ range_states（2026-09-13 加）：**条带形状**（mode/base/target）。
        用户反馈"明明拉了条带，还原只恢复一个值、控件没还原"——根因是快照
        只存 `{fid: {ch: val}}` 值表，还原时用均值强制 `flat`。现支持传入
        保存的形状，逐通道恢复 `one_way/two_way/…` 与 base/target，
        使控件（条带视觉 + 推杆位置）真实还原。

        ★★ touch_shape=False（2026-09-18 回访修正）：**纯播放**（场景调用）
          时值照写、但**一个控件都不许动**。原实现没有这个开关，纯播放
          （`range_states=None`）只能走下面的 `else` 分支 —— 那本是给"旧快照无形状"
          准备的均值兜底，于是"点一下场景只播放效果"变成了"把用户正在编辑的
          one_way 条带压成 flat"。见 `dispatch_recall_state` 的调用点。
        """
        batch: Dict[str, Dict[str, int]] = {}
        # 每个通道类型记录写入的所有值，用于推断条带应显示的代表值。
        per_ch_vals: Dict[str, List[int]] = {}
        self._is_refreshing = True
        try:
            for fi in self._fixtures:
                fi_vals = snapshot.get(fi.id, {})
                if not fi_vals:
                    continue
                fd = self._fixture_defs.get(fi.def_id)
                for ct in self._ch_types:
                    if ct not in fi_vals:
                        continue
                    if not _fixture_supports_logical_channel(fd, ct):
                        continue
                    val = max(0, min(255, int(round(float(fi_vals[ct])))))
                    self._layers.setdefault("fader", {}).setdefault(fi.id, {})[ct] = val
                    merged = int(round(self._compute_merged_value(fi.id, ct)))
                    self._values.setdefault(fi.id, {})[ct] = merged
                    _mark_cell_edited(self, fi.id, ct, merged)
                    if self._expanded and self._body.isVisible():
                        self._refresh_body_cell(fi.id, ct, merged)
                    batch.setdefault(fi.id, {})[ct] = val
                    per_ch_vals.setdefault(ct, []).append(val)
        finally:
            self._is_refreshing = False

        # 折叠态下没有走 _refresh_body_cell 逐字节刷新单元格，置脏标志，
        # 使下次展开时 _toggle 触发 _rebuild_body，反映还原后的值。
        if not (self._expanded and self._body.isVisible()):
            self._body_needs_rebuild = True

        # 快照可能含颜色通道 → 失效虚拟亮度基色采样缓存
        self._bump_vd_color_version()

        # 还原条带/推杆/滑块/摇杆控件位置（形状还原见 `_restore_row_range_shapes`）：
        #   · 有保存的形状（range_states）→ **按原形状还原**（mode/base/target）
        #     —— 修复"拉了条带却只还原一个值、控件没还原"（2026-09-13）。
        #   · 无形状（旧快照）→ 回退均值 flat（旧行为，向后兼容）。
        # ★★ touch_shape=False = 纯播放语义：**整段跳过**（值已写进 fader 层，
        #   控件保持用户当前状态）。这条守卫必须在循环之前，否则"无形状兜底"
        #   仍会把用户拉好的条带压平。
        if touch_shape:
            _restore_row_range_shapes(self, per_ch_vals, range_states)
        if "R" in self._ch_types:
            self._update_color_preview()
        if batch:
            self.fixture_layer_batch_changed.emit(batch, "fader")
        return bool(batch)

    def clear_values(self):
        self._values.clear()
        self._marked.clear()
        self._detached_cells.clear()
        _clear_external_cells(self)
        for layer_name in self._layers:
            self._layers[layer_name].clear()
        self._bump_vd_color_version()
        if hasattr(self, "_master_slider"):
            self._master_slider.blockSignals(True)
            self._master_slider.setValue(0)
            self._master_slider.blockSignals(False)
        if hasattr(self, "_val_label"):
            self._val_label.setText(_ptool.format_value_dual(0))  # R183 项 13
        if "R" in self._ch_types and hasattr(self, "_color_preview"):
            self._color_preview.setStyleSheet(
                "background:#000000;border:1px solid #3D3D3D;border-radius:3px;")
        if self._expanded or self._body.isVisible():
            self._rebuild_body()
        else:
            self._body_needs_rebuild = True

    def full_open(self):
        # ★★ R188 性能（方案A·边界合并，语义逐字节等价）：旧实现逐格
        #   `_set_value` → `_set_layer_value`（本组 16631 格）→ 每格两路面板级
        #   信号逐格触发 `_schedule_live_output`/`report_to_bus`，实测整键 ~1.0s。
        #   批量体搬到 `programmer_panel_lifecycle.apply_full_open`（上帝类方法
        #   行数/CC 门禁；作用域/末态/轨迹等价性说明见该 helper docstring）。
        apply_full_open(self)
        if hasattr(self, "_master_slider"):
            self._master_slider.blockSignals(True)
            self._master_slider.setValue(255)
            self._master_slider.blockSignals(False)
        if hasattr(self, "_val_label"):
            self._val_label.setText(_ptool.format_value_dual(255))  # R183 项 13
        if "R" in self._ch_types:
            self._update_color_preview()

    def has_channels_for_fixtures(self) -> bool:
        # ★ 性能：设备能力只取决于 def（行内设备共享 def），按 def 去重判定，
        #   避免组切换 load_fixtures 对 54 行 × 整组设备反复全量扫描。
        for fd in self._iter_defs():
            if any(_fixture_supports_logical_channel(fd, ct) for ct in self._ch_types):
                return True
        return False

    # ── 摇杆绑定指示器 ──────────────────────────────────────────────────

    def update_binding_indicators(self):
        """根据 JoystickControlBindManager 状态刷新指示器。"""
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        if not hasattr(self, "_binding_indicators"):
            return
        my_def = getattr(self, "_def_id", "")
        for ct in self._ch_types:
            ind = self._binding_indicators.get(ct)
            if ind is None:
                continue
            # ★ 串台修复：只显示「绑定目标属于本行设备类型」的指示器——同 ch_type
            #   跨设备类型行（数控基底电机 vs 数控三维通用二仪）不再互相亮灯误导。
            #   旧式通用绑定（target_def_id 为空）保持全部行显示（旧行为）。
            candidates = [mgr.get_binding(ct, m) for m in ("2d", "3d", "")]
            mine = [r for r in candidates
                    if r is not None and (not r.target_def_id or r.target_def_id == my_def)]
            if mine:
                ind.setPixmap(load_tabler_pixmap("device-gamepad-2", 14, FG_ACCENT))
                ind.setVisible(True)
                # tooltip 显示具体绑定信息
                tips = []
                for r in mine:
                    tag = "通用" if not r.space_mode else r.space_mode.upper()
                    tips.append(f"{tag}: {r.physical_axis.upper()}轴")
                ind.setToolTip("已绑定摇杆\n" + "\n".join(tips) +
                               "\n— 左键调整映射，右键管理")
            else:
                ind.setText("")
                ind.setVisible(False)

    def _on_binding_indicator_click(self, ct: str, event):
        """左键→调整映射，右键→弹出菜单。"""
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                from .joystick_control_binding import (
                    get_joystick_bind_manager, BindingMappingDialog)
            except Exception:
                return
            mgr = get_joystick_bind_manager()
            # 优先打开活跃绑定（按当前空间模式）
            space = getattr(self, "_joy_space_mode", "2d")
            rule = mgr.get_binding(ct, space)
            if rule is None:
                rule = mgr.get_binding(ct, "")
            if rule is None:
                return
            dlg = BindingMappingDialog(rule, self.window())
            dlg.exec()
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            try:
                from .joystick_control_binding import (
                    get_joystick_bind_manager, BindingMappingDialog)
            except Exception:
                return
            mgr = get_joystick_bind_manager()
            space = getattr(self, "_joy_space_mode", "2d")
            rule = mgr.get_binding(ct, space)
            if rule is None:
                rule = mgr.get_binding(ct, "")
            if rule:
                adj_action = menu.addAction("调整映射…")
                adj_action.triggered.connect(lambda: BindingMappingDialog(
                    rule, self.window()).exec())
            unbind_action = menu.addAction(f"取消 {ct} 绑定")
            unbind_action.triggered.connect(lambda: mgr.remove_binding(ct, space)
                                            or mgr.remove_binding(ct, ""))
            menu.addSeparator()
            mgr_action = menu.addAction("管理所有绑定…")
            mgr_action.triggered.connect(lambda: self._open_binding_manager_from_indicator())
            menu.exec(event.globalPosition().toPoint())

    def _open_binding_manager_from_indicator(self):
        """从指示器打开绑定管理窗口（转发到 ProgrammerPanel）。"""
        try:
            from .joystick_control_binding import BindingManagerDialog
        except Exception:
            return
        # 找到祖先 ProgrammerPanel
        parent = self
        while parent is not None:
            if hasattr(parent, "_open_binding_manager"):
                parent._open_binding_manager()
                return
            parent = parent.parent() if hasattr(parent, "parent") else None
        # fallback: 直接打开
        dlg = BindingManagerDialog(self.window())
        dlg.show()


# ══════════════════════════════════════════════════════════════════════════════
#  ChannelTypeList
# ══════════════════════════════════════════════════════════════════════════════
class ChannelTypeList(QWidget):
    value_changed = pyqtSignal(str, str, int)
    layer_value_changed = pyqtSignal(str, str, int, str)
    layer_batch_changed = pyqtSignal(dict, str)
    joy_amplitude_changed = pyqtSignal(dict)  # ★ 摇杆幅度控制信号转发
    sync_z_command_changed = pyqtSignal(object, list)  # ★ 同步组 Z 命令转发
    controlTrajectoryEvent = pyqtSignal(str, object)  # ★ 轨迹事件转发（见行类说明）
    status_feedback = pyqtSignal(str, int)  # ★ R173/R174: 状态栏反馈信号转发

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[ChannelTypeRow] = []
        self._fixtures = []
        self._fixture_defs = {}
        self._fixture_signature: Tuple = ()
        self._pkg_arrange: bool = False
        self._pkg_of: Dict[str, dict] = {}
        # ★ 性能：def 级通道信息缓存（id(fd) → (功能通道, 中文名)）
        self._def_ch_info_cache: Dict[int, tuple] = {}
        # ★ 性能：虚拟亮度基色版本号——颜色通道写入/组切换时递增，
        #   ProgrammerPanel._vd_color_cache 据此失效。
        self._vd_color_version: int = 0
        self._build_ui()

    def set_pkg_arrange(self, pkg_of: dict, on: bool):
        """包排列模式（ProgrammerPanel 工具栏按钮下发；纯展示整理）。"""
        self._pkg_arrange = bool(on)
        self._pkg_of = pkg_of or {}
        for row in self._rows:
            if hasattr(row, "set_pkg_arrange"):
                row.set_pkg_arrange(self._pkg_arrange, self._pkg_of)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(28)
        scope_qss(title_bar,
            f"background:{BG_TOOLBAR};border-bottom:1px solid {_th.BORDER_NORMAL};")
        tlay = QHBoxLayout(title_bar)
        tlay.setContentsMargins(8, 0, 8, 0)
        self._group_title = QLabel("未选择设备组")
        self._group_title.setStyleSheet(
            f"color:{FG_DIM};font-size:11px;font-weight:bold;")
        tlay.addWidget(self._group_title)
        tlay.addStretch()
        self._fixture_count_lbl = QLabel("")
        self._fixture_count_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        tlay.addWidget(self._fixture_count_lbl)
        outer.addWidget(title_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # 滚动条外观由全局 QSS 提供；滚动区保持透明无边框
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet(f"background:{BG_MAIN};")
        self._scroll_lay = QVBoxLayout(self._scroll_content)
        self._scroll_lay.setContentsMargins(0, 0, 0, 0)
        self._scroll_lay.setSpacing(0)
        self._scroll_lay.addStretch()
        scroll.setWidget(self._scroll_content)
        outer.addWidget(scroll, stretch=1)

        # ★ 按设备类型分区：每种设备一个容器（标题=类型名+台数，可折叠），容器内
        #   是该类型设备的通道行。只显示当前选中组里存在的类型（没有的直接隐藏，
        #   不留空壳），避免长列表翻找。数据逻辑 self._rows 保持全局不变，仅显示层
        #   按类型分组——推杆/滑块/摇杆/模式/翼块/合并编辑等全部控件逻辑不受影响。
        self._type_containers: Dict[str, QWidget] = {}
        self._type_bodies: Dict[str, QWidget] = {}
        self._type_headers: Dict[str, QLabel] = {}
        self._container_rows: Dict[str, list] = {}
        self._container_row_keys: Dict[str, list] = {}
        self._container_collapsed: Dict[str, bool] = {}

        # 空态占位：工程无设备 / 未选择组时提示（懒构建，只建一次）
        self._empty_state: Optional[EmptyState] = None

    def _toggle_empty_state(self, empty: bool):
        """按当前设备数量显示/隐藏空态占位。"""
        if empty and self._empty_state is None:
            self._empty_state = EmptyState(
                "当前设备组没有可编辑的设备",
                "先在「配接面板」添加设备，或从左侧选择一个设备组",
                "device-desktop", image_id="empty_plant")
            self._scroll_lay.insertWidget(self._scroll_lay.count() - 1,
                                          self._empty_state)
        if self._empty_state is not None:
            self._empty_state.setVisible(bool(empty))

    def _connect_row_signals(self, row: ChannelTypeRow):
        row.fixture_value_changed.connect(
            lambda fid, ct, v: self.value_changed.emit(fid, ct, v))
        row.fixture_layer_value_changed.connect(
            lambda fid, ct, v, layer: self.layer_value_changed.emit(fid, ct, v, layer))
        row.fixture_layer_batch_changed.connect(
            lambda batch, layer: self.layer_batch_changed.emit(batch, layer))
        row.joy_amplitude_changed.connect(
            lambda overrides: self.joy_amplitude_changed.emit(overrides))
        row.sync_z_command_changed.connect(
            lambda angle_deg, fids: self.sync_z_command_changed.emit(angle_deg, fids))
        row.controlTrajectoryEvent.connect(
            lambda key, val: self.controlTrajectoryEvent.emit(key, val))
        row.status_feedback.connect(
            lambda msg, timeout: self.status_feedback.emit(msg, timeout))
        # 调度台·轨迹转发（幅度/Z 同步为离散事件，一次性记录）
        # ★★ 2026-09-17 修复（本类第二次同类误用）：此处 `self` 是 ChannelTypeList，
        #   而 `_emit_traj_overrides` 定义在 ChannelTypeRow 上 → 每次摇杆幅度事件
        #   都 AttributeError（被 Qt 吞掉/弹未处理异常框），幅度轨迹录制整条丢失。
        #   展开逻辑改走模块级 helper（与行内实现同源，不再依赖行对象方法）。
        row.joy_amplitude_changed.connect(
            lambda overrides, _lst=self: _emit_traj_overrides_to(
                _lst.controlTrajectoryEvent, "programmer|joyamp", overrides))
        row.sync_z_command_changed.connect(
            lambda angle_deg, fids: self.controlTrajectoryEvent.emit(
                "programmer|zsync", {"angle": angle_deg, "fixtures": fids}))

    def _def_channel_info(self, fd):
        """按 def 对象计算容器级通道信息（功能通道列表 + 功能通道中文名）。

        ★ 性能：结果只取决于 def，与设备数量无关；def 对象未变时缓存复用，
        组切换不再对整组设备（最多 2100+ 台）逐台扫描通道。
        与原逐台扫描结果完全一致（同一 def 的 union 语义）。
        ★ 正确性：value 持有 fd 引用——工程切换后新 def 对象不复用旧 id，
        命中校验 `cached[0] is fd` 双保险（审查发现 id 复用风险）。
        """
        cached = self._def_ch_info_cache.get(id(fd))
        if cached is not None and cached[0] is fd:
            return cached[1], cached[2]
        func_chs = sorted(
            {
                str(ch) for ch in (getattr(fd, "channels", []) or [])
                if not str(ch).endswith("_fine") and _is_function_channel(str(ch))
            },
            key=_function_channel_number,
        )
        names: Dict[str, str] = {}
        fd_names = getattr(fd, "function_channel_names", {}) or {}
        if isinstance(fd_names, dict):
            for ch_type, name in fd_names.items():
                ch_type_s = str(ch_type)
                if _is_function_channel(ch_type_s) and str(name).strip():
                    names.setdefault(ch_type_s, str(name).strip())
        self._def_ch_info_cache[id(fd)] = (fd, func_chs, names)
        return func_chs, names

    def _toggle_type_container(self, did: str):
        """点击类型头折叠/展开该类型容器（仅显示整理，不改任何通道数据）。"""
        collapsed = not self._container_collapsed.get(did, False)
        self._container_collapsed[did] = collapsed
        body = self._type_bodies.get(did)
        if body is not None:
            body.setVisible(not collapsed)
        hdr = self._type_headers.get(did)
        if hdr is not None:
            txt = str(hdr.text())
            if txt[:1] in ("▾", "▸"):
                hdr.setText(("▸" if collapsed else "▾") + txt[1:])

    def _create_type_container(self, did: str, dfixtures, fixture_defs: dict):
        """为一种设备类型建容器（类型头 + 可折叠内容区）。"""
        fd = fixture_defs.get(did)
        tname = str(fd.name) if fd is not None else "未知类型"
        container = QWidget()
        container.setStyleSheet(f"background:{BG_MAIN};")
        vlay = QVBoxLayout(container)
        vlay.setContentsMargins(0, 4, 0, 4)
        vlay.setSpacing(0)
        hdr = QLabel(f"▾ {tname}  ({len(dfixtures)}台)")
        hdr.setStyleSheet(
            f"color:{_th.TEXT_PRIMARY};font-size:12px;font-weight:bold;"
            f"background:{BG_HEADER};border-left:4px solid {_th.BRIGHT_SUCCESS};"
            f"padding:5px 10px;border-radius:3px;")
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr.setToolTip("点击折叠/展开该设备类型的通道行")
        hdr.mousePressEvent = lambda e, d=did: self._toggle_type_container(d)
        vlay.addWidget(hdr)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(8, 2, 4, 2)
        body_lay.setSpacing(0)
        vlay.addWidget(body)
        self._type_containers[did] = container
        self._type_bodies[did] = body
        self._type_headers[did] = hdr
        self._container_rows[did] = []
        self._container_row_keys[did] = []
        idx = self._scroll_lay.count() - 1
        self._scroll_lay.insertWidget(idx, container)

    def _update_container_rows(self, did: str, dfixtures, fixture_defs: dict):
        """刷新一个类型容器的通道行：只建有该类型设备支持的通道行，行内合并编辑。"""
        fd = fixture_defs.get(did)
        tname = str(fd.name) if fd is not None else "未知类型"
        rows = self._container_rows.get(did) or []
        keys = self._container_row_keys.get(did) or []
        body = self._type_bodies.get(did)
        if body is None:
            return
        hdr = self._type_headers.get(did)
        if hdr is not None:
            collapsed = self._container_collapsed.get(did, False)
            arrow = "▸" if collapsed else "▾"
            hdr.setText(f"{arrow} {tname}  ({len(dfixtures)}台)")
        # ★ 性能：通道行集合只取决于设备类型 def（容器内全部设备共享同一 def）。
        #   按 def 对象去重后计算（union 语义与原逐台扫描完全一致），
        #   把「对整组设备（最多 2100+ 台）× 通道」的反复扫描降为 O(唯一def数)。
        defs_seen = []
        for _fi in dfixtures or []:
            _fd = fixture_defs.get(getattr(_fi, "def_id", ""))
            if _fd is None or _fd in defs_seen:
                continue
            defs_seen.append(_fd)
        names: Dict[str, str] = {}
        func_chs: List[str] = []
        for _fd in defs_seen:
            _fchs, _fnams = self._def_channel_info(_fd)
            for _c in _fchs:
                if _c not in func_chs:
                    func_chs.append(_c)
            for _ct, _nm in _fnams.items():
                names.setdefault(_ct, _nm)
        wanted = []
        for group_name, ch_types in PARAM_GROUPS:
            if any(_fixture_supports_logical_channel(_fd, ct) for _fd in defs_seen for ct in ch_types):
                # 行标题按该类型实际存在的通道动态生成（如只有 motor_x 的设备显示
                # 「电机 X」而非静态组名「Motor XYZ」，避免误导；双轴设备显示
                # 「电机 X · 电机 Y」）
                actual = [ct for ct in ch_types
                          if any(_fixture_supports_logical_channel(_fd, ct) for _fd in defs_seen)]
                title = " · ".join(_ch_label(ct) for ct in actual) if actual else group_name
                # ★ 行通道列表必须与标题一致（actual 子集）。旧实现传完整组元组
                #   （Motor XYZ 组传 ("motor_x","motor_y","motor_z")），使仅 X 轴的
                #   二仪/喷头等设备行显示 XYZ 三轴芯片/展开节/XY摇杆/Y-Z 下拉
                #   （用户「带电机的设备被识别成 XYZ 三轴」根因）；传 tuple(actual)
                #   后行只建实际存在的通道。键为 actual 且同会话内 def 固定→自洽。
                wanted.append((title, tuple(actual)))
        for ch_type in sorted(func_chs, key=_function_channel_number):
            wanted.append((_ch_label(ch_type, names), (ch_type,)))
        # ★ 保留判定必须与 _container_row_keys 同构（ch_types 元组）：wanted 是
        #   (title, ch_types) 二元组，若拿二元组做集合成员判定恒 False → 每次组
        #   切换全量销毁重建行 → 行内 _values/_layers(用户编辑数据)全部丢失
        #   （审查 Bug-1）
        wanted_set = {ch_types for _title, ch_types in wanted}
        key_to_title = {ch_types: title for title, ch_types in wanted}
        # 移除该类型不再需要的行
        keep = []
        for row, key in zip(rows, keys):
            if key in wanted_set:
                keep.append((row, key))
                title = key_to_title.get(key)
                if title and hasattr(row, "set_group_name"):
                    row.set_group_name(title)
                continue
            if row in self._rows:
                self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()
        self._container_rows[did] = [r for r, _k in keep]
        self._container_row_keys[did] = [k for _r, k in keep]
        # 建缺失行
        body_lay = body.layout()
        for group_name, ch_types in wanted:
            if ch_types in self._container_row_keys[did]:
                continue
            row = ChannelTypeRow(group_name, list(ch_types))
            row._owner_list = self
            row._def_id = did   # 串台修复：记录行所属设备类型容器
            self._connect_row_signals(row)
            self._rows.append(row)
            self._container_rows[did].append(row)
            self._container_row_keys[did].append(ch_types)
            if body_lay is not None:
                body_lay.addWidget(row)
        collapsed = self._container_collapsed.get(did, False)
        for row in self._container_rows[did]:
            row.load_fixtures(dfixtures, fixture_defs)  # 本类型子集，随后应用折叠。
            _pinj.sync_jitter_row(row)
            row.setVisible(not collapsed)

    def _rebuild_type_containers(self, fixtures, fixture_defs: dict):
        """按设备类型分区重建：只显示当前选中组里存在的类型（没有的直接隐藏）。

        ★ 容器/行跨组保留（隐藏而非销毁）：行内 _values/_layers（用户编辑的 fader/
        推杆/摇杆/音频值）随行对象存活，切组不丢值（审查 Bug-1 的容器级根因）。
        """
        # ★ 性能：by_def 子集列表按 (fixtures, fixture_defs) 对象身份缓存——
        #  _refresh_fixtures 同状态刷新时 fixtures/defs 是稳定副本（见
        #  _refresh_fixture_sources），子集列表复用 → 行级 load_fixtures 的
        #  (id(dfixtures), id(defs)) 签名缓存命中（40 行 × 整组设备扫描 ~110ms
        #  → 接近零）。fixtures 真正变化时对象身份变 → 重建子集。
        by_def = None
        if (getattr(self, "_by_def_fx_ref", None) is fixtures
                and getattr(self, "_by_def_defs_ref", None) is fixture_defs):
            by_def = getattr(self, "_by_def_cache", None)
        if by_def is None:
            by_def = {}
            for fi in fixtures or []:
                by_def.setdefault(str(getattr(fi, "def_id", "")), []).append(fi)
            self._by_def_fx_ref = fixtures
            self._by_def_defs_ref = fixture_defs
            self._by_def_cache = by_def
        for did in list(self._type_containers.keys()):
            if did not in by_def:
                cont = self._type_containers.get(did)
                if cont is not None:
                    cont.setVisible(False)
        for did, dfixtures in by_def.items():
            if did not in self._type_containers:
                self._create_type_container(did, dfixtures, fixture_defs)
            else:
                cont = self._type_containers.get(did)
                if cont is not None:
                    cont.setVisible(True)
            self._update_container_rows(did, dfixtures, fixture_defs)

    def load_fixtures(self, fixtures, fixture_defs: dict, group_name: str = ""):
        # 快速路径：源列表对象与 defs 字典均未替换且标题未变时直接返回，
        # 避免 11382 台设备每次刷新都重建 list + tuple 签名。
        if (fixtures is getattr(self, "_fixtures", None)
                and fixture_defs is getattr(self, "_fixture_defs", None)):
            try:
                expected = group_name or f"设备组（{len(fixtures)} 台）"
                if expected == self._group_title.text():
                    return
            except Exception:
                pass
        new_fixtures = list(fixtures)
        new_sig = tuple(
            (
                getattr(f, "id", ""),
                getattr(f, "name", ""),
                getattr(f, "def_id", ""),
                int(getattr(f, "universe", 1) or 1),
                int(getattr(f, "start_channel", 1) or 1),
            )
            for f in new_fixtures
        )
        if (new_sig == self._fixture_signature and fixture_defs is self._fixture_defs and
                (group_name or f"设备组（{len(new_fixtures)} 台）") == self._group_title.text()):
            return
        self._fixtures = new_fixtures
        self._fixture_signature = new_sig
        self._fixture_defs = fixture_defs
        self._group_title.setText(group_name or f"设备组（{len(new_fixtures)} 台）")
        self._fixture_count_lbl.setText(f"{len(new_fixtures)} 台设备")
        self._toggle_empty_state(len(new_fixtures) == 0)
        # 按设备类型分区：每类型容器内行只加载该类型设备（子集），
        # 不存在的类型直接隐藏（见 _rebuild_type_containers）
        self._rebuild_type_containers(new_fixtures, fixture_defs)
        _pkg_on = bool(getattr(self, "_pkg_arrange", False))
        _pkg_of = getattr(self, "_pkg_of", None) or {}
        for row in self._rows:
            if hasattr(row, "set_pkg_arrange"):
                row.set_pkg_arrange(_pkg_on, _pkg_of)
            has = row.has_channels_for_fixtures()
            row._body.setEnabled(has)
            if hasattr(row, '_ctrl_panel'):
                row._ctrl_panel.setEnabled(True)
            # ★ 性能：setStyleSheet 单次约 1ms（Qt 全量重解析样式），组切换 54 行
            #   ≈ 59ms。样式只依赖 has 布尔值，未变化时跳过（视觉结果完全一致）。
            if getattr(row, "_last_header_has", None) != has:
                _pcui.style_channel_header(row, has, _ch_color(row._ch_types[0]))
                row._last_header_has = has
        # ★ 串台二次修复：行构建完成(唯一时机)后对旧版绑定数据(无 target_def_id)
        #   自动迁移固化；无法固化的绑定在分发时暂停输出(见 _find_joystick_target_row)。
        self._migrate_legacy_joystick_bindings()

    def _joystick_match_rows(self, ch_type: str, space_mode: str = "") -> list:
        """全量候选行(可见+隐藏——可见性随组切换漂移,不可作为解析依据)。

        与 _find_joystick_target_row 的候选判定同构: 匹配 ch_type 行 + 空间模式。
        """
        result = []
        for row in self._rows:
            if sip.isdeleted(row):
                continue
            if ch_type not in getattr(row, "_ch_types", []):
                continue
            if space_mode:
                if getattr(row, "_joy_space_mode", "2d") != space_mode:
                    continue
            result.append(row)
        return result

    def _migrate_legacy_joystick_bindings(self):
        """旧绑定数据(空 target_def_id, d4cacc0 之前保存)自动迁移。

        启动自动 load() 恢复的旧绑定若走「可见行优先」解析,两设备类型容器都有
        motor_x 时会写进非绑定目标行=串台。此处: 当前工程中该 ch_type 全量候选
        **唯一** → 固化 target_def_id(此后分发完全确定性); 多候选/无候选无法
        确定 → 保持空,分发时不写值(不串台),一次性提示重新绑定。幂等可反复调用。
        """
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        changed = False
        unresolved = 0
        for rule in mgr.get_all_bindings():
            if rule.target_kind == "effect_param" or rule.target_def_id:
                continue
            rows = self._joystick_match_rows(rule.ch_type, rule.space_mode or "")
            if len(rows) == 1:
                did = str(getattr(rows[0], "_def_id", "")) or ""
                if did:
                    rule.target_def_id = did
                    changed = True
            else:
                unresolved += 1
        if changed:
            mgr.save()
        if unresolved and not getattr(mgr, "unresolved_warned", False):
            mgr.unresolved_warned = True
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self, "摇杆绑定需要重新设置",
                f"检测到 {unresolved} 条旧版摇杆绑定无法自动匹配当前工程的设备类型"
                f"(同名通道存在于多个设备类型或目标类型已移除)。为避免串台,这些"
                f"绑定已暂停输出,请打开「绑定管理」窗口重新绑定。"))

    def get_all_values(self) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {}
        for row in self._rows:
            for fid, ch_vals in _static_values_skip_external(row, row.get_values()).items():
                if fid not in result:
                    result[fid] = {}
                result[fid].update(ch_vals)
        return result

    def get_marked_values(self, *, include_detached: bool = True) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {}
        for row in self._rows:
            for fid, ch_vals in row.get_marked_values(
                    include_detached=include_detached).items():
                if fid not in result:
                    result[fid] = {}
                result[fid].update(ch_vals)
        return result

    def clear_all(self):
        for row in self._rows:
            row.clear_values()

    def full_open(self):
        for row in self._rows:
            if row.has_channels_for_fixtures():
                row.full_open()

    def load_cue(self, cue: CueData):
        for row in self._rows:
            row.set_values_from_cue(cue)

    def restore_static_snapshot(self, snapshot: Dict[str, Dict[str, int]],
                                range_states: Optional[Dict[str, dict]] = None,
                                scope_ids: Optional[list] = None,
                                touch_shape: bool = True) -> bool:
        """把静态快照还原到所有 Row 的控制条带并下发。

        ★ 2026-09-14 用户实报数据丢失：本包装原签名**没有** `range_states`，把上游
          传进来的条带形状**静默丢弃**（`restore_static_snapshot(vals, range_states)`
          在这里被截成只传 `snapshot`）→ 条带永远按均值压成 flat。必须透传。
        ★ 2026-09-17 范围门卫：传了 `scope_ids` 就只允许写作用域内的设备条目 ——
          隐藏行的旧 `_fixtures` 不得把扇子等越界设备写出去（用户实报）。
        ★ 2026-09-18 纯度门卫：`touch_shape=False` = 纯播放，透传给 Row（见其说明）。
        """
        if scope_ids is not None:
            snapshot = _scope_static_values(snapshot, scope_ids)
        changed = False
        for row in self._rows:
            if row.restore_static_snapshot(snapshot, range_states,
                                           touch_shape=touch_shape):
                changed = True
        return changed

    def restore_range_states(self, range_states: Dict[str, dict]) -> None:
        """只还原**条带形状**（mode/base/target）到 owning 行（无静态值时的兜底）。"""
        if not range_states:
            return
        for row in self._rows:
            did = str(getattr(row, "_def_id", "") or "")
            for ct, st in _iter_range_states(range_states, row._ch_types, did):
                try:
                    row._set_range_state(ct, str(st.get("mode", "flat")),
                                         int(st.get("base", 0)),
                                         int(st.get("target", 0)))
                except Exception:
                    continue

    def snapshot_static_values(self) -> Dict[str, Dict[str, int]]:
        """抓取当前所有 Row 的静态位置值快照（与 get_all_values 等价的合并视图）。

        ★ R141：`get_all_values` 已剔除"只由音频/矩阵贡献"的格子，不会再回退到音频瞬时值。
        """
        return self.get_all_values()

    def apply_batch_values(self, dmx_data: dict, emit_signals: bool = True, refresh_body: bool = True):
        for row in self._rows:
            row.apply_batch_values(dmx_data, emit_signals=emit_signals, refresh_body=refresh_body)


# ══════════════════════════════════════════════════════════════════════════════
#  GroupSelector
# ══════════════════════════════════════════════════════════════════════════════
class GroupSelector(QWidget):
    group_selected = pyqtSignal(str, list, str)
    group_delete_requested = pyqtSignal(str, str)
    logical_mode_toggle_requested = pyqtSignal(str, str)  # (logical_id, new_mode)
    light_control_mode_changed = pyqtSignal(str)  # 'single' | 'whole'（全局灯控，联动所有页面）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups = []
        self._all_fixtures = []
        self._logical_devices: list = []  # LogicalDevice dict 列表（组合/单元）
        self._selected_id: Optional[str] = None
        self._selected_ids: set[str] = set()
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 标题栏
        title = QWidget()
        title.setFixedHeight(26)
        scope_qss(title,
            f"background:{BG_TOOLBAR};border-bottom:1px solid {_th.BORDER_NORMAL};")
        tlay = QHBoxLayout(title)
        tlay.setContentsMargins(10, 0, 8, 0)
        lbl = QLabel("设备组")
        lbl.setToolTip("选择要编辑的设备组；Ctrl+点击可多选")
        lbl.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;"
            f"letter-spacing:1px;")
        tlay.addWidget(lbl)
        tlay.addStretch()
        outer.addWidget(title)

        # 全局灯控模式开关（常驻可见，联动所有页面）：单控灯=各自坐标系 / 整体控灯=整体坐标系
        light_bar = QWidget()
        light_bar.setFixedHeight(30)
        scope_qss(light_bar,
            f"background:{_th.BG_BOX};border-bottom:1px solid {_th.BORDER_NORMAL};")
        llay = QHBoxLayout(light_bar)
        llay.setContentsMargins(8, 3, 8, 3)
        llay.setSpacing(5)
        llbl = QLabel("灯控")
        llbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;font-weight:bold;")
        llay.addWidget(llbl)
        self._light_mode_btn_single = QPushButton("单控灯")
        self._light_mode_btn_whole = QPushButton("整体控灯")
        for b in (self._light_mode_btn_single, self._light_mode_btn_whole):
            b.setCheckable(True)
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background:{_th.BG_BUTTON};"
                f" border:1px solid {_th.BORDER_NORMAL};"
                f" border-radius:{_th.RADIUS}px; color:{_th.TEXT_PRIMARY};"
                f" font-size:11px; padding:0 8px; min-height:0px; }}"
                f"QPushButton:checked {{ background:{_th.SELECT_BLUE};"
                f" border:1px solid {_th.SELECT_BLUE};"
                f" color:{_th.TEXT_SELECTED_FG}; font-weight:bold; }}"
                f"QPushButton:hover {{ background:{_th.BG_BUTTON_HOVER}; }}"
                f"QPushButton:disabled {{ color:{_th.TEXT_DISABLED};"
                f" background:{_th.BG_EDITOR}; }}"
            )
            llay.addWidget(b, 1)
        self._light_mode_btn_single.setToolTip(
            "单控灯：包内灯逐个独立控制（effect 各自计算值，全局联动所有页面）。\n"
            "与坐标系正交：切换不改变各自/整体坐标系。")
        self._light_mode_btn_whole.setToolTip(
            "整体控灯：包内灯当一个灯控制（effect 共享同一效果值，如气爆配 4 灯同变；"
            "全局联动所有页面）。\n与坐标系正交：切换不改变各自/整体坐标系。")
        self._light_mode_btn_single.clicked.connect(lambda: self._on_light_mode_clicked("single"))
        self._light_mode_btn_whole.clicked.connect(lambda: self._on_light_mode_clicked("whole"))
        self._light_mode_btn_whole.setChecked(True)
        outer.addWidget(light_bar)

        # All Fixtures 按钮
        self._all_btn = QPushButton("All Fixtures")
        self._all_btn.setFixedHeight(30)
        self._all_btn.setCheckable(True)
        self._all_btn.setChecked(True)
        self._all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._all_btn.setToolTip("选中全部设备（点击组瓦片自动取消）")
        self._all_btn.setStyleSheet(
            f"QPushButton {{ background:{_th.BG_BUTTON};"
            f" border:1px solid {_th.BORDER_NORMAL};"
            f" border-radius:{_th.RADIUS}px; color:{_th.TEXT_PRIMARY};"
            f" font-size:12px; font-weight:bold; margin:4px 4px 2px 4px;"
            f" min-height:0px; padding:2px 8px; }}"
            f"QPushButton:checked {{ background:{_th.SELECT_BLUE};"
            f" border:1px solid {_th.SELECT_BLUE};"
            f" color:{_th.TEXT_SELECTED_FG}; }}"
            f"QPushButton:hover {{ background:{_th.BG_BUTTON_HOVER}; }}"
        )
        self._all_btn.clicked.connect(
            lambda: self._on_all_clicked())
        outer.addWidget(self._all_btn)

        # 组按钮滚动区（2列网格）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # 滚动条外观由全局 QSS 提供；滚动区保持透明，容器底色走页面根底
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        self._btn_container = QWidget()
        self._btn_container.setStyleSheet(f"background:{BG_MAIN};")
        self._btn_lay = QGridLayout(self._btn_container)
        self._btn_lay.setContentsMargins(4, 4, 4, 4)
        self._btn_lay.setSpacing(4)
        scroll.setWidget(self._btn_container)
        outer.addWidget(scroll, stretch=1)

    def _on_light_mode_clicked(self, mode: str):
        mode = "whole" if mode == "whole" else "single"
        self._light_mode_btn_single.setChecked(mode == "single")
        self._light_mode_btn_whole.setChecked(mode == "whole")
        self.light_control_mode_changed.emit(mode)

    def set_light_control_mode(self, mode: str):
        """外部同步当前全局灯控模式到按钮（不发信号）。"""
        mode = "whole" if str(mode).strip().lower() == "whole" else "single"
        if hasattr(self, "_light_mode_btn_single"):
            self._light_mode_btn_single.blockSignals(True)
            self._light_mode_btn_whole.blockSignals(True)
            self._light_mode_btn_single.setChecked(mode == "single")
            self._light_mode_btn_whole.setChecked(mode == "whole")
            self._light_mode_btn_single.blockSignals(False)
            self._light_mode_btn_whole.blockSignals(False)

    def load_groups(self, groups, all_fixtures, logical_devices=None):
        self._groups = list(groups)
        self._all_fixtures = list(all_fixtures)
        if logical_devices is not None:
            # 瓦片只显示 kind="composite"（按类型合并的新设备）。kind="unit" 一律不显示——
            # 单元是单控/整体控的隐藏控制分组(由全局灯控开关操作)，不是可点选的设备组瓦片；
            # 这也清掉旧版残留的两两一对 unit。另外要求成员仍存在(剔孤儿)。
            live_ids = {str(getattr(f, "id", "") or (f.get("id") if isinstance(f, dict) else ""))
                        for f in self._all_fixtures}
            live_ids.discard("")
            cleaned = []
            for d in logical_devices:
                if not isinstance(d, dict):
                    continue
                if str(d.get("kind", "unit") or "unit") != "composite":
                    continue
                if d.get("hidden"):
                    continue
                members = [str(m) for m in (d.get("member_fixture_ids", []) or [])]
                if any(m in live_ids for m in members):
                    cleaned.append(d)
            self._logical_devices = cleaned
        valid_ids = {self._group_id(g) for g in self._groups}
        valid_ids |= {str(d.get("id", "")) for d in self._logical_devices}
        self._selected_ids = {gid for gid in self._selected_ids if gid in valid_ids}
        if self._selected_id not in valid_ids:
            self._selected_id = None
        if hasattr(self, "_all_btn"):
            self._all_btn.setChecked(not self._selected_ids)
        self._rebuild_buttons()

    def _group_id(self, group) -> str:
        if group is None:
            return ""
        return str(group.get("id", "") if isinstance(group, dict) else getattr(group, "id", ""))

    def _group_name(self, group) -> str:
        if group is None:
            return ""
        return str(group.get("name", "未命名") if isinstance(group, dict) else getattr(group, "name", "未命名"))

    def _group_fixture_ids(self, group) -> list[str]:
        if group is None:
            return []
        if isinstance(group, dict):
            base_order = list(group.get("base_order_fixture_ids", []) or [])
            fixture_ids = list(group.get("fixture_ids", []) or [])
        else:
            base_order = list(getattr(group, "base_order_fixture_ids", []) or [])
            fixture_ids = list(getattr(group, "fixture_ids", []) or [])
        # R196：真实组瓦片也必须限制成员，不能把母序先扩员再交给下游。
        return _order_members_by_base_seq(
            [str(fid) for fid in fixture_ids if str(fid)],
            [str(fid) for fid in base_order if str(fid)])

    def _rebuild_buttons(self):
        while self._btn_lay.count():
            item = self._btn_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._group_btns: list = []
        row, col = 0, 0
        for g in self._groups:
            if isinstance(g, dict):
                color = g.get("color", "#4A90D9")
                name = g.get("name", "未命名")
                fcount = len(g.get("fixture_ids", []))
            else:
                color = getattr(g, "color", "#4A90D9")
                name = getattr(g, "name", "未命名")
                fcount = len(getattr(g, "fixture_ids", []))
            btn = self._make_tile_btn(name, color, fcount, g)
            btn.setChecked(self._group_id(g) in self._selected_ids)
            self._btn_lay.addWidget(btn, row, col)
            self._group_btns.append(btn)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        # 逻辑/组合设备瓦片：同一次「按类型合并」的一批 composite 共享 merge_group →
        # 折叠成**一个**瓦片(代表整类合并设备)，避免 222 个实例铺成 222 个瓦片。
        grouped: dict[str, list] = {}
        singles: list = []
        for ld in self._logical_devices:
            if not str(ld.get("id", "")):
                continue
            mg = str(ld.get("merge_group", "") or "")
            if mg:
                grouped.setdefault(mg, []).append(ld)
            else:
                singles.append(ld)

        def _place(btn):
            nonlocal row, col
            self._btn_lay.addWidget(btn, row, col)
            self._group_btns.append(btn)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        for mg, lds in grouped.items():
            members = []
            seen = set()
            for ld in lds:
                for m in (ld.get("member_fixture_ids", []) or []):
                    sm = str(m)
                    if sm not in seen:
                        seen.add(sm); members.append(sm)
            modes = {str(ld.get("control_mode", "merged") or "merged") for ld in lds}
            mode = "merged" if modes == {"merged"} else ("decomposed" if modes == {"decomposed"} else "merged")
            syn = {
                "id": f"mg:{mg}",
                "name": mg,
                "member_fixture_ids": members,
                "kind": "composite",
                "control_mode": mode,
                "merge_group": mg,
                "_group_ids": [str(ld.get("id", "")) for ld in lds],
                "_count": len(lds),
            }
            btn = self._make_logical_tile_btn(f"{mg}", "#E6B455", len(lds), mode, syn)  # 复合瓦片强调→bright_warn 档（原 #ff9f43）
            btn.setChecked(syn["id"] in self._selected_ids)
            _place(btn)

        for ld in singles:
            lid = str(ld.get("id", ""))
            name = str(ld.get("name", "组合"))
            mode = str(ld.get("control_mode", "merged") or "merged")
            kind = str(ld.get("kind", "unit") or "unit")
            cnt = len(ld.get("member_fixture_ids", []) or [])
            color = "#E6B455" if kind == "composite" else "#71A8FF"  # composite→bright_warn / unit→focus_blue（原 #ff9f43/#54a0ff）
            btn = self._make_logical_tile_btn(name, color, cnt, mode, ld)
            btn.setChecked(lid in self._selected_ids)
            _place(btn)

        # 空态：无保存组、无逻辑设备时给占位说明（禁止大片纯黑）
        total_tiles = len(self._group_btns)
        if total_tiles == 0:
            es = EmptyState(
                "暂无设备组",
                "在「设备组」面板创建设备组，或直接使用 All Fixtures 编辑全部设备",
                "stack-2")
            es.setStyleSheet("background:transparent;")
            self._btn_lay.addWidget(es, row + (1 if (row or col) else 0), 0, 1, 2)
        _pcui.arrange_group_rows(self)

    def _make_logical_tile_btn(self, name, color, fcount, mode, ld) -> QPushButton:
        mode_tag = "合并" if mode == "merged" else "分解"
        btn = _pcui.make_group_row(name, color, fcount, mode=mode_tag)
        btn.setToolTip(f"组合设备 {name} · {fcount} 台（右键=切换合并/分解）")
        btn.clicked.connect(lambda checked, d=ld, b=btn: self._on_logical_click(d, b))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, d=ld, b=btn: self._show_logical_context_menu(d, b, pos))
        return btn

    def _on_logical_click(self, ld, clicked_btn):
        """选中逻辑设备 → 控制其全部真实成员（合并模式下主滑块广播给所有成员）。"""
        lid = str(ld.get("id", ""))
        members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
        self._all_btn.setChecked(False)
        self._selected_ids = {lid}
        self._selected_id = lid
        for btn in getattr(self, '_group_btns', []):
            btn.setChecked(btn is clicked_btn)
        self.group_selected.emit(lid, members, str(ld.get("name", "组合")))

    def _show_logical_context_menu(self, ld, button: QPushButton, pos):
        lid = str(ld.get("id", ""))
        if not lid:
            return
        mode = str(ld.get("control_mode", "merged") or "merged")
        menu = QMenu(self)
        # 右键菜单样式由全局 QMenu 统一提供，删除本页重复定义
        if mode == "merged":
            act = menu.addAction("切换为分解模式（按真实数目分别控制）")
            new_mode = "decomposed"
        else:
            act = menu.addAction("切换为合并模式（整体一个控件）")
            new_mode = "merged"
        chosen = menu.exec(button.mapToGlobal(pos))
        if chosen == act:
            self.logical_mode_toggle_requested.emit(lid, new_mode)

    def _make_tile_btn(self, name: str, color: str, fcount: int, group) -> QPushButton:
        btn = _pcui.make_group_row(name, color, fcount)
        btn.setToolTip(f"{name} · {fcount} 台（右键=删除设备组）")
        btn.clicked.connect(lambda checked, g=group, b=btn: self._on_click(g, b))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, g=group, b=btn: self._show_group_context_menu(g, b, pos))
        return preserve_group_space(btn)

    def _show_group_context_menu(self, group, button: QPushButton, pos):
        gid = self._group_id(group)
        if not gid:
            return
        name = self._group_name(group) or "未命名"
        menu = QMenu(self)
        # 右键菜单样式由全局 QMenu 统一提供，删除本页重复定义
        delete_action = menu.addAction("删除设备组")
        action = menu.exec(button.mapToGlobal(pos))
        if action == delete_action:
            self.group_delete_requested.emit(gid, name)

    def _on_all_clicked(self):
        """All Fixtures 按钮点击处理"""
        # 取消所有组按钮选中状态
        for btn in getattr(self, '_group_btns', []):
            btn.setChecked(False)
        self._all_btn.setChecked(True)
        self._selected_id = None
        self._selected_ids.clear()
        self.group_selected.emit(
            "__all__", [f.id for f in self._all_fixtures], "全部设备")

    def _on_click(self, group, clicked_btn):
        if group is None:
            for btn in getattr(self, '_group_btns', []):
                btn.setChecked(False)
            self._all_btn.setChecked(True)
            self._selected_id = None
            self._selected_ids.clear()
            self.group_selected.emit(
                "__all__", [f.id for f in self._all_fixtures], "全部设备")
            return

        gid = self._group_id(group)
        gname = self._group_name(group)
        mods = QApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        self._all_btn.setChecked(False)
        chosen = selected_groups(self._selected_ids,
            [self._group_id(g) for g in self._groups], gid, control=ctrl)
        self._selected_ids = set(chosen)
        self._selected_id = chosen[-1] if chosen else None
        for index, btn in enumerate(getattr(self, '_group_btns', [])):
            selected = index < len(self._groups) and self._group_id(self._groups[index]) in self._selected_ids
            btn.setChecked(selected)

        if not self._selected_ids:
            self._all_btn.setChecked(True)
            self._selected_id = None
            self.group_selected.emit("__all__", [f.id for f in self._all_fixtures], "全部设备")
            return

        group_by_id = {self._group_id(g): g for g in self._groups}
        selected_group_ids = [
            self._group_id(g)
            for g in self._groups
            if self._group_id(g) in self._selected_ids
        ]
        self._selected_id = selected_group_ids[-1] if selected_group_ids else None
        seen = set()
        merged_ids = []
        names = []
        for sid in selected_group_ids:
            sg = group_by_id.get(sid)
            if sg is None:
                continue
            names.append(self._group_name(sg))
            for fid in self._group_fixture_ids(sg):
                if fid not in seen:
                    merged_ids.append(fid)
                    seen.add(fid)
        out_gid = selected_group_ids[0] if len(selected_group_ids) == 1 else "__multi__"
        out_name = names[0] if len(names) == 1 else f"多选设备组（{len(names)}组）"
        self.group_selected.emit(out_gid, merged_ids, out_name)

    def clear_selection_state(self, all_checked: bool = False):
        self._selected_id = None
        self._selected_ids.clear()
        if hasattr(self, "_all_btn"):
            self._all_btn.setChecked(bool(all_checked))
        for btn in getattr(self, "_group_btns", []):
            try:
                btn.setChecked(False)
            except Exception:
                pass

    def sync_from_fixture_group_panel(self, panel):
        try:
            groups = panel.get_groups()
            all_fixtures = getattr(panel, "_all_fixtures", [])
            self.load_groups(groups, all_fixtures)
        except Exception as e:
            log.warning("GroupSelector sync failed: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  面板级共用辅助
# ══════════════════════════════════════════════════════════════════════════════

def _topview_driven(panel) -> bool:
    """本面板的选择是否由 TopView 驱动（或正被显式抑制）→ 不该回吐 TopView。

    ★ 2026-09-20 R121：抽成模块级函数 —— 内联两个 `or` 条件会把
      `_emit_fixture_selection_to_topview` 的 CC 顶到 9（门禁上限 8）。
    """
    return (str(getattr(panel, "_current_group_id", "") or "") == "__topview_live__"
            or bool(getattr(panel, "_suppress_topview_selection_emit", False)))


# ══════════════════════════════════════════════════════════════════════════════
#  CueButton  —  自映射 Cue 卡片（R182：静态存储半边已删）
# ══════════════════════════════════════════════════════════════════════════════
class CueButton(QPushButton):
    """CUE 卡片：八色数据色只落在左侧竖条（§4 保护），选中统一 select_blue 白字。"""

    def __init__(self, cue: CueData, parent=None):
        super().__init__(parent)
        self._cue = cue
        self.setFixedSize(96, 64)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(cue.name)
        c = cue.color
        self.setToolTip(f"{cue.name}\n左键=调用该 CUE；右键=发送到时间线/重命名/删除")
        self.setStyleSheet(
            f"QPushButton{{"
            f"background:{_hex_alpha(c, '26')};"
            f"border:1px solid {_hex_alpha(c, '44')};"
            f"border-left:3px solid {c};"
            f"color:{_th.TEXT_PRIMARY};border-radius:{_th.RADIUS}px;"
            f"font-size:11px;font-weight:bold;padding:3px 6px 3px 7px;"
            f"text-align:center;"
            f"}}"
            f"QPushButton:hover{{"
            f"background:{_hex_alpha(c, '40')};"
            f"border-left:3px solid {c};"
            f"}}"
            f"QPushButton:checked{{"
            f"background:{_th.SELECT_BLUE};"
            f"border:1px solid {_th.SELECT_BLUE};"
            f"border-left:3px solid {c};"
            f"color:{_th.TEXT_SELECTED_FG};"
            f"}}"
            f"QPushButton:checked:hover{{"
            f"background:#5480C5;border-left:3px solid {c};"
            f"}}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CueGrid  —  自映射 Cue 容器（静态 CUE 库卡片已随 R182 前端删除移除）
# ══════════════════════════════════════════════════════════════════════════════
class CueGrid(QWidget):
    cue_selected = pyqtSignal(object)
    cue_deleted  = pyqtSignal(str)
    cue_send_to_timeline = pyqtSignal(object)
    contents_changed = pyqtSignal()
    _COLORS = [
        "#4A90D9", "#E67E22", "#27AE60", "#8E44AD",
        "#E74C3C", "#16A085", "#2980B9", "#D35400",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cues: List[CueData] = []
        self._selected: Optional[str] = None
        self.setFixedHeight(140)
        self.setStyleSheet(
            f"background:{BG_PANEL};"
            f"border-top:1px solid {_th.BORDER_NORMAL};")
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_lbl = QLabel("自映射 CUE")
        title_lbl.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;"
            f"letter-spacing:1px;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        hint_lbl = QLabel("单击=加载映射  右键=加载/重命名/删除")
        hint_lbl.setToolTip("自映射页面发送来的映射 Cue；右键卡片可加载/重命名/删除")
        hint_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        title_row.addWidget(hint_lbl)
        outer.addLayout(title_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 滚动条四态外观由全局 QSS 统一提供（本页原重复定义已删除）
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._btn_w = QWidget()
        self._btn_w.setStyleSheet("background:transparent;")
        self._btn_lay = QHBoxLayout(self._btn_w)
        self._btn_lay.setContentsMargins(0, 0, 0, 0)
        self._btn_lay.setSpacing(6)
        self._btn_lay.addStretch()
        scroll.setWidget(self._btn_w)
        outer.addWidget(scroll)

    def add_cue(self, cue: CueData):
        self._cues.append(cue)
        self._rebuild()

    def _rebuild(self):
        while self._btn_lay.count():
            item = self._btn_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for cue in self._cues:
            btn = CueButton(cue)
            btn.setChecked(cue.id == self._selected)
            btn.clicked.connect(lambda checked, c=cue: self._on_cue_click(c))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, c=cue: self._ctx_menu(c, pos))
            self._btn_lay.addWidget(btn)

        # 空态：一条 CUE 都没有时给占位说明（禁止大片纯黑）
        if not self._cues:
            self._btn_lay.addWidget(EmptyState(
                "暂无自映射 CUE",
                "在自映射页面配置映射后发送到 Programmer；右键卡片可加载/重命名/删除",
                "playlist"))
        self._btn_lay.addStretch()
        self.contents_changed.emit()

    def _on_cue_click(self, cue: CueData):
        self._selected = cue.id
        self._rebuild()
        self.cue_selected.emit(cue)

    def _ctx_menu(self, cue: CueData, pos):
        menu = QMenu(self)
        # 右键菜单样式由全局 QMenu 统一提供，删除本页重复定义
        if not hasattr(cue, "self_map_rules"):
            menu.addAction("发送到时间线", lambda: self.cue_send_to_timeline.emit(cue))
            menu.addSeparator()
        else:
            menu.addAction("加载自映射 Cue", lambda: self.cue_selected.emit(cue))
            menu.addSeparator()
        menu.addAction("重命名", lambda: self._rename_cue(cue))
        menu.addAction("删除", lambda: self._delete_cue(cue))
        menu.exec(self.mapToGlobal(pos))

    def _rename_cue(self, cue: CueData):
        dlg = QDialog(self)
        dlg.setWindowTitle("重命名 Cue")
        dlg.setMinimumSize(280, 100)
        dlg.resize(320, 120)
        # QDialog 外观由全局主题提供
        lay = QVBoxLayout(dlg)
        edit = QLineEdit(cue.name)
        # QLineEdit 外观由全局主题提供
        lay.addWidget(edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cue.name = edit.text().strip() or cue.name
            self._rebuild()

    def _delete_cue(self, cue: CueData):
        self._cues = [c for c in self._cues if c.id != cue.id]
        self.cue_deleted.emit(cue.id)
        self._rebuild()

    def get_cues(self) -> List[CueData]:
        return list(self._cues)

    def clear_cues(self):
        self._cues.clear()
        self._selected = None
        self._rebuild()

    def next_color(self) -> str:
        idx = len(self._cues) % len(self._COLORS)
        return self._COLORS[idx]


# ══════════════════════════════════════════════════════════════════════════════
#  _SendToTimelineDialog
# ══════════════════════════════════════════════════════════════════════════════
class _SendToTimelineDialog(QDialog):
    def __init__(self, pm, cue: CueData, parent=None):
        super().__init__(parent)
        self._pm = pm
        self._cue = cue
        self.setWindowTitle(f"发送 Cue 到时间线 — {cue.name}")
        self.setMinimumSize(320, 220)
        self.resize(380, 260)
        self.setModal(True)
        # QDialog/QLabel/QGroupBox 外观由全局主题提供
        self._build_ui()

    def _build_ui(self):
        # D 波：浮窗外框（apply_dialog_chrome）+ PanelSection/prop_row
        # 版式；浮点时长参数换 NumSlider（s 后缀，右键弹数值输入）。
        from .widget_kit import apply_dialog_chrome
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        sec_track = _KitSection("目标轨道")
        self._track_combo = QComboBox()
        self._track_combo.setToolTip("Cue 将作为 Clip 插入该轨道")
        try:
            tracks = self._pm.project.tracks
            for t in tracks:
                self._track_combo.addItem(t.name, t.id)
        except Exception:
            pass
        sec_track.add_widget(prop_row("轨道", self._track_combo))
        lay.addWidget(sec_track)

        sec_time = _KitSection("时间参数")
        self._start_spin = NumSlider(0.0, 3600.0, 0.0, decimals=2, suffix="s")
        self._start_spin.setToolTip("Clip 在时间线上的起始时间（右键输入数值）")
        sec_time.add_widget(prop_row("起始时间", self._start_spin))
        self._dur_spin = NumSlider(0.1, 3600.0, 5.0, decimals=2, suffix="s")
        self._dur_spin.setToolTip("Clip 持续时长；CUE 帧按此长度重采样（最小 0.1s）")
        sec_time.add_widget(prop_row("持续时长", self._dur_spin))
        self._fade_spin = NumSlider(0.0, 60.0, 0.0, decimals=2, suffix="s")
        self._fade_spin.setToolTip("淡入时长：从上一动作现场值平滑过渡到本 CUE（电机类防甩跳）")
        sec_time.add_widget(prop_row("淡入时长", self._fade_spin))
        lay.addWidget(sec_time)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setObjectName("primary")
        if cancel_btn is not None:
            cancel_btn.setProperty("role", "cancel")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        apply_dialog_chrome(self)

    def get_params(self) -> dict:
        return {
            "track_id": self._track_combo.currentData(),
            "start": _ns_value(self._start_spin),
            "duration": _ns_value(self._dur_spin),
            "fade_in": _ns_value(self._fade_spin),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  ProgrammerPanel
# ══════════════════════════════════════════════════════════════════════════════
def _apply_recall_values(panel, vals, range_states, scope_ids=None,
                         touch_shape: bool = True) -> bool:
    """把静态值 + **条带形状**写回控件并**同步**下发（NM 在返回前即持有新值）。

    ★ 条带形状必须一起传（用户实报数据丢失）：不传则 `restore_static_snapshot` 走
      "无形状"分支，把每通道压成均值 flat → 存过的 `128-255` 条带调出来变成
      "平均值"，且写进 `_marked` 后**再存一次就永久丢失**（真实数据损失）。
    ★ 立刻 `_flush_live_output()`（用户实报保存区→监控区明显延迟）：本面板把 NM 写
      推迟到 33ms 的 `_live_output_timer`，而调用方紧接着同步调 `refresh_monitor()`
      → 那一刻 NM 还是旧值，监控区得等自己的值 tick（100/400ms）。提前 flush 后
      同步刷新即命中。
    ★★ touch_shape（2026-09-18 回访修正）：**纯播放**（场景调用）必须传 False。
      原调用点用 `(range_states if for_edit else None)` 表达"纯播放不还原形状"，
      但 `None` 在 Row 里等于"旧快照无形状"→ 走均值 flat 兜底 → 纯播放把用户
      正在编辑的 one_way 条带**压平**（实测 one_way/0/255 → flat/200/200，且
      再存一次场景就把压平后的形状写进存档）。"不还原"与"按均值兜底"是两种语义，
      必须分开表达：这里用显式布尔位，`None` 只留给真正的旧快照。
    """
    # ★★ 深查修正（recalled 被自己的还原路径抵消）：`restore_static_snapshot` 每行都发
    #   `fixture_layer_batch_changed` → `_on_layer_batch_changed`
    #   → `_release_active_cue_buffer_on_user_edit` → 清掉 `_recalled_play`。
    #   于是刚调用的格在 `report_to_bus` 前就被标回"可保存"，会被抄进新场景。
    #   复用面板既有的 `_cue_recall_in_progress` 护栏（=回灌信号是程序行为）。
    applied = _restore_guarded(panel, vals, range_states, scope_ids, touch_shape)
    # ★★ 2026-09-18 回访修正（R88 调用/编辑双语义引入的漏值）：纯播放**不切设备组**
    #   （`dispatch_recall_state` 的 for_edit=False 分支刻意跳过 `load_fixture_group`），
    #   而 `restore_static_snapshot` 只能写**当前行**里的设备（`row._fixtures`）→
    #   场景存的设备集与面板当前组不一致时，那些值被静默丢弃。
    #   实测（3 台设备 / 场景存 f0..f2 pump=128 / 面板切到 [f2] 组）：
    #     点场景后只有 f2 出值，f0/f1 输出为 0 —— "只播放效果"变成"只播一部分"，
    #     并连带打断"依次恢复性缓存"（回放的上一个场景若属别的组 → 写不出值）。
    #   行级还原保持不变（控件语义不动），这里把**行未覆盖的设备/通道**按地址级
    #   路径补写（复用面板既有 `_addr_cache`：`_rebuild_addr_cache` 刻意保留**全部**
    #   设备的映射，见其 v23 注释），因此不新增第二套地址语义。
    wrote_extra = _write_unloaded_recall_values(panel, vals)
    _flush_after_recall(panel, bool(vals) or bool(wrote_extra))
    return applied or bool(wrote_extra)


def _write_unloaded_recall_values(panel, vals) -> int:
    """把**当前行未承载**的存档值按地址级路径补写到输出层（纯播放漏值的兜底）。

    判据：`(fid, ch_type)` 是否落在某行的 (`_fixtures` × `_ch_types`) 里 ——
    落不到的值 `restore_static_snapshot` 一定写不到，必须在这里补。
    只写输出层缓存（`_layer_sparse_cache` / dirty），不碰 `_values/_marked/控件` ——
    与"纯播放不动页面"的语义一致。返回补写的设备数（0 = 无需补写）。
    """
    missing = _unloaded_recall_values(panel, vals)
    if not missing:
        return 0
    try:
        panel._mark_layer_sparse_from_fixture_values("fader", missing)
        panel._programmer_output_released = False
        return len(missing)
    except Exception:
        log.debug("programmer: failed to write unloaded recall values", exc_info=True)
        return 0


def _unloaded_recall_values(panel, vals) -> dict:
    """存档值里**哪一行都不承载**的 `{fid: {ch_type: val}}`（空 = 无漏值）。"""
    if not vals:
        return {}
    handled = _row_cell_keys(panel)
    missing = {}
    for fid, chs in (vals or {}).items():
        rest = {str(ct): val for ct, val in (chs or {}).items()
                if (str(fid), str(ct)) not in handled}
        if rest:
            missing[str(fid)] = rest
    return missing


def _row_cell_keys(panel) -> set:
    """面板当前所有行承载的 `(fid, ch_type)` 集合（行级还原的可达面）。"""
    out: set = set()
    for row in (getattr(getattr(panel, "_ch_list", None), "_rows", []) or []):
        ids = [str(getattr(f, "id", "") or "")
               for f in (getattr(row, "_fixtures", []) or [])]
        out.update((fid, str(ct)) for fid in ids
                   for ct in (getattr(row, "_ch_types", []) or []))
    return out


def _restore_guarded(panel, vals, range_states, scope_ids=None,
                     touch_shape: bool = True) -> bool:
    """在 `_cue_recall_in_progress` 护栏内还原静态值/条带形状（异常不抛出）。

    `touch_shape=False` = 纯播放：值照写，形状控件一律不碰（透传给 Row）。
    """
    applied = False
    try:
        panel._cue_recall_in_progress = True
    except Exception:
        pass
    try:
        if vals:
            applied = bool(panel._ch_list.restore_static_snapshot(
                vals, range_states, scope_ids, touch_shape=touch_shape))
            panel._programmer_output_released = False
            panel._schedule_live_output({})
        elif range_states and touch_shape:
            panel._ch_list.restore_range_states(range_states)   # 只有形状（罕见）
            applied = True
    except Exception:
        log.debug("programmer: restore values/range_states failed", exc_info=True)
    finally:
        try:
            panel._cue_recall_in_progress = False
        except Exception:
            pass
    return applied


def _reattach_recalled_cells(panel, values: dict) -> None:
    """Edit recall reattaches only cells present in the recalled snapshot."""
    rows = getattr(getattr(panel, "_ch_list", None), "_rows", []) or []
    keys = {(str(fid), str(ct)) for fid, channels in values.items()
            for ct in channels}
    for row in rows:
        row._detached_cells.difference_update(keys)


def _finish_programmer_recall(panel, values: dict, for_edit: bool) -> None:
    if for_edit:
        _rearm_output_after_edit(panel)
        _reattach_recalled_cells(panel, values)
    panel._recalled_play = True
    panel._recalled_output_values = values
    panel.report_to_bus(force=True)


def _flush_after_recall(panel, had_vals: bool) -> None:
    """立刻 flush 一次（去监控区延迟）+ 需要时起 keepalive。"""
    try:
        panel._flush_live_output()
    except Exception:
        log.debug("programmer: immediate flush after recall failed", exc_info=True)
    try:
        if had_vals and not panel._keepalive_timer.isActive():
            panel._keepalive_timer.start()
    except Exception:
        pass


def _restore_range_states(panel, state: dict) -> bool:
    """按 `range_states` 还原条带形状（mode/base/target）；有还原到返回 True。

    ★ 键为 `def_id|ch_type`（见 `_range_state_key`），兼容旧的仅 ch_type 格式。
    ★ 2026-09-17 范围门卫：按存档作用域（`group_ids`）过滤 —— 隐藏的旧设备类型行
      不得把越界的条带形状写回面板（与 `static_values` 同一根因）。
    """
    rs = (state or {}).get("range_states") or {}
    if not rs:
        return False
    scope = _recall_scope_ids((state or {}).get("group_ids"))
    rs = _scope_range_states(rs, _def_ids_for(panel, scope))
    try:
        ch = getattr(panel, "_ch_list", None)
        return any(_apply_row_ranges(row, rs) for row in (getattr(ch, "_rows", []) or []))
    except Exception:
        return False


def _apply_row_ranges(row, rs) -> bool:
    """把 `rs` 里属于该行的条带形状写回该行控件；写到了返回 True。"""
    did = str(getattr(row, "_def_id", "") or "")
    done = False
    for ct, st in _iter_range_states(rs, row._ch_types, did):
        try:
            row._set_range_state(str(ct), str(st.get("mode", "flat")),
                                 int(st.get("base", 0)), int(st.get("target", 0)))
            done = True
        except Exception:
            continue
    return done


def _recall_node_from_state(state: dict) -> dict:
    """把 `save_state` 产物转成 recall 用的 node。

    ★★ 深查修正（工程往返丢设备作用域）：`dispatch_recall_state` 从
      `node["fixture_ids"]` 取设备集并据此切组，原实现**没填**该键 → 工程加载路径
      永远不切组，存下的设备组/顺序重开工程即丢（实报："我存的组没了"）。
      场景（调度台）路径不受影响：那条路的 node 自带 fixture_ids。
    """
    ids = [str(x) for x in (state.get("group_ids") or []) if str(x)]
    return {"static_values": state.get("static_values") or {},
            "fixture_ids": ids, "group_ids": ids,
            "base_order_ids": state.get("base_order_ids") or [],
            "group_name": state.get("group_name") or ""}


def _row_state(row):
    """单行的翼/块/方向状态（取不到给 None，由调用方过滤）。

    ★ 翼/块/翼方向是**每通道类型行**的状态（ChannelTypeRow）；单控/整体在 GroupSelector。
    """
    try:
        return {
            "group_name": str(getattr(row, "_group_name", "") or ""),
            "def_id": str(getattr(row, "_def_id", "") or ""),
            "ch_types": [str(t) for t in (getattr(row, "_ch_types", []) or [])],
            "wings": int(getattr(row, "_value_wings", 1) or 1),
            "block_size": int(getattr(row, "_value_block_size", 1) or 1),
            "wing_direction": str(getattr(row, "_value_wing_direction", "") or ""),
            **_wing_flags.values(row, "_value_"),
        }
    except Exception:
        return None


def _programmer_node(ct, vals, ids, ctx, recalled):
    """构造 programmer 的一格节点（一通道类型一节点）。

    `ctx` = (group_key, group_name, group_id)。
    ★ ch_types：只有**通道类型**（pump/valve/…）才是本效果真正作用的通道 ——
      带上它，监控区就不会把某个通道的效果糊满整行（见 cell_enrich 的按通道过滤）。
    ★ extra["recalled"]：只是被场景调用带活的 → 保存新场景时排除（用户 2026-09-14）。
    ★★ 2026-09-20 用户实报修复（"TopView 里选了母组里的子集，给泵值，调度台存下来
      变成整个组都有值"）：节点作用域必须 = **实际有值的设备**，不能取 `ids`
      （= 母组基准序 ∪ 当前子集）。取 ids 会让"子集效果"在监控区/场景里**扩成整组**
      （组内没做值的设备也被算进效果范围 → 调用时整组一起动）。
      组身份仍由 group_key/group_id 表达（那是"来自哪个组"，不是"作用于哪些设备"）。
    """
    from ..processing.dispatch import EffectNode
    group_key, group_name, group_id = ctx
    scope_ids = _order_members_by_base_seq(list(vals), ids) if vals else list(ids or [])
    return EffectNode(
        panel="programmer", track_type=str(ct), group_key=group_key,
        group_name=group_name, fixture_ids=scope_ids,
        effect_id="", effect_name="静态通道", category="",
        layers=["programmer"], summary=f"通道 {ct} · {len(vals)} 个值", live=True,
        group_id=group_id, ch_types=[str(ct)], extra={"recalled": recalled})


def _order_members_by_base_seq(member: list, base_sequence_ids: list) -> list:
    """保留旧接口，所有页面复用同一个成员/母序合同。"""
    from src.core.fixture_selection import ordered_members
    return ordered_members(member, base_sequence_ids)


class ProgrammerPanel(QWidget):
    # ★ 调度台·控件轨迹汇口：条带值/条带形/摇杆 xy·xyz/摇杆幅度/Z 同步，
    #   全部经面板既有 handler 转发（hub 只连这一个信号）。
    controlTrajectoryEvent = pyqtSignal(str, object)
    """Programmer 主面板"""
    # ★ 新增：统一 DMX 输出信号 (universe, data)
    dmx_out = pyqtSignal(int, np.ndarray)
    fixture_selection_changed = pyqtSignal(list)
    # ★ R173: 状态栏反馈信号 (message, timeout_ms)
    status_feedback = pyqtSignal(str, int)

    # ★ R38-D4：把"当前选择"改成带版本号的属性。
    #   effect_browser._effective_output_scope_ids 需要一个"廉价且真实"的键来
    #   缓存输出设备集。R36 用的是 id(pw._current_fixtures_selected)，两个致命
    #   问题：①这两个列表每次都是整体重新绑定的新对象，id 每次都变 → 缓存 0
    #   命中，等于没缓存（这正是"打开 Programmer 后全局 PPT"的放大器）；
    #   ②更危险的是 id 会被回收复用——旧列表释放后新列表可能拿到同一个 id，
    #   于是缓存"命中"了一份**别的选择**的结果 → 输出到错误的设备（P0，比卡顿
    #   严重得多）。版本号单调递增，这两个问题一起消失。
    #   做成 property 而不是在 9 个赋值点手动 +1：全文件已确认没有任何就地修改
    #   （append/extend/...），所有写入都是整体重绑，所以 setter 能 100% 覆盖，
    #   而且以后谁再加一个赋值点也不会漏。漏一个 = 陈旧作用域 = 输出到错误设备。
    _live_selection_version = 0

    @property
    def _current_fixtures_selected(self):
        return self.__dict__.get("_cfs_store", [])

    @_current_fixtures_selected.setter
    def _current_fixtures_selected(self, value):
        self.__dict__["_cfs_store"] = value
        self._live_selection_version = int(self._live_selection_version) + 1

    @property
    def _current_base_order_ids(self):
        return self.__dict__.get("_cboi_store", [])

    @_current_base_order_ids.setter
    def _current_base_order_ids(self, value):
        self.__dict__["_cboi_store"] = value
        self._live_selection_version = int(self._live_selection_version) + 1

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self._pm = project
        self._current_fixtures = []
        self._current_fixtures_selected = []
        self._fixture_defs_cache: Dict[str, object] = {}
        self._current_group_id = "__all__"
        self._current_group_name = ""
        self._current_base_order_ids: List[str] = []
        # ★ 包排列模式（纯展示整理）：包内子设备按文件夹嵌套显示
        self._pkg_arrange: bool = False
        self._pkg_of: Dict[str, dict] = {}
        self._topview_live_fixture_ids: List[str] = []
        self._topview_live_base_order_ids: List[str] = []
        self._topview_ref = None
        self._suppress_topview_selection_emit = False
        self._fixture_positions: Dict[str, Tuple[float, float]] = {}
        # 全局静态缓冲（跨组持久；R182 起前端 CUE 库已删，保留字段供既有
        # `load_fixture_group` 回灌路径与测试兼容，恒为空 dict 时行为不变）。
        self._active_cue_buffer: Dict[str, Dict[str, int]] = {}
        # 程序回灌期间为 True:此时 restore_static_snapshot 触发的 batch 信号是
        # "程序回灌"（场景调用/恢复），不算用户操作,不得清 `_recalled_play` 标记。
        self._cue_recall_in_progress: bool = False
        self._nm_ref = None
        self._timeline_ctrl_ref = None
        self._live_output_timer = QTimer(self)
        self._live_output_timer.setSingleShot(True)
        self._live_output_timer.setInterval(33)
        self._live_output_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._live_output_timer.timeout.connect(self._flush_live_output)
        # _pending_live: {universe: {addr: value}}（dict 让我们能精确跟踪 active 通道，含 0）
        self._pending_live: Dict[int, Dict[int, int]] = {}
        # ★ 性能：输入事件累积 {layer: {fid: {ct: value}}}，地址转换在 33ms flush
        #   统一执行（见 _on_layer_batch_changed / _flush_live_output）。
        self._pending_layer_batch: Dict[str, Dict[str, Dict[str, int]]] = {}
        # ★ 性能：虚拟亮度基色采样缓存 {fid: (version, colors)}——颜色写入/切组时
        #   按 _ch_list._vd_color_version 失效；拖动 dimmer 不再每 flush 逐台采样 4 色。
        self._vd_color_cache: Dict[str, tuple] = {}
        # ★ 性能：层稀疏缓存版本号——无新写入时 _send_keepalive 跳过全量重发
        #   （NM stop() 会清空 _layers，空层守卫强制补发，输出不丢）。
        self._layer_sparse_version: int = 0
        # 展开网格 body 单元重绘的合并队列：{(fid, ch_type): merged_value}。
        # 拖推杆时每像素对 N 设备 setValue 重绘是纯视觉开销，权威值已同步写入 _values。
        # 把重绘攒到 _flush_live_output(33ms)统一刷，拖动期视觉最多延迟 33ms(无感),
        # 最终值正确,与 NM 输出同节流。
        self._pending_body_cells: Dict[Tuple[str, str], int] = {}
        # _keepalive_data: {layer_name: {universe: {addr: value}}}
        # ★ v22 fix: 改为按源层（fader/joystick/audio）拆分，避免 keepalive
        #   把 SHAPE 形状层数据回写到 NM 'fader' BASE 层污染基线。
        self._keepalive_data: Dict[str, Dict[int, Dict[int, int]]] = {}
        # 深度调优：按 DMX 地址维护 Programmer 各源层的增量缓存。
        # _layer_sparse_cache 是当前完整 Programmer 层状态，_dirty_layer_sparse 只保存本轮变更。
        # 这样 _flush_live_output 不再需要每 33ms 扫描所有 Row / Fixture / Channel。
        self._layer_sparse_cache: Dict[str, Dict[int, Dict[int, int]]] = {
            "fader": {}, "joystick": {}, "audio": {}, "matrix": {}
        }
        self._dirty_layer_sparse: Dict[str, Dict[int, Dict[int, int]]] = {
            "fader": {}, "joystick": {}, "audio": {}, "matrix": {}
        }
        self._addr_cache: Dict[str, Dict[str, tuple]] = {}
        self._addr_output_cache: Dict[str, Dict[str, tuple]] = {}
        self._addr_cache_signature_cache: Optional[tuple] = None
        self._audio_color_rgb: tuple = (255, 68, 68)  # 音频颜色模式预设颜色 (R,G,B)
        self._spec_shuffle_map: list = []  # 频谱分段随机洗牌缓存
        self._last_spectrum_ui_frame_t = 0.0
        self._spectrum_ui_frame_interval = SPECTRUM_UI_FRAME_INTERVAL
        self._keepalive_timer = QTimer(self)
        self._keepalive_timer.setInterval(100)
        # 平滑输出模式下本定时器会被改为 25ms（见 _on_smooth_preset_changed），
        # 该路径需要精确定时以免平滑插值抖动；构造即设 PreciseTimer 覆盖两种间隔。
        self._keepalive_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._keepalive_timer.timeout.connect(self._send_keepalive)
        self._record_frame_layers = ("fader", "joystick", "audio", "matrix")
        self._keepalive_layers = ("fader", "joystick", "audio")
        self._programmer_output_released = False
        # ── 轨迹曲线 状态 ───────────────────────────────────────────────
        self._engine_ref = None           # PlaybackEngine（主窗注入；轨迹复用其 per_shape 分段）
        self._traj_selected_id: Optional[str] = None
        self._traj_playing = False
        self._traj_play_t = 0.0           # 当前播放时间（秒）
        self._traj_last_tick = 0.0        # 上一帧 monotonic
        self._traj_play_timer = QTimer(self)
        self._traj_play_timer.setInterval(33)
        self._traj_play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._traj_play_timer.timeout.connect(self._traj_on_play_tick)
        self._traj_rec_active = False
        self._traj_rec_samples: List[List[float]] = []
        self._traj_rec_t0 = 0.0
        self._traj_rec_timer = QTimer(self)
        self._traj_rec_timer.setInterval(50)
        self._traj_rec_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._traj_rec_timer.timeout.connect(self._traj_on_rec_tick)
        # ── 外接摇杆绑定 ────────────────────────────────────────────────
        self._joystick_binding_mode = False
        self._binding_mgr_dialog = None
        self._xy_joy_cache: Dict[str, float] = {}
        _r183_init_state(self)   # R183 批次 I：深查 17 项新增状态
        finalize_programmer_init(self)

    def _build_ui(self):
        _pwui.build_workspace(self, GroupSelector, ChannelTypeList, CueGrid)

    def _toggle_programmer_curtain(self, checked: bool):
        target = getattr(self, "_programmer_scroll", None) or getattr(self, "_main_body_widget", None)
        if target is not None:
            target.setVisible(bool(checked))
        if hasattr(self, "_curtain_btn"):
            self._curtain_btn.setText("展开Programmer主体" if checked else "Programmer主体已折叠")

    # ════════════════════════════════════════════════════════════════════
    #  轨迹曲线子页面
    # ════════════════════════════════════════════════════════════════════
    _TRAJ_CH_LABELS = {
        "dimmer": "明暗", "R": "红", "G": "绿", "B": "蓝", "W": "白",
        "motor_x": "电机X", "motor_y": "电机Y", "motor_z": "电机Z",
        "valve": "阀", "pump": "泵",
    }
    _TRAJ_CH_ORDER = ["dimmer", "R", "G", "B", "W",
                      "motor_x", "motor_y", "motor_z", "valve", "pump"]

    def _traj_ch_label(self, ch: str) -> str:
        return f"{self._TRAJ_CH_LABELS.get(ch, ch)} [{ch}]"

    def _build_trajectory_page(self) -> QWidget:
        # 轨迹页控件族：四态外观全部由全局 QSS 提供；此处仅保留紧凑特化。
        # （原页面级 QGroupBox 覆盖样式 _GRP 已删除——分组改用 widget_kit.PanelSection）
        _BTN = "QPushButton{font-size:11px;padding:3px 8px;}"
        _SPIN = "QSpinBox,QDoubleSpinBox{padding:2px;}"
        _COMBO = "QComboBox{padding:2px 6px;}"

        # ── 三栏主体：横向 QSplitter（可拖各栏宽度）；整页 QScrollArea 可滚动浏览 ──
        body = QWidget()
        body.setMinimumWidth(1190)          # 三栏总最小宽(左~190+中~560+右~250+把手)：小窗整页横向滚动而非挤压
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(8, 8, 8, 8)
        body_lay.setSpacing(0)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.setChildrenCollapsible(False)   # 不允许把某栏拖没（防误操作丢失栏目）
        hsplit.setHandleWidth(6)
        body_lay.addWidget(hsplit, 1)

        # ════ 左栏：曲线列表 + 管理按钮 ════
        left = QVBoxLayout(); left.setSpacing(6)
        ttl = QLabel("曲线列表")
        ttl.setStyleSheet(f"color:{FG_ACCENT};font-size:12px;font-weight:bold;letter-spacing:1px;")
        left.addWidget(ttl)
        self._traj_list = QListWidget()
        self._traj_list.setMinimumWidth(150)
        # 列表外观（含选中态 select_blue 白字）走全局 QSS；保留条目密度特化
        self._traj_list.setStyleSheet("QListWidget::item{padding:5px 6px;}")
        self._traj_list.currentItemChanged.connect(lambda *_: self._traj_on_list_selected())
        left.addWidget(self._traj_list, 1)
        lbtns = QGridLayout(); lbtns.setSpacing(4)
        def _mk(label, slot):
            b = QPushButton(label)
            b.setStyleSheet(_BTN)
            b.setFixedHeight(24)
            b.clicked.connect(slot)
            return b
        new_b = make_icon_button("circle-plus", "新建", "新建一条路径/造型曲线")
        new_b.clicked.connect(self._traj_on_new)
        ren_b = make_icon_button("pencil", "重命名", "重命名选中的曲线")
        ren_b.clicked.connect(self._traj_on_rename)
        del_b = make_icon_button("trash", "删除", "删除选中的曲线")
        del_b.clicked.connect(self._traj_on_delete)
        dup_b = make_icon_button("copy", "复制", "复制选中曲线为副本")
        dup_b.clicked.connect(self._traj_on_duplicate)
        imp_b = make_icon_button("file-import", "导入", "从 .curve.json 文件导入曲线")
        imp_b.clicked.connect(self._traj_on_import)
        exp_b = make_icon_button("file-export", "导出", "把选中曲线导出为 .curve.json")
        exp_b.clicked.connect(self._traj_on_export)
        lbtns.addWidget(new_b, 0, 0); lbtns.addWidget(ren_b, 0, 1)
        lbtns.addWidget(del_b, 1, 0); lbtns.addWidget(dup_b, 1, 1)
        lbtns.addWidget(imp_b, 2, 0); lbtns.addWidget(exp_b, 2, 1)
        left.addLayout(lbtns)
        left_w = QWidget(); left_w.setLayout(left); left_w.setMinimumWidth(190)
        hsplit.addWidget(left_w)

        # ════ 中栏：编辑（模式栏 / 大绘图 / 录制 / 段·边界设置 / transport）════
        # 中栏自身用纵向 QSplitter：上=绘图(可拖高)，下=控件区。工具栏/提示固定在最上。
        mid_w = QWidget()
        mid = QVBoxLayout(mid_w); mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(7)

        # 模式工具栏 + 类型标签 + 时长（工具行统一 24px、分组间 8px）
        tools = QHBoxLayout(); tools.setSpacing(8)
        self._traj_type_lbl = QLabel("—")
        self._traj_type_lbl.setToolTip("当前曲线类型：路径 / 造型")
        self._traj_type_lbl.setStyleSheet(
            f"color:{_th.TEXT_SELECTED_FG};background:{_th.SELECT_BLUE};"
            f"border:1px solid {_th.SELECT_BLUE};border-radius:{_th.RADIUS}px;"
            "padding:3px 10px;font-size:11px;font-weight:bold;")
        tools.addWidget(self._traj_type_lbl)
        self._traj_mode_select_btn = make_icon_button(
            "pointer", "选择", "路径：选段设方向/翼/块；造型：选段设静态造型、选分割线/首尾设过渡",
            checkable=True)
        self._traj_mode_select_btn.setChecked(True)
        self._traj_mode_point_btn = make_icon_button(
            "pencil-plus", "描点", "在曲线上加点（左键），拖动移动，右键删除点", checkable=True)
        self._traj_mode_cut_btn = make_icon_button(
            "scissors", "分段", "点击曲线竖切一刀分段；点击分界线可删除该分界", checkable=True)
        self._traj_mode_select_btn.clicked.connect(lambda: self._traj_set_mode("select"))
        self._traj_mode_point_btn.clicked.connect(lambda: self._traj_set_mode("point"))
        self._traj_mode_cut_btn.clicked.connect(lambda: self._traj_set_mode("cut"))
        tools.addWidget(self._traj_mode_select_btn)
        tools.addWidget(self._traj_mode_point_btn)
        tools.addWidget(self._traj_mode_cut_btn)
        clear_cut_btn = make_icon_button("eraser", "清除分段", "删除当前曲线全部分段线")
        clear_cut_btn.clicked.connect(self._traj_on_clear_cuts)
        tools.addWidget(clear_cut_btn)
        tools.addStretch()
        dur_lab = _p_label("时长"); dur_lab.setToolTip("曲线总时长；录制亦以此时长为准")
        tools.addWidget(dur_lab)
        # 浮点时长参数换 widget_kit.NumSlider（s 后缀，右键弹数值输入）
        self._traj_dur_spin = NumSlider(0.1, 3600.0, 4.0, decimals=1, suffix="s")
        self._traj_dur_spin.setFixedWidth(110)
        self._traj_dur_spin.setToolTip(
            "曲线总时长（秒）：拖动调值，滚轮微调，右键直接输入数值")
        self._traj_dur_spin.valueChanged.connect(
            lambda *_i: self._traj_on_duration_changed(_ns_value(self._traj_dur_spin)))
        tools.addWidget(self._traj_dur_spin)
        mid.addLayout(tools)

        self._traj_hint = QLabel("")
        self._traj_hint.setWordWrap(True); self._traj_hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        mid.addWidget(self._traj_hint)

        # 纵向 QSplitter：上=大绘图(可拖高)，下=控件区(录制/段/边界/transport)。
        mid_split = QSplitter(Qt.Orientation.Vertical)
        mid_split.setChildrenCollapsible(False)
        mid_split.setHandleWidth(6)
        mid.addWidget(mid_split, 1)

        # 大绘图（放在 splitter 上半，可拖拽改高）
        self._traj_view = TrajectoryCurveView()
        self._traj_view.setMinimumHeight(150)
        self._traj_view.points_changed.connect(self._traj_on_curve_changed)
        self._traj_view.segment_selected.connect(self._traj_on_segment_selected)
        self._traj_view.boundary_selected.connect(self._traj_on_boundary_selected)
        mid_split.addWidget(self._traj_view)

        # 控件区容器（splitter 下半，内部可滚动避免被拖太矮时挤压）
        ctrl_w = QWidget()
        ctrl_lay = QVBoxLayout(ctrl_w); ctrl_lay.setContentsMargins(0, 0, 0, 0); ctrl_lay.setSpacing(7)
        mid_split.addWidget(ctrl_w)
        mid_split.setStretchFactor(0, 1)   # 绘图优先占多余空间
        mid_split.setStretchFactor(1, 0)

        # 推杆录制 → DAW 自动化录制（四模式 + 录制源；整段构建搬入独立模块，本类只接线）
        ctrl_lay.addWidget(build_recording_section(self))

        # ── 段设置（路径：方向/翼/块/scope/分组/时序；造型：静态 scope/翼/块）──
        self._traj_seg_grp = _KitSection("当前段设置")
        sr1 = QHBoxLayout(); sr1.setContentsMargins(0, 0, 0, 0); sr1.setSpacing(8)
        self._traj_seg_lbl = QLabel("段 —")
        self._traj_seg_lbl.setStyleSheet(f"color:{FG_ACCENT};font-size:11px;font-weight:bold;")
        sr1.addWidget(self._traj_seg_lbl)
        self._traj_seg_dir_lbl = _p_label("方向")
        sr1.addWidget(self._traj_seg_dir_lbl)
        self._traj_seg_dir = QComboBox(); self._traj_seg_dir.setStyleSheet(_COMBO)
        self._traj_seg_dir.setFixedHeight(24)
        self._traj_seg_dir.setToolTip("本段的读数方向（整段/左→右/右→左/…）")
        for d in SEGMENT_DIRECTIONS:
            self._traj_seg_dir.addItem(DIRECTION_LABELS.get(d, d), d)
        self._traj_seg_dir.currentIndexChanged.connect(self._traj_on_seg_changed)
        sr1.addWidget(self._traj_seg_dir)
        wing_lab_s = _p_label("翼")
        wing_lab_s.setToolTip("把本段设备序列分成 N 个同步翼")
        sr1.addWidget(wing_lab_s)
        self._traj_seg_wing = QSpinBox(); self._traj_seg_wing.setRange(1, 32); self._traj_seg_wing.setStyleSheet(_SPIN)
        self._traj_seg_wing.setFixedHeight(22); self._traj_seg_wing.setFixedWidth(52)
        self._traj_seg_wing.valueChanged.connect(self._traj_on_seg_changed)
        sr1.addWidget(self._traj_seg_wing)
        blk_lab_s = _p_label("块")
        blk_lab_s.setToolTip("每 N 台共用一个插值位")
        sr1.addWidget(blk_lab_s)
        self._traj_seg_block = QSpinBox(); self._traj_seg_block.setRange(1, 64); self._traj_seg_block.setStyleSheet(_SPIN)
        self._traj_seg_block.setFixedHeight(22); self._traj_seg_block.setFixedWidth(52)
        self._traj_seg_block.valueChanged.connect(self._traj_on_seg_changed)
        sr1.addWidget(self._traj_seg_block)
        sr1.addWidget(_wing_flags.create(self, self._traj_on_seg_changed, "_traj_seg_"))
        sr1.addStretch()
        self._traj_seg_grp.add_layout(sr1)

        sr2 = QHBoxLayout(); sr2.setContentsMargins(0, 0, 0, 0); sr2.setSpacing(8)
        scope_lab_s = _p_label("坐标系")
        scope_lab_s.setToolTip("各自坐标系=每个 shape 子组独立；整体坐标系=全组一条；跟随=按全局灯控开关")
        sr2.addWidget(scope_lab_s)
        self._traj_seg_scope = QComboBox(); self._traj_seg_scope.setStyleSheet(_COMBO)
        self._traj_seg_scope.setFixedHeight(24)
        self._traj_seg_scope.setToolTip("各自坐标系=每个 shape 子组独立；整体坐标系=全组一条；跟随=按全局灯控开关")
        for sc in TRAJ_SPATIAL_SCOPES:
            self._traj_seg_scope.addItem(TRAJ_SCOPE_LABELS.get(sc, sc), sc)
        self._traj_seg_scope.currentIndexChanged.connect(self._traj_on_seg_changed)
        sr2.addWidget(self._traj_seg_scope)
        self._traj_seg_groups_lbl = _p_label("分组")
        sr2.addWidget(self._traj_seg_groups_lbl)
        self._traj_seg_groups = QSpinBox(); self._traj_seg_groups.setRange(1, 64); self._traj_seg_groups.setStyleSheet(_SPIN)
        self._traj_seg_groups.setFixedHeight(22); self._traj_seg_groups.setFixedWidth(52)
        self._traj_seg_groups.valueChanged.connect(self._traj_on_seg_changed)
        sr2.addWidget(self._traj_seg_groups)
        self._traj_seg_mode = QComboBox(); self._traj_seg_mode.setStyleSheet(_COMBO)
        self._traj_seg_mode.setFixedHeight(24)
        self._traj_seg_mode.setToolTip("分组播放时序：并行 / 顺序")
        for gm in TRAJ_GROUP_MODES:
            self._traj_seg_mode.addItem(TRAJ_GROUP_MODE_LABELS.get(gm, gm), gm)
        self._traj_seg_mode.currentIndexChanged.connect(self._traj_on_seg_changed)
        sr2.addWidget(self._traj_seg_mode)
        self._traj_seg_pause_lbl = _p_label("过渡间隔(s)")
        self._traj_seg_pause_lbl.setToolTip("顺序分组时，各批之间插入的过渡间隔（秒）")
        sr2.addWidget(self._traj_seg_pause_lbl)
        self._traj_seg_pause = NumSlider(0.0, 60.0, 0.0, decimals=1, suffix="s")
        self._traj_seg_pause.setFixedWidth(96)
        self._traj_seg_pause.setToolTip("顺序分组时，各批之间插入的过渡间隔（秒）")
        self._traj_seg_pause.valueChanged.connect(
            lambda *_i: self._traj_on_seg_changed())
        sr2.addWidget(self._traj_seg_pause)
        sr2.addStretch()
        self._traj_seg_grp.add_layout(sr2)
        ctrl_lay.addWidget(self._traj_seg_grp)

        # ── 边界 morph 设置（仅造型曲线；选分割线/起点/终点时显示）──
        self._traj_bnd_grp = _KitSection("过渡设置（造型：分割线 / 起点淡入 / 终点淡出）")
        br1 = QHBoxLayout(); br1.setContentsMargins(0, 0, 0, 0); br1.setSpacing(8)
        self._traj_bnd_lbl = QLabel("过渡 —")
        self._traj_bnd_lbl.setStyleSheet(f"color:{_th.BRIGHT_WARN};font-size:11px;font-weight:bold;")
        br1.addWidget(self._traj_bnd_lbl)
        trans_lab = _p_label("过渡时间(s)")
        trans_lab.setToolTip("0=瞬切；>0 时在分割线周围（蚕食相邻两段、不增总时长）做 morph")
        br1.addWidget(trans_lab)
        self._traj_bnd_trans = NumSlider(0.0, 600.0, 0.0, decimals=1, suffix="s")
        self._traj_bnd_trans.setFixedWidth(104)
        self._traj_bnd_trans.setToolTip("0=瞬切；>0 时在分割线周围（蚕食相邻两段、不增总时长）做 morph")
        self._traj_bnd_trans.valueChanged.connect(
            lambda *_i: self._traj_on_bnd_changed())
        br1.addWidget(self._traj_bnd_trans)
        dir_lab_s = _p_label("方向")
        br1.addWidget(dir_lab_s)
        self._traj_bnd_dir = QComboBox(); self._traj_bnd_dir.setStyleSheet(_COMBO)
        self._traj_bnd_dir.setFixedHeight(24)
        self._traj_bnd_dir.setToolTip("morph 的扫描方向")
        for d in SEGMENT_DIRECTIONS:
            self._traj_bnd_dir.addItem(DIRECTION_LABELS.get(d, d), d)
        self._traj_bnd_dir.currentIndexChanged.connect(self._traj_on_bnd_changed)
        br1.addWidget(self._traj_bnd_dir)
        wing_lab_b = _p_label("翼")
        wing_lab_b.setToolTip("morph 过程中的翼分组")
        br1.addWidget(wing_lab_b)
        self._traj_bnd_wing = QSpinBox(); self._traj_bnd_wing.setRange(1, 32); self._traj_bnd_wing.setStyleSheet(_SPIN)
        self._traj_bnd_wing.setFixedHeight(22); self._traj_bnd_wing.setFixedWidth(52)
        self._traj_bnd_wing.valueChanged.connect(self._traj_on_bnd_changed)
        br1.addWidget(self._traj_bnd_wing)
        blk_lab_b = _p_label("块")
        blk_lab_b.setToolTip("morph 过程中的块分组")
        br1.addWidget(blk_lab_b)
        self._traj_bnd_block = QSpinBox(); self._traj_bnd_block.setRange(1, 64); self._traj_bnd_block.setStyleSheet(_SPIN)
        self._traj_bnd_block.setFixedHeight(22); self._traj_bnd_block.setFixedWidth(52)
        self._traj_bnd_block.valueChanged.connect(self._traj_on_bnd_changed)
        br1.addWidget(self._traj_bnd_block)
        br1.addWidget(_wing_flags.create(self, self._traj_on_bnd_changed, "_traj_bnd_"))
        br1.addStretch()
        self._traj_bnd_grp.add_layout(br1)

        br2 = QHBoxLayout(); br2.setContentsMargins(0, 0, 0, 0); br2.setSpacing(8)
        scope_lab_b = _p_label("坐标系")
        sr2_tip = "各自坐标系=每个 shape 子组独立；整体坐标系=全组一条"
        scope_lab_b.setToolTip(sr2_tip)
        br2.addWidget(scope_lab_b)
        self._traj_bnd_scope = QComboBox(); self._traj_bnd_scope.setStyleSheet(_COMBO)
        self._traj_bnd_scope.setFixedHeight(24)
        self._traj_bnd_scope.setToolTip(sr2_tip)
        for sc in TRAJ_SPATIAL_SCOPES:
            self._traj_bnd_scope.addItem(TRAJ_SCOPE_LABELS.get(sc, sc), sc)
        self._traj_bnd_scope.currentIndexChanged.connect(self._traj_on_bnd_changed)
        br2.addWidget(self._traj_bnd_scope)
        grp_lab_b = _p_label("分组")
        grp_lab_b.setToolTip("morph 分成的批数")
        br2.addWidget(grp_lab_b)
        self._traj_bnd_groups = QSpinBox(); self._traj_bnd_groups.setRange(1, 64); self._traj_bnd_groups.setStyleSheet(_SPIN)
        self._traj_bnd_groups.setFixedHeight(22); self._traj_bnd_groups.setFixedWidth(52)
        self._traj_bnd_groups.valueChanged.connect(self._traj_on_bnd_changed)
        br2.addWidget(self._traj_bnd_groups)
        self._traj_bnd_mode = QComboBox(); self._traj_bnd_mode.setStyleSheet(_COMBO)
        self._traj_bnd_mode.setFixedHeight(24)
        self._traj_bnd_mode.setToolTip("分批 morph 的时序：并行 / 顺序")
        for gm in TRAJ_GROUP_MODES:
            self._traj_bnd_mode.addItem(TRAJ_GROUP_MODE_LABELS.get(gm, gm), gm)
        self._traj_bnd_mode.currentIndexChanged.connect(self._traj_on_bnd_changed)
        br2.addWidget(self._traj_bnd_mode)
        pause_lab_b = _p_label("过渡间隔(s)")
        pause_lab_b.setToolTip("顺序分批 morph 时，各批之间的间隔秒数")
        br2.addWidget(pause_lab_b)
        self._traj_bnd_pause = NumSlider(0.0, 60.0, 0.0, decimals=1, suffix="s")
        self._traj_bnd_pause.setFixedWidth(96)
        self._traj_bnd_pause.valueChanged.connect(
            lambda *_i: self._traj_on_bnd_changed())
        br2.addWidget(self._traj_bnd_pause)
        br2.addStretch()
        self._traj_bnd_grp.add_layout(br2)
        ctrl_lay.addWidget(self._traj_bnd_grp)

        # transport（播放/停止按钮与全局工具行同高）
        tp = QHBoxLayout(); tp.setSpacing(8)
        self._traj_play_btn = make_icon_button("player-play", "播放", "播放/暂停当前曲线")
        self._traj_play_btn.clicked.connect(self._traj_on_play_pause)
        tp.addWidget(self._traj_play_btn)
        stop_btn = make_icon_button("player-stop", "停止", "停止播放并回到起点")
        stop_btn.clicked.connect(self._traj_on_stop); tp.addWidget(stop_btn)
        self._traj_loop_chk = QCheckBox("循环")
        self._traj_loop_chk.setToolTip("播放到末尾后自动从头继续")
        self._traj_loop_chk.setStyleSheet(f"color:{FG_MAIN};font-size:11px;")
        self._traj_loop_chk.toggled.connect(self._traj_on_loop_changed)
        tp.addWidget(self._traj_loop_chk)
        self._traj_scrub = QSlider(Qt.Orientation.Horizontal)
        self._traj_scrub.setRange(0, 1000); self._traj_scrub.setValue(0)
        self._traj_scrub.setToolTip("拖动预听曲线输出位置")
        self._traj_scrub.sliderMoved.connect(self._traj_on_scrub)
        tp.addWidget(self._traj_scrub, 1)
        self._traj_time_lbl = QLabel("0.00 / 4.00s")
        self._traj_time_lbl.setToolTip("当前播放时间 / 曲线总时长")
        self._traj_time_lbl.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-family:{_th.FONT_MONO};font-size:11px;")
        self._traj_time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._traj_time_lbl.setFixedWidth(110)
        tp.addWidget(self._traj_time_lbl)
        ctrl_lay.addLayout(tp)

        mid_w.setMinimumWidth(560)
        hsplit.addWidget(mid_w)

        # ════ 右栏：应用设备组 / 通道（单开侧栏，整页右侧，全高）════
        right = QVBoxLayout(); right.setSpacing(6)
        rtitle = QLabel("应用设备组 / 通道")
        rtitle.setStyleSheet(f"color:{FG_ACCENT};font-size:12px;font-weight:bold;letter-spacing:1px;")
        right.addWidget(rtitle)
        rhint = QLabel("可多选组，每组可多选通道。曲线据此把值平分（造型）或扫描（路径）到设备。")
        rhint.setWordWrap(True); rhint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        right.addWidget(rhint)
        self._traj_targets_tree = QTreeWidget(); self._traj_targets_tree.setHeaderHidden(True)
        # 树形列表外观走全局；保留条目密度特化
        self._traj_targets_tree.setStyleSheet("QTreeWidget::item{padding:2px 4px;}")
        self._traj_targets_tree.itemChanged.connect(self._traj_on_target_item_changed)
        right.addWidget(self._traj_targets_tree, 1)
        right_w = QWidget(); right_w.setLayout(right); right_w.setMinimumWidth(250)
        hsplit.addWidget(right_w)

        # 三栏初始宽 + 伸展策略：中栏吃多余空间，左右栏拖动可改、不缩没。
        hsplit.setStretchFactor(0, 0)
        hsplit.setStretchFactor(1, 1)
        hsplit.setStretchFactor(2, 0)
        _pcui.finish_trajectory_ui(body, hsplit)

        # ── 整页 QScrollArea：栏目不缩小，小窗口时上下左右拖动浏览 ──
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setWidget(body)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 滚动条外观由全局 QSS 提供；滚动区保持透明无边框
        page.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        # tab 切换到本页时刷新（设备组/通道随项目变化）
        try:
            self._sub_tabs.currentChanged.connect(self._traj_on_tab_changed)
        except Exception:
            pass
        self._traj_refresh_list()
        return page

    # ── 选择 / 列表 ───────────────────────────────────────────────────
    def _traj_selected(self) -> Optional[TrajectoryCurve]:
        return get_trajectory_curve_store().get(self._traj_selected_id) if self._traj_selected_id else None

    def _traj_on_tab_changed(self, idx: int):
        try:
            if self._sub_tabs.widget(idx) is None:
                return
            if self._sub_tabs.tabText(idx) == "轨迹曲线":
                self._traj_refresh_list()
                self._traj_rebuild_targets_tree()
        except Exception:
            pass

    def _traj_curve_type_tag(self, tr) -> str:
        return "造型" if getattr(tr, "curve_type", CURVE_TYPE_PATH) == CURVE_TYPE_SHAPE else "路径"

    def _traj_refresh_list(self):
        if not hasattr(self, "_traj_list"):
            return
        store = get_trajectory_curve_store()
        prev = self._traj_selected_id
        self._traj_list.blockSignals(True)
        self._traj_list.clear()
        for tr in store.all():
            src = "录制" if tr.source == "recorded" else "手画"
            nseg = len(tr.cuts) + 1
            tag = self._traj_curve_type_tag(tr)
            it = QListWidgetItem(f"[{tag}] {tr.name}  ({tr.duration:g}s · {src} · {nseg}段)")
            it.setData(Qt.ItemDataRole.UserRole, tr.id)
            self._traj_list.addItem(it)
            if tr.id == prev:
                self._traj_list.setCurrentItem(it)
        self._traj_list.blockSignals(False)
        if self._traj_selected() is None and self._traj_list.count() > 0:
            self._traj_list.setCurrentRow(0)
            self._traj_selected_id = self._traj_list.item(0).data(Qt.ItemDataRole.UserRole)
        self._traj_load_selected_into_editor()

    def _traj_on_list_selected(self):
        it = self._traj_list.currentItem()
        self._traj_stop_internal()
        self._traj_play_t = 0.0
        self._traj_selected_id = it.data(Qt.ItemDataRole.UserRole) if it else None
        self._traj_load_selected_into_editor()

    def _traj_load_selected_into_editor(self):
        tr = self._traj_selected()
        self._traj_view.set_trajectory(tr)
        if tr is not None:
            self._traj_dur_spin.blockSignals(True); _ns_set(self._traj_dur_spin, float(tr.duration)); self._traj_dur_spin.blockSignals(False)
            self._traj_loop_chk.blockSignals(True); self._traj_loop_chk.setChecked(bool(tr.loop)); self._traj_loop_chk.blockSignals(False)
        self._traj_apply_type_ui()
        self._traj_on_segment_selected(0)
        if self._traj_is_shape():
            self._traj_on_boundary_selected(0)
        self._traj_rebuild_targets_tree()
        self._traj_update_transport_ui()

    def _traj_is_shape(self) -> bool:
        tr = self._traj_selected()
        return tr is not None and getattr(tr, "curve_type", CURVE_TYPE_PATH) == CURVE_TYPE_SHAPE

    def _traj_apply_type_ui(self):
        """按曲线类型切换：类型标签 / 段栏字段可见性 / 边界栏可见性 / 提示文案。"""
        tr = self._traj_selected()
        shape = self._traj_is_shape()
        if hasattr(self, "_traj_type_lbl"):
            if tr is None:
                self._traj_type_lbl.setText("—")
            else:
                self._traj_type_lbl.setText(CURVE_TYPE_LABELS.get(tr.curve_type, tr.curve_type))
        # 段栏：path 显示方向/分组/时序；shape 只留静态 scope/翼/块。
        for w in (getattr(self, "_traj_seg_dir_lbl", None), getattr(self, "_traj_seg_dir", None),
                  getattr(self, "_traj_seg_groups_lbl", None), getattr(self, "_traj_seg_groups", None),
                  getattr(self, "_traj_seg_mode", None),
                  getattr(self, "_traj_seg_pause_lbl", None), getattr(self, "_traj_seg_pause", None)):
            if w is not None:
                w.setVisible(not shape)
        # 边界 morph 栏：仅 shape。
        if hasattr(self, "_traj_bnd_grp"):
            self._traj_bnd_grp.setVisible(shape)
        if hasattr(self, "_traj_seg_grp"):
            self._traj_seg_grp.set_title("当前段·静态造型设置" if shape else "当前段设置")
        if hasattr(self, "_traj_hint"):
            if shape:
                self._traj_hint.setText("造型：选段设静态造型(坐标系/翼/块，曲线那一截平分到设备)；"
                                        "选分割线/起点/终点设过渡(时间/方向/翼/块/分组)。描点改曲线，分段竖切。")
            else:
                self._traj_hint.setText("路径：选段设方向/翼/块/坐标系/分组/时序。描点：左键加点/拖动/右键删点。"
                                        "分段：点击竖切一刀，点中分界线删除。")

    def _traj_on_new(self):
        store = get_trajectory_curve_store()
        # 类型选择对话框
        dlg = QDialog(self)
        dlg.setWindowTitle("新建曲线")
        # QDialog/QLabel 外观由全局主题提供
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("名称："))
        name_edit = QLineEdit(f"曲线{len(store.all())+1}")
        # QLineEdit 外观由全局主题提供
        lay.addWidget(name_edit)
        lay.addWidget(QLabel("类型："))
        type_combo = QComboBox()
        # QComboBox 外观由全局主题提供
        for ct in CURVE_TYPES:
            type_combo.addItem(CURVE_TYPE_LABELS.get(ct, ct), ct)
        lay.addWidget(type_combo)
        desc = QLabel("路径曲线：一个读数指针随时间扫过设备。\n造型曲线：曲线形状平分到一组设备的空间分布。")
        desc.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(desc)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ctype = type_combo.currentData() or CURVE_TYPE_PATH
        tr = store.create(name=name_edit.text().strip() or "曲线",
                          duration=_ns_value(self._traj_dur_spin),
                          source="manual", curve_type=ctype)
        self._traj_selected_id = tr.id
        self._traj_refresh_list()

    def _traj_on_duplicate(self):
        tr = self._traj_selected()
        if tr is None:
            return
        store = get_trajectory_curve_store()
        d = tr.to_dict(); d.pop("id", None)
        clone = TrajectoryCurve.from_dict(d)
        clone.name = f"{tr.name} 副本"
        store.add(clone)
        self._traj_selected_id = clone.id
        self._traj_refresh_list()

    def _traj_on_rename(self):
        tr = self._traj_selected()
        if tr is None:
            return
        name, ok = QInputDialog.getText(self, "重命名轨迹", "名称：", text=tr.name)
        if ok and name:
            get_trajectory_curve_store().rename(tr.id, name)
            self._traj_refresh_list()

    def _traj_on_delete(self):
        tr = self._traj_selected()
        if tr is None:
            return
        self._traj_stop_internal()
        get_trajectory_curve_store().remove(tr.id)
        self._traj_selected_id = None
        self._traj_refresh_list()

    def _traj_on_duration_changed(self, val: float):
        tr = self._traj_selected()
        if tr is not None:
            tr.duration = max(0.1, float(val))
            self._traj_view.update()
            self._traj_update_transport_ui()

    def _traj_on_curve_changed(self):
        # 曲线视图直接改的是 tr.curve.points（权威）；这里只刷新列表标签计数。
        self._traj_refresh_list_labels()

    def _traj_refresh_list_labels(self):
        tr = self._traj_selected()
        if tr is None:
            return
        it = self._traj_list.currentItem()
        if it is not None:
            src = "录制" if tr.source == "recorded" else "手画"
            tag = self._traj_curve_type_tag(tr)
            it.setText(f"[{tag}] {tr.name}  ({tr.duration:g}s · {src} · {len(tr.cuts)+1}段)")

    # ── 模式 / 分段 ───────────────────────────────────────────────────
    def _traj_set_mode(self, mode: str):
        mode = mode if mode in ("select", "point", "cut") else "select"
        self._traj_mode_select_btn.setChecked(mode == "select")
        self._traj_mode_point_btn.setChecked(mode == "point")
        self._traj_mode_cut_btn.setChecked(mode == "cut")
        self._traj_view.set_mode(mode)

    def _traj_on_clear_cuts(self):
        tr = self._traj_selected()
        if tr is None:
            return
        tr.clear_cuts()
        self._traj_view.set_selected_segment(0)
        self._traj_view.set_selected_boundary(0)
        self._traj_view.update()
        self._traj_on_segment_selected(0)
        if self._traj_is_shape():
            self._traj_on_boundary_selected(0)
        self._traj_refresh_list_labels()

    def _traj_on_segment_selected(self, idx: int):
        tr = self._traj_selected()
        if tr is None:
            self._traj_seg_lbl.setText("段 —")
            return
        idx = max(0, min(idx, len(tr.segments) - 1))
        self._traj_view.set_selected_segment(idx)
        seg = tr.segments[idx]
        bounds = tr.segment_bounds()
        s, e = bounds[idx]
        dur = float(tr.duration)
        self._traj_seg_lbl.setText(f"段{idx+1}/{len(tr.segments)} ({s*dur:.2f}~{e*dur:.2f}s)")
        widgets = (self._traj_seg_dir, self._traj_seg_wing, self._traj_seg_block,
                   self._traj_seg_scope, self._traj_seg_groups, self._traj_seg_mode,
                   self._traj_seg_pause)
        for w in widgets:
            w.blockSignals(True)
        i = self._traj_seg_dir.findData(seg.direction)
        self._traj_seg_dir.setCurrentIndex(i if i >= 0 else 0)
        self._traj_seg_wing.setValue(int(seg.wings))
        self._traj_seg_block.setValue(int(seg.block))
        _wing_flags.restore(self, seg.to_dict(), "_traj_seg_")
        i = self._traj_seg_scope.findData(getattr(seg, "spatial_scope", SCOPE_FOLLOW))
        self._traj_seg_scope.setCurrentIndex(i if i >= 0 else 0)
        self._traj_seg_groups.setValue(int(getattr(seg, "groups", 1)))
        i = self._traj_seg_mode.findData(getattr(seg, "group_mode", GROUP_PARALLEL))
        self._traj_seg_mode.setCurrentIndex(i if i >= 0 else 0)
        _ns_set(self._traj_seg_pause, float(getattr(seg, "group_pause", 0.0)))
        for w in widgets:
            w.blockSignals(False)

    def _traj_on_seg_changed(self, *_):
        tr = self._traj_selected()
        if tr is None:
            return
        idx = self._traj_view.selected_segment()
        if not (0 <= idx < len(tr.segments)):
            return
        seg = tr.segments[idx]
        seg.direction = self._traj_seg_dir.currentData() or DIR_WHOLE
        seg.wings = int(self._traj_seg_wing.value())
        seg.block = int(self._traj_seg_block.value())
        seg.wing_mirror = bool(getattr(self, "_traj_seg_wing_mirror", False))
        seg.wing_reverse = bool(getattr(self, "_traj_seg_wing_reverse", False))
        seg.spatial_scope = self._traj_seg_scope.currentData() or SCOPE_FOLLOW
        seg.groups = int(self._traj_seg_groups.value())
        seg.group_mode = self._traj_seg_mode.currentData() or GROUP_PARALLEL
        seg.group_pause = _ns_value(self._traj_seg_pause)
        self._traj_view.update()
        _mark_pm_dirty(self)

    # ── 边界 morph 设置（造型曲线）──────────────────────────────────────
    def _traj_on_boundary_selected(self, idx: int):
        tr = self._traj_selected()
        if tr is None or not self._traj_is_shape():
            self._traj_bnd_lbl.setText("过渡 —")
            return
        tr._sync_boundaries()
        n_bnd = len(tr.boundaries)
        idx = max(0, min(idx, n_bnd - 1))
        self._traj_view.set_selected_boundary(idx)
        if idx == 0:
            kind = "起点淡入（从默认值）"
        elif idx == n_bnd - 1:
            kind = "终点淡出（到默认值）"
        else:
            kind = f"分割线 {idx}"
        self._traj_bnd_lbl.setText(f"过渡：{kind}")
        bseg = tr.boundaries[idx]
        widgets = (self._traj_bnd_trans, self._traj_bnd_dir, self._traj_bnd_wing,
                   self._traj_bnd_block, self._traj_bnd_scope, self._traj_bnd_groups,
                   self._traj_bnd_mode, self._traj_bnd_pause)
        for w in widgets:
            w.blockSignals(True)
        _ns_set(self._traj_bnd_trans, float(getattr(bseg, "transition", 0.0)))
        i = self._traj_bnd_dir.findData(bseg.direction)
        self._traj_bnd_dir.setCurrentIndex(i if i >= 0 else 0)
        self._traj_bnd_wing.setValue(int(bseg.wings))
        self._traj_bnd_block.setValue(int(bseg.block))
        _wing_flags.restore(self, bseg.to_dict(), "_traj_bnd_")
        i = self._traj_bnd_scope.findData(getattr(bseg, "spatial_scope", SCOPE_FOLLOW))
        self._traj_bnd_scope.setCurrentIndex(i if i >= 0 else 0)
        self._traj_bnd_groups.setValue(int(getattr(bseg, "groups", 1)))
        i = self._traj_bnd_mode.findData(getattr(bseg, "group_mode", GROUP_PARALLEL))
        self._traj_bnd_mode.setCurrentIndex(i if i >= 0 else 0)
        _ns_set(self._traj_bnd_pause, float(getattr(bseg, "group_pause", 0.0)))
        for w in widgets:
            w.blockSignals(False)

    def _traj_on_bnd_changed(self, *_):
        tr = self._traj_selected()
        if tr is None or not self._traj_is_shape():
            return
        tr._sync_boundaries()
        idx = self._traj_view.selected_boundary()
        if not (0 <= idx < len(tr.boundaries)):
            return
        bseg = tr.boundaries[idx]
        bseg.transition = _ns_value(self._traj_bnd_trans)
        bseg.direction = self._traj_bnd_dir.currentData() or DIR_WHOLE
        bseg.wings = int(self._traj_bnd_wing.value())
        bseg.block = int(self._traj_bnd_block.value())
        bseg.wing_mirror = bool(getattr(self, "_traj_bnd_wing_mirror", False))
        bseg.wing_reverse = bool(getattr(self, "_traj_bnd_wing_reverse", False))
        bseg.spatial_scope = self._traj_bnd_scope.currentData() or SCOPE_FOLLOW
        bseg.groups = int(self._traj_bnd_groups.value())
        bseg.group_mode = self._traj_bnd_mode.currentData() or GROUP_PARALLEL
        bseg.group_pause = _ns_value(self._traj_bnd_pause)
        self._traj_view.update()
        _mark_pm_dirty(self)

    # ── 导入 / 导出（单曲线 JSON，随子页面）─────────────────────────────
    def _traj_on_export(self):
        tr = self._traj_selected()
        if tr is None:
            QMessageBox.information(self, "导出曲线", "请先选择一条曲线。")
            return
        safe = "".join(ch for ch in tr.name if ch not in '\\/:*?"<>|').strip() or "curve"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出曲线", f"{safe}.curve.json", "曲线 JSON (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            payload = {"kind": "programmer_curve", "version": 1, "curve": tr.to_dict()}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"写入失败：{e}")
            return
        log.info("曲线已导出: %s", path)

    def _traj_on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入曲线", "", "曲线 JSON (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"读取失败：{e}")
            return
        cd = payload.get("curve") if isinstance(payload, dict) else None
        if cd is None and isinstance(payload, dict) and "cuts" in payload:
            cd = payload  # 容错：直接是 curve dict
        if not isinstance(cd, dict):
            QMessageBox.warning(self, "导入失败", "文件格式不是有效的曲线 JSON。")
            return
        try:
            tr = TrajectoryCurve.from_dict(cd)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"解析失败：{e}")
            return
        tr.id = "traj_" + uuid.uuid4().hex[:12]  # 避免与现有 id 冲突
        store = get_trajectory_curve_store()
        if any(c.name == tr.name for c in store.all()):
            tr.name = f"{tr.name} (导入)"
        store.add(tr)
        self._traj_selected_id = tr.id
        self._traj_refresh_list()
        log.info("曲线已导入: %s", path)

    # ── 造型淡入淡出的默认值（按设备/通道，归一 0..1）────────────────────
    def _traj_resolve_default(self, fid: str, ch: str) -> float:
        """设备 fid 的通道 ch 的默认值（归一 0..1）。

        读 fixture def 的 function_channel_defaults（原始 0-255）；缺则 0。
        电机/功能通道用原始通道名（含 function_* 与 motor 系列），能命中
        非 0 中位；motor fine 还会回退查其 coarse 名。

        ★ R/G/B/W/dimmer 是 addr_cache 规范化出的「灯通道」键，按设计
        永远不存在于 function_channel_defaults 里（该字典仅收 function_*
        前缀键，见 patch_panel._collect_function_channel_defaults / save），
        故这些键恒查不到 → 回退 0（灭）。这正是灯通道淡入/淡出的期望静止
        态，并非缺陷；不必为它们反查原始通道名（反查也永远命中不到）。
        """
        cache = getattr(self, "_traj_default_cache", None)
        if cache is None:
            cache = self._traj_default_cache = {}
        key = (str(fid), str(ch))
        if key in cache:
            return cache[key]
        # 设备 id→fixture 映射（与默认值缓存同生命周期，addr_cache 重建时一并清），
        # 避免每个 (fid,ch) 对大工程做 O(n) 线性扫描。
        fx_by_id = getattr(self, "_traj_fx_by_id", None)
        if fx_by_id is None:
            fx_by_id = self._traj_fx_by_id = {
                str(getattr(f, "id", "")): f for f in (self._current_fixtures or [])
            }
        val = 0.0
        try:
            fx = fx_by_id.get(str(fid))
            if fx is not None:
                defs = getattr(self, "_fixture_defs_cache", {}) or {}
                fd = defs.get(getattr(fx, "def_id", None))
                fcd = getattr(fd, "function_channel_defaults", {}) or {} if fd else {}
                # 直接命中通道类型；motor fine 也尝试其 coarse 名
                raw = fcd.get(ch)
                if raw is None and ch in MOTOR_FINE_TO_COARSE:
                    raw = fcd.get(MOTOR_FINE_TO_COARSE[ch])
                if raw is not None:
                    val = max(0.0, min(1.0, float(raw) / 255.0))
        except Exception:
            val = 0.0
        cache[key] = val
        return val

    # ── 录制 ─────────────────────────────────────────────────────────
    def _traj_on_record_toggle(self):
        if not self._traj_rec_active:
            tr = self._traj_selected()
            if tr is None:
                self._traj_on_new(); tr = self._traj_selected()
            if tr is None:
                return
            self._traj_rec_active = True
            automation_begin(getattr(self, "_traj_auto", None))  # 四模式录制：复位+同步模式+刷新通道表
            self._traj_rec_samples = []
            self._traj_rec_t0 = _time.monotonic()
            style_record_button(self._traj_rec_btn, recording=True)
            if automation_is_read(getattr(self, "_traj_auto", None)):
                # read 模式：不录样本；play timer 回放现有曲线（emit_output +
                # automation_read_step 把曲线值经主值链写回并发 master 轨迹键）。
                self._traj_on_play_pause()
                return
            self._traj_rec_timer.start()
        else:
            self._traj_rec_active = False
            self._traj_rec_timer.stop()
            style_record_button(self._traj_rec_btn, recording=False)
            self._traj_finish_recording()

    def _traj_on_rec_tick(self):
        if not self._traj_rec_active:
            return
        elapsed = _time.monotonic() - self._traj_rec_t0
        # 录制源扩展：推杆 or 通道实时值；四模式（write/touch/latch/read）决定是否写入。
        auto = getattr(self, "_traj_auto", None)
        sample = automation_tick(auto, elapsed)
        if sample is None and auto is None:
            sample = [float(elapsed), self._traj_rec_slider.value() / 255.0]
        if sample is not None:
            self._traj_rec_samples.append(sample)
        # 实时刷新心电图（每帧直接把样本序列传给 view，不改模型）
        if hasattr(self, "_traj_view"):
            self._traj_view.set_live_recording(
                self._traj_rec_samples, duration_hint=max(1.0, elapsed)
            )

    def _traj_finish_recording(self):
        # 清除实时录制叠加层
        if hasattr(self, "_traj_view"):
            self._traj_view.clear_live_recording()
        tr = self._traj_selected()
        samples = list(self._traj_rec_samples or [])
        if tr is None or len(samples) < 2:
            return
        total = max(1e-3, samples[-1][0])
        norm = [[max(0.0, min(1.0, ts / total)), max(0.0, min(1.0, v))] for ts, v in samples]
        cleaned = []; last_x = -1.0
        for x, y in norm:
            if x <= last_x:
                x = last_x + 1e-4
            cleaned.append([min(1.0, x), y]); last_x = x
        # R180：RDP 抽稀（垂直容差，录制区可调）——长录不再产生海量控制点。
        cleaned = automation_reduce(getattr(self, "_traj_auto", None), cleaned)
        tr.duration = round(total, 3)
        tr.source = "recorded"
        try:
            from ..core.models import CurveMapping, CurvePoint
            tr.curve = CurveMapping(points=[CurvePoint(x, y) for x, y in cleaned], source="recorded")
            tr.points = []
        except Exception:
            tr.curve = None
            tr.points = cleaned
        self._traj_dur_spin.blockSignals(True); _ns_set(self._traj_dur_spin, tr.duration); self._traj_dur_spin.blockSignals(False)
        self._traj_view.set_trajectory(tr)
        self._traj_refresh_list_labels()

    # ── 应用目标（设备组 + 通道）──────────────────────────────────────
    def _traj_group_records(self) -> List[dict]:
        """与 programmer 完全一致的「可选设备组」全集 + 各组的有序设备 id。

        来源镜像 GroupSelector 实际识别到的一切（不论是否保存）：
          全部设备 / 当前选中 / TopView 实时临时组 / 保存的设备组 / 逻辑·组合设备(含 mg: 合并组瓦片)。
        保存组按成员内母序排列；当前/实时选择保持原显式顺序、仅留存在设备；
        TopView 实时组用其传来的顺序。`从左到右`=正序、`从右到左`=反序（由翼/块单元处理），
        与 effect/clip 的方向语义一致。
        """
        all_fixtures = {str(f.id): f for f in (self._current_fixtures or [])}

        def _existing(ids):
            seen = set(); out = []
            for fid in ids or []:
                sfid = str(fid)
                if sfid in all_fixtures and sfid not in seen:
                    out.append(sfid); seen.add(sfid)
            return out

        records: List[dict] = []
        # 1) 全部设备
        records.append({"id": "__all__", "name": "全部设备",
                        "ids": _existing(all_fixtures.keys())})
        # 2) 当前选中（编辑范围）
        sel_ids = [str(getattr(f, "id", "")) for f in (self._current_fixtures_selected or [])]
        sel_ids = _existing(sel_ids)
        if sel_ids:
            records.append({"id": "__selected__", "name": f"当前选中 ({len(sel_ids)}台)", "ids": sel_ids})
        # 3) TopView 实时临时组（即使未保存）
        tv_ids = _existing(getattr(self, "_topview_live_fixture_ids", []) or [])
        if tv_ids:
            records.append({"id": "__topview_live__",
                            "name": f"TopView实时选择 ({len(tv_ids)}台)", "ids": tv_ids})

        selector = getattr(self, "_group_selector", None)
        # 4) 保存的设备组（与 GroupSelector 同源、同序）
        groups = list(getattr(selector, "_groups", []) or []) if selector is not None else []
        for g in groups:
            try:
                gid = selector._group_id(g); name = selector._group_name(g)
                ids = _existing(selector._group_fixture_ids(g))
            except Exception:
                continue
            if gid and ids:
                records.append({"id": gid, "name": name, "ids": ids})
        # 5) 逻辑/组合设备：复刻 _rebuild_buttons 的合并组(mg:)折叠 + 单体
        logical = list(getattr(selector, "_logical_devices", []) or []) if selector is not None else []
        if not logical:
            # 兜底：直接读项目，套用与 GroupSelector.load_groups 相同的 composite 过滤
            for d in self._read_logical_devices():
                if str(d.get("kind", "unit") or "unit") == "composite" and not d.get("hidden"):
                    logical.append(d)
        grouped: Dict[str, list] = {}; singles: list = []
        for ld in logical:
            if not str(ld.get("id", "")):
                continue
            mg = str(ld.get("merge_group", "") or "")
            if mg:
                grouped.setdefault(mg, []).append(ld)
            else:
                singles.append(ld)
        for mg, lds in grouped.items():
            members = []
            for ld in lds:
                members.extend(ld.get("member_fixture_ids", []) or [])
            ids = _existing(members)
            if ids:
                records.append({"id": f"mg:{mg}", "name": mg, "ids": ids})
        for ld in singles:
            lid = str(ld.get("id", "")); name = str(ld.get("name", "组合"))
            ids = _existing(ld.get("member_fixture_ids", []) or [])
            if lid and ids:
                records.append({"id": lid, "name": f"◆{name}", "ids": ids})
        return records

    def _traj_group_options(self):
        return [(r["id"], r["name"]) for r in self._traj_group_records()]

    def _traj_resolve_group_fixtures(self, group_id: str) -> List[str]:
        """组 → programmer 同序的存在设备 id（base order；TopView 临时组用其顺序）。"""
        gid = str(group_id or "")
        for r in self._traj_group_records():
            if r["id"] == gid:
                return list(r["ids"])
        return []

    def _traj_engine(self):
        """取 PlaybackEngine（优先注入引用，否则沿父链找主窗 _engine）。"""
        eng = getattr(self, "_engine_ref", None)
        if eng is not None:
            return eng
        p = self.parent()
        hops = 0
        while p is not None and hops < 12:
            cand = getattr(p, "_engine", None)
            if cand is not None and hasattr(cand, "_shape_aware_order_segments_for_fade"):
                self._engine_ref = cand
                return cand
            p = p.parent() if hasattr(p, "parent") else None
            hops += 1
        return None

    def _traj_resolve_scope(self, scope: str) -> str:
        """follow → 跟随项目全局灯控模式的坐标系语义（single→各自坐标系 /
        whole→整体坐标系）。★ 2026-08-04：坐标系与控灯粒度正交，本方法只
        解析坐标系维度；粒度由 effect 链路 _control_mode/_control_groups
        独立处理。"""
        from .trajectory_curve import SCOPE_FOLLOW, SCOPE_GLOBAL, SCOPE_PER_SHAPE
        s = str(scope or SCOPE_FOLLOW)
        if s == SCOPE_FOLLOW:
            return SCOPE_GLOBAL if self._current_light_control_mode() == "whole" else SCOPE_PER_SHAPE
        return s if s in (SCOPE_GLOBAL, SCOPE_PER_SHAPE) else SCOPE_PER_SHAPE

    def _traj_resolve_group_segments(self, group_id: str, spatial_scope: str) -> List[List[str]]:
        """组 + 坐标系 → shape 子组列表（每个子组按 L→R 有序），与 clip 同一套引擎逻辑。

        global：单个子组（全组一条扫线）。
        per_shape：用 engine._shape_aware_order_segments_for_fade 按 TopView 圆/椭圆 shape 切子组，
                   每个子组再用 engine._order_fixture_ids_for_direction 排 L→R。无引擎/不足则回退单段。
        """
        base_ids = self._traj_resolve_group_fixtures(group_id)
        if not base_ids:
            return []
        scope = self._traj_resolve_scope(spatial_scope)
        eng = self._traj_engine()
        proj = getattr(self._pm, "project", None) or self._pm
        # L→R 规范定序（与 clip 一致）：ltr 方向定位，scope_ids 决定坐标系归一范围。
        from .trajectory_curve import SCOPE_GLOBAL

        def _order_lr(ids, scope_ids):
            if eng is None or len(ids) <= 1:
                return list(ids)
            try:
                return eng._order_fixture_ids_for_direction(
                    list(ids), "ltr", proj=proj, clip=None, scope_ids=list(scope_ids))
            except Exception:
                return list(ids)

        if scope == SCOPE_GLOBAL or eng is None:
            return [_order_lr(base_ids, base_ids)]
        # per_shape：切 shape 子组
        try:
            segments = eng._shape_aware_order_segments_for_fade(base_ids, proj=proj, clip=None) or []
        except Exception:
            segments = []
        if not segments:
            return [_order_lr(base_ids, base_ids)]
        out: List[List[str]] = []
        for seg_ids in segments:
            seg_ids = [str(x) for x in seg_ids if str(x) in set(base_ids)]
            if seg_ids:
                out.append(_order_lr(seg_ids, seg_ids))
        return out or [_order_lr(base_ids, base_ids)]

    def _traj_group_channel_types(self, group_id: str) -> List[str]:
        fids = self._traj_resolve_group_fixtures(group_id)
        seen = set(); types = []
        for fid in fids:
            chm = self._addr_cache.get(fid, {}) or {}
            for ch in chm.keys():
                if ch.startswith("__") or ch.endswith("__fine"):
                    continue
                if ch not in seen:
                    seen.add(ch); types.append(ch)
        types.sort(key=lambda c: (self._TRAJ_CH_ORDER.index(c) if c in self._TRAJ_CH_ORDER else 99, c))
        return types

    def _traj_rebuild_targets_tree(self):
        if not hasattr(self, "_traj_targets_tree"):
            return
        tr = self._traj_selected()
        tree = self._traj_targets_tree
        tree.blockSignals(True)
        tree.clear()
        if tr is None:
            tree.blockSignals(False); return
        tmap = {str(t.get("group_id", "")): set(t.get("channel_types", []) or [])
                for t in (tr.targets or [])}
        for gid, gname in self._traj_group_options():
            chans = self._traj_group_channel_types(gid)
            if not chans:
                continue
            top = QTreeWidgetItem([gname])
            top.setData(0, Qt.ItemDataRole.UserRole, gid)
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            sel = tmap.get(gid, set())
            top.setCheckState(0, Qt.CheckState.Checked if gid in tmap else Qt.CheckState.Unchecked)
            for ch in chans:
                child = QTreeWidgetItem([self._traj_ch_label(ch)])
                child.setData(0, Qt.ItemDataRole.UserRole, ch)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked if ch in sel else Qt.CheckState.Unchecked)
                top.addChild(child)
            tree.addTopLevelItem(top)
            top.setExpanded(gid in tmap)
        tree.blockSignals(False)

    def _traj_on_target_item_changed(self, item, _col):
        tr = self._traj_selected()
        if tr is None:
            return
        tree = self._traj_targets_tree
        tree.blockSignals(True)
        parent = item.parent()
        if parent is None:
            state = item.checkState(0)
            if state == Qt.CheckState.Checked:
                any_on = any(item.child(i).checkState(0) == Qt.CheckState.Checked
                             for i in range(item.childCount()))
                if not any_on:
                    for i in range(item.childCount()):
                        item.child(i).setCheckState(0, Qt.CheckState.Checked)
                item.setExpanded(True)
            else:
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, Qt.CheckState.Unchecked)
        else:
            any_on = any(parent.child(i).checkState(0) == Qt.CheckState.Checked
                         for i in range(parent.childCount()))
            parent.setCheckState(0, Qt.CheckState.Checked if any_on else Qt.CheckState.Unchecked)
        tree.blockSignals(False)
        self._traj_commit_targets()

    def _traj_commit_targets(self):
        tr = self._traj_selected()
        if tr is None:
            return
        tree = self._traj_targets_tree
        targets = []
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            gid = top.data(0, Qt.ItemDataRole.UserRole)
            chans = []
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.CheckState.Checked:
                    chans.append(str(ch.data(0, Qt.ItemDataRole.UserRole)))
            if chans:
                targets.append({"group_id": str(gid), "channel_types": chans})
        tr.targets = targets

    # ── 播放 transport ────────────────────────────────────────────────
    def _traj_on_play_pause(self):
        if self._traj_playing:
            self._traj_stop_internal()
            set_play_icon(self._traj_play_btn, playing=False)
        else:
            tr = self._traj_selected()
            if tr is None:
                return
            # ★ 2026-09-17：用户主动按播放 = 重新接管输出，解除"已释放"标记
            #   （否则上面的 emit 守卫会让播放看起来"没反应"）。
            _rearm_output_after_edit(self)
            _runtime_targets.bind_trajectory(self, tr)
            self._traj_playing = True
            self._traj_last_tick = _time.monotonic()
            self._traj_play_timer.start()
            set_play_icon(self._traj_play_btn, playing=True)

    def _traj_stop_internal(self):
        self._traj_playing = False
        self._trajectory_target_binding = None
        if self._traj_play_timer.isActive():
            self._traj_play_timer.stop()
        if hasattr(self, "_traj_play_btn"):
            set_play_icon(self._traj_play_btn, playing=False)

    def _traj_on_stop(self):
        self._traj_stop_internal()
        self._traj_play_t = 0.0
        self._traj_view.set_playhead(None)
        self._traj_update_transport_ui()

    def _traj_on_loop_changed(self, on: bool):
        tr = self._traj_selected()
        if tr is not None:
            tr.loop = bool(on)

    def _traj_on_scrub(self, val: int):
        tr = self._traj_selected()
        if tr is None:
            return
        dur = float(tr.duration)
        self._traj_play_t = max(0.0, min(dur, dur * val / 1000.0))
        self._traj_emit_output(tr, self._traj_play_t)
        self._traj_time_lbl.setText(f"{self._traj_play_t:.2f} / {dur:.2f}s")

    def _traj_on_play_tick(self):
        tr = self._traj_selected()
        if tr is None or not self._traj_playing:
            return
        now = _time.monotonic()
        dt = now - self._traj_last_tick
        self._traj_last_tick = now
        self._traj_play_t += dt
        dur = float(tr.duration)
        if self._traj_play_t >= dur:
            if tr.loop:
                self._traj_play_t = self._traj_play_t % dur if dur > 0 else 0.0
            else:
                self._traj_play_t = dur
                self._traj_emit_output(tr, self._traj_play_t)
                self._traj_update_transport_ui()
                self._traj_stop_internal()
                return
        self._traj_emit_output(tr, self._traj_play_t)
        automation_read_step(self)  # read 模式：曲线值经面板既有值链写回（其余模式 no-op）
        self._traj_update_transport_ui()

    def _traj_emit_output(self, tr: TrajectoryCurve, t: float):
        # ★ 2026-09-17：已释放时不得再写层（否则释放后 33ms 轨迹又把灯点亮）。
        #   语义：释放 = 停止一切自动输出；用户重新按播放会自行解除标记。
        if bool(getattr(self, "_programmer_output_released", False)):
            return
        try:
            values = _runtime_targets.evaluate_trajectory(self, tr, t)
        except Exception as e:
            log.warning("轨迹曲线求值失败: %s", e)
            values = {}
        if values:
            self._mark_layer_sparse_from_fixture_values("fader", values)
            self._schedule_live_output({})
            if not self._keepalive_timer.isActive():
                self._keepalive_timer.start()
        if hasattr(self, "_traj_view"):
            self._traj_view.set_playhead(t)

    def _traj_update_transport_ui(self):
        tr = self._traj_selected()
        if tr is None or not hasattr(self, "_traj_time_lbl"):
            return
        dur = float(tr.duration)
        self._traj_time_lbl.setText(f"{self._traj_play_t:.2f} / {dur:.2f}s")
        self._traj_scrub.blockSignals(True)
        self._traj_scrub.setValue(int(1000 * self._traj_play_t / dur) if dur > 0 else 0)
        self._traj_scrub.blockSignals(False)


    def _build_toolbar(self) -> QWidget:
        presets = BUILTIN_SMOOTH_PRESETS if _SMOOTHER_AVAILABLE else {}
        return _pwui.build_toolbar(self, presets)

    def _init_joystick_binding(self):
        """初始化摇杆绑定管理器连接。"""
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        mgr.set_value_handler(self._dispatch_bound_value)
        mgr.binding_added.connect(lambda k: self._refresh_all_binding_indicators())
        mgr.binding_removed.connect(lambda k: self._refresh_all_binding_indicators())
        mgr.bindings_cleared.connect(self._refresh_all_binding_indicators)
        # Clear stale bindings from previous session on fresh start

    def _toggle_joystick_binding_mode(self, enabled: bool):
        """进入/退出绑定模式。"""
        self._joystick_binding_mode = enabled
        if enabled:
            self._install_binding_event_filters()
        else:
            self._remove_binding_event_filters()

    def _install_binding_event_filters(self):
        """在所有可绑定控件上安装 eventFilter。XYJoystick 用内建绑定模式。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            # 主滑块
            ms = getattr(row, "_master_slider", None)
            if ms is not None:
                ms.installEventFilter(self)
            # 推杆/滑块/摇杆 strips
            for strip in getattr(row, "_ctrl_fader_strips", {}).values():
                strip.installEventFilter(self)
            for strip in getattr(row, "_ctrl_vslider_strips", {}).values():
                strip.installEventFilter(self)
            for strip in getattr(row, "_ctrl_joy_strips", {}).values():
                strip.installEventFilter(self)
            # XYJoystick: use built-in binding mode (eventFilter conflicts with mouse handling)
            for attr in ("ctrl_joy", "_joystick"):
                joy = getattr(row, attr, None)
                if joy is not None and not sip.isdeleted(joy):
                    joy.set_binding_mode(True)
                    try:
                        joy.bind_clicked.disconnect()
                    except (TypeError, RuntimeError):
                        pass
                    joy.bind_clicked.connect(self._on_xy_joystick_bind_clicked)

    def _remove_binding_event_filters(self):
        """卸载所有 eventFilter + 关闭 XYJoystick 绑定模式。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            ms = getattr(row, "_master_slider", None)
            if ms is not None:
                ms.removeEventFilter(self)
            for strip in getattr(row, "_ctrl_fader_strips", {}).values():
                strip.removeEventFilter(self)
            for strip in getattr(row, "_ctrl_vslider_strips", {}).values():
                strip.removeEventFilter(self)
            for strip in getattr(row, "_ctrl_joy_strips", {}).values():
                strip.removeEventFilter(self)
            for attr in ("ctrl_joy", "_joystick"):
                joy = getattr(row, attr, None)
                if joy is not None and not sip.isdeleted(joy):
                    joy.set_binding_mode(False)
                    try:
                        joy.bind_clicked.disconnect()
                    except (TypeError, RuntimeError):
                        pass

    def _on_xy_joystick_bind_clicked(self):
        """XYJoystick 被点击时触发绑定流程。找到发送信号的 widget 并识别其 ch_types。"""
        sender = self.sender()
        if sender is None:
            return
        xy_info = self._identify_xy_joystick_ch_types(sender)
        if xy_info:
            # 串台修复：绑定时刻记录用户点击的 XYJoystick 所属行（设备类型），
            # 分发时按 def_id 精确解析，不再依赖「可见行优先」重解析。
            target_row = self._row_for_xy_widget(sender)
            self._start_binding_for_xy_joystick(xy_info, target_row)

    def eventFilter(self, obj, event):
        """绑定模式：Enter/Leave 高亮 + MouseButtonPress 启动绑定。"""
        if not self._joystick_binding_mode:
            return super().eventFilter(obj, event)
        etype = event.type()
        if etype == event.Type.Enter:
            self._apply_binding_hover_style(obj, True)
        elif etype == event.Type.Leave:
            self._apply_binding_hover_style(obj, False)
        elif etype == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                # XYJoystick handles its own clicks via bind_clicked signal
                ch_type = self._identify_control_ch_type(obj)
                if ch_type:
                    # 串台修复：绑定时刻记录控件所属行（设备类型容器 def_id），
                    # 分发时按 def_id 精确解析目标行（见 _find_joystick_target_row）。
                    row = self._row_for_control_widget(obj)
                    def_id = str(getattr(row, "_def_id", "")) if row is not None else ""
                    self._start_binding_for_control(ch_type, def_id)
                    return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _apply_binding_hover_style(widget, hover: bool):
        """绿色发光高亮（仅用于普通控件；XYJoystick 自己画边框）。"""
        if hover:
            glow = QGraphicsDropShadowEffect()
            glow.setColor(QColor("#88FF88"))
            glow.setBlurRadius(16)
            glow.setOffset(0, 0)
            widget.setGraphicsEffect(glow)
        else:
            widget.setGraphicsEffect(None)

    def _row_for_control_widget(self, widget):
        """根据控件 widget 引用反查所属行（串台修复：绑定时刻定位设备类型）。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            ms = getattr(row, "_master_slider", None)
            if ms is widget:
                return row
            for ct, strip in getattr(row, "_ctrl_fader_strips", {}).items():
                if strip is widget:
                    return row
            for ct, strip in getattr(row, "_ctrl_vslider_strips", {}).items():
                if strip is widget:
                    return row
            for ct, strip in getattr(row, "_ctrl_joy_strips", {}).items():
                if strip is widget:
                    return row
        return None

    def _row_for_xy_widget(self, widget):
        """根据 XYJoystick widget 引用反查所属行。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            cj = getattr(row, "ctrl_joy", None)
            bj = getattr(row, "_joystick", None)
            if widget is cj or widget is bj:
                return row
        return None

    def _identify_control_ch_type(self, widget) -> Optional[str]:
        """根据 widget 引用反查 ch_type。"""
        row = self._row_for_control_widget(widget)
        if row is None:
            return None
        ms = getattr(row, "_master_slider", None)
        if ms is widget and len(row._ch_types) == 1:
            return row._ch_types[0]
        for ct, strip in getattr(row, "_ctrl_fader_strips", {}).items():
            if strip is widget:
                return ct
        for ct, strip in getattr(row, "_ctrl_vslider_strips", {}).items():
            if strip is widget:
                return ct
        for ct, strip in getattr(row, "_ctrl_joy_strips", {}).items():
            if strip is widget:
                return ct
        return None

    def _identify_xy_joystick_ch_types(self, widget) -> Optional[dict]:
        """识别 XYJoystick 控件的子轴→ch_type 映射。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            cj = getattr(row, "ctrl_joy", None)
            bj = getattr(row, "_joystick", None)
            if widget is cj or widget is bj:
                result = {}
                xc = getattr(row, "_axis_x_combo", None)
                yc = getattr(row, "_axis_y_combo", None)
                zc = getattr(row, "_axis_z_combo", None)
                if xc and xc.currentIndex() > 0:
                    ct = row._ch_types[xc.currentIndex() - 1] if xc.currentIndex() - 1 < len(row._ch_types) else None
                    if ct:
                        result["x"] = ct
                else:
                    # 主体 XYJoystick 硬编码 motor_x
                    if "motor_x" in row._ch_types:
                        result["x"] = "motor_x"
                if yc and yc.currentIndex() > 0:
                    ct = row._ch_types[yc.currentIndex() - 1] if yc.currentIndex() - 1 < len(row._ch_types) else None
                    if ct:
                        result["y"] = ct
                else:
                    if "motor_y" in row._ch_types:
                        result["y"] = "motor_y"
                if zc and zc.isEnabled() and zc.currentIndex() > 0:
                    ct = row._ch_types[zc.currentIndex() - 1] if zc.currentIndex() - 1 < len(row._ch_types) else None
                    if ct:
                        result["z"] = ct
                elif getattr(row, "_joy_space_mode", "2d") == "3d" and "motor_z" in row._ch_types:
                    result["z"] = "motor_z"
                if len(result) >= 2:
                    return result
        return None

    def _is_widget_already_bound(self, ch_types_map: dict, def_id: str = "") -> Optional[str]:
        """检查控件是否已有绑定，返回第一个已绑定 ch_type（或 None）。

        def_id=绑定时刻记录的设备类型容器(串台修复), 与分发解析一致。
        """
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return None
        mgr = get_joystick_bind_manager()
        for sub_axis, ct in ch_types_map.items():
            row = self._find_joystick_target_row(ct, "", def_id)
            space = getattr(row, "_joy_space_mode", "2d") if row is not None else ""
            if mgr.has_binding(ct, space) or mgr.has_binding(ct, ""):
                return ct
        return None

    def _start_binding_for_control(self, ch_type: str, def_id: str = ""):
        """启动普通控件的单轴绑定流程。def_id=控件所属设备类型容器（串台修复）。"""
        try:
            from .joystick_control_binding import (
                get_joystick_bind_manager, AxisDetectPopup)
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        if mgr.has_binding(ch_type, ""):
            existing = mgr.get_binding(ch_type, "")
            ax = existing.physical_axis.upper() if existing else "?"
            QMessageBox.information(
                self, "已绑定",
                f"通道 {ch_type} 已绑定到 {ax} 轴(通用)，\n请先取消现有绑定再重新绑定。")
            return
        popup = AxisDetectPopup(ch_type, self)
        if popup.exec() == QDialog.DialogCode.Accepted:
            axis = popup.detected_axis
            if axis:
                mgr.add_binding(ch_type, axis, target_def_id=def_id)
                self._exit_binding_mode()
                self._refresh_all_binding_indicators()

    def _start_binding_for_xy_joystick(self, ch_types_map: dict, target_row=None):
        """启动 XYJoystick 的多轴绑定流程。target_row=用户点击的 XYJoystick 所属行。

        串台修复：绑定时刻记录目标行所属设备类型容器 def_id，分发时精确解析，
        不再依赖「可见行优先」重解析（同 ch_type 跨设备类型行会漂移）。
        """
        try:
            from .joystick_control_binding import (
                get_joystick_bind_manager, MultiAxisDetectPopup)
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        # 获取当前空间模式(可见行优先:隐藏容器行可能持有过期 space_mode)
        space_mode = "2d"
        group_name = ""
        target = target_row if target_row is not None else \
            self._find_joystick_target_row(list(ch_types_map.values())[0])
        target_def_id = str(getattr(target, "_def_id", "")) if target is not None else ""
        if target is not None:
            space_mode = getattr(target, "_joy_space_mode", "2d")
            group_name = getattr(target, "_group_name", "")
        # 检查重复绑定(带目标设备类型,与分发解析一致)
        dup = self._is_widget_already_bound(ch_types_map, target_def_id)
        if dup:
            QMessageBox.information(
                self, "已绑定",
                f"{group_name} 的 {dup} 在 {space_mode.upper()} 模式下已有绑定，\n请先取消现有绑定再重新绑定。")
            return

        # ── 智能默认：已知设备自动建议绑定 ──────────────────────────────
        skip_detection = False
        detected_map = {}
        try:
            from .physical_joystick_driver import get_physical_joystick_driver
            pdriver = get_physical_joystick_driver()
            if pdriver.is_connected():
                info = pdriver.get_device_info(pdriver._device_index)
                if info:
                    name_upper = info.name.upper()
                    is_t16000m = any(p in name_upper for p in (
                        "T16000M", "T.16000M", "THRUSTMASTER"))
                    if is_t16000m and info.num_axes >= 3:
                        reply = QMessageBox.question(
                            self,
                            "检测到 T16000M",
                            f"检测到 T16000M 游戏摇杆 ({info.num_axes} 轴)。\n\n"
                            f"建议自动绑定:\n"
                            f"  X → 左右 (轴 0)\n"
                            f"  Y → 前后 (轴 1)\n"
                            f"{'  Z → 油门 (轴 2)' if 'z' in ch_types_map else ''}\n\n"
                            f"是否使用建议绑定？\n"
                            f"(点击 [否] 将进入手动检测)",
                            QMessageBox.StandardButton.Yes |
                            QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.Yes,
                        )
                        if reply == QMessageBox.StandardButton.Yes:
                            default_map = {"x": "x", "y": "y", "z": "z"}
                            detected_map = {s: p for s, p in default_map.items()
                                           if s in ch_types_map}
                            skip_detection = True
        except Exception:
            pass  # 任何异常 → 降级到手动检测

        if not skip_detection:
            sub_axes = list(ch_types_map.keys())
            popup = MultiAxisDetectPopup(
                group_name or "XYJoystick", sub_axes, self)
            if popup.exec() != QDialog.DialogCode.Accepted:
                return
            detected_map = popup.get_detected_map()

        if detected_map:
            rules_data = []
            for sub, ct in ch_types_map.items():
                phys = detected_map.get(sub)
                if phys:
                    rules_data.append({
                        "ch_type": ct,
                        "physical_axis": phys,
                        "sub_axis": sub,
                        "space_mode": space_mode,
                        "target_def_id": target_def_id,
                    })
            if rules_data:
                mgr.add_bindings_batch(rules_data)
                self._exit_binding_mode()
                self._refresh_all_binding_indicators()

    def _find_joystick_target_row(self, ch_type: str, space_mode: str = "", def_id: str = ""):
        """查找摇杆绑定的目标行（确定性）。

        类型分区后同一 ch_type 存在多行(每设备类型容器一行),且 9b04422 起
        隐藏容器"隐藏不销毁"、其行保留过期设备集仍留在 _rows 中。若摇杆分发
        仍取"创建顺序第一个匹配行",值会写进隐藏/过期行——用户盯着的可见行
        永远收不到值("绑定有效但值不动")。此 helper 与 _row_fixture_channel_value
        的 (fid,ct) 修复同构:先按 def_id 精确解析。

        ★ 串台二次修复(d4cacc0 后用户实测仍串台,2026-08-04):
          - def_id 非空(新绑定/已迁移): 精确命中绑定时刻记录的设备类型容器
            行——与可见性/行顺序无关。容器/行已不存在(def_id 失效: 跨工程/
            重导入 def uuid 变化)时**不再无条件回退「可见行优先」**——目标
            ch_type 仍存在多容器(数控基底 vs 数控三维通用二仪 均为 motor_x)
            时旧回退=写进另一设备类型行=串台。仅当全量候选唯一才回退。
          - def_id 为空(旧绑定数据): 全量候选(可见+隐藏行,可见性随组切换
            漂移不可作为依据)唯一 → 命中(启动迁移 _migrate_legacy_
            joystick_bindings 会固化该容器); 多候选/无候选 → None(不写值:
            宁可绑定失效暂停输出,不可串台)。
        """
        def _row_matches(row) -> bool:
            if sip.isdeleted(row):
                return False
            if ch_type not in getattr(row, "_ch_types", []):
                return False
            if space_mode:
                if getattr(row, "_joy_space_mode", "2d") != space_mode:
                    return False
            return True

        if def_id:
            try:
                rows = list(getattr(self._ch_list, "_container_rows", {}).get(def_id, []) or [])
            except Exception:
                rows = []
            for row in rows:
                if _row_matches(row):
                    return row
            # def_id 失效(设备类型容器/行已不存在): 落到下方全量候选判定,
            # 仅唯一候选可回退——多候选绝不写值。
        candidates = []
        try:
            all_rows = list(getattr(self._ch_list, "_rows", []) or [])
        except Exception:
            return None
        for row in all_rows:
            if _row_matches(row):
                candidates.append(row)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _dispatch_bound_value(self, rule, mapped_value: float):
        """统一分发：根据 rule 的类型路由到正确的值处理器。"""
        if rule.is_xy_joystick:
            # XYJoystick 绑定：需要聚合所有子轴值后一次性写入
            # 暂存值，在全部子轴值到齐后触发
            self._route_xy_joystick_value(rule, mapped_value)
        else:
            self._on_joystick_bound_value(rule, mapped_value)

    def _on_joystick_bound_value(self, rule, mapped_value: float):
        """普通控件（slider/fader/strip）值路由。"""
        row = self._find_joystick_target_row(
            rule.ch_type, rule.space_mode or "", rule.target_def_id)
        if row is None:
            # ★ 串台二次修复: 目标行无法确定性解析(跨工程 def 失效/多类型同通道
            #   且未迁移) → 暂停输出不写值, 一次性提示重新绑定(不串台)。
            self._warn_joystick_unresolved()
            return
        # ★ 修复: 幅度控制模式下将值转为幅度因子(-1~+1)
        if getattr(row, "_joy_mode", "additive") == "amplitude":
            amp = (mapped_value / 255.0 - 0.5) * 2.0
            row._send_joy_amplitude_overrides(amp, None, None)
            return
        row._on_master_changed(rule.ch_type, mapped_value, layer="joystick")

    def _route_xy_joystick_value(self, rule, mapped_value: float):
        """XYJoystick 绑定值暂存与聚合路由。缓存各子轴最新值，避免未变动轴被重置。"""
        row = self._find_joystick_target_row(rule.ch_type, "", rule.target_def_id)
        if row is None:
            # ★ 串台二次修复: 目标行无法确定性解析 → 暂停输出 + 一次性提示。
            self._warn_joystick_unresolved()
            return
        space = getattr(row, "_joy_space_mode", "2d")
        ch_space = rule.space_mode or space
        # ★ 串台二次修复: XY 整组一致性——「一个控件」的全部子轴绑定必须解析到
        #   同一目标行; 任一子轴无法确定性解析(多候选/def 失效)或落向其他行 →
        #   整组暂停输出(含 amplitude 模式)。否则「唯一候选子轴(如 motor_y)把
        #   多候选子轴(如 motor_x)拖进自己容器行」形成半聚合串台。
        try:
            from .joystick_control_binding import get_joystick_bind_manager as _gbm
            _mgr = _gbm()
        except Exception:
            _mgr = None
        if _mgr is not None:
            for _br in _mgr.get_all_bindings():
                if _br.space_mode != ch_space or not _br.sub_axis:
                    continue
                _r2 = self._find_joystick_target_row(_br.ch_type, "", _br.target_def_id)
                if _r2 is None or _r2 is not row:
                    self._warn_joystick_unresolved()
                    return
        if getattr(row, "_joy_mode", "additive") == "amplitude":
            # ★ 修复: 幅度控制模式下将物理摇杆值转为幅度因子(-1~+1)
            if not hasattr(self, "_xy_joy_cache"):
                self._xy_joy_cache: Dict[str, float] = {}
            row_key = f"{id(row)}_{space}"
            if rule.sub_axis:
                self._xy_joy_cache[f"{row_key}_{rule.sub_axis}"] = mapped_value / 255.0
            x_val = self._xy_joy_cache.get(f"{row_key}_x", 0.5)
            y_val = self._xy_joy_cache.get(f"{row_key}_y", 0.5)
            z_val = self._xy_joy_cache.get(f"{row_key}_z", 0.5)
            amp_x = (x_val - 0.5) * 2.0
            amp_y = (y_val - 0.5) * 2.0
            amp_z = (z_val - 0.5) * 2.0
            row._send_joy_amplitude_overrides(amp_x, amp_y, amp_z)
            return
        # 初始化缓存
        if not hasattr(self, "_xy_joy_cache"):
            self._xy_joy_cache: Dict[str, float] = {}
        row_key = f"{id(row)}_{space}"
        # 更新当前子轴的最新值
        if rule.sub_axis:
            self._xy_joy_cache[f"{row_key}_{rule.sub_axis}"] = mapped_value / 255.0
        # 读取所有子轴缓存值（默认 0.5）
        x_val = self._xy_joy_cache.get(f"{row_key}_x", 0.5)
        y_val = self._xy_joy_cache.get(f"{row_key}_y", 0.5)
        z_val = self._xy_joy_cache.get(f"{row_key}_z", 0.5)
        # 应用极坐标转换后写入
        out_x, out_y, out_z = row._convert_joystick_xyz(x_val, y_val, z_val)
        # 用绑定规则自身的 space_mode(而非目标行推断的)构建子轴→通道映射,
        # 避免首匹配行/目标行的 3d/2d 模式与绑定不匹配时 sub_to_ch 为空、
        # 全部子轴静默丢弃。
        # Build sub_axis→ch_type map from actual bindings (not hardcoded motor_x/y/z)
        # ★ 串台二次修复：只合并「与已解析目标行同设备类型容器（或旧式通用绑定）」
        #   的子轴——不同设备类型行的同名绑定不再互相串入聚合。用 row_def(解析后
        #   的行容器)替代 rule.target_def_id: 旧数据(空 def_id)时 row 为全量唯一
        #   候选, row_def 即其容器, 仍精确; 空 def_id 兄弟子轴与目标行同容器,
        #   聚合进来自洽且安全。
        sub_to_ch = {}
        try:
            from .joystick_control_binding import get_joystick_bind_manager
            mgr = get_joystick_bind_manager()
            row_def = str(getattr(row, "_def_id", "")) or ""
            for br in mgr.get_all_bindings():
                if br.space_mode != ch_space or not br.sub_axis:
                    continue
                if row_def and br.target_def_id not in ("", row_def):
                    continue
                sub_to_ch[br.sub_axis] = br.ch_type
        except Exception:
            pass
        for sub_axis, out_val in [("x", out_x), ("y", out_y), ("z", out_z)]:
            ch = sub_to_ch.get(sub_axis)
            if ch is None or ch not in row._ch_types:
                continue
            if sub_axis == "z" and ch_space != "3d":
                continue
            row._on_master_changed(ch, out_val * 255.0, layer="joystick")

    def _warn_joystick_unresolved(self):
        """绑定目标行无法确定性解析(跨工程 def 失效/多类型同通道未迁移)。

        串台二次修复: 分发热路径上仅标记+调度, 会话级一次性弹窗提示重新绑定;
        值不写入任何行 = 硬保证不串台。
        """
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        if getattr(mgr, "unresolved_warned", False):
            return
        mgr.unresolved_warned = True
        QTimer.singleShot(0, lambda: QMessageBox.warning(
            self, "摇杆绑定失效",
            "检测到摇杆绑定无法匹配当前工程的设备类型(可能已切换工程,或该通道"
            "存在于多个设备类型无法确定目标)。为避免串台已暂停输出,请打开"
            "「绑定管理」窗口重新绑定。"))

    def _exit_binding_mode(self):
        """退出绑定模式。"""
        self._joystick_binding_mode = False
        self._remove_binding_event_filters()
        self._joy_bind_btn.blockSignals(True)
        self._joy_bind_btn.setChecked(False)
        self._joy_bind_btn.blockSignals(False)

    def _unbind_control(self, ch_type: str, space_mode: str = ""):
        """取消特定绑定。"""
        try:
            from .joystick_control_binding import get_joystick_bind_manager
        except Exception:
            return
        mgr = get_joystick_bind_manager()
        mgr.remove_binding(ch_type, space_mode)
        self._refresh_all_binding_indicators()

    def _open_binding_manager(self):
        """打开绑定管理子窗口（非模态单例）。"""
        try:
            from .joystick_control_binding import BindingManagerDialog
        except Exception:
            return
        if self._binding_mgr_dialog is None or sip.isdeleted(self._binding_mgr_dialog):
            self._binding_mgr_dialog = BindingManagerDialog(self)
        dlg = self._binding_mgr_dialog
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _refresh_all_binding_indicators(self):
        """刷新所有 ChannelTypeRow 的绑定指示器。"""
        for row in self._ch_list._rows:
            if sip.isdeleted(row):
                continue
            if hasattr(row, "update_binding_indicators"):
                row.update_binding_indicators()

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(24)
        sep.setStyleSheet(f"background:{_th.BORDER_NORMAL};")
        return sep

    def _mk_btn(self, text: str, bg: str, fg: str, icon_key: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            f"QPushButton{{"
            f"background:{bg};color:{fg};"
            f"border:1px solid {_hex_alpha(fg, '44')};"
            f"border-radius:{_th.RADIUS}px;"
            f"padding:0 12px;font-size:11px;font-weight:bold;"
            f"min-height:0px;"
            f"}}"
            f"QPushButton:hover{{"
            f"background:{_hex_alpha(fg, '22')};"
            f"border-color:{_hex_alpha(fg, '88')};"
            f"}}"
            f"QPushButton:disabled{{color:{_th.TEXT_DISABLED};}}"
            f"QPushButton:checked{{"
            f"background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};"
            f"border:1px solid {_th.SELECT_BLUE};"
            f"}}"
        )
        if icon_key:
            try:
                from .main_icon_library import make_main_icon
                btn.setIcon(make_main_icon(icon_key, 20))
                btn.setIconSize(QSize(16, 16))
            except Exception:
                pass
        return btn

    def _read_logical_devices(self) -> list:
        """读项目的 logical_devices dict 列表（容错）。"""
        for src in (self._pm, getattr(self._pm, "project", None)):
            if src is None:
                continue
            recs = getattr(src, "logical_devices", None)
            if recs:
                return [r for r in recs if isinstance(r, dict)]
        return []

    def _on_logical_mode_toggle(self, logical_id: str, new_mode: str):
        try:
            from .logical_view import set_control_mode
            if str(logical_id).startswith("mg:"):
                # 合并组瓦片：对该 merge_group 的全部 composite 一起切模式。
                label = str(logical_id)[3:]
                changed = False
                for rec in self._read_logical_devices():
                    if str(rec.get("merge_group", "") or "") == label:
                        if set_control_mode(self._pm, str(rec.get("id", "")), new_mode):
                            changed = True
            else:
                changed = set_control_mode(self._pm, logical_id, new_mode)
        except Exception:
            changed = False
        if changed:
            try:
                if hasattr(self._pm, "project_changed"):
                    self._pm.project_changed.emit()
            except Exception:
                pass
            self._refresh_fixtures()

    def _on_light_control_mode_changed(self, mode: str):
        """全局灯控模式切换（联动所有页面）：写回 project.light_control_mode。

        single=单控(各盏灯获得灯组内部坐标系/per_shape)；whole=整体(整体坐标系/global)。
        effect 的 spatial_scope='follow' 立刻跟随；显式 per_shape/global 不受影响。
        """
        mode = "whole" if str(mode).strip().lower() == "whole" else "single"
        try:
            # 直接写工程字段 + 标脏，**不发 project_changed**：灯控模式只被 effect 播放/
            # 引擎分段实时读取(playback_engine._clip_spatial_scope、effect_browser
            # _resolved_spatial_scope 每帧读)，无任何面板需要重建(Programmer 显示无区别)。
            # 旧实现走 PM setter → project_changed 全链路刷新(约 1.8s 卡顿 + 弹窗链)。
            # ★ 性能：不再调用 _snapshot()——大工程下其全量序列化约 190ms，是切灯控
            #   ~260ms 的主因。灯控模式是全局布尔量，写入不改变选择/编辑范围，无需
            #   撤销快照与 load_fixture_group 回灌(旧回灌仅为抵消 project_changed 对
            #   编辑范围的副作用，而本路径已不发 project_changed)。
            proj = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
            if proj is not None:
                proj.light_control_mode = mode
                if hasattr(self._pm, "_mark_dirty"):
                    self._pm._mark_dirty()
            else:
                self._pm.light_control_mode = mode
        except Exception:
            try:
                proj = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None)
                if proj is not None:
                    proj.light_control_mode = mode
                else:
                    self._pm.light_control_mode = mode
            except Exception:
                log.warning("light_control_mode 写入失败", exc_info=True)

    def _current_light_control_mode(self) -> str:
        for src in (self._pm, getattr(self._pm, "project", None)):
            if src is None:
                continue
            m = getattr(src, "light_control_mode", None)
            if m:
                return "whole" if str(m).strip().lower() == "whole" else "single"
        # ★ 兜底统一 whole（2026-08-04 用户：默认应为整体控灯；旧兜底 single 是回归源）
        return "whole"

    def _on_pkg_arrange_toggled(self, checked: bool):
        self._pkg_arrange = bool(checked)
        if hasattr(self, "_ch_list"):
            self._ch_list.set_pkg_arrange(getattr(self, "_pkg_of", {}) or {}, self._pkg_arrange)

    def _refresh_fixtures(self):
        from .programmer_refresh_scope import capture_scope, restore_scope
        previous_scope = capture_scope(self)
        try:
            fixtures, defs = self._refresh_fixture_sources()
        except Exception:
            fixtures = []
            defs = {}
        self._current_fixtures = fixtures
        self._fixture_defs_cache = defs

        # 包隶属映射（包排列模式用：成员 fid → 包信息；纯展示整理）
        pkg_of = {}
        try:
            for _ld in (getattr(self._pm, "logical_devices", []) or []):
                if _ld.get("kind") != "package":
                    continue
                _pid = str(_ld.get("id", "") or "")
                _pname = str(_ld.get("name", "包") or "包")
                for _m in (_ld.get("member_fixture_ids", []) or []):
                    pkg_of[str(_m)] = {"id": _pid, "name": _pname}
        except Exception:
            pkg_of = {}
        self._pkg_of = pkg_of
        if hasattr(self, "_ch_list"):
            self._ch_list.set_pkg_arrange(pkg_of, getattr(self, "_pkg_arrange", False))
        if hasattr(self, "_pkg_arrange_btn"):
            self._pkg_arrange_btn.setVisible(bool(pkg_of))

        try:
            raw_groups = []
            if hasattr(self._pm, "fixture_groups"):
                raw_groups = list(self._pm.fixture_groups or [])
            elif hasattr(self._pm, "_fixture_groups"):
                raw_groups = list(self._pm._fixture_groups or [])
            elif hasattr(self._pm, "project") and hasattr(self._pm.project, "fixture_groups"):
                raw_groups = list(self._pm.project.fixture_groups or [])
            groups = []
            for g in raw_groups:
                if isinstance(g, dict):
                    class _GrpProxy:
                        pass
                    obj = _GrpProxy()
                    obj.id = g.get("id", "")
                    obj.name = g.get("name", "未命名")
                    obj.color = g.get("color", "#4A90D9")
                    obj.fixture_ids = list(g.get("fixture_ids", []))
                    obj.base_order_fixture_ids = list(g.get("base_order_fixture_ids", []))
                    groups.append(obj)
                else:
                    groups.append(g)
            logical = self._read_logical_devices()
            self._group_selector.load_groups(groups, fixtures, logical_devices=logical)
        except Exception as e:
            log.warning("ProgrammerPanel._refresh_fixtures group load failed: %s", e)
            self._group_selector.load_groups([], fixtures)
        try:
            self._group_selector.set_light_control_mode(self._current_light_control_mode())
        except Exception:
            pass
        if restore_scope(self, previous_scope, fixtures, defs):
            return

        self._current_fixtures_selected = fixtures
        self._current_base_order_ids = [f.id for f in fixtures]
        self._ch_list.load_fixtures(fixtures, defs, "全部设备")
        self._rebuild_addr_cache(fixtures, defs)
        try:
            if hasattr(self, "_traj_list"):
                self._traj_refresh_list()
        except Exception:
            pass

    def _refresh_fixture_sources(self):
        """pm 源未变时复用上次 fixtures/defs 副本——行为等价，只省重建。

        ★ 性能：至冬 7.0 (11382 台) 上旧实现每次调用都新建 fixtures 列表 +
        defs 字典，对象身份每帧都变，使下游三级 id 缓存全部失效：
          1. ChannelListPanel.load_fixtures 的签名缓存（行级/面板级）
          2. _rebuild_addr_cache 的廉价预检（id+len）
          3. _rebuild_type_containers 的 by_def 子集列表 → 行级签名缓存
        同状态刷新（选择/页面未变的 project_changed 链）因此每帧 ~310ms 纯
        签名重建。此处以 (源身份, 长度, 内容戳) 判定 pm 数据未变 → 复用副本
        → 三级缓存全部命中（热路径降到 ~30ms）。
        ★ 正确性：内容戳与 _ch_list.load_fixtures 签名 + _addr_cache_signature
        依赖同构，覆盖「补丁面板 setattr 就地改 def / 通道内容」（patch_panel.
        _edit_fixture_def_by_id，列表身份+长度不变）——戳不匹配即重建副本。
        """
        src = getattr(self._pm, "fixtures", None) or []
        src_defs = getattr(self._pm, "fixture_defs", None) or []
        prev = getattr(self, "_refresh_src_stamp", None)
        if (prev is not None
                and prev[0] is src and prev[1] == len(src)
                and prev[2] is src_defs and prev[3] == len(src_defs)
                and self._fixture_src_stamp(src, src_defs) == prev[4]):
            return self._refresh_fixtures_cached, self._refresh_defs_cached
        fixtures = list(src)
        defs = {fd.id: fd for fd in src_defs}
        self._refresh_fixtures_cached = fixtures
        self._refresh_defs_cached = defs
        self._refresh_src_stamp = (
            src, len(src), src_defs, len(src_defs),
            self._fixture_src_stamp(src, src_defs))
        return fixtures, defs

    def _fixture_src_stamp(self, src, src_defs):
        """pm 源内容戳：只比较影响下游缓存结果的字段。

        fixtures: (id, name, def_id, universe, start_channel)——与面板级
        load_fixtures 签名一致；defs: (id, name, channel_count, channels
        身份+逐通道 type/default, function_channel_defaults)——与
        _addr_cache_signature._fi_sig 依赖一致。
        """
        fx_sig = tuple(
            (str(getattr(f, "id", "") or ""),
             str(getattr(f, "name", "") or ""),
             str(getattr(f, "def_id", "") or ""),
             int(getattr(f, "universe", 1) or 1),
             int(getattr(f, "start_channel", 1) or 1))
            for f in src)
        dm_sig = tuple(
            (str(getattr(fd, "id", "") or ""),
             str(getattr(fd, "name", "") or ""),
             getattr(fd, "channel_count", len(getattr(fd, "channels", []) or [])),
             id(getattr(fd, "channels", None)),
             tuple(
                 (c.get("type") if isinstance(c, dict) else getattr(c, "type", None),
                  c.get("default_value") if isinstance(c, dict)
                  else getattr(c, "default_value", None))
                 for c in (getattr(fd, "channels", []) or [])),
             tuple(sorted((str(k), int(v))
                          for k, v in ((getattr(fd, "function_channel_defaults", {}) or {}).items())))
             if (getattr(fd, "function_channel_defaults", None) or {}) else ())
            for fd in src_defs)
        return (fx_sig, dm_sig)

    @staticmethod
    def _unique_fixture_ids(ids) -> List[str]:
        out: List[str] = []
        seen = set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid and sfid not in seen:
                out.append(sfid)
                seen.add(sfid)
        return out

    def _filter_existing_fixture_ids(self, ids) -> List[str]:
        all_ids = {str(getattr(fi, "id", "")) for fi in (self._current_fixtures or [])}
        return [fid for fid in self._unique_fixture_ids(ids) if fid in all_ids]

    def _topview_selected_ids(self) -> List[str]:
        tv = getattr(self, "_topview_ref", None)
        if not _qobject_alive(tv):
            return []
        try:
            if hasattr(tv, "_ordered_selected_ids"):
                ids = tv._ordered_selected_ids() or []
                return self._filter_existing_fixture_ids(ids)
        except Exception:
            pass
        try:
            selected = {str(fid) for fid in (getattr(tv, "selected_ids", []) or []) if str(fid)}
            if not selected:
                return []
            if hasattr(tv, "fixture_ids"):
                return self._filter_existing_fixture_ids(
                    [fid for fid in (tv.fixture_ids() or []) if str(fid) in selected])
            return self._filter_existing_fixture_ids(selected)
        except Exception:
            return []

    def _topview_base_order_ids(self, selected_ids: List[str]) -> List[str]:
        from .topview_selection_context import live_base_if_established

        tv = getattr(self, "_topview_ref", None)
        selected = self._unique_fixture_ids(selected_ids)
        if not _qobject_alive(tv):
            return selected
        try:
            base = live_base_if_established(tv, selected)
            if base:
                filtered = self._filter_existing_fixture_ids(base)
                if filtered:
                    return filtered
        except Exception:
            pass
        return selected

    def sync_from_topview_selection(self):
        ids = self._topview_selected_ids()
        if not ids:
            QMessageBox.information(self, "TopView同步", "TopView 当前没有选中的设备。")
            return
        base = self._topview_base_order_ids(ids)
        self.load_fixture_group(
            "__topview_live__",
            ids,
            "TopView实时选择",
            base_order_ids=base or ids,
        )

    def _fixture_group_id_from_record(self, group) -> str:
        if group is None:
            return ""
        if isinstance(group, dict):
            return str(group.get("id", "") or "")
        return str(getattr(group, "id", "") or "")

    def _delete_fixture_group_from_programmer(self, group_id: str, group_name: str = ""):
        gid = str(group_id or "")
        if not gid:
            return
        name = str(group_name or "未命名")
        if QMessageBox.question(
                self,
                "删除设备组",
                f"确认删除设备组 '{name}'？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        pm = self._pm
        project = self._project_data()
        try:
            groups = []
            if pm is not None and hasattr(pm, "fixture_groups"):
                groups = list(getattr(pm, "fixture_groups", []) or [])
            elif project is not None and hasattr(project, "fixture_groups"):
                groups = list(getattr(project, "fixture_groups", []) or [])
            elif pm is not None and hasattr(pm, "_fixture_groups"):
                groups = list(getattr(pm, "_fixture_groups", []) or [])
            new_groups = [
                group for group in groups
                if self._fixture_group_id_from_record(group) != gid
            ]
            if len(new_groups) == len(groups):
                return
            if pm is not None and hasattr(pm, "_snapshot"):
                try:
                    pm._snapshot(domain=_DOMAIN_PROGRAMMER)
                except Exception:
                    pass
            if pm is not None and hasattr(pm, "fixture_groups"):
                try:
                    pm.fixture_groups = new_groups
                except Exception:
                    if project is not None:
                        setattr(project, "fixture_groups", new_groups)
            elif project is not None:
                setattr(project, "fixture_groups", new_groups)
                if pm is not None and hasattr(pm, "_mark_dirty"):
                    pm._mark_dirty()
                elif pm is not None and hasattr(pm, "set_dirty"):
                    pm.set_dirty()
                if pm is not None and hasattr(pm, "project_changed"):
                    pm.project_changed.emit()
                elif hasattr(project, "project_changed"):
                    project.project_changed.emit()
            if getattr(self, "_current_group_id", "") == gid:
                self._group_selector.clear_selection_state(True)
                self.load_all_fixtures([f.id for f in self._current_fixtures])
            else:
                self._refresh_fixtures()
        except Exception as e:
            log.exception("Programmer delete fixture group failed")
            QMessageBox.critical(self, "删除设备组失败", f"无法删除设备组：\n{e}")

    def _update_light_mode_capability(self) -> None:
        """根据当前选中设备集的包构成,更新「整体控灯/单控灯」按钮能力提示。

        2026-08-04 用户需求:部分设备(如数控 4 灯)设计上固定整体控灯;
        有补光灯/效果灯两重灯的设备(如百变),整体/单控只对效果灯生效。
        判定源与 effect 侧 _light_capability_for_ids 同规则:包(kind=package)
        内任一成员 def 名含「数控」→ 整包固定整体控(恒整体,不可切);
        灯成员 def 名含「底座」或贴包中心 → 补光灯(整体/单控只对效果灯生效)。
        选中组全部为固定整体控设备时,「单控灯」按钮禁用。
        """
        try:
            # ★ 2026-08-04 修复:按钮属于 GroupSelector(self._group_selector),
            #   ProgrammerPanel 自身无此属性——原引用恒为 None,整个函数直接
            #   return,数控包的单控按钮禁用/提示从不生效。改为从子部件取。
            gs = getattr(self, "_group_selector", None)
            btns = (getattr(gs, "_light_mode_btn_single", None),
                    getattr(gs, "_light_mode_btn_whole", None))
            if not any(btns):
                return
            ids = set(self.get_live_selected_fixture_ids() or [])
            if not ids:
                return
            defs = getattr(self, "_fixture_defs_cache", {}) or {}
            def_name = {}
            if isinstance(defs, dict):
                for did, fd in defs.items():
                    def_name[str(did)] = str(getattr(fd, "name", "") or "")
            else:
                for fd in (defs or []):
                    did = getattr(fd, "id", None) or getattr(fd, "def_id", None)
                    if did is not None:
                        def_name[str(did)] = str(getattr(fd, "name", "") or "")
            fid_def = {}
            for fi in (self._current_fixtures or []):
                f = str(getattr(fi, "id", "") or "")
                if f and f in ids:
                    fid_def[f] = str(getattr(fi, "def_id", "") or "")

            def _type_name(fid: str) -> str:
                return def_name.get(fid_def.get(fid, ""), "")

            pkgs = []
            fixed_any = False
            has_fill = False
            for ld in self._read_logical_devices():
                if not isinstance(ld, dict) or ld.get("kind") != "package":
                    continue
                members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])
                           if str(m) in ids]
                if len(members) < 2:
                    continue
                names = [_type_name(m) for m in members]
                fixed = any("数控" in str(n) for n in names if n)
                lamp_names = [str(n) for n in names
                              if any(k in str(n) for k in ("灯", "LED", "RGBW", "Spot", "光源", "Light"))]
                has_fill = has_fill or any("底座" in n for n in lamp_names)
                fixed_any = fixed_any or fixed
                pkgs.append((ld, members, fixed))
            base_tip = ("整体控灯:包内灯当一个灯控制(effect 共享同一效果值);"
                        "单控灯:包内灯逐个独立。\n"
                        "两维正交:切换粒度不影响各自/整体坐标系。")
            if not pkgs:
                for b in btns:
                    if b is not None:
                        b.setToolTip(base_tip)
                        b.setEnabled(True)
                return
            notes = []
            for ld, members, fixed in pkgs[:3]:
                name = str(ld.get("name", "包") or "包")
                notes.append(f"{name}({len(members)}成员"
                             + ("·固定整体控" if fixed else "") + ")")
            extra = ("\n当前组含包:" + "、".join(notes))
            if fixed_any:
                extra += ("\n数控类设备设计上固定整体控灯(写死 4 灯当一个),"
                          "单控灯切换对其无效——单控按钮已禁用。")
            if has_fill:
                extra += ("\n百变类两重灯:整体/单控只对效果灯生效,"
                          "补光灯=效果灯总亮度/全开比例。")
            for b in btns:
                if b is not None:
                    b.setToolTip(base_tip + extra)
            single_btn = getattr(getattr(self, "_group_selector", None), "_light_mode_btn_single", None)
            if single_btn is not None:
                single_btn.setEnabled(not fixed_any)
        except Exception:
            return

    def load_fixture_group(self, group_id: str, fixture_ids: list, group_name: str = "", base_order_ids: Optional[list] = None):
        self._last_load_all_sig = None   # 切到具体组→ch_list 内容变,使"全部"守卫失效以便切回时重载
        all_fixtures = {fi.id: fi for fi in self._current_fixtures}
        defs = self._fixture_defs_cache
        base_sequence_ids = [str(fid) for fid in (base_order_ids or []) if str(fid) in all_fixtures]
        live_topview = str(group_id or "") == "__topview_live__"
        # R196：Fixture 面板桥接与本页瓦片同一成员合同；实时选择保留传入序。
        source_ids = [str(fid) for fid in (fixture_ids or []) if str(fid) in all_fixtures]
        ordered_ids = _order_members_by_base_seq(
            source_ids, [] if live_topview else base_sequence_ids)
        actual_ids = _r183_narrow_to_post_filter(self, ordered_ids)
        selected = [all_fixtures[fid] for fid in actual_ids]
        self._current_group_id = group_id
        self._current_group_name = group_name
        self._current_base_order_ids = list(base_sequence_ids or ordered_ids)
        if live_topview:
            self._topview_live_fixture_ids = list(actual_ids)
            self._topview_live_base_order_ids = list(base_sequence_ids or ordered_ids)
        else:
            self._topview_live_fixture_ids = []
            self._topview_live_base_order_ids = []
        self._current_fixtures_selected = selected
        if str(group_id or "") == "__topview_live__":
            selector = getattr(self, "_group_selector", None)
            if selector is not None and hasattr(selector, "clear_selection_state"):
                selector.clear_selection_state(False)
        label = group_name or f"设备组（{len(selected)} 台）"
        self._ch_list.load_fixtures(selected, defs, label)
        self._rebuild_addr_cache(selected, defs)
        self._emit_fixture_selection_to_topview(actual_ids); _r183_after_group_load(self)
        self._update_light_mode_capability()

    def load_all_fixtures(self, fixture_ids: list):
        # 去重守卫:一次全选连发多信号→本方法被重复调载入相同设备,每次重建全部通道行(1356设备昂贵)。
        # 与上次载入相同(同为"全部"视图+同设备集)则跳过重建。设备集变化(id/len)会自然失效。
        load_sig = ("__all__", id(self._current_fixtures),
                    len(self._current_fixtures), tuple(fixture_ids))
        if load_sig == getattr(self, "_last_load_all_sig", None):
            return
        self._last_load_all_sig = load_sig
        all_fixtures = {fi.id: fi for fi in self._current_fixtures}
        defs = self._fixture_defs_cache
        selected = [all_fixtures[fid] for fid in fixture_ids if fid in all_fixtures]
        self._current_group_id = "__all__"
        self._current_group_name = "全部设备"
        self._current_base_order_ids = [f.id for f in selected]
        self._topview_live_fixture_ids = []
        self._topview_live_base_order_ids = []
        self._current_fixtures_selected = selected
        self._ch_list.load_fixtures(selected, defs, "全部设备")
        self._rebuild_addr_cache(selected, defs)
        self._emit_fixture_selection_to_topview([f.id for f in selected])
        _r183_after_group_load(self)   # R183：步进标签复位 + 过滤条/快捷条刷新
        self._update_light_mode_capability()

    def get_live_selected_fixture_ids(self) -> list[str]:
        return [str(getattr(f, "id", "")) for f in (self._current_fixtures_selected or []) if getattr(f, "id", "")]

    def sync_groups_from_panel(self, panel):
        self._group_selector.sync_from_fixture_group_panel(panel)

    def update_fixture_positions(self, positions: list[dict]):
        try:
            new_pos = {}
            for item in positions:
                if isinstance(item, dict) and 'id' in item:
                    x = item.get('pos_x', item.get('x', 0.0))
                    y = item.get('pos_y', item.get('y', 0.0))
                    new_pos[item['id']] = (float(x), float(y))
            self._fixture_positions = new_pos
            if self._current_fixtures_selected:
                self._on_group_selected(
                    getattr(self, "_current_group_id", "__all__"),
                    [f.id for f in self._current_fixtures_selected],
                    self._current_group_name
                )
            log.info(f"Programmer 已同步 {len(new_pos)} 台设备的坐标布局并重排视图")
        except Exception as e:
            log.error(f"update_fixture_positions 失败: {e}")
            self._fixture_positions = {}

    def _on_group_selected(self, group_id: str, fixture_ids: list, group_name: str, base_order_ids: Optional[list] = None):
        # ★ 性能：11382 台的 id→fixture 索引按列表对象身份缓存（_current_fixtures
        #   只整体替换不就地修改），组切换不再重复构建。
        # ★ 正确性(审查A)：值持有列表引用 + 身份校验——工程重载后新列表复用
        #   旧 id 时不得误命中旧工程的 fixture 对象。
        _cf = self._current_fixtures
        if (getattr(self, "_all_fixtures_key", None) != id(_cf)
                or getattr(self, "_all_fixtures_ref", None) is not _cf):
            self._all_fixtures_key = id(_cf)
            self._all_fixtures_ref = _cf
            self._all_fixtures_index = {fi.id: fi for fi in _cf}
        all_fixtures = self._all_fixtures_index
        defs = self._fixture_defs_cache
        live_topview = str(group_id or "") == "__topview_live__"
        base_ids = list(base_order_ids or [])
        if live_topview and not base_ids:
            base_ids = list(getattr(self, "_topview_live_base_order_ids", []) or getattr(self, "_current_base_order_ids", []) or [])
        if not base_ids:
            try:
                raw_groups = []
                if hasattr(self._pm, "fixture_groups"):
                    raw_groups = list(self._pm.fixture_groups or [])
                elif hasattr(self._pm, "project") and hasattr(self._pm.project, "fixture_groups"):
                    raw_groups = list(self._pm.project.fixture_groups or [])
                for g in raw_groups:
                    gid = str(g.get("id", "")) if isinstance(g, dict) else str(getattr(g, "id", ""))
                    if gid == str(group_id):
                        if isinstance(g, dict):
                            base_ids = list(g.get("base_order_fixture_ids", []) or [])
                        else:
                            base_ids = list(getattr(g, "base_order_fixture_ids", []) or [])
                        break
            except Exception:
                base_ids = []

        base_sequence_ids = [str(fid) for fid in (base_ids or []) if str(fid) in all_fixtures]
        # ★ R195：base_order 只做顺序追溯，成员以 fixture_ids 为准（第三处同病）
        ordered_ids = _order_members_by_base_seq(
            [str(fid) for fid in (fixture_ids or []) if str(fid) in all_fixtures],
            [] if live_topview else base_sequence_ids)
        actual_ids = _r183_narrow_to_post_filter(self, ordered_ids)
        selected = [all_fixtures[fid] for fid in actual_ids]
        self._current_group_id = group_id
        self._current_group_name = group_name
        self._current_base_order_ids = list(base_sequence_ids or ordered_ids)
        if live_topview:
            self._topview_live_fixture_ids = list(actual_ids)
            self._topview_live_base_order_ids = list(base_sequence_ids or ordered_ids)
        else:
            self._topview_live_fixture_ids = []
            self._topview_live_base_order_ids = []
        self._current_fixtures_selected = selected

        if self._fixture_positions and not ordered_ids:
            selected.sort(key=lambda f: (
                round(self._fixture_positions.get(f.id, (0, 0))[1], 1),
                round(self._fixture_positions.get(f.id, (0, 0))[0], 1)
            ))

        self._ch_list.load_fixtures(selected, defs, group_name)
        # 如果有活跃的 CUE 缓冲，把属于本组的设备值恢复到条带控件（★2026-09-17 范围门卫：
        # 只写本组设备，旧实现不过滤 → 其它组条目写进隐藏行，旧设备照样出光）。
        # 置 _cue_recall_in_progress:此回灌 batch 信号是程序行为,不算用户操作,不能清 buffer(否则切组丢跨组持久语义)。
        cue_buf = getattr(self, "_active_cue_buffer", {})
        if cue_buf:
            self._cue_recall_in_progress = True
            try:
                self._ch_list.restore_static_snapshot(cue_buf, None, actual_ids)
            except Exception:
                pass
            finally:
                self._cue_recall_in_progress = False
        self._rebuild_addr_cache(selected, defs)
        self._emit_fixture_selection_to_topview(actual_ids); _r183_after_group_load(self)
        # 审计 module-08 P2: 主选中路径从不刷新数控包「单控灯」能力
        # (2026-08-04 用户需求实际失效) — 补调用与 load 路径对齐。
        self._update_light_mode_capability()

        if hasattr(self, "_matrix_panel") and self._matrix_panel:
            fx_data = []
            for f in selected:
                pos = self._fixture_positions.get(f.id, (0, 0))
                fx_data.append({'id': f.id, 'x': pos[0], 'y': pos[1]})
            self._matrix_panel.set_fixtures(fx_data)

        # 调度台：设备组/选中变化是离散状态变化点 → 立即上报（不做高频轮询）
        self.report_to_bus(force=True)

    def _emit_fixture_selection_to_topview(self, ids: list):
        # ★★ R121 用户实报修复（"保存组/实时选中方向的信号问题"）：本面板在
        #   「由 TopView 反向驱动」或显式抑制期间**不得回吐选择**。原实现只在
        #   MainWindow 的 `_selected_by_topview` 守卫里判（依赖 `self.sender()`），
        #   而保存设备组触发的 `sync_groups_from_panel → load_groups` 是内部重载、
        #   sender 为 None → 守卫失效 → 回吐"默认全选" → 用户框选的子集被扩成整组。
        if _topview_driven(self):
            return          # 反向驱动 / 显式抑制期间不回吐（见上方说明）
        ordered, seen = [], set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid and sfid not in seen:
                ordered.append(sfid)
                seen.add(sfid)
        try:
            self.fixture_selection_changed.emit(ordered)
        except Exception:
            pass

    def _on_value_changed(self, fixture_id: str, ch_type: str, value: int):
        try:
            self.controlTrajectoryEvent.emit(
                f"programmer|strip|{ch_type}", int(value))
        except Exception:
            pass
        """旧版无来源层信号的兼容入口。

        这里不再把 merged 值无条件写入 fader pending。摇杆、音频和矩阵都会先更新
        ChannelTypeRow 自身的分层缓存，再由 _on_layer_value_changed 触发实时刷新。
        若继续在这里写入 fader，动态层的值会污染 fader 层并覆盖其它类型轨道。
        """
        self._schedule_live_output({})

        if not self._keepalive_timer.isActive():
            self._keepalive_timer.start()

    def _on_layer_value_changed(self, fixture_id: str, ch_type: str, value: int, layer: str):
        # 用户真实操作控件 → 交回控制权,清掉调入的 CUE 缓冲(不再切组回灌)。
        self._release_active_cue_buffer_on_user_edit()
        # ★★ R130：写值即重新接管 —— 与 `_on_layer_batch_changed` 同款。缺这一步时
        #   存场景自动清台置上的"已释放"标记会**卡死**：值照写、定时器照重启（输出真
        #   存在），标记却仍是 True → 该格既上不了总线（存不进下一个场景），也与实际
        #   输出不一致。实测：连存 3 个场景只落库 1 条。
        _rearm_output_after_edit(self)
        # ★ 性能：只累积到 pending，地址转换统一在 33ms flush 内批量执行
        #   （NM 收数的时机不变——本来就在 flush 时收），单点编辑不触发逐台转换。
        self._pending_layer_batch.setdefault(layer, {}).setdefault(fixture_id, {})[ch_type] = value
        self._schedule_live_output({})

        if not self._keepalive_timer.isActive():
            self._keepalive_timer.start()

    def _release_active_cue_buffer_on_user_edit(self):
        """用户手动操作控件时清空调入的 CUE 缓冲。调入/切组回灌期
        (_cue_recall_in_progress=True)触发的信号不算用户操作,跳过。

        ★ 2026-09-14 用户定案（"只要不是我正在编辑的就不存"）：这里同时清掉
          `_recalled_play` —— 用户真的动了控件 = 这是编辑，该格重新参与"保存当前
          正在编辑的效果"（没动过则保持标记，存新场景时被排除）。
        """
        if getattr(self, "_cue_recall_in_progress", False):
            return
        if getattr(self, "_active_cue_buffer", None):
            self._active_cue_buffer = {}
        self._recalled_play = False
        # ★★ 深查修正（改完关窗口不提示、改动丢失）：这里是"真实用户编辑控件"的唯一
        #   漏斗（回灌期已在上方 return），但此前**两个脏标记都没置** → 拖动条带/推杆
        #   后直接关窗口，工程不会被提示保存，改动静默丢失。只调 pm（桥接到主窗标记）。
        _mark_pm_dirty(self)

    def _on_sync_z_command(self, angle_deg, fixture_ids):
        """同步模式下 Programmer Z 滑块/摇杆 Z → **本次写入设备**的 Z 组命令。

        只影响同步模式设备（2026-08-04 用户需求）；非同步行不推送，X/Y 不动。
        实现与"为什么要无条件清命令"见 `_push_sync_z_command`（2026-09-19 修
        幽灵锁：清除被 `if not sync_ids` 挡住 → Z 永久锁死）。
        """
        _push_sync_z_command(self, angle_deg, fixture_ids)

    def _on_layer_batch_changed(self, batch: dict, layer: str):
        # 用户真实操作(Master/摇杆/范围条批量)→ 交回控制权,清 CUE 缓冲。
        self._release_active_cue_buffer_on_user_edit()
        _rearm_output_after_edit(self)   # 编辑即重新接管：解除已释放标记
        # ★ 性能：只把批次累积进 pending（同 fid 后写覆盖前写，最终值相同），
        #   地址转换推迟到 33ms flush 一次完成——拖动/摇杆 60-120Hz 事件不再
        #   每事件对 1992 台做转换+采样（实测每事件 100ms → 事件路径仅剩行内写值）。
        pend = self._pending_layer_batch.setdefault(layer, {})
        for _fid, _chans in (batch or {}).items():
            if _fid in pend:
                pend[_fid].update(_chans)
            else:
                pend[_fid] = dict(_chans)
        self._schedule_live_output({})

        ctrl = self._timeline_ctrl_ref
        if ctrl is not None and getattr(ctrl, '_recording', False) and self._nm_ref is not None:
            # 批量操作只安排一次刷新，录制帧由 _flush_live_output 基于汇总后的层数据生成。
            pass

        if not self._keepalive_timer.isActive():
            self._keepalive_timer.start()

    def _reset_live_layer_caches(self):
        """清空 ProgrammerPanel 侧的增量层缓存。仅在需要彻底释放输出状态时使用。"""
        self._layer_sparse_cache = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        self._dirty_layer_sparse = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        self._pending_live = {}
        self._pending_layer_batch = {}
        self._keepalive_data.clear()

    def _rebuild_live_layer_caches_from_rows(self):
        """按当前地址映射从 Row 状态重建完整层缓存。

        设备组切换或地址缓存重建时，不能简单清空缓存，否则已有 Programmer 输出会丢失。
        这里只在低频结构变化时做一次全量扫描，并把现有 Row._layers 重新转换为地址级缓存，
        既保持深度调优的热路径增量化，也保证切组、刷新设备列表后的输出语义不变。
        """
        if bool(getattr(self, "_programmer_output_released", False)):
            return
        self._layer_sparse_cache = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        self._dirty_layer_sparse = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        self._pending_live = {}
        try:
            rows = list(getattr(self._ch_list, "_rows", []) or [])
        except Exception:
            rows = []
        for row in rows:
            layers = getattr(row, "_layers", {}) or {}
            for layer_name in ("fader", "joystick", "audio", "matrix"):
                for fid, channels in (layers.get(layer_name, {}) or {}).items():
                    self._mark_layer_sparse_from_fixture_values(layer_name, {fid: dict(channels)})
        # 重建后不要立刻把所有通道当成用户刚操作的 dirty 输出；keepalive 会持续维持完整层状态。
        self._dirty_layer_sparse = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        self._keepalive_data = {
            layer_name: {int(uni): dict(chs) for uni, chs in self._layer_sparse_cache.get(layer_name, {}).items() if chs}
            for layer_name in getattr(self, "_keepalive_layers", ("fader", "joystick", "audio"))
        }

    def _recording_ctrl(self):
        ctrl = self._timeline_ctrl_ref
        if ctrl is not None and getattr(ctrl, "_recording", False):
            return ctrl
        return None

    def _feed_recording_frames(self, layer_names=None):
        ctrl = self._recording_ctrl()
        if ctrl is None:
            return
        if bool(getattr(ctrl, "_record_editor_mix", False)):
            return
        if layer_names is None:
            layer_names = getattr(self, "_record_frame_layers", ("fader", "joystick", "audio", "matrix"))
        rec_frames: Dict[int, np.ndarray] = {}
        for layer_name in layer_names:
            uni_map = self._layer_sparse_cache.get(layer_name, {})
            for uni, sparse in uni_map.items():
                if not sparse:
                    continue
                frame = rec_frames.setdefault(int(uni), np.zeros(512, dtype=np.uint8))
                for addr, val in sparse.items():
                    addr = int(addr)
                    if 0 <= addr < 512:
                        frame[addr] = int(val) & 0xFF
        for uni, frame in sorted(rec_frames.items()):
            try:
                ctrl.feed_dmx_frame(uni, frame)
            except Exception:
                pass

    def _mark_layer_sparse_from_fixture_values(self, layer: str, fixture_values: dict):
        """把 {fixture_id: {ch_type: value}} 增量转换为 {layer: universe: addr: value}。

        该函数只处理本次变化的数据，并同步写入完整层缓存与 dirty 缓存；输出语义与
        原先 _collect_layer_data 的地址转换一致，但避免每次刷新全量扫描所有 Row。
        """
        if layer not in self._layer_sparse_cache:
            self._layer_sparse_cache[layer] = {}
        if layer not in self._dirty_layer_sparse:
            self._dirty_layer_sparse[layer] = {}
        # ★ 性能：任何写入都递增版本（keepalive 据此跳过空闲期全量重发）
        self._layer_sparse_version += 1
        for fid, channels in (fixture_values or {}).items():
            fid_cache = self._addr_cache.get(fid)
            if not fid_cache:
                continue
            compiled_cache = self._addr_output_cache.get(str(fid), {})
            for ch_type, val in (channels or {}).items():
                addr_list = fid_cache.get(ch_type)
                if not addr_list:
                    if _norm_channel_type(ch_type) == "dimmer":
                        addr_list = fid_cache.get("dimmer", [])
                    if not addr_list and not (
                            _norm_channel_type(ch_type) == "dimmer" and
                            any(fid_cache.get(ct) for ct in ("R", "G", "B", "W"))):
                        continue
                try:
                    out_val = min(255.0, max(0.0, float(val)))
                except Exception:
                    out_val = 0.0
                spec = compiled_cache.get(str(ch_type)) or compiled_cache.get(_norm_channel_type(ch_type))
                if spec is not None and spec[0] == "direct":
                    mapped_val = min(255, max(0, int(round(float(out_val)))))
                    for uni, dmx_addr in spec[1]:
                        if not (0 <= int(dmx_addr) < 512):
                            continue
                        uni = int(uni)
                        addr = int(dmx_addr)
                        uni_cache = self._layer_sparse_cache[layer].setdefault(uni, {})
                        if uni_cache.get(addr) == mapped_val:
                            continue
                        uni_cache[addr] = mapped_val
                        self._dirty_layer_sparse[layer].setdefault(uni, {})[addr] = mapped_val
                    continue
                if spec is not None and spec[0] == "virtual_dimmer":
                    # ★ 性能：内联虚拟亮度展开——复用调用方已查到的 spec，
                    #   省去 _precompiled_channel_output 的二次查表与中间 mapped 列表
                    #   （大组 dimmer 拖动每 flush 8428 次调用，实测 64-76ms → ~45ms）。
                    #   数值与原路径逐字节一致：gain 同式、base 同缓存、双重取整恒等。
                    gain = max(0.0, min(1.0, float(out_val) / 255.0))
                    base = self._sample_fixture_color_values(fid_cache)
                    for _ct, _addrs in spec[1].items():
                        mapped_val = min(255, max(0, int(round(base.get(_ct, 0) * gain))))
                        for uni, dmx_addr in _addrs:
                            if not (0 <= int(dmx_addr) < 512):
                                continue
                            uni = int(uni)
                            addr = int(dmx_addr)
                            uni_cache = self._layer_sparse_cache[layer].setdefault(uni, {})
                            if uni_cache.get(addr) == mapped_val:
                                continue
                            uni_cache[addr] = mapped_val
                            self._dirty_layer_sparse[layer].setdefault(uni, {})[addr] = mapped_val
                    continue
                for mapped in self._precompiled_channel_output(fid, ch_type, out_val, fid_cache, addr_list):
                    uni, dmx_addr, mapped_val = mapped
                    if not (0 <= int(dmx_addr) < 512):
                        continue
                    uni = int(uni)
                    addr = int(dmx_addr)
                    mapped_val = min(255, max(0, int(round(float(mapped_val)))))
                    uni_cache = self._layer_sparse_cache[layer].setdefault(uni, {})
                    if uni_cache.get(addr) == mapped_val:
                        continue
                    uni_cache[addr] = mapped_val
                    self._dirty_layer_sparse[layer].setdefault(uni, {})[addr] = mapped_val
        # ★ 补光灯合成（2026-08-06 用户需求）：单控写效果灯 → 同包补光灯 =
        #   效果灯当前平均亮度（与 effect 侧 _apply_fill_lights 同式，领域前提 #6
        #   任何控制模式恒成立）。只在本轮写入命中 fill_map 效果灯成员时增量合成，
        #   未命中零开销；合成值写入本层 sparse 缓存 → flush/keepalive 自然带出。
        self._compose_fill_lights_for_writes(layer, fixture_values)

    def _fill_map_current(self):
        """全项目补光灯能力表（复用 effect_browser._light_capability_for_ids 判定，
        不重造配对）。key=(id(project), len(all_ids), id(_addr_cache))——换工程或
        地址表重建（新 dict 对象）即失效。返回 (fill_map, eff→fill 反向索引)。
        """
        cache = getattr(self, "_fill_map_cache", None)
        project = getattr(getattr(self, "_pm", None), "project", None)
        if project is None:
            return {}, {}
        addr_cache = getattr(self, "_addr_cache", None)
        key = (id(project), len(getattr(project, "fixtures", []) or []), id(addr_cache))
        if (cache is not None and cache[0] is project
                and cache[1] is addr_cache and cache[2] == key):
            return cache[3], cache[4]
        fill_map: Dict[str, List[str]] = {}
        try:
            eb = self._get_effect_browser_ref()
            if eb is not None and hasattr(eb, "_light_capability_for_ids"):
                ids = [str(getattr(fi, "id", "") or "") for fi in (project.fixtures or [])]
                if ids:
                    caps = eb._light_capability_for_ids(ids)
                    if isinstance(caps, dict) and isinstance(caps.get("fill_map"), dict):
                        fill_map = caps["fill_map"]
        except Exception:
            fill_map = {}
        reverse: Dict[str, List[str]] = {}
        for fill_fid, effs in fill_map.items():
            for e in (effs or []):
                reverse.setdefault(str(e), []).append(fill_fid)
        self._fill_map_cache = (project, addr_cache, key, fill_map, reverse)
        return fill_map, reverse

    def _compose_fill_lights_for_writes(self, layer: str, fixture_values: dict) -> None:
        """单控路径补光灯合成：补光灯 = 同包效果灯当前平均亮度（0-255 映射）。

        与 effect_engine._apply_fill_lights 同式：每灯亮度 = 最亮光通道
        (R/G/B/W)归一化，无光键（未写）按 0 计；ratio = Σ / 效果灯数 N；
        fill_dmx = round(ratio*255) 写入补光灯 R/G/B/W 四通道。全组无光
        （any_light=False）→ 保持补光灯自身值（不覆盖用户直接写入）。
        """
        if not fixture_values:
            return
        # 补光灯=光通道平均,只对光层有意义;joystick/audio 等非光层跳过。
        if str(layer) not in ("fader", "matrix"):
            return
        try:
            fill_map, reverse = self._fill_map_current()
        except Exception:
            return
        if not fill_map or not reverse:
            return
        touched = set()
        for fid in fixture_values:
            for ff in reverse.get(str(fid), ()):
                touched.add(str(ff))
        if not touched:
            return
        uni_map = self._layer_sparse_cache.setdefault(layer, {})
        dirty = self._dirty_layer_sparse.setdefault(layer, {})
        for fill_fid in touched:
            effs = fill_map.get(fill_fid, [])
            if not effs:
                continue
            total = 0.0
            any_light = False
            for eff in effs:
                fc = self._addr_cache.get(str(eff))
                if fc is None:
                    fc = self._addr_cache.get(eff)
                if not fc:
                    continue
                mx = 0
                for ct in ("R", "G", "B", "W"):
                    for uni, addr in fc.get(ct, []) or []:
                        v = uni_map.get(int(uni), {}).get(int(addr))
                        if v is not None:
                            mx = max(mx, int(v))
                            if int(v) > 0:
                                any_light = True
                total += mx / 255.0
            if not any_light:
                continue
            ratio = total / len(effs)
            fill_dmx = max(0, min(255, int(round(ratio * 255.0))))
            ffc = self._addr_cache.get(str(fill_fid))
            if ffc is None:
                ffc = self._addr_cache.get(fill_fid)
            if not ffc:
                continue
            for ct in ("R", "G", "B", "W"):
                for uni, addr in ffc.get(ct, []) or []:
                    uni, addr = int(uni), int(addr)
                    if not (0 <= addr < 512):
                        continue
                    if uni_map.get(uni, {}).get(addr) == fill_dmx:
                        continue
                    uni_map.setdefault(uni, {})[addr] = fill_dmx
                    dirty.setdefault(uni, {})[addr] = fill_dmx

    def _sample_fixture_color_values(self, fid_cache: dict) -> Dict[str, int]:
        """虚拟亮度基色 = 设备当前 fader 层颜色值(未写过色 = 满亮 255)。

        只采样隐藏后端 Row 的 fader 层/_values（用户直接写的颜色、CUE 调入值），
        不采样 _layer_sparse_cache——虚拟 dimmer 自己的输出也写 sparse 地址，
        采样它会拿到"亮度×基色"的衰减值，连续拖亮度会逐次变暗且无法恢复
        （PRG-INT-01 拖尾）。Row 层只记录用户操作/调入的颜色，天然干净；
        无记录 = 未写过颜色 = 满白。有地址的通道才参与;W 与 R/G/B 同权。
        """
        vals: Dict[str, int] = {}
        fid = str(fid_cache.get("__fixture_id", "") or "")
        # ★ 性能：基色只随颜色写入/地址表重建/切组变化，按版本缓存——
        #   拖动 dimmer 时每 flush 不再逐台采样 4 通道（2107 台 ≈ 46ms → 命中 ≈ 0）。
        ver = getattr(getattr(self, "_ch_list", None), "_vd_color_version", 0)
        cached = self._vd_color_cache.get(fid)
        if cached is not None and cached[0] == ver:
            return cached[1]
        for ct in ("R", "G", "B", "W"):
            addrs = fid_cache.get(ct) or []
            if not addrs:
                continue
            row_cur = self._row_fixture_channel_value(fid, ct)
            vals[ct] = 255 if row_cur is None else row_cur
        self._vd_color_cache[fid] = (ver, vals)
        return vals

    def _row_fixture_channel_value(self, fid: str, ct: str) -> Optional[int]:
        """查隐藏后端 ChannelTypeList 中管理 ct 的 Row 的 fader 层/合并值（无则 None）。

        row 查找按 ct 缓存（虚拟 dimmer 每帧 × 4 通道，避免逐帧全量扫描
        _rows/_dynamic_rows；组切换时失效缓存）。
        """
        cache = getattr(self, "_row_chan_cache", None)
        if cache is None:
            cache = self._row_chan_cache = {}
        # ★ 按 (fid, ct) 采样（审查 Bug-2）：类型分区后同一 ct 存在多行（每设备类型
        #   一行），按 ct 缓存"第一行"会采到不含该 fid 的行 → 虚拟亮度基色对非首
        #   类型设备误用满白。改为先按 fid 匹配行，再按 ct 兜底。
        key = (str(fid), ct)
        row = cache.get(key)
        if row is None or not _qobject_alive(row):
            row = None
            try:
                rows = list(getattr(self._ch_list, "_rows", []) or [])
            except Exception:
                return None
            for r in rows:
                if ct in getattr(r, "_ch_types", []) and _qobject_alive(r):
                    if str(fid) in getattr(r, "_fixture_id_set", set()):
                        cache[key] = r
                        row = r
                        break
            if row is None:
                # 兜底：无按 fid 匹配的行时回退首个含 ct 的行（兼容旧路径）
                for r in rows:
                    if ct in getattr(r, "_ch_types", []) and _qobject_alive(r):
                        cache[key] = r
                        row = r
                        break
        if row is None or not _qobject_alive(row):
            return None
        try:
            layer_val = (row._layers.get("fader", {}).get(fid, {}) or {}).get(ct)
            if layer_val is not None:
                return max(0, min(255, int(round(float(layer_val)))))
            merged = row._values.get(fid, {}).get(ct)
            if merged is not None:
                return max(0, min(255, int(round(float(merged)))))
        except Exception:
            return None
        return None

    def _expand_channel_output(self, ch_type: str, value: float, addr_list, fid_cache: dict) -> list[tuple]:
        """Expand logical 8-bit motor axes into physical coarse/fine DMX channels when present."""
        if _norm_channel_type(ch_type) == "dimmer" and not list(addr_list or []):
            gain = max(0.0, min(1.0, float(value) / 255.0))
            base = self._sample_fixture_color_values(fid_cache)
            mapped = []
            for ct in ("R", "G", "B", "W"):
                for uni, addr in fid_cache.get(ct, []) or []:
                    mapped.append((int(uni), int(addr), int(round(base.get(ct, 0) * gain))))
            return mapped
        # ★ 通用 16bit 拆分：任何 {X}/{X}_fine 对（含 function_N 对）都按电机逻辑
        #   输出——粗调滑块一个控件，输出自动拆成粗+精两个 DMX 字节。
        fine_addrs = list(fid_cache.get(ch_type + "__fine", []) or [])
        if fine_addrs:
            coarse, fine = _motor_8bit_to_16bit_pair(value)
            mapped = [(int(uni), int(addr), coarse) for uni, addr in (addr_list or [])]
            mapped.extend((int(uni), int(addr), fine) for uni, addr in fine_addrs)
            return mapped
        if str(ch_type).endswith("_fine"):
            return [(int(uni), int(addr), int(value)) for uni, addr in (addr_list or [])]
        return [(int(uni), int(addr), int(value)) for uni, addr in (addr_list or [])]

    def _precompiled_channel_output(self, fid: str, ch_type: str, value: float, fid_cache: dict, addr_list):
        spec = self._addr_output_cache.get(str(fid), {}).get(str(ch_type))
        if spec is None:
            norm_key = _norm_channel_type(ch_type)
            spec = self._addr_output_cache.get(str(fid), {}).get(norm_key)
        if spec is None:
            return self._expand_channel_output(ch_type, value, addr_list, fid_cache)
        kind = spec[0]
        if kind == "direct":
            out_val = int(value)
            return [(uni, addr, out_val) for uni, addr in spec[1]]
        if kind == "motor_pair":
            coarse, fine = _motor_8bit_to_16bit_pair(value)
            mapped = [(uni, addr, coarse) for uni, addr in spec[1]]
            mapped.extend((uni, addr, fine) for uni, addr in spec[2])
            return mapped
        if kind == "virtual_dimmer":
            gain = max(0.0, min(1.0, float(value) / 255.0))
            base = self._sample_fixture_color_values(fid_cache)
            mapped = []
            for ct, addrs in spec[1].items():
                out_val = int(round(base.get(ct, 0) * gain))
                mapped.extend((uni, addr, out_val) for uni, addr in addrs)
            return mapped
        return self._expand_channel_output(ch_type, value, addr_list, fid_cache)

    def _mark_layer_sparse_from_universe_values(self, layer: str, universe_values: dict):
        """把 {universe: {addr: value}} 直接合并到增量层缓存。"""
        if layer not in self._layer_sparse_cache:
            self._layer_sparse_cache[layer] = {}
        if layer not in self._dirty_layer_sparse:
            self._dirty_layer_sparse[layer] = {}
        for uni, channels in (universe_values or {}).items():
            uni = int(uni)
            for addr, val in (channels or {}).items():
                addr = int(addr)
                if 0 <= addr < 512:
                    out_val = min(255, max(0, int(val)))
                    uni_cache = self._layer_sparse_cache[layer].setdefault(uni, {})
                    if uni_cache.get(addr) == out_val:
                        continue
                    uni_cache[addr] = out_val
                    self._dirty_layer_sparse[layer].setdefault(uni, {})[addr] = out_val

    def _dirty_layers_snapshot(self):
        # 整体换引用而非逐层/逐地址深拷贝：旧 dict 只被本帧消费，
        # NM 对 sparse dict 只读（已验证），新周期从空 dict 开始。
        dirty = getattr(self, "_dirty_layer_sparse", {})
        self._dirty_layer_sparse = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}
        return dirty

    def _layer_cache_to_frames(self, layers: dict):
        """把稀疏层缓存按需转为 data/active 帧，仅用于旧 NM 兼容与录制。"""
        uni_data: Dict[str, Dict[int, np.ndarray]] = {}
        uni_active: Dict[str, Dict[int, np.ndarray]] = {}
        for layer_name, uni_map in (layers or {}).items():
            uni_data[layer_name] = {}
            uni_active[layer_name] = {}
            for uni, sparse in (uni_map or {}).items():
                if not sparse:
                    continue
                data = np.zeros(512, dtype=np.uint8)
                active = np.zeros(512, dtype=bool)
                for addr, val in sparse.items():
                    if 0 <= int(addr) < 512:
                        data[int(addr)] = int(val) & 0xFF
                        active[int(addr)] = True
                if np.any(active):
                    uni_data[layer_name][int(uni)] = data
                    uni_active[layer_name][int(uni)] = active
        return uni_data, uni_active

    # ★★★ 收集各层数据 + active 掩码（active=True 表示该地址被本层显式写过）★★★
    def _collect_layer_data(self):
        """收集 (data, active) 形式的各层数据。

        active 真正反映"本层有没有写过这个 DMX 地址"，这样下游的
        NetworkManager 在做基线/形状混合时就能正确区分"未写入"和"显式写 0"——
        摇杆中位 128 / 推杆 0 都能稳定写入，不再被混合阶段当成"未触碰"。
        """
        layer_bufs = {"fader": {}, "joystick": {}, "audio": {}, "matrix": {}}

        for row in self._ch_list._rows:
            for layer_name in layer_bufs:
                for fi_id, channels in row._layers.get(layer_name, {}).items():
                    if fi_id not in layer_bufs[layer_name]:
                        layer_bufs[layer_name][fi_id] = {}
                    for ch_type, val in channels.items():
                        layer_bufs[layer_name][fi_id][ch_type] = val

        uni_data = {}
        uni_active = {}
        for layer_name, fixtures_data in layer_bufs.items():
            uni_data[layer_name] = {}
            uni_active[layer_name] = {}
            for fi_id, channels in fixtures_data.items():
                cache = self._addr_cache.get(fi_id, {})
                for ch_type, val in channels.items():
                    addr_list = cache.get(ch_type, [])
                    for uni, addr, mapped_val in self._precompiled_channel_output(fi_id, ch_type, float(val), cache, addr_list):
                        if uni not in uni_data[layer_name]:
                            uni_data[layer_name][uni] = np.zeros(512, dtype=np.uint8)
                            uni_active[layer_name][uni] = np.zeros(512, dtype=bool)
                        uni_data[layer_name][uni][addr] = int(round(float(mapped_val)))
                        uni_active[layer_name][uni][addr] = True

        return uni_data, uni_active

    def _schedule_live_output(self, dmx_data: dict):
        """调度即时输出；兼容旧入口的 fader 数据会被写入地址级 dirty 缓存。"""
        if dmx_data:
            self._mark_layer_sparse_from_fixture_values("fader", dmx_data)
        if not self._live_output_timer.isActive():
            self._live_output_timer.start()
        # 调度台上报（节流在 report_to_bus 内部；失败不影响出光）
        self.report_to_bus()

        # ★★★ 按控制源层分别写入 NetworkManager（含 active mask）★★★
    def _flush_live_output(self):
        """增量输出路径：只处理本轮 dirty DMX 地址，由 NM 统一混音输出。

        深度调优重点：不再每 33ms 扫描所有 Row 的 _layers；绝大多数拖动只会把本轮
        变化的地址写入 NetworkManager。完整层状态保存在 _layer_sparse_cache，供
        keep-alive 与录制使用，因此不会改变 fader/joystick/audio/matrix 的层语义。
        """
        # 兼容旧的 _pending_live 入口；正常路径已经直接写入 _dirty_layer_sparse。
        pending = self._pending_live
        self._pending_live = {}
        if pending:
            self._mark_layer_sparse_from_universe_values("fader", pending)

        # ★ 性能：批量转换本窗口内累积的输入事件（拖动/摇杆/条带），
        #   每 33ms 只做一次逐台地址转换，输出到达 NM 的时机与逐事件转换一致。
        if self._pending_layer_batch:
            for _pl, _pbatch in self._pending_layer_batch.items():
                self._mark_layer_sparse_from_fixture_values(_pl, _pbatch)
            self._pending_layer_batch = {}

        dirty_layers = self._dirty_layers_snapshot()
        has_dirty = any(bool(uni_map) for uni_map in dirty_layers.values())

        # 清空未激活 Row 的 audio 层残留；该逻辑保留原行为，但只在 audio 层确实
        # 有本轮写入时才执行，避免每 33ms 全量扫所有 Row。
        if dirty_layers.get("audio"):
            for row in self._ch_list._rows:
                if not row._audio_active:
                    row._layers["audio"].clear()

        # 1) 只把本轮 dirty 地址写给 NetworkManager。
        dirty_frames = None
        dirty_active = None
        for layer_name, uni_dict in dirty_layers.items():
            if not isinstance(uni_dict, dict) or not uni_dict:
                continue
            nm = self._nm_ref
            if nm is not None and hasattr(nm, "update_layer_sparse_batch"):
                try:
                    nm.update_layer_sparse_batch(layer_name, uni_dict)
                    continue
                except Exception:
                    pass
            for uni, sparse_channels in uni_dict.items():
                if not sparse_channels:
                    continue
                if nm is not None:
                    try:
                        if hasattr(nm, "update_layer_sparse"):
                            nm.update_layer_sparse(layer_name, uni, sparse_channels)
                        else:
                            if dirty_frames is None or dirty_active is None:
                                dirty_frames, dirty_active = self._layer_cache_to_frames(dirty_layers)
                            data = dirty_frames.get(layer_name, {}).get(uni)
                            act = dirty_active.get(layer_name, {}).get(uni)
                            if data is None or act is None:
                                continue
                            if hasattr(nm, "merge_layer"):
                                nm.merge_layer(layer_name, uni, data, active=act)
                            elif hasattr(nm, "update_layer"):
                                prev = getattr(nm, "_layers", {}).get(layer_name, {}).get(uni)
                                if prev is not None:
                                    merged_d = prev[0].copy()
                                    merged_a = prev[1].copy()
                                    merged_d[act] = data[act]
                                    merged_a[act] = True
                                    nm.update_layer(layer_name, uni, merged_d, active=merged_a)
                                else:
                                    nm.update_layer(layer_name, uni, data, active=act)
                    except Exception:
                        pass

        # 2) 本轮有 joystick dirty 时，立即驱动一次自映射运行时，保持原先“动摇杆即响应”。
        joystick_active = bool(dirty_layers.get("joystick"))
        if joystick_active:
            # 解析一次并缓存到实例，避免每个 joystick-dirty 帧都走 import try/except。
            get_selfmap_engine = getattr(self, "_selfmap_engine_getter", None)
            if get_selfmap_engine is None:
                try:
                    from ..processing.self_map_engine import get_selfmap_engine
                except Exception:
                    try:
                        from src.processing.self_map_engine import get_selfmap_engine
                    except Exception:
                        get_selfmap_engine = None
                self._selfmap_engine_getter = get_selfmap_engine
            if get_selfmap_engine is not None:
                try:
                    engine = get_selfmap_engine()
                    if self._pm is not None:
                        engine.set_project_manager(self._pm)
                    if self._nm_ref is not None:
                        engine.set_network_manager(self._nm_ref)
                    enabled_attr = getattr(engine, "is_enabled", False)
                    is_enabled = bool(enabled_attr() if callable(enabled_attr) else enabled_attr)
                    if is_enabled:
                        engine._tick()
                except Exception:
                    pass

        # 3) 录制时基于完整层缓存生成帧，避免只录 dirty 地址导致 Clip 稀疏状态丢失。
        if has_dirty:
            self._feed_recording_frames()

        # 4) keep-alive 直接复用完整稀疏层缓存；只刷新源层，不把 matrix 持续回写成 Programmer 常驻源。
        # ★ 性能：无本轮 dirty 且层缓存对象未替换（_reset/_rebuild 会换新对象）时，
        #   上一份 _keepalive_data 内容与本次逐字节相同，跳过重建（至冬规模 ~11 万
        #   条目/帧的深拷贝只在有变化时发生；NM 对传入 dict 只读，已验证）。
        if has_dirty or getattr(self, "_last_keepalive_source", None) is not self._layer_sparse_cache:
            # NM 对传入 dict 只读（已验证），因此直接复用 sparse 内层 dict 引用，
            # 避免 11 万条目的深拷贝造成每轮 flush 上百 ms 卡顿。
            self._keepalive_data = {
                layer_name: {int(uni): chs for uni, chs in self._layer_sparse_cache.get(layer_name, {}).items() if chs}
                for layer_name in getattr(self, "_keepalive_layers", ("fader", "joystick", "audio"))
            }
            self._last_keepalive_source = self._layer_sparse_cache
        if (has_dirty or self._keepalive_data) and self._keepalive_data and not self._keepalive_timer.isActive():
            self._keepalive_timer.start()

    # ★★★ keep-alive 按各源层分别写回（fader→NM fader, joystick→NM joystick, audio→NM audio）★★★
    def _send_keepalive(self):
        """定时刷新 Programmer 各源层 NM 数据，并在录制中持续喂帧。

        ★ 修复：旧逻辑会把所有数据塞到 NM "fader" 一层，使得摇杆中位 128 这种
          应作为 SHAPE 偏移叠加的值变成 BASE 绝对值，影响混合结果。
        """
        # ★ 性能：层版本未变（无新写入）且 NM 侧各层仍有数据时跳过全量重发
        #   （100ms 定时器遍历 11 万条 ≈ 数 ms/次；NM stop() 清空 _layers 后
        #   空层守卫强制补发，保证重连输出不丢）。
        nm = self._nm_ref
        if (getattr(self, "_last_keepalive_version", None) == self._layer_sparse_version
                and nm is not None):
            try:
                if all(nm._layers.get(ln) for ln in getattr(self, "_keepalive_layers", ("fader", "joystick", "audio"))):
                    return
            except Exception:
                pass
        self._last_keepalive_version = self._layer_sparse_version
        # 1) 按层各自写回 NM
        for layer_name, uni_map in self._keepalive_data.items():
            if not uni_map:
                continue
            nm = self._nm_ref
            if nm is not None and hasattr(nm, "update_layer_sparse_batch"):
                try:
                    nm.update_layer_sparse_batch(layer_name, uni_map, touch_ltp=False)
                    continue
                except Exception:
                    pass
            for uni, sparse_channels in uni_map.items():
                if not sparse_channels:
                    continue
                try:
                    if nm is not None:
                        if hasattr(nm, "update_layer_sparse"):
                            nm.update_layer_sparse(layer_name, uni, sparse_channels, touch_ltp=False)
                        else:
                            layer_frame = np.zeros(512, dtype=np.uint8)
                            active_mask = np.zeros(512, dtype=bool)
                            for addr, val in sparse_channels.items():
                                if 0 <= addr < 512:
                                    layer_frame[addr] = val
                                    active_mask[addr] = True
                            if hasattr(nm, "merge_layer"):
                                nm.merge_layer(layer_name, uni, layer_frame, active=active_mask, touch_ltp=False)
                            elif hasattr(nm, "update_layer"):
                                prev = getattr(nm, "_layers", {}).get(layer_name, {}).get(uni)
                                if prev is not None:
                                    merged_d = prev[0].copy()
                                    merged_a = prev[1].copy()
                                    merged_d[active_mask] = layer_frame[active_mask]
                                    merged_a[active_mask] = True
                                    nm.update_layer(layer_name, uni, merged_d, active=merged_a, touch_ltp=False)
                                else:
                                    nm.update_layer(layer_name, uni, layer_frame, active=active_mask, touch_ltp=False)
                except Exception as e:
                    log.debug("Programmer keepalive layer=%s u=%s: %s",
                              layer_name, uni, e)

        # 2) 录制时按常驻源层喂帧，保持旧语义：matrix 只在自身 flush 时录制，
        # 不作为 keepalive 常驻源持续回写。
        self._feed_recording_frames(getattr(self, "_keepalive_layers", ("fader", "joystick", "audio")))

    def _on_smooth_preset_changed(self, index: int):
        if not _SMOOTHER_AVAILABLE or get_smoother is None:
            return
        preset_id = self._smooth_combo.itemData(index)
        if preset_id:
            try:
                smoother = get_smoother()
                smoother.set_preset(preset_id)
                # 双保险：旁通时关闭平滑并清状态，非旁通时打开平滑。
                smoother.enabled = (preset_id != "bypass")
                smoother.reset_states()
                if preset_id == "bypass":
                    self._keepalive_timer.setInterval(100)
                else:
                    self._keepalive_timer.setInterval(25)
                if self._keepalive_data and not self._keepalive_timer.isActive():
                    self._keepalive_timer.start()
                log.info("输出平滑预设切换为: %s", preset_id)
            except Exception as e:
                log.debug("set smooth preset failed: %s", e)

    def _addr_cache_signature(self, fixtures, defs: dict) -> tuple:
        """地址缓存的输入指纹：影响 _rebuild_addr_cache 结果的全部输入。

        结果只取决于：传入 fixtures + 全部 _current_fixtures 的
        (id, universe, start_channel, def_id) 以及对应 def 的 channels 列表
        与 function_channel_defaults（造型曲线首尾淡入淡出默认值依赖它，改了需重建+清默认值缓存）。
        与之无关的工程编辑（拖 clip、改名等）不会改变此指纹，可整段跳过重建。
        """
        all_fixtures = getattr(self, '_current_fixtures', []) or []
        all_defs = getattr(self, '_fixture_defs_cache', {}) or defs

        def _fi_sig(fi, dmap):
            fd = dmap.get(getattr(fi, "def_id", None))
            channels = tuple(getattr(fd, "channels", []) or []) if fd is not None else ()
            fcd = getattr(fd, "function_channel_defaults", {}) or {} if fd is not None else {}
            fcd_sig = tuple(sorted((str(k), int(v)) for k, v in fcd.items())) if fcd else ()
            return (
                str(getattr(fi, "id", "") or ""),
                int(getattr(fi, "universe", 1) or 1),
                int(getattr(fi, "start_channel", 1) or 1),
                str(getattr(fi, "def_id", "") or ""),
                channels,
                fcd_sig,
            )

        return (
            tuple(_fi_sig(fi, defs) for fi in fixtures),
            tuple(_fi_sig(fi, all_defs) for fi in all_fixtures),
        )

    def _rebuild_addr_cache(self, fixtures, defs: dict):
        """重建地址缓存。

        ★ v23 修复：始终保留全部设备的地址映射，而不是只保留当前选中组。
        这样即使用户切换了设备组，Row 中残留的其它设备层数据仍然能被
        正确转换为 DMX 地址并写入 NM，避免切组/摇杆操作时丢失其它类型
        通道的数据。

        ★ 性能：大工程（1356 设备）实测全量重建约 200ms。加输入指纹守卫，
        设备/定义未变时直接复用上次结果，避免与设备无关的工程编辑触发重建。
        """
        # 廉价结构预检:地址缓存只取决于全部设备+定义的结构(非选择)。选择变化(如全选)不改结构,
        # 但完整指纹要遍历 1356 设备×通道(~万次)才能判定"没变"。先用 id+len 快检,结构没动就直接
        # 复用,连完整指纹都不算——把全选等"仅选择变化"的重建开销降到接近零。
        all_fx = getattr(self, "_current_fixtures", []) or []
        all_dm = getattr(self, "_fixture_defs_cache", {}) or defs or {}
        cheap = (id(all_fx), len(all_fx), id(all_dm), len(all_dm))
        if (cheap == getattr(self, "_addr_cache_cheap_sig", None)
                and getattr(self, "_addr_cache", None) is not None):
            return
        try:
            sig = self._addr_cache_signature(fixtures, defs)
        except Exception:
            sig = None
        if (
            sig is not None
            and sig == getattr(self, "_addr_cache_signature_cache", None)
            and getattr(self, "_addr_cache", None) is not None
        ):
            self._addr_cache_cheap_sig = cheap
            return
        cache: Dict[str, Dict[str, tuple]] = {}
        # 先加入传入的 fixtures（当前选中组）
        for fi in fixtures:
            fd = defs.get(fi.def_id)
            if fd is None:
                continue
            ch_list = getattr(fd, 'channels', []) or []
            ch_map: Dict[str, List[tuple]] = {}
            ch_map["__fixture_id"] = str(getattr(fi, "id", "") or "")
            for offset, ch_type in enumerate(ch_list):
                dmx_addr = fi.start_channel - 1 + offset
                if 0 <= dmx_addr < 512:
                    ch_norm = _norm_channel_type(ch_type)
                    if ch_norm == "dimmer":
                        continue
                    if str(ch_type).endswith("_fine"):
                        coarse_type = str(ch_type)[:-5]
                        ch_map.setdefault(ch_type, []).append((fi.universe, dmx_addr))
                        ch_map.setdefault(coarse_type + "__fine", []).append((fi.universe, dmx_addr))
                        continue
                    if ch_norm in ("r", "red"):
                        ch_map.setdefault("R", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("g", "green"):
                        ch_map.setdefault("G", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("b", "blue"):
                        ch_map.setdefault("B", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("w", "white"):
                        ch_map.setdefault("W", []).append((fi.universe, dmx_addr))
                    if ch_type not in ch_map:
                        ch_map[ch_type] = []
                    ch_map[ch_type].append((fi.universe, dmx_addr))
            if _fixture_has_light_channels(fd):
                ch_map.setdefault("dimmer", [])
            cache[fi.id] = ch_map
        # ★ 补充：把全部设备中不在当前选中组的设备也加入缓存
        all_fixtures = getattr(self, '_current_fixtures', []) or []
        all_defs = getattr(self, '_fixture_defs_cache', {}) or defs
        for fi in all_fixtures:
            if fi.id in cache:
                continue  # 已经在缓存中
            fd = all_defs.get(fi.def_id)
            if fd is None:
                continue
            ch_list = getattr(fd, 'channels', []) or []
            ch_map: Dict[str, List[tuple]] = {}
            ch_map["__fixture_id"] = str(getattr(fi, "id", "") or "")
            for offset, ch_type in enumerate(ch_list):
                dmx_addr = fi.start_channel - 1 + offset
                if 0 <= dmx_addr < 512:
                    ch_norm = _norm_channel_type(ch_type)
                    if ch_norm == "dimmer":
                        continue
                    if str(ch_type).endswith("_fine"):
                        coarse_type = str(ch_type)[:-5]
                        ch_map.setdefault(ch_type, []).append((fi.universe, dmx_addr))
                        ch_map.setdefault(coarse_type + "__fine", []).append((fi.universe, dmx_addr))
                        continue
                    if ch_norm in ("r", "red"):
                        ch_map.setdefault("R", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("g", "green"):
                        ch_map.setdefault("G", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("b", "blue"):
                        ch_map.setdefault("B", []).append((fi.universe, dmx_addr))
                    elif ch_norm in ("w", "white"):
                        ch_map.setdefault("W", []).append((fi.universe, dmx_addr))
                    if ch_type not in ch_map:
                        ch_map[ch_type] = []
                    ch_map[ch_type].append((fi.universe, dmx_addr))
            if _fixture_has_light_channels(fd):
                ch_map.setdefault("dimmer", [])
            cache[fi.id] = ch_map
        self._addr_cache = cache
        # 地址表结构重建 → 虚拟亮度基色采样缓存一并失效（地址映射已变）
        _cl = getattr(self, "_ch_list", None)
        if _cl is not None:
            _cl._vd_color_version = getattr(_cl, "_vd_color_version", 0) + 1
        self._traj_default_cache = {}  # 设备/定义变了，造型淡入淡出默认值缓存失效
        self._traj_fx_by_id = None     # id→fixture 映射一并失效
        output_cache: Dict[str, Dict[str, tuple]] = {}
        for fid, ch_map in cache.items():
            fid_cache: Dict[str, tuple] = {}
            for ch_type, addr_list in ch_map.items():
                if str(ch_type).startswith("__"):
                    continue
                norm = _norm_channel_type(ch_type)
                if norm == "dimmer" and not list(addr_list or []):
                    rgbw = {
                        ct: tuple((int(uni), int(addr)) for uni, addr in (ch_map.get(ct, []) or []))
                        for ct in ("R", "G", "B", "W")
                    }
                    if any(rgbw.values()):
                        fid_cache[str(ch_type)] = ("virtual_dimmer", rgbw)
                    continue
                fine_addrs = tuple(
                    (int(uni), int(addr))
                    for uni, addr in (
                        ch_map.get(ch_type + "__fine", [])
                        or []
                    )
                )
                if fine_addrs:
                    coarse_addrs = tuple((int(uni), int(addr)) for uni, addr in (addr_list or []))
                    fid_cache[str(ch_type)] = ("motor_pair", coarse_addrs, fine_addrs)
                    continue
                fid_cache[str(ch_type)] = (
                    "direct",
                    tuple((int(uni), int(addr)) for uni, addr in (addr_list or [])),
                )
            output_cache[str(fid)] = fid_cache
        self._addr_output_cache = output_cache
        self._addr_cache_signature_cache = sig
        self._addr_cache_cheap_sig = cheap   # 结构快检指纹,下次仅选择变化时直接命中
        # Patch / 设备组地址映射变更后，按新地址映射重建缓存，避免设备组切换时丢失已有 Programmer 输出。
        self._rebuild_live_layer_caches_from_rows()
    def _on_joy_amplitude_changed(self, overrides: dict):
        """
        ★ 摇杆幅度控制模式回调：将摇杆幅度参数转发到 EffectBrowser。
        路径：ChannelTypeRow → ChannelTypeList → ProgrammerPanel → EffectBrowser
        """
        # 获取 EffectBrowser 引用（通过 MainWindow）
        eb = self._get_effect_browser_ref()
        if eb is None:
            return
        # 过滤掉 None 值（用于清除）
        clean_overrides = {}
        clear_keys = []
        for k, v in overrides.items():
            if v is None:
                clear_keys.append(k)
            else:
                clean_overrides[k] = v
        # 清除指定的键
        if clear_keys and hasattr(eb, '_param_overrides'):
            for k in clear_keys:
                eb._param_overrides.pop(k, None)
        # 更新有效的覆盖值
        if clean_overrides and hasattr(eb, 'update_param_overrides'):
            eb.update_param_overrides(clean_overrides)

    def _get_effect_browser_ref(self):
        """获取 EffectBrowser widget 引用（通过 parent chain 查找 MainWindow）。"""
        # 缓存引用
        if hasattr(self, '_effect_browser_cache') and self._effect_browser_cache is not None:
            return self._effect_browser_cache
        # 向上查找 MainWindow
        p = self.parent()
        while p is not None:
            if hasattr(p, '_effect_browser_widget'):
                self._effect_browser_cache = p._effect_browser_widget
                return p._effect_browser_widget
            p = p.parent() if hasattr(p, 'parent') else None
        return None

    def link_timeline_audio(self, audio_path: str):
        for row in self._ch_list._rows:
            aw = getattr(row, '_audio_widget_ref', None)
            if aw is not None:
                aw.link_timeline(audio_path)

    def on_timeline_tick(self, pos: float):
        for row in self._ch_list._rows:
            aw = getattr(row, '_audio_widget_ref', None)
            if aw is not None:
                aw.on_timeline_tick(pos)

    def on_timeline_play(self, pos: float):
        for row in self._ch_list._rows:
            aw = getattr(row, '_audio_widget_ref', None)
            if aw is not None:
                aw.on_timeline_play(pos)

    def on_timeline_stop(self):
        for row in self._ch_list._rows:
            aw = getattr(row, '_audio_widget_ref', None)
            if aw is not None:
                aw.on_timeline_stop()

    def clear_live_output(self):
        """Stop Programmer output without resetting the programming UI state."""
        try:
            if hasattr(self, "_live_output_timer"):
                self._live_output_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "_keepalive_timer"):
                self._keepalive_timer.stop()
        except Exception:
            pass

        self._pending_live = {}
        self._keepalive_data.clear()
        self._reset_live_layer_caches()
        self._programmer_output_released = True
        # ★ R173: 状态反馈
        self.status_feedback.emit("Programmer 输出已释放", 3000)
        # ★★ R130：释放即重报总线（`_bus_nodes` 见 released → 返回空 → 差集删除把本
        #   面板窗格摘掉）。漏这一步则窗格永久残留，下次存场景把上一次的效果一起抄走。
        self.report_to_bus(force=True)

        # ★ 2026-09-14 深查修正（stuck output）：每行还有自己的 33ms 节流单发定时器
        #   （`ChannelTypeRow._strip_timer`/`_master_timer`）。释放前刚拖过条带/推杆时，
        #   那枚定时器会在释放**之后**才到点，发出
        #   `fixture_layer_batch_changed` → `_on_layer_batch_changed`
        #   → `_schedule_live_output`（重启 live/keepalive 定时器）→ 又把层写回去。
        #   实测：释放后 120ms `fader` 层被重新写回（值 180）。必须在此把它排空。
        _drain_row_timers(self)
        # ★ 2026-09-17 深查修正（生成器绕开释放）：轨迹回放/音频驱动每帧重写层，
        #   只停面板定时器拦不住——下一拍就 `_schedule_live_output` 把层写回。
        _stop_live_generators(self)

        _clear_programmer_layers(self)
        _detach_programmer_editor_state(self)
        _clear_sync_z(getattr(self, "_nm_ref", None))

    def reset_project_state(self):
        """新建/切换空项目时清理 Programmer 的项目级内存状态。"""
        try:
            if hasattr(self._cue_grid, 'clear_cues'):
                self._cue_grid.clear_cues()
        except Exception:
            pass
        try:
            # Programmer CUE 数据层（静态位置，模型保留）按项目级状态清理，避免跨项目残留。
            get_programmer_cue_store().clear()
        except Exception:
            pass
        try:
            self._ch_list.clear_all()
        except Exception:
            pass
        try:
            self.clear_live_output()
        except Exception:
            pass
        try:
            if self._nm_ref is not None:
                for layer_name in ("fader", "programmer", "audio", "joystick", "matrix", "matrix_audio"):
                    if hasattr(self._nm_ref, 'clear_layer'):
                        self._nm_ref.clear_layer(layer_name)
        except Exception:
            pass

    def _on_cue_selected(self, cue: CueData):
        if hasattr(cue, "self_map_rules"):
            try:
                from ..processing.self_map_engine import get_selfmap_engine
            except Exception:
                from src.processing.self_map_engine import get_selfmap_engine
            engine = get_selfmap_engine()
            engine.set_project_manager(self._pm)
            engine.set_network_manager(self._nm_ref)
            engine.load_cue(cue)
            engine.set_enabled(True)
            return
        self._ch_list.load_cue(cue)

    def add_generalized_cue(self, cue):
        """接收 SelfMapPanel 发来的广义 Cue，并加入底部 Cue 列表，同时立即作为当前映射关系启用。"""
        if not hasattr(cue, "color"):
            try:
                cue.color = "#8E44AD"
            except Exception:
                pass
        self._cue_grid.add_cue(cue)

        # 用户从自映射页“发送到 Programmer”后，通常期望接下来在 Programmer
        # 里动摇杆就立刻按该 Cue 的映射关系响应，而不是还要再手动点选一次 Cue。
        if hasattr(cue, "self_map_rules"):
            try:
                from ..processing.self_map_engine import get_selfmap_engine
            except Exception:
                from src.processing.self_map_engine import get_selfmap_engine
            try:
                engine = get_selfmap_engine()
                engine.set_project_manager(self._pm)
                engine.set_network_manager(self._nm_ref)
                engine.load_cue(cue)
                engine.set_enabled(True)
            except Exception:
                pass

    def snapshot_static_values(self) -> Dict[str, Dict[str, int]]:
        """供调度台抓取：返回当前所有控制条带的静态位置值快照。

        优先取 marked（用户已动过的通道），为空时退回全量值。返回
        {fixture_id: {ch_type: 0-255}}。
        """
        # ``clear_live_output`` deliberately leaves the visible control cache in
        # place so the UI does not jump.  That cache is no longer an authored
        # value after an explicit release and must not cross a save boundary.
        if bool(getattr(self, "_editor_state_detached", False)):
            return {}
        values = self._ch_list.get_marked_values(include_detached=False)
        if not values and not getattr(self, "_editor_release_seen", False):
            values = self._ch_list.snapshot_static_values()
        return values

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：静态通道总线上报（唯一出口）
    #
    #  为什么在这里：Programmer 的"效果"就是**当前设备组/通道类型上的静态值集合**，
    #  即面板既有 snapshot_static_values()（CUE 同款 values 结构）能回答的东西。
    #
    #  ★ 身份契约：一窗格 = (panel, track_type, group_key)。track_type = 通道类型，
    #  group_key 由设备 id 内容寻址 → "同值不同设备""同设备不同通道类型"天然两格；
    #  **绝不按 effect_id/名称/分类合并**（用户：微差即两种效果）。
    # ══════════════════════════════════════════════════════════════════════════

    #: 上报节流（秒）：输出路径可能 30Hz，总线只需人眼刷新率级别
    _BUS_REPORT_INTERVAL = 0.25

    @staticmethod
    def _dispatch_group_key(ids) -> str:
        """设备 id 内容寻址的设备集身份（与 EffectBrowser._group_key_from_ids 同规则）。"""
        out, seen = [], set()
        for fid in (ids or []):
            sfid = str(fid)
            if sfid and sfid not in seen:
                seen.add(sfid)
                out.append(sfid)
        return "grp:" + "|".join(out) if out else "__all__"

    def _bus_nodes(self) -> list:
        """把当前静态通道值转成调度台窗格（一通道类型一节点，不去重）。"""
        from ..processing.dispatch import EffectNode
        nodes: List[EffectNode] = []
        try:
            # ★★ R130 用户实报（存场景自动清台后窗格仍留在总线 → 下次存场景把上一次
            #   的效果一起抄走）：`_programmer_output_released` = 无输出；释放**刻意
            #   保留 UI 值**，故只按值表上报会让已释放照样成窗格。IfExp 不计门禁 CC。
            released = bool(getattr(self, "_programmer_output_released", False))
            # ★★ 2026-09-19（大组内多个子集的值只能存下最后一个）：作用域 =
            #   **大组基准序 ∪ 当前子集**（见 `_dispatch_scope_ids`）。
            ids = [] if released else _dispatch_scope_ids(self)
            group_key = self._dispatch_group_key(ids)
            group_name = (str(getattr(self, "_current_group_name", "") or "")
                          or ("临时选中" if ids else "全部设备"))
            # ★ 组身份回填（2026-09-13）：供调度台"子母文件夹"归类；伪 id
            #   （__all__/__topview_live__/…）由调度台侧 `group_resolver` 甄别清空。
            cur_group_id = str(getattr(self, "_current_group_id", "") or "")
            # 复用面板既有静态快照设施（CUE 同款 values 结构），不新增第二套序列化
            snapshot = {} if released else _programmer_bus_snapshot(self)
            # ★ 2026-09-17 范围门卫（用户实报范围污染）：只上报**编辑作用域内**的
            #   条目 —— 行是跨组容器（切组不丢值），不过滤会把已离开该大组的旧通道
            #   也报成窗格（节点 scope 与值表不一致的源头）。
            if ids:
                snapshot = _scope_static_values(snapshot, ids)
            by_ch: Dict[str, Dict[str, int]] = {}
            for fid, chs in snapshot.items():
                for ct, val in (chs or {}).items():
                    by_ch.setdefault(str(ct), {})[str(fid)] = val
            recalled = bool(getattr(self, "_recalled_play", False))
            for ct in sorted(by_ch):
                nodes.append(_programmer_node(
                    ct, by_ch[ct], ids,
                    (group_key, group_name, cur_group_id), recalled))
        except Exception:
            log.debug("programmer: _bus_nodes failed", exc_info=True)
        return nodes

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前静态通道窗格到总线（节流；失败绝不影响出光）。"""
        try:
            now = _time.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ..processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 逐键 upsert + 差集删除：某通道类型清空时只删它自己的格，不整体 clear_panel
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    #  面板状态快照（save_state / load_state）
    #
    #  ★ 2026-09-13 补：Programmer 曾是**唯一没有 save_state 的面板**，导致
    #    1) 工程级 undo 无法还原它（main_window._collect_project_state_for_save
    #       对它只写 audio_driver_state）；
    #    2) 调度台缓存靠专用通道 capture_state，与其它面板协议不一致。
    #    用户反馈"拉了条带，还原只恢复一个值、控件没还原"——根因就是
    #    **条带形状 `_range_states`（mode/base/target）从未被序列化**。
    #    现纳入 save_state，与 TopView 的 selected_ids 同范式。
    # ══════════════════════════════════════════════════════════════════════
    def _collect_range_states(self) -> dict:
        """收集每通道类型的条带形状（mode/base/target）。

        真源在 ChannelTypeRow._range_states（条带与 range 两控件共享）。"""
        out: dict = {}
        try:
            ch = getattr(self, "_ch_list", None)
            for row in (getattr(ch, "_rows", []) or []):
                states = getattr(row, "_range_states", None) or {}
                did = str(getattr(row, "_def_id", "") or "")
                for ct, st in states.items():
                    if not isinstance(st, dict):
                        continue
                    # ★ 键含 def_id：两个设备类型同名通道不得互相覆盖（见函数说明）
                    out[_range_state_key(did, ct)] = _clean_range_state(st)
        except Exception:
            pass
        return out

    def save_state(self) -> dict:
        """完整面板状态（供工程 undo / 调度台缓存复用同一协议）。

        含：静态值表 + 设备作用域 + 每行控件（翼/块/方向）+ **条带形状**。
        其它面板（topview/effect/matrix…）都用同一 save_state→project.*_state
        协议，Programmer 自此对齐。
        """
        try:
            base = dict(self.dispatch_capture_state() or {})
        except Exception:
            base = {}
        # ★ 2026-09-17：`dispatch_capture_state` 现已自带**按作用域过滤**的 range_states，
        #   仅当捕获整体失败（base 无该键）时按当前 live 选择过滤兜底。
        if "range_states" not in base:
            live = [str(x) for x in (self.get_live_selected_fixture_ids() or []) if str(x)]
            base["range_states"] = _scope_range_states(self._collect_range_states(),
                                                       _def_ids_for(self, live))
        return capture_editor_scope(self, base)

    def load_state(self, state: Optional[dict]) -> bool:
        """按 save_state 的产物还原面板（含条带形状）。"""
        return restore_editor_state(self, state)

    def dispatch_capture_state(self) -> dict:
        """给调度台捕获"可完整还原"的面板状态。

        Programmer **没有** save_state()（证据：全文件仅 CUE/静态快照设施），因此
        复用其既有静态快照设施 `snapshot_static_values()`（= CUE 的 values 结构）
        加设备组元数据 —— 不新增平行序列化。
        """
        try:
            # An explicit release detaches the editor from every device.  Keep
            # the visual group/control cache for the operator, but expose no
            # authored state to Dispatch/Clip capture until a new edit occurs.
            if bool(getattr(self, "_editor_state_detached", False)):
                return {
                    "static_values": {}, "group_ids": [],
                    "base_order_ids": [], "group_name": "", "rows": [],
                    "light_control_mode": "single", "range_states": {},
                }
            # ★★ 2026-09-19 用户实报修复（大组内多个子集的值只能存下最后一个）：
            #   捕获作用域 = **大组基准序 ∪ 当前子集**（见 `_dispatch_scope_ids`）。
            #   原实现只按当前 live 选择过滤 → 用户"选2个→做角度→从第3个→做角度"
            #   时第一批的值被整批丢掉。R87 防污染语义由"大组基准序"承接：
            #   已离开该大组的陈旧条目不在 base 里，仍被过滤。
            ids = _dispatch_scope_ids(self)
            # ★ 翼/块/翼方向是**每通道类型行**的状态（ChannelTypeRow）；
            #   单控/整体在 GroupSelector。全部取自面板既有属性，不新增第二套真源。
            ch = getattr(self, "_ch_list", None)
            gs = getattr(self, "_group_selector", None)
            rows_state = [_row_state(row) for row in (getattr(ch, "_rows", []) or [])]
            rows_state = [r for r in rows_state if r]
            single_btn = getattr(gs, "_light_mode_btn_single", None)
            return {
                "static_values": _scope_static_values(self.snapshot_static_values(), ids),
                "group_ids": ids,
                "base_order_ids": [str(x) for x in (getattr(self, "_current_base_order_ids", []) or [])],
                "group_name": str(getattr(self, "_current_group_name", "") or ""),
                "rows": rows_state,
                "light_control_mode": ("whole" if (single_btn is not None and
                                                   not single_btn.isChecked()) else "single"),
                # ★★ 2026-09-14 用户实报（数据丢失）：场景保存**漏掉了条带信息**。
                #   真源 `ChannelTypeRow._range_states` = {ch_type: {mode, base, target}}
                #   （条带起点/终点/单向双向）。此前只有**工程存档**路径 `save_state()`
                #   收集它，调度台场景路径完全没有 → 存进场景再调出来，条带被
                #   `restore_static_snapshot` 按均值强制压成 flat，监控区显示的是
                #   "平均值"而不是 `128-255 W4 B2 ↑`。这里补齐，两条路径共用
                #   `_collect_range_states()` 单一真源（不新增第二套序列化）。
                "range_states": _scope_range_states(self._collect_range_states(),
                                                    _def_ids_for(self, ids)),
            }
        except Exception:
            return {}

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None,
                              *, for_edit: bool = False) -> bool:
        """按调度台节点还原静态通道（先恢复设备作用域，再写回该格的值）。

        for_edit：★ 2026-09-18 用户定案（问题9 调用/编辑双语义）——
          · False（**调用/播放**，左键点场景卡）：只把值**下发到输出**，
            **不动条带控件、不还原翼/块、不切设备组**（用户："只播放效果"）；
          · True（**编辑/调入监控区**）：额外还原控件（设备组 + 条带 + 翼/块 + 单控整体）。
        """
        try:
            node = dict(node or {})
            state = dict(state or {})
            ch = getattr(self, "_ch_list", None)
            ids = [str(x) for x in (node.get("fixture_ids", []) or []) if str(x)]
            vals = dict(state.get("static_values", {}) or {})
            # ★ 2026-09-14 实报修正：可还原内容**不止静态值** —— 翼/块/翼方向（`rows`）
            #   与单控/整体（`light_control_mode`）同样要还原。原守卫
            #   `not ids and not vals → return False` 会在"只配了翼/块、没有静态值"
            #   的状态上**提前返回** → 工程加载路径（node 无 fixture_ids）翼/块永不
            #   还原（探针实测：快照 wings=1，还原后仍是 3）。守卫须覆盖全部可还原项。
            rows_state = list(state.get("rows", []) or [])
            mode_state = str(state.get("light_control_mode", "") or "")
            range_states = dict(state.get("range_states", {}) or {})
            # ★★ 2026-09-17 用户实报（存储区场景范围污染）：写回是第二道防线 ——
            #   旧档 `static_values` 里已混入过越界设备（捕获侧当时无过滤），必须在
            #   这里按存档作用域二次过滤，否则隐藏行的旧 `_fixtures` 会把它们写出去。
            scope = _recall_scope_ids(ids)
            if scope:      # 作用域未知（旧档无设备集）时不过滤，保持旧行为不误删
                vals = _scope_static_values(vals, scope)
            range_states = _scope_range_states(range_states, _def_ids_for(self, scope))
            if not ids and not vals and not rows_state and not mode_state:
                return False
            # 1) 设备作用域：**编辑语义**才切组（重建行/控件）。
            #    纯播放时**不切** —— 值仍按 scope 过滤后下发到输出，
            #    但用户眼前正在编辑的设备组/条带保持不变（问题9）。
            if ids and for_edit:
                try:
                    cur = {str(getattr(f, "id", "")): f
                           for f in (getattr(self, "_current_fixtures", []) or [])}
                    if all(i in cur for i in ids):
                        self.load_fixture_group(
                            str(node.get("group_key", "") or ""), ids,
                            str(state.get("group_name", "") or node.get("group_name", "") or ""),
                            [str(x) for x in (state.get("base_order_ids", []) or [])])
                except Exception:
                    log.debug("programmer: dispatch recall switch group failed", exc_info=True)
            # 2) 写回静态值并下发；控件级内容（翼/块/方向/单控整体 + 条带形状）
            #    只在**编辑语义**下还原 —— 纯播放不碰控件（问题9 定案）。
            #    ★ 2026-09-18 回访修正（纯播放污染编辑态）：`touch_shape` 才表达
            #    "不碰控件"——传 None 会被当成"旧快照无形状"而走均值 flat 兜底。
            if for_edit:
                _restore_rows_and_mode(self, state)
            applied = _apply_recall_values(self, vals,
                                           (range_states if for_edit else None),
                                           scope or None, touch_shape=bool(for_edit))
            # ★ 2026-09-14 用户定案：这次还原是场景调用带活的、不是我正在编辑的 →
            #   置标记，保存新场景时排除它（"只要不是我正在编辑的就不存"）。
            #   **必须在 `_apply_recall_values` 之后落**：还原本身会触发 batch 信号，
            #   越早置越会被那条路径清掉（深查实测：置在前面等于没置）。
            _finish_programmer_recall(self, vals, for_edit)
            # 成功判据覆盖全部还原项：静态值写好、或翼/块/方向还原过（`rows_state`）
            return bool(applied or vals or rows_state)
        except Exception:
            log.debug("programmer: dispatch_recall_state failed", exc_info=True)
            return False

    def _send_cue_to_timeline(self, cue: CueData):
        dlg = _SendToTimelineDialog(self._pm, cue, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        params = dlg.get_params()
        track_id = params["track_id"]
        # 修复：旧代码误把 duration 当作 start，导致用户输入的起始时间完全无效。
        start = params["start"]
        duration = params["duration"]
        fade_in = params["fade_in"]

        if not track_id:
            QMessageBox.warning(self, "发送到时间线", "请先在时间线上创建轨道。")
            return

        self._generate_clip(cue, track_id, start, duration, fade_in)

    def _project_data(self):
        """返回实际 Project 对象，兼容 ProjectManager / Project 两种传入方式。"""
        return getattr(self._pm, "project", self._pm)

    def _find_track_for_timeline(self, track_id: str):
        proj = self._project_data()
        if proj is None:
            return None
        if hasattr(proj, "get_track"):
            try:
                return proj.get_track(track_id)
            except Exception:
                pass
        for t in getattr(proj, "tracks", []) or []:
            if getattr(t, "id", None) == track_id:
                return t
        return None

    def _add_clip_to_timeline(self, track_id: str, clip) -> bool:
        """
        将 clip 加入时间线，并返回是否真实加入成功。
        ProjectManager.add_clip 在部分工程状态下可能因快照/信号链异常抛错，
        旧逻辑只记录错误，最终提示“已生成 0 个 Clip”。这里保留首选路径，
        失败时回退到直接写入目标轨道并主动刷新时间线。
        """
        try:
            if hasattr(self._pm, "add_clip"):
                before_track = self._find_track_for_timeline(track_id)
                before_count = len(getattr(before_track, "clips", []) or []) if before_track is not None else -1
                self._pm.add_clip(track_id, clip)
                after_track = self._find_track_for_timeline(track_id)
                after_count = len(getattr(after_track, "clips", []) or []) if after_track is not None else -1
                if after_count > before_count or any(getattr(c, "id", None) == clip.id for c in getattr(after_track, "clips", []) or []):
                    return True
                log.warning("ProjectManager.add_clip 未将 clip 写入轨道，准备回退添加: track=%s", track_id)
        except Exception as e:
            log.error("ProjectManager.add_clip 失败，准备回退添加: %s", e)
            maybe_track = self._find_track_for_timeline(track_id)
            if any(getattr(c, "id", None) == clip.id for c in getattr(maybe_track, "clips", []) or []):
                return True

        track = self._find_track_for_timeline(track_id)
        if track is None:
            log.error("发送 Cue 到时间线失败：找不到目标轨道 %s", track_id)
            return False
        try:
            if hasattr(track, "add_clip"):
                track.add_clip(clip)
            else:
                clip.track_id = track_id
                track.clips.append(clip)
                track.clips.sort(key=lambda c: getattr(c, "start_time", 0.0))
        except Exception as e:
            log.error("回退添加 clip 到轨道失败: %s", e)
            return False

        try:
            if hasattr(self._pm, "clip_added"):
                self._pm.clip_added.emit(track_id, clip.id)
            if hasattr(self._pm, "project_changed"):
                self._pm.project_changed.emit()
            if hasattr(self._pm, "_mark_dirty"):
                self._pm._mark_dirty()
        except Exception:
            pass
        try:
            ctrl = self._timeline_ctrl_ref
            if ctrl is not None and hasattr(ctrl, "tracks_changed"):
                ctrl.tracks_changed.emit()
        except Exception:
            pass
        return True

    def _expand_motor_pair_channels_for_universe(self, universe: int, channels) -> set:
        """Return 1-based DMX channels, adding paired coarse/fine motor addresses."""
        expanded = set()
        for ch in channels or []:
            try:
                ch_i = int(ch)
            except Exception:
                continue
            if 1 <= ch_i <= 512:
                expanded.add(ch_i)
        if not expanded:
            return expanded

        try:
            proj = self._project_data()
            fdefs = {
                str(getattr(fd, "id", "")): fd
                for fd in (getattr(proj, "fixture_defs", []) or [])
                if getattr(fd, "id", None)
            }
            for fi in (getattr(proj, "fixtures", []) or []):
                if int(getattr(fi, "universe", 1) or 1) != int(universe):
                    continue
                fd = fdefs.get(str(getattr(fi, "def_id", "")))
                by_type: Dict[str, List[int]] = {}
                start_ch = int(getattr(fi, "start_channel", 1) or 1)
                for offset, ch_type in enumerate(getattr(fd, "channels", []) or []):
                    dmx_ch = start_ch + int(offset)
                    if 1 <= dmx_ch <= 512:
                        by_type.setdefault(str(ch_type), []).append(dmx_ch)
                for coarse_type, fine_type in MOTOR_FINE_CHANNELS.items():
                    pair = list(by_type.get(coarse_type, []) or []) + list(by_type.get(fine_type, []) or [])
                    if pair and any(ch in expanded for ch in pair):
                        expanded.update(pair)
        except Exception:
            pass
        return expanded

    def _sample_transition_anchor_frame(self, universe: int, start: float) -> np.ndarray:
        """采样新动作的过渡起点。

        连续编曲时，Cue/效果发送到时间线的淡入不应从 0 开始；对电机类通道，
        0 往往代表物理极限，会造成“先甩到最右/最左，再回到目标角度”的跳变。
        因此这里优先读取当前 timeline 层（用户拖针预听后的上一动作输出），
        若没有实时 timeline 层，则回退到工程中 start 前一瞬间的已有 clip 输出。
        返回 int16 数组，-1 表示该通道没有可用锚点。
        """
        universe = int(universe)
        anchor = np.full(512, -1, dtype=np.int16)

        # 1) 连续编曲最可靠的起点是“当前最终混合输出”：
        # timeline 预听 + Programmer/Matrix/Effect/Joystick/Audio 等编辑页叠加后的真实现场值。
        # 对电机来说，0 也可能是有效左侧位置，所以这里把 512 通道全部作为可用锚点。
        nm = getattr(self, "_nm_ref", None)
        try:
            if nm is not None and hasattr(nm, "compute_editor_universe_now"):
                current = nm.compute_editor_universe_now(universe, apply_output_smoothing=False)
            elif nm is not None and hasattr(nm, "compute_universe_now"):
                current = nm.compute_universe_now(universe, apply_output_smoothing=False, exclude_layers=["clip_playback", "clip_preview", "timeline"])
            elif nm is not None and hasattr(nm, "get_universe_data"):
                current = nm.get_universe_data(universe)
            else:
                current = None
            if current is not None:
                data = np.asarray(current, dtype=np.uint8).reshape(-1)
                n = min(512, data.size)
                if n > 0:
                    anchor[:n] = data[:n].astype(np.int16)
        except Exception:
            pass

        # 2) 若当前输出不可用，再读取拖针预听后 PlaybackEngine 写入的 timeline 层。
        try:
            if not np.any(anchor >= 0):
                layers = getattr(nm, "_layers", {}) if nm is not None else {}
                entry = layers.get("timeline", {}).get(universe)
                if entry is not None:
                    data, active = entry
                    data = np.asarray(data, dtype=np.uint8).reshape(-1)
                    active = np.asarray(active, dtype=bool).reshape(-1)
                    n = min(512, data.size, active.size)
                    if n > 0 and np.any(active[:n]):
                        idxs = np.flatnonzero(active[:n])
                        anchor[idxs] = data[idxs].astype(np.int16)
        except Exception:
            pass

        # 3) 回退：直接从工程时间线中采样 start 前一瞬间，以支持未拖针但按顺序追加的编曲。
        sample_t = max(0.0, float(start) - 1e-4)
        proj = self._project_data()
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        if not tracks:
            return anchor
        try:
            # 2026-08-13 R13: 祖先链静音/单播判定(与 playback_engine 同构) ——
            # 原仅直接属性, 文件夹静音/单播漏掉子轨道(用户报「排除不彻底」)。
            track_by_id = {
                str(getattr(t, "id", "") or ""): t for t in tracks if getattr(t, "id", None)
            }

            def _anc_muted(trk):
                if bool(getattr(trk, "muted", False)):
                    return True
                seen = set()
                pid = getattr(trk, "parent_id", "") or ""
                while pid and pid not in seen:
                    seen.add(pid)
                    parent = track_by_id.get(str(pid))
                    if parent is None:
                        break
                    if bool(getattr(parent, "muted", False)):
                        return True
                    pid = getattr(parent, "parent_id", "") or ""
                return False

            def _anc_soloed(trk):
                if bool(getattr(trk, "solo", False)):
                    return True
                seen = set()
                pid = getattr(trk, "parent_id", "") or ""
                while pid and pid not in seen:
                    seen.add(pid)
                    parent = track_by_id.get(str(pid))
                    if parent is None:
                        break
                    if bool(getattr(parent, "solo", False)):
                        return True
                    pid = getattr(parent, "parent_id", "") or ""
                return False

            any_solo = any(
                _anc_soloed(t) for t in tracks
                if not bool(getattr(t, "is_folder", False)) and not _anc_muted(t)
            )
            for track in tracks:
                if bool(getattr(track, "is_folder", False)):
                    continue
                if _anc_muted(track):
                    continue
                if any_solo and not _anc_soloed(track):
                    continue
                track_universes = set(int(u) for u in (getattr(track, "universes", None) or [getattr(track, "universe", 1)]))
                if universe not in track_universes:
                    continue

                rule = None
                if hasattr(track, "get_filter_for_universe"):
                    rule = track.get_filter_for_universe(universe)
                elif hasattr(track, "universe_filters"):
                    rule = (getattr(track, "universe_filters", {}) or {}).get(universe)
                if rule is None:
                    rule = getattr(track, "filter_rule", None)

                for clip in sorted(getattr(track, "clips", []) or [], key=lambda c: float(getattr(c, "start_time", 0.0))):
                    c_start = float(getattr(clip, "start_time", 0.0) or 0.0)
                    c_dur = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
                    if c_dur <= 0.0:
                        continue
                    if not (c_start <= sample_t < c_start + c_dur or (bool(getattr(clip, "hold_last_frame", False)) and sample_t >= c_start + c_dur)):
                        continue
                    clip_unis = set(int(u) for u in (getattr(clip, "clip_universes", None) or track_universes))
                    if universe not in clip_unis:
                        continue
                    local_t = sample_t - c_start
                    if bool(getattr(clip, "hold_last_frame", False)) and local_t >= c_dur:
                        local_t = max(0.0, c_dur - 1e-4)
                    frames = [f for f in (getattr(clip, "frames", []) or []) if int(getattr(f, "universe", universe)) == universe]
                    if not frames:
                        continue
                    frames.sort(key=lambda f: float(getattr(f, "timestamp", 0.0)))
                    chosen = frames[0]
                    for f in frames:
                        if float(getattr(f, "timestamp", 0.0)) <= local_t + 1e-6:
                            chosen = f
                        else:
                            break
                    raw = np.asarray(getattr(chosen, "data", np.zeros(512, dtype=np.uint8)), dtype=np.uint8).reshape(-1)
                    n = min(512, raw.size)
                    if n <= 0:
                        continue
                    if rule is not None and hasattr(rule, "is_empty") and not rule.is_empty():
                        # 有轨道过滤时，过滤内通道即使值为 0 也必须作为有效锚点。
                        for ch in self._expand_motor_pair_channels_for_universe(universe, getattr(rule, "channels", []) or []):
                            idx = int(ch) - 1
                            if 0 <= idx < n:
                                anchor[idx] = int(raw[idx])
                    else:
                        # 无过滤规则时不能只用 raw>0 判断 active；电机左侧/下限经常就是 0，
                        # 若把 0 当“无锚点”，下一动作会从默认/极值起跳，造成先甩到最右再回目标。
                        # 因此对当前工程已配接设备在该 universe 的所有真实通道采样上一帧值。
                        try:
                            fdefs = {str(getattr(fd, "id", "")): fd for fd in (getattr(proj, "fixture_defs", []) or [])}
                            for fi in (getattr(proj, "fixtures", []) or []):
                                if int(getattr(fi, "universe", 1) or 1) != universe:
                                    continue
                                fd = fdefs.get(str(getattr(fi, "def_id", "")))
                                ch_count = len(getattr(fd, "channels", []) or []) if fd is not None else int(getattr(fi, "channel_count", 1) or 1)
                                start_idx = int(getattr(fi, "start_channel", 1) or 1) - 1
                                for off in range(max(0, ch_count)):
                                    idx = start_idx + off
                                    if 0 <= idx < n:
                                        anchor[idx] = int(raw[idx])
                        except Exception:
                            active = raw[:n] > 0
                            if np.any(active):
                                idxs = np.flatnonzero(active)
                                anchor[idxs] = raw[idxs].astype(np.int16)
        except Exception:
            pass
        return anchor

    def _generate_clip(self, cue: CueData, track_id: str,
                       start: float, duration: float, fade_in: float,
                       _silent: bool = False):
        from ..core.models import Clip
        fps = 40.0
        n_frames = max(1, int(duration * fps))
        fade_frames = max(0, int(fade_in * fps))
        fixtures = self._current_fixtures_selected or self._current_fixtures
        valid_fixture_ids = {str(f.id) for f in fixtures}
        base_order_ids = [
            str(fid)
            for fid in list(self._current_base_order_ids or [f.id for f in fixtures])
            if str(fid) in valid_fixture_ids
        ]
        if not base_order_ids:
            base_order_ids = [str(f.id) for f in fixtures]
        try:
            cue_base_order = list((cue.effects or {}).get("_base_order_fixture_ids", []) or [])
        except Exception:
            cue_base_order = []
        if cue_base_order:
            merged_order = [str(fid) for fid in cue_base_order if str(fid) in valid_fixture_ids]
            present = set(merged_order)
            for fid in base_order_ids:
                sfid = str(fid)
                if sfid in valid_fixture_ids and sfid not in present:
                    merged_order.append(sfid)
                    present.add(sfid)
            if merged_order:
                base_order_ids = merged_order
        from .programmer_clip_frames import render_cue_frames
        universe_frames, active_channels_by_universe, ownership = render_cue_frames(
            self, cue, fixtures,
            {"fps": fps, "count": n_frames, "fade_count": fade_frames, "start": start},
            _fixture_has_light_channels)

        if not universe_frames:
            if not _silent:
                QMessageBox.warning(self, "发送到时间线", "Cue 中没有有效的设备数据，请检查设备配接。")
            return

        try:
            self._pm.project_changed.disconnect(self._refresh_fixtures)
        except Exception:
            pass
        clip_count = 0
        for univ, frames in universe_frames.items():
            clip = Clip(
                track_id=track_id,
                start_time=start,
                duration=duration,
                playback_rate=1.0,
                source_duration=duration,
                frames=frames,
                label=f"{cue.name} (U{univ})",
                clip_universes=[univ],
                active_channels_by_universe={int(univ): sorted(active_channels_by_universe.get(int(univ), set()))},
                preset_name=cue.name,
            )
            clip.recorded_channel_masks = {int(univ): ownership[int(univ)]}
            clip.base_order_fixture_ids = [str(fid) for fid in (base_order_ids or [])]
            if self._add_clip_to_timeline(track_id, clip):
                clip_count += 1
                log.info("Cue '%s' → 时间线 轨道=%s U%d", cue.name, track_id, univ)
        try:
            self._pm.project_changed.connect(self._refresh_fixtures)
        except Exception:
            pass
        if not _silent:
            if clip_count > 0:
                QMessageBox.information(
                    self, "发送成功",
                    f"Cue '{cue.name}' 已生成 {clip_count} 个 Clip 到时间线。")
                _release_after_save(self)   # ★ R130：保存到时间线 = 保存语义 → 释放输出
            else:
                QMessageBox.warning(
                    self, "发送到时间线失败",
                    f"Cue '{cue.name}' 未能写入任何 Clip，请检查目标轨道是否仍然存在。")

    def _on_matrix_pattern_updated(self, dmx_data: dict):
        if not dmx_data:
            return
        self._feed_matrix_to_timeline(dmx_data)

    def _feed_matrix_to_timeline(self, dmx_data: dict):
        # MatrixSlideshowPanel writes the NM "matrix" layer directly. Mirroring
        # it back into Programmer fader/base state keeps stale colors alive and
        # doubles the realtime work for large LED matrices.
        ctrl = self._timeline_ctrl_ref
        if ctrl is not None and getattr(ctrl, "_recording", False):
            self._feed_recording_frames(layer_names=("matrix",))


def _restore_rows_and_mode(panel, state: dict) -> None:
    """还原各行的**翼/块/翼方向**与**单控/整体**按钮（`dispatch_recall_state` 第 2 步）。

    ★ 为什么抽到模块级：ProgrammerPanel 是上帝类（禁止新增方法，CLAUDE.md 10.1），
      且原内联块让 `dispatch_recall_state` 超门禁行数/CC 上限。
    ★ 用 spin 的 `setValue` 走 `valueChanged` 正常通路 → 条带分段/按钮态可见刷新。
    """
    try:
        _apply_saved_rows(panel, state.get("rows", []) or [])
        mode = str(state.get("light_control_mode", "") or "")
        gs = getattr(panel, "_group_selector", None)
        if mode and gs is not None and hasattr(gs, "set_light_control_mode"):
            gs.set_light_control_mode(mode)   # 只同步按钮，不发全局信号
    except Exception:
        log.debug("programmer: dispatch recall wing/block restore failed", exc_info=True)


def _apply_saved_rows(panel, saved_rows) -> None:
    """按 (def_id, ch_types) 匹配行，回填翼/块/翼方向。"""
    ch = getattr(panel, "_ch_list", None)
    index = _rows_index(saved_rows)
    for row in (getattr(ch, "_rows", []) or []):
        match = index.get(_row_identity(row))
        if match is not None:
            _apply_row_wings(row, match)


def _rows_index(saved_rows) -> dict:
    """快照行 → {(def_id, ch_types): 该行快照}。"""
    return {_saved_row_identity(sr): sr for sr in (saved_rows or [])}


def _row_identity(row) -> tuple:
    """实时行的身份键（与快照同规则）。"""
    return (str(getattr(row, "_def_id", "") or ""),
            tuple(str(t) for t in (getattr(row, "_ch_types", []) or [])))


def _saved_row_identity(sr) -> tuple:
    """快照行的身份键。"""
    return (str(sr.get("def_id", "") or ""),
            tuple(str(t) for t in (sr.get("ch_types", []) or [])))


def _apply_row_wings(row, match) -> None:
    """静默回填翼/块元数据；显式静态值另走原 sparse 作者恢复通路。"""
    was_refreshing = row._is_refreshing
    row._is_refreshing = True
    try:
        _wing_flags.restore(row, match, "_value_")
        row._header_wing_spin.setValue(max(1, int(match.get("wings", 1) or 1)))
        row._header_block_spin.setValue(max(1, int(match.get("block_size", 1) or 1)))
        wd = str(match.get("wing_direction", "") or "")
        if wd and hasattr(row, "_set_wing_direction"):
            row._set_wing_direction(wd)
    except Exception:
        log.debug("programmer: recall row wing restore failed", exc_info=True)
    finally:
        row._is_refreshing = was_refreshing


def _mark_pm_dirty(panel) -> None:
    """把"用户真的编辑了"上报为项目脏（→ 主窗 `closeEvent` 会提示保存）。

    ★ 深查修正：programmer/effect/matrix/topview 的主编辑路径此前**一个脏标记都没置**，
      用户改完直接关窗口既无提示、改动也丢。`_mark_dirty` 自身幂等（已脏则直接返回），
      所以在高频拖动路径上重复调用无额外开销。
    """
    try:
        pm = getattr(panel, "_pm", None)
        if pm is not None and hasattr(pm, "_mark_dirty"):
            pm._mark_dirty()
    except Exception:
        pass


#: 频谱 UI 刷新间隔（秒）——行级与面板级共用同一常量。
#  ★ 2026-09-17：原为两处各写，且行级用 getattr(self, ...) 读**面板**属性
#    （ChannelTypeRow 无此属性 → 恒取默认值）。收成常量后语义单一，
#    面板要调间隔只需改这里（不会出现"面板改了、行内静默用旧值"）。
SPECTRUM_UI_FRAME_INTERVAL: float = 1.0 / 12.0


def _emit_traj_overrides_to(signal, prefix: str, overrides: dict) -> None:
    """把 {param_key: value} 展开成逐条轨迹事件发到给定信号。

    ★ 单点实现：ChannelTypeRow 与 ChannelTypeList **都要**转发幅度轨迹
      （行对象有 `_emit_traj_overrides`，列表对象没有——历史误用根源），
      两处共用本函数，避免再次各写一份。
    """
    try:
        for k, v in dict(overrides or {}).items():
            signal.emit(f"{prefix}|{k}", v)
    except Exception:
        pass


def _panel_of(row):
    """从行对象取所属 `ProgrammerPanel`（`_owner_list` 是 ChannelTypeRow 列表）。

    ChannelTypeRow 自身**没有** `_nm_ref`/`_addr_cache`/`_layer_sparse_cache`
    （这些只在面板上）——任何行内需要碰这些状态的地方都必须经本函数回到面板，
    否则 `getattr(self, "_nm_ref", None)` 恒为 None、整段逻辑静默变成空操作。
    """
    owner_list = getattr(row, "_owner_list", None)
    if owner_list is None:
        return None
    p = getattr(owner_list, "_panel", None)
    if p is not None:
        return p
    # 兜底：列表直接挂在面板上时，`_owner_list` 即面板本身
    return owner_list if hasattr(owner_list, "_nm_ref") else None


def _stop_live_generators(panel) -> None:
    """停止会**每帧重写层**的实时生成器（轨迹回放 / 音频驱动）。

    ★★ 2026-09-17 深查修正：`clear_live_output` 只停面板级输出定时器，但轨迹
      (`_traj_play_timer` → `_traj_emit_output`) 与音频（驱动帧 → `_on_audio_value`
      → `fixture_layer_batch_changed`）是**独立的写入者**：释放后 33~100ms 它们
      重新调 `_schedule_live_output` → 重启定时器 → 把层写回 NM。
      实测表现为"点了 release / 关了场景，灯又亮起来"。
    """
    try:
        panel._traj_stop_internal()
    except Exception:
        pass
    for row in _panel_rows(panel):
        _runtime_targets.stop_audio(row)


def _rearm_output_after_edit(panel) -> None:
    """用户真实编辑控件后，解除"已释放"标记并恢复输出定时器。

    ★★ 2026-09-17 深查修正（标志卡死）：`_programmer_output_released` 旧实现只在
      释放时置 True、仅在 CUE 调入/还原时置 False。用户手动拖推杆会重新出光，
      但标记仍是 True → `effect_browser` 的输出作用域判定认为"已释放"→ 返回空
      作用域 → 效果输出退化为**全项目设备**（用户看到"作用域突然变大/不认人"）。
      语义：编辑即重新接管，标记与定时器一起复位。
    """
    try:
        panel._programmer_output_released = False
    except Exception:
        pass
    try:
        # First real edit after an explicit release reattaches only the newly
        # authored row values; the old visual cache remains display-only.
        if not getattr(panel, "_cue_recall_in_progress", False):
            panel._editor_state_detached = False
    except Exception:
        pass
    try:
        _ka = getattr(panel, "_keepalive_timer", None)
        if _ka is not None and not _ka.isActive():
            _ka.start()
    except Exception:
        pass


def _programmer_bus_snapshot(panel) -> dict:
    """Playback stays visible on the bus without becoming authored save data."""
    if getattr(panel, "_recalled_play", False):
        return getattr(panel, "_recalled_output_values", {}) or {}
    return panel.snapshot_static_values() or {}


def _forget_panel_cell_cache(row, fixture_id: str, ch_type: str) -> None:
    """把 (设备, 通道) 对应地址从**面板级** sparse/dirty 缓存中删除并递增版本。

    ★★ 2026-09-17 深查修正：`ChannelTypeRow.clear_cell_store` 只清行内缓存，
      而 `_send_keepalive`/`_flush_live_output` 重发的是面板级
      `_layer_sparse_cache`（`{layer: {universe: {addr: value}}}`，**地址键**）。
      不清面板缓存 → 调度台"清空所选格"后，任意一次编辑递增版本，keepalive 就把
      旧值+active 掩码重新写回（实测 100ms 内复活），正是"释放不生效"的根因。
    """
    panel = _panel_of(row)
    if panel is None:
        return
    addrs = _cell_cache_addrs(panel, fixture_id, ch_type)
    if not addrs:
        return
    for layer in ("fader", "joystick", "audio", "matrix"):
        _drop_addrs_from_layer_cache(panel, layer, addrs)
    try:
        panel._layer_sparse_version += 1
    except Exception:
        pass


def _addrs_by_universe(panel, ch_type: str) -> dict:
    """该 ch_type 在面板 `_addr_cache` 中的全部地址 → {universe: {addr}}。"""
    by_uni: dict = {}
    try:
        for _fid, fid_cache in (getattr(panel, "_addr_cache", {}) or {}).items():
            for uni, addr in (fid_cache.get(ch_type) or ()):
                if 0 <= int(addr) < 512:
                    by_uni.setdefault(int(uni), set()).add(int(addr))
    except Exception:
        pass
    return by_uni


def _drop_layer_cell(row, fid: str, ct: str) -> bool:
    """从四层里删该格（空了连 fid 一起删）。返回是否有变化。"""
    changed = False
    for layer_name in row._layers:
        per_fid = row._layers.get(layer_name)
        if not per_fid or fid not in per_fid:
            continue
        if per_fid[fid].pop(ct, None) is not None:
            changed = True
        if not per_fid[fid]:
            per_fid.pop(fid, None)
    return changed


def _drop_row_cell_caches(row, fid: str, ct: str) -> bool:
    """清行内缓存：四层 `_layers` + `_values` + `_marked` + 外部残留（返回是否有变化）。"""
    changed = _drop_layer_cell(row, fid, ct)
    for store in (row._values, row._marked):
        if _drop_one_store(store, fid, ct):
            changed = True
    return _drop_external_cell(row, fid, ct) or changed


def _drop_one_store(store, fid: str, ct: str) -> bool:
    """从 {fid: {ch_type: v}} 结构删除该格（空了连 fid 一起删）。返回是否变化。"""
    d = store.get(fid)
    if not d or ct not in d:
        return False
    d.pop(ct, None)
    if not d:
        store.pop(fid, None)
    return True


def _cell_cache_addrs(panel, fixture_id: str, ch_type: str):
    """取 (设备, 通道) 对应的 [(universe, addr)]（dimmer 走虚拟亮度同名键）。"""
    fid_cache = (getattr(panel, "_addr_cache", {}) or {}).get(str(fixture_id))
    if not fid_cache:
        return []
    addrs = fid_cache.get(ch_type)
    if not addrs and _norm_channel_type(ch_type) == "dimmer":
        addrs = fid_cache.get("dimmer")
    return list(addrs or ())


def _drop_addrs_from_layer_cache(panel, layer: str, addrs) -> None:
    """把给定地址从该层的完整缓存与 dirty 缓存中删除（键=universe，值键=addr）。"""
    try:
        for layer_store in (_layer_maps(panel, "_layer_sparse_cache", layer),
                            _layer_maps(panel, "_dirty_layer_sparse", layer)):
            for uni, addr in addrs:
                row = layer_store.get(int(uni))
                if isinstance(row, dict):
                    row.pop(int(addr), None)
    except Exception:
        pass


def _layer_maps(panel, attr: str, layer: str):
    """取 {layer: {universe: {addr: v}}} 中某一层的映射（缺则空 dict）。"""
    store = (getattr(panel, attr, {}) or {}).get(layer)
    return store if isinstance(store, dict) else {}


def _drain_row_timers(panel) -> None:
    """停掉并清空**每行**的节流单发定时器与其挂起载荷。

    ★ 深查修正（stuck output）：释放只停了面板级定时器，行级 33ms 单发定时器仍可能
      在释放后才到点 → 经 `fixture_layer_batch_changed` 重启输出定时器并把层写回
      （实测释放后 120ms fader 层被重新写入）。释放时一并排空，杜绝"关不掉"。

    ★★ 2026-09-17 用户实报崩溃修正：排空必须按字段类型给**空的同类值** ——
      `_pending_strip` 是可空元组（`_process_pending_strip` 有 `is None` 守卫），
      但 `_pending_master` 是 **dict**，`_on_master_changed` 直接
      `self._pending_master[(ct, layer)] = …`。旧实现把两者一律置 None →
      释放后第一次推摇杆/推杆即 `TypeError: 'NoneType' object does not support
      item assignment`（实测栈：_on_ctrl_joy → _on_master_changed:3136）。
    """
    for row in _panel_rows(panel):
        for tname in ("_strip_timer", "_master_timer"):
            _quiet_stop(getattr(row, tname, None))
        for pname, empty in (("_pending_strip", None), ("_pending_master", {})):
            try:
                if hasattr(row, pname):
                    setattr(row, pname, empty)
            except Exception:
                pass


def _panel_rows(panel):
    """面板当前的全部行（取不到给空列表）。"""
    try:
        return list(getattr(getattr(panel, "_ch_list", None), "_rows", None) or [])
    except Exception:
        return []


def _quiet_stop(timer) -> None:
    """停掉一个（可能为 None / 已销毁的）QTimer，异常不外抛。"""
    try:
        if timer is not None and timer.isActive():
            timer.stop()
    except Exception:
        pass


#: 编程器**有权**清的层。★ 2026-09-14 深查修正（违反"绝不碰 clip 层"红线）：
#: 原列表含 `clip_playback`/`clip_preview`/`timeline`（时间线**播放**层）与
#: `matrix`/`matrix_audio`（矩阵面板的层）—— 释放一个 scene 时清它们，会把正在播放的
#: 时间线一起打黑、并越权清别的面板。与 `cell_clear.CLEAR_LAYERS` 刻意排除 clip 层的
#: 既有纪律对齐。
_PROG_CLEAR_LAYERS = ("fader", "programmer", "tester", "audio", "joystick")


def _clear_programmer_layers(panel) -> None:
    """清编程器自己的层并立即 flush（不动 clip / matrix 等他面板层）。"""
    nm = getattr(panel, "_nm_ref", None)
    if nm is None or not hasattr(nm, "clear_layer"):
        return
    for layer_name in _PROG_CLEAR_LAYERS:
        try:
            nm.clear_layer(layer_name)
        except Exception:
            pass
    if hasattr(nm, "flush_universes_now"):
        try:
            # ★ R194/AUD-05：worker 启用时置脏+唤醒（毫秒级），不再 GUI 持锁
            #   全域混音+发送；worker 关闭时方法内部回退同步 flush。
            if hasattr(nm, "request_universe_refresh"):
                nm.request_universe_refresh()
            else:
                nm.flush_universes_now()
        except Exception:
            pass


def _detach_programmer_editor_state(panel) -> None:
    """Detach all row ownership without resetting visible controls.

    ``_values`` is also used by the visible controls and is intentionally
    retained.  The authored markers and source layers are the serialization
    boundary; clearing them prevents a later Clip/Dispatch capture from
    reviving an effect that the operator explicitly released.
    """
    if panel is None:
        return
    try:
        rows = list(getattr(getattr(panel, "_ch_list", None), "_rows", None) or [])
    except Exception:
        rows = []
    for row in rows:
        _detach_programmer_row(row)
    try:
        panel._editor_state_detached = True
        panel._editor_release_seen = True
        panel._recalled_play = False
        panel._recalled_output_values = {}
        panel._active_cue_buffer = {}
    except Exception:
        pass


def _detach_programmer_row(row) -> None:
    """Detach one row while retaining its visible authored marks."""
    try:
        _remember_detached_cells(row)
        for layer in (getattr(row, "_layers", {}) or {}).values():
            if isinstance(layer, dict):
                layer.clear()
        _clear_row_pending_state(row)
    except Exception:
        pass


def _remember_detached_cells(row) -> None:
    detached = getattr(row, "_detached_cells", None)
    if detached is None:
        detached = row._detached_cells = set()
    for source in (getattr(row, "_marked", {}) or {},
                   *(getattr(row, "_layers", {}) or {}).values()):
        if isinstance(source, dict):
            for fid, channels in source.items():
                detached.update((str(fid), str(ct)) for ct in (channels or {}))


def _clear_row_pending_state(row) -> None:
    external = getattr(row, "_external_cells", None)
    if hasattr(external, "clear"):
        external.clear()
    pending = getattr(row, "_pending_master", None)
    if isinstance(pending, dict):
        pending.clear()
    if hasattr(row, "_pending_strip"):
        row._pending_strip = None


def _release_after_save(panel) -> None:
    """**子页面自己的保存**（Programmer → 时间线生成 Clip）完成后释放输出。

    ★★ R130 用户定案（"各个子页面的自己的保存效果的功能…都要按照各自的语义正确的
      释放掉"）：保存 = 效果已入槽位，现场回到"无效果"起点，与调度台"存场景后自动
      清台"同一语义。（R182 起静态 CUE 存储前端已删，仅 `_generate_clip` 的
      发送到时间线路径调用本函数。）三件事缺一不可：
        ① `clear_live_output()` 停 live/keepalive 定时器 + 清层（否则下一拍灌回）；
        ② 让总线上的本面板窗格消失（`clear_live_output` 内部的 `report_to_bus` 见
           released 标志 → 空 → 差集删除），否则下次存场景会把已入库的效果一起抄走；
        ③ 保留 UI 值（`clear_live_output` 本就不清 `_values/_marked`），用户接着改
           即可重新接管（`_rearm_output_after_edit` 复位标志）。
    """
    try:
        if panel is not None and hasattr(panel, "clear_live_output"):
            panel.clear_live_output()
    except Exception:
        log.debug("programmer: clear_live_output after save failed", exc_info=True)


def _programmer_dispatch_release_state(panel, node: dict, state=None) -> bool:
    """**释放**某调度台 Programmer 格（关闭场景 / 取消激活时调用）。

    ★ 2026-09-14：关闭场景原实现只清 NetworkManager 层，但 Programmer 的
      **keepalive/live 输出定时器仍在跑** → 清掉的层会被下一拍点名重写，
      表现"消不掉、面板也没同步"。真释放必须停掉本面板的输出定时器。

    复用面板既有 `clear_live_output()`（停 live/keepalive 定时器 + 清
    fader/programmer 等层 + flush），不新增第二套停止逻辑。
    """
    try:
        released = not bool(getattr(panel, "_programmer_output_released", False))
        panel.clear_live_output()
        panel._programmer_output_released = True
        panel.report_to_bus(force=True)     # 立即反映到调度台
        return released
    except Exception:
        log.debug("programmer: dispatch_release_state failed", exc_info=True)
        return False


ProgrammerPanel.dispatch_release_state = _programmer_dispatch_release_state
# ★ 声明理解 `for_edit` 语义位（dispatch_flow 据此传参）
ProgrammerPanel.dispatch_recall_state.__accepts_for_edit__ = True
