# -*- coding: utf-8 -*-
"""【通用/音频】真音频效果的**实现函数**（每个 variant 一个 0..1 权重函数）。

签名统一 `fn(a, t, i, n, p)`：`a`=`_AudioRead` 音频量快照、`t`=帧时间（已乘 rate）、
`i`=序列下标、`n`=设备数、`p`=参数字典。返回 0..1 权重（引擎再按 lo/hi 映射成 DMX）。

**全部假定有真实音频**（`a.ok` 为真）—— 无音频的静音处理在内核 `true_audio_kernel`。
"""
from __future__ import annotations

import math

from .effect_presets_audio_true import (_clamp01, _euclid, _f, _seq_pos,
                                        _smoothstep)


# ─────────────────────────────────────────────────────────────────────────────
#  效果实现：`atx_<name>(a, t, i, n, p)` → 0..1 权重
#
#  · a = _AudioRead（本帧音频量）
#  · t = 帧时间（秒）  · i = 序列下标  · n = 设备总数  · p = 参数字典
#  · 返回 0..1 的单通道权重；引擎再按 lo/hi 映射到 DMX 值
#  · **全部假定 a.ok 为真**（无音频时引擎/内核已提前静音，见 `_true_audio_family`）
# ─────────────────────────────────────────────────────────────────────────────

# ── 基础族（14）：单一音频量直驱 ─────────────────────────────────────────────

def _f_rms_gate(a, t, i, n, p):
    """整体能量闸门：过阈值才亮，亮度随能量。"""
    thr = _f(p.get("threshold", 0.12))
    return _smoothstep(thr, min(1.0, thr + 0.25), a.rms)


def _f_rms_breath(a, t, i, n, p):
    """呼吸：能量驱动正弦呼吸曲线（弱音慢、强音快）。"""
    rate = 1.0 + a.rms * 3.0
    return _clamp01(0.5 + 0.5 * math.sin(t * rate * math.tau * 0.5 + _seq_pos(n, i)))


def _f_rms_gradient(a, t, i, n, p):
    """沿设备序列的亮度梯度：能量决定坡的陡峭。"""
    steep = 0.5 + a.rms * 4.0
    return _clamp01(math.exp(-_seq_pos(n, i) * steep) * (0.35 + a.rms * 0.65) + a.rms * 0.25)


def _f_peak_flash(a, t, i, n, p):
    """峰值闪：峰值越高越亮，带拍衰减。"""
    return _clamp01(max(a.peak, a.rms) * (0.55 + 0.45 * a.beat_decay(t, 0.18)))


def _f_band_low(a, t, i, n, p):
    """低频（贝斯/大鼓）驱动全组。"""
    return _clamp01(a.low * 1.25)


def _f_band_mid(a, t, i, n, p):
    """中频（人声/旋律）驱动全组。"""
    return _clamp01(a.mid * 1.35)


def _f_band_high(a, t, i, n, p):
    """高频（镲片/亮度）驱动全组。"""
    return _clamp01(a.high * 1.5)


def _f_beat_pulse(a, t, i, n, p):
    """节拍脉冲：每拍全组闪一次。"""
    return a.beat_decay(t, _f(p.get("decay", 0.2)))


def _f_beat_strobe(a, t, i, n, p):
    """节拍频闪：拍相位前段亮、后段灭（硬切）。"""
    duty = _clamp01(_f(p.get("duty", 0.35)))
    per = a.beat_period()
    ph = (t % per) / per if per > 0 else 0.0
    return 1.0 if ph < duty else 0.0


def _f_beat_offbeat(a, t, i, n, p):
    """反拍：只在拍间后半段亮（与正拍互补）。"""
    per = a.beat_period()
    ph = (t % per) / per if per > 0 else 0.0
    return _clamp01(_smoothstep(0.5, 0.62, ph) * (1.0 - _smoothstep(0.88, 1.0, ph)))


def _f_onset_accent(a, t, i, n, p):
    """起音强调：只对瞬态（鼓点/拨弦）响应，持续音不亮。"""
    sens = _f(p.get("sensitivity", 0.3))
    return _smoothstep(sens, min(1.0, sens + 0.3), a.onset)


