# -*- coding: utf-8 -*-
"""音频调制效果库（R182 重构 + R183 M4 扩容）—— 85 种通用音频效果预设的唯一数据源。

设计（用户规格 2026-09-29 二次迭代）：
  · 像 effect 内置音频效果一样提供**大量预设**（85 种，7 组分类），
    每种效果 = 一组语义化参数（信号源/波形/频率/深度/起应/阈值/相位…），
    任何效果都可以调制**任意通道类型**；
  · 效果卡（kind="rule"）不再绑定单一通道类型，而是携带：
      effect（效果 key）+ params（该效果的完整参数表）
      targets（目标通道类型列表——按 clip 含有的通道勾选）
  · 卡内嵌控件 = 效果关键参数滑杆（各效果声明自己的 primary 参数子集）。

效果参数模型（全部 0..1 归一，运行期映射到真实量纲）：
    signal   信号源   beat_strength / bpm_phase / band_low / band_mid / band_high
                     / band_ratio / onset / energy / section
    wave     包络波形 WAVE_FUNCS 全集 + ""（恒定）
    freq     波形频率（每时基单位周期数，时基见 freq_unit；缺省=每拍）
    freq_unit 时基     beat(缺省)/hz/bars/dotted/triplet（R183 M2）
    depth    调制深度（0..1）——与全局 amount 相乘
    attack   起应（秒，0..2；一阶跟随上升，R183 M2 真 DSP）
    release  落应（秒，0..2；一阶跟随下降，R183 M2 真 DSP）
    phase    相位偏移（0..1）
    invert   反相（0/1）
    threshold 阈值门（0..1 开阈值；低于→0；配 threshold_close/hold_ms 迟滞）
    smoothing 平滑（0..1；EMA τ 口径，60fps 等价旧 1-sm 系数）
    random_mode Random 波形模式 ""/discrete/slew（R183 M2）

编译产物 schema（state["rules"] 行）向后兼容旧档：
    {"ch_type": <首个目标>,          # 旧字段保留（旧消费方/指纹读取）
     "ch_types": [全部目标],          # 新：多目标
     "effect": <key>, "params": {...}, "source_ids": [...]}
`ch_type` = ch_types[0] 使旧单目标读取路径（如老指纹、旧 scope_index）
继续工作；driver 侧按 `ch_types` 展开。
"""
from __future__ import annotations

from typing import Dict, List, Tuple


#: 信号源目录（key → 中文名；与 engine.audio_mod_draw_compile.source_value 同口径）
SIGNAL_LABELS: Dict[str, str] = {
    "beat_strength": "节拍强度",
    "bpm_phase": "BPM 相位",
    "band_low": "低频能量",
    "band_mid": "中频能量",
    "band_high": "高频能量",
    "band_ratio": "频段能量比",
    "onset": "瞬态冲击",
    "energy": "整体能量",
    "section": "段落位置",
}

#: 参数规格：key → (中文名, 默认值, 说明)
PARAM_SPECS: Dict[str, tuple] = {
    "signal":    ("信号源", "beat_strength", "驱动本效果的音频特征"),
    "wave":      ("波形", "Sine", "包络波形（恒定=只用信号值）"),
    "freq":      ("频率", 2.0, "每单位时基的周期数（波形快慢）"),
    "depth":     ("深度", 1.0, "调制深度（0=无效果）"),
    "attack":    ("起应", 0.05, "效果内包络起音（秒；一阶跟随上升）"),
    "release":   ("落应", 0.2, "效果内包络落音（秒；一阶跟随下降）"),
    "phase":     ("相位", 0.0, "波形相位偏移（0..1 周期）"),
    "invert":    ("反相", 0.0, "1=信号取反（暗击/反向呼吸）"),
    "threshold": ("阈值", 0.0, "开门阈值（信号达到才开；迟滞门开阈值）"),
    "smoothing": ("平滑", 0.0, "时间平滑（EMA τ 口径；0=不额外平滑）"),
    # R183 M2 扩容（缺省=旧档行为逐位不变）
    "freq_unit":       ("时基", "beat", "频率单位（拍/Hz/小节/附点/三连音）"),
    "random_mode":     ("随机型", "", "Random 波形模式（连续/离散/滑移）"),
    "threshold_close": ("关阈值", 0.0, "迟滞门关阈值（低于开阈值；0=单阈值）"),
    "hold_ms":         ("保持", 0.0, "门最短保持（毫秒；0=不保持）"),
    # R183 3.4b UI 接线（引擎读 state["spread_deg"]；卡片值经 compile_graph 聚合）
    "spread_deg":      ("相位展开", 0.0, "多灯相位展开（0=同相；360=按灯铺满一圈）"),
}

