"""
matrix_slide_engine.py — 矩阵 ON/OFF 幻灯片引擎
=================================================
核心职责：
  1. 管理 MatrixSlide 帧序列（数据模型）
  2. 播放控制：循环/单次/乒乓/随机，帧间过渡（立切/淡入淡出/逐个ON/逐个OFF）
  3. 实时输出到 NM "matrix" 层（稀疏写入，仅写 pump/valve 通道）
  4. 内置喷泉动作序列预设生成器

设计原则：
  - 纯数据/逻辑层，不依赖 Qt UI（可被 UI 层调用）
  - 与 NetworkManager 解耦：通过回调注入 NM 引用
  - 支持序列化/反序列化（用于项目保存）
"""
from __future__ import annotations

from .matrix_wing_flags import transform_generated

import copy
import math
import random
import time
from dataclasses import dataclass, field

import numpy as np
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ...utils.wing_block import (
    WING_DIRECTION_LOW_TO_HIGH,
    build_wing_block_units,
)
from .output_shaper import OUTPUT_SHAPE_DEFAULTS, normalize_output_shape_dict
from .matrix_gray_frames import normalize_state_value, state_level
from .matrix_output_lut import normalize_zone_gamma_dict
from .matrix_channel_targets import (
    install_engine_maps, refresh_profile_maps, set_engine_profile_definitions, write_engine_weights,
)
from .matrix_channel_profiles import normalize_channel_profiles
from .matrix_ppt_detail_sequences import DETAIL_SEQUENCE_GENERATORS, generate_detail_sequence

# ─────────────────────────────────────────────────────────────────────────────
#  通道类型常量
# ─────────────────────────────────────────────────────────────────────────────
CH_PUMP  = "pump"
CH_VALVE = "valve"
CH_DIMMER = "dimmer"

SUPPORTED_CH_TYPES = [CH_PUMP, CH_VALVE, CH_DIMMER]

# ─────────────────────────────────────────────────────────────────────────────
#  过渡模式
# ─────────────────────────────────────────────────────────────────────────────
TRANS_CUT       = "cut"         # 立切（无过渡）
TRANS_FADE      = "fade"        # 淡入淡出（线性插值）
TRANS_SEQ_ON    = "seq_on"      # 逐个 ON（按位置顺序依次点亮）
TRANS_SEQ_OFF   = "seq_off"     # 逐个 OFF（按位置顺序依次熄灭）
TRANS_SEQ_BOTH  = "seq_both"    # 先逐个 OFF 再逐个 ON
TRANS_WIPE_LR   = "wipe_lr"     # 从左到右擦除切换
TRANS_WIPE_RL   = "wipe_rl"     # 从右到左擦除切换
TRANS_WIPE_TB   = "wipe_tb"     # 从上到下擦除切换
TRANS_WIPE_BT   = "wipe_bt"     # 从下到上擦除切换
TRANS_WIPE_OUT  = "wipe_out"    # 从中心向外擦除
TRANS_WIPE_IN   = "wipe_in"     # 从外向中心擦除

TRANS_MODES = {
    TRANS_CUT:      "立切",
    TRANS_FADE:     "淡入淡出",
    TRANS_SEQ_ON:   "逐个 ON",
    TRANS_SEQ_OFF:  "逐个 OFF",
    TRANS_SEQ_BOTH: "先OFF后ON",
    TRANS_WIPE_LR:  "左→右擦除",
    TRANS_WIPE_RL:  "右→左擦除",
    TRANS_WIPE_TB:  "上→下擦除",
    TRANS_WIPE_BT:  "下→上擦除",
    TRANS_WIPE_OUT: "中心→外擦除",
    TRANS_WIPE_IN:  "外→中心擦除",
}

# ─────────────────────────────────────────────────────────────────────────────
#  播放模式
# ─────────────────────────────────────────────────────────────────────────────
PLAY_LOOP     = "loop"      # 循环
PLAY_ONCE     = "once"      # 单次（播完停在最后一帧）
PLAY_PINGPONG = "pingpong"  # 乒乓
PLAY_RANDOM   = "random"    # 随机

PLAY_MODES = {
    PLAY_LOOP:     "循环",
    PLAY_ONCE:     "单次",
    PLAY_PINGPONG: "乒乓",
    PLAY_RANDOM:   "随机",
}


# ─────────────────────────────────────────────────────────────────────────────
#  数据模型
# ─────────────────────────────────────────────────────────────────────────────

def _safe_int(v, *, default: int = 0, minimum=None, maximum=None) -> int:
    """任意值 → int（失败取 default；可带上下限；helper 压 CC）。"""
    try:
        i = int(v)
    except (TypeError, ValueError):
        i = default
    if minimum is not None:
        i = max(int(minimum), i)
    if maximum is not None:
        i = min(int(maximum), i)
    return i


