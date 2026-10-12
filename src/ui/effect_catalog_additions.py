"""EF-M01: operator gaps, with one useful schema per capability."""
from .effect_music_kernels import kernel_for


# key, label, default, minimum, maximum, tooltip. Integer defaults imply int.
SPECS = {
    "attack": ("进入秒数", 0.25, 0.02, 3.0, "从零到目标值的时间；增大可放慢进入。"),
    "hold": ("保持秒数", 0.6, 0.02, 3.0, "保持满幅或事件记忆的时间。"),
    "release": ("回落秒数", 0.5, 0.02, 3.0, "从目标值回落的时间；增大可延长尾部。"),
    "rest": ("间歇秒数", 0.4, 0.0, 3.0, "退出后保持空场的时间。"),
    "group_size": ("每组台数", 4, 1, 32, "相邻多少台设备一起交接。"),
    "stagger": ("交接间隔", 0.3, 0.02, 2.0, "下一组开始的间隔；短于当前组时长会交叠。"),
    "cycle_a": ("奇数组周期", 0.6, 0.1, 3.0, "奇数设备的独立节拍周期，单位秒。"),
    "cycle_b": ("偶数组周期", 0.9, 0.1, 3.0, "偶数设备的独立节拍周期，单位秒。"),
    "duty_a": ("奇数组亮时", 0.35, 0.05, 0.95, "奇数设备每周期点亮的比例。"),
    "duty_b": ("偶数组亮时", 0.45, 0.05, 0.95, "偶数设备每周期点亮的比例。"),
    "mix": ("两拍混合", 0.15, 0.0, 0.5, "把另一组节拍混入本组；0保持两组独立。"),
    "steps": ("循环步数", 8, 2, 16, "一次编排循环包含多少步。"),
    "accent_every": ("重拍间隔", 4, 2, 8, "每隔多少步出现一个重拍。"),
    "accent_level": ("重拍幅度", 1.0, 0.1, 1.0, "重拍的输出幅度。"),
    "weak_level": ("弱拍幅度", 0.35, 0.0, 1.0, "普通步的输出幅度。"),
    "duty": ("点亮比例", 0.55, 0.05, 0.95, "每步点亮的时间比例。"),
    "rate": ("每秒步数", 3.0, 0.2, 8.0, "每秒向下一步推进多少次。"),
    "band": ("频段序号", 0, 0, 7, "0为最低频，7为最高频；只跟随选定频段。"),
    "threshold": ("触发阈值", 0.35, 0.05, 0.95, "越高需要越强的起音或频段变化才触发。"),
    "travel": ("行进秒数", 0.8, 0.15, 3.0, "一次起音从序列首走到尾的时间。"),
    "width": ("亮带宽度", 0.14, 0.03, 0.6, "亮带占设备序列的比例；与整体效果宽窄独立。"),
    "echo_delay": ("回声间隔", 0.25, 0.08, 1.0, "一次起音之后各次回声的间隔秒数。"),
    "echo_count": ("回声次数", 3, 2, 6, "一次起音形成多少次依次减弱的回声。"),
    "decay": ("尾部衰减", 0.6, 0.08, 0.95, "回声幅度保留比例；谱变效果中为衰减秒数。"),
    "history_seconds": ("回看秒数", 1.0, 0.15, 3.0, "序列尾部展示多久之前的真实频谱。"),
    "band_count": ("展示频段", 8, 2, 8, "按序列交织展示最低开始的多少个频段。"),
    "contrast": ("细节对比", 2.0, 0.3, 5.0, "扩大频谱细节或相对近期背景的音量差。"),
    "interval_scale": ("满幅音程", 12.0, 2.0, 24.0, "达到满幅展开所需的音高跳进，单位半音。"),
    "transition": ("换景秒数", 0.7, 0.1, 2.0, "检测到和弦变化后，新和声图案擦入的时间。"),
    "background": ("背景幅度", 0.12, 0.0, 0.7, "未激活区域保留的输出幅度。"),
    "beats_per_bar": ("每句拍数", 4, 2, 8, "检测到多少次真实拍相位过零后重新从首位开始。"),
    "accent": ("普通拍幅度", 0.55, 0.1, 1.0, "首拍满幅，其余各拍采用此幅度。"),
    "rest_seconds": ("间隙等待", 0.6, 0.1, 2.0, "连续多久没有新起音才开始收束。"),
    "margin": ("对答门槛", 0.1, 0.02, 0.7, "低高频差超过此值才换侧；接近时保留上一侧。"),
    "window": ("背景窗口", 1.0, 0.15, 3.0, "用多久的近期平均音量作为背景参照。"),
    "gain": ("变化增益", 3.0, 0.5, 6.0, "放大真实频谱差分；平稳输入保持无事件。"),
}

