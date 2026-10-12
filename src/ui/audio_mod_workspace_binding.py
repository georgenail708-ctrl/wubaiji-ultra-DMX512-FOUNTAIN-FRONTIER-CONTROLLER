"""AM23 thin ownership glue for the existing audio/Draw workspace.

Uses the original global analyzer, project audio resolver and Main's existing
status refresh. No capture, timer, render, sender or second analysis worker.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def install_audio_workspace(win):
    if getattr(win, "_audio_mod_source_binding", None) is not None:
        return
    from .audio_driver import get_audio_driver
    from .audio_mod_source_binding import bind_audio_mod_source
    analyzer = get_audio_driver()
    win._audio_mod_source_binding = bind_audio_mod_source(win, analyzer, analyzer._mode)
    win._audio_mod_prepared_source = None


def _project_and_path(win):
    project = win._project()
    path = win._current_timeline_audio_path()
    if not path:
        path = str(getattr(project, "audio_path", "") or "")
    if path:
        path = os.fspath(win._resolve_project_audio_path(getattr(win, "_project_path", ""), path))
    return project, path


def prepare_audio_workspace(win):
    """Source/Apply/restore setup only; file decoding stays in the original cache."""
    binding = getattr(win, "_audio_mod_source_binding", None)
    if binding is None:
        return
    try:
        from src.audio.audio_feature_cache import file_stamp
        from src.engine.audio_mod_feature_view import prepare_feature_file
        project, path = _project_and_path(win)
        mode = str(binding._analyzer._mode)
        key = (id(project), mode, path, file_stamp(path))
        if key != getattr(win, "_audio_mod_prepared_source", None):
            binding.set_source(mode, path)
            win._audio_mod_prepared_source = key
        state = getattr(project, "audio_modulation_state", {}) or {}
        if mode == "file" and state.get("processor_mode") == "chain" and state.get("processors"):
            prepare_feature_file(path, background=True)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        log.debug("audio workspace source preparation failed", exc_info=True)


def refresh_audio_workspace(win):
    """Existing 200ms UI refresh; only a visible Draw receives scalar feedback."""
    binding = getattr(win, "_audio_mod_source_binding", None)
    if binding is None:
        return
    try:
        project, path = _project_and_path(win)
        mode = str(binding._analyzer._mode)
        old = getattr(win, "_audio_mod_prepared_source", None)
        from src.audio.audio_feature_cache import file_stamp
        if (id(project), mode, path, file_stamp(path)) != old:
            prepare_audio_workspace(win)
        draw = getattr(win, "_audio_mod_draw_editor", None)
        if draw is None or not draw.isVisible():
            return
        from src.engine.audio_mod_clip_processor import processor_feedback
        draw._processor_chain.set_feedback(processor_feedback(win._engine))
    except (AttributeError, RuntimeError, TypeError):
        log.debug("audio workspace feedback failed", exc_info=True)


def dispose_audio_workspace(win):
    binding = getattr(win, "_audio_mod_source_binding", None)
    if binding is not None:
        binding.dispose()
        win._audio_mod_source_binding = None