#: 时基单位 → 中文（R183 M2；命名对照 Timebase）
FREQ_UNIT_LABELS: Dict[str, str] = {
    "beat": "拍", "hz": "Hz", "bars": "小节", "dotted": "附点", "triplet": "三连音",
    "sixteenth": "十六分",
}

#: Random 波形模式 → 中文（R183 M2；对照 Random discrete/slew）
RANDOM_MODE_LABELS: Dict[str, str] = {
    "": "连续", "discrete": "离散", "slew": "滑移",
}

#: 效果目录：key → (显示名, 分组, 图标, 一句话说明, 参数覆盖 dict)
#: 未列出的参数取 PARAM_SPECS 默认。分组顺序见 EFFECT_GROUPS。
EFFECT_CATALOG: Dict[str, tuple] = {
    # ── 律动 Groove（跟拍类）────────────────────────────────────────────
    "pulse_beat":     ("节拍脉冲", "律动", "activity", "每拍一个圆润脉冲，通用跟拍", {"signal": "beat_strength", "wave": "Sine", "freq": 1.0}),
    "stomp":          ("重击踏步", "律动", "activity", "鼓点重击，起应极快收得干脆", {"signal": "beat_strength", "wave": "Expo", "attack": 0.0, "release": 0.08}),
    "hard_gate":      ("硬门切分", "律动", "activity", "方波硬门，节奏顿挫", {"signal": "beat_strength", "wave": "Square"}),
    "heartbeat":      ("心跳", "律动", "heartbeat", "双跳心跳节律", {"signal": "beat_strength", "wave": "Bounce", "freq": 2.0}),
    "groove_swing":   ("摇摆律动", "律动", "activity", "相位偏移的摇摆感", {"signal": "bpm_phase", "wave": "Sine", "phase": 0.25}),
    "offbeat":        ("反拍", "律动", "activity", "反相跟拍（弱拍强）", {"signal": "beat_strength", "invert": 1.0}),
    "double_time":    ("双倍速", "律动", "activity", "每拍双脉冲", {"signal": "beat_strength", "wave": "Sine", "freq": 2.0}),
    "half_time":      ("半速浪潮", "律动", "activity", "两拍一循环", {"signal": "beat_strength", "wave": "Sine", "freq": 0.5}),
    # ── M4 扩容（R183 3.4a；波形/时基语义对照 手册 · 时基对照 ）──
    "shark_beat":     ("鲨齿律动", "律动", "activity", "慢起陡升、句末急落的齿形重音（Shark Tooth）", {"signal": "beat_strength", "wave": "Expo", "freq": 1.0, "attack": 0.0, "release": 0.4}),
    "stutter_gate":   ("断续门", "律动", "wave-square", "高速方波断奏，迟滞门防抖", {"signal": "beat_strength", "wave": "Square", "freq": 4.0, "threshold": 0.15, "threshold_close": 0.05, "hold_ms": 30.0, "attack": 0.0, "release": 0.03}),
    "triplet_flow":   ("三连音流", "律动", "metronome", "每拍三个脉冲的三连音律动", {"signal": "bpm_phase", "wave": "Sine", "freq": 3.0}),
    "dotted_push":    ("附点推进", "律动", "clock", "附点时基（1.5 拍一循环）的推进", {"signal": "bpm_phase", "wave": "Sine", "freq": 1.0, "freq_unit": "dotted"}),
    "bounce_groove":  ("弹跳律动", "律动", "guitar-pick", "弹跳曲线的跟拍律动", {"signal": "beat_strength", "wave": "Bounce", "freq": 1.0, "attack": 0.0}),
    "swing_8th":      ("摇摆八分", "律动", "arrows-shuffle", "重音后移的摇摆八分（Shuffle 语义）", {"signal": "beat_strength", "wave": "Expo", "freq": 2.0, "phase": 0.15, "attack": 0.0, "release": 0.1}),

    # ── 呼吸 Breath（缓动类）────────────────────────────────────────────
    "breath_slow":    ("缓慢呼吸", "呼吸", "wind", "4 拍一循环的慢呼吸", {"signal": "bpm_phase", "wave": "Sine", "freq": 0.25, "attack": 0.3, "release": 0.5}),
    "breath_deep":    ("深呼吸", "呼吸", "wind", "幅度 deepened 的呼吸（阈值压底）", {"signal": "bpm_phase", "wave": "Sine", "threshold": 0.15, "attack": 0.4}),
    "swell":          ("涌浪", "呼吸", "wave-saw-tool", "低频驱动的长涌起落", {"signal": "band_low", "wave": "Sine", "freq": 1.0, "attack": 0.5, "release": 0.8}),
    "tide":           ("潮汐", "呼吸", "wave-saw-tool", "极慢的低频潮汐", {"signal": "band_low", "wave": "Cosine", "freq": 1.0, "attack": 0.8, "release": 1.0}),
    "float_up":       ("上浮", "呼吸", "arrow-up", "持续爬升的整段能量", {"signal": "energy", "wave": "", "attack": 1.0}),
    "sink_down":      ("下沉", "呼吸", "arrow-down", "反相能量，强段压暗", {"signal": "energy", "invert": 1.0, "attack": 0.6}),
    "breathe_fast":   ("快速呼吸", "呼吸", "wind", "每拍两次的急促呼吸", {"signal": "bpm_phase", "wave": "Sine", "freq": 2.0}),
    # ── M4 扩容 ─────────────────────────────────────────────────────────
    "wander_breath":  ("漂移呼吸", "呼吸", "wind", "缓慢随机漂移（Wander 语义，滑移随机）", {"signal": "bpm_phase", "wave": "Random", "freq": 0.5, "random_mode": "slew", "smoothing": 0.6, "attack": 0.2, "release": 0.4}),
    "step_breath":    ("阶梯呼吸", "呼吸", "step-into", "整值保持的阶梯呼吸（S&H 语义）", {"signal": "bpm_phase", "wave": "Random", "freq": 0.5, "random_mode": "discrete", "smoothing": 0.3}),
    "hz_breath":      ("赫兹呼吸", "呼吸", "heartbeat", "按秒计的慢呼吸（0.3 Hz 约 3.3 秒一循环）", {"signal": "bpm_phase", "wave": "Sine", "freq": 0.3, "freq_unit": "hz", "attack": 0.3, "release": 0.6}),
    "bar_swell":      ("小节浪潮", "呼吸", "wave-saw-tool", "按小节（4 拍）涌起的低频浪潮", {"signal": "band_low", "wave": "Sine", "freq": 1.0, "freq_unit": "bars", "attack": 0.6, "release": 0.9}),
    "exhale_breath":  ("吐息", "呼吸", "ripple", "余韵拉长的呼出感（余弦缓落）", {"signal": "bpm_phase", "wave": "Cosine", "freq": 0.5, "attack": 0.4, "release": 0.7}),
    "night_tide":     ("夜潮", "呼吸", "wind", "极慢的低频潮汐（4 拍一循环，强平滑）", {"signal": "band_low", "wave": "Cosine", "freq": 0.25, "attack": 1.0, "release": 1.2, "smoothing": 0.3}),

    # ── 波形 Wave（连续波形类）──────────────────────────────────────────
    "sine_wave":      ("正弦波", "波形", "wave-sine", "标准正弦调制", {"signal": "bpm_phase", "wave": "Sine"}),
    "saw_rise":       ("锯齿上升", "波形", "wave-saw-tool", "线性上升回落的锯齿", {"signal": "bpm_phase", "wave": "Sawtooth", "freq": 1.0}),
    "triangle_wave":  ("三角波", "波形", "wave-sine", "对称往返的三角波", {"signal": "bpm_phase", "wave": "Triangle"}),
    "square_wave":    ("方波切换", "波形", "wave-square", "两态切换", {"signal": "bpm_phase", "wave": "Square", "freq": 1.0}),
    "expo_ramp":      ("指数爬升", "波形", "trending-up", "指数曲线的爬升", {"signal": "bpm_phase", "wave": "Expo", "freq": 1.0}),
    "bounce_wave":    ("弹跳", "波形", "wave-sine", "弹跳曲线", {"signal": "bpm_phase", "wave": "Bounce"}),
    "random_wave":    ("随机", "波形", "dice", "随机值调制（稳播确定）", {"signal": "bpm_phase", "wave": "Random", "freq": 2.0}),
    "cosine_wave":    ("余弦波", "波形", "wave-sine", "相位偏移 90° 的波形", {"signal": "bpm_phase", "wave": "Cosine"}),
    # ── M4 扩容（波形表命名对照）────────────────────────────────
    "shark_tooth":    ("鲨齿波", "波形", "wave-sine", "慢起陡升、末端急落的不对称齿形（Shark Tooth）", {"signal": "bpm_phase", "wave": "Expo", "freq": 1.0, "attack": 0.0, "release": 0.3}),
    "saw_down":       ("锯齿下降", "波形", "arrow-down", "反相锯齿的下降扫动（Saw Down）", {"signal": "bpm_phase", "wave": "Sawtooth", "invert": 1.0, "freq": 1.0}),
    "dotted_saw":     ("附点锯齿", "波形", "wave-saw-tool", "附点时基（1.5 拍）的锯齿上升", {"signal": "bpm_phase", "wave": "Sawtooth", "freq": 1.0, "freq_unit": "dotted"}),
    "triangle_8":     ("三角八分", "波形", "wave-sine", "每拍两个往返的三角波（Triangle 8）", {"signal": "bpm_phase", "wave": "Triangle", "freq": 2.0}),
    "triangle_16":    ("三角十六分", "波形", "wave-sine", "每拍四个往返的三角波（Triangle 16）", {"signal": "bpm_phase", "wave": "Triangle", "freq": 4.0}),
    "rectangle_wide": ("宽矩形", "波形", "wave-square", "相位偏移的宽占空比矩形（Rectangle）", {"signal": "bpm_phase", "wave": "Square", "freq": 0.5, "phase": 0.25}),
    "step_seq":       ("阶梯序列", "波形", "list-numbers", "整值保持的随机阶梯（Steps 量化）", {"signal": "bpm_phase", "wave": "Random", "freq": 4.0, "random_mode": "discrete"}),
    "wander_wave":    ("漂移波", "波形", "dice", "缓慢随机漂移（Wander，滑移随机）", {"signal": "bpm_phase", "wave": "Random", "freq": 2.0, "random_mode": "slew"}),
    "sample_hold_wave": ("采样保持", "波形", "disc", "周期采样的随机保持（S&H）", {"signal": "bpm_phase", "wave": "Random", "freq": 2.0, "random_mode": "discrete", "smoothing": 0.15}),
    "hz_sine":        ("赫兹正弦", "波形", "wave-sine", "按赫兹计的自由正弦（1 Hz）", {"signal": "bpm_phase", "wave": "Sine", "freq": 1.0, "freq_unit": "hz"}),

    # ── 频谱 Spectrum（频段驱动类）──────────────────────────────────────
    "bass_drive":     ("低频驱动", "频谱", "wave-saw-tool", "低频能量直接驱动", {"signal": "band_low", "wave": ""}),
    "mid_flow":       ("中频流动", "频谱", "activity", "中频人声/旋律驱动", {"signal": "band_mid", "wave": ""}),
    "high_sparkle":   ("高频闪烁", "频谱", "sparkles", "镲片/高音闪烁响应", {"signal": "band_high", "wave": "", "attack": 0.0, "release": 0.1}),
    "full_spectrum":  ("全频谱", "频谱", "adjustments", "低+中+高频比混合", {"signal": "band_ratio", "wave": ""}),
    "bass_punch":     ("低频重拳", "频谱", "activity", "低频瞬态强化", {"signal": "band_low", "wave": "Expo", "attack": 0.0, "release": 0.12}),
    "treble_shimmer": ("高频微光", "频谱", "sparkles", "高频+平滑的微光闪烁", {"signal": "band_high", "wave": "Sine", "smoothing": 0.5}),
    "spectrum_balance": ("频谱平衡", "频谱", "adjustments", "频段比长期均值驱动", {"signal": "band_ratio", "smoothing": 0.7}),
    # ── M4 扩容 ─────────────────────────────────────────────────────────
    "sub_rumble":     ("次低频涌动", "频谱", "ripple", "次低频持续涌动（强平滑）", {"signal": "band_low", "wave": "Sine", "freq": 0.5, "attack": 0.3, "release": 0.6, "smoothing": 0.4}),
    "vocal_presence": ("人声突出", "频谱", "music", "中频人声/旋律的稳定跟随", {"signal": "band_mid", "wave": "", "attack": 0.05, "release": 0.25, "smoothing": 0.2, "depth": 0.8}),
    "air_band":       ("空气感", "频谱", "sparkles", "高频空气感的细腻响应", {"signal": "band_high", "wave": "", "attack": 0.01, "release": 0.15, "smoothing": 0.35}),
    "bass_ratio_gate": ("低频占比门", "频谱", "adjustments", "低频占比过阈才开（迟滞防抖）", {"signal": "band_ratio", "threshold": 0.35, "threshold_close": 0.25, "hold_ms": 120.0, "release": 0.2}),
    "spectrum_split": ("频谱切分", "频谱", "circle-dot", "频段比上方的方波切分", {"signal": "band_ratio", "wave": "Square", "freq": 2.0, "threshold": 0.4}),
    "crossover_pulse": ("分频脉冲", "频谱", "activity", "低频过阈的小节脉冲（迟滞+保持）", {"signal": "band_low", "wave": "Expo", "freq": 1.0, "freq_unit": "bars", "threshold": 0.15, "threshold_close": 0.05, "hold_ms": 60.0, "attack": 0.0, "release": 0.25}),

    # ── 冲击 Impact（瞬态类）────────────────────────────────────────────
    "transient_hit":  ("瞬态冲击", "冲击", "bolt", "瞬态检测直通", {"signal": "onset", "wave": "", "attack": 0.0, "release": 0.15}),
    "flash":          ("频闪", "冲击", "bolt", "瞬态触发满值快闪", {"signal": "onset", "wave": "Square", "attack": 0.0, "release": 0.05}),
    "thunder":        ("雷鸣", "冲击", "bolt", "低频+瞬态的重击", {"signal": "band_low", "wave": "Expo", "threshold": 0.2, "release": 0.3}),
    "trap_drop":      ("陷波落下", "冲击", "arrow-down", "瞬态反相（爆点压暗）", {"signal": "onset", "invert": 1.0, "release": 0.2}),
    "spark_burst":    ("火花迸发", "冲击", "sparkles", "高频瞬态爆点", {"signal": "band_high", "wave": "Expo", "threshold": 0.25, "attack": 0.0}),
    # ── M4 扩容 ─────────────────────────────────────────────────────────
    "clap":           ("拍手", "冲击", "sparkles", "拍手式的三连快击", {"signal": "onset", "wave": "Bounce", "freq": 3.0, "attack": 0.0, "release": 0.08, "threshold": 0.2, "hold_ms": 50.0}),
    "snare_hit":      ("军鼓", "冲击", "bolt", "军鼓瞬态的干脆命中（迟滞防抖）", {"signal": "onset", "wave": "Expo", "attack": 0.0, "release": 0.12, "threshold": 0.15, "threshold_close": 0.05}),
    "kick_lock":      ("底鼓锁定", "冲击", "heartbeat", "底鼓每击锁定亮起（保持 90ms）", {"signal": "band_low", "wave": "Expo", "threshold": 0.25, "threshold_close": 0.1, "hold_ms": 90.0, "attack": 0.0, "release": 0.2}),
    "laser_stab":     ("激光刺", "冲击", "arrow-bar-up", "高频瞬态的尖锐刺点", {"signal": "band_high", "wave": "Sawtooth", "freq": 4.0, "attack": 0.0, "release": 0.06, "threshold": 0.3}),
    "impact_hold":    ("冲击保持", "冲击", "flag-2", "冲击后长保持再缓落", {"signal": "onset", "wave": "", "threshold": 0.2, "threshold_close": 0.12, "hold_ms": 300.0, "release": 0.4}),
    "ripple_hit":     ("涟漪冲击", "冲击", "ripple", "冲击后泛开的涟漪余波", {"signal": "onset", "wave": "Sine", "freq": 1.0, "attack": 0.0, "release": 0.5, "smoothing": 0.2}),
    "double_hit":     ("双击", "冲击", "bolt", "双击式的双脉冲命中", {"signal": "onset", "wave": "Bounce", "freq": 2.0, "attack": 0.0, "release": 0.1}),

    # ── 段落 Section（结构类）──────────────────────────────────────────
    "section_follow": ("段落跟随", "段落", "route", "跟随音乐段落推进", {"signal": "section", "wave": ""}),
    "section_rise":   ("段落抬升", "段落", "trending-up", "段落内持续抬升", {"signal": "section", "wave": "Sawtooth", "freq": 1.0}),
    "section_pulse":  ("段落脉冲", "段落", "activity", "段落切换时脉冲", {"signal": "section", "wave": "Square", "attack": 0.0, "release": 0.4}),
    # ── M4 扩容（Build/Drop/Riser 实战语义）─────────────────────────────
    "build_up":       ("蓄力堆叠", "段落", "trending-up", "段落内逐小节抬升（Build）", {"signal": "energy", "wave": "Sawtooth", "freq": 1.0, "freq_unit": "bars", "attack": 1.0, "release": 0.3, "depth": 0.9}),
    "drop_open":      ("爆点开启", "段落", "arrow-down", "能量过阈后长保持（Drop 开启）", {"signal": "energy", "threshold": 0.55, "threshold_close": 0.45, "hold_ms": 400.0, "attack": 0.0, "release": 0.5}),
    "riser":          ("上扬", "段落", "arrow-up", "高频逐小节上扬（Riser）", {"signal": "band_high", "wave": "Expo", "freq": 2.0, "freq_unit": "bars", "attack": 0.0, "release": 0.4, "depth": 0.8}),
    "breakdown":      ("静默段", "段落", "walk", "强段压暗的静默段处理（反相+迟滞）", {"signal": "energy", "invert": 1.0, "threshold": 0.3, "threshold_close": 0.2, "smoothing": 0.5, "attack": 0.5}),
    "section_step":   ("段落阶梯", "段落", "route", "按小节整值跳变的段落阶梯", {"signal": "section", "wave": "Random", "freq": 1.0, "freq_unit": "bars", "random_mode": "discrete"}),

    # ── 动态 Dynamic（平滑/门限类）──────────────────────────────────────
    "smooth_follow":  ("平滑跟随", "动态", "adjustments", "强平滑的整体跟随", {"signal": "energy", "smoothing": 0.8}),
    "slow_swell_dyn": ("慢推", "动态", "trending-up", "长起应慢推", {"signal": "energy", "wave": "Sine", "freq": 1.0, "attack": 1.2, "release": 1.2}),
    "ducking":        ("闪避", "动态", "arrow-down", "强段压弱（反相+平滑）", {"signal": "energy", "invert": 1.0, "smoothing": 0.5}),
    # ── M4 扩容 ─────────────────────────────────────────────────────────
    "pump_comp":      ("泵感压缩", "动态", "activity", "强段压暗的泵感（反相+快落）", {"signal": "energy", "invert": 1.0, "attack": 0.02, "release": 0.25, "smoothing": 0.4, "depth": 0.85}),
    "lazy_follow":    ("迟滞跟随", "动态", "walk", "带迟滞门的慵懒跟随（保持 250ms）", {"signal": "energy", "threshold": 0.2, "threshold_close": 0.1, "hold_ms": 250.0, "attack": 0.4, "release": 0.6, "smoothing": 0.5}),
    "swell_ride":     ("起伏骑乘", "动态", "wave-saw-tool", "小节级的能量起伏骑乘", {"signal": "energy", "wave": "Sine", "freq": 1.0, "freq_unit": "bars", "attack": 0.7, "release": 0.9, "depth": 0.9}),
    "reverse_swell":  ("反向退潮", "动态", "arrow-down", "反相锯齿的小节级退潮", {"signal": "energy", "wave": "Sawtooth", "invert": 1.0, "freq": 1.0, "freq_unit": "bars", "attack": 0.2, "release": 0.6}),
}

