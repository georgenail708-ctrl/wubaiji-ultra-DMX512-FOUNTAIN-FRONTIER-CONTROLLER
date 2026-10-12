# -*- coding: utf-8 -*-
"""音频调制 clip 断点包络（R180 #14，Draw Mode 范式；纯逻辑无 Qt）。

数据模型（挂在 `audio_modulation` 的 clip dict 上，可选字段）：

    clip["points"] = [[t, v] | [t, v, c], ...]
      t : 0..1  clip 内归一化时间（start→0，end→1）
      v : 0..1  包络值
      c : -1..1 该点到**下一个**点之间段的曲率（缺省/0 = 直线；末点的 c 无意义）。
            c>0 = 幂曲线慢出（ease-in，贴合起点值），c<0 = 快出（ease-out，
            早达终点值）——段方向空间语义，与升/降无关，单调不越界。

求值语义（`audio_modulation._active_envelope_norm` 接线，确定性）：
  - clip 带非空 points → 包络项 = 断点折线/曲线求值（**替代** attack/release
    斜坡；波形项 `clip.wave` 仍照常相乘）。无 points → 旧斜坡逐位不变。
  - 段内形：s∈[0,1]，q = _BEND_EXP**c（c=±1 → q=16 或 1/16）；
    c>0 → s**q；c<0 → 1-(1-s)**(1/q)；c=0 → s。形状单调 ⇒ 值恒在两端点
    hull 内 ⇒ 不外溢 [0,1]。
  - 同 t 断点 = 垂直跳变（阶跃）：normalize 每个 t 最多保留首+末两点；
    求值右连续（u 恰在跳变点 = 末点值）。

序列化：t/v 量化 4 位小数、c 3 位（与 clip start/end 的 round(,4) 同族），
normalize 幂等 → 同输入逐字节同输出。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from typing import List, Optional, Sequence, Tuple

#: 段曲率指数上限（|c|=1 → q=16；几何映射 |c|=ln q / ln 16 使拖拽手感线性）
_BEND_EXP = 16.0

#: 归一化量化精度（与 audio_modulation clip.start/end round(,4) 一致）
_T_DIGITS = 4
_V_DIGITS = 4
_C_DIGITS = 3

#: RDP 简化默认容差（归一化坐标单位，≈4‰ 幅度）
SIMPLIFY_EPS = 0.01


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _clamp01(v) -> float:
    return _clamp(_f(v, 0.0), 0.0, 1.0)


# ── 归一化 / 校验 ─────────────────────────────────────────────────────
def normalize_points(raw) -> List[list]:
    """原始 points → 规范表 [[t,v] / [t,v,c]]（非法行丢弃；空/非法 → []）。

    规则：t,v,c 数值化+钳位；按 t 升序稳定排序；同一 t 保留首+末
    （中间点对求值无贡献，丢弃）；量化 4/4/3 位小数；c=0 记 2 元素。
    单点合法（= 全段恒值）；0 点 = 无包络（调用方走旧斜坡）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    rows: List[list] = []
    for p in raw:
        row = _normalize_point(p)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r[0])
    return _dedup_t(rows)


def _normalize_point(p) -> Optional[list]:
    """单点归一化：[t,v] / [t,v,c] / {t,v,c} 三种来源；非法 → None。"""
    if isinstance(p, dict):
        t, v, c = p.get("t"), p.get("v"), p.get("c")
    elif isinstance(p, (list, tuple)) and len(p) >= 2:
        t, v = p[0], p[1]
        c = p[2] if len(p) >= 3 else 0.0
    else:
        return None
    t, v, c = _clamp01(t), _clamp01(v), _clamp(_f(c, 0.0), -1.0, 1.0)
    if t != t or v != v or c != c:      # NaN 防线（float('nan') 会污染排序）
        return None
    out = [round(t, _T_DIGITS), round(v, _V_DIGITS)]
    if abs(c) > 1e-9:
        out.append(round(c, _C_DIGITS))
    return out


def _dedup_t(rows: List[list]) -> List[list]:
    """同 t（量化后相等）保留首+末（=阶跃对的左/右值），其余丢。"""
    out: List[list] = []
    i, n = 0, len(rows)
    while i < n:
        j = i + 1
        while j < n and rows[j][0] == rows[i][0]:
            j += 1
        out.append(rows[i])
        if j - 1 > i:
            out.append(rows[j - 1])
        i = j
    return out


