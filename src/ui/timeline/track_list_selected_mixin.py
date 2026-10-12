"""
track_list_selected_mixin.py

作用：
    给 TrackList 提供“点击轨道头部 → 高亮选中”的能力

特点：
    - 兼容 Python 3.8+
    - 不使用 str | None（避免版本问题）
    - 不使用危险 f-string 嵌套
    - 不破坏原 mousePressEvent（安全调用原逻辑）
"""

from PyQt6.QtWidgets import QLabel

# 2026-08-27 D5：回退样式色值改引主题令牌（值与原字面量一致，仅消除散落 hex）。
try:
    from ..qascade_theme import (   # 修前 .qascade_theme（timeline 包内不存在）→ 恒走硬编码回退色
        SELECT_BLUE as _C_SELECT_BLUE,
        FOCUS_BLUE as _C_FOCUS_BLUE,
        BG_EDITOR as _C_BG_EDITOR,
        TEXT_SELECTED_FG as _C_TEXT_ON_SEL,
        TEXT_TERTIARY as _C_TEXT_NORMAL,
    )
except Exception:  # pragma: no cover - 极端环境保持旧字面量
    _C_SELECT_BLUE = "#4772B3"
    _C_FOCUS_BLUE = "#71A8FF"
    _C_BG_EDITOR = "#303030"
    _C_TEXT_ON_SEL = "#FFFFFF"
    _C_TEXT_NORMAL = "#CCCCCC"


class TrackSelectedMixin:
    """
    依赖（子类必须提供）：
        self._headers: dict {track_id: header_widget}
        self.track_selected: pyqtSignal(str)
    """

    # --------------------------------------------------
    # 初始化
    # --------------------------------------------------
    def _init_selection(self):
        self._selected_track_id = None

    # --------------------------------------------------
    # 注册点击
    # --------------------------------------------------
    def _register_header_click(self, header, track_id):
        """
        安全方式：
        不覆盖原 mousePressEvent，而是包一层
        """

        old_handler = getattr(header, "mousePressEvent", None)

        def new_handler(event):
            # 先执行原逻辑（如果有）
            if old_handler:
                old_handler(event)

            # 再执行选中逻辑
            self._on_header_clicked(track_id)

        header.mousePressEvent = new_handler

    # --------------------------------------------------
    # 点击逻辑
    # --------------------------------------------------
    def _on_header_clicked(self, track_id):
        if self._selected_track_id == track_id:
            # 取消选中
            self._selected_track_id = None
            self._apply_selection(None)
            self.track_selected.emit("")
        else:
            # 选中
            self._selected_track_id = track_id
            self._apply_selection(track_id)
            self.track_selected.emit(track_id)

    # --------------------------------------------------
    # 刷新高亮
    # --------------------------------------------------
    def _apply_selection(self, selected_id):
        headers = getattr(self, "_headers", {})
        for tid, header in headers.items():
            is_selected = (tid == selected_id)
            _set_header_selected(header, is_selected)

    # --------------------------------------------------
    # 外部同步（不发信号）
    # --------------------------------------------------
    def set_selected(self, track_id):
        self._selected_track_id = track_id
        self._apply_selection(track_id)

    # --------------------------------------------------
    # 属性
    # --------------------------------------------------
    @property
    def selected_track_id(self):
        return self._selected_track_id


# ======================================================
# 工具函数（独立，避免类污染）
# ======================================================

def _set_header_selected(header, selected):
    """
    设置 header 高亮

    优先用 header.set_selected
    否则 fallback 到 stylesheet
    """

    # 如果 header 自带方法
    if hasattr(header, "set_selected"):
        try:
            header.set_selected(selected)
            return
        except Exception:
            pass

    # 回退样式（令牌引用；TrackHeader 自带 set_highlight，本路径仅兜底）
    if selected:
        bg = _C_SELECT_BLUE
        border = _C_FOCUS_BLUE
        text_color = _C_TEXT_ON_SEL
    else:
        bg = _C_BG_EDITOR
        border = "rgba(255,255,255,21)"
        text_color = _C_TEXT_NORMAL

    # widget 背景
    try:
        header.setStyleSheet(
            "background: " + bg + ";"
            "border-bottom: 1px solid " + border + ";"
        )
    except Exception:
        pass

    # 子 QLabel 颜色
    try:
        labels = header.findChildren(QLabel)
        for lbl in labels:
            lbl.setStyleSheet(
                "color: " + text_color + ";"
                "background: transparent;"
            )
    except Exception:
        pass