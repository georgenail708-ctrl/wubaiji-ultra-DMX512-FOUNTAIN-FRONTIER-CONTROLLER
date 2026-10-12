"""
command_arrange.ir — 命令行代码编曲的「中间表示 (IR)」与共享契约

这是整个 command_arrange 包的**单一真源**：解析器(lexer_parser)产出 IR，
渲染器(renderer)消费 IR，沙箱(sandbox)执行 IR 里的代码，导出器(skill_export)
按这里的关键字表生成教学文档。所有模块都从本文件 import，禁止各自硬编码关键字，
以免漂移。

纯 core，无任何 PyQt 依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
#  方向集合（与 clip 右击菜单 / playback_engine 完全一致，一个不少）
# ─────────────────────────────────────────────────────────────────────────────
# 内部规范值 → (中文别名 list, 英文别名 list)。第一个英文别名为规范写法。
DIRECTIONS: Dict[str, Tuple[List[str], List[str]]] = {
    "none":        (["整体", "无方向", "无"], ["none", "whole"]),
    "ltr":         (["从左到右", "左到右", "左右"], ["ltr", "l2r"]),
    "rtl":         (["从右到左", "右到左", "右左"], ["rtl", "r2l"]),
    "ftb":         (["从前到后", "前到后"], ["ftb", "f2b"]),
    "btf":         (["从后到前", "后到前"], ["btf", "b2f"]),
    "cw":          (["顺时针"], ["cw"]),
    "ccw":         (["逆时针"], ["ccw"]),
    "ctr":         (["中心向两边", "中心扩散", "由内而外"], ["ctr", "center"]),
    "rtc":         (["两边向中心", "向心", "由外而内"], ["rtc"]),
    "alt":         (["奇偶交替", "奇偶"], ["alt", "oddeven"]),
    "alt_block":   (["块交替"], ["alt_block", "altblock"]),
    "wave":        (["波浪"], ["wave"]),
    "cascade":     (["级联"], ["cascade"]),
    "rev_cascade": (["反向级联", "反级联"], ["rev_cascade", "revcascade"]),
    "zigzag":      (["折返", "之字"], ["zigzag"]),
    "random":      (["随机"], ["random", "rand"]),
}


def _build_direction_lookup() -> Dict[str, str]:
    lut: Dict[str, str] = {}
    for canon, (zh, en) in DIRECTIONS.items():
        lut[canon.lower()] = canon
        for a in zh + en:
            lut[str(a).strip().lower()] = canon
    return lut


_DIRECTION_LUT = _build_direction_lookup()


def normalize_direction(raw: str) -> Optional[str]:
    """把任意中英别名规范化为内部方向值；未知返回 None。"""
    if raw is None:
        return None
    return _DIRECTION_LUT.get(str(raw).strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
#  通道类型（与 voice_clip_renderer.build_patch / 配接通道名一致）
# ─────────────────────────────────────────────────────────────────────────────
# 规范通道类型 → 中英别名。规范值就是配接里的 channel type 名。
CHANNEL_TYPES: Dict[str, List[str]] = {
    "pump":     ["pump", "泵", "水泵"],
    "valve":    ["valve", "阀", "电磁阀", "阀门"],
    "motor_x":  ["motor_x", "mx", "电机x", "数控x", "水平"],
    "motor_y":  ["motor_y", "my", "电机y", "数控y", "俯仰"],
    "motor_z":  ["motor_z", "mz", "电机z", "数控z"],
    "R":        ["R", "r", "红", "red"],
    "G":        ["G", "g", "绿", "green"],
    "B":        ["B", "b", "蓝", "blue"],
    "W":        ["W", "w", "白", "white"],
    "dimmer":   ["dimmer", "dim", "调光", "亮度", "总亮"],
}


def _build_channel_lookup() -> Dict[str, str]:
    lut: Dict[str, str] = {}
    for canon, aliases in CHANNEL_TYPES.items():
        lut[canon.lower()] = canon
        for a in aliases:
            lut[str(a).strip().lower()] = canon
    return lut


_CHANNEL_LUT = _build_channel_lookup()


def normalize_channel(raw: str) -> Optional[str]:
    """把任意中英别名规范化为配接通道类型；未知返回 None。"""
    if raw is None:
        return None
    return _CHANNEL_LUT.get(str(raw).strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
#  关键字别名表（中英双语）— 块内每个属性行的引导词
# ─────────────────────────────────────────────────────────────────────────────
# 规范键 → 别名 list（含中英）。lexer 用它把一行的首词映射到属性。
KEYWORDS: Dict[str, List[str]] = {
    "slct":        ["slct", "select", "sel", "选", "选择"],
    "at":          ["at", "start", "起", "起始", "开始"],
    "dur":         ["dur", "duration", "长", "持续", "时长"],
    "fi":          ["fi", "fadein", "fade_in", "淡入"],
    "fo":          ["fo", "fadeout", "fade_out", "淡出"],
    "dir":         ["dir", "direction", "方向"],
    "sets":        ["sets", "groups", "set", "分组", "组"],
    "seq":         ["seq", "sequential", "顺序", "依次"],
    "parallel":    ["parallel", "par", "并行", "同时"],
    "pause":       ["pause", "停顿", "组间停顿"],
    "batches":     ["batches", "batch", "批次", "执行批次"],
    "scope":       ["scope", "坐标系", "空间"],
    "wings":       ["wings", "wing", "翼"],
    "block":       ["block", "blk", "block_size", "块"],
    "wingdir":     ["wingdir", "wing_dir", "翼方向", "翼向"],
    "wing_mirror": ["wing_mirror", "wingmirror", "反射对称"],
    "wing_reverse": ["wing_reverse", "wingreverse", "倒转"],
    "param":       ["param", "params", "set_param", "参数", "变量"],
    "seq_delay":   ["seq_delay", "delay", "延迟", "依次延迟"],
    "fade_ratio":  ["fade_ratio", "ratio", "过渡比", "通道过渡比"],
    "track_light": ["track_light", "lighttrack", "灯跟踪", "跟踪"],
    "motorphase":  ["motorphase", "motor_phase", "电机相位", "相位差"],
    "hold":        ["hold", "保持"],
    "rate":        ["rate", "speed_rate", "速度", "倍速"],
    "smooth":      ["smooth", "平滑", "自动平滑"],
    "rec_src":     ["rec_src", "record_source", "录制接收", "录制源"],
    "def":         ["def"],   # def 全式效果函数（python 关键字，原样）
}


def _build_keyword_lookup() -> Dict[str, str]:
    lut: Dict[str, str] = {}
    for canon, aliases in KEYWORDS.items():
        for a in aliases:
            lut[str(a).strip().lower()] = canon
    return lut


_KEYWORD_LUT = _build_keyword_lookup()


def normalize_keyword(raw: str) -> Optional[str]:
    if raw is None:
        return None
    return _KEYWORD_LUT.get(str(raw).strip().lower())


# 坐标系 scope 别名
SCOPE_ALIASES: Dict[str, List[str]] = {
    "per_shape": ["per_shape", "pershape", "各自", "各自坐标系", "各自选择组"],
    "global":    ["global", "整体", "整体坐标系"],
    "follow":    ["follow", "跟随", "跟随全局灯控", "跟随灯控"],
}
_SCOPE_LUT = {a.strip().lower(): c for c, al in SCOPE_ALIASES.items() for a in al}
_SCOPE_LUT.update({c: c for c in SCOPE_ALIASES})


def normalize_scope(raw: str) -> Optional[str]:
    if raw is None:
        return None
    return _SCOPE_LUT.get(str(raw).strip().lower())


# 保持模式别名
HOLD_ALIASES: Dict[str, List[str]] = {
    "none":       ["none", "off", "无", "关"],
    "last_frame": ["last_frame", "lastframe", "末帧", "最后一帧"],
    "last_cycle": ["last_cycle", "lastcycle", "末周期", "最后一周期"],
}
_HOLD_LUT = {a.strip().lower(): c for c, al in HOLD_ALIASES.items() for a in al}
_HOLD_LUT.update({c: c for c in HOLD_ALIASES})


def normalize_hold(raw: str) -> Optional[str]:
    if raw is None:
        return None
    return _HOLD_LUT.get(str(raw).strip().lower())


# 灯跟踪来源别名
TRACK_SOURCE_ALIASES: Dict[str, List[str]] = {
    "pump":  ["pump", "泵"],
    "valve": ["valve", "阀"],
}
_TRACK_SRC_LUT = {a.strip().lower(): c for c, al in TRACK_SOURCE_ALIASES.items() for a in al}


def normalize_track_source(raw: str) -> Optional[str]:
    if raw is None:
        return None
    return _TRACK_SRC_LUT.get(str(raw).strip().lower())


# 翼方向别名（对齐 utils.wing_block 的四模式 + 符号 / \ < >）
WING_DIR_ALIASES: Dict[str, List[str]] = {
    "low_to_high": ["low_to_high", "lth", "/", "asc", "up", "正", "低到高"],
    "high_to_low": ["high_to_low", "htl", "\\", "desc", "down", "反", "高到低"],
    "high_low":    ["high_low", "<", "中聚", "高低"],
    "low_high":    ["low_high", ">", "中扩", "低高"],
}
_WING_DIR_LUT = {a.strip().lower(): c for c, al in WING_DIR_ALIASES.items() for a in al}
_WING_DIR_LUT.update({c: c for c in WING_DIR_ALIASES})


def normalize_wing_dir(raw: str) -> Optional[str]:
    if raw is None:
        return None
    return _WING_DIR_LUT.get(str(raw).strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
#  选择器（设备编号三种方案，向真实代码看齐）
# ─────────────────────────────────────────────────────────────────────────────
SELECT_GLOBAL = "global"        # slct 1-10 / 1,5,9-12  → project.fixtures 全局序(1-based)
SELECT_GROUP = "group"          # slct 环1 / "外圈灯"     → fixture_groups 整组
SELECT_GROUP_INDEX = "group_index"  # slct 环1.2-4       → 组内成员下标(1-based)
SELECT_ALL = "all"              # slct all              → 当前项目全部设备
SELECT_TYPE = "type"            # slct type pump        → 配接含该通道类型的全部设备

# 'all' / 'type' 选择关键字别名(渲染期解析,不依赖坐标)
SELECT_ALL_ALIASES = ["all", "全部", "所有", "全选"]
SELECT_TYPE_ALIASES = ["type", "类型", "按类型", "kind"]
_SELECT_ALL_SET = {a.strip().lower() for a in SELECT_ALL_ALIASES}
_SELECT_TYPE_SET = {a.strip().lower() for a in SELECT_TYPE_ALIASES}


def is_select_all_token(raw: str) -> bool:
    return raw is not None and str(raw).strip().lower() in _SELECT_ALL_SET


def is_select_type_token(raw: str) -> bool:
    return raw is not None and str(raw).strip().lower() in _SELECT_TYPE_SET


# 选择器算子(控台风格)中英双语别名 → 规范算子
# 注:不收 '选'(=slct line-keyword)/ '起'(=at line-keyword),避免跨类同形歧义(#11)。
SELECT_OPS: Dict[str, List[str]] = {
    "thru":  ["thru", "through", "to", "至", "到", "—"],
    "step":  ["step", "by", "间隔", "步进", "每隔"],
    "count": ["count", "qty", "个", "取"],
    "from":  ["from", "从"],
}
_SELECT_OP_LUT = {a.strip().lower(): c for c, al in SELECT_OPS.items() for a in al}
_SELECT_OP_LUT.update({c: c for c in SELECT_OPS})


def normalize_select_op(raw: str) -> Optional[str]:
    """把选择器算子别名(thru/step/count/from 及中文)规范化；未知返回 None。"""
    if raw is None:
        return None
    return _SELECT_OP_LUT.get(str(raw).strip().lower())


MAX_SELECT: int = 50000  # 单条选择器最多展开的索引数，防止无上界循环卡死


@dataclass
class SelectorTerm:
    """选择器的一个项。多个项的并集 = clip 选中设备。

    两种产出索引(1-based)的形态(控台风格):
      区间   : ranges=[(lo,hi),...]，step 控制步进(相邻选中相差 step)。
      from式 : start + step + count(从 start 起按 step 取 count 个；可选 end 截止)。
    step=相邻选中编号相差 N(控台通用语义)：1 thru 10 step 3 → 1,4,7,10。
    """
    kind: str = SELECT_GLOBAL          # SELECT_*
    group_ref: str = ""                # group 名或 id（kind != global 时）
    type_ref: str = ""                 # SELECT_TYPE 时的通道类型名（如 pump）
    ranges: List[Tuple[int, int]] = field(default_factory=list)  # [(lo,hi),...] 1-based 闭区间
    step: int = 1                      # 步进(>=1)，相邻选中编号相差 step
    count: int = 0                     # 取个数；0=不限(由区间/end 决定)
    start: int = 0                     # from 式起点(1-based)；0=未用 from 式
    end: int = 0                       # from 式可选截止(含)；0=不截止

    def expand_indices(self) -> List[int]:
        """展开为有序 1-based 索引列表(去重保序)。"""
        step = max(1, int(self.step or 1))
        out: List[int] = []
        seen = set()
        produced = 0

        def _add(v: int):
            if v >= 1 and v not in seen:
                seen.add(v); out.append(v)

        def _bump() -> None:
            nonlocal produced
            produced += 1
            if produced > MAX_SELECT:
                raise ValueError(
                    f"选择器展开索引数超过上限 {MAX_SELECT}，请缩小 slct 范围或增大 step")

        if self.start:  # from 式
            cnt = int(self.count or 0)
            v = int(self.start)
            if cnt > 0:
                for _ in range(cnt):
                    if self.end and v > self.end:
                        break
                    _bump(); _add(v); v += step
            elif self.end:   # from..end，无 count
                while v <= self.end:
                    _bump(); _add(v); v += step
            else:
                _add(self.start)   # 只给了 from，无 count/end → 单个
            return out

        # 区间式：每个 (lo,hi) 内按 step 取
        for lo, hi in self.ranges:
            v = lo
            while v <= hi:
                _bump(); _add(v); v += step
                if self.count and produced >= self.count:
                    return out
        return out



@dataclass
class Selector:
    terms: List[SelectorTerm] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
#  通道体（标量速写 或 def 全式）
# ─────────────────────────────────────────────────────────────────────────────
BODY_SCALAR = "scalar"   # ch: <expr>     —— 每帧每台对 <expr> 求值写该通道
BODY_DEF = "def"         # def effect(c): ... —— 自由代码，c.set(ch, val)


@dataclass
class ChannelBody:
    kind: str = BODY_SCALAR
    channel: str = ""        # scalar: 规范通道类型；def: 留空（函数自己 c.set）
    code: str = ""           # scalar: 表达式源；def: 整个 def 函数源（含签名）
    src_line: int = 0        # 源行号（错误定位 / 沙箱回溯）


# ─────────────────────────────────────────────────────────────────────────────
#  Clip 语句（一个 clip{ } = 一个 ClipStmt，承载全部右击可设置项）
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ClipStmt:
    # 块头
    label: str = ""
    track_name: str = ""            # -> track "名"；空=按通道类型给默认轨名
    src_line: int = 0

    # 选择 + 效果体
    selector: Selector = field(default_factory=Selector)
    bodies: List[ChannelBody] = field(default_factory=list)

    # 时间
    at: float = 0.0                 # 起始（秒）；None 语义由 parser 决定，这里默认 0
    at_explicit: bool = False       # 是否显式写了 at（未写则 renderer 接在上一条后）
    dur: float = 4.0
    dur_explicit: bool = False      # 是否显式写了 dur（未写时 renderer/校验给提示，默认 4s）

    # 过渡 / 淡入淡出
    fade_in: float = 0.0
    fade_out: float = 0.0
    direction: str = "none"         # 规范方向值（同时用于 in/out）

    # 分组
    sets: int = 1
    sequence_mode: str = "parallel"  # parallel / sequential
    pause: float = 0.0
    pauses: List[float] = field(default_factory=list)
    batches: int = 0                # 0=等于分组数
    scope: str = "per_shape"        # per_shape / global / follow

    # 翼 / 块（effect 引擎通用空间分组；渲染期把设备折成虚拟同步单元）
    wings: int = 1                  # 翼数：把设备序列切成 N 段，每段跑同一波形
    block_size: int = 1             # 块大小：相邻 N 台共用一个单元值（阶梯）
    wing_dir: str = "low_to_high"   # 翼内方向：low_to_high/high_to_low/high_low/low_high

    wing_mirror: bool = False
    wing_reverse: bool = False

    # 依次延迟 / 分通道过渡比
    seq_delay_in: float = 0.0
    seq_delay_out: float = 0.0
    fade_ratio_in: Dict[str, float] = field(default_factory=dict)
    fade_ratio_out: Dict[str, float] = field(default_factory=dict)

    # 灯跟踪
    track_light_enabled: bool = False
    track_light_source: str = "pump"     # pump / valve
    track_light_threshold: int = 128
    track_light_attack: float = 0.08
    track_light_release: float = 0.22
    track_light_color: str = "#FFFFFF"

    # 电机相位
    motor_phase_enabled: bool = False
    motor_phase_amount: float = 1.0

    # 保持 / 速度 / 平滑 / 录制接收
    hold_mode: str = "none"
    rate: float = 1.0
    smooth_preset: str = ""
    rec_src_enabled: bool = False

    # 渲染器/沙箱可用参数（def 里通过 c.p[...] 取；scalar 里直接作变量名）
    params: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
#  解析错误（带行号定位，供 UI 下划线 + 实时校验）
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ParseError:
    line: int = 0
    col: int = 0
    message: str = ""

    def __str__(self) -> str:
        return f"行{self.line}: {self.message}"


class CommandParseException(Exception):
    """解析期致命错误的容器，携带一组 ParseError。"""
    def __init__(self, errors: List[ParseError]):
        self.errors = list(errors or [])
        super().__init__("; ".join(str(e) for e in self.errors) or "解析失败")


@dataclass
class ParseResult:
    """parse() 的返回：成功的 clip + 收集到的错误（容错解析，部分成功也返回）。"""
    clips: List[ClipStmt] = field(default_factory=list)
    errors: List[ParseError] = field(default_factory=list)
    warnings: List[ParseError] = field(default_factory=list)  # 非致命提示(dur默认/能力不符等)

    @property
    def ok(self) -> bool:
        return not self.errors

    def track_names(self) -> List[str]:
        seen = []
        for c in self.clips:
            tn = c.track_name or ""
            if tn and tn not in seen:
                seen.append(tn)
        return seen
