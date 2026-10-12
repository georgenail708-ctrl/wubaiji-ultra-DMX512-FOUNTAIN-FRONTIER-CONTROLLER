# -*- coding: utf-8 -*-
"""Clip Studio 面板工具层混入（R183 W2-ST #10/#16 + 撤销 token 接线）—— 从
`studio_panel` 拆出（该文件贴 500 行门禁）。

职责：

* **画布工具接线**（#15）：给主画布注入 `flush_fn`（挂起编辑先落盘）与
  `snapshot_fn`（撤销快照），并在 `_on_graph_changed` 里消费画布的 undo token
  —— 画布侧操作已压过一条快照时，面板不再压第二条（一次操作 = 一条撤销，
  `studio_session.no_inner_snapshots` 同思路）。
* **导出画布 PNG**（#10）：`QFileDialog` 保存到用户路径（画布整帧 grab）。
* **便签编辑**（#3）：双击便签 → 多行文本弹窗 → 写节点 params → 走面板回路。
* **Sheet 缩略图条**（#16）：多 sheet 各一枚 80px 缩略（复用总览管线），
  点击切页。

与宿主（ClipStudioPanel）鸭子类型协作，依赖：`_canvas/_state/_sheets()/
_active_sheet()/_sheet_by_id()/_on_graph_changed()/_refresh_all()/_persist()/
state_edited`。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QFileDialog

from src.ui.clip_studio import studio_note

log = logging.getLogger(__name__)

__all__ = ["PanelToolsMixin"]


class PanelToolsMixin:
    """面板工具接线（宿主 = ClipStudioPanel）。"""

    # ── 接线（面板构造/换绑时调用）──────────────────────────────────────
    def _install_panel_tools(self) -> None:
        """注入画布工具钩子 + 接缩略图条信号（幂等；测试可直接重调）。"""
        strip = getattr(self, "_sheet_thumbs", None)
        if strip is not None:
            try:
                strip.sheet_activated.disconnect(self._on_thumb_sheet_clicked)
            except TypeError:
                pass
            strip.sheet_activated.connect(self._on_thumb_sheet_clicked)
        self._wire_canvas_tools(self._canvas)
        for cv in getattr(self, "_grid_canvases", ()):
            self._wire_canvas_tools(cv)

    def _wire_canvas_tools(self, cv) -> None:
        """给画布注入撤销钩子与导出入口（四宫格单元格各自可导出自身画面）。"""
        cv.set_undo_hooks(flush_fn=self.flush_pending, snapshot_fn=self._snapshot)
        cv.notice.connect(self._on_canvas_notice)
        cv.export_requested.connect(lambda _cv=cv: self.export_canvas_png(_cv))

    def sheet_thumb_strip(self):
        """缩略图条控件（测试入口）。"""
        return self._sheet_thumbs

    def _on_canvas_notice(self, text: str) -> None:
        """画布一条提示 → 状态标签（复用编译状态行，不新建 UI 通道）。"""
        self._status_label.setText(str(text))
        self._status_label.setToolTip(str(text))

    def _on_thumb_sheet_clicked(self, sid: str) -> None:
        """缩略图点击 → 切到该 sheet（与标签条同一路径）。"""
        idx = next((i for i, s in enumerate(self._sheets())
                    if str(s.get("id")) == str(sid)), -1)
        if idx >= 0 and idx != self._tabs.currentIndex():
            self._tabs.setCurrentIndex(idx)

    # ── #16 缩略图条刷新（`_sync_tabs` 末尾调用）───────────────────────
    def _sync_sheet_thumbs(self) -> None:
        strip = getattr(self, "_sheet_thumbs", None)
        if strip is None:
            return
        sheets = self._sheets()
        strip.set_sheets(sheets, str(self._state.get("active_sheet", "")))
        strip.setVisible(bool(sheets))

    # ── #10 导出画布 PNG ───────────────────────────────────────────────
    def export_canvas_png(self, cv=None, *, path: str | None = None) -> str:
        """导出整帧 PNG；`path` 缺省 → QFileDialog 询问。返回落盘路径（取消 → ""）。"""
        cv = cv if cv is not None else self._canvas
        sheet = self._active_sheet()
        default = f"{str((sheet or {}).get('name') or 'clip_studio')}.png"
        target = str(path or "")
        if not target:
            target, _sel = QFileDialog.getSaveFileName(
                self, "导出画布图像", default, "PNG 图像 (*.png)")
        if not target:
            return ""
        if not target.lower().endswith(".png"):
            target += ".png"
        ok = False
        try:
            ok = bool(cv.export_png(target))
        except Exception:
            log.warning("[studio_panel_tools] 导出画布失败", exc_info=True)
        self._on_canvas_notice(f"已导出图像：{target}" if ok else "导出图像失败")
        return target if ok else ""

    # ── #3 便签编辑（双击便签路径）─────────────────────────────────────
    def edit_note(self, nid, *, text: str | None = None) -> bool:
        """编辑便签：`text` 缺省 → 多行弹窗；写回并走面板编辑回路。"""
        node = self._canvas.node_by_id(str(nid))
        if node is None or not studio_note.is_note(node):
            return False
        if text is None:
            from src.ui.clip_studio.studio_dialogs import NoteTextDialog
            dlg = NoteTextDialog(studio_note.note_text(node), self)
            if not dlg.exec():
                return False
            text = dlg.text()
        before = studio_note.note_text(node)
        if str(text) == before:
            return False
        self._canvas._begin_undo_op()
        studio_note.set_note_text(node, text)
        self._canvas._commit_graph_change()
        self._canvas.update()
        return True
