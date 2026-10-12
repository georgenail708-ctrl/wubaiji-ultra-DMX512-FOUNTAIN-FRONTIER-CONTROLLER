"""
trajectory_curve.py — 轨迹曲线（Programmer 子功能）
====================================================
一条「时间 → 归一化值[0,1]」的曲线（手画 / 推杆录制），可沿时间竖切成若干**段**，
每段单独设置 *方向*（整体 / 从左到右 / 从右到左）+ *翼* + *块*。播放时：

  - 全局读数 V = curve(phase)，phase = t/duration（loop 时按周期折叠）。
  - 段只决定**当前 V 反映在哪些设备上**：
      · whole：组内全部设备同时 = V（整体缓缓升降）。
      · ltr/rtl：一条「揭示前沿」按方向扫过组内设备（按 TopView X 排序、翼/块分单元），
        前沿已扫到的设备 = V；**未扫到的设备不写**，于是天然保持上一刻的值。
    => 段与段之间无需任何显式过渡：后段的扫描推进到哪、覆盖到哪；没推进到的设备值不动。

设计上完全复用：
  - core.models.CurveMapping / CurvePoint（控制点 + 线性插值 + 序列化）
  - utils.wing_block.build_wing_block_units（翼/块分单元，复用 Programmer 既有定义）

数据层与 UI / 引擎解耦：本模块只懂「给我一个 group_id→有序设备列表 的解析器，我算出此刻
每个设备该写的 {ch_type: 0-255}」。设备坐标排序、通道地址转换、实际输出都在 Programmer 侧。
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Sequence, Tuple

try:
    from ..core.models import CurveMapping, CurvePoint
except Exception:  # pragma: no cover - 兜底，保证可独立导入
    CurveMapping = None  # type: ignore
    CurvePoint = None    # type: ignore

from . import trajectory_wing_adapter as _wing_adapter


# 曲线类别：
#   path  = 路径曲线（旧行为）：全局读数 V=curve(t)，方向揭示前沿按时间扫过设备。
#   shape = 造型曲线（新）：每段取自己那截曲线，按设备空间序(L→R)平分采样成一个**静态造型**；
#           段内值不随时间变化（指针只反映在哪段）；段与段交界处在 transition 秒窗口内
#           （蚕食相邻两段、总时长不变）于「上段造型 ↔ 本段造型」间线性 morph。
CURVE_TYPE_PATH = "path"
CURVE_TYPE_SHAPE = "shape"
CURVE_TYPES = (CURVE_TYPE_PATH, CURVE_TYPE_SHAPE)
CURVE_TYPE_LABELS = {
    CURVE_TYPE_PATH: "路径曲线",
    CURVE_TYPE_SHAPE: "造型曲线",
}

# 段方向（与 clip 右击「分组方向过渡」保持一致的语义码）。
DIR_WHOLE = "whole"
DIR_LTR   = "ltr"
DIR_RTL   = "rtl"
DIR_FTB   = "ftb"   # 从前到后（Y 轴正方向）
DIR_BTF   = "btf"   # 从后到前
DIR_CTR   = "ctr"   # 中心向两边
DIR_RTC   = "rtc"   # 两边向中心
DIR_CW    = "cw"    # 顺时针（角度升序）
DIR_CCW   = "ccw"   # 逆时针
DIR_ALT   = "alt"   # 奇偶交替
SEGMENT_DIRECTIONS = (
    DIR_WHOLE, DIR_LTR, DIR_RTL,
    DIR_FTB, DIR_BTF,
    DIR_CTR, DIR_RTC,
    DIR_CW, DIR_CCW,
    DIR_ALT,
)
DIRECTION_LABELS = {
    DIR_WHOLE: "整体",
    DIR_LTR:   "从左到右",
    DIR_RTL:   "从右到左",
    DIR_FTB:   "从前到后",
    DIR_BTF:   "从后到前",
    DIR_CTR:   "中心向两边",
    DIR_RTC:   "两边向中心",
    DIR_CW:    "顺时针",
    DIR_CCW:   "逆时针",
    DIR_ALT:   "奇偶交替",
}
# 需要 XY 坐标才能排序的方向集合（不能仅靠 L→R 索引）。
_SPATIAL_DIRS = frozenset({DIR_FTB, DIR_BTF, DIR_CTR, DIR_RTC, DIR_CW, DIR_CCW})

# 坐标系（与 clip 的 fade_group_spatial_scope 一致）：
#   per_shape=各自坐标系(每个 shape 子组独立 0..1 扫)；global=整体坐标系(全组一条扫线)；
#   follow=跟随项目全局灯控开关(单控→per_shape / 整体→global)。
SCOPE_PER_SHAPE = "per_shape"
SCOPE_GLOBAL = "global"
SCOPE_FOLLOW = "follow"
SPATIAL_SCOPES = (SCOPE_FOLLOW, SCOPE_PER_SHAPE, SCOPE_GLOBAL)
SCOPE_LABELS = {
    SCOPE_FOLLOW: "跟随灯控",
    SCOPE_PER_SHAPE: "各自坐标系",
    SCOPE_GLOBAL: "整体坐标系",
}

# 分组时序（与 clip 的 fade_group_sequence_mode 一致）。
GROUP_PARALLEL = "parallel"
GROUP_SEQUENTIAL = "sequential"
GROUP_MODES = (GROUP_PARALLEL, GROUP_SEQUENTIAL)
GROUP_MODE_LABELS = {GROUP_PARALLEL: "并行", GROUP_SEQUENTIAL: "顺序"}

# 造型曲线 morph 的方向扫描错峰强度：0=全设备同步、→1=硬扫。非 whole 方向时各单元
# 起点错峰 = p*spread、斜坡 = (1-spread)，保证 f=1 时所有单元 alpha=1（到位）。
_MORPH_SWEEP_SPREAD = 0.6

# 路径曲线揭示扫描的「落位比例」：把整段扫描压进段内前 SETTLE 比例完成，剩余时间所有设备
# 已被扫到、停在段末值。保证「段时间跑完后所有设备都到达段末状态」（用户要求）。
_PATH_REVEAL_SETTLE = 0.9


def _clamp01(v: float) -> float:
    try:
        v = float(v)
    except Exception:
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


@dataclass
class TrajectorySegment:
    """一段曲线的播放属性（不含曲线本身，曲线是整条共享的）。

    direction     : whole / ltr / rtl（揭示前沿扫描方向）。
    wings / block : 翼/块分单元（沿用 wing_block 定义），决定扫描的空间粒度。
    spatial_scope : per_shape / global / follow（各自/整体/跟随灯控坐标系）。
    groups        : 分组数（1=不分组）。把方向有序的单元切成 N 个连续批次。
    group_mode    : parallel / sequential（并行=各批同时在窗口内错峰；顺序=一批接一批）。
    group_pause   : 顺序模式各批之间的过渡间隔（秒，标量回退）。
    group_pauses  : 顺序模式逐间隔过渡时间列表（秒，长度 = 批次数-1；空则用 group_pause）。
    group_batches : 有效批次数（0=等于 groups）。
    transition    : 【造型曲线专用】进入本段的 morph 时长（秒）。窗口居中于「本段起点」边界，
                    蚕食上一段尾部与本段头部各一半，**不增加总时长**。段 0 无意义（忽略）。
    """
    direction: str = DIR_WHOLE
    wings: int = 1
    block: int = 1
    spatial_scope: str = SCOPE_FOLLOW
    groups: int = 1
    group_mode: str = GROUP_PARALLEL
    group_pause: float = 0.0
    group_pauses: List[float] = field(default_factory=list)
    group_batches: int = 0
    transition: float = 0.0
    wing_mirror: bool = False
    wing_reverse: bool = False

    def __post_init__(self):
        self.direction = self.direction if self.direction in SEGMENT_DIRECTIONS else DIR_WHOLE
        self.wings = max(1, int(self.wings or 1))
        self.block = max(1, int(self.block or 1))
        self.spatial_scope = self.spatial_scope if self.spatial_scope in SPATIAL_SCOPES else SCOPE_FOLLOW
        self.groups = max(1, int(self.groups or 1))
        self.group_mode = self.group_mode if self.group_mode in GROUP_MODES else GROUP_PARALLEL
        try:
            self.group_pause = max(0.0, float(self.group_pause or 0.0))
        except Exception:
            self.group_pause = 0.0
        self.group_pauses = [max(0.0, float(p or 0.0)) for p in (self.group_pauses or [])]
        self.group_batches = max(0, int(self.group_batches or 0))
        try:
            self.transition = max(0.0, float(self.transition or 0.0))
        except Exception:
            self.transition = 0.0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "wings": int(self.wings),
            "block": int(self.block),
            "wing_mirror": bool(self.wing_mirror),
            "wing_reverse": bool(self.wing_reverse),
            "spatial_scope": self.spatial_scope,
            "groups": int(self.groups),
            "group_mode": self.group_mode,
            "group_pause": float(self.group_pause),
            "group_pauses": [float(p) for p in self.group_pauses],
            "group_batches": int(self.group_batches),
            "transition": float(self.transition),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrajectorySegment":
        d = dict(d or {})
        return cls(
            direction=str(d.get("direction", DIR_WHOLE)),
            **_wing_adapter.from_storage(d),
            spatial_scope=str(d.get("spatial_scope", SCOPE_FOLLOW)),
            groups=int(d.get("groups", 1) or 1),
            group_mode=str(d.get("group_mode", GROUP_PARALLEL)),
            group_pause=float(d.get("group_pause", 0.0) or 0.0),
            group_pauses=[float(p or 0.0) for p in (d.get("group_pauses", []) or [])],
            group_batches=int(d.get("group_batches", 0) or 0),
            transition=float(d.get("transition", 0.0) or 0.0),
        )

    # ── 批次时序辅助（复刻 clip 的 _clip_sequential_offsets 语义）─────────────
    def effective_batches(self) -> int:
        if self.group_batches <= 0:
            return max(1, self.groups)
        return max(1, min(self.groups, self.group_batches))

    def batch_offsets_seconds(self, per_group: float) -> List[float]:
        """顺序模式各批的起始偏移（秒）。offset[i]=i*per_group + sum(pauses[:i])。"""
        bc = self.effective_batches()
        offsets = []
        cursor = 0.0
        for b in range(bc):
            offsets.append(cursor)
            cursor += per_group
            if b < bc - 1:
                cursor += self.group_pauses[b] if b < len(self.group_pauses) else self.group_pause
        return offsets

    def sequential_span_seconds(self, per_group: float) -> float:
        bc = self.effective_batches()
        extra = sum(self.group_pauses[b] if b < len(self.group_pauses) else self.group_pause
                    for b in range(max(0, bc - 1)))
        return per_group * bc + extra


@dataclass
class TrajectoryCurve:
    """一条轨迹曲线。

    curve_type : path（路径曲线，旧）/ shape（造型曲线，新）。
    duration : 整条时长（秒）。
    curve    : CurveMapping，控制点 x=相位[0,1]、y=归一化值[0,1]。
    cuts     : 内部分界相位 (0,1) 升序列表；N 个 cut => N+1 段。
    segments : 长度 == len(cuts)+1 的段属性列表（与 cuts 同步）。
               path 模式：每段方向/翼块/分组/scope（揭示前沿扫描）。
               shape 模式：每段静态造型的 spatial_scope/wings/block（铺造型的那套）。
    boundaries: 长度 == len(cuts)+2 的**边界** morph 设置（仅 shape 模式用）。
               [0]=起点淡入、[1..n-1]=内部分割线、[n]=终点淡出。各持自己的
               transition(秒)+direction/wings/block/scope/groups/group_mode/group_pause
               （morph 那套，与段静态那套**分开**）。
    targets  : 应用目标 [{"group_id": str, "channel_types": [str,...]}]，可多组、每组多通道。
    loop     : 播放时是否循环。
    """
    id: str = field(default_factory=lambda: "traj_" + uuid.uuid4().hex[:12])
    name: str = "轨迹"
    curve_type: str = CURVE_TYPE_PATH
    duration: float = 4.0
    source: str = "manual"            # manual / recorded
    curve: Any = None
    points: List[List[float]] = field(default_factory=list)
    cuts: List[float] = field(default_factory=list)
    segments: List[TrajectorySegment] = field(default_factory=list)
    boundaries: List[TrajectorySegment] = field(default_factory=list)
    targets: List[dict] = field(default_factory=list)
    loop: bool = False
    created_at: float = field(default_factory=lambda: time.time())

    def __post_init__(self):
        self.curve_type = self.curve_type if self.curve_type in CURVE_TYPES else CURVE_TYPE_PATH
        if self.duration is None or float(self.duration) <= 0.0:
            self.duration = 4.0
        if self.curve is None and CurveMapping is not None:
            self.curve = CurveMapping(
                points=[CurvePoint(0.0, 0.0), CurvePoint(1.0, 1.0)],
                source=self.source or "manual",
            )
        raw_cuts = sorted(_clamp01(c) for c in (self.cuts or []) if 0.0 < float(c) < 1.0)
        # 相邻去重（与 add_cut 的 1e-3 近重判据一致），避免损坏存档产生零宽段。
        deduped: List[float] = []
        for c in raw_cuts:
            if not deduped or abs(c - deduped[-1]) >= 1e-3:
                deduped.append(c)
        self.cuts = deduped
        self._sync_segments()
        self._sync_boundaries()

    # ── 段 / cut 管理 ──────────────────────────────────────────────────────────
    def _sync_segments(self):
        """保证 segments 长度 == len(cuts)+1；多删少补（新段默认 whole）。"""
        want = len(self.cuts) + 1
        segs = list(self.segments or [])
        if len(segs) > want:
            segs = segs[:want]
        while len(segs) < want:
            segs.append(TrajectorySegment())
        # 容错：dict → TrajectorySegment
        self.segments = [s if isinstance(s, TrajectorySegment) else TrajectorySegment.from_dict(s)
                         for s in segs]

    def _sync_boundaries(self):
        """保证 boundaries 长度 == len(cuts)+2（start + 内部分割线 + end）。"""
        want = len(self.cuts) + 2
        bnds = list(self.boundaries or [])
        if len(bnds) > want:
            bnds = bnds[:want]
        while len(bnds) < want:
            bnds.append(TrajectorySegment())
        self.boundaries = [b if isinstance(b, TrajectorySegment) else TrajectorySegment.from_dict(b)
                           for b in bnds]

    def segment_bounds(self) -> List[Tuple[float, float]]:
        """返回每段的 (start_phase, end_phase)，已含首尾 0/1。"""
        edges = [0.0] + list(self.cuts) + [1.0]
        return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

    def add_cut(self, phase: float) -> int:
        """在 phase 处切一刀；返回新 cut 的索引（已存在很近的则忽略）。"""
        phase = _clamp01(phase)
        if not (0.0 < phase < 1.0):
            return -1
        for c in self.cuts:
            if abs(c - phase) < 1e-3:
                return -1
        # 切分时把被切的那段属性复制给两半（保持原方向/翼块）。
        bounds = self.segment_bounds()
        seg_idx = 0
        for i, (s, e) in enumerate(bounds):
            if s <= phase < e:
                seg_idx = i
                break
        self.cuts.append(phase)
        self.cuts.sort()
        new_idx = self.cuts.index(phase)
        # 复制原段属性到两半
        src = self.segments[seg_idx] if seg_idx < len(self.segments) else TrajectorySegment()
        clone = TrajectorySegment(
            direction=src.direction, wings=src.wings, block=src.block,
            wing_mirror=src.wing_mirror, wing_reverse=src.wing_reverse,
            spatial_scope=src.spatial_scope, groups=src.groups, group_mode=src.group_mode,
            group_pause=src.group_pause, group_pauses=list(src.group_pauses),
            group_batches=src.group_batches, transition=src.transition,
        )
        self.segments.insert(seg_idx + 1, clone)
        self._sync_segments()
        # 新分割线对应一个新内部边界 boundaries[new_idx+1]（默认 morph 设置）。
        self.boundaries.insert(new_idx + 1, TrajectorySegment())
        self._sync_boundaries()
        return new_idx

    def remove_cut(self, cut_index: int):
        """删除第 cut_index 条分界线（合并其右段进左段，保留左段属性）。"""
        if not (0 <= cut_index < len(self.cuts)):
            return
        self.cuts.pop(cut_index)
        # 合并：删掉右段属性（cut_index+1 对应的段）。
        if cut_index + 1 < len(self.segments):
            self.segments.pop(cut_index + 1)
        # 删掉该分割线对应的内部边界 boundaries[cut_index+1]。
        if cut_index + 1 < len(self.boundaries) - 1:
            self.boundaries.pop(cut_index + 1)
        self._sync_segments()
        self._sync_boundaries()

    def clear_cuts(self):
        self.cuts = []
        self.segments = [self.segments[0] if self.segments else TrajectorySegment()]
        # 仅保留起点/终点两个边界。
        start_b = self.boundaries[0] if self.boundaries else TrajectorySegment()
        end_b = self.boundaries[-1] if len(self.boundaries) >= 2 else TrajectorySegment()
        self.boundaries = [start_b, end_b]

    # ── 求值 ─────────────────────────────────────────────────────────────────
    def phase_at(self, t: float) -> float:
        dur = float(self.duration) if self.duration else 4.0
        if dur <= 0.0:
            dur = 4.0
        try:
            t = float(t)
        except Exception:
            t = 0.0
        if self.loop:
            return _clamp01((t % dur) / dur)
        return _clamp01(t / dur)

    def value_at(self, t: float) -> float:
        """整条曲线在时刻 t 的归一值 [0,1]。"""
        return self._eval_phase(self.phase_at(t))

    def _eval_phase(self, phase: float) -> float:
        phase = _clamp01(phase)
        if self.curve is not None and hasattr(self.curve, "evaluate"):
            try:
                return _clamp01(float(self.curve.evaluate(phase)))
            except Exception:
                pass
        pts = sorted(self.points or [], key=lambda p: p[0])
        if not pts:
            return 0.0
        if phase <= pts[0][0]:
            return _clamp01(pts[0][1])
        if phase >= pts[-1][0]:
            return _clamp01(pts[-1][1])
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 <= phase <= x1:
                k = (phase - x0) / (x1 - x0) if x1 != x0 else 0.0
                return _clamp01(y0 + k * (y1 - y0))
        return _clamp01(pts[-1][1])

    def segment_progress_at(self, t: float) -> Tuple[int, "TrajectorySegment", float]:
        """返回 (段索引, 段, 段内进度 f∈[0,1])。"""
        phase = self.phase_at(t)
        bounds = self.segment_bounds()
        for i, (s, e) in enumerate(bounds):
            if (s <= phase < e) or (i == len(bounds) - 1 and phase >= s):
                f = (phase - s) / (e - s) if e > s else 1.0
                seg = self.segments[i] if i < len(self.segments) else TrajectorySegment()
                return i, seg, _clamp01(f)
        seg = self.segments[0] if self.segments else TrajectorySegment()
        return 0, seg, 0.0

    @staticmethod
    def _batch_partition(n_units: int, batch_count: int):
        """把 n_units 个单元按 clip 语义切成 bc 个连续批次（ceil 分块）。返回 (每单元批号, bc)。"""
        bc = max(1, min(int(batch_count), max(1, n_units)))
        step = -(-n_units // bc) if n_units > 0 else 1  # ceil(n/bc)
        return [min(bc - 1, i // step) for i in range(n_units)], bc

    @staticmethod
    def _sort_by_direction(ids: List[str], direction: str,
                           positions: Optional[Dict[str, Tuple[float, float]]] = None) -> List[str]:
        """按方向对 ids 重新排序，返回新列表。positions 可为空（退化为 ltr 语义）。"""
        if direction in (DIR_WHOLE, DIR_LTR) or not ids:
            return list(ids)
        if direction == DIR_RTL:
            return list(reversed(ids))
        pos = positions or {}
        n = len(ids)

        def _xy(fid):
            p = pos.get(str(fid))
            return (float(p[0]), float(p[1])) if p is not None else (0.0, 0.0)

        if direction == DIR_FTB:
            return sorted(ids, key=lambda fid: (_xy(fid)[1],  _xy(fid)[0]))
        if direction == DIR_BTF:
            return sorted(ids, key=lambda fid: (-_xy(fid)[1], _xy(fid)[0]))
        if direction in (DIR_CTR, DIR_RTC):
            xs = [_xy(fid)[0] for fid in ids]
            mid = sum(xs) / n if n else 0.0
            if direction == DIR_CTR:
                return sorted(ids, key=lambda fid: abs(_xy(fid)[0] - mid))
            else:
                return sorted(ids, key=lambda fid: -abs(_xy(fid)[0] - mid))
        if direction in (DIR_CW, DIR_CCW):
            import math as _math
            xs = [_xy(fid)[0] for fid in ids]
            ys = [_xy(fid)[1] for fid in ids]
            cx = sum(xs) / n if n else 0.0
            cy = sum(ys) / n if n else 0.0
            key_fn = lambda fid: _math.atan2(_xy(fid)[1] - cy, _xy(fid)[0] - cx) % _math.tau
            return sorted(ids, key=key_fn, reverse=(direction == DIR_CCW))
        if direction == DIR_ALT:
            # 真·奇偶交替：先全部偶数下标(0,2,4…)再全部奇数下标(1,3,5…)。
            return list(ids[0::2]) + list(ids[1::2])
        return list(ids)

    @classmethod
    def reached_fixture_ids(cls, ids_left_to_right: Sequence[str], seg: "TrajectorySegment",
                            f: float, seg_dur: Optional[float] = None,
                            positions: Optional[Dict[str, Tuple[float, float]]] = None) -> List[str]:
        """一个 shape 子组（已按 L→R 排好序）在段内进度 f 时被揭示前沿扫到的设备。

        whole：全部同时。否则：
          1) 按方向定序（ltr/rtl/ftb/btf/ctr/rtc/cw/ccw/alt）；
          2) 翼/块分单元（扫描的空间粒度）；
          3) 分组数 groups 把单元序切成连续批次；
          4) parallel：各批同时在整段窗口内按批内 rank 错峰；
             sequential：批 i 在 offset_i 秒后起，批间插入 group_pause(s) 间隔（需 seg_dur）。
        """
        ids = [str(x) for x in (ids_left_to_right or []) if str(x)]
        if not ids:
            return []
        if seg is None or seg.direction == DIR_WHOLE:
            return list(ids)
        ordered = cls._sort_by_direction(ids, seg.direction, positions)
        units = _wing_adapter.units(ordered, seg)
        if not units:
            return list(ids)
        n = len(units)
        f = _clamp01(f)
        # 落位比例：把扫描压进前 SETTLE，剩余时间所有设备已到位（段末全到达）。
        f_eff = min(1.0, f / _PATH_REVEAL_SETTLE) if _PATH_REVEAL_SETTLE > 1e-6 else 1.0
        batch_of, bc = cls._batch_partition(n, seg.effective_batches())
        batch_units: Dict[int, List[int]] = {}
        for ui, b in enumerate(batch_of):
            batch_units.setdefault(b, []).append(ui)

        reached: List[str] = []

        def _emit_batch(uidxs, local_f):
            m = len(uidxs)
            for li, ui in enumerate(uidxs):
                p = (float(li) / float(m - 1)) if m > 1 else 0.0
                if p <= local_f + 1e-6:
                    reached.extend(str(x) for x in units[ui])

        if seg.group_mode == GROUP_SEQUENTIAL and bc > 1:
            sd = float(seg_dur) if (seg_dur and seg_dur > 0) else 1.0
            # 段末兜底：f_eff 到达 1（含 pauses_sum≥sd 导致 per_group=0 的退化）→ 全部落位。
            if f_eff >= 1.0 - 1e-9:
                return [str(x) for u in units for x in u]
            pauses_sum = sum(seg.group_pauses[b] if b < len(seg.group_pauses) else seg.group_pause
                             for b in range(bc - 1))
            per_group = max(0.0, (sd - pauses_sum) / bc)
            offsets = seg.batch_offsets_seconds(per_group)
            # 顺序模式同样压进 SETTLE：用 f_eff*sd 作为已流逝时间，让末批在段末前落位。
            elapsed = f_eff * sd
            for b, uidxs in batch_units.items():
                off = offsets[b] if b < len(offsets) else 0.0
                if elapsed + 1e-9 < off:
                    continue  # 该批尚未开始（过渡间隔生效）→ 不写，设备保持原值
                if per_group > 1e-9:
                    local_f = min(1.0, (elapsed - off) / per_group)
                else:
                    local_f = 1.0
                _emit_batch(uidxs, local_f)
        else:
            # parallel（或单批）：所有批共享整段进度 f_eff，按各批内 rank 错峰。
            for b, uidxs in batch_units.items():
                _emit_batch(uidxs, f_eff)
        return reached

    def evaluate(self, t: float,
                 resolve_group_segments: Callable[[str, str], List[List[str]]],
                 positions: Optional[Dict[str, Tuple[float, float]]] = None,
                 resolve_default: Optional[Callable[[str, str], float]] = None,
                 ) -> Dict[str, Dict[str, int]]:
        """算出时刻 t 应写出的 {fixture_id: {ch_type: 0-255}}。

        path  → 旧的揭示前沿扫描语义（见 _evaluate_path）。
        shape → 每段一个静态造型 + 边界 morph（见 _evaluate_shape）。
        resolve_default 仅 shape 模式用（首尾淡入淡出的默认值，见 _evaluate_shape）。
        """
        if self.curve_type == CURVE_TYPE_SHAPE:
            return self._evaluate_shape(t, resolve_group_segments, positions, resolve_default)
        return self._evaluate_path(t, resolve_group_segments, positions)

    def _evaluate_path(self, t: float,
                       resolve_group_segments: Callable[[str, str], List[List[str]]],
                       positions: Optional[Dict[str, Tuple[float, float]]] = None,
                       ) -> Dict[str, Dict[str, int]]:
        """路径曲线：全局读数 V=curve(t)，段决定 V 反映在哪些设备上（扫描揭示前沿）。

        未被扫到的设备**不出现在结果里**（调用方据此让其保持原值）。
        resolve_group_segments(group_id, spatial_scope) 须返回**shape 子组列表**，每个子组是
        一条按 L→R 排序的 fixture_id 列表。
        positions: {fixture_id: (x, y)} 用于 ftb/btf/ctr/rtc/cw/ccw 等空间方向排序；不传则
        退化为按索引顺序（等同 ltr/rtl）。
        """
        out: Dict[str, Dict[str, int]] = {}
        val255 = int(round(_clamp01(self.value_at(t)) * 255.0))
        seg_i, seg, f = self.segment_progress_at(t)
        bounds = self.segment_bounds()
        s, e = bounds[seg_i] if seg_i < len(bounds) else (0.0, 1.0)
        seg_dur = (e - s) * float(self.duration)
        for target in (self.targets or []):
            gid = str((target or {}).get("group_id", "") or "")
            chans = [str(c) for c in ((target or {}).get("channel_types", []) or []) if str(c)]
            if not gid or not chans:
                continue
            try:
                shape_segments = resolve_group_segments(gid, seg.spatial_scope) or []
            except Exception:
                shape_segments = []
            for ids in shape_segments:
                for fid in self.reached_fixture_ids(ids, seg, f, seg_dur, positions):
                    slot = out.setdefault(str(fid), {})
                    for ch in chans:
                        slot[ch] = val255
        return out

    # ── 造型曲线（shape）求值 ─────────────────────────────────────────────────
    def _shape_segment_field(self, seg_idx: int, gid: str,
                             resolve_group_segments: Callable[[str, str], List[List[str]]],
                             ) -> Dict[str, float]:
        """一个目标组在「段 seg_idx 造型」下的 {fixture_id: 归一值[0,1]}。

        取段 [s,e] 那截曲线，沿每个 shape 子组的 L→R 空间序平分采样：空间位置 p∈[0,1] 的设备
        取值 = curve(s + p*(e-s))。翼/块用 build_wing_block_units 折成同步单元（同单元同值 →
        多翼重复波形）。用**段自己的** spatial_scope/wings/block（与 morph 的那套分开）。
        """
        bounds = self.segment_bounds()
        if not (0 <= seg_idx < len(bounds)):
            return {}
        s, e = bounds[seg_idx]
        seg = self.segments[seg_idx] if seg_idx < len(self.segments) else TrajectorySegment()
        try:
            shape_segments = resolve_group_segments(gid, seg.spatial_scope) or []
        except Exception:
            shape_segments = []
        field: Dict[str, float] = {}
        for ids in shape_segments:
            ids = [str(x) for x in (ids or []) if str(x)]
            if not ids:
                continue
            units = _wing_adapter.units(ids, seg)
            if not units:
                units = [[x] for x in ids]
            n = len(units)
            for j, unit in enumerate(units):
                p = (float(j) / float(n - 1)) if n > 1 else 0.0
                v = self._eval_phase(s + p * (e - s))
                for fid in unit:
                    field[str(fid)] = v
        return field

    @classmethod
    def _morph_alpha_for_subgroup(cls, ids: Sequence[str], bseg: "TrajectorySegment",
                                  f: float, window_dur: float,
                                  positions: Optional[Dict[str, Tuple[float, float]]] = None,
                                  ) -> Dict[str, float]:
        """边界 morph：返回 {fid: 局部 alpha[0,1]}，按边界 direction/翼块/分组错峰。

        f∈[0,1] 是 morph 窗口整体进度；window_dur 是窗口秒长（顺序分组按秒排批，复刻 path）。
        whole 方向 + 单批 → 全设备同步 alpha=f。否则沿方向定序、翼块折单元，
        每单元在窗口内有一个错峰的起点 + 斜坡，**f=1 时全部 alpha=1**（保证到位）。
        """
        ids = [str(x) for x in (ids or []) if str(x)]
        out: Dict[str, float] = {}
        if not ids:
            return out
        f = _clamp01(f)
        bseg = bseg or TrajectorySegment()
        ordered = cls._sort_by_direction(ids, bseg.direction, positions)
        units = _wing_adapter.units(ordered, bseg)
        if not units:
            units = [[x] for x in ids]
        n = len(units)
        sweep = 0.0 if bseg.direction == DIR_WHOLE else _MORPH_SWEEP_SPREAD
        ramp = max(1e-6, 1.0 - sweep)

        def _unit_alpha(li: int, m: int, local_f: float) -> float:
            p = (float(li) / float(m - 1)) if m > 1 else 0.0
            start_p = p * sweep
            return _clamp01((local_f - start_p) / ramp)

        batch_of, bc = cls._batch_partition(n, bseg.effective_batches())
        batch_units: Dict[int, List[int]] = {}
        for ui, b in enumerate(batch_of):
            batch_units.setdefault(b, []).append(ui)

        if bseg.group_mode == GROUP_SEQUENTIAL and bc > 1:
            # 窗末兜底：f 到达 1（含 pauses_sum≥window_dur 退化）→ 全部 alpha=1 到位。
            if f >= 1.0 - 1e-9:
                for u in units:
                    for fid in u:
                        out[str(fid)] = 1.0
                for fid in ids:
                    out.setdefault(str(fid), 1.0)
                return out
            wd = float(window_dur) if (window_dur and window_dur > 0) else 1.0
            pauses_sum = sum(bseg.group_pauses[b] if b < len(bseg.group_pauses) else bseg.group_pause
                             for b in range(bc - 1))
            per_group = max(0.0, (wd - pauses_sum) / bc)
            offsets = bseg.batch_offsets_seconds(per_group)
            elapsed = f * wd
            for b, uidxs in batch_units.items():
                off = offsets[b] if b < len(offsets) else 0.0
                if elapsed + 1e-9 < off:
                    local_f = 0.0
                elif per_group > 1e-9:
                    local_f = _clamp01((elapsed - off) / per_group)
                else:
                    local_f = 1.0
                m = len(uidxs)
                for li, ui in enumerate(uidxs):
                    a = _unit_alpha(li, m, local_f)
                    for fid in units[ui]:
                        out[str(fid)] = a
        else:
            for b, uidxs in batch_units.items():
                m = len(uidxs)
                for li, ui in enumerate(uidxs):
                    a = _unit_alpha(li, m, f)
                    for fid in units[ui]:
                        out[str(fid)] = a
        # 任何未覆盖的设备（理论不该有）兜底为 f
        for fid in ids:
            out.setdefault(str(fid), f)
        return out

    def shape_boundary_windows(self) -> List[dict]:
        """造型曲线 n+1 个边界的时间窗（秒），保证**互不重叠**且总时长不变。

        每个边界向相邻段「伸入」一段时间（reach）：
          start  idx=0      只向右伸 r0 → 窗 [0, r0]，default→段0。
          internal idx=1..n-1 向两侧各伸 h_i → 窗 [bt-h_i, bt+h_i]，段{i-1}→段{i}。
          end    idx=n      只向左伸 rN → 窗 [D-rN, D]，段{n-1}→default。
        约束（不重叠）：每个段 i 被其左右两边界伸入之和 ≤ 段时长。
          段0:  r0 + h_1 ≤ dur0          段k(中): h_k + h_{k+1} ≤ dur_k
          段n-1: h_{n-1} + rN ≤ dur_{n-1}  单段: r0 + rN ≤ D
        各边界期望 reach = transition/2（内部按对称半窗）或 transition（首尾单侧）；
        若某段两侧期望和超段时长，则按比例同收缩，直至全部满足（只减不增，必收敛）。
        """
        D = float(self.duration) if self.duration else 4.0
        if D <= 0:
            D = 4.0
        edges = [0.0] + list(self.cuts) + [1.0]
        n_seg = len(edges) - 1
        self._sync_boundaries()
        bnds = self.boundaries

        def _b(i):
            return bnds[i] if 0 <= i < len(bnds) else TrajectorySegment()

        seg_dur = [(edges[i + 1] - edges[i]) * D for i in range(n_seg)]
        # 每个边界 b 的「期望伸入量」reach[b]：首尾=transition（单侧），内部=transition/2（对称半窗）。
        reach = [0.0] * (n_seg + 1)
        for b in range(n_seg + 1):
            tau = max(0.0, float(getattr(_b(b), "transition", 0.0) or 0.0))
            reach[b] = tau if (b == 0 or b == n_seg) else tau / 2.0
        # 段 i 的左右边界是 b=i（左）与 b=i+1（右）；其伸入和不得超过 seg_dur[i]。
        # 反复按比例收缩越界段的两侧 reach，直到全部满足。
        for _ in range(64):
            worst = 1.0
            for i in range(n_seg):
                used = reach[i] + reach[i + 1]
                if used > seg_dur[i] + 1e-12 and used > 1e-12:
                    worst = min(worst, seg_dur[i] / used)
            if worst >= 1.0 - 1e-12:
                break
            for b in range(n_seg + 1):
                reach[b] *= worst
        # 二次保险：逐段直接钳（处理浮点残差）。
        for i in range(n_seg):
            over = reach[i] + reach[i + 1] - seg_dur[i]
            if over > 1e-9:
                tot = reach[i] + reach[i + 1]
                if tot > 1e-12:
                    sc = seg_dur[i] / tot
                    reach[i] *= sc
                    reach[i + 1] *= sc

        out: List[dict] = []
        # start
        if reach[0] > 1e-9:
            out.append({"kind": "start", "idx": 0, "t0": 0.0, "t1": reach[0], "bseg": _b(0)})
        # internal
        for i in range(1, n_seg):
            h = reach[i]
            if h > 1e-9:
                bt = edges[i] * D
                out.append({"kind": "internal", "idx": i, "t0": bt - h, "t1": bt + h, "bseg": _b(i)})
        # end
        if n_seg >= 1 and reach[n_seg] > 1e-9:
            out.append({"kind": "end", "idx": n_seg, "t0": D - reach[n_seg], "t1": D, "bseg": _b(n_seg)})
        return out

    def _evaluate_shape(self, t: float,
                        resolve_group_segments: Callable[[str, str], List[List[str]]],
                        positions: Optional[Dict[str, Tuple[float, float]]] = None,
                        resolve_default: Optional[Callable[[str, str], float]] = None,
                        ) -> Dict[str, Dict[str, int]]:
        """造型曲线：段内静态造型；边界窗口内逐设备 staggered morph（按边界方向/分组/翼块）。

        首=各设备默认值淡入到段0造型；尾=段{n-1}造型淡出到默认值；内部=相邻两段造型互morph。
        resolve_default(fid, ch)→归一默认值[0,1]（不传则 0.0）。
        """
        D = float(self.duration) if self.duration else 4.0
        if D <= 0:
            D = 4.0
        t_eff = (t % D) if (self.loop and D > 0) else max(0.0, min(D, float(t)))

        def _def(fid: str, ch: str) -> float:
            if resolve_default is None:
                return 0.0
            try:
                return _clamp01(float(resolve_default(str(fid), str(ch))))
            except Exception:
                return 0.0

        # 命中哪个 morph 窗口？
        active = None
        for win in self.shape_boundary_windows():
            if win["t0"] - 1e-9 <= t_eff <= win["t1"] + 1e-9 and win["t1"] > win["t0"]:
                active = win
                break

        out: Dict[str, Dict[str, int]] = {}
        targets = [(str((tg or {}).get("group_id", "") or ""),
                    [str(c) for c in ((tg or {}).get("channel_types", []) or []) if str(c)])
                   for tg in (self.targets or [])]
        targets = [(g, c) for g, c in targets if g and c]

        if active is None:
            # 静态段：定位 t_eff 落在哪段，输出该段造型。
            bounds = self.segment_bounds()
            seg_idx = 0
            for i, (s, e) in enumerate(bounds):
                if (s * D <= t_eff < e * D) or (i == len(bounds) - 1 and t_eff >= s * D):
                    seg_idx = i
                    break
            for gid, chans in targets:
                field = self._shape_segment_field(seg_idx, gid, resolve_group_segments)
                for fid, v in field.items():
                    val255 = int(round(_clamp01(v) * 255.0))
                    slot = out.setdefault(str(fid), {})
                    for ch in chans:
                        slot[ch] = val255
            return out

        # morph 窗口：from_field → to_field，逐设备 alpha 错峰。
        kind = active["kind"]; bidx = active["idx"]; bseg = active["bseg"]
        w_dur = max(1e-6, float(active["t1"]) - float(active["t0"]))
        f = _clamp01((t_eff - float(active["t0"])) / w_dur)

        for gid, chans in targets:
            # morph 子组用**边界自己的** scope；造型 field 用**段自己的** scope（在 _shape_segment_field 内）。
            try:
                morph_subgroups = resolve_group_segments(gid, bseg.spatial_scope) or []
            except Exception:
                morph_subgroups = []
            alpha_map: Dict[str, float] = {}
            for ids in morph_subgroups:
                alpha_map.update(self._morph_alpha_for_subgroup(ids, bseg, f, w_dur, positions))

            if kind == "start":
                to_field = self._shape_segment_field(0, gid, resolve_group_segments)
                from_field = None  # 用默认值
            elif kind == "end":
                from_field = self._shape_segment_field(bidx - 1, gid, resolve_group_segments)
                to_field = None    # 用默认值
            else:  # internal
                from_field = self._shape_segment_field(bidx - 1, gid, resolve_group_segments)
                to_field = self._shape_segment_field(bidx, gid, resolve_group_segments)

            fids = set(alpha_map.keys())
            if from_field is not None:
                fids |= set(from_field.keys())
            if to_field is not None:
                fids |= set(to_field.keys())
            for fid in fids:
                a = alpha_map.get(fid, f)
                slot = out.setdefault(str(fid), {})
                for ch in chans:
                    fv = from_field.get(fid, 0.0) if from_field is not None else _def(fid, ch)
                    tv = to_field.get(fid, 0.0) if to_field is not None else _def(fid, ch)
                    v = fv + (tv - fv) * a
                    slot[ch] = int(round(_clamp01(v) * 255.0))
        return out

    # ── 序列化 ───────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "curve_type": self.curve_type,
            "duration": float(self.duration),
            "source": self.source,
            "cuts": [float(c) for c in self.cuts],
            "segments": [s.to_dict() for s in self.segments],
            "boundaries": [b.to_dict() for b in self.boundaries],
            "targets": [
                {"group_id": str(t.get("group_id", "")),
                 "channel_types": [str(c) for c in (t.get("channel_types", []) or [])]}
                for t in (self.targets or [])
            ],
            "loop": bool(self.loop),
            "created_at": float(self.created_at),
        }
        if self.curve is not None and hasattr(self.curve, "to_dict"):
            d["curve"] = self.curve.to_dict()
        else:
            d["points"] = [[float(x), float(y)] for x, y in (self.points or [])]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TrajectoryCurve":
        d = dict(d or {})
        curve = None
        pts: List[List[float]] = []
        if d.get("curve") and CurveMapping is not None:
            try:
                curve = CurveMapping.from_dict(d["curve"])
            except Exception:
                curve = None
        if curve is None:
            for p in (d.get("points", []) or []):
                try:
                    pts.append([float(p[0]), float(p[1])])
                except Exception:
                    continue
        segs = [TrajectorySegment.from_dict(s) for s in (d.get("segments", []) or [])]
        bnds = [TrajectorySegment.from_dict(b) for b in (d.get("boundaries", []) or [])]
        return cls(
            id=str(d.get("id") or ("traj_" + uuid.uuid4().hex[:12])),
            name=str(d.get("name", "轨迹")),
            curve_type=str(d.get("curve_type", CURVE_TYPE_PATH)),
            duration=float(d.get("duration", 4.0) or 4.0),
            source=str(d.get("source", "manual")),
            curve=curve,
            points=pts,
            cuts=[float(c) for c in (d.get("cuts", []) or [])],
            segments=segs,
            boundaries=bnds,
            targets=[
                {"group_id": str(t.get("group_id", "")),
                 "channel_types": [str(c) for c in (t.get("channel_types", []) or [])]}
                for t in (d.get("targets", []) or []) if isinstance(t, dict)
            ],
            loop=bool(d.get("loop", False)),
            created_at=float(d.get("created_at", time.time()) or time.time()),
        )


class TrajectoryCurveStore:
    """轨迹曲线仓库（随项目序列化）。"""

    def __init__(self):
        self._curves: Dict[str, TrajectoryCurve] = {}

    def add(self, traj: TrajectoryCurve) -> TrajectoryCurve:
        if traj is None:
            return traj
        if not traj.id:
            traj.id = "traj_" + uuid.uuid4().hex[:12]
        self._curves[traj.id] = traj
        return traj

    def create(self, name: str = "轨迹", duration: float = 4.0,
               source: str = "manual", curve_type: str = CURVE_TYPE_PATH) -> TrajectoryCurve:
        return self.add(TrajectoryCurve(name=name, duration=duration, source=source,
                                        curve_type=curve_type))

    def remove(self, traj_id: str) -> None:
        self._curves.pop(traj_id, None)

    def rename(self, traj_id: str, new_name: str) -> None:
        t = self._curves.get(traj_id)
        if t is not None:
            t.name = str(new_name or t.name)

    def get(self, traj_id: str) -> Optional[TrajectoryCurve]:
        return self._curves.get(traj_id)

    def all(self) -> List[TrajectoryCurve]:
        return sorted(self._curves.values(), key=lambda t: t.created_at)

    def to_dict(self) -> dict:
        return {"version": 1, "curves": [t.to_dict() for t in self.all()]}

    def from_dict(self, d: Optional[dict]) -> None:
        d = dict(d or {})
        self._curves.clear()
        for cd in (d.get("curves", []) or []):
            try:
                t = TrajectoryCurve.from_dict(cd)
                self._curves[t.id] = t
            except Exception:
                continue

    def clear(self) -> None:
        self._curves.clear()


_store_instance: Optional[TrajectoryCurveStore] = None


def get_trajectory_curve_store() -> TrajectoryCurveStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = TrajectoryCurveStore()
    return _store_instance
