# -*- coding: utf-8 -*-
"""调度台**逐格动作接线**（右击菜单 + 右拖快速编辑 + 双击路由兜底）。

存在理由（用户 2026-09-14 实报）：
  "为什么点击相应的设备的通道不能自动唤起相关的面板？…右击该通道为什么不能
   release。"

真因：视图侧 `FixtureTableView.context_menu` / `quick_edit` 两个信号**从未被连接**
（只有存储区的 `_context_menu` 连了），所以右击/右拖**静默失效**；`cell_ops` 里
`clear_cells`（真清空 = release）等动作因此**零调用者**。

设计（与 `store_handlers.py` 同款）：模块级函数 + `_install_cell_handlers(cls)`
用 `setattr` 挂到面板类，避免继续往面板文件堆方法（架构门禁：文件 ≤500 行）。

职责边界：
  · 菜单**构造**在 `table_ops`（QMenu/QInputDialog，纯 UI 策略）
  · 落值/清空/标记在 `cell_ops`（跨三真源的唯一入口）
  · 本模块只做"信号 → 菜单选择 → cell_ops 调用"的路由
"""
from __future__ import annotations

from typing import Any, List, Optional

from . import table_ops as _ops


# ── 状态栏 ──────────────────────────────────────────────────────────────────
def _set_status_text(self, text: str) -> None:
    """把一句提示写到面板底部状态栏（失败静默，绝不因提示打断操作）。"""
    try:
        lbl = getattr(self, "_status", None)
        if lbl is not None:
            lbl.setText(str(text))
    except Exception:
        pass


# ── 公共取格 ────────────────────────────────────────────────────────────────
def _cell_of(self, key):
    """格键 → `TableCell`（取不到返回 None）。"""
    try:
        pair = self.view._cells.get(tuple(key), (None, None))
    except Exception:
        return None
    return pair[1] if isinstance(pair, tuple) and len(pair) > 1 else None


def _cells_for(self, keys) -> List[Any]:
    """一组格键 → `TableCell` 列表（跳过取不到的）。"""
    out = []
    for k in (keys or ()):
        c = _cell_of(self, k)
        if c is not None:
            out.append(c)
    return out


def _action_cells(self, ctx_key) -> List[Any]:
    """动作作用对象：有选中用整选区，否则用右键命中的那一格。"""
    cells = _cells_for(self, self.view.selected_cells())
    if cells:
        return cells
    one = _cell_of(self, ctx_key)
    return [one] if one is not None else []


def _after_action(self, cells, *, dirty: bool = True) -> None:
    """动作后：刷值（**只刷这批格**）+ 通知主窗置脏。

    ★ 性能（用户报"拖动 programmer 实时编辑时监控区很卡"）：原实现每步
      `_refresh_values(full=True)` 全表重读 + 每步 `cacheChanged`（主窗要序列化
      整个缓存库并置脏）。改为**定向刷新**这批格；`dirty=False` 供拖动过程用
      （拖动结束时再置脏一次，避免每 3px 序列化一次）。
    """
    try:
        self._refresh_values(cells=cells)
    except TypeError:
        try:
            self._refresh_values(full=False)
        except Exception:
            pass
    except Exception:
        pass
    if dirty:
        try:
            self.cacheChanged.emit()
        except Exception:
            pass


def _run_action(self, act: str, cells: List[Any], *, ch_type: str = "") -> bool:
    """把一个菜单动作落到 `cell_ops`（单一真源）。返回是否执行。

    动作 → 实现映射在 `_ACTION_TABLE`（下表按名分发）—— 原先是一长串
    `if/elif` 链（44 行 / CC 19 / 嵌套 11，架构门禁违例），改成查表后本函数只做
    "取 ops → 查表 → 收尾"三件事。
    """
    ops = getattr(self, "_cell_ops", None)
    if ops is None or not cells:
        self._set_status_text("无可操作对象（未注入 NetworkManager / Programmer 未加载）")
        return False
    fn = _ACTION_TABLE.get(str(act))
    if fn is None:
        return False
    try:
        done = fn(self, ops, cells, ch_type or _first_ch_type(cells))
    except Exception as exc:
        self._set_status_text(f"操作失败：{exc}")
        return False
    if not done:
        return False
    _after_action(self, cells)
    return True


# ── 各动作实现（签名统一 `(panel, ops, cells, ch_type) -> bool`）──────────────
def _act_set_value(self, ops, cells, ch_type) -> bool:
    """弹框问值 → 写入。取消（None）视为未执行。"""
    val = _ops.ask_value(self, current=_current_value(ops, cells),
                         unit=str(getattr(self, "_unit", "dmx")))
    if val is None:
        return False
    ops.write_cells(cells, int(val))
    return True


