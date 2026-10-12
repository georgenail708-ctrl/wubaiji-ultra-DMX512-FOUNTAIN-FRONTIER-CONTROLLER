"""R35 平滑事务：先算独立结果，成功后才提交；解除回到保留来源。"""
from dataclasses import dataclass

from .output_smoother import BUILTIN_SMOOTH_PRESETS


@dataclass
class SmoothChange:
    """一个clip的完整提交单元，不持有可变结果的别名。"""
    frames: list
    source: list
    preset_id: str
    preset_name: str
    custom: dict


def prepare_smoothing(clip, preset_id, custom=None, project=None):
    """调参始终读同一来源，计算/校验失败时原clip不发生变化。"""
    preset = BUILTIN_SMOOTH_PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"未知的平滑预设：{preset_id}")
    source = list(clip.smooth_source_frames)
    if preset_id == "bypass":
        return _prepare_bypass(clip, source)
    source = source or _initial_source(clip)
    if len(source) < 2:
        raise ValueError("Clip 帧数不足，无法平滑")
    custom = dict(getattr(clip, "smooth_custom_params", None) or {}) if custom is None else dict(custom)
    from .clip_smoothing_profiles import controls
    controls(preset, custom)
    from .clip_smoothing_pairs import selected_pairs
    from .clip_smoothing_math import smooth_recording
    frames = smooth_recording(source, preset, clip.active_channels_by_universe, custom,
                              selected_pairs(project, clip.active_channels_by_universe))
    return SmoothChange(frames, source, preset_id, preset.name, custom)


def _initial_source(clip):
    """第一次处理必须有未处理数据，旧损伤不能被重新命名成原始录制。"""
    if clip.smooth_preset:
        raise ValueError("旧平滑片段没有未处理来源，请从原始录制或工程备份恢复")
    return [frame.copy() for frame in clip.frames]


def _prepare_bypass(clip, source):
    """旧档缺来源时明确失败，不能把清标签当还原。"""
    if not source and getattr(clip, "smooth_preset", ""):
        raise ValueError("此旧片段未保存原始来源，不能确认恢复原样；请使用录制或备份")
    frames = [frame.copy() for frame in source] if source else list(clip.frames)
    return SmoothChange(frames, [], "", "", {})


def commit_smoothing(clip, change):
    """只提交本层数据；不回滚时长、目标、绑定或其他合法后处理编辑。"""
    clip.frames = change.frames
    clip.smooth_source_frames = change.source
    clip.smooth_preset = change.preset_id
    clip.smooth_preset_name = change.preset_name
    clip.smooth_custom_params = dict(change.custom)


def apply_clip_smoothing(clip, preset_id):
    """兼容原单clip接口；源码与菜单/Studio都复用同一准备和提交。"""
    change = prepare_smoothing(clip, preset_id)
    commit_smoothing(clip, change)
    return change.preset_name or BUILTIN_SMOOTH_PRESETS["bypass"].name
