# -*- coding: utf-8 -*-
"""样条几何工具：三次样条参数化/采样、圆锚点中心、样条与页匹配。

从设备布局样条解析中提取的纯几何例程（无 Qt 依赖，可 headless 单测）：
 - spline_param   设备在样条折线上的参数位置 t∈[0,1]（排序键）
 - spline_sample   均匀参数自然三次样条采样（过控制点、自然边界）
 - _circle_anchor_centers 整圆/星形闭合样条 → (cx, cz, R, star) 锚点中心
 - match_splines_to_page 样条包围盒/贴合判据 → 匹配一页设备的全部样条
"""
from __future__ import annotations

import math


def spline_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
 """样条世界坐标包围盒 (min_x, min_z, max_x, max_z)。"""
 xs = [p[0] for p in points]
 zs = [p[1] for p in points]
 return (min(xs), min(zs), max(xs), max(zs))


def spline_sample(points: list[tuple[float, float]],
     per_seg: int = 100,
     mode: str = "cubic") -> tuple[list[tuple[float, float]], list[float]]:
 """均匀参数自然三次样条采样 — 渲染弧线的精确还原。

 标准自然三次样条 (过每个控制点), 节点参数等距 (t_i = i/N, 与坐标间距无关
 — 非弦长参数化), 每段 per_seg=100 插值点, 端点二阶导 = 0 (自然边界)。
 120 台底弧线设备到本采样最大残差 0.19 世界单位 (对照: 弦长参数化 13465 /
 clamped 68 / not-a-knot 180)。控制点 X==0 强制替换 0.0001。

 返回 (采样点 [(x, z), ...], 每采样点切向角 [deg, ...]) — 两列一一对应,
 长度 = (N-1)*per_seg + 1 (N = 控制点数)。闭合模式按非闭合处理
 (闭合扩展逻辑未验证, 工程实测 Closed=False; 上游解析不携带 Closed
 属性, 调用方无法区分)。
 mode="linear" (SplineMode=0 控制点直连折线, 切向 = 边方向角); 闭合折线
 (星形/整圆, 首尾重合) 自动按折线模式采样。
 """
 pts = [(float(x), float(z)) for x, z in points]
 if len(pts) < 2:
  return list(points), [0.0] * len(points)
 for i, (x, z) in enumerate(pts):
  if x == 0.0:
   pts[i] = (0.0001, z)
 # 闭合折线（星形 / 整圆 = 首尾重合多边形）
 # 或 Linear 模式（SplineMode=0 控制点直连折线）：每边直连采样，
 # 切向 = 边方向角（设备贴折线排列，样条曲线会过冲，不可用）
 if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1e-6 \
   or mode == "linear":
  out: list[tuple[float, float]] = []
  ang: list[float] = []
  for i in range(len(pts) - 1):
   ax, az = pts[i]
   bx, bz = pts[i + 1]
   da = math.degrees(math.atan2(bz - az, bx - ax))
   for k in range(per_seg):
    t = k / per_seg
    out.append((ax + (bx - ax) * t, az + (bz - az) * t))
    ang.append(da)
  out.append(pts[-1])
  ang.append(da)
  return out, ang
 n = len(pts)
 N = n - 1
 h = 1.0 / N
 inv_h2 = 6.0 / (h * h)
 # 右端项 d[i] = 6*(P[i+1] - 2P[i] + P[i-1])/h^2, i = 1..N-1
 rx = [0.0] * n
 rz = [0.0] * n
 for i in range(1, N):
  rx[i] = inv_h2 * (pts[i + 1][0] - 2.0 * pts[i][0] + pts[i - 1][0])
  rz[i] = inv_h2 * (pts[i + 1][1] - 2.0 * pts[i][1] + pts[i - 1][1])
 # Thomas 追赶法 (三对角 1/4/1, 自然边界 M[0] = M[N] = 0 隐含)
 mx = [0.0] * n
 mz = [0.0] * n
 size = N - 1
 if size >= 1:
  mu = [0.0] * (size + 2)
  div = 4.0
  mx[1] = rx[1] / div
  mz[1] = rz[1] / div
  for i in range(2, size + 1):
   mu[i] = 1.0 / div
   div = 4.0 - mu[i]
   mx[i] = (rx[i] - mx[i - 1]) / div
   mz[i] = (rz[i] - mz[i - 1]) / div
  for i in range(size - 1, 0, -1):
   mx[i] -= mu[i + 1] * mx[i + 1]
   mz[i] -= mu[i + 1] * mz[i + 1]
 # 重采样: out[0] = P[0], j = 1..total-1 逐点
 out: list[tuple[float, float]] = [pts[0]]
 # 起点 P[0] 切向 = 段 1 在 u=0 的解析导数 S'(0) = c (M[0] = 0):
 # c = (P1-P0)/h - M1*h/6
 c0x = (pts[1][0] - pts[0][0]) / h - mx[1] * h / 6.0
 c0z = (pts[1][1] - pts[0][1]) / h - mz[1] * h / 6.0
 ang: list[float] = [math.degrees(math.atan2(c0z, c0x))]
 total = N * per_seg
 for j in range(1, total):
  t = j / total
  k = int(math.floor(t * N)) + 1
  if k > N:
   k = N
  u = t - (k - 1) / N
  mkx, mkz = mx[k - 1], mz[k - 1] # M[k-1]
  mk1x, mk1z = mx[k], mz[k]  # M[k]
  p0x, p0z = pts[k - 1]
  p1x, p1z = pts[k]
  ax = (mk1x - mkx) / 6.0 / h
  bx = mkx / 2.0
  cx = (p1x - p0x) / h - (2.0 * mkx + mk1x) * h / 6.0
  az = (mk1z - mkz) / 6.0 / h
  bz = mkz / 2.0
  cz = (p1z - p0z) / h - (2.0 * mkz + mk1z) * h / 6.0
  u2 = u * u
  x = ax * u2 * u + bx * u2 + cx * u + p0x
  z = az * u2 * u + bz * u2 + cz * u + p0z
  out.append((x, z))
  # 解析切向 S'(u) = 3a*u^2 + 2b*u + c
  dx = 3.0 * ax * u2 + 2.0 * bx * u + cx
  dz = 3.0 * az * u2 + 2.0 * bz * u + cz
  ang.append(math.degrees(math.atan2(dz, dx)))
 out.append(pts[N])
 # 端点 P[N] 处切向 = 段 N 在 u=h 的解析导数 (与采样点同公式)
 mkx, mkz = mx[N - 1], mz[N - 1]
 p0x, p0z = pts[N - 1]
 p1x, p1z = pts[N]
 ax = (0.0 - mkx) / 6.0 / h
 bx = mkx / 2.0
 cx = (p1x - p0x) / h - (2.0 * mkx + 0.0) * h / 6.0
 az = (0.0 - mkz) / 6.0 / h
 bz = mkz / 2.0
 cz = (p1z - p0z) / h - (2.0 * mkz + 0.0) * h / 6.0
 h2 = h * h
 ang.append(math.degrees(math.atan2(3.0 * az * h2 + 2.0 * bz * h + cz,
          3.0 * ax * h2 + 2.0 * bx * h + cx)))
 return out, ang


