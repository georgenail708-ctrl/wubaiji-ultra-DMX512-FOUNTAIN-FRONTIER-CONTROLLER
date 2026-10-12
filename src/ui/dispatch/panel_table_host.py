# -*- coding: utf-8 -*-
"""调度台面板的**表格宿主**方法（从 `dispatch_panel` 拆出：单文件 ≤500 行）。

职责：把总线节点 + 工程设备表投影成表格模型、把格值刷进视图、把双击路由到
对应面板，以及"单位/行模式"两个视图偏好的处理。这些方法都只与**表格视图**有关，
与"缓存/场景库管理"无关，因此独立成 mixin：

  · `dispatch_panel` 只剩"面板骨架 + 场景库动作"，读起来是一条主线；
  · 表格相关的改动集中在本文件，不触碰面板骨架。

依赖由宿主提供（`set_services` 注入）：`_fixtures_provider` / `_defs_provider` /
`_nm_provider` / `_panel_provider` / `_groups_provider` / `_cell_ops` /
`_focus_panel_fn`，以及视图 `self.view`。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...processing.dispatch import (
    COL_GROUP_LABELS,
    COL_GROUPINGS,
    COL_GROUP_SOURCE,
    ROW_MODE_LABELS,
    ROW_MODES,
    SCOPE_GROUP_ALL,
    build_fixture_info,
    build_selection_table,
    build_table,
    channel_address_at,
    panel_for_layer,
)
from . import cell_enrich as _enrich
from . import cell_recall as _recall
from . import table_ops as _ops
from .cell_ops import CellOps, refs_from_cells
from .cell_ref_cache import (fixture_info_cached, fixtures_token_of,
                             invalidate_ref_cache, refs_for_cells_of)
from .monitor_cells import PANEL_FOLDERS, PANEL_LABELS


def _dual_get(obj, key, default=None):
    """字典/对象双取（工程模型是 dataclass，导入/测试路径常是 dict）。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _fixture_sig(items) -> tuple:
    """设备表内容签名：覆盖**全部会改地址/列名**的字段。

    对象走直取属性快路径（实测 2000 台 0.26ms）；出现 AttributeError 时（dict 表）
    回退 `_dual_get`。**不可退化成首尾抽样**——那会漏检"改中间设备"，导致按旧地址
    写值（静默写错域，本项目已两次踩到）。
    """
    try:
        return tuple((f.id, f.name, f.universe, f.start_channel, f.def_id)
                     for f in items)
    except AttributeError:
        return tuple((_dual_get(f, "id", ""), _dual_get(f, "name", ""),
                      _dual_get(f, "universe", 1), _dual_get(f, "start_channel", 1),
                      _dual_get(f, "def_id", ""))
                     for f in items)


def _defs_sig(defs_map) -> tuple:
    """定义表内容签名：列顺序（channels）与功能通道名决定表格的列与表头。"""
    return tuple(
        (k, _dual_get(v, "name", ""),
         tuple(_dual_get(v, "channels", []) or ()),
         tuple(sorted((str(a), str(b)) for a, b in
                      (_dual_get(v, "function_channel_names", {}) or {}).items())))
        for k, v in sorted((defs_map or {}).items()))


def _node_sig_of(n) -> tuple:
    """单节点的结构签名：(cell_key, 设备 id 集, group_id, parent_group_id, **live**)。

    ★ `live` 必须进签名：`SCOPE_EFFECT_ONLY` 只列 live 节点（"只显示真实在输出的
      设备"），播放开始/停止会翻转 live → 不给签名就会命中旧缓存、表格不更新。
    """
    try:
        ck = n.cell_key()
    except Exception:
        ck = (str(getattr(n, "panel", "") or ""),
              str(getattr(n, "track_type", "") or ""),
              str(getattr(n, "group_key", "") or ""))
    fids = getattr(n, "fixture_ids", None) or []
    return (ck, tuple(map(str, fids)), str(getattr(n, "group_id", "") or ""),  # ★ R193/P2-06：设备 id 集进签名（成员变动即重建）
            str(getattr(n, "parent_group_id", "") or ""),
            bool(getattr(n, "live", True)))


