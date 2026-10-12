"""TopView 真实布局读取 —— 把工程设备摆位变成 ML 编曲可用的空间坐标。

这是"实验室读取 TopView 布局来自动规划编曲"的落地：编曲的空间语义（低到高、
双翼、中心扩散、对角扫掠…）只有在【真实摆位】上才有意义——8×5 的数学网格
和现场 3 排弧形跑泉完全是两种效果。

数据流：
    TopView 摆位坐标（画布单位≈cm）
      → 包围盒归一化 (u,v) ∈ [0,1]²
      → ML 光场 16×16 双线性采样（field_mapper 消费）
      → 每台设备的真实 DMX 通道值

坐标来源优先级：
    1) TopViewPanel.get_all_fixture_positions()（跨 Sheet 全量、已是画布坐标）
    2) project.fixtures 的 pos_x/pos_y（无 TopView 面板时的兜底）
    3) 未摆放（全 0）→ 按网格回退排布，保证总有可用布局

输出的 kind 判定（决定光场第 4 通道语义）：
    pump  = 有 pump 通道 → 第 4 通道是水柱高度
    dim   = 只有 R/G/B(/W)  → 第 4 通道是亮度
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# 通道类型判定（与 src/core/models.ChannelType.value 对齐，此处用字面量容错）
_PUMP_CH = ("pump",)
_LIGHT_CH = ("r", "g", "b", "w", "white", "dimmer", "dim")
_MOTOR_CH = ("motor_x", "motor_y", "motor_z")


@dataclass
class LabFixture:
    """一台参与实验室编曲的真实设备。"""
    fid: str
    name: str
    universe: int
    start_channel: int
    channels: list[str]                 # 定义通道表（顺序即偏移）
    u: float = 0.5                      # 归一化空间坐标
    v: float = 0.5
    kind: str = "dim"                   # dim | pump
    world_x: float = 0.0
    world_z: float = 0.0
    raw_x: float = 0.0                  # 原始画布坐标（预览用）
    raw_y: float = 0.0

    @property
    def has_pump(self) -> bool:
        return any(str(c).lower() in _PUMP_CH for c in self.channels)

    @property
    def is_light(self) -> bool:
        return any(str(c).lower() in _LIGHT_CH for c in self.channels)


@dataclass
class LayoutSpec:
    """一次布局快照（可缓存、可序列化给 3D 预览）。"""
    fixtures: list[LabFixture] = field(default_factory=list)
    gx: int = 8
    gy: int = 5
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # minx,miny,maxx,maxy
    source: str = "none"                # topview | project | grid

    @property
    def count(self) -> int:
        return len(self.fixtures)

    @property
    def has_pump(self) -> bool:
        return any(f.has_pump for f in self.fixtures)

    def to_preview_json(self) -> dict:
        """给内嵌 3D 预览的摆位数据（预览即现场）。"""
        return {
            "gridSize": [self.gx, self.gy],
            "source": self.source,
            "positions": [
                {"id": f.fid, "name": f.name, "x": f.u, "z": f.v,
                 "kind": f.kind, "universe": f.universe,
                 "startChannel": f.start_channel, "channels": list(f.channels)}
                for f in self.fixtures
            ],
        }


def _is_identity(x: float, y: float) -> bool:
    """未摆放判定：坐标几乎为 0（TopView 默认未拖入）。"""
    return abs(x) < 1e-9 and abs(y) < 1e-9


def _kind_of(channels: list[str]) -> str:
    low = [str(c).lower() for c in channels]
    if any(c in _PUMP_CH for c in low):
        return "pump"
    return "dim"


def _grid_fallback(n: int) -> tuple[list[tuple[float, float]], int, int]:
    """未摆放设备的兜底网格排布（保证布局永远可用）。"""
    if n <= 0:
        return [], 1, 1
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, int(math.ceil(n / cols)))
    pts = []
    for i in range(n):
        r, c = divmod(i, cols)
        u = 0.5 if cols == 1 else c / (cols - 1)
        v = 0.5 if rows == 1 else r / (rows - 1)
        pts.append((u, v))
    return pts, cols, rows


def _estimate_grid(us: list[float], vs: list[float]) -> tuple[int, int]:
    """按去重后的行/列坐标数估算 gx×gy（给 LLM 描述与预览用）。"""
    def _uniq(vals: list[float]) -> int:
        if not vals:
            return 1
        tol = 0.02
        s = sorted(vals)
        n = 1
        for a, b in zip(s, s[1:]):
            if abs(b - a) > tol:
                n += 1
        return n
    return max(1, _uniq(us)), max(1, _uniq(vs))


def read_layout(topview=None, project=None,
                fixtures: Optional[list] = None) -> LayoutSpec:
    """读取真实设备布局 → LayoutSpec。

    参数（按可用性传其一）:
      topview  TopViewPanel 实例（首选，含画布坐标）
      project  Project（无 TopView 时的字段来源）
      fixtures 直接给定 FixtureInstance 列表（测试用）
    """
    # ── 1. 取设备实例与坐标 ──────────────────────────────────────
    pos: dict[str, tuple[float, float]] = {}
    insts: list = []
    source = "none"

    if fixtures is not None:
        insts = list(fixtures)
    elif topview is not None:
        try:
            rows = topview.get_all_fixture_positions()
        except Exception:
            rows = []
        if rows:
            source = "topview"
            for r in rows:
                fid = str(r.get("fixture_id") or r.get("id") or "")
                if not fid:
                    continue
                pos[fid] = (float(r.get("pos_x", r.get("x", 0.0)) or 0.0),
                            float(r.get("pos_y", r.get("y", 0.0)) or 0.0))
            try:
                insts = list(topview.fixtures_by_id().values())
            except Exception:
                insts = []
    if not insts and project is not None:
        insts = list(getattr(project, "fixtures", []) or [])
        if source == "none" and insts:
            source = "project"

    defs = {}
    if project is not None:
        for d in getattr(project, "fixture_defs", []) or []:
            defs[str(getattr(d, "id", ""))] = d

    # ── 2. 组装 LabFixture（过滤非灯光/非泵设备）────────────────
    out: list[LabFixture] = []
    for fi in insts:
        fid = str(getattr(fi, "id", ""))
        if not fid:
            continue
        d = defs.get(str(getattr(fi, "def_id", "")))
        channels = list(getattr(d, "channels", []) or []) if d is not None else []
        lf = LabFixture(
            fid=fid,
            name=str(getattr(fi, "name", fid)),
            universe=int(getattr(fi, "universe", 1) or 1),
            start_channel=int(getattr(fi, "start_channel", 1) or 1),
            channels=channels,
            kind=_kind_of(channels),
            world_x=float(getattr(fi, "world_x", 0.0) or 0.0),
            world_z=float(getattr(fi, "world_z", 0.0) or 0.0),
        )
        # 坐标：TopView 优先，其次实例自带
        if fid in pos:
            lx, ly = pos[fid]
        else:
            lx = float(getattr(fi, "pos_x", 0.0) or 0.0)
            ly = float(getattr(fi, "pos_y", 0.0) or 0.0)
        lf.raw_x, lf.raw_y = lx, ly
        # 只保留"能出光/能出水"的设备（LED 灯组、跑泉、喷泉），跳过纯电机/阀门
        if lf.is_light or lf.has_pump:
            out.append(lf)

    # ── 3. 归一化 → (u,v) ───────────────────────────────────────
    placed = [f for f in out if not _is_identity(f.raw_x, f.raw_y)]
    if placed and len(placed) == len(out):
        xs = [f.raw_x for f in out]
        ys = [f.raw_y for f in out]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        dx = mxx - mnx
        dy = mxy - mny
        for f in out:
            # TopView 的 y 是向下（屏幕坐标）；归一化后 v 直接用，3D 页再决定朝向
            f.u = (f.raw_x - mnx) / dx if dx > 1e-9 else 0.5
            f.v = (f.raw_y - mny) / dy if dy > 1e-9 else 0.5
        bounds = (mnx, mny, mxx, mxy)
    else:
        # 有未摆放设备 → 整体回退网格（保持相对顺序，避免半真实半网格的错位）
        pts, cols, rows = _grid_fallback(len(out))
        for f, (u, v) in zip(out, pts):
            f.u, f.v = u, v
        bounds = (0.0, 0.0, 1.0, 1.0)
        source = source if source != "none" else "grid"
        if placed and len(placed) < len(out):
            source = "grid"      # 混合摆放 → 明确标注是网格回退
        return LayoutSpec(fixtures=out, gx=cols, gy=rows, bounds=bounds, source=source)

    gx, gy = _estimate_grid([f.u for f in out], [f.v for f in out])
    return LayoutSpec(fixtures=out, gx=gx, gy=gy, bounds=bounds,
                      source=source if source != "none" else "grid")


def read_layout_safe(project=None, topview=None) -> LayoutSpec:
    """不抛异常的封装（窗口调用）。"""
    try:
        return read_layout(topview=topview, project=project)
    except Exception:
        return LayoutSpec(fixtures=[], gx=1, gy=1, source="error")
