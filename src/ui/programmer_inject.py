# -*- coding: utf-8 -*-
"""programmer_inject — Programmer 面板 R183 批次 I 的注入助手（模块级）。

面板侧受架构门禁约束（§10.1 上帝类不得新增方法；新函数 ≤100 行 / CC ≤8 /
位置参数 ≤5），故新逻辑全部落在模块级函数里、面板只留一行调用。本模块 = 注入点清单。

注入点（面板文件内按函数名搜索）：`_r183_init_state` / `_r183_build_toolbar_extras` /
`_r183_build_left_column` / `_r183_build_ch_column` / `_r183_after_group_load` /
`_r183_narrow_to_post_filter` / `_r183_add_color_mix_button` /
`_r183_add_row_align_controls` / `_r183_add_joystick_record` /
`_r183_show_row_context_menu` / `_r183_prepare_range_write` /
`_r183_strip_hover_tooltip` / `_r183_strip_label_text` / `_r183_set_range_readout`
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from . import programmer_color_mix as _pcm
from . import programmer_highlight as _phl
from . import programmer_palette as _ppal
from . import programmer_sheet as _psheet
from . import programmer_tools as _ptool
from .programmer_shared_state_ui import mirror_jitter_rows, sync_jitter_row as sync_jitter_row
from .flow_layout import FlowLayout  # re-export：programmer_panel 工具栏换行布局（C3）
from .qascade_theme import TEXT_SECONDARY  # 次级文字令牌（原 #999999 裸色）

_BAR_BTN = ("QPushButton{font-size:11px;padding:0 6px;min-height:0px;}")


# ── __init__ ──────────────────────────────────────────────────────────────

def _r183_init_state(panel: Any) -> None:
    """R183 批次 I 新增状态（tester 试灯 / 后选过滤 / 步进焦点 / 抖动% / 摇杆录制）。"""
    panel._highlight_on = False          # 试灯 ON/OFF（临时层 tester）
    panel._highlight_addrs = []          # 试灯写过的 [(universe, addr)]（关闭时精确还原）
    panel._post_filter_active = False    # 后选过滤是否生效
    panel._post_filter_ids = []          # 过滤后可见 id
    panel._post_filter_base = []         # 过滤前全集序（All 还原用）
    panel._step_focus_index = -1         # Step 焦点设备下标（-1 = 未开始）
    panel._jitter_pct = 0.0              # 组内随机抖动%（0 = 逐字节旧行为）
    panel._joystick_record_btn = None    # 摇杆路径录制开关钮（_add_xy_joystick 建）


# ── 工具栏（Highlight + Step 步进）─────────────────────────────────────────

def _r183_build_toolbar_extras(panel: Any, lay: Any) -> None:
    """在工具栏追加 Highlight 试灯钮 + Step ◀ i/N ▶ 步进控件。"""
    panel._highlight_btn = _mk_btn(panel, "Highlight", "#F2C64B")
    panel._highlight_btn.setCheckable(True)
    panel._highlight_btn.setToolTip(
        "试灯：选中设备全开白光（临时层 tester，**不写程序值**）。\n"
        "再点关闭即还原；空选/无输出接线时安全提示（后选过滤语义）。")
    panel._highlight_btn.clicked.connect(
        lambda _c=False: panel._highlight_btn.setChecked(
            _phl.highlight_toggle(panel, panel._highlight_btn.isChecked())))
    lay.addWidget(panel._highlight_btn)

    prev_btn = _mk_btn(panel, "◀", "#CCCCCC")
    prev_btn.setFixedWidth(26)
    prev_btn.setToolTip("步进：上一台（越界环绕；焦点高亮到 TopView，不改写值通路）")
    prev_btn.clicked.connect(lambda: _phl.step_and_show(panel, -1))
    lay.addWidget(prev_btn)
    step_lbl = QLabel("0/0")
    step_lbl.setToolTip("当前焦点设备 / 组内台数")
    step_lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;font-family:Consolas;min-width:52px;")
    lay.addWidget(step_lbl)
    panel._step_label = step_lbl
    next_btn = _mk_btn(panel, "▶", "#CCCCCC")
    next_btn.setFixedWidth(26)
    next_btn.setToolTip("步进：下一台（越界环绕；焦点高亮到 TopView，不改写值通路）")
    next_btn.clicked.connect(lambda: _phl.step_and_show(panel, 1))
    lay.addWidget(next_btn)


def _mk_btn(panel: Any, text: str, color: str) -> QPushButton:
    """工具栏小钮（复用面板既有 `_mk_btn` 外观；失败回退裸钮）。"""
    try:
        return panel._mk_btn(text, "#2D2D2D", color)
    except Exception:
        btn = QPushButton(text)
        btn.setStyleSheet(_BAR_BTN)
        return btn


# ── 左栏 / 通道列 ─────────────────────────────────────────────────────────

def _r183_build_left_column(panel: Any) -> QWidget:
    """左栏：设备组选择器 + Palette 调色板条 + 最近效果快捷条（项 8 / 项 15）。"""
    col = QWidget()
    lay = QVBoxLayout(col)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(panel._group_selector, stretch=1)
    panel._palette_bar = None
    try:
        panel._palette_bar = _ppal.build_palette_bar(panel)
        lay.addWidget(panel._palette_bar)
    except Exception:
        panel._palette_bar = None
    panel._effect_quick_bar = None
    try:
        panel._effect_quick_bar = _psheet.build_effect_quick_bar(panel)
        lay.addWidget(panel._effect_quick_bar)
    except Exception:
        panel._effect_quick_bar = None
    return col


def _r183_build_ch_column(panel: Any) -> QWidget:
    """通道列：Post-Selection 后选过滤条（项 4）+ ChannelTypeList。"""
    col = QWidget()
    lay = QVBoxLayout(col)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    panel._post_filter_bar = _build_post_filter_bar(panel)
    lay.addWidget(panel._post_filter_bar)
    lay.addWidget(panel._ch_list, stretch=1)
    return col


def _r183_after_group_load(panel: Any) -> None:
    """组载入后：步进焦点复位 + i/N 标签 + 过滤条/快捷条刷新（全部安全 no-op）。"""
    try:
        _phl.refresh_step_label(panel)
    except Exception:
        pass
    for attr, method in (("_post_filter_bar", "refresh_options"),
                         ("_effect_quick_bar", "refresh_quick_bar")):
        bar = getattr(panel, attr, None)
        fn = getattr(bar, method, None) if bar is not None else None
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


# ── 后选过滤条 ────────────────────────────────────────────────────────────

def _build_post_filter_bar(panel: Any) -> QWidget:
    """后选过滤条：universe / 设备类型(def) / 地址段 三谓词 + 应用 + All 还原。

    后选过滤语义：只在**当前选中集内**再筛（不改选择真源），
    点 "All" 一键还原。谓词与重载逻辑在 programmer_tools / programmer_highlight。
    """
    bar = QWidget()
    bar.setObjectName("programmerPostFilterBar")
    bar.setMinimumHeight(26)   # C3：最小高度（定高会在窄窗挤压组合框）
    bar.setStyleSheet("#programmerPostFilterBar{background:#252525;"
                      "border-bottom:1px solid #3D3D3D;}")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(10, 1, 8, 1)
    lay.setSpacing(5)
    tip = QLabel("后选过滤")
    tip.setToolTip("在**当前选中集内**按特征再筛（后选过滤语义）；"
                   "不改选择真源，点 All 一键还原。")
    tip.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
    lay.addWidget(tip)
    uni = _small_combo("不限宇宙", 78, "按宇宙（universe）过滤；\"不限\"= 不启用该条件")
    defs = _small_combo("不限类型", 120, "按设备类型（fixture def）过滤；\"不限\"= 不启用该条件")
    lo = _small_spin(0, "地址段起点（0=不限）")
    hi = _small_spin(512, "地址段终点（512=不限）")
    lay.addWidget(uni)
    lay.addWidget(defs)
    lay.addWidget(_dim_label("地址"))
    lay.addWidget(lo)
    lay.addWidget(_dim_label("~"))
    lay.addWidget(hi)
    apply_btn = QPushButton("应用")
    all_btn = QPushButton("All")
    for btn, text in ((apply_btn, "按当前三条件过滤（结果内仍按原组序编辑）"),
                      (all_btn, "还原过滤前的全部选中设备")):
        btn.setFixedHeight(20)
        btn.setStyleSheet(_BAR_BTN)
        btn.setToolTip(text)
    lay.addWidget(apply_btn)
    lay.addWidget(all_btn)
    lay.addStretch()

    bar.refresh_options = lambda: _refresh_filter_options(panel, uni, defs)
    apply_btn.clicked.connect(
        lambda: _apply_filter_from_bar(panel, uni, defs, lo, hi))
    all_btn.clicked.connect(lambda: (_phl.clear_post_filter(panel),
                                     bar.refresh_options()))
    bar.refresh_options()
    return bar


def _small_combo(placeholder: str, width: int, tooltip: str) -> QComboBox:
    combo = QComboBox()
    combo.setFixedHeight(20)
    combo.setMinimumWidth(width)
    combo.setToolTip(tooltip)
    combo.addItem(placeholder, None)
    return combo


def _small_spin(value: int, tooltip: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 512)
    spin.setValue(value)
    spin.setFixedHeight(20)
    spin.setFixedWidth(62)
    spin.setToolTip(tooltip)
    return spin


def _dim_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
    return lbl


def _refresh_filter_options(panel: Any, uni_combo: QComboBox, def_combo: QComboBox) -> None:
    """按当前选中集重填宇宙/类型下拉（保留用户已选项，找不到则回退"不限"）。"""
    ids = _phl.group_base_ids(panel)
    fixtures = {str(getattr(f, "id", "")): f
                for f in (getattr(panel, "_current_fixtures", []) or [])}
    defs = getattr(panel, "_fixture_defs_cache", {}) or {}
    cur_u, cur_d = uni_combo.currentData(), def_combo.currentData()
    uni_combo.blockSignals(True)
    uni_combo.clear()
    uni_combo.addItem("不限宇宙", None)
    for u in _ptool.available_universes(ids, fixtures):
        uni_combo.addItem(f"U{u}", int(u))
    idx = uni_combo.findData(cur_u)
    uni_combo.setCurrentIndex(idx if idx >= 0 else 0)
    uni_combo.blockSignals(False)
    def_combo.blockSignals(True)
    def_combo.clear()
    def_combo.addItem("不限类型", None)
    for did, name in _ptool.available_def_ids(ids, fixtures, defs):
        def_combo.addItem(name, did)
    idx = def_combo.findData(cur_d)
    def_combo.setCurrentIndex(idx if idx >= 0 else 0)
    def_combo.blockSignals(False)


def _apply_filter_from_bar(panel: Any, uni_combo: QComboBox, def_combo: QComboBox,
                           lo: QSpinBox, hi: QSpinBox) -> None:
    """读取过滤条三条件 → 应用 → 刷新选项（过滤后选项集自然收窄）。"""
    did = def_combo.currentData()
    _phl.apply_post_filter(panel, universe=uni_combo.currentData(),
                           def_ids=[did] if did else None,
                           addr_lo=int(lo.value()), addr_hi=int(hi.value()))
    _refresh_filter_options(panel, uni_combo, def_combo)


# ── 通道行注入 ────────────────────────────────────────────────────────────

def _r183_add_color_mix_button(row: Any, hlay: Any) -> None:
    """"拾色"旁加"色轮"钮（项 1/2：Color Mix 主入口；系统拾色器保留为次级入口）。"""
    btn = QPushButton("色轮")
    btn.setFixedHeight(24)
    btn.setToolTip(
        "Color Mix：色相-饱和度圆盘 + 8 基色网格 + 色纸（Lee/Rosco 常用 50 色）\n"
        "点击即写 R/G/B（设备支持 W/A/UV 时一并写，按能力检测）")
    btn.clicked.connect(lambda _c=False, r=row: _pcm.open_color_mix_editor(r))
    hlay.addWidget(btn)
    row._color_mix_btn = btn


def _r183_add_row_align_controls(row: Any, hlay: Any) -> None:
    """行内「按位置排序」+「抖动%」控件（项 6 Panorama / 项 10 抖动）。"""
    pano = QPushButton("按位置排序")
    pano.setFixedHeight(22)
    pano.setStyleSheet(_BAR_BTN)
    pano.setToolTip(
        "Panorama Alignment：把本行设备序列按物理 x 坐标（TopView 布局）升序重排，\n"
        "让 </> 等翼方向的\"左→右\"与现场灯位一致。缺坐标时提示不可用。")
    pano.clicked.connect(lambda _c=False, r=row: _panorama_of_row(r))
    hlay.addWidget(pano)
    row._responsive_secondary_widgets.append(pano)

    lbl = QLabel("抖动%")
    lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
    lbl.setToolTip("组内随机抖动：写入条带时每台值加 ±(pct%×255) 的均匀随机量（钳 0..255）")
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(0)
    spin.setFixedWidth(50)
    spin.setToolTip(
        "组内随机抖动：写入条带时每台值加 ±(pct%×255) 的均匀随机量（钳 0..255）。\n"
        "0 = 关闭（既有行为逐字节不变）；随机是效果语义，不改变确定性回放。")
    spin.valueChanged.connect(lambda v, r=row: _set_jitter_of_row(r, float(v)))
    hlay.addWidget(lbl)
    hlay.addWidget(spin)
    row._jitter_label = lbl
    row._jitter_spin = spin
    row._responsive_secondary_widgets.extend([lbl, spin])
    # ★ 项 11：行头右键 →「当前值表格」（只读快照；合规"逐台页面已删"定案）。
    row._header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    row._header.customContextMenuRequested.connect(
        lambda pos, r=row: _r183_show_row_context_menu(r, pos))


def _panorama_of_row(row: Any) -> None:
    """行 → 面板 → Panorama 排序（行未挂面板时安全 no-op）。"""
    from .programmer_panel import _panel_of
    panel = _panel_of(row)
    if panel is not None:
        _phl.panorama_sort(panel)


def _set_jitter_of_row(row: Any, value: float) -> None:
    """抖动% 写回**面板**（`_apply_channel_range` 从面板读），行也存一份兜底。"""
    from .programmer_panel import _panel_of
    row._jitter_pct = float(value)
    panel = _panel_of(row)
    if panel is not None:
        panel._jitter_pct = float(value)
        mirror_jitter_rows(panel)


def _r183_add_joystick_record(panel: Any, joy_lay: Any, row: Any) -> None:
    """摇杆区「录制路径」开关钮（项 12：录完抽稀成轨迹曲线送轨迹页）。"""
    btn = QPushButton("录制路径")
    btn.setCheckable(True)
    btn.setFixedHeight(24)
    btn.setToolTip(
        "录制摇杆路径：点开 → 推摇杆画路径 → 再点关闭即按 RDP 抽稀成轨迹曲线\n"
        "并自动载入「轨迹曲线」页（源=录制；失败安全降级为提示）")
    btn.clicked.connect(lambda checked, b=btn: _toggle_joystick_record(panel, row, b, checked))
    joy_lay.addWidget(btn)
    panel._joystick_record_btn = btn


def _toggle_joystick_record(panel: Any, row: Any, button: Any, checked: bool) -> None:
    """摇杆「录制路径」开关：开=start_record，关=stop_record + 抽稀送入轨迹页。

    数据源是 XYJoystick 自己的 `_record_path`（(t,x,y[,z]) 样本），与推杆录制
    （programmer_automation）互不干扰 —— "手绘路径即曲线"的直连（两项既有 API 对接）。
    """
    from .programmer_panel import _panel_of
    host = _panel_of(row) or panel
    joy = getattr(host, "_joystick", None) or getattr(row, "_joystick", None)
    if joy is None:
        button.setChecked(False)
        _phl.status(host, "摇杆路径：找不到摇杆控件（先展开 Programmer 主体摇杆区）")
        return
    if checked:
        try:
            joy.start_record()
        except Exception:
            button.setChecked(False)
            _phl.status(host, "摇杆路径：无法开始录制")
            return
        _phl.status(host, "摇杆路径录制中…推摇杆画路径，再点「录制路径」结束")
        return
    try:
        samples = joy.stop_record()
    except Exception:
        samples = []
    if not samples:
        _phl.status(host, "摇杆路径：没有录到样本（录制期间没有推摇杆）")
        return
    _ptool.send_joystick_path_to_trajectory(host, samples)


def _r183_show_row_context_menu(row: Any, pos: Any) -> None:
    """通道行头右键菜单：① 当前值表格（只读）② 按位置排序 ③ 释放该通道。

    ★ 只读语义合规：表格是快照视图，不建逐台编辑控件（用户定案 2026-08-02
      「逐台设备页面已移除」）；另两项复用既有通路（Panorama / 行级释放）。
    """
    from PyQt6.QtWidgets import QMenu
    menu = QMenu(row)
    act_sheet = menu.addAction("当前值表格（只读）")
    act_pano = menu.addAction("按位置排序（Panorama Alignment）")
    menu.addSeparator()
    ct = (list(getattr(row, "_ch_types", []) or []) or [""])[0]
    act_release = menu.addAction(f"释放该通道调用层（{ct}）")
    chosen = menu.exec(row.mapToGlobal(pos))
    if chosen is act_sheet:
        _open_row_sheet(row)
    elif chosen is act_pano:
        _panorama_of_row(row)
    elif chosen is act_release:
        try:
            row._release_strip_layers(str(ct))
        except Exception:
            pass


def _row_values_snapshot(row: Any) -> Dict[str, Dict[str, int]]:
    """行 `_values` 快照（坏值剔除；取不到给空 dict）。"""
    out: Dict[str, Dict[str, int]] = {}
    try:
        for fid, chans in (getattr(row, "_values", {}) or {}).items():
            out[str(fid)] = {str(ct): int(v) for ct, v in (chans or {}).items()
                             if isinstance(v, (int, float))}
    except Exception:
        return {}
    return out


def _row_sheet_payload(row: Any) -> dict:
    """只读表格的数据面：值快照 + 设备序/名 + 通道显示名。"""
    from .programmer_panel import _ch_label
    fixtures = getattr(row, "_fixtures", []) or []
    ids = [str(getattr(fi, "id", "")) for fi in fixtures]
    return {
        "values": _row_values_snapshot(row),
        "order": ids,
        "names": {str(getattr(fi, "id", "")): str(getattr(fi, "name", "")
                                                  or getattr(fi, "id", ""))
                  for fi in fixtures},
        "ch_labels": {str(c): _ch_label(str(c))
                      for c in (getattr(row, "_ch_types", []) or [])},
        "title": f"当前值表格 · {getattr(row, '_group_name', '') or '通道行'}",
    }


def _open_row_sheet(row: Any) -> None:
    """打开只读值表格（不建编辑控件；值取自行 `_values` 快照）。"""
    payload = _row_sheet_payload(row)
    title = payload.pop("title")
    _psheet.open_fixture_sheet(
        row.window() if hasattr(row, "window") else None,
        payload.pop("values"), title=title, **payload)


# ── 后选过滤的可见集收窄（项 4）────────────────────────────────────────────

def _r183_narrow_to_post_filter(panel: Any, ordered_ids: List[str]) -> List[str]:
    """后选过滤生效时，把本组可见集收窄到过滤结果（交集，保持原序）。

    ★ 为什么放在 load 通路：`load_fixture_group` / `_on_group_selected` 是**所有**
      组载入的唯一漏斗；过滤结果在这里收窄，编辑面（行内 `_fixtures`）与可见面
      一致，而 `base_order_ids` 仅保留母序追溯，不定义当前成员 —— 与既有实现
      Post Selection "temporary filter to your current selection" 同语义。
    未启用过滤 → 原样返回（零影响）。
    """
    if not bool(getattr(panel, "_post_filter_active", False)):
        return list(ordered_ids)
    allowed = {str(x) for x in (getattr(panel, "_post_filter_ids", []) or [])}
    return [fid for fid in ordered_ids if str(fid) in allowed]
