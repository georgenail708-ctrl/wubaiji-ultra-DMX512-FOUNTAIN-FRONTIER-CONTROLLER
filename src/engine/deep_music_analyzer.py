"""
deep_music_analyzer.py — 深度音乐结构分析器
==============================================
在音频文件加载后对全曲进行预分析，提取：
  - 乐句边界（phrase boundary）
  - 问答句法结构（antecedent / consequent）
  - 情绪弧线（emotional arc：tension / release）
  - build-up 渐进段落
  - 小节能量轮廓（bar energy profile）
  - 乐器织体密度（texture density）

实时播放时，通过当前时间戳查询预分析结果，
向编舞引擎提供高层音乐语义。
"""
from __future__ import annotations

import math
import time
import threading
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  枚举 & 数据结构
# ═══════════════════════════════════════════════════════════════════════════

class PhraseRole(Enum):
    """乐句角色（问答结构）"""
    ANTECEDENT  = "antecedent"   # 问句：上行/开放/紧张
    CONSEQUENT  = "consequent"   # 答句：下行/收束/释放
    TRANSITION  = "transition"   # 过渡句：连接两个主题
    CLIMAX      = "climax"       # 高潮句：能量顶点
    NEUTRAL     = "neutral"      # 中性句：无明确方向

class EmotionArc(Enum):
    """情绪弧线状态"""
    RISING      = "rising"       # 上升（紧张积累）
    PEAK        = "peak"         # 顶点
    FALLING     = "falling"      # 下降（释放）
    VALLEY      = "valley"       # 谷底（平静）
    STABLE      = "stable"       # 平稳

@dataclass
class PhraseInfo:
    """一个乐句的完整信息"""
    start_t: float = 0.0
    end_t: float = 0.0
    role: PhraseRole = PhraseRole.NEUTRAL
    energy_mean: float = 0.0
    energy_peak: float = 0.0
    centroid_mean: float = 0.0
    centroid_trend: float = 0.0   # 正=上行，负=下行
    flux_mean: float = 0.0
    tension: float = 0.0          # 0..1 紧张度
    release: float = 0.0          # 0..1 释放度
    texture_density: float = 0.0  # 0..1 织体密度
    bar_count: int = 0
    phrase_index: int = 0
    pair_index: int = -1          # 配对的问/答句索引

    @property
    def duration(self) -> float:
        return max(0.0, self.end_t - self.start_t)

    @property
    def mid_t(self) -> float:
        return (self.start_t + self.end_t) / 2.0

@dataclass
class BarInfo:
    """一个小节的信息"""
    bar_number: int = 0
    start_t: float = 0.0
    end_t: float = 0.0
    energy: float = 0.0
    centroid: float = 0.0
    flux: float = 0.0
    kick_strength: float = 0.0
    snare_strength: float = 0.0
    melody_strength: float = 0.0
    beat_count: int = 4
    is_buildup: bool = False
    buildup_progress: float = 0.0  # 0..1，在buildup段中的进度

@dataclass
class MusicAnalysisResult:
    """全曲预分析结果"""
    duration: float = 0.0
    bpm_mean: float = 120.0
    bpm_std: float = 0.0
    bars: List[BarInfo] = field(default_factory=list)
    phrases: List[PhraseInfo] = field(default_factory=list)
    section_timeline: List[Tuple[float, float, str]] = field(default_factory=list)
    # 情绪弧线：每0.1秒一个采样点
    emotion_arc: List[Tuple[float, EmotionArc, float]] = field(default_factory=list)
    # build-up段落列表：(start_t, end_t, intensity)
    buildups: List[Tuple[float, float, float]] = field(default_factory=list)
    # 全曲能量轮廓（归一化）
    energy_profile: List[float] = field(default_factory=list)
    energy_profile_dt: float = 0.1
    # 分析完成标志
    is_complete: bool = False
    analysis_time: float = 0.0

# ═══════════════════════════════════════════════════════════════════════════
#  深度音乐分析器
# ═══════════════════════════════════════════════════════════════════════════

