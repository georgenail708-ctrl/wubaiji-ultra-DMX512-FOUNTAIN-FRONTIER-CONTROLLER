# -*- coding: utf-8 -*-
"""Clip Studio 弹层（R172 设计 B11 / §3.4「双击卡=参数编辑浮层」）—— 独立文件（≤500 行门禁）。

* `CardParamDialog`：按 `CardSpec.params` 动态建表单 —— int/float→SpinBox、
  choice→ComboBox、bool→CheckBox、str→LineEdit、color→预设色 combo、
  spec→组选择子表单（mode combo + start/end/take/gap spins）。确定 → `values()`。
* `BindClipsDialog`（设计 B11）：列出工程全部 clip，checkbox 勾选态 =
  `clip.process_sheet_id == 本sheet`；勾/去勾即时回调注入的 `bind_fn(clip_id, sheet_id|None)`。

表单控件范围刻意最小（本仓 全局 QSS 接管外观，无内联样式魔法）。
每类型一行由共享 `studio_param_form.CardParamForm` 构造；弹窗只装配原生表单和按钮。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

from src.ui.widget_kit import set_button_roles
from src.ui.clip_studio.studio_cards import CardSpec
from src.ui.clip_studio.studio_param_form import CardParamForm, PRESET_COLORS  # compatibility export
from src.ui.clip_studio.studio_note import NOTE_MAX_LEN

__all__ = ["CardParamDialog", "BindClipsDialog", "NoteTextDialog"]
def _add_buttons(v: QVBoxLayout, dialog: QDialog) -> None:
    """确定/取消按钮行 + 弹窗标准外框。"""
    row = QHBoxLayout()
    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    ok.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(ok)
    v.addLayout(row)
    set_button_roles(dialog, primary=ok, cancel=(cancel,))


class CardParamDialog(QDialog):
    """卡片参数编辑浮层（双击卡触发）。`values()` → 节点 params dict。"""

    def __init__(self, spec: CardSpec, params: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"编辑卡片：{spec.name}")
        self.setModal(True)
        self.setMinimumWidth(340)
        v = QVBoxLayout(self)
        self._form = CardParamForm(spec, params, self)
        self._readers = self._form._readers
        v.addWidget(self._form)
        hint = QLabel(spec.desc)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")
        v.addWidget(hint)
        _add_buttons(v, self)

    def values(self) -> dict:
        """读回全部参数（bool→bool / spec→五键子表 / 其余按控件类型）。"""
        return self._form.values()


class BindClipsDialog(QDialog):
    """绑定 Clip 弹层（设计 B11）：一个 sheet 可套多个 clip，勾选即生效。"""

    def __init__(self, sheet_id: str, sheet_name: str, clips, bind_fn, *,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"绑定 Clip：{sheet_name}")
        self.setModal(True)
        self.setMinimumSize(360, 320)
        self._sheet_id = str(sheet_id)
        self._bind_fn = bind_fn
        v = QVBoxLayout(self)
        v.addWidget(QLabel("勾选使用该后处理页的 Clip（勾=绑，去勾=解绑）："))
        self._list = QListWidget()
        self._populate(clips)
        self._list.itemChanged.connect(self._on_item_changed)  # 填充完再接（防构造期误触发）
        v.addWidget(self._list, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        set_button_roles(self, primary=close)

    def _populate(self, clips) -> None:
        """每 clip 一行：title + 勾选态（process_sheet_id == 本 sheet）。"""
        for clip in clips:
            cid = str(getattr(clip, "id", ""))
            title = str(getattr(clip, "label", "") or getattr(clip, "preset_name", "")
                        or f"Clip {cid[:8]}")
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            bound = str(getattr(clip, "process_sheet_id", "") or "") == self._sheet_id
            item.setCheckState(Qt.CheckState.Checked if bound else Qt.CheckState.Unchecked)
            self._list.addItem(item)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """勾/去勾 → bind_fn(clip_id, sheet_id|None)（勾选态即绑定关系真值）。"""
        if self._bind_fn is None:
            return
        cid = str(item.data(Qt.ItemDataRole.UserRole))
        checked = item.checkState() == Qt.CheckState.Checked
        self._bind_fn(cid, self._sheet_id if checked else None)


class NoteTextDialog(QDialog):
    """注释便签编辑弹层（R183 W2-ST #3）：多行纯文本，确定 → `text()`。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑便签")
        self.setModal(True)
        self.setMinimumSize(320, 220)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("便签文本（纯文本；不上墙、不参与编译）："))
        self._edit = QPlainTextEdit(str(text or "")[:NOTE_MAX_LEN])
        self._edit.setPlaceholderText("例如：副歌段用这组过渡")
        v.addWidget(self._edit, 1)
        _add_buttons(v, self)

    def text(self) -> str:
        """读回文本（限长截断，与 `studio_note.NOTE_MAX_LEN` 同口径）。"""
        return self._edit.toPlainText()[:NOTE_MAX_LEN]
