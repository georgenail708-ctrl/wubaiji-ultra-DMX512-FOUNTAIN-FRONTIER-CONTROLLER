# -*- coding: utf-8 -*-
"""Studio 状态由编译、绑定和实际应用结果共同决定，普通刷新不伪造成功。

复用现有 QTimer 防抖身份与 ApplyLedger 回路，不另建调度/应用系统。
参考：节点数据上下文、Qt QTimer.isActive；资源台账 R185。
"""
from src.ui import qascade_theme as _th

_FAILED_LABELS = {"smooth_preset": "平滑", "smooth_preset_name": "平滑名称", "smooth_custom_params": "平滑参数"}


def show_pending(panel, sid: str) -> None:
    """只更新编辑所属页的反馈，不在每个图变更通知中重复编译全图。"""
    if sid != str(panel._state.get("active_sheet", "")):
        return
    panel._status_label.setText("待应用")
    panel._status_label.setStyleSheet(f"color: {_th.BRIGHT_INFO};")
    panel._status_label.setToolTip("修改尚未作用于 Clip")


def _application_state(panel, sheet, res):
    """错误、目标、挂起与失败各有事实来源，成功仅在真实应用回路之后显示。"""
    if sheet is None:
        return "未应用", _th.TEXT_DISABLED
    if res is not None and res.errors:
        return f"{len(res.errors)} 个问题", _th.BRIGHT_ERROR
    sid = str(sheet.get("id", ""))
    if not panel._bound_clips(sid):
        return _unbound_state(res)
    pending = str(panel._pending_sheet_id or panel._state.get("active_sheet", ""))
    if panel._apply_timer.isActive() and pending == sid:
        return "待应用", _th.BRIGHT_INFO
    return _settled_state(panel, sid, res)


def _unbound_state(res):
    """无目标仍保留有效的编译提示与定位，不能因绑定反馈遮住问题。"""
    if res is not None and res.warnings:
        return f"未绑定 Clip · {len(res.warnings)} 条提示", _th.BRIGHT_WARN
    return "未绑定 Clip", _th.TEXT_SECONDARY


def _settled_state(panel, sid, res):
    """保留该页实际失败；编译提示继续可见并保留点击定位入口。"""
    failed = panel._apply_failures.get(sid, ())
    if failed:
        label = _FAILED_LABELS.get(failed[0], failed[0])
        return f"{len(failed)} 项未生效（如 {label}）", _th.BRIGHT_WARN
    if res is not None and res.warnings:
        return f"已应用 · {len(res.warnings)} 条提示", _th.BRIGHT_WARN
    return "已应用", _th.BRIGHT_SUCCESS


def show_compile_status(panel, sheet, res) -> None:
    """统一投影到既有状态控件；刷新不修改应用账本或失败事实。"""
    text, color = _application_state(panel, sheet, res)
    label = panel._status_label
    label.setText(text)
    label.setStyleSheet(f"color: {color};")
    issues = res.issues if res is not None else []
    details = [f"[{item.code or 'V'}] {item.text}" for item in issues[:6]]
    if sheet is not None:
        failed = panel._apply_failures.get(str(sheet.get("id", "")), ())
        details.extend(f"未生效：{_FAILED_LABELS.get(key, key)}" for key in failed)
    label.setToolTip("\n".join(details) or text)
    label.setEnabled(bool(issues))
