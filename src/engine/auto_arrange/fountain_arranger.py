"""
fountain_arranger.py — 喷泉秀编排引擎 (数据驱动, 对标真实标杆)

核心洞察（从 To The Moon 81 clip 逆向提取, 非套用音乐理论）:

  A. 能量是唯一主驱动 — 标杆中所有参数(速度/设备数/拓扑/颜色)都随能量变化,
     但每种设备的能量→行为映射完全不同(有自己的"性格")

  B. 设备性格 (device personality) — 不是管弦乐角色, 是物理特性决定的运动模式:
     - 数控/电机: 全场最慢(55~259 DMX/s), 像舞蹈演员, 平滑过渡绝不突变
     - 泵: 爆发型(中位206, 峰值6087), 跟鼓点脉冲, 静→炸→静
     - 阀: 节奏型(中位96), 气爆的分组追逐(4seq/8seq), 绝不全开
     - LED灯组|RGB: 全场最快(中位600, 峰值8745), 画布/背景, 快速变色
     - 喷泉自带RGB: 中速染色(中位100~200), 跟随电机/泵的颜色变化
     - Function通道: 永远静止(speed=0), 固定配置开关

  C. 渐进叠加 (progressive layering) — clip数量不是随机的:
     - 前奏(~0.18能): 2-3个设备, 散开大半径
     - 主歌(~0.30能): 3-4个设备, 线形聚焦
     - 副歌(~0.55能): 4-6个设备, 弧形收束, seq pattern出现
     - 高潮(~0.77能): 6-8个设备, 整环齐鸣, 20 clips共存

  D. 空间动态 (spatial dynamics) — 不是拓扑标签, 是视觉运动:
     - 散→线→弧→整环 = 从弥散到聚焦的过程, 对应能量爬升
     - 电机过渡(motor transition)只在能量≥0.70出现, 带长淡入(1.6-2.3s)
     - seq追逐方向交替(顺/逆时针)避免单调

  E. 色彩律 (color law) — 标杆实测比例:
     - 低能(<0.30): 白色主导(70%+), 偶尔纯色
     - 中能(0.30-0.55): 白色+纯色(各50%)
     - 高能(0.55-0.75): 纯色主导(60%), 白色辅助, 少量彩色
     - 峰值(>0.75): 彩色允许(16%), 但仍以白色/纯色为主
     - 气爆永远偏白(70%) — 水雾本身就是白色的

  F. 留白 (breathing room) —
     - 不是每段所有设备都开, 有明确的休止段
     - 段落交接有淡出淡入重叠(0.4-2.6s), 非硬切
     - 柔美段速度整体降30-40%

纯 core, 无 Qt/引擎依赖。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── 设备性格档案 (从标杆 81 clip 实测) ──────────────────────────

@dataclass
class DevicePersonality:
    """一种设备类型的运动性格 — 物理特性决定, 非音乐角色。"""
    device_type: str = ""
    # 速度带 (DMX单位/秒, 标杆实测)
    speed_lo: float = 0.0       # 低能段速度
    speed_mid: float = 100.0    # 中能段速度
    speed_hi: float = 500.0     # 高能段速度
    speed_peak: float = 2000.0  # 峰值速度

    # 响应特性
    responds_to_percussion: bool = False   # 是否跟打击乐
    responds_to_melody: bool = False       # 是否跟旋律
    prefers_smooth_transition: bool = True # 是否偏好平滑过渡
    min_fade_s: float = 0.4    # 最短淡入淡出
    max_fade_s: float = 2.6    # 最长淡入淡出

    # 空间偏好
    preferred_topology: str = "scattered"  # scattered/line/arc/ring
    can_do_sequential: bool = False        # 能否做 seq 追逐模式
    can_do_motor_transition: bool = False  # 能否做电机过渡

    # 色彩偏好
    color_white_ratio: float = 0.6   # 白色占比
    color_pure_ratio: float = 0.22   # 纯色占比
    color_full_ratio: float = 0.18   # 彩色占比


# 从标杆实测数据构建设备性格档案
PERSONALITIES = {
    "数控": DevicePersonality(
        device_type="数控/电机",
        speed_lo=0, speed_mid=25, speed_hi=50, speed_peak=259,
        responds_to_melody=True,
        prefers_smooth_transition=True,
        min_fade_s=0.6, max_fade_s=2.3,
        preferred_topology="line",
        can_do_motor_transition=True,
        color_white_ratio=0.6, color_pure_ratio=0.3, color_full_ratio=0.1,
    ),
    "泵": DevicePersonality(
        device_type="泵",
        speed_lo=0, speed_mid=82, speed_hi=310, speed_peak=6087,
        responds_to_percussion=True,
        prefers_smooth_transition=False,  # 泵是爆发的,不怕突变
        min_fade_s=0.4, max_fade_s=1.5,
        preferred_topology="line",
        can_do_sequential=True,
        color_white_ratio=0.5, color_pure_ratio=0.4, color_full_ratio=0.1,
    ),
    "阀": DevicePersonality(
        device_type="阀",
        speed_lo=0, speed_mid=0, speed_hi=0, speed_peak=96,
        responds_to_percussion=True,
        prefers_smooth_transition=False,
        min_fade_s=0.4, max_fade_s=1.2,
        preferred_topology="arc",
        can_do_sequential=True,  # 4seq/8seq 气爆追逐
        color_white_ratio=0.7, color_pure_ratio=0.2, color_full_ratio=0.1,
    ),
    "LED灯组|RGB": DevicePersonality(
        device_type="LED灯组|RGB",
        speed_lo=352, speed_mid=1513, speed_hi=2937, speed_peak=8745,
        responds_to_melody=False,
        prefers_smooth_transition=True,
        min_fade_s=0.0, max_fade_s=2.6,
        preferred_topology="scattered",
        color_white_ratio=0.5, color_pure_ratio=0.3, color_full_ratio=0.2,
    ),
    "喷泉RGB": DevicePersonality(
        device_type="喷泉自带RGB",
        speed_lo=10, speed_mid=53, speed_hi=177, speed_peak=300,
        responds_to_melody=True,  # 跟随电机颜色变化
        prefers_smooth_transition=True,
        min_fade_s=0.4, max_fade_s=2.0,
        preferred_topology="scattered",
        color_white_ratio=0.7, color_pure_ratio=0.2, color_full_ratio=0.1,
    ),
    "气爆": DevicePersonality(
        device_type="调压气爆",
        speed_lo=0, speed_mid=0, speed_hi=0, speed_peak=0,  # 气爆速度=0(静态爆炸)
        responds_to_percussion=True,
        prefers_smooth_transition=False,
        min_fade_s=0.4, max_fade_s=1.5,
        preferred_topology="arc",
        can_do_sequential=True,
        color_white_ratio=0.7, color_pure_ratio=0.2, color_full_ratio=0.1,
    ),
}


# ── 喷泉编排决策 ────────────────────────────────────────────────

@dataclass
class FountainMoment:
    """一个喷泉时刻的完整描述 — 对标标杆中一个 MusicCue 应该产生什么。"""

    t_start: float = 0.0
    t_end: float = 0.0
    duration_s: float = 0.0
    energy: float = 0.5
    percussive: float = 0.5

    # ── 各设备类型的动作指令 ──
    active_devices: Dict[str, bool] = field(default_factory=dict)
    device_speeds: Dict[str, float] = field(default_factory=dict)
    device_topologies: Dict[str, str] = field(default_factory=dict)
    device_fades: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    device_colors: Dict[str, str] = field(default_factory=dict)

    # ── 全局 ──
    sequential_pattern: str = ""   # "" | "4seq" | "8seq"
    motor_transition: bool = False
    global_speed_mult: float = 1.0

    # ── 过渡 ──
    fade_in_s: float = 0.8
    fade_out_s: float = 0.8


# ── 喷泉编排引擎 ────────────────────────────────────────────────

class FountainArranger:
    """喷泉秀编排引擎 — 数据驱动, 对标真实标杆。

    输入: 音乐特征(能量/打击力/旋律度/BPM)
    输出: 每个设备类型在当前段应该做什么

    不是管弦乐指挥, 是"喷泉编导" — 理解每种设备的物理特性和视觉语言。
    """

    @classmethod
    def arrange(
        cls,
        energy: float,
        percussive: float,
        melodic: float = 0.1,
        *,
        _bpm: float = 120.0,
        section_duration_s: float = 8.0,
        is_buildup: bool = False,   # 是否在攀升段
        is_release: bool = False,   # 是否在释放段
        is_first_section: bool = False,
        is_last_section: bool = False,
        rng_seed: int = 0,
    ) -> FountainMoment:
        """为一个音乐段生成喷泉编排决策。

        Args:
            energy: 段平均能量 (标杆实测范围 0.18~0.77)
            percussive: 打击力度 (标杆实测 0.69~1.0)
            melodic: 旋律存在度 (标杆实测 0.05~0.31)
            bpm: BPM
            section_duration_s: 段时长
            is_buildup: 是否在张力攀升段
            is_release: 是否在张力释放段
            is_first_section: 第一段(触发渐入)
            is_last_section: 最后段(触发渐弱+终止)

        Returns:
            FountainMoment with per-device decisions
        """
        rng = random.Random(rng_seed + int(energy * 1000))

        t_start = 0.0  # 由调用者设定
        m = FountainMoment(
            duration_s=section_duration_s,
            energy=energy,
            percussive=percussive,
        )

        # ── A. 渐进叠加: 多少设备该活跃 ──
        # 对标: 能量0.18→2-3设备, 0.30→3-4, 0.55→4-6, 0.77→6-8
        base_device_count = int(2 + energy * 7)  # 2~9
        actual_count = min(base_device_count, 8)

        # ── B. 设备活跃判定: 每个设备按能量阈值+自身性格决定 ──
        device_candidates = cls._rank_devices_for_energy(energy, percussive, melodic)

        active = {}
        speeds = {}
        topologies = {}
        fades = {}
        colors = {}

        for i, (dev_key, personality) in enumerate(device_candidates):
            if i >= actual_count:
                active[dev_key] = False
                speeds[dev_key] = 0.0
                continue

            active[dev_key] = True

            # 速度: 按能量线性插值 + 微调避免过度机械
            if energy < 0.30:
                base_speed = personality.speed_lo
            elif energy < 0.55:
                t_energy = (energy - 0.30) / 0.25
                base_speed = personality.speed_lo + (personality.speed_mid - personality.speed_lo) * t_energy
            elif energy < 0.75:
                t_energy = (energy - 0.55) / 0.20
                base_speed = personality.speed_mid + (personality.speed_hi - personality.speed_mid) * t_energy
            else:
                t_energy = (energy - 0.75) / 0.25
                base_speed = personality.speed_hi + (personality.speed_peak - personality.speed_hi) * min(t_energy, 1.0)

            # 打击乐加速(泵/阀/气爆)
            if personality.responds_to_percussion:
                base_speed *= 1.0 + percussive * 0.5  # 强打击×1.5

            # 旋律加速(数控/喷泉RGB)
            if personality.responds_to_melody:
                base_speed *= 1.0 + melodic * 1.0  # 强旋律×2.0

            speeds[dev_key] = base_speed

            # 拓扑: 按能量阶梯
            topologies[dev_key] = cls._topology_for_energy(energy, personality)

            # 淡入淡出: 设备性格决定范围, 能量决定具体值
            fade_range = personality.max_fade_s - personality.min_fade_s
            fade_in = personality.min_fade_s + fade_range * (0.3 + energy * 0.4)
            fade_out = personality.min_fade_s + fade_range * (0.2 + energy * 0.5)
            if is_first_section:
                fade_in *= 1.5  # 首段长渐入
            if is_last_section:
                fade_out *= 2.0  # 末段长渐出
            fades[dev_key] = (round(fade_in, 2), round(fade_out, 2))

            # 色彩
            colors[dev_key] = cls._color_for_device(personality, energy, percussive)

        m.active_devices = active
        m.device_speeds = speeds
        m.device_topologies = topologies
        m.device_fades = fades
        m.device_colors = colors

        # ── C. 气爆 seq 模式(对标: 能量>0.38引入) ──
        if energy > 0.65 and percussive > 0.75:
            m.sequential_pattern = "8seq"
        elif energy > 0.50 and percussive > 0.65:
            m.sequential_pattern = "4seq"
        elif energy > 0.35:
            m.sequential_pattern = "2seq"  # 轻度追逐
        else:
            m.sequential_pattern = ""

        # ── D. 电机过渡(对标: 仅能量≥0.70+长淡入) ──
        m.motor_transition = energy >= 0.70 and melodic > 0.08

        # ── E. 全局速度倍率 ──
        if is_first_section:
            m.global_speed_mult = 0.6   # 缓慢起始
        elif is_release:
            m.global_speed_mult = 0.7   # 释放降速
        elif is_last_section:
            m.global_speed_mult = 0.35  # 尾声极慢
        elif is_buildup:
            m.global_speed_mult = 1.1   # 攀升略加速
        else:
            m.global_speed_mult = 1.0

        # ── F. 留白强制: 首段不炸, 尾段不炸 ──
        if is_first_section:
            m.sequential_pattern = ""
            m.motor_transition = False
        if is_last_section:
            m.sequential_pattern = ""
            m.active_devices["气爆"] = False

        return m

    # ── 内部方法 ──────────────────────────────────────────────

    @classmethod
    def _rank_devices_for_energy(
        cls, energy: float, percussive: float, melodic: float,
    ) -> List[Tuple[str, DevicePersonality]]:
        """按当前能量的"适配度"对设备排序 — 高排名的先激活。

        排序逻辑对标标杆: 低能段LED先开、中能段泵阀加入、高能段气爆+电机过渡。
        """
        ranked = []

        for dev_key, pers in PERSONALITIES.items():
            # 计算激活优先级
            priority = 0.0

            # LED/喷泉RGB: 全能量段都活跃, 基线最高
            if "RGB" in dev_key or "LED" in dev_key:
                priority = 10.0
            # 数控/电机: 能量>0.15 活跃
            elif dev_key == "数控":
                priority = 8.0 if energy > 0.15 else 3.0
            # 泵: 能量>0.30 加入
            elif dev_key == "泵":
                priority = 7.0 if energy > 0.25 else 1.0
            # 阀: 能量>0.35 加入
            elif dev_key == "阀":
                priority = 6.0 if energy > 0.30 else 1.0
            # 气爆: 能量>0.35 加入
            elif dev_key == "气爆":
                priority = 5.0 if energy > 0.35 else 0.0
            else:
                priority = 4.0

            # 微调: 打击乐段 泵/阀/气爆 提前
            if percussive > 0.70 and pers.responds_to_percussion:
                priority += 1.5
            # 旋律段 数控 提前
            if melodic > 0.15 and pers.responds_to_melody:
                priority += 1.5

            ranked.append((dev_key, pers, priority))

        # 按优先级降序
        ranked.sort(key=lambda x: x[2], reverse=True)
        return [(dev_key, pers) for dev_key, pers, _ in ranked]

    @classmethod
    def _topology_for_energy(cls, energy: float, personality: DevicePersonality) -> str:
        """根据能量选择空间拓扑。

        标杆规律:
          散(scattered): 能量<0.25, 大面积弥散
          线(line): 能量0.25-0.40, 开始聚焦
          弧(arc): 能量0.40-0.55, 进一步收束
          整环(full_ring): 能量>0.55, 全环齐鸣

        每种设备有自己的偏好拓扑, 能量只部分覆盖。
        """
        if energy < 0.25:
            return "scattered"
        elif energy < 0.40:
            return "line" if personality.prefers_smooth_transition else "scattered"
        elif energy < 0.55:
            return "arc" if personality.can_do_sequential else "line"
        else:
            return "full_ring" if personality.can_do_sequential else "arc"

    @classmethod
    def _color_for_device(
        cls, personality: DevicePersonality, energy: float, percussive: float,
    ) -> str:
        """按设备性格+能量选择色彩模式。

        对标标杆色彩律:
          低能(<0.30): 白色主导
          中能(0.30-0.55): 白色+纯色
          高能(0.55-0.75): 纯色主导
          峰值(>0.75): 少量彩色加入
          气爆永远偏白
        """
        r = random.Random(int(energy * 10000 + percussive * 1000))

        if "气爆" in personality.device_type:
            # 气爆永远偏白 (水雾即白色)
            return "white"

        if energy < 0.30:
            return "white" if r.random() < personality.color_white_ratio else "pure"
        elif energy < 0.55:
            roll = r.random()
            if roll < personality.color_white_ratio:
                return "white"
            elif roll < personality.color_white_ratio + personality.color_pure_ratio:
                return "pure"
            else:
                return "color"
        elif energy < 0.75:
            roll = r.random()
            if roll < personality.color_pure_ratio:
                return "pure"
            elif roll < personality.color_pure_ratio + personality.color_white_ratio:
                return "white"
            else:
                return "color"
        else:
            # 峰值: 三色按比例
            roll = r.random()
            if roll < personality.color_full_ratio:
                return "color"
            elif roll < personality.color_full_ratio + personality.color_pure_ratio:
                return "pure"
            else:
                return "white"

    @classmethod
    def arrange_full_show(
        cls,
        section_energies: List[float],
        section_percussives: List[float],
        section_melodics: Optional[List[float]] = None,
        *,
        total_duration_s: float = 180.0,
        seed: int = 42,
    ) -> List[FountainMoment]:
        """为整首歌编排喷泉秀 — 加入跨段落的宏观弧线。

        对标: To The Moon 20段 → 81 clip 的渐进叠加→峰值→回落模式。
        """
        n = len(section_energies)
        if n == 0:
            return []

        section_dur = total_duration_s / n
        melodics = section_melodics or [0.1] * n

        # 找出峰值位置
        peak_idx = int(max(range(n), key=lambda i: section_energies[i]))

        moments = []
        for i in range(n):
            e = section_energies[i]
            p = section_percussives[i] if i < len(section_percussives) else 0.5
            m = melodics[i] if i < len(melodics) else 0.1

            # 判断攀升/释放
            is_buildup = i < peak_idx and e > 0.3
            is_release = i > peak_idx

            moment = cls.arrange(
                energy=e,
                percussive=p,
                melodic=m,
                section_duration_s=section_dur,
                is_buildup=is_buildup,
                is_release=is_release,
                is_first_section=(i == 0),
                is_last_section=(i == n - 1),
                rng_seed=seed + i,
            )
            moment.t_start = i * section_dur
            moment.t_end = (i + 1) * section_dur
            moments.append(moment)

        return moments
