"""Six distinct music arrangements, with one canonical parameter contract."""
MUSIC_DEFAULTS = {
    "music_history_s": 2.0, "music_steps": 8.0, "music_delay_beats": 0.5,
    "music_line_width": 0.12, "music_pitch_min": 48.0,
    "music_pitch_max": 84.0, "music_pitch_confidence": 0.35,
}
MUSIC_NUMBERS = {
    "music_history_s": ("余音时长", 0.2, 8.0, 0.05, 2, "s"),
    "music_steps": ("编排步数", 2.0, 16.0, 1.0, 0, ""),
    "music_delay_beats": ("呼应间隔", 0.125, 2.0, 0.125, 3, "拍"),
    "music_line_width": ("光带宽度", 0.02, 0.8, 0.01, 2, ""),
    "music_pitch_min": ("音域下界", 24.0, 95.0, 1.0, 0, "MIDI"),
    "music_pitch_max": ("音域上界", 25.0, 108.0, 1.0, 0, "MIDI"),
    "music_pitch_confidence": ("音高可信度", 0.0, 1.0, 0.05, 2, ""),
    "audio_bar_gamma": ("频段 Gamma", 0.1, 3.0, 0.05, 2, ""),
    "audio_bar_smoothing": ("频段平滑", 0.0, 0.95, 0.05, 2, ""),
}
MUSIC_COMBOS = {
    "audio_bar_direction": ("编排方向", (("向上", "bottom"), ("向下", "top"),
                                        ("向右", "left"), ("向左", "right"))),
    "audio_band_layout": ("频段布局", (("低到高", "linear"), ("高到低", "reverse"),
                                      ("低频居中", "center_low"), ("阶梯", "stair"))),
}
_TRAIL = ("music_history_s", "music_line_width", "audio_bar_direction")
_BANDS = ("audio_band_layout", "audio_bar_gamma", "audio_bar_smoothing")
MUSIC_MODES = {
    "audio_music_beat_relay": {
        "name": "节拍接力", "description": "每次主拍向下一区交棒，前一区按秒留下余音。",
        "parameters": _TRAIL + ("music_steps",),
    },
    "audio_music_beat_response": {
        "name": "拍间呼应", "description": "主拍先点亮一侧，按歌曲拍速在对侧回应。",
        "parameters": _TRAIL + ("music_steps", "music_delay_beats"),
    },
    "audio_music_timbre_trail": {
        "name": "音色轨迹", "description": "音色重心与音量画出二维轨迹，保留最近的声音变化。",
        "parameters": _TRAIL + _BANDS,
    },
    "audio_music_beat_weave": {
        "name": "节奏编织", "description": "连续主拍交替写入横线与竖线，形成有余音的经纬。",
        "parameters": _TRAIL + ("music_steps",),
    },
    "audio_music_spectral_terrain": {
        "name": "分频余音地形", "description": "相对频段幅度描出山脊，固定参考保留音量变化与余音。",
        "parameters": _TRAIL + _BANDS,
    },
    "audio_music_pitch_steps": {
        "name": "真实音高阶梯", "description": "可信音符按音域高低留下阶梯，缺少音高时不造音符。",
        "parameters": _TRAIL + ("music_pitch_min", "music_pitch_max", "music_pitch_confidence"),
    },
}


def initialize_music_parameters(engine):
    for key, value in MUSIC_DEFAULTS.items():
        setattr(engine, key, value)


def collect_music_parameters(engine):
    return {key: getattr(engine, key, value) for key, value in MUSIC_DEFAULTS.items()}


def register_music_arrangements(pixel_modes, compute_map):
    from .matrix_pixel_music_arrangements import compute_music_arrangement
    pixel_modes["音乐空间编排"] = [(mode, spec["name"]) for mode, spec in MUSIC_MODES.items()]
    compute_map.update({mode: (lambda engine, selected=mode: compute_music_arrangement(engine, selected))
                        for mode in MUSIC_MODES})
