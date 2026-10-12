"""Bounded file-video tick steps using the existing playhead and capture APIs.

Processing, previews, camera reads and capture ownership remain in the engine.
"""
from __future__ import annotations

from typing import Any

from .matrix_video_transforms import next_video_playhead, normalize_video_rate


def video_advance_steps(engine: Any) -> int:
    """Consume one tick's rate, retaining the original near-1x gate and remainder."""
    rate = normalize_video_rate(getattr(engine, "video_rate", 1.0))
    if 0.999 <= rate <= 1.0:
        return 1
    engine._video_rate_acc += rate
    steps = min(8, int(engine._video_rate_acc))
    engine._video_rate_acc -= steps
    return steps


def read_file_video_step(engine: Any, cv2: Any) -> tuple[bool, Any]:
    """Read one original direction/in-out step, including the old failure loop."""
    nxt, play_dir = next_video_playhead(
        current=engine._video_current_frame, play_dir=engine._video_play_dir,
        direction=engine.video_direction, in_point=engine.video_in_point,
        out_point=engine.video_out_point, total=engine._video_total_frames)
    engine._video_play_dir = play_dir
    total = engine._video_total_frames
    if total > 0:
        nxt = max(0, min(int(nxt), total - 1))
    else:
        nxt = max(0, int(nxt))
    engine._seek_video_to(nxt, cv2)
    ok, frame = engine._read_capture_frame()
    if not ok or frame is None:
        ok, frame, loop_nxt = engine._loop_read_from_in_point(cv2)
        if not ok or frame is None:
            return False, None
        nxt = loop_nxt
    engine._video_current_frame = int(nxt)
    return True, frame


def read_file_video_frame(engine: Any, cv2: Any) -> tuple[bool, Any]:
    """Advance at most eight original steps and return the final decoded frame."""
    steps = video_advance_steps(engine)
    ok, frame = False, None
    for _ in range(steps):
        ok, frame = read_file_video_step(engine, cv2)
        if not ok or frame is None:
            return False, None
    return ok, frame
