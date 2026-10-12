"""
command_arrange_dialog — 「命令行代码编曲」子窗口(非模态)

入口:主界面 面板 菜单的「命令行代码编曲」按钮(以主窗口作宿主,不再依赖自动编曲面板)。
功能:
  · 导出项目信息(skill.md)  → 把整套语法规范+本项目真实设备/编组/配接+示例写成教学文档,
                              丢给通用大模型就能学会变成"我的自动编程师"。
  · 导入命令 / 导出命令      → .txt/.cmd 命令文件读写。
  · Word 式大文本框          → 行号 + 语法高亮 + 实时校验(输入停顿后只解析不渲染,轻量)。
  · 生成到时间线            → 后台线程解析+渲染(不卡 UI),主线程只挂轨(追加模式)。

不卡顿:解析/渲染全在 QThread;主线程仅 add_track。实时校验防抖 350ms 且只 parse。
"""
from __future__ import annotations

import os
from typing import Optional, List, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect, QSize
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QTextFormat, QSyntaxHighlighter, QTextCharFormat,
    QTextCursor, QKeyEvent,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QPlainTextEdit,
    QProgressBar, QFileDialog, QMessageBox, QWidget, QTextEdit, QMenu,
)

# D 波统一：颜色一律引用 主题令牌；对话框走 widget_kit.apply_dialog_chrome。
from .. import qascade_theme as _th
from ..widget_kit import apply_dialog_chrome

_BG = _th.BG_INPUT          # 控制台内底 #1D1D1D
_FG = _th.TEXT_PRIMARY
_ACCENT = _th.SELECT_BLUE

# 语法高亮编码色（保 hue 精修明度，§4 保护清单）：
_KW_COLOR  = _th.FOCUS_BLUE      # 关键字：select_blue 同族提亮 → focus_blue（可读性）
_CLIP_COLOR = "#E6B455"          # clip/track 块标 = bright_warn 族原值保留
_STR_COLOR = _th.BRIGHT_SUCCESS  # 字符串
_CMT_COLOR = _th.TEXT_SECONDARY  # 注释
_NUM_COLOR = "#AC7FDC"           # 数字：原 #9e6abf 紫 hue 不变、仅提升明度
try:
    from ..main_icon_library import make_main_icon as _make_icon
except ImportError:
    _make_icon = None


#: 轨道容器在 `TimelineController` 上的两种可能布局（见 `_timeline_tracks`）。
_TRACK_ATTRS = ("tracks", "_tracks")


def _timeline_tracks(ctrl) -> list:
    """取时间线控制器的轨道列表（**兼容两种真实布局**）。

    ★★ R167：旧代码用 `getattr(ctrl, "tracks", None)` 找同名轨，但
      `TimelineController` **没有** `tracks` 属性 —— 轨道实际存在
      `ctrl._session.tracks`。于是"同名轨道合并"**从未生效**：每次生成都会新建一条
      同名轨（实测生成两次得到两条 `CMD·pump`，clip 叠加而非合并，且撤销快照被绕过）。

    抽成模块级函数以便单测；按 `tracks` → `_tracks` → `_session.tracks` 顺序探测，
    任一命中即用（同一控制器只会命中一种，不存在混用）。
    """
    if ctrl is None:
        return []
    for attr in _TRACK_ATTRS:
        try:
            tracks = getattr(ctrl, attr, None)
        except Exception:
            tracks = None
        if isinstance(tracks, list):
            return tracks
    session = getattr(ctrl, "_session", None)
    tracks = getattr(session, "tracks", None)
    return tracks if isinstance(tracks, list) else []


