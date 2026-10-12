"""光场 → 真实 DMX 通道值映射 —— 实验室输出的最后一公里。

输入：模块求值器产出的逐帧光场 `field`（`[T, res*res*ch]`，float32，值 0..1，
      通道序 `[R,G,B,c4]`，c4 = LED 的 dim 或跑泉的 pump）
输出：`{universe: {addr_0based: value_0_255}}` —— 直接可喂
      `NetworkManager.update_layer_sparse("ml_lab", ...)`。

两条硬约束（都踩过坑）：
  1) **按通道表位置映射，不按类型查字典**：`running_light` 这类设备有 4 组重复
     的 R/G/B 通道，字典按类型查会丢/错位；只有 `def.channels` 的**下标**
     才是真实 DMX 偏移。
  2) **地址 = universe + (start_channel - 1) + offset**（start_channel 是 1-based，
     主系统层内地址是 0-based）——这是与主系统输出引擎的地址契约。
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import numpy as np
except Exception:                                     # pragma: no cover
    np = None

# 通道类型（小写容错：工程里有 "R" 也有 "r"、"dim" 也有 "dimmer"）
_CH_R, _CH_G, _CH_B, _CH_W = "r", "g", "b", "w"
_CH_DIM = ("dim", "dimmer")
_CH_PUMP = ("pump",)
_CH_MOTOR = ("motor_x", "motor_y", "motor_z")
# 电机中位（12 位设备已随产品删除，此处仅作防御性中性值）
_MOTOR_NEUTRAL = 127


@dataclass
class MappedFrame:
    """单帧映射结果。"""
    sparse: dict[int, dict[int, int]]        # {universe: {addr0: val}}
    per_fixture: dict[str, list[int]]        # {fid: [ch值]}（调试/预览用）


def _bilinear(field_row, res: int, ch: int, u: float, v: float) -> tuple[float, ...]:
    """在 res×res 空间场上按 (u,v)∈[0,1]² 双线性采样，返回该点 ch 个通道值。

    场的内存布局（与 effects_py / cue_planner 一致）：
        idx = vi*res + ui，u = (idx % res)/(res-1)，v = (idx // res)/(res-1)
    """
    res_m1 = res - 1
    x = max(0.0, min(1.0, u)) * res_m1
    y = max(0.0, min(1.0, v)) * res_m1
    x0 = int(x)
    y0 = int(y)
    x1 = min(x0 + 1, res_m1)
    y1 = min(y0 + 1, res_m1)
    fx = x - x0
    fy = y - y0

    def px(xi: int, yi: int) -> tuple[float, ...]:
        base = (yi * res + xi) * ch
        return tuple(float(field_row[base + c]) for c in range(ch))

    p00 = px(x0, y0)
    p10 = px(x1, y0)
    p01 = px(x0, y1)
    p11 = px(x1, y1)
    out = []
    for c in range(ch):
        top = p00[c] * (1.0 - fx) + p10[c] * fx
        bot = p01[c] * (1.0 - fx) + p11[c] * fx
        out.append(top * (1.0 - fy) + bot * fy)
    return tuple(out)


def _channels_for(fixture, rgb: tuple[float, float, float], c4: float,
                  white_boost: float = 0.0) -> dict[int, int]:
    """按 fixture.channels 的**位置**生成 {offset: dmx值}。

    rgb/c4 均为 0..1；white_boost 为 program 的 white 参数（0..1，抬升 W 通道）。
    """
    def _u8(x: float) -> int:
        # 统一收口：先 clamp 0..1 再量化，防上游溢出（DMX 0-255 无符号）
        x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
        return int(round(x * 255))

    r, g, b = rgb
    rv, gv, bv = _u8(r), _u8(g), _u8(b)
    # 白通道：取 RGB 最小分量（三色等量即纯白）——比简单平均更符合加色混合直觉
    wv = _u8(min(r, g, b))
    if white_boost > 0.0:
        wv = max(wv, _u8(white_boost))
    dimv = _u8(c4)

    out: dict[int, int] = {}
    for off, name in enumerate(getattr(fixture, "channels", []) or []):
        n = str(name).strip().lower()
        if n == _CH_R:
            out[off] = rv
        elif n == _CH_G:
            out[off] = gv
        elif n == _CH_B:
            out[off] = bv
        elif n == _CH_W:
            out[off] = wv
        elif n in _CH_DIM:
            out[off] = dimv
        elif n in _CH_PUMP:
            out[off] = dimv
        elif n in _CH_MOTOR:
            out[off] = _MOTOR_NEUTRAL
        else:
            # 未知/功能通道：安全默认 0（不动它）
            out[off] = 0
    return out


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def map_field_frame(spec, field_row, res: int = 16, ch: int = 4,
                    white: float = 0.0) -> MappedFrame:
    """把一帧光场映射成真实设备通道值。

    spec      LayoutSpec（含每台设备的 universe/start_channel/channels/u/v）
    field_row 长度 res*res*ch 的一维序列（float32/0..1）
    white     program 的 white 参数（抬升 W 通道；0=关闭）

    ★ 出口统一 clamp 到 [0,1]：模块的 `build_cue_timeline` 会用
      `bright = 0.25 + 0.85*c4` 抬升亮度，当 c4 接近 1 时乘出 >1 的值
      （实测 rampUp + amount 0.98 → 1.068）。DMX 是 0-255 无符号，
      越界会溢出/被下游截断成不可预期值。此处是集成侧最收口的防线——
      无论上游哪个分支/公式溢出，落到真实通道前都被钳住。
    """
    sparse: dict[int, dict[int, int]] = {}
    per: dict[str, list[int]] = {}
    for f in spec.fixtures:
        if not getattr(f, "channels", None):
            continue
        vals = _bilinear(field_row, res, ch, f.u, f.v)
        rgb = tuple(_clamp01(v) for v in vals[:3]) if len(vals) >= 3 else (0.0, 0.0, 0.0)
        c4 = _clamp01(vals[3]) if len(vals) >= 4 else 0.0
        per_off = _channels_for(f, rgb, c4, white_boost=white)
        uni = int(f.universe)
        slot = sparse.setdefault(uni, {})
        base = int(f.start_channel) - 1          # 1-based → 0-based
        for off, v in per_off.items():
            addr = base + off
            if 0 <= addr < 512:
                slot[addr] = v
        # 调试视图：按定义顺序展开
        n = len(f.channels)
        per[f.fid] = [slot.get(base + i, 0) for i in range(n)]
    return MappedFrame(sparse=sparse, per_fixture=per)


class FieldPlayer:
    """把整段光场按帧号映射（带缓存，避免逐帧重复采样）。"""

    def __init__(self, spec, field, res: int = 16, ch: int = 4):
        self.spec = spec
        self.field = field
        self.res = int(res)
        self.ch = int(ch)
        if np is not None and hasattr(field, "shape"):
            self.frames = int(field.shape[0])
        else:
            row = self.res * self.res * self.ch
            self.frames = max(1, len(field) // row) if row else 0
        self._cache: dict[int, MappedFrame] = {}

    def frame(self, idx: int, white: float = 0.0) -> MappedFrame:
        if self.frames <= 0:
            return MappedFrame(sparse={}, per_fixture={})
        i = max(0, min(int(idx), self.frames - 1))
        hit = self._cache.get(i)
        if hit is not None:
            return hit
        row_len = self.res * self.res * self.ch
        if np is not None and hasattr(self.field, "__getitem__"):
            row = self.field[i]
        else:
            row = self.field[i * row_len:(i + 1) * row_len]
        mf = map_field_frame(self.spec, row, self.res, self.ch, white=white)
        self._cache[i] = mf
        if len(self._cache) > 256:              # 简单容量控制（防长会话增长）
            for k in list(self._cache)[:128]:
                self._cache.pop(k, None)
        return mf
