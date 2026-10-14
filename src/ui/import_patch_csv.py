# -*- coding: utf-8 -*-
"""R4 配接表 (CSV) 解析 + 坐标/形状/朝向推断。

纯逻辑模块：CSV 读取、按设备名分组、坐标变换、圆/弧/网格拟合、旋转→朝向
都不依赖 Qt，可 headless 单测。仅形状分类在需要时**懒导入** topview_panel
的几何算法（_fit_circle 等），以保证与 TopView 现场反算行为完全一致、避免分叉。

列含义（对照 R4 导出表，0-based 索引）：
 0 是否配接(Checked)  1 序号ID  2 名称(=设备类型)  3 DMX线路(universe)
 4 DMX通道(起始通道)  5 DMX地址(combined)  6 舞台位置  7 制造商
 8 通道数量(每台)     9 X位置(cm) 10 Z位置(cm,替代Y) 11 X(米) 12 Z(米)
 13 X轴旋转(俯仰)     14 Z轴旋转(俯视朝向)
"""
from __future__ import annotations

import csv
import io
import hashlib
import math
import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

# CSV 列索引
COL_CHECKED, COL_ID, COL_NAME = 0, 1, 2
COL_UNIVERSE, COL_START_CH, COL_ADDR = 3, 4, 5
COL_STAGE_POS, COL_MAKER, COL_CH_COUNT = 6, 7, 8
COL_X_CM, COL_Z_CM, COL_X_M, COL_Z_M = 9, 10, 11, 12
COL_XROT, COL_ZROT = 13, 14
MIN_COLS = 15

# 画布标定/导入缩放：唯一真源在 core.constants（2026-09-12 收敛；
# 此前 playback_engine 内另抄一份，形成"改一处漏一处"+层反转）。
from ..core.constants import (  # noqa: F401
    CANVAS_UNITS_PER_CM, DEFAULT_IMPORT_SCALE, Y_AXIS_SIGN,
)


def _f(v, d: float = 0.0) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return d


def _i(v, d: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return d


def normalize_angle(a: float) -> float:
    """归一化到 [-180, 180]。"""
    a = float(a) % 360.0
    if a > 180.0:
        a -= 360.0
    return a


# ──────────────────────────────────────────────────────────────────────────────
#  数据结构
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PatchRow:
    seq_id: int
    type_name: str          # col2 stripped — FixtureDefinition.name 去重键
    universe: int           # col3
    start_channel: int      # col4 (1-based)
    channel_count: int      # col8 (每台)
    x_cm: float             # col9
    z_cm: float             # col10 (替代 Y)
    x_rot_deg: float        # col13
    z_rot_deg: float        # col14
    checked: bool
    y_cm: float = 0.0        # 高度(PosY)；CSV 无此列默认 0，导入器可覆盖

    @property
    def is_patchable(self) -> bool:
        # ch=0 行是 R4 的容器/分组节点（Fixture Module / Holder 等），不可配接
        return self.channel_count > 0


@dataclass
class TypeGroup:
    type_name: str
    channel_count: int                          # 该类型众数通道数
    rows: List[PatchRow] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
#  读取
# ──────────────────────────────────────────────────────────────────────────────
def _looks_like_data(row: List[str]) -> bool:
    """第二列(序号ID)是纯数字 → 该行是数据行（说明无表头）。"""
    try:
        int(float(str(row[COL_ID]).strip()))
        return True
    except Exception:
        return False


def read_patch_csv(path: str) -> Tuple[List[PatchRow], List[str]]:
    """读取 R4 配接表。返回 (rows, warnings)。

    编码回退链：gb18030 → cp936 → utf-8-sig → utf-8；全失败则 gb18030 容错。
    """
    warnings: List[str] = []
    raw: Optional[str] = None
    for enc in ("gb18030", "cp936", "utf-8-sig", "utf-8"):
        try:
            with io.open(path, "r", encoding=enc, newline="") as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            return [], [f"找不到文件：{path}"]
    if raw is None:
        with io.open(path, "r", encoding="gb18030", errors="replace", newline="") as f:
            raw = f.read()
        warnings.append("编码无法完全识别，已用 gb18030 容错读取，请核对设备名称。")

    reader = csv.reader(io.StringIO(raw), delimiter=";")
    all_rows = [r for r in reader if r and any(str(c).strip() for c in r)]
    if not all_rows:
        return [], ["CSV 为空或无有效行。"]

    start = 1 if (len(all_rows[0]) >= MIN_COLS and not _looks_like_data(all_rows[0])) else 0
    out: List[PatchRow] = []
    for ln, r in enumerate(all_rows[start:], start + 1):
        if len(r) < MIN_COLS:
            warnings.append(f"第 {ln} 行列数不足({len(r)})，已跳过。")
            continue
        out.append(PatchRow(
            seq_id=_i(r[COL_ID]),
            type_name=(r[COL_NAME] or "").strip() or f"未命名_{ln}",
            universe=max(1, _i(r[COL_UNIVERSE], 1)),
            start_channel=max(1, _i(r[COL_START_CH], 1)),
            channel_count=_i(r[COL_CH_COUNT]),
            x_cm=_f(r[COL_X_CM]),
            z_cm=_f(r[COL_Z_CM]),
            x_rot_deg=_f(r[COL_XROT]),
            z_rot_deg=_f(r[COL_ZROT]),
            checked=str(r[COL_CHECKED]).strip().lower() in ("checked", "1", "true", "是", "yes"),
        ))
    return out, warnings


# ──────────────────────────────────────────────────────────────────────────────
#  分组
# ──────────────────────────────────────────────────────────────────────────────
def group_by_type(rows: List[PatchRow], patchable_only: bool = True) -> List[TypeGroup]:
    """按设备类型名分组；channel_count 取该组众数以规避个别脏行。保留首次出现顺序。"""
    buckets: Dict[str, TypeGroup] = {}
    for r in rows:
        if patchable_only and not r.is_patchable:
            continue
        g = buckets.get(r.type_name)
        if g is None:
            g = buckets[r.type_name] = TypeGroup(r.type_name, r.channel_count)
        g.rows.append(r)
    for g in buckets.values():
        counts = [rw.channel_count for rw in g.rows if rw.channel_count > 0]
        if counts:
            g.channel_count = Counter(counts).most_common(1)[0][0]
    return list(buckets.values())


def skipped_container_types(rows: List[PatchRow]) -> List[str]:
    """通道数为 0 的容器/分组节点类型名（去重、保序）。"""
    seen: List[str] = []
    for r in rows:
        if not r.is_patchable and r.type_name not in seen:
            seen.append(r.type_name)
    return seen


# ──────────────────────────────────────────────────────────────────────────────
#  设备类型「家族」识别 + 通道配置共享
# ──────────────────────────────────────────────────────────────────────────────
# 现实里设备多为非一体机：调压气爆配一个泵、跑泉也配一个泵，泵的控制通道完全一样。
# 导出软件会在名称上做小区分（气爆泵 / 跑泉泵）以便后续虚拟合并时区分。用户诉求：
# 「名称相似 + 通道数相同」的设备类型应共用同一套通道配接设置——填一次，相似的自动继承，
# 名称仍各自保留。本节提供纯函数判定「家族」并把已填配置传播给同族未填(全占位)的类型。
def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF) or (0xF900 <= o <= 0xFAFF)


def _common_suffix_len(a: str, b: str) -> int:
    n = 0
    i, j = len(a) - 1, len(b) - 1
    while i >= 0 and j >= 0 and a[i] == b[j]:
        n += 1; i -= 1; j -= 1
    return n


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def names_are_kin(a: str, b: str) -> bool:
    """两个设备类型名是否「相似」（同一类设备的命名变体）。

    判据（满足其一）：
      - 公共后缀：CJK 后缀 ≥1 字（如 气爆「泵」/ 跑泉「泵」），ASCII 后缀 ≥2 字符。
      - 公共前缀 ≥2 字符（如 "LED-A" / "LED-B"）。
    名称完全相同不算（那是同一类型，不需要传播）。空名/任一为空 → 否。
    """
    a = (a or "").strip(); b = (b or "").strip()
    if not a or not b or a == b:
        return False
    sfx = _common_suffix_len(a, b)
    if sfx >= 1 and _is_cjk(a[-1]):
        return True
    if sfx >= 2:
        return True
    if _common_prefix_len(a, b) >= 2:
        return True
    return False


