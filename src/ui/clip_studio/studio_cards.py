# -*- coding: utf-8 -*-
"""Clip Studio 卡片目录（R172 设计 §3.2 全表）—— dataclass + 常量，不用任何 Qt API。

每卡 = `CardSpec(key, 类别, 中文名, 说明, icon(tabler), art(底图类型), ports,
params, field_map, 推荐后继, 最低配套)`；产出 `CARD_CATALOG` 与 `PALETTE_GROUPS`。

**复用纪律（勿复制第二份清单）**——以下常量全部 import 自既有真源：

* 16 方向清单     → `src.ui.timeline.clip_context_menu._DIRECTIONS`
* 电机力度 5 预设 → `src.ui.timeline.clip_context_menu._MOTOR_FORCE_PRESETS`
* 平滑预设        → `src.engine.output_smoother.BUILTIN_SMOOTH_PRESETS`（循环生成）
* 过渡方式 7 策略 → `src.ui.timeline.transition_optimizer_dialog._SHUTDOWN_STRATEGIES`

（源模块自身 import Qt，但本文件只取其中的纯数据常量；本模块不引用 Qt 类型。
 icon 名一律取自 `src/ui/icon_assets/tabler/package/icons/{outline,filled}` **实存 svg**
 （tests/test_clip_studio_ui_r172.py 有全表探针防回归；勿从 curated 预设名臆造图标名）。
）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.engine.output_smoother import BUILTIN_SMOOTH_PRESETS
from src.core.group_selection import DEFAULT_GROUP_SPEC, SPEC_MODES
from src.ui import qascade_theme as _th
from . import studio_smoothing
from src.ui.timeline.clip_context_menu import _DIRECTIONS, _MOTOR_FORCE_PRESETS
from src.ui.timeline.transition_optimizer_dialog import _SHUTDOWN_STRATEGIES

__all__ = [
    "ParamSpec", "CardSpec", "CARD_CATALOG", "PALETTE_GROUPS", "GROUP_LABELS",
    "GROUP_COLORS", "PORT_IN", "PORT_OUT", "PORT_COMPOSITE", "SPEC_SUB_FIELDS",
    "EDGE_SCOPE_CHOICES", "COORD_SCOPE_CHOICES", "TRANSITION_STRATEGY_CHOICES",
    "MOTOR_FORCE_CHOICES", "TRANSITION_CURVE_CHOICES",
    "card", "keys_in_group",
]

# ── 桩位名（ports 元素取值）─────────────────────────────────────────────────
PORT_IN = "in"                # 入桩：接收参数供给 / 链上游
PORT_OUT = "out"              # 出桩：供给下游 / 链序
PORT_COMPOSITE = "composite"  # 复合入桩：模块收束（仅方向卡有）

# ── 效果大类（CardSpec.group；中文显示名见 GROUP_LABELS）────────────────────
GROUP_DIRECTION = "direction"
GROUP_SMOOTH = "smooth"
GROUP_BATCH = "batch"
GROUP_TRACKING = "tracking"
GROUP_MASTER = "master"
GROUP_OPTIMIZER = "optimizer"
GROUP_PARAM = "param"
GROUP_LOGIC = "logic"

#: 大类 → 中文（左栏分组标题用）。
GROUP_LABELS = {
    GROUP_DIRECTION: "过渡方向", GROUP_SMOOTH: "平滑处理",
    GROUP_BATCH: "过渡批次", GROUP_TRACKING: "灯跟踪",
    GROUP_MASTER: "主控类效果", GROUP_OPTIMIZER: "优化类效果",
    GROUP_PARAM: "参数类", GROUP_LOGIC: "逻辑判定",
}

#: 大类 → 卡片顶部色条（节点分类色条先例；全取自 qascade_theme 既有令牌，
#: 不新造颜色）。画布按 `CardSpec.group` 查表，未知组回退 `BORDER_NORMAL`。
GROUP_COLORS = {
    GROUP_DIRECTION: _th.FOCUS_BLUE, GROUP_SMOOTH: _th.BRIGHT_INFO,
    GROUP_BATCH: _th.ACTIVE_ORANGE, GROUP_TRACKING: _th.BRIGHT_WARN,
    GROUP_MASTER: _th.BRIGHT_ERROR, GROUP_OPTIMIZER: _th.BRIGHT_SUCCESS,
    GROUP_PARAM: _th.SELECT_BLUE, GROUP_LOGIC: _th.KEYFRAME_GOLD,
}

#: 指定组选择器（spec 子表）的数值子字段：(键, min, max)——语义照
#: `src/core/group_selection.py`（range/odd/even/stride，1-based）。
SPEC_SUB_FIELDS = (("start", 1, 64), ("end", 1, 64), ("take", 1, 16), ("gap", 0, 16))

#: 应用边卡选项（值与 `Clip.fade_direction_edge` 词汇一致：both/in/out）。
EDGE_SCOPE_CHOICES = (
    ("both", "整体（淡入淡出一致）"), ("in", "仅淡入"), ("out", "仅淡出"),
)

#: 坐标系卡选项（值与 `Clip.fade_group_spatial_scope` 词汇一致）。
COORD_SCOPE_CHOICES = (
    ("follow", "跟随全局"), ("per_shape", "各自选择组"), ("global", "整体"),
)

#: 过渡方式卡选项 = 跟随统一 + `_SHUTDOWN_STRATEGIES` 7 策略（import 复用）。
TRANSITION_STRATEGY_CHOICES = (
    ("follow", "跟随统一"),
) + tuple((sid, sname) for sid, sname, _d in _SHUTDOWN_STRATEGIES)

#: 电机力度卡预设选项 = 5 预设 + 自定义（import 复用 `_MOTOR_FORCE_PRESETS`）。
MOTOR_FORCE_CHOICES = (
    tuple((pid, pname) for pid, pname, _d in _MOTOR_FORCE_PRESETS)
    + (("custom", "自定义"),)
)

#: 主控过渡卡的过渡曲线选项 = 引擎 `TRANSITION_CURVES` 真词汇（`playback_engine.py`：
#: linear/easein/easeout/easeinout/bezier）。前四项与 `EASE_FUNCS_V` 的 linear/
#: ease_in/ease_out/ease_in_out **公式逐点一致**（t / t² / 1−(1−t)² / t²(3−2t)）；
#: `EASE_FUNCS_V` 的 exponential 引擎无消费点 → 不提供（防假接线）。
TRANSITION_CURVE_CHOICES = (
    ("linear", "线性"), ("easein", "缓入"), ("easeout", "缓出"),
    ("easeinout", "缓入缓出"), ("bezier", "贝塞尔"),
)


@dataclass(frozen=True)
class ParamSpec:
    """卡片参数条目（Studio 版精简 schema：key/label/type/default/min/max/step/choices/unit）。

    type ∈ int/float/bool/choice/str/color/spec；`spec` = 指定组选择器子表
    （值形如 `DEFAULT_GROUP_SPEC`，语义照 `src/core/group_selection.py`）。
    """
    key: str
    label: str
    type: str = "float"
    default: Any = 0.0
    min: Any = 0.0
    max: Any = 100.0
    step: Any = 1.0
    choices: tuple = ()
    unit: str = ""


@dataclass(frozen=True)
class CardSpec:
    """一张 Studio 卡片的静态规格（设计 §3.2）。"""
    key: str
    group: str
    name: str
    desc: str
    icon: str                       # tabler 名（须在 effect_presets_curated_data 出现过）
    art: str                        # 底图类型（画布按此画箭头/旋转/波形等美术图层）
    ports: tuple = (PORT_IN, PORT_OUT)
    params: tuple = ()              # (ParamSpec, ...)
    field_map: tuple = ()           # ((param_key, clip_field), ...)；param_key ""=卡片级
    recommends: tuple = ()          # 静态推荐后继卡 key
    min_companion: str = ""         # 最低配套说明（空=无要求）
    extra: dict = field(default_factory=dict, compare=False)


def _ps(key, label, ptype, **kw) -> ParamSpec:
    """ParamSpec 简写构造（保持表行紧凑）。"""
    return ParamSpec(key=key, label=label, type=ptype, **kw)


# ── 效果类 / 过渡方向 ×16 ───────────────────────────────────────────────────
#: 方向 id → 底图类型（§0-B7：箭头/旋转/波纹等美术底图）。
_DIR_ART = {
    "none": "uniform", "ltr": "arrow_ltr", "rtl": "arrow_rtl",
    "ftb": "arrow_ftb", "btf": "arrow_btf", "cw": "rotate_cw",
    "ccw": "rotate_ccw", "ctr": "center_out", "rtc": "edge_in",
    "alt": "alternate", "alt_block": "blocks", "wave": "wave",
    "cascade": "stair", "rev_cascade": "stair_rev", "zigzag": "zigzag",
    "random": "random",
}
#: 方向 id → tabler 图标（全部经 svg 实存核验：icon_assets/{outline,filled}，探针测试钉死）。
_DIR_ICONS = {
    "none": "circle-dot", "ltr": "arrow-right", "rtl": "arrow-left-bar",
    "ftb": "arrow-up-bar", "btf": "arrow-right-bar", "cw": "rotate-clockwise",
    "ccw": "rotate-2", "ctr": "arrows-maximize", "rtc": "filter", "alt": "arrows-shuffle",
    "alt_block": "grid-pattern", "wave": "wave-sine", "cascade": "stairs-down",
    "rev_cascade": "stairs-up", "zigzag": "wave-square", "random": "dice",
}
#: 平滑预设 id → tabler 图标（同上：svg 实存核验）。
_SMOOTH_ICONS = {
    "bypass": "route", "gentle": "feather", "natural": "lungs",
    "silky": "wave-sine", "punchy": "bolt", "elastic": "arrow-bounce",
    "heavy": "meteor", "fountain_valve": "toggle-left", "fountain_pump": "fountain",
    "led_dimmer": "brightness", "motor_servo": "engine",
    "audio_reactive": "activity",
}

#: 指定组 spec 参数的公共 ParamSpec（组数/批数卡用）。
_SPEC_PARAM = _ps(
    "spec", "指定组选择器", "spec", default=dict(DEFAULT_GROUP_SPEC),
    min=1, max=64, step=1, choices=tuple(SPEC_MODES),
)

#: 通道延迟比例卡覆盖的粗通道类型（键=引擎 `_norm_ratio_channel_type` 归一口径）。
_RATIO_TYPES = (
    ("dimmer", "调光"), ("r", "红"), ("g", "绿"), ("b", "蓝"), ("w", "白"),
    ("motor_x", "电机X"), ("motor_y", "电机Y"), ("motor_z", "电机Z"),
    ("pump", "泵"), ("valve", "阀"),
)


def _direction_cards() -> dict:
    """过渡方向 ×16（清单 import 自 `clip_context_menu._DIRECTIONS`，勿复制）。"""
    out = {}
    for did, dname in _DIRECTIONS:
        out[f"dir_{did}"] = CardSpec(
            key=f"dir_{did}", group=GROUP_DIRECTION, name=dname,
            desc=f"过渡方向「{dname}」：淡入/淡出按该方向扫过各组（受应用边卡约束；"
                 "串联指定组卡并收束进主方向卡 = 复合过渡子模块）。",
            icon=_DIR_ICONS.get(did, "arrow-right"), art=_DIR_ART.get(did, "arrow_ltr"),
            ports=(PORT_IN, PORT_OUT, PORT_COMPOSITE),
            field_map=(("", "fade_in_direction"), ("", "fade_out_direction")),
            recommends=("group_batch",),
            min_companion="复合模块：需指定组卡（组数/批数卡 spec）串联并收束进主方向卡",
        )
    return out


def _smooth_cards() -> dict:
    """平滑处理 = `BUILTIN_SMOOTH_PRESETS` 每预设一卡（含旁通），循环生成。"""
    out = {}
    for pid, p in BUILTIN_SMOOTH_PRESETS.items():
        out[f"smooth_{pid}"] = CardSpec(
            key=f"smooth_{pid}", group=GROUP_SMOOTH, name=f"平滑·{p.name}",
            desc=studio_smoothing.description(p),
            icon=_SMOOTH_ICONS.get(pid, "wave-sine"), art="curve",
            params=tuple(_ps(key, label, "float", default=default, min=low, max=high,
                             step=step, unit=unit)
                         for key, label, default, low, high, step, unit in studio_smoothing.parameter_rows(p)),
            field_map=(("", "smooth_preset"), ("", "smooth_preset_name"), ("", "smooth_custom_params")),
        )
    return out


def _ch_ratio_params() -> tuple:
    """通道延迟比例卡：每粗类型 × 淡入/淡出 各一滑杆（默认 1.0=100%）。"""
    return tuple(
        _ps(f"{io}_{t}", f"{lab}·{'淡入' if io == 'in' else '淡出'}",
            "float", default=1.0, min=0.0, max=2.0, step=0.05, unit="×")
        for io in ("in", "out") for t, lab in _RATIO_TYPES
    )


def _ch_ratio_field_map() -> tuple:
    return tuple(
        (f"{io}_{t}", f"fade_type_ratio_{io}")
        for io in ("in", "out") for t, _lab in _RATIO_TYPES
    )


def _add_master_cards(cards: dict) -> None:
    """§3.2 主控类效果 5 卡（限速/限位/开关灯/降压泵/电机相位差）。"""
    cards["motor_speed"] = CardSpec(
        key="motor_speed", group=GROUP_MASTER, name="电机限速",
        desc="虚拟最大摆速限制（度/秒），0=不限速。",
        icon="gauge", art="gear",
        params=(_ps("dps", "限速", "float", default=0.0, min=0.0, max=3600.0, step=10.0, unit="度/秒"),),
        field_map=(("dps", "motor_speed_limit_dps"),),
        recommends=("duration",),
    )
    cards["motor_limit"] = CardSpec(
        key="motor_limit", group=GROUP_MASTER, name="电机限位",
        desc="电机幅度往归位角缩放的比例系数，1.0=原样。",
        icon="scale", art="percent",
        params=(_ps("scale", "幅度系数", "float", default=1.0, min=0.0, max=1.0, step=0.01),),
        field_map=(("scale", "motor_limit_scale"),),
        recommends=("duration",),
    )
    cards["light_switch"] = CardSpec(
        key="light_switch", group=GROUP_MASTER, name="开关灯",
        desc="旧版开关灯卡；节目灯光总开关已移到主界面，旧图保留用于迁移。",
        icon="bolt", art="bulb",
        params=(_ps("mode", "灯输出", "choice", default="on",
                    choices=(("on", "启用灯输出"), ("off", "禁用灯输出"))),),
        field_map=(("mode", "__light_suppressed__"),),
    )
    cards["pump_master"] = CardSpec(
        key="pump_master", group=GROUP_MASTER, name="降压泵",
        desc="泵主控：泵输出同比例压低，1.0=原样。",
        icon="adjustments", art="valve",
        params=(_ps("scale", "输出比例", "float", default=1.0, min=0.0, max=1.0, step=0.01),),
        field_map=(("scale", "pump_master_scale"),),
        recommends=("duration",),
    )
    cards["motor_phase"] = CardSpec(
        key="motor_phase", group=GROUP_MASTER, name="电机相位差",
        desc="电机参与过渡相位差（参与开关 + 强度%）；百分比卡（落点=相位差强度）可连入供值。",
        icon="arrows-cross", art="wave",
        params=(
            _ps("enabled", "参与开关", "bool", default=True),
            _ps("amount", "相位差强度", "float", default=1.0, min=0.0, max=1.0, step=0.01, unit="×"),
        ),
        field_map=(("enabled", "motor_transition_phase_enabled"),
                   ("amount", "motor_transition_phase_amount")),
    )
    cards["master_transition"] = CardSpec(
        key="master_transition", group=GROUP_MASTER, name="主控过渡",
        desc="按通道类型单独设置淡入淡出比例（0-100%，行内滑杆；双击卡片可分设淡入/淡出）"
             "与过渡曲线（曲线为 Clip 级：引擎无逐类型曲线消费点）。行可增删，"
             "拖入时按绑定 Clip 的通道类型预填。",
        icon="adjustments-horizontal", art="curve",
        params=(
            _ps("curve_in", "淡入曲线", "choice", default="linear",
                choices=TRANSITION_CURVE_CHOICES),
            _ps("curve_out", "淡出曲线", "choice", default="linear",
                choices=TRANSITION_CURVE_CHOICES),
            # rows = [{"type","ratio_in","ratio_out"}]（0..100 整数）；归一在
            # studio_master.normalize_rows（studio_graph._coerce_param 分派）。
            _ps("rows", "通道类型比例行", "rows", default=[]),
        ),
        recommends=("duration",),
    )


def _add_optimizer_cards(cards: dict) -> None:
    """§3.2 优化类效果 3 卡（泵预充/过渡优化/电机力度）。"""
    cards["pump_precharge"] = CardSpec(
        key="pump_precharge", group=GROUP_OPTIMIZER, name="泵预充",
        desc="提前启动预充电（开关/提前量/预置电平）；「详细设置…」打开原预充对话框。",
        icon="sunrise", art="pump",
        params=(
            _ps("enabled", "泵预充", "bool", default=True),
            _ps("lead_ms", "预充提前量", "float", default=1000.0, min=0.0, max=10000.0, step=50.0, unit="ms"),
            _ps("level", "预置电平", "int", default=13, min=0, max=255, step=1),
        ),
        field_map=(("enabled", "precharge_enabled"), ("lead_ms", "precharge_lead_ms"),
                   ("level", "precharge_level")),
    )
    cards["trans_opt"] = CardSpec(
        key="trans_opt", group=GROUP_OPTIMIZER, name="过渡优化",
        desc="双击或点「执行优化…」打开原过渡优化器；至少绑定两个 Clip，确认后参数保存到 Clip。",
        icon="layers-difference", art="branch",
        params=(_ps("preset", "优化预设名", "str", default=""),),
        field_map=(("preset", "transitions_optimized"),),
    )
    cards["motor_force"] = CardSpec(
        key="motor_force", group=GROUP_OPTIMIZER, name="电机力度",
        desc="电机力度优化（5 预设 + 自定义曲线三段/力度层相位差）。",
        icon="grip-horizontal", art="gear",
        params=(
            _ps("preset", "力度预设", "choice", default="natural", choices=MOTOR_FORCE_CHOICES),
            _ps("phase_amount", "力度层相位差", "float", default=0.0, min=0.0, max=2.0, step=0.01),
            _ps("accel", "加速段", "float", default=0.2, min=0.0, max=1.0, step=0.01),
            _ps("cruise", "匀速段", "float", default=0.6, min=0.0, max=1.0, step=0.01),
            _ps("decel", "减速段", "float", default=0.2, min=0.0, max=1.0, step=0.01),
        ),
        field_map=(("preset", "motor_force_preset"), ("phase_amount", "motor_force_phase_amount"),
                   ("accel", "motor_force_accel"), ("cruise", "motor_force_cruise"),
                   ("decel", "motor_force_decel")),
    )


def _add_param_cards(cards: dict) -> None:
    """§3.2 参数类 4 卡（组数/批数[含指定组选择器]、百分比、时长、通道延迟比例）。"""
    cards["group_batch"] = CardSpec(
        key="group_batch", group=GROUP_PARAM, name="组数 / 批数",
        desc="拖入设置组数和批次数；开启指定组并串联方向卡=复合模块。",
        icon="grid-dots", art="grid",
        ports=(PORT_OUT,),
        params=(
            _ps("groups", "组数", "int", default=2, min=1, max=64, step=1, unit="组"),
            _ps("batches", "批次数", "int", default=0, min=0, max=64, step=1, unit="批"),
            _ps("spec_enabled", "指定组选择器", "bool", default=False),
            _SPEC_PARAM,
        ),
        field_map=(("groups", "fade_groups"), ("batches", "fade_group_sequence_batches"),
                   ("spec", "fade_group_direction_overrides")),
        recommends=("batch_seq",),
        min_companion="组数/批数拖入即生效；指定组选择器需接入复合过渡",
    )
    cards["percent"] = CardSpec(
        key="percent", group=GROUP_PARAM, name="百分比",
        desc="速度%（playback_rate）或 相位差强度%（目标下拉选择落点）。",
        icon="chart-bar", art="percent",
        ports=(PORT_OUT,),
        params=(
            _ps("target", "目标", "choice", default="playback_rate",
                choices=(("playback_rate", "速度"), ("motor_phase", "相位差强度"))),
            _ps("value", "数值", "float", default=100.0, min=0.0, max=1000.0, step=1.0, unit="%"),
        ),
        field_map=(("value", "playback_rate"), ("value", "motor_transition_phase_amount")),
        min_companion="速度拖入即生效，80%=原速的0.8倍；相位差强度需连到电机相位差卡",
    )
    cards["duration"] = CardSpec(
        key="duration", group=GROUP_PARAM, name="时长",
        desc="淡入时长 / 淡出时长（秒）；连到批次卡 = 组间停顿。",
        icon="metronome", art="clock",
        ports=(PORT_OUT,),
        params=(
            _ps("fade_in", "淡入时长", "float", default=0.0, min=0.0, max=3600.0, step=0.1, unit="秒"),
            _ps("fade_out", "淡出时长", "float", default=0.0, min=0.0, max=3600.0, step=0.1, unit="秒"),
            _ps("pause", "组间停顿", "float", default=0.0, min=0.0, max=3600.0, step=0.1, unit="秒"),
        ),
        field_map=(("fade_in", "fade_in"), ("fade_out", "fade_out"), ("pause", "fade_group_pause")),
        recommends=("batch_seq",),
        min_companion="淡入/淡出拖入即生效；组间停顿需连到批次卡",
    )
    cards["ch_ratio"] = CardSpec(
        key="ch_ratio", group=GROUP_PARAM, name="通道延迟比例",
        desc="各通道类型单独的过渡时间比例（原高级处理 Tab2 滑杆；1.0=100%）。",
        icon="adjustments-horizontal", art="valve",
        ports=(PORT_OUT,),
        params=_ch_ratio_params(),
        field_map=_ch_ratio_field_map(),
        min_companion="拖入即生效；各通道比例作用于已设置的过渡时长",
    )


def _add_logic_cards(cards: dict) -> None:
    """§3.2 逻辑判定 3 卡（应用边 / 坐标系 / 过渡方式）。"""
    cards["edge_scope"] = CardSpec(
        key="edge_scope", group=GROUP_LOGIC, name="应用边",
        desc="整体/仅淡入/仅淡出。连到方向卡上游=作用该卡（只写对应边）；独立放置=写全局应用边。",
        icon="arrow-curve-right", art="branch",
        ports=(PORT_OUT,),
        params=(_ps("edge", "应用边", "choice", default="both", choices=EDGE_SCOPE_CHOICES),),
        field_map=(("", "fade_direction_edge"),),
        recommends=("dir_ltr",),
    )
    cards["coord_scope"] = CardSpec(
        key="coord_scope", group=GROUP_LOGIC, name="坐标系",
        desc="跟随全局 / 各自选择组 / 整体 + 跳过编号排序（方向过渡的坐标参考）。",
        icon="compass", art="grid",
        ports=(PORT_OUT,),
        params=(
            _ps("scope", "坐标系", "choice", default="follow", choices=COORD_SCOPE_CHOICES),
            _ps("skip_order", "跳过编号排序", "bool", default=False),
        ),
        field_map=(("scope", "fade_group_spatial_scope"),
                   ("skip_order", "fade_group_skip_base_order")),
    )
    cards["transition_mode"] = CardSpec(
        key="transition_mode", group=GROUP_LOGIC, name="过渡方式",
        desc="淡入/淡出各自的关水策略（7 策略 + 跟随统一）。",
        icon="arrows-exchange", art="curve",
        ports=(PORT_OUT,),
        params=(
            _ps("strategy_in", "淡入过渡方式", "choice", default="follow",
                choices=TRANSITION_STRATEGY_CHOICES),
            _ps("strategy_out", "淡出过渡方式", "choice", default="follow",
                choices=TRANSITION_STRATEGY_CHOICES),
        ),
        field_map=(("strategy_in", "transition_strategy_in"),
                   ("strategy_out", "transition_strategy_out")),
    )


def build_catalog() -> dict:
    """§3.2 全表 → `{key: CardSpec}`（插入顺序 = 左栏展示顺序）。"""
    cards = {}
    cards.update(_direction_cards())
    cards.update(_smooth_cards())
    cards["batch_seq"] = CardSpec(
        key="batch_seq", group=GROUP_BATCH, name="顺序执行批次",
        desc="把分组过渡切成若干批次执行；批次数 0=跟随分组数。时长卡连到本卡 = 组间停顿。",
        icon="stack", art="branch",
        params=(
            _ps("batches", "批次数", "int", default=0, min=0, max=64, step=1, unit="批"),
            _ps("run_mode", "组间运行方式", "choice", default="parallel",
                choices=(("parallel", "并行"), ("sequential", "顺序"))),
        ),
        field_map=(("batches", "fade_group_sequence_batches"),
                   ("run_mode", "fade_group_sequence_mode")),
        recommends=("duration",),
    )
    cards["light_track"] = CardSpec(
        key="light_track", group=GROUP_TRACKING, name="自动灯跟踪",
        desc="按泵/阀输出自动点亮灯通道（预设一套齐：来源、阈值、attack/release、最大亮度、颜色）。",
        icon="bulb", art="bulb",
        params=(
            _ps("source", "跟踪来源", "choice", default="pump",
                choices=(("pump", "泵"), ("valve", "阀"))),
            _ps("threshold", "触发阈值", "int", default=128, min=0, max=255, step=1),
            _ps("attack", "灯亮淡入", "float", default=0.08, min=0.01, max=2.0, step=0.01, unit="秒"),
            _ps("release", "灯灭淡出", "float", default=0.22, min=0.01, max=2.0, step=0.01, unit="秒"),
            _ps("max_brightness", "最大亮度", "int", default=255, min=0, max=255, step=1),
            _ps("color", "目标颜色", "color", default="#FFFFFF"),
        ),
        field_map=(("", "light_tracking_enabled"), ("source", "light_tracking_source"),
                   ("threshold", "light_tracking_threshold"), ("attack", "light_tracking_attack"),
                   ("release", "light_tracking_release"), ("color", "light_tracking_color"),
                   ("max_brightness", "light_tracking_max_brightness")),
    )
    _add_master_cards(cards)
    _add_optimizer_cards(cards)
    _add_param_cards(cards)
    _add_logic_cards(cards)
    return cards


#: 卡片目录（key → CardSpec）——§3.2 唯一实现。
CARD_CATALOG: dict = build_catalog()

#: 左栏分组：(中文组名, [卡 key...])，顺序即展示顺序。
PALETTE_GROUPS: list = [
    ("过渡方向", [f"dir_{d}" for d, _n in _DIRECTIONS]),
    ("平滑处理", [k for k in CARD_CATALOG if CARD_CATALOG[k].group == GROUP_SMOOTH]),
    ("过渡批次", ["batch_seq"]),
    ("灯跟踪", ["light_track"]),
    ("主控类效果", ["motor_speed", "motor_limit", "pump_master",
                    "motor_phase", "master_transition"]),
    ("优化类效果", ["pump_precharge", "trans_opt", "motor_force"]),
    ("参数类", ["group_batch", "percent", "duration", "ch_ratio"]),
    ("逻辑判定", ["edge_scope", "coord_scope", "transition_mode"]),
]


def card(key: str) -> CardSpec | None:
    """按 key 取卡片规格（未知 key → None）。"""
    return CARD_CATALOG.get(str(key))


def keys_in_group(group: str) -> list:
    """某效果大类下的全部卡 key（目录顺序）。"""
    return [k for k, s in CARD_CATALOG.items() if s.group == group and k != "light_switch"]
