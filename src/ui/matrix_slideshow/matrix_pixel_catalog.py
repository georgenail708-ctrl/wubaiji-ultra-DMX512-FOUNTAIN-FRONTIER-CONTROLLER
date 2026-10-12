"""Creation-only pixel directory policy; no mode registration or computation.

Reference: r189-library-pixel.md PIX directory evidence; the original Qt 6.11
combo/list, editor regions and external input/pattern distinction are reused.
All saved IDs and version-zero implementations remain with their original owner.
"""
from __future__ import annotations

from .matrix_pixel_music_catalog import MUSIC_MODES


CATEGORIES = ("音乐编排", "音频频谱", "对称波纹", "空间点阵", "方向流动",
              "粒子运动", "形状变化", "纹理与扫描", "媒体输入")
_BAR_PREFIX = "matrix_v40_audio_bar_"
_PATTERN_PREFIX = "matrix_v40_sample_post_"
_BAR_NAMES = {
    "matrix_v40_audio_bar_0000": "标准频段条",
    "matrix_v40_audio_bar_0004": "斜向频段",
    "matrix_v40_audio_bar_0005": "反斜频段",
    "matrix_v40_audio_bar_0006": "径向频谱",
    "matrix_v40_audio_bar_0007": "环绕频谱",
}
_SOURCE_GROUPS = (
    ("音乐空间", "音乐编排"), ("音频", "音频频谱"),
    ("对称波动", "对称波纹"), ("空间点阵", "空间点阵"),
    ("非对称运动", "方向流动"), ("粒子物理", "粒子运动"),
    ("形状变形", "形状变化"), ("屏幕与视频", "媒体输入"),
    ("外部纹理", "媒体输入"),
)
_GROUP_PURPOSES = {
    "音乐编排": "用真实音频事件和有限历史编排空间光形。",
    "音频频谱": "用当前音频频段或音量驱动矩阵光形。",
    "对称波纹": "在矩阵坐标中生成波纹、轴线或规则图案。",
    "空间点阵": "在矩阵坐标中生成点阵或规则标记图案。",
    "方向流动": "用坐标和时间生成有方向变化的光形。",
    "粒子运动": "用粒子位置和距离生成运动光点及轨迹。",
    "形状变化": "在几何轮廓间变化，生成边框或填充光形。",
    "纹理与扫描": "用矩阵坐标和时间生成纹理或扫描图案。",
    "媒体输入": "将真实媒体画面采样到矩阵设备。",
}
_PURPOSES = {
    "audio_eq_bars": "按频段强弱显示实心柱，保留峰值余光。",
    "audio_eq_soft": "以柔边光条显示各频段的强弱。",
    "audio_eq_dots": "用离散亮点表示各频段能量。",
    "audio_eq_center": "从中心向两侧显示频段的强弱。",
    "audio_eq_scan": "用扫描纹理显示频段强弱。",
    "audio_band_matrix": "以矩阵柱显示多个音频频段。",
    "audio_band_reverse": "以反向排列的矩阵柱显示频段。",
    "audio_band_center": "把低频放在中心显示矩阵频段柱。",
    "audio_band_dots": "以矩阵点列显示各频段强弱。",
    "audio_vu_meter": "用音量条表示整体音频能量。",
    "audio_fft": "将频谱强弱映射为矩阵柱形。",
    "audio_waveform": "将音频波形或频段轮廓映射为光条。",
    "audio_energy": "整体音量越过阈值时触发矩阵。",
    "matrix_v40_audio_bar_0000": "按可编辑方向排列频段条，显示频段强弱和峰值。",
    "matrix_v40_audio_bar_0004": "频段沿主对角线排列，光带由两侧向斜线靠拢。",
    "matrix_v40_audio_bar_0005": "频段按离斜线的距离排列，亮区沿反斜方向推进。",
    "matrix_v40_audio_bar_0006": "频段沿圆周排列，由外围向中心显示强弱。",
    "matrix_v40_audio_bar_0007": "频段按半径排列，沿圆周显示各圈强弱。",
    "screen_sample": "从所选屏幕区域采样亮度或颜色到矩阵。",
    "video_sample": "从已选择的视频帧采样亮度或颜色到矩阵。",
    "ext_spout": "接收Spout画面后采样；需要可用的发送端和接收模块。",
    "ext_ndi": "NDI远期登记入口，当前版本不提供接收画面。",
}
_PATTERN_STYLES = (
    ("条带", "生成周期条带图案"), ("渐变", "生成周期渐变图案"),
    ("门控", "生成亮暗切换图案"), ("噪点", "生成条带与噪点混合图案"),
    ("双层", "生成两层条带叠加图案"), ("散点", "生成条带与散点混合图案"),
    ("双波", "生成正弦与余弦混合图案"), ("斜纹", "生成条带与斜纹混合图案"),
    ("柔阶", "生成平滑渐变图案"),
)
_PATTERN_OFFSETS = {
    "asym_motion": 0, "space_dot": 24, "pixel_texture": 48,
    "scan_gate": 60, "symmetric_wave": 72, "sample_post": 84,
}


