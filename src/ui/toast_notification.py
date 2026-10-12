"""轻量级成功通知 — Toast 提示系统。

用于非阻塞式成功反馈：保存完成、导出成功、构建完成等。
相比 QMessageBox，Toast 不阻塞交互、自动消失、视觉轻盈。

R181 改造：图标从 Unicode 字符（✓/ℹ）改为 **tabler 矢量图标**
（全仓禁 emoji/符号字形口径；色走 qascade_theme 令牌）。

用法示例:
    from src.ui.toast_notification import show_success_toast

    # 简单成功提示
    show_success_toast(parent_widget, "项目已保存")

    # 带详情的提示
    show_success_toast(parent_widget, "导出完成", "已导出到: output.mvr")

    # 自定义持续时间（毫秒）
    show_success_toast(parent_widget, "构建完成", duration_ms=5000)
"""
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QLabel, QFrame, QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
import weakref

from . import qascade_theme as _th

# 同父窗口在显 Toast 的弱引用集（垂直堆叠定位用；Toast 销毁自动移除）
_LIVE_TOASTS = weakref.WeakSet()

#: 圆角面板皮肤（QFrame 持背景；文字透明；色值走令牌不出裸值）
_BODY_QSS = (
    f"#ToastBody{{background:{_th.BG_PANEL};border:1px solid {_th.BORDER_NORMAL};border-radius:8px;}}"
    f"#ToastBody QLabel{{color:{_th.TEXT_PRIMARY};background:transparent;}}"
)
_MSG_QSS = "font-size:11pt;font-weight:600;background:transparent;"
_DETAIL_QSS = f"font-size:10pt;color:{_th.TEXT_SECONDARY};background:transparent;"


class ToastWidget(QWidget):
    """Toast 通知窗口 — 浮动在父窗口上方的半透明提示框。

    设计原则:
    - 非模态，不阻塞交互
    - 自动淡入淡出
    - 屏幕中央偏下显示（同父多条垂直堆叠）
    - 3秒后自动消失
    - 图标 = tabler 矢量（禁 emoji/Unicode 字形）
    """

    def __init__(
        self,
        parent: QWidget,
        message: str,
        detail: Optional[str] = None,
        duration_ms: int = 3000,
        *,
        icon_key: str = "",
        icon_color: str = "",
    ):
        """初始化 Toast 通知

        Args:
            parent: 父窗口（Toast 将居中显示在其上方）
            message: 主消息（简短，一行）
            detail: 详细信息（可选，第二行小字）
            duration_ms: 显示持续时间（毫秒），默认 3000ms
            icon_key: tabler 图标名（空=无图标）
            icon_color: 图标色（空=亮成功绿令牌）
        """
        super().__init__(parent)
        self._duration_ms = duration_ms

        # 窗口属性：无边框、置顶、半透明背景
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # 圆角面板（背景在 QFrame 上，图标+文字都在其内）
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._body = QFrame(self)
        self._body.setObjectName("ToastBody")
        self._body.setStyleSheet(_BODY_QSS)
        outer.addWidget(self._body)
        row = QHBoxLayout(self._body)
        row.setContentsMargins(20, 10, 22, 10)
        row.setSpacing(10)

        self._icon = QLabel(self._body)
        self._set_icon(icon_key, icon_color)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._label = QLabel(str(message or ""), self._body)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(_MSG_QSS)
        col.addWidget(self._label)
        self._detail = QLabel(str(detail or ""), self._body)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(_DETAIL_QSS)
        self._detail.setVisible(bool(detail))
        col.addWidget(self._detail)
        row.addLayout(col, 1)

        # 调整大小以适应内容
        self._body.adjustSize()
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

        # 不透明度动画效果
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)

        # 淡入动画
        self._fade_in = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_in.setDuration(200)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 淡出动画
        self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_out.setDuration(200)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self.close)

        # 定时器：显示指定时间后开始淡出
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_fade_out)

    def _set_icon(self, icon_key: str, icon_color: str) -> None:
        """挂 tabler 图标（缺失/未命中→隐藏图标位，文本照常）。"""
        pm = None
        if icon_key:
            try:
                from .main_icon_library import load_tabler_pixmap
                pm = load_tabler_pixmap(
                    str(icon_key), 18, str(icon_color or _th.BRIGHT_SUCCESS))
            except Exception:
                pm = None
        if pm is not None and not pm.isNull():
            self._icon.setPixmap(pm)
            self._icon.setFixedSize(pm.size())
        else:
            self._icon.hide()

    def show_toast(self):
        """显示 Toast（自动定位到父窗口中央偏下；同父多条垂直堆叠不重叠）"""
        if self.parent():
            parent_rect = self.parent().rect()
            x = (parent_rect.width() - self.width()) // 2
            y = int(parent_rect.height() * 0.7)
            # 垂直堆叠：同父窗口下已在显示的 Toast，向上逐条错开（R177 已知问题修复）
            live = [t for t in _LIVE_TOASTS
                    if t is not self and not t.isHidden() and t.parent() is self.parent()]
            y = max(24, y - sum(t.height() + 8 for t in live))
            self.move(x, y)

        _LIVE_TOASTS.add(self)
        self.destroyed.connect(lambda *_: _LIVE_TOASTS.discard(self))
        self.show()
        self._fade_in.start()
        self._timer.start(self._duration_ms)

    def _start_fade_out(self):
        """开始淡出动画"""
        self._fade_out.start()

    def mousePressEvent(self, event):
        """点击 Toast 立即关闭"""
        self._timer.stop()
        self._start_fade_out()


def show_success_toast(
    parent: QWidget,
    message: str,
    detail: Optional[str] = None,
    duration_ms: int = 3000
) -> None:
    """显示成功通知 Toast

    这是最常用的便捷函数，用于快速显示成功提示。

    Args:
        parent: 父窗口
        message: 主消息（如 "保存成功"）
        detail: 详细信息（如文件路径，可选）
        duration_ms: 显示时长（毫秒），默认 3000

    示例:
        show_success_toast(self, "项目已保存")
        show_success_toast(self, "导出完成", "已导出到: output.mvr")
    """
    toast = ToastWidget(parent, message, detail, duration_ms,
                        icon_key="circle-check")
    toast.show_toast()


def show_info_toast(
    parent: QWidget,
    message: str,
    detail: Optional[str] = None,
    duration_ms: int = 2500
) -> None:
    """显示信息通知 Toast

    用于一般信息提示（相比成功提示，视觉上更轻量）。

    Args:
        parent: 父窗口
        message: 主消息
        detail: 详细信息（可选）
        duration_ms: 显示时长（毫秒），默认 2500
    """
    toast = ToastWidget(parent, message, detail, duration_ms,
                        icon_key="info-circle", icon_color=_th.BRIGHT_INFO)
    toast.show_toast()
