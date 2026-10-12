# -*- coding: utf-8 -*-
"""场景**键盘触发**接线（应用级按键 → 复用鼠标触发逻辑）。

用户 2026-09-14：
  "每个 scene 可以绑定键盘上的按键，按键通过**模拟鼠标点击的逻辑**去跟鼠标一样
   实现，比如一个 scene 处于 flash 模式，绑定一个按键 P，我按下 P，则触发一次。
   跟 MA 控台的即时创作一样。"

实现方式（`eventFilter`，而非 QShortcut）：
  · 需要**同时**拿到 KeyPress 与 KeyRelease（flash 是"按住触发/松手释放"），
    QShortcut 只给"按下"，故用事件过滤器；
  · 挂在 `QApplication` 上 → 任意页面按都生效（MA 控台式即时创作）；
  · **文本输入焦点时不吞键**（复用项目既有纪律：正在打字就别抢按键）。

与鼠标完全同路：按下调 `_on_store_pressed`（flash）/`_on_store_activated`（latch），
松开调 `_on_store_released`（flash）/`_deactivate_scene`（latch 开关）。

★★ 深查后的四条修正（2026-09-14）：
  · **H4 模态对话框优先**：绑定按键时那个"请按键"对话框本身是模态的，若不先让它，
    按 P 会被这里截走并触发场景（甚至被 `event.isAutoRepeat` 吞掉），对话框永远
    收不到这个键 → 无法绑定。→ 只要有 `activeModalWidget()` 就一律放行。
  · **M1 失焦兜底释放**：flash 是"按住才亮"。若按着 P 时切走窗口（Alt+Tab / 弹窗），
    KeyRelease 永远不来 → 场景**永久卡在激活**（还占着输出）。→ 窗口/应用失活时把
    当前按住的场景全部按 flash 语义释放。
  · **M2 释放用**按下时**记录的模式**：原本松手时**重读**当前模式；若用户在按住期间把
    flash 改成 latch，松手就不会释放（或反之误释放）。→ 按下时把它固定下来。
  · **L1/L4**：文本控件补 `QPlainTextEdit`；同时只允许**一个**面板挂过滤器（多开页面时
    否则同一按键被触发多次），新面板接管即卸掉旧的。
"""
from __future__ import annotations

from typing import Dict, List

import logging

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QLineEdit,
                             QPlainTextEdit, QSpinBox, QTextEdit)

from . import scene_keys as _keys

_log = logging.getLogger(__name__)

#: 当前生效的过滤器（L4：应用级只允许一个；强引用防被 GC）
_ACTIVE: "SceneKeyFilter | None" = None

#: "窗口/应用失活"事件集；**按存在性取**，不硬写枚举名。
#: ★ 踩过的坑：`QEvent.Type.WindowBlockedByModal` 在当前 PyQt6 构建里**不存在**，
#:   硬写在元组里会让每次 `eventFilter` 在构造元组时抛 AttributeError，而外层
#:   `except Exception: return False` 会把它吞掉 → **整个按键过滤器静默失效**
#:   （所有键都不触发，且看起来"没报错"）。故这里必须惰性、容错地组装。
def _deactivate_events() -> tuple:
    T = QEvent.Type
    names = ("WindowDeactivate", "ApplicationDeactivate",
             "ApplicationStateChange", "WindowBlockedByModal")
    return tuple(getattr(T, n) for n in names if hasattr(T, n))


_DEACTIVATE_EVENTS = _deactivate_events()


def _text_focus_active() -> bool:
    """当前焦点是否在文本/数值输入控件上（则应让用户打字，不抢键）。

    ★ 与项目既有约定同款（effect_browser/matrix 的 `_focused_text_input_active`），
      但那两个是面板内的 `self.focusWidget()`；本过滤器是**应用级**，故用
      `QApplication.focusWidget()`。
    """
    try:
        fw = QApplication.focusWidget()
    except Exception:
        return False
    if fw is None:
        return False
    return isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit,
                           QSpinBox, QDoubleSpinBox, QComboBox))


def _modal_active() -> bool:
    """有模态对话框在最前（绑定按键对话框 / 确认框 / 文件框…）→ 一律放行。"""
    try:
        return QApplication.activeModalWidget() is not None
    except Exception:
        return False


