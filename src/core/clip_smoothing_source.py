"""R35 未处理录制的保存、轻快照和解除；复用 R140 frame_blob 编解码。

原始帧只读，处理结果另建。轻快照只携带列表引用，避免交互时序列化
第二份完整录制。旧两项 refs 保持兼容，有来源的 clip 才增加第三项。
"""
from types import SimpleNamespace


def source_payload(clip, include_frames):
    """完整保存才编码来源，不能让 scoped undo 变成全量帧打包。"""
    frames = getattr(clip, "smooth_source_frames", None) or []
    if not include_frames or not frames:
        return {}
    from .models import _frames_payload
    return {"smooth_source": {"version": 1, "frame_count": len(frames),
                              **_frames_payload(frames)}}


def load_source(clip, data, lazy, memo):
    """来源缺失表示旧格式；存在却损坏必须报错，不能假装仍能复原。"""
    payload = data.get("smooth_source")
    if payload is None:
        return
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("自动平滑原始来源格式无效")
    from .models import _clip_frames_load
    holder = SimpleNamespace(frames=[])
    _clip_frames_load(holder, payload, lazy, memo)
    if len(holder.frames) != int(payload.get("frame_count", -1)):
        raise ValueError("自动平滑原始来源不完整，已保留原工程")
    clip.smooth_source_frames = holder.frames


def capture_frame_ref(clip):
    """快照保存输出与来源两个列表；引用帧的前提是改写前解耦。"""
    frames = list(getattr(clip, "frames", None) or [])
    source = list(getattr(clip, "smooth_source_frames", None) or [])
    if not frames and not source:
        return None
    ref = (str(clip.id), frames)
    return (*ref, source) if source else ref


def attach_frame_ref(clip, ref):
    """兼容历史 (id, frames) 条目；只回挂快照中剥离的数据。"""
    if not clip.frames:
        clip.frames = list(ref[1])
    if len(ref) > 2:
        clip.smooth_source_frames = list(ref[2])


def split_frames(frames, cut):
    """R24/R35：处理结果与来源使用同一切点/step-hold 边界规则。"""
    from copy import deepcopy
    left, right = [], []
    ordered = sorted(frames, key=lambda f: (int(f.universe), float(f.timestamp)))
    for universe in sorted({int(f.universe) for f in ordered}):
        group = [f for f in ordered if int(f.universe) == universe]
        before = [f for f in group if float(f.timestamp) <= cut]
        after = [f for f in group if float(f.timestamp) > cut]
        left.extend(_shift_frames(before, 0.0, cut))
        boundary = before[-1] if before else group[0]
        first = deepcopy(boundary)
        first.timestamp = 0.0
        right.append(first)
        right.extend(_shift_frames(after, cut, None))
    key = lambda f: (float(f.timestamp), int(f.universe))
    return sorted(left, key=key), sorted(right, key=key)


def _shift_frames(frames, offset, maximum):
    """复制并平移采样时间，保持原来源不被裁剪或原地写入。"""
    from copy import deepcopy
    result = []
    for frame in frames:
        copy = deepcopy(frame)
        copy.timestamp = max(0.0, float(copy.timestamp) - offset)
        if maximum is not None:
            copy.timestamp = min(copy.timestamp, maximum)
        if offset and copy.timestamp <= 0.0001:
            continue
        result.append(copy)
    return result