def _current_value(ops, cells) -> int:
    """该批格的首格当前值（弹框默认值）；取不到给 0。"""
    try:
        first = ops.read_cells(cells[:1])
        return int(next(iter(first.values()))[0] or 0) if first else 0
    except Exception:
        return 0


def _act_write_full(self, ops, cells, ch_type) -> bool:
    ops.write_cells(cells, 255)
    return True


def _act_write_zero(self, ops, cells, ch_type) -> bool:
    ops.write_cells(cells, 0)              # 值 0 ≠ 清空（active=True）
    return True


def _act_clear(self, ops, cells, ch_type) -> bool:
    ops.clear_cells(cells)                 # ★ 真清空 = release（active=False）
    return True


def _act_clear_type(self, ops, cells, ch_type) -> bool:
    ops.clear_value_type(ch_type, cells)
    return True


def _act_release_col(self, ops, cells, ch_type) -> bool:
    ops.release_column(ch_type, cells)
    return True


def _act_mark(self, ops, cells, ch_type) -> bool:
    ops.mark_cells(cells, True)
    return True


def _act_unmark(self, ops, cells, ch_type) -> bool:
    ops.mark_cells(cells, False)
    return True


def _act_copy(self, ops, cells, ch_type) -> bool:
    _copy_values(self, cells)
    return True


def _act_paste(self, ops, cells, ch_type) -> bool:
    _paste_values(self, cells)
    return True


#: 菜单动作 → 实现（键来自 `table_ops` 的 ACT_* 常量，避免字面量漂移）。
_ACTION_TABLE = {
    _ops.ACT_SET_VALUE: _act_set_value,
    _ops.ACT_SET_FULL: _act_write_full,
    _ops.ACT_SET_ZERO: _act_write_zero,
    _ops.ACT_CLEAR: _act_clear,
    _ops.ACT_CLEAR_TYPE: _act_clear_type,
    _ops.ACT_RELEASE_COL: _act_release_col,
    _ops.ACT_MARK: _act_mark,
    _ops.ACT_UNMARK: _act_unmark,
    _ops.ACT_COPY: _act_copy,
    _ops.ACT_PASTE: _act_paste,
}


def _first_ch_type(cells: List[Any]) -> str:
    for c in cells:
        ct = str(getattr(c, "ch_type", "") or "")
        if ct:
            return ct
    return ""


def _copy_values(self, cells) -> None:
    """复制：以**值类型**为键（跨设备粘贴时按通道对齐）。"""
    ops = getattr(self, "_cell_ops", None)
    if ops is None:
        return
    try:
        read = ops.read_cells(cells)
    except Exception:
        return
    self._cell_clipboard = {
        str(getattr(c, "ch_type", "") or ""):
            int(read.get((str(c.fixture_id), str(c.col_key)), (None, False))[0] or 0)
        for c in cells}
    self._set_status_text(f"已复制 {len(self._cell_clipboard)} 个值类型")


def _paste_values(self, cells) -> None:
    ops = getattr(self, "_cell_ops", None)
    clip = getattr(self, "_cell_clipboard", None)
    if ops is None or not clip:
        return
    for c in cells:
        ch = str(getattr(c, "ch_type", "") or "")
        if ch in clip:
            try:
                ops.write_cells([c], int(clip[ch]))
            except Exception:
                continue


# ── 右击菜单 ────────────────────────────────────────────────────────────────
def _on_table_context_menu(self, ctx, global_pos) -> None:
    """右击：行头 / 列头 / 格 三种上下文各给不同菜单，动作落 `cell_ops`。"""
    try:
        data = ctx if isinstance(ctx, dict) else {}
        key = data.get("cell")
        if key is not None:
            _menu_for_cell(self, key, global_pos)
            return
        col = data.get("col")
        if col is not None:
            _menu_for_column(self, col, global_pos)
            return
        row = data.get("row")
        if row is not None:
            _menu_for_row(self, row, global_pos)
    except Exception:
        pass


def _menu_for_cell(self, key, global_pos) -> None:
    cell = _cell_of(self, key)
    cells = _action_cells(self, key)
    if not cells:
        return
    act = _ops.build_cell_menu(
        self, global_pos,
        has_selection=bool(cells),
        is_agg=bool(getattr(cell, "agg", False)),
        has_value_type=bool(_first_ch_type(cells)),
        clipboard=bool(getattr(self, "_cell_clipboard", None)))
    if act:
        _run_action(self, act, cells, ch_type=_first_ch_type(cells))


def _menu_for_column(self, col_key, global_pos) -> None:
    """列头右键：作用对象 = 该列的格（优先选区内，无选中则全表该列）。"""
    act = _ops.build_column_menu(self, global_pos, col_label=str(col_key))
    if not act:
        return
    want = str(col_key)
    cells = [c for c in _cells_for(self, self.view.selected_cells())
             if _col_of(c) == want] or _cells_of_column(self, want)
    if not cells:
        return
    _run_action(self, act, cells, ch_type=_first_ch_type(cells))


