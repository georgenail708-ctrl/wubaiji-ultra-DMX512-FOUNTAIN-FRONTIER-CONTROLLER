"""Finish the original program-open chain after its track/audio restoration."""
from PyQt6.QtCore import QTimer

from ...engine.timeline_source_rebind import recheck_restored_timeline


def finish_program_open(window, program, project, audio_status, same_program):
    if not (same_program and recheck_restored_timeline(window._engine, project, project)):
        window._invalidate_timeline_program_cache()
    window._refresh_program_list()
    window._status.set_message(f"已打开节目: {getattr(program, 'name', '未命名节目')}{audio_status}")
    if window._engine is not None and hasattr(window._engine, "_start_background_precompile"):
        QTimer.singleShot(500, lambda: window._engine._start_background_precompile(project))
