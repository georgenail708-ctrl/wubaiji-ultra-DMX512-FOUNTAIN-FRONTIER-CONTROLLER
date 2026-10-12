"""
topview_reader.py
──────────────────────────────────────────────────────────────────────────────
DeepAqua TopView 拓扑读取与检测系统

核心职责：
  从 TopViewPanel.get_fixture_positions() 读取真实设备空间坐标，
  自动检测设备的拓扑类型，并将其转换为 AI 编舞系统可直接使用的
  空间语义数据。

设计原则：
  "最后启用的时候，选择的设备摆放方式从 TopView 读取，是什么样子就是什么样"

支持的拓扑类型：
  LINEAR      线性排列（一字形）
  ARC         弧线排列（弯曲的线）
  CIRCLE      圆形/环形排列
  GRID        矩阵/网格排列（水膜、LED 矩阵等）
  SCATTER     自由散点布局
  FOUNTAIN    直流跑泉（一排独立水柱，强调独立控制）
  FAN         扇形排列
  DUAL_ARC    双弧线（对称弧线）
  CLUSTER     多簇分布

输出：
  TopologyProfile — 包含拓扑类型、空间参数、设备排序、网格尺寸等
  DeviceNode      — 每台设备的完整空间语义（位置、排序索引、行列号等）
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

# ═══════════════════════════════════════════════════════════════════════════
#  1. 拓扑类型枚举
# ═══════════════════════════════════════════════════════════════════════════

class TopologyType(Enum):
    """设备空间拓扑类型"""
    LINEAR     = "linear"      # 线性一字形
    ARC        = "arc"         # 弧线形
    CIRCLE     = "circle"      # 圆形/环形
    GRID       = "grid"        # 矩阵/网格（水膜、矩阵喷泉等）
    FOUNTAIN   = "fountain"    # 直流跑泉（一排独立水柱）
    FAN        = "fan"         # 扇形
    DUAL_ARC   = "dual_arc"    # 双弧线（对称）
    CLUSTER    = "cluster"     # 多簇分布
    SCATTER    = "scatter"     # 自由散点（不规则）
    UNKNOWN    = "unknown"

# ═══════════════════════════════════════════════════════════════════════════
#  2. 设备节点数据结构
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceNode:
    """
    单台设备的完整空间语义节点。

    包含从 TopView 读取的原始坐标，以及由拓扑检测器计算出的
    空间语义参数（线性位置、极坐标、行列号等）。
    """
    # ── 原始数据（来自 TopView）──────────────────────────────────────────
    device_id: str = ""
    name: str = ""
    universe: int = 0
    start_channel: int = 0
    pos_x: float = 0.0          # TopView 原始 X 坐标（像素）
    pos_y: float = 0.0          # TopView 原始 Y 坐标（像素）

    # ── 归一化坐标（由 TopologyDetector 计算）────────────────────────────
    norm_x: float = 0.0         # 归一化 X [0,1]（左=0，右=1）
    norm_y: float = 0.0         # 归一化 Y [0,1]（上=0，下=1）

    # ── 线性排序（用于 1D 波形）──────────────────────────────────────────
    linear_index: int = 0       # 在线性排序中的索引（从左到右/从前到后）
    linear_phase: float = 0.0   # 在线性排列中的相位 [0,1]

    # ── 极坐标（用于圆形/弧线/扇形）─────────────────────────────────────
    angle_rad: float = 0.0      # 相对于中心的角度（弧度，-π 到 π）
    angle_norm: float = 0.0     # 归一化角度 [0,1]
    radius_norm: float = 0.0    # 归一化半径 [0,1]（0=中心，1=最外圈）

    # ── 矩阵坐标（用于网格/矩阵设备）────────────────────────────────────
    grid_row: int = -1          # 矩阵行号（-1=非矩阵）
    grid_col: int = -1          # 矩阵列号（-1=非矩阵）
    grid_row_norm: float = 0.0  # 归一化行位置 [0,1]
    grid_col_norm: float = 0.0  # 归一化列位置 [0,1]

    # ── 直流跑泉（用于独立水柱）─────────────────────────────────────────
    fountain_index: int = -1    # 跑泉水柱索引（-1=非跑泉）
    fountain_phase: float = 0.0 # 在跑泉排列中的相位 [0,1]

    # ── 簇信息（用于多簇布局）────────────────────────────────────────────
    cluster_id: int = -1        # 所属簇 ID（-1=无簇）
    cluster_local_index: int = 0 # 在簇内的索引

    # ── 通道能力（由 fixture_defs 填充）──────────────────────────────────
    has_motor: bool = True
    has_pump: bool = True
    has_light: bool = True
    has_valve: bool = True

@dataclass
class GridInfo:
    """矩阵拓扑的网格信息"""
    rows: int = 0               # 行数
    cols: int = 0               # 列数
    row_spacing: float = 0.0    # 行间距（像素）
    col_spacing: float = 0.0    # 列间距（像素）
    is_regular: bool = True     # 是否规则网格（等间距）

@dataclass
class TopologyProfile:
    """
    设备拓扑特征档案。

    由 TopologyDetector 自动生成，包含拓扑类型、空间参数、
    以及所有设备节点的完整空间语义。
    """
    # ── 拓扑类型 ─────────────────────────────────────────────────────────
    topo_type: TopologyType = TopologyType.UNKNOWN
    confidence: float = 0.0     # 检测置信度 [0,1]

    # ── 设备节点列表（按线性顺序排列）────────────────────────────────────
    nodes: List[DeviceNode] = field(default_factory=list)

    # ── 空间边界 ──────────────────────────────────────────────────────────
    center_x: float = 0.5       # 中心 X（归一化）
    center_y: float = 0.5       # 中心 Y（归一化）
    span_x: float = 1.0         # X 方向跨度（归一化）
    span_y: float = 1.0         # Y 方向跨度（归一化）

    # ── 矩阵信息（仅 GRID 拓扑有效）──────────────────────────────────────
    grid: Optional[GridInfo] = None

    # ── 对称性 ────────────────────────────────────────────────────────────
    has_x_symmetry: bool = False  # 是否左右对称
    has_y_symmetry: bool = False  # 是否前后对称
    has_radial_symmetry: bool = False  # 是否径向对称

    # ── 直流跑泉信息 ──────────────────────────────────────────────────────
    fountain_count: int = 0     # 跑泉水柱数量
    fountain_spacing: float = 0.0  # 水柱间距

    # ── 簇信息 ────────────────────────────────────────────────────────────
    cluster_count: int = 0
    cluster_sizes: List[int] = field(default_factory=list)

    def get_node(self, device_id: str) -> Optional[DeviceNode]:
        """根据设备 ID 获取节点"""
        for node in self.nodes:
            if node.device_id == device_id:
                return node
        return None

    def get_nodes_sorted_linear(self) -> List[DeviceNode]:
        """按线性顺序返回所有节点"""
        return sorted(self.nodes, key=lambda n: n.linear_phase)

    def get_nodes_by_row(self, row: int) -> List[DeviceNode]:
        """获取矩阵指定行的所有节点"""
        return sorted(
            [n for n in self.nodes if n.grid_row == row],
            key=lambda n: n.grid_col
        )

    def get_nodes_by_col(self, col: int) -> List[DeviceNode]:
        """获取矩阵指定列的所有节点"""
        return sorted(
            [n for n in self.nodes if n.grid_col == col],
            key=lambda n: n.grid_row
        )

    def get_nodes_in_cluster(self, cluster_id: int) -> List[DeviceNode]:
        """获取指定簇的所有节点"""
        return [n for n in self.nodes if n.cluster_id == cluster_id]


# ═══════════════════════════════════════════════════════════════════════════
#  3. TopViewReader：从 TopView 读取设备数据
# ═══════════════════════════════════════════════════════════════════════════

class TopViewReader:
    """
    TopView 数据读取器。

    从 TopViewPanel.get_fixture_positions() 读取设备的真实空间坐标，
    并转换为 DeepAqua AI 编舞系统可直接使用的格式。

    使用方式：
        reader = TopViewReader(topview_panel)
        raw_nodes = reader.read()
        profile = TopologyDetector().detect(raw_nodes)
    """

    def __init__(self, topview_panel=None):
        """
        Args:
            topview_panel: TopViewPanel 实例（可选，也可以直接传入数据）
        """
        self._topview = topview_panel

    def read(self) -> List[Dict[str, Any]]:
        """
        从 TopViewPanel 读取所有设备的位置数据。

        Returns:
            [{"id": str, "name": str, "pos_x": float, "pos_y": float, ...}]
        """
        if self._topview is None:
            return []
        try:
            return self._topview.get_fixture_positions()
        except Exception:
            return []

    def read_with_defs(
        self,
        fixture_defs: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        读取设备位置数据，并补充通道能力信息（来自 fixture_defs）。

        Args:
            fixture_defs: {fixture_id: {channels: [...]}} 设备定义字典

        Returns:
            增强后的设备数据列表
        """
        raw = self.read()
        if not fixture_defs:
            return raw

        result = []
        for entry in raw:
            enhanced = dict(entry)
            fid = entry.get("id", "")
            fdef = fixture_defs.get(fid, {})
            channels = fdef.get("channels", [])

            # 检测通道能力
            channel_types = {ch.get("type", "").lower() for ch in channels}
            enhanced["has_motor"] = any("motor" in t or "pan" in t or "tilt" in t for t in channel_types)
            enhanced["has_pump"] = any("pump" in t or "water" in t or "flow" in t for t in channel_types)
            enhanced["has_light"] = any("dimmer" in t or "color" in t or "rgb" in t or "light" in t for t in channel_types)
            enhanced["has_valve"] = any("valve" in t or "shutter" in t for t in channel_types)
            enhanced["channel_count"] = len(channels)
            result.append(enhanced)

        return result

    @staticmethod
    def from_list(positions: List[Dict[str, Any]]) -> 'TopViewReader':
        """
        从已有的位置数据列表创建 Reader（不需要 TopViewPanel）。

        用于测试或离线处理。
        """
        reader = TopViewReader(None)
        reader._static_data = positions
        original_read = reader.read

        def read_static():
            return reader._static_data
        reader.read = read_static
        return reader