class DeepMusicAnalyzer:
    """
    对全曲音频进行深度预分析。
    
    使用方式：
      analyzer = DeepMusicAnalyzer()
      analyzer.analyze_from_msa_history(msa_events)  # 从MSA历史事件分析
      # 或
      analyzer.analyze_realtime_accumulate(event)    # 实时累积分析
      
      # 查询当前时刻的音乐语义
      ctx = analyzer.query(current_t)
    """

    def __init__(self):
        self._result: Optional[MusicAnalysisResult] = None
        self._lock = threading.Lock()

        # 实时累积缓冲（用于实时分析模式）
        # ★ R193/P1-11（AUD-01）：_rt_events 全仓无读取方（仅 append/clear）——
        #   纯泄漏（实测口径 ~60KB/s ≈ 200MB/小时）。deque 封顶 2 分钟 @40Hz。
        from collections import deque
        self._rt_events: "deque[dict]" = deque(maxlen=4800)
        self._rt_bars: List[BarInfo] = []
        self._rt_phrases: List[PhraseInfo] = []
        self._rt_current_bar: Optional[BarInfo] = None
        self._rt_bar_events: List[dict] = []
        self._rt_phrase_events: List[dict] = []
        self._rt_phrase_start_t: float = 0.0
        self._rt_phrase_bar_count: int = 0
        self._rt_last_bar_number: int = -1
        self._rt_buildup_start: Optional[float] = None
        self._rt_buildups: List[Tuple[float, float, float]] = []

        # 情绪弧线追踪（实时）
        self._tension_history: deque = deque(maxlen=200)
        self._emotion_arc_rt: List[Tuple[float, EmotionArc, float]] = []
        self._last_emotion_t: float = 0.0

        # 乐句配对状态
        self._pending_antecedent: Optional[PhraseInfo] = None

    # ─────────────────────────────────────────────────────────────────────
    #  实时累积分析（主要接口）
    # ─────────────────────────────────────────────────────────────────────

    def on_msa_event(self, event) -> None:
        """
        接收来自 MusicStructureAnalyzer 的 analysis_tick 事件，
        实时累积分析结果。
        """
        t = getattr(event, 't', 0.0)
        rms = getattr(event, 'rms', 0.0)
        bands = list(getattr(event, 'bands', [0.0] * 8))
        bpm = float(getattr(event, 'bpm', 120.0))
        beat_phase = float(getattr(event, 'beat_phase', 0.0))
        is_beat = bool(getattr(event, 'is_beat', False))
        bar_number = int(getattr(event, 'bar_number', 0))
        beat_in_bar = int(getattr(event, 'beat_in_bar', 0))
        section_label = getattr(event, 'section_label', 'unknown')
        instruments = dict(getattr(event, 'instruments', {}))
        energy_short = float(getattr(event, 'energy_short', rms))
        energy_long = float(getattr(event, 'energy_long', rms))
        spectral_centroid = float(getattr(event, 'spectral_centroid', 0.5))
        novelty = float(getattr(event, 'novelty', 0.0))
        extras = getattr(event, 'extras', {}) or {}
        buildup_intensity = float(extras.get('buildup_intensity', extras.get('buildup', 0.0)))
        buildup_active = bool(extras.get('buildup_active', buildup_intensity > 0.25))

        ev_dict = {
            't': t, 'rms': rms, 'bands': bands, 'bpm': bpm,
            'beat_phase': beat_phase, 'is_beat': is_beat,
            'bar_number': bar_number, 'beat_in_bar': beat_in_bar,
            'section_label': section_label, 'instruments': instruments,
            'energy_short': energy_short, 'energy_long': energy_long,
            'centroid': spectral_centroid, 'novelty': novelty,
            'buildup_active': buildup_active, 'buildup_intensity': buildup_intensity,
            'section_confidence': float(extras.get('section_confidence', extras.get('confidence', 0.0))),
            'onset_density': float(extras.get('onset_density', 0.0)),
            'spectral_flux': float(extras.get('spectral_flux', extras.get('flux', novelty))),
        }

        with self._lock:
            self._rt_events.append(ev_dict)
            self._update_bar(ev_dict)
            self._update_tension(ev_dict)
            self._update_emotion_arc(ev_dict, t)
            self._update_buildup(ev_dict, t)

    def _update_bar(self, ev: dict) -> None:
        """检测小节边界，累积小节信息"""
        bar_number = ev['bar_number']
        beat_in_bar = ev['beat_in_bar']
        t = ev['t']

        # 新小节开始
        if bar_number != self._rt_last_bar_number:
            # 完成上一个小节
            if self._rt_current_bar is not None and self._rt_bar_events:
                self._finalize_bar(self._rt_current_bar, self._rt_bar_events)
                self._rt_bars.append(self._rt_current_bar)
                self._rt_phrase_events.extend(self._rt_bar_events)
                self._rt_phrase_bar_count += 1

                # 检测乐句边界（每2或4小节一个乐句）
                if self._rt_phrase_bar_count >= 4:
                    self._finalize_phrase(t)

            # 开始新小节
            self._rt_current_bar = BarInfo(
                bar_number=bar_number,
                start_t=t,
                end_t=t + 60.0 / max(ev['bpm'], 60.0) * 4,
            )
            self._rt_bar_events = []
            self._rt_last_bar_number = bar_number

            # 如果还没有乐句开始时间，记录
            if self._rt_phrase_bar_count == 0:
                self._rt_phrase_start_t = t

        if self._rt_current_bar is not None:
            self._rt_bar_events.append(ev)

    def _finalize_bar(self, bar: BarInfo, events: List[dict]) -> None:
        """从事件列表计算小节统计信息"""
        if not events:
            return
        rms_vals = [e['rms'] for e in events]
        centroid_vals = [e['centroid'] for e in events]
        flux_vals = [e.get('novelty', 0.0) for e in events]
        inst_vals = [e['instruments'] for e in events]

        bar.energy = float(np.mean(rms_vals))
        bar.centroid = float(np.mean(centroid_vals))
        bar.flux = float(np.mean(flux_vals))
        bar.end_t = events[-1]['t']
        bar.kick_strength = float(np.mean([i.get('kick', 0.0) for i in inst_vals]))
        bar.snare_strength = float(np.mean([i.get('snare', 0.0) for i in inst_vals]))
        bar.melody_strength = float(np.mean([i.get('melody', 0.0) for i in inst_vals]))
        bar.beat_count = 4

        # buildup标记
        buildup_flags = [e.get('buildup_active', False) for e in events]
        bar.is_buildup = sum(buildup_flags) > len(buildup_flags) * 0.5
        if bar.is_buildup:
            bar.buildup_progress = float(np.mean([e.get('buildup_intensity', 0.0) for e in events]))

    def _finalize_phrase(self, end_t: float) -> None:
        """完成一个乐句，分析其角色"""
        if not self._rt_phrase_events:
            return

        events = self._rt_phrase_events
        start_t = self._rt_phrase_start_t

        rms_vals = [e['rms'] for e in events]
        centroid_vals = [e['centroid'] for e in events]
        flux_vals = [e.get('novelty', 0.0) for e in events]
        inst_vals = [e['instruments'] for e in events]

        # 计算重心趋势（前半 vs 后半）
        half = len(centroid_vals) // 2
        if half > 0:
            centroid_trend = float(np.mean(centroid_vals[half:])) - float(np.mean(centroid_vals[:half]))
        else:
            centroid_trend = 0.0

        energy_mean = float(np.mean(rms_vals))
        energy_peak = float(np.max(rms_vals))

        # 计算紧张度：基于能量上升、重心上升、flux增加
        energy_rise = float(np.mean(rms_vals[half:])) - float(np.mean(rms_vals[:half])) if half > 0 else 0.0
        tension = float(np.clip(
            0.3 * max(0, centroid_trend) * 5 +
            0.4 * max(0, energy_rise) * 10 +
            0.3 * float(np.mean(flux_vals)),
            0.0, 1.0
        ))

        # 计算释放度：能量下降、重心下降
        release = float(np.clip(
            0.5 * max(0, -centroid_trend) * 5 +
            0.5 * max(0, -energy_rise) * 10,
            0.0, 1.0
        ))

        # 织体密度：乐器活跃度
        texture_density = float(np.mean([
            sum(i.values()) / max(len(i), 1) for i in inst_vals
        ]))

        # 判断乐句角色
        role = self._classify_phrase_role(
            centroid_trend, energy_rise, tension, release,
            energy_mean, texture_density
        )

        phrase = PhraseInfo(
            start_t=start_t,
            end_t=end_t,
            role=role,
            energy_mean=energy_mean,
            energy_peak=energy_peak,
            centroid_mean=float(np.mean(centroid_vals)),
            centroid_trend=centroid_trend,
            flux_mean=float(np.mean(flux_vals)),
            tension=tension,
            release=release,
            texture_density=texture_density,
            bar_count=self._rt_phrase_bar_count,
            phrase_index=len(self._rt_phrases),
        )

        # 问答配对
        self._pair_phrase(phrase)

        self._rt_phrases.append(phrase)

        # 重置乐句缓冲
        self._rt_phrase_events = []
        self._rt_phrase_bar_count = 0
        self._rt_phrase_start_t = end_t

    def _classify_phrase_role(
        self, centroid_trend: float, energy_rise: float,
        tension: float, release: float,
        energy_mean: float, texture_density: float
    ) -> PhraseRole:
        """根据特征判断乐句角色"""
        # 高潮句：能量峰值 + 高织体密度
        if energy_mean > 0.6 and texture_density > 0.5:
            return PhraseRole.CLIMAX

        # 问句：上行趋势（重心上升或能量上升）
        if centroid_trend > 0.02 or energy_rise > 0.05:
            return PhraseRole.ANTECEDENT

        # 答句：下行趋势（重心下降或能量下降）
        if centroid_trend < -0.02 or energy_rise < -0.05:
            return PhraseRole.CONSEQUENT

        # 过渡句：高flux + 中等能量
        if texture_density > 0.3 and 0.2 < energy_mean < 0.5:
            return PhraseRole.TRANSITION

        return PhraseRole.NEUTRAL

    def _pair_phrase(self, phrase: PhraseInfo) -> None:
        """将问句和答句配对"""
        if phrase.role == PhraseRole.ANTECEDENT:
            self._pending_antecedent = phrase
        elif phrase.role == PhraseRole.CONSEQUENT and self._pending_antecedent is not None:
            # 配对
            ant = self._pending_antecedent
            ant.pair_index = phrase.phrase_index
            phrase.pair_index = ant.phrase_index
            self._pending_antecedent = None

    def _update_tension(self, ev: dict) -> None:
        """更新紧张度历史"""
        rms = ev['rms']
        centroid = ev['centroid']
        novelty = ev.get('novelty', 0.0)
        buildup_intensity = ev.get('buildup_intensity', 0.0)

        tension = float(np.clip(
            0.3 * rms +
            0.2 * centroid +
            0.2 * novelty +
            0.3 * buildup_intensity,
            0.0, 1.0
        ))
        self._tension_history.append(tension)

    def _update_emotion_arc(self, ev: dict, t: float) -> None:
        """每0.5秒更新一次情绪弧线"""
        if t - self._last_emotion_t < 0.5:
            return
        self._last_emotion_t = t

        if len(self._tension_history) < 4:
            return

        tension_list = list(self._tension_history)
        current_tension = float(np.mean(tension_list[-4:]))
        prev_tension = float(np.mean(tension_list[-8:-4])) if len(tension_list) >= 8 else current_tension
        delta = current_tension - prev_tension

        if delta > 0.03:
            arc = EmotionArc.RISING
        elif delta < -0.03:
            arc = EmotionArc.FALLING
        elif current_tension > 0.65:
            arc = EmotionArc.PEAK
        elif current_tension < 0.25:
            arc = EmotionArc.VALLEY
        else:
            arc = EmotionArc.STABLE

        self._emotion_arc_rt.append((t, arc, current_tension))

    def _update_buildup(self, ev: dict, t: float) -> None:
        """追踪build-up段落"""
        buildup_active = ev.get('buildup_active', False)
        buildup_intensity = ev.get('buildup_intensity', 0.0)

        if buildup_active and self._rt_buildup_start is None:
            self._rt_buildup_start = t
        elif not buildup_active and self._rt_buildup_start is not None:
            # build-up结束
            duration = t - self._rt_buildup_start
            if duration > 1.0:  # 至少1秒才算有效buildup
                self._rt_buildups.append((self._rt_buildup_start, t, buildup_intensity))
            self._rt_buildup_start = None

    # ─────────────────────────────────────────────────────────────────────
    #  查询接口（实时播放时调用）
    # ─────────────────────────────────────────────────────────────────────

    def query(self, t: float) -> "MusicContext":
        """
        查询当前时刻的音乐语义上下文。
        这是编舞引擎的主要输入接口。
        """
        with self._lock:
            return self._build_context(t)

    def _build_context(self, t: float) -> "MusicContext":
        """构建当前时刻的音乐上下文"""
        ctx = MusicContext()
        ctx.t = t

        # 当前乐句
        current_phrase = self._find_phrase_at(t)
        if current_phrase is not None:
            ctx.phrase_role = current_phrase.role
            ctx.phrase_tension = current_phrase.tension
            ctx.phrase_release = current_phrase.release
            ctx.phrase_texture_density = current_phrase.texture_density
            ctx.phrase_progress = (t - current_phrase.start_t) / max(current_phrase.duration, 0.01)
            ctx.phrase_centroid_trend = current_phrase.centroid_trend

            # 查找配对的另一半
            if current_phrase.pair_index >= 0 and current_phrase.pair_index < len(self._rt_phrases):
                partner = self._rt_phrases[current_phrase.pair_index]
                ctx.has_phrase_pair = True
                ctx.phrase_pair_role = partner.role
            
            # 下一个乐句预判
            next_phrase = self._find_next_phrase(current_phrase)
            if next_phrase is not None:
                ctx.next_phrase_role = next_phrase.role
                ctx.next_phrase_tension = next_phrase.tension
                # 距离下一乐句的时间
                ctx.time_to_next_phrase = next_phrase.start_t - t
        
        # 当前小节
        current_bar = self._find_bar_at(t)
        if current_bar is not None:
            ctx.bar_number = current_bar.bar_number
            ctx.bar_energy = current_bar.energy
            ctx.bar_is_buildup = current_bar.is_buildup
            ctx.bar_buildup_progress = current_bar.buildup_progress
            ctx.bar_progress = (t - current_bar.start_t) / max(current_bar.end_t - current_bar.start_t, 0.01)
            ctx.bar_kick_strength = current_bar.kick_strength
            ctx.bar_snare_strength = current_bar.snare_strength
            ctx.bar_melody_strength = current_bar.melody_strength

        # 情绪弧线
        arc_state = self._find_emotion_arc_at(t)
        if arc_state is not None:
            ctx.emotion_arc, ctx.tension_level = arc_state

        # build-up状态
        buildup = self._find_buildup_at(t)
        if buildup is not None:
            start, end, intensity = buildup
            ctx.in_buildup = True
            ctx.buildup_progress = (t - start) / max(end - start, 0.01)
            ctx.buildup_intensity = intensity

        # 全局能量轮廓位置
        ctx.global_energy_position = self._estimate_energy_position(t)

        # 乐句历史（用于判断上下文）
        ctx.recent_phrase_roles = [p.role for p in self._rt_phrases[-4:]]

        return ctx

    def _find_phrase_at(self, t: float) -> Optional[PhraseInfo]:
        """找到包含时间t的乐句"""
        for phrase in reversed(self._rt_phrases):
            if phrase.start_t <= t < phrase.end_t:
                return phrase
        # 如果t在最后一个乐句之后，返回最后一个
        if self._rt_phrases and t >= self._rt_phrases[-1].start_t:
            return self._rt_phrases[-1]
        return None

    def _find_next_phrase(self, current: PhraseInfo) -> Optional[PhraseInfo]:
        """找到当前乐句的下一个乐句"""
        idx = current.phrase_index + 1
        if idx < len(self._rt_phrases):
            return self._rt_phrases[idx]
        return None

    def _find_bar_at(self, t: float) -> Optional[BarInfo]:
        """找到包含时间t的小节"""
        for bar in reversed(self._rt_bars):
            if bar.start_t <= t:
                return bar
        return None

    def _find_emotion_arc_at(self, t: float) -> Optional[Tuple[EmotionArc, float]]:
        """找到时间t的情绪弧线状态"""
        for i in range(len(self._emotion_arc_rt) - 1, -1, -1):
            arc_t, arc, tension = self._emotion_arc_rt[i]
            if arc_t <= t:
                return arc, tension
        return None

    def _find_buildup_at(self, t: float) -> Optional[Tuple[float, float, float]]:
        """找到包含时间t的buildup段落"""
        for buildup in self._rt_buildups:
            start, end, intensity = buildup
            if start <= t <= end:
                return buildup
        # 检查正在进行的buildup
        if self._rt_buildup_start is not None:
            return (self._rt_buildup_start, t + 1.0, 0.5)
        return None

    def _estimate_energy_position(self, t: float) -> float:
        """
        估算当前时刻在全曲能量轮廓中的位置（0=最低点，1=最高点）。
        基于已知的小节能量历史。
        """
        if not self._rt_bars:
            return 0.5
        energies = [b.energy for b in self._rt_bars]
        if not energies:
            return 0.5
        current_bar = self._find_bar_at(t)
        if current_bar is None:
            return 0.5
        e_min = min(energies)
        e_max = max(energies)
        if e_max <= e_min:
            return 0.5
        return float(np.clip((current_bar.energy - e_min) / (e_max - e_min), 0.0, 1.0))

    # ─────────────────────────────────────────────────────────────────────
    #  状态查询
    # ─────────────────────────────────────────────────────────────────────

    def get_phrase_count(self) -> int:
        with self._lock:
            return len(self._rt_phrases)

    def get_bar_count(self) -> int:
        with self._lock:
            return len(self._rt_bars)

    def get_buildup_count(self) -> int:
        with self._lock:
            return len(self._rt_buildups)

    def reset(self) -> None:
        """重置所有分析状态（新曲目加载时调用）"""
        with self._lock:
            self._rt_events.clear()
            self._rt_bars.clear()
            self._rt_phrases.clear()
            self._rt_current_bar = None
            self._rt_bar_events.clear()
            self._rt_phrase_events.clear()
            self._rt_phrase_start_t = 0.0
            self._rt_phrase_bar_count = 0
            self._rt_last_bar_number = -1
            self._rt_buildup_start = None
            self._rt_buildups.clear()
            self._tension_history.clear()
            self._emotion_arc_rt.clear()
            self._last_emotion_t = 0.0
            self._pending_antecedent = None
            self._result = None

