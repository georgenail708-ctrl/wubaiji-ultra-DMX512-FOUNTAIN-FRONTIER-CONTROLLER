"""
effect_presets_curated — 精炼效果库（审计后重建）

设计原则（贯彻用户全部要求）：
- 设备集仅：dimmer/R/G/B/W（灯）、motor_x/y/z（电机/摆臂喷头）、valve/pump（阀/泵）。
  无摇头灯(pan/tilt)、无烟机/CO₂/喷火/火花/泡泡/造雪等设备。
- 每条效果 = 真实 compute 内核 + 手调参数 + 传神中文名 + 中文说明 + Tabler 图标 + 基础/高级分级。
- 以“原型 × 真正改变观感的变体”组织；变体之间在波形/速度/宽窄/颜色/方向上有实质差异，
  绝非 v40 式 “V40 NNN” 机械填充。
- 重心：通用(channel-agnostic) + 灯效。

注册：被 effect_engine 末尾的链式补丁调用 _register_curated_presets(engine)。
"""
from __future__ import annotations
import math
from typing import List, Callable

from .effect_engine import EffectPreset, ParamSpec, CH_TYPES
from . import effect_width_support as _width_support
from . import effect_width_native as _native_width
from . import effect_width_temporal as _temporal_width
from .effect_presets_curated_ext import _formation_pose_ext, _POSE_KINDS  # 扩充姿态库（2026-08-12）
# R183 hsv 统一（本文件为 4 处定义之一）：多前缀兜底兼容 app/src 两种导入根
try:
    from ..utils.color_math import hsv_to_rgb_u8_norm as _hsv_to_rgb_norm
except ImportError:
    try:
        from utils.color_math import hsv_to_rgb_u8_norm as _hsv_to_rgb_norm
    except ImportError:
        from src.utils.color_math import hsv_to_rgb_u8_norm as _hsv_to_rgb_norm

RGB = ["R", "G", "B"]
RGBW = ["R", "G", "B", "W"]
ALL_CH = ["dimmer", "R", "G", "B", "W", "motor_x", "motor_y", "motor_z", "valve", "pump"]
MOTOR = ["motor_x", "motor_y"]


# ───────────────────────── 数学工具 ─────────────────────────
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _smooth(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _ease(x: float, mode: str) -> float:
    x = _clamp01(x)
    if mode == "smooth":
        return x * x * (3.0 - 2.0 * x)
    if mode == "in":      # 先慢后快
        return x * x
    if mode == "out":     # 先快后慢
        return 1.0 - (1.0 - x) * (1.0 - x)
    return x              # linear


_NOISE_CACHE = {}


def _vnoise(i: int, seed: int) -> float:
    """确定性伪随机 [0,1]（不依赖 random，保证可复现 & 打包安全）。"""
    n = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def _hsv_to_rgb(h: float, s: float, v: float):
    """R183 统一：薄封装 src/utils/color_math.py（h%1.0 + int 截断口径）。

    名字保留（effect_presets_r8_color.py 从本模块 import）；与 CE C 侧
    `_ce_hsv_to_rgb` 逐位同口径，256×N + 20 万随机对拍 0 差异。
    """
    return _hsv_to_rgb_norm(h, s, v)


def _kelvin_rgb(k: float):
    """近似色温→RGB（1000–12000K）。"""
    k = max(1000.0, min(12000.0, k)) / 100.0
    if k <= 66:
        r = 255
        g = 99.47 * math.log(k) - 161.12 if k > 0 else 0
    else:
        r = 329.7 * ((k - 60) ** -0.1332)
        g = 288.12 * ((k - 60) ** -0.0755)
    if k >= 66:
        b = 255
    elif k <= 19:
        b = 0
    else:
        b = 138.52 * math.log(k - 10) - 305.04
    return (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))))


# ───────────────────────── 标量波形内核 ─────────────────────────
def _waveshape(shape: str, phase: float, duty: float = 0.5) -> float:
    """输入相位[0,1)→输出[0,1]。各 shape 观感不同。"""
    p = phase % 1.0
    if shape == "sine":
        return 0.5 - 0.5 * math.cos(p * 2 * math.pi)
    if shape == "tri":
        return 1.0 - abs(2.0 * p - 1.0)
    if shape == "saw_up":
        return p
    if shape == "saw_down":
        return 1.0 - p
    if shape == "square":
        return 1.0 if p < duty else 0.0
    if shape == "pulse":
        return 1.0 if p < duty * 0.5 else 0.0
    if shape == "expo":
        return p * p
    if shape == "log":
        return 1.0 - (1.0 - p) * (1.0 - p)
    if shape == "bounce":
        return abs(math.sin(p * math.pi * 2)) * (1.0 - p)
    if shape == "fold":  # 折叠三角
        x = (2.0 * p) % 2.0
        return 1.0 - abs(x - 1.0)
    if shape == "notch":  # 缺口
        return 0.0 if 0.4 < p < 0.6 else 1.0
    if shape == "twin":   # 双峰
        return 0.5 - 0.5 * math.cos(p * 4 * math.pi)
    if shape == "trapz":  # 梯形波：duty=平台占比，软边方波
        rise = max(1e-4, (1.0 - duty) * 0.5)
        if p < rise:
            return p / rise
        if p < rise + duty:
            return 1.0
        if p < rise + duty + rise:
            return 1.0 - (p - rise - duty) / rise
        return 0.0
    if shape == "gauss":  # 高斯钟脉冲：每周期一个钟形包，duty 控宽
        sig = max(0.02, duty * 0.25)
        return math.exp(-((p - 0.5) ** 2) / (2 * sig * sig))
    if shape == "rectsin":  # 整流驼峰：全波整流，双倍密的正向驼峰
        return abs(math.sin(p * 2 * math.pi))
    if shape == "skewtri":  # 偏锋三角：duty=峰位，锯齿↔三角连续过渡
        pk = min(0.99, max(0.01, duty))
        return p / pk if p < pk else (1.0 - p) / (1.0 - pk)
    if shape == "dpulse":   # 双脉冲：一周期两击
        if p < duty * 0.5:
            return 1.0
        if 0.5 <= p < 0.5 + duty * 0.5:
            return 1.0
        return 0.0
    if shape == "flattop":  # 平顶正弦：峰被削平的正弦
        v = 0.5 - 0.5 * math.cos(p * 2 * math.pi)
        return min(1.0, v * 1.6)
    if shape == "stairsin":  # 阶梯正弦：量化的正弦
        v = 0.5 - 0.5 * math.cos(p * 2 * math.pi); steps = max(2, int(round(2 + duty * 8)))
        # ★ R193/P1-10：钳峰值 ≤1.0（精确峰值 10/9 经 uint8 回绕成 27，DM-05/N2）
        return min(1.0, math.floor(v * steps) / max(1, steps - 1))
    return 0.5 - 0.5 * math.cos(p * 2 * math.pi)