def _f_energy_meter(a, t, i, n, p):
    """电平表：设备序列 = 电平条的等分刻度。"""
    lvl = _clamp01(a.rms * 1.15)
    return 1.0 if _seq_pos(n, i) <= lvl else 0.06


def _f_energy_floor(a, t, i, n, p):
    """地板跟随：最低亮度随能量抬升（弱音不灭、强音不爆）。"""
    floor = _clamp01(_f(p.get("floor", 0.15)))
    return _clamp01(floor + (1.0 - floor) * a.rms)


def _f_silence_blackout(a, t, i, n, p):
    """静音检测：有声即亮（演示"真音频才有输出"的最小效果）。"""
    return 1.0 if a.ok and a.rms > 0.02 else 0.0


# ── 中级族（16）：多频段 / 时序网格 / 谱心 ──────────────────────────────────

def _f_band_split_zone(a, t, i, n, p):
    """三段分频：把设备序列按位置切成低/中/高三个区，各区由对应频段驱动。"""
    pos = _seq_pos(n, i)
    v = a.low if pos < 1 / 3 else (a.mid if pos < 2 / 3 else a.high)
    return _clamp01(v * 1.4)


def _f_band_split_interleave(a, t, i, n, p):
    """三段分频·交错：设备按下标取模分配到低/中/高（棋盘式）。"""
    v = (a.low, a.mid, a.high)[i % 3]
    return _clamp01(v * 1.4)


def _f_band_8_scroll(a, t, i, n, p):
    """八段频谱滚动：8 个频段轮转铺满设备序列。"""
    if not a.bands:
        return 0.0
    v = a.bands[i % len(a.bands)]
    return _clamp01(v * 1.6)


