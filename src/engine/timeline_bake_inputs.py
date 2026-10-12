"""Owned inputs for the existing sequential offline renderer.

Reuse Project's original JSON factory and PM frame-ref attachment. Frame objects
are mutable (recording/smoothing); only immutable bytes-backed data can be shared.
Reference: R191-A audit, CPython 3.13.13/PSF-2.0, Qt 6.11 worker ownership.
"""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json

import numpy as np

from src.core.models import DMXFrame, Project, parse_project_json
from .timeline_render_metadata import render_metadata, metadata_signature


_NM_INPUTS = (
    "_config", "_input_filter", "_output_filter", "_input_filter_mask", "_output_filter_mask",
    "_layers", "_channel_defaults", "_universe_snapshot", "_universe_virtual_snapshot",
    "_xy_reference_by_universe", "_xy_reference_z_command_deg", "_xy_reference_z_command_ids",
    "_xy_ref_map_version", "_shape_blend_mode", "_shape_gain", "_layer_gains",
    "_shape_runmin", "_shape_runmax", "_shape_anchor_ema", "_direct_anchor_by_universe",
    "_ltp_editor_time", "_ltp_clip_time", "_ltp_editor_layer_time",
)
_ENGINE_INPUTS = (
    "_audio_mod_signature", "_fade_in_anchor", "_fade_in_anchor_deps",
    "_hold_boundary_latch", "_hold_consumed_masks", "_takeover_consumed_masks",
    "_light_tracking_state", "_light_tracking_time", "_light_tracking_color_cache",
)


def check_cancel(opts):
    callback = opts.get("should_abort")
    if callback is not None and callback():
        from .playback_engine import TimelineBakeAbort
        raise TimelineBakeAbort("offline inputs retired before publication")
    cooperate = opts.get("input_yield")
    if cooperate is not None:
        cooperate()
        if callback is not None and callback():
            from .playback_engine import TimelineBakeAbort
            raise TimelineBakeAbort("offline inputs retired during preparation")


def _metadata_text(project):
    data = project.to_dict(include_frames=False)
    # Project.to_dict stamps the current program with time.time on every call.
    # Keep its actual persisted timestamp, so a no-edit comparison is stable.
    stamps = {str(program.id): program.updated_at for program in project.programs or []}
    for program in data.get("programs", []):
        program["updated_at"] = stamps.get(str(program.get("id")), program.get("updated_at"))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _track_clips(tracks):
    for track in tracks or []:
        yield from track.clips or []


def fingerprint_metadata(project):
    """Only render dependencies; complete persistence stays in _metadata_text."""
    return render_metadata(project)


def _all_clips(project):
    for program in project.programs or []:
        yield from _track_clips(program.tracks)
    yield from _track_clips(project.tracks)


def input_signature(project, opts):
    metadata = json.dumps(fingerprint_metadata(project), ensure_ascii=False, sort_keys=True)
    digest = _new_content_digest()
    digest.update(metadata.encode("utf-8"))
    for clip in _all_clips(project):
        check_cancel(opts)
        digest.update(str(clip.id).encode("utf-8"))
        _digest_frames(digest, clip.frames or [], opts)
        digest.update(b"smooth_source")
        _digest_frames(digest, getattr(clip, "smooth_source_frames", ()) or (), opts)
    return digest.digest()


# ★ R195/P0（用户实机 py-spy 实证：91MB 工程烘焙线程 80% 单核，其中 42% 在
#   重复全帧 SHA256——snapshot_inputs 每次尝试算两遍 + inputs_current 再一遍，
#   中止/编辑重试全部从头再付）。
# ★ R195 修订（R191 E01 契约测试打回 memo：DMXFrame.__post_init__ 把 data
#   统一拷成 uint8 ndarray 且**允许原地写**、发布门必须检测——"引用不变即
#   内容不变"的缓存假设不成立，帧内容哈希必须每轮真读）。提速改为无损三刀：
#   ① 零拷贝：ndarray/bytearray 以 buffer 直喂哈希，消 np.asarray().tobytes()
#      的两次全量分配（原实现每帧分配 512B×2）；
#   ② 零拷贝直喂实测 2× (sha256 direct 1.2ms vs cast 2.4ms / tobytes 5ms @4000 帧)；
#   ③ snapshot_inputs 双算复用一次签名 + inputs_current 先 O(n) 身份快筛
#      （表对象/长度变 → 立即 stale，免 91MB 内容哈希）。
def _new_content_digest():
    return hashlib.sha256()