# ─────────────────────────────────────────────────────────────────────────────
#  语法高亮
# ─────────────────────────────────────────────────────────────────────────────
class _CmdHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self._kw_fmt = QTextCharFormat(); self._kw_fmt.setForeground(QColor(_KW_COLOR))
        self._kw_fmt.setFontWeight(QFont.Weight.Bold)
        self._clip_fmt = QTextCharFormat(); self._clip_fmt.setForeground(QColor(_CLIP_COLOR))
        self._clip_fmt.setFontWeight(QFont.Weight.Bold)
        self._str_fmt = QTextCharFormat(); self._str_fmt.setForeground(QColor(_STR_COLOR))
        self._cmt_fmt = QTextCharFormat(); self._cmt_fmt.setForeground(QColor(_CMT_COLOR))
        self._cmt_fmt.setFontItalic(True)
        self._num_fmt = QTextCharFormat(); self._num_fmt.setForeground(QColor(_NUM_COLOR))
        # 关键字集合(从 ir 取,防漂移)
        self._kws = set()
        try:
            from ...engine.command_arrange import ir
            for aliases in ir.KEYWORDS.values():
                for a in aliases:
                    self._kws.add(str(a).lower())
        except Exception:
            self._kws = {"slct", "at", "dur", "fi", "fo", "dir", "sets", "seq"}
        self._kws.update({"clip", "track", "def"})

    def highlightBlock(self, text: str):
        import re
        # 注释(# 之后,排除 #hex 颜色:简化为整行 # 优先,但保留 #后跟6位hex不算注释)
        cmt = text.find("#")
        # 不把 #FFAA00 当注释:# 后紧跟 3/6 位 hex 且边界是单词
        while cmt != -1:
            tail = text[cmt + 1:cmt + 7]
            if re.match(r"^[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?\b", tail):
                nxt = text.find("#", cmt + 1)
                cmt = nxt
                continue
            self.setFormat(cmt, len(text) - cmt, self._cmt_fmt)
            text = text[:cmt]  # 注释后不再高亮
            break
        # 字符串
        for m in re.finditer(r'"[^"]*"', text):
            self.setFormat(m.start(), m.end() - m.start(), self._str_fmt)
        # clip / track / } 块标
        for m in re.finditer(r'\b(clip|track)\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self._clip_fmt)
        # 数字
        for m in re.finditer(r'\b\d+(\.\d+)?s?\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self._num_fmt)
        # 关键字
        for m in re.finditer(r'\b[A-Za-z_一-鿿]+\b', text):
            if m.group(0).lower() in self._kws:
                self.setFormat(m.start(), m.end() - m.start(), self._kw_fmt)


# ─────────────────────────────────────────────────────────────────────────────
#  带行号的编辑器
# ─────────────────────────────────────────────────────────────────────────────
class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint(event)


class CommandEditor(QPlainTextEdit):
    INDENT = 4  # 缩进=4空格(缩进敏感的 def 解析需要稳定空格,绝不用真 Tab)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lna = _LineNumberArea(self)
        self.blockCountChanged.connect(lambda _: self._update_lna_width())
        self.updateRequest.connect(self._update_lna)
        self._update_lna_width()
        f = QFont("Consolas"); f.setStyleHint(QFont.StyleHint.Monospace); f.setPixelSize(12)
        self.setFont(f)
        # 凹陷控制台：bg_input 内底 + 1px 描边 + 4px 圆角
        self.setStyleSheet(
            f"QPlainTextEdit{{background:{_th.BG_INPUT};color:{_FG};"
            f"border:1px solid {_th.BORDER_NORMAL};"
            f"border-radius:4px;padding:4px;"
            f"selection-background-color:{_th.FOCUS_BLUE};"
            f"selection-color:{_th.BG_DEEP};}}")
        self.setTabStopDistance(28)
        # 错误行的额外选区(由对话框校验填充);当前行高亮在两者之上合并
        self._error_selections: List[QTextEdit.ExtraSelection] = []
        # 提示行(非致命:dur默认/能力不符)的额外选区,琥珀底,层级在错误之下→红优先
        self._warning_selections: List[QTextEdit.ExtraSelection] = []
        self.cursorPositionChanged.connect(self._refresh_extra_selections)
        self._refresh_extra_selections()

    # ── 当前行高亮 + 错误行标记(合并为同一组 ExtraSelection)──────────────
    def _current_line_selection(self) -> "QTextEdit.ExtraSelection":
        sel = QTextEdit.ExtraSelection()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(_th.BG_BOX))
        fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.format = fmt
        cur = self.textCursor()
        cur.clearSelection()
        sel.cursor = cur
        return sel

    def set_error_lines(self, lines: List[int]):
        """lines: 1-based 行号集合。重建错误行额外选区并刷新显示。"""
        self._error_selections = []
        doc = self.document()
        seen = set()
        for ln in lines or []:
            try:
                idx = int(ln) - 1
            except Exception:
                continue
            if idx < 0 or idx in seen:
                continue
            blk = doc.findBlockByNumber(idx)
            if not blk.isValid():
                continue
            seen.add(idx)
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(self._error_bg())
            fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.format = fmt
            cur = QTextCursor(blk)
            sel.cursor = cur
            self._error_selections.append(sel)
        self._refresh_extra_selections()

    @staticmethod
    def _error_bg() -> QColor:
        """错误行底：ERROR_RED 同 hue 半透明，叠在 bg_input 上仍可读。"""
        c = QColor(_th.ERROR_RED)
        c.setAlphaF(0.32)
        return c

    @staticmethod
    def _warning_bg() -> QColor:
        """提示行底：WARN_ORANGE 同 hue 半透明（非致命提示）。"""
        c = QColor(_th.WARN_ORANGE)
        c.setAlphaF(0.32)
        return c

    def set_warning_lines(self, lines: List[int]):
        """lines: 1-based 行号集合。重建提示行(琥珀底)额外选区并刷新显示。"""
        self._warning_selections = []
        doc = self.document()
        seen = set()
        for ln in lines or []:
            try:
                idx = int(ln) - 1
            except Exception:
                continue
            if idx < 0 or idx in seen:
                continue
            blk = doc.findBlockByNumber(idx)
            if not blk.isValid():
                continue
            seen.add(idx)
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(self._warning_bg())   # 琥珀暗底,区别于错误的暗红
            fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.format = fmt
            cur = QTextCursor(blk)
            sel.cursor = cur
            self._warning_selections.append(sel)
        self._refresh_extra_selections()

    def _refresh_extra_selections(self):
        # 先铺提示(琥珀),再铺错误(红)→ 同一行有错误时红覆盖琥珀,错误优先
        sels = list(self._warning_selections)
        sels.extend(self._error_selections)
        sels.append(self._current_line_selection())
        self.setExtraSelections(sels)

    # ── 键盘:自动缩进 / Tab→空格 / 块缩进 / Ctrl+/ 注释 ────────────────────
    def keyPressEvent(self, e: QKeyEvent):
        key = e.key()
        mods = e.modifiers()
        # Ctrl+/  注释切换
        if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_Slash:
            self._toggle_comment(); return
        # Enter / Return  自动缩进
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                mods & Qt.KeyboardModifier.ShiftModifier):
            self._auto_indent_newline(); return
        # Tab / Shift+Tab
        if key == Qt.Key.Key_Backtab or (
                key == Qt.Key.Key_Tab and (mods & Qt.KeyboardModifier.ShiftModifier)):
            self._dedent_selection(); return
        if key == Qt.Key.Key_Tab:
            cur = self.textCursor()
            if cur.hasSelection() and self._selection_spans_multiline(cur):
                self._indent_selection()
            else:
                cur.insertText(" " * self.INDENT)
                self.setTextCursor(cur)
            return
        super().keyPressEvent(e)

    def _auto_indent_newline(self):
        cur = self.textCursor()
        block_text = cur.block().text()
        # 光标前的部分(用于判断是否以 ':' 结尾)
        col = cur.positionInBlock()
        before = block_text[:col]
        lead = block_text[:len(block_text) - len(block_text.lstrip(" "))]
        # 只取前导空格(忽略 tab,缩进统一空格)
        lead = ""
        for ch in block_text:
            if ch == " ":
                lead += " "
            else:
                break
        new_indent = lead
        if before.rstrip().endswith(":"):
            new_indent = lead + " " * self.INDENT
        cur.insertText("\n" + new_indent)
        self.setTextCursor(cur)

    def _selection_spans_multiline(self, cur: QTextCursor) -> bool:
        doc = self.document()
        a = doc.findBlock(cur.selectionStart()).blockNumber()
        b = doc.findBlock(cur.selectionEnd()).blockNumber()
        return a != b

    def _iter_selected_blocks(self, cur: QTextCursor):
        doc = self.document()
        start = cur.selectionStart()
        end = cur.selectionEnd()
        first = doc.findBlock(start).blockNumber()
        last = doc.findBlock(end).blockNumber()
        # 若选区结束正好在行首,不把那一整行算进来
        if last > first and doc.findBlock(end).position() == end:
            last -= 1
        return first, last

    def _indent_selection(self):
        cur = self.textCursor()
        first, last = self._iter_selected_blocks(cur)
        doc = self.document()
        edit = self.textCursor()
        edit.beginEditBlock()
        for n in range(first, last + 1):
            blk = doc.findBlockByNumber(n)
            c = QTextCursor(blk)
            c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            c.insertText(" " * self.INDENT)
        edit.endEditBlock()
        # 重选整块
        self._reselect_blocks(first, last)

    def _dedent_selection(self):
        cur = self.textCursor()
        if cur.hasSelection() and self._selection_spans_multiline(cur):
            first, last = self._iter_selected_blocks(cur)
        else:
            first = last = cur.block().blockNumber()
        doc = self.document()
        edit = self.textCursor()
        edit.beginEditBlock()
        for n in range(first, last + 1):
            blk = doc.findBlockByNumber(n)
            txt = blk.text()
            strip = 0
            while strip < self.INDENT and strip < len(txt) and txt[strip] == " ":
                strip += 1
            if strip:
                c = QTextCursor(blk)
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                c.movePosition(QTextCursor.MoveOperation.Right,
                               QTextCursor.MoveMode.KeepAnchor, strip)
                c.removeSelectedText()
        edit.endEditBlock()
        if first != last:
            self._reselect_blocks(first, last)

    def _reselect_blocks(self, first: int, last: int):
        doc = self.document()
        fb = doc.findBlockByNumber(first)
        lb = doc.findBlockByNumber(last)
        c = self.textCursor()
        c.setPosition(fb.position())
        c.setPosition(lb.position() + lb.length() - 1,
                      QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)

    def _toggle_comment(self):
        cur = self.textCursor()
        if cur.hasSelection() and self._selection_spans_multiline(cur):
            first, last = self._iter_selected_blocks(cur)
        else:
            first = last = cur.block().blockNumber()
        doc = self.document()
        # 决定加还是去:有任何非空行未注释 → 全加注释;否则全去
        any_uncommented = False
        for n in range(first, last + 1):
            txt = doc.findBlockByNumber(n).text()
            s = txt.lstrip(" ")
            if s and not s.startswith("#"):
                any_uncommented = True
                break
        edit = self.textCursor()
        edit.beginEditBlock()
        for n in range(first, last + 1):
            blk = doc.findBlockByNumber(n)
            txt = blk.text()
            s = txt.lstrip(" ")
            if not s:
                continue
            indent = len(txt) - len(s)
            c = QTextCursor(blk)
            c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            c.movePosition(QTextCursor.MoveOperation.Right,
                           QTextCursor.MoveMode.MoveAnchor, indent)
            if any_uncommented:
                c.insertText("# ")
            else:
                # 去注释:去掉前导 '# ' 或 '#'
                rest = txt[indent:]
                if rest.startswith("# "):
                    rem = 2
                elif rest.startswith("#"):
                    rem = 1
                else:
                    rem = 0
                if rem:
                    c.movePosition(QTextCursor.MoveOperation.Right,
                                   QTextCursor.MoveMode.KeepAnchor, rem)
                    c.removeSelectedText()
        edit.endEditBlock()
        if first != last:
            self._reselect_blocks(first, last)

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_lna_width(self):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_lna(self, rect, dy):
        if dy:
            self._lna.scroll(0, dy)
        else:
            self._lna.update(0, rect.y(), self._lna.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_lna_width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._lna.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint(self, event):
        painter = QPainter(self._lna)
        painter.fillRect(event.rect(), QColor(_th.BG_INPUT))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        painter.setPen(QColor(_th.TEXT_DISABLED))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._lna.width() - 5,
                                 self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            num += 1


# ─────────────────────────────────────────────────────────────────────────────
#  后台渲染线程
# ─────────────────────────────────────────────────────────────────────────────
class _RenderWorker(QThread):
    message = pyqtSignal(str)
    finished_render = pyqtSignal(object)   # (rendered_dict, report) 或 None

    def __init__(self, project, text: str, fps: int = 40, parent=None):
        super().__init__(parent)
        self._project = project
        self._text = text
        self._fps = int(fps)

    def run(self):
        try:
            from ...engine.command_arrange import parse, render
            res = parse(self._text)
            if res.errors:
                for e in res.errors:
                    self.message.emit(f"{e}")
            if not res.clips:
                self.message.emit("没有可生成的 clip。")
                self.finished_render.emit(None)
                return
            rendered, report = render(res, self._project, fps=self._fps)
            self.finished_render.emit((rendered, report))
        except Exception as e:
            import traceback
            self.message.emit(f"渲染异常:{e}")
            self.message.emit(traceback.format_exc()[-400:])
            self.finished_render.emit(None)


# ─────────────────────────────────────────────────────────────────────────────
#  主对话框
# ─────────────────────────────────────────────────────────────────────────────
class CommandArrangeDialog(QDialog):
    """命令行代码编曲子窗口。父面板需提供 _project() 与 _timeline_ctrl()。"""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._worker: Optional[_RenderWorker] = None
        self.setWindowTitle("命令行代码编曲")
        self.setModal(False)
        self.resize(820, 640)
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(350)
        self._validate_timer.timeout.connect(self._run_validation)
        self._build_ui()
        # D 波：统一浮窗外框（主/次按钮语义 + 圆角面板底）
        apply_dialog_chrome(self)
        # R54：按钮行收敛。chrome 的 min-height:24 是内容盒尺寸（Qt 实际渲染
        # 30px 高），旧 setFixedHeight(26) 被 polish 抬高、越改越大。这里仅在本
        # 对话框内把主/次钮收敛到全局规范 padding（2px 10px）并放开 min-height，
        # 配合 setFixedHeight(24) 得到真实 24px 实高；颜色/悬停/按下仍走 chrome
        # 的 select_blue 规范，不自造配色。
        self.setStyleSheet(self.styleSheet() + """
QDialog QPushButton#primary, QDialog QPushButton[role="cancel"] {
    min-height: 18px; padding: 2px 10px;
}
""")

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(8)

        # 顶部按钮行（外观走全局按钮主题，不再页面自造 QSS）
        top = QHBoxLayout(); top.setSpacing(6)
        self._btn_export_skill = QPushButton("导出项目信息(教AI)")
        self._btn_export_skill.setIcon(self._tab("upload"))
        self._btn_export_skill.setToolTip("生成一份教学 Markdown:语法规范+本项目真实设备/编组/配接+示例,"
                                          "丢给大模型就能学会按本语法编曲。")
        self._btn_export_skill.clicked.connect(self._on_export_skill)
        top.addWidget(self._btn_export_skill)

        self._btn_import = QPushButton("导入命令")
        self._btn_import.setIcon(self._tab("download"))
        self._btn_import.setToolTip("从 .cmd/.txt 命令文件读取内容到编辑器。")
        self._btn_import.clicked.connect(self._on_import)
        top.addWidget(self._btn_import)

        self._btn_export_cmd = QPushButton("导出命令")
        self._btn_export_cmd.setIcon(self._tab("database-export"))
        self._btn_export_cmd.setToolTip("把编辑器内命令保存为 .cmd/.txt 文件。")
        self._btn_export_cmd.clicked.connect(self._on_export_cmd)
        top.addWidget(self._btn_export_cmd)

        self._btn_proj_ref = QPushButton("项目设备/组")
        self._btn_proj_ref.setIcon(self._tab("folder"))
        self._btn_proj_ref.setToolTip("插入本项目的编组名 / 通道类型到光标处,免去手翻配接表。")
        self._btn_proj_ref.clicked.connect(self._show_project_ref)
        top.addWidget(self._btn_proj_ref)

        top.addStretch()
        lay.addLayout(top)

        hint = QLabel("一个 clip{ } 块 = 时间线上一个 clip。点「导出项目信息」喂给大模型即可生成命令;"
                      "也可手写。生成为追加模式,轨道带 CMD· 前缀。")
        hint.setWordWrap(True)
        hint.setProperty("hint", True)
        hint.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:11px;background:transparent;")
        lay.addWidget(hint)

        # 编辑器
        self._editor = CommandEditor()
        self._highlighter = _CmdHighlighter(self._editor.document())
        self._editor.setPlainText(self._sample_text())
        self._editor.textChanged.connect(lambda: self._validate_timer.start())
        lay.addWidget(self._editor, 1)

        # 校验状态行
        vrow = QHBoxLayout(); vrow.setSpacing(8)
        self._validate_lbl = QLabel("")
        self._validate_lbl.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:11px;background:transparent;")
        self._validate_lbl.setWordWrap(True)
        vrow.addWidget(self._validate_lbl, 1)
        self._btn_jump_err = QPushButton("↪ 跳到首个错误")
        # 语义色：警告琥珀族（bright_warn）文字 + 中性面，不算通用控件外观重复
        self._btn_jump_err.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_th.BRIGHT_WARN};"
            f"border:1px solid {_th.BORDER_NORMAL};"
            f"border-radius:4px;font-size:11px;padding:2px 8px;}}"
            f"QPushButton:hover{{background:{_th.BG_PANEL};}}"
            f"QPushButton:disabled{{color:{_th.TEXT_DISABLED};"
            f"border-color:{_th.POPUP_BORDER};}}")
        self._btn_jump_err.setToolTip("把光标定位到第一个有语法问题的行。")
        self._btn_jump_err.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_jump_err.clicked.connect(self._jump_to_first_error)
        self._btn_jump_err.setEnabled(False)
        vrow.addWidget(self._btn_jump_err)
        lay.addLayout(vrow)
        self._error_lines: List[int] = []

        # 底部:生成 + 进度（动作行右对齐；primary=主行动，cancel=次级语义）
        bottom = QHBoxLayout(); bottom.setSpacing(6)
        bottom.addStretch(1)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0); self._progress.setFixedHeight(14)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{_th.BG_INPUT};"
            f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;}}"
            f"QProgressBar::chunk{{background:{_th.SELECT_BLUE};border-radius:3px;}}")
        self._progress.setVisible(False)
        bottom.addWidget(self._progress, 1)
        self._btn_gen = QPushButton("生成到时间线")
        try:
            from ..main_icon_library import load_tabler_icon
            # player-play 语义贴「生成执行」；20px 请求给 HiDPI 位图留余量，
            # setIconSize 固定 16 逻辑 px。旧 actualSize 在 DPR>1 会把设备像素
            # 当逻辑尺寸回填 → 槽位被放大 → 图标发虚（用户点名的根因）。
            _ic = load_tabler_icon("player-play", 20, "#FFFFFF")
            if not _ic.isNull():
                self._btn_gen.setIcon(_ic)
                self._btn_gen.setIconSize(QSize(16, 16))
        except Exception:
            pass
        self._btn_gen.setObjectName("primary")
        self._btn_gen.setFixedHeight(24)
        self._btn_gen.clicked.connect(self._on_generate)
        bottom.addWidget(self._btn_gen)
        self._btn_cancel = QPushButton("取消")
        try:
            from ..main_icon_library import load_tabler_icon
            _ic2 = load_tabler_icon("x", 16, "#E36855")
            if not _ic2.isNull():
                self._btn_cancel.setIcon(_ic2)
        except Exception:
            pass
        self._btn_cancel.setProperty("role", "cancel")
        self._btn_cancel.setFixedHeight(24)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel_render)
        bottom.addWidget(self._btn_cancel)
        lay.addLayout(bottom)

        # 日志（等宽 11px 控制台；自动滚动由 appendPlainText 保持）
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True); self._log.setMaximumBlockCount(300)
        self._log.setFixedHeight(110)
        log_f = QFont("Consolas"); log_f.setStyleHint(QFont.StyleHint.Monospace)
        log_f.setPixelSize(11)
        self._log.setFont(log_f)
        self._log.setStyleSheet(
            f"QPlainTextEdit{{background:{_th.BG_INPUT};color:{_FG};"
            f"border:1px solid {_th.BORDER_NORMAL};"
            f"border-radius:4px;padding:4px;}}")
        lay.addWidget(self._log)

        QTimer.singleShot(100, self._run_validation)

    def _sample_text(self) -> str:
        return (
            '# 命令行代码编曲示例。点「导出项目信息」生成教学文档喂给大模型,可代写命令。\n'
            '# 一个 clip{ } 块 = 一个 clip。中英关键字都认。\n\n'
            'clip "主泵山形" -> track "泵" {\n'
            '  slct 1-8\n'
            '  pump: clamp(220 - 30*abs(i - n/2))\n'
            '  at 0s dur 6s\n'
            '  fi 2s fo 2s\n'
            '  dir ltr; sets 2; scope global\n'
            '}\n\n'
            'clip "彩虹追逐" -> track "灯" {\n'
            '  slct 1-8\n'
            '  def effect(c):\n'
            '      hue = (c.t*0.4 + c.i*0.08) % 1.0\n'
            '      r,g,b = hsv(hue, 1, 1)\n'
            '      c.set("R", r); c.set("G", g); c.set("B", b)\n'
            '  dur 8s; fi 1s; dir ltr; sets 4; seq\n'
            '}\n'
        )

    # ── 实时校验(只解析,不渲染)────────────────────────────────────────────
    def _run_validation(self):
        try:
            from ...engine.command_arrange import parse
            res = parse(self._editor.toPlainText())
        except Exception as e:
            self._validate_lbl.setText(f"校验异常:{e}")
            self._validate_lbl.setToolTip(str(e))
            self._validate_lbl.setStyleSheet(
                f"color:{_th.BRIGHT_ERROR};font-size:11px;background:transparent;")
            self._error_lines = []
            self._editor.set_error_lines([])
            self._btn_jump_err.setEnabled(False)
            return
        ntr = len(res.track_names())
        if res.errors:
            full = [str(e) for e in res.errors]
            # 错误行号(1-based;0 表示无法定位,跳过标记)
            self._error_lines = [getattr(e, "line", 0) for e in res.errors
                                 if getattr(e, "line", 0)]
            msg = f"{len(res.errors)} 处问题: " + " | ".join(full[:4])
            if len(res.errors) > 4:
                msg += f" …(+{len(res.errors)-4})"
            self._validate_lbl.setText(msg)
            self._validate_lbl.setToolTip("\n".join(full))  # FIX F: 完整列表挂 tooltip
            self._validate_lbl.setStyleSheet(
                f"color:{_th.BRIGHT_WARN};font-size:11px;background:transparent;")
            # FIX F: 完整错误也镜像进日志(仅当列表变化时,避免刷屏)
            sig = "\n".join(full)
            if getattr(self, "_last_err_sig", None) != sig:
                self._last_err_sig = sig
                self._append("─ 校验:" + f"{len(full)} 处问题 ─")
                for line in full:
                    self._append("  • " + line)
            self._editor.set_error_lines(self._error_lines)  # FIX E: 行内红底标记
            self._btn_jump_err.setEnabled(bool(self._error_lines))
        else:
            self._validate_lbl.setText(f"✓ {len(res.clips)} 个 clip / {ntr} 条轨道,语法通过")
            self._validate_lbl.setToolTip("")
            self._validate_lbl.setStyleSheet(
                f"color:{_th.BRIGHT_SUCCESS};font-size:11px;background:transparent;")
            self._error_lines = []
            self._last_err_sig = None
            self._editor.set_error_lines([])      # FIX E: 清除错误标记
            self._btn_jump_err.setEnabled(False)

    def _jump_to_first_error(self):
        """把光标移到首个错误行(FIX E)。"""
        if not self._error_lines:
            return
        ln = min(self._error_lines)
        doc = self._editor.document()
        blk = doc.findBlockByNumber(max(0, int(ln) - 1))
        if not blk.isValid():
            return
        cur = QTextCursor(blk)
        self._editor.setTextCursor(cur)
        self._editor.centerCursor()
        self._editor.setFocus()

    # ── 导出项目信息(skill.md)───────────────────────────────────────────────
    def _on_export_skill(self):
        proj = self._panel_project()
        try:
            from ...engine.command_arrange import build_skill_markdown, suggested_filename
            md = build_skill_markdown(proj)
            default_name = suggested_filename(proj)
        except Exception as e:
            self._append(f"生成教学文档失败:{e}")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出项目信息(教AI的Markdown)", default_name, "Markdown (*.md);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            self._append(f"已导出教学文档:{os.path.basename(path)}({len(md)} 字)。把它丢给大模型即可。")
        except Exception as e:
            self._append(f"写文件失败:{e}")

    # ── 导入 / 导出命令 ─────────────────────────────────────────────────────
    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入命令文件", "", "命令 (*.cmd *.txt *.arr);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._editor.setPlainText(f.read())
            self._append(f"已导入:{os.path.basename(path)}")
            self._run_validation()
        except Exception as e:
            self._append(f"读取失败:{e}")

    def _on_export_cmd(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出命令文件", "编曲命令.cmd", "命令 (*.cmd);;文本 (*.txt);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._editor.toPlainText())
            self._append(f"已导出命令:{os.path.basename(path)}")
        except Exception as e:
            self._append(f"写文件失败:{e}")

    # ── 生成到时间线 ────────────────────────────────────────────────────────
    def _tab(self, name: str):
        """按钮小图标：tabler 16px 次级色（emoji 退役）。"""
        try:
            from ..main_icon_library import load_tabler_icon
            ic = load_tabler_icon(name, 16, "#CCCCCC")
            return ic if not ic.isNull() else __import__("PyQt6.QtGui", fromlist=["QIcon"]).QIcon()
        except Exception:
            __import__("PyQt6.QtGui", fromlist=["QIcon"])
            return __import__("PyQt6.QtGui", fromlist=["QIcon"]).QIcon()

    def _on_generate(self):
        proj = self._panel_project()
        if proj is None:
            self._append("无法获取当前项目。")
            return
        if self._panel_ctrl() is None:
            self._append("未找到时间线,无法生成。")
            return
        if self._worker is not None and self._worker.isRunning():
            self._append("⏳ 正在生成中,请稍候。")
            return
        text = self._editor.toPlainText()
        self._btn_gen.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._cancelled = False
        self._progress.setVisible(True)
        self._append("» 开始解析+渲染(后台)…")
        w = _RenderWorker(proj, text, fps=40, parent=self)
        w.message.connect(self._append)
        w.finished_render.connect(self._on_render_done)
        self._worker = w
        w.start()

    def _on_cancel_render(self):
        """请求中断后台渲染并 join(FIX G)。"""
        w = self._worker
        if w is None or not w.isRunning():
            self._btn_cancel.setEnabled(False)
            return
        self._cancelled = True
        self._append("✕ 取消中…(等待当前渲染收尾)")
        self._btn_cancel.setEnabled(False)
        try:
            w.requestInterruption()
            w.quit()
            w.wait(8000)  # 渲染是 CPU 紧循环,可能需等当前 clip 收尾
        except Exception:
            pass
        if not w.isRunning():
            self._append("✕ 已取消。")
        self._btn_gen.setEnabled(True)
        self._progress.setVisible(False)

    def _on_render_done(self, payload):
        self._btn_gen.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress.setVisible(False)
        if getattr(self, "_cancelled", False):
            self._cancelled = False
            self._append("✕ 渲染已取消,未挂轨。")
            return
        if payload is None:
            self._append("未生成任何 clip。")
            return
        rendered, report = payload
        for wmsg in (report.warnings or [])[:30]:
            self._append(f"· {wmsg}")
        try:
            self._attach_to_timeline(rendered)
        except Exception as e:
            import traceback
            self._append(f"建轨失败:{e}")
            self._append(traceback.format_exc()[-300:])
            return
        self._append(f"» 完成:{report.n_tracks} 条轨道、{report.n_clips} 个 clip 已追加到时间线。")

    def _attach_to_timeline(self, rendered: dict):
        """主线程建轨挂 clip(追加模式)。同名轨道合并到已存在的同名轨。"""
        from ...core.models import Track
        ctrl = self._panel_ctrl()
        if ctrl is None or not rendered:
            return
        _color = {"泵": "#56B65B", "灯": "#9e6abf", "阀": "#E6B455",
                  "电机": "#4772B3", "命令": "#888899"}
        existing = {t.name: t for t in _timeline_tracks(ctrl)}   # ★R167 真实布局
        for tname, clips in rendered.items():
            if not clips:
                continue
            unis = sorted({u for c in clips for u in (c.clip_universes or [])}) or [1]
            track = existing.get(tname)
            if track is None:
                # 颜色按后缀猜
                key = "命令"
                for k in _color:
                    if tname.endswith(k):
                        key = k; break
                track = Track(name=tname, universes=unis, color=_color.get(key, "#4A90D9"))
                ctrl.add_track(track)
                existing[tname] = track
            else:
                # 合并 universe
                merged = sorted(set(track.universes or []) | set(unis))
                track.universes = merged or [1]
            for c in clips:
                c.track_id = track.id
                track.add_clip(c)
        try:
            ctrl.tracks_changed.emit()
        except Exception:
            pass

    # ── 取项目/时间线(委托父面板)──────────────────────────────────────────
    def _panel_project(self):
        try:
            return self._panel._project()
        except Exception:
            return None

    def _panel_ctrl(self):
        try:
            return self._panel._timeline_ctrl()
        except Exception:
            return None

    def _append(self, text: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{ts}] {text}")
        # 行距 +2px：给最后一行块加底边距（纯排版，不动内容与自动滚动）
        blk = self._log.document().lastBlock()
        if blk.isValid():
            c = QTextCursor(blk)
            bf = c.blockFormat()
            if bf.bottomMargin() != 2:
                bf.setBottomMargin(2)
                c.setBlockFormat(bf)

    # ── 安全关闭:绝不在 QThread 运行时销毁(FIX G)──────────────────────────
    def closeEvent(self, e):
        w = self._worker
        if w is not None and w.isRunning():
            if getattr(self, "_close_deferred", False):
                # 已延后关闭(等线程 finished),本次不再重复等待。
                e.ignore()
                return
            self._cancelled = True
            try:
                w.requestInterruption()
                w.quit()
            except Exception:
                pass
            # 渲染是 CPU 紧循环且不检查 interruption,只能有限等待其自然收尾。
            try:
                if not w.wait(8000):
                    w.wait(2000)
            except Exception:
                pass
            if w.isRunning():
                # 超时仍未收尾:绝不能析构运行中的 QThread(否则 "QThread: Destroyed
                # while thread is still running" → abort)。忽略本次关闭,待线程
                # finished 后再真正关闭(信号驱动关闭,而非硬 wait 后硬销毁)。
                e.ignore()
                self._close_deferred = True
                w.finished.connect(self.close)
                return
        super().closeEvent(e)

    # ── 项目设备/组速查(FIX H)──────────────────────────────────────────────
    def _gather_project_ref(self):
        """返回 (groups:[name], channel_types:[type])。防御式读取,project 可能为 None。"""
        groups: List[str] = []
        chans: List[str] = []
        proj = self._panel_project()
        if proj is None:
            return groups, chans
        try:
            for g in (getattr(proj, "fixture_groups", None) or []):
                if isinstance(g, dict):
                    nm = g.get("name")
                else:
                    nm = getattr(g, "name", None)
                if nm and nm not in groups:
                    groups.append(str(nm))
        except Exception:
            pass
        try:
            seen = set()
            for fd in (getattr(proj, "fixture_defs", None) or []):
                ch_list = getattr(fd, "channels", None)
                if ch_list is None and isinstance(fd, dict):
                    ch_list = fd.get("channels")
                for ch in (ch_list or []):
                    if isinstance(ch, dict):
                        t = ch.get("type")
                    elif isinstance(ch, str):
                        t = ch
                    else:
                        t = getattr(ch, "type", None) or getattr(ch, "value", None)
                    if t and str(t) not in seen:
                        seen.add(str(t)); chans.append(str(t))
        except Exception:
            pass
        return groups, chans

    def _show_project_ref(self):
        """弹菜单列出本项目编组名 + 通道类型;点选即插入光标处(FIX H)。"""
        groups, chans = self._gather_project_ref()
        # 系统级 QMenu（全局主题已覆盖，不再页面自造菜单样式）
        menu = QMenu(self)
        if not groups and not chans:
            act = menu.addAction("无项目(或无设备/编组)")
            act.setEnabled(False)
            menu.exec(self._btn_proj_ref.mapToGlobal(
                self._btn_proj_ref.rect().bottomLeft()))
            return
        if groups:
            hdr = menu.addAction("— 编组(点击插入名称)—"); hdr.setEnabled(False)
            for nm in groups:
                token = f'"{nm}"' if (" " in nm or "\t" in nm) else nm
                a = menu.addAction(f"{nm}")
                a.triggered.connect(lambda _=False, t=token: self._insert_token(t))
        if chans:
            menu.addSeparator()
            hdr = menu.addAction("— 通道类型(点击插入)—"); hdr.setEnabled(False)
            for t in chans:
                a = menu.addAction(self._tab("adjustments-horizontal"), f"{t}")
                a.triggered.connect(lambda _=False, tt=t: self._insert_token(tt))
        menu.exec(self._btn_proj_ref.mapToGlobal(
            self._btn_proj_ref.rect().bottomLeft()))

    def _insert_token(self, token: str):
        cur = self._editor.textCursor()
        cur.insertText(token)
        self._editor.setTextCursor(cur)
        self._editor.setFocus()
