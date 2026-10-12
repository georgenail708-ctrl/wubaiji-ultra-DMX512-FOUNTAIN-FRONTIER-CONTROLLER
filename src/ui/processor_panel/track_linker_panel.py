"""
DMXTimeline — TrackLinkerPanel  v1.0
轨道关联器 UI 面板

布局（三栏）：
  ┌──────────────────────────────────────────────────────────────┐
  │  [● 启用关联器]  [+ 新建规则]  [批量生成]  [清空]  状态标签   │
  ├──────────────┬──────────────────────────┬────────────────────┤
  │  设备组/轨道  │      规则列表             │   规则编辑器        │
  │  类型选择器   │  (每条规则一行，可启停)   │  (预设/参数/通道)   │
  └──────────────┴──────────────────────────┴────────────────────┘

左栏：设备组列表 + 轨道类型过滤
中栏：规则列表（可拖排序、启停、删除）
右栏：选中规则的详细编辑（预设选择、参数滑块、源/目标通道配置）
"""
from __future__ import annotations
from ..editor_workspace_regions import popup

import uuid
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGroupBox, QComboBox, QSlider, QCheckBox,
    QSplitter, QStackedWidget, QFrame, QListWidget, QListWidgetItem,
    QLineEdit, QSizePolicy, QSpinBox, QDoubleSpinBox, QDialog,
    QDialogButtonBox, QMessageBox, QMenu, QAbstractItemView, QToolButton,
    QInputDialog,
    QGridLayout, QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPainterPath

# ── D波标准件（widget_kit）─────────────────────────────────────────────────
from ...core.constants import CH_COLOR_ALIASES  # 常量单源(2026-09-12)
from ..widget_kit import (
    PanelSection, prop_row, NumSlider,
    apply_dialog_chrome, EmptyState, ROW_H, HEADER_H,
)

log = logging.getLogger(__name__)

#: 调度台用：记录本面板实例上次上报过的窗格身份键（id(panel) → set）。
#: 仅在"本面板窗格减少时删掉消失的键"时使用，避免误删其它面板/实例的窗格。

# ── 颜色常量（S1 令牌化：变量名保留，值全部重定向到中心主题令牌）──────────
from ..qascade_theme import (
    BG_ROOT, BG_EDITOR, BG_BOX, BG_PANEL, BG_PANEL_SUB,
    BG_BUTTON_HOVER, BRAND_ORANGE_SELECTED as SELECT_BLUE, ACTIVE_ORANGE,
    BORDER_NORMAL, SEP_LINE, POPUP_BORDER, TEXT_PRIMARY as TEXT_SELECTED_FG,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DISABLED,
    BRIGHT_SUCCESS, BRIGHT_ERROR, BRIGHT_INFO,
    STATUS_SUCCESS_INK, INFO_BLUE, BRAND_ORANGE_TEXT,
    RADIUS, LEGACY_LINKER_HOVER,
)
DARK_BG  = BG_ROOT        # #1D1D1D（原 #1e1e1e）
PANEL_BG = BG_EDITOR      # #303030 面板基底（原 #252526）
ACCENT   = BRAND_ORANGE_TEXT
GREEN    = STATUS_SUCCESS_INK
RED      = BRIGHT_ERROR   # #E36855 危险/删除文字色（原 #c47a7a）
BLUE     = INFO_BLUE
PURPLE   = "#9B6DD6"      # 紫 hue 保持（对齐 CATEGORY_COLORS uv）
BORDER   = BORDER_NORMAL  # #3D3D3D 控件描边（原 #3a3a4a）

# ── 通道类型标签 ──────────────────────────────────────────────────────────────
#: 通道显示名 —— **真源在 `src/ui/project_channels.py`**（2026-09-19 收敛）。
#: 关联器页与 effect / matrix 共用同一份清单（含功能通道真名），此处仅重导出，
#: 避免第三份实现再次漂移。
from ...ui.project_channels import (          # noqa: E402
    CH_LABELS,
    channel_item_label as _shared_item_label,
    collect_channel_items_plain as _shared_collect_plain,
    def_channel_keys as _def_channel_keys,
    ch_label as _shared_ch_label,
    label_map as _shared_label_map,
)


def _channel_label_map(items) -> dict:
    """[(标签, 键)] → {键: 标签}（**契约方向的唯一判定处**，见共用模块）。"""
    return _shared_label_map(items)


def _collect_project_channel_items(defs: list) -> list:
    """工程设备定义 → [(显示标签, 通道键)]（功能通道显示工程真名）。

    ★ 关联器用**裸键**版：本页每条通道引用都带 fixture_id，作用域由设备决定，
      键只作引擎匹配用（引擎必须收裸键，收带作用域的值会查不到通道）。
      effect 侧才需要按类型分开（见 `project_channels.collect_channel_items`）。
    """
    return _shared_collect_plain(defs)


def _ch_label(key) -> str:
    """单个通道键 → 显示名（真源在共用模块，此处保持既有调用点不变）。"""
    return _shared_ch_label(key)


def _fill_combo(combo, keys, items=None) -> None:
    """按 keys 重建下拉项，尽量保留当前选中项（避免用户已选项跳位）。

    items：[(标签, 键), ...]（由 `_collect_project_channel_items` 生成，功能通道
    带工程真实名）。缺省时回退 `_ch_label`。
    """
    label_of = _channel_label_map(items)
    cur = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for key in keys:
        combo.addItem(label_of.get(key) or _ch_label(key), key)
    if cur is not None:
        idx = combo.findData(cur)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.blockSignals(False)


def _auto_pick_working_pair(dlg, keys) -> None:
    """当前 (src,dst) 出 0 条时，自动切到首个能出规则的通道对。

    ★ 2026-09-17 用户实报（同批）：默认 dimmer→R 对「单通道设备」（单通道水泵
      pump / 雾森 valve）永远不匹配 —— 它们只有一个通道，需 src==dst —— 用户
      一打开就看见"预计生成：0 条规则"。只在**确实 0 条**时回退，不覆盖用户
      已选的有效组合。
    """
    try:
        if dlg._estimate_rule_count() != 0:
            return
        pairs = [(k, k) for k in keys] + [(a, b) for a in keys for b in keys if a != b]
        for a, b in pairs:
            ia, ib = dlg._src_combo.findData(a), dlg._dst_combo.findData(b)
            if ia < 0 or ib < 0:
                continue
            for combo, idx in ((dlg._src_combo, ia), (dlg._dst_combo, ib)):
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
            if dlg._estimate_rule_count() > 0:
                return
    except Exception:
        pass


def _defs_of(scoped, defs) -> list:
    """scoped 设备引用到的 FixtureDefinition 列表（按首见顺序去重）。"""
    out: list = []
    seen: set = set()
    for f in (scoped or ()):
        did = str(getattr(f, "def_id", "") or "")
        if not did or did in seen:
            continue
        seen.add(did)
        fd = (defs or {}).get(did)
        if fd is not None:
            out.append(fd)
    return out


def _collect_project_channel_keys(defs: list) -> list:
    """所选作用域内**实际存在**的通道键（按首次出现顺序，去重）。

    ★ 2026-09-17 用户实报修复（通用规则，适用所有项目）：关联器的通道选择器
      原先只列硬编码的 12 项 → 工程真实通道若不在此表内（最典型是功能通道
      function_N）永远选不到 → 该设备"始终显示可创建 0 条规则"。
      此处从 def.channels 收集真实通道，让选择器覆盖工程全量通道。
    """
    out: list = []
    seen: set = set()
    for fd in (defs or ()):
        for ch in (getattr(fd, "channels", None) or ()):
            key = _channel_type_key(ch)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _group_rules_by_type(rules: list, defs: dict) -> dict:
    """把规则按**设备类型名**归并（用户定案：一类设备一个组条目）。

    类型名取规则涉及设备的 def.name（生成时由引擎写入规则名的前缀），
    取不到时归入「未知类型」，保证绝不丢规则。
    """
    out: dict = {}
    for r in (rules or ()):
        # 引擎生成时已写入源设备 def.name（最可靠）
        tname = str(getattr(r, "type_name", "") or "").strip()
        if not tname:
            nm = str(getattr(r, "name", "") or "").strip()
            tname = nm.split(" ")[0] if nm else ""
        out.setdefault(tname or "未知类型", []).append(r)
    return out


def _batch_status_text(total: int, skipped: int, overlap: int) -> str:
    """批量生成状态栏文案：生成数 + 跳过重复数 + 目标通道重叠提示。"""
    if not total:
        return "已生成 0 条规则"
    txt = f"已生成 {total} 条规则（按设备类型聚合）"
    if skipped:
        txt += f"，跳过 {skipped} 条重复映射"
    if overlap:
        txt += f"；提示：有 {overlap} 个目标通道与组内已有预设重叠（低值覆盖取最严）"
    return txt


def _store_batch_by_type(engine, rules: list, defs: dict) -> tuple:
    """把生成结果**按类型组**入库；返回 (首个组 id, 跳过数, 重叠通道数)。"""
    if not rules:
        engine.add_rules([])
        return "", 0, 0
    created_gid, skipped, overlap = "", 0, set()
    for _tn, _rs in _group_rules_by_type(rules, defs).items():
        overlap |= engine.overlapping_dst_in_group(_tn, _rs)
        _gid = engine.add_rules_to_type_group(_rs, _tn)
        skipped += len(getattr(engine, "last_group_add_skipped", ()) or ())
        if _gid and not created_gid:
            created_gid = _gid
    return created_gid, skipped, overlap


def _scope_channel_presence(scope_sets: list, defs: dict,
                            src_key: str, dst_key: str) -> tuple:
    """扫描范围内设备通道：返回 (是否有源通道, 是否有目标通道)。"""
    has_src = has_dst = False
    for inst in [f for s in scope_sets for f in s]:
        fd = defs.get(getattr(inst, "def_id", ""))
        if fd is None:
            continue
        for ch in getattr(fd, "channels", []):
            ck = _channel_type_key(ch)
            has_src = has_src or ck == src_key
            has_dst = has_dst or ck == dst_key
    return has_src, has_dst


def _no_rules_hint(result: dict, scope_sets: list, defs: dict,
                   cancelled: bool) -> str:
    """零规则时的状态提示：区分"用户取消"与"范围内缺源/目标通道"。"""
    if cancelled:
        return "已取消：未生成规则"
    # 入围组内源或目标通道整体缺失（如纯电机组生成 电机→泵 无泵可映射）：
    # 明确提示，避免用户以为"生成了但没生效"。
    # ★ 2026-09-18：功能通道也走 _ch_label（基表 → constants → 键名），别只查
    #   基表 —— 否则提示里会出现 "有 function_1 但无 pump" 这种用户看不懂的键名。
    src_label = _ch_label(str(result.get("src_type") or ""))
    dst_label = _ch_label(str(result.get("dst_type") or ""))
    has_src, has_dst = _scope_channel_presence(
        scope_sets, defs, _channel_type_key(result.get("src_type", "")),
        _channel_type_key(result.get("dst_type", "")))
    if has_src and not has_dst:
        return f"未生成规则：所选范围有 {src_label} 但无 {dst_label} 通道"
    if has_dst and not has_src:
        return f"未生成规则：所选范围有 {dst_label} 但无 {src_label} 通道"
    return "未生成规则：所选范围无对应通道"


def _preset_default_params(pdef) -> dict:
    """预设的默认参数（用于叠加新预设时给出合理初值）。"""
    out = {}
    for pd in (getattr(pdef, "params_def", None) or ()):
        key = pd.get("key")
        if key:
            out[key] = float(pd.get("default", 0.0)) * float(pd.get("scale", 1.0))
    return out


def _stack_status_text(preset_name: str, added: int, skipped: int,
                       total: int) -> str:
    """叠加预设后的状态栏文案。"""
    txt = f"已叠加「{preset_name}」：新增 {added} 条映射（本组共 {total} 条）"
    if skipped:
        txt += f"，跳过 {skipped} 条重复"
    return txt


def _append_group_dst_refs(pm, rule, group_keys) -> int:
    """把**源设备上实际存在的整组通道**补进规则目标（返回新增条数）。

    用于整组目标预设（电机→颜色 = RGBW / 泵→灯跟踪 = 全部灯通道）的手动新增：
    用户点一次"添加目标通道"即补齐整组，不必逐个挑通道。
    去重：已在目标里的同一 (fixture, universe, addr) 不重复添加。
    """
    from ...processing.track_linker_engine import ChannelRef
    insts = {str(getattr(f, "id", "")): f for f in (getattr(pm, "fixtures", None) or [])}
    defs = {str(getattr(d, "id", "")): d for d in (getattr(pm, "fixture_defs", None) or [])}
    have = {(str(r.fixture_id), int(r.universe), int(r.dmx_addr))
            for r in rule.dst_channels}
    added = 0
    for src_ref in list(rule.src_channels):
        fid = str(getattr(src_ref, "fixture_id", "") or "")
        added += _append_group_refs_for_fixture({
            "cls_ref": ChannelRef, "rule": rule, "inst": insts.get(fid),
            "defs": defs, "group_keys": group_keys, "have": have})
    return added


def _fixture_addr_ctx(inst) -> tuple:
    """(fixture_id, 起始地址0基, universe)，异常时给安全默认。"""
    fid = str(getattr(inst, "id", "") or "")
    try:
        start = int(getattr(inst, "start_channel", 1) or 1) - 1
    except Exception:
        start = 0
    return fid, start, int(getattr(inst, "universe", 1) or 1)


def _append_group_refs_for_fixture(ctx) -> int:
    """给单台设备补它实际存在的组通道（返回新增条数，ctx 内 have 去重）。

    ctx = {"cls_ref","rule","defs","inst","group_keys","have"} —— 用一个字典
    收拢必要上下文，避免长参数列表（门禁：位置参数 ≤5）。
    """
    inst, rule = ctx["inst"], ctx["rule"]
    fd = ctx["defs"].get(str(getattr(inst, "def_id", ""))) if inst else None
    if fd is None:
        return 0
    fid, start, uni = _fixture_addr_ctx(inst)
    have, group_keys = ctx["have"], ctx["group_keys"]
    added = 0
    for i, ch in enumerate(getattr(fd, "channels", []) or []):
        key, addr = _channel_type_key(ch), start + i
        if key not in group_keys or not (0 <= addr < 512)                 or (fid, uni, addr) in have:
            continue
        rule.dst_channels.append(ctx["cls_ref"](
            fixture_id=fid, ch_type=_canon_ch_type_for_target(key),
            universe=uni, dmx_addr=addr))
        have.add((fid, uni, addr))
        added += 1
    return added


def _canon_ch_type_for_target(key: str) -> str:
    """目标通道类型归一（颜色大写 R/G/B/W，其余原样）。"""
    k = str(key or "")
    return k.upper() if k in ("r", "g", "b", "w") else k


def _picker_channel_label(pm, inst, ch_key: str, ch_type: str) -> str:
    """通道选择对话框单行的通道名：基表 → 工程真名 → 裸类型名。"""
    return (CH_LABELS.get(ch_type)
            or CH_LABELS.get(ch_key)
            or _fixture_channel_name(pm, getattr(inst, "id", ""), ch_key)
            or ch_type)


def _pm_fixture_def(pm, fixture_id):
    """按设备实例 id 找它的 FixtureDefinition（找不到返回 None）。"""
    inst = next((f for f in (getattr(pm, "fixtures", None) or [])
                 if str(getattr(f, "id", "")) == str(fixture_id)), None)
    if inst is None:
        return None
    return next((d for d in (getattr(pm, "fixture_defs", None) or [])
                 if str(getattr(d, "id", "")) == str(getattr(inst, "def_id", ""))),
                None)


def _fixture_channel_name(pm, fixture_id, ch_key: str) -> str:
    """按**设备实例所属类型**查功能通道的工程真名（查不到返回 ""）。

    ★ 2026-09-18：功能通道显示的统一入口。与 `_collect_project_channel_items`
      （批量生成下拉）同源，但这里面向"单台设备的某个通道"（规则编辑器列表、
      通道选择对话框），必须先由 fixture_id 找到 def_id 再查
      `function_channel_names`。
    """
    key = str(ch_key or "")
    if not key.startswith("function_"):
        return ""
    try:
        fd = _pm_fixture_def(pm, fixture_id)
        if fd is None:
            return ""
        names = getattr(fd, "function_channel_names", None) or {}
        return str(names.get(key, "") or "").strip()
    except Exception:
        return ""


def _channel_type_key(ch_type) -> str:
    raw = getattr(ch_type, "value", ch_type)
    raw = getattr(raw, "ch_type", raw)
    raw = getattr(raw, "type", raw)
    key = str(raw or "").strip().lower()
    return CH_COLOR_ALIASES.get(key, key)

# 轨道类型分组（用于左栏过滤）
TRACK_TYPE_GROUPS = [
    ("全部",     []),
    ("灯光",     ["dimmer", "R", "G", "B", "W"]),
    ("电机",     ["motor_x", "motor_y", "motor_z", "motor_xy"]),
    ("泵",       ["pump"]),
    ("阀门",     ["valve"]),
]