def audio_bar_replacement(mode_id):
    """Strict equality applies only to controlled version-one bars, not v0."""
    if not mode_id.startswith(_BAR_PREFIX):
        return ""
    suffix = mode_id[len(_BAR_PREFIX):]
    if not suffix.isdecimal() or not 0 <= int(suffix) < 140:
        return ""
    geometry = int(suffix) % 8
    return f"{_BAR_PREFIX}{0 if geometry < 4 else geometry:04d}"


def usage_category(mode_id, source_category):
    if mode_id.startswith(_PATTERN_PREFIX):
        return "纹理与扫描"
    for source, category in _SOURCE_GROUPS:
        if source in source_category:
            return category
    return "纹理与扫描"


def display_name(mode_id, original_name):
    if mode_id in _BAR_NAMES:
        return _BAR_NAMES[mode_id]
    if mode_id.startswith(_PATTERN_PREFIX):
        index = int(mode_id[len(_PATTERN_PREFIX):])
        style = _PATTERN_STYLES[(index + 84) % 9][0]
        return f"生成图案·{style} {index + 1:03d}"
    return original_name


def purpose(mode_id, category):
    if mode_id in MUSIC_MODES:
        return MUSIC_MODES[mode_id]["description"]
    if mode_id in _PURPOSES:
        return _PURPOSES[mode_id]
    if mode_id.startswith(_PATTERN_PREFIX):
        index = int(mode_id[len(_PATTERN_PREFIX):])
        return _PATTERN_STYLES[(index + 84) % 9][1] + "，不读取媒体画面。"
    return _GROUP_PURPOSES.get(category, "历史预设，按原保存的效果ID和版本执行。")


def creation_categories(registry):
    """Adapt the original visible registry without mutating it or saved modes."""
    groups = {category: [] for category in CATEGORIES}
    for source, entries in registry.items():
        for mode_id, original_name in entries:
            if mode_id == "ext_ndi":
                continue  # No receiver exists; saved IDs remain registered.
            replacement = audio_bar_replacement(mode_id)
            if replacement and replacement != mode_id:
                continue
            category = usage_category(mode_id, source)
            groups[category].append((mode_id, display_name(mode_id, original_name)))
    return groups


def source_evidence(mode_id):
    """Record the actual dispatch family; it is not a uniqueness claim."""
    if mode_id.startswith(_BAR_PREFIX):
        return dict(caller="compute_versioned_audio_bar", v1_geometry=int(mode_id[-4:]) % 8,
                    v0="original _compute_audio_bar retained")
    for family, offset in _PATTERN_OFFSETS.items():
        prefix = f"matrix_v40_{family}_"
        if mode_id.startswith(prefix):
            index = int(mode_id[len(prefix):])
            return dict(caller="v40._compute_pattern", variant=index + offset,
                        metric_family=(index + 2 * offset) % 96, style=(index + offset) % 9)
    return dict(caller="original registered dispatcher", grouping="original mode family")


def _creation_state(mode_id, visible, hidden_duplicate):
    if not visible:
        return "legacy_hidden"
    if mode_id == "ext_ndi":
        return "creation_hidden_unimplemented"
    if hidden_duplicate:
        return "creation_hidden_v1_duplicate"
    return "retained"


def describe_mode(mode_id, original_name, source_category, *, visible=True):
    """Per-ID policy for the audit manifest; not a second persisted model."""
    category = usage_category(mode_id, source_category)
    replacement = audio_bar_replacement(mode_id)
    hidden_duplicate = bool(visible and replacement and replacement != mode_id)
    state = _creation_state(mode_id, visible, hidden_duplicate)
    explanation = purpose(mode_id, category) if visible else "历史预设，按原保存的效果ID和版本执行。"
    return dict(id=mode_id, original_name=original_name, source_category=source_category,
                category=category if visible else "历史兼容", display_name=display_name(mode_id, original_name),
                purpose=explanation, disposition=state,
                creation_visible=state == "retained",
                replacement=replacement if hidden_duplicate else "", replacement_version=1 if hidden_duplicate else None,
                preserve_saved_id=True, preserve_v0=True, source=source_evidence(mode_id),
                verification="directory/source policy only; no per-ID native output claim")