# ═══════════════════════════════════════════════════════════════════════════
#  音乐上下文（编舞引擎的输入）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MusicContext:
    """
    当前时刻的完整音乐语义上下文。
    这是编舞引擎做决策的核心输入。
    """
    t: float = 0.0

    # ── 乐句信息 ──
    phrase_role: PhraseRole = PhraseRole.NEUTRAL
    phrase_tension: float = 0.5
    phrase_release: float = 0.5
    phrase_texture_density: float = 0.5
    phrase_progress: float = 0.0        # 0..1，在当前乐句中的进度
    phrase_centroid_trend: float = 0.0  # 正=上行，负=下行

    # ── 乐句问答配对 ──
    has_phrase_pair: bool = False
    phrase_pair_role: PhraseRole = PhraseRole.NEUTRAL

    # ── 下一乐句预判 ──
    next_phrase_role: PhraseRole = PhraseRole.NEUTRAL
    next_phrase_tension: float = 0.5
    time_to_next_phrase: float = 999.0

    # ── 小节信息 ──
    bar_number: int = 0
    bar_energy: float = 0.5
    bar_is_buildup: bool = False
    bar_buildup_progress: float = 0.0
    bar_progress: float = 0.0          # 0..1，在当前小节中的进度

    # ── 情绪弧线 ──
    emotion_arc: EmotionArc = EmotionArc.STABLE
    tension_level: float = 0.5

    # ── Build-up ──
    in_buildup: bool = False
    buildup_progress: float = 0.0
    buildup_intensity: float = 0.0

    # ── 全局位置 ──
    global_energy_position: float = 0.5

    # ── 小节乐器强度（供 motif 乐器亲和力使用）──
    bar_kick_strength: float = 0.0
    bar_snare_strength: float = 0.0
    bar_melody_strength: float = 0.0

    # ── 历史上下文 ──
    recent_phrase_roles: List[PhraseRole] = field(default_factory=list)

    # ─────────────────────────────────────────────────────────────────────
    #  派生属性
    # ─────────────────────────────────────────────────────────────────────

    @property
    def is_question_phrase(self) -> bool:
        """当前是问句（上行/紧张积累）"""
        return self.phrase_role == PhraseRole.ANTECEDENT

    @property
    def is_answer_phrase(self) -> bool:
        """当前是答句（下行/释放）"""
        return self.phrase_role == PhraseRole.CONSEQUENT

    @property
    def is_climax(self) -> bool:
        """当前是高潮句"""
        return self.phrase_role == PhraseRole.CLIMAX

    @property
    def is_transition(self) -> bool:
        """当前是过渡句"""
        return self.phrase_role == PhraseRole.TRANSITION

    @property
    def approaching_climax(self) -> bool:
        """即将到达高潮（下一句是高潮或当前是buildup）"""
        return (self.next_phrase_role == PhraseRole.CLIMAX or
                (self.in_buildup and self.buildup_progress > 0.7))

    @property
    def just_released(self) -> bool:
        """刚刚完成释放（当前是答句且在后半段）"""
        return self.is_answer_phrase and self.phrase_progress > 0.5

    @property
    def energy_rising(self) -> bool:
        return self.emotion_arc == EmotionArc.RISING

    @property
    def energy_falling(self) -> bool:
        return self.emotion_arc == EmotionArc.FALLING

    @property
    def at_peak(self) -> bool:
        return self.emotion_arc == EmotionArc.PEAK

    @property
    def at_valley(self) -> bool:
        return self.emotion_arc == EmotionArc.VALLEY

    def suggested_complexity_multiplier(self) -> float:
        """
        根据音乐上下文建议的复杂度乘数（0.2..2.0）。
        编舞引擎将此与用户设定的基础复杂度相乘。
        """
        base = 1.0

        # 高潮：最高复杂度
        if self.is_climax or self.at_peak:
            base = 2.0
        # Build-up：随进度递增
        elif self.in_buildup:
            base = 0.5 + 1.5 * self.buildup_progress
        # 问句：中高复杂度（积累）
        elif self.is_question_phrase:
            base = 1.0 + 0.5 * self.phrase_progress
        # 答句：递减复杂度（释放）
        elif self.is_answer_phrase:
            base = 1.2 * (1.0 - 0.5 * self.phrase_progress)
        # 过渡：中等
        elif self.is_transition:
            base = 1.2
        # 谷底：最低复杂度
        elif self.at_valley:
            base = 0.3

        return float(np.clip(base, 0.2, 2.0))

    def suggested_device_density(self) -> float:
        """
        建议的设备激活密度（0..1）。
        0=最少设备，1=全部设备。
        """
        if self.is_climax or self.at_peak:
            return 1.0
        if self.in_buildup:
            return 0.3 + 0.7 * self.buildup_progress
        if self.is_question_phrase:
            return 0.4 + 0.3 * self.phrase_progress
        if self.is_answer_phrase:
            return 0.7 * (1.0 - 0.3 * self.phrase_progress)
        if self.at_valley:
            return 0.2
        return 0.5

    def suggested_transition_urgency(self) -> float:
        """
        建议的过渡紧迫度（0..1）。
        值越高，动作切换越快、越剧烈。
        """
        if self.approaching_climax:
            return 0.9
        if self.in_buildup:
            return 0.5 + 0.4 * self.buildup_progress
        if self.is_question_phrase:
            return 0.6
        if self.is_answer_phrase:
            return 0.3
        return 0.5


# ═══════════════════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════════════════

_global_deep_analyzer: Optional[DeepMusicAnalyzer] = None

def get_deep_music_analyzer() -> DeepMusicAnalyzer:
    global _global_deep_analyzer
    if _global_deep_analyzer is None:
        _global_deep_analyzer = DeepMusicAnalyzer()
    return _global_deep_analyzer