#: 效果卡美术通道（R183-H，数据层唯一真源）：效果 key → B0 资产 id。
#:
#: * 消费方 = `draw_effect_grid.EffectGrid.set_effects`（第 6 元组元素显式传入，
#:   经 `honeycomb_grid.HoneycombGrid` 的画布通道画 16px 缩略；无 id 时瓦片照旧
#:   回退 tabler 图标 + 程序化底图，**向后兼容**）。
#: * 取值口径：85 效果全量走 `effects/eff_<key>` 族（manifest 已按语义配对落库：
#:   41 个基础图 + 44 个 M4 别名，别名 `alias_of` 指向同族近义图，经
#:   `art_asset_library.art_path` 正常解析；两族 key 与 `cards/` 零碰撞）。
#: * 许可：素材图标 MIT（Bootstrap Icons）/ CC-BY-3.0（game-icons.net，逐件署名见
#:   `src/ui/art_assets/LICENSES.md`）；本表只登记 id，不复制资产。
EFFECT_ART: Dict[str, str] = {
    # ── 律动（节拍/摆动；eff_stomp=stomp-tornado、eff_groove_swing=metronome）──
    "pulse_beat": "eff_pulse_beat",
    "stomp": "eff_stomp",
    "hard_gate": "eff_hard_gate",
    "heartbeat": "eff_heartbeat",
    "groove_swing": "eff_groove_swing",
    "offbeat": "eff_offbeat",
    "double_time": "eff_double_time",
    "half_time": "eff_half_time",
    "shark_beat": "eff_shark_beat",
    "stutter_gate": "eff_stutter_gate",
    "triplet_flow": "eff_triplet_flow",
    "dotted_push": "eff_dotted_push",
    "bounce_groove": "eff_bounce_groove",
    "swing_8th": "eff_swing_8th",
    # ── 呼吸（缓动/水浪；eff_swell=big-wave、eff_tide=high-tide）────────────
    "breath_slow": "eff_breath_slow",
    "breath_deep": "eff_breath_deep",
    "swell": "eff_swell",
    "tide": "eff_tide",
    "float_up": "eff_float_up",
    "sink_down": "eff_sink_down",
    "breathe_fast": "eff_breathe_fast",
    "wander_breath": "eff_wander_breath",
    "step_breath": "eff_step_breath",
    "hz_breath": "eff_hz_breath",
    "bar_swell": "eff_bar_swell",
    "exhale_breath": "eff_exhale_breath",
    "night_tide": "eff_night_tide",
    # ── 波形（eff_sine_wave=soundwave、eff_expo_ramp=graph-up-arrow）────────
    "sine_wave": "eff_sine_wave",
    "saw_rise": "eff_saw_rise",
    "triangle_wave": "eff_triangle_wave",
    "square_wave": "eff_square_wave",
    "expo_ramp": "eff_expo_ramp",
    "bounce_wave": "eff_bounce_wave",
    "random_wave": "eff_random_wave",
    "cosine_wave": "eff_cosine_wave",
    "shark_tooth": "eff_shark_tooth",
    "saw_down": "eff_saw_down",
    "dotted_saw": "eff_dotted_saw",
    "triangle_8": "eff_triangle_8",
    "triangle_16": "eff_triangle_16",
    "rectangle_wide": "eff_rectangle_wide",
    "step_seq": "eff_step_seq",
    "wander_wave": "eff_wander_wave",
    "sample_hold_wave": "eff_sample_hold_wave",
    "hz_sine": "eff_hz_sine",
    # ── 频谱（eff_bass_drive=speaker、eff_full_spectrum=sliders）────────────
    "bass_drive": "eff_bass_drive",
    "mid_flow": "eff_mid_flow",
    "high_sparkle": "eff_high_sparkle",
    "full_spectrum": "eff_full_spectrum",
    "bass_punch": "eff_bass_punch",
    "treble_shimmer": "eff_treble_shimmer",
    "spectrum_balance": "eff_spectrum_balance",
    "sub_rumble": "eff_sub_rumble",
    "vocal_presence": "eff_vocal_presence",
    "air_band": "eff_air_band",
    "bass_ratio_gate": "eff_bass_ratio_gate",
    "spectrum_split": "eff_spectrum_split",
    "crossover_pulse": "eff_crossover_pulse",
    # ── 冲击（eff_flash=lightning、eff_transient_hit=lightning-charge）──────
    "transient_hit": "eff_transient_hit",
    "flash": "eff_flash",
    "thunder": "eff_thunder",
    "trap_drop": "eff_trap_drop",
    "spark_burst": "eff_spark_burst",
    "clap": "eff_clap",
    "snare_hit": "eff_snare_hit",
    "kick_lock": "eff_kick_lock",
    "laser_stab": "eff_laser_stab",
    "impact_hold": "eff_impact_hold",
    "ripple_hit": "eff_ripple_hit",
    "double_hit": "eff_double_hit",
    # ── 段落（eff_section_follow=signpost、eff_build_up=graph-up）───────────
    "section_follow": "eff_section_follow",
    "section_rise": "eff_section_rise",
    "section_pulse": "eff_section_pulse",
    "build_up": "eff_build_up",
    "drop_open": "eff_drop_open",
    "riser": "eff_riser",
    "breakdown": "eff_breakdown",
    "section_step": "eff_section_step",
    # ── 动态（eff_smooth_follow=bezier2、eff_ducking=arrow-down-right）──────
    "smooth_follow": "eff_smooth_follow",
    "slow_swell_dyn": "eff_slow_swell_dyn",
    "ducking": "eff_ducking",
    "pump_comp": "eff_pump_comp",
    "lazy_follow": "eff_lazy_follow",
    "swell_ride": "eff_swell_ride",
    "reverse_swell": "eff_reverse_swell",
}