# ── 段形曲线（单调幂族）──────────────────────────────────────────────
def _shape(s: float, c: float) -> float:
    """段内形 f(s)→[0,1]：c=0 线性；c>0 慢出 s**q；c<0 快出 1-(1-s)**(1/q)。"""
    s = _clamp(s, 0.0, 1.0)
    if abs(c) < 1e-9:
        return s
    q = _BEND_EXP ** _clamp(abs(c), 0.0, 1.0)
    if c > 0:
        return s ** q
    return 1.0 - (1.0 - s) ** q


def truncate_points_tail(points, u_cut: float) -> Optional[List[list]]:
    """截掉 [u_cut, 1] **尾段**，保留 [0,u_cut] 头段重参数化到 [0,1]
    （R183 清查⑤①F1：_resolve_overlaps 截短保形——修前只改 end 不动
    points，表值与保形真值偏差最高 100%）。

    复用 `_resplit_points` 左半纪律：t' = t/u_cut，曲率 p[2:] 原样保留，
    末点钉 [1, v_cut]（v_cut=eval(u_cut)）。
    u_cut<=0 → None（无可保留）；u_cut>=1 → 全表拷贝（幂等）。
    """
    try:
        pts = normalize_points(points)
    except Exception:
        return None
    if not pts or len(pts) < 2:
        return None
    u = _clamp(_f(u_cut, 0.0), 0.0, 1.0)
    if u <= 0.0:
        return None
    if u >= 1.0:
        return [list(p) for p in pts]
    v_cut = eval_points(pts, u)
    kept = [[float(p[0]) / u, float(p[1]), *p[2:]]
            for p in pts if float(p[0]) <= u]
    if not kept or kept[-1][0] < 1.0:
        kept.append([1.0, v_cut])
    return kept


def eval_points(points: Sequence[Sequence[float]], u: float) -> float:
    """已归一化断点表在 u∈[0,1] 的包络值（每帧标量路径：bisect 无大分配）。

    u 越界钳到首/末点值；空表给 0；同 t 跳变取右连续值。
    """
    n = len(points)
    if n == 0:
        return 0.0
    u = _clamp(_f(u, 0.0), 0.0, 1.0)
    if u <= points[0][0]:
        return float(points[0][1])
    if u >= points[-1][0]:
        return float(points[-1][1])
    ts = [p[0] for p in points]
    i = min(n - 2, bisect_right(ts, u) - 1)     # 右连续：dup t 取后段
    t0, v0 = float(points[i][0]), float(points[i][1])
    t1, v1 = float(points[i + 1][0]), float(points[i + 1][1])
    span = t1 - t0
    if span <= 0.0:
        return v1
    c = float(points[i][2]) if len(points[i]) > 2 else 0.0
    return v0 + _shape((u - t0) / span, c) * (v1 - v0)


def solve_bend(s: float, f: float, cur: float = 0.0) -> float:
    """段拖拽→曲率：求 c 使段形过 (s, f)（段方向空间，s/f∈[0,1]）。

    曲线过点反演：f>s → 快出 q = ln(1-f)/ln(1-s)；f<s → 慢出 q = ln(f)/ln(s)。
    c = ±ln(q)/ln(16)。f 超出可达域（c=±1 的极限弧）→ 钳位=饱和手感；
    s 贴端点（无可调空间）→ 返回现有 c。
    """
    if not (0.0 < s < 1.0):
        return _clamp(cur, -1.0, 1.0)
    f = _clamp(f, s ** _BEND_EXP, 1.0 - (1.0 - s) ** _BEND_EXP)
    if f >= s:
        q = math.log(max(1e-12, 1.0 - f)) / math.log(max(1e-12, 1.0 - s))
        return -_clamp(math.log(q) / math.log(_BEND_EXP), 0.0, 1.0)
    q = math.log(max(1e-12, f)) / math.log(max(1e-12, s))
    return _clamp(math.log(q) / math.log(_BEND_EXP), 0.0, 1.0)


# ── 编辑操作（返回新表，不改入参；调用方负责 commit/undo 快照）─────────
def insert_point(points: Sequence[Sequence[float]], u: float, v: float) -> Tuple[List[list], int]:
    """在 (u,v) 加点 → (新表, 新点下标)。同 t 落在已有同值组之后（阶跃可塑）。"""
    pts = [list(p) for p in (points or [])]
    row = _normalize_point([u, v]) or [0.0, _clamp01(v)]
    idx = bisect_right([p[0] for p in pts], row[0])
    pts.insert(idx, row)
    return pts, idx


