"""
motif_system.py — 动作元系统 v2.0（大幅扩写版）
================================================
定义喷泉编舞的基本动作元（Motif），包括：
  - 动作元词汇（MotifVocabulary）：63个精细化动作元
  - 乐器亲和力（InstrumentAffinity）：每个动作元对哪类乐器有天然响应
  - 运动语义（MotionSemantic）：每个动作元的抽象运动意义
  - 运动曲线（MotionCurve）：每个动作元的时间-幅度曲线类型
  - 呼应配对规则（CorrespondenceRules）：动作元之间的呼应关系
  - 变奏系统（VariationEngine）：对动作元进行实时改编

动作元分类（9大类，63个）：
  波动类（6）：单向波/驻波/呼吸波/行波/反向波/叠加波
  交叉类（6）：单交叉/双交叉/级联交叉/镜像交叉/斜交叉/螺旋交叉
  扩散类（5）：中心扩散/边缘扩散/径向扩散/螺旋扩散/扇形扩散
  脉冲类（6）：节拍脉冲/重音脉冲/随机脉冲/级联脉冲/双重脉冲/消退脉冲
  追逐类（5）：线性追逐/圆形追逐/随机追逐/双向追逐/螺旋追逐
  形态类（6）：弧形/环形/扇形/散点/网格/星形
  对话类（7）：呼唤/回应/回声延迟/问答交替/接力/镜像对话/多声部对话
  织体类（8）：稀疏编织/密集编织/瀑布积累/瀑布释放/交错填空/层叠/抽丝/织入
  节奏切分类（5）：切分/附点节奏/三连音爆发/反拍强调/复合节奏
  情绪强度类（5）：张力保持/释放跌落/微颤/爆发冲击/渐强积累
  静态类（4）：全开保持/全关保持/图案保持/渐变保持

核心理念：
  动作元是"词汇"，音乐结构是"语法"，编舞引擎用这套语言"造句"。
  每个动作元有精细的运动曲线、子参数、乐器亲和力和呼应配对规则。
  变奏引擎可以在动作元的任意维度做改编，保持风格的同时创造新意。
"""
# ⚠ FROZEN — NO UI ENTRY（2026-09-12 审计确认）
# 本模块在当前仓库无任何 UI 入口（原 src/ui/deep_choreography_panel.py 已删除）。
# 改动前请先确认/恢复入口，否则你改的是死代码；新增功能不要建在此处。
# 见 ai-memory/maintainability-audit-2026-09-12.md 易损点 5。
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
#  运动语义
# ═══════════════════════════════════════════════════════════════════════════

class MotionSemantic(Enum):
    """动作元的抽象运动语义 - 决定动作的性格和情绪"""
    APPROACH    = "approach"    # 靠拢：两侧向中心聚集，有"打招呼"感
    DIVERGE     = "diverge"     # 分离：从中心向外展开，有"告别"感
    ASCEND      = "ascend"      # 上升：向上/向外的运动，有"期待"感
    DESCEND     = "descend"     # 下降：向下/向内的运动，有"释放"感
    ROTATE      = "rotate"      # 旋转：绕中心旋转，有"循环"感
    PULSE       = "pulse"       # 脉冲：突然的冲击，有"强调"感
    FLOW        = "flow"        # 流动：连续的波动，有"叙述"感
    SCATTER     = "scatter"     # 散射：随机分散，有"混乱/活力"感
    CONVERGE    = "converge"    # 汇聚：从四周向中心，有"凝聚"感
    RIPPLE      = "ripple"      # 涟漪：从一点向外扩散，有"回响"感
    CHASE       = "chase"       # 追逐：一个点跟随另一个点，有"追逐"感
    FREEZE      = "freeze"      # 静止：保持不动，有"悬念"感
    CALL        = "call"        # 呼唤：主动发出信号，有"邀请"感
    RESPOND     = "respond"     # 回应：被动接收信号，有"答复"感
    WEAVE       = "weave"       # 编织：交错穿插，有"织体"感
    BUILD       = "build"       # 积累：逐渐增强，有"期待/紧张"感
    RELEASE     = "release"     # 释放：突然减弱，有"解脱/满足"感
    TENSION     = "tension"     # 张力：保持高位紧张，有"悬念/压迫"感
    SHIMMER     = "shimmer"     # 微颤：细微快速抖动，有"兴奋/紧张"感
    SYNCOPATE   = "syncopate"   # 切分：在非节拍点动作，有"律动/意外"感


# ═══════════════════════════════════════════════════════════════════════════
#  运动曲线类型
# ═══════════════════════════════════════════════════════════════════════════

class MotionCurve(Enum):
    """动作元的时间-幅度曲线类型，决定动作的'手感'
    """
    LINEAR          = "linear"          # 线性：匀速，机械感
    EASE_IN         = "ease_in"         # 缓入：开始慢，加速，有"启动"感
    EASE_OUT        = "ease_out"        # 缓出：开始快，减速，有"落地"感
    EASE_IN_OUT     = "ease_in_out"     # 缓入缓出：S曲线，有"呼吸"感
    BOUNCE          = "bounce"          # 弹跳：到达后有小幅弹跳，有"活力"感
    SPRING          = "spring"          # 弹簧：过冲后回弹，有"弹性"感
    SHARP_ATTACK    = "sharp_attack"    # 锐利起始：瞬间达到峰值，有"冲击"感
    SHARP_DECAY     = "sharp_decay"     # 锐利衰减：快速从峰值下降，有"消散"感
    PULSE_CURVE     = "pulse_curve"     # 脉冲曲线：快速上升-保持-快速下降
    SINE            = "sine"            # 正弦：平滑周期，有"波动"感
    EXPONENTIAL     = "exponential"     # 指数：加速增长，有"爆发"感
    LOGARITHMIC     = "logarithmic"     # 对数：快速增长后趋于平缓，有"饱和"感
    STEP            = "step"            # 阶跃：突然切换，有"切换"感
    RANDOM_WALK     = "random_walk"     # 随机游走：不规则变化，有"有机"感
    SAWTOOTH        = "sawtooth"        # 锯齿：线性上升后突降，有"节奏"感
    TRIANGLE        = "triangle"        # 三角：线性上升后线性下降，有"对称"感


# ═══════════════════════════════════════════════════════════════════════════
#  乐器亲和力
# ═══════════════════════════════════════════════════════════════════════════

class InstrumentAffinity(Enum):
    """动作元对哪类乐器有天然的亲和力"""
    BASS_KICK   = "bass_kick"   # 低频/踢鼓：强调节拍的动作
    SNARE       = "snare"       # 军鼓：反拍/强调的动作
    HIHAT       = "hihat"       # 踩镲：快速细密的动作
    MELODY      = "melody"      # 旋律：跟随旋律线的动作
    HARMONY     = "harmony"     # 和声：跟随和声变化的动作
    BASS_LINE   = "bass_line"   # 贝斯线：低沉持续的动作
    PERCUSSION  = "percussion"  # 打击乐：节奏性的动作
    AMBIENT     = "ambient"     # 环境音：慢速/背景性的动作
    STRINGS     = "strings"     # 弦乐：流畅连绵的动作
    PIANO       = "piano"       # 钢琴：清晰颗粒感的动作
    SYNTH_PAD   = "synth_pad"   # 合成器垫音：持续演化的动作
    VOCAL       = "vocal"       # 人声：呼吸感/情绪性的动作
    ALL         = "all"         # 全乐器：对所有乐器都有响应


# ═══════════════════════════════════════════════════════════════════════════
#  呼应类型
# ═══════════════════════════════════════════════════════════════════════════

class CorrespondenceType(Enum):
    """两个动作元之间的呼应关系类型"""
    UNISON      = "unison"      # 齐奏：同时、同方向运动（强调统一感）
    ANTIPHON    = "antiphon"    # 对位：同时、反方向运动（强调对比感）
    ECHO        = "echo"        # 回声：A动后B延迟跟随（强调空间感）
    COMPLEMENT  = "complement"  # 互补：A动时B静，A静时B动（强调填空感）
    DIALOGUE    = "dialogue"    # 对话：A问B答，交替主导（强调叙事感）
    LAYER       = "layer"       # 叠层：A是骨架B是装饰，同时运行（强调织体感）
    HANDOFF     = "handoff"     # 接力：A的结尾是B的开头（强调连续感）
    SHADOW      = "shadow"      # 影子：B以较小幅度模仿A（强调深度感）
    INVERSION   = "inversion"   # 反转：B是A的镜像/倒影（强调对称感）
    AUGMENT     = "augment"     # 扩大：B以更大幅度/更慢速度重复A（强调庄重感）
    DIMINISH    = "diminish"    # 缩小：B以更小幅度/更快速度重复A（强调轻盈感）


# ═══════════════════════════════════════════════════════════════════════════
#  动作元类型枚举
# ═══════════════════════════════════════════════════════════════════════════

class MotifType(Enum):
    """所有可用的动作元类型（63个）"""
    # ── 波动类（6）──
    WAVE_SINGLE         = "wave_single"         # 单向波：沿弧线传递的单次波
    WAVE_STANDING       = "wave_standing"       # 驻波：固定节点的驻波
    WAVE_BREATH         = "wave_breath"         # 呼吸波：整体同步的呼吸
    WAVE_TRAVEL         = "wave_travel"         # 行波：持续传递的行波
    WAVE_REVERSE        = "wave_reverse"        # 反向波：与行波方向相反的波
    WAVE_SUPERPOSE      = "wave_superpose"      # 叠加波：两个波叠加产生干涉图案

    # ── 交叉类（6）──
    CROSS_SINGLE        = "cross_single"        # 单交叉：两电机一次交叉
    CROSS_DOUBLE        = "cross_double"        # 双交叉：两对电机同时交叉
    CROSS_CASCADE       = "cross_cascade"       # 级联交叉：多个交叉依次发生
    CROSS_MIRROR        = "cross_mirror"        # 镜像交叉：左右对称交叉
    CROSS_OBLIQUE       = "cross_oblique"       # 斜交叉：非正交方向的交叉
    CROSS_SPIRAL        = "cross_spiral"        # 螺旋交叉：交叉路径呈螺旋形

    # ── 扩散类（5）──
    DIFFUSE_CENTER      = "diffuse_center"      # 中心扩散：从中心向外
    DIFFUSE_EDGE        = "diffuse_edge"        # 边缘扩散：从边缘向中心
    DIFFUSE_RADIAL      = "diffuse_radial"      # 径向扩散：沿径向方向
    DIFFUSE_SPIRAL      = "diffuse_spiral"      # 螺旋扩散：螺旋形扩散
    DIFFUSE_SECTOR      = "diffuse_sector"      # 扇形扩散：扇形区域扩散

    # ── 脉冲类（6）──
    PULSE_BEAT          = "pulse_beat"          # 节拍脉冲：跟随节拍的冲击
    PULSE_ACCENT        = "pulse_accent"        # 重音脉冲：跟随重音的冲击
    PULSE_RANDOM        = "pulse_random"        # 随机脉冲：随机位置的冲击
    PULSE_CASCADE       = "pulse_cascade"       # 级联脉冲：依次触发的脉冲
    PULSE_DOUBLE        = "pulse_double"        # 双重脉冲：一拍内两次冲击
    PULSE_DECAY         = "pulse_decay"         # 消退脉冲：冲击后缓慢消退

    # ── 追逐类（5）──
    CHASE_LINEAR        = "chase_linear"        # 线性追逐：沿直线追逐
    CHASE_CIRCULAR      = "chase_circular"      # 圆形追逐：沿圆形追逐
    CHASE_RANDOM        = "chase_random"        # 随机追逐：随机跳跃追逐
    CHASE_BIDIRECTIONAL = "chase_bidirectional" # 双向追逐：两个点相向追逐
    CHASE_SPIRAL        = "chase_spiral"        # 螺旋追逐：沿螺旋路径追逐

    # ── 形态类（6）──
    SHAPE_ARC           = "shape_arc"           # 弧形：激活一段弧线
    SHAPE_RING          = "shape_ring"          # 环形：激活完整环
    SHAPE_SECTOR        = "shape_sector"        # 扇形：激活一个扇区
    SHAPE_SCATTER       = "shape_scatter"       # 散点：激活随机散点
    SHAPE_GRID          = "shape_grid"          # 网格：激活规则网格点
    SHAPE_STAR          = "shape_star"          # 星形：激活星形图案

    # ── 对话类（7）──
    DIALOGUE_CALL       = "dialogue_call"       # 呼唤：一侧发出信号
    DIALOGUE_RESPONSE   = "dialogue_response"   # 回应：另一侧接力响应
    DIALOGUE_ECHO_DELAY = "dialogue_echo_delay" # 回声延迟：延迟跟随
    DIALOGUE_ALTERNATE  = "dialogue_alternate"  # 问答交替：两组交替主导
    DIALOGUE_HANDOFF    = "dialogue_handoff"    # 接力：动作从一组传递到另一组
    DIALOGUE_MIRROR     = "dialogue_mirror"     # 镜像对话：两组做镜像动作
    DIALOGUE_POLYPHONY  = "dialogue_polyphony"  # 多声部对话：三组以上交织

    # ── 织体类（8）──
    TEXTURE_SPARSE      = "texture_sparse"      # 稀疏编织：少量设备交替
    TEXTURE_DENSE       = "texture_dense"       # 密集编织：设备快速交替
    TEXTURE_CASCADE_UP  = "texture_cascade_up"  # 瀑布积累：逐渐增加设备
    TEXTURE_CASCADE_DN  = "texture_cascade_dn"  # 瀑布释放：逐渐减少设备
    TEXTURE_INTERLOCK   = "texture_interlock"   # 交错填空：A组动时B组静
    TEXTURE_LAYER       = "texture_layer"       # 层叠：多层动作叠加
    TEXTURE_UNRAVEL     = "texture_unravel"     # 抽丝：逐渐从密集变稀疏
    TEXTURE_WEAVE_IN    = "texture_weave_in"    # 织入：新元素逐渐加入织体

    # ── 节奏切分类（5）──
    RHYTHM_SYNCOPATE    = "rhythm_syncopate"    # 切分：在非节拍点激活
    RHYTHM_DOTTED       = "rhythm_dotted"       # 附点节奏：长-短节奏型
    RHYTHM_TRIPLET      = "rhythm_triplet"      # 三连音爆发：一拍内三次
    RHYTHM_OFFBEAT      = "rhythm_offbeat"      # 反拍强调：强调弱拍
    RHYTHM_POLYRHYTHM   = "rhythm_polyrhythm"   # 复合节奏：两种节奏叠加

    # ── 情绪强度类（5）──
    EMOTION_TENSION     = "emotion_tension"     # 张力保持：缓慢升压后保持
    EMOTION_RELEASE     = "emotion_release"     # 释放跌落：高位快速跌落
    EMOTION_SHIMMER     = "emotion_shimmer"     # 微颤：高位细微快速抖动
    EMOTION_BURST       = "emotion_burst"       # 爆发冲击：瞬间全开
    EMOTION_SWELL       = "emotion_swell"       # 渐强积累：缓慢积累到高潮

    # ── 静态类（4）──
    HOLD_ALL            = "hold_all"            # 全开保持：所有设备保持开启
    HOLD_NONE           = "hold_none"           # 全关保持：所有设备关闭
    HOLD_PATTERN        = "hold_pattern"        # 图案保持：保持特定图案
    HOLD_FADE           = "hold_fade"           # 渐变保持：缓慢渐入/渐出


