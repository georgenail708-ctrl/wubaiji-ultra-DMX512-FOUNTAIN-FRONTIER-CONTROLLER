# -*- coding: utf-8 -*-
"""
effect_presets_fam_data2 — R172 三族扩容数据表（E1 经 R173 精编留存：+8 randrun / +9 varyspeed / +10 foldcascade）

承接 effect_presets_fam_data（v1 基础 31 条）之后的三族扩容（用户需求："新效果明显不够多"）。
每条 = 真实 compute 内核变体（effect_presets_curated 的 randrun/varyspeed/foldcascade 工厂，
新 kind 数学走 _RR2_FNS / _VS_LOOK_FNS+_VS_KINDS / _FC2_FRONTS+_FC2_DEVICE 分发表）+
手调参数 + 传神中文名 + 一句话中文说明 + **Tabler outline 实存图标**（tests 里有全量核验）
+ 基础/中级/高级分级。单通道通用（ch_type choice=CH_TYPES）。
确定性与反克隆纪律同 v1；新增参数槽（density/mod_depth/单循环/游走 shape）全部带默认值，
不影响 v1 既有 kind（E2 C 化按本表 + v1 表全量对齐）。
"""
from __future__ import annotations

from .effect_engine import ParamSpec
from .effect_presets_fam_data import (
    _TT_SEED, _ch, _e, _fc_params, _hi, _lo, _rr_params,
    _vs_freq, _vs_spread, _vs_width,
)


# ───────────────────────── E1.randrun 专属新增参数槽 ─────────────────────────
def _rr2_density(v=0.6):
    return ParamSpec("density", "簇密度", "float", v, 0.0, 1.0, 0.02,
        tooltip="簇发类效果的簇内密度：值越大簇内连发间隔越短（仅簇发类使用）")


def _rr2_moddepth(v=0.75):
    return ParamSpec("mod_depth", "调制深度", "float", v, 0.0, 1.0, 0.02,
        tooltip="密度潮汐的慢波调制深度：0=恒定密度，1=繁忙段与安静段差异最大")


# ───────────────────────── E1.varyspeed：速度 shape 含两个新形状 ─────────────────────────
_VS_SHAPES2 = ["正弦", "三角", "阶梯", "呼吸", "顿挫", "心跳", "单循环", "游走"]


def _vs2_params(*, speed=1.0, swing=0.6, cycle=4.0, shape="正弦", uni=0.3, floor=0.0,
                extra=None, seed=False, ch="dimmer"):
    """扩容版变速参数：快慢分配新增「单循环」「游走」两个形状。"""
    ps = [
        _ch(ch),
        ParamSpec("speed", "中心速度", "float", speed, 0.0, 12.0, 0.05,
            tooltip="可变速度范围的中心点：快慢围绕该速度起伏。swing=0 时即恒速"),
        ParamSpec("swing", "差异度", "float", swing, 0.0, 1.0, 0.01,
            tooltip="周期内快慢的差异度：0=恒速（与对应恒速载体逐位一致），1=快慢差拉满"),
        ParamSpec("cycle", "变速周期", "float", cycle, 0.5, 30.0, 0.1, unit="s",
            tooltip="速度忽快忽慢的一个循环时长（秒）"),
        ParamSpec("vs_shape", "快慢分配", "choice", shape, choices=_VS_SHAPES2,
            tooltip="快慢段分配形状：单循环=一周期一次完整加减速（满 swing 首尾触底停）；"
                    "游走=速度乘子经种子平滑随机游走（确定性可跳读）"),
        ParamSpec("uniformity", "均匀度", "float", uni, 0.0, 1.0, 0.01,
            tooltip="快慢段的时间偏斜：0=快慢段各半均匀，1=快段极短慢段极长"),
        ParamSpec("floor", "停顿占比", "float", floor, 0.0, 0.85, 0.01,
            tooltip="每个周期末尾速度触底为 0 的停顿窗占比。0=不停顿（游走形状忽略本参数）"),
    ]
    if seed:
        ps.append(ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1, tooltip=_TT_SEED))
    ps.extend(extra or [])
    ps.extend([_lo(), _hi()])
    return ps