class TableHostMixin:
    """表格宿主方法（由 `DispatchPanel` 继承）。"""

    def _read_providers(self):
        """读注入的三个 provider：→ (设备表, 定义表, 分组表, 设备 id 序)。"""
        fixtures = list(self._fixtures_provider() if self._fixtures_provider else []) or []
        defs_map = dict(self._defs_provider() if self._defs_provider else {}) or {}
        groups = list(self._groups_provider() if self._groups_provider else []) or []
        order = [str(getattr(f, "id", "") or "") for f in fixtures]
        return fixtures, defs_map, groups, order

    def _build_table_model(self, nodes):
        """节点 + 工程设备表 → 表格模型（注入给视图的构造器）。

        ★ 依赖注入而非自行 import MainWindow：设备表 / 定义 / NetworkManager 全由
          MainWindow 在 `set_services` 时注入，本面板不反向依赖主窗（零耦合）。

        ★★ 2026-09-14 用户定案：监控区**只反映"当前选中设备"**——
          与工程全量设备、与哪些设备正在编辑、与各面板上报的节点都无关。
          注入了 `_selected_ids_provider` 时走 `build_selection_table`（行 = 设备类型，
          列 = 值类型；空选择 → 空表 → 视图显示清空）。未注入时**保留**原
          `build_table` 路径作兜底（独立构造/老测试不崩）。

        ★★ 2026-09-13 性能修复（用户报"整个软件卡得跟 PPT 一样"）：
          本方法原先每 400ms 被调一次、每次**全量重建**整张表（实测 1000 台设备
          216ms → 常占 52% 一个核心，主线程被吃满 → 任何 Qt 操作都卡）。
          现在按**结构指纹**缓存：只有"设备表 / 设备定义 / 节点身份与分组 / 分组 /
          单位 / 行模式 / **选中集**"变化时才重建。**画格值不在此列** —— 值走
          `_refresh_values`（便宜，每域一次批量取），因此刷新周期内通常**零重建**。
        """
        fixtures, defs_map, groups, order = self._read_providers()
        fp = self._structure_fingerprint(nodes, fixtures, defs_map, groups, order)
        if fp is not None and fp == getattr(self, "_model_fp", None):
            cached = getattr(self, "_model_cache", None)
            if cached is not None:
                return cached
        info = build_fixture_info(fixtures, defs_map)
        if not info:
            # 没有设备表（未注入 / 工程为空）→ None，视图回退"仅节点"兜底模型；
            # 硬用空表会把每一行都过滤掉（表格空白且节点级选择为空，实测踩到）。
            self._model_fp, self._model_cache = None, None
            return None
        provider = getattr(self, "_selected_ids_provider", None)
        if callable(provider):
            # ★ 选择表：只看"当前选中设备"，行 = 设备类型（同类型折叠），空选 → 空表
            try:
                selected = list(provider() or [])
            except Exception:
                selected = []
            model = build_selection_table(selected, info, unit=self._unit)
        else:
            model = build_table(
                nodes, info, order,
                panel_labels=PANEL_LABELS, panel_folders=PANEL_FOLDERS,
                scope=self._row_scope, row_mode=self._row_mode,
                unit=self._unit, groups=groups,
                col_grouping=getattr(self, "_col_grouping", COL_GROUP_SOURCE))
        self._model_fp, self._model_cache = fp, model
        return model

    @staticmethod
    def _fixtures_token(fixtures, defs_map) -> tuple:
        """设备表 / 定义表的**内容令牌**（用于"结构未变即短路"判定）。

        ★ 必须是**内容**指纹，不能是 `id(...)`：provider 每次调用返回新的 list/dict
          容器（`list(...)`/`dict(...)` 各造一份），`id` 每次都变 → 缓存 100% miss，
          退化成每 tick 全量重建（本轮回滚的真实回归）。
        ★ 必须覆盖**全部会改地址的字段**，不能只取首尾抽样：只取 head/tail 会让
          "改中间某台设备的域/起始地址"不失效缓存 → 按旧地址写值（静默写错域）。
        ★ 为什么敢用全量：实测 2000 台构造全量三元组仅 **0.16ms**（旧注释里
          "1000 台指纹花 200ms"是误判——那是把逐台 `build_fixture_info` 算进了指纹）。
          相对 15ms 刷新预算有 ~100× 余量，故**用全量换正确**，彻底消除陈旧缓存这类 bug。
        """
        items = fixtures or ()
        return (len(items), _fixture_sig(items),
                len(defs_map or {}), _defs_sig(defs_map))

    def _structure_fingerprint(self, nodes, fixtures, defs_map, groups, order):
        """影响**表格结构**的输入指纹（不含格值！）。

        组成（全部 O(节点数)，与设备数无关）：
          · 设备表/定义表的廉价令牌（对象身份+长度+首尾 id）
          · 各节点的 `cell_key`、设备集合、组身份（节点只有几十个 → 便宜）
          · 分组 id + 成员数、显示单位、行模式、行范围
          · **当前选中集内容 token**（选择表模式下决定行/列；O(选中数)）
        **刻意不含** 节点的 level / summary / live —— 它们只影响格值；若含进去，
        动态效果每帧改 level 都会把整张表重建到卡死（实测 level 变 3 次花了 1s）。
        返回 None = 无法判定 → 调用方保守重建一次。
        """
        try:
            node_sig = tuple(_node_sig_of(n) for n in (nodes or ()))
            grp_sig = tuple((str(g.get("id", "")), str(g.get("name", "")), len(g.get("fixture_ids") or []))  # ★ R193/P2-05：组名进签名（改名后表模型重建）
                            for g in (groups or ()) if isinstance(g, dict))
            return (self._fixtures_token(fixtures, defs_map),
                    node_sig, grp_sig,
                    str(self._unit), str(self._row_mode), str(self._row_scope),
                    # ★ 选择表模式下"行=设备类型、列=值类型"完全由**选中集内容**决定，
                    #   必须进指纹，否则改选设备后命中旧缓存 → 表格不更新（本轮回滚点）。
                    #   O(选中数)（用户框选上限通常在几十~数百台），可接受。
                    self._selection_token(),
                    # ★ 展示分组影响**列与分区结构**（合并=1 份表 / 按来源=分区+角标），
                    #   必须进指纹，否则切换后命中旧缓存 → 表不变（实测踩到）。
                    str(getattr(self, "_col_grouping", COL_GROUP_SOURCE)))
        except Exception:
            return None

    def _selection_token(self) -> tuple:
        """`_selected_ids_provider` 的选中集内容 token（未注入 → 空元组）。

        ★ 必须包 `try/except` 且 provider 可能抛异常（主窗/俯视图未就绪）——
          指纹构建失败会返回 None（调用方保守重建），但这里更稳：取不到就当空选。
        """
        provider = getattr(self, "_selected_ids_provider", None)
        if not callable(provider):
            return ()
        try:
            return tuple(str(x) for x in (provider() or ()))
        except Exception:
            return ()

    def _set_nodes_with_selection(self, cells, force: bool = False) -> bool:
        """`view.set_nodes` + "选中集/设备表变化 → 强制重建"；返回是否需全量取值。

        ★ 选择表（监控区只反映当前选中设备）的结构由**选中集内容**决定、聚合格的
          `src_addrs` 由**设备表内容**决定（建表时写死），而视图的短路签名只看节点结构
          → 两者都必须在此比对并强制重建，否则视图短路 → 表格不更新 / 仍按旧地址
          读写清（后者 = R136：打补丁改域/起始地址；见 `cell_ref_cache.fixtures_token_of`）。
          token 未注入（provider 为 None）时恒为 `()` / `None` → 不触发额外重建。
        """
        tok = self._selection_token()
        if tok != getattr(self, "_last_sel_tok", None):
            self._last_sel_tok = tok
            force = True
        ftok = fixtures_token_of(self)
        if ftok is not None and ftok != getattr(self, "_last_fx_tok", None):
            self._last_fx_tok, force = ftok, True
        from . import cell_sources as _cs
        _cs.install_node_lookup(self)          # ★ R138：双击路由的节点查询（幂等）
        self.view.set_nodes(cells, force=force, struct_token=ftok)
        rebuilt = force or (len(self.view.nodes())
                            != getattr(self, "_last_node_count", -1))
        self._last_node_count = len(self.view.nodes())
        if rebuilt:
            # 换了一批格对象 → 旧缓存里的强引用只会占内存（语义上新格必然未命中）
            invalidate_ref_cache(self)
        return rebuilt

    def _apply_view_prefs(self) -> None:
        """把记忆的"单位"应用到底层视图。

        ★ 2026-09-14：顶栏已精简（单位/行模式/分组按钮全删，见 dispatch_panel
          `_build_toolbar`），故这里不再同步按钮文案，只把单位推给视图。
        """
        try:
            self.view.set_unit(self._unit)
        except Exception:
            pass

    def _fixture_info(self) -> dict:
        """当前设备信息表（格地址换算用；与模型构造同源，避免两套换算）。

        缓存语义（内容令牌 / 为何不能用 id）见 `cell_ref_cache.fixture_info_cached`。
        """
        return fixture_info_cached(self)

    def _refs_for_cells(self, cells):
        """表格格 → CellRef（含聚合格/虚拟通道的取样地址透传）。

        逐格身份缓存见 `cell_ref_cache` 模块头（实测 7.9ms → 1.3ms/tick）。
        """
        return refs_for_cells_of(self, cells, channel_address_at)

    def _refresh_values(self, full: bool = False, cells=None) -> None:
        """把格值刷进视图（每域一次批量取值；聚合格同时算"分歧数"）。

        ★ 性能（用户报"任何操作都卡"）：默认只取**可视范围**的格
          （`view.visible_cells()`，可视行数级）；`full=True` 时取全部
          （结构重建后 / 导入 / 换工程 —— 保证离屏格也有值）。
        ★★ `cells` 显式给出时只刷这批格（拖拽/单格动作后**定向刷新**）：
          原先拖拽每一小步都 `full=True` 全表重读，是"拖动时卡顿"的直接原因。
        """
        view = self.view
        model = view.model()
        if model is None or self._cell_ops is None:
            return
        if cells is None:
            if full:
                cells = view.all_cells()
            else:
                cells = view.visible_cells()
                if not cells:
                    # 视图尚无尺寸（未 show）→ 退化为全量，保证首帧有值
                    cells = view.all_cells()
        if not cells:
            return
        refs = self._refs_for_cells(cells)
        try:
            rows_out = self._cell_ops.read_cells_ex(refs)
        except Exception:
            return
        values, spreads, sources = _pack_read_results(rows_out)
        # ★★ 语义富化（用户 2026-09-14："细化混合"）：把"谁在编辑什么"写到格上 ——
        #   programmer → 条带起止/翼块；effect/matrix → 正在编辑的效果名。
        #   之后显示层（cell_semantics）按这些字段给出语义文本，不再一律「混合」。
        try:
            self._enrich_cells_for_display(cells)
        except Exception:
            pass
        # 面板侧 S 标记（Programmer 已碰过的通道）透传给视图
        marked = self._panel_marks_now()
        # ★ 单一入口 + 单次 update()：把这批键的"分歧/来源"按本次结果**权威替换**
        #   （否则收回条带 / 效果暂停后该格会永远停在「混合」，见 table_view.update_values）。
        view.update_values(values, spreads, sources, panel_marks=marked,
                           refreshed_keys=set(values.keys()))

    def _enrich_cells_for_display(self, cells) -> None:
        """给这批格补语义字段（效果名 / programmer 条带），供显示层细化「混合」。

        节点索引带指纹缓存（见 CellEnricher）；条带按 ch_type 缓存 —— 避免每格重算。
        """
        try:
            from ...processing.dispatch import EffectBus
            nodes = list(EffectBus.instance().cells() or [])
        except Exception:
            nodes = []
        enricher = getattr(self, "_enricher_cache", None)
        if enricher is None:
            enricher = _enrich.CellEnricher()
            self._enricher_cache = enricher
        ctx = {
            "index": enricher.index_for(nodes),
            "pw": self._programmer_panel(),
            "strips": {},           # ch_type → 条带信息（每类型只解析一次）
            "memo": enricher,       # 效果名记忆化（按设备集对象身份）
        }
        for c in (cells or ()):
            try:
                self._enrich_one(c, ctx)
            except Exception:
                continue

    def _programmer_panel(self):
        """Programmer 面板（取不到 None）。"""
        try:
            return self._panel_provider("programmer") if self._panel_provider else None
        except Exception:
            return None

    def _enrich_one(self, c, ctx: dict) -> None:
        """单格富化：有 ch_type → 条带语义（programmer）；否则 → 效果名。"""
        ch_type = str(getattr(c, "ch_type", "") or "")
        if ch_type:
            strips = ctx["strips"]
            info = strips.get(ch_type)
            if info is None:
                info = _enrich.strip_info_for_ch_type(ctx["pw"], ch_type)
                strips[ch_type] = info
            _enrich.apply_strip(c, info)
        _enrich.apply_names(c, ctx["index"], memo=ctx["memo"])

    def _panel_marks_now(self) -> Optional[set]:
        """Programmer 已碰过的通道集合（视图 S 标记）；取不到返回 None。"""
        try:
            pw = self._panel_provider("programmer") if self._panel_provider else None
            if pw is None or not hasattr(pw, "snapshot_static_values"):
                return None
            marked = set()
            for fid, chans in (pw.snapshot_static_values() or {}).items():
                for ch in (chans or ()):
                    marked.add((str(fid), str(ch)))
            return marked
        except Exception:
            return None

    # ── 监控区 ────────────────────────────────────────────────────────────

    def _on_unit_changed(self, unit: str) -> None:
        """切换显示单位（DMX/%/HEX）：存偏好 + 刷新表格与按钮态。"""
        self._unit = str(unit or "dmx")
        _ops.save_unit(self._unit)
        try:
            self.view.set_unit(self._unit)
        except Exception:
            pass
        self._refresh_values()

    def _on_cycle_row_mode(self) -> None:
        """循环切换行排布模式（按组整合 → 全部设备 → 仅有效果）并重建表。"""
        try:
            i = ROW_MODES.index(self._row_mode)
        except (ValueError, AttributeError):
            i = 0
        self._row_mode = ROW_MODES[(i + 1) % len(ROW_MODES)]
        _ops.save_row_mode(self._row_mode)
        self.refresh_monitor()

    def _on_cell_activated(self, payload) -> None:
        """双击格 → 唤起**对应面板的对应编辑界面**并聚焦到该设备/通道。

        `payload` = `(cell_key, panel_id)`（见 table_view.mouseDoubleClickEvent）。
        兼容旧的"只传 cell_key"形式（老测试/调用方）：此时按节点语义回落。
        """
        key = None
        panel_id = ""
        try:
            if isinstance(payload, (tuple, list)) and len(payload) == 2:
                key, panel_id = payload[0], str(payload[1] or "")
            else:
                key = payload
        except Exception:
            key = payload
        if key is None:
            return
        # ★ 2026-09-14：双击通道 → 唤起来源页面并**回到该效果的制作状态**（见 cell_recall）。
        pl = _recall.build_recall_payload(self, key, panel_id)
        if self._focus_panel(pl):
            return
        # 无法唤起（面板未注入 / 打开失败）：回落为"请求主窗路由"
        if pl.node:
            try:
                self.recallRequested.emit([{"node": pl.node, "state": pl.state}])
            except Exception:
                pass
        if pl.fids:
            self.focusRequested.emit(str(panel_id or "programmer"), list(pl.fids))

    def _fids_of_cell(self, key) -> list:
        """格 → 设备 id 列表（聚合格取全组）。"""
        try:
            sec, cell = self.view._cells.get(tuple(key), (None, None))
        except Exception:
            return []
        if cell is None:
            return [str(key[0])]
        fids = list(getattr(cell, "fids", None) or ())
        return [str(f) for f in fids] if fids else [str(getattr(cell, "fixture_id", ""))]

    def _node_of_cell(self, key, panel_id: str = ""):
        """格 → 对应总线节点（"这个值是谁写的" → 双击路由到来源面板）。

        实现已抽到 `cell_recall.node_of_cell`（那里有完整的优先级说明与历史修正
        记录：跨面板搜索、同面板过滤、列键/通道类型/覆盖设备三级匹配）。
        本方法只保留"面板侧取格"的一层壳，避免 god-file 继续膨胀。
        """
        return _recall.node_of_cell(self, key, panel_id)

    def _focus_panel(self, payload) -> bool:
        """把 `RecallPayload` 交给 MainWindow 注入的回调去唤起面板并还原状态。

        ★ 零耦合：本面板不 import 任何效果面板、也不直接操作主窗 —— 路由是
          MainWindow 的职责（实现集中在 `cell_recall.handle_focus_target`）。
        """
        fn = getattr(self, "_focus_panel_fn", None)
        if fn is None:
            return False
        return bool(_try_call(fn, payload))

    # ── 保存区 ────────────────────────────────────────────────────────────


