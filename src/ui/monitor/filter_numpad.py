"""
Qascade V1.0 — Filter Numpad
MA 控台风格的通道选择小键盘
支持：数字、THRU、BY、NOT、ALL、CLEAR、ENTER
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLineEdit, QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent

# S1 令牌化 → D 波任务 D 键帽解剖：配色统一走中心主题令牌
from ..qascade_theme import (
    BG_BUTTON, BG_BUTTON_HOVER, SELECT_BLUE, FOCUS_BLUE, INFO_BLUE,
    BORDER_NORMAL, BG_INPUT, TEXT_PRIMARY, TEXT_TERTIARY,
    SUCCESS_GREEN, BRIGHT_SUCCESS, BRIGHT_WARN,
    STATUS_TEXT,
)

# SUCCESS_GREEN / SELECT_BLUE / INFO_BLUE 的 rgba 整数分量（QSS alpha 用 0-255）
_SUCCESS_RGB = (24, 134, 37)     # #188625
_SUCCESS_BRD = (86, 182, 91)     # BRIGHT_SUCCESS


class NumpadButton(QPushButton):
    """MA 小键盘键帽（D 波任务 D 解剖学）。

    - 数字键（square=True）：36px 方形、圆角 4、数字 12px text_primary；
    - 功能键：11px text_tertiary、hover bg_button_hover、圆角 4；
    - 按下态统一 select_blue 填充白字；focus 走 focus_blue 环；
    - THRU/BY/ALL 保成功绿 hue 的语义强调（success 家族映射）；
    - ENTER 由调用方套 primary 蓝填充白字（确认语义）。
    """

    def __init__(self, text: str, accent: bool = False,
                 square: bool = False, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(36)          # 任务 D：键帽 36px
        if square:
            self.setFixedWidth(36)
        if accent:
            # 成功/开启语义键（THRU/BY/ALL）：保绿 hue，映射到 success 家族
            base = f"rgba({_SUCCESS_RGB[0]},{_SUCCESS_RGB[1]},{_SUCCESS_RGB[2]},55)"
            hover = f"rgba({_SUCCESS_RGB[0]},{_SUCCESS_RGB[1]},{_SUCCESS_RGB[2]},95)"
            press = f"rgb({_SUCCESS_BRD[0]},{_SUCCESS_BRD[1]},{_SUCCESS_BRD[2]})"
            fg = BRIGHT_SUCCESS
            brd = (f"rgba({_SUCCESS_BRD[0]},{_SUCCESS_BRD[1]},"
                   f"{_SUCCESS_BRD[2]},80)")
            fs = 11          # 功能键 11px（任务 D）
        else:
            base = BG_BUTTON           # #545454 标准按钮面
            hover = BG_BUTTON_HOVER    # #656565 hover（任务 D）
            press = SELECT_BLUE        # 按下=选中蓝填充
            fg = TEXT_PRIMARY
            brd = BORDER_NORMAL
            fs = 12          # 数字键 12px（任务 D）
        self.setStyleSheet(f"""
            QPushButton {{
                background: {base};
                color: {fg};
                border: 1px solid {brd};
                border-radius: 4px;
                font-size: {fs}px;
                font-weight: normal;
                padding: 0 8px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {press}; color: #FFFFFF; }}
            QPushButton:focus {{ border-color: {FOCUS_BLUE}; }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class FilterNumpad(QWidget):
    """
    MA 风格小键盘
    发出 rule_changed(set) 信号，包含新的通道集合（1-512）
    """
    rule_changed   = pyqtSignal(set)     # 新的通道集合
    expression_changed = pyqtSignal(str) # 当前表达式字符串（供显示）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens: list = []          # 词元列表
        self._current_input: str = ""    # 当前正在输入的数字
        self._result_channels: set = set()
        self._setup_ui()

    def _setup_ui(self):
        # S1 重构：删除裸 background 通配覆写；底色由宿主 GroupBox/全局主题提供。
        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(6, 6, 6, 6)
        vlay.setSpacing(4)

        # 表达式显示栏（金=输入值惯例 → bright_warn 文字，audit-B 惯例保留）
        self._expr_display = QLineEdit()
        self._expr_display.setReadOnly(True)
        self._expr_display.setPlaceholderText("在此输入通道表达式...")
        self._expr_display.setStyleSheet(f"""
            QLineEdit {{
                background: {BG_INPUT};
                color: {BRIGHT_WARN};
                border: 1px solid {BORDER_NORMAL};
                border-radius: 4px;
                font-size: 13px;
                font-family: Consolas, monospace;
                padding: 4px 8px;
            }}
        """)
        self._expr_display.setToolTip(
            "通道表达式：数字、THRU(至)、BY(步长)、+(并)、NOT(排除)、ALL")
        self._expr_display.setFixedHeight(32)
        vlay.addWidget(self._expr_display)

        # 操作符按钮行
        op_tips = {
            "THRU": "范围连接符：1 THRU 10 = 通道 1~10",
            "BY":   "步长：1 THRU 9 BY 2 = 1,3,5,7,9",
            "NOT":  "从当前选择中排除其后输入的通道",
            "ALL":  "全选 512 个通道",
            "CLEAR": "清空表达式与当前选择",
        }
        op_row = QHBoxLayout()
        op_row.setSpacing(4)
        for txt, acc in [("THRU", True), ("BY", True), ("NOT", True),
                          ("ALL", True), ("CLEAR", False)]:
            btn = NumpadButton(txt, accent=(acc and txt in ("THRU", "BY", "ALL")))
            btn.setToolTip(op_tips.get(txt, ""))
            btn.clicked.connect(lambda _, t=txt: self._on_op(t))
            op_row.addWidget(btn)
        vlay.addLayout(op_row)

        # 数字键盘
        grid = QGridLayout()
        grid.setSpacing(4)
        digits = [("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
                  ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
                  ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
                  ("0", 3, 0), ("+", 3, 1)]
        for txt, r, c in digits:
            btn = NumpadButton(txt, square=True)   # 任务 D：36px 方形数字键
            btn.clicked.connect(lambda _, t=txt: self._on_digit(t))
            grid.addWidget(btn, r, c)

        # ENTER 按钮（primary 确认语义；竖跨：两行键高 36×2 + 行距 4 = 76）
        enter_btn = NumpadButton("ENTER", accent=True)
        enter_btn.setFixedHeight(76)
        enter_btn.setToolTip("解析表达式并应用为当前通道选择")
        enter_btn.clicked.connect(self._on_enter)
        # ENTER 按钮 primary 语义：select_blue 填充白字（取代旧高亮蓝）
        enter_btn.setStyleSheet(f"""
            QPushButton {{ background: {SELECT_BLUE}; color: #FFFFFF;
                border: 1px solid {SELECT_BLUE}; border-radius: 4px;
                font-size: 12px; font-weight: normal; }}
            QPushButton:hover {{ background: #5480C5; }}
            QPushButton:pressed {{ background: {INFO_BLUE}; }}
            QPushButton:focus {{ border-color: {FOCUS_BLUE}; }}
        """)
        grid.addWidget(enter_btn, 2, 3, 2, 1)

        # BACKSPACE（⌫ 字形走 Segoe UI Symbol 兜底，避免等宽字体缺字形 tofu）
        back_btn = NumpadButton("⌫", square=True)
        back_btn.setStyleSheet(back_btn.styleSheet() +
            "QPushButton{font-family:'Segoe UI Symbol','Segoe UI',sans-serif;}")
        back_btn.setToolTip("退格：删除最后一位数字 / 最后一个词元")
        back_btn.clicked.connect(self._on_backspace)
        grid.addWidget(back_btn, 3, 2)

        vlay.addLayout(grid)

        # 结果预览（D波：24px 解剖详情行，状态色走 STATUS_TEXT）
        self._result_label = QLabel("通道: 未选择")
        self._result_label.setFixedHeight(24)
        self._set_result_style("dim")
        vlay.addWidget(self._result_label)

    # ── 输入处理 ──────────────────────────────────

    def _on_digit(self, txt: str):
        if txt == "+":
            # 连接运算符
            self._flush_current()
            self._tokens.append("+")
        else:
            self._current_input += txt
        self._refresh_display()

    def _on_op(self, op: str):
        if op == "CLEAR":
            self._tokens.clear()
            self._current_input = ""
            self._result_channels.clear()
            self._refresh_display()
            self._update_result_label()
            return
        if op == "ALL":
            self._tokens = ["ALL"]
            self._current_input = ""
            self._refresh_display()
            return
        if op == "NOT":
            self._flush_current()
            self._tokens.append("NOT")
        elif op in ("THRU", "BY"):
            self._flush_current()
            self._tokens.append(op)
        self._refresh_display()

    def _on_backspace(self):
        if self._current_input:
            self._current_input = self._current_input[:-1]
        elif self._tokens:
            self._tokens.pop()
        self._refresh_display()

    def _flush_current(self):
        if self._current_input:
            self._tokens.append(self._current_input)
            self._current_input = ""

    def _on_enter(self):
        self._flush_current()
        channels = self._parse_tokens(self._tokens)
        self._result_channels = channels
        self.rule_changed.emit(channels)
        self._update_result_label()

    # ── 解析表达式 ────────────────────────────────

    def _parse_tokens(self, tokens: list) -> set:
        """
        解析词元列表 → 通道集合
        支持:
          ALL
          n
          n THRU m
          n THRU m BY k
          NOT n (从当前集合排除)
          n + m + ... (union)
        """
        result = set()
        i = 0
        toks = [t.strip() for t in tokens if t.strip()]

        if not toks:
            return result

        if toks == ["ALL"]:
            return set(range(1, 513))

        # 处理 NOT
        exclude = set()
        filtered_toks = []
        j = 0
        while j < len(toks):
            if toks[j] == "NOT" and j + 1 < len(toks):
                try:
                    exclude.add(int(toks[j + 1]))
                    j += 2
                    continue
                except ValueError:
                    pass
            filtered_toks.append(toks[j])
            j += 1

        # 按 "+" 分段
        segments = []
        seg = []
        for t in filtered_toks:
            if t == "+":
                if seg:
                    segments.append(seg)
                seg = []
            else:
                seg.append(t)
        if seg:
            segments.append(seg)

        for seg in segments:
            result |= self._parse_segment(seg)

        return result - exclude

    def _parse_segment(self, seg: list) -> set:
        """解析单段: n / n THRU m / n THRU m BY k"""
        try:
            if len(seg) == 1:
                v = int(seg[0])
                return {v} if 1 <= v <= 512 else set()
            if len(seg) == 3 and seg[1] == "THRU":
                start, end = int(seg[0]), int(seg[2])
                return {ch for ch in range(min(start, end), max(start, end) + 1)
                        if 1 <= ch <= 512}
            if len(seg) == 5 and seg[1] == "THRU" and seg[3] == "BY":
                start, end, step = int(seg[0]), int(seg[2]), int(seg[4])
                if step <= 0:
                    step = 1
                return {ch for ch in range(min(start, end), max(start, end) + 1, step)
                        if 1 <= ch <= 512}
        except (ValueError, IndexError):
            pass
        return set()

    # ── 显示更新 ──────────────────────────────────

    def _refresh_display(self):
        parts = list(self._tokens)
        if self._current_input:
            parts.append(self._current_input)
        expr = " ".join(parts)
        self._expr_display.setText(expr)
        self.expression_changed.emit(expr)

    def _set_result_style(self, kind: str):
        """结果标签状态色统一走中心 STATUS_TEXT（dim/success/info）。"""
        self._result_label.setStyleSheet(
            f"color: {STATUS_TEXT[kind]}; font-size: 11px; padding: 0 6px;")

    def _update_result_label(self):
        n = len(self._result_channels)
        if n == 0:
            self._result_label.setText("通道: 未选择")
            self._set_result_style("dim")
        elif n == 512:
            self._result_label.setText("通道: ALL (512)")
            self._set_result_style("success")
        else:
            sample = sorted(self._result_channels)[:8]
            s = ", ".join(str(c) for c in sample)
            if n > 8:
                s += f"... (共{n}个)"
            self._result_label.setText(f"通道: {s}")
            self._set_result_style("info")

    # ── 外部调用 ──────────────────────────────────

    def set_channels_from_selection(self, channels: set):
        """从外部（框选等）同步通道集合到小键盘显示"""
        self._result_channels = channels
        # 反向生成表达式
        self._tokens = []
        self._current_input = ""
        if not channels:
            self._expr_display.setText("")
            self._update_result_label()
            return
        if channels == set(range(1, 513)):
            self._tokens = ["ALL"]
        else:
            # 简单序列化
            sorted_ch = sorted(channels)
            groups = []
            i = 0
            while i < len(sorted_ch):
                start = sorted_ch[i]
                j = i + 1
                while j < len(sorted_ch) and sorted_ch[j] == sorted_ch[j-1] + 1:
                    j += 1
                end = sorted_ch[j-1]
                if end > start:
                    groups.append([str(start), "THRU", str(end)])
                else:
                    groups.append([str(start)])
                i = j
            for idx, g in enumerate(groups):
                self._tokens.extend(g)
                if idx < len(groups) - 1:
                    self._tokens.append("+")
        self._refresh_display()
        self._update_result_label()

    def get_channels(self) -> set:
        return self._result_channels.copy()