def _fam2_rows_rr():
    """扩容伪随机增强族行表：(kind, 中文名, 一句话说明, 图标(Tabler outline 实存), 分级, 参数)。"""
    return [
        ("dualzone", "双域对跑", "左右两个半区各自独立随机游走包，左一下右一下各跑各的", "arrows-diff", "中级",
         _rr_params(speed=1.6, run_size=3, proximity=0.6, phase_gap=0.5, decay=0.45, dmode="跟随")),
        ("shufwalk", "乱序点检", "落点按种子洗牌全排列依次点检，一遍点完整排重洗", "arrows-shuffle", "基础",
         _rr_params(speed=1.4, run_size=2, proximity=0.9, phase_gap=0.0, decay=0.45)),
        ("siteripple", "随机涟心", "每个随机落点发出双向扩散环，环速环宽随宽度", "ripple", "中级",
         _rr_params(speed=1.0, run_size=2, proximity=0.7, phase_gap=0.0, decay=0.75)),
        ("burst", "簇发乱蹦", "2-4 连发快簇与静默窗交替，簇内间隔随簇密度", "bomb", "基础",
         _rr_params(speed=1.5, run_size=2, proximity=0.6, phase_gap=0.3, decay=0.45)
         + [_rr2_density(0.6)]),
        ("converge", "对撞乱汇", "两落点相向游走、中央对撞闪光后从两端重生", "arrows-diagonal", "高级",
         _rr_params(speed=1.0, run_size=3, proximity=0.5, phase_gap=0.0, decay=0.5)),
        ("parit", "奇偶乱跳", "触发在奇数偶数序位间交替，各自区内随机落点", "binary", "基础",
         _rr_params(speed=1.6, run_size=4, proximity=0.5, phase_gap=0.3, decay=0.5)),
        ("denswave", "密度潮汐", "触发密度被慢波调制，繁忙段与安静段交替涨落", "wave-sine", "高级",
         _rr_params(speed=1.8, run_size=2, proximity=0.6, phase_gap=0.4, decay=0.5)
         + [_rr2_moddepth(0.75)]),
        ("wavewalk", "波形游走", "正弦波形的峰值位置本身随机游走，波形与随机叠加", "route", "高级",
         _rr_params(speed=1.2, run_size=3, proximity=0.8, phase_gap=0.0, decay=0.6, dmode="跟随")),
    ]


def _fam2_rows_vs():
    """扩容变速族行表：9 个新载体 look，速度 shape 错开选（含单循环/游走）。"""
    return [
        ("wavepkt", "波包行进", "局部振荡波包行进，包内波峰波谷可见", "chart-histogram", "高级",
         _vs2_params(speed=0.8, swing=0.6, cycle=4.0, shape="三角", extra=[_vs_freq(1.0)])),
        ("string", "绳波驻立", "两列反向波叠加的驻波，波腹原地起伏", "vector-spline", "中级",
         _vs2_params(speed=0.6, swing=0.6, cycle=5.0, shape="正弦", extra=[_vs_freq(1.5)])),
        ("marquee", "走马吞速", "等宽亮块循环推进，忽急忽缓的走马灯", "rectangle-vertical", "基础",
         _vs2_params(speed=0.8, swing=0.6, cycle=3.5, shape="阶梯", extra=[_vs_width(0.12)])),
        ("levelbar", "水位起落", "水位线沿阵列升起又回落，像蓄水又放水", "chart-bar", "中级",
         _vs2_params(speed=0.5, swing=0.6, cycle=5.0, shape="游走", seed=True)),
        ("buildup", "蓄势逐级", "四级台阶逐级抬升的蓄势 build", "stack-2", "中级",
         _vs2_params(speed=0.6, swing=0.65, cycle=4.0, shape="单循环")),
        ("pluck", "拨弦吞速", "快攻慢衰的单发拨音，节奏快慢随种子游走", "music", "基础",
         _vs2_params(speed=0.8, swing=0.6, cycle=2.5, shape="游走", seed=True)),
        ("swell", "涌浪慢起", "慢攻慢放带平台期的涌浪，沿阵列缓缓行进", "droplets", "基础",
         _vs2_params(speed=0.4, swing=0.55, cycle=6.0, shape="呼吸", extra=[_vs_spread()])),
        ("stair", "阶梯爬升", "整场量化台阶逐级爬升", "stairs", "基础",
         _vs2_params(speed=0.7, swing=0.6, cycle=3.0, shape="阶梯")),
        ("fountain", "泉涌吞速", "跑泉包变速行进，喷涌忽急忽缓（泵版变速桥）", "fountain", "中级",
         _vs2_params(speed=0.7, swing=0.6, cycle=4.0, shape="顿挫", extra=[_vs_width(0.16, "喷宽")])),
    ]