def _scalar_kernel(shape: str):
    """返回标量波形 compute：沿设备方向相位推进，写 ch_type。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        speed = float(params.get("speed", 1.0))
        freq = float(params.get("spatial_freq", 1.0))
        duty = float(params.get("duty", 0.5))
        ease = params.get("ease", "linear")
        n = max(1, len(sorted_fx))
        sampler = _width_support.scalar_sampler(shape, params, t, n, _waveshape)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            ph = pos * freq - t * speed
            w = _ease(_width_support.scalar_at(sampler, shape, ph, duty, _waveshape), ease)
            ef.values[orig_i][ch] = int(lo + w * (hi - lo))
    return fn


def _envelope_kernel(kind: str):
    """ADSR/采样保持/斜率/阶梯 等动态包络。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch, lo, hi = _temporal_width.channel_range(params)
        speed = float(params.get("speed", 1.0))
        a = float(params.get("attack", 0.1)); d = float(params.get("decay", 0.3))
        steps = int(params.get("steps", 4))
        spread = float(params.get("spread", 0.0))
        seed = int(params.get("seed", 0))
        n = max(1, len(sorted_fx))
        period = 1.0 / max(0.01, speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            lt = (t + pos * spread * period) % period
            ph = lt / period
            if kind == "adsr":
                if ph < a:
                    w = ph / max(1e-4, a)
                elif ph < a + d:
                    w = 1.0 - 0.4 * (ph - a) / max(1e-4, d)
                elif ph < 0.8:
                    w = 0.6
                else:
                    w = 0.6 * (1.0 - (ph - 0.8) / 0.2)
            elif kind == "sh":   # 采样保持
                w = _vnoise(int(t * speed) + seq_i * 7, seed)
            elif kind == "step":  # 阶梯
                w = math.floor(ph * steps) / max(1, steps - 1)
            elif kind == "ramp_hold":  # 斜坡保持
                w = min(1.0, ph / 0.6) if ph < 0.85 else 0.0
            elif kind == "slew":  # 斜率限速正弦
                w = _smooth(0.5 - 0.5 * math.cos(ph * 2 * math.pi))
            elif kind == "pluck":  # 拨弦：瞬起指数落
                w = math.exp(-ph / max(0.02, d))
            elif kind == "swell":  # 渐强保持：缓升到顶保持
                w = _smooth(min(1.0, ph / max(0.02, a)))
            elif kind == "multistrike":  # 连击：一周期多次衰减
                hits = max(2, steps)
                lp = (ph * hits) % 1.0
                w = math.exp(-lp / max(0.02, d))
            else:
                w = ph
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
        _temporal_width.finish(("time", kind), ef, params, sorted_fx)
    return fn


def _chase_kernel(kind: str):
    """跑动/扫描/擦除/蛇形 等位置驱动效果。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        speed = float(params.get("speed", 1.0))
        width = float(params.get("width", 0.18))
        tail = float(params.get("tail", 0.5))
        direction = params.get("direction", "正向")
        n = max(1, len(sorted_fx))
        head = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            if direction == "反向":
                pos = 1.0 - pos
            elif direction == "中心向外":
                pos = abs(pos - 0.5) * 2.0
            elif direction == "两侧向中":
                pos = 1.0 - abs(pos - 0.5) * 2.0
            dist = (pos - head) % 1.0
            if kind == "comet":
                w = math.exp(-dist / max(0.01, width)) if dist < tail else 0.0
            elif kind == "dot":
                w = 1.0 if dist < width else 0.0
            elif kind == "scan":
                back = (head - pos) % 1.0
                w = 1.0 if min(dist, back) < width else 0.0
            elif kind == "wipe":
                w = 1.0 if pos < head else 0.0
            elif kind == "wave-saw-tool":
                w = _smooth(1.0 - abs(dist - 0.0) / max(0.01, width)) if dist < width * 2 else 0.0
            elif kind == "bounce":
                bh = 1.0 - abs(((t * speed) % 2.0) - 1.0)
                w = math.exp(-abs(pos - bh) / max(0.01, width))
            elif kind == "marquee":   # 跑马灯：每 N 格亮、整体平移
                grp = max(2, int(round(1.0 / max(0.05, width))))
                idx = int(seq_i + t * speed * grp)
                w = 1.0 if (idx % grp) == 0 else 0.0
            elif kind == "kitt":      # 来回扫(KITT)：带拖尾的往返
                bh = 1.0 - abs(((t * speed) % 2.0) - 1.0)
                d = abs(pos - bh)
                w = math.exp(-d / max(0.01, width))
            elif kind == "build":     # 逐格堆积：依次点亮累积
                w = 1.0 if pos <= head else 0.0
            elif kind == "dissolve":  # 逐格消解：依次熄灭
                w = 0.0 if pos <= head else 1.0
            elif kind == "jumpseq":   # 跳序追逐：确定性乱序点亮
                order = _vnoise(seq_i, 17)
                d = (order - head) % 1.0
                w = 1.0 if d < width else 0.0
            elif kind == "collide":   # 双彗对撞：两头相向，中点相遇
                h2 = head
                d1 = (pos - h2) % 1.0
                d2 = (1.0 - pos - h2) % 1.0
                w = max(math.exp(-d1 / max(0.01, width)) if d1 < tail else 0.0,
                        math.exp(-d2 / max(0.01, width)) if d2 < tail else 0.0)
            elif kind == "multihead":  # 多头追逐：等距多个头
                heads = max(2, int(params.get("heads", 3)))
                w = 0.0
                for hh in range(heads):
                    d = (pos - (head + hh / heads)) % 1.0
                    w = max(w, math.exp(-d / max(0.01, width)) if d < tail else 0.0)
            elif kind == "levelbar":   # 电平条填充：到水位全亮、随波起伏
                lvl = 0.5 - 0.5 * math.cos(t * speed * 2 * math.pi)
                w = 1.0 if pos <= lvl else 0.0
            elif kind == "mirror":     # 镜像追逐：左右对称同跑
                m = abs(pos - 0.5) * 2.0
                d = (m - head) % 1.0
                w = math.exp(-d / max(0.01, width)) if d < tail else 0.0
            else:
                w = 1.0 if dist < width else 0.0
            ef.values[orig_i][ch] = _native_width.chase_byte(kind, (pos, head, t, n), params, (w, lo, hi), _clamp01)
    return _native_width.register_chase(fn, kind)


def _random_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        speed = float(params.get("speed", 1.0))
        density = float(params.get("density", 0.3))
        seed = int(params.get("seed", 0))
        n = max(1, len(sorted_fx))
        tick = int(t * speed * 6)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            r = _vnoise(seq_i * 13 + tick, seed)
            if kind == "sparkle":
                w = 1.0 if r < density else 0.0
            elif kind == "twinkle":
                base = _vnoise(seq_i, seed)
                w = _smooth(0.5 + 0.5 * math.sin(t * speed * 2 + base * 6.28)) if base < density + 0.4 else 0.1
            elif kind == "jitter":
                w = 0.5 + (r - 0.5) * density * 2.0
            elif kind == "perlin":
                a = _vnoise(seq_i, seed); b = _vnoise(seq_i + 1, seed)
                fphase = (t * speed * 0.3) % 1.0
                w = a + (b - a) * _smooth(fphase)
            elif kind == "popcorn":   # 爆米花：随机点燃后各自淡出
                period = max(1, int(8 / max(0.05, density)))
                tick = int(t * speed * 4)
                # 找该设备最近一次"爆"的起点
                bin_i = (tick + seq_i * 7) % period
                ignite = _vnoise(seq_i * 13 + (tick - bin_i), seed) < density
                w = math.exp(-bin_i / max(1.0, period * 0.35)) if ignite else 0.0
            elif kind == "glitch":    # 故障跳变：随机阶跃
                tick = int(t * speed * 8)
                r = _vnoise(seq_i * 5 + tick, seed)
                w = r if r > (1.0 - density) else (0.0 if r < density * 0.5 else 0.15)
            elif kind == "lightning":  # 闪电：偶发强闪带余辉
                tick = int(t * speed * 6)
                ph = (t * speed * 6) % 1.0
                strike = _vnoise(seq_i // 3 + tick, seed) < density * 0.5
                w = (1.0 if ph < 0.1 else 0.4 * math.exp(-(ph - 0.1) * 6)) if strike else 0.0
            elif kind == "ripple":    # 涟漪触发：随机落点向两侧扩散环
                acc = 0.0
                for k in range(3):
                    tt = (t * speed + _vnoise(k, seed) * 3.0) % 3.0
                    c = _vnoise(k + int((t * speed) / 3.0), seed + 1)
                    d = abs(pos - c)
                    acc = max(acc, math.cos((d * 12 - tt * 6)) * math.exp(-tt * 0.8) * math.exp(-d * 4))
                w = _clamp01(acc)
            else:
                w = r
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


def _breathe_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch, lo, hi = _temporal_width.channel_range(params)
        speed = float(params.get("speed", 0.5))
        spread = float(params.get("spread", 0.0))
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            ph = t * speed + pos * spread
            if kind == "breathe":
                w = _smooth(0.5 - 0.5 * math.cos(ph * 2 * math.pi))
            elif kind == "heartbeat":
                p = ph % 1.0
                w = (math.exp(-((p - 0.1) ** 2) / 0.004) + 0.6 * math.exp(-((p - 0.3) ** 2) / 0.004))
            elif kind == "tide":
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi) + 0.15 * math.sin(ph * 6 * math.pi)
            else:
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
        _temporal_width.finish(("time", kind), ef, params, sorted_fx)
    return fn


# ───────────────────────── 颜色内核 ─────────────────────────
def _color_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        sat = float(params.get("saturation", 1.0))
        bright = float(params.get("brightness", 1.0))
        freq = float(params.get("spatial_freq", 1.0))
        spread = float(params.get("spread", 0.0))
        seed = int(params.get("seed", 0))
        k_lo = float(params.get("k_lo", 2700)); k_hi = float(params.get("k_hi", 6500))
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            if kind == "rainbow":
                h = (pos * freq + t * speed) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "hue_rot":
                h = (t * speed + pos * spread) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "colortemp":
                w = 0.5 - 0.5 * math.cos((t * speed + pos * spread) * 2 * math.pi)
                r, g, b = _kelvin_rgb(k_lo + w * (k_hi - k_lo))
                r, g, b = int(r * bright), int(g * bright), int(b * bright)
            elif kind == "plasma":
                v = (math.sin(pos * 6 * freq + t * speed) +
                     math.sin(pos * 9 * freq - t * speed * 1.3))
                h = (0.5 + 0.5 * v / 2.0) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "perlin_color":
                a = _vnoise(seq_i, seed); bb = _vnoise(seq_i + 1, seed)
                fphase = (t * speed) % 1.0
                h = (a + (bb - a) * _smooth(fphase)) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "fire":
                fl = 0.5 + 0.5 * math.sin(t * speed * 3 + pos * 8) * _vnoise(seq_i, int(t * 4))
                h = 0.02 + 0.08 * fl
                r, g, b = _hsv_to_rgb(h, 1.0, _clamp01(0.4 + fl) * bright)
            elif kind == "warmcool":
                w = 0.5 - 0.5 * math.cos((t * speed + pos) * 2 * math.pi)
                r, g, b = _kelvin_rgb(2400 + w * 4000)
            elif kind == "dualtone":  # 双色交替
                w = 0.5 + 0.5 * math.cos((t * speed + pos * freq) * 2 * math.pi)
                h = (float(params.get("hue_a", 0.0)) * w +
                     float(params.get("hue_b", 0.55)) * (1.0 - w)) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "tricolor":  # 三色轮换
                idx = int((t * speed + pos) * 3) % 3
                h = [0.0, 0.33, 0.66][idx]
                r, g, b = _hsv_to_rgb(h, sat, bright)
            elif kind == "colorpulse":  # 颜色脉冲
                pulse = max(0.0, math.sin((t * speed + pos) * math.pi))
                h = (pos * freq + t * speed * 0.2) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * pulse)
            elif kind == "colorwave":  # 颜色波浪
                w = 0.5 - 0.5 * math.cos((pos * freq - t * speed) * 2 * math.pi)
                h = (0.55 + 0.25 * w) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * (0.4 + 0.6 * w))
            elif kind == "starfield":  # 星空色点
                tw = _vnoise(seq_i, int(t * speed * 2))
                lit = tw < 0.25
                h = _vnoise(seq_i, 7)
                r, g, b = _hsv_to_rgb(h, sat * 0.6, bright if lit else 0.05)
            elif kind == "sparkles":  # 极光
                v = math.sin(pos * 3 + t * speed) + 0.5 * math.sin(pos * 7 - t * speed * 0.6)
                h = (0.4 + 0.18 * v) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * _clamp01(0.4 + 0.4 * v))
            elif kind == "ocean":  # 海洋
                v = 0.5 - 0.5 * math.cos((pos * freq - t * speed) * 2 * math.pi)
                h = (0.5 + 0.08 * v) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * (0.5 + 0.5 * v))
            else:
                h = (pos + t * speed) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright)
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b
    return fn


# ───────────────────────── 电机轨迹内核 ─────────────────────────
def _motor_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        amp = float(params.get("amplitude", 1.0))
        spread = float(params.get("spread", 0.0))
        fx_ratio = float(params.get("freq_x", 1.0))
        fy_ratio = float(params.get("freq_y", 1.0))
        center = int(params.get("center", 128))
        n = max(1, len(sorted_fx))
        rad = 127.0 * max(0.0, min(1.0, amp))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            ph = t * speed * 2 * math.pi + pos * spread * 2 * math.pi
            if kind == "circle":
                x = math.cos(ph); y = math.sin(ph)
            elif kind == "ellipse":
                x = math.cos(ph); y = 0.5 * math.sin(ph)
            elif kind == "fig8":
                x = math.sin(ph); y = math.sin(2 * ph)
            elif kind == "lissajous":
                x = math.sin(fx_ratio * ph); y = math.sin(fy_ratio * ph + math.pi / 2)
            elif kind == "spiral":
                rr = (t * speed * 0.3) % 1.0
                x = rr * math.cos(ph * 3); y = rr * math.sin(ph * 3)
            elif kind == "rose":
                k = fx_ratio
                r = math.cos(k * ph)
                x = r * math.cos(ph); y = r * math.sin(ph)
            elif kind == "sweep":
                s = math.sin(ph)
                x = s; y = 0.0
            elif kind == "ballyhoo":
                x = math.sin(ph) + 0.4 * (_vnoise(int(t * 3), seq_i) - 0.5)
                y = math.cos(ph * 1.3) + 0.4 * (_vnoise(int(t * 3) + 1, seq_i) - 0.5)
            elif kind == "pingpong":
                x = 1.0 - abs(((t * speed) % 2.0) - 1.0) * 2.0 + 0.0
                y = 0.0
            elif kind == "square_path":  # 方形轨迹
                seg = (ph / (2 * math.pi)) % 1.0
                if seg < 0.25:   x, y = -1 + 8 * seg, -1
                elif seg < 0.5:  x, y = 1, -1 + 8 * (seg - 0.25)
                elif seg < 0.75: x, y = 1 - 8 * (seg - 0.5), 1
                else:            x, y = -1, 1 - 8 * (seg - 0.75)
            elif kind == "star":  # 星形轨迹
                pts = int(max(2, params.get("freq_x", 5)))
                a = ph
                rr = 0.5 + 0.5 * math.cos(pts * a)
                x = rr * math.cos(a); y = rr * math.sin(a)
            elif kind == "precess":  # 进动圆
                drift = t * speed * 0.2
                x = math.cos(ph) + 0.3 * math.cos(drift)
                y = math.sin(ph) + 0.3 * math.sin(drift)
            elif kind == "spiral_up":  # 螺旋上升摆
                rr = 0.3 + 0.7 * (0.5 - 0.5 * math.cos(t * speed * 0.5))
                x = rr * math.cos(ph * 2); y = rr * math.sin(ph * 2)
            elif kind == "rwalk":  # 随机游走
                gx = _vnoise(int(t * speed * 2), seq_i) - 0.5
                gy = _vnoise(int(t * speed * 2) + 99, seq_i) - 0.5
                x = math.sin(ph) * 0.5 + gx; y = math.cos(ph) * 0.5 + gy
            else:
                x = math.cos(ph); y = math.sin(ph)
            ef.values[orig_i]["motor_x"] = int(max(0, min(255, center + x * rad)))
            ef.values[orig_i]["motor_y"] = int(max(0, min(255, center + y * rad)))
    return fn


# ───────────────────────── 水景内核 ─────────────────────────
def _fountain_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch, lo, hi = _temporal_width.channel_range(params, "pump")
        speed = float(params.get("speed", 1.0))
        width = float(params.get("width", 0.2))
        attack = float(params.get("attack", 0.1)); decay = float(params.get("decay", 0.4))
        direction = params.get("direction", "正向")
        n = max(1, len(sorted_fx))
        ph = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            if direction == "反向":
                pos = 1.0 - pos
            elif direction == "中心向外":
                pos = abs(pos - 0.5) * 2.0
            elif direction == "两侧向中":
                pos = 1.0 - abs(pos - 0.5) * 2.0
            if kind == "burst":   # 间歇泉/礼炮：整体起爆收
                if ph < attack:
                    w = ph / max(1e-4, attack)
                elif ph < attack + decay:
                    w = 1.0 - (ph - attack) / max(1e-4, decay)
                else:
                    w = 0.0
            elif kind == "supershoot":  # 单点慢起高喷
                lead = pos * 0.3
                lp = (ph - lead) % 1.0
                w = _smooth(min(1.0, lp / 0.5)) if lp < 0.7 else _smooth((1.0 - lp) / 0.3)
            elif kind == "chase":  # 跑泉链
                dist = (pos - ph) % 1.0
                w = math.exp(-dist / max(0.01, width))
            elif kind == "crown":  # 皇冠：环形齐起伏
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi)
            elif kind == "willow":  # 垂柳：中心高外缘渐落
                base = 1.0 - pos
                w = base * (0.7 + 0.3 * math.sin(t * speed * 2 + pos * 6))
            elif kind == "bloom":  # 绽放：中心向外环扩散
                ring = (ph - abs(pos - 0.5) * 2.0) % 1.0
                w = math.exp(-ring / max(0.01, width))
            elif kind == "cross":  # 交叉射流：奇偶对射
                odd = (seq_i % 2)
                w = (0.5 - 0.5 * math.cos((ph + odd * 0.5) * 2 * math.pi))
            elif kind == "wave":  # 波浪跑泉
                w = 0.5 - 0.5 * math.cos((pos * 2 - ph) * 2 * math.pi)
            elif kind == "spiral_arr":  # 螺旋阵列
                w = 0.5 - 0.5 * math.cos((pos * 4 - ph * 2) * 2 * math.pi)
            elif kind == "jumping":  # 跳泉：定向抛物
                lead = pos
                lp = (ph - lead * 0.5) % 1.0
                w = math.sin(lp * math.pi) if lp < 1.0 else 0.0
            elif kind == "fan":  # 扇形展开
                w = _smooth(1.0 - abs(pos - _clamp01(ph)) * 2.0)
            elif kind == "wall":  # 水幕脉动
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi) * (0.6 + 0.4 * math.sin(pos * 6))
            elif kind == "tide":  # 呼吸潮汐水位
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi) + 0.12 * math.sin(ph * 6 * math.pi + pos * 3)
            elif kind == "step_water":  # 阶梯水位
                steps = 4
                w = math.floor(((ph + pos) % 1.0) * steps) / max(1, steps - 1)
            else:
                w = 0.5 - 0.5 * math.cos(ph * 2 * math.pi)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
        _temporal_width.finish(("fountain", kind), ef, params, sorted_fx)
    return fn


KERNELS = {
    "scalar": _scalar_kernel, "env": _envelope_kernel, "chase": _chase_kernel,
    "rand": _random_kernel, "breathe": _breathe_kernel, "color": _color_kernel,
    "motor": _motor_kernel, "fountain": _fountain_kernel,
}


# ───────────────────────── 高级算法内核（单通道通用，确定性可复现） ─────────────────────────
def _snoise(x: float, seed: int) -> float:
    """平滑 1D 值噪声 [0,1]。"""
    i = int(math.floor(x)); f = x - i
    a = _vnoise(i, seed); b = _vnoise(i + 1, seed)
    return a + (b - a) * _smooth(f)


def _fbm(x: float, seed: int, octaves: int = 4) -> float:
    amp = 0.5; freq = 1.0; total = 0.0; norm = 0.0
    for o in range(octaves):
        total += amp * _snoise(x * freq, seed + o * 101)
        norm += amp; amp *= 0.5; freq *= 2.0
    return total / max(1e-6, norm)


_CA_CACHE = {}


def _ca_rows(rule: int, n: int, seed: int):
    key = (rule, n, seed)
    r = _CA_CACHE.get(key)
    if r is not None:
        return r
    row = [1 if _vnoise(i, seed) < 0.5 else 0 for i in range(n)]
    rows = [row[:]]
    for _g in range(max(8, n * 2)):
        nr = []
        for i in range(n):
            idx = (row[(i - 1) % n] << 2) | (row[i] << 1) | row[(i + 1) % n]
            nr.append((rule >> idx) & 1)
        row = nr; rows.append(row[:])
    _CA_CACHE[key] = rows
    return rows


_LOGI_CACHE = {}


def _logistic_orbit(seed: int, length: int = 512):
    r = _LOGI_CACHE.get(seed)
    if r is not None:
        return r
    x = 0.1 + 0.8 * _vnoise(seed, 7)
    orbit = []
    for _ in range(length):
        x = 3.92 * x * (1.0 - x)
        orbit.append(x)
    _LOGI_CACHE[seed] = orbit
    return orbit


_LORENZ_CACHE = {}


def _lorenz_orbit(seed: int, length: int = 1024):
    """洛伦兹吸引子 x 分量归一轨迹 [0,1]（确定性预计算缓存）。"""
    r = _LORENZ_CACHE.get(seed)
    if r is not None:
        return r
    x = 0.1 + 0.2 * _vnoise(seed, 3)
    y = 0.0; z = 0.0
    s, rho, beta, dt = 10.0, 28.0, 8.0 / 3.0, 0.008
    xs = []
    for _ in range(length + 200):
        dx = s * (y - x); dy = x * (rho - z) - y; dz = x * y - beta * z
        x += dx * dt; y += dy * dt; z += dz * dt
        xs.append(x)
    xs = xs[200:]
    lo, hi = min(xs), max(xs)
    rng = max(1e-6, hi - lo)
    orbit = [(v - lo) / rng for v in xs]
    _LORENZ_CACHE[seed] = orbit
    return orbit


_LANGTON_CACHE = {}


def _langton_rows(n: int, seed: int):
    """朗顿蚂蚁 1D 投影：蚂蚁在环形格上爬行，记录每代格态行（确定性缓存）。"""
    key = (n, seed)
    r = _LANGTON_CACHE.get(key)
    if r is not None:
        return r
    cells = [0] * n
    ant = (seed % n); d = 1
    rows = []
    for _g in range(max(16, n * 2)):
        c = cells[ant]
        d = (d * (1 if c == 0 else -1))  # 翻转方向
        cells[ant] ^= 1
        ant = (ant + d) % n
        rows.append(cells[:])
    _LANGTON_CACHE[key] = rows
    return rows


def _advanced_kernel(kind: str):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        speed = float(params.get("speed", 1.0))
        scale = float(params.get("scale", 1.0))
        freq = float(params.get("spatial_freq", 3.0))
        seed = int(params.get("seed", 0))
        density = float(params.get("density", 0.5))
        n = max(1, len(sorted_fx))
        rows = _ca_rows(110 if kind == "ca110" else 90, n, seed) if kind in ("ca90", "ca110") else None
        orbit = _logistic_orbit(seed) if kind == "chaos" else None
        # 粒子（确定性）
        if kind == "particle":
            pc = max(2, int(params.get("steps", 10)))
            parts = [(_vnoise(k, seed), 0.3 + 0.7 * _vnoise(k, seed + 31)) for k in range(pc)]
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            if kind == "fbm":
                w = _fbm(pos * freq + t * speed * 0.5, seed, 4)
            elif kind == "chaos":
                idx = (int(t * speed * 8) + seq_i * 3) % len(orbit)
                w = orbit[idx]
            elif kind == "standing":   # 驻波
                w = abs(math.sin(pos * math.pi * freq)) * (0.5 - 0.5 * math.cos(t * speed * 2 * math.pi))
            elif kind == "interference":  # 双源干涉
                s1, s2 = 0.2, 0.8
                d1 = abs(pos - s1); d2 = abs(pos - s2)
                w = 0.5 + 0.25 * (math.cos(d1 * freq * 6 - t * speed * 4) + math.cos(d2 * freq * 6 - t * speed * 4))
            elif kind in ("ca90", "ca110"):
                gen = int(t * speed * 4) % len(rows)
                w = float(rows[gen][seq_i])
            elif kind == "particle":
                acc = 0.0
                for (p0, v) in parts:
                    pp = (p0 + v * t * speed * 0.3) % 1.0
                    d = abs(pos - pp); d = min(d, 1.0 - d)
                    acc += math.exp(-(d * d) / (2 * (0.04 * scale) ** 2))
                w = min(1.0, acc)
            elif kind == "turing":   # 反应扩散近似（不可公约正弦阈值斑纹）
                v = (math.sin(pos * freq * 6.3 + t * speed) +
                     math.sin(pos * freq * 9.7 - t * speed * 0.7) +
                     math.sin(pos * freq * 14.1 + t * speed * 0.3))
                w = _smooth(0.5 + 0.18 * v)
            elif kind == "voronoi":  # 漂移泰森距离场
                nc = max(2, int(params.get("steps", 6)))
                best = 9.9
                for k in range(nc):
                    c = (_vnoise(k, seed) + 0.1 * math.sin(t * speed + k)) % 1.0
                    d = abs(pos - c); d = min(d, 1.0 - d)
                    best = min(best, d)
                w = 1.0 - min(1.0, best * freq * 2.0)
            elif kind == "moire":    # 莫尔拍频
                w = 0.5 + 0.5 * math.sin(pos * freq * 6 + t * speed) * math.sin(pos * freq * 6.6 - t * speed * 0.8)
            elif kind == "ripplefield":  # 中心涟漪场
                d = abs(pos - 0.5) * 2.0
                w = 0.5 + 0.5 * math.sin(d * freq * 4 - t * speed * 4)
            elif kind == "swarm":    # 群集密度（漂移吸引子）
                acc = 0.0
                for k in range(3):
                    a = (0.5 + 0.4 * math.sin(t * speed * (0.6 + 0.2 * k) + k * 2.1))
                    d = abs(pos - a); d = min(d, 1.0 - d)
                    acc += math.exp(-(d * d) / (2 * 0.09))
                w = min(1.0, acc * density * 2.0)
            elif kind == "lzwave":   # 弹簧链行波+反射（确定性叠加）
                w = 0.5 + 0.25 * (math.sin((pos * freq - t * speed) * 2 * math.pi) +
                                  math.sin((pos * freq + t * speed) * 2 * math.pi))
            # ── 自然/物理 续 ──
            elif kind == "avalanche":   # 沙堆自组织临界：缓积累+随机崩塌级联
                acc = (pos + t * speed * 0.15) % 1.0
                ev = _vnoise(int((t * speed * 0.5)) + seq_i, seed)
                w = acc if ev > 0.12 else (1.0 - acc) * 0.3
            elif kind == "capillary":   # 毛细爬升：液面爬升再回落
                lvl = 0.5 - 0.5 * math.cos(t * speed * 2 * math.pi)
                w = _smooth(1.0 - abs(pos - lvl) * 3.0) if pos < lvl + 0.1 else 0.0
            elif kind == "resonant":    # 共振水柱：被周期力驱动的共振幅
                drive = math.sin(t * speed * 2 * math.pi)
                q = 0.7 + 0.3 * math.sin(pos * freq * 3)
                w = 0.5 + 0.5 * drive * q
            elif kind == "geyser":      # 间歇泉模型：压力积累→喷发→冷却
                cyc = (t * speed * 0.3 + pos * 0.2) % 1.0
                if cyc < 0.6:
                    w = 0.1 + 0.15 * cyc / 0.6           # 缓慢积累
                elif cyc < 0.75:
                    w = 1.0                                # 喷发
                else:
                    w = (1.0 - (cyc - 0.75) / 0.25) * 0.4  # 冷却
            elif kind == "firewall":    # 火焰扩散：热上升+湍流
                base = 1.0 - pos
                turb = 0.4 * _fbm(pos * 8 + t * speed * 2, seed, 3)
                w = _clamp01(base * (0.6 + turb) + 0.2 * math.sin(t * speed * 6 + pos * 10))
            elif kind == "raindrops":   # 多落点涟漪干涉
                acc = 0.0
                for k in range(4):
                    c = _vnoise(k, seed)
                    tt = (t * speed + _vnoise(k, seed + 9) * 3.0) % 3.0
                    d = abs(pos - c)
                    acc += math.cos((d * freq * 8 - tt * 6)) * math.exp(-tt * 0.6) * math.exp(-d * 3)
                w = 0.5 + 0.5 * _clamp01(0.5 + acc)
            # ── 数学/混沌 续 ──
            elif kind == "ca30":
                grows = _ca_rows(30, n, seed)
                gen = int(t * speed * 4) % len(grows)
                w = float(grows[gen][seq_i])
            elif kind == "ca184":   # 交通流
                grows = _ca_rows(184, n, seed)
                gen = int(t * speed * 4) % len(grows)
                w = float(grows[gen][seq_i])
            elif kind == "harmonic":    # 谐波加法合成
                hn = max(1, int(params.get("steps", 4)))
                acc = 0.0; norm = 0.0
                for h in range(1, hn + 1):
                    acc += (1.0 / h) * math.sin((pos * freq - t * speed) * 2 * math.pi * h)
                    norm += 1.0 / h
                w = 0.5 + 0.5 * acc / max(1e-6, norm)
            elif kind == "phyllotaxis":  # 向日葵黄金角相位
                ph = (seq_i * 0.61803398875) % 1.0
                w = 0.5 - 0.5 * math.cos((ph + t * speed) * 2 * math.pi)
            elif kind == "fibonacci":   # 斐波那契节律
                fib = [1, 2, 3, 5, 8, 13, 21]
                step = fib[seq_i % len(fib)]
                w = 1.0 if (int(t * speed * 4) % step) == 0 else 0.0
            elif kind == "dpend":       # 双摆混沌投影
                o = _logistic_orbit(seed + seq_i % 7)
                idx = int(t * speed * 6) % len(o)
                w = o[idx]
            # ── 群体/涌现 ──
            elif kind == "firefly":     # 萤火虫同步
                nat = 0.7 + 0.3 * _vnoise(seq_i, seed)
                couple = 0.15 * math.sin(t * speed * 2 * math.pi)
                ph = (t * speed * nat + couple + _vnoise(seq_i, seed + 3)) % 1.0
                w = 1.0 if ph < 0.12 else math.exp(-(ph) * 3) * 0.2
            elif kind == "forestfire":  # 森林火灾 SIR
                front = (t * speed * 0.4) % 1.4
                d = pos - (front - 0.2)
                if 0 <= d < 0.15:
                    w = 1.0                       # 燃烧前沿
                elif d < 0:
                    w = max(0.0, 0.4 + 0.4 * _vnoise(seq_i, seed))  # 未燃（树）
                else:
                    w = 0.05                      # 烧尽
            elif kind == "pheromone":   # 蚁群信息素轨迹
                trail = 0.5 + 0.5 * math.sin(pos * freq * 4 - t * speed * 3)
                decay = math.exp(-((pos - (t * speed * 0.3) % 1.0) ** 2) / 0.05)
                w = _clamp01(trail * 0.3 + decay)
            elif kind == "startle":     # 惊飞级联
                trig = (t * speed * 0.5) % 1.0
                d = abs(pos - 0.5) * 2.0
                w = 1.0 if abs(d - trig) < 0.12 else 0.05
            # ── 空间场 ──
            elif kind == "vortex":      # 涡旋场
                d = abs(pos - 0.5) * 2.0
                w = 0.5 + 0.5 * math.sin(d * freq * 5 - t * speed * 5 + d * 8)
            elif kind == "gravity":     # 引力场（漂移吸引子）
                acc = 0.0
                for k in range(3):
                    a = 0.5 + 0.4 * math.sin(t * speed * 0.5 + k * 2.1)
                    d = abs(pos - a)
                    acc += 1.0 / (1.0 + (d * freq * 4) ** 2)
                w = min(1.0, acc)
            elif kind == "magnet":      # 磁极吸斥
                npole = 0.3 + 0.2 * math.sin(t * speed)
                spole = 0.7 - 0.2 * math.sin(t * speed)
                w = _clamp01(0.5 + 0.5 / (1 + ((pos - npole) * freq * 5) ** 2)
                             - 0.5 / (1 + ((pos - spole) * freq * 5) ** 2))
            # ── 视觉错觉/生理 ──
            elif kind == "shepard":     # 谢泼德楼梯：永恒上升
                acc = 0.0
                for k in range(4):
                    ph = (pos * (k + 1) + t * speed + k * 0.25) % 1.0
                    acc += ph * (0.5 - 0.5 * math.cos((k + 1) * 0.5))
                w = (acc / 4.0) % 1.0
            elif kind == "physio":      # 生理节律：呼吸×心跳×叹息
                br = 0.5 - 0.5 * math.cos(t * speed * 0.3 * 2 * math.pi)
                hb = 0.3 * math.exp(-((t * speed % 1.0) - 0.1) ** 2 / 0.004)
                w = _clamp01(br * 0.7 + hb + 0.1 * math.sin(t * speed * 0.07))
            elif kind == "drift":       # 错觉漂移
                w = 0.5 + 0.5 * math.sin(pos * freq * 6 + math.sin(t * speed + pos * 3) * 2)
            elif kind == "tidemodel":   # 天文潮汐：多正弦叠加
                w = (0.5 + 0.2 * math.sin(t * speed * 0.9 + pos * 2)
                     + 0.15 * math.sin(t * speed * 1.93 + pos)
                     + 0.1 * math.sin(t * speed * 0.5))
            # ── 电机单轴物理 ──
            elif kind == "damped":      # 阻尼振荡回中
                cyc = (t * speed * 0.5 + pos * 0.3)
                w = 0.5 + 0.5 * math.cos(cyc * 2 * math.pi * 3) * math.exp(-(cyc % 1.0) * 3)
            elif kind == "overshoot":   # 弹性过冲settle
                p = (t * speed + pos * 0.4) % 1.0
                w = _clamp01(1.0 - math.cos(p * math.pi * 1.5) * math.exp(-p * 4)) if p < 0.6 else 1.0
            elif kind == "inertia":     # 惯性扫掠
                w = _smooth(_smooth(0.5 - 0.5 * math.cos((t * speed + pos * 0.3) * 2 * math.pi)))
            elif kind == "pendwave":    # 钟摆波：相位失谐摆阵的聚散行波
                period = 1.0 + pos * 0.6 / max(0.2, scale)
                w = 0.5 - 0.5 * math.cos(t * speed * 2 * math.pi / period)
            elif kind == "lorenz":      # 洛伦兹吸引子混沌
                o = _lorenz_orbit(seed)
                idx = (int(t * speed * 24) + seq_i * 5) % len(o)
                w = o[idx]
            elif kind == "soliton":     # 孤波对穿：两 sech² 孤立子穿越互不干扰
                c1 = (t * speed * 0.3) % 1.4 - 0.2
                c2 = 1.2 - (t * speed * 0.22) % 1.4
                def _sech2(d):
                    return 1.0 / math.cosh(d * freq * 3.0) ** 2
                w = _clamp01(_sech2(pos - c1) + _sech2(pos - c2))
            elif kind == "wavepacket":  # 波包：高斯包络载波，色散漂移
                c = (t * speed * 0.3) % 1.0
                d = pos - c
                env = math.exp(-(d * d) * freq * 4.0)
                w = 0.5 + 0.5 * env * math.cos((pos * freq * 6 - t * speed * 5) * math.pi)
            elif kind == "metaball":    # 融球：等值面阈值合并的水银融并
                acc = 0.0
                for k in range(3):
                    c = 0.5 + 0.4 * math.sin(t * speed * (0.5 + 0.2 * k) + k * 2.1)
                    d = abs(pos - c); d = min(d, 1.0 - d)
                    acc += (0.04 * scale) / max(1e-4, d * d + 0.002)
                w = _clamp01((acc - 1.0) * 0.6)
            elif kind == "diffusion":   # 热扩散：墨滴扩散变宽再复位
                cyc = (t * speed * 0.3) % 1.0
                sig = 0.03 + cyc * 0.4 * scale
                d = abs(pos - 0.5)
                w = math.exp(-(d * d) / (2 * sig * sig))
            elif kind == "warp":        # 扭曲噪声：fbm 域扭曲的漩涡质感
                q = _fbm(pos * freq + t * speed * 0.3, seed, 3)
                w = _fbm(pos * freq + 4.0 * q + t * speed * 0.2, seed + 51, 3)
            elif kind == "doppler":     # 多普勒：移动源压缩/拉伸波前
                src = 0.5 + 0.45 * math.sin(t * speed * 0.6)
                d = pos - src
                k = freq * 6.0 / (1.0 + 0.6 * (1 if d >= 0 else -1) * math.sin(t * speed * 0.6))
                w = 0.5 + 0.5 * math.sin(d * k - t * speed * 5)
            elif kind == "fracband":    # 分形带：1D 逃逸时间分形条纹
                c = -0.8 + 1.6 * pos + 0.3 * math.sin(t * speed * 0.5)
                z = 0.0; it = 0
                for it in range(24):
                    z = z * z + c
                    if abs(z) > 2.0:
                        break
                w = it / 24.0
            elif kind == "langton":     # 朗顿蚂蚁
                rows = _langton_rows(n, seed)
                gen = int(t * speed * 6) % len(rows)
                w = float(rows[gen][seq_i])
            elif kind == "vortexstreet":  # 龙卷涡街：交替脱落的涡
                row = seq_i % 2
                w = 0.5 + 0.5 * math.sin((pos * freq * 5 - t * speed * 4) * math.pi + row * math.pi)
                w *= math.exp(-abs(pos - 0.5) * 1.5) + 0.3
                w = _clamp01(w)
            elif kind == "lightningfork":  # 闪电分叉：主干带随机分支的瞬亮
                ph = (t * speed) % 1.0
                main = pos < ph * 1.2
                branch = _vnoise(seq_i, seed + int(t * speed)) < 0.3
                flash = ph < 0.12
                w = (1.0 if (main and flash) else (0.5 if (branch and flash) else 0.0))
                w += 0.25 * math.exp(-ph * 4) * (1.0 if main else 0.0)
                w = _clamp01(w)
            elif kind == "string":      # 弦振动模态：多驻波模态叠加
                modes = max(1, int(params.get("steps", 3)))
                acc = 0.0; norm = 0.0
                for m in range(1, modes + 1):
                    acc += (1.0 / m) * math.sin(m * pos * math.pi) * math.cos(t * speed * m * 1.5)
                    norm += 1.0 / m
                w = 0.5 + 0.5 * acc / max(1e-6, norm)
            elif kind == "ecg":         # 心电图：PQRST 波群沿线行进
                lp = (pos - (t * speed * 0.5)) % 1.0
                w = (0.1 * math.exp(-((lp - 0.2) ** 2) / 0.001)    # P
                     - 0.15 * math.exp(-((lp - 0.46) ** 2) / 0.0002)  # Q
                     + 1.0 * math.exp(-((lp - 0.5) ** 2) / 0.0002)    # R
                     - 0.2 * math.exp(-((lp - 0.54) ** 2) / 0.0003)   # S
                     + 0.25 * math.exp(-((lp - 0.72) ** 2) / 0.002))  # T
                w = _clamp01(0.15 + w)
            elif kind == "contour":     # 等高线行进：fbm 场的等值线带
                v = _fbm(pos * freq + t * speed * 0.3, seed, 4)
                band = (v * 6.0) % 1.0
                w = 1.0 - abs(band - 0.5) * 2.0
            else:
                w = _fbm(pos * freq + t * speed, seed, 3)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["adv"] = _advanced_kernel


# ───────────────────────── 音乐/节律内核（BPM 锁定，确定性） ─────────────────────────
def _euclid(k: int, n: int):
    """欧几里得节奏：把 k 个脉冲尽量均匀分布到 n 步。"""
    if n <= 0:
        return []
    pattern = []
    bucket = 0
    for i in range(n):
        bucket += k
        if bucket >= n:
            bucket -= n
            pattern.append(1)
        else:
            pattern.append(0)
    return pattern


# ───────────────────────── 编组内核（formation）─────────────────────────
# 把整个设备阵列当作一个集体：每个设备的 pan/tilt = f(它在队列中相对中心的位置, t)，
# 从而拼出海鸥/蝴蝶/行进扇 等关系性形态。全部确定性 f(seq_i, t)，可跳读复现。


def _prog_wave(phase: float, x_env: float, y_base: float, y_env: float) -> tuple:
    """辅助 _formation_pose：行进波形 (x, y) = (sin(phase)*x_env, y_base+cos(phase)*y_env)。

    R173 静态俯仰修复抽出的公共子函数——dragon/fishtail/tide 三种姿态的俯仰(y)
    改为随同一相位同步起伏（此前恒为常量 u*k 或固定偏移，是本轮修的静态俯仰
    bug）。抽成子函数是为了不增加 `_formation_pose` 本身的行数/分支（该函数已
    是圈复杂度超限的旧函数，CLAUDE.md 10.2 要求旧超限函数不得再增长）。
    """
    return (math.sin(phase) * x_env, y_base + math.cos(phase) * y_env)


def _formation_pose(kind, u, pos, seq_i, n, t, p):
    """返回 (x, y) ∈ 大致[-1,1]，x→pan、y→tilt 偏移量（再乘角度半径）。
    u = 设备到队列中心的有符号位置 [-1,1]；pos = [0,1]；p = 参数 dict。"""
    if kind in _POSE_KINDS:  # 扩充姿态库（2026-08-12，见 effect_presets_curated_ext）
        return _formation_pose_ext(kind, u, pos, seq_i, n, t, p)
    speed = float(p.get("speed", 0.5))
    groups = max(1, int(p.get("groups", 1)))
    spread = float(p.get("spread", 0.0))
    ph = t * speed
    au = abs(u)
    par = 1.0 if (seq_i % 2 == 0) else -1.0
    g = (pos * groups) % 1.0          # 组内归一位置
    gu = g * 2.0 - 1.0                # 组内有符号位置 [-1,1]
    TWO = 2 * math.pi

    if kind == "seagull":      # 海鸥振翅：翼尖从V下扫到V上的深扑（强时变）
        wing = math.cos(ph * TWO)
        return (u, -au * (0.2 + 0.8 * wing) - 0.05)
    if kind == "vee":          # 雁阵 V 飞：近静态 V 字带整体微起伏
        return (u, -au + 0.2 * math.sin(ph * TWO))
    if kind == "butterfly":    # 蝶舞开合：中心定，两翼对称扑动
        wing = 0.5 + 0.5 * math.cos(ph * TWO)
        return (u * (0.4 + 0.6 * wing), au * wing * par * 0.3 + au * 0.4)
    if kind == "dragonfly":    # 蜻蜓：四翼小幅高频扑
        flap = math.sin(ph * TWO * 2.0)
        return (u, au * 0.5 + 0.4 * par * flap)
    if kind == "peacock":      # 孔雀开屏：扇面渐次张开
        open_amt = 0.5 - 0.5 * math.cos(ph * TWO)
        return (u * open_amt, -au * open_amt + (1.0 - open_amt) * 0.2)
    if kind == "peacock_close":  # 孔雀收屏：从全开缓合
        open_amt = 0.5 + 0.5 * math.cos(ph * TWO)
        return (u * open_amt, -au * open_amt)
    if kind == "arch":         # 拱桥起伏：tilt 抛物线 + 整体呼吸
        return (u, (1.0 - u * u) * (0.5 + 0.5 * math.sin(ph * TWO)) - 0.3)
    if kind == "wheat":        # 麦浪：tilt 行进正弦波
        return (u * 0.3, math.sin((pos * 2.0 - ph) * TWO))
    if kind == "dualwave":     # 双波交织：两列反向行进波叠加
        return (math.sin((pos - ph) * TWO) * 0.6, math.sin((pos + ph) * TWO) * 0.6)
    if kind == "heart":        # 心形绽放：阵列连成跳动心形
        a = pos * TWO
        hx = 16 * math.sin(a) ** 3 / 16.0
        hy = (13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)) / 16.0
        beat = 0.85 + 0.15 * math.exp(-((ph % 1.0) - 0.1) ** 2 / 0.01)
        return (hx * beat, hy * beat)
    if kind == "crown":        # 王冠环绕：环形齐起伏
        a = pos * TWO
        r = 0.7 + 0.3 * math.sin(ph * TWO + pos * TWO * groups)
        return (math.cos(a) * r, math.sin(a) * r)
    if kind == "fountain":     # 喷涌发散：中心向外喷射张角
        sh = 0.5 - 0.5 * math.cos(ph * TWO)
        return (u * (0.3 + 0.7 * sh), -1.0 + au * 0.6 + 0.4 * sh)
    if kind == "fan_open":     # 扇形开合：整把扇原地开合
        open_amt = 0.5 - 0.5 * math.cos(ph * TWO)
        return (u * open_amt, -au * open_amt * 0.5)
    if kind == "phyllo":       # 花序螺旋：黄金角分布旋转
        a = seq_i * 2.39996 + ph * TWO
        r = math.sqrt(pos)
        return (math.cos(a) * r, math.sin(a) * r)
    if kind == "pendarray":    # 钟摆阵：相位失谐 → 物理钟摆波
        period = 1.0 + pos * 0.8
        return (math.sin(ph * TWO / period), -0.3 + 0.2 * abs(math.sin(ph * TWO / period)))
    if kind == "seesaw":       # 跷跷板：奇偶反向起落
        return (u, par * math.sin(ph * TWO) * (0.4 + 0.6 * au))
    if kind == "mirrordance":  # 镜花对舞：左右镜像对称舞动
        s = math.sin(ph * TWO + au * 2.0)
        return (u * (0.6 + 0.4 * s), s * 0.5 * (1.0 if u >= 0 else 1.0))
    if kind == "funnel":       # 漏斗汇聚：向中心收拢再张开
        k = 0.5 + 0.5 * math.cos(ph * TWO)
        return (u * k, -au * (1.0 - k))
    if kind == "zipper":       # 拉链开合：从中心向两端依次开
        thr = (0.5 - 0.5 * math.cos(ph * TWO))
        op = 1.0 if au < thr else 0.0
        return (u * (0.3 + 0.7 * op), au * op * 0.5 - 0.2)
    if kind == "dna":          # DNA 双螺旋：两股相位相反旋绕
        a = pos * TWO * groups + ph * TWO
        return (math.cos(a) * 0.5, par * math.sin(a) * 0.6)
    if kind == "ripple_out":   # 涟漪外扩：tilt 同心环外扩
        return (u * 0.3, math.sin((au - ph) * TWO * groups))
    if kind == "dragon":       # 游龙摆尾：S 形行波幅度沿线增大（俯仰随行波同步起伏，非静态）
        return _prog_wave((pos * 2.0 - ph) * TWO, 0.2 + 0.8 * pos, u * 0.3, 0.15 + 0.6 * pos)
    if kind == "horn":         # 号角张合：喇叭口开合
        op = 0.5 - 0.5 * math.cos(ph * TWO)
        return (u, math.copysign(math.sqrt(au), u) * op - 0.2)
    if kind == "mountains":    # 群山起伏：错相高低峰峦
        return (u * 0.2, math.sin(pos * TWO * groups + ph * TWO) * (0.5 + 0.5 * au))
    if kind == "checker":      # 棋盘错动：奇偶分块反向平移
        return (par * math.sin(ph * TWO) * 0.6, par * math.cos(ph * TWO) * 0.4)
    if kind == "windmill":     # 风车旋臂：绕中心整体旋转
        a = ph * TWO + pos * TWO
        return (math.cos(a) * au, math.sin(a) * au)
    if kind == "fishtail":     # 鱼尾 S 摆：尾端摆幅大（俯仰随摆动同步起伏，非静态）
        return _prog_wave((pos - ph) * TWO, 0.2 + 0.8 * pos, u * 0.2, 0.15 + 0.5 * pos)
    if kind == "satellite":    # 卫星绕心：中心定、其余等半径绕圈（相位按序错开）
        if au < 1e-3:
            return (0.0, 0.0)
        a = ph * TWO + pos * TWO * 2.0
        return (math.cos(a) * 0.8, math.sin(a) * 0.8)
    if kind == "weave":        # 编织交叉：相邻设备交叉穿插
        return (u + par * math.sin(ph * TWO) * 0.4, par * math.cos(ph * TWO) * 0.4)
    if kind == "domino":       # 倾倒多米诺：依次倾倒再立起
        front = (ph % 1.0) * 1.2
        fall = _clamp01((front - pos) * 4.0)
        return (u, -fall * 0.8 + 0.4)
    if kind == "scissors":     # 剪刀对开：上下两组反向开合
        op = math.sin(ph * TWO)
        return (u * 0.4, par * op * (0.3 + 0.7 * au))
    if kind == "cascade":      # 此起彼伏：行进起伏波
        return (u * 0.2, 0.5 - 0.5 * math.cos((pos * groups - ph) * TWO))
    if kind == "school":       # 鱼群涌动：群体同向缓漂 + 个体偏移
        drift = math.sin(ph * TWO + pos * 3.0)
        wob = 0.3 * (_vnoise(seq_i, 7) - 0.5)
        return (drift + wob, math.cos(ph * TWO + pos * 2.0) * 0.5 + wob)
    if kind == "turn":         # 列阵转向：整列依次转向同角
        a = math.sin(ph * TWO) * (1.0 - 0.3 * pos)
        return (a, math.cos(ph * TWO) * 0.3)
    if kind == "tide":         # 潮汐摆：缓慢左右摇摆带位差（水位随潮汐相位起落，非静态）
        return _prog_wave(ph * TWO + pos * 1.5, 0.5 + 0.5 * au, -0.2, 0.25)
    if kind == "galaxy":       # 星河旋臂：对数螺旋旋转
        arms = max(1, groups)
        a = pos * TWO * arms + ph * TWO + pos * 3.0
        r = pos
        return (math.cos(a) * r, math.sin(a) * r)
    if kind == "spread":       # 聚拢/发散：径向呼吸
        k = 0.5 - 0.5 * math.cos(ph * TWO)
        return (u * k, (au * 2.0 - 1.0) * k * 0.5)
    if kind == "bow":          # 拱形波：弓形起伏整体平移
        return (u, math.sin((u - math.sin(ph) ) * math.pi) * 0.6)
    if kind == "flutter":      # 全场振翅：全体高频小幅扑动
        return (u * 0.3, 0.4 * math.sin(ph * TWO * 3 + pos * 6))
    if kind == "spiral_in":    # 螺旋收束：边旋边向心收
        a = pos * TWO + ph * TWO
        r = 0.5 + 0.5 * math.cos(ph * 0.5 * TWO)
        return (math.cos(a) * r, math.sin(a) * r)
    if kind == "swing":        # 整列同摆：单摆同步左右（俯仰随摆动象限相位起伏，非静态）
        return (math.sin(ph * TWO) * (0.6 + 0.4 * au), -0.3 + math.cos(ph * TWO) * 0.25)
    if kind == "vortex_form":  # 漩涡阵：越靠外转越快
        a = ph * TWO * (0.5 + au) + pos * TWO
        return (math.cos(a) * au, math.sin(a) * au)
    # ── 交替覆盖扇族（带行进相位的对称三角角度场）──
    if kind in ("fan_traverse", "fan_breathe", "fan_wave", "fan_cross",
                "fan_sweep", "fan_relay", "fan_bidir", "fan_drift",
                "fan_step", "fan_ripple", "fan_mirror", "fan_converge"):
        # 三角角度场：零交叉=扇心、峰=扇缘；相位推进使扇心走到旧扇缘处 → 重叠覆盖
        gp = pos * groups
        if kind == "fan_traverse":
            tri = 1.0 - abs(((gp - ph) % 1.0) * 2.0 - 1.0)
            x = (tri * 2.0 - 1.0)
        elif kind == "fan_breathe":   # 原地对称开合
            op = 0.5 - 0.5 * math.cos(ph * TWO)
            x = gu * op
        elif kind == "fan_wave":      # 依次开成行进波
            op = 0.5 - 0.5 * math.cos((pos * groups - ph) * TWO)
            x = gu * op
        elif kind == "fan_cross":     # 相邻扇互叉成 X
            grp_idx = int(pos * groups)
            sgn = 1.0 if grp_idx % 2 == 0 else -1.0
            op = 0.5 - 0.5 * math.cos(ph * TWO)
            x = gu * op * sgn
        elif kind == "fan_sweep":     # 全展同扫
            x = gu + math.sin(ph * TWO) * 0.6
        elif kind == "fan_relay":     # 一开一合交接
            grp_idx = int(pos * groups)
            local_ph = ph - grp_idx * 0.5
            op = max(0.0, math.sin(local_ph * math.pi))
            x = gu * op
        elif kind == "fan_bidir":     # 双向覆盖：奇偶组反向行进
            grp_idx = int(pos * groups)
            dir_s = 1.0 if grp_idx % 2 == 0 else -1.0
            tri = 1.0 - abs(((gp - ph * dir_s) % 1.0) * 2.0 - 1.0)
            x = tri * 2.0 - 1.0
        elif kind == "fan_drift":     # 扇心漂移
            drift = math.sin(ph * 0.5) * 0.5
            x = gu + drift
        elif kind == "fan_step":      # 阶梯开扇
            op = math.floor((ph % 1.0) * groups) / max(1, groups - 1)
            x = gu * op
        elif kind == "fan_ripple":    # 涟漪扇：从中心组向外依次开
            grp_idx = abs(int(pos * groups) - groups / 2.0) / max(1.0, groups / 2.0)
            op = 0.5 - 0.5 * math.cos((ph - grp_idx) * TWO)
            x = gu * op
        elif kind == "fan_mirror":    # 镜像扇
            x = abs(gu) * (1.0 if u >= 0 else -1.0) * (0.5 - 0.5 * math.cos(ph * TWO))
        else:  # fan_converge        # 收束扇
            op = 0.5 + 0.5 * math.cos(ph * TWO)
            x = gu * op
        return (x, -abs(x) * 0.4)
    # 默认圆
    a = ph * TWO + pos * TWO
    return (math.cos(a), math.sin(a))


def _formation_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        angle = float(params.get("angle", 1.0))
        center = int(params.get("center", 128))
        axis = params.get("axis", "双轴XY")
        mirror = bool(params.get("mirror", False))
        n = max(1, len(sorted_fx))
        rad = 127.0 * max(0.0, min(1.0, angle))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            u = pos * 2.0 - 1.0
            if mirror:
                u = abs(u)
            x, y = _formation_pose(kind, u, pos, seq_i, n, t, params)
            px = int(max(0, min(255, center + x * rad)))
            py = int(max(0, min(255, center + y * rad)))
            if axis == "单轴X":
                ef.values[orig_i]["motor_x"] = px
            elif axis == "单轴Y":
                ef.values[orig_i]["motor_y"] = py
            else:
                ef.values[orig_i]["motor_x"] = px
                ef.values[orig_i]["motor_y"] = py
    return fn


KERNELS["formation"] = _formation_kernel


# ───────────────────────── 电机序列程序（formseq）─────────────────────────
# 把若干编组造型当关键帧，按 phase 定位当前帧并 smoothstep 交叉淡化到下一帧。
# 序列定义在数据表里以 params["_seq"] 传入（造型 kind 列表），确定可复现。
_FORMSEQ_LIB = {
    "birds_dawn":   ["vee", "seagull", "spread", "phyllo"],          # 群鸟晨飞
    "butterfly_love": ["butterfly", "phyllo", "peacock"],            # 蝶恋花
    "tide_cycle":   ["tide", "wheat", "arch", "funnel"],             # 潮起潮落
    "parade":       ["turn", "checker", "windmill", "swing"],        # 列队点兵
    "ceremony":     ["funnel", "fountain", "crown", "peacock"],      # 开场仪典
    "twin_dragons": ["dragon", "dna", "weave", "fishtail"],          # 双龙戏珠
    "shanhaijing":  ["mountains", "tide", "ripple_out", "galaxy"],   # 山海经
    "ballet":       ["swing", "scissors", "mirrordance", "spread"],  # 机械芭蕾
    "peacock_tour": ["peacock", "fan_open", "crown", "peacock_close"],  # 孔雀巡礼
    "storm":        ["flutter", "vortex_form", "school", "cascade"], # 风暴来袭
    "daybreak":     ["arch", "fountain", "spread", "phyllo"],        # 破晓
    "galaxy_flow":  ["galaxy", "spiral_in", "windmill", "phyllo"],   # 星河流转
    "review":       ["vee", "turn", "checker", "swing"],             # 阅兵分列
    "hundred_birds": ["school", "vee", "seagull", "crown"],          # 百鸟朝凤
    "tornado":      ["vortex_form", "spiral_in", "funnel", "galaxy"],# 龙卷
    "ripple_sym":   ["ripple_out", "crown", "dualwave", "tide"],     # 涟漪交响
    "fan_show":     ["fan_breathe", "fan_wave", "fan_traverse", "fan_cross"],  # 扇舞
    "wings":        ["seagull", "butterfly", "dragonfly", "flutter"],# 振翅组曲
    "bloom_seq":    ["funnel", "spread", "heart", "phyllo"],         # 绽放
    "ocean":        ["tide", "wheat", "dualwave", "fountain"],       # 海之歌
    "march_wave":   ["domino", "cascade", "zipper", "mountains"],    # 行进波
    "celestial":    ["galaxy", "phyllo", "crown", "ripple_out"],     # 天体
    "courtship":    ["peacock", "butterfly", "dragonfly", "heart"],  # 求偶之舞
    "machine":      ["checker", "windmill", "scissors", "turn"],     # 机械律动
    "serpent":      ["dragon", "fishtail", "dna", "weave"],          # 群蛇
    "phoenix":      ["fountain", "peacock", "crown", "flutter"],     # 凤舞
    "carnival":     ["fan_wave", "windmill", "school", "spread"],    # 嘉年华
    "meditation":   ["tide", "swing", "arch", "spread"],             # 静心
    "victory":      ["vee", "crown", "fountain", "peacock"],         # 凯旋
    "kaleido":      ["mirrordance", "phyllo", "galaxy", "windmill"], # 万花筒
    # ── 本次扩充：3 个序列（2026-08-12，段来自新编组 kind）──
    "star_odyssey": ["star_trails", "constellation", "meteor_rain", "galaxy"],  # 星夜漫游
    "four_seasons": ["dandelion", "ripple_out", "windchime", "snowfall"],       # 四季流转
    "nine_heavens": ["eagle", "meteor_rain", "moon_phases", "fireworks_ring"],  # 九天揽月
}


def _formseq_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        hold = float(params.get("hold", 0.6))      # 停留 vs 过渡占比
        seq = params.get("_seq") or _FORMSEQ_LIB.get(kind, ["circle"])
        m = len(seq)
        center = int(params.get("center", 128))
        axis = params.get("axis", "双轴XY")
        angle = float(params.get("angle", 1.0))
        n = max(1, len(sorted_fx))
        rad = 127.0 * max(0.0, min(1.0, angle))
        gphase = (t * speed) % m
        idx = int(gphase)
        frac = gphase - idx
        # 停留区平、过渡区 smoothstep
        if frac < hold:
            blend = 0.0
        else:
            tt = (frac - hold) / max(1e-4, 1.0 - hold)
            blend = tt * tt * (3.0 - 2.0 * tt)
        k_cur = seq[idx % m]; k_nxt = seq[(idx + 1) % m]
        # 让造型自身动画继续推进：用与 formation 相同的内部时间
        ltime = t * 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            u = pos * 2.0 - 1.0
            x0, y0 = _formation_pose(k_cur, u, pos, seq_i, n, ltime, params)
            x1, y1 = _formation_pose(k_nxt, u, pos, seq_i, n, ltime, params)
            x = x0 + (x1 - x0) * blend
            y = y0 + (y1 - y0) * blend
            px = int(max(0, min(255, center + x * rad)))
            py = int(max(0, min(255, center + y * rad)))
            if axis == "单轴X":
                ef.values[orig_i]["motor_x"] = px
            elif axis == "单轴Y":
                ef.values[orig_i]["motor_y"] = py
            else:
                ef.values[orig_i]["motor_x"] = px
                ef.values[orig_i]["motor_y"] = py
    return fn


KERNELS["formseq"] = _formseq_kernel


# ───────────────────────── 通用交替覆盖（array）─────────────────────────
# 把"编组此起彼伏"思想下放到任意标量通道：阵列分 G 组各做鼓包，
# 鼓包行进使新组中心落到旧组边缘 → 组块覆盖波。确定性 f(seq_i, t)。
def _array_weight(kind, pos, seq_i, n, t, p):
    speed = float(p.get("speed", 1.0))
    groups = max(1, int(p.get("groups", 4)))
    spread = float(p.get("spread", 0.0))
    ph = t * speed
    par = 1.0 if (seq_i % 2 == 0) else -1.0
    gp = pos * groups
    TWO = 2 * math.pi
    if kind == "cover":        # 组块覆盖波：三角行进，重叠覆盖
        tri = 1.0 - abs(((gp - ph) % 1.0) * 2.0 - 1.0)
        return tri
    if kind == "cascade":      # 此起彼伏：逐组依次起落的离散鼓包（组间留间隙）
        grp = int(pos * groups)
        local_ph = ph - grp * 0.7
        bump = max(0.0, math.sin(local_ph * math.pi)) if 0.0 <= (local_ph % (groups * 0.7)) < 1.0 else 0.0
        return bump
    if kind == "relay":        # 接力波：一组亮完下一组
        grp = int(pos * groups)
        local = (ph - grp * 0.5) % (groups * 0.5)
        return max(0.0, math.sin(local * math.pi)) if local < 1.0 else 0.0
    if kind == "overlap":      # 重叠行进波：多正弦相位叠加
        return _clamp01(0.5 + 0.5 * (math.sin((pos * groups - ph) * TWO) * 0.6 +
                                     math.sin((pos * groups - ph * 1.3) * TWO) * 0.4))
    if kind == "mirror":       # 镜像覆盖：左右对称推进
        m = abs(pos - 0.5) * 2.0
        tri = 1.0 - abs(((m * groups - ph) % 1.0) * 2.0 - 1.0)
        return tri
    if kind == "zipperfill":   # 拉链填充：从中心向两端
        thr = (ph % 1.0)
        m = abs(pos - 0.5) * 2.0
        return 1.0 if m < thr else 0.0
    if kind == "parity":       # 奇偶交替：两组反相闪
        return 0.5 + 0.5 * math.cos(ph * TWO) * par
    if kind == "breathe_grp":  # 组块呼吸：各组整体呼吸错相
        grp = int(pos * groups)
        return 0.5 - 0.5 * math.cos((ph + grp / groups) * TWO)
    if kind == "stepcover":    # 阶梯覆盖：组逐级累积点亮再清零（楼梯填满）
        filled = int((ph % 1.0) * (groups + 1))
        return 1.0 if int(pos * groups) < filled else 0.1
    if kind == "ripplegrp":    # 涟漪组波：中心组起波外扩
        grp = abs(int(pos * groups) - groups / 2.0)
        return 0.5 - 0.5 * math.cos((ph - grp * 0.3) * TWO)
    if kind == "dualflow":     # 双向对涌：两端向中相遇
        a = 1.0 - abs(((pos * groups - ph) % 1.0) * 2.0 - 1.0)
        b = 1.0 - abs((((1 - pos) * groups - ph) % 1.0) * 2.0 - 1.0)
        return max(a, b)
    if kind == "peakdrift":    # 波峰漂移：单峰沿阵列慢移
        c = (ph % 1.0)
        d = abs(pos - c); d = min(d, 1.0 - d)
        return math.exp(-(d * d) / (2 * (0.5 / groups) ** 2))
    if kind == "rotate":       # 扇区轮播：依次点亮各扇区
        sect = int(pos * groups)
        active = int(ph * groups) % groups
        return 1.0 if sect == active else 0.1
    if kind == "wavefront":    # 波峰行进单向扫
        return 0.5 - 0.5 * math.cos((pos - ph) * TWO * groups)
    return 0.5 - 0.5 * math.cos((gp - ph) * TWO)


def _array_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            w = _array_weight(kind, pos, seq_i, n, t, params)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["array"] = _array_kernel


# ───────────────────────── 通用序列程序（seq）─────────────────────────
# 把若干"look"（波形/跑灯/噪声）当关键帧串接、smoothstep 交叉淡化 → 灯光秀程序。
def _look_weight(look, pos, seq_i, n, t, p):
    """单个 look 的标量权重 [0,1]。look=(内核类型, variant)。"""
    kind, var = look
    speed = float(p.get("speed", 1.0))
    n1 = max(1, n - 1)
    if kind == "scalar":
        freq = float(p.get("spatial_freq", 1.0))
        ph = pos * freq - t * speed
        return _waveshape(var, ph, float(p.get("duty", 0.5)))
    if kind == "chase":
        width = float(p.get("width", 0.15))
        head = (t * speed) % 1.0
        dist = (pos - head) % 1.0
        if var == "comet":
            return math.exp(-dist / max(0.01, width)) if dist < 0.6 else 0.0
        if var == "dot":
            return 1.0 if dist < width else 0.0
        return 1.0 if dist < width else 0.0
    if kind == "rand":
        seed = int(p.get("seed", 0))
        tick = int(t * speed * 6)
        r = _vnoise(seq_i * 13 + tick, seed)
        return 1.0 if r < float(p.get("density", 0.3)) else 0.0
    if kind == "breathe":
        return _smooth(0.5 - 0.5 * math.cos(t * speed * 2 * math.pi))
    if kind == "color":  # 用亮度近似
        return 0.5 - 0.5 * math.cos((pos + t * speed) * 2 * math.pi)
    return 0.5 - 0.5 * math.cos(t * speed * 2 * math.pi)


_SEQ_LIB = {
    "intro_arc":   [("breathe", "breathe"), ("scalar", "sine"), ("chase", "comet"), ("rand", "sparkle")],
    "climax":      [("scalar", "sine"), ("chase", "comet"), ("rand", "sparkle"), ("scalar", "square")],
    "calm_storm":  [("breathe", "breathe"), ("scalar", "tri"), ("rand", "jitter")],
    "color_journey": [("color", "rainbow"), ("color", "fire"), ("color", "sparkles")],
    "static_dyn":  [("breathe", "breathe"), ("chase", "dot"), ("breathe", "breathe")],
    "build_drop":  [("scalar", "saw_up"), ("rand", "sparkle"), ("scalar", "square"), ("breathe", "breathe")],
    "pulse_run":   [("scalar", "pulse"), ("chase", "comet"), ("scalar", "square")],
    "wave_suite":  [("scalar", "sine"), ("scalar", "tri"), ("scalar", "fold"), ("scalar", "twin")],
    "night_day":   [("breathe", "breathe"), ("scalar", "saw_up"), ("rand", "sparkle"), ("scalar", "saw_down")],
    "heartbeat_run": [("breathe", "heartbeat"), ("chase", "comet"), ("breathe", "heartbeat")],
    "spark_flow":  [("rand", "sparkle"), ("scalar", "sine"), ("chase", "dot")],
    "rise_fall":   [("scalar", "saw_up"), ("scalar", "sine"), ("scalar", "saw_down")],
    "groove":      [("scalar", "square"), ("scalar", "pulse"), ("chase", "comet")],
    "ambient_drift": [("breathe", "breathe"), ("rand", "perlin"), ("scalar", "sine")],
    "festival":    [("chase", "comet"), ("rand", "sparkle"), ("scalar", "square"), ("chase", "dot")],
    "ocean_seq":   [("scalar", "sine"), ("breathe", "tide"), ("scalar", "twin")],
    "fire_seq":    [("rand", "jitter"), ("scalar", "expo"), ("rand", "sparkle")],
    "march_seq":   [("scalar", "square"), ("chase", "dot"), ("scalar", "pulse")],
    "dream_seq":   [("breathe", "breathe"), ("color", "rainbow"), ("rand", "perlin")],
    "finale":      [("scalar", "sine"), ("chase", "comet"), ("rand", "sparkle"), ("scalar", "square"), ("breathe", "breathe")],
}


def _seq_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        speed = float(params.get("speed", 0.3))
        hold = float(params.get("hold", 0.6))
        looks = params.get("_looks") or _SEQ_LIB.get(kind, [("breathe", "breathe")])
        m = len(looks)
        n = max(1, len(sorted_fx))
        gphase = (t * speed) % m
        idx = int(gphase); frac = gphase - idx
        if frac < hold:
            blend = 0.0
        else:
            tt = (frac - hold) / max(1e-4, 1.0 - hold)
            blend = tt * tt * (3.0 - 2.0 * tt)
        L0 = looks[idx % m]; L1 = looks[(idx + 1) % m]
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            w0 = _look_weight(L0, pos, seq_i, n, t, params)
            w1 = _look_weight(L1, pos, seq_i, n, t, params)
            w = w0 + (w1 - w0) * blend
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["seq"] = _seq_kernel


# ═════════════════════ 水景：泵/阀 重写内核（仿 formation 范式）═════════════════════
# 设计：pump 与 valve 编排逻辑彻底分开。
#   pump  = 连续"高度剪影场" w∈[0,1]（一排喷头靠各自高度拼出水形轮廓）。
#   valve = "开合门控"场 w∈{0,1}/graded（编排靠"谁在何时开"）。
# 全部确定性 f(喷头位置, t)，可跳读复现。记号：u=2pos-1, au=|u|, ph=t·speed。
def _wrapdist(a, b):
    d = abs(a - b)
    return min(d, 1.0 - d)


def _apply_dir(pos, direction):
    if direction == "反向":
        return 1.0 - pos
    if direction == "中心向外":
        return abs(pos - 0.5) * 2.0
    if direction == "两侧向中":
        return 1.0 - abs(pos - 0.5) * 2.0
    return pos


_PEAK_CACHE = {}


def _peak_profile(n, groups, seed):
    key = (n, groups, seed)
    r = _PEAK_CACHE.get(key)
    if r is not None:
        return r
    prof = []
    for i in range(n):
        pos = i / max(1, n - 1)
        prof.append(0.4 + 0.6 * _vnoise(int(pos * groups * 3) + i, seed))
    _PEAK_CACHE[key] = prof
    return prof


def _pump_height(kind, pos, u, seq_i, n, t, p):
    """泵高度场 → w∈[0,1]。"""
    speed = float(p.get("speed", 1.0))
    freq = float(p.get("spatial_freq", 1.0))
    width = float(p.get("width", 0.18))
    groups = max(1, int(p.get("groups", 3)))
    span = float(p.get("span", 1.0))
    bend = float(p.get("bend", 2.0))
    steps = max(2, int(p.get("steps", 4)))
    seed = int(p.get("seed", 0))
    heads = max(1, int(p.get("heads", 3)))
    edge_compensate = bool(p.get("edge_compensate", False))
    ph = t * speed
    au = abs(u)
    par = 1.0 if (seq_i % 2 == 0) else -1.0
    TWO = 2 * math.pi
    if kind == "wavewall":      # 波浪水墙：高度行进正弦水幕
        return 0.5 - 0.5 * math.cos((pos * freq - ph) * TWO)
    if kind == "arch_static":   # 水拱·静拱：抛物高度+整体呼吸
        return _clamp01(1.0 - (u * span) ** 2) * (0.6 + 0.4 * math.sin(ph * TWO))
    if kind == "arch_move":     # 水拱·行拱：拱顶左右行走
        c = math.sin(ph)
        return _clamp01(1.0 - ((u - c) * span) ** 2)
    if kind == "crown":         # 皇冠涌：环列齐起伏带微相位差
        return 0.5 - 0.5 * math.cos((ph + pos * freq) * TWO)
    if kind == "willow":        # 垂柳：中心高边缘垂落带轻摆
        return _clamp01((1.0 - au ** bend) * (0.65 + 0.35 * math.sin(ph * 2 + pos * 6)))
    if kind == "peacock_open":  # 雀尾开屏：自中向缘渐增，开
        op = 0.5 - 0.5 * math.cos(ph * TWO)
        return (0.15 + 0.85 * au) * op
    if kind == "peacock_close": # 雀尾收屏
        op = 0.5 + 0.5 * math.cos(ph * TWO)
        return (0.15 + 0.85 * au) * op
    if kind == "peaks":         # 群峰：固定错落峰高轻摆
        prof = _peak_profile(n, groups, seed)
        base = prof[seq_i] if seq_i < len(prof) else 0.5
        return _clamp01(base * (0.7 + 0.3 * math.sin(ph + pos * 4)))
    if kind == "swell":         # 涌浪：整阵同步压力涌起落
        return _smooth(0.5 - 0.5 * math.cos(ph * TWO))
    if kind == "tide":          # 潮汐水位：主涌叠次涟漪（明显空间起伏）
        return _clamp01(0.5 - 0.4 * math.cos(ph * TWO) + 0.28 * math.sin(ph * 3 + pos * TWO * 2))
    if kind == "runjet":        # 跑泉：高斯高度包奔跑
        c = ph % 1.0
        d = _wrapdist(pos, c)
        return math.exp(-(d * d) / (2 * max(0.01, width) ** 2))
    if kind == "runjet_multi":  # 跑泉·多头
        w = 0.0
        for k in range(heads):
            c = (ph + k / heads) % 1.0
            d = _wrapdist(pos, c)
            w = max(w, math.exp(-(d * d) / (2 * max(0.01, width) ** 2)))
        return w
    if kind == "cascade":       # 此起彼伏：分组离散高度鼓包
        grp = int(pos * groups)
        if edge_compensate:
            # 末组错相 (groups-1)*0.6 拍 → 把窗扩到 [0,1+lag] 重映射，
            # 使首组早段、末组末段都在 ph∈[0,1] 内完整跑完 0→1→0 鼓包。
            gmax = max(1, groups - 1)
            gp = min(grp, gmax) / gmax
            total_lag = gmax * 0.6
            local = _clamp01(ph * (1.0 + total_lag) - gp * total_lag)
            return max(0.0, math.sin(local * math.pi))
        local = (ph - grp * 0.6) % max(1.0, groups * 0.6)
        return max(0.0, math.sin(local * math.pi)) if local < 1.0 else 0.0
    if kind == "opposing":      # 对涌：奇偶高低反相
        return _clamp01(0.5 + 0.5 * par * math.cos(ph * TWO))
    if kind == "spiral":        # 螺旋水阵：多臂行进高度
        return 0.5 - 0.5 * math.cos((pos * groups - ph) * TWO)
    if kind == "bloom":         # 绽放：高度环自中心外扩
        m = au
        return 0.5 - 0.5 * math.cos((m - ph) * TWO)
    if kind == "stair_water":   # 阶梯水位：量化高度台地行进
        if edge_compensate:
            # 行进错相系数 1.0（(pos+ph) 中 pos 项）→ total_lag=1.0，
            # 重映射使末喷头(pos=1)在 ph=1 时恰好登顶，不被切到 0。
            total_lag = 1.0
            local = _clamp01(ph * (1.0 + total_lag) - pos * total_lag)
            return math.floor(local * (steps - 1e-9)) / max(1, steps - 1)
        return math.floor(((pos + ph) % 1.0) * steps) / max(1, steps - 1)
    if kind == "supershoot_salvo":  # 超级水炮·齐射：全体慢蓄高喷急落
        lp = ph % 1.0; rise = 0.5; fall = 0.2
        return _smooth(lp / rise) if lp < rise else max(0.0, 1.0 - (lp - rise) / fall)
    if kind == "supershoot_seq":    # 超级水炮·顺射：顺序单点慢蓄高喷
        rise = 0.5; fall = 0.2
        if edge_compensate:
            # 顺射错相系数 0.5（pos*0.5）→ total_lag=0.5。把本地相位窗
            # 由 [0,1] 扩到 [0,1+0.5] 重映射：首喷头早段、末喷头(pos=1)
            # 在 ph 末段仍能完整跑完 蓄→喷→落，cue 在 ph=1 时全体收尾。
            total_lag = 0.5
            lp = _clamp01(ph * (1.0 + total_lag) - pos * total_lag)
            return _smooth(lp / rise) if lp < rise else max(0.0, 1.0 - (lp - rise) / fall)
        lp = (ph - pos * 0.5) % 1.0
        return _smooth(lp / rise) if lp < rise else max(0.0, 1.0 - (lp - rise) / fall)
    if kind == "pulse_surge":   # 脉涌：双拍压力脉冲
        pp = ph % 1.0
        return _clamp01(math.exp(-((pp - 0.1) ** 2) / 0.004) + 0.6 * math.exp(-((pp - 0.3) ** 2) / 0.004))
    if kind == "geyser":        # 间歇泉律动：蓄压→喷发→冷却
        cyc = (ph * 0.5 + pos * 0.1) % 1.0
        if cyc < 0.6:
            return 0.1 + 0.15 * cyc / 0.6
        if cyc < 0.75:
            return 1.0
        return (1.0 - (cyc - 0.75) / 0.25) * 0.4
    return 0.5 - 0.5 * math.cos(ph * TWO)


_EUCLID_CACHE = {}


def _euclid_cached(pulses, steps):
    key = (pulses, steps)
    r = _EUCLID_CACHE.get(key)
    if r is None:
        r = _euclid(max(1, min(pulses, steps)), max(1, steps))
        _EUCLID_CACHE[key] = r
    return r


def _valve_open(kind, pos, u, seq_i, n, t, p):
    """阀门门控场 → w∈[0,1]（多数为 {0,1}）。"""
    speed = float(p.get("speed", 1.0))
    duty = float(p.get("duty", 0.4))
    width = float(p.get("width", 0.15))
    groups = max(1, int(p.get("groups", 3)))
    density = float(p.get("density", 0.4))
    spread = float(p.get("spread", 0.0))
    steps = max(1, int(p.get("steps", 8)))
    pulses = max(1, int(p.get("pulses", 4)))
    seed = int(p.get("seed", 0))
    edge_compensate = bool(p.get("edge_compensate", False))
    ph = t * speed
    au = abs(u)
    TWO = 2 * math.pi
    if kind == "chop":          # 喷停：全阀同步占空开合
        return 1.0 if (ph % 1.0) < duty else 0.0
    if kind == "chop_fast":     # 碎喷：高速密集断续+概率
        on = (ph % 1.0) < duty
        keep = _vnoise(seq_i + int(ph * 2), seed) < density
        return 1.0 if (on and keep) else 0.0
    if kind == "dblclick":      # 双击喷：每周期两击
        lp = (ph * 2) % 1.0
        return 1.0 if lp < duty else 0.0
    if kind == "breach":        # 溃坝：全开骤起渐收
        lp = ph % 1.0
        return max(0.0, 1.0 - lp / max(0.02, duty))
    if kind == "pulsetrain":    # 脉冲列喷：欧氏节拍簇
        pat = _euclid_cached(pulses, steps)
        idx = int(ph * steps) % len(pat)
        return 1.0 if pat[idx] else 0.0
    if kind == "flow_breath":   # 流量呼吸：连续流量缓变
        return _smooth(0.5 - 0.5 * math.cos((ph + pos * spread) * TWO))
    if kind == "euclid":        # 欧氏喷律：均匀分布脉冲
        pat = _euclid_cached(pulses, steps)
        idx = int(ph * steps)
        return 1.0 if pat[(idx + seq_i) % len(pat)] else 0.0
    if kind == "probgate":      # 概率门喷：生成式随机触发
        step = int(ph * steps)
        return 1.0 if _vnoise(step + seq_i * 3, seed) < density else 0.0
    if kind == "runner":        # 跑阀：门窗沿阵列跑
        head = ph % 1.0
        if edge_compensate:
            # 默认窗回绕 (pos-head)%1，末喷头要等下一周期才被扫到，
            # 一次性 cue 里会被切。补偿改用非回绕单调扫描：head 从
            # -width 线性扫到 1，使 pos=0 在 ph≈0、pos=1 在 ph≈1 各被
            # 完整扫过一次门窗，cue 在 ph=1 时末喷头刚收尾。
            w = max(0.01, width)
            sweep = -w + ph * (1.0 + w)
            return 1.0 if (0.0 <= (pos - sweep) < w) else 0.0
        d = (pos - head) % 1.0
        return 1.0 if d < width else 0.0
    if kind == "wavewindow":    # 波窗扫：软边开阀窗扫过
        d = _wrapdist(pos, ph % 1.0)
        return _smooth(1.0 - d / max(0.01, width)) if d < width else 0.0
    if kind == "opposing":      # 对喷：奇偶交替开
        return 1.0 if (seq_i % 2) == (int(ph * 2) % 2) else 0.0
    if kind == "banks":         # N岸轮替：分组依次开
        sect = int(pos * groups)
        active = int(ph * groups) % groups
        return 1.0 if sect == active else 0.0
    if kind == "relay":         # 接力开阀：一组开满下一组
        grp = int(pos * groups)
        local = (ph - grp / groups) % 1.0
        return 1.0 if local < 1.0 / groups else 0.0
    if kind == "zipper":        # 拉链喷：中心向两端依次开
        thr = ph % 1.0
        return 1.0 if au < thr else 0.0
    if kind == "stair_banks":   # 阶梯开岸：分组逐级填满
        filled = int((ph % 1.0) * (groups + 1))
        return 1.0 if int(pos * groups) < filled else 0.0
    if kind == "headon":        # 对冲喷：两端向中相遇
        head = ph % 1.0
        d1 = (pos - head) % 1.0
        d2 = ((1.0 - pos) - head) % 1.0
        return 1.0 if (d1 < width or d2 < width) else 0.0
    if kind == "sparkle":       # 随机喷点：确定性伪随机点喷
        tick = int(ph * 4)
        return 1.0 if _vnoise(seq_i * 13 + tick, seed) < density else 0.0
    return 1.0 if (ph % 1.0) < duty else 0.0


def _pumpform_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        direction = params.get("direction", "正向")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = _apply_dir(seq_i / max(1, n - 1), direction)
            u = pos * 2.0 - 1.0
            w = _pump_height(kind, pos, u, seq_i, n, t, params)
            ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["pumpform"] = _pumpform_kernel


def _valvegate_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        direction = params.get("direction", "正向")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = _apply_dir(seq_i / max(1, n - 1), direction)
            u = pos * 2.0 - 1.0
            w = _valve_open(kind, pos, u, seq_i, n, t, params)
            ef.values[orig_i]["valve"] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["valvegate"] = _valvegate_kernel


# ── 组合：同时驱动 pump + valve，拼真实水景 ──
def _watercombo_sample(kind, pos, u, seq_i, n, t, p):
    """返回 (pump_w, valve_w) ∈ [0,1]。"""
    speed = float(p.get("speed", 1.0))
    freq = float(p.get("spatial_freq", 1.0))
    groups = max(1, int(p.get("groups", 3)))
    gate_thr = float(p.get("gate_thr", 0.15))
    width = float(p.get("width", 0.15))
    ph = t * speed
    au = abs(u)
    TWO = 2 * math.pi
    if kind == "peacock":       # 雀尾水炮：阀扇形开 + 泵自中向缘渐增
        op = 0.5 - 0.5 * math.cos(ph * TWO)
        pw = (0.15 + 0.85 * au) * op
        vw = 1.0 if au < op else 0.0
        return pw, vw
    if kind == "runner":        # 跑泉拖尾：阀行进窗 + 泵窗头高度衰减拖尾
        head = ph % 1.0
        d = (pos - head) % 1.0
        vw = 1.0 if d < width else 0.0
        pw = math.exp(-d / max(0.01, width * 2)) if d < 0.6 else 0.0
        return pw, vw
    if kind == "cascade":       # 此起彼伏炮：分组阀开 + 泵涌错相
        c = 0.5 - 0.5 * math.cos((pos * groups - ph) * TWO)
        return c, (1.0 if c > 0.5 else 0.0)
    if kind == "arch":          # 水拱开合：中心阀开 + 泵抛物拱呼吸
        breath = 0.5 + 0.5 * math.sin(ph * TWO)
        pw = _clamp01(1.0 - u * u) * breath
        vw = 1.0 if au < breath else 0.0
        return pw, vw
    if kind == "salvo":         # 礼炮齐射：全阀节拍齐开 + 泵爆涨急落
        lp = ph % 1.0
        pw = _smooth(min(1.0, lp / 0.3)) if lp < 0.3 else max(0.0, 1.0 - (lp - 0.3) / 0.3)
        vw = 1.0 if lp < 0.55 else 0.0
        return pw, vw
    if kind == "crown":         # 皇冠加冕：环列阀齐开 + 泵齐涌微相位
        c = 0.5 - 0.5 * math.cos((ph + pos * freq) * TWO)
        return c, (1.0 if c > 0.3 else 0.0)
    if kind == "wavewall":      # 起伏水墙：泵行进高度 + 波谷自动断喷
        h = 0.5 - 0.5 * math.cos((pos * freq - ph) * TWO)
        return h, (1.0 if h > gate_thr else 0.0)
    if kind == "geyser":        # 间歇泉：长蓄→喷发→冷却 双通道联动
        cyc = (ph * 0.5 + pos * 0.1) % 1.0
        if cyc < 0.6:
            return 0.1 + 0.1 * cyc / 0.6, (1.0 if cyc > 0.5 else 0.0)
        if cyc < 0.75:
            return 1.0, 1.0
        return (1.0 - (cyc - 0.75) / 0.25) * 0.4, 0.0
    # 默认：泵涌 + 全开
    h = 0.5 - 0.5 * math.cos(ph * TWO)
    return h, (1.0 if h > gate_thr else 0.0)


def _watercombo_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        plo = int(params.get("lo", 0)); phi = int(params.get("hi", 255))
        direction = params.get("direction", "正向")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = _apply_dir(seq_i / max(1, n - 1), direction)
            u = pos * 2.0 - 1.0
            pw, vw = _watercombo_sample(kind, pos, u, seq_i, n, t, params)
            ef.values[orig_i]["pump"] = int(plo + _clamp01(pw) * (phi - plo))
            ef.values[orig_i]["valve"] = int(_clamp01(vw) * 255)
    return fn


KERNELS["watercombo"] = _watercombo_kernel


# ── 水景编程秀：水形当关键帧 smoothstep 串接（formseq 风格）──
_PUMP_KINDS = set(["wavewall", "arch_static", "arch_move", "crown", "willow",
                   "peacock_open", "peacock_close", "peaks", "swell", "tide",
                   "runjet", "runjet_multi", "cascade", "opposing", "spiral",
                   "bloom", "stair_water", "supershoot_salvo", "supershoot_seq",
                   "pulse_surge", "geyser"])
_VALVE_KINDS = set(["chop", "chop_fast", "dblclick", "breach", "pulsetrain",
                    "flow_breath", "euclid", "probgate", "runner", "wavewindow",
                    "opposing", "banks", "relay", "zipper", "stair_banks",
                    "headon", "sparkle"])
_COMBO_KINDS = set(["peacock", "runner", "cascade", "arch", "salvo", "crown",
                    "wavewall", "geyser"])


def _water_sample(kind, pos, u, seq_i, n, t, p):
    """统一水形采样 → (pump_w, valve_w)；某通道不驱动则为 None。"""
    if kind in _COMBO_KINDS:
        return _watercombo_sample(kind, pos, u, seq_i, n, t, p)
    if kind in _VALVE_KINDS:
        return None, _valve_open(kind, pos, u, seq_i, n, t, p)
    return _pump_height(kind, pos, u, seq_i, n, t, p), None


_WATERSHOW_LIB = {
    "overture":   ["swell", "wavewall", "crown", "peacock_open"],          # 喷泉序曲
    "runner_show": ["runner", "wavewindow", "banks", "runjet"],            # 跑泉秀
    "cascade_show": ["cascade", "opposing", "relay", "cascade"],           # 此起彼伏
    "ode_water":  ["salvo", "crown", "peacock_open", "willow"],            # 水之礼赞
    "tide_song":  ["tide", "swell", "wavewall", "bloom"],                  # 潮汐之歌
    "peacock_show": ["peacock_open", "arch_static", "crown", "peacock_close"],  # 雀屏开合
    "geyser_show": ["geyser", "swell", "runjet", "tide"],                  # 间歇喷涌
    "music_water": ["euclid", "chop", "runner", "banks"],                  # 音乐喷泉
    "walldance":  ["wavewall", "bloom", "spiral", "arch_static"],          # 水墙幻舞
    "finale_water": ["supershoot_salvo", "salvo", "crown", "swell"],       # 终曲水炮
    "dawn_water": ["swell", "arch_move", "bloom", "peacock_open"],         # 破晓之泉
    "storm_water": ["chop_fast", "breach", "wavewindow", "headon"],        # 暴雨水阵
    "dragon_water": ["spiral", "runjet_multi", "wavewall", "cascade"],     # 蛟龙出海
    "lotus_water": ["bloom", "crown", "willow", "peacock_open"],           # 莲华绽放
    "march_water": ["banks", "relay", "stair_banks", "cascade"],           # 列阵喷涌
    "celebr_water": ["salvo", "supershoot_salvo", "crown", "peacock_open"],# 庆典水炮
}


def _watershow_kernel(kind):
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.25))
        hold = float(params.get("hold", 0.6))
        seq = params.get("_water_seq") or _WATERSHOW_LIB.get(kind, ["swell"])
        m = len(seq)
        n = max(1, len(sorted_fx))
        plo = int(params.get("lo", 0)); phi = int(params.get("hi", 255))
        gphase = (t * speed) % m
        idx = int(gphase); frac = gphase - idx
        if frac < hold:
            blend = 0.0
        else:
            tt = (frac - hold) / max(1e-4, 1.0 - hold)
            blend = tt * tt * (3.0 - 2.0 * tt)
        k0 = seq[idx % m]; k1 = seq[(idx + 1) % m]
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            u = pos * 2.0 - 1.0
            p0, v0 = _water_sample(k0, pos, u, seq_i, n, t, params)
            p1, v1 = _water_sample(k1, pos, u, seq_i, n, t, params)
            pw = (p0 or 0.0) + ((p1 or 0.0) - (p0 or 0.0)) * blend
            vw = (v0 or 0.0) + ((v1 or 0.0) - (v0 or 0.0)) * blend
            ef.values[orig_i]["pump"] = int(plo + _clamp01(pw) * (phi - plo))
            ef.values[orig_i]["valve"] = int(_clamp01(vw) * 255)
    return fn


KERNELS["watershow"] = _watershow_kernel


# ───────────────────────── 音频反应内核 ─────────────────────────
# ───────────────────────── 音频反应内核 ─────────────────────────
# 按设备类型拆分为5个独立内核，每个只输出对应通道类型。
# 所有内核通过 params["_audio_data"] 获取实时音频数据（RMS/频段/节拍/质心等）。

def _audio_light_kernel(kind: str):
    """音频→单通道亮度效果（dimmer/任何ch_type）。
    rate 参数实际控制时间速率（t_eff = t * rate），而非仅作幅度乘数。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        audio = params.get("_audio_data") or {}
        has_audio = bool(audio.get("has_audio")) if audio else False

        if not has_audio:
            for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = lo
            return

        n = max(1, len(sorted_fx))
        a_rms = float(audio.get("rms", 0.0))
        a_low = float(audio.get("low", 0.0))
        a_mid = float(audio.get("mid", 0.0))
        a_high = float(audio.get("high", 0.0))
        a_is_beat = bool(audio.get("is_beat", False))
        a_onset = float(audio.get("onset_strength", 0.0))
        a_bpm = float(audio.get("bpm", 120.0))
        a_beat_phase = float(audio.get("beat_phase", 0.0))
        beat_period = 60.0 / max(1.0, a_bpm)

        rate = float(params.get("rate", 1.0))
        t_eff = t * rate  # ★ rate 实际控制时序，非幅度
        decay = float(params.get("decay", 0.3))
        sensitivity = float(params.get("sensitivity", 0.3))
        spread = float(params.get("spread", 0.0))

        for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            w = 0.0

            if kind == "beat_sync":
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                w = math.exp(-bp / max(0.01, decay))
                if a_is_beat:
                    w = max(w, 0.9)

            elif kind == "beat_chase":
                beat_count = int(t_eff / beat_period) if beat_period > 0 else 0
                active = beat_count % n
                dist = abs(seq_i - active)
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                w = math.exp(-dist / 2.0) * math.exp(-bp / max(0.01, decay))

            elif kind == "beat_euclid":
                pulses = max(1, int(params.get("pulses", 5)))
                steps = max(2, int(params.get("steps", 16)))
                pat = _euclid(pulses, steps)
                step_idx = int(t_eff / (beat_period / 2)) % len(pat) if beat_period > 0 else 0
                on = pat[(step_idx + seq_i) % len(pat)]
                step_phase = ((t_eff / (beat_period / 2)) % 1.0) if beat_period > 0 else 0.0
                w = math.exp(-step_phase / max(0.01, decay)) if on else 0.0

            elif kind == "energy_follow":
                w = a_rms * rate
                if a_onset > sensitivity:
                    w = max(w, a_onset)

            elif kind == "energy_pump":
                pump_decay = float(params.get("pump_decay", 0.15))
                if a_low > sensitivity:
                    w = 1.0
                else:
                    bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                    w = math.exp(-bp / max(0.01, pump_decay)) * 0.4

            elif kind == "energy_range":
                floor = float(params.get("floor", 0.2))
                w = max(floor, a_rms) * rate

            elif kind == "beat_burst":
                # 节拍触发全高喷射 + 快速衰减
                if a_is_beat:
                    w = 1.0
                else:
                    bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                    w = math.exp(-bp / max(0.01, decay)) * 0.5

            elif kind == "energy_geyser":
                # RMS 超过 sensitivity 时喷发，否则缓慢回落
                threshold = sensitivity * 1.2
                if a_rms > threshold:
                    w = 1.0
                else:
                    w = a_rms * (1.0 / max(0.01, threshold))

            elif kind == "beat_pulsetrain":
                pulses = max(1, int(params.get("pulses", 5)))
                steps = max(2, int(params.get("steps", 16)))
                pat = _euclid(pulses, steps)
                step_idx = int(t_eff / (beat_period / 2)) % len(pat) if beat_period > 0 else 0
                on = pat[(step_idx + seq_i) % len(pat)]
                step_phase = ((t_eff / (beat_period / 2)) % 1.0) if beat_period > 0 else 0.0
                w = (1.0 - step_phase) if on else 0.0

            elif kind == "beat_heartbeat":
                # 强弱双拍：强拍(100%) + 弱拍(40%)，各自衰减
                bar_phase = (t_eff / (beat_period * 4)) % 1.0 if beat_period > 0 else 0.0
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                if bar_phase < 0.125:
                    w = math.exp(-bp / max(0.01, decay * 0.5))
                elif 0.25 <= bar_phase < 0.375:
                    w = 0.4 * math.exp(-bp / max(0.01, decay))
                else:
                    w = 0.0

            elif kind == "beat_groove":
                # Swing 节奏偏移：每对拍中第二拍延迟
                beat_in_pair = int(t_eff / beat_period) % 2 if beat_period > 0 else 0
                swing = 0.25  # 第二拍延迟偏移量
                phase = ((t_eff / beat_period + (swing if beat_in_pair == 1 else 0.0)) % 1.0
                        if beat_period > 0 else 0.0)
                w = math.exp(-phase / max(0.01, decay))

            elif kind == "structure_energy":
                analysis = params.get("_audio_analysis") or {}
                segments = analysis.get("segments", []) if isinstance(analysis, dict) else []
                current_seg = None
                for seg in segments:
                    if float(seg.get("t_start", 0)) <= t_eff <= float(seg.get("t_end", float("inf"))):
                        current_seg = seg
                        break
                w = float(current_seg.get("energy_rank", 0.5)) if current_seg else a_rms

            elif kind == "structure_build_drop":
                analysis = params.get("_audio_analysis") or {}
                segments = analysis.get("segments", []) if isinstance(analysis, dict) else []
                current_seg = None
                for seg in segments:
                    if float(seg.get("t_start", 0)) <= t_eff <= float(seg.get("t_end", float("inf"))):
                        current_seg = seg
                        break
                if current_seg:
                    if current_seg.get("is_drop"):
                        w = 1.0
                    elif current_seg.get("is_build"):
                        seg_dur = float(current_seg.get("t_end", t_eff + 1)) - float(
                            current_seg.get("t_start", t_eff))
                        progress = (t_eff - float(current_seg.get("t_start", t_eff))) / max(0.01, seg_dur)
                        w = 0.2 + 0.8 * progress
                    else:
                        w = float(current_seg.get("energy_rank", 0.5))
                else:
                    w = a_rms

            elif kind == "structure_onset_chase":
                if a_onset > sensitivity * 1.5:
                    step = int(a_onset * n)
                    active = (step + int(t_eff / beat_period)) % n if beat_period > 0 else 0
                    w = 1.0 if seq_i == active else 0.0
                else:
                    w = 0.0

            elif kind == "struct_music_water":
                # 音乐段落能量 → 喷泉高度
                analysis = params.get("_audio_analysis") or {}
                segments = analysis.get("segments", []) if isinstance(analysis, dict) else []
                current_seg = None
                for seg in segments:
                    if float(seg.get("t_start", 0)) <= t_eff <= float(seg.get("t_end", float("inf"))):
                        current_seg = seg
                        break
                seg_energy = float(current_seg.get("energy_rank", 0.5)) if current_seg else a_rms
                w = seg_energy * (0.5 + 0.5 * a_rms)

            elif kind == "audio_strobe":
                # 节拍触发瞬间全亮 + 极快衰减，仿频闪灯
                if a_is_beat:
                    w = 1.0
                else:
                    bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                    w = math.exp(-bp / max(0.01, decay * 0.3)) * 0.3

            elif kind == "audio_chase_energy":
                # 能量驱动跑灯：RMS 高 → 快跑，RMS 低 → 慢跑
                step_speed = 0.5 + a_rms * 3.0
                step_idx = int(t_eff * step_speed) % n
                dist = abs(seq_i - step_idx)
                w = math.exp(-dist / 1.5) * _clamp01(a_rms * rate)

            elif kind == "audio_onset_spark":
                # 强起音触发瞬间全亮 + 快速衰减
                if a_onset > sensitivity:
                    w = a_onset
                else:
                    w = math.exp(-a_beat_phase / max(0.01, decay * 0.3)) * a_rms * 0.3

            elif kind == "audio_wave_sine":
                # RMS 驱动正弦波亮度，强弱音振幅变化
                ph = t_eff * a_bpm / 60.0 * 2 * math.pi
                amp_mod = 0.3 + 0.7 * a_rms
                w = 0.5 + 0.5 * math.sin(ph + pos * spread * 2 * math.pi) * amp_mod

            elif kind == "audio_wave_triangle":
                # BPM 驱动三角波，设备间相位展开
                tri_phase = ((t_eff * a_bpm / 60.0 + pos * spread) % 1.0)
                w = 2.0 * abs(tri_phase - 0.5)

            elif kind == "audio_fade_breath":
                # RMS 驱动呼吸渐变
                breath_period = 2.0 / max(0.1, a_rms * rate)
                bp = (t_eff % breath_period) / breath_period
                w = 0.5 - 0.5 * math.cos(bp * 2 * math.pi)

            elif kind == "audio_band_scroll":
                # 低/中/高频段能量驱动三段亮度滚动
                band_scroll = (t_eff * 0.5 + pos * spread) % 1.0
                band = min(2, int(band_scroll * 3))
                w = [a_low, a_mid, a_high][band] * rate

            else:
                w = a_rms

            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))

    return fn