# ═══════════════════════════════════════════════════════════════════════════
#  动作元子参数定义
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MotifSubParam:
    """动作元的一个可变奏子参数"""
    name: str                           # 参数名
    description: str                    # 参数描述
    default: float                      # 默认值
    min_val: float                      # 最小值
    max_val: float                      # 最大值
    unit: str = ""                      # 单位（beats/degrees/0-1/etc）
    audio_mappable: bool = False        # 是否可以被音频参数实时映射
    audio_source: str = ""              # 推荐的音频映射源（rms/bpm/band_X/etc）


# ═══════════════════════════════════════════════════════════════════════════
#  动作元完整定义
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MotifDefinition:
    """一个动作元的完整定义"""
    motif_type: MotifType
    name: str
    description: str

    # 运动语义
    primary_semantic: MotionSemantic
    secondary_semantic: Optional[MotionSemantic] = None

    # 运动曲线
    primary_curve: MotionCurve = MotionCurve.EASE_IN_OUT
    secondary_curve: Optional[MotionCurve] = None  # 用于双阶段动作（如上升+下降）

    # 乐器亲和力（可以有多个）
    instrument_affinities: List[InstrumentAffinity] = field(default_factory=list)

    # 适合的乐句角色
    preferred_phrase_roles: List[str] = field(default_factory=list)
    # "question"=问句 / "answer"=答句 / "climax"=高潮 / "neutral"=中性 / "transition"=过渡

    # 适合的段落类型
    preferred_sections: List[str] = field(default_factory=list)
    # "intro"/"verse"/"chorus"/"bridge"/"outro"/"drop"/"break"

    # 复杂度要求（0..1，越高需要越多设备/通道）
    complexity_requirement: float = 0.5

    # 能量要求（0..1，适合的能量级别范围）
    energy_range: Tuple[float, float] = (0.0, 1.0)

    # 持续时间（以节拍为单位，可被变奏引擎缩放）
    duration_beats: float = 4.0

    # 可以与哪些动作元形成呼应（motif_type.value -> CorrespondenceType）
    correspondence_rules: Dict[str, CorrespondenceType] = field(default_factory=dict)

    # 精细化子参数（可变奏的维度）
    sub_params: List[MotifSubParam] = field(default_factory=list)

    # 变奏参数名列表（向后兼容）
    variation_params: List[str] = field(default_factory=list)

    # 空间拓扑偏好（影响激活拓扑元的生成）
    topo_preference: str = "arc"
    # "arc"=弧段 / "ring"=环形 / "scatter"=散点 / "sector"=扇形 / "grid"=网格

    # 是否适合作为主声部（True=可以单独作为主角）
    can_be_primary: bool = True

    # 是否适合作为装饰声部（True=适合作为背景层）
    can_be_decoration: bool = True

    # 情绪标签（用于主题引擎的情绪匹配）
    emotion_tags: List[str] = field(default_factory=list)
    # "joyful"/"melancholic"/"tense"/"peaceful"/"energetic"/"mysterious"/"triumphant"


# ═══════════════════════════════════════════════════════════════════════════
#  动作元词汇库（63个精细化动作元）
# ═══════════════════════════════════════════════════════════════════════════