def _col_of(cell) -> str:
    """格所属的列键（col_ref 优先，回落 col_key）。"""
    return str(getattr(cell, "col_ref", "") or getattr(cell, "col_key", "") or "")


def _cells_of_column(self, col_key: str) -> List[Any]:
    """该列在视觉顺序下的全部格。"""
    out = []
    try:
        for k in list(getattr(self.view, "_order", ()) or ()):
            c = _cell_of(self, k)
            if c is not None and _col_of(c) == str(col_key):
                out.append(c)
    except Exception:
        return []
    return out


def _menu_for_row(self, row_id, global_pos) -> None:
    """行头右键：动作落到与格菜单**同源**的 cell_ops（行 = 该设备的所有列格）。"""
    act = _ops.build_row_menu(self, global_pos, row_label=str(row_id))
    if not act:
        return
    cells = _cells_of_row(self, row_id)
    if not cells:
        return
    ch_type = _first_ch_type(cells)
    if act == _ops.ACT_ROW_SET:
        _run_action(self, _ops.ACT_SET_VALUE, cells, ch_type=ch_type)
    elif act == _ops.ACT_ROW_CLEAR:
        _run_action(self, _ops.ACT_CLEAR, cells, ch_type=ch_type)
    elif act == _ops.ACT_ROW_ONLY:
        _select_only_row(self, row_id, cells)
    elif act == _ops.ACT_ROW_LOCATE:
        _locate_row(self, row_id)
    else:
        _run_action(self, act, cells, ch_type=ch_type)


def _cells_of_row(self, row_id) -> List[Any]:
    """该行（设备）在视觉顺序下的全部格。"""
    out = []
    try:
        for k in list(getattr(self.view, "_order", ()) or ()):
            c = _cell_of(self, k)
            if c is not None and str(getattr(c, "fixture_id", "")) == str(row_id):
                out.append(c)
    except Exception:
        return []
    return out


def _select_only_row(self, row_id, cells) -> None:
    """「只显示该设备」：把选中集换成该行的全部格。"""
    try:
        self.view.set_selected({(str(row_id), str(getattr(c, "col_key", "")))
                                for c in cells})
    except Exception:
        pass


def _locate_row(self, row_id) -> None:
    """「定位该设备」：请求主窗把顶视图聚焦到这台设备。"""
    try:
        self._focus_panel("topview", [str(row_id)], (str(row_id), ""))
    except Exception:
        pass


# ── 右拖快速编辑 ────────────────────────────────────────────────────────────
def _on_table_edit_committed(self) -> None:
    """右拖松手 → 补齐置脏（拖动过程刻意不置脏，见 table_input.mouseReleaseEvent）。

    ★★ 2026-09-14 深查修正（未保存丢失）：缺这一步时"右拖改完直接关窗口"既没有
      未保存提示、改动也会被丢掉（与其它格操作 `_run_action(dirty=True)` 不一致）。
    """
    try:
        self.cacheChanged.emit()
    except Exception:
        pass


def _on_table_quick_edit(self, delta: int) -> None:
    """右拖相对增减：以视图记录的**拖动起点基准值** + 累计位移 落绝对值。

    `quick_edit(int)` 发的是**累计位移**（起点为 0，每 3px = 1 DMX），不是增量；
    基准值由 `table_input._begin_quick_drag` 存入 `view._qdrag_base_value`，
    因此"基准 + delta"即正确目标值（不会把累计当增量反复叠加）。
    """
    ops = getattr(self, "_cell_ops", None)
    if ops is None:
        return
    cells = _cells_for(self, self.view.selected_cells())
    if not cells:
        return
    base = 0
    try:
        base = int(getattr(self.view, "_qdrag_base_value", 0) or 0)
    except Exception:
        base = 0
    target = max(0, min(255, base + int(delta)))
    for c in cells:
        try:
            ops.write_cells([c], target)
        except Exception:
            continue
    # 拖动过程只定向刷新、不置脏（每 3px 序列化整个缓存库会把拖动拖成 PPT）；
    # 置脏由拖动结束（左键松开/选区变化）时的 tick 或下一动作补上。
    _after_action(self, cells, dirty=False)


# ── 安装 ────────────────────────────────────────────────────────────────────
CELL_HANDLERS = (
    _set_status_text,
    _on_table_context_menu,
    _on_table_quick_edit,
    _on_table_edit_committed,
)


def _install_cell_handlers(cls) -> None:
    """把上面的模块级函数挂到面板类上（幂等）。"""
    for fn in CELL_HANDLERS:
        setattr(cls, fn.__name__, fn)


__all__ = ["_install_cell_handlers", "CELL_HANDLERS"]