# ─────────────────────────────────────────────────────────────────────────────
#  数据访问助手
# ─────────────────────────────────────────────────────────────────────────────
# 旧版本面板通过 pm.fixture_instances / pm.fixture_definitions 读取设备，
# 但当前 ProjectManager 暴露的实际属性是 pm.fixtures / pm.fixture_defs
# （Project 模型亦同：project.fixtures / project.fixture_defs）。
# 命名不匹配导致通道选择对话框 / 批量生成对话框始终拿到空列表，
# 进而无法选择"已添加设备"的对应通道，关联规则也无法实时输出。
# 此助手统一所有命名变体，作为唯一的数据访问入口。

def _build_logical_devices(pm) -> List[dict]:
    """从 ProjectManager / Project 读逻辑设备 dict 列表（包单元分组用）。"""
    recs = None
    for src in (pm, getattr(pm, "project", None)):
        if src is None:
            continue
        recs = getattr(src, "logical_devices", None)
        if recs is not None:
            break
    out: List[dict] = []
    for r in (recs or []):
        if isinstance(r, dict):
            out.append(r)
        elif hasattr(r, "to_dict"):
            try:
                out.append(r.to_dict())
            except Exception:
                continue
    return out


def _resolve_pm_fixtures(pm) -> Tuple[list, dict]:
    """从 ProjectManager / Project 解析 (设备实例列表, {def_id: 设备定义})。

    兼容以下属性命名（按优先级）：
        pm.fixtures            / pm.fixture_defs            （ProjectManager 当前 API）
        pm.fixture_instances   / pm.fixture_definitions     （旧版/外部 API）
        pm.project.fixtures    / pm.project.fixture_defs    （直接访问 Project）
        pm.project.fixture_instances / pm.project.fixture_definitions （兜底）

    任一路径取到非空集合即返回；全部为空时返回 ([], {})。
    """
    if pm is None:
        return [], {}

    def _try_attrs(obj, names):
        for n in names:
            try:
                v = getattr(obj, n, None)
            except Exception:
                v = None
            if v:
                try:
                    return list(v)
                except Exception:
                    return []
        return None

    insts = _try_attrs(pm, ('fixtures', 'fixture_instances'))
    if insts is None:
        proj = getattr(pm, 'project', None)
        if proj is not None:
            insts = _try_attrs(proj, ('fixtures', 'fixture_instances'))
    if insts is None:
        insts = []

    defs_list = _try_attrs(pm, ('fixture_defs', 'fixture_definitions'))
    if defs_list is None:
        proj = getattr(pm, 'project', None)
        if proj is not None:
            defs_list = _try_attrs(proj, ('fixture_defs', 'fixture_definitions'))
    if defs_list is None:
        defs_list = []

    defs: dict = {}
    for d in defs_list:
        d_id = getattr(d, 'id', None)
        if d_id:
            defs[d_id] = d
    return insts, defs


# ── CSS 辅助（S1：工厂签名不动，输出全面重定向到 主题令牌）───────────────

def _norm6(h: str) -> str:
    """3位hex归一化为6位（"#aaa"→"#aaaaaa"），拼 alpha 后缀前必须调用，否则产生无效5位色。"""
    if isinstance(h, str) and len(h) == 4 and h.startswith("#"):
        return "#" + "".join(ch * 2 for ch in h[1:])
    return h


def _legacy_hover(hover: str) -> str:
    """旧主题 hover 色迁移：未改的旧默认值 → 主题按钮 hover 面。"""
    return BG_BUTTON_HOVER if hover == LEGACY_LINKER_HOVER else hover


def _css_btn(bg=PANEL_BG, fg=TEXT_SECONDARY, hover=LEGACY_LINKER_HOVER, border=BORDER,
             radius=3, font_size=11, padding="2px 10px"):
    """语义按钮（红删/绿确认等保留文字色语义）；通用外观对齐全局四态。
    禁止再拼 #RRGGBBaa 形式（Qt 里是 #AARRGGBB 位序）。"""
    if fg == "#aaa":   # 旧默认弱文字 → 主题弱文字色
        fg = TEXT_SECONDARY
    hover = _legacy_hover(hover)
    if radius != RADIUS:
        radius = RADIUS   # 统一圆角
    return (
        f"QPushButton{{background:{bg};color:{fg};"
        f"border:1px solid {border};border-radius:{RADIUS}px;"
        f"font-size:{font_size}px;padding:{padding};min-height:18px;}}"
        f"QPushButton:hover{{background:{hover};border-color:#5C5C5C;}}"
        f"QPushButton:pressed{{background:{SELECT_BLUE};"
        f"border-color:{SELECT_BLUE};color:{TEXT_SELECTED_FG};}}"
        f"QPushButton:disabled{{background:{PANEL_BG};"
        f"color:{TEXT_DISABLED};border-color:{POPUP_BORDER};}}"
        f"QPushButton:checked{{background:{SELECT_BLUE};"
        f"border-color:{SELECT_BLUE};color:{TEXT_SELECTED_FG};}}"
    )


def _mk_btn(text: str, fg: str, icon_key: str = "", bg: str = PANEL_BG,
            hover: str = "#656565", border: str = BORDER, height: int = 26) -> QPushButton:
    """带图标的按钮工厂。"""
    from PyQt6.QtCore import QSize
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setStyleSheet(_css_btn(bg=bg, fg=fg, hover=hover, border=border))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_key:
        try:
            from ..main_icon_library import make_main_icon
            btn.setIcon(make_main_icon(icon_key, 16))
            btn.setIconSize(QSize(14, 14))
        except Exception:
            pass
    return btn

def _mk_toolbar_btn(text: str, icon_key: str = "", *,
                    danger: bool = False, height: int = 26) -> QPushButton:
    """关联器顶部工具行按钮（极简：灰底 + 主文字色，语义色只留极少数）。

    ★ 2026-09-18 用户实报（问题5："新建规则 批量生成 清空全部的字体颜色等"难看）：
      旧实现给四个按钮各配一个高饱和文字色（橙 #FFA028 / 蓝 #6FA8DC / 红 #E36855 /
      绿 #56B65B），四个彩色字挤在一行深底上既花又互相争视线，不符合扁平
      克制配色。改为统一 **BG_BUTTON 灰底 + TEXT_PRIMARY 白字**（与全局
      `QPushButton` 基线一致），语义只靠图标与"是否危险"两处表达：
        · 危险操作（清空全部）→ 低饱和红字；
        · 开关态（启用关联器）→ :checked 走 SELECT_BLUE（开启态）；
        · 其余一律中性。
    """
    from PyQt6.QtCore import QSize
    from ..qascade_theme import BG_BUTTON
    fg = RED if danger else TEXT_PRIMARY
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setStyleSheet(
        f"QPushButton{{background:{BG_BUTTON};color:{fg};"
        f"border:1px solid {BORDER};border-radius:{RADIUS}px;"
        f"font-size:11px;padding:3px 10px;min-height:18px;}}"
        f"QPushButton:hover{{background:{BG_BUTTON_HOVER};"
        f"border-color:#6A6A6A;}}"
        f"QPushButton:pressed{{background:{SELECT_BLUE};"
        f"border-color:{SELECT_BLUE};color:{TEXT_SELECTED_FG};}}"
        f"QPushButton:checked{{background:{SELECT_BLUE};"
        f"border-color:{SELECT_BLUE};color:{TEXT_SELECTED_FG};}}"
        f"QPushButton:disabled{{background:{PANEL_BG};"
        f"color:{TEXT_DISABLED};border-color:{POPUP_BORDER};}}")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_key:
        try:
            from ..main_icon_library import make_main_icon
            btn.setIcon(make_main_icon(icon_key, 16))
            btn.setIconSize(QSize(14, 14))
        except Exception:
            pass
    return btn


def _css_group(title_color=ACCENT):
    """分组框仅保留标题分色特化；边框/底色走全局主题（QGroupBox 分组即色相语义保留）。"""
    return (
        f"QGroupBox::title{{subcontrol-origin:margin;"
        f"subcontrol-position:top left;padding:0 4px;color:{title_color};}}"
    )

def _css_combo():
    # S1 重构：删除与全局主题重复的通用 QComboBox 外观，统一走 GLOBAL_QSS。
    return ""

def _css_slider():
    # S1 重构：删除与全局主题重复的通用 QSlider 外观，统一走 GLOBAL_QSS。
    return ""

def _css_list():
    # S1 重构：删除与全局主题重复的通用 QListWidget 外观，统一走 GLOBAL_QSS。
    return ""

def _css_lineedit():
    # S1 重构：删除与全局主题重复的通用 QLineEdit 外观，统一走 GLOBAL_QSS。
    return ""

def _css_spinbox():
    # S1 重构：删除与全局主题重复的通用 QSpinBox 外观，统一走 GLOBAL_QSS。
    return ""

def _css_doublespinbox():
    # S1 重构：删除与全局主题重复的通用 QDoubleSpinBox 外观，统一走 GLOBAL_QSS。
    return ""

def _css_check():
    # S1 重构：删除与全局主题重复的通用 QCheckBox 外观，统一走 GLOBAL_QSS。
    return ""


class LinkParamSlider(NumSlider):
    """D波：关联预设参数 数值滑杆（值绘在滑槽上 + 单位后缀）。

    滑杆域保持引擎 params_def 的原始整数区间（min..max），显示值 =
    原始值 × scale —— 与旧 ``slider.value() * scale`` 数值契约完全同源，
    仅呈现层换形态（浮点/单位直接绘在滑槽上）。
    """

    def __init__(self, pd: dict, parent=None):
        self._pd_scale = float(pd.get("scale", 1.0) or 1.0)
        self._pd_unit = str(pd.get("unit", "") or "")
        dec = 0 if abs(self._pd_scale - round(self._pd_scale)) < 1e-9 else 2
        super().__init__(
            minimum=float(pd["min"]), maximum=float(pd["max"]),
            value=float(pd.get("default", pd["min"])),
            decimals=dec, parent=parent)

    def scaled_value(self) -> float:
        """原始 UI 值 × scale（写入引擎的最终参数数值）。"""
        return self.float_value() * self._pd_scale

    def _fmt(self) -> str:
        v = self.scaled_value()
        if self._pd_unit:
            return f"{v:.2f}{self._pd_unit}"
        if self._pd_scale == int(self._pd_scale):
            return f"{v:.0f}"
        return f"{v:.2f}"

# ─────────────────────────────────────────────────────────────────────────────
#  曲线预览控件（显示当前预设的输入→输出曲线）
# ─────────────────────────────────────────────────────────────────────────────