def group_name_families(entries: List[Tuple[str, int]]) -> List[List[int]]:
    """把 (name, channel_count) 列表按「通道数相同 + 名称相似」并查集聚成家族。

    返回索引分组列表（每组 ≥1 个 entries 下标）。仅返回 size≥2 的家族 + 落单项各自成组，
    保持输入顺序稳定。通道数必须严格相等才可能同族（泵=泵的前提）。
    """
    n = len(entries)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(n):
        ni, ci = entries[i]
        for j in range(i + 1, n):
            nj, cj = entries[j]
            if ci == cj and names_are_kin(ni, nj):
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)
    # 按每组最小下标排序，保持稳定
    return [members for _root, members in sorted(groups.items(), key=lambda kv: min(kv[1]))]


def _default_is_placeholder(chs) -> bool:
    return bool(chs) and all(c == "placeholder" for c in chs)


def plan_family_channels(defs: List[object],
                         is_placeholder=_default_is_placeholder) -> List[Tuple[int, int]]:
    """规划同族通道传播，**不修改**任何定义。返回 [(target_idx, donor_idx), ...]。

    判据同 propagate_family_channels（同族 = 名称相似 + 通道数相同；捐赠者=同族唯一已填配置；
    目标=全占位且通道数匹配）。供调用方先向用户确认每对 source→target 再决定是否应用，
    避免「单字 CJK 后缀」误判（如 摇头灯/染色灯）静默污染不相关类型。
    """
    entries = [(str(getattr(d, "name", "")), int(getattr(d, "channel_count", 0) or 0)) for d in defs]
    families = group_name_families(entries)
    plan: List[Tuple[int, int]] = []
    for members in families:
        if len(members) < 2:
            continue
        donors = []
        for idx in members:
            chs = list(getattr(defs[idx], "channels", []) or [])
            if chs and not is_placeholder(chs):
                donors.append(idx)
        if not donors:
            continue
        donor_sigs = {tuple(getattr(defs[i], "channels", []) or []) for i in donors}
        if len(donor_sigs) != 1:
            continue  # 同族多套不同已填配置 → 歧义，跳过
        donor = defs[donors[0]]
        donor_chs = list(getattr(donor, "channels", []) or [])
        for idx in members:
            if idx in donors:
                continue
            tgt = defs[idx]
            if not is_placeholder(list(getattr(tgt, "channels", []) or [])):
                continue
            if int(getattr(tgt, "channel_count", 0) or 0) != len(donor_chs):
                continue
            plan.append((idx, donors[0]))
    return plan


def apply_family_plan(defs: List[object], plan: List[Tuple[int, int]]) -> List[Tuple[str, str]]:
    """按 plan 把 donor 的通道配置就地写入 target（仅 plan 中的对；调用方已确认）。

    返回 [(target_name, donor_name), ...]。
    """
    applied: List[Tuple[str, str]] = []
    for tgt_idx, donor_idx in plan or []:
        if not (0 <= tgt_idx < len(defs) and 0 <= donor_idx < len(defs)):
            continue
        tgt, donor = defs[tgt_idx], defs[donor_idx]
        donor_chs = list(getattr(donor, "channels", []) or [])
        if int(getattr(tgt, "channel_count", 0) or 0) != len(donor_chs):
            continue
        tgt.channels = list(donor_chs)
        if hasattr(tgt, "function_channel_names"):
            tgt.function_channel_names = dict(getattr(donor, "function_channel_names", {}) or {})
        if hasattr(tgt, "function_channel_defaults"):
            tgt.function_channel_defaults = dict(getattr(donor, "function_channel_defaults", {}) or {})
        applied.append((str(getattr(tgt, "name", "")), str(getattr(donor, "name", ""))))
    return applied


def propagate_family_channels(defs: List[object],
                              is_placeholder=_default_is_placeholder
                              ) -> List[Tuple[str, str]]:
    """在「设备类型定义」列表内，把同族已填配置传播给同族未填(全占位)定义。

    defs: 任意对象列表，需有属性 name / channel_count / channels；可选
          function_channel_names / function_channel_defaults。**就地修改** channels 等。
    is_placeholder: 判定某 channels 是否「未填」（默认=非空且全为 'placeholder'）。

    规则（保守、不破坏已填）：
      - 仅对「全占位」的目标定义写入；已自行填过的绝不覆盖。
      - 每个家族选一个「捐赠者」：同族中已填且 channels 配置唯一的那套；若家族内有
        多个互不相同的已填配置（歧义）则跳过该族不传播。
    返回 [(被填类型名, 捐赠来源类型名), ...] 供调用方提示。

    注意：这是「直接套用」版本（无确认）。UI 路径应改用 plan_family_channels + 用户确认 +
    apply_family_plan，避免单字 CJK 后缀误判静默污染。本函数保留供 headless/测试与兜底。
    """
    return apply_family_plan(defs, plan_family_channels(defs, is_placeholder=is_placeholder))


# ──────────────────────────────────────────────────────────────────────────────
#  坐标变换（纯函数）
# ──────────────────────────────────────────────────────────────────────────────
def csv_to_canvas(x_cm: float, z_cm: float,
                  scale: float = CANVAS_UNITS_PER_CM) -> Tuple[float, float]:
    """CSV (X cm, Z cm) → TopView 画布 (x, y)。Z 取负（CAD +Z 朝上，画布 y 向下）。"""
    return (x_cm * scale, z_cm * Y_AXIS_SIGN * scale)


def canvas_points_for_group(g: TypeGroup, scale: float = CANVAS_UNITS_PER_CM,
                            recenter: bool = True) -> List[Tuple[float, float]]:
    """该类型每行映射后的画布点；recenter=True 把形心平移到原点（各 Sheet 自身居中）。"""
    pts = [csv_to_canvas(r.x_cm, r.z_cm, scale) for r in g.rows]
    if recenter and pts:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        pts = [(x - cx, y - cy) for x, y in pts]
    return pts


# ──────────────────────────────────────────────────────────────────────────────
#  簇拆分（一个设备类型常由多条独立样条/环/弧组成）
# ──────────────────────────────────────────────────────────────────────────────
# 用户操作逻辑是「画一条样条→沿线顺序摆设备」。R4 导出按 DMX 地址(序号)排列，故同一条
# 样条上的设备在表中是连续的、空间相邻；切换到下一条样条时会出现远大于常态间距的跳变。
# 据此把一个类型按「相邻设备空间跳变」切成多个簇，每簇单独拟合形状。
CLUSTER_GAP_FACTOR = 3.0     # 间距超过中位数的该倍数视为簇边界
CLUSTER_MIN_ABS_GAP = 50.0   # 绝对最小跳变阈值（画布单位），避免微小间距误切


def split_group_into_clusters(g: TypeGroup,
                              scale: float = CANVAS_UNITS_PER_CM,
                              gap_factor: float = CLUSTER_GAP_FACTOR,
                              min_abs_gap: float = CLUSTER_MIN_ABS_GAP) -> List[List[PatchRow]]:
    """把一个类型的设备行（按序号排序）按空间跳变切成多个簇，返回 [[PatchRow,...], ...]。

    若整类被判为规则网格（matrix），则**不切**，整体作为一个簇返回（矩阵不拟合曲线）。
    """
    rows = sorted(g.rows, key=lambda r: r.seq_id)
    if len(rows) <= 2:
        return [list(rows)] if rows else []

    pts_all = [csv_to_canvas(r.x_cm, r.z_cm, scale) for r in rows]

    # 整类是规则网格 → 不切（避免把按行摆放的网格切成多条线）
    try:
        _fit_circle, _angular_span_deg, _pca_axis, _is_grid_like = _geom()
        if len(pts_all) >= 4 and _is_grid_like(pts_all):
            return [list(rows)]
    except Exception:
        pass

    gaps = [math.hypot(pts_all[i + 1][0] - pts_all[i][0], pts_all[i + 1][1] - pts_all[i][1])
            for i in range(len(pts_all) - 1)]
    # 用「非零间距」中位数：同坐标叠放的副本设备(gap≈0)会把普通 median 拉到 0，
    # 导致阈值塌到下限、把正常的样条内间距误判成簇边界。剔除 gap≈0 后才是真实样条步距。
    nonzero = [d for d in gaps if d > 1.0]
    med = statistics.median(nonzero) if nonzero else 0.0
    threshold = max(min_abs_gap, med * gap_factor) if med > 0 else min_abs_gap

    clusters: List[List[PatchRow]] = []
    cur: List[PatchRow] = [rows[0]]
    for i in range(len(rows) - 1):
        if gaps[i] > threshold:
            clusters.append(cur)
            cur = []
        cur.append(rows[i + 1])
    if cur:
        clusters.append(cur)
    return clusters