class SceneKeyFilter(QObject):
    """把绑定的按键转成"与鼠标相同"的场景触发/释放。

    由 `install_scene_key_filter(panel)` 创建并挂到 `QApplication`；
    面板销毁时（或 provider 失效）自动停用（`_app is None` 时全部放行）。
    """

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self._app = None
        #: {键名: [(场景 id, 按下时的模式), …]} —— 按住的键（失焦兜底释放 / M2 用）
        self._held: Dict[str, List[tuple]] = {}

    # ── 安装 / 卸载 ─────────────────────────────────────────────────────
    def install(self) -> bool:
        """挂到 QApplication（幂等）；无 QApplication 返回 False。"""
        global _ACTIVE
        try:
            app = QApplication.instance()
            if app is None:
                return False
            if self._app is app:
                return True
            if self._app is not None:
                self._app.removeEventFilter(self)
            prev = _ACTIVE
            if prev is not None and prev is not self:
                try:
                    prev.release_held("被新调度台接管")     # 旧面板不再响应
                    prev.uninstall()
                except Exception:
                    pass
            app.installEventFilter(self)
            self._app = app
            _ACTIVE = self
            return True
        except Exception:
            return False

    def uninstall(self) -> None:
        global _ACTIVE
        try:
            self.release_held("面板卸载")
        except Exception:
            pass
        try:
            if self._app is not None:
                self._app.removeEventFilter(self)
        except Exception:
            pass
        self._app = None
        if _ACTIVE is self:
            _ACTIVE = None

    # ── 事件 ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, event) -> bool:          # noqa: N802
        """按下/松开 → 复用鼠标触发逻辑；不可处理的场合一律放行（返回 False）。"""
        try:
            et = event.type()
        except Exception:
            return False
        if et in _DEACTIVATE_EVENTS:
            self._on_deactivate()
            return False
        if et not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        try:
            if _modal_active():
                return False                      # ★ H4：绑定按键对话框优先
            return self._press(event) if et == QEvent.Type.KeyPress else self._release(event)
        except Exception:
            # 触发链出错的兜底：放行比"吞掉按键"安全。但**绝不**让异常静默到
            # 连正常键都不响应 —— 故异常只包住真正会出错的触发段（上面的类型判断
            # 与失活处理都已在外层完成）。
            return False

    def _on_deactivate(self) -> None:
        """窗口/应用失活：把**键盘与鼠标**按住的 flash 全部兜底释放。

        · 键盘：★ M1 —— 按住 P 时切走窗口，KeyRelease 永远不来 → 场景永久卡住；
        · 鼠标：★ R183 T8 —— 按住卡片切走窗口，Qt 不派发 MouseRelease，同款卡住。
          两路合成"失焦 = 释放全部按住"的完整语义（外提压 eventFilter CC/行数）。
        """
        try:
            self.release_held("窗口失焦")
        except Exception:
            _log.debug("键盘按住兜底释放失败", exc_info=True)
        try:
            fn = getattr(self._panel, "_release_mouse_flash", None)
            if callable(fn):
                fn()
        except Exception:
            _log.debug("鼠标按住兜底释放失败", exc_info=True)

    def _press(self, event) -> bool:
        ids = self._mapped_ids(event)
        if not ids:
            return False
        if event.isAutoRepeat():
            return True                 # 长按不重复触发（点动/开关都只认一次）
        key = _keys.normalize_key(_keys.key_name_from_qt(event.key()))
        # ★ M2：把"按下这一刻"的模式固定下来，松手时按它释放（用户中途改模式也不跑偏）
        held = [(cid, self._scene_mode(cid)) for cid in ids]
        self._held[key or ""] = held
        for cid, mode in held:
            try:
                if mode == "flash":
                    self._panel._on_store_pressed(cid)
                else:
                    self._panel._on_store_activated(cid)
            except Exception:
                continue
        return True

    def _release(self, event) -> bool:
        key = _keys.normalize_key(_keys.key_name_from_qt(event.key()))
        held = self._held.pop(key or "", None)
        if held is None:
            # 不是我们按下的（例如在文本焦点里按下才发现映射）→ 放行
            if not self._mapped_ids(event):
                return False
            held = []
        for cid, mode in held:
            try:
                if mode == "flash":
                    self._panel._on_store_released(cid)
                # latch 是开关（按下已切换），松开不动 —— 与鼠标一致
            except Exception:
                continue
        return True

    def release_held(self, _why: str = "") -> None:
        """把当前**按住的**场景全部按 flash 语义释放（失焦/卸载兜底）。"""
        for key, held in list(self._held.items()):
            self._held.pop(key, None)
            for cid, mode in held:
                if mode != "flash":
                    continue
                try:
                    self._panel._on_store_released(cid)
                except Exception:
                    continue

    def _mapped_ids(self, event) -> list:
        """该按键映射到的场景 id；不该接管则返回空（含修饰键/文本焦点/保留键）。"""
        if self._app is None or _text_focus_active():
            return []
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier |
                   Qt.KeyboardModifier.AltModifier |
                   Qt.KeyboardModifier.MetaModifier):
            return []                       # 带修饰键交给系统/既有快捷键
        key = _keys.normalize_key(_keys.key_name_from_qt(event.key()))
        return self._scene_ids_for(key) if key else []

    # ── 触发 ────────────────────────────────────────────────────────────
    def _scene_ids_for(self, key: str) -> list:
        """该键映射到的场景 id（**一个键可映射多个场景** —— 用户明确要求）。"""
        try:
            caches = list(self._panel.list.items() or [])
        except Exception:
            return []
        ids = _keys.scenes_for_key(caches, key)
        if not ids:
            return []
        # 只保留仍在库里的
        try:
            alive = {str(getattr(c, "id", "")) for c in caches}
            return [i for i in ids if i in alive]
        except Exception:
            return ids

    @staticmethod
    def _scene_mode(cid: str) -> str:
        """该场景的触发模式（归一化；取不到按 latch）。"""
        try:
            from .store_area_ops import _cache_or_none, _scene_mode_of
            cache = _cache_or_none(cid)
            return _scene_mode_of(cache) if cache is not None else "latch"
        except Exception:
            return "latch"


def install_scene_key_filter(panel) -> bool:
    """给调度台面板装键盘过滤器（幂等）。返回是否成功。"""
    try:
        f = getattr(panel, "_scene_key_filter", None)
        if f is None:
            f = SceneKeyFilter(panel)
            panel._scene_key_filter = f
        return bool(f.install())
    except Exception:
        return False


__all__ = ["SceneKeyFilter", "install_scene_key_filter"]
