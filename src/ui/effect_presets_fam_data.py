# -*- coding: utf-8 -*-
"""
effect_presets_fam_data — R172 三族新效果数据表（伪随机增强 / 变速载体 / 折返级联）

对应 docs/clip_studio_r172_design.md §5.1-5.3（W2a）。每条 = 真实 compute 内核变体
（effect_presets_curated 的 randrun / varyspeed / foldcascade 工厂）+ 手调参数 +
传神中文名 + 一句话中文说明 + 图标（沿用 curated 数据表出现过的图标名）+ 基础/中级/高级分级。
单通道通用（ch_type choice=CH_TYPES）；speed/wings/block_size/effect_width/phase_spread/
subset_delay_phase 等通用参数由注册器 `_reg → _append_universal_params` 自动追加，此处不重复。
"""
from __future__ import annotations
import logging

from .effect_engine import EffectPreset, ParamSpec, CH_TYPES

_ALL = list(CH_TYPES)

_TT_CH = "选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"
_TT_SPEED = "控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"
_TT_SEED = "控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"
#: randrun 专属（§5.1）：触发链预计算缓存上限 _RANDRUN_CAP=4096，触发序按 4096
#: 取模复用 → 模式周期性环绕。别族种子无此特性，故不并入共享 _TT_SEED。
_TT_SEED_RR = _TT_SEED + "。触发链长度 4096 后模式周期性环绕"
_TT_LO = "控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"
_TT_HI = "控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"


def _ch(ch="dimmer"):
    return ParamSpec("ch_type", "通道类别", "choice", ch, choices=_ALL, tooltip=_TT_CH)


def _lo(v=0):
    return ParamSpec("lo", "低值", "int", v, 0, 255, 1, tooltip=_TT_LO)


def _hi(v=255):
    return ParamSpec("hi", "高值", "int", v, 0, 255, 1, tooltip=_TT_HI)


# ───────────────────────── 5.1 伪随机增强族参数 ─────────────────────────
def _rr_params(*, speed=2.0, run_size=2, proximity=0.6, phase_gap=0.5, decay=0.4,
               dmode="随机", ch="dimmer"):
    return [
        _ch(ch),
        ParamSpec("speed", "触发节奏", "float", speed, 0.0, 12.0, 0.05,
            tooltip="每秒触发的跑动包个数。值越大落点越密集"),
        ParamSpec("run_size", "跑动宽度", "int", run_size, 1, 64, 1, unit="台",
            tooltip="每次触发点亮的连续设备数（跑动包宽度）。1=单点、越大越成片"),
        ParamSpec("proximity", "临近程度", "float", proximity, 0.0, 1.0, 0.01,
            tooltip="相邻两次触发落点的临近程度：0=全区域乱跳，1=紧邻游走（跑泉式左一下右一下）"),
        ParamSpec("phase_gap", "触发相位差", "float", phase_gap, 0.0, 1.0, 0.01,
            tooltip="跑动包内设备的相位差：0=整体一下同时亮，1=依次跑动（完全错相）"),
        ParamSpec("decay", "衰减", "float", decay, 0.02, 1.0, 0.01,
            tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ParamSpec("direction_mode", "包方向", "choice", dmode, choices=["跟随", "交替", "随机"],
            tooltip="每次触发的跑动方向：跟随=顺上次游走方向，交替=左右横跳，随机=按种子散列"),
        ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1, tooltip=_TT_SEED_RR),
        _lo(), _hi(),
    ]


