"""Refresh existing video maps on processing and layout events.

The decoded source, capture, playhead and preview caches stay engine-owned.
R194：重算一律走引擎接口 ``_refresh_video_maps``（worker 存活→命令化到解码
线程；无 worker→内联）——本模块不再直写 ``_video_preview_grid`` 等共享态。
"""
from __future__ import annotations

from typing import Any


def refresh_video_sample(engine: Any, reset_history: bool = False) -> bool:
    """Reprocess the retained source without reading or reopening its capture."""
    refresh = getattr(engine, "_refresh_video_maps", None)
    if not callable(refresh):
        return False
    return bool(refresh(reset_history))


def refresh_pixel_resources(engine: Any) -> None:
    """Refresh the current sample, then preserve the original running resources."""
    refresh_video_sample(engine)
    if bool(getattr(engine, "_running", False)):
        engine._start_mode_resources(reset_audio_frame=False)