class LinkCurvePreview(QFrame):
    """实时绘制关联预设的 src→dst 曲线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)
        self.setMinimumWidth(160)
        self.setStyleSheet(
            f"LinkCurvePreview{{background:{BG_ROOT};"
            f"border:1px solid {BORDER};border-radius:{RADIUS}px;}}")
        self._fn = None
        self._params: dict = {}

    def set_fn(self, fn, params: dict):
        self._fn = fn
        self._params = params
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        pl, pr, pt, pb = 28, 8, 8, 18
        dw = W - pl - pr
        dh = H - pt - pb

        # 网格（D波：rgba(255,255,255,13) 细网格，与曲线编辑器画布同语言）
        p.setPen(QPen(QColor(BORDER_NORMAL), 1))
        for i in range(5):
            y = pt + int(dh * i / 4)
            p.drawLine(pl, y, pl + dw, y)
        for i in range(5):
            x = pl + int(dw * i / 4)
            p.drawLine(x, pt, x, pt + dh)

        # 轴标签
        p.setPen(QPen(QColor(TEXT_DISABLED), 1))
        font = QFont("Consolas")
        font.setPixelSize(10)
        p.setFont(font)
        for i in range(5):
            v = int(255 * i / 4)
            y = pt + int(dh * (4 - i) / 4)
            p.drawText(0, y + 4, pl - 2, 12, Qt.AlignmentFlag.AlignRight, str(v))
        p.drawText(pl, pt + dh + 2, dw, 14, Qt.AlignmentFlag.AlignCenter, "src (0→255)")

        if self._fn is None:
            p.setPen(QPen(QColor(TEXT_DISABLED), 1))
            p.drawText(pl, pt, dw, dh, Qt.AlignmentFlag.AlignCenter, "无预设")
            return

        # 曲线
        import math
        path = QPainterPath()
        first = True
        for xi in range(dw + 1):
            src_val = xi / dw * 255.0
            try:
                dst_val = float(self._fn(src_val, self._params))
                dst_val = max(0.0, min(255.0, dst_val))
            except Exception:
                dst_val = src_val

            x = pl + xi
            y = pt + int(dh * (1.0 - dst_val / 255.0))
            y = max(pt, min(pt + dh, y))

            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)

        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawPath(path)

        # 对角参考线（直通）
        p.setPen(QPen(QColor(BORDER_NORMAL), 1, Qt.PenStyle.DashLine))
        p.drawLine(pl, pt + dh, pl + dw, pt)


# ─────────────────────────────────────────────────────────────────────────────
#  规则列表项控件
# ─────────────────────────────────────────────────────────────────────────────

class RuleListItem(QWidget):
    """规则列表中的单行控件（D波解剖：24px 行 · select_blue 白字选中 · 2px 左竖条）。

    行版式：[复选] 规则名 …(stretch)… 元数据(预设·模式, Consolas 右对齐) [▲][▼][✕]
    """

    toggled  = pyqtSignal(str, bool)   # rule_id, enabled
    selected = pyqtSignal(str)          # rule_id
    deleted  = pyqtSignal(str)          # rule_id
    moved_up = pyqtSignal(str)
    moved_dn = pyqtSignal(str)

    def __init__(self, rule, parent=None):
        super().__init__(parent)
        self._rule = rule
        self._selected = False
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # audit-B 修复：plain-QWidget 子类需 WA_StyledBackground 才能渲染 QSS 背景/hover
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build()
        self._update_style()

    def _build(self):
        """规则行（紧凑列表风）：勾选 + 名称 + 预设徽标 + 通道指向 + 操作。

        ★ 2026-09-18 重设计：旧行把「预设·低值覆盖」和 `[组名]` 用 11px 等宽
          字堆在右侧，既挤又难读（用户："很多老旧的控件尤其丑"）。现在改为：
            左：勾选 + 规则名
            中：预设徽标（灰底小胶囊，一眼看出这条属于哪套预设）
            右：源→目标 通道指向（等宽，但只在需要时显示）
            操作按钮默认隐藏，悬停该行才出现（列表同款）
        """
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(8)

        self._chk = QCheckBox()
        self._chk.setChecked(self._rule.enabled)
        self._chk.setToolTip("启用/停用该关联规则")
        self._chk.setFixedSize(16, 16)
        self._chk.toggled.connect(lambda v: self.toggled.emit(self._rule.id, v))
        lay.addWidget(self._chk)

        self._name_lbl = QLabel(self._rule.name)
        self._name_lbl.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:11px;background:transparent;")
        lay.addWidget(self._name_lbl, 1)

        # 预设徽标（胶囊）
        from ...processing.track_linker_engine import PRESET_BY_ID
        pdef = PRESET_BY_ID.get(self._rule.preset_id)
        preset_name = pdef.name if pdef is not None else str(self._rule.preset_id)
        self._badge = QLabel(preset_name)
        self._badge.setStyleSheet(
            f"background:{BG_PANEL_SUB};color:{TEXT_SECONDARY};"
            f"border:1px solid rgba(255,255,255,22);"
            f"border-radius:7px;padding:1px 7px;font-size:10px;")
        self._badge.setToolTip(f"预设：{preset_name}（低值覆盖）")
        lay.addWidget(self._badge)

        # 通道指向（源→目标 数）
        n_src, n_dst = len(self._rule.src_channels), len(self._rule.dst_channels)
        self._route_lbl = QLabel(f"{n_src}→{n_dst}")
        self._route_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:10px;background:transparent;")
        self._route_lbl.setToolTip(f"{n_src} 个源通道 → {n_dst} 个目标通道")
        lay.addWidget(self._route_lbl)

        # 操作按钮组：默认隐藏，悬停显示
        self._btns = QWidget()
        bl = QHBoxLayout(self._btns)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        btn_style = _css_btn(font_size=10, padding="0px 3px", border="transparent")
        for text, tip, sig in (("▲", "上移规则", self.moved_up),
                               ("▼", "下移规则", self.moved_dn)):
            b = QPushButton(text)
            b.setFixedSize(18, 18)
            b.setStyleSheet(btn_style)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, s=sig: s.emit(self._rule.id))
            bl.addWidget(b)
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(_css_btn(fg=RED, font_size=10,
                                       padding="0px 3px", border="transparent"))
        del_btn.setToolTip("删除规则")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.deleted.emit(self._rule.id))
        bl.addWidget(del_btn)
        # ★ 不隐藏按钮组：用"很淡的静音色"代替（悬停由 QSS 提亮）。
        #   曾用 enterEvent/leaveEvent 切换 visible —— 行被 deleteLater 拆掉时
        #   回调会碰已销毁的 C++ 控件 → **进程级 abort（rc=127，无 traceback）**。
        lay.addWidget(self._btns)

    def set_selected(self, v: bool):
        self._selected = v
        self._update_style()

    def _update_style(self):
        # D波解剖：选中 = select_blue 实底白字 + 2px 强调橙左竖条；
        # 未选 = 平铺透明行，hover bg_panel；不再用 styleSheet() 追加拼接。
        if self._selected:
            self.setStyleSheet(
                f"RuleListItem{{background:{SELECT_BLUE};"
                f"border-left:2px solid {ACCENT};}}")
            self._name_lbl.setStyleSheet(
                f"color:{TEXT_SELECTED_FG};font-size:11px;background:transparent;")
            self._badge.setStyleSheet(
                f"background:{BG_PANEL};color:{TEXT_PRIMARY};"
                f"border:1px solid {BORDER};"
                "border-radius:7px;padding:1px 7px;font-size:10px;")
            self._route_lbl.setStyleSheet(
                f"color:{TEXT_SECONDARY};font-size:10px;background:transparent;")
        else:
            self.setStyleSheet(
                f"RuleListItem{{background:transparent;"
                f"border-left:2px solid transparent;}}"
                f"RuleListItem:hover{{background:{BG_PANEL_SUB};}}")
            self._name_lbl.setStyleSheet(
                f"color:{TEXT_PRIMARY};font-size:11px;background:transparent;")
            self._badge.setStyleSheet(
                f"background:{BG_PANEL_SUB};color:{TEXT_SECONDARY};"
                f"border:1px solid rgba(255,255,255,22);"
                f"border-radius:7px;padding:1px 7px;font-size:10px;")
            self._route_lbl.setStyleSheet(
                f"color:{TEXT_SECONDARY};font-size:10px;background:transparent;")

    def mousePressEvent(self, event):
        self.selected.emit(self._rule.id)
        super().mousePressEvent(event)

    def refresh(self, rule):
        self._rule = rule
        self._chk.setChecked(rule.enabled)
        self._name_lbl.setText(rule.name)
        from ...processing.track_linker_engine import PRESET_BY_ID
        pdef = PRESET_BY_ID.get(rule.preset_id)
        preset_name = pdef.name if pdef is not None else str(rule.preset_id)
        self._badge.setText(preset_name)
        self._badge.setToolTip(f"预设：{preset_name}（低值覆盖）")
        n_src, n_dst = len(rule.src_channels), len(rule.dst_channels)
        self._route_lbl.setText(f"{n_src}→{n_dst}")
        self._route_lbl.setToolTip(f"{n_src} 个源通道 → {n_dst} 个目标通道")
        self._update_style()


# ─────────────────────────────────────────────────────────────────────────────
#  组条目控件（规则列表分组头部）
# ─────────────────────────────────────────────────────────────────────────────
# 展示层级：每个设备组 = 一个组条目（像 programmer 一台设备一行），
# 组内通道映射（规则）收纳在条目内折叠/展开，不拆成一条条独立平铺。

UNGROUPED_FOLDER_ID = "__ungrouped__"   # 未分组虚拟条目 id（手动创建规则归此）

class FolderHeaderItem(QWidget):
    """规则列表中的组条目头部行：折叠/展开、整体选中、重命名、删除。"""

    toggled          = pyqtSignal(str, bool)   # folder_id, expanded
    selected         = pyqtSignal(str)          # folder_id
    deleted          = pyqtSignal(str)          # folder_id
    rename_requested = pyqtSignal(str)          # folder_id

    def __init__(self, folder_id: str, name: str, count: int,
                 expanded: bool, mode: str = "folder", parent=None):
        super().__init__(parent)
        self._folder_id = folder_id
        self._mode = mode          # "folder"（真实组）| "ungrouped"（未分组虚拟组）
        self._selected = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # audit-B 修复：plain-QWidget 子类需 WA_StyledBackground 才能渲染 QSS 背景/hover
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build(name, count, expanded)
        self._update_style()

    def _build(self, name: str, count: int, expanded: bool):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(6)

        # 折叠/展开箭头
        self._arrow_btn = QToolButton()
        self._arrow_btn.setText("▾" if expanded else "▸")
        self._arrow_btn.setFixedSize(16, 22)
        self._arrow_btn.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;"
            f"color:{ACCENT};font-size:11px;padding:0;}}")
        self._arrow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._arrow_btn.setToolTip("展开/收起组内映射")
        self._arrow_btn.clicked.connect(self._on_arrow_clicked)
        lay.addWidget(self._arrow_btn)

        # 组名（加粗）
        self._name_lbl = QLabel(name)
        font = self._name_lbl.font()
        font.setBold(True)
        self._name_lbl.setFont(font)
        self._name_lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;background:transparent;")
        lay.addWidget(self._name_lbl)

        lay.addStretch()

        # 映射数徽标（胶囊，比裸等宽文字清楚）
        self._count_lbl = QLabel(f"{count} 条映射")
        self._count_lbl.setStyleSheet(
            f"background:{BG_PANEL_SUB};color:{TEXT_SECONDARY};"
            f"border:1px solid rgba(255,255,255,22);"
            f"border-radius:7px;padding:1px 7px;font-size:10px;")
        lay.addWidget(self._count_lbl)

        # 按钮组常驻（同规则行：不切 visible，防拆行时碰已销毁控件）
        self._btns = QWidget()
        bl = QHBoxLayout(self._btns)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        if self._mode == "folder":
            btn_style = _css_btn(font_size=10, padding="0px 3px", border="transparent")
            rename_btn = QPushButton("✎")
            rename_btn.setFixedSize(18, 18)
            rename_btn.setStyleSheet(btn_style)
            rename_btn.setToolTip("重命名组条目")
            rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rename_btn.clicked.connect(
                lambda: self.rename_requested.emit(self._folder_id))
            bl.addWidget(rename_btn)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(18, 18)
            del_btn.setStyleSheet(_css_btn(fg=RED, font_size=10,
                                           padding="0px 3px", border="transparent"))
            del_btn.setToolTip("删除组条目（含组内全部映射）")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda: self.deleted.emit(self._folder_id))
            bl.addWidget(del_btn)
        # 同规则行：不切 visible（防拆行时碰已销毁控件），只用静音配色
        lay.addWidget(self._btns)

    @staticmethod
    def _meta_qss(color: str | None = None) -> str:
        c = color or TEXT_SECONDARY
        return (f"color:{c};font-size:11px;"
                "font-family:'Consolas','DejaVu Sans Mono',monospace;"
                "background:transparent;")

    def _on_arrow_clicked(self):
        new_expanded = self._arrow_btn.text() != "▾"   # 当前 ▸（折叠）→ 展开
        self._arrow_btn.setText("▾" if new_expanded else "▸")
        self.toggled.emit(self._folder_id, new_expanded)

    def set_selected(self, v: bool):
        self._selected = v
        self._update_style()

    def _update_style(self):
        # D波解剖：与规则行同一选中语言（select_blue 白字 + 2px 橙左竖条），
        # 组条目底色未选时用 BG_PANEL_SUB 与行区分；hover bg_panel。
        if self._selected:
            self.setStyleSheet(
                f"FolderHeaderItem{{background:{SELECT_BLUE};"
                f"border-left:2px solid {ACCENT};}}")
            self._name_lbl.setStyleSheet(
                f"color:{TEXT_SELECTED_FG};font-size:11px;background:transparent;")
            self._count_lbl.setStyleSheet(self._meta_qss(TEXT_SECONDARY))
            self._arrow_btn.setStyleSheet(
                f"QToolButton{{background:transparent;border:none;"
                f"color:{TEXT_SELECTED_FG};font-size:11px;padding:0;}}")
        else:
            self.setStyleSheet(
                f"FolderHeaderItem{{background:{BG_PANEL_SUB};"
                f"border-left:2px solid transparent;}}"
                f"FolderHeaderItem:hover{{background:{BG_PANEL};}}")
            self._name_lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;background:transparent;")
            self._count_lbl.setStyleSheet(self._meta_qss())
            self._arrow_btn.setStyleSheet(
                f"QToolButton{{background:transparent;border:none;"
                f"color:{ACCENT};font-size:11px;padding:0;}}")

    def mousePressEvent(self, event):
        self.selected.emit(self._folder_id)
        super().mousePressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  通道选择对话框
# ─────────────────────────────────────────────────────────────────────────────

class ChannelPickerDialog(QDialog):
    """选择设备实例的通道。"""

    def __init__(self, pm, ch_type_hint: str = "", title: str = "选择通道", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(480, 400)
        # S1 重构：删除与全局主题重复的 QDialog 背景样式（GLOBAL_QSS 已覆盖）。
        self._pm = pm
        self._ch_type_hint = ch_type_hint
        self._allow_virtual_xy = "源" in str(title)
        self._result_refs = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        # 过滤器
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("通道类型过滤："))
        self._type_combo = QComboBox()
        self._type_combo.setStyleSheet(_css_combo())
        self._type_combo.addItem("全部", "")
        for key, label in CH_LABELS.items():
            if key == "motor_xy" and not self._allow_virtual_xy:
                continue
            self._type_combo.addItem(label, key)
        if self._ch_type_hint:
            idx = self._type_combo.findData(self._ch_type_hint)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
        self._type_combo.currentIndexChanged.connect(self._refresh_list)
        filter_row.addWidget(self._type_combo, stretch=1)
        lay.addLayout(filter_row)

        # 设备列表
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lay.addWidget(self._list, stretch=1)

        # 按钮（D波：对话框 chrome 按钮语义——确定 primary / 取消 cancel）
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        apply_dialog_chrome(self)

        self._refresh_list()

    def _refresh_list(self):
        self._list.clear()
        type_filter = self._type_combo.currentData() or ""

        fixtures, defs = self._get_fixtures()
        for inst in fixtures:
            def_id = getattr(inst, 'def_id', '')
            fdef = defs.get(def_id)
            if fdef is None:
                continue
            channels = getattr(fdef, 'channels', [])
            start_ch = getattr(inst, 'start_channel', 1) - 1
            universe  = getattr(inst, 'universe', 1)
            inst_name = getattr(inst, 'name', 'Fixture')
            channel_rows = []
            motor_xy_rows = []

            for i, ch_type in enumerate(channels):
                ch_key = _channel_type_key(ch_type)
                addr = start_ch + i
                if not (0 <= addr < 512):
                    continue
                if ch_key in {"motor_x", "motor_y"}:
                    motor_xy_rows.append({
                        "fixture_id": getattr(inst, 'id', ''),
                        "ch_type":    ch_key,
                        "universe":   universe,
                        "dmx_addr":   addr,
                    })
                channel_rows.append((ch_key, ch_type, addr))

            if type_filter and _channel_type_key(type_filter) == "motor_xy":
                if motor_xy_rows:
                    names = "/".join(CH_LABELS.get(r["ch_type"], r["ch_type"]) for r in motor_xy_rows)
                    text = f"{inst_name}  |  电机 XY（{names}） |  U{universe}"
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, list(motor_xy_rows))
                    self._list.addItem(item)
                continue

            for ch_key, ch_type, addr in channel_rows:
                filter_key = _channel_type_key(type_filter)
                if type_filter and ch_key != filter_key:
                    continue
                ch_label = _picker_channel_label(self._pm, inst, ch_key, ch_type)
                text = f"{inst_name}  |  {ch_label}  |  U{universe} ch{addr+1}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, {
                    "fixture_id": getattr(inst, 'id', ''),
                    "ch_type":    "R" if ch_key == "r" else "G" if ch_key == "g" else "B" if ch_key == "b" else "W" if ch_key == "w" else ch_key,
                    "universe":   universe,
                    "dmx_addr":   addr,
                })
                self._list.addItem(item)

    def _get_fixtures(self):
        """获取所有设备实例和定义。"""
        # 通过统一助手访问 pm，避免属性命名不一致导致拿不到设备
        return _resolve_pm_fixtures(self._pm)

    def _on_accept(self):
        from ...processing.track_linker_engine import ChannelRef
        self._result_refs = []
        for item in self._list.selectedItems():
            d = item.data(Qt.ItemDataRole.UserRole)
            rows = d if isinstance(d, list) else [d]
            for row in rows:
                if row:
                    self._result_refs.append(ChannelRef(
                        fixture_id=row["fixture_id"],
                        ch_type=row["ch_type"],
                        universe=row["universe"],
                        dmx_addr=row["dmx_addr"],
                    ))
        self.accept()

    def get_result(self):
        return self._result_refs


# ─────────────────────────────────────────────────────────────────────────────
#  批量生成对话框
# ─────────────────────────────────────────────────────────────────────────────

class BatchGenerateDialog(QDialog):
    """从设备组批量生成关联规则（两步式）。

    步骤 0 — 设备组多选（入围）：Shift 连续多选 / Ctrl 加选，选中多个
              设备组后点「确认入围」；「全部设备」与其他组互斥，选中
              具体组时自动取消「全部设备」。
    步骤 1 — 通道映射最终确认：关联预设 + 源/目标通道类型 + 参数，
              对所有入围组统一确认（生成时逐组独立配对，不跨组）。
    """

    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量生成关联规则")
        self.setMinimumSize(540, 480)
        # S1 重构：删除与全局主题重复的 QDialog 背景样式（GLOBAL_QSS 已覆盖）。
        self._pm = pm
        self._result = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # S1：两步向导步骤指示（纯视觉，§9/audit-B）
        self._steps_lbl = QLabel("第 1/2 步 · 选择设备组（入围）")
        self._steps_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:11px;font-weight:bold;")
        lay.addWidget(self._steps_lbl)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack, stretch=1)

        self._build_shortlist_page()
        self._build_mapping_page()

        # D波：统一对话框外框 + 主/次按钮语义（primary 蓝 / cancel 灰）
        apply_dialog_chrome(self)

        self._stack.setCurrentIndex(0)
        self._on_preset_changed()
        self._preset_combo.currentIndexChanged.connect(self._update_preview)
        self._src_combo.currentIndexChanged.connect(self._update_preview)
        self._dst_combo.currentIndexChanged.connect(self._update_preview)
        self._update_selected_lbl()

    def _build_shortlist_page(self):
        """步骤 0：设备组多选（入围）——Shift/Ctrl 多选 + 确认入围。"""
        page0 = QWidget()
        p0 = QVBoxLayout(page0)
        p0.setSpacing(8)

        tip = QLabel(
            "选择设备组（入围）：\n"
            "Shift 连续多选 / Ctrl 加选；选完后点「确认入围」进入通道映射确认。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        p0.addWidget(tip)

        self._group_list = QListWidget()
        self._group_list.setStyleSheet(_css_list())
        self._group_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._populate_groups()
        self._group_list.itemSelectionChanged.connect(
            self._on_group_selection_changed)
        p0.addWidget(self._group_list, stretch=1)

        self._selected_lbl = QLabel("已选：0 个设备组")
        self._selected_lbl.setStyleSheet(f"color:{GREEN};font-size:11px;")
        p0.addWidget(self._selected_lbl)

        step0_btns = QHBoxLayout()
        cancel0_btn = QPushButton("取消")
        cancel0_btn.setProperty("role", "cancel")
        cancel0_btn.clicked.connect(self.reject)
        step0_btns.addWidget(cancel0_btn)
        step0_btns.addStretch()
        confirm_btn = QPushButton("确认入围 →")
        confirm_btn.setToolTip("进入第 2 步：通道映射确认")
        confirm_btn.setObjectName("primary")
        confirm_btn.clicked.connect(self._confirm_shortlist)
        step0_btns.addWidget(confirm_btn)
        p0.addLayout(step0_btns)
        self._stack.addWidget(page0)

    def _build_mapping_page(self):
        """步骤 1：通道映射最终确认（预设 + 源/目标通道类型 + 参数）。"""
        from ...processing.track_linker_engine import BUILTIN_LINK_PRESETS
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setSpacing(10)

        # 已入围组摘要
        self._shortlist_lbl = QLabel("")
        self._shortlist_lbl.setWordWrap(True)
        self._shortlist_lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        p1.addWidget(self._shortlist_lbl)

        # 预设选择（D波 prop_row 行版式）
        self._preset_combo = QComboBox()
        # 按分类分组
        cats: Dict[str, list] = {}
        for p in BUILTIN_LINK_PRESETS:
            cats.setdefault(p.category, []).append(p)
        for cat, presets in cats.items():
            for p in presets:
                self._preset_combo.addItem(f"[{cat}] {p.name}", p.id)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        p1.addWidget(prop_row("关联预设", self._preset_combo))

        # 源/目标通道类型
        # ★★ 2026-09-17 用户实报（"其它设备始终显示可创建 0 条规则"）修复：
        #   原实现从**硬编码 12 项** CH_LABELS 建列表 —— 工程里真实存在的
        #   **功能通道**（function_N，如 Vario 的 Zoom、调气爆的 PumpOnOff、
        #   Winch 的 Height）以及任何本表未列出的通道**根本不出现在选择器里**，
        #   用户对这类设备永远只能选到不匹配的通道 → 预览恒 0 条。
        #   统一规则：候选通道 = 硬编码基表 ∪ **所选设备组内实际出现的通道**
        #   （含功能通道，按物理顺序），标签优先取工程通道名。
        self._src_combo = QComboBox()
        self._dst_combo = QComboBox()
        self._rebuild_channel_combos()
        self._src_wrap = prop_row("源通道类型", self._src_combo)
        self._dst_wrap = prop_row("目标通道类型", self._dst_combo)
        # 组目标预设时用来替代目标下拉的只读说明（默认隐藏）
        self._dst_group_lbl = QLabel("")
        self._dst_group_lbl.setStyleSheet(
            f"color:{GREEN};font-size:11px;background:transparent;")
        self._dst_group_wrap = prop_row("目标通道", self._dst_group_lbl)
        self._dst_group_wrap.setVisible(False)
        ch_row = QHBoxLayout()
        ch_row.addWidget(self._src_wrap, stretch=1)
        ch_row.addWidget(self._dst_wrap, stretch=1)
        p1.addLayout(ch_row)
        p1.addWidget(self._dst_group_wrap)

        # 预设参数（简化版，只显示默认值 · D波 PanelSection 分区）
        self._param_group = PanelSection("预设参数（默认值）")
        self._param_layout = QVBoxLayout()
        self._param_layout.setContentsMargins(0, 0, 0, 0)
        self._param_layout.setSpacing(4)
        self._param_group.add_layout(self._param_layout)
        p1.addWidget(self._param_group)

        # 预览：将生成多少条规则
        self._preview_lbl = QLabel("预计生成：0 条规则")
        self._preview_lbl.setStyleSheet(f"color:{GREEN};font-size:11px;")
        p1.addWidget(self._preview_lbl)

        # 按钮：上一步 / 生成规则 / 取消（chrome 按钮语义）
        btns_row = QHBoxLayout()
        back_btn = QPushButton("← 上一步")
        back_btn.setToolTip("返回第 1 步：重新选择设备组")
        back_btn.clicked.connect(self._back_to_shortlist)
        btns_row.addWidget(back_btn)
        btns_row.addStretch()
        ok_btn = QPushButton("生成规则")
        ok_btn.setToolTip("按确认的映射对所有入围组生成关联规则")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self._on_accept)
        btns_row.addWidget(ok_btn)
        cancel1_btn = QPushButton("取消")
        cancel1_btn.setProperty("role", "cancel")
        cancel1_btn.clicked.connect(self.reject)
        btns_row.addWidget(cancel1_btn)
        p1.addLayout(btns_row)
        self._stack.addWidget(page1)

    def _populate_groups(self):
        self._group_list.clear()
        all_item = QListWidgetItem("（全部设备）")
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self._group_list.addItem(all_item)
        try:
            if hasattr(self._pm, 'fixture_groups'):
                groups = self._pm.fixture_groups or []
            elif hasattr(self._pm, 'project'):
                groups = getattr(self._pm.project, 'fixture_groups', []) or []
            else:
                groups = []
            for g in groups:
                if isinstance(g, dict):
                    name = g.get('name', 'Group')
                    gid  = g.get('id', '')
                else:
                    name = getattr(g, 'name', 'Group')
                    gid  = getattr(g, 'id', '')
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, gid)
                self._group_list.addItem(item)
        except Exception:
            pass
        # 默认选中「全部设备」（与原下拉框默认一致）
        self._group_list.setCurrentRow(0)

    def _on_preset_changed(self):
        from ...processing.track_linker_engine import PRESET_BY_ID
        preset_id = self._preset_combo.currentData()
        pdef = PRESET_BY_ID.get(preset_id) if preset_id else None

        # 清空参数区（L 波原型B：立即脱离可视树，防延迟销毁残影）
        while self._param_layout.count():
            item = self._param_layout.takeAt(0)
            w_old = item.widget()
            if w_old is not None:
                w_old.hide()
                w_old.setParent(None)
                w_old.deleteLater()

        self._param_widgets: dict = {}

        if pdef:
            self._apply_preset_hints(pdef)
            self._build_preset_param_rows(pdef)
        if not pdef or not pdef.params_def:
            self._param_layout.addWidget(QLabel("（此预设无参数）"))
        self._update_preview()

    def _apply_preset_hints(self, pdef) -> None:
        """按预设建议设源/目标下拉，并同步组目标 UI。

        ★★ 用户定案（2026-09-18）：预设自己声明目标通道组时（如电机→颜色 =
          RGBW 全组、泵→灯跟踪 = 全部灯通道），**不再让用户选某个单通道** ——
          选了也做不出效果（偏白必须同时写 R/G/B/W）。目标下拉收起，改为显示
          预设声明的目标说明；生成与预览都走该声明。
        """
        for combo, hint in ((self._src_combo, pdef.src_type_hint),
                            (self._dst_combo, pdef.dst_type_hint)):
            if hint:
                idx = combo.findData(hint)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self._sync_dst_group_ui(pdef)

    def _build_preset_param_rows(self, pdef) -> None:
        """重建预设参数行（D波：prop_row + LinkParamSlider，浮点绘在滑槽上）。"""
        for pd in pdef.params_def:
            sl = LinkParamSlider(pd)
            self._param_widgets[pd["key"]] = (sl, pd.get("scale", 1.0))
            self._param_layout.addWidget(prop_row(pd["label"], sl))

    def _sync_dst_group_ui(self, pdef) -> None:
        """按预设声明的 `dst_group` 切换目标通道 UI（组目标 → 收起下拉）。"""
        from ...processing.track_linker_engine import DST_GROUP_CHANNELS
        grp = getattr(pdef, "dst_group", "") or ""
        if not grp:
            self._dst_group_wrap.setVisible(False)
            self._dst_wrap.setVisible(True)
            return
        keys = DST_GROUP_CHANNELS.get(grp, ())
        names = " / ".join(_ch_label(k) for k in keys) or grp
        self._dst_group_lbl.setText(f"{names}（此预设整组驱动，无需选择）")
        self._dst_wrap.setVisible(False)
        self._dst_group_wrap.setVisible(True)

    def _update_preview(self):
        count = self._estimate_rule_count()
        self._preview_lbl.setText(f"预计生成：{count} 条规则")

    def _rebuild_channel_combos(self) -> None:
        """按**入围设备组内实际存在的通道**重建源/目标下拉（含功能通道）。

        ★★ 2026-09-17 用户实报修复（统一规则，适用所有项目）：
          原实现写死 CH_LABELS 的 12 项 → 工程真实通道若不在表内（最典型的就是
          功能通道 function_N：Vario 的 Zoom、调气爆的 PumpOnOff、Winch 的
          Height…）永远选不到 → "其它设备始终显示可创建 0 条规则"。
          候选 = 基表 ∪ 实际通道（实际通道按其在 def.channels 中的出现顺序，
          标签用 _ch_label 取工程通道名）。保持选项顺序稳定（基表在前）以免
          用户已选项跳位。
        """
        try:
            keys = _collect_project_channel_keys(self._selected_scope_defs())
        except Exception:
            keys = []
        base_src = [k for k in CH_LABELS if k != "placeholder"]
        base_dst = [k for k in base_src if k != "motor_xy"]
        extra = [k for k in keys if k not in CH_LABELS]
        try:
            _items = _collect_project_channel_items(self._selected_scope_defs())
        except Exception:
            _items = []
        _fill_combo(self._src_combo, base_src + extra, _items)
        _fill_combo(self._dst_combo, base_dst + extra, _items)
        # 入围后若当前选择在该作用域内不可能出规则 → 自动切到首对可用通道
        # （默认 dimmer→R 对单通道设备恒 0 条，见模块内说明）
        if keys:
            _auto_pick_working_pair(self, keys)

    def _selected_scope_defs(self) -> list:
        """入围组（或全部设备）覆盖到的 FixtureDefinition 列表。"""
        all_sel, group_ids, _ = self._selection_summary()
        fixtures, defs = self._get_fixtures()
        if all_sel or not group_ids:
            scoped = fixtures
        else:
            scoped = [f for scope in self._group_scopes(fixtures, group_ids)
                      for f in scope]
        return _defs_of(scoped, defs)

    def _estimate_rule_count(self) -> int:
        """预览计数：与引擎 build_rules_from_group 完全同源
        （自关联 + 跨设备配对），保证预览与实际生成一致。
        多选入围时逐组估算求和（生成也逐组独立，不跨组配对）。
        """
        src_type = self._src_combo.currentData() or ""
        dst_type = self._dst_combo.currentData() or ""
        all_sel, group_ids, _ = self._selection_summary()

        fixtures, defs = self._get_fixtures()
        if all_sel or not group_ids:
            scopes = [fixtures]
        else:
            scopes = self._group_scopes(fixtures, group_ids)

        from ...processing.track_linker_engine import TrackLinkerEngine
        total = 0
        for scope in scopes:
            try:
                rules = TrackLinkerEngine().build_rules_from_group(
                    fixture_instances=scope,
                    fixture_defs=defs,
                    preset_id=self._preset_combo.currentData() or "passthrough",
                    src_ch_type=src_type,
                    dst_ch_type=dst_type,
                    params={},
                    group_label="",
                    # 与批量生成同源：传逻辑设备做包单元分组；
                    # 无 resolve_choice → 多候选自动取首个类型（数量与询问一致）
                    logical_devices=_build_logical_devices(self._pm),
                )
                total += len(rules)
            except Exception:
                continue
        return total

    def _get_fixtures(self):
        # 通过统一助手访问 pm，避免属性命名不一致导致拿不到设备
        return _resolve_pm_fixtures(self._pm)

    def _selection_summary(self) -> Tuple[bool, List[str], List[str]]:
        """返回 (是否选中全部设备, 选中组 id 列表, 选中组名列表)。"""
        all_sel = False
        ids: List[str] = []
        names: List[str] = []
        for item in self._group_list.selectedItems():
            gid = item.data(Qt.ItemDataRole.UserRole)
            if gid is None:
                all_sel = True
            else:
                ids.append(gid)
                names.append(item.text())
        return all_sel, ids, names

    def _on_group_selection_changed(self):
        # 「全部设备」与其他组互斥：选中具体组时自动取消「全部设备」
        all_item = self._group_list.item(0)
        selected = self._group_list.selectedItems()
        if all_item is not None and all_item in selected and len(selected) > 1:
            self._group_list.blockSignals(True)
            all_item.setSelected(False)
            self._group_list.blockSignals(False)
        self._update_selected_lbl()

    def _update_selected_lbl(self):
        all_sel, ids, _names = self._selection_summary()
        if all_sel:
            self._selected_lbl.setText("已选：全部设备")
        else:
            self._selected_lbl.setText(f"已选：{len(ids)} 个设备组")

    def _confirm_shortlist(self):
        """入围确认：校验至少选中一个设备组，进入通道映射确认步骤。"""
        all_sel, ids, names = self._selection_summary()
        if not all_sel and not ids:
            QMessageBox.warning(self, "未选择设备组",
                                "请至少选择一个设备组（或选择「全部设备」）。")
            return
        label = "全部设备" if all_sel else "、".join(names)
        self._shortlist_lbl.setText(f"已入围设备组：{label}")
        # ★ 入围确定后按实际通道重建下拉（含功能通道）——修"其它设备恒 0 条"
        self._rebuild_channel_combos()
        self._steps_lbl.setText("第 2/2 步 · 通道映射确认")
        self._stack.setCurrentIndex(1)
        self._update_preview()

    def _back_to_shortlist(self):
        self._steps_lbl.setText("第 1/2 步 · 选择设备组（入围）")
        self._stack.setCurrentIndex(0)

    def _group_scopes(self, fixtures, group_ids: List[str]) -> List[list]:
        """按组 id 把设备切分为逐组范围（组内保持 fixture_ids 顺序）。

        多选入围 = 逐组独立生成：每组只在自己的设备范围内配对，
        不跨组配对（与单选单组行为一致）。
        """
        try:
            if hasattr(self._pm, 'fixture_groups'):
                groups = self._pm.fixture_groups or []
            elif hasattr(self._pm, 'project'):
                groups = getattr(self._pm.project, 'fixture_groups', []) or []
            else:
                groups = []
            by_fid = {getattr(f, 'id', ''): f for f in fixtures}
            scopes: List[list] = []
            for gid in group_ids:
                for g in groups:
                    gid2 = g.get('id', '') if isinstance(g, dict) else getattr(g, 'id', '')
                    if gid2 != gid:
                        continue
                    fids = (g.get('fixture_ids', []) if isinstance(g, dict)
                            else getattr(g, 'fixture_ids', []))
                    scopes.append([by_fid[fid] for fid in fids if fid in by_fid])
                    break
            return scopes
        except Exception:
            return [list(fixtures)]

    def _on_accept(self):
        preset_id = self._preset_combo.currentData()
        src_type  = self._src_combo.currentData() or ""
        dst_type  = self._dst_combo.currentData() or ""
        all_sel, group_ids, group_names = self._selection_summary()

        params = {}
        for key, (widget, scale) in getattr(self, '_param_widgets', {}).items():
            # D波：LinkParamSlider 直接给出 ×scale 后的最终参数值（与旧契约同源）
            if isinstance(widget, LinkParamSlider):
                params[key] = widget.scaled_value()
            else:
                params[key] = widget.value() * scale

        if all_sel or not group_ids:
            group_ids = []
            group_names = ["（全部设备）"]
        self._result = {
            "preset_id":   preset_id,
            "src_type":    src_type,
            "dst_type":    dst_type,
            # 多选入围：全部选中组一起进入生成流程（空列表 = 全部设备）
            "group_ids":   list(group_ids),
            "group_names": list(group_names),
            # 兼容旧字段：首组（单选场景与原 result 一致）
            "group_id":    group_ids[0] if group_ids else None,
            "group_name":  group_names[0] if group_names else "",
            "params":      params,
        }
        self.accept()

    def get_result(self):
        return self._result


# ─────────────────────────────────────────────────────────────────────────────
#  包内子设备类型选择对话框
# ─────────────────────────────────────────────────────────────────────────────

class SubDeviceTypeDialog(QDialog):
    """设备包内源/目标子设备类型选择（批量生成"再多一步询问"）。

    场景：设备包（如 全能卡一维包）内含多个同功能子设备（2 台全能卡电机 +
    1 台 MDD 电机），批量生成 电机→泵 时必须明确"哪个子设备的电机 → 哪个
    子设备的泵"。本对话框列出全局候选类型，用户各选其一；单候选类型自动
    带出（仅当源或目标存在多候选时引擎才会弹出本对话框）。
    """

    def __init__(self, src_types: List[str], dst_types: List[str],
                 preset_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择源/目标子设备")
        self.setMinimumSize(460, 220)
        # S1 重构：删除与全局主题重复的 QDialog 背景样式（GLOBAL_QSS 已覆盖）。
        self._result: Optional[Tuple[str, str]] = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        tip = QLabel(
            f"设备包含多个子设备，请选择用于「{preset_name}」的子设备：\n"
            "（规则将映射到所选子设备的真实通道）")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        lay.addWidget(tip)

        self._src_combo = QComboBox()
        for t in src_types:
            self._src_combo.addItem(t, t)
        self._dst_combo = QComboBox()
        for t in dst_types:
            self._dst_combo.addItem(t, t)
        lay.addWidget(prop_row("源子设备", self._src_combo))
        lay.addSpacing(4)
        lay.addWidget(prop_row("目标子设备", self._dst_combo))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # D波：对话框 chrome 按钮语义（确定 primary / 取消 cancel）
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        apply_dialog_chrome(self)

    def _on_accept(self):
        self._result = (
            self._src_combo.currentData() or "",
            self._dst_combo.currentData() or "",
        )
        self.accept()

    def get_result(self):
        return self._result


# ─────────────────────────────────────────────────────────────────────────────
#  规则编辑器（右栏）
# ─────────────────────────────────────────────────────────────────────────────

class PresetBlock(QFrame):
    """关联器「一套预设」的 极简堆栈条目（用户定案 2026-09-18 重设计）。

    为什么重做（用户实报）：
      · 旧版用 QTabWidget 承载多预设：页签窄、名字被截断、末尾"＋"不显眼，
        用户根本发现不了"能给同一套设备再叠一套预设"；
      · 旧版顶部还有一幅"关联预设"曲线预览，与页内那幅重复，而且顶部那幅
        用的是第一条规则的预设 + 活动页参数（两者不一致）→ 同屏两幅图且上幅错。

    新形态（对齐 修改器/形态键堆栈）：
      ▾ 电机→泵（线性衰减）        3 条    [×]     ← 头行：三角 + 名 + 计数 + 删
        ┌── 曲线预览（本预设唯一一幅）──┐
        └──────────────────────────────┘
        归中值      [=====|====]
        最大偏差    [====|=====]
      一次只展开一套（`set_expanded`），点别套自动收起 —— 参数永远不会串页。
    """

    activated      = pyqtSignal(str)       # 点该条目 → 设为活动预设
    params_changed = pyqtSignal(str)       # 任一参数变动（携带 preset_id）
    deleted        = pyqtSignal(str)       # 请求移除该预设的所有规则

    def __init__(self, pid: str, title: str, *, preset_def,
                 params: dict, count: int = 0, parent=None):
        super().__init__(parent)
        self._pid = str(pid)
        self._expanded = True
        self.setObjectName("presetBlock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 头行：三角 + 名 + 计数 + 删除 ────────────────────────────────
        head = QWidget()
        head.setFixedHeight(24)
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head.setObjectName("presetHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(6, 0, 4, 0)
        hl.setSpacing(6)
        self._arrow = QLabel("▾")
        self._arrow.setFixedWidth(10)
        hl.addWidget(self._arrow)
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:11px;background:transparent;")
        hl.addWidget(self._title_lbl, 1)
        self._count_lbl = QLabel(f"{count} 条")
        self._count_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:10px;background:transparent;")
        hl.addWidget(self._count_lbl)
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 16)
        del_btn.setStyleSheet(_css_btn(fg=RED, font_size=10,
                                       padding="0px 2px", border="transparent"))
        del_btn.setToolTip("移除这套预设（只删本预设的映射）")
        del_btn.clicked.connect(lambda: self.deleted.emit(self._pid))
        hl.addWidget(del_btn)
        head.mousePressEvent = self._on_head_click      # 点头行 = 展开/激活
        lay.addWidget(head)

        # ── 体：曲线 + 参数 ─────────────────────────────────────────────
        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(10, 4, 6, 8)
        bl.setSpacing(5)
        self.preview = LinkCurvePreview()
        bl.addWidget(self.preview)
        self.widgets: dict = {}
        for pd in (getattr(preset_def, "params_def", None) or ()):
            slider = LinkParamSlider(pd)
            key = pd["key"]
            if key in (params or {}):
                slider.set_float(params[key] / pd.get("scale", 1.0))
            scale = pd.get("scale", 1.0)
            slider.valueChanged.connect(
                lambda _v: self.params_changed.emit(self._pid))
            bl.addWidget(prop_row(pd["label"], slider))
            self.widgets[key] = (slider, scale)
        if preset_def is not None and not getattr(preset_def, "params_def", None):
            bl.addWidget(QLabel("（此预设无参数）"))
        bl.addStretch()
        self._body.setVisible(True)
        lay.addWidget(self._body)

    def _on_head_click(self, ev):
        self.activated.emit(self._pid)

    def preset_id(self) -> str:
        return self._pid

    def set_expanded(self, on: bool) -> None:
        """展开/收起 + 极简态：展开=卡片（灰底细边 + 蓝色左竖条），
        收起=扁平行（透明底，仅悬停提亮）——一眼能看出哪套正在编辑。"""
        self._expanded = bool(on)
        self._arrow.setText("▾" if self._expanded else "▸")
        self._body.setVisible(self._expanded)
        if self._expanded:
            self.setStyleSheet(
                f"QFrame#presetBlock{{background:{BG_BOX};"
                f"border:1px solid rgba(255,255,255,30);"
                f"border-left:2px solid {SELECT_BLUE};"
                f"border-radius:{RADIUS}px;}}"
                f"QWidget#presetHead{{background:transparent;}}")
        else:
            self.setStyleSheet(
                f"QFrame#presetBlock{{background:transparent;"
                f"border:1px solid transparent;border-left:2px solid transparent;"
                f"border-radius:{RADIUS}px;}}"
                f"QFrame#presetBlock:hover{{background:{BG_PANEL_SUB};}}"
                f"QWidget#presetHead{{background:transparent;}}")

    def params(self) -> dict:
        """本块参数（与旧 `_page_params` 数值契约同源）。"""
        out = {}
        for key, (widget, scale) in self.widgets.items():
            out[key] = (widget.scaled_value() if isinstance(widget, LinkParamSlider)
                        else widget.value() * scale)
        return out

    def set_enabled_state(self, on: bool) -> None:
        for slider, _ in self.widgets.values():
            slider.setEnabled(on)


class RuleEditor(QWidget):
    """编辑单条 LinkRule 的详细参数；组条目模式下次参数修改应用到组内全部规则。"""

    rule_changed       = pyqtSignal(object)   # 单条模式：发射修改后的 LinkRule
    rules_batch_changed = pyqtSignal(list)    # 组条目模式：发射修改后的规则列表
    # ★ 组条目模式：请求"再给这个组加一套预设"（「＋ 添加预设」按钮触发）
    add_preset_requested = pyqtSignal(str)
    # ★ 请求移除一套预设（组 id, 预设 id, 该预设的规则 id 列表）
    preset_removed = pyqtSignal(str, str, list)

    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self._pm   = pm
        self._rule = None
        self._batch_rules: Optional[List] = None   # 非 None = 组条目整体编辑模式
        self._param_widgets: dict = {}
        self._loading_rule = False
        self._group_id: str = ""     # 组条目模式下的组 id（「＋」页签要用）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"RuleEditor{{background:{PANEL_BG};}}")
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        lay.addWidget(self._build_name_row())

        # 预设选择（D波：可折叠 PanelSection 分区）
        self._preset_grp = preset_grp = PanelSection("关联预设")
        preset_lay = QVBoxLayout()
        preset_lay.setContentsMargins(0, 0, 0, 0)
        preset_lay.setSpacing(6)

        # 分类过滤
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("全部", "")
        self._populate_categories()
        self._cat_combo.currentIndexChanged.connect(self._refresh_preset_list)
        preset_lay.addWidget(prop_row("分类", self._cat_combo))

        self._preset_combo = QComboBox()
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_lay.addWidget(self._preset_combo)
        # ★ 2026-09-18 重设计：这里**不再放曲线预览** —— 曲线跟随活动预设块
        #   （旧版此处与"预设参数"区各一幅，上幅还用错了数据源 → 用户实报
        #   "同一个预设两幅图，上面那幅还是无效的"）。本条说明只在组模式下显示。
        self._preset_hint = QLabel(
            "在下方「预设堆栈」中点击一套预设即可展开其曲线与参数；"
            "点「＋ 添加预设」给同一套设备叠加更多关联。")
        self._preset_hint.setWordWrap(True)
        self._preset_hint.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:10px;background:transparent;")
        self._preset_hint.setVisible(False)
        preset_lay.addWidget(self._preset_hint)
        preset_grp.add_layout(preset_lay)

        lay.addWidget(preset_grp)

        # ★ 2026-09-18 重设计：预设堆栈（堆栈式）
        #   每套预设 = 一个可展开块（自带曲线 + 参数），一次只展开一套。
        self._param_grp = PanelSection("预设堆栈")
        self._param_layout = QVBoxLayout()
        self._param_layout.setContentsMargins(0, 0, 0, 0)
        self._param_layout.setSpacing(4)
        self._param_grp.add_layout(self._param_layout)
        lay.addWidget(self._param_grp)
        self._build_preset_pages_host()

        lay.addWidget(self._build_blend_row())

        # ── 时序控制（跟水参数 · D波：PanelSection 分区）───────────────────────
        timing_grp = PanelSection("时序控制（跟水参数）")
        timing_lay = QVBoxLayout()
        timing_lay.setContentsMargins(0, 0, 0, 0)
        timing_lay.setSpacing(4)

        # 时差（Stagger Delay）
        self._stagger_spin = QDoubleSpinBox()
        self._stagger_spin.setRange(0, 5000)
        self._stagger_spin.setValue(0)
        self._stagger_spin.setSingleStep(10)
        self._stagger_spin.setSuffix(" ms")
        self._stagger_spin.valueChanged.connect(self._on_field_changed)
        timing_lay.addWidget(prop_row("时差 (Stagger)", self._stagger_spin))

        # 紧距（Spacing Profile）
        self._spacing_combo = QComboBox()
        self._spacing_combo.addItem("uniform", "uniform")
        self._spacing_combo.addItem("线性衰减", "linear_fade")
        self._spacing_combo.addItem("高斯分布", "gaussian")
        self._spacing_combo.setCurrentIndex(0)
        self._spacing_combo.currentIndexChanged.connect(self._on_field_changed)
        timing_lay.addWidget(prop_row("紧距 (Spacing)", self._spacing_combo))

        # 喷水延时（Spray Delay）
        self._spray_spin = QDoubleSpinBox()
        self._spray_spin.setRange(0, 5000)
        self._spray_spin.setValue(0)
        self._spray_spin.setSingleStep(10)
        self._spray_spin.setSuffix(" ms")
        self._spray_spin.valueChanged.connect(self._on_field_changed)
        timing_lay.addWidget(prop_row("喷水延时 (Spray)", self._spray_spin))

        timing_grp.add_layout(timing_lay)
        lay.addWidget(timing_grp)

        # v1.2：源/目标通道由批量生成自动选择，两个 GroupBox 隐藏（仍入布局
        # 管理生命周期；数据逻辑 _refresh_channel_lists 等保留）。
        lay.addWidget(self._build_channel_grp("源通道", GREEN, self._src_list_holder()))
        lay.addWidget(self._build_channel_grp("目标通道", RED, self._dst_list_holder()))

        lay.addStretch()

        # 初始填充预设列表
        self._refresh_preset_list()
        self._set_enabled(False)

    def _build_name_row(self) -> QWidget:
        """规则名称行（组模式下附「组条目」徽标）。"""
        self._name_edit = QLineEdit()
        self._name_edit.setStyleSheet(_css_lineedit())
        self._name_edit.setPlaceholderText("输入规则名称…")
        self._name_edit.editingFinished.connect(self._on_field_changed)
        row = prop_row("规则名称", self._name_edit)
        # ★ 2026-09-18 用户实报（问题5："丑陋的 组条目整体编辑 绿色的难看"）：
        #   旧样式是 SUCCESS_GREEN 半透底 + BRIGHT_SUCCESS 字 + 绿描边（两套绿混用）。
        #   改为与规则**选中态**同语言的蓝底白字胶囊（它描述的是"当前处于组整体
        #   编辑态"这一状态，用蓝色状态色而非绿色成功色）。
        self._batch_badge = QLabel("组条目整体编辑")
        self._batch_badge.setStyleSheet(
            f"background:{SELECT_BLUE};color:{TEXT_SELECTED_FG};"
            f"border:1px solid {BORDER};"
            f"border-radius:{RADIUS}px;padding:2px 8px;font-size:10px;font-weight:bold;")
        self._batch_badge.setVisible(False)
        row.layout().addWidget(self._batch_badge)
        return row

    def _build_blend_row(self) -> QWidget:
        """混合模式行（v1.2 起统一为低值覆盖：只减不加，作为已有值的遮罩）。"""
        lbl = QLabel("低值覆盖（只减不加）")
        lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        return prop_row("混合模式", lbl)

    def _src_list_holder(self) -> QListWidget:
        """源通道列表（隐藏区用；构造期建好供 _refresh_channel_lists 使用）。"""
        self._src_list = QListWidget()
        self._src_list.setStyleSheet(_css_list())
        self._src_list.setFixedHeight(80)
        return self._src_list

    def _dst_list_holder(self) -> QListWidget:
        """目标通道列表（隐藏区用）。"""
        self._dst_list = QListWidget()
        self._dst_list.setStyleSheet(_css_list())
        self._dst_list.setFixedHeight(80)
        return self._dst_list

    def _build_channel_grp(self, title: str, color: str,
                           list_w: QListWidget) -> QGroupBox:
        """构建（隐藏的）源/目标通道 GroupBox：列表 + 添加/移除按钮。"""
        grp = QGroupBox(title)
        grp.setStyleSheet(_css_group(color))
        v = QVBoxLayout(grp)
        v.addWidget(list_w)
        row = QHBoxLayout()
        which = "src" if list_w is self._src_list else "dst"
        add_btn = QPushButton(f"+ 添加{title[0]}通道")
        add_btn.setStyleSheet(_css_btn(fg=color))
        add_btn.clicked.connect(
            self._add_src_channel if which == "src" else self._add_dst_channel)
        row.addWidget(add_btn)
        rm_btn = QPushButton("- 移除选中")
        rm_btn.setStyleSheet(_css_btn(fg=RED))
        rm_btn.clicked.connect(lambda: self._remove_channel(list_w, which))
        row.addWidget(rm_btn)
        v.addLayout(row)
        grp.setVisible(False)
        return grp

    def _build_preset_pages_host(self):
        """组条目「预设堆栈」宿主：块容器 + 末尾「＋ 添加预设」（单条模式隐藏）。"""
        self._preset_pages: dict = {}
        self._stack_host = QWidget()
        sh = QVBoxLayout(self._stack_host)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(4)
        self._stack_lay = sh
        self._stack_host.setVisible(False)
        self._param_layout.addWidget(self._stack_host)
        # 「＋ 添加预设」按钮（添加按钮位置感）
        self._add_preset_btn = QPushButton("＋ 添加预设")
        # 添加按钮的位置感与克制配色（虚线框 + 静音文字）
        self._add_preset_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_SECONDARY};"
            f"font-size:11px;padding:5px 8px;border:1px dashed "
            f"rgba(255,255,255,45);border-radius:{RADIUS}px;}}"
            f"QPushButton:hover{{color:{ACCENT};"
            f"border-color:rgba(255,160,60,120);background:{BG_PANEL_SUB};}}")
        self._add_preset_btn.setToolTip(
            "给当前这套设备再叠加一套关联预设（参数独立，互不覆盖）")
        self._add_preset_btn.clicked.connect(
            lambda: self.add_preset_requested.emit(str(self._group_id or "")))
        self._add_preset_btn.setVisible(False)
        self._param_layout.addWidget(self._add_preset_btn)

    def _populate_categories(self):
        from ...processing.track_linker_engine import BUILTIN_LINK_PRESETS
        cats = sorted(set(p.category for p in BUILTIN_LINK_PRESETS))
        for cat in cats:
            self._cat_combo.addItem(cat, cat)

    def _refresh_preset_list(self):
        from ...processing.track_linker_engine import BUILTIN_LINK_PRESETS
        cat_filter = self._cat_combo.currentData() or ""
        current_id = self._preset_combo.currentData()

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for p in BUILTIN_LINK_PRESETS:
            if cat_filter and p.category != cat_filter:
                continue
            self._preset_combo.addItem(p.name, p.id)
        self._preset_combo.blockSignals(False)

        # 恢复之前选中的预设
        if current_id:
            idx = self._preset_combo.findData(current_id)
            if idx >= 0:
                self._preset_combo.setCurrentIndex(idx)

        self._on_preset_changed()

    def _clear_param_rows(self):
        """清空参数区行（保留常驻件：预设堆栈宿主 + 「＋ 添加预设」按钮）。

        L 波原型B：立即脱离可视树，防延迟销毁残影。
        """
        keep = {id(getattr(self, "_stack_host", None)),
                id(getattr(self, "_add_preset_btn", None))}
        for i in range(self._param_layout.count() - 1, -1, -1):
            item = self._param_layout.itemAt(i)
            w_old = item.widget() if item is not None else None
            if w_old is None or id(w_old) in keep:
                continue
            self._param_layout.takeAt(i)
            w_old.hide()
            w_old.setParent(None)
            w_old.deleteLater()
        self._param_widgets.clear()
        for host in (getattr(self, "_stack_host", None),
                     getattr(self, "_add_preset_btn", None)):
            if host is not None:
                host.setVisible(False)

    def _on_preset_changed(self):
        from ...processing.track_linker_engine import PRESET_BY_ID
        preset_id = self._preset_combo.currentData()
        pdef = PRESET_BY_ID.get(preset_id) if preset_id else None

        # ★★ 2026-09-18 用户实报（问题6："添加预设的下面存在重复的对同一个参数
        #    的多个调整控件渲染，老接口没删"）：**组条目模式**下每套预设的参数由
        #    各自的 `PresetBlock` 承载 —— 这里若再建一套老滑块，同一个参数就会
        #    在堆栈块内、块外下方各出现一个控件。故组模式直接返回（只把
        #    `_param_widgets` 指回活动块），老滑块只在**单条规则模式**构建。
        if self._preset_pages:
            self._activate_preset_block(
                self._active_page_id() or next(iter(self._preset_pages), ""))
            return

        self._clear_param_rows()

        if pdef:
            for pd in pdef.params_def:
                # D波：参数行 = prop_row + LinkParamSlider（值绘在滑槽上）
                slider = LinkParamSlider(pd)
                # 从当前规则获取值
                if self._rule and pd["key"] in self._rule.params:
                    raw_val = self._rule.params[pd["key"]] / pd.get("scale", 1.0)
                    slider.set_float(raw_val)
                slider.valueChanged.connect(lambda _v: self._on_field_changed())
                row_w = prop_row(pd["label"], slider)
                self._param_widgets[pd["key"]] = (slider, pd.get("scale", 1.0))
                self._param_layout.addWidget(row_w)

            if not pdef.params_def:
                self._param_layout.addWidget(
                    QLabel("（此预设无参数）"))
        else:
            self._param_layout.addWidget(QLabel("（无预设）"))

        # 更新曲线预览
        self._on_field_changed()

    def _build_preset_pages(self, rules):
        """组条目模式：把组内规则**按预设聚成堆栈块**（堆栈式）。

        ★ 2026-09-18 用户定案（"同一个设备应用两套预设但根本没法实现，也没有
          对应前端设计"）：一个组条目下可以叠多套预设，**每套一个可展开块**，
          块内自带曲线 + 独立参数；一次只展开一套，参数永不串页。

        为什么不用 QTabWidget：页签窄、名字被截断、末尾"＋"不显眼 —— 用户
          根本发现不了能叠加（原话"完全可以说是没有规划好"）。
        """
        from ...processing.track_linker_engine import PRESET_BY_ID
        # ★ 关键：清掉**构造期/上次单条模式**遗留在 `_param_layout` 里的老参数行
        #   （旧实现只清堆栈自己的子件 → 老行与堆栈块同屏 = 用户看到的"重复控件"）。
        #   `_clear_param_rows` 保留堆栈宿主与「＋ 添加预设」两个常驻件。
        self._clear_param_rows()
        self._preset_pages = {}
        while self._stack_lay.count():
            item = self._stack_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        grouped: dict = {}
        for r in (rules or ()):
            grouped.setdefault(str(r.preset_id or ""), []).append(r)
        for pid, rs in grouped.items():
            self._add_preset_block(pid, rs, PRESET_BY_ID.get(pid))
        visible = bool(grouped)
        self._stack_host.setVisible(visible)
        self._add_preset_btn.setVisible(visible)
        self._preset_hint.setVisible(visible)
        self._preset_combo.setVisible(not visible)
        # ★ 2026-09-18：组模式下**整块「关联预设」收走** —— 堆栈块本身就是
        #   预设选择器（分类过滤 + 下拉 + 曲线在块里都有），保留它才是真正的
        #   冗余（用户："看右边那个完全是重复的"）。单条模式恢复显示。
        self._preset_grp.setVisible(not visible)
        if grouped:
            self._activate_preset_block(next(iter(grouped)))

    def _add_preset_block(self, pid: str, rs: list, pdef) -> "PresetBlock":
        """创建一个预设块并挂到堆栈（登记进 `_preset_pages`）。"""
        title = pdef.name if pdef is not None else (pid or "无预设")
        base = (rs[0].params or {}) if rs else {}
        blk = PresetBlock(pid, title, preset_def=pdef, params=base,
                          count=len(rs))
        blk.activated.connect(self._activate_preset_block)
        blk.params_changed.connect(self._on_page_params_changed)
        blk.deleted.connect(self._on_remove_preset)
        if pdef is not None:
            blk.preview.set_fn(pdef.fn, blk.params())
        self._stack_lay.addWidget(blk)
        self._preset_pages[pid] = {"rules": rs, "block": blk, "def": pdef}
        return blk

    def _on_remove_preset(self, pid: str) -> None:
        """移除一套预设（只删该预设的规则，组与其它预设不受影响）。"""
        page = self._preset_pages.get(pid)
        if not page:
            return
        title = page["block"]._title_lbl.text()
        if QMessageBox.question(
                self, "移除预设",
                "确定移除「{}」这套预设的 {} 条映射吗？\n"
                "（同组其它预设不受影响）".format(title, len(page["rules"])),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        gid = str(self._group_id or "")
        self.preset_removed.emit(gid, pid, [r.id for r in page["rules"]])

    def _activate_preset_block(self, pid: str) -> None:
        """展开某预设块并收起其余（一次只展开一套）。"""
        if pid not in self._preset_pages:
            return
        for other, page in self._preset_pages.items():
            blk = page["block"]
            blk.set_expanded(other == pid)
            blk.setProperty("active", other == pid)
        self._param_widgets = self._preset_pages[pid]["block"].widgets

    def _page_params(self, pid: str) -> dict:
        """某一预设块的参数（独立参数集合）。"""
        page = self._preset_pages.get(pid)
        return page["block"].params() if page else {}

    def _active_page_id(self) -> str:
        """当前展开的预设 id（无 → ""）。"""
        for pid, page in self._preset_pages.items():
            if page["block"]._expanded:
                return pid
        return next(iter(self._preset_pages), "")

    def _on_page_params_changed(self, pid: str):
        """某一预设块参数变化：只把该块参数写回**该预设的规则**。"""
        if self._loading_rule:
            return
        page = self._preset_pages.get(pid)
        if not page:
            return
        params = self._page_params(pid)
        if page.get("def") is not None:
            page["block"].preview.set_fn(page["def"].fn, params)
        for r in page["rules"]:
            r.params = dict(params)
        self.rules_batch_changed.emit(
            [r for pg in self._preset_pages.values() for r in pg["rules"]])

    def _collect_params(self) -> dict:
        params = {}
        for key, (widget, scale) in self._param_widgets.items():
            # D波：LinkParamSlider 的 float_value/scaled_value 即最终参数值；
            # 其余历史控件（若有）保持 value()*scale 旧契约。
            if isinstance(widget, LinkParamSlider):
                params[key] = widget.scaled_value()
            else:
                params[key] = widget.value() * scale
        return params

    def _pull_rule_fields(self):
        """把名义/时序控件值读回当前规则（参数另按模式处理）。"""
        self._rule.name = self._name_edit.text() or self._rule.name
        self._rule.stagger_delay_ms = self._stagger_spin.value()
        spacing_data = self._spacing_combo.currentData()
        if spacing_data:
            self._rule.spacing_profile = str(spacing_data)
        self._rule.spray_delay_ms = self._spray_spin.value()

    def _apply_timing_to_group(self):
        """多预设页模式：只把**时序**铺给组内全部规则（参数按页独立）。"""
        for r in (self._batch_rules or ()):
            r.stagger_delay_ms = self._rule.stagger_delay_ms
            r.spacing_profile = self._rule.spacing_profile
            r.spray_delay_ms = self._rule.spray_delay_ms
        self._rule.params = self._page_params(self._active_page_id())
        self.rules_batch_changed.emit(list(self._batch_rules or ()))

    def _apply_single_preset_to_group(self):
        """组条目整体编辑（同一预设）：预设/参数/时序应用到组内全部规则。"""
        for r in self._batch_rules:
            r.preset_id = self._rule.preset_id
            r.params = dict(self._rule.params)
            r.stagger_delay_ms = self._rule.stagger_delay_ms
            r.spacing_profile = self._rule.spacing_profile
            r.spray_delay_ms = self._rule.spray_delay_ms
        self.rules_batch_changed.emit(list(self._batch_rules))

    def _on_field_changed(self, *_):
        if self._rule is None or self._loading_rule:
            return
        self._pull_rule_fields()
        if self._preset_pages:
            self._apply_timing_to_group()
            return
        self._rule.preset_id = self._preset_combo.currentData() or self._rule.preset_id
        self._rule.params = self._collect_params()
        if self._batch_rules is not None:
            self._apply_single_preset_to_group()
        else:
            self.rule_changed.emit(self._rule)

    def load_rule(self, rule):
        """加载单条规则到编辑器。"""
        self._batch_rules = None
        self._preset_pages = {}
        self._stack_host.setVisible(False)
        self._add_preset_btn.setVisible(False)
        self._preset_hint.setVisible(False)
        self._preset_combo.setVisible(True)
        self._preset_grp.setVisible(True)
        self._batch_badge.setVisible(False)
        self._name_edit.setEnabled(True)
        self._preset_combo.setEnabled(True)
        self._loading_rule = True
        try:
            self._rule = rule
            self._set_enabled(True)

            self._name_edit.setText(rule.name)

            # 设置预设
            idx = self._preset_combo.findData(rule.preset_id)
            if idx >= 0:
                self._preset_combo.blockSignals(True)
                self._preset_combo.setCurrentIndex(idx)
                self._preset_combo.blockSignals(False)
            self._on_preset_changed()

            # 混合模式 v1.2 起统一为低值覆盖（旧存档的 override/add/multiply
            # 值由引擎一律按低值覆盖执行，此处无需恢复下拉）

            # 时序控制
            self._stagger_spin.blockSignals(True)
            self._stagger_spin.setValue(rule.stagger_delay_ms)
            self._stagger_spin.blockSignals(False)

            spacing_idx = self._spacing_combo.findData(rule.spacing_profile)
            if spacing_idx >= 0:
                self._spacing_combo.blockSignals(True)
                self._spacing_combo.setCurrentIndex(spacing_idx)
                self._spacing_combo.blockSignals(False)

            self._spray_spin.blockSignals(True)
            self._spray_spin.setValue(rule.spray_delay_ms)
            self._spray_spin.blockSignals(False)

            # 填充源/目标通道列表
            self._refresh_channel_lists()
        finally:
            self._loading_rule = False

    def load_rules(self, rules):
        """组条目整体编辑模式：加载组内全部规则，参数修改应用到全部。

        以组内第一条规则为准显示当前参数；名称输入禁用（各条名称保持独立）。
        """
        if not rules:
            self.clear()
            return
        self._batch_rules = list(rules)
        self._loading_rule = True
        try:
            self._rule = self._batch_rules[0]
            self._set_enabled(True)

            self._name_edit.setEnabled(False)
            self._batch_badge.setVisible(True)
            self._batch_badge.setText(f"组条目整体编辑 · {len(self._batch_rules)} 条映射")
            self._name_edit.setText(self._rule.name)

            # ★ 用户定案（2026-09-17）：组内**每种预设一页**，各自独立参数。
            #   预设下拉在组模式下只读（页签就是预设切换器，防误改全组预设）。
            idx = self._preset_combo.findData(self._rule.preset_id)
            if idx >= 0:
                self._preset_combo.blockSignals(True)
                self._preset_combo.setCurrentIndex(idx)
                self._preset_combo.blockSignals(False)
            self._preset_combo.setEnabled(False)
            self._build_preset_pages(self._batch_rules)

            # 时序控制
            self._stagger_spin.blockSignals(True)
            self._stagger_spin.setValue(self._rule.stagger_delay_ms)
            self._stagger_spin.blockSignals(False)

            spacing_idx = self._spacing_combo.findData(self._rule.spacing_profile)
            if spacing_idx >= 0:
                self._spacing_combo.blockSignals(True)
                self._spacing_combo.setCurrentIndex(spacing_idx)
                self._spacing_combo.blockSignals(False)

            self._spray_spin.blockSignals(True)
            self._spray_spin.setValue(self._rule.spray_delay_ms)
            self._spray_spin.blockSignals(False)

            # 批量模式源/目标通道只读展示第一条的（隐藏 GroupBox，数据逻辑保留）
            self._refresh_channel_lists()
        finally:
            self._loading_rule = False

    def _ref_ch_label(self, ref) -> str:
        """单条通道 ref 的显示名 —— 功能通道优先取**该设备类型的工程真名**。

        ★ 2026-09-18：此前直接用 `CH_LABELS.get(ch_type, ch_type)`，而功能通道
          不在基表里 → 规则编辑器里显示成 `[function_1]`。用户三次反馈"还是
          只看到 function_1"即此。查表按 ref 所属设备类型的
          function_channel_names 取真名，取不到才回退基表/键名。
        """
        key = str(getattr(ref, "ch_type", "") or "")
        name = _fixture_channel_name(self._pm, getattr(ref, "fixture_id", ""), key)
        return name or CH_LABELS.get(key, key)

    def _refresh_channel_lists(self):
        if self._rule is None:
            return
        self._src_list.clear()
        for ref in self._rule.src_channels:
            text = (f"U{ref.universe} ch{ref.dmx_addr+1}  "
                    f"[{self._ref_ch_label(ref)}]")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ref)
            self._src_list.addItem(item)

        self._dst_list.clear()
        for ref in self._rule.dst_channels:
            text = (f"U{ref.universe} ch{ref.dmx_addr+1}  "
                    f"[{self._ref_ch_label(ref)}]")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ref)
            self._dst_list.addItem(item)

    def _add_src_channel(self):
        if self._rule is None:
            return
        from ...processing.track_linker_engine import PRESET_BY_ID
        hint = ""
        pdef = PRESET_BY_ID.get(self._rule.preset_id)
        if pdef:
            hint = pdef.src_type_hint
        dlg = ChannelPickerDialog(self._pm, ch_type_hint=hint,
                                  title="选择源通道", parent=self)
        if popup(dlg).exec():
            refs = dlg.get_result()
            self._rule.src_channels.extend(refs)
            self._refresh_channel_lists()
            self.rule_changed.emit(self._rule)

    def _add_dst_channel(self):
        if self._rule is None:
            return
        from ...processing.track_linker_engine import (
            PRESET_BY_ID, preset_dst_group, DST_GROUP_CHANNELS)
        pdef = PRESET_BY_ID.get(self._rule.preset_id)
        # ★ 用户定案（2026-09-18）：整组目标预设**一次补齐整组通道**（按设备
        #   实际存在的色彩/灯通道），不再让用户逐个手挑 —— 偏白/泵→灯必须
        #   同时写整组才有意义。单通道预设仍走原有选择对话框。
        grp = preset_dst_group(self._rule.preset_id)
        if grp and self._rule.src_channels:
            added = _append_group_dst_refs(
                self._pm, self._rule, DST_GROUP_CHANNELS.get(grp, ()))
            if added:
                self._refresh_channel_lists()
                self.rule_changed.emit(self._rule)
                return
        hint = pdef.dst_type_hint if pdef else ""
        dlg = ChannelPickerDialog(self._pm, ch_type_hint=hint,
                                  title="选择目标通道", parent=self)
        if popup(dlg).exec():
            refs = dlg.get_result()
            self._rule.dst_channels.extend(refs)
            self._refresh_channel_lists()
            self.rule_changed.emit(self._rule)

    def _remove_channel(self, list_widget: QListWidget, which: str):
        if self._rule is None:
            return
        selected = list_widget.selectedItems()
        if not selected:
            return
        refs_to_remove = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        if which == "src":
            self._rule.src_channels = [
                r for r in self._rule.src_channels if r not in refs_to_remove]
        else:
            self._rule.dst_channels = [
                r for r in self._rule.dst_channels if r not in refs_to_remove]
        self._refresh_channel_lists()
        self.rule_changed.emit(self._rule)

    def clear(self):
        self._rule = None
        self._batch_rules = None
        self._preset_pages = {}
        self._stack_host.setVisible(False)
        self._add_preset_btn.setVisible(False)
        self._preset_hint.setVisible(False)
        self._preset_combo.setVisible(True)
        self._preset_grp.setVisible(True)
        self._batch_badge.setVisible(False)
        self._name_edit.setEnabled(True)
        self._preset_combo.setEnabled(True)
        self._set_enabled(False)
        self._name_edit.clear()
        self._src_list.clear()
        self._dst_list.clear()

    def _set_enabled(self, v: bool):
        for w in [self._name_edit, self._cat_combo,
                  self._src_list, self._dst_list,
                  self._stagger_spin, self._spacing_combo, self._spray_spin]:
            w.setEnabled(v)
        # 组模式（多预设页）下预设下拉只读：页签就是预设切换器
        self._preset_combo.setEnabled(v and not self._preset_pages)
        for page in self._preset_pages.values():
            for slider, _ in (page.get("widgets") or {}).values():
                slider.setEnabled(v)
        for slider, _ in self._param_widgets.values():
            slider.setEnabled(v)


# ─────────────────────────────────────────────────────────────────────────────
#  TrackLinkerPanel（主面板）
# ─────────────────────────────────────────────────────────────────────────────

class TrackLinkerPanel(QWidget):
    """
    轨道关联器主面板。
    作为编辑主面板之一，实时输出到 NM linker 层。
    """

    def __init__(self, pm, nm=None, parent=None):
        super().__init__(parent)
        self._pm = pm
        self._nm = nm
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"TrackLinkerPanel{{background:{DARK_BG};color:{TEXT_PRIMARY};}}")

        # 获取/创建引擎
        from ...processing.track_linker_engine import get_linker_engine
        self._engine = get_linker_engine()
        if nm is not None:
            self._engine.set_network_manager(nm)
        self._engine.rules_changed.connect(self._refresh_rule_list)
        # ★★ R130：规则任何变化都要重报总线 —— 否则调度台窗格停留在旧集合（新增/停用
        #   规则看不到），且用户改完规则后窗格与真实输出不一致。（原先唯一的上报者
        #   `_update_status` 是死代码、无人调用。）必须 `force=True`：连续编辑会落在
        #   `_BUS_REPORT_INTERVAL` 节流窗口内被静默丢弃。
        self._engine.rules_changed.connect(lambda: self.report_to_bus(force=True))

        # 当前选中规则 ID / 组条目 ID（互斥）
        self._selected_rule_id: Optional[str] = None
        self._selected_folder_id: Optional[str] = None
        # 规则列表项控件映射 / 组条目控件映射
        self._rule_items: Dict[str, RuleListItem] = {}
        self._folder_items: Dict[str, FolderHeaderItem] = {}
        # 未分组虚拟条目的折叠状态（UI 状态，不持久化）
        self._ungrouped_expanded = True

        self._build()
        self._sync_enable_ui()
        self._refresh_rule_list()

    def set_network_manager(self, nm):
        """延迟注入 NM。"""
        self._nm = nm
        self._engine.set_network_manager(nm)

    # ── UI 构建 ──────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        # ── 主体三栏 ────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(True)
        splitter.setMinimumSize(0, 0)
        splitter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        # S1 重构：删除与全局主题重复的 QSplitter::handle 样式（走 GLOBAL_QSS）。

        # 左栏：设备组/轨道类型过滤
        left = self._build_left_panel()
        left.setMinimumWidth(0)
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        splitter.addWidget(left)

        # 中栏：规则列表
        mid = self._build_mid_panel()
        mid.setMinimumWidth(0)
        mid.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        splitter.addWidget(mid)

        # 右栏：规则编辑器
        self._rule_editor = RuleEditor(self._pm)
        self._rule_editor.setMinimumWidth(0)
        self._rule_editor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._rule_editor.rule_changed.connect(self._on_rule_edited)
        self._rule_editor.rules_batch_changed.connect(self._on_rules_batch_edited)
        self._rule_editor.add_preset_requested.connect(self._on_add_preset_to_group)
        self._rule_editor.preset_removed.connect(self._on_remove_preset_from_group)
        splitter.addWidget(self._rule_editor)

        root.addWidget(splitter, stretch=1)
        from .linker_workspace_ui import install
        install(self, splitter)

    def _build_toolbar(self) -> QWidget:
        """顶部工具行（启用 / 新建 / 批量 / 清空 + 状态与计数）。"""
        toolbar = QWidget()
        toolbar.setFixedHeight(38)
        toolbar.setStyleSheet(
            f"background:{PANEL_BG};border-bottom:1px solid {SEP_LINE};")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(8, 4, 8, 4)
        tb_lay.setSpacing(8)

        # 启用开关
        self._enable_btn = _mk_toolbar_btn("启用关联器",
                                           icon_key="network_start", height=26)
        self._enable_btn.setCheckable(True)
        self._enable_btn.setChecked(self._engine.is_enabled)
        self._enable_btn.setToolTip(
            "启用/停用轨道关联器。启用后按当前映射规则实时改写输出通道值。\n"
            "选中一条规则或组条目可整体编辑参数（修改应用到组内全部映射）。")
        self._enable_btn.toggled.connect(self._on_enable_toggled)
        tb_lay.addWidget(self._enable_btn)

        tb_lay.addWidget(self._make_separator())

        # 新建规则
        new_btn = _mk_toolbar_btn("新建规则", icon_key="track_add")
        new_btn.setToolTip("创建一条空白关联规则并进入编辑")
        new_btn.clicked.connect(self._new_rule)
        tb_lay.addWidget(new_btn)

        # 批量生成
        batch_btn = _mk_toolbar_btn("批量生成", icon_key="effects")
        batch_btn.setToolTip(
            "按设备组批量生成关联规则：先选设备组入围，再统一确认\n"
            "关联预设与源/目标通道类型后一次生成多条映射。")
        batch_btn.clicked.connect(self._batch_generate)
        tb_lay.addWidget(batch_btn)

        # 清空
        clear_btn = _mk_toolbar_btn("清空全部", icon_key="track_delete", danger=True)
        clear_btn.setToolTip("删除全部组条目与关联规则（弹窗二次确认）")
        clear_btn.clicked.connect(self._clear_all)
        tb_lay.addWidget(clear_btn)

        tb_lay.addStretch()

        # 状态标签
        self._status_lbl = QLabel("就绪")
        self._status_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;"
            "font-family:'Consolas','DejaVu Sans Mono',monospace;")
        tb_lay.addWidget(self._status_lbl)

        # 规则计数
        self._count_lbl = QLabel("0 条规则")
        self._count_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:11px;"
            "font-family:'Consolas','DejaVu Sans Mono',monospace;")
        tb_lay.addWidget(self._count_lbl)

        return toolbar

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        # S1 重构：删除裸 background 通配覆写（audit-B），面板底色由根容器/全局提供。
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # v1.2：轨道类型 UI 已移除——批量生成按设备组自动选择通道，
        # 类型过滤恒为"全部"（_get_active_type_filter 返回 []）

        # 设备组列表（D波：PanelSection 分区）
        grp_grp = PanelSection("设备组")
        grp_lay = QVBoxLayout()
        grp_lay.setContentsMargins(0, 0, 0, 0)
        grp_lay.setSpacing(4)

        self._group_list = QListWidget()
        self._group_list.setStyleSheet(_css_list())
        self._group_list.addItem("（全部）")
        self._group_list.setCurrentRow(0)
        self._group_list.currentRowChanged.connect(self._on_group_filter)
        grp_lay.addWidget(self._group_list)

        refresh_btn = QPushButton("↻ 刷新设备组")
        # ↻ 字形补 Segoe UI Symbol 兜底（防缺字形 tofu，纯呈现层）
        refresh_btn.setStyleSheet(_css_btn(font_size=10) +
            "QPushButton{font-family:'Segoe UI Symbol','Segoe UI',"
            "'Microsoft YaHei UI',sans-serif;}")
        refresh_btn.setToolTip("重新读取工程中的设备组列表")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_groups)
        grp_lay.addWidget(refresh_btn)
        grp_grp.add_layout(grp_lay)
        lay.addWidget(grp_grp, stretch=1)

        self._refresh_groups()
        return w

    def _build_mid_panel(self) -> QWidget:
        w = QWidget()
        # S1 重构：删除裸 background 通配覆写；底色改由滚动区 viewport 提供。
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 标题（叶子控件直接着色，避免通配影响子树）
        header = QLabel("  关联规则列表")
        header.setFixedHeight(26)
        header.setStyleSheet(
            f"background:{PANEL_BG};color:{ACCENT};"
            f"font-size:11px;font-weight:bold;"
            f"border-bottom:1px solid {SEP_LINE};")
        lay.addWidget(header)

        # D波：规则列表空态（禁止大片纯黑）——滚动区与 EmptyState 双页切换
        self._mid_stack = QStackedWidget()
        self._mid_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        scroll_page = QWidget()
        sp_lay = QVBoxLayout(scroll_page)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # D波：viewport/容器底色显式落色（QScrollArea 的 background 不落到
        # qt_scrollarea_viewport，需补直接子级选择器，否则透出原生白底）。
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{DARK_BG};}}"
            f"QScrollArea > QWidget{{background:{DARK_BG};}}")

        self._rule_container = QWidget()
        self._rule_container.setObjectName("_rule_list_body")
        self._rule_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._rule_container.setStyleSheet(
            f"QWidget#_rule_list_body{{background:{DARK_BG};}}")
        self._rule_vlay = QVBoxLayout(self._rule_container)
        self._rule_vlay.setContentsMargins(4, 4, 4, 4)
        self._rule_vlay.setSpacing(2)
        self._rule_vlay.addStretch()

        scroll.setWidget(self._rule_container)
        sp_lay.addWidget(scroll)
        self._mid_stack.addWidget(scroll_page)

        empty_page = QWidget()
        ep_lay = QVBoxLayout(empty_page)
        ep_lay.setContentsMargins(0, 0, 0, 0)
        ep_lay.addWidget(EmptyState(
            "暂无关联规则",
            "点击「新建规则」手动创建通道映射，或用「批量生成」按设备组一次生成",
            "link"))
        self._mid_stack.addWidget(empty_page)
        self._mid_stack.setCurrentIndex(0)

        lay.addWidget(self._mid_stack, stretch=1)

        return w

    def _make_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        # audit-B：QFrame VLine 上 color: 不生效，须用 background/border
        sep.setStyleSheet(f"background:{SEP_LINE};border:none;")
        return sep

    # ── 设备组刷新 ───────────────────────────────────────────────────────────

    def _refresh_groups(self):
        self._group_list.clear()
        self._group_list.addItem("（全部）")
        try:
            if hasattr(self._pm, 'fixture_groups'):
                groups = self._pm.fixture_groups or []
            elif hasattr(self._pm, 'project'):
                groups = getattr(self._pm.project, 'fixture_groups', []) or []
            else:
                groups = []
            for g in groups:
                name = g.get('name', 'Group') if isinstance(g, dict) else getattr(g, 'name', 'Group')
                self._group_list.addItem(name)
        except Exception:
            pass
        self._group_list.setCurrentRow(0)

    # ── 过滤器 ───────────────────────────────────────────────────────────────

    def _on_group_filter(self, row: int):
        self._refresh_rule_list()

    def _get_active_type_filter(self) -> List[str]:
        # v1.2：轨道类型 UI 已移除，类型过滤恒为"全部"（不过滤）
        return []

    def _get_active_group_filter(self) -> str:
        row = self._group_list.currentRow()
        if row <= 0:
            return ""
        return self._group_list.item(row).text()

    # ── 规则列表刷新 ─────────────────────────────────────────────────────────

    def _refresh_rule_list(self):
        # ★ 防御：_build_left_panel 内的 setCurrentRow(0) 会触发 _on_group_filter,
        # 但此时 _build_mid_panel 还没跑（_rule_vlay 未建）。该路径下直接 return,
        # _build_mid_panel 完成后会有正常调用补刷新。
        if not hasattr(self, "_rule_vlay") or self._rule_vlay is None:
            return
        rules = self._engine.get_rules()
        groups = self._engine.get_groups()
        type_filter = self._get_active_type_filter()
        group_filter = self._get_active_group_filter()

        # 过滤规则
        filtered = []
        for r in rules:
            # 组过滤
            if group_filter and r.group_label != group_filter:
                if group_filter not in r.group_label:
                    continue
            # 类型过滤
            if type_filter:
                src_types = [c.ch_type for c in r.src_channels]
                dst_types = [c.ch_type for c in r.dst_channels]
                if not any(t in type_filter for t in src_types + dst_types):
                    continue
            filtered.append(r)
        by_id = {r.id: r for r in filtered}

        # 清空旧控件。
        # L 波审计 #10：takeAt+deleteLater 只是**延迟**销毁——被移除的旧行仍是
        # _rule_container 的子件且保持可见（停留在 640 默认宽度），审计走父树
        # 时这些"僵尸行"按右裁切上报。先 hide + setParent(None) 立即脱离可视
        # 树与布局，再 deleteLater 兜底回收；行为/信号不受影响。
        while self._rule_vlay.count() > 1:
            item = self._rule_vlay.takeAt(0)
            w_old = item.widget()
            if w_old is not None:
                w_old.hide()
                w_old.setParent(None)
                w_old.deleteLater()
        self._rule_items.clear()
        self._folder_items.clear()

        # 按组条目渲染：组条目一行（可折叠/选中/改名/删除），组内映射收纳其下
        for g in groups:
            g_rules = [by_id[rid] for rid in g.rule_ids if rid in by_id]
            self._insert_folder_item(g.id, g.name, len(g_rules), g.expanded, "folder")
            if g.expanded:
                for rule in g_rules:
                    self._insert_rule_item(rule)

        # 未分组（手动创建 / 旧存档规则）：虚拟条目，不可删改
        grouped_ids = {rid for g in groups for rid in g.rule_ids}
        ungrouped = [r for r in filtered if r.id not in grouped_ids]
        if ungrouped:
            self._insert_folder_item(UNGROUPED_FOLDER_ID, "未分组",
                                     len(ungrouped), self._ungrouped_expanded,
                                     "ungrouped")
            if self._ungrouped_expanded:
                for rule in ungrouped:
                    self._insert_rule_item(rule)

        # 更新计数
        total = len(rules)
        enabled = sum(1 for r in rules if r.enabled)
        self._count_lbl.setText(f"{enabled}/{total} 条规则")

        # D波：空态切换（纯视觉，不影响任何业务分支）
        mid_stack = getattr(self, "_mid_stack", None)
        if mid_stack is not None:
            has_items = bool(self._rule_items) or bool(self._folder_items)
            mid_stack.setCurrentIndex(0 if has_items else 1)

    def _insert_folder_item(self, folder_id: str, name: str, count: int,
                            expanded: bool, mode: str):
        """向规则列表插入一个组条目头部行。"""
        header = FolderHeaderItem(folder_id, name, count, expanded, mode=mode)
        header.toggled.connect(self._on_folder_toggled)
        header.selected.connect(self._on_folder_selected)
        header.deleted.connect(self._on_folder_deleted)
        header.rename_requested.connect(self._on_folder_rename)
        header.set_selected(folder_id == self._selected_folder_id)
        self._rule_vlay.insertWidget(self._rule_vlay.count() - 1, header)
        self._folder_items[folder_id] = header

    def _insert_rule_item(self, rule):
        """向规则列表插入一条规则行（组条目内）。"""
        item_widget = RuleListItem(rule)
        item_widget.toggled.connect(self._on_rule_toggled)
        item_widget.selected.connect(self._on_rule_selected)
        item_widget.deleted.connect(self._on_rule_deleted)
        item_widget.moved_up.connect(lambda rid: (
            self._engine.move_rule_up(rid)))
        item_widget.moved_dn.connect(lambda rid: (
            self._engine.move_rule_down(rid)))
        item_widget.set_selected(rule.id == self._selected_rule_id)
        self._rule_vlay.insertWidget(self._rule_vlay.count() - 1, item_widget)
        self._rule_items[rule.id] = item_widget

    # ── 规则操作 ─────────────────────────────────────────────────────────────

    def _new_rule(self):
        from ...processing.track_linker_engine import LinkRule
        rule = LinkRule(name=f"规则 {len(self._engine.get_rules()) + 1}")
        self._engine.add_rule(rule)
        self._selected_rule_id = rule.id
        self._selected_folder_id = None
        self._refresh_rule_list()
        self._rule_editor.load_rule(rule)

    def _collect_batch_scopes(self, insts, group_ids: List[str],
                              default_label: str) -> Tuple[List[list], List[str]]:
        """多选入围 → 逐组范围 (scope_sets, scope_labels)。

        每组在自己的设备范围内生成（不跨组配对）；组 id 找不到或组内
        无设备时跳过。group_ids 为空 = 全部设备（label 用 default_label）。
        """
        try:
            if hasattr(self._pm, 'fixture_groups'):
                all_groups = self._pm.fixture_groups or []
            elif hasattr(self._pm, 'project'):
                all_groups = getattr(self._pm.project, 'fixture_groups', []) or []
            else:
                all_groups = []
            by_gid = {}
            for g in all_groups:
                gid = g.get('id', '') if isinstance(g, dict) else getattr(g, 'id', '')
                by_gid[gid] = g
        except Exception:
            by_gid = {}

        # 组 id → 组内设备列表（按组内 fixture_ids 顺序过滤，
        # 保证跨设备配对顺序确定：第 i 台源设备 ↔ 第 i 台目标设备）
        by_fid = {getattr(f, 'id', ''): f for f in insts}
        scope_sets: List[list] = []
        scope_labels: List[str] = []
        if group_ids:
            for gid in group_ids:
                g = by_gid.get(gid)
                if g is None:
                    continue
                fids = list(g.get('fixture_ids', []) if isinstance(g, dict)
                            else getattr(g, 'fixture_ids', []))
                group_fixtures = [by_fid[fid] for fid in fids if fid in by_fid]
                if not group_fixtures:
                    continue
                scope_sets.append(group_fixtures)
                scope_labels.append(
                    g.get('name', 'Group') if isinstance(g, dict)
                    else getattr(g, 'name', 'Group'))
        else:
            scope_sets = [insts]
            scope_labels = [default_label]
        return scope_sets, scope_labels

    def _ask_batch_sub_device(self, _preset_name: str, src_types: List[str],
                              dst_types: List[str]):
        """包内子设备多候选询问：取消则置位 _choice_cancelled（整批中止）。"""
        dlg = SubDeviceTypeDialog(src_types, dst_types, _preset_name,
                                  parent=self)
        if not popup(dlg).exec():
            self._choice_cancelled = True
            return None
        return dlg.get_result()

    def _batch_generate(self):
        dlg = BatchGenerateDialog(self._pm, parent=self)
        if not popup(dlg).exec():
            return
        result = dlg.get_result()
        if not result:
            return

        # 获取设备信息
        # 通过统一助手访问 pm，避免属性命名不一致导致拿不到设备
        insts, defs = _resolve_pm_fixtures(self._pm)

        # 多选入围：选中的多个设备组逐组独立生成（组内配对，不跨组），
        # 合并为一批规则；空列表（或旧版 group_id=None）= 全部设备。
        group_ids = result.get("group_ids")
        if group_ids is None:
            # 兼容旧版 result（单选）：group_id 为 None = 全部设备
            group_ids = [result.get("group_id")] if result.get("group_id") else []
        group_ids = [gid for gid in (group_ids or []) if gid]

        scope_sets, scope_labels = self._collect_batch_scopes(
            insts, group_ids, result.get("group_name") or "全部设备")

        # 包内子设备多候选询问（"再多一步询问"）：全局候选类型 >1 时弹出，
        # 用户明确选择源/目标子设备；取消则整体中止，不生成错误规则。
        from ...processing.track_linker_engine import PRESET_BY_ID
        _pdef = PRESET_BY_ID.get(result["preset_id"])
        _preset_name = _pdef.name if _pdef else (result["preset_id"] or "关联")
        logical_devices = _build_logical_devices(self._pm)
        self._choice_cancelled = False

        # 逐组生成（每组独立配对），任一组取消子设备询问 → 整批中止
        rules = []
        for _scope, label in zip(scope_sets, scope_labels):
            batch = self._engine.build_rules_from_group(
                fixture_instances=_scope,
                fixture_defs=defs,
                preset_id=result["preset_id"],
                src_ch_type=result["src_type"],
                dst_ch_type=result["dst_type"],
                params=result["params"],
                group_label=label,
                logical_devices=logical_devices,
                resolve_choice=lambda s, d: self._ask_batch_sub_device(
                    _preset_name, s, d),
            )
            if getattr(self, "_choice_cancelled", False):
                rules = []
                break
            rules.extend(batch)

        # ★★ 用户定案（2026-09-17）：批量生成后**按设备类型聚合**成一个组条目，
        #   不再一台设备一行平铺（与调度台同观感）；同一类型**可叠加不冲突的
        #   多个预设**（组名相同 → 追加进同一组，各自参数独立保留）。
        #   组名 = 设备类型名；多类型一起生成时按类型各建一组。
        created_gid, _skipped, _overlap = _store_batch_by_type(
            self._engine, rules, defs)

        if rules:
            self._status_lbl.setText(
                _batch_status_text(len(rules), _skipped, len(_overlap)))
        else:
            self._status_lbl.setText(
                _no_rules_hint(result, scope_sets, defs,
                               getattr(self, "_choice_cancelled", False)))
        if rules and created_gid:
            # 生成后选中组条目 → 编辑器进入整体编辑模式（改参数应用到组内全部）
            self._selected_folder_id = created_gid
            self._selected_rule_id = None
            self._refresh_rule_list()
            _gs = self._engine.get_rules_in_group(created_gid)
            self._rule_editor.load_rules(_gs or rules)

    def _clear_all(self):
        if QMessageBox.question(
            self, "清空确认", "确定要清空所有关联规则吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self._engine.clear_rules()
            self._selected_rule_id = None
            self._selected_folder_id = None
            self._rule_editor.clear()

    def _on_rule_toggled(self, rule_id: str, enabled: bool):
        rules = self._engine.get_rules()
        for r in rules:
            if r.id == rule_id:
                r.enabled = enabled
                self._engine.update_rule(r)
                break

    def _on_rule_selected(self, rule_id: str):
        self._selected_rule_id = rule_id
        self._selected_folder_id = None
        # 更新选中状态
        for rid, item in self._rule_items.items():
            item.set_selected(rid == rule_id)
        for fid, item in self._folder_items.items():
            item.set_selected(False)
        # 加载到编辑器
        rules = self._engine.get_rules()
        for r in rules:
            if r.id == rule_id:
                self._rule_editor.load_rule(r)
                break

    # ── 组条目（文件夹）操作 ────────────────────────────────────────────────

    def _make_batch_folder_name(self, group_name: str, preset_name: str) -> str:
        """批量生成的组条目命名：组名·预设名；重名自动追加序号。"""
        base = f"{group_name}·{preset_name}"
        existing = {g.name for g in self._engine.get_groups()}
        name, n = base, 2
        while name in existing:
            name = f"{base} ({n})"
            n += 1
        return name

    def _on_folder_selected(self, folder_id: str):
        self._selected_folder_id = folder_id
        self._selected_rule_id = None
        self._rule_editor._group_id = ("" if folder_id == UNGROUPED_FOLDER_ID
                                       else str(folder_id))
        for fid, item in self._folder_items.items():
            item.set_selected(fid == folder_id)
        for rid, item in self._rule_items.items():
            item.set_selected(False)
        # 组条目整体编辑：组内全部映射加载到编辑器
        if folder_id == UNGROUPED_FOLDER_ID:
            grouped_ids = {rid for g in self._engine.get_groups() for rid in g.rule_ids}
            rules = [r for r in self._engine.get_rules() if r.id not in grouped_ids]
        else:
            rules = self._engine.get_rules_in_group(folder_id)
        self._rule_editor.load_rules(rules)

    def _on_folder_toggled(self, folder_id: str, expanded: bool):
        if folder_id == UNGROUPED_FOLDER_ID:
            self._ungrouped_expanded = expanded
            self._refresh_rule_list()
        else:
            self._engine.set_group_expanded(folder_id, expanded)

    def _on_folder_deleted(self, folder_id: str):
        if folder_id == UNGROUPED_FOLDER_ID:
            return
        group = next((g for g in self._engine.get_groups() if g.id == folder_id),
                     None)
        if group is None:
            return
        if QMessageBox.question(
            self, "删除组条目",
            f"确定删除组条目「{group.name}」及其 {len(group.rule_ids)} 条映射吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._selected_folder_id == folder_id:
            self._selected_folder_id = None
            self._rule_editor.clear()
        self._engine.remove_group(folder_id, delete_rules=True)

    def _on_folder_rename(self, folder_id: str):
        if folder_id == UNGROUPED_FOLDER_ID:
            return
        group = next((g for g in self._engine.get_groups() if g.id == folder_id),
                     None)
        if group is None:
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "重命名组条目",
                                        "新名称：", text=group.name)
        if ok and name.strip():
            self._engine.rename_group(folder_id, name.strip())

    def _on_add_preset_to_group(self, group_id: str) -> None:
        """给已有的类型组**再叠加一套预设**（用户定案 2026-09-18）。

        交互：弹出的预设选择框（复用批量生成的第 2 步表单语义）→ 按该组内
        已有规则涉及的设备重新生成一套规则 → **追加进同一个组条目**
        （参数独立、互不覆盖；冲突的目标通道在状态栏提示）。

        与批量生成的差别：这里设备范围来自**组内已有规则**（同一套设备），
        不再让用户重选设备组 —— 用户心智是"给这台设备再加一条关联"。
        """
        group = next((g for g in self._engine.get_groups() if g.id == group_id),
                     None)
        if group is None:
            return
        anchor = self._engine.get_rules_in_group(group_id)
        if not anchor:
            return
        preset_id = self._ask_preset_for_group(group, anchor)
        if not preset_id:
            return
        from ...processing.track_linker_engine import PRESET_BY_ID
        pdef = PRESET_BY_ID.get(preset_id)
        insts, defs = _resolve_pm_fixtures(self._pm)
        fid_set = {str(getattr(r, "fixture_id", "") or "")
                   for rule in anchor
                   for r in (list(rule.src_channels) + list(rule.dst_channels))}
        scope = [f for f in insts if str(getattr(f, "id", "")) in fid_set]
        if not scope:
            self._status_lbl.setText("无法叠加：组内设备已不在工程中")
            return
        src_key = anchor[0].src_channels[0].ch_type if anchor[0].src_channels else ""
        dst_key = anchor[0].dst_channels[0].ch_type if anchor[0].dst_channels else ""
        try:
            rules = self._engine.build_rules_from_group(
                fixture_instances=scope, fixture_defs=defs,
                preset_id=preset_id, src_ch_type=src_key, dst_ch_type=dst_key,
                params=_preset_default_params(pdef), group_label=group.name,
                logical_devices=_build_logical_devices(self._pm),
                resolve_choice=lambda s, d: self._ask_batch_sub_device(
                    getattr(pdef, "name", preset_id), s, d))
        except Exception:
            log.exception("叠加预设失败")
            self._status_lbl.setText("叠加预设失败（详见日志）")
            return
        skip = getattr(self._engine, "last_group_add_skipped", ()) or ()
        added = self._engine.add_rules_to_type_group(rules, group.name)
        total = len(self._engine.get_rules_in_group(group.id))
        self._selected_folder_id = group.id
        self._refresh_rule_list()
        self._rule_editor.load_rules(self._engine.get_rules_in_group(group.id))
        self._status_lbl.setText(_stack_status_text(
            getattr(pdef, "name", preset_id), len(rules) - len(skip),
            len(skip), total))

    def _on_remove_preset_from_group(self, group_id: str, preset_id: str,
                                     rule_ids: list) -> None:
        """从组里摘掉一套预设：删除其规则（组条目与其它预设保留）。"""
        for rid in list(rule_ids or ()):
            self._engine.remove_rule(rid)
        remain = self._engine.get_rules_in_group(group_id) if group_id else []
        self._refresh_rule_list()
        if remain:
            self._selected_folder_id = group_id
            self._rule_editor.load_rules(remain)
            self._status_lbl.setText(
                f"已移除该预设：本组还剩 {len(remain)} 条映射")
        else:
            self._engine.remove_group(group_id, delete_rules=False)                 if group_id else None
            self._selected_folder_id = None
            self._rule_editor.clear()
            self._status_lbl.setText("已移除最后一个预设（组条目已清空）")

    def _ask_preset_for_group(self, group, anchor) -> str:
        """弹预设选择（复用编辑器预设下拉的候选集），返回 preset_id 或 ""。"""
        from ...processing.track_linker_engine import BUILTIN_LINK_PRESETS
        have = {str(r.preset_id) for r in anchor}
        items = [(f"{p.name}（已在此组）" if p.id in have else p.name, p.id)
                 for p in BUILTIN_LINK_PRESETS]
        labels = [lab for lab, _ in items]
        cur = 0
        choice, ok = QInputDialog.getItem(
            self, "叠加预设", f"给「{group.name}」再加一套预设：",
            labels, cur, False)
        if not ok or not choice:
            return ""
        for lab, pid in items:
            if lab == choice:
                return pid
        return ""

    def _on_rules_batch_edited(self, rules):
        """组条目整体编辑：同步引擎（update_rules 触发列表刷新）。"""
        self._engine.update_rules(rules)

    def _on_rule_deleted(self, rule_id: str):
        self._engine.remove_rule(rule_id)
        if self._selected_rule_id == rule_id:
            self._selected_rule_id = None
            self._rule_editor.clear()

    def _on_rule_edited(self, rule):
        """规则编辑器修改后同步到引擎。"""
        self._engine.update_rule(rule)
        # 刷新列表项显示
        if rule.id in self._rule_items:
            self._rule_items[rule.id].refresh(rule)

    # ── 启用/停用 ────────────────────────────────────────────────────────────

    def _on_enable_toggled(self, v: bool):
        self._engine.set_enabled(v)
        self._sync_enable_ui()
        self.report_to_bus(force=True)   # ★ R130：停用即摘窗格（否则被下次存场景抄走）

    def _sync_enable_ui(self):
        running = bool(self._engine.is_enabled)
        if self._enable_btn.isChecked() != running:
            old = self._enable_btn.blockSignals(True)
            self._enable_btn.setChecked(running)
            self._enable_btn.blockSignals(old)
        if running:
            self._enable_btn.setText("关联器运行中")
            self._status_lbl.setText("运行中")
        else:
            self._enable_btn.setText("启用关联器")
            self._status_lbl.setText("已停止")

    # ── 状态更新 ─────────────────────────────────────────────────────────────

    def _update_status(self):
        if self._engine.is_enabled:
            rules = self._engine.get_rules()
            enabled = sum(1 for r in rules if r.enabled)
            total   = len(rules)
            self._status_lbl.setText(f"运行中  {enabled}/{total} 条规则激活")
        else:
            self._status_lbl.setText("已停止")
        # 调度台上报（节流在 report_to_bus 内部；失败不影响输出）
        self.report_to_bus()

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：关联器总线上报（唯一出口）
    #
    #  为什么在这里：一条启用的关联规则 = 一个"效果制作节点"（源→预设→目标）。
    #
    #  ★ 身份契约：一窗格 = (panel, track_type, group_key)。track_type = 规则 id，
    #  group_key 由规则源/目标设备 id 内容寻址 → "同规则不同设备""不同规则"天然
    #  两格；**绝不按预设/名称/分类合并**（用户：微差即两种效果）。
    # ══════════════════════════════════════════════════════════════════════════

    #: 上报节流（秒）：状态定时器 2Hz，总线只需人眼刷新率级别
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
        """把当前关联规则转成调度台窗格（一条启用规则一节点，不去重）。"""
        from ...processing.dispatch import EffectNode
        nodes: List[EffectNode] = []
        try:
            enabled = bool(self._engine.is_enabled)
            # ★ R130 用户实报（"已经没有启用了…在输出那边看不到却被存新效果时存进去"）：
            #   全局停用 = 定时器已停 + linker 层已清 → 输出侧无关联效果，规则不得成窗格
            #   （IfExp 不计门禁 CC；空集与 `report_to_bus` 差集删除配合摘格）。
            for rule in ((self._engine.get_rules() or []) if enabled else []):
                if not bool(getattr(rule, "enabled", False)):
                    continue
                src_ch = list(getattr(rule, "src_channels", []) or [])
                both = src_ch + list(getattr(rule, "dst_channels", []) or [])
                fixture_ids = list(dict.fromkeys(
                    [str(c.fixture_id) for c in both if getattr(c, "fixture_id", "")]))
                ch_types = list(dict.fromkeys(
                    [str(c.ch_type) for c in both if getattr(c, "ch_type", "")]))
                nodes.append(EffectNode(
                    panel="linker",
                    track_type=str(getattr(rule, "id", "") or ""),
                    group_key=self._dispatch_group_key(fixture_ids),
                    group_name=str(getattr(rule, "group_label", "") or "") or "关联规则",
                    fixture_ids=fixture_ids,
                    effect_id=str(getattr(rule, "preset_id", "") or ""),
                    effect_name=str(getattr(rule, "name", "") or "") or "关联规则",
                    category="", layers=["linker"],
                    summary=f"通道 {'/'.join(ch_types[:3])} · {len(fixture_ids)} 台",
                    live=enabled,
                ))
        except Exception:
            log.debug("track_linker: _bus_nodes failed", exc_info=True)
        return nodes

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前关联规则窗格到总线（节流；失败绝不影响关联输出）。"""
        try:
            import time as _t
            now = _t.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ...processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 逐键 upsert + 差集删除：停用/删除规则时只删它自己的格，不整体 clear_panel
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    def dispatch_capture_state(self) -> dict:
        """给调度台捕获可完整还原的面板状态（复用面板既有 save_state → 引擎 to_dict）。"""
        try:
            return dict(self.save_state())
        except Exception:
            return {}

    def dispatch_release_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """**释放**关联（关闭场景时调用）：停引擎输出，清 linker 层。

        ★★ 2026-09-14 深查修正：本面板此前只有 capture/recall、**没有 release**，
          而释放路由 `dispatch_flow.release_one_node` 对"无 release 回调"是
          **静默当作成功** → 关闭含关联的场景后，引擎 25ms 定时器仍在写 linker 层
          → 值下一拍就回来（"关不掉"）。复用引擎既有 `set_enabled(False)`
          （停定时器 + 清层），不新增第二套停止逻辑。
        """
        try:
            eng = getattr(self, "_engine", None)
            if eng is not None and hasattr(eng, "set_enabled"):
                eng.set_enabled(False)
            try:
                self.report_to_bus(force=True)
            except Exception:
                pass
            return True
        except Exception:
            log.debug("track_linker: dispatch_release_state failed", exc_info=True)
            return False

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """按调度台节点还原关联规则：复用 load_state（规则表整体往返），再上报。"""
        try:
            node = dict(node or {})
            if state:
                self.load_state(dict(state))
            self.report_to_bus(force=True)     # 立即反映到调度台
            return True
        except Exception:
            log.debug("track_linker: dispatch_recall_state failed", exc_info=True)
            return False

    # ── 序列化（供项目保存/加载使用）────────────────────────────────────────

    def save_state(self) -> dict:
        return self._engine.to_dict()

    def load_state(self, d: dict):
        # ★★ 2026-09-14 深查修正：原实现直接 `self._engine.from_dict(d)` →
        #   `load_state(None)` 抛 AttributeError（`None.get`）。其它面板的 load_state
        #   都能容忍 None/空，这个不能 —— 任何转发未校验 None/JSON null 的调用方都会崩。
        if not d or not isinstance(d, dict):
            return
        self._engine.from_dict(d)
        self._sync_enable_ui()
        self._refresh_rule_list()