# ───────────────────────── 5.2 速度忽快忽慢族参数 ─────────────────────────
def _vs_params(*, speed=1.0, swing=0.6, cycle=4.0, shape="正弦", uni=0.3, floor=0.0,
               extra=None, seed=False, ch="dimmer"):
    ps = [
        _ch(ch),
        ParamSpec("speed", "中心速度", "float", speed, 0.0, 12.0, 0.05,
            tooltip="可变速度范围的中心点：快慢围绕该速度起伏。swing=0 时即恒速"),
        ParamSpec("swing", "差异度", "float", swing, 0.0, 1.0, 0.01,
            tooltip="周期内快慢的差异度：0=恒速（与对应恒速载体逐位一致），1=快慢差拉满（配合停顿占比可到一会动一会停）"),
        ParamSpec("cycle", "变速周期", "float", cycle, 0.5, 30.0, 0.1, unit="s",
            tooltip="速度忽快忽慢的一个循环时长（秒）"),
        ParamSpec("vs_shape", "快慢分配", "choice", shape,
            choices=["正弦", "三角", "阶梯", "呼吸", "顿挫", "心跳"],
            tooltip="一个周期内快慢段的分配形状：正弦=平滑起伏、三角=匀变速、阶梯=快慢各半、呼吸=慢快慢、顿挫=短促快+停、心跳=强弱双击"),
        ParamSpec("uniformity", "均匀度", "float", uni, 0.0, 1.0, 0.01,
            tooltip="快慢段的时间偏斜：0=快慢段各半均匀，1=快段极短慢段极长"),
        ParamSpec("floor", "停顿占比", "float", floor, 0.0, 0.85, 0.01,
            tooltip="每个周期末尾速度触底为 0 的停顿窗占比。0=不停顿；越大一动一停越明显"),
    ]
    if seed:
        ps.append(ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1, tooltip=_TT_SEED))
    ps.extend(extra or [])
    ps.extend([_lo(), _hi()])
    return ps