DEFINITIONS = (
    ("phrase_envelope", "四段呼吸", "自由设置进入、保持、退出、间歇，适合安静场景与段落衔接。", "通用/渐变", "基础", "attack hold release rest curve"),
    ("group_overlap_relay", "交叠接力", "相邻组按独立进入与退出时间交接，可留空隙或连续交叠。", "通用/编排", "进阶", "group_size attack hold release stagger"),
    ("dual_clock_weave", "双拍交织", "奇偶设备跟随不同周期，两组节拍可独立或少量混合。", "通用/编排", "进阶", "cycle_a cycle_b duty_a duty_b mix"),
    ("accent_step_cycle", "重音步进", "按步轮流点亮，自由安排重拍间隔与强弱幅度。", "通用/编排", "进阶", "steps accent_every accent_level weak_level duty rate"),
    ("audio_peak_memory", "峰值记忆", "保留真实声音尖峰，再缓缓回落，短促细节不会一闪消失。", "通用/音频", "基础", "hold release"),
    ("audio_band_envelope", "频段柔响", "指定频段控制输出，进入与回落速度分别可调。", "通用/音频", "基础", "band attack release"),
    ("audio_onset_relay", "音落接力", "每次真实起音发出独立亮带，声音结束后继续走完序列。", "通用/音频", "进阶", "threshold travel width"),
    ("audio_onset_echo", "音落回声", "一次真实起音触发多次错位回声，按设定间隔逐次减弱。", "通用/音频", "进阶", "threshold echo_delay echo_count decay"),
    ("audio_spectrum_memory", "谱纹记忆", "把过去的真实频谱铺成时间轨迹，可看出声音纹理的变化。", "通用/音频", "进阶", "history_seconds band_count contrast"),
    ("audio_interval_open", "音程展开", "旋律向上跳进从中心展开，向下跳进从两端收拢。", "通用/音频", "高级", "interval_scale hold"),
    ("audio_chord_wipe", "和声换景", "换和弦时擦入新和声图案；稳定和弦保持静驻。", "通用/音频", "高级", "transition background"),
    ("audio_bar_steps", "小节接力", "跟随真实拍相位逐拍接力，句首重拍，其余拍可调强度。", "通用/音频", "进阶", "beats_per_bar accent width"),
    ("audio_phrase_rest", "间隙收束", "乐句间隙后从两端收束，新的起音重新打开空间。", "通用/音频", "高级", "rest_seconds release threshold"),
    ("audio_band_dialogue", "音色对答", "低频与高频轮流主导两侧，音色接近时保留上一侧。", "通用/音频", "进阶", "margin background"),
    ("audio_dynamic_focus", "动态聚焦", "相对近期背景更突出的声音细节，改变亮带的位置和幅度。", "通用/音频", "高级", "window contrast width"),
    ("audio_spectral_change", "谱变扫描", "真实频段能量突增才产生亮点，平稳声音不会凭空运动。", "通用/音频", "高级", "threshold gain decay"),
)


def _spec(key):
    from .effect_engine import ParamSpec
    if key == "curve":
        return ParamSpec(key, "进入曲线", "choice", "平滑", choices=["平滑", "线性"],
                         tooltip="平滑让起落两端变柔；线性保持匀速。")
    label, default, low, high, tooltip = SPECS[key]
    kind = "int" if isinstance(default, int) else "float"
    return ParamSpec(key, label, kind, default, low, high, 1 if kind == "int" else 0.01,
                     tooltip=tooltip)


def register_music_presets(engine):
    from .effect_engine import CH_TYPES, EffectPreset, ParamSpec
    common = [ParamSpec("ch_type", "输出通道", "choice", "dimmer", choices=CH_TYPES,
                        tooltip="选择这些设备上要驱动的物理通道。"),
              ParamSpec("lo", "输出低值", "int", 0, 0, 255, 1, tooltip="输出范围的低端。"),
              ParamSpec("hi", "输出高值", "int", 255, 0, 255, 1, tooltip="输出范围的高端。")]
    for variant, name, description, category, level, keys in DEFINITIONS:
        preset = EffectPreset("r189_" + variant, name, category, description,
                              CH_TYPES, common + [_spec(k) for k in keys.split()],
                              icon="music" if variant.startswith("audio_") else "wave-sine",
                              tags=[level, "音乐编排" if variant.startswith("audio_") else "编排"])
        engine._reg(preset, kernel_for(variant, preset.default_params()))
    return len(DEFINITIONS)