def _audio_color_kernel(kind: str):
    """音频→RGB 颜色效果（仿 _color_kernel，直写 R/G/B）。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        audio = params.get("_audio_data") or {}
        has_audio = bool(audio.get("has_audio")) if audio else False
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))

        if not has_audio:
            for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[orig_i]["R"] = lo
                ef.values[orig_i]["G"] = lo
                ef.values[orig_i]["B"] = lo
            return

        n = max(1, len(sorted_fx))
        a_rms = float(audio.get("rms", 0.0))
        a_low = float(audio.get("low", 0.0))
        a_mid = float(audio.get("mid", 0.0))
        a_high = float(audio.get("high", 0.0))
        a_centroid = float(audio.get("centroid", 0.5))
        a_is_beat = bool(audio.get("is_beat", False))
        a_bpm = float(audio.get("bpm", 120.0))
        a_onset = float(audio.get("onset_strength", 0.0))

        rate = float(params.get("rate", 1.0))
        t_eff = t * rate
        sat = float(params.get("saturation", 1.0))
        bright = float(params.get("brightness", 1.0))
        spread = float(params.get("spread", 0.0))
        beat_period = 60.0 / max(1.0, a_bpm)

        for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)

            if kind == "spectrum_rainbow":
                h = (a_centroid + pos * spread) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * _clamp01(0.3 + a_rms))

            elif kind == "spectrum_bands":
                # 低频→红，中频→绿，高频→蓝，按能量混合
                r = int(lo + a_low * (hi - lo))
                g = int(lo + a_mid * (hi - lo))
                b = int(lo + a_high * (hi - lo))

            elif kind == "spectrum_warmcool":
                # 低 centroid→暖色，高 centroid→冷色 (2000K-8000K)
                k = 2000 + (1.0 - a_centroid) * 6000
                r, g, b = _kelvin_rgb(k)
                v = _clamp01(0.3 + a_rms)
                r, g, b = int(r * v * bright), int(g * v * bright), int(b * v * bright)

            elif kind == "spectrum_hsv_cycle":
                # centroid 驱动色相环连续旋转
                h = (a_centroid * 2.0 + t_eff * 0.1 + pos * spread) % 1.0
                r, g, b = _hsv_to_rgb(h, sat, bright * _clamp01(0.3 + a_rms))

            elif kind == "beat_color_pop":
                # 每拍随机色相，拍内渐暗
                beat_idx = int(t_eff / beat_period) if beat_period > 0 else 0
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                h_seed = _vnoise(beat_idx, 42)
                h = (h_seed + pos * spread) % 1.0
                fade = math.exp(-bp / 0.25)
                r, g, b = _hsv_to_rgb(h, sat, bright * fade)

            elif kind == "audio_bicolor_beat":
                # 节拍在双色之间交替切换
                beat_idx = int(t_eff / beat_period) if beat_period > 0 else 0
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                h = 0.0 if beat_idx % 2 == 0 else 0.55  # 红/蓝交替
                fade = math.exp(-bp / 0.25)
                r, g, b = _hsv_to_rgb(h, sat, bright * fade)

            elif kind == "audio_centroid_fire":
                # 低质心→火焰暖色，高质心→冰蓝冷色
                k = 1800 + (1.0 - a_centroid) * 7000
                r, g, b = _kelvin_rgb(k)
                v = _clamp01(0.3 + a_rms + a_low * 0.3)
                r, g, b = int(r * v * bright), int(g * v * bright), int(b * v * bright)

            else:
                r = g = b = int(lo + a_rms * (hi - lo))

            ef.values[orig_i]["R"] = max(0, min(255, r))
            ef.values[orig_i]["G"] = max(0, min(255, g))
            ef.values[orig_i]["B"] = max(0, min(255, b))

    return fn


def _audio_motor_kernel(kind: str):
    """音频→电机轨迹效果（motor_x/motor_y，仿 _motor_kernel）。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        audio = params.get("_audio_data") or {}
        has_audio = bool(audio.get("has_audio")) if audio else False
        center = int(params.get("center", 128))
        lo = int(params.get("lo", 0))

        if not has_audio:
            for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[orig_i]["motor_x"] = center
                ef.values[orig_i]["motor_y"] = center
            return

        n = max(1, len(sorted_fx))
        a_rms = float(audio.get("rms", 0.0))
        a_bpm = float(audio.get("bpm", 120.0))
        a_is_beat = bool(audio.get("is_beat", False))
        a_beat_phase = float(audio.get("beat_phase", 0.0))
        a_low = float(audio.get("low", 0.0))

        rate = float(params.get("rate", 1.0))
        t_eff = t * rate
        amp = float(params.get("amplitude", 1.0))
        spread = float(params.get("spread", 0.0))
        beat_period = 60.0 / max(1.0, a_bpm)
        rad = 127.0 * max(0.0, min(1.0, amp))

        for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            ph = t_eff * a_bpm / 60.0 * 2 * math.pi + pos * spread * 2 * math.pi

            if kind == "motor_audio_circle":
                # 圆圈半径跟随 RMS，速度跟随 BPM
                r_factor = 0.3 + 0.7 * a_rms
                x = r_factor * math.cos(ph)
                y = r_factor * math.sin(ph)

            elif kind == "motor_audio_spiral":
                # 螺旋：RMS 驱动半径，BPM 驱动角速度
                rr = (t_eff * a_rms * 0.5) % 1.0
                x = rr * math.cos(ph * 3)
                y = rr * math.sin(ph * 3)

            elif kind == "motor_audio_shake":
                # 强拍触发随机抖动位置
                if a_is_beat:
                    shake_x = (_vnoise(int(t_eff * 10), seq_i) - 0.5) * 2.0 * a_rms
                    shake_y = (_vnoise(int(t_eff * 10) + 99, seq_i) - 0.5) * 2.0 * a_rms
                else:
                    decay_factor = math.exp(-a_beat_phase / 0.15)
                    shake_x = (_vnoise(int(t_eff * 10), seq_i) - 0.5) * 2.0 * a_rms * decay_factor
                    shake_y = (_vnoise(int(t_eff * 10) + 99, seq_i) - 0.5) * 2.0 * a_rms * decay_factor
                x = math.cos(ph) * 0.3 + shake_x
                y = math.sin(ph) * 0.3 + shake_y

            elif kind == "motor_audio_sweep":
                # RMS 驱动水平扫摆幅度，BPM 驱动频率
                sweep_amp = 0.3 + 0.7 * a_rms
                x = math.sin(ph) * sweep_amp
                y = math.cos(ph * 0.5) * 0.2

            else:
                x = math.cos(ph)
                y = math.sin(ph)

            ef.values[orig_i]["motor_x"] = int(max(0, min(255, center + x * rad)))
            ef.values[orig_i]["motor_y"] = int(max(0, min(255, center + y * rad)))

    return fn


