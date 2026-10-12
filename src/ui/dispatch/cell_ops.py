# -*- coding: utf-8 -*-
"""调度台**逐格操作**适配层（读值 / 设值 / 清空 / 标记）。

存在理由（用户需求 2026-09-13）：
  表格视图的每个格子都要能"读当前值、右键设值、清空、标记保存"。这些动作跨三个
  真源（NetworkManager 混成层 / Programmer 行值缓存 / 调度台自身标记），若散在 UI
  的鼠标回调里，就会出现"清空只清了画面没清层""值0 与已清空分不清"这类历史 bug。

  ★ 因此把**全部**逐格语义集中在本模块，UI 只调这里的方法。纪律：
    1. 写值走 Programmer 行既有路径（`ChannelTypeRow.set_cell_value` → 信号 →
       pending → 33ms flush），保证 pending/keepalive/录制链路一致，不新开输出路径。
    2. 清空必须**双清**：NetworkManager 层 `active=False`（真清空）**且** 清掉行
       缓存（否则 keepalive 会把旧值灌回层里 —— 这是"release 后监视器不变"的根因）。
    3. "设为 0" 与 "清空" 是**两个不同动作**：前者写值 0 且 `active=True`
       （有效果且暗），后者 `active=False`（无归属）。两者在本模块各有独立方法，
       调用方（右键菜单）并列提供，不合并。
    4. 本模块**不 import 任何效果面板**、不 import MainWindow；面板实例由调用方
       注入（`panel_provider`），保持调度台零耦合（路由是 MainWindow 的职责）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...core.constants import CLIP_LAYERS as _CLIP_LAYERS
from . import cell_clear as _clear
from . import cell_read as _read
from .cell_clear import CLEAR_LAYERS as _CLEAR_LAYERS
from .cell_read import Record

#: 按地址写入用的层（与 Programmer 手工值的层一致；见 constants.BASE_LAYERS）
DEFAULT_LAYER = "fader"

#: 释放覆盖的层集合同源真源在 `cell_clear.CLEAR_LAYERS`（= BASE + SHAPE，
#: 与 `NetworkManager.compute_editor_active_mask` 读的集合一致；已在上面 import）。
#: ★ 只清 fader 是 bug：值可能由 programmer/effect/matrix 等层持有，漏清会让该通道
#:   继续显示"有效果"（用户 2026-09-14："右击为什么不能 release"）。


@dataclass
class CellRef:
    """一个格的**操作句柄**（表格视图 → 逐格动作的载体）。

    它是纯描述（无 Qt、无 nm），把"画在哪"与"对应哪个 DMX 地址"绑在一起：
        fixture_id 设备 id（层清理与行缓存都要它）
        col_key    格身份键（含子序号，如 `r@2`）—— 标记表与选中集用它
        ch_type    值类型（行缓存与"仅清空当前值类型"用它）
        universe / addr  物理地址（None = 虚拟通道，只清行缓存）
        virtual    虚拟通道（无物理地址）
        src_addrs  取样地址；(universe, addr) 或聚合格的 (device_index, universe, addr)
        fids       聚合格代表的设备（批量设值/清空用）；单设备格为空
    """
    fixture_id: str
    col_key: str
    ch_type: str = ""
    universe: Optional[int] = None
    addr: Optional[int] = None
    virtual: bool = False
    src_addrs: Tuple[Tuple[int, ...], ...] = ()
    fids: Tuple[str, ...] = ()
    #: 这格当前被哪些混音层控制（"这值是谁写的"）——来源角标 + 双击路由用。
    owners: Tuple[str, ...] = ()

    def key(self) -> Tuple[str, str]:
        return (str(self.fixture_id), str(self.col_key))


def _is_agg_addrs(src_addrs) -> bool:
    """取样地址是聚合格（三元组）还是单设备格（二元组）。"""
    for item in (src_addrs or ()):
        return len(tuple(item)) >= 3
    return False


def refs_from_cells(cells: Sequence[Any], addr_lookup: Callable[[Any], Optional[Tuple[int, int]]],
                    virtual_lookup: Optional[Callable[[Any], bool]] = None) -> List[CellRef]:
    """把表格模型的 TableCell 序列转成 CellRef 序列。

    `addr_lookup(cell)` 由调用方注入（UI 层用 fixture_table.channel_address_at +
    设备信息表实现），使本模块不依赖设备表的内部结构——依赖方向保持单向。
    虚拟通道与聚合格的取样地址从 cell.src_addrs 透传（由表模型算好）。
    """
    out: List[CellRef] = []
    for c in (cells or ()):
        uni = addr = None
        try:
            got = addr_lookup(c)
        except Exception:
            got = None
        if got is not None:
            uni, addr = int(got[0]), int(got[1])
        virt = False
        if virtual_lookup is not None:
            try:
                virt = bool(virtual_lookup(c))
            except Exception:
                virt = False
        srcs: Tuple[Tuple[int, ...], ...] = ()
        try:
            srcs = tuple(tuple(int(x) for x in item)
                         for item in (getattr(c, "src_addrs", None) or ()))
        except Exception:
            srcs = ()
        fids: Tuple[str, ...] = ()
        try:
            if getattr(c, "agg", False):
                fids = tuple(str(f) for f in (getattr(c, "fids", None) or ()))
        except Exception:
            fids = ()
        out.append(CellRef(
            fixture_id=str(getattr(c, "fixture_id", "") or ""),
            col_key=str(getattr(c, "col_key", "") or ""),
            ch_type=str(getattr(c, "ch_type", "") or ""),
            universe=uni, addr=addr, virtual=virt, src_addrs=srcs, fids=fids))
    return out


def _safe(fn):
    """执行 `fn()`，异常返回 None（批量取数容错，不让单个域失败拖垮整表）。"""
    try:
        return fn()
    except Exception:
        return None


def _editor_universe(nm, uni: int):
    """该域**编辑器层**的混合输出（排除 clip）——"我正在编辑什么"的真源。

    优先用 NM 既有 `compute_editor_universe_now`（录制/连续动作过渡锚点同款，
    语义一致）；缺失时回退 `get_universe_data`（兼容旧 NM 替身）。
    """
    fn = getattr(nm, "compute_editor_universe_now", None)
    if fn is not None:
        try:
            return fn(uni)
        except Exception:
            pass
    return nm.get_universe_data(uni)


def _layer_owners(nm, uni: int, channels) -> Dict[int, Tuple[str, ...]]:
    """每通道被哪些层控制。优先**批量** API（一次加锁），回退逐通道。

    ★ 排除 clip 层：来源角标与双击路由只应指向"编辑来源"
      （用户："监控区…与 clip 完全无关"）。
    """
    try:
        if hasattr(nm, "get_channels_owners"):
            return dict(nm.get_channels_owners(
                uni, channels, exclude_layers=_CLIP_LAYERS) or {})
        if hasattr(nm, "get_channel_owners"):
            return {a: tuple(nm.get_channel_owners(uni, a) or ()) for a in channels}
    except Exception:
        pass
    return {}


class CellOps:
    """逐格操作器。

    依赖注入（都由 MainWindow 提供，测试可传假实现）：
        nm_provider()      -> NetworkManager | None
        panel_provider(id) -> 效果面板 | None（按调度台 node["panel"] 取）
        cell_map()         -> {fixture_id: 该设备可用通道类型集合}（可选，做能力校验）
    """

    def __init__(self, nm_provider: Optional[Callable[[], Any]] = None,
                 panel_provider: Optional[Callable[[str], Any]] = None,
                 cell_map: Optional[Callable[[], Dict[str, set]]] = None,
                 *, info_provider: Optional[Callable[[], dict]] = None) -> None:
        self._nm_provider = nm_provider
        self._panel_provider = panel_provider
        self._cell_map = cell_map
        #: 设备信息表 provider（清空聚合格时**现算**地址用；见 `cell_clear`）
        self._info_provider = info_provider
        #: 本会话调度台自己的 S 标记（非 Programmer 的效果槽格用）
        self._marks: Dict[Tuple[str, str], bool] = {}

    # ── 页面级释放（存场景后"拍完即清台"）───────────────────────────────
    def release_all_editor(self) -> int:
        """清掉**全部编辑层**在**所有域**上的占用（页面级释放）。

        ★ 用户 2026-09-14："一个效果保存到场景之后，其应该自动全 release 状态，
          也就是监控区显示通道都没有效果了。" —— 与控台"录完 cue 即清台"同一语义：
          效果已进槽位，现场回到"无效果"起点，方便接着做下一个场景。

        与 `clear_cells` 的分工：那个按格清（知道清谁）；本方法不知道格，直接
        按层整域清空 —— 用 `NetworkManager.clear_layer(layer)`（无域参数 = 全域）。

        返回清掉的层数（便于状态栏提示）；无 NM 时返回 0。
        """
        nm = self._nm()
        if nm is None or not hasattr(nm, "clear_layer"):
            return 0
        n = 0
        for layer in _CLEAR_LAYERS:
            try:
                nm.clear_layer(str(layer))
                n += 1
            except Exception:
                continue
        return n

    def clear_all_editor_in_universe(self, universe: int) -> int:
        """只清某域的编辑层（页面级释放的定位版本，供调试/局部清台）。"""
        nm = self._nm()
        if nm is None or not hasattr(nm, "clear_layer"):
            return 0
        n = 0
        for layer in _CLEAR_LAYERS:
            try:
                nm.clear_layer(str(layer), int(universe))
                n += 1
            except Exception:
                continue
        return n

    # ── 依赖 ────────────────────────────────────────────────────────────
    def _nm(self):
        if self._nm_provider is None:
            return None
        try:
            return self._nm_provider()
        except Exception:
            return None

    def _programmer(self):
        if self._panel_provider is None:
            return None
        try:
            return self._panel_provider("programmer")
        except Exception:
            return None

    def _row(self, fixture_id: str):
        """取该设备所在的行（`ChannelTypeRow`）；Programmer 未加载时返回 None。"""
        pw = self._programmer()
        if pw is None:
            return None
        ch_list = getattr(pw, "_ch_list", None)
        if ch_list is None:
            return None
        fid = str(fixture_id)
        for row in (getattr(ch_list, "_rows", None) or []):
            id_set = getattr(row, "_fixture_id_set", None)
            if id_set is None:
                continue
            if fid in id_set:
                return row
        return None

    # ── 读 ──────────────────────────────────────────────────────────────
    def read_cells(self, cells: Sequence[Any]) -> Dict[Tuple[str, str], Tuple[Optional[int], bool]]:
        """批量读一组格的 (值, 是否有归属)。用于**单设备格**。

        取值为**混合后的实时输出**（`get_universe_data`），归属为任一层是否控制
        （`get_channels_ownership`）——每域只查一次，避免逐格加锁（性能契约）。

        ★ 虚拟通道（虚拟 dimmer）：无自身地址，值是"灯色最大输出"
          （Programmer 的 dimmer 增益语义）——取 `src_addrs` 中的最大值。
        """
        out: Dict[Tuple[str, str], Tuple[Optional[int], bool]] = {}
        for rec in self.read_cells_ex(cells):
            out[rec[0]] = (rec[2], rec[3])
        return out

    def read_cells_ex(self, cells: Sequence[Any]):
        """扩展读取：产出 `(key, spread, value, owned, fids, owners)`。

        `spread` 为该格内**不同取值的个数**（聚合格用它判「混合」；单设备格恒 ≤1）。
        `owners` 为该格被哪些混音层控制（如 `("effect","matrix")`）——用于
        "这值是谁写的"来源角标与双击跳转对应面板（用户点名）。

        实现主体在 `cell_read`（按数据流分四步，见该模块说明）。这里只做编排：
        收集有效格 → 逐域取数（**每域一次**）→ 逐格汇总。
        """
        results: List[Record] = []
        refs = _read.collect_refs(cells)
        if not refs:
            return results
        nm = self._nm()
        if nm is None:
            for c in refs:
                results.append(((str(c.fixture_id), str(c.col_key)), 0, None, False,
                                tuple(getattr(c, "fids", None) or ()), ()))
            return results
        by_uni = _read.collect_by_universe(refs)
        # ★★ 2026-09-14 用户定案：取值读**编辑器层**（排除 clip）——
        #   "不要管 clip 播放引起的好像这个设备有值，而是只在我真的在各个功能页面
        #    对这个设备有编辑的情况下才出值…清空都按了也没用"。故值 = 编辑器层混合
        #   输出（非 get_universe_data），归属 = 编辑器层是否在控制。
        data_cache, own_cache, owners_cache = _read.read_caches(
            nm, by_uni,
            editor_universe_fn=lambda n, u: _safe(lambda: _editor_universe(n, u)),
            ownership_fn=lambda n, u, chs: _safe(
                lambda: n.get_channels_ownership(
                    u, chs, exclude_layers=_CLIP_LAYERS)),
            owners_fn=_layer_owners)
        for c in refs:
            if getattr(c, "src_addrs", None):
                results.append(_read.summarize_agg(
                    c, data_cache, own_cache, owners_cache))
            else:
                results.append(_read.summarize_plain(
                    c, data_cache, own_cache, owners_cache))
        return results

    # ── 写 ──────────────────────────────────────────────────────────────
    def write_cells(self, cells: Sequence[Any], value: int,
                    layer: str = DEFAULT_LAYER) -> int:
        """把一组格设为同一个值（**写值 0 也算有效果**：active=True）。

        走 Programmer 行的既有路径（含 `_marked` 更新与信号），返回成功格数。
        聚合格会**展开到组内每台设备**（"按组整合"视图里改一行 = 改组内全部灯）。
        未加载 Programmer 或该设备不在行内时跳过（不静默改层，避免两真源打架）。
        """
        n = 0
        val = max(0, min(255, int(value)))
        for fid in self._target_fixtures(cells):
            ch_type = str(getattr(fid[1], "ch_type", "") or "")
            if not ch_type:
                continue
            row = self._row(fid[0])
            if row is None:
                continue
            try:
                row.set_cell_value(fid[0], ch_type, val, layer=layer)
                n += 1
            except Exception:
                continue
        return n

    @staticmethod
    def _target_fixtures(cells: Sequence[Any]):
        """把格序列展开成 `(fixture_id, cell)` 序列（聚合格 → 组内每台设备）。"""
        out = []
        for c in (cells or ()):
            fids = tuple(getattr(c, "fids", None) or ())
            if fids:
                out.extend((str(f), c) for f in fids)
            else:
                out.append((str(getattr(c, "fixture_id", "") or ""), c))
        return out

    def adjust_cells(self, cells: Sequence[Any], delta: int,
                     layer: str = DEFAULT_LAYER) -> int:
        """相对增减（右拖快速编辑）：在当前值基础上 ±delta，逐格夹紧 0-255。

        聚合格用其**显示值**作基准，调整后同步展开到组内全部设备（保持一致）。
        """
        n = 0
        cur = self.read_cells(cells)
        for c in (cells or ()):
            ch_type = str(getattr(c, "ch_type", "") or "")
            if not ch_type:
                continue
            base = cur.get((str(c.fixture_id), str(c.col_key)), (None, False))[0]
            base = 0 if base is None else int(base)
            v = max(0, min(255, base + int(delta)))
            for fid, _cc in self._target_fixtures([c]):
                row = self._row(fid)
                if row is None:
                    continue
                try:
                    row.set_cell_value(fid, ch_type, v, layer=layer)
                    n += 1
                except Exception:
                    continue
        return n

    # ── 清空（真清空：active=False）──────────────────────────────────────
    def clear_cells(self, cells: Sequence[Any], layer: Optional[str] = None) -> int:
        """**真清空**一组格：NetworkManager 层置 `active=False` + 清行缓存。

        ★ 双清不可省：只清层，行缓存会在 keepalive/重建时把旧值灌回去；只清行缓存，
        层里仍是 active=True，监视器与表格都会继续显示"有效果"。

        `layer=None`（默认）= 释放该地址在**全部编辑层**上的占用（programmer/fader/
        effect/matrix… 但不含 clip 播放层）。要只清某一层就显式传 `layer=...`。

        ★★ R136（大组清空只清前 64 台）：聚合格展开**不走被截断的 `src_addrs`**，
        改按 `fids × ch_type` 现算（`info_provider` 提供设备信息表；见 `cell_clear`）。
        """
        nm = self._nm()
        targets = _clear.expand_clear_targets(cells, self._fixture_info())
        total = _clear.clear_layer_addrs(nm, targets, layer)
        _clear.clear_row_stores(self._row, targets)
        for c in (cells or ()):
            self._marks.pop((str(c.fixture_id), str(c.col_key)), None)
        return total

    def _fixture_info(self) -> dict:
        """设备信息表（清空聚合格时现算地址用）；未注入返回 {}。"""
        if self._info_provider is None:
            return {}
        try:
            return dict(self._info_provider() or {})
        except Exception:
            return {}

    def clear_value_type(self, ch_type: str, cells: Sequence[Any],
                         layer: Optional[str] = None) -> int:
        """「仅清空当前值类型」：只清该值类型的格（其余列不动）。"""
        ct = str(ch_type or "")
        picked = [c for c in (cells or ()) if str(getattr(c, "ch_type", "") or "") == ct]
        return self.clear_cells(picked, layer=layer)

    def release_column(self, ch_type: str, cells: Sequence[Any],
                       layer: Optional[str] = None) -> int:
        """「释放该列（全部设备）」：清该值类型在**全部已知设备**上的层与缓存。

        与 `clear_value_type` 的差别只在语义表述（列头右键 vs 格右键），实现同一份，
        避免两套清理逻辑漂移；保留两个入口是为了菜单文案与意图清晰。
        """
        return self.clear_value_type(ch_type, cells, layer=layer)

    # ── 标记（S）────────────────────────────────────────────────────────
    def mark_cells(self, cells: Sequence[Any], marked: bool = True) -> int:
        """标记 / 取消标记一组格（写本会话标记表 + Programmer 行标记）。"""
        n = 0
        for c in (cells or ()):
            key = (str(c.fixture_id), str(c.col_key))
            if marked:
                self._marks[key] = True
            else:
                self._marks.pop(key, None)
            created = bool(getattr(c, "ch_type", ""))
            if created:
                row = self._row(getattr(c, "fixture_id", ""))
                if row is not None and hasattr(row, "unmark_cell") and not marked:
                    try:
                        row.unmark_cell(str(c.fixture_id), str(c.ch_type))
                    except Exception:
                        pass
            n += 1
        return n

    def is_marked(self, fixture_id: str, col_key: str, marked_by_panel: bool = False) -> bool:
        """该格是否已标记保存。

        Programmer 通道列的 S 标记**天然来自 Programmer**（编程器碰过的通道，
        与既有语义一致 —— 用户在 Programmer 调过的值本来就该是"要存"的候选），
        其余列用本会话标记表。两者取**或**，不互相覆盖。
        """
        if marked_by_panel:
            return True
        return bool(self._marks.get((str(fixture_id), str(col_key))))

    def marked_keys(self) -> List[Tuple[str, str]]:
        """本会话被标记的格（工具栏"保存已标记"用）。"""
        return [k for k, v in self._marks.items() if v]

    def clear_marks(self) -> None:
        self._marks.clear()


__all__ = ["CellOps", "CellRef", "refs_from_cells", "DEFAULT_LAYER"]
