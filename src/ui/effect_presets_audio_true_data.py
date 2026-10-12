# -*- coding: utf-8 -*-
"""【通用/音频】真音频效果的**预设数据表**（与内核分离，守 500 行单文件上限）。

内核与音频量解析见 `effect_presets_audio_true.py`；本文件只描述
"有哪些效果、叫什么、说明、图标、档位、默认参数"。
见 `effect_presets_audio_true` 模块 docstring 的完整设计说明与用户需求原文。
"""
from __future__ import annotations


def build_true_audio_presets():
    """返回 `[(variant, name, desc, icon, tier, params_dict), ...]`（供注册处消费）。

    刻意用"数据表 + 统一参数构造"的形态（与 curated 族一致），使新增效果只需加一行。
    """
    out = []
    add = out.append

    def _p(**ov):
        base = {"rate": 1.0, "spread": 0.0, "lo": 0, "hi": 255,
                "threshold": 0.12, "sensitivity": 0.3, "decay": 0.2,
                "duty": 0.35, "floor": 0.15, "pulses": 5, "steps": 16}
        base.update(ov)
        return base

    # ── 基础（14）：一眼看懂、现场最常用 ──────────────────────────────────
    for v, nm, ds, ic, t, ov in [
        ("rms_gate", "能量闸门", "整体能量过阈值才亮，亮度随能量连续变化", "activity", "基础", dict(threshold=0.12)),
        ("rms_breath", "能量呼吸", "能量越高呼吸越快，弱音缓慢起伏", "wind", "基础", {}),
        ("rms_gradient", "能量坡道", "能量决定亮度坡的陡峭度，强音坡更陡", "trending-up", "基础", {}),
        ("peak_flash", "峰值闪光", "取峰值与能量较大者，配拍衰减，鼓点突出", "flash", "基础", {}),
        ("band_low", "低频驱动", "大鼓/贝斯驱动全场，最常用的重低音反应", "speaker", "基础", {}),
        ("band_mid", "中频驱动", "人声/主旋律驱动全场", "microphone", "基础", {}),
        ("band_high", "高频驱动", "镲片/高频'嘶'声驱动全场，细节敏感", "sparkles", "基础", {}),
        ("beat_pulse", "节拍脉冲", "每拍全组闪一次，衰减可调", "heartbeat", "基础", dict(decay=0.2)),
        ("beat_strobe", "节拍频闪", "每拍前段亮、后段灭的硬切频闪", "flash", "基础", dict(duty=0.35)),
        ("beat_offbeat", "反拍律动", "只在拍与拍之间亮，与正拍互补", "music", "基础", {}),
        ("onset_accent", "起音强调", "只对鼓点/拨弦等瞬态响应，持续音不亮", "arrow-up", "基础", dict(sensitivity=0.3)),
        ("energy_meter", "电平表", "设备序列 = 电平条刻度，随响度伸长", "chart-bar", "基础", {}),
        ("energy_floor", "地板跟随", "最低亮度随能量抬升，弱音不灭强音不爆", "arrow-down", "基础", dict(floor=0.15)),
        ("silence_blackout", "有声即亮", "有声就全亮、静音就全灭（验证真音频接入的最小效果）", "toggle", "基础", {}),
    ]:
        add((v, nm, ds, ic, t, _p(**ov)))

    # ── 中级（16）：分频 / 时序网格 / 谱心 ────────────────────────────────
    for v, nm, ds, ic, t, ov in [
        ("band_split_zone", "三段分频", "设备分三段，分别由低/中/高频驱动", "equalizer", "中级", {}),
        ("band_split_interleave", "三段交错", "设备按下标轮流分配到低/中/高频", "grid-dots", "中级", {}),
        ("band_8_scroll", "八段滚动", "8 个频段沿设备序列滚动铺开", "equalizer", "中级", {}),
        ("band_8_bars", "八段柱状", "8 个频段各占一段序列，段内柱高随能量", "chart-bar", "中级", {}),
        ("band_centroid_sweep", "谱心扫描", "声音'亮度中心'越高，亮区越往后移", "sun", "中级", {}),
        ("beat_chase_sequential", "节拍追逐", "每拍亮点沿序列推进一格", "arrows-right", "中级", {}),
        ("beat_pingpong", "节拍往返", "亮点在序列上往返弹跳", "arrows-left-right", "中级", {}),
        ("beat_euclid_ring", "欧几里得节奏", "脉冲按欧氏图案分布到设备，节奏型丰富", "circle-dots", "中级", dict(pulses=5, steps=16)),
        ("bpm_sync_wave", "BPM锁定波", "波速恰好每拍推进一台，快慢曲都自动对齐", "sine-wave", "中级", {}),
        ("bpm_sync_scale", "BPM音阶", "设备映射到音阶级数，按拍上行", "music", "中级", {}),
        ("onset_ripple", "起音涟漪", "每个瞬态从序列一端向外扩散一圈", "ripple", "中级", {}),
        ("transient_spark", "瞬态火花", "只在起音瞬间确定性随机撒点", "sparkles", "中级", dict(sensitivity=0.25)),
        ("energy_contour_shape", "能量轮廓塑形", "能量走 S 曲线塑形后沿序列铺开", "wave-sine", "中级", {}),
        ("loudness_zone", "响度分区", "按响度把序列切成亮区/暗区，边界随响度滑动", "chart-bar", "中级", {}),
        ("crest_factor", "峰均比驱动", "打击乐（峰均比大）整体更亮，持续音偏暗", "activity", "中级", {}),
        ("spectral_tilt", "频谱倾斜", "高频多则亮端亮、低频多则暗端亮", "sliders", "中级", {}),
    ]:
        add((v, nm, ds, ic, t, _p(**ov)))

    # ── 高级（14）：音高 / 和声 / 频谱形态 ────────────────────────────────
    for v, nm, ds, ic, t, ov in [
        ("pitch_height", "音高高度", "音越高亮的设备越靠后（音高→空间高度）", "stairs-up", "高级", {}),
        ("pitch_octave_fold", "八度折叠", "音高按八度折叠成 12 级音阶条", "piano", "高级", {}),
        ("pitch_glide_trail", "音高拖尾", "亮区跟随音高滑动，后方拖一条渐弱尾", "arrow-right", "高级", {}),
        ("pitch_confidence_gate", "歌声明亮", "只在人声/旋律清晰时亮，纯鼓段不亮", "microphone", "高级", dict(threshold=0.35)),
        ("chord_root_zone", "和弦根音分区", "12 个根音映射到序列 12 区，当前和弦区亮", "circle-half", "高级", {}),
        ("chord_quality_tint", "大小调色彩", "大调/小调分别驱动序列的偶/奇位，和声变化可见", "contrast", "高级", {}),
        ("chord_change_accent", "换和弦强调", "每次换和弦全组闪一下（事件检测）", "refresh", "高级", {}),
        ("harmonic_tension", "和声张力", "音高离和弦越远（越不和协）越亮——紧张感可视化", "alert-triangle", "高级", {}),
        ("spectral_centroid_band", "谱心分档", "谱心落在低/中/高三档，分别点亮序列三段", "sun", "高级", {}),
        ("spectral_flux_sweep", "频谱通量扫描", "频谱变化越快，亮带扫过越急", "activity", "高级", {}),
        ("multiband_mosaic", "多频段马赛克", "8 频段按二维棋盘铺到序列，各格独立闪烁", "grid-dots", "高级", {}),
        ("dynamic_range_map", "动态范围反转", "响度越大亮区越往前扩（音量条反着长）", "arrows-left", "高级", {}),
        ("energy_histogram", "能量直方图", "每台设备一个刻度，能量扫到才亮", "chart-bar", "高级", {}),
        ("low_mid_high_cascade", "三段级联", "低→中→高依次点亮，低频先动高频跟上", "layers", "高级", {}),
    ]:
        add((v, nm, ds, ic, t, _p(**ov)))

    # ── 专家（8）：深层特征组合 ──────────────────────────────────────────
    for v, nm, ds, ic, t, ov in [
        ("melody_contour_track", "旋律轮廓轨迹", "滚动记录音高走向并映射到序列，'看见'旋律线", "wave-sine", "专家", {}),
        ("harmony_stack", "和声堆叠", "根音+三度+五度在序列上叠成和弦柱", "stack", "专家", {}),
        ("transient_density_heat", "瞬态密度热图", "起音越密点亮的设备越多（密集鼓段铺满）", "flame", "专家", {}),
        ("beat_phase_spiral", "拍相位螺旋", "拍相位同时驱动位置与亮度，形成旋转感", "rotate", "专家", {}),
        ("energy_slope_detector", "渐强检测", "上升段亮、下降段暗，区分'起'与'落'", "trending-up", "专家", {}),
        ("pitch_stability_hold", "长音保持", "音高稳定（长音）时保持亮，花腔抖音则闪", "minus", "专家", {}),
        ("full_spectrum_waterfall", "全谱瀑布", "时间→序列轴、8 频段→亮度，随音乐滚动", "wave-saw-tool", "专家", {}),
        ("audio_signature_pulse", "音频指纹脉冲", "多频段组合成确定性图案，随音乐变化撒点", "fingerprint", "专家", {}),
    ]:
        add((v, nm, ds, ic, t, _p(**ov)))

    return out


def true_audio_preset_ids():
    """本族全部预设 id（`atx_<variant>`）。"""
    return [f"atx_{v}" for v, *_ in build_true_audio_presets()]