def move_point(points: Sequence[Sequence[float]], idx: int, u: float, v: float,
               lock_ends: bool = True) -> Tuple[List[list], int]:
    """拖点：v 钳 0..1；t 钳 [前点t, 后点t]（可达同 t = 竖直跳变，不可越序）。

    `lock_ends`：首/末点横向锁定（端点钉死惯例），只许纵向拖。
    返回 (新表, 下标)——表长与下标不变。
    """
    pts = [list(p) for p in (points or [])]
    n = len(pts)
    if not (0 <= idx < n):
        return pts, idx
    lo = pts[idx - 1][0] if idx > 0 else 0.0
    hi = pts[idx + 1][0] if idx < n - 1 else 1.0
    t = (pts[idx][0] if (lock_ends and idx in (0, n - 1))
         else _clamp(_f(u, pts[idx][0]), lo, hi))
    pts[idx] = [t, _clamp01(v)] + pts[idx][2:]
    return pts, idx


def delete_point(points: Sequence[Sequence[float]], idx: int) -> List[list]:
    """删点：删到 <1 个 → []（= 回到旧斜坡包络语义）。"""
    pts = [list(p) for p in (points or [])]
    if 0 <= idx < len(pts):
        del pts[idx]
    return pts


def bend_segment(points: Sequence[Sequence[float]], seg_idx: int, c: float) -> List[list]:
    """设段 [i→i+1] 曲率（写在该段起点第 3 元）。末段/越界不动。"""
    pts = [list(p) for p in (points or [])]
    if not (0 <= seg_idx < len(pts) - 1):
        return pts
    c = _clamp(_f(c, 0.0), -1.0, 1.0)
    p = pts[seg_idx][:2]
    if abs(c) > 1e-9:
        p.append(round(c, _C_DIGITS))
    pts[seg_idx] = p
    return pts


def segment_bend(points: Sequence[Sequence[float]], seg_idx: int) -> float:
    """段 [i→i+1] 当前曲率（无 c → 0.0）。"""
    if not (0 <= seg_idx < len(points) - 1):
        return 0.0
    p = points[seg_idx]
    return _f(p[2], 0.0) if len(p) > 2 else 0.0


def _ramp_env(u: float, ua: float, ub: float) -> float:
    """旧 attack/release 斜坡的归一化求值（ua=attack/span，ub=1-release/span）。

    与 `audio_modulation._active_envelope_norm` 同公式：u<ua 升坡、u>ub 降坡、
    其余平台 1（attack+release>clip 长时的重叠怪形原样保留）。
    """
    if u < ua:
        return min(1.0, u / max(ua, 1e-9))
    if u > ub:
        return min(1.0, (1.0 - u) / max(1.0 - ub, 1e-9))
    return 1.0


def seed_points_from_ramp(attack_s: float, release_s: float,
                          span_s: float) -> List[list]:
    """首次编辑的种子：把全局 attack/release 斜坡（秒）物化成断点。

    锚点 = 斜坡折线的全部拐点 u∈{0, ua, ub, 1}，值用 `_ramp_env` 现算——
    常规（attack+release≤span）下精确复刻旧梯形包络，用户第一次加点时
    听到的曲线不跳变；量化到 4 位小数后经 normalize 幂等。
    """
    span = max(_f(span_s, 0.0), 1e-9)
    ua = _clamp(_f(attack_s, 0.0) / span, 0.0, 1.0)
    ub = 1.0 - _clamp(_f(release_s, 0.0) / span, 0.0, 1.0)
    anchors = {u: _ramp_env(u, ua, ub) for u in (0.0, ua, ub, 1.0)}
    return normalize_points([[u, v] for u, v in sorted(anchors.items())])


def simplify_points(points: Sequence[Sequence[float]],
                    eps: float = SIMPLIFY_EPS) -> List[list]:
    """RDP 抽稀（复用 route_animator.track_smooth.rdp；曲率清零=直段化）。

    主流 DAW「Simplify Envelope」同款：密集断点收敛成关键折点。
    同 t 跳变点不参与抽稀（阶跃语义会被 RDP 抹平），先剔除再抽稀后合回。
    """
    pts = normalize_points(points)
    if len(pts) < 3:
        return pts
    from .route_animator.track_smooth import rdp     # 懒加载（numpy 链）
    jump_idx = {i for i in range(1, len(pts)) if pts[i][0] == pts[i - 1][0]}
    base = [(i, p) for i, p in enumerate(pts) if i not in jump_idx]
    kept = set(rdp([(p[0], p[1]) for _i, p in base], max(0.0, _f(eps, SIMPLIFY_EPS))))
    out = [list(p) for i, p in enumerate(pts)
           if i in jump_idx or (p[0], p[1]) in kept]
    return normalize_points(out)


# ── 指纹 ─────────────────────────────────────────────────────────────
def points_key(raw) -> tuple:
    """points 的稳定指纹元组（空 → ()；进 audio_modulation.signature）。"""
    return tuple(tuple(p) for p in normalize_points(raw))