def _audio_valve_kernel(kind: str):
    """音频→阀门序列效果（valve，0/255 开关 + 衰减）。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        audio = params.get("_audio_data") or {}
        has_audio = bool(audio.get("has_audio")) if audio else False

        if not has_audio:
            for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = lo
            return

        n = max(1, len(sorted_fx))
        a_is_beat = bool(audio.get("is_beat", False))
        a_bpm = float(audio.get("bpm", 120.0))
        a_beat_phase = float(audio.get("beat_phase", 0.0))
        a_rms = float(audio.get("rms", 0.0))
        beat_period = 60.0 / max(1.0, a_bpm)

        rate = float(params.get("rate", 1.0))
        t_eff = t * rate
        decay = float(params.get("decay", 0.3))

        for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            if kind == "valve_beat_gate":
                # 节拍时开，快速衰减关
                if a_is_beat:
                    w = 1.0
                else:
                    bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                    w = math.exp(-bp / max(0.01, decay))

            elif kind == "valve_beat_chase":
                # 每拍跳到下一个阀门
                beat_count = int(t_eff / beat_period) if beat_period > 0 else 0
                active = beat_count % n
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                w = math.exp(-bp / max(0.01, decay)) if seq_i == active else 0.0

            elif kind == "valve_onset_burst":
                # 强起音触发全组阀门齐开 + 衰减
                a_onset = float(audio.get("onset_strength", 0.0))
                if a_onset > 0.3:
                    w = 1.0
                else:
                    bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                    w = math.exp(-bp / max(0.01, decay)) * 0.5

            else:
                w = a_rms

            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))

    return fn


def _audio_pump_kernel(kind: str):
    """音频→泵/变频效果（pump，连续值）。"""
    def fn(ef, t, sorted_fx, fdefs, params, fps):
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        audio = params.get("_audio_data") or {}
        has_audio = bool(audio.get("has_audio")) if audio else False

        if not has_audio:
            for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[orig_i][ch] = lo
            return

        n = max(1, len(sorted_fx))
        a_rms = float(audio.get("rms", 0.0))
        a_low = float(audio.get("low", 0.0))
        a_mid = float(audio.get("mid", 0.0))
        a_high = float(audio.get("high", 0.0))
        a_is_beat = bool(audio.get("is_beat", False))
        a_onset = float(audio.get("onset_strength", 0.0))
        a_bpm = float(audio.get("bpm", 120.0))
        beat_period = 60.0 / max(1.0, a_bpm)

        rate = float(params.get("rate", 1.0))
        t_eff = t * rate
        sensitivity = float(params.get("sensitivity", 0.3))
        spread = float(params.get("spread", 0.0))

        for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            w = 0.0

            if kind == "pump_energy_surge":
                # RMS 驱动泵基值，onset 触发尖峰 + 递进偏移
                base = a_rms * 0.7
                onset_boost = a_onset * 0.3 if a_onset > sensitivity else 0.0
                w = min(1.0, base + onset_boost + pos * spread * a_rms)

            elif kind == "pump_band_react":
                # 低频→设备0段，中频→中段，高频→末段
                band = min(2, int(pos * 3))
                w = [a_low, a_mid, a_high][band]

            elif kind == "pump_beat_wave":
                # 节拍触发波浪沿设备序列传播
                bp = (t_eff % beat_period) / beat_period if beat_period > 0 else 0.0
                dist = abs(pos - bp)
                w = math.exp(-dist / 0.15) if a_is_beat else a_rms * 0.3

            elif kind == "pump_audio_gradient":
                # RMS 驱动设备高度渐变，pos 越远偏移越大
                base = a_rms * 0.7 * rate
                gradient = pos * spread * a_rms
                w = min(1.0, base + gradient)

            else:
                w = a_rms

            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))

    return fn


KERNELS["audio"] = _audio_light_kernel        # 保持旧 "audio" key 兼容
KERNELS["audio_light"] = _audio_light_kernel
KERNELS["audio_color"] = _audio_color_kernel
KERNELS["audio_motor"] = _audio_motor_kernel
KERNELS["audio_valve"] = _audio_valve_kernel
KERNELS["audio_pump"] = _audio_pump_kernel


# ───────────────────────── 图标名 → emoji 字形映射 ─────────────────────────
# 效果浏览器把 preset.icon 当作文本(emoji 字体)直接渲染，因此用 emoji/unicode 字形，
# 而非 Tabler 名。下表把数据表里用的语义名映射成与现有库一致风格的字形。
_ICON_GLYPH = {
    "activity": "≋", "adjustments": "🎛", "adjustments-bolt": "⚡", "arrow-big-up": "⬆",
    "arrow-bounce": "⤴", "arrow-curve-right": "↪", "arrow-left-bar": "⇤", "arrow-ramp-right": "📈",
    "arrow-right-bar": "⇥", "arrow-up-bar": "⤒", "arrows-cross": "✕", "arrows-right": "⇉",
    "arrows-shuffle": "🔀", "arrows-up": "⇈", "atom": "⚛", "binary": "▦", "bolt": "⚡",
    "bulb": "💡", "chart-histogram": "📊", "circle-dot": "◉", "color-swatch": "🎨",
    "comet": "☄", "crown": "👑", "dice": "🎲", "droplet": "💧", "eraser": "▭",
    "flame": "🔥", "flower": "✿", "fold": "⋀", "grid-pattern": "▦", "grip-horizontal": "⣿",
    "heartbeat": "💓", "infinity": "∞", "layers-difference": "▤", "line-height": "↕",
    "lungs": "🫁", "magnet": "🧲", "math-symbols": "∑", "metronome": "🎼", "oval-vertical": "⬭",
    "palette": "🎨", "plant-2": "🌿", "point": "•", "polygon": "⬡", "rainbow": "🌈",
    "rectangular-prism": "▥", "ripple": "🌊", "rotate-clockwise": "🌀", "route": "🐜",
    "scan": "⫿", "sparkles": "✨", "spiral": "🌀", "square": "◻", "square-rotated": "◆",
    "stairs": "📶", "stairs-down": "📉", "stairs-up": "📶", "star": "★", "stars": "✦",
    "sunrise": "🌅", "temperature": "🌡", "trending-down": "📉", "trending-up": "📈",
    "triangle": "△", "users-group": "👥", "vector-spline": "∿", "wall": "▤",
    "wave-saw-tool": "⩘", "wave-sine": "〜", "wave-square": "⊓", "wind": "🌬",
    "windmill": "🌀",
    # ── 本次扩充：编组 / 序列 / 通用整体 / 高级 新图标 ──
    "bird": "🕊", "butterfly": "🦋", "dragonfly": "🪰", "peacock": "🦚",
    "bridge": "🌉", "wheat": "🌾", "heart": "❤", "crown2": "👑",
    "fan": "🪭", "fan-fold": "🪭", "spiral2": "🌀", "pendulum": "⊙",
    "seesaw": "⚖", "mirror": "🪞", "funnel": "▽", "zipper": "🤐",
    "dna": "🧬", "ripple2": "🌊", "dragon": "🐉", "horn": "📯",
    "mountain": "⛰", "checker": "▦", "fish": "🐟", "satellite": "🛰",
    "weave": "🪢", "domino": "🁢", "scissors": "✂", "cascade": "🌊",
    "school": "🐠", "compass": "🧭", "galaxy": "🌌", "expand": "⤢",
    "bow": "🏹", "flutter": "🦋", "swing": "🎏", "tornado": "🌪",
    "sequence": "🎬", "program": "📽", "film": "🎞", "stage": "🎭",
    "wings": "🪽", "phoenix": "🔥", "carnival": "🎪", "meditation": "🧘",
    "victory": "🏆", "kaleidoscope": "🔮", "march": "🚶", "celestial": "✦",
    "pendwave": "⊙", "lorenz": "🦋", "soliton": "〰", "wavepacket": "📦",
    "metaball": "🔵", "diffusion": "🌫", "warp": "🌀", "doppler": "📡",
    "fractal": "❄", "ant": "🐜", "vortex2": "🌀", "lightning": "⚡",
    "string": "🎻", "ecg": "💓", "contour": "🗺", "popcorn": "🍿",
    "glitch": "📺", "pluck": "🎸", "swell": "📈", "strike": "🥁",
    "marquee": "🎰", "scanner": "🔦", "stack": "🥞", "collide": "💥",
    "multihead": "⫶", "levelbar": "📊", "overlap": "≈", "relay": "🏃",
    "cover": "▦", "rotate2": "🔄", "peak": "⛰", "alternate": "⇄",
    # ── 泵阀水景重写 ──
    "water-wall": "🌊", "arch": "⛩", "geyser": "♨", "peacock-water": "🦚",
    "willow-water": "🌿", "crown-water": "👑", "runjet": "💨", "swell": "🌊",
    "tide-water": "🌊", "spiral-water": "🌀", "bloom-water": "🌸", "stair-water": "📶",
    "watercannon": "🎆", "pulse-water": "💓", "peaks-water": "⛰",
    "valve-chop": "⏸", "valve-burst": "⚡", "valve-dbl": "‼", "breach": "🌊",
    "valve-train": "▮", "flow-breath": "🫁", "euclid-water": "◉", "dice-water": "🎲",
    "valve-run": "⇥", "wavewindow": "〰", "cross-water": "✕", "banks": "▦",
    "relay-water": "🏃", "zipper-water": "🤐", "stair-banks": "🥞", "headon": "💥",
    "sparkle-water": "✨", "water-show": "🎬", "water-music": "🎼", "water-trophy": "🏆",
    "water-combo": "⛲", "fountain": "⛲",
    # ── 本次扩充：电机水型 10 字形（2026-08-12）──
    "snowflake": "❄", "yin-yang": "☯", "bell": "🔔", "feather": "🪶",
    "meteor": "☄", "moon": "🌙", "hexagon": "⬡", "music": "🎵",
    "flare": "✨", "droplets": "💧", "ripple-up": "🌊",
}


def icon_glyph(name: str) -> str:
    """把图标语义名转成浏览器可直接渲染的 emoji/unicode 字形。"""
    return _ICON_GLYPH.get(name, name if any(ord(c) > 0x2000 for c in (name or "")) else "✨")





# ───────────────────────── 参数构建器 ─────────────────────────
def _P(*specs) -> List[ParamSpec]:
    return list(specs)


# ── 音频效果参数（按内核类型拆分）────────────────────────────────────

def _audio_light_params(rate=1.0, decay=0.3, sensitivity=0.3, spread=0.0,
                        lo=0, hi=255, ch="dimmer"):
    """音频→单通道亮度效果通用参数。rate 控制实际时序速率。"""
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("rate", "速率", "float", rate, 0.05, 8.0, 0.05,
            tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
        ParamSpec("decay", "衰减", "float", decay, 0.02, 1.0, 0.01,
            tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ParamSpec("sensitivity", "灵敏度", "float", sensitivity, 0.05, 1.0, 0.05,
            tooltip="控制音频输入的灵敏度。值越低需要越强的音频信号才能触发反应"),
        ParamSpec("spread", "相位展开", "float", spread, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _audio_color_params(rate=0.3, sat=1.0, bright=1.0, spread=0.0, lo=0, hi=255):
    """音频→RGB 颜色效果参数。"""
    return [
        ParamSpec("rate", "速率", "float", rate, 0.05, 8.0, 0.05,
            tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
        ParamSpec("saturation", "饱和度", "float", sat, 0.0, 1.0, 0.02,
            tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
        ParamSpec("brightness", "亮度", "float", bright, 0.0, 1.0, 0.02,
            tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
        ParamSpec("spread", "相位展开", "float", spread, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _audio_motor_params(rate=0.5, amp=1.0, spread=0.0, center=128):
    """音频→电机轨迹效果参数。"""
    return [
        ParamSpec("rate", "速率", "float", rate, 0.05, 8.0, 0.05,
            tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
        ParamSpec("amplitude", "幅度", "float", amp, 0.0, 1.0, 0.02,
            tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ParamSpec("spread", "相位展开", "float", spread, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("center", "中心", "int", center, 0, 255, 1,
            tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
    ]


def _audio_valve_params(rate=1.0, decay=0.3, lo=0, hi=255, ch="valve"):
    """音频→阀门序列效果参数。"""
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=["valve", "pump"],
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("rate", "速率", "float", rate, 0.05, 8.0, 0.05,
            tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
        ParamSpec("decay", "衰减", "float", decay, 0.02, 1.0, 0.01,
            tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _audio_pump_params(rate=1.0, sensitivity=0.3, spread=0.0, lo=0, hi=255, ch="pump"):
    """音频→泵效果参数。"""
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=["pump", "valve"],
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("rate", "速率", "float", rate, 0.05, 8.0, 0.05,
            tooltip="控制效果的变化速率。值越高每秒变化次数越多"),
        ParamSpec("sensitivity", "灵敏度", "float", sensitivity, 0.05, 1.0, 0.05,
            tooltip="控制音频输入的灵敏度。值越低需要越强的音频信号才能触发反应"),
        ParamSpec("spread", "相位展开", "float", spread, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _scalar_params(speed=1.0, freq=1.0, duty=0.5, lo=0, hi=255, ease=False, ch="dimmer"):
    ps = [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("speed", "速度", "float", speed, 0.0, 16.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 16.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]
    if ease:
        ps.append(ParamSpec("ease", "缓动", "choice", "linear",
                            choices=["linear", "smooth", "in", "out"],
            tooltip="选择缓动函数类型。控制变化速度的加速/减速节奏"))
    return ps


def _color_params(speed=0.3, freq=1.0, sat=1.0, bright=1.0):
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("saturation", "饱和度", "float", sat, 0.0, 1.0, 0.02,
            tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
        ParamSpec("brightness", "亮度", "float", bright, 0.0, 1.0, 0.02,
            tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
        ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
    ]


def _motor_params(speed=0.5, amp=1.0, fx=1.0, fy=1.0):
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("amplitude", "幅度", "float", amp, 0.0, 1.0, 0.02,
            tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ParamSpec("freq_x", "X频率", "float", fx, 1.0, 8.0, 1.0,
            tooltip="控制 X 轴方向的波动频率。值越高水平方向波动越密集"),
        ParamSpec("freq_y", "Y频率", "float", fy, 1.0, 8.0, 1.0,
            tooltip="控制 Y 轴方向的波动频率。值越高垂直方向波动越密集"),
        ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("center", "中心", "int", 128, 0, 255, 1,
            tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
    ]


def _fountain_params(speed=1.0, width=0.2, lo=0, hi=255, ch="pump"):
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=["pump", "valve"],
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("width", "宽度", "float", width, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("attack", "起音", "float", 0.1, 0.0, 1.0, 0.01,
            tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
        ParamSpec("decay", "衰减", "float", 0.4, 0.0, 1.0, 0.01,
            tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ParamSpec("lo", "低值", "int", lo, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", hi, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ParamSpec("direction", "方向", "choice", "正向",
                  choices=["正向", "反向", "中心向外", "两侧向中"],
            tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
    ]


def _formation_params(speed=0.4, angle=1.0, groups=1, axis="双轴XY"):
    """编组造型参数：丰富但不冗余。groups=扇/组数；axis=单轴/双轴。"""
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 6.0, 0.02,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("angle", "展开角度", "float", angle, 0.0, 1.0, 0.02,
            tooltip="控制角度位置或展开角度。值越大角度偏转越明显"),
        ParamSpec("groups", "组数", "int", groups, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("axis", "运动轴", "choice", axis, choices=["双轴XY", "单轴X", "单轴Y"],
            tooltip="选择参考轴方向。决定效果沿哪个空间维度展开"),
        ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("mirror", "镜像", "bool", False,
            tooltip="控制是否启用镜像对称。开启后效果在两侧对称复制"),
        ParamSpec("center", "中心", "int", 128, 0, 255, 1,
            tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
    ]


def _formseq_params(speed=0.25, angle=1.0, hold=0.6):
    """电机序列程序参数。"""
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 3.0, 0.01,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("hold", "停留占比", "float", hold, 0.0, 0.95, 0.01,
            tooltip="控制触发后效果的保持持续时间。值越大持续输出时间越长"),
        ParamSpec("angle", "展开角度", "float", angle, 0.0, 1.0, 0.02,
            tooltip="控制角度位置或展开角度。值越大角度偏转越明显"),
        ParamSpec("groups", "组数", "int", 3, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("axis", "运动轴", "choice", "双轴XY", choices=["双轴XY", "单轴X", "单轴Y"],
            tooltip="选择参考轴方向。决定效果沿哪个空间维度展开"),
        ParamSpec("center", "中心", "int", 128, 0, 255, 1,
            tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
    ]


def _arraycov_params(speed=1.0, groups=4, ch="dimmer"):
    """通用交替覆盖参数。"""
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("groups", "组数", "int", groups, 1, 16, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
            tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _seqprog_params(speed=0.3, hold=0.6, ch="dimmer"):
    """通用序列程序参数。"""
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("speed", "速度", "float", speed, 0.0, 3.0, 0.01,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("hold", "停留占比", "float", hold, 0.0, 0.95, 0.01,
            tooltip="控制触发后效果的保持持续时间。值越大持续输出时间越长"),
        ParamSpec("spatial_freq", "空间频率", "float", 1.0, 0.0, 8.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("width", "宽度", "float", 0.15, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("density", "密度", "float", 0.3, 0.0, 1.0, 0.02,
            tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
        ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
            tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
        ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _pumpform_params(speed=1.0, freq=1.0, width=0.18, groups=3, direction="正向", extra=None):
    """泵水形参数：丰富但不冗余（按 variant 需要追加 span/bend/heads/steps）。"""
    ps = [
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("width", "喷头宽度", "float", width, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("groups", "组数", "int", groups, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("direction", "方向", "choice", direction,
                  choices=["正向", "反向", "中心向外", "两侧向中"],
            tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]
    for spec in (extra or []):
        ps.insert(4, spec)
    return ps


def _valvegate_params(speed=1.0, duty=0.4, width=0.15, groups=3, density=0.4, direction="正向", extra=None):
    """阀门门控参数。"""
    ps = [
        ParamSpec("speed", "速度", "float", speed, 0.0, 12.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("duty", "占空", "float", duty, 0.02, 1.0, 0.01,
            tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
        ParamSpec("width", "门窗宽度", "float", width, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("groups", "组数", "int", groups, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("density", "密度/概率", "float", density, 0.0, 1.0, 0.02,
            tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
        ParamSpec("direction", "方向", "choice", direction,
                  choices=["正向", "反向", "中心向外", "两侧向中"],
            tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
            tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
        ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]
    for spec in (extra or []):
        ps.insert(5, spec)
    return ps


def _watercombo_params(speed=1.0, freq=1.0, groups=3, gate_thr=0.15, width=0.15, direction="正向"):
    """组合(pump+valve)参数。"""
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("groups", "组数", "int", groups, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("gate_thr", "断喷阈值", "float", gate_thr, 0.0, 1.0, 0.01,
            tooltip="控制音频门限阈值。低于此值的信号被静音忽略，防止噪声误触发"),
        ParamSpec("width", "宽度", "float", width, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("direction", "方向", "choice", direction,
                  choices=["正向", "反向", "中心向外", "两侧向中"],
            tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ParamSpec("lo", "泵低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "泵高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


def _watershow_params(speed=0.22, hold=0.6, freq=1.0, groups=3):
    """水景编程秀参数。"""
    return [
        ParamSpec("speed", "速度", "float", speed, 0.0, 2.0, 0.01,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("hold", "停留占比", "float", hold, 0.0, 0.95, 0.01,
            tooltip="控制触发后效果的保持持续时间。值越大持续输出时间越长"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("groups", "组数", "int", groups, 1, 12, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("width", "宽度", "float", 0.15, 0.01, 1.0, 0.01,
            tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ParamSpec("lo", "泵低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "泵高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


# 实际的家族数据表在 effect_presets_curated_data.py 中定义，避免单文件过大。
def _register_curated_presets(engine) -> None:
    from .effect_presets_curated_data import build_families
    families = build_families()
    count = 0
    for fam in families:
        kernel_factory = KERNELS[fam["kernel"]]
        compute = kernel_factory(fam["variant"])
        preset = EffectPreset(
            id=fam["id"],
            name=fam["name"],
            category=fam["category"],
            description=fam["desc"],
            target_channels=fam["channels"],
            icon=icon_glyph(fam["icon"]),
            tags=fam.get("tags", []),
            params=fam["params"],
        )
        engine._reg(preset, compute)
        count += 1
    return count


# ═══════════════════════════════════════════════════════════════════════════
#  R172 三族新效果（docs/clip_studio_r172_design.md §5.1-5.3，W2a）
#  · 伪随机增强族 randrun（通用/随机）：确定性触发链 + 跑动包
#  · 变速载体族 varyspeed（通用/变速）：时间重映射 t_eff = Φ(t)，载体复用既有波形函数
#  · 折返级联族 foldcascade（通用/级联）：落点跨波接力，O(1) 闭式
#  共同纪律：确定性可复现（_vnoise 整数哈希 + 步窗 tick + 模块级预计算缓存，
#  禁 np.random / 逐帧累积状态）；单通道通用（ch_type choice）。
# ═══════════════════════════════════════════════════════════════════════════

# ───────────────────────── 5.1 伪随机增强族 ─────────────────────────
_RANDRUN_CAP = 4096
_RANDRUN_SITES: dict = {}


def _randrun_chain(n, proximity, seed, kmax):
    """确定性触发链（带缓存、按需延长）：site_k ∈ [0,n) 设备单位。

    site_k = site_{k-1} + (hash_k-0.5)*2*span 环绕；span = max(1,(1-proximity)*n)：
    proximity=0 → 全域跳，1 → 紧邻游走。缓存键 (seed,n,proximity)，只增不改 → 任意跳读一致。
    """
    key = (seed, n, round(float(proximity), 4))
    ent = _RANDRUN_SITES.get(key)
    if ent is None:
        ent = {"sites": [_vnoise(101, seed) * n], "steps": [0.0]}
        _RANDRUN_SITES[key] = ent
    sites = ent["sites"]
    span = max(1.0, (1.0 - float(proximity)) * n)
    while len(sites) <= kmax:
        k = len(sites)
        step = (_vnoise(k * 7 + 3, seed) - 0.5) * 2.0 * span
        sites.append((sites[-1] + step) % n)
        ent["steps"].append(step)
    return ent


_RANDRUN_KINDS = {
    # kind: (每窗包数 tick, 跑动宽度 rs, 临近 prox, 相位差 gap, 衰减 decay, 方向 dmode, 特殊标志)
    "sparkwalk":   dict(rs=2, prox=0.85, gap=0.55, decay=0.5, dmode="跟随"),
    "hopall":      dict(rs=1, prox=0.0, gap=0.0, decay=0.3, dmode="随机"),
    "dualrace":    dict(rs=3, prox=0.5, gap=0.7, decay=0.45, dmode="交替", mirror=True),
    "tritrace":    dict(rs=2, prox=0.6, gap=0.8, decay=0.5, dmode="跟随", heads=3),
    "edgehop":     dict(rs=2, prox=0.45, gap=0.4, decay=0.35, dmode="随机", bias="edge"),
    "centerhop":   dict(rs=2, prox=0.5, gap=0.4, decay=0.4, dmode="随机", bias="center"),
    "shower":      dict(tick=3, rs=2, prox=0.7, gap=0.3, decay=0.3, dmode="随机"),
    "fireflyd":    dict(tick=2, rs=1, prox=0.8, gap=0.5, decay=0.7, dmode="随机", soft=True),
    "pulsepoint":  dict(rs=1, prox=0.6, gap=0.0, decay=0.18, dmode="随机", punch=True),
    "sidehop":     dict(rs=2, prox=0.5, gap=0.2, decay=0.35, dmode="交替"),
    "runjetrand":  dict(rs=5, prox=0.55, gap=0.75, decay=0.6, dmode="跟随"),
    "flamespark":  dict(rs=2, prox=0.65, gap=0.5, decay=0.45, dmode="随机", flick=True),
    "twinklerand": dict(rs=3, prox=0.75, gap=0.6, decay=0.6, dmode="跟随", soft=True),
    "fallstar":    dict(rs=3, prox=0.6, gap=0.6, decay=0.55, dmode="跟随", slide=True),
    "grandhop":    dict(rs=6, prox=0.4, gap=0.35, decay=0.5, dmode="随机"),
}


def _rr_pack_dir(dmode, k, step, seed):
    """跑动包首方向：跟随=按步进符号；交替=按触发序奇偶；其余=整数哈希噪声。"""
    if dmode == "跟随":
        return 1.0 if step >= 0 else -1.0
    if dmode == "交替":
        return 1.0 if k % 2 == 0 else -1.0
    return 1.0 if _vnoise(k * 3 + 1, seed) < 0.5 else -1.0


def _rr_bias_site(bias, site, k, n, seed):
    """跑动包起点偏向：edge=噪声二选一压向两端；center=压向中心；其余原样。"""
    if bias == "edge" and _vnoise(k * 11 + 5, seed + 31) < 0.6:
        return 0.0 if _vnoise(k * 5 + 9, seed + 17) < 0.5 else float(n - 1)
    if bias == "center":
        return (site + n * 0.5) * 0.5 + (_vnoise(k, seed + 7) - 0.5) * n * 0.15
    return site


def _rr_pkgs(st):
    """构建当前帧仍存活的跑动包列表（确定性，纯由触发链与整数哈希决定）。"""
    out = []
    n = st["n"]
    for back in range(st["lookback"]):
        k = st["k0"] - back
        if k < 0:
            continue
        idx = k % _RANDRUN_CAP
        site = st["sites"][idx]
        step = st["steps"][idx]
        tau0 = st["t"] * st["rate"] - k
        if tau0 > 1.0 + st["gap"] + st["dscale"] * 4.0:
            continue  # 包已熄灭（含包内错相点燃的最晚时刻）
        d1 = _rr_pack_dir(st["dmode"], k, step, st["seed"])
        site = _rr_bias_site(st["bias"], site, k, n, st["seed"])
        start = float(int(math.floor(site)) % n)   # 包起点吸附到设备格点，包内相位差确定
        if st["mirror"]:   # 双头对跑：镜像落点反向同发
            out.append(dict(k=k, start=n - 1.0 - start, dir=-d1, tau0=tau0))
        if st["heads"] > 1:  # 多头轮跑：等距轮转错相起燃
            for h in range(st["heads"]):
                out.append(dict(k=k, start=(start + h * n / st["heads"]) % n,
                                dir=d1, tau0=tau0 - h / st["heads"]))
        else:
            out.append(dict(k=k, start=start, dir=d1, tau0=tau0))
    return out


def _rr_weight(st, seq_i):
    """设备 seq_i 在所有存活跑动包上的最大包络值 ∈ [0,1]（起-燃-衰减）。"""
    acc = 0.0
    for pk in st["pkgs"]:
        start = pk["start"]
        if st["slide"]:  # 群星坠落：包体在窗内向下一站滑动
            tgt = st["sites"][(pk["k"] + 1) % _RANDRUN_CAP]
            tgt = float(int(math.floor(tgt)) % st["n"])
            start = start + (tgt - start) * min(1.0, max(0.0, pk["tau0"]))
        rel = ((seq_i - start) * pk["dir"]) % st["n"]
        if rel >= st["span"]:
            continue
        tau = pk["tau0"] - st["gap"] * (rel / st["span"])
        if tau < 0.0:
            continue
        if tau < st["attack"]:
            w = _smooth(tau / st["attack"]) if st["soft"] else tau / st["attack"]
        else:
            w = math.exp(-(tau - st["attack"]) / st["dscale"])
        if st["flick"]:  # 火星闪烁：包内逐设备噪声调制
            w *= 0.55 + 0.45 * _vnoise(pk["k"] * 13 + seq_i * 5, st["seed"] + 3)
        if w > acc:
            acc = w
    return acc


def _randrun_kernel(kind):
    cfg = _RANDRUN_KINDS.get(kind) or {}
    def fn(ef, t, sorted_fx, fdefs, *rest):
        params, fps = rest   # 引擎按 6 参位置调用约定不变（params, fps 收尾）
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        n = max(1, len(sorted_fx))
        speed = max(0.01, float(params.get("speed", 2.0)))
        rate = speed * int(cfg.get("tick", 1))
        k0 = int(t * rate)
        seed = int(params.get("seed", 0))
        prox = _clamp01(float(params.get("proximity", float(cfg.get("prox", 0.6)))))
        ent = _randrun_chain(n, prox, seed, min(k0 + 2, _RANDRUN_CAP - 1))
        decay = _clamp01(float(params.get("decay", float(cfg.get("decay", 0.4)))))
        st = dict(
            n=n, t=t, rate=rate, k0=k0, seed=seed,
            sites=ent["sites"], steps=ent["steps"],
            span=max(1, min(n, int(params.get("run_size", int(cfg.get("rs", 2)))))),
            gap=_clamp01(float(params.get("phase_gap", float(cfg.get("gap", 0.5))))),
            dmode=str(params.get("direction_mode", cfg.get("dmode", "随机"))),
            dscale=max(0.06, decay * 0.6),
            attack=0.3 if cfg.get("soft") else (0.05 if cfg.get("punch") else 0.15),
            soft=bool(cfg.get("soft")), flick=bool(cfg.get("flick")),
            slide=bool(cfg.get("slide")), mirror=bool(cfg.get("mirror")),
            heads=int(cfg.get("heads", 1)), bias=cfg.get("bias"),
            lookback=3 if decay > 0.45 else 2,
        )
        st.update(params)   # E1 扩容：新 kind 直接读原始参数槽（proximity/density/mod_depth）
        st["pkgs"] = _rr_pkgs(st)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            w = (_RR2_FNS.get(kind) or _rr_weight)(st, seq_i)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["randrun"] = _randrun_kernel


# ───────────────────────── 5.2 速度忽快忽慢族 ─────────────────────────
def _vs_hump_int(y, c, w, h):
    """升余弦驼峰 hump(s)=h/2*(1+cos(pi*(s-a)/w))（a=c-w）在 [0,y] 的闭式积分。"""
    a = c - w
    if y <= a:
        return 0.0
    d = min(y, c + w) - a
    return h * 0.5 * (d + (w / math.pi) * math.sin(math.pi * d / w))


def _vs_shape_S(shape, y, uni):
    """快慢分配形状的均值零原函数 S(y)（S(1)=0，解析/分段解析，禁数值累积）。

    uni=均匀度偏斜 [0,1]：0=快慢段各半均匀，1=快段极短慢段极长。
    """
    if shape == "三角":
        pk = 0.5 + 0.35 * uni
        if y < pk:
            return y * y / (2.0 * pk) - 0.5 * y
        base = pk / 2.0 + ((y - y * y / 2.0) - (pk - pk * pk / 2.0)) / (1.0 - pk)
        return base - 0.5 * y
    if shape == "阶梯":
        f = 0.5 - 0.35 * uni
        return (1.0 - f) * y / f if y < f else 1.0 - y
    if shape == "顿挫":
        f = 0.28 - 0.13 * uni
        return (1.0 - f) * y / f if y < f else 1.0 - y
    if shape == "呼吸":
        w1 = 0.40 * (1.0 - 0.4 * uni); w2 = 0.16 * (1.0 - 0.4 * uni)
        return (_vs_hump_int(y, 0.5, w1, 1.0) + 0.55 * _vs_hump_int(y, 0.5, w2, 1.0)
                ) - (w1 + 0.55 * w2) * y
    if shape == "心跳":
        w1 = 0.11 * (1.0 - 0.4 * uni); w2 = 0.06 * (1.0 - 0.4 * uni)
        return (_vs_hump_int(y, 0.22, w1, 1.0) + 0.5 * _vs_hump_int(y, 0.52, w2, 1.0)
                ) - (w1 + 0.5 * w2) * y
    if shape == "单循环":  # E1：一次加速-减速完整过程；满 swing 时周期首尾速度触底
        return (1.0 - math.cos(y * math.pi)) * 0.5 - y
    # 正弦：单驼峰（升余弦），驼峰宽度随均匀度收窄
    w = 0.5 - 0.3 * uni
    return _vs_hump_int(y, 0.5, w, 1.0) - w * y


def _varyspeed_teff(t, params):
    """时间重映射 t_eff = Φ(t) = ∫₀ᵗ m(u)du，m(u)=center*(1+swing*shape)，floor 为停顿窗占比。

    swing=0 且无停顿时走恒速快路径（与既有恒速内核逐位一致）。
    """
    center = max(1e-6, float(params.get("speed", 1.0)))
    swing = _clamp01(float(params.get("swing", 0.5)))
    floor = min(0.85, max(0.0, float(params.get("floor", 0.0))))
    if swing <= 1e-9 and floor <= 1e-9:
        return t * center
    cycle = max(0.2, float(params.get("cycle", 4.0)))
    uni = _clamp01(float(params.get("uniformity", 0.3)))
    shape = str(params.get("vs_shape", "正弦"))
    if shape == "游走":  # E1：速度乘子经 vnoise 平滑随机游走（闭式累积表，确定性可跳读）
        return _vs_wander_teff(t, center, swing, max(0.5, cycle), int(params.get("seed", 0)))
    x = t / cycle
    cyc = math.floor(x)
    s = x - cyc
    active = 1.0 - floor
    if s >= active or active <= 1e-6:
        psi = active  # 停顿窗：m 触底 0，Φ 保持不变（载体冻结）
    else:
        psi = s + swing * active * _vs_shape_S(shape, s / active, uni)
    return center * cycle * (cyc * active + psi)


def _vslook_wave(st, pos, seq_i, n):
    # R173：载体默认取 kind 自身的 look 变体（wave_tri→tri / wave_saw→saw_up）；
    # 原先恒默认 sine，致"三角变速/锯齿涌进"实为正弦载体。显式 vs_wave 参数仍优先。
    freq = float(st.get("spatial_freq", 1.0))
    return _waveshape(str(st.get("vs_wave", st["_lookvar"])), pos * freq - st["_teff"],
                      float(st.get("duty", 0.5)))


def _vslook_chase(st, pos, seq_i, n):
    width = max(0.01, float(st.get("width", 0.18)))
    var = st["_lookvar"]
    if var == "kitt":
        bh = 1.0 - abs((st["_teff"] % 2.0) - 1.0)
        return math.exp(-abs(pos - bh) / width)
    dist = (pos - (st["_teff"] % 1.0)) % 1.0
    if var == "dot":
        return 1.0 if dist < width else 0.0
    tail = float(st.get("tail", 0.5))
    return math.exp(-dist / width) if dist < tail else 0.0


def _vslook_breathe(st, pos, seq_i, n):
    ph = st["_teff"] + pos * float(st.get("spread", 0.0))
    var = st["_lookvar"]
    if var == "heartbeat":
        q = ph % 1.0
        return math.exp(-((q - 0.1) ** 2) / 0.004) + 0.6 * math.exp(-((q - 0.3) ** 2) / 0.004)
    if var == "tide":
        return 0.5 - 0.5 * math.cos(ph * 2 * math.pi) + 0.15 * math.sin(ph * 6 * math.pi)
    return _smooth(0.5 - 0.5 * math.cos(ph * 2 * math.pi))


def _vslook_twinkle(st, pos, seq_i, n):
    base = _vnoise(seq_i, int(st.get("seed", 0)))
    if base < float(st.get("density", 0.3)) + 0.4:
        return _smooth(0.5 + 0.5 * math.sin(st["_teff"] * 2 + base * 6.28))
    return 0.1


def _vslook_pumpwave(st, pos, seq_i, n):
    ph = st["_teff"] % 1.0
    w = 0.5 - 0.5 * math.cos((pos * 2 - ph) * 2 * math.pi)
    if st["_lookvar"] == "tide":
        w += 0.12 * math.sin(ph * 6 * math.pi + pos * 3)
    return w


def _vslook_arch(st, pos, seq_i, n):
    ring = ((st["_teff"] % 1.0) - abs(pos - 0.5) * 2.0) % 1.0
    return math.exp(-ring / max(0.01, float(st.get("width", 0.18))))


def _vslook_pendulum(st, pos, seq_i, n):
    w = 0.5 + 0.5 * math.sin(st["_teff"] * math.pi + pos * math.pi)
    return _smooth(w) if st["_lookvar"] == "soft" else w


def _vslook_windmill(st, pos, seq_i, n):
    shape = "twin" if st["_lookvar"] == "dual" else "fold"
    freq = float(st.get("spatial_freq", 2.0))
    return _waveshape(shape, pos * freq - st["_teff"], float(st.get("duty", 0.5)))


_VS_LOOK_FNS = {"wave": _vslook_wave, "chase": _vslook_chase, "breathe": _vslook_breathe,
                "twinkle": _vslook_twinkle, "pumpwave": _vslook_pumpwave, "arch": _vslook_arch,
                "pendulum": _vslook_pendulum, "windmill": _vslook_windmill}

#: 载体 look ≥8 类（波形/跑灯/呼吸/闪烁/泵浪/水拱/摆动/旋转），kind → (look, look 变体)
_VS_KINDS = {
    "wave_sine": ("wave", "sine"), "wave_tri": ("wave", "tri"), "wave_saw": ("wave", "saw_up"),
    "chase_comet": ("chase", "comet"), "chase_dot": ("chase", "dot"), "chase_kitt": ("chase", "kitt"),
    "breathe_soft": ("breathe", "breathe"), "breathe_heart": ("breathe", "heartbeat"),
    "breathe_tide": ("breathe", "tide"),
    "twinkle_drift": ("twinkle", "drift"), "twinkle_grid": ("twinkle", "grid"),
    "pumpwave": ("pumpwave", "wave"), "pumpwave_tide": ("pumpwave", "tide"),
    "arch": ("arch", "arch"), "arch_wide": ("arch", "arch"),
    "pendulum": ("pendulum", "std"), "pendulum_soft": ("pendulum", "soft"),
    "windmill": ("windmill", "std"), "windmill_dual": ("windmill", "dual"),
}


def _varyspeed_kernel(kind):
    look, lookvar = _VS_KINDS.get(kind, ("wave", "sine"))
    def fn(ef, t, sorted_fx, fdefs, *rest):
        params, fps = rest   # 引擎按 6 参位置调用约定不变（params, fps 收尾）
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        st = dict(params)
        st["_look"] = look
        st["_lookvar"] = lookvar
        st["_teff"] = _varyspeed_teff(t, params)
        wf = _VS_LOOK_FNS[look]
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            w = wf(st, pos, seq_i, n)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return fn


KERNELS["varyspeed"] = _varyspeed_kernel


# ───────────────────────── 5.3 折返级联族 ─────────────────────────
_FC_FRONT_FNS = {
    "exp": lambda d, wd: math.exp(-d / max(0.02, wd)),
    "band": lambda d, wd: max(0.0, 1.0 - d / max(0.02, wd * 2.0)),
    "sharp": lambda d, wd: (1.0 if d < wd else
                            (math.exp(-(d - wd) / max(0.02, wd * 0.4))
                             if d < wd * 2.5 else 0.0)),
    "cos": lambda d, wd: (max(0.0, math.cos(min(1.0, d / max(0.02, wd * 2.0)) * math.pi * 0.5))
                          if d < wd * 2.0 else 0.0),
}

_FC_KIND_CFG = {
    "fold": dict(front="exp"), "slide": dict(front="exp"), "bounce": dict(front="exp"),
    "chasef": dict(front="sharp"), "surge": dict(front="cos"), "layer": dict(front="band"),
    "arch": dict(front="exp", arch=True), "ripple": dict(front="exp", ret=True),
    "fold3": dict(front="exp"), "bloomc": dict(front="exp"), "bloomedge": dict(front="exp"),
    "dual": dict(front="exp"), "pingpong": dict(front="exp"),
}


def _fc_origin_head(kind, k, oneway):
    """bloomc/bloomedge/dual 的（起点, 落点, 波前模式）闭式解；不匹配返回 None。"""
    if kind == "bloomc":   # 交错开花：中心外扩 → 两端接力交替
        o = 0.5 if k == 0 else (1.0 if k % 2 == 1 else 0.0)
        return o, (0.0 if o > 0.5 else 1.0), "out"
    if kind == "bloomedge":  # 双边开花：两端交替外扩（错瓣由 seed 决定）
        o = 0.0 if (k % 2 == 0 or oneway) else 1.0
        return o, 1.0 - o, "out"
    if kind == "dual":     # 左右开弓：两端向中 ↔ 中向外 交替
        if k % 2 == 0 or oneway:
            return 0.0, 0.5, "in"
        return 0.5, 0.5, "out"
    return None


def _fc_origins(kind, k, oneway):
    """波 k 的（起点, 落点, 波前模式）闭式解；落点 = 下一波起点（落点跨波接力）。

    mode：single=单波前 out=自起点双向外扩 in=两端向中 cross=两端对穿全幅。
    """
    hit = _fc_origin_head(kind, k, oneway)
    if hit is not None:
        return hit
    if kind == "pingpong":  # 乒乓对滑：两波前恒相对对穿
        return 0.0, 1.0, "cross"
    if kind == "fold3":    # 三段折返：1/3 段落跳接，段内方向交替
        seg = k % 3
        lo = seg / 3.0; hi = (seg + 1) / 3.0
        return (lo, hi, "single") if ((k // 3) % 2 == 0 or oneway) else (hi, lo, "single")
    o = 0.0 if (k % 2 == 0 or oneway) else 1.0
    return o, 1.0 - o, "single"


def _fc_fronts(st, k, p):
    """波 k 在进度 p 下的波前位置列表（out/in/cross 为双波前）。"""
    fn2 = _FC2_FRONTS.get(st["kind"])   # E1 扩容：新 kind 分发表路由
    if fn2 is not None:
        return fn2(st, k, p)
    o, L, mode = _fc_origins(st["kind"], k, st["oneway"])
    if mode == "single":
        if st.get("ret"):   # 涟漪回流：进到落点再折回起点（落点按折返点传递）
            p = 1.0 - abs(2.0 * p - 1.0)
        return [o + (L - o) * p]
    if mode == "out":
        d = p * max(o, 1.0 - o)
        if st["kind"] == "bloomedge" and st["seed"]:
            d2 = d * (0.7 + 0.6 * _vnoise(k * 3, st["seed"] + 2))  # 双瓣交错
            return [max(0.0, o - d), min(1.0, o + d2)]
        return [max(0.0, o - d), min(1.0, o + d)]
    if mode == "in":
        return [p * 0.5, 1.0 - p * 0.5]
    return [p, 1.0 - p]


def _fc_weight(st, x):
    """设备坐标 x 的波场权重：各波前轮廓取最大（+ 拱形包络 / 弹球衰减）。"""
    wf = _FC_FRONT_FNS[st["front"]]
    w = 0.0
    for F in st["fronts"]:
        w = max(w, wf(abs(x - F), st["width"]))
    if st["arch"]:
        w *= math.sin(min(1.0, st["p"]) * math.pi)
    if st["amp"] < 1.0:
        w *= st["amp"]
    return w


def _fc_device(st, x):
    """单设备权重（含波尾交接过渡：淡接 / 花开）。"""
    fn3 = _FC2_DEVICE.get(st["kind"])   # E1 扩容：新 kind 分发表路由
    if fn3 is not None:
        return _clamp01(fn3(st, x))
    w = _fc_weight(st, x)
    v = st["v"]
    if v <= 0.0:
        return w
    if st["handoff"] == "淡接":
        return w * (1.0 - _smooth(v))
    if st["handoff"] == "花开":
        vv = _smooth(v)
        dspan = vv * 0.35
        wb = 0.0
        for F in st["bc"]:
            wb = max(wb, _FC_FRONT_FNS["exp"](abs(x - (F - dspan)), st["width"] * 0.7))
            wb = max(wb, _FC_FRONT_FNS["exp"](abs(x - (F + dspan)), st["width"] * 0.7))
        return max(w * (1.0 - 0.6 * vv), wb)
    return w


def _fc_pause_sweep(ef, sorted_fx, ch, lo) -> None:
    """停顿窗整波静默：把通道统一压到低值 lo（明确参数 pause）。"""
    for orig_i, _fi in sorted_fx:
        ef.values[orig_i][ch] = lo


def _foldcascade_kernel(kind):
    cfg = _FC_KIND_CFG.get(kind) or _FC_KIND_CFG["fold"]
    def fn(ef, t, sorted_fx, fdefs, *rest):
        params, fps = rest   # 引擎按 6 参位置调用约定不变（params, fps 收尾）
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0)); hi = int(params.get("hi", 255))
        n = max(1, len(sorted_fx))
        speed = max(0.01, float(params.get("speed", 0.5)))
        tw = 1.0 / speed                       # 单波时长（不含停顿）
        pause = max(0.0, float(params.get("pause", 0.12)))
        tp = tw * (1.0 + pause)
        k = int(t / tp)
        u = max(0.0, (t - k * tp) / tw)        # 波内进度 [0,1)；≥1 → 组间停顿窗（钳 0：波界浮点误差负底幂得复数）
        if u >= 1.0:                           # 停顿窗：整波静默（明确参数 pause）
            _fc_pause_sweep(ef, sorted_fx, ch, lo)
            return
        handoff = str(params.get("handoff", "硬接"))
        accel = _clamp01(float(params.get("accel", 0.35)))
        st = dict(kind=kind, seed=int(params.get("seed", 0)),
                  oneway=str(params.get("direction", "往返")) == "单向",
                  width=max(0.02, float(params.get("width", 0.14))),
                  front=cfg.get("front", "exp"), arch=bool(cfg.get("arch")),
                  ret=bool(cfg.get("ret")),
                  amp=0.35 + 0.65 * (0.72 ** (k % 6)) if kind == "bounce" else 1.0,
                  handoff=handoff, v=0.0, bc=())
        p = u ** (1.0 + 2.2 * accel)           # 加速度：由慢到快推进
        hh = 0.0 if handoff == "硬接" else 0.22
        if hh > 0.0 and u > 1.0 - hh:          # 波尾交接段
            st["v"] = (u - (1.0 - hh)) / hh
        pr = p * (1.0 - _smooth(st["v"])) if handoff == "回卷" else p
        st["p"] = pr
        st["fronts"] = _fc_fronts(st, k, pr)
        if st["v"] > 0.0 and handoff == "花开":
            st["bc"] = _fc_fronts(st, k, 1.0)  # 落点（p=1 的波前）双向开花
        groups = max(1, int(params.get("groups", 1)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            x = pos if groups <= 1 else (math.floor(pos * groups) + 0.5) / groups
            w = _fc_device(st, x)
            ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))
    return _native_width.register_native(fn)


KERNELS["foldcascade"] = _foldcascade_kernel


# ═══════════════════════════════════════════════════════════════════════════
#  R172 三族扩容（E1）：randrun +8 / varyspeed +9 look + 2 速度 shape /
#  foldcascade +10（R173 精编后留存数）。纪律（照 ai-memory/effect-library-standards.md）：
#  · 全部确定性 f(t, seq_i, params)；新随机只用 _vnoise/_snoise + 步窗/查表；
#    需要"历史/洗牌"的用带容量上限的模块级缓存（照 _randrun_chain 模式）。
#  · 既有 47 kind 的输出路径**逐位不变**：仅 dict 分发表 + 单点路由（无分支
#    塞进旧内核），swing=0 恒速快路径 / pause / proximity 语义全部保留。
#  · E2（C 化）交接：新 kind 的参数槽 = 各族既有参数 + 新增槽（burst→density、
#    denswave→mod_depth、游走 shape→seed）；新增数学分支标记见各 fn docstring
#    与 _RR2_FNS/_VS_LOOK_FNS+_VS_KINDS/_FC2_FRONTS/_FC2_DEVICE 分发表。
# ═══════════════════════════════════════════════════════════════════════════

# ───────────────────────── E1.randrun +8 ─────────────────────────
# 新 kind 一律"每 kind 一个权重函数"：(st, seq_i) -> w∈[0,1]。
# st 由 _randrun_kernel 构建（update(params) 后含 proximity/density/mod_depth 等原始槽）。
_RR2_FNS: dict = {}
_RR2_PERM: dict = {}


def _rr2_env(st, tau):
    """单触发包络（起-燃-衰减），tau=触发后经过的触发单位数（复用内核 state）。"""
    if tau < 0.0:
        return 0.0
    if tau < st["attack"]:
        return _smooth(tau / st["attack"]) if st["soft"] else tau / st["attack"]
    return math.exp(-(tau - st["attack"]) / st["dscale"])


def _rr2_zonepack(st, seq_i, spec):
    """半区受限跑动包：包与包内相位差都限制在 spec=(z0,z1,start,d1,tau0) 的半区环内。"""
    z0, z1, start, d1, tau0 = spec
    if not (z0 <= seq_i < z1):
        return 0.0
    span = max(1.0, min(float(st["span"]), z1 - z0))
    rel = ((seq_i - start) * d1) % (z1 - z0)
    if rel >= span:
        return 0.0
    return _rr2_env(st, tau0 - st["gap"] * (rel / span))


def _rr2_dualzone(st, seq_i):
    """双域对跑：左右两个半区各自独立随机游走包（独立触发链），每窗各发一包。【新增分支】"""
    n = st["n"]
    half = (n + 1) // 2
    prox = _clamp01(float(st.get("proximity", 0.6)))
    acc = 0.0
    for back in range(st["lookback"] + 1):
        k = st["k0"] - back
        if k < 0:
            continue
        tau0 = st["t"] * st["rate"] - k
        for z0, z1, sd in ((0, half, st["seed"]), (half, n, st["seed"] + 991)):
            zn = z1 - z0
            if zn <= 0:
                continue
            ent = _randrun_chain(zn, prox, sd, k)
            d1 = _rr_pack_dir(st["dmode"], k, ent["steps"][k], st["seed"])
            start = z0 + float(int(ent["sites"][k]) % zn)
            acc = max(acc, _rr2_zonepack(st, seq_i, (z0, z1, start, d1, tau0)))
    return acc


def _rr2_perm(n, seed):
    """种子洗牌全排列（Fisher-Yates，_vnoise 驱动，带容量上限缓存）。"""
    key = (n, seed)
    perm = _RR2_PERM.get(key)
    if perm is None:
        perm = list(range(n))
        for i in range(n - 1, 0, -1):
            j = int(_vnoise(i * 31 + 7, seed + 13) * (i + 1)) % (i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        if len(_RR2_PERM) > 48:
            _RR2_PERM.clear()
        _RR2_PERM[key] = perm
    return perm


def _rr2_shufwalk(st, seq_i):
    """乱序点检：位置按种子洗牌的全排列依次触发，一遍完重洗（覆盖保证）。

    phase_gap：0=每点整体闪；1=窗内滑向下一点。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    perm = _rr2_perm(n, st["seed"])
    acc = 0.0
    for back in range(st["lookback"] + 1):
        k = st["k0"] - back
        if k < 0:
            continue
        tau0 = t * rate - k
        cur = perm[k % n]
        nxt = perm[(k + 1) % n]
        c = cur + (nxt - cur) * st["gap"] * _smooth(min(1.0, tau0))
        acc = max(acc, _rr2_env(st, tau0) * math.exp(
            -((seq_i - c) ** 2) / (2.0 * max(1.0, st["span"] * 0.6) ** 2)))
    return acc


