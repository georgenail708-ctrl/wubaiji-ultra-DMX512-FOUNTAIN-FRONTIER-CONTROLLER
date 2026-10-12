"""Original overlay state/container order around the mounted trajectory domain."""
from ..processing.dispatch.native_trajectory_replay import domain


def combined_frame(clips, position, mappings):
    from .clip_trajectory_overlay import _source_time, build_clip_traj_frame, get_release_registry
    registry = get_release_registry()
    frames = []
    for clip in clips:
        released = registry.released_for_clip(str(clip.id))
        frames.append(build_clip_traj_frame(clip, _source_time(clip, position),
                                           *mappings, released))
    return domain().combine(frames)
