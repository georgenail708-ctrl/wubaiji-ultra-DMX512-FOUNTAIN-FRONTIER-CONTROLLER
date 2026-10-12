"""
project_profile — 项目画像提取

托管"像人一样编曲"的精准根基：人编曲前会先看清"这个项目有哪几种设备、每种怎么布的"，
这些信息在做项目时已天然留在工程里（配接=设备类型、编组=语义分组、TopView坐标=几何）。
本模块把这些读成结构化画像，供编曲总指挥决策。

纯 core，无 PyQt 依赖。输入 = Project 活对象（或其 to_dict）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


# ── 设备能力标签（从配接通道反推）──────────────────────────────────────────
CAP_MOTOR = "motor"      # 有 motor_x/motor_y → 数控电机，可走路径
CAP_PUMP = "pump"        # 有 pump → 水泵，可控水柱高度
CAP_VALVE = "valve"      # 有 valve → 电磁阀，开关型
CAP_RGB = "rgb"          # 有 R/G/B → 彩色灯
CAP_DIMMER = "dimmer"    # 有 dimmer → 亮度灯


@dataclass
class GroupProfile:
    """单个编组的画像。"""
    group_id: str = ""
    name: str = ""
    member_ids: List[str] = field(default_factory=list)
    count: int = 0
    device_type: str = ""         # 该组成员的主导设备类型(def名),用于声部精确归类
    # 几何
    shape: str = "unknown"        # "ring"(环/圆) / "line"(排/线) / "area"(散布/面) / "small"
    radius: float = 0.0           # 环/圆时的半径
    centroid: Tuple[float, float] = (0.0, 0.0)
    # 能力（该组成员普遍具备的能力）
    caps: List[str] = field(default_factory=list)
    # 语义（从组名推断的角色提示）
    semantic_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id, "name": self.name, "count": self.count,
            "device_type": self.device_type,
            "shape": self.shape, "radius": round(self.radius, 1),
            "caps": list(self.caps), "semantic_tags": list(self.semantic_tags),
        }


@dataclass
class ProjectProfile:
    """整个项目的画像。"""
    # 设备类型构成：def 名 → {count, caps}
    device_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 各能力的设备总数
    cap_counts: Dict[str, int] = field(default_factory=dict)
    # 编组画像
    groups: List[GroupProfile] = field(default_factory=list)
    # 整体布局拓扑摘要
    total_fixtures: int = 0
    n_rings: int = 0
    n_lines: int = 0
    has_matrix: bool = False
    layout_kind: str = "unknown"   # "multi-ring"(多环) / "linear"(排布) / "scattered"(散布) / "mixed"

    def to_dict(self) -> dict:
        return {
            "device_types": self.device_types,
            "cap_counts": dict(self.cap_counts),
            "total_fixtures": self.total_fixtures,
            "n_rings": self.n_rings, "n_lines": self.n_lines,
            "has_matrix": self.has_matrix, "layout_kind": self.layout_kind,
            "groups": [g.to_dict() for g in self.groups],
        }

    def summary_text(self) -> str:
        """人类可读的一句话画像，用于实时消息栏。"""
        caps = self.cap_counts
        parts = []
        if caps.get(CAP_MOTOR): parts.append(f"数控电机×{caps[CAP_MOTOR]}")
        if caps.get(CAP_PUMP): parts.append(f"泵×{caps[CAP_PUMP]}")
        if caps.get(CAP_VALVE): parts.append(f"阀×{caps[CAP_VALVE]}")
        if caps.get(CAP_RGB): parts.append(f"RGB灯×{caps[CAP_RGB]}")
        layout = {"multi-ring": "多环布局", "linear": "排布", "scattered": "散布",
                  "mixed": "混合布局"}.get(self.layout_kind, "布局未知")
        return (f"{self.total_fixtures}台设备 · {layout}"
                f"（{self.n_rings}环/{self.n_lines}排）· " + "、".join(parts))


# ── 几何判定 ────────────────────────────────────────────────────────────
def _shape_of(pts: List[Tuple[float, float]]) -> Tuple[str, float, Tuple[float, float]]:
    """从坐标点判定形态。返回 (shape, radius, centroid)。"""
    n = len(pts)
    if n < 3:
        cx = sum(p[0] for p in pts) / n if n else 0.0
        cy = sum(p[1] for p in pts) / n if n else 0.0
        return "small", 0.0, (cx, cy)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = sum(xs) / n
    cy = sum(ys) / n
    rs = [math.hypot(x - cx, y - cy) for x, y in pts]
    rm = sum(rs) / n
    if rm < 1e-6:
        return "small", 0.0, (cx, cy)
    cv = (sum((r - rm) ** 2 for r in rs) / n) ** 0.5 / rm
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if cv < 0.25:
        return "ring", rm, (cx, cy)
    if w > 2.5 * h or h > 2.5 * w:
        return "line", 0.0, (cx, cy)
    return "area", 0.0, (cx, cy)


def _caps_from_channels(channels: List[str]) -> List[str]:
    """从配接通道列表反推能力标签。"""
    chs = set(channels or [])
    caps = []
    if "motor_x" in chs or "motor_y" in chs:
        caps.append(CAP_MOTOR)
    if "pump" in chs:
        caps.append(CAP_PUMP)
    if "valve" in chs:
        caps.append(CAP_VALVE)
    if {"R", "G", "B"} <= chs:
        caps.append(CAP_RGB)
    if "dimmer" in chs:
        caps.append(CAP_DIMMER)
    return caps


def _semantic_tags(name: str) -> List[str]:
    """从组名推断语义角色提示（中文喷泉项目常见命名）。"""
    tags = []
    n = name or ""
    if any(k in n for k in ("环", "圈", "ring")):
        tags.append("ring")
    if any(k in n for k in ("圆", "园", "circle")):
        tags.append("circle")
    if any(k in n for k in ("中心", "中间", "center", "中央")):
        tags.append("center")
    if any(k in n for k in ("外", "outer")):
        tags.append("outer")
    if any(k in n for k in ("内", "inner")):
        tags.append("inner")
    if any(k in n for k in ("喷泉", "fountain", "喷")):
        tags.append("fountain")
    if any(k in n for k in ("LED", "灯", "light")):
        tags.append("light")
    if any(k in n for k in ("矩阵", "matrix", "屏")):
        tags.append("matrix")
    return tags


# ── 主入口 ──────────────────────────────────────────────────────────────
def _iter_pages_positions(topview_state: dict) -> List[Dict[str, Tuple[float, float]]]:
    """从 topview_state 各页提取 {fixture_id: (x,y)} 坐标表（只取可见且 x/y 都有效的）。"""
    out = []
    for pg in (topview_state or {}).get("pages", []) or []:
        ff = pg.get("fixtures") or {}
        pos = {}
        if isinstance(ff, dict):
            for fid, v in ff.items():
                if not (isinstance(v, dict) and v.get("visible", True)):
                    continue
                x = v.get("x")
                y = v.get("y")
                if x is None or y is None:
                    continue
                try:
                    pos[fid] = (float(x), float(y))
                except (TypeError, ValueError):
                    continue
        if pos:
            out.append(pos)
    return out


def _spread(pts: List[Tuple[float, float]]) -> float:
    if not pts:
        return 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def build_project_profile(project: Any) -> ProjectProfile:
    """从 Project 活对象构建画像。容错：字段缺失不崩。"""
    prof = ProjectProfile()

    # ── 配接：设备定义 → 能力 ──
    fixture_defs = getattr(project, "fixture_defs", None) or []
    def_caps: Dict[str, List[str]] = {}
    def_name: Dict[str, str] = {}
    for fd in fixture_defs:
        did = getattr(fd, "id", None) or (fd.get("id") if isinstance(fd, dict) else None)
        chs = getattr(fd, "channels", None)
        if chs is None and isinstance(fd, dict):
            chs = fd.get("channels", [])
        nm = getattr(fd, "name", None) or (fd.get("name") if isinstance(fd, dict) else "")
        if did is not None:
            def_caps[did] = _caps_from_channels(chs or [])
            def_name[did] = nm or "?"

    # ── 设备实例：按类型计数 + 能力汇总 ──
    fixtures = getattr(project, "fixtures", None) or []
    fx_def = {}
    from collections import Counter
    type_cnt: Counter = Counter()
    cap_cnt: Counter = Counter()
    for fx in fixtures:
        fid = getattr(fx, "id", None) or (fx.get("id") if isinstance(fx, dict) else None)
        did = getattr(fx, "def_id", None) or (fx.get("def_id") if isinstance(fx, dict) else None)
        fx_def[fid] = did
        nm = def_name.get(did, "?")
        type_cnt[nm] += 1
        for c in def_caps.get(did, []):
            cap_cnt[c] += 1
    prof.total_fixtures = len(fixtures)
    prof.cap_counts = dict(cap_cnt)
    for nm, c in type_cnt.items():
        # 找该类型的能力（任一该 def 的能力）
        caps = []
        for did, dn in def_name.items():
            if dn == nm:
                caps = def_caps.get(did, [])
                break
        prof.device_types[nm] = {"count": c, "caps": caps}

    # ── 几何：每组用"该组最展开的页"算形态 ──
    pages_pos = _iter_pages_positions(getattr(project, "topview_state", None) or {})
    groups = getattr(project, "fixture_groups", None) or []
    n_ring = n_line = 0
    for g in groups:
        gid = g.get("id", "") if isinstance(g, dict) else getattr(g, "id", "")
        gname = g.get("name", "") if isinstance(g, dict) else getattr(g, "name", "")
        mids = (g.get("fixture_ids", []) if isinstance(g, dict)
                else getattr(g, "fixture_ids", [])) or []
        gp = GroupProfile(group_id=gid, name=gname, member_ids=list(mids), count=len(mids))
        # 能力 = 成员能力并集
        gcaps = set()
        for mid in mids:
            for c in def_caps.get(fx_def.get(mid), []):
                gcaps.add(c)
        gp.caps = sorted(gcaps)
        gp.semantic_tags = _semantic_tags(gname)
        # 主导设备类型:成员所属 def 名的众数(用于声部精确归类,避免按cap跨类型串组)
        from collections import Counter as _Cnt
        _dt = _Cnt(def_name.get(fx_def.get(mid), "") for mid in mids if fx_def.get(mid))
        gp.device_type = _dt.most_common(1)[0][0] if _dt else ""
        # 几何：选成员最展开的页
        best_pts = None
        best_spread = -1.0
        for pos in pages_pos:
            pts = [pos[i] for i in mids if i in pos]
            if len(pts) >= 3:
                s = _spread(pts)
                if s > best_spread:
                    best_spread = s
                    best_pts = pts
        if best_pts:
            shape, radius, centroid = _shape_of(best_pts)
            gp.shape = shape
            gp.radius = radius
            gp.centroid = centroid
            if shape == "ring":
                n_ring += 1
            elif shape == "line":
                n_line += 1
        prof.groups.append(gp)

    prof.n_rings = n_ring
    prof.n_lines = n_line
    # matrix：topview 页里有 pattern_matrices，或有矩阵语义组
    tv = getattr(project, "topview_state", None) or {}
    prof.has_matrix = any(
        (pg.get("pattern_matrices") for pg in (tv.get("pages", []) or []))
    ) or any("matrix" in g.semantic_tags for g in prof.groups)

    # 整体布局判定
    if n_ring >= 2:
        prof.layout_kind = "multi-ring"
    elif n_line >= 2 and n_ring == 0:
        prof.layout_kind = "linear"
    elif n_ring == 0 and n_line == 0:
        prof.layout_kind = "scattered"
    else:
        prof.layout_kind = "mixed"

    return prof