#: 分组显示顺序（左栏分组名）
EFFECT_GROUPS: Tuple[str, ...] = ("律动", "呼吸", "波形", "频谱", "冲击", "段落", "动态")

#: 分组图标（左栏分组头）
GROUP_ICONS: Dict[str, str] = {
    "律动": "activity", "呼吸": "wind", "波形": "wave-sine", "频谱": "adjustments",
    "冲击": "bolt", "段落": "route", "动态": "trending-up",
}

#: 效果卡世界尺寸（比旧的通道规则卡更大：容纳参数行 + 目标勾选行）
EFFECT_CARD_W, EFFECT_CARD_H = 232.0, 172.0
#: 目标勾选行（最多显示数；超出滚动不进—— 受限于卡片高度，取前 8 个）
MAX_TARGET_CHIPS = 8


def effect_spec(key: str) -> tuple:
    """效果 key → 目录元组（未知 → 默认脉冲效果）。"""
    return EFFECT_CATALOG.get(str(key), EFFECT_CATALOG["pulse_beat"])


def effect_label(key: str) -> str:
    """效果 key → 中文显示名。"""
    return effect_spec(key)[0]


def effect_icon(key: str) -> str:
    """效果 key → tabler 图标名。"""
    return effect_spec(key)[2]


def effect_group(key: str) -> str:
    """效果 key → 分组名。"""
    return effect_spec(key)[1]


