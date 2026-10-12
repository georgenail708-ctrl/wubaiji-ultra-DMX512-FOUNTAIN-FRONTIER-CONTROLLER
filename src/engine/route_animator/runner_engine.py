"""route_animator.runner_engine — 跑火车引擎 v2 (图模型 + 完整车厢物理 + 道岔语义)
 ======================================================================

 替代旧 cue/keyframe 编排链(engine.py)与 v1 单轨车队引擎, 提供「跑火车页」的
 真图模型运行态:

   * 图: 节点 = 道岔/端点; 边 = 轨道段(折线, 弧长表 + 设备投影缓存)。
     每边一条 CE path(复用 ce_route_coverage 投影机制), 页设备集变化时同步。
   * 火车 = 有序车厢组; 每车厢状态 = (所在边, 沿边弧长 s, 朝向)。车头在前,
     车厢依次跟随(定距 = 车长); 每帧 s += base_speed*throttle*dt(世界单位/秒)。
   * 道岔语义(命令节点): split 分流 / merge 汇入保序 / uncouple 解挂 /
     couple 连挂。
   * 覆盖激活: 每车厢按绝对弧区间 [s-car_length, s] + 半径圈覆盖当前页设备,
     强度 = max(255*(1-d/radius), 1)(语义与 v1/CE 契约逐字一致)。

 覆盖语义同时是本模块对 CE 内核 ce_route_coverage 的 Python 参考实现:
   * 车厢 j 覆盖弧区间 = [nose_s-(j+1)*car_unit, nose_s-j*car_unit] (绝对弧区间,
     可为负, 不做 per-car mod)
   * 设备激活当 投影弧长 s 落在任一区间 且 横向距离 d <= radius
   * 强度 = max(255*(1-d/radius), 1) 取 max over cars; 未覆盖 = 0

 CE 门控: QASCADE_CE_ROUTE 默认 "1"; tick 时若 qascade_ce 暴露 ce_route_coverage
 则走 C(首帧/几何变化传全几何同步, 之后只传车厢复用已同步投影), 否则 numpy
 向量化回退; 两种路径输出数组语义逐位一致。

 向后兼容: 保留 v1 的 set_device_positions / add_train(旧签名) / set_path /
 _trains 属性(nose_s/path_points/path_length/car_unit/proj_s ...) 与
 _rebuild_projection, 供旧 runner_panel 与既有测试平滑迁移。
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field

import numpy as np

from .easing import easing_value
# 几何纯函数已搬到 track_geom（R183 W2：本文件超基线，新增 R8/R13 需先腾行数）。
# 名字在本模块仍可见（旧调用方/测试零迁移）；函数体逐行未改。
from .track_geom import (  # noqa: F401  (向后兼容再导出)
    _LOOP_MARKER_CAP,
    _detect_self_intersections,
    _point_at_arclen,
    _polyline_cumlen,
    _segment_intersection_t,
    _sub_polyline,
    _tangent_angle,
    _tangent_at_arclen,
    _wrap_spans,
)

logger = logging.getLogger(__name__)

_SWITCH_TYPES = ("split", "merge", "uncouple", "couple")
_TRAIN_MODES = ("loop_forward", "loop_reverse", "bounce",
                "one_way_forward", "one_way_reverse")
_PROJ_CHUNK = 128      # 投影向量化分块段数(内存 ~ chunk*devices*8B)


@dataclass
class FrameResult:
    """tick(dt) 的一帧输出。

    nose_s : 每火车车头弧长(火车插入序)
    car_positions : 每火车的每车厢中心世界坐标(火车插入序)
    car_angles : 每火车的每车厢切线朝向角(弧度, 供 UI 朝向)
    coverage : 每设备覆盖强度 uint8 数组(索引 = 页设备注册序)
    coverage_radius_list : 每火车的每车厢判定半径(与可见圈同源)
    active_mask : 每设备激活布尔掩码(coverage >= 1)
    coverage_strength : 稀疏 {fid: 0..1} 软边强度(仅 strength>0, 向后兼容)
    active_ordered_ids : 被覆盖设备 id 列表(on/off 语义, 仅变化时更新, 向后兼容)
    train_ids : 本帧存活车列 id(含解挂分裂出的车队), 面板据此同步卡片(T4)
    """

    nose_s: list[float] = field(default_factory=list)
    car_positions: list[list[tuple[float, float]]] = field(default_factory=list)
    car_angles: list[list[float]] = field(default_factory=list)
    coverage: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.uint8))
    coverage_radius_list: list[list[float]] = field(default_factory=list)
    active_mask: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=bool))
    coverage_strength: dict[str, float] = field(default_factory=dict)
    active_ordered_ids: list[str] = field(default_factory=list)
    train_ids: list[str] = field(default_factory=list)


class _Car:
    """单节车厢运行态: 所在边 + 车头(鼻端)弧长 + 朝向 + 判定半径 + 车长。"""

    __slots__ = ("edge_id", "s", "direction", "radius", "car_length")

    def __init__(self, edge_id: str, s: float = 0.0, direction: int = 1,
                 radius: float = 0.0, car_length: float = 0.0) -> None:
        self.edge_id = edge_id
        self.s = float(s)
        self.direction = int(direction)
        self.radius = float(radius)
        self.car_length = float(car_length)


class _Edge:
    """轨道边(折线) + 弧长表 + 设备投影缓存 + 拓扑端点。"""

    __slots__ = ("edge_id", "points", "cum", "length", "end_node",
                 "start_node",
                 "loop", "loop_markers", "closed", "lap_count",
                 "proj_s", "proj_d", "s_order", "s_sorted", "d_sorted")

    def __init__(self, edge_id: str, points: list[tuple[float, float]]) -> None:
        self.edge_id = edge_id
        self.points = [tuple(map(float, p)) for p in points]
        self.cum = _polyline_cumlen(self.points)
        self.length = self.cum[-1] if self.cum else 0.0
        # 闭环: 首点精确等于末点(由 UI 侧 smooth_track 显式追加, 引擎不做容差猜测)
        self.closed = (len(self.points) >= 3
                       and self.points[0] == self.points[-1])
        self.end_node: str | None = None
        self.start_node: str | None = None  # 边起点处的道岔节点(切分出的出段)
        self.loop = False
        self.loop_markers: list[dict] = []
        # 绕圈折叠后的圈数(T7): 只影响显示(xN 标签), 不影响覆盖
        self.lap_count = 1
        self.proj_s: np.ndarray | None = None
        self.proj_d: np.ndarray | None = None
        self.s_order: np.ndarray | None = None
        self.s_sorted: np.ndarray | None = None
        self.d_sorted: np.ndarray | None = None


class _Node:
    """道岔/端点节点(命令节点)。"""

    __slots__ = ("node_id", "type", "in_edges", "out_edges", "params",
                 "_arrival", "_held_train", "_route_cache")

    def __init__(self, node_id: str, ntype: str) -> None:
        self.node_id = node_id
        self.type = ntype
        self.in_edges: list[str] = []
        self.out_edges: list[str] = []
        self.params: dict = {}
        self._arrival: list[str] = []
        self._held_train: str | None = None
        self._route_cache: dict[str, str | None] = {}


class _Train:
    """火车 = 有序车厢组 + 油门/基速/颜色(向后兼容 v1 路径别名字段)。"""

    __slots__ = ("train_id", "base_speed", "throttle", "color",
                 "car_count", "car_length", "chain_length", "chain_lengths", "radius",
                 "mode", "dir_sign", "ended", "skip_gaps", "_attracted",
                 "cars", "held_node",
                 "trail",
                 "_home_edge_id", "_nose_s",
                 "path_points", "path_length", "car_unit", "cum",
                 "proj_s", "proj_d", "s_order", "s_sorted", "d_sorted",
                 # R1 油门斜坡: 目标值 + 缓动逼近状态（ramp_sec<=0 = 立即生效）
                 "throttle_target", "ramp_sec", "ramp_from", "ramp_elapsed",
                 "ramp_easing",
                 # R6 错峰出发: 剩余延迟秒数, 每帧 tick 消耗, >0 时本车不推进
                 "start_delay",
                 # R8 A-B 段循环: loop_span=(s0,s1) 与所在边 id（None=不启用）
                 "loop_span", "loop_span_edge")

    def __init__(self, train_id: str, car_count: int, car_length: float,
                 radius: float, base_speed: float, color: str) -> None:
        self.train_id = str(train_id)
        self.base_speed = float(base_speed)
        self.throttle = 1.0
        # R1 油门斜坡状态（默认零斜坡 = set_throttle 立即生效, 原行为不变）
        self.throttle_target = 1.0
        self.ramp_sec = 0.0
        self.ramp_from = 1.0
        self.ramp_elapsed = 0.0
        self.ramp_easing = "OutCubic"
        # R6 错峰出发延迟（秒）
        self.start_delay = 0.0
        # R8 A-B 段循环（默认关 = 原整轨行为不变）
        self.loop_span: tuple[float, float] | None = None
        self.loop_span_edge: str | None = None
        self.color = color
        self.car_count = max(0, int(car_count))
        self.car_length = float(car_length)
        # 锁链长度 = 相邻车厢(珠子)间距; 与车长(珠子大小/覆盖区间)独立可调
        self.chain_length = float(car_length)
        # 每根锁链独立长度(长度=车厢数-1); None=全部使用 chain_length 统一值
        self.chain_lengths: list[float] | None = None
        self.radius = float(radius)
        # 运行模式: loop_forward/loop_reverse/bounce/one_way_forward/one_way_reverse
        self.mode = "loop_forward"
        self.dir_sign = 1.0      # 当前运动方向(返程模式会翻转)
        self.ended = False       # 单程模式跑到底后停住
        self.skip_gaps = False   # 跳过连接处(设备空余段)开关
        self._attracted = False   # 连挂磁吸动画中(油门让位于吸附)
        self.cars: list[_Car] = []
        # trail[0] = 车头所在边; 其后依次是车列尾部仍占用的更早的边
        self.trail: list[str] = []
        self.held_node: str | None = None
        self._home_edge_id: str | None = None
        # 向后兼容别名(由 _sync_train_legacy 从 home edge 同步)
        self._nose_s = 0.0
        self.path_points: list[tuple[float, float]] = []
        self.path_length = 0.0
        self.car_unit = float(car_length)
        self.cum: list[float] = []
        self.proj_s = None
        self.proj_d = None
        self.s_order = None
        self.s_sorted = None
        self.d_sorted = None

    @property
    def nose_s(self) -> float:
        """车头弧长(向后兼容); 写操作会把整列车厢重排到该弧长(定距跟随)。"""
        return self.cars[0].s if self.cars else self._nose_s

    def chain_back(self, j: int) -> float:
        """第 j 节车厢相对车头的回溯弧长(前 j 根锁链长度之和)。"""
        if self.chain_lengths and len(self.chain_lengths) >= j:
            return sum(self.chain_lengths[:j])
        return j * self.chain_length

    @nose_s.setter
    def nose_s(self, v: float) -> None:
        self._nose_s = float(v)
        if not self.cars:
            return
        # v1 兼容路径: 测试用瞬移 API, 把整列车厢重排到车头所在边(语义保持原样)
        self.trail = [self.cars[0].edge_id]
        head = self.cars[0]
        head.s = float(v)
        for j in range(1, len(self.cars)):
            c = self.cars[j]
            c.edge_id = head.edge_id
            c.s = float(v) - self.chain_back(j)


class RouteRunnerEngine:
    """跑火车引擎 v2: 轨道图 + 车厢物理 + 道岔语义 + 覆盖激活。"""

    _TRAIL_CAP = 64  # 防止 trail 无限增长
    _couple_attract = 60.0   # 连挂磁吸半径(世界单位)
    _couple_pull_speed = 90.0  # 磁吸拉近速度(世界单位/秒, 丝滑动画)
    _couple_link_dist = 8.0    # 全部到齐(到节点弧距≤此值)即完成链接

    def __init__(self) -> None:
        self._device_ids: list[str] = []
        self._device_pos = np.zeros((0, 2), dtype=np.float64)
        self._id_to_index: dict[str, int] = {}
        self._edges: dict[str, _Edge] = {}
        self._nodes: dict[str, _Node] = {}
        self._trains: dict[str, _Train] = {}
        self._coverage = np.zeros(0, dtype=np.uint8)
        self._active_ordered_ids: list[str] = []
        self._active_sig: bytes = b""
        self._mask_sig: bytes = b""
        self._route_mask_map_cache: dict[str, int] | None = None
        self._coverage_work = np.zeros(0, dtype=np.float64)
        self._order_work = np.zeros(0, dtype=np.float64)
        self._ce_env: bool | None = None
        self._ce_route_fn = None
        self._ce_checked = False
        self._ce_ctx_dirty = True
        self._owner_map: np.ndarray | None = None
        self._gap_cache: dict[tuple, list[tuple[float, float]]] = {}
        self._edge_index: dict[str, int] = {}

    # ------------------------------------------------------------------
    #  设备注册
    # ------------------------------------------------------------------

    @property
    def _num_devices(self) -> int:
        return len(self._device_ids)

    @staticmethod
    def _legacy_track_id(train_id: str) -> str:
        return f"__legacy_track__{train_id}"

    def set_page_devices(self, positions: dict) -> None:
        """注册页设备位置, 建立 id -> index(注册序)映射(契约 API)。"""
        self._set_device_positions_impl(positions)

    def set_device_positions(self, positions: dict) -> None:
        """向后兼容别名(旧 runner_panel / v1 测试)。"""
        self._set_device_positions_impl(positions)

    def _set_device_positions_impl(self, positions: dict) -> None:
        ids: list[str] = []
        pts: list[tuple[float, float]] = []
        for did, xy in (positions or {}).items():
            if isinstance(xy, (tuple, list)) and len(xy) >= 2:
                ids.append(str(did))
                pts.append((float(xy[0]), float(xy[1])))
        self._device_ids = ids
        self._id_to_index = {did: i for i, did in enumerate(ids)}
        self._device_pos = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        for edge in self._edges.values():
            edge.proj_s = None
            edge.proj_d = None
        self._owner_map = None
        self._reset_frame_cache()
        self._ce_ctx_dirty = True
        self._gap_cache = {}

    # ------------------------------------------------------------------
    #  轨道(图边)
    # ------------------------------------------------------------------

    def add_track(self, track_id, points, lap_count: int = 1) -> str:
        """建立轨道边并做自相交检测; 自交 → 记录绕圈标记(loop 节点), 返回 track_id。

        绕圈标记可经 get_loop_markers / loop_markers 读取, 供 UI 画绕圈图标。
        lap_count 只影响显示(绕圈 xN 标签), 不影响覆盖(覆盖仍按单圈弧长算,
        火车自然转 N 圈)。
        """
        tid = str(track_id)
        pts = [tuple(map(float, p)) for p in (points or [])]
        edge = _Edge(tid, pts)
        edge.lap_count = max(1, int(lap_count))
        edge.loop_markers = _detect_self_intersections(pts)
        edge.loop = bool(edge.loop_markers)
        self._edges[tid] = edge
        self._owner_map = None
        self._gap_cache = {}
        self._ce_ctx_dirty = True
        return tid

    @property
    def loop_markers(self) -> dict[str, list[dict]]:
        """所有自交轨道的绕圈标记 {track_id: [marker, ...]}。"""
        return {eid: list(e.loop_markers)
                for eid, e in self._edges.items() if e.loop}

    def get_loop_markers(self, track_id=None) -> list[dict]:
        """读取绕圈标记; 无 track_id 返回全部(扁平列表), 否则返回该轨道标记。"""
        if track_id is None:
            out: list[dict] = []
            for e in self._edges.values():
                out.extend(e.loop_markers)
            return out
        edge = self._edges.get(str(track_id))
        return list(edge.loop_markers) if edge else []

    # ------------------------------------------------------------------
    #  火车管理
    # ------------------------------------------------------------------

    def add_train(self, *args, **kwargs) -> str:
        """新增火车(兼容 v1/v2 两套签名)。

        v2(契约): add_train(train_id, car_count, car_length, radius, base_speed, color)
        v1(旧):   add_train(train_id, path_points, car_count, car_unit, radius, base_speed, color)
        """
        if len(args) >= 2 and isinstance(args[1], (list, tuple)):
            return self._add_train_legacy(*args, **kwargs)
        return self._add_train_v2(*args, **kwargs)

    def _add_train_v2(self, train_id, car_count: int = 1, car_length: float = 0.0,
                      radius: float = 0.0, base_speed: float = 1.0,
                      color: str = "#FFB86C") -> str:
        tid = str(train_id)
        self._trains[tid] = _Train(
            tid, int(car_count), float(car_length), float(radius),
            float(base_speed), str(color))
        self._ce_ctx_dirty = True
        return tid

    def _add_train_legacy(self, train_id, path_points, car_count: int = 1,
                          car_unit: float = 0.0, radius: float = 0.0,
                          base_speed: float = 1.0, color: str = "#FFB86C",
                          **kwargs) -> str:
        tid = str(train_id)
        car_unit = float(car_unit or 0.0)
        car_length = car_unit if car_unit > 0.0 else 0.0
        # v1 base_speed 单位 = car_unit/秒 → 转世界单位/秒
        world_speed = float(base_speed) * car_unit if car_unit > 0.0 else float(base_speed)
        self._add_train_v2(tid, int(car_count), car_length, float(radius),
                           world_speed, str(color))
        pts = [tuple(map(float, p)) for p in (path_points or [])]
        if pts:
            self._ensure_legacy_track(tid, pts)
            self.attach_train_to_track(tid, self._legacy_track_id(tid), 0.0)
        else:
            self._ensure_legacy_track(tid, [])
        return tid

    def _ensure_legacy_track(self, train_id: str, points) -> str:
        eid = self._legacy_track_id(str(train_id))
        pts = [tuple(map(float, p)) for p in (points or [])]
        edge = _Edge(eid, pts)
        edge.loop_markers = _detect_self_intersections(pts)
        edge.loop = bool(edge.loop_markers)
        self._edges[eid] = edge
        self._owner_map = None
        self._gap_cache = {}
        return eid

    def attach_train_to_track(self, train_id, track_id, s0: float = 0.0) -> None:
        """把火车挂到轨道; 车头鼻端置于弧长 s0, 车厢依次按车长跟随。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        edge = self._edges.get(str(track_id))
        if edge is None:
            return
        tr.cars = []
        if tr.chain_lengths is not None and len(tr.chain_lengths) != max(0, tr.car_count - 1):
            tr.chain_lengths = None
        for j in range(tr.car_count):
            tr.cars.append(_Car(edge.edge_id, float(s0) - tr.chain_back(j),
                                radius=tr.radius, car_length=tr.car_length))
        tr.trail = [str(track_id)]
        tr.ended = False
        tr._attracted = False
        tr.dir_sign = -1.0 if tr.mode in ("loop_reverse", "one_way_reverse") else 1.0
        tr._home_edge_id = edge.edge_id
        tr.held_node = None
        self._sync_train_legacy(tr)
        self._ce_ctx_dirty = True

    def set_path(self, train_id, points) -> None:
        """向后兼容: 替换旧式单轨路径(保持当前 nose_s)。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        tid = str(train_id)
        pts = [tuple(map(float, p)) for p in (points or [])]
        self._ensure_legacy_track(tid, pts)
        eid = self._legacy_track_id(tid)
        if tr._home_edge_id is None or not tr.cars:
            self.attach_train_to_track(tid, eid, 0.0)
            return
        edge = self._edges.get(tr._home_edge_id)
        if edge is not None:
            edge.points = pts
            edge.cum = _polyline_cumlen(pts)
            edge.length = edge.cum[-1] if edge.cum else 0.0
            edge.closed = (len(pts) >= 3 and pts[0] == pts[-1])
            edge.loop_markers = _detect_self_intersections(pts)
            edge.loop = bool(edge.loop_markers)
            edge.proj_s = None
            edge.proj_d = None
            edge.s_order = None
            edge.s_sorted = None
            edge.d_sorted = None
            self._owner_map = None
            self._gap_cache = {}
        s0 = tr.nose_s
        for j, c in enumerate(tr.cars):
            c.edge_id = tr._home_edge_id
            c.s = s0 - j * tr.car_length
        self._sync_train_legacy(tr)
        self._ce_ctx_dirty = True

    def remove_train(self, train_id) -> None:
        """移除火车; 不存在则静默忽略。"""
        tid = str(train_id)
        tr = self._trains.pop(tid, None)
        if tr is not None:
            self._edges.pop(self._legacy_track_id(tid), None)
            for node in self._nodes.values():
                node._route_cache.pop(tid, None)
                node._arrival = [x for x in node._arrival if x != tid]
        self._owner_map = None
        self._ce_ctx_dirty = True
        self._gap_cache = {}

    def set_throttle(self, train_id, throttle: float,
                     ramp_sec: float | None = None,
                     easing: str | None = None) -> None:
        """设置某车油门, 钳制到 [0.0, 1.0]。

        R1 油门斜坡: ``ramp_sec > 0`` 时目标值经缓动曲线（``easing`` 名,
        见 easing.py）在 ramp_sec 秒内逼近, 每帧 tick 推进;
        ``ramp_sec=None`` 沿用该车现有斜坡设置; ``ramp_sec<=0`` 立即生效
        （与旧行为一致）。物理灯/泵最怕速度阶跃——斜坡默认关, 面板按需开。
        """
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        target = max(0.0, min(1.0, float(throttle)))
        if easing:
            tr.ramp_easing = str(easing)
        if ramp_sec is not None:
            tr.ramp_sec = max(0.0, float(ramp_sec))
        if tr.ramp_sec <= 0.0 or abs(target - tr.throttle) < 1e-12:
            tr.throttle = target
            tr.throttle_target = target
            tr.ramp_from = target
            tr.ramp_elapsed = 0.0
            return
        tr.throttle_target = target
        tr.ramp_from = float(tr.throttle)
        tr.ramp_elapsed = 0.0

    def set_train_start_delay(self, train_id, delay_sec: float) -> None:
        """R6 错峰出发: 设置该车剩余出发延迟（秒）; <=0 立即出发。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        tr.start_delay = max(0.0, float(delay_sec))

    # ------------------------------------------------------------------
    #  R8 单轨 A-B 段循环
    # ------------------------------------------------------------------

    def set_train_loop_span(self, train_id, s0, s1) -> bool:
        """R8: 让某车在轨道弧区间 [s0,s1] 内循环（缺省/非法值 = 关闭）。

        区间按 ``_wrap_spans`` 开链语义夹到所在边 [0,length]；``s1<=s0`` 或边
        长度不足 → 关闭并返回 False。关闭后行为与整轨模式逐字一致（零回归）。
        """
        tr = self._trains.get(str(train_id))
        if tr is None:
            return False
        edge = self._edges.get(self._loop_span_edge_id(tr))
        if edge is None or edge.length <= 0.0:
            return False
        spans = _wrap_spans(float(s0 or 0.0), float(s1 or 0.0),
                            edge.length, False)
        if not spans or spans[0][1] - spans[0][0] <= 1e-9:
            self.clear_train_loop_span(train_id)
            return False
        tr.loop_span = (float(spans[0][0]), float(spans[0][1]))
        tr.loop_span_edge = edge.edge_id
        tr.ended = False
        return True

    def clear_train_loop_span(self, train_id) -> None:
        """R8: 关闭某车的 A-B 段循环（恢复整轨运行）。"""
        tr = self._trains.get(str(train_id))
        if tr is not None:
            tr.loop_span = None
            tr.loop_span_edge = None

    @staticmethod
    def _loop_span_edge_id(tr: _Train) -> str:
        """A-B 段所在边 id（优先记下的边，缺省用车头当前边）。"""
        if tr.loop_span_edge:
            return str(tr.loop_span_edge)
        return str(tr.cars[0].edge_id) if tr.cars else ""

    def _step_loop_span(self, tr: _Train, step: float) -> bool:
        """R8 段内推进：车头越界回绕另一端；返回 True 表示本帧已由段循环消化。

        单程模式（one_way_*）不循环——到段端停住（``ended=True``）= "出段自动停"。
        仅当车头正在记下的那条边上才接管；否则返回 False 走原整轨推进
        （过岔/换轨后本段循环自然失效，不会跨轨误绕）。
        """
        span = tr.loop_span
        if not span or not tr.cars:
            return False
        eid = tr.loop_span_edge or tr.cars[0].edge_id
        edge = self._edges.get(eid)
        head = tr.cars[0]
        if edge is None or edge.length <= 0.0 or head.edge_id != edge.edge_id:
            return False
        s0, s1 = float(span[0]), float(span[1])
        if s1 - s0 <= 1e-9:
            return False
        target = head.s + step
        head.s = self._span_target(tr, target, s0, s1)
        self._place_cars(tr)
        self._wrap_cars_into_span(tr, (eid, s0, s1, s1 - s0))
        return True

    @staticmethod
    def _span_target(tr: _Train, target: float, s0: float, s1: float) -> float:
        """段内推进目标：未越界原值；越界时单程停在段端（置 ended）/ 循环回绕。"""
        if s0 <= target <= s1:
            return target
        if tr.mode in ("one_way_forward", "one_way_reverse"):
            tr.ended = True
            return s1 if target > s1 else s0
        span_len = s1 - s0
        if target > s1:
            return s0 + (target - s0) % span_len
        return s1 - (s0 - target) % span_len

    def _wrap_cars_into_span(self, tr: _Train, span) -> None:
        """把回绕后落在段外的跟随车厢投影回 [s0,s1]（车尾从另一端接上）。

        `span` = (edge_id, s0, s1, span_len) 四元组（位置参数门禁 ≤5）。
        """
        eid, s0, s1, span_len = span
        for c in tr.cars:
            if c.edge_id == eid and (c.s < s0 or c.s > s1):
                c.s = s0 + (c.s - s0) % span_len

    def _advance_throttle_ramp(self, tr: _Train, dt: float) -> None:
        """单车油门斜坡推进: current 向 target 按缓动曲线逼近（R1）。"""
        target = tr.throttle_target
        if tr.ramp_sec <= 0.0 or abs(target - tr.throttle) < 1e-12:
            return
        if dt <= 0.0:
            return
        tr.ramp_elapsed = min(tr.ramp_sec, tr.ramp_elapsed + dt)
        p = tr.ramp_elapsed / tr.ramp_sec
        eased = easing_value(tr.ramp_easing, p)
        # Back/Elastic 族按定义过冲（负值/超 1）——油门必须钳到 [0,1]，
        # 否则倒车或超基速（`step = base_speed × throttle`）。
        value = tr.ramp_from + (target - tr.ramp_from) * eased
        tr.throttle = max(0.0, min(1.0, value))
        if p >= 1.0:
            tr.throttle = target

    def set_train_mode(self, train_id, mode: str) -> None:
        """实时切换运行模式(循环/返程/正跑/反跑), 立即生效。"""
        tr = self._trains.get(str(train_id))
        if tr is None or mode not in _TRAIN_MODES:
            return
        tr.mode = mode
        tr.dir_sign = -1.0 if mode in ("loop_reverse", "one_way_reverse") else 1.0
        tr.ended = False   # 换模式即解除单程到站停住

    def detach_train_cars(self, train_id, car_index: int) -> str | None:
        """把车厢 car_index(含)之后解挂成独立子列车(链条断在此处)。

        后段保持当前位置(s/edge 不变)与朝向语义, 视觉上位置连续、链断开;
        继承母列车颜色/基速/链长/模式/油门, 可再单独控制。
        """
        tr = self._trains.get(str(train_id))
        if tr is None or not (0 < int(car_index) < len(tr.cars)):
            return None
        k = int(car_index)
        n = 0
        new_id = f"{tr.train_id}#detach#{k}#{n}"
        while new_id in self._trains:
            n += 1
            new_id = f"{tr.train_id}#detach#{k}#{n}"
        rear_cars = tr.cars[k:]
        tr.cars = tr.cars[:k]
        tr.car_count = k
        rear = _Train(new_id, len(rear_cars), tr.car_length, tr.radius,
                      tr.base_speed, tr.color)
        rear.throttle = tr.throttle
        # R1/R6: 斜坡与错峰状态随解挂继承（后段与母车运动语义一致）
        rear.throttle_target = tr.throttle_target
        rear.ramp_sec = tr.ramp_sec
        rear.ramp_from = tr.ramp_from
        rear.ramp_elapsed = tr.ramp_elapsed
        rear.ramp_easing = tr.ramp_easing
        rear.start_delay = tr.start_delay
        rear.chain_length = tr.chain_length
        rear.mode = tr.mode
        rear.dir_sign = tr.dir_sign
        rear.skip_gaps = tr.skip_gaps
        rear.cars = rear_cars
        # 后段 trail = 原 trail 自后段头所在边起的后缀(车头→车尾顺序不变)
        rear_head_edge = rear_cars[0].edge_id
        trail = list(tr.trail)
        if rear_head_edge in trail:
            sub_trail = trail[trail.index(rear_head_edge):]
        else:
            sub_trail = [rear_head_edge]
        rear.trail = sub_trail or [rear_head_edge]
        # 继承母车 home edge，供 _rebuild_engine / _reset_trains 重新挂轨
        rear._home_edge_id = tr._home_edge_id
        self._trains[new_id] = rear
        self._ce_ctx_dirty = True
        return new_id

    def merge_trains(self, front_id, rear_id) -> bool:
        """R13 解挂回并：把后段 ``rear_id`` 并回前段 ``front_id`` 的车尾。

        与 :meth:`detach_train_cars` 互为逆操作：后段车厢按前段尾车所在边与
        链距**尾部对齐**（``nose_s`` 与 ``edge`` 由前段决定），trail 顺接
        前段来路，于是归队后车列连续；后段从 ``_trains`` 摘除（含道岔路由
        缓存清理），前段车厢数/链长表随之合并。返回是否真合并。

        调用方（面板）负责挑前/后段；引擎不猜顺序（解挂子列 id 含 ``#``）。
        """
        front = self._trains.get(str(front_id))
        rear = self._trains.get(str(rear_id))
        if front is None or rear is None or front is rear:
            return False
        if not front.cars or not rear.cars:
            return False
        n_front = len(front.cars)
        # 尾部对齐: 后段首车挂到前段尾车之后一根链距处（边 = 前段尾车所在边）
        tail = front.cars[-1]
        rear.cars[0].edge_id = tail.edge_id
        rear.cars[0].s = tail.s - front.chain_length
        front.cars.extend(rear.cars)
        front.car_count = len(front.cars)
        self._merge_chain_lengths(front, rear, n_front)
        self._merge_trails(front, rear)
        front.ended = False
        front.held_node = None
        front._attracted = False
        self._place_cars(front)
        self._drop_merged_train(rear)
        self._ce_ctx_dirty = True
        return True

    def _merge_chain_lengths(self, front: _Train, rear: _Train,
                             n_front: int) -> None:
        """合并锁链长度表：前段原有 + 接缝链（用前段链长）+ 后段原有。"""
        f_links = (list(front.chain_lengths) if front.chain_lengths
                   else [front.chain_length] * max(0, n_front - 1))
        n_rear = len(rear.cars)
        r_links = (list(rear.chain_lengths) if rear.chain_lengths
                   else [rear.chain_length] * max(0, n_rear - 1))
        # 接缝用前段链距：对齐公式（tail.s - front.chain_length）与之一致
        front.chain_lengths = f_links + [front.chain_length] + r_links

    def _merge_trails(self, front: _Train, rear: _Train) -> None:
        """trail 顺接：前段来路 + 后段来路（去重接缝边），覆盖并回车尾。"""
        trail = list(front.trail)
        for eid in rear.trail:
            if not trail or trail[-1] != eid:
                trail.append(eid)
        front.trail = trail[:self._TRAIL_CAP]

    def _drop_merged_train(self, rear: _Train) -> None:
        """摘除被并段：清道岔路由/到达缓存与其 legacy 轨道边。"""
        self._trains.pop(rear.train_id, None)
        self._edges.pop(self._legacy_track_id(rear.train_id), None)
        for node in self._nodes.values():
            node._route_cache.pop(rear.train_id, None)
            node._arrival = [x for x in node._arrival if x != rear.train_id]
        self._owner_map = None
        self._gap_cache = {}

    def set_train_skip_gaps(self, train_id, skip: bool) -> None:
        """实时开关「跳过连接处」: 火车过设备空余段时瞬移到下一设备组。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        tr.skip_gaps = bool(skip)
        tr.ended = False

    def set_chain_length(self, train_id, chain_idx: int, length: float) -> None:
        """单独设置某根锁链长度(chain_idx 从 0 开始; 0=第1根锁链)。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        n_chain = max(0, tr.car_count - 1)
        if chain_idx < 0 or chain_idx >= n_chain:
            return
        if tr.chain_lengths is None:
            tr.chain_lengths = [tr.chain_length] * n_chain
        tr.chain_lengths[chain_idx] = max(0.01, float(length))
        self._place_cars(tr)
        self._ce_ctx_dirty = True

    def set_chain_length_all(self, train_id, length: float) -> None:
        """把所有锁链统一设为 length, 清除独立锁链覆盖。"""
        tr = self._trains.get(str(train_id))
        if tr is None:
            return
        tr.chain_length = max(0.01, float(length))
        tr.chain_lengths = None
        self._place_cars(tr)
        self._ce_ctx_dirty = True

    # ------------------------------------------------------------------
    #  道岔(命令节点)
    # ------------------------------------------------------------------

    def add_switch(self, node_id, type, track_id, s: float,
                   branch_params: dict | None = None) -> str | None:
        """在轨道 track_id 弧长 s 处放置道岔节点, 返回 node_id。

        type ∈ {split, merge, uncouple, couple}; branch_params 约定:
          split/uncouple: {"branch": <支线轨 id>} 或 {"branches": [a, b, ...]}
          merge/couple:   {"branch": <汇入轨 id>} 或 {"branches": [...]}
          split 可选 {"ratio": 0..1}(比例分流; 缺省交替); uncouple 需 {"car_index": k}
        """
        ntype = str(type)
        if ntype not in _SWITCH_TYPES:
            raise ValueError(f"unknown switch type: {ntype}")
        edge = self._edges.get(str(track_id))
        if edge is None:
            return None
        s = float(s)
        if edge.length <= 0.0 or s <= 0.0 or s >= edge.length:
            return None
        nid = str(node_id)
        node = _Node(nid, ntype)
        node.params = dict(branch_params or {})
        main_out_id = f"{track_id}#out#{nid}"
        self._split_edge_at(edge, s, nid, main_out_id)
        in_edge_id = str(track_id)
        branch = node.params.get("branch")
        if branch is None:
            branch = node.params.get("branches")
        branch_ids = self._normalize_branch_ids(branch, in_edge_id)
        if ntype in ("split", "uncouple"):
            node.in_edges = [in_edge_id]
            node.out_edges = [main_out_id] + branch_ids
            for b in branch_ids:
                b_edge = self._edges.get(b)
                if b_edge is not None:
                    b_edge.start_node = nid
        else:  # merge / couple
            node.in_edges = [in_edge_id] + branch_ids
            for b in branch_ids:
                b_edge = self._edges.get(b)
                if b_edge is not None:
                    b_edge.end_node = nid
            node.out_edges = [main_out_id]
        self._nodes[nid] = node
        self._ce_ctx_dirty = True
        return nid

    def _normalize_branch_ids(self, branch, in_edge_id: str) -> list[str]:
        out: list[str] = []
        if isinstance(branch, (list, tuple)):
            cands = [str(b) for b in branch]
        elif branch is not None:
            cands = [str(branch)]
        else:
            cands = []
        for b in cands:
            if b in self._edges and b != in_edge_id and b not in out:
                out.append(b)
        return out

    def add_node_branch(self, node_id, track_id) -> bool:
        """把既有轨道接成某道岔节点的支线(画轨道从节点出发/汇入节点)。

        节点类型决定接线方向: split/uncouple → 支线从节点出发(out_edges);
        merge/couple → 支线汇入节点(in_edges + 支线 end_node=节点)。
        """
        node = self._nodes.get(str(node_id))
        edge = self._edges.get(str(track_id))
        if node is None or edge is None:
            return False
        if node.type in ("split", "uncouple"):
            if str(track_id) not in node.out_edges:
                node.out_edges.append(str(track_id))
            edge.start_node = node.node_id
            if edge.end_node == node.node_id:
                edge.end_node = None
        else:  # merge / couple
            if str(track_id) not in node.in_edges:
                node.in_edges.append(str(track_id))
            edge.end_node = node.node_id
        self._ce_ctx_dirty = True
        self._owner_map = None
        self._gap_cache = {}
        return True

    def set_switch_direction(self, node_id, direction: int) -> None:
        """人工道岔: 设定分叉节点当前指向的出边(0=主干, 1=支线1...), 实时生效。"""
        node = self._nodes.get(str(node_id))
        if node is None or not node.out_edges:
            return
        node.params["direction"] = int(direction) % len(node.out_edges)

    def stack_switch(self, node_id, type: str) -> bool:
        """把另一个功能标牌叠加到既有节点(分叉+解挂 等效果组合)。"""
        node = self._nodes.get(str(node_id))
        if node is None or type not in _SWITCH_TYPES or type == node.type:
            return False
        stacked = node.params.setdefault("stacked", [])
        if type not in stacked:
            stacked.append(type)
        return True

    def _node_effects(self, node: _Node) -> set:
        """节点的全部功能标牌(主类型 + 叠加类型)。"""
        effects = {node.type}
        effects.update(node.params.get("stacked") or [])
        return effects

    def _split_edge_at(self, edge: _Edge, s: float, node_id: str,
                       main_out_id: str) -> None:
        """把边在弧长 s 处切开: 入段保留原 id(end_node=node_id), 主续出段为新边。"""
        was_closed = edge.closed
        in_pts = _sub_polyline(edge.points, edge.cum, 0.0, s)
        out_pts = _sub_polyline(edge.points, edge.cum, s, edge.length)
        edge.points = in_pts
        edge.cum = _polyline_cumlen(in_pts)
        edge.length = edge.cum[-1] if edge.cum else 0.0
        # 切开后的入段是开链（除非 s=0/末尾的退化情况），必须重算 closed，
        # 否则覆盖/反向运动会把它当闭环环绕。
        edge.closed = (len(in_pts) >= 3 and in_pts[0] == in_pts[-1])
        edge.end_node = node_id
        edge.proj_s = None
        edge.proj_d = None
        edge.s_order = None
        edge.s_sorted = None
        edge.d_sorted = None
        out_edge = _Edge(main_out_id, out_pts)
        out_edge.start_node = node_id
        self._edges[main_out_id] = out_edge
        if was_closed:
            # 接缝回路: 出段末端 → 入段起点(隐式 loop 节点, 不进 UI)
            loop_id = f"{node_id}#loop"
            loop_node = _Node(loop_id, "loop")
            loop_node.in_edges = [main_out_id]
            loop_node.out_edges = [edge.edge_id]
            out_edge.end_node = loop_id
            edge.start_node = loop_id
            self._nodes[loop_id] = loop_node
        self._owner_map = None
        self._gap_cache = {}

    # ------------------------------------------------------------------
    #  帧推进
    # ------------------------------------------------------------------

    def tick(self, dt: float) -> FrameResult:
        """推进一帧并返回覆盖/坐标/切线角/激活掩码。"""
        self._couple_pull(dt)
        self._advance_all(dt)
        coverage, coverage_strength, order_s = self._compute_coverage()
        self._coverage = coverage
        self._refresh_active_ids(order_s)
        nose_s: list[float] = []
        car_positions: list[list[tuple[float, float]]] = []
        car_angles: list[list[float]] = []
        coverage_radius_list: list[list[float]] = []
        train_ids: list[str] = []
        for tr in self._trains.values():
            nose_s.append(tr.nose_s)
            train_ids.append(tr.train_id)
            pos, ang, rad = self._car_outputs(tr)
            car_positions.append(pos)
            car_angles.append(ang)
            coverage_radius_list.append(rad)
        return FrameResult(
            nose_s=nose_s,
            car_positions=car_positions,
            car_angles=car_angles,
            coverage=coverage,
            coverage_radius_list=coverage_radius_list,
            active_mask=(self._coverage >= 1),
            coverage_strength=coverage_strength,
            active_ordered_ids=list(self._active_ordered_ids),
            train_ids=train_ids,
        )

    def _couple_anchor(self, node: _Node) -> tuple[float, float] | None:
        """连挂节点锚点 = 第一条入线末端(所有入线在节点处汇合)。"""
        for eid in node.in_edges:
            e = self._edges.get(eid)
            if e is not None and e.points:
                return e.points[-1]
        return None

    def _couple_near_trains(self, node: _Node) -> list[_Train]:
        """连挂候选: 已挂起在本节点的车列 + 在节点前后主线上且进入磁吸半径的车列。"""
        anchor = self._couple_anchor(node)
        if anchor is None:
            return []
        allowed = list(node.in_edges)
        if node.out_edges:
            allowed.append(node.out_edges[0])
        out: list[_Train] = []
        for tr in self._trains.values():
            if not tr.cars:
                continue
            if tr.held_node is not None and tr.held_node != node.node_id:
                continue
            head = tr.cars[0]
            edge = self._edges.get(head.edge_id)
            if edge is None or edge.length <= 0.0:
                continue
            if head.edge_id not in allowed:
                continue  # 只在节点主线上(入段/出段)才参与吸附
            if tr.held_node == node.node_id:
                out.append(tr)
                continue
            hx, hy = _point_at_arclen(head.s, edge.points, edge.cum)
            if math.hypot(hx - anchor[0], hy - anchor[1]) <= self._couple_attract:
                out.append(tr)
        return out

    def _couple_try_same_track(self, node: _Node, near: list[_Train],
                               dt: float) -> bool:
        """同轨道同方向检查点连挂: 在节点前后主线上找最近两列, 后车追前车头尾链接。

        返回 True 表示已处理(可能仍在吸附中, 也可能已完成合并); False 表示
        没有同主线同向候选, 交给调用方回退到旧的多入线节点吸附语义。
        """
        if not node.in_edges or not node.out_edges:
            return False
        in_id = node.in_edges[0]
        out_id = node.out_edges[0]
        candidates: list[tuple[float, _Train]] = []
        for tr in near:
            if tr.dir_sign < 0.0:
                continue
            head = tr.cars[0]
            if head.edge_id == in_id:
                edge = self._edges.get(in_id)
                if edge is None:
                    continue
                coord = head.s - edge.length
            elif head.edge_id == out_id:
                edge = self._edges.get(out_id)
                if edge is None:
                    continue
                coord = head.s
            else:
                continue
            candidates.append((coord, tr))
        if len(candidates) < 2:
            return False
        candidates.sort(key=lambda x: x[0])
        best: tuple[float, int, float] | None = None
        for i in range(len(candidates) - 1):
            gap = candidates[i + 1][0] - candidates[i][0]
            if gap < 0.0:
                continue
            if (best is None or gap < best[0]
                    or (gap == best[0] and abs(candidates[i + 1][0]) < abs(best[2]))):
                best = (gap, i, candidates[i + 1][0])
        if best is None:
            return False
        rear = candidates[best[1]][1]
        front = candidates[best[1] + 1][1]
        gap = best[0]
        step = self._couple_pull_speed * max(0.0, float(dt))
        rear._attracted = True
        front._attracted = True
        if gap > self._couple_link_dist:
            # 后车追前车, 前车在磁吸中暂停, 形成头尾靠近
            rear_head = rear.cars[0]
            move = min(step, gap - self._couple_link_dist)
            rear_head.s += move
            self._place_cars(rear)
            return True
        # 头尾已到链接距离: 后车并入前车(前车在前)
        front.cars.extend(rear.cars)
        front.car_count = len(front.cars)
        rear._attracted = False
        rear.held_node = None
        self._trains.pop(rear.train_id, None)
        front._attracted = False
        front.held_node = None
        front.ended = False
        node._route_cache[front.train_id] = out_id
        self._place_cars(front)
        return True

    def _couple_pull(self, dt: float) -> None:
        """连挂磁吸: 同轨道同方向检查点优先(后车追前车), 多入线节点回退旧语义。"""
        for tr in self._trains.values():
            tr._attracted = False  # 每帧重置: 只有本帧进入吸附的车列才被吸住
        for node in list(self._nodes.values()):
            if "couple" not in self._node_effects(node):
                continue
            near = self._couple_near_trains(node)
            if len(near) < 2:
                continue
            if self._couple_try_same_track(node, near, dt):
                continue
            # 旧语义回退: 多入线(不同轨道)在节点处汇合吸附
            step = self._couple_pull_speed * max(0.0, float(dt))
            ready = True
            for tr in near:
                tr._attracted = True
                head = tr.cars[0]
                edge = self._edges.get(head.edge_id)
                d = max(0.0, edge.length - head.s)
                if d > self._couple_link_dist:
                    head.s += min(step, d - self._couple_link_dist)
                    self._place_cars(tr)
                ready = ready and (d <= self._couple_link_dist)
            if not ready:
                continue
            # 链接完成: 车头位置不动, 沿 front 原有 trail 回溯放置 → 无缝衔接。
            # 不能把其它入线 trail 追加进 front.trail: 各入线在节点处汇合,
            # 合并后的车厢应沿前车来路排列, 而不是沿另一条支线错位回溯。
            front = near[0]
            for tr in near[1:]:
                front.cars.extend(tr.cars)
                front.car_count = len(front.cars)
                tr._attracted = False
                tr.held_node = None
                self._trains.pop(tr.train_id, None)
            front._attracted = False
            front.held_node = None
            front.ended = False
            # 已完成的连挂在本节点直通(不能再被当成“先到”挂起)
            node._route_cache[front.train_id] = (
                node.out_edges[0] if node.out_edges else front.cars[0].edge_id)
            self._place_cars(front)

    def _car_outputs(self, tr: _Train):
        pos: list[tuple[float, float]] = []
        ang: list[float] = []
        rad: list[float] = []
        for j, c in enumerate(tr.cars):
            # 车厢中心 = 从车头沿 trail 回溯 前 j 根锁链总长 + 车长/2 弧长。
            # 中心可能落在更早的边上(过岔瞬间), 直接用 c.edge_id 会把车厢闪到新边。
            eid, s_center = self._walk_back(
                tr, tr.chain_back(j) + 0.5 * tr.car_length)
            edge = self._edges.get(eid)
            if edge is None or edge.length <= 0.0:
                pos.append((0.0, 0.0))
                ang.append(0.0)
                rad.append(c.radius)
                continue
            if edge.closed:
                s_center = s_center % edge.length   # 闭环: 环绕
            else:
                s_center = max(0.0, min(s_center, edge.length))  # 开链: 钳制
            pos.append(_point_at_arclen(s_center, edge.points, edge.cum))
            ang.append(_tangent_angle(s_center, edge.points, edge.cum))
            rad.append(c.radius)
        return pos, ang, rad

    # ------------------------------------------------------------------
    #  运动学(车厢物理)
    # ------------------------------------------------------------------

    def _consume_start_delay(self, tr: _Train, dt: float):
        """R6 错峰出发: 消耗本帧延迟量, 返回该车本帧可用 dt。

        延迟未消耗完 → 返回 None（本车不动）; 恰好在本帧内走完 → 返回
        剩余 dt（出发瞬间才开始爬坡, 避免延迟期间油门已到目标 → 出发变阶跃）。
        """
        if tr.start_delay <= 0.0:
            return dt
        if dt < tr.start_delay:
            tr.start_delay -= dt
            return None
        dt_eff = dt - tr.start_delay
        tr.start_delay = 0.0
        return dt_eff

    def _advance_all(self, dt: float) -> None:
        added: dict[str, _Train] = {}
        removed: set[str] = set()
        for tr in list(self._trains.values()):
            if tr.held_node is not None or tr.ended or tr._attracted or tr.train_id in removed:
                continue
            dt_eff = self._consume_start_delay(tr, dt)
            if dt_eff is None or not tr.cars:
                continue
            self._advance_throttle_ramp(tr, dt_eff)
            self._advance_one_train(tr, tr.base_speed * tr.throttle * dt_eff
                                    * tr.dir_sign, added, removed)
        for tid in removed:
            self._trains.pop(tid, None)
        self._trains.update(added)

    def _advance_one_train(self, tr: _Train, step: float, added: dict,
                           removed: set) -> None:
        """单车本帧推进：R8 段循环优先消化，否则走原整轨推进（步长 0 跳过）。"""
        if step == 0.0:
            return
        if not self._step_loop_span(tr, step):
            self._advance_train(tr, step, added, removed)

    def _advance_train(self, tr: _Train, step: float, added: dict,
                        removed: set) -> None:
        """车头推进(正/反向) + 节点转移 + 端点按运行模式处理 + 车厢定距跟随。

        运行模式: loop_* 环绕 / bounce 端点掉头 / one_way_* 到站停住。
        反向运动沿 trail 回溯(过岔时按来路原路退回)。
        """
        head = tr.cars[0]
        head.s += step
        if step >= 0.0:
            # ── 正向 ──
            while True:
                if tr.train_id in removed or tr.held_node is not None:
                    break
                edge = self._edges.get(head.edge_id)
                if edge is None or edge.length <= 0.0 or head.s < edge.length:
                    break
                over = head.s - edge.length
                if edge.end_node is None:
                    # 轨道真端点: 按运行模式处理
                    if tr.mode in ("loop_forward", "loop_reverse"):
                        # 分叉切出的出段: 环绕应回到整条轨道起点(入段起点),
                        # 而不是回到分叉节点(否则分叉被当成循环起点)。
                        sn = edge.start_node
                        s_node = self._nodes.get(sn) if sn is not None else None
                        if s_node is not None and s_node.in_edges:
                            back_id = s_node.in_edges[0]
                            back = self._edges.get(back_id)
                            if back is not None and back.length > 0.0:
                                head.edge_id = back_id
                                head.s = over
                                if not tr.trail or tr.trail[0] != back_id:
                                    tr.trail.insert(0, back_id)
                                self._trim_trail(tr)
                                continue
                        head.s = over          # 循环: 环绕回本边起点
                        continue
                    if tr.mode == "bounce":
                        tr.dir_sign = -1.0     # 返程: 掉头
                        head.s = edge.length
                        break
                    tr.ended = True            # 单程正跑: 到站停住
                    head.s = edge.length
                    break
                out = self._route_head(tr, head, edge, over, added, removed)
                if out is None:
                    break
                head.edge_id = out
                head.s = over
                if not tr.trail or tr.trail[0] != out:      # 记账: 走过的边
                    tr.trail.insert(0, out)
                self._trim_trail(tr)
            if tr.train_id in removed:
                return
            self._snap_cars_over_gaps(tr)
            self._place_cars(tr)
            return
        # ── 反向 ──
        while True:
            edge = self._edges.get(head.edge_id)
            if edge is None or edge.length <= 0.0:
                break
            if head.s >= 0.0:
                break
            if edge.closed:
                head.s += edge.length           # 闭环反向环绕是连续的
                continue
            if len(tr.trail) > 1:
                # 过岔: 沿来路退回上一条边
                prev = self._edges.get(tr.trail[1])
                if prev is None or prev.length <= 0.0:
                    break
                head.s += prev.length
                head.edge_id = prev.edge_id
                tr.trail.pop(0)
                # 反向经过解挂/连挂节点: 同样触发效果(用户: 无论正反经过)
                node_id = prev.end_node
                node = self._nodes.get(node_id) if node_id is not None else None
                if (node is not None
                        and "uncouple" in self._node_effects(node)
                        and tr.train_id not in node._route_cache):
                    self._route_uncouple(node, tr, prev, added)
                continue
            # 无历史 trail: 从出段起点退回节点(经 start_node)
            sn = edge.start_node
            s_node = self._nodes.get(sn) if sn is not None else None
            if s_node is not None and s_node.in_edges:
                in_e = self._edges.get(s_node.in_edges[0])
                if in_e is not None and in_e.length > 0.0:
                    head.s += in_e.length
                    head.edge_id = in_e.edge_id
                    # trail[0] 必须始终是当前车头所在边；退回节点后旧边在前方，不能 insert。
                    if tr.trail:
                        tr.trail[0] = in_e.edge_id
                    else:
                        tr.trail.append(in_e.edge_id)
                    # 反向经过解挂节点: 同样触发平分
                    if ("uncouple" in self._node_effects(s_node)
                            and tr.train_id not in s_node._route_cache):
                        self._route_uncouple(s_node, tr, edge, added)
                    continue
            # 轨道起点
            if tr.mode == "bounce":
                tr.dir_sign = 1.0               # 返程: 掉头回正
                head.s = 0.0
                break
            if tr.mode in ("one_way_reverse", "one_way_forward"):
                tr.ended = True                 # 单程反跑: 到站停住
                head.s = 0.0
                break
            head.s += edge.length               # 循环: 环绕到本边末端
            break
        self._snap_cars_over_gaps(tr)
        self._place_cars(tr)

    def get_track_gaps(self, track_id, radius: float) -> list[tuple[float, float]]:
        """某轨道在判定半径内的「设备空余段」(连接处) 列表 [(a,b),...]。

        供 UI 画连接处标记; 判定半径一般取火车激活半径。
        """
        edge = self._edges.get(str(track_id))
        if edge is None or edge.length <= 0.0 or radius <= 0.0:
            return []
        return list(self._edge_gaps(edge, float(radius)))

    def _edge_gaps(self, edge: _Edge, radius: float
                   ) -> list[tuple[float, float]]:
        """计算边的设备空余段: 半径内无任何设备的弧区间(连接处)。

        设备覆盖区间 = [proj_s-radius, proj_s+radius] 的并集; 空余段 = 其补集。
        结果按 (edge_id, 半径1位小数) 缓存, 几何变化时整体失效。
        """
        key = (edge.edge_id, float(radius))
        cached = self._gap_cache.get(key)
        if cached is not None:
            return cached
        self._edge_projection(edge)
        s = edge.proj_s
        d = edge.proj_d
        if s is None or d is None or s.shape[0] == 0:
            self._gap_cache[key] = []
            return []
        length = edge.length
        spans: list[tuple[float, float]] = []
        for si, di in zip(s.tolist(), d.tolist(), strict=True):
            if di <= radius:
                spans.append((max(0.0, si - radius), min(length, si + radius)))
        if not spans:
            gaps = [(0.0, length)]
        else:
            spans.sort()
            merged = [list(spans[0])]
            for lo, hi in spans[1:]:
                if lo <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            gaps = []
            cur = 0.0
            for lo, hi in merged:
                if lo > cur:
                    gaps.append((cur, lo))
                cur = max(cur, hi)
            if cur < length:
                gaps.append((cur, length))
        self._gap_cache[key] = gaps
        return gaps

    def _snap_cars_over_gaps(self, tr: _Train) -> None:
        """跳过连接处: 把落在设备空余段内的车厢瞬移到空余段出口。

        正向出口 = 段尾 b, 反向出口 = 段头 a(时间上压缩空余段 = 激活不中断)。
        """
        if not tr.skip_gaps or not tr.cars:
            return
        forward = tr.dir_sign >= 0.0
        for c in tr.cars:
            edge = self._edges.get(c.edge_id)
            if edge is None or edge.length <= 0.0:
                continue
            for a, b in self._edge_gaps(edge, tr.radius):
                if a <= c.s <= b:
                    c.s = b if forward else a
                    break

    def _walk_back(self, tr: _Train, back: float) -> tuple[str, float]:
        """从车头沿 trail 向后回溯 back 弧长, 返回 (edge_id, s)。

        trail 走完仍为负 → 返回最早那条边上的负 s(车尾还没上轨, 覆盖端自然为空)。
        """
        idx = 0
        s = tr.cars[0].s - back
        while s < 0.0 and idx + 1 < len(tr.trail):
            idx += 1
            prev = self._edges.get(tr.trail[idx])
            if prev is None or prev.length <= 0.0:
                break
            s += prev.length
        return (tr.trail[idx] if tr.trail else tr.cars[0].edge_id, s)

    def _place_cars(self, tr: _Train) -> None:
        """按 trail 回溯放置第 1..n-1 节车厢(车头 cars[0] 由调用方先摆好)。

        车厢(珠子)间距 = 锁链长度 chain_length, 与车长(珠子大小)独立。
        """
        for j in range(1, len(tr.cars)):
            eid, s = self._walk_back(tr, tr.chain_back(j))
            c = tr.cars[j]
            c.edge_id = eid
            c.s = s

    def _trim_trail(self, tr: _Train) -> None:
        """丢弃整列车厢已完全驶离的旧边。"""
        # 车尾显示中心最多回溯 前 n-1 根锁链总长 + 0.5*car_length，覆盖区间还要更长。
        need = tr.chain_back(max(0, len(tr.cars) - 1)) + tr.car_length
        acc = tr.cars[0].s
        keep = 1
        while acc < need and keep < len(tr.trail):
            e = self._edges.get(tr.trail[keep])
            if e is None:
                break
            acc += e.length
            keep += 1
        del tr.trail[keep:]
        del tr.trail[self._TRAIL_CAP:]

    def _route_head(self, tr: _Train, head: _Car, edge: _Edge, over: float,
                    added: dict, removed: set) -> str | None:
        """按边末端节点类型做车头转移(返回下一出边 id; None=挂起不转移)。"""
        node_id = edge.end_node
        if node_id is None:
            return edge.edge_id  # 普通端点: wrap 回本边起点
        node = self._nodes.get(node_id)
        if node is None:
            return edge.edge_id
        if node.type == "loop":
            # 隐式回路节点: 出段末端绕回入段起点(闭环切分接缝)
            return node.out_edges[0] if node.out_edges else edge.edge_id
        if node.type == "split":
            return self._route_split(node, tr, added)
        if node.type == "merge":
            # 记录到达顺序并去重，避免绕圈时 _arrival 无限增长。
            if tr.train_id in node._arrival:
                node._arrival.remove(tr.train_id)
            node._arrival.append(tr.train_id)
            return node.out_edges[0] if node.out_edges else edge.edge_id
        if node.type == "uncouple":
            return self._route_uncouple(node, tr, edge, added)
        if node.type == "couple":
            cached = node._route_cache.get(tr.train_id)
            if cached is not None:
                return cached if cached in self._edges else (
                    node.out_edges[0] if node.out_edges else edge.edge_id)
            # 磁吸语义: 先到挂起等待, 后到追尾合并。任何车列到达都先挂起,
            # 由 _couple_pull 在凑齐两列后合并并写入直通缓存。
            tr.held_node = node.node_id
            head.s = edge.length
            return None
        return edge.edge_id

    def _route_split(self, node: _Node, tr: _Train, added: dict) -> str:
        """分叉 = 人工道岔: 用户选定方向后, 整车只走所选支线(不解挂)。

        叠加了解挂标牌(分叉+解挂)时: 经过的火车自动平分车厢,
        前半走所选方向, 后半走另一支线。
        """
        if not node.out_edges:
            return None
        effects = self._node_effects(node)
        if len(node.out_edges) == 1 and "uncouple" not in effects:
            return node.out_edges[0]
        idx = int(node.params.get("direction", 0)) % max(1, len(node.out_edges))
        if "uncouple" in effects:
            # 每个火车在同一节点只分裂一次；闭环绕圈时不能每圈都再分。
            if tr.train_id in node._route_cache:
                return node.out_edges[idx]
            n = len(tr.cars)
            # 检查点语义: 不按具体车厢/归属, 统一平分车厢断开
            k = max(1, n // 2)
            if k < n:
                other = 1 - idx if len(node.out_edges) == 2 else (idx + 1) % len(node.out_edges)
                rear_cars = tr.cars[k:]
                tr.cars = tr.cars[:k]
                tr.car_count = k
                rear_id = f"{tr.train_id}#uncouple#{node.node_id}"
                cnt = 0
                while rear_id in self._trains or rear_id in added:
                    cnt += 1
                    rear_id = f"{tr.train_id}#uncouple#{node.node_id}#{cnt}"
                rear = _Train(rear_id,
                              len(rear_cars), tr.car_length, tr.radius,
                              tr.base_speed, tr.color)
                rear.throttle = tr.throttle
                rear.chain_length = tr.chain_length
                if tr.chain_lengths:
                    rear.chain_lengths = list(
                        tr.chain_lengths[k:k + max(0, len(rear_cars) - 1)])
                rear.mode = tr.mode
                rear.dir_sign = tr.dir_sign
                rear.skip_gaps = tr.skip_gaps
                rear.cars = rear_cars
                rear.trail = [node.out_edges[other]]
                rear._home_edge_id = tr._home_edge_id
                for j, c in enumerate(rear_cars):
                    c.edge_id = node.out_edges[other]
                    c.s = -rear.chain_back(j)
                added[rear_id] = rear
                node._route_cache[tr.train_id] = node.out_edges[idx]
                node._route_cache[rear_id] = node.out_edges[other]
        return node.out_edges[idx]

    def _route_uncouple(self, node: _Node, tr: _Train, edge: _Edge,
                        added: dict) -> str:
        cached = node._route_cache.get(tr.train_id)
        if cached is not None:
            return cached
        effects = self._node_effects(node)
        main_out = node.out_edges[0] if node.out_edges else edge.edge_id
        branch_out = node.out_edges[1] if len(node.out_edges) >= 2 else None
        # 分叉+解挂叠加: 前半走用户选定方向, 后半走另一支线(与 split 为基底时对称)
        if "split" in effects:
            idx = int(node.params.get("direction", 0)) % max(1, len(node.out_edges))
            main_out = node.out_edges[idx]
            if len(node.out_edges) >= 2:
                other = (1 - idx if len(node.out_edges) == 2
                         else (idx + 1) % len(node.out_edges))
                branch_out = node.out_edges[other]
            else:
                branch_out = main_out
        rear_out = branch_out if branch_out is not None else main_out
        n = len(tr.cars)
        # 检查点语义: 不按具体车厢/归属, 统一平分车厢断开; 无支线时后段留在同主线
        k = max(1, n // 2)
        if k >= n:
            node._route_cache[tr.train_id] = main_out
            return main_out
        rear_cars = tr.cars[k:]
        tr.cars = tr.cars[:k]
        tr.car_count = k
        rear = _Train(f"{tr.train_id}#uncouple#{node.node_id}", len(rear_cars),
                      tr.car_length, tr.radius, tr.base_speed, tr.color)
        rear.throttle = tr.throttle
        rear.chain_length = tr.chain_length
        if tr.chain_lengths:
            rear.chain_lengths = list(
                tr.chain_lengths[k:k + max(0, len(rear_cars) - 1)])
        rear.mode = tr.mode
        rear.dir_sign = tr.dir_sign
        rear.skip_gaps = tr.skip_gaps
        rear.cars = rear_cars
        rear.trail = [rear_out]
        rear._home_edge_id = tr._home_edge_id
        for j, c in enumerate(rear_cars):
            c.edge_id = rear_out
            c.s = -rear.chain_back(j)
        added[rear.train_id] = rear
        node._route_cache[tr.train_id] = main_out
        node._route_cache[rear.train_id] = rear_out
        return main_out

    def _compute_coverage(self) -> tuple[np.ndarray, dict[str, float], np.ndarray]:
        if self._use_ce():
            return self._compute_coverage_ce()
        return self._compute_coverage_numpy()

    def _ensure_work_buffers(self, n: int) -> None:
        if self._coverage_work.shape[0] != n:
            self._coverage_work = np.zeros(n, dtype=np.float64)
            self._order_work = np.full(n, np.inf, dtype=np.float64)

    def _edge_projection(self, edge: _Edge) -> None:
        """预计算每设备在边上的 (最近弧长 s, 横向距离 d); 缓存于 edge。"""
        if edge.proj_s is not None:
            return
        n = self._num_devices
        if n == 0 or len(edge.points) < 2 or edge.length <= 0.0:
            edge.proj_s = np.zeros(n, dtype=np.float64)
            edge.proj_d = np.full(n, np.inf, dtype=np.float64)
            edge.s_order = np.arange(n)
            edge.s_sorted = edge.proj_s.copy()
            edge.d_sorted = edge.proj_d.copy()
            return
        px = self._device_pos[:, 0]
        py = self._device_pos[:, 1]
        best_s = np.zeros(n, dtype=np.float64)
        best_d2 = np.full(n, np.inf, dtype=np.float64)
        # 分块向量化: (m, n) 批量投影, m<=_PROJ_CHUNK; 平局保持「先注册段胜」
        # (argmin 取首个最小 + 跨块严格小于), 与原逐段循环逐位一致。
        pts = np.asarray(edge.points, dtype=np.float64)
        cum_arr = np.asarray(edge.cum, dtype=np.float64)
        n_seg = len(edge.points) - 1
        dev_idx = np.arange(n)
        for start in range(0, n_seg, _PROJ_CHUNK):
            i_end = min(start + _PROJ_CHUNK, n_seg)
            a = pts[start:i_end]
            b = pts[start + 1:i_end + 1]
            dx = b[:, 0] - a[:, 0]
            dy = b[:, 1] - a[:, 1]
            seg2 = dx * dx + dy * dy
            safe = np.where(seg2 == 0.0, 1.0, seg2)
            t = ((px[None, :] - a[:, 0, None]) * dx[:, None]
                 + (py[None, :] - a[:, 1, None]) * dy[:, None]) / safe[:, None]
            t = np.clip(t, 0.0, 1.0)
            qx = a[:, 0, None] + t * dx[:, None]
            qy = a[:, 1, None] + t * dy[:, None]
            d2 = (px[None, :] - qx) ** 2 + (py[None, :] - qy) ** 2
            seg_len = np.sqrt(seg2)
            s_off = cum_arr[start:i_end, None] + t * seg_len[:, None]
            k = np.argmin(d2, axis=0)
            chunk_d2 = d2[k, dev_idx]
            chunk_s = s_off[k, dev_idx]
            better = chunk_d2 < best_d2
            best_d2 = np.where(better, chunk_d2, best_d2)
            best_s = np.where(better, chunk_s, best_s)
        edge.proj_s = best_s
        edge.proj_d = np.sqrt(best_d2)
        edge.s_order = np.argsort(best_s, kind="stable")
        edge.s_sorted = best_s[edge.s_order]
        edge.d_sorted = edge.proj_d[edge.s_order]

    def _compute_coverage_numpy(self) -> tuple[np.ndarray, dict[str, float], np.ndarray]:
        n = self._num_devices
        self._ensure_work_buffers(n)
        out = self._coverage_work
        order_s = self._order_work
        out.fill(0.0)
        order_s.fill(np.inf)
        for tr in self._trains.values():
            for car in tr.cars:
                edge = self._edges.get(car.edge_id)
                if edge is None or edge.length <= 0.0 or car.radius <= 0.0 or car.car_length <= 0.0:
                    continue
                self._edge_projection(edge)
                self._apply_car_coverage(car, edge, out, order_s)
        coverage = out.astype(np.uint8)
        return coverage, self._sparse_strength(coverage), order_s

    def _apply_car_coverage(self, car: _Car, edge: _Edge,
                            out: np.ndarray | None,
                            order_s: np.ndarray) -> None:
        """单车厢覆盖合成。out=None 时只更新 order_s(供 CE 路径复用同一份逻辑)。

        区间按 _wrap_spans 切分: 开链夹取 / 闭环环绕(T3); 每设备只允许属于
        "离它最近的那条边"(T3b _edge_owner_mask), 消除道岔切分处的假激活。
        """
        s_sorted = edge.s_sorted
        if s_sorted is None or s_sorted.shape[0] == 0:
            return
        # 激活圈语义: 以每节车厢中心为圆心, car.radius 为半径的真实圆形激活区。
        # 沿轨道方向取 [s-radius, s+radius] 包围盒, 再按 (弧向差² + 横向差²) 判圆。
        spans = _wrap_spans(car.s - car.radius, car.s + car.radius,
                            edge.length, edge.closed)
        car_s = car.s
        if edge.closed:
            car_s = car.s % edge.length
        # 延迟取 owner：优先用 _owner_map[idx_sel] 切片，避免每节车厢分配全量 n 布尔数组。
        owner_map = self._owner_map
        if owner_map is None:
            self._rebuild_owner_map()
            owner_map = self._owner_map
        edge_idx = self._edge_index.get(edge.edge_id, -1) if owner_map is not None else -1
        r2 = float(car.radius) * float(car.radius)
        for lo_eff, hi_eff in spans:
            lo_idx = int(np.searchsorted(s_sorted, lo_eff, side="left"))
            hi_idx = int(np.searchsorted(s_sorted, hi_eff, side="right"))
            if hi_idx <= lo_idx:
                continue
            s_sel = s_sorted[lo_idx:hi_idx]
            d_sel = edge.d_sorted[lo_idx:hi_idx]
            idx_sel = edge.s_order[lo_idx:hi_idx]
            s_diff = np.abs(s_sel - car_s)
            if edge.closed:
                s_diff = np.minimum(s_diff, edge.length - s_diff)
            within = (s_diff * s_diff + d_sel * d_sel) <= r2
            if owner_map is not None:
                within &= owner_map[idx_sel] == edge_idx
            if not np.any(within):
                continue
            s_hit = s_sel[within]
            idx_hit = idx_sel[within]
            prev_order = order_s[idx_hit]
            order_s[idx_hit] = np.where(~np.isfinite(prev_order), s_hit, prev_order)
            if out is not None:
                d_hit = d_sel[within]
                s_diff_hit = s_diff[within]
                dist_hit = np.sqrt(s_diff_hit * s_diff_hit + d_hit * d_hit)
                val = np.maximum(255.0 * (1.0 - dist_hit / car.radius), 1.0)
                out[idx_hit] = np.maximum(out[idx_hit], val)

    def _compute_order_s(self) -> np.ndarray:
        """CE 路径用: 与 numpy 覆盖路径逐位一致的排序弧长(复用同一实现)。"""
        order_s = np.full(self._num_devices, np.inf, dtype=np.float64)
        for tr in self._trains.values():
            for car in tr.cars:
                edge = self._edges.get(car.edge_id)
                if (edge is None or edge.length <= 0.0
                        or car.radius <= 0.0 or car.car_length <= 0.0):
                    continue
                self._edge_projection(edge)
                self._apply_car_coverage(car, edge, None, order_s)
        return order_s

    def _edge_owner_mask(self, edge: _Edge) -> np.ndarray | None:
        """该边"拥有"的设备布尔掩码(设备的全局最近边 == 本边)。"""
        if self._owner_map is None:
            self._rebuild_owner_map()
        if self._owner_map is None:
            return None
        return self._owner_map == self._edge_index.get(edge.edge_id, -1)

    def _rebuild_owner_map(self) -> None:
        """重建每设备的最近边归属(纯几何事实, 只在几何变化时算一次)。"""
        n = self._num_devices
        eids = [e for e in self._edges if self._edges[e].length > 0.0]
        if n == 0 or not eids:
            self._owner_map = None
            self._edge_index = {}
            return
        self._edge_index = {eid: i for i, eid in enumerate(eids)}
        best_d = np.full(n, np.inf, dtype=np.float64)
        owner = np.full(n, -1, dtype=np.int32)
        for eid in eids:
            e = self._edges[eid]
            self._edge_projection(e)
            better = e.proj_d < best_d       # 严格小于: 保证平局归先注册的边(确定性)
            best_d = np.where(better, e.proj_d, best_d)
            owner = np.where(better, self._edge_index[eid], owner)
        self._owner_map = owner

    def _sparse_strength(self, strength_arr: np.ndarray) -> dict[str, float]:
        nz = np.flatnonzero(strength_arr > 0.0)
        return {
            self._device_ids[i]: float(strength_arr[i]) / 255.0
            for i in nz.tolist()
        }

    def _rebuild_projection(self, tr: _Train) -> None:
        """向后兼容: 重建某火车 home edge 的投影并同步别名字段。"""
        edge = self._edges.get(getattr(tr, "_home_edge_id", None))
        if edge is None:
            return
        self._edge_projection(edge)
        self._sync_train_legacy(tr)

    def _sync_train_legacy(self, tr: _Train) -> None:
        """把 home edge 几何/投影同步到火车向后兼容别名字段。"""
        edge = self._edges.get(tr._home_edge_id)
        tr._nose_s = tr.cars[0].s if tr.cars else 0.0
        tr.car_unit = tr.car_length
        if edge is None:
            tr.path_points = []
            tr.path_length = 0.0
            tr.cum = []
            tr.proj_s = tr.proj_d = tr.s_order = tr.s_sorted = tr.d_sorted = None
            return
        tr.path_points = [tuple(p) for p in edge.points]
        tr.path_length = edge.length
        tr.cum = list(edge.cum)
        tr.proj_s = edge.proj_s
        tr.proj_d = edge.proj_d
        tr.s_order = edge.s_order
        tr.s_sorted = edge.s_sorted
        tr.d_sorted = edge.d_sorted

    # ------------------------------------------------------------------
    #  帧结果缓存(向后兼容)
    # ------------------------------------------------------------------

    def _coverage_snapshot(self) -> np.ndarray:
        return self._coverage

    def route_mask(self) -> np.ndarray | None:
        """遮罩门掩码(供 effect_engine.set_route_mask); 无火车返回 None。"""
        if not self._trains:
            return None
        return self._coverage

    def route_mask_map(self) -> dict[str, int]:
        """按设备 id 的遮罩(唯一对外口径)。仅含当前页已注册设备。

        旧 route_mask() 返回数组(索引=引擎注册序), 与 effect 侧 fixtures 列表下标
        不是同一口径, 会作用到错误设备上(T1 病灶)。生产路径必须用本方法。
        结果按 coverage 变化缓存, 避免每帧多次重建 11382 项 dict。
        """
        sig = self._coverage.tobytes()
        if self._route_mask_map_cache is None or sig != self._mask_sig:
            cov = self._coverage_snapshot()
            # dict(zip(ids, cov.tolist())) 比逐项推导快约 2.5 倍(11382 项)
            self._route_mask_map_cache = dict(zip(self._device_ids, cov.tolist()))
            self._mask_sig = sig
        return self._route_mask_map_cache

    def _refresh_active_ids(self, order_s: np.ndarray) -> None:
        nz = np.flatnonzero(self._coverage >= 1)
        if nz.size > 0:
            nz_sorted = nz[np.argsort(order_s[nz], kind="stable")]
        else:
            nz_sorted = nz
        sig = nz_sorted.tobytes()
        if sig != self._active_sig:
            self._active_sig = sig
            self._active_ordered_ids = [
                self._device_ids[i] for i in nz_sorted.tolist()]

    def _reset_frame_cache(self) -> None:
        self._coverage = np.zeros(self._num_devices, dtype=np.uint8)
        self._active_ordered_ids = []
        self._active_sig = b""

    # ------------------------------------------------------------------
    #  CE 门控
    # ------------------------------------------------------------------

    def _ce_route_enabled(self) -> bool:
        if self._ce_env is None:
            raw = os.environ.get("QASCADE_CE_ROUTE", "1").strip().lower()
            self._ce_env = raw not in ("0", "false", "no", "off", "")
        return self._ce_env

    def _get_ce_route_fn(self):
        if self._ce_checked:
            return self._ce_route_fn
        self._ce_checked = True
        try:
            import qascade_ce  # render_lab 隔离区, 可选依赖
        except Exception as exc:  # pragma: no cover - 无 CE 环境
            logger.debug("qascade_ce import skipped: %s", exc)
            self._ce_route_fn = None
            return None
        self._ce_route_fn = getattr(qascade_ce, "ce_route_coverage", None)
        return self._ce_route_fn

    def _use_ce(self) -> bool:
        # 圆形激活圈语义修正: CE 内核仍是“沿轨区间+横向半径”的矩形激活,
        # 与用户要求的“以车厢为中心的圆”不一致。在 CE 内核升级前回退 numpy,
        # 保证激活圈/真实激活完全一致。
        return False

    def _ce_car_inputs(self) -> list[dict]:
        """映射所有车厢 → CE trains（每边 = 一条 CE path，path 缓存由调用方按 dirty 维护）。

        每节车厢按 _wrap_spans 产 1~2 条 car_count=1 的记录(闭环接缝环绕切分,
        T3/E4)。CE 语义 car i 覆盖 [nose_s-(i+1)*u, nose_s-i*u], 取 car_count=1,
        i=0 即 [nose_s-u, nose_s] → 令 nose_s=b, car_unit=b-a。
        """
        trains: list[dict] = []
        for tr in self._trains.values():
            for car in tr.cars:
                if car.car_length <= 0.0 or car.radius <= 0.0:
                    continue
                edge = self._edges.get(car.edge_id)
                if edge is None or edge.length <= 0.0:
                    continue
                spans = _wrap_spans(car.s - car.car_length, car.s,
                                   edge.length, edge.closed)
                for a, b in spans:
                    if b - a <= 0.0:
                        continue  # 零长 span(车尾在起点前)与 numpy 路径一致: 无覆盖
                    trains.append({
                        "path_id": car.edge_id,
                        "nose_s": float(b),
                        "car_unit": float(b - a),
                        "car_count": 1,
                        "radius": float(car.radius),
                    })
        return trains

    @staticmethod
    def _ce_entries_by_edge(entries: list[dict]) -> dict[str, list[dict]]:
        """把 CE 记录按 path_id(边)分组, 供按边调用 + 归属零化(T3b)。"""
        groups: dict[str, list[dict]] = {}
        for rec in entries:
            groups.setdefault(str(rec["path_id"]), []).append(rec)
        return groups

    def _compute_coverage_ce(self) -> tuple[np.ndarray, dict[str, float], np.ndarray]:
        # CE 内核仍是旧矩形激活语义; 圆形激活圈修正期间统一走 numpy, 保证一致。
        return self._compute_coverage_numpy()
        # 以下 CE 旧路径暂不启用(保留代码供 CE 内核升级后恢复)
        fn = self._ce_route_fn
        n = self._num_devices
        if n == 0 or fn is None:
            return self._compute_coverage_numpy()  # CE 绑定要求 n_dev>0; 无设备直接走 numpy
        acc = np.zeros(n, dtype=np.uint8)
        trains = self._ce_car_inputs()
        # 边数(路径数)超 CE MAX_PATHS=32 → 回退 numpy; 车厢条目超限则分批(见下)
        if not trains:
            return acc, {}, np.arange(n, dtype=np.float64)
        need_sync = self._ce_ctx_dirty
        if need_sync or getattr(self, "_ce_path_ids", None) is None:
            self._ce_path_ids = list(self._edges.keys())
            self._ce_paths = [list(e.points) for e in self._edges.values()]
        path_ids = self._ce_path_ids
        paths = self._ce_paths
        if len(paths) > 32:
            return self._compute_coverage_numpy()
        scratch = np.zeros(n, dtype=np.uint8)
        try:
            for eid, entries in self._ce_entries_by_edge(trains).items():
                edge = self._edges.get(eid)
                if edge is None:
                    continue
                own = self._edge_owner_mask(edge)  # T3b: 按边零化非归属设备
                # CE 内核 out 是覆盖写非累加, 且单次最多 32 条 → 分批 scratch + max
                for k in range(0, len(entries), 32):
                    chunk = entries[k:k + 32]
                    scratch.fill(0)
                    if need_sync:
                        fn(scratch, chunk, self._device_pos, path_ids, paths)
                        need_sync = False
                    else:
                        fn(scratch, chunk)
                    if own is not None:
                        scratch[~own] = 0
                    np.maximum(acc, scratch, out=acc)
            self._ce_ctx_dirty = False
        except Exception as exc:  # pragma: no cover - C 失败回退
            logger.warning("ce_route_coverage failed, fallback numpy: %s", exc)
            self._ce_ctx_dirty = True
            return self._compute_coverage_numpy()
        return acc, self._sparse_strength(acc), self._compute_order_s()