def _f_band_8_bars(a, t, i, n, p):
    """八段柱状图：每个频段在序列上占一段，柱高随能量。

    ★ R173 修复：设备数 < 8（频段数）时 `seg = n // 8 == 0` → `max(1, 0) == 1`，
      每段只有 1 台设备，`inner = (i % 1) / max(1, 1 - 1) = 0 / 1 = 0` 恒为 0，
      对任意非零频段都恒 `0 <= level` → 恒定满亮，与音频完全脱钩（真实音频/静音
      /任意两首歌输出逐帧相同）。段宽为 1 时没有"段内位置"可言，柱高本身就该直接
      = 频段电平（模拟量），而不是"段内阈值"二值图案；段宽 ≥2（n≥16）时原图案不变。
    """
    if not a.bands:
        return 0.0
    seg = max(1, n // len(a.bands))
    bi = min(len(a.bands) - 1, i // seg)
    level = _clamp01(a.bands[bi] * 1.5)
    if seg <= 1:
        return level
    inner = (i % seg) / max(1, seg - 1)
    return 1.0 if inner <= level else 0.05


def _f_band_centroid_sweep(a, t, i, n, p):
    """谱心扫描：谱心越高，亮区越向序列高位移动。"""
    c = _clamp01(a.centroid / 8000.0)
    return _clamp01(math.exp(-abs(_seq_pos(n, i) - c) * 4.0) * (0.4 + a.rms * 0.8))


def _f_beat_chase_sequential(a, t, i, n, p):
    """节拍追逐：每拍把亮点沿序列推进一格。"""
    per = a.beat_period()
    k = int(t / per) % max(1, n) if per > 0 else 0
    dist = abs(i - k)
    return _clamp01(math.exp(-dist / 1.6) * (0.45 + 0.55 * a.beat_decay(t, 0.25)))


def _f_beat_pingpong(a, t, i, n, p):
    """节拍往返：亮点在序列上往返弹跳，每拍换向。"""
    if n <= 1:
        return a.beat_decay(t, 0.3)
    k = int(t / a.beat_period()) if a.beat_period() > 0 else 0
    span = max(1, 2 * (n - 1))
    xi = k % span
    target = xi if xi < n else span - xi
    return _clamp01(math.exp(-abs(i - target) / 1.4) * (0.4 + 0.6 * a.beat_decay(t, 0.3)))


def _f_beat_euclid_ring(a, t, i, n, p):
    """欧几里得节奏环：脉冲按图案分布到设备上。"""
    pulses = max(1, int(_f(p.get("pulses", 5))))
    steps = max(2, min(32, int(_f(p.get("steps", 16)))))
    pat = _euclid(pulses, steps)
    per = a.beat_period()
    half = per / 2.0
    idx = int(t / half) % len(pat) if half > 0 else 0
    on = pat[(idx + i) % len(pat)]
    ph = ((t / half) % 1.0) if half > 0 else 0.0
    return math.exp(-ph / 0.3) if on else 0.0


def _f_bpm_sync_wave(a, t, i, n, p):
    """BPM 锁定波：波速恰好 = 每拍推进一个设备（音频再快/再慢都对齐）。"""
    per = a.beat_period()
    phase = (t / per) if per > 0 else 0.0
    return _clamp01(0.5 + 0.5 * math.sin((phase - _seq_pos(n, i)) * math.tau))


def _f_bpm_sync_scale(a, t, i, n, p):
    """BPM 锁定音阶：设备映射到音阶级数，节拍推进。"""
    per = a.beat_period()
    step = int(t / per) % max(1, n) if per > 0 else 0
    pos = (step + i) % max(1, n)
    return _clamp01(0.15 + 0.85 * (_seq_pos(n, pos) ** 0.6))


def _f_onset_ripple(a, t, i, n, p):
    """起音涟漪：每个瞬态从序列一端向外扩散一圈。"""
    if a.onset < 0.05:
        return 0.0
    per = a.beat_period()
    ph = (t % per) / per if per > 0 else 0.0
    radius = ph * 1.6
    return _clamp01(a.onset * math.exp(-abs(_seq_pos(n, i) - radius) * 5.0))


def _f_transient_spark(a, t, i, n, p):
    """瞬态火花：确定性伪随机地撒点，只在起音时点亮。"""
    if a.onset < _f(p.get("sensitivity", 0.25)):
        return 0.0
    x = (i * 2654435761) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    r = (x & 0xFFFF) / 65535.0
    return 1.0 if r < 0.35 + a.onset * 0.4 else 0.0


def _f_energy_contour_shape(a, t, i, n, p):
    """能量轮廓塑形：把 RMS 映射到一条 S 曲线再铺满序列。"""
    x = _clamp01(a.rms * 1.2)
    e = _smoothstep(0.05, 0.95, x)
    return _clamp01(e * (0.6 + 0.4 * math.cos(_seq_pos(n, i) * math.pi)))


def _f_loudness_zone(a, t, i, n, p):
    """响度分区：按响度把序列分成"亮区/暗区"，边界随响度滑动。"""
    edge = _clamp01(a.rms * 1.3)
    return 1.0 if _seq_pos(n, i) < edge else 0.08


def _f_crest_factor(a, t, i, n, p):
    """峰均比：峰值与均值差距大（打击乐）时整体更亮。"""
    crest = _clamp01((a.peak - a.rms) * 2.5)
    return _clamp01(0.25 + crest * 0.75)


def _f_spectral_tilt(a, t, i, n, p):
    """频谱倾斜：高频占比高 → 亮端亮；低频占比高 → 暗端亮。"""
    tot = max(1e-6, a.low + a.mid + a.high)
    tilt = a.high / tot
    want = tilt if _seq_pos(n, i) > 0.5 else (1.0 - tilt)
    return _clamp01(want * (0.5 + a.rms * 0.8))


# ── 高级族（14）：频谱形态 / 音高 / 和声 ────────────────────────────────────

def _f_pitch_height(a, t, i, n, p):
    """音高高度：音越高，亮的设备位置越靠后。"""
    pn = a.pitch_norm()
    return _clamp01(math.exp(-abs(_seq_pos(n, i) - pn) * 3.5) * (0.4 + a.pitch_conf * 0.6))


def _f_pitch_octave_fold(a, t, i, n, p):
    """八度折叠：音高按八度折叠成 12 级，铺成音阶条。"""
    if a.pitch < 0:
        return 0.0
    deg = int(round(a.pitch)) % 12
    return 1.0 if (i % 12) == deg else 0.12


def _f_pitch_glide_trail(a, t, i, n, p):
    """音高滑行拖尾：亮区跟随音高滑动，后面拖一条渐弱尾。"""
    pn = a.pitch_norm()
    d = _seq_pos(n, i) - pn
    return _clamp01(math.exp(-max(0.0, d) * 6.0) * a.pitch_conf) if d >= 0 else 0.0


def _f_pitch_confidence_gate(a, t, i, n, p):
    """音高置信闸门：只有"歌声明亮"（置信高）时才亮 —— 纯鼓段不亮。"""
    thr = _f(p.get("threshold", 0.35))
    return _smoothstep(thr, min(1.0, thr + 0.3), a.pitch_conf) * (0.35 + a.rms * 0.65)


def _f_chord_root_zone(a, t, i, n, p):
    """和弦根音分区：12 个根音映射到序列 12 个区，当前根音区亮。"""
    root = a.chord_tone()
    if root < 0:
        return 0.0
    zone = int(_seq_pos(n, i) * 12.0) % 12
    return 1.0 if zone == root else 0.1


def _f_chord_quality_tint(a, t, i, n, p):
    """和弦色彩：大调/小调分别驱动序列的偶数/奇数位（可听出和声变化）。"""
    q = str(a.chord_quality or "")
    odd = (i % 2) == 1
    want = odd if q == "minor" else (not odd)
    return _clamp01((1.0 if want else 0.15) * (0.45 + a.rms * 0.7))


def _f_chord_change_accent(a, t, i, n, p):
    """和弦变化强调：根音/性质一变，全组闪一下（检测"换和弦"这一事件）。"""
    st = p.setdefault("_chord_state", {})
    cur = (a.chord_tone(), a.chord_quality)
    prev = st.get("prev")
    st["prev"] = cur
    changed = prev is not None and prev != cur
    base = 0.25 + a.rms * 0.5
    return _clamp01(1.0 if changed else base)


def _f_harmonic_tension(a, t, i, n, p):
    """和声张力：音高离和弦根音越远（半音越怪）越亮 —— "紧张感可视化"。"""
    if a.pitch < 0 or a.chord_tone() < 0:
        return 0.2
    dist = min((int(round(a.pitch)) - a.chord_tone()) % 12,
               (a.chord_tone() - int(round(a.pitch))) % 12)
    return _clamp01(dist / 6.0 * (0.4 + a.rms * 0.7))


def _f_spectral_centroid_band(a, t, i, n, p):
    """谱心分档：谱心落在低/中/高三档，分别点亮序列三段。"""
    c = a.centroid
    zone = 0 if c < 1500.0 else (1 if c < 4000.0 else 2)
    seg = min(2, int(_seq_pos(n, i) * 3.0))
    return _clamp01((1.0 if seg == zone else 0.12) * (0.5 + a.rms * 0.6))


def _f_spectral_flux_sweep(a, t, i, n, p):
    """频谱通量扫描：通量（变化率，用起音近似）高时亮带快速扫过。"""
    ph = (t * (0.6 + a.bpm / 240.0)) % 1.0
    band = math.exp(-abs(_seq_pos(n, i) - ph) * 5.0)
    return _clamp01(band * (0.35 + a.onset * 0.9))


def _f_multiband_mosaic(a, t, i, n, p):
    """多频段马赛克：把 8 频段按二维棋盘铺到序列，各格独立。"""
    if not a.bands:
        return 0.0
    v = a.bands[(i * 3) % len(a.bands)]
    return _clamp01(v * 1.5 + 0.05)


def _f_dynamic_range_map(a, t, i, n, p):
    """动态范围映射：弱→强映射到序列从后到前逐段点亮（音量条反着长）。"""
    lvl = _clamp01(a.rms * 1.2)
    return 1.0 if _seq_pos(n, i) >= (1.0 - lvl) else 0.06


def _f_energy_histogram(a, t, i, n, p):
    """能量直方图：给每台设备一个固定"刻度"，只有当前能量扫过它的刻度才亮。"""
    idx = int(a.rms * max(1, n + 2))
    return 1.0 if i <= idx else 0.05


def _f_low_mid_high_cascade(a, t, i, n, p):
    """三段级联：低→中→高依次点亮序列的三段（低频先动，高频跟上）。"""
    pos = _seq_pos(n, i)
    if pos < 1 / 3:
        return _clamp01(a.low * 1.4)
    if pos < 2 / 3:
        return _clamp01(a.mid * 1.4 * _clamp01(a.low * 2.0))
    return _clamp01(a.high * 1.4 * _clamp01(a.mid * 2.0))


# ── 专家族（8）：深层特征组合 ──────────────────────────────────────────────

def _f_melody_contour_track(a, t, i, n, p):
    """旋律轮廓跟踪：以音高为"高度"、以时间为"横轴"的滚动轨迹。

    把最近 16 帧的音高写进环形缓冲，序列映射成时间轴，设备亮度 = 该时刻音高高度。
    """
    buf = p["_audio_runtime"].melody
    if not buf:
        return 0.0
    # 设备 i 映射到缓冲里的一格（0=最新）
    idx = int(_seq_pos(n, i) * (len(buf) - 1))
    return _clamp01(buf[len(buf) - 1 - idx] * (0.45 + a.rms * 0.7))


def _f_harmony_stack(a, t, i, n, p):
    """和声堆叠：根音 + 三度 + 五度在序列上叠成一条"和弦柱"。"""
    root = a.chord_tone()
    if root < 0:
        return 0.1
    minor = str(a.chord_quality or "") == "minor"
    tones = {root % 12, (root + (3 if minor else 4)) % 12, (root + 7) % 12}
    deg = i % 12
    return _clamp01((1.0 if deg in tones else 0.1) * (0.5 + a.rms * 0.7))


def _f_transient_density_heat(a, t, i, n, p):
    """瞬态密度热图：起音越密，点亮的设备越多（密集鼓段铺满全场）。"""
    return 1.0 if _seq_pos(n, i) < p["_audio_runtime"].onset_heat else 0.04


def _f_beat_phase_spiral(a, t, i, n, p):
    """拍相位螺旋：拍相位同时驱动"空间位置"与"亮度"，形成旋转感。"""
    per = a.beat_period()
    ph = (t % per) / per if per > 0 else 0.0
    pos = (_seq_pos(n, i) + ph) % 1.0
    return _clamp01(math.exp(-min(pos, 1.0 - pos) * 5.0) * (0.4 + a.rms * 0.8))


def _f_energy_slope_detector(a, t, i, n, p):
    """能量斜率检测：上升段（渐强）亮、下降段暗 —— 区分"起"与"落"。"""
    prev = p.setdefault("_prev_rms", [a.rms])
    d = a.rms - prev[0]
    prev[0] = a.rms
    rising = _clamp01(0.5 + d * 12.0)
    return _clamp01(rising * (0.4 + a.rms * 0.7))


def _f_pitch_stability_hold(a, t, i, n, p):
    """音高稳定度：音高稳定（长音）时保持亮，抖动的花腔则闪。"""
    d = p["_audio_runtime"].pitch_delta
    stable = 1.0 - _clamp01(d / 6.0)
    return _clamp01(stable * a.pitch_conf * (0.35 + a.rms * 0.75))


def _f_full_spectrum_waterfall(a, t, i, n, p):
    """全谱瀑布：时间→序列轴，8 频段→亮度，形成随音乐滚动的瀑布图。"""
    step = int(t * max(2.0, a.bpm / 30.0))
    bi = (step + i * 3) % max(1, len(a.bands) if a.bands else 1)
    v = a.bands[bi] if a.bands else a.rms
    return _clamp01(v * 1.5 * (0.5 + a.rms * 0.8))


def _f_audio_signature_pulse(a, t, i, n, p):
    """音频指纹脉冲：把 low/mid/high/onset 四位量组合成确定性图案，随音乐变化。"""
    x = int(a.low * 255) * 2654435761 ^ int(a.mid * 255) * 40503 ^ int(a.high * 255) * 2246822519
    x &= 0xFFFFFFFF
    r = ((x ^ (x >> 15)) & 0xFFFF) / 65535.0
    gate = _clamp01(a.rms * 1.4)
    return 1.0 if (r < gate and (i + int(t * 10)) % 2 == 0) else 0.06
