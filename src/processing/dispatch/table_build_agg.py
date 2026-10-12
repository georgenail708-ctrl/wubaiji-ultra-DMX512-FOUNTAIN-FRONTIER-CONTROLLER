# -*- coding: utf-8 -*-
"""表格的**按组整合 / 行范围**构建（从 `table_build` 拆出：单文件 <=500 行）。

用户点名"参考 的设备按组整合逻辑，不要每盏灯都排列出来，多种模式"。
本模块负责"行怎么来"：
  · 组索引（已创建组优先，未建组按设备类型）
  · 聚合格（一行代表 N 台设备的同一灯位；组内取值不同 → 消费方显示「混合」）
  · 行范围（只显示有效果 / 整组全部）
与"列/单设备格"的构建（`table_build`）分开，两个文件都落在 500 行内。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .fixture_table import (
    ColSpec,
    channel_address_at,
    channel_addrs_for,
    dimmer_source_addrs,
    is_virtual_dimmer,
)
from .table_model import (
    ROW_MODE_EFFECT_ONLY,
    SCOPE_EFFECT_ONLY,
    SCOPE_GROUP_ALL,
    TableCell,
    TableRow,
)

#: 聚合格的通道取样地址上限（防大组把地址表撑爆；整合行只需判"是否一致"）
_AGG_ADDR_CAP = 64

#: 伪组 id（面板用的临时选择标记）：**不是**已创建组，不能作为整合行的组身份。
_PSEUDO_GROUP_IDS = frozenset({
    "__all__", "__topview_live__", "__selected__", "__multi__", "__none__", "",
})


def build_group_index(groups: Sequence[dict]) -> Dict[str, str]:
    """设备 id → 已创建组 id（同一设备属于多个组时取**范围最小**的那组）。

    ★ 与 `group_resolver.resolve_parent_group` 同一套"最小完整母集"语义：一台灯
      同属"全场"和"左半"两个组时，按组整合应归到更贴切的"左半"。这里只建索引
      （纯查表），不参与层级嵌套——嵌套仍由 resolver 在总线侧解析。
    """
    owner: Dict[str, str] = {}
    best: Dict[str, int] = {}
    for g in (groups or ()):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id", "") or "")
        if not gid or gid in _PSEUDO_GROUP_IDS:
            continue
        fids = [str(x) for x in (g.get("fixture_ids") or [])]
        for fid in fids:
            size = len(fids)
            if fid not in best or size < best[fid]:
                best[fid] = size
                owner[fid] = gid
    return owner

def _claim_fixtures(fids: List[str], key: str, sel_key: Dict[str, str],
                    best: Dict[str, int]) -> None:
    """把这些设备归到该选择；同属多个选择时取**设备数最少**者（最小完整母集）。"""
    size = len(fids)
    for fid in fids:
        if fid not in best or size < best[fid]:
            best[fid] = size
            sel_key[fid] = key


def _scan_selections(nodes: Sequence[Any]):
    """扫描节点 → (设备→选择键, 选择键→首现名, 选择键首现序)。"""
    sel_key: Dict[str, str] = {}
    best: Dict[str, int] = {}
    name_of: Dict[str, str] = {}
    order: List[str] = []
    for n in nodes or ():
        key = str(getattr(n, "group_key", "") or "")
        fids = [str(x) for x in (getattr(n, "fixture_ids", None) or [])]
        if not key or not fids:
            continue
        if key not in name_of:
            name_of[key] = _selection_name(n)
            order.append(key)
        _claim_fixtures(fids, key, sel_key, best)
    return sel_key, name_of, order


def _selection_name(node) -> str:
    """节点显示名；"" / "临时选中" 这类泛称 → ""（由调用方编号成"临时组 k"）。"""
    nm = str(getattr(node, "group_name", "") or "").strip()
    return nm if nm and nm != "临时选中" else ""


def _unique_names(name_of: Dict[str, str], order: List[str]) -> Dict[str, str]:
    """给每个选择一个**唯一**显示名。

    无名的按首现序编成"临时组 k"；**重名**的补序号 `(2)/(3)` ——
    否则两个不同选择若恰好同名（如都叫"主舞台"），两行标签仍不可区分，
    用户要求的"两行"就白做了（本函数即该 bug 的修复）。
    """
    generic = {k: i + 1 for i, k in enumerate(order) if not name_of.get(k)}
    seen: Dict[str, int] = {}
    out: Dict[str, str] = {}
    for k in order:
        nm = name_of.get(k) or f"临时组 {generic.get(k, 1)}"
        c = seen.get(nm, 0) + 1
        seen[nm] = c
        out[k] = nm if c == 1 else f"{nm} ({c})"
    return out


def _group_identity(groups: Sequence[dict]) -> Dict[str, Tuple[str, str, int]]:
    """设备 → (组id, 组名, 设备数)，同属多组取**设备数最少**者。"""
    out: Dict[str, Tuple[str, str, int]] = {}
    best: Dict[str, int] = {}
    for g in (groups or ()):
        ids, gid, nm = _usable_group(g)
        if not ids:
            continue
        for fid in ids:
            if fid not in best or len(ids) < best[fid]:
                best[fid] = len(ids)
                out[fid] = (gid, nm, len(ids))
    return out


def _usable_group(g: Any) -> Tuple[List[str], str, str]:
    """一个可参与行身份的已创建组 → (设备id列表, 组id, 组名)；不可用返回 ([], "", "")。"""
    if not isinstance(g, dict):
        return [], "", ""
    gid = str(g.get("id", "") or "")
    fids = [str(x) for x in (g.get("fixture_ids") or [])]
    if not gid or gid in _PSEUDO_GROUP_IDS or not fids:
        return [], "", ""
    return fids, gid, str(g.get("name", "") or gid)


def _selection_sizes(nodes: Sequence[Any]) -> Dict[str, int]:
    """选择键 → 该选择的设备数（多个节点同键时取最大）。"""
    sizes: Dict[str, int] = {}
    for n in nodes or ():
        k = str(getattr(n, "group_key", "") or "")
        fids = getattr(n, "fixture_ids", None) or []
        if k and fids:
            sizes[k] = max(sizes.get(k, 0), len(fids))
    return sizes


def build_row_identity(nodes: Sequence[Any],
                       groups: Sequence[dict]) -> Dict[str, Tuple[str, str]]:
    """设备 id → (行身份键, 显示名)：**最小完整集合取胜**。

    ★★ 用户 2026-09-14 定案："左边一组设备一个效果，右边一组设备另一个效果，
      应该出现**两行**，每行里的设备套同一个效果/值"，且"自动显示隶属于一个
      最小能把它们都涵盖的母集合"。

    候选身份 = 覆盖该设备的**节点选择** ∪ 包含该设备的**已创建组**；
    取**设备数最少**者作为行身份（"最小完整母集"语义，与 `group_resolver` 一致）：

      · 用户例：左组 4 台 / 右组 4 台，母集合「全场」8 台
        → 选择(4) < 组(8) → **选择取胜 → 两行** ✓
      · 一个节点覆盖全场、其下有两个已建组（4 台 / 2 台）
        → 组(2) < 选择(6) → **组取胜 → 两行** ✓

    这样"临时选择"与"已创建组"用**同一条尺子**比较，两种真实场景都正确；
    母集合自然退化为**分组条**（`_derive_row_groups` 的 parent）。
    """
    sel_key, name_of, order = _scan_selections(nodes)
    sel_names = _unique_names(name_of, order)
    sel_size = _selection_sizes(nodes)
    grp_of = _group_identity(groups)
    out: Dict[str, Tuple[str, str]] = {}
    for fid in set(sel_key) | set(grp_of):
        cands: List[Tuple[int, str, str]] = []
        k = sel_key.get(fid)
        if k:
            cands.append((sel_size.get(k, 1), f"sel:{k}", sel_names.get(k) or "临时组"))
        g = grp_of.get(fid)
        if g:
            cands.append((g[2], f"gid:{g[0]}", g[1]))
        if cands:
            cands.sort(key=lambda c: c[0])
            out[fid] = (cands[0][1], cands[0][2])
    return out


def build_type_identity(fids: Sequence[str],
                        info_by_id: Dict[str, dict]) -> Dict[str, Tuple[str, str]]:
    """设备 id → `(f"type:{def_id}", 类型族名)`：**同类型设备折叠成一行**。

    ★ 用户 2026-09-14 定案（监控区只反映"当前选中设备"）：
      "监控区显示的设备…只与当前选中了哪些设备有关…设备完全按照**设备类型**
       分类"。因此"选择表"的行身份**只按 `def_id`**，不掺已创建组、不掺
      临时选中/面板来源节点 —— 与 `build_row_identity`（供来源/全量视图）区分开。
      `info_by_id` 里没有的设备跳过（不产出行）。
    """
    out: Dict[str, Tuple[str, str]] = {}
    for fid in (fids or ()):
        info = info_by_id.get(str(fid))
        if info is None:
            continue
        def_id = str(info.get("def_id", "") or "")
        out[str(fid)] = (f"type:{def_id}", _type_label(info))
    return out


def _agg_key_of(info: dict, fid: str, group_index: Dict[str, str],
                group_names: Dict[str, str],
                sel_of: Optional[Dict[str, Tuple[str, str]]] = None) -> Tuple[str, str]:
    """一台设备的整合分组键与显示名。

    优先级：
      1. **行身份**（`sel_of`，`build_row_identity` 产物：已按"最小完整集合"
         在"节点选择"与"已创建组"之间**取胜**）→ 直接用
      2. 已创建组（`group_index`，兜底：设备不在任何选择覆盖范围内时）
      3. 设备**类型**（def_id）→ 类型族名（大工程"240 台同一型号"整合成一行）
    """
    ident = (sel_of or {}).get(str(fid))
    if ident:
        return ident
    gid = group_index.get(str(fid))
    if gid:
        return f"gid:{gid}", group_names.get(gid, gid)
    def_id = str(info.get("def_id", "") or "")
    if def_id:
        return f"def:{def_id}", _type_label(info)
    return f"one:{fid}", str(info.get("name") or fid[:8])

def _type_label(info: dict) -> str:
    """同类型设备的显示名：去掉名字尾部数字，得到"灯"/"跑泉"这类族名。"""
    name = str(info.get("name") or "").strip()
    if not name:
        return str(info.get("def_id") or "设备")
    i = len(name)
    while i > 0 and (name[i - 1].isdigit() or name[i - 1] in " 　-_"):
        i -= 1
    base = name[:i].strip()
    return base or name

def _agg_channel_cells(row: TableRow, cols: Sequence[ColSpec],
                       info_by_id: Dict[str, dict]) -> None:
    """整合行的通道格：每格引用**该组多台设备**的同一灯位地址。

    ★ 一致性判定放在**消费方（CellOps）**做：格子里存"这台组内每台设备该列的
      取样地址（按设备对齐的列表）"，消费方读值后若全同 → 显示该值，否则显示
      「混合」。这样表模型保持纯数据、且能复用同一套取值链路（不新增第二条取数路径）。

    `src_addrs` 的每项是 `(device_index, universe, addr)`（三元组）——与单设备格
    的 `(universe, addr)` 二元组区分靠长度判断。
    """
    for col in cols:
        samples: List[Tuple[int, int, int]] = []
        for di, fid in enumerate(row.fids):
            info = info_by_id.get(fid) or {}
            if is_virtual_dimmer(info, col.ch_type):
                for uni, addr in dimmer_source_addrs(info):
                    samples.append((di, uni, addr))
                continue
            addr = channel_address_at(info, col.ch_type, row.sub)
            if addr is not None:
                samples.append((di, int(addr[0]), int(addr[1])))
        first_info = info_by_id.get(row.fids[0]) or {} if row.fids else {}
        virtual = is_virtual_dimmer(first_info, col.ch_type)
        row.cells.append(TableCell(
            fixture_id=row.fixture_id,
            col_key=col.key, col_ref=col.key,
            ch_type=col.ch_type, occ=row.sub,
            tag=f"{len(row.fids)} 台 · {col.label}",
            virtual=virtual,
            src_addrs=tuple(samples[:_AGG_ADDR_CAP]),
            agg=True,     # 见 TableCell.agg
        ))
        row.cells[-1].fids = list(row.fids)

def _agg_effect_cells(row: TableRow, nodes: Sequence[Any],
                      cols: Sequence[ColSpec]) -> None:
    """整合行的效果格：该效果在本组内覆盖的设备若全有值→取该值，否则「混合」。"""
    by_col = {c.key: n for c, n in zip(cols, nodes)}
    for col in cols:
        n = by_col.get(col.key)
        if n is None:
            continue
        covered = {str(x) for x in (getattr(n, "fixture_ids", None) or [])}
        fids = [f for f in (row.fids or [row.fixture_id]) if f in covered]
        if not fids:
            continue      # 该效果不覆盖本组 → 空格
        cell = TableCell(
            fixture_id=row.fixture_id, col_key=col.key, col_ref=col.key,
            effect_id=str(getattr(n, "effect_id", "") or ""),
            tag=str(getattr(n, "summary", "") or ""),
            level=float(getattr(n, "level", -1.0) or -1.0),
            live=bool(getattr(n, "live", True)),
            agg=True)
        cell.fids = fids
        row.cells.append(cell)

def _agg_row_label(name: str, n_fixtures: int, sub: int, multi_sub: bool) -> str:
    """整合行标签：`N × 设备名`（语汇，台数在前）；复合设备附灯位。"""
    label = f"{n_fixtures} × {name}"
    if multi_sub:
        label += f" · 灯位 {sub + 1}"
    return label


def _rows_for_agg(info_by_id: Dict[str, dict], fid_list: Sequence[str],
                  cols: Sequence[ColSpec], *, group_index: Dict[str, str],
                  group_names: Dict[str, str],
                  max_sub: int = 1,
                  sel_of: Optional[Dict[str, Tuple[str, str]]] = None,
                  label_positions: bool = True) -> List[TableRow]:
    """按组整合出行：同组设备按**灯位（子序号）**对齐合并。

    行标签格式**对齐 **（见 `_agg_row_label`）：`N × 设备名`。
    例：主舞台 8 台 RGBW → 「8 × 主舞台」；未建组时按设备类型 → 「240 × 灯」。

    `sel_of` 让**不同的选择各占一行** —— 用户："左边一组一个效果、右边一组另一个
    效果 → 两行"。

    ★ `label_positions=False`：标签**不带**「· 灯位 k」后缀，且调用方通常配 `max_sub=1`
      → **一个设备类型只出一行**。用户 2026-09-14（监控区）："我明明只框了一组设备…
      但是出现了重复显示的几行。为什么会有什么灯位 1 2 3 4 这种。**不要这样**"。
      灯位改在**格内**合并（见 `_selection_channel_cells`）：不再分行、不再上标签。
    """
    buckets: Dict[Tuple[str, str], List[str]] = {}
    order: List[Tuple[str, str]] = []
    for fid in fid_list:
        info = info_by_id.get(fid)
        if info is None:
            continue
        key, name = _agg_key_of(info, fid, group_index, group_names, sel_of)
        if key not in buckets:
            buckets[key] = []
            order.append((key, name))
        buckets[key].append(fid)
    rows: List[TableRow] = []
    ctx = _BucketCtx(info_by_id, cols, max_sub, label_positions)
    for key, name in order:
        rows.extend(_rows_for_bucket(key, name, buckets[key], ctx))
    return rows


@dataclass(frozen=True)
class _BucketCtx:
    """拆行所需的共享输入（收扰长参数表，满足位置参数 ≤5 门禁）。"""
    info_by_id: Dict[str, dict]
    cols: Sequence[ColSpec]
    max_sub: int
    label_positions: bool


def _rows_for_bucket(key: str, name: str, fids: List[str],
                     ctx: "_BucketCtx") -> List[TableRow]:
    """一个分组桶 → 若干行（按灯位拆 `max_sub` 行；无灯位则只有 sub=0 一行）。"""
    info_by_id, cols = ctx.info_by_id, ctx.cols
    subs = [_sub_total_of(info_by_id.get(f) or {}, cols) for f in fids]
    out: List[TableRow] = []
    for sub in range(max(1, int(ctx.max_sub))):
        sub_fids = [f for f, n in zip(fids, subs) if n > sub]
        if not sub_fids:
            continue
        info0 = info_by_id.get(sub_fids[0]) or {}
        if ctx.label_positions:
            label = _agg_row_label(name, len(sub_fids), sub, any(n > 1 for n in subs))
        else:
            label = f"{len(sub_fids)} × {name}"
        out.append(TableRow(
            row_id=f"{key}::{sub}",
            fixture_id=sub_fids[0],
            label=label,
            universe=int(info0.get("universe", 1) or 1),
            start_channel=int(info0.get("start_channel", 1) or 1),
            sub=sub, sub_total=max(1, int(ctx.max_sub)),
            fids=list(sub_fids), agg=True, agg_count=len(sub_fids)))
    return out

def _selection_channel_cells(row: TableRow, cols: Sequence[ColSpec],
                             info_by_id: Dict[str, dict]) -> None:
    """监控区单行（= 一个设备类型）的通道格：**跨所有设备 × 所有灯位**取样。

    ★ 用户 2026-09-14："不要 灯位1/2/3/4 这种" —— 一个设备类型**只出一行**；
      复合设备（一台含多组灯位，如调压气爆 4 组）的各灯位**并入同一格**，由消费方
      按"全同显示该值 / 不同显示「混合」"处理 —— 与"跨设备聚合"完全同一套语义
      （`_agg_channel_cells` 的跨设备版本）。
    """
    for col in cols:
        samples = _selection_samples(row, col, info_by_id)
        if not samples:
            continue
        first_info = info_by_id.get(row.fids[0]) or {} if row.fids else {}
        row.cells.append(TableCell(
            fixture_id=row.fixture_id,
            col_key=col.key, col_ref=col.key,
            ch_type=col.ch_type, occ=0,
            tag=f"{len(row.fids)} 台 · {col.label}",
            virtual=is_virtual_dimmer(first_info, col.ch_type),
            src_addrs=tuple(samples[:_AGG_ADDR_CAP]),
            agg=True))
        row.cells[-1].fids = list(row.fids)
        # ★ 监控区 = 设备类型 × 值类型 → 语义显示归 programmer（条带/翼块）
        row.cells[-1].panel = "programmer"


def _selection_samples(row: TableRow, col: ColSpec,
                       info_by_id: Dict[str, dict]) -> List[Tuple[int, int, int]]:
    """该行该列的取样地址：组内**每台设备 × 每个灯位**的 (device_index, uni, addr)。

    · 虚拟亮度（dimmer）：取该设备 R/G/B/W 的全部地址（亮度语义由消费方取最大）；
    · 其它通道：取该类型在该设备上的**全部**出现（复合设备多组灯位都算），
      而不是只取 `row.sub` 那一个灯位。
    """
    samples: List[Tuple[int, int, int]] = []
    for di, fid in enumerate(row.fids):
        info = info_by_id.get(fid) or {}
        if is_virtual_dimmer(info, col.ch_type):
            for uni, addr in dimmer_source_addrs(info):
                samples.append((di, uni, addr))
            continue
        for uni, addr in channel_addrs_for(info, col.ch_type):
            samples.append((di, int(uni), int(addr)))
    return samples


def _resolve_rows_by_mode(info_by_id: Dict[str, dict], fixtures_order: Sequence[str],
                          covered: Sequence[str], row_mode: str, scope: str) -> List[str]:
    """按行模式决定"参与列出的设备"（整合模式在 `_rows_for_agg` 里再合并）。"""
    if scope == SCOPE_GROUP_ALL and row_mode != ROW_MODE_EFFECT_ONLY:
        return [f for f in fixtures_order if f in info_by_id]
    return [f for f in covered if f in info_by_id]


def _sub_total_of(info: dict, cols: Sequence[ColSpec]) -> int:
    """该设备要拆几行子设备 = 本区列里该设备拥有的最大出现次数（≥1）。

    按**该设备实际拥有**的列取 max，而不是全局列 count：全局 count 可能被别台
    复合设备抬高（如 4 组灯的邻居），不该把普通 3 通道灯也拆成 4 行。
    """
    per: Dict[str, int] = {}
    for ct in (info.get("channels") or []):
        per[ct] = per.get(ct, 0) + 1
    n = 1
    for col in cols:
        if col.ch_type == "dimmer":
            continue          # 虚拟亮度不参与子设备拆分
        n = max(n, int(per.get(col.ch_type, 0)) or 1)
    return max(n, 1)