def _try_call(fn, payload) -> bool:
    """调注入的 focus 回调；新旧签名都兼容，任何异常返回 False。"""
    try:
        return bool(fn(payload))
    except TypeError:
        try:      # 兼容旧的 3 参注入签名（panel_id, fids, cell_key）
            return bool(fn(payload.panel_id, list(payload.fids),
                           tuple(payload.node.get("cell_key") or ())))
        except Exception:
            return False
    except Exception:
        return False


def _pack_read_results(rows_out):
    """`read_cells_ex` 结果 → `(values, spreads, sources)` 三字典。

    `sources` 是"这值是谁写的"来源角标用的**面板 id**集合（层名经
    `panel_for_layer` 映射；未注册的层不出现）。
    """
    values = {}
    spreads = {}
    sources = {}
    for rec in (rows_out or ()):
        key, spread, val, owned = rec[0], rec[1], rec[2], rec[3]
        values[key] = (val, owned)
        if spread and spread > 1:
            spreads[key] = spread
        srcs = _source_panels(rec[5] if len(rec) > 5 else ())
        if srcs:
            sources[key] = srcs
    return values, spreads, sources


def _source_panels(owners) -> tuple:
    """混音层名 → 去重的**面板 id** 元组（"这值是谁写的"来源角标）。"""
    srcs = []
    for lay in (owners or ()):
        pid = panel_for_layer(lay)
        if pid and pid not in srcs:
            srcs.append(pid)
    return tuple(srcs)
