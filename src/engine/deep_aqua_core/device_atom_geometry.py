"""
device_atom_geometry.py — DeepAqua 设备元空间构型语言
======================================================
定义设备元的"编珠子"逻辑：如何从物理设备池中按照空间关系
组织成具有表现力的设备元。

支持 12 种空间构型算子：
  1.  linear          — 线性排列（一维顺序）
  2.  linear_uniform  — 均匀间隔线性排列
  3.  symmetric       — 对称排列（左右/上下镜像）
  4.  split_equal     — 等分拆分（N 等分组）
  5.  split_interleave— 交错拆分（奇偶分组）
  6.  merge           — 合并（多组合并为一组）
  7.  ring            — 环形排列
  8.  grid            — 网格排列（二维矩阵）
  9.  radial          — 放射状排列（中心 + 外围）
  10. spiral          — 螺旋排列
  11. checkerboard    — 棋盘格排列（交替分组）
  12. cross_axis      — 十字轴排列（两轴交叉）

每个算子接受：
  - devices: 设备 ID 列表（物理顺序）
  - positions: 可选的物理坐标 [(x, y), ...]
  - params: 算子参数字典

返回：
  - List[List[int]]  每个子列表是一个设备元的设备 ID 集合
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════
#  构型结果
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GeometryResult:
    """
    空间构型算子的输出结果。
    包含分组后的设备元列表及其空间元数据。
    """
    # 设备元列表：每个元素是一个设备元（设备 ID 列表）
    groups: List[List[int]] = field(default_factory=list)
    # 每个设备元的中心坐标（可选）
    group_centers: List[Tuple[float, float]] = field(default_factory=list)
    # 算子名称
    operator: str = "linear"
    # 构型描述（用于调试和日志）
    description: str = ""
    # 是否满足对称性
    is_symmetric: bool = False
    # 是否均匀间隔
    is_uniform: bool = False
    # 实际使用的设备数
    device_count: int = 0

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def total_devices(self) -> int:
        return sum(len(g) for g in self.groups)


# ═══════════════════════════════════════════════════════════════════════════
#  构型算子基类
# ═══════════════════════════════════════════════════════════════════════════

class GeometryOperator:
    """空间构型算子基类"""

    name: str = "base"

    def apply(
        self,
        devices: List[int],
        positions: Optional[List[Tuple[float, float]]] = None,
        **params: Any,
    ) -> GeometryResult:
        raise NotImplementedError

    def _default_positions(
        self,
        devices: List[int],
        positions: Optional[List[Tuple[float, float]]],
    ) -> List[Tuple[float, float]]:
        """如果没有提供坐标，生成默认线性坐标"""
        if positions and len(positions) >= len(devices):
            return positions[:len(devices)]
        n = len(devices)
        return [(float(i) / max(n - 1, 1), 0.0) for i in range(n)]

    def _center_of(self, group: List[int], all_devices: List[int],
                   positions: List[Tuple[float, float]]) -> Tuple[float, float]:
        """计算一组设备的中心坐标"""
        if not group:
            return (0.0, 0.0)
        idx_map = {d: i for i, d in enumerate(all_devices)}
        xs = [positions[idx_map[d]][0] for d in group if d in idx_map]
        ys = [positions[idx_map[d]][1] for d in group if d in idx_map]
        if not xs:
            return (0.0, 0.0)
        return (sum(xs) / len(xs), sum(ys) / len(ys))


# ═══════════════════════════════════════════════════════════════════════════
#  12 种构型算子实现
# ═══════════════════════════════════════════════════════════════════════════

class LinearOperator(GeometryOperator):
    """
    1. linear — 线性排列
    将所有设备按顺序组成一个设备元。
    params: (无)
    """
    name = "linear"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        pos = self._default_positions(devices, positions)
        return GeometryResult(
            groups=[list(devices)],
            group_centers=[self._center_of(devices, devices, pos)],
            operator=self.name,
            description=f"线性排列 {len(devices)} 台设备",
            is_uniform=False,
            device_count=len(devices),
        )


class LinearUniformOperator(GeometryOperator):
    """
    2. linear_uniform — 均匀间隔线性排列
    从设备池中按均匀间隔选取 n 台设备组成一个设备元。
    params:
      n: 目标设备数（默认=全部）
      step: 间隔步长（默认自动计算）
    """
    name = "linear_uniform"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = int(params.get("n", len(devices)))
        n = min(n, len(devices))
        if n <= 1:
            selected = devices[:1]
        else:
            # 均匀间隔采样
            step = (len(devices) - 1) / (n - 1)
            selected = [devices[round(i * step)] for i in range(n)]
        pos = self._default_positions(devices, positions)
        return GeometryResult(
            groups=[selected],
            group_centers=[self._center_of(selected, devices, pos)],
            operator=self.name,
            description=f"均匀间隔线性排列 {n}/{len(devices)} 台设备",
            is_uniform=True,
            device_count=n,
        )


class SymmetricOperator(GeometryOperator):
    """
    3. symmetric — 对称排列
    将设备分为 n_groups 组，每组包含对称位置的设备。
    params:
      n_groups: 分组数（默认 2，即左右对称）
      axis: 对称轴 "horizontal"/"vertical"/"auto"（默认 "auto"）
    """
    name = "symmetric"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n_groups = int(params.get("n_groups", 2))
        n_groups = max(1, min(n_groups, len(devices)))
        pos = self._default_positions(devices, positions)

        # 按坐标排序，然后对称分组
        indexed = sorted(enumerate(devices), key=lambda x: pos[x[0]][0])
        sorted_devs = [d for _, d in indexed]

        n = len(sorted_devs)
        group_size = n // n_groups
        groups = []
        centers = []
        for i in range(n_groups):
            start = i * group_size
            end = start + group_size if i < n_groups - 1 else n
            g = sorted_devs[start:end]
            groups.append(g)
            centers.append(self._center_of(g, devices, pos))

        return GeometryResult(
            groups=groups,
            group_centers=centers,
            operator=self.name,
            description=f"对称排列 {n_groups} 组，每组约 {group_size} 台",
            is_symmetric=True,
            is_uniform=True,
            device_count=len(devices),
        )


class SplitEqualOperator(GeometryOperator):
    """
    4. split_equal — 等分拆分
    将设备均匀拆分为 n_groups 组。
    params:
      n_groups: 分组数（默认 2）
      allow_remainder: 是否允许余数组（默认 True）
    """
    name = "split_equal"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n_groups = int(params.get("n_groups", 2))
        n_groups = max(1, min(n_groups, len(devices)))
        allow_remainder = bool(params.get("allow_remainder", True))
        pos = self._default_positions(devices, positions)

        n = len(devices)
        base_size = n // n_groups
        remainder = n % n_groups

        groups = []
        centers = []
        idx = 0
        for i in range(n_groups):
            size = base_size + (1 if i < remainder and allow_remainder else 0)
            g = devices[idx: idx + size]
            if g:
                groups.append(g)
                centers.append(self._center_of(g, devices, pos))
            idx += size

        return GeometryResult(
            groups=groups,
            group_centers=centers,
            operator=self.name,
            description=f"等分拆分 {n_groups} 组，每组 {base_size}~{base_size+1} 台",
            is_uniform=True,
            device_count=n,
        )


class SplitInterleaveOperator(GeometryOperator):
    """
    5. split_interleave — 交错拆分
    将设备按奇偶（或 N 路交错）分组。
    params:
      n_groups: 交错路数（默认 2，即奇偶分组）
    """
    name = "split_interleave"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n_groups = int(params.get("n_groups", 2))
        n_groups = max(2, min(n_groups, len(devices)))
        pos = self._default_positions(devices, positions)

        groups = [[] for _ in range(n_groups)]
        for i, d in enumerate(devices):
            groups[i % n_groups].append(d)

        centers = [self._center_of(g, devices, pos) for g in groups]
        return GeometryResult(
            groups=[g for g in groups if g],
            group_centers=[c for g, c in zip(groups, centers) if g],
            operator=self.name,
            description=f"交错拆分 {n_groups} 路（奇偶分组）",
            is_uniform=True,
            device_count=len(devices),
        )


class MergeOperator(GeometryOperator):
    """
    6. merge — 合并
    将多个已有的设备元合并为一个大设备元。
    params:
      groups: List[List[int]]，要合并的设备元列表
    """
    name = "merge"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        input_groups = params.get("groups", None)
        pos = self._default_positions(devices, positions)

        if input_groups:
            # 合并所有输入组
            merged = []
            for g in input_groups:
                merged.extend(g)
            # 去重保序
            seen = set()
            merged_unique = []
            for d in merged:
                if d not in seen:
                    seen.add(d)
                    merged_unique.append(d)
            return GeometryResult(
                groups=[merged_unique],
                group_centers=[self._center_of(merged_unique, devices, pos)],
                operator=self.name,
                description=f"合并 {len(input_groups)} 组 → 1 组 {len(merged_unique)} 台",
                device_count=len(merged_unique),
            )
        else:
            # 直接合并所有设备
            return GeometryResult(
                groups=[list(devices)],
                group_centers=[self._center_of(devices, devices, pos)],
                operator=self.name,
                description=f"合并所有 {len(devices)} 台设备",
                device_count=len(devices),
            )


class RingOperator(GeometryOperator):
    """
    7. ring — 环形排列
    将设备按环形顺序组织，支持多圈。
    params:
      n_rings: 环数（默认 1）
      start_angle: 起始角度（度，默认 0）
    """
    name = "ring"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n_rings = int(params.get("n_rings", 1))
        n_rings = max(1, min(n_rings, len(devices)))
        pos = self._default_positions(devices, positions)

        n = len(devices)
        if n_rings == 1:
            # 单环：所有设备组成一个环形设备元
            return GeometryResult(
                groups=[list(devices)],
                group_centers=[(0.5, 0.5)],
                operator=self.name,
                description=f"环形排列 {n} 台设备",
                is_uniform=True,
                device_count=n,
            )
        else:
            # 多环：按圈数均匀分配
            per_ring = n // n_rings
            groups = []
            centers = []
            for i in range(n_rings):
                start = i * per_ring
                end = start + per_ring if i < n_rings - 1 else n
                g = devices[start:end]
                groups.append(g)
                r = (i + 1) / n_rings
                centers.append((0.5, 0.5))  # 同心圆中心相同
            return GeometryResult(
                groups=groups,
                group_centers=centers,
                operator=self.name,
                description=f"多环排列 {n_rings} 圈",
                is_uniform=True,
                device_count=n,
            )


class GridOperator(GeometryOperator):
    """
    8. grid — 网格排列（二维矩阵）
    将设备按 cols × rows 矩阵组织，支持按行/列分组。
    params:
      cols: 列数（默认自动计算为最接近的平方根）
      rows: 行数（默认自动计算）
      group_by: "row"/"col"/"all"（默认 "all"）
    """
    name = "grid"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = len(devices)
        cols = int(params.get("cols", max(1, round(math.sqrt(n)))))
        cols = max(1, min(cols, n))
        rows = int(params.get("rows", math.ceil(n / cols)))
        group_by = str(params.get("group_by", "all"))
        pos = self._default_positions(devices, positions)

        if group_by == "all":
            # 所有设备组成一个矩阵设备元
            return GeometryResult(
                groups=[list(devices)],
                group_centers=[self._center_of(devices, devices, pos)],
                operator=self.name,
                description=f"网格排列 {cols}×{rows}（共 {n} 台）",
                is_uniform=True,
                device_count=n,
            )
        elif group_by == "row":
            # 按行分组
            groups = []
            centers = []
            for r in range(rows):
                start = r * cols
                end = min(start + cols, n)
                g = devices[start:end]
                if g:
                    groups.append(g)
                    centers.append(self._center_of(g, devices, pos))
            return GeometryResult(
                groups=groups,
                group_centers=centers,
                operator=self.name,
                description=f"网格按行分组 {rows} 行 × {cols} 列",
                is_uniform=True,
                device_count=n,
            )
        elif group_by == "col":
            # 按列分组
            groups = []
            centers = []
            for c in range(cols):
                g = [devices[r * cols + c] for r in range(rows) if r * cols + c < n]
                if g:
                    groups.append(g)
                    centers.append(self._center_of(g, devices, pos))
            return GeometryResult(
                groups=groups,
                group_centers=centers,
                operator=self.name,
                description=f"网格按列分组 {cols} 列 × {rows} 行",
                is_uniform=True,
                device_count=n,
            )
        else:
            return GeometryResult(
                groups=[list(devices)],
                group_centers=[self._center_of(devices, devices, pos)],
                operator=self.name,
                description=f"网格排列 {n} 台",
                device_count=n,
            )


class RadialOperator(GeometryOperator):
    """
    9. radial — 放射状排列
    将设备分为中心组和外围组（或多层放射）。
    params:
      n_layers: 层数（默认 2：中心 + 外围）
      center_count: 中心设备数（默认 1）
    """
    name = "radial"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = len(devices)
        n_layers = int(params.get("n_layers", 2))
        n_layers = max(1, min(n_layers, n))
        center_count = int(params.get("center_count", max(1, n // (n_layers * 3))))
        pos = self._default_positions(devices, positions)

        if n_layers == 1:
            return GeometryResult(
                groups=[list(devices)],
                group_centers=[self._center_of(devices, devices, pos)],
                operator=self.name,
                description=f"放射状排列（单层）{n} 台",
                device_count=n,
            )

        # 中心组
        center_group = devices[:center_count]
        remaining = devices[center_count:]
        per_layer = max(1, len(remaining) // (n_layers - 1))

        groups = [center_group]
        centers = [self._center_of(center_group, devices, pos)]

        for i in range(n_layers - 1):
            start = i * per_layer
            end = start + per_layer if i < n_layers - 2 else len(remaining)
            g = remaining[start:end]
            if g:
                groups.append(g)
                centers.append(self._center_of(g, devices, pos))

        return GeometryResult(
            groups=groups,
            group_centers=centers,
            operator=self.name,
            description=f"放射状排列 {n_layers} 层（中心 {center_count} 台）",
            device_count=n,
        )


class SpiralOperator(GeometryOperator):
    """
    10. spiral — 螺旋排列
    将设备按螺旋顺序重新排列，支持顺时针/逆时针。
    params:
      clockwise: 是否顺时针（默认 True）
      n_arms: 螺旋臂数（默认 1）
    """
    name = "spiral"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = len(devices)
        clockwise = bool(params.get("clockwise", True))
        n_arms = int(params.get("n_arms", 1))
        n_arms = max(1, min(n_arms, n))
        pos = self._default_positions(devices, positions)

        if n_arms == 1:
            # 单臂螺旋：按螺旋顺序重排（基于坐标角度排序）
            if positions:
                cx = sum(p[0] for p in pos) / len(pos)
                cy = sum(p[1] for p in pos) / len(pos)
                angles = [math.atan2(pos[i][1] - cy, pos[i][0] - cx) for i in range(n)]
                if not clockwise:
                    angles = [-a for a in angles]
                sorted_devs = [d for _, d in sorted(zip(angles, devices))]
            else:
                sorted_devs = list(devices) if clockwise else list(reversed(devices))

            return GeometryResult(
                groups=[sorted_devs],
                group_centers=[self._center_of(sorted_devs, devices, pos)],
                operator=self.name,
                description=f"螺旋排列 {n} 台（{'顺' if clockwise else '逆'}时针）",
                device_count=n,
            )
        else:
            # 多臂螺旋：交错分配到各臂
            arms = [[] for _ in range(n_arms)]
            for i, d in enumerate(devices):
                arms[i % n_arms].append(d)
            groups = [a for a in arms if a]
            centers = [self._center_of(g, devices, pos) for g in groups]
            return GeometryResult(
                groups=groups,
                group_centers=centers,
                operator=self.name,
                description=f"螺旋排列 {n_arms} 臂",
                device_count=n,
            )


class CheckerboardOperator(GeometryOperator):
    """
    11. checkerboard — 棋盘格排列
    将设备按棋盘格模式分为两组（黑格/白格）。
    params:
      cols: 列数（默认自动）
      invert: 是否反转（默认 False）
    """
    name = "checkerboard"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = len(devices)
        cols = int(params.get("cols", max(1, round(math.sqrt(n)))))
        cols = max(1, min(cols, n))
        invert = bool(params.get("invert", False))
        pos = self._default_positions(devices, positions)

        group_a = []  # 黑格
        group_b = []  # 白格
        for i, d in enumerate(devices):
            row = i // cols
            col = i % cols
            is_black = (row + col) % 2 == 0
            if is_black ^ invert:
                group_a.append(d)
            else:
                group_b.append(d)

        groups = [g for g in [group_a, group_b] if g]
        centers = [self._center_of(g, devices, pos) for g in groups]
        return GeometryResult(
            groups=groups,
            group_centers=centers,
            operator=self.name,
            description=f"棋盘格排列 {cols} 列（黑 {len(group_a)} 台，白 {len(group_b)} 台）",
            device_count=n,
        )


class CrossAxisOperator(GeometryOperator):
    """
    12. cross_axis — 十字轴排列
    将设备分为水平轴组和垂直轴组（或 X/Y 两轴交叉）。
    params:
      n_h: 水平轴设备数（默认 n//2）
      n_v: 垂直轴设备数（默认 n//2）
      interleave: 是否交错分配（默认 True）
    """
    name = "cross_axis"

    def apply(self, devices, positions=None, **params):
        if not devices:
            return GeometryResult(operator=self.name)
        n = len(devices)
        interleave = bool(params.get("interleave", True))
        n_h = int(params.get("n_h", n // 2))
        n_v = int(params.get("n_v", n - n_h))
        pos = self._default_positions(devices, positions)

        if interleave:
            # 交错分配：奇数→水平轴，偶数→垂直轴
            h_group = [d for i, d in enumerate(devices) if i % 2 == 0][:n_h]
            v_group = [d for i, d in enumerate(devices) if i % 2 == 1][:n_v]
        else:
            # 顺序分配：前半→水平轴，后半→垂直轴
            h_group = devices[:n_h]
            v_group = devices[n_h:n_h + n_v]

        groups = [g for g in [h_group, v_group] if g]
        centers = [self._center_of(g, devices, pos) for g in groups]
        return GeometryResult(
            groups=groups,
            group_centers=centers,
            operator=self.name,
            description=f"十字轴排列（水平 {len(h_group)} 台，垂直 {len(v_group)} 台）",
            is_symmetric=True,
            device_count=n,
        )


# ═══════════════════════════════════════════════════════════════════════════
#  构型语言引擎
# ═══════════════════════════════════════════════════════════════════════════

class DeviceAtomGeometry:
    """
    设备元空间构型语言引擎。

    提供统一的接口调用 12 种空间构型算子，支持：
    - 单算子调用
    - 算子链式组合（先拆分再对称等）
    - 动态重构（根据音乐参数实时切换构型）

    使用方式：
        geo = DeviceAtomGeometry()
        result = geo.apply("symmetric", devices=[0,1,2,3,4,5,6,7], n_groups=2)
        # result.groups → [[0,2,4,6], [1,3,5,7]]

        # 链式：先等分再交错
        result = geo.chain(["split_equal", "split_interleave"],
                           devices=devices, n_groups=2)
    """

    # 所有算子注册表
    _OPERATORS: Dict[str, GeometryOperator] = {
        "linear":           LinearOperator(),
        "linear_uniform":   LinearUniformOperator(),
        "symmetric":        SymmetricOperator(),
        "split_equal":      SplitEqualOperator(),
        "split_interleave": SplitInterleaveOperator(),
        "merge":            MergeOperator(),
        "ring":             RingOperator(),
        "grid":             GridOperator(),
        "radial":           RadialOperator(),
        "spiral":           SpiralOperator(),
        "checkerboard":     CheckerboardOperator(),
        "cross_axis":       CrossAxisOperator(),
        # 别名
        "any":              LinearOperator(),
        "cross":            CrossAxisOperator(),
        "matrix":           GridOperator(),
        "interleave":       SplitInterleaveOperator(),
    }

    def apply(
        self,
        operator: str,
        devices: List[int],
        positions: Optional[List[Tuple[float, float]]] = None,
        **params: Any,
    ) -> GeometryResult:
        """
        应用指定的空间构型算子。

        Args:
            operator: 算子名称（见上方 12 种）
            devices: 设备 ID 列表
            positions: 可选的物理坐标列表
            **params: 算子参数

        Returns:
            GeometryResult
        """
        op = self._OPERATORS.get(operator)
        if op is None:
            # 未知算子，回退到线性
            op = self._OPERATORS["linear"]
        return op.apply(devices, positions, **params)

    def chain(
        self,
        operators: List[str],
        devices: List[int],
        positions: Optional[List[Tuple[float, float]]] = None,
        params_list: Optional[List[Dict[str, Any]]] = None,
    ) -> GeometryResult:
        """
        链式应用多个算子。
        第一个算子的输出作为第二个算子的输入（展平后）。

        Args:
            operators: 算子名称列表
            devices: 初始设备列表
            positions: 物理坐标
            params_list: 每个算子的参数字典列表

        Returns:
            最终 GeometryResult
        """
        if not operators:
            return self.apply("linear", devices, positions)

        params_list = params_list or [{} for _ in operators]
        current_devices = list(devices)
        result = None

        for op_name, op_params in zip(operators, params_list):
            result = self.apply(op_name, current_devices, positions, **op_params)
            # 展平所有组作为下一步的输入
            current_devices = [d for g in result.groups for d in g]

        return result

    def reconstruct(
        self,
        from_op: str,
        to_op: str,
        devices: List[int],
        positions: Optional[List[Tuple[float, float]]] = None,
        from_params: Optional[Dict[str, Any]] = None,
        to_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[GeometryResult, GeometryResult]:
        """
        动态重构：从一种构型切换到另一种构型。
        返回 (旧构型, 新构型)，供过渡动画使用。

        典型用例：
            # 从"两组 2x2 交叉"重构为"一组 4x4 网格"
            old, new = geo.reconstruct("cross_axis", "grid", devices,
                                       from_params={"n_h":4,"n_v":4},
                                       to_params={"cols":4})
        """
        old_result = self.apply(from_op, devices, positions, **(from_params or {}))
        new_result = self.apply(to_op, devices, positions, **(to_params or {}))
        return old_result, new_result

    def suggest_operator(
        self,
        n_devices: int,
        action_class_name: str,
        energy: float = 0.5,
        rhythm: float = 0.5,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        根据设备数、动作类型和音频特征，自动推荐最合适的构型算子。

        Returns:
            (operator_name, params)
        """
        ac = action_class_name.lower()

        # 矩阵类
        if "matrix" in ac:
            cols = max(2, round(math.sqrt(n_devices)))
            return "grid", {"cols": cols}

        # 交叉类
        if "cross" in ac or "alternate" in ac:
            return "cross_axis", {"interleave": True}

        # 龙摆/串联类
        if "dragon" in ac or "cascade" in ac or "chase" in ac:
            return "linear_uniform", {"n": n_devices}

        # 螺旋/环形类
        if "spiral" in ac or "ring" in ac or "circle" in ac:
            return "ring", {}

        # 爆发/涟漪类
        if "burst" in ac or "ripple" in ac or "explode" in ac:
            return "radial", {"n_layers": max(2, n_devices // 4)}

        # 脉冲类（高能量时多组，低能量时单组）
        if "pulse" in ac:
            if energy > 0.6:
                n_groups = min(4, n_devices // 2)
                return "split_equal", {"n_groups": max(2, n_groups)}
            else:
                return "linear", {}

        # 呼吸类（高旋律时对称，低旋律时线性）
        if "breath" in ac or "fader" in ac:
            if rhythm > 0.5:
                return "symmetric", {"n_groups": 2}
            else:
                return "linear", {}

        # 波浪类（高能量时棋盘，低能量时线性）
        if "wave" in ac:
            if energy > 0.7 and n_devices >= 4:
                return "checkerboard", {"cols": max(2, n_devices // 2)}
            else:
                return "linear_uniform", {"n": n_devices}

        # 默认：线性
        return "linear", {}

    @property
    def available_operators(self) -> List[str]:
        """返回所有可用算子名称"""
        return [k for k in self._OPERATORS.keys() if k not in ("any", "cross", "matrix", "interleave")]
