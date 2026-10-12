# -*- coding: utf-8 -*-
"""programmer_highlight — Programmer 面板操作集：试灯 / 后选过滤 / 排序 / 步进 /
三级释放 / hover 文本 / 摇杆路径送轨迹（R183 批次 I）。

为什么单独一个模块：`programmer_panel.py` 的 `ProgrammerPanel` 是**冻结上帝类**
（CLAUDE.md §10.1：不得新增独立方法），新逻辑必须落在新模块里，面板侧只在既有
方法体内加"注入行"。

全部函数都是**鸭子类型**操作面板/行对象（不 import programmer_panel），因此可在
pytest 里用轻量假面板直接断言。

语义要点：
  · Highlight 试灯 —— Post Selection 官方页原文："Highlight temporarily
    opens the fixture fully without writing values"；写 `tester` 临时层（见
    `core.constants.BASE_LAYERS`），**不写 programmer 层**。
  · Post-Selection 后选过滤 —— "temporary filter to your current
    selection… Clicking All restores"。
  · Panorama Alignment —— 翼方向自动按物理 x 坐标（TTM 设备均带 pos_x）。
  · Step —— ："Step buttons… move sequentially through the fixtures"。
  · Clear 三级释放 —— 分级清除语义 / 分级释放：分级下台，不误清全组。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Highlight 临时层（与 signal_tester 同层）。同层冲突窗口极小——用户点试灯时不会
#: 同时在信号测试器里跑图案；关闭时只清自己写过的地址（clear_layer_channels），
#: 不整层抹掉其它写进 tester 的内容。
HIGHLIGHT_LAYER = "tester"
#: 试灯通道优先级：dimmer 优先（纯白全开）；无 dimmer 的纯色灯写它支持的色通道全 255。
HIGHLIGHT_CHANNELS = ("dimmer", "R", "G", "B", "W")


# ── 通用小工具 ────────────────────────────────────────────────────────────

def _supports(fd: Any, ch_type: str) -> bool:
    """能力检测复用既有口径（`_fixture_supports_logical_channel`）。"""
    try:
        from .programmer_panel import _fixture_supports_logical_channel
        return bool(_fixture_supports_logical_channel(fd, ch_type))
    except Exception:
        return False


def _addr_list_for(panel: Any, fid: str, ch_type: str) -> List[Tuple[int, int]]:
    """取 (设备, 通道) 的 [(universe, addr)]；dimmer 回退同名键（虚拟亮度）。"""
    fid_cache = (getattr(panel, "_addr_cache", {}) or {}).get(str(fid))
    if not fid_cache:
        return []
    addrs = fid_cache.get(ch_type) or fid_cache.get("dimmer")
    out: List[Tuple[int, int]] = []
    for item in (addrs or ()):
        try:
            uni, addr = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= addr < 512:
            out.append((uni, addr))
    return out


def status(panel: Any, message: str, timeout: int = 3000) -> None:
    """状态提示（面板 signal → 主窗状态栏）；提示失败绝不影响主流程。"""
    try:
        panel.status_feedback.emit(str(message), int(timeout))
    except Exception:
        try:
            st = getattr(panel.window(), "_status", None)
            if st is not None and hasattr(st, "set_message"):
                st.set_message(str(message))
        except Exception:
            pass


def _fixtures_by_id(panel: Any) -> Dict[str, Any]:
    return {str(getattr(f, "id", "")): f
            for f in (getattr(panel, "_current_fixtures", []) or [])}


def selected_ids(panel: Any) -> List[str]:
    """当前选中设备 id 列表。

    ★ `_current_fixtures_selected` 存的是 **FixtureInstance 对象**（不是 id 字符串），
      必须先取 `.id` —— 直接 `str(obj)` 会得到 dataclass 的 repr（旧实现即踩在这）。
    """
    raw = getattr(panel, "_current_fixtures_selected", []) or []
    out: List[str] = []
    for item in raw:
        fid = item if isinstance(item, str) else getattr(item, "id", None)
        sfid = str(fid or "")
        if sfid:
            out.append(sfid)
    return out


def group_base_ids(panel: Any) -> List[str]:
    """后选过滤的原实际选区；母序追溯不构成可选设备。"""
    if bool(getattr(panel, "_post_filter_active", False)):
        return [str(x) for x in (getattr(panel, "_post_filter_base", []) or []) if str(x)]
    return selected_ids(panel)


# ═════════════════════════════════════════════════════════════════════════════
#  项 3 · Highlight 试灯
# ═════════════════════════════════════════════════════════════════════════════

def _highlight_channels_for(panel: Any, fid: str, fd: Any) -> List[str]:
    """该设备试灯该写哪些通道：有 dimmer 只写 dimmer；否则写它支持的色通道。"""
    if _supports(fd, "dimmer") and _addr_list_for(panel, fid, "dimmer"):
        return ["dimmer"]
    return [ct for ct in HIGHLIGHT_CHANNELS
            if ct != "dimmer" and _supports(fd, ct)]


def collect_highlight_addrs(panel: Any,
                            ids: Optional[Sequence[str]] = None) -> Dict[int, Dict[int, int]]:
    """选中设备 → `{universe: {addr: 255}}` 的白场全开地址表。

    有 dimmer 的设备只写 dimmer（同时写 R/G/B 会改色相，而试灯要的是白场全开）；
    无 dimmer 的纯色灯写它支持的 R/G/B/W 全部 255。返回空 = 没有可试灯设备。
    """
    fixtures = _fixtures_by_id(panel)
    defs = getattr(panel, "_fixture_defs_cache", {}) or {}
    wanted = [str(x) for x in (ids if ids is not None else selected_ids(panel)) if str(x)]
    out: Dict[int, Dict[int, int]] = {}
    for fid in wanted:
        fd = defs.get(getattr(fixtures.get(fid), "def_id", ""))
        if fd is None:
            continue
        for ct in _highlight_channels_for(panel, fid, fd):
            for uni, addr in _addr_list_for(panel, fid, ct):
                out.setdefault(uni, {})[addr] = 255
    return out


def highlight_on(panel: Any, ids: Optional[Sequence[str]] = None) -> int:
    """打开试灯：写 tester 临时层全 255（**不写 programmer 层**）。

    返回写入通道数；0 = 空选 / 无 NM / 设备不支持（安全 no-op + 状态提示）。
    """
    nm = getattr(panel, "_nm_ref", None)
    batch = collect_highlight_addrs(panel, ids)
    if not batch:
        status(panel, "试灯：当前选择里没有可试灯的设备（空选或设备无灯通道）")
        return 0
    if nm is None or not hasattr(nm, "update_layer_sparse_batch"):
        status(panel, "试灯：NetworkManager 未接线，无法输出")
        return 0
    try:
        nm.update_layer_sparse_batch(HIGHLIGHT_LAYER, batch)
    except Exception:
        status(panel, "试灯：写入失败（NetworkManager 异常）")
        return 0
    addrs = [(uni, addr) for uni, chans in batch.items() for addr in chans]
    panel._highlight_addrs = addrs
    panel._highlight_on = True
    status(panel, f"试灯 ON：{len(addrs)} 个通道全开（临时层 tester，未写程序值）")
    return len(addrs)


def highlight_off(panel: Any) -> int:
    """关闭试灯：只清自己写过的地址（不动他人写在 tester 层的内容）。"""
    nm = getattr(panel, "_nm_ref", None)
    addrs = list(getattr(panel, "_highlight_addrs", []) or [])
    panel._highlight_on = False
    panel._highlight_addrs = []
    if not addrs:
        status(panel, "试灯 OFF")
        return 0
    if nm is None or not hasattr(nm, "clear_layer_channels"):
        status(panel, "试灯 OFF（NetworkManager 未接线，未清通道）")
        return 0
    by_uni: Dict[int, List[int]] = {}
    for uni, addr in addrs:
        by_uni.setdefault(int(uni), []).append(int(addr))
    cleared = 0
    for uni, chans in by_uni.items():
        try:
            uniq = sorted(set(chans))
            nm.clear_layer_channels(HIGHLIGHT_LAYER, uni, uniq)
            cleared += len(uniq)
        except Exception:
            continue
    status(panel, f"试灯 OFF：已还原 {cleared} 个通道")
    return cleared


def highlight_toggle(panel: Any, checked: Optional[bool] = None) -> bool:
    """Highlight 钮统一入口：None=翻转 / True=开 / False=关。返回最终状态。"""
    want = (not bool(getattr(panel, "_highlight_on", False))) if checked is None else bool(checked)
    if want:
        return highlight_on(panel) > 0
    highlight_off(panel)
    return False


# ═════════════════════════════════════════════════════════════════════════════
#  项 4 · Post-Selection 后选过滤
# ═════════════════════════════════════════════════════════════════════════════

def apply_post_filter(panel: Any, universe: Optional[int] = None,
                      def_ids: Optional[Sequence[str]] = None,
                      addr_lo: int = 0, addr_hi: int = 512) -> int:
    """按三谓词筛当前选中集 → 同组 id 重新 load（保留 base_order_ids 原全集序）。

    返回过滤后可见台数；无选中 → 0（no-op + 提示）。
    """
    from .programmer_tools import build_filter_predicate, apply_post_selection_filter
    base = group_base_ids(panel)
    if not base:
        status(panel, "后选过滤：当前没有选中设备")
        return 0
    defs = getattr(panel, "_fixture_defs_cache", {}) or {}
    pred = build_filter_predicate(universe=universe, def_ids=def_ids,
                                  addr_lo=addr_lo, addr_hi=addr_hi, defs=defs)
    kept = apply_post_selection_filter(base, _fixtures_by_id(panel), pred)
    panel._post_filter_active = True
    panel._post_filter_ids = list(kept)
    panel._post_filter_base = list(base)
    reference = list(getattr(panel, "_current_base_order_ids", []) or base)
    panel.load_fixture_group(str(getattr(panel, "_current_group_id", "__all__") or "__all__"),
                             kept, str(getattr(panel, "_current_group_name", "") or ""),
                             base_order_ids=reference)
    status(panel, f"后选过滤：{len(kept)}/{len(base)} 台（All 一键还原）")
    return len(kept)


def clear_post_filter(panel: Any) -> int:
    """「All」还原过滤前的实际选区；独立母序仍供相位与编辑值追溯。"""
    base = [str(x) for x in (getattr(panel, "_post_filter_base", []) or []) if str(x)]
    reference = list(getattr(panel, "_current_base_order_ids", []) or base)
    panel._post_filter_active = False
    panel._post_filter_ids = []
    panel._post_filter_base = []
    if not base:
        status(panel, "后选过滤：已是全部")
        return len(selected_ids(panel))
    panel.load_fixture_group(str(getattr(panel, "_current_group_id", "__all__") or "__all__"),
                             base, str(getattr(panel, "_current_group_name", "") or ""),
                             base_order_ids=reference)
    status(panel, f"后选过滤已还原：{len(base)} 台")
    return len(base)


# ═════════════════════════════════════════════════════════════════════════════
#  项 6 · Panorama Alignment
# ═════════════════════════════════════════════════════════════════════════════

def panorama_sort(panel: Any) -> int:
    """当前组按物理 x 升序重排（同组 id、同 base_order_ids）。

    数据源优先 `panel._fixture_positions`（TopView 同步真源），回退 `fixture.pos_x`；
    全缺 → 提示不可用并原样返回 0（不改动任何东西）。
    """
    from .programmer_tools import sort_ids_by_pos_x
    ids = selected_ids(panel)
    if not ids:
        status(panel, "按位置排序：当前没有选中设备")
        return 0
    ordered, missing = sort_ids_by_pos_x(ids, getattr(panel, "_fixture_positions", None) or {},
                                        _fixtures_by_id(panel))
    if missing >= len(ordered):
        status(panel, "按位置排序：当前设备没有 x 坐标数据（先在 TopView 同步布局）")
        return 0
    panel.load_fixture_group(str(getattr(panel, "_current_group_id", "__all__") or "__all__"),
                             ordered, str(getattr(panel, "_current_group_name", "") or ""),
                             base_order_ids=ordered)
    tail = f"（{missing} 台缺坐标排在末尾）" if missing else ""
    status(panel, f"按位置排序：已按 pos_x 升序重排 {len(ordered)} 台{tail}")
    return len(ordered)


# ═════════════════════════════════════════════════════════════════════════════
#  项 5 · Step 步进
# ═════════════════════════════════════════════════════════════════════════════

def _focus_index(panel: Any) -> int:
    """当前步进焦点（**不能用 `or -1`**：合法下标 0 会被 or 吞成 -1 → 永远停在第一台前）。"""
    raw = getattr(panel, "_step_focus_index", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _show_step_target(panel: Any, fid: str) -> None:
    """显式 Step 更新画布焦点；被动 TopView 反驱仍不得回吐选择。"""
    if bool(getattr(panel, "_suppress_topview_selection_emit", False)):
        return
    if str(getattr(panel, "_current_group_id", "")) != "__topview_live__":
        panel._emit_fixture_selection_to_topview([fid])
        return
    # Live 模式的通用回写闸门会拦这次用户动作。走原外部选择 API，
    # emit=False 保持 Programmer 的完整 Step 列表，不引发单台反向重载。
    topview = getattr(panel, "_topview_ref", None)
    if topview is not None:
        topview.set_external_fixture_selection([fid], emit=False)
    panel.fixture_selection_changed.emit([fid])


def step_focus(panel: Any, delta: int) -> int:
    """组内步进焦点设备 + 单台高亮到 TopView（编辑语义仍按既有组模式，不改写值通路）。"""
    from .programmer_tools import step_target_index
    ids = selected_ids(panel)
    total = len(ids)
    if total <= 0:
        status(panel, "步进：当前没有选中设备")
        return -1
    idx = step_target_index(_focus_index(panel), total, delta)
    if idx < 0:
        return -1
    panel._step_focus_index = idx
    fid = ids[idx]
    fx = _fixtures_by_id(panel).get(fid)
    try:
        _show_step_target(panel, fid)
    except Exception:
        pass
    status(panel, f"{idx + 1}/{total} {str(getattr(fx, 'name', '') or fid)}", 2500)
    return idx


def step_and_show(panel: Any, delta: int) -> int:
    """步进 + 同步工具栏 "i/N" 标签（按钮点击入口）。"""
    idx = step_focus(panel, delta)
    lbl = getattr(panel, "_step_label", None)
    if lbl is not None:
        try:
            lbl.setText(step_label(panel))
        except Exception:
            pass
    return idx


def step_label(panel: Any) -> str:
    """工具栏 "i/N" 标签文本（无组/未开始 → "0/N"）。"""
    total = len(selected_ids(panel))
    idx = _focus_index(panel)
    return f"{idx + 1 if 0 <= idx < total else 0}/{total}"


def refresh_step_label(panel: Any) -> None:
    """组重载后刷新 "i/N" 标签并复位焦点（下一次 ◀ 从第 1 台开始）。"""
    panel._step_focus_index = -1
    lbl = getattr(panel, "_step_label", None)
    if lbl is not None:
        try:
            lbl.setText(step_label(panel))
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  项 14 · Clear 三级释放
# ═════════════════════════════════════════════════════════════════════════════

def release_selected_channels(panel: Any) -> int:
    """① 释放选中通道：当前组每行的每个 ch_type 走行级 `_release_strip_layers`。"""
    rows = getattr(getattr(panel, "_ch_list", None), "_rows", None) or []
    released = 0
    for row in rows:
        for ct in list(getattr(row, "_ch_types", []) or []):
            try:
                row._release_strip_layers(str(ct))
                released += 1
            except Exception:
                continue
    status(panel, f"释放选中通道：{released} 个通道行已清层")
    return released


def _clear_row_layers_for(row: Any, ids: List[str]) -> None:
    """清行内四层 + `_values` + `_marked` 里本次作用的设备（其余保持不动）。"""
    try:
        for layer_name in list(getattr(row, "_layers", {}) or {}):
            per_fid = row._layers.get(layer_name) or {}
            for fid in ids:
                per_fid.pop(fid, None)
        for fid in ids:
            row._values.pop(fid, None)
            row._marked.pop(fid, None)
    except Exception:
        pass


def _clear_row_nm_for(panel: Any, row: Any, ids: List[str]) -> None:
    """该行涉及本次设备时，清它在各层的 NM 调用 + 行内缓存。

    缓存必须一起清，否则 keepalive 下一拍会把旧值灌回（R130 既有教训）。
    """
    if not (set(ids) & set(getattr(row, "_fixture_id_set", set()) or ())):
        return
    for ct in list(getattr(row, "_ch_types", []) or []):
        try:
            row._clear_nm_layer_channels(str(ct))
        except Exception:
            continue
    _clear_row_layers_for(row, ids)


def release_selected_fixtures(panel: Any) -> int:
    """② 释放选中设备：清这些设备在各层的调用（NM + 行内缓存同步）。"""
    ids = selected_ids(panel)
    if not ids:
        status(panel, "释放选中设备：当前没有选中设备")
        return 0
    for row in (getattr(getattr(panel, "_ch_list", None), "_rows", None) or []):
        _clear_row_nm_for(panel, row, ids)
    status(panel, f"释放选中设备：{len(ids)} 台的调用层已清")
    return len(ids)


def release_all(panel: Any) -> None:
    """③ 全部释放：既有 `clear_live_output` 全链路（定时器 / 层 / 总线摘窗格）。"""
    try:
        panel.clear_live_output()
    except Exception:
        pass
    status(panel, "全部释放：Programmer 输出已完全停止")


def show_release_menu(panel: Any, button: Any, pos: Any) -> None:
    """Release 钮右键菜单：Clear 三级（选中通道 / 选中设备 / 全部）。"""
    from PyQt6.QtWidgets import QMenu
    menu = QMenu(button)
    act_channels = menu.addAction("① 释放选中通道（当前组各通道行）")
    act_fixtures = menu.addAction("② 释放选中设备（当前组这些设备）")
    menu.addSeparator()
    act_all = menu.addAction("③ 全部释放（整个 Programmer 输出）")
    chosen = menu.exec(button.mapToGlobal(pos))
    if chosen is act_channels:
        release_selected_channels(panel)
    elif chosen is act_fixtures:
        release_selected_fixtures(panel)
    elif chosen is act_all:
        release_all(panel)


# ═════════════════════════════════════════════════════════════════════════════
#  项 16 · hover 值气泡
# ═════════════════════════════════════════════════════════════════════════════

def _row_cell_value(row: Any, fid: str, ct: str) -> Any:
    """该格当前值：先取 `_values` 快照，缺则回退合并值（都取不到 → None）。"""
    try:
        raw = (row._values.get(fid) or {}).get(ct)
    except Exception:
        raw = None
    if raw is not None:
        return raw
    try:
        return row._compute_merged_value(fid, ct)
    except Exception:
        return None


def hover_cell_text(row: Any, logical_index: int, ch_type: str = "") -> str:
    """条带 hover：按逻辑 slot 反查该位置设备名 + 该通道当前值（双读数）。

    无槽位/越界 → ""（调用方 no-op，不弹空气泡）。
    """
    from .programmer_tools import format_value_dual
    ct = str(ch_type or "")
    if not ct:
        ch_types = list(getattr(row, "_ch_types", []) or [])
        ct = str(ch_types[0]) if ch_types else ""
    try:
        fixtures = row._ordered_fixtures_for_channel(ct)
    except Exception:
        fixtures = []
    if not fixtures or not (0 <= int(logical_index) < len(fixtures)):
        return ""
    fi = fixtures[int(logical_index)]
    fid = str(getattr(fi, "id", ""))
    name = str(getattr(fi, "name", "") or fid)
    raw = _row_cell_value(row, fid, ct)
    return f"{name}  [{ct}]  " + ("—" if raw is None else format_value_dual(raw))
