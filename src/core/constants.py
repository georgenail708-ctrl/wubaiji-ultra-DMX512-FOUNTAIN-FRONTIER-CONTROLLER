"""跨模块共享的纯数据常量 —— **唯一真源**（2026-09-12 P1-1）。

存在理由：审计发现同一常量散落多份（电机粗精映射 2-3 份、颜色别名 3 份、
层名集合 7+ 处字面量、导入缩放 2 份），"改一处漏一处"是历史上
"某页有反应、另一页没有"类 bug 的根源。此处收敛后，UI 与 engine 都 import 本模块。

纪律（写进 AGENTS.md）：
  - 本模块**只放纯数据**（list/dict/frozenset/数值），不 import 任何项目模块，无副作用。
  - 其它模块**不得**再定义同义常量；需要对外暴露时用 `from ..core.constants import X`
    再在本地名字空间可见即可（保持既有 import 路径不破）。
  - 新增共享常量一律加到这里，不要贴在某个业务模块里。
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
#  电机粗/精通道（16bit 拆分）——原散落于 xy_reference / track_linker_engine /
#  self_map_engine 三处（后两者逐字节相同）
# ─────────────────────────────────────────────────────────────────────────────
MOTOR_FINE_CHANNELS: dict[str, str] = {
    "motor_x": "motor_x_fine",
    "motor_y": "motor_y_fine",
    "motor_z": "motor_z_fine",
}
MOTOR_FINE_TO_COARSE: dict[str, str] = {
    fine: coarse for coarse, fine in MOTOR_FINE_CHANNELS.items()
}
# 低值覆盖"停放检测"关注的电机源类型全集（含 fine）
MOTOR_SRC_TYPES = frozenset({
    "motor_x", "motor_y", "motor_z",
    "motor_x_fine", "motor_y_fine", "motor_z_fine",
})

# ─────────────────────────────────────────────────────────────────────────────
#  通道类型颜色别名 ——原散落于 network_manager / track_linker_engine /
#  track_linker_panel 三处（内容相同）
# ─────────────────────────────────────────────────────────────────────────────
CH_COLOR_ALIASES: dict[str, str] = {
    "red": "r",
    "green": "g",
    "blue": "b",
    "white": "w",
    "intensity": "dimmer",
}

# ─────────────────────────────────────────────────────────────────────────────
#  通道类型中文显示名 + 参数分组 ——原散落于 programmer_panel.CH_LABELS /
#  track_linker_panel.CH_LABELS / matrix_slideshow._MATRIX_CH_LABELS / ofl_importer
#  四份（2026-09-13 调度台表格重做时收敛）。
#
#  ★ 为什么放这里：调度台表格视图要按"值类型"生成列头，它位于 processing 层
#  （不得 import ui，见 CLAUDE.md 10.2 反层反转），又必须与 Programmer 页显示同一
#  套名字——不收敛就只能再造一份副本（正是"语义双源"易损点）。此处为唯一真源，
#  UI 与 processing 共用；老代码的本地字典保持不动（最小改动），由
#  tests/test_constants_single_source.py 的等价断言守护不漂移。
# ─────────────────────────────────────────────────────────────────────────────
CHANNEL_LABELS: dict[str, str] = {
    "dimmer": "亮度 Dimmer", "R": "红 R", "G": "绿 G", "B": "蓝 B",
    "W": "白 W", "valve": "阀 Valve", "motor_x": "电机 X",
    "motor_y": "电机 Y", "motor_z": "电机 Z",
    "motor_x_fine": "电机 X Fine", "motor_y_fine": "电机 Y Fine",
    "motor_z_fine": "电机 Z Fine",
    "pump": "泵 Pump",
    "placeholder": "占位",
}
#: 参数分组（列头"分组条"的标题与顺序）——顺序即表格列的分组次序。
CHANNEL_PARAM_GROUPS: list = [
    ("Intensity",  ["dimmer"]),
    ("Color",      ["R", "G", "B", "W"]),
    ("Motor XYZ",  ["motor_x", "motor_y", "motor_z"]),
    ("Valve",      ["valve"]),
    ("Pump",       ["pump"]),
]

# ─────────────────────────────────────────────────────────────────────────────
#  混音层分类 ——原定义于 network_manager，被 record_engine / track_linker_engine /
#  main_window / programmer_panel 以**字面量**重复书写
# ─────────────────────────────────────────────────────────────────────────────
# BASE 层 = 绝对值层，列表顺序 = LTP 优先级（靠后覆盖靠前）
BASE_LAYERS: list[str] = ["fader", "programmer", "tester", "linker"]
# SHAPE 层 = 动态形状/调制层，以 (value − anchor) 叠加到 base
#   ml_lab = ML 实验室（外部编曲模块）专用输出层，与 effect_auto（AI 编舞引擎）
#   同构：绝对直出 + 按通道 LTP 与 base 层仲裁，逐帧整帧写入真实设备通道。
#   ★ 刻意不进 CLIP_LAYERS：clip 的最终覆盖循环是硬编码元组
#     ("timeline","clip_playback","clip_preview")（network_manager._mix_one_universe），
#     新层名加进去会被静默忽略——这是本项目的实际陷阱，勿改。
SHAPE_LAYERS: list[str] = ["effect", "effect_auto", "matrix", "matrix_audio",
                           "audio", "joystick", "clip_traj", "ml_lab"]
# CLIP 层 = 播放/预览覆盖层（timeline 为旧版兼容层）
CLIP_LAYERS: list[str] = ["clip_playback", "clip_preview", "timeline"]
ALL_LAYERS: list[str] = BASE_LAYERS + SHAPE_LAYERS + CLIP_LAYERS

# 直接模式下代表"绝对 DMX 值"的形状层（不做 base 相对化，否则静态波形塌陷）
ABSOLUTE_DIRECT_SHAPE_LAYERS: frozenset = frozenset(
    {"effect", "effect_auto", "matrix", "audio", "matrix_audio", "ml_lab"})
AUDIO_HTP_SHAPE_LAYERS: frozenset = frozenset({"audio", "matrix_audio"})

# 静态编辑器层（用户手写值、非运动）：低值覆盖/停放判定用
STATIC_EDITOR_LAYERS: frozenset = frozenset({"programmer", "fader", "tester"})
# 编辑器 LTP 层（含 linker）：关联器快照取 LTP 时间时排除，避免静态停放压过动效
EDITOR_LTP_LAYERS: frozenset = frozenset({"fader", "programmer", "tester", "linker"})

# ─────────────────────────────────────────────────────────────────────────────
#  导入/画布标定 ——原定义于 import_patch_csv，playback_engine 内又抄一份
#  （借用 ui 模块 = 层反转，收敛到此处同时修掉该反转）
# ─────────────────────────────────────────────────────────────────────────────
CANVAS_UNITS_PER_CM: float = 1.0   # 1 画布单位 = 1 CSV 厘米
Y_AXIS_SIGN: float = -1.0          # CSV +Z 朝上 → 画布 y 向下，取负
DEFAULT_IMPORT_SCALE: float = 0.4  # 导入整体缩放（坐标偏大，压到 ~0.4×）

# ─────────────────────────────────────────────────────────────────────────────
#  特殊类型通道：绝对位置/升降电机（2026-09-12）
#
#  常规连续电机（TFeature_MotorPan / MotorTilt / MotorYaw，旋转类）的
#  DMX 归中值是 128（≈0°），空闲/播放前默认输出 128。
#  但升降/卷扬类电机（TFeature_MoveMotor，如 LED TUBE / Winch LED +
#  Tube 的 Height 轴）是**绝对位置**语义：默认应在底面 = 0，而不是归中 128。
#  源工程已把该值写在 ControlData.DMXHomeValue（实测 0），导入器也保留了 0，
#  因此这类通道必须**取设备定义的显式默认值**，不能按电机归中 128 覆盖。
#
#  这里用**通道类型集合**表达该特殊语义（不是按设备名/GUID 散落 if）：
#  导入层（fixture_type_to_channels）对集合内通道即使默认值为 0 也显式记录；
#  输出层（network_manager.set_fixture_defaults_from_project）对集合内通道
#  优先取设备定义默认值，缺失才回退归中 128（旧/手动定义 = 行为不变）。
#  含 motor_z 是因为 的 Z 轴既可能是"自转/扇面"归中类（MotorYaw，
#  源默认 128，取默认仍是 128），也可能是"升降"绝对类（MoveMotor，源默认 0）。
# ─────────────────────────────────────────────────────────────────────────────
ABSOLUTE_HOME_MOTOR_CHANNELS: frozenset = frozenset({"motor_z"})
MOTOR_CENTER_HOME: int = 128   # 常规电机归中默认值
MOTOR_FINE_HOME: int = 0       # 16bit 精调通道默认值（粗值即值）

# ─────────────────────────────────────────────────────────────────────────────
#  后处理滤网的**通道分类真源**（2026-09-20 R110 新增）
#  原先"灯通道"在 UI（programmer_panel._is_light_channel，只含 RGBW）、
#  processing（fixture_table.LIGHT_CHANNELS）、引擎（灯跟踪字面量）各写一份，
#  且**都不含 dimmer**。三个后处理滤网需要统一的判定 → 收口到这里
#  （engine 不得 import src.ui，故真源必须放 core）。
# ─────────────────────────────────────────────────────────────────────────────
#: 灯输出通道（"灯禁用"滤网的作用集）：RGBW + dimmer（含颜色别名归一）
LIGHT_OUTPUT_CHANNEL_TYPES: frozenset = frozenset(
    {"r", "g", "b", "w", "dimmer"} | set(CH_COLOR_ALIASES.keys())
)
#: 泵通道（"泵主控"滤网的作用集）。`valve` 是独立通道类型（泄气阀），
#: 默认不并入；如现场需要把阀一起压，由调用方传 extra 集合并入。
PUMP_CHANNEL_TYPES: frozenset = frozenset({"pump"})
#: 电机**粗**通道（限位/限速的幅度作用点；精通道随粗通道同系数缩放）
MOTOR_COARSE_CHANNELS: frozenset = frozenset(MOTOR_FINE_CHANNELS.keys())