def _vs_freq(v=1.0):
    return ParamSpec("spatial_freq", "空间频率", "float", v, 0.0, 12.0, 0.1,
        tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集")


def _vs_width(v=0.15, label="宽度"):
    return ParamSpec("width", label, "float", v, 0.01, 1.0, 0.01,
        tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽")


def _vs_spread():
    return ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
        tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广")


def _vs_density(v=0.3):
    return ParamSpec("density", "密度", "float", v, 0.0, 1.0, 0.02,
        tooltip="控制单位空间内的元素密度。值越大视觉元素越密集")


# ───────────────────────── 5.3 折返级联族参数 ─────────────────────────
def _fc_params(*, speed=0.45, pause=0.12, width=0.14, groups=1, accel=0.35,
               handoff="硬接", direction="往返", ch="dimmer"):
    return [
        _ch(ch),
        ParamSpec("speed", "波速度", "float", speed, 0.0, 8.0, 0.02,
            tooltip="单波推进速度：值越大一波走完越快（组间停顿另计）"),
        ParamSpec("pause", "组间停顿", "float", pause, 0.0, 2.0, 0.01, unit="×波长",
            tooltip="两波之间的静默停顿（占单波时长的比例）：0=上一波落点紧接着下一波起点，越大波与波之间静止越久"),
        ParamSpec("width", "波宽", "float", width, 0.02, 1.0, 0.01,
            tooltip="波峰前锋的空间宽度。值越大波带越宽"),
        ParamSpec("groups", "组数", "int", groups, 1, 16, 1,
            tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ParamSpec("accel", "推进加速", "float", accel, 0.0, 1.0, 0.01,
            tooltip="波峰推进的加速曲线：0=匀速，越大越由慢到快地推进"),
        ParamSpec("handoff", "交接过渡", "choice", handoff, choices=["硬接", "淡接", "花开", "回卷"],
            tooltip="两波交接时的过渡方式：硬接=到点即接、淡接=落点淡出、花开=落点双向开花重叠、回卷=落点回卷回起点"),
        ParamSpec("direction", "方向", "choice", direction, choices=["往返", "单向"],
            tooltip="往返=波峰在落点间折返接力；单向=每次都从同一起点出发"),
        ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1, tooltip=_TT_SEED),
        _lo(), _hi(),
    ]


def _e(*, eid, name, cat, desc, kernel, variant, icon, tier, params):
    return dict(id=eid, name=name, category=cat, desc=desc, kernel=kernel,
                variant=variant, channels=_ALL, icon=icon,
                tags=["curated", tier], params=params)


def _fam_rows_rr():
    """伪随机增强族行表：(kind, 中文名, 一句话说明, 图标, 分级, 参数)。"""
    return [
        # (kind, 中文名, 一句话说明, 图标, 分级, 参数覆盖)
        ("sparkwalk", "星火游走", "落点紧邻游走的星火，起燃即衰减，左一下右一下", "sparkles", "基础",
         _rr_params(speed=2.2, run_size=2, proximity=0.85, phase_gap=0.55, decay=0.5, dmode="跟随")),
        ("dualrace", "双头对跑", "左右对称双落点相向对冲跑动", "arrows-shuffle", "基础",
         _rr_params(speed=1.6, run_size=3, proximity=0.5, phase_gap=0.7, decay=0.45, dmode="交替")),
        ("runjetrand", "跑泉增强", "跑泉式随机落点成片喷涌（泵版跑动增强）", "runjet", "基础",
         _rr_params(speed=1.2, run_size=5, proximity=0.55, phase_gap=0.75, decay=0.6,
                    dmode="跟随", ch="pump")),
        ("flamespark", "火焰星飞", "带逐点闪烁噪点的火星飞溅", "flame", "中级",
         _rr_params(speed=2.6, run_size=2, proximity=0.65, phase_gap=0.5, decay=0.45)),
        ("twinklerand", "随机微闪", "临近游走的柔和微闪场，一片此起彼伏的细碎闪光", "star", "基础",
         _rr_params(speed=1.5, run_size=3, proximity=0.75, phase_gap=0.6, decay=0.6, dmode="跟随")),
        ("fallstar", "群星坠落", "落点包尾向下一站滑动的接力星群", "meteor", "中级",
         _rr_params(speed=1.3, run_size=3, proximity=0.6, phase_gap=0.6, decay=0.55, dmode="跟随")),
    ]


def _fam_rows_vs():
    """速度忽快忽慢族行表：(kind, 中文名, 一句话说明, 图标, 分级, 参数)。"""
    return [
        # (kind, 中文名, 一句话说明, 图标, 分级, 参数)
        ("wave_sine", "缓板波形", "忽急忽缓的正弦波，速度围绕中心起伏", "wave-sine", "基础",
         _vs_params(speed=1.0, swing=0.6, cycle=4.0, extra=[_vs_freq(1.0)])),
        ("wave_tri", "三角变速", "匀加减速的三角波推进", "triangle", "中级",
         _vs_params(speed=1.0, swing=0.6, cycle=4.0, shape="三角", extra=[_vs_freq(1.0)])),
        ("wave_saw", "锯齿涌进", "快段冲刺慢段回落的锯齿涌动", "wave-saw-tool", "高级",
         _vs_params(speed=0.9, swing=0.65, cycle=5.0, shape="顿挫", uni=0.5, extra=[_vs_freq(1.0)])),
        ("chase_comet", "忽急忽缓彗星", "彗星拖尾时快时慢地掠过", "comet", "基础",
         _vs_params(speed=0.9, swing=0.6, cycle=4.0, extra=[_vs_width(0.12, "拖尾宽度"),
                                                            ParamSpec("tail", "拖尾", "float", 0.6, 0.0, 1.0, 0.02,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久")])),
        ("chase_dot", "顿挫跑点", "单点跑动一顿一冲", "point", "基础",
         _vs_params(speed=0.8, swing=0.7, cycle=3.0, shape="顿挫", uni=0.5,
                    extra=[_vs_width(0.08)])),
        ("chase_kitt", "回弹扫光", "往返扫描忽进忽停的回弹感", "arrow-bounce", "高级",
         _vs_params(speed=0.8, swing=0.6, cycle=4.0, shape="呼吸", extra=[_vs_width(0.15)])),
        ("breathe_heart", "心悸双搏", "心跳式忽强忽弱的双搏动", "heartbeat", "中级",
         _vs_params(speed=0.8, swing=0.6, cycle=3.0, shape="心跳")),
        ("breathe_tide", "潮汐吞速", "潮汐式慢快慢的长周期吞吐", "ripple", "基础",
         _vs_params(speed=0.35, swing=0.6, cycle=8.0, shape="呼吸", uni=0.5)),
        ("twinkle_drift", "醉闪星尘", "微闪节奏忽密忽疏的星尘", "stars", "中级",
         _vs_params(speed=0.8, swing=0.6, cycle=4.0, seed=True, extra=[_vs_density(0.3)])),
        ("pumpwave_tide", "潮涌泵浪", "泵浪叠加潮汐副波的长周期涌动", "tide-water", "中级",
         _vs_params(speed=0.5, swing=0.6, cycle=7.0, shape="呼吸")),
        ("arch", "水拱吞速", "水拱起落忽急忽缓", "arch", "基础",
         _vs_params(speed=0.7, swing=0.6, cycle=4.0, extra=[_vs_width(0.18)])),
        ("pendulum", "钟摆吞速", "整排倾摆忽快忽慢如钟摆", "pendulum", "基础",
         _vs_params(speed=0.6, swing=0.6, cycle=4.0)),
        ("windmill_dual", "双翼轮转变速", "双峰轮转的变速风车", "fan", "高级",
         _vs_params(speed=0.8, swing=0.65, cycle=4.0, extra=[_vs_freq(2.0)])),
    ]


def _fam_rows_fc():
    """折返级联族行表：(kind, 中文名, 一句话说明, 图标, 分级, 参数)。"""
    return [
        # (kind, 中文名, 一句话说明, 图标, 分级, 参数覆盖)
        ("fold", "单线折返", "波峰从落处起从起处落的单线折返接力", "fold", "基础",
         _fc_params(speed=0.45, pause=0.12, width=0.13, accel=0.35)),
        ("bloomc", "交错开花", "中心外扩开花、落点向外交替接力的开花级联", "flower", "中级",
         _fc_params(speed=0.4, pause=0.15, width=0.12, accel=0.3, handoff="花开")),
        ("bounce", "弹球衰减", "折返逐波衰减再重新起跳的弹球", "arrow-bounce", "基础",
         _fc_params(speed=0.6, pause=0.1, width=0.12, accel=0.45)),
        ("pingpong", "乒乓对滑", "两波前恒相对对穿的乒乓级联", "seesaw", "基础",
         _fc_params(speed=0.55, pause=0.1, width=0.1, accel=0.25)),
        ("arch", "水拱接力", "水拱在落点处重新起拱的接力（泵版）", "arch", "基础",
         _fc_params(speed=0.45, pause=0.12, width=0.2, accel=0.35)),
        ("layer", "灯层折返", "宽幅光层来回折返铺光", "layers-difference", "基础",
         _fc_params(speed=0.4, pause=0.15, width=0.3, accel=0.3)),
        ("surge", "涌动级联", "由慢到快汹涌推进、落点淡出的涌浪", "swell", "高级",
         _fc_params(speed=0.3, pause=0.25, width=0.16, accel=0.8, handoff="淡接")),
        ("fold3", "三段折返", "阵列分三段落跳接的折返", "stairs", "中级",
         _fc_params(speed=0.55, pause=0.1, width=0.12, accel=0.35)),
        ("ripple", "涟漪回流", "波进到落点再回卷的涟漪回流", "ripple", "高级",
         _fc_params(speed=0.4, pause=0.18, width=0.12, accel=0.3, handoff="花开")),
        ("dual", "左右开弓", "两端向中、中向外交替开弓", "wings", "高级",
         _fc_params(speed=0.45, pause=0.12, width=0.12, accel=0.35)),
    ]


#: R173 精编下架的 v1 变体（参数克隆 / 近似重复）：按 HEAD 原行原样注册完整预设后下架
#: （effect_retire：列表隐藏、按 id 仍可解析），旧场景快照/自定义效果/录制引用输出逐位不变。
#: 行 = (id 前缀, kernel, 分类, kind, 中文名, 一句话说明, 图标, 分级, 参数)。
def _fam_rows_retired():
    rr = ("cur_randrun_", "randrun", "通用/随机")
    vs = ("cur_vs_", "varyspeed", "通用/变速")
    fc = ("cur_fcdc_", "foldcascade", "通用/级联")
    return [
        (*rr, "hopall", "全域跳点", "落点全区域乱跳的单点硬闪", "dice", "基础",
         _rr_params(speed=2.5, run_size=1, proximity=0.0, phase_gap=0.0, decay=0.3)),
        (*rr, "tritrace", "三头轮跑", "三等分轮转错相起燃的三个跑动头", "multihead", "中级",
         _rr_params(speed=1.4, run_size=2, proximity=0.6, phase_gap=0.8, decay=0.5, dmode="跟随")),
        (*rr, "edgehop", "边缘乱蹦", "落点偏向两端蹦跳，中段少光", "arrows-cross", "中级",
         _rr_params(speed=2.0, run_size=2, proximity=0.45, phase_gap=0.4, decay=0.35)),
        (*rr, "centerhop", "中心乱蹦", "落点聚在中部附近乱蹦", "circle-dot", "中级",
         _rr_params(speed=2.0, run_size=2, proximity=0.5, phase_gap=0.4, decay=0.4)),
        (*rr, "shower", "阵雨", "多包同窗的密集阵雨，快起快落", "droplets", "基础",
         _rr_params(speed=1.8, run_size=2, proximity=0.7, phase_gap=0.3, decay=0.3)),
        (*rr, "fireflyd", "流萤", "小包高密度缓燃的流萤浮动", "stars", "中级",
         _rr_params(speed=1.2, run_size=1, proximity=0.8, phase_gap=0.5, decay=0.7)),
        (*rr, "pulsepoint", "心跳乱点", "强衰减打击式乱点，颗颗有力", "heartbeat", "中级",
         _rr_params(speed=2.4, run_size=1, proximity=0.6, phase_gap=0.0, decay=0.18)),
        (*rr, "sidehop", "左右横跳", "方向交替的大步横跳", "alternate", "基础",
         _rr_params(speed=1.8, run_size=2, proximity=0.5, phase_gap=0.2, decay=0.35, dmode="交替")),
        (*rr, "grandhop", "礼花乱蹦", "宽包重落点的大颗礼花蹦跳", "popcorn", "高级",
         _rr_params(speed=1.0, run_size=6, proximity=0.4, phase_gap=0.35, decay=0.5)),
        (*vs, "breathe_soft", "吞速柔呼吸", "呼吸吞速：吸得急呼得缓的柔呼吸", "lungs", "基础",
         _vs_params(speed=0.5, swing=0.55, cycle=5.0, shape="呼吸")),
        (*vs, "twinkle_grid", "乱频密闪", "阶梯式忽快忽慢的高频密闪", "bolt", "高级",
         _vs_params(speed=1.2, swing=0.7, cycle=2.5, shape="阶梯", uni=0.7, seed=True,
                    extra=[_vs_density(0.5)])),
        (*vs, "pumpwave", "泵浪吞速", "泵浪推进忽快忽慢，如浪被吞咽", "water-wall", "中级",
         _vs_params(speed=0.7, swing=0.6, cycle=5.0)),
        (*vs, "arch_wide", "阔拱缓涌", "宽拱幅慢吞吐的大水拱", "fountain", "高级",
         _vs_params(speed=0.4, swing=0.65, cycle=6.0, shape="呼吸", extra=[_vs_width(0.32)])),
        (*vs, "pendulum_soft", "柔摆微速", "轻微速度起伏的柔摆", "swing", "基础",
         _vs_params(speed=0.5, swing=0.35, cycle=5.0, shape="正弦")),
        (*vs, "windmill", "风车变速", "折叠轮转忽快忽慢", "windmill", "中级",
         _vs_params(speed=0.8, swing=0.6, cycle=4.0, extra=[_vs_freq(2.0)])),
        (*fc, "slide", "匀速折返", "匀速滑动的往返折返级联", "vector-spline", "基础",
         _fc_params(speed=0.5, pause=0.08, width=0.14, accel=0.0)),
        (*fc, "bloomedge", "双边开花", "两端交替双边开花、错瓣外扩", "alternate", "中级",
         _fc_params(speed=0.4, pause=0.15, width=0.12, accel=0.3, handoff="花开")),
        (*fc, "chasef", "追逐折返", "窄锋快推、到点急折的追逐折返", "scanner", "中级",
         _fc_params(speed=0.7, pause=0.08, width=0.08, accel=0.55)),
    ]


def _build_retired_fam_presets():
    """下架旧变体的完整预设 dict（与在库表同构，供注册后下架）。"""
    return [_e(eid=f"{prefix}{kind}", name=name, cat=cat, desc=d, kernel=kern, variant=kind,
               icon=icon, tier=tier, params=params)
            for prefix, kern, cat, kind, name, d, icon, tier, params in _fam_rows_retired()]


def legacy_fam_ids():
    """下架 v1 变体 [(preset_id, kernel, variant)]；注册器、CE hook 与测试共用。"""
    return [(f"{row[0]}{row[3]}", row[1], row[3]) for row in _fam_rows_retired()]


def _build_fam_presets_v1():
    """R172 三族预设基础表 v1（R173 精编后 29 条；id 前缀 cur_randrun_ / cur_vs_ / cur_fcdc_）。"""
    F = []
    add = F.append
    # ══════════════ 通用/随机：伪随机增强族（randrun）══════════════
    for kind, name, d, icon, tier, params in _fam_rows_rr():
        add(_e(eid=f"cur_randrun_{kind}", name=name, cat="通用/随机", desc=d, kernel="randrun",
               variant=kind, icon=icon, tier=tier, params=params))
    # ══════════════ 通用/变速：速度忽快忽慢族（varyspeed）══════════════
    for kind, name, d, icon, tier, params in _fam_rows_vs():
        add(_e(eid=f"cur_vs_{kind}", name=name, cat="通用/变速", desc=d, kernel="varyspeed",
               variant=kind, icon=icon, tier=tier, params=params))
    # ══════════════ 通用/级联：折返级联族（foldcascade）══════════════
    for kind, name, d, icon, tier, params in _fam_rows_fc():
        add(_e(eid=f"cur_fcdc_{kind}", name=name, cat="通用/级联", desc=d, kernel="foldcascade",
               variant=kind, icon=icon, tier=tier, params=params))
    return F


def build_fam_presets():
    """R172 三族预设全量表 = v1 基础 29 条（本文件）+ E1 扩容 27 条（effect_presets_fam_data2）。

    注册/测试/探针一律取本函数（R173 精编后 56 条）；下架旧 id 见 `legacy_fam_ids()`。
    """
    from .effect_presets_fam_data2 import build_fam2_presets
    return _build_fam_presets_v1() + build_fam2_presets()


def _reg_fam_rows(engine, fams) -> int:
    from .effect_presets_curated import KERNELS, icon_glyph
    for fam in fams:
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
        engine._reg(preset, KERNELS[fam["kernel"]](fam["variant"]))
    return len(fams)


def _register_fam_presets(engine) -> int:
    """注册 R172 三族预设 + R173 下架旧变体（注册后即下架）；断言条数（v37-v40 死注册教训）。"""
    from .effect_retire import retire_presets
    fams = build_fam_presets()
    count = _reg_fam_rows(engine, fams)
    retired = _build_retired_fam_presets()
    _reg_fam_rows(engine, retired)
    n_ret = retire_presets(engine, [f["id"] for f in retired])
    logging.getLogger(__name__).info(
        "r172 family presets registered: %s (table=%d, r173 retired=%d)", count, len(fams), n_ret)
    assert all(f["id"] in engine._presets for f in fams), "r172 family registration lost presets"
    assert n_ret == len(retired), f"r173 family retire lost presets: {n_ret} != {len(retired)}"
    return count
