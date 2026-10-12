# -*- coding: utf-8 -*-
"""programmer_sheet — Programmer 只读「当前值表格」（Fixture Sheet，R183 批次 I 项 11）。

参考语义：Table View / gMA3 Fixture Sheet —— 行=设备、列=通道类型，
一眼总览"这一组每台设备现在是什么值"。

★ 合规边界（用户定案 2026-08-02「逐台设备页面已移除」）：本模块**只读快照**，
  不建任何逐台编辑控件（没有 QLineEdit/QSpinBox 单元格委托），双击/编辑一律
  不接线 → 与"逐台页面已删"的定案不冲突。

数据面前端：调用方（ProgrammerPanel 的行右键菜单）从既有只读链取快照：
  · `ChannelTypeRow._values`（合并后权威值）——口径与 `get_all_values()` 一致；
  · `ChannelTypeList.get_all_values()` —— 跨行汇总。
本模块只负责摆表格与显示文本，不回流写值。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .programmer_tools import format_value_dual, pct_of


def _dedup_keep_order(candidates: Iterable[Any]) -> List[str]:
    """去重保序的字符串序列（空串剔除）。"""
    out: List[str] = []
    seen: set = set()
    for raw in candidates:
        fid = str(raw)
        if fid and fid not in seen:
            out.append(fid)
            seen.add(fid)
    return out


def _ordered_row_ids(values: Dict[str, Dict[str, int]], order: Optional[Sequence[str]]) -> List[str]:
    """设备序：order 优先（含无值设备），其余出现在 values 里的按字典序补在末尾。"""
    ordered = _dedup_keep_order(order or [])
    done = set(ordered)
    tail = sorted(str(k) for k in (values or {}).keys())
    return ordered + _dedup_keep_order(f for f in tail if f not in done)


def _col_order(values: Dict[str, Dict[str, int]], ordered: Sequence[str]) -> List[str]:
    """通道列序：按设备序的首现顺序去重（与组内通道顺序一致）。"""
    cols: List[str] = []
    seen: set = set()
    for fid in ordered:
        for ct in (values.get(fid) or {}).keys():
            sct = str(ct)
            if sct and sct not in seen:
                cols.append(sct)
                seen.add(sct)
    return cols


def _matrix_of(values: Dict[str, Dict[str, int]], ordered: Sequence[str],
               cols: Sequence[str]) -> List[List[Optional[int]]]:
    """值矩阵；缺值格 = None（"没写过"与"写了 0"必须可分辨）。"""
    matrix: List[List[Optional[int]]] = []
    for fid in ordered:
        chan = values.get(fid) or {}
        row: List[Optional[int]] = []
        for ct in cols:
            raw = chan.get(ct)
            try:
                row.append(int(raw) if raw is not None else None)
            except (TypeError, ValueError):
                row.append(None)
        matrix.append(row)
    return matrix


def build_sheet_rows(values: Dict[str, Dict[str, int]],
                     order: Optional[Sequence[str]] = None,
                     names: Optional[Dict[str, str]] = None
                     ) -> Tuple[List[str], List[str], List[List[Optional[int]]]]:
    """把 `{fid: {ch_type: value}}` 快照摆成 (设备序, 通道列序, 值矩阵)。"""
    ordered = _ordered_row_ids(values or {}, order)
    cols = _col_order(values or {}, ordered)
    return ordered, cols, _matrix_of(values or {}, ordered, cols)


def _cell_item(val: Optional[int]) -> QTableWidgetItem:
    """单格：None → 空文本（"没写过"）；有值 → "128(50%)" + 居中 + 热力前景色。"""
    item = QTableWidgetItem("" if val is None else format_value_dual(val))
    if val is not None:
        item.setToolTip(f"DMX {val} · {pct_of(val)}%")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if val > 0:
            item.setForeground(_value_color(val))
    return item


class FixtureSheetDialog(QDialog):
    """只读 Fixture Sheet：行=设备、列=ch_type，文本 `DMX(百分比)`。"""

    def __init__(self, parent: Optional[QWidget] = None, title: str = "当前值表格",
                 sheet: Optional[dict] = None):
        """`sheet` = {"values":…, "order":…, "names":…, "ch_labels":…}（dict 传参避免
        位置参数超限；旧调用点用 `open_fixture_sheet` 的关键字形式）。"""
        super().__init__(parent)
        sheet = dict(sheet or {})
        self.setWindowTitle(title)
        self.resize(900, 520)
        self._names = dict(sheet.get("names") or {})
        rows, cols, matrix = build_sheet_rows(sheet.get("values") or {},
                                             sheet.get("order"), self._names)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        head = QLabel(
            f"{len(rows)} 台设备 × {len(cols)} 个通道类型（只读快照：值取自当前组"
            "合并值；空=未写过该通道）")
        head.setStyleSheet("color:#999999;font-size:10px;")
        lay.addWidget(head)
        table = self._build_table(rows, cols, matrix, sheet.get("ch_labels") or {})
        lay.addWidget(table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self._table = table
        self._counts = (len(rows), len(cols))

    def _build_table(self, rows, cols, matrix, labels) -> QTableWidget:
        """建只读表（无编辑委托；值文本 `DMX(百分比)`）。"""
        table = QTableWidget(len(rows), max(1, len(cols) + 1))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setAlternatingRowColors(True)
        table.setHorizontalHeaderLabels(
            ["设备"] + [str(labels.get(ct, ct)) for ct in cols])
        for r, fid in enumerate(rows):
            name_item = QTableWidgetItem(self._names.get(fid, fid))
            name_item.setToolTip(fid)
            table.setItem(r, 0, name_item)
            for c, val in enumerate(matrix[r]):
                table.setItem(r, c + 1, _cell_item(val))
        table.verticalHeader().setDefaultSectionSize(20)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def counts(self) -> Tuple[int, int]:
        return self._counts


def _value_color(value: int) -> QColor:
    """按值大小给一个"热力"前景色（只读视图的视觉辅助，不影响语义）。"""
    v = max(0, min(255, int(value)))
    return QColor(120 + int(135 * v / 255.0), 200 - int(90 * v / 255.0), 120)


def open_fixture_sheet(parent: Optional[QWidget], values: Dict[str, Dict[str, int]],
                       **kw) -> FixtureSheetDialog:
    """打开只读表格（非模态 `show()`：查看表格不该挡住编辑动作）。

    关键字：`order` / `names` / `ch_labels` / `title`（dict 传参避免位置参数超限）。
    """
    dlg = FixtureSheetDialog(parent=parent, title=kw.get("title", "当前值表格"),
                             sheet={"values": values, "order": kw.get("order"),
                                    "names": kw.get("names"),
                                    "ch_labels": kw.get("ch_labels")})
    dlg.setModal(False)
    dlg.show()
    return dlg


def effect_history_ids(browser: Any, limit: int = 6) -> List[str]:
    """effect browser 历史栈前 N 项（字段名与其持久化一致：`_effect_history`）。"""
    hist = list(getattr(browser, "_effect_history", []) or [])
    return [str(x) for x in hist[:max(1, int(limit))] if str(x)]


def effect_display_name(browser: Any, preset_id: str) -> str:
    """历史项显示名（优先浏览器自带的命名函数；失败回退预设名/ id）。"""
    fn = getattr(browser, "_effect_display_name", None)
    if callable(fn):
        try:
            name = fn(str(preset_id))
            if name:
                return str(name)
        except Exception:
            pass
    try:
        from .effect_engine import get_engine   # 修前 ..engine.effect_engine 不存在 → 恒回退显示裸 id
        preset = get_engine().presets.get(str(preset_id))
        if preset is not None:
            return str(getattr(preset, "name", "") or preset_id)
    except Exception:
        pass
    return str(preset_id)


def apply_effect_preset(browser: Any, preset_id: str) -> bool:
    """点击快捷钮 = 调用浏览器既有"选中预设"API（`_on_preset_click`）。

    ★ 不编造 API：本仓 effect browser 的"选中/应用"入口就是 `_on_preset_click`
      （`effect_browser.py:3520`；预设列表点击与历史列表点击都走它）。
      拿不到该方法 → False（调用方给提示，不静默假装成功）。
    """
    fn = getattr(browser, "_on_preset_click", None)
    if not callable(fn):
        return False
    try:
        fn(str(preset_id))
        return True
    except Exception:
        return False


def _quick_bar_buttons(bar: Any, panel: Any, lay: Any, browser: Any,
                       ids: List[str]) -> None:
    """按历史 id 重建按钮行（保留标题；无历史 → 单个禁用说明钮）。"""
    while lay.count() > 1:                      # 保留标题
        item = lay.takeAt(1)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    if not ids:
        btn = QPushButton("（无历史）")
        btn.setEnabled(False)
        btn.setToolTip("效果浏览器暂无使用历史；在效果面板选中任一效果后会出现在这里")
        lay.addWidget(btn)
        lay.addStretch()
        return
    for pid in ids:
        lay.addWidget(_quick_effect_button(panel, browser, pid))
    lay.addStretch()


def _quick_effect_button(panel: Any, browser: Any, pid: str) -> QPushButton:
    """单个最近效果钮（点击 = 浏览器既有 `_on_preset_click`；失败给提示）。"""
    btn = QPushButton(effect_display_name(browser, pid))
    btn.setToolTip(f"应用最近效果：{pid}")
    btn.setStyleSheet(
        "QPushButton{background:#2D2D2D;border:1px solid #3D3D3D;"
        "border-radius:3px;color:#CCCCCC;font-size:10px;padding:0 6px;"
        "min-height:20px;max-width:120px;}"
        "QPushButton:hover{background:#3A3A3A;color:#FFFFFF;}")

    def _on_click(_c=False, preset_id=str(pid), b=browser):
        if apply_effect_preset(b, preset_id):
            return
        try:
            panel.status_feedback.emit("效果快捷：无法应用该预设（浏览器接口不可用）", 3000)
        except Exception:
            pass

    btn.clicked.connect(_on_click)
    return btn


class EffectQuickBar(QWidget):
    """最近效果快捷条控件：**首次显示时自刷新**。

    ★ 为什么需要：本条在 `ProgrammerPanel.__init__`（`_build_ui`）里构建，那一刻
      面板还没挂到 MainWindow 下 → `_get_effect_browser_ref()` 沿父链找不到
      effect browser，会显示"（无历史）"。`showEvent` 里再拉一次即可在用户真正
      看到它时同步历史（不新增上帝类方法；刷新本身有签名守卫，重复调用零开销）。
    """

    def __init__(self, panel: Any):
        super().__init__()
        self.setObjectName("programmerEffectQuickBar")
        self._quick_sig = None
        self._panel = panel
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(4)
        title = QLabel("最近效果")
        title.setStyleSheet("color:#999999;font-size:10px;")
        lay.addWidget(title)
        self._quick_lay = lay      # 测试与外部检查用（按钮行布局）
        self._rebuild(force=True)

    def showEvent(self, event):  # noqa: N802 (Qt 覆写)
        super().showEvent(event)
        self._rebuild(force=False)

    def _browser(self):
        try:
            return self._panel._get_effect_browser_ref()
        except Exception:
            return None

    def _rebuild(self, force: bool = False) -> None:
        """历史签名变了才重建按钮（零无谓重建）。"""
        browser = self._browser()
        ids = effect_history_ids(browser, 6) if browser is not None else []
        sig = tuple(ids)
        if not force and sig == self._quick_sig:
            return
        self._quick_sig = sig
        _quick_bar_buttons(self, self._panel, self._quick_lay, browser, ids)


def build_effect_quick_bar(panel: Any) -> Any:
    """最近效果快捷条（6 键）：数据 = effect browser 历史栈，点击 = 选中该预设。

    挂在 GroupSelector 下方（`_r183_build_left_column` 一行注入）。拿不到
    effect browser 或历史为空 → 按钮禁用 + 说明性 tooltip（不假装有数据）。
    条上挂 `refresh_quick_bar()`（= 控件的 `_rebuild`）：签名变了才重建。
    """
    bar = EffectQuickBar(panel)
    bar.refresh_quick_bar = bar._rebuild
    return bar
