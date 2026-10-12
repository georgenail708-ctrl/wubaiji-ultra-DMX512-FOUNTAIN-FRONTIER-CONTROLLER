"""R26.5 original Draw feedback wording; no source, cache or DSP ownership.

References: existing /chain state conventions and Draw _dry_reason;
finite source evidence and exact seams: ai-memory/_references/r189-library-pixel.md.
"""
from __future__ import annotations


_REASONS = {
    "file_unset": "未配置参考音频",
    "file_missing": "参考音频文件缺失，请重新定位文件",
    "file_unprepared": "参考音频尚未准备分析数据",
    "cache_busy": "音频分析数据暂忙，等待本次读取",
    "analysis_failed": "参考音频分析失败，请检查文件",
    "analysis_pending": "参考音频分析中",
    "features_unavailable": "参考音频暂无可用分析数据",
    "outside_audio": "当前位置超出参考音频范围",
    "offline_live": "离线渲染不使用实时音频",
    "live_waiting": "等待实时音频数据",
    "live_expired": "实时音频数据已过期，等待新数据",
    "silence": "当前位置为静音",
}


def status_text(packet: dict) -> str:
    """Return a dry reason, or let the original active numeric feedback render."""
    if packet.get("ready") and packet.get("has_audio"):
        return ""
    reason = packet.get("reason", "")
    fallback = "silence" if packet.get("ready") else "live_waiting"
    text = _REASONS.get(reason, _REASONS[fallback])
    return text + "：原数据直接通过。"