def _fam2_rows_fc():
    """扩容折返级联族行表：(kind, 中文名, 一句话说明, 图标(Tabler outline 实存), 分级, 参数)。"""
    return [
        ("trianchor", "三点轮折", "三个锚点两端与中心轮转接力的折返", "compass", "基础",
         _fc_params(speed=0.5, pause=0.1, width=0.13, accel=0.3)),
        ("meetbloom", "汇流绽放", "两端双波向心汇合，合流点再向外双环开花", "flower", "中级",
         _fc_params(speed=0.4, pause=0.12, width=0.12, accel=0.3)),
        ("decay", "能量衰减折", "每波幅度宽度递减的能量耗尽感，五波后回满", "trending-down", "中级",
         _fc_params(speed=0.5, pause=0.1, width=0.14, accel=0.35)),
        ("echofol", "影随波", "主波后拖一个半幅随影，半速差追随", "copy", "中级",
         _fc_params(speed=0.45, pause=0.1, width=0.12, accel=0.3)),
        ("elevator", "电梯折返", "离散步进上升顶层折返，步间停顿如电梯", "elevator", "中级",
         _fc_params(speed=0.5, pause=0.1, width=0.1, accel=0.25)),
        ("trough", "暗谷行进", "反相级联：行进的是暗谷，到端点暗花外扩", "arrow-narrow-down", "高级",
         _fc_params(speed=0.4, pause=0.12, width=0.13, accel=0.3, handoff="花开")),
        ("wallpush", "水墙推进", "宽平水墙整体推进端点折返，墙后保持（泵友好）", "wall", "基础",
         _fc_params(speed=0.4, pause=0.0, width=0.16, accel=0.25)),
        ("interleave", "双源交错", "两个源交替发波，A 波 B 波交错推进", "arrows-exchange", "中级",
         _fc_params(speed=0.5, pause=0.1, width=0.12, accel=0.3)),
        ("ringfold", "环折巡游", "序位当环上角度，折返沿环双向巡游（圆弧友好）", "rotate-clockwise", "高级",
         _fc_params(speed=0.45, pause=0.08, width=0.12, accel=0.2)),
        ("microbloom", "碎花连绽", "随机落点的小型开花高频连绽，多而小", "sparkles-2", "高级",
         _fc_params(speed=0.9, pause=0.06, width=0.08, accel=0.3)),
    ]


def build_fam2_presets():
    """R172 三族扩容预设数据表（E1 精编后 +27：id 前缀沿用 cur_randrun_ / cur_vs_ / cur_fcdc_）。"""
    F = []
    add = F.append
    for kind, name, d, icon, tier, params in _fam2_rows_rr():
        add(_e(eid=f"cur_randrun_{kind}", name=name, cat="通用/随机", desc=d, kernel="randrun",
               variant=kind, icon=icon, tier=tier, params=params))
    for kind, name, d, icon, tier, params in _fam2_rows_vs():
        add(_e(eid=f"cur_vs_{kind}", name=name, cat="通用/变速", desc=d, kernel="varyspeed",
               variant=kind, icon=icon, tier=tier, params=params))
    for kind, name, d, icon, tier, params in _fam2_rows_fc():
        add(_e(eid=f"cur_fcdc_{kind}", name=name, cat="通用/级联", desc=d, kernel="foldcascade",
               variant=kind, icon=icon, tier=tier, params=params))
    return F