def cluster_canvas_points(cluster: List[PatchRow],
                          scale: float = CANVAS_UNITS_PER_CM) -> List[Tuple[float, float]]:
    """簇内每行映射后的画布点（不 recenter，保留与同类型其他簇的相对位置）。"""
    return [csv_to_canvas(r.x_cm, r.z_cm, scale) for r in cluster]


# ──────────────────────────────────────────────────────────────────────────────
#  微单元检测（N 个灯围绕一中心 → 一个单元；单元中心再沿大圆/弧排布）
# ──────────────────────────────────────────────────────────────────────────────
# 用户场景：现实里常见「4 个灯围着一个中心成一组（单元），这些单元又沿一条大圆/弧排
# 布」。旧逻辑会把单元内 4 灯拟合成小圆圈；用户要求**单元内按矩阵理解（不拟合曲线）**，
# 只让单元中心序列去拟合大圆/弧。这里用「两级间距」：组内最近邻间距 ≪ 组间间距时，
# 把紧致的 N 联体识别成一个微单元，折叠成中心点喂给宏拟合。
UNIT_MAX_MEMBERS = 8          # 单元成员数上限（3/4/6… 常见，留余量到 8）
UNIT_LINK_FACTOR = 2.0        # 单链聚类边阈值 = 全局最近邻间距中位数 × 该因子
UNIT_MIN_UNITS = 2            # 至少检出这么多单元才认为是「单元化」布局，否则不折叠
UNIT_MIN_MEMBERS = 3          # 单元最小成员数：N 灯「围一中心」至少 3 盏；两两一对不算单元


@dataclass
class UnitSpec:
    """一个微单元：成员（按 seq_id）+ 中心 + 成员相对中心的局部偏移（画布坐标）。"""
    member_seq_ids: List[int]
    center: Tuple[float, float]
    member_offsets: Dict[int, Tuple[float, float]] = field(default_factory=dict)


def _nn_dists_exact(tree, arr, xs: List[float], zs: List[float]) -> List[float]:
    """每点最近邻距离：cKDTree 前 3 候选 + math.hypot 精确复算取最小。

    树内 sqrt 与 math.hypot 可差 1 ulp，复算后与朴素逐对 math.hypot 位级
    一致（等距并列点由 hypot 参数对称性给出同一位模式）。要求 n >= 2。
    """
    n = arr.shape[0]
    k = min(3, n)
    _, ii = tree.query(arr, k=k)
    if k == 1:
        ii = ii.reshape(n, 1)
    out = [0.0] * n
    for i in range(n):
        best = math.inf
        for c in range(k):
            j = int(ii[i, c])
            if j == i:
                continue
            d = math.hypot(xs[i] - xs[j], zs[i] - zs[j])
            if d < best:
                best = d
        out[i] = best
    return out


def _nn_distances(pts: List[Tuple[float, float]]) -> List[float]:
    """每点到其最近邻的距离。

    ★ 性能（R183 3.3 计算层批）：原朴素 O(n^2) 双循环 → scipy cKDTree
    （见 _nn_dists_exact 的位级一致口径）。scipy 懒导入：本模块保持纯逻辑
    零重依赖。
    """
    if len(pts) < 2:
        return []
    import numpy as np
    from scipy.spatial import cKDTree
    xs = [float(p[0]) for p in pts]
    zs = [float(p[1]) for p in pts]
    arr = np.column_stack([np.asarray(xs), np.asarray(zs)])
    return _nn_dists_exact(cKDTree(arr), arr, xs, zs)


def _threshold_edges_sorted(pts: List[Tuple[float, float]],
                            edge_thr: float) -> List[Tuple[int, int]]:
    """阈值内的点对边，按 (i, j) 升序 = 朴素 O(N²) 双循环枚举序。

    ★ 性能（R183 3.3 计算层批）：双循环逐对 math.hypot → cKDTree 候选对
    （半径加 1e-9 相对余量兜 1 ulp 差）+ math.hypot 按原阈值精确复筛 →
    边集位级一致、枚举序一致 ⇒ 并查集 union 序一致 ⇒ 根与连通分量插入序
    一致（调用方输出顺序不变）。scipy 懒导入。
    """
    import numpy as np
    from scipy.spatial import cKDTree
    cand = cKDTree(np.asarray(pts, dtype=np.float64)).query_pairs(
        edge_thr * (1.0 + 1e-9) + 1e-9, output_type="ndarray")
    edges: List[Tuple[int, int]] = []
    for a, b in cand:
        ai, bi = int(a), int(b)
        if ai > bi:
            ai, bi = bi, ai
        if math.hypot(pts[ai][0] - pts[bi][0],
                      pts[ai][1] - pts[bi][1]) <= edge_thr:
            edges.append((ai, bi))
    edges.sort()
    return edges