def _safe_float(v, *, default: float = 0.0, lo=None, hi=None) -> float:
    """任意值 → float（失败/NaN 取 default；可带上下限；helper 压 CC）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = default
    if f != f:
        f = default
    if lo is not None:
        f = max(float(lo), f)
    if hi is not None:
        f = min(float(hi), f)
    return f


@dataclass
class MatrixSlide:
    """一张幻灯片帧：记录每个设备的亮度状态

     深查②三#1 P0 灰度帧：states 值域由 bool 扩展到 **0-255 灰度**。
      * 消费侧兼容：读权重一律走 `state_level`（bool=True→1.0 旧语义逐字节不变；
        int/float → v/255），生成器/面板既有 bool 写入点无需改动。
      * 存储侧规整：`to_dict`/`from_dict` 落盘为 int 0-255（bool True→255）。
    """
    name: str = "帧"
    # 设备亮度状态：{fixture_id: 0-255}；bool True/False 亦合法（=255/0）
    # 未出现的设备默认为 OFF(0)
    states: Dict[str, int] = field(default_factory=dict)
    # 停留时长（秒），0 = 手动切换
    hold_time: float = 2.0
    # 过渡时长（秒）
    transition_time: float = 0.5
    # 过渡模式
    transition_mode: str = TRANS_CUT
    # ON 时输出值（0-255）
    on_value: int = 255
    # OFF 时输出值（0-255）
    off_value: int = 0
    # 备注
    note: str = ""

    def on_count(self) -> int:
        return sum(1 for v in self.states.values() if v)

    def off_count(self) -> int:
        return sum(1 for v in self.states.values() if not v)

    def total_count(self) -> int:
        return len(self.states)

    def get_on_ids(self) -> Set[str]:
        return {fid for fid, v in self.states.items() if v}

    def get_off_ids(self) -> Set[str]:
        return {fid for fid, v in self.states.items() if not v}

    def state_level(self, fid: str) -> float:
        """单设备输出权重 0..1（bool→1.0/0.0 兼容；int→v/255 灰度）。"""
        return state_level(self.states.get(fid, 0))

    def levels(self) -> Dict[str, float]:
        """整帧权重 {fid: 0..1}（灰度帧的输出视角）。"""
        return {fid: state_level(v) for fid, v in self.states.items()}

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "states": {k: normalize_state_value(v) for k, v in self.states.items()},
            "hold_time": self.hold_time,
            "transition_time": self.transition_time,
            "transition_mode": self.transition_mode,
            "on_value": self.on_value,
            "off_value": self.off_value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "MatrixSlide":
        s = cls()
        s.name = d.get("name", "帧")
        #  灰度帧：旧工程 bool（True→255）与新 int 0-255 都规整为 int
        s.states = {str(k): normalize_state_value(v)
                    for k, v in d.get("states", {}).items()}
        s.hold_time = float(d.get("hold_time", 2.0))
        s.transition_time = float(d.get("transition_time", 0.5))
        s.transition_mode = d.get("transition_mode", TRANS_CUT)
        s.on_value = int(d.get("on_value", 255))
        s.off_value = int(d.get("off_value", 0))
        s.note = d.get("note", "")
        return s

    def clone(self) -> "MatrixSlide":
        return copy.deepcopy(self)


@dataclass
class MatrixSlideshowConfig:
    """幻灯片序列配置"""
    # 关联的设备组 ID（空字符串=全部设备）
    group_id: str = ""
    group_name: str = "全部设备"
    # 输出通道类型
    ch_type: str = CH_PUMP
    # 播放模式
    play_mode: str = PLAY_LOOP
    # 全局 ON 值覆盖（-1=使用每帧自己的 on_value）
    global_on_value: int = -1
    # 全局 OFF 值覆盖（-1=使用每帧自己的 off_value）
    global_off_value: int = -1
    #  R178 输出整形：Matrix 输出全局平滑淡入淡出（attack/release/曲线）。
    #   dict 形态见 output_shaper.OUTPUT_SHAPE_DEFAULTS；enabled=False = 关闭
    #   （旧工程无此字段 → from_dict 补默认关闭 → 旧行为逐字节不变）。
    output_shape: Dict = field(default_factory=lambda: dict(OUTPUT_SHAPE_DEFAULTS))
    channel_profiles: Dict = field(default_factory=normalize_channel_profiles)
    #  深查②三#10 P2 BPM 量化播放：开启后停留时长不再用每帧 hold_time，
    #   而是 60/bpm × bpm_beats（按节拍切帧）。默认关闭 = 旧行为。
    bpm_enabled: bool = False
    bpm: float = 120.0
    bpm_beats: int = 1
    #  深查②三#8 P2 每分区输出 Gamma LUT（dict 形态见 matrix_output_lut）。
    output_gamma: Dict = field(default_factory=lambda: {
        "enabled": False, "zone_count": 2, "gammas": [1.0, 1.0]})
    #  R183 视频扩展（深查②三#5/#2/#3/#11）：摄像头源/入出点/方向/速率/
    #   四角透视/掩码路径。全部默认 = 旧行为（文件源、整段、正向 1x、不变形、无掩码）。
    video_source: str = "file"
    camera_device: int = 0
    video_in_point: int = 0
    video_out_point: int = 0
    video_direction: str = "forward"
    video_rate: float = 1.0
    video_warp_corners: Optional[list] = None
    sample_mask_path: str = ""

    def _video_ext_dict(self) -> Dict:
        """视频扩展字段 → 落盘 dict（helper 压 to_dict CC）。"""
        return {
            "video_source": str(self.video_source or "file"),
            "camera_device": int(self.camera_device or 0),
            "video_in_point": int(self.video_in_point or 0),
            "video_out_point": int(self.video_out_point or 0),
            "video_direction": str(self.video_direction or "forward"),
            "video_rate": float(self.video_rate or 1.0),
            "video_warp_corners": (list(self.video_warp_corners)
                                   if self.video_warp_corners else None),
            "sample_mask_path": str(self.sample_mask_path or ""),
        }

    def _load_video_ext(self, d: Dict) -> None:
        """视频扩展字段 ← 旧档 dict（缺键 = 默认旧行为；helper 压 from_dict CC）。"""
        from .matrix_video_transforms import (
            normalize_video_direction, normalize_video_rate,
            normalize_video_source_kind,
        )
        #  W2 #4/#6：file/camera/spout/ndi 全量规整（外部纹理源意图随工程落盘）
        self.video_source = normalize_video_source_kind(d.get("video_source", "file"))
        self.camera_device = _safe_int(d.get("camera_device", 0), minimum=0)
        self.video_in_point = _safe_int(d.get("video_in_point", 0), minimum=0)
        self.video_out_point = _safe_int(d.get("video_out_point", 0), minimum=0)
        self.video_direction = normalize_video_direction(d.get("video_direction"))
        self.video_rate = normalize_video_rate(d.get("video_rate", 1.0))
        corners = d.get("video_warp_corners")
        self.video_warp_corners = list(corners) if corners else None
        self.sample_mask_path = str(d.get("sample_mask_path", "") or "")

    def effective_hold_time(self, frame: "MatrixSlide") -> float:
        """该帧的实际停留秒数（BPM 量化开启时按 60/bpm×拍数）。"""
        if self.bpm_enabled and self.bpm > 0:
            beat = 60.0 / float(self.bpm)
            return max(0.01, beat * max(1, int(self.bpm_beats)))
        return frame.hold_time

    def to_dict(self) -> Dict:
        out = {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "ch_type": self.ch_type,
            "play_mode": self.play_mode,
            "global_on_value": self.global_on_value,
            "global_off_value": self.global_off_value,
            "output_shape": dict(self.output_shape or {}),
            "channel_profiles": normalize_channel_profiles(self.channel_profiles),
            "bpm_enabled": bool(self.bpm_enabled),
            "bpm": float(self.bpm),
            "bpm_beats": int(self.bpm_beats),
            "output_gamma": dict(self.output_gamma or {}),
        }
        out.update(self._video_ext_dict())   #  R183 视频扩展（旧读取方忽略多余键）
        return out

    @classmethod
    def from_dict(cls, d: Dict) -> "MatrixSlideshowConfig":
        c = cls()
        c.group_id = d.get("group_id", "")
        c.group_name = d.get("group_name", "全部设备")
        c.ch_type = d.get("ch_type", CH_PUMP)
        c.play_mode = d.get("play_mode", PLAY_LOOP)
        c.global_on_value = int(d.get("global_on_value", -1))
        c.global_off_value = int(d.get("global_off_value", -1))
        #  R178 .get 带默认：旧工程 dict 无 output_shape 字段 → 默认（关闭）
        c.output_shape = normalize_output_shape_dict(d.get("output_shape"))
        c.channel_profiles = normalize_channel_profiles(d.get("channel_profiles"))
        #  BPM 量化 / 分区 Gamma：旧工程无字段 → 默认关闭（旧行为不变）
        c.bpm_enabled = bool(d.get("bpm_enabled", False))
        c.bpm = _safe_float(d.get("bpm", 120.0), default=120.0, lo=1.0, hi=600.0)
        c.bpm_beats = _safe_int(d.get("bpm_beats", 1), default=1, minimum=1, maximum=64)
        try:
            from .matrix_output_lut import normalize_zone_gamma_dict
            c.output_gamma = normalize_zone_gamma_dict(d.get("output_gamma"))
        except ImportError:
            c.output_gamma = {"enabled": False, "zone_count": 2,
                              "gammas": [1.0, 1.0]}
        c._load_video_ext(d)   #  R183 视频扩展（旧档缺键 = 默认旧行为）
        return c


# ─────────────────────────────────────────────────────────────────────────────
#  内置序列预设生成器
# ─────────────────────────────────────────────────────────────────────────────

#  内置序列名称表 — 供 UI 下拉框枚举（call site 用 BUILTIN_SEQUENCES.keys()）。
#   值为空字典占位，运行时由 BuiltinSequenceGenerator.generate(name, fixture_ids)
#   按当前设备数实时生成具体帧，所以 value 内容不重要。
#   这个常量原本被 matrix_slideshow_panel.py 第 47 行 import，但本文件未导出，
#   导致 MatrixSlideshowPanel 模块**整体导入失败**，连带 main_window 懒加载时
#   try/except 兜住 → dock 永远是空 placeholder → 用户看到一片空白。
BUILTIN_SEQUENCES: Dict[str, dict] = {
    # 跑泉类
    "正跑": {}, "反跑": {}, "对跑": {}, "增加跑": {}, "减少跑": {},
    "放射跑": {}, "收缩跑": {}, "波浪跑": {}, "双波浪": {},
    "山型跑": {}, "锯齿跑": {}, "三角跑": {},
    "矩形正跑": {}, "矩形反跑": {},
    "旗帜跑": {}, "向两边旗帜": {}, "向中间旗帜": {},
    "渐开": {}, "渐关": {}, "慢升快降": {},
    "间1跑": {}, "间2跑": {}, "间3跑": {},
    # 开合类
    "正开": {}, "反开": {}, "边开": {}, "中开": {},
    "全开全关": {}, "奇偶交替": {}, "三组轮换": {},
    # 特殊
    "随机闪烁": {}, "跑一次": {},
    # v25 新增 — 高级序列
    "螺旋跑": {}, "对角跑": {}, "Z字跑": {},
    "棋盘闪": {}, "十字展开": {}, "菱形展开": {},
    "百叶窗": {}, "像素雨": {}, "涟漪扩散": {},
    "心跳脉冲": {}, "呼吸渐变": {}, "随机填充": {},
    "蛇形跑": {}, "双螺旋": {}, "星形展开": {},
    # v30 新增 — 跑泉变式
    "正跑双": {}, "正跑三": {}, "反跑双": {},
    "正跑半": {}, "反跑半": {}, "对跑合": {},
    "中心放射": {}, "边缘收缩": {},
    "追逐跑": {}, "三点追逐": {}, "四点追逐": {},
    # v30 新增 — 波浪变式
    "三波浪": {}, "宽波浪": {}, "窄波浪": {},
    "反向波浪": {}, "双向波浪": {}, "呼吸波浪": {},
    # v30 新增 — 间隔变式
    "间4跑": {}, "间隔追逐": {}, "间隔渐开": {}, "间隔渐关": {},
    # v30 新增 — 渐变变式
    "反向渐开": {}, "反向渐关": {}, "中心渐开": {}, "边缘渐开": {},
    "快升慢降": {}, "交替渐变": {},
    # v30 新增 — 开合变式
    "正关": {}, "反关": {}, "边关": {}, "中关": {},
    "开合循环": {}, "对开对关": {},
    # v30 新增 — 特殊变式
    "四组轮换": {}, "五组轮换": {}, "六组轮换": {},
    "奇偶追逐": {}, "三组追逐": {},
    "随机闪烁2": {}, "随机单点": {}, "随机双点": {},
    "全闪": {}, "慢闪": {}, "节奏闪": {},
    # v30 新增 — 空间编组
    "前后交替": {}, "前后追逐": {},
    "三段轮流": {}, "四段轮流": {},
    "三段追逐": {}, "四段追逐": {},
    "前后渐开": {}, "分段闪烁": {},
    "分段渐亮": {}, "分段渐灭": {},
    # v30 新增 — 山型锯齿变式
    "双山型": {}, "锯齿双": {}, "锯齿反": {},
    "阶梯跑": {}, "反阶梯跑": {},
    # v30 新增 — 矩形旗帜变式
    "矩形闪": {}, "三旗帜": {}, "旗帜追逐": {}, "旗帜展开": {},
    # v30 新增 — 螺旋对角变式
    "螺旋反": {}, "螺旋宽": {}, "对角反": {}, "Z字反": {}, "螺旋开合": {},
    # v30 新增 — 涟漪心跳变式
    "涟漪反": {}, "涟漪全": {}, "双心跳": {},
    "呼吸慢": {}, "呼吸快": {}, "随机呼吸": {},
    # v30 新增 — 棋盘变式
    "棋盘追逐": {}, "棋盘渐开": {}, "棋盘波浪": {}, "棋盘闪2": {},
    # v30 新增 — 星形展开变式
    "星形收缩": {}, "星形闪": {}, "双星展开": {},
    # v30 新增 — 蛇形双螺旋变式
    "蛇形双": {}, "蛇形宽": {}, "双螺旋反": {}, "双螺旋宽": {},
    # v30 新增 — 随机填充变式
    "随机填充2": {}, "随机清除": {}, "随机爆炸": {},
}

SYMMETRIC_SPACE_PRESETS: List[str] = [
    "空间菱形环波", "空间双菱环波", "空间方框同心", "空间中心绽放",
    "空间嵌套边框", "空间旋转菱形", "空间十字镜像", "空间轴线门控",
    "空间X镜像波", "空间Y镜像波", "空间对角镜像", "空间反对角镜像",
    "空间沙漏对称", "空间分裂镜面", "空间相位镜面", "空间四象限同步",
    "空间四角回声", "空间四源干涉", "空间多瓣脉冲", "空间万花轴线",
    "空间曼陀罗瓣", "空间花冠波纹", "空间同心十字", "空间旋向条纹",
    "空间径向点阵", "空间菱形棋盘", "空间呼吸砖格", "空间旋转网格",
    "空间水平镜像波", "空间垂直镜像波", "空间双轴推拉", "空间内外交替",
    "空间六轴雪花", "空间八轴花瓣", "空间扇区镜像", "空间扇区追逐",
    "空间星芒扩散", "空间星芒收缩", "空间环形开合", "空间环形追逐",
    "空间边缘呼吸", "空间中心呼吸", "空间左右对拍", "空间上下对拍",
    "空间对角对拍", "空间棋盘镜像", "空间风车四叶", "空间风车八叶",
    "空间波纹叠影", "空间涟漪交叉", "空间窗花开合", "空间窗花追逐",
    "空间双心扩散", "空间双心收缩", "空间边角穿梭", "空间对称雨幕",
    "空间对称火花", "空间对称阶梯", "空间十字扫掠", "空间双环呼吸",
]

LATTICE_MORPH_PRESETS: List[str] = [
    "点阵倒V变三角", "点阵三角裂变", "点阵方块堆叠", "点阵阶梯漂移",
    "点阵俄罗斯I块", "点阵俄罗斯O块", "点阵俄罗斯T块", "点阵俄罗斯L块",
    "点阵俄罗斯J块", "点阵俄罗斯S块", "点阵俄罗斯Z块", "点阵斜坡堆积",
    "点阵折线行走", "点阵城墙起伏", "点阵塔形生长", "点阵碎片聚合",
    "点阵斜雨拼图", "点阵拼块旋转", "点阵缺口填充", "点阵蛇形拼接",
    "点阵山峰迁移", "点阵窗格变形", "点阵字形跳变", "点阵波块推进",
    "点阵双塔互换", "点阵三塔生长", "点阵阶梯折返", "点阵横条错位",
    "点阵竖条错位", "点阵斜条拼接", "点阵菱块漂移", "点阵十字拼块",
    "点阵圆环像素", "点阵边框游走", "点阵中心塌陷", "点阵中心聚合",
    "点阵棋块翻页", "点阵门形开合", "点阵桥形延展", "点阵箭头变阵",
    "点阵V形翻转", "点阵沙漏落点", "点阵瀑布堆积", "点阵火花归位",
    "点阵分子游走", "点阵蜂巢跳格", "点阵迷宫推进", "点阵锯齿幕布",
    "点阵双蛇交错", "点阵波峰堆叠", "点阵碎片爆开", "点阵碎片收拢",
    "点阵方阵旋页", "点阵四角拼图", "点阵回字变形", "点阵井字变形",
    "点阵雨滴扩散", "点阵岛屿漂移", "点阵月牙变形", "点阵皇冠起伏",
    "点阵城堡轮廓", "点阵脉冲拼块", "点阵折纸翻转", "点阵多米诺",
]

SPACE_PPT_PRESETS: List[str] = SYMMETRIC_SPACE_PRESETS + LATTICE_MORPH_PRESETS

ASYMMETRIC_PPT_PRESETS: List[str] = [
    "非对称斜切推进", "非对称单边扫线", "非对称右偏追逐", "非对称左偏追逐",
    "非对称错位阶梯", "非对称断点跑", "非对称蛇形缺口", "非对称斜门开合",
    "非对称偏心涟漪", "非对称边角流动", "非对称孤岛闪", "非对称交错窗格",
    "非对称折返走廊", "非对称斜雨扫", "非对称锯齿流", "非对称错拍波",
    "非对称跳格追逐", "非对称半场推进", "非对称偏心放射", "非对称偏心收缩",
    "非对称三角切片", "非对称斜向百叶", "非对称边缘绕行", "非对称中心偏移",
    "非对称双速追逐", "非对称碎片滑行", "非对称黑洞吸入", "非对称火花外溅",
    "非对称横向错层", "非对称纵向错层", "非对称波峰漂移", "非对称岛链跑",
    "非对称单翼展开", "非对称双翼错开", "非对称逆时针门", "非对称顺时针门",
    "非对称尾迹拖影", "非对称脉冲断续", "非对称扇形扫", "非对称镜外镜",
]

PARTICLE_PPT_PRESETS: List[str] = [
    "粒子喷射", "粒子回落", "粒子旋涡", "粒子吸附", "粒子爆裂",
    "粒子雨滴", "粒子火花", "粒子彗尾", "粒子弹跳", "粒子漂移",
    "粒子风场", "粒子潮汐", "粒子扩散云", "粒子聚合云", "粒子双涡",
    "粒子星尘", "粒子瀑布", "粒子喷泉弧", "粒子斜抛", "粒子回旋镖",
    "粒子磁场线", "粒子环绕", "粒子边界反弹", "粒子中线穿越", "粒子随机游走",
    "粒子阶跃跳", "粒子光点群", "粒子流沙", "粒子烟花层", "粒子雨幕左右",
    "粒子雨幕上下", "粒子双向喷射", "粒子脉冲群", "粒子星轨", "粒子虫洞",
    "粒子散点追逐", "粒子涟漪核", "粒子重力井", "粒子横风", "粒子纵风",
]

SHAPE_SEQUENCE_PRESETS: List[str] = [
    "形状圆到方", "形状方到菱", "形状三角展开", "形状十字收缩", "形状星芒旋转",
    "形状环形开口", "形状月牙漂移", "形状沙漏翻转", "形状箭头推进", "形状箭头回退",
    "形状门框开合", "形状窗格翻页", "形状皇冠起伏", "形状心形脉冲", "形状八瓣花",
    "形状四叶风车", "形状六边波", "形状井字裂变", "形状回字扩散", "形状S形游走",
    "形状Z形折返", "形状V形翻转", "形状X形扫", "形状双环互换", "形状中心空洞",
    "形状边框侵入", "形状斜切方块", "形状阶梯折叠", "形状多边变换", "形状波形边界",
    "形状碎片成形", "形状成形碎裂", "形状岛屿合并", "形状桥梁延展", "形状迷宫打开",
    "形状双塔升降", "形状山峰迁移", "形状瀑布轮廓", "形状皇冠闪", "形状星门",
]

RHYTHM_SEQUENCE_PRESETS: List[str] = [
    "节奏四拍跑", "节奏八拍跑", "节奏切分闪", "节奏反拍闪", "节奏重拍全开",
    "节奏弱拍半开", "节奏三连追逐", "节奏摆动跑", "节奏鼓点爆闪", "节奏低频脉冲",
    "节奏高频碎闪", "节奏停顿推进", "节奏快慢交替", "节奏一强三弱", "节奏二强二弱",
    "节奏问答跑", "节奏左右问答", "节奏前后问答", "节奏中心回应", "节奏边缘回应",
    "节奏拍点填充", "节奏拍点清空", "节奏上升句", "节奏下降句", "节奏循环句",
    "节奏断奏句", "节奏连奏句", "节奏同步开合", "节奏错拍开合", "节奏半场对答",
    "节奏四段对答", "节奏分组切片", "节奏闪烁尾迹", "节奏重音涟漪", "节奏静默爆发",
    "节奏渐密", "节奏渐疏", "节奏三段爆点", "节奏环形鼓点", "节奏舞台收束",
]

EXTENDED_PPT_PRESETS: List[str] = (
    ASYMMETRIC_PPT_PRESETS
    + PARTICLE_PPT_PRESETS
    + SHAPE_SEQUENCE_PRESETS
    + RHYTHM_SEQUENCE_PRESETS
)

BUILTIN_SEQUENCES.update({name: {} for name in SPACE_PPT_PRESETS + EXTENDED_PPT_PRESETS + list(DETAIL_SEQUENCE_GENERATORS)})


class BuiltinSequenceGenerator:
    """根据设备列表（已按位置排序）生成内置喷泉动作序列"""

    @staticmethod
    def _make_slide(name: str, on_ids: List[str], all_ids: List[str],
                    hold: float = 0.5, trans: float = 0.2,
                    trans_mode: str = TRANS_CUT) -> MatrixSlide:
        s = MatrixSlide(name=name, hold_time=hold,
                        transition_time=trans, transition_mode=trans_mode)
        on_set = set(on_ids)
        for fid in all_ids:
            s.states[fid] = fid in on_set
        return s

    @staticmethod
    def _remap_ids(
        fixture_ids: List[str],
        wings: int = 1,
        block_size: int = 1,
        wing_direction: str = WING_DIRECTION_LOW_TO_HIGH,
    ) -> List[str]:
        """
        根据 wings（翼数）和 block_size（块大小）对设备索引进行重新映射。

        block_size > 1：先按 block_size 分组，每组内设备共享同一个逻辑索引，
                        即 N 个设备作为一个单元同步运动。
        wings > 1：将设备列表分为 N 翼，每一翼都按同一方向复制第 0 翼的逻辑索引；
                   2 翼 = 两段完全一致的同向波形。
        """
        ids = list(fixture_ids)
        n = len(ids)
        if n == 0:
            return ids
        units = build_wing_block_units(
            ids,
            wings=max(1, int(wings or 1)),
            block_size=max(1, int(block_size or 1)),
            direction=wing_direction,
        )
        if not units:
            return ids
        remapped = list(ids)
        for unit in units:
            rep = unit[0]
            for fid in unit:
                try:
                    remapped[ids.index(fid)] = rep
                except ValueError:
                    pass
        return remapped

    @staticmethod
    def _grid_points(n: int) -> Tuple[int, int, List[Tuple[float, float, int, int]]]:
        """Infer a compact XY lattice from the current sorted fixture list."""
        cols = max(1, int(math.ceil(math.sqrt(max(1, n)))))
        rows = max(1, int(math.ceil(n / cols)))
        pts: List[Tuple[float, float, int, int]] = []
        for i in range(n):
            r = i // cols
            c = i % cols
            x = 0.0 if cols <= 1 else (c / (cols - 1)) * 2.0 - 1.0
            y = 0.0 if rows <= 1 else (r / (rows - 1)) * 2.0 - 1.0
            pts.append((x, y, r, c))
        return rows, cols, pts

    @staticmethod
    def _top_density_ids(fids: List[str], scores: List[float], density: float) -> List[str]:
        if not fids:
            return []
        d = max(0.02, min(1.0, float(density)))
        k = max(1, min(len(fids), int(round(len(fids) * d))))
        ranked = sorted(range(len(fids)), key=lambda i: (scores[i], -i), reverse=True)
        return [fids[i] for i in ranked[:k] if scores[i] > -999999.0]

    @staticmethod
    def _ring_score(value: float, center: float, width: float) -> float:
        return 1.0 - min(1.0, abs(value - center) / max(0.001, width))

    @classmethod
    def _generate_symmetric_space(cls, preset_name: str, fids: List[str], mk,
                                  hold: float, trans: float,
                                  density: float) -> List[MatrixSlide]:
        n = len(fids)
        if n == 0:
            return []
        _, _, pts = cls._grid_points(n)
        try:
            preset_idx = SYMMETRIC_SPACE_PRESETS.index(preset_name)
        except ValueError:
            preset_idx = 0
        family = preset_idx % 30
        frames = max(8, min(18, n + 4))
        slides: List[MatrixSlide] = []
        branches = 4 + (preset_idx % 5) * 2
        freq = 1.4 + (preset_idx % 4) * 0.45

        for step in range(frames):
            p = step / frames
            angle0 = p * math.tau
            scores: List[float] = []
            for x, y, r, c in pts:
                ax, ay = abs(x), abs(y)
                radius = min(1.414, math.hypot(x, y)) / 1.414
                manh = min(1.0, (ax + ay) / 2.0)
                cheb = max(ax, ay)
                theta = math.atan2(y, x)
                diag = abs(x - y) / 2.0
                anti = abs(x + y) / 2.0
                edge = max(ax, ay)
                corner = min(
                    math.hypot(x - 1.0, y - 1.0), math.hypot(x + 1.0, y - 1.0),
                    math.hypot(x - 1.0, y + 1.0), math.hypot(x + 1.0, y + 1.0)
                ) / 2.828

                if family == 0:
                    score = cls._ring_score((manh * freq - p) % 1.0, 0.0, 0.18)
                elif family == 1:
                    score = cls._ring_score((cheb * freq - p) % 1.0, 0.0, 0.16)
                elif family == 2:
                    score = cls._ring_score((radius * freq - p) % 1.0, 0.0, 0.17)
                elif family == 3:
                    score = max(cls._ring_score(ax, (p * 1.8) % 1.0, 0.12),
                                cls._ring_score(ay, (p * 1.8) % 1.0, 0.12))
                elif family == 4:
                    score = max(cls._ring_score(diag, (p * 1.5) % 1.0, 0.11),
                                cls._ring_score(anti, (p * 1.5) % 1.0, 0.11))
                elif family == 5:
                    score = 0.5 + 0.5 * math.sin((ax * freq + p) * math.tau)
                elif family == 6:
                    score = 0.5 + 0.5 * math.sin((ay * freq + p) * math.tau)
                elif family == 7:
                    score = 0.5 + 0.5 * math.sin(((ax + ay) * freq - p) * math.tau)
                elif family == 8:
                    score = cls._ring_score((corner * 2.2 - p) % 1.0, 0.0, 0.20)
                elif family == 9:
                    d1 = math.hypot(x - 1, y - 1)
                    d2 = math.hypot(x + 1, y + 1)
                    score = 0.5 + 0.25 * math.sin((d1 * freq - p) * math.tau) + 0.25 * math.sin((d2 * freq - p) * math.tau)
                elif family == 10:
                    score = (0.5 + 0.5 * math.cos(branches * theta - angle0)) * (0.35 + 0.65 * (1.0 - radius))
                elif family == 11:
                    score = abs(math.cos(branches * theta + angle0)) * (0.4 + 0.6 * math.sin((radius + p) * math.pi) ** 2)
                elif family == 12:
                    score = 1.0 - min(1.0, abs(math.sin((branches / 2) * theta - angle0)) / 0.35)
                elif family == 13:
                    score = 1.0 if ((r + c + step) % 2 == 0) else 0.25 * (1.0 - radius)
                elif family == 14:
                    score = 0.5 + 0.5 * math.sin((x * math.cos(angle0) + y * math.sin(angle0)) * math.pi * freq)
                elif family == 15:
                    score = cls._ring_score(abs(ax - ay), (p * 1.4) % 1.0, 0.13)
                elif family == 16:
                    score = max(cls._ring_score(ax, 0.0, 0.09 + 0.16 * p),
                                cls._ring_score(ay, 0.0, 0.09 + 0.16 * p))
                elif family == 17:
                    sector = abs(math.sin((branches / 2) * theta + angle0))
                    score = (1.0 - sector) * (0.45 + 0.55 * (1.0 - radius))
                elif family == 18:
                    score = max(cls._ring_score(radius, p, 0.16),
                                1.0 - min(1.0, abs(math.sin(branches * theta - angle0)) / 0.25))
                elif family == 19:
                    score = cls._ring_score(edge, 0.75 + 0.2 * math.sin(angle0), 0.15)
                elif family == 20:
                    score = cls._ring_score(radius, 0.15 + 0.55 * (0.5 + 0.5 * math.sin(angle0)), 0.18)
                elif family == 21:
                    score = 0.5 + 0.5 * math.sin((x * 1.7 + abs(y) * 0.8 - p) * math.tau)
                elif family == 22:
                    score = 0.5 + 0.5 * math.sin((y * 1.7 + abs(x) * 0.8 - p) * math.tau)
                elif family == 23:
                    score = 0.5 + 0.5 * math.sin(((x + y) * 1.2 + abs(x - y) * 0.6 - p) * math.tau)
                elif family == 24:
                    score = cls._ring_score((manh + radius + 0.35 * math.sin(theta * branches)) % 1.0, p, 0.16)
                elif family == 25:
                    score = 0.5 + 0.5 * math.sin((cheb * 1.8 + (1.0 - radius) * 0.8 - p) * math.tau)
                elif family == 26:
                    score = max(cls._ring_score(ax, p, 0.10), cls._ring_score(ay, 1.0 - p, 0.10))
                elif family == 27:
                    score = 0.5 + 0.5 * math.sin((abs(math.cos(theta - angle0)) * radius * freq) * math.tau)
                elif family == 28:
                    score = cls._ring_score((radius + corner) % 1.0, p, 0.18)
                else:
                    score = 0.5 + 0.5 * math.sin((manh * 1.5 + cheb * 0.8 - p) * math.tau)
                scores.append(score)

            mode = TRANS_FADE if step % 2 else TRANS_CUT
            slides.append(mk(f"{preset_name} {step + 1}", cls._top_density_ids(fids, scores, density), hold, trans, mode))
        return slides

    @staticmethod
    def _shape_cells(kind: str, frame: int, rows: int, cols: int) -> List[Tuple[int, int]]:
        cx = cols // 2
        cy = rows // 2
        span = max(1, min(rows, cols) // 3)
        cells: Set[Tuple[int, int]] = set()

        def add(rr: int, cc: int):
            if 0 <= rr < rows and 0 <= cc < cols:
                cells.add((rr, cc))

        if kind == "v_to_tri":
            if frame < 4:
                for d in range(span + 1):
                    add(cy - d, cx - d)
                    add(cy - d, cx + d)
            elif frame < 8:
                for d in range(span + 1):
                    for cc in range(cx - d, cx + d + 1):
                        add(cy - d, cc)
            else:
                for off in (-span, 0, span):
                    for d in range(max(1, span // 2) + 1):
                        for cc in range(cx + off - d, cx + off + d + 1):
                            add(cy - d, cc)
        elif kind == "tri_split":
            shift = frame % max(1, span + 1)
            for center in (cx - shift, cx + shift):
                for d in range(span + 1):
                    for cc in range(center - d, center + d + 1):
                        add(cy + d - span // 2, cc)
        elif kind == "stack":
            h = 1 + frame % max(1, rows)
            width = max(2, cols // 4)
            for rr in range(rows - h, rows):
                for cc in range((frame + rr) % max(1, cols - width + 1), (frame + rr) % max(1, cols - width + 1) + width):
                    add(rr, cc)
        elif kind == "stairs":
            for s in range(min(rows, cols)):
                height = 1 + ((s + frame) % max(1, rows // 2 + 1))
                for rr in range(rows - height, rows):
                    add(rr, (s + frame) % cols)
        elif kind.startswith("tet_"):
            shapes = {
                "tet_i": [(0, -1), (0, 0), (0, 1), (0, 2)],
                "tet_o": [(0, 0), (0, 1), (1, 0), (1, 1)],
                "tet_t": [(0, -1), (0, 0), (0, 1), (1, 0)],
                "tet_l": [(0, -1), (0, 0), (0, 1), (1, 1)],
                "tet_j": [(0, -1), (0, 0), (0, 1), (1, -1)],
                "tet_s": [(0, 0), (0, 1), (1, -1), (1, 0)],
                "tet_z": [(0, -1), (0, 0), (1, 0), (1, 1)],
            }
            base = shapes.get(kind, shapes["tet_t"])
            ox = frame % max(1, cols)
            oy = (frame // max(1, cols)) % max(1, rows)
            rot = (frame // 3) % 4
            for rr, cc in base:
                r0, c0 = rr, cc
                for _ in range(rot):
                    r0, c0 = c0, -r0
                add((oy + cy // 2 + r0) % rows, (ox + c0) % cols)
        elif kind == "zigzag":
            for cc in range(cols):
                rr = (frame + (cc % 4 if (cc // 4) % 2 == 0 else 3 - cc % 4)) % rows
                add(rr, cc)
                add((rr + 1) % rows, cc)
        elif kind == "wall":
            for rr in range(rows):
                offset = (rr + frame) % 3
                for cc in range(offset, cols, 4):
                    add(rr, cc)
                    add(rr, cc + 1)
        elif kind == "tower":
            levels = 1 + frame % max(1, rows)
            for i in range(levels):
                width = max(1, min(cols, 1 + (i % 4) * 2))
                rr = rows - 1 - i
                for cc in range(cx - width // 2, cx + width // 2 + 1):
                    add(rr, cc)
        elif kind == "fragments":
            for i in range(max(4, (rows * cols) // 8)):
                rr = (i * 3 + frame) % rows
                cc = (i * 5 + frame * 2) % cols
                add(rr, cc)
                if frame % 3:
                    add((rr + 1) % rows, cc)
        elif kind == "rain":
            for cc in range(0, cols, 2):
                rr = (frame + cc // 2) % rows
                add(rr, cc)
                add((rr - 1) % rows, cc)
        elif kind == "rotate_blocks":
            centers = [(rows // 3, cols // 3), (rows // 3, cols * 2 // 3), (rows * 2 // 3, cols // 2)]
            for k, (rr0, cc0) in enumerate(centers):
                rot = (frame + k) % 4
                for rr, cc in [(0, 0), (0, 1), (1, 0)]:
                    r0, c0 = rr, cc
                    for _ in range(rot):
                        r0, c0 = c0, -r0
                    add(rr0 + r0, cc0 + c0)
        elif kind == "fill_gap":
            for rr in range(rows):
                for cc in range(cols):
                    if abs(cc - cx) > (frame % max(1, cols // 2 + 1)):
                        add(rr, cc)
        elif kind == "snake":
            length = max(3, (rows * cols) // 5)
            start = (frame * 2) % max(1, rows * cols)
            for j in range(length):
                idx = (start + j) % (rows * cols)
                rr = idx // cols
                cc = idx % cols if rr % 2 == 0 else cols - 1 - (idx % cols)
                add(rr, cc)
        elif kind == "mountain":
            peak = frame % cols
            for cc in range(cols):
                h = max(1, span + 1 - abs(cc - peak))
                for rr in range(rows - h, rows):
                    add(rr, cc)
        elif kind == "window":
            gap = 1 + frame % max(1, min(rows, cols) // 2 + 1)
            for rr in range(0, rows, gap + 1):
                for cc in range(cols):
                    add(rr, cc)
            for cc in range(0, cols, gap + 1):
                for rr in range(rows):
                    add(rr, cc)
        elif kind == "glyph":
            for rr in range(rows):
                for cc in range(cols):
                    if ((rr + frame) % 4 == 0) or ((cc - frame) % 5 == 0 and rr > rows // 3):
                        add(rr, cc)
        elif kind == "twin_towers":
            h = 1 + frame % max(1, rows)
            for base in (cols // 4, cols * 3 // 4):
                for i in range(h):
                    width = 1 + ((i + frame) % 3)
                    for cc in range(base - width, base + width + 1):
                        add(rows - 1 - i, cc)
        elif kind == "triple_towers":
            for k, base in enumerate((cols // 5, cols // 2, cols * 4 // 5)):
                h = 1 + ((frame + k * 2) % max(1, rows))
                for i in range(h):
                    for cc in range(base - (i % 2), base + (i % 2) + 1):
                        add(rows - 1 - i, cc)
        elif kind == "stairs_bounce":
            for cc in range(cols):
                h = 1 + abs(((cc + frame) % max(2, rows * 2 - 2)) - (rows - 1))
                for rr in range(rows - h, rows):
                    add(rr, cc)
        elif kind == "h_bars":
            for rr in range(0, rows, 2):
                shift = (frame + rr) % max(1, cols)
                for j in range(max(2, cols // 3)):
                    add(rr, (shift + j) % cols)
        elif kind == "v_bars":
            for cc in range(0, cols, 2):
                shift = (frame + cc) % max(1, rows)
                for j in range(max(2, rows // 3)):
                    add((shift + j) % rows, cc)
        elif kind == "diagonal_bars":
            for rr in range(rows):
                for w in range(2):
                    add(rr, (rr + frame + w * 2) % cols)
                    add(rr, (cols - 1 - rr - frame + w * 2) % cols)
        elif kind == "diamond_blocks":
            centers = [(cy, (frame + cols // 4) % cols), (cy // 2, (cols - frame - 1) % cols)]
            for rr0, cc0 in centers:
                for rr in range(rows):
                    for cc in range(cols):
                        if abs(rr - rr0) + abs(cc - cc0) <= max(1, span // 2):
                            add(rr, cc)
        elif kind == "cross_blocks":
            arm = 1 + frame % max(1, span + 1)
            for d in range(-arm, arm + 1):
                add(cy, cx + d)
                add(cy + d, cx)
            for d in range(-max(1, arm // 2), max(1, arm // 2) + 1):
                add(cy + d, (cx + frame) % cols)
                add((cy + frame) % rows, cx + d)
        elif kind == "pixel_ring":
            rad = 1 + frame % max(1, min(rows, cols) // 2 + 1)
            for rr in range(rows):
                for cc in range(cols):
                    d = max(abs(rr - cy), abs(cc - cx))
                    if abs(d - rad) <= 1:
                        add(rr, cc)
        elif kind == "border_walk":
            perimeter = max(1, rows * 2 + cols * 2 - 4)
            length = max(3, perimeter // 5)
            for j in range(length):
                p = (frame * 2 + j) % perimeter
                if p < cols:
                    add(0, p)
                elif p < cols + rows - 1:
                    add(p - cols + 1, cols - 1)
                elif p < cols * 2 + rows - 2:
                    add(rows - 1, cols - 2 - (p - cols - rows + 1))
                else:
                    add(rows - 2 - (p - cols * 2 - rows + 2), 0)
        elif kind == "collapse":
            rad = max(0, min(rows, cols) // 2 - frame % max(1, min(rows, cols) // 2 + 1))
            for rr in range(rows):
                for cc in range(cols):
                    if abs(rr - cy) + abs(cc - cx) <= rad:
                        add(rr, cc)
        elif kind == "gather":
            rad = frame % max(1, min(rows, cols) // 2 + 1)
            for rr in range(rows):
                for cc in range(cols):
                    if abs(rr - cy) + abs(cc - cx) <= rad:
                        add(rr, cc)
        elif kind == "checker_page":
            page = frame % max(1, cols)
            for rr in range(rows):
                for cc in range(cols):
                    if (rr + cc + frame) % 2 == 0 and cc <= page:
                        add(rr, cc)
        elif kind == "gate":
            gap = frame % max(1, cols // 2 + 1)
            for rr in range(rows):
                for cc in list(range(0, gap + 1)) + list(range(cols - gap - 1, cols)):
                    add(rr, cc)
        elif kind == "bridge":
            y = cy + int(math.sin(frame * 0.6) * max(1, rows // 4))
            for cc in range(cols):
                add(y, cc)
                if abs(cc - cx) < frame % max(2, cols // 2 + 1):
                    add(y - 1, cc)
                    add(y + 1, cc)
        elif kind == "arrow":
            head = frame % cols
            for cc in range(cols):
                add(cy, cc)
            for d in range(span + 1):
                add(cy - d, head - d)
                add(cy + d, head - d)
        elif kind == "v_flip":
            sign = -1 if frame % 8 < 4 else 1
            for d in range(span + 1):
                add(cy + sign * d, cx - d)
                add(cy + sign * d, cx + d)
        elif kind == "hourglass":
            level = frame % max(1, rows)
            for rr in range(rows):
                width = abs(rr - cy)
                for cc in range(cx - width, cx + width + 1):
                    if rr <= level:
                        add(rr, cc)
        elif kind == "waterfall":
            for cc in range(cols):
                h = 1 + ((frame + cc * 2) % max(1, rows))
                for rr in range(h):
                    add(rr, cc)
        elif kind == "sparks_home":
            for i in range(max(4, rows * cols // 7)):
                rr = int((i * 7 + frame * (i % 3 + 1)) % rows)
                cc = int((i * 11 - frame * (i % 4 + 1)) % cols)
                add(rr, cc)
                if frame % 5 > 2:
                    add(cy + (rr - cy) // 2, cx + (cc - cx) // 2)
        elif kind == "molecule":
            for k in range(5):
                rr0 = int((cy + math.sin(frame * 0.4 + k) * rows * 0.35) % rows)
                cc0 = int((cx + math.cos(frame * 0.5 + k * 1.7) * cols * 0.35) % cols)
                add(rr0, cc0)
                add(rr0 + 1, cc0)
                add(rr0, cc0 + 1)
        elif kind == "honeycomb":
            for rr in range(rows):
                for cc in range(cols):
                    if (rr * 2 + cc + frame) % 5 in (0, 1):
                        add(rr, cc)
        elif kind == "maze":
            frontier = frame % max(1, rows + cols)
            for rr in range(rows):
                for cc in range(cols):
                    if (rr + cc <= frontier and (rr % 2 == 0 or cc % 3 == 0)):
                        add(rr, cc)
        elif kind == "zigzag_curtain":
            for rr in range(rows):
                width = (frame + rr) % max(1, cols)
                for cc in range(width):
                    add(rr, cc if rr % 2 == 0 else cols - 1 - cc)
        elif kind == "double_snake":
            length = max(4, rows * cols // 6)
            for base in (frame * 2, rows * cols - frame * 2):
                for j in range(length):
                    idx = (base + j) % (rows * cols)
                    rr = idx // cols
                    cc = idx % cols if rr % 2 == 0 else cols - 1 - (idx % cols)
                    add(rr, cc)
        elif kind == "wave_peaks":
            for cc in range(cols):
                h = int((math.sin((cc + frame) * 0.7) * 0.5 + 0.5) * (rows - 1)) + 1
                for rr in range(rows - h, rows):
                    add(rr, cc)
        elif kind == "explode":
            rad = frame % max(1, rows + cols)
            for rr in range(rows):
                for cc in range(cols):
                    d = abs(rr - cy) + abs(cc - cx)
                    if abs(d - rad) <= 1 or (d < rad and frame % 3 == 0):
                        add(rr, cc)
        elif kind == "implode":
            rad = max(0, rows + cols - frame)
            for rr in range(rows):
                for cc in range(cols):
                    d = abs(rr - cy) + abs(cc - cx)
                    if abs(d - rad) <= 1 or d < max(1, frame // 3):
                        add(rr, cc)
        elif kind == "page_rotate":
            split = frame % max(1, cols)
            for rr in range(rows):
                for cc in range(cols):
                    if (cc < split and rr % 2 == 0) or (cc >= split and rr % 2 == 1):
                        add(rr, cc)
        elif kind == "corner_puzzle":
            grow = frame % max(1, min(rows, cols) // 2 + 1)
            for rr0, cc0 in ((0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1)):
                for rr in range(rows):
                    for cc in range(cols):
                        if abs(rr - rr0) + abs(cc - cc0) <= grow:
                            add(rr, cc)
        elif kind == "square_morph":
            outer = frame % max(1, min(rows, cols) // 2 + 1)
            for rr in range(cy - outer, cy + outer + 1):
                add(rr, cx - outer)
                add(rr, cx + outer)
            for cc in range(cx - outer, cx + outer + 1):
                add(cy - outer, cc)
                add(cy + outer, cc)
        elif kind == "hash_morph":
            gap = 2 + frame % 4
            for rr in range(rows):
                add(rr, cols // 3)
                add(rr, cols * 2 // 3)
            for cc in range(cols):
                add(rows // 3 + (frame % gap == 0), cc)
                add(rows * 2 // 3 - (frame % gap == 0), cc)
        elif kind == "droplet":
            for rr in range(rows):
                for cc in range(cols):
                    d = math.hypot((rr - cy) / max(1, rows), (cc - cx) / max(1, cols))
                    if abs(d * 8 - frame % 8) < 0.8 and rr >= cy - frame % max(1, rows):
                        add(rr, cc)
        elif kind == "islands":
            for k in range(4):
                rr0 = int((rows * (k + 1) / 5 + math.sin(frame * 0.4 + k) * 2) % rows)
                cc0 = int((cols * (k + 1) / 5 + math.cos(frame * 0.3 + k) * 3) % cols)
                for rr in range(rows):
                    for cc in range(cols):
                        if abs(rr - rr0) + abs(cc - cc0) <= 2:
                            add(rr, cc)
        elif kind == "crescent":
            rad = max(2, min(rows, cols) // 3)
            shift = int(math.sin(frame * 0.5) * max(1, cols // 5))
            for rr in range(rows):
                for cc in range(cols):
                    d1 = math.hypot(rr - cy, cc - cx)
                    d2 = math.hypot(rr - cy, cc - (cx + shift))
                    if d1 <= rad and d2 > rad * 0.7:
                        add(rr, cc)
        elif kind == "crown":
            for cc in range(cols):
                h = 1 + (span if cc in (cols // 4, cols // 2, cols * 3 // 4) else (cc + frame) % max(1, span))
                for rr in range(rows - h, rows):
                    add(rr, cc)
        elif kind == "castle":
            for cc in range(cols):
                battlement = 2 if (cc + frame) % 4 < 2 else 0
                h = rows // 3 + battlement + (2 if cc in (0, cols - 1, cols // 2) else 0)
                for rr in range(rows - h, rows):
                    add(rr, cc)
        elif kind == "pulse_blocks":
            rad = 1 + frame % max(1, min(rows, cols) // 3 + 1)
            for rr0, cc0 in ((cy, cx), (rows // 4, cols // 4), (rows * 3 // 4, cols * 3 // 4)):
                for rr in range(rr0 - rad, rr0 + rad + 1):
                    for cc in range(cc0 - rad, cc0 + rad + 1):
                        add(rr, cc)
        elif kind == "origami":
            fold = frame % max(1, cols)
            for rr in range(rows):
                for cc in range(cols):
                    if (cc < fold and (rr + cc) % 2 == 0) or (cc >= fold and (rr - cc) % 3 == 0):
                        add(rr, cc)
        elif kind == "domino":
            for k in range(frame % max(1, rows * cols)):
                rr = k // cols
                cc = k % cols if rr % 2 == 0 else cols - 1 - (k % cols)
                add(rr, cc)
        else:
            width = max(2, cols // 3)
            left = frame % max(1, cols)
            for rr in range(rows):
                for j in range(width):
                    add(rr, (left + j + rr // 2) % cols)
        return list(cells)

    @classmethod
    def _generate_lattice_morph(cls, preset_name: str, fids: List[str], mk,
                                hold: float, trans: float,
                                density: float) -> List[MatrixSlide]:
        n = len(fids)
        if n == 0:
            return []
        rows, cols, pts = cls._grid_points(n)
        kinds = [
            "v_to_tri", "tri_split", "stack", "stairs",
            "tet_i", "tet_o", "tet_t", "tet_l", "tet_j", "tet_s", "tet_z",
            "stack", "zigzag", "wall", "tower", "fragments", "rain",
            "rotate_blocks", "fill_gap", "snake", "mountain", "window",
            "glyph", "wave_blocks",
            "twin_towers", "triple_towers", "stairs_bounce", "h_bars",
            "v_bars", "diagonal_bars", "diamond_blocks", "cross_blocks",
            "pixel_ring", "border_walk", "collapse", "gather",
            "checker_page", "gate", "bridge", "arrow", "v_flip",
            "hourglass", "waterfall", "sparks_home", "molecule",
            "honeycomb", "maze", "zigzag_curtain", "double_snake",
            "wave_peaks", "explode", "implode", "page_rotate",
            "corner_puzzle", "square_morph", "hash_morph", "droplet",
            "islands", "crescent", "crown", "castle", "pulse_blocks",
            "origami", "domino",
        ]
        try:
            preset_idx = LATTICE_MORPH_PRESETS.index(preset_name)
        except ValueError:
            preset_idx = 0
        kind = kinds[preset_idx % len(kinds)]
        frames = max(10, min(22, n + 6 + preset_idx % 5))
        slides: List[MatrixSlide] = []
        for frame in range(frames):
            phase_frame = frame + preset_idx * 3
            cells = set(cls._shape_cells(kind, phase_frame, rows, cols))
            scores: List[float] = []
            for _x, _y, rr, cc in pts:
                if (rr, cc) in cells:
                    score = 1.0
                else:
                    dist = min((abs(rr - sr) + abs(cc - sc)) for sr, sc in cells) if cells else rows + cols
                    score = max(0.0, 0.75 - dist * (0.18 + 0.02 * (preset_idx % 4)))
                if preset_idx % 3 == 1:
                    score += 0.12 * math.sin((rr + phase_frame) * 0.9)
                elif preset_idx % 3 == 2:
                    score += 0.12 * math.cos((cc - phase_frame) * 0.8)
                scores.append(score)
            slides.append(mk(f"{preset_name} {frame + 1}", cls._top_density_ids(fids, scores, density), hold, trans, TRANS_CUT))
        return slides

    @classmethod
    def _generate_extended_ppt(cls, preset_name: str, fids: List[str], mk,
                               hold: float, trans: float,
                               density: float) -> List[MatrixSlide]:
        """Generate newer Matrix PPT presets from spatial scoring patterns.

        These presets intentionally share one generator so every newly listed
        preset is loadable for any fixture count while still producing distinct
        movement by category/name index.
        """
        n = len(fids)
        if n == 0:
            return []
        rows, cols, pts = cls._grid_points(n)
        frames = max(8, min(22, n + 6))
        slides: List[MatrixSlide] = []

        if preset_name in ASYMMETRIC_PPT_PRESETS:
            preset_idx = ASYMMETRIC_PPT_PRESETS.index(preset_name)
            family = preset_idx % 20
            for step in range(frames):
                p = step / frames
                angle = p * math.tau
                scores: List[float] = []
                for x, y, r, c in pts:
                    ax, ay = abs(x), abs(y)
                    radius = min(1.414, math.hypot(x, y)) / 1.414
                    theta = math.atan2(y, x)
                    skew = x * (0.65 + 0.2 * (preset_idx % 3)) + y * (0.35 + 0.15 * ((preset_idx + 1) % 3))
                    offset_x = x - (0.45 * math.sin(angle + preset_idx * 0.37))
                    offset_y = y - (0.35 * math.cos(angle * 0.7 + preset_idx * 0.23))
                    dist = min(1.414, math.hypot(offset_x, offset_y)) / 1.414
                    if family == 0:
                        score = cls._ring_score((skew + 1.5) / 3.0, p, 0.12)
                    elif family == 1:
                        score = cls._ring_score((x + 1.0) / 2.0, p, 0.10) * (0.35 + 0.65 * (1.0 - ay))
                    elif family == 2:
                        score = cls._ring_score((x * 0.8 + y * 0.4 + 1.2) / 2.4, p, 0.14)
                    elif family == 3:
                        score = cls._ring_score((y * 0.8 - x * 0.35 + 1.2) / 2.4, p, 0.14)
                    elif family == 4:
                        score = 1.0 if ((r + step) % max(2, 3 + preset_idx % 4) == 0) else 0.2 * (1.0 - radius)
                    elif family == 5:
                        score = 1.0 if ((c * 2 + r + step) % max(3, 4 + preset_idx % 5) == 0) else 0.1
                    elif family == 6:
                        score = max(cls._ring_score(radius, (p * 1.4) % 1.0, 0.12),
                                    cls._ring_score((theta / math.tau + 1.0) % 1.0, p, 0.08))
                    elif family == 7:
                        score = cls._ring_score(dist, 0.15 + 0.65 * p, 0.16)
                    elif family == 8:
                        score = cls._ring_score(dist, 0.75 - 0.55 * p, 0.15)
                    elif family == 9:
                        score = cls._ring_score(min(abs(x - 1.0), abs(y + 1.0)), (p * 1.3) % 1.0, 0.13)
                    elif family == 10:
                        score = 0.5 + 0.5 * math.sin((x * 2.1 + y * 0.7 - p * 1.8) * math.tau)
                    elif family == 11:
                        score = 0.5 + 0.5 * math.sin((y * 2.1 - x * 0.7 + p * 1.8) * math.tau)
                    elif family == 12:
                        score = max(cls._ring_score(abs(x - y), (p * 1.4) % 1.0, 0.12),
                                    cls._ring_score(abs(x + y), (1.0 - p) % 1.0, 0.12))
                    elif family == 13:
                        score = (0.55 + 0.45 * math.sin((radius * 2.2 - p * 1.6) * math.tau)) * (0.45 + 0.55 * (1.0 - max(ax, ay)))
                    elif family == 14:
                        score = cls._ring_score((math.atan2(y + 0.25, x - 0.35) / math.tau + 1.0) % 1.0, p, 0.10)
                    elif family == 15:
                        score = cls._ring_score((math.atan2(y - 0.25, x + 0.35) / math.tau + 1.0) % 1.0, 1.0 - p, 0.10)
                    elif family == 16:
                        score = max(0.0, 1.0 - dist * 1.4) + 0.4 * cls._ring_score(radius, p, 0.13)
                    elif family == 17:
                        score = 0.5 + 0.5 * math.sin((abs(x) * 1.3 + y * 0.9 + step * 0.17) * math.tau)
                    elif family == 18:
                        score = cls._ring_score((theta / math.tau + 1.0 + radius * 0.35) % 1.0, p, 0.12)
                    else:
                        score = max(cls._ring_score((x + 1.0) / 2.0, p, 0.10),
                                    cls._ring_score((y + 1.0) / 2.0, (p * 1.7) % 1.0, 0.08))
                    scores.append(score)
                mode = TRANS_WIPE_LR if family in {0, 1, 2, 3} else (TRANS_FADE if step % 2 else TRANS_CUT)
                slides.append(mk(f"{preset_name} {step + 1}", cls._top_density_ids(fids, scores, density), hold, trans, mode))
            return slides

        if preset_name in PARTICLE_PPT_PRESETS:
            preset_idx = PARTICLE_PPT_PRESETS.index(preset_name)
            family = preset_idx % 20
            particle_count = 2 + (preset_idx % 4)
            for step in range(frames):
                p = step / frames
                centers = []
                for j in range(particle_count):
                    phase = p + j / max(1, particle_count) + preset_idx * 0.017
                    if family in {0, 1, 5, 6, 17, 18}:
                        cx = -1.15 + 2.3 * ((phase * (0.7 + 0.1 * (j % 3))) % 1.0)
                        cy = -0.75 + 1.5 * (0.5 + 0.5 * math.sin((phase + j * 0.21) * math.tau))
                    elif family in {2, 4, 14, 19}:
                        rr = 0.25 + 0.65 * ((phase * 1.3) % 1.0)
                        cx = math.cos(phase * math.tau) * rr
                        cy = math.sin(phase * math.tau) * rr
                    elif family in {7, 8, 10, 11}:
                        cx = -0.8 + j * (1.6 / max(1, particle_count - 1))
                        cy = 1.2 - 2.4 * ((phase * (0.9 + 0.05 * j)) % 1.0)
                    else:
                        cx = math.sin((phase * 1.7 + j * 0.31) * math.tau) * 0.9
                        cy = math.cos((phase * 1.1 + j * 0.19) * math.tau) * 0.9
                    centers.append((cx, cy))
                scores = []
                for x, y, _r, _c in pts:
                    best = 0.0
                    for j, (cx, cy) in enumerate(centers):
                        d = math.hypot(x - cx, y - cy)
                        trail = max(0.0, 1.0 - d / (0.23 + 0.03 * (preset_idx % 4)))
                        if family in {3, 9, 12, 16}:
                            trail += 0.35 * max(0.0, 1.0 - abs((x - cx) * math.sin(j + 1) + (y - cy) * math.cos(j + 1)) / 0.22)
                        if family in {4, 13, 18}:
                            trail += cls._ring_score(d, 0.18 + 0.34 * ((step + j) % frames) / frames, 0.08)
                        best = max(best, trail)
                    if family in {6, 15}:
                        best *= 0.55 + 0.45 * math.sin((x + y + p) * math.tau) ** 2
                    scores.append(best)
                slides.append(mk(f"{preset_name} {step + 1}", cls._top_density_ids(fids, scores, density), hold * 0.8, trans * 0.8, TRANS_CUT))
            return slides

        if preset_name in SHAPE_SEQUENCE_PRESETS:
            preset_idx = SHAPE_SEQUENCE_PRESETS.index(preset_name)
            family = preset_idx % 20
            for step in range(frames):
                p = step / frames
                angle = p * math.tau
                scores = []
                for x, y, _r, _c in pts:
                    ax, ay = abs(x), abs(y)
                    radius = min(1.414, math.hypot(x, y)) / 1.414
                    theta = math.atan2(y, x)
                    diamond = (ax + ay) / 2.0
                    square = max(ax, ay)
                    if family == 0:
                        target = (1.0 - p) * radius + p * square
                        score = cls._ring_score(target, 0.45, 0.13)
                    elif family == 1:
                        target = (1.0 - p) * square + p * diamond
                        score = cls._ring_score(target, 0.52, 0.12)
                    elif family == 2:
                        edge = abs(y - (0.75 - 1.5 * abs(x))) / 2.0
                        score = cls._ring_score(edge, 0.0, 0.08 + 0.06 * p)
                    elif family == 3:
                        score = max(cls._ring_score(ax, 0.0, 0.08 + 0.22 * (1.0 - p)),
                                    cls._ring_score(ay, 0.0, 0.08 + 0.22 * (1.0 - p)))
                    elif family == 4:
                        score = abs(math.cos(5 * theta - angle)) * (0.35 + 0.65 * (1.0 - radius))
                    elif family == 5:
                        score = cls._ring_score(radius, 0.55, 0.11) * (1.0 - cls._ring_score(theta / math.tau % 1.0, p, 0.06))
                    elif family == 6:
                        score = cls._ring_score(abs(radius - (0.42 + 0.18 * math.sin(angle))), 0.0, 0.10)
                    elif family == 7:
                        score = max(cls._ring_score(abs(x - y), p, 0.10), cls._ring_score(abs(x + y), 1.0 - p, 0.10))
                    elif family == 8:
                        score = max(cls._ring_score(y, 0.0, 0.12), cls._ring_score((x + 1.0) / 2.0, p, 0.10))
                    elif family == 9:
                        score = max(cls._ring_score(y, 0.0, 0.12), cls._ring_score((x + 1.0) / 2.0, 1.0 - p, 0.10))
                    elif family == 10:
                        score = cls._ring_score(square, 0.7 - 0.45 * math.sin(angle) ** 2, 0.10)
                    elif family == 11:
                        score = 1.0 if ((int((x + 1) * cols / 2) + step) % 4 == 0 or (int((y + 1) * rows / 2) - step) % 5 == 0) else 0.0
                    elif family == 12:
                        score = cls._ring_score(y, -0.2 + 0.35 * math.sin(angle * 2), 0.10) + cls._ring_score(abs(x), 0.45, 0.10)
                    elif family == 13:
                        heart = (x * x + (y + 0.15) * (y + 0.15) - 0.55) ** 3 - x * x * (y + 0.15) ** 3
                        score = cls._ring_score(abs(heart), 0.0, 0.22 + 0.12 * math.sin(angle) ** 2)
                    elif family == 14:
                        score = abs(math.cos(8 * theta + angle)) * cls._ring_score(radius, 0.45, 0.24)
                    elif family == 15:
                        score = abs(math.sin(4 * theta - angle)) * (0.5 + 0.5 * cls._ring_score(radius, 0.55, 0.24))
                    elif family == 16:
                        score = cls._ring_score(abs(math.sin(3 * theta) * radius), p * 0.7, 0.12)
                    elif family == 17:
                        score = max(cls._ring_score(abs(x), p * 0.9, 0.08), cls._ring_score(abs(y), (1.0 - p) * 0.9, 0.08))
                    elif family == 18:
                        score = cls._ring_score(square, p, 0.12) + cls._ring_score(diamond, 1.0 - p, 0.12)
                    else:
                        score = 0.5 + 0.5 * math.sin((radius + 0.3 * math.sin(theta * 6 + angle) - p) * math.tau)
                    scores.append(score)
                slides.append(mk(f"{preset_name} {step + 1}", cls._top_density_ids(fids, scores, density), hold, trans, TRANS_FADE if step % 3 == 0 else TRANS_CUT))
            return slides

        if preset_name in RHYTHM_SEQUENCE_PRESETS:
            preset_idx = RHYTHM_SEQUENCE_PRESETS.index(preset_name)
            beats = 8 if preset_idx % 2 else 4
            frames_r = max(8, beats * 2)
            for step in range(frames_r):
                beat = step % beats
                density_mod = density
                if preset_idx % 5 == 0:
                    density_mod = min(1.0, density * (1.2 if beat == 0 else 0.55))
                elif preset_idx % 5 == 1:
                    density_mod = min(1.0, density * (0.75 + 0.35 * (beat % 2)))
                elif preset_idx % 5 == 2:
                    density_mod = min(1.0, density * (0.45 + beat / max(1, beats - 1)))
                elif preset_idx % 5 == 3:
                    density_mod = min(1.0, density * (1.2 - beat / max(1, beats - 1) * 0.75))
                scores = []
                for i, (_x, _y, r, c) in enumerate(pts):
                    group = (i + preset_idx) % max(2, min(8, beats))
                    pulse = 1.0 if group == beat % max(2, min(8, beats)) else 0.0
                    if preset_idx % 4 == 1:
                        pulse = 1.0 if (r + beat) % 2 == 0 else 0.1
                    elif preset_idx % 4 == 2:
                        pulse = 1.0 if (c - beat) % 3 == 0 else 0.05
                    elif preset_idx % 4 == 3:
                        pulse = max(
                            cls._ring_score((i / max(1, n - 1)), beat / max(1, beats - 1), 0.10),
                            cls._ring_score((1.0 - i / max(1, n - 1)), beat / max(1, beats - 1), 0.10),
                        )
                    scores.append(pulse)
                tm = TRANS_CUT if beat % 2 == 0 else TRANS_FADE
                slides.append(mk(f"{preset_name} {step + 1}", cls._top_density_ids(fids, scores, density_mod), hold * 0.65, trans * 0.7, tm))
            return slides

        return []

    @classmethod
    @transform_generated
    def generate(cls, preset_name: str, fixture_ids: List[str],
                 hold: float = 0.5, trans: float = 0.2,
                 wings: int = 1, block_size: int = 1,
                 wing_direction: str = WING_DIRECTION_LOW_TO_HIGH,
                 density: float = 0.35, *, sequence_options: dict | None = None) -> List[MatrixSlide]:
        """
        fixture_ids: 已按 TopView 位置排序的设备 ID 列表（从左到右/从上到下）
        wings: 翼数（1~8），控制同形波形复制数量；2 翼表示两段完全一致的同向波形
        block_size: 块大小（1~16），N 个设备作为一个单元同步运动
        """
        n = len(fixture_ids)
        if n == 0:
            return []

        # 应用翼/块重映射：fids 是重映射后的逻辑顺序（用于 on_ids）
        # fixture_ids 是原始列表（用于 all_ids，确保 states 覆盖全部设备）
        fids = cls._remap_ids(
            fixture_ids,
            wings=wings,
            block_size=block_size,
            wing_direction=wing_direction,
        )

        logic_groups: Dict[str, List[str]] = {}
        for logical_id, physical_id in zip(fids, fixture_ids):
            logic_groups.setdefault(logical_id, []).append(physical_id)

        def _expand_logical_ids(on_ids) -> List[str]:
            expanded: List[str] = []
            seen = set()
            for oid in list(on_ids or []):
                for physical_id in logic_groups.get(oid, [oid]):
                    if physical_id not in seen:
                        expanded.append(physical_id)
                        seen.add(physical_id)
            return expanded

        # 局部包装：固定 all_ids=fixture_ids，各分支只传逻辑 on_ids；这里展开为真实设备。
        # 兼容旧分支签名：mk(name, on_ids, all_ids, hold, trans, mode)，其中 all_ids
        # 现在应由 fixture_ids 统一提供；也兼容 mk("全开", hold, trans) 这类旧写法。
        def mk(name: str, on_ids=None, *args) -> "MatrixSlide":
            h = hold
            t = trans
            tm = TRANS_CUT
            if on_ids is None:
                on_ids = []
            if isinstance(on_ids, (int, float)):
                h = float(on_ids)
                if len(args) >= 1:
                    t = float(args[0])
                if len(args) >= 2:
                    tm = args[1]
                on_ids = fixture_ids
            else:
                rest = list(args)
                if rest and isinstance(rest[0], (list, tuple, set)):
                    rest.pop(0)  # 旧版 all_ids 参数，弃用
                if len(rest) >= 1:
                    h = float(rest[0])
                if len(rest) >= 2:
                    t = float(rest[1])
                if len(rest) >= 3:
                    tm = rest[2]
            return cls._make_slide(name, _expand_logical_ids(on_ids), fixture_ids, h, t, tm)

        if preset_name in ("间隔追逐", *DETAIL_SEQUENCE_GENERATORS):
            return generate_detail_sequence(preset_name, fids, mk,
                {"hold": hold, "trans": trans, "density": density, "sequence_options": sequence_options,
                 "order": (fixture_ids, wings, block_size, wing_direction)})
        if preset_name in SYMMETRIC_SPACE_PRESETS:
            return cls._generate_symmetric_space(
                preset_name, fids, mk, hold, trans, density)

        if preset_name in LATTICE_MORPH_PRESETS:
            return cls._generate_lattice_morph(
                preset_name, fids, mk, hold, trans, density)

        if preset_name in EXTENDED_PPT_PRESETS:
            return cls._generate_extended_ppt(
                preset_name, fids, mk, hold, trans, density)

        # ── 跑泉类 ──────────────────────────────────────────────────────────
        if preset_name == "正跑":
            # 每帧点亮一个设备，从左到右
            return [mk(f"正跑 {i+1}", [fids[i]], hold, trans)
                    for i in range(n)]

        elif preset_name == "反跑":
            # 从右到左
            return [mk(f"反跑 {i+1}", [fids[n-1-i]], hold, trans)
                    for i in range(n)]

        elif preset_name == "对跑":
            # 两端向中间交替
            slides = []
            for i in range(n // 2):
                slides.append(mk(
                    f"对跑 {i+1}", [fids[i], fids[n-1-i]], hold, trans))
            return slides

        elif preset_name == "增加跑":
            # 逐个累加点亮
            return [mk(f"增加 {i+1}", fids[:i+1], hold, trans)
                    for i in range(n)]

        elif preset_name == "减少跑":
            # 逐个累减熄灭
            return [mk(f"减少 {i+1}", fids[i:], hold, trans)
                    for i in range(n)]

        elif preset_name == "放射跑":
            # 从中心向两端扩散
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = []
                for j in range(i + 1):
                    if mid - j >= 0:
                        on_ids.append(fids[mid - j])
                    if mid + j < n and j > 0:
                        on_ids.append(fids[mid + j])
                slides.append(mk(f"放射 {i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "收缩跑":
            # 从两端向中心收缩
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:i] + fids[n-i:]
                slides.append(mk(f"收缩 {i+1}", on_ids, hold, trans))
            return slides

        # ── 波浪类 ──────────────────────────────────────────────────────────
        elif preset_name == "波浪跑":
            # 每帧点亮连续 n//3 个设备，滑动窗口
            w = max(1, n // 3)
            slides = []
            for i in range(n):
                on_ids = [fids[(i + j) % n] for j in range(w)]
                slides.append(mk(f"波浪 {i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "双波浪":
            # 两个窗口同时滑动
            w = max(1, n // 4)
            slides = []
            for i in range(n):
                on_ids = ([fids[(i + j) % n] for j in range(w)] +
                          [fids[(i + n//2 + j) % n] for j in range(w)])
                slides.append(mk(f"双波浪 {i+1}", on_ids, hold, trans))
            return slides

        # ── 山型/锯齿类 ──────────────────────────────────────────────────────
        elif preset_name == "山型跑":
            # 先正跑再反跑（形成山型）
            fwd = [mk(f"山型↑{i+1}", fids[:i+1], hold, trans)
                   for i in range(n)]
            bwd = [mk(f"山型↓{i+1}", fids[:n-i], hold, trans)
                   for i in range(1, n)]
            return fwd + bwd

        elif preset_name == "锯齿跑":
            # 奇数帧正跑，偶数帧全灭
            slides = []
            for i in range(n):
                slides.append(mk(f"锯齿↑{i+1}", [fids[i]], hold, trans))
                slides.append(mk(f"锯齿↓{i+1}", [], hold * 0.3, trans))
            return slides

        elif preset_name == "三角跑":
            # 从两端向中心，然后从中心向两端
            mid = n // 2
            fwd = []
            for i in range(mid + 1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                fwd.append(mk(f"三角↑{i+1}", on_ids, hold, trans))
            bwd = []
            for i in range(mid - 1, -1, -1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                bwd.append(mk(f"三角↓{mid-i}", on_ids, hold, trans))
            return fwd + bwd

        # ── 矩形类 ──────────────────────────────────────────────────────────
        elif preset_name == "矩形正跑":
            # 全亮 → 全灭 → 全亮（矩形波）
            slides = []
            for i in range(4):
                if i % 2 == 0:
                    slides.append(mk(f"矩形ON {i//2+1}", fids, hold, trans))
                else:
                    slides.append(mk(f"矩形OFF {i//2+1}", [], hold, trans))
            return slides

        elif preset_name == "矩形反跑":
            # 奇偶交替（棋盘）
            odd  = fids[0::2]
            even = fids[1::2]
            return [
                mk("矩形奇", odd, hold, trans),
                mk("矩形偶", even, hold, trans),
            ]

        # ── 旗帜类 ──────────────────────────────────────────────────────────
        elif preset_name == "旗帜跑":
            # 前半亮后半灭，然后前半灭后半亮，交替
            half = n // 2
            return [
                mk("旗帜前", fids[:half], hold, trans),
                mk("旗帜后", fids[half:], hold, trans),
            ]

        elif preset_name == "向两边旗帜":
            # 中间亮，逐渐向两边扩展
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[mid-i:mid+i+1]
                slides.append(mk(f"旗帜扩{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "向中间旗帜":
            # 两端亮，逐渐向中间收缩
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"旗帜缩{i+1}", on_ids, hold, trans))
            return slides

        # ── 渐变类 ──────────────────────────────────────────────────────────
        elif preset_name == "渐开":
            # 逐个点亮（带淡入过渡）
            return [mk(f"渐开 {i+1}", fids[:i+1], fids,
                                    hold, trans, TRANS_FADE)
                    for i in range(n)]

        elif preset_name == "渐关":
            # 逐个熄灭（带淡出过渡）
            return [mk(f"渐关 {i+1}", fids[i:], fids,
                                    hold, trans, TRANS_FADE)
                    for i in range(n)]

        elif preset_name == "慢升快降":
            # 慢慢点亮全部，然后立即全灭
            slides = [mk(f"慢升 {i+1}", fids[:i+1], fids,
                                      hold * 1.5, trans * 2, TRANS_FADE)
                      for i in range(n)]
            slides.append(mk("快降", [], hold * 0.2, 0.0, TRANS_CUT))
            return slides

        # ── 间隔类 ──────────────────────────────────────────────────────────
        elif preset_name == "间1跑":
            # 每隔1个点亮，然后偏移1
            slides = []
            for offset in range(2):
                on_ids = fids[offset::2]
                slides.append(mk(f"间1跑 {offset+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "间2跑":
            # 每隔2个点亮，3组轮换
            slides = []
            for offset in range(3):
                on_ids = fids[offset::3]
                slides.append(mk(f"间2跑 {offset+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "间3跑":
            # 每隔3个点亮，4组轮换
            slides = []
            for offset in range(4):
                on_ids = fids[offset::4]
                slides.append(mk(f"间3跑 {offset+1}", on_ids, hold, trans))
            return slides

        # ── 开合类 ──────────────────────────────────────────────────────────
        elif preset_name == "正开":
            # 从第一个开始逐个点亮
            return [mk(f"正开 {i+1}", fids[:i+1], fids,
                                    hold, trans, TRANS_SEQ_ON)
                    for i in range(n)]

        elif preset_name == "反开":
            # 从最后一个开始逐个点亮
            return [mk(f"反开 {i+1}", fids[n-i-1:], fids,
                                    hold, trans, TRANS_SEQ_ON)
                    for i in range(n)]

        elif preset_name == "边开":
            # 从两端向中间点亮
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"边开 {i+1}", on_ids, fids,
                                              hold, trans, TRANS_SEQ_ON))
            return slides

        elif preset_name == "中开":
            # 从中间向两端点亮
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[mid-i:mid+i+1]
                slides.append(mk(f"中开 {i+1}", on_ids, fids,
                                              hold, trans, TRANS_SEQ_ON))
            return slides

        # ── 特殊类 ──────────────────────────────────────────────────────────
        elif preset_name == "全开全关":
            return [
                mk("全开", hold * 2, trans),
                mk("全关", [], hold * 2, trans),
            ]

        elif preset_name == "奇偶交替":
            odd  = fids[0::2]
            even = fids[1::2]
            return [
                mk("奇数ON", odd, hold, trans),
                mk("偶数ON", even, hold, trans),
            ]

        elif preset_name == "三组轮换":
            g1 = fids[0::3]
            g2 = fids[1::3]
            g3 = fids[2::3]
            return [
                mk("第一组", g1, hold, trans),
                mk("第二组", g2, hold, trans),
                mk("第三组", g3, hold, trans),
            ]

        elif preset_name == "随机闪烁":
            slides = []
            for i in range(8):
                k = random.randint(1, max(1, n // 2))
                on_ids = random.sample(fids, k)
                slides.append(mk(f"随机 {i+1}", on_ids, fids,
                                              hold * 0.5, trans * 0.5))
            return slides

        elif preset_name == "跑一次":
            # 正跑一遍后全灭
            slides = [mk(f"跑 {i+1}", [fids[i]], hold, trans)
                      for i in range(n)]
            slides.append(mk("全灭", [], hold, trans))
            return slides

        # ── v25 高级序列 ─────────────────────────────────────────────────────
        elif preset_name == "螺旋跑":
            # 从第一个设备开始，每帧向后偏移，形成螺旋感
            slides = []
            w = max(1, n // 4)
            for i in range(n):
                on_ids = [fids[(i + j) % n] for j in range(w)]
                slides.append(mk(f"螺旋 {i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "对角跑":
            # 奇数索引和偶数索引交替，模拟对角线感
            slides = []
            for i in range(n):
                on_ids = [fids[i]]
                if i + 2 < n:
                    on_ids.append(fids[i + 2])
                slides.append(mk(f"对角 {i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "Z字跑":
            # 正跑 → 反跑 → 正跑，形成Z字形
            fwd = [mk(f"Z↑{i+1}", [fids[i]], hold, trans)
                   for i in range(n)]
            bwd = [mk(f"Z↓{i+1}", [fids[n-1-i]], hold, trans)
                   for i in range(n)]
            return fwd + bwd

        elif preset_name == "棋盘闪":
            # 奇偶交替，快速切换
            odd  = fids[0::2]
            even = fids[1::2]
            slides = []
            for _ in range(4):
                slides.append(mk("棋盘奇", odd, hold * 0.4, trans * 0.3))
                slides.append(mk("棋盘偶", even, hold * 0.4, trans * 0.3))
            return slides

        elif preset_name == "十字展开":
            # 从中心向两端展开，然后收回
            mid = n // 2
            expand = []
            for i in range(mid + 1):
                on_ids = fids[mid-i:mid+i+1]
                expand.append(mk(f"十字展{i+1}", on_ids, hold, trans, TRANS_FADE))
            shrink = list(reversed(expand[:-1]))
            return expand + shrink

        elif preset_name == "菱形展开":
            # 从中心向四周扩散（1D近似：中心向两端扩散再收缩）
            mid = n // 2
            expand = []
            for i in range(mid + 1):
                lo = max(0, mid - i)
                hi = min(n, mid + i + 1)
                on_ids = fids[lo:hi]
                expand.append(mk(f"菱形展{i+1}", on_ids, hold, trans, TRANS_FADE))
            shrink = list(reversed(expand[:-1]))
            return expand + shrink

        elif preset_name == "百叶窗":
            # 分成4组，依次点亮每组，形成百叶窗效果
            k = max(1, n // 4)
            slides = []
            for g in range(4):
                on_ids = fids[g*k:(g+1)*k]
                slides.append(mk(f"百叶 {g+1}", on_ids, hold, trans))
            slides.append(mk("百叶全亮", fids, hold, trans))
            slides.append(mk("百叶全灭", [], hold, trans))
            return slides

        elif preset_name == "像素雨":
            # 随机位置逐个点亮，然后全灭
            import random as _random
            order = list(range(n))
            _random.shuffle(order)
            slides = []
            for i, idx_r in enumerate(order):
                slides.append(mk(f"雨{i+1}", [fids[idx_r]], hold * 0.3, trans * 0.2))
            slides.append(mk("雨全灭", [], hold, trans))
            return slides

        elif preset_name == "涟漪扩散":
            # 从中心向外扩散，然后消散
            mid = n // 2
            expand = []
            for i in range(mid + 2):
                lo = max(0, mid - i)
                hi = min(n, mid + i + 1)
                # 只亮边缘
                on_ids = ([fids[lo]] if lo < n else []) + ([fids[hi-1]] if hi-1 > lo else [])
                expand.append(mk(f"涟漪{i+1}", on_ids, hold * 0.5, trans * 0.3))
            return expand

        elif preset_name == "心跳脉冲":
            # 全亮 → 全灭，快速两次，然后慢速一次
            slides = []
            for _ in range(2):
                slides.append(mk("心跳亮", fids, hold * 0.3, trans * 0.2))
                slides.append(mk("心跳灭", [], hold * 0.3, trans * 0.2))
            slides.append(mk("心跳慢亮", fids, hold * 1.5, trans))
            slides.append(mk("心跳慢灭", [], hold * 1.5, trans))
            return slides

        elif preset_name == "呼吸渐变":
            # 渐亮 → 渐灭，带淡入淡出
            bright = [mk(f"呼吸亮{i+1}", fids[:int(n*(i+1)/8)],
                          hold * 0.6, trans, TRANS_FADE)
                      for i in range(8)]
            dim = [mk(f"呼吸灭{i+1}", fids[:int(n*(8-i)/8)],
                      hold * 0.6, trans, TRANS_FADE)
                   for i in range(8)]
            return bright + dim

        elif preset_name == "随机填充":
            # 随机顺序逐个点亮全部设备，然后随机顺序逐个熄灭
            import random as _random
            order_on = list(range(n))
            _random.shuffle(order_on)
            order_off = list(range(n))
            _random.shuffle(order_off)
            slides = []
            lit: set = set()
            for idx_r in order_on:
                lit.add(fids[idx_r])
                slides.append(mk(f"填充{len(lit)}", list(lit), hold * 0.4, trans * 0.3))
            for idx_r in order_off:
                lit.discard(fids[idx_r])
                slides.append(mk(f"清除{n-len(lit)}", list(lit), hold * 0.4, trans * 0.3))
            return slides

        elif preset_name == "蛇形跑":
            # 正跑到头后反跑，来回蛇形
            fwd = [mk(f"蛇→{i+1}", [fids[i]], hold, trans)
                   for i in range(n)]
            bwd = [mk(f"蛇←{i+1}", [fids[n-1-i]], hold, trans)
                   for i in range(n)]
            return fwd + bwd

        elif preset_name == "双螺旋":
            # 两个窗口从两端向中间同时移动
            slides = []
            w = max(1, n // 6)
            for i in range(n // 2 + 1):
                on_ids = ([fids[(j) % n] for j in range(i, i+w)] +
                          [fids[(n-1-j) % n] for j in range(i, i+w)])
                slides.append(mk(f"双螺旋{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "星形展开":
            # 从中心向两端快速展开，然后全亮，然后全灭
            mid = n // 2
            expand = []
            for i in range(mid + 1):
                lo = max(0, mid - i)
                hi = min(n, mid + i + 1)
                expand.append(mk(f"星展{i+1}", fids[lo:hi],
                                  hold * 0.4, trans * 0.3, TRANS_FADE))
            expand.append(mk("星全亮", fids, hold, trans))
            expand.append(mk("星全灭", [], hold, trans))
            return expand

        # ── v30 扩充：跑泉变式 ──────────────────────────────────────────────
        elif preset_name == "正跑双":  # 每帧点亮2个相邻设备，正向滑动
            w = 2
            return [mk(f"正跑双{i+1}", [fids[(i+j)%n] for j in range(w)], hold, trans)
                    for i in range(n)]

        elif preset_name == "正跑三":  # 每帧点亮3个相邻设备，正向滑动
            w = 3
            return [mk(f"正跑三{i+1}", [fids[(i+j)%n] for j in range(w)], hold, trans)
                    for i in range(n)]

        elif preset_name == "反跑双":  # 每帧点亮2个相邻设备，反向滑动
            w = 2
            return [mk(f"反跑双{i+1}", [fids[(n-1-i+j)%n] for j in range(w)], hold, trans)
                    for i in range(n)]

        elif preset_name == "正跑半":  # 每帧点亮前半段，整体正向滑动
            hw = max(1, n // 2)
            return [mk(f"正跑半{i+1}", [fids[(i+j)%n] for j in range(hw)], hold, trans)
                    for i in range(n)]

        elif preset_name == "反跑半":  # 每帧点亮前半段，整体反向滑动
            hw = max(1, n // 2)
            return [mk(f"反跑半{i+1}", [fids[(n-1-i+j)%n] for j in range(hw)], hold, trans)
                    for i in range(n)]

        elif preset_name == "对跑合":  # 两端向中心跑，汇合后全亮再全灭
            mid = n // 2
            slides = []
            for i in range(mid):
                slides.append(mk(f"对跑合{i+1}", [fids[i], fids[n-1-i]], hold, trans))
            slides.append(mk("对跑全亮", fids, hold, trans))
            slides.append(mk("对跑全灭", [], hold, trans))
            return slides

        elif preset_name == "中心放射":  # 从中心向两端逐个点亮，不收回
            mid = n // 2
            slides = []
            lit: set = set()
            for i in range(mid + 1):
                if mid - i >= 0:
                    lit.add(fids[mid - i])
                if mid + i < n and i > 0:
                    lit.add(fids[mid + i])
                slides.append(mk(f"中心放射{i+1}", list(lit), hold, trans))
            return slides

        elif preset_name == "边缘收缩":  # 从两端向中心逐个熄灭
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[i:n-i]
                slides.append(mk(f"边缘收缩{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "追逐跑":  # 一亮一暗交替，形成追逐感
            slides = []
            for i in range(n):
                on_ids = [fids[i], fids[(i+2)%n], fids[(i+4)%n]]
                slides.append(mk(f"追逐{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "三点追逐":  # 三个点等间距追逐
            step = max(1, n // 3)
            slides = []
            for i in range(n):
                on_ids = [fids[(i+j*step)%n] for j in range(3)]
                slides.append(mk(f"三点追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "四点追逐":  # 四个点等间距追逐
            step = max(1, n // 4)
            slides = []
            for i in range(n):
                on_ids = [fids[(i+j*step)%n] for j in range(4)]
                slides.append(mk(f"四点追{i+1}", on_ids, hold, trans))
            return slides

        # ── v30 扩充：波浪变式 ────────────────────────────────────────────────
        elif preset_name == "三波浪":  # 三个窗口同时滑动
            w = max(1, n // 5)
            slides = []
            for i in range(n):
                on_ids = ([fids[(i+j)%n] for j in range(w)] +
                          [fids[(i+n//3+j)%n] for j in range(w)] +
                          [fids[(i+2*n//3+j)%n] for j in range(w)])
                slides.append(mk(f"三波浪{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "宽波浪":  # 宽窗口（n//2）滑动波浪
            w = max(1, n // 2)
            slides = []
            for i in range(n):
                on_ids = [fids[(i+j)%n] for j in range(w)]
                slides.append(mk(f"宽波浪{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "窄波浪":  # 窄窗口（2个）滑动波浪
            slides = []
            for i in range(n):
                on_ids = [fids[i%n], fids[(i+1)%n]]
                slides.append(mk(f"窄波浪{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "反向波浪":  # 波浪反向滑动
            w = max(1, n // 3)
            slides = []
            for i in range(n):
                on_ids = [fids[(n-1-i+j)%n] for j in range(w)]
                slides.append(mk(f"反波浪{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "双向波浪":  # 两个波浪相向而行
            w = max(1, n // 4)
            slides = []
            for i in range(n):
                on_ids = ([fids[(i+j)%n] for j in range(w)] +
                          [fids[(n-1-i+j)%n] for j in range(w)])
                slides.append(mk(f"双向波{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "呼吸波浪":  # 波浪宽度随时间呼吸变化
            slides = []
            for i in range(n):
                w = max(1, int(n * (0.2 + 0.3 * abs(math.sin(i * math.pi / n)))))
                on_ids = [fids[(i+j)%n] for j in range(w)]
                slides.append(mk(f"呼吸波{i+1}", on_ids, hold, trans, TRANS_FADE))
            return slides

        # ── v30 扩充：间隔变式 ────────────────────────────────────────────────
        elif preset_name == "间4跑":  # 每隔4个点亮，5组轮换
            slides = []
            for offset in range(5):
                on_ids = fids[offset::5]
                slides.append(mk(f"间4跑{offset+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "间隔渐开":  # 间隔点亮，逐渐增加密度
            slides = []
            for step in range(n, 0, -1):
                on_ids = fids[::step]
                slides.append(mk(f"间隔渐开{n-step+1}", on_ids, hold * 0.5, trans))
            return slides

        elif preset_name == "间隔渐关":  # 间隔点亮，逐渐减少密度
            slides = []
            for step in range(1, n+1):
                on_ids = fids[::step]
                slides.append(mk(f"间隔渐关{step}", on_ids, hold * 0.5, trans))
            return slides

        # ── v30 扩充：渐变变式 ────────────────────────────────────────────────
        elif preset_name == "反向渐开":  # 从末端开始逐个点亮
            return [mk(f"反渐开{i+1}", fids[n-i-1:], hold, trans, TRANS_FADE)
                    for i in range(n)]

        elif preset_name == "反向渐关":  # 从末端开始逐个熄灭
            return [mk(f"反渐关{i+1}", fids[:n-i], hold, trans, TRANS_FADE)
                    for i in range(n)]

        elif preset_name == "中心渐开":  # 从中心向两端逐渐点亮
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[max(0,mid-i):min(n,mid+i+1)]
                slides.append(mk(f"中渐开{i+1}", on_ids, hold, trans, TRANS_FADE))
            return slides

        elif preset_name == "边缘渐开":  # 从两端向中心逐渐点亮
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"边渐开{i+1}", on_ids, hold, trans, TRANS_FADE))
            return slides

        elif preset_name == "快升慢降":  # 快速点亮全部，缓慢逐个熄灭
            slides = [mk("快升", fids, hold * 0.2, 0.0, TRANS_CUT)]
            slides += [mk(f"慢降{i+1}", fids[i:], hold * 1.5, trans * 2, TRANS_FADE)
                       for i in range(n)]
            return slides

        elif preset_name == "交替渐变":  # 奇偶组交替渐亮渐灭
            odd = fids[0::2]
            even = fids[1::2]
            slides = []
            for i in range(8):
                if i % 2 == 0:
                    slides.append(mk(f"交替奇{i//2+1}", odd, hold, trans, TRANS_FADE))
                else:
                    slides.append(mk(f"交替偶{i//2+1}", even, hold, trans, TRANS_FADE))
            return slides

        # ── v30 扩充：开合变式 ────────────────────────────────────────────────
        elif preset_name == "正关":  # 从第一个开始逐个熄灭
            return [mk(f"正关{i+1}", fids[i+1:], hold, trans, TRANS_SEQ_OFF)
                    for i in range(n)]

        elif preset_name == "反关":  # 从最后一个开始逐个熄灭
            return [mk(f"反关{i+1}", fids[:n-i-1], hold, trans, TRANS_SEQ_OFF)
                    for i in range(n)]

        elif preset_name == "边关":  # 从两端向中心逐个熄灭
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[i+1:n-i-1]
                slides.append(mk(f"边关{i+1}", on_ids, hold, trans, TRANS_SEQ_OFF))
            return slides

        elif preset_name == "中关":  # 从中心向两端逐个熄灭
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:max(0,mid-i)] + fids[min(n,mid+i+1):]
                slides.append(mk(f"中关{i+1}", on_ids, hold, trans, TRANS_SEQ_OFF))
            return slides

        elif preset_name == "开合循环":  # 正开 → 反关 → 反开 → 正关
            open_fwd  = [mk(f"开合开{i+1}", fids[:i+1], hold, trans, TRANS_SEQ_ON) for i in range(n)]
            close_bwd = [mk(f"开合关{i+1}", fids[:n-i-1], hold, trans, TRANS_SEQ_OFF) for i in range(n)]
            return open_fwd + close_bwd

        elif preset_name == "对开对关":  # 两端向中心开，再两端向中心关
            mid = n // 2
            open_slides  = [mk(f"对开{i+1}", fids[:i+1]+fids[n-i-1:], hold, trans, TRANS_SEQ_ON)  for i in range(mid+1)]
            close_slides = [mk(f"对关{i+1}", fids[i+1:n-i-1],          hold, trans, TRANS_SEQ_OFF) for i in range(mid+1)]
            return open_slides + close_slides

        # ── v30 扩充：特殊变式 ────────────────────────────────────────────────
        elif preset_name == "四组轮换":  # 4组依次点亮
            g = [fids[i::4] for i in range(4)]
            return [mk(f"四组{i+1}", g[i], hold, trans) for i in range(4)]

        elif preset_name == "五组轮换":  # 5组依次点亮
            g = [fids[i::5] for i in range(5)]
            return [mk(f"五组{i+1}", g[i], hold, trans) for i in range(5)]

        elif preset_name == "六组轮换":  # 6组依次点亮
            g = [fids[i::6] for i in range(6)]
            return [mk(f"六组{i+1}", g[i], hold, trans) for i in range(6)]

        elif preset_name == "奇偶追逐":  # 奇偶交替并滑动
            slides = []
            for i in range(n):
                on_ids = fids[(i%2)::2]
                slides.append(mk(f"奇偶追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "三组追逐":  # 三组依次追逐滑动
            slides = []
            for i in range(n):
                on_ids = fids[(i%3)::3]
                slides.append(mk(f"三组追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "随机闪烁2":  # 更密集的随机闪烁（12帧）
            slides = []
            for i in range(12):
                k = random.randint(max(1, n//3), max(2, 2*n//3))
                on_ids = random.sample(fids, k)
                slides.append(mk(f"随机2_{i+1}", on_ids, hold * 0.3, trans * 0.2))
            return slides

        elif preset_name == "随机单点":  # 每帧随机点亮1个
            slides = []
            for i in range(n):
                on_ids = [random.choice(fids)]
                slides.append(mk(f"随机单{i+1}", on_ids, hold * 0.5, trans * 0.3))
            return slides

        elif preset_name == "随机双点":  # 每帧随机点亮2个
            slides = []
            for i in range(n):
                on_ids = random.sample(fids, min(2, n))
                slides.append(mk(f"随机双{i+1}", on_ids, hold * 0.5, trans * 0.3))
            return slides

        elif preset_name == "全闪":  # 全亮全灭快速闪烁（8次）
            slides = []
            for i in range(8):
                slides.append(mk(f"全闪亮{i+1}", fids, hold * 0.3, 0.0, TRANS_CUT))
                slides.append(mk(f"全闪灭{i+1}", [], hold * 0.3, 0.0, TRANS_CUT))
            return slides

        elif preset_name == "慢闪":  # 全亮全灭慢速闪烁（4次）
            slides = []
            for i in range(4):
                slides.append(mk(f"慢闪亮{i+1}", fids, hold * 2.0, trans, TRANS_FADE))
                slides.append(mk(f"慢闪灭{i+1}", [], hold * 2.0, trans, TRANS_FADE))
            return slides

        elif preset_name == "节奏闪":  # 强弱节奏：2快1慢
            slides = []
            for _ in range(3):
                slides.append(mk("节奏亮", fids, hold * 0.2, 0.0, TRANS_CUT))
                slides.append(mk("节奏灭", [], hold * 0.2, 0.0, TRANS_CUT))
            slides.append(mk("节奏慢亮", fids, hold * 1.5, trans, TRANS_FADE))
            slides.append(mk("节奏慢灭", [], hold * 1.5, trans, TRANS_FADE))
            return slides

        # ── v30 扩充：空间编组效果 ────────────────────────────────────────────
        elif preset_name == "前后交替":  # 前半/后半交替点亮
            half = n // 2
            return [
                mk("前半亮", fids[:half], hold, trans),
                mk("后半亮", fids[half:], hold, trans),
            ]

        elif preset_name == "前后追逐":  # 前半/后半追逐，带滑动
            half = n // 2
            slides = []
            for i in range(half):
                slides.append(mk(f"前后追{i+1}", [fids[i], fids[half+i]], hold, trans))
            return slides

        elif preset_name == "三段轮流":  # 三段依次点亮
            seg = n // 3
            return [
                mk("三段1", fids[:seg], hold, trans),
                mk("三段2", fids[seg:2*seg], hold, trans),
                mk("三段3", fids[2*seg:], hold, trans),
            ]

        elif preset_name == "四段轮流":  # 四段依次点亮
            seg = n // 4
            return [
                mk("四段1", fids[:seg], hold, trans),
                mk("四段2", fids[seg:2*seg], hold, trans),
                mk("四段3", fids[2*seg:3*seg], hold, trans),
                mk("四段4", fids[3*seg:], hold, trans),
            ]

        elif preset_name == "三段追逐":  # 三段依次追逐滑动
            seg = n // 3
            slides = []
            for i in range(seg):
                on_ids = [fids[i], fids[seg+i], fids[2*seg+i]]
                slides.append(mk(f"三段追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "四段追逐":  # 四段依次追逐滑动
            seg = max(1, n // 4)
            slides = []
            for i in range(seg):
                on_ids = [fids[j*seg+i] for j in range(4) if j*seg+i < n]
                slides.append(mk(f"四段追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "前后渐开":  # 前半从左渐开，后半从右渐开
            half = n // 2
            slides = []
            for i in range(half):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"前后渐开{i+1}", on_ids, hold, trans, TRANS_FADE))
            return slides

        elif preset_name == "分段闪烁":  # 各段依次快速闪烁
            seg = max(1, n // 4)
            slides = []
            for s in range(4):
                seg_ids = fids[s*seg:(s+1)*seg]
                slides.append(mk(f"分段闪{s+1}", seg_ids, hold * 0.4, trans * 0.3))
                slides.append(mk(f"分段灭{s+1}", [], hold * 0.2, 0.0, TRANS_CUT))
            return slides

        elif preset_name == "分段渐亮":  # 各段依次渐亮，最终全亮
            seg = max(1, n // 4)
            slides = []
            lit: set = set()
            for s in range(4):
                lit.update(fids[s*seg:(s+1)*seg])
                slides.append(mk(f"分段渐亮{s+1}", list(lit), hold, trans, TRANS_FADE))
            slides.append(mk("分段全灭", [], hold, trans))
            return slides

        elif preset_name == "分段渐灭":  # 各段依次渐灭，从全亮开始
            seg = max(1, n // 4)
            slides = [mk("分段全亮", fids, hold, trans, TRANS_FADE)]
            lit = set(fids)
            for s in range(4):
                lit -= set(fids[s*seg:(s+1)*seg])
                slides.append(mk(f"分段渐灭{s+1}", list(lit), hold, trans, TRANS_FADE))
            return slides

        # ── v30 扩充：山型锯齿变式 ────────────────────────────────────────────
        elif preset_name == "双山型":  # 两个山型同时从两端向中心
            mid = n // 2
            slides = []
            for i in range(mid + 1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"双山↑{i+1}", on_ids, hold, trans))
            for i in range(mid - 1, -1, -1):
                on_ids = fids[:i+1] + fids[n-i-1:]
                slides.append(mk(f"双山↓{mid-i}", on_ids, hold, trans))
            return slides

        elif preset_name == "锯齿双":  # 两个锯齿同时正向跑
            slides = []
            half = n // 2
            for i in range(half):
                slides.append(mk(f"锯齿双↑{i+1}", [fids[i], fids[half+i]], hold, trans))
                slides.append(mk(f"锯齿双↓{i+1}", [], hold * 0.3, trans))
            return slides

        elif preset_name == "锯齿反":  # 锯齿反向跑
            slides = []
            for i in range(n):
                slides.append(mk(f"锯齿反↑{i+1}", [fids[n-1-i]], hold, trans))
                slides.append(mk(f"锯齿反↓{i+1}", [], hold * 0.3, trans))
            return slides

        elif preset_name == "阶梯跑":  # 每次多亮一个，形成阶梯
            slides = []
            for i in range(n):
                on_ids = [fids[(i+j)%n] for j in range(i+1)]
                slides.append(mk(f"阶梯{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "反阶梯跑":  # 每次少亮一个，形成反阶梯
            slides = []
            for i in range(n):
                on_ids = [fids[(n-1-i+j)%n] for j in range(n-i)]
                slides.append(mk(f"反阶梯{i+1}", on_ids, hold, trans))
            return slides

        # ── v30 扩充：矩形旗帜变式 ────────────────────────────────────────────
        elif preset_name == "矩形闪":  # 全亮全灭快速矩形闪（6次）
            slides = []
            for i in range(6):
                slides.append(mk(f"矩形闪亮{i+1}", fids, hold * 0.3, 0.0, TRANS_CUT))
                slides.append(mk(f"矩形闪灭{i+1}", [], hold * 0.3, 0.0, TRANS_CUT))
            return slides

        elif preset_name == "三旗帜":  # 三段旗帜依次点亮
            seg = n // 3
            slides = []
            for i in range(3):
                slides.append(mk(f"三旗{i+1}", fids[i*seg:(i+1)*seg], hold, trans))
            return slides

        elif preset_name == "旗帜追逐":  # 旗帜窗口追逐滑动
            w = max(1, n // 3)
            slides = []
            for i in range(n):
                on_ids = fids[i:i+w]
                slides.append(mk(f"旗帜追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "旗帜展开":  # 旗帜从中心向两端展开
            mid = n // 2
            w = max(1, n // 6)
            slides = []
            for i in range(mid // w + 1):
                lo = max(0, mid - i * w)
                hi = min(n, mid + i * w + w)
                slides.append(mk(f"旗帜展{i+1}", fids[lo:hi], hold, trans))
            return slides

        # ── v30 扩充：螺旋对角变式 ────────────────────────────────────────────
        elif preset_name == "螺旋反":  # 螺旋反向
            w = max(1, n // 4)
            slides = []
            for i in range(n):
                on_ids = [fids[(n-1-i+j)%n] for j in range(w)]
                slides.append(mk(f"螺旋反{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "螺旋宽":  # 宽螺旋（n//2窗口）
            w = max(1, n // 2)
            slides = []
            for i in range(n):
                on_ids = [fids[(i+j)%n] for j in range(w)]
                slides.append(mk(f"螺旋宽{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "对角反":  # 对角反向跑
            slides = []
            for i in range(n):
                on_ids = [fids[n-1-i]]
                if n-1-i-2 >= 0:
                    on_ids.append(fids[n-1-i-2])
                slides.append(mk(f"对角反{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "Z字反":  # Z字反向跑
            bwd = [mk(f"Z反↑{i+1}", [fids[n-1-i]], hold, trans) for i in range(n)]
            fwd = [mk(f"Z反↓{i+1}", [fids[i]], hold, trans) for i in range(n)]
            return bwd + fwd

        elif preset_name == "螺旋开合":  # 螺旋展开后收回
            w = max(1, n // 4)
            expand = []
            for i in range(n // w + 1):
                on_ids = [fids[(j)%n] for j in range(i * w)]
                expand.append(mk(f"螺旋开{i+1}", on_ids, hold, trans))
            shrink = list(reversed(expand[:-1]))
            return expand + shrink

        # ── v30 扩充：涟漪心跳变式 ────────────────────────────────────────────
        elif preset_name == "涟漪反":  # 从两端向中心收缩的涟漪
            mid = n // 2
            slides = []
            for i in range(mid + 2):
                lo = i
                hi = n - i
                on_ids = ([fids[lo]] if lo < hi else []) + ([fids[hi-1]] if hi-1 > lo else [])
                slides.append(mk(f"涟漪反{i+1}", on_ids, hold * 0.5, trans * 0.3))
            return slides

        elif preset_name == "涟漪全":  # 涟漪扩散后全亮再消散
            mid = n // 2
            expand = []
            for i in range(mid + 2):
                lo = max(0, mid - i)
                hi = min(n, mid + i + 1)
                on_ids = ([fids[lo]] if lo < n else []) + ([fids[hi-1]] if hi-1 > lo else [])
                expand.append(mk(f"涟漪全{i+1}", on_ids, hold * 0.5, trans * 0.3))
            expand.append(mk("涟漪全亮", fids, hold, trans))
            expand.append(mk("涟漪全灭", [], hold, trans))
            return expand

        elif preset_name == "双心跳":  # 心跳脉冲加强版（3次快+1次慢）
            slides = []
            for _ in range(3):
                slides.append(mk("双心亮", fids, hold * 0.2, trans * 0.15))
                slides.append(mk("双心灭", [], hold * 0.2, trans * 0.15))
            slides.append(mk("双心慢亮", fids, hold * 2.0, trans))
            slides.append(mk("双心慢灭", [], hold * 2.0, trans))
            return slides

        elif preset_name == "呼吸慢":  # 慢速呼吸（16步）
            bright = [mk(f"呼吸慢亮{i+1}", fids[:int(n*(i+1)/16)],
                          hold * 1.0, trans * 1.5, TRANS_FADE) for i in range(16)]
            dim    = [mk(f"呼吸慢灭{i+1}", fids[:int(n*(16-i)/16)],
                          hold * 1.0, trans * 1.5, TRANS_FADE) for i in range(16)]
            return bright + dim

        elif preset_name == "呼吸快":  # 快速呼吸（4步）
            bright = [mk(f"呼吸快亮{i+1}", fids[:int(n*(i+1)/4)],
                          hold * 0.3, trans * 0.4, TRANS_FADE) for i in range(4)]
            dim    = [mk(f"呼吸快灭{i+1}", fids[:int(n*(4-i)/4)],
                          hold * 0.3, trans * 0.4, TRANS_FADE) for i in range(4)]
            return bright + dim

        elif preset_name == "随机呼吸":  # 随机数量的呼吸渐变
            slides = []
            for i in range(8):
                k = random.randint(1, n)
                on_ids = fids[:k]
                slides.append(mk(f"随机呼{i+1}", on_ids, hold * 0.8, trans, TRANS_FADE))
            return slides

        # ── v30 扩充：棋盘变式 ────────────────────────────────────────────────
        elif preset_name == "棋盘追逐":  # 棋盘格追逐滑动
            slides = []
            for i in range(n):
                on_ids = fids[(i%2)::2]
                slides.append(mk(f"棋盘追{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "棋盘渐开":  # 棋盘格逐渐点亮
            odd = fids[0::2]
            even = fids[1::2]
            slides = []
            for i in range(len(odd)):
                slides.append(mk(f"棋盘渐奇{i+1}", odd[:i+1], hold, trans, TRANS_FADE))
            for i in range(len(even)):
                slides.append(mk(f"棋盘渐偶{i+1}", odd+even[:i+1], hold, trans, TRANS_FADE))
            slides.append(mk("棋盘全灭", [], hold, trans))
            return slides

        elif preset_name == "棋盘波浪":  # 棋盘格波浪滑动
            w = max(1, n // 4)
            slides = []
            for i in range(n):
                base = [fids[(i+j)%n] for j in range(w)]
                on_ids = [f for j, f in enumerate(base) if j % 2 == 0]
                slides.append(mk(f"棋盘波{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "棋盘闪2":  # 棋盘快速闪烁（8次）
            odd = fids[0::2]
            even = fids[1::2]
            slides = []
            for _ in range(8):
                slides.append(mk("棋盘闪奇", odd, hold * 0.25, 0.0, TRANS_CUT))
                slides.append(mk("棋盘闪偶", even, hold * 0.25, 0.0, TRANS_CUT))
            return slides

        # ── v30 扩充：星形展开变式 ────────────────────────────────────────────
        elif preset_name == "星形收缩":  # 从两端向中心收缩
            mid = n // 2
            shrink = []
            for i in range(mid + 1):
                lo = i
                hi = n - i
                shrink.append(mk(f"星缩{i+1}", fids[lo:hi], hold * 0.4, trans * 0.3, TRANS_FADE))
            shrink.append(mk("星缩全灭", [], hold, trans))
            return shrink

        elif preset_name == "星形闪":  # 星形展开后快速闪烁
            mid = n // 2
            expand = [mk(f"星闪展{i+1}", fids[max(0,mid-i):min(n,mid+i+1)],
                          hold * 0.3, trans * 0.2, TRANS_FADE) for i in range(mid+1)]
            flash = []
            for _ in range(4):
                flash.append(mk("星闪亮", fids, hold * 0.2, 0.0, TRANS_CUT))
                flash.append(mk("星闪灭", [], hold * 0.2, 0.0, TRANS_CUT))
            return expand + flash

        elif preset_name == "双星展开":  # 两个中心同时展开
            q1, q2 = n // 4, 3 * n // 4
            slides = []
            for i in range(n // 4 + 1):
                on_ids = (fids[max(0,q1-i):min(n,q1+i+1)] +
                          fids[max(0,q2-i):min(n,q2+i+1)])
                slides.append(mk(f"双星展{i+1}", on_ids, hold * 0.4, trans * 0.3, TRANS_FADE))
            slides.append(mk("双星全亮", fids, hold, trans))
            slides.append(mk("双星全灭", [], hold, trans))
            return slides

        # ── v30 扩充：蛇形双螺旋变式 ──────────────────────────────────────────
        elif preset_name == "蛇形双":  # 两条蛇同时正反跑
            half = n // 2
            slides = []
            for i in range(half):
                slides.append(mk(f"蛇双{i+1}", [fids[i], fids[n-1-i]], hold, trans))
            return slides

        elif preset_name == "蛇形宽":  # 蛇形，每次点亮2个
            slides = []
            for i in range(n):
                slides.append(mk(f"蛇宽→{i+1}", [fids[i%n], fids[(i+1)%n]], hold, trans))
            bwd = []
            for i in range(n):
                bwd.append(mk(f"蛇宽←{i+1}", [fids[(n-1-i)%n], fids[(n-2-i)%n]], hold, trans))
            return slides + bwd

        elif preset_name == "双螺旋反":  # 双螺旋反向
            w = max(1, n // 6)
            slides = []
            for i in range(n // 2 + 1):
                on_ids = ([fids[(n-1-j)%n] for j in range(i, i+w)] +
                          [fids[(j)%n] for j in range(i, i+w)])
                slides.append(mk(f"双螺旋反{i+1}", on_ids, hold, trans))
            return slides

        elif preset_name == "双螺旋宽":  # 双螺旋宽窗口
            w = max(1, n // 3)
            slides = []
            for i in range(n // 2 + 1):
                on_ids = ([fids[(j)%n] for j in range(i, i+w)] +
                          [fids[(n-1-j)%n] for j in range(i, i+w)])
                slides.append(mk(f"双螺旋宽{i+1}", on_ids, hold, trans))
            return slides

        # ── v30 扩充：随机填充变式 ────────────────────────────────────────────
        elif preset_name == "随机填充2":  # 随机填充（更快，步进2）
            import random as _random
            order_on = list(range(n))
            _random.shuffle(order_on)
            slides = []
            lit: set = set()
            for idx_r in order_on:
                lit.add(fids[idx_r])
                if len(lit) % 2 == 0 or len(lit) == n:
                    slides.append(mk(f"快填{len(lit)}", list(lit), hold * 0.3, trans * 0.2))
            slides.append(mk("快填灭", [], hold, trans))
            return slides

        elif preset_name == "随机清除":  # 从全亮开始随机逐个熄灭
            import random as _random
            order_off = list(range(n))
            _random.shuffle(order_off)
            slides = [mk("随机清全亮", fids, hold * 0.5, trans * 0.3)]
            lit = set(fids)
            for idx_r in order_off:
                lit.discard(fids[idx_r])
                slides.append(mk(f"随机清{n-len(lit)}", list(lit), hold * 0.4, trans * 0.3))
            return slides

        elif preset_name == "随机爆炸":  # 从中心随机向外扩散
            import random as _random
            mid = n // 2
            order = list(range(n))
            order.sort(key=lambda x: (abs(x - mid) + _random.randint(0, 2)))
            slides = []
            lit: set = set()
            for idx in order:
                lit.add(fids[idx])
                slides.append(mk(f"随机爆{len(lit)}", list(lit), hold * 0.3, trans * 0.2))
            slides.append(mk("随机爆灭", [], hold, trans))
            return slides

        # 未知预设，返回空
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  内置预设分类目录
# ─────────────────────────────────────────────────────────────────────────────
BUILTIN_PRESETS: Dict[str, List[str]] = {
    "跑泉类": [
        "正跑", "反跑", "对跑", "增加跑", "减少跑",
        "放射跑", "收缩跑", "跑一次",
        # v30 跑泉变式
        "正跑双", "正跑三", "反跑双",
        "正跑半", "反跑半", "对跑合",
        "中心放射", "边缘收缩",
        "追逐跑", "三点追逐", "四点追逐",
    ],
    "波浪类": [
        "波浪跑", "双波浪",
        # v30 波浪变式
        "三波浪", "宽波浪", "窄波浪",
        "反向波浪", "双向波浪", "呼吸波浪",
    ],
    "山型/锯齿": [
        "山型跑", "锯齿跑", "三角跑",
        # v30 山型锯齿变式
        "双山型", "锯齿双", "锯齿反",
        "阶梯跑", "反阶梯跑",
    ],
    "矩形/旗帜": [
        "矩形正跑", "矩形反跑",
        "旗帜跑", "向两边旗帜", "向中间旗帜",
        # v30 矩形旗帜变式
        "矩形闪", "三旗帜", "旗帜追逐", "旗帜展开",
    ],
    "渐变类": [
        "渐开", "渐关", "慢升快降",
        # v30 渐变变式
        "反向渐开", "反向渐关", "中心渐开", "边缘渐开",
        "快升慢降", "交替渐变",
    ],
    "间隔类": [
        "间1跑", "间2跑", "间3跑",
        # v30 间隔变式
        "间4跑", "间隔追逐", "间隔渐开", "间隔渐关",
    ],
    "开合类": [
        "正开", "反开", "边开", "中开",
        # v30 开合变式
        "正关", "反关", "边关", "中关",
        "开合循环", "对开对关",
    ],
    "特殊类": [
        "全开全关", "奇偶交替", "三组轮换", "随机闪烁",
        # v30 特殊变式
        "四组轮换", "五组轮换", "六组轮换",
        "奇偶追逐", "三组追逐",
        "随机闪烁2", "随机单点", "随机双点",
        "全闪", "慢闪", "节奏闪",
    ],
    "空间编组": [
        "前后交替", "前后追逐",
        "三段轮流", "四段轮流",
        "三段追逐", "四段追逐",
        "前后渐开", "分段闪烁",
        "分段渐亮", "分段渐灭",
        # 注意：分段渐灯对应 BUILTIN_SEQUENCES 中的“分段渐灯”
    ],
    "对称空间": SYMMETRIC_SPACE_PRESETS,
    "点阵变形": LATTICE_MORPH_PRESETS,
    "非对称运动": ASYMMETRIC_PPT_PRESETS,
    "粒子物理": PARTICLE_PPT_PRESETS,
    "形状变形": SHAPE_SEQUENCE_PRESETS,
    "节奏编排": RHYTHM_SEQUENCE_PRESETS,
    "高级序列": [
        *DETAIL_SEQUENCE_GENERATORS,
        "螺旋跑", "对角跑", "Z字跑",
        "棋盘闪", "十字展开", "菱形展开",
        "百叶窗", "像素雨", "涟漪扩散",
        "心跳脉冲", "呼吸渐变", "随机填充",
        "蛇形跑", "双螺旋", "星形展开",
        # v30 高级变式
        "螺旋反", "螺旋宽", "对角反", "Z字反", "螺旋开合",
        "涟漪反", "涟漪全", "双心跳",
        "呼吸慢", "呼吸快", "随机呼吸",
        "棋盘追逐", "棋盘渐开", "棋盘波浪", "棋盘闪2",
        "星形收缩", "星形闪", "双星展开",
        "蛇形双", "蛇形宽", "双螺旋反", "双螺旋宽",
        "随机填充2", "随机清除", "随机爆炸",
    ],
}

# 所有预设名称扁平列表
ALL_PRESET_NAMES: List[str] = [
    name for names in BUILTIN_PRESETS.values() for name in names
]


# ─────────────────────────────────────────────────────────────────────────────
#  过渡权重计算（模块级纯函数，从 MatrixSlideEngine._compute_transition_weights
#  拆出；逐行搬移、语义零变化，供 engine 与测试直接调用）
# ─────────────────────────────────────────────────────────────────────────────

def _on_off_array(ids, frame) -> np.ndarray:
    """帧 states → float64 0/1 数组（seq 模式沿用旧 bool 语义）。"""
    return np.fromiter(
        (1.0 if frame.states.get(fid, False) else 0.0 for fid in ids),
        dtype=np.float64, count=len(ids))


def _seq_transition(ids, from_frame, to_frame, alpha, mode) -> Dict[str, float]:
    """seq_on/seq_off/seq_both：按位置逐个切换（numpy 向量化，逐位不变）。"""
    n = len(ids)
    from_arr = _on_off_array(ids, from_frame)
    to_arr = _on_off_array(ids, to_frame)
    weights = from_arr.copy()
    cutoff = int(alpha * n)
    if mode == TRANS_SEQ_ON:
        # 逐个点亮目标帧中为 ON 的设备
        to_on_idx = np.flatnonzero(to_arr)
        weights[to_on_idx[:cutoff]] = 1.0
    elif mode == TRANS_SEQ_OFF:
        # 逐个熄灭当前帧中为 ON 的设备；目标为 ON 的保持 1.0
        from_on_idx = np.flatnonzero(from_arr)
        sel = from_on_idx[:cutoff]
        weights[sel] = to_arr[sel]
    elif mode == TRANS_SEQ_BOTH:
        # 前半段逐个 OFF，后半段逐个 ON
        if alpha < 0.5:
            cutoff2 = int((alpha * 2) * n)
            from_on_idx = np.flatnonzero(from_arr)
            sel = from_on_idx[:cutoff2]
            weights[sel] = 0.0
        else:
            weights[:] = 0.0
            cutoff2 = int(((alpha - 0.5) * 2) * n)
            to_on_idx = np.flatnonzero(to_arr)
            weights[to_on_idx[:cutoff2]] = 1.0
    return dict(zip(ids, weights.tolist()))


def _wipe_progress(mode, nx: float, ny: float) -> float:
    """擦除进度（归一化坐标 → 0..1 分界线位置）。"""
    if mode == TRANS_WIPE_LR:
        return nx
    if mode == TRANS_WIPE_RL:
        return 1.0 - nx
    if mode == TRANS_WIPE_TB:
        return ny
    if mode == TRANS_WIPE_BT:
        return 1.0 - ny
    cx, cy = 0.5, 0.5
    if mode == TRANS_WIPE_OUT:
        return math.sqrt((nx - cx)**2 + (ny - cy)**2) * 1.5
    return 1.0 - math.sqrt((nx - cx)**2 + (ny - cy)**2) * 1.5


# R194/P2-13：wipe 过渡 alpha 无关量缓存（几何 + 帧状态向量）。
# 键契约与 _tick_hold 的 _hold_output_sig 一致（:3019-3022 在码契约）：
# 面板编辑走整表替换 → id 必变 → 缓存失效；states 就地写仅发生在播放前。
# 几何摘要取首/中/尾坐标（O(1)）；set_fixtures 整表替换列表 → id(fixtures) 变。
_WIPE_GEO_CACHE: Dict[str, tuple] = {}     # 单槽：sig → (slots, nx, ny, progress_by_mode)
_WIPE_STATE_CACHE: Dict[int, tuple] = {}   # id(frame) → (states身份, ids身份, 0/1 向量)
_FADE_LEVEL_CACHE: Dict[int, tuple] = {}   # id(frame) → (states身份, ids身份, level 向量)


def _wipe_geo_signature(fixtures) -> tuple:
    """fixtures 廉价签名（O(1)，不逐灯扫描）：id + len + 首中尾坐标。

    局限（与 `_hold_output_sig` 同级的契约假设）：播放期间仅就地改中间某台
    pos 不重建列表时检测不到；面板编辑路径走 set_fixtures 整表替换 → id 变。
    """
    if not fixtures:
        return (0, 0, None, None, None, None, None, None, None)
    f0, fm, fl = fixtures[0], fixtures[len(fixtures) // 2], fixtures[-1]
    return (id(fixtures), len(fixtures), f0.get("pos_x"), f0.get("pos_y"),
            fm.get("pos_x"), fm.get("pos_y"), fl.get("pos_x"), fl.get("pos_y"))


def _wipe_progress_vec(mode, nx, ny):
    """`_wipe_progress` 的向量化（同分支序、同运算序，保证逐位一致）。"""
    if mode == TRANS_WIPE_LR:
        return nx
    if mode == TRANS_WIPE_RL:
        return 1.0 - nx
    if mode == TRANS_WIPE_TB:
        return ny
    if mode == TRANS_WIPE_BT:
        return 1.0 - ny
    dist = np.sqrt((nx - 0.5) ** 2 + (ny - 0.5) ** 2) * 1.5
    if mode == TRANS_WIPE_OUT:
        return dist
    return 1.0 - dist


def _align_norm_coords(slots, nx, ny):
    """归一化坐标重排到 ids 对齐（缺槽位填 0.5 占位，progress 阶段置 inf 永不选中）。"""
    if nx.size == 0:
        return np.full(slots.shape, 0.5), np.full(slots.shape, 0.5)
    safe = np.maximum(slots, 0)
    return np.where(slots >= 0, nx[safe], 0.5), np.where(slots >= 0, ny[safe], 0.5)


def _wipe_geometry(ids, fixtures):
    """按 fixtures/ids 身份缓存 fid→槽位映射与归一化坐标（alpha 无关）。

    返回 (slots, nx, ny, progress_by_mode)；slots 与 `ids` 对齐，
    无 fixture 的 fid 槽位为 -1。
    """
    sig = (_wipe_geo_signature(fixtures), id(ids), len(ids))
    hit = _WIPE_GEO_CACHE.get("v")
    if hit is not None and hit[0] == sig:
        return hit[1]
    fid_to_slot: Dict[object, int] = {}
    for idx, fx in enumerate(fixtures):
        fid = fx.get("id")
        if fid is not None and fid not in fid_to_slot:
            fid_to_slot[fid] = idx
    xs = [fx.get("pos_x", 0.0) for fx in fixtures]
    ys = [fx.get("pos_y", 0.0) for fx in fixtures]
    if xs:
        mn_x, mx_x, mn_y, mx_y = min(xs), max(xs), min(ys), max(ys)
        nx = (np.asarray(xs, dtype=np.float64) - mn_x) / max(1e-6, mx_x - mn_x)
        ny = (np.asarray(ys, dtype=np.float64) - mn_y) / max(1e-6, mx_y - mn_y)
    else:
        nx = ny = np.zeros(0, dtype=np.float64)
    slots = np.fromiter((fid_to_slot.get(fid, -1) for fid in ids),
                        dtype=np.int64, count=len(ids))
    nx_ids, ny_ids = _align_norm_coords(slots, nx, ny)
    geo = (slots, nx_ids, ny_ids, {})
    if len(_WIPE_GEO_CACHE) > 4:  # 多引擎/多面板共存时的容量护栏
        _WIPE_GEO_CACHE.clear()
    _WIPE_GEO_CACHE["v"] = (sig, geo)
    return geo


def _wipe_frame_states(ids, frame) -> np.ndarray:
    """帧 ON/OFF 向量缓存（wipe 用 bool 旧语义，非 state_level）。"""
    states = getattr(frame, "states", {}) or {}
    entry = (id(states), len(states), id(ids), len(ids))
    hit = _WIPE_STATE_CACHE.get(id(frame))
    if hit is not None and hit[0] == entry:
        return hit[1]
    arr = _on_off_array(ids, frame)
    if len(_WIPE_STATE_CACHE) > 8:
        _WIPE_STATE_CACHE.clear()
    _WIPE_STATE_CACHE[id(frame)] = (entry, arr)
    return arr


def _wipe_transition(ids, fixtures, from_frame, to_frame, *,
                     alpha, mode) -> Dict[str, float]:
    """wipe_*：按位置排序、alpha 决定分界线（一次性 id 索引 + 归一化坐标）。

    R194/P2-13：每 tick 原本重建 fid_to_fx/xs/ys/min-max + 逐灯位置运算；
    现几何（槽位/归一化坐标/progress 向量）与帧状态向量均为 alpha 无关量
    走身份缓存，每 tick 只剩 `progress <= alpha` 的向量比较 + np.where。
    弃"缓存权重"：权重依赖每 tick alpha，不可缓存。缺槽位 progress=inf →
    恒取 from 帧（与旧缺灯分支逐位一致）。
    """
    slots, nx, ny, prog_map = _wipe_geometry(ids, fixtures)
    progress = prog_map.get(mode)
    if progress is None:
        progress = _wipe_progress_vec(mode, nx, ny)  # ids 对齐，缺槽占位 0.5
        progress[slots < 0] = np.inf  # 缺槽恒取 from 帧（与旧缺灯分支一致）
        prog_map[mode] = progress
    from_arr = _wipe_frame_states(ids, from_frame)
    to_arr = _wipe_frame_states(ids, to_frame)
    vals = np.where(progress <= alpha, to_arr, from_arr)
    return dict(zip(ids, vals.tolist()))


def _fade_state_levels(ids, frame) -> np.ndarray:
    """fade 的 state_level 向量缓存（每 tick 两次 np.fromiter → 命中零重建）。"""
    states = getattr(frame, "states", {}) or {}
    entry = (id(states), len(states), id(ids), len(ids))
    hit = _FADE_LEVEL_CACHE.get(id(frame))
    if hit is not None and hit[0] == entry:
        return hit[1]
    arr = np.fromiter(
        (state_level(states.get(fid, 0)) for fid in ids),
        dtype=np.float64, count=len(ids))
    if len(_FADE_LEVEL_CACHE) > 8:
        _FADE_LEVEL_CACHE.clear()
    _FADE_LEVEL_CACHE[id(frame)] = (entry, arr)
    return arr


def _fade_transition(ids, from_frame, to_frame, alpha, ease) -> Dict[str, float]:
    """fade：灰度逐灯 lerp（bool 旧语义 1.0/0.0 逐位不变）。

    R194/P2-13 顺带：from/to 的 state_level 向量按帧身份缓存（alpha 无关），
    每 tick 只剩 lerp 与缓动查表；lerp 运算式逐字未动。
    """
    from_arr = _fade_state_levels(ids, from_frame)
    to_arr = _fade_state_levels(ids, to_frame)
    eff_alpha = alpha
    if ease != "linear":
        try:
            from .output_shaper import curve_value
            eff_alpha = curve_value(ease, alpha)
        except Exception:
            eff_alpha = alpha
    vals = from_arr * (1.0 - eff_alpha) + to_arr * eff_alpha
    return dict(zip(ids, vals.tolist()))


# ─────────────────────────────────────────────────────────────────────────────
#  播放引擎
# ─────────────────────────────────────────────────────────────────────────────

class MatrixSlideEngine:
    """
    矩阵 ON/OFF 幻灯片播放引擎（纯逻辑，不依赖 Qt）

    使用方式：
        engine = MatrixSlideEngine()
        engine.set_nm(nm)           # 注入 NetworkManager
        engine.set_fixtures(...)    # 设置设备列表（含位置和通道信息）
        engine.slides = [...]       # 设置幻灯片序列
        engine.play()               # 开始播放
        engine.tick(dt)             # 每帧调用（dt=秒）
    """

    def __init__(self):
        self.slides: List[MatrixSlide] = []
        self.config = MatrixSlideshowConfig()

        # 设备列表（含位置和通道信息）
        # 每个元素：{"id": str, "universe": int, "start_channel": int,
        #            "pos_x": float, "pos_y": float, "channels": List[str]}
        self._fixtures: List[Dict] = []
        # 按位置排序后的 fixture_ids（用于生成预设）
        self._sorted_ids: List[str] = []
        # 大量设备性能缓存：{ch_type: [(fixture_id, universe, dmx_addr0)]}
        self._channel_addr_cache: Dict[str, List[tuple]] = {}

        # 播放状态
        self._playing: bool = False
        self._current_idx: int = 0
        self._pingpong_dir: int = 1
        self._frame_elapsed: float = 0.0

        # 过渡状态
        self._in_transition: bool = False
        self._trans_elapsed: float = 0.0
        self._trans_from_states: Dict[str, float] = {}  # {fid: 0.0~1.0}
        self._trans_to_idx: int = 0

        # NM 引用（外部注入）
        self._nm = None

        #  深查②三#7 P1 灰度过渡缓动曲线（linear=旧行为；可换 EASE_FUNCS_V 键）
        self.fade_ease: str = "linear"

        #  深查②三#8 P2 每分区输出 gamma（config.output_gamma 驱动；关闭=透传）
        self._output_gamma_luts: List[np.ndarray] = []
        self._output_gamma_fid_zone: Dict[str, int] = {}

        # 回调：帧切换通知（供 UI 更新缩略图高亮）
        self.on_frame_changed: Optional[Callable[[int], None]] = None
        # 回调：播放结束通知
        self.on_play_finished: Optional[Callable[[], None]] = None
        # 回调：每次实际输出的权重，供 UI 预览使用
        self.on_output: Optional[Callable[..., None]] = None

    # ── 设备管理 ──────────────────────────────────────────────────────────────

    def set_fixtures(self, fixtures: List[Dict], *, fixture_defs=None):
        """设置设备列表，保留调用方传入的设备组/导入顺序。"""
        set_engine_profile_definitions(self, fixture_defs)
        self._fixtures = list(fixtures)
        self._sorted_ids = [f["id"] for f in self._fixtures if "id" in f]
        self._rebuild_channel_addr_cache()
        self._rebuild_output_gamma_zone()

    def _rebuild_output_gamma_zone(self) -> None:
        """按 config.output_gamma + 当前设备 pos_y 重建分区 LUT/映射缓存。"""
        from .matrix_output_lut import build_zone_luts, zone_assignment
        cfg = None
        try:
            cfg = normalize_zone_gamma_dict(getattr(self.config, "output_gamma", None))
        except Exception:
            cfg = None
        if not cfg or not cfg.get("enabled"):
            self._output_gamma_luts = []
            self._output_gamma_fid_zone = {}
            return
        self._output_gamma_luts = build_zone_luts(cfg["gammas"])
        _arr, fid_zone = zone_assignment(self._fixtures, int(cfg.get("zone_count", 2)))
        self._output_gamma_fid_zone = fid_zone

    def set_output_gamma(self, cfg) -> None:
        """设置分区 gamma 配置（规整后写回 config 并重建缓存）。"""
        self.config.output_gamma = normalize_zone_gamma_dict(cfg)
        self._rebuild_output_gamma_zone()

    def apply_output_gamma_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        """帧输出权重按分区查表（未启用/异常 → 原样透传）。"""
        luts = self._output_gamma_luts
        if not luts or not weights:
            return weights
        try:
            from .matrix_output_lut import apply_zone_gamma_weights
            return apply_zone_gamma_weights(
                weights, self._output_gamma_fid_zone, luts)
        except Exception:
            # 清查⑧：本模块无 logger 名（原 `logger.debug` 让异常处理自身 NameError）
            import logging
            logging.getLogger(__name__).debug(
                "matrix: slide engine zone gamma failed", exc_info=True)
            return weights

    def _rebuild_channel_addr_cache(self):
        install_engine_maps(self)

    def get_sorted_ids(self) -> List[str]:
        return list(self._sorted_ids)

    def set_nm(self, nm):
        self._nm = nm

    # ── 幻灯片管理 ────────────────────────────────────────────────────────────

    def add_slide(self, slide: Optional[MatrixSlide] = None, after_idx: int = -1) -> int:
        """添加幻灯片，返回新帧索引"""
        if slide is None:
            slide = MatrixSlide(name=f"帧 {len(self.slides) + 1}")
            # 默认所有设备 OFF
            for fid in self._sorted_ids:
                slide.states[fid] = False
        insert_at = after_idx + 1 if 0 <= after_idx < len(self.slides) else len(self.slides)
        self.slides.insert(insert_at, slide)
        if self._current_idx >= insert_at:
            self._current_idx += 1
        return insert_at

    def remove_slide(self, idx: int):
        if 0 <= idx < len(self.slides):
            self.slides.pop(idx)
            if self._current_idx >= len(self.slides):
                self._current_idx = max(0, len(self.slides) - 1)

    def move_slide(self, idx: int, direction: int):
        new_idx = idx + direction
        if 0 <= new_idx < len(self.slides):
            self.slides[idx], self.slides[new_idx] = self.slides[new_idx], self.slides[idx]
            if self._current_idx == idx:
                self._current_idx = new_idx

    def duplicate_slide(self, idx: int) -> int:
        if 0 <= idx < len(self.slides):
            clone = self.slides[idx].clone()
            clone.name += " (副本)"
            self.slides.insert(idx + 1, clone)
            return idx + 1
        return idx

    def load_preset(self, preset_name: str, hold: float = 0.5, trans: float = 0.2,
                     wings: int = 1, block_size: int = 1,
                     wing_direction: str = WING_DIRECTION_LOW_TO_HIGH,
                     density: float = 0.35, *, sequence_options: dict | None = None,
                 wing_mirror: bool = False, wing_reverse: bool = False):
        """加载内置预设，替换当前序列"""
        from .matrix_ppt_composition_sequences import load_sequence_frames
        load_sequence_frames(self, preset_name, hold=hold, trans=trans,
                             wings=wings, block_size=block_size, wing_direction=wing_direction,
                             density=density, sequence_options=sequence_options,
                             wing_mirror=wing_mirror, wing_reverse=wing_reverse)

    # ── 播放控制 ──────────────────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_idx(self) -> int:
        return self._current_idx

    def play(self):
        if not self.slides:
            return
        self._playing = True
        self._frame_elapsed = 0.0
        self._in_transition = False

    def stop(self):
        self._playing = False
        self._in_transition = False
        # 清空 NM matrix 层
        self._clear_nm_layer()
        #  正确性(审查M2)：stop 清空输出层后重放同帧，停留帧签名必须失效，
        #   否则首 tick 签名相同被跳过 → 画面保持黑/关直到切帧。
        self._hold_output_sig = None

    def pause(self):
        self._playing = False

    def resume(self):
        if self.slides:
            self._playing = True

    def goto(self, idx: int):
        """跳转到指定帧（立即输出，不过渡）"""
        if 0 <= idx < len(self.slides):
            self._current_idx = idx
            self._frame_elapsed = 0.0
            self._in_transition = False
            self._output_current_frame_direct()
            if self.on_frame_changed:
                self.on_frame_changed(idx)

    def step_next(self):
        """手动步进到下一帧"""
        if not self.slides:
            return
        next_idx = self._compute_next_idx()
        if next_idx >= 0:
            self._start_transition(self._current_idx, next_idx)
        if self.on_frame_changed:
            self.on_frame_changed(self._current_idx)

    def step_prev(self):
        """手动步进到上一帧"""
        if not self.slides:
            return
        n = len(self.slides)
        prev_idx = (self._current_idx - 1) % n
        self._start_transition(self._current_idx, prev_idx)
        if self.on_frame_changed:
            self.on_frame_changed(self._current_idx)

    # ── 主 tick（每帧调用） ───────────────────────────────────────────────────

    def tick(self, dt: float):
        """dt: 距上次调用的秒数"""
        if not self._playing or not self.slides:
            return

        if self._in_transition:
            self._tick_transition(dt)
        else:
            self._tick_hold(dt)

    def _tick_hold(self, dt: float):
        """停留阶段"""
        frame = self.slides[self._current_idx]
        self._frame_elapsed += dt

        #  性能：停留阶段帧状态每 tick 不变（hold 持续数秒），输出与上次
        #   逐字节相同——仅当 帧/状态集/输出值/序号 变化时下发，不再每 tick
        #   重建 11382 项权重字典并全量走输出管道。
        on_val = (self.config.global_on_value
                  if self.config.global_on_value >= 0 else frame.on_value)
        off_val = (self.config.global_off_value
                   if self.config.global_off_value >= 0 else frame.off_value)
        # 签名含 states 对象身份+长度：面板编辑走整表替换（5098/5102）→ id 必变
        #   → 重发 ✓；states 就地写仅发生在创建时点（371/2610，播放前）。
        _sig = (id(frame), id(frame.states), len(getattr(frame, "states", {}) or {}),
                self._current_idx, on_val, off_val)
        if _sig != getattr(self, "_hold_output_sig", None):
            self._hold_output_sig = _sig
            self._output_current_frame_direct()

        # 检查是否需要切换（ BPM 量化：开启后停留时长 = 60/bpm × 拍数）
        effective_hold = self.config.effective_hold_time(frame)
        if effective_hold > 0 and self._frame_elapsed >= effective_hold:
            next_idx = self._compute_next_idx()
            if next_idx < 0:
                # 播放结束（单次模式）
                self._playing = False
                if self.on_play_finished:
                    self.on_play_finished()
                return
            self._start_transition(self._current_idx, next_idx)

    def _tick_transition(self, dt: float):
        """过渡阶段"""
        self._trans_elapsed += dt
        from_frame = self.slides[self._current_idx]
        to_frame = self.slides[self._trans_to_idx]
        trans_dur = max(0.001, to_frame.transition_time)
        alpha = min(1.0, self._trans_elapsed / trans_dur)

        # 计算过渡权重
        weights = self._compute_transition_weights(
            from_frame, to_frame, alpha)

        # 输出过渡帧（ #8：分区 gamma 在输出前统一查表）
        self._output_weights(self.apply_output_gamma_weights(weights),
                             to_frame.on_value, to_frame.off_value)

        if alpha >= 1.0:
            # 过渡完成
            self._current_idx = self._trans_to_idx
            self._frame_elapsed = 0.0
            self._in_transition = False
            if self.on_frame_changed:
                self.on_frame_changed(self._current_idx)

    def _start_transition(self, from_idx: int, to_idx: int):
        """开始过渡"""
        to_frame = self.slides[to_idx]
        if to_frame.transition_time <= 0 or to_frame.transition_mode == TRANS_CUT:
            # 立切
            self._current_idx = to_idx
            self._frame_elapsed = 0.0
            self._in_transition = False
            self._output_current_frame_direct()
            if self.on_frame_changed:
                self.on_frame_changed(to_idx)
        else:
            # 开始过渡
            from_frame = self.slides[from_idx]
            # 记录起始状态（浮点权重； 灰度帧按 v/255，bool 兼容 1.0/0.0）
            self._trans_from_states = {
                fid: state_level(from_frame.states.get(fid, 0))
                for fid in self._sorted_ids
            }
            self._trans_to_idx = to_idx
            self._trans_elapsed = 0.0
            self._in_transition = True

    def _compute_transition_weights(
            self, from_frame: MatrixSlide, to_frame: MatrixSlide,
            alpha: float) -> Dict[str, float]:
        """计算过渡中间状态的权重 {fid: 0.0~1.0}（分发到模块级纯函数）。"""
        mode = to_frame.transition_mode
        ids = self._sorted_ids
        if mode == TRANS_FADE:
            # 线性插值（numpy 向量化；float64 与原 Python 浮点逐位一致）
            #  深查②三#7 P1 灰度 crossfade：states 取 `state_level`（bool 旧语义
            #   1.0/0.0 逐位不变；int 0-255 按灰度逐灯 lerp，告别两帧硬切）。
            ease = str(getattr(self, "fade_ease", "linear") or "linear")
            return _fade_transition(ids, from_frame, to_frame, alpha, ease)
        if mode in (TRANS_SEQ_ON, TRANS_SEQ_OFF, TRANS_SEQ_BOTH):
            return _seq_transition(ids, from_frame, to_frame, alpha, mode)
        if mode in (TRANS_WIPE_LR, TRANS_WIPE_RL, TRANS_WIPE_TB,
                    TRANS_WIPE_BT, TRANS_WIPE_OUT, TRANS_WIPE_IN):
            return _wipe_transition(ids, self._fixtures, from_frame, to_frame,
                                    alpha=alpha, mode=mode)
        # 默认立切
        return {fid: 1.0 if to_frame.states.get(fid, False) else 0.0
                for fid in ids}

    def _compute_next_idx(self) -> int:
        """计算下一帧索引，返回 -1 表示播放结束"""
        n = len(self.slides)
        if n == 0:
            return -1
        mode = self.config.play_mode
        if mode == PLAY_LOOP:
            return (self._current_idx + 1) % n
        elif mode == PLAY_ONCE:
            ni = self._current_idx + 1
            return ni if ni < n else -1
        elif mode == PLAY_PINGPONG:
            ni = self._current_idx + self._pingpong_dir
            if ni >= n:
                self._pingpong_dir = -1
                ni = n - 2
            elif ni < 0:
                self._pingpong_dir = 1
                ni = 1
            return max(0, min(n - 1, ni))
        elif mode == PLAY_RANDOM:
            cands = [i for i in range(n) if i != self._current_idx]
            return random.choice(cands) if cands else self._current_idx
        return (self._current_idx + 1) % n

    # ── NM 输出 ───────────────────────────────────────────────────────────────

    def _output_current_frame_direct(self):
        """直接输出当前帧（无插值； 灰度帧按 state_level 输出中间值）"""
        if not (0 <= self._current_idx < len(self.slides)):
            return
        frame = self.slides[self._current_idx]
        on_val = (self.config.global_on_value
                  if self.config.global_on_value >= 0 else frame.on_value)
        off_val = (self.config.global_off_value
                   if self.config.global_off_value >= 0 else frame.off_value)
        #  灰度帧：bool→1.0/0.0（旧语义逐位一致），int→v/255
        weights = {fid: state_level(frame.states.get(fid, 0))
                   for fid in self._sorted_ids}
        #  #8：分区 gamma 在输出前统一查表（未启用=原样透传）
        self._output_weights(self.apply_output_gamma_weights(weights), on_val, off_val)

    def _output_weights(self, weights: Dict[str, float],
                        on_value: int = 255, off_value: int = 0):
        """将权重字典输出到 NM matrix 层"""
        try:
            if self.on_output:
                self.on_output(dict(weights), int(on_value), int(off_value))
                return

            if self._nm is None:
                return
            write_engine_weights(self, weights, on_value, off_value)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("MatrixSlideEngine output error: %s", e)

    def _clear_nm_layer(self):
        """清空 NM matrix 层"""
        if self._nm is None:
            return
        try:
            if hasattr(self._nm, "clear_layer"):
                self._nm.clear_layer("matrix")
        except Exception:
            # R181 P2-3：清层失败 = 停止播放/关闭矩阵后残留输出（灯不灭），留痕
            import logging
            logging.getLogger(__name__).debug(
                "清空 NM matrix 层失败（停止播放后可能残留输出）", exc_info=True)

    # ── 序列化 ────────────────────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        return {
            "config": self.config.to_dict(),
            "slides": [s.to_dict() for s in self.slides],
            "current_idx": self._current_idx,
        }

    def from_dict(self, d: Dict):
        self.config = MatrixSlideshowConfig.from_dict(d.get("config", {}))
        self.slides = [MatrixSlide.from_dict(sd) for sd in d.get("slides", [])]
        self._current_idx = min(d.get("current_idx", 0), max(0, len(self.slides) - 1))
        self._playing = False
        self._in_transition = False
        self._rebuild_output_gamma_zone()   # config 被整体替换 → 分区 gamma 缓存重建
        refresh_profile_maps(self)
