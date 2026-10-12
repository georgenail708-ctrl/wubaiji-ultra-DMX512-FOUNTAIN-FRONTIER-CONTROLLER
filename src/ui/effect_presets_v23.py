"""
effect_presets_v23.py — 空间图案预设（融入 EffectEngine）
==========================================================
将 MatrixPatternPanel 的 PatternGenerator 接入 EffectEngine，
使图案预设可在 Programmer 效果预设浏览器中直接选择和应用。

每个图案预设：
- 基于 TopView 的 pos_x/pos_y 归一化坐标（不强制格式）
- 支持 density/size/phase/group_size 参数
- 支持 phase_speed 参数（在 EffectEngine.compute 的时间轴上推进相位）
- 支持 audio_rms/audio_beat 参数（由 Programmer 的音频层注入）
- 支持 cx/cy/rotation 参数（由摇杆层注入）
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Any, List

if TYPE_CHECKING:
    from .effect_engine import EffectEngine

from .effect_engine import EffectPreset, ParamSpec, EffectFrame

CH_TYPES = ["dimmer", "R", "G", "B", "valve", "pump",
            "motor_x", "motor_y", "motor_z", "strobe"]


def _register_v23_presets(engine: "EffectEngine"):
    """注册空间图案预设到 EffectEngine"""
    from .matrix_pattern_panel import PatternGenerator, PATTERN_CATEGORIES

    reg = engine._reg

    # 通用图案参数规格
    common_params = [
        ParamSpec("ch_type",     "通道类别", "choice", "dimmer",
                  choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("density",     "密度",     "float",  0.8,  0.0, 1.0,  0.05,
            tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
        ParamSpec("size",        "尺寸",     "float",  1.0,  0.1, 5.0,  0.1,
            tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
        ParamSpec("phase_start", "相位起始", "float",  0.0,  0.0, 10.0, 0.1,
            tooltip="控制相位的起始值。决定效果在波形周期中的初始位置"),
        ParamSpec("phase_speed", "相位速度", "float",  0.02, 0.0, 0.5,  0.005,
            tooltip="控制相位的旋转/变化速度。值越大相位随时间变化越快"),
        ParamSpec("group_size",  "分组大小", "int",    3,    1,   50,   1,
            tooltip="控制每组包含的设备数量。值越大同组运行的设备越多"),
        ParamSpec("min_val",     "最小值",   "int",    0,    0,   255,  1,
            tooltip="控制允许的最小值。限制参数的最低边界，防止输出过低"),
        ParamSpec("max_val",     "最大值",   "int",    255,  0,   255,  1,
            tooltip="控制允许的最大值。限制参数的最高边界，防止输出过高"),
    ]

    def _make_pattern_fn(pattern_name: str):
        """工厂函数：为每个图案名生成 EffectEngine 计算函数"""
        def _fn(ef: EffectFrame, t: float, sorted_fx: list,
                fdefs: Dict, params: Dict[str, Any], fps: float):
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("min_val", 0))
            hi = int(params.get("max_val", 255))
            phase_start = float(params.get("phase_start", 0.0))
            phase_speed = float(params.get("phase_speed", 0.02))
            phase = phase_start + t * phase_speed * fps * 0.04

            # 构建 fixtures 字典列表（PatternGenerator 需要 pos_x/pos_y）
            fixtures_list: List[Dict] = []
            orig_indices: List[int] = []
            for orig_i, fi in sorted_fx:
                fid = getattr(fi, "id", str(orig_i))
                pos_x = getattr(fi, "pos_x", 0.0)
                pos_y = getattr(fi, "pos_y", 0.0)
                fixtures_list.append({"id": fid, "pos_x": pos_x, "pos_y": pos_y})
                orig_indices.append(orig_i)

            pg_params = {
                "density":    params.get("density",    0.8),
                "size":       params.get("size",       1.0),
                "phase":      phase,
                "group_size": params.get("group_size", 3),
                "cx":         params.get("cx",         0.0),
                "cy":         params.get("cy",         0.0),
                "rotation":   params.get("rotation",   0.0),
                "audio_rms":  params.get("audio_rms",  0.0),
                "audio_beat": params.get("audio_beat", 0.0),
            }

            weights = PatternGenerator.get_pattern(
                pattern_name, fixtures_list, pg_params)

            for seq_i, orig_i in enumerate(orig_indices):
                w = weights[seq_i] if seq_i < len(weights) else 0.0
                val = int(lo + w * (hi - lo))
                ef.values[orig_i][ch] = max(0, min(255, val))

        return _fn

    # 按分类批量注册
    category_icons = {
        "基础形状": "◆",
        "网格图案": "⊞",
        "动态波形": "〜",
        "追逐序列": "→",
        "特殊效果": "✦",
        "喷泉专用": "💧",
        "坐标艺术": "🎨",
    }

    for cat_name, patterns in PATTERN_CATEGORIES.items():
        icon = category_icons.get(cat_name, "⚡")
        for pattern_name in patterns:
            preset_id = f"pattern_{pattern_name.lower()}"
            display_name = f"{pattern_name.replace('_', ' ')}"
            reg(EffectPreset(
                id=preset_id,
                name=display_name,
                category=f"图案/{cat_name}",
                description=(
                    f"空间图案：{display_name}。"
                    f"基于 TopView 坐标，支持音频/摇杆实时调制。"
                    f"分类：{cat_name}"),
                target_channels=CH_TYPES,
                icon=icon,
                tags=["pattern", "spatial", cat_name],
                params=common_params,
            ), _make_pattern_fn(pattern_name))

    # 额外注册：序列预设（将内置序列的第一帧作为静态预设）
    try:
        from .matrix_pattern_panel import BUILTIN_SEQUENCES
        for seq_name, frames in BUILTIN_SEQUENCES.items():
            if not frames:
                continue
            first = frames[0]
            pattern_name = first.get("pattern", "All_On")
            preset_id = f"seq_{seq_name.replace(' ', '_').replace('/', '_')}_frame1"
            reg(EffectPreset(
                id=preset_id,
                name=f"序列/{seq_name}（首帧）",
                category="图案/序列",
                description=f"内置序列「{seq_name}」的首帧图案：{pattern_name}",
                target_channels=CH_TYPES,
                icon="🎞",
                tags=["pattern", "sequence", seq_name],
                params=[
                    ParamSpec("ch_type",     "通道类别", "choice", "dimmer",
                              choices=CH_TYPES,
                        tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                    ParamSpec("density",     "密度",     "float",
                              first.get("density", 0.8), 0.0, 1.0, 0.05,
                        tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
                    ParamSpec("size",        "尺寸",     "float",
                              first.get("size", 1.0), 0.1, 5.0, 0.1,
                        tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
                    ParamSpec("phase_start", "相位起始", "float",
                              first.get("phase_start", 0.0), 0.0, 10.0, 0.1,
                        tooltip="控制相位的起始值。决定效果在波形周期中的初始位置"),
                    ParamSpec("phase_speed", "相位速度", "float",
                              first.get("phase_speed", 0.02), 0.0, 0.5, 0.005,
                        tooltip="控制相位的旋转/变化速度。值越大相位随时间变化越快"),
                    ParamSpec("group_size",  "分组大小", "int",    3, 1, 50, 1,
                        tooltip="控制每组包含的设备数量。值越大同组运行的设备越多"),
                    ParamSpec("min_val",     "最小值",   "int",    0, 0, 255, 1,
                        tooltip="控制允许的最小值。限制参数的最低边界，防止输出过低"),
                    ParamSpec("max_val",     "最大值",   "int",    255, 0, 255, 1,
                        tooltip="控制允许的最大值。限制参数的最高边界，防止输出过高"),
                ],
            ), _make_pattern_fn(pattern_name))
    except Exception:
        pass