def match_splines_to_page(
 world_pts: list[tuple[float, float]],
 splines: list[dict],
 min_overlap_ratio: float = 0.5,
 min_fit_ratio: float = 0.0,
 min_covered_count: int = 8,
 min_covered_ratio: float = 0.15,
) -> list[dict]:
 """把样条匹配到一页设备（用户原则：弧线必须与当前页设备匹配，不乱传）。

 匹配 = 样条包围盒与设备世界坐标包围盒的重叠占比（重叠 / 样条自身跨度）
 ≥ min_overlap_ratio，且满足任一贴合判据：
  - **中位贴合**：全页设备到弧线的中位距离 ≤ 跨度×min_fit_ratio
  （多数设备真贴弧线——雾森 6/11 台贴 Spline 2 中位 0.070 过，
  误匹配的散布设备 3-5/11 台中位 0.10+ 淘汰）；
  - **小簇豁免**（2026-08-03 用户实测：副池 80 台大星 + 40 台小星
  只显示大星、副池 Spline 5 小弧线丢失——全页中位被大簇稀释）：
  距弧线 ≤ 跨度×min_fit_ratio 的设备数 ≥ min_covered_count
  且占比 ≥ min_covered_ratio（副池小星 240/720=33%、Spline 5
  13/72=18% 豁免显示；雾森散布误匹配 3-5 台 < min_covered_count 仍淘汰）。
 返回**全部**匹配弧线（一页可有多个贴合弧线，逐条渲染）。
 设备少/散的点集不参与（len < 4 返回空）。
 min_fit_ratio ≤ 0：不做贴合过滤（仅面积重叠）。
 ★ min_covered_count 默认 8（2026-09-12 由 10 下调）：小簇页每弧仅 12 台设备
 时，合格台数常为 8-9 < 10 → 整条弧漏匹配（海边别墅2 三维扇子弧#0 实测 8 台）。
 8 与小簇占比判据 0.15 配合可覆盖 12 台/弧的常规小簇；再低会误纳散布簇。
 """
 if len(world_pts) < 4 or not splines:
  return []
 xs = [p[0] for p in world_pts]
 zs = [p[1] for p in world_pts]
 dev_min_x, dev_min_z, dev_max_x, dev_max_z = min(xs), min(zs), max(xs), max(zs)
 dev_area = max(1.0, (dev_max_x - dev_min_x) * (dev_max_z - dev_min_z))
 scored: list[tuple[float, int, dict]] = []
 for sp in splines:
  smin_x, smin_z, smax_x, smax_z = spline_bounds(sp["points"])
  # ★ 面积重叠比（两轴都必须重叠）：x 轴重叠大但 z 完全不交的样条不算匹配
  # （2026-08-03 实测 Spline 4 z∈[18399,29073] 与底弧线 z∈[-22303,-16760]
  # 不相交却因 x 贡献被误匹配）。
  ox = max(0.0, min(dev_max_x, smax_x) - max(dev_min_x, smin_x))
  oz = max(0.0, min(dev_max_z, smax_z) - max(dev_min_z, smin_z))
  spline_area = max(1.0, (smax_x - smin_x) * (smax_z - smin_z))
  overlap = (ox * oz) / spline_area
  if overlap >= min_overlap_ratio:
   if min_fit_ratio <= 0.0:
    scored.append((0.0, 0, sp))
    continue
   pts = sp["points"]
   span = math.hypot(smax_x - smin_x, smax_z - smin_z)
   if span <= 1.0:
    continue
   thr = span * min_fit_ratio
   ds = []
   # ★ 性能（2026-08-14）：段向量/长度只在每样条预解一次（原每点每段
   # 重复 unpack 与平方），算术与比较逐位不变。
   segs = []
   for k in range(len(pts) - 1):
    ax, az = pts[k]
    bx, bz = pts[k + 1]
    vx, vz = bx - ax, bz - az
    seg2 = vx * vx + vz * vz
    if seg2 > 1e-12:
     segs.append((ax, az, vx, vz, seg2))
   for wx, wz in world_pts:
    best = float("inf")
    for ax, az, vx, vz, seg2 in segs:
     t = max(0.0, min(1.0, ((wx - ax) * vx + (wz - az) * vz) / seg2))
     best = min(best, math.hypot(wx - (ax + vx * t),
            wz - (az + vz * t)))
    ds.append(best if best < float("inf") else 0.0)
   ds.sort()
   mid_d = ds[len(ds) // 2]
   n_fit = sum(1 for d in ds if d <= thr)
   if mid_d <= thr or (n_fit >= min_covered_count
        and n_fit >= len(world_pts) * min_covered_ratio):
    # 排序键 = (中位距离升序, 贴合数降序)：最贴合、覆盖最广的先
    scored.append((mid_d, -n_fit, sp))
 if not scored:
  return []
 scored.sort(key=lambda t: (t[0], t[1]))
 return [sp for _m, _n, sp in scored]


def spline_param(x: float, z: float, points: list[tuple[float, float]]) -> float:
 """设备在样条折线上的参数位置 t∈[0,1]（最近线段投影），用于沿弧线排序参考。

 仅作排序键（2026-08-03 用户指正：源工程弧线不直接当曲线用，只参考顺序；
 切向由设备定位点自拟合的平滑曲线给出）。
 """
 best_t = 0.0
 best_d2 = float("inf")
 n = max(1, len(points) - 1)
 for i in range(len(points) - 1):
  ax, az = points[i]
  bx, bz = points[i + 1]
  vx, vz = bx - ax, bz - az
  seg2 = vx * vx + vz * vz
  if seg2 <= 1e-12:
   continue
  t = ((x - ax) * vx + (z - az) * vz) / seg2
  t = max(0.0, min(1.0, t))
  px, pz = ax + vx * t, az + vz * t
  d2 = (x - px) ** 2 + (z - pz) ** 2
  if d2 < best_d2:
   best_d2 = d2
   best_t = (i + t) / n
 return best_t


def _circle_anchor_centers(
 splines: list[dict] | None,
) -> list[tuple[float, float, float, bool]]:
 """整圆/星形样条锚点：闭合样条 → (cx, cz, R, star) 列表。

 2026-08-08 主池圆页：整圆圆心作锚点，供无弧线匹配页按
 圆心分簇补画同心环圆。弧样条（首尾不闭合）
 不参与；已匹配弧线的页走分支 2，不进锚点路径。
 ★ 星形样条（2026-08-09 副池扇子）：星形（star=True 且无 mode
 键）同样入锚点（star=True）——20 台设备排在两组同心星形的外顶点
 圆环上，星形中心 ≈ 环心，由同一锚点分带机制补画同心双圆。
 ★ 近似重复锚点合并（中心距 <500 世界单位）：同心星对中心差 63~118、
 主池重复整圆差 0（主池锚点两两 ≥2000 不受影响）。不合并则最近锚点
 分簇会把同一环设备劈成两簇（星对偏移 < 星环宽度）→ 环带 <5 台被拒、
 同心圆丢失。合并 = 中心取均值、半径取最大值、star 标记取或。
 """
 out: list[tuple[float, float, float, bool]] = []
 for _sp in (splines or []):
  if not _sp.get("star"):
   continue
  _pts = _sp.get("points") or []
  if not (_pts and len(_pts) >= 4 and _pts[0] == _pts[-1]):
   continue
  _px = sum(p[0] for p in _pts) / len(_pts)
  _pz = sum(p[1] for p in _pts) / len(_pts)
  _pr = sum(math.hypot(p[0] - _px, p[1] - _pz) for p in _pts) / len(_pts)
  if _pr > 1.0:
   out.append((_px, _pz, _pr, "mode" not in _sp))
 merged: list[list[float]] = []
 for _a in out:
  _hit = None
  for _m in merged:
   if math.hypot(_a[0] - _m[0], _a[1] - _m[1]) < 500.0:
    _hit = _m
    break
  if _hit is None:
   merged.append([_a[0], _a[1], _a[2], 1.0 if _a[3] else 0.0])
  else:
   _hit[0] = (_hit[0] + _a[0]) / 2.0
   _hit[1] = (_hit[1] + _a[1]) / 2.0
   _hit[2] = max(_hit[2], _a[2])
   _hit[3] = 1.0 if (_hit[3] or _a[3]) else 0.0
 return [(m[0], m[1], m[2], m[3] > 0.5) for m in merged]
