# -*- coding: utf-8 -*-
"""调度台面板的**库管理**方法（场景的存·取·更·删 + 监控区选择同步）。

从 `dispatch_panel` 拆出（单文件 <=500 行）。拆法按**职责**而非行数：
  · `dispatch_panel`     面板骨架（工具栏 + 左右两区 + 服务注入）
  · `panel_table_host`   表格宿主（模型构建 + 取值 + 双击路由）
  · `panel_library`（本文件）  库管理（保存/覆盖/删除/调用 + 选择态同步）
  · `store_handlers`     存储区右键动作（场景的完整菜单流程）

只依赖宿主已有的注入（`_capture_state`/`_recall_nodes`）与视图/存储区控件。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtWidgets import QInputDialog, QMessageBox

_log = logging.getLogger(__name__)


class LibraryMixin:
    """库管理方法（由 `DispatchPanel` 继承）。"""

    def _on_release_all(self) -> None:
        """页面级"释放全部调用层"：清 registry（叠加语义关闭，可重新调用叠加）。"""
        try:
            from ...processing.dispatch import get_release_registry
            n = get_release_registry().release_all()
            self._status.setText(f"已释放全部调用层（{n} 项）；再次调用即可重新叠加")
        except Exception as e:
            QMessageBox.warning(self, "调度台", f"释放失败：{e}")

    def _clear_all_selection(self) -> None:
        """Esc：清空监控区与保存区的选择（并同步按钮门控）。"""
        try:
            self.view.clear_selection()
            self.list.clearSelection()
            self._on_monitor_selection(self.view.selected_keys())
            self._on_cache_selection_changed()
        except Exception:
            pass

    def _on_monitor_selection(self, keys) -> None:
        """监控区选择变化 → 只更新状态栏提示。

        ★ 2026-09-14：顶栏的「保存选中/同步到面板」等按钮已删（保存改由"点存储区
          空格"完成，见 store_handlers），故这里不再切换按钮 enabled/文案。
        """
        n = len(keys or ())
        try:
            self._status.setText(
                f"已保存 {self.list.count()} 条 · 监控区选中 {n} 个格")
        except Exception:
            pass

    def refresh_saved(self) -> None:
        """重建存储区场景列表（**只显示当前页**；保留原选择）。

        ★ R183 T7：场景页 —— 数据依旧全在库里（搜索/触发/键绑定都能看到），
        只是视图按 `EffectCache.page` 过滤；总条数仍报**全库**数量（用户需要知道
        "这页之外还有多少"）。
        """
        try:
            from ...processing.dispatch import get_effect_cache_store
            from .scene_pages import page_of
            keep = self.selected_cache_ids()
            store = get_effect_cache_store()
            page = int(getattr(self, "_scene_page", 0) or 0)
            self.list.set_scenes([c for c in store.search("") if page_of(c) == page])
            self._saved_hdr.setText(
                f"存储区 · 场景（第 {page + 1} 页，单击=调用）  "
                f"{self.list.count()}/{store.count()} 条")
            if keep:
                self.list.set_selected(keep)
            self._on_cache_selection_changed()
        except Exception:
            pass

    def selected_cache_ids(self) -> List[str]:
        """存储区当前选中的场景条目 id。"""
        try:
            return list(self.list.selected_ids())
        except Exception:
            return []

    def _on_cache_selection_changed(self) -> None:
        """存储区选择变化 → 状态栏提示（右键菜单按选中项操作，无需按钮门控）。"""
        ids = self.selected_cache_ids()
        try:
            self._status.setText(
                f"已保存 {self.list.count()} 条 · 存储区选中 {len(ids)} 条")
        except Exception:
            pass

    # ── 动作：保存 ────────────────────────────────────────────────────────

    def _nodes_for_save(self, only_selected: bool) -> list:
        if only_selected:
            return self.view.selected_nodes()
        return self.view.nodes()

    def _do_save(self, nodes: list, position: Optional[int] = None) -> None:
        """保存场景（整体快照）。

        `position` 非空 = 从**指定空槽**新建（"点击仓库场景部分中的空网格
        单元"的交互）；为空 = 追加到末尾（工具栏"保存选中/全部保存"路径）。
        """
        if not nodes:
            QMessageBox.information(self, "调度台", "没有可保存的窗格。\n"
                                  "先在左侧选中窗格（或让效果正在播放）。")
            return
        default = f"场景 {len(nodes)} 格"
        name, ok = QInputDialog.getText(self, "保存场景", "名称：", text=default)
        if not ok or not str(name).strip():
            return
        try:
            from ...processing.dispatch import get_effect_cache_store
            cap = self._capture_state
            cache = get_effect_cache_store().save_from_nodes(
                str(name).strip(), nodes,
                capture_state=(cap if cap is not None else None),
                position=position)
            # ★ R183 T7：新场景落在**当前页**（点空槽新建 = 在当前页建）
            try:
                cache.page = int(getattr(self, "_scene_page", 0) or 0)
            except Exception:
                pass
            self.refresh_saved()
            self.cacheChanged.emit()
            where = f"第 {int(position) + 1} 格" if position is not None else "末尾"
            self._status.setText(
                f"已存场景「{cache.name}」到{where}：{cache.node_count()} 个窗格 · "
                f"{len(cache.panels())} 个面板")
            # ★ 用户 2026-09-14："一个效果保存到场景之后，其应该自动全 release 状态，
            #   也就是监控区显示通道都没有效果了。" —— 与控台"录完 cue 即清台"同语义；
            #   集中在此，**所有保存路径**（点空槽 / 菜单 / 顶栏）都一致生效。
            fn = getattr(self, "_release_after_save", None)
            if fn is not None:
                fn()
        except Exception as e:
            QMessageBox.warning(self, "调度台", f"保存失败：{e}")

    def _on_save_selected(self) -> None:
        self._do_save(self._nodes_for_save(True))

    def _on_save_all(self) -> None:
        self._do_save(self._nodes_for_save(False))

    def _on_update_cache(self) -> None:
        ids = self.selected_cache_ids()
        nodes = self.view.selected_nodes()
        if not ids or not nodes:
            return
        try:
            from ...processing.dispatch import get_effect_cache_store
            store = get_effect_cache_store()
            cap = self._capture_state
            for cid in ids:
                store.update_from_nodes(cid, nodes,
                                        capture_state=(cap if cap is not None else None))
                # ★ R183 审计⑦B P2-9：内容被替换 → 摘掉该场景的 stamp 台账
                #   （旧 nodes 记的地址会幽灵压制低优先级场景）。
                try:
                    from .scene_stamp_ledger import note_scene_content_changed
                    note_scene_content_changed(cid)
                except Exception:
                    _log.debug("覆盖更新后摘 stamp 台账失败 cid=%s", cid, exc_info=True)
            self.refresh_saved()
            self.cacheChanged.emit()
            self._status.setText(f"已用 {len(nodes)} 个窗格覆盖更新 {len(ids)} 条缓存")
        except Exception as e:
            QMessageBox.warning(self, "调度台", f"覆盖更新失败：{e}")

    def _on_delete_caches(self) -> None:
        ids = self.selected_cache_ids()
        if not ids:
            return
        if QMessageBox.question(self, "调度台", f"删除选中的 {len(ids)} 条缓存？") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            from ...processing.dispatch import get_effect_cache_store
            store = get_effect_cache_store()
            _release_many_before_delete(self, store, ids)   # ★ M1/M2（helper 压基线）
            store.remove_many(ids)
            self.refresh_saved()
            self.cacheChanged.emit()
        except Exception:
            # R181 P2-3：删除链整体失败曾静默 → 用户看到"点了删除没反应"无任何线索
            _log.debug("删除场景缓存失败 ids=%s", ids, exc_info=True)

    # ── 动作：调用 / 同步 ─────────────────────────────────────────────────

    def _recall_ids(self, ids: List[str], sync_selection: bool = False) -> None:
        """调用（播放）若干缓存：把其全部节点状态推回各效果面板。

        ★★ `sync_selection` **默认 False**（用户 2026-09-14 补充）：
          "如果仅仅是点击了保存区的 scene，那么不应该把设备选中状态刷新，也不应该
           调入监控区。仅仅作为一个效果的启用播放。"
        → 纯点击场景卡走默认值；只有「编辑（调入监控区修改）」传 True。
        ★★ 2026-09-18 用户实报（问题7 第二层，**后发接管**）：本函数是"场景点亮"
          的唯一收口，故在此对**纯调用**路径做 LTP 重 stamp（不同面板写不同层，
          recall 路径都不 stamp → 后点亮的场景抢不到同轨通道）。见 `scene_takeover`；
          编辑语义（`sync_selection=True`）**刻意不 stamp** —— 用户定案「调入监控区
          修改时此前效果保留」，接管会把它们压下去、与原语义冲突。
        """
        if not ids:
            return
        try:
            entries = self._entries_for_recall(ids)
            if not entries:
                return
            _send_recall(self, len(ids), entries, sync_selection)
            if not sync_selection:
                _activate_takeover(self, ids)         # ★ 后发接管（唯一收口）
            if len(ids) == 1 and sync_selection:
                self._editing_id = ids[0]             # 只有"调入编辑"才算编辑态
            # ★★ 2026-09-14 用户实报（保存区→监控区明显延迟）：这里必须 `force=True`。
            #   各面板 `dispatch_recall_state` 已**同步**把新值写进 NM，此刻读到的就是
            #   调用后的值；不带 force 会走"无变化则跳过重绘"快路径 → 用户要等 tick。
            self.refresh_monitor(force=True)
        except Exception:
            pass

    def _entries_for_recall(self, ids: List[str]) -> List[dict]:
        """把缓存 id 列表 → recall 用的 entries，并顺手记每格状态（供同步复用）。"""
        from ...processing.dispatch import get_effect_cache_store
        store = get_effect_cache_store()
        entries: List[dict] = []
        for cid in ids:
            c = store.get(cid)
            if c is None:
                continue
            for e in c.nodes:
                entries.append({"node": dict(e.get("node", {})),
                                "state": dict(e.get("state", {}))})
                nd = dict(e.get("node", {}))
                k = (str(nd.get("panel", "")), str(nd.get("track_type", "")),
                     str(nd.get("group_key", "")))
                self._cell_state[k] = dict(e.get("state", {}))
        return entries

    def _on_sync_to_panels(self) -> None:
        """把左侧选中窗格的状态推回对应效果面板（还原到当时状态）。"""
        nodes = self.view.selected_nodes()
        if not nodes:
            return
        entries = []
        for n in nodes:
            st = self._cell_state.get(n.cell_key(), {})
            entries.append({"node": n.to_dict(), "state": dict(st)})
        if self._recall_nodes is not None:
            ok = self._recall_nodes(entries)
            self._status.setText(
                f"已同步 {len(entries)} 个窗格到对应面板" + ("" if ok else " · 部分未还原"))
        else:
            self.recallRequested.emit(entries)
        self.refresh_monitor()


def _send_recall(panel, n_ids: int, entries: list, sync_selection: bool) -> None:
    """把 entries 推给主窗路由（未注入 `_recall_nodes` 时回落信号）。"""
    if panel._recall_nodes is not None:
        try:
            ok = panel._recall_nodes(entries, sync_selection=sync_selection)
        except TypeError:
            ok = panel._recall_nodes(entries)         # 兼容旧签名注入
        panel._status.setText(
            f"已调用 {n_ids} 条缓存（{len(entries)} 个窗格）"
            + ("" if ok else " · 部分面板未还原"))
    else:
        panel.recallRequested.emit(entries)


def _activate_takeover(panel, cids) -> int:
    """按给定顺序给刚调用（点亮）的场景做接管收口：确保出光 + LTP 重 stamp。

    放在模块级（不往 DispatchPanel 上帝类加方法，见 CLAUDE.md 10.1 / 架构门禁）。
    顺序敏感：批量调用时**最后**处理的（= 最近激活的）在同轨仲裁中胜出。
    实现见 `scene_takeover.activate_scene_takeover`（含"矩阵 recall 不起输出"的补启动）。
    """
    try:
        from .scene_takeover import activate_scene_takeover
        n = 0
        for cid in (cids or ()):
            activate_scene_takeover(panel, cid)
            n += 1
        return n
    except Exception:
        return 0


def _release_many_before_delete(panel, store, ids) -> None:
    """多选删除前的释放收口（R178 审计 M1/M2；从 _on_delete_caches 拆出压基线）：

    激活中的场景必须先走 _release_before_delete（否则 stamp 台账残留、压制永久
    失效、面板继续输出被删场景）；推杆窗同步关闭（防死场景 id 残留）。
    """
    from .scene_release import _release_before_delete
    for cid in ids:
        cache = store.get(cid)
        if cache is not None:
            try:
                _release_before_delete(panel, cid, cache)
            except Exception:
                # R181 P2-3：删除前释放失败 → 被删场景可能仍有输出/残留记账
                _log.debug("删除前释放失败 cid=%s（输出可能残留）", cid, exc_info=True)
    try:
        from .fader_takeover import close_takeover_window, reset_takeover_window
        for cid in ids:
            # ★ R181 P2-4：复位先于关闭 —— `close_takeover_window` 只摘窗，
            #   不复位滑杆；若不先把 scale 归还 1.0，该场景若被同名 id 重新保存
            #   会带着残留缩放（FadeRunner.drop 已在释放链里摘 scale，此处同步 UI）。
            reset_takeover_window(panel, cid)
            close_takeover_window(panel, cid)
    except Exception:
        # R181 P2-3：推杆窗收口失败 → 死场景 id 残留窗
        _log.debug("删除后推杆窗收口失败 ids=%s", ids, exc_info=True)
