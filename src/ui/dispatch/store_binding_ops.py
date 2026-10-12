# -*- coding: utf-8 -*-
"""场景**键盘绑定 / 自动淡入淡出**的菜单动作（从 `store_handlers` 拆出）。

用户 2026-09-14：
  · "每个 scene 可以绑定键盘上的按键…映射随项目持久化，入口就加在每个 scene 的
     右击页面"；
  · "可以设置自动的淡入淡出时间…即使不绑定按键，我鼠标点击一样生效。都放在右击菜单。"
  · "给右击菜单的每个功能配上符合我的前端风格的图标（参考 clip 的右击菜单的设计）"。

拆出理由：`store_handlers` 已超门禁 500 行；这两个动作自成一组（都是"改场景属性"），
与"存/取/删"路径无耦合。仍由 `_install_store_handlers` 挂到 DispatchPanel 上。
"""
from __future__ import annotations

from typing import Optional

from . import scene_keys as _keys


def _bind_key(self, *, c, cid) -> None:
    """「绑定键盘映射…」：让用户按一个键 → 存到该场景（按键即时捕获，不用手打）。

    ★ 用**按键捕获对话框**而不是文本框：用户要的是"按一下 P 就绑上"
      （MA 控台式），手打字母既慢又容易绑错。
    """
    key = _capture_key(self, current=str(getattr(c, "key_binding", "") or ""))
    if key is None:
        return
    store = _store()
    if store is None:
        return
    store.set_key_binding(cid, key)
    _after_binding_change(self)
    self._status.setText(
        f"「{c.name}」已绑定按键 {key}" if key else f"「{c.name}」已解绑键盘")


def _set_fade(self, *, c, cid) -> None:
    """「自动淡入淡出时间…」：设置该场景触发/释放的淡化秒数（0=瞬时）。

    ★ 用户 2026-09-14："跟哪种模式无关，flash 还是 latch 都可以生效"、
      "即使不绑定按键，我鼠标点击一样生效" —— 故这只是场景属性，触发路径统一读取。
    """
    cur = float(getattr(c, "fade_seconds", lambda: 0.0)())         if callable(getattr(c, "fade_seconds", None)) else 0.0
    from PyQt6.QtWidgets import QInputDialog
    val, ok = QInputDialog.getDouble(
        self, "自动淡入淡出", "淡入淡出时间（秒，0 = 瞬时）：", cur, 0.0, 30.0, 2)
    if not ok:
        return
    store = _store()
    if store is None:
        return
    store.set_fade(cid, float(val))
    _after_binding_change(self)
    self._status.setText(
        f"「{c.name}」淡入淡出 = {float(val):.2f}s" if val else f"「{c.name}」已设为瞬时（不淡化）")


def _set_priority(self, *, c, cid) -> None:
    """「优先级…」：设置该场景的通道接管优先级（0=未设，1-5 档，5 最高）。

    ★ R178 革新：高优先级场景已 stamp 的通道，低优先级场景点亮时**跳过 stamp**
    （不抢通道）；优先级相同（含都未设）维持既有"后发接管"语义不变。
    """
    cur = 0
    try:
        cur = int(getattr(c, "priority_level", lambda: 0)())
    except Exception:
        cur = 0
    from PyQt6.QtWidgets import QInputDialog
    val, ok = QInputDialog.getInt(
        self, "场景优先级",
        "优先级（0 = 未设·后发接管不变；1-5 档，5 最高保通道）：",
        cur, 0, 5, 1)
    if not ok:
        return
    store = _store()
    if store is None:
        return
    store.set_priority(cid, int(val))
    _after_binding_change(self)
    self._status.setText(
        f"「{c.name}」优先级 = {int(val)}"
        if val else f"「{c.name}」已清除优先级（回到后发接管）")


def _open_fader_takeover(self, *, c, cid) -> None:
    """「推杆接管…」：弹出该场景的增益推杆小窗（实时缩放其层输出）。"""
    from .fader_takeover import open_fader_takeover
    open_fader_takeover(self, cid, scene_name=str(getattr(c, "name", "") or ""))


def _capture_key(parent, current: str = "") -> Optional[str]:
    """弹一个"请按键"对话框 → 返回归一化键名；取消返回 None（空串 = 解绑）。

    非阻塞替代：用一个极简 QDialog 抓 keyPressEvent（比 QKeySequenceEdit 更可控，
    能按我们的 `scene_keys.normalize_key` 归一化与拒绝保留键）。
    """
    from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout
    dlg = QDialog(parent)
    dlg.setWindowTitle("绑定键盘映射")
    lay = QVBoxLayout(dlg)
    hint = QLabel(
        "当前：%s\n\n请直接按下要绑定的按键…\n（Esc 取消，Delete/Backspace 解绑）"
        % (current or "未绑定"),
        dlg)
    lay.addWidget(hint)
    result = {"key": None}

    def _on_key(ev):
        from PyQt6.QtCore import Qt as _Qt
        k = ev.key()
        if k in (_Qt.Key.Key_Escape,):
            dlg.reject()
            return
        if k in (_Qt.Key.Key_Delete, _Qt.Key.Key_Backspace):
            result["key"] = ""
            dlg.accept()
            return
        name = _key_name(_Qt, k)
        norm = _keys.normalize_key(name)
        if norm:
            result["key"] = norm
            dlg.accept()

    dlg.keyPressEvent = _on_key
    from ..editor_workspace_regions import popup
    popup(dlg).exec()
    return result["key"]


def _key_name(qt, key_code) -> str:
    """Qt 键码 → 名字（`Qt.Key_P` → "P"；功能键 → "F5"）。"""
    try:
        return str(qt.Key(key_code).name)
    except Exception:
        return ""


def _after_binding_change(self) -> None:
    """绑定/淡化改动后统一刷新 + 置脏（随项目持久化）。"""
    try:
        self.refresh_saved()
    except Exception:
        pass
    try:
        self.cacheChanged.emit()
    except Exception:
        pass


def _store():
    """取效果缓存库（单一真源）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        return get_effect_cache_store()
    except Exception:
        return None




BINDING_HANDLERS = (_bind_key, _set_fade, _set_priority, _open_fader_takeover,
                    _capture_key)

__all__ = ["BINDING_HANDLERS", "_bind_key", "_set_fade", "_set_priority",
           "_open_fader_takeover", "_capture_key"]