class MotifVocabulary:
    """所有动作元的定义库"""

    def __init__(self):
        self._motifs: Dict[MotifType, MotifDefinition] = {}
        self._build_vocabulary()

    def _build_vocabulary(self) -> None:
        """构建完整的动作元词汇库（63个）"""
        defs = [

            # ════════════════════════════════════════════════════════════════
            #  波动类（6个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.WAVE_SINGLE,
                name="单向波",
                description=(
                    "一个波峰从弧线一端传递到另一端，完成后消失。"
                    "波峰宽度和传递速度可变，是最基础的叙事性动作。"
                    "问句乐句中常用，表达一种'提问'或'出发'的意象。"
                ),
                primary_semantic=MotionSemantic.FLOW,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                secondary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["question", "neutral"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.3,
                energy_range=(0.2, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_standing":    CorrespondenceType.ANTIPHON,
                    "wave_reverse":     CorrespondenceType.ANTIPHON,
                    "cross_single":     CorrespondenceType.LAYER,
                    "pulse_beat":       CorrespondenceType.LAYER,
                    "wave_breath":      CorrespondenceType.DIALOGUE,
                    "dialogue_response":CorrespondenceType.HANDOFF,
                    "chase_linear":     CorrespondenceType.SHADOW,
                },
                sub_params=[
                    MotifSubParam("speed", "波传递速度", 1.0, 0.2, 4.0, "beats/cycle",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("amplitude", "波峰幅度", 0.8, 0.2, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("width", "波峰宽度（占弧线比例）", 0.3, 0.05, 0.8, "0-1"),
                    MotifSubParam("direction", "传递方向（0=正向,1=反向）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("phase_offset", "相位偏移", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("tail_decay", "尾部衰减长度", 0.2, 0.0, 0.6, "0-1"),
                    MotifSubParam("peak_hold", "波峰保持时间", 0.0, 0.0, 0.5, "beats"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["peaceful", "flowing", "narrative"],
            ),

            MotifDefinition(
                motif_type=MotifType.WAVE_STANDING,
                name="驻波",
                description=(
                    "固定节点的驻波，波腹和波节交替振动。"
                    "节点位置固定，波腹处设备做呼吸运动。"
                    "适合和声段落，表达稳定的背景织体。"
                ),
                primary_semantic=MotionSemantic.RIPPLE,
                secondary_semantic=MotionSemantic.PULSE,
                primary_curve=MotionCurve.SINE,
                secondary_curve=MotionCurve.SINE,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["verse", "intro"],
                complexity_requirement=0.4,
                energy_range=(0.2, 0.7),
                duration_beats=8.0,
                correspondence_rules={
                    "wave_single":      CorrespondenceType.ANTIPHON,
                    "cross_mirror":     CorrespondenceType.UNISON,
                    "diffuse_center":   CorrespondenceType.COMPLEMENT,
                    "texture_layer":    CorrespondenceType.LAYER,
                    "wave_superpose":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("frequency", "振动频率（节点数）", 2.0, 1.0, 6.0, "nodes",
                                  audio_mappable=True, audio_source="band_2"),
                    MotifSubParam("amplitude", "振幅", 0.7, 0.1, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("node_count", "节点数量", 3.0, 1.0, 8.0, "count"),
                    MotifSubParam("phase_drift", "相位漂移速度", 0.0, -0.5, 0.5, "beats/cycle"),
                    MotifSubParam("node_width", "节点宽度", 0.1, 0.02, 0.3, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["peaceful", "mysterious", "ambient"],
            ),

            MotifDefinition(
                motif_type=MotifType.WAVE_BREATH,
                name="呼吸波",
                description=(
                    "所有设备同步的整体呼吸，随能量涨落。"
                    "最简单的动作元，但在情绪性段落中极为有效。"
                    "可以作为其他复杂动作的背景层，提供整体的'生命感'。"
                ),
                primary_semantic=MotionSemantic.ASCEND,
                secondary_semantic=MotionSemantic.DESCEND,
                primary_curve=MotionCurve.EASE_IN_OUT,
                secondary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.AMBIENT, InstrumentAffinity.SYNTH_PAD,
                                       InstrumentAffinity.VOCAL],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["intro", "outro", "bridge"],
                complexity_requirement=0.1,
                energy_range=(0.1, 0.6),
                duration_beats=8.0,
                correspondence_rules={
                    "wave_single":      CorrespondenceType.DIALOGUE,
                    "cross_single":     CorrespondenceType.COMPLEMENT,
                    "pulse_beat":       CorrespondenceType.LAYER,
                    "emotion_swell":    CorrespondenceType.HANDOFF,
                    "dialogue_call":    CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("rate", "呼吸速率", 0.5, 0.1, 2.0, "cycles/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("depth", "呼吸深度", 0.6, 0.1, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("offset", "相位偏移（用于多组不同步）", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("base_level", "基础水平（最低点）", 0.1, 0.0, 0.5, "0-1"),
                    MotifSubParam("sync_to_beat", "是否同步到节拍", 1.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["peaceful", "melancholic", "ambient"],
            ),

            MotifDefinition(
                motif_type=MotifType.WAVE_TRAVEL,
                name="行波",
                description=(
                    "持续传递的行波，不间断地从一端到另一端，到达后从另一端重新开始。"
                    "与单向波的区别：行波是持续循环的，单向波是一次性的。"
                    "适合副歌段落，提供持续的动感背景。"
                ),
                primary_semantic=MotionSemantic.FLOW,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.LINEAR,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["chorus", "verse"],
                complexity_requirement=0.4,
                energy_range=(0.4, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "cross_cascade":    CorrespondenceType.LAYER,
                    "pulse_cascade":    CorrespondenceType.ECHO,
                    "chase_linear":     CorrespondenceType.UNISON,
                    "wave_reverse":     CorrespondenceType.ANTIPHON,
                    "texture_dense":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("speed", "行波速度", 1.0, 0.2, 5.0, "cycles/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("width", "波宽（占弧线比例）", 0.25, 0.05, 0.7, "0-1"),
                    MotifSubParam("direction", "方向（0=正,1=反）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("amplitude", "幅度", 0.9, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("sharpness", "波前锐利度", 0.5, 0.0, 1.0, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["energetic", "flowing", "continuous"],
            ),

            MotifDefinition(
                motif_type=MotifType.WAVE_REVERSE,
                name="反向波",
                description=(
                    "与行波方向相反的波，常与行波配对形成对位效果。"
                    "两波相遇时产生短暂的叠加高峰，形成自然的高潮点。"
                    "适合答句乐句，表达'回应'的意象。"
                ),
                primary_semantic=MotionSemantic.FLOW,
                secondary_semantic=MotionSemantic.RESPOND,
                primary_curve=MotionCurve.LINEAR,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.HARMONY],
                preferred_phrase_roles=["answer", "neutral"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.9),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_travel":      CorrespondenceType.ANTIPHON,
                    "wave_single":      CorrespondenceType.ANTIPHON,
                    "cross_mirror":     CorrespondenceType.LAYER,
                    "dialogue_response":CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("speed", "速度", 1.0, 0.2, 5.0, "cycles/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("width", "波宽", 0.25, 0.05, 0.7, "0-1"),
                    MotifSubParam("amplitude", "幅度", 0.8, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("phase_offset", "相对于正向波的相位偏移", 0.5, 0.0, 1.0, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["responsive", "flowing", "dialogue"],
            ),

            MotifDefinition(
                motif_type=MotifType.WAVE_SUPERPOSE,
                name="叠加波",
                description=(
                    "两个不同频率/方向的波叠加，产生干涉图案。"
                    "叠加区域产生高峰，抵消区域产生低谷，形成复杂的空间图案。"
                    "适合复杂段落，表达'多声部'的音乐织体。"
                ),
                primary_semantic=MotionSemantic.RIPPLE,
                secondary_semantic=MotionSemantic.WEAVE,
                primary_curve=MotionCurve.SINE,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.7,
                energy_range=(0.4, 1.0),
                duration_beats=8.0,
                correspondence_rules={
                    "wave_standing":    CorrespondenceType.LAYER,
                    "texture_layer":    CorrespondenceType.LAYER,
                    "diffuse_center":   CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("freq_ratio", "两波频率比", 1.5, 0.5, 4.0, "ratio"),
                    MotifSubParam("amplitude_a", "波A幅度", 0.7, 0.1, 1.0, "0-1",
                                  audio_mappable=True, audio_source="band_3"),
                    MotifSubParam("amplitude_b", "波B幅度", 0.5, 0.1, 1.0, "0-1",
                                  audio_mappable=True, audio_source="band_5"),
                    MotifSubParam("angle", "两波夹角", 90.0, 0.0, 180.0, "degrees"),
                    MotifSubParam("drift_speed", "干涉图案漂移速度", 0.1, 0.0, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["complex", "mysterious", "layered"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  交叉类（6个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.CROSS_SINGLE,
                name="单交叉",
                description=(
                    "两个电机从两侧向中心靠拢，在中心交叉后分开。"
                    "交叉瞬间是动作的高潮点，可以与节拍点对齐。"
                    "是最具'打招呼'感的动作元，表达相遇与分离。"
                ),
                primary_semantic=MotionSemantic.APPROACH,
                secondary_semantic=MotionSemantic.DIVERGE,
                primary_curve=MotionCurve.EASE_IN,
                secondary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.SNARE],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["chorus", "verse"],
                complexity_requirement=0.5,
                energy_range=(0.4, 1.0),
                duration_beats=2.0,
                correspondence_rules={
                    "wave_single":          CorrespondenceType.LAYER,
                    "wave_breath":          CorrespondenceType.COMPLEMENT,
                    "cross_mirror":         CorrespondenceType.UNISON,
                    "diffuse_center":       CorrespondenceType.ANTIPHON,
                    "pulse_beat":           CorrespondenceType.UNISON,
                    "dialogue_call":        CorrespondenceType.UNISON,
                    "emotion_burst":        CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("speed", "交叉速度", 1.0, 0.3, 3.0, "beats/cross",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("angle", "交叉角度（0=正交,1=斜交）", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("timing_offset", "交叉时刻相对于节拍的偏移", 0.0, -0.5, 0.5, "beats"),
                    MotifSubParam("depth", "交叉深度（过冲量）", 0.1, 0.0, 0.5, "0-1"),
                    MotifSubParam("hold_at_cross", "交叉点停留时间", 0.0, 0.0, 0.5, "beats"),
                    MotifSubParam("pump_sync", "交叉时泵压同步冲击", 1.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["energetic", "dramatic", "encounter"],
            ),

            MotifDefinition(
                motif_type=MotifType.CROSS_DOUBLE,
                name="双交叉",
                description=(
                    "两对电机同时交叉，形成X形图案。"
                    "比单交叉更具视觉冲击力，适合副歌高潮。"
                    "两对交叉可以同时发生，也可以有微小的时间差（更有层次感）。"
                ),
                primary_semantic=MotionSemantic.CONVERGE,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.SHARP_ATTACK,
                secondary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["climax"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.7,
                energy_range=(0.6, 1.0),
                duration_beats=2.0,
                correspondence_rules={
                    "diffuse_center":   CorrespondenceType.ANTIPHON,
                    "cross_single":     CorrespondenceType.AUGMENT,
                    "pulse_accent":     CorrespondenceType.UNISON,
                    "emotion_burst":    CorrespondenceType.UNISON,
                    "shape_ring":       CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("speed", "交叉速度", 1.2, 0.5, 3.0, "beats/cross"),
                    MotifSubParam("angle", "X形角度", 90.0, 30.0, 150.0, "degrees"),
                    MotifSubParam("time_stagger", "两对交叉的时间差", 0.0, 0.0, 0.5, "beats"),
                    MotifSubParam("symmetry", "对称轴角度", 0.0, 0.0, 180.0, "degrees"),
                    MotifSubParam("pump_burst", "交叉时泵压爆发强度", 0.8, 0.0, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["triumphant", "energetic", "climactic"],
            ),

            MotifDefinition(
                motif_type=MotifType.CROSS_CASCADE,
                name="级联交叉",
                description=(
                    "多个交叉动作依次发生，形成波浪式的交叉序列。"
                    "每个交叉在前一个完成后的半拍开始，形成多米诺效果。"
                    "适合旋律性段落，表达'一连串事件'的叙事感。"
                ),
                primary_semantic=MotionSemantic.CHASE,
                secondary_semantic=MotionSemantic.FLOW,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.PIANO],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["chorus", "verse"],
                complexity_requirement=0.6,
                energy_range=(0.5, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_travel":      CorrespondenceType.LAYER,
                    "pulse_cascade":    CorrespondenceType.UNISON,
                    "chase_linear":     CorrespondenceType.ECHO,
                    "texture_cascade_up": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("speed", "单次交叉速度", 0.8, 0.3, 2.0, "beats/cross"),
                    MotifSubParam("interval", "相邻交叉的时间间隔", 0.5, 0.1, 2.0, "beats"),
                    MotifSubParam("direction", "级联方向（0=正,1=反）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("count", "级联次数", 4.0, 2.0, 8.0, "count"),
                    MotifSubParam("amplitude_decay", "幅度衰减（后面的交叉越来越小）", 0.0, 0.0, 0.5, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["narrative", "energetic", "sequential"],
            ),

            MotifDefinition(
                motif_type=MotifType.CROSS_MIRROR,
                name="镜像交叉",
                description=(
                    "左右对称的交叉，两侧同时向中心靠拢，形成完美的镜像。"
                    "强调对称美感，适合答句乐句或高潮时刻。"
                    "交叉点在中心，两侧的动作完全对称。"
                ),
                primary_semantic=MotionSemantic.CONVERGE,
                secondary_semantic=MotionSemantic.APPROACH,
                primary_curve=MotionCurve.EASE_IN,
                secondary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.HARMONY],
                preferred_phrase_roles=["answer", "climax"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.4, 0.9),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_standing":    CorrespondenceType.UNISON,
                    "cross_single":     CorrespondenceType.AUGMENT,
                    "diffuse_center":   CorrespondenceType.DIALOGUE,
                    "dialogue_mirror":  CorrespondenceType.UNISON,
                    "shape_ring":       CorrespondenceType.HANDOFF,
                },
                sub_params=[
                    MotifSubParam("speed", "速度", 1.0, 0.3, 3.0, "beats/cross"),
                    MotifSubParam("angle", "交叉角度", 0.0, -45.0, 45.0, "degrees"),
                    MotifSubParam("phase", "两侧相位差（0=同步,0.5=交替）", 0.0, 0.0, 0.5, "0-1"),
                    MotifSubParam("mirror_axis", "镜像轴角度", 0.0, 0.0, 180.0, "degrees"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["symmetric", "balanced", "triumphant"],
            ),

            MotifDefinition(
                motif_type=MotifType.CROSS_OBLIQUE,
                name="斜交叉",
                description=(
                    "非正交方向的交叉，两个电机以斜角相交。"
                    "比正交叉更有动感，适合切分节奏段落。"
                    "斜角可以根据音乐情绪动态调整。"
                ),
                primary_semantic=MotionSemantic.APPROACH,
                secondary_semantic=MotionSemantic.SYNCOPATE,
                primary_curve=MotionCurve.EASE_IN,
                instrument_affinities=[InstrumentAffinity.SNARE, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["question", "neutral"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.4, 0.9),
                duration_beats=2.0,
                correspondence_rules={
                    "cross_single":     CorrespondenceType.SHADOW,
                    "rhythm_syncopate": CorrespondenceType.UNISON,
                    "wave_single":      CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("oblique_angle", "斜角（相对于正交的偏转）", 30.0, 10.0, 70.0, "degrees",
                                  audio_mappable=True, audio_source="band_4"),
                    MotifSubParam("speed", "速度", 1.0, 0.3, 3.0, "beats/cross"),
                    MotifSubParam("asymmetry", "不对称度（两侧速度差）", 0.0, 0.0, 0.5, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["dynamic", "asymmetric", "rhythmic"],
            ),

            MotifDefinition(
                motif_type=MotifType.CROSS_SPIRAL,
                name="螺旋交叉",
                description=(
                    "交叉路径呈螺旋形，两个电机在相交的同时做旋转运动。"
                    "视觉上最复杂的交叉形式，适合高能量段落。"
                    "螺旋的圈数和速度可以根据音乐强度调整。"
                ),
                primary_semantic=MotionSemantic.ROTATE,
                secondary_semantic=MotionSemantic.APPROACH,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["climax"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.8,
                energy_range=(0.6, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "diffuse_spiral":   CorrespondenceType.LAYER,
                    "chase_spiral":     CorrespondenceType.UNISON,
                    "cross_double":     CorrespondenceType.AUGMENT,
                },
                sub_params=[
                    MotifSubParam("spiral_turns", "螺旋圈数", 1.5, 0.5, 4.0, "turns"),
                    MotifSubParam("spiral_radius", "螺旋半径变化", 0.5, 0.1, 1.0, "0-1"),
                    MotifSubParam("speed", "速度", 1.0, 0.3, 2.0, "beats/turn"),
                    MotifSubParam("direction", "旋转方向（0=顺,1=逆）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["complex", "triumphant", "spectacular"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  扩散类（5个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.DIFFUSE_CENTER,
                name="中心扩散",
                description=(
                    "从中心喷头向外逐渐激活，形成扩散效果。"
                    "像水面上的涟漪，从中心向外传播。"
                    "适合答句乐句或段落结束，表达'绽放'或'释放'的意象。"
                ),
                primary_semantic=MotionSemantic.DIVERGE,
                secondary_semantic=MotionSemantic.RIPPLE,
                primary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.AMBIENT],
                preferred_phrase_roles=["answer", "neutral"],
                preferred_sections=["intro", "bridge", "outro"],
                complexity_requirement=0.4,
                energy_range=(0.2, 0.8),
                duration_beats=8.0,
                correspondence_rules={
                    "cross_single":     CorrespondenceType.ANTIPHON,
                    "cross_double":     CorrespondenceType.ANTIPHON,
                    "cross_mirror":     CorrespondenceType.DIALOGUE,
                    "diffuse_edge":     CorrespondenceType.ANTIPHON,
                    "dialogue_call":    CorrespondenceType.ECHO,
                    "emotion_swell":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("speed", "扩散速度", 1.0, 0.2, 4.0, "radius/beat",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("max_radius", "最大扩散半径", 1.0, 0.3, 1.0, "0-1"),
                    MotifSubParam("fade_width", "扩散边缘渐变宽度", 0.2, 0.0, 0.5, "0-1"),
                    MotifSubParam("ring_count", "同心环数量（0=连续扩散）", 0.0, 0.0, 4.0, "count"),
                    MotifSubParam("hold_at_edge", "到达边缘后保持时间", 0.5, 0.0, 2.0, "beats"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["peaceful", "expansive", "releasing"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIFFUSE_EDGE,
                name="边缘汇聚",
                description=(
                    "从边缘向中心收拢，形成汇聚效果。"
                    "与中心扩散相反，表达'凝聚'或'收拢'的意象。"
                    "适合问句乐句，表达期待和张力。"
                ),
                primary_semantic=MotionSemantic.CONVERGE,
                secondary_semantic=MotionSemantic.APPROACH,
                primary_curve=MotionCurve.EASE_IN,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.9),
                duration_beats=8.0,
                correspondence_rules={
                    "diffuse_center":   CorrespondenceType.ANTIPHON,
                    "wave_breath":      CorrespondenceType.COMPLEMENT,
                    "emotion_tension":  CorrespondenceType.LAYER,
                    "dialogue_response":CorrespondenceType.HANDOFF,
                },
                sub_params=[
                    MotifSubParam("speed", "汇聚速度", 1.0, 0.2, 4.0, "radius/beat",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("min_radius", "最小汇聚半径", 0.0, 0.0, 0.5, "0-1"),
                    MotifSubParam("fade_width", "边缘渐变宽度", 0.2, 0.0, 0.5, "0-1"),
                    MotifSubParam("spiral_drift", "汇聚时的螺旋漂移", 0.0, 0.0, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["tense", "converging", "focused"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIFFUSE_RADIAL,
                name="径向扩散",
                description=(
                    "沿径向方向扩散，形成放射状图案。"
                    "像太阳光线一样从中心向外放射。"
                    "适合高潮时刻，表达'爆发'或'辉煌'的意象。"
                ),
                primary_semantic=MotionSemantic.DIVERGE,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.EXPONENTIAL,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["climax", "answer"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.5,
                energy_range=(0.5, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "shape_ring":       CorrespondenceType.COMPLEMENT,
                    "cross_double":     CorrespondenceType.LAYER,
                    "emotion_burst":    CorrespondenceType.UNISON,
                    "diffuse_center":   CorrespondenceType.AUGMENT,
                },
                sub_params=[
                    MotifSubParam("ray_count", "放射线数量", 6.0, 2.0, 12.0, "count"),
                    MotifSubParam("speed", "扩散速度", 1.5, 0.5, 4.0, "radius/beat",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("rotation", "整体旋转速度", 0.0, -1.0, 1.0, "turns/beat"),
                    MotifSubParam("ray_width", "射线宽度", 0.15, 0.05, 0.4, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["triumphant", "radiant", "explosive"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIFFUSE_SPIRAL,
                name="螺旋扩散",
                description=(
                    "螺旋形扩散，激活图案沿螺旋路径展开。"
                    "结合了旋转和扩散两种运动，视觉上最为优雅。"
                    "适合过渡段落，表达'演化'或'变形'的意象。"
                ),
                primary_semantic=MotionSemantic.ROTATE,
                secondary_semantic=MotionSemantic.DIVERGE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.HARMONY],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.6,
                energy_range=(0.4, 1.0),
                duration_beats=8.0,
                correspondence_rules={
                    "chase_circular":   CorrespondenceType.LAYER,
                    "diffuse_center":   CorrespondenceType.HANDOFF,
                    "cross_spiral":     CorrespondenceType.UNISON,
                    "chase_spiral":     CorrespondenceType.SHADOW,
                },
                sub_params=[
                    MotifSubParam("speed", "螺旋速度", 0.5, 0.1, 2.0, "turns/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("turns", "螺旋圈数", 2.0, 0.5, 5.0, "turns"),
                    MotifSubParam("direction", "旋转方向（0=顺,1=逆）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("arm_count", "螺旋臂数量", 1.0, 1.0, 4.0, "count"),
                    MotifSubParam("tightness", "螺旋紧密度", 0.5, 0.1, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["elegant", "evolving", "complex"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIFFUSE_SECTOR,
                name="扇形扩散",
                description=(
                    "扇形区域内的扩散，只激活特定角度范围内的设备。"
                    "可以用于强调空间的某个方向，表达'指向性'。"
                    "适合与其他扩散类动作配合，形成分区的空间叙事。"
                ),
                primary_semantic=MotionSemantic.DIVERGE,
                secondary_semantic=MotionSemantic.FLOW,
                primary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["neutral", "question"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "diffuse_center":   CorrespondenceType.COMPLEMENT,
                    "shape_sector":     CorrespondenceType.LAYER,
                    "dialogue_call":    CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("sector_angle", "扇形角度", 90.0, 30.0, 270.0, "degrees"),
                    MotifSubParam("sector_start", "扇形起始角度", 0.0, 0.0, 360.0, "degrees",
                                  audio_mappable=True, audio_source="band_3"),
                    MotifSubParam("speed", "扩散速度", 1.0, 0.2, 3.0, "radius/beat"),
                    MotifSubParam("rotate_sector", "扇形旋转速度", 0.0, -0.5, 0.5, "turns/beat"),
                ],
                topo_preference="sector",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["directional", "focused", "asymmetric"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  脉冲类（6个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.PULSE_BEAT,
                name="节拍脉冲",
                description=(
                    "跟随节拍的冲击，每个节拍点产生一次脉冲。"
                    "最基础的节奏性动作，提供稳定的节拍感。"
                    "可以选择特定的设备组，形成空间上的节拍标记。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=None,
                primary_curve=MotionCurve.SHARP_ATTACK,
                secondary_curve=MotionCurve.SHARP_DECAY,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["neutral", "climax"],
                preferred_sections=["chorus", "verse", "drop"],
                complexity_requirement=0.2,
                energy_range=(0.3, 1.0),
                duration_beats=1.0,
                correspondence_rules={
                    "wave_single":      CorrespondenceType.LAYER,
                    "wave_breath":      CorrespondenceType.LAYER,
                    "cross_single":     CorrespondenceType.UNISON,
                    "chase_linear":     CorrespondenceType.ECHO,
                    "rhythm_syncopate": CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("width", "脉冲宽度（占一拍的比例）", 0.2, 0.05, 0.8, "0-1"),
                    MotifSubParam("decay", "衰减时间", 0.3, 0.05, 1.0, "beats",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("target_group", "目标设备组（0=全部）", 0.0, 0.0, 8.0, "group_id"),
                    MotifSubParam("beat_division", "节拍细分（1=每拍,2=每半拍）", 1.0, 1.0, 4.0, "division"),
                    MotifSubParam("amplitude", "脉冲幅度", 1.0, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="band_1"),
                ],
                topo_preference="scatter",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["rhythmic", "energetic", "driving"],
            ),

            MotifDefinition(
                motif_type=MotifType.PULSE_ACCENT,
                name="重音脉冲",
                description=(
                    "跟随音乐重音（通常是第1、3拍）的强冲击。"
                    "比节拍脉冲更强烈，专门强调重音位置。"
                    "适合高能量段落，提供强烈的节奏锚点。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.ASCEND,
                primary_curve=MotionCurve.SHARP_ATTACK,
                secondary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.SNARE, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["climax", "question"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.3,
                energy_range=(0.5, 1.0),
                duration_beats=2.0,
                correspondence_rules={
                    "cross_single":     CorrespondenceType.UNISON,
                    "diffuse_center":   CorrespondenceType.ECHO,
                    "pulse_beat":       CorrespondenceType.COMPLEMENT,
                    "emotion_burst":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("width", "脉冲宽度", 0.3, 0.1, 0.8, "0-1"),
                    MotifSubParam("decay", "衰减时间", 0.5, 0.1, 1.5, "beats"),
                    MotifSubParam("accent_pattern", "重音模式（0=1,3拍;1=2,4拍）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("spatial_spread", "空间扩散（脉冲从中心向外扩散）", 0.5, 0.0, 1.0, "0-1"),
                    MotifSubParam("pump_spike", "泵压冲击强度", 0.8, 0.0, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["powerful", "accented", "driving"],
            ),

            MotifDefinition(
                motif_type=MotifType.PULSE_RANDOM,
                name="随机脉冲",
                description=(
                    "随机位置的冲击，每次脉冲激活不同的设备。"
                    "产生'星星点点'的视觉效果，适合打击乐密集段落。"
                    "随机性可以被控制，形成有规律的随机感。"
                ),
                primary_semantic=MotionSemantic.SCATTER,
                secondary_semantic=MotionSemantic.PULSE,
                primary_curve=MotionCurve.PULSE_CURVE,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.HIHAT],
                preferred_phrase_roles=["neutral", "climax"],
                preferred_sections=["bridge", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.9),
                duration_beats=2.0,
                correspondence_rules={
                    "shape_scatter":    CorrespondenceType.UNISON,
                    "wave_breath":      CorrespondenceType.COMPLEMENT,
                    "texture_sparse":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("density", "脉冲密度（每拍触发次数）", 2.0, 0.5, 8.0, "count/beat",
                                  audio_mappable=True, audio_source="band_6"),
                    MotifSubParam("randomness", "随机程度（0=规律,1=完全随机）", 0.7, 0.0, 1.0, "0-1"),
                    MotifSubParam("width", "单次脉冲宽度", 0.15, 0.05, 0.5, "0-1"),
                    MotifSubParam("cluster_size", "簇大小（每次激活几个相邻设备）", 1.0, 1.0, 4.0, "count"),
                    MotifSubParam("seed_drift", "随机种子漂移速度", 0.1, 0.0, 1.0, "0-1"),
                ],
                topo_preference="scatter",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["chaotic", "energetic", "textural"],
            ),

            MotifDefinition(
                motif_type=MotifType.PULSE_CASCADE,
                name="级联脉冲",
                description=(
                    "脉冲从一端依次传递到另一端，形成多米诺效果。"
                    "每个设备在前一个触发后的固定延迟后触发。"
                    "适合旋律线条，表达'一个音符接一个音符'的感觉。"
                ),
                primary_semantic=MotionSemantic.CHASE,
                secondary_semantic=MotionSemantic.FLOW,
                primary_curve=MotionCurve.PULSE_CURVE,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.PIANO],
                preferred_phrase_roles=["question", "climax"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.5,
                energy_range=(0.4, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_travel":      CorrespondenceType.ECHO,
                    "cross_cascade":    CorrespondenceType.UNISON,
                    "chase_linear":     CorrespondenceType.LAYER,
                    "texture_cascade_up": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("speed", "级联速度", 1.0, 0.2, 4.0, "devices/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("width", "单次脉冲宽度", 0.2, 0.05, 0.5, "0-1"),
                    MotifSubParam("direction", "方向（0=正,1=反）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("tail_length", "拖尾长度", 0.3, 0.0, 0.8, "0-1"),
                    MotifSubParam("bounce", "到达末端后反弹", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["sequential", "flowing", "melodic"],
            ),

            MotifDefinition(
                motif_type=MotifType.PULSE_DOUBLE,
                name="双重脉冲",
                description=(
                    "一拍内两次冲击，形成'咚-咚'的双击效果。"
                    "第二次冲击比第一次稍弱，形成自然的强-弱对比。"
                    "适合附点节奏或强调性段落。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.SYNCOPATE,
                primary_curve=MotionCurve.SHARP_ATTACK,
                secondary_curve=MotionCurve.SHARP_DECAY,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.SNARE],
                preferred_phrase_roles=["climax", "question"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.4,
                energy_range=(0.5, 1.0),
                duration_beats=1.0,
                correspondence_rules={
                    "pulse_beat":       CorrespondenceType.AUGMENT,
                    "rhythm_dotted":    CorrespondenceType.UNISON,
                    "cross_single":     CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("interval", "两次冲击的间隔", 0.25, 0.1, 0.5, "beats"),
                    MotifSubParam("second_amplitude", "第二次冲击幅度（相对第一次）", 0.6, 0.2, 1.0, "0-1"),
                    MotifSubParam("width", "单次脉冲宽度", 0.15, 0.05, 0.4, "0-1"),
                    MotifSubParam("spatial_offset", "两次冲击的空间偏移", 0.0, 0.0, 0.5, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["punchy", "rhythmic", "emphatic"],
            ),

            MotifDefinition(
                motif_type=MotifType.PULSE_DECAY,
                name="消退脉冲",
                description=(
                    "冲击后缓慢消退，形成'余音绕梁'的效果。"
                    "与普通脉冲的区别：消退时间更长，有更多的'余韵'。"
                    "适合情绪性段落，表达'回响'或'余味'。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.DESCEND,
                primary_curve=MotionCurve.SHARP_ATTACK,
                secondary_curve=MotionCurve.LOGARITHMIC,
                instrument_affinities=[InstrumentAffinity.PIANO, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["answer", "neutral"],
                preferred_sections=["bridge", "outro", "verse"],
                complexity_requirement=0.3,
                energy_range=(0.3, 0.8),
                duration_beats=2.0,
                correspondence_rules={
                    "wave_breath":      CorrespondenceType.LAYER,
                    "dialogue_echo_delay": CorrespondenceType.UNISON,
                    "emotion_release":  CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("attack", "起始冲击速度", 0.05, 0.01, 0.2, "beats"),
                    MotifSubParam("decay_time", "消退时间", 1.5, 0.3, 4.0, "beats",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("sustain_level", "持续电平", 0.2, 0.0, 0.6, "0-1"),
                    MotifSubParam("spatial_spread", "消退时空间扩散", 0.3, 0.0, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["melancholic", "resonant", "fading"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  追逐类（5个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.CHASE_LINEAR,
                name="线性追逐",
                description=(
                    "一个激活点沿弧线线性追逐，形成流动感。"
                    "追逐点可以有拖尾，形成彗星效果。"
                    "适合旋律性段落，追逐速度跟随旋律的节奏感。"
                ),
                primary_semantic=MotionSemantic.CHASE,
                secondary_semantic=MotionSemantic.FLOW,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["question", "neutral"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.3,
                energy_range=(0.3, 0.9),
                duration_beats=4.0,
                correspondence_rules={
                    "wave_travel":      CorrespondenceType.UNISON,
                    "pulse_cascade":    CorrespondenceType.LAYER,
                    "cross_cascade":    CorrespondenceType.ECHO,
                    "chase_bidirectional": CorrespondenceType.ANTIPHON,
                },
                sub_params=[
                    MotifSubParam("speed", "追逐速度", 1.0, 0.2, 4.0, "cycles/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("width", "追逐点宽度", 0.15, 0.03, 0.5, "0-1"),
                    MotifSubParam("direction", "方向（0=正,1=反）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("bounce", "到达末端后反弹", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("tail_length", "拖尾长度", 0.3, 0.0, 0.8, "0-1"),
                    MotifSubParam("tail_decay", "拖尾衰减速度", 0.5, 0.1, 1.0, "0-1"),
                    MotifSubParam("acceleration", "加速度（正=加速,负=减速）", 0.0, -0.5, 0.5, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["flowing", "narrative", "chasing"],
            ),

            MotifDefinition(
                motif_type=MotifType.CHASE_CIRCULAR,
                name="圆形追逐",
                description=(
                    "激活点沿圆形路径旋转，形成旋转感。"
                    "可以有多个追逐点，形成多重旋转效果。"
                    "适合过渡段落，表达'循环'或'旋转'的意象。"
                ),
                primary_semantic=MotionSemantic.ROTATE,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.LINEAR,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.HARMONY],
                preferred_phrase_roles=["neutral", "question"],
                preferred_sections=["bridge", "verse"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=8.0,
                correspondence_rules={
                    "diffuse_spiral":   CorrespondenceType.LAYER,
                    "chase_linear":     CorrespondenceType.ANTIPHON,
                    "cross_spiral":     CorrespondenceType.LAYER,
                    "shape_ring":       CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("speed", "旋转速度", 0.5, 0.1, 3.0, "turns/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("width", "追逐点宽度", 0.15, 0.03, 0.5, "0-1"),
                    MotifSubParam("direction", "方向（0=顺,1=逆）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("point_count", "追逐点数量", 1.0, 1.0, 4.0, "count"),
                    MotifSubParam("radius_variation", "半径变化（0=固定,1=呼吸）", 0.0, 0.0, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=True,
                emotion_tags=["circular", "hypnotic", "continuous"],
            ),

            MotifDefinition(
                motif_type=MotifType.CHASE_RANDOM,
                name="随机追逐",
                description=(
                    "追逐点随机跳跃，每次跳到不同位置。"
                    "跳跃的时机和目标都有随机性，但可以设置偏好区域。"
                    "适合打击乐密集段落，表达'活泼'或'不可预测'的感觉。"
                ),
                primary_semantic=MotionSemantic.SCATTER,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.STEP,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.HIHAT],
                preferred_phrase_roles=["neutral", "climax"],
                preferred_sections=["bridge", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.4, 0.9),
                duration_beats=2.0,
                correspondence_rules={
                    "pulse_random":     CorrespondenceType.LAYER,
                    "texture_sparse":   CorrespondenceType.COMPLEMENT,
                    "shape_scatter":    CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("jump_rate", "跳跃频率（每拍次数）", 2.0, 0.5, 8.0, "jumps/beat",
                                  audio_mappable=True, audio_source="band_5"),
                    MotifSubParam("width", "激活点宽度", 0.1, 0.03, 0.4, "0-1"),
                    MotifSubParam("randomness", "随机程度", 0.8, 0.2, 1.0, "0-1"),
                    MotifSubParam("preference_zone", "偏好区域（0=均匀,1=偏向中心）", 0.0, 0.0, 1.0, "0-1"),
                ],
                topo_preference="scatter",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["chaotic", "playful", "unpredictable"],
            ),

            MotifDefinition(
                motif_type=MotifType.CHASE_BIDIRECTIONAL,
                name="双向追逐",
                description=(
                    "两个追逐点从两端相向运动，在中间相遇后交错继续。"
                    "相遇时产生短暂的叠加高峰，形成自然的节奏强调。"
                    "适合问答结构，两个点代表两个'声音'的对话。"
                ),
                primary_semantic=MotionSemantic.APPROACH,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.BASS_LINE],
                preferred_phrase_roles=["question", "answer"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.4, 0.9),
                duration_beats=4.0,
                correspondence_rules={
                    "chase_linear":     CorrespondenceType.ANTIPHON,
                    "cross_single":     CorrespondenceType.ECHO,
                    "dialogue_alternate": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("speed", "追逐速度", 1.0, 0.3, 3.0, "cycles/beat"),
                    MotifSubParam("phase_offset", "两点相位差", 0.5, 0.0, 1.0, "0-1"),
                    MotifSubParam("width", "追逐点宽度", 0.15, 0.05, 0.4, "0-1"),
                    MotifSubParam("meeting_burst", "相遇时的爆发强度", 0.5, 0.0, 1.0, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["dialogue", "encounter", "dynamic"],
            ),

            MotifDefinition(
                motif_type=MotifType.CHASE_SPIRAL,
                name="螺旋追逐",
                description=(
                    "追逐点沿螺旋路径运动，同时做旋转和径向移动。"
                    "视觉上最复杂的追逐形式，适合高能量段落。"
                    "螺旋半径可以随音乐能量变化。"
                ),
                primary_semantic=MotionSemantic.ROTATE,
                secondary_semantic=MotionSemantic.DIVERGE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["climax", "question"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.7,
                energy_range=(0.5, 1.0),
                duration_beats=8.0,
                correspondence_rules={
                    "diffuse_spiral":   CorrespondenceType.LAYER,
                    "cross_spiral":     CorrespondenceType.SHADOW,
                    "chase_circular":   CorrespondenceType.AUGMENT,
                },
                sub_params=[
                    MotifSubParam("angular_speed", "角速度", 0.5, 0.1, 2.0, "turns/beat",
                                  audio_mappable=True, audio_source="bpm"),
                    MotifSubParam("radial_speed", "径向速度", 0.3, 0.05, 1.0, "radius/beat",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("direction", "方向（0=向外,1=向内）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("width", "追逐点宽度", 0.15, 0.05, 0.4, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["spectacular", "complex", "evolving"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  形态类（6个）
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.SHAPE_ARC,
                name="弧形激活",
                description=(
                    "激活一段弧线上的设备，形成弧形图案。"
                    "弧线的起止角度和宽度可以动态调整。"
                    "是最基础的空间形态，适合作为其他动作的背景。"
                ),
                primary_semantic=MotionSemantic.FLOW,
                secondary_semantic=None,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.AMBIENT],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["intro", "bridge", "outro"],
                complexity_requirement=0.2,
                energy_range=(0.1, 0.7),
                duration_beats=4.0,
                correspondence_rules={
                    "shape_ring":       CorrespondenceType.HANDOFF,
                    "wave_single":      CorrespondenceType.LAYER,
                    "dialogue_call":    CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("start_angle", "弧线起始角度", 0.0, 0.0, 360.0, "degrees"),
                    MotifSubParam("arc_length", "弧线长度", 120.0, 10.0, 360.0, "degrees",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("fade_edges", "边缘渐变", 0.2, 0.0, 0.5, "0-1"),
                    MotifSubParam("rotate_speed", "弧线旋转速度", 0.0, -1.0, 1.0, "turns/beat"),
                    MotifSubParam("amplitude", "激活幅度", 0.8, 0.2, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["gentle", "ambient", "structural"],
            ),

            MotifDefinition(
                motif_type=MotifType.SHAPE_RING,
                name="环形激活",
                description=(
                    "激活完整环形上的所有设备，形成完整的圆环。"
                    "是最具'全场感'的形态，适合高潮时刻。"
                    "可以有渐入渐出，也可以瞬间全开。"
                ),
                primary_semantic=MotionSemantic.CONVERGE,
                secondary_semantic=MotionSemantic.RIPPLE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["climax", "answer"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.3,
                energy_range=(0.5, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "shape_arc":        CorrespondenceType.HANDOFF,
                    "diffuse_center":   CorrespondenceType.COMPLEMENT,
                    "cross_double":     CorrespondenceType.LAYER,
                    "hold_all":         CorrespondenceType.HANDOFF,
                    "emotion_burst":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("fade_in", "渐入时间", 0.5, 0.0, 2.0, "beats"),
                    MotifSubParam("hold_time", "保持时间", 2.0, 0.0, 8.0, "beats"),
                    MotifSubParam("fade_out", "渐出时间", 0.5, 0.0, 2.0, "beats"),
                    MotifSubParam("amplitude", "激活幅度", 1.0, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("pulse_rate", "保持期间的脉冲速率（0=无脉冲）", 0.0, 0.0, 4.0, "pulses/beat"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["triumphant", "complete", "climactic"],
            ),

            MotifDefinition(
                motif_type=MotifType.SHAPE_SECTOR,
                name="扇形激活",
                description=(
                    "激活一个扇形区域内的设备，形成扇形图案。"
                    "扇形可以旋转，形成'扫描'效果。"
                    "适合强调空间的某个方向，表达'指向性'。"
                ),
                primary_semantic=MotionSemantic.FLOW,
                secondary_semantic=MotionSemantic.ROTATE,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["neutral", "question"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "diffuse_sector":   CorrespondenceType.LAYER,
                    "shape_arc":        CorrespondenceType.AUGMENT,
                    "chase_circular":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("sector_angle", "扇形角度", 90.0, 20.0, 270.0, "degrees",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("center_angle", "扇形中心角度", 0.0, 0.0, 360.0, "degrees"),
                    MotifSubParam("rotate_speed", "旋转速度", 0.0, -1.0, 1.0, "turns/beat"),
                    MotifSubParam("fade_edges", "边缘渐变", 0.3, 0.0, 0.5, "0-1"),
                ],
                topo_preference="sector",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["directional", "scanning", "focused"],
            ),

            MotifDefinition(
                motif_type=MotifType.SHAPE_SCATTER,
                name="散点激活",
                description=(
                    "随机激活分散的设备，形成散点图案。"
                    "散点的密度和分布可以动态调整。"
                    "适合打击乐段落，表达'活力'或'混乱'的感觉。"
                ),
                primary_semantic=MotionSemantic.SCATTER,
                secondary_semantic=MotionSemantic.PULSE,
                primary_curve=MotionCurve.RANDOM_WALK,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.SNARE],
                preferred_phrase_roles=["neutral", "climax"],
                preferred_sections=["bridge", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.9),
                duration_beats=2.0,
                correspondence_rules={
                    "pulse_random":     CorrespondenceType.UNISON,
                    "wave_breath":      CorrespondenceType.COMPLEMENT,
                    "texture_sparse":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("density", "散点密度（激活比例）", 0.3, 0.05, 0.8, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("randomness", "随机程度", 0.8, 0.0, 1.0, "0-1"),
                    MotifSubParam("update_rate", "图案更新频率", 2.0, 0.5, 8.0, "updates/beat"),
                    MotifSubParam("cluster_tendency", "聚集倾向（0=均匀,1=聚集）", 0.0, 0.0, 1.0, "0-1"),
                ],
                topo_preference="scatter",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["chaotic", "textural", "energetic"],
            ),

            MotifDefinition(
                motif_type=MotifType.SHAPE_GRID,
                name="网格激活",
                description=(
                    "激活规则网格点上的设备，形成网格图案。"
                    "网格可以有不同的间距和角度。"
                    "适合电子音乐段落，表达'规律'或'精确'的感觉。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.FREEZE,
                primary_curve=MotionCurve.STEP,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.HIHAT],
                preferred_phrase_roles=["neutral"],
                preferred_sections=["verse", "intro"],
                complexity_requirement=0.3,
                energy_range=(0.2, 0.7),
                duration_beats=4.0,
                correspondence_rules={
                    "shape_scatter":    CorrespondenceType.ANTIPHON,
                    "texture_interlock":CorrespondenceType.LAYER,
                    "rhythm_polyrhythm":CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("grid_spacing", "网格间距", 0.2, 0.05, 0.5, "0-1"),
                    MotifSubParam("grid_angle", "网格角度", 0.0, 0.0, 90.0, "degrees"),
                    MotifSubParam("phase_x", "X方向相位", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("phase_y", "Y方向相位", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("animate_phase", "相位动画速度", 0.0, -1.0, 1.0, "cycles/beat"),
                ],
                topo_preference="grid",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["mechanical", "precise", "electronic"],
            ),

            MotifDefinition(
                motif_type=MotifType.SHAPE_STAR,
                name="星形激活",
                description=(
                    "激活星形图案上的设备，形成放射状的星形。"
                    "星形的臂数和角度可以调整。"
                    "适合高潮时刻，表达'闪耀'或'爆发'的意象。"
                ),
                primary_semantic=MotionSemantic.DIVERGE,
                secondary_semantic=MotionSemantic.PULSE,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["climax"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.5,
                energy_range=(0.6, 1.0),
                duration_beats=2.0,
                correspondence_rules={
                    "diffuse_radial":   CorrespondenceType.LAYER,
                    "shape_ring":       CorrespondenceType.COMPLEMENT,
                    "emotion_burst":    CorrespondenceType.UNISON,
                },
                sub_params=[
                    MotifSubParam("arm_count", "星形臂数", 5.0, 3.0, 12.0, "count"),
                    MotifSubParam("arm_length", "臂长", 0.8, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("arm_width", "臂宽", 0.1, 0.03, 0.3, "0-1"),
                    MotifSubParam("rotation", "旋转角度", 0.0, 0.0, 360.0, "degrees"),
                    MotifSubParam("rotate_speed", "旋转速度", 0.0, -1.0, 1.0, "turns/beat"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["radiant", "triumphant", "spectacular"],
            ),

            # ════════════════════════════════════════════════════════════════
            #  对话类（7个）— 新增方向
            # ════════════════════════════════════════════════════════════════

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_CALL,
                name="呼唤",
                description=(
                    "一侧设备主动激活，发出'呼唤'信号，另一侧保持静止等待。"
                    "是对话结构的'问'，表达主动、期待、邀请的情绪。"
                    "通常与DIALOGUE_RESPONSE配对，形成完整的问答。"
                ),
                primary_semantic=MotionSemantic.CALL,
                secondary_semantic=MotionSemantic.ASCEND,
                primary_curve=MotionCurve.EASE_IN,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.VOCAL],
                preferred_phrase_roles=["question"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "dialogue_response":CorrespondenceType.DIALOGUE,
                    "dialogue_echo_delay": CorrespondenceType.ECHO,
                    "wave_single":      CorrespondenceType.LAYER,
                    "cross_single":     CorrespondenceType.UNISON,
                    "diffuse_sector":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("call_side", "呼唤方向（0=左,0.5=中,1=右）", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("intensity", "呼唤强度", 0.8, 0.3, 1.0, "0-1",
                                  audio_mappable=True, audio_source="rms"),
                    MotifSubParam("duration", "呼唤持续时间", 2.0, 0.5, 4.0, "beats"),
                    MotifSubParam("gesture_type", "呼唤姿态（0=上升,1=展开,2=脉冲）", 0.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("echo_self", "自身回声（呼唤后的余韵）", 0.3, 0.0, 1.0, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["inviting", "questioning", "expectant"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_RESPONSE,
                name="回应",
                description=(
                    "另一侧设备接力激活，对'呼唤'做出回应。"
                    "是对话结构的'答'，表达被动、回应、接受的情绪。"
                    "回应的强度和方式可以与呼唤形成对比或呼应。"
                ),
                primary_semantic=MotionSemantic.RESPOND,
                secondary_semantic=MotionSemantic.DESCEND,
                primary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.VOCAL],
                preferred_phrase_roles=["answer"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "dialogue_call":    CorrespondenceType.DIALOGUE,
                    "wave_reverse":     CorrespondenceType.LAYER,
                    "cross_mirror":     CorrespondenceType.UNISON,
                    "diffuse_edge":     CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("response_side", "回应方向（与呼唤相反）", 1.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("intensity", "回应强度（相对呼唤）", 0.7, 0.2, 1.2, "ratio"),
                    MotifSubParam("delay", "回应延迟（相对呼唤结束）", 0.0, 0.0, 1.0, "beats"),
                    MotifSubParam("gesture_type", "回应姿态（0=下降,1=收拢,2=脉冲）", 0.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("mirror_call", "镜像呼唤动作（1=完全镜像）", 0.5, 0.0, 1.0, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["responsive", "answering", "accepting"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_ECHO_DELAY,
                name="回声延迟",
                description=(
                    "主动作发出后，另一组以较小幅度延迟跟随，形成空间回声效果。"
                    "延迟时间可以是半拍、一拍或两拍，形成不同的空间深度感。"
                    "适合空间感强的段落，表达'远处的回声'。"
                ),
                primary_semantic=MotionSemantic.RESPOND,
                secondary_semantic=MotionSemantic.RIPPLE,
                primary_curve=MotionCurve.EASE_OUT,
                instrument_affinities=[InstrumentAffinity.AMBIENT, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["answer", "neutral"],
                preferred_sections=["bridge", "outro", "verse"],
                complexity_requirement=0.4,
                energy_range=(0.2, 0.7),
                duration_beats=4.0,
                correspondence_rules={
                    "dialogue_call":    CorrespondenceType.ECHO,
                    "pulse_decay":      CorrespondenceType.LAYER,
                    "wave_single":      CorrespondenceType.ECHO,
                },
                sub_params=[
                    MotifSubParam("delay_beats", "回声延迟时间", 1.0, 0.25, 4.0, "beats"),
                    MotifSubParam("amplitude_ratio", "回声幅度（相对原始）", 0.5, 0.1, 0.9, "ratio"),
                    MotifSubParam("echo_count", "回声次数", 2.0, 1.0, 4.0, "count"),
                    MotifSubParam("decay_per_echo", "每次回声的衰减", 0.4, 0.1, 0.8, "ratio"),
                    MotifSubParam("spatial_spread", "每次回声的空间扩散", 0.2, 0.0, 0.5, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["spatial", "resonant", "distant"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_ALTERNATE,
                name="问答交替",
                description=(
                    "两组设备交替主导，形成持续的问答对话结构。"
                    "每次交替时，前一组渐出，后一组渐入，形成流畅的接力。"
                    "适合整个乐句，表达两个'声音'的持续对话。"
                ),
                primary_semantic=MotionSemantic.CALL,
                secondary_semantic=MotionSemantic.RESPOND,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.BASS_LINE],
                preferred_phrase_roles=["question", "answer"],
                preferred_sections=["verse", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.3, 0.8),
                duration_beats=8.0,
                correspondence_rules={
                    "chase_bidirectional": CorrespondenceType.LAYER,
                    "texture_interlock":CorrespondenceType.LAYER,
                    "wave_single":      CorrespondenceType.SHADOW,
                },
                sub_params=[
                    MotifSubParam("period", "交替周期", 2.0, 0.5, 4.0, "beats"),
                    MotifSubParam("overlap", "交叠时间（渐入渐出重叠）", 0.3, 0.0, 0.8, "beats"),
                    MotifSubParam("intensity_a", "A组强度", 0.8, 0.2, 1.0, "0-1"),
                    MotifSubParam("intensity_b", "B组强度", 0.6, 0.2, 1.0, "0-1"),
                    MotifSubParam("asymmetry", "不对称度（A比B持续更长）", 0.0, -0.5, 0.5, "0-1"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["dialogue", "alternating", "conversational"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_HANDOFF,
                name="接力传递",
                description=(
                    "动作从一组设备传递到另一组，形成接力效果。"
                    "传递点是两组的交接处，可以有短暂的重叠。"
                    "适合表达'传递'或'继承'的叙事意象。"
                ),
                primary_semantic=MotionSemantic.CHASE,
                secondary_semantic=MotionSemantic.HANDOFF,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.MELODY, InstrumentAffinity.PIANO],
                preferred_phrase_roles=["neutral", "transition"],
                preferred_sections=["bridge", "verse"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "chase_linear":     CorrespondenceType.LAYER,
                    "texture_cascade_up": CorrespondenceType.HANDOFF,
                    "wave_single":      CorrespondenceType.HANDOFF,
                },
                sub_params=[
                    MotifSubParam("handoff_point", "交接位置（0=起点,1=终点）", 0.5, 0.0, 1.0, "0-1"),
                    MotifSubParam("overlap_time", "交接重叠时间", 0.3, 0.0, 1.0, "beats"),
                    MotifSubParam("speed", "传递速度", 1.0, 0.3, 3.0, "beats/transfer"),
                    MotifSubParam("direction", "传递方向（0=正,1=反）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["transitional", "flowing", "connected"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_MIRROR,
                name="镜像对话",
                description=(
                    "两组设备做完全镜像的动作，形成对称的对话效果。"
                    "镜像轴可以是水平、垂直或任意角度。"
                    "适合强调对称美感的段落，表达'和谐'或'平衡'。"
                ),
                primary_semantic=MotionSemantic.CALL,
                secondary_semantic=MotionSemantic.RESPOND,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["answer", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.3, 0.9),
                duration_beats=4.0,
                correspondence_rules={
                    "cross_mirror":     CorrespondenceType.UNISON,
                    "wave_standing":    CorrespondenceType.LAYER,
                    "diffuse_center":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("mirror_axis", "镜像轴角度", 0.0, 0.0, 180.0, "degrees"),
                    MotifSubParam("phase_offset", "镜像相位差（0=同步,0.5=反相）", 0.0, 0.0, 0.5, "0-1"),
                    MotifSubParam("amplitude_ratio", "镜像幅度比（1=完全对称）", 1.0, 0.5, 1.5, "ratio"),
                    MotifSubParam("base_motion", "基础动作类型（0=波,1=脉冲,2=扩散）", 0.0, 0.0, 2.0, "0-2"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["symmetric", "harmonious", "balanced"],
            ),

            MotifDefinition(
                motif_type=MotifType.DIALOGUE_POLYPHONY,
                name="多声部对话",
                description=(
                    "三组或以上设备交织对话，形成复调结构。"
                    "每组有独立的节奏和动作，但在音乐语义上相互呼应。"
                    "是最复杂的对话形式，适合高能量的多声部音乐段落。"
                ),
                primary_semantic=MotionSemantic.WEAVE,
                secondary_semantic=MotionSemantic.CALL,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.STRINGS, InstrumentAffinity.HARMONY],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.8,
                energy_range=(0.6, 1.0),
                duration_beats=8.0,
                correspondence_rules={
                    "wave_travel":      CorrespondenceType.LAYER,
                    "chase_circular":   CorrespondenceType.AUGMENT,
                    "diffuse_center":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("voice_count",    "声部数量",             3.0,  2.0,  6.0,  "count",
                                  audio_source="rms"),
                    MotifSubParam("phase_spread",   "声部相位分布",         0.33, 0.1,  1.0,  "0-1"),
                    MotifSubParam("independence",   "声部独立性（0=齐奏,1=完全独立）", 0.7, 0.0, 1.0, "0-1"),
                    MotifSubParam("convergence",    "汇聚时刻（0=无,1=结尾汇聚）",    0.5, 0.0, 1.0, "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["complex", "rich", "polyphonic"],
            ),

            # ─── TEXTURE 织体密度类 ─────────────────────────────────────────────
            MotifDefinition(
                motif_type=MotifType.TEXTURE_SPARSE,
                name="稀疏编织",
                description=(
                    "少量设备交替激活，有大量空隙和留白。"
                    "空隙本身是动作的一部分，制造'等待'和'呼吸'感。"
                    "适合安静的verse段落或过渡段，强调空间感和期待感。"
                ),
                primary_semantic=MotionSemantic.BREATHE,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.AMBIENT, InstrumentAffinity.PIANO],
                preferred_phrase_roles=["question", "neutral"],
                preferred_sections=["intro", "verse", "outro"],
                complexity_requirement=0.1,
                energy_range=(0.0, 0.4),
                duration_beats=4.0,
                correspondence_rules={
                    "texture_dense":    CorrespondenceType.COMPLEMENT,
                    "emotion_tension":  CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("density",        "激活密度（0=极稀疏）",  0.2,  0.05, 0.4,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("gap_duration",   "空隙时长",              1.5,  0.5,  4.0,  "beats"),
                    MotifSubParam("position_mode",  "位置模式（0=随机,1=顺序）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("fade_time",      "渐入渐出时间",          0.3,  0.0,  1.0,  "beats"),
                ],
                topo_preference="scatter",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["sparse", "breathing", "anticipation"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_DENSE,
                name="密集编织",
                description=(
                    "设备快速交替激活，几乎无间隙，形成密集的织体。"
                    "高频率的切换制造'沸腾'或'涌动'的视觉效果。"
                    "适合高能量的chorus或drop段落，强调密度和能量感。"
                ),
                primary_semantic=MotionSemantic.WEAVE,
                secondary_semantic=MotionSemantic.BURST,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.HIHAT, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.7,
                energy_range=(0.7, 1.0),
                duration_beats=2.0,
                correspondence_rules={
                    "texture_sparse":   CorrespondenceType.COMPLEMENT,
                    "pulse_beat":       CorrespondenceType.LAYER,
                    "chase_circular":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("density",        "激活密度（1=极密集）",  0.85, 0.6,  1.0,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("switch_rate",    "切换速率",              4.0,  2.0,  16.0, "Hz",
                                  audio_source="bpm"),
                    MotifSubParam("pattern_type",   "图案类型（0=随机,1=交错,2=波浪）", 1.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("intensity",      "强度",                  1.0,  0.5,  1.0,  "0-1",
                                  audio_source="rms"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["dense", "energetic", "boiling"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_CASCADE_UP,
                name="瀑布积累",
                description=(
                    "从1个设备开始，每拍增加一个，直到全部激活。"
                    "视觉上像水位上涨，制造强烈的期待和积累感。"
                    "适合build-up段落，是表现渐强最直观的动作元。"
                ),
                primary_semantic=MotionSemantic.EXPAND,
                secondary_semantic=MotionSemantic.BURST,
                primary_curve=MotionCurve.EXPONENTIAL,
                instrument_affinities=[InstrumentAffinity.BASS_LINE, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["buildup", "question"],
                preferred_sections=["pre_chorus", "build"],
                complexity_requirement=0.3,
                energy_range=(0.2, 0.9),
                duration_beats=8.0,
                correspondence_rules={
                    "texture_cascade_dn":  CorrespondenceType.ANTIPHON,
                    "emotion_burst":       CorrespondenceType.HANDOFF,
                    "diffuse_center":      CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("add_rate",       "每次增加数量",          1.0,  1.0,  4.0,  "count"),
                    MotifSubParam("add_interval",   "增加间隔",              1.0,  0.25, 4.0,  "beats",
                                  audio_source="bpm"),
                    MotifSubParam("order",          "激活顺序（0=从内,1=从外,2=随机）", 0.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("hold_previous",  "保持之前激活的设备",    1.0,  0.0,  1.0,  "binary"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["buildup", "accumulation", "anticipation"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_CASCADE_DN,
                name="瀑布释放",
                description=(
                    "从全部激活开始，每拍减少一个，直到只剩最后一个。"
                    "视觉上像水位下降，制造强烈的消散和释放感。"
                    "适合高潮后的释放段落，与瀑布积累形成完美的呼应。"
                ),
                primary_semantic=MotionSemantic.CONTRACT,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.LOGARITHMIC,
                instrument_affinities=[InstrumentAffinity.STRINGS, InstrumentAffinity.AMBIENT],
                preferred_phrase_roles=["answer", "release"],
                preferred_sections=["outro", "bridge"],
                complexity_requirement=0.3,
                energy_range=(0.1, 0.8),
                duration_beats=8.0,
                correspondence_rules={
                    "texture_cascade_up":  CorrespondenceType.ANTIPHON,
                    "emotion_release":     CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("remove_rate",    "每次减少数量",          1.0,  1.0,  4.0,  "count"),
                    MotifSubParam("remove_interval","减少间隔",              1.0,  0.25, 4.0,  "beats",
                                  audio_source="bpm"),
                    MotifSubParam("order",          "关闭顺序（0=从外,1=从内,2=随机）", 0.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("final_hold",     "最后保持时长",          2.0,  0.0,  8.0,  "beats"),
                ],
                topo_preference="arc",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["release", "dissipation", "ending"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_INTERLOCK,
                name="交错填空",
                description=(
                    "A组设备激活时B组静止，A组静止时B组激活，形成完美的互补织体。"
                    "两组设备在时间上交错，在空间上互补，制造'呼吸'的空间感。"
                    "适合中等能量段落，强调空间的有序利用和节奏的精确感。"
                ),
                primary_semantic=MotionSemantic.WEAVE,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.STEP,
                instrument_affinities=[InstrumentAffinity.SNARE, InstrumentAffinity.HIHAT],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.7),
                duration_beats=4.0,
                correspondence_rules={
                    "texture_dense":    CorrespondenceType.COMPLEMENT,
                    "wave_standing":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("group_ratio",    "A/B组比例（0.5=各半）", 0.5,  0.2,  0.8,  "0-1"),
                    MotifSubParam("switch_beat",    "切换节拍",              2.0,  0.5,  4.0,  "beats",
                                  audio_source="bpm"),
                    MotifSubParam("overlap",        "重叠时间（0=无重叠）",  0.0,  0.0,  0.5,  "beats"),
                    MotifSubParam("spatial_split",  "空间分割方式（0=左右,1=内外,2=随机）", 0.0, 0.0, 2.0, "0-2"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["interlocking", "rhythmic", "precise"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_LAYER,
                name="层叠织体",
                description=(
                    "多层动作同时运行，每层有不同的节奏、幅度和空间位置。"
                    "层与层之间相互独立但在音乐语义上相互呼应，形成丰富的织体。"
                    "是复杂度最高的织体动作，适合高潮段落的多声部表现。"
                ),
                primary_semantic=MotionSemantic.WEAVE,
                secondary_semantic=MotionSemantic.EXPAND,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.8,
                energy_range=(0.5, 1.0),
                duration_beats=8.0,
                correspondence_rules={
                    "dialogue_polyphony": CorrespondenceType.LAYER,
                    "texture_dense":      CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("layer_count",    "层数",                  3.0,  2.0,  5.0,  "count"),
                    MotifSubParam("layer_offset",   "层间相位偏移",          0.25, 0.0,  0.5,  "beats"),
                    MotifSubParam("layer_amplitude","层间幅度衰减",          0.8,  0.5,  1.0,  "ratio"),
                    MotifSubParam("convergence",    "汇聚时刻",              0.0,  0.0,  1.0,  "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["layered", "rich", "complex"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_UNRAVEL,
                name="抽丝解织",
                description=(
                    "从密集的织体开始，逐渐抽离元素，直到只剩最核心的线条。"
                    "像一首曲子的尾声，层层剥落，回归本质。"
                    "适合outro或段落结尾，制造'消散'和'沉淀'的感觉。"
                ),
                primary_semantic=MotionSemantic.CONTRACT,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.LOGARITHMIC,
                instrument_affinities=[InstrumentAffinity.AMBIENT, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["answer", "release"],
                preferred_sections=["outro", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.1, 0.6),
                duration_beats=8.0,
                correspondence_rules={
                    "texture_weave_in":   CorrespondenceType.ANTIPHON,
                    "emotion_release":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("unravel_rate",   "抽离速率",              0.5,  0.1,  2.0,  "layers/beat"),
                    MotifSubParam("core_density",   "核心保留密度",          0.1,  0.05, 0.3,  "0-1"),
                    MotifSubParam("fade_mode",      "消退方式（0=突然,1=渐变）", 1.0, 0.0, 1.0, "binary"),
                    MotifSubParam("spatial_order",  "空间消退顺序（0=从外,1=随机）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["dissolving", "fading", "ending"],
            ),
            MotifDefinition(
                motif_type=MotifType.TEXTURE_WEAVE_IN,
                name="织入新元素",
                description=(
                    "新的动作元素逐渐加入现有织体，不打断原有节奏，而是融入其中。"
                    "像乐队中新乐器的加入，先以弱音出现，再逐渐增强到完整音量。"
                    "适合段落转换时的平滑过渡，避免突兀的切换感。"
                ),
                primary_semantic=MotionSemantic.EXPAND,
                secondary_semantic=MotionSemantic.WEAVE,
                primary_curve=MotionCurve.EASE_IN,
                instrument_affinities=[InstrumentAffinity.SYNTH_PAD, InstrumentAffinity.STRINGS],
                preferred_phrase_roles=["transition", "buildup"],
                preferred_sections=["pre_chorus", "bridge"],
                complexity_requirement=0.4,
                energy_range=(0.3, 0.8),
                duration_beats=4.0,
                correspondence_rules={
                    "texture_unravel":    CorrespondenceType.ANTIPHON,
                    "texture_layer":      CorrespondenceType.HANDOFF,
                },
                sub_params=[
                    MotifSubParam("entry_volume",   "进入音量（0=无声进入）", 0.0, 0.0, 0.5, "0-1"),
                    MotifSubParam("ramp_time",      "渐强时间",              2.0,  0.5,  8.0,  "beats"),
                    MotifSubParam("entry_position", "进入空间位置（0=边缘,1=中心）", 0.0, 0.0, 1.0, "0-1"),
                    MotifSubParam("blend_mode",     "融合方式（0=叠加,1=替换）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["introduction", "blending", "transition"],
            ),

            # ─── RHYTHM 节奏切分类 ──────────────────────────────────────────────
            MotifDefinition(
                motif_type=MotifType.RHYTHM_SYNCOPATE,
                name="切分节奏",
                description=(
                    "在非正拍位置激活设备，制造切分感和律动感。"
                    "切分点通常在正拍前半拍或后半拍，与预期节拍形成张力。"
                    "适合funk/soul风格的音乐，或任何需要'律动感'的段落。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.SNARE, InstrumentAffinity.BASS_LINE],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.4,
                energy_range=(0.4, 0.8),
                duration_beats=2.0,
                correspondence_rules={
                    "pulse_beat":       CorrespondenceType.COMPLEMENT,
                    "rhythm_offbeat":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("sync_offset",    "切分偏移量（拍）",      0.5,  0.25, 0.75, "beats"),
                    MotifSubParam("strength",       "切分强度",              0.8,  0.3,  1.0,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("pattern",        "切分图案（0=前切,1=后切,2=混合）", 0.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("device_count",   "同时激活设备数",        3.0,  1.0,  8.0,  "count"),
                ],
                topo_preference="scatter",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["syncopated", "groovy", "unexpected"],
            ),
            MotifDefinition(
                motif_type=MotifType.RHYTHM_DOTTED,
                name="附点节奏",
                description=(
                    "长-短-长-短的附点节奏型，制造'跳跃'和'弹性'感。"
                    "长音对应大幅度/全开，短音对应小幅度/半开，形成有弹性的节奏感。"
                    "适合进行曲风格或任何需要'行进感'的段落。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.BOUNCE,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.BASS_KICK],
                preferred_phrase_roles=["neutral", "question"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.3,
                energy_range=(0.4, 0.8),
                duration_beats=2.0,
                correspondence_rules={
                    "rhythm_triplet":   CorrespondenceType.COMPLEMENT,
                    "chase_linear":     CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("long_ratio",     "长音比例（0.75=标准附点）", 0.75, 0.5, 0.875, "0-1"),
                    MotifSubParam("long_amplitude", "长音幅度",              1.0,  0.5,  1.0,  "0-1"),
                    MotifSubParam("short_amplitude","短音幅度",              0.5,  0.1,  0.8,  "0-1"),
                    MotifSubParam("tempo_mult",     "速度倍数",              1.0,  0.5,  2.0,  "x"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["dotted", "marching", "bouncy"],
            ),
            MotifDefinition(
                motif_type=MotifType.RHYTHM_TRIPLET,
                name="三连音爆发",
                description=(
                    "在一拍内均匀分布三次激活，制造三连音的节奏感。"
                    "三个激活点在空间上依次移动，形成快速的三点扫描。"
                    "适合jazz/swing风格，或需要'三拍子'感觉的段落。"
                ),
                primary_semantic=MotionSemantic.BURST,
                secondary_semantic=MotionSemantic.CHASE,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.HIHAT, InstrumentAffinity.PIANO],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.5,
                energy_range=(0.5, 1.0),
                duration_beats=1.0,
                correspondence_rules={
                    "rhythm_dotted":    CorrespondenceType.COMPLEMENT,
                    "pulse_cascade":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("triplet_spacing","三连音间距（1/3拍）",   0.33, 0.25, 0.4,  "beats"),
                    MotifSubParam("amplitude_curve","幅度变化（0=平,1=渐强,2=渐弱）", 1.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("spatial_spread", "空间分布范围",          0.5,  0.1,  1.0,  "0-1"),
                    MotifSubParam("device_per_hit", "每次激活设备数",        2.0,  1.0,  5.0,  "count"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["triplet", "jazz", "quick"],
            ),
            MotifDefinition(
                motif_type=MotifType.RHYTHM_OFFBEAT,
                name="反拍强调",
                description=(
                    "强调弱拍（2、4拍），与正拍形成对比，制造'反拍'感。"
                    "反拍激活时幅度更大，正拍激活时幅度更小，颠覆常规节奏预期。"
                    "适合reggae/ska风格，或需要'逆向律动'的段落。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.SNARE, InstrumentAffinity.HIHAT],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.3,
                energy_range=(0.3, 0.7),
                duration_beats=2.0,
                correspondence_rules={
                    "rhythm_syncopate": CorrespondenceType.LAYER,
                    "pulse_accent":     CorrespondenceType.COMPLEMENT,
                },
                sub_params=[
                    MotifSubParam("offbeat_amp",    "反拍幅度",              1.0,  0.6,  1.0,  "0-1"),
                    MotifSubParam("onbeat_amp",     "正拍幅度",              0.3,  0.0,  0.6,  "0-1"),
                    MotifSubParam("device_count",   "同时激活设备数",        4.0,  2.0,  8.0,  "count"),
                    MotifSubParam("spatial_mode",   "空间模式（0=固定,1=移动）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="arc",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["offbeat", "reggae", "reversed"],
            ),
            MotifDefinition(
                motif_type=MotifType.RHYTHM_POLYRHYTHM,
                name="复合节奏",
                description=(
                    "两种不同节奏同时运行，形成复合节奏（如3对2，4对3）。"
                    "两组设备分别跟随不同的节奏，在某些节拍点汇合，其他时候错开。"
                    "是节奏复杂度最高的动作元，适合高能量的多节奏段落。"
                ),
                primary_semantic=MotionSemantic.WEAVE,
                secondary_semantic=MotionSemantic.PULSE,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.PERCUSSION, InstrumentAffinity.BASS_LINE],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "bridge"],
                complexity_requirement=0.7,
                energy_range=(0.5, 1.0),
                duration_beats=6.0,
                correspondence_rules={
                    "texture_interlock": CorrespondenceType.LAYER,
                    "dialogue_polyphony": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("rhythm_a",       "节奏A（每N拍）",        2.0,  1.0,  6.0,  "beats"),
                    MotifSubParam("rhythm_b",       "节奏B（每N拍）",        3.0,  1.0,  6.0,  "beats"),
                    MotifSubParam("group_a_size",   "A组设备数",             4.0,  2.0,  8.0,  "count"),
                    MotifSubParam("group_b_size",   "B组设备数",             3.0,  2.0,  8.0,  "count"),
                    MotifSubParam("convergence_amp","汇合时幅度增强",        1.5,  1.0,  2.0,  "ratio"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["polyrhythm", "complex", "interlocking"],
            ),

            # ─── EMOTION 情绪强度类 ─────────────────────────────────────────────
            MotifDefinition(
                motif_type=MotifType.EMOTION_TENSION,
                name="张力保持",
                description=(
                    "设备缓慢升压，在高位长时间维持，制造强烈的悬念和张力。"
                    "升压过程用指数曲线，保持阶段有微小的颤动，强化紧张感。"
                    "适合高潮前的最后积累，或戏剧性的悬念时刻。"
                ),
                primary_semantic=MotionSemantic.EXPAND,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.EXPONENTIAL,
                instrument_affinities=[InstrumentAffinity.STRINGS, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["buildup", "question"],
                preferred_sections=["pre_chorus", "bridge"],
                complexity_requirement=0.3,
                energy_range=(0.5, 0.9),
                duration_beats=8.0,
                correspondence_rules={
                    "emotion_release":  CorrespondenceType.HANDOFF,
                    "emotion_burst":    CorrespondenceType.HANDOFF,
                    "texture_cascade_up": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("rise_time",      "升压时间",              4.0,  1.0,  16.0, "beats"),
                    MotifSubParam("hold_level",     "保持高度",              0.9,  0.6,  1.0,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("hold_duration",  "保持时长",              4.0,  1.0,  16.0, "beats"),
                    MotifSubParam("tremor_amount",  "颤动幅度（制造紧张感）", 0.05, 0.0,  0.2,  "0-1"),
                    MotifSubParam("device_coverage","覆盖设备比例",          0.7,  0.3,  1.0,  "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["tension", "suspense", "buildup"],
            ),
            MotifDefinition(
                motif_type=MotifType.EMOTION_RELEASE,
                name="释放跌落",
                description=(
                    "从高位快速跌落，配合高潮后的释放感和解脱感。"
                    "跌落曲线用对数衰减，快速下降后有一段缓慢的余韵。"
                    "适合高潮结束后的释放段落，与张力保持形成完美的呼应。"
                ),
                primary_semantic=MotionSemantic.CONTRACT,
                secondary_semantic=MotionSemantic.SCATTER,
                primary_curve=MotionCurve.SHARP_DECAY,
                instrument_affinities=[InstrumentAffinity.STRINGS, InstrumentAffinity.AMBIENT],
                preferred_phrase_roles=["answer", "release"],
                preferred_sections=["outro", "bridge"],
                complexity_requirement=0.2,
                energy_range=(0.0, 0.6),
                duration_beats=4.0,
                correspondence_rules={
                    "emotion_tension":  CorrespondenceType.ANTIPHON,
                    "emotion_burst":    CorrespondenceType.ECHO,
                    "texture_cascade_dn": CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("fall_speed",     "跌落速度",              2.0,  0.5,  8.0,  "beats"),
                    MotifSubParam("fall_curve",     "跌落曲线（0=线性,1=对数,2=指数）", 1.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("tail_duration",  "余韵时长",              2.0,  0.0,  8.0,  "beats"),
                    MotifSubParam("tail_level",     "余韵高度",              0.1,  0.0,  0.3,  "0-1"),
                    MotifSubParam("scatter_mode",   "消散方式（0=整体,1=逐个）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["release", "falling", "resolution"],
            ),
            MotifDefinition(
                motif_type=MotifType.EMOTION_SHIMMER,
                name="微颤闪烁",
                description=(
                    "设备在高位做微小快速的抖动，表现极度紧张、兴奋或喜悦。"
                    "抖动频率随音频能量变化，能量越高抖动越快。"
                    "适合高潮时刻的持续状态，或表现极度兴奋的情绪。"
                ),
                primary_semantic=MotionSemantic.PULSE,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.SINE,
                instrument_affinities=[InstrumentAffinity.HIHAT, InstrumentAffinity.VOCAL],
                preferred_phrase_roles=["climax", "neutral"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.4,
                energy_range=(0.6, 1.0),
                duration_beats=4.0,
                correspondence_rules={
                    "emotion_tension":  CorrespondenceType.LAYER,
                    "texture_dense":    CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("base_level",     "基础高度",              0.8,  0.5,  1.0,  "0-1"),
                    MotifSubParam("shimmer_depth",  "抖动深度",              0.1,  0.02, 0.3,  "0-1"),
                    MotifSubParam("shimmer_rate",   "抖动频率",              8.0,  4.0,  20.0, "Hz",
                                  audio_source="rms"),
                    MotifSubParam("randomness",     "随机性（0=规律,1=随机）", 0.3, 0.0,  1.0,  "0-1"),
                    MotifSubParam("device_coverage","覆盖设备比例",          0.9,  0.5,  1.0,  "0-1"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["shimmer", "excited", "vibrating"],
            ),
            MotifDefinition(
                motif_type=MotifType.EMOTION_BURST,
                name="爆发冲击",
                description=(
                    "瞬间全开所有设备，制造强烈的视觉冲击感。"
                    "持续时间极短（0.1-0.5拍），之后快速衰减。"
                    "适合音乐中最强的重音点、drop点或高潮顶点。"
                ),
                primary_semantic=MotionSemantic.BURST,
                secondary_semantic=MotionSemantic.EXPAND,
                primary_curve=MotionCurve.SHARP_ATTACK,
                instrument_affinities=[InstrumentAffinity.BASS_KICK, InstrumentAffinity.PERCUSSION],
                preferred_phrase_roles=["climax", "accent"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.1,
                energy_range=(0.8, 1.0),
                duration_beats=0.5,
                correspondence_rules={
                    "emotion_tension":  CorrespondenceType.ECHO,
                    "texture_cascade_up": CorrespondenceType.HANDOFF,
                    "diffuse_center":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("burst_duration", "爆发持续时间",          0.25, 0.1,  1.0,  "beats"),
                    MotifSubParam("decay_time",     "衰减时间",              0.5,  0.1,  2.0,  "beats"),
                    MotifSubParam("peak_level",     "峰值高度",              1.0,  0.7,  1.0,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("coverage",       "覆盖比例",              1.0,  0.5,  1.0,  "0-1"),
                    MotifSubParam("aftershock",     "余震次数",              0.0,  0.0,  3.0,  "count"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["burst", "impact", "climax"],
            ),
            MotifDefinition(
                motif_type=MotifType.EMOTION_SWELL,
                name="渐强积累",
                description=(
                    "从极低的能量缓慢积累，用很长的时间渐强到高潮。"
                    "积累过程中设备数量和幅度同时增加，形成双重渐强。"
                    "适合长时间的build-up段落，是最宏大的积累动作元。"
                ),
                primary_semantic=MotionSemantic.EXPAND,
                secondary_semantic=MotionSemantic.BURST,
                primary_curve=MotionCurve.EXPONENTIAL,
                instrument_affinities=[InstrumentAffinity.STRINGS, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["buildup", "question"],
                preferred_sections=["pre_chorus", "build"],
                complexity_requirement=0.4,
                energy_range=(0.1, 1.0),
                duration_beats=16.0,
                correspondence_rules={
                    "emotion_burst":    CorrespondenceType.HANDOFF,
                    "texture_cascade_up": CorrespondenceType.LAYER,
                    "emotion_tension":  CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("swell_duration", "渐强总时长",            16.0, 4.0,  64.0, "beats"),
                    MotifSubParam("start_density",  "起始设备密度",          0.05, 0.0,  0.2,  "0-1"),
                    MotifSubParam("end_density",    "结束设备密度",          1.0,  0.7,  1.0,  "0-1"),
                    MotifSubParam("curve_shape",    "积累曲线形状（0=线性,1=指数,2=S曲线）", 1.0, 0.0, 2.0, "0-2"),
                    MotifSubParam("spatial_order",  "空间扩展顺序（0=从中心,1=从边缘）", 0.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="ring",
                can_be_primary=True,
                can_be_decoration=False,
                emotion_tags=["swell", "crescendo", "epic"],
            ),

            # ─── HOLD 保持状态类 ────────────────────────────────────────────────
            MotifDefinition(
                motif_type=MotifType.HOLD_ALL,
                name="全开保持",
                description=(
                    "所有设备保持全开状态，制造'凝固'的瞬间。"
                    "通常用于高潮顶点的短暂定格，或段落结束的收束。"
                    "持续时间通常很短，之后接其他动作元。"
                ),
                primary_semantic=MotionSemantic.EXPAND,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.STEP,
                instrument_affinities=[InstrumentAffinity.ALL],
                preferred_phrase_roles=["climax", "accent"],
                preferred_sections=["chorus", "drop"],
                complexity_requirement=0.0,
                energy_range=(0.7, 1.0),
                duration_beats=1.0,
                correspondence_rules={
                    "hold_none":        CorrespondenceType.ANTIPHON,
                    "emotion_burst":    CorrespondenceType.ECHO,
                },
                sub_params=[
                    MotifSubParam("hold_level",     "保持高度",              1.0,  0.5,  1.0,  "0-1"),
                    MotifSubParam("hold_duration",  "保持时长",              1.0,  0.25, 8.0,  "beats"),
                    MotifSubParam("entry_mode",     "进入方式（0=瞬间,1=渐入）", 0.0, 0.0, 1.0, "binary"),
                    MotifSubParam("exit_mode",      "退出方式（0=瞬间,1=渐出）", 1.0, 0.0, 1.0, "binary"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["frozen", "peak", "full"],
            ),
            MotifDefinition(
                motif_type=MotifType.HOLD_NONE,
                name="全关静默",
                description=(
                    "所有设备关闭，制造完全的静默和空白。"
                    "静默本身是一种强烈的表达，在高能量段落后的突然静默效果极强。"
                    "通常用于音乐中的'空拍'或段落之间的戏剧性停顿。"
                ),
                primary_semantic=MotionSemantic.CONTRACT,
                secondary_semantic=MotionSemantic.BREATHE,
                primary_curve=MotionCurve.STEP,
                instrument_affinities=[InstrumentAffinity.ALL],
                preferred_phrase_roles=["transition", "accent"],
                preferred_sections=["bridge", "intro"],
                complexity_requirement=0.0,
                energy_range=(0.0, 0.3),
                duration_beats=1.0,
                correspondence_rules={
                    "hold_all":         CorrespondenceType.ANTIPHON,
                    "texture_sparse":   CorrespondenceType.HANDOFF,
                },
                sub_params=[
                    MotifSubParam("silence_duration","静默时长",             1.0,  0.25, 8.0,  "beats"),
                    MotifSubParam("fade_out_time",  "渐出时间（0=瞬间）",    0.0,  0.0,  2.0,  "beats"),
                    MotifSubParam("fade_in_time",   "恢复渐入时间",          0.5,  0.0,  4.0,  "beats"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["silence", "pause", "dramatic"],
            ),
            MotifDefinition(
                motif_type=MotifType.HOLD_PATTERN,
                name="图案保持",
                description=(
                    "保持一个特定的空间图案不变，作为其他动作的稳定背景。"
                    "图案可以是弧线、环形、星形等任意形状，在保持期间不变化。"
                    "适合作为编舞的'锚点'，在复杂动作中提供视觉稳定性。"
                ),
                primary_semantic=MotionSemantic.BREATHE,
                secondary_semantic=MotionSemantic.EXPAND,
                primary_curve=MotionCurve.EASE_IN_OUT,
                instrument_affinities=[InstrumentAffinity.HARMONY, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["neutral", "answer"],
                preferred_sections=["verse", "chorus"],
                complexity_requirement=0.1,
                energy_range=(0.2, 0.7),
                duration_beats=8.0,
                correspondence_rules={
                    "texture_dense":    CorrespondenceType.LAYER,
                    "chase_circular":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("pattern_type",   "图案类型（0=弧,1=环,2=星,3=扇）", 1.0, 0.0, 3.0, "0-3"),
                    MotifSubParam("pattern_level",  "图案亮度/高度",         0.7,  0.3,  1.0,  "0-1"),
                    MotifSubParam("hold_duration",  "保持时长",              8.0,  2.0,  32.0, "beats"),
                    MotifSubParam("pulse_overlay",  "叠加脉冲（0=无,1=有）", 0.0,  0.0,  1.0,  "binary"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["stable", "anchored", "background"],
            ),
            MotifDefinition(
                motif_type=MotifType.HOLD_FADE,
                name="渐变保持",
                description=(
                    "在保持状态下缓慢渐入或渐出，制造'呼吸'般的持续变化。"
                    "渐变速度可以跟随音乐的情绪弧线，能量高时渐强，能量低时渐弱。"
                    "适合长时间的背景层，提供持续的视觉支撑。"
                ),
                primary_semantic=MotionSemantic.BREATHE,
                secondary_semantic=MotionSemantic.CONTRACT,
                primary_curve=MotionCurve.SINE,
                instrument_affinities=[InstrumentAffinity.AMBIENT, InstrumentAffinity.SYNTH_PAD],
                preferred_phrase_roles=["neutral", "transition"],
                preferred_sections=["intro", "verse", "outro"],
                complexity_requirement=0.1,
                energy_range=(0.1, 0.6),
                duration_beats=16.0,
                correspondence_rules={
                    "wave_breath":      CorrespondenceType.LAYER,
                    "texture_sparse":   CorrespondenceType.LAYER,
                },
                sub_params=[
                    MotifSubParam("fade_period",    "渐变周期",              8.0,  2.0,  32.0, "beats"),
                    MotifSubParam("min_level",      "最低亮度/高度",         0.1,  0.0,  0.4,  "0-1"),
                    MotifSubParam("max_level",      "最高亮度/高度",         0.8,  0.5,  1.0,  "0-1",
                                  audio_source="rms"),
                    MotifSubParam("device_coverage","覆盖设备比例",          0.6,  0.2,  1.0,  "0-1"),
                    MotifSubParam("energy_track",   "跟随能量变化",          1.0,  0.0,  1.0,  "binary"),
                ],
                topo_preference="ring",
                can_be_primary=False,
                can_be_decoration=True,
                emotion_tags=["breathing", "ambient", "gentle"],
            ),
        ]

        # 构建快速查找字典
        self._vocab: Dict[MotifType, MotifDefinition] = {
            m.motif_type: m for m in defs
        }
        return self._vocab

    # ═══════════════════════════════════════════════════════════════════════
    #  公共接口
    # ═══════════════════════════════════════════════════════════════════════

    def get(self, motif_type: MotifType) -> Optional[MotifDefinition]:
        """获取指定类型的动作元定义"""
        return self._vocab.get(motif_type)

    def get_all(self) -> List[MotifDefinition]:
        """获取所有动作元定义"""
        return list(self._vocab.values())

    def get_by_category(self, prefix: str) -> List[MotifDefinition]:
        """按类别前缀获取动作元（如 'wave_', 'cross_', 'dialogue_'）"""
        return [m for t, m in self._vocab.items() if t.value.startswith(prefix)]

    def get_compatible(self, motif_type: MotifType,
                        correspondence_type: Optional[CorrespondenceType] = None
                        ) -> List[MotifDefinition]:
        """获取与指定动作元兼容的动作元列表"""
        source = self._vocab.get(motif_type)
        if not source:
            return []
        result = []
        for t, m in self._vocab.items():
            if t == motif_type:
                continue
            # 检查source的呼应规则中是否包含目标
            if t.value in source.correspondence_rules:
                if correspondence_type is None or                    source.correspondence_rules[t.value] == correspondence_type:
                    result.append(m)
        return result

    def select_for_context(self,
                           section: str,
                           phrase_role: str,
                           energy: float,
                           complexity: float,
                           instrument_hints: Optional[List[str]] = None
                           ) -> List[MotifDefinition]:
        """
        根据音乐上下文选择合适的动作元列表（按适合度排序）。

        Parameters
        ----------
        section       : 段落类型（intro/verse/chorus/bridge/outro等）
        phrase_role   : 乐句角色（question/answer/buildup/climax/neutral等）
        energy        : 当前能量（0-1）
        complexity    : 编舞复杂度参数（0-1）
        instrument_hints : 当前活跃乐器列表
        """
        scored: List[tuple] = []
        for motif in self._vocab.values():
            score = 0.0

            # 段落匹配
            if section in motif.preferred_sections:
                score += 2.0
            elif "all" in motif.preferred_sections:
                score += 0.5

            # 乐句角色匹配
            if phrase_role in motif.preferred_phrase_roles:
                score += 2.0
            elif "neutral" in motif.preferred_phrase_roles:
                score += 0.5

            # 能量范围匹配
            e_min, e_max = motif.energy_range
            if e_min <= energy <= e_max:
                score += 3.0
            elif abs(energy - (e_min + e_max) / 2) < 0.2:
                score += 1.0

            # 复杂度匹配
            if complexity >= motif.complexity_requirement:
                score += 1.0
            else:
                score -= 2.0  # 复杂度不够时降权

            # 乐器亲和力匹配
            if instrument_hints:
                for hint in instrument_hints:
                    for affinity in motif.instrument_affinities:
                        if affinity.value in hint.lower() or hint.lower() in affinity.value:
                            score += 1.5
                            break

            if score > 0:
                scored.append((score, motif))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    def select_pair(self,
                    primary: MotifDefinition,
                    correspondence: CorrespondenceType,
                    energy: float,
                    complexity: float
                    ) -> Optional[MotifDefinition]:
        """
        为主动作元选择一个呼应配对的次动作元。

        Parameters
        ----------
        primary       : 主动作元
        correspondence: 期望的呼应类型
        energy        : 当前能量
        complexity    : 复杂度
        """
        candidates = self.get_compatible(primary.motif_type, correspondence)
        if not candidates:
            # 放宽条件，不限制呼应类型
            candidates = self.get_compatible(primary.motif_type)
        if not candidates:
            return None

        # 按能量范围过滤
        valid = [c for c in candidates
                 if c.energy_range[0] <= energy <= c.energy_range[1]
                 and complexity >= c.complexity_requirement]
        return valid[0] if valid else (candidates[0] if candidates else None)

    def compute_motif_value(self,
                            motif_type: MotifType,
                            phase: float,
                            audio_frame: Optional[dict] = None,
                            variation_seed: float = 0.0
                            ) -> float:
        """
        计算动作元在给定相位下的归一化输出值（0-1）。

        Parameters
        ----------
        motif_type    : 动作元类型
        phase         : 当前相位（0-1，一个完整周期）
        audio_frame   : 音频帧数据（可选，用于音频调制）
        variation_seed: 变奏种子（0-1，用于随机变奏）
        """
        import math
        motif = self._vocab.get(motif_type)
        if not motif:
            return 0.0

        curve = motif.primary_curve
        base = self._apply_curve(phase, curve)

        # 音频调制
        if audio_frame:
            rms = audio_frame.get("rms", 0.5)
            beat_phase = audio_frame.get("beat_phase", 0.0)

            # 根据动作元类型做特定的音频调制
            if motif_type in (MotifType.PULSE_BEAT, MotifType.EMOTION_BURST):
                # 节拍脉冲：在节拍点增强
                beat_boost = max(0.0, 1.0 - abs(beat_phase - 0.0) * 4)
                base = base * (0.7 + 0.3 * beat_boost)
            elif motif_type in (MotifType.WAVE_BREATH, MotifType.HOLD_FADE):
                # 呼吸波：跟随RMS
                base = base * (0.5 + 0.5 * rms)
            elif motif_type in (MotifType.EMOTION_SHIMMER,):
                # 微颤：高频调制
                shimmer = math.sin(phase * 2 * math.pi * 8) * 0.1
                base = min(1.0, base + shimmer * rms)

        # 变奏：轻微扰动
        if variation_seed > 0:
            import random
            rng = random.Random(int(variation_seed * 10000))
            noise = (rng.random() - 0.5) * 0.1 * variation_seed
            base = max(0.0, min(1.0, base + noise))

        return base

    @staticmethod
    def _apply_curve(phase: float, curve: MotionCurve) -> float:
        """将相位值通过运动曲线转换为输出值"""
        import math
        t = max(0.0, min(1.0, phase))

        if curve == MotionCurve.LINEAR:
            return t
        elif curve == MotionCurve.EASE_IN:
            return t * t
        elif curve == MotionCurve.EASE_OUT:
            return 1.0 - (1.0 - t) ** 2
        elif curve == MotionCurve.EASE_IN_OUT:
            return t * t * (3.0 - 2.0 * t)
        elif curve == MotionCurve.BOUNCE:
            if t < 0.727:
                return 7.5625 * t * t
            elif t < 0.909:
                t -= 0.818
                return 7.5625 * t * t + 0.75
            elif t < 0.964:
                t -= 0.936
                return 7.5625 * t * t + 0.9375
            else:
                t -= 0.982
                return 7.5625 * t * t + 0.984375
        elif curve == MotionCurve.SPRING:
            return math.sin(t * math.pi * 2.5) * math.exp(-t * 3) + 1.0 - math.exp(-t * 3)
        elif curve == MotionCurve.SHARP_ATTACK:
            return 1.0 - math.exp(-t * 8)
        elif curve == MotionCurve.SHARP_DECAY:
            return math.exp(-t * 5)
        elif curve == MotionCurve.PULSE_CURVE:
            if t < 0.1:
                return t / 0.1
            elif t < 0.7:
                return 1.0
            else:
                return 1.0 - (t - 0.7) / 0.3
        elif curve == MotionCurve.SINE:
            return (math.sin(t * math.pi * 2 - math.pi / 2) + 1.0) / 2.0
        elif curve == MotionCurve.EXPONENTIAL:
            return (math.exp(t * 3) - 1.0) / (math.exp(3) - 1.0)
        elif curve == MotionCurve.LOGARITHMIC:
            return math.log(1.0 + t * 9) / math.log(10)
        elif curve == MotionCurve.STEP:
            return 1.0 if t >= 0.5 else 0.0
        elif curve == MotionCurve.RANDOM_WALK:
            import random
            rng = random.Random(int(t * 1000))
            return rng.random()
        elif curve == MotionCurve.SAWTOOTH:
            return t % 1.0
        elif curve == MotionCurve.TRIANGLE:
            return 1.0 - abs(2.0 * t - 1.0)
        else:
            return t


# ═══════════════════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════════════════

_global_motif_system: Optional[MotifVocabulary] = None


def get_motif_system() -> MotifVocabulary:
    """获取全局MotifVocabulary单例"""
    global _global_motif_system
    if _global_motif_system is None:
        _global_motif_system = MotifVocabulary()
    return _global_motif_system
