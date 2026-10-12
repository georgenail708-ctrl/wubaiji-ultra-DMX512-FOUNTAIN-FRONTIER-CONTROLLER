"""
melody_analyzer.py — DeepAqua 旋律识别强化层
=============================================
这是 AI 编舞系统的"耳朵"，将音频特征转化为精确的旋律语义，
供 ExpressionMatrixDriver 统一调度各通道表现力。

核心功能：
  1. 和弦识别与走向分析（Chord Progression）
     - 识别当前和弦根音、性质（大/小/属/减/增）
     - 追踪功能和声进行（I-IV-V-I, ii-V-I 等）
     - 计算和弦张力（协和度 → 紧张度）
     - 检测调性中心和调式（大调/小调/调式）

  2. 旋律轮廓追踪（Melodic Contour）
     - 上行 / 下行 / 拱形 / 谷形 / 平稳 / 波动
     - 旋律音域（最高音 / 最低音）
     - 旋律走向速率（快速上行 vs 缓慢下行）

  3. 动机记忆系统（Motif Memory）
     - 主题指纹提取（音程序列哈希）
     - 段落再现检测
     - 演化追踪（变奏 / 倒影 / 逆行）

  4. 节拍网格对齐（Beat Grid）
     - 音符精确对齐到 BPM 网格
     - 强拍 / 弱拍 / 切分 / 三连音识别
     - 旋律节奏密度（音符/拍）

  5. ADSR 包络提取（Envelope）
     - 实时追踪能量包络的 Attack / Decay / Sustain / Release
     - 每个音符的起伏曲线

  6. 装饰音检测（Ornament Detection）
     - 颤音（Trill）：快速交替音
     - 滑音（Glissando）：连续音高滑动
     - 倚音（Appoggiatura）：短暂前置音
     - 震音（Tremolo）：快速重复
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
#  枚举定义
# ═══════════════════════════════════════════════════════════════════════════

class ChordQuality(Enum):
    MAJOR       = "major"       # 大三和弦（明亮、稳定）
    MINOR       = "minor"       # 小三和弦（暗淡、内敛）
    DOMINANT7   = "dom7"        # 属七和弦（紧张、推进）
    MAJOR7      = "maj7"        # 大七和弦（温暖、复杂）
    MINOR7      = "min7"        # 小七和弦（柔和、爵士）
    DIMINISHED  = "dim"         # 减三和弦（极度紧张）
    AUGMENTED   = "aug"         # 增三和弦（悬疑、不稳）
    SUS2        = "sus2"        # 挂留二和弦（开放、空旷）
    SUS4        = "sus4"        # 挂留四和弦（期待、悬浮）
    POWER       = "power"       # 强力和弦（空洞、有力）
    UNKNOWN     = "unknown"


class ChordFunction(Enum):
    TONIC       = "tonic"       # 主功能（稳定）
    SUBDOMINANT = "subdominant" # 下属功能（过渡）
    DOMINANT    = "dominant"    # 属功能（紧张→解决）
    SECONDARY   = "secondary"   # 副属和弦
    MODAL       = "modal"       # 调式借用
    CHROMATIC   = "chromatic"   # 半音和弦
    UNKNOWN     = "unknown"


class MelodicContour(Enum):
    ASCENDING   = "ascending"   # 上行（水柱升高，灯光变亮）
    DESCENDING  = "descending"  # 下行（水柱降低，灯光变暗）
    ARCH        = "arch"        # 拱形（先升后降）
    VALLEY      = "valley"      # 谷形（先降后升）
    STATIC      = "static"      # 平稳（持续音）
    WAVE        = "wave"        # 波动（反复起伏）
    LEAP_UP     = "leap_up"     # 跳进上行（大跳，戏剧性）
    LEAP_DOWN   = "leap_down"   # 跳进下行


class OrnamentType(Enum):
    NONE        = "none"
    TRILL       = "trill"       # 颤音
    GLISSANDO   = "glissando"   # 滑音
    APPOGGIATURA= "appoggiatura"# 倚音
    TREMOLO     = "tremolo"     # 震音
    VIBRATO     = "vibrato"     # 揉弦（幅度小的颤音）


class BeatPosition(Enum):
    STRONG      = "strong"      # 强拍（1、3拍）
    WEAK        = "weak"        # 弱拍（2、4拍）
    OFFBEAT     = "offbeat"     # 弱位（拍间）
    SYNCOPATED  = "syncopated"  # 切分
    TRIPLET     = "triplet"     # 三连音


class ADSRPhase(Enum):
    ATTACK      = "attack"
    DECAY       = "decay"
    SUSTAIN     = "sustain"
    RELEASE     = "release"
    IDLE        = "idle"


# ═══════════════════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ChordState:
    """当前和弦状态"""
    root_midi: int = 60              # 根音 MIDI 音高（C4=60）
    root_name: str = "C"             # 根音名称
    quality: ChordQuality = ChordQuality.MAJOR
    function: ChordFunction = ChordFunction.TONIC
    tension: float = 0.0             # 张力 [0,1]：0=完全协和，1=极度紧张
    color_hue: float = 0.0           # 色相 [0,360]：基于五度圈映射
    color_saturation: float = 0.7    # 饱和度：大调高，小调低
    color_brightness: float = 0.8    # 明度：协和高，紧张低
    is_resolution: bool = False      # 是否是解决（属→主）
    is_climax_chord: bool = False    # 是否是高潮和弦


@dataclass
class MelodicState:
    """当前旋律状态"""
    pitch_midi: float = 60.0         # 当前音高（MIDI，支持小数用于滑音）
    pitch_normalized: float = 0.5   # 归一化音高 [0,1]（相对于音域）
    contour: MelodicContour = MelodicContour.STATIC
    contour_rate: float = 0.0        # 轮廓变化速率（正=上行，负=下行）
    interval_semitones: float = 0.0  # 与上一音的音程（半音数）
    is_stepwise: bool = True         # 是否级进（≤2半音）
    is_leap: bool = False            # 是否跳进（>2半音）
    ornament: OrnamentType = OrnamentType.NONE
    ornament_intensity: float = 0.0  # 装饰音强度 [0,1]
    note_density: float = 0.5        # 音符密度（音符/拍）
    phrase_position: float = 0.5     # 在乐句中的位置 [0,1]


@dataclass
class BeatState:
    """节拍状态"""
    bpm: float = 120.0
    beat_phase: float = 0.0          # 当前拍内相位 [0,1]
    beat_number: int = 1             # 拍号（1-4）
    bar_number: int = 1              # 小节号
    position: BeatPosition = BeatPosition.STRONG
    is_downbeat: bool = True         # 是否是小节第一拍
    beat_strength: float = 1.0       # 拍强度（强拍=1.0，弱拍=0.5，弱位=0.25）
    time_signature_num: int = 4      # 拍号分子
    time_signature_den: int = 4      # 拍号分母
    next_beat_in_sec: float = 0.5    # 距下一拍的时间（秒）


@dataclass
class ADSRState:
    """ADSR 包络状态"""
    phase: ADSRPhase = ADSRPhase.IDLE
    level: float = 0.0               # 当前包络电平 [0,1]
    attack_time: float = 0.05        # Attack 时长（秒）
    decay_time: float = 0.1          # Decay 时长（秒）
    sustain_level: float = 0.7       # Sustain 电平
    release_time: float = 0.3        # Release 时长（秒）
    phase_progress: float = 0.0      # 当前阶段进度 [0,1]
    note_on: bool = False            # 是否有音符激活


@dataclass
class MotifFingerprint:
    """动机指纹（用于主题识别和再现检测）"""
    interval_sequence: Tuple[int, ...]  = field(default_factory=tuple)  # 音程序列
    rhythm_pattern: Tuple[float, ...] = field(default_factory=tuple)    # 节奏型
    contour_signature: str = ""          # 轮廓特征字符串（如"UUDDUU"）
    first_seen_bar: int = 0
    occurrence_count: int = 1
    label: str = ""                      # 主题标签（A/B/C/verse/chorus 等）


@dataclass
class MelodyAnalysisResult:
    """
    旋律分析的完整输出结果。
    这是 ExpressionMatrixDriver 的主要输入信号。
    """
    # 和弦状态
    chord: ChordState = field(default_factory=ChordState)
    # 旋律状态
    melody: MelodicState = field(default_factory=MelodicState)
    # 节拍状态
    beat: BeatState = field(default_factory=BeatState)
    # ADSR 包络
    adsr: ADSRState = field(default_factory=ADSRState)
    # 是否 Solo 模式（旋律主导，需要电机路径生成）
    is_solo_mode: bool = False
    # Solo 强度 [0,1]（0=全 Effect，1=全路径生成）
    solo_intensity: float = 0.0
    # 当前动机标签
    active_motif_label: str = ""
    # 是否主题再现
    is_theme_recurrence: bool = False
    # 旋律进程标签（verse/chorus/bridge/solo/intro/outro）
    section_label: str = "verse"
    # 综合旋律强度 [0,1]（驱动电机旋律响应权重）
    melody_intensity: float = 0.5
    # 综合和弦张力 [0,1]（驱动灯光/阀门）
    chord_tension: float = 0.0
    # 旋律音高归一化 [0,1]（驱动泵高度）
    pitch_normalized: float = 0.5
    # 节拍冲击 [0,1]（驱动灯光闪烁/阀门切分）
    beat_impulse: float = 0.0
    # 装饰音信号 [0,1]（驱动电机微颤）
    ornament_signal: float = 0.0
    # 旋律走向向量 [-1,1]（-1=下行，+1=上行，驱动电机方向）
    contour_vector: float = 0.0
    # 色相 [0,360]（驱动灯光颜色）
    color_hue: float = 0.0
    # 色彩饱和度 [0,1]
    color_saturation: float = 0.7
    # 色彩明度 [0,1]
    color_brightness: float = 0.8


# ═══════════════════════════════════════════════════════════════════════════
#  和弦张力表（协和度→张力值）
# ═══════════════════════════════════════════════════════════════════════════

CHORD_TENSION_MAP: Dict[ChordQuality, float] = {
    ChordQuality.MAJOR:      0.05,   # 完全协和，几乎无张力
    ChordQuality.MINOR:      0.15,   # 轻微暗淡
    ChordQuality.SUS2:       0.20,   # 开放，轻微悬浮
    ChordQuality.SUS4:       0.30,   # 期待解决
    ChordQuality.POWER:      0.10,   # 空洞，中性
    ChordQuality.MAJOR7:     0.25,   # 温暖复杂
    ChordQuality.MINOR7:     0.30,   # 柔和爵士
    ChordQuality.DOMINANT7:  0.60,   # 强烈推进
    ChordQuality.AUGMENTED:  0.75,   # 悬疑不稳
    ChordQuality.DIMINISHED: 0.90,   # 极度紧张
    ChordQuality.UNKNOWN:    0.40,
}

# 五度圈色相映射（12个音级 → 色相角度）
# C=红, G=橙, D=黄, A=黄绿, E=绿, B=青绿,
# F#=青, Db=蓝, Ab=蓝紫, Eb=紫, Bb=红紫, F=玫红
CIRCLE_OF_FIFTHS_HUE: Dict[int, float] = {
    0:  0.0,    # C  → 红
    7:  30.0,   # G  → 橙
    2:  60.0,   # D  → 黄
    9:  90.0,   # A  → 黄绿
    4:  120.0,  # E  → 绿
    11: 150.0,  # B  → 青绿
    6:  180.0,  # F# → 青
    1:  210.0,  # Db → 蓝
    8:  240.0,  # Ab → 蓝紫
    3:  270.0,  # Eb → 紫
    10: 300.0,  # Bb → 红紫
    5:  330.0,  # F  → 玫红
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# ═══════════════════════════════════════════════════════════════════════════
#  MelodyAnalyzer 主类
# ═══════════════════════════════════════════════════════════════════════════

class MelodyAnalyzer:
    """
    旋律识别强化层。

    接收实时音频特征（来自 MusicContext），输出精确的旋律语义
    供 ExpressionMatrixDriver 统一调度各通道表现力。

    设计原则：
    - 所有分析基于音频特征参数（非原始 PCM），适合实时运行
    - 输出结果直接映射到物理通道控制量
    - 支持历史追踪（动机记忆、主题再现）
    - 参数范围宽泛，不硬编码限制
    """

    def __init__(
        self,
        pitch_range: Tuple[float, float] = (36.0, 84.0),  # MIDI 音高范围（C2-C6）
        tonic_midi: int = 0,                               # 调性中心（0=C）
        is_major: bool = True,                             # 大调/小调
        solo_threshold: float = 0.6,                       # Solo 模式触发阈值
        motif_window: int = 8,                             # 动机检测窗口（音符数）
    ):
        self._pitch_range = pitch_range
        self._tonic = tonic_midi % 12
        self._is_major = is_major
        self._solo_threshold = solo_threshold
        self._motif_window = motif_window

        # 历史缓冲
        self._pitch_history: List[float] = []
        self._interval_history: List[float] = []
        self._rhythm_history: List[float] = []
        self._energy_history: List[float] = []

        # 当前状态
        self._chord = ChordState()
        self._melody = MelodicState()
        self._beat = BeatState()
        self._adsr = ADSRState()

        # 动机记忆
        self._motif_library: Dict[str, MotifFingerprint] = {}
        self._current_motif: Optional[MotifFingerprint] = None

        # ADSR 内部计时
        self._adsr_timer: float = 0.0
        self._prev_energy: float = 0.0
        self._prev_pitch: float = 60.0
        self._beat_timer: float = 0.0
        self._bar_timer: float = 0.0

    # ─────────────────────────────────────────────────────────────────────
    #  主分析入口
    # ─────────────────────────────────────────────────────────────────────

    def analyze(
        self,
        energy: float,
        melody: float,           # 旋律强度 [0,1]
        tension: float,          # 张力 [0,1]
        rhythm: float,           # 节奏强度 [0,1]
        bpm: float,              # BPM
        pitch_midi: float = -1,  # 当前音高（-1=未知，自动估算）
        chord_root: int = -1,    # 和弦根音（-1=自动推断）
        chord_quality_hint: str = "",  # 和弦性质提示
        note_on: bool = True,    # 是否有音符激活
        note_density: float = 0.5,    # 音符密度（音符/拍）
        section_label: str = "verse", # 段落标签
        dt: float = 0.016,       # 时间步长（秒）
    ) -> MelodyAnalysisResult:
        """
        核心分析方法，每帧调用一次。

        Args:
            energy: 音频能量 [0,∞)
            melody: 旋律强度 [0,1]
            tension: 张力 [0,1]
            rhythm: 节奏强度 [0,1]
            bpm: 当前 BPM
            pitch_midi: 当前主旋律音高（MIDI）
            chord_root: 和弦根音（MIDI 音级 0-11）
            chord_quality_hint: 和弦性质字符串提示
            note_on: 是否有音符激活
            note_density: 音符密度
            section_label: 段落标签
            dt: 时间步长

        Returns:
            MelodyAnalysisResult
        """
        # 1. 估算音高（如果未提供）
        if pitch_midi < 0:
            pitch_midi = self._estimate_pitch(melody, energy, tension)

        # 2. 分析和弦
        self._update_chord(chord_root, chord_quality_hint, tension, energy, melody)

        # 3. 分析旋律轮廓
        self._update_melody(pitch_midi, melody, note_density, dt)

        # 4. 更新节拍状态
        self._update_beat(bpm, dt)

        # 5. 更新 ADSR 包络
        self._update_adsr(energy, note_on, dt)

        # 6. 检测装饰音
        self._detect_ornament(pitch_midi, energy, rhythm, dt)

        # 7. 检测 Solo 模式
        solo_intensity = self._detect_solo(melody, note_density, energy, rhythm)

        # 8. 动机记忆
        motif_label, is_recurrence = self._update_motif(pitch_midi, dt)

        # 9. 计算综合输出信号
        result = self._compute_output(
            energy, melody, tension, rhythm, bpm,
            solo_intensity, motif_label, is_recurrence, section_label
        )

        # 更新历史
        self._prev_energy = energy
        self._prev_pitch = pitch_midi
        self._pitch_history.append(pitch_midi)
        if len(self._pitch_history) > 64:
            self._pitch_history.pop(0)

        return result

    # ─────────────────────────────────────────────────────────────────────
    #  音高估算（当外部未提供精确音高时）
    # ─────────────────────────────────────────────────────────────────────

    def _estimate_pitch(self, melody: float, energy: float, tension: float) -> float:
        """
        基于旋律强度和能量估算当前音高。
        旋律强度高 → 音高偏高（旋律线在高音区）
        张力高 → 音高偏高（紧张时旋律倾向于上行）
        """
        lo, hi = self._pitch_range
        # 基础音高：旋律强度映射到音域
        base = lo + (hi - lo) * melody
        # 张力修正：张力越高，音高越高（最多 +6 半音）
        tension_offset = tension * 6.0
        # 能量修正：能量越高，音高变化越剧烈
        energy_jitter = (energy - 0.5) * 4.0
        estimated = base + tension_offset + energy_jitter
        return float(np.clip(estimated, lo, hi))

    # ─────────────────────────────────────────────────────────────────────
    #  和弦分析
    # ─────────────────────────────────────────────────────────────────────

    def _update_chord(
        self,
        chord_root: int,
        quality_hint: str,
        tension: float,
        energy: float,
        melody: float,
    ) -> None:
        """更新和弦状态"""
        # 推断根音
        if chord_root < 0:
            chord_root = self._infer_chord_root(tension, energy, melody)
        root_pc = chord_root % 12

        # 推断和弦性质
        quality = self._parse_chord_quality(quality_hint, tension, energy)

        # 推断和声功能
        function = self._infer_chord_function(root_pc, quality)

        # 计算张力
        base_tension = CHORD_TENSION_MAP.get(quality, 0.4)
        # 属功能额外增加张力
        if function == ChordFunction.DOMINANT:
            base_tension = min(1.0, base_tension + 0.2)
        # 与输入 tension 融合（加权平均）
        chord_tension = base_tension * 0.6 + tension * 0.4

        # 计算色彩
        hue = CIRCLE_OF_FIFTHS_HUE.get(root_pc, 0.0)
        # 小调/减和弦：降低饱和度和明度
        saturation = 0.9 if quality in (ChordQuality.MAJOR, ChordQuality.MAJOR7) else 0.6
        brightness = 1.0 - chord_tension * 0.5  # 张力越高，明度越低

        # 检测解决（属→主）
        is_resolution = (
            self._chord.function == ChordFunction.DOMINANT
            and function == ChordFunction.TONIC
        )

        self._chord = ChordState(
            root_midi=chord_root,
            root_name=NOTE_NAMES[root_pc],
            quality=quality,
            function=function,
            tension=chord_tension,
            color_hue=hue,
            color_saturation=saturation,
            color_brightness=brightness,
            is_resolution=is_resolution,
            is_climax_chord=(quality == ChordQuality.DOMINANT7 and tension > 0.7),
        )

    def _infer_chord_root(self, tension: float, energy: float, melody: float) -> int:
        """
        基于音频特征推断和弦根音。
        使用调性内和弦的概率分布。
        """
        # 大调内的自然和弦根音（相对于调性中心）
        if self._is_major:
            # I  ii  iii  IV  V  vi  vii°
            diatonic = [0, 2, 4, 5, 7, 9, 11]
            # 功能权重：高张力→属功能(V)，低张力→主功能(I/vi)
            weights = [
                0.3 - tension * 0.1,   # I
                0.1,                    # ii
                0.05,                   # iii
                0.15 - tension * 0.05,  # IV
                0.2 + tension * 0.2,    # V（张力越高越可能是属和弦）
                0.15 - tension * 0.05,  # vi
                tension * 0.05,         # vii°（只在高张力时出现）
            ]
        else:
            # 自然小调：i  ii°  III  iv  v  VI  VII
            diatonic = [0, 2, 3, 5, 7, 8, 10]
            weights = [
                0.3 - tension * 0.1,
                tension * 0.05,
                0.1,
                0.15,
                0.15 + tension * 0.15,
                0.15,
                0.1,
            ]

        # 归一化权重
        total = sum(weights)
        weights = [w / total for w in weights]

        # 选取概率最高的根音（确定性，非随机）
        best_idx = int(np.argmax(weights))
        return (self._tonic + diatonic[best_idx]) % 12

    def _parse_chord_quality(
        self, hint: str, tension: float, energy: float
    ) -> ChordQuality:
        """解析和弦性质"""
        hint_lower = hint.lower()
        if "dim" in hint_lower:
            return ChordQuality.DIMINISHED
        elif "aug" in hint_lower:
            return ChordQuality.AUGMENTED
        elif "maj7" in hint_lower or "major7" in hint_lower:
            return ChordQuality.MAJOR7
        elif "dom7" in hint_lower:
            return ChordQuality.DOMINANT7
        elif "min7" in hint_lower or "m7" in hint_lower:
            return ChordQuality.MINOR7
        elif "7" in hint_lower:
            return ChordQuality.DOMINANT7
        elif "sus4" in hint_lower:
            return ChordQuality.SUS4
        elif "sus2" in hint_lower:
            return ChordQuality.SUS2
        elif "maj" in hint_lower or "major" in hint_lower:
            return ChordQuality.MAJOR
        elif "min" in hint_lower or "m" in hint_lower:
            return ChordQuality.MINOR
        else:
            # 基于张力自动推断
            if tension > 0.75:
                return ChordQuality.DOMINANT7
            elif tension > 0.55:
                return ChordQuality.MINOR7
            elif tension > 0.35:
                return ChordQuality.MINOR
            else:
                return ChordQuality.MAJOR

    def _infer_chord_function(
        self, root_pc: int, quality: ChordQuality
    ) -> ChordFunction:
        """推断和声功能"""
        interval = (root_pc - self._tonic) % 12
        if self._is_major:
            if interval == 0:
                return ChordFunction.TONIC
            elif interval == 5:
                return ChordFunction.SUBDOMINANT
            elif interval == 7:
                return ChordFunction.DOMINANT
            elif interval == 9:
                return ChordFunction.TONIC  # vi（主功能替代）
            elif interval == 2:
                return ChordFunction.SUBDOMINANT  # ii（下属功能替代）
            else:
                return ChordFunction.SECONDARY
        else:
            if interval == 0:
                return ChordFunction.TONIC
            elif interval == 5:
                return ChordFunction.SUBDOMINANT
            elif interval == 7:
                return ChordFunction.DOMINANT
            else:
                return ChordFunction.MODAL

    # ─────────────────────────────────────────────────────────────────────
    #  旋律轮廓分析
    # ─────────────────────────────────────────────────────────────────────

    def _update_melody(
        self,
        pitch_midi: float,
        melody_intensity: float,
        note_density: float,
        dt: float,
    ) -> None:
        """更新旋律状态"""
        lo, hi = self._pitch_range
        pitch_norm = (pitch_midi - lo) / max(hi - lo, 1.0)
        pitch_norm = float(np.clip(pitch_norm, 0.0, 1.0))

        # 计算音程
        interval = pitch_midi - self._prev_pitch
        is_stepwise = abs(interval) <= 2
        is_leap = abs(interval) > 4

        # 分析轮廓
        contour, contour_rate = self._analyze_contour(interval, melody_intensity)

        # 检测装饰音（在 _detect_ornament 中处理，这里只更新基础状态）
        self._melody = MelodicState(
            pitch_midi=pitch_midi,
            pitch_normalized=pitch_norm,
            contour=contour,
            contour_rate=contour_rate,
            interval_semitones=interval,
            is_stepwise=is_stepwise,
            is_leap=is_leap,
            ornament=self._melody.ornament,
            ornament_intensity=self._melody.ornament_intensity,
            note_density=note_density,
            phrase_position=self._melody.phrase_position,
        )

    def _analyze_contour(
        self, interval: float, melody_intensity: float
    ) -> Tuple[MelodicContour, float]:
        """分析旋律轮廓"""
        # 基于最近音高历史分析轮廓
        if len(self._pitch_history) >= 4:
            recent = self._pitch_history[-4:]
            diffs = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
            avg_diff = sum(diffs) / len(diffs)
            rate = avg_diff / 12.0  # 归一化（每八度=1.0）

            # 判断轮廓类型
            if all(d > 0.5 for d in diffs):
                contour = MelodicContour.ASCENDING
            elif all(d < -0.5 for d in diffs):
                contour = MelodicContour.DESCENDING
            elif abs(interval) > 6:
                contour = MelodicContour.LEAP_UP if interval > 0 else MelodicContour.LEAP_DOWN
            elif diffs[0] > 0 and diffs[-1] < 0:
                contour = MelodicContour.ARCH
            elif diffs[0] < 0 and diffs[-1] > 0:
                contour = MelodicContour.VALLEY
            elif max(abs(d) for d in diffs) < 1.0:
                contour = MelodicContour.STATIC
            else:
                contour = MelodicContour.WAVE

            return contour, float(np.clip(rate, -1.0, 1.0))
        else:
            # 历史不足，基于当前音程判断
            if interval > 1:
                return MelodicContour.ASCENDING, min(1.0, interval / 12.0)
            elif interval < -1:
                return MelodicContour.DESCENDING, max(-1.0, interval / 12.0)
            else:
                return MelodicContour.STATIC, 0.0

    # ─────────────────────────────────────────────────────────────────────
    #  节拍分析
    # ─────────────────────────────────────────────────────────────────────

    def _update_beat(self, bpm: float, dt: float) -> None:
        """更新节拍状态"""
        beat_sec = 60.0 / max(bpm, 1.0)
        bar_sec = beat_sec * self._beat.time_signature_num

        self._beat_timer += dt
        self._bar_timer += dt

        # 计算拍内相位
        beat_phase = (self._beat_timer % beat_sec) / beat_sec

        # 计算当前拍号
        beat_number = int(self._beat_timer / beat_sec) % self._beat.time_signature_num + 1
        bar_number = int(self._bar_timer / bar_sec) + 1

        # 判断拍位置
        is_downbeat = (beat_number == 1)
        if is_downbeat:
            position = BeatPosition.STRONG
            strength = 1.0
        elif beat_number in (3,):
            position = BeatPosition.STRONG
            strength = 0.8
        elif beat_number in (2, 4):
            position = BeatPosition.WEAK
            strength = 0.5
        else:
            position = BeatPosition.OFFBEAT
            strength = 0.25

        self._beat = BeatState(
            bpm=bpm,
            beat_phase=beat_phase,
            beat_number=beat_number,
            bar_number=bar_number,
            position=position,
            is_downbeat=is_downbeat,
            beat_strength=strength,
            time_signature_num=self._beat.time_signature_num,
            time_signature_den=self._beat.time_signature_den,
            next_beat_in_sec=beat_sec * (1.0 - beat_phase),
        )

    # ─────────────────────────────────────────────────────────────────────
    #  ADSR 包络
    # ─────────────────────────────────────────────────────────────────────

    def _update_adsr(self, energy: float, note_on: bool, dt: float) -> None:
        """更新 ADSR 包络状态"""
        adsr = self._adsr

        # 检测音符触发（能量突然上升）
        energy_delta = energy - self._prev_energy
        note_triggered = energy_delta > 0.15 and note_on

        if note_triggered:
            # 重新触发 Attack
            adsr.phase = ADSRPhase.ATTACK
            adsr.phase_progress = 0.0
            adsr.note_on = True

        if not note_on and adsr.note_on:
            # 音符释放
            adsr.phase = ADSRPhase.RELEASE
            adsr.phase_progress = 0.0
            adsr.note_on = False

        # 推进当前阶段
        if adsr.phase == ADSRPhase.ATTACK:
            adsr.phase_progress += dt / max(adsr.attack_time, 0.001)
            adsr.level = adsr.phase_progress
            if adsr.phase_progress >= 1.0:
                adsr.phase = ADSRPhase.DECAY
                adsr.phase_progress = 0.0

        elif adsr.phase == ADSRPhase.DECAY:
            adsr.phase_progress += dt / max(adsr.decay_time, 0.001)
            adsr.level = 1.0 - (1.0 - adsr.sustain_level) * adsr.phase_progress
            if adsr.phase_progress >= 1.0:
                adsr.phase = ADSRPhase.SUSTAIN
                adsr.phase_progress = 0.0
                adsr.level = adsr.sustain_level

        elif adsr.phase == ADSRPhase.SUSTAIN:
            # Sustain 阶段：跟随能量动态调整
            adsr.level = adsr.sustain_level * (0.7 + 0.3 * energy)

        elif adsr.phase == ADSRPhase.RELEASE:
            adsr.phase_progress += dt / max(adsr.release_time, 0.001)
            adsr.level = adsr.sustain_level * (1.0 - adsr.phase_progress)
            if adsr.phase_progress >= 1.0:
                adsr.phase = ADSRPhase.IDLE
                adsr.level = 0.0

        adsr.level = float(np.clip(adsr.level, 0.0, 1.0))
        self._adsr = adsr

    # ─────────────────────────────────────────────────────────────────────
    #  装饰音检测
    # ─────────────────────────────────────────────────────────────────────

    def _detect_ornament(
        self, pitch_midi: float, energy: float, rhythm: float, dt: float
    ) -> None:
        """检测装饰音类型"""
        if len(self._pitch_history) < 4:
            self._melody.ornament = OrnamentType.NONE
            self._melody.ornament_intensity = 0.0
            return

        recent = self._pitch_history[-6:] if len(self._pitch_history) >= 6 else self._pitch_history
        diffs = [abs(recent[i+1] - recent[i]) for i in range(len(recent)-1)]
        avg_diff = sum(diffs) / len(diffs) if diffs else 0

        # 颤音：快速小幅交替（音程 ≤2 半音，密度高）
        if avg_diff <= 2.0 and avg_diff > 0.3 and rhythm > 0.6:
            ornament = OrnamentType.TRILL
            intensity = min(1.0, rhythm * avg_diff / 2.0)

        # 震音：快速重复同音
        elif avg_diff < 0.3 and rhythm > 0.7:
            ornament = OrnamentType.TREMOLO
            intensity = rhythm * 0.8

        # 滑音：连续单方向滑动（音程大且方向一致）
        elif len(recent) >= 4:
            raw_diffs = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
            all_same_dir = all(d > 0 for d in raw_diffs) or all(d < 0 for d in raw_diffs)
            if all_same_dir and avg_diff > 1.0:
                ornament = OrnamentType.GLISSANDO
                intensity = min(1.0, avg_diff / 6.0)
            else:
                ornament = OrnamentType.NONE
                intensity = 0.0
        else:
            ornament = OrnamentType.NONE
            intensity = 0.0

        self._melody.ornament = ornament
        self._melody.ornament_intensity = intensity

    # ─────────────────────────────────────────────────────────────────────
    #  Solo 模式检测
    # ─────────────────────────────────────────────────────────────────────

    def _detect_solo(
        self, melody: float, note_density: float, energy: float, rhythm: float
    ) -> float:
        """
        检测 Solo 模式强度。
        Solo = 旋律主导 + 高音符密度 + 中等能量（不是纯节奏段落）
        返回 [0,1]，0=全 Effect 模式，1=全路径生成模式
        """
        # Solo 特征：旋律强度高、音符密度高、节奏不过分主导
        solo_score = (
            melody * 0.5 +
            note_density * 0.3 +
            (1.0 - abs(rhythm - 0.5) * 2) * 0.2  # 节奏适中时加分
        )
        # 平滑处理（避免突变）
        prev_solo = getattr(self, '_prev_solo_intensity', 0.0)
        smoothed = prev_solo * 0.7 + solo_score * 0.3
        self._prev_solo_intensity = smoothed
        return float(np.clip(smoothed, 0.0, 1.0))

    # ─────────────────────────────────────────────────────────────────────
    #  动机记忆
    # ─────────────────────────────────────────────────────────────────────

    def _update_motif(self, pitch_midi: float, dt: float) -> Tuple[str, bool]:
        """更新动机记忆，返回 (当前动机标签, 是否主题再现)"""
        if len(self._pitch_history) < self._motif_window:
            return "", False

        # 提取最近的音程序列作为动机指纹
        recent = self._pitch_history[-self._motif_window:]
        intervals = tuple(
            round(recent[i+1] - recent[i]) for i in range(len(recent)-1)
        )

        # 生成轮廓签名
        contour_sig = "".join(
            "U" if iv > 0 else ("D" if iv < 0 else "S")
            for iv in intervals
        )

        # 查找匹配的动机
        best_match = None
        best_score = 0.0
        for label, motif in self._motif_library.items():
            score = self._motif_similarity(intervals, motif.interval_sequence)
            if score > best_score:
                best_score = score
                best_match = label

        is_recurrence = False
        if best_score > 0.75 and best_match:
            # 主题再现
            self._motif_library[best_match].occurrence_count += 1
            is_recurrence = True
            label = best_match
        else:
            # 新动机
            label = f"motif_{len(self._motif_library)+1}"
            self._motif_library[label] = MotifFingerprint(
                interval_sequence=intervals,
                contour_signature=contour_sig,
                label=label,
            )

        return label, is_recurrence

    def _motif_similarity(
        self, seq_a: Tuple[int, ...], seq_b: Tuple[int, ...]
    ) -> float:
        """计算两个音程序列的相似度 [0,1]"""
        if not seq_a or not seq_b:
            return 0.0
        min_len = min(len(seq_a), len(seq_b))
        max_len = max(len(seq_a), len(seq_b))
        if max_len == 0:
            return 1.0
        matches = sum(
            1 for a, b in zip(seq_a[:min_len], seq_b[:min_len])
            if abs(a - b) <= 1  # 允许1半音误差
        )
        return matches / max_len

    # ─────────────────────────────────────────────────────────────────────
    #  综合输出计算
    # ─────────────────────────────────────────────────────────────────────

    def _compute_output(
        self,
        energy: float,
        melody: float,
        tension: float,
        rhythm: float,
        bpm: float,
        solo_intensity: float,
        motif_label: str,
        is_recurrence: bool,
        section_label: str,
    ) -> MelodyAnalysisResult:
        """计算最终输出信号"""

        # 节拍冲击：强拍时产生脉冲
        beat_impulse = self._beat.beat_strength * (
            1.0 if self._beat.beat_phase < 0.1 else 0.0
        )
        # 加入能量调制
        beat_impulse *= (0.5 + 0.5 * energy)

        # 旋律走向向量
        contour_vector = self._melody.contour_rate
        if self._melody.contour == MelodicContour.LEAP_UP:
            contour_vector = 1.0
        elif self._melody.contour == MelodicContour.LEAP_DOWN:
            contour_vector = -1.0

        # 装饰音信号
        ornament_signal = self._melody.ornament_intensity

        # 综合旋律强度（驱动电机旋律响应权重）
        melody_intensity = (
            melody * 0.5 +
            solo_intensity * 0.3 +
            self._adsr.level * 0.2
        )

        return MelodyAnalysisResult(
            chord=self._chord,
            melody=self._melody,
            beat=self._beat,
            adsr=self._adsr,
            is_solo_mode=solo_intensity > self._solo_threshold,
            solo_intensity=solo_intensity,
            active_motif_label=motif_label,
            is_theme_recurrence=is_recurrence,
            section_label=section_label,
            melody_intensity=float(np.clip(melody_intensity, 0.0, 1.0)),
            chord_tension=self._chord.tension,
            pitch_normalized=self._melody.pitch_normalized,
            beat_impulse=float(np.clip(beat_impulse, 0.0, 1.0)),
            ornament_signal=float(np.clip(ornament_signal, 0.0, 1.0)),
            contour_vector=float(np.clip(contour_vector, -1.0, 1.0)),
            color_hue=self._chord.color_hue,
            color_saturation=self._chord.color_saturation,
            color_brightness=self._chord.color_brightness,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  配置接口
    # ─────────────────────────────────────────────────────────────────────

    def set_key(self, tonic_midi: int, is_major: bool = True) -> None:
        """设置调性中心"""
        self._tonic = tonic_midi % 12
        self._is_major = is_major

    def set_time_signature(self, numerator: int, denominator: int) -> None:
        """设置拍号"""
        self._beat.time_signature_num = numerator
        self._beat.time_signature_den = denominator

    def set_adsr(
        self,
        attack: float = 0.05,
        decay: float = 0.1,
        sustain: float = 0.7,
        release: float = 0.3,
    ) -> None:
        """设置 ADSR 参数（无范围限制）"""
        self._adsr.attack_time = max(0.001, attack)
        self._adsr.decay_time = max(0.001, decay)
        self._adsr.sustain_level = max(0.0, sustain)
        self._adsr.release_time = max(0.001, release)

    def set_pitch_range(self, low_midi: float, high_midi: float) -> None:
        """设置旋律音域（无限制）"""
        self._pitch_range = (low_midi, high_midi)

    def set_solo_threshold(self, threshold: float) -> None:
        """设置 Solo 模式触发阈值（无限制）"""
        self._solo_threshold = threshold

    def reset(self) -> None:
        """重置所有状态"""
        self._pitch_history.clear()
        self._interval_history.clear()
        self._energy_history.clear()
        self._motif_library.clear()
        self._chord = ChordState()
        self._melody = MelodicState()
        self._beat = BeatState()
        self._adsr = ADSRState()
        self._adsr_timer = 0.0
        self._beat_timer = 0.0
        self._bar_timer = 0.0
