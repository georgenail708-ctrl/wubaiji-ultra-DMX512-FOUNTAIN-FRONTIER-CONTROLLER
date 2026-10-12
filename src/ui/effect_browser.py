"""
effect_browser.py — DMXTimeline 效果预设浏览器面板
===================================================
提供 EffectBrowserWidget：
  ┌─────────────────────────────────────────────────────────────────────┐
  │  [搜索框]  [分类筛选下拉]  [通道过滤]                              │
  ├───────────────────────────┬─────────────────────────────────────────┤
  │  预设列表（带分类标题）    │  右侧：参数调整区                       │
  │  - 图标 + 名称 + 描述     │  ┌ 预设名称 + 描述                     │
  │  - 点击选中               │  ├ 参数控件（自动生成）                 │
  │                           │  ├ TopView 排序模式                     │
  │                           │  ├ 应用方向 / TopView 排序               │
  │                           │  ├ 时长 / FPS                           │
  │                           │  └ [预览] [Apply to Programmer] [→时间线]│
  └───────────────────────────┴─────────────────────────────────────────┘

信号：
  effect_applied(preset_id, params, ch_types, sort_mode)
  send_to_timeline(preset_id, params, ch_types, sort_mode, duration, fps)
"""

from __future__ import annotations

from copy import deepcopy

import logging
import math
import os
import random
from typing import Tuple, Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# ★ R38-D8 可观测性：_tick_play 分段计时开关（默认关，零开销；模块级读一次，
#   不在每帧读 env）。QASCADE_EFFECT_TICK_PROF=1 时每 40 帧打印一次中位帧时。
_EFFECT_TICK_PROF = os.environ.get("QASCADE_EFFECT_TICK_PROF", "") == "1"
# 帧异常日志节流：前 N 次打完整 traceback，之后每 M 次汇报一次，避免 40fps
#   下每帧打 traceback 反而成为新的性能问题（R33 教训是"看不见"，不是"打太少"）。
_TICK_ERR_VERBOSE_LIMIT = 3
_TICK_ERR_SAMPLE_EVERY = 200

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter,
    QPushButton, QLabel, QLineEdit, QScrollArea, QFrame,
    QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox,
    QGroupBox, QSizePolicy, QSlider, QToolButton, QListWidget,
    QListWidgetItem, QStackedWidget, QMainWindow, QDockWidget,
    QInputDialog, QMessageBox, QMenu,
    QListView, QAbstractItemView, QStyledItemDelegate, QStyle,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRect, QRectF, QPointF, QSize, QPoint, QModelIndex, QAbstractListModel
from PyQt6.QtGui import (QColor, QPainter, QFont, QPen, QBrush, QKeySequence,
                         QShortcut, QPainterPath, QLinearGradient, QPixmap,
                         QCursor, QIcon)

from .effect_engine import (
    get_engine, EffectPreset, ParamSpec, SORT_MODES, CH_TYPES, EFFECT_DIRECTIONS,
    EFFECT_APPLICATION_SCOPES, EFFECT_SPATIAL_SCOPES,
    normalize_effect_category, effect_category_sort_key, normalize_effect_application_scope,
    build_effect_spatial_groups,
    BUILTIN_PATTERNS,
)
from .param_envelope import get_param_envelope_store, ParamEnvelope
# ★ R178：自定义效果库已删除（旧工程数据由 dispatch.custom_effect_migration
#   迁往调度台场景卡），此处不再 import custom_effect_store / icon_map。
from .effect_icon_library import (
    effect_icon_name_for_preset,
)
from .widget_kit import (
    EmptyState as _BkEmptyState,
    NumSlider as _BkNumSlider,
    PanelSection as _BkPanelSection,
    PAD_M as _BK_PAD_M,
    prop_row as _bk_prop_row, scope_qss as _bk_scope_qss,
)
from .main_icon_library import load_tabler_icon
from .effect_categories import (
    FAV_HEADER, TOP_FAV, USER_CATEGORY, order_favorites, order_recent,
    plan_preset_rows,
)
from .effect_category_bar import EffectCategoryBar
from . import effect_recall_fade as _recallfade
from . import effect_audio_runtime as _audio_runtime
from . import effect_width_controls as _width_controls
from . import effect_audio_controls as _audio_controls
from . import effect_catalog_curation as _catalog
from . import effect_target_scope as _target_scope, effect_owner_edit as _owner_edit
from . import effect_direction_groups as _direction_groups
from . import effect_manual_slice as _slice_control
from . import effect_operating_preferences as _operating_preferences
from .effect_transport_sync import sync_speed_spin
from .effect_range_sync import restore_pair_values, sync_pair_spins
from . import effect_dispatch_state as _dispatch_state
from . import effect_reference_order as _reference_order
from . import effect_param_reuse as _param_reuse
from . import effect_form_author as _form_author
from . import effect_master_speed as _master_speed
from . import effect_user_store as _fxstore
# ★ R183 W2-FX：深查② §一剩余 14 项（标签编辑器 / 搜索语法 / 随机预设 / A/B 槽 /
#   参数宏 / 预设包导入导出 / 热度 / 悬停试听 / 不兼容淡出 / 缩略动画 / 骰子微扰 /
#   代理过滤 / 回到默认）实现在该模块；本文件只留个别一行接线 —— 上帝类方法数
#   不得增长（CLAUDE.md §10.1），故新逻辑一律不进本类。
from . import effect_browser_w2 as _w2fx, effect_workspace_ui as _fx_workspace, effect_library_delegate as _fx_cards
from ..utils.wing_block import (
    WING_DIRECTION_LABELS,
    WING_DIRECTION_LOW_TO_HIGH,
    WING_DIRECTION_MODES,
    WING_DIRECTION_SYMBOLS,
    normalize_wing_direction,
)
from ..core.unified_params import SlotRegistry, SlotSemantics
from src.utils.identity_cache import IdentityCache, fixture_ids_signature

from . import qascade_theme as _th

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..processing.dispatch.effect_bus import EffectNode

# ─── 颜色常量（令牌化改造：值迁移到 qascade_theme 令牌，变量名保持不变，
#      全文件 ~131 处 setStyleSheet 引用自动跟随；CAT_COLORS/CH_COLORS 为
#      领域编码色，按 restyle_spec §4 保 hue 不动）───────────────────────────
BG_MAIN    = _th.BG_ROOT        # 原 #1e1e1e（根底）
BG_PANEL   = _th.BG_EDITOR      # 原 #252526（面板基底）
BG_HEADER  = _th.BG_PANEL       # 原 #252526 → 抬升表头层
BG_ITEM    = _th.BG_ROOT        # 原 #1e1e1e（嵌套容器）
BG_ITEM_SEL = _th.SELECT_BLUE   # 列表选中统一 select_blue（对齐全局 QSS）
BG_ITEM_HOV = _th.BG_PANEL      # 行 hover → bg_panel
FG_MAIN    = _th.TEXT_PRIMARY
FG_DIM     = _th.TEXT_SECONDARY
FG_ACCENT  = _th.FOCUS_BLUE     # 高亮蓝文字/描边→focus_blue（作填充处个别改 SELECT_BLUE）
FG_GREEN   = _th.BRIGHT_SUCCESS
FG_ORANGE  = _th.BRIGHT_WARN
ACCENT     = _th.ACTIVE_ORANGE  # 主行动色：原酒红 #b85a6e → active_orange 家族（audit-D 玫红退役）


def _scoped_key(ch_value: Any) -> Tuple[str, str]:
    """通道值 → (纯键, 类型作用域)：`function_1@<def_id>` → ("function_1", def_id)。"""
    try:
        from .project_channels import split_scope
        return split_scope(ch_value)
    except Exception:
        v = str(ch_value or "")
        return (v.partition("@")[0], v.partition("@")[2]) if "@" in v else (v, "")


def _fixtures_for_scope(fixtures: list, fdefs: dict, ch_value: Any) -> list:
    """按通道值的**类型作用域**过滤设备（未限定 → 原样返回）。

    ★ 2026-09-19 用户定案："把几种通道全部混到一起了，都没分开" —— 选中
      `Vario · Zoom`（function_1@Vario）时只能写 Vario 设备，不能把 Zoom 的
      值顺带写给 Jet 的 Motor / Nozzle Fan 的 Turn。
    """
    _k, def_id = _scoped_key(ch_value)
    if not def_id:
        return fixtures
    out = []
    for fi in fixtures or []:
        fd = (fdefs or {}).get(getattr(fi, "id", None))
        did = str(getattr(fd, "id", "") or getattr(fi, "def_id", "") or "")
        if did == def_id:
            out.append(fi)
    return out


def _scope_frames_inputs(fixtures: list, fdefs: dict, positions: dict,
                         ch_types: list) -> tuple:
    """按通道值的**设备类型作用域**收窄 (fixtures, fdefs, positions)。

    未限定作用域（通用通道）→ 原样返回；限定了 → 只留该类型的设备，
    并同步收窄 fdefs/positions（三者必须同口径，否则坐标/定义表错位）。
    """
    if not ch_types:
        return fixtures, fdefs, positions
    scoped = _fixtures_for_scope(fixtures, fdefs, ch_types[0])
    if scoped is fixtures:
        return fixtures, fdefs, positions
    keep = {str(getattr(fi, "id", "")) for fi in scoped}
    return (scoped,
            {fid: fd for fid, fd in (fdefs or {}).items() if str(fid) in keep},
            {fid: pos for fid, pos in (positions or {}).items() if str(fid) in keep})


def _slice_status(panel, msg: str) -> None:
    """往状态栏写一条切片错误信息（面板无状态栏 → 静默）。"""
    try:
        if hasattr(panel, "_status_lbl"):
            panel._status_lbl.setText(msg)
    except Exception:
        logger.debug("effect_browser: slice status write failed", exc_info=True)


def _rotate_scope_narrow(panel, fixtures, positions, params) -> tuple:
    """`_rotate_manual_slice_values` 的自收窄：作用域取自 `params` 里的通道键。

    为什么从 `params` 取而不是加参数：本函数被两处调用，其中一处在通道表单里
    已经把该键替换成**纯键**（引擎只收纯键）—— 那种情况下这里取不到作用域，
    返回原样；调用方自己已用 `_slice_scoped_inputs(..., [ch])` 收过。
    直接调用者（测试/未来新增路径）传的是带作用域的原值 → 这里收窄。
    """
    key = panel._effect_channel_param_key(params)
    if not key:
        return fixtures, positions
    _plain, def_id = _scoped_key((params or {}).get(key))
    if not def_id:
        return fixtures, positions
    return _slice_scoped_inputs(panel, fixtures, positions, [str((params or {}).get(key))])


def _slice_scoped_inputs(panel, fixtures: list, positions: dict,
                         ch_types: Optional[list] = None) -> tuple:
    """切片分组/旋转用的 `(fixtures, positions)`，**与真正写帧的那条路径同口径**。

    ★★ R141 修复（另一 agent 实测的"切片空推进"）：切片数/旋转分组走的是
      `_get_active_fixtures()` 直接结果，而真正写出的帧在 `_compute_channel_frames`
      里还要先过 `_scope_frames_inputs`（按 `ch_type` 的**设备类型作用域**再收窄一次）。
      带类型作用域的通道（如 `function_1@vario`）下两者不一致 —— 实测
      「选择集 8 / 作用域 4」→ 面板按 8 组切片、引擎实际只有 4 组 →
      **连按切片键出现空推进**（按两次才动一次），状态栏 `切片 x/n` 与设备数不符。

    这里复用同一组模块级函数（`_fixtures_for_scope` / `_scope_frames_inputs` 的身份判据），
    并把**位置表**同步收窄 —— 三者必须同口径，否则坐标错位。
    未限定作用域（通用通道）→ 原样返回，零行为变化。

    `ch_types` 传 None → 取面板当前选择集；旋转路径逐通道表单渲染，必须传**该表单的
    通道**（`[ch]`），否则组域与 `frame.values` 的下标域对不上（块>1 时旋转错位）。
    """
    try:
        return _narrow_by_ch_scope(panel, fixtures, positions, ch_types)
    except Exception:
        logger.debug("effect_browser: slice scope narrow failed", exc_info=True)
        return fixtures, positions


def _narrow_by_ch_scope(panel, fixtures, positions, ch_types) -> tuple:
    """`_slice_scoped_inputs` 的实际计算（拆出以守住 CC/行数门禁）。"""
    chs = list(ch_types if ch_types is not None
               else (panel._get_selected_ch_types() or []))
    if not chs:
        return fixtures, positions
    scoped, _fdefs, scoped_pos = _scope_frames_inputs(
        list(fixtures or []), _fx_fdefs_map(panel, fixtures),
        dict(positions or {}), chs)
    same = scoped is fixtures or len(scoped) == len(fixtures or [])
    return (fixtures, positions) if same else (scoped, scoped_pos)


def _fx_fdefs_map(panel, fixtures: list) -> dict:
    """`{fixture_id: FixtureDefinition}`（按工程定义表建；取不到 → 空表）。

    `_fixtures_for_scope` 要按 `fi.id` 查定义，而面板各处传的是 `{def_id: fd}`
    → 这里做一次按设备的映射（切片路径每次调用一次，量级 = 选择集大小）。
    """
    proj = _module_project_object(panel)
    raw = getattr(proj, "fixture_defs", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        by_id = dict(raw)
    else:
        by_id = {str(getattr(fd, "id", "")): fd for fd in raw}
    out = {}
    for fi in fixtures or ():
        fd = by_id.get(str(getattr(fi, "def_id", "")))
        if fd is not None:
            out[str(getattr(fi, "id", ""))] = fd
    return out


def _norm_channel_type(ch_type: Any) -> str:
    raw = str(getattr(ch_type, "value", ch_type) or "").strip().lower()
    if "@" in raw:                     # 类型作用域值 → 按纯键归一
        raw = raw.partition("@")[0]
    aliases = {
        "red": "r",
        "green": "g",
        "blue": "b",
        "white": "w",
        "intensity": "dimmer",
        "brightness": "dimmer",
        "light": "dimmer",
        "pan": "motor_x",
        "x": "motor_x",
        "x_axis": "motor_x",
        "tilt": "motor_y",
        "y": "motor_y",
        "y_axis": "motor_y",
        "z": "motor_z",
        "z_axis": "motor_z",
    }
    return aliases.get(raw, raw)


def _wing_block_mode_active(params: Optional[dict]) -> bool:
    from ..utils.wing_block import spatial_transform_active
    return spatial_transform_active(params)


def _channel_matches(ch_type: Any, allowed_ch: list) -> bool:
    if not allowed_ch:
        return True
    target = _norm_channel_type(ch_type)
    allowed = {_norm_channel_type(ch) for ch in allowed_ch}
    if target in allowed:
        return True
    level_family = {"dimmer", "pump", "valve"}
    if target == "level" and bool(allowed & level_family):
        return True
    if "level" in allowed and target in level_family:
        return True
    return False


def _is_rgbw_channel(ch_type: Any) -> bool:
    return _norm_channel_type(ch_type) in {"r", "red", "g", "green", "b", "blue", "w", "white"}


def _effect_target_aliases(ch_type: Any) -> set:
    target = _norm_channel_type(ch_type)
    aliases = {
        "r": {"r", "red"},
        "g": {"g", "green"},
        "b": {"b", "blue"},
        "w": {"w", "white"},
        "dimmer": {"dimmer"},
        "level": {"level", "dimmer", "pump", "valve"},
        "pump": {"pump"},
        "valve": {"valve"},
        "motor_x": {"motor_x", "pan"},
        "motor_y": {"motor_y", "tilt"},
        "motor_z": {"motor_z"},
    }
    return set(aliases.get(target, {target}))


def _effect_address_key(ch_type: Any) -> str:
    target = _norm_channel_type(ch_type)
    upper = {"r": "R", "g": "G", "b": "B", "w": "W"}
    return upper.get(target, target)



def _module_project_object(owner) -> Any:
    """面板持有的工程对象（`_project` 或它包着的 `.project`，两代口径都试）。"""
    proj = getattr(owner, "_project", None)
    return getattr(proj, "project", proj)


def _module_extra_ch_keys(owner) -> List[str]:
    """工程里**内置 CH_TYPES 之外**的通道键（功能通道/自定义通道）。

    ★ 2026-09-19 用户实报："通用类型的效果应该自动识别该项目所有的通道，
      包括功能通道"。内置表只管 dimmer/R/G/B/W/motor_*/valve/pump，工程里的
      `function_N`（真名如 Zoom / PumpOnOff）不在其中；把它们补进候选，
      效果才能选中并真正写到它们上面。
    """
    try:
        from .project_channels import extra_keys as _extra
        return list(_extra(_module_project_object(owner), CH_TYPES))
    except Exception:
        logger.debug("effect_browser: 工程通道补充失败", exc_info=True)
        return []


def _module_channel_choices(owner) -> List[str]:
    """参数区「通道类别」候选：内置通用通道在前，**工程通道按类型分开**在后。

    ★ 2026-09-19 用户定案：同名槽位在不同类型里语义不同（function_1 在 Jet 是
      Motor、在 Vario 是 Zoom）→ 必须逐类型各成一条（值为 `键@类型id`），
      不能合并成一条（那等于同时动了所有类型）。
    """
    out = list(CH_TYPES)
    builtin_norm = {_norm_channel_type(c) for c in CH_TYPES}
    try:
        from .project_channels import is_scoped, project_items
        for _label, val in project_items(_module_project_object(owner)):
            if val in out:
                continue
            if not is_scoped(val) and _norm_channel_type(val) in builtin_norm:
                continue          # 内置已覆盖（如工程里的 'R' → 'r'）
            out.append(val)
    except Exception:
        logger.debug("effect_browser: 工程通道候选构建失败", exc_info=True)
    return out


def _module_channel_label(owner, value: Any) -> str:
    """通道值 → 显示名。

    作用域值（`function_1@类型id`）→ 工程真名（"Vario · Zoom"）；
    全局键 → 内置名（"红 R"）。两者都取自同一张工程清单表。
    """
    v = str(value or "")
    try:
        from .project_channels import ch_label as _cl, project_label_map as _pm
        hit = _pm(_module_project_object(owner)).get(v)
        if hit and hit != v:
            return hit
        plain = _scoped_key(v)[0]
        return _cl(plain)
    except Exception:
        logger.debug("effect_browser: 工程通道标签查询失败", exc_info=True)
        return _scoped_key(v)[0]


def _addr_candidate_types(owner) -> List[str]:
    """地址映射的候选通道类型 = 内置 + 工程额外通道（含功能通道）。

    ★ 2026-09-19 用户实报："通用效果应能应用到项目所有通道，含功能通道"。
      候选恒为内置 CH_TYPES 时，工程里的 `function_1` **根本进不了地址映射**
      → 即便用户在参数区选了它，写回也静默落空（实测：`_resolve_ch_offsets`
      能解析出该通道偏移，但映射键里没有它 = 白写）。
    """
    return list(CH_TYPES) + ["level", "pan", "tilt"] + _module_extra_ch_keys(owner)



def _fill_owned_combo(combo, owner, keys) -> None:
    """按 keys 填充下拉，显示名走工程真名（功能通道不是裸键名）。"""
    for ch in keys:
        combo.addItem(_module_channel_label(owner, ch), ch)


def _pw_channel_choices(pw) -> List[str]:
    """通道控件选项 = `ParamSpec.choices` + **工程实际通道**。

    ★ 2026-09-19 用户实报："通用类型的效果应该自动识别该项目所有的通道，
      包括功能通道（传真实名字，不要还在 function1）能应用到所有类型的通道上。"
      内置表只有 dimmer/R/G/B/W/motor_*/valve/pump；工程里的 `function_N`
      由此处补入，显示名交给工程真名表。
    """
    base = [str(c) for c in (getattr(pw._spec, "choices", []) or [])]
    out = list(base)
    try:
        from .project_channels import extra_keys as _extra
        for k in _extra(getattr(pw, "_project", None), base):
            if k not in out:
                out.append(k)
    except Exception:
        pass
    return out


def _fill_pw_channel_combo(combo, pw) -> None:
    """把「内置 + 工程实际通道」填进通道类下拉（功能通道显示工程真名）。"""
    for ch in _pw_channel_choices(pw):
        combo.addItem(_pw_channel_label(pw, ch), ch)


def _pw_channel_label(pw, ch) -> str:
    """通道类下拉项的显示名（功能通道取工程真名）。"""
    try:
        from .project_channels import project_label_map as _pm, ch_label as _cl
        k = str(ch or "")
        hit = _pm(getattr(pw, "_project", None)).get(k)
        if hit and hit != k:
            return hit
        return _cl(k)
    except Exception:
        return str(ch or "")


def _is_channel_param_spec(spec) -> bool:
    """该 ParamSpec 是否是通道类参数（取值空间要补工程通道的那几个 key）。"""
    if spec is None or str(getattr(spec, "type", "") or "") != "choice":
        return False
    return str(getattr(spec, "key", "") or "").lower() in _CHANNEL_PARAM_KEYS


def _append_missing_options(combo, owner, keys) -> None:
    """把 `keys` 里下拉尚未含有的项追加进去（显示名走工程真名）。"""
    extra = [k for k in keys if combo.findData(k) < 0]
    if not extra:
        return
    combo.blockSignals(True)
    for k in extra:
        combo.addItem(_module_channel_label(owner, k), k)
    combo.blockSignals(False)


def _reselect_current(combo, pw, want=None) -> None:
    """把下拉选中项恢复到控件当前值（追加项命中时必须重选）。"""
    cur = want or _current_choice_value(pw)
    if not cur:
        return
    idx = combo.findData(str(cur))
    if idx >= 0 and idx != combo.currentIndex():
        combo.setCurrentIndex(idx)


def _inject_channel_choices(owner, pw, spec) -> None:
    """把**工程实际通道**追加进通道类下拉（`ParamSpec.choices` 是静态内置表）。

    仅作用于通道类参数；追加后若当前值正落在追加项上必须重新选中 —— 否则
    界面显示内置项、实际写的却是另一个通道。
    """
    if not _is_channel_param_spec(spec):
        return
    combo = getattr(pw, "_channel_combo", None) or getattr(pw, "_ctrl", None)
    if not isinstance(combo, QComboBox):
        return
    _append_missing_options(combo, owner, _module_extra_ch_keys(owner))
    _reselect_current(combo, pw)


def _current_choice_value(pw) -> Optional[str]:
    """通道控件当前值（QComboBox 直读，退化到 `_channel_values` 首项）。"""
    try:
        if hasattr(pw, "value"):
            v = pw.value()
            if v:
                return str(v)
    except Exception:
        pass
    vals = list(getattr(pw, "_channel_values", None) or [])
    return str(vals[0]) if vals else None


#: 通道类参数键（这些参数的取值空间要补工程通道）
_CHANNEL_PARAM_KEYS = frozenset({"ch_type", "ch", "channel", "target_channel"})


def _allowed_norm_set(allowed_ch: Optional[list]) -> set:
    return {_norm_channel_type(ch) for ch in (allowed_ch or []) if str(ch)}


def _address_keys_for_output(ch_type: Any, allowed_ch: Optional[list]) -> list:
    target = _norm_channel_type(ch_type)
    allowed = _allowed_norm_set(allowed_ch)
    if target == "level":
        if not allowed or "level" in allowed:
            return ["pump", "valve", "dimmer"]
        candidates = [ch for ch in ("pump", "valve", "dimmer") if ch in allowed]
        return candidates or ["pump", "valve", "dimmer"]
    return [_effect_address_key(ch_type), target, str(ch_type)]


def _fixture_addr_for_keys(am, output_keys, target_norm):
    """逐键取该设备**第一个命中**的地址（键序 = 偏好序）；都没有 → None。

    ★ R141 修正（另一轮 agent 报的"只有泵的设备收不到亮度类效果"）：`dimmer` 的键表
      原先恒为 `["dimmer"]`，**没有退回项** —— 只有 `pump` 的喷头/电磁阀设备
      （真工程常见）对 498 个声明 `dimmer` 的内置预设**完全不响应**
      （实测：只有 pump 的设备层值全 0，明确选 `pump` 才出光）。
      这里在"既无调光、也无 RGBW"时退回 `pump`/`valve`。
    为什么放在 RGBW 之后：原实现给"无 dimmer 但有 RGBW"的设备走亮度模拟，
    那是既有语义，不能被抢走（有 RGBW 就不退回泵）。
    """
    for ak in output_keys:
        got = am.get(ak)
        if got:
            return got
    if target_norm in ("dimmer", "level"):
        if not any(am.get(k) for k in ("R", "G", "B", "W")):
            for ak in ("pump", "valve"):
                got = am.get(ak)
                if got:
                    return got
    return None


def _slot_to_paramspec(sem: SlotSemantics) -> ParamSpec:
    """Convert a SlotSemantics to a ParamSpec so existing UI widgets can render it."""
    return ParamSpec(
        key=sem.slot.value,
        label=sem.label_cn,
        type=sem.param_type,
        default=sem.default,
        min_val=sem.min_val,
        max_val=sem.max_val,
        step=1.0 if sem.param_type == "int" else 0.01,
        choices=list(sem.choices),
        tooltip=sem.description,
    )


# 分类颜色：与 effect_engine.normalize_effect_category 后的规范分类一致。
#: 方向 → 中文标签。**模块级**：原先内联在 `_build_ui`，补齐 9 种方向后令该函数
#: 超出架构门禁基线（817 行）—— 搬出来同时让标签表可被单独引用与测试。
#: ★ 2026-09-19 用户要求"方向种类不低于 clip"：后 9 条为与 clip 对齐新增
#:   （语义见 `effect_directions` 模块头）。
DIRECTION_LABELS = {
    "default": "默认方向（跟随效果）",
    "reverse": "反向（默认方向反过来）",
    "left_to_right": "左 → 右",
    "right_to_left": "右 → 左",
    "front_to_back": "前 → 后",
    "back_to_front": "后 → 前",
    "diag_tl_br": "斜向：左前 → 右后",
    "diag_br_tl": "斜向：右后 → 左前",
    "diag_bl_tr": "斜向：左后 → 右前",
    "diag_tr_bl": "斜向：右前 → 左后",
    "center_out": "中间 → 两边",
    "edge_in": "两边 → 中间",
    "clockwise": "顺时针（绕中心）",
    "counterclockwise": "逆时针（绕中心）",
    "alternate": "奇偶交替",
    "alternate_block": "块交替",
    "wave": "波浪",
    "cascade": "级联（由慢到快）",
    "reverse_cascade": "反向级联（由快到慢）",
    "zigzag": "折返",
    "random": "随机（可复现）",
}


CAT_COLORS = {
    "通用/波形": "#44AAFF",
    "通用/跑灯": "#FF8844",
    "通用/渐变": "#5a9e6f",
    # ★ R172 三族新分类
    "通用/随机": "#a78bfa",
    "通用/变速": "#f2a65a",
    "通用/级联": "#4fd1c5",
    "通用/摇杆": "#FF44AA",
    "通用/音频": "#FFDD44",
    "通用/位置": "#88FFAA",
    "通用/自定义": "#AAAAAA",
    "灯光/亮度": "#FFCC44",
    "灯光/颜色": "#FF88DD",
    "灯光/跑灯": "#FFD166",
    "灯光/闪烁": "#FFE066",
    "灯光/开合": "#E9C46A",
    "泵/跑泉": "#FF6688",
    "泵/波浪": "#F72585",
    "泵/渐变": "#FF8FAB",
    "泵/脉冲": "#FF4D6D",
    "阀门/开合": "#FF8844",
    "阀门/脉冲": "#FF6B35",
    "阀门/跑泉": "#F77F00",
    "阀门/渐变": "#F4A261",
    "电机/龙摆": "#44CCFF",
    "电机/交叉": "#48CAE4",
    "电机/开合": "#90E0EF",
    "电机/波形": "#00B4D8",
    "电机/造型": "#0096C7",
    "电机/高级": "#0077B6",
    "电机/Cue": "#ADE8F4",
    # ★ R180 一级大类分组头色：全部复用上面的既有分类色值（无新色值），
    #   "全部"视图的分组头即一级大类名，走 _get_category_color 的 in CAT_COLORS 分支。
    #   （R194/L4-4：原"编排·高级"死键已删——normalize_effect_category 永不产出该名。）
    "灯光": "#FFCC44",
    "通用": "#44AAFF",
    "音频": "#FFDD44",
    "电机": "#44CCFF",
    "水景": "#FF6688",
    "矩阵": "#00B4D8",
    "其它": "#AAAAAA",
    "最近使用": "#E6B455",
}

def _get_category_color(cat_name: str) -> str:
    """根据规范分类名称返回稳定颜色（**必须返回 Qt 可解析的字符串**）。

    ★ 历史 bug（2026-09-13 修）：末段兜底曾返回 `hsl(h, 70%, 65%)` 字符串，
    而 `QColor("hsl(...)")` **解析失败**（Qt 只认 HSV/HSL 结构体或 #hex）→
    这些分类的卡片/色块被画成**纯黑**。实测受影响分类 5 个：
    水景/泵、水景/阀、水景/编程秀、音频反应、自定义/我的效果。
    修法：仍用初值给的小时数做确定性着色，但经 `QColor.fromHsl` 转成 `#rrggbb`
    再返回 —— 保持"分类→颜色稳定"的既有意图，同时保证 Qt 能解析。
    """
    cat_name = normalize_effect_category(cat_name)
    if cat_name in CAT_COLORS:
        return CAT_COLORS[cat_name]
    if cat_name.startswith("灯光"):
        return "#FFCC44"
    elif cat_name.startswith("泵"):
        return "#FF6688"
    elif cat_name.startswith("电机"):
        return "#44CCFF"
    elif cat_name.startswith("阀门"):
        return "#FF8844"
    elif cat_name.startswith("通用"):
        return "#a0d4ff"
    h = hash(cat_name) % 360
    # ★ 必须转 #hex：QColor 不解析 "hsl(...)" 字符串（会得到无效色→画成黑色）。
    #   fromHsl(h, s*, l*) 用 0-255 的 s/l；此处 70%/65% → 179/166。
    #   ★ h 必须用确定性哈希（zlib.crc32）：内建 hash() 对 str 每次启动随机化，
    #   会导致同一分类每次开软件颜色都不同（实测"几何"一次绿一次红）。
    import zlib as _zlib
    h = _zlib.crc32(cat_name.encode("utf-8")) % 360
    return QColor.fromHsl(int(h) % 360, 179, 166).name()

# 通道类别颜色
CH_COLORS = {
    "dimmer":  "#FFDD88",
    "R":       "#FF5555",
    "G":       "#55FF55",
    "B":       "#5599FF",
    "W":       "#FFFFFF",
    "valve":   "#FF8844",
    "motor_x": "#44AAFF",
    "motor_y": "#44FFAA",
    "motor_z": "#AA44FF",
    "pump":    "#FF44AA",
}


def _normalize_fixture_ids(ids: Optional[list]) -> List[str]:
    out: List[str] = []
    seen = set()
    for fid in (ids or []):
        sfid = str(fid)
        if sfid and sfid not in seen:
            out.append(sfid)
            seen.add(sfid)
    return out


def _normalize_fixture_sequence_ids(ids: Optional[list]) -> List[str]:
    return [str(fid) for fid in (ids or []) if str(fid)]


# ── _tick_play 热路径缓存工具（对象身份稳定化 + 有界淘汰）──────────────────
# 背景（2026-09-17 实报「点存储区场景后完全冻结」根因）：
#   _tick_play 每帧把 fixtures / positions / fdefs / 地址映射 交给效果引擎，
#   而这些结构的缓存键是 `id(obj)` + `cached is obj` 的身份校验。任何一处
#   每帧返回新对象（旧实现空作用域 `list(all_fx)`），全部下游缓存就每帧
#   全量 miss：4889 台规模单帧 0.2~0.8s，UI 线程直接冻结且不自愈。
# 因此：① 解析结果必须复用同一对象；② 缓存淘汰只逐出最旧一条，绝不整表
#   clear() —— 整表清空会连带击穿其它正在运行的效果的缓存。
_FX_LIST_CACHE_MAX = 16
_POS_CACHE_MAX = 16
_FDEFS_CACHE_MAX = 16
_ADDR_MAP_CACHE_MAX = 16
_CH_STRUCT_CACHE_MAX = 32
_CH_OFFSET_CACHE_MAX = 4096

#: 每 `(配接表身份, 通道表版本, def_id, ch_type)` → 命中的通道下标元组。
#: 见 `_def_channel_offsets`：这一层与**设备实例无关**，故可从"每设备 × 每通道类型"
#: 的重复扫描里彻底提出来（真工程实测 = 一次编辑语义调用里 10395 次 `_norm_channel_type`）。
_DEF_CH_OFFSET_CACHE: Dict[tuple, tuple] = {}


def _find_fixture_def(raw_defs, def_id):
    """从 dict / list 两代形态的配接表里取该设备定义（没有 → None）。"""
    if not def_id:
        return None
    if isinstance(raw_defs, dict):
        return raw_defs.get(def_id)
    for d in (raw_defs or ()):
        if getattr(d, "id", None) == def_id or getattr(d, "def_id", None) == def_id:
            return d
    return None


def _def_channel_offsets(raw_defs, def_id, ch_type, rev: int = 0) -> tuple:
    """该设备定义里 `ch_type` 命中的**通道下标**（只与「定义 + 通道类型」有关）。

    ★★ R141 性能修复（真工程实测）：原 `_resolve_ch_offsets` 每次调用都遍历
      `fd.channels` 并逐项跑 `_norm_channel_type(ct) in _effect_target_aliases(ch_type)`。
      而它在 `_effect_addr_maps_for_fixtures` 里被**每设备 × 每候选通道类型**调用 ——
      真工程一次 `for_edit=True` 里累计 **1536 次**、`_norm_channel_type` **10395 次**，
      是那次调用剩余 ~70ms 里最大的一块。
      结果其实只由 `(设备定义, 通道类型)` 决定，与设备实例无关 → 按该二元组记忆化。
      `rev` 传工程配接版本号（`Project.patch_rev`，R141b 引入）；
      `id(raw_defs)` + 长度 + 版本号三者任一变即失效（换工程 / 改通道表 / 改配接）。
    """
    key = (id(raw_defs), len(raw_defs or ()), int(rev or 0),
           str(def_id or ""), str(ch_type or ""))
    hit = _DEF_CH_OFFSET_CACHE.get(key)
    if hit is not None:
        return hit
    fd = _find_fixture_def(raw_defs, def_id)
    targets = _effect_target_aliases(ch_type)
    out = tuple(
        i for i, ct in enumerate(list(getattr(fd, "channels", None) or ()))
        if _norm_channel_type(ct) in targets
    ) if fd is not None else ()
    if len(_DEF_CH_OFFSET_CACHE) >= _CH_OFFSET_CACHE_MAX:
        _DEF_CH_OFFSET_CACHE.clear()
    _DEF_CH_OFFSET_CACHE[key] = out
    return out


def _ensure_cache_dict(owner, name: str) -> dict:
    """取 owner 上的 dict 缓存字段，缺失或为 None 时懒创建。

    `_invalidate_tick_caches` 用 None 作失效哨兵；懒创建让热路径不依赖
    「set_context 一定先跑过」，也避免给上帝类 __init__ 再加字段行。
    """
    cache = getattr(owner, name, None)
    if cache is None:
        cache = {}
        setattr(owner, name, cache)
    return cache


def _cache_put_bounded(cache: dict, key, value, cap: int) -> None:
    """有界写入：满时只逐出最久未插入的一条（旧实现 clear() 击穿全表）。"""
    if key not in cache and len(cache) >= cap:
        oldest = next(iter(cache), None)
        if oldest is not None:
            del cache[oldest]
    cache[key] = value


def _stable_all_fixtures(cache: dict, raw_fixtures) -> list:
    """「全项目设备」列表的稳定对象（空作用域每帧都要用它）。

    按「源容器身份 + 长度」判定设备表是否变化：不变则复用同一 list 对象，
    使下游所有 `id(fixtures)` 键缓存可命中。持有源容器引用 → 其 id 不会被
    回收复用，身份判定安全（设备增删改长度或换容器都会自动重建）。
    """
    cached = cache.get("value")
    src = cache.get("src")
    if cached is not None and src is raw_fixtures and len(cached) == len(raw_fixtures):
        return cached
    out = list(raw_fixtures or [])
    cache["src"] = raw_fixtures
    cache["value"] = out
    return out


def _fixtures_for_ids_stable(ids_norm: List[str], by_id: dict,
                             fx_cache: dict) -> list:
    """按设备 id 序列解析实例列表，返回**对象身份稳定**的缓存对象。

    未命中时才重建；命中直接返回同一 list。下游 _build_positions /
    _build_fdefs / 地址映射 / 通道结构都以 `id(fixtures)` 为键，每帧新建
    list 会让它们全量重建（本次冻结根因）。
    """
    key = tuple(ids_norm)
    cached = fx_cache.get(key)
    if cached is not None:
        return cached
    out: list = []
    seen: set = set()
    for fid in ids_norm:
        fi = by_id.get(fid)
        if fi is not None and fid not in seen:
            out.append(fi)
            seen.add(fid)
    _cache_put_bounded(fx_cache, key, out, _FX_LIST_CACHE_MAX)
    return out


def _fixture_defs_by_id(project) -> dict:
    """project.fixture_defs → {def_id: FixtureDefinition}（播放期不变）。"""
    if project is None or not hasattr(project, 'fixture_defs'):
        return {}
    raw_defs = project.fixture_defs
    if isinstance(raw_defs, dict):
        return raw_defs
    out: dict = {}
    for d in (raw_defs or []):
        k = getattr(d, 'id', None) or getattr(d, 'def_id', None)
        if k:
            out[k] = d
    return out


def _subset_marked_ids(explicit, current_ids) -> list:
    """R165：本次要标记的「子集」id 列表。

    「子集来源=按TopView子集标记」时，标记的就是**TopView 当前选中的那些**
    （= 第 0 组）。显式传入优先，留出将来"标记 ≠ 当前选择"的口子。
    """
    if explicit is not None:
        return _normalize_fixture_ids(explicit)
    return _normalize_fixture_ids(current_ids)


def _inject_subset_marked(widget, out: dict, ids) -> None:
    """R165/R170：把 **TopView 里选中的子集**注入 params（`_subset_ids`，**原地**）。

    ★★ R170 定案（用户纠正）：子集没有"来源"选项了 —— **TopView 里选中谁，谁就是子集**。
      所以这里不再看 `subset_source`，只要组内有子集标记就注入；没有则 pop 掉
      （不注入 → 引擎里分组表全是母集合组 → 无相位差，等于关）。

    只保留母集合内的 id（`allowed`）——越界的 id 会让分组表把不存在的设备算进子集。
    """
    allowed = set(ids or ())
    marked = [fid for fid in (getattr(widget, "_selected_group_subset_ids", None) or [])
              if fid in allowed]
    if marked:
        out["_subset_ids"] = marked
    else:
        out.pop("_subset_ids", None)


def _spec_range(spec) -> tuple:
    """参数取值域 `(lo, hi)`；坏值回退 0..255（`randomize` 用，抽出以省其行数）。"""
    try:
        return (float(getattr(spec, "min_val", 0.0) or 0.0),
                float(getattr(spec, "max_val", 255.0) or 255.0))
    except Exception:
        return (0.0, 255.0)


#: ★ R165：**不参与「随机」按钮的参数**，见 `_randomize_current_params` docstring。
#:   子集相位是"精确可追溯"的结构量（随手随出 137° 没有意义）。
_NO_RANDOM_KEYS = frozenset({"subset_delay_phase"})


def _subset_scope_override(widget, params: dict, fixture_ids: Optional[list]):
    """R189: phase metadata cannot enlarge the caller's actual target set.

    A saved whole-group node still supplies its whole-group targets explicitly.
    """
    return fixture_ids


def _scope_ids_from_programmer_state(ids: List[str],
                                    base: list) -> tuple[List[str], List[str]]:
    """Programmer 选择 → 效果输出作用域 (ids, 有序序列)。

    按 Programmer 的基准顺序（`_current_base_order_ids`）排序选择集，
    选择集里不在基准中的追加在尾部（保序、去重）。基准为空时以 ids 自身为序
    （与旧内联实现 `base or ids` 同语义）。
    ★ 隐藏加载语义（2026-09-17 用户定案）：调用方只按「是否已释放」决定是否
      走本函数，**不再看 `isVisible()`** —— 旧实现一隐藏就返回空作用域，
      效果输出范围突变为全项目设备（用户实报"一关页面就完全不认人了"）。
    """
    norm_ids = _normalize_fixture_ids(ids)
    if not norm_ids:
        return [], []
    base_ids = _normalize_fixture_sequence_ids(base) or norm_ids
    allowed = set(norm_ids)
    ordered = [fid for fid in base_ids if fid in allowed]
    present = set(ordered)
    for fid in norm_ids:
        if fid not in present:
            ordered.append(fid)
            present.add(fid)
    return norm_ids, (ordered or norm_ids)


def _is_light_member_name(name: str) -> bool:
    """灯成员类型名判定：
    名字含 灯/LED/RGBW/Spot/光源/Light。底座灯(如「超广角水下Led 底座RGBW」)
    也命中——是否补光灯由调用方按「底座」名/到包中心距离≈0 判定。
    """
    n = str(name or "").lower()
    return any(k in n for k in ("灯", "led", "rgbw", "spot", "光源", "light"))


def _deep_audio_fields(frame) -> dict:
    """把**已有深度识别成果**（C5 音高/和声，来自 `deep_music_analysis`）送进效果层。

    ★ R132：【通用/音频】真音频族的高级/专家效果直接吃这些量（音高走向、和声张力…）。
    缺失字段给安全默认（-1 / 0 / ""），效果侧按"无音高"降级，绝不抛。
    """
    try:
        return {
            "pitch_midi": float(getattr(frame, "pitch_midi", -1.0) or -1.0),
            "pitch_confidence": float(getattr(frame, "pitch_confidence", 0.0) or 0.0),
            "chord_root": int(getattr(frame, "chord_root", -1)
                              if getattr(frame, "chord_root", None) is not None else -1),
            "chord_quality": str(getattr(frame, "chord_quality", "") or ""),
        }
    except Exception:
        return {"pitch_midi": -1.0, "pitch_confidence": 0.0,
                "chord_root": -1, "chord_quality": ""}


def _group_key_from_ids(ids: Optional[list]) -> str:
    norm = _normalize_fixture_ids(ids)
    if not norm:
        return "__all__"
    return "grp:" + "|".join(norm)


def _compute_group_scope_ids(selected, base) -> list:
    """★★★ R168：**效果作用域** = 母集合（当"选中"是母集合的**真子集**时），否则 = 选中本身。

    用户语义（原话）::

        "我选中例如42个二维数控，对它们的x轴应用一个波形，是整体摇摆，然后呢选中部分
         设备例如奇数个的子集，在同一个轨道查看应用的效果现在已经给我区分成新效果了，
         也就是母集合当前的状态不会传给子集合，没有根据逻辑的关系让子集合得以真的去
         调整原来效果里面的那个子集 delay 了。也就是只要一选新的组，哪怕明明是子集，
         但是却不同步传递当前的状态。"

    → **子集是"母集合效果内部的相位分组标记"，不是新的效果作用域**。
      旧实现把子集当选中的作用域：
        · `_current_live_selection_key()` 由子集算 → 与母集合不同 → 判为"新效果"，
          母集合上正在跑的效果状态（preset + 参数）不传递 → 用户无法在原子效果上
          调『子集 delay 相位』；
        · 传给引擎的 fixtures 列表也只剩子集 → 另一半设备没有输出。

    判据用**真子集**（`set(sel) < set(base)`）：相等时（普通框选/设备组）
    行为与改前**逐字节相同**，不影响任何既有用法。
    """
    sel = _normalize_fixture_ids(selected)
    base_seq = _normalize_fixture_sequence_ids(base)
    if base_seq and sel and set(sel) < set(base_seq):
        return list(base_seq)
    return list(sel)

def _clear_group_scope_fields(panel) -> None:
    """把"作用域/子集标记/空间分组"四字段清空（切到"全部设备"时用）。

    ★ R168：作用域与子集标记必须一起清 —— 否则切到"全部设备"后仍沿用上一个组的
    子集标记，下一条效果会带着**不该有的分组相位**（静默走样，用户只看到"效果不对"）。
    """
    panel._scope_explicit = False
    for attr in ("_selected_group_scope_ids", "_selected_group_subset_ids",
                 "_selected_group_spatial_groups",
                 "_selected_group_spatial_groups_raw"):
        try:
            setattr(panel, attr, [])
        except Exception:
            pass


def _group_ui_state_key(panel) -> str:
    """★ R168：UI 参数状态的键 = **作用域**（不是选中）—— 母/子集共享同一份，
    否则选中子集时会读到另一份（或读不到）参数，用户看到"母集合状态没传过来"。
    """
    ids = (getattr(panel, "_selected_group_scope_ids", None)
           or getattr(panel, "_selected_group_ids", None))
    return _group_key_from_ids(ids)


def _group_label_from_ids(ids: Optional[list]) -> str:
    norm = _normalize_fixture_ids(ids)
    return "ALL" if not norm else f"GROUP({len(norm)})"

# 共享控件 QSS：通用外观尽量对齐全局 GLOBAL_QSS（restyle_spec §3），仅保留
# 本面板的局部 padding/字号特化；四态色值全部走主题令牌。
_BTN = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{_th.TEXT_PRIMARY};"
    f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:4px 10px;font-size:12px;}}"
    f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};border-color:#5C5C5C;}}"
    f"QPushButton:pressed{{background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};}}"
)
_BTN_ACCENT = (
    f"QPushButton{{background:{ACCENT};color:{_th.TEXT_SELECTED_FG};border:none;"
    "border-radius:4px;padding:5px 14px;font-size:12px;font-weight:bold;}"
    f"QPushButton:hover{{background:{_th.SELECT_ORANGE};}}"
    f"QPushButton:pressed{{background:{_th.WARN_ORANGE};color:{_th.TEXT_SELECTED_FG};}}"
)
_COMBO = (
    f"QComboBox{{background:{BG_PANEL};color:{_th.TEXT_PRIMARY};"
    f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:3px 6px;"
    "font-size:11px;}"
    "QComboBox::drop-down{border:none;width:18px;}"
    # §11.4 原型A：下拉弹层禁纯黑，与全局一致改灰 #282828 + 1px #3A3A3A 边
    f"QComboBox QAbstractItemView{{background:#282828;color:{_th.TEXT_PRIMARY};"
    f"selection-background-color:{_th.SELECT_BLUE};selection-color:{_th.TEXT_SELECTED_FG};"
    "border:1px solid #3A3A3A;}"
)
_INPUT = (
    f"QLineEdit{{background:{_th.BG_INPUT};color:{_th.TEXT_PRIMARY};"
    f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:4px 6px;"
    "font-size:11px;}"
    f"QLineEdit:focus{{border-color:{_th.FOCUS_BLUE};"
    f"background-color:{_th.BG_INPUT_EDIT};}}"
)
_SPIN = (
    f"QDoubleSpinBox,QSpinBox{{background:{_th.BG_INPUT};color:{_th.TEXT_PRIMARY};"
    f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:3px;"
    "font-size:11px;}"
    f"QDoubleSpinBox:focus,QSpinBox:focus{{border-color:{_th.FOCUS_BLUE};}}"
)
_SLIDER = (
    f"QSlider::groove:horizontal{{background:{_th.BG_INPUT};height:4px;"
    "border-radius:2px;}"
    f"QSlider::handle:horizontal{{background:{_th.FOCUS_BLUE};width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    f"QSlider::handle:horizontal:hover{{background:{_th.TEXT_PRIMARY};}}"
    f"QSlider::sub-page:horizontal{{background:{_th.SELECT_BLUE};border-radius:2px;}}"
)
_CHECK = (
    f"QCheckBox{{color:{_th.TEXT_PRIMARY};font-size:11px;spacing:5px;background:transparent;}}"
    f"QCheckBox::indicator{{width:14px;height:14px;border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:3px;background:{_th.BG_BUTTON};}}"
    f"QCheckBox::indicator:checked{{background:{_th.SELECT_BLUE};border-color:{_th.SELECT_BLUE};}}"
)


def _stable_visual_seed(text: Any) -> int:
    total = 0
    for idx, ch in enumerate(str(text or "")):
        total = (total + (idx + 1) * ord(ch)) % 1000003
    return total


def _preset_icon_family(preset: EffectPreset) -> tuple[str, int]:
    icon = str(getattr(preset, "icon", "") or "")
    if icon.startswith("dyn:"):
        parts = icon.split(":")
        family = parts[1] if len(parts) > 1 and parts[1] else "effect"
        try:
            variant = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            logger.debug("effect_browser: failed to parse dynamic icon variant, using stable seed")
            variant = _stable_visual_seed(icon)
        return family, variant

    fields = [
        getattr(preset, "id", ""),
        getattr(preset, "name", ""),
        getattr(preset, "category", ""),
        getattr(preset, "description", ""),
        " ".join(str(t) for t in (getattr(preset, "tags", []) or [])),
        " ".join(str(ch) for ch in (getattr(preset, "target_channels", []) or [])),
    ]
    marker = " ".join(str(x) for x in fields).lower()
    variant = _stable_visual_seed(marker)
    if "audio" in marker or "bpm" in marker:
        return "audio", variant
    if "strobe" in marker:
        return "strobe", variant
    if "color" in marker or "rgb" in marker or "r g b" in marker:
        return "color", variant
    if "light" in marker or "dimmer" in marker:
        if "chase" in marker:
            return "chase", variant
        return "light", variant
    if "motor" in marker or "joystick" in marker or "pan" in marker or "tilt" in marker:
        if "cue" in marker:
            return "cue", variant
        return "motor", variant
    if "pump" in marker:
        return "pump", variant
    if "valve" in marker:
        return "valve", variant
    if "matrix" in marker or "pattern" in marker:
        return "pattern", variant
    if "chase" in marker:
        return "chase", variant
    if "fader" in marker or "fade" in marker:
        return "fader", variant
    if "wave" in marker or "sine" in marker or "ripple" in marker:
        return "wave", variant
    if "pulse" in marker:
        return "pulse", variant
    return "effect", variant


def _preset_text_icon(preset: EffectPreset) -> str:
    icon = str(getattr(preset, "icon", "") or "").strip()
    if not icon or icon.startswith("dyn:"):
        return ""
    if "\ufffd" in icon or len(icon) > 4:
        return ""
    return icon


def _icon_color(seed: int, offset: int = 0, sat: int = 180, val: int = 245) -> QColor:
    return QColor.fromHsv((int(seed) * 37 + offset * 61) % 360, sat, val)


# ── 效果卡片视觉（delegate 与 PresetItemWidget 双路径共享，restyle_spec B）─────
CARD_ICON_SIZE = 16          # §10.5：卡片语义 tabler glyph 统一 16px


def _card_glyph_pixmap(icon_name: str, color: Optional[str] = None) -> QPixmap:
    """tabler 语义 glyph 位图；缺图时返回空 pixmap，由绘制方做兜底。"""
    try:
        # HiDPI：直接取 DPR 感知位图，避免 QIcon 中转时的再采样发虚
        from .main_icon_library import load_tabler_pixmap
        return load_tabler_pixmap(str(icon_name or ""), CARD_ICON_SIZE,
                                  color or _th.TEXT_PRIMARY)
    except Exception:
        logger.debug("effect_browser: failed to load card glyph %s", icon_name)
        return QPixmap()


# ── 收藏星标（★ R183 批 A）：绘制与命中同一几何真源 ────────────────────────
#: 星标边长（宽窄卡统一；落在卡片右下角、分类色点正下方）
STAR_PX = 14
#: 星标位图缓存：{已收藏: pixmap}（两种状态各一张，绘制热路径零重复解析）
_STAR_PIXMAPS: Dict[bool, QPixmap] = {}


def favorite_star_rect(card_rect: QRect) -> QRect:
    """卡片内星标的**命中/绘制**矩形（同一函数供 delegate 绘制与鼠标命中）。

    位置 = 卡片右下角内缩（x: body.right()-20 起 14px；y: body.bottom()-18 起
    14px）：避开标题/描述文本区与分类色点（顶部右侧），窄卡下也不压分类竖条。
    """
    body = card_rect.adjusted(3, 1, -4, -3)
    return QRect(int(body.right() - 20), int(body.bottom() - 18), STAR_PX, STAR_PX)


def _star_pixmap(on: bool) -> QPixmap:
    """星标位图（已收藏 = filled 金色 / 未收藏 = outline 次级灰）。"""
    cached = _STAR_PIXMAPS.get(bool(on))
    if cached is not None:
        return cached
    pix = QPixmap()
    try:
        from .main_icon_library import load_tabler_pixmap
        color = _th.KEYFRAME_GOLD if on else _th.TEXT_DISABLED
        pix = load_tabler_pixmap("star", STAR_PX, color)
    except Exception:
        logger.debug("effect_browser: failed to load favorite star glyph")
    _STAR_PIXMAPS[bool(on)] = pix
    return pix


def _star_polygon_path(rect: QRect) -> QPainterPath:
    """五角星封闭路径（内接于 rect；纯几何，无新依赖/无位图资源）。

    ★ 为什么自绘填充而不是再取一枚 tabler 图标：仓内图标加载器
      （`main_icon_library._tabler_svg_text`）按「outline 优先」解析，同名 `star`
      永远命中 outline 版（filled 仅在 outline 缺字形时才用）——再调一次拿不到
      实心字形，而 outline/filled 必须有可辨差异（收藏状态要一眼看出）。
      故：未收藏走 tabler outline 线稿（既有图标语言），已收藏走本函数实心填充。
    """
    cx = rect.left() + rect.width() / 2.0
    cy = rect.top() + rect.height() / 2.0
    outer = rect.width() / 2.0
    inner = outer * 0.42        # 内外半径比（常规五角星观感）
    path = QPainterPath()
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        ang = -math.pi / 2.0 + i * math.pi / 5.0
        pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def paint_favorite_star(painter: QPainter, card_rect: QRect, on: bool) -> None:
    """在卡片右下角画收藏星标（未收藏也画：入口必须始终可见可点）。

    已收藏 = 实心金（``KEYFRAME_GOLD``）；未收藏 = tabler outline 线稿灰
    （``TEXT_DISABLED``）。两态像素可辨，测试对绘制像素数做断言。
    """
    rect = favorite_star_rect(card_rect)
    if on:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(_th.KEYFRAME_GOLD), 1))
        painter.setBrush(QBrush(QColor(_th.KEYFRAME_GOLD)))
        painter.drawPath(_star_polygon_path(rect))
        painter.restore()
        return
    pix = _star_pixmap(False)
    if pix is None or pix.isNull():
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(_th.TEXT_DISABLED), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_star_polygon_path(rect))
        painter.restore()
        return
    painter.drawPixmap(rect.topLeft(), pix)


def _spatial_scope_is(params, want: str) -> bool:
    """`params["spatial_scope"]` 规范化后是否等于 `want`（比较口径单一真源）。"""
    from .effect_engine import normalize_spatial_scope
    return normalize_spatial_scope((params or {}).get("spatial_scope")) == want


def _effect_chain_params(panel, params, positions):
    """浏览器预览与实际计算复用方向镜像和编号/坐标相位合同。"""
    from .effect_order_execution import prepare_params
    from .effect_spatial_semantics import effective_params
    out = effective_params(str(getattr(panel, "_selected_id", "") or ""), params)
    return prepare_params(out, positions)


def _position_hits_star(card_rect: QRect, pos) -> bool:
    """`pos`（列表视口坐标）是否落在该卡片的星标上。"""
    try:
        return favorite_star_rect(card_rect).contains(int(pos.x()), int(pos.y()))
    except Exception:
        return False


def _row_is_favorite(index) -> bool:
    """模型行是否已收藏（★ R183 批 A；drawing 与命中判定都走这一条）。

    模型不是 `PresetListModel`（测试替身/未来换模型）→ 一律 False，绝不抛。
    """
    try:
        fn = getattr(index.model(), "row_is_favorite", None)
        return bool(fn(index.row())) if callable(fn) else False
    except Exception:
        return False


def paint_effect_card(painter: QPainter, rect: QRect, *, selected: bool, hover: bool,
                      glyph: QPixmap, name: str, desc: str,
                      cat_color: QColor, cat_text: str):
    """效果卡片统一绘制（两条渲染路径共用同一函数，保证状态视觉完全一致）。

    结构：
      - 底 BG_EDITOR + 1px rgba(255,255,255,17) 边 + 4px 圆角；
      - 选中 = select_blue 左 2px 竖条 + select_blue 半透明微亮底；
      - hover = bg_panel 抬升底；
      - 图标 16px tabler 语义 glyph（垂直居中）；
      - 标题 12px bold，次行 11px text_secondary；
      - 分类色只允许落在右侧 9px 圆点（§10.5 扁平规则）。
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = rect.adjusted(3, 1, -4, -3)

    base = QColor(_th.BG_PANEL if (hover and not selected) else _th.BG_EDITOR)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(base))
    painter.drawRoundedRect(body, 4, 4)
    if selected:
        tint = QColor(_th.SELECT_BLUE)
        tint.setAlpha(60)
        painter.setBrush(QBrush(tint))
        painter.drawRoundedRect(body, 4, 4)
    painter.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body.adjusted(0, 0, -1, -1), 4, 4)

    # ── 窄卡竖排分支（宽 <150px：左侧预设窄列）──
    # 固定偏移的宽卡布局（icon x+13 / 文本 x+44）在窄列会把标题压上图标，
    # 这里单行省略渲染，保证任意宽度零重叠。
    if body.width() < 150:
        gy = int(body.top() + (body.height() - CARD_ICON_SIZE) / 2)
        gx = int(body.left() + 7)
        if glyph is not None and not glyph.isNull():
            painter.drawPixmap(gx, gy, glyph)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(_th.TEXT_DISABLED)))
            painter.drawEllipse(QPointF(gx + CARD_ICON_SIZE / 2.0,
                                        body.top() + body.height() / 2.0), 4.5, 4.5)
        nf = _th.cached_font(11)
        painter.setFont(nf)
        tx = gx + CARD_ICON_SIZE + 5
        nrect = QRect(tx, body.top(),
                      max(24, body.right() - 10 - tx), body.height())
        painter.setPen(QColor(_th.TEXT_SELECTED_FG if selected else FG_MAIN))
        painter.drawText(nrect,
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         painter.fontMetrics().elidedText(
                             name, Qt.TextElideMode.ElideRight, nrect.width()))
        if cat_color is not None:
            painter.setPen(QPen(QColor(cat_color), 2))
            painter.drawLine(int(body.right() - 4), body.top() + 4,
                             int(body.right() - 4), body.bottom() - 4)
        painter.restore()
        return

    icon_cy = body.top() + body.height() / 2.0
    if selected:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(_th.SELECT_BLUE)))
        painter.drawRect(QRectF(body.left() + 1.5, body.top() + 3.0, 2.0,
                                body.height() - 6.0))

    gx = int(body.left() + 13)
    gy = int(icon_cy - CARD_ICON_SIZE / 2)
    if glyph is not None and not glyph.isNull():
        painter.drawPixmap(gx, gy, glyph)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(_th.TEXT_DISABLED)))
        painter.drawEllipse(QPointF(gx + CARD_ICON_SIZE / 2.0, icon_cy), 4.5, 4.5)

    text_x = body.left() + 44
    name_rect = QRect(text_x, body.top() + 13,
                      max(40, body.right() - 108 - text_x), 16)
    nf = _th.cached_font(12, True)
    painter.setFont(nf)
    painter.setPen(QColor(_th.TEXT_SELECTED_FG if selected else FG_MAIN))
    painter.drawText(name_rect,
                     int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                     painter.fontMetrics().elidedText(
                         name, Qt.TextElideMode.ElideRight, name_rect.width()))

    desc_rect = QRect(text_x, body.top() + 33,
                      max(40, body.right() - 100 - text_x), 15)
    df = _th.cached_font(11)
    painter.setFont(df)
    display_desc = desc[:48] + ("…" if len(desc) > 48 else "")
    painter.setPen(QColor(_th.TEXT_TERTIARY if selected else _th.TEXT_SECONDARY))
    painter.drawText(desc_rect,
                     int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                     painter.fontMetrics().elidedText(
                         display_desc, Qt.TextElideMode.ElideRight, desc_rect.width()))

    if cat_text:
        cat_f = _th.cached_font(11)
        painter.setFont(cat_f)
        cat_rect = QRect(body.right() - 86, name_rect.top(), 66, name_rect.height())
        painter.setPen(QColor(_th.TEXT_SECONDARY))
        painter.drawText(cat_rect,
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         painter.fontMetrics().elidedText(
                             str(cat_text), Qt.TextElideMode.ElideRight, cat_rect.width()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(cat_color))
        dot_cy = cat_rect.center().y()
        painter.drawEllipse(QPointF(body.right() - 10.5, dot_cy), 4.5, 4.5)
    painter.restore()


class EffectActionIcon(QWidget):
    """效果卡片语义 glyph（tabler 16px，双路径共享 _card_glyph_pixmap）。

    D 波重皮后不再使用 38px 彩虹 chip，改用语义单色字形直接落卡。
    """

    def __init__(self, preset: EffectPreset, parent=None):
        super().__init__(parent)
        self._preset = preset
        self._glyph = _card_glyph_pixmap(effect_icon_name_for_preset(preset))
        self.setFixedSize(CARD_ICON_SIZE + 8, CARD_ICON_SIZE + 8)
        self.setToolTip(str(getattr(preset, "name", "") or ""))

    def paintEvent(self, event):
        p = QPainter(self)
        if not self._glyph.isNull():
            x = (self.width() - CARD_ICON_SIZE) // 2
            y = (self.height() - CARD_ICON_SIZE) // 2
            p.drawPixmap(x, y, self._glyph)
            p.end()
            return
        # SVG 缺图兜底：中性小圆点占位（底由卡片层绘制）
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_th.TEXT_DISABLED)))
        c = (self.width() - 9) / 2.0
        p.drawEllipse(QRectF(c, c, 9, 9))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  预设列表项 Widget
# ─────────────────────────────────────────────────────────────────────────────

class PresetItemWidget(QWidget):
    """预设卡片（直接实例路径）。视觉完全复用 paint_effect_card，
    与 PresetItemDelegate（model/delegate 渲染路径）保持逐像素一致。"""

    clicked = pyqtSignal(str)  # preset_id

    def __init__(self, preset: EffectPreset, parent=None):
        super().__init__(parent)
        self._preset = preset
        self._selected = False
        self._hover = False
        self.setFixedHeight(68)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._glyph = _card_glyph_pixmap(effect_icon_name_for_preset(preset))
        # 无子控件：全部内容由 paint_effect_card 绘制，保证双路径一致（audit §4.7）
        self.set_selected(False)

    def _preset_cat(self) -> str:
        return normalize_effect_category(getattr(self._preset, "category", ""))

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_effect_card(
            painter, self.rect(),
            selected=self._selected, hover=self._hover,
            glyph=self._glyph,
            name=str(getattr(self._preset, "name", "") or ""),
            desc=str(getattr(self._preset, "description", "") or ""),
            cat_color=QColor(_get_category_color(self._preset.category)),
            cat_text=self._preset_cat())
        painter.end()

    def set_selected(self, sel: bool):
        self._selected = bool(sel)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, event):
        self.clicked.emit(self._preset.id)


# ─────────────────────────────────────────────────────────────────────────────
#  参数控件（根据 ParamSpec 自动生成）
# ─────────────────────────────────────────────────────────────────────────────

_LOW_EXPAND_KEYS = (
    "speed", "rate", "freq", "frequency", "phase_speed", "step_speed",
    "start_speed", "end_speed",
)
_GLOBAL_SPEED_KEYS = set()
_LOW_EXPAND_LABELS = ("速度", "频率")
_JOY_AMP_AXIS_PARAM = ParamSpec(
    "_joy_amp_axis",
    "摇杆绑定",
    "choice",
    "自动",
    choices=["自动", "X轴", "Y轴", "Z轴", "关闭"],
    tooltip="幅度控制模式下选择当前通道由摇杆 X/Y/Z 轴或自动规则控制。",
)
_JOY_AMP_OVERRIDE_KEYS = ("joy_amplitude_x", "joy_amplitude_y", "joy_amplitude_z")


def _is_low_expand_param(spec: ParamSpec) -> bool:
    key = str(getattr(spec, "key", "") or "").lower()
    label = str(getattr(spec, "label", "") or "")
    return (
        any(token in key for token in _LOW_EXPAND_KEYS)
        or any(token in label for token in _LOW_EXPAND_LABELS)
    )


def _is_global_speed_param(spec: ParamSpec) -> bool:
    return str(getattr(spec, "key", "") or "").lower() in _GLOBAL_SPEED_KEYS


def _is_channel_choice_param(spec: ParamSpec) -> bool:
    if str(getattr(spec, "type", "") or "") != "choice":
        return False
    key = str(getattr(spec, "key", "") or "").lower()
    if key not in {"ch_type", "ch", "channel", "target_channel"}:
        return False
    choices = [str(ch) for ch in (getattr(spec, "choices", []) or [])]
    return bool(choices) and all(ch in CH_TYPES for ch in choices)


def _float_ui_range(spec: ParamSpec) -> tuple[float, float]:
    lo = float(spec.min_val)
    hi = float(spec.max_val)
    if not _is_low_expand_param(spec):
        return lo, hi
    lo = min(lo, 0.01) if lo > 0.0 else min(lo, 0.0)
    if hi <= 8.0:
        hi = max(hi * 4.0, 8.0)
    elif hi <= 20.0:
        hi = hi * 2.0
    return lo, hi


def _float_to_slider_pct(value: float, lo: float, hi: float, expanded: bool) -> float:
    if hi <= lo:
        return 0.0
    value = max(lo, min(hi, float(value)))
    if expanded and lo > 0.0:
        pct = (math.log(value) - math.log(lo)) / max(1e-9, math.log(hi) - math.log(lo))
    else:
        pct = (value - lo) / max(1e-9, hi - lo)
        if expanded:
            pct = math.pow(max(0.0, min(1.0, pct)), 1.0 / 2.4)
    return max(0.0, min(1.0, pct))


def _slider_pct_to_float(pct: float, lo: float, hi: float, expanded: bool) -> float:
    pct = max(0.0, min(1.0, float(pct)))
    if expanded and lo > 0.0:
        return math.exp(math.log(lo) + pct * (math.log(hi) - math.log(lo)))
    if expanded:
        pct = math.pow(pct, 2.4)
    return lo + pct * (hi - lo)


def _is_numeric_param(spec: ParamSpec) -> bool:
    typ = str(getattr(spec, "type", "") or "").lower()
    if typ not in {"int", "float"}:
        return False
    try:
        float(getattr(spec, "min_val", 0.0))
        float(getattr(spec, "max_val", 0.0))
    except Exception:
        logger.debug("effect_browser: failed to validate param spec range values")
        return False
    return True


_RANGE_EXACT_KEY_PAIRS = {
    ("off_val", "on_val"),
    ("close_val", "open_val"),
    ("base", "peak"),
}

_RANGE_LOW_KEY_PREFIXES = ("min", "lo", "low", "lower")
_RANGE_HIGH_KEY_PREFIXES = ("max", "hi", "high", "upper")


def _range_key_role(spec: ParamSpec) -> Optional[tuple[str, str]]:
    key = str(getattr(spec, "key", "") or "").strip().lower()
    if not key:
        return None
    for prefix in _RANGE_LOW_KEY_PREFIXES:
        if key == prefix:
            return "low", ""
        if key.startswith(prefix + "_"):
            return "low", key[len(prefix) + 1:]
        if key.endswith("_" + prefix):
            return "low", key[:-(len(prefix) + 1)]
    for prefix in _RANGE_HIGH_KEY_PREFIXES:
        if key == prefix:
            return "high", ""
        if key.startswith(prefix + "_"):
            return "high", key[len(prefix) + 1:]
        if key.endswith("_" + prefix):
            return "high", key[:-(len(prefix) + 1)]
    return None


def _range_label_role(spec: ParamSpec) -> Optional[str]:
    label = str(getattr(spec, "label", "") or "")
    low_words = ("最小", "下限", "低值", "暗值", "基础", "关闭", "左边界", "下界")
    high_words = ("最大", "上限", "高值", "亮值", "峰值", "开启", "右边界", "上界")
    is_low = any(w in label for w in low_words)
    is_high = any(w in label for w in high_words)
    if is_low and not is_high:
        return "low"
    if is_high and not is_low:
        return "high"
    return None


def _is_range_param_pair(low_spec: ParamSpec, high_spec: ParamSpec) -> bool:
    if not (_is_numeric_param(low_spec) and _is_numeric_param(high_spec)):
        return False
    pair = (
        str(getattr(low_spec, "key", "") or "").lower(),
        str(getattr(high_spec, "key", "") or "").lower(),
    )
    if pair in _RANGE_EXACT_KEY_PAIRS:
        return True
    low_role = _range_key_role(low_spec)
    high_role = _range_key_role(high_spec)
    if (
        low_role is not None
        and high_role is not None
        and low_role[0] == "low"
        and high_role[0] == "high"
        and low_role[1] == high_role[1]
    ):
        return True
    return _range_label_role(low_spec) == "low" and _range_label_role(high_spec) == "high"


def _range_param_pair_specs(first: ParamSpec, second: Optional[ParamSpec]) -> Optional[tuple[ParamSpec, ParamSpec]]:
    if second is None:
        return None
    if _is_range_param_pair(first, second):
        return first, second
    if _is_range_param_pair(second, first):
        return second, first
    return None


class RangeSlider(QWidget):
    """Two-handle horizontal slider used for Effect numeric ranges."""

    value_changed = pyqtSignal(float, float)

    def __init__(self, minimum: float = 0.0, maximum: float = 255.0, parent=None):
        super().__init__(parent)
        self._min = float(minimum)
        self._max = max(float(maximum), self._min)
        self._low = self._min
        self._high = self._max
        self._active_handle = ""
        self._drag_start_x = 0.0
        self._drag_start_low = self._low
        self._drag_start_high = self._high
        self.setMinimumHeight(28)
        # 可拖拽 affordance（audit §4.6：视觉/命中提示补齐）
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动两端手柄调整范围；拖动中间色带整体平移")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def values(self) -> tuple[float, float]:
        return float(self._low), float(self._high)

    def set_values(self, low: float, high: float, emit: bool = False):
        low = max(self._min, min(self._max, float(low)))
        high = max(self._min, min(self._max, float(high)))
        if low > high:
            low, high = high, low
        changed = abs(low - self._low) > 1e-9 or abs(high - self._high) > 1e-9
        self._low, self._high = low, high
        self.update()
        if changed and emit:
            self.value_changed.emit(self._low, self._high)

    def _track_rect(self):
        margin = 9
        y = self.height() // 2
        return margin, y, max(1, self.width() - margin * 2)

    def _value_to_x(self, value: float) -> int:
        left, _y, width = self._track_rect()
        span = max(1e-9, self._max - self._min)
        return int(round(left + (float(value) - self._min) / span * width))

    def _x_to_value(self, x: float) -> float:
        left, _y, width = self._track_rect()
        pct = max(0.0, min(1.0, (float(x) - left) / max(1, width)))
        return self._min + pct * (self._max - self._min)

    def _event_x(self, event) -> float:
        try:
            return float(event.position().x())
        except Exception:
            logger.debug("effect_browser: failed to get event position().x(), trying event.x()")
            try:
                return float(event.x())
            except Exception:
                logger.debug("effect_browser: failed to get event.x(), returning 0.0")
                return 0.0

    def _set_handle_from_x(self, x: float):
        value = self._x_to_value(x)
        if self._active_handle == "low":
            self.set_values(min(value, self._high), self._high, emit=True)
        elif self._active_handle == "high":
            self.set_values(self._low, max(value, self._low), emit=True)

    def _value_delta_from_dx(self, dx: float) -> float:
        _left, _y, width = self._track_rect()
        span = max(1e-9, self._max - self._min)
        return float(dx) / max(1, width) * span

    def _set_band_from_x(self, x: float):
        delta = self._value_delta_from_dx(float(x) - self._drag_start_x)
        new_low = float(self._drag_start_low) + delta
        new_high = float(self._drag_start_high) + delta
        if new_low < self._min:
            low = self._min
            high = max(self._min, min(self._max, new_high))
        elif new_high > self._max:
            low = max(self._min, min(self._max, new_low))
            high = self._max
        else:
            low = new_low
            high = new_high
        self.set_values(low, high, emit=True)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = self._event_x(event)
        low_x = self._value_to_x(self._low)
        high_x = self._value_to_x(self._high)
        handle_radius = 10
        low_dist = abs(x - low_x)
        high_dist = abs(x - high_x)
        left_x, right_x = min(low_x, high_x), max(low_x, high_x)
        self._drag_start_x = x
        self._drag_start_low = self._low
        self._drag_start_high = self._high
        if low_dist <= handle_radius and low_dist <= high_dist:
            self._active_handle = "low"
            self._set_handle_from_x(x)
        elif high_dist <= handle_radius:
            self._active_handle = "high"
            self._set_handle_from_x(x)
        elif left_x <= x <= right_x:
            self._active_handle = "band"
        else:
            self._active_handle = "low" if low_dist <= high_dist else "high"
            self._set_handle_from_x(x)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._active_handle:
            return
        x = self._event_x(event)
        if self._active_handle == "band":
            self._set_band_from_x(x)
        else:
            self._set_handle_from_x(x)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._active_handle = ""
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left, y, width = self._track_rect()
        low_x = self._value_to_x(self._low)
        high_x = self._value_to_x(self._high)

        # 凹槽 bg_input 深底；选段/手柄走 select_blue→focus_blue 主色族
        painter.setPen(QPen(QColor(_th.BG_INPUT), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(left, y, left + width, y)
        painter.setPen(QPen(QColor(_th.SELECT_BLUE), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(low_x, y, high_x, y)
        painter.setPen(QPen(QColor(_th.POPUP_BORDER), 1))
        painter.setBrush(QBrush(QColor(_th.FOCUS_BLUE)))
        painter.drawEllipse(low_x - 6, y - 6, 12, 12)
        painter.drawEllipse(high_x - 6, y - 6, 12, 12)


class WingDirectionToolbar(QWidget):
    value_changed = pyqtSignal(str)

    def __init__(self, value: Any = WING_DIRECTION_LOW_TO_HIGH, parent=None):
        super().__init__(parent)
        self._value = normalize_wing_direction(value)
        self._buttons: List[QToolButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for mode in WING_DIRECTION_MODES:
            btn = QToolButton()
            btn.setText(WING_DIRECTION_SYMBOLS.get(mode, "/"))
            btn.setCheckable(True)
            btn.setProperty("wing_direction", mode)
            btn.setToolTip(WING_DIRECTION_LABELS.get(mode, mode))
            btn.setFixedSize(24, 22)
            btn.setStyleSheet(
                f"QToolButton{{background:{_th.BG_EDITOR};border:1px solid {_th.BORDER_NORMAL};"
                f"color:{_th.TEXT_PRIMARY};padding:0;border-radius:3px;"
                "font-size:11px;font-weight:bold;}"
                f"QToolButton:hover{{background:{_th.BG_BUTTON_HOVER};}}"
                f"QToolButton:checked{{background:{_th.SELECT_BLUE};"
                f"border-color:{_th.FOCUS_BLUE};color:{_th.TEXT_SELECTED_FG};}}"
            )
            btn.clicked.connect(lambda _checked=False, m=mode: self.set_value(m, emit=True))
            lay.addWidget(btn)
            self._buttons.append(btn)
        lay.addStretch()
        self._sync()

    def _sync(self):
        for btn in self._buttons:
            try:
                mode = normalize_wing_direction(btn.property("wing_direction"))
                btn.blockSignals(True)
                btn.setChecked(mode == self._value)
                btn.blockSignals(False)
            except Exception:
                logger.debug("effect_browser: failed to sync wing direction button state")
                continue

    def set_value(self, value: Any, emit: bool = False):
        normalized = normalize_wing_direction(value)
        changed = normalized != self._value
        self._value = normalized
        self._sync()
        if emit and changed:
            self.value_changed.emit(self._value)

    def value(self) -> str:
        return normalize_wing_direction(self._value)


# ─────────────────────────────────────────────────────────────────────────────
#  D 波参数面板标准件：NumSlider 语义适配 / 参数分组（restyle_spec §10）
# ─────────────────────────────────────────────────────────────────────────────

class _ParamNumSlider(_BkNumSlider):
    """带输出换算的 数值滑杆（滑槽上绘制的值 = 语义值 × 展示倍率）。

    - 展示层：归一化 0~1 百分比参数以 ``%`` 显示（内部展示值 = 原值×100），
      其余浮点按语义值直接显示（``x``/``s``/无后缀）。
    - 读写仍走 widget_kit.NumSlider 约定：拖拽时 Qt 发的是 QSlider 的 int 信号，
      语义值只能经 out_value()/set_out() 出入；valueChanged 上的 emit 由调用方
      做一次除法换算，参数数值语义与旧 QDoubleSpinBox 完全一致。
    """

    def __init__(self, spec: ParamSpec, display_scale: float = 1.0,
                 suffix: str = "", decimals: int = 2, parent=None):
        lo, hi = _float_ui_range(spec)
        super().__init__(
            minimum=lo * display_scale, maximum=hi * display_scale,
            value=float(spec.default) * display_scale,
            decimals=max(0, int(decimals)), suffix=suffix, parent=parent)
        self._spec_key = str(getattr(spec, "key", "") or "")
        self._out_scale = float(display_scale)
        self._legacy_width = None
        self._restoring_width = False
        self.valueChanged.connect(self._clear_legacy_width)

    def _clear_legacy_width(self, _value):
        if not self._restoring_width:
            self._legacy_width = None

    def out_value(self) -> float:
        """滑槽展示值 → 语义参数值。"""
        if self._legacy_width is not None:
            return self._legacy_width
        return self.float_value() / self._out_scale

    def set_out(self, v: float):
        """语义参数值 → 滑槽展示值。"""
        legacy = self._spec_key == "effect_width" and float(v) > 1.0
        self._legacy_width = float(v) if legacy else None
        self._restoring_width = True
        try:
            self.set_float((1.0 if legacy else float(v)) * self._out_scale)
        finally:
            self._restoring_width = False


#: 按数值范围等比换算成展示空间的 QSS 无关小数位（步进决定辨识度）。
_NUM_X_TOKENS = ("speed", "rate", "factor", "multiplier", "velocity",
                 "速度", "倍率", "速率")
_NUM_S_TOKENS = ("time", "duration", "decay", "attack", "release", "hold",
                 "fade", "delay", "period", "interval", "timeout",
                 "时长", "周期", "延迟", "秒", "过渡")


def _param_display_semantics(spec: ParamSpec) -> tuple[str, float]:
    """返回 (后缀, 展示倍率)。unit 显式声明优先；再按 key/label 语义词推断
    ``x``（倍率）/``s``（时间）；归一化 0~1 浮点按百分比展示（×100）。
    纯展示映射，不改变任何参数数值语义。"""
    if spec.key == "effect_width":
        return "", 1.0
    unit = str(getattr(spec, "unit", "") or "").strip()
    if unit in {"%", "％"}:
        return "%", 100.0
    if unit == "x":
        return "x", 1.0
    if unit == "s":
        return "s", 1.0
    if str(getattr(spec, "type", "") or "").lower() != "float":
        return "", 1.0
    probe = f"{getattr(spec, 'key', '')} {getattr(spec, 'label', '')}".lower()
    if any(t in probe for t in _NUM_X_TOKENS):
        return "x", 1.0
    if any(t in probe for t in _NUM_S_TOKENS):
        return "s", 1.0
    return _normalized_param_display(spec)


def _normalized_param_display(spec: ParamSpec) -> tuple[str, float]:
    """Fallback display scale for a numeric range without an explicit unit."""
    try:
        hi = float(getattr(spec, "max_val", 1.0))
        lo = float(getattr(spec, "min_val", 0.0))
    except Exception:
        logger.debug("effect_browser: failed to read param range for display semantics")
        return "", 1.0
    if -1e-6 <= lo and hi <= 1.000001:
        return "%", 100.0
    return "", 1.0


def _make_param_num_slider(spec: ParamSpec, parent=None) -> _ParamNumSlider:
    """按 ParamSpec 构建 极简数值滑杆（suffix/decimals 语义自动判定）。"""
    suffix, scale = _param_display_semantics(spec)
    try:
        step = abs(float(getattr(spec, "step", 0.01) or 0.01))
    except Exception:
        logger.debug("effect_browser: failed to read step from spec, using default 0.01")
        step = 0.01
    disp_step = max(1e-9, step * scale)
    decimals = 0
    while disp_step < 0.99 and decimals < 4:
        disp_step *= 10.0
        decimals += 1
    return _ParamNumSlider(spec, display_scale=scale, suffix=suffix,
                           decimals=decimals, parent=parent)


# ── 成组参数分区归类（restyle_spec §10.2：PanelSection 折叠分区）──────────────
_PARAM_GROUP_ORDER = ("基础", "颜色", "空间", "时序", "音频")

#: 调度台用：记录每个 effect 面板实例上次上报过的窗格身份键（id(panel) → set）。
#: 用于「本面板窗格减少时删掉消失的键」，同时避免误删其它面板/实例的窗格。
#: 用 id() 作键在这里是安全的：只做增删同步、不做跨对象身份判定；面板实例
#: 生命周期与主窗口一致，且集合每轮按当前 keys 整体替换（不会无限增长）。

_PARAM_GROUP_RULES = (
    ("音频", ("audio", "bpm", "beat", "music", "spectrum", "band", "vu",
              "频谱", "频段", "音频", "节拍", "灵敏度", "音乐")),
    ("颜色", ("color", "hue", "saturation", "rgb", "rainbow", "palette",
              "swatch", "colortemp", "色相", "饱和", "颜色", "色彩", "色调",
              "色温", "色轮")),
    ("时序", ("speed", "rate", "freq", "frequency", "phase", "sync", "gate",
              "period", "interval", "duration", "fade", "attack", "decay",
              "release", "hold", "delay", "tempo",
              # ★ R165：子集三参数必须**同区** —— 它们是同一组件的三个字段。
              #   只让 delay_phase 靠"相位/延迟"进时序、另两个掉进"基础"，会让用户
              #   翻两个折叠区才能配完一组，等于把新功能的入口藏了一半。
              "subset",
              "速度", "速率", "频率", "相位", "周期", "时长", "延迟", "淡入",
              "淡出", "衰减", "节奏", "时序", "子集")),
    ("空间", ("width", "size", "spread", "position", "offset", "angle",
              "rotation", "direction", "center", "radius", "spatial",
              "mirror", "wing", "block", "布局", "空间", "扩散", "角度",
              "旋转", "位置", "偏移", "尺寸", "宽度", "翼", "分块")),
)


def _param_group_of(spec: ParamSpec) -> str:
    """把 ParamSpec 归入 颜色/空间/时序/音频/基础 五个折叠分区之一。

    纯呈现分组：仅决定该行参数落在哪个 PanelSection，不影响任何读写逻辑。
    """
    probe = f"{getattr(spec, 'key', '')} {getattr(spec, 'label', '')}".lower()
    for group, tokens in _PARAM_GROUP_RULES:
        if any(t in probe for t in tokens):
            return group
    return "基础"


class RangeParamWidget(QWidget):
    value_changed = pyqtSignal(str, object)

    def __init__(self, low_spec: ParamSpec, high_spec: ParamSpec, parent=None):

        super().__init__(parent)
        self._low_spec = low_spec
        self._high_spec = high_spec
        self._range = None
        self._low_spin = None
        self._high_spin = None
        self._is_float = (
            str(getattr(low_spec, "type", "") or "").lower() == "float"
            or str(getattr(high_spec, "type", "") or "").lower() == "float"
        )
        self._build()

    def _decimals_for_spec(self, spec: ParamSpec) -> int:
        try:
            step = abs(float(getattr(spec, "step", 0.01) or 0.01))
        except Exception:
            logger.debug("effect_browser: failed to read step from spec, using default 0.01")
            step = 0.01
        decimals = 0
        while step < 1.0 and decimals < 6:
            step *= 10.0
            decimals += 1
        return max(2, decimals)

    def _number_attr(self, spec: ParamSpec, name: str, default: float) -> float:
        value = getattr(spec, name, default)
        if value is None:
            return float(default)
        try:
            return float(value)
        except Exception:
            logger.debug("effect_browser: failed to cast number attr %s to float", name)
            return float(default)

    def _cast_value(self, spec: ParamSpec, value: Any) -> Any:
        typ = str(getattr(spec, "type", "") or "").lower()
        try:
            raw = float(value)
        except Exception:
            logger.debug("effect_browser: failed to cast value to float for spec %s", getattr(spec, "key", "?"))
            raw = self._number_attr(spec, "default", 0.0)
        lo = self._number_attr(spec, "min_val", raw)
        hi = max(lo, self._number_attr(spec, "max_val", raw))
        raw = max(lo, min(hi, raw))
        if typ == "int":
            return int(round(raw))
        return float(raw)

    def _spin_value(self, spin, spec: ParamSpec) -> Any:
        return self._cast_value(spec, spin.value())

    def _make_spin(self, spec: ParamSpec):
        typ = str(getattr(spec, "type", "") or "").lower()
        if typ == "int":
            spin = QSpinBox()
            lo = int(round(self._number_attr(spec, "min_val", 0.0)))
            hi = max(lo, int(round(self._number_attr(spec, "max_val", 255.0))))
            spin.setRange(lo, hi)
            spin.setSingleStep(max(1, int(round(self._number_attr(spec, "step", 1.0)))))
            spin.setFixedWidth(58)
            spin.setValue(int(self._cast_value(spec, getattr(spec, "default", 0))))
        else:
            spin = QDoubleSpinBox()
            decimals = self._decimals_for_spec(spec)
            spin.setDecimals(decimals)
            lo = self._number_attr(spec, "min_val", 0.0)
            hi = max(lo, self._number_attr(spec, "max_val", 1.0))
            spin.setRange(lo, hi)
            spin.setSingleStep(max(10 ** -decimals, self._number_attr(spec, "step", 0.01)))
            spin.setFixedWidth(84 if decimals > 2 else 72)
            spin.setValue(float(self._cast_value(spec, getattr(spec, "default", 0.0))))
        spin.setStyleSheet(_SPIN)
        return spin

    def _sync_spins_from_values(self, low: Any, high: Any):
        return sync_pair_spins(self, low, high)

    def _set_pair_values(self, low: Any, high: Any, emit: bool = False):
        low = self._cast_value(self._low_spec, low)
        high = self._cast_value(self._high_spec, high)
        self._range.set_values(float(low), float(high), emit=False)
        low, high = self._range.values()
        low, high = self._sync_spins_from_values(low, high)
        self._range.set_values(float(low), float(high), emit=False)
        if emit:
            self.value_changed.emit(self._low_spec.key, low)
            self.value_changed.emit(self._high_spec.key, high)

    def _build(self):
        # 属性行版式对齐 widget_kit.prop_row（标签 11px text_secondary 右对齐，
        # 左右 8px 边距、行内间距 8），范围条为低/高成对参数的复合控件。
        lay = QHBoxLayout(self)
        lay.setContentsMargins(_BK_PAD_M, 2, _BK_PAD_M, 2)
        lay.setSpacing(_BK_PAD_M)
        label = QLabel("范围:")
        label.setMinimumWidth(96)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:11px;background:transparent;")
        lay.addWidget(label)

        minimum = min(
            self._number_attr(self._low_spec, "min_val", 0.0),
            self._number_attr(self._high_spec, "min_val", 0.0),
        )
        maximum = max(
            self._number_attr(self._low_spec, "max_val", 255.0),
            self._number_attr(self._high_spec, "max_val", 255.0),
        )
        slider = RangeSlider(minimum, maximum)
        low_spin = self._make_spin(self._low_spec)
        high_spin = self._make_spin(self._high_spec)
        slider.set_values(low_spin.value(), high_spin.value())

        def _from_slider(low: float, high: float):
            self._set_pair_values(low, high, emit=True)

        def _from_low(value):
            self._set_pair_values(value, high_spin.value(), emit=True)

        def _from_high(value):
            self._set_pair_values(low_spin.value(), value, emit=True)

        slider.value_changed.connect(_from_slider)
        low_spin.valueChanged.connect(_from_low)
        high_spin.valueChanged.connect(_from_high)
        lay.addWidget(low_spin)
        lay.addWidget(slider, 1)
        lay.addWidget(high_spin)
        self._range = slider
        self._low_spin = low_spin
        self._high_spin = high_spin
        self._set_pair_values(low_spin.value(), high_spin.value(), emit=False)

    def get_values(self) -> Dict[str, Any]:
        return {
            self._low_spec.key: self._spin_value(self._low_spin, self._low_spec),
            self._high_spec.key: self._spin_value(self._high_spin, self._high_spec),
        }

    def get_value(self) -> Any:
        return self.get_values().get(self._low_spec.key, self._cast_value(self._low_spec, self._low_spec.default))

    def set_param_value(self, key: str, value: Any):
        values = self.get_values()
        low = values.get(self._low_spec.key, self._low_spec.default)
        high = values.get(self._high_spec.key, self._high_spec.default)
        if str(key) == str(self._low_spec.key):
            low = value
        elif str(key) == str(self._high_spec.key):
            high = value
        self._set_pair_values(low, high, emit=False)


class ParamWidget(QWidget):
    """单个参数的编辑控件，支持 float/int/bool/choice/pattern"""

    value_changed = pyqtSignal(str, object)  # key, value
    # ★ 摇杆绑定请求（channel, key, label）— EffectBrowser 统一处理绑定流程
    bind_requested = pyqtSignal(str, str, str)
    # ★ 右键管理菜单请求（channel, key, label）
    bind_menu_requested = pyqtSignal(str, str, str)

    def __init__(self, spec: ParamSpec, parent=None, project=None):
        super().__init__(parent)
        self._spec = spec
        self._project = project      # 工程对象（通道下拉补工程通道用；可 None）
        self._ctrl = None
        self._channel_values: List[str] = []
        self._channel_combo = None
        self._channel_list = None
        self._bind_channel: str = ""   # 通道表单上下文（每通道页独立绑定）
        self._bind_state: bool = False
        self._build()
        # ★ 摇杆绑定指示器（float/int 滑块参数；图标走 make_main_icon 统一图标库）
        if str(getattr(self._spec, "type", "") or "") in ("float", "int"):
            self._bind_ind = QLabel("")
            self._bind_ind.setFixedSize(18, 18)
            self._bind_ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._bind_ind.setCursor(Qt.CursorShape.PointingHandCursor)
            self._bind_ind.setToolTip("摇杆绑定：左键绑定/调整，右键管理")
            self._bind_ind.mousePressEvent = self._on_bind_ind_click
            self._body_lay.addWidget(self._bind_ind)

    def set_bind_channel(self, channel: str):
        """设置通道表单上下文（每通道页独立绑定）。"""
        self._bind_channel = str(channel or "")

    def refresh_bind_indicator(self, mgr):
        """刷新绑定指示器状态（joystick_device 图标或空）。"""
        if not hasattr(self, "_bind_ind") or mgr is None:
            return
        bound = mgr.has_effect_param_binding(self._bind_channel, self._spec.key)
        self._bind_state = bool(bound)
        if bound:
            try:
                from .main_icon_library import make_main_icon
                self._bind_ind.setPixmap(
                    make_main_icon("joystick_device", 14).pixmap(14, 14))
            except Exception:
                self._bind_ind.setText("")
            r = mgr.get_effect_param_binding(self._bind_channel, self._spec.key)
            tip = f"已绑定：{r.physical_axis.upper()}轴"
            if r.curve and r.curve != "linear":
                tip += f"，曲线 {r.curve}"
            self._bind_ind.setToolTip(
                f"{self._spec.label} 摇杆绑定\n{tip}\n— 左键调整，右键管理")
        else:
            self._bind_ind.setPixmap(QPixmap())
            self._bind_ind.setToolTip(
                f"{self._spec.label} 摇杆绑定\n— 左键绑定，右键管理")

    def _on_bind_ind_click(self, event):
        _ch = self._bind_channel
        _k = self._spec.key
        _lbl = str(getattr(self._spec, "label", "") or self._spec.key)
        if event.button() == Qt.MouseButton.LeftButton:
            self.bind_requested.emit(_ch, _k, _lbl)
        elif event.button() == Qt.MouseButton.RightButton:
            self.bind_menu_requested.emit(_ch, _k, _lbl)

    def apply_external_value(self, v255: int):
        """摇杆绑定值（0-255 归一）应用到本参数滑块（等效用户拖动滑块）。"""
        if self._spec.type not in ("float", "int") or self._ctrl is None:
            return
        lo = float(getattr(self._spec, "min_val", 0.0) or 0.0)
        hi = float(getattr(self._spec, "max_val", 255.0) or 255.0)
        if hi <= lo:
            return
        v255 = max(0, min(255, int(v255)))
        v = lo + (v255 / 255.0) * (hi - lo)
        if self._spec.type == "float":
            if isinstance(self._ctrl, _ParamNumSlider):
                self._ctrl.set_out(float(v))     # 数值滑杆 语义值入口
            else:
                self._ctrl.setValue(float(v))
        else:
            self._ctrl.setValue(int(round(v)))

    def _set_channel_values(self, values: list):
        allowed = [str(ch) for ch in (getattr(self._spec, "choices", []) or [])]
        allowed_set = set(allowed)
        out = []
        for value in values or []:
            ch = str(value)
            if ch in allowed_set and ch not in out:
                out.append(ch)
        if not out:
            default = str(getattr(self._spec, "default", "") or "")
            if default in allowed_set:
                out = [default]
            elif allowed:
                out = [allowed[0]]
        self._channel_values = out
        self._refresh_channel_list()

    def _refresh_channel_list(self):
        lst = self._channel_list
        if lst is None:
            return
        lst.blockSignals(True)
        lst.clear()
        for ch in self._channel_values:
            item = QListWidgetItem(ch)
            item.setData(Qt.ItemDataRole.UserRole, ch)
            lst.addItem(item)
        lst.blockSignals(False)
        try:
            lst.setFixedHeight(max(26, min(96, 24 * max(1, len(self._channel_values)) + 4)))
        except Exception:
            logger.debug("effect_browser: failed to set channel list height")
            pass

    def _emit_channel_values(self):
        self.value_changed.emit(self._spec.key, list(self._channel_values))

    def _add_channel_value(self):
        combo = self._channel_combo
        if combo is None:
            return
        val = combo.currentData()
        if val is None:
            val = combo.currentText()
        ch = str(val or "")
        if ch and ch not in self._channel_values:
            self._channel_values.append(ch)
            self._refresh_channel_list()
            self._emit_channel_values()

    def _remove_channel_value(self):
        lst = self._channel_list
        if lst is None:
            return
        rows = sorted({idx.row() for idx in lst.selectedIndexes()}, reverse=True)
        if not rows and self._channel_values:
            rows = [len(self._channel_values) - 1]
        changed = False
        for row in rows:
            if 0 <= row < len(self._channel_values):
                self._channel_values.pop(row)
                changed = True
        if not self._channel_values:
            self._set_channel_values([])
            self._emit_channel_values()
            return
        if changed:
            self._refresh_channel_list()
            self._emit_channel_values()

    def _build(self):
        # ── 属性行（restyle_spec §10.1：widget_kit.prop_row 版式）─────────
        # 标签 11px text_secondary 右对齐（宽 96），右侧控件+单位+绑定指示器。
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        blay = QHBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        self._body_lay = blay

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(_bk_prop_row(
            str(getattr(self._spec, "label", "") or ""), body, label_width=96))

        if str(getattr(self._spec, "key", "") or "") in {"wing_direction", "local_wing_direction"}:
            toolbar = WingDirectionToolbar(getattr(self._spec, "default", WING_DIRECTION_LOW_TO_HIGH))
            toolbar.value_changed.connect(lambda v: self.value_changed.emit(self._spec.key, v))
            blay.addWidget(toolbar, 1)
            self._ctrl = toolbar

        elif self._spec.type in ("float",):
            # 极简 num-slider：值绘在滑槽上、右键弹数值输入（§10.3）。
            # 语义后缀 %/x/s 由展示映射判定；emit 的仍是实际参数值，
            # 与旧 QSlider+QDoubleSpinBox 组合的读写语义一致。
            slider = _make_param_num_slider(self._spec, parent=self)
            slider.setToolTip(f"{self._spec.label}：左右拖动调值；右键（或双击区右键）输入精确数值")
            slider.valueChanged.connect(
                lambda _iv, s=slider, spec=self._spec: self.value_changed.emit(
                    spec.key, s.out_value()))
            blay.addWidget(slider, 1)
            self._ctrl = slider

        elif self._spec.type == "int":
            spin = QSpinBox()
            spin.setRange(int(self._spec.min_val), int(self._spec.max_val))
            spin.setValue(int(self._spec.default))
            spin.setSingleStep(max(1, int(self._spec.step)))
            spin.setStyleSheet(_SPIN)
            spin.valueChanged.connect(lambda v: self.value_changed.emit(self._spec.key, v))
            blay.addWidget(spin, 1)
            self._ctrl = spin

        elif self._spec.type == "bool":
            chk = QCheckBox()
            chk.setChecked(bool(self._spec.default))
            chk.setStyleSheet(_CHECK)
            chk.stateChanged.connect(lambda s: self.value_changed.emit(
                self._spec.key, s == Qt.CheckState.Checked.value))
            blay.addWidget(chk)
            blay.addStretch()
            self._ctrl = chk

        elif _is_channel_choice_param(self._spec):
            holder = QWidget()
            holder_lay = QVBoxLayout(holder)
            holder_lay.setContentsMargins(0, 0, 0, 0)
            holder_lay.setSpacing(4)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            combo = QComboBox()
            combo.setStyleSheet(_COMBO)
            _fill_pw_channel_combo(combo, self)     # 内置 + 工程通道（真名）
            add_btn = QToolButton()
            add_btn.setText("+")
            add_btn.setFixedSize(24, 22)
            add_btn.setStyleSheet(_BTN)
            add_btn.clicked.connect(self._add_channel_value)
            del_btn = QToolButton()
            del_btn.setText("-")
            del_btn.setFixedSize(24, 22)
            del_btn.setStyleSheet(_BTN)
            del_btn.clicked.connect(self._remove_channel_value)
            row.addWidget(combo, 1)
            row.addWidget(add_btn)
            row.addWidget(del_btn)
            holder_lay.addLayout(row)

            lst = QListWidget()
            lst.setStyleSheet(
                f"QListWidget{{background:{BG_HEADER};color:{FG_MAIN};"
                "border:1px solid #3D3D3D;border-radius:3px;}"
                f"QListWidget::item{{padding:2px 6px;}}"
                f"QListWidget::item:selected{{background:{BG_ITEM_SEL};}}")
            lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
            holder_lay.addWidget(lst)
            blay.addWidget(holder, 1)
            self._channel_combo = combo
            self._channel_list = lst
            self._ctrl = lst
            self._set_channel_values([self._spec.default])

        elif self._spec.type == "choice":
            combo = QComboBox()
            combo.setStyleSheet(_COMBO)
            for ch in self._spec.choices:
                combo.addItem(ch)
            if self._spec.default in self._spec.choices:
                combo.setCurrentText(str(self._spec.default))
            combo.currentTextChanged.connect(
                lambda v: self.value_changed.emit(self._spec.key, v))
            blay.addWidget(combo, 1)
            self._ctrl = combo

        elif self._spec.type == "pattern":
            combo = QComboBox()
            combo.setStyleSheet(_COMBO)
            eng = get_engine()
            for pid, pat in eng.all_patterns().items():
                combo.addItem(pat.name, pid)
            combo.currentIndexChanged.connect(
                lambda i, c=combo: self.value_changed.emit(
                    self._spec.key, c.currentData()))
            blay.addWidget(combo, 1)
            self._ctrl = combo

        if self._spec.unit:
            unit_text = str(self._spec.unit).strip()
            slider_suffix = ""
            if isinstance(self._ctrl, _ParamNumSlider):
                slider_suffix = getattr(self._ctrl, "_suffix", "")
            if unit_text != slider_suffix:
                unit_lbl = QLabel(unit_text)
                unit_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
                blay.addWidget(unit_lbl)

    def get_value(self) -> Any:
        if self._ctrl is None:
            return self._spec.default
        if isinstance(self._ctrl, _ParamNumSlider):
            return self._ctrl.out_value()          # 滑槽展示值 → 语义参数值
        if isinstance(self._ctrl, QDoubleSpinBox):
            return self._ctrl.value()
        if isinstance(self._ctrl, QSpinBox):
            return self._ctrl.value()
        if isinstance(self._ctrl, QCheckBox):
            return self._ctrl.isChecked()
        if isinstance(self._ctrl, QListWidget):
            return list(self._channel_values)
        if isinstance(self._ctrl, WingDirectionToolbar):
            return self._ctrl.value()
        if isinstance(self._ctrl, QComboBox):
            d = self._ctrl.currentData()
            return d if d is not None else self._ctrl.currentText()
        return self._spec.default



# ── Virtual Scrolling: Model + Delegate ──────────────────────────────────────
# Custom data roles for the preset list model.
_ItemTypeRole = Qt.ItemDataRole.UserRole + 100       # "header" or "item"
_CategoryNameRole = Qt.ItemDataRole.UserRole + 101    # category name (header & item)
_CategoryCountRole = Qt.ItemDataRole.UserRole + 102   # preset count for header rows
_CategoryColorRole = Qt.ItemDataRole.UserRole + 103   # QColor for category accent
_DescriptionRole = Qt.ItemDataRole.UserRole + 104     # preset description


class PresetListModel(QAbstractListModel):
    """Flat list model of category headers + preset items.  Each row is either a
    header tuple ('header', cat_name, count) or an item tuple ('item', preset).

    The delegate reads custom roles to paint identically to the old PresetItemWidget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flat_items: list = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._flat_items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._flat_items):
            return None
        entry = self._flat_items[index.row()]
        typ = entry[0]

        if role == _ItemTypeRole:
            return typ

        if typ == "header":
            if role == _CategoryNameRole:
                return entry[1]
            if role == _CategoryCountRole:
                return entry[2]
            if role == _CategoryColorRole:
                return QColor(_get_category_color(entry[1]))
            if role == Qt.ItemDataRole.DisplayRole:
                return f"{entry[1]} ({entry[2]})"
        else:
            preset = entry[1]
            if role == Qt.ItemDataRole.DisplayRole:
                return _catalog.display_name(preset)
            if role == Qt.ItemDataRole.UserRole:
                return preset.id
            if role == _DescriptionRole:
                return _catalog.display_description(preset)
            if role == _CategoryNameRole:
                return _catalog.category_for(preset)
            if role == _CategoryColorRole:
                return QColor(_get_category_color(_catalog.category_for(preset)))
            if role == Qt.ItemDataRole.DecorationRole:
                return self._make_icon(preset)
        return None

    def row_is_favorite(self, row: int) -> bool:
        """该行预设是否已收藏（★ R183 批 A；绘制星标用）。

        ★ 为什么不做成 role：`data()` 在门禁基线里是 33 行 / CC 15（已越阈值），
          加任何分支都算"债务恶化"→ ERROR。星标是视图侧查询，走本方法等价。
        """
        try:
            entry = self._flat_items[int(row)]
        except (IndexError, TypeError, ValueError):
            return False
        return entry[0] == "item" and bool(_fxstore.is_favorite(entry[1].id))

    @staticmethod
    def _make_icon(preset: EffectPreset):
        # §10.5：列表卡片统一 16px tabler 语义 glyph（与 PresetItemWidget 同源）
        return QIcon(_card_glyph_pixmap(effect_icon_name_for_preset(preset)))

    def set_presets(self, presets: list, group_fn=None, sort_key=None):
        """Replace all items with a new grouped flat list.

        ★ R180 两级分类：``group_fn(preset) -> 分组头名`` 与 ``sort_key(头名)``
        可注入；"全部"视图用一级大类做头（默认不传时 = 旧行为：按规范细分类
        分组 + effect_category_sort_key 排序，其它调用方不受影响）。
        """
        self.beginResetModel()
        self._flat_items = []
        grouped: dict = {}
        if group_fn is None:
            group_fn = lambda p: normalize_effect_category(p.category)
            if sort_key is None:
                sort_key = effect_category_sort_key
        for p in presets:
            grouped.setdefault(group_fn(p), []).append(p)
        cats_order = (sorted(grouped.keys(), key=sort_key)
                      if sort_key is not None else list(grouped.keys()))
        for cat_name in cats_order:
            self._flat_items.append(("header", cat_name, len(grouped[cat_name])))
            for p in grouped[cat_name]:
                self._flat_items.append(("item", p))
        self.endResetModel()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        entry = self._flat_items[index.row()]
        if entry[0] == "header":
            # Headers are enabled (so they paint) but not selectable.
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled


class PresetItemDelegate(QStyledItemDelegate):
    """Paints category headers and preset items to match the PresetItemWidget
    look exactly — 卡片底/边框/glyph/双行文字全部复用 paint_effect_card，
    两条渲染路径（model+delegate / 直接实例 widget）视觉一致。"""

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        item_type = index.data(_ItemTypeRole)
        if item_type == "header":
            self._paint_header(painter, option, index)
        else:
            self._paint_item(painter, option, index)

        painter.restore()

    # ── header：扁平分区头（左 2px 分类竖条 + 弱化文字，分类色仅竖条）──────

    def _paint_header(self, painter, option, index):
        rect = option.rect
        cat_color: QColor = index.data(_CategoryColorRole)

        painter.fillRect(rect, QColor(_th.BG_ROOT))

        # left accent border（2px，§10.5 允许的分类色落点）
        pen = QPen(cat_color, 2)
        painter.setPen(pen)
        y0 = rect.top() + 3
        painter.drawLine(QPoint(rect.left() + 1, y0), QPoint(rect.left() + 1, rect.bottom() - 3))

        name = index.data(_CategoryNameRole) or ""
        count = index.data(_CategoryCountRole) or 0
        text = f" {name} · {count}"

        fnt = QFont()
        fnt.setPixelSize(11)
        fnt.setBold(False)
        painter.setFont(fnt)
        painter.setPen(QColor(_th.TEXT_SECONDARY))
        painter.drawText(rect.adjusted(10, 0, 0, 0),
                         int(Qt.AlignmentFlag.AlignVCenter), text)

    # ── item：完整卡片绘制委托给共享 paint_effect_card ─────────────────────

    def _paint_item(self, painter, option, index):
        rect = option.rect
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        glyph = QPixmap()
        if icon is not None:
            glyph = icon.pixmap(CARD_ICON_SIZE, CARD_ICON_SIZE)

        paint_effect_card(
            painter, rect,
            selected=is_selected, hover=is_hover,
            glyph=glyph,
            name=index.data(Qt.ItemDataRole.DisplayRole) or "",
            desc=index.data(_DescriptionRole) or "",
            cat_color=index.data(_CategoryColorRole) or QColor(_th.TEXT_SECONDARY),
            cat_text=index.data(_CategoryNameRole) or "")
        # ★ R183 批 A：收藏星标画在卡片上（布局常量与命中判定同源
        #   `favorite_star_rect`；paint_effect_card 本体在基线内零余量，不动）
        paint_favorite_star(painter, rect, _row_is_favorite(index))

    def sizeHint(self, option, index: QModelIndex):
        if index.data(_ItemTypeRole) == "header":
            return QSize(0, 22)
        return QSize(0, 68)


class PresetListView(QListView):
    """预设列表视图：卡片星标点击 + 右键菜单（★ R183 批 A）。

    为什么不写进 `EffectBrowserWidget`：该类是冻结上帝类（CLAUDE.md §10.1，
    门禁断言方法数不增长）。星标命中与右键菜单都是**视图**职责，落在独立子类里
    用信号上行，上帝类只多两行接线（`_build_ui` 内 connect）。
    """

    #: 卡片星标被点击：携带 preset_id（已收藏↔未收藏由面板侧切换）
    starClicked = pyqtSignal(str)
    #: 卡片请求右键菜单：携带 preset_id 与视口坐标
    menuRequested = pyqtSignal(str, QPoint)

    def _item_id_at(self, pos) -> str:
        """pos（视口坐标）处的预设 id；命中标题行/空白 → ""。"""
        try:
            idx = self.indexAt(pos)
            if not idx.isValid() or idx.data(_ItemTypeRole) != "item":
                return ""
            return str(idx.data(Qt.ItemDataRole.UserRole) or "")
        except Exception:
            return ""

    def mousePressEvent(self, event):
        """左键落在星标上 → 只切收藏，不改变当前选中（避免"点星误切预设"）。

        星标有效性用 `visualRect` 取该行真实矩形（滚动/分组头偏移自动跟随）。
        """
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                pid = self._item_id_at(pos)
                if pid:
                    idx = self.indexAt(pos)
                    if _position_hits_star(self.visualRect(idx), pos):
                        self.starClicked.emit(pid)
                        event.accept()
                        return
        except Exception:
            logger.debug("effect_browser: favorite star hit test failed", exc_info=True)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """右键 → 该预设的操作菜单（另存/收藏/重命名/删除）。"""
        pid = self._item_id_at(event.pos())
        if not pid:
            super().contextMenuEvent(event)
            return
        self.menuRequested.emit(pid, event.globalPos())
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  效果预设浏览器主 Widget
# ─────────────────────────────────────────────────────────────────────────────

def _effect_node(ctx, st) -> "EffectNode":
    """构造 effect 面板的一格节点。`ctx` = (track_type, group_key, group_name,
    group_ids, base_ids, preset_id, name, category, summary)。

    ★ `ch_types`（2026-09-14 用户实报显示错误）：效果只作用于部分通道（如 pump），
      节点若不带通道作用域，监控区会把效果名糊到该设备**所有**通道格上
      （"这个问题很普遍"）。通道集本就在 state 里（与 track_type 同源），带上即可。
    ★ `extra["recalled"]`：只是被场景调用带活的 → 保存新场景时排除（用户定案）。
    """
    from ..processing.dispatch import EffectNode
    (track_type, group_key, group_name, group_ids,
     base_ids, preset_id, name, category, summary) = ctx
    _ids = _node_str_ids
    return EffectNode(
        panel="effect", track_type=track_type, group_key=group_key,
        group_name=group_name or ("临时选中" if group_ids else "全部设备"),
        fixture_ids=_ids(group_ids), effect_id=preset_id, effect_name=name,
        category=category, layers=["effect"], summary=summary, live=True,
        # ★ R165：母集合与子集标记一并上报（否则 `fixture_ids` 在 group_ids 非空时
        #   把母集合丢掉 → 监控区看不到"母集合里子集错相"这层关系）。
        #   ⚠ 子集标记在 `st["params"]["_subset_ids"]`（由 `_inject_subset_marked` 注入），
        #     不在 `st` 顶层 —— 读错层会永远拿到空表（R165 自审发现并修正）。
        base_ids=_ids(base_ids),
        subset_ids=_ids((st.get("params") or {}).get("_subset_ids")),
        ch_types=[str(x) for x in (st.get("ch_types", []) or []) if str(x)],
        extra={"speed_factor": float(st.get("speed_factor", 1.0) or 1.0),
               "virtual_t": float(st.get("virtual_t", 0.0) or 0.0),
               "recalled": bool(st.get("recalled", False))})


def _restore_selected_preset(browser: "EffectBrowserWidget") -> None:
    """重定筛选后恢复选中项（屏蔽选择信号；选中已不在可见集则静默放弃）。

    自 ``_populate_list`` 抽出（R180 门禁债：该方法行数/CC 超基线不得增长）。
    """
    if not browser._selected_id or browser._selected_id not in browser._visible_preset_ids:
        return
    sm = browser._preset_list.selectionModel()
    if sm is None:
        return
    sm.blockSignals(True)
    for row in range(browser._preset_model.rowCount()):
        idx = browser._preset_model.index(row)
        if idx.data(Qt.ItemDataRole.UserRole) == browser._selected_id:
            browser._preset_list.setCurrentIndex(idx)
            break
    sm.blockSignals(False)


class EffectBrowserWidget(QWidget):
    # ★ 调度台·控件轨迹汇口：所有参数控件（含通道表单）经 _on_param_changed
    #   单点到达；全局速度经 set_global_speed 收口。TrajectorySignalHub 只连这一个。
    controlTrajectoryEvent = pyqtSignal(str, object)
    """
    完整的效果预设浏览器。
    嵌入到 Programmer 面板的 Effect 标签页中。

    信号：
      effect_applied(preset_id, params, ch_types, sort_mode)
      send_to_timeline(preset_id, params, ch_types, sort_mode, duration, fps)
    """

    effect_applied    = pyqtSignal(str, dict, list, str)
    send_to_timeline  = pyqtSignal(str, dict, list, str, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_id: Optional[str] = None
        self._param_widgets: Dict[str, ParamWidget] = {}
        self._param_columns: list[QVBoxLayout] = []
        self._param_next_column: int = 0
        self._param_span_widgets: list[QWidget] = []
        self._param_span_row: int = 1
        self._param_channel_lay: Optional[QVBoxLayout] = None
        self._param_channel_frame: Optional[QWidget] = None
        self._param_channel_scroll: Optional[QScrollArea] = None
        self._param_body_split: Optional[QSplitter] = None
        self._dock_window: Optional[QMainWindow] = None
        self._selection_dock: Optional[QDockWidget] = None
        self._adjust_dock: Optional[QDockWidget] = None
        self._effect_quick_panel: Optional[QWidget] = None
        self._effect_list_panel: Optional[QWidget] = None
        self._effect_adjust_panel: Optional[QWidget] = None
        self._adjust_side_split: Optional[QSplitter] = None
        self._visible_preset_ids: set = set()
        self._channel_param_key: Optional[str] = None
        self._channel_param_spec: Optional[ParamSpec] = None
        self._channel_form_specs: List[ParamSpec] = []
        self._channel_forms: List[dict] = []
        self._channel_forms_widget = None
        self._channel_forms_lay = None
        self._channel_add_combo = None
        self._channel_list_widget = None
        self._channel_stack = None
        self._updating_speed_controls: bool = False
        # ── 外部 context（由 MainWindow 注入）─────────────────────────────
        self._project = None        # project 对象（含 fixtures）
        self._nm = None             # NetworkManager
        self._topview = None        # TopViewPanel（读取 pos_x/pos_y）
        self._fixture_group_panel = None  # FixtureGroupPanel（读取设备组）
        self._programmer_panel = None     # ProgrammerPanel（读取当前调用组）
        self._audio_analyzer = None
        self._linked_audio_path: str = ""
        self._selected_group_ids: list = []          # 空列表=全部设备
        self._selected_group_base_order_ids: list = []
        self._selected_group_scope_ids: list = []    # ★R168 作用域（≠选中）
        self._selected_group_subset_ids: list = []   # ★R165 子集标记
        self._selected_group_spatial_groups: list = []
        # ★ R38-D1：另存"未按 keep_single 过滤"的原始分组原料 —— 只存结论([])
        #   会让消费方掉进 TopView 的 O(设备×形状) 几何兜底（= "打开 Programmer
        #   后全局 PPT"）。语义判定仍由消费方各自完成。
        self._selected_group_spatial_groups_raw: list = []
        self._selected_group_name: str = ""
        self._group_states: Dict[str, dict] = {}
        # ★ 摇杆绑定管理器（effect 参数滑块绑定：语义冲突=同效果种类+同参数）
        self._joy_mgr = None
        try:
            from .joystick_control_binding import get_joystick_bind_manager
            self._joy_mgr = get_joystick_bind_manager()
            self._joy_mgr.effect_param_changed.connect(self._on_effect_param_bound)
        except Exception:
            self._joy_mgr = None
        # ── 连续播放引擎 ──────────────────────────────────────────────────
        self._is_playing: bool = False
        self._play_start_time: float = 0.0
        # ── _tick_play 热路径缓存 ──────────────────────────────────────────
        # 原来每 25ms 帧都重建这些播放期不变的结构：TopView 全设备坐标
        # (get_all_fixture_positions 遍历所有 Sheet×全部设备)、by_id 映射、
        # fixture_defs 映射。缓存后每帧只做真正随时间变化的效果计算。
        # 失效点：TopView layout_changed(拖动/换页) / set_context(换工程/设备)。
        self._ef_topmap_cache: Optional[dict] = None   # {fid: {pos_x,pos_y}}
        self._ef_by_id_cache: Optional[dict] = None     # {fid: FixtureInstance}
        self._ef_defs_cache: Optional[dict] = None      # {def_id: FixtureDefinition}
        self._ef_fixture_ids_cache: dict = {}           # id(fixtures) -> (fixtures, ids)
        self._output_scope_cache: dict = {}             # Programmer/选择组 -> (ids, base)
        # ★ R38-D4：作用域版本号。_selected_group_ids / _base_order_ids 每次变化
        #   前推进，配合 Programmer 的 _live_selection_version 组成
        #   _effective_output_scope_ids 的缓存键。禁止再用 id() 当键：那两个列表
        #   每次都是新对象（缓存永不命中），且 id 会被回收复用（可能命中到别的
        #   选择的结果 = 输出到错误设备）。
        self._scope_version: int = 0
        # ★ R38-D2：_current_output_spatial_groups 结果缓存（含空结果）。
        self._spatial_groups_cache: dict = {}
        self._effect_channel_structure_cache: dict = {} # id(fixtures)+ch -> 静态地址结构
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(25)   # 40fps
        self._play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._play_timer.timeout.connect(self._tick_play)
        # ── 外部驱动状态（如 AudioBridge 触发）─────────────────────────────
        # 非 None 时 _tick_play 用此状态而非 UI 控件值，使外部参数覆盖 UI。
        # 元组：(preset_id, params, ch_types, sort_mode)
        self._external_state: Optional[tuple] = None
        # ★ v24: 多效果类型并行外部状态
        # Dict[track_type -> (preset_id, params, ch_types, sort_mode)]
        self._external_states: Dict[tuple, dict] = {}
        self._effect_addr_cache: Dict[tuple, Dict[str, Dict[str, tuple]]] = {}
        self._effect_history: List[str] = []
        # ★ 全局效果速度倍率（0.05x ~ 4.0x），用于统一减慢/加快所有效果动画
        # 修改此值不重置时间基准，效果会平滑加速/减速
        self._global_speed_factor: float = 1.0
        # 虚拟时间累计（用于全局速度倍率）
        self._virtual_t: float = 0.0
        self._last_real_t: float = 0.0
        self._slice_key: Optional[int] = None
        self._slice_key_modifiers: int = 0
        self._capturing_slice_key: bool = False
        self._manual_slice_index: int = 0
        self._manual_slice_t_override: Optional[float] = None
        self._manual_slice_force_motion: bool = False
        self._manual_slice_latched: bool = False
        self._manual_slice_frame_cache: Dict[int, tuple] = {}
        self._slice_context_key: str = ""
        self._slice_bindings_by_context: Dict[str, tuple] = {}
        self._manual_slice_indices_by_context: Dict[str, int] = {}
        self._manual_slice_frame_cache_by_context: Dict[str, Dict[int, tuple]] = {}
        self._legacy_slice_binding: Optional[tuple] = None
        self._updating_slice_context: bool = False
        self._slice_shortcut: Optional[QShortcut] = None
        # ★ 切片步距(粒度)：每按一次切片键波形前进的基本单元数；1=逐台最细。
        self._slice_stride: int = 1
        # ★ 切片自动过渡：每片之间柔和淡入淡出秒数；0=瞬切。
        #   过渡途中用专用定时器逐字节插值 from→to 缓冲(latch 期间主播放定时器是停的)。
        self._slice_fade_timer: Optional[QTimer] = None
        self._slice_fade_active: bool = False
        self._slice_fade_elapsed: float = 0.0
        self._slice_fade_duration: float = 0.0
        self._slice_fade_from: Dict[int, tuple] = {}
        self._slice_fade_to: Dict[int, tuple] = {}
        self._slice_fade_last_real: float = 0.0
        self._slice_fade_last_written: Dict[int, tuple] = {}
        # ★ 参数覆盖字典（摇杆绑定/音频调制等外部源实时注入）
        # 在 UI 手动模式下，_tick_play 会将这些值合并到 _get_current_params() 结果中
        self._param_overrides: Dict[str, Any] = {}
        self.setStyleSheet(f"background:{BG_MAIN};color:{FG_MAIN};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self._populate_list()

    def _build_ui(self):
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)


        # ── 子页面堆栈 ────────────────────────────────────────────────────────
        self._subpage_stack = QStackedWidget()
        root.addWidget(self._subpage_stack, 1)

        # 页 0：效果库（原始 toolbar + main_split 内容）
        page0 = QWidget()
        page0.setStyleSheet(f"background:{BG_MAIN};")
        root = QVBoxLayout(page0)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._subpage_stack.addWidget(page0)

        # ── 顶部工具栏 ──────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(40)
        _bk_scope_qss(toolbar, f"background:{BG_HEADER};border-bottom:1px solid {_th.SEP_LINE_FAINT};")
        tbar_lay = QHBoxLayout(toolbar)
        tbar_lay.setContentsMargins(8, 4, 8, 4)
        tbar_lay.setSpacing(6)

        # 搜索框（R180：placeholder 去 emoji，图标走 tabler；匹配域扩到
        #   名称/ID/描述/标签/细分类/大类名，见 effect_categories.preset_matches_query）
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索名称/ID/标签…")
        self._search.setStyleSheet(_INPUT)
        self._search.setMinimumWidth(80)
        self._search.setMaximumWidth(180)
        _sicon = load_tabler_icon("search", 13, _th.TEXT_SECONDARY)
        if not _sicon.isNull():
            self._search.addAction(_sicon, QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._on_search_text_changed)
        tbar_lay.addWidget(self._search)
        # 搜索防抖:每次按键重建 1611 个 widget 太重,改为停止输入 220ms 后只重建一次。
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(220)
        self._search_debounce.timeout.connect(self._filter_list)

        # 分类筛选 → ★ R180 两级分类条（EffectCategoryBar，toolbar 下方整行）：
        #   一级大类 chips + 二级细分类 chips，窄屏自动折叠成两个下拉。
        #   `self._cat_combo` 保留为兼容句柄（currentData()=当前细分类，""=不限），
        #   load_state / _sync_ui_to_recalled_cell 等旧引用语义不变。
        self._cat_bar = EffectCategoryBar(self)
        try:
            self._cat_bar.refresh(get_engine().all_presets())
        except Exception:
            logger.debug("effect_browser: failed to load categories into category bar")
        self._cat_bar.filterChanged.connect(self._filter_list)
        self._cat_combo = self._cat_bar.fine_combo


        tbar_lay.addStretch()

        # ★ R183 批 A：「另存为我的效果」——把当前参数存成命名自定义预设，
        #   落「自定义/我的效果」细分类（此前空壳占位，现在由用户填满）。
        self._save_user_preset_btn = QPushButton("另存为我的效果")
        self._save_user_preset_btn.setStyleSheet(_BTN)
        self._save_user_preset_btn.setToolTip(
            "把当前效果的参数（含通道表单）存为命名预设，出现在「其它 · 我的效果」分类；"
            "右键条目可重命名/删除。")
        _save_icon = load_tabler_icon("device-floppy", 13, _th.TEXT_SECONDARY)
        if not _save_icon.isNull():
            self._save_user_preset_btn.setIcon(_save_icon)
        self._save_user_preset_btn.clicked.connect(self._on_save_user_preset)
        tbar_lay.addWidget(self._save_user_preset_btn)

        # ★ R183 批 A：召回过渡时长（场景/快照召回时按 N 秒插值参数；0=立即=旧行为）。
        #   历史偏好持久化在用户档（跨会话），默认 0.0 → 未设过的用户零行为变化。
        self._recall_fade_lbl = QLabel("召回过渡(s):")
        self._recall_fade_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._recall_fade_lbl.setToolTip(
            "场景/快照召回时，把参数从上一状态在 N 秒内缓动到目标值。\n"
            "0 = 立即（与旧版本完全一致）。数值参数插值，形状/通道类参数立即切换。")
        tbar_lay.addWidget(self._recall_fade_lbl)
        self._recall_fade_spin = QDoubleSpinBox()
        self._recall_fade_spin.setRange(0.0, float(_fxstore.RECALL_FADE_MAX))
        self._recall_fade_spin.setSingleStep(0.1)
        self._recall_fade_spin.setDecimals(2)
        self._recall_fade_spin.setFixedWidth(62)
        self._recall_fade_spin.setStyleSheet(_SPIN)
        try:
            self._recall_fade_spin.setValue(float(_fxstore.recall_fade_pref()))
        except Exception:
            logger.debug("effect_browser: read recall fade pref failed")
        self._recall_fade_spin.valueChanged.connect(self._on_recall_fade_changed)
        tbar_lay.addWidget(self._recall_fade_spin)

        root.addWidget(toolbar)
        root.addWidget(self._cat_bar)
        # ★ R183 W2-FX：#5/#6/#9/#10/#11/#14/#15/#17 功能条（实现在 _w2fx）
        root.addWidget(_w2fx.build_tools_row(self))

        # ── 主体分割 ────────────────────────────────────────────────────────
        selection_splitter = QSplitter(Qt.Orientation.Horizontal)
        selection_splitter.setChildrenCollapsible(False)
        selection_splitter.setHandleWidth(5)
        selection_splitter.setMinimumSize(0, 0)
        selection_splitter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        selection_splitter.setStyleSheet("QSplitter::handle{background:#3D3D3D;width:5px;}")

        quick = QWidget()
        self._effect_quick_panel = quick
        quick.setMinimumWidth(140)
        quick.setMaximumWidth(240)
        quick.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        quick.setStyleSheet(f"background:{BG_PANEL};")
        quick_lay = QVBoxLayout(quick)
        quick_lay.setContentsMargins(0, 0, 0, 0)
        quick_lay.setSpacing(0)
        active_lbl = QLabel("正在使用")
        active_lbl.setStyleSheet(
            f"color:{FG_GREEN};font-size:10px;font-weight:bold;padding:4px 8px;"
            f"background:{BG_HEADER};border-bottom:1px solid #242424;")
        quick_lay.addWidget(active_lbl)
        self._active_effect_list = QListWidget()
        self._active_effect_list.setIconSize(QSize(18, 18))
        self._active_effect_list.setSpacing(2)
        # §11 附加：逐像素纵向滚动更顺畅 + 视口底部 4px 呼吸位
        self._active_effect_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._active_effect_list.setViewportMargins(0, 0, 0, 4)
        self._active_effect_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._active_effect_list.setStyleSheet(
            f"QListWidget{{background:{BG_PANEL};color:{FG_MAIN};border:none;}}"
            "QListWidget::item{padding:6px 7px;min-height:38px;border-bottom:1px solid rgba(255,255,255,21);}"
            f"QListWidget::item:selected{{background:{BG_ITEM_SEL};color:{_th.TEXT_SELECTED_FG};}}"
            f"QListWidget::item:hover{{background:{BG_ITEM_HOV};}}"
            "QScrollBar:vertical{background:transparent;width:14px;border-radius:6px;margin:0;}"
            "QScrollBar::handle:vertical{background:#545454;border-radius:6px;min-height:32px;}"
            "QScrollBar::handle:vertical:hover{background:#656565;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;background:transparent;}")
        self._active_effect_list.itemClicked.connect(self._on_quick_effect_clicked)
        quick_lay.addWidget(self._active_effect_list, 1)
        history_lbl = QLabel("历史使用")
        history_lbl.setStyleSheet(
            f"color:{FG_ORANGE};font-size:10px;font-weight:bold;padding:4px 8px;"
            f"background:{BG_HEADER};border-top:1px solid #242424;"
            f"border-bottom:1px solid #242424;")
        quick_lay.addWidget(history_lbl)
        self._history_effect_list = QListWidget()
        self._history_effect_list.setIconSize(QSize(18, 18))
        self._history_effect_list.setSpacing(2)
        # §11 附加：同上——逐像素滚动 + 底部 4px
        self._history_effect_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._history_effect_list.setViewportMargins(0, 0, 0, 4)
        self._history_effect_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._history_effect_list.setStyleSheet(
            f"QListWidget{{background:{BG_PANEL};color:{FG_MAIN};border:none;}}"
            "QListWidget::item{padding:6px 7px;min-height:38px;border-bottom:1px solid rgba(255,255,255,21);}"
            f"QListWidget::item:selected{{background:{BG_ITEM_SEL};color:{_th.TEXT_SELECTED_FG};}}"
            f"QListWidget::item:hover{{background:{BG_ITEM_HOV};}}"
            "QScrollBar:vertical{background:transparent;width:14px;border-radius:6px;margin:0;}"
            "QScrollBar::handle:vertical{background:#545454;border-radius:6px;min-height:32px;}"
            "QScrollBar::handle:vertical:hover{background:#656565;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;background:transparent;}")
        self._history_effect_list.itemClicked.connect(self._on_quick_effect_clicked)
        quick_lay.addWidget(self._history_effect_list, 1)
        selection_splitter.addWidget(quick)

        # 左侧：预设列表
        left = QWidget()
        self._effect_list_panel = left
        left.setMinimumSize(0, 0)
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        left.setStyleSheet(f"background:{BG_PANEL};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        # 预设数量标签
        self._count_lbl = QLabel("0 个预设")
        self._count_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:11px;padding:4px 8px;"
            f"background:{BG_HEADER};border-bottom:1px solid #242424;")
        left_lay.addWidget(self._count_lbl)

        self._preset_list = PresetListView()
        self._preset_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # §11 附加：效果库列表底部 4px padding（末张卡片不贴底裁切）
        self._preset_list.setViewportMargins(0, 0, 0, 4)
        self._preset_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._preset_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._preset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._preset_list.setMouseTracking(True)
        self._preset_list.setStyleSheet(
            f"QListView{{background:{BG_PANEL};border:none;outline:none;}}"
            f"QListView::item{{background:transparent;border:none;}}"
            f"QScrollBar:vertical{{background:{BG_HEADER};width:16px;border-radius:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:#545454;border-radius:6px;min-height:36px;}}"
            f"QScrollBar::handle:vertical:hover{{background:#656565;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;background:transparent;}}")
        self._preset_model = PresetListModel(self._preset_list)
        # ★ R183 W2-FX：#6/#10/#12/#14 卡片附加层（子类在既有绘制之后叠加，
        #   星标绘制路径（批 A）与其后的本层绘制互不覆盖）
        self._preset_delegate = _fx_cards.make_delegate(self._preset_list, self)
        self._preset_list.setModel(self._preset_model)
        self._preset_list.setItemDelegate(self._preset_delegate)
        self._preset_list.selectionModel().currentChanged.connect(self._on_list_current_changed)
        # ★ R183 批 A：星标点击 / 右键菜单（视图职责在 PresetListView，这里只接线）
        self._preset_list.starClicked.connect(self._on_star_clicked)
        self._preset_list.menuRequested.connect(self._on_preset_menu)
        left_lay.addWidget(self._preset_list, 1)
        # 空态占位（restyle_spec §10.6）：筛选无结果时替代纯黑列表区
        self._list_empty_state = _BkEmptyState(
            "没有匹配的效果", "调整搜索关键字或分类/通道筛选条件", icon_key="search-off")
        self._list_empty_state.setVisible(False)
        left_lay.addWidget(self._list_empty_state, 1)
        selection_splitter.addWidget(left)

        # 右侧：参数面板
        right = QWidget()
        self._effect_adjust_panel = right
        right.setMinimumWidth(620)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        right.setStyleSheet(f"background:{BG_PANEL};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(8)

        # ── 效果名 + 分类徽标行（restyle_spec §10：glyph16 + 名12 + 分类11）───
        head_row = QWidget()
        head_row.setStyleSheet("background:transparent;")
        head_lay = QHBoxLayout(head_row)
        head_lay.setContentsMargins(0, 0, 0, 0)
        head_lay.setSpacing(8)
        self._preset_icon_lbl = QLabel()
        self._preset_icon_lbl.setFixedSize(20, 20)
        self._preset_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preset_icon_lbl.setStyleSheet("background:transparent;")
        head_lay.addWidget(self._preset_icon_lbl)
        self._preset_name_lbl = QLabel("请选择预设")
        self._preset_name_lbl.setStyleSheet(
            f"color:{FG_MAIN};font-size:12px;font-weight:bold;background:transparent;")
        head_lay.addWidget(self._preset_name_lbl)
        self._preset_cat_lbl = QLabel("")
        self._preset_cat_lbl.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:11px;background:transparent;")
        head_lay.addWidget(self._preset_cat_lbl)
        head_lay.addStretch()
        # ★ R183 W2-FX #17：「已改 N 项」dirty 提示（实现在 _w2fx）
        head_lay.addWidget(_w2fx.build_dirty_label(self))
        right_lay.addWidget(head_row)

        self._preset_desc_lbl = QLabel("")
        self._preset_desc_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;background:transparent;")
        self._preset_desc_lbl.setWordWrap(True)
        right_lay.addWidget(self._preset_desc_lbl)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#3D3D3D;")   # HLine 分隔线 border_normal
        right_lay.addWidget(sep)

        # 调整区按左右两列分割：左侧应用设置，右侧通道设置。
        param_body = QSplitter(Qt.Orientation.Horizontal)
        param_body.setChildrenCollapsible(True)
        param_body.setOpaqueResize(True)
        param_body.setHandleWidth(7)
        param_body.setStyleSheet(
            "QSplitter::handle{background:#3D3D3D;width:7px;margin:0 2px;}"
            "QSplitter::handle:hover{background:#4772B3;}"
        )
        self._param_body_split = param_body

        self._param_container = QWidget()
        _bk_scope_qss(self._param_container, "background:transparent;border:none;")
        self._param_container.setVisible(False)
        self._param_lay = QGridLayout(self._param_container)
        self._param_lay.setContentsMargins(0, 0, 0, 0)
        self._param_lay.setHorizontalSpacing(8)
        self._param_lay.setVerticalSpacing(4)
        self._param_col_left = QVBoxLayout()
        self._param_col_left.setContentsMargins(0, 0, 0, 0)
        self._param_col_left.setSpacing(4)
        self._param_col_right = QVBoxLayout()
        self._param_col_right.setContentsMargins(0, 0, 0, 0)
        self._param_col_right.setSpacing(4)
        self._param_col_left.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self._param_col_right.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self._param_col_left.addStretch()
        self._param_col_right.addStretch()
        self._param_columns = [self._param_col_left, self._param_col_right]
        self._param_lay.addLayout(self._param_col_left, 0, 0)
        self._param_lay.addLayout(self._param_col_right, 0, 1)
        self._param_lay.setColumnStretch(0, 1)
        self._param_lay.setColumnStretch(1, 1)

        controls_frame = QFrame()
        controls_frame.setMinimumHeight(260)
        controls_frame.setMinimumWidth(220)
        controls_frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        controls_frame.setStyleSheet(
            "QFrame{background:#1D1D1D;border:1px solid #3D3D3D;border-radius:4px;}"
        )
        controls_outer = QVBoxLayout(controls_frame)
        controls_outer.setContentsMargins(8, 8, 8, 8)
        controls_outer.setSpacing(6)
        controls_title = QLabel("应用设置")
        controls_title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;padding:0 2px;")
        controls_outer.addWidget(controls_title)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setMinimumSize(0, 0)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        controls_scroll.setStyleSheet(
            "QScrollArea{background:#1D1D1D;border:none;}"
            "QScrollBar:vertical{background:transparent;width:14px;border-radius:6px;margin:0;}"
            "QScrollBar:horizontal{background:transparent;height:14px;border-radius:6px;margin:0;}"
            "QScrollBar::handle:vertical,QScrollBar::handle:horizontal{background:#545454;border-radius:6px;min-height:32px;min-width:32px;}"
            "QScrollBar::handle:vertical:hover,QScrollBar::handle:horizontal:hover{background:#656565;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;background:transparent;}")
        controls_container = QWidget()
        _bk_scope_qss(controls_container, f"background:{_th.BG_ROOT};border:none;")
        controls_grid = QGridLayout(controls_container)
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setHorizontalSpacing(8)
        controls_grid.setVerticalSpacing(6)
        controls_grid.setColumnStretch(0, 1)
        controls_grid.setColumnStretch(1, 1)
        controls_grid.addWidget(self._param_container, 0, 0, 1, 2)
        # ★ R183 W2-FX #8：参数宏旋钮区（一个旋钮驱动 N 个参数；实现在 _w2fx）
        controls_grid.addWidget(_w2fx.build_macro_area(self), 1, 0, 1, 2)
        controls_scroll.setWidget(controls_container)
        controls_outer.addWidget(controls_scroll, 1)

        self._param_channel_frame = QFrame()
        self._param_channel_frame.setStyleSheet(
            "QFrame{background:#1D1D1D;border:1px solid #3D3D3D;border-radius:4px;}")
        self._param_channel_frame.setMinimumHeight(320)
        self._param_channel_frame.setMinimumWidth(320)
        self._param_channel_frame.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        channel_outer = QVBoxLayout(self._param_channel_frame)
        channel_outer.setContentsMargins(8, 8, 8, 8)
        channel_outer.setSpacing(6)
        channel_title = QLabel("通道设置")
        channel_title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:12px;font-weight:bold;padding:0 2px;")
        channel_outer.addWidget(channel_title)
        self._param_channel_scroll = QScrollArea()
        self._param_channel_scroll.setWidgetResizable(True)
        self._param_channel_scroll.setMinimumSize(0, 0)
        self._param_channel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._param_channel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._param_channel_scroll.setStyleSheet(
            "QScrollArea{background:#1D1D1D;border:none;}"
            "QScrollBar:vertical{background:transparent;width:14px;border-radius:6px;margin:0;}"
            "QScrollBar:horizontal{background:transparent;height:14px;border-radius:6px;margin:0;}"
            "QScrollBar::handle:vertical,QScrollBar::handle:horizontal{background:#545454;border-radius:6px;min-height:32px;min-width:32px;}"
            "QScrollBar::handle:vertical:hover,QScrollBar::handle:horizontal:hover{background:#656565;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;background:transparent;}")
        self._param_channel_container = QWidget()
        _bk_scope_qss(self._param_channel_container, f"background:{_th.BG_ROOT};border:none;")
        self._param_channel_lay = QVBoxLayout(self._param_channel_container)
        self._param_channel_lay.setContentsMargins(0, 0, 0, 0)
        self._param_channel_lay.setSpacing(6)
        self._param_channel_lay.addStretch()
        self._param_channel_scroll.setWidget(self._param_channel_container)
        channel_outer.addWidget(self._param_channel_scroll, 1)
        self._param_channel_frame.setVisible(False)

        # ── 设备组选择 ──────────────────────────────────────────────────────
        grp_grp = QGroupBox("应用到设备组")
        grp_grp.setStyleSheet(
            f"QGroupBox{{color:{FG_DIM};font-size:10px;border:1px solid #242424;"
            f"border-radius:4px;margin-top:6px;padding-top:8px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:8px;top:0px;}}")
        grp_outer = QVBoxLayout(grp_grp)
        grp_outer.setContentsMargins(6, 4, 6, 4)
        grp_outer.setSpacing(3)
        # 快捷按钮行
        grp_quick = QHBoxLayout()
        grp_quick.setSpacing(4)
        all_btn = QPushButton("全部")
        all_btn.setStyleSheet(_BTN)
        all_btn.setFixedHeight(22)
        all_btn.setFixedWidth(40)
        all_btn.clicked.connect(lambda: self._on_group_quick_all())
        grp_quick.addWidget(all_btn)
        grp_quick.addStretch()
        grp_outer.addLayout(grp_quick)
        # 设备组下拉框（动态填充）
        self._group_combo = QComboBox()
        self._group_combo.setStyleSheet(_COMBO)
        self._group_combo.addItem("全部设备", None)
        self._group_combo.currentIndexChanged.connect(self._on_group_combo_changed)
        grp_outer.addWidget(self._group_combo)
        # 状态标签
        self._group_status_lbl = QLabel("全部设备")
        self._group_status_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        grp_outer.addWidget(self._group_status_lbl)
        controls_grid.addWidget(grp_grp, 2, 0, 1, 2)

        # ── 排序模式 ────────────────────────────────────────────────────────
        sort_grp = QGroupBox("TopView 排序模式")
        sort_grp.setStyleSheet(
            f"QGroupBox{{color:{FG_DIM};font-size:10px;border:1px solid #242424;"
            f"border-radius:4px;margin-top:6px;padding-top:8px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:8px;top:0px;}}")
        sort_lay = QHBoxLayout(sort_grp)
        sort_lay.setContentsMargins(6, 4, 6, 4)
        self._sort_combo = QComboBox()
        self._sort_combo.setStyleSheet(_COMBO)
        sort_labels = {
            "index":     "原始顺序",
            "linear_x":  "X 轴线性",
            "linear_y":  "Y 轴线性",
            "radial":    "圆形极角",
            "grid_row":  "矩阵行扫",
            "grid_col":  "矩阵列扫",
        }
        for mode in SORT_MODES:
            self._sort_combo.addItem(sort_labels.get(mode, mode), mode)
        sort_lay.addWidget(self._sort_combo)
        self._skip_base_order_chk = QCheckBox("跳过编号排序")
        self._skip_base_order_chk.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;")
        self._skip_base_order_chk.setToolTip(
            "勾选后效果只按设备坐标排序（x 主 / y 次），不看 TopView 选中编号序；\n"
            "再按上面的「方向」做轴向重排（左到右=+x、前到后=+y…）。\n"
            "不勾选 = 按当前选择的基准序（TopView 编号/框选链序）依次执行。\n"
            "注：「整体坐标系」下不再按全局坐标重排（R201 起）——要全局扫描\n"
            "请勾选本开关或改用 X/Y 轴线性排序模式。")
        sort_lay.addWidget(self._skip_base_order_chk)
        controls_grid.addWidget(sort_grp, 3, 0, 1, 2)

        # ── 应用模式 / 方向 ───────────────────────────────────────────────────
        dir_grp = QGroupBox("应用模式 / 方向")
        dir_grp.setStyleSheet(
            f"QGroupBox{{color:{FG_DIM};font-size:10px;border:1px solid #242424;"
            f"border-radius:4px;margin-top:6px;padding-top:8px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:8px;top:0px;}}")
        dir_lay = QGridLayout(dir_grp)
        dir_lay.setContentsMargins(6, 4, 6, 4)
        dir_lay.setHorizontalSpacing(6)
        dir_lay.setVerticalSpacing(4)
        self._direction_combo = QComboBox()
        self._direction_combo.setStyleSheet(_COMBO)
        for d in EFFECT_DIRECTIONS:
            self._direction_combo.addItem(DIRECTION_LABELS.get(d, d), d)
        dir_lay.addWidget(self._direction_combo, 0, 0)
        self._app_scope_combo = QComboBox()
        self._app_scope_combo.setStyleSheet(_COMBO)
        app_scope_labels = {
            "both": "波形与顺序",
            "whole": "仅波形",
            "sequence": "仅顺序",
        }
        for scope in EFFECT_APPLICATION_SCOPES:
            self._app_scope_combo.addItem(app_scope_labels.get(scope, scope), scope)
        self._app_scope_combo.setToolTip("波形与顺序：同时应用两者；仅波形：整组运行波形；仅顺序：固定波形值并推进顺序窗口。")
        dir_lay.addWidget(self._app_scope_combo, 0, 1)
        self._spatial_scope_combo = QComboBox()
        self._spatial_scope_combo.setStyleSheet(_COMBO)
        spatial_scope_labels = {
            "per_shape": "各自选择组坐标系",
            "global": "整体坐标系",
        }
        # ★ 2026-08-04 坐标系与控灯粒度正交拆分：坐标系(各自/整体)是 effect 的坐标空间
        #   （per_shape=每个设备自己的圆/组坐标系，global=全部灯一个矩阵坐标系）；
        #   控灯粒度(整体控灯/单控灯)是「包内配灯当一个灯还是逐个」——见 _control_mode_combo。
        #   首项「跟随全局灯控」只解析**坐标系**语义（single=各自坐标系/whole=整体坐标系）。
        self._spatial_scope_combo.addItem("跟随全局灯控", "follow")
        for scope in EFFECT_SPATIAL_SCOPES:
            self._spatial_scope_combo.addItem(spatial_scope_labels.get(scope, scope), scope)
        self._spatial_scope_combo.setToolTip(
            "坐标系（effect 的坐标空间，与控灯粒度正交）：\n"
            "默认「跟随全局灯控」：单控灯=各自坐标系（每台设备自己的圆），整体控灯=整体坐标系（全部灯一个矩阵）。\n"
            "也可显式覆盖为某一坐标系。")
        dir_lay.addWidget(self._spatial_scope_combo, 1, 0)
        self._control_mode_combo = QComboBox()
        self._control_mode_combo.setStyleSheet(_COMBO)
        control_mode_labels = {
            "single": "单控灯(逐个)",
            "whole": "整体控灯(配灯合一)",
        }
        # ★ 2026-08-04 控灯粒度维度（与坐标系正交）：整体控灯=包内多个配灯当一个灯
        #   （共享同一效果值）；单控灯=包内成员逐个独立值。首项跟随 Programmer 全局
        #   「单控灯/整体控灯」按钮（联动所有页面）。
        self._control_mode_combo.addItem("跟随全局灯控", "follow")
        for cm in ("single", "whole"):
            self._control_mode_combo.addItem(control_mode_labels.get(cm, cm), cm)
        self._control_mode_combo.setToolTip(
            "控灯粒度（包内配灯当一个灯还是逐个，与坐标系正交）：\n"
            "默认「跟随全局灯控」：跟随 Programmer 全局「单控灯/整体控灯」按钮。\n"
            "整体控灯=包内多个配灯当作一盏灯统一控制；单控灯=逐个独立。\n"
            "固定整体控设备（如数控）不受本设置影响，恒整体控。")
        dir_lay.addWidget(self._control_mode_combo, 2, 0)
        hint = QLabel("坐标系=效果的空间基准（各自设备圆/整体矩阵）；控灯粒度=配灯合一或逐个（两维度独立设置）。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        dir_lay.addWidget(hint, 2, 1)
        controls_grid.addWidget(dir_grp, 4, 0, 1, 2)

        self._sort_combo.currentIndexChanged.connect(
            lambda _=None: self._on_param_changed("_sort_mode", self._get_sort_mode()))
        self._direction_combo.currentIndexChanged.connect(
            lambda _=None: self._on_param_changed("_app_direction", self._get_app_direction()))
        self._app_scope_combo.currentIndexChanged.connect(
            lambda _=None: self._on_param_changed("_app_scope", self._get_app_scope()))
        self._spatial_scope_combo.currentIndexChanged.connect(
            lambda _=None: self._on_param_changed("spatial_scope", self._get_spatial_scope()))
        self._control_mode_combo.currentIndexChanged.connect(
            lambda _=None: self._on_param_changed("control_mode", self._get_control_mode()))
        # ★ 2026-08-11 #18 跳过编号排序开关（只看坐标按应用方向排序）
        self._skip_base_order_chk.toggled.connect(
            lambda _=None: self._on_param_changed("_skip_base_order", self._get_skip_base_order()))

        # ── 通道类型选择已合并到各效果参数 ─────────────────────────────────────
        # 旧版右侧还有一组“应用到通道类型”全局复选框，但多数效果已经在
        # 参数区通过 ch_type/ch 等字段选择通道。重复选择会造成“参数里选了
        # 电机，外层又过滤成灯光”的无效输出。本版取消右侧全局通道复选框，
        # 输出通道由 _get_selected_ch_types() 从当前预设参数和 target_channels
        # 自动推导。保留 _ch_checks 空字典，仅用于兼容旧方法引用。
        self._ch_checks: Dict[str, QCheckBox] = {}

        # ── 全局效果速度倍率 ───────────────────────────────────────────────────────────────────
        spd_row = QHBoxLayout()
        spd_row.setSpacing(8)
        self._current_speed_title = spd_lbl_title = QLabel("通道速度:")
        spd_lbl_title.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        spd_row.addWidget(spd_lbl_title)
        self._global_speed_sld = QSlider(Qt.Orientation.Horizontal)
        self._global_speed_sld.setRange(0, 1000)
        self._global_speed_sld.setValue(100)       # 默认 1.0x
        self._global_speed_sld.setStyleSheet(_SLIDER)
        self._global_speed_sld.setMinimumWidth(80)
        self._global_speed_sld.setMaximumWidth(140)
        self._global_speed_sld.valueChanged.connect(self._on_global_speed_changed)
        spd_row.addWidget(self._global_speed_sld)
        self._global_speed_lbl = QLabel("1.00x")
        self._global_speed_lbl.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;min-width:44px;")
        spd_row.addWidget(self._global_speed_lbl)
        self._global_speed_spin = QDoubleSpinBox()
        self._global_speed_spin.setRange(0.0, 16.0)
        self._global_speed_spin.setDecimals(3)
        self._global_speed_spin.setSingleStep(0.01)
        self._global_speed_spin.setValue(1.0)
        self._global_speed_spin.setSuffix("x")
        self._global_speed_spin.setStyleSheet(_SPIN)
        self._global_speed_spin.setFixedWidth(78)
        self._global_speed_spin.valueChanged.connect(self._on_global_speed_spin_changed)
        spd_row.addWidget(self._global_speed_spin)
        # 快捷按鈕
        for label, val in [("慢", 25), ("半", 50), ("正常", 100), ("快", 200)]:
            btn = QPushButton(label)
            btn.setStyleSheet(_BTN)
            btn.setFixedHeight(20)
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda _, v=val: self.set_global_speed(v / 100.0))
            spd_row.addWidget(btn)
        spd_row.addStretch()
        controls_grid.addLayout(spd_row, 5, 0, 1, 2)
        self.set_global_speed(1.0)
        _master_speed.install(self, controls_grid, _SPIN, _BTN)

        slice_row = QHBoxLayout()
        slice_row.setSpacing(6)
        slice_title = QLabel("切片键:")
        slice_title.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        slice_row.addWidget(slice_title)
        self._slice_key_lbl = QLabel("未绑定")
        self._slice_key_lbl.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;min-width:56px;")
        slice_row.addWidget(self._slice_key_lbl)
        self._slice_key_bind_btn = QPushButton("绑定")
        self._slice_key_bind_btn.setStyleSheet(_BTN)
        self._slice_key_bind_btn.setFixedHeight(22)
        self._slice_key_bind_btn.setFixedWidth(48)
        self._slice_key_bind_btn.clicked.connect(self._begin_slice_key_capture)
        slice_row.addWidget(self._slice_key_bind_btn)
        self._slice_key_clear_btn = QPushButton("清除")
        self._slice_key_clear_btn.setStyleSheet(_BTN)
        self._slice_key_clear_btn.setFixedHeight(22)
        self._slice_key_clear_btn.setFixedWidth(48)
        self._slice_key_clear_btn.clicked.connect(self._clear_slice_key_binding)
        slice_row.addWidget(self._slice_key_clear_btn)
        slice_row.addStretch()
        controls_grid.addLayout(slice_row, 7, 0, 1, 2)

        # ── 切片粒度(步距) / 自动过渡时间 ────────────────────────────────────
        # 粒度=每按一次切片键，波形沿应用方向前进的“基本单元(台/组)”数；
        #   1=逐台最细，>1=每按跳更多台，变化更粗犷。仅改步距，不改效果形态。
        # 过渡=每片之间的柔和淡入淡出秒数；0=瞬切(零回归)，>0 时缓冲级插值。
        slice_row2 = QHBoxLayout()
        slice_row2.setSpacing(6)
        gran_title = QLabel("切片粒度:")
        gran_title.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        gran_title.setToolTip(
            "每按一次切片键，波形沿应用方向前进的基本单元数。\n"
            "1=逐台最细；增大则每次跳得更远、变化更粗犷。只改步距，不改效果形态。")
        slice_row2.addWidget(gran_title)
        self._slice_stride_sld = QSlider(Qt.Orientation.Horizontal)
        self._slice_stride_sld.setRange(1, 32)
        self._slice_stride_sld.setValue(1)
        self._slice_stride_sld.setFixedWidth(96)
        self._slice_stride_sld.setStyleSheet(_SPIN)
        self._slice_stride_sld.valueChanged.connect(self._on_slice_stride_changed)
        slice_row2.addWidget(self._slice_stride_sld)
        self._slice_stride_spin = QSpinBox()
        self._slice_stride_spin.setRange(1, 32)
        self._slice_stride_spin.setValue(1)
        self._slice_stride_spin.setFixedWidth(48)
        self._slice_stride_spin.setStyleSheet(_SPIN)
        self._slice_stride_spin.valueChanged.connect(self._on_slice_stride_changed)
        slice_row2.addWidget(self._slice_stride_spin)
        fade_title = QLabel("切片过渡(s):")
        fade_title.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        fade_title.setToolTip(
            "每片之间的柔和过渡时长(秒)。0=瞬切；>0 时在两片之间做平滑淡入淡出。")
        slice_row2.addWidget(fade_title)
        self._slice_fade_spin = QDoubleSpinBox()
        self._slice_fade_spin.setRange(0.0, 30.0)
        self._slice_fade_spin.setSingleStep(0.05)
        self._slice_fade_spin.setDecimals(2)
        self._slice_fade_spin.setValue(0.0)
        self._slice_fade_spin.setFixedWidth(64)
        self._slice_fade_spin.setStyleSheet(_SPIN)
        slice_row2.addWidget(self._slice_fade_spin)
        slice_row2.addStretch()
        controls_grid.addLayout(slice_row2, 8, 0, 1, 2)

        # ── 时长 / FPS ───────────────────────────────────────────────────────
        dur_row = QHBoxLayout()
        dur_row.setSpacing(8)
        dur_row.addWidget(QLabel("时长(s):"))
        self._dur_spin = QDoubleSpinBox()
        self._dur_spin.setRange(0.1, 600.0)
        self._dur_spin.setValue(4.0)
        self._dur_spin.setSingleStep(0.5)
        self._dur_spin.setStyleSheet(_SPIN)
        self._dur_spin.setFixedWidth(70)
        dur_row.addWidget(self._dur_spin)

        dur_row.addWidget(QLabel("FPS:"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(40)
        self._fps_spin.setStyleSheet(_SPIN)
        self._fps_spin.setFixedWidth(55)
        dur_row.addWidget(self._fps_spin)
        dur_row.addStretch()
        controls_grid.addLayout(dur_row, 10, 0, 1, 2)

        # ── 操作按钮 ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        # R47：播放钮中性化（去橙accent）——统一 _BTN 中性面，语义交给图标
        self._play_btn = QPushButton("开始播放")
        self._play_btn.setStyleSheet(_BTN)
        try:
            _pic = load_tabler_icon("player-play", 16, "#E6E6E6")
            if not _pic.isNull():
                self._play_btn.setIcon(_pic)
        except Exception:
            pass
        self._play_btn.setFixedHeight(34)
        self._play_btn.setMinimumWidth(118)
        self._play_btn.clicked.connect(self._on_play_toggle)
        btn_row.addWidget(self._play_btn)

        stop_btn = QPushButton("全部停止")
        # R47：停止钮中性面 + 语义仅亮描边红字（钮面永不着色）
        stop_btn.setStyleSheet(
            "QPushButton{background:#545454;color:#E36855;"
            "border:1px solid #3D3D3D;"
            "border-radius:4px;padding:5px 10px;font-size:12px;}"
            "QPushButton:hover{background:#656565;border-color:#E36855;}"
            "QPushButton:pressed{background:#4772B3;color:#FFFFFF;}")
        try:
            _sic = load_tabler_icon("player-stop", 16, "#E36855")
            if not _sic.isNull():
                stop_btn.setIcon(_sic)
        except Exception:
            pass
        stop_btn.setFixedHeight(34)
        stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(stop_btn)

        tl_btn = QPushButton("→ 时间线")
        tl_btn.setStyleSheet(_BTN)
        tl_btn.setFixedHeight(34)
        tl_btn.clicked.connect(self._on_send_timeline)
        btn_row.addWidget(tl_btn)

        # ★ R178：「存为自定义」按钮已删（自定义效果库移除；请用调度台存储区
        #   点空格存场景 —— 那条路径带存前确认清单与完整面板快照）。
        btn_row.addStretch()

        controls_grid.addLayout(btn_row, 9, 0, 1, 2)

        param_body.addWidget(controls_frame)
        param_body.addWidget(self._param_channel_frame)
        param_body.setCollapsible(0, False)
        param_body.setCollapsible(1, True)
        param_body.setStretchFactor(0, 1)
        param_body.setStretchFactor(1, 2)
        param_body.setSizes([360, 620])
        self._adjust_side_split = param_body
        right_lay.addWidget(param_body, 1)

        # ── 底部按钮行（restyle_spec §10：应用/随机/重置 右对齐，
        #    主按钮 objectName="primary" 走 select_blue 语义）───────────────
        action_row = QWidget()
        action_row.setStyleSheet(
            "QWidget{background:transparent;}"
            f"QPushButton#primary{{background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};"
            f"border:1px solid {_th.SELECT_BLUE};border-radius:4px;"
            "min-height:24px;padding:2px 18px;font-size:12px;}"
            "QPushButton#primary:hover{background:#5480C5;border-color:#5480C5;}"
            "QPushButton#primary:pressed{background:#35507C;color:#FFFFFF;}"
        )
        act_lay = QHBoxLayout(action_row)
        act_lay.setContentsMargins(0, 2, 0, 2)
        act_lay.setSpacing(8)
        act_lay.addStretch()
        apply_btn = QPushButton("应用")
        apply_btn.setObjectName("primary")          # 主行动钮（主行动蓝）
        apply_btn.setToolTip("应用当前选中效果及其参数（无运行引擎时发出 effect_applied 信号）")
        apply_btn.setFixedHeight(26)
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(self._on_apply)
        act_lay.addWidget(apply_btn)
        randomize_btn = QPushButton("随机")
        randomize_btn.setStyleSheet(_BTN)
        # ★ R183 W2-FX #15：默认走「当前值 ±30% 微扰」（工具栏「微扰」可切回全域随机）
        randomize_btn.setToolTip("把当前效果的数值参数在当前值附近 ±30% 微扰（关掉工具栏"
                                 "「微扰」= 全域随机；两者都走原有参数写入路径）")
        randomize_btn.setFixedHeight(26)
        randomize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        randomize_btn.clicked.connect(lambda: _w2fx.randomize_or_perturb(self))
        act_lay.addWidget(randomize_btn)
        reset_btn = QPushButton("重置")
        reset_btn.setStyleSheet(_BTN)
        reset_btn.setToolTip("把当前效果的全部参数恢复为预设默认值")
        reset_btn.setFixedHeight(26)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_current_params)
        act_lay.addWidget(reset_btn)
        right_lay.addWidget(action_row)

        # 状态标签
        self._status_lbl = QLabel("就绪")
        self._status_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;padding:2px 4px;")
        right_lay.addWidget(self._status_lbl)

        selection_splitter.setStretchFactor(0, 0)
        selection_splitter.setStretchFactor(1, 1)
        selection_splitter.setSizes([150, 290])   # C4：快捷列 150 / 预设卡列 290

        # 主分栏：左=效果选择(quick+列表)，右=效果调整。普通 QSplitter，
        # 分隔条可自由拖动、两侧均不会被拖没（防止旧 dock-in-dock 右页消失/拖不动的 bug）。
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.setObjectName("effect_main_split")
        main_split.setChildrenCollapsible(False)
        main_split.setHandleWidth(7)
        main_split.setOpaqueResize(True)
        main_split.setStyleSheet(
            "QSplitter#effect_main_split::handle{background:#3D3D3D;width:7px;margin:0 1px;}"
            "QSplitter#effect_main_split::handle:hover{background:#4772B3;}"
        )
        # 下调过大的最小宽度，使窄侧栏也能容纳并自由拖动
        selection_splitter.setMinimumWidth(240)   # C4：修前 180（快捷列 140 后卡列只剩 40）
        right.setMinimumWidth(320)
        main_split.addWidget(selection_splitter)
        main_split.addWidget(right)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([440, 640])   # C4：修前左栏 300 → 预设卡列被挤到 ~160px、名称截成 2 字
        self._main_split = main_split
        self._dock_window = None
        self._selection_dock = None
        self._adjust_dock = None
        root.addWidget(main_split, 1)
        # ★ R183 W2-FX：一次性接线（代理过滤层/轨迹旁路/悬停试听/缩略定时器）
        _w2fx.install(self)
        _fx_workspace.install(self)

    # ── 预设列表填充 ─────────────────────────────────────────────────────────

    def _populate_list(self, filter_text: str = "", cat: str = "",
                       top: str = ""):
        eng = get_engine()
        presets = list(eng.presets.values())

        # ★ R180 两级分类：top=一级大类键（""=全部 / TOP_RECENT=最近使用）；
        #   有搜索词时跨全部大类检索（命中自动展开分组），清空后还原原筛选。
        # ★ R183 批 A：用户预设注入 + 收藏表（见 `_user_state_for_list`）。
        ft = str(filter_text or "").strip()
        bar = getattr(self, "_cat_bar", None)
        favorites = _user_state_for_list(eng, bar)
        if bar is not None:
            bar.set_recent_count(len(order_recent(
                eng.presets.values(), getattr(self, "_effect_history", []))))
            # ★ R182：搜索态一致性——query 非空时筛选被旁路（跨全库检索），
            #   bar 如实禁用并标注"全库搜索中"，退出搜索自动恢复原筛选。
            bar.set_search_active(bool(ft))
            # ★ R183 批 A：收藏归零会把分类条静默拉回「全部」→ 以 bar 为准读回，
            #   否则这里会拿着调用方传入的过期 "fav" 排出一个必然为空的视图。
            top = bar.top_key()
        presets, group_fn, sort_key = plan_preset_rows(
            presets, top=top or "", sub=cat or "", query=ft,
            history=getattr(self, "_effect_history", []), favorites=favorites)

        # 更新 model（分组 + 类别标题由 model 内部完成）
        self._preset_model.set_presets(presets, group_fn=group_fn, sort_key=sort_key)
        self._visible_preset_ids = {p.id for p in presets}

        total = len(presets)
        self._count_lbl.setText(f"{total} 个预设" + (" · 全库搜索" if ft else ""))

        # 空态切换（§10.6）：无结果时隐藏列表、显示 EmptyState 占位
        empty = total == 0
        empty_state = getattr(self, "_list_empty_state", None)
        if empty_state is not None:
            try:
                empty_state.setVisible(empty)
                self._preset_list.setVisible(not empty)
            except Exception:
                logger.debug("effect_browser: failed to toggle list empty state")
                pass

        # 恢复选中状态
        _restore_selected_preset(self)

        self._refresh_quick_effect_lists()

    def _on_search_text_changed(self, *_):
        # 防抖:重启计时器,停止输入 220ms 后才真正 _filter_list(避免逐键重建全部 widget)。
        # ★ R183 W2-FX #16：防抖期间先做一次**即时收窄**（视图行隐藏，不重建模型），
        #   到点后仍由 _filter_list 做全量权威重建（跨大类检索语义不变）。
        _w2fx.apply_instant_filter(self, self._search.text())
        if hasattr(self, "_search_debounce"):
            self._search_debounce.start()
        else:
            self._filter_list()

    def _filter_list(self):
        bar = getattr(self, "_cat_bar", None)
        self._populate_list(
            filter_text=self._search.text(),
            cat=self._cat_combo.currentData() or "",
            top=bar.top_key() if bar is not None else "",
        )
        # ★ R183 W2-FX：#12/#10/#14 收口（不可见/热度/缩略缓存）
        _w2fx.refresh_after_populate(self)

    # ── 收藏星标 / 预设右键菜单（★ R183 批 A；逻辑在模块级，类内只接线）──────

    def _on_star_clicked(self, preset_id: str):
        _toggle_favorite_and_refresh(self, preset_id)

    def _on_preset_menu(self, preset_id: str, global_pos):
        _show_preset_menu(self, preset_id, global_pos)

    def _on_save_user_preset(self):
        _save_current_as_user_preset(self, None)

    def _on_recall_fade_changed(self, value):
        """召回过渡时长变更（★ R183 批 A）：写用户偏好 + 持久化到工程 state。"""
        _set_recall_fade_pref(self, value)

    # ── 预设选中 ─────────────────────────────────────────────────────────────

    def _on_preset_click(self, preset_id: str, *, save_current=True):
        # 先保存当前预设的 UI 参数，避免切到别的预设时丢失
        if save_current:
            try:
                self._save_current_group_ui_state()
            except Exception:
                logger.debug("effect_browser: failed to save group UI state before preset click")
        self._selected_id = preset_id
        self._slice_context_key = ""
        self._manual_slice_frame_cache = {}

        # 同步列表选中（阻止信号循环）
        if hasattr(self, "_preset_list") and self._preset_list is not None:
            sm = self._preset_list.selectionModel()
            if sm is not None:
                sm.blockSignals(True)
                for row in range(self._preset_model.rowCount()):
                    idx = self._preset_model.index(row)
                    if idx.data(Qt.ItemDataRole.UserRole) == preset_id:
                        self._preset_list.setCurrentIndex(idx)
                        break
                sm.blockSignals(False)

        self._remember_effect_history(preset_id)
        self._load_preset_params(preset_id)
        self._update_slice_key_label()
        self._update_transport_state()

    def _on_list_current_changed(self, current: QModelIndex, previous: QModelIndex):
        """Selection-model slot — syncs the browser to the selected preset."""
        if not current.isValid():
            return
        if current.data(_ItemTypeRole) == "header":
            # 不可选中标题行，恢复上一个选中项
            sm = self._preset_list.selectionModel()
            if sm is not None and previous.isValid():
                sm.blockSignals(True)
                self._preset_list.setCurrentIndex(previous)
                sm.blockSignals(False)
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        if pid:
            self._on_preset_click(pid)

    def _is_preset_visible(self, preset_id: str) -> bool:
        """Check whether *preset_id* appears in the current filtered model."""
        return preset_id in getattr(self, "_visible_preset_ids", set())

    def _effect_display_name(self, preset_id: str) -> str:
        try:
            preset = get_engine().get_preset(preset_id)
            if preset:
                return str(preset.name)
        except Exception:
            logger.debug("effect_browser: failed to get display name for preset %s", preset_id)
            pass
        return str(preset_id)

    def _effect_item_icon(self, preset_id: str):
        try:
            preset = get_engine().get_preset(preset_id)
            if preset:
                # §10.5 图标语言统一：正在使用/历史列表同用 16px 语义 glyph
                return QIcon(_card_glyph_pixmap(effect_icon_name_for_preset(preset)))
        except Exception:
            logger.debug("effect_browser: failed to create effect item icon for %s", preset_id)
            pass
        return None

    def _remember_effect_history(self, preset_id: str):
        preset_id = str(preset_id or "")
        if not preset_id:
            return
        old = list(getattr(self, "_effect_history", []) or [])
        # ★ R183 W2-FX #10：使用计数（历史只存序，热度需要**次数**；持久化在用户档）。
        #   只在"换到另一个预设"时 +1：音频桥/调度台对同一预设的连续重放不重复计数，
        #   否则演出跑到一半基数就失真了。
        if not old or str(old[0]) != preset_id:
            _w2fx.note_usage(preset_id)
        hist = [pid for pid in old if pid != preset_id]
        hist.insert(0, preset_id)
        self._effect_history = hist[:24]
        bar = getattr(self, "_cat_bar", None)
        if bar is not None:
            bar.set_recent_count(len(order_recent(get_engine().presets.values(), hist)))
        self._refresh_quick_effect_lists()

    def _refresh_quick_effect_lists(self):
        active_list = getattr(self, "_active_effect_list", None)
        history_list = getattr(self, "_history_effect_list", None)
        if active_list is None or history_list is None:
            return
        active_seen = set()
        active_items = []
        for (_track_type, group_key), state in list(getattr(self, "_external_states", {}).items()):
            if not isinstance(state, dict):
                continue
            preset_id = str(state.get("preset_id", "") or "")
            if not preset_id or preset_id in active_seen:
                continue
            active_seen.add(preset_id)
            ch_text = "+".join(str(ch) for ch in (state.get("ch_types", []) or []) if str(ch))
            group_text = _group_label_from_ids(state.get("group_ids", []))
            detail = " / ".join(x for x in (ch_text, group_text) if x)
            active_items.append((preset_id, detail))

        active_list.blockSignals(True)
        active_list.clear()
        for preset_id, detail in active_items:
            text = self._effect_display_name(preset_id)
            if detail:
                text = f"{text}\n{detail}"
            item = QListWidgetItem(text)
            icon = self._effect_item_icon(preset_id)
            if icon is not None:
                item.setIcon(icon)
            item.setSizeHint(QSize(0, 48 if detail else 42))
            item.setData(Qt.ItemDataRole.UserRole, preset_id)
            active_list.addItem(item)
        active_list.blockSignals(False)

        # ★ R178：自定义 effect 页实时状态栏已随该页删除（原 _custom_run_status）。

        history_list.blockSignals(True)
        history_list.clear()
        for preset_id in _w2fx.known_preset_ids(getattr(self, "_effect_history", []))[:24]:
            item = QListWidgetItem(self._effect_display_name(preset_id))
            icon = self._effect_item_icon(preset_id)
            if icon is not None:
                item.setIcon(icon)
            item.setSizeHint(QSize(0, 42))
            item.setData(Qt.ItemDataRole.UserRole, preset_id)
            history_list.addItem(item)
        history_list.blockSignals(False)

    def _on_quick_effect_clicked(self, item: QListWidgetItem):
        preset_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
        if not preset_id:
            return
        self._on_preset_click(str(preset_id))

    def _clear_param_columns(self):
        channel_lay = getattr(self, "_param_channel_lay", None)
        if channel_lay is not None:
            while channel_lay.count() > 1:
                item = channel_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            frame = getattr(self, "_param_channel_frame", None)
            if frame is not None:
                frame.setVisible(False)
        for widget in list(getattr(self, "_param_span_widgets", []) or []):
            try:
                self._param_lay.removeWidget(widget)
            except Exception:
                logger.debug("effect_browser: failed to remove param span widget from layout")
                pass
            widget.deleteLater()
        self._param_span_widgets = []
        self._param_span_row = 1
        for col in list(getattr(self, "_param_columns", []) or []):
            while col.count() > 1:
                item = col.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        self._param_next_column = 0
        container = getattr(self, "_param_container", None)
        if container is not None:
            container.setVisible(False)

    def _add_param_widget_to_columns(self, widget: QWidget, span: bool = False):
        # ★ 摇杆绑定请求（全局参数）
        if hasattr(widget, "bind_requested"):
            widget.bind_requested.connect(self._on_param_bind_requested)
            widget.bind_menu_requested.connect(self._on_param_bind_menu)
        container = getattr(self, "_param_container", None)
        if container is not None:
            container.setVisible(True)
        cols = list(getattr(self, "_param_columns", []) or [])
        if not cols:
            try:
                self._param_lay.addWidget(widget, self._param_lay.rowCount(), 0)
            except Exception:
                logger.debug("effect_browser: failed to add param widget at row, using fallback")
                self._param_lay.addWidget(widget)
            return
        if span:
            box = QWidget()
            _bk_scope_qss(box, "background:transparent;border:none;")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(widget)
            row = int(getattr(self, "_param_span_row", 1) or 1)
            self._param_lay.addWidget(box, row, 0, 1, 2)
            self._param_span_widgets.append(box)
            self._param_span_row = row + 1
            return
        idx = int(getattr(self, "_param_next_column", 0) or 0) % len(cols)
        cols[idx].insertWidget(max(0, cols[idx].count() - 1), widget)
        self._param_next_column = idx + 1

    def _add_channel_widget_to_column(self, widget: QWidget):
        channel_lay = getattr(self, "_param_channel_lay", None)
        if channel_lay is None:
            self._add_param_widget_to_columns(widget, span=True)
            return
        channel_lay.insertWidget(max(0, channel_lay.count() - 1), widget)
        frame = getattr(self, "_param_channel_frame", None)
        if frame is not None:
            was_hidden = frame.isHidden()
            frame.setVisible(True)
            side = getattr(self, "_adjust_side_split", None)
            if was_hidden and side is not None and side.count() >= 2:
                sizes = list(side.sizes())
                if len(sizes) >= 2 and sizes[1] < 260:
                    side.setSizes([320, 560])

    def _load_preset_params(self, preset_id: str):
        eng = get_engine()
        preset = eng.get_preset(preset_id)
        if not preset:
            return

        _w2fx.preset_switched(self, preset_id)  # R183 W2-FX: dirty/A-B reset
        _update_preset_badge(self, preset)
        # ★★ R141 卡顿修复：控件树**只由 `preset.params` 决定** —— 同一预设+同一参数结构
        #   已在页上时，整页重建是**等价但昂贵**的动作（实测单次 ~134ms：663×addWidget、
        #   414×setStyleSheet、99 个 QWidget）。dispatch 编辑语义路径一次调用连跑 3 遍
        #   （`load_state` → `_apply_param_values` → `_restore_running_effect_ui_state`），
        #   秒级卡顿即由此而来。参数值随后由 `_apply_param_values` 写入。
        sig = _param_tree_signature(preset)
        if _reuse_preset_param_widgets(self, preset_id, sig):
            _finish_preset_param_page(self)
            return

        # 清空旧参数控件
        self._clear_param_columns()
        self._param_widgets.clear()
        self._param_preset_id = ""     # 重建期间禁用复用（防重入误判，见 _reuse_preset_param_widgets）
        self._param_signature = ()     # 同上：重建完成后才落新签名
        self._channel_param_key = None
        self._channel_param_spec = None
        self._channel_form_specs = []
        self._channel_forms = []
        self._channel_forms_widget = None
        self._channel_forms_lay = None
        self._channel_add_combo = None
        self._channel_list_widget = None
        self._channel_stack = None

        # 生成新参数控件
        channel_spec = None
        non_channel_specs = []
        skip_keys = set()
        for spec in preset.params:
            if _is_global_speed_param(spec):
                continue
            if str(getattr(spec, "key", "") or "") == "spatial_scope":
                continue
            if channel_spec is None and _is_channel_choice_param(spec):
                channel_spec = spec
                continue
            non_channel_specs.append(spec)
        if not any(getattr(spec, "key", "") == "_joy_amp_axis" for spec in non_channel_specs):
            non_channel_specs.append(_JOY_AMP_AXIS_PARAM)

        # Store unified slot info for per-form rendering
        self._current_unified_slots = list(getattr(preset, "unified_slots", []) or [])
        self._current_preset = preset

        if channel_spec is None:
            # ── No channel selector: render unified slots as a top span ──
            if self._current_unified_slots and preset.target_channels:
                target_ch = preset.target_channels[0]
                unified_group = self._build_unified_slots_group(target_ch)
                if unified_group is not None:
                    self._add_param_widget_to_columns(unified_group, span=True)
            # ── Regular params：按 颜色/空间/时序/音频 PanelSection 折叠分组
            #    （restyle_spec §10.2）。仅呈现分组，信号与读写路径不变。
            sections: Dict[str, tuple] = {}
            section_order: List[str] = []

            def _ensure_section(group: str):
                if group not in sections:
                    title = "基础参数" if group == "基础" else f"{group}参数"
                    sec = _BkPanelSection(title)
                    inner = QVBoxLayout()
                    inner.setContentsMargins(0, 0, 0, 0)
                    inner.setSpacing(4)
                    sec.add_layout(inner)
                    sections[group] = (sec, inner)
                    section_order.append(group)
                return sections[group][1]

            for i, spec in enumerate(non_channel_specs):
                if spec.key in skip_keys:
                    continue
                next_spec = non_channel_specs[i + 1] if i + 1 < len(non_channel_specs) else None
                range_pair = _range_param_pair_specs(spec, next_spec)
                if range_pair is not None:
                    low_spec, high_spec = range_pair
                    pw = RangeParamWidget(low_spec, high_spec)
                    pw.value_changed.connect(self._on_param_changed)
                    _ensure_section(_param_group_of(low_spec)).addWidget(pw)
                    self._param_widgets[low_spec.key] = pw
                    self._param_widgets[high_spec.key] = pw
                    skip_keys.add(next_spec.key)
                    continue
                pw = ParamWidget(spec, project=self._project_object())
                pw.value_changed.connect(self._on_param_changed)
                _inject_channel_choices(self, pw, spec)
                # 分组后 widget 不再直接进入 columns 容器，绑定信号在此补齐
                # （等价于原 _add_param_widget_to_columns 的 hasattr 连接分支）。
                if hasattr(pw, "bind_requested"):
                    pw.bind_requested.connect(self._on_param_bind_requested)
                    pw.bind_menu_requested.connect(self._on_param_bind_menu)
                _ensure_section(_param_group_of(spec)).addWidget(pw)
                self._param_widgets[spec.key] = pw
            for g in section_order:
                self._add_param_widget_to_columns(sections[g][0])
        else:
            self._channel_param_key = channel_spec.key
            self._channel_param_spec = channel_spec
            self._channel_form_specs = list(non_channel_specs)
            self._build_channel_forms_ui(channel_spec)
        # 控件树建成 → 落复用标记（预设 id + 参数结构签名，见 `_reuse_preset_param_widgets`）
        self._param_preset_id = str(preset_id)
        self._param_signature = sig
        _finish_preset_param_page(self)

        # 通道输出不再由外层复选框控制；优先使用参数区的 ch_type/ch，
        # 没有单通道参数时使用 preset.target_channels。

    def _build_unified_slots_group(self, ch_type: str) -> Optional[QWidget]:
        """Build a collapsible PanelSection holding unified slots for *ch_type*.

        Returns None when there are no unified slots registered for this type.
        呈现为 widget_kit.PanelSection 折叠分区（restyle_spec §10.2），
        信号仍连接 _on_param_changed，槽位读写逻辑不变。
        """
        slots = getattr(self, "_current_unified_slots", None)
        if not slots:
            return None
        semantics = SlotRegistry.list_for_channel_filtered(ch_type, slots)
        if not semantics:
            return None

        group = _BkPanelSection("统一参数")
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        for sem in semantics:
            spec = _slot_to_paramspec(sem)
            pw = ParamWidget(spec, project=self._project_object())
            _inject_channel_choices(self, pw, spec)
            pw.value_changed.connect(self._on_param_changed)
            lay.addWidget(pw)
            self._param_widgets[spec.key] = pw
        group.add_layout(lay)
        return group

    def _rebuild_unified_slots_for_form(self, form: dict, ch_type: str):
        """Remove and re-create the unified slots section inside a channel form.

        Called when the user changes the channel type combo.
        """
        layout = form.get("layout")
        old_group = form.get("unified_group")
        if old_group is not None:
            try:
                if layout is not None:
                    layout.removeWidget(old_group)
                old_group.deleteLater()
            except Exception:
                logger.debug("effect_browser: failed to remove old unified slots group")
            form["unified_group"] = None
        # Build new group for the current channel type
        new_group = self._build_unified_slots_group(ch_type)
        if new_group is not None and layout is not None:
            layout.insertWidget(1, new_group)
        form["unified_group"] = new_group

    def _build_channel_forms_ui(self, channel_spec: ParamSpec):
        group = QGroupBox(str(getattr(channel_spec, "label", "") or "Channels"))
        group.setStyleSheet(
            f"QGroupBox{{color:{FG_DIM};font-size:10px;border:1px solid #242424;"
            f"border-radius:4px;margin-top:6px;padding-top:8px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:8px;top:0px;}}")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        sheet_lbl = QLabel("Sheets")
        sheet_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setFixedSize(26, 24)
        add_btn.setStyleSheet(_BTN)
        add_btn.clicked.connect(lambda: self._add_channel_form_from_combo())
        add_row.addWidget(sheet_lbl)
        add_row.addStretch()
        add_row.addWidget(add_btn)
        outer.addLayout(add_row)

        forms_widget = QWidget()
        forms_lay = QVBoxLayout(forms_widget)
        forms_lay.setContentsMargins(0, 0, 0, 0)
        forms_lay.setSpacing(6)

        channel_list = QListWidget()
        channel_list.setMinimumHeight(72)
        channel_list.setMaximumHeight(122)
        channel_list.setFlow(QListWidget.Flow.LeftToRight)
        channel_list.setWrapping(True)
        channel_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        channel_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        channel_list.setStyleSheet(
            f"QListWidget{{background:{BG_HEADER};color:{FG_MAIN};"
            "border:1px solid #3D3D3D;border-radius:4px;}"
            "QListWidget::item{padding:5px 6px;}"
            f"QListWidget::item:selected{{background:{BG_ITEM_SEL};color:{_th.TEXT_SELECTED_FG};}}"
            f"QListWidget::item:hover{{background:{BG_ITEM_HOV};}}")
        channel_list.currentRowChanged.connect(self._on_channel_form_selected)
        forms_lay.addWidget(channel_list)

        stack = QStackedWidget()
        stack.setMinimumWidth(260)
        stack.setStyleSheet(
            f"QStackedWidget{{background:{BG_ITEM};border:1px solid #3D3D3D;"
            "border-radius:4px;}")
        forms_lay.addWidget(stack, 1)
        outer.addWidget(forms_widget)

        self._channel_forms_widget = forms_widget
        self._channel_forms_lay = forms_lay
        self._channel_add_combo = None
        self._channel_list_widget = channel_list
        self._channel_stack = stack
        self._add_channel_widget_to_column(group)
        self._add_channel_form(_operating_preferences.default_channel(
            self, channel_spec, _module_channel_choices(self)))

    def _add_channel_form_from_combo(self):
        ch = self._next_available_channel_type()
        form = self._add_channel_form(str(ch or ""))
        self._set_active_channel_form(form)
        self._on_param_changed(str(self._channel_param_key or ""), self._capture_channel_forms())

    def _next_available_channel_type(self) -> str:
        allowed = _module_channel_choices(self)
        used = {self._channel_form_type(form) for form in self._channel_forms or []}
        for ch in allowed:
            if ch not in used:
                return ch
        active = self._active_channel_form()
        if active:
            return self._channel_form_type(active)
        return allowed[0] if allowed else "dimmer"

    def _add_channel_form(self, ch_type: str, params: Optional[dict] = None):
        ch_type = str(ch_type or "")
        allowed = _module_channel_choices(self)
        ch_type = _operating_preferences.form_channel(ch_type, allowed, params)
        if params is None:
            for form in self._channel_forms:
                if self._channel_form_type(form) == ch_type:
                    return form
        if params is None and len(self._channel_forms) >= len(allowed):
            return self._active_channel_form() or (self._channel_forms[0] if self._channel_forms else None)
        container = QFrame()
        container.setStyleSheet(
            f"QFrame{{background:{BG_ITEM};border:1px solid #3D3D3D;border-radius:4px;}}")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        combo = QComboBox()
        combo.setStyleSheet(_COMBO)
        _fill_owned_combo(combo, self, allowed)   # 显示名走工程真名（功能通道）
        _operating_preferences.select_channel(combo, ch_type)
        del_btn = QToolButton()
        del_btn.setText("-")
        del_btn.setFixedSize(24, 22)
        del_btn.setStyleSheet(_BTN)
        title = QLabel("Channel")
        title.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        head.addWidget(title)
        head.addWidget(combo, 1)
        head.addWidget(del_btn)
        lay.addLayout(head)

        widgets = {}
        specs = list(self._channel_form_specs or [])
        skip_keys = set()
        for i, spec in enumerate(specs):
            if spec.key in skip_keys:
                continue
            next_spec = specs[i + 1] if i + 1 < len(specs) else None
            range_pair = _range_param_pair_specs(spec, next_spec)
            if range_pair is not None:
                low_spec, high_spec = range_pair
                pw = RangeParamWidget(low_spec, high_spec)
                pw.value_changed.connect(self._on_param_changed)
                lay.addWidget(pw)
                widgets[low_spec.key] = pw
                widgets[high_spec.key] = pw
                skip_keys.add(next_spec.key)
                continue
            pw = ParamWidget(spec, project=self._project_object())
            pw.value_changed.connect(self._on_param_changed)
            _inject_channel_choices(self, pw, spec)
            pw.set_bind_channel(ch_type)   # ★ 每通道页独立绑定
            pw.bind_requested.connect(self._on_param_bind_requested)
            pw.bind_menu_requested.connect(self._on_param_bind_menu)
            lay.addWidget(pw)
            widgets[spec.key] = pw
        lay.addStretch()

        # Inject unified slots group (if any) between header and param widgets
        unified_group = self._build_unified_slots_group(ch_type)

        item = QListWidgetItem(ch_type)
        item.setData(Qt.ItemDataRole.UserRole, ch_type)
        form = {
            "widget": container,
            "combo": combo,
            "params": widgets,
            "last_ch": ch_type,
            "item": item,
            "layout": lay,
            "unified_group": unified_group,
            "playing": False,
            "speed_factor": 1.0,
            "virtual_t": 0.0,
            "last_real_t": 0.0,
        }
        if unified_group is not None:
            # Insert after header (index 0), before param widgets
            lay.insertWidget(1, unified_group)
        self._channel_forms.append(form)
        if isinstance(self._channel_list_widget, QListWidget):
            self._channel_list_widget.addItem(item)
        if isinstance(self._channel_stack, QStackedWidget):
            self._channel_stack.addWidget(container)
        _operating_preferences.connect_channel_choice(self, combo, form)
        del_btn.clicked.connect(lambda _=False, f=form: self._remove_channel_form(f))
        if params is not None:
            _form_author.restore(self, form, params)
        self._update_channel_form_item(form)
        self._set_active_channel_form(form)
        return form

    def _remove_channel_form(self, form: dict):
        if len(self._channel_forms) <= 1:
            return
        ch_removed = self._channel_form_type(form)
        group_key = self._current_live_selection_key()
        for key, state in list(getattr(self, "_external_states", {}).items()):
            if len(key) < 2 or key[1] != group_key:
                continue
            if not isinstance(state, dict) or state.get("preset_id") != self._selected_id:
                continue
            st_ch = [str(c) for c in (state.get("ch_types", []) or []) if str(c)]
            if st_ch == [ch_removed]:
                self._external_states.pop(key, None)
        was_active = False
        removed_row = -1
        if isinstance(self._channel_stack, QStackedWidget):
            was_active = self._channel_stack.currentWidget() is form.get("widget")
        if form in self._channel_forms:
            removed_row = self._channel_forms.index(form)
            self._channel_forms.remove(form)
        item = form.get("item")
        if isinstance(self._channel_list_widget, QListWidget) and isinstance(item, QListWidgetItem):
            row = self._channel_list_widget.row(item)
            if row >= 0:
                self._channel_list_widget.takeItem(row)
        widget = form.get("widget")
        if widget is not None:
            if isinstance(self._channel_stack, QStackedWidget):
                self._channel_stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        if was_active and self._channel_forms:
            next_row = min(max(removed_row, 0), len(self._channel_forms) - 1)
            self._set_active_channel_form(self._channel_forms[next_row])
        else:
            self._sync_transport_controls_to_active_form()
        self._on_param_changed(str(self._channel_param_key or ""), self._capture_channel_forms())

    def _on_channel_form_type_changed(self, form: dict):
        old_ch_for_state = str(form.get("last_ch", "") or "")
        ch = self._channel_form_type(form)
        used_by_others = {
            self._channel_form_type(existing)
            for existing in self._channel_forms
            if existing is not form
        }
        if ch in used_by_others:
            combo = form.get("combo")
            allowed = _module_channel_choices(self)
            old_ch = str(form.get("last_ch", "") or "")
            replacement = old_ch if old_ch and old_ch not in used_by_others else next(
                (candidate for candidate in allowed if candidate not in used_by_others), "")
            if isinstance(combo, QComboBox) and replacement:
                idx = combo.findData(replacement)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
                    form["last_ch"] = replacement
            elif len(self._channel_forms) > 1:
                self._remove_channel_form(form)
                return
        else:
            form["last_ch"] = ch
        if old_ch_for_state and old_ch_for_state != ch:
            # Rebuild unified slots for the new channel type
            self._rebuild_unified_slots_for_form(form, ch)
            group_key = self._current_live_selection_key()
            for key, state in list(getattr(self, "_external_states", {}).items()):
                if len(key) < 2 or key[1] != group_key:
                    continue
                if not isinstance(state, dict) or state.get("preset_id") != self._selected_id:
                    continue
                st_ch = [str(c) for c in (state.get("ch_types", []) or []) if str(c)]
                if st_ch == [old_ch_for_state]:
                    self._external_states.pop(key, None)
        self._update_channel_form_item(form)
        self._set_active_channel_form(form)
        self._on_param_changed(str(self._channel_param_key or ""), self._capture_channel_forms())

    def _channel_form_type(self, form: dict) -> str:
        combo = form.get("combo")
        if isinstance(combo, QComboBox):
            data = combo.currentData()
            return str(data if data is not None else combo.currentText())
        return str((form or {}).get("last_ch", "") or "")

    def _resolved_joy_amp_axis_for_channel(self, ch_type: str) -> str:
        norm = _norm_channel_type(ch_type)
        if norm == "motor_z":
            return "Z轴"
        if norm == "motor_y":
            return "Y轴"
        return "X轴"

    def _joy_amp_axis_display_for_form(self, form: dict) -> str:
        ch = self._channel_form_type(form)
        axis = "自动"
        pw = (form.get("params", {}) or {}).get("_joy_amp_axis")
        if pw is not None:
            try:
                axis = str(pw.get_value() or "自动")
            except Exception:
                logger.debug("effect_browser: failed to get joy amp axis value from param widget")
                axis = "自动"
        if axis == "自动":
            axis = f"自动({self._resolved_joy_amp_axis_for_channel(ch)})"
        return f"摇杆:{axis}"

    def _joy_amp_axis_for_channel(self, ch_type: str, params: Optional[dict] = None) -> str:
        """读取通道的摇杆绑定选择（自动/X轴/Y轴/Z轴/关闭）。
        优先读通道表单 _joy_amp_axis 控件；无匹配表单时回退顶层参数。"""
        ch = str(ch_type or "")
        for form in list(self._channel_forms or []):
            if self._channel_form_type(form) == ch:
                pw = (form.get("params", {}) or {}).get("_joy_amp_axis")
                if pw is not None:
                    try:
                        return str(pw.get_value() or "自动")
                    except Exception:
                        logger.debug("effect_browser: failed to read joy amp axis from channel form")
                        return "自动"
                return "自动"
        val = (params or {}).get("_joy_amp_axis")
        return str(val or "自动") if val is not None else "自动"

    def _strip_joy_amp_if_closed(self, params: dict, ch_type: str, *, runtime_owner=None) -> dict:
        """摇杆绑定=关闭 的通道：剔除幅度覆盖键，使该通道在幅度控制模式下不被摇杆控制。"""
        return _target_scope.strip_joy_amp(self, params, ch_type, runtime_owner, _JOY_AMP_OVERRIDE_KEYS)

    def _update_channel_form_item(self, form: dict):
        item = form.get("item")
        ch = self._channel_form_type(form)
        if isinstance(item, QListWidgetItem):
            item.setText(f"{ch}  [{self._joy_amp_axis_display_for_form(form)}]")
            item.setData(Qt.ItemDataRole.UserRole, ch)

    def _set_active_channel_form(self, form: Optional[dict]):
        if not form:
            return
        widget = form.get("widget")
        item = form.get("item")
        if isinstance(self._channel_stack, QStackedWidget) and widget is not None:
            idx = self._channel_stack.indexOf(widget)
            if idx >= 0:
                self._channel_stack.setCurrentIndex(idx)
        if isinstance(self._channel_list_widget, QListWidget) and isinstance(item, QListWidgetItem):
            row = self._channel_list_widget.row(item)
            if row >= 0 and self._channel_list_widget.currentRow() != row:
                self._channel_list_widget.setCurrentRow(row)
        _width_controls.sync_form(self)
        self._slice_context_key = ""
        self._manual_slice_frame_cache = {}
        self._update_slice_key_label()

    def _active_channel_form(self) -> Optional[dict]:
        if not self._channel_forms:
            return None
        if isinstance(self._channel_list_widget, QListWidget):
            row = self._channel_list_widget.currentRow()
            if 0 <= row < len(self._channel_forms):
                return self._channel_forms[row]
        if isinstance(self._channel_stack, QStackedWidget):
            widget = self._channel_stack.currentWidget()
            for form in self._channel_forms:
                if form.get("widget") is widget:
                    return form
        return self._channel_forms[0]

    def _has_channel_forms(self) -> bool:
        return bool(self._channel_param_key and self._channel_forms)

    def _has_active_channel_playback(self) -> bool:
        return any(bool(form.get("playing", False)) for form in self._channel_forms or [])

    def _running_keys_for_current_selection(self) -> list:
        from .effect_owner_edit import running_keys
        form = self._active_channel_form() if self._has_channel_forms() else None
        active_ch = self._channel_form_type(form) if form is not None else ""
        return running_keys(self, active_ch)

    def _current_selection_effect_active(self) -> bool:
        return bool(self._running_keys_for_current_selection())

    def _manual_slice_context_key(self) -> str:
        preset = str(self._selected_id or "effect").replace(":", "_").replace("@", "_")
        group_key = self._current_live_selection_key() or "all"
        ch = ""
        if self._has_channel_forms():
            form = self._active_channel_form()
            ch = self._channel_form_type(form) if form is not None else ""
        if not ch:
            ch_types = self._get_selected_ch_types()
            ch = "+".join(sorted(str(v) for v in (ch_types or []) if str(v))) or "all"
        return f"{preset}@{group_key}@{ch}"

    def _sync_manual_slice_context(self) -> str:
        ctx = self._manual_slice_context_key()
        if ctx != getattr(self, "_slice_context_key", ""):
            old_ctx = getattr(self, "_slice_context_key", "")
            if old_ctx:
                self._manual_slice_indices_by_context[old_ctx] = int(
                    getattr(self, "_manual_slice_index", 0) or 0)
                cache = getattr(self, "_manual_slice_frame_cache", {}) or {}
                if cache:
                    self._manual_slice_frame_cache_by_context[old_ctx] = dict(cache)
            self._slice_context_key = ctx
            self._manual_slice_index = int(
                self._manual_slice_indices_by_context.get(ctx, 0) or 0)
            self._manual_slice_frame_cache = dict(
                self._manual_slice_frame_cache_by_context.get(ctx, {}) or {})
        return ctx

    def _clear_current_manual_slice_cache(self):
        ctx = self._sync_manual_slice_context()
        self._stop_slice_fade()
        self._slice_fade_last_written = {}
        self._manual_slice_frame_cache.clear()
        self._manual_slice_frame_cache_by_context.pop(ctx, None)
        self._manual_slice_latched = self._slice_key is not None

    def _current_manual_slice_speed(self) -> float:
        if self._has_channel_forms():
            form = self._active_channel_form()
            if form is not None:
                try:
                    return float(form.get("speed_factor", 1.0) or 0.0)
                except Exception:
                    logger.debug("effect_browser: failed to parse speed factor from channel form")
                    return 1.0
        try:
            return float(getattr(self, "_global_speed_factor", 1.0) or 0.0)
        except Exception:
            logger.debug("effect_browser: failed to parse global speed factor")
            return 1.0

    def _manual_slice_speed_param_keys(self, params: Dict[str, Any]) -> List[str]:
        keys = []
        for key in (params or {}).keys():
            key_s = str(key)
            key_l = key_s.lower()
            if key_l.startswith("_") or key_l == "speed_factor":
                continue
            if key_l in {"speed", "rate", "h_speed", "s_speed", "v_speed"}:
                keys.append(key_s)
            elif key_l.endswith("_speed") or key_l.endswith("speed"):
                keys.append(key_s)
        return keys

    def _current_manual_slice_params_for_speed_check(self) -> Dict[str, Any]:
        params = dict(self._get_current_params() or {})
        if not self._has_channel_forms():
            return params
        form = self._active_channel_form()
        if form is None:
            return params
        params.pop("_channel_forms", None)
        ch = self._channel_form_type(form)
        if ch and self._channel_param_key:
            params[self._channel_param_key] = ch
        seen_widgets = set()
        for key, widget in (form.get("params", {}) or {}).items():
            if isinstance(widget, RangeParamWidget):
                if id(widget) in seen_widgets:
                    continue
                seen_widgets.add(id(widget))
                params.update(widget.get_values())
            else:
                params[key] = widget.get_value()
        return params

    def _current_effect_motion_speed_is_zero(self) -> bool:
        params = self._current_manual_slice_params_for_speed_check()
        speed_keys = self._manual_slice_speed_param_keys(params)
        if not speed_keys:
            return False
        checked = []
        for key in speed_keys:
            try:
                checked.append(abs(float(params.get(key) or 0.0)) <= 1e-6)
            except Exception:
                logger.debug("effect_browser: failed to parse speed param for manual slice check")
                pass
        return bool(checked) and all(checked)

    def _active_effect_count(self) -> int:
        return len(getattr(self, "_external_states", {}) or {})

    def _update_transport_state(self):
        self._update_play_button(self._current_selection_effect_active())
        _master_speed.sync(self)
        # ★★ R130 用户实报修复（存场景自动清台后窗格仍留在总线/监控区，下次存场景
        #   把上一次的效果一起抄走）：`stop_external` / `stop_external_type` / `_on_stop`
        #   三条停止路径**都不重报总线** → `_external_states` 已空、`_bus_seen_keys`
        #   却还记着旧格，差集删除永不触发 → 窗格永久残留。三条路径都收口到本函数，
        #   故这里补一次强制上报（幂等 + 内部节流，重复调用无副作用）。
        try:
            self.report_to_bus(force=True)
        except Exception:
            logger.debug("effect_browser: bus report on transport update failed",
                         exc_info=True)

    def _sync_transport_controls_to_active_form(self):
        if self._updating_speed_controls:
            return
        form = self._active_channel_form() if self._has_channel_forms() else None
        if not form:
            speed = float(getattr(self, "_global_speed_factor", 1.0) or 0.0)
            self._updating_speed_controls = True
            try:
                if hasattr(self, '_global_speed_sld'):
                    self._global_speed_sld.blockSignals(True)
                    if speed <= 0.0:
                        self._global_speed_sld.setValue(0)
                    else:
                        self._global_speed_sld.setValue(
                            int(_float_to_slider_pct(speed, 0.01, 16.0, True) * 1000))
                    self._global_speed_sld.blockSignals(False)
                if hasattr(self, '_global_speed_lbl'):
                    self._global_speed_lbl.setText(f"{speed:.2f}x")
                if hasattr(self, '_global_speed_spin'):
                    self._global_speed_spin.blockSignals(True)
                    sync_speed_spin(self._global_speed_spin, speed)
                    self._global_speed_spin.blockSignals(False)
            finally:
                self._updating_speed_controls = False
            self._update_transport_state()
            return
        speed = float(form.get("speed_factor", 1.0) or 0.0)
        self._updating_speed_controls = True
        try:
            if hasattr(self, '_global_speed_sld'):
                self._global_speed_sld.blockSignals(True)
                if speed <= 0.0:
                    self._global_speed_sld.setValue(0)
                else:
                    self._global_speed_sld.setValue(
                        int(_float_to_slider_pct(speed, 0.01, 16.0, True) * 1000))
                self._global_speed_sld.blockSignals(False)
            if hasattr(self, '_global_speed_lbl'):
                self._global_speed_lbl.setText(f"{speed:.2f}x")
            if hasattr(self, '_global_speed_spin'):
                self._global_speed_spin.blockSignals(True)
                sync_speed_spin(self._global_speed_spin, speed)
                self._global_speed_spin.blockSignals(False)
        finally:
            self._updating_speed_controls = False
        self._update_transport_state()

    def _update_play_button(self, playing: bool):
        if not hasattr(self, "_play_btn"):
            return
        if playing:
            self._play_btn.setText("暂停")
            # §11.1 净化：播放态成功绿改哑光 rgba(24,134,37,70) 底 + bright_success
            # 亮字（原饱和 SUCCESS_GREEN 白字填充块退役；hover 仅提底不换饱和色）
            self._play_btn.setStyleSheet(
                f"QPushButton{{background:rgba(24,134,37,70);color:{_th.BRIGHT_SUCCESS};"
                "border:1px solid rgba(86,182,91,110);border-radius:4px;padding:5px 14px;"
                "font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:rgba(24,134,37,110);"
                "border-color:#56B65B;}"
                "QPushButton:pressed{background:rgba(24,134,37,150);}")
        else:
            self._play_btn.setText("开始播放")
            self._play_btn.setStyleSheet(_BTN)
        _fx_workspace.refresh_transport(self)

    def _on_channel_form_selected(self, row: int):
        if not isinstance(self._channel_stack, QStackedWidget):
            return
        if 0 <= row < len(self._channel_forms):
            widget = self._channel_forms[row].get("widget")
            idx = self._channel_stack.indexOf(widget)
            if idx >= 0:
                self._channel_stack.setCurrentIndex(idx)
            self._sync_transport_controls_to_active_form()
            self._update_transport_state()

    def _capture_channel_forms(self) -> list:
        out = []
        for form in self._channel_forms or []:
            params = deepcopy(form.get("_unrepresented_params", {}))
            seen_widgets = set()
            for key, widget in (form.get("params", {}) or {}).items():
                if isinstance(widget, RangeParamWidget):
                    if id(widget) in seen_widgets:
                        continue
                    seen_widgets.add(id(widget))
                    params.update(widget.get_values())
                else:
                    params[key] = widget.get_value()
            out.append({
                "ch_type": self._channel_form_type(form),
                "params": _form_author.capture(self, form, params),
                "playing": bool(form.get("playing", False)),
                "speed_factor": float(form.get("speed_factor", 1.0) or 0.0),
            })
        return out

    def _apply_channel_form_values(self, form: dict, params: dict):
        if isinstance(params, dict):
            if "playing" in params:
                form["playing"] = bool(params.get("playing"))
            if "speed_factor" in params:
                try:
                    form["speed_factor"] = max(0.0, min(16.0, float(params.get("speed_factor"))))
                except Exception:
                    logger.debug("effect_browser: failed to parse speed factor in apply channel form values")
                    form["speed_factor"] = 1.0
            ch = self._channel_form_type(form)
            group_key = self._current_live_selection_key()
            for key, state in list(getattr(self, "_external_states", {}).items()):
                if len(key) < 2 or key[1] != group_key:
                    continue
                if not isinstance(state, dict) or state.get("preset_id") != self._selected_id:
                    continue
                st_ch = [str(c) for c in (state.get("ch_types", []) or []) if str(c)]
                if st_ch == [ch]:
                    state["speed_factor"] = float(form.get("speed_factor", 1.0) or 0.0)
        for key, val in _channel_form_restore_values(form, params).items():
            if key in {"playing", "speed_factor"}:
                continue
            pw = (form.get("params", {}) or {}).get(key)
            if pw is None:
                continue
            if isinstance(pw, RangeParamWidget):
                restore_pair_values(pw, params)
                continue
            ctrl = getattr(pw, "_ctrl", None)
            try:
                if isinstance(ctrl, _ParamNumSlider):
                    ctrl.set_out(float(val))       # 数值滑杆 语义值回填
                elif isinstance(ctrl, QDoubleSpinBox):
                    ctrl.setValue(float(val))
                elif isinstance(ctrl, QSpinBox):
                    ctrl.setValue(int(val))
                elif isinstance(ctrl, QCheckBox):
                    ctrl.setChecked(bool(val))
                elif isinstance(ctrl, WingDirectionToolbar):
                    ctrl.set_value(val)
                elif isinstance(ctrl, QComboBox):
                    idx = ctrl.findData(val)
                    if idx < 0 and isinstance(val, str):
                        idx = ctrl.findText(val)
                    if idx >= 0:
                        ctrl.setCurrentIndex(idx)
            except Exception:
                logger.debug("effect_browser: failed to restore channel form widget value")
                continue

    def _restore_channel_forms(self, forms: list):
        if self._channel_stack is None or self._channel_list_widget is None:
            return
        for form in list(self._channel_forms):
            widget = form.get("widget")
            if widget is not None:
                if isinstance(self._channel_stack, QStackedWidget):
                    self._channel_stack.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._channel_forms = []
        if isinstance(self._channel_list_widget, QListWidget):
            self._channel_list_widget.clear()
        for item in forms or []:
            if not isinstance(item, dict):
                continue
            form_params = dict(item.get("params", {}) or {})
            if "playing" in item:
                form_params["playing"] = bool(item.get("playing"))
            if "speed_factor" in item:
                form_params["speed_factor"] = item.get("speed_factor")
            self._add_channel_form(str(item.get("ch_type", "") or ""), form_params)
        if not self._channel_forms and self._channel_param_spec is not None:
            self._add_channel_form(str(getattr(self._channel_param_spec, "default", "") or "dimmer"))
        if self._channel_forms:
            self._set_active_channel_form(self._channel_forms[0])
        self._sync_transport_controls_to_active_form()

    def _running_states_for_current_preset(self) -> list:
        from .effect_owner_edit import running_keys
        return [self._external_states[key] for key in running_keys(self)]

    def _restore_running_effect_ui_state(self, states=None):
        states = self._running_states_for_current_preset() if states is None else states
        if not states:
            return
        first_params = dict(states[0].get("params", {}) or {})
        if not self._has_channel_forms():
            self._apply_param_values(first_params)
            return
        forms = []
        for state in states:
            params = dict(state.get("params", {}) or {})
            chs = [str(ch) for ch in (state.get("ch_types", []) or []) if str(ch)]
            if not chs:
                ch = params.get(self._channel_param_key)
                if isinstance(ch, str) and ch:
                    chs = [ch]
            for ch in chs:
                form_params = dict(params)
                form_params.pop("_channel_forms", None)
                if self._channel_param_key:
                    form_params.pop(self._channel_param_key, None)
                forms.append({
                    "ch_type": ch,
                    "params": form_params,
                    "playing": True,
                    "speed_factor": float(state.get("speed_factor", 1.0) or 0.0),
                })
        if not forms:
            return
        first_params["_channel_forms"] = forms
        if self._channel_param_key:
            first_params[self._channel_param_key] = [f["ch_type"] for f in forms]
        self._apply_param_values(first_params)

    # ── 摇杆绑定（effect 参数滑块；语义冲突=同效果种类+同参数）──────────────
    # 绑定管理统一在 Programmer 的「管理绑定…」（同一对话框同时列出通道与
    # effect 参数绑定），effect 面板不设独立管理页面。

    def _on_param_bind_requested(self, channel: str, key: str, label: str):
        """参数绑定流程：已绑定→直接调整；未绑定→冲突检测→轴检测→建绑定→调整。"""
        from PyQt6.QtWidgets import QMessageBox
        from .joystick_control_binding import (BindingMappingDialog,
                                               get_joystick_bind_manager)
        from .joystick_control_binding_popups import AxisDetectPopup
        mgr = get_joystick_bind_manager()
        existing = mgr.get_effect_param_binding(channel, key)
        if existing is not None:
            # 已绑定：左键=调整映射（与 Programmer 通道指示器一致）
            BindingMappingDialog(existing, self.window()).exec()
            self._refresh_param_bind_indicators()
            return
        preset_id = str(getattr(self, "_selected_id", "") or "")
        conflict = mgr.find_effect_param_conflict(key, preset_id)
        if conflict is not None:
            r = QMessageBox.question(
                self, "绑定冲突",
                f"「{label}」已绑定到 {conflict.physical_axis.upper()} 轴"
                f"（{conflict.channel or '全局'}，同效果参数）。\n是否替换为新绑定？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            mgr.remove_effect_param_binding(conflict.channel, conflict.param_key)
        popup = AxisDetectPopup(str(label or key), self.window())
        popup.exec()
        axis = getattr(popup, "detected_axis", None)
        if not axis:
            return
        rule = mgr.add_effect_param_binding(channel, key, label,
                                            physical_axis=axis,
                                            preset_id=preset_id)
        BindingMappingDialog(rule, self.window()).exec()
        self._refresh_param_bind_indicators()

    def _on_param_bind_menu(self, channel: str, key: str, label: str):
        """右键菜单：调整映射 / 取消绑定（管理入口统一在 Programmer）。"""
        from PyQt6.QtWidgets import QMenu
        from .joystick_control_binding import (BindingMappingDialog,
                                               get_joystick_bind_manager)
        mgr = get_joystick_bind_manager()
        rule = mgr.get_effect_param_binding(channel, key)
        menu = QMenu(self)
        # ★ 调度台·释放调用层（该参数；跨所有 clip）—— 叠加/释放语义的控件入口
        try:
            from ..processing.dispatch import get_release_registry
            reg = get_release_registry()
            rel = menu.addAction("释放调用层（此参数）")
            rel.triggered.connect(
                lambda: (reg.release_control(f"effect|param|{key}"),
                         self._status_lbl.setText("已释放该参数的调用层（重新叠加可再调用）")))
            menu.addSeparator()
        except Exception:
            pass
        if rule is not None:
            adj = menu.addAction("调整映射…")
            adj.triggered.connect(
                lambda: BindingMappingDialog(rule, self.window()).exec())
            unbind = menu.addAction("取消绑定")
            unbind.triggered.connect(
                lambda: (mgr.remove_effect_param_binding(channel, key),
                         self._refresh_param_bind_indicators()))
        else:
            menu.addAction("（未绑定 — 左键绑定）").setEnabled(False)
        # ★ R183 W2-FX #13/#8：跟随音频 / 分配到宏（实现在 _w2fx）
        _w2fx.append_param_actions(menu, self, key, label)
        menu.exec(QCursor.pos())

    def _on_effect_param_bound(self, channel: str, key: str, value: int):
        """Source values reach their running owner and the matching editor."""
        _dispatch_state.route_bound_value(self, channel, key, value)

    def _refresh_param_bind_indicators(self):
        """刷新全部参数行的 🎮 绑定指示器。"""
        mgr = getattr(self, "_joy_mgr", None)
        if mgr is None:
            return
        for pw in list(self._param_widgets.values()):
            if hasattr(pw, "refresh_bind_indicator"):
                pw.refresh_bind_indicator(mgr)
        for form in (self._channel_forms or []):
            for pw in (form.get("params") or {}).values():
                if hasattr(pw, "refresh_bind_indicator"):
                    pw.refresh_bind_indicator(mgr)

    def _on_param_changed(self, key: str, value: Any):
        _form_author.mark_edit(self, key, value)
        self._clear_current_manual_slice_cache()
        if key in {"_joy_amp_axis", str(self._channel_param_key or "")}:
            for form in list(self._channel_forms or []):
                self._update_channel_form_item(form)
        params = _width_controls.current_params(self)
        ch_types = self._get_selected_ch_types()
        sort_mode = self._get_sort_mode()

        group_key = self._current_live_selection_key()
        if self._external_states and group_key:
            for old_key in list(self._running_keys_for_current_selection()):
                state = self._external_states.get(old_key)
                if not isinstance(state, dict):
                    continue
                if self._selected_id and state.get("preset_id") != self._selected_id:
                    continue
                new_state = dict(state)
                if self._has_channel_forms():
                    st_ch = [str(ch) for ch in (state.get("ch_types", []) or []) if str(ch)]
                    target_ch = st_ch[0] if st_ch else ""
                    form_item = next(
                        (f for f in (self._channel_forms or [])
                         if self._channel_form_type(f) == target_ch),
                        self._active_channel_form())
                    form_params = dict(params)
                    form_params.pop("_channel_forms", None)
                    if target_ch and self._channel_param_key:
                        form_params[self._channel_param_key] = target_ch
                    if form_item is not None:
                        seen_form_widgets = set()
                        for pkey, widget in (form_item.get("params", {}) or {}).items():
                            if isinstance(widget, RangeParamWidget):
                                if id(widget) in seen_form_widgets:
                                    continue
                                seen_form_widgets.add(id(widget))
                                form_params.update(widget.get_values())
                            else:
                                form_params[pkey] = widget.get_value()
                        new_state["speed_factor"] = float(form_item.get("speed_factor", 1.0) or 0.0)
                    new_state["params"] = _owner_edit.edit_params(self, form_params, state, key)
                    new_state["ch_types"] = [target_ch] if target_ch else list(ch_types)
                else:
                    new_state["params"] = _owner_edit.edit_params(self, params, state, key)
                    new_state["ch_types"] = list(ch_types)
                new_state["sort_mode"] = sort_mode
                new_state["_ltp_takeover_pending"] = True; new_state["recalled"] = False
                new_key = (self._manual_effect_track_type(
                    state.get("preset_id") or self._selected_id,
                    new_state.get("ch_types", [])), old_key[1])
                if new_key != old_key:
                    self._external_states.pop(old_key, None)
                self._external_states[new_key] = _audio_runtime.transfer_audio_owner(self, state, new_state)

        if self._external_state is not None:
            preset_id = self._selected_id or self._external_state[0]
            if preset_id == self._external_state[0]:
                self._external_state = _audio_runtime.transfer_audio_owner(self, self._external_state, (
                    preset_id,
                    self._params_with_current_output_scope(params),
                    list(ch_types),
                    sort_mode,
                ))

        if self._is_playing and self._nm is not None and self._project is not None:
            # 状态(_external_states/_external_state)已在上面同步更新——这是让改动生效的关键。
            # _play_timer 正在以 40Hz(25ms)跑 _tick_play,会在 ≤25ms 内用新状态渲染。
            # 拖动时 valueChanged 以 60+Hz 触发,若每次都同步再渲一帧 → 双重渲染、卡顿。
            # 故:timer 活跃时跳过这次冗余同步渲染(交给 timer);timer 未跑时才立即渲一帧
            # 保证单次离散改动也即时预览。输出值完全不变,只是去掉拖动期的重复全帧 compute。
            try:
                if not (hasattr(self, "_play_timer") and self._play_timer.isActive()):
                    self._tick_play()
            except Exception:
                logger.debug("effect_browser: failed to tick play on slider drag")
                pass

    # ── 通道快速选择 ─────────────────────────────────────────────────────────

    def _quick_select_ch(self, types: List[str]):
        # 兼容旧代码入口：全局通道类型选择已经取消，通道由参数区决定。
        return

    # ── 获取当前参数 ─────────────────────────────────────────────────────────

    def _get_current_params(self) -> Dict[str, Any]:
        params = {}
        seen_widgets = set()
        for key, pw in self._param_widgets.items():
            if isinstance(pw, RangeParamWidget):
                if id(pw) in seen_widgets:
                    continue
                seen_widgets.add(id(pw))
                params.update(pw.get_values())
            else:
                params[key] = pw.get_value()
        if self._channel_param_key:
            forms = self._capture_channel_forms()
            params[self._channel_param_key] = [f.get("ch_type", "") for f in forms if f.get("ch_type")]
            params["_channel_forms"] = forms
        params["_app_direction"] = self._get_app_direction()
        params["_app_scope"] = self._get_app_scope()
        # ★★ 2026-09-20 修复（用户追问"两种坐标系都对吗"）：此处必须注入**已解析**的
        #   坐标系 —— 原实现传原始值（可能是哨兵 "follow"），而引擎侧此前把 follow
        #   一律当 global → **单控灯工程**下"跟随全局灯控"退化成整体坐标系，
        #   与 clip 侧（`_clip_spatial_scope` 按 light_control_mode 解析）行为不一致。
        #   `_resolved_spatial_scope()` 正是既有解析器（follow → per_shape/global），
        #   此前**从未被调用**（死方法）。
        params["spatial_scope"] = self._resolved_spatial_scope()
        params["_skip_base_order"] = self._get_skip_base_order()
        return params

    def _get_app_direction(self) -> str:
        if hasattr(self, "_direction_combo"):
            return self._direction_combo.currentData() or "default"
        return "default"

    def _get_skip_base_order(self) -> bool:
        # ★ 2026-08-11 #18：跳过编号排序（只看坐标按应用方向排序）
        if hasattr(self, "_skip_base_order_chk"):
            return bool(self._skip_base_order_chk.isChecked())
        return False

    def _get_spatial_scope(self) -> str:
        if hasattr(self, "_spatial_scope_combo"):
            scope = self._spatial_scope_combo.currentData() or "follow"
        else:
            scope = "follow"
        scope = str(scope or "follow").strip().lower()
        return scope if scope in ("follow", "per_shape", "global") else "follow"

    def _get_control_mode(self) -> str:
        """读取控灯粒度下拉值：follow(跟随全局灯控)/whole(整体控灯)/single(单控灯)。

        与坐标系(spatial_scope)正交——粒度是「包内配灯当一个灯还是逐个」。
        """
        if hasattr(self, "_control_mode_combo"):
            cm = self._control_mode_combo.currentData() or "follow"
        else:
            cm = "follow"
        cm = str(cm or "follow").strip().lower()
        return cm if cm in ("follow", "single", "whole") else "follow"

    def _get_app_scope(self) -> str:
        if hasattr(self, "_app_scope_combo"):
            scope = self._app_scope_combo.currentData() or "both"
        else:
            scope = "both"
        scope = str(scope or "both").strip().lower()
        return normalize_effect_application_scope(scope)

    def _get_selected_ch_types(self) -> List[str]:
        """从当前效果参数自动推导需要写出的通道类型。

        旧版使用右侧全局复选框，容易与参数区 ch_type/ch 发生冲突。现在：
        1. 若效果参数中存在 ch_type，直接使用该参数；
        2. 若存在 v36 Cue 类常见的 ch 参数，使用该参数；
        3. 否则回退到预设 target_channels；
        4. 再无信息时返回空列表，表示不过滤写出通道。
        """
        if self._channel_param_key:
            vals = [f.get("ch_type", "") for f in self._capture_channel_forms()]
            vals = [str(v) for v in vals if v]
            if vals:
                return vals
        params = self._get_current_params()
        for key in ("ch_type", "ch", "channel", "target_channel"):
            val = params.get(key)
            if isinstance(val, str) and val:
                return [val]
            if isinstance(val, (list, tuple)):
                vals = [str(v) for v in val if v]
                if vals:
                    return vals
        if self._selected_id:
            try:
                preset = get_engine().get_preset(self._selected_id)
                if preset is not None and preset.target_channels:
                    return list(preset.target_channels)
            except Exception:
                logger.debug("effect_browser: failed to get channel types from selected preset")
                pass
        return []

    def _effect_channel_param_key(self, params: Optional[dict] = None) -> Optional[str]:
        params = params or self._get_current_params()
        for key in ("ch_type", "ch", "channel", "target_channel"):
            if key in params:
                return key
        return None

    def _project_object(self):
        project = self._project
        return getattr(project, "project", project)

    def _invalidate_tick_caches(self, *args):
        """失效 _tick_play 热路径缓存（TopView 变化 / 换工程 / 换设备时调用）。"""
        self._ef_topmap_cache = None
        self._ef_by_id_cache = None
        self._ef_defs_cache = None
        self._ef_fx_list_cache = None
        self._ef_pos_cache = None
        # ★ 全设备稳定列表：按「源容器身份+长度」自校验，这里再显式失效一道
        #   （换工程/换设备表时宁可多清一次，同 _spatial_groups_cache 的处理）。
        self._ef_all_fx_cache = None
        self._ef_fdefs_cache = None
        self._ef_fixture_ids_cache.clear()
        self._output_scope_cache.clear()
        # ★ R38-D2：空间分组缓存同样在这里作废。键里已经带了三个版本号，这一行
        #   是第二道保险——换工程/换设备表时宁可多清一次。
        if getattr(self, "_spatial_groups_cache", None):
            self._spatial_groups_cache.clear()
        self._effect_channel_structure_cache.clear()
        self._effect_addr_cache.clear()
        # ★ R173 性能：四个 (project, fixture_ids) 内容签名缓存改用 IdentityCache
        #   后已有容量上限（不再无界增长），这里仍显式清一次——换工程后旧
        #   Project 的强引用应尽快释放，不用等它被逐出。
        for _cache_attr in ("_unit_groups_cache", "_fixture_groups_cache",
                            "_control_groups_cache", "_light_caps_cache"):
            cache = getattr(self, _cache_attr, None)
            if cache is not None:
                cache.clear()

    def _current_project_audio_path(self) -> str:
        project = self._project_object()
        try:
            path = os.fspath(getattr(project, "audio_path", "") or "")
        except Exception:
            logger.debug("effect_browser: failed to get project audio path")
            path = ""
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        try:
            project_path = os.fspath(getattr(self._project, "_current_path", "") or "")
        except Exception:
            logger.debug("effect_browser: failed to get project current path")
            project_path = ""
        if project_path:
            return os.path.abspath(os.path.join(os.path.dirname(project_path), path))
        return os.path.abspath(path)

    def _ensure_audio_context(self):
        try:
            from .audio_driver import get_audio_driver
            analyzer = get_audio_driver()
        except Exception:
            logger.debug("effect_browser: failed to import/get audio driver")
            self._audio_analyzer = None
            return None
        self._audio_analyzer = analyzer
        return analyzer

    @staticmethod
    def _derive_audio_spectral(bands) -> dict:
        """从 AudioFrame.bands（低→高频段能量，各 0..1）派生音乐内核需要的
        low/mid/high/centroid 四个字段。

        bands 语义见 audio_driver.FREQ_RANGES：8 个频段，索引 0 为最低频(Sub)、
        索引 N-1 为最高频(Air)，每个值已被裁到 0..1。

        - low/mid/high：把 bands 按索引大致三等分，每段取均值（已是 0..1）。
          bands 为空或缺段时该段记 0（带除零保护）。
        - centroid：能量加权平均频段位置 / (段数-1)，归一到 0..1；
          总能量为 0（或段数<=1）时回退 0.5。
        """
        try:
            vals = [float(b) for b in (bands or [])]
        except Exception:
            logger.debug("effect_browser: failed to parse audio bands to float")
            vals = []
        n = len(vals)
        if n <= 0:
            return {"low": 0.0, "mid": 0.0, "high": 0.0, "centroid": 0.5}

        def _seg_avg(seq):
            return (sum(seq) / len(seq)) if seq else 0.0

        # 三等分边界：用四舍五入分配，保证 n>=3 时三段都非空、n<3 时不越界。
        b1 = int(round(n / 3.0))
        b2 = int(round(2.0 * n / 3.0))
        b1 = max(0, min(n, b1))
        b2 = max(b1, min(n, b2))
        low = _seg_avg(vals[:b1])
        mid = _seg_avg(vals[b1:b2])
        high = _seg_avg(vals[b2:])

        total = sum(vals)
        if total > 1e-9 and n > 1:
            weighted = sum(i * v for i, v in enumerate(vals))
            centroid = (weighted / total) / (n - 1)
        else:
            centroid = 0.5

        def _c01(x):
            return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))

        return {
            "low": _c01(low),
            "mid": _c01(mid),
            "high": _c01(high),
            "centroid": _c01(centroid),
        }

    def _audio_frame_dict(self, t: float) -> dict:
        """读取主界面已选的唯一音频源；文件空态保持静音。"""
        from .effect_audio_runtime import selected_audio_frame, spectral_centroid_hz
        analyzer = self._ensure_audio_context()
        if analyzer is None:
            return {}
        try:
            frame = selected_audio_frame(analyzer, t)
        except Exception:
            logger.debug("effect_browser: failed to get audio frame from analyzer")
            frame = None
        if frame is None:
            return {}
        bands = list(getattr(frame, "bands", []) or [])
        spectral = self._derive_audio_spectral(bands)
        return {
            "t": float(getattr(frame, "t", t) or 0.0),
            "rms": float(getattr(frame, "rms", 0.0) or 0.0),
            "peak": float(getattr(frame, "peak", 0.0) or 0.0),
            "bands": bands,
            # 派生频段：供音乐内核 band3(low/mid/high) 与 spectrumscan(centroid) 使用
            "low": spectral["low"],
            "mid": spectral["mid"],
            "high": spectral["high"],
            "centroid": spectral["centroid"],
            "centroid_hz": spectral_centroid_hz(bands),
            "bpm": float(getattr(frame, "bpm", 120.0) or 120.0),
            "beat_phase": float(getattr(frame, "beat_phase", 0.0) or 0.0),
            "is_beat": bool(getattr(frame, "is_beat", False)),
            "onset_strength": float(getattr(frame, "onset_strength", 0.0) or 0.0),
            "has_audio": bool(getattr(frame, "has_audio", False)),
        } | _deep_audio_fields(frame)

    def _params_with_audio(self, params: Optional[dict], t: float,
                           preset_id: str = "", runtime_owner=None) -> dict:
        from .effect_audio_runtime import bind_audio_runtime
        out = dict(params or {})
        audio_data = self._audio_frame_dict(t)
        if audio_data:
            out["_audio_data"] = audio_data
            out["_audio_frame"] = audio_data
            out["_audio_provider"] = self._audio_frame_dict
            if audio_data.get("has_audio"):
                out["bpm"] = audio_data.get("bpm", out.get("bpm", 120.0))
            # Audio-reactive presets: mark as requiring real audio
            if preset_id and preset_id.startswith("cur_audio_"):
                out["_audio_required"] = not audio_data.get("has_audio", False)
            # ★ R183 W2-FX #13：静态参数 → 跟随音频（未配置绑定时原对象返回 = 零开销）
            out = _w2fx.expand_audio_follow(self, preset_id, out)
        return bind_audio_runtime(self, preset_id, out, t, runtime_owner)

    def _cached_fixture_ids(self, fixtures: list) -> List[str]:
        """返回 fixtures 对应的稳定 id 列表（播放期按对象身份缓存）。"""
        key = id(fixtures)
        entry = self._ef_fixture_ids_cache.get(key)
        if entry is None or entry[0] is not fixtures:
            ids = [str(getattr(fi, "id", "") or "") for fi in (fixtures or [])]
            # ★ 淘汰只逐出最旧一条（旧实现 >16 → clear() 击穿其它效果）。
            _cache_put_bounded(self._ef_fixture_ids_cache, key,
                               (fixtures, ids), _FX_LIST_CACHE_MAX)
            return ids
        return entry[1]

    def _compute_channel_frames(self, eng, preset_id: str, t: float, params: dict,
                                fixtures: list, fdefs: dict, positions: dict,
                                fps: float, sort_mode: str, base_order_ids: list,
                                ch_types: list, *, runtime_owner=None) -> list:
        # ★ 性能：fixtures 列表播放期稳定，id 列表按对象身份缓存，避免每帧
        #   4889 次 getattr+str 重建。
        # ★ 作用域：通道值可带设备类型（见 `_scope_frames_inputs`）→ 只作用于
        #   该类型设备；引擎一律收**纯键**，作用域不进计算语义（2026-09-19 定案）。
        fixtures, fdefs, positions = _scope_frames_inputs(
            fixtures, fdefs, positions, ch_types)
        fixture_ids = self._cached_fixture_ids(fixtures)
        params = self._params_with_audio(params, t, preset_id, runtime_owner)
        ch_types = [str(ch) for ch in (ch_types or []) if str(ch)]
        plain_ch_types = [k for k, _s in map(_scoped_key, ch_types)]
        ch_key = self._effect_channel_param_key(params)
        forms = []
        if isinstance(params, dict):
            forms = list(params.get("_channel_forms", []) or [])
        def _render(ch_params):   # 三处分支共用：引擎算一帧（参数已收窄）
            return eng.compute_at(
                preset_id, t, ch_params, fixtures, fdefs, positions,
                fps=fps, sort_mode=sort_mode, base_order_ids=base_order_ids)

        if ch_key and forms:
            frames = []
            for item in forms:
                if not isinstance(item, dict):
                    continue
                ch = str(item.get("ch_type", "") or "")
                if not ch:
                    continue
                ch_params = dict(params or {})
                ch_params.pop("_channel_forms", None)
                ch_params[ch_key] = _scoped_key(ch)[0]   # 引擎收纯键
                ch_params.update(dict(item.get("params", {}) or {}))
                ch_params = _target_scope.frame_params(self, ch_params, fixture_ids, runtime_owner)
                ch_params = self._strip_joy_amp_if_closed(
                    ch_params, _scoped_key(ch)[0], runtime_owner=runtime_owner)
                frame = _render(ch_params)
                if frame is not None:
                    frames.append((frame, [_scoped_key(ch)[0]], fixtures))
            return frames
        if not ch_key or len(ch_types) <= 1:
            clean_params = dict(params or {})
            clean_params.pop("_channel_forms", None)
            clean_params = _target_scope.frame_params(self, clean_params, fixture_ids, runtime_owner)
            clean_params = self._strip_joy_amp_if_closed(
                clean_params, plain_ch_types[0] if plain_ch_types else "", runtime_owner=runtime_owner)
            frame = _render(clean_params)
            return [(frame, plain_ch_types, fixtures)] if frame is not None else []
        frames = []
        for ch in ch_types:
            plain = _scoped_key(ch)[0]               # 引擎收纯键
            ch_params = dict(params or {})
            ch_params.pop("_channel_forms", None)
            ch_params[ch_key] = plain
            ch_params = _target_scope.frame_params(self, ch_params, fixture_ids, runtime_owner)
            ch_params = self._strip_joy_amp_if_closed(ch_params, plain, runtime_owner=runtime_owner)
            frame = _render(ch_params)
            if frame is not None:
                frames.append((frame, [plain], fixtures))
        return frames

    def _get_sort_mode(self) -> str:
        return self._sort_combo.currentData() or "index"

    def _is_topview_live_selection(self) -> bool:
        return bool(
            getattr(self, "_selected_group_ids", None)
            and str(getattr(self, "_selected_group_name", "") or "").startswith("TopView")
        )

    def _project_fixture_ids(self) -> List[str]:
        project = self._project_object()
        return [
            str(getattr(fi, "id", "") or "")
            for fi in (getattr(project, "fixtures", []) or [])
            if str(getattr(fi, "id", "") or "")
        ]

    def _is_project_all_fixture_selection(self, ids: Optional[list]) -> bool:
        norm = _normalize_fixture_ids(ids)
        all_ids = self._project_fixture_ids()
        return bool(norm) and bool(all_ids) and set(norm) == set(all_ids)

    def _programmer_output_scope_ids(self) -> tuple[List[str], List[str]]:
        # 隐藏加载语义（2026-09-17 用户定案）：作用域跟随「是否已释放」而非
        # 「面板是否可见」——详见模块级 `_scope_ids_from_programmer_state`。
        pw = getattr(self, "_programmer_panel", None)
        if pw is None or bool(getattr(pw, "_programmer_output_released", True)):
            return [], []
        try:
            group_id = str(getattr(pw, "_current_group_id", "") or "")
            ids = _normalize_fixture_ids(pw.get_live_selected_fixture_ids() or [])
        except Exception:
            logger.debug("effect_browser: failed to get live selection from programmer panel")
            return [], []
        if (not ids or group_id in ("", "__all__")
                or self._is_project_all_fixture_selection(ids)):
            return [], []
        return _scope_ids_from_programmer_state(
            ids, getattr(pw, "_current_base_order_ids", []) or [])

    def _effective_output_scope_ids(self) -> tuple[List[str], List[str]]:
        """按 (选择组, Programmer 选择) 状态缓存输出设备集。

        ★ 性能：打开 Programmer 后，_programmer_output_scope_ids 每次都会从
        Programmer 的 _current_fixtures_selected 重建新列表；而 _tick_play 每帧
        会多次调用本方法。若不缓存，4889 台设备每次全量 normalize/去重，直接
        造成“打开 Programmer 后全局 PPT 卡顿”且关闭后仍持续。
        """
        pw = getattr(self, "_programmer_panel", None)
        # ★ R38-D4：缓存键换成版本号。旧键 (id(sel_ids), ..., id(pw_列表), ...)
        #   有两个致命缺陷：
        #   ① 上面 `self._selected_group_ids or []` 在为空时**每次都新建一个
        #      空列表**，id 每次都不同 → 键每次都不同 → 缓存 0 命中。而"选择组
        #      为空、靠 Programmer 决定作用域"正是本 bug 的现场，等于 R36 加的
        #      缓存在最需要它的场景里从来没生效过。
        #   ② id 是内存地址，会被回收复用：旧列表释放后新列表可能拿到同一个
        #      id，长度又恰好相同 → 缓存"命中"到**另一份选择**的结果 → 输出到
        #      错误的设备。这比卡顿严重得多（P0）。
        #   版本号单调递增，两个问题一起消失，且键是小整数，比较开销可忽略。
        #   ★ 2026-09-17：键里**移除** isVisible() —— 隐藏加载语义下可见性已不再
        #   影响作用域结果（见 `_programmer_output_scope_ids`），保留它只会让
        #   吸附/切 tab 时无谓地失效缓存并重建 4889 台的选择集（性能回退点）。
        #   ★★ 同时**加入** released 标志：释放/重新接管会翻转作用域结果，
        #   不进键的话缓存会停留在释放前的旧值（释放后仍按 N 台输出）。
        try:
            pw_key = 0 if pw is None else (
                str(getattr(pw, "_current_group_id", "") or ""),
                int(getattr(pw, "_live_selection_version", 0)),
                bool(getattr(pw, "_programmer_output_released", True)),
            )
        except Exception:
            logger.debug("effect_browser: programmer scope key unavailable")
            pw_key = 0
        key = (int(getattr(self, "_scope_version", 0)), id(pw), pw_key)
        cached = self._output_scope_cache.get(key)
        if cached is not None and cached[0] is pw:
            return cached[1]
        # UI inheritance retains its mother key; output uses only actual targets.
        scope = _target_scope.current_scope(self)
        result = scope if scope is not None else self._programmer_output_scope_ids()
        if len(self._output_scope_cache) > 32:
            self._output_scope_cache.clear()
        self._output_scope_cache[key] = (pw, result)
        return result

    def _current_live_selection_key(self) -> str:
        ids, _base = self._effective_output_scope_ids()
        return _target_scope.selection_key(self, ids, _group_key_from_ids)

    def _fixture_id_set_from_ids(self, ids: Optional[list]) -> set[str]:
        return {str(fid) for fid in _normalize_fixture_ids(ids) if str(fid)}

    def _external_state_fixture_id_set(self, state: Optional[dict]) -> set[str]:
        if not isinstance(state, dict):
            return set()
        ids = self._fixture_id_set_from_ids(state.get("group_ids", []) or [])
        if ids:
            return ids
        params = state.get("params", {})
        if isinstance(params, dict):
            return self._fixture_id_set_from_ids(params.get("_output_fixture_ids") or [])
        return set()

    def _external_state_group_matches(self, key, state: Optional[dict],
                                      group_ids: Optional[list] = None,
                                      group_key: Optional[str] = None) -> bool:
        target_key = group_key or _group_key_from_ids(group_ids)
        if target_key and len(key) >= 2 and key[1] == target_key:
            return True
        target_ids = self._fixture_id_set_from_ids(group_ids or [])
        if not target_ids:
            return False
        state_ids = self._external_state_fixture_id_set(state)
        return bool(state_ids and state_ids == target_ids)

    def _params_with_current_output_scope(self, params: Optional[dict]) -> dict:
        out = _target_scope.bound_params(self, params)
        ids, base = self._effective_output_scope_ids()
        out["_output_fixture_ids"] = list(ids)
        out["_output_base_order_ids"] = list(base or ids)
        # ★ R38-D5：传"来自缓存的长寿对象" ids，而不是刚 list() 出来的新副本。
        #   D2 的空间分组缓存要按 id(fixture_ids) 做廉价身份判定；如果这里传
        #   out["_output_fixture_ids"]（每帧新建的副本），下游缓存又会 0 命中，
        #   等于把 D4 刚修好的坑在下一层重挖一遍。
        #   语义等价：out["_output_fixture_ids"] or out["_output_base_order_ids"]
        #   展开就是 ids or (base or ids)。下游只读不改这个列表（已核对
        #   _normalize_spatial_groups / _topview_layout_cluster_spatial_groups
        #   都只做 set(...) 与遍历），所以共享安全。
        return self._inject_output_spatial_groups(out, ids)

    def _active_external_keys_for_current_selection(self) -> list:
        return self._running_keys_for_current_selection()

    def _stop_overlapping_manual_effects(self, preset_id: str, group_ids: Optional[list],
                                         ch_types: Optional[list]) -> None:
        group_key = _group_key_from_ids(group_ids)
        wanted_channels = {str(ch) for ch in (ch_types or []) if str(ch)}
        stopped_channels: set[str] = set()
        for key, state in list(getattr(self, "_external_states", {}).items()):
            if not self._external_state_group_matches(
                    key, state, group_ids=group_ids, group_key=group_key):
                continue
            if not isinstance(state, dict):
                continue
            state_channels = {str(ch) for ch in (state.get("ch_types", []) or []) if str(ch)}
            if wanted_channels and state_channels and not (wanted_channels & state_channels):
                continue
            self._external_states.pop(key, None)
            stopped_channels.update(state_channels or wanted_channels)
        if stopped_channels:
            for form in list(self._channel_forms or []):
                if self._channel_form_type(form) in stopped_channels:
                    form["playing"] = False
                    form["virtual_t"] = 0.0
                    form["last_real_t"] = 0.0

    def _stop_current_selection_effects(self) -> bool:
        keys = self._running_keys_for_current_selection()
        if not keys:
            return False
        stopped_ch = _drop_running_states(self, keys)
        _mark_forms_stopped(self, stopped_ch)
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText(
                f"已停止当前效果；剩余并行 {len(self._external_states)} 个")
        self._refresh_quick_effect_lists()
        if not self._external_states:
            _reset_transport_on_stop(self)
        else:
            self._update_transport_state()
        # ★ 2026-09-14（用户实报）：暂停/停止后**必须重报总线**，否则本面板的
        #   调度台窗格不会被 diff 删除 → 监控区一直显示成"还在播放"。
        #   原先只有 `_tick_play` 的"无活跃键"分支会重报，而本方法已 stop 了
        #   `_play_timer` → 那个分支永不执行（这正是"暂停后仍显示播放"的根因）。
        _report_after_stop(self)
        return True

    def _play_current_selection_as_independent_effect(self) -> bool:
        if not self._selected_id or self._nm is None or self._project is None:
            return False
        params = self._get_current_params()
        ch_types = self._get_selected_ch_types()
        sort_mode = self._get_sort_mode()
        params = self._params_with_current_output_scope(params)
        track_type = self._manual_effect_track_type(self._selected_id, ch_types)
        self.play_preset_for_type(track_type, self._selected_id, params, ch_types, sort_mode)
        if hasattr(self, "_status_lbl"):
            count = len(params["_output_fixture_ids"]) if params["_output_fixture_ids"] else len(self._project.fixtures)
            self._status_lbl.setText(
                f"已加入并行效果：{self._selected_id} / {count} 台 / 当前并行 {len(self._external_states)} 个"
                + self._per_shape_scope_hint(params.get("_output_fixture_ids")))
        self._update_transport_state()
        return True

    def _capture_current_param_values(self) -> Dict[str, Any]:
        values = {}
        seen_widgets = set()
        for key, pw in self._param_widgets.items():
            if isinstance(pw, RangeParamWidget):
                if id(pw) in seen_widgets:
                    continue
                seen_widgets.add(id(pw))
                values.update(pw.get_values())
            else:
                values[key] = pw.get_value()
        if self._channel_param_key:
            forms = self._capture_channel_forms()
            values[self._channel_param_key] = [f.get("ch_type", "") for f in forms if f.get("ch_type")]
            values["_channel_forms"] = forms
        values["_app_direction"] = self._get_app_direction()
        values["_app_scope"] = self._get_app_scope()
        values["spatial_scope"] = self._get_spatial_scope()
        # ★ 2026-08-12 持久化缺口修复: 「跳过编号排序」复选框状态此前从不进
        #   save_state/load_state, 保存重开 100% 丢失 (#18 功能统一审查)。
        values["_skip_base_order"] = self._get_skip_base_order()
        # 全局速度（非通道表单模式下，speed_factor 存在 _global_speed_factor 而不在 _param_widgets）
        if not self._has_channel_forms():
            values["_global_speed_factor"] = float(getattr(self, "_global_speed_factor", 1.0))
        return values

    # ── 底部按钮行：随机 / 重置（restyle_spec §10；复用既有参数写入路径）────

    def _route_param_values(self, values: Dict[str, Any]):
        """把 {key: value} 走既有回填路径写回控件（触发与手工编辑一致的信号）。

        通道表单模式下逐表单回填（各表单同 key 控件同步），否则走全局
        _apply_param_values。仅复用旧路径，不新增任何读写分支。
        """
        if not values:
            return
        if self._has_channel_forms():
            snapshot = dict(values)
            for form in list(self._channel_forms or []):
                _form_author.edit_values(form, snapshot)
                self._apply_channel_form_values(form, snapshot)
        else:
            self._apply_param_values(dict(values))

    def _iter_resettable_specs(self) -> List[ParamSpec]:
        """当前选中预设的参数清单（重置/随机共用；与 _load_preset_params 的
        跳过规则一致：全局速度、坐标系、通道选择主键不参与）。"""
        eng = get_engine()
        preset = eng.get_preset(self._selected_id) if self._selected_id else None
        if not preset:
            return []
        ch_key = str(self._channel_param_key or "")
        out = []
        for spec in (getattr(preset, "params", []) or []):
            key = str(getattr(spec, "key", "") or "")
            if not key or key == ch_key or key == "spatial_scope":
                continue
            if _is_global_speed_param(spec):
                continue
            out.append(spec)
        return out

    def _randomize_current_params(self):
        """「随机」按钮：把数值参数随机设到各自取值范围内。

        仅生成取值，写入完全走 _apply_param_values/_apply_channel_form_values
        既有路径（与手工拖滑杆等价的信号链）。
        """
        if not self._selected_id:
            return
        values: Dict[str, Any] = {}
        for spec in self._iter_resettable_specs():
            typ = str(getattr(spec, "type", "") or "").lower()
            # ★ R165：子集参数不参与随机（见 _NO_RANDOM_KEYS 注释）
            if typ not in ("float", "int") or str(spec.key) in _NO_RANDOM_KEYS:
                continue
            lo, hi = _spec_range(spec)
            if hi <= lo:
                continue
            try:
                if typ == "int":
                    values[str(spec.key)] = random.randint(int(math.ceil(lo)), int(math.floor(hi)))
                else:
                    values[str(spec.key)] = random.uniform(lo, hi)
            except Exception:
                logger.debug("effect_browser: failed to sample random value for %s", spec.key)
                continue
        self._route_param_values(values)

    def _reset_current_params(self):
        """「重置」按钮：全部可复位参数恢复为预设默认值。"""
        if not self._selected_id:
            return
        values: Dict[str, Any] = {}
        for spec in self._iter_resettable_specs():
            typ = str(getattr(spec, "type", "") or "").lower()
            key = str(spec.key)
            if typ in ("float", "int"):
                values[key] = getattr(spec, "default", 0)
            elif typ == "bool":
                values[key] = bool(getattr(spec, "default", False))
            elif typ == "choice":
                default = getattr(spec, "default", "")
                if isinstance(default, (list, tuple)):
                    continue   # 通道列表型默认值由表单逻辑管理，不做整体复位
                values[key] = default
        self._route_param_values(values)

    def _apply_param_values(self, values: Dict[str, Any]):
        if not values:
            return
        # 全局速度回填（非通道表单模式）
        if "_global_speed_factor" in values and not self._has_channel_forms():
            try:
                _master_speed.set_factor(self, _num_or(values["_global_speed_factor"], 1.0))
                self._sync_transport_controls_to_active_form()
            except Exception:
                logger.debug("effect_browser: failed to restore global speed value")
                pass
        if self._channel_param_key and "_channel_forms" in values:
            try:
                _restore_channel_forms_smart(self, list(values.get("_channel_forms", []) or []))
            except Exception:
                logger.debug("effect_browser: failed to restore channel forms")
                pass
        try:
            if "_app_direction" in values:
                idx = self._direction_combo.findData(values.get("_app_direction"))
                if idx >= 0:
                    self._direction_combo.setCurrentIndex(idx)
        except Exception:
            logger.debug("effect_browser: failed to restore app direction combo")
            pass
        try:
            if "_app_scope" in values and hasattr(self, "_app_scope_combo"):
                idx = self._app_scope_combo.findData(normalize_effect_application_scope(values.get("_app_scope")))
                if idx >= 0:
                    self._app_scope_combo.setCurrentIndex(idx)
        except Exception:
            logger.debug("effect_browser: failed to restore app scope combo")
            pass
        try:
            if "spatial_scope" in values and hasattr(self, "_spatial_scope_combo"):
                idx = self._spatial_scope_combo.findData(values.get("spatial_scope"))
                if idx >= 0:
                    self._spatial_scope_combo.setCurrentIndex(idx)
        except Exception:
            logger.debug("effect_browser: failed to restore spatial scope combo")
            pass
        try:
            if "_skip_base_order" in values and hasattr(self, "_skip_base_order_chk"):
                self._skip_base_order_chk.blockSignals(True)
                self._skip_base_order_chk.setChecked(bool(values.get("_skip_base_order", False)))
                self._skip_base_order_chk.blockSignals(False)
        except Exception:
            logger.debug("effect_browser: failed to restore skip base order checkbox")
            pass
        for key, val in values.items():
            if key in {"_channel_forms", "_app_direction", "_app_scope", "spatial_scope"}:
                continue
            pw = self._param_widgets.get(key)
            if pw is None:
                if key == self._channel_param_key and self._channel_param_key and "_channel_forms" not in values:
                    try:
                        if isinstance(val, (list, tuple)):
                            legacy_params = {
                                k: v for k, v in values.items()
                                if k not in {self._channel_param_key, "_channel_forms"}
                            }
                            self._restore_channel_forms([
                                {"ch_type": ch, "params": dict(legacy_params)} for ch in val
                            ])
                        elif val:
                            legacy_params = {
                                k: v for k, v in values.items()
                                if k not in {self._channel_param_key, "_channel_forms"}
                            }
                            self._restore_channel_forms([{"ch_type": val, "params": legacy_params}])
                    except Exception:
                        logger.debug("effect_browser: failed to restore legacy channel forms")
                        pass
                continue
            if isinstance(pw, RangeParamWidget):
                restore_pair_values(pw, values)
                continue
            ctrl = getattr(pw, "_ctrl", None)
            if ctrl is None:
                continue
            try:
                if isinstance(ctrl, _ParamNumSlider):
                    ctrl.set_out(float(val))       # 数值滑杆 语义值回填
                elif isinstance(ctrl, QDoubleSpinBox):
                    ctrl.setValue(float(val))
                elif isinstance(ctrl, QSpinBox):
                    ctrl.setValue(int(val))
                elif isinstance(ctrl, QCheckBox):
                    ctrl.setChecked(bool(val))
                elif isinstance(ctrl, QListWidget):
                    if isinstance(val, (list, tuple)):
                        pw._set_channel_values(list(val))
                    elif val:
                        pw._set_channel_values([val])
                elif isinstance(ctrl, WingDirectionToolbar):
                    ctrl.set_value(val)
                elif isinstance(ctrl, QComboBox):
                    idx = ctrl.findData(val)
                    if idx < 0 and isinstance(val, str):
                        idx = ctrl.findText(val)
                    if idx >= 0:
                        ctrl.setCurrentIndex(idx)
            except Exception:
                logger.debug("effect_browser: failed to restore group state combo box value")
                continue

    def _save_current_group_ui_state(self):
        key = _group_ui_state_key(self)
        self._group_states[key] = _form_author.editor_recipe(self, key, _target_scope.editor_recipe(self))

    def _restore_group_ui_state(self):
        key = _group_ui_state_key(self)
        _target_scope.restore_editor_recipe(self, self._group_states.get(key), _apply_param_values_smart)

    # ── 外部 context 注入 ────────────────────────────────────────────────────

    def set_context(self, project, nm, topview=None, fixture_group_panel=None,
                    programmer_panel=None):
        """
        由 MainWindow 在懒加载后调用，注入运行时依赖。
        project             : 含 fixtures 列表的 Project 对象
        nm                  : NetworkManager（用于直接发送 DMX）
        topview             : TopViewPanel（读取 pos_x/pos_y，可选）
        fixture_group_panel : FixtureGroupPanel（读取设备组，可选）
        """
        prev_topview = self._topview
        self._project = project
        self._nm = nm
        self._topview = topview
        self._fixture_group_panel = fixture_group_panel
        self._programmer_panel = programmer_panel
        self._effect_addr_cache.clear()
        self._invalidate_tick_caches()
        # TopView 位置/换页变化 → 失效坐标缓存(拖动设备后波形立即跟随新位置)
        if topview is not prev_topview:
            if prev_topview is not None:
                try:
                    prev_topview.layout_changed.disconnect(self._invalidate_tick_caches)
                except Exception:
                    logger.debug("effect_browser: failed to disconnect TopView layout_changed signal")
                    pass
            if topview is not None:
                try:
                    topview.layout_changed.connect(self._invalidate_tick_caches)
                except Exception:
                    logger.debug("effect_browser: failed to connect TopView layout_changed signal")
                    pass
        self._ensure_audio_context()
        self._refresh_group_list()
        # ★ R193/P2-08（UI-10）：上下文刷新不得自发开播/清零相位——保存设备组
        #   （main_window._on_topview_fixture_group_saved）也会走到这里，会让
        #   外部驱动的效果相位跳变。播放由显式操作启动。
        if getattr(self, "_effect_addr_cache", None):
            self._effect_addr_cache = {}
        self._update_transport_state()

    # ── 播放控制 ─────────────────────────────────────────────────────────────
    # ── 设备组管理 ───────────────────────────────────────────────────────────
    def _refresh_group_list(self):
        """从 fixture_group_panel 刷新设备组下拉框。"""
        _target_scope.refresh_group_choices(self)

    def _on_group_combo_changed(self, index: int):
        self._save_current_group_ui_state()
        # ★R38-D4：作用域源即将改变，先推进版本号（它在 `_effective_output_scope_ids` 缓存键里）
        self._scope_version = int(getattr(self, "_scope_version", 0)) + 1
        gid = self._group_combo.itemData(index)
        if gid is None:
            _target_scope.assign_targets(self, [], [], explicit=False)
            _clear_group_scope_fields(self)   # ★R168：作用域/子集标记一并清
            self._selected_group_name = ""
            self._group_status_lbl.setText("全部设备")
        else:
            ids = []
            base_order = []
            route_sheets = []
            if self._fixture_group_panel is not None or self._project is not None:
                try:
                    for grp in _target_scope.project_groups(self):
                        if grp.id == gid:
                            ids = list(getattr(grp, "fixture_ids", []) or [])
                            base_order = list(getattr(grp, "base_order_fixture_ids", []) or [])
                            route_sheets = list(getattr(grp, "route_order_sheets", []) or [])
                            break
                except Exception:
                    logger.debug("effect_browser: failed to iterate groups for selection")
                    pass
            ids = _target_scope.ordered_members(ids, base_order)
            _target_scope.assign_targets(self, ids, base_order or ids)
            # ★ R168：设备组自己就是作用域（不是"从母集合里选的子集"）
            self._selected_group_scope_ids = list(ids)
            self._selected_group_subset_ids = []
            self._selected_group_spatial_groups = self._spatial_groups_from_route_sheets(
                route_sheets,
                ids,
            )
            self._selected_group_spatial_groups_raw = self._spatial_groups_from_route_sheets(
                route_sheets,
                ids,
                keep_single=True,
            )
            name = self._group_combo.itemText(index)
            self._selected_group_name = str(name or "")
            self._group_status_lbl.setText(f"{name}（{len(ids)} 台设备）")

        self._restore_group_ui_state()
        self._slice_context_key = ""
        self._manual_slice_frame_cache = {}
        self._update_slice_key_label()

    def _on_group_quick_all(self):
        self._group_combo.setCurrentIndex(0)

    def set_live_fixture_selection(self, ids, base_order_ids=None, name="TopView live selection",
                                   spatial_groups=None, *, subset_ids=None):
        ids_norm = _normalize_fixture_ids(ids)
        base_norm = _normalize_fixture_sequence_ids(base_order_ids) or _normalize_fixture_sequence_ids(ids)
        # Live selection carries TopView's explicit order; the mother sequence
        # remains separate reference metadata, unlike a saved group load.
        self._save_current_group_ui_state()
        self._scope_version = int(getattr(self, "_scope_version", 0)) + 1
        _target_scope.assign_targets(self, ids_norm, base_norm)
        self._selected_group_scope_ids = _compute_group_scope_ids(ids_norm, base_norm)  # ★R168
        self._selected_group_subset_ids = _subset_marked_ids(subset_ids, ids_norm)
        self._selected_group_spatial_groups = self._normalize_spatial_groups(spatial_groups, ids_norm)
        self._selected_group_spatial_groups_raw = self._normalize_spatial_groups(
            spatial_groups, ids_norm, keep_single=True)
        self._selected_group_name = str(name or "")
        try:
            self._group_combo.blockSignals(True)
            self._group_combo.setCurrentIndex(0)
            self._group_combo.blockSignals(False)
        except Exception:
            logger.debug("effect_browser: failed to reset group combo to index 0")
            pass
        try:
            if ids_norm and len(base_norm) != len(ids_norm):
                self._group_status_lbl.setText(f"{name}: {len(ids_norm)} fixtures / sequence {len(base_norm)}")
            else:
                self._group_status_lbl.setText(f"{name}: {len(ids_norm)}" if ids_norm else "未选择设备")
        except Exception:
            logger.debug("effect_browser: failed to set group status label")
            pass
        _target_scope.restore_live_editor_view(self)
        self._slice_context_key = ""
        self._manual_slice_frame_cache = {}
        self._update_slice_key_label()
        self._update_transport_state()

    def _normalize_spatial_groups(self, groups, fixture_ids: Optional[list] = None,
                                  keep_single: bool = False) -> List[List[str]]:
        # ★ R38-D3：传了 fixture_ids 时 effective_ids 根本用不上（原表达式
        #   fixture_ids or effective_ids 会短路），但旧写法已经先付了一次作用域
        #   解析的钱。一次 _current_output_spatial_groups 会经过本方法 2~3 次，
        #   每次都白算一遍。惰性化后语义与 `fixture_ids or effective_ids` 完全等价。
        if fixture_ids:
            allowed = set(_normalize_fixture_ids(fixture_ids))
        else:
            effective_ids, _base = self._effective_output_scope_ids()
            allowed = set(_normalize_fixture_ids(effective_ids))
        out: List[List[str]] = []
        seen_segments: set[tuple] = set()
        for raw in (groups or []):
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
            key = tuple(segment)
            if key in seen_segments:
                continue
            seen_segments.add(key)
            out.append(segment)
        used_ids: set[str] = set()
        for segment in out:
            if any(fid in used_ids for fid in segment):
                return []
            used_ids.update(segment)
        return out if (keep_single or len(out) > 1) else []

    def _spatial_groups_from_route_sheets(self, sheets, fixture_ids: Optional[list] = None,
                                          keep_single: bool = False) -> List[List[str]]:
        groups = []
        for sheet in (sheets or []):
            if not isinstance(sheet, dict):
                continue
            ordered = sheet.get("ordered_ids") or sheet.get("source_ids") or []
            groups.append(_normalize_fixture_sequence_ids(ordered))
        return self._normalize_spatial_groups(groups, fixture_ids, keep_single=keep_single)

    def _current_output_spatial_groups(self, fixture_ids: Optional[list] = None,
                                       keep_single: bool = False) -> List[List[str]]:
        ids = fixture_ids or self._effective_output_scope_ids()[0]
        # ★ R38-D2/R173：本方法是"每帧 × 每效果 × 每通道"被调的，且旧实现无论走
        #   哪条路都零缓存——包括最贵的那条（TopView 几何兜底算完返回 []）。
        #   键 = (作用域版本, Programmer 选择版本, TopView 布局版本, keep_single,
        #        入参内容签名)。前三个版本号覆盖了所有能改变结果的状态源；
        #   入参改按内容签名（而非 id()+len()）是因为子集作用域每次传入新建
        #   的等价列表，id 每帧不同必 miss，签名让等价列表也能命中，正确性
        #   仍由三个版本号兜底（任一变化整表清空）。
        cache = self._spatial_groups_cache
        tv = getattr(self, "_topview", None)
        # ★ R38-D2：版本号读取必须包 try。getattr 的默认值只兜 AttributeError，
        #   而 Qt 部件的 C++ 对象一旦销毁，任何属性访问抛的是 RuntimeError
        #   ("wrapped C/C++ object has been deleted") —— 默认值救不了，会直接把
        #   异常抛到 _tick_play 的整帧兜底里，变成"效果整帧不输出"。
        #   旧代码碰 tv 时全在 try 内，这里不能退化。
        try:
            ver_key = (
                int(getattr(self, "_scope_version", 0)),
                int(getattr(getattr(self, "_programmer_panel", None),
                            "_live_selection_version", 0) or 0),
                int(getattr(tv, "_layout_version", 0) or 0),
            )
        except Exception:
            logger.debug("effect_browser: spatial group version key unavailable")
            ver_key = None
        ckey = None if ver_key is None else (
            ver_key, bool(keep_single), fixture_ids_signature(ids))
        hit = None if ckey is None else cache.get(ckey)
        if hit is not None:
            return [list(g) for g in hit]
        # ★ R38-D1：优先用未过滤的原始分组做输入。旧写法读的是已被
        #   keep_single=False 过滤过的结论，单组场景恒为 [] → 必然掉进下面的
        #   TopView 几何兜底。改读 _raw 后本方法仍按调用方真实的 keep_single
        #   重新判定，返回值语义逐字节不变，只是不再被上游提前丢掉。
        source = (getattr(self, "_selected_group_spatial_groups_raw", None)
                  or getattr(self, "_selected_group_spatial_groups", None)
                  or [])
        groups = self._normalize_spatial_groups(
            source,
            ids,
            keep_single=keep_single,
        )
        if groups:
            return self._store_spatial_groups(ckey, groups)
        try:
            if tv is not None and hasattr(tv, "spatial_groups_for_fixture_ids"):
                groups = self._normalize_spatial_groups(
                    tv.spatial_groups_for_fixture_ids(ids),
                    ids,
                    keep_single=keep_single,
                )
                if groups:
                    return self._store_spatial_groups(ckey, groups)
        except Exception:
            logger.debug("effect_browser: failed to get spatial groups from TopView")
        return self._store_spatial_groups(ckey, [])

    def _store_spatial_groups(self, ckey: Optional[tuple],
                              groups: List[List[str]]) -> List[List[str]]:
        """★ R38-D2：写入空间分组缓存并返回副本（空结果同样缓存）。

        ckey 为 None 表示版本号不可读（部件已销毁等），此时只返回结果不缓存——
        宁可慢，也不能拿一个不可信的键把错误结果留在表里。
        """
        result = [list(g) for g in groups]
        if ckey is None:
            return result
        # ★ 同上：只逐出最旧一条（旧实现 >64 → clear() 每帧击穿其它效果）。
        _cache_put_bounded(self._spatial_groups_cache, ckey,
                           [list(g) for g in groups], 64)
        return result

    def _topview_layout_cluster_spatial_groups(self, fixture_ids: Optional[list] = None,
                                               keep_single: bool = False) -> List[List[str]]:
        # ★ R38-D3：惰性解析作用域，语义与 `fixture_ids or effective_ids` 等价。
        ids = _normalize_fixture_ids(
            fixture_ids or self._effective_output_scope_ids()[0])
        allowed = set(ids)
        if len(allowed) <= 1:
            return []
        tv = getattr(self, "_topview", None)
        if tv is None or not hasattr(tv, "_layout_group_clusters"):
            return []
        try:
            clusters = tv._layout_group_clusters()
        except Exception:
            logger.debug("effect_browser: failed to get layout group clusters from TopView")
            clusters = []
        groups: List[List[str]] = []
        for cluster in (clusters or []):
            raw_ids = [
                str(fid) for fid in (cluster.get("fixture_ids", []) or [])
                if str(fid) in allowed
            ] if isinstance(cluster, dict) else []
            if not raw_ids:
                continue
            try:
                if hasattr(tv, "_fixture_number_order_ids"):
                    raw_ids = list(tv._fixture_number_order_ids(raw_ids))
            except Exception:
                logger.debug("effect_browser: failed to get fixture number order ids")
                pass
            groups.append(raw_ids)
        return self._normalize_spatial_groups(groups, ids, keep_single=keep_single)

    def _resolved_spatial_scope(self) -> str:
        """解析「坐标系」下拉值：follow 跟随工程 light_control_mode 的**坐标系语义**
        （single=单控灯→per_shape 各自坐标系/whole=整体控灯→global 整体坐标系），
        显式 per_shape/global 原样返回。

        ★ 2026-08-04 用户概念澄清：坐标系(各自/整体)与控灯模式(整体/单控)是
        两个正交维度——坐标系是 effect 的坐标空间（per_shape=每个设备自己的圆、
        global=全部灯一个矩阵），控灯粒度（_control_mode）是「包内灯当一个还是
        逐个」。本方法只解析**坐标系**维度；粒度由 _resolved_control_mode 独立
        解析（_control_groups/_control_mode 随 params 注入 engine）。
        """
        scope = self._get_spatial_scope()
        if scope != "follow":
            return scope
        project = self._project_object()
        mode = str(getattr(project, "light_control_mode", "whole") or "whole").strip().lower()
        return "global" if mode == "whole" else "per_shape"

    def _resolved_control_mode(self) -> str:
        """解析「控灯粒度」下拉值：follow 跟随工程 light_control_mode 原值
        （single=单控灯/whole=整体控灯），显式 single/whole 原样返回。

        与坐标系(spatial_scope)正交：粒度决定包内配灯是共享同一效果值(整体控灯)
        还是逐个独立(单控灯)，任何坐标系下都成立。旧存档无此字段时默认 follow。
        """
        cm = self._get_control_mode()
        if cm != "follow":
            return cm if cm in ("single", "whole") else "whole"
        project = self._project_object()
        mode = str(getattr(project, "light_control_mode", "whole") or "whole").strip().lower()
        return mode if mode in ("single", "whole") else "whole"

    def _unit_groups_for_ids(self, fixture_ids: Optional[list] = None) -> List[List[str]]:
        """从工程 logical_devices(kind=unit) 提取微单元成员表，只保留与给定设备集
        相交 ≥2 台的单元。微单元在导入时创建（N 灯围一中心），
        engine 侧据此在 per_shape 坐标系下把单元成员聚拢并按质心极角排序。"""
        project = self._project_object()
        if project is None:
            return []
        cache = getattr(self, "_unit_groups_cache", None)
        if cache is None:
            cache = self._unit_groups_cache = IdentityCache(_FX_LIST_CACHE_MAX)
        # ★ R173 性能：旧键含 id(fixture_ids)，子集作用域每次传新建等价列表必
        #   miss；改内容签名做键（guard=project 仍 `is` 复核防 id 回收误命中），
        #   命中返回同一对象供下游 id(_unit_groups) 键继续复用。
        content_key = fixture_ids_signature(fixture_ids)
        cached = cache.get((project,), content_key)
        if cached is not None:
            return cached
        allowed = set(_normalize_fixture_ids(fixture_ids))
        if not allowed:
            cache.put((project,), content_key, [])
            return []
        units = []
        for ld in (getattr(project, "logical_devices", None) or []):
            if not isinstance(ld, dict):
                continue
            if ld.get("kind") not in ("unit", "package"):
                continue
            members = _normalize_fixture_ids(ld.get("member_fixture_ids") or [])
            present = [f for f in members if f in allowed]
            # 与引擎簇阈值一致(≥3，微单元定义 3-8 成员，审查 P3-5)；
            # ★ 2026-08-04 气爆包(kind=package, 1 气爆+4 灯)并入——单控/各自
            #   坐标系时包成员按包中心聚拢成簇，各包自己的圆 cw/ccw。
            if len(present) >= 3:
                units.append(present)
        cache.put((project,), content_key, units)
        return units

    def _fixture_groups_for_ids(self, fixture_ids: Optional[list] = None) -> List[List[str]]:
        """从工程 fixture_groups(设备组,TopView 圆组)提取分块成员表,只保留与
        给定设备集相交 ≥2 台的组。

        ★ 2026-08-08 per_shape 无配灯单元回退来源:logical_devices 恒空的工程
        (To The Moon 类,9 圆 210 台)per_shape 无微单元 → engine 按本组分块,
        每圆独立 0..m-1 序列,末尾圆不再被压进波形尾部。成员以 fixture_ids
        为准，base_order_fixture_ids 仅重排成员；engine 消费方以
        该对象 id 作缓存键 → 本方法按 (工程对象, 设备集身份) 缓存并返回
        缓存对象本身(对象稳定才不误命中,同 _unit_groups_for_ids 纪律)。
        """
        project = self._project_object()
        if project is None:
            return []
        cache = getattr(self, "_fixture_groups_cache", None)
        if cache is None:
            cache = self._fixture_groups_cache = IdentityCache(_FX_LIST_CACHE_MAX)
        content_key = fixture_ids_signature(fixture_ids)
        cached = cache.get((project,), content_key)
        if cached is not None:
            return cached
        allowed = set(_normalize_fixture_ids(fixture_ids))
        if not allowed:
            cache.put((project,), content_key, [])
            return []
        groups = []
        for gd in (getattr(project, "fixture_groups", None) or []):
            if not isinstance(gd, dict):
                continue
            members = _target_scope.ordered_members(
                gd.get("fixture_ids"), gd.get("base_order_fixture_ids"))
            present = [f for f in members if f in allowed]
            if len(present) >= 2:
                groups.append(present)
        cache.put((project,), content_key, groups)
        return groups

    def _per_shape_scope_hint(self, fixture_ids: Optional[list] = None) -> str:
        """per_shape 分块来源提示(2026-08-08 防静默降级)。

        To The Moon 末尾圆「卡住后全亮」教训:降级必须可见——无配灯单元时告知
        走了设备组分块回退,或完全不可用(整体扁平序列,建议先建立设备组)。
        有单元/global 返回空串不打扰。播放路径在状态文本末尾拼接本提示。
        """
        if self._resolved_spatial_scope() != "per_shape":
            return ""
        ids = _normalize_fixture_ids(fixture_ids)
        if not ids:
            ids = _normalize_fixture_ids(self._effective_output_scope_ids()[0])
        if not ids:
            return ""
        if self._unit_groups_for_ids(ids):
            return ""
        sp = self._current_output_spatial_groups(ids)
        if sp:
            return f"｜per_shape 已按 {len(sp)} 个图形分组(无配灯单元)"
        fgroups = self._fixture_groups_for_ids(ids)
        if fgroups:
            return "｜per_shape 无配灯单元,已按设备组回退分块"
        return "｜per_shape 无可用设备组:整体扁平序列(建议先建立设备组)"

    def _control_groups_for_ids(self, fixture_ids: Optional[list] = None) -> List[List[str]]:
        """从工程 logical_devices(kind=package) 提取「整套设备」成员表(如气爆+4灯)，
        只保留与给定设备集相交 ≥2 台的包。

        这是**控灯粒度**的载体：整体控灯时 effect 把包内成员当一个灯(共享值)，
        单控时逐个。与坐标系(spatial_scope)正交。性能同 _unit_groups_for_ids
        按 (工程对象, 设备集身份) 缓存。
        """
        project = self._project_object()
        if project is None:
            return []
        cache = getattr(self, "_control_groups_cache", None)
        if cache is None:
            cache = self._control_groups_cache = IdentityCache(_FX_LIST_CACHE_MAX)
        content_key = fixture_ids_signature(fixture_ids)
        cached = cache.get((project,), content_key)
        if cached is not None:
            return cached
        allowed = set(_normalize_fixture_ids(fixture_ids))
        if not allowed:
            cache.put((project,), content_key, [])
            return []
        groups = []
        for ld in (getattr(project, "logical_devices", None) or []):
            if not isinstance(ld, dict):
                continue
            if ld.get("kind") != "package":
                continue
            members = _normalize_fixture_ids(ld.get("member_fixture_ids") or [])
            present = [f for f in members if f in allowed]
            if len(present) >= 2:
                groups.append(present)
        cache.put((project,), content_key, groups)
        return groups

    def _light_capability_for_ids(self, fixture_ids: Optional[list] = None) -> dict:
        """设备能力判定：固定整体控 + 补光灯/效果灯角色（按包结构）。

        - **固定整体控**：包(kind=package)内任一成员 def 名含「数控」→ 整包
          设计上写死只能整体控（如数控 4 灯组），engine 侧即使单控灯模式也
          恒整体控（_fixed_whole_groups）。
        - **补光灯**：两重灯设备（当前至冬工程仅百变）——包内灯成员（def 名含
          灯/LED/RGBW/Spot/光源/Light）中，def 名含「底座」或到包中心偏移≈0
          （配灯圆布置时底座灯保持原位）者为**补光灯**；同包其余灯成员为
          **效果灯**。补光灯输出 = 效果灯总亮度/全开比例（engine 消费 _fill_map）。
        - 气爆/数控等**无补光灯**设备：fill_map 为空 → 常规控灯行为。

        返回 {"fixed_whole_groups": [[fid, ...], ...],
               "fill_map": {补光灯 fid: [同包效果灯 fid, ...]}}。
        按 (工程对象, 设备集签名) 缓存（性能同 _unit_groups_for_ids）。
        """
        project = self._project_object()
        if project is None:
            return {}
        cache = getattr(self, "_light_caps_cache", None)
        if cache is None:
            cache = self._light_caps_cache = IdentityCache(_FX_LIST_CACHE_MAX)
        content_key = fixture_ids_signature(fixture_ids)
        cached = cache.get((project,), content_key)
        if cached is not None:
            return cached
        allowed = set(_normalize_fixture_ids(fixture_ids))
        if not allowed:
            cache.put((project,), content_key, {})
            return {}
        # def_id → def 名映射（供角色判定）。
        defs = getattr(project, "fixture_defs", {}) or {}
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
        for fi in (getattr(project, "fixtures", []) or []):
            f = str(getattr(fi, "id", "") or "")
            if f and f in allowed:
                fid_def[f] = str(getattr(fi, "def_id", "") or "")

        def _type_name(fid: str) -> str:
            return def_name.get(fid_def.get(fid, ""), "")

        fixed_whole_groups: List[List[str]] = []
        fill_map: Dict[str, List[str]] = {}
        for ld in (getattr(project, "logical_devices", None) or []):
            if not isinstance(ld, dict):
                continue
            if ld.get("kind") != "package":
                continue
            members = _normalize_fixture_ids(ld.get("member_fixture_ids") or [])
            present = [m for m in members if m in allowed]
            if len(present) < 2:
                continue
            names = {m: _type_name(m) for m in present}
            # 固定整体控：包内任一成员类型名含「数控」（如数控配灯 4 灯组）。
            if any("数控" in str(n) for n in names.values()):
                fixed_whole_groups.append(present)
            # 补光灯/效果灯角色（仅两重灯设备；气爆等无底座灯 → fill 空 → 常规）。
            lights = [m for m in present
                      if _is_light_member_name(str(names.get(m, "")))]
            if len(lights) < 2:
                continue
            offsets = ld.get("local_offsets") or {}
            ds = []
            for m in lights:
                off = offsets.get(m)
                if isinstance(off, (list, tuple)) and len(off) >= 2:
                    try:
                        ds.append(math.hypot(float(off[0]), float(off[1])))
                    except Exception:
                        ds.append(1e9)
            median_r = sorted(ds)[len(ds) // 2] if ds else 0.0
            thr = max(1.0, median_r * 0.05) if median_r > 1e-6 else 1.0
            fill: List[str] = []
            eff: List[str] = []
            for m in lights:
                off = offsets.get(m)
                d = 1e9
                if isinstance(off, (list, tuple)) and len(off) >= 2:
                    try:
                        d = math.hypot(float(off[0]), float(off[1]))
                    except Exception:
                        d = 1e9
                # 底座灯：类型名含「底座」或位于包中心（距离≈0，圆布置保持原位）。
                if "底座" in str(names.get(m, "")) or d < thr:
                    fill.append(m)
                else:
                    eff.append(m)
            if fill and eff:
                for f in fill:
                    fill_map[f] = list(eff)
        out: Dict[str, Any] = {}
        if fixed_whole_groups:
            out["fixed_whole_groups"] = fixed_whole_groups
        if fill_map:
            out["fill_map"] = fill_map
        cache.put((project,), content_key, out)
        return out

    def _inject_output_spatial_groups(self, params: Optional[dict], fixture_ids: Optional[list] = None) -> dict:
        out = dict(params or {})
        fixture_ids = _subset_scope_override(self, out, fixture_ids)
        # ★ R38-D3：同 _normalize_spatial_groups，只在前两个来源都空时才解析作用域。
        ids = _target_scope.spatial_target_ids(self, out, fixture_ids)
        keep_single = _wing_block_mode_active(out)
        groups = self._current_output_spatial_groups(ids, keep_single=keep_single)
        if groups:
            out["_output_spatial_groups"] = groups
        else:
            existing = self._normalize_spatial_groups(
                out.get("_output_spatial_groups"),
                ids,
                keep_single=keep_single,
            )
            if existing:
                out["_output_spatial_groups"] = existing
            else:
                out.pop("_output_spatial_groups", None)
        # 坐标系 follow=跟随全局灯控（见 _resolved_spatial_scope）；微单元分组注入给
        # engine（仅 per_shape 时被消费，global 时 engine 忽略 _unit_groups）。
        out["spatial_scope"] = self._resolved_spatial_scope()
        unit_groups = self._unit_groups_for_ids(ids)
        if unit_groups:
            out["_unit_groups"] = unit_groups
            out.pop("_fixture_groups", None)
        else:
            out.pop("_unit_groups", None)
            # ★ 2026-08-08 per_shape 无配灯单元回退来源:工程设备组(fixture_groups,
            #   TopView 圆组)分块——engine 侧 _per_shape_unit_blocks 在 _unit_groups
            #   为空时消费本参数;有单元时不存在该键,单元路径逐字节不变。
            fgroups = self._fixture_groups_for_ids(ids)
            if fgroups:
                out["_fixture_groups"] = fgroups
            else:
                out.pop("_fixture_groups", None)
        # 控灯粒度(与坐标系正交)：整体控灯 → 包(kind=package)成员共享同一效果值
        # （4 灯当一个灯）；单控灯 → 包成员逐个独立值。engine 侧 _apply_control_groups
        # 消费 _control_groups + _control_mode。粒度下拉独立设置，follow 时跟随
        # Programmer 全局「单控灯/整体控灯」按钮。
        out["_control_mode"] = self._resolved_control_mode()
        control_groups = self._control_groups_for_ids(ids)
        if control_groups:
            out["_control_groups"] = control_groups
        else:
            out.pop("_control_groups", None)
        # 设备能力：固定整体控 + 补光灯/效果灯角色（仅百变两重灯，engine 侧消费）。
        caps = self._light_capability_for_ids(ids)
        if caps:
            out["_light_caps"] = caps
        else:
            out.pop("_light_caps", None)
        _inject_subset_marked(self, out, ids)
        return out

    def _effect_addr_maps_for_fixtures(self, fixtures: list) -> Dict[str, Dict[str, tuple]]:
        # ★ 性能：地址映射只依赖 fixtures 对象身份 + 工程 defs 容器身份；播放期
        #   稳定。旧实现每帧用 4889 元组全量 cache-key（含逐台 getattr/def 签名），
        #   在至冬 7.0 规模上单帧多花 ~30ms。这里改为 id 键，_invalidate_tick_caches
        #   负责在 TopView/换工程/设备变化时清缓存。
        try:
            project = self._project_object()
            raw_defs = getattr(project, "fixture_defs", {}) if project is not None else {}
        except Exception:
            raw_defs = {}
        key = (id(fixtures), len(fixtures or []), id(raw_defs))
        cached = self._effect_addr_cache.get(key)
        if cached is not None and cached[0] is fixtures and cached[1] is raw_defs:
            return cached[2]
        maps: Dict[str, Dict[str, tuple]] = {}
        candidate_types = _addr_candidate_types(self)   # 内置 + 工程通道（含功能通道）
        for fi in fixtures or []:
            fid = str(getattr(fi, "id", "") or "")
            if not fid:
                continue
            fmap: Dict[str, tuple] = {}
            for ch_type in candidate_types:
                offsets = self._resolve_ch_offsets(fi, ch_type)
                if offsets:
                    mapped = tuple(
                        (int(getattr(fi, "universe", 1) or 1),
                         int(getattr(fi, "start_channel", 1) or 1) - 1 + int(off))
                        for off in offsets
                        if 0 <= int(getattr(fi, "start_channel", 1) or 1) - 1 + int(off) < 512
                    )
                    fmap[str(ch_type)] = mapped
                    fmap[_effect_address_key(ch_type)] = mapped
                    fmap[_norm_channel_type(ch_type)] = mapped
            maps[fid] = fmap
        # ★ 淘汰只逐出最旧一条（旧实现 >16→clear() 会击穿其它运行中效果）。
        _cache_put_bounded(self._effect_addr_cache, key,
                           (fixtures, raw_defs, maps), _ADDR_MAP_CACHE_MAX)
        return maps

    def _write_channel_vectorized(self, bufs: dict, acts: dict,
                                  fx_list: list, values: list,
                                  ch_type: str, allowed_ch: list,
                                  compute_cache: dict) -> int:
        """把一个通道类型的一整帧值向量化写入 NM effect 层。

        与旧逐台 Python 循环语义一致：
          - 直接通道：非电机 HTP(max)，电机后写覆盖；
          - dimmer/level 无物理 dimmer 时走 RGBW 亮度模拟（读当前 universe 基色）。
        返回写入地址数。
        """
        if not _channel_matches(ch_type, allowed_ch):
            return 0
        output_keys = _address_keys_for_output(ch_type, allowed_ch)
        target_norm = _norm_channel_type(ch_type)
        is_motor = target_norm in ("motor_x", "motor_y", "motor_z")
        direct_uni, rgbw_uni = self._collect_channel_structure(
            fx_list, output_keys, target_norm)
        import numpy as np
        values_arr = np.asarray(values, dtype=np.float64)
        cnt = self._write_direct_channel_vectorized(
            bufs, acts, direct_uni, values_arr, is_motor)
        cnt += self._write_rgbw_channel_vectorized(
            bufs, acts, rgbw_uni, values_arr, compute_cache)
        return cnt

    def _collect_channel_structure(self, fx_list, output_keys, target_norm):
        """收集一个通道类型的静态地址结构（不依赖每帧 values）。

        返回 (direct_uni, rgbw_uni)，其中每个 universe 只存地址 + 对应 fixture
        下标/设备索引；每帧只把 values 映射进去，避免 4889 台 Python 循环重建。
        """
        import numpy as np
        try:
            project = self._project_object()
            raw_defs = getattr(project, "fixture_defs", {}) if project is not None else {}
        except Exception:
            raw_defs = {}
        key = (id(fx_list), tuple(output_keys), target_norm, id(raw_defs))
        cached = self._effect_channel_structure_cache.get(key)
        if cached is not None and cached[0] is fx_list and cached[1] is raw_defs:
            return cached[2]
        addr_maps = self._effect_addr_maps_for_fixtures(fx_list)
        direct_uni = {}
        rgbw_uni = {}
        for idx, fi in enumerate(fx_list):
            fid = str(getattr(fi, "id", "") or "")
            am = addr_maps.get(fid, {})
            uni = int(getattr(fi, "universe", 1) or 1)
            ch_addrs = _fixture_addr_for_keys(am, output_keys, target_norm)
            if ch_addrs:
                bucket = direct_uni.setdefault(uni, ([], []))
                for _u, addr in ch_addrs:
                    bucket[0].append(addr)
                    bucket[1].append(idx)
                continue
            if target_norm in ("dimmer", "level"):
                r = am.get("R") or ()
                g = am.get("G") or ()
                b = am.get("B") or ()
                w = am.get("W") or ()
                if r or g or b or w:
                    has_rgb = bool(r or g or b)
                    bucket = rgbw_uni.setdefault(uni, {
                        "r": [], "g": [], "b": [], "w": [],
                        "r_fx": [], "g_fx": [], "b_fx": [], "w_fx": [],
                        "r_dev": [], "g_dev": [], "b_dev": [], "w_dev": [],
                        "has_rgb": [],
                    })
                    dev_id = len(bucket["has_rgb"])
                    bucket["has_rgb"].append(has_rgb)
                    bucket["r"].extend(a for _, a in r)
                    bucket["r_fx"].extend([idx] * len(r))
                    bucket["r_dev"].extend([dev_id] * len(r))
                    bucket["g"].extend(a for _, a in g)
                    bucket["g_fx"].extend([idx] * len(g))
                    bucket["g_dev"].extend([dev_id] * len(g))
                    bucket["b"].extend(a for _, a in b)
                    bucket["b_fx"].extend([idx] * len(b))
                    bucket["b_dev"].extend([dev_id] * len(b))
                    bucket["w"].extend(a for _, a in w)
                    bucket["w_fx"].extend([idx] * len(w))
                    bucket["w_dev"].extend([dev_id] * len(w))
        direct_result = {
            uni: (
                np.asarray(addrs, dtype=np.intp),
                np.asarray(fxs, dtype=np.intp),
            )
            for uni, (addrs, fxs) in direct_uni.items()
        }
        rgbw_result = {}
        for uni, bucket in rgbw_uni.items():
            rgbw_result[uni] = {
                "r": np.asarray(bucket["r"], dtype=np.intp),
                "g": np.asarray(bucket["g"], dtype=np.intp),
                "b": np.asarray(bucket["b"], dtype=np.intp),
                "w": np.asarray(bucket["w"], dtype=np.intp),
                "r_fx": np.asarray(bucket["r_fx"], dtype=np.intp),
                "g_fx": np.asarray(bucket["g_fx"], dtype=np.intp),
                "b_fx": np.asarray(bucket["b_fx"], dtype=np.intp),
                "w_fx": np.asarray(bucket["w_fx"], dtype=np.intp),
                "r_dev": np.asarray(bucket["r_dev"], dtype=np.intp),
                "g_dev": np.asarray(bucket["g_dev"], dtype=np.intp),
                "b_dev": np.asarray(bucket["b_dev"], dtype=np.intp),
                "w_dev": np.asarray(bucket["w_dev"], dtype=np.intp),
                "has_rgb": np.asarray(bucket["has_rgb"], dtype=bool),
            }
        result = (direct_result, rgbw_result)
        # ★ 同上：只逐出最旧一条，不整表清空（清空 = 击穿其它效果）。
        _cache_put_bounded(self._effect_channel_structure_cache, key,
                           (fx_list, raw_defs, result), _CH_STRUCT_CACHE_MAX)
        return result

    def _write_direct_channel_vectorized(self, bufs, acts, direct_uni,
                                         values_arr, is_motor) -> int:
        import numpy as np
        cnt = 0
        for uni, (addrs, fxs) in direct_uni.items():
            if addrs.size == 0:
                continue
            if uni not in bufs:
                bufs[uni] = np.zeros(512, dtype=np.uint8)
                acts[uni] = np.zeros(512, dtype=bool)
            arr = bufs[uni]
            a = addrs
            # ★ R194/DM-05：uint8 保险丝——float→uint8 回绕（>255→mod 256、
            # 负→高值）绕过一切下游防线，前置饱和钳制（stairsin 上游已修，
            # 此处兜住自定义曲线/未来 kernel）。
            v = np.clip(values_arr[fxs], 0, 255).astype(np.uint8)
            if is_motor:
                arr[a] = v
            else:
                arr[a] = np.maximum(arr[a], v)
            acts[uni][a] = True
            cnt += int(a.size)
        return cnt

    def _write_rgbw_channel_vectorized(self, bufs, acts, rgbw_uni,
                                       values_arr, compute_cache) -> int:
        import numpy as np
        cnt = 0
        for uni, bucket in rgbw_uni.items():
            r = bucket["r"]
            g = bucket["g"]
            b = bucket["b"]
            w = bucket["w"]
            if not (r.size or g.size or b.size or w.size):
                continue
            if uni not in bufs:
                bufs[uni] = np.zeros(512, dtype=np.uint8)
                acts[uni] = np.zeros(512, dtype=bool)
            arr = bufs[uni]
            current = None
            if self._nm is not None and hasattr(self._nm, "compute_universe_now"):
                _uni_key = int(uni)
                if _uni_key not in compute_cache:
                    try:
                        current = self._nm.compute_universe_now(
                            int(uni),
                            apply_output_smoothing=False,
                            exclude_layers=["effect", "clip_playback", "clip_preview", "timeline"],
                            exclude_ltp_editor_layers={"effect"},
                            apply_xy_reference=False,
                        )
                    except TypeError:
                        try:
                            current = self._nm.compute_universe_now(
                                int(uni),
                                apply_output_smoothing=False,
                                exclude_layers=["effect", "clip_playback", "clip_preview", "timeline"],
                            )
                        except Exception:
                            current = None
                    except Exception:
                        current = None
                    compute_cache[_uni_key] = current
                else:
                    current = compute_cache[_uni_key]
            base_r = current[r] if (current is not None and r.size) else np.zeros(r.size, dtype=np.uint8)
            base_g = current[g] if (current is not None and g.size) else np.zeros(g.size, dtype=np.uint8)
            base_b = current[b] if (current is not None and b.size) else np.zeros(b.size, dtype=np.uint8)
            base_w = current[w] if (current is not None and w.size) else np.zeros(w.size, dtype=np.uint8)
            device_count = int(bucket["has_rgb"].size)
            sums = np.zeros(device_count, dtype=np.float64)
            if r.size:
                sums += np.bincount(bucket["r_dev"], weights=base_r.astype(np.float64), minlength=device_count)
            if g.size:
                sums += np.bincount(bucket["g_dev"], weights=base_g.astype(np.float64), minlength=device_count)
            if b.size:
                sums += np.bincount(bucket["b_dev"], weights=base_b.astype(np.float64), minlength=device_count)
            if w.size:
                sums += np.bincount(bucket["w_dev"], weights=base_w.astype(np.float64), minlength=device_count)
            all_zero = sums == 0
            if r.size:
                a = r
                dev = bucket["r_dev"]
                lev = values_arr[bucket["r_fx"]] / 255.0
                default = np.where(all_zero[dev], 255, base_r).astype(np.uint8)
                out = np.clip(np.rint(default.astype(np.float64) * lev), 0, 255).astype(np.uint8)  # R194/DM-05 保险丝
                arr[a] = np.maximum(arr[a], out)
                acts[uni][a] = True
                cnt += int(a.size)
            if g.size:
                a = g
                dev = bucket["g_dev"]
                lev = values_arr[bucket["g_fx"]] / 255.0
                default = np.where(all_zero[dev], 255, base_g).astype(np.uint8)
                out = np.clip(np.rint(default.astype(np.float64) * lev), 0, 255).astype(np.uint8)  # R194/DM-05 保险丝
                arr[a] = np.maximum(arr[a], out)
                acts[uni][a] = True
                cnt += int(a.size)
            if b.size:
                a = b
                dev = bucket["b_dev"]
                lev = values_arr[bucket["b_fx"]] / 255.0
                default = np.where(all_zero[dev], 255, base_b).astype(np.uint8)
                out = np.clip(np.rint(default.astype(np.float64) * lev), 0, 255).astype(np.uint8)  # R194/DM-05 保险丝
                arr[a] = np.maximum(arr[a], out)
                acts[uni][a] = True
                cnt += int(a.size)
            if w.size:
                a = w
                dev = bucket["w_dev"]
                lev = values_arr[bucket["w_fx"]] / 255.0
                has_rgb_dev = bucket["has_rgb"][dev]
                default = np.where(all_zero[dev], np.where(has_rgb_dev, 0, 255), base_w).astype(np.uint8)
                out = np.clip(np.rint(default.astype(np.float64) * lev), 0, 255).astype(np.uint8)  # R194/DM-05 保险丝
                arr[a] = np.maximum(arr[a], out)
                acts[uni][a] = True
                cnt += int(a.size)
        return cnt

    def _get_active_fixtures(self) -> list:
        """Resolve actual targets; legacy unbound ALL differs from selected empty."""
        if self._project is None:
            return []
        project = self._project_object()
        raw_fx = getattr(project, "fixtures", None) or []
        all_fx = _stable_all_fixtures(
            _ensure_cache_dict(self, "_get_active_fx_cache"), raw_fx)
        group_ids, _base_order_ids = self._effective_output_scope_ids()
        if not group_ids:
            return all_fx if _target_scope.empty_targets_are_all(self) else []
        by_id = {fi.id: fi for fi in all_fx}
        out = []
        seen = set()
        for fid in group_ids:
            sfid = str(fid)
            fi = by_id.get(sfid)
            if fi is not None and sfid not in seen:
                out.append(fi)
                seen.add(sfid)
        return out

    def _positions_for_fixtures(self, fixtures: list) -> dict:
        pos = {}
        top_map = {}
        if self._topview is not None:
            try:
                rows = (self._topview.get_all_fixture_positions()
                        if hasattr(self._topview, "get_all_fixture_positions")
                        else self._topview.get_fixture_positions())
                for entry in rows or []:
                    fid = str(entry.get("id") or entry.get("fixture_id") or "")
                    if fid:
                        top_map[fid] = {
                            "pos_x": float(entry.get("pos_x", entry.get("x", 0.0)) or 0.0),
                            "pos_y": float(entry.get("pos_y", entry.get("y", 0.0)) or 0.0),
                        }
            except Exception:
                logger.debug("effect_browser: failed to get fixture positions from TopView")
                top_map = {}
        for fi in fixtures or []:
            fid = str(getattr(fi, "id", ""))
            pos[fid] = top_map.get(fid, {
                "pos_x": float(getattr(fi, "pos_x", 0.0) or 0.0),
                "pos_y": float(getattr(fi, "pos_y", 0.0) or 0.0),
            })
        return pos

    def _ordered_fixtures_for_effect(self, fixtures: list, positions: dict,
                                     sort_mode: str, base_order_ids: list,
                                     params: Dict[str, Any]) -> list:
        from .effect_engine import (apply_effect_direction, apply_spatial_scope,
                                    sort_fixtures)
        params = _effect_chain_params(self, params, positions)
        ordered = sort_fixtures(
            fixtures, positions, sort_mode,
            base_order_ids=list(base_order_ids or []),
            skip_base_order=bool(params.get("_skip_base_order", False)))
        # per_shape 时传 _unit_groups → 方向只在各单元块内生效（2026-09-12 方向修复）。
        ordered = apply_spatial_scope(ordered, positions, params, sort_mode=sort_mode)
        return apply_effect_direction(
            ordered, positions, params.get("_app_direction", "default"),
            unit_groups=_direction_groups.direction_groups(get_engine(), ordered, params))



    def _on_play_toggle(self):
        if self._current_selection_effect_active():
            self._stop_current_selection_effects()
            return
        self._start_play()

    def _start_play(self):
        if not _operating_preferences.prepare_play(self):
            return
        if self._nm is None or self._project is None:
            # 降级：发射旧信号给 Programmer 处理
            self._on_apply()
            return
        self._save_current_group_ui_state()
        self._external_state = None
        if self._slice_key is None and not self._external_states:
            self._manual_slice_latched = False
            self._clear_current_manual_slice_cache()
        if self._has_channel_forms():
            form = self._active_channel_form()
            if form is None:
                return
            base_params = self._get_current_params()
            base_params.pop("_channel_forms", None)
            ch = self._channel_form_type(form)
            if self._channel_param_key:
                base_params[self._channel_param_key] = ch
            seen_form_widgets = set()
            for key, widget in (form.get("params", {}) or {}).items():
                if isinstance(widget, RangeParamWidget):
                    if id(widget) in seen_form_widgets:
                        continue
                    seen_form_widgets.add(id(widget))
                    base_params.update(widget.get_values())
                else:
                    base_params[key] = widget.get_value()
            base_params["_state_speed_factor"] = float(form.get("speed_factor", 1.0) or 0.0)
            ch_types = [ch] if ch else self._get_selected_ch_types()
            sort_mode = self._get_sort_mode()
            base_params = self._params_with_current_output_scope(base_params)
            track_type = self._manual_effect_track_type(self._selected_id, ch_types)
            self.play_preset_for_type(track_type, self._selected_id, base_params, ch_types, sort_mode)
            form["playing"] = True
            form["_ltp_takeover_pending"] = True
            self._sync_transport_controls_to_active_form()
            self._status_lbl.setText(
                f"播放中：{self._selected_id} / {ch}" + self._per_shape_scope_hint())
            return
        self._play_current_selection_as_independent_effect()

    def _on_stop(self):
        _audio_runtime.release_audio_runtime(self)
        self._is_playing = False
        self._play_timer.stop()
        self._status_lbl.setText("已全部停止")
        # 重置虚拟时间
        self._virtual_t = 0.0
        self._last_real_t = 0.0
        for form in list(self._channel_forms or []):
            form["playing"] = False
            form["virtual_t"] = 0.0
            form["last_real_t"] = 0.0
        # 停止播放时清除 Effect 层；切片模式下保留最后写出的帧，交给主页面全清空释放。
        if self._nm is not None and self._slice_key is None:
            self._nm.clear_layer("effect")
        # 同步清掉外部驱动状态（避免下次手动 Play 还沿用上次的外部参数）
        self._external_state = None
        self._external_states.clear()
        self._refresh_quick_effect_lists()
        if self._slice_key is None:
            self._clear_current_manual_slice_cache()
            self._manual_slice_latched = False
        else:
            self._manual_slice_latched = True
        self._sync_transport_controls_to_active_form()
        self._update_transport_state()

    def _on_global_speed_changed(self, val: int):
        """全局效果速度倍率滑块回调。val 范围 5~400，对应 0.05x~4.0x。"""
        if val <= 0:
            self.set_global_speed(0.0)
        else:
            self.set_global_speed(_slider_pct_to_float(val / 1000.0, 0.01, 16.0, True))

    def _on_global_speed_spin_changed(self, factor: float):
        self.set_global_speed(factor)

    def set_global_speed(self, factor: float):
        try:
            self.controlTrajectoryEvent.emit(
                "effect|global_speed", float(factor))
        except Exception:
            pass
        """外部接口：直接设置全局速度倍率（0.05~4.0）。"""
        factor = max(0.0, min(16.0, float(factor)))
        if self._has_channel_forms() and not self._updating_speed_controls:
            form = self._active_channel_form()
            if form is not None:
                if abs(factor - float(form.get("speed_factor", 1.0) or 0.0)) > 1e-9:
                    self._clear_current_manual_slice_cache()
                form["speed_factor"] = factor
                ch = self._channel_form_type(form)
                group_key = self._current_live_selection_key()
                for key, state in list(getattr(self, "_external_states", {}).items()):
                    if len(key) < 2 or key[1] != group_key:
                        continue
                    if not isinstance(state, dict) or state.get("preset_id") != self._selected_id:
                        continue
                    st_ch = [str(c) for c in (state.get("ch_types", []) or []) if str(c)]
                    if st_ch == [ch]:
                        state["speed_factor"] = factor
                self._sync_transport_controls_to_active_form()
                self._on_param_changed(str(self._channel_param_key or ""), self._capture_channel_forms())
                return
        _master_speed.set_factor(self, factor)
        if hasattr(self, '_global_speed_sld'):
            self._global_speed_sld.blockSignals(True)
            if factor <= 0.0:
                self._global_speed_sld.setValue(0)
            else:
                self._global_speed_sld.setValue(int(_float_to_slider_pct(factor, 0.01, 16.0, True) * 1000))
            self._global_speed_sld.blockSignals(False)
        if hasattr(self, '_global_speed_lbl'):
            self._global_speed_lbl.setText(f"{factor:.2f}x")
        if hasattr(self, '_global_speed_spin'):
            self._global_speed_spin.blockSignals(True)
            self._global_speed_spin.setValue(factor)
            self._global_speed_spin.blockSignals(False)

    def _key_modifiers_value(self, modifiers) -> int:
        try:
            return int(modifiers.value)
        except Exception:
            logger.debug("effect_browser: failed to get modifiers.value, trying raw modifiers")
            try:
                return int(modifiers)
            except Exception:
                logger.debug("effect_browser: failed to get int modifiers, returning 0")
                return 0

    def _event_key_parts(self, event) -> tuple[int, int]:
        key = int(event.key())
        mods = self._key_modifiers_value(event.modifiers())
        allowed = (
            Qt.KeyboardModifier.ShiftModifier |
            Qt.KeyboardModifier.ControlModifier |
            Qt.KeyboardModifier.AltModifier |
            Qt.KeyboardModifier.MetaModifier
        )
        return key, mods & self._key_modifiers_value(allowed)

    def _format_slice_key(self, key: Optional[int] = None,
                          modifiers: Optional[int] = None) -> str:
        key = self._slice_key if key is None else key
        modifiers = self._slice_key_modifiers if modifiers is None else modifiers
        if key is None:
            return "未绑定"
        try:
            text = QKeySequence(int(key) | int(modifiers or 0)).toString(
                QKeySequence.SequenceFormat.NativeText)
            if text:
                return text
        except Exception:
            logger.debug("effect_browser: failed to format key sequence text")
            pass
        return f"Key {int(key)}"

    def _update_slice_key_label(self):
        if getattr(self, "_updating_slice_context", False):
            return
        self._updating_slice_context = True
        try:
            self._sync_manual_slice_context()
            binding = self._slice_bindings_by_context.get(self._slice_context_key)
            if (
                binding is None
                and self._legacy_slice_binding is not None
                and not self._slice_bindings_by_context
            ):
                binding = self._legacy_slice_binding
                self._slice_bindings_by_context[self._slice_context_key] = binding
                self._legacy_slice_binding = None
            if binding is not None:
                self._slice_key, self._slice_key_modifiers = int(binding[0]), int(binding[1] or 0)
            else:
                self._slice_key, self._slice_key_modifiers = None, 0
            if hasattr(self, "_slice_key_lbl"):
                self._slice_key_lbl.setText(self._format_slice_key())
            self._refresh_slice_shortcut()
        finally:
            self._updating_slice_context = False

    def _refresh_slice_shortcut(self):
        if self._slice_shortcut is not None:
            try:
                self._slice_shortcut.deleteLater()
            except Exception:
                logger.debug("effect_browser: failed to delete slice shortcut")
                pass
            self._slice_shortcut = None
        if self._slice_key is None:
            return
        try:
            seq = QKeySequence(int(self._slice_key) | int(self._slice_key_modifiers or 0))
            shortcut = QShortcut(seq, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._on_slice_shortcut_activated)
            self._slice_shortcut = shortcut
        except Exception:
            logger.exception("创建切片快捷键失败: slice_key=%s", self._slice_key)
            self._slice_shortcut = None

    def _on_slice_shortcut_activated(self):
        if self._capturing_slice_key or self._focused_text_input_active():
            return
        self._advance_manual_slice()

    def _begin_slice_key_capture(self):
        self._capturing_slice_key = True
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if hasattr(self, "_slice_key_lbl"):
            self._slice_key_lbl.setText("按键...")
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText("请按下要绑定的切片键")

    def _clear_slice_key_binding(self):
        ctx = self._sync_manual_slice_context()
        self._slice_key = None
        self._slice_key_modifiers = 0
        self._slice_bindings_by_context.pop(ctx, None)
        if self._legacy_slice_binding is not None:
            self._legacy_slice_binding = None
        self._capturing_slice_key = False
        self._manual_slice_latched = False
        self._manual_slice_frame_cache.clear()
        self._manual_slice_frame_cache_by_context.pop(ctx, None)
        self._update_slice_key_label()
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText("切片键已清除")

    def _is_modifier_key(self, key: int) -> bool:
        return key in (
            int(Qt.Key.Key_Shift), int(Qt.Key.Key_Control),
            int(Qt.Key.Key_Alt), int(Qt.Key.Key_Meta),
            int(Qt.Key.Key_AltGr),
        )

    def _focused_text_input_active(self) -> bool:
        fw = self.focusWidget()
        return _slice_control.focus_blocks_slice(self._slice_key, isinstance(
            fw, (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, WingDirectionToolbar)))

    def keyPressEvent(self, event):
        if self._handle_slice_key_event(event):
            return
        super().keyPressEvent(event)

    def _handle_slice_key_event(self, event) -> bool:
        if event.isAutoRepeat():
            return False
        key, mods = self._event_key_parts(event)
        if self._capturing_slice_key:
            if self._is_modifier_key(key):
                event.accept()
                return True
            ctx = self._sync_manual_slice_context()
            self._slice_key = key
            self._slice_key_modifiers = mods
            self._slice_bindings_by_context[ctx] = (key, mods)
            self._capturing_slice_key = False
            self._manual_slice_latched = True
            self._manual_slice_frame_cache.clear()
            self._manual_slice_frame_cache_by_context.pop(ctx, None)
            self._update_slice_key_label()
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText(f"切片键已绑定：{self._format_slice_key()}")
            event.accept()
            return True
        if self._slice_key is None:
            return False
        if self._focused_text_input_active():
            return False
        if key == int(self._slice_key) and mods == int(self._slice_key_modifiers or 0):
            if self._slice_shortcut is None:
                self._advance_manual_slice()
            event.accept()
            return True
        return False

    def _can_manual_slice(self) -> bool:
        self._sync_manual_slice_context()
        if not self._selected_id:
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("请先选择一个预设")
            return False
        if self._nm is None or self._project is None:
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("切片键需要当前项目和网络输出")
            return False
        if _slice_control.externally_driven(self):
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("音频识别/外部驱动开启时切片键无效")
            return False
        if (
            self._current_manual_slice_speed() > 1e-6
            and not self._current_effect_motion_speed_is_zero()
        ):
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("当前效果/sheet 或预设速度为 0 时切片键才生效")
            return False
        if not self._get_active_fixtures():
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("没有可输出的设备")
            return False
        return True

    def _manual_slice_count(self) -> int:
        self._sync_manual_slice_context()
        try:
            params = self._get_current_params()
            fixtures = self._get_active_fixtures()
            positions = self._positions_for_fixtures(fixtures)
            # ★ R141：与真正写帧的路径同口径（按 ch_type 的类型作用域再收窄），
            #   否则带类型作用域时切片数与引擎真实分组数不符 → 空推进。
            fixtures, positions = _slice_scoped_inputs(self, fixtures, positions)
            ordered = self._ordered_fixtures_for_effect(
                fixtures, positions, self._get_sort_mode(),
                list(self._selected_group_base_order_ids or []), params)
            groups = build_effect_spatial_groups(ordered, params)
            return max(1, len(groups))
        except Exception as exc:
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText(f"切片分组计算失败：{exc}")
            return 1

    def _prepare_manual_slice_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        for key in (
            "speed", "rate", "h_speed", "s_speed", "v_speed",
            "radius_speed", "return_speed",
        ):
            if key not in params:
                continue
            try:
                if abs(float(params.get(key) or 0.0)) <= 1e-6:
                    params[key] = 1.0
            except Exception:
                logger.debug("effect_browser: failed to adjust manual slice speed param")
                pass
        params["_manual_slice"] = True
        return params

    def _rotate_manual_slice_values(self, values: list, fixtures: list,
                                    positions: dict, sort_mode: str,
                                    base_order_ids: list,
                                    params: Dict[str, Any]) -> list:
        if not values or not fixtures:
            return values
        # ★ R141：组域必须与 `values` 的下标域同口径（调用方已收窄时本步是 no-op）
        fixtures, positions = _rotate_scope_narrow(self, fixtures, positions, params)
        try:
            ordered = self._ordered_fixtures_for_effect(fixtures, positions, sort_mode, base_order_ids, params)
            groups = build_effect_spatial_groups(ordered, params)
        except Exception as exc:
            _slice_status(self, f"切片旋转分组失败：{exc}")
            groups = [[(i, fixtures[i]) for i in range(min(len(values), len(fixtures)))]]
        groups = [
            [(int(orig_i), fi) for orig_i, fi in group if 0 <= int(orig_i) < len(values)]
            for group in groups
        ]
        groups = [group for group in groups if group]
        n = len(groups)
        if n <= 1:
            return values
        self._sync_manual_slice_context()
        shift = int(getattr(self, "_manual_slice_index", 0) or 0) % n
        if shift == 0:
            return values
        source = [dict(values[group[0][0]] or {}) for group in groups]
        rotated = source[-shift:] + source[:-shift]
        out = list(values)
        for group, val in zip(groups, rotated):
            for orig_i, _fi in group:
                out[orig_i] = dict(val)
        return out

    def _on_slice_stride_changed(self, value: int):
        """切片粒度(步距)滑块/数字框回调，两个控件互相同步。"""
        try:
            value = max(1, min(32, int(value)))
        except Exception:
            logger.debug("effect_browser: failed to clamp slice stride value")
            value = 1
        self._slice_stride = value
        for w in (getattr(self, "_slice_stride_sld", None),
                  getattr(self, "_slice_stride_spin", None)):
            if w is not None and w.value() != value:
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)

    def _current_slice_fade_seconds(self) -> float:
        spin = getattr(self, "_slice_fade_spin", None)
        if spin is None:
            return float(getattr(self, "_slice_fade_duration", 0.0) or 0.0)
        try:
            return max(0.0, float(spin.value()))
        except Exception:
            logger.debug("effect_browser: failed to read slice fade spin value")
            return 0.0

    def _advance_manual_slice(self):
        if not self._can_manual_slice():
            return
        ctx = self._sync_manual_slice_context()
        base_count = self._manual_slice_count()  # 基本单元(台/组)总数
        stride = max(1, int(getattr(self, "_slice_stride", 1) or 1))
        # _manual_slice_index 直接是“沿设备序列旋转的格数”，按步距推进、对基本单元数取模。
        self._manual_slice_index = (int(self._manual_slice_index) + stride) % base_count
        self._manual_slice_indices_by_context[ctx] = self._manual_slice_index
        fade = self._current_slice_fade_seconds()
        # 过渡起点 = 当前屏幕上正显示的帧(可能是上一次过渡的插值结果)。
        prev_frame = (dict(self._slice_fade_last_written)
                      if (self._slice_fade_active and self._slice_fade_last_written)
                      else dict(getattr(self, "_manual_slice_frame_cache", {}) or {}))
        self._stop_slice_fade()
        manual_t = 0.0
        was_playing = bool(self._is_playing)
        try:
            self._manual_slice_t_override = manual_t
            self._manual_slice_force_motion = True
            self._is_playing = True
            self._tick_play()
        finally:
            self._manual_slice_t_override = None
            self._manual_slice_force_motion = False
            self._manual_slice_latched = True
            self._is_playing = was_playing
        # _tick_play 已把新帧写进 _manual_slice_frame_cache；若设了过渡时间则做插值。
        new_frame = dict(getattr(self, "_manual_slice_frame_cache", {}) or {})
        if fade > 1e-3 and prev_frame and new_frame:
            self._start_slice_fade(prev_frame, new_frame, fade)
        slice_total = max(1, -(-base_count // stride))  # ceil(base/stride)
        slice_pos = (self._manual_slice_index // stride) % slice_total + 1
        if hasattr(self, "_status_lbl"):
            extra = f"（粒度{stride}台）" if stride > 1 else ""
            self._status_lbl.setText(
                f"切片 {slice_pos}/{slice_total}{extra}：{self._selected_id}")

    # ── 切片自动过渡(逐字节缓冲插值) ──────────────────────────────────────────
    def _start_slice_fade(self, from_frame: Dict[int, tuple],
                          to_frame: Dict[int, tuple], duration: float):
        import time
        self._slice_fade_from = {int(u): v for u, v in (from_frame or {}).items()}
        self._slice_fade_to = {int(u): v for u, v in (to_frame or {}).items()}
        self._slice_fade_duration = max(1e-3, float(duration))
        self._slice_fade_elapsed = 0.0
        self._slice_fade_last_real = time.monotonic()
        self._slice_fade_active = True
        if self._slice_fade_timer is None:
            self._slice_fade_timer = QTimer(self)
            self._slice_fade_timer.setInterval(25)  # 40fps
            self._slice_fade_timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._slice_fade_timer.timeout.connect(self._slice_fade_tick)
        self._slice_fade_timer.start()
        self._slice_fade_tick()  # 立即写一帧(alpha≈0)，避免首帧瞬跳

    def _stop_slice_fade(self):
        self._slice_fade_active = False
        if self._slice_fade_timer is not None:
            self._slice_fade_timer.stop()
        self._slice_fade_from = {}
        self._slice_fade_to = {}
        self._slice_fade_elapsed = 0.0

    def _slice_fade_tick(self):
        import time
        import numpy as np
        if not self._slice_fade_active or self._nm is None:
            self._stop_slice_fade()
            return
        now = time.monotonic()
        dt = max(0.0, now - float(self._slice_fade_last_real or now))
        self._slice_fade_last_real = now
        self._slice_fade_elapsed += dt
        raw = min(1.0, self._slice_fade_elapsed / max(1e-3, self._slice_fade_duration))
        alpha = raw * raw * (3.0 - 2.0 * raw)  # smoothstep 柔和过渡
        unis = set(self._slice_fade_from) | set(self._slice_fade_to)
        written: Dict[int, tuple] = {}
        for uni in unis:
            f = self._slice_fade_from.get(uni)
            tg = self._slice_fade_to.get(uni)
            ref = tg if tg is not None else f
            if ref is None:
                continue
            ref_buf, ref_act = ref
            length = len(ref_buf)
            f_buf = (f[0] if f is not None
                     else np.zeros(length, dtype=np.uint8))
            t_buf = (tg[0] if tg is not None
                     else np.zeros(length, dtype=np.uint8))
            f_arr = np.asarray(f_buf, dtype=np.float32)
            t_arr = np.asarray(t_buf, dtype=np.float32)
            if f_arr.shape != t_arr.shape:
                blended = t_arr
            else:
                # 逐字节线性插值：对 16bit 粗/精字节数学等价(误差<1LSB)。
                blended = f_arr * (1.0 - alpha) + t_arr * alpha
            buf = np.clip(np.round(blended), 0, 255).astype(np.uint8)
            f_act = (f[1] if f is not None
                     else np.zeros(length, dtype=bool))
            t_act = (tg[1] if tg is not None
                     else np.zeros(length, dtype=bool))
            act = np.asarray(f_act, dtype=bool) | np.asarray(t_act, dtype=bool)
            written[uni] = (buf, act)
            try:
                if hasattr(self._nm, "update_layer"):
                    self._nm.update_layer("effect", uni, buf, active=act, touch_ltp=False)
                else:
                    self._stop_slice_fade()
                    return
            except TypeError:
                self._nm.update_layer("effect", uni, buf)
            except Exception:
                logger.debug("effect_browser: failed to update_layer in slice fade write")
                self._stop_slice_fade()
                return
        self._slice_fade_last_written = written
        if raw >= 1.0:
            # 过渡结束：锁定 to 帧为新的保持缓存，停定时器。
            self._manual_slice_frame_cache = {
                int(u): (np.asarray(v[0]).copy(), np.asarray(v[1]).copy())
                for u, v in self._slice_fade_to.items()
            }
            ctx = self._sync_manual_slice_context()
            self._manual_slice_frame_cache_by_context[ctx] = dict(self._manual_slice_frame_cache)
            self._stop_slice_fade()

    def _write_cached_manual_slice_frame(self) -> bool:
        self._sync_manual_slice_context()
        if not self._manual_slice_frame_cache or self._nm is None:
            return False
        for uni, (buf, act) in list(self._manual_slice_frame_cache.items()):
            try:
                if hasattr(self._nm, "update_layer"):
                    self._nm.update_layer("effect", uni, buf, active=act, touch_ltp=False)
                else:
                    return False
            except TypeError:
                self._nm.update_layer("effect", uni, buf)
            except Exception:
                logger.debug("effect_browser: failed to write cached manual slice frame")
                return False
        return True

    def _split_speed_overrides(self, values: Dict[str, Any]) -> Dict[str, Any]:
        return dict(values or {})

    def set_param_overrides(self, overrides: Dict[str, Any]):
        """外部接口：设置参数覆盖字典（摇杆绑定/音频调制等）。
        在 UI 手动模式下，这些值会在每帧合并到当前参数中。
        传入空字典或 None 可清除覆盖。
        """
        self._param_overrides = self._split_speed_overrides(overrides) if overrides else {}

    def update_param_overrides(self, partial: Dict[str, Any]):
        """外部接口：增量更新参数覆盖（不清除已有的其他键）。"""
        self._param_overrides.update(self._split_speed_overrides(partial))

    def clear_param_overrides(self):
        """外部接口：清除所有参数覆盖。"""
        self._param_overrides.clear()

    def get_param_overrides(self) -> Dict[str, Any]:
        """外部接口：获取当前参数覆盖字典。"""
        return dict(self._param_overrides)

    # ── 外部驱动入口（AudioBridge / EffectBrowser.Apply）─────────────────────

    def _resolve_output_group_ids(self, params: Optional[dict]) -> List[str]:
        if isinstance(params, dict) and "_output_fixture_ids" in params:
            return _normalize_fixture_ids(params["_output_fixture_ids"])
        ids, _base = self._effective_output_scope_ids()
        return ids

    def _resolve_output_base_order_ids(self, params: Optional[dict], group_ids: Optional[list]) -> List[str]:
        base = []
        if isinstance(params, dict):
            base = _normalize_fixture_sequence_ids(params.get("_output_base_order_ids"))
        if not base:
            _ids, base = self._effective_output_scope_ids()
        group_norm = _normalize_fixture_ids(group_ids)
        if not group_norm:
            return base
        allowed = set(group_norm)
        ordered = [fid for fid in base if fid in allowed]
        present = set(ordered)
        for fid in group_norm:
            if fid not in present:
                ordered.append(fid)
                present.add(fid)
        return ordered

    def play_preset_external(self, preset_id: str, params: dict,
                              ch_types: list, sort_mode: str):
        """外部驱动入口：以给定参数立即开始连续播放该预设。

        与用户在 UI 选预设 + 点 Play 等价，但参数完全来自调用方而非 UI 控件。
        多次调用会切换预设但保持播放连续（不重启计时基准），以匹配 AudioBridge
        按段切换 preset 的语义；首次调用启动 _play_timer。

        ★ 这是 Bug 2（Audio Bridge → Effect 链路断链）的修复入口。
        旧实现走 row._apply_preset（不存在的方法），AttributeError 被静默吞掉。
        新路径直接复用 EffectBrowser 既有的 25ms 定时器和 NM 'effect' 层写入逻辑。
        """
        if not preset_id:
            self.stop_external()
            return
        track_type = "external:" + self._manual_effect_track_type(preset_id, ch_types)
        self.play_preset_for_type(track_type, preset_id, params, ch_types, sort_mode)
        if hasattr(self, "_status_lbl"):
            try:
                self._status_lbl.setText(f"外部驱动中：{preset_id}")
            except Exception:
                logger.debug("effect_browser: failed to set status label for external drive")
                pass

    # ── v24: 多效果类型并行外部驱动 ─────────────────────────────────────

    def play_preset_for_type(self, track_type: str, preset_id: str,
                              params: dict, ch_types: list, sort_mode: str):
        """按效果类型设置外部驱动状态（多类型可并行）。
        每种 track_type 独立维护一组参数，_tick_play 会遍历所有活跃的类型。
        """
        group_ids = self._resolve_output_group_ids(params)
        base_order_ids = self._resolve_output_base_order_ids(params, group_ids)
        group_key = _target_scope.bound_group_key(self, params, group_ids, _group_key_from_ids)
        if not preset_id:
            self.stop_external_type(track_type, group_ids=group_ids)
            return
        state_params = _target_scope.capture_runtime_params(self, params, group_ids)
        state_params.pop("_output_fixture_ids", None)
        state_params.pop("_output_base_order_ids", None)
        state_speed, recalled = _dispatch_state.playback_options(state_params)
        old_state = self._external_states.get((track_type, group_key), {})
        try:
            speed_factor = max(0.0, min(16.0, float(
                state_speed if state_speed is not None else old_state.get("speed_factor", 1.0))))
        except Exception:
            logger.debug("effect_browser: failed to parse state speed factor, using 1.0")
            speed_factor = 1.0
        self._external_states[(track_type, group_key)] = {
            "preset_id": preset_id,
            "params": state_params,
            "ch_types": list(ch_types or []),
            "sort_mode": sort_mode or "index",
            "group_ids": list(group_ids),
            **_reference_order.play_fields(params, base_order_ids or group_ids, _normalize_fixture_sequence_ids),
            "speed_factor": speed_factor,
            "virtual_t": float(old_state.get("virtual_t", 0.0) or 0.0),
            "last_real_t": float(old_state.get("last_real_t", 0.0) or 0.0),
            "_ltp_takeover_pending": True, "recalled": recalled,
        }
        _dispatch_state.remember_applied_preset(self, preset_id, recalled)
        # 同时更新旧的单一状态（兼容）
        self._external_state = None
        if hasattr(self, "_status_lbl"):
            try:
                active_types = [f"{tt}@{gk}" for (tt, gk) in self._external_states.keys()]
                if self._is_topview_live_selection():
                    self._status_lbl.setText(f"TopView 并行效果：{len(active_types)} 个")
                else:
                    self._status_lbl.setText("active: " + ", ".join(active_types))
            except Exception:
                logger.debug("effect_browser: failed to update status label in play_preset_for_type")
                pass
        self._update_transport_state()
        _dispatch_state.refresh_applied_lists(self, recalled)
        if not self._is_playing:
            import time as _t
            self._is_playing = True
            self._play_start_time = _t.monotonic()
            # 重置虚拟时间（多类型外部驱动首次启动）
            self._virtual_t = 0.0
            self._last_real_t = 0.0
            self._play_timer.start()
            self._update_transport_state()
        else:
            import time as _t
            now = _t.monotonic()
            for state in getattr(self, "_external_states", {}).values():
                if isinstance(state, dict) and float(state.get("last_real_t", 0.0) or 0.0) <= 0.0:
                    state["last_real_t"] = now

    def _manual_effect_track_type(self, preset_id_or_ch_types, ch_types: Optional[list] = None) -> str:
        if ch_types is None:
            preset_id = self._selected_id or "effect"
            channels_in = preset_id_or_ch_types
        else:
            preset_id = str(preset_id_or_ch_types or self._selected_id or "effect")
            channels_in = ch_types
        channels = "+".join(sorted(str(ch) for ch in (channels_in or []) if str(ch)))
        if not channels:
            channels = "all"
        safe_preset = str(preset_id or "effect").replace(":", "_").replace("@", "_")
        return f"manual:{safe_preset}:{channels}"

    def stop_external_type(self, track_type: str, group_ids: Optional[list] = None):
        """停止指定效果类型的外部驱动。"""
        if group_ids is None:
            keys = [k for k in list(self._external_states.keys()) if k[0] == track_type]
        else:
            keys = [(track_type, _group_key_from_ids(group_ids))]
        for key in keys:
            st = _audio_runtime.pop_stopped_audio(self, key)
            if isinstance(st, dict):
                stopped = {str(ch) for ch in (st.get("ch_types", []) or []) if str(ch)}
                for form in list(self._channel_forms or []):
                    if self._channel_form_type(form) in stopped:
                        form["playing"] = False
                        form["virtual_t"] = 0.0
                        form["last_real_t"] = 0.0
        if hasattr(self, "_status_lbl"):
            try:
                if self._external_states:
                    active_types = [f"{tt}@{gk}" for (tt, gk) in self._external_states.keys()]
                    self._status_lbl.setText("active: " + ", ".join(active_types))
                else:
                    self._status_lbl.setText("已停止")
            except Exception:
                logger.debug("effect_browser: failed to update status label in _update_transport_state")
                pass
        self._refresh_quick_effect_lists()
        if not self._external_states:
            # 所有类型都停了，完全停止
            self.stop_external()
        else:
            # 还有其他类型在运行，更新单一状态为第一个活跃的
            self._external_state = None
            self._update_transport_state()

    def stop_external(self):
        """外部驱动停止入口（如 AudioBridge 禁用 / 段落间隙）。

        清掉外部状态并停止 _play_timer（_on_stop 内部会清 NM 'effect' 层）。
        """
        if self._external_state is None and not self._external_states and not self._is_playing:
            return
        self._external_state = None
        self._external_states.clear()
        self._refresh_quick_effect_lists()
        for form in list(self._channel_forms or []):
            form["playing"] = False
            form["virtual_t"] = 0.0
            form["last_real_t"] = 0.0
        if self._slice_key is None:
            self._manual_slice_latched = False
            self._clear_current_manual_slice_cache()
        else:
            self._manual_slice_latched = True
        if self._is_playing:
            self._on_stop()
        else:
            # 不在播放但仍可能有 effect 层残留
            if self._nm is not None and self._slice_key is None:
                try:
                    self._nm.clear_layer("effect")
                except Exception:
                    logger.debug("effect_browser: clear effect layer failed")
            self._update_transport_state()
        self._stop_slice_fade()   # ★ 切片淡化自己的定时器是 latch 下唯一 writer

    def _tick_play(self):
        """定时器回调：按当前时间计算单帧并直接写入 DMX。
        ★ v24: 支持多效果类型并行。遍历 _external_states 中所有活跃类型，
          分别计算帧数据并叠加写入同一 DMX 缓冲区。
          如果 _external_states 为空但 _external_state 非 None，则走旧的单一模式。
          如果两者都为空，走 UI 手动模式。
        """
        import time
        if not self._is_playing:
            self._play_timer.stop()
            return
        if self._nm is None or self._project is None:
            return
        _prof_t0 = time.perf_counter() if _EFFECT_TICK_PROF else 0.0
        try:
            from .effect_engine import get_engine as _get_ef
            eng = _get_ef()
            # 全局速度倍率：用虚拟时间累计，不重置相位，平滑加速/减速
            _now_real = time.monotonic()
            manual_t = getattr(self, "_manual_slice_t_override", None)
            if manual_t is not None:
                t = float(manual_t)
                self._last_real_t = _now_real
            elif self._last_real_t > 0:
                _dt = _now_real - self._last_real_t
                self._virtual_t += _dt * self._global_speed_factor
                self._last_real_t = _now_real
                t = self._virtual_t
            else:
                self._virtual_t = 0.0
                self._last_real_t = _now_real
                t = self._virtual_t
            fps = float(self._fps_spin.value())
            project = self._project_object()
            # ★ 冻结根因：全设备列表必须同版本同对象（_stable_all_fixtures）。
            #   旧实现每帧 list(...) → 所有 id(fixtures) 键缓存每帧全 miss。
            raw_fx = getattr(project, "fixtures", None) or []
            all_fx = _stable_all_fixtures(
                _ensure_cache_dict(self, "_ef_all_fx_cache"), raw_fx)
            if not all_fx:
                return
            # by_id 映射播放期不变 → 缓存(失效于 layout_changed/set_context)
            by_id = self._ef_by_id_cache
            if by_id is None:
                by_id = {fi.id: fi for fi in all_fx}
                self._ef_by_id_cache = by_id
            # 从 TopView 读取位置
            def _fixtures_from_ids(ids: Optional[list], scope_params=None) -> list:
                ids_norm = _normalize_fixture_ids(ids)
                if not ids_norm:
                    # 空作用域 = 全项目设备：all_fx 已是稳定对象（同一设备表
                    # 版本返回同一个 list）→ 下游 id(fixtures) 键缓存可命中。
                    # 旧实现 `return list(all_fx)` 每帧新对象 = 冻结根因。
                    return all_fx if _target_scope.empty_targets_are_all(self, scope_params) else []
                # ★ 性能+正确性：按内容指纹缓存并持有引用——既省每帧 1992 项
                #   列表构建，又保证 compute_at 排序链的 id(fixtures) 键稳定
                #   （缓存持有旧对象 → allocator 不会复用其 id，切组换设备集时
                #   键必变，杜绝"旧排序链误用于新设备集"）。见模块级
                #   _fixtures_for_ids_stable（淘汰只逐出最旧一条）。
                return _fixtures_for_ids_stable(
                    ids_norm, by_id,
                    _ensure_cache_dict(self, "_ef_fx_list_cache"))

            def _build_positions(fixtures: list) -> dict:
                # ★ 性能+正确性：pos 只依赖 (fixtures 对象, top_map)，播放期稳定。
                #   fixtures 来自 _fixtures_from_ids 缓存（对象稳定）→ 按对象 id
                #   缓存并持有列表引用（compute_at 排序链的 id(positions) 键稳定；
                #   缓存驱逐后旧列表 id 复用时不误命中，审查A）。
                pos_cache = getattr(self, "_ef_pos_cache", None)
                if pos_cache is not None:
                    cached = pos_cache.get(id(fixtures))
                    if cached is not None and cached[0] is fixtures:
                        return cached[1]
                pos = {}
                if self._topview is not None:
                    # 全设备坐标表(get_all_fixture_positions 遍历所有 Sheet×全部设备)
                    # 是本帧最贵的一步、且播放期不变 → 缓存,失效于 TopView layout_changed。
                    top_map = self._ef_topmap_cache
                    if top_map is None:
                        top_map = {}
                        rows = (self._topview.get_all_fixture_positions()
                                if hasattr(self._topview, "get_all_fixture_positions")
                                else self._topview.get_fixture_positions())
                        for entry in rows or []:
                            fid = str(entry.get("id") or entry.get("fixture_id") or "")
                            if not fid:
                                continue
                            top_map[fid] = {
                                "pos_x": float(entry.get("pos_x", entry.get("x", 0.0)) or 0.0),
                                "pos_y": float(entry.get("pos_y", entry.get("y", 0.0)) or 0.0),
                            }
                        self._ef_topmap_cache = top_map
                    for fi in fixtures:
                        pos[fi.id] = top_map.get(str(fi.id), {
                            "pos_x": getattr(fi, "pos_x", 0.0),
                            "pos_y": getattr(fi, "pos_y", 0.0),
                        })
                else:
                    for fi in fixtures:
                        pos[fi.id] = {
                            "pos_x": getattr(fi, "pos_x", 0.0),
                            "pos_y": getattr(fi, "pos_y", 0.0),
                        }
                # 值持有 fixtures 引用：驱逐后旧列表 id 复用时命中校验挡掉；
                # 淘汰只逐出最旧一条，绝不整表清空（清空会击穿其它效果）。
                _cache_put_bounded(
                    _ensure_cache_dict(self, "_ef_pos_cache"),
                    id(fixtures), (fixtures, pos), _POS_CACHE_MAX)
                return pos
            # 构建 fixture_defs（播放期不变 → 缓存）
            defs_by_id = self._ef_defs_cache
            if defs_by_id is None:
                defs_by_id = _fixture_defs_by_id(project)
                self._ef_defs_cache = defs_by_id

            # ★ 冻结根因之二：_build_fdefs 旧实现**零缓存**，每帧每效果
            #   4889 次 getattr + dict 重建。改为与 _build_positions 同风格：
            #   键 id(fixtures) + `is` 校验（身份稳定由上面两处保证）。
            fdefs_by_fx = _ensure_cache_dict(self, "_ef_fdefs_cache")

            def _build_fdefs(fixtures: list) -> dict:
                # 键含 defs 容器身份（同 _effect_addr_maps_for_fixtures 的
                # raw_defs）：换工程/重载定义时 defs_by_id 是新对象 → 必 miss，
                # 不会拿旧定义表的结果当命中。
                key = (id(fixtures), id(defs_by_id))
                cached = fdefs_by_fx.get(key)
                if (cached is not None and cached[0] is fixtures
                        and cached[1] is defs_by_id):
                    return cached[2]
                out = {}
                for fi in fixtures:
                    def_id = getattr(fi, 'def_id', None)
                    fd = defs_by_id.get(def_id)
                    if fd:
                        out[fi.id] = fd
                _cache_put_bounded(fdefs_by_fx, key,
                                   (fixtures, defs_by_id, out), _FDEFS_CACHE_MAX)
                return out

            import numpy as np

            def _write_to_bufs(bufs: dict, acts: dict, fx_list: list, fv_list: list,
                               allowed_ch: list) -> int:
                cnt = 0
                compute_cache: dict = {}
                ch_types: set = set()
                for fv in fv_list:
                    ch_types.update(fv.keys())
                for ch_type in ch_types:
                    values = [fv.get(ch_type, 0) for fv in fv_list]
                    cnt += self._write_channel_vectorized(
                        bufs, acts, fx_list, values, ch_type, allowed_ch, compute_cache)
                return cnt

            uni_bufs: dict = {}
            uni_acts: dict = {}
            effect_touch_ltp = False

            # ★ v24: 多效果类型并行模式
            if self._external_states:
                active_keys = []
                for (_tt, _gk), ext_state in list(self._external_states.items()):
                    ext_id = ext_state.get("preset_id", "")
                    if not ext_id:
                        continue
                    ext_params = dict(ext_state.get("params", {}) or {})
                    # ★ R183 批 A：召回过渡（N 秒参数插值；无过渡时原样返回原对象）
                    ext_params = _recallfade.recall_fade_params(ext_state, ext_params, _now_real)
                    ext_ch_types = ext_state.get("ch_types", []) or []
                    ext_sort = ext_state.get("sort_mode", "index")
                    ext_group_ids = ext_state.get("group_ids", []) or []
                    ext_base_order_ids = ext_state.get("base_order_ids", []) or ext_group_ids
                    fx_local = _fixtures_from_ids(ext_group_ids, ext_params)
                    if not fx_local:
                        continue
                    active_keys.append((_tt, _gk))
                    if bool(ext_state.pop("_ltp_takeover_pending", False)):
                        effect_touch_ltp = True
                    speed_factor = _recallfade.recall_fade_scalar(
                        ext_state, "speed_factor",
                        float(ext_state.get("speed_factor", 1.0) or 0.0), _now_real)
                    local_t = _slice_control.advance_state_clock(
                        self, ext_state, _now_real, speed_factor, (_tt, _gk))
                    pos_local = _build_positions(fx_local)
                    fdefs_local = _build_fdefs(fx_local)
                    # ★ 幅度控制模式：在外部驱动模式下也合并 param_overrides
                    if self._param_overrides:
                        ext_params.update(self._param_overrides)
                    for frame, allowed_types, fx_used in _slice_control.state_channel_frames(
                            self, eng, ext_id, local_t, ext_params,
                            fixtures=fx_local, definitions=fdefs_local, positions=pos_local,
                            fps=fps, sort_mode=ext_sort, base_order=ext_base_order_ids or ext_group_ids,
                            channel_types=ext_ch_types, owner=ext_state, owner_key=(_tt, _gk)):
                        written = _write_to_bufs(
                            uni_bufs, uni_acts, fx_used, frame.values, allowed_types)
                current_unis = set(uni_bufs.keys())
                previous_unis = set()
                try:
                    previous_unis = {
                        int(uni) for uni in self._nm._layers.get("effect", {}).keys()
                    }
                except Exception:
                    logger.debug("effect_browser: failed to get previous effect layer unis")
                    previous_unis = set()
                for uni in (previous_unis | current_unis):
                    if uni in uni_bufs:
                        continue
                    uni_bufs[uni] = np.zeros(512, dtype=np.uint8)
                    uni_acts[uni] = np.zeros(512, dtype=bool)
                if not active_keys and not _target_scope.has_empty_editor(self):
                    self._external_states.clear()
                    self._is_playing = False
                    self._play_timer.stop()
                    self._update_transport_state()
                    self.report_to_bus(force=True)   # 停止 → 清掉本面板窗格
                    return
            elif self._external_state is not None:
                # 旧的单一外部驱动兼容模式
                ext_id, ext_params, ext_ch_types, ext_sort = self._external_state
                effect_touch_ltp = False
                group_ids, base_ids = self._effective_output_scope_ids()
                fixtures = _fixtures_from_ids(group_ids)
                if not fixtures:
                    return
                positions = _build_positions(fixtures)
                fdefs = _build_fdefs(fixtures)
                # ★ 幅度控制模式：在外部驱动模式下也合并 param_overrides
                if self._param_overrides:
                    ext_params = dict(ext_params)
                    ext_params.update(self._param_overrides)
                for frame, allowed_types, fx_used in self._compute_channel_frames(
                        eng, ext_id, t, ext_params,
                        fixtures, fdefs, positions,
                        fps, ext_sort,
                        base_ids or self._cached_fixture_ids(fixtures),
                        ext_ch_types, runtime_owner=self._external_state):
                    written = _write_to_bufs(
                        uni_bufs, uni_acts, fx_used, frame.values, allowed_types)
            else:
                # UI 手动模式
                if (getattr(self, "_manual_slice_latched", False) and
                        getattr(self, "_manual_slice_t_override", None) is None):
                    if self._write_cached_manual_slice_frame():
                        return
                preset_id = self._selected_id
                params = self._get_current_params()
                # ★ 合并外部参数覆盖（摇杆绑定/音频调制等）
                if self._param_overrides:
                    params.update(self._param_overrides)
                if getattr(self, "_manual_slice_force_motion", False):
                    params = self._prepare_manual_slice_params(params)
                ch_types = self._get_selected_ch_types()
                sort_mode = self._get_sort_mode()
                group_ids, base_ids = self._effective_output_scope_ids()
                fixtures = _fixtures_from_ids(group_ids)
                if not fixtures:
                    return
                positions = _build_positions(fixtures)
                fdefs = _build_fdefs(fixtures)
                base_order_ids = base_ids or self._cached_fixture_ids(fixtures)
                if self._has_channel_forms():
                    now_local = time.monotonic()
                    played = False
                    if getattr(self, "_manual_slice_force_motion", False):
                        active_form = self._active_channel_form()
                        forms_to_render = [active_form] if active_form is not None else []
                    else:
                        forms_to_render = [
                            f for f in list(self._channel_forms or [])
                            if bool(f.get("playing", False))
                        ]
                    for form in forms_to_render:
                        if form is None:
                            continue
                        played = True
                        if bool(form.pop("_ltp_takeover_pending", False)):
                            effect_touch_ltp = True
                        if getattr(self, "_manual_slice_force_motion", False):
                            local_t = float(getattr(self, "_manual_slice_t_override", 0.0) or 0.0)
                            form["last_real_t"] = now_local
                        else:
                            last_real = float(form.get("last_real_t", 0.0) or 0.0)
                            if last_real <= 0.0:
                                last_real = now_local
                            dt_local = max(0.0, now_local - last_real)
                            speed_local = float(form.get("speed_factor", 1.0) or 0.0)
                            local_t = float(form.get("virtual_t", 0.0) or 0.0) + dt_local * speed_local
                            form["virtual_t"] = local_t
                            form["last_real_t"] = now_local
                        ch = self._channel_form_type(form)
                        if not ch:
                            continue
                        form_params = dict(params)
                        form_params.pop("_channel_forms", None)
                        form_params[self._channel_param_key] = ch
                        seen_form_widgets = set()
                        for key, widget in (form.get("params", {}) or {}).items():
                            if isinstance(widget, RangeParamWidget):
                                if id(widget) in seen_form_widgets:
                                    continue
                                seen_form_widgets.add(id(widget))
                                form_params.update(widget.get_values())
                            else:
                                form_params[key] = widget.get_value()
                        if self._param_overrides:
                            form_params.update(self._param_overrides)
                        if getattr(self, "_manual_slice_force_motion", False):
                            form_params = self._prepare_manual_slice_params(form_params)
                        for frame, allowed_types, fx_used in self._compute_channel_frames(
                                eng, preset_id, local_t, form_params,
                                fixtures, fdefs, positions,
                                fps, sort_mode, base_order_ids, [ch], runtime_owner=form):
                            if getattr(self, "_manual_slice_force_motion", False):
                                # ★ R141：组域必须与 `frame.values` 的下标域同口径
                                _fx_r, _pos_r = _slice_scoped_inputs(self, fixtures, positions, [ch])
                                frame.values = self._rotate_manual_slice_values(
                                    frame.values, _fx_r, _pos_r, sort_mode, base_order_ids, form_params)
                            written = _write_to_bufs(
                                uni_bufs, uni_acts, fx_used, frame.values, allowed_types)
                    if not played:
                        self._is_playing = False
                        self._play_timer.stop()
                        self._sync_transport_controls_to_active_form()
                        return
                else:
                    for frame, allowed_types, fx_used in self._compute_channel_frames(
                            eng, preset_id, t, params,
                            fixtures, fdefs, positions,
                            fps, sort_mode, base_order_ids, ch_types, runtime_owner=self):
                        if getattr(self, "_manual_slice_force_motion", False):
                            _fx_r, _pos_r = _slice_scoped_inputs(self, fixtures, positions, ch_types)
                            frame.values = self._rotate_manual_slice_values(
                                frame.values, _fx_r, _pos_r, sort_mode, base_order_ids, params)
                        written = _write_to_bufs(
                            uni_bufs, uni_acts, fx_used, frame.values, allowed_types)

            # 写入 NM
            if (getattr(self, "_manual_slice_force_motion", False) and
                    getattr(self, "_manual_slice_t_override", None) is not None):
                ctx = self._sync_manual_slice_context()
                self._manual_slice_frame_cache = {
                    int(uni): (buf.copy(), uni_acts.get(uni).copy())
                    for uni, buf in uni_bufs.items()
                    if uni_acts.get(uni) is not None
                }
                self._manual_slice_frame_cache_by_context[ctx] = dict(self._manual_slice_frame_cache)
            for uni, buf in uni_bufs.items():
                act = uni_acts.get(uni)
                try:
                    if hasattr(self._nm, "update_layer"):
                        self._nm.update_layer(
                            "effect", uni, buf, active=act,
                            touch_ltp=bool(effect_touch_ltp))
                except TypeError:
                    self._nm.update_layer("effect", uni, buf)
            # 调度台上报（节流在 report_to_bus 内部；失败不影响出光）
            self.report_to_bus()
        except Exception as e:
            # ★ R38-D8：整帧兜底不能静默也不能刷屏。前 3 次打完整 traceback
            #   （R33 的 NameError 就是被每帧 warning 淹没才查了一整轮），
            #   之后每 200 次汇报一次并带累计计数，便于判断是持续失败还是偶发。
            self._tick_play_err_count = getattr(self, "_tick_play_err_count", 0) + 1
            n = self._tick_play_err_count
            if n <= _TICK_ERR_VERBOSE_LIMIT:
                logger.exception(
                    "EffectBrowserWidget._tick_play error (#%d): %s", n, e)
            elif n % _TICK_ERR_SAMPLE_EVERY == 0:
                logger.warning(
                    "EffectBrowserWidget._tick_play still failing (#%d): %s", n, e)
        finally:
            if _EFFECT_TICK_PROF:
                self._record_tick_prof(time.perf_counter() - _prof_t0)

    def _record_tick_prof(self, elapsed: float) -> None:
        """★ R38-D8：累积 _tick_play 帧耗时，每 40 帧打印一次中位/p90/max。

        仅在 QASCADE_EFFECT_TICK_PROF=1 时被调用。用来判断"打开 Programmer 后
        变卡"是否真的落在 effect tick 上，以及修复前后的量化对比。
        """
        samples = getattr(self, "_tick_prof_samples", None)
        if samples is None:
            samples = []
            self._tick_prof_samples = samples
        samples.append(elapsed * 1000.0)
        if len(samples) < 40:
            return
        ordered = sorted(samples)
        mid = ordered[len(ordered) // 2]
        p90 = ordered[int(len(ordered) * 0.9)]
        logger.info(
            "effect tick_play prof: n=%d median=%.2fms p90=%.2fms max=%.2fms",
            len(ordered), mid, p90, ordered[-1])
        samples.clear()

    def _resolve_ch_offsets(self, fi, ch_type: str) -> List[int]:
        """
        根据设备实例和通道类型，查找其在 DMX 帧中的所有偏移量。
        只匹配设备定义中真实存在的同类通道；没有该类型时返回空，避免
        dimmer/valve/pump 等效果落到 motor 或 function 通道。

        ★ R141：结果只由「设备定义 + 通道类型」决定（与设备实例无关），
          故实际计算搬到模块级 `_def_channel_offsets` 并记忆化 —— 本方法只是取值入口。
        """
        project = self._project_object()
        raw_defs = getattr(project, "fixture_defs", None) if project is not None else None
        if raw_defs is None:
            return []
        return list(_def_channel_offsets(
            raw_defs, getattr(fi, "def_id", None), ch_type,
            int(getattr(project, "patch_rev", 0) or 0)))

    # ── 旧兼容信号（降级路径）────────────────────────────────────────────────

    def _on_apply(self):
        if not self._selected_id:
            return
        params = self._get_current_params()
        ch_types = self._get_selected_ch_types()
        sort_mode = self._get_sort_mode()
        if self._nm is not None and self._project is not None:
            params = self._params_with_current_output_scope(params)
            track_type = self._manual_effect_track_type(ch_types)
            self.play_preset_for_type(track_type, self._selected_id, params, ch_types, sort_mode)
            return
        self.effect_applied.emit(self._selected_id, params, ch_types, sort_mode)

    def _on_send_timeline(self):
        if not self._selected_id:
            return
        params = self._get_current_params()
        ch_types = self._get_selected_ch_types()
        sort_mode = self._get_sort_mode()
        duration = self._dur_spin.value()
        fps = float(self._fps_spin.value())
        self.send_to_timeline.emit(
            self._selected_id, params, ch_types, sort_mode, duration, fps)

    # ── 图案编辑器 ────────────────────────────────────────────────────────────

    def _open_pattern_editor(self):
        from .pattern_editor import PatternEditorDialog
        dlg = PatternEditorDialog(self)
        dlg.pattern_saved.connect(self._on_pattern_saved)
        dlg.exec()

    def _on_pattern_saved(self, pat):
        # 刷新所有 pattern 类型的参数控件
        widgets = list(self._param_widgets.values())
        for form in getattr(self, "_channel_forms", []) or []:
            widgets.extend((form.get("params", {}) or {}).values())
        for pw in widgets:
            if isinstance(getattr(pw, "_ctrl", None), QComboBox) and getattr(getattr(pw, "_spec", None), "type", "") == "pattern":
                eng = get_engine()
                pw._ctrl.clear()
                for pid, p in eng.all_patterns().items():
                    pw._ctrl.addItem(p.name, pid)

    # ── 外部接口：刷新图案列表 ────────────────────────────────────────────────

    def refresh_patterns(self):
        """当用户图案库变化时调用"""
        self._on_pattern_saved(None)

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：效果制作总线上报（唯一出口）
    #
    #  为什么在这里：_tick_play 的 _external_states 里每个 (track_type, group_key)
    #  就是调度台的**一个窗格**，且带完整 params/preset/设备集 —— 天然是上报点。
    #
    #  ★ 身份契约：一窗格 = (panel, track_type, group_key)。group_key 由设备 id
    #  内容寻址，所以"同效果不同设备""同设备不同效果槽"天然两格；**绝不按
    #  effect_id/分类合并**（用户：微差即两种效果）。
    # ══════════════════════════════════════════════════════════════════════════

    #: 上报节流（秒）：_tick_play 可能 60Hz，总线只需人眼刷新率级别
    _BUS_REPORT_INTERVAL = 0.25

    def _bus_summary(self, preset_id: str,
                     params: Optional[dict] = None,
                     state: Optional[dict] = None) -> str:
        """生成窗格副标题：只列**与预设默认值不同**的参数（差异即身份线索）。

        复用既有分类规则 `_PARAM_GROUP_RULES`（空间/时序/颜色/音频）给差异
        归组，不新增分类体系；默认值参数省略以保持窗格简洁。
        角度/速度/时差这类"微差"必须能显示出来（用户硬要求）——预设参数之外的
        运行期字段由 `_bus_runtime_diffs` 补充（速度倍率/时差/方向/切片）。
        """
        parts: List[str] = []
        try:
            from .effect_engine import get_engine as _get_ef
            preset = _get_ef().get_preset(str(preset_id or ""))
            if preset is not None:
                defaults = preset.default_params()
                live = dict(params or {})
                diffs: List[tuple] = []
                for spec in preset.params:
                    key = getattr(spec, "key", "")
                    if not key or key.startswith("_"):
                        continue
                    cur = live.get(key, defaults.get(key))
                    old = defaults.get(key)
                    # 数值用容差比较，其它按等值（容差避免 0.30000001 被当差异）
                    try:
                        same = abs(float(cur) - float(old)) <= 1e-6
                    except (TypeError, ValueError):
                        same = (cur == old)
                    if same:
                        continue
                    diffs.append((_param_group_of(spec),
                                  str(getattr(spec, "label", "") or key), cur))
                order = {g: i for i, g in enumerate(_PARAM_GROUP_ORDER)}
                diffs.sort(key=lambda d: (order.get(d[0], 99), d[1]))
                for group, label, val in diffs[:3]:
                    sval = f"{val:.2f}" if isinstance(val, float) else str(val)
                    parts.append(f"{group} {label}={sval}")
                if len(diffs) > 3:
                    parts.append(f"+{len(diffs) - 3}")
            # 运行期差异（速度/时差/方向/切片）——用户点名的"微差"主战场
            if state:
                parts.extend(self._bus_runtime_diffs(dict(state)))
        except Exception:
            pass
        # 最多 4 段，避免副标题过长挤掉窗格
        return " · ".join(parts[:4])

    def _bus_runtime_diffs(self, st: dict) -> List[str]:
        """运行期差异化字段（不是预设参数，但用户常用来区分两个格）。

        用户明确点名：**只差速度、只差时差（相位）、只差角度**都必须能区分。
        这些不在 preset.params 里，而是外部驱动状态字段 / 内部覆盖键：
          - speed_factor（速度倍率）→ 时序
          - virtual_t（虚拟时间 = 起始时差）→ 时序
          - _app_direction（应用方向 = 角度语义）→ 空间
          - slice 相关（切片键/步长）→ 空间
        仅在与"默认"不同时显示，保持窗格简洁。
        """
        out: List[str] = []
        try:
            spd = float(st.get("speed_factor", 1.0) or 1.0)
            if abs(spd - 1.0) > 1e-6:
                out.append(f"时序 速度={spd:.2f}×")
            vt = float(st.get("virtual_t", 0.0) or 0.0)
            if abs(vt) > 1e-6:
                out.append(f"时序 时差={vt:.2f}s")
            params = dict(st.get("params", {}) or {})
            dirc = str(params.get("_app_direction", "") or "")
            if dirc and dirc not in ("default", "ltr"):
                out.append(f"空间 方向={dirc}")
            stride = params.get("_slice_stride", None)
            try:
                if stride is not None and int(stride) > 1:
                    out.append(f"空间 切片步长={int(stride)}")
            except (TypeError, ValueError):
                pass
        except Exception:
            pass
        return out

    def _bus_nodes(self) -> list:
        """把当前 effect 状态转成调度台窗格节点（**一状态一节点，不去重**）。"""
        from ..processing.dispatch import EffectNode
        from .effect_engine import get_engine as _get_ef, normalize_effect_category
        nodes: List[EffectNode] = []
        try:
            eng = _get_ef()
        except Exception:
            eng = None
        states = getattr(self, "_external_states", {}) or {}
        for key, st in list(states.items()):
            if not isinstance(st, dict):
                continue
            track_type = str(key[0]) if len(key) >= 1 else ""
            group_key = str(key[1]) if len(key) >= 2 else ""
            preset_id = str(st.get("preset_id", "") or "")
            params = dict(st.get("params", {}) or {})
            group_ids = [str(x) for x in (st.get("group_ids", []) or []) if str(x)]
            base_ids = _reference_order.reference_ids(st, _node_str_ids)
            name = group_name = ""
            category = ""
            try:
                preset = eng.get_preset(preset_id) if eng is not None else None
                if preset is not None:
                    name = str(getattr(preset, "name", "") or "")
                    category = normalize_effect_category(getattr(preset, "category", ""))
            except Exception:
                pass
            # 设备组显示名：优先用户当前组名，否则按是否有 id 判断
            try:
                sel_key = str(self._current_live_selection_key() or "")
                if group_key == sel_key and getattr(self, "_selected_group_name", ""):
                    group_name = str(self._selected_group_name)
            except Exception:
                pass
            nodes.append(_effect_node(
                (track_type, group_key, group_name, group_ids, base_ids,
                 preset_id, name, category, self._bus_summary(preset_id, params, st)),
                st))
        return nodes

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前效果窗格到总线（节流；失败绝不影响出光）。

        force=True 用于状态变化点（选效果/改参数/停止），保证立即反映。
        """
        try:
            import time as _t
            now = _t.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ..processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 本面板的窗格集合可能在减少（某槽停了）→ 用逐键 upsert + 差集删除，
            # 不能整体 clear_panel：面板可能有多个 report 来源交错（保持幂等）。
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：状态捕获 / 还原（供 EffectCache 保存与调用）
    # ══════════════════════════════════════════════════════════════════════════
    def dispatch_capture_state(self) -> dict:
        """给调度台捕获"可完整还原"的面板状态。

        直接复用 `save_state()` —— 不新增第二套序列化，因此将来面板加参数，
        调度台缓存自动覆盖（这是"不复杂化"的关键取舍）。
        """
        try:
            return _dispatch_state.capture_state(self, self.save_state())
        except Exception:
            return {}

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None,
                              *, for_edit: bool = False) -> bool:
        """按调度台节点还原**该节点对应的那一格**（不打断其它格）。

        node     : EffectNode.to_dict()，含 track_type/group_key/fixture_ids
        state    : 该面板保存时的 save_state() 快照
        for_edit : ★★ 2026-09-18 用户定案（问题9）——
                   · **False（调用/播放）**：只把该格的参数交给引擎播放 + 恢复作用域，
                     **完全不碰页面控件**（不 `load_state`、不刷预设/参数/速度控件、
                     不切设备组选中）。用户左键点场景卡走这条。
                   · **True（编辑/调入监控区）**：只还原页面控件与该格参数，
                     不启动该格播放，保留其它已运行 owner 的身份与时钟。
                   旧调用方不传该参数 → False，即**默认纯播放**（安全默认：
                   不误改用户正在编辑的页面）。
        """
        try:
            node = dict(node or {})
            track_type = str(node.get("track_type", "") or "")
            group_key = str(node.get("group_key", "") or "")
            fixture_ids = [str(x) for x in (node.get("fixture_ids", []) or []) if str(x)]
            effect_id = str(node.get("effect_id", "") or "")
            if not effect_id:
                return False
            # 0) 页面级 UI（包络/自定义效果/分组 UI 缓存）：**仅编辑语义**才还原。
            #    ★ 问题9 根因：此步无条件下执行 → 一点播放就把整页控件重建，
            #    用户正在编辑的页面被"调用"污染。且 `load_state` 内的
            #    `_load_preset_params` 会重建全部参数控件 —— 是卡顿最大一块。
            if for_edit:
                return _dispatch_state.restore_editor(self, node, state,
                    (_restore_recall_scope, _archived_cell_state,
                     _sync_ui_to_recalled_cell, _rebind_slice_key_after_restore))
            # 1) 设备作用域（播放与编辑都要）：编辑=切组+刷控件，播放=只设作用域
            _restore_recall_scope(self, fixture_ids, node, for_edit)
            # 2) 从 state 里取该格当时的参数（按 track_type/group_key 匹配）
            params, ch_types, sort_mode, speed_factor, virtual_t, base_order = \
                _archived_cell_state(state, track_type, group_key)
            # 2.2) 只恢复真实master及其运行反馈，不触碰当前通道编辑控件。
            try:
                gspd = _num_or((state or {}).get("global_speed"), 1.0)
                _master_speed.set_factor(self, gspd)
            except Exception:
                logger.debug("effect_browser: dispatch recall global speed failed",
                             exc_info=True)
            # 2.5) 控件逐项对应：**仅编辑语义**（纯播放不动控件，见问题9 定案）
            if for_edit:
                _sync_ui_to_recalled_cell(self, effect_id, params, fixture_ids)
            # 3) 写回该格的 _external_states（存在则更新，不存在则新建该格）
            params = dict(params)
            params["_output_fixture_ids"] = list(fixture_ids)
            # ★ 基准序：存档带就用存档的（越界项由既有真源过滤/补齐），
            #   旧档（无该键）保持原行为 = 用节点设备序。
            if base_order:
                params["_output_base_order_ids"] = list(base_order)
            else:
                params["_output_base_order_ids"] = list(fixture_ids)
            try:
                base_order = list(self._resolve_output_base_order_ids(params, fixture_ids))
            except Exception:
                logger.debug("effect_browser: base order resolve failed", exc_info=True)
                base_order = list(fixture_ids)
            key = (track_type, group_key)
            # ★ R183 批 A：召回过渡起点必须在**覆盖该格之前**抓取 —— 覆盖之后
            #   `_external_states[key]` 已是新状态，再读就成了"从自己淡到自己"。
            #   0 秒（默认）或该格此前无输出 → None，不写任何键 = 旧行为。
            fade = _recall_fade_from(self, key)
            self._external_states[key] = {
                "preset_id": effect_id,
                "params": params,
                "ch_types": ch_types,
                "group_ids": list(fixture_ids),
                **_reference_order.recall_fields(node, _archived_entry(state, track_type, group_key), base_order or list(fixture_ids), _normalize_fixture_sequence_ids),
                "speed_factor": speed_factor,
                "virtual_t": virtual_t,
                "last_real_t": 0.0,
                "sort_mode": sort_mode,
                # ★ 2026-09-14 用户定案：这是**场景调用带活的**、不是我正在编辑的 →
                #   打标记，保存新场景时排除它（"只要不是我正在编辑的就不存"）。
                #   用户一旦真的改该格（play_preset_for_type / _on_param_changed）
                #   标记即被清掉（那才算编辑）。
                "recalled": True,
            }
            # ★ R183 批 A：挂上召回过渡（fade 为 None → 一个字都不写 = 旧行为）
            _dispatch_state.bind_recalled_owner(self, node, state)
            _attach_recall_fade(self, key, fade)
            self._external_state = None
            self._is_playing = True
            if not self._play_timer.isActive():
                self._play_timer.start()
            # ★★ 2026-09-14 用户实报（保存区→监控区有明显延迟，"不应该有"）：
            #   本面板把 NM 层写入推迟到 `_play_timer`（25ms）里 → 而调用方
            #   `panel_library._recall_ids` 在**本函数返回后**立刻同步调
            #   `refresh_monitor()`，那一刻 NM 里还是**调用前**的值 → 监控区什么
            #   都没变，只能等它自己的值刷新 tick（快 tick 100ms / 慢 tick 400ms）
            #   才更新 → 用户看到 100~400ms 的可感延迟。
            #   修法：这里**立刻手跑一帧**（与"定时器 25ms 后跑第一帧"语义完全相同，
            #   只是提前），使 NM 在返回前就拿到调用后的值，同步刷新即命中。
            try:
                self._tick_play()
            except Exception:
                logger.debug("effect_browser: immediate tick after recall failed",
                             exc_info=True)
            self._update_transport_state()
            # ★★ 2026-09-14（切片键还原丢失·调度台路径）：上面第 1/2.5 步会切设备组与
            #   预设，切片键的**上下文**（preset@group@ch）随之改变，而 `load_state`
            #   里的补挂是在旧上下文上做的 → 这里必须**最后再补挂一次**，切片键才
            #   真正随场景一起还原（实报："存了切片键，调入后丢失"）。
            _rebind_slice_key_after_restore(self, state)
            self.report_to_bus(force=True)     # 立即反映到调度台
            return True
        except Exception:
            logger.debug("effect_browser: dispatch_recall_state failed", exc_info=True)
            return False


    def save_state(self) -> dict:
        """Persist Effect page UI choices with the project."""
        try:
            self._save_current_group_ui_state()
        except Exception:
            logger.debug("effect_browser: failed to save group UI state")
            pass
        active_effects = _active_effects_for_save(self)
        return {
            "version": 1,
            "selected_id": self._selected_id,
            "params": self._capture_current_param_values(),
            **_scope_state_for_save(self),   # ★ R165：作用域四键（含子集标记）
            "sort_mode": self._get_sort_mode(),
            "app_direction": self._get_app_direction(),
            "app_scope": self._get_app_scope(),
            "spatial_scope": self._get_spatial_scope(),
            "control_mode": self._get_control_mode(),
            "global_speed": float(getattr(self, "_global_speed_factor", 1.0)),
            "slice_key": self._slice_key,
            "slice_key_modifiers": int(getattr(self, "_slice_key_modifiers", 0) or 0),
            "manual_slice_index": int(getattr(self, "_manual_slice_index", 0) or 0),
            "slice_stride": int(getattr(self, "_slice_stride", 1) or 1),
            "slice_fade": float(self._current_slice_fade_seconds()),
            "slice_bindings_by_context": {
                str(ctx): [int(v[0]), int(v[1] or 0)]
                for ctx, v in (getattr(self, "_slice_bindings_by_context", {}) or {}).items()
                if v and v[0] is not None
            },
            "manual_slice_indices_by_context": {
                str(ctx): int(idx)
                for ctx, idx in (getattr(self, "_manual_slice_indices_by_context", {}) or {}).items()
            },
            "group_states": dict(getattr(self, "_group_states", {}) or {}),
            "effect_history": list(getattr(self, "_effect_history", []) or [])[:24],
            # ★ R183 批 A：召回过渡时长（同时持久化在用户档；工程内带走 = 换机一致）
            "recall_fade": float(_recall_fade_seconds(self)),
            # ★ R183 W2-FX #6：A/B 对比槽随工程（无槽 → {}，旧档字节不变）
            _w2fx.W2_STATE_KEY: _w2fx.state_for_save(self),
            "active_effects": active_effects,
            "effects_playing": bool(active_effects),
            # ★ 2026-09-13 去重（C2）：`param_envelopes` / `custom_effects` 这两个仓库
            #   原本**同时**写进本 state 和工程的专用字段
            #   （`project.param_envelopes_data` / `custom_effects_data`，见 main_window
            #   的收集/加载），是同一个单例的两份载体。加载顺序不同会让"哪份赢"变得
            #   隐式（实测后者覆盖前者）。现只保留专用字段为唯一载体，本 state 不再内嵌。
            #   `load_state` 仍**兼容**读取旧工程的内嵌键（见下），旧工程不会丢数据。
        }

    def load_state(self, state: Optional[dict]):
        """Restore Effect page UI choices from project state."""
        state = dict(state or {})

        # ── D1 通用参数包络：从项目状态重建仓库 + 绑定表 ────────────────────
        # ★ 仅当 state **确实带着**该键时才 from_dict：新工程把仓库写在专用字段
        #   （`project.param_envelopes_data`），本 state 里没有该键；若此处仍无条件
        #   `from_dict({})` 会**先把仓库清空**，再靠后面的专用字段救回来——顺序一旦
        #   变化就会静默丢数据。旧工程（内嵌键）照旧可加载。
        if "param_envelopes" in state:
            try:
                get_param_envelope_store().from_dict(
                    state.get("param_envelopes", {}) or {})
            except Exception:
                logger.debug("effect_browser: failed to restore param envelopes")
                pass

        # ── D2 自定义效果库：★ R178 已删除。旧工程 state 里可能内嵌
        #    `custom_effects` 键（2026-09-13 前的载体）—— 安全忽略不炸；
        #    其数据由 dispatch.custom_effect_migration 在工程装载点迁往调度台。

        # ── ★ R183 批 A：召回过渡时长（工程内也随档；旧档无该键 → 保持当前偏好）
        if "recall_fade" in state:
            _set_recall_fade_pref(self, state.get("recall_fade", 0.0))

        # ── ★ R183 W2-FX #6：A/B 对比槽（旧档无该键 → 什么都不做）
        _w2fx.apply_saved_state(self, state.get(_w2fx.W2_STATE_KEY))

        def _set_combo_data(combo, value):
            if combo is None or value is None:
                return
            try:
                idx = combo.findData(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            except Exception:
                logger.debug("effect_browser: failed to set combo data in load_state")
                pass

        try:
            self._group_states = dict(state.get("group_states", {}) or {})
        except Exception:
            logger.debug("effect_browser: failed to restore group states, using empty dict")
            self._group_states = {}
        # ★★★ R165：恢复**作用域三件套**（母集合 / 子集标记 / 组名）。
        #   旧存档没有这三键 → 保持现有值（不覆盖），行为与改前一致。
        #   不恢复的话：重载后「按TopView子集标记」的子集表为空 → 引擎退回按顺序划分，
        #   奇偶分组可能整体错位（本该反相的两组变同相）= 静默走样。
        _restore_scope_keys(self, state)
        try:
            self._effect_history = [
                str(pid) for pid in (state.get("effect_history", []) or []) if str(pid)
            ][:24]
        except Exception:
            logger.debug("effect_browser: failed to restore effect history, using empty list")
            self._effect_history = []

        _set_combo_data(getattr(self, "_sort_combo", None), state.get("sort_mode"))
        _set_combo_data(getattr(self, "_direction_combo", None), state.get("app_direction"))
        _set_combo_data(getattr(self, "_app_scope_combo", None),
                        normalize_effect_application_scope(state["app_scope"]) if "app_scope" in state else None)
        _set_combo_data(getattr(self, "_spatial_scope_combo", None), state.get("spatial_scope"))
        _set_combo_data(getattr(self, "_control_mode_combo", None), state.get("control_mode"))

        try:
            if "global_speed" in state:
                _master_speed.set_factor(self, float(state.get("global_speed", 1.0)))
        except Exception:
            logger.debug("effect_browser: failed to restore global speed in load_state")
            pass

        _restore_slice_bindings(self, state)

        try:
            if "slice_stride" in state:
                self._on_slice_stride_changed(int(state.get("slice_stride", 1) or 1))
            if "slice_fade" in state and getattr(self, "_slice_fade_spin", None) is not None:
                self._slice_fade_spin.blockSignals(True)
                self._slice_fade_spin.setValue(max(0.0, float(state.get("slice_fade", 0.0) or 0.0)))
                self._slice_fade_spin.blockSignals(False)
        except Exception:
            logger.debug("effect_browser: failed to restore slice settings in load_state")
            pass

        preset_id = state.get("selected_id")
        if preset_id and _catalog.can_restore_preset(self, preset_id):
            try:
                self._on_preset_click(preset_id, save_current=False)
                self._apply_param_values(dict(state.get("params", {}) or {}))
            except Exception:
                logger.debug("effect_browser: failed to restore selected preset in load_state")
                pass

        restored = {}
        try:
            for item in list(state.get("active_effects", []) or []):
                if not isinstance(item, dict):
                    continue
                track_type = str(item.get("track_type", "") or "")
                preset = str(item.get("preset_id", "") or "")
                if not track_type or not preset:
                    continue
                group_ids = _normalize_fixture_ids(item.get("group_ids", []))
                group_key = str(item.get("group_key", "") or "") or _group_key_from_ids(group_ids)
                restored[(track_type, group_key)] = {
                    "preset_id": preset,
                    "params": dict(item.get("params", {}) or {}),
                    "ch_types": list(item.get("ch_types", []) or []),
                    "sort_mode": item.get("sort_mode", "index") or "index",
                    "group_ids": group_ids,
                    **_reference_order.load_fields(item, _normalize_fixture_sequence_ids(item.get("base_order_ids", [])) or group_ids, _normalize_fixture_sequence_ids),
                    "speed_factor": float(item.get("speed_factor", 1.0) or 0.0),
                    "virtual_t": float(item.get("virtual_t", 0.0) or 0.0),
                    "last_real_t": 0.0,
                }
        except Exception:
            logger.debug("effect_browser: failed to restore active effects from state")
            restored = {}
        self._external_states = restored
        for st in restored.values():
            if isinstance(st, dict) and st.get("preset_id"):
                self._remember_effect_history(str(st.get("preset_id")))
        if restored:
            self._external_state = None
            if bool(state.get("effects_playing", True)) and self._nm is not None and self._project is not None:
                import time as _t
                self._is_playing = True
                self._play_start_time = _t.monotonic()
                self._virtual_t = 0.0
                self._last_real_t = 0.0
                self._play_timer.start()
        self._update_transport_state()
        self._refresh_quick_effect_lists()
        # 刷新效果库列表与包络子页（★ R178：自定义效果子页已删）。
        try:
            self._filter_list()   # 等价于旧的内联 _populate_list（含 R180 大类筛选）
        except Exception:
            logger.debug("effect_browser: failed to populate list in load_state")
            pass
        # ★★ 2026-09-14 实报修正（切片键还原丢失·终局）：切片键的**上下文**是
        #   `preset@group@ch` 动态键，而上面切片键的恢复**早于** `selected_id` 的恢复
        #   → 绑定被落到旧上下文；等 preset/分组切过来后，`_update_slice_key_label`
        #   按**新上下文**重解析，查不到该预设的绑定 → 把 `_slice_key` 清空
        #   （`_legacy_slice_binding` 兜底要求 bindings 全空，救不回来）。
        #   修法：全部界面状态（含 selected_id / 分组）就位后，**最后**再补挂一次
        #   并重解析 —— 此时上下文已稳定，"调入后切片键丢失"不再发生。
        _rebind_slice_key_after_restore(self, state)


def _archived_entry(state, track_type: str, group_key: str):
    """场景存档里**这一格**的条目（track_type + group_key 双匹配；匹配不到 → None）。"""
    for entry in ((state or {}).get("active_effects") or ()):
        if isinstance(entry, dict) and _entry_is_cell(entry, track_type, group_key):
            return entry
    return None


def _entry_is_cell(entry: dict, track_type: str, group_key: str) -> bool:
    """该存档条目是否就是这一格（身份 = track_type + group_key，与节点同构）。"""
    if str(entry.get("track_type", "")) != str(track_type):
        return False
    return str(entry.get("group_key", "")) == str(group_key)


def _num_or(v, default: float) -> float:
    """数值兜底（坏数据/缺失 → default），不抛。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _archived_cell_state(state, track_type: str, group_key: str):
    """从场景存档里取**这一格**当时的参数 → `(params, ch_types, sort_mode, speed, vt, base_order)`。

    ★★ 2026-09-18 回访修正（存档里写了、这里从不读 → 静默变形）：本格存档的
      `base_order_ids`（"编号排序基准序"）此前被**整段丢弃**，调用方一律用节点自报的
      fixture_ids 当基准序。而这两者语义不同：`group_ids` = 选择集原序、
      `base_order_ids` = 按基准/编号重排后的序（见 `_resolve_output_base_order_ids`），
      且 `sort_fixtures` 的 linear_* 等排序**以 base_order 为第一排序键**
      （见 effect_engine.sort_fixtures 的 `base_key`）→ 丢掉它会让"存的时候是
      1-2-3 顺序跑、调出来变成 3-2-1"（静默变形）。工程存档路径（`load_state`）本就
      读这个键，两条路因此不对称；这里统一取值口径，两路同源。
      匹配不到该格（旧档/已改 key）→ 返回全套默认值（调用方保持原行为）。
    """
    entry = _archived_entry(state, track_type, group_key)
    if entry is None:
        return {}, [], "index", 1.0, 0.0, []
    return (
        dict(entry.get("params") or {}),
        list(entry.get("ch_types") or []),
        str(entry.get("sort_mode") or "index"),
        _num_or(entry.get("speed_factor"), 1.0),
        _num_or(entry.get("virtual_t"), 0.0),
        [str(x) for x in (entry.get("base_order_ids") or []) if str(x)],
    )


def _restore_recall_scope(panel, fixture_ids: list, node: dict,
                          for_edit: bool) -> None:
    """恢复该格的设备作用域（播放/编辑两种语义的唯一分流点）。

    · for_edit=True  → `set_live_fixture_selection`（切设备组 + 刷分组/参数控件）
    · for_edit=False → 节点自己的 params/state 持有目标，不改当前编辑选择。

    ★★★ R165：**带上 node 里的母集合与子集标记** —— 旧写法把 `fixture_ids` 当基准序
      传两遍，于是"母集合 N 台里子集错相"这种效果**调用后只剩子集**（母集合丢掉 →
      作用域缩水；子集标记丢掉 → 奇偶划分整体错位）。两者都取 node 的值，缺省回退
      `fixture_ids`（旧存档/旧节点天然兼容）。
    """
    if not for_edit or not fixture_ids:
        return
    base_ids = _node_str_ids(node.get("base_ids")) or list(fixture_ids)
    subset_ids = _node_str_ids(node.get("subset_ids"))
    try:
        panel.set_live_fixture_selection(
            fixture_ids, base_ids,
            name=str(node.get("group_name", "") or ""),
            subset_ids=subset_ids or None)
    except Exception:
        logger.debug("effect_browser: dispatch recall scope failed", exc_info=True)


def _active_effects_for_save(panel) -> list:
    """`_external_states` → 存档用的 `active_effects` 列表（字段白名单）。

    ⚠ 新增**每格**状态时必须同时改这里与 `load_state` 的读取端 —— `params` 是整字典
    会自动流转，而 `group_ids`/`base_order_ids` 这类白名单字段不会。
    """
    out = []
    overrides = getattr(panel, "_param_overrides", {}) or {}
    for (track_type, group_key), st in list(
            getattr(panel, "_external_states", {}).items()):
        params = dict(st.get("params", {}) or {})
        # Freeze the current amplitude in this owner, below future live input.
        params.update({key: overrides[key] for key in _JOY_AMP_OVERRIDE_KEYS if key in overrides})
        out.append({
            "track_type": track_type,
            "group_key": group_key,
            "preset_id": st.get("preset_id", ""),
            "params": params,
            "ch_types": list(st.get("ch_types", []) or []),
            "sort_mode": st.get("sort_mode", "index"),
            "group_ids": list(st.get("group_ids", []) or []),
            **_reference_order.save_fields(st),
            "speed_factor": float(st.get("speed_factor", 1.0) or 0.0),
            "virtual_t": float(st.get("virtual_t", 0.0) or 0.0),
        })
    return out


def _node_str_ids(vals) -> list:
    """节点/状态里的 id 表 → 干净 `[str]`（None/坏值 → []）。抽出来压各函数的 CC。"""
    try:
        return [str(x) for x in (vals or []) if str(x)]
    except Exception:
        return []


def _apply_str_list_keys(panel, state: dict, pairs) -> None:
    """把 `state[key]` 的 id 列表写入 `panel.attr`；**键不存在则不覆盖**（旧存档兼容）。"""
    for key, attr in pairs:
        if key not in state:
            continue
        setattr(panel, attr, _node_str_ids(state.get(key)))


def _scope_state_for_save(panel) -> dict:
    """★★★ R165：effect 面板的**作用域四键**（母集合 / 子集标记 / 选择 / 组名）。

    为什么要存：`子集来源=按TopView子集标记` 时，子集标记决定哪个子集是第 0 组；
    丢了就退回"按顺序划分"，奇偶分组可能**整体错位**（本该反相的两组变同相）——
    用户看不出报错，只觉得"效果不对"。母集合丢了则作用域缩水成子集。
    """
    return {
        **_target_scope.state_for_save(panel),
        "selected_group_ids": list(getattr(panel, "_selected_group_ids", []) or []),
        "selected_group_base_order_ids": list(
            getattr(panel, "_selected_group_base_order_ids", []) or []),
        # ★★★ R168：**作用域**必须一起存 —— 它是"这是哪个效果格"的键。丢了则重载/调用后
        #   子集又会被当成新效果（母集合状态不传递），退化成用户报的那个 bug。
        "selected_group_scope_ids": list(
            getattr(panel, "_selected_group_scope_ids", []) or []),
        "selected_group_subset_ids": list(
            getattr(panel, "_selected_group_subset_ids", []) or []),
        "selected_group_name": str(getattr(panel, "_selected_group_name", "") or ""),
    }


def _restore_scope_keys(panel, state: dict) -> None:
    """★★★ R165：从存档恢复 effect 面板的**作用域三件套**（母集合 / 子集标记 / 组名）。

    旧存档没有这三个键 → **不覆盖**现有值，行为与改前一致（不会凭空清掉作用域）。

    为什么必须存/取：
      · 「子集来源=按TopView子集标记」时，`_selected_group_subset_ids` 决定哪个子集是
        第 0 组；丢了就退回"按顺序划分"，奇偶分组可能**整体错位**（本该反相的两组
        变成同相）——用户看不出报错，只觉得"效果不对"。
      · `base_order_ids` = 母集合基准序；丢了作用域会缩水成子集
        （"母集合 N 台里奇偶错相"退化成"只有奇数十台有输出"）。
    """
    if not isinstance(state, dict):
        return
    _apply_str_list_keys(panel, state, (
        ("selected_group_ids", "_selected_group_ids"),
        ("selected_group_base_order_ids", "_selected_group_base_order_ids"),
        ("selected_group_scope_ids", "_selected_group_scope_ids"),   # ★ R168
        ("selected_group_subset_ids", "_selected_group_subset_ids"),
    ))
    # ★ R168：旧存档没有 `selected_group_scope_ids` → 按 R168 规则回推，避免作用域退化
    if "selected_group_scope_ids" not in state:
        panel._selected_group_scope_ids = _compute_group_scope_ids(
            getattr(panel, "_selected_group_ids", None),
            getattr(panel, "_selected_group_base_order_ids", None))
    if "selected_group_name" in state:
        panel._selected_group_name = str(state.get("selected_group_name", "") or "")
    _target_scope.restore_explicit_scope(panel, state)
    panel._scope_version = int(getattr(panel, "_scope_version", 0)) + 1  # 失效作用域缓存

def _update_preset_badge(panel, preset) -> None:
    """刷新信息徽标行（restyle_spec §10：glyph16 + 名12px + 分类11px 弱化色，分类色只落在 glyph 上）。

    ★ R141：从 `_load_preset_params` 抽出 —— 复用分支（不重建控件）也必须刷徽标。
    """
    try:
        cat_norm = normalize_effect_category(preset.category)
        cat_color = _get_category_color(preset.category)
        panel._preset_name_lbl.setText(_catalog.display_name(preset))
        panel._preset_cat_lbl.setText(str(cat_norm or ""))
        glyph = load_tabler_icon(effect_icon_name_for_preset(preset), 16, cat_color)
        if not glyph.isNull():
            panel._preset_icon_lbl.setPixmap(glyph.pixmap(16, 16))
        else:
            panel._preset_icon_lbl.clear()
    except Exception:
        logger.debug("effect_browser: failed to load preset badge glyph")
        try:
            panel._preset_icon_lbl.clear()
        except Exception:
            pass
    try:
        panel._preset_desc_lbl.setText(_catalog.display_description(preset))
    except Exception:
        logger.debug("effect_browser: failed to set preset description")


def _param_tree_signature(preset) -> Optional[tuple]:
    """Complete registered and generated parameter render contract."""
    return _param_reuse.signature(preset)


def _reuse_preset_param_widgets(panel, preset_id: str, sig) -> bool:
    """Reuse the current tree while preserving same-ID edits and new-ID defaults."""
    reused = _param_reuse.reuse(panel, preset_id, sig)
    if reused:
        _form_author.prepare_preset(panel, preset_id)
    return reused


def _finish_preset_param_page(panel) -> None:
    """参数控件树就位后的统一收口。"""
    panel._restore_running_effect_ui_state()
    panel._sync_transport_controls_to_active_form()
    panel._refresh_param_bind_indicators()
    _width_controls.finish_parameters(panel, _fx_workspace.refresh_parameters)
    _audio_controls.finish_parameters(panel)


def _form_type_of(panel, form) -> str:
    """该表单当前的通道类型；取不到 → 空串（调用方据此判定不可复用）。"""
    try:
        return str(panel._channel_form_type(form))
    except Exception:
        return ""


def _reuse_channel_forms(panel, forms) -> bool:
    """现有通道表单能否**原位复用**（通道类型逐项一致即复用，只回填值不重建控件）。

    ★ R141：`_restore_channel_forms` 原实现无条件 `setParent(None)` + `deleteLater()`
      全部表单再逐个重建 —— 一次 `load_state` 里跑 3 遍。通道类型序列一致时，
      控件树结构完全相同，逐项 `_apply_channel_form_values` 是等价变换。
    """
    items = [it for it in (forms or []) if isinstance(it, dict)]
    cur = list(getattr(panel, "_channel_forms", None) or [])
    if not (bool(items) and len(cur) == len(items)):
        return False
    for form, item in zip(cur, items):
        if _form_type_of(panel, form) != str(item.get("ch_type", "") or ""):
            return False
    return True


def _apply_reused_channel_forms(panel, forms) -> None:
    """复用路径的写入侧：逐项回填参数值并刷新列表项文案（不新建/销毁控件）。"""
    for form, item in zip(list(panel._channel_forms), forms):
        form_params = dict(item.get("params", {}) or {})
        if "playing" in item:
            form_params["playing"] = bool(item.get("playing"))
        if "speed_factor" in item:
            form_params["speed_factor"] = item.get("speed_factor")
        form.pop("_unrepresented_params", None)
        _form_author.restore(panel, form, form_params)
        panel._update_channel_form_item(form)
    if panel._channel_forms:
        panel._set_active_channel_form(panel._channel_forms[0])


def _channel_form_restore_values(form, params):
    """Preserve legacy values without rebuilding their retired controls."""
    values = dict(params or {})
    known = form.get("params", {}) or {}
    form.setdefault("_unrepresented_params", {}).update({
        key: deepcopy(value) for key, value in values.items()
        if key not in known and key not in {"playing", "speed_factor"}})
    return values


def _restore_channel_forms_smart(panel, forms) -> None:
    """`_restore_channel_forms` 的**复用优先**包装（R141）：结构相同就只回填值。

    ★ 放在模块级而非改 `_restore_channel_forms` 本体：后者在门禁基线里是
      27 行 / CC 18（**零余量**），加任何一个分支都会越界；而这里是纯新增函数。
    """
    if _reuse_channel_forms(panel, forms):
        _apply_reused_channel_forms(panel, forms)
        return
    panel._restore_channel_forms(forms)


# ═════════════════════════════════════════════════════════════════════════════
#  R183 批 A：收藏星标 / 用户预设另存（模块级实现；上帝类只保留两行接线）
# ═════════════════════════════════════════════════════════════════════════════

def _user_state_for_list(eng, bar) -> list:
    """列表刷新前的用户态同步：注入用户预设 + 刷新收藏计数；返回收藏 id 表。

    抽成模块级以守 `_populate_list` 的行数门禁（本仓 `main_window_*.py` 同范式）。
    任何一步失败都**不阻断列表刷新**（用户预设/收藏是增益，不是前置条件）。
    """
    try:
        _fxstore.register_user_presets(eng)
    except Exception:
        logger.debug("effect_browser: register user presets failed", exc_info=True)
    try:
        favorites = _fxstore.list_favorites()
    except Exception:
        favorites = []
    if bar is not None:
        try:
            bar.set_favorite_count(len(order_favorites(eng.presets.values(), favorites)))
        except Exception:
            logger.debug("effect_browser: refresh favorite count failed", exc_info=True)
    return favorites

def _toggle_favorite_and_refresh(panel, preset_id) -> bool:
    """切换收藏并刷新列表/分类条；返回切换后是否已收藏。

    收藏是**用户态**（`effect_user_store` 持久化），不进预设元数据；刷新走
    `_filter_list()`（唯一列表重建入口）→ 星标/「收藏」chip/收藏视图同步。
    """
    pid = str(preset_id or "")
    if not pid:
        return False
    try:
        on = bool(_fxstore.toggle_favorite(pid))
    except Exception:
        logger.debug("effect_browser: toggle favorite failed", exc_info=True)
        return False
    try:
        panel._filter_list()
    except Exception:
        logger.debug("effect_browser: refresh after favorite toggle failed",
                     exc_info=True)
    return on


def _ask_user_preset_name(panel, base_id, title: str = "另存为我的效果"):
    """弹名称输入框；返回 (名字, 是否确认)。取消/异常 → (None, False)。"""
    try:
        default = panel._effect_display_name(base_id) + " 副本"
        name, ok = QInputDialog.getText(panel, title, "预设名称:", text=default)
    except Exception:
        logger.debug("effect_browser: preset name dialog failed", exc_info=True)
        return None, False
    return name, bool(ok)


def _persist_user_preset(panel, name, base_id, params) -> Optional[str]:
    """落盘 + 注入引擎 + 拉回全库视图；返回落库 id（任一步失败 → None）。"""
    try:
        preset = get_engine().get_preset(base_id)
        desc = str(getattr(preset, "name", "") or "")
    except Exception:
        desc = ""
    try:
        pid = _fxstore.save_user_preset(
            name, base_id, params, description=f"基于 {desc}" if desc else "")
    except Exception:
        logger.debug("effect_browser: save user preset failed", exc_info=True)
        return None
    if not pid:
        return None
    try:
        _fxstore.register_user_presets(get_engine(), force=True)
    except Exception:
        logger.debug("effect_browser: re-register after save failed", exc_info=True)
    _clear_filters_after_save(panel)
    return pid


def _save_current_as_user_preset(panel, name=None) -> Optional[str]:
    """把当前预设的**参数控件值**另存为命名用户预设；返回落库 id（失败 None）。

    参数快照取 `_capture_current_param_values()`（save_state 同源），落
    `effect_user_store`；随后 `register_user_presets(force=True)` 注入引擎并
    清「全部」筛选，保证新条目**立刻可见**（用户刚存的东西不能找不到）。
    `name=None` 时弹输入框（QInputDialog，与场景改名同范式）。
    """
    base_id = str(getattr(panel, "_selected_id", "") or "")
    if not base_id:
        return None
    if name is None:
        name, ok = _ask_user_preset_name(panel, base_id)
        if not ok:
            return None
    try:
        params = dict(panel._capture_current_param_values() or {})
    except Exception:
        logger.debug("effect_browser: capture params for user preset failed",
                     exc_info=True)
        return None
    return _persist_user_preset(panel, name, base_id, params)


def _clear_filters_after_save(panel) -> None:
    """另存/删除/改名后把视图拉回全库：新条目必在「其它」大类下，不能被旧筛选挡住。"""
    try:
        panel._search.setText("")
    except Exception:
        logger.debug("effect_browser: clear search after save failed")
    bar = getattr(panel, "_cat_bar", None)
    if bar is not None:
        try:
            bar.set_top_key("")
        except Exception:
            logger.debug("effect_browser: clear category filter after save failed")
    try:
        panel._filter_list()
    except Exception:
        logger.debug("effect_browser: refresh list after save failed", exc_info=True)


def _show_preset_menu(panel, preset_id, global_pos) -> None:
    """预设卡片右键菜单：另存为我的效果 / 收藏切换 / 重命名 / 删除。

    重命名与删除**只对该条目是用户预设时可用**（内置预设是库资产，不允许改）。
    """
    pid = str(preset_id or "")
    if not pid:
        return
    try:
        name = _fxstore.find_user_preset_name(pid)
    except Exception:
        name = ""
    menu = QMenu(panel)
    acts = {
        "save": menu.addAction("另存为我的效果…"),
        "fav": menu.addAction("取消收藏" if _is_fav(pid) else "加入收藏"),
    }
    if name:
        acts["rename"] = menu.addAction("重命名我的效果…")
        acts["delete"] = menu.addAction("删除我的效果")
    # ★ R183 W2-FX #3/#10：编辑标签 / 使用次数（实现在 _w2fx）
    _w2fx.extend_card_menu(menu, panel, pid)
    chosen = menu.exec(global_pos)
    for tag, action in acts.items():
        if chosen is action:
            _run_preset_menu_action(tag, panel, pid, name)
            return


def _run_preset_menu_action(tag: str, panel, pid: str, name: str) -> None:
    """右键菜单动作分发（`_show_preset_menu` 的瘦末端；异常只记日志）。"""
    handlers = {
        "save": lambda: _save_current_as_user_preset(panel, None),
        "fav": lambda: _toggle_favorite_and_refresh(panel, pid),
        "rename": lambda: _rename_user_preset_dialog(panel, name),
        "delete": lambda: _delete_user_preset_with_confirm(panel, name),
    }
    fn = handlers.get(str(tag))
    if fn is not None:
        _guarded(fn, str(tag))


def _guarded(fn, tag: str) -> None:
    """执行回调并把异常压成日志（菜单动作绝不允许把异常抛回事件循环）。"""
    try:
        fn()
    except Exception:
        logger.debug("effect_browser: preset menu action %s failed", tag,
                     exc_info=True)


def _is_fav(preset_id) -> bool:
    try:
        return bool(_fxstore.is_favorite(preset_id))
    except Exception:
        return False


def _ask_rename_target(panel, old_name):
    """弹重命名输入框；返回 (新名, 是否确认)。取消 → (None, False)。"""
    try:
        new_name, ok = QInputDialog.getText(
            panel, "重命名我的效果", "新名称:", text=str(old_name))
    except Exception:
        return None, False
    if not ok or not str(new_name or "").strip():
        return None, False
    return new_name, True


def _rename_user_preset_dialog(panel, old_name) -> bool:
    """重命名用户预设（QInputDialog；占用/非法 → 提示不炸）。"""
    new_name, ok = _ask_rename_target(panel, old_name)
    if not ok:
        return False
    try:
        done = bool(_fxstore.rename_user_preset(old_name, new_name))
    except Exception:
        done = False
    if not done:
        _warn(panel, "重命名失败", "名称重复或为空，或旧条目已不存在。")
        return False
    try:
        _fxstore.register_user_presets(get_engine(), force=True)
    except Exception:
        logger.debug("effect_browser: re-register after rename failed")
    _clear_filters_after_save(panel)
    return True


def _warn(panel, title: str, text: str) -> None:
    """模态警告（对话框本身失败 → 只记日志，绝不因提示失败而中断主流程）。"""
    try:
        QMessageBox.warning(panel, title, text)
    except Exception:
        logger.debug("effect_browser: warning dialog failed")


def _confirm(panel, title: str, text: str) -> bool:
    """模态确认；取消/异常 → False（**默认不删**，保守侧）。"""
    try:
        ans = QMessageBox.question(panel, title, text)
    except Exception:
        logger.debug("effect_browser: confirm dialog failed")
        return False
    return ans == QMessageBox.StandardButton.Yes


def _delete_user_preset_with_confirm(panel, name) -> bool:
    """删除用户预设（确认后删；同时撤收藏标记，避免收藏榜留幽灵 id）。"""
    if not _confirm(panel, "删除我的效果", f"删除「{name}」？此操作不可撤销。"):
        return False
    try:
        rec = _fxstore.get_user_preset(name) or {}
        pid = str(rec.get("id", "") or "")
        if not _fxstore.delete_user_preset(name):
            return False
        if pid:
            _fxstore.set_favorite(pid, False)
        _fxstore.register_user_presets(get_engine(), force=True)
    except Exception:
        logger.debug("effect_browser: delete user preset failed", exc_info=True)
        return False
    _clear_filters_after_save(panel)
    return True


def _recall_fade_seconds(panel) -> float:
    """当前用户设定的召回过渡秒数（控件缺失 → 用户偏好档 → 0）。"""
    spin = getattr(panel, "_recall_fade_spin", None)
    if spin is not None:
        try:
            return max(0.0, float(spin.value()))
        except Exception:
            logger.debug("effect_browser: read recall fade spin failed")
    try:
        return float(_fxstore.recall_fade_pref())
    except Exception:
        return 0.0


def _recall_fade_from(panel, key):
    """构造该格召回过渡的起点记录；秒数 ≤0 或该格此前无输出 → None（不淡）。

    起点 = 上一格**当前实际输出**的参数（若上一格自己还在过渡中，取插值中间态，
    连续召回不会"跳回旧起点再淡"，而是从当前视觉状态续接到新目标）。
    """
    import time as _time
    secs = _recall_fade_seconds(panel)
    prev = (getattr(panel, "_external_states", {}) or {}).get(key)
    if secs <= 0.0 or not isinstance(prev, dict):
        return None
    prev_params = dict(prev.get("params", {}) or {})
    now = _time.monotonic()
    return _recallfade.make_fade_record(
        _recallfade.recall_fade_params(prev, prev_params, now),
        {"speed_factor": float(prev.get("speed_factor", 1.0) or 0.0)},
        secs, now)


def _attach_recall_fade(panel, key, fade) -> None:
    """把过渡记录挂到该格状态上（fade 为 None = 不淡，一个字也不写）。"""
    if not isinstance(fade, dict):
        return
    state = (getattr(panel, "_external_states", {}) or {}).get(key)
    if isinstance(state, dict):
        state[_recallfade.FADE_STATE_KEY] = fade


def _set_recall_fade_pref(panel, value) -> float:
    """写召回过渡秒数偏好 + 同步控件（非法值 → 0）；返回夹取后的秒数。"""
    try:
        secs = max(0.0, min(float(_recallfade.FADE_MAX), float(value or 0.0)))
    except (TypeError, ValueError):
        secs = 0.0
    try:
        _fxstore.set_recall_fade_pref(secs)
    except Exception:
        logger.debug("effect_browser: persist recall fade pref failed", exc_info=True)
    spin = getattr(panel, "_recall_fade_spin", None)
    if spin is not None:
        try:
            spin.blockSignals(True)
            spin.setValue(secs)
            spin.blockSignals(False)
        except Exception:
            logger.debug("effect_browser: sync recall fade spin failed")
    return secs


def _legacy_channel_forms(values, key) -> list:
    """旧档形态 → 合成通道表单清单：通道类型在 `values[key]`，参数平铺在顶层。

    与 `_apply_param_values` 里那段 legacy 分支**同一口径**
    （`legacy_params` = 除 `key`/`_channel_forms` 外的全部顶层键）。
    """
    raw = values.get(key) if key else None
    chs = [str(c) for c in raw] if isinstance(raw, (list, tuple)) else ([str(raw)] if raw else [])
    if not chs:
        return []
    legacy_params = {k: v for k, v in values.items()
                     if k not in {key, "_channel_forms"}}
    return [{"ch_type": c, "params": dict(legacy_params)} for c in chs]


def _apply_param_values_smart(panel, values) -> None:
    """`_apply_param_values` 的**通道表单复用优先**包装（R141）。

    为什么需要第二个入口：`_apply_param_values` 对 `_channel_forms` 有**两种**装配路径 ——
    新版（`_channel_forms` 内嵌，走 `_restore_channel_forms`）与**旧档 legacy**（通道类型
    只在 `_channel_param_key` 的值里、参数平铺顶层，见 `_apply_param_values` 里
    `"_channel_forms" not in values` 那段）。两者都会 `setParent(None)`+`deleteLater()`
    拆掉全部通道表单再重建；真工程实测这两种各白拆一遍
    （`probe_r141_whobuilds.py` / `probe_r141_dbg2.py`）。
    本函数把**两种形态归一**成"表单清单"后只走一次复用判定；命中就只回填值，
    并把会触发内层重建的键从 `values` 里摘掉（`_channel_forms` 与 `key`），
    剩下的顶层参数由内层循环照常写进控件。
    """
    if not isinstance(values, dict):
        panel._apply_param_values(values)
        return
    key = str(getattr(panel, "_channel_param_key", "") or "")
    values = dict(values)
    forms = list(values.get("_channel_forms") or []) or _legacy_channel_forms(values, key)
    if forms and _reuse_channel_forms(panel, forms):
        _apply_reused_channel_forms(panel, forms)
        values.pop("_channel_forms", None)
        if key:
            values.pop(key, None)      # 抑制内层 legacy 分支再拆一遍
    panel._apply_param_values(values)


def _apply_playback_scope(panel, ids, base_order_ids=None) -> None:
    """**纯播放语义**的作用域设置：只改引擎读取的作用域字段，**不动任何控件**。

    ★ 2026-09-18 问题9：`set_live_fixture_selection` 除设作用域外还会
      `_save_current_group_ui_state()` + 重置分组下拉 + `_restore_group_ui_state()`
      （刷预设/参数控件）—— 那是"编辑语义"的动作。左键点场景卡只想**播**，
      不该动用户正在编辑的页面，故这里只做作用域三字段 + 版本号。

    与 `set_live_fixture_selection` 的等价性：作用域解析
    （`_effective_output_scope_ids` / `_current_live_selection_key`）只读
    `_selected_group_ids` / `_selected_group_base_order_ids` /
    `_selected_group_spatial_groups*` 与 `_scope_version` —— 本方法把这五项
    设成与完整版**逐字节相同**的值，故"效果播到哪些设备"完全一致。
    """
    ids_norm = _normalize_fixture_ids(ids)
    base_norm = (_normalize_fixture_sequence_ids(base_order_ids)
                 or _normalize_fixture_sequence_ids(ids))
    panel._scope_version = int(getattr(panel, "_scope_version", 0)) + 1
    _target_scope.assign_targets(panel, ids_norm, base_norm, follow=False)
    panel._selected_group_scope_ids = list(ids_norm)   # ★ R168：播放语义=已定作用域
    panel._selected_group_spatial_groups = panel._normalize_spatial_groups(None, ids_norm)
    panel._selected_group_spatial_groups_raw = panel._normalize_spatial_groups(
        None, ids_norm, keep_single=True)
    panel._slice_context_key = ""
    panel._manual_slice_frame_cache = {}


def _reveal_preset_in_categories(panel, effect_id) -> bool:
    """清搜索并（必要时）回退分类，直到预设可见；返回最终是否可见。

    ★ R182：只清搜索，**保留用户的大类/细分类选择**——旧实现直接 `bar.clear()`
      把分类弹回"全部"（用户实报"分类被弹回"）。只有清搜索后预设仍被分类挡住
      时才回退全库（最小侵入）。抽成模块级以守 `_sync_ui_to_recalled_cell`
      的嵌套深度门禁（CLAUDE.md §10.2）。
    """
    try:
        panel._search.setText("")
    except Exception:
        return False
    try:
        panel._filter_list()
    except Exception:
        pass
    if panel._is_preset_visible(effect_id):
        return True
    bar = getattr(panel, "_cat_bar", None)
    if bar is None:
        return False
    try:
        bar.clear()
        panel._filter_list()
    except Exception:
        pass
    return bool(panel._is_preset_visible(effect_id))


def _sync_ui_to_recalled_cell(panel, effect_id, params, fixture_ids) -> None:
    """把调入那一格的参数刷到**可见控件**（否则引擎在跑旧参数、页面还是默认值）。

    ★ restore 内部 `_on_preset_click` 会用控件值**重存** group_states（把条目覆盖成
      默认参数）—— 故末尾以该格保存参数为准**重写一次**。
    """
    try:
        sel_key = _group_key_from_ids(panel._selected_group_ids)
        ui_params = dict(params)
        ui_params["_output_fixture_ids"] = list(fixture_ids)
        ui_params["_output_base_order_ids"] = list(fixture_ids)
        panel._group_states[sel_key] = {"selected_id": effect_id, "params": ui_params}
        # load_state 会还原搜索框文本——可能把该预设过滤隐藏；先清过滤保证可见。
        if not _catalog.can_restore_preset(panel, effect_id):
            _reveal_preset_in_categories(panel, effect_id)
        if _catalog.can_restore_preset(panel, effect_id):
            panel._restore_group_ui_state()
        panel._group_states[sel_key] = {"selected_id": effect_id, "params": ui_params}
    except Exception:
        logger.debug("effect_browser: dispatch recall sync ui failed", exc_info=True)


def _dispatch_release_state(panel, node: dict, state=None) -> bool:
    """**释放**某调度台节点对应的效果（关闭场景 / 取消激活时调用）。

    ★★ 2026-09-14 用户实报："再点一次（hold 模式）它却没有 release，一直消不掉…
      而且 latch 模式…依然该 release 的时候不 release。"
    真因：关闭场景原实现只清 NetworkManager 的层，但**效果引擎仍在每帧把该效果
    重新写回**（`_play_timer` 仍在跑、`_external_states` 里该格还在）→ 清掉的层
    下一帧就被灌回，表现为"消不掉"。真正的释放必须**停掉该格的外部驱动**。

    语义：只停该 node 对应的那一格（`track_type` + 设备集），不打断其它并行格；
    若该格是最后一个，`stop_external_type` 会收口到 `stop_external()`（清层停表）。
    """
    try:
        node = dict(node or {})
        node = dict(node or {})
        before = len(getattr(panel, "_external_states", {}) or {})
        if not _stop_node_by_node(panel, node):
            return False
        _sync_transport_after_release(panel)
        try:
            panel.report_to_bus(force=True)  # 立即反映到调度台（窗格消失）
        except Exception:
            pass        # 重报失败不影响"已释放"这一事实
        return len(getattr(panel, "_external_states", {}) or {}) != before
    except Exception:
        logger.debug("effect_browser: dispatch_release_state failed", exc_info=True)
        return False


def _stop_node_by_node(panel, node: dict) -> bool:
    """按节点停一个效果格；节点身份为空（无从定位）时返回 False。"""
    track_type = str(node.get("track_type", "") or "")
    ids = [str(x) for x in (node.get("fixture_ids", []) or []) if str(x)]
    if not track_type and not ids:
        return False
    _stop_node_effect(panel, track_type, ids)
    return True


def _stop_node_effect(panel, track_type: str, ids: list) -> None:
    """停掉该节点对应的效果格。

    ★★ 为什么不能直接用 `stop_external_type(track_type, ids)`：那个内部把设备集**重算**
    成 `grp:<id>|<id>...` 再拼 key，而 recall 存进 `_external_states` 的 key 用的是
    **节点自报的 `group_key`**（可能是 `g1`、`__all__`、或已创建组 id）→ 两者不一致
    时 pop **打不中**，表现为"点了关闭也消不掉"（实测复现：key 是 ('R','g1')，
    释放却去 pop ('R','grp:f0|f1|...')）。

    改为**按设备集合匹配**：遍历该 track_type 下的所有格，设备集合与目标相同（或目标
    为空=按类型全停）的整格移除。这样与 key 的表示法无关，语义仍精确到格。
    """
    target = {str(x) for x in (ids or ()) if str(x)}
    states = getattr(panel, "_external_states", None)
    if not isinstance(states, dict):
        return
    doomed = [k for k in list(states.keys())
              if _release_key_matches(states.get(k) or {}, k, track_type, target)]
    for key in doomed:
        _reset_stopped_form(panel, states.pop(key, None))
    if doomed:
        _after_effect_release(panel)


def _release_key_matches(st: dict, key, track_type: str, target: set) -> bool:
    """该格是否属于要释放的目标（类型匹配 + 设备集合匹配）。"""
    try:
        if track_type and str(key[0]) != str(track_type):
            return False
        if not target:
            return True                     # 无设备集 → 该类型全停
        have = {str(x) for x in (st.get("group_ids", []) or []) if str(x)}
        return have == target               # 设备集合相同 → 就是这一格
    except Exception:
        return False


def _reset_stopped_form(panel, st) -> None:
    """把该格涉及的通道表单标为停止（与 stop_external_type 同语义）。"""
    if not isinstance(st, dict):
        return
    stopped = {str(ch) for ch in (st.get("ch_types", []) or []) if str(ch)}
    for form in list(getattr(panel, "_channel_forms", []) or []):
        try:
            if panel._channel_form_type(form) in stopped:
                form["playing"] = False
                form["virtual_t"] = 0.0
                form["last_real_t"] = 0.0
        except Exception:
            continue


def _after_effect_release(panel) -> None:
    """有格被移除后收口：全空了就彻底停（清 effect 层 + 停表），否则刷新状态。"""
    if not getattr(panel, "_external_states", None):
        panel.stop_external()
    else:
        panel._external_state = None
        panel._update_transport_state()
        panel._refresh_quick_effect_lists()


def _sync_transport_after_release(panel) -> None:
    """释放后同步走带状态（仍有别的格在跑时刷新 transport，保持 UI 一致）。"""
    if getattr(panel, "_external_states", None) and not panel._is_playing:
        panel._update_transport_state()


EffectBrowserWidget.dispatch_release_state = _dispatch_release_state
# ★ 声明本面板理解 `for_edit` 语义位（dispatch_flow 据此传参；见 recall_nodes）
EffectBrowserWidget.dispatch_recall_state.__accepts_for_edit__ = True


# ── 切片键恢复（模块级：上帝类禁止新增方法 / 控制函数体积，见 CLAUDE.md 10.1）──
def _restore_slice_bindings(panel, state) -> None:
    """从 state 恢复切片绑定与切片键。

    ★★ 2026-09-14 实报修正（切片键还原丢失）：`_update_slice_key_label` 按**当前
      上下文**（`preset@group@ch`，动态键）解析切片键；若 bindings 里只有**别的
      上下文**的条目，当前上下文查不到 → 刚落下的 `_slice_key` 被重置为 None
      （`_legacy_slice_binding` 兜底要求 bindings **全空**才生效，救不回来）。
      故这里把切片键**显式绑定到当前上下文**后再解析标签。
      注意：此时 selected_id/分组可能尚未恢复 → 上下文还会变，
      `_rebind_slice_key_after_restore` 会在 `load_state` 末尾再补一次。
    """
    try:
        panel._slice_bindings_by_context = _parse_slice_bindings(state)
        panel._manual_slice_indices_by_context = {
            str(ctx): int(idx)
            for ctx, idx in (state.get("manual_slice_indices_by_context", {}) or {}).items()
        }
        panel._slice_context_key = ""
        key = state.get("slice_key", None)
        panel._slice_key = int(key) if key is not None else None
        panel._slice_key_modifiers = int(state.get("slice_key_modifiers", 0) or 0)
        panel._manual_slice_index = int(state.get("manual_slice_index", 0) or 0)
        panel._legacy_slice_binding = (
            (panel._slice_key, panel._slice_key_modifiers)
            if panel._slice_key is not None else None
        )
        panel._capturing_slice_key = False
        _bind_slice_key_to_current_context(panel)
        panel._update_slice_key_label()
    except Exception:
        logger.debug("effect_browser: failed to restore slice bindings in load_state")
        panel._slice_key = None
        panel._slice_key_modifiers = 0
        panel._legacy_slice_binding = None
        panel._update_slice_key_label()


def _parse_slice_bindings(state) -> dict:
    """state 里的 `{上下文: [key, mods]}` → `{上下文: (key, mods)}`（跳坏数据）。"""
    bindings = {}
    for ctx, value in (state.get("slice_bindings_by_context", {}) or {}).items():
        if isinstance(value, (list, tuple)) and value:
            bindings[str(ctx)] = (int(value[0]), int(value[1] if len(value) > 1 else 0))
    return bindings


def _bind_slice_key_to_current_context(panel) -> None:
    """把当前切片键**显式绑定到当前上下文**（上下文查不到 binding 时兜底）。"""
    if panel._slice_key is None:
        return
    try:
        ctx = panel._sync_manual_slice_context()
        if ctx and ctx not in panel._slice_bindings_by_context:
            panel._slice_bindings_by_context[ctx] = (
                panel._slice_key, panel._slice_key_modifiers)
    except Exception:
        logger.debug("effect_browser: failed to bind slice key to context")


# ── 停止效果的辅助（模块级：上帝类禁止新增方法，见 CLAUDE.md 10.1）──────────
def _rebind_slice_key_after_restore(panel, state) -> None:
    """界面状态全部就位后，把状态里的切片键补挂到**当前上下文**并重解析标签。

    ★ 为什么必须在最后做：切片键的上下文键是 `preset@group@ch`（动态），而
      `load_state` 里切片键的恢复早于 `selected_id`/分组恢复 → 绑定落到旧上下文，
      随后 `_update_slice_key_label` 按新上下文重解析会把切片键清空（实报"调入后
      切片键丢失"）。此处重挂后 `_slice_key` 才稳定为状态里的值。
    """
    try:
        key = state.get("slice_key", None)
        if key is None:
            return
        want = int(key)
        mods = int(state.get("slice_key_modifiers", 0) or 0)
        ctx = panel._sync_manual_slice_context()
        if ctx:
            panel._slice_bindings_by_context[ctx] = (want, mods)
        panel._slice_key, panel._slice_key_modifiers = want, mods
        panel._legacy_slice_binding = (want, mods)
        panel._update_slice_key_label()
        # 上面重解析若仍把键清掉（异常上下文），直接落回状态值兜底。
        if panel._slice_key is None:
            panel._slice_key, panel._slice_key_modifiers = want, mods
    except Exception:
        logger.debug("effect_browser: failed to rebind slice key after restore",
                     exc_info=True)


def _drop_running_states(panel, keys) -> set:
    """弹出这些键的运行态；返回它们涉及的值类型集合（供表单复位）。"""
    stopped_ch = set()
    for key in keys:
        st = panel._external_states.pop(key, None)
        if isinstance(st, dict):
            stopped_ch.update(str(ch) for ch in (st.get("ch_types", []) or [])
                              if str(ch))
    return stopped_ch


def _mark_forms_stopped(panel, stopped_ch: set) -> None:
    """把相关通道表单标记为停止（无 stopped_ch = 全部表单）。"""
    for form in list(panel._channel_forms or []):
        ch = panel._channel_form_type(form)
        if not stopped_ch or ch in stopped_ch:
            form["playing"] = False
            form["virtual_t"] = 0.0
            form["last_real_t"] = 0.0


def _reset_transport_on_stop(panel) -> None:
    """全部效果都停了：复位播放态、停产定时器、清 effect 层（除非在切片里）。"""
    panel._external_state = None
    panel._is_playing = False
    panel._play_timer.stop()
    panel._update_transport_state()
    if panel._nm is not None and panel._slice_key is None:
        try:
            panel._nm.clear_layer("effect")
        except Exception:
            logger.debug("effect_browser: failed to clear effect layer on stop")


def _report_after_stop(panel) -> None:
    """停止后重报总线（失败只记日志，绝不打断停止流程）。"""
    try:
        panel.report_to_bus(force=True)
    except Exception:
        logger.debug("effect_browser: report to bus after stop failed", exc_info=True)