def _digest_frames(digest, frames, opts):
    for index, frame in enumerate(frames):
        if index % 128 == 0:
            check_cancel(opts)
        digest.update(f"{int(frame.universe)}:{float(frame.timestamp).hex()}:".encode("ascii"))
        data = frame.data
        if isinstance(data, np.ndarray) and data.dtype == np.uint8:
            digest.update(data)      # ★ R195/P0：零拷贝直喂（原 np.asarray+tobytes 双分配）
        elif isinstance(data, (bytes, bytearray, memoryview)):
            digest.update(data)      # 零拷贝（原地写每轮如实反映 — R191 E01 契约）
        else:
            digest.update(np.asarray(data, dtype=np.uint8).tobytes())


def _freeze_frame(frame, memo):
    key = id(frame)
    if key not in memo:
        data = np.asarray(frame.data, dtype=np.uint8)
        if data.flags.writeable or not isinstance(data.base, bytes):
            data = np.frombuffer(data.tobytes(), dtype=np.uint8)
        memo[key] = DMXFrame(universe=int(frame.universe), timestamp=float(frame.timestamp), data=data)
    return memo[key]


def _freeze_refs(refs, memo, opts):
    result = []
    for ref in refs:
        check_cancel(opts)
        lists = []
        for frames in ref[1:]:
            frozen = []
            for index, frame in enumerate(frames):
                if index % 128 == 0:
                    check_cancel(opts)
                frozen.append(_freeze_frame(frame, memo))
            lists.append(frozen)
        result.append((ref[0], *lists))
    return result


def _container_refs(manager, container, memo, opts):
    original = manager._project
    # The manager here is the worker-owned PM, never the live PM.
    manager._project = container
    try:
        _program_refs, refs = manager._collect_frames_refs()
    finally:
        manager._project = original
    return _freeze_refs(refs, memo, opts)


def snapshot_project(manager, project, opts):
    clone = Project._from_dict(parse_project_json(_metadata_text(project)))
    memo = {}
    originals = {str(program.id): program for program in project.programs or []}
    for program in clone.programs or []:
        source = (project if not originals or str(program.id) == str(project.current_program_id)
                  else originals[str(program.id)])
        manager._reattach_frames(_container_refs(manager, source, memo, opts), [program])
    manager._reattach_frames(_container_refs(manager, project, memo, opts), [clone])
    return clone


def snapshot_inputs(engine, project, manager, opts):
    check_cancel(opts)
    token = input_signature(project, opts)
    metadata = metadata_signature(project)
    clone = snapshot_project(manager, project, opts)
    check_cancel(opts)
    # ★ R195/P0：同一同步段内的第二次签名与 token 必然相同（无让出点）——
    #   91MB 工程全帧哈希三遍改一遍（原 42% CPU 的重复哈希大头）。
    nm = engine._nm
    with getattr(nm, "_state_lock", nullcontext()):
        network = {name: deepcopy(getattr(nm, name)) for name in _NM_INPUTS if hasattr(nm, name)}
        runtime = engine._timeline_render_runtime_signature_value()
    seed = {name: deepcopy(getattr(engine, name)) for name in _ENGINE_INPUTS if hasattr(engine, name)}
    return {"project": clone, "source": project, "token": token, "metadata": metadata, "network": network,
            "seed": seed, "runtime": runtime,
            "id_sig": _frames_identity_sig(project),
            "interval": engine._timer.interval(),
            "layout": engine._cached_project_clip_layout_signature(project.tracks or [])}


def _frames_identity_sig(project) -> tuple:
    """O(n) 帧身份指纹（id 扫描，无内容读取）——快筛用。"""
    return tuple((id(clip), id(clip.frames), len(clip.frames or ()),
                  id(getattr(clip, "smooth_source_frames", None) or ()))
                 for clip in _all_clips(project))


def inputs_current(engine, inputs, opts):
    check_cancel(opts)
    if engine._pm.project is not inputs["source"]:
        return False
    if engine._timeline_render_runtime_signature_value() != inputs["runtime"]:
        return False
    # 快筛：帧身份（表对象/长度）变了 → 必 stale（省 91MB 内容哈希）
    if _frames_identity_sig(inputs["source"]) != inputs.get("id_sig"):
        return False
    # 内容级复核（帧原地写检测——R191 E01 契约）
    return input_signature(inputs["source"], opts) == inputs["token"]