def _rr2_siteripple(st, seq_i):
    """随机涟心：每个随机落点发出双向扩散环（非跑动包），环速/环宽随 speed/run_size。

    环幅以 0.45× 触发钟衰减（环比包存活更久，能扩散到远处）。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    acc = 0.0
    for back in range(8):
        k = st["k0"] - back
        if k < 0:
            continue
        tau = t * rate - k
        ent = _randrun_chain(n, _clamp01(float(st.get("proximity", 0.6))), st["seed"], k)
        c = ent["sites"][k]
        d = min(abs(seq_i - c), n - abs(seq_i - c))
        r = tau * (1.2 + 0.5 * st["span"])
        th = 0.6 + 0.3 * st["span"]
        g = abs(d - r)
        if g < th:
            acc = max(acc, _rr2_env(st, tau * 0.45) * (1.0 - g / th))
    return acc


def _rr2_burst(st, seq_i):
    """簇发乱蹦：2-4 连发快簇 + 静默窗交替；density 控簇内间隔（0 疏 1 密）。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    dens = _clamp01(float(st.get("density", 0.6)))
    gap_step = 0.42 - 0.30 * dens
    sup = 2.4
    cyc = max(0, int(t * rate / sup))
    fr = t * rate - cyc * sup
    hits = 2 + int(_vnoise(cyc * 13 + 1, st["seed"] + 5) * 2.999)
    ent = _randrun_chain(n, _clamp01(float(st.get("proximity", 0.6))), st["seed"], cyc)
    site0 = float(int(ent["sites"][cyc]) % n)
    d1 = _rr_pack_dir(st["dmode"], cyc, ent["steps"][cyc], st["seed"])
    span = max(1.0, min(float(st["span"]), n))
    acc = 0.0
    for j in range(hits):
        off = j * gap_step
        if fr < off:
            continue
        rel = abs(seq_i - (site0 + j * d1) % n)
        if rel < span:
            acc = max(acc, _rr2_env(st, fr - off - st["gap"] * 0.4 * (rel / span)))
    return acc


