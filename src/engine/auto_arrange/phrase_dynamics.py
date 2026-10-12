"""
phrase_dynamics.py — 乐句级力度弧线引擎
从标杆数据逆向提炼：To The Moon 81 clip 的渐进叠加→峰值→回落模式。

核心概念（对标真人编曲手法）：
  1. 力度弧线 — 每个乐句内有自然的 crescendo/decrescendo，而非恒定 level
  2. 渐进叠加 — clip 数量随能量逐步积累，峰值后回落，非全开全关
  3. 呼吸感 — 段落交接有明确的"留白"过渡，不留声部残响
  4. 终止式 — 乐句结尾有渐慢 + 收束手势（标杆验证：尾声渐弱+终端暂停）

纯 core，无 UI/引擎依赖。被 voice_model.arrange_voices 调用以生成精细力度曲线。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── 数据类型 ──────────────────────────────────────────────────────

@dataclass
class PhraseDynamics:
    """一个乐句内的力度弧线描述。"""
    phrase_start_s: float = 0.0
    phrase_end_s: float = 0.0
    duration_s: float = 0.0

    # 力度弧线参数（0~1，可 >1 表示超常强调）
    attack_level: float = 0.5     # 起音力度
    peak_level: float = 0.8       # 峰值力度
    release_level: float = 0.3    # 释放力度

    # 弧线形状
    attack_shape: str = "linear"  # "linear" | "ease_in" | "ease_out" | "s_curve"
    decay_shape: str = "ease_out" # 衰减形状
    peak_position: float = 0.62   # 峰值在乐句中的位置（0~1, 0.62=黄金比例附近）

    # 终止式
    has_cadence: bool = False     # 是否有终止式
    ritardando_factor: float = 1.0 # 渐慢因子（1.0=不变速, <1.0=渐慢）


@dataclass
class TensionArc:
    """跨多个乐句的宏观张力弧线。"""
    sections: List[str] = field(default_factory=list)
    tensions: List[float] = field(default_factory=list)  # 0~1 张力值
    peak_section_idx: int = -1
    arc_type: str = "arch"  # "arch" | "climbing" | "wave" | "flat"


# ── 乐句力度引擎 ─────────────────────────────────────────────────

class PhraseDynamicsEngine:
    """乐句级力度弧线计算引擎。

    对标数据验证：
      - 前奏(能量0.18): 力度 0.3→0.3, 无峰值, 释放平缓
      - 主歌(能量0.30): 力度 0.35→0.5→0.3, 小弧
      - 副歌(能量0.55): 力度 0.4→0.8→0.35, 大弧, 峰值偏后
      - 高潮(能量0.77): 力度 0.5→0.95→0.4, 全弧, 多层叠加
      - 尾声(能量0.3): 力度 0.3→0.2→0.1, 渐慢渐弱, 有终止式
    """

    @staticmethod
    def from_energy(
        section_energy: float,
        duration_s: float,
        *,
        is_last_section: bool = False,
        is_first_section: bool = False,
    ) -> PhraseDynamics:
        """从音乐段能量推断乐句力度弧线。

        Args:
            section_energy: 段平均能量 (0~1, 标杆实测范围 0.18~0.77)
            duration_s: 段时长(秒)
            is_last_section: 是否为最后一段(触发终止式)
            is_first_section: 是否为第一段(触发渐入)
        """
        d = PhraseDynamics(duration_s=duration_s)

        # ── 力度等级映射（对标标杆能量→力道对应）──
        if section_energy < 0.25:
            # 前奏/尾声级：轻柔，小弧或无弧
            d.attack_level = 0.25
            d.peak_level = 0.30 + section_energy * 0.15
            d.release_level = 0.20
            d.peak_position = 0.50
            d.attack_shape = "ease_in"
            d.decay_shape = "ease_out"
        elif section_energy < 0.40:
            # 主歌级：中等，有弧但不剧烈
            d.attack_level = 0.30
            d.peak_level = 0.45 + section_energy * 0.30
            d.release_level = 0.25
            d.peak_position = 0.55
            d.attack_shape = "ease_in"
            d.decay_shape = "ease_out"
        elif section_energy < 0.60:
            # 副歌级：明显弧线，峰值偏后
            d.attack_level = 0.35
            d.peak_level = 0.65 + section_energy * 0.25
            d.release_level = 0.30
            d.peak_position = 0.62
            d.attack_shape = "linear"
            d.decay_shape = "ease_out"
        elif section_energy < 0.75:
            # 桥接/展开级：大弧，能量聚集
            d.attack_level = 0.40
            d.peak_level = 0.80 + section_energy * 0.15
            d.release_level = 0.35
            d.peak_position = 0.68
            d.attack_shape = "s_curve"
            d.decay_shape = "ease_out"
        else:
            # 高潮/最高能级：全弧，多层叠加
            # 对标: 能量0.77时有20个clip同时活跃
            d.attack_level = 0.50
            d.peak_level = 0.90 + (section_energy - 0.75) * 0.40
            d.release_level = 0.40
            d.peak_position = 0.72
            d.attack_shape = "s_curve"
            d.decay_shape = "linear"

        # ── 首段渐入 ──
        if is_first_section:
            d.attack_level *= 0.3  # 极轻柔起
            d.attack_shape = "ease_in"

        # ── 尾声终止式 ──
        if is_last_section:
            d.release_level = 0.05  # 近乎消失
            d.decay_shape = "ease_out"
            d.has_cadence = True
            d.ritardando_factor = 0.60  # 渐慢 40%

        return d

    @staticmethod
    def level_at_time(dyn: PhraseDynamics, t_rel: float) -> float:
        """计算乐句内给定相对时间的力度值。

        Args:
            dyn: 乐句力度描述
            t_rel: 相对时间 (0~1, 0=乐句起点, 1=乐句终点)
        """
        t = max(0.0, min(1.0, t_rel))
        pk = min(max(dyn.peak_position, 1e-6), 1.0 - 1e-6)

        if t <= pk:
            # 起音→峰值段
            progress = t / pk
            return _interp_shape(dyn.attack_level, dyn.peak_level, progress, dyn.attack_shape)
        else:
            # 峰值→释放段
            progress = (t - pk) / (1.0 - pk)
            return _interp_shape(dyn.peak_level, dyn.release_level, progress, dyn.decay_shape)

    @staticmethod
    def effective_speed(dyn: PhraseDynamics, t_rel: float, base_speed: float) -> float:
        """计算乐句内给定时间的效果速度（包含终止式渐慢）。

        Args:
            dyn: 乐句力度描述
            t_rel: 相对时间
            base_speed: 基准速度
        """
        speed = base_speed

        # 终止式渐慢
        if dyn.has_cadence and t_rel > 0.85:
            cadence_progress = (t_rel - 0.85) / 0.15
            rit = 1.0 - (1.0 - dyn.ritardando_factor) * cadence_progress
            speed *= rit

        return speed


# ── 宏观张力弧线 ─────────────────────────────────────────────────

class TensionArcBuilder:
    """跨乐句张力弧线构建器。

    对标数据模式（To The Moon 20 段）：
      [0-4s]    张力0.18 → intro
      [4-19s]   张力0.30 → verse, 开始叠加
      [19-23s]  张力0.38 → pre-chorus, 加速叠加
      [23-31s]  张力0.55 → chorus, seq pattern 出现
      [31-38s]  张力0.77 → 🔥 PEAK, 20 clips, motor transitions
      [38-50s]  张力0.77 → peak sustained
      [50-69s]  张力0.62 → 回落但仍高
      ...
      [169-183s] 张力0.33 → outro, fade out
    """

    @staticmethod
    def build(
        section_energies: List[float],
        section_labels: Optional[List[str]] = None,
    ) -> TensionArc:
        """从各段能量构建张力弧线。

        Args:
            section_energies: 各段平均能量
            section_labels: 各段标签（可选：intro/verse/chorus...）
        """
        n = len(section_energies)
        if n == 0:
            return TensionArc()

        arc = TensionArc(
            sections=section_labels or [f"s{i}" for i in range(n)],
            tensions=section_energies.copy(),
            peak_section_idx=int(max(range(n), key=lambda i: section_energies[i])),
        )

        # ── 弧线类型判定 ──
        peak_idx = arc.peak_section_idx
        energies = section_energies

        # 从起点到峰值的爬升幅度
        climb = energies[peak_idx] - energies[0] if peak_idx > 0 else 0

        # 从峰值到终点的下降幅度
        descent = energies[peak_idx] - energies[-1] if peak_idx < n - 1 else 0

        if climb > 0.3 and descent > 0.2:
            arc.arc_type = "arch"       # 完整拱形：上升→峰值→下降
        elif climb > 0.3:
            arc.arc_type = "climbing"   # 持续爬升（可能是截断的音乐）
        elif max(energies) - min(energies) < 0.15:
            arc.arc_type = "flat"       # 持续高能或持续低能
        else:
            arc.arc_type = "wave"       # 波浪形

        return arc

    @staticmethod
    def section_weight(tension_arc: TensionArc, section_idx: int) -> float:
        """计算某段在张力弧线中的权重（用于决定该段投入多少设备和变化）。

        峰值段 = 1.0, 离峰值越远越轻。
        """
        n = len(tension_arc.tensions)
        if n == 0:
            return 0.5

        peak = tension_arc.peak_section_idx
        dist = abs(section_idx - peak) / max(n - 1, 1)

        # 用高斯衰减（峰值处=1, 2σ外≈0.1）
        sigma = 0.35
        weight = math.exp(-(dist ** 2) / (2 * sigma ** 2))

        return max(0.08, weight)


# ── 内部辅助 ─────────────────────────────────────────────────────

def _interp_shape(a: float, b: float, t: float, shape: str) -> float:
    """形状插值函数。"""
    t = max(0.0, min(1.0, t))

    if shape == "linear":
        return a + (b - a) * t
    elif shape == "ease_in":
        return a + (b - a) * (t ** 2)
    elif shape == "ease_out":
        return a + (b - a) * (1 - (1 - t) ** 2)
    elif shape == "s_curve":
        # smoothstep
        t2 = t * t * (3 - 2 * t)
        return a + (b - a) * t2
    else:
        return a + (b - a) * t