def effect_art(key: str) -> str:
    """效果 key → B0 美术资产 id（`EFFECT_ART` 显式通道；未知 key 回退约定名）。

    未知 key 回退 `eff_<key>`（manifest 无登记时 `art_asset_library` 静默返回空
    位图，绘制端回退 tabler 图标）——**永不抛异常、永不阻塞绘制**。
    """
    k = str(key or "")
    got = EFFECT_ART.get(k)
    return got if got else ("eff_" + k if k else "")


def default_params(key: str) -> dict:
    """效果 key → 完整参数表（目录覆盖 + 规格默认）。"""
    params = {pk: spec[1] for pk, spec in PARAM_SPECS.items()}
    for pk, pv in (effect_spec(key)[4] or {}).items():
        if pk in params:
            params[pk] = pv
    return params


def effect_choices() -> List[tuple]:
    """左栏/下拉条目：[(key, 显示名, 分组, 图标, 说明)]（按分组序）。"""
    rows = []
    for group in EFFECT_GROUPS:
        for key, spec in EFFECT_CATALOG.items():
            if spec[1] == group:
                rows.append((key, spec[0], spec[1], spec[2], spec[3]))
    return rows


def grouped_choices() -> List[tuple]:
    """[(分组名, 分组图标, [（key, 显示名, 说明, 图标）...])]（左栏分组渲染）。"""
    out = []
    for group in EFFECT_GROUPS:
        items = [(key, spec[0], spec[3], spec[2])
                 for key, spec in EFFECT_CATALOG.items() if spec[1] == group]
        if items:
            out.append((group, GROUP_ICONS.get(group, "activity"), items))
    return out


