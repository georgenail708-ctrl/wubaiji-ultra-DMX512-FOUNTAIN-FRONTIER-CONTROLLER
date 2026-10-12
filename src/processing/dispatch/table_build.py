# -*- coding: utf-8 -*-
"""调度台表格的**构建函数**（从 `table_model` 拆出：单文件 ≤500 行，见架构门禁）。

为什么独立成模块：构建逻辑（行/列/格的投影）与数据结构（`TableModel`/`TableRow`…）
是两件事——拆分后"数据结构"稳定、构建策略可独立演进，也让两个文件都落在 500 行内
（`tools/check_arch_rules.py` 的新文件硬限）。

依赖方向单向：本模块 import `table_model`（数据结构），反之不成立。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .fixture_table import (
    ColSpec,
    build_columns,
    norm_channel_type,
)
from .table_build_agg import (  # noqa: F401  （整合/行范围构建，见该模块）
    _agg_channel_cells,
    _agg_effect_cells,
    _selection_channel_cells,
    _resolve_rows_by_mode,
    _rows_for_agg,
    _sub_total_of,
    build_group_index,
    build_row_identity,
    build_type_identity,
)
from .table_model import (
    CHANNEL_PANELS,
    CLIP_PANEL,
    COL_CHANNEL,
    COL_CLIP,
    COL_EFFECT,
    COL_GROUP_MERGE,
    COL_GROUP_SOURCE,
    MIXED_LABEL,
    ROW_MODE_EFFECT_ONLY,
    ROW_MODE_GROUPS,
    SCOPE_EFFECT_ONLY,
    SCOPE_GROUP_ALL,
    SELECTION_PANEL,
    SELECTION_TITLE,
    SUMMARY_PANEL,
    SUMMARY_TITLE,
    TableCell,
    TableModel,
    TableRow,
    TableSection,
    col_key_for,
    format_value,
    row_key,
)




def _fixture_ids_of(nodes: Sequence[Any]) -> List[str]:
    """按首次出现顺序并集各节点的 fixture_ids（保持稳定）。"""
    seen: Dict[str, None] = {}
    for n in nodes or ():
        for fid in (getattr(n, "fixture_ids", None) or []):
            if fid:
                seen.setdefault(str(fid), None)
    return list(seen.keys())




def _selected_fixtures_of(nodes: Sequence[Any]) -> set:
    """被节点标记为"当前选中"的设备 id（clip 选中高亮透传用）。

    ★ 节点级 `selected` 与表格自身的选择是两件事：前者是"时间线里选中的 clip"，
      后者是"用户在调度台里框选"。故单独收集，落到 `TableRow.selected`。
    """
    out = set()
    for n in (nodes or ()):
        if not bool(getattr(n, "selected", False)):
            continue
        for fid in (getattr(n, "fixture_ids", None) or []):
            out.add(str(fid))
    return out


def _columns_for_channel_panel(nodes: Sequence[Any], info_by_id: Dict[str, dict],
                               fid_list: Sequence[str]) -> List[ColSpec]:
    """通道列：取本区设备集的有序并集 + 节点上报的 track_type（静态通道）。

    ★ 必须带上 `track_type`：Programmer 的静态值可能落在设备定义**没有**的通道上
      （典型是虚拟 dimmer——纯 RGB/W 灯具无独立调光通道），只按设备定义建列会漏列。
    """
    extra: List[str] = []
    for n in nodes or ():
        tt = norm_channel_type(getattr(n, "track_type", "") or "")
        if tt and tt != "mixed":
            extra.append(tt)
    infos = [info_by_id[f] for f in fid_list if f in info_by_id]
    return build_columns(infos, extra_types=extra)


def _effect_columns(nodes: Sequence[Any]) -> List[ColSpec]:
    """效果槽列：**一个节点一列**（身份契约：微差即两种效果）。

    列身份 = 节点 cell_key 的稳定串（panel|track_type|group_key），保证同名不同
    设备/不同参数的两效果是两个独立列。
    """
    cols: List[ColSpec] = []
    for i, n in enumerate(nodes or ()):
        panel, tt, gk = getattr(n, "cell_key", lambda: ("", "", ""))()
        tag = str(getattr(n, "display_tag", lambda: "")() or "")
        name = str(getattr(n, "effect_name", "") or "")
        label = tag or name or f"效果 {i + 1}"
        summary = str(getattr(n, "summary", "") or "")
        cols.append(ColSpec(
            key=f"{panel}|{tt}|{gk}",
            ch_type="",
            count=1,
            label=label,
            group=summary or str(getattr(n, "group_name", "") or ""),
            sort=(0, i),
        ))
    return cols


def _section_title(panel: str, labels: Dict[str, str]) -> str:
    return labels.get(panel, panel)


def _build_summary_section(info_by_id: Dict[str, dict], all_fids: Sequence[str],
                           panel_nodes: Dict[str, List[Any]], *,
                           model: "TableModel", group_index: Dict[str, str],
                           group_names: Dict[str, str],
                           sel_of: Optional[Dict[str, Tuple[str, str]]] = None,
                           ) -> TableSection:
    """构造**汇总分区**：所有面板写过的设备 → 一张通道表（天然汇总）。

    列 = 值类型（含各面板上报的 track_type）；格 = 有物理地址的通道格，取值走
    混合输出 → effect/matrix/音频/摇杆 的贡献自动出现在同一张表里。
    行 = **永远按组/设备类型折叠**（用户定案：取消逐台展开）。
    """
    # 汇总的"覆盖集" = 所有节点 fixture_ids 的并集
    covered: List[str] = []
    seen = set()
    for _panel, nds in panel_nodes.items():
        for n in nds:
            for fid in (getattr(n, "fixture_ids", None) or []):
                fid = str(fid)
                if fid not in seen:
                    seen.add(fid)
                    covered.append(fid)
    fids = [f for f in covered if f in info_by_id]
    extra: List[str] = []
    for _panel, nds in panel_nodes.items():
        for n in nds:
            tt = norm_channel_type(getattr(n, "track_type", "") or "")
            if tt and tt != "mixed":
                extra.append(tt)
    sec = TableSection(key=SUMMARY_PANEL, title=SUMMARY_TITLE, folder="汇总")
    sec.columns = build_columns([info_by_id[f] for f in fids], extra_types=extra)
    max_sub = max((_sub_total_of(info_by_id.get(f) or {}, sec.columns)
                   for f in fids), default=1)
    sec.rows = _rows_for_agg(info_by_id, fids, sec.columns,
                             group_index=group_index,
                             group_names=group_names, max_sub=max_sub,
                             sel_of=sel_of)
    for row in sec.rows:
        _agg_channel_cells(row, sec.columns, info_by_id)
    return sec


def _merge_channel_types(all_nodes: Sequence[Any]) -> List[str]:
    """全部节点上报的 track_type（去重、保序）——合并表的通道列用。"""
    out: List[str] = []
    seen = set()
    for n in all_nodes or ():
        tt = norm_channel_type(getattr(n, "track_type", "") or "")
        if tt and tt != "mixed" and tt not in seen:
            seen.add(tt)
            out.append(tt)
    return out


def _dedupe_known(fids: Sequence[str], info_by_id: Dict[str, dict]) -> List[str]:
    """设备 id 序列 → 去重、保序、且只保留信息表里存在的（稳定行序）。"""
    out: List[str] = []
    seen = set()
    for f in fids or ():
        f = str(f)
        if f in info_by_id and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _mark_selected_rows(rows: Sequence[TableRow], sel_fids: set) -> None:
    """把"节点标记为选中"的设备落到对应折叠行的 `selected`。"""
    if not sel_fids:
        return
    for row in rows:
        if row.fixture_id in sel_fids or (set(row.fids or ()) & sel_fids):
            row.selected = True


def _build_merged_section(all_nodes: Sequence[Any], info_by_id: Dict[str, dict],
                          fixtures_order: Sequence[str], *,
                          group_index: Dict[str, str], group_names: Dict[str, str],
                          scope: str,
                          sel_of: Optional[Dict[str, Tuple[str, str]]] = None,
                          ) -> TableSection:
    """**合并分区**（默认视图）：一份表 = 全部通道列 + 全部效果槽列，行按设备类型折叠。

    用户 2026-09-14 定案："不应该区分不同的来源…仅按照设备不同类型区分，其它的都不分，
    都默认合并了"；"提供一个选项可以把设备再按照来源分开，但是默认不要"。

    ★ 与原「汇总分区」的区别：本分区**同时含效果槽列**（每列一个效果实例，身份仍是
      `panel|track_type|group_key`，不合并同效果的不同实例），因此同一张表既能看
      通道实时值、又能看各来源的效果；且**不按 panel 分区**。
    """
    src = fixtures_order if scope == SCOPE_GROUP_ALL else _fixture_ids_of(all_nodes)
    covered = _dedupe_known(src, info_by_id)
    sec = TableSection(key=SUMMARY_PANEL, title="效果制作总线", folder="")
    sec.columns = build_columns([info_by_id[f] for f in covered],
                                extra_types=_merge_channel_types(all_nodes))
    # ★ 效果槽列只取**非通道类**面板：Programmer 写的是**通道**（其 track_type 已并入
    #   上面的通道列），若再给它效果槽列会出现重复列（实测出「红 R」与「R」两列）。
    sec.columns += _effect_columns(
        [n for n in (all_nodes or ())
         if str(getattr(n, "panel", "") or "") not in CHANNEL_PANELS])
    max_sub = max((_sub_total_of(info_by_id.get(f) or {}, sec.columns)
                   for f in covered), default=1)
    sec.rows = _rows_for_agg(info_by_id, covered, sec.columns,
                             group_index=group_index,
                             group_names=group_names, max_sub=max_sub,
                             sel_of=sel_of)
    for row in sec.rows:
        _agg_channel_cells(row, sec.columns, info_by_id)
        _agg_effect_cells(row, all_nodes, sec.columns)
    _mark_selected_rows(sec.rows, _selected_fixtures_of(all_nodes))
    return sec


def build_table(nodes: Sequence[Any], info_by_id: Dict[str, dict],
                fixtures_order: Sequence[str], *,
                panel_labels: Optional[Dict[str, str]] = None,
                panel_folders: Optional[Dict[str, str]] = None,
                scope: str = SCOPE_EFFECT_ONLY,
                row_mode: str = ROW_MODE_GROUPS,
                unit: str = "dmx",
                groups: Optional[Sequence[dict]] = None,
                include_summary: bool = True,
                col_grouping: str = COL_GROUP_SOURCE) -> TableModel:
    """总入口：节点 + 设备表 → 可直接绘制的 TableModel。

    参数:
        nodes          调度台总线节点（EffectNode），已按面板/组稳定排序
        info_by_id     `fixture_table.build_fixture_info()` 的产物
        fixtures_order 全工程设备 id（SCOPE_GROUP_ALL 用；须为稳定顺序）
        panel_labels   panel id → 显示名（UI 传 PANEL_LABELS，默认用 id）
        panel_folders  panel id → 组分类名（"Clip 分档/编程/巡演"）
        scope          行范围（见 SCOPE_*）
        row_mode       行排布模式（ROW_MODE_GROUPS 整合全部类型 / ROW_MODE_EFFECT_ONLY
                       仅本区覆盖类型）。**行永远折叠**，此参数只影响"列哪些类型"。
        unit           显示单位（随模型带出，方便渲染层直接格式化）
        groups         已创建设备组（供"按组整合"认组；未建组的按设备类型整合）

    返回 TableModel（sections 按"面板 id 字典序"稳定排列，行/列内部顺序稳定）。
    """
    labels = dict(panel_labels or {})
    folders = dict(panel_folders or {})
    model = TableModel(unit=str(unit or "dmx"))
    model.row_mode = str(row_mode or ROW_MODE_GROUPS)
    model.col_grouping = (str(col_grouping)
                          if col_grouping in (COL_GROUP_MERGE, COL_GROUP_SOURCE)
                          else COL_GROUP_SOURCE)

    group_index = build_group_index(groups)
    group_names = {str(g.get("id", "")): str(g.get("name", "") or g.get("id", ""))
                   for g in (groups or ()) if isinstance(g, dict)}
    # ★★ 「只显示当前**真实在输出**的设备」（用户 2026-09-14 定案）：
    #   `SCOPE_EFFECT_ONLY`（默认）下**只取 `live=True` 的节点** —— `live` 的语义
    #   就是"是否正在输出"（时间线 clip 未播放时 `live=False`，正是"残留播放样子"
    #   的根源）。`SCOPE_GROUP_ALL` 保留全量（供核对/排查，不受 live 限制）。
    if scope == SCOPE_EFFECT_ONLY:
        nodes = [n for n in (nodes or ()) if bool(getattr(n, "live", True))]
    # ★ 行身份：在"节点选择"与"已创建组"之间按**最小完整集合**取胜
    #   （用户 2026-09-14 定案"左边一组一个效果、右边一组另一个效果 → 两行"，
    #    且母集合自动作为分组条）。见 build_row_identity。
    sel_of = build_row_identity(nodes, groups or ())
    by_panel: Dict[str, List[Any]] = {}
    for n in nodes or ():
        by_panel.setdefault(str(getattr(n, "panel", "") or ""), []).append(n)

    # ★★ 默认「合并」视图（用户 2026-09-14 定案）：**唯一一份表**，不按来源分区、
    #   不画来源角标；列 = 全部通道类型 + 全部效果槽，行 = 设备类型折叠。
    #   用户原话："不应该区分不同的来源…仅按照设备不同类型区分，其它的都不分，
    #   都默认合并了"。`col_grouping=source` 时才回到"按来源分开"（见下）。
    if model.col_grouping == COL_GROUP_MERGE:
        if by_panel:
            sec = _build_merged_section(nodes, info_by_id, list(fixtures_order),
                                        group_index=group_index,
                                        group_names=group_names, scope=scope,
                                        sel_of=sel_of)
            if sec.columns and sec.rows:
                model.sections.append(sec)
        return model

    # ★ 汇总分区置顶（用户点名"天然汇总"）：任何面板写到设备通道的值都在这里
    if include_summary and by_panel:
        try:
            summ = _build_summary_section(info_by_id, list(fixtures_order),
                                          by_panel, model=model,
                                          group_index=group_index,
                                          group_names=group_names,
                                          sel_of=sel_of)
            if summ.columns and summ.rows:
                model.sections.append(summ)
        except Exception:
            pass

    for panel in sorted(by_panel):
        model.sections.append(_build_source_section(
            panel, by_panel[panel], info_by_id, fixtures_order,
            _SourceCtx(labels=labels, folders=folders, model=model,
                       group_index=group_index, group_names=group_names, scope=scope,
                       sel_of=sel_of)))
    return model


def build_selection_table(selected_ids: Sequence[str], info_by_id: Dict[str, dict],
                          *, unit: str = "dmx") -> TableModel:
    """**只反映"当前选中设备"**的监控表：单一分区，行 = 设备**类型**，列 = 值类型。

    ★★ 2026-09-14 用户定案（语义必须精确落实）：
      "监控区显示的设备与项目有什么设备无关，与当前哪些设备有正在进行的编辑操作
       也无关，**只与当前选中了哪些设备有关**…设备完全按照设备类型分类…如果选中
       30 个里的其中没有效果的 15 个喷头，那么此时调度台这个通道就会变成空余的
       状态…取消选中，则调度台清空，什么也没有。"

    实现要点（**复用既有整合逻辑**，不新造轮子）：
      · `fids` = 去重、保序、且信息表里存在的 selected_ids（`_dedupe_known`）；
      · 行身份 = `build_type_identity`（**只按 def_id**，不按已创建组/临时选中）；
        `group_index={}` / `group_names={}` —— 用户要求"只按设备类型"；
      · 列 = 该选中设备集的有序并集（**不传 extra_types**，避免混入非该设备集的通道）；
      · 空选择 → 返回**无 sections** 的空 model（视图显示空态 = "调度台清空"）；
      · 不建汇总区、不建来源分区、不含 clip。
    """
    fids = _dedupe_known([str(x) for x in (selected_ids or ())], info_by_id)
    model = TableModel(unit=str(unit or "dmx"))
    model.row_mode = ROW_MODE_GROUPS
    model.col_grouping = COL_GROUP_SOURCE
    if not fids:
        return model            # 空选择 → 空表（无 sections），视图显示空态
    sec = TableSection(key=SELECTION_PANEL, title=SELECTION_TITLE, folder="")
    sec.columns = build_columns([info_by_id[f] for f in fids])
    sel_of = build_type_identity(fids, info_by_id)
    # ★ 2026-09-14 用户定案："一个设备类型**只出一行**，不要 灯位1/2/3/4 这种"
    #   → `max_sub=1`（不按灯位拆行）+ `label_positions=False`（标签不带灯位后缀）；
    #   复合设备的各灯位在**格内**合并（`_selection_channel_cells`），
    #   与"跨设备聚合"同一套"全同显示值/不同显示混合"语义。
    sec.rows = _rows_for_agg(info_by_id, fids, sec.columns,
                             group_index={}, group_names={},
                             max_sub=1, sel_of=sel_of, label_positions=False)
    for row in sec.rows:
        _selection_channel_cells(row, sec.columns, info_by_id)
    model.sections.append(sec)
    return model


@dataclass(frozen=True)
class _SourceCtx:
    """「按来源」分区构建的共享上下文（收扰长参数表，满足位置参数 ≤5 门禁）。"""
    labels: Dict[str, str]
    folders: Dict[str, str]
    model: "TableModel"
    group_index: Dict[str, str]
    group_names: Dict[str, str]
    scope: str
    sel_of: Optional[Dict[str, Tuple[str, str]]] = None


def _build_source_section(panel: str, panel_nodes: Sequence[Any],
                          info_by_id: Dict[str, dict], fixtures_order: Sequence[str],
                          ctx: "_SourceCtx") -> TableSection:
    """「按来源」模式的一个面板分区（通道列或效果槽列 + 折叠行）。"""
    covered = _fixture_ids_of(panel_nodes)
    rows_order = _resolve_rows_by_mode(info_by_id, fixtures_order, covered,
                                       ctx.model.row_mode, ctx.scope)
    col_kind = (COL_CLIP if panel == CLIP_PANEL
                else COL_CHANNEL if panel in CHANNEL_PANELS
                else COL_EFFECT)
    sec = TableSection(key=panel, title=_section_title(panel, ctx.labels),
                       folder=ctx.folders.get(panel, ""))
    if col_kind == COL_CHANNEL:
        sec.columns = _columns_for_channel_panel(panel_nodes, info_by_id, rows_order)
    else:
        sec.columns = _effect_columns(panel_nodes)
    # ★ 行**永远折叠**（用户定案：调度台不再逐台陈列设备——大工程下极卡，
    #   且选设备本来就该在俯视图做）。同创建组/同设备类型 → 一行；组内不同显示「混合」。
    max_sub = max((_sub_total_of(info_by_id.get(f) or {}, sec.columns)
                   for f in rows_order), default=1)
    sec.rows = _rows_for_agg(info_by_id, rows_order, sec.columns,
                             group_index=ctx.group_index,
                             group_names=ctx.group_names, max_sub=max_sub,
                             sel_of=ctx.sel_of)
    for row in sec.rows:
        if col_kind == COL_CHANNEL:
            _agg_channel_cells(row, sec.columns, info_by_id)
        else:
            _agg_effect_cells(row, panel_nodes, sec.columns)
    _mark_selected_rows(sec.rows, _selected_fixtures_of(panel_nodes))
    return sec