def detect_micro_units(rows: List[PatchRow],
                       scale: float = CANVAS_UNITS_PER_CM
                       ) -> Tuple[List[UnitSpec], List[int]]:
    """在一个设备类型内检测微单元。

    返回 (units, free_seq_ids)：
      - units: 检出的微单元列表（每个含成员 seq_id、中心、成员局部偏移）。
      - free_seq_ids: 未被归入任何单元的散台 seq_id。

    判据：用单链聚类，边阈值 = 全局最近邻间距中位数 × UNIT_LINK_FACTOR。单元内成员间距
    ≈最近邻中位数 → 互相连边成一个连通分量；单元之间的间距远大于该阈值 → 不连。连成的
    连通分量若大小在 [UNIT_MIN_MEMBERS, UNIT_MAX_MEMBERS] 即为一个候选单元（N 灯围一中心，
    至少 3 盏；两两一对不算单元。过大=均匀样条/网格，整体连成一团，会被尺寸上限剔除）；
    需检出 ≥ UNIT_MIN_UNITS 个单元才返回，否则视为无单元化。
    """
    # 同 seq_id 去重（R4 重复行）：每个 seq_id 取首次坐标。
    seen: Dict[int, Tuple[float, float]] = {}
    order: List[int] = []
    for r in rows:
        if r.seq_id not in seen:
            seen[r.seq_id] = csv_to_canvas(r.x_cm, r.z_cm, scale)
            order.append(r.seq_id)
    if len(order) < UNIT_MIN_UNITS * 2:
        return [], list(order)

    pts = [seen[s] for s in order]
    nn = sorted(d for d in _nn_distances(pts) if d > 1e-6)
    if not nn:
        return [], list(order)
    # 用「较紧的一半」最近邻间距的中位数作基准，而非全体中位数：当布局里混有几个孤立
    # 散台时，它们的大最近邻间距会把全体中位数抬高，阈值随之过大→把相距数百单位的散台
    # 误连成假单元。低半中位数反映真正的「组内」紧致间距，孤立点不再污染阈值。
    k = max(1, len(nn) // 2)
    base = statistics.median(nn[:k])
    if base <= 1e-6:
        return [], list(order)
    edge_thr = base * UNIT_LINK_FACTOR
    if edge_thr <= 1e-6:
        return [], list(order)

    # 单链并查集：阈值内的点连边（_threshold_edges_sorted：位级=旧双循环）。
    parent = list(range(len(order)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in _threshold_edges_sorted(pts, edge_thr):
        union(a, b)

    comps: Dict[int, List[int]] = {}
    for idx in range(len(order)):
        comps.setdefault(find(idx), []).append(idx)

    units: List[UnitSpec] = []
    free: List[int] = []
    # 直径上限：紧致单元内部点距应在 edge_thr 量级。单链可能跨稀疏间隙把两个本应独立的
    # 簇连成一团；用最大两两距离封顶，过松的连通分量退回散台，防止"桥接"误并。
    diam_cap = edge_thr * UNIT_MAX_MEMBERS
    for members in comps.values():
        mpts = [pts[m] for m in members]
        if UNIT_MIN_MEMBERS <= len(members) <= UNIT_MAX_MEMBERS:
            diam = 0.0
            for a in range(len(mpts)):
                for b in range(a + 1, len(mpts)):
                    d = math.hypot(mpts[a][0] - mpts[b][0], mpts[a][1] - mpts[b][1])
                    if d > diam:
                        diam = d
            if diam > diam_cap:
                free.extend(order[m] for m in members)
                continue
            cx = sum(p[0] for p in mpts) / len(mpts)
            cy = sum(p[1] for p in mpts) / len(mpts)
            seqs = [order[m] for m in members]
            offs = {order[m]: (pts[m][0] - cx, pts[m][1] - cy) for m in members}
            units.append(UnitSpec(member_seq_ids=seqs, center=(cx, cy), member_offsets=offs))
        else:
            free.extend(order[m] for m in members)

    if len(units) < UNIT_MIN_UNITS:
        return [], list(order)
    return units, free



# ──────────────────────────────────────────────────────────────────────────────
#  形状分类 / 拟合（懒导入 TopView 几何算法，保证行为一致）
# ──────────────────────────────────────────────────────────────────────────────
def _geom():
    """懒导入 topview_panel 的几何算法。仅形状拟合时触发（带入 Qt 依赖）。"""
    from .topview_panel import _fit_circle, _angular_span_deg, _pca_axis, _is_grid_like
    return _fit_circle, _angular_span_deg, _pca_axis, _is_grid_like


def classify_layout(points: List[Tuple[float, float]]) -> str:
    """返回 'matrix' | 'circle' | 'arc' | 'line' | 'none'。

    阈值与 topview_panel._compute_xy_reference_angles 完全对齐，确保拟合出的
    曲线与 TopView 现场反算的朝向一致。
    """
    n = len(points)
    if n < 2:
        return "none"
    _fit_circle, _angular_span_deg, _pca_axis, _is_grid_like = _geom()

    if n >= 4 and _is_grid_like(points):
        return "matrix"

    if n >= 3:
        fit = _fit_circle(points)
        if fit is not None:
            cx, cy, radius, rms = fit
            min_r = min((math.hypot(x - cx, y - cy) for x, y in points), default=0.0)
            span = _angular_span_deg(points, cx, cy)
            if span >= 25.0 and min_r >= max(5.0, radius * 0.35) and rms <= max(6.0, radius * 0.08):
                return "circle" if ring_is_closed(points, (cx, cy)) else "arc"

    _vx, _vy, minor_major = _pca_axis(points)
    if minor_major <= 0.035:
        # ★ 近直线需复核「点到直线偏差」：弯折弧线（样条形，非单圆）PCA 也呈
        #   近直线，但点离线远（底弧线拱高≈1000 vs 跨度≈10000）——误判 line 会
        #   画出与设备完全不符的水平直线（2026-08-03 用户指正）。偏差 > 跨度 5%
        #   即视为弯折曲线 → spline（过点平滑曲线）。
        cx = sum(p[0] for p in points) / n
        cy = sum(p[1] for p in points) / n
        max_dev = 0.0
        tmin, tmax = float("inf"), float("-inf")
        for x, y in points:
            dev = abs((x - cx) * _vy - (y - cy) * _vx)
            max_dev = max(max_dev, dev)
            t = (x - cx) * _vx + (y - cy) * _vy
            tmin = min(tmin, t)
            tmax = max(tmax, t)
        span = max(1e-9, tmax - tmin)
        if max_dev > span * 0.05:
            return "spline"
        return "line"
    # ★ 弯折曲线判定（不依赖 ratio，2026-08-03 用户指正）：circle 拟合失败但
    #   点到 PCA 直线偏差大的弧线（如副池弧线，弯折大 → ratio>0.035 落入此分支）
    #   不应判 matrix——否则弧线页不产出曲线。
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    max_dev = 0.0
    tmin, tmax = float("inf"), float("-inf")
    for x, y in points:
        max_dev = max(max_dev, abs((x - cx) * _vy - (y - cy) * _vx))
        t = (x - cx) * _vx + (y - cy) * _vy
        tmin = min(tmin, t)
        tmax = max(tmax, t)
    span = max(1e-9, tmax - tmin)
    if max_dev > span * 0.05:
        return "spline"
    # ★ 非网格散点一律过点拟合（2026-08-03 用户指正）：上弧线/落叶头等沿弧线或
    #   闭合图形布局（宽带状、非规则网格）不应判 matrix 不拟合——只有开头已拦的
    #   规则网格（百变）才算 matrix；其余按 spline 过每个设备点拟合。
    return "spline" if n >= 3 else "line"


def _bspline_sample(points: List[Tuple[float, float]], per_seg: int = 12) -> List[Tuple[float, float]]:
    """均匀三次 B 样条采样（2026-08-03 用户：弧线几乎都是乱折的）。

    TSpline3D 控制点连线本身锯齿（底弧线相邻转角 143°/136°/106°），
    渲染流畅 = 样条**不过控制点**（B 样条曲线在控制点附近平滑通过）；
    过点插值（Catmull-Rom）会把锯齿原样画出 = 乱折。B 样条输出平滑曲线。
    """
    pts = [(float(x), float(y)) for x, y in points]
    n = len(pts)
    if n < 4:
        return list(pts)
    out: List[Tuple[float, float]] = []
    for i in range(n - 3):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        for k in range(per_seg):
            t = k / per_seg
            t2, t3 = t * t, t * t * t
            x = ((1 - t) ** 3 * p0[0] + (3 * t3 - 6 * t2 + 4) * p1[0]
                 + (-3 * t3 + 3 * t2 + 3 * t + 1) * p2[0] + t3 * p3[0]) / 6.0
            y = ((1 - t) ** 3 * p0[1] + (3 * t3 - 6 * t2 + 4) * p1[1]
                 + (-3 * t3 + 3 * t2 + 3 * t + 1) * p2[1] + t3 * p3[1]) / 6.0
            out.append((x, y))
    out.append(pts[-1])
    return out


def _catmull_rom_sample(points: List[Tuple[float, float]], per_seg: int = 16,
                        closed: bool = False) -> List[Tuple[float, float]]:
    """过控制点的平滑曲线采样——**向心 Catmull-Rom（centripetal）**。

    2026-08-03 用户反馈：均匀 Catmull-Rom 在弯折大处过冲（上弧线顶部弧线
    绕一圈再经过下一设备）；向心参数化（t 按弦长开方，k=0.5）消除过冲/自交，
    曲线自然衔接。曲线严格经过每个控制点，段间切向连续。
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return list(pts)
    n = len(pts)
    if closed and n >= 3:
        ring = [pts[n - 1]] + pts + [pts[0], pts[1]]
        out: List[Tuple[float, float]] = []
        for i in range(1, n + 1):
            p0, p1, p2, p3 = ring[i - 1], ring[i], ring[i + 1], ring[i + 2]
            out.extend(_centripetal_segment(p0, p1, p2, p3, per_seg))
        out.append(pts[0])
        return out
    out = [pts[0]]
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        out.extend(_centripetal_segment(p0, p1, p2, p3, per_seg, include_first=False))
    return out


def _centripetal_segment(p0, p1, p2, p3, per_seg: int, include_first: bool = True) -> List[Tuple[float, float]]:
    """向心 Catmull-Rom 单段采样（t = 弦长^0.5，无过冲/自交）。"""
    def d(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])
    t0 = 0.0
    t1 = t0 + math.sqrt(d(p0, p1))
    t2 = t1 + math.sqrt(d(p1, p2))
    t3 = t2 + math.sqrt(d(p2, p3))
    if t2 - t1 <= 1e-12:
        return [p1]
    # 安全除法：控制点重复（t 参数退化）时返回 0，避免除零（2026-08-03 实测）
    def sd(num: float, den: float) -> float:
        return num / den if abs(den) > 1e-12 else 0.0
    out: List[Tuple[float, float]] = []
    start = 0 if include_first else 1
    for k in range(start, per_seg + 1):
        t = t1 + (t2 - t1) * k / per_seg
        a1x = sd(t1 - t, t1 - t0) * p0[0] + sd(t - t0, t1 - t0) * p1[0]
        a1y = sd(t1 - t, t1 - t0) * p0[1] + sd(t - t0, t1 - t0) * p1[1]
        a2x = sd(t2 - t, t2 - t1) * p1[0] + sd(t - t1, t2 - t1) * p2[0]
        a2y = sd(t2 - t, t2 - t1) * p1[1] + sd(t - t1, t2 - t1) * p2[1]
        a3x = sd(t3 - t, t3 - t2) * p2[0] + sd(t - t2, t3 - t2) * p3[0]
        a3y = sd(t3 - t, t3 - t2) * p2[1] + sd(t - t2, t3 - t2) * p3[1]
        b1x = sd(t2 - t, t2 - t0) * a1x + sd(t - t0, t2 - t0) * a2x
        b1y = sd(t2 - t, t2 - t0) * a1y + sd(t - t0, t2 - t0) * a2y
        b2x = sd(t3 - t, t3 - t1) * a2x + sd(t - t1, t3 - t1) * a3x
        b2y = sd(t3 - t, t3 - t1) * a2y + sd(t - t1, t3 - t1) * a3y
        px = sd(t2 - t, t2 - t1) * b1x + sd(t - t1, t2 - t1) * b2x
        py = sd(t2 - t, t2 - t1) * b1y + sd(t - t1, t2 - t1) * b2y
        out.append((px, py))
    return out


def build_fit_shape(points: List[Tuple[float, float]]) -> Optional[dict]:
    """按分类构造 TopView flat-dict CAD shape。matrix/none → None（不加拟合曲线）。

    shape id 用几何坐标确定性 hash（非随机 uuid），使同一 CSV 重导入产生相同 id，
    TopView import_layout_pages 的按-id 去重才能生效、避免重复叠加图形。

    shape 键与 topview_panel._shape_points 读取的 schema 一致：
      line:   {type,x1,y1,x2,y2}
      circle: {type,cx,cy,rx,ry,angle}           (整圆，默认 360°)
      arc:    {type,cx,cy,rx,ry,angle,start_angle,span_angle}  (部分圆)
    """
    kind = classify_layout(points)
    if kind in ("matrix", "none"):
        return None

    # ★ 星形优先于 circle（2026-08-03 用户指正：副池两个五角星顶点共圆，
    #   circle fit rms≈0 但实际是星形，必须隔点连接成星）。
    star = _detect_star(points)
    if star is not None:
        return {
            "id": _shape_id("polyline", star[0][0], star[0][1], star[-1][0], star[-1][1]),
            "type": "polyline", "points": star,
        }

    _fit_circle, _angular_span_deg, _pca_axis, _is_grid_like = _geom()

    if kind == "line":
        vx, vy, _ = _pca_axis(points)
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        ts = [(x - cx) * vx + (y - cy) * vy for x, y in points]
        tmin, tmax = min(ts), max(ts)
        if (tmax - tmin) <= 1e-6:
            return None  # 退化：所有点重合（如 3 个叠放副本），不产出零长线伪影
        x1, y1 = cx + tmin * vx, cy + tmin * vy
        x2, y2 = cx + tmax * vx, cy + tmax * vy
        return {
            "id": _shape_id("line", x1, y1, x2, y2), "type": "line",
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        }

    if kind in ("spline", "none"):
        # ★ 平滑曲线（2026-08-03 用户指正：折线不流畅应退回平滑方案）——
        #   向心 Catmull-Rom 过设备点（不过冲/不自交，均匀版会扭），
        #   闭合图形极角环绕平滑闭合，开放主轴排序平滑。
        star = _detect_star(points)
        if star is not None:
            return {
                "id": _shape_id("polyline", star[0][0], star[0][1], star[-1][0], star[-1][1]),
                "type": "polyline", "points": star,
            }
        _vx, _vy, _ = _pca_axis(points)
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        # ★ 2026-09-17：闭合判定走共享真源 ring_is_closed（原为本地绝对阈值
        #   60°，n=5/6 环判不开；与 geometry_splines 那份实现重复且同样有缺陷）
        closed = ring_is_closed(points, (cx, cy))
        if closed:
            ordered = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        else:
            ordered = sorted(points, key=lambda p: (p[0] - cx) * _vx + (p[1] - cy) * _vy)
        if len(ordered) < 2:
            return None
        smooth = _catmull_rom_sample(ordered, per_seg=8, closed=closed)
        if len(smooth) < 4:
            return None
        return {
            "id": _shape_id("spline", smooth[0][0], smooth[0][1], smooth[-1][0], smooth[-1][1]),
            "type": "spline", "points": smooth,
        }

    fit = _fit_circle(points)
    if fit is None:
        return None
    cx, cy, radius, _rms = fit
    if kind == "circle":
        return {"id": _shape_id("circle", cx, cy, radius), "type": "circle",
                "cx": cx, "cy": cy, "rx": radius, "ry": radius, "angle": 0.0}
    # arc：start_angle 必须落在「最大间隙之后」的那台设备处，与 _angular_span_deg
    # 定位的间隙一致；否则当开口朝 +x（点跨 ±180°）时，渲染会从 angs[0]≈-180 顺时针
    # 扫过真实缺口、错过实际设备。环绕间隙的常态下退化为 angs[0]（保旧行为）。
    start, span = _arc_start_span(points, cx, cy)
    return {"id": _shape_id("arc", cx, cy, radius, start, span), "type": "circle",
            "cx": cx, "cy": cy, "rx": radius, "ry": radius, "angle": 0.0,
            "start_angle": start, "span_angle": span}


def _arc_start_span(points, cx, cy):
    """Occupied circular interval starts immediately after the largest empty gap."""
    angles = sorted(math.degrees(math.atan2(y - cy, x - cx)) for x, y in points)
    if len(angles) < 2:
        return (angles[0] if angles else 0.0), 0.0
    gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % 360.0
            for i in range(len(angles))]
    index = max(range(len(gaps)), key=gaps.__getitem__)
    return angles[(index + 1) % len(angles)], 360.0 - gaps[index]


def _shape_id(kind: str, *coords: float) -> str:
    """由形状类型 + 量化坐标生成确定性 id（同几何 → 同 id），用于重导入去重。"""
    key = kind + "|" + "|".join(f"{round(float(c), 2):.2f}" for c in coords)
    return "imp-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def cluster_points_by_gap(pts: List[Tuple[float, float]],
                          gap_factor: float = 2.2,
                          min_abs_gap: float = 40.0) -> List[List[Tuple[float, float]]]:
    """按**输入(序号)顺序**的空间跳变把点序列切成多个簇（供合并后代表点重新拟合）。

    与 split_group_into_clusters 同思路：设备/代表点按配接序号顺序排列，沿序相邻点间距
    远大于中位步距处即为簇边界。**不能用无序的空间最近邻聚类**——多环布局里环与环靠近，
    最近邻聚类会把所有环连成一团(实测 222 锚→1 簇→0 图线)；按序跳变切则能分出每个环。
    """
    n = len(pts)
    if n <= 2:
        return [list(pts)] if pts else []
    gaps = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            for i in range(n - 1)]
    nonzero = [d for d in gaps if d > 1.0]
    med = statistics.median(nonzero) if nonzero else 0.0
    thr = max(min_abs_gap, med * gap_factor) if med > 0 else min_abs_gap
    clusters: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = [pts[0]]
    for i in range(n - 1):
        if gaps[i] > thr:
            clusters.append(cur); cur = []
        cur.append(pts[i + 1])
    if cur:
        clusters.append(cur)
    return clusters


def _spatial_cluster(pts: List[Tuple[float, float]], factor: float = 5.0) -> List[List[Tuple[float, float]]]:
    """空间连通聚类：相邻距离 ≤ 间距 p90×factor 的点连成簇。

    2026-08-03 用户指正：副池是两个独立五角星（星间 705 → 分开）；
    落叶头/底弧线设备按单元组排列但整体沿曲线（组间间隙 ≤ p90×5 → 连成一条）。
    实测 factor=5：落叶头 1 簇、副池 2 簇。

    ★ 性能（R183 3.3 计算层批）：上版已把两段 O(N²) 逐对 hypot 收敛为一次
    N×N 距离矩阵（仍 O(N²) 内存/时间，大页 ~90ms）→ 本版改 scipy cKDTree：
    最近邻 = 树前 3 候选 + math.hypot 精确复算（树内 sqrt 与 hypot 可差
    1 ulp，复算消除）；阈值内边 = query_pairs 加 1e-9 相对余量取候选再按
    **原阈值 + math.hypot** 精确复筛 → 边集与旧矩阵读位级一致。连通分量
    遍历与旧实现逐语句同构（set.pop 起点、frontier LIFO、list(unassigned)
    快照迭代序），仅把 row[j] <= threshold 换邻接集命中——簇成员序与簇序
    全部不变（下游 shape id `circle-%d` / `dp-spline-%s-circle-%d` 依赖簇序，
    见 geometry_splines）。scipy 懒导入，纯逻辑模块不添重依赖。
    """
    n = len(pts)
    if n < 4:
        return [list(pts)]
    import numpy as np
    from scipy.spatial import cKDTree
    xs = [float(p[0]) for p in pts]
    zs = [float(p[1]) for p in pts]
    arr = np.column_stack([np.asarray(xs), np.asarray(zs)])
    tree = cKDTree(arr)
    # 最近邻间距（旧矩阵语义 = 行内第二小值，对角 0 恒最小；重合点亦成立）
    dists = _nn_dists_exact(tree, arr, xs, zs)
    dists.sort()
    p90 = dists[min(n - 1, int(n * 0.9))]
    threshold = max(1e-6, p90 * factor)
    # 候选边：半径加极小余量兜住 1 ulp 差，候选按原阈值 + math.hypot 复筛。
    pairs = tree.query_pairs(threshold * (1.0 + 1e-9) + 1e-9,
                             output_type="ndarray")
    adj: List[set] = [set() for _ in range(n)]
    for a, b in pairs:
        ai, bi = int(a), int(b)
        if math.hypot(xs[ai] - xs[bi], zs[ai] - zs[bi]) <= threshold:
            adj[ai].add(bi)
            adj[bi].add(ai)
    clusters: List[List[int]] = []
    unassigned = set(range(n))
    while unassigned:
        start = unassigned.pop()
        cluster = [start]
        frontier = [start]
        while frontier:
            i = frontier.pop()
            for j in list(unassigned):
                if j in adj[i]:
                    unassigned.discard(j)
                    cluster.append(j)
                    frontier.append(j)
        clusters.append(cluster)
    return [[pts[i] for i in cl] for cl in clusters]


def _detect_star(points: List[Tuple[float, float]]) -> Optional[List[Tuple[float, float]]]:
    """五角星等星形检测：5-12 点近似共圆 + 等角分布 → 返回隔点连接的星形折线点。

    2026-08-03 用户指正：副池两个五角星顶点共圆（circle fit rms≈0），但实际是
    星形不是圆——必须按隔点序连接（五角星 0-2-4-1-3）。
    """
    n = len(points)
    if not (5 <= n <= 12):
        return None
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in points]
    r_mean = sum(rs) / n
    if r_mean <= 1e-6:
        return None
    if (max(rs) - min(rs)) > r_mean * 0.12:
        return None  # 半径不均匀 → 非共圆
    angs = sorted((math.atan2(p[1] - cy, p[0] - cx), i) for i, p in enumerate(points))
    gaps = [angs[k + 1][0] - angs[k][0] for k in range(n - 1)]
    gaps.append(angs[0][0] + math.tau - angs[-1][0])
    g_mean = math.tau / n
    if any(abs(g - g_mean) > g_mean * 0.15 for g in gaps):
        return None  # 非等角分布
    order = [angs[k][1] for k in range(0, n, 2)] + [angs[k][1] for k in range(1, n, 2)]
    return [points[i] for i in order] + [points[order[0]]]


# ──────────────────────────────────────────────────────────────────────────────
#  同心多圆检测（2026-09-17）
# ──────────────────────────────────────────────────────────────────────────────
# 用户定调（水仙十字）：布局基本是「大小同心圆 + 一条弧线」，百变是「三条弧 +
# 三个同心圆」。设备自己的坐标就构成这些几何，**圆心必须来自设备点本身**，
# 不能依赖 里是否画了 Circle Spline（用户画了就直接采用，没画就得自拟合）。
#
# 关键事实：同心圆的圆心与「那条弧」的圆心**不同**（水仙十字实测：两圆同心于
# 喷泉中心，弧在另一圆心）。因此不能对整个点簇用一个圆心——正确做法是把簇内
# 「同圆心的多圈」识别出来单独成圆，**剩余点回退原拟合**（那条弧）。
#
# 圆心求解：同心环的公共圆心无法用「全簇最小二乘」稳定得到（弧点会把圆心拉偏），
# 故用确定性网格搜索最小化「圆层内半径方差」，分 3 轮逐步收敛。纯确定性（无随机），
# 保证同一工程重复导入输出逐位一致（shape id 依赖坐标 hash）。
_RING_MIN_MEMBERS = 4          # 一个环至少这么多台才当圆（少于 4 台不判圆）
_RING_CIRCLE_SPAN = 300.0      # 张角 ≥ 此值为整圆
_RING_CIRCLE_RMS_REL = 0.05    # 环内 rms ≤ 半径×该值 才算共圆
_RING_SEARCH_MAX_PTS = 1500    # 超过此点数的簇不做网格搜索（大面阵交给 matrix 判据）
_RING_SEARCH_GRID = 25         # 网格边长


def _kasa_circle(pts: List[Tuple[float, float]]):
    """Kasa 代数最小二乘圆拟合 → (cx, cy, r, rms)，退化返回 None。"""
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    u = [p[0] - mx for p in pts]
    v = [p[1] - my for p in pts]
    suu = sum(a * a for a in u)
    svv = sum(b * b for b in v)
    suv = sum(a * b for a, b in zip(u, v))
    suuu = sum(a ** 3 for a in u)
    svvv = sum(b ** 3 for b in v)
    suvv = sum(a * b * b for a, b in zip(u, v))
    suuv = sum(a * a * b for a, b in zip(u, v))
    det = suu * svv - suv * suv
    if abs(det) < 1e-9:
        return None
    c1 = (suuu + suvv) / 2.0
    c2 = (svvv + suuv) / 2.0
    uc = (c1 * svv - c2 * suv) / det
    vc = (c2 * suu - c1 * suv) / det
    cx, cy = uc + mx, vc + my
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    r = sum(rs) / n
    rms = math.sqrt(sum((x - r) ** 2 for x in rs) / n)
    return cx, cy, r, rms


def ring_is_closed(pts, center) -> bool:
    """点集绕 `center` 是否构成**闭合环**（整圆）——单一真源。

    ★★ 2026-09-17 统一规则（替代两处重复的绝对阈值实现）：
      旧实现（此处 + geometry_splines._strict_ring_geometry 各一份）判据为
      `max_gap <= 60°`。两个真实缺陷：
      ① **n=5/6 无法判定**：均匀 6 台环的最大角隙 = 60.000000000000004°
         （1 ULP 之差，实测 1.0471975511965985 vs π/3 = 1.0471975511965976，
         超 8.88e-16）→ 判为"开口"；均匀 5 台环角隙恒 72° → **永不闭合**。
      ② **绝对阈值不随设备数缩放**：设备越多角隙越小，少台数环越容易被误判。
      新判据 = **相对角隙**：`max_gap <= max(QUANT, ratio × median_gap)`，
      其中 median_gap 是各角隙的中位数（均匀环 ≈ 360/n）：
        - 均匀 n=5..24 环 → 中位=360/n、max≈中位 → 全部判闭合 ✓
        - 真开口弧（实测 189°/157°/113°/60°/30°…）→ max 远大于中位 → 开口 ✓
        - 缺 1 台的 12 环（缺 60°，中位 30°，比 2.0）→ 仍判闭合 ✓（有意：
          少一台的画圆仍是圆，不该掉成开口曲线）
        - 只有 2 个角隙（n=2/3）时中位数无意义 → 由 QUANT 兜底。
    `ratio=2.0` 与 `QUANT=30°`（弧度）为唯一参数，两处调用方共用。
    """
    n = len(pts)
    if n < 3:
        return False
    cx, cy = center
    angs = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in pts)
    gaps = [angs[i + 1] - angs[i] for i in range(n - 1)]
    gaps.append(angs[0] + math.tau - angs[-1])
    max_gap = max(gaps)
    if len(gaps) >= 4:
        med = statistics.median(gaps)
        return max_gap <= max(math.radians(30.0), med * 2.0)
    return max_gap <= math.radians(30.0)


def _angular_span_at(pts: List[Tuple[float, float]], c) -> float:
    """点集绕 c 的张角（最大角隙的补角）。"""
    aa = sorted(math.degrees(math.atan2(p[1] - c[1], p[0] - c[0])) % 360.0
                for p in pts)
    if len(aa) < 2:
        return 0.0
    gaps = [aa[k + 1] - aa[k] for k in range(len(aa) - 1)]
    gaps.append(aa[0] + 360.0 - aa[-1])
    return 360.0 - max(gaps)


def _ring_band(maxr: float) -> float:
    """半径分层带宽 = 半径量程的一个小比例。

    ★ **不能用「相邻点半径差的中位数」**：同心环的环内点半径几乎相等（差值≈0
    被剔除），剩下全是环间距 → 中位数≈环间距 → 带宽被撑到能吞掉多圈
    （实测 3 圈并成 1 层）。环内抖动实测 ≤4%R，故取 5% 量程留余量，再由
    `_merge_close_rings` 兜住「一圈被抖成两圈」。
    """
    return max(50.0, 0.05 * maxr)


def _radial_layers(pts, c, band):
    """按「到圆心 c 的半径」把点分层（相邻半径差 > band 即断带）。"""
    rs = sorted(((math.hypot(p[0] - c[0], p[1] - c[1]), p) for p in pts),
                key=lambda t: t[0])
    layers = [[rs[0]]]
    for k in range(1, len(rs)):
        if rs[k][0] - layers[-1][-1][0] > band:
            layers.append([])
        layers[-1].append(rs[k])
    return layers


def _layer_circle(layer, c, tol_rel):
    """一个半径层若构成整圆则返回 (cx, cy, r, bp, rms)，否则 None。"""
    if len(layer) < _RING_MIN_MEMBERS:
        return None
    bp = [p for _, p in layer]
    if _angular_span_at(bp, c) < _RING_CIRCLE_SPAN:
        return None
    f = _kasa_circle(bp)
    if f is None:
        return None
    cx, cy, r, rms = f
    if r <= 1e-6 or rms > max(3.0, r * tol_rel):
        return None
    return cx, cy, r, bp, rms


def _eval_ring_center(pts: List[Tuple[float, float]], c, tol_rel=None):
    """给定圆心 c，按半径分层并挑出「整圆层」。

    返回 (cost, circles) 或 None（整圆 < 2 圈即 None——只对多圈场景接管）。
    cost = 各圆层**归一化** rms 均值（rms/R，量纲无关，便于跨半径比较）。
    tol_rel：圆层 rms 容差（相对半径）。搜索期放松、终检收紧（见调用方）。
    """
    if tol_rel is None:
        tol_rel = _RING_CIRCLE_RMS_REL
    maxr = max(math.hypot(p[0] - c[0], p[1] - c[1]) for p in pts)
    if maxr <= 1e-6:
        return None
    band = _ring_band(maxr)
    circles = [cr for cr in (_layer_circle(L, c, tol_rel)
                             for L in _radial_layers(pts, c, band)) if cr]
    if len(circles) < 2:
        return None
    # ★ 合并近等半径的圈（同一物理圆被半径抖动劈成两带）。
    circles = _merge_close_rings(circles, band)
    if len(circles) < 2:
        return None
    cost = sum(cr[4] / max(1e-6, cr[2]) for cr in circles) / len(circles)
    return cost, circles


def _pattern_search_center(pts, seed, init_step, tol_rel):
    """从 seed 出发的模式搜索（8 邻域 + 步长减半），最小化 `_eval_ring_center` 代价。

    网格搜索对圆心精度不够（圆层 rms 容差 ≈5%R，网格步长必须远小于它才能命中），
    故先粗网格定位、再用本函数精修到任意精度。纯确定性：方向顺序固定、同分不换点。
    无可行解（<2 圆）→ 返回 None。
    """
    best = _eval_ring_center(pts, seed, tol_rel)
    if best is None:
        return None
    bc = seed
    step = init_step
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1))
    for _ in range(60):
        if step < 1e-3:
            break
        improved = False
        for dx, dy in dirs:
            cand = (bc[0] + dx * step, bc[1] + dy * step)
            ev = _eval_ring_center(pts, cand, tol_rel)
            if ev is not None and ev[0] < best[0]:
                best, bc, improved = ev, cand, True
                break
        if not improved:
            step *= 0.5
    return bc, best


def _try_merge_ring(groups, ring, band):
    """把 ring 并入某个已存在组（圆心近且半径近，合并后仍共圆）→ True。"""
    cx, cy, r, bp, _rms = ring
    for m in groups:
        hx, hy, hr, hbp, _h = m[-1]
        if math.hypot(cx - hx, cy - hy) > band or abs(r - hr) > band:
            continue
        cand = hbp + bp
        f = _kasa_circle(cand)
        if f is not None and f[3] <= max(3.0, f[2] * _RING_CIRCLE_RMS_REL):
            m.append((f[0], f[1], f[2], cand, f[3]))
            return True
    return False


def _merge_close_rings(circles, band):
    """把圆心相近、半径差 < band 的圈并成一条（取点集并集重拟合）。

    防「一圈因半径抖动被切成两带」→ 输出两个几乎重合的圆。
    """
    if len(circles) < 2:
        return circles
    order = sorted(range(len(circles)), key=lambda i: circles[i][2])
    groups: List[list] = []
    for i in order:
        if not _try_merge_ring(groups, circles[i], band):
            groups.append([circles[i]])
    return [m[-1] for m in groups]


def _ring_candidate_ok(cl) -> bool:
    """预筛：全簇 Kasa 残差比落在中间带才可能分层出 ≥2 个整圆。

    真正的单圆（≈0）与散点/面状（很大）都不可能，直接跳过网格搜索——
    兼作性能护栏（至冬7.0 超广角 5418 台等大面阵由 `_RING_SEARCH_MAX_PTS`
    先拦）。实测：同心两圆≈0.5、单圆≈0、散布≈1.0。
    """
    pre = _kasa_circle(cl)
    if pre is None:
        return False
    rel = pre[3] / max(1e-6, pre[2])
    return 0.12 < rel < 0.85


def _grid_best_center(cl, box, tol):
    """粗网格搜索最优同心圆心 → ((cost,(cx,cy)) 或 None, 网格步长)。"""
    gx0, gx1, gy0, gy1 = box
    step_x = (gx1 - gx0) / (_RING_SEARCH_GRID - 1)
    step_y = (gy1 - gy0) / (_RING_SEARCH_GRID - 1)
    best = None          # (cost, (cx, cy))
    for i in range(_RING_SEARCH_GRID):
        for j in range(_RING_SEARCH_GRID):
            cx = gx0 + step_x * i
            cy = gy0 + step_y * j
            ev = _eval_ring_center(cl, (cx, cy), tol)
            if ev is not None and (best is None or ev[0] < best[0]):
                best = (ev[0], (cx, cy))
    return best, max(step_x, step_y)


def _rings_to_shapes(circles, cl):
    """圆层列表 → (整圆 shapes, 未归圆的剩余点)。"""
    shapes: List[dict] = []
    used: set = set()
    for cx, cy, r, bp, _rms in circles:
        shapes.append({
            "id": _shape_id("circle", cx, cy, r),
            "type": "circle", "cx": cx, "cy": cy,
            "rx": r, "ry": r, "angle": 0.0,
        })
        for p in bp:
            used.add((round(p[0], 3), round(p[1], 3)))
    rest = [p for p in cl if (round(p[0], 3), round(p[1], 3)) not in used]
    return shapes, rest


def _concentric_ring_shapes(cl: List[Tuple[float, float]]):
    """簇内识别同心多圆 → (circles_shapes, rest_points)。

    circles_shapes：整圆 shape 列表（同心 ≥2 圈才有）；rest_points：未归入
    任何圆的点（那条弧/线等），交调用方回退原拟合。识别不到多圈 → ([], cl)。
    """
    if len(cl) < _RING_MIN_MEMBERS * 2 or len(cl) > _RING_SEARCH_MAX_PTS:
        return [], cl
    if not _ring_candidate_ok(cl):
        return [], cl
    xs = [p[0] for p in cl]
    ys = [p[1] for p in cl]
    margin = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 0.15
    box = (min(xs) - margin, max(xs) + margin,
           min(ys) - margin, max(ys) + margin)
    # 搜索期用较松的圆层容差（圆心略偏时环内 rms 会放大，严容差会让粗网格
    # 全盘无解）；找到圆心后再用严格阈值终检。
    search_tol = 0.18
    best, step = _grid_best_center(cl, box, search_tol)
    if best is None:
        return [], cl
    refined = _pattern_search_center(cl, best[1], step, search_tol)
    if refined is None:
        return [], cl
    final = _eval_ring_center(cl, refined[0], _RING_CIRCLE_RMS_REL)
    if final is None:
        return [], cl
    return _rings_to_shapes(final[1], cl)


def _cluster_shapes(cl: List[Tuple[float, float]]) -> List[dict]:
    """单簇 → shape 列表：同心多圆优先，剩余点回退原拟合出弧/线。

    同心多圆（≥2 圈才接管；单圆/单弧/散点 → 原行为，零变化）。
    """
    ring_shapes: List[dict] = []
    rest = cl
    try:
        ring_shapes, rest = _concentric_ring_shapes(cl)
    except Exception:
        ring_shapes, rest = [], cl
    out = list(ring_shapes)
    if len(rest) < 3:
        return out
    try:
        s = build_fit_shape(rest)
    except Exception:
        s = None
    if s is not None:
        out.append(s)
    return out


def fit_shapes_for_points(pts: List[Tuple[float, float]]) -> List[dict]:
    """对一组点输出拟合形状列表（2026-08-03 用户指正重做）。

    - 矩阵类排列（百变）不拟合；
    - 空间聚类识别独立形状（双五角星/多弧）；
    - ★ 2026-09-17：簇内**同心多圆**优先单独成圆（用户「两圆一线」/百变
      「三圆三线」），剩余点再回退原拟合出那条弧——同心圆不再被合并成乱曲线；
    - 每簇简洁拟合：圆/弧/直线优先，五角星星形折线，其余排序后折线直连
      （不过 Catmull-Rom——过点样条对带偏移的设备点会扭来扭去，用户指正）。
    """
    pts = [(float(x), float(y)) for x, y in (pts or [])]
    if len(pts) < 3:
        return []
    if classify_layout(pts) in ("matrix", "none"):
        return []
    out: List[dict] = []
    for cl in _spatial_cluster(pts):
        if len(cl) >= 3:
            out.extend(_cluster_shapes(cl))
    return out


# ──────────────────────────────────────────────────────────────────────────────
#  类型布局（每类型一个 Sheet：居中坐标 + 按簇拟合图形；不含任何朝向）
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class TypeLayout:
    type_name: str
    positions: Dict[int, Tuple[float, float]]   # seq_id -> (画布x, 画布y)，整类居中后
    shapes: List[dict] = field(default_factory=list)  # 按簇拟合的 CAD 图形（matrix/单台不产出）
    units: List[UnitSpec] = field(default_factory=list)  # 微单元（居中坐标系下：成员 seq + 中心 + 偏移）


def build_type_layout(g: TypeGroup, scale: float = CANVAS_UNITS_PER_CM) -> TypeLayout:
    """为一个设备类型构建 TopView 布局：

    - positions: 每台设备(按 seq_id)的画布坐标，**整类形心平移到原点**（各 Sheet 自身居中，
      保留所有簇之间的真实相对位置）。这是真实散布坐标，会写进各成员 FixtureInstance。
    - units: 检出的微单元（成员相对单元中心的局部偏移=隐藏局部坐标系），单元中心进入宏拟合。
    - shapes: 对「单元中心 + 散台」拟合圆/弧/线；**单元内部不拟合曲线**（按矩阵理解）。
      未检出单元时退化为旧行为（按簇拟合）。

    朝向/旋转完全不参与——仅按位置摆放并拟合图形。
    """
    # 同 seq_id 去重（R4 重复行）：每个 seq_id 取首次坐标，与 detect_micro_units 一致。
    # 否则形心/positions(last-wins) 与单元中心/偏移(first-wins) 相互矛盾，且重复行偏置形心。
    seen_rows: Dict[int, "PatchRow"] = {}
    for r in g.rows:
        if r.seq_id not in seen_rows:
            seen_rows[r.seq_id] = r
    rows_sorted = [seen_rows[s] for s in sorted(seen_rows)]
    raw_pts = [csv_to_canvas(r.x_cm, r.z_cm, scale) for r in rows_sorted]
    if raw_pts:
        cx = sum(p[0] for p in raw_pts) / len(raw_pts)
        cy = sum(p[1] for p in raw_pts) / len(raw_pts)
    else:
        cx = cy = 0.0
    positions: Dict[int, Tuple[float, float]] = {}
    centered_by_seq: Dict[int, Tuple[float, float]] = {}
    for r, (x, y) in zip(rows_sorted, raw_pts):
        p = (x - cx, y - cy)
        positions[r.seq_id] = p
        centered_by_seq[r.seq_id] = p

    # 微单元检测（在原始 scale 坐标系；中心/偏移再平移到居中坐标系）。
    units_raw, _free = detect_micro_units(g.rows, scale=scale)
    units: List[UnitSpec] = []
    unit_member_seqs: set = set()
    unit_centers: List[Tuple[float, float]] = []
    for u in units_raw:
        c_centered = (u.center[0] - cx, u.center[1] - cy)
        units.append(UnitSpec(member_seq_ids=list(u.member_seq_ids),
                              center=c_centered,
                              member_offsets=dict(u.member_offsets)))
        unit_member_seqs.update(u.member_seq_ids)
        unit_centers.append(c_centered)

    shapes: List[dict] = []
    if units:
        # 宏点集：每单元一个中心 + 未入单元的散台中心坐标。整体一次拟合（已折叠单元，
        # 不再对单元内小圈拟合）。散台 + 单元中心共同决定大圆/弧/线。
        macro_pts: List[Tuple[float, float]] = list(unit_centers)
        for seq, p in centered_by_seq.items():
            if seq not in unit_member_seqs:
                macro_pts.append(p)
        shape = build_fit_shape(macro_pts)
        if shape is not None:
            shapes.append(shape)
    else:
        for cluster in split_group_into_clusters(g, scale=scale):
            cl_pts = [centered_by_seq[r.seq_id] for r in cluster]
            shape = build_fit_shape(cl_pts)
            if shape is not None:
                shapes.append(shape)
    return TypeLayout(type_name=g.type_name, positions=positions, shapes=shapes, units=units)

