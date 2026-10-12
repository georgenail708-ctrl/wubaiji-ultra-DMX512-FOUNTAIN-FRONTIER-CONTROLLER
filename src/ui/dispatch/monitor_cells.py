# -*- coding: utf-8 -*-
"""调度台 · 监控区视图（**兼容薄壳**：对外仍是 `EffectBusView`，内部是 表格）。

背景（2026-09-13 用户定案）：
  监控区从"自绘窗格流（一格 = 一个效果）"整体改为 **表格视图**
  （行 = 设备/子设备/设备组，列 = 值类型，格 = 实时数值，`S` = 已标记保存）。
  新实现在 `table_view.FixtureTableView`。

★ 为什么保留 `EffectBusView` 这个名字与本文件：
  该名字及其公开方法（`set_nodes/nodes/selected_nodes/selected_keys/set_selected/
  select_all/clear_selection`、信号 `selection_changed`/`cell_activated`、
  `_order`/`_lay_items`）被既有测试与面板引用。保留薄壳 → 这次替换是"换实现"
  而非"换契约"，调用方不必跟改（最小改动原则；CLAUDE.md Part 2）。

节点级 API 与表格的语义桥接（关键映射，别处不要再实现一份）：
  · 一个 `EffectNode` 在效果类分区里就是**一列**（列键 = `panel|track_type|group_key`
    —— 与 `cell_key()` 同构），因此"选中一个节点" = 选中它那一列的全部格。
  · Programmer 类分区是**通道列**（列键 = 通道类型），节点不占列；此时"选中一个
    节点" = 选中该节点 `fixture_ids` 覆盖到的该分区全部格。
  两种映射都在 `_node_to_cells()` 里，单一真源。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from ...processing.dispatch.table_model import (
    CHANNEL_PANELS,
    CLIP_PANEL,
    ROW_MODE_GROUPS,
    TableModel,
)
from ...processing.dispatch.fixture_table import ColSpec as _ColSpec
from .table_view import FixtureTableView


#: 面板 id → 中文显示名（分区标题用）。未知 id 原样显示。
PANEL_LABELS: Dict[str, str] = {
    "effect": "效果预设库 Effect",
    "programmer": "Programmer 编程器",
    "linker": "关联器 Track Linker",
    "matrix": "矩阵 Matrix",
    "audio": "音频 Audio",
    "route": "跑动编排 Route",
    "joystick": "摇杆 Joystick",
    "clip": "时间线 Clip",
}

#: 面板 id → 组分类（用户要求"按模块子窗口来源分类"的分档）
PANEL_FOLDERS: Dict[str, str] = {
    "clip": "Clip 分档",
    "programmer": "编程",
    "effect": "编程",
    "matrix": "编程",
    "linker": "编程",
    "audio": "巡演",
    "route": "巡演",
    "joystick": "巡演",
}


def _node_col_key(node: Any) -> str:
    """节点的列键（效果列与节点的 `cell_key()` 同构）。"""
    try:
        panel, tt, gk = node.cell_key()
    except Exception:
        panel = str(getattr(node, "panel", "") or "")
        tt = str(getattr(node, "track_type", "") or "")
        gk = str(getattr(node, "group_key", "") or "")
    return f"{panel}|{tt}|{gk}"


def _short_circuit_key(node_sig, struct_token) -> Optional[Tuple[Any, ...]]:
    """短路签名 = 节点结构签名（+ **设备表内容令牌**，见 `set_nodes`）。

    ★★ R136 用户实报（打补丁改域/起始地址后监控区仍按旧地址读写清）：节点结构签名
      判定不了"设备域/起始地址被改"（节点一个都没变），而聚合格的 `src_addrs` 是
      **建表时**按设备表算好写死的 → 必须让设备表内容令牌也进签名，否则视图短路 →
      仍持旧模型 → 表格按旧地址读写清。`node_sig is None`（无法判定）时保持 `None`
      （调用方保守重建，语义与原实现一致）；`struct_token is None`（未注入/取数失败）
      → 只按节点签名短路，与原行为一致。
    """
    if node_sig is None:
        return None
    return node_sig if struct_token is None else (node_sig, struct_token)


class EffectBusView(FixtureTableView):
    """监控区表格视图 + 节点级兼容 API（对外契约与旧版同名）。

    新增（相对 FixtureTableView）：
        set_nodes(nodes)      设置节点列表（面板刷新时调）；需先 `set_model_builder`
        set_model_builder(fn) 注入 "nodes → TableModel"（由面板提供，含设备表/分组）
        nodes()               当前节点列表
        selected_nodes()      被选节点（按视觉顺序）
        selected_keys()       被选节点的 `cell_key()` 集合（节点级语义）
        set_selected(keys)    按节点 `cell_key()` 选择（映射到该节点覆盖的格）
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nodes: List[Any] = []
        self._nodes_by_panel: Dict[str, List[Any]] = {}
        self._model_builder: Optional[Callable[[List[Any]], TableModel]] = None
        #: 上一次 `set_nodes` 的**结构签名**（供"结构未变则短路"判定）。
        #  ★ 必须在首次 `set_nodes` 之前初始化：`set_nodes` 先读 `self._nodes_sig`
        #    再回写，缺此初始化会让**第一次**调用抛 AttributeError（真实回归）。
        self._nodes_sig: Optional[Tuple[Any, ...]] = None
        #: 节点 `cell_key()` → 该节点覆盖的格键集合（唯一真源映射）
        self._node_cells: Dict[Tuple[str, str, str], Set[Tuple[str, str]]] = {}
        #: 视觉顺序下的**节点键**（与基类的 `_order`（格键）分开，切勿混用）
        self._node_order: List[Any] = []
        #: 节点派生的"设备 → (组id, 组名, 父组id)"（供表格画分组条；见 _derive_row_groups）
        self._node_row_groups: Dict[str, Tuple[str, str, str]] = {}

    # ── 注入 ────────────────────────────────────────────────────────────
    def set_model_builder(self, fn: Optional[Callable[[List[Any]], TableModel]]) -> None:
        """注入"节点列表 → 表格模型"的构造器（面板持有设备信息表，故由它提供）。"""
        self._model_builder = fn

    # ── 节点级 API ──────────────────────────────────────────────────────
    @staticmethod
    def _nodes_structure_sig(nodes: Sequence[Any]):
        """节点**结构**签名（O(节点数)）——用于判断"是否必须重建表格"。

        ★ 性能修复（用户报"任何操作都卡"的根因之一）：原实现每次 tick 都无条件
          `set_model + _relayout + _rebuild_node_map + 全行排序`（实测 ~150ms/次，
          1000 台设备）。而**节点只有几十个**、设备有几千台 —— 结构绝大多数 tick
          是不变的，只有格值在变。

        签名含：节点 `cell_key`（= panel|track_type|group_key，其中 group_key 是
        **按设备集内容寻址**的 → 设备集一变 key 就变）、设备数量、组身份。
        **刻意不含** level/summary/live（那些只影响格值，不影响表结构）。
        返回 None = 无法判定 → 调用方保守重建。
        """
        try:
            sig = []
            for n in (nodes or ()):
                try:
                    ck = n.cell_key()
                except Exception:
                    ck = (str(getattr(n, "panel", "") or ""),
                          str(getattr(n, "track_type", "") or ""),
                          str(getattr(n, "group_key", "") or ""))
                sig.append((ck,
                            len(getattr(n, "fixture_ids", None) or ()),
                            str(getattr(n, "group_id", "") or ""),
                            str(getattr(n, "parent_group_id", "") or "")))
            return tuple(sig)
        except Exception:
            return None

    def set_nodes(self, nodes: Sequence[Any], force: bool = False, *, struct_token=None) -> None:
        """设置节点；**结构未变时短路**（只让面板刷值，不重建表格）。

        `force=True` 强制重建；`struct_token`（设备表内容令牌）并入短路签名（R136，见 `_short_circuit_key`）。
        """
        self._nodes = list(nodes or [])
        self._nodes_by_panel = {}
        for n in self._nodes:
            self._nodes_by_panel.setdefault(str(getattr(n, "panel", "") or ""), []).append(n)
        sig = _short_circuit_key(self._nodes_structure_sig(self._nodes), struct_token)
        unchanged = (not force and sig is not None
                     and sig == self._nodes_sig
                     and self.model() is not None)
        self._nodes_sig = sig
        if unchanged:
            # ★ 结构未变 → 不 set_model / 不 _relayout / 不 _rebuild_node_map
            #   （这三项才是 tick 的大头；格值由面板 `_refresh_values` 负责）
            return
        self._derive_row_groups()
        model = None
        if callable(self._model_builder):
            try:
                model = self._model_builder(self._nodes)
            except Exception:
                model = None
        if model is None:
            # ★ 未注入构造器时给一份**兜底模型**：只用节点自身信息
            #   （panel + fixture_ids + track_type），使控件可独立构造与测试。
            #   真实运行时由面板注入带设备表的构造器（更完整：设备名/通道地址）。
            model = self._fallback_model(self._nodes)
        self.clear_value_caches()      # 结构变了 → 旧格键的值缓存作废
        self.set_model(model)
        self._rebuild_node_map()
        self.update()

    def _derive_row_groups(self) -> None:
        """注入**空**"设备 → 组"映射：监控区**只按设备类型**，不画分组条。

        ★ 2026-09-14 用户定案（监控区只反映"当前选中设备"，行=设备类型）：
          "监控区显示的设备…只与当前选中了哪些设备有关…设备完全按照设备类型分类"。
          旧实现从节点自身派生"设备→(组id, 组名, 父组id)"并画母组/临时选中分组条，
          这与"只按类型、忽略各面板上报的节点并集"的要求相悖 → 现在注入空映射。

        保守做法（见任务约束）：**保留本方法与 `_reorder_rows_by_group` 不删**
          （既有测试可能引用），只把映射置空 → 分组条自然消失、行序不乱。
        """
        self._node_row_groups = {}
        # defer=True：随即还会 set_model → 由那次 _relayout 统一排（省一遍全量重排）
        self.set_row_groups({}, defer=True)

    def _reorder_rows_by_group(self) -> None:
        """把各分区的行按组排连续（母组在前、其临时子组紧随），使分组条有意义。

        ★ 为什么要重排：分组条是"标题"，若其行散布在别组之间，标题就指向不明。
          这里只重排**行的呈现顺序**，不改行内容/格值（模型仍是数据真源）。
        """
        model = self.model()
        if model is None or not self._node_row_groups:
            return
        for sec in model.sections:
            if sec.key == "clip":
                continue      # clip 不分组（用户明确除外）
            def _key(row):
                gid, _n, pid = self._node_row_groups.get(row.fixture_id, ("", "", ""))
                # (母组键, 是否临时子组, 原序) → 同组连续、母组在子组之前
                return (pid or gid or "~", 1 if (not gid and pid) else 0, row.sub)
            try:
                sec.rows.sort(key=_key)
            except Exception:
                continue

    def _fallback_model(self, nodes: List[Any]) -> TableModel:
        """兜底表格模型（无设备表时）：分区=面板，效果槽列=节点，行=节点设备并集。"""
        from ...processing.dispatch.table_model import (
            COL_EFFECT, TableCell, TableModel, TableRow, TableSection,
        )
        model = TableModel(unit=self.unit(), row_mode=ROW_MODE_GROUPS)
        by_panel: Dict[str, List[Any]] = {}
        for n in nodes:
            by_panel.setdefault(str(getattr(n, "panel", "") or ""), []).append(n)
        for panel in sorted(by_panel):
            pnodes = by_panel[panel]
            fids: List[str] = []
            seen = set()
            for n in pnodes:
                for f in (getattr(n, "fixture_ids", None) or []):
                    if str(f) not in seen:
                        seen.add(str(f))
                        fids.append(str(f))
            sec = TableSection(key=panel, title=PANEL_LABELS.get(panel, panel),
                               folder=PANEL_FOLDERS.get(panel, ""))
            for i, n in enumerate(pnodes):
                tag = str(getattr(n, "display_tag", lambda: "")() or "")
                sec.columns.append(_ColSpec(
                    key=_node_col_key(n), ch_type="", count=1,
                    label=tag or str(getattr(n, "effect_name", "") or f"效果 {i + 1}"),
                    group=str(getattr(n, "summary", "") or ""), sort=(0, i)))
            sel_fids = {str(f) for n in pnodes
                        if bool(getattr(n, "selected", False))
                        for f in (getattr(n, "fixture_ids", None) or [])}
            for fid in fids:
                row = TableRow(row_id=fid, fixture_id=fid, label=fid[:8],
                               selected=(fid in sel_fids))
                for c, n in zip(sec.columns, pnodes):
                    covered = {str(x) for x in (getattr(n, "fixture_ids", None) or [])}
                    if covered and fid not in covered:
                        continue
                    row.cells.append(TableCell(
                        fixture_id=fid, col_key=c.key, col_ref=c.key,
                        effect_id=str(getattr(n, "effect_id", "") or ""),
                        tag=str(getattr(n, "summary", "") or "")))
                sec.rows.append(row)
            model.sections.append(sec)
        return model

    def nodes(self) -> List[Any]:
        """当前节点列表。"""
        return list(self._nodes)

    def node_count(self) -> int:
        return len(self._nodes)

    def _rebuild_node_map(self) -> None:
        """重建 节点 ↔ 格 的映射与视觉顺序（单一真源）。

        ★ 性能修复：原实现把 `keys = {k for k in sec_cells if ...}` 写在**节点循环
          内** → O(节点 × 格)（40 节点 × 8000 格 = 32 万次比较，实测 ~40ms/tick）。
          现改为**先建一次索引**（设备→格、列→格），节点只做 O(1) 查表 → O(节点 + 格)。
        """
        self._node_cells = {}
        self._node_order = []
        model = self.model()
        if model is None:
            return
        for sec in model.sections:
            nodes = self._nodes_by_panel.get(sec.key, [])
            if not nodes:
                continue
            # ── 一次遍历：建立本分区的两个索引 ─────────────────────────────
            sec_cells: List[Tuple[str, str]] = []
            cells_of_fixture: Dict[str, Set[Tuple[str, str]]] = {}
            cells_of_col: Dict[str, Set[Tuple[str, str]]] = {}
            for row in sec.rows:
                for c in sec.columns:
                    cell = row.cell_of(c.key)
                    if cell is None:
                        continue
                    k = (row.fixture_id, cell.col_key)
                    sec_cells.append(k)
                    cells_of_fixture.setdefault(row.fixture_id, set()).add(k)
                    # 列键：复合设备的格是 `列@子序号` → 归一到列键
                    cells_of_col.setdefault(str(c.key), set()).add(k)
            if sec.key in CHANNEL_PANELS or sec.key == CLIP_PANEL:
                # 通道/clip 分区：节点不占列 → 按其覆盖设备取格（查设备索引）
                for n in nodes:
                    nk = n.cell_key()
                    fids = [str(x) for x in (getattr(n, "fixture_ids", None) or [])]
                    if fids:
                        keys: Set[Tuple[str, str]] = set()
                        for f in fids:
                            keys |= cells_of_fixture.get(f, set())
                    else:
                        keys = set(sec_cells)      # 无设备集 = 全部（"全部设备"语义）
                    self._node_cells[nk] = keys
                    self._node_order.append(nk)
            else:
                # 效果类分区：一节点一列（列键与节点 cell_key 同构）→ 查列索引
                for n in nodes:
                    nk = n.cell_key()
                    ck = _node_col_key(n)
                    self._node_cells[nk] = set(cells_of_col.get(ck, set()))
                    self._node_order.append(nk)

    def selected_keys(self) -> Set[Tuple[str, str, str]]:
        """被选**节点**的 `cell_key()` 集合（节点级语义；表格内部按格选）。"""
        out: Set[Tuple[str, str, str]] = set()
        sel = set(getattr(self, "_selected", set()))
        for nk, keys in self._node_cells.items():
            if keys and keys & sel:
                out.add(nk)
        return out

    def selected_nodes(self) -> List[Any]:
        """被选节点（按视觉顺序；未选中返回空）。"""
        sel_keys = self.selected_keys()
        by_key = {n.cell_key(): n for n in self._nodes}
        return [by_key[k] for k in self._node_order if k in sel_keys and k in by_key]

    def set_selected(self, keys) -> None:
        """按**节点** `cell_key()` 选择（映射到覆盖的格），或按格键选择。

        两种入参都可能出现（旧测试传节点键，新代码可能传格键），因此按键长自动判别：
        3 元组 = 节点键；2 元组 = 格键。
        """
        want_nodes: Set[Tuple[str, str, str]] = set()
        want_cells: Set[Tuple[str, str]] = set()
        for k in (keys or set()):
            try:
                t = tuple(k)
            except Exception:
                continue
            if len(t) == 3:
                want_nodes.add(t)          # type: ignore[arg-type]
            elif len(t) == 2:
                want_cells.add(t)          # type: ignore[arg-type]
        if want_nodes:
            for nk in want_nodes:
                want_cells |= self._node_cells.get(nk, set())
        super().set_selected(want_cells)

    def select_all(self) -> None:
        """全选（节点级：即全选其覆盖的格）。"""
        all_cells: Set[Tuple[str, str]] = set()
        for keys in self._node_cells.values():
            all_cells |= keys
        if not all_cells:
            return
        super().set_selected(all_cells)
        self.selection_changed.emit(set(self._node_cells.keys()))

    def clear_selection(self) -> None:
        super().clear_selection()

    def selected_cells_of_node(self, node: Any) -> List[Tuple[str, str]]:
        """该节点当前被选中的格键（回落用；按视觉顺序）。"""
        keys = self._node_cells.get(node.cell_key(), set())
        if not keys:
            return []
        return [k for k in self._order_cells() if k in keys]

    def _order_cells(self) -> List[Tuple[str, str]]:
        """全部格的视觉顺序（基类 `_order` 即格键序——两侧语义已分开）。"""
        return list(getattr(self, "_order", []) or [])

    def node_order(self) -> List[Tuple[str, str, str]]:
        """节点的视觉顺序（节点级 API 的"顺序真源"）。

        ★ 与基类 `_order`（**格键**序）语义不同：本方法是**节点键**序。
          旧版把两者都叫 `_order`，表格化后格与节点不再是 1:1，必须分开
          （否则"按视觉顺序返回"的断言会在两种语义间摇摆）。
        """
        return list(self._node_order)


__all__ = ["EffectBusView", "PANEL_LABELS", "PANEL_FOLDERS"]