# ═══════════════════════════════════════════════════════════════════════════
#  4. TopologyDetector：自动检测拓扑类型
# ═══════════════════════════════════════════════════════════════════════════

class TopologyDetector:
    """
    拓扑自动检测器。

    从设备的 (pos_x, pos_y) 坐标自动判断设备的空间排列类型，
    并计算每台设备的完整空间语义参数。

    检测算法：
    1. 计算空间统计特征（长宽比、方差、聚类等）
    2. 按特征匹配拓扑类型（置信度排序）
    3. 计算每台设备在对应拓扑中的语义坐标

    设计原则：
    - 不强制任何拓扑类型，完全由实际坐标决定
    - 多种拓扑同时评分，取最高置信度
    - 边界情况降级处理（如只有1台设备）
    """

    # 矩阵检测的对齐容差（归一化坐标）
    GRID_ALIGN_TOLERANCE = 0.08
    # 直流跑泉检测：长宽比阈值
    FOUNTAIN_ASPECT_RATIO = 4.0
    # 圆形检测：半径方差阈值
    CIRCLE_RADIUS_VARIANCE = 0.15
    # 线性检测：线性度阈值
    LINEAR_LINEARITY = 0.92

    def detect(self, raw_data: List[Dict[str, Any]]) -> TopologyProfile:
        """
        主检测接口：输入原始设备数据，输出完整拓扑档案。

        Args:
            raw_data: TopViewReader.read() 的输出

        Returns:
            TopologyProfile
        """
        if not raw_data:
            return TopologyProfile(topo_type=TopologyType.UNKNOWN)

        # 1. 提取坐标
        positions = [(float(d.get("pos_x", 0)), float(d.get("pos_y", 0))) for d in raw_data]
        n = len(positions)

        # 2. 归一化坐标
        norm_positions, bounds = self._normalize(positions)
        cx = sum(p[0] for p in norm_positions) / n
        cy = sum(p[1] for p in norm_positions) / n
        span_x = bounds["max_x"] - bounds["min_x"]
        span_y = bounds["max_y"] - bounds["min_y"]

        # 3. 单设备特殊处理
        if n == 1:
            node = self._make_node(raw_data[0], norm_positions[0], 0, n)
            return TopologyProfile(
                topo_type=TopologyType.SCATTER,
                confidence=1.0,
                nodes=[node],
                center_x=cx, center_y=cy,
                span_x=span_x, span_y=span_y,
            )

        # 4. 计算极坐标
        polar = self._compute_polar(norm_positions, cx, cy)

        # 5. 评分各拓扑类型
        scores = {
            TopologyType.GRID:     self._score_grid(norm_positions),
            TopologyType.CIRCLE:   self._score_circle(polar),
            TopologyType.LINEAR:   self._score_linear(norm_positions),
            TopologyType.ARC:      self._score_arc(norm_positions, polar),
            TopologyType.FOUNTAIN: self._score_fountain(norm_positions, span_x, span_y),
            TopologyType.FAN:      self._score_fan(polar),
            TopologyType.DUAL_ARC: self._score_dual_arc(norm_positions, polar),
            TopologyType.CLUSTER:  self._score_cluster(norm_positions),
        }

        # 6. 选取最高分拓扑
        best_type = max(scores, key=lambda t: scores[t])
        confidence = scores[best_type]

        # 7. 构建设备节点
        nodes = self._build_nodes(raw_data, norm_positions, polar, best_type, cx, cy)

        # 8. 构建拓扑档案
        profile = TopologyProfile(
            topo_type=best_type,
            confidence=confidence,
            nodes=nodes,
            center_x=cx,
            center_y=cy,
            span_x=span_x,
            span_y=span_y,
        )

        # 9. 填充拓扑特定信息
        if best_type == TopologyType.GRID:
            profile.grid = self._compute_grid_info(norm_positions)
            self._assign_grid_coords(profile)
        elif best_type == TopologyType.FOUNTAIN:
            profile.fountain_count = n
            profile.fountain_spacing = span_x / max(n - 1, 1)
            self._assign_fountain_coords(profile)
        elif best_type == TopologyType.CLUSTER:
            self._assign_cluster_coords(profile, norm_positions)

        # 10. 检测对称性
        profile.has_x_symmetry = self._detect_x_symmetry(norm_positions, cx)
        profile.has_y_symmetry = self._detect_y_symmetry(norm_positions, cy)
        profile.has_radial_symmetry = self._detect_radial_symmetry(polar)

        return profile

    # ─────────────────────────────────────────────────────────────────────
    #  坐标处理
    # ─────────────────────────────────────────────────────────────────────

    def _normalize(
        self, positions: List[Tuple[float, float]]
    ) -> Tuple[List[Tuple[float, float]], Dict]:
        """将像素坐标归一化到 [0,1]"""
        if not positions:
            return [], {}
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        range_x = max_x - min_x or 1.0
        range_y = max_y - min_y or 1.0
        norm = [((x - min_x) / range_x, (y - min_y) / range_y) for x, y in positions]
        return norm, {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y,
                      "range_x": range_x, "range_y": range_y}

    def _compute_polar(
        self,
        norm_positions: List[Tuple[float, float]],
        cx: float, cy: float,
    ) -> List[Dict]:
        """计算每台设备的极坐标"""
        result = []
        for x, y in norm_positions:
            dx, dy = x - cx, y - cy
            r = math.sqrt(dx * dx + dy * dy)
            theta = math.atan2(dy, dx)
            result.append({"r": r, "theta": theta, "dx": dx, "dy": dy})
        return result

    # ─────────────────────────────────────────────────────────────────────
    #  拓扑评分函数
    # ─────────────────────────────────────────────────────────────────────

    def _score_grid(self, norm_positions: List[Tuple[float, float]]) -> float:
        """
        矩阵评分：检测设备是否形成规则网格。
        方法：检查 X 坐标和 Y 坐标是否各自形成等间距的离散值组。
        """
        if len(norm_positions) < 4:
            return 0.0

        xs = sorted(set(round(p[0] / self.GRID_ALIGN_TOLERANCE) * self.GRID_ALIGN_TOLERANCE
                        for p in norm_positions))
        ys = sorted(set(round(p[1] / self.GRID_ALIGN_TOLERANCE) * self.GRID_ALIGN_TOLERANCE
                        for p in norm_positions))

        # 检测 X 方向的列对齐
        col_groups = {}
        for p in norm_positions:
            col_key = round(p[0] / self.GRID_ALIGN_TOLERANCE)
            col_groups.setdefault(col_key, []).append(p[1])

        row_groups = {}
        for p in norm_positions:
            row_key = round(p[1] / self.GRID_ALIGN_TOLERANCE)
            row_groups.setdefault(row_key, []).append(p[0])

        n_cols = len(col_groups)
        n_rows = len(row_groups)

        if n_cols < 2 or n_rows < 2:
            return 0.0

        # 检查每列的设备数是否相近（规则矩阵）
        col_sizes = [len(v) for v in col_groups.values()]
        row_sizes = [len(v) for v in row_groups.values()]
        col_uniformity = 1.0 - (max(col_sizes) - min(col_sizes)) / max(max(col_sizes), 1)
        row_uniformity = 1.0 - (max(row_sizes) - min(row_sizes)) / max(max(row_sizes), 1)

        # 检查总设备数是否接近 rows × cols
        expected = n_rows * n_cols
        actual = len(norm_positions)
        fill_ratio = actual / expected

        score = (col_uniformity * 0.3 + row_uniformity * 0.3 + min(fill_ratio, 1.0) * 0.4)
        return min(1.0, score)

    def _score_circle(self, polar: List[Dict]) -> float:
        """圆形评分：检测设备是否均匀分布在圆周上"""
        if len(polar) < 4:
            return 0.0
        radii = [p["r"] for p in polar]
        mean_r = sum(radii) / len(radii)
        if mean_r < 0.01:
            return 0.0
        variance = sum((r - mean_r) ** 2 for r in radii) / len(radii)
        cv = math.sqrt(variance) / mean_r  # 变异系数
        # 角度均匀性
        thetas = sorted(p["theta"] for p in polar)
        gaps = [thetas[i+1] - thetas[i] for i in range(len(thetas)-1)]
        gaps.append(thetas[0] + 2*math.pi - thetas[-1])
        expected_gap = 2 * math.pi / len(polar)
        gap_variance = sum((g - expected_gap)**2 for g in gaps) / len(gaps)
        gap_cv = math.sqrt(gap_variance) / expected_gap

        radius_score = max(0.0, 1.0 - cv / self.CIRCLE_RADIUS_VARIANCE)
        angle_score = max(0.0, 1.0 - gap_cv)
        return (radius_score * 0.5 + angle_score * 0.5)

    def _score_linear(self, norm_positions: List[Tuple[float, float]]) -> float:
        """线性评分：检测设备是否近似排列在一条直线上"""
        if len(norm_positions) < 3:
            return 0.8 if len(norm_positions) == 2 else 0.0
        xs = [p[0] for p in norm_positions]
        ys = [p[1] for p in norm_positions]
        n = len(norm_positions)
        # 线性回归
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        sxx = sum((x - mean_x)**2 for x in xs)
        syy = sum((y - mean_y)**2 for y in ys)
        sxy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
        if sxx < 1e-10 and syy < 1e-10:
            return 1.0  # 所有设备重叠
        if sxx < 1e-10 or syy < 1e-10:
            return 0.95  # 完美垂直或水平线
        r2 = sxy**2 / (sxx * syy)
        return min(1.0, r2)

    def _score_arc(
        self,
        norm_positions: List[Tuple[float, float]],
        polar: List[Dict],
    ) -> float:
        """弧线评分：检测设备是否排列在弧线上（非完整圆）"""
        circle_score = self._score_circle(polar)
        linear_score = self._score_linear(norm_positions)
        # 弧线 = 半径一致（类似圆）但角度不完整（非完整圆）
        if circle_score < 0.3:
            return 0.0
        thetas = sorted(p["theta"] for p in polar)
        arc_span = thetas[-1] - thetas[0] if len(thetas) > 1 else 0
        # 弧线：角度跨度在 30°~270° 之间
        if arc_span < math.pi / 6 or arc_span > 1.5 * math.pi:
            return circle_score * 0.5
        return circle_score * 0.8 + 0.2 * (1.0 - linear_score)

    def _score_fountain(
        self,
        norm_positions: List[Tuple[float, float]],
        span_x: float,
        span_y: float,
    ) -> float:
        """
        直流跑泉评分：检测设备是否为一排独立水柱。
        特征：线性排列 + 长宽比大 + 设备间距均匀。
        """
        linear_score = self._score_linear(norm_positions)
        if linear_score < 0.85:
            return 0.0
        if span_x < 1e-6 or span_y < 1e-6:
            aspect = 10.0
        else:
            aspect = max(span_x / span_y, span_y / span_x)
        # 跑泉：长宽比大，且设备数量 >= 3
        if aspect < self.FOUNTAIN_ASPECT_RATIO or len(norm_positions) < 3:
            return 0.0
        # 检查间距均匀性
        xs = sorted(p[0] for p in norm_positions)
        ys = sorted(p[1] for p in norm_positions)
        if span_x > span_y:
            gaps = [xs[i+1] - xs[i] for i in range(len(xs)-1)]
        else:
            gaps = [ys[i+1] - ys[i] for i in range(len(ys)-1)]
        if not gaps:
            return 0.0
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap < 1e-6:
            return 0.0
        gap_cv = math.sqrt(sum((g - mean_gap)**2 for g in gaps) / len(gaps)) / mean_gap
        uniformity = max(0.0, 1.0 - gap_cv)
        return linear_score * 0.4 + uniformity * 0.4 + min(1.0, aspect / 8.0) * 0.2

    def _score_fan(self, polar: List[Dict]) -> float:
        """扇形评分：检测设备是否呈扇形分布（多行弧线）"""
        if len(polar) < 6:
            return 0.0
        radii = [p["r"] for p in polar]
        mean_r = sum(radii) / len(radii)
        if mean_r < 0.01:
            return 0.0
        # 扇形特征：半径有多个不同层次（多行弧线）
        sorted_r = sorted(radii)
        r_range = sorted_r[-1] - sorted_r[0]
        if r_range < 0.1:
            return 0.0
        # 检测半径分层
        layers = self._detect_layers(sorted_r, tolerance=0.08)
        if layers < 2:
            return 0.0
        # 角度集中在某个扇区
        thetas = [p["theta"] for p in polar]
        theta_range = max(thetas) - min(thetas)
        if theta_range > math.pi:
            return 0.0  # 超过180°认为不是扇形
        return min(1.0, layers / 4.0) * 0.5 + (1.0 - theta_range / math.pi) * 0.5

    def _score_dual_arc(
        self,
        norm_positions: List[Tuple[float, float]],
        polar: List[Dict],
    ) -> float:
        """双弧线评分：检测是否为两条对称弧线"""
        if len(norm_positions) < 6:
            return 0.0
        # 按 Y 坐标分为两半，各自检测弧线
        ys = [p[1] for p in norm_positions]
        median_y = sorted(ys)[len(ys) // 2]
        upper = [(p, polar[i]) for i, p in enumerate(norm_positions) if p[1] <= median_y]
        lower = [(p, polar[i]) for i, p in enumerate(norm_positions) if p[1] > median_y]
        if len(upper) < 3 or len(lower) < 3:
            return 0.0
        upper_arc = self._score_arc([p for p, _ in upper], [po for _, po in upper])
        lower_arc = self._score_arc([p for p, _ in lower], [po for _, po in lower])
        return (upper_arc + lower_arc) / 2.0 * 0.8

    def _score_cluster(self, norm_positions: List[Tuple[float, float]]) -> float:
        """多簇评分：检测设备是否形成多个空间簇"""
        if len(norm_positions) < 6:
            return 0.0
        clusters = self._simple_cluster(norm_positions, threshold=0.25)
        if len(clusters) < 2:
            return 0.0
        # 多簇：每个簇至少有2台设备
        valid_clusters = [c for c in clusters if len(c) >= 2]
        if len(valid_clusters) < 2:
            return 0.0
        return min(1.0, len(valid_clusters) / 4.0) * 0.7 + 0.3

    # ─────────────────────────────────────────────────────────────────────
    #  节点构建
    # ─────────────────────────────────────────────────────────────────────

    def _make_node(
        self,
        raw: Dict[str, Any],
        norm_pos: Tuple[float, float],
        idx: int,
        total: int,
    ) -> DeviceNode:
        """创建基础设备节点"""
        node = DeviceNode(
            device_id=str(raw.get("id", f"dev_{idx}")),
            name=str(raw.get("name", f"Device {idx}")),
            universe=int(raw.get("universe", 0)),
            start_channel=int(raw.get("start_channel", 0)),
            pos_x=float(raw.get("pos_x", 0)),
            pos_y=float(raw.get("pos_y", 0)),
            norm_x=norm_pos[0],
            norm_y=norm_pos[1],
            has_motor=bool(raw.get("has_motor", True)),
            has_pump=bool(raw.get("has_pump", True)),
            has_light=bool(raw.get("has_light", True)),
            has_valve=bool(raw.get("has_valve", True)),
        )
        return node

    def _build_nodes(
        self,
        raw_data: List[Dict[str, Any]],
        norm_positions: List[Tuple[float, float]],
        polar: List[Dict],
        topo_type: TopologyType,
        cx: float, cy: float,
    ) -> List[DeviceNode]:
        """构建所有设备节点，填充拓扑相关的空间语义"""
        n = len(raw_data)
        nodes = []

        # 计算线性排序（按主轴方向）
        if topo_type in (TopologyType.LINEAR, TopologyType.FOUNTAIN):
            # 按 X 或 Y 排序（取跨度更大的轴）
            xs = [p[0] for p in norm_positions]
            ys = [p[1] for p in norm_positions]
            if max(xs) - min(xs) >= max(ys) - min(ys):
                linear_order = sorted(range(n), key=lambda i: norm_positions[i][0])
            else:
                linear_order = sorted(range(n), key=lambda i: norm_positions[i][1])
        elif topo_type in (TopologyType.CIRCLE, TopologyType.ARC):
            # 按角度排序
            linear_order = sorted(range(n), key=lambda i: polar[i]["theta"])
        else:
            # 默认：按 X 主轴排序
            linear_order = sorted(range(n), key=lambda i: norm_positions[i][0])

        # 构建索引映射
        linear_rank = {orig_idx: rank for rank, orig_idx in enumerate(linear_order)}

        for i, raw in enumerate(raw_data):
            node = self._make_node(raw, norm_positions[i], i, n)

            # 线性相位
            rank = linear_rank[i]
            node.linear_index = rank
            node.linear_phase = rank / max(n - 1, 1)

            # 极坐标
            node.angle_rad = polar[i]["theta"]
            node.angle_norm = (polar[i]["theta"] + math.pi) / (2 * math.pi)
            max_r = max(p["r"] for p in polar) if polar else 1.0
            node.radius_norm = polar[i]["r"] / max_r if max_r > 0 else 0.0

            nodes.append(node)

        return nodes

    # ─────────────────────────────────────────────────────────────────────
    #  拓扑特定坐标分配
    # ─────────────────────────────────────────────────────────────────────

    def _compute_grid_info(self, norm_positions: List[Tuple[float, float]]) -> GridInfo:
        """计算矩阵网格信息"""
        col_groups: Dict[int, List[float]] = {}
        row_groups: Dict[int, List[float]] = {}
        for p in norm_positions:
            col_key = round(p[0] / self.GRID_ALIGN_TOLERANCE)
            row_key = round(p[1] / self.GRID_ALIGN_TOLERANCE)
            col_groups.setdefault(col_key, []).append(p[1])
            row_groups.setdefault(row_key, []).append(p[0])

        n_cols = len(col_groups)
        n_rows = len(row_groups)

        # 计算间距
        sorted_col_keys = sorted(col_groups.keys())
        sorted_row_keys = sorted(row_groups.keys())
        col_spacing = 0.0
        row_spacing = 0.0
        if len(sorted_col_keys) > 1:
            col_spacing = (sorted_col_keys[-1] - sorted_col_keys[0]) * self.GRID_ALIGN_TOLERANCE / (n_cols - 1)
        if len(sorted_row_keys) > 1:
            row_spacing = (sorted_row_keys[-1] - sorted_row_keys[0]) * self.GRID_ALIGN_TOLERANCE / (n_rows - 1)

        return GridInfo(
            rows=n_rows,
            cols=n_cols,
            row_spacing=row_spacing,
            col_spacing=col_spacing,
            is_regular=(n_rows * n_cols == len(norm_positions)),
        )

    def _assign_grid_coords(self, profile: TopologyProfile) -> None:
        """为矩阵拓扑的节点分配行列坐标"""
        if not profile.nodes or not profile.grid:
            return

        # 按 Y 坐标分组（行），按 X 坐标分组（列）
        row_groups: Dict[int, List[int]] = {}
        col_groups: Dict[int, List[int]] = {}

        for i, node in enumerate(profile.nodes):
            row_key = round(node.norm_y / self.GRID_ALIGN_TOLERANCE)
            col_key = round(node.norm_x / self.GRID_ALIGN_TOLERANCE)
            row_groups.setdefault(row_key, []).append(i)
            col_groups.setdefault(col_key, []).append(i)

        # 分配行号
        sorted_row_keys = sorted(row_groups.keys())
        sorted_col_keys = sorted(col_groups.keys())
        row_map = {k: r for r, k in enumerate(sorted_row_keys)}
        col_map = {k: c for c, k in enumerate(sorted_col_keys)}

        n_rows = len(sorted_row_keys)
        n_cols = len(sorted_col_keys)

        for node in profile.nodes:
            row_key = round(node.norm_y / self.GRID_ALIGN_TOLERANCE)
            col_key = round(node.norm_x / self.GRID_ALIGN_TOLERANCE)
            node.grid_row = row_map.get(row_key, 0)
            node.grid_col = col_map.get(col_key, 0)
            node.grid_row_norm = node.grid_row / max(n_rows - 1, 1)
            node.grid_col_norm = node.grid_col / max(n_cols - 1, 1)

    def _assign_fountain_coords(self, profile: TopologyProfile) -> None:
        """为直流跑泉节点分配水柱索引"""
        sorted_nodes = sorted(profile.nodes, key=lambda n: n.linear_phase)
        for i, node in enumerate(sorted_nodes):
            node.fountain_index = i
            node.fountain_phase = i / max(len(sorted_nodes) - 1, 1)

    def _assign_cluster_coords(
        self,
        profile: TopologyProfile,
        norm_positions: List[Tuple[float, float]],
    ) -> None:
        """为多簇布局节点分配簇坐标"""
        clusters = self._simple_cluster(norm_positions, threshold=0.25)
        # 构建 position → cluster_id 映射
        pos_to_cluster: Dict[int, int] = {}
        for cluster_id, cluster_indices in enumerate(clusters):
            for idx in cluster_indices:
                pos_to_cluster[idx] = cluster_id

        cluster_local: Dict[int, int] = {}
        for i, node in enumerate(profile.nodes):
            cid = pos_to_cluster.get(i, 0)
            node.cluster_id = cid
            local_idx = cluster_local.get(cid, 0)
            node.cluster_local_index = local_idx
            cluster_local[cid] = local_idx + 1

        profile.cluster_count = len(clusters)
        profile.cluster_sizes = [len(c) for c in clusters]

    # ─────────────────────────────────────────────────────────────────────
    #  对称性检测
    # ─────────────────────────────────────────────────────────────────────

    def _detect_x_symmetry(
        self,
        norm_positions: List[Tuple[float, float]],
        cx: float,
        tolerance: float = 0.1,
    ) -> bool:
        """检测左右对称性"""
        for x, y in norm_positions:
            mirror_x = 2 * cx - x
            has_mirror = any(
                abs(p[0] - mirror_x) < tolerance and abs(p[1] - y) < tolerance
                for p in norm_positions
            )
            if not has_mirror:
                return False
        return True

    def _detect_y_symmetry(
        self,
        norm_positions: List[Tuple[float, float]],
        cy: float,
        tolerance: float = 0.1,
    ) -> bool:
        """检测前后对称性"""
        for x, y in norm_positions:
            mirror_y = 2 * cy - y
            has_mirror = any(
                abs(p[0] - x) < tolerance and abs(p[1] - mirror_y) < tolerance
                for p in norm_positions
            )
            if not has_mirror:
                return False
        return True

    def _detect_radial_symmetry(self, polar: List[Dict], tolerance: float = 0.15) -> bool:
        """检测径向对称性（旋转对称）"""
        if len(polar) < 4:
            return False
        radii = [p["r"] for p in polar]
        mean_r = sum(radii) / len(radii)
        if mean_r < 0.01:
            return False
        r_cv = math.sqrt(sum((r - mean_r)**2 for r in radii) / len(radii)) / mean_r
        return r_cv < tolerance

    # ─────────────────────────────────────────────────────────────────────
    #  工具函数
    # ─────────────────────────────────────────────────────────────────────

    def _detect_layers(self, sorted_values: List[float], tolerance: float = 0.08) -> int:
        """检测数值序列中的层次数量"""
        if not sorted_values:
            return 0
        layers = 1
        for i in range(1, len(sorted_values)):
            if sorted_values[i] - sorted_values[i-1] > tolerance:
                layers += 1
        return layers

    def _simple_cluster(
        self,
        norm_positions: List[Tuple[float, float]],
        threshold: float = 0.25,
    ) -> List[List[int]]:
        """简单的空间聚类（基于距离阈值）"""
        n = len(norm_positions)
        if n == 0:
            return []
        assigned = [-1] * n
        cluster_id = 0
        for i in range(n):
            if assigned[i] >= 0:
                continue
            assigned[i] = cluster_id
            # BFS 扩展
            queue = [i]
            while queue:
                curr = queue.pop(0)
                for j in range(n):
                    if assigned[j] >= 0:
                        continue
                    dx = norm_positions[curr][0] - norm_positions[j][0]
                    dy = norm_positions[curr][1] - norm_positions[j][1]
                    if math.sqrt(dx*dx + dy*dy) < threshold:
                        assigned[j] = cluster_id
                        queue.append(j)
            cluster_id += 1
        clusters: Dict[int, List[int]] = {}
        for i, cid in enumerate(assigned):
            clusters.setdefault(cid, []).append(i)
        return list(clusters.values())


# ═══════════════════════════════════════════════════════════════════════════
#  5. TopologyAwareWaveGenerator：基于拓扑的 2D 波形生成器
# ═══════════════════════════════════════════════════════════════════════════

class TopologyAwareWaveGenerator:
    """
    拓扑感知波形生成器。

    根据 TopologyProfile 中的设备空间语义，为每台设备计算
    在当前时刻的目标值。

    支持的波形模式（按拓扑类型）：

    线性/弧线/直流跑泉（1D）：
      - sine_wave:    正弦行波（从左到右）
      - pulse_chase:  脉冲追逐
      - fountain_melody: 旋律音高→水柱高度（直流跑泉专用）
      - rise_fall:    渐升渐降

    圆形/环形（极坐标）：
      - radial_pulse: 径向脉冲（从中心向外扩散）
      - arc_wave:     弧线行波（沿圆周旋转）
      - spiral:       螺旋波

    矩阵（2D 网格）：
      - row_wave:     行波（每行依次触发）
      - col_wave:     列波（每列依次触发）
      - diagonal:     对角线波
      - checkerboard: 棋盘交替
      - diamond:      菱形扩散（从中心向四角）
      - ripple:       水波纹（从中心向外扩散）

    自由布局（2D 任意）：
      - spatial_sine: 空间正弦（基于 X 坐标）
      - radial_expand: 径向扩张（从中心向外）
      - cluster_pulse: 簇脉冲（各簇独立触发）
    """

    def compute(
        self,
        profile: TopologyProfile,
        wave_mode: str,
        phase: float,
        energy: float = 0.7,
        tension: float = 0.3,
        melody_pitch: float = 0.5,
        melody_contour: float = 0.0,
    ) -> Dict[str, float]:
        """
        计算所有设备的当前目标值。

        Args:
            profile:        拓扑档案
            wave_mode:      波形模式
            phase:          当前相位 [0,1]（由 BPM 驱动）
            energy:         音频能量 [0,1]
            tension:        和弦张力 [0,1]
            melody_pitch:   旋律音高归一化 [0,1]
            melody_contour: 旋律走向 [-1,1]

        Returns:
            {device_id: value [0,1]}
        """
        amplitude = 0.4 + 0.6 * energy
        result = {}

        for node in profile.nodes:
            if wave_mode == "fountain_melody":
                # 直流跑泉：旋律音高映射到水柱高度
                v = self._fountain_melody(node, melody_pitch, melody_contour, phase)
            elif wave_mode == "row_wave":
                v = self._row_wave(node, phase, amplitude)
            elif wave_mode == "col_wave":
                v = self._col_wave(node, phase, amplitude)
            elif wave_mode == "checkerboard":
                v = self._checkerboard(node, phase, amplitude)
            elif wave_mode == "diamond":
                v = self._diamond(node, phase, amplitude)
            elif wave_mode == "ripple":
                v = self._ripple(node, phase, amplitude)
            elif wave_mode == "diagonal":
                v = self._diagonal(node, phase, amplitude)
            elif wave_mode == "radial_pulse":
                v = self._radial_pulse(node, phase, amplitude)
            elif wave_mode == "arc_wave":
                v = self._arc_wave(node, phase, amplitude)
            elif wave_mode == "spiral":
                v = self._spiral(node, phase, amplitude)
            elif wave_mode == "cluster_pulse":
                v = self._cluster_pulse(node, phase, amplitude, profile.cluster_count)
            elif wave_mode == "spatial_sine":
                v = self._spatial_sine(node, phase, amplitude)
            elif wave_mode == "radial_expand":
                v = self._radial_expand(node, phase, amplitude)
            else:
                # 默认：基于线性相位的正弦行波
                v = self._sine_wave(node, phase, amplitude)

            result[node.device_id] = max(0.0, min(1.0, v))

        return result

    def get_best_wave_mode(
        self,
        profile: TopologyProfile,
        action_type: str,
        energy: float,
        tension: float,
    ) -> str:
        """
        根据拓扑类型和动作类型自动选择最佳波形模式。
        """
        topo = profile.topo_type

        if topo == TopologyType.FOUNTAIN:
            return "fountain_melody"
        elif topo == TopologyType.GRID:
            if action_type in ("wave", "chase"):
                return "row_wave" if energy > 0.5 else "col_wave"
            elif action_type in ("pulse", "beat"):
                return "checkerboard"
            elif action_type in ("expand", "fountain", "peak"):
                return "diamond"
            elif action_type in ("ripple", "radial"):
                return "ripple"
            else:
                return "diagonal"
        elif topo in (TopologyType.CIRCLE, TopologyType.ARC):
            if action_type in ("wave", "chase"):
                return "arc_wave"
            elif action_type in ("expand", "radial"):
                return "radial_pulse"
            else:
                return "spiral"
        elif topo == TopologyType.CLUSTER:
            return "cluster_pulse"
        else:
            # 线性/弧线/散点：默认正弦行波
            return "spatial_sine"

    # ─────────────────────────────────────────────────────────────────────
    #  1D 波形
    # ─────────────────────────────────────────────────────────────────────

    def _sine_wave(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """正弦行波（基于线性相位）"""
        t = (node.linear_phase + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _spatial_sine(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """空间正弦（基于 X 坐标）"""
        t = (node.norm_x + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _fountain_melody(
        self,
        node: DeviceNode,
        melody_pitch: float,
        melody_contour: float,
        phase: float,
    ) -> float:
        """
        直流跑泉旋律映射。

        旋律音高决定哪根水柱最高，相邻水柱按距离衰减。
        旋律走向决定水柱的倾斜方向。
        """
        if node.fountain_index < 0:
            return melody_pitch
        # 目标水柱位置（由旋律音高决定）
        target_phase = melody_pitch
        # 当前水柱与目标的距离
        dist = abs(node.fountain_phase - target_phase)
        dist = min(dist, 1.0 - dist)  # 环形距离
        # 高斯衰减
        sigma = 0.15 + 0.1 * abs(melody_contour)  # 走向越大，扩散越宽
        v = math.exp(-(dist ** 2) / (2 * sigma ** 2))
        # 基础高度 + 旋律高度
        base = 0.2
        return base + (1.0 - base) * v * (0.5 + 0.5 * melody_pitch)

    # ─────────────────────────────────────────────────────────────────────
    #  2D 矩阵波形
    # ─────────────────────────────────────────────────────────────────────

    def _row_wave(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """行波：每行依次触发（从上到下）"""
        t = (node.grid_row_norm + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _col_wave(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """列波：每列依次触发（从左到右）"""
        t = (node.grid_col_norm + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _checkerboard(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """棋盘交替：奇偶行列交替触发"""
        parity = (node.grid_row + node.grid_col) % 2
        beat = (math.sin(2 * math.pi * phase) + 1) / 2
        if parity == 0:
            return 0.2 + 0.8 * amplitude * beat
        else:
            return 0.2 + 0.8 * amplitude * (1.0 - beat)

    def _diamond(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """菱形扩散：从中心向四角扩散"""
        # 曼哈顿距离（归一化）
        dist = abs(node.grid_row_norm - 0.5) + abs(node.grid_col_norm - 0.5)
        t = (dist + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _ripple(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """水波纹：从中心向外扩散（欧几里得距离）"""
        dist = math.sqrt((node.grid_row_norm - 0.5)**2 + (node.grid_col_norm - 0.5)**2)
        t = (dist * 2 + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _diagonal(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """对角线波：沿对角线方向传播"""
        diag = (node.grid_row_norm + node.grid_col_norm) / 2.0
        t = (diag + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    # ─────────────────────────────────────────────────────────────────────
    #  极坐标波形
    # ─────────────────────────────────────────────────────────────────────

    def _radial_pulse(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """径向脉冲：从中心向外扩散"""
        t = (node.radius_norm + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _arc_wave(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """弧线行波：沿圆周旋转"""
        t = (node.angle_norm + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _spiral(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """螺旋波：角度+半径共同决定"""
        t = (node.angle_norm * 0.7 + node.radius_norm * 0.3 + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    def _radial_expand(self, node: DeviceNode, phase: float, amplitude: float) -> float:
        """径向扩张：从中心向外（自由布局版）"""
        t = (node.radius_norm + phase) % 1.0
        return 0.5 + 0.5 * amplitude * math.sin(2 * math.pi * t)

    # ─────────────────────────────────────────────────────────────────────
    #  簇波形
    # ─────────────────────────────────────────────────────────────────────

    def _cluster_pulse(
        self,
        node: DeviceNode,
        phase: float,
        amplitude: float,
        cluster_count: int,
    ) -> float:
        """簇脉冲：各簇依次触发"""
        if node.cluster_id < 0 or cluster_count <= 0:
            return self._sine_wave(node, phase, amplitude)
        cluster_phase = node.cluster_id / max(cluster_count, 1)
        t = (cluster_phase + phase) % 1.0
        local_t = node.cluster_local_index / max(10, 1)
        v = math.sin(2 * math.pi * (t + local_t * 0.1))
        return 0.5 + 0.5 * amplitude * v


# ═══════════════════════════════════════════════════════════════════════════
#  6. 便捷工厂函数
# ═══════════════════════════════════════════════════════════════════════════

def build_topology_from_topview(
    topview_panel=None,
    raw_positions: Optional[List[Dict]] = None,
    fixture_defs: Optional[Dict] = None,
) -> TopologyProfile:
    """
    从 TopView 构建完整拓扑档案的便捷函数。

    .. deprecated::
        无调用者，特性未接入主链路；请直接使用 TopologyDetector.detect()。
        保留仅作为公共 API 占位，后续可安全删除。

    Args:
        topview_panel:  TopViewPanel 实例（与 raw_positions 二选一）
        raw_positions:  直接传入位置数据列表（与 topview_panel 二选一）
        fixture_defs:   设备定义字典（可选，用于填充通道能力）

    Returns:
        TopologyProfile — 完整拓扑档案，可直接传入 CoherentChoreographer
    """
    if raw_positions is not None:
        reader = TopViewReader.from_list(raw_positions)
    else:
        reader = TopViewReader(topview_panel)

    raw_data = reader.read_with_defs(fixture_defs)
    detector = TopologyDetector()
    return detector.detect(raw_data)