def _rr2_converge(st, seq_i):
    """对撞乱汇：两落点相向游走、中央对撞闪光，周期末端从两端重生。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    sup = 5.0
    cyc = max(0, int(t * rate / sup))
    fr = t * rate / sup - cyc
    prox = _clamp01(float(st.get("proximity", 0.5)))
    s1 = 0.04 + (1.0 - prox) * 0.30 * _vnoise(cyc * 7 + 3, st["seed"] + 9)
    prog = _smooth(min(1.0, fr / 0.86))
    p1 = (s1 + (0.5 - s1) * prog) * (n - 1)
    wdt = max(0.7, st["span"] * 0.45)
    acc = max(math.exp(-((seq_i - p1) ** 2) / (2 * wdt ** 2)),
              math.exp(-((seq_i - (n - 1 - p1)) ** 2) / (2 * wdt ** 2)))
    flash = math.exp(-((fr - 0.92) ** 2) / 0.0016)
    if flash > 0.02:
        acc = max(acc, flash * math.exp(-((seq_i - (n - 1) * 0.5) ** 2)
                                        / (2 * (n * 0.12) ** 2)))
    return acc


def _rr2_parit(st, seq_i):
    """奇偶乱跳：触发在奇数/偶数序位间交替，各自区内随机落点。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    acc = 0.0
    for back in range(st["lookback"] + 1):
        k = st["k0"] - back
        if k < 0:
            continue
        par = k % 2
        if seq_i % 2 != par:
            continue
        ent = _randrun_chain(n, _clamp01(float(st.get("proximity", 0.5))),
                             st["seed"] + par * 577, k // 2)
        site = int(ent["sites"][k // 2]) % n
        if site % 2 != par:
            site = (site + 1) % n
        span_p = max(1.0, st["span"] * 0.5)
        relp = abs(seq_i - site) * 0.5
        if relp < span_p:
            acc = max(acc, _rr2_env(st, t * rate - k - st["gap"] * 0.5 * (relp / span_p)))
    return acc


def _rr2_denswave(st, seq_i):
    """密度潮汐：触发密度被慢波调制，繁忙段与安静段交替（mod_depth 控深度）。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    depth = _clamp01(float(st.get("mod_depth", 0.75)))
    acc = 0.0
    for back in range(st["lookback"] + 1):
        k = st["k0"] - back
        if k < 0:
            continue
        tide = 0.5 + 0.5 * depth * math.cos(k * 2 * math.pi / 14.0)
        if _vnoise(k * 23 + 9, st["seed"] + 31) > tide:
            continue
        ent = _randrun_chain(n, _clamp01(float(st.get("proximity", 0.6))), st["seed"], k)
        start = float(int(ent["sites"][k]) % n)
        d1 = _rr_pack_dir(st["dmode"], k, ent["steps"][k], st["seed"])
        rel = ((seq_i - start) * d1) % n
        if rel < st["span"]:
            acc = max(acc, _rr2_env(st, t * rate - k - st["gap"] * (rel / st["span"])))
    return acc


def _rr2_wavewalk(st, seq_i):
    """波形游走：正弦波形的峰值位置本身随机游走（波形与随机的叠加态）。【新增分支】"""
    n, rate, t = st["n"], st["rate"], st["t"]
    k = min(st["k0"], _RANDRUN_CAP - 2)
    ent = _randrun_chain(n, _clamp01(float(st.get("proximity", 0.8))), st["seed"], k + 1)
    tau = t * rate - k
    s0 = ent["sites"][k]
    s1 = ent["sites"][k + 1]
    delta = ((s1 - s0 + n * 0.5) % n) - n * 0.5
    c = (s0 + delta * _smooth(min(1.0, tau))) % n
    d = min(abs(seq_i - c), n - abs(seq_i - c))
    lam = 2.0 + 2.0 * st["span"]
    return 0.25 + 0.75 * max(0.0, math.cos(math.pi * d / lam))


_RR2_FNS.update({
    "dualzone": _rr2_dualzone, "shufwalk": _rr2_shufwalk,
    "siteripple": _rr2_siteripple, "burst": _rr2_burst,
    "converge": _rr2_converge, "parit": _rr2_parit, "denswave": _rr2_denswave,
    "wavewalk": _rr2_wavewalk,
})

_RANDRUN_KINDS.update({
    # kind: (跑动宽度 rs, 临近 prox, 相位差 gap, 衰减 decay, 方向 dmode, 特殊标志)
    "dualzone":   dict(rs=3, prox=0.60, gap=0.50, decay=0.45, dmode="跟随"),
    "shufwalk":   dict(rs=2, prox=0.90, gap=0.00, decay=0.45, dmode="随机"),
    "siteripple": dict(rs=2, prox=0.70, gap=0.00, decay=0.75, dmode="随机"),
    "burst":      dict(rs=2, prox=0.60, gap=0.30, decay=0.45, dmode="随机"),
    "converge":   dict(rs=3, prox=0.50, gap=0.00, decay=0.50, dmode="随机"),
    "parit":      dict(rs=4, prox=0.50, gap=0.30, decay=0.50, dmode="随机"),
    "denswave":   dict(rs=2, prox=0.60, gap=0.40, decay=0.50, dmode="随机"),
    "wavewalk":   dict(rs=3, prox=0.80, gap=0.00, decay=0.60, dmode="跟随"),
})


# ───────────────────────── E1.varyspeed +9 look + 2 速度 shape ─────────────────────────
_VS_WANDER_CACHE: dict = {}
_VS_WANDER_PERIOD = 1024


def _vs_wander_teff(t, center, swing, cycle, seed):
    """游走变速的时间重映射 Φ(t)：m(u)=center*(1+swing*(2v-1))，v 为步间平滑噪声。

    以 1024 步为一周期预计算闭式累积表（带缓存、容量上限），任意 t 直接闭式求值
    → 确定性可跳读，无逐帧累积。【新增分支】
    """
    step = max(0.2, cycle * 0.25)
    key = (seed, round(swing, 4), round(step, 4))
    ent = _VS_WANDER_CACHE.get(key)
    if ent is None:
        vs = [_vnoise(j * 5 + 13, seed) for j in range(_VS_WANDER_PERIOD + 1)]
        cum = [0.0]
        for j in range(_VS_WANDER_PERIOD):
            cum.append(cum[-1] + 1.0 + swing * (vs[j] + vs[j + 1] - 1.0))
        if len(_VS_WANDER_CACHE) > 16:
            _VS_WANDER_CACHE.clear()
        ent = (vs, cum)
        _VS_WANDER_CACHE[key] = ent
    vs, cum = ent
    x = t / step
    idx = int(x)
    f = x - idx
    i = idx % _VS_WANDER_PERIOD
    base = (idx // _VS_WANDER_PERIOD) * cum[_VS_WANDER_PERIOD] + cum[i]
    within = (f * (1.0 + swing * (2.0 * vs[i] - 1.0))
              + swing * 2.0 * (vs[i + 1] - vs[i]) * (f ** 3 - 0.5 * f ** 4))
    return center * step * (base + within)


# ── E1：9 个新载体 look（挂 _VS_LOOK_FNS；时间源统一 st["_teff"]）──
def _vslook_wavepkt(st, pos, seq_i, n):
    """波包：局部振荡包行进，包内可见波峰波谷。【新增分支】"""
    freq = max(0.5, float(st.get("spatial_freq", 1.0)))
    d = pos - (st["_teff"] % 1.0)
    env = math.exp(-(d * d) / 0.016)
    return 0.5 + 0.5 * env * math.cos(d * freq * 18.85)


def _vslook_string(st, pos, seq_i, n):
    """绳波：两列反向波叠加的驻波形态（波腹原地起伏）。【新增分支】"""
    freq = max(0.5, float(st.get("spatial_freq", 1.5)))
    return 0.5 + 0.45 * math.cos(pos * freq * 6.283) * math.cos(st["_teff"] * 6.283)


def _vslook_marquee(st, pos, seq_i, n):
    """走马灯：等宽亮块循环推进。【新增分支】"""
    width = max(0.02, float(st.get("width", 0.12)))
    grp = max(2, int(round(1.0 / width)))
    return 1.0 if (int(seq_i + st["_teff"] * grp) % grp) == 0 else 0.0


def _vslook_levelbar(st, pos, seq_i, n):
    """水位条：水位线沿阵列升起又回落（蓄水→放水）。【新增分支】"""
    lvl = 0.5 - 0.5 * math.cos(st["_teff"] * 2 * math.pi)
    return 1.0 if pos <= lvl else 0.06


def _vslook_buildup(st, pos, seq_i, n):
    """蓄势：逐级抬升的 build 形态（四级台阶、级间短过渡）。【新增分支】"""
    h = st["_teff"] % 1.0
    step = math.floor(h * 4.0)
    hq = (step + min(1.0, (h * 4.0 - step) / 0.35)) / 4.0
    return 1.0 if pos <= hq else 0.04


def _vslook_pluck(st, pos, seq_i, n):
    """拨弦：快攻慢衰的单发拨音形态（整场同步）。【新增分支】"""
    q = st["_teff"] % 1.0
    return q / 0.04 if q < 0.04 else math.exp(-q / 0.30)


def _vslook_swell(st, pos, seq_i, n):
    """涌起：慢攻慢放带 plateau 的涌浪形态（沿 spread 缓慢行进）。【新增分支】"""
    q = (st["_teff"] - pos * float(st.get("spread", 0.35))) % 1.0
    return _smooth(min(1.0, q / 0.45)) * _smooth(min(1.0, (1.0 - q) / 0.30))


def _vslook_stair(st, pos, seq_i, n):
    """阶梯：整场量化台阶爬升。【新增分支】"""
    q = st["_teff"] % 1.0
    return math.floor(q * 5.0) / 4.0


def _vslook_fountain(st, pos, seq_i, n):
    """泉涌：复用 _pump_height 跑泉包做变速桥（与恒速跑泉的区别在变速）。【新增分支·桥】"""
    u = pos * 2.0 - 1.0
    return _pump_height("runjet", pos, u, seq_i, n, st["_teff"],
                        {"speed": 1.0, "width": float(st.get("width", 0.18))})


_VS_LOOK_FNS.update({
    "wavepkt": _vslook_wavepkt, "string": _vslook_string,
    "marquee": _vslook_marquee, "levelbar": _vslook_levelbar, "buildup": _vslook_buildup,
    "pluck": _vslook_pluck, "swell": _vslook_swell, "stair": _vslook_stair,
    "fountain": _vslook_fountain,
})

_VS_KINDS.update({
    "wavepkt": ("wavepkt", "std"), "string": ("string", "std"),
    "marquee": ("marquee", "std"), "levelbar": ("levelbar", "std"), "buildup": ("buildup", "std"),
    "pluck": ("pluck", "std"), "swell": ("swell", "std"), "stair": ("stair", "std"),
    "fountain": ("fountain", "std"),
})


# ───────────────────────── E1.foldcascade +10 ─────────────────────────
# 新 kind 走分发表：_FC2_FRONTS（波前/进度数学）+ _FC2_DEVICE（设备权重绘制），
# 不给 _foldcascade_kernel 增加任何分支。kind 仍登记进 _FC_KIND_CFG（E2 参数槽来源）。
_FC2_FRONTS: dict = {}
_FC2_DEVICE: dict = {}


def _fc2_stdf(st, k, p, spec):
    """按 spec=(o, L, mode) 复用既有波前几何（single/out/in/cross）。"""
    o, L, mode = spec
    if mode == "single":
        if st.get("ret"):
            p = 1.0 - abs(2.0 * p - 1.0)
        return [o + (L - o) * p]
    if mode == "out":
        d = p * max(o, 1.0 - o)
        return [max(0.0, o - d), min(1.0, o + d)]
    if mode == "in":
        return [p * 0.5, 1.0 - p * 0.5]
    return [p, 1.0 - p]


def _fc2_fold_ol(st, k):
    """往返折返的（起点, 落点）。"""
    o = 0.0 if (k % 2 == 0 or st["oneway"]) else 1.0
    return o, 1.0 - o


def _fc2_trianchor(st, k, p):
    """三点轮折：三个锚点（两端+中心）轮转接力。【新增分支】"""
    o, L = [(0.0, 0.5), (0.5, 1.0), (1.0, 0.0)][k % 3]
    return _fc2_stdf(st, k, p, (o, L, "single"))


def _fc2_meetbloom(st, k, p):
    """汇流绽放：两端双波向中心汇合，合流点再向外双环开花（合→开交替）。【新增分支】"""
    if k % 2 == 0:
        return _fc2_stdf(st, k, p, (0.5, 0.5, "in"))
    d = p * 0.5
    return [0.5 - d, 0.5 + d, 0.5 - d * 0.45, 0.5 + d * 0.45]


def _fc2_decay(st, k, p):
    """衰减折返：每波幅度/宽度递减，5 波后回满重置（能量耗尽感）。【新增分支】"""
    e = 0.78 ** (k % 5)
    st["amp"] = 0.12 + 0.88 * e
    w0 = st.get("w0") or st["width"]
    st["w0"] = w0
    st["width"] = max(0.03, w0 * (0.35 + 0.65 * e))
    o, L = _fc2_fold_ol(st, k)
    return _fc2_stdf(st, k, p, (o, L, "single"))


def _fc2_echofol_fronts(st, k, p):
    """影随：主波 + 半幅随影（半速差追随）的锚点记录。【新增分支】"""
    o, L = _fc2_fold_ol(st, k)
    st["ej"] = (o, L)
    return _fc2_stdf(st, k, p, (o, L, "single"))


def _fc2_echofol_dev(st, x):
    """影随：主波前全幅、其后 55% 进度处拖半幅随影。【新增分支】"""
    wf = _FC_FRONT_FNS[st["front"]]
    o, L = st["ej"]
    F1 = o + (L - o) * st["p"]
    F2 = o + (L - o) * (st["p"] * 0.55)
    w = wf(abs(x - F1), st["width"])
    return max(w, 0.45 * wf(abs(x - F2), st["width"] * 0.8))


def _fc2_elevator(st, k, p):
    """电梯折返：离散步进上升 + 顶层折返，步间停顿。【新增分支】"""
    s = 4.0
    step = math.floor(p * s)
    pq = (step + min(1.0, (p * s - step) / 0.6)) / s
    o, L = _fc2_fold_ol(st, k)
    return [o + (L - o) * pq]


def _fc2_trough_dev(st, x):
    """波谷引导：反相级联——行进的是暗谷，落点处暗花外扩。【新增分支】"""
    w = _fc_weight(st, x)
    out = 0.9 - 0.9 * w
    if st["v"] > 0.0 and st["bc"]:
        vv = _smooth(st["v"])
        for F in st["bc"]:
            dark = _FC_FRONT_FNS["exp"](abs(x - F), st["width"] * 0.7) * vv
            out = min(out, 0.9 - 0.9 * dark)
    return out


def _fc2_wallpush_fronts(st, k, p):
    """水墙推进：宽平水墙整体推进 + 端点折返（填充由 _FC2_DEVICE 完成）。【新增分支】"""
    o, L = _fc2_fold_ol(st, k)
    st["pdir"] = 1.0 if o < 0.5 else -1.0
    return [o + (L - o) * p]


def _fc2_wallpush_dev(st, x):
    """水墙推进：band 墙头前沿 + 墙后保持填充（泵友好）。【新增分支】"""
    wf = _FC_FRONT_FNS["band"]
    w = 0.0
    for F in st["fronts"]:
        w = max(w, wf(abs(x - F), st["width"]))
        behind = x < F if st["pdir"] > 0 else x > F
        if behind:
            w = max(w, 0.82)
    return w


def _fc2_interleave(st, k, p):
    """双源交错：两个源（1/3、2/3）交替发波，各自推向对端。【新增分支】"""
    if k % 2 == 0:
        return _fc2_stdf(st, k, p, (1.0 / 3.0, 1.0, "single"))
    return _fc2_stdf(st, k, p, (2.0 / 3.0, 0.0, "single"))


def _fc2_ringfold_fronts(st, k, p):
    """环折：折返点沿环行进，波前自折返点双向绕环。【新增分支】"""
    f = (k * 0.25 + p * 0.5) % 1.0
    a = (f - p * 0.5) % 1.0
    b = (f + p * 0.5) % 1.0
    st["ring"] = (a, b)
    return [a, b]


def _fc2_ringfold_dev(st, x):
    """环折：波前距离按环上最短弧计算（对圆弧排布友好，跨接缝连续）。【新增分支】"""
    wf = _FC_FRONT_FNS[st["front"]]
    w = 0.0
    for F in st["ring"]:
        w = max(w, wf(_wrapdist(x, F), st["width"]))
    return w


def _fc2_microbloom(st, k, p):
    """碎花连绽：每波在随机落点小型开花（多而小，区别于 bloomc 少而大）。【新增分支】"""
    o = 0.1 + 0.8 * _vnoise(k * 9 + 2, st["seed"] + 3)
    d = p * 0.22
    return [max(0.0, o - d), min(1.0, o + d)]


_FC2_FRONTS.update({
    "trianchor": _fc2_trianchor, "meetbloom": _fc2_meetbloom, "decay": _fc2_decay,
    "echofol": _fc2_echofol_fronts, "elevator": _fc2_elevator,
    "wallpush": _fc2_wallpush_fronts, "interleave": _fc2_interleave,
    "ringfold": _fc2_ringfold_fronts, "microbloom": _fc2_microbloom,
})

_FC2_DEVICE.update({
    "echofol": _fc2_echofol_dev,
    "trough": _fc2_trough_dev, "wallpush": _fc2_wallpush_dev,
    "ringfold": _fc2_ringfold_dev,
})

_FC_KIND_CFG.update({
    "trianchor": dict(front="exp"), "meetbloom": dict(front="exp"),
    "decay": dict(front="exp"), "echofol": dict(front="exp"),
    "elevator": dict(front="sharp"), "trough": dict(front="exp"),
    "wallpush": dict(front="band"),
    "interleave": dict(front="cos"), "ringfold": dict(front="cos"),
    "microbloom": dict(front="exp"),
})