def primary_params(key: str) -> List[str]:
    """效果卡上显示的关键控件参数（深度/频率/起应/落应 四个固定位）。"""
    return ["depth", "freq", "attack", "release"]


def param_label(pkey: str) -> str:
    """参数 key → 中文名（未知回退原键）。"""
    spec = PARAM_SPECS.get(str(pkey))
    return spec[0] if spec else str(pkey)


def _merge_params(effect: str, params) -> dict:
    """效果默认参数 + 覆盖（仅这些参数键生效）。"""
    p = default_params(effect)
    if isinstance(params, dict):
        for k, v in params.items():
            if k in p:
                p[k] = v
    return p


def effect_card_node(nid: str, effect: str, pos, *, params=None,
                     ch_types=None) -> dict:
    """效果卡节点（画布 dict；kind 沿用 "rule" 保持旧编译/命中路径兼容）。"""
    p = _merge_params(effect, params)
    targets = [str(c) for c in (ch_types or []) if str(c)]
    return {"id": str(nid), "kind": "rule", "x": float(pos[0]),
            "y": float(pos[1]), "effect": str(effect or "pulse_beat"),
            "params": p, "ch_types": targets,
            "ch_type": targets[0] if targets else "",
            "wave": str(p.get("wave", "") or ""),
            "wave_freq": float(p.get("freq", 2.0) or 2.0),
            "amount": float(p.get("depth", 1.0) or 0.0)}
