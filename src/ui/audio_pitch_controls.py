"""Passive file-pitch status and one explicit opt-in action shared by pages.

Reuses AudioAnalyzer's existing guarded pyin/chroma worker. It neither creates
an analyzer nor starts capture, workers, timers or analysis while showing UI.
"""
from __future__ import annotations


def existing_analyzer(preferred=None):
    analyzer = preferred
    if analyzer is None:
        from . import audio_driver
        analyzer = getattr(audio_driver, "_global_analyzer", None)
    if analyzer is not None and hasattr(analyzer, "objectName"):
        try:
            analyzer.objectName()
        except RuntimeError:
            return None
    return analyzer


def file_ready(analyzer):
    return (analyzer is not None and getattr(analyzer, "_mode", "") == "file"
            and getattr(analyzer, "_file_data", None) is not None)


def _missing_source_message(analyzer):
    if analyzer is not None and getattr(analyzer, "_mode", "") == "microphone":
        return ("当前麦克风提供能量、频段和起音；音高与和弦仅支持参考音频文件。"
                "请在全局音频来源载入文件后分析。")
    return "先在全局音频来源选择并载入参考音频文件，再分析音高与和弦。"


def pitch_status(analyzer):
    """Return an honest capability message and whether an explicit retry can run."""
    if not file_ready(analyzer):
        return _missing_source_message(analyzer), False
    status = analyzer.pitch_status()
    worker = getattr(analyzer, "_pitch_thread", None)
    analyzing = status == "analyzing" and worker is not None and worker.is_alive()
    descriptions = {
        "off": "此预设需要音高或和弦。点击按钮分析当前参考音频文件。",
        "ready": "音高与和弦已就绪；无可信音高的片段不会产生旋律跟随。",
        "analyzing": "正在分析参考音频的音高与和弦；基础能量和频段仍可使用。",
    }
    message = descriptions.get(status, "当前参考音频的音高尚未就绪。")
    if status == "analyzing" and not analyzing:
        message = "音高分析尚未就绪，可点击重新分析当前参考音频。"
    return message, not analyzing and status != "ready"


def refresh_pitch_controls(analyzer, note, button):
    message, enabled = pitch_status(analyzer)
    if note.text() != message:
        note.setText(message)
    if button.isEnabled() != enabled:
        button.setEnabled(enabled)


def request_pitch_analysis(analyzer):
    """The caller must use an explicit operator action; no page/restore call."""
    if not file_ready(analyzer):
        return
    worker = getattr(analyzer, "_pitch_thread", None)
    if worker is None or not worker.is_alive():
        analyzer.set_pitch_detection(True)
