"""
symphonic_orchestrator.py — 交响化编排引擎
将古典管弦乐创作法则映射到喷泉/DMX 控制系统。

类比映射（核心洞察）：
  ┌─────────────────────┬──────────────────────────────────┐
  │ 管弦乐队             │ Qascade 喷泉系统                   │
  ├─────────────────────┼──────────────────────────────────┤
  │ 第一小提琴 (旋律)     │ 数控/电机 solo path (演员独舞)      │
  │ 第二小提琴 (和声副旋) │ RGB 喷泉染色 (中层动态色彩)         │
  │ 中提琴 (和声填充)     │ 阀 rhythmic gate (节奏性开合)       │
  │ 大提琴/低音提琴 (基础)│ 泵 burst accent (力量爆发,低频)     │
  │ 木管 (色彩装饰)       │ Matrix LED (极速变色画布, 细碎点缀)  │
  │ 铜管 (高潮强调)       │ 调压气爆 (爆炸性重音, 4seq/8seq 追逐)│
  │ 定音鼓 (节奏标点)     │ dimmer strobe (节拍脉冲)          │
  │ 竖琴/钢片琴 (特殊色彩)│ Function 通道 (固定配置, 开关式)    │
  │ 指挥 (conductor)      │ ArrangementDirector              │
  │ 总谱 (score)          │ TensionArc + PhraseDynamics       │
  └─────────────────────┴──────────────────────────────────┘

  "一部喷泉秀 = 一首视觉交响乐"
  — 同样的曲式、织体、配器、力度、动机发展法则

纯 core，无 UI/引擎依赖。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .project_profile import (
    ProjectProfile, GroupProfile,
    CAP_MOTOR, CAP_PUMP, CAP_VALVE, CAP_RGB, CAP_DIMMER,
)


# ── 管弦乐声部 → 喷泉设备映射 ──────────────────────────────────

class FountainSection(str, Enum):
    """喷泉声部 = 管弦乐声部的视觉对应。"""
    SOLOIST = "soloist"          # 独奏者 = 数控/电机 (独舞演员)
    STRINGS = "strings"           # 弦乐组 = LED灯组 (持续背景/画布)
    BRASS = "brass"              # 铜管组 = 泵 (力量爆发, 高潮重音)
    WOODWINDS = "woodwinds"      # 木管组 = 阀 (节奏性开合, 装饰)
    PERCUSSION = "percussion"    # 打击乐组 = 调压气爆 (爆炸性节拍)
    COLORATURA = "coloratura"    # 花腔/装饰 = Matrix LED (极速变化)
    CONTINUO = "continuo"        # 通奏低音 = Function 通道 (持续底层)


# 设备能力 → 管弦乐声部
_CAP_TO_SECTION = {
    CAP_MOTOR:  FountainSection.SOLOIST,
    CAP_PUMP:   FountainSection.BRASS,
    CAP_VALVE:  FountainSection.WOODWINDS,
    CAP_RGB:    FountainSection.STRINGS,   # 默认，会被 matrix/fountain 细分覆盖
    CAP_DIMMER: FountainSection.PERCUSSION,
}


# ── 曲式结构（对标 Sonata Form）─────────────────────────────────

@dataclass
class SymphonicForm:
    """交响曲式结构，映射到灯光秀的时间线。

    Sonata Form 标准结构：
      呈示部 (Exposition): 主题首次亮相，设备渐入，轻柔
      展开部 (Development): 主题变形/发展，设备交替，张力攀升
      再现部 (Recapitulation): 主题回归，全设备复现，力度增强
      尾声 (Coda): 总结收束，渐慢渐弱
    """
    exposition_start: float = 0.0
    exposition_end: float = 0.0
    development_start: float = 0.0
    development_end: float = 0.0
    recapitulation_start: float = 0.0
    recapitulation_end: float = 0.0
    coda_start: float = 0.0
    coda_end: float = 0.0

    def form_section_at(self, t: float) -> str:
        """返回时刻 t 所在的曲式段落。"""
        if t <= self.exposition_end:
            return "exposition"
        if t <= self.development_end:
            return "development"
        if t <= self.recapitulation_end:
            return "recapitulation"
        return "coda"


# ── 配器策略（Orchestration Strategy）───────────────────────────

@dataclass
class OrchestrationStrategy:
    """一段音乐的配器策略：每个声部在干什么。

    对标标杆 To The Moon 数据提取的真实模式。
    """
    section_name: str = ""
    energy: float = 0.5
    percussive: float = 0.5  # 打击力度

    # 每个喷泉声部的角色
    soloist_active: bool = False       # 电机是否独舞
    strings_role: str = "background"   # "background" | "color_field" | "off"
    brass_role: str = "off"            # "off" | "accent" | "burst" | "full"
    woodwinds_role: str = "off"        # "off" | "rhythm" | "sequence" | "chase"
    percussion_role: str = "off"       # "off" | "4seq" | "8seq" | "blast"
    coloratura_active: bool = False    # Matrix LED 是否活跃
    continuo_active: bool = True       # Function 通道（始终开着）

    # 织体
    texture: str = "homophony"         # "monophony"|"homophony"|"polyphony"|"tutti"
    layer_count: int = 3               # 同时活跃的声部数

    # 空间策略
    topology: str = "scattered"        # "scattered"|"line"|"arc"|"full_ring"
    direction: int = 1

    # 色彩策略
    color_mode: str = "white"          # "white"|"pure"|"color" (对标标杆: 白61%/纯22%/彩16%)
    color_temperature: str = "warm"    # "warm"|"neutral"|"cool" (和声驱动)

    # 速度策略（对标标杆真实速度带）
    speed_multiplier: float = 1.0      # 全局速度倍率


# ── 交响编排引擎 ────────────────────────────────────────────────

class SymphonicOrchestrator:
    """交响编排引擎 — 核心：把音乐特征翻译成"哪个声部在干什么"的决策。

    设计原则（对标标杆 81 clip + 交响乐理论）：
      1. 呈示部: 声部逐个入场(accumulate), 轻柔, 主题首次陈述
      2. 展开部: 声部交替主导, 动机变形, 张力攀升
      3. 再现部: 主题回归, 全声部齐鸣(tutti), 力度增强
      4. 尾声: 声部逐个退场(thinning), 渐慢渐弱, 终止式
    """

    # ── 标杆速度带（从 To The Moon 81 clip 实测提取）─────────
    # 格式: (lo能量, hi能量) → 速度范围(DMX单位/秒)
    _SPEED_BANDS = {
        "数控": {
            (0.0, 0.30): (0, 16),     # 低能: 静止或极慢
            (0.30, 0.55): (12, 48),   # 中能: 缓慢舞蹈
            (0.55, 0.75): (25, 50),   # 高能: 明显动作
            (0.75, 1.00): (16, 50),   # 峰值: 可控速度(避免失控)
        },
        "泵": {
            (0.0, 0.30): (0, 82),
            (0.30, 0.55): (0, 179),
            (0.55, 0.75): (82, 310),
            (0.75, 1.00): (179, 6087),  # 极限爆发
        },
        "阀": {
            (0.0, 0.30): (0, 0),
            (0.30, 0.55): (0, 59),
            (0.55, 0.75): (0, 0),      # 阀在高能段反而静止(让给泵)
            (0.75, 1.00): (0, 96),
        },
        "RGB": {
            (0.0, 0.30): (53, 352),
            (0.30, 0.55): (36, 134),
            (0.55, 0.75): (177, 1640),
            (0.75, 1.00): (237, 2937),  # 极速变色
        },
    }

    @classmethod
    def orchestrate(
        cls,
        energy: float,
        percussive: float,
        *,
        form_section: str = "exposition",
        section_duration_s: float = 8.0,
        is_solo: bool = False,
        _solo_target: str = "",
        rng: Optional[random.Random] = None,
    ) -> OrchestrationStrategy:
        """根据音乐特征+曲式位置生成配器策略。

        Args:
            energy: 段平均能量 (0~1)
            percussive: 打击力度 (0~1)
            form_section: 曲式位置 ("exposition"|"development"|"recapitulation"|"coda")
            section_duration_s: 段时长
            is_solo: 是否为独奏段 (C5 音高判定)
            solo_target: 独奏目标设备
            rng: 随机数生成器(确定性重现)
        """
        rng = rng or random.Random(0)

        strat = OrchestrationStrategy(energy=energy, percussive=percussive)

        # ── 1. 织体选择 ──
        strat.texture, strat.layer_count = cls._choose_texture(
            energy, percussive, form_section, is_solo
        )

        # ── 2. 各声部角色分配 ──
        strat = cls._assign_roles(strat, energy, percussive, form_section, is_solo)

        # ── 3. 拓扑选择（对标标杆: 散→线→弧→整环 逐级收束）──
        strat.topology = cls._choose_topology(energy, form_section)

        # ── 4. 色彩模式 ——
        strat.color_mode, strat.color_temperature = cls._choose_color(
            energy, form_section, percussive
        )

        # ── 5. 速度倍率 ——
        strat.speed_multiplier = cls._choose_speed_multiplier(energy, form_section)

        return strat

    # ── 内部方法 ──────────────────────────────────────────────

    @classmethod
    def _choose_texture(cls, energy: float, perc: float,
                        form: str, is_solo: bool) -> Tuple[str, int]:
        """选择织体类型 + 同时活跃声部数。

        对标:
          呈示 intro: monophony, 1-2 声部
          呈示 verse: homophony, 2-3 声部
          展开 bridge: polyphony, 3-4 声部
          再现 chorus: tutti, 5-6 声部
          高潮 peak: tutti, 6+ 声部
        """
        if is_solo:
            return ("monophony", 1)  # 独奏 = 单声部

        if form == "exposition":
            if energy < 0.30:
                return ("monophony", 2)  # 前奏: 轻开
            return ("homophony", 3)       # 主歌: 主调

        if form == "development":
            if energy > 0.60:
                return ("polyphony", 4)   # 展开: 多声部对位
            return ("homophony", 3)

        if form == "recapitulation":
            if energy > 0.70:
                return ("tutti", 6)       # 再现+高能 = 全奏
            return ("homophony", 4)       # 再现: 增强的复述

        # coda
        if energy > 0.40:
            return ("homophony", 3)       # 尾声仍有能量
        return ("monophony", 1)           # 最终淡出

    @classmethod
    def _assign_roles(
        cls, strat: OrchestrationStrategy, energy: float,
        perc: float, form: str, is_solo: bool,
    ) -> OrchestrationStrategy:
        """分配每个喷泉声部的角色。

        对标标杆核心规律:
          - LED灯组|RGB 贯穿全曲，速度波动 → strings background/color_field
          - HJC 数控 全曲活跃，速度跟随能量 → soloist (独舞)
          - 调压气爆 只在能量>0.38出现 → percussion 4seq→8seq
          - HJC 泵 能量>0.55出现 → brass burst/full
          - HJC 阀 能量>0.55出现 → woodwinds rhythm/sequence
        """
        # ── Strings (LED/RGB): 始终在，作画布/背景 ──
        if energy < 0.25:
            strat.strings_role = "background"  # 极轻柔背景
        elif energy < 0.55:
            strat.strings_role = "color_field" # 慢速变色
        else:
            strat.strings_role = "color_field" # 快速变色画布

        # ── Soloist (数控/电机): 能量>0.20 活跃, 高能或 melody 时独舞 ──
        if is_solo:
            strat.soloist_active = True       # C5 判定 = solo 模式
        elif energy > 0.55:
            strat.soloist_active = True       # 高能段电机活跃
        elif energy > 0.20 and perc > 0.80:
            strat.soloist_active = True       # 强节奏段电机配合
        else:
            strat.soloist_active = False

        # ── Brass (泵): 打击乐强或高能时爆发 ──
        if energy > 0.70 and perc > 0.75:
            strat.brass_role = "full"         # 高潮全开
        elif energy > 0.50 and perc > 0.65:
            strat.brass_role = "burst"        # 副歌爆发
        elif energy > 0.35:
            strat.brass_role = "accent"       # 中能强调
        else:
            strat.brass_role = "off"

        # ── Woodwinds (阀): 中能段做节奏装饰, 高能让给铜管 ──
        if 0.30 < energy < 0.55:
            strat.woodwinds_role = "rhythm"   # 主副歌段节奏开合
        elif energy >= 0.55 and perc > 0.70:
            strat.woodwinds_role = "sequence" # 高能追逐
        else:
            strat.woodwinds_role = "off"       # 低能量或峰值让位

        # ── Percussion (气爆): 对标标杆 — 能量>0.38 引入, 递增 seq 规模 ──
        if energy > 0.65 and perc > 0.75:
            strat.percussion_role = "8seq"    # 高潮: 8 步追逐
        elif energy > 0.50 and perc > 0.65:
            strat.percussion_role = "4seq"    # 副歌: 4 步追逐
        elif energy > 0.35:
            strat.percussion_role = "blast"   # 引入: 单次爆炸
        else:
            strat.percussion_role = "off"      # 前奏/间奏: 安静

        # ── Coloratura (Matrix LED): 只在极高能+弱打击时活跃(细碎闪烁, 防过度) ──
        if energy > 0.70 and perc < 0.70:
            strat.coloratura_active = True
        else:
            strat.coloratura_active = False

        # ── Continuo (Function 通道): 始终开着(对标: speed=0 静态) ──
        strat.continuo_active = True

        # ── 独奏模式覆盖: 所有非独奏声部降级 ──
        if is_solo:
            strat.brass_role = "off"
            strat.woodwinds_role = "off"
            strat.percussion_role = "off"
            strat.coloratura_active = False
            strat.strings_role = "background"  # 仅铺底

        return strat

    @classmethod
    def _choose_topology(cls, energy: float, form: str) -> str:
        """根据能量选择空间拓扑。

        对标标杆规律:
          低能(0.18-0.38): scattered (散) — 无聚焦, 弥漫感
          中能(0.55):     line/arc (线/弧) — 开始聚焦
          高能(0.77):     full_ring/scattered 混合 — 整环齐鸣
        """
        if energy < 0.25:
            return "scattered"
        if energy < 0.40:
            return "scattered" if form == "exposition" else "line"
        if energy < 0.55:
            return "arc"
        if energy < 0.70:
            return "line"  # 紧致线聚焦
        return "full_ring"  # 整环齐鸣

    @classmethod
    def _choose_color(cls, energy: float, form: str, perc: float) -> Tuple[str, str]:
        """选择色彩模式和色温。

        对标标杆实测:
          白 61% / 纯色 22% / 彩色 16%
          气爆白 70% / 低能偏白 / 高潮才彩色
        """
        # 色彩模式
        if energy < 0.30:
            mode = "white"           # 低能 = 白
        elif energy < 0.55:
            mode = "white" if perc > 0.70 else "pure"  # 主歌白,间奏纯色
        elif energy < 0.75:
            mode = "pure"            # 副歌纯色
        else:
            mode = "color"           # 高潮彩色

        # 色温（用和声驱动 — 此处用能量+打击代理）
        if perc > 0.80:
            temp = "warm"            # 强节奏 = 暖色 (橙/红)
        elif energy > 0.60:
            temp = "neutral"         # 高能 = 中性
        else:
            temp = "cool"            # 低能 = 冷色 (蓝/紫)

        return mode, temp

    @classmethod
    def _choose_speed_multiplier(cls, energy: float, form: str) -> float:
        """选择全局速度倍率。

        对标: 前奏/尾声慢、展开加速、再现最快。
        """
        base = 1.0
        if form == "exposition":
            base = 0.6 + energy * 0.5   # 0.6~1.1
        elif form == "development":
            base = 0.8 + energy * 0.6   # 0.8~1.4
        elif form == "recapitulation":
            base = 1.0 + energy * 0.5   # 1.0~1.5
        else:  # coda
            base = 0.3 + energy * 0.3   # 渐慢 0.3~0.6
        return base

    @classmethod
    def build_score(
        cls,
        section_energies: List[float],
        section_percussives: List[float],
        *,
        duration_s: float = 180.0,
    ) -> Tuple[SymphonicForm, List[OrchestrationStrategy]]:
        """从音乐段能量列表构建完整"总谱"。

        Args:
            section_energies: 各段平均能量
            section_percussives: 各段打击力度
            duration_s: 总时长

        Returns:
            (曲式结构, 各段配器策略)
        """
        n = len(section_energies)
        if n == 0:
            return SymphonicForm(), []

        total = duration_s

        # ── 自动划分曲式（Sonata Form 比例）──
        # 呈示 35% / 展开 25% / 再现 30% / 尾声 10%
        form = SymphonicForm(
            exposition_start=0,
            exposition_end=total * 0.35,
            development_start=total * 0.35,
            development_end=total * 0.60,
            recapitulation_start=total * 0.60,
            recapitulation_end=total * 0.90,
            coda_start=total * 0.90,
            coda_end=total,
        )

        # ── 每段生成配器策略 ──
        strategies = []
        section_dur = total / n

        for i, (energy, perc) in enumerate(zip(section_energies, section_percussives)):
            t = (i + 0.5) * section_dur
            fs = form.form_section_at(t)

            strategy = cls.orchestrate(
                energy=energy,
                percussive=perc,
                form_section=fs,
                section_duration_s=section_dur,
            )
            strategy.section_name = f"s{i}"
            strategies.append(strategy)

        return form, strategies